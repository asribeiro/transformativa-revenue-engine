#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fecha a ENTREGA das ondas W1-W9 no registro versionado, com a decisão do dono registrada.

POR QUE EXISTE
--------------
Os artefatos `control-plane/deliveries/W*.json` são o registro de entrega que o painel projeta. Os cards das
ondas W1-W9 estão todos `done` no board e os `work_items` de cada artefato estão todos `DONE` — mas o bloco
`human_approval` seguia `PENDING`, então o painel (corretamente) mostrava VALIDATION/PENDING. Fechar a onda é
ato de dono: este script NÃO decide nada — ele registra a decisão dada no Telegram (06/10/2026:
"A com promoção a produção") e o que as evidências existentes provam.

O QUE ELE GRAVA (e o que NÃO grava)
-----------------------------------
- Grava apenas os campos de NÍVEL DE ENTREGA: `release_status`, `current_gate`, `completed_at`, `updated_at` e
  `human_approval` (bloco de decisão + `evidence` montada do que já existe + `history` com a palavra do dono).
- **Não toca em `work_items`** — nem nos `children`, `validation_result` ou nos textos de evidência do
  pipeline. Isso é provado a cada escrita: o sha256 da subárvore `work_items` é comparado antes/depois e a
  escrita FALHA se mudar (`EVIDENCIA_INTACTA`).
- Backup datado de cada artefato antes de escrever (`<nome>.bak-<UTC>.json`), como faz a cadeia do W0.

USO
---
    python3 scripts/validacao-integrada/fechar_entrega_waves.py            # --check (padrão: não escreve)
    python3 scripts/validacao-integrada/fechar_entrega_waves.py --aplicar
    python3 scripts/validacao-integrada/fechar_entrega_waves.py --onda W3 --aplicar
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ENTREGAS = RAIZ / "control-plane" / "deliveries"
BANCO = pathlib.Path(os.environ.get(
    "TRE_KANBAN_DB", "/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db"))
APROVADOR = "Anderson Ribeiro"
PALAVRA = os.environ.get("TRE_PALAVRA_DO_DONO", "A com promoção a produção")
APROVADO_EM = os.environ.get("TRE_APROVADO_EM", "2026-10-06")
REF_AUTORIZACOES = ("Autorizações 6 (escrita em produção até 13/10/2026), 7 (release `homolog` -> `main` = "
                    "`70b4442`) e 8 (release do conserto de publicação/backup = `c773433`) — todas em "
                    "`docs/operations/registro-de-aprovacoes.md`")
ACEITE_PROD = ("aceite de produção do mesmo dia: banco de vendas `PROD_SALES_OK 20/0`, Odoo `PROD_ODOO_OK "
               "15/0`, n8n `N8N_PROD_OK 44/0`, ciclo E2E nas duas direções, replay sem duplicata e dente do "
               "portão com par antes/depois")
HOMOLOG_POR_CARD = "HOMOLOGACAO DO ANDERSON"


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def hoje() -> str:
    return dt.date.today().isoformat()


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def artefatos(onda: str | None) -> list[pathlib.Path]:
    saida = []
    for p in sorted(ENTREGAS.glob("W*.json")):
        if ".bak-" in p.name:
            continue
        if onda and not p.name.startswith(onda + "-"):
            continue
        saida.append(p)
    return saida


def onda_de(nome: str) -> str:
    return nome.split("-", 1)[0]


def board_do_board() -> dict[str, dict[str, int]]:
    """Cards por onda, lidos do board em MODO LEITURA (mesma postura do verificador da projeção)."""
    con = sqlite3.connect("file:%s?mode=ro" % BANCO, uri=True)
    por_onda: dict[str, dict[str, int]] = {}
    for tid, titulo, status in con.execute("select id, title, status from tasks"):
        tok = next((t for t in (titulo or "").split() if t.startswith("TRE-W")), None)
        if not tok:
            continue
        w = tok.split("-")[1]
        d = por_onda.setdefault(w, {})
        d[status] = d.get(status, 0) + 1
    return por_onda


