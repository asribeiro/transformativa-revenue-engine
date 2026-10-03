#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote RLS/tenant/isolamento).

Rito do projeto (ver skills advp-pipeline-ops / validacao-de-entregas e o
precedente scripts/validacao-integrada/registrar_vereditos_lote1.py):

1. **LE e ALTERA** o artefato — nunca o regera. A unica excecao e a onda cujo
   artefato ainda NAO existe (aqui W2 e W3): ai o arquivo e criado com o MESMO
   schema do W0 (`control-plane/deliveries/<onda>.json`).
2. **dry-run por padrao**: sem `--aplicar`, nada e escrito.
3. **Backup datado ao lado** antes de escrever (`*.bak-<ts>.json`) e escrita atomica.
4. **Nunca autoriza producao**: `production_promotion_authorized` segue `false` e
   `production_promoted_task_ids` vazio. Validar entrega != promover release.
5. **Fail-closed**: recusa se um card declarado nao estiver `done` no board, se o
   artefato tiver schema inesperado, ou se algum veredito offline nao tiver passado.

Contrato do veredito (o leitor do dashboard le estes campos):
  work_items[].children[] = {
      hermes_task_id, stage, current_gate, validation_result, evidence,
      validated_at, validated_by,
      verification: {commit, ambiente, passes_independentes, portoes:[{gate, exit, resultado}]}
  }
  PASS  -> stage/current_gate DONE  (o item tambem precisa declarar DONE)
  FAIL  -> stage/current_gate VALIDATION + verification.motivo/pre_condicao
  BLOCKED -> stage/current_gate VALIDATION + verification.motivo/pre_condicao

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote_rls.py        # dry-run
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote_rls.py --aplicar
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import sqlite3
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent.parent
DELIVERIES = RAIZ / "control-plane" / "deliveries"
ARTEFATO_W1 = DELIVERIES / "W1-dados-e-dedup.json"
ARTEFATO_W2 = DELIVERIES / "W2-odoo-e-seguranca.json"
ARTEFATO_W3 = DELIVERIES / "W3-integracao-odoo-pg.json"
BOARD = pathlib.Path(
    "/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db"
)

COMMIT_BASE = "393bc457a951a1e0bf77089b96e4b033be4ddc35"  # origin/develop no momento da medicao
AMBIENTE = (
    "VPS Contabo vmi3619453 (dev): container pg-sales-dev / banco sales_intelligence / "
    "usuario sales_ai; scripts lidos da copia isolada /tmp/verif-393bc45 (git archive de "
    "393bc45, nunca a copia operacional); verificacoes read-only (suite em --somente-leitura; "
    "ACL em duplas descartaveis que se removem); duas passadas identicas"
)
DATA = "2026-10-03"
VALIDADOR = "Hermes — validacao integrada (lote RLS/tenant/isolamento)"

