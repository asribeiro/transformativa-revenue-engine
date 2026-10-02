#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agente Scout v1 — descoberta/ingestao de empresa candidata (card TRE-W4-E01-T01).

O que este agente FAZ (e so isto): recebe empresas candidatas de uma fonte declarada,
normaliza, resolve a IDENTIDADE por identificador FORTE na prioridade do Data Contract
V1.0 (cnpj -> domain -> linkedin_url) e cria a organizacao no PostgreSQL com
`status = 'DISCOVERED'` — o primeiro estado do funil (doc 03 §2, "Descoberto").

O que ele NAO faz, por desenho (declarado em `agente-scout-v1.json` -> lacunas):
  - nao pesquisa, nao enriquece, nao pontua (research/signals/score sao W4-E02..E06 e W5);
  - nao emite evento de outbox (COMPANY_QUALIFIED nasce no score, doc 06 §5);
  - nao mescla duplicidade: a decisao de MERGE e do modulo do TRE-W1-E04-T01
    (`entity_match_confidence`, limiar `auto_merge_threshold` lido do contrato);
  - nao toca Odoo, Titan, n8n nem o host;
  - nao escreve em producao (ADR-005).

Regras de identidade (contrato, nao heuristica):
  - cria SOMENTE com identificador forte VALIDO;
  - candidata que carrega identificador forte e nenhum valido -> RECUSADA;
  - candidata sem nenhum identificador forte declarado -> REVISAO_IDENTIDADE (fila humana);
  - fortes casando com organizacoes DISTINTAS -> REVISAO_IDENTIDADE (ambiguidade e
    reportada, nunca resolvida por heuristica — `dedup.rule` do contrato);
  - identificador forte invalido e DESCARTADO (nao entra no banco nem na chave).

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"):
  - a chave e a identidade (`scout:org:<tipo>:<valor>`), gravada em
    `sync_events.idempotency_key` (UNIQUE). A ingestao e uma unica instrucao SQL com CTEs
    de escrita: se a chave ja existe, NADA e inserido (nem organizacao, nem evento duplicado)
    e o veredito e JA_EXISTE.
  - a historia de cada tentativa fica em `agent_runs` (uma linha por candidato, com o
    `correlation_id` do lote) — auditoria nao depende da narrativa de quem rodou.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/agents/scout/scout.py --planejar --fonte candidatas.jsonl
  python3 hermes/agents/scout/scout.py --ambiente dev --fonte candidatas.jsonl \
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
      --relatorio /tmp/scout-rodada.json
  python3 hermes/agents/scout/scout.py --desfazer <correlation_id>
  python3 hermes/agents/scout/scout.py --desfazer <correlation_id> --confirmo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do agente (espelha `hermes/agents/scout/agente-scout-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "scout"
PAPEL = "discovery"
VERSAO = "1.0.0"
WORKFLOW = "descoberta-empresa"
WORKFLOW_VERSAO = "v1"

STATUS_ORGANIZACAO = "DISCOVERED"
ACTION_TYPE_REVISAO = "SCOUT_IDENTITY_REVIEW"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"
TABELA_APPROVALS = "sales_intelligence.human_approvals"
TABELAS_PERMITIDAS = (TABELA_ORGANIZACOES, TABELA_AGENT_RUNS, TABELA_SYNC_EVENTS, TABELA_APPROVALS)

VER_CRIADA = "CRIADA"
VER_JA_EXISTE = "JA_EXISTE"
VER_REVISAO = "REVISAO_IDENTIDADE"
VER_RECUSADA = "RECUSADA"
VER_ERRO = "ERRO"
VEREDITOS = (VER_CRIADA, VER_JA_EXISTE, VER_REVISAO, VER_RECUSADA, VER_ERRO)

STATUS_AGENT_RUNS = {
    VER_CRIADA: "COMPLETED",
    VER_JA_EXISTE: "COMPLETED",
    VER_REVISAO: "REVIEW_REQUIRED",
    VER_RECUSADA: "REJECTED",
    VER_ERRO: "FAILED",
}

# Vereditos do modo `--planejar` (nenhuma conexao de banco e feita nele)
PLANEJADO_CRIAR = "PLANEJADO_CRIAR"
PLANEJADO_REVISAR = "PLANEJADO_REVISAR"
PLANEJADO_RECUSAR = "PLANEJADO_RECUSAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

FONTES = (
    "LINKEDIN",
    "WEB",
    "GOOGLE",
    "META",
    "WHATSAPP",
    "TITAN",
    "EVENTOS",
    "DADOS_PUBLICOS",
)

# Vocabulario do recibo do JEV (hermes/jev/routing/router.py: OUTCOME_*)
JEV_EXECUTAR = "PASS"
JEV_ESCALAR = "ESCALATE"
JEV_BLOQUEAR = "BLOCK"
LANES = ("small", "medium", "high", "critical")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4
EXIT_FONTE = 5

RAIZ_PADRAO = Path(__file__).resolve().parents[3]
CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_AGENTE_PADRAO = "hermes/agents/scout/agente-scout-v1.json"


class RecusaDeAmbiente(Exception):
    """Ambiente nao permitido (ADR-005): nada e escrito."""


class ReciboJEVInvalido(Exception):
    """Chamada de LLM sem recibo valido do JEV: fail-closed (doc 07 §7, W4)."""


class GuardaDeEscritaViolada(Exception):
    """SQL tentando escrever fora das tabelas/operacoes declaradas."""


class PortaIndisponivel(Exception):
    """A porta de banco falhou."""


# ---------------------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------------------
def carregar_json(caminho) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_contrato_do_agente(raiz) -> dict:
    return carregar_json(Path(raiz) / CONTRATO_AGENTE_PADRAO)


def fortes_do_contrato_de_dados(raiz) -> tuple:
    """A prioridade dos identificadores fortes vem do Data Contract V1.0, nao do codigo."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    return tuple(contrato["dedup"]["strong"])


# ---------------------------------------------------------------------------------------
# Normalizacao e validacao
# ---------------------------------------------------------------------------------------
def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def declarado(candidata: dict, campo: str) -> bool:
    return _texto(candidata.get(campo)) != ""


def normalizar_cnpj(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def cnpj_valido(valor: str) -> bool:
    """Digito verificador do CNPJ (modulo 11) — identificador forte invalido e descartado."""
    digitos = normalizar_cnpj(valor)
    if len(digitos) != 14 or digitos == digitos[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(d) * p for d, p in zip(digitos[:pos], pesos))
        resto = soma % 11
        esperado = "0" if resto < 2 else str(11 - resto)
        if digitos[pos] != esperado:
            return False
    return True


def normalizar_domain(valor: str) -> str:
    host = _texto(valor).lower()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = host.split("/")[0].split("?")[0]
    host = host.split("@")[-1]
    host = re.sub(r":\d+$", "", host)
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_valido(valor: str) -> bool:
    host = normalizar_domain(valor)
    if not host or " " in host or "." not in host:
        return False
    rotulos = host.split(".")
    if len(rotulos) < 2:
        return False
    return all(re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", r) for r in rotulos if r)


def normalizar_linkedin(valor: str) -> str:
    texto = _texto(valor).lower()
    texto = re.sub(r"^[a-z][a-z0-9+.-]*://", "", texto)
    texto = texto.split("?")[0].rstrip("/")
    m = re.search(r"linkedin\.com/(?:company|school|showcase)/([a-z0-9\-\._%]+)", texto)
    if not m:
        return ""
    return "https://www.linkedin.com/company/" + m.group(1)


def linkedin_valido(valor: str) -> bool:
    return normalizar_linkedin(valor) != ""


def identificadores_validos(candidata: dict, fortes: tuple) -> list:
    """Lista [(tipo, valor_normalizado)] na ordem de prioridade do contrato; invalido some."""
    validadores = {
        "cnpj": (cnpj_valido, normalizar_cnpj),
        "domain": (domain_valido, normalizar_domain),
        "linkedin_url": (linkedin_valido, normalizar_linkedin),
    }
    saida = []
    for tipo in fortes:
        bruto = _texto(candidata.get(tipo))
        if not bruto:
            continue
        valida, normaliza = validadores[tipo]
        if valida(bruto):
            valor = normaliza(bruto)
            if all(valor != v for _, v in saida):
                saida.append((tipo, valor))
    return saida


def identificadores_declarados(candidata: dict, fortes: tuple) -> list:
    return [t for t in fortes if _texto(candidata.get(t))]


def chave_idempotencia(tipo: str, valor: str) -> str:
    return "scout:org:%s:%s" % (tipo, valor)


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def validar_candidata(candidata: dict, fortes: tuple) -> dict:
    """Validacao fora do banco: nome, fonte e identificadores. Nao decide veredito."""
    problemas = []
    nome = _texto(candidata.get("legal_name")) or _texto(candidata.get("trade_name"))
    if not nome:
        problemas.append("SEM_NOME")
    fonte = _texto(candidata.get("source")).upper()
    if not fonte:
        problemas.append("SEM_FONTE")
    elif fonte not in FONTES:
        problemas.append("FONTE_DESCONHECIDA")
    declarados = identificadores_declarados(candidata, fortes)
    validos = identificadores_validos(candidata, fortes)
    if problemas:
        return {"nome": nome, "fonte": fonte, "declarados": declarados,
                "validos": validos, "problemas": problemas}
    if declarados and not validos:
        return {"nome": nome, "fonte": fonte, "declarados": declarados,
                "validos": validos, "problemas": ["IDENTIFICADOR_FORTE_INVALIDO"]}
    return {"nome": nome, "fonte": fonte, "declarados": declarados,
            "validos": validos, "problemas": []}


def decidir_veredito(declarados: list, validos: list, organizacoes_casadas: list) -> tuple:
    """Decisao pura (testavel sem banco) — a regra vive aqui e so aqui."""
    if not validos:
        if declarados:
            return VER_RECUSADA, "IDENTIFICADOR_FORTE_INVALIDO"
        return VER_REVISAO, "SEM_IDENTIFICADOR_FORTE"
    if len(organizacoes_casadas) == 0:
        return VER_CRIADA, None
    if len(organizacoes_casadas) == 1:
        return VER_JA_EXISTE, None
    return VER_REVISAO, "CONFLITO_DE_IDENTIDADE_FORTE"


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o agente pode e nao pode escrever)
# ---------------------------------------------------------------------------------------
def lit(valor) -> str:
    if valor is None:
        return "NULL"
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, float)):
        return repr(valor)
    return "'" + str(valor).replace("'", "''") + "'"


def lit_json(objeto) -> str:
    return lit(json.dumps(objeto, ensure_ascii=False, sort_keys=True)) + "::jsonb"


_ESCRITA = (
    ("insert", re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][\w\.]*)", re.I)),
    ("update", re.compile(r"\bUPDATE\s+([A-Za-z_][\w\.]*)", re.I)),
    ("delete", re.compile(r"\bDELETE\s+FROM\s+([A-Za-z_][\w\.]*)", re.I)),
)
_DDL = re.compile(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT\s+ON)\b", re.I)


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL e escrita fora do declarado (as 4 tabelas do agente)."""
    if _DDL.search(sql):
        raise GuardaDeEscritaViolada("DDL nao e permitido ao agente Scout")
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(sql):
            if tabela.lower() not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela nao declarada: %s" % tabela)
            if operacao in ("update", "delete") and tabela == TABELA_ORGANIZACOES:
                permitido = permitir_remocao and operacao == "delete"
                if not permitido:
                    raise GuardaDeEscritaViolada(
                        "%s em organizations nao e permitido (o Scout so insere; desfazer e "
                        "caminho explicito com --confirmo)" % operacao.upper())
            if operacao == "delete" and tabela != TABELA_ORGANIZACOES and not permitir_remocao:
                raise GuardaDeEscritaViolada("DELETE fora do desfazer explicito: %s" % tabela)


# ---------------------------------------------------------------------------------------
# Porta de banco (ADR-0008: o SQL roda na VPS; a porta e o prefixo psql)
# ---------------------------------------------------------------------------------------
class PortaSQL:
    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        raise NotImplementedError


class PortaAusente(PortaSQL):
    """Sem porta configurada: qualquer tentativa de falar com o banco RECUSA."""

    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        raise PortaIndisponivel(
            "nenhuma porta de banco configurada (use --prefixo ou TRE_PSQL_PREFIXO)")


class PortaPsql(PortaSQL):
    """Executa SQL pelo prefixo psql informado (ex.: 'docker exec -i pg-sales-dev psql -U ..')."""

    def __init__(self, prefixo: str, timeout: int = 120):
        if not prefixo:
            raise PortaIndisponivel("prefixo psql vazio (use --prefixo ou TRE_PSQL_PREFIXO)")
        self.prefixo = shlex.split(prefixo)
        self.timeout = timeout

    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        validar_sql(sql, permitir_remocao=permitir_remocao)
        comando = self.prefixo + ["-v", "ON_ERROR_STOP=1", "-tA", "-F", "|"]
        try:
            p = subprocess.run(comando, input=sql, capture_output=True, text=True,
                               timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PortaIndisponivel("porta psql falhou: %s" % exc)
        return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------------------
# SQL do agente
# ---------------------------------------------------------------------------------------
_COLUNAS_ORGANIZACAO = (
    "id, legal_name, trade_name, domain, website_url, linkedin_url, cnpj, industry_code, "
    "industry_name, employee_count, employee_band, revenue_estimate, unit_count, city, state, "
    "country_code, business_model, status, source, created_at, updated_at"
)


def sql_consultar_identidade(validos: list) -> str:
    termos = []
    for tipo, valor in validos:
        termos.append("(%s IS NOT NULL AND %s = %s)" % (tipo, tipo, lit(valor)))
    return (
        "SELECT id::text, COALESCE(cnpj, ''), COALESCE(domain, ''), COALESCE(linkedin_url, ''), "
        "COALESCE(legal_name, ''), COALESCE(trade_name, '') "
        "FROM %s WHERE deleted_at IS NULL AND (%s) ORDER BY id;" % (
            TABELA_ORGANIZACOES, " OR ".join(termos))
    )


def _faixa_de_empregados(quantidade):
    if quantidade is None:
        return None
    try:
        n = int(quantidade)
    except (TypeError, ValueError):
        return None
    limites = ((70, "LT_70"), (150, "70_149"), (300, "150_299"), (500, "300_499"),
               (700, "500_699"), (1000, "700_1000"))
    for limite, faixa in limites:
        if n < limite:
            return faixa
    return "GT_1000"


def montar_linha_organizacao(candidata: dict, identificacao: dict) -> dict:
    """Campos que o Scout escreve. O que ele nao sabe fica NULL — nada de dado inventado."""
    valores = {
        "legal_name": _texto(candidata.get("legal_name")) or None,
        "trade_name": _texto(candidata.get("trade_name")) or None,
        "cnpj": None,
        "domain": None,
        "linkedin_url": None,
        "website_url": _texto(candidata.get("website_url")) or None,
        "industry_code": _texto(candidata.get("industry_code")) or None,
        "industry_name": _texto(candidata.get("industry_name")) or None,
        "employee_count": None,
        "employee_band": None,
        "revenue_estimate": None,
        "unit_count": None,
        "city": _texto(candidata.get("city")) or None,
        "state": _texto(candidata.get("state")) or None,
        "country_code": (_texto(candidata.get("country_code")) or "BR").upper()[:2],
        "business_model": _texto(candidata.get("business_model")) or None,
        "status": STATUS_ORGANIZACAO,
        "source": identificacao["fonte"],
    }
    for tipo, valor in identificacao["validos"]:
        valores[tipo] = valor
    for campo in ("employee_count", "unit_count"):
        bruto = candidata.get(campo)
        if bruto not in (None, ""):
            try:
                valores[campo] = int(bruto)
            except (TypeError, ValueError):
                valores[campo] = None
    if valores["employee_count"] is not None:
        valores["employee_band"] = _faixa_de_empregados(valores["employee_count"])
    try:
        if candidata.get("revenue_estimate") not in (None, ""):
            valores["revenue_estimate"] = float(candidata["revenue_estimate"])
    except (TypeError, ValueError):
        valores["revenue_estimate"] = None
    return valores


def sql_ingerir(organizacao_id: str, sync_event_id: str, chave: str, valores: dict,
                evidencia: dict) -> str:
    """Uma unica instrucao: claim da chave de idempotencia -> insere a organizacao -> fecha o evento.

    Se a chave ja existe, `claim` volta vazia, `org` nao insere nada e o UPDATE fecha 0 linhas:
    a saida vazia E a prova de que nada foi duplicado (retry nao cria duplicata).
    """
    colunas = [c.strip() for c in _COLUNAS_ORGANIZACAO.split(",")]
    expressoes = [lit(organizacao_id)] + [lit(valores.get(c)) for c in colunas[1:-2]] + \
                 [lit(valores["status"]), lit(valores["source"]), "now()", "now()"]
    lista_colunas = ", ".join(colunas)
    lista_valores = ", ".join(expressoes)
    payload = dict(evidencia)
    payload["idempotency_key"] = chave
    return (
        "BEGIN;\n"
        "WITH claim AS (\n"
        "  INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, created_at)\n"
        "  VALUES ({sevid}, 'organization', {orgid}, 'scout', 'postgresql', 'INSERT', {versao}, "
        "{chave}, 'PENDING', {payload}, now())\n"
        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        "), org AS (\n"
        "  INSERT INTO {orgs} ({cols})\n"
        "  SELECT {vals} FROM claim\n"
        "  RETURNING id\n"
        ")\n"
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(),\n"
        "  response_payload = jsonb_build_object('organization_id', {orgid}, "
        "'veredito', 'CRIADA', 'idempotency_key', {chave})\n"
        "WHERE id = (SELECT id FROM claim) AND EXISTS (SELECT 1 FROM org)\n"
        "RETURNING status;\n"
        "COMMIT;\n"
    ).format(sync=TABELA_SYNC_EVENTS, orgs=TABELA_ORGANIZACOES, sevid=lit(sync_event_id),
             orgid=lit(organizacao_id), versao=lit(VERSAO), chave=lit(chave),
             payload=lit_json(payload), cols=lista_colunas, vals=lista_valores)


def sql_registrar_execucao(run_id: str, correlation_id: str, organizacao_id, status: str,
                           entrada: dict, saida: dict, iniciado: str, terminado: str,
                           erro=None) -> str:
    return (
        "INSERT INTO {tabela} (id, agent_name, agent_role, agent_version, workflow, "
        "workflow_version, organization_id, triggered_by, correlation_id, input, output, model, "
        "started_at, finished_at, status, error, created_at)\n"
        "VALUES ({rid}, {agente}, {papel}, {versao}, {workflow}, {wfversao}, {orgid}, {gatilho}, "
        "{corr}, {entrada}, {saida}, NULL, {ini}, {fim}, {status}, {erro}, now());\n"
    ).format(tabela=TABELA_AGENT_RUNS, rid=lit(run_id), agente=lit(AGENTE), papel=lit(PAPEL),
             versao=lit(VERSAO), workflow=lit(WORKFLOW), wfversao=lit(WORKFLOW_VERSAO),
             orgid=lit(organizacao_id), gatilho=lit("cli"), corr=lit(correlation_id),
             entrada=lit_json(entrada), saida=lit_json(saida), ini=lit(iniciado), fim=lit(terminado),
             status=lit(status), erro=lit_json(erro) if erro else "NULL")


def sql_pedir_revisao(approval_id: str, candidata: dict, motivo: str, casadas: list,
                      correlation_id: str) -> str:
    proposta = {
        "motivo": motivo,
        "agente": "%s/%s" % (AGENTE, VERSAO),
        "correlation_id": correlation_id,
        "candidata": candidata,
        "organizacoes_casadas": casadas,
        "regra": "ambiguidade nao e resolvida por heuristica: e reportada (dedup.rule do contrato)",
    }
    return (
        "INSERT INTO {tabela} (id, action_type, entity_type, entity_id, requested_by, "
        "proposed_action, status, requested_at)\n"
        "VALUES ({aid}, {acao}, 'organization', NULL, {quem}, {proposta}, 'PENDING', now());\n"
    ).format(tabela=TABELA_APPROVALS, aid=lit(approval_id), acao=lit(ACTION_TYPE_REVISAO),
             quem=lit("%s/%s" % (AGENTE, VERSAO)), proposta=lit_json(proposta))


def sql_selecionar_criadas(correlation_id: str) -> str:
    return (
        "SELECT o.id::text FROM {orgs} o\n"
        "JOIN {runs} a ON (a.output ->> 'organization_id') = o.id::text\n"
        "WHERE a.correlation_id = {corr} AND a.agent_name = {agente}\n"
        "  AND a.output ->> 'veredito' = 'CRIADA' AND o.status = {status};\n"
    ).format(orgs=TABELA_ORGANIZACOES, runs=TABELA_AGENT_RUNS, corr=lit(correlation_id),
             agente=lit(AGENTE), status=lit(STATUS_ORGANIZACAO))


def sql_desfazer(ids: list, correlation_id: str, sync_event_id: str) -> str:
    lista = ", ".join(lit(i) for i in ids)
    payload = {"motivo": "desfazer da rodada", "correlation_id": correlation_id}
    return (
        "BEGIN;\n"
        "DELETE FROM {sync} WHERE entity_type = 'organization' AND entity_id IN ({ids}) "
        "AND operation = 'INSERT';\n"
        "DELETE FROM {orgs} WHERE id IN ({ids}) AND status = {status};\n"
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, operation, "
        "source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'organization', NULL, 'scout', 'postgresql', 'ROLLBACK', {versao}, "
        "{chave}, 'SUCCESS', {payload}, now());\n"
        "COMMIT;\n"
    ).format(sync=TABELA_SYNC_EVENTS, orgs=TABELA_ORGANIZACOES, ids=lista,
             status=lit(STATUS_ORGANIZACAO), sevid=lit(sync_event_id), versao=lit(VERSAO),
             chave=lit("scout:rollback:%s" % correlation_id), payload=lit_json(payload))


# ---------------------------------------------------------------------------------------
# Gate do JEV (doc 07 §7: antes de chamada de LLM em tarefa elegivel, o Hermes consulta o JEV)
# ---------------------------------------------------------------------------------------
def validar_recibo_jev(recibo) -> dict:
    if not recibo:
        raise ReciboJEVInvalido("sem recibo de decisao do JEV: abstencao e recusa, nao permissao")
    if not isinstance(recibo, dict):
        raise ReciboJEVInvalido("recibo do JEV ilegivel")
    faltando = [c for c in ("decision_id", "lane", "outcome") if not recibo.get(c)]
    if faltando:
        raise ReciboJEVInvalido("recibo do JEV incompleto: %s" % ", ".join(faltando))
    if recibo["outcome"] != JEV_EXECUTAR:
        raise ReciboJEVInvalido("recibo do JEV com outcome %r (exige %r)"
                                % (recibo["outcome"], JEV_EXECUTAR))
    if recibo["lane"] not in LANES:
        raise ReciboJEVInvalido("lane fora do vocabulario: %r" % recibo["lane"])
    return recibo


def chamar_llm(prompt: str, recibo=None, provedor=None, modelo=None) -> dict:
    """v1 NAO executa LLM — o gate existe e e fail-closed. Sem recibo valido, nao passa."""
    recibo = validar_recibo_jev(recibo)
    return {
        "autorizado": True,
        "decision_id": recibo["decision_id"],
        "lane": recibo["lane"],
        "provedor": provedor,
        "modelo": modelo,
        "prompt_sha256": _sha256(prompt),
        "executado": False,
        "motivo": "v1 do Scout e deterministica: nenhuma chamada de LLM e feita",
    }


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------------------
class Scout:
    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None):
        self.raiz = Path(raiz or RAIZ_PADRAO)
        self.contrato = carregar_contrato_do_agente(self.raiz)
        self.fortes = fortes_do_contrato_de_dados(self.raiz)
        if list(self.fortes) != list(self.contrato["identidade"]["fortes_por_prioridade"]):
            raise ValueError("divergencia entre o contrato do agente e o Data Contract V1.0: %s x %s"
                             % (list(self.fortes), self.contrato["identidade"]["fortes_por_prioridade"]))
        self.porta = porta if porta is not None else PortaAusente()
        self.relogio = relogio
        self.ambiente = ambiente
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.alvo = None

    def porta_sql(self):
        """A porta responde sempre; sem prefixo configurado ela RECUSA (fail-closed)."""
        return self.porta

    # -- ambiente ---------------------------------------------------------------------
    def conferir_ambiente(self) -> str:
        if self.ambiente in (None, ""):
            raise RecusaDeAmbiente("ambiente nao declarado: recusa (fail-closed)")
        if self.ambiente == AMBIENTE_RECUSADO:
            raise RecusaDeAmbiente(
                "Scout v1 nao escreve em prod (ADR-005): a promocao exige card proprio com "
                "aprovacao humana registrada")
        if self.ambiente not in AMBIENTES_PERMITIDOS:
            raise RecusaDeAmbiente("ambiente desconhecido: %r" % self.ambiente)
        return self.ambiente

    def identificar_alvo(self) -> dict:
        """Registra a identidade do alvo medido (sem afirmar nome de ambiente — a convencao
        de nome divergiu no dev e alinhar isso e decisao do dono, nao do agente)."""
        rc, saida, erro = self.porta.executar(
            "SELECT current_database(), current_user, version();")
        if rc != 0:
            raise PortaIndisponivel("nao consegui ler a identidade do alvo: %s" % (erro or saida))
        campos = saida.strip().split("|")
        self.alvo = {"banco": campos[0] if campos else None,
                     "usuario": campos[1] if len(campos) > 1 else None,
                     "servidor": (campos[2] if len(campos) > 2 else "")[:80]}
        return self.alvo

    # -- candidata --------------------------------------------------------------------
    def organizacoes_casadas(self, validos: list) -> list:
        if not validos:
            return []
        rc, saida, erro = self.porta.executar(sql_consultar_identidade(validos))
        if rc != 0:
            raise PortaIndisponivel("consulta de identidade falhou: %s" % (erro or saida))
        casadas = []
        for linha in saida.splitlines():
            if not linha.strip():
                continue
            campos = linha.split("|")
            if len(campos) < 4:
                continue
            org_id = campos[0].strip()
            colunas = {"cnpj": campos[1], "domain": campos[2], "linkedin_url": campos[3]}
            for tipo, valor in validos:
                bruto = (colunas[tipo] or "").strip()
                if not bruto:
                    continue
                if tipo == "cnpj" and normalizar_cnpj(bruto) == valor:
                    casadas.append({"organization_id": org_id, "tipo": tipo, "valor": valor})
                    break
                if tipo == "domain" and normalizar_domain(bruto) == valor:
                    casadas.append({"organization_id": org_id, "tipo": tipo, "valor": valor})
                    break
                if tipo == "linkedin_url" and normalizar_linkedin(bruto) == valor:
                    casadas.append({"organization_id": org_id, "tipo": tipo, "valor": valor})
                    break
        return casadas

    def planejar(self, candidata: dict) -> dict:
        """Modo sem banco: valida, normaliza e diz o que FARIA."""
        identificacao = validar_candidata(candidata, self.fortes)
        if identificacao["problemas"]:
            veredito = PLANEJADO_RECUSAR
        elif not identificacao["validos"]:
            veredito = PLANEJADO_REVISAR
        else:
            veredito = PLANEJADO_CRIAR
        chave = None
        if identificacao["validos"]:
            tipo, valor = identificacao["validos"][0]
            chave = chave_idempotencia(tipo, valor)
        return {"veredito": veredito, "motivos": identificacao["problemas"],
                "identidades_validas": [{"tipo": t, "valor": v} for t, v in identificacao["validos"]],
                "identidades_declaradas": identificacao["declarados"],
                "idempotency_key": chave, "nome": identificacao["nome"],
                "fonte": identificacao["fonte"]}

    def processar(self, candidata: dict) -> dict:
        inicio = self.relogio()
        entrada = {"candidata": candidata, "ambiente": self.ambiente,
                   "correlation_id": self.correlation_id}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": VER_ERRO, "motivos": [], "organization_id": None,
                     "idempotency_key": None, "identidades_validas": [],
                     "idempotency_key_ingerida": None}
        try:
            identificacao = validar_candidata(candidata, self.fortes)
            validos = identificacao["validos"]
            resultado["identidades_validas"] = [{"tipo": t, "valor": v} for t, v in validos]
            if validos:
                tipo, valor = validos[0]
                resultado["idempotency_key"] = chave_idempotencia(tipo, valor)
            casadas = self.organizacoes_casadas(validos) if validos else []
            ids_casados = sorted({c["organization_id"] for c in casadas})
            veredito, motivo = decidir_veredito(identificacao["declarados"], validos, ids_casados)
            resultado["veredito"] = veredito
            resultado["motivos"] = identificacao["problemas"] or ([motivo] if motivo else [])
            resultado["organizacoes_casadas"] = casadas
            if veredito == VER_RECUSADA:
                pass
            elif veredito == VER_REVISAO:
                aprovacao_id = str(uuid.uuid4())
                self.porta_sql().executar(sql_pedir_revisao(aprovacao_id, candidata,
                                                      resultado["motivos"][0], casadas,
                                                      self.correlation_id))
                resultado["human_approval_id"] = aprovacao_id
            elif veredito == VER_JA_EXISTE:
                resultado["organization_id"] = casadas[0]["organization_id"]
            elif veredito == VER_CRIADA:
                organizacao_id = str(uuid.uuid4())
                sync_event_id = str(uuid.uuid4())
                valores = montar_linha_organizacao(candidata, identificacao)
                evidencia = {"origem": "scout", "agente": "%s/%s" % (AGENTE, VERSAO),
                             "correlation_id": self.correlation_id,
                             "candidata": candidata,
                             "identidade": resultado["idempotency_key"]}
                rc, saida, erro = self.porta.executar(
                    sql_ingerir(organizacao_id, sync_event_id, resultado["idempotency_key"],
                                valores, evidencia))
                if rc != 0:
                    raise PortaIndisponivel("ingestao falhou: %s" % (erro or saida))
                if saida.strip() == "SUCCESS":
                    resultado["organization_id"] = organizacao_id
                    resultado["sync_event_id"] = sync_event_id
                else:
                    # a chave ja existia: nada foi duplicado (retry idempotente)
                    resultado["veredito"] = VER_JA_EXISTE
                    resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]
                    resultado["idempotency_key_ingerida"] = resultado["idempotency_key"]
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = VER_ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        saida = {k: v for k, v in resultado.items() if k != "organizacoes_casadas"}
        status = STATUS_AGENT_RUNS[resultado["veredito"]]
        self.porta_sql().executar(sql_registrar_execucao(
            run_id, self.correlation_id, resultado.get("organization_id"), status,
            entrada, saida, inicio, fim, resultado.get("erro")))
        resultado["agent_run_id"] = run_id
        resultado["status_agent_runs"] = status
        return resultado

    def rodar(self, candidatas: list, planejar: bool = False) -> dict:
        resultados = []
        for candidata in candidatas:
            if planejar:
                resultados.append(self.planejar(candidata))
            else:
                resultados.append(self.processar(candidata))
        resumo = {"agente": "%s/%s" % (AGENTE, VERSAO), "ambiente": self.ambiente,
                  "correlation_id": self.correlation_id, "alvo": self.alvo,
                  "planejar": planejar, "total": len(resultados),
                  "por_veredito": {}, "resultados": resultados}
        for r in resultados:
            resumo["por_veredito"][r["veredito"]] = resumo["por_veredito"].get(r["veredito"], 0) + 1
        return resumo

    def desfazer(self, correlation_id: str, confirmo: bool = False) -> dict:
        rc, saida, erro = self.porta.executar(sql_selecionar_criadas(correlation_id))
        if rc != 0:
            raise PortaIndisponivel("nao consegui listar as organizacoes da rodada: %s"
                                    % (erro or saida))
        ids = [l.strip() for l in saida.splitlines() if l.strip()]
        if not confirmo:
            return {"correlation_id": correlation_id, "confirmo": False, "organizacoes": ids,
                    "apagadas": 0, "dry_run": True}
        if ids:
            rc, saida, erro = self.porta.executar(
                sql_desfazer(ids, correlation_id, str(uuid.uuid4())), permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
        return {"correlation_id": correlation_id, "confirmo": True, "organizacoes": ids,
                "apagadas": len(ids), "dry_run": False}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_candidatas(caminho: str) -> list:
    if caminho == "-":
        texto = sys.stdin.read()
    else:
        p = Path(caminho)
        if not p.is_file():
            raise SystemExit(EXIT_FONTE)
        texto = p.read_text(encoding="utf-8")
    candidatas = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        try:
            candidatas.append(json.loads(linha))
        except json.JSONDecodeError as exc:
            raise SystemExit("FALHOU linha %d da fonte nao e JSON: %s" % (numero, exc))
    return candidatas


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agente Scout v1 (TRE-W4-E01-T01)")
    p.add_argument("--fonte", help="arquivo jsonl com as empresas candidatas ('-' = stdin)")
    p.add_argument("--ambiente", help="dev | homolog (prod e recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true", help="valida e planeja; NAO toca o banco")
    p.add_argument("--relatorio", help="caminho do relatorio JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrao e dry-run)")
    p.add_argument("--raiz", default=str(RAIZ_PADRAO), help="raiz do repo (contratos)")
    return p


def main(argv=None) -> int:
    args = montar_parser().parse_args(argv)
    if not args.fonte and not args.desfazer:
        print("uso: --fonte <arquivo.jsonl> [--ambiente <amb>] [--planejar] | --desfazer <cid>")
        return EXIT_USO
    porta = None
    if not args.planejar:
        try:
            porta = PortaPsql(args.prefixo)
        except PortaIndisponivel as exc:
            print("FALHOU %s" % exc)
            return EXIT_USO
    agente = Scout(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                   correlation_id=args.correlation_id)
    # O ambiente e exigido em todo modo que ESCREVE. No modo de planejamento (que nao abre
    # conexao nenhuma) ele e opcional: se declarado, e conferido; se ausente, nada e escrito.
    if args.ambiente or not args.planejar:
        try:
            agente.conferir_ambiente()
        except RecusaDeAmbiente as exc:
            print("RECUSADO_AMBIENTE %s" % exc)
            return EXIT_RECUSOU_AMBIENTE
    try:
        if args.desfazer:
            agente.identificar_alvo()
            relatorio = agente.desfazer(args.desfazer, confirmo=args.confirmo)
        else:
            candidatas = ler_candidatas(args.fonte)
            if args.planejar:
                relatorio = agente.rodar(candidatas, planejar=True)
            else:
                agente.identificar_alvo()
                relatorio = agente.rodar(candidatas)
    except PortaIndisponivel as exc:
        print("FALHOU %s" % exc)
        return EXIT_FALHOU
    if args.relatorio:
        Path(args.relatorio).write_text(json.dumps(relatorio, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({"agente": relatorio.get("agente"), "ambiente": relatorio.get("ambiente"),
                      "correlation_id": relatorio["correlation_id"],
                      "alvo": relatorio.get("alvo"), "total": relatorio.get("total"),
                      "por_veredito": relatorio.get("por_veredito"),
                      "apagadas": relatorio.get("apagadas"), "dry_run": relatorio.get("dry_run")},
                     ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-20s %-45s %s" % (r["veredito"], (r.get("nome") or "-")[:45],
                                    ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == VER_ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
