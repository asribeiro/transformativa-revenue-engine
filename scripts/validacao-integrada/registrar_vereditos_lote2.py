#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote 2 — nucleo de dados + credenciais).

Rito do projeto (ver skills advp-pipeline-ops / validacao-de-entregas e os precedentes
scripts/validacao-integrada/registrar_vereditos_lote1.py e registrar_vereditos_lote_rls.py):

1. **LE e ALTERA** o artefato — nunca o regera. A unica excecao e a onda cujo artefato
   ainda NAO existe (aqui W6): ai o arquivo e criado com o MESMO schema do W0
   (`control-plane/deliveries/<onda>.json`).
2. **dry-run por padrao**: sem `--aplicar`, nada e escrito.
3. **Backup datado ao lado** antes de escrever (`*.bak-<ts>.json`) e escrita atomica.
4. **Nunca autoriza producao**: `production_promotion_authorized` segue `false` e
   `production_promoted_task_ids` vazio. Validar entrega != promover release.
5. **Fail-closed**: recusa se um card declarado nao estiver `done` no board, se o
   artefato tiver schema inesperado, ou se algum veredito nao tiver passado.

Contrato do veredito (o leitor do dashboard le estes campos):
  work_items[].children[] = {
      hermes_task_id, stage, current_gate, validation_result, evidence,
      validated_at, validated_by, eixo_de_risco,
      verification: {commit, ambiente, passes_independentes, portoes:[{gate, exit, resultado}]}
  }
  PASS  -> stage/current_gate DONE  (o item tambem precisa declarar DONE)

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote2.py        # dry-run
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote2.py --aplicar
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
ARTEFATO_W6 = DELIVERIES / "W6-outbound-e-canais.json"
BOARD = pathlib.Path(
    "/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db"
)

COMMIT_BASE = "ec97374e40c3f50f7b0803afb264ab9c64cf1469"  # origin/develop no momento da medicao
DATA = "2026-10-03"
VALIDADOR = "Hermes — validacao integrada (lote 2 — nucleo de dados e credenciais)"

AMB_OFFLINE_W6 = (
    "clone limpo (scratch) em origin/develop ec97374; aceite roda INTEIRO OFFLINE num sink "
    "descartavel proprio em 127.0.0.1 (TLS gerado na hora com openssl); nenhuma credencial "
    "Titan, nenhum host externo, nada de producao; duas passadas identicas"
)
AMB_DESC = (
    "VPS Contabo vmi3619453 (dev), copia isolada /tmp/tre_val/repo (tar de ec97374); "
    "container PostgreSQL DESCARTALVEL proprio com nome/prefixo unico (--rm), aplicando a "
    "migration 0001 congelada; containers do dev (pg-sales-dev/pg-odoo-dev/odoo-dev/"
    "proxy-dev/pg-wa-probe) INTOCADOS; nada escrito em sales_intelligence do dev/producao; "
    "duas passadas identicas"
)
AMB_ENTITY = (
    "VPS Contabo vmi3619453 (dev), copia isolada /tmp/tre_val/repo (tar de ec97374); cenario "
    "medido em container PostgreSQL DESCARTALVEL proprio (trv-ent-*, --rm) com OVERRIDE "
    "DECLARADO de deploy/environments/dev.env apontando para o container descartavel (a escrita "
    "em sales_intelligence do dev e vedada nesta rodada); containers do dev INTOCADOS; duas "
    "passadas identicas"
)
AMB_DEV_READONLY = (
    "VPS Contabo vmi3619453 (dev), copia isolada /tmp/tre_val/repo (tar de ec97374); medicao "
    "READ-ONLY contra o dev real (container pg-sales-dev, banco sales_intelligence, usuario "
    "sales_ai): estado_do_ambiente.sh + verificar_constraints_indices.py (somente SELECT) + "
    "verificar_contrato_dados.py offline; nenhuma escrita no dev; duas passadas identicas"
)