# --------------------------------------------------------------------------- #
# W1 — onda existente: ALTERA o item D01 e ACRESCENTA os itens novos.
# --------------------------------------------------------------------------- #
ITENS_W1: list[dict] = [
    {
        "id": "TRE-W1-E05-T01-D01",
        "title": (
            "DEFEITO: migration 0001 registrada em dev diverge do arquivo do repo e trava o runner"
        ),
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_39838c5b",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "evidence": (
                    "registro em public.tre_schema_migrations realinhado (opcao 1 do dono, 30/09) e "
                    "CONFIRMADO no dev vivo: sha256 do registro (0484a3701b8c85243e5bc92c…) == sha256 "
                    "do arquivo db/migrations/0001_sales_intelligence_v1.sql; o runner --somente-checar "
                    "PULA a versao por ser o MESMO sha (MIGRACAO_OK, exit 0). Duas passadas identicas."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/db/aplicar_migracoes.sh dev --somente-checar",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: MIGRACAO_OK (--somente-checar; 0 aplicada(s)/pendente(s), "
                            "1 pulada(s), 4 itens, 0 falhas) — 2 passadas identicas"
                        ),
                    },
                    {
                        "gate": "bash scripts/db/estado_do_ambiente.sh dev",
                        "exit": 0,
                        "resultado": (
                            "registro 0001 | 0484a3701b8c85243e5bc92c… == arquivo do repo (iguais); "
                            "12 tabelas | 30 indices — 2 passadas identicas"
                        ),
                    },
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E05-T01",
        "title": "Criar database test suite (schema, constraints, isolamento)",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_c7281fce",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "evidence": (
                    "suite de banco unica (scripts/db/suite_banco.sh) medida no dev em modo "
                    "--somente-leitura (respeita o mandato read-only desta rodada: a etapa 4 dedup "
                    "ambiente roda em varredura --detectar e NAO escreve em sales_intelligence). "
                    "AC1 (schema x contrato), AC2 (isolamento na forma reformulada) e AC3 (exit code "
                    "confiavel) verdes; o teste de isolamento roda tambem isolado e independente."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/db/suite_banco.sh dev --somente-leitura",
                        "exit": 0,
                        "resultado": "RESULTADO: SUITE_OK (69 itens, 0 falhas) — 2 passadas identicas",
                    },
                    {
                        "gate": "bash scripts/db/teste_isolamento_clientes.sh dev",
                        "exit": 0,
                        "resultado": "RESULTADO: ISOLAMENTO_OK (5 itens, 0 falhas) — 2 passadas identicas",
                    },
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E05-T01-D02",
        "title": (
            "AC2 (tenant/RLS) nao testavel contra o Data Contract V1.0 — decisao de requisito"
        ),
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_e340c29b",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "evidence": (
                    "decisao do dono (30/09/2026, opcao A) registrada: isolamento FISICO — um banco por "
                    "cliente; AC2 reformulado para a forma testavel 'nao existem dois clientes no mesmo "
                    "banco'. A forma reformulada esta MEDIDA em dev (teste_isolamento_clientes.sh) e a "
                    "decisao aparece no Data Contract e no registro de aprovacoes. Tenant/RLS como "
                    "dimensao de 1a classe fica para o V2."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/db/teste_isolamento_clientes.sh dev",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ISOLAMENTO_OK (5 itens, 0 falhas) — o AC2 reformulado medido "
                            "contra o V1.0 — 2 passadas identicas"
                        ),
                    },
                    {
                        "gate": "grep decisao em docs/data/DATA_CONTRACT_V1.md + docs/operations/registro-de-aprovacoes.md",
                        "exit": 0,
                        "resultado": (
                            "decisao opcao A (isolamento fisico, um banco por cliente) registrada em 30/09/2026"
                        ),
                    },
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E01-T01",
        "title": "Schema em dev por migration versionada — revisao independente",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_2cc57d80",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "evidence": (
                    "revisao independente do E01-T01 reproduzida: a migration e versionada e o runner "
                    "aplica/pula em dev sem inventar estado; o banco sales_intelligence tem as 12 "
                    "tabelas previstas (medidas no banco); deploy/environments/dev.env declara o par "
                    "ambiente -> container/banco/usuario SEM segredo; o verificador de contrato nao "
                    "foi afrouxado (26 itens PASS)."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/db/estado_do_ambiente.sh dev",
                        "exit": 0,
                        "resultado": (
                            "12 tabelas | 30 indices; registro da migration 0484a370… == arquivo do repo "
                            "— 2 passadas identicas"
                        ),
                    },
                    {
                        "gate": "python3 scripts/verificar_contrato_dados.py",
                        "exit": 0,
                        "resultado": "RESULTADO: PASS (26 itens, 0 falhas)",
                    },
                ],
            }
        ],
    },
]

