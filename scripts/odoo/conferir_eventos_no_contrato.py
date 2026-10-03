#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conferidor: os eventos Odoo -> PostgreSQL do modulo sao os do CONTRATO — card TRE-W3-E03-T01.

Le a lista declarada no modulo (`EVENTOS_ODOO_PARA_PG`, em
`odoo/addons/transformativa_sales_ai/models/tf_evento_outbox.py`), a lista do Data Contract V1.0
(`docs/data/data_contract_v1.json` -> `events.odoo_to_pg`) e a lista do contrato da porta
(`n8n/contracts/odoo-events-ingest.v1.json` -> `eventos`) e exige que as TRES sejam iguais.

Por que um conferidor separado: a suite do Odoo prova isso DENTRO do Odoo, onde o modulo esta'
instalado; este aqui roda em qualquer lugar (sem Odoo, sem container) e serve de guarda barata no
pre-commit/aceite — e e' citado no docstring do proprio modelo.

Uso: python3 scripts/odoo/conferir_eventos_no_contrato.py
Saida: OK/FALHOU por item + RESULTADO; exit 0 = tudo OK, 1 = divergencia.
"""
import ast
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
MODELO = RAIZ / "odoo" / "addons" / "transformativa_sales_ai" / "models" / "tf_evento_outbox.py"
CONTRATO_DE_DADOS = RAIZ / "docs" / "data" / "data_contract_v1.json"
CONTRATO_DA_PORTA = RAIZ / "n8n" / "contracts" / "odoo-events-ingest.v1.json"

ITENS = []
FALHAS = []


def confere(nome, condicao, detalhe=""):
    ITENS.append(condicao)
    if condicao:
        print("OK    %s" % nome)
    else:
        FALHAS.append(nome)
        print("FALHOU %s%s" % (nome, ("  [%s]" % detalhe) if detalhe else ""))


def eventos_do_modulo():
    arvore = ast.parse(MODELO.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign) and any(
            isinstance(alvo, ast.Name) and alvo.id == "EVENTOS_ODOO_PARA_PG" for alvo in no.targets
        ):
            return list(ast.literal_eval(no.value))
    return []


def main():
    modulo = eventos_do_modulo()
    baseline = list(json.loads(CONTRATO_DE_DADOS.read_text(encoding="utf-8"))["events"]["odoo_to_pg"])
    porta = [evento["event_type"] for evento in
             json.loads(CONTRATO_DA_PORTA.read_text(encoding="utf-8"))["eventos"]]

    confere("o modulo declara a lista de eventos (nao inventa por prosa)", bool(modulo), str(modulo))
    confere("a lista do modulo == a lista do Data Contract V1.0", sorted(modulo) == sorted(baseline),
            "modulo=%s contrato=%s" % (sorted(modulo), sorted(baseline)))
    confere("a lista da porta == a lista do Data Contract V1.0", sorted(porta) == sorted(baseline),
            "porta=%s contrato=%s" % (sorted(porta), sorted(baseline)))
    confere("a versao do envelope declarada e a do contrato",
            "VERSAO_DO_EVENTO = '1.0'" in MODELO.read_text(encoding="utf-8"))
    confere("nenhum evento inventado (nem LEAD_CREATED)",
            "LEAD_CREATED" not in MODELO.read_text(encoding="utf-8"))

    if FALHAS:
        print("RESULTADO: EVENTOS_NO_CONTRATO_FALHOU (%s itens, %s falha(s))" % (len(ITENS), len(FALHAS)))
        return 1
    print("RESULTADO: EVENTOS_NO_CONTRATO_OK (%s itens, 0 falhas)" % len(ITENS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
