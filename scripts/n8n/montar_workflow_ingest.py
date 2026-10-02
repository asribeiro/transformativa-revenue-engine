#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monta o workflow da PORTA de ingestao Odoo -> PostgreSQL — card TRE-W3-E03-T01.

O WORKFLOW E' ARTEFATO DERIVADO: ele nao se edita a mao. Este montador le os quatro artefatos
versionados e escreve (ou confere) o JSON do workflow:

    n8n/contracts/odoo-events-ingest.v1.json   o contrato (parametro do nucleo e das rotas)
    n8n/codigo/nucleo-ingest-eventos.js        o nucleo puro, EMBUTIDO no Code node
    n8n/sql/ingerir-evento.sql                 o SQL do ramo ACEITO
    n8n/sql/registrar-recusa.sql               o SQL do ramo RECUSADO

O adaptador do Code node (o trecho que le `$input` e chama `decidir`) vive AQUI e e' separado do
nucleo por um marcador: e' o que a lente estrutural usa para provar que o codigo sob teste e' o
nucleo versionado, sem confundir com o que so' existe dentro do n8n.

Uso:
    python3 scripts/n8n/montar_workflow_ingest.py --saida <arquivo>
    python3 scripts/n8n/montar_workflow_ingest.py --conferir      # compara com o versionado
Saida: 0 = ok (montado/igual) · 1 = divergencia (--conferir) · 2 = uso errado.
"""
import argparse
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CONTRATO = RAIZ / "n8n" / "contracts" / "odoo-events-ingest.v1.json"
NUCLEO = RAIZ / "n8n" / "codigo" / "nucleo-ingest-eventos.js"
SQL_ACEITE = RAIZ / "n8n" / "sql" / "ingerir-evento.sql"
SQL_RECUSA = RAIZ / "n8n" / "sql" / "registrar-recusa.sql"
SAIDA = RAIZ / "n8n" / "workflows" / "TRE-odoo-events-ingest.json"

ID_WORKFLOW = "TREodooEventos1"
WEBHOOK_ID = "tre-odoo-eventos"
MARCADOR = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"

NO_WEBHOOK = "Webhook: evento do Odoo"
NO_NUCLEO = "Nucleo: decidir ingestao"
NO_DECISAO = "Decisao: aceita?"
NO_INGERIR = "Postgres: ingerir evento (trilha)"
NO_RECUSA = "Postgres: registrar recusa (trilha)"
NO_RESPOSTA = "Resposta da porta"


def ler(caminho):
    return caminho.read_text(encoding="utf-8")


def adaptador(contrato):
    """O trecho que so' existe dentro do n8n: le o corpo do webhook e chama o nucleo puro."""
    corpo_do_contrato = json.dumps(contrato, ensure_ascii=False, indent=4, sort_keys=True)
    return "\n".join([
        "",
        MARCADOR,
        "var CONTRATO_INGEST = " + corpo_do_contrato + ";",
        "var __item = $input.first().json;",
        "var __corpo = (__item && __item.body !== undefined) ? __item.body : __item;",
        "var __decisao = decidir(__corpo, typeof __corpo === 'string' ? __corpo"
        " : JSON.stringify(__corpo), CONTRATO_INGEST);",
        "return [{ json: __decisao }];",
        "",
    ])


