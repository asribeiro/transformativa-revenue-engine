#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agente Signal Detector v1 — deteccao de sinais de mudanca/demanda (card TRE-W4-E03-T01).

O que este agente FAZ (e so isto): recebe OBSERVACOES sobre empresas que JA existem no PostgreSQL
(o produtor da empresa e o Scout, TRE-W4-E01-T01; a pesquisa/enriquecimento e o Research,
TRE-W4-E02-T01), resolve a organizacao pelos identificadores FORTES do Data Contract V1.0
(cnpj -> domain -> linkedin_url), valida o `signal_type` contra o vocabulario fechado do contrato
(doc 03 §7) e GRAVA o sinal datado em `signals` — com a categoria DERIVADA do tipo e as evidencias
conservadas. E' o terceiro item da W4 (doc 07 §7) e o produtor que faltava para a W5 (Buying Signal
Score, TRE-W5-E03-T01).

O que ele NAO faz, por desenho (declarado em `agente-signal-v1.json` -> lacunas):
  - nao CRIA organizacao e nao escreve NENHUMA coluna de `organizations` (o sinal e aditivo:
    identidade e do Scout/dedup, estagio e do Odoo — doc 12 §1);
  - nao CALCULA score: `buying_signal_points`, `relevance_score`, `decay_factor` e `expires_at`
    ficam como o contrato os deixou (o Buying Signal Score e o card TRE-W5-E03-T01, com formula
    homologada propria). A guarda de escrita RECUSA quem tentar escrever essas colunas;
  - nao cria hipotese de dor nem contato (W4-E04/E05) e nao emite evento de outbox (nao existe
    evento de sinal no contrato — doc 06 §5);
  - nao faz requisicao de rede nenhuma (sem crawler, sem LLM na v1) e nao toca Odoo/Titan/n8n;
  - nao escreve em producao (ADR-005).

Regras que sustentam a deteccao:
  - **o tipo e do vocabulario fechado** (`tipos_de_sinal`, 18 valores = `vocabularies.signal_type`
    do contrato): tipo fora da lista e RECUSA, nao aviso;
  - **a categoria e DERIVADA** do tipo (`categorias_por_tipo`, tabela declarada e coberta por
    suite): categoria vinda da fonte e DESCARTADA com motivo, nunca escrita;
  - **a evidencia e conservada**: as fontes (tipo do canal + url + trecho) entram inteiras em
    `signals.evidence.fontes`, e a PRIMEIRA fonte declarada e a primaria (vira `source_type` /
    `source_url`); o que e descartado fica registrado com motivo (compliance do contrato);
  - **nada e inventado**: sem `confianca` na fonte a coluna fica `NULL`; sem titulo ela fica NULL
    (o DDL nao exige titulo); data invalida e descartada em vez de escrita torta;
  - **vinculo logico sem FK**: `signals.research_run_id` existe no DDL sem chave estrangeira; se o
    `research_run_id` declarado nao existir em `research_runs`, ele e descartado com motivo
    (`RESEARCH_RUN_NAO_ENCONTRADO`) e o sinal segue — o fato observado nao se perde por causa de um
    vinculo quebrado.

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"):
  - a chave e a ENTRADA, nao a rodada: `signal:org:<uuid>:<tipo>:<input_hash>`, gravada em
    `sync_events.idempotency_key` (UNIQUE). A ingestao e UMA transacao com dois comandos de escrita:
    claim da chave + INSERT do sinal ancorado no claim (replay nao insere) -> fechamento do
    sync_event ANCORADO no sinal DESTA rodada (replay nao marca sucesso). Replay: o claim volta
    vazio, nada e inserido e o veredito vira JA_DETECTADO;
  - a historia de cada observacao fica em `agent_runs` (uma linha por observacao, com o
    `correlation_id` do lote) — auditoria nao depende da narrativa de quem rodou.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/agents/signal/signal.py --planejar --fonte observacoes.jsonl
  python3 hermes/agents/signal/signal.py --ambiente dev --fonte observacoes.jsonl \
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
      --relatorio /tmp/signal-rodada.json
  python3 hermes/agents/signal/signal.py --desfazer <correlation_id>
  python3 hermes/agents/signal/signal.py --desfazer <correlation_id> --confirmo
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

# ---------------------------------------------------------------------------------------
# Identidade do agente (espelha `hermes/agents/signal/agente-signal-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "signal"
PAPEL = "signal_detection"
VERSAO = "1.0.0"
WORKFLOW = "deteccao-de-sinais"
WORKFLOW_VERSAO = "v1"

TABELA_SINAIS = "sales_intelligence.signals"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_RESEARCH_RUNS = "sales_intelligence.research_runs"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"
TABELA_APPROVALS = "sales_intelligence.human_approvals"
# `organizations` e `research_runs` NAO sao tabelas de escrita do detector: aparecem aqui apenas
# como leitura declarada (identidade e vinculo logico). Quem escreve nelas e o Scout e o Research.
TABELAS_PERMITIDAS = (TABELA_SINAIS, TABELA_AGENT_RUNS, TABELA_SYNC_EVENTS, TABELA_APPROVALS)
TABELAS_LIDAS = (TABELA_ORGANIZACOES, TABELA_RESEARCH_RUNS)

ACTION_TYPE_REVISAO = "SIGNAL_IDENTITY_REVIEW"
OPERACAO_SYNC = "SIGNAL"
MARCA_DETECTADO = "SIGNAL_DETECTADO"
# Revisao de identidade ambigua: operacao/entidade proprias em sync_events (o contrato nao fecha
# vocabulario para essas colunas) e marca propria, para que o fechamento prove a linha da fila.
OPERACAO_REVISAO = "SIGNAL_REVIEW"
ENTITY_TYPE_REVISAO = "signal_review"
MARCA_REVISAO = "SIGNAL_REVISAO_REGISTRADA"

# Tipo de sinal: vocabulario FECHADO do contrato (`vocabularies.signal_type`, doc 03 §7).
TIPOS_DE_SINAL = (
    "GROWTH",
    "HIRING",
    "NEW_EXECUTIVE",
    "NEW_LOCATION",
    "ERP_CHANGE",
    "CRM_CHANGE",
    "DIGITAL_TRANSFORMATION",
    "AI_INITIATIVE",
    "M_AND_A",
    "NEW_PRODUCT",
    "TECH_ADOPTION",
    "FUNDING",
    "PROCESS_COMPLEXITY",
    "CUSTOMER_COMPLAINT",
    "SERVICE_VOLUME",
    "REGULATORY_CHANGE",
    "EFFICIENCY_PROGRAM",
    "COST_REDUCTION",
)