# --------------------------------------------------------------------------- #
# W1 — onda existente: ACRESCENTA os itens novos (nunca toca os ja gravados).
# --------------------------------------------------------------------------- #
ITENS_W1: list[dict] = [
    {
        "id": "TRE-W1-E02-T01",
        "title": "Criar tabelas core",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_d9cb5755",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "As 12 tabelas core do contrato nascem da propria migration 0001 congelada, e o "
                    "fixture de smoke (db/fixtures/smoke_dev.sql) carrega de forma IDEMPOTENTE com "
                    "contagem por tabela conferida. O verificador de fixture REPROVA linha a mais e "
                    "linha a menos (dentes), e o rollback e reversivel; a aplicacao roda em container "
                    "PostgreSQL DESCARTALVEL proprio, sem tocar nenhum ambiente real."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/db/teste-fixture-smoke.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: TESTE_OK (12 itens, 0 falhas); verificador APROVA alvo "
                            "integro (20 itens OK, 12 tabelas) — exit 0 nas 2 passadas independentes"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E03-T01",
        "title": "Criar constraints e índices",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_49e8e2e3",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "MIGRACAO",
                "evidence": (
                    "O alvo nasce da propria migration congelada e tem exatamente os 30 indices do "
                    "contrato (12 PK + 15 CREATE INDEX + 3 UNIQUE) e as PK/FK/UNIQUE item a item. O "
                    "verificador de constraints e indices tem DENTE: 7 mutacoes proibidas pelo "
                    "contrato (DROP/RENAME de indice, coluna errada, DROP de FK/UNIQUE/PK, indice a "
                    "mais) reprovam o alvo e, desfeita a mutacao, ele volta a aprovar. Roda em "
                    "container PostgreSQL DESCARTALVEL proprio."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/db/teste-constraints-indices.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: TESTE_OK (18 itens, 0 falhas); alvo integro 16 itens OK/30 "
                            "indices; 7 mutacoes REPROVADAS e reversiveis — exit 0 nas 2 passadas "
                            "independentes"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E04-T02",
        "title": "Implementar entity_match_confidence",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_430ba4cc",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "O campo entity_match_confidence e calculado, classificado por faixa e PERSISTIDO "
                    "e lido DE VOLTA do banco no registro auditado do merge (sync_events) e na "
                    "pendencia da fila humana (human_approvals): 0,94 na faixa REVISAO_HUMANA e 0,95 "
                    "na faixa de MERGE_AUTOMATICO, coerentes com o limiar do contrato (0,95); o campo "
                    "canonico == alias do E04-T01. As 4 sabotagens (persistencia, coerencia, limiar, "
                    "detalhe) reprovam a suite (dentes). O cenario usa container PostgreSQL "
                    "DESCARTALVEL proprio com override declarado de dev.env — a escrita em "
                    "sales_intelligence do dev e vedada nesta rodada."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/dedup/teste_entity_match_confidence.sh dev",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: TESTE_ENTITY_MATCH_CONFIDENCE_OK (22 itens, 0 falhas); "
                            "CENARIO_OK (21 itens); TESTE_DEDUP_AMBIENTE_OK (4 itens) — exit 0 nas 2 "
                            "passadas independentes"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E01-T01-EXEC",
        "title": "Criar database/schema Sales Intelligence (execucao)",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_969affa7",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "MIGRACAO",
                "evidence": (
                    "O schema sales_intelligence existe de fato no dev (12 tabelas | 30 indices, "
                    "medidos em leitura) e o registro da migration 0001 em public.tre_schema_migrations "
                    "bate com o arquivo do repo; o verificador de constraints/indices passa READ-ONLY "
                    "contra o alvo real (16 itens) e o verificador do contrato de dados passa offline "
                    "(26 itens). Nenhuma DDL em producao; nenhuma escrita no dev nesta medicao. Item "
                    "distinto do TRE-W1-E01-T01 (revisao independente, t_2cc57d80) para nao sobrescrever "
                    "o veredito ja gravado."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/db/estado_do_ambiente.sh dev",
                        "exit": 0,
                        "resultado": (
                            "12 tabelas | 30 indices no schema sales_intelligence; registro da migration "
                            "0001 == arquivo do repo — 2 passadas identicas"
                        ),
                    },
                    {
                        "gate": (
                            "python3 scripts/db/verificar_constraints_indices.py --banco "
                            "'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'"
                        ),
                        "exit": 0,
                        "resultado": "RESULTADO: PASS (16 itens, 0 falhas) — leitura pura, sem DDL/DML",
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
# W6 — artefato ainda inexistente: cria com o schema do W0.
# --------------------------------------------------------------------------- #
ITENS_W6: list[dict] = [
    {
        "id": "TRE-W6-E01-T01",
        "title": "Configurar Titan SMTP",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_6267d886",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "CREDENCIAL",
                "evidence": (
                    "Configuracao e primitivo de envio da Titan SMTP validados INTEIRO OFFLINE contra "
                    "um sink SMTP descartavel proprio em 127.0.0.1 (TLS implicito 2465 e STARTTLS 2587, "
                    "certificado gerado na hora): planejar/conferir, EHLO+TLS+AUTH+NOOP, entrega de UMA "
                    "mensagem com --confirmo, idempotencia por chave, guardas (host real em dev, prod, "
                    "destino fora do dominio de dev, porta x TLS, porta 25) todas RECUSADAS, senha nunca "
                    "em claro e desfazer preservando a trilha. Nenhuma credencial Titan nem host externo "
                    "usados; repo sai identico."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/integracoes/teste_smtp_titan_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_SMTP_TITAN_001_OK (23 itens, 0 falhas) — exit 0 nas 2 "
                            "passadas independentes"
                        ),
                    },
                    {
                        "gate": "bash scripts/integracoes/teste_smtp_titan_aceite.sh --prova-de-dente",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_SMTP_TITAN_001_OK (29 itens, 0 falhas) — as 5 mutacoes "
                            "reprovam O ITEM ESPERADO (guarda de host, matriz porta x TLS, --confirmo, "
                            "mascara de senha, idempotencia) e o modulo INTACTO volta verde (controle)"
                        ),
                    },
                ],
            }
        ],
    },
    {
        "id": "TRE-W6-E01-T02",
        "title": "Configurar Titan IMAP",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_9d38e360",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "CREDENCIAL",
                "evidence": (
                    "Leitura validada e guardada da caixa Titan IMAP, medida INTEIRO OFFLINE contra um "
                    "sink IMAP descartavel proprio em 127.0.0.1 (TLS implicito 2993 e STARTTLS 2143): "
                    "planejar/conferir, TLS+LOGIN+CAPACIDADE+EXAMINE+NOOP, --listar sem marcar lido, "
                    "ingesta com --confirmo gravando UMA copia por mensagem e replay JA_INGERIDO; "
                    "INVARIANTE de leitura provado no sink (todas as selecoes em EXAMINE, zero busca sem "
                    "PEEK, nenhuma flag \\Seen); guardas RECUSADAS; senha nunca em claro. Nenhuma "
                    "credencial Titan nem host externo."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/integracoes/teste_imap_titan_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_IMAP_TITAN_001_OK (33 itens, 0 falhas) — exit 0 nas 2 "
                            "passadas independentes"
                        ),
                    },
                    {
                        "gate": "bash scripts/integracoes/teste_imap_titan_aceite.sh --prova-de-dente",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_IMAP_TITAN_001_OK (40 itens, 0 falhas) — as 6 mutacoes "
                            "reprovam O ITEM ESPERADO (host, matriz porta x TLS, --confirmo, senha, "
                            "busca sem PEEK, idempotencia) e o modulo INTACTO volta verde (controle)"
                        ),
                    },
                ],
            }
        ],
    },
]