def homologacoes_por_onda() -> dict[str, dict[str, int]]:
    con = sqlite3.connect("file:%s?mode=ro" % BANCO, uri=True)
    linhas = list(con.execute(
        "select t.title, count(*) from task_comments c join tasks t on t.id = c.task_id "
        "where c.body like ? group by t.title", ("%" + HOMOLOG_POR_CARD + "%",)))
    res: dict[str, dict[str, int]] = {}
    for titulo, n in linhas:
        tok = next((t for t in (titulo or "").split() if t.startswith("TRE-W")), None)
        if not tok:
            continue
        res.setdefault(tok.split("-")[1], {})["homologados"] = res.get(tok.split("-")[1], {}).get("homologados", 0) + n
    return res


def proposta(art: dict, board: dict, homolog: dict, hoje_iso: str, agora_iso: str) -> dict:
    itens = art.get("work_items") or []
    ids = [i.get("id") for i in itens if i.get("id")]
    estagios = {}
    passou = 0
    for i in itens:
        estagios[i.get("stage")] = estagios.get(i.get("stage"), 0) + 1
        for ch in (i.get("children") or []):
            if str(ch.get("validation_result", "")).upper() == "PASS":
                passou += 1
    if not itens or set(estagios) != {"DONE"}:
        raise SystemExit("RECUSADO: %s tem work_items nao-DONE (%s) — nao fecho onda incompleta"
                         % (art.get("delivery_id"), estagios))
    ids_filhos = sorted({c.get("hermes_task_id") for i in itens for c in (i.get("children") or [])
                         if c.get("hermes_task_id")})
    aprov = copy.deepcopy(art.get("human_approval") or {})
    rodada = int(aprov.get("round") or 1) + 1
    escopo = list(aprov.get("scope") or [])
    escopo.append("fechamento da ENTREGA da onda com promoção à produção autorizada — decisão do dono no "
                  "Telegram, %s" % hoje_iso)
    aprov.update({
        "required": True, "status": "APPROVED", "decision": "APPROVED",
        "approved_by": APROVADOR, "approved_at": hoje_iso,
        "scope": escopo, "production_promotion_authorized": True, "round": rodada,
        "decided_by": APROVADOR, "decision_at": hoje_iso, "authority": "HUMAN_ONLY",
        "approved_items": ids,
        "evidence": {
            "como_foi_aprovado": ("Telegram, %s: \"%s\" — sobre a tabela das 9 ondas (cards 100%% `done`, "
                                  "`work_items` todos DONE, `human_approval` PENDING) apresentada ao dono, "
                                  "com a ressalva de que a rodada 1 nunca foi homologada." % (hoje_iso, PALAVRA)),
            "medido_em": hoje_iso,
            "no_artefato": ("%d work_items, todos DONE; %d filho(s) com validation_result=PASS"
                            % (len(itens), passou)),
            "no_board": ("cards do board desta onda: %s (kanban.db, leitura)"
                         % ", ".join("%s=%d" % kv for kv in sorted(board.items())) or "sem card"),
            "homologacao_por_card": ("%d comentário(s) de homologação do dono nos cards desta onda"
                                     % homolog.get("homologados", 0)),
            "autorizacao_de_producao": REF_AUTORIZACOES + "; " + ACEITE_PROD,
            "nao_medido": ("não houve bateria item-a-item nesta rodada: o que consta é o que o pipeline "
                           "registrou por item no próprio artefato e as homologações por card no board — "
                           "não uma reverificação independente feita agora."),
        },
    })
    aprov["history"] = list(aprov.get("history") or []) + [{
        "round": rodada, "at": agora_iso, "decidido_por": APROVADOR, "autoridade": "dono do board",
        "instrucao": PALAVRA,
        "escopo": ("fechar a entrega das ondas W1-W9 (cards e work_items já DONE) e autorizar a promoção à "
                   "produção; a rodada 1 permanecia PENDING"),
        "production_promotion_authorized": True,
        "nota": ("fecha o registro de entrega; NÃO autoriza release novo nem rollback (esses seguem exigindo "
                 "aprovação própria)."),
    }]
    novo = copy.deepcopy(art)
    novo["release_status"] = "PRODUCTION_PROMOTED"   # o valor que o plugin projeta na coluna de produção
    novo["current_gate"] = "DONE"
    novo["production_promotion_authorized"] = True   # espelha o W0: o campo existe também no nível de entrega
    # A projeção do painel só move card para a coluna de produção quando o artefato traz a lista EXPLÍCITA
    # (plugin_api._delivery_production_promoted_task_ids). Derivada — como no W0 — dos hermes_task_id dos
    # filhos dos work_items: nenhum id é inventado.
    novo["production_promoted_task_ids"] = ids_filhos
    novo.setdefault("completed_at", None)
    novo["completed_at"] = novo.get("completed_at") or agora_iso
    novo["updated_at"] = hoje_iso
    novo["human_approval"] = aprov
    return novo


