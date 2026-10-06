#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote 5).

(A) DESTRAVA o unico BLOCKED da W3 (TRE-W3-E01-T04 / t_8b2ed1b7 — "oportunidade upsert").
    A ancora do dente 3 (`controlador-sem-upsert`) tinha apodrecido: procurava o literal
    `if existentes:`, que o fecho do card reescreveu. A ancora passou a ser o codigo ATUAL
    (`if registros:`, ramo "ha' registro casado -> ATUALIZA" de `_executar_upsert`), com a
    MESMA intencao e sem afrouxar o dente. BLOCKED -> PASS, preservando integralmente o
    historico do BLOCKED na `evidence`.

(B) W4 (eixo 3 restante + inicio do eixo 2): CRIA o artefato da onda W4 com os 5 agentes
    (Scout/Research/Signal/Pain Hypothesis/Contact Research) e o E2E Sales Intelligence,
    cada um com aceite E2E executado de verdade, 2 passadas identicas (byte a byte) e prova
    de dente.

Rito (skill validacao-de-entregas / precedentes lote1..lote4):
1. LE/ALTERA o artefato existente — nunca o regera; a onda W4 nasce nova (precedente: W6 no lote 2).
2. dry-run por padrao; `--aplicar` escreve.
3. backup datado ao lado antes de escrever; escrita atomica.
4. NUNCA autoriza producao (`production_promotion_authorized` segue false).
5. fail-closed: recusa se algum card declarado nao estiver `done` no board.

Contrato do veredito (leitor do dashboard): trabalho em `work_items[].children[]` = {hermes_task_id,
stage, current_gate:'DONE' quando PASS, validation_result, evidence, validated_at, validated_by,
eixo_de_risco, verification:{commit, ambiente, passes_independentes:'2', portoes:[{gate, exit,
resultado}]}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote5.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote5.py --aplicar
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import shutil
import sqlite3
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent.parent
DELIVERIES = RAIZ / "control-plane" / "deliveries"
ARTEFATO_W3 = DELIVERIES / "W3-integracao-odoo-pg.json"
ARTEFATO_W4 = DELIVERIES / "W4-agentes-e-e2e-sales-intelligence.json"
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")
PLUGIN_API = pathlib.Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")

COMMIT = "903424ee4834ced1648d370b5ceae19bd4e7e8d1"  # lote 5: ancora do dente 3 corrigida
DATA = "2026-10-03"
VALIDADOR = "Hermes — validacao integrada (lote 5 — destrava do BLOCKED W3-E01-T04 + agentes W4 + E2E SI)"

AMB_LOTE5 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): copia isolada /tmp/tre_lote5/repo (git archive de "
    "903424ee4834ced1648d370b5ceae19bd4e7e8d1, 11M). Os aceites sobem containers DESCARTALVEIS proprios "
    "(postgres:16 e/ou odoo:19.0) com nomes/banco/rede proprios e --rm na limpeza; containers do dev "
    "(pg-sales-dev/pg-odoo-dev/odoo-dev/proxy-dev/pg-wa-probe) INTOCADOS; nada escrito em sales_intelligence "
    "do dev/producao; duas passadas identicas por aceite (saida normalizada por nome de container/pid/porta/"
    "uuid/timestamp; no lote 5 as passadas sairam BYTE-IDENTICAS)."
)

