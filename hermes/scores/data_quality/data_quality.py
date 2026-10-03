#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score Data Quality v1 — nota de confiabilidade e completude do dado (card TRE-W5-E04-T01).

O que este modulo FAZ (e so isto): le as organizacoes do ambiente, MEDE o dado que ja existe
(completude, validade de formato, lastro de fonte e atualidade), grava o historico em
`sales_intelligence.scores` (`score_type = 'DATA_QUALITY'`, `score_version = 'v1.0'`) e espelha
o valor ATUAL em `organizations.data_quality_score`.

O que ele NAO faz, por desenho (declarado em `score-data-quality-v1.json` -> lacunas):
  - nao cria organizacao (produtor da empresa e o Scout, TRE-W4-E01-T01);
  - nao enriquece, nao corrige e nao normaliza o dado que mede: em `organizations` ele escreve
    UMA coluna (o espelho) e nada mais — nem `updated_at`;
  - nao calcula ICQ/Automation Fit/Buying Signal/Priority nem tier (W5-E01/E02/E03/E05/E06);
  - nao emite evento de outbox (COMPANY_QUALIFIED nasce na qualificacao, doc 06 §5);
  - nao abre fila humana: identidade ambigua e do dedup/Scout (o score nao decide quem e a empresa);
  - nao faz requisicao de rede, nao chama LLM e nao escreve em producao (ADR-005).

Por que a nota e REPRODUZIVEL: `valor = f(estado do banco, data de referencia da rodada)`. Os
inputs medidos + a referencia viram `inputs_sha256`; o mesmo estado com a mesma referencia produz o
mesmo valor e o mesmo hash — e o replay NAO duplica (a idempotencia vive no SQL, nao na prosa).
Referencia de outro dia e uma MEDICAO nova (o historico e append-only, nunca reescrito).

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/scores/data_quality/data_quality.py --planejar --todas \
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
  python3 hermes/scores/data_quality/data_quality.py --ambiente dev --todas \
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
      --relatorio /tmp/data-quality-rodada.json
  python3 hermes/scores/data_quality/data_quality.py --ambiente dev --desfazer <correlation_id>
  python3 hermes/scores/data_quality/data_quality.py --ambiente dev --desfazer <correlation_id> --confirmo
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
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do score (espelha `hermes/scores/data_quality/score-data-quality-v1.json`)
# ---------------------------------------------------------------------------------------
SCORE = "data_quality"
PAPEL = "scoring"
VERSAO = "1.0.0"
SCORE_TIPO = "DATA_QUALITY"
SCORE_VERSAO = "v1.0"
WORKFLOW = "score-data-quality"
WORKFLOW_VERSAO = "v1"

TABELA_SCORES = "sales_intelligence.scores"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_RESEARCH_RUNS = "sales_intelligence.research_runs"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"

TABELAS_PERMITIDAS = (TABELA_SCORES, TABELA_ORGANIZACOES, TABELA_AGENT_RUNS)
TABELAS_LIDAS = (TABELA_ORGANIZACOES, TABELA_RESEARCH_RUNS, TABELA_SCORES)

# Colunas que este score pode escrever. `colunas_organizations_escritas` e a lista de UMA coluna
# de proposito: o score mede o dado, nao conserta nem enriquece (e `updated_at` fora da lista
# impede o score de rejuvenescer a propria componente de atualidade).
COLUNAS_SCORES = ("id", "organization_id", "score_type", "score_value", "score_version",
                  "inputs", "explanation", "calculated_at")
COLUNAS_SCORES_OBRIGATORIAS = ("organization_id", "score_type", "score_value", "score_version")
COLUNAS_ORGANIZACOES_ESCRITAS = ("data_quality_score",)
COLUNAS_AGENT_RUNS = ("id", "agent_name", "agent_role", "agent_version", "workflow",
                      "workflow_version", "organization_id", "triggered_by", "correlation_id",
                      "input", "output", "started_at", "finished_at", "status", "created_at")

# Modelo v1 — os pesos/parametros do score. Espelhados no JSON do score (a suite confere que os
# dois nao divergiram): mudar peso aqui exige VERSAO NOVA do score (v1.1), nunca edicao silenciosa.
COMPONENTES = (("completude", Decimal("0.45")), ("validade", Decimal("0.20")),
               ("confiabilidade", Decimal("0.25")), ("atualidade", Decimal("0.10")))
PESOS = dict(COMPONENTES)
COMPLETUDE_CAMPOS = (
    ("cnpj", Decimal("0.15")),
    ("domain", Decimal("0.15")),
    ("linkedin_url", Decimal("0.10")),
    ("industria", Decimal("0.10")),
    ("porte", Decimal("0.10")),
    ("revenue_estimate", Decimal("0.10")),
    ("city", Decimal("0.10")),
    ("state", Decimal("0.05")),
    ("website_url", Decimal("0.05")),
    ("unit_count", Decimal("0.05")),
    ("business_model", Decimal("0.05")),
)
CONFIABILIDADE_PARCELAS = (("fonte_declarada", Decimal("0.40")),
                           ("pesquisa_existente", Decimal("0.35")),
                           ("cobertura_de_fontes", Decimal("0.25")))
PESOS_CONFIABILIDADE = dict(CONFIABILIDADE_PARCELAS)
FONTES_PARA_COBERTURA_MAXIMA = 3
ATUALIDADE_FRESCO_DIAS = 90
ATUALIDADE_EXPIRADO_DIAS = 365

# Vocabulario de fontes do contrato (a fonte fora do vocabulario nao ganha credito de confiabilidade)
FONTES = (
    "LINKEDIN", "WEB", "GOOGLE", "META", "WHATSAPP", "TITAN", "EVENTOS", "DADOS_PUBLICOS",
)

# Vocabulario do recibo do JEV (hermes/jev/routing/router.py: OUTCOME_*)
JEV_EXECUTAR = "PASS"
JEV_ESCALAR = "ESCALATE"
JEV_BLOQUEAR = "BLOCK"
LANES = ("small", "medium", "high", "critical")

VER_ESCRITO = "ESCRITO"
VER_JA_EXISTE = "JA_EXISTE"
VER_RECUSADA = "RECUSADA"
VER_ERRO = "ERRO"
VEREDITOS = (VER_ESCRITO, VER_JA_EXISTE, VER_RECUSADA, VER_ERRO)
STATUS_AGENT_RUNS = {
    VER_ESCRITO: "COMPLETED",
    VER_JA_EXISTE: "COMPLETED",
    VER_RECUSADA: "REJECTED",
    VER_ERRO: "FAILED",
}

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4
EXIT_FONTE = 5

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_SCORE_PADRAO = "hermes/scores/data_quality/score-data-quality-v1.json"

# A regra de identidade e UMA so no repo (a do produtor da empresa). Copia local seria divergencia
# esperando acontecer — a suite reprova se aparecer uma segunda implementacao aqui.
MODULO_IDENTIDADE = "hermes/agents/scout/scout.py"

