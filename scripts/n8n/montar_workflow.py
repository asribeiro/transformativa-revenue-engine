#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monta o workflow do consumidor de outbox a partir dos artefatos versionados.

Card TRE-W3-E02-T01 (board transformativa-revenue-engine). O workflow versionado
(`n8n/workflows/TRE-outbox-consumer.json`) NAO e' escrito a mao: ele e' o resultado
deste montador sobre tres artefatos que tem um dono cada um:

  * `n8n/contracts/outbox-consumer.v1.json` — a declaracao (eventos, mapeamento, teto
    de tentativas, status, classificacao HTTP, credenciais por id/nome);
  * `n8n/codigo/nucleo-outbox-consumer.js` — a decisao pura (roda no n8n e no node);
  * `n8n/sql/ler-pendentes.sql` e `n8n/sql/registrar-resultado.sql` — a fila e o estado final.

Assim "o workflow embute o nucleo/o SQL/o contrato" deixa de ser afirmacao de leitura: o
texto do Code node e' o arquivo + um adaptador marcado, e o verificador
(`scripts/n8n/verificar-outbox-consumer.sh`) reprova quando qualquer um dos dois lados anda
sozinho. `--conferir` faz essa comparacao sem escrever nada.

Uso:
    python3 scripts/n8n/montar_workflow.py                 # escreve o workflow
    python3 scripts/n8n/montar_workflow.py --saida /tmp/x.json
    python3 scripts/n8n/montar_workflow.py --conferir       # compara com o que esta em disco