# --------------------------------------------------------------------------- #
# (A) W3-E01-T04: BLOCKED -> PASS (preserva o historico do BLOCKED)
# --------------------------------------------------------------------------- #
RESOLUCAO_W3_T04 = (
    "RESOLVIDO no lote 5. A ancora do dente 3 (`controlador-sem-upsert`) do aceite foi corrigida para o "
    "codigo ATUAL: o ramo \"ha' registro casado -> ATUALIZA\" de `_executar_upsert` em "
    "controllers/api_controlada.py passou a ser ancorado em `if registros:` (logo abaixo da guarda de "
    "ambiguidade `if len(registros) > 1:`), com a MESMA intencao do antigo `if existentes:` (desligar o ramo "
    "de atualizacao -> cria sempre -> duplicata). A ancora continua EXATA: mutacao que nao aplica reprova o "
    "harness (fail-closed). PROVAS no commit 903424e (copia isolada /tmp/tre_lote5/repo): "
    "(1) `bash scripts/odoo/verificar-oportunidade-upsert.sh --prova-de-dente` -> exit 0, "
    "`RESULTADO: OPORTUNIDADE_UPSERT_DENTE_OK (3 provas, 0 falhas)`; o dente 3 reprova o item nomeado "
    "'segunda chamada nao atualizou' com 96 itens medidos; "
    "(2) PROVA NEGATIVA da propria reparacao: com a mutacao (`if registros:` -> `if False:`) aplicada A MAO "
    "numa copia do modulo, `--apenas-http` -> exit 1, `RESULTADO: OPORTUNIDADE_UPSERT_FALHOU (108 itens, 12 "
    "falhas)`, entre elas 'FALHOU segunda chamada nao atualizou' — o aceite REPROVA a mutacao como esperado; "
    "(3) DUAS passadas identicas do aceite normal: exit 0 nas duas, 125 itens OK cada, saida BYTE-IDENTICA, "
    "`RESULTADO: OPORTUNIDADE_UPSERT_OK (125 itens, 0 falhas)`."
)

PORTOES_W3_T04 = [
    {"gate": "bash scripts/odoo/verificar-oportunidade-upsert.sh (passada 1, apos conserto da ancora)",
     "exit": 0,
     "resultado": "RESULTADO: OPORTUNIDADE_UPSERT_OK (125 itens, 0 falhas) modulo=transformativa_sales_ai "
                  "banco=tre_e01_t04_oportunidade imagens=odoo:19.0+postgres:16"},
    {"gate": "bash scripts/odoo/verificar-oportunidade-upsert.sh (passada 2, identica)",
     "exit": 0,
     "resultado": "saida BYTE-IDENTICA a passada 1 (sha256 A3_pass1==A4_pass2); RESULTADO: "
                  "OPORTUNIDADE_UPSERT_OK (125 itens, 0 falhas)"},
    {"gate": "bash scripts/odoo/verificar-oportunidade-upsert.sh --prova-de-dente",
     "exit": 0,
     "resultado": "RESULTADO: OPORTUNIDADE_UPSERT_DENTE_OK (3 provas, 0 falhas): dente 1/2 OK; dente 3 "
                  "'controlador-sem-upsert' (ancora nova `if registros:`) reprova o item 'segunda chamada nao "
                  "atualizou' com 96 itens medidos"},
    {"gate": "PROVA NEGATIVA (mutation aplicada a mao na copia; ESPERA-SE exit != 0)",
     "exit": 1,
     "resultado": "mutacao `if registros:` -> `if False:` numa copia do modulo; `--apenas-http` -> "
                  "RESULTADO: OPORTUNIDADE_UPSERT_FALHOU (108 itens, 12 falhas); entre elas 'FALHOU segunda "
                  "chamada nao atualizou' — reprovacao esperada da prova negativa"},
]

# --------------------------------------------------------------------------- #
# (B) W4: 5 agentes (eixo 3) + E2E Sales Intelligence (eixo 2)
# --------------------------------------------------------------------------- #
def _item(idx: str, titulo: str, tid: str, evidence: str, portoes: list[dict]) -> dict:
    return {
        "id": idx,
        "title": titulo,
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": tid,
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": evidence,
                "validated_at": DATA,
                "validated_by": VALIDADOR,
                "verification": {
                    "commit": COMMIT,
                    "ambiente": AMB_LOTE5,
                    "passes_independentes": "2",
                    "portoes": portoes,
                },
            }
        ],
    }