def montar():
    contrato = json.loads(ler(CONTRATO))
    nucleo = ler(NUCLEO)
    sql_aceite = ler(SQL_ACEITE)
    sql_recusa = ler(SQL_RECUSA)
    porta = contrato["porta"]
    credencial_pg = contrato["credenciais"]["postgres"]
    credencial_token = contrato["credenciais"]["token_da_porta"]
    caminho_webhook = porta["rota"].replace("/webhook/", "", 1)

    nos = [
        {
            "parameters": {
                "httpMethod": porta["metodo"],
                "path": caminho_webhook,
                "responseMode": "responseNode",
                "authentication": "headerAuth",
                "options": {},
            },
            "id": "tre-odoo-eventos-webhook",
            "name": NO_WEBHOOK,
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [-260, 300],
            "webhookId": WEBHOOK_ID,
            "credentials": {
                "httpHeaderAuth": {
                    "id": credencial_token["id"],
                    "name": credencial_token["nome"],
                }
            },
        },
        {
            "parameters": {
                "mode": "runOnceForAllItems",
                "language": "javaScript",
                "jsCode": nucleo + adaptador(contrato),
            },
            "id": "tre-odoo-eventos-nucleo",
            "name": NO_NUCLEO,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [-40, 300],
        },
        {
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "loose",
                        "version": 2,
                    },
                    "conditions": [
                        {
                            "id": "tre-ingest-aceita",
                            "leftValue": "={{ $json.aceito }}",
                            "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "id": "tre-odoo-eventos-decisao",
            "name": NO_DECISAO,
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.3,
            "position": [180, 300],
        },
        {
            "parameters": {
                "operation": "executeQuery",
                "query": sql_aceite,
                "options": {"queryReplacement": "={{ $json.parametros }}"},
            },
            "id": "tre-odoo-eventos-ingerir",
            "name": NO_INGERIR,
            "type": "n8n-nodes-base.postgres",
            "typeVersion": 2.7,
            "position": [420, 180],
            "credentials": {
                "postgres": {"id": credencial_pg["id"], "name": credencial_pg["nome"]}
            },
        },
        {
            "parameters": {
                "operation": "executeQuery",
                "query": sql_recusa,
                "options": {"queryReplacement": "={{ $json.parametros }}"},
            },
            "id": "tre-odoo-eventos-recusa",
            "name": NO_RECUSA,
            "type": "n8n-nodes-base.postgres",
            "typeVersion": 2.7,
            "position": [420, 420],
            "credentials": {
                "postgres": {"id": credencial_pg["id"], "name": credencial_pg["nome"]}
            },
        },
        {
            "parameters": {
                "respondWith": "json",
                "responseBody": "={{ $json.corpo }}",
                "options": {"responseCode": "={{ $json.codigo_http }}"},
            },
            "id": "tre-odoo-eventos-resposta",
            "name": NO_RESPOSTA,
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1.1,
            "position": [680, 300],
        },
    ]
    conexoes = {
        NO_WEBHOOK: {"main": [[{"node": NO_NUCLEO, "type": "main", "index": 0}]]},
        NO_NUCLEO: {"main": [[{"node": NO_DECISAO, "type": "main", "index": 0}]]},
        NO_DECISAO: {
            "main": [
                [{"node": NO_INGERIR, "type": "main", "index": 0}],
                [{"node": NO_RECUSA, "type": "main", "index": 0}],
            ]
        },
        NO_INGERIR: {"main": [[{"node": NO_RESPOSTA, "type": "main", "index": 0}]]},
        NO_RECUSA: {"main": [[{"node": NO_RESPOSTA, "type": "main", "index": 0}]]},
    }
    return {
        "id": ID_WORKFLOW,
        "name": "TRE odoo-events-ingest",
        "active": False,
        "settings": {"executionOrder": "v1"},
        "nodes": nos,
        "connections": conexoes,
        "pinData": {},
    }


def serializar(workflow):
    return json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Monta o workflow do ingestor de eventos Odoo.")
    ap.add_argument("--saida", default=None, help="arquivo de saida (default: o versionado)")
    ap.add_argument("--conferir", action="store_true",
                    help="nao escreve: compara com o arquivo versionado")
    args = ap.parse_args()

    texto = serializar(montar())
    if args.conferir:
        alvo = pathlib.Path(args.saida) if args.saida else SAIDA
        if not alvo.exists():
            print("FALHOU workflow ausente: %s" % alvo)
            return 1
        atual = alvo.read_text(encoding="utf-8")
        if atual == texto:
            print("OK    workflow versionado e' o montado a partir dos artefatos (%s)" % alvo.name)
            return 0
        print("FALHOU workflow divergente do montado — remonte com --saida")
        return 1
    alvo = pathlib.Path(args.saida) if args.saida else SAIDA
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(texto, encoding="utf-8")
    print("OK    workflow montado em %s (%s nos)" % (alvo, len(montar()["nodes"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
