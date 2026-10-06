#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote 3 — scores da W5 e observabilidade da W3).

Rito do projeto (ver skill validacao-de-entregas e os precedentes
scripts/validacao-integrada/registrar_vereditos_lote1.py e registrar_vereditos_lote2.py):

1. **LE e ALTERA** o artefato — nunca o regera. A unica excecao e a onda cujo artefato
   ainda NAO existe (aqui W5): ai o arquivo e criado com o MESMO schema do W0
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
  PASS -> stage/current_gate DONE (o item tambem precisa declarar DONE)

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote3.py            # dry-run
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote3.py --aplicar
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
ARTEFATO_W3 = DELIVERIES / "W3-integracao-odoo-pg.json"
ARTEFATO_W5 = DELIVERIES / "W5-scores-e-nba.json"
BOARD = pathlib.Path(
    "/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db"
)

COMMIT_BASE = "3d64b8d48b376a3ed09916d1ae9f947623c89475"  # origin/develop no momento da medicao
DATA = "2026-10-03"
VALIDADOR = "Hermes — validacao integrada (lote 3 — scores W5 e observabilidade W3)"

AMB_W5 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): copia isolada /tmp/tre_lote3/repo "
    "(git archive de 3d64b8d, 11M); o aceite sobe um container PostgreSQL DESCARTALVEL "
    "proprio por card (postgres:16, nome fixo do proprio script, --rm) e aplica a migration "
    "0001 congelada; containers do dev (pg-sales-dev/pg-odoo-dev/odoo-dev/proxy-dev/"
    "pg-wa-probe) INTOCADOS; nada escrito em sales_intelligence do dev/producao; "
    "duas passadas identicas (saida normalizada por container/tmp/uuid/timestamp/md5 de "
    "uuid-ordenacao)."
)
AMB_OBS = (
    "VPS Contabo vmi3619453 (dev, root via ssh): copia isolada /tmp/tre_lote3/repo "
    "(git archive de 3d64b8d); trio DESCARTÁVEL proprio (postgres:16 + n8nio/n8n:latest) "
    "com rede/banco/nomes proprios (rede e05t01-<rand>, banco tre_obs_<rand>, --rm), "
    "dev/homolog/producao medidos ANTES e DEPOIS; containers do dev INTOCADOS; "
    "duas passadas identicas (saida normalizada por nome do banco descartavel/tmp/repo)."
)