# Categoria: DERIVADA do tipo (nunca aceita da fonte). A tabela cobre o vocabulario exatamente uma
# vez — a suite compara tipo a tipo com o vocabulario do contrato.
CATEGORIAS_POR_TIPO = {
    "GROWTH": "EXPANSAO",
    "HIRING": "EXPANSAO",
    "NEW_LOCATION": "EXPANSAO",
    "NEW_PRODUCT": "EXPANSAO",
    "FUNDING": "EXPANSAO",
    "M_AND_A": "CORPORATIVO",
    "NEW_EXECUTIVE": "CORPORATIVO",
    "ERP_CHANGE": "TECNOLOGIA",
    "CRM_CHANGE": "TECNOLOGIA",
    "TECH_ADOPTION": "TECNOLOGIA",
    "DIGITAL_TRANSFORMATION": "TECNOLOGIA",
    "AI_INITIATIVE": "TECNOLOGIA",
    "PROCESS_COMPLEXITY": "PRESSAO_OPERACIONAL",
    "CUSTOMER_COMPLAINT": "PRESSAO_OPERACIONAL",
    "SERVICE_VOLUME": "PRESSAO_OPERACIONAL",
    "EFFICIENCY_PROGRAM": "EFICIENCIA",
    "COST_REDUCTION": "EFICIENCIA",
    "REGULATORY_CHANGE": "REGULATORIO",
}

# Colunas de `signals` que o detector ESCREVE (ordem do INSERT). Fora desta lista a guarda recusa.
COLUNAS_DO_SINAL = (
    "id",
    "organization_id",
    "signal_type",
    "signal_category",
    "title",
    "description",
    "source_type",
    "source_url",
    "event_date",
    "detected_at",
    "confidence",
    "evidence",
    "research_run_id",
    "created_at",
)

# Colunas de `signals` que o detector NAO escreve: a forca/atualidade do sinal (pontos, relevancia
# e decaimento) e o Buying Signal Score — card TRE-W5-E03-T01. Escrever uma delas e recusa.
COLUNAS_PROIBIDAS = ("relevance_score", "buying_signal_points", "decay_factor", "expires_at")

# Campos aceitos na entrada. Qualquer outro campo e descartado com motivo (CAMPO_NAO_DECLARADO).
CAMPOS_DA_OBSERVACAO = ("organizacao", "tipo", "titulo", "descricao", "fontes", "data_do_evento",
                        "confianca", "research_run_id", "categoria")

LIMITE_TITULO = 500
LIMITE_URL = 512
LIMITE_TRECHO = 2000
LIMITE_DESCRICAO = 8000

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

DETECTADO = "DETECTADO"
JA_DETECTADO = "JA_DETECTADO"
REVISAO = "REVISAO_IDENTIDADE"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (DETECTADO, JA_DETECTADO, REVISAO, RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    DETECTADO: "COMPLETED",
    JA_DETECTADO: "COMPLETED",
    REVISAO: "REVIEW_REQUIRED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

# Vereditos do modo `--planejar` (nenhuma conexao de banco e feita nele)
PLANEJADO_DETECTAR = "PLANEJADO_DETECTAR"
PLANEJADO_REVISAR = "PLANEJADO_REVISAR"
PLANEJADO_RECUSAR = "PLANEJADO_RECUSAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

# Vocabulario do recibo do JEV (hermes/jev/routing/router.py: OUTCOME_*)
JEV_EXECUTAR = "PASS"
LANES = ("small", "medium", "high", "critical")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4
EXIT_FONTE = 5

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_AGENTE_PADRAO = "hermes/agents/signal/agente-signal-v1.json"
# A identidade de empresa e UMA regra so neste projeto (normalizacao e digito verificador do CNPJ,
# dominio canonico, LinkedIn canonico). O detector IMPORTA a regra do produtor (Scout) em vez de
# manter uma segunda copia dela — copia de regra de identidade e' divergencia esperando acontecer,
# e a suite reprova se aparecer uma segunda implementacao aqui.
MODULO_IDENTIDADE = "hermes/agents/scout/scout.py"

_UUID_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                      re.I)
_DATA_ISO = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d{1,6})?(Z|[+-]\d{2}:?\d{2})?)?$")


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR — nunca pela profundidade do arquivo.

    A copia mutada (autoteste) e o teste de importacao vivem em diretorio raso (as vezes `/tmp`),
    onde `Path(__file__).resolve().parents[3]` estoura `IndexError` ja na importacao. A raiz e o
    primeiro ancestral que contem o contrato DESTE agente; o diretorio de trabalho entra como
    segunda tentativa, porque a copia sob teste nao esta na arvore do repo.
    """
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():
                return pasta
    return Path.cwd()


RAIZ_PADRAO = descobrir_raiz_padrao()


class RecusaDeAmbiente(Exception):
    """Ambiente nao permitido (ADR-005): nada e escrito."""


class ReciboJEVInvalido(Exception):
    """Chamada de LLM sem recibo valido do JEV: fail-closed (doc 07 §7, W4)."""


class GuardaDeEscritaViolada(Exception):
    """SQL tentando escrever fora das tabelas/colunas/operacoes declaradas."""


class PortaIndisponivel(Exception):
    """A porta de banco falhou."""


# ---------------------------------------------------------------------------------------
# Contratos e regra de identidade importada do produtor
# ---------------------------------------------------------------------------------------
def carregar_json(caminho) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_contrato_do_agente(raiz) -> dict:
    return carregar_json(Path(raiz) / CONTRATO_AGENTE_PADRAO)


def fortes_do_contrato_de_dados(raiz) -> tuple:
    """A prioridade dos identificadores fortes vem do Data Contract V1.0, nao do codigo."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    return tuple(contrato["dedup"]["strong"])