_UUID_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_NUMERO_2_CASAS = re.compile(r"^\d{1,3}\.\d{2}$")
_UUID_SIMPLES = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR — nunca pela profundidade do arquivo.

    A copia mutada (autoteste) vive em diretorio raso (as vezes `/tmp`), onde
    `Path(__file__).resolve().parents[3]` estoura `IndexError` ja na importacao. A raiz e o
    primeiro ancestral que contem o contrato DESTE score; o diretorio de trabalho entra como
    segunda tentativa, porque a copia sob teste nao esta na arvore do repo.
    """
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_SCORE_PADRAO).is_file():
                return pasta
    return Path.cwd()


RAIZ_PADRAO = descobrir_raiz_padrao()


class RecusaDeAmbiente(Exception):
    """Ambiente nao permitido (ADR-005): nada e escrito."""


class ReciboJEVInvalido(Exception):
    """Chamada de LLM sem recibo valido do JEV: fail-closed (doc 07 §7)."""


class GuardaDeEscritaViolada(Exception):
    """SQL tentando escrever fora das tabelas/colunas/operacoes declaradas."""


class PortaIndisponivel(Exception):
    """A porta de banco falhou."""


# ---------------------------------------------------------------------------------------
# Contratos e regra de identidade importada do produtor
# ---------------------------------------------------------------------------------------
def carregar_json(caminho) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_contrato_do_score(raiz) -> dict:
    return carregar_json(Path(raiz) / CONTRATO_SCORE_PADRAO)


def fortes_do_contrato_de_dados(raiz) -> tuple:
    """A prioridade dos identificadores fortes vem do Data Contract V1.0, nao do codigo."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    return tuple(contrato["dedup"]["strong"])


