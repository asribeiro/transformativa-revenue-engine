#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Duble de porta de banco (psql) para a medicao OFFLINE do envio outbound (envio-outbound-v1).

NAO e banco: e um duble de teste, e existe para medir a ORQUESTRACAO do componente (leitura do pedido
aprovado, claim exatamente-uma-vez, idempotencia por chave, gravacao do fato em interactions) sem
PostgreSQL. A medicao contra o banco de verdade e do aceite E2E
(`scripts/agentes/teste_envio_outbound_aceite.sh`, PostgreSQL descartavel na VPS).

Le o SQL do stdin e processa STATEMENT a STATEMENT — o componente manda transacoes com varios comandos
num unico stdin (BEGIN; INSERT interactions; UPDATE sync_events; COMMIT) — mantendo o estado num JSON.

Uso: python3 scripts/agentes/duble_psql_envio.py <estado.json>
"""
import json
import os
import re
import sys

CAMINHO = sys.argv[1]


def ler():
    if not os.path.exists(CAMINHO):
        return {"pedidos": {}, "sync": {}, "interacoes": []}
    with open(CAMINHO, encoding="utf-8") as fh:
        return json.load(fh)


def gravar(e):
    with open(CAMINHO, "w", encoding="utf-8") as fh:
        json.dump(e, fh, ensure_ascii=False)


def valor(sb, chave):
    m = re.search(r"%s\s*=\s*'([^']*)'" % re.escape(chave), sb)
    return m.group(1) if m else None


def processar(sql, e):
    saida = []
    for sb in [s.strip() for s in sql.split(";") if s.strip()]:
        if sb.upper() in ("BEGIN", "COMMIT", "ROLLBACK"):
            continue
        if "FROM sales_intelligence.human_approvals a" in sb:
            alvo = valor(sb, "a.id")
            for pid, pedido in e["pedidos"].items():
                if alvo and pid != alvo:
                    continue
                if pedido.get("status") != "APPROVED" and not pedido.get("forcar_status"):
                    continue
                sinc = e["sync"].get(pedido["chave"])
                saida.append({"id": pid, "action_type": pedido["action_type"], "entity_type": "contact",
                              "entity_id": pedido["contato_id"], "decidido_por": pedido.get("decidido_por"),
                              "decidido_em": pedido.get("decidido_em"), "texto_hash": pedido["texto_hash"],
                              "status": pedido.get("status"), "proposed_action": pedido.get("proposed_action"),
                              "contato": pedido.get("contato"), "contato_email": pedido.get("contato_email"),
                              "contato_canal": pedido.get("contato_canal"),
                              "do_not_contact": pedido.get("do_not_contact", False),
                              "opt_out_email": pedido.get("opt_out_email", False),
                              "opt_out_whatsapp": pedido.get("opt_out_whatsapp", False),
                              "recomendacao_status": pedido.get("recomendacao_status"),
                              "organizacao_id": pedido["organizacao_id"],
                              "organization_id": pedido["organizacao_id"],
                              "sincronizacao_status": (sinc or {}).get("status"),
                              "enviado_em": (sinc or {}).get("enviado_em"),
                              "interaction_id": (sinc or {}).get("interaction_id")})
        elif "json_build_object('status', s.status" in sb:
            chave = valor(sb, "idempotency_key")
            linha = e["sync"].get(chave)
            if linha:
                saida.append({"status": linha["status"], "tentativas": linha.get("tentativas", 0),
                              "interaction_id": linha.get("interaction_id")})
        elif "INSERT INTO sales_intelligence.sync_events" in sb:
            # a chave vem POSICIONAL no INSERT (nunca como `idempotency_key = '...'`)
            m = re.search(r"'SEND_EMAIL', '[^']*', '([^']+)'", sb)
            chave = m.group(1) if m else None
            if chave in e["sync"]:
                if e["sync"][chave]["status"] == "FALHOU" and "DO NOTHING" not in sb:
                    e["sync"][chave]["status"] = "ENVIANDO"
                    e["sync"][chave]["tentativas"] = e["sync"][chave].get("tentativas", 0) + 1
                    e["sync"][chave]["correlation_id"] = e.get("_correlation_atual")
                    saida.append({"claim": "RETENTATIVA"})
            else:
                e["sync"][chave] = {"status": "ENVIANDO", "tentativas": 1, "interaction_id": None,
                                    "enviado_em": None, "correlation_id": e.get("_correlation_atual")}
                saida.append({"claim": "NOVA"})
        elif "INSERT INTO sales_intelligence.interactions" in sb:
            m = re.search(r", '([^']*)'\) RETURNING json_build_object\('interaction_id'", sb)
            ref = m.group(1) if m else None
            ident = "int-" + str(len(e["interacoes"]) + 1)
            e["interacoes"].append({"interaction_id": ident, "content_reference": ref})
            e["_ultima_interacao"] = ident
            saida.append({"interaction_id": ident})
        elif "UPDATE sales_intelligence.sync_events SET status = 'ENVIADO'" in sb:
            chave = valor(sb, "idempotency_key")
            if chave in e["sync"] and e["sync"][chave]["status"] == "ENVIANDO":
                e["sync"][chave]["status"] = "ENVIADO"
                e["sync"][chave]["enviado_em"] = "2026-10-03T00:00:00Z"
                e["sync"][chave]["interaction_id"] = e.get("_ultima_interacao")
                saida.append({"sync_event_id": "se-" + chave[-6:]})
        elif "SET status = 'FALHOU'" in sb:
            chave = valor(sb, "idempotency_key")
            if chave in e["sync"]:
                e["sync"][chave]["status"] = "FALHOU"
        elif "json_build_object('seriam_desfeitos'" in sb:
            corr = valor(sb, "correlation_id")
            n = sum(1 for s in e["sync"].values()
                    if s.get("correlation_id") == corr and s["status"] == "ENVIADO")
            saida.append({"seriam_desfeitos": n})
        elif "UPDATE sales_intelligence.sync_events SET status = 'DESFEITO'" in sb:
            corr = valor(sb, "correlation_id")
            marcados = 0
            for chave, s in e["sync"].items():
                if s.get("correlation_id") == corr and s["status"] == "ENVIADO":
                    s["status"] = "DESFEITO"
                    s["error_message"] = valor(sb, "error_message")
                    marcados += 1
                    saida.append({"desfeito": chave})
            if "json_build_object('desfeitos'" in sb:
                saida.append({"desfeitos": marcados})
    return saida


estado = ler()
for item in processar(sys.stdin.read(), estado):
    print(json.dumps(item, ensure_ascii=False))
gravar(estado)
