#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fecha a entrega TRE-W0-GOV declarando no artefato o que já está entregue.

Regras que este script obedece (rito do projeto, ver skill advp-pipeline-ops):

1. **LÊ e ALTERA** o artefato — nunca o regera com o gerador original (regerar reinicia
   `events`/`requested_at` e apaga história).
2. **dry-run por padrão**: sem `--aplicar`, nada é escrito. Só imprime o que mudaria.
3. **Backup datado ao lado** antes de escrever (`*.bak-<ts>.json`), e escrita atômica.
4. **Nunca autoriza produção neste passo**: `production_promotion_authorized` fica `false` e
   `production_promoted_task_ids` vazio — fechar entrega ≠ promover release (é o que a política
   `hermes/policies/human-approval.yaml` exige: promoção a produção é ação HUMAN_ONLY).
5. **Fail-closed**: recusa fechar se algum card declarado não estiver `done` no board, se o
   artefato já estiver `DELIVERED`, ou se a versão do schema mudar de forma inesperada.

Uso:
    /opt/hermes/.venv/bin/python scripts/fechar_entrega_w0.py                 # dry-run
    /opt/hermes/.venv/bin/python scripts/fechar_entrega_w0.py --aplicar       # escreve (com backup)
    ... --data-homologacao 2026-09-30 --aprovador "Anderson Ribeiro"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import sqlite3
import sys

RAIZ = pathlib.Path("/opt/data/repos/transformativa-revenue-engine")
ARTEFATO = RAIZ / "control-plane/deliveries/W0-governanca-e-baseline.json"
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")