def carregar_identidade(raiz):
    """Carrega a regra de identidade do Scout (o produtor da empresa) por caminho.

    Import tardio de proposito: o modulo e localizado pela raiz do repo, entao a copia do score
    sob teste em diretorio raso continua importavel.
    """
    caminho = Path(raiz) / MODULO_IDENTIDADE
    if not caminho.is_file():
        raise ValueError("modulo de identidade do produtor ausente: %s" % caminho)
    spec = importlib.util.spec_from_file_location("scout_identidade", str(caminho))
    if spec is None or spec.loader is None:
        raise ValueError("nao consegui carregar o modulo de identidade: %s" % caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------------------------------
# Normalizacao e leitura do dado medido
# ---------------------------------------------------------------------------------------
def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def referencia_padrao() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def campo_presente(valor) -> bool:
    """Presente = existe e nao e vazio. Zero NUMERICO e dado presente (nao se confunde com ausencia)."""
    if valor is None:
        return False
    if isinstance(valor, str):
        return bool(valor.strip())
    return True


def _decimal(valor) -> Decimal:
    return Decimal(str(valor))


def _quantizar(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _iso(valor) -> str:
    return _texto(valor)


def _data_do_texto(valor):
    texto = _iso(valor)
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------
# Modelo v1: componentes
# ---------------------------------------------------------------------------------------
def componente_completude(organizacao: dict) -> tuple:
    """Proporcao PONDERADA dos campos declarados que estao preenchidos.

    Campo composto (`industria`, `porte`) conta uma vez: basta um dos dois nomes existir.
    Ausente = NULL / string vazia / so espacos. Zero numerico conta como dado presente.
    """
    valores = {
        "cnpj": organizacao.get("cnpj"),
        "domain": organizacao.get("domain"),
        "linkedin_url": organizacao.get("linkedin_url"),
        "industria": organizacao.get("industry_name") if campo_presente(organizacao.get("industry_name"))
                     else organizacao.get("industry_code"),
        "porte": organizacao.get("employee_count") if campo_presente(organizacao.get("employee_count"))
                 else organizacao.get("employee_band"),
        "revenue_estimate": organizacao.get("revenue_estimate"),
        "city": organizacao.get("city"),
        "state": organizacao.get("state"),
        "website_url": organizacao.get("website_url"),
        "unit_count": organizacao.get("unit_count"),
        "business_model": organizacao.get("business_model"),
    }
    detalhe = {}
    soma = Decimal("0")
    for campo, peso in COMPLETUDE_CAMPOS:
        presente = campo_presente(valores.get(campo))
        detalhe[campo] = {"presente": presente, "peso": str(peso)}
        if presente:
            soma += peso
    return soma, {"campos": detalhe, "valores": {c: (_iso(v) if isinstance(v, str) else v)
                                                 for c, v in valores.items()}}


def _website_valido(valor) -> bool:
    texto = _texto(valor)
    m = re.match(r"^(https?)://([^\s/]+)(/.*)?$", texto, re.I)
    if not m:
        return False
    host = m.group(2).split(":")[0]
    if "." not in host or host.startswith(".") or host.endswith("."):
        return False
    tld = host.rsplit(".", 1)[-1]
    return len(tld) >= 2 and tld.isalpha()


def checks_validade(organizacao: dict, identidade) -> list:
    """Checks de formato/coerencia. Cada um declara se E APLICAVEL ao que existe.

    Regra do desenho: validade julga SOMENTE o que existe — campo ausente ja foi punido na
    completude e nao e punido de novo aqui (sem dupla punicao). Dado invalido NAO e corrigido nem
    reescrito: o score mede, nao conserta.
    """
    checks = []
    cnpj = _texto(organizacao.get("cnpj"))
    if cnpj:
        checks.append(("cnpj", identidade.cnpj_valido(cnpj), "CNPJ_INVALIDO"))
    domain = _texto(organizacao.get("domain"))
    if domain:
        checks.append(("domain", identidade.domain_valido(domain), "DOMAIN_INVALIDO"))
    website = _texto(organizacao.get("website_url"))
    if website:
        checks.append(("website_url", _website_valido(website), "WEBSITE_URL_INVALIDA"))
    linkedin = _texto(organizacao.get("linkedin_url"))
    if linkedin:
        checks.append(("linkedin_url", identidade.linkedin_valido(linkedin), "LINKEDIN_URL_INVALIDA"))
    banda = _texto(organizacao.get("employee_band"))
    contagem = organizacao.get("employee_count")
    if banda and contagem is not None:
        esperada = identidade._faixa_de_empregados(contagem)
        coerente = (banda != "UNKNOWN") and (esperada is None or banda == esperada)
        checks.append(("porte_x_contagem", coerente, "BANDA_INCOERENTE_COM_CONTAGEM"))
    if domain and website:
        host = re.sub(r"^https?://", "", website, flags=re.I).split("/")[0].split(":")[0].lower()
        alvo = domain.lower()
        checks.append(("domain_x_website", host == alvo or host.endswith("." + alvo),
                       "WEBSITE_DE_OUTRO_DOMINIO"))
    saida = []
    for nome, aprovado, motivo in checks:
        saida.append({"check": nome, "aplicavel": True, "aprovado": bool(aprovado),
                      "motivo": None if aprovado else motivo})
    return saida


def componente_validade(organizacao: dict, identidade) -> tuple:
    checks = checks_validade(organizacao, identidade)
    if not checks:
        return Decimal("0"), {"checks": [], "motivo": "SEM_CHECK_APLICAVEL (nada preenchido para julgar)"}
    aprovados = sum(1 for c in checks if c["aprovado"])
    return Decimal(aprovados) / Decimal(len(checks)), {"checks": checks}


def componente_confiabilidade(organizacao: dict, research_runs: list) -> tuple:
    """Lastro medido no banco: fonte declarada, pesquisa executada e cobertura de fontes."""
    detalhe = {"fonte_organizacao": _texto(organizacao.get("source")) or None}
    fonte = _texto(organizacao.get("source")).upper()
    detalhe["fonte_no_vocabulario"] = bool(fonte) and fonte in FONTES

    concluidas = [r for r in research_runs if _texto(r.get("status")).upper() == "COMPLETED"]
    soma_fontes = sum(int(r.get("source_count") or 0) for r in concluidas)
    detalhe["research_runs"] = len(research_runs)
    detalhe["research_runs_concluidas"] = len(concluidas)
    detalhe["source_count_total"] = soma_fontes

    cobertura = min(Decimal(1),
                    Decimal(soma_fontes) / Decimal(FONTES_PARA_COBERTURA_MAXIMA))
    parcelas = {
        "fonte_declarada": Decimal(1) if detalhe["fonte_no_vocabulario"] else Decimal(0),
        "pesquisa_existente": Decimal(1) if concluidas else Decimal(0),
        "cobertura_de_fontes": cobertura,
    }
    detalhe["parcelas"] = {nome: str(valor) for nome, valor in parcelas.items()}
    detalhe["fontes_para_cobertura_maxima"] = FONTES_PARA_COBERTURA_MAXIMA
    soma = sum(PESOS_CONFIABILIDADE[nome] * valor for nome, valor in parcelas.items())
    return soma, detalhe


def referencia_de_dados(organizacao: dict, research_runs: list):
    """A data do DADO: criacao da empresa ou conclusao da pesquisa. `updated_at` e PROIBIDO aqui.

    Usar `updated_at` faria o proprio write do score rejuvenescer a componente de atualidade (o
    espelho em `organizations` e um UPDATE) — o score aumentaria a propria nota so por ter rodado.
    """
    candidatos = [_data_do_texto(organizacao.get("created_at"))]
    for run in research_runs:
        if _texto(run.get("status")).upper() == "COMPLETED":
            candidatos.append(_data_do_texto(run.get("completed_at")))
    validos = [c for c in candidatos if c is not None]
    return max(validos) if validos else None


def componente_atualidade(organizacao: dict, research_runs: list, referencia: date) -> tuple:
    base = referencia_de_dados(organizacao, research_runs)
    detalhe = {"referencia_de_dados": base.isoformat() if base else None,
               "referencia_de_calculo": referencia.isoformat(),
               "fresco_dias": ATUALIDADE_FRESCO_DIAS, "expirado_dias": ATUALIDADE_EXPIRADO_DIAS}
    if base is None:
        detalhe["dias_desde_referencia"] = None
        detalhe["motivo"] = "SEM_REFERENCIA_DE_DADOS"
        return Decimal("0"), detalhe
    dias = (referencia - base).days
    detalhe["dias_desde_referencia"] = dias
    if dias <= ATUALIDADE_FRESCO_DIAS:
        return Decimal("1"), detalhe
    if dias >= ATUALIDADE_EXPIRADO_DIAS:
        return Decimal("0"), detalhe
    faixa = Decimal(ATUALIDADE_EXPIRADO_DIAS - dias) / Decimal(ATUALIDADE_EXPIRADO_DIAS - ATUALIDADE_FRESCO_DIAS)
    return faixa, detalhe


def calcular(organizacao: dict, research_runs: list, referencia: date, identidade) -> dict:
    """Mede, monta inputs/explanation e devolve o valor — determinista para (estado, referencia)."""
    completude, det_completude = componente_completude(organizacao)
    validade, det_validade = componente_validade(organizacao, identidade)
    confiabilidade, det_confiabilidade = componente_confiabilidade(organizacao, research_runs)
    atualidade, det_atualidade = componente_atualidade(organizacao, research_runs, referencia)
    medidas = {"completude": completude, "validade": validade,
               "confiabilidade": confiabilidade, "atualidade": atualidade}
    parcelas = {nome: PESOS[nome] * medidas[nome] for nome in PESOS}
    bruto = sum(parcelas.values()) * Decimal(100)
    valor = _quantizar(bruto)
    if valor < 0 or valor > 100:
        raise ValueError("valor fora da faixa [0,00; 100,00]: %s" % valor)
    inputs = {
        "score_type": SCORE_TIPO,
        "score_version": SCORE_VERSAO,
        "referencia_de_calculo": referencia.isoformat(),
        "organizacao": {
            "id": organizacao.get("id"),
            "status": organizacao.get("status"),
            "source": organizacao.get("source"),
        },
        "completude": det_completude,
        "validade": det_validade,
        "confiabilidade": det_confiabilidade,
        "atualidade": det_atualidade,
    }
    canonico = json.dumps({"score_version": SCORE_VERSAO,
                           "referencia_de_calculo": referencia.isoformat(),
                           "inputs": inputs},
                          sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    inputs_sha256 = _sha256(canonico)
    explanation = {
        "modelo": "%s/%s" % (SCORE_TIPO, SCORE_VERSAO),
        "componentes": {nome: str(medidas[nome].quantize(Decimal("0.0001"))) for nome in medidas},
        "pesos": {nome: str(PESOS[nome]) for nome in PESOS},
        "parcelas": {nome: str(parcelas[nome].quantize(Decimal("0.0001"))) for nome in parcelas},
        "valor": str(valor),
        "valor_anterior": (_iso(organizacao.get("data_quality_score"))
                           if campo_presente(organizacao.get("data_quality_score")) else None),
        "inputs_sha256": inputs_sha256,
        "referencia_de_calculo": referencia.isoformat(),
        "faixa_de_tiering": "fora do escopo (TRE-W5-E06-T01)",
        "deterministico": True,
        "llm": {"executado": False,
                "motivo": "o Data Quality Score v1 e deterministico; o gate do JEV existe e e fail-closed"},
    }
    return {"valor": str(valor), "inputs": inputs, "explanation": explanation,
            "inputs_sha256": inputs_sha256,
            "componentes": {nome: str(medidas[nome].quantize(Decimal("0.0001"))) for nome in medidas}}


# ---------------------------------------------------------------------------------------
# Identidade da organizacao (resolucao pelos fortes do contrato)
# ---------------------------------------------------------------------------------------
def identificadores_validos(entrada: dict, fortes: tuple, identidade) -> list:
    """[(tipo, valor_normalizado)] na ordem de prioridade do contrato; invalido e descartado."""
    organizacao = entrada.get("organizacao") or {}
    if not isinstance(organizacao, dict):
        return []
    validadores = {
        "cnpj": (identidade.cnpj_valido, identidade.normalizar_cnpj),
        "domain": (identidade.domain_valido, identidade.normalizar_domain),
        "linkedin_url": (identidade.linkedin_valido, identidade.normalizar_linkedin),
    }
    saida = []
    for tipo in fortes:
        bruto = _texto(organizacao.get(tipo))
        if not bruto:
            continue
        valida, normaliza = validadores[tipo]
        if valida(bruto):
            valor = normaliza(bruto)
            if all(valor != v for _, v in saida):
                saida.append((tipo, valor))
    return saida


def identificadores_declarados(entrada: dict, fortes: tuple) -> list:
    organizacao = entrada.get("organizacao") or {}
    if not isinstance(organizacao, dict):
        return []
    return [(tipo, _texto(organizacao.get(tipo))) for tipo in fortes
            if _texto(organizacao.get(tipo))]


def organizacoes_declaradas(entrada: dict) -> list:
    """IDs declarados direto (`organizacao_id` / `organizacao.id`) — usado para encadear agentes."""
    ids = []
    for chave in ("organizacao_id", "organization_id", "id"):
        valor = _texto(entrada.get(chave))
        if valor:
            ids.append(valor)
    organizacao = entrada.get("organizacao") or {}
    if isinstance(organizacao, dict):
        for chave in ("organizacao_id", "organization_id", "id"):
            valor = _texto(organizacao.get(chave))
            if valor:
                ids.append(valor)
    return sorted(set(ids))


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o score pode e nao pode escrever)
# ---------------------------------------------------------------------------------------
_LITERAIS = None  # preenchido por `preparar_literais` (lit/lit_json vem do modulo de identidade)


def preparar_literais(identidade) -> None:
    global _LITERAIS
    _LITERAIS = (identidade.lit, identidade.lit_json)


def lit(valor) -> str:
    if _LITERAIS is None:
        raise RuntimeError("modulo de identidade nao carregado: chame preparar_literais()")
    return _LITERAIS[0](valor)


def lit_json(objeto) -> str:
    if _LITERAIS is None:
        raise RuntimeError("modulo de identidade nao carregado: chame preparar_literais()")
    return _LITERAIS[1](objeto)


_ESCRITA = (
    ("insert", re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][\w\.]*)", re.I)),
    ("update", re.compile(r"\bUPDATE\s+([A-Za-z_][\w\.]*)", re.I)),
    ("delete", re.compile(r"\bDELETE\s+FROM\s+([A-Za-z_][\w\.]*)", re.I)),
)
_DDL = re.compile(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT\s+ON)\b", re.I)
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_INSERT_COLUNAS = re.compile(r"INSERT\s+INTO\s+%s\s*\(([^)]*)\)")


def _sem_literais(sql: str) -> str:
    """A instrucao SEM o conteudo dos literais — so o codigo SQL.

    Dado de negocio (nome de empresa, dominio) nao e instrucao: um valor com a palavra 'drop'
    faria a guarda recusar escrita legitima como se fosse DDL. Quem escreve dado e o `lit()`, que
    escapa apostrofo dobrando — entao a varredura olha o codigo, nao a prosa.
    """
    return _LITERAL.sub("''", sql)


def colunas_do_insert(sql: str, tabela: str) -> list:
    padrao = re.compile(_INSERT_COLUNAS.pattern % re.escape(tabela), re.I)
    return [[c.strip().lower() for c in bloco.split(",") if c.strip()]
            for bloco in padrao.findall(sql)]


def _colunas_do_set(bloco: str) -> list:
    colunas = []
    for parte in bloco.split(","):
        if "=" not in parte:
            continue
        colunas.append(parte.split("=", 1)[0].strip().strip('"').lower())
    return [c for c in colunas if c]


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL e escrita fora do declarado.

    Alem das regras de tabela/operacao, o SQL deste score tem tres exigencias de DESENHO:
    (a) o INSERT em `scores` declara as colunas obrigatorias (inclusive `score_version` — score sem
    versao nao e reprodutivel, doc 12 §8) e grava ESTE score (`DATA_QUALITY`), nao outro;
    (b) o UPDATE em `organizations` tem WHERE e escreve SO a coluna do espelho — nem `updated_at`
    (que rejuvenesceria a componente de atualidade) nem qualquer outro dado;
    (c) DELETE so em `scores` e so no desfazer explicito.
    """
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL nao e permitido ao Score Data Quality")
    for colunas in colunas_do_insert(codigo, TABELA_SCORES):
        faltando = [c for c in COLUNAS_SCORES_OBRIGATORIAS if c not in colunas]
        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT em scores sem coluna obrigatoria: %s" % ", ".join(faltando))
        proibidas = [c for c in colunas if c not in COLUNAS_SCORES]
        if proibidas:
            raise GuardaDeEscritaViolada(
                "INSERT em scores com coluna fora do contrato do score: %s" % ", ".join(proibidas))
        if "'%s'" % SCORE_TIPO not in sql or "'%s'" % SCORE_VERSAO not in sql:
            raise GuardaDeEscritaViolada(
                "INSERT em scores sem o par score_type/score_version deste score (%s/%s)"
                % (SCORE_TIPO, SCORE_VERSAO))
    for colunas in colunas_do_insert(codigo, TABELA_AGENT_RUNS):
        proibidas = [c for c in colunas if c not in COLUNAS_AGENT_RUNS]
        if proibidas:
            raise GuardaDeEscritaViolada(
                "INSERT em agent_runs com coluna fora do contrato: %s" % ", ".join(proibidas))
    for m in re.finditer(r"\bUPDATE\s+([A-Za-z_][\w\.]*)", codigo, re.I):
        tabela = m.group(1).lower()
        resto = codigo[m.end():]
        # O alvo do UPDATE carrega ALIAS em PG (`UPDATE <tab> o SET ...`); a partir do SET, o que
        # separa a lista de colunas do filtro e o primeiro WHERE.
        mset = re.search(r"\bSET\b(.*?)\bWHERE\b", resto, re.S | re.I)
        if not mset:
            raise GuardaDeEscritaViolada(
                "UPDATE em %s sem WHERE (ou sem SET): recusado por desenho" % tabela)
        if tabela == TABELA_ORGANIZACOES:
            colunas = _colunas_do_set(mset.group(1))
            fora = [c for c in colunas if c not in COLUNAS_ORGANIZACOES_ESCRITAS]
            if fora or not colunas:
                raise GuardaDeEscritaViolada(
                    "UPDATE em organizations escreve coluna fora do espelho do score "
                    "(data_quality_score): %s" % (", ".join(fora) or "nenhuma coluna"))
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela nao declarada: %s" % tabela)
            if operacao == "insert" and tabela == TABELA_ORGANIZACOES:
                raise GuardaDeEscritaViolada(
                    "INSERT em organizations nao e permitido: o score nao cria nem copia empresa "
                    "(quem cria e o Scout, TRE-W4-E01-T01)")
            if operacao == "update" and tabela != TABELA_ORGANIZACOES:
                raise GuardaDeEscritaViolada(
                    "UPDATE em %s nao e permitido: o historico do score e append-only e a auditoria "
                    "nao se reescreve" % tabela)
            if operacao == "delete":
                if tabela != TABELA_SCORES:
                    raise GuardaDeEscritaViolada("DELETE so e permitido em scores (desfazer)")
                if not permitir_remocao:
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
        comando = self.prefixo + ["-v", "ON_ERROR_STOP=1", "-q", "-tA", "-F", "|"]
        try:
            p = subprocess.run(comando, input=sql, capture_output=True, text=True,
                               timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PortaIndisponivel("porta psql falhou: %s" % exc)
        return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------------------
# SQL do score
# ---------------------------------------------------------------------------------------
MARCA_ESCRITO = "ESCRITO"
MARCA_JA_EXISTE = "JA_EXISTE"

_CAMPOS_ORGANIZACAO = (
    "id", "legal_name", "trade_name", "domain", "website_url", "linkedin_url", "cnpj",
    "industry_code", "industry_name", "employee_count", "employee_band", "revenue_estimate",
    "unit_count", "city", "state", "country_code", "business_model", "status", "source",
    "data_quality_score", "created_at",
)


def sql_listar_organizacoes() -> str:
    return ("SELECT id::text FROM %s WHERE deleted_at IS NULL ORDER BY created_at ASC, id ASC;"
            % TABELA_ORGANIZACOES)


def _filtro_grosso(tipo: str, valor: str) -> str:
    """Pre-filtro SUPERSET do casamento de identidade — nunca descarta um casamento verdadeiro.

    A coluna guarda a forma NORMALIZADA (o produtor escreve normalizado), mas `cnpj`/`domain`/
    `linkedin_url` sao VARCHAR livre: aceitam pontuacao, caixa alta e URL inteira. O filtro aceita
    as duas formas; quem DECIDE e' o Python, com a mesma regra do produtor (paridade de identidade).
    """
    if tipo == "cnpj":
        return ("o.cnpj IS NOT NULL AND o.cnpj <> '' AND "
                "regexp_replace(o.cnpj, '\\D', '', 'g') = %s" % lit(re.sub(r"\D", "", valor)))
    if tipo == "domain":
        return ("o.domain IS NOT NULL AND o.domain <> '' AND "
                "(lower(o.domain) LIKE %s OR %s LIKE '%%' || lower(o.domain) || '%%')"
                % (lit("%" + valor + "%"), lit(valor)))
    # linkedin_url: o valor normalizado carrega o slug, e a forma guardada contem o slug
    slug = valor.rstrip("/").rsplit("/", 1)[-1]
    return ("o.linkedin_url IS NOT NULL AND o.linkedin_url <> '' AND "
            "lower(o.linkedin_url) LIKE %s" % lit("%" + slug + "%"))


_CAMPOS_IDENTIDADE = ("id", "cnpj", "domain", "linkedin_url")


def sql_consultar_organizacao(validos: list) -> str:
    """Candidatas por identidade forte — o SQL PRE-FILTRA; a decisao exata e' do modulo (Python)."""
    campos = []
    for campo in _CAMPOS_IDENTIDADE:
        campos.append("'%s'" % campo)
        campos.append("o.%s" % campo)
    filtros = " OR ".join(_filtro_grosso(tipo, valor) for tipo, valor in validos)
    return ("SELECT json_build_object(%s)::text FROM %s o WHERE o.deleted_at IS NULL AND (%s);"
            % (", ".join(campos), TABELA_ORGANIZACOES, filtros))


def sql_ler_organizacao(organizacao_id: str) -> str:
    campos = []
    for campo in _CAMPOS_ORGANIZACAO:
        campos.append("'%s'" % campo)
        campos.append("o.%s" % campo)
    return ("SELECT json_build_object(%s)::text FROM %s o "
            "WHERE o.id = %s::uuid AND o.deleted_at IS NULL;"
            % (", ".join(campos), TABELA_ORGANIZACOES, lit(organizacao_id)))


def sql_ler_research_runs(organizacao_id: str) -> str:
    return ("SELECT COALESCE(json_agg(json_build_object("
            "'id', r.id, 'status', r.status, 'source_count', r.source_count, "
            "'completed_at', r.completed_at, 'confidence', r.confidence) "
            "ORDER BY r.completed_at ASC NULLS LAST, r.id ASC)::text, '[]') "
            "FROM %s r WHERE r.organization_id = %s::uuid;"
            % (TABELA_RESEARCH_RUNS, lit(organizacao_id)))


def sql_ultimo_score(organizacao_id: str) -> str:
    return ("SELECT json_build_object('id', s.id, 'score_value', s.score_value, "
            "'calculated_at', s.calculated_at, "
            "'inputs_sha256', s.explanation->>'inputs_sha256')::text "
            "FROM %s s WHERE s.organization_id = %s::uuid AND s.score_type = %s "
            "AND s.score_version = %s ORDER BY s.calculated_at DESC, s.id DESC LIMIT 1;"
            % (TABELA_SCORES, lit(organizacao_id), lit(SCORE_TIPO), lit(SCORE_VERSAO)))


def sql_gravar_score(score_id: str, organizacao_id: str, resultado: dict, calculado_em: str) -> str:
    """Uma instrucao: insere a MEDICAO e espelha o valor — ou nao escreve nada.

    A idempotencia vive aqui, nao na prosa: a insercao so acontece quando o ULTIMO
    `DATA_QUALITY/v1.0` da empresa NAO tem o mesmo `inputs_sha256` desta medicao. Replay com o banco
    igual devolve `JA_EXISTE` e escreve ZERO linha (nem em `scores`, nem o espelho). O `UPDATE` do
    espelho so roda quando o INSERT aconteceu (`EXISTS (SELECT 1 FROM novo)`) e NAO toca
    `updated_at` — quem carimba a empresa e quem enriquece o dado, nao o score.
    """
    valor = str(resultado["valor"])
    if not _NUMERO_2_CASAS.match(valor):
        raise GuardaDeEscritaViolada("valor do score fora do formato de 2 casas: %r" % valor)
    inputs = lit_json(resultado["inputs"])
    explanation = lit_json(resultado["explanation"])
    hash_ = resultado["inputs_sha256"]
    return (
        "WITH novo AS (\n"
        "  INSERT INTO {scores} ({colunas})\n"
        "  SELECT {score_id}::uuid, {organizacao_id}::uuid, {tipo}, {valor}::numeric, {versao},\n"
        "         {inputs}::jsonb, {explanation}::jsonb, {calculado_em}::timestamptz\n"
        "  WHERE NOT EXISTS (\n"
        "    SELECT 1 FROM {scores} s\n"
        "    WHERE s.organization_id = {organizacao_id}::uuid AND s.score_type = {tipo}\n"
        "      AND s.score_version = {versao}\n"
        "      AND s.calculated_at = (SELECT max(t.calculated_at) FROM {scores} t\n"
        "                             WHERE t.organization_id = {organizacao_id}::uuid\n"
        "                               AND t.score_type = {tipo} AND t.score_version = {versao})\n"
        "      AND s.explanation->>'inputs_sha256' = {hash}\n"
        "  )\n"
        "  RETURNING id\n"
        "),\n"
        "espelho AS (\n"
        "  UPDATE {organizacoes} o SET data_quality_score = {valor}::numeric\n"
        "  WHERE o.id = {organizacao_id}::uuid AND o.deleted_at IS NULL\n"
        "    AND EXISTS (SELECT 1 FROM novo)\n"
        "  RETURNING o.id\n"
        ")\n"
        "SELECT {marca_escrito} AS marca FROM novo\n"
        "UNION ALL\n"
        "SELECT {marca_ja_existe} AS marca WHERE NOT EXISTS (SELECT 1 FROM novo);"
    ).format(scores=TABELA_SCORES, organizacoes=TABELA_ORGANIZACOES, colunas=", ".join(COLUNAS_SCORES),
             score_id=lit(score_id), organizacao_id=lit(organizacao_id), tipo=lit(SCORE_TIPO),
             valor=lit(valor), versao=lit(SCORE_VERSAO), inputs=inputs, explanation=explanation,
             calculado_em=lit(calculado_em), hash=lit(hash_), marca_escrito=lit(MARCA_ESCRITO),
             marca_ja_existe=lit(MARCA_JA_EXISTE))


def sql_registrar_execucao(run_id: str, correlation_id: str, organizacao_id, status: str,
                           entrada: dict, saida: dict, inicio: str, fim: str, erro=None) -> str:
    organizacao = ("%s::uuid" % lit(organizacao_id)) if organizacao_id else "NULL"
    return ("INSERT INTO {tabela} (id, agent_name, agent_role, agent_version, workflow, "
            "workflow_version, organization_id, triggered_by, correlation_id, input, output, "
            "started_at, finished_at, status, created_at) VALUES "
            "({run_id}::uuid, {nome}, {papel}, {versao}, {workflow}, {workflow_versao}, "
            "{organizacao}, {gatilho}, {correlation_id}::uuid, {entrada}::jsonb, {saida}::jsonb, "
            "{inicio}::timestamptz, {fim}::timestamptz, {status}, now());").format(
        tabela=TABELA_AGENT_RUNS, run_id=lit(run_id), nome=lit(SCORE), papel=lit(PAPEL),
        versao=lit(VERSAO), workflow=lit(WORKFLOW), workflow_versao=lit(WORKFLOW_VERSAO),
        organizacao=organizacao, gatilho=lit("card:TRE-W5-E04-T01"),
        correlation_id=lit(correlation_id), entrada=lit_json(entrada), saida=lit_json(saida),
        inicio=lit(inicio), fim=lit(fim), status=lit(status))


def sql_selecionar_da_rodada(correlation_id: str) -> str:
    """O que a rodada ESCREVEU: os scores (veredito ESCRITO) ligados pelo `score_id` da auditoria."""
    return ("SELECT json_build_object('score_id', a.output->>'score_id', "
            "'organization_id', a.organization_id::text, 'valor', a.output->>'valor', "
            "'veredito', a.output->>'veredito')::text "
            "FROM {tabela} a WHERE a.correlation_id = {cid}::uuid AND a.agent_name = {nome} "
            "AND a.output->>'veredito' = {escrito} AND a.output->>'score_id' IS NOT NULL "
            "ORDER BY a.started_at ASC, a.id ASC;").format(
        tabela=TABELA_AGENT_RUNS, cid=lit(correlation_id), nome=lit(SCORE),
        escrito=lit(VER_ESCRITO))


def sql_desfazer(correlation_id: str) -> str:
    """Apaga do historico SO o que a rodada criou e devolve o espelho ao valor ANTERIOR.

    A restauracao so acontece quando o valor atual do espelho e exatamente o valor escrito pela
    rodada: nota mais nova (de outra rodada) NAO e sobrescrita. `agent_runs` e auditoria e fica.
    """
    return (
        "WITH alvos AS (\n"
        "  SELECT (a.output->>'score_id')::uuid AS score_id FROM {agent_runs} a\n"
        "  WHERE a.correlation_id = {cid}::uuid AND a.agent_name = {nome}\n"
        "    AND a.output->>'veredito' = {escrito} AND a.output->>'score_id' IS NOT NULL\n"
        "),\n"
        "apagados AS (\n"
        "  DELETE FROM {scores} s USING alvos a WHERE s.id = a.score_id\n"
        "  RETURNING s.organization_id, s.score_value,\n"
        "            (s.explanation->>'valor_anterior') AS anterior\n"
        "),\n"
        "restaurado AS (\n"
        "  UPDATE {organizacoes} o SET data_quality_score =\n"
        "    CASE WHEN apagados.anterior IS NULL THEN NULL ELSE apagados.anterior::numeric END\n"
        "  FROM apagados\n"
        "  WHERE o.id = apagados.organization_id AND o.data_quality_score = apagados.score_value\n"
        "  RETURNING o.id\n"
        ")\n"
        "SELECT 'APAGADO'::text AS marca, apagados.organization_id::text AS organizacao_id FROM apagados\n"
        "UNION ALL\n"
        "SELECT 'RESTAURADO'::text, restaurado.id::text FROM restaurado;").format(
        agent_runs=TABELA_AGENT_RUNS, scores=TABELA_SCORES, organizacoes=TABELA_ORGANIZACOES,
        cid=lit(correlation_id), nome=lit(SCORE), escrito=lit(VER_ESCRITO))


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
        "motivo": "v1 do Data Quality Score e deterministica: nenhuma chamada de LLM e feita",
    }