# --------------------------------------------------------------------------- #
# W2 — artefato ainda inexistente: cria com o schema do W0.
# --------------------------------------------------------------------------- #
ITEM_W2_E07: dict = {
    "id": "TRE-W2-E07-T01",
    "title": "Configurar ACLs/security (carteira x tenant)",
    "stage": "VALIDATION",
    "current_gate": "VALIDATION",
    "children": [
        {
            "hermes_task_id": "t_e0b1bcbf",
            "stage": "VALIDATION",
            "current_gate": "VALIDATION",
            "validation_result": "FAIL",
            "evidence": (
                "modulo do develop (git archive 393bc45) medido em dupla descartavel propria "
                "(postgres:16 + odoo:19.0); os 26 testes do modulo passam, mas a prova negativa do "
                "aceite REPROVA: AC3 exige que a superficie de ACL do modulo seja SO "
                "tf.process.opportunity (2 ACLs) e o develop tem tambem tf.evento.outbox (5 ACLs). "
                "Nao houve escrita no banco do dev: a medicao usa containers descartaveis (o proprio "
                "aceite mede a instancia do dev antes/depois)."
            ),
            "portoes": [
                {
                    "gate": "bash scripts/odoo/verificar-acl-modulo.sh (dupla descartavel postgres:16+odoo:19.0)",
                    "exit": 1,
                    "resultado": (
                        "RESULTADO: ACL_FALHOU (51 itens, 4 falha(s)) — 2 passadas identicas "
                        "(so rotulos de container/tmp diferem)"
                    ),
                },
            ],
            "motivo": (
                "AC3 do aceite (provar_acl_modulo.py) exige que a superficie de ACL do modulo seja SO "
                "o modelo tf.process.opportunity (2 ACLs); o develop adicionou o modelo "
                "tf.evento.outbox com 3 ACLs (card posterior TRE-W3-E03-T01, commit d0b8d5a) -> 5 "
                "ACLs, e o harness do E07 (commit 060c369, ultimo a toca-lo) reprova: 'superficie de "
                "ACL do modulo: esperado so tf.process.opportunity' + 'ACLs do modulo: 5 (esperado 2)', "
                "arrastando 2 falhas em cascata na prova negativa. Defeito de INTEGRACAO entre ondas "
                "(o harness do E07 ficou preso a superficie de ACL do seu commit)."
            ),
            "pre_condicao": (
                "ampliar o harness do E07 (provar_acl_modulo.py / verificar-acl-modulo.sh) para "
                "declarar a superficie de ACL esperada INCLUINDO tf.evento.outbox, com aprovacao "
                "humana registrada; ou decisao de requisito. Entao regravar como PASS. "
                "(Nao e escrita em sales_intelligence: as ACLs sao do modulo Odoo.)"
            ),
        }
    ],
}

