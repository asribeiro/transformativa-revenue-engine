#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LinkedIn assistido v1 (`linkedin-assistido-v1`) — card TRE-W7-E04-T01.

O que este componente E: o workflow assistido por IA do canal LinkedIn (W7 / E04, Motor 2 — LinkedIn
organico). Ele faz exatamente TRES coisas:

  1. PREPARAR_RASCUNHO — a partir de uma recomendacao `PREPARE_LINKEDIN` do NBA (W5) + evidencia lida do
     banco, monta o rascunho (post ou DM) e registra o PEDIDO de aprovacao humana;
  2. PEDIR_APROVACAO — o texto vive em `human_approvals` (action_type LINKEDIN_RASCUNHO) e so sai do
     sistema como ARTEFATO PARA O HUMANO depois de `APPROVED` decidido pelo workflow irmao (W6-E03);
  3. REGISTRAR_ENGAJAMENTO — o que chega pelo LinkedIn (visita, curtida, comentario, DM, convite) entra
     como `interactions` channel=LINKEDIN, com inferencia MARCADA como inferencia.

O que ele NAO E (e nao vira por afrouxamento):
  - NAO publica, NAO agenda publicacao, NAO chama API do LinkedIn, NAO automatiza navegador. Quem publica
    e' o humano, no LinkedIn, fora do sistema — a maquina no maximo entrega o texto aprovado;
  - NAO comenta, NAO reage, NAO segue, NAO envia convite, NAO envia DM, NAO menciona, NAO responde
    comentario em nome dele: essas acoes RECUSAM com exit 5 (ACOES_HUMANAS_EXCLUSIVAS), sempre, com
    auditoria em agent_runs;
  - NAO decide aprovacao (dono: TRE-W6-E03-T01 / ADR-0004) e NAO muda status de pedido: o vocabulario de
    `human_approvals.status` e' fechado e quem o move e' o workflow irmao;
  - NAO gera texto com LLM aqui: o rascunho do aceite e' o renderizador OFFLINE declarado (nao inventa
    fato: cita a evidencia lida). O gerador real e' INJETAVEL (`--gerador <arquivo.py>`) e fica em homolog.

Guardas: sem evidencia nao existe rascunho; contato com `do_not_contact` BLOQUEIA; escrita so em
`human_approvals`, `interactions`, `agent_runs` e `sync_events` (DDL/UPDATE/DELETE RECUSAM); `prod`
RECUSA (exit 4, ADR-005).

Uso:
  python3 hermes/agents/linkedin/linkedin_assistido.py --planejar | --regras
  python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "docker exec -i pg-... psql -U sales_ai -d sales_intelligence" --preparar <recommendation_id> [--tipo POST|DM]
  python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --fila
  python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --tentar-publicar <approval_id>
  python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --tentar-acao PUBLICAR
  python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --engajamento <organization_id> --tipo LINKEDIN_CURTIDA --ocorrido-em 2026-10-03T10:00:00Z [--intent ELOGIO --ai-confidence 0.8]
  python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --desfazer <correlation_id> --confirmo

Exit: 0 = OK · 1 = FALHOU · 2 = uso · 3 = politica/escrita recusada · 4 = ambiente recusado ·
      5 = acao humana exclusiva recusada.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

VERSAO = "linkedin-assistido-v1"
AGENTE = "linkedin"
PAPEL = "agente_linkedin"
WORKFLOW = "linkedin-assistido"
WORKFLOW_VERSAO = "v1"
CARD = "TRE-W7-E04-T01"

POLITICA_PADRAO = "hermes/agents/linkedin/linkedin-assistido-v1.json"
CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"

TABELA_APROVACOES = "sales_intelligence.human_approvals"
TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_AUDITORIA = "sales_intelligence.agent_runs"
TABELA_SYNC = "sales_intelligence.sync_events"
TABELAS_ESCRITA = (TABELA_APROVACOES, TABELA_INTERACOES, TABELA_AUDITORIA, TABELA_SYNC)
TABELAS_LEITURA = (TABELA_APROVACOES, TABELA_INTERACOES, TABELA_AUDITORIA, TABELA_SYNC,
                   "sales_intelligence.organizations", "sales_intelligence.contacts",
                   "sales_intelligence.recommendations", "sales_intelligence.research_runs",
                   "sales_intelligence.pain_hypotheses", "sales_intelligence.signals")

PLANO = "PLANO"
PEDIDO_CRIADO = "PEDIDO_CRIADO"
JA_PEDIDO = "JA_PEDIDO"
SEM_EVIDENCIA = "SEM_EVIDENCIA"
BLOQUEADO = "BLOQUEADO"
ENTREGAVEL_AO_HUMANO = "ENTREGAVEL_AO_HUMANO"
AGUARDANDO_APROVACAO = "AGUARDANDO_APROVACAO"
ENGAJAMENTO_REGISTRADO = "ENGAJAMENTO_REGISTRADO"
JA_REGISTRADO = "JA_REGISTRADO"
RECUSADA = "RECUSADA"
DESFEITO = "DESFEITO"
ERRO = "ERRO"