# ---------------------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------------------
class ScoreDataQuality:
    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None,
                 identidade=None, referencia=None):
        self.raiz = Path(raiz or RAIZ_PADRAO)
        self.contrato = carregar_contrato_do_score(self.raiz)
        self.fortes = fortes_do_contrato_de_dados(self.raiz)
        if list(self.fortes) != list(self.contrato["entrada"]["resolucao"]["fortes_por_prioridade"]):
            raise ValueError("divergencia entre o contrato do score e o Data Contract V1.0: %s x %s"
                             % (list(self.fortes),
                                self.contrato["entrada"]["resolucao"]["fortes_por_prioridade"]))
        if self.contrato["identidade_do_score"]["score_type"] != SCORE_TIPO:
            raise ValueError("divergencia no score_type do contrato")
        if self.contrato["identidade_do_score"]["score_version"] != SCORE_VERSAO:
            raise ValueError("divergencia no score_version do contrato")
        if dict(self.contrato["modelo_v1"]["componentes"]) != {k: float(v) for k, v in COMPONENTES}:
            raise ValueError("divergencia nos pesos dos componentes (contrato x codigo)")
        if dict(self.contrato["modelo_v1"]["completude_campos"]) != {k: float(v)
                                                                   for k, v in COMPLETUDE_CAMPOS}:
            raise ValueError("divergencia nos pesos da completude (contrato x codigo)")
        if list(self.contrato["escrita"]["colunas_scores"]) != list(COLUNAS_SCORES):
            raise ValueError("divergencia nas colunas escritas em scores")
        if list(self.contrato["escrita"]["colunas_organizations_escritas"]) != \
                list(COLUNAS_ORGANIZACOES_ESCRITAS):
            raise ValueError("divergencia nas colunas escritas em organizations")
        self.identidade = identidade if identidade is not None else carregar_identidade(self.raiz)
        preparar_literais(self.identidade)
        self.porta = porta if porta is not None else PortaAusente()
        self.relogio = relogio
        self.ambiente = ambiente
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.referencia = self._referencia(referencia)
        self.alvo = None

    @staticmethod
    def _referencia(referencia):
        if referencia in (None, ""):
            return datetime.now(timezone.utc).date()
        if isinstance(referencia, date):
            return referencia
        try:
            return date.fromisoformat(str(referencia)[:10])
        except ValueError:
            raise ValueError("referencia invalida: %r (use AAAA-MM-DD)" % referencia)

    # -- ambiente ---------------------------------------------------------------------
    def conferir_ambiente(self) -> str:
        if self.ambiente in (None, ""):
            raise RecusaDeAmbiente("ambiente nao declarado: recusa (fail-closed)")
        if self.ambiente == AMBIENTE_RECUSADO:
            raise RecusaDeAmbiente(
                "Score Data Quality v1 nao escreve em prod (ADR-005): a promocao exige card proprio "
                "com aprovacao humana registrada")
        if self.ambiente not in AMBIENTES_PERMITIDOS:
            raise RecusaDeAmbiente("ambiente desconhecido: %r" % self.ambiente)
        return self.ambiente

    def identificar_alvo(self) -> dict:
        """Registra a identidade do alvo medido (sem afirmar nome de ambiente — alinhar a
        convencao de nome e decisao do dono, nao do score)."""
        rc, saida, erro = self.porta.executar(
            "SELECT current_database(), current_user, version();")
        if rc != 0:
            raise PortaIndisponivel("nao consegui ler a identidade do alvo: %s" % (erro or saida))
        campos = saida.strip().split("|")
        self.alvo = {"banco": campos[0] if campos else None,
                     "usuario": campos[1] if len(campos) > 1 else None,
                     "servidor": (campos[2] if len(campos) > 2 else "")[:80]}
        return self.alvo

    # -- leitura ----------------------------------------------------------------------
    def _linhas_json(self, sql: str) -> list:
        rc, saida, erro = self.porta.executar(sql)
        if rc != 0:
            raise PortaIndisponivel("consulta falhou: %s" % (erro or saida))
        registros = []
        for linha in saida.splitlines():
            linha = linha.strip()
            if not linha or linha in ("BEGIN", "COMMIT"):
                continue
            try:
                registros.append(json.loads(linha))
            except json.JSONDecodeError:
                continue
        return registros

    def ids_da_listagem(self) -> list:
        rc, saida, erro = self.porta.executar(sql_listar_organizacoes())
        if rc != 0:
            raise PortaIndisponivel("listagem de organizacoes falhou: %s" % (erro or saida))
        return [l.strip() for l in saida.splitlines() if l.strip() and l.strip() not in ("BEGIN", "COMMIT")]

    def organizacoes_casadas(self, validos: list) -> list:
        """Casamento EXATO pelo modulo de identidade — o SQL so' pre-filtra (superset).

        Mesma ordem do produtor (Scout): o filtro do banco e' grosso e a comparacao normaliza a
        forma GUARDADA antes de comparar. Sem essa camada, CNPJ gravado com pontuacao (a coluna e'
        VARCHAR livre) nao casaria — defeito medido no aceite E2E, item `fonte-forte-escreve-uma-medicao`.
        """
        if not validos:
            return []
        normalizadores = {"cnpj": self.identidade.normalizar_cnpj,
                          "domain": self.identidade.normalizar_domain,
                          "linkedin_url": self.identidade.normalizar_linkedin}
        casadas = []
        for registro in self._linhas_json(sql_consultar_organizacao(validos)):
            if not _texto(registro.get("id")):
                continue
            for tipo, valor in validos:
                bruto = _texto(registro.get(tipo))
                if bruto and normalizadores[tipo](bruto) == valor:
                    casadas.append(registro)
                    break
        return casadas

    def ler_organizacao(self, organizacao_id: str):
        registros = self._linhas_json(sql_ler_organizacao(organizacao_id))
        for registro in registros:
            if _texto(registro.get("id")):
                return registro
        return None

    def ler_research_runs(self, organizacao_id: str) -> list:
        """O SELECT devolve UM array JSON numa linha: aqui ele volta como lista de runs."""
        registros = self._linhas_json(sql_ler_research_runs(organizacao_id))
        if registros and isinstance(registros[0], list):
            return registros[0]
        return registros

    def ultimo_score(self, organizacao_id: str):
        registros = self._linhas_json(sql_ultimo_score(organizacao_id))
        for registro in registros:
            if _texto(registro.get("id")):
                return registro
        return None

    # -- resolucao do alvo ------------------------------------------------------------
    def alvos_da_entrada(self, entrada: dict) -> tuple:
        """(ids, veredito_antecipado, motivos) — resolucao pelos fortes ou por id declarado."""
        ids = organizacoes_declaradas(entrada)
        declarados = identificadores_declarados(entrada, self.fortes)
        validos = identificadores_validos(entrada, self.fortes, self.identidade)
        if not ids and not declarados:
            return [], VER_RECUSADA, ["SEM_IDENTIFICADOR: declare os fortes da organizacao ou o id"]
        if declarados and not validos and not ids:
            return [], VER_RECUSADA, ["IDENTIFICADOR_FORTE_INVALIDO"]
        for organizacao_id in ids:
            if not _UUID_SIMPLES.match(organizacao_id):
                return [], VER_RECUSADA, ["ID_MALFORMADO: %s" % organizacao_id]
        if ids:
            return ids, None, []
        casadas = self.organizacoes_casadas(validos)
        if not casadas:
            return [], VER_RECUSADA, ["ORGANIZACAO_NAO_ENCONTRADA"]
        if len(casadas) > 1:
            return [], VER_RECUSADA, ["IDENTIDADE_AMBIGUA: %d organizacoes casadas" % len(casadas)]
        return [casadas[0]["id"]], None, []

    # -- execucao ---------------------------------------------------------------------
    def planejar(self, entrada: dict) -> dict:
        """Modo sem escrita: resolve o alvo e diz o que FARIA (com o valor medido, quando ha banco)."""
        ids, veredito, motivos = self.alvos_da_entrada(entrada)
        if veredito:
            return {"veredito": "PLANEJADO_RECUSAR", "motivos": motivos, "organizacoes": []}
        saida = []
        for organizacao_id in ids:
            organizacao = self.ler_organizacao(organizacao_id)
            if organizacao is None:
                saida.append({"organizacao_id": organizacao_id, "veredito": "PLANEJADO_RECUSAR",
                              "motivos": ["ORGANIZACAO_NAO_ENCONTRADA"]})
                continue
            runs = self.ler_research_runs(organizacao_id)
            resultado = calcular(organizacao, runs, self.referencia, self.identidade)
            saida.append({"organizacao_id": organizacao_id, "veredito": "PLANEJADO_ESCREVER",
                          "valor": str(resultado["valor"]),
                          "componentes": resultado["componentes"],
                          "inputs_sha256": resultado["inputs_sha256"],
                          "ultimo_score": self.ultimo_score(organizacao_id)})
        return {"veredito": "PLANEJADO", "motivos": [], "organizacoes": saida}

    def processar(self, entrada: dict) -> dict:
        inicio = self.relogio()
        entrada_auditoria = {"entrada": entrada, "ambiente": self.ambiente,
                             "correlation_id": self.correlation_id,
                             "referencia_de_calculo": self.referencia.isoformat()}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": VER_ERRO, "motivos": [], "organization_id": None,
                     "score_id": None, "valor": None, "componentes": None,
                     "inputs_sha256": None}
        ids, veredito_antecipado, motivos = ([], VER_ERRO, [])
        try:
            ids, veredito_antecipado, motivos = self.alvos_da_entrada(entrada)
            if veredito_antecipado == VER_RECUSADA:
                resultado["veredito"] = VER_RECUSADA
                resultado["motivos"] = list(motivos)
            else:
                organizacao_id = ids[0]
                resultado["organization_id"] = organizacao_id
                organizacao = self.ler_organizacao(organizacao_id)
                if organizacao is None:
                    resultado["veredito"] = VER_RECUSADA
                    resultado["motivos"] = ["ORGANIZACAO_NAO_ENCONTRADA"]
                else:
                    runs = self.ler_research_runs(organizacao_id)
                    medicao = calcular(organizacao, runs, self.referencia, self.identidade)
                    resultado["valor"] = str(medicao["valor"])
                    resultado["componentes"] = medicao["componentes"]
                    resultado["inputs_sha256"] = medicao["inputs_sha256"]
                    score_id = str(uuid.uuid4())
                    calculado_em = self.relogio()
                    rc, saida, erro = self.porta.executar(sql_gravar_score(
                        score_id, organizacao_id, medicao, calculado_em))
                    if rc != 0:
                        raise PortaIndisponivel("gravacao do score falhou: %s" % (erro or saida))
                    marcas = [l.strip() for l in (saida or "").splitlines() if l.strip()]
                    if MARCA_ESCRITO in marcas:
                        resultado["veredito"] = VER_ESCRITO
                        resultado["score_id"] = score_id
                        resultado["calculated_at"] = calculado_em
                    elif MARCA_JA_EXISTE in marcas:
                        resultado["veredito"] = VER_JA_EXISTE
                        resultado["motivos"] = ["IDEMPOTENCIA_REPLAY"]
                    else:
                        raise PortaIndisponivel(
                            "gravacao sem marca conhecida (%s)" % (saida or "").strip()[:120])
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = VER_ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        status = STATUS_AGENT_RUNS[resultado["veredito"]]
        saida_auditoria = {k: v for k, v in resultado.items() if k != "entrada"}
        rc_run, saida_run, erro_run = self.porta.executar(sql_registrar_execucao(
            run_id, self.correlation_id, resultado.get("organization_id"), status,
            entrada_auditoria, saida_auditoria, inicio, fim, resultado.get("erro")))
        if rc_run != 0:
            # Fail-closed: a medicao NAO pode ser reportada como concluida quando a propria
            # auditoria nao foi escrita.
            resultado["veredito"] = VER_ERRO
            resultado["motivos"] = list(resultado.get("motivos") or []) + \
                ["AUDITORIA_NAO_REGISTRADA: %s" % (erro_run or saida_run)]
            resultado["erro"] = {"tipo": "PortaIndisponivel",
                                 "mensagem": "auditoria nao registrada: %s" % (erro_run or saida_run)}
            resultado["auditoria_registrada"] = False
            resultado["agent_run_id"] = run_id
            resultado["status_agent_runs"] = STATUS_AGENT_RUNS[VER_ERRO]
            return resultado
        resultado["agent_run_id"] = run_id
        resultado["auditoria_registrada"] = True
        resultado["status_agent_runs"] = status
        return resultado

    def rodar(self, entradas: list, planejar: bool = False) -> dict:
        resultados = []
        for entrada in entradas:
            resultados.append(self.planejar(entrada) if planejar
                              else self.processar(entrada))
        resumo = {"score": "%s/%s" % (SCORE, VERSAO), "ambiente": self.ambiente,
                  "correlation_id": self.correlation_id, "alvo": self.alvo,
                  "referencia_de_calculo": self.referencia.isoformat(),
                  "planejar": planejar, "total": len(resultados),
                  "por_veredito": {}, "resultados": resultados}
        for r in resultados:
            resumo["por_veredito"][r["veredito"]] = resumo["por_veredito"].get(r["veredito"], 0) + 1
        return resumo

    def desfazer(self, correlation_id: str, confirmo: bool = False) -> dict:
        rc, saida, erro = self.porta.executar(sql_selecionar_da_rodada(correlation_id))
        if rc != 0:
            raise PortaIndisponivel("nao consegui listar a rodada: %s" % (erro or saida))
        itens = []
        for linha in saida.splitlines():
            linha = linha.strip()
            if not linha or linha in ("BEGIN", "COMMIT"):
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if registro.get("score_id"):
                itens.append(registro)
        if not confirmo:
            return {"correlation_id": correlation_id, "confirmo": False,
                    "scores": [i["score_id"] for i in itens],
                    "organizacoes": sorted({i["organization_id"] for i in itens}),
                    "apagados": 0, "restaurados": 0, "dry_run": True}
        apagados = 0
        restaurados = 0
        if itens:
            rc, saida, erro = self.porta.executar(sql_desfazer(correlation_id),
                                                  permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
            for linha in saida.splitlines():
                linha = linha.strip()
                if not linha:
                    continue
                if linha.startswith("APAGADO"):
                    apagados += 1
                elif linha.startswith("RESTAURADO"):
                    restaurados += 1
        return {"correlation_id": correlation_id, "confirmo": True,
                "scores": [i["score_id"] for i in itens],
                "organizacoes": sorted({i["organization_id"] for i in itens}),
                "apagados": apagados, "restaurados": restaurados, "dry_run": False}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_entradas(caminho: str) -> list:
    if caminho == "-":
        texto = sys.stdin.read()
    else:
        p = Path(caminho)
        if not p.is_file():
            raise SystemExit(EXIT_FONTE)
        texto = p.read_text(encoding="utf-8")
    entradas = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        try:
            entradas.append(json.loads(linha))
        except json.JSONDecodeError as exc:
            raise SystemExit("FALHOU linha %d da fonte nao e JSON: %s" % (numero, exc))
    return entradas


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Score Data Quality v1 (TRE-W5-E04-T01)")
    p.add_argument("--todas", action="store_true", help="mede todas as organizacoes ativas")
    p.add_argument("--organizacao", action="append", default=[],
                   help="UUID de organizacao (repetivel)")
    p.add_argument("--fonte", help="arquivo jsonl com as organizacoes ('-' = stdin)")
    p.add_argument("--ambiente", help="dev | homolog (prod e recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true",
                   help="mede e reporta; NAO escreve (o score nao abre conexao de escrita)")
    p.add_argument("--referencia", default=None,
                   help="data de calculo AAAA-MM-DD (padrao: hoje UTC) — deixa a medicao reproduzivel")
    p.add_argument("--relatorio", help="caminho do relatorio JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrao e dry-run)")
    p.add_argument("--raiz", default=str(RAIZ_PADRAO), help="raiz do repo (contratos)")
    return p


def main(argv=None) -> int:
    args = montar_parser().parse_args(argv)
    if not args.todas and not args.organizacao and not args.fonte and not args.desfazer:
        print("uso: (--todas | --organizacao <uuid> | --fonte <arquivo.jsonl>) [--ambiente <amb>] "
              "[--planejar] | --desfazer <cid>")
        return EXIT_USO
    if args.fonte and (args.todas or args.organizacao):
        print("uso: --fonte nao combina com --todas/--organizacao")
        return EXIT_USO
    porta = None
    if not args.planejar:
        try:
            porta = PortaPsql(args.prefixo)
        except PortaIndisponivel as exc:
            print("FALHOU %s" % exc)
            return EXIT_USO
    try:
        agente = ScoreDataQuality(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                                  correlation_id=args.correlation_id, referencia=args.referencia)
    except ValueError as exc:
        print("FALHOU %s" % exc)
        return EXIT_USO
    # O ambiente e exigido em todo modo que ESCREVE. No modo de planejamento ele e opcional: se
    # declarado, e conferido; o caminho de planejamento simplesmente nao escreve.
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
            if args.fonte:
                entradas = ler_entradas(args.fonte)
            elif args.organizacao:
                entradas = [{"organizacao_id": o} for o in args.organizacao]
            else:
                entradas = [{"organizacao_id": o} for o in agente.ids_da_listagem()]
                if not entradas:
                    print("FALHOU nenhuma organizacao ativa no ambiente")
                    return EXIT_FALHOU
            if args.planejar:
                relatorio = agente.rodar(entradas, planejar=True)
            else:
                agente.identificar_alvo()
                relatorio = agente.rodar(entradas)
    except PortaIndisponivel as exc:
        print("FALHOU %s" % exc)
        return EXIT_FALHOU
    if args.relatorio:
        Path(args.relatorio).write_text(json.dumps(relatorio, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({"score": relatorio.get("score"), "ambiente": relatorio.get("ambiente"),
                      "correlation_id": relatorio["correlation_id"],
                      "referencia_de_calculo": relatorio.get("referencia_de_calculo"),
                      "alvo": relatorio.get("alvo"), "total": relatorio.get("total"),
                      "por_veredito": relatorio.get("por_veredito"),
                      "apagados": relatorio.get("apagados"),
                      "restaurados": relatorio.get("restaurados"),
                      "dry_run": relatorio.get("dry_run")}, ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-18s %-38s %-8s %s" % (r["veredito"], r.get("organization_id") or "-",
                                         r.get("valor") or "-",
                                         ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == VER_ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