# --------------------------------------------------------------------------- #
# W3 — artefato ainda inexistente; cards fora do eixo RLS/tenant/isolamento,
# nao medidos nesta rodada (aceite E2E exige trio descartavel n8n+odoo+postgres).
# --------------------------------------------------------------------------- #
MOTIVO_W3 = (
    "aceite E2E exige trio descartavel proprio (postgres + Odoo + n8n) com execucao real "
    "(scripts/n8n/verificar-*.sh); nao executado nesta rodada — fora do eixo RLS/tenant/isolamento "
    "e do mandato read-only desta rodada. A revisao independente (estagio 6) ja esta registrada no card."
)
PRE_W3 = (
    "rodar scripts/n8n/verificar-<card>.sh na VPS (trio descartavel proprio), duas passadas "
    "identicas; entao regravar PASS/FAIL."
)
ITENS_W3: list[dict] = [
    {
        "id": "TRE-W3-E04-T01",
        "title": "Criar reconciliation job (n8n)",
        "stage": "VALIDATION",
        "current_gate": "VALIDATION",
        "children": [
            {
                "hermes_task_id": "t_2ee17829",
                "stage": "VALIDATION",
                "current_gate": "VALIDATION",
                "validation_result": "BLOCKED",
                "evidence": "aceite: scripts/n8n/verificar-reconciliacao.sh (trio descartavel + n8n)",
                "portoes": [
                    {
                        "gate": "bash scripts/n8n/verificar-reconciliacao.sh",
                        "exit": None,
                        "resultado": "NAO EXECUTADO nesta rodada (exige trio descartavel n8n+odoo+postgres)",
                    }
                ],
                "motivo": MOTIVO_W3,
                "pre_condicao": PRE_W3,
            }
        ],
    },
    {
        "id": "TRE-W3-E03-T01",
        "title": "Implementar Odoo→PG events",
        "stage": "VALIDATION",
        "current_gate": "VALIDATION",
        "children": [
            {
                "hermes_task_id": "t_85cb2838",
                "stage": "VALIDATION",
                "current_gate": "VALIDATION",
                "validation_result": "BLOCKED",
                "evidence": "aceite: scripts/n8n/verificar-odoo-eventos.sh (trio descartavel + n8n)",
                "portoes": [
                    {
                        "gate": "bash scripts/n8n/verificar-odoo-eventos.sh",
                        "exit": None,
                        "resultado": "NAO EXECUTADO nesta rodada (exige trio descartavel n8n+odoo+postgres)",
                    }
                ],
                "motivo": MOTIVO_W3,
                "pre_condicao": PRE_W3,
            }
        ],
    },
    {
        "id": "TRE-W3-E02-T01",
        "title": "Implementar outbox consumer n8n",
        "stage": "VALIDATION",
        "current_gate": "VALIDATION",
        "children": [
            {
                "hermes_task_id": "t_ba84b412",
                "stage": "VALIDATION",
                "current_gate": "VALIDATION",
                "validation_result": "BLOCKED",
                "evidence": "aceite: scripts/n8n/verificar-outbox-consumer.sh (trio descartavel + n8n)",
                "portoes": [
                    {
                        "gate": "bash scripts/n8n/verificar-outbox-consumer.sh",
                        "exit": None,
                        "resultado": "NAO EXECUTADO nesta rodada (exige trio descartavel n8n+odoo+postgres)",
                    }
                ],
                "motivo": MOTIVO_W3,
                "pre_condicao": PRE_W3,
            }
        ],
    },
    {
        "id": "TRE-W3-E02-T02",
        "title": "Implementar idempotency (dedup por idempotency_key)",
        "stage": "VALIDATION",
        "current_gate": "VALIDATION",
        "children": [
            {
                "hermes_task_id": "t_3bde06ab",
                "stage": "VALIDATION",
                "current_gate": "VALIDATION",
                "validation_result": "BLOCKED",
                "evidence": (
                    "aceite: scripts/n8n/verificar-outbox-consumer.sh (mesmo harness do T01; AC9 dedup)"
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/n8n/verificar-outbox-consumer.sh",
                        "exit": None,
                        "resultado": "NAO EXECUTADO nesta rodada (exige trio descartavel n8n+odoo+postgres)",
                    }
                ],
                "motivo": MOTIVO_W3,
                "pre_condicao": PRE_W3,
            }
        ],
    },
]


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


def conferir_board(board: dict[str, str], ids: list[str]) -> None:
    faltando = [i for i in ids if board.get(i) != "done"]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")