# --------------------------------------------------------------------------- #
# W5 — artefato ainda inexistente: cria com o schema do W0.
# --------------------------------------------------------------------------- #
ITENS_W5: list[dict] = [
    {
        "id": "TRE-W5-E01-T01",
        "title": "ICP Score V1",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_e4a90eba",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E do agente ICP Score v1 medido por EXECUCAO REAL num container "
                    "PostgreSQL DESCARTALVEL proprio: 6 scores CALCULADO com os valores conferidos "
                    "um a um (sweet spot 100,00 / logistica 86,00 / B2C 0,00 / sem dado 0,00 / B2B2C "
                    "94,00 / porte derivado 100,00), explicacao fecha a conta, auditoria sem LLM; "
                    "replay nao duplica (JA_EXISTE); a fonte que mente nao move o score; dado novo "
                    "grava linha NOVA preservando o historico; guardas (prod recusado exit 4 sem "
                    "escrita, --planejar sem conexao) e desfazer --confirmo apaga SO a rodada "
                    "preservando auditoria; os 4 containers do dev INTACTOS."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/agentes/teste_icp_score_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_ICP_SCORE_001_OK (50 itens, 0 falhas) — exit 0 nas 2 "
                            "passadas independentes (saida identica apos normalizacao)"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W5-E02-T01",
        "title": "Automation Fit Score V1",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_11815e63",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E do Automation Fit Score v1 (A1..A14) por EXECUCAO REAL em container "
                    "PostgreSQL DESCARTALVEL proprio: score gravado com tipo/versao/inputs/explicacao, "
                    "LE o estado e NAO escreve nas tabelas de negocio (em especial "
                    "organizations.data_quality_score), leitura filtrada por organization_id (sinal de "
                    "terceiro fora da conta), determinismo no banco (JA_CALCULADO) e historico (estado "
                    "novo cria linha nova), sem lastro RECUSADA sem escrita, identidade ambigua vai a "
                    "fila humana sem score, discriminacao medida (faixas/margem/desvio), prod recusado "
                    "exit 4 sem escrita, desfazer --confirmo apaga so a rodada preservando auditoria."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/agentes/teste_automation_fit_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_AUTOMATION_FIT_001_OK (76 itens, 0 falhas) — exit 0 nas "
                            "2 passadas independentes (saida identica apos normalizacao)"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W5-E03-T01",
        "title": "Buying Signal Score V1",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_967911e0",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E do Buying Signal Score v1 (A1..A10) por EXECUCAO REAL em container "
                    "PostgreSQL DESCARTALVEL proprio: score legitimo gravado em scores "
                    "(tipo/versao/inputs/explicacao/valid_until) sem tocar signals nem organizations; "
                    "prod recusado exit 4 sem escrita e --planejar sem conexao; replay nao duplica "
                    "(idempotencia) e sinal novo gera linha NOVA (historico); sem sinal utilizavel o "
                    "score e 0,00 com motivo SEM_SINAIS; empresa inexistente RECUSADA; desfazer "
                    "--confirmo apaga so a rodada; agent_runs/sync_events registrados."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/agentes/teste_buying_signal_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_BSS_001_OK (36 OK / 0 FALHOU) — exit 0 nas 2 passadas "
                            "independentes (saida identica apos normalizacao)"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W5-E04-T01",
        "title": "Data Quality Score V1",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_701c574f",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E do Data Quality Score v1 (AC1..AC13) por EXECUCAO REAL em container "
                    "PostgreSQL DESCARTALVEL proprio: mede a organizacao que JA existe (recusa quem "
                    "nao existe), grava UMA linha por organizacao em scores (DATA_QUALITY/v1.0) e "
                    "espelha o valor atual em organizations.data_quality_score sem tocar nenhuma outra "
                    "coluna (digital do estado de negocio IDENTICA antes/depois); valores exatos "
                    "conferiveis (100,00 / 77,00 / 0,00 / 65,71); replay nao duplica (JA_EXISTE); dado "
                    "novo gera medicao nova; --planejar nao escreve; prod recusado exit 4; soft-deleted "
                    "fica fora; desfazer --confirmo restaura o valor anterior preservando auditoria."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/scores/teste_data_quality_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_DATA_QUALITY_001_OK (35 itens, 0 falhas, 0 dentes "
                            "reprovados) — exit 0 nas 2 passadas independentes (saida identica apos "
                            "normalizacao)"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W5-E05-T01",
        "title": "Priority Score V1",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_8ab79fb9",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E do Priority Score v1 (A1..A10) por EXECUCAO REAL em container "
                    "PostgreSQL DESCARTALVEL proprio: score com o VALOR do contrato "
                    "(0,35*94+0,30*76+0,25*83+0,10*100 = 86,45), inputs com os quatro componentes "
                    "citados (id/versao/pesos) e sem renormalizacao; a gravacao NAO toca os scores dos "
                    "componentes (digital valor+versao intacta) nem organizations; prod recusado exit 4 "
                    "sem escrita e --planejar sem conexao; replay nao duplica e componente novo gera "
                    "linha NOVA; componente AUSENTE/VENCIDO recusa com SEM_LASTRO_COMPLETO sem escrever "
                    "(vencimento LIDO do banco); desfazer --confirmo apaga so a rodada."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/scores/teste_priority_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_PRIORITY_001_OK (46 OK / 0 FALHOU) — exit 0 nas 2 "
                            "passadas independentes (saida identica apos normalizacao)"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W5-E06-T01",
        "title": "Tiering",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_c6b9ecf1",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E do Tiering v1 (A1..A9) por EXECUCAO REAL em container PostgreSQL "
                    "DESCARTALVEL proprio: classificacao LEGITIMA registrada (operation=TIER em "
                    "sync_events) com tier/faixa/faixas vigentes/identidade do PRIORITY lido; a rodada "
                    "NAO toca scores (digital valor+versao intacta) nem organizations; prod recusado "
                    "exit 4 sem escrita e --planejar/--faixas sem conexao; replay nao duplica e PRIORITY "
                    "novo gera registro NOVO; empresa SEM PRIORITY recusa (SEM_PRIORITY) sem default "
                    "Nurture; PRIORITY vencido recusa (vencimento lido do banco); tier e do ULTIMO "
                    "PRIORITY; desfazer --confirmo apaga so o registro deixando a auditoria."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/scores/teste_tiering_aceite.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: ACEITE_TIERING_001_OK (47 OK / 0 FALHOU) — exit 0 nas 2 passadas "
                            "independentes (saida identica apos normalizacao)"
                        ),
                    }
                ],
            }
        ],
    },
]

