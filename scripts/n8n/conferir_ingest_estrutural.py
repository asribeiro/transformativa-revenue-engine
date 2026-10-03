#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lente estrutural da PORTA de ingestao Odoo -> PostgreSQL — card TRE-W3-E03-T01.

O que esta lente prova (item por item, sem container nenhum — ela roda antes do aceite caro):

  1. o workflow versionado E' o montado a partir dos artefatos (o nucleo embutido e' o arquivo, byte
     a byte; o SQL dos nos e' o arquivo, byte a byte; o adaptador do Code node e' declarado);
  2. a PORTA e' uma so: um webhook POST com autenticacao por header, sem host literal e sem
     caminho paralelo (nenhum outro no de escrita alem dos dois Postgres da mesma credencial);
  3. os dois lados falam a MESMA lista de eventos: a lista do contrato do ingestor == a lista do
     contrato de dados == a lista declarada no modulo Odoo;
  4. cada campo exigido de cada evento EXISTE no detector do lado Odoo (o contrato nao pede campo
     que o produtor nao manda) e o nucleo nao fala com banco nem com HTTP;
  5. a idempotencia esta' onde a regra manda: ON CONFLICT (idempotency_key) DO NOTHING nos dois SQL,
     e a recusa tem status REFUSED com motivo nomeado;
  6. o segredo nao esta' no versionado: o workflow aponta credencial por id/nome.