def _aplicar_itens(base: dict | None, itens: list[dict], delivery_id: str, wave: str) -> dict:
    if base is None:
        novo = {
            "delivery_id": delivery_id,
            "project": "Transformativa Revenue Engine",
            "release": "R1 — Foundation",
            "wave": wave,
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
                "scope": [
                    "validacao integrada do lote RLS/tenant/isolamento"
                ],
                "production_promotion_authorized": False,
                "round": 1,
            },
            "work_items": [],
            "production_promoted_task_ids": [],
            "events": [],
        }
    else:
        novo = json.loads(json.dumps(base))
        if novo.get("delivery_id") != delivery_id:
            raise SystemExit(
                f"FAIL-CLOSED: {delivery_id} com delivery_id inesperado: {novo.get('delivery_id')!r}"
            )

    existentes = {it.get("id"): it for it in novo.get("work_items") or []}
    ordem = [it.get("id") for it in novo.get("work_items") or []]
    for item in itens:
        it = json.loads(json.dumps(item))
        for child in it.get("children") or []:
            portoes = child.pop("portoes")
            vr = child["validation_result"]
            v: dict = {
                "commit": COMMIT_BASE,
                "ambiente": AMBIENTE,
                "passes_independentes": 2,
                "portoes": portoes,
            }
            if vr in ("BLOCKED", "FAIL"):
                v["motivo"] = child.pop("motivo")
                v["pre_condicao"] = child.pop("pre_condicao")
            child["validated_at"] = DATA
            child["validated_by"] = VALIDADOR
            child["verification"] = v
        if it["id"] in existentes:
            existentes[it["id"]].update(it)
        else:
            existentes[it["id"]] = it
            ordem.append(it["id"])
    novo["work_items"] = [existentes[i] for i in ordem]

    novo["updated_at"] = DATA
    novo["production_promotion_authorized"] = False
    eventos = list(novo.get("events") or [])
    eventos.append({
        "event": "INTEGRATED_VALIDATION_RECORDED",
        "at": agora(),
        "by": VALIDADOR,
        "scope": "lote RLS/tenant/isolamento — validacao integrada (dev, read-only)",
        "cards": [c["hermes_task_id"] for it in itens for c in it["children"]],
        "production_promotion_authorized": False,
    })
    novo["events"] = eventos
    return novo


def gravar(caminho: pathlib.Path, novo: dict, backup: bool) -> None:
    if backup and caminho.is_file():
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bkp = caminho.with_name(caminho.name.replace(".json", f".bak-{ts}.json"))
        shutil.copy2(caminho, bkp)
        print(f"  backup: {bkp}")
    tmp = caminho.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(novo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))  # valida antes de trocar
    tmp.replace(caminho)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve (com backup datado)")
    args = p.parse_args()

    board = ler_board()
    todos = [c["hermes_task_id"] for lst in (ITENS_W1, [ITEM_W2_E07], ITENS_W3)
             for it in lst for c in it["children"]]
    conferir_board(board, todos)

    if not ARTEFATO_W1.is_file():
        raise SystemExit(f"FAIL-CLOSED: artefato W1 ausente: {ARTEFATO_W1}")
    w1 = json.loads(ARTEFATO_W1.read_text(encoding="utf-8"))
    novo_w1 = _aplicar_itens(w1, ITENS_W1, "TRE-W1", w1.get("wave"))

    w2_antigo = json.loads(ARTEFATO_W2.read_text(encoding="utf-8")) if ARTEFATO_W2.is_file() else None
    novo_w2 = _aplicar_itens(w2_antigo, [ITEM_W2_E07], "TRE-W2",
                             "W2 — Odoo (modulo transformativa_sales_ai e seguranca)")

    w3_antigo = json.loads(ARTEFATO_W3.read_text(encoding="utf-8")) if ARTEFATO_W3.is_file() else None
    novo_w3 = _aplicar_itens(w3_antigo, ITENS_W3, "TRE-W3",
                             "W3 — Integracao Odoo -> PostgreSQL (n8n)")

    print("=== REGISTRO DOS VEREDITOS — LOTE RLS/TENANT/ISOLAMENTO —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")
    for nome, caminho, novo, backup in (
        ("W1", ARTEFATO_W1, novo_w1, True),
        ("W2", ARTEFATO_W2, novo_w2, w2_antigo is not None),
        ("W3", ARTEFATO_W3, novo_w3, w3_antigo is not None),
    ):
        print(f"{nome}: {caminho.name} | updated_at={novo['updated_at']} | "
              f"prod_autorizada={novo['production_promotion_authorized']}")
        for it in novo["work_items"]:
            for c in it.get("children") or []:
                if c["hermes_task_id"] in todos:
                    print(f"   {it['id']:<24} {c['hermes_task_id']} -> {c['validation_result']}")
        if args.aplicar:
            gravar(caminho, novo, backup)
            print("   GRAVADO")
    print("(dry-run: rode com --aplicar para escrever)" if not args.aplicar else "OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