def carregar_identidade(raiz):
    """Carrega a regra de identidade do Scout (o produtor da empresa) por caminho.

    Import tardio de proposito: o modulo e localizado pela raiz do repo, entao a copia do agente
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
# Normalizacao e validacao da observacao
# ---------------------------------------------------------------------------------------
def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def categoria_do_tipo(tipo: str):
    """Categoria DERIVADA do tipo — nunca aceita da fonte (dado derivado nao se aceita de fora)."""
    return CATEGORIAS_POR_TIPO.get(tipo)


def identificadores_validos(observacao: dict, fortes: tuple, identidade) -> list:
    """[(tipo, valor_normalizado)] na ordem de prioridade do contrato; invalido e descartado."""
    organizacao = observacao.get("organizacao") or {}
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


def identificadores_declarados(observacao: dict, fortes: tuple) -> list:
    organizacao = observacao.get("organizacao") or {}
    if not isinstance(organizacao, dict):
        return []
    return [t for t in fortes if _texto(organizacao.get(t))]


def validar_fontes(observacao: dict) -> tuple:
    """>=1 fonte do vocabulario, com evidencia. Devolve (fontes_normalizadas, problemas)."""
    fontes = observacao.get("fontes")
    if not isinstance(fontes, list) or not fontes:
        return [], ["SEM_FONTE"]
    normalizadas = []
    problemas = []
    for fonte in fontes:
        if not isinstance(fonte, dict):
            problemas.append("FONTE_ILEGIVEL")
            continue
        tipo = _texto(fonte.get("tipo")).upper()
        if tipo not in FONTES:
            problemas.append("FONTE_DESCONHECIDA")
            continue
        url = _texto(fonte.get("url")) or None
        if url and (len(url) > LIMITE_URL or not re.match(r"^https?://[^\s]+$", url)):
            problemas.append("URL_DE_FONTE_INVALIDA")
            continue
        trecho = _texto(fonte.get("trecho")) or None
        if trecho and len(trecho) > LIMITE_TRECHO:
            problemas.append("TRECHO_ACIMA_DO_LIMITE")
            continue
        normalizadas.append({"tipo": tipo, "url": url, "trecho": trecho})
    if not normalizadas:
        return [], problemas or ["SEM_FONTE"]
    return normalizadas, problemas


def validar_titulo(valor) -> tuple:
    texto = _texto(valor)
    if not texto:
        return None, None
    if len(texto) > LIMITE_TITULO:
        return None, "TITULO_ACIMA_DO_LIMITE"
    return texto, None


def validar_descricao(valor) -> tuple:
    texto = _texto(valor)
    if not texto:
        return None, None
    if len(texto) > LIMITE_DESCRICAO:
        return None, "DESCRICAO_ACIMA_DO_LIMITE"
    return texto, None


def validar_data(valor) -> tuple:
    """Data do evento em ISO-8601 **estendido** (YYYY-MM-DD[Thh:mm[:ss]][Z|±hh:mm]).

    Fora do formato declarado e' DESCARTADA com motivo, nunca adivinhada nem escrita torta: o
    detector nao aceita a forma basica (`20260928`) nem separador de barra (`28/09/2026`) — quem
    normaliza formato e' a fonte, nao o agente.
    """
    texto = _texto(valor)
    if not texto:
        return None, None
    if not _DATA_ISO.match(texto):
        return None, "DATA_INVALIDA"
    try:
        datetime.fromisoformat(texto.replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None, "DATA_INVALIDA"
    return texto, None


def validar_confianca(valor) -> tuple:
    if valor in (None, ""):
        return None, None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None, "CONFIANCA_NAO_E_NUMERO"
    if not 0 <= numero <= 1:
        return None, "CONFIANCA_FORA_DA_FAIXA"
    return round(numero, 4), None


def validar_research_run_id(valor) -> tuple:
    """Vinculo logico (sem FK no DDL): o FORMATO e conferido aqui; a EXISTENCIA, no banco."""
    texto = _texto(valor)
    if not texto:
        return None, None
    if not _UUID_V4.match(texto):
        return None, "RESEARCH_RUN_ID_INVALIDO"
    return texto.lower(), None


def validar_observacao(observacao: dict, fortes: tuple, identidade) -> dict:
    """Validacao fora do banco: identidade, tipo, fontes, titulo, data, confianca e vinculo.

    Nao decide veredito (isso e `decidir_veredito`). O que e invalido mas nao impede a deteccao
    (titulo longo, data torta, categoria declarada, campo inventado) vira DESCARTE COM MOTIVO.
    """
    descartados = []
    if not isinstance(observacao, dict):
        return {"problemas": ["OBSERVACAO_ILEGIVEL"], "validos": [], "declarados": [], "tipo": "",
                "categoria": None, "titulo": None, "descricao": None, "fontes": [],
                "data_do_evento": None, "confianca": None, "research_run_id": None,
                "descartados": []}
    problemas = []
    tipo = _texto(observacao.get("tipo")).upper()
    if not tipo:
        problemas.append("SEM_TIPO_DE_SINAL")
    elif tipo not in TIPOS_DE_SINAL:
        problemas.append("TIPO_DE_SINAL_DESCONHECIDO")
    fontes, problemas_fontes = validar_fontes(observacao)
    problemas.extend(problemas_fontes)
    declarados = identificadores_declarados(observacao, fortes)
    validos = identificadores_validos(observacao, fortes, identidade)
    if declarados and not validos:
        problemas.append("IDENTIFICADOR_FORTE_INVALIDO")
    elif not validos:
        problemas.append("SEM_IDENTIFICADOR_FORTE")
    titulo, motivo_titulo = validar_titulo(observacao.get("titulo"))
    if motivo_titulo:
        descartados.append({"campo": "titulo", "motivo": motivo_titulo,
                            "valor": observacao.get("titulo")})
    descricao, motivo_descricao = validar_descricao(observacao.get("descricao"))
    if motivo_descricao:
        descartados.append({"campo": "descricao", "motivo": motivo_descricao})
    data, motivo_data = validar_data(observacao.get("data_do_evento"))
    if motivo_data:
        descartados.append({"campo": "data_do_evento", "motivo": motivo_data,
                            "valor": observacao.get("data_do_evento")})
    confianca, motivo_confianca = validar_confianca(observacao.get("confianca"))
    if motivo_confianca:
        descartados.append({"campo": "confianca", "motivo": motivo_confianca,
                            "valor": observacao.get("confianca")})
    research_run_id, motivo_run = validar_research_run_id(observacao.get("research_run_id"))
    if motivo_run:
        descartados.append({"campo": "research_run_id", "motivo": motivo_run,
                            "valor": observacao.get("research_run_id")})
    if "categoria" in observacao and _texto(observacao.get("categoria")):
        # A categoria e DERIVADA do tipo: a que a fonte declarou nao e escrita.
        descartados.append({"campo": "categoria", "motivo": "DERIVADO_NAO_ACEITO",
                            "valor": observacao.get("categoria")})
    for campo in observacao:
        if campo not in CAMPOS_DA_OBSERVACAO:
            descartados.append({"campo": campo, "motivo": "CAMPO_NAO_DECLARADO",
                                "valor": observacao.get(campo)})
    return {"problemas": problemas, "validos": validos, "declarados": declarados, "tipo": tipo,
            "categoria": categoria_do_tipo(tipo), "titulo": titulo, "descricao": descricao,
            "fontes": fontes, "data_do_evento": data, "confianca": confianca,
            "research_run_id": research_run_id, "descartados": descartados}


def hash_da_entrada(identidades: list, validacao: dict) -> str:
    """Chave da idempotencia: a ENTRADA canonica (nao a rodada).

    Ordem de chaves fixa e JSON sem espacos: a MESMA observacao apresentada de novo tem de produzir
    o MESMO hash — e' isso que faz o retry nao criar duplicata.
    """
    canonico = {
        "identidades": [[t, v] for t, v in identidades],
        "tipo": validacao["tipo"],
        "titulo": validacao["titulo"],
        "descricao": validacao["descricao"],
        "fontes": [{"tipo": f["tipo"], "url": f["url"], "trecho": f["trecho"]}
                   for f in validacao["fontes"]],
        "data_do_evento": validacao["data_do_evento"],
        "confianca": validacao["confianca"],
        "research_run_id": validacao["research_run_id"],
    }
    return _sha256(json.dumps(canonico, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def chave_idempotencia(organizacao_id: str, tipo: str, entrada_hash: str) -> str:
    return "signal:org:%s:%s:%s" % (organizacao_id, tipo, entrada_hash)


def chave_da_revisao(organizacoes_ids: list, entrada_hash: str) -> str:
    """Chave da requisicao na fila humana: as candidatas ordenadas + a entrada (retry nao duplica)."""
    return "signal:revisao:%s:%s" % (",".join(sorted(organizacoes_ids)), entrada_hash)


def decidir_veredito(problemas: list, validos: list, organizacoes_casadas: list) -> tuple:
    """Decisao pura (testavel sem banco) — a regra vive aqui e so aqui.

    O detector NAO cria empresa: identidade que nao casa e recusa, nao descoberta. Ambiguidade
    (dois ou mais casamentos distintos) vai para a fila humana, nunca e resolvida por heuristica
    (`dedup.rule` do contrato).
    """
    if problemas:
        return RECUSADA, problemas[0]
    if not validos:
        return RECUSADA, "SEM_IDENTIFICADOR_FORTE"
    if len(organizacoes_casadas) == 0:
        return RECUSADA, "ORGANIZACAO_NAO_ENCONTRADA"
    if len(organizacoes_casadas) == 1:
        return DETECTADO, None
    return REVISAO, "CONFLITO_DE_IDENTIDADE_FORTE"


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o agente pode e nao pode escrever)
# ---------------------------------------------------------------------------------------
_LITERAIS = None  # preenchido por `preparar_literais` (lit/lit_json vem do modulo de identidade)


def preparar_literais(identidade) -> None:
    """`lit`/`lit_json` sao importados do produtor: uma unica implementacao de escape de literal."""
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

    Titulo/descricao de sinal nao sao instrucao: um trecho com a palavra "drop" ('queda de
    receita') faria a guarda recusar sinal legitimo como se fosse DDL. Quem escreve dado e o
    `lit()`, que escapa apostrofo dobrando — entao a varredura olha o codigo, nao a prosa.
    """
    return _LITERAL.sub("''", sql)