Uso: python3 scripts/n8n/conferir_ingest_estrutural.py [--json]
Saida: OK/FALHOU por item + RESULTADO; exit 0 = tudo OK, 1 = falhou.
"""
import ast
import json
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CONTRATO = RAIZ / "n8n" / "contracts" / "odoo-events-ingest.v1.json"
NUCLEO = RAIZ / "n8n" / "codigo" / "nucleo-ingest-eventos.js"
SQL_ACEITE = RAIZ / "n8n" / "sql" / "ingerir-evento.sql"
SQL_RECUSA = RAIZ / "n8n" / "sql" / "registrar-recusa.sql"
WORKFLOW = RAIZ / "n8n" / "workflows" / "TRE-odoo-events-ingest.json"
CONTRATO_DE_DADOS = RAIZ / "docs" / "data" / "data_contract_v1.json"
MODULO = RAIZ / "odoo" / "addons" / "transformativa_sales_ai"
MODELO_DA_FILA = MODULO / "models" / "tf_evento_outbox.py"
DETECTORES = {
    "crm.lead": MODULO / "models" / "eventos_crm_lead.py",
    "mail.activity": MODULO / "models" / "eventos_mail_activity.py",
    "calendar.event": MODULO / "models" / "eventos_calendar_event.py",
}
MARCADOR = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"

ITENS = []
FALHAS = []


def ok(nome):
    ITENS.append(True)
    print("OK    %s" % nome)


def falhou(nome, detalhe=""):
    ITENS.append(False)
    FALHAS.append(nome)
    print("FALHOU %s%s" % (nome, ("  [%s]" % detalhe) if detalhe else ""))


def confere(nome, condicao, detalhe=""):
    if condicao:
        ok(nome)
    else:
        falhou(nome, detalhe)


def no_por_nome(workflow, nome):
    for no in workflow["nodes"]:
        if no["name"] == nome:
            return no
    return None


def parametros_do_no(workflow, nome):
    no = no_por_nome(workflow, nome)
    return (no or {}).get("parameters", {})


def eventos_do_modulo():
    arvore = ast.parse(MODELO_DA_FILA.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign) and any(
            isinstance(alvo, ast.Name) and alvo.id == "EVENTOS_ODOO_PARA_PG" for alvo in no.targets
        ):
            return list(ast.literal_eval(no.value))
    return []


def main():
    contrato = json.loads(CONTRATO.read_text(encoding="utf-8"))
    nucleo = NUCLEO.read_text(encoding="utf-8")
    sql_aceite = SQL_ACEITE.read_text(encoding="utf-8")
    sql_recusa = SQL_RECUSA.read_text(encoding="utf-8")
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    contrato_de_dados = json.loads(CONTRATO_DE_DADOS.read_text(encoding="utf-8"))

    eventos_do_contrato = [evento["event_type"] for evento in contrato["eventos"]]
    eventos_do_baseline = list(contrato_de_dados["events"]["odoo_to_pg"])

    # ---- 1. o workflow e' o montado a partir dos artefatos
    confere("o workflow tem o id estavel declarado", workflow.get("id") == "TREodooEventos1",
            str(workflow.get("id")))
    confere("o workflow nasce INATIVO (nada nasce ligado)", workflow.get("active") is False)
    no_nucleo = no_por_nome(workflow, "Nucleo: decidir ingestao")
    confere("existe o Code node do nucleo", no_nucleo is not None)
    if no_nucleo:
        js = no_nucleo["parameters"]["jsCode"]
        confere("o nucleo versionado esta EMBUTIDO byte a byte", js.startswith(nucleo))
        confere("o adaptador do Code node e' declarado por marcador", MARCADOR in js)
        confere("fora do nucleo so' existe o adaptador (nenhuma funcao declarada la')",
                js[len(nucleo):].count("function ") == 0,
                "funcoes fora do nucleo: %s" % js[len(nucleo):].count("function "))
    for nome, esperado, rotulo in (
        ("Postgres: ingerir evento (trilha)", sql_aceite, "ingerir-evento.sql"),
        ("Postgres: registrar recusa (trilha)", sql_recusa, "registrar-recusa.sql"),
    ):
        no = no_por_nome(workflow, nome)
        confere("existe o no Postgres de %s" % rotulo, no is not None)
        if no:
            confere("o SQL do no e' o arquivo %s (byte a byte)" % rotulo,
                    no["parameters"]["query"] == esperado)

    # ---- 2. porta unica, sem host literal, sem caminho paralelo
    webhook = no_por_nome(workflow, "Webhook: evento do Odoo")
    confere("existe o webhook da porta", webhook is not None)
    if webhook:
        parametros = webhook["parameters"]
        confere("o webhook e' POST no caminho do contrato",
                parametros.get("httpMethod") == contrato["porta"]["metodo"]
                and contrato["porta"]["rota"].endswith("/" + parametros.get("path", "")),
                str(parametros.get("path")))
        confere("o webhook responde pelo no de resposta",
                parametros.get("responseMode") == "responseNode")
        confere("o webhook autentica por header (credencial declarada)",
                parametros.get("authentication") == "headerAuth"
                and webhook.get("credentials", {}).get("httpHeaderAuth", {}).get("id")
                == contrato["credenciais"]["token_da_porta"]["id"])
    texto_do_workflow = json.dumps(workflow, ensure_ascii=False)
    confere("nenhum host literal no workflow",
            not re.search(r"https?://[a-zA-Z0-9]", texto_do_workflow))
    escritas = [no for no in workflow["nodes"]
                if no["type"] in ("n8n-nodes-base.postgres", "n8n-nodes-base.httpRequest")]
    confere("so' os dois Postgres da mesma credencial escrevem",
            len(escritas) == 2 and all(
                no["credentials"]["postgres"]["id"] == contrato["credenciais"]["postgres"]["id"]
                for no in escritas))
    tabelas = set(re.findall(r"INSERT\s+INTO\s+([a-z_.]+)", sql_aceite + sql_recusa, re.I))
    confere("a unica tabela tocada e' a trilha do contrato",
            tabelas == {"sales_intelligence.sync_events"}, str(tabelas))
    confere("nenhum UPDATE/DELETE/DDL nos SQL da porta",
            not re.search(r"\b(UPDATE|DELETE|DROP|ALTER|TRUNCATE)\b", sql_aceite + sql_recusa, re.I))
    confere("o no de resposta devolve o corpo e o codigo do SQL",
            "corpo" in parametros_do_no(workflow, "Resposta da porta").get("responseBody", "")
            and "codigo_http" in
            parametros_do_no(workflow, "Resposta da porta").get("options", {}).get("responseCode", ""))

    # ---- 3. os dois lados falam a mesma lista de eventos
    confere("a lista do ingestor == a lista do contrato de dados",
            sorted(eventos_do_contrato) == sorted(eventos_do_baseline),
            "%s x %s" % (eventos_do_contrato, eventos_do_baseline))
    confere("a lista do ingestor == a lista declarada no modulo Odoo",
            sorted(eventos_do_contrato) == sorted(eventos_do_modulo()),
            str(eventos_do_modulo()))
    for evento in contrato["eventos"]:
        confere("o evento %s tem campos exigidos declarados" % evento["event_type"],
                bool(evento.get("exigidos")))
        confere("o evento %s declara modelo de origem conhecido" % evento["event_type"],
                evento.get("modelo_origem") in DETECTORES,
                str(evento.get("modelo_origem")))

    # ---- 4. o contrato nao pede campo que o produtor nao manda
    fontes = {modelo: caminho.read_text(encoding="utf-8") for modelo, caminho in DETECTORES.items()}
    for evento in contrato["eventos"]:
        fonte = fontes[evento["modelo_origem"]]
        for campo in evento["exigidos"]:
            confere("o detector de %s emite o campo exigido %s" % (evento["event_type"], campo),
                    ("'%s'" % campo) in fonte or ("%s=" % campo) in fonte)
    confere("o nucleo puro nao fala com banco nem com HTTP",
            not re.search(r"\brequire\s*\(|\bfetch\s*\(|\bSELECT\b|\bINSERT\b", nucleo))

    # ---- 5. idempotencia e recusa nomeada
    for rotulo, sql in (("aceite", sql_aceite), ("recusa", sql_recusa)):
        confere("o SQL de %s e idempotente pela chave (ON CONFLICT DO NOTHING)" % rotulo,
                "ON CONFLICT (idempotency_key) DO NOTHING" in sql)
        confere("o SQL de %s escreve source_system odoo e target_system postgres" % rotulo,
                "'odoo', 'postgres'" in sql)
    confere("a recusa grava status REFUSED e o motivo em error_message",
            "$7, $6::jsonb" in sql_recusa and "error_message" in sql_recusa)
    confere("o aceite grava status COMPLETED",
            contrato["trilha"]["status"]["aceito"] == "COMPLETED"
            and contrato["trilha"]["status"]["recusado"] == "REFUSED")

    # ---- 6. contrato completo e sem segredo
    regras = contrato["envelope"]["ordem_da_validacao"]
    motivos = contrato["envelope"]["motivos_de_recusa"]
    confere("cada regra da ordem de validacao tem motivo declarado",
            all(regra in motivos for regra in regras),
            str([regra for regra in regras if regra not in motivos]))
    confere("a versao do nucleo acompanha a do contrato",
            ("NUCLEO_VERSAO = '%s'" % contrato["versao"]) in nucleo)
    confere("o workflow aponta a credencial do token por id/nome (nunca por valor)",
            set(((webhook or {}).get("credentials", {}).get("httpHeaderAuth") or {}).keys())
            == {"id", "name"}
            and webhook["credentials"]["httpHeaderAuth"]["id"]
            == contrato["credenciais"]["token_da_porta"]["id"])

    if FALHAS:
        print("RESULTADO: CONTRATO_INGEST_FALHOU (%s itens, %s falha(s))" % (len(ITENS), len(FALHAS)))
        return 1
    print("RESULTADO: CONTRATO_INGEST_OK (%s itens, 0 falhas)" % len(ITENS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