ITENS_W4 = [
    _item(
        "TRE-W4-E01-T01", "Implementar Scout Agent", "t_fd3e41f0",
        (
            "Aceite E2E do AGENTE SCOUT v1 por EXECUCAO REAL em container PostgreSQL DESCARTALVEL proprio "
            "(pg-scout-acc): 10 candidatas sinteticas (3 novas, 2 que casam forte com as pre-existentes, 1 com "
            "fortes CONFLITANTES, 1 sem identificador forte, 1 com CNPJ invalido, 1 sem nome, 1 com fonte fora "
            "do vocabulario), retry da MESMA fonte nao cria duplicata; auditoria por pedido; guardas "
            "(`--ambiente prod` recusado sem escrita; `--planejar` com prefixo inexistente nao conecta); "
            "desfazer (dry-run nao apaga; `--confirmo` apaga SO o que a rodada criou). DUAS passadas identicas "
            "(exit 0 em ambas; 37 itens OK cada, saida BYTE-IDENTICA) e prova de dente 3/3 (cada mutacao pelo "
            "item esperado)."
        ),
        [
            {"gate": "bash scripts/agentes/teste_scout_aceite.sh (passada 1)", "exit": 0,
             "resultado": "RESULTADO: ACEITE_SCOUT_001_OK (37 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_scout_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida byte-identica a passada 1; RESULTADO: ACEITE_SCOUT_001_OK (37 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_scout_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (3/3 mutacoes detectadas, cada uma pelo item esperado): sem-idempotencia, "
                          "sem-forte-tambem-cria, revisao-nao-vai-para-a-fila-humana; ACEITE_SCOUT_001_OK"},
        ],
    ),
    _item(
        "TRE-W4-E02-T01", "Implementar Research Agent", "t_d9be7d3c",
        (
            "Aceite E2E do AGENTE RESEARCH v1 por EXECUCAO REAL em container PostgreSQL DESCARTALVEL proprio "
            "(pg-research-acc): 12 pedidos sinteticos (6 pesquisas legitimas de 4 tipos, 1 organizacao "
            "inexistente, 1 sem identificador forte, 1 com CNPJ invalido, 1 com fonte fora do vocabulario, 1 sem "
            "fonte, 1 conflito de identidade); retry da MESMA fonte nao cria research_run novo; replay "
            "ON CONFLICT DO NOTHING; enriquecimento NAO sobrescreve dado curado; guardas (`prod` recusado; "
            "`--planejar` sem conexao) e desfazer (dry-run nao apaga; `--confirmo` restaura colunas e registra "
            "ROLLBACK). DUAS passadas identicas (exit 0 em ambas; 55 itens OK cada, saida BYTE-IDENTICA) e prova "
            "de dente 4/4."
        ),
        [
            {"gate": "bash scripts/agentes/teste_research_aceite.sh (passada 1)", "exit": 0,
             "resultado": "RESULTADO: ACEITE_RESEARCH_001_OK (55 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_research_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida byte-identica a passada 1; RESULTADO: ACEITE_RESEARCH_001_OK (55 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_research_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (4/4 mutacoes detectadas, cada uma pelo item esperado): sem-idempotencia, "
                          "enriquecimento-sem-coalesce, coluna-fora-do-tipo-liberada, numero-fora-do-tipo-liberado"},
        ],
    ),
    _item(
        "TRE-W4-E03-T01", "Implementar Signal Detector", "t_61a620b4",
        (
            "Aceite E2E do AGENTE SIGNAL DETECTOR v1 por EXECUCAO REAL em container PostgreSQL DESCARTALVEL "
            "proprio (pg-signal-acc): deteccao legitima grava `signals` com categoria DERIVADA do tipo; a "
            "deteccao NAO cria organizacao e NAO escreve coluna de `organizations`; nenhum score e' escrito; "
            "descartes com motivo (data/confianca/categoria/titulo/vinculo com research_run inexistente); retry "
            "nao duplica; `prod` recusado e `--planejar` nao abre conexao; desfazer preserva organizations, "
            "research_runs, agent_runs e human_approvals. DUAS passadas identicas (exit 0 em ambas; 64 itens OK "
            "cada, saida BYTE-IDENTICA) e prova de dente 4/4."
        ),
        [
            {"gate": "bash scripts/agentes/teste_signal_aceite.sh (passada 1)", "exit": 0,
             "resultado": "RESULTADO: ACEITE_SIGNAL_001_OK (64 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_signal_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida byte-identica a passada 1; RESULTADO: ACEITE_SIGNAL_001_OK (64 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_signal_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (4/4 mutacoes detectadas, cada uma pelo item esperado): sem-idempotencia, "
                          "fechamento-sem-ancora-no-sinal, categoria-chumbada, vinculo-quebrado-aceito"},
        ],
    ),
    _item(
        "TRE-W4-E04-T01", "Implementar Pain Hypothesis Agent", "t_b4e01433",
        (
            "Aceite E2E do AGENTE PAIN HYPOTHESIS v1 por EXECUCAO REAL em container PostgreSQL DESCARTALVEL "
            "proprio (pg-pain-acc): hipotese COM lastro grava `pain_hypotheses` como INFERENCIA com lastro "
            "amarrado as origens reais; hipotese SEM lastro NAO e' gravada (SEM_EVIDENCIA_VALIDA) e nada e' "
            "escrito em organizations/signals/research_runs; nenhuma coluna proibida escrita e status sempre "
            "HYPOTHESIS; descartes com motivo; retry nao duplica; `prod` recusado (exit 4) e `--planejar` sem "
            "conexao; desfazer preserva a base. DUAS passadas identicas (exit 0 em ambas; 85 itens OK cada, "
            "saida BYTE-IDENTICA) e prova de dente 5/5."
        ),
        [
            {"gate": "bash scripts/agentes/teste_pain_hypothesis_aceite.sh (passada 1)", "exit": 0,
             "resultado": "RESULTADO: ACEITE_PAIN_001_OK (85 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_pain_hypothesis_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida byte-identica a passada 1; RESULTADO: ACEITE_PAIN_001_OK (85 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_pain_hypothesis_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (5/5 mutacoes detectadas, cada uma pelo item esperado): sem-idempotencia, "
                          "fechamento-sem-ancora-na-hipotese, lastro-nao-conferido, lastro-de-outra-empresa-aceito, "
                          "status-chumbado-validado"},
        ],
    ),
    _item(
        "TRE-W4-E05-T01", "Implementar Contact Research", "t_1a85a424",
        (
            "Aceite E2E do AGENTE CONTACT RESEARCH v1 por EXECUCAO REAL em container PostgreSQL DESCARTALVEL "
            "proprio (pg-contact-acc): 11 pedidos (contatos novos legitimos, enriquecimento do contato semeado "
            "casando sem diferenca de caixa, organizacao inexistente, sem identificador forte, e-mail invalido, "
            "sem base legal, sem fonte, conflito de identidade); retry nao cria duplicata; replay ON CONFLICT DO "
            "NOTHING; guardas (`prod` recusado; `--planejar` sem conexao); desfazer dry-run/`--confirmo` "
            "preservando o contato semeado (cargo curado e opt-out). DUAS passadas identicas (exit 0 em ambas; "
            "65 itens OK cada, saida BYTE-IDENTICA) e prova de dente 4/4."
        ),
        [
            {"gate": "bash scripts/agentes/teste_contact_research_aceite.sh (passada 1)", "exit": 0,
             "resultado": "RESULTADO: ACEITE_CONTACT_RESEARCH_001_OK (65 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_contact_research_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida byte-identica a passada 1; RESULTADO: ACEITE_CONTACT_RESEARCH_001_OK "
                          "(65 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_contact_research_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (4/4 mutacoes detectadas, cada uma pelo item esperado): sem-idempotencia, "
                          "enriquecimento-sem-coalesce, identidade-sem-lower, rollback-sem-guarda-de-espelho"},
        ],
    ),
    _item(
        "TRE-W4-E06-T01", "E2E Sales Intelligence", "t_a32ae24f",
        (
            "Aceite E2E 'SALES INTELLIGENCE' (gate da onda W4: empresa -> research/signals/hypothesis/contacts) "
            "por EXECUCAO REAL num UNICO container PostgreSQL DESCARTALVEL proprio (pg-e2e-si-acc): os cinco "
            "agentes da W4 rodam em SEQUENCIA no MESMO banco, o id produzido por um entrando como entrada do "
            "proximo; as 5 suites offline dos agentes rodam verdes ANTES; a cadeia NAO calcula score/tier/NBA "
            "(itens de ZERO, declarados). DUAS passadas identicas (exit 0 em ambas; `-- principal: 76 OK / 0 "
            "FALHOU`, saida BYTE-IDENTICA) e prova de dente 5/5 (uma mutacao por agente)."
        ),
        [
            {"gate": "bash scripts/e2e/verificar-e2e-sales-intelligence.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_E2E_SALES_INTELLIGENCE_001_OK (-- principal: 76 OK / 0 FALHOU; suites offline "
                          "dos 5 agentes verdes)"},
            {"gate": "bash scripts/e2e/verificar-e2e-sales-intelligence.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida byte-identica a passada 1; ACEITE_E2E_SALES_INTELLIGENCE_001_OK"},
            {"gate": "bash scripts/e2e/verificar-e2e-sales-intelligence.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (5/5 mutacoes detectadas, cada uma pelo item esperado): "
                          "scout-escreve-empresa-sem-identidade, pesquisa-run-sem-organizacao, "
                          "sinal-anexa-run-inexistente, hipotese-aceita-lastro-de-outra-empresa, "
                          "contato-sem-idempotencia"},
        ],
    ),
]