def colunas_do_insert(sql: str, tabela: str) -> list:
    """Listas de colunas de cada `INSERT INTO <tabela> (...)` do SQL (para a guarda do sinal)."""
    padrao = re.compile(_INSERT_COLUNAS.pattern % re.escape(tabela), re.I)
    return [[c.strip().lower() for c in bloco.split(",") if c.strip()]
            for bloco in padrao.findall(sql)]


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL e escrita fora do declarado.

    Alem das regras de tabela/operacao, o INSERT de `signals` tem duas exigencias de DESENHO:
    (a) declara `organization_id` e `signal_type` (NOT NULL do DDL — sinal sem empresa ou sem tipo
    nao existe); (b) NAO declara nenhuma coluna de score/decaimento/expiracao (isso e W5). As duas
    sao medidas por mutacao na suite e pelo dente do aceite.
    """
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL nao e permitido ao agente Signal Detector")
    for colunas in colunas_do_insert(codigo, TABELA_SINAIS):
        faltando = [c for c in ("organization_id", "signal_type") if c not in colunas]
        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT de sinal sem coluna obrigatoria do DDL: %s" % ", ".join(faltando))
        proibidas = [c for c in colunas if c in COLUNAS_PROIBIDAS]
        if proibidas:
            raise GuardaDeEscritaViolada(
                "INSERT de sinal com coluna de score/decaimento (W5): %s" % ", ".join(proibidas))
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela == TABELA_ORGANIZACOES:
                raise GuardaDeEscritaViolada(
                    "%s em organizations nao e permitido: a deteccao de sinal nao escreve na "
                    "empresa (o sinal e aditivo — quem enriquece a organizacao e o Research)"
                    % operacao.upper())
            if tabela not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela nao declarada: %s" % tabela)
            if operacao == "delete" and not permitir_remocao:
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
        # `-q` evita que os carimbos de comando (BEGIN/COMMIT) poluam a saida; o resultado e
        # conferido LINHA a linha, porque a porta nao promete formato.
        comando = self.prefixo + ["-v", "ON_ERROR_STOP=1", "-q", "-tA", "-F", "|"]
        try:
            p = subprocess.run(comando, input=sql, capture_output=True, text=True,
                               timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PortaIndisponivel("porta psql falhou: %s" % exc)
        return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------------------
# SQL do agente
# ---------------------------------------------------------------------------------------
def sql_consultar_organizacao(validos: list) -> str:
    """Identidade -> organizacoes casadas, em JSON (o texto da empresa nao quebra a leitura)."""
    termos = ["(%s IS NOT NULL AND %s = %s)" % (tipo, tipo, lit(valor)) for tipo, valor in validos]
    campos = ["'id'", "id::text", "'status'", "COALESCE(status, '')",
              "'legal_name'", "COALESCE(legal_name, '')", "'trade_name'", "COALESCE(trade_name, '')"]
    return (
        "SELECT jsonb_build_object(%s)::text FROM %s "
        "WHERE deleted_at IS NULL AND (%s) ORDER BY id;" % (
            ", ".join(campos), TABELA_ORGANIZACOES, " OR ".join(termos))
    )


def sql_research_runs_existentes(ids: list) -> str:
    """Vinculo logico (sem FK): quais dos research_run_ids declarados existem de fato."""
    lista = ", ".join(lit(i) for i in ids)
    return "SELECT id::text FROM %s WHERE id IN (%s);" % (TABELA_RESEARCH_RUNS, lista)


def montar_linha_sinal(sinal_id: str, valores: dict) -> tuple:
    """Colunas x valores do INSERT de `signals` — a paridade e conferida contra o DDL pela suite."""
    colunas = list(COLUNAS_DO_SINAL)
    expressoes = [
        lit(sinal_id),                                # id
        lit(valores["organization_id"]),              # organization_id (empresa que JA existe)
        lit(valores["signal_type"]),                  # signal_type
        lit(valores["signal_category"]),              # signal_category (DERIVADA)
        lit(valores["title"]) if valores["title"] is not None else "NULL",        # title
        lit(valores["description"]) if valores["description"] is not None else "NULL",
        lit(valores["source_type"]),                  # source_type (fonte primaria)
        lit(valores["source_url"]) if valores["source_url"] is not None else "NULL",
        lit(valores["event_date"]) if valores["event_date"] is not None else "NULL",
        lit(valores["detected_at"]),                  # detected_at (relogio da rodada)
        lit(valores["confidence"]) if valores["confidence"] is not None else "NULL",
        lit_json(valores["evidence"]),                # evidence (evidencia conservada)
        lit(valores["research_run_id"]) if valores["research_run_id"] is not None else "NULL",
        "now()",                                      # created_at
    ]
    if len(expressoes) != len(colunas):
        raise ValueError("montagem do INSERT de signals divergente: %d colunas x %d valores"
                         % (len(colunas), len(expressoes)))
    return colunas, expressoes


def sql_ingerir(sinal_id: str, sync_event_id: str, chave: str,
                valores: dict, payload: dict) -> str:
    """Uma transacao, DOIS comandos de escrita: claim+INSERT do sinal -> fechamento ancorado.

    Por que dois comandos e nao um so: as CTEs de escrita e a instrucao principal rodam no MESMO
    snapshot, entao a instrucao principal NAO enxerga a linha que a CTE acabou de inserir (defeito
    medido no aceite E2E do Scout). Aqui:
      1. `WITH claim AS (INSERT sync_events ... ON CONFLICT DO NOTHING RETURNING id)` +
         `INSERT signals SELECT ... FROM claim ON CONFLICT (id) DO NOTHING RETURNING id` — replay
         nao insere sinal nenhum;
      2. `UPDATE sync_events SET status='SUCCESS' ... AND EXISTS (SELECT 1 FROM signals WHERE
         id = <sinal desta rodada>) RETURNING 'SIGNAL_DETECTADO'` — o fechamento so marca sucesso
         se o sinal DESTA rodada existe; ausente = replay (a chave ja estava reivindicada).

    Nao existe o terceiro comando do Research (UPDATE de enriquecimento em `organizations`): o
    sinal e ADITIVO — a deteccao nao escreve nenhuma coluna de empresa. E' o desenho, nao uma
    economia.
    """
    colunas, expressoes = montar_linha_sinal(sinal_id, valores)
    comandos = [
        "BEGIN;",
        "WITH claim AS (\n"
        "  INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, created_at)\n"
        "  VALUES ({sevid}, 'signal', {sid}, 'signal', 'postgresql', {operacao}, "
        "{versao}, {chave}, 'PENDING', {payload}, now())\n"
        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {sinais} ({cols})\n"
        "SELECT {vals} FROM claim\n"
        "ON CONFLICT (id) DO NOTHING\n"
        "RETURNING id;".format(
            sync=TABELA_SYNC_EVENTS, sinais=TABELA_SINAIS, sevid=lit(sync_event_id),
            sid=lit(sinal_id), operacao=lit(OPERACAO_SYNC), versao=lit(VERSAO),
            chave=lit(chave), payload=lit_json(payload), cols=", ".join(colunas),
            vals=", ".join(expressoes)),
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(),\n"
        "  response_payload = jsonb_build_object('signal_id', {sid}, 'veredito', "
        "'DETECTADO', 'idempotency_key', {chave})\n"
        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {sinais} WHERE id = {sid})\n"
        "RETURNING {marca};".format(sync=TABELA_SYNC_EVENTS, sinais=TABELA_SINAIS,
                                    sid=lit(sinal_id), chave=lit(chave),
                                    marca=lit(MARCA_DETECTADO)),
        "COMMIT;",
    ]
    return "\n".join(comandos) + "\n"


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


def sql_pedir_revisao(approval_id: str, sync_event_id: str, chave: str, observacao: dict, motivo: str,
                      casadas: list, correlation_id: str) -> str:
    """Requisicao da fila humana — mesmo desenho idempotente do sinal (claim + fechamento).

    A chave e' a da observacao com o sufixo `:revisao`: a MESMA ambiguidade reapresentada nao abre
    pedido duplicado para o humano (o claim ja existe), e o fechamento so' marca sucesso quando a
    linha da fila existe de verdade — auditoria nao conta narrativa, conta linha.
    """
    proposta = {
        "motivo": motivo,
        "agente": "%s/%s" % (AGENTE, VERSAO),
        "correlation_id": correlation_id,
        "observacao": observacao,
        "organizacoes_casadas": casadas,
        "regra": "ambiguidade nao e resolvida por heuristica: e reportada (dedup.rule do contrato)",
    }
    payload = {"origem": AGENTE, "agente": "%s/%s" % (AGENTE, VERSAO), "motivo": motivo,
               "correlation_id": correlation_id, "human_approval_id": approval_id,
               "organizacoes_casadas": casadas, "idempotency_key": chave}
    resposta = {"motivo": motivo, "human_approval_id": approval_id, "idempotency_key": chave}
    return (
        "BEGIN;\n"
        "WITH claim AS (\n"
        "  INSERT INTO {sync} (id, entity_type, entity_id, source_system, operation,\n"
        "                      source_version, idempotency_key, status, request_payload, created_at)\n"
        "  VALUES ({seid}, {tipo_revisao}, NULL, {origem}, {operacao}, {versao}, {chave},\n"
        "          'PENDING', {payload}, now())\n"
        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {approvals} (id, action_type, entity_type, entity_id, requested_by,\n"
        "                         proposed_action, status, requested_at)\n"
        "SELECT {aid}, {acao}, {tipo_revisao}, NULL, {quem}, {proposta}, 'PENDING', now()\n"
        "  FROM claim\n"
        "RETURNING id;\n"
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(), response_payload = {resposta}\n"
        " WHERE idempotency_key = {chave}\n"
        "   AND EXISTS (SELECT 1 FROM {approvals} WHERE id = {aid})\n"
        "RETURNING {marca};\n"
        "COMMIT\n"
    ).format(sync=TABELA_SYNC_EVENTS, approvals=TABELA_APPROVALS, seid=lit(sync_event_id),
             aid=lit(approval_id), acao=lit(ACTION_TYPE_REVISAO), quem=lit("%s/%s" % (AGENTE, VERSAO)),
             tipo_revisao=lit(ENTITY_TYPE_REVISAO), operacao=lit(OPERACAO_REVISAO),
             origem=lit("hermes"), versao=lit(VERSAO), chave=lit(chave),
             payload=lit_json(payload), proposta=lit_json(proposta), resposta=lit_json(resposta),
             marca=lit(MARCA_REVISAO))


def sql_selecionar_da_rodada(correlation_id: str) -> str:
    """O que a rodada criou: os signals (veredito DETECTADO) e os sync_events deles."""
    return (
        "SELECT jsonb_build_object(\n"
        "  'signal_id', s.id::text,\n"
        "  'organization_id', s.organization_id::text,\n"
        "  'sync_event_id', e.id::text\n"
        ")::text\n"
        "FROM {sinais} s\n"
        "JOIN {sync} e ON e.entity_id = s.id AND e.operation = {operacao}\n"
        "JOIN {audit} a ON (a.output ->> 'signal_id') = s.id::text\n"
        "WHERE a.correlation_id = {corr} AND a.agent_name = {agente}\n"
        "  AND a.output ->> 'veredito' = {veredito}\n"
        "ORDER BY s.id;\n"
    ).format(sinais=TABELA_SINAIS, sync=TABELA_SYNC_EVENTS, audit=TABELA_AGENT_RUNS,
             operacao=lit(OPERACAO_SYNC), corr=lit(correlation_id), agente=lit(AGENTE),
             veredito=lit(DETECTADO))


def sql_desfazer(itens: list, correlation_id: str, sync_event_id: str) -> str:
    """Apaga o que a rodada criou (sinais + sync_events deles). Nada em empresa, nada em auditoria.

    Nao ha valor ANTERIOR a restaurar: o sinal e uma LINHA NOVA (aditivo). O que prova a posse da
    linha pela rodada e o `sync_events` da chave — e' por ele que o DELETE chega no sinal certo.
    """
    ids_sinais = ", ".join(lit(i["signal_id"]) for i in itens if i.get("signal_id"))
    ids_sync = ", ".join(lit(i["sync_event_id"]) for i in itens if i.get("sync_event_id"))
    comandos = ["BEGIN;"]
    if ids_sinais:
        comandos.append("DELETE FROM {sinais} WHERE id IN ({ids});".format(
            sinais=TABELA_SINAIS, ids=ids_sinais))
    if ids_sync:
        comandos.append("DELETE FROM {sync} WHERE id IN ({ids});".format(
            sync=TABELA_SYNC_EVENTS, ids=ids_sync))
    payload = {"motivo": "desfazer da rodada de deteccao de sinais",
               "correlation_id": correlation_id,
               "signals": [i["signal_id"] for i in itens]}
    comandos.append(
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, operation, "
        "source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'signal', NULL, 'signal', 'postgresql', 'ROLLBACK', {versao}, "
        "{chave}, 'SUCCESS', {payload}, now());".format(
            sync=TABELA_SYNC_EVENTS, sevid=lit(sync_event_id), versao=lit(VERSAO),
            chave=lit("signal:rollback:%s" % correlation_id), payload=lit_json(payload)))
    comandos.append("COMMIT;")
    return "\n".join(comandos) + "\n"


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
        "motivo": "v1 do Signal Detector e deterministica: nenhuma chamada de LLM e feita",
    }


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resumo_da_deteccao(validacao: dict, fonte_primaria: dict) -> str:
    """Resumo determinístico: nenhuma frase e inferencia, e o que nao foi escrito esta declarado."""
    return (
        "Sinal %s (categoria derivada %s, deterministico, sem LLM): fonte primaria %s; "
        "%d fonte(s) conservada(s); %d descarte(s) com motivo; data do evento %s; confianca %s."
        % (validacao["tipo"], validacao["categoria"], fonte_primaria["tipo"],
           len(validacao["fontes"]), len(validacao["descartados"]),
           validacao["data_do_evento"] or "-",
           validacao["confianca"] if validacao["confianca"] is not None else "NULL")
    )


# ---------------------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------------------
class Signal:
    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None,
                 identidade=None):
        self.raiz = Path(raiz or RAIZ_PADRAO)
        self.contrato = carregar_contrato_do_agente(self.raiz)
        self.fortes = fortes_do_contrato_de_dados(self.raiz)
        if list(self.fortes) != list(self.contrato["identidade"]["fortes_por_prioridade"]):
            raise ValueError("divergencia entre o contrato do agente e o Data Contract V1.0: %s x %s"
                             % (list(self.fortes),
                                self.contrato["identidade"]["fortes_por_prioridade"]))
        if list(self.contrato["tipos_de_sinal"]) != list(TIPOS_DE_SINAL):
            raise ValueError("divergencia no vocabulario de tipos de sinal")
        if dict(self.contrato["categorias_por_tipo"]) != dict(CATEGORIAS_POR_TIPO):
            raise ValueError("divergencia na tabela de categorias derivadas")
        if list(self.contrato["escrita_sinal"]["colunas_proibidas"]) != list(COLUNAS_PROIBIDAS):
            raise ValueError("divergencia nas colunas proibidas (score/decaimento)")
        self.identidade = identidade if identidade is not None else carregar_identidade(self.raiz)
        preparar_literais(self.identidade)
        self.porta = porta if porta is not None else PortaAusente()
        self.relogio = relogio
        self.ambiente = ambiente
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.alvo = None

    # -- ambiente ---------------------------------------------------------------------
    def conferir_ambiente(self) -> str:
        if self.ambiente in (None, ""):
            raise RecusaDeAmbiente("ambiente nao declarado: recusa (fail-closed)")
        if self.ambiente == AMBIENTE_RECUSADO:
            raise RecusaDeAmbiente(
                "Signal Detector v1 nao escreve em prod (ADR-005): a promocao exige card proprio "
                "com aprovacao humana registrada")
        if self.ambiente not in AMBIENTES_PERMITIDOS:
            raise RecusaDeAmbiente("ambiente desconhecido: %r" % self.ambiente)
        return self.ambiente

    def identificar_alvo(self) -> dict:
        """Registra a identidade do alvo medido (sem afirmar nome de ambiente — a convencao de
        nome divergiu no dev e alinhar isso e decisao do dono, nao do agente)."""
        rc, saida, erro = self.porta.executar(
            "SELECT current_database(), current_user, version();")
        if rc != 0:
            raise PortaIndisponivel("nao consegui ler a identidade do alvo: %s" % (erro or saida))
        campos = saida.strip().split("|")
        self.alvo = {"banco": campos[0] if campos else None,
                     "usuario": campos[1] if len(campos) > 1 else None,
                     "servidor": (campos[2] if len(campos) > 2 else "")[:80]}
        return self.alvo

    # -- observacao -------------------------------------------------------------------
    def organizacoes_casadas(self, validos: list) -> list:
        if not validos:
            return []
        rc, saida, erro = self.porta.executar(sql_consultar_organizacao(validos))
        if rc != 0:
            raise PortaIndisponivel("consulta de organizacao falhou: %s" % (erro or saida))
        casadas = []
        for linha in saida.splitlines():
            linha = linha.strip()
            if not linha or linha in ("BEGIN", "COMMIT"):
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if not registro.get("id"):
                continue
            casadas.append(registro)
        return casadas

    def research_run_existe(self, run_id: str) -> bool:
        """Vinculo logico sem FK: o detector CONFERE a existencia antes de gravar o vinculo."""
        rc, saida, erro = self.porta.executar(sql_research_runs_existentes([run_id]))
        if rc != 0:
            raise PortaIndisponivel("consulta de research_run falhou: %s" % (erro or saida))
        return run_id in [linha.strip() for linha in saida.splitlines() if linha.strip()]

    def planejar(self, observacao: dict) -> dict:
        """Modo sem banco: valida e diz o que FARIA."""
        validacao = validar_observacao(observacao, self.fortes, self.identidade)
        if validacao["problemas"]:
            veredito = PLANEJADO_RECUSAR
        else:
            veredito = PLANEJADO_DETECTAR
        identidades = validacao["validos"]
        return {"veredito": veredito, "motivos": validacao["problemas"], "tipo": validacao["tipo"],
                "categoria_derivada": validacao["categoria"],
                "identidades_validas": [{"tipo": t, "valor": v} for t, v in identidades],
                "identidades_declaradas": validacao["declarados"],
                "fontes": validacao["fontes"], "fonte_primaria": validacao["fontes"][0]
                if validacao["fontes"] else None,
                "descartados": validacao["descartados"],
                "confianca": validacao["confianca"],
                "data_do_evento": validacao["data_do_evento"],
                "research_run_id": validacao["research_run_id"],
                "input_hash": hash_da_entrada(identidades, validacao)
                if identidades and validacao["tipo"] else None}

    def processar(self, observacao: dict) -> dict:
        inicio = self.relogio()
        entrada = {"observacao": observacao, "ambiente": self.ambiente,
                   "correlation_id": self.correlation_id}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": ERRO, "motivos": [], "organization_id": None, "signal_id": None,
                     "idempotency_key": None, "tipo": None, "categoria": None, "fontes": [],
                     "descartados": [], "confianca": None, "data_do_evento": None,
                     "research_run_id": None}
        try:
            validacao = validar_observacao(observacao, self.fortes, self.identidade)
            resultado["tipo"] = validacao["tipo"]
            resultado["categoria"] = validacao["categoria"]
            resultado["fontes"] = validacao["fontes"]
            resultado["descartados"] = list(validacao["descartados"])
            resultado["confianca"] = validacao["confianca"]
            resultado["data_do_evento"] = validacao["data_do_evento"]
            resultado["identidades_validas"] = [{"tipo": t, "valor": v}
                                                for t, v in validacao["validos"]]
            casadas = (self.organizacoes_casadas(validacao["validos"])
                       if validacao["validos"] else [])
            veredito, motivo = decidir_veredito(validacao["problemas"], validacao["validos"],
                                                [c["id"] for c in casadas])
            resultado["veredito"] = veredito
            resultado["motivos"] = list(validacao["problemas"]) or ([motivo] if motivo else [])
            if veredito == RECUSADA:
                pass
            elif veredito == REVISAO:
                aprovacao_id = str(uuid.uuid4())
                sync_event_id = str(uuid.uuid4())
                entrada_hash = hash_da_entrada(validacao["validos"], validacao)
                candidatas = sorted(c["id"] for c in casadas)
                chave_revisao = chave_da_revisao(candidatas, entrada_hash)
                resultado["idempotency_key"] = chave_revisao
                resultado["candidatas"] = candidatas
                rc_rev, saida_rev, erro_rev = self.porta.executar(
                    sql_pedir_revisao(aprovacao_id, sync_event_id, chave_revisao, observacao,
                                      resultado["motivos"][0],
                                      [{"organization_id": c["id"]} for c in casadas],
                                      self.correlation_id))
                if rc_rev != 0:
                    # Fail-closed: sem a linha em human_approvals a ambiguidade NAO foi reportada —
                    # entao nao se pode dizer REVISAO_IDENTIDADE.
                    raise PortaIndisponivel("fila humana nao registrada: %s"
                                            % (erro_rev or saida_rev))
                marcas = [l.strip() for l in (saida_rev or "").splitlines()]
                resultado["human_approval_id"] = aprovacao_id
                resultado["revisao_registrada"] = MARCA_REVISAO in marcas
                if not resultado["revisao_registrada"]:
                    # Mesma ambiguidade reapresentada: o claim ja existia, entao NAO se abre pedido
                    # duplicado para o humano (retry idempotente tambem na fila de revisao).
                    resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]
            elif veredito == DETECTADO:
                registro = casadas[0]
                organizacao_id = registro["id"]
                fonte_primaria = validacao["fontes"][0]
                research_run_id = validacao["research_run_id"]
                if research_run_id and not self.research_run_existe(research_run_id):
                    # Vinculo logico apontando para research_run inexistente: descarta o VINCULO
                    # com motivo (o sinal segue) — o fato observado nao se perde.
                    resultado["descartados"].append({"campo": "research_run_id",
                                                     "motivo": "RESEARCH_RUN_NAO_ENCONTRADO",
                                                     "valor": research_run_id})
                    research_run_id = None
                resultado["research_run_id"] = research_run_id
                validacao["research_run_id"] = research_run_id
                entrada_hash = hash_da_entrada(validacao["validos"], validacao)
                chave = chave_idempotencia(organizacao_id, validacao["tipo"], entrada_hash)
                sinal_id = str(uuid.uuid4())
                sync_event_id = str(uuid.uuid4())
                detected_at = self.relogio()
                evidence = {
                    "fontes": validacao["fontes"],
                    "fonte_primaria": fonte_primaria,
                    "descartados": resultado["descartados"],
                    "correlation_id": self.correlation_id,
                    "input_hash": entrada_hash,
                    "deterministico": True,
                    "llm": {"executado": False,
                            "motivo": "v1 do Signal Detector e deterministica (o gate do JEV "
                                      "existe e e fail-closed)"},
                    "regra": "categoria derivada do tipo; evidencia conservada; nenhum score "
                             "calculado aqui (Buying Signal Score e W5)",
                }
                payload = {"origem": AGENTE, "agente": "%s/%s" % (AGENTE, VERSAO),
                           "correlation_id": self.correlation_id, "signal_id": sinal_id,
                           "organization_id": organizacao_id,
                           "signal_type": validacao["tipo"],
                           "signal_category": validacao["categoria"],
                           "fontes": validacao["fontes"], "input_hash": entrada_hash,
                           "idempotency_key": chave, "descartados": resultado["descartados"],
                           "identidades": [{"tipo": t, "valor": v}
                                           for t, v in validacao["validos"]]}
                valores = {
                    "organization_id": organizacao_id,
                    "signal_type": validacao["tipo"],
                    "signal_category": validacao["categoria"],
                    "title": validacao["titulo"],
                    "description": validacao["descricao"],
                    "source_type": fonte_primaria["tipo"],
                    "source_url": fonte_primaria["url"],
                    "event_date": validacao["data_do_evento"],
                    "detected_at": detected_at,
                    "confidence": validacao["confianca"],
                    "evidence": evidence,
                    "research_run_id": research_run_id,
                }
                rc, saida, erro = self.porta.executar(
                    sql_ingerir(sinal_id, sync_event_id, chave, valores, payload))
                if rc != 0:
                    raise PortaIndisponivel("deteccao falhou: %s" % (erro or saida))
                marcas = [l.strip() for l in saida.splitlines()]
                resultado["organization_id"] = organizacao_id
                resultado["idempotency_key"] = chave
                if MARCA_DETECTADO in marcas:
                    resultado["signal_id"] = sinal_id
                    resultado["sync_event_id"] = sync_event_id
                else:
                    # a chave ja existia: nada foi duplicado (retry idempotente)
                    resultado["veredito"] = JA_DETECTADO
                    resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        saida_auditoria = {k: v for k, v in resultado.items() if k != "observacao"}
        status = STATUS_AGENT_RUNS[resultado["veredito"]]
        rc_run, saida_run, erro_run = self.porta.executar(sql_registrar_execucao(
            run_id, self.correlation_id, resultado.get("organization_id"), status,
            entrada, saida_auditoria, inicio, fim, resultado.get("erro")))
        if rc_run != 0:
            # Fail-closed: a execucao NAO pode ser reportada como concluida quando a propria
            # auditoria nao foi escrita.
            resultado["veredito"] = ERRO
            resultado["motivos"] = list(resultado.get("motivos") or []) + \
                ["AUDITORIA_NAO_REGISTRADA: %s" % (erro_run or saida_run)]
            resultado["erro"] = {"tipo": "PortaIndisponivel",
                                 "mensagem": "auditoria nao registrada: %s"
                                             % (erro_run or saida_run)}
            resultado["auditoria_registrada"] = False
            resultado["agent_run_id"] = run_id
            resultado["status_agent_runs"] = STATUS_AGENT_RUNS[ERRO]
            return resultado
        resultado["agent_run_id"] = run_id
        resultado["auditoria_registrada"] = True
        resultado["status_agent_runs"] = status
        return resultado

    def rodar(self, observacoes: list, planejar: bool = False) -> dict:
        resultados = []
        for observacao in observacoes:
            resultados.append(self.planejar(observacao) if planejar
                              else self.processar(observacao))
        resumo = {"agente": "%s/%s" % (AGENTE, VERSAO), "ambiente": self.ambiente,
                  "correlation_id": self.correlation_id, "alvo": self.alvo,
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
            if registro.get("signal_id"):
                itens.append(registro)
        if not confirmo:
            return {"correlation_id": correlation_id, "confirmo": False,
                    "signals": [i["signal_id"] for i in itens],
                    "organizacoes": sorted({i["organization_id"] for i in itens}),
                    "apagados": 0, "dry_run": True}
        if itens:
            rc, saida, erro = self.porta.executar(
                sql_desfazer(itens, correlation_id, str(uuid.uuid4())), permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
        return {"correlation_id": correlation_id, "confirmo": True,
                "signals": [i["signal_id"] for i in itens],
                "organizacoes": sorted({i["organization_id"] for i in itens}),
                "apagados": len(itens), "dry_run": False}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_observacoes(caminho: str) -> list:
    if caminho == "-":
        texto = sys.stdin.read()
    else:
        p = Path(caminho)
        if not p.is_file():
            raise SystemExit(EXIT_FONTE)
        texto = p.read_text(encoding="utf-8")
    observacoes = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        try:
            observacoes.append(json.loads(linha))
        except json.JSONDecodeError as exc:
            raise SystemExit("FALHOU linha %d da fonte nao e JSON: %s" % (numero, exc))
    return observacoes


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agente Signal Detector v1 (TRE-W4-E03-T01)")
    p.add_argument("--fonte", help="arquivo jsonl com as observacoes ('-' = stdin)")
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
    agente = Signal(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                    correlation_id=args.correlation_id)
    # O ambiente e exigido em todo modo que ESCREVE. No modo de planejamento (que nao abre conexao
    # nenhuma) ele e opcional: se declarado, e conferido; se ausente, nada e escrito.
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
            observacoes = ler_observacoes(args.fonte)
            if args.planejar:
                relatorio = agente.rodar(observacoes, planejar=True)
            else:
                agente.identificar_alvo()
                relatorio = agente.rodar(observacoes)
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
                      "apagados": relatorio.get("apagados"), "dry_run": relatorio.get("dry_run")},
                     ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-20s %-24s %s" % (r["veredito"], (r.get("tipo") or "-"),
                                    ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
