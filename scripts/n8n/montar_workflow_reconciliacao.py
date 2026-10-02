#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monta o workflow da RECONCILIACAO a partir dos artefatos versionados.

Card TRE-W3-E04-T01; board transformativa-revenue-engine. O workflow versionado
(`n8n/workflows/TRE-reconciliation.json`) NAO e' escrito a mao: ele e' o
resultado deste montador sobre artefatos que tem um dono cada um:

  * `n8n/contracts/reconciliation-job.v1.json` — a declaracao (comparacoes, lote,
    leituras do destino, janela, vocabulario, regras de fail-closed, credenciais
    por id/nome, grafo e saida);
  * `n8n/codigo/nucleo-reconciliacao.js` — a decisao pura (roda no n8n e no node);
  * `n8n/sql/reconciliacao-origem.sql` — o lado PostgreSQL (entidades do lote);
  * `n8n/sql/reconciliacao-pendentes.sql` — o lado PostgreSQL (fila x trilha).

Assim "o workflow embute o nucleo/o SQL/o contrato" deixa de ser afirmacao de
leitura: o texto do Code node e' o arquivo + um adaptador marcado, e a lente
estrutural (`scripts/n8n/conferir_reconciliacao.py`) reprova quando qualquer um
dos dois lados anda sozinho. `--conferir` faz essa comparacao sem escrever nada.

Uso:
    python3 scripts/n8n/montar_workflow_reconciliacao.py            # escreve o workflow
    python3 scripts/n8n/montar_workflow_reconciliacao.py --saida /tmp/x.json
    python3 scripts/n8n/montar_workflow_reconciliacao.py --conferir  # compara com o que esta em disco
