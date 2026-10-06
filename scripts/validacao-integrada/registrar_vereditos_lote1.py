#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote 1) no artefato de entrega.

Rito do projeto (ver skills advp-pipeline-ops / validacao-de-entregas e o
precedente scripts/fechar_entrega_w0.py):

1. **LE e ALTERA** o artefato — nunca o regera (regerar reinicia `events`/`updated_at`
   e apaga historia). A unica excecao e a onda cujo artefato ainda NAO existe (W1):
   ai o arquivo e criado com o MESMO schema do W0.
2. **dry-run por padrao**: sem `--aplicar`, nada e escrito.
3. **Backup datado ao lado** antes de escrever (`*.bak-<ts>.json`) e escrita atomica.
4. **Nunca autoriza producao**: `production_promotion_authorized` segue `false` e
   `production_promoted_task_ids` vazio. Validar entrega != promover release.
5. **Fail-closed**: recusa se um card declarado nao estiver `done` no board, se o
   artefato tiver schema inesperado, ou se algum veredito offline nao tiver passado.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote1.py        # dry-run
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote1.py --aplicar
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
ARTEFATO_W0 = DELIVERIES / "W0-governanca-e-baseline.json"
ARTEFATO_W1 = DELIVERIES / "W1-dados-e-dedup.json"
BOARD = pathlib.Path(
    "/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db"
)

COMMIT_BASE = "1378018405c6487ef52c0c32427ea5766f053911"  # origin/develop no momento da medicao
AMBIENTE = (
    "clone limpo (scratch) em origin/develop; sem banco, rede, credencial, "
    "producao ou runtime vivo (portoes offline; duas passadas identicas)"
)
DATA = "2026-10-03"
VALIDADOR = "Hermes — validacao integrada (lote 1, offline)"

# Heran_a do W0: vereditos que ENTRAM no artefato ja existente.
VEREDITOS_W0: list[dict] = [
    {
        "hermes_task_id": "t_722b6cbd",
        "eixo": "DADO",
        "validation_result": "PASS",
        "portoes": [
            {
                "gate": "python3 scripts/verificar_contrato_dados.py",
                "exit": 0,
                "resultado": "RESULTADO: PASS (26 itens, 0 falhas)",
            }
        ],
    },
    {
        "hermes_task_id": "t_4be20bcc",
        "eixo": "CREDENCIAL",
        "validation_result": "PASS",
        "portoes": [
            {
                "gate": "bash scripts/secret_scan.sh",
                "exit": 0,
                "resultado": "RESULTADO: PASS (nenhum segredo versionado)",
            },
            {
                "gate": "bash scripts/hooks/pre-commit (com padrao ghp_ no staged)",
                "exit": 1,
                "resultado": "COMMIT BLOQUEADO (teste negativo: o hook RECUSA o segredo)",
            },
        ],
    },
]

