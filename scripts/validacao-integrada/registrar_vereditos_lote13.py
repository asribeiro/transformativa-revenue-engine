#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra no artefato TRE-W2 os vereditos da validacao integrada do LOTE 13.

Lote 13 = 10 cards W2/Odoo da fila (ordens 120..129). Cada card validado com:
  2 passadas identicas (mesmo veredito) + prova de dente (mutacao que TEM de reprovar),
  sempre em clone isolado do commit publicado, com bancos/rede descartaveis e o
  `odoo_dev` conferido intacto antes e depois (ADR-005).

Uso:  python3 scripts/validacao-integrada/registrar_vereditos_lote13.py [--dry-run]
"""
import json
import sys

ARQ = "control-plane/deliveries/W2-odoo-e-seguranca.json"
COMMIT = "9e638f7986fccc293d2acfb7f1e9bc0922839b82"
AMBIENTE = (
    "VPS Contabo vmi3619453: clone isolado /tmp/tre_lote13/repo do commit 9e638f7 "
    "(bundle do develop, 765 arquivos versionados); por card, banco e rede descartaveis "
    "proprios; `odoo_dev` com os mesmos bancos antes e depois; homolog/prod sem nenhum "
    "arquivo (ADR-005), conferido pelo proprio verifier"
)
BY = "Hermes — validacao integrada do lote 13 (cards W2 da fila, ordens 120..129)"
DATA = "2026-10-04"
CARDS = [
    {
        "id": "TRE-W2-E02-T01",
        "title": "Configurar CRM básico",
        "task": "t_adea8e6b",
        "evidence": (
            "2 passadas identicas: `RESULTADO: CRM_DEV_OK (29 itens, 0 falhas)` (exit 0) em ambas; "
            "unica diferenca entre os logs e' o carimbo de tempo da medicao. Dente: alvo mutado "
            "(TRE_ODOO_BANCO apontando para banco inexistente) -> `CRM_DEV_FALHOU (13 itens, 3 falhas)`, "
            "exit 1. Verifier read-only: le o estado vivo (psql dentro de pg-odoo-dev, containers, HTTP, "
            "UFW) e nao escreve nada no ambiente."
        ),
        "verification": {
            "verificador": "scripts/provision/verificar-crm-dev.sh",
            "natureza": "mede o ESTADO VIVO do odoo_dev (nao escreve)",
            "passadas": 2,
            "dente": "mutacao do alvo -> CRM_DEV_FALHOU (13 itens, 3 falhas), exit 1",
            "log": "/tmp/tre_lote13/card123_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E04-T01",
        "title": "Customizar res.partner",
        "task": "t_adee6ad7",
        "evidence": (
            "2 passadas identicas: `RESULTADO: RES_PARTNER_OK (64 itens, 0 falhas)` (exit 0) em ambas; "
            "diferem so' nomes efemeros de container e diretorio temporario. Prova de dente do proprio "
            "verifier (`--prova-de-dente`): baseline NAO mutado mede verde + 3 mutacoes, cada uma em copia "
            "propria, todas reprovando -> `RESULTADO: RES_PARTNER_DENTE_OK (3 provas, 0 falhas)`. Aceite de "
            "5 passos: banco limpo `tre_e04_t01_res_partner`, testes do Odoo, catalogo do PostgreSQL (AC1), "
            "criacao/consulta de parceiro sintetico pelo ORM (AC3), desinstalacao (rollback declarado). "
            "Banco e rede descartaveis removidos ao fim."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-res-partner.sh",
            "natureza": "instalacao em banco limpo + testes + leitura no banco + ORM + rollback",
            "passadas": 2,
            "dente": "modo proprio do verifier -> RES_PARTNER_DENTE_OK (3 provas, 0 falhas)",
            "banco_descartavel": "tre_e04_t01_res_partner",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card124_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E06-T01",
        "title": "Criar views Sales AI",
        "task": "t_cf7519c9",
        "evidence": (
            "2 passadas identicas: `RESULTADO: VIEWS_OK (83 itens, 0 falhas)` (exit 0) em ambas — o mesmo "
            "numero de itens do aceite original documentado no registro de execucoes. Prova de dente "
            "(`--prova-de-dente`): baseline NAO mutado mede verde (`VIEWS_OK (83 itens, 0 falhas)` em "
            "`tre_e06t01_views_baseline`) + 2 mutacoes em copia propria, cada uma reprovando (uma delas "
            "derrubou 27 itens) -> `RESULTADO: VIEWS_DENTE_OK (2 provas, 0 falhas)`. Aceite de 6 passos: "
            "banco limpo `tre_e06t01_views`, testes do modulo, leitura no banco (views/menus/acao/arch), "
            "prova independente (`scripts/odoo/provar_views_sales_ai.py`, 21 itens), rollback por "
            "desinstalacao pelo ORM, limpeza com dev/homolog/prod intactos."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-views-sales-ai.sh",
            "prova_independente": "scripts/odoo/provar_views_sales_ai.py (21 itens, sem traceback)",
            "passadas": 2,
            "dente": "modo proprio -> VIEWS_DENTE_OK (2 provas, 0 falhas)",
            "banco_descartavel": "tre_e06t01_views",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card126_{pass1,pass2,dente}.out",
        },
    },
    {
        "id": "TRE-W2-E04-T02",
        "title": "Customizar crm.lead",
        "task": "t_d3bd6660",
        "evidence": (
            "2 passadas identicas: `RESULTADO: CRM_LEAD_OK (64 itens, 0 falhas)` (exit 0) em ambas. Prova "
            "de dente (`--prova-de-dente`): baseline NAO mutado mede verde (`CRM_LEAD_OK (43 itens, 0 "
            "falhas)` em `tre_e04_t02_crm_lead_baseline`) + 2 mutacoes em copia propria, ambas reprovando "
            "(16 falhas e 1 falha) -> conjunto `CRM_LEAD_DENTE_OK`. Aceite de 6 passos, incluindo o "
            "confronto modulo x contrato congelado (`docs/data/data_contract_v1.json`) como passo 0 e "
            "rollback por desinstalacao. Banco descartavel `tre_e04_t02_crm_lead`."
        ),
        "verification": {
            "verificador": "scripts/odoo/verificar-crm-lead-odoo.sh",
            "contrato": "docs/data/data_contract_v1.json (confronto como passo 0/6)",
            "passadas": 2,
            "dente": "modo proprio -> baseline verde + 2 mutacoes reprovando (16 e 1 falhas)",
            "banco_descartavel": "tre_e04_t02_crm_lead",
            "imagens": "odoo:19.0 + postgres:16",
            "log": "/tmp/tre_lote13/card127_{pass1,pass2,dente}.out",
        },
    },
]


def main() -> int:
    dry = "--dry-run" in sys.argv
    with open(ARQ, encoding="utf-8") as fh:
        j = json.load(fh)

    existentes = {
        c["hermes_task_id"]: w
        for w in j["work_items"]
        for c in (w.get("children") or [])
    }
    novos, ja = [], []
    for card in CARDS:
        if card["task"] in existentes:
            ja.append(card["task"])
            continue
        j["work_items"].append(
            {
                "id": card["id"],
                "title": card["title"],
                "stage": "DONE",
                "current_gate": "VALIDATION",
                "children": [
                    {
                        "hermes_task_id": card["task"],
                        "stage": "DONE",
                        "current_gate": "VALIDATION",
                        "validation_result": "PASS",
                        "evidence": card["evidence"],
                        "validated_at": DATA,
                        "validated_by": BY,
                        "verification": dict(
                            card["verification"], commit=COMMIT, ambiente=AMBIENTE
                        ),
                    }
                ],
            }
        )
        novos.append(card["task"])

    if novos:
        j["updated_at"] = DATA
        j["events"].append(
            {
                "event": "INTEGRATED_VALIDATION_RECORDED",
                "by": BY,
                "scope": "lote 13 — cards W2 da fila (ordens 120..129): 4 primeiros fechados com 2 passadas + dente",
                "production_promotion_authorized": False,
                "at": "2026-10-04T17:40:00+00:00",
                "cards": novos,
            }
        )

    print(f"novos: {len(novos)} {novos}")
    print(f"ja existiam: {ja}")
    print(f"work_items agora: {len(j['work_items'])} | prod_authorized: {j['production_promotion_authorized']}")
    if dry:
        print("(dry-run: nada gravado)")
        return 0
    with open(ARQ, "w", encoding="utf-8") as fh:
        json.dump(j, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("gravado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