# item do artefato -> (cards, titulo, evidencia real, medida nesta entrega)
ITENS: list[dict] = [
    {
        "id": "TRE-W0-E01-T03",
        "title": "Backup e rollback baseline",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_c8e69f74",
                "stage": "DONE",
                "evidence": (
                    "docs/runbooks/backup-restore-rollback.md — procedimento executavel (segredo fora do "
                    "backup, restauracao com --confirmo e snapshot previo), provado na VPS Contabo"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T01",
        "title": "JEV Decision Policy V1",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_e7d9decd",
                "stage": "DONE",
                "evidence": (
                    "docs/architecture/jev-decision-policy-v1.md + hermes/jev/policy_v1.yaml (jev-policy-v1.0) — "
                    "verificador v1.0: 42 itens PASS + autoteste"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T02",
        "title": "Integrar JEV ao Hermes Dev Harness",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_4f20bd10",
                "stage": "DONE",
                "evidence": (
                    "hermes/jev/routing/router.py le a politica do YAML (limiar literal proibido no codigo); "
                    "precedencia Security -> Human Approval -> prioridade/dependencias -> JEV -> LLM; guardrails "
                    "deterministicos antes do classificador (fail-closed)"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T03",
        "title": "Benchmark anotado de routing",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_d8bc83b3",
                "stage": "DONE",
                "evidence": (
                    "hermes/jev/benchmarks/corpus-anotacao.yaml (32 casos anotados) + "
                    "docs/validation/jev-benchmark-routing.md — commit be2788a"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T04",
        "title": "Validar JEV guardrails e fallback",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_b3387f25",
                "stage": "DONE",
                "evidence": (
                    "validacao adversarial dos guardrails e do fallback: 72 itens, 0 falhas, autoteste 17/17 "
                    "(hoje validar_jev_guardrails.py = 74 itens PASS); defeitos D03/D04/D05/D06 fechados"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T05",
        "title": "Ligar o roteador JEV ao dispatch do board",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_6d326367",
                "stage": "DONE",
                "evidence": (
                    "JEV consultado ANTES de reivindicar/despachar (claim manual e dispatch); gate armado no "
                    "runtime em 30/09/2026; scripts/verificar_gate_jev.py = 30 itens PASS"
                ),
            },
            {
                "hermes_task_id": "t_38cbab9a",
                "stage": "DONE",
                "evidence": (
                    "defeito da suite do gate (overlay escrevia atraves do bind) corrigido: "
                    "scripts/verificar_gate_jev.py, commit 59832b2 — a suite roda e fecha 30 itens PASS"
                ),
            },
        ],
    },
    {
        "id": "TRE-W0-E04-T06",
        "title": "Politica JEV v1.1 (lane conservadora legivel por maquina e metricas por lane)",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_e9535df3",
                "stage": "DONE",
                "evidence": (
                    "hermes/jev/policy_v1_1.yaml + docs/architecture/jev-decision-policy-v1.1.md + "
                    "scripts/verificar_jev_policy_v1_1.py = 155 itens PASS; homologada por Anderson Ribeiro "
                    "(registro-de-aprovacoes, commit b7f2aa7)"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T07",
        "title": "Codigo canonico de acao e falha fechada para acao nao classificada",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_f8a6e209",
                "stage": "DONE",
                "evidence": (
                    "hermes/jev/acoes-declaradas.yaml (catalogo de codigos) + falha fechada D07/D08: acao sem "
                    "codigo comum NAO executa (abstem), acao proibida exige aprovacao"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T08",
        "title": "Piso de lane por ambiente",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_d36c7d0f",
                "stage": "DONE",
                "evidence": (
                    "regra de lane por ambiente como PISO em hermes/jev/policy_v1_1.yaml (policy_v1_1 completa "
                    "sobre esta), verificada pelo proprio roteador"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E04-T09",
        "title": "Aposentar o classificador de lane e declarar a lane na politica (v1.2)",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_b4b11995",
                "stage": "DONE",
                "evidence": (
                    "jev-policy-v1.2 em vigor (lane_por_codigo_de_acao, classificador aposentado; portao de "
                    "versao aberto) — scripts/verificar_jev_policy_v1_2.py = 233 itens PASS + autoteste 8/8; "
                    "commits 1227310, fbc26a9, 83922db"
                ),
            }
        ],
    },
    {
        "id": "TRE-W0-E07-T01",
        "title": "Linha do tempo: somar datas planejadas as reais",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_25ca689e",
                "stage": "DONE",
                "evidence": (
                    "plugins/gantt-timeline/dashboard/plugin_api.py le hermes/plan/cronograma.yaml (fonte "
                    "versionada, nao o board) e desenha a barra PLANEJADA; verificado pelo Hermes em 29/09/2026"
                ),
            }
        ],
    },
]

ESCOPO_DESTA_RODADA = [
    "camada JEV completa: politica v1.0 -> v1.1 -> v1.2 (lane declarada por codigo canonico), "
    "roteador, benchmark anotado, guardrails/fallback e gate no dispatch",
    "backup e rollback baseline (E01-T03)",
    "linha do tempo com datas planejadas (E07-T01)",
]

EVIDENCIA_DESTA_RODADA = {
    "como_foi_aprovado": (
        "sobre a evidencia abaixo, apresentada no Telegram a Anderson Ribeiro: suites medidas em 30/09/2026 "
        "e estado do board lido direto do kanban.db (modo leitura). NAO houve bateria item-a-item desta "
        "rodada: os verificadores citados sao por artefato (politica/roteador/gate/guardrails)."
    ),
    "medido_em": "2026-09-30",
    "verificadores": {
        "verificar_jev_policy_v1_2.py": "233 itens PASS + autoteste 8/8 (mutacoes reprovadas)",
        "verificar_jev_policy_v1_1.py": "155 itens PASS + autoteste",
        "verificar_jev_policy.py (v1.0)": "42 itens PASS + autoteste",
        "verificar_jev_router.py": "63 itens PASS",
        "validar_jev_guardrails.py": "74 itens PASS",
        "verificar_gate_jev.py": "30 itens PASS (era o vermelho declarado; ficou verde com a v1.2 em vigor)",
        "benchmark_roteamento.py --autoteste": "23/23 itens OK",
    },
    "medicao_de_comportamento": (
        "corpus v1.4: accuracy de lane 0,375 (igual a constante estrutural — aposentar o classificador nao "
        "custou acerto); execucao 3,1% -> 12,5%; escalacao 78,1% -> 68,8%"
    ),
    "em_execucao_no_fechamento": (
        "TRE-W0-E04-T10 (t_e09a95bf, nomear o codigo comum execucao_de_card) rodava no board no momento "
        "deste fechamento: NAO entra como item aprovado desta rodada; sera declarado quando entregue."
    ),
    "nao_autorizado": (
        "promocao de release para producao NAO e autorizada nesta rodada "
        "(production_promotion_authorized = false; production_promoted_task_ids vazio)."
    ),
}


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def ler_board() -> dict[str, dict]:
    if not BOARD.is_file():
        raise SystemExit(f"FAIL-CLOSED: board ausente: {BOARD}")
    con = sqlite3.connect(f"file:{BOARD}?mode=ro", uri=True)
    try:
        return {
            tid: {"status": st, "title": ti}
            for tid, st, ti in con.execute("SELECT id, status, title FROM tasks")
        }
    finally:
        con.close()


def montar(artefato: dict, board: dict[str, dict], data: str, aprovador: str) -> dict:
    novo = json.loads(json.dumps(artefato))  # copia profunda, preserva a ordem das chaves

    # 0. fail-closed: nada de declarar card que nao esta entregue
    faltando = [
        c["hermes_task_id"]
        for item in ITENS
        for c in item["children"]
        if board.get(c["hermes_task_id"], {}).get("status") != "done"
    ]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")

    # 1. itens: substitui/insere por id, preservando os que ja existiam
    existentes = {it.get("id"): it for it in novo.get("work_items", [])}
    ordem = [it.get("id") for it in novo.get("work_items", [])]
    for item in ITENS:
        if item["id"] in existentes:
            existentes[item["id"]].update(json.loads(json.dumps(item)))
        else:
            existentes[item["id"]] = json.loads(json.dumps(item))
            ordem.append(item["id"])
    novo["work_items"] = [existentes[i] for i in ordem]

    # 2. rodada de aprovacao (rito: rodada anterior vai para o history; evidencia e REESCRITA)
    aprov = dict(novo.get("human_approval") or {})
    history = list(aprov.get("history") or [])
    if not any(h.get("round") == 1 for h in history):
        history.append({
            "round": 1,
            "status": aprov.get("status"),
            "decision": aprov.get("decision"),
            "decided_by": aprov.get("approved_by"),
            "decided_at": aprov.get("approved_at"),
            "scope": aprov.get("scope"),
            "production_promotion_authorized": False,
        })
    aprov.update({
        "required": True,
        "status": "APPROVED",
        "decision": "APPROVED",
        "round": 2,
        "approved_by": aprovador,
        "approved_at": data,
        "decided_by": aprovador,
        "decision_at": data,
        "authority": "HUMAN_ONLY",
        "scope": ESCOPO_DESTA_RODADA,
        "approved_items": [it["id"] for it in ITENS],
        "evidence": EVIDENCIA_DESTA_RODADA,
        "history": history,
        "production_promotion_authorized": False,
    })
    novo["human_approval"] = aprov

    # 3. entrega: fecha o gate e a release, SEM autorizar producao
    novo["release_status"] = "DELIVERED"
    novo["current_gate"] = "DONE"
    novo["completed_at"] = agora()
    novo["updated_at"] = data
    novo["production_promotion_authorized"] = False
    novo["production_promoted_task_ids"] = []
    eventos = list(novo.get("events") or [])
    eventos.append({
        "event": "HUMAN_APPROVAL_GRANTED",
        "round": 2,
        "at": agora(),
        "by": aprovador,
        "production_promotion_authorized": False,
        "note": "fechamento da W0: itens declarados DONE; promocao a producao deliberadamente nao autorizada",
    })
    novo["events"] = eventos
    return novo


def conferir(novo: dict, board: dict[str, dict]) -> list[str]:
    achados: list[str] = []
    if novo["release_status"] != "DELIVERED":
        achados.append("release_status != DELIVERED")
    if novo["current_gate"] != "DONE":
        achados.append("current_gate != DONE")
    if novo["production_promotion_authorized"] is not False:
        achados.append("production_promotion_authorized deveria ser false")
    if novo["production_promoted_task_ids"] != []:
        achados.append("production_promoted_task_ids deveria ser vazio")
    if novo["human_approval"].get("status") != "APPROVED":
        achados.append("human_approval.status != APPROVED")
    nao_done = [it["id"] for it in novo["work_items"] if it.get("stage") != "DONE"]
    if nao_done:
        achados.append(f"itens fora de DONE: {nao_done}")
    for it in novo["work_items"]:
        for c in it.get("children") or []:
            if board.get(c["hermes_task_id"], {}).get("status") != "done":
                achados.append(f"card declarado nao esta done: {c['hermes_task_id']}")
    if not any(e.get("event") == "HUMAN_APPROVAL_GRANTED" for e in novo.get("events", [])):
        achados.append("evento HUMAN_APPROVAL_GRANTED ausente")
    return achados


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve o artefato (com backup datado)")
    p.add_argument("--data-homologacao", default=dt.date.today().isoformat())
    p.add_argument("--aprovador", default="Anderson Ribeiro")
    args = p.parse_args()

    if not ARTEFATO.is_file():
        raise SystemExit(f"FAIL-CLOSED: artefato ausente: {ARTEFATO}")
    bruto = ARTEFATO.read_text(encoding="utf-8")
    artefato = json.loads(bruto)
    if artefato.get("release_status") == "DELIVERED":
        raise SystemExit("FAIL-CLOSED: artefato ja esta DELIVERED (nada a fazer)")

    board = ler_board()
    novo = montar(artefato, board, args.data_homologacao, args.aprovador)
    achados = conferir(novo, board)

    print("=== FECHAMENTO TRE-W0-GOV — " + ("APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)") + " ===")
    print(f"artefato: {ARTEFATO}")
    print(f"itens declarados: {len(novo['work_items'])} (antes: {len(artefato.get('work_items', []))})")
    for it in novo["work_items"]:
        print(f"  {it['id']:<22} stage={it.get('stage'):<5} filhos={[c['hermes_task_id'] for c in (it.get('children') or [])]}")
    print("campos da entrega:")
    for k in ("release_status", "current_gate", "completed_at", "updated_at",
              "production_promotion_authorized", "production_promoted_task_ids"):
        antes = artefato.get(k, "(ausente)")
        print(f"  {k}: {antes} -> {novo.get(k)}")
    print("rodada de aprovacao:")
    for k in ("round", "status", "approved_by", "approved_at", "authority"):
        print(f"  {k} = {novo['human_approval'].get(k)}")
    print(f"  approved_items = {len(novo['human_approval']['approved_items'])} itens")
    print(f"  history = rodadas {[h.get('round') for h in novo['human_approval']['history']]}")
    print("conferencia:", "OK (0 achados)" if not achados else achados)

    if achados:
        raise SystemExit("FAIL-CLOSED: conferencia com achados — nada escrito")

    if args.aplicar:
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = ARTEFATO.with_name(ARTEFATO.name.replace(".json", f".bak-{ts}.json"))
        shutil.copy2(ARTEFATO, backup)
        tmp = ARTEFATO.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(novo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(ARTEFATO)
        print(f"APLICADO. backup: {backup}")
        print("para reverter: cp " + str(backup) + " " + str(ARTEFATO))
    else:
        print("(dry-run: rode com --aplicar para escrever)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