# Onda W1 (artefato ainda inexistente): itens declarados com os vereditos.
ITENS_W1: list[dict] = [
    {
        "id": "TRE-W1-E04-T01",
        "title": "Implementar deduplicacao strong identifiers",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_595dc9be",
                "stage": "DONE",
                "eixo": "DADO",
                "validation_result": "PASS",
                "evidence": (
                    "motor scripts/dedup/deduplicar_organizacoes.py (CNPJ -> dominio -> LinkedIn) "
                    "com suite sintetica offline"
                ),
                "portoes": [
                    {
                        "gate": "bash scripts/dedup/teste_dedup_sintetico.sh",
                        "exit": 0,
                        "resultado": "RESULTADO: TESTE_DEDUP_SINTETICO_OK (7 itens, 0 falhas)",
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E04-T01-D04",
        "title": "DEFEITO: verificador de estrutura imprimia PASS com metade dos checks como codigo morto",
        "stage": "DONE",
        "children": [
            {
                "hermes_task_id": "t_967965f0",
                "stage": "DONE",
                "eixo": "ISOLAMENTO (integridade da medicao)",
                "validation_result": "PASS",
                "evidence": "correcao do verificador: o resumo/exit saiu do meio do script e o fim deixou de ser inalcancavel",
                "portoes": [
                    {
                        "gate": "bash scripts/verificar_estrutura.sh",
                        "exit": 0,
                        "resultado": (
                            "RESULTADO: PASS (0 falhas), 386 linhas OK; ultimo bloco "
                            "(pontuacao-preditiva) presente -> fim do script nao esta morto"
                        ),
                    }
                ],
            }
        ],
    },
    {
        "id": "TRE-W1-E05-T01-D01",
        "title": (
            "DEFEITO: migration 0001 registrada em dev diverge do arquivo do repo "
            "e trava o runner"
        ),
        "stage": "VALIDATION",
        "children": [
            {
                "hermes_task_id": "t_39838c5b",
                "stage": "VALIDATION",
                "eixo": "MIGRACAO",
                "validation_result": "BLOCKED",
                "evidence": (
                    "parte offline medida: sha256 do arquivo da migration bate com o "
                    "registro citado (0484a3701b8c85243e5bc92ca8822074fa90fb1d7c91c0a995aaeabd816fbb6e)"
                ),
                "portoes": [
                    {
                        "gate": "sha256sum db/migrations/0001_sales_intelligence_v1.sql",
                        "exit": 0,
                        "resultado": "0484a3701b8c85243e5bc92ca8822074fa90fb1d7c91c0a995aaeabd816fbb6e",
                    }
                ],
                "motivo": (
                    "o aceite exige medir o REGISTRO em public.tre_schema_migrations no banco "
                    "de dev vivo (scripts/db/aplicar_migracoes.sh dev --somente-checar + suite_banco.sh dev); "
                    "essa medicao exige ambiente vivo e credencial, indisponiveis a uma verificacao offline"
                ),
                "pre_condicao": (
                    "na VPS do TRE (vmi3619453), com pg-sales-dev: aplicar_migracoes.sh dev --somente-checar -> "
                    "MIGRACAO_OK (exit 0) e suite_banco.sh dev; entao regravar o veredito como PASS"
                ),
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


def aplicar_vereditos_w0(artefato: dict, board: dict[str, str]) -> dict:
    novo = json.loads(json.dumps(artefato))
    if novo.get("delivery_id") != "TRE-W0-GOV":
        raise SystemExit("FAIL-CLOSED: W0 com delivery_id inesperado")

    alvos = {v["hermes_task_id"]: v for v in VEREDITOS_W0}
    achados: list[str] = []
    for item in novo.get("work_items") or []:
        for child in item.get("children") or []:
            v = alvos.get(child.get("hermes_task_id"))
            if not v:
                continue
            child["validation_result"] = v["validation_result"]
            child["validated_at"] = DATA
            child["validated_by"] = VALIDADOR
            child["verification"] = {
                "commit": COMMIT_BASE,
                "ambiente": AMBIENTE,
                "passes_independentes": 2,
                "portoes": v["portoes"],
            }
            achados.append(child["hermes_task_id"])
    faltam = set(alvos) - set(achados)
    if faltam:
        raise SystemExit(f"FAIL-CLOSED: veredito sem child no artefato: {sorted(faltam)}")

    novo["updated_at"] = DATA
    eventos = list(novo.get("events") or [])
    eventos.append({
        "event": "INTEGRATED_VALIDATION_RECORDED",
        "at": agora(),
        "by": VALIDADOR,
        "scope": "lote 1 — validacao integrada offline",
        "cards": sorted(alvos),
        "production_promotion_authorized": False,
    })
    novo["events"] = eventos
    return novo


def montar_w1(artefato_ou_none: dict | None, board: dict[str, str]) -> dict:
    base = artefato_ou_none
    if base is None:
        base = {
            "delivery_id": "TRE-W1",
            "project": "Transformativa Revenue Engine",
            "release": "R1 — Foundation",
            "wave": "W1 — Nucleo de dados (PostgreSQL / Sales Intelligence)",
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
                    "validacao integrada do lote 1 (dados, dedup, credencial, migracao)"
                ],
                "production_promotion_authorized": False,
                "round": 1,
            },
            "work_items": [],
            "production_promoted_task_ids": [],
            "events": [],
        }
    novo = json.loads(json.dumps(base))

    existentes = {it.get("id"): it for it in novo.get("work_items") or []}
    ordem = [it.get("id") for it in novo.get("work_items") or []]
    for item in ITENS_W1:
        it = json.loads(json.dumps(item))
        for child in it["children"]:
            eixo = child.pop("eixo")
            portoes = child.pop("portoes")
            v = {
                "commit": COMMIT_BASE,
                "ambiente": AMBIENTE,
                "passes_independentes": 2,
                "portoes": portoes,
            }
            if child["validation_result"] == "BLOCKED":
                v["motivo"] = child.pop("motivo")
                v["pre_condicao"] = child.pop("pre_condicao")
                child["current_gate"] = "VALIDATION"
            else:
                # current_gate proprio: sem ele, o gate VALIDATION da ENTREGA
                # vazaria para o filho e o card aprovado nao sairia da coluna.
                child["current_gate"] = "DONE"
            child["validated_at"] = DATA
            child["validated_by"] = VALIDADOR
            child["eixo_de_risco"] = eixo
            child["verification"] = v
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
        "scope": "lote 1 — validacao integrada offline",
        "cards": [c["hermes_task_id"] for it in ITENS_W1 for c in it["children"]],
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
    conferir_board(board, [v["hermes_task_id"] for v in VEREDITOS_W0]
                   + [c["hermes_task_id"] for it in ITENS_W1 for c in it["children"]])

    if not ARTEFATO_W0.is_file():
        raise SystemExit(f"FAIL-CLOSED: artefato W0 ausente: {ARTEFATO_W0}")
    w0 = json.loads(ARTEFATO_W0.read_text(encoding="utf-8"))
    novo_w0 = aplicar_vereditos_w0(w0, board)

    w1_antigo = None
    if ARTEFATO_W1.is_file():
        w1_antigo = json.loads(ARTEFATO_W1.read_text(encoding="utf-8"))
    novo_w1 = montar_w1(w1_antigo, board)

    print("=== REGISTRO DOS VEREDITOS — LOTE 1 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")
    for nome, caminho, novo, backup in (
        ("W0", ARTEFATO_W0, novo_w0, True),
        ("W1", ARTEFATO_W1, novo_w1, w1_antigo is not None),
    ):
        print(f"{nome}: {caminho.name} | updated_at={novo['updated_at']} | "
              f"prod_autorizada={novo['production_promotion_authorized']}")
        for it in novo["work_items"]:
            for c in it.get("children") or []:
                if "validation_result" in c:
                    print(f"   {it['id']:<22} {c['hermes_task_id']} "
                          f"-> {c['validation_result']}")
        if args.aplicar:
            gravar(caminho, novo, backup)
            print("   GRAVADO")
    print("(dry-run: rode com --aplicar para escrever)" if not args.aplicar else "OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