AMBIENTE_POR_CARD = {
    "t_6267d886": AMB_OFFLINE_W6,
    "t_9d38e360": AMB_OFFLINE_W6,
    "t_430ba4cc": AMB_ENTITY,
    "t_49e8e2e3": AMB_DESC,
    "t_d9cb5755": AMB_DESC,
    "t_969affa7": AMB_DEV_READONLY,
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
                    "validacao integrada do lote 2 (nucleo de dados e credenciais)"
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
            eixo = child.pop("eixo_de_risco")
            portoes = child.pop("portoes")
            if child["validation_result"] != "PASS":
                raise SystemExit(
                    f"FAIL-CLOSED: lote 2 so registra PASS; {child['hermes_task_id']} veio "
                    f"{child['validation_result']}"
                )
            child["validated_at"] = DATA
            child["validated_by"] = VALIDADOR
            child["eixo_de_risco"] = eixo
            child["verification"] = {
                "commit": COMMIT_BASE,
                "ambiente": AMBIENTE_POR_CARD[child["hermes_task_id"]],
                "passes_independentes": 2,
                "portoes": portoes,
            }
        if it["id"] in existentes:
            existentes[it["id"]].update(it)
        else:
            existentes[it["id"]] = it
            ordem.append(it["id"])
    novo["work_items"] = [existentes[i] for i in ordem]

    novo["updated_at"] = DATA
    novo["production_promotion_authorized"] = False
    novo["production_promoted_task_ids"] = []
    eventos = list(novo.get("events") or [])
    eventos.append({
        "event": "INTEGRATED_VALIDATION_RECORDED",
        "at": agora(),
        "by": VALIDADOR,
        "scope": "lote 2 — validacao integrada (nucleo de dados e credenciais)",
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
    p.add_argument("--onda", choices=["W1", "W6", "ambas"], default="ambas",
                   help="restringe o registro a uma onda (default: ambas)")
    args = p.parse_args()

    board = ler_board()
    todos = [c["hermes_task_id"] for lst in (ITENS_W1, ITENS_W6) for it in lst for c in it["children"]]
    conferir_board(board, todos)

    alvos: list[tuple[str, pathlib.Path, list[dict], str, str]] = []
    if args.onda in ("W1", "ambas"):
        if not ARTEFATO_W1.is_file():
            raise SystemExit(f"FAIL-CLOSED: artefato W1 ausente: {ARTEFATO_W1}")
        w1 = json.loads(ARTEFATO_W1.read_text(encoding="utf-8"))
        alvos.append(("W1", ARTEFATO_W1, ITENS_W1, "TRE-W1", w1.get("wave")))
    if args.onda in ("W6", "ambas"):
        w6_antigo = json.loads(ARTEFATO_W6.read_text(encoding="utf-8")) if ARTEFATO_W6.is_file() else None
        alvos.append((
            "W6", ARTEFATO_W6, ITENS_W6, "TRE-W6",
            "W6 — Outbound e canais (Titan SMTP/IMAP, geracao de abordagem, aprovacao humana e envio)",
        ))

    print("=== REGISTRO DOS VEREDITOS — LOTE 2 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")
    for nome, caminho, itens, delivery_id, wave in alvos:
        antigo = json.loads(caminho.read_text(encoding="utf-8")) if caminho.is_file() else None
        novo = _aplicar_itens(antigo, itens, delivery_id, wave)
        print(f"{nome}: {caminho.name} | updated_at={novo['updated_at']} | "
              f"prod_autorizada={novo['production_promotion_authorized']}")
        for it in novo["work_items"]:
            for c in it.get("children") or []:
                if c["hermes_task_id"] in todos:
                    print(f"   {it['id']:<24} {c['hermes_task_id']} -> {c['validation_result']}")
        if args.aplicar:
            gravar(caminho, novo, antigo is not None)
            print("   GRAVADO")
    print("(dry-run: rode com --aplicar para escrever)" if not args.aplicar else "OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