"""
import argparse
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ARQ_CONTRATO = RAIZ / "n8n" / "contracts" / "reconciliation-job.v1.json"
ARQ_NUCLEO = RAIZ / "n8n" / "codigo" / "nucleo-reconciliacao.js"
ARQ_SQL_ORIGEM = RAIZ / "n8n" / "sql" / "reconciliacao-origem.sql"
ARQ_SQL_PENDENTES = RAIZ / "n8n" / "sql" / "reconciliacao-pendentes.sql"
ARQ_WORKFLOW = RAIZ / "n8n" / "workflows" / "TRE-reconciliation.json"

# O adaptador e' a FRONTEIRA declarada: o que esta antes e' o arquivo versionado, o que
# esta depois e' a cola do n8n. A lente corta exatamente neste marcador.
MARCADOR = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"

NOTAS = {
    "agenda": ("Gatilho declarado (cadencia do contrato, doc 06 §8 'job diario'). Quem ATIVA ajusta a hora "
               "ao plantao real: o card nao escolhe a janela de operacao."),
    "sob_demanda": ("Mesma cadeia, disparada a mao: e' o caminho que o aceite e o operador usam "
                    "(`n8n execute --id=<id>` executa o workflow a partir deste gatilho)."),
    "origem": ("Primeira consulta (n8n/sql/reconciliacao-origem.sql): o LOTE de organizacoes do lado "
               "PostgreSQL, com a ponta do vinculo, os identificadores fortes e a linha de COBERTURA "
               "— e' a cobertura que distingue base vazia (informacao) de consulta quebrada. "
               "SOMENTE LEITURA: o job mede e nomeia; nao corrige (doc 06 §8)."),
    "preparar_leitura": ("Code node: roda o NUCLEO VERSIONADO (n8n/codigo/nucleo-reconciliacao.js) para "
                         "montar os pedidos de leitura da PORTA UNICA a partir do lote. Sem entidade o "
                         "filtro vai com lista vazia (`in []`) — o pedido NAO vira leitura da base "
                         "inteira do CRM."),
    "destino": ("Leitura do lado Odoo PELA PORTA UNICA (`POST /tf/api/v1/crm_registros_ler`, operacao de "
                "leitura declarada na politica da API): nenhum caminho paralelo, nenhum SQL no Odoo. "
                "`fullResponse`+`neverError`: a resposta crua vem para o nucleo, que decide se ela e' "
                "MEDICAO — recusa ou corpo sem `dados.registros` NAO viram 'sem divergencia'."),
    "pendentes": ("Segunda consulta (n8n/sql/reconciliacao-pendentes.sql): a FILA (status de entrada) com "
                  "a idade e as tentativas, cruzada com a TRILHA da chave derivada — e a linha de "
                  "cobertura da fila. SOMENTE LEITURA."),
    "avaliar": ("Code node: roda o NUCLEO VERSIONADO sobre as quatro fontes (origem, fila e as duas "
                "leituras da porta unica) e devolve veredito, cobertura, divergencias NOMEADAS, "
                "observacoes e o RELATORIO. Limiar, janela e vocabulario vem do contrato embutido: sem "
                "contrato o nucleo nao decide nada (INDETERMINADO)."),
    "relatorio": ("Saida do workflow: o resultado desta execucao E o relatorio que o operador le. O job "
                  "NAO escreve em tabela nenhuma (o contrato declara a lacuna de persistencia): o destino "
                  "do relatorio/do alerta e' decisao do dono."),
}


def ler(caminho):
    return caminho.read_text(encoding="utf-8")


def carregar_contrato_texto():
    """Devolve o contrato como JSON normalizado (o mesmo texto vai para o Code node)."""
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
        "parameters": {"operation": "executeQuery", "query": sql.rstrip() + "\n", "options": {}},
        "id": ident,
        "name": nome,
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": posicao,
        # `executeOnce`: a consulta e' a MEDICAO da rodada, nao um passo por item de
        # entrada (a origem roda ANTES das leituras; sem isto ela se multiplicaria a
        # cada item que chegasse).
        "executeOnce": True,
        "credentials": {"postgres": {"id": contrato["credenciais"]["postgres"]["id"],
                                     "name": contrato["credenciais"]["postgres"]["nome"]}},
        "notes": nota,
        "notesInFlow": False,
    }


def no_de_leitura(contrato, leitura, posicao, referencia):
    """No HTTP da leitura declarada: URL pela base de ambiente, corpo pelo pedido do
    nucleo.
    `referencia` e' a expressao do corpo: `$json.pedidos.<id>` quando o no anterior e' o
    preparador, `$('<no do preparador>').first().json.pedidos.<id>` quando a cadeia ja'
    passou por outro no' (o segundo pedido NAO pode depender do item corrente, que e' a
    resposta do primeiro — depender dele seria ler o estado errado).
    O corpo vai dentro do involucro que o CONTRATO declara (`fontes.destino.envelope_da_requisicao`):
    a porta unica recusa pedido sem ele (400 payload_invalido) — erro de forma que o job
    leria como INDETERMINADO, nao como divergencia real."""
    envelope = contrato["fontes"]["destino"]["envelope_da_requisicao"]
    chave = envelope["chave_dos_parametros"]
    # `onError` do no' vem do CONTRATO (`no_que_nao_mede`): falha de conexao nao pode abortar a
    # execucao — ela tem de virar item que o nucleo le como NAO MEDIDA.
    on_error = envelope["no_que_nao_mede"]["onError"]
    return {
        "parameters": {
            "method": "POST",
            "url": "={{ $env.TRE_API_BASE }}/tf/api/v1/" + contrato["fontes"]["destino"]["operacao_de_leitura"],
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({" + chave + ": " + referencia + "}) }}",
            "options": {
                "timeout": 15000,
                "response": {"response": {"fullResponse": True, "neverError": True}},
            },
        },
        "onError": on_error,
        "id": "tre-reconc-" + leitura["id"].replace("_", "-"),
        "name": leitura["no"],
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.5,
        "position": posicao,
        "credentials": {"httpHeaderAuth": {"id": contrato["credenciais"]["api"]["id"],
                                           "name": contrato["credenciais"]["api"]["nome"]}},
        "notes": NOTAS["destino"],
        "notesInFlow": False,
    }


def montar():
    contrato, contrato_texto = carregar_contrato_texto()
    sql_origem = ler(ARQ_SQL_ORIGEM)
    sql_pendentes = ler(ARQ_SQL_PENDENTES)
    nos = contrato["workflow"]["nos"]
    gatilho = contrato["workflow"]["gatilho"]
    if gatilho["unidade"] != "hours":
        raise SystemExit("gatilho declarado com unidade nao montada: %s" % gatilho["unidade"])
    leituras = contrato["leituras_do_destino"]
    if len(leituras) != 2:
        raise SystemExit("o montador monta DUAS leituras do destino (medidas: %d)" % len(leituras))

    preparar = js_code(
        "const itens = $input.all().map(function (item) { return item.json; });\n"
        "return adaptadorPrepararLeitura(CONTRATO, itens);",
        contrato_texto)

    linhas_do_adaptador = [
        "const origem = $('%s').all().map(function (item) { return item.json; });" % nos["origem"],
        "const pendentes = $('%s').all().map(function (item) { return item.json; });" % nos["pendentes"],
        "const leituras = {};",
    ]
    # Cada leitura e' referenciada pelo NOME DO NO declarado no contrato — literal no
    # adaptador (o Code node resolve `$('nome')` em tempo de execucao), montado aqui para
    # os dois nao andarem separados.
    for leitura in leituras:
        linhas_do_adaptador.append(
            "leituras['%s'] = $('%s').all().map(function (item) { return item.json; });"
            % (leitura["id"], leitura["no"]))
    linhas_do_adaptador.append(
        "return adaptadorAvaliar(CONTRATO, origem, pendentes, leituras, new Date().toISOString());")

    avaliar = js_code("\n".join(linhas_do_adaptador), contrato_texto)

    nos_do_workflow = [
        {
            "parameters": {"rule": {"interval": [{"field": "hours",
                                                  "hoursInterval": gatilho["intervalo"]}]}},
            "id": "tre-reconc-agenda",
            "name": nos["agenda"],
            "type": gatilho["tipo"],
            "typeVersion": 1.2,
            "position": [-560, -80],
            "notes": NOTAS["agenda"],
        },
        {
            "parameters": {},
            "id": "tre-reconc-sob-demanda",
            "name": nos["sob_demanda"],
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [-560, 120],
            "notes": NOTAS["sob_demanda"],
        },
        no_de_pg("tre-reconc-origem", nos["origem"], sql_origem, contrato, [-320, 40], NOTAS["origem"]),
        {
            "parameters": {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": preparar},
            "id": "tre-reconc-preparar",
            "name": nos["preparar_leitura"],
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [-80, 40],
            "notes": NOTAS["preparar_leitura"],
        },
        no_de_leitura(contrato, leituras[0], [160, 40], "$json.pedidos." + leituras[0]["id"]),
        no_de_leitura(contrato, leituras[1], [400, 40],
                      "$('" + nos["preparar_leitura"] + "').first().json.pedidos." + leituras[1]["id"]),
        no_de_pg("tre-reconc-pendentes", nos["pendentes"], sql_pendentes, contrato, [640, 40],
                 NOTAS["pendentes"]),
        {
            "parameters": {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": avaliar},
            "id": "tre-reconc-avaliar",
            "name": nos["avaliar"],
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [880, 40],
            "notes": NOTAS["avaliar"],
        },
        {
            "parameters": {},
            "id": "tre-reconc-relatorio",
            "name": nos["relatorio"],
            "type": "n8n-nodes-base.noOp",
            "typeVersion": 1,
            "position": [1120, 40],
            "notes": NOTAS["relatorio"],
        },
    ]

    def aresta(*destinos):
        return {"main": [[{"node": destino, "type": "main", "index": 0} for destino in destinos]]}

    conexoes = {
        nos["agenda"]: aresta(nos["origem"]),
        nos["sob_demanda"]: aresta(nos["origem"]),
        nos["origem"]: aresta(nos["preparar_leitura"]),
        nos["preparar_leitura"]: aresta(leituras[0]["no"]),
        leituras[0]["no"]: aresta(leituras[1]["no"]),
        leituras[1]["no"]: aresta(nos["pendentes"]),
        nos["pendentes"]: aresta(nos["avaliar"]),
        nos["avaliar"]: aresta(nos["relatorio"]),
    }

    return {
        "id": contrato["workflow"]["id_estavel"],
        "name": contrato["workflow"]["nome"],
        "active": False,
        "nodes": nos_do_workflow,
        "connections": conexoes,
        "settings": {"executionOrder": "v1"},
        "staticData": None,
        "meta": {
            "card": "TRE-W3-E04-T01",
            "contrato": "n8n/contracts/reconciliation-job.v1.json",
            "montado_por": "scripts/n8n/montar_workflow_reconciliacao.py",
        },
        "tags": [],
    }


def texto_do_workflow(workflow):
    return json.dumps(workflow, ensure_ascii=False, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--saida", default=str(ARQ_WORKFLOW))
    parser.add_argument("--conferir", action="store_true",
                        help="compara o workflow montado com o que esta' em disco, sem escrever")
    args = parser.parse_args()

    texto = texto_do_workflow(montar())
    destino = pathlib.Path(args.saida)

    if args.conferir:
        if not destino.exists():
            print("FALHOU workflow ausente: %s" % destino)
            return 1
        if destino.read_text(encoding="utf-8") == texto:
            print("OK    workflow em disco == workflow montado agora (%s)" % destino)
            return 0
        print("FALHOU workflow em disco DIVERGE do montado agora: %s" % destino)
        return 1

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    print("workflow escrito: %s (%d bytes)" % (destino, len(texto.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