EVENTO = {
    "event": "INTEGRATED_VALIDATION_RECORDED",
    "by": VALIDADOR,
    "scope": "lote 5 — destrava do BLOCKED W3-E01-T04 (oportunidade upsert) + agentes W4 (eixo 3) + E2E Sales Intelligence (eixo 2)",
    "production_promotion_authorized": False,
}


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def ler_board() -> dict[str, str]:
    if not BOARD.is_file():
        raise SystemExit(f"FAIL-CLOSED: board ausente: {BOARD}")
    con = sqlite3.connect(f"file:{BOARD}?mode=ro", uri=True)
    try:
        return {tid: st for tid, st in con.execute("SELECT id, status FROM tasks")}
    finally:
        con.close()


def gravar(caminho: pathlib.Path, novo: dict, backup: bool) -> None:
    if backup and caminho.is_file():
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bkp = caminho.with_name(caminho.name.replace(".json", f".bak-{ts}.json"))
        shutil.copy2(caminho, bkp)
        print(f"  backup: {bkp}")
    tmp = caminho.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(novo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(caminho)


def atualizar_w3() -> dict:
    base = json.loads(ARTEFATO_W3.read_text(encoding="utf-8"))
    novo = json.loads(json.dumps(base))
    achado = 0
    for item in novo.get("work_items") or []:
        for child in item.get("children") or []:
            if child.get("hermes_task_id") != "t_8b2ed1b7":
                continue
            achado += 1
            hist = child.get("evidence") or ""
            pre = child.get("pre_condicao") or ""
            child["stage"] = "DONE"
            child["current_gate"] = "DONE"
            child["validation_result"] = "PASS"
            child["validated_at"] = DATA
            child["validated_by"] = VALIDADOR
            child["eixo_de_risco"] = child.get("eixo_de_risco") or "INTEGRACAO"
            child["evidence"] = (
                RESOLUCAO_W3_T04
                + "\n\n[HISTORICO DO VEREDITO BLOCKED — lote 4, PRESERVADO INTEGRALMENTE]\n"
                + hist
                + ("\n\n[PRE-CONDICAO REGISTRADA NO BLOCKED — cumprida no lote 5]\n" + pre if pre else "")
            )
            if "pre_condicao" in child:
                child["pre_condicao_cumprida"] = child.pop("pre_condicao")
            child["verification"] = {
                "commit": COMMIT,
                "ambiente": AMB_LOTE5,
                "passes_independentes": "2",
                "portoes": PORTOES_W3_T04,
            }
    if achado != 1:
        raise SystemExit(f"FAIL-CLOSED: esperava 1 child t_8b2ed1b7 na W3, achei {achado}")
    novo["updated_at"] = DATA
    novo["production_promotion_authorized"] = False
    novo["production_promoted_task_ids"] = []
    ev = dict(EVENTO)
    ev["at"] = agora()
    ev["cards"] = ["t_8b2ed1b7"] + [c["hermes_task_id"] for it in ITENS_W4 for c in it["children"]]
    novo.setdefault("events", []).append(ev)
    return novo


def montar_w4() -> dict:
    return {
        "delivery_id": "TRE-W4",
        "project": "Transformativa Revenue Engine",
        "release": "R1 — Foundation",
        "wave": "W4 — Agentes de inteligencia (Scout, Research, Signal, Pain Hypothesis, Contact Research) e E2E Sales Intelligence",
        "release_status": "IN_PROGRESS",
        "current_gate": "VALIDATION",
        "production_promotion_authorized": False,
        "updated_at": DATA,
        "human_approval": {
            "required": True,
            "status": "PENDING",
            "decision": "PENDING",
            "approved_by": None,
            "approved_at": None,
            "scope": ["validacao integrada do lote 5 (agentes W4 e E2E Sales Intelligence)"],
            "production_promotion_authorized": False,
            "round": 1,
        },
        "work_items": json.loads(json.dumps(ITENS_W4)),
        "production_promoted_task_ids": [],
        "events": [],
    }


def _validar_leitor(ids: list[str]) -> None:
    """Aceite da gravacao: o leitor do dashboard tem de classificar os cards como evidenciados."""
    if not PLUGIN_API.is_file():
        print(f"  AVISO: leitor ausente ({PLUGIN_API}) — aceite da gravacao nao verificado")
        return
    spec = importlib.util.spec_from_file_location("papi", str(PLUGIN_API))
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    colunas = m._delivery_lifecycle_task_columns(board="transformativa-revenue-engine")
    explicitos = m._delivery_explicit_done_task_ids(board="transformativa-revenue-engine")
    reter = [i for i in ids if i in (colunas or {})]
    print(f"  leitor: {len(ids)} cards; ainda retidos na coluna de validacao: {reter or 'nenhum'}")
    faltam = [i for i in ids if i not in (explicitos or set())]
    if faltam:
        raise SystemExit(f"FAIL-CLOSED: leitor NAO marcou como DONE explicito: {faltam}")
    print("  leitor: todos os cards classificados como DONE explicito (saem da coluna de validacao)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve (com backup datado)")
    args = p.parse_args()

    board = ler_board()
    ids = ["t_8b2ed1b7"] + [c["hermes_task_id"] for it in ITENS_W4 for c in it["children"]]
    faltando = [i for i in ids if board.get(i) != "done"]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")

    print("=== REGISTRO DOS VEREDITOS — LOTE 5 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")

    w3 = atualizar_w3()
    w4 = montar_w4()
    ev = dict(EVENTO)
    ev["at"] = agora()
    ev["cards"] = [c["hermes_task_id"] for it in w4["work_items"] for c in it["children"]]
    w4["events"].append(ev)

    print(f"W3: {ARTEFATO_W3.name} | itens={len(w3['work_items'])} | t_8b2ed1b7 -> PASS "
          f"(historico BLOCKED preservado na evidence)")
    print(f"W4: {ARTEFATO_W4.name} | itens={len(w4['work_items'])} | "
          f"prod_autorizada={w4['production_promotion_authorized']}")
    for it in w4["work_items"]:
        for c in it["children"]:
            print(f"   W4 {it['id']:<18} {c['hermes_task_id']} -> {c['validation_result']}")

    if not args.aplicar:
        print("(dry-run: rode com --aplicar para escrever)")
        return 0

    gravar(ARTEFATO_W3, w3, True)
    gravar(ARTEFATO_W4, w4, True)
    print("GRAVADO")
    _validar_leitor(ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