MOTIVO_ACAO_PROIBIDA = "ACAO_HUMANA_EXCLUSIVA"
MOTIVO_PUBLICACAO_PROIBIDA = "PUBLICACAO_HUMANA_EXCLUSIVA"
MOTIVO_RECOMENDACAO_AUSENTE = "RECOMENDACAO_AUSENTE"
MOTIVO_ACAO_NAO_PREPARE_LINKEDIN = "ACAO_NAO_E_PREPARE_LINKEDIN"
MOTIVO_RECOMENDACAO_NAO_OPEN = "RECOMENDACAO_NAO_OPEN"
MOTIVO_SEM_EVIDENCIA = "SEM_EVIDENCIA"
MOTIVO_CONTATO_BLOQUEADO = "CONTATO_BLOQUEADO"
MOTIVO_ORGANIZACAO_AUSENTE = "ORGANIZACAO_AUSENTE"
MOTIVO_TIPO_DESCONHECIDO = "TIPO_DE_ENGAJAMENTO_DESCONHECIDO"
MOTIVO_POLITICA_INCOERENTE = "POLITICA_INCOERENTE"
MOTIVO_CONFIRMACAO_AUSENTE = "CONFIRMACAO_AUSENTE"
MOTIVO_GERADOR_AUSENTE = "GERADOR_AUSENTE"

AMBIENTE_RECUSADO = "prod"
AMBIENTES_PERMITIDOS = ("dev", "homolog")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_POLITICA_RECUSADA = 3
EXIT_RECUSOU_AMBIENTE = 4
EXIT_ACAO_HUMANA_EXCLUSIVA = 5

RAIZ_PADRAO = Path(__file__).resolve().parents[3]

TERMOS_PROIBIDOS = ("senha", "password", "token", "secret", "api_key", "authorization")


class RecusaDePolitica(Exception):
    """Contrato/politica incoerente: RECUSA (fail-closed), nunca 'corrige sozinho'."""


class RecusaDeEscrita(Exception):
    """Escrita fora das tabelas declaradas, DDL, UPDATE ou DELETE: RECUSA."""