# --------------------------------------------------------------------------- #
# W3 — onda existente: ACRESCENTA o item novo (observabilidade de sync), nunca
# toca os ja gravados. O gate da W3 nunca foi registrado nesta onda.
# --------------------------------------------------------------------------- #
ITENS_W3: list[dict] = [
    {
        "id": "TRE-W3-E05-T01",
        "title": "Criar observabilidade de sync",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_0b77a689",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": (
                    "Aceite E2E da observabilidade de sincronizacao (TRE-W3-E05-T01) medido por "
                    "EXECUCAO REAL num trio DESCARTÁVEL proprio (postgres:16 + n8nio/n8n:latest, "
                    "nomes/banco/rede proprios): o workflow versionado e' o montado agora pelos "
                    "artefatos versionados (lente estrutural); os estados A..H produzem o veredito "
                    "atribuivel (pior sinal: A OK, B ATENCAO, C CRITICO por dead-letter SEM motivo, "
                    "D ATENCAO por dead-letter COM motivo, E/F/G/H CRITICO) e a medicao e' replicada "
                    "pelos DOIS caminhos (consulta direta no psql x relatorio do workflow no n8n); "
                    "retrato das duas tabelas IDENTICO antes/depois (somente leitura); ambiente do dev "
                    "intocado (lista de bancos identica), homolog/prod sem arquivo novo, nenhum segredo "
                    "em claro no cofre do n8n descartavel."
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/n8n/verificar-observabilidade-sync.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: OBSERVABILIDADE_SYNC_OK (119 itens, 0 falhas) — exit 0 nas 2 "
                            "passadas independentes (saida identica apos normalizacao do nome do banco "
                            "descartavel)"
                        ),
                    }
                ],
            }
        ],
    },
]

AMBIENTE_POR_CARD = {
    "t_e4a90eba": AMB_W5,
    "t_11815e63": AMB_W5,
    "t_967911e0": AMB_W5,
    "t_701c574f": AMB_W5,
    "t_8ab79fb9": AMB_W5,
    "t_c6b9ecf1": AMB_W5,
    "t_0b77a689": AMB_OBS,
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
                    "validacao integrada do lote 3 (scores W5 e observabilidade W3)"
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
                    f"FAIL-CLOSED: lote 3 so registra PASS; {child['hermes_task_id']} veio "
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
        "scope": "lote 3 — validacao integrada (scores W5 e observabilidade W3)",
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
    p.add_argument("--onda", choices=["W3", "W5", "ambas"], default="ambas",
                   help="restringe o registro a uma onda (default: ambas)")
    args = p.parse_args()

    board = ler_board()
    todos = [c["hermes_task_id"] for lst in (ITENS_W3, ITENS_W5) for it in lst for c in it["children"]]
    conferir_board(board, todos)

    alvos: list[tuple[str, pathlib.Path, list[dict], str, str]] = []
    if args.onda in ("W5", "ambas"):
        w5_antigo = json.loads(ARTEFATO_W5.read_text(encoding="utf-8")) if ARTEFATO_W5.is_file() else None
        alvos.append((
            "W5", ARTEFATO_W5, ITENS_W5, "TRE-W5",
            "W5 — Scores e Next Best Action (ICP, Automation Fit, Buying Signal, Data Quality, "
            "Priority, Tiering)",
        ))
    if args.onda in ("W3", "ambas"):
        if not ARTEFATO_W3.is_file():
            raise SystemExit(f"FAIL-CLOSED: artefato W3 ausente: {ARTEFATO_W3}")
        w3_antigo = json.loads(ARTEFATO_W3.read_text(encoding="utf-8"))
        alvos.append(("W3", ARTEFATO_W3, ITENS_W3, "TRE-W3", w3_antigo.get("wave")))

    print("=== REGISTRO DOS VEREDITOS — LOTE 3 —",
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