def main() -> int:
    ap = argparse.ArgumentParser(description="Fecha a entrega das ondas no registro versionado (com --check).")
    ap.add_argument("--aplicar", action="store_true", help="escreve (sem isto, só relata)")
    ap.add_argument("--onda", help="fecha só esta onda (ex.: W3)")
    args = ap.parse_args()

    board = board_do_board()
    homolog = homologacoes_por_onda()
    agora_iso, hoje_iso = agora(), hoje()
    print("palavra do dono: %r | aprovado em: %s" % (PALAVRA, APROVADO_EM))
    escritos = 0
    for caminho in artefatos(args.onda):
        art = json.loads(caminho.read_text(encoding="utf-8"))
        onda = onda_de(caminho.name)
        ja_fechado = ((art.get("human_approval") or {}).get("status") == "APPROVED"
                      and art.get("release_status") == "PRODUCTION_PROMOTED"
                      and bool(art.get("production_promoted_task_ids")))
        if ja_fechado:
            print("   %-34s JA FECHADO (%s, aprovado por %s, %d promovido(s)) — nao re-decido"
                  % (caminho.name, art.get("release_status"),
                     (art.get("human_approval") or {}).get("approved_by"),
                     len(art.get("production_promoted_task_ids") or [])))
            continue
        novo = proposta(art, board.get(onda, {}), homolog.get(onda, {}), hoje_iso, agora_iso)
        antes_wi = sha(json.dumps(art.get("work_items"), ensure_ascii=False, sort_keys=True))
        depois_wi = sha(json.dumps(novo.get("work_items"), ensure_ascii=False, sort_keys=True))
        if antes_wi != depois_wi:
            raise SystemExit("RECUSADO: %s — work_items mudariam (evidencia seria adulterada)" % caminho.name)
        print("   %-34s %-20s -> %-20s gate=%s prod=%s aprov=%s (itens DONE=%d, evidencia intacta)"
              % (caminho.name, art.get("release_status"), novo["release_status"], novo["current_gate"],
                 novo["production_promotion_authorized"], novo["human_approval"]["status"],
                 len(novo.get("work_items") or [])))
        if args.aplicar:
            bkp = caminho.with_name("%s.bak-%s.json" % (caminho.stem, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
            if not bkp.exists():
                bkp.write_text(json.dumps(art, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            caminho.write_text(json.dumps(novo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # releitura: prova o que foi escrito e reafirma a integridade da evidencia
            lido = json.loads(caminho.read_text(encoding="utf-8"))
            assert lido["human_approval"]["status"] == "APPROVED"
            assert sha(json.dumps(lido.get("work_items"), ensure_ascii=False, sort_keys=True)) == antes_wi
            assert lido["release_status"] == "PRODUCTION_PROMOTED"
            assert lido["production_promoted_task_ids"] == sorted(
                {c.get("hermes_task_id") for i in (art.get("work_items") or [])
                 for c in (i.get("children") or []) if c.get("hermes_task_id")})
            assert len(lido["production_promoted_task_ids"]) > 0
            escritos += 1
    print("\n%s: %d artefato(s) examinado(s)%s"
          % ("APLICADO" if args.aplicar else "CHECK (nada escrito)", len(artefatos(args.onda)),
             ", %d ESCRITO(S)" % escritos if args.aplicar else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