class RecusaDeAcao(Exception):
    """Acao humana exclusiva ou pre-condicao ausente: motivo nomeado, nada de efeito escrito."""

    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(f"{motivo}: {detalhe}" if detalhe else motivo)
        self.motivo = motivo
        self.detalhe = detalhe


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ler_json(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def achar_arquivo(raiz: Path, caminho: str) -> Path:
    alvo = Path(caminho)
    if not alvo.is_absolute():
        alvo = raiz / caminho
    if not alvo.is_file():
        raise RecusaDePolitica(f"arquivo ausente: {alvo}")
    return alvo


def carregar_politicas(raiz: Path, caminho_politica: Path, caminho_contrato: Path) -> dict:
    politica = ler_json(caminho_politica)
    contrato = ler_json(caminho_contrato)
    problemas: list[str] = []

    if politica.get("canal") != "LINKEDIN":
        problemas.append("politica.canal deve ser LINKEDIN")
    if "PREPARE_LINKEDIN" not in contrato.get("vocabularies", {}).get("next_best_action", []):
        problemas.append("PREPARE_LINKEDIN nao esta no vocabulario next_best_action do contrato de dados")
    # atencao: o contrato usa chaves ACHATADAS em `vocabularies` (a chave e' literalmente
    # "human_approvals.status", nao human_approvals.status aninhado) — conferido nesta rodada.
    vocabulario_status = contrato.get("vocabularies", {}).get("human_approvals.status", [])
    for status in politica.get("aprovacao_humana", {}).get("status_do_vocabulario", []):
        if status not in vocabulario_status:
            problemas.append(f"status {status!r} fora do vocabulario human_approvals.status do contrato")
    proibidas = politica.get("acoes_declaradas", {}).get("maquina_proibidas", {})
    for exigida in ("PUBLICAR", "COMENTAR", "REAGIR", "ENVIAR_DM", "ENVIAR_CONVITE", "MENCIONAR"):
        if exigida not in proibidas:
            problemas.append(f"acao {exigida} precisa estar declarada em maquina_proibidas")
    if not politica.get("aprovacao_humana", {}).get("obrigatoria"):
        problemas.append("aprovacao humana precisa ser obrigatoria (ADR-0004)")
    guarda = politica.get("guarda_de_escrita", {}).get("tabelas", [])
    for tabela in guarda:
        if tabela not in (TABELA_APROVACOES, TABELA_INTERACOES, TABELA_AUDITORIA, TABELA_SYNC):
            problemas.append(f"politica.guarda_de_escrita.tabelas: {tabela} nao e' uma das 4 tabelas do card")
    if politica.get("ambiente", {}).get("recusado") != AMBIENTE_RECUSADO:
        problemas.append("politica.ambiente.recusado deve ser prod (ADR-005)")
    if politica.get("ambiente", {}).get("permitidos") != list(AMBIENTES_PERMITIDOS):
        problemas.append("politica.ambiente.permitidos deve ser [dev, homolog]")
    if problemas:
        raise RecusaDePolitica("; ".join(problemas))
    return {"politica": politica, "contrato": contrato}


def carregar_gerador(caminho: Path | None):
    """Gerador externo opcional (LLM). Sem ele, vale o renderizador offline declarado."""
    if caminho is None:
        return None
    if not caminho.is_file():
        raise RecusaDeAcao(MOTIVO_GERADOR_AUSENTE, str(caminho))
    espec = importlib.util.spec_from_file_location("gerador_linkedin_injetado", caminho)
    if espec is None or espec.loader is None:
        raise RecusaDeAcao(MOTIVO_GERADOR_AUSENTE, str(caminho))
    modulo = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(modulo)
    return modulo


def lit(texto) -> str:
    if texto is None:
        return "NULL"
    return "'" + str(texto).replace("'", "''") + "'"


def lit_json(objeto) -> str:
    return lit(json.dumps(objeto, ensure_ascii=False, sort_keys=True)) + "::jsonb"


def validar_sql(sql: str) -> None:
    if not sql:
        raise RecusaDeEscrita("SQL vazio")
    if re.search(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b", sql, re.IGNORECASE):
        raise RecusaDeEscrita("DDL recusado: nenhum schema muda por aqui (ADR-005)")
    if re.search(r"\bDELETE\s+FROM\b", sql, re.IGNORECASE):
        raise RecusaDeEscrita("DELETE recusado: o desfazer MARCA (sync_events), nao apaga")
    if re.search(r"\bUPDATE\b", sql, re.IGNORECASE):
        raise RecusaDeEscrita("UPDATE recusado: nenhum status de pedido muda por aqui")
    for m in re.finditer(r"\bINSERT\s+INTO\s+([a-zA-Z_\.]+)", sql, re.IGNORECASE):
        tabela = m.group(1).lower()
        if tabela not in TABELAS_ESCRITA:
            raise RecusaDeEscrita(f"INSERT em {tabela}: escrita permitida so em {list(TABELAS_ESCRITA)}")
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([a-zA-Z_\.]+)", sql, re.IGNORECASE):
        tabela = m.group(1).lower()
        if tabela.startswith("sales_intelligence.") and tabela not in TABELAS_LEITURA:
            raise RecusaDeEscrita(f"leitura de {tabela} nao declarada")


def executar_sql(sql: str | None, prefixo: str) -> tuple[int, str, str]:
    if sql:
        validar_sql(sql)
    # -q e' obrigatorio: sem ele o psql imprime o TAG do comando ("INSERT 0 0") no stdout e um
    # INSERT ... ON CONFLICT DO NOTHING pareceria ter escrito (defeito medido na rodada 1 deste aceite).
    comando = shlex.split(prefixo) + ["-q", "-v", "ON_ERROR_STOP=1", "-tA", "-F|", "-f", "-"]
    proc = subprocess.run(comando, input=(sql or ""), capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def linhas_de_json(saida: str) -> list[dict]:
    itens = []
    for linha in saida.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            itens.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return itens


def conta(prefixo: str, sql: str) -> int:
    codigo, saida, _ = executar_sql(sql, prefixo)
    if codigo != 0:
        raise RecusaDeAcao(ERRO, f"consulta falhou (exit {codigo})")
    return int((saida.strip() or "0").splitlines()[0])


# ---------------------------------------------------------------------------------------
# leitura dos fatos
# ---------------------------------------------------------------------------------------
def sql_da_recomendacao(recommendation_id: str) -> str:
    return (f"SELECT row_to_json(t)::text FROM (SELECT id::text, organization_id::text, contact_id::text, "
            f"action, status, COALESCE(description,'') AS description, COALESCE(rationale,'') AS rationale, "
            f"priority, confidence FROM sales_intelligence.recommendations WHERE id = {lit(recommendation_id)}) t;")


def sql_da_organizacao(organization_id: str) -> str:
    return (f"SELECT row_to_json(t)::text FROM (SELECT id::text, COALESCE(trade_name, legal_name, '') AS nome, "
            f"status, COALESCE(linkedin_url,'') AS linkedin_url, deleted_at::text "
            f"FROM sales_intelligence.organizations WHERE id = {lit(organization_id)}) t;")


def sql_do_contato(contact_id: str) -> str:
    return (f"SELECT row_to_json(t)::text FROM (SELECT id::text, COALESCE(full_name,'') AS full_name, "
            f"COALESCE(job_title,'') AS job_title, COALESCE(preferred_channel,'') AS preferred_channel, "
            f"do_not_contact, opt_out_whatsapp, COALESCE(linkedin_url,'') AS linkedin_url "
            f"FROM sales_intelligence.contacts WHERE id = {lit(contact_id)}) t;")


def sql_da_evidencia(organization_id: str) -> str:
    return (f"SELECT json_build_object("
            f"'pesquisas', (SELECT count(*) FROM sales_intelligence.research_runs r "
            f"  WHERE r.organization_id = {lit(organization_id)} AND r.status = 'COMPLETED'), "
            f"'dores', (SELECT count(*) FROM sales_intelligence.pain_hypotheses p "
            f"  WHERE p.organization_id = {lit(organization_id)}), "
            f"'sinais', (SELECT count(*) FROM sales_intelligence.signals s "
            f"  WHERE s.organization_id = {lit(organization_id)}), "
            f"'dores_citadas', (SELECT COALESCE(json_agg(p.pain_statement)::text, '[]') FROM "
            f"  (SELECT pain_statement FROM sales_intelligence.pain_hypotheses "
            f"   WHERE organization_id = {lit(organization_id)} ORDER BY created_at LIMIT 2) p), "
            f"'sinais_citados', (SELECT COALESCE(json_agg(s.title)::text, '[]') FROM "
            f"  (SELECT title FROM sales_intelligence.signals "
            f"   WHERE organization_id = {lit(organization_id)} ORDER BY detected_at DESC LIMIT 2) s)"
            f")::text;")


def ler_fatos(prefixo: str, recommendation_id: str) -> dict:
    linhas = linhas_de_json(executar_sql(sql_da_recomendacao(recommendation_id), prefixo)[1])
    if not linhas:
        raise RecusaDeAcao(MOTIVO_RECOMENDACAO_AUSENTE, recommendation_id)
    recomendacao = linhas[0]
    organization_id = recomendacao["organization_id"]
    linhas = linhas_de_json(executar_sql(sql_da_organizacao(organization_id), prefixo)[1])
    if not linhas:
        raise RecusaDeAcao(MOTIVO_ORGANIZACAO_AUSENTE, organization_id)
    organizacao = linhas[0]
    contato = None
    if recomendacao.get("contact_id"):
        linhas = linhas_de_json(executar_sql(sql_do_contato(recomendacao["contact_id"]), prefixo)[1])
        contato = linhas[0] if linhas else None
    evidence = linhas_de_json(executar_sql(sql_da_evidencia(organization_id), prefixo)[1])[0]
    return {"recomendacao": recomendacao, "organizacao": organizacao, "contato": contato, "evidencia": evidence}


# ---------------------------------------------------------------------------------------
# rascunho (renderizador offline declarado; gerador externo e' injetavel)
# ---------------------------------------------------------------------------------------
def montar_evidencia_citavel(fatos: dict) -> list[str]:
    itens: list[str] = []
    ev = fatos["evidencia"]
    for dor in json.loads(ev.get("dores_citadas") or "[]"):
        if dor:
            itens.append(f"hipotese de dor (nao validada): {dor}")
    for sinal in json.loads(ev.get("sinais_citados") or "[]"):
        if sinal:
            itens.append(f"sinal registrado: {sinal}")
    if ev.get("pesquisas"):
        itens.append(f"pesquisa concluida da empresa ({fatos['organizacao']['nome']})")
    return itens


def renderizar_offline(fatos: dict, tipo: str, politica: dict) -> dict:
    organizacao = fatos["organizacao"]
    contato = fatos["contato"] or {}
    evidencia = montar_evidencia_citavel(fatos)
    limite = int(politica.get("rascunho", {}).get("tamanho_maximo", 1300))
    if tipo == "DM":
        corpo = (
            f"Ola {contato.get('full_name') or 'tudo bem'}, "
            f"acompanho o trabalho da {organizacao['nome']}. "
            "Trabalho com eficiencia operacional e agentes de IA para tirar a operacao do modo "
            "apagar incendio — sem prometer numero antes de olhar o processo de perto. "
            "Faz sentido conversarmos 20 minutos para eu entender como as coisas rodam hoje?"
        )
        destinatario = contato.get("full_name") or organizacao["nome"]
    else:
        base = evidencia[0] if evidencia else "processos manuais no dia a dia"
        corpo = (
            f"Uma empresa que eu acompanho, {organizacao['nome']}, vive algo comum: {base}. "
            "Nao e' falta de ferramenta — e' falta de processo que aguente o volume. "
            "Automatizar antes de organizar so acelera o retrabalho. "
            "Organizar o processo primeiro, automatizar o repetitivo depois, e o time volta a fazer "
            "o que agrega valor."
        )
        destinatario = ""
    return {"tipo": tipo, "destinatario": destinatario, "corpo": corpo[:limite],
            "evidencia_citada": evidencia, "gerador": "renderizador-offline-declarado"}


def montar_rascunho(fatos: dict, tipo: str, politica: dict, gerador) -> dict:
    if gerador is not None and hasattr(gerador, "gerar"):
        rascunho = gerador.gerar(fatos=fatos, tipo=tipo, politica=politica)
        rascunho["gerador"] = getattr(gerador, "GERADOR", "gerador-injetado")
    else:
        rascunho = renderizar_offline(fatos, tipo, politica)
    if not rascunho.get("corpo"):
        raise RecusaDeAcao(MOTIVO_SEM_EVIDENCIA, "rascunho vazio nao entra em pedido de aprovacao")
    return rascunho


def hash_do_texto(rascunho: dict) -> str:
    material = json.dumps({"tipo": rascunho["tipo"], "destinatario": rascunho["destinatario"],
                           "corpo": rascunho["corpo"]}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def id_do_pedido(organization_id: str, recommendation_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"linkedin:{organization_id}:{recommendation_id}"))


def sql_do_pedido_existente(pedido_id: str) -> str:
    return (f"SELECT count(*) FROM sales_intelligence.human_approvals WHERE id = {lit(pedido_id)};")


def sql_da_escrita_pedido(politica: dict, pedido: dict) -> str:
    return (
        f"INSERT INTO sales_intelligence.human_approvals "
        f"(id, action_type, entity_type, entity_id, requested_by, proposed_action, status, requested_at) VALUES ("
        f"{lit(pedido['id'])}, {lit(politica['aprovacao_humana']['action_type'])}, "
        f"{lit(politica['aprovacao_humana']['entity_type'])}, {lit(pedido['entity_id'])}, "
        f"{lit(pedido['requested_by'])}, {lit_json(pedido['proposed_action'])}, 'PENDING', NOW()) RETURNING id::text;\n"
        f"INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, "
        f"operation, idempotency_key, status, request_payload, created_at) VALUES ("
        f"gen_random_uuid(), 'recommendation', {lit(pedido['entity_id'])}, 'hermes', 'linkedin', "
        f"'PREPARAR_RASCUNHO', {lit(pedido['chave'])}, 'PENDENTE_AO_HUMANO', "
        f"{lit_json({'approval_id': pedido['id'], 'tipo': pedido['proposed_action']['rascunho']['tipo']})}, NOW());\n")


def sql_da_auditoria(auditoria: dict) -> str:
    return (
        f"INSERT INTO sales_intelligence.agent_runs (id, agent_name, agent_role, agent_version, workflow, "
        f"workflow_version, organization_id, triggered_by, correlation_id, input, output, model, started_at, "
        f"finished_at, status) VALUES ("
        f"gen_random_uuid(), {lit(AGENTE)}, {lit(PAPEL)}, {lit(VERSAO)}, {lit(WORKFLOW)}, {lit(WORKFLOW_VERSAO)}, "
        f"{lit(auditoria.get('organization_id'))}, {lit(auditoria['triggered_by'])}, "
        f"{lit(auditoria['correlation_id'])}, {lit_json(auditoria.get('input') or {})}, "
        f"{lit_json(auditoria.get('output') or {})}, 'sem-llm', NOW(), NOW(), {lit(auditoria['status'])});\n")


def auditar(prefixo: str, auditoria: dict) -> None:
    codigo, _, erro = executar_sql(sql_da_auditoria(auditoria), prefixo)
    if codigo != 0:
        raise RecusaDeAcao(ERRO, f"auditoria falhou: {erro.strip()[:200]}")


# ---------------------------------------------------------------------------------------
# verbos
# ---------------------------------------------------------------------------------------
def preparar(carregado: dict, prefixo: str, recommendation_id: str, tipo: str, correlation_id: str,
             triggered_by: str, gerador) -> dict:
    politica = carregado["politica"]
    tipos_ok = politica["rascunho"]["tipos"]
    if tipo not in tipos_ok:
        raise RecusaDeAcao(MOTIVO_TIPO_DESCONHECIDO, tipo)
    fatos = ler_fatos(prefixo, recommendation_id)
    recomendacao, organizacao, contato, evidencia = (fatos["recomendacao"], fatos["organizacao"],
                                                     fatos["contato"], fatos["evidencia"])
    if organizacao.get("deleted_at"):
        auditar(prefixo, {"triggered_by": triggered_by, "correlation_id": correlation_id,
                          "organization_id": organizacao["id"], "status": "RECUSADA",
                          "input": {"recommendation_id": recommendation_id},
                          "output": {"veredito": BLOQUEADO, "motivo": MOTIVO_CONTATO_BLOQUEADO,
                                     "regra": "organizacao com deleted_at"}})
        raise RecusaDeAcao(MOTIVO_CONTATO_BLOQUEADO, "organizacao removida")
    if recomendacao["action"] != "PREPARE_LINKEDIN":
        raise RecusaDeAcao(MOTIVO_ACAO_NAO_PREPARE_LINKEDIN, recomendacao["action"])
    if recomendacao["status"] != "OPEN":
        raise RecusaDeAcao(MOTIVO_RECOMENDACAO_NAO_OPEN, recomendacao["status"])
    if contato and (contato.get("do_not_contact") or (tipo == "DM" and contato.get("opt_out_whatsapp"))):
        auditar(prefixo, {"triggered_by": triggered_by, "correlation_id": correlation_id,
                          "organization_id": organizacao["id"], "status": "RECUSADA",
                          "input": {"recommendation_id": recommendation_id, "contato_id": contato["id"]},
                          "output": {"veredito": BLOQUEADO, "motivo": MOTIVO_CONTATO_BLOQUEADO}})
        raise RecusaDeAcao(MOTIVO_CONTATO_BLOQUEADO, contato["id"])
    total_evidencia = int(evidencia.get("pesquisas", 0)) + int(evidencia.get("dores", 0)) + int(evidencia.get("sinais", 0))
    if total_evidencia < int(politica["evidencia"]["minima_para_preparar"]):
        auditar(prefixo, {"triggered_by": triggered_by, "correlation_id": correlation_id,
                          "organization_id": organizacao["id"], "status": "SEM_EVIDENCIA",
                          "input": {"recommendation_id": recommendation_id},
                          "output": {"veredito": SEM_EVIDENCIA, "motivo": MOTIVO_SEM_EVIDENCIA,
                                     "evidencia": evidencia}})
        raise RecusaDeAcao(MOTIVO_SEM_EVIDENCIA, json.dumps(evidencia, ensure_ascii=False))

    rascunho = montar_rascunho(fatos, tipo, politica, gerador)
    pedido_id = id_do_pedido(organizacao["id"], recommendation_id)
    if conta(prefixo, sql_do_pedido_existente(pedido_id)) > 0:
        return {"veredito": JA_PEDIDO, "approval_id": pedido_id, "motivo": "pedido ja existe (idempotencia)",
                "entregavel": False, "publica_a_maquina": False}
    texto_hash = hash_do_texto(rascunho)
    pedido = {
        "id": pedido_id, "entity_id": recommendation_id,
        "requested_by": f"{VERSAO} (dev harness)",
        "chave": f"linkedin:{organizacao['id']}:{recommendation_id}",
        "proposed_action": {
            "canal": "LINKEDIN",
            "recommendation_id": recommendation_id,
            "organization_id": organizacao["id"],
            "contato_id": (contato or {}).get("id"),
            "rascunho": rascunho,
            "texto_hash": texto_hash,
            "envio": "EXCLUSIVO_DO_HUMANO",
            "publicacao": "EXCLUSIVA_DO_HUMANO",
            "operador_canonico": politica["aprovacao_humana"]["operador_canonico"],
        },
    }
    codigo, saida, erro = executar_sql(sql_da_escrita_pedido(politica, pedido), prefixo)
    if codigo != 0:
        raise RecusaDeAcao(ERRO, f"escrita do pedido falhou: {erro.strip()[:200]}")
    auditar(prefixo, {"triggered_by": triggered_by, "correlation_id": correlation_id,
                      "organization_id": organizacao["id"], "status": "PEDIDO_CRIADO",
                      "input": {"recommendation_id": recommendation_id, "tipo": tipo},
                      "output": {"veredito": PEDIDO_CRIADO, "approval_id": pedido_id, "texto_hash": texto_hash,
                                 "evidencia": rascunho["evidencia_citada"], "gerador": rascunho["gerador"]}})
    return {"veredito": PEDIDO_CRIADO, "approval_id": pedido_id, "texto_hash": texto_hash,
            "tipo": tipo, "gerador": rascunho["gerador"], "evidencia_citada": rascunho["evidencia_citada"],
            "entregavel": False, "publica_a_maquina": False}


def fila(carregado: dict, prefixo: str, limite: int | None) -> dict:
    politica = carregado["politica"]
    acao = politica["aprovacao_humana"]["action_type"]
    sql = (f"SELECT row_to_json(t)::text FROM (SELECT id::text, status, requested_at::text, "
           f"proposed_action FROM sales_intelligence.human_approvals "
           f"WHERE action_type = {lit(acao)} AND status IN ('PENDING', 'APPROVED') "
           f"ORDER BY requested_at, id{' LIMIT ' + str(int(limite)) if limite else ''}) t;")
    itens = linhas_de_json(executar_sql(sql, prefixo)[1])
    itens.sort(key=lambda i: i["id"])
    entregaveis, aguardando = [], []
    for item in itens:
        texto = (item.get("proposed_action") or {}).get("rascunho", {})
        if item["status"] == "APPROVED":
            entregaveis.append({"approval_id": item["id"], "status": item["status"],
                                "texto_hash": (item.get("proposed_action") or {}).get("texto_hash"),
                                "tipo": texto.get("tipo"), "corpo": texto.get("corpo"),
                                "veredito": ENTREGAVEL_AO_HUMANO,
                                "proximo_passo": "o HUMANO publica no LinkedIn; a maquina nao publica"})
        else:
            aguardando.append({"approval_id": item["id"], "status": item["status"], "veredito": AGUARDANDO_APROVACAO})
    return {"veredito": PLANO, "entregaveis_ao_humano": entregaveis, "aguardando_aprovacao": aguardando,
            "entregaveis": len(entregaveis), "aguardando": len(aguardando), "publica_a_maquina": False}


def recusar_acao_humana(carregado: dict, prefixo: str, acao: str, correlation_id: str,
                        triggered_by: str, pedido_id: str | None = None) -> dict:
    politica = carregado["politica"]
    proibidas = politica["acoes_declaradas"]["maquina_proibidas"]
    if acao not in proibidas:
        raise RecusaDeAcao(MOTIVO_TIPO_DESCONHECIDO, acao)
    motivo = MOTIVO_PUBLICACAO_PROIBIDA if acao in ("PUBLICAR", "AGENDAR_PUBLICACAO") else MOTIVO_ACAO_PROIBIDA
    auditar(prefixo, {"triggered_by": triggered_by, "correlation_id": correlation_id,
                      "status": "RECUSADA",
                      "input": {"acao_pedida": acao, "approval_id": pedido_id},
                      "output": {"veredito": RECUSADA, "motivo": motivo, "regra": proibidas[acao]}})
    return {"veredito": RECUSADA, "motivo": motivo, "acao": acao, "regra": proibidas[acao],
            "approval_id": pedido_id, "escrito": 0, "publica_a_maquina": False}


def registrar_engajamento(carregado: dict, prefixo: str, organization_id: str, tipo: str, direction: str,
                          ocorrido_em: str, resumo: str | None, intent: str | None, ai_confidence,
                          correlation_id: str, triggered_by: str) -> dict:
    politica = carregado["politica"]
    if tipo not in politica["engajamento"]["interaction_types"]:
        raise RecusaDeAcao(MOTIVO_TIPO_DESCONHECIDO, tipo)
    if direction not in politica["engajamento"]["directions"]:
        raise RecusaDeAcao(MOTIVO_TIPO_DESCONHECIDO, direction)
    linhas = linhas_de_json(executar_sql(sql_da_organizacao(organization_id), prefixo)[1])
    if not linhas:
        raise RecusaDeAcao(MOTIVO_ORGANIZACAO_AUSENTE, organization_id)
    chave = f"linkedin-eng:{organization_id}:{tipo}:{ocorrido_em}"
    inferencia = "INFERENCIA" if (intent or ai_confidence is not None) else None
    sql = (
        f"INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, "
        f"operation, idempotency_key, status, request_payload, created_at) VALUES ("
        f"gen_random_uuid(), 'organization', {lit(organization_id)}, 'linkedin', 'hermes', "
        f"'REGISTRAR_ENGAJAMENTO', {lit(chave)}, 'REGISTRADO', {lit_json({'tipo': tipo, 'direction': direction})}, NOW()) "
        f"ON CONFLICT (idempotency_key) DO NOTHING RETURNING id::text;\n"
    )
    codigo, saida, erro = executar_sql(sql, prefixo)
    if codigo != 0:
        raise RecusaDeAcao(ERRO, f"claim do engajamento falhou: {erro.strip()[:200]}")
    # a reclamacao so' vale com um id UUID de volta: stdout vazio/poluido (tag de comando, aviso) e'
    # tratado como JA_REGISTRADO, nunca como escrita (fail-closed, defeito medido na rodada 1 do aceite).
    primeira = saida.strip().splitlines()[0].strip() if saida.strip() else ""
    if not re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", primeira):
        return {"veredito": JA_REGISTRADO, "chave": chave, "escrito": 0}
    sql_interacao = (
        f"INSERT INTO sales_intelligence.interactions (id, organization_id, contact_id, channel, direction, "
        f"interaction_type, occurred_at, content_summary, intent, ai_confidence, created_at) VALUES ("
        f"gen_random_uuid(), {lit(organization_id)}, NULL, {lit(politica['engajamento']['channel'])}, "
        f"{lit(direction)}, {lit(tipo)}, {lit(ocorrido_em)}::timestamptz, {lit(resumo)}, {lit(intent)}, "
        f"{'NULL' if ai_confidence is None else str(ai_confidence)}, NOW()) RETURNING id::text;\n")
    codigo, saida, erro = executar_sql(sql_interacao, prefixo)
    if codigo != 0:
        raise RecusaDeAcao(ERRO, f"interacao falhou: {erro.strip()[:200]}")
    interacao_id = saida.strip().splitlines()[0] if saida.strip() else None
    auditar(prefixo, {"triggered_by": triggered_by, "correlation_id": correlation_id,
                      "organization_id": organization_id, "status": "ENGAJAMENTO_REGISTRADO",
                      "input": {"tipo": tipo, "direction": direction, "ocorrido_em": ocorrido_em},
                      "output": {"veredito": ENGAJAMENTO_REGISTRADO, "interaction_id": interacao_id,
                                 "inferencia": inferencia}})
    return {"veredito": ENGAJAMENTO_REGISTRADO, "interaction_id": interacao_id, "chave": chave,
            "inferencia": inferencia, "escrito": 1}


def desfazer(prefixo: str, correlation_id: str, confirmar: bool) -> dict:
    if not confirmar:
        raise RecusaDeAcao(MOTIVO_CONFIRMACAO_AUSENTE, "--desfazer exige --confirmo")
    sql = (f"INSERT INTO sales_intelligence.sync_events (id, entity_type, source_system, target_system, operation, "
           f"idempotency_key, status, request_payload, created_at) VALUES ("
           f"gen_random_uuid(), 'agent_run', 'hermes', 'linkedin', 'DESFAZER', "
           f"{lit('linkedin-desfazer:' + correlation_id)}, 'MARCADO', "
           f"{lit_json({'correlation_id': correlation_id})}, NOW()) RETURNING id::text;")
    codigo, saida, erro = executar_sql(sql, prefixo)
    if codigo != 0:
        raise RecusaDeAcao(ERRO, f"desfazer falhou: {erro.strip()[:200]}")
    return {"veredito": DESFEITO, "marca": saida.strip(), "apagou": 0,
            "regra": "o desfazer MARCA (sync_events), nao apaga e nao muda status de pedido"}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def comando_planejar(carregado: dict, caminho_politica: Path) -> int:
    politica = carregado["politica"]
    print(f"PLANO {VERSAO} card={CARD} politica={caminho_politica.name}@{politica['versao']}")
    print(f"  canal: {politica['canal']} ({politica['motor']})")
    print(f"  maquina pode: {politica['acoes_declaradas']['maquina_liberadas']}")
    print(f"  maquina NUNCA: {sorted(politica['acoes_declaradas']['maquina_proibidas'])}")
    print(f"  aprovacao humana: {politica['aprovacao_humana']['action_type']} "
          f"(dono: {politica['aprovacao_humana']['dono_da_decisao']})")
    print(f"  ambiente: permitidos={politica['ambiente']['permitidos']} recusado={politica['ambiente']['recusado']}")
    print(f"  escrita: {politica['guarda_de_escrita']['tabelas']} "
          f"(ddl={politica['guarda_de_escrita']['ddl']}, delete={politica['guarda_de_escrita']['delete']})")
    print(f"  evidencia minima: {politica['evidencia']['minima_para_preparar']}")
    print(f"  vereditos: {politica['vereditos']}")
    print("PLANO_OK: nenhuma conexao foi aberta (nem banco, nem LinkedIn)")
    return EXIT_OK


def comando_regras(carregado: dict) -> int:
    politica = carregado["politica"]
    print(f"REGRAS {VERSAO} (politica {politica['nome']}@{politica['versao']})")
    for chave in ("o_que_e", "o_que_nao_e", "rascunho", "engajamento", "lacunas_declaradas"):
        print(f"  {chave}: {json.dumps(politica[chave], ensure_ascii=False, sort_keys=True)}")
    return EXIT_OK


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSAO} — LinkedIn assistido (card {CARD})")
    parser.add_argument("--ambiente", default=None, choices=["dev", "homolog", AMBIENTE_RECUSADO])
    parser.add_argument("--prefixo", default=None, help="porta de banco (ex.: docker exec -i pg-... psql ...)")
    parser.add_argument("--preparar", action="append", default=[], help="recommendation_id (repetivel)")
    parser.add_argument("--tipo", default="POST", help="POST (organico) ou DM (rascunho para o humano)")
    parser.add_argument("--fila", action="store_true", help="pedidos LINKEDIN_RASCUNHO e o que ja pode ir ao humano")
    parser.add_argument("--limite", type=int, default=None)
    parser.add_argument("--tentar-publicar", default=None, help="approval_id: RECUSA por desenho (exit 5)")
    parser.add_argument("--tentar-acao", default=None, help="acao proibida a maquina: RECUSA (exit 5)")
    parser.add_argument("--engajamento", default=None, help="organization_id do engajamento recebido")
    parser.add_argument("--direction", default="INBOUND", choices=["INBOUND", "OUTBOUND"])
    parser.add_argument("--ocorrido-em", default=None)
    parser.add_argument("--resumo", default=None)
    parser.add_argument("--intent", default=None)
    parser.add_argument("--ai-confidence", type=float, default=None)
    parser.add_argument("--desfazer", default=None)
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--gerador", default=None, help="modulo gerador externo (LLM) — injecao de teste")
    parser.add_argument("--relatorio", default=None)
    parser.add_argument("--correlation-id", default=None)
    parser.add_argument("--triggered-by", default="hermes-dev-harness")
    parser.add_argument("--raiz", default=None)
    parser.add_argument("--politica", default=None)
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--planejar", action="store_true")
    parser.add_argument("--regras", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = montar_parser().parse_args(argv)
    raiz = Path(args.raiz).resolve() if args.raiz else RAIZ_PADRAO
    try:
        caminho_politica = achar_arquivo(raiz, args.politica or POLITICA_PADRAO)
        caminho_contrato = achar_arquivo(raiz, args.contrato or CONTRATO_DADOS_PADRAO)
        carregado = carregar_politicas(raiz, caminho_politica, caminho_contrato)
    except RecusaDePolitica as recusa:
        print(f"RECUSADO {MOTIVO_POLITICA_INCOERENTE}: {recusa}", file=sys.stderr)
        return EXIT_POLITICA_RECUSADA
    if args.planejar:
        return comando_planejar(carregado, caminho_politica)
    if args.regras:
        return comando_regras(carregado)
    if args.ambiente is None:
        print("uso: --ambiente dev|homolog (ou --planejar/--regras)", file=sys.stderr)
        return EXIT_USO
    if args.ambiente == AMBIENTE_RECUSADO:
        print("RECUSADO ambiente prod: ADR-005 (nada nasce em producao)", file=sys.stderr)
        return EXIT_RECUSOU_AMBIENTE
    if not args.prefixo:
        print("uso: --prefixo exige a porta de banco", file=sys.stderr)
        return EXIT_USO
    if not (args.preparar or args.fila or args.tentar_publicar or args.tentar_acao or args.engajamento or args.desfazer):
        print("uso: escolha --preparar, --fila, --tentar-publicar, --tentar-acao, --engajamento ou --desfazer",
              file=sys.stderr)
        return EXIT_USO
    if args.tentar_acao and args.tentar_acao not in carregado["politica"]["acoes_declaradas"]["maquina_proibidas"]:
        print(f"uso: {args.tentar_acao!r} nao e' uma acao declarada como proibida", file=sys.stderr)
        return EXIT_USO
    if args.engajamento and not args.ocorrido_em:
        print("uso: --engajamento exige --ocorrido-em (ISO-8601)", file=sys.stderr)
        return EXIT_USO

    correlation_id = args.correlation_id or str(uuid.uuid4())
    try:
        uuid.UUID(str(correlation_id))
    except ValueError:
        print("uso: --correlation-id precisa ser UUID (coluna uuid no canonico)", file=sys.stderr)
        return EXIT_USO

    relatorio: dict = {"workflow": VERSAO, "card": CARD, "ambiente": args.ambiente,
                       "correlation_id": correlation_id, "iniciado_em": agora(),
                       "triggered_by": args.triggered_by, "publica_a_maquina": False}
    try:
        gerador = carregar_gerador(Path(args.gerador).resolve() if args.gerador else None)
        if args.preparar:
            resultados = []
            for recommendation_id in args.preparar:
                try:
                    resultados.append(preparar(carregado, args.prefixo, recommendation_id, args.tipo,
                                               correlation_id, args.triggered_by, gerador))
                except RecusaDeAcao as recusa:
                    resultados.append({"veredito": RECUSADA, "recommendation_id": recommendation_id,
                                       "motivo": recusa.motivo, "detalhe": recusa.detalhe, "escrito": 0})
            relatorio["preparos"] = resultados
            relatorio["veredito"] = (PEDIDO_CRIADO if any(r["veredito"] == PEDIDO_CRIADO for r in resultados)
                                     else (JA_PEDIDO if any(r["veredito"] == JA_PEDIDO for r in resultados)
                                           else RECUSADA))
        if args.fila:
            relatorio["fila"] = fila(carregado, args.prefixo, args.limite)
            relatorio["veredito"] = relatorio["fila"]["veredito"]
        if args.tentar_publicar:
            relatorio["recusa_publicacao"] = recusar_acao_humana(carregado, args.prefixo, "PUBLICAR",
                                                                 correlation_id, args.triggered_by,
                                                                 args.tentar_publicar)
            relatorio["veredito"] = RECUSADA
        if args.tentar_acao:
            relatorio["recusa_acao"] = recusar_acao_humana(carregado, args.prefixo, args.tentar_acao,
                                                           correlation_id, args.triggered_by)
            relatorio["veredito"] = RECUSADA
        if args.engajamento:
            relatorio["engajamento"] = registrar_engajamento(
                carregado, args.prefixo, args.engajamento, args.tipo, args.direction, args.ocorrido_em,
                args.resumo, args.intent, args.ai_confidence, correlation_id, args.triggered_by)
            relatorio["veredito"] = relatorio["engajamento"]["veredito"]
        if args.desfazer:
            relatorio["desfazer"] = desfazer(args.prefixo, args.desfazer, args.confirmo)
            relatorio["veredito"] = DESFEITO
    except RecusaDeAcao as recusa:
        relatorio["veredito"] = RECUSADA
        relatorio["recusa"] = {"motivo": recusa.motivo, "detalhe": recusa.detalhe}
    except RecusaDeEscrita as recusa:
        print(f"RECUSADO {EXIT_POLITICA_RECUSADA}: {recusa}", file=sys.stderr)
        return EXIT_POLITICA_RECUSADA

    relatorio["terminado_em"] = agora()
    saida = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
    for termo in TERMOS_PROIBIDOS:
        if termo in saida.lower():
            print(f"RECUSADO vazamento de termo sensivel na saida: {termo}", file=sys.stderr)
            return EXIT_FALHOU
    if args.relatorio:
        Path(args.relatorio).write_text(saida + "\n", encoding="utf-8")
    print(saida)
    if relatorio["veredito"] == ERRO:
        return EXIT_FALHOU
    if relatorio["veredito"] == RECUSADA and (args.tentar_publicar or args.tentar_acao):
        return EXIT_ACAO_HUMANA_EXCLUSIVA
    return EXIT_OK if relatorio["veredito"] != RECUSADA else EXIT_FALHOU


if __name__ == "__main__":
    sys.exit(main())