"""
import argparse
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ARQ_CONTRATO = RAIZ / "n8n" / "contracts" / "outbox-consumer.v1.json"
ARQ_NUCLEO = RAIZ / "n8n" / "codigo" / "nucleo-outbox-consumer.js"
ARQ_SQL_LER = RAIZ / "n8n" / "sql" / "ler-pendentes.sql"
ARQ_SQL_REGISTRAR = RAIZ / "n8n" / "sql" / "registrar-resultado.sql"
ARQ_WORKFLOW = RAIZ / "n8n" / "workflows" / "TRE-outbox-consumer.json"

# O adaptador e' a FRONTEIRA declarada: o que esta antes e' o arquivo versionado, o que esta
# depois e' a cola do n8n. O verificador corta exatamente neste marcador.
MARCADOR = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"

ID_DO_WORKFLOW = "TREOUTBOXCONSUM1"
NOME_DO_WORKFLOW = "TRE — outbox consumer (PostgreSQL → Odoo)"

NOTAS = {
    "nucleo": ("Code node: roda o NUCLEO VERSIONADO (n8n/codigo/nucleo-outbox-consumer.js) sobre os "
               "eventos lidos e devolve a decisao por evento (ENVIAR | RECUSAR | ESGOTADO | IGNORAR). "
               "Sem contrato ele nao decide nada: o contrato vai embutido abaixo."),
    "classificar": ("Code node: roda o MESMO nucleo versionado para virar a RESPOSTA da API em estado "
                    "final do evento (PROCESSED | RETRY | DEAD_LETTER), conferindo o correlation_id "
                    "ecoado contra a decisao que originou a chamada."),
}


def ler(caminho):
    return caminho.read_text(encoding="utf-8")


def carregar_contrato_texto():
    """Devolve o contrato como JSON normalizado (o mesmo texto vai para os Code nodes)."""
    contrato = json.loads(ler(ARQ_CONTRATO))
    return contrato, json.dumps(contrato, ensure_ascii=False, indent=2)


def js_code(adaptador, contrato_texto):
    return "\n".join([
        ler(ARQ_NUCLEO).rstrip(),
        "",
        MARCADOR,
        "const CONTRATO = %s;" % contrato_texto,
        "",
        adaptador,
        "",
    ])


def no_de_pg(ident, nome, sql, contrato, posicao, nota):
    return {
        "parameters": {
            "operation": "executeQuery",
            "query": sql.rstrip() + "\n",
            # A lista (e nao a string separada por virgula) e' o que o node Postgres 2.5+ aceita como
            # valores de $1..$n: com virgula, um motivo com virgula no meio deslocaria os parametros.
            "options": {"queryReplacement": "={{ [" + ", ".join(
                "$json." + campo for campo in CAMPOS_DO_SQL_REGISTRAR) + "] }}"},
        },
        "id": ident,
        "name": nome,
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": posicao,
        "credentials": {"postgres": {"id": contrato["credenciais"]["postgres"]["id"],
                                     "name": contrato["credenciais"]["postgres"]["nome"]}},
        "notes": nota,
        "notesInFlow": False,
    }


# Ordem dos parametros de n8n/sql/registrar-resultado.sql ($1..$12). Mudar aqui sem mudar o
# arquivo (ou o contrario) e' defeito: o verificador confere a contagem.
CAMPOS_DO_SQL_REGISTRAR = (
    "status_final",
    "incrementa_tentativas",
    "last_error",
    "evento_id",
    "aggregate_type",
    "aggregate_id",
    "event_version",
    "chave",
    "status_trilha",
    "request_payload",
    "response_payload",
    "last_error",
)


def montar():
    contrato, contrato_texto = carregar_contrato_texto()
    sql_ler = ler(ARQ_SQL_LER)
    sql_registrar = ler(ARQ_SQL_REGISTRAR)

    nucleo = js_code("return decisaoDoLote($input.all(), CONTRATO);", contrato_texto)
    classificar = js_code("return resultadoDasRespostas($input.all(), $('Nucleo: validar e decidir').all(), CONTRATO);",
                          contrato_texto)

    nos = [
        {
            "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}},
            "id": "tre-outbox-agenda",
            "name": "Entrada por agenda (poll)",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.2,
            "position": [-620, -60],
            "notes": ("Gatilho de producao do consumidor: varre a fila do outbox a cada minuto. "
                      "Card TRE-W3-E02-T01."),
        },
        {
            "parameters": {},
            "id": "tre-outbox-sob-demanda",
            "name": "Entrada sob demanda",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [-620, 140],
            "notes": ("Mesma cadeia, disparada a mao: e' o caminho que o aceite e o operador usam "
                      "(`n8n execute --id=<id>` executa o workflow a partir deste gatilho)."),
        },
        {
            "parameters": {"operation": "executeQuery", "query": sql_ler.rstrip() + "\n", "options": {}},
            "id": "tre-outbox-ler-pendentes",
            "name": "Ler pendentes (outbox)",
            "type": "n8n-nodes-base.postgres",
            "typeVersion": 2.7,
            "position": [-380, 40],
            "credentials": {"postgres": {"id": contrato["credenciais"]["postgres"]["id"],
                                         "name": contrato["credenciais"]["postgres"]["nome"]}},
            "notes": ("Le a fila (n8n/sql/ler-pendentes.sql). Somente leitura: quem muda o estado do "
                      "evento e' o no de registro."),
        },
        {
            "parameters": {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": nucleo},
            "id": "tre-outbox-nucleo",
            "name": "Nucleo: validar e decidir",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [-140, 40],
            "notes": NOTAS["nucleo"],
        },
        {
            "parameters": {"conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                "conditions": [{
                    "id": "tre-outbox-cond-entrega",
                    "leftValue": "={{ $json.decisao }}",
                    "rightValue": "ENVIAR",
                    "operator": {"type": "string", "operation": "equals"},
                }],
                "combinator": "and",
            }},
            "id": "tre-outbox-decisao",
            "name": "Decisao: entregar?",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.3,
            "position": [100, 40],
            "notes": "So' quem decidiu ENVIAR chama a API; recusa e esgotado vao para a trilha sem chamada.",
        },
        {
            "parameters": {
                "method": "POST",
                "url": "={{ $env.TRE_API_BASE }}/tf/api/v1/{{ $json.operacao }}",
                "authentication": "genericCredentialType",
                "genericAuthType": "httpHeaderAuth",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json.pedido) }}",
                "options": {
                    "timeout": 15000,
                    # UMA entrega por vez, em ordem (declarado no contrato, secao destino):
                    # sem isto o no dispara as chamadas em PARALELO e dois eventos da MESMA
                    # identidade podem criar dois parceiros — o aceite mediu exatamente isso
                    # (a busca de identidade da API nao ve o registro ainda nao commitado do
                    # pedido vizinho). Ordem do lote = ordem da fila (created_at, id).
                    "batching": {"batch": {"batchSize": contrato["destino"]["entrega_serializada"]["batch_size"],
                                           "batchInterval": contrato["destino"]["entrega_serializada"]["intervalo_ms"]}},
                    "response": {"response": {"fullResponse": True, "neverError": True}},
                },
            },
            "id": "tre-outbox-chamar-api",
            "name": "Chamar API controlada (porta unica)",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.5,
            "position": [340, -80],
            "credentials": {"httpHeaderAuth": {"id": contrato["credenciais"]["api"]["id"],
                                               "name": contrato["credenciais"]["api"]["nome"]}},
            "onError": "continueRegularOutput",
            "notes": ("POST /tf/api/v1/<operacao> com a credencial do cofre do n8n (Bearer). "
                      "neverError + continueRegularOutput: recusa e falha de transporte viram DADO "
                      "classificado pelo nucleo, nao excecao que perde a trilha."),
        },
        {
            "parameters": {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": classificar},
            "id": "tre-outbox-classificar",
            "name": "Classificar resposta",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [580, -80],
            "notes": NOTAS["classificar"],
        },
        no_de_pg("tre-outbox-registrar-entrega", "Registrar entrega (outbox + trilha)", sql_registrar, contrato,
                 [820, -80],
                 "UPDATE do outbox + linha em sync_events numa transacao (n8n/sql/registrar-resultado.sql)."),
        no_de_pg("tre-outbox-registrar-recusa", "Registrar recusa (outbox + trilha)", sql_registrar, contrato,
                 [340, 200],
                 ("Mesmo SQL do caminho de entrega, com os parametros da recusa/esgotado: recusa nao "
                  "incrementa tentativas e deixa o motivo no last_error.")),
    ]

    conexoes = {
        "Entrada por agenda (poll)": {"main": [[{"node": "Ler pendentes (outbox)", "type": "main", "index": 0}]]},
        "Entrada sob demanda": {"main": [[{"node": "Ler pendentes (outbox)", "type": "main", "index": 0}]]},
        "Ler pendentes (outbox)": {"main": [[{"node": "Nucleo: validar e decidir", "type": "main", "index": 0}]]},
        "Nucleo: validar e decidir": {"main": [[{"node": "Decisao: entregar?", "type": "main", "index": 0}]]},
        "Decisao: entregar?": {"main": [
            [{"node": "Chamar API controlada (porta unica)", "type": "main", "index": 0}],
            [{"node": "Registrar recusa (outbox + trilha)", "type": "main", "index": 0}],
        ]},
        "Chamar API controlada (porta unica)": {"main": [[{"node": "Classificar resposta", "type": "main", "index": 0}]]},
        "Classificar resposta": {"main": [[{"node": "Registrar entrega (outbox + trilha)", "type": "main", "index": 0}]]},
    }

    return {
        "id": ID_DO_WORKFLOW,
        "name": NOME_DO_WORKFLOW,
        "active": False,
        "nodes": nos,
        "connections": conexoes,
        "settings": {"executionOrder": "v1"},
        "staticData": None,
        "meta": {"card": "TRE-W3-E02-T01", "contrato": "n8n/contracts/outbox-consumer.v1.json",
                 "montado_por": "scripts/n8n/montar_workflow.py"},
        "tags": [],
    }


def serializar(workflow):
    return json.dumps(workflow, ensure_ascii=False, indent=2) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Monta/conferi o workflow do consumidor de outbox.")
    ap.add_argument("--saida", default=str(ARQ_WORKFLOW), help="onde escrever (padrao: o workflow versionado)")
    ap.add_argument("--conferir", action="store_true",
                    help="nao escreve: compara o montado agora com o arquivo em disco")
    args = ap.parse_args()

    texto = serializar(montar())
    if args.conferir:
        atual = pathlib.Path(args.saida)
        if not atual.exists():
            print("FALHOU workflow ausente: %s" % atual)
            return 1
        if atual.read_text(encoding="utf-8") != texto:
            print("FALHOU o workflow em %s nao corresponde aos artefatos versionados" % atual)
            print("       (rode: python3 scripts/n8n/montar_workflow.py)")
            return 1
        print("OK    workflow corresponde aos artefatos versionados (%s)" % atual)
        return 0

    destino = pathlib.Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    print("OK    workflow escrito em %s (%d bytes)" % (destino, len(texto.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
