#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agente Pain Hypothesis v1 — hipotese de dor COM lastro em evidencia (card TRE-W4-E04-T01).

O que este agente FAZ (e so isto): recebe HIPOTESES de dor sobre empresas que JA existem no
PostgreSQL (o produtor da empresa e o Scout, TRE-W4-E01-T01; a pesquisa e o Research,
TRE-W4-E02-T01; o fato datado e o Signal Detector, TRE-W4-E03-T01), resolve a organizacao pelos
identificadores FORTES do Data Contract V1.0 (cnpj -> domain -> linkedin_url), confere que as
EVIDENCIAS declaradas existem de verdade no banco (e que sao da MESMA empresa) e GRAVA a hipotese
em `pain_hypotheses` marcada como INFERENCIA, com o lastro conservado.

E' o quarto item da W4 (doc 07 §7) e o produtor que faltava para o gate da onda ("empresa ->
research/signals/hypothesis/contacts") e para o E2E #001 do doc 08 §3 (passo 9: "cria pain
hypothesis").

O que ele NAO faz, por desenho (declarado em `agente-pain-hypothesis-v1.json` -> lacunas):
  - nao CRIA organizacao e nao escreve NENHUMA coluna de `organizations` (a hipotese e aditiva:
    identidade e do Scout/dedup, estagio e do Odoo — doc 12 §1);
  - nao CALCULA score: `business_impact_score`, `estimated_impact_description` e `validated_at`
    NAO sao escritos (impacto de negocio nao tem formula homologada no baseline e a validacao da
    hipotese e ato HUMANO — doc 12 §6). A guarda de escrita RECUSA quem tentar;
  - nao VALIDA a hipotese: `status` e SEMPRE o inicial do contrato (`HYPOTHESIS`); o status que a
    fonte declarar e DESCARTADO com motivo (VALIDATED/PARTIALLY_VALIDATED/REJECTED/STALE sao
    transicao humana, nao inferencia de agente);
  - nao cria contato (W4-E05) e nao emite evento de outbox (nao existe evento de hipotese no
    contrato — doc 06 §5);
  - nao faz requisicao de rede nenhuma (sem crawler, sem LLM na v1) e nao toca Odoo/Titan/n8n;
  - nao escreve em producao (ADR-005).

Regras que sustentam a hipotese:
  - **hipotese sem lastro NAO e gravada**: pelo menos uma evidencia declarada tem de EXISTIR no
    banco (SINAL -> `signals.id`, PESQUISA -> `research_runs.id`) e ser da MESMA empresa resolvida.
    Evidencia inexistente ou de outra empresa e descartada com motivo e, se nenhuma sobrar, a
    hipotese e RECUSADA (`SEM_EVIDENCIA_VALIDA`) — inferencia sem evidencia e opiniao;
  - **a inferencia e MARCADA como inferencia** (compliance, doc 12 §8): `evidence.inferencia = true`
    e a regra viaja na propria linha, junto do `input_hash` e do `correlation_id`;
  - **a evidencia e conservada**: a linha de origem entra inteira no `evidence.evidencias` (tipo,
    id e os campos da origem — `signal_type`/`signal_category`/`event_date`,
    `research_type`/`status`) e o que foi descartado fica registrado com campo, motivo e valor;
  - **a categoria da dor e do vocabulario da BASELINE** (`dores_prioritarias` do doc 01 §5:
    FINANCEIRO, COMERCIAL, ATENDIMENTO, OPERACOES, DOCUMENTOS): categoria fora da lista e RECUSA,
    categoria ausente fica `NULL` — nao se inventa categoria;
  - **o texto da dor e obrigatorio** (`pain_statement` e NOT NULL no DDL): ausente e RECUSADA
    (`SEM_DOR_DECLARADA`), acima do limite e RECUSADA (`DOR_ACIMA_DO_LIMITE`) — nao se trunca o
    enunciado de uma hipotese;
  - **vinculo com FK**: `pain_hypotheses.research_run_id` e FK de verdade no DDL (ao contrario de
    `signals.research_run_id`, que e vinculo logico sem FK). O agente CONFERE a existencia (e a
    empresa) antes de escrever; inexistente ou de outra empresa e DESCARTADO com motivo e a
    hipotese segue sem o vinculo;
  - **nada e inventado**: sem `confianca` na fonte a coluna fica `NULL`; sem resumo o
    `evidence_summary` fica `NULL`.

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"):
  - a chave e a ENTRADA declarada (normalizada), nao a rodada: `pain:org:<uuid>:<input_hash>`,
    gravada em `sync_events.idempotency_key` (UNIQUE). A ingestao e UMA transacao com dois
    comandos de escrita: claim da chave + INSERT da hipotese ancorado no claim (replay nao insere)
    -> fechamento do sync_event ANCORADO na hipotese DESTA rodada (replay nao marca sucesso).
    Replay: o claim volta vazio, nada e inserido e o veredito vira JA_REGISTRADA;
  - a historia de cada hipotese fica em `agent_runs` (uma linha por hipotese, com o
    `correlation_id` do lote) — auditoria nao depende da narrativa de quem rodou.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --planejar --fonte hipoteses.jsonl
  python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --ambiente dev --fonte hipoteses.jsonl \
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
      --relatorio /tmp/pain-rodada.json
  python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --desfazer <correlation_id>
  python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --desfazer <correlation_id> --confirmo
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
# Identidade do agente (espelha `hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "pain_hypothesis"
PAPEL = "pain_hypothesis"
VERSAO = "1.0.0"
WORKFLOW = "hipotese-de-dor"
WORKFLOW_VERSAO = "v1"

TABELA_HIPOTESES = "sales_intelligence.pain_hypotheses"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_RESEARCH_RUNS = "sales_intelligence.research_runs"
TABELA_SINAIS = "sales_intelligence.signals"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"
TABELA_APPROVALS = "sales_intelligence.human_approvals"
# `organizations`, `research_runs` e `signals` NAO sao tabelas de escrita deste agente: aparecem
# aqui apenas como LEITURA declarada (identidade, existencia e dono das evidencias). Quem escreve
# nelas e o Scout, o Research e o Signal Detector.
TABELAS_PERMITIDAS = (TABELA_HIPOTESES, TABELA_AGENT_RUNS, TABELA_SYNC_EVENTS, TABELA_APPROVALS)
TABELAS_LIDAS = (TABELA_ORGANIZACOES, TABELA_RESEARCH_RUNS, TABELA_SINAIS)

ACTION_TYPE_REVISAO = "PAIN_IDENTITY_REVIEW"
OPERACAO_SYNC = "PAIN_HYPOTHESIS"
MARCA_REGISTRADA = "PAIN_REGISTRADA"
# Revisao de identidade ambigua: operacao/entidade proprias em sync_events (o contrato nao fecha
# vocabulario para essas colunas) e marca propria, para que o fechamento prove a linha da fila.
OPERACAO_REVISAO = "PAIN_REVIEW"
ENTITY_TYPE_REVISAO = "pain_review"
MARCA_REVISAO = "PAIN_REVISAO_REGISTRADA"

# Categoria da dor: vocabulario do BASELINE (doc 01 §5 "Hipoteses de dor prioritarias" — as cinco
# areas com as dores tipicas que o produto prioriza). Nao e' vocabulario fechado do Data Contract
# V1.0 (que fecha apenas `pain_hypotheses.status`, doc 12 §6); por isso ele mora no contrato DESTE
# agente, versionado junto, e a suite reprova se ele divergir do documento.
CATEGORIAS_DE_DOR = ("FINANCEIRO", "COMERCIAL", "ATENDIMENTO", "OPERACOES", "DOCUMENTOS")

# Status: SEMPRE o inicial do contrato (`vocabularies.pain_hypotheses.status[0]`, que e' tambem o
# DEFAULT do DDL). A transicao (VALIDATED/PARTIALLY_VALIDATED/REJECTED/STALE) e ato HUMANO.
STATUS_INICIAL = "HYPOTHESIS"

# Tipos de evidencia aceitos: o fato datado (`signals`) e a pesquisa (`research_runs`).
TIPOS_DE_EVIDENCIA = ("SINAL", "PESQUISA")

# Colunas de LEITURA das duas origens de evidencia (a ordem e' a da consulta, conferida por suite):
# `signals` traz o fato de origem; `research_runs` traz o tipo e o estado da pesquisa.
CAMPOS_DE_SINAL_LIDOS = ("organization_id", "signal_type", "signal_category", "event_date")
CAMPOS_DE_RESEARCH_LIDOS = ("organization_id", "research_type", "status")

# Colunas de `pain_hypotheses` que o agente ESCREVE (ordem do INSERT). Fora desta lista a guarda
# recusa.
COLUNAS_DA_HIPOTESE = (
    "id",
    "organization_id",
    "research_run_id",
    "pain_category",
    "pain_statement",
    "evidence_summary",
    "evidence",
    "confidence",
    "status",
    "created_at",
)

# Colunas de `pain_hypotheses` que o agente NAO escreve: o impacto de negocio (score sem formula
# homologada no baseline) e a validacao (ato humano — doc 12 §6). Escrever uma delas e recusa.
COLUNAS_PROIBIDAS = ("business_impact_score", "estimated_impact_description", "validated_at")

# Campos aceitos na entrada. Qualquer outro campo e descartado com motivo (CAMPO_NAO_DECLARADO).
# Os campos da segunda lista sao DERIVADOS: se vierem da fonte, sao descartados com
# DERIVADO_NAO_ACEITO (nunca escritos) — e por isso estao declarados aqui, para o motivo ser esse
# e nao "campo inventado".
CAMPO_DERIVADOS_NA_ENTRADA = ("status", "id", "validated_at", "business_impact_score",
                              "estimated_impact_description")
CAMPOS_DA_HIPOTESE = ("organizacao", "dor", "categoria", "evidencias", "resumo_da_evidencia",
                      "confianca", "research_run_id") + CAMPO_DERIVADOS_NA_ENTRADA
CAMPOS_DA_EVIDENCIA = ("tipo", "id")

LIMITE_DOR = 4000
LIMITE_RESUMO = 4000
LIMITE_CATEGORIA = 100

REGISTRADA = "REGISTRADA"
JA_REGISTRADA = "JA_REGISTRADA"
REVISAO = "REVISAO_IDENTIDADE"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (REGISTRADA, JA_REGISTRADA, REVISAO, RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    REGISTRADA: "COMPLETED",
    JA_REGISTRADA: "COMPLETED",
    REVISAO: "REVIEW_REQUIRED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

# Vereditos do modo `--planejar` (nenhuma conexao de banco e feita nele)
PLANEJADO_REGISTRAR = "PLANEJADO_REGISTRAR"
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
CONTRATO_AGENTE_PADRAO = "hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json"
# A identidade de empresa e UMA regra so neste projeto (normalizacao e digito verificador do CNPJ,
# dominio canonico, LinkedIn canonico). Este agente IMPORTA a regra do produtor (Scout) em vez de
# manter uma segunda copia dela — copia de regra de identidade e' divergencia esperando acontecer,
# e a suite reprova se aparecer uma segunda implementacao aqui.
MODULO_IDENTIDADE = "hermes/agents/scout/scout.py"

_UUID_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                      re.I)


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
# Normalizacao e validacao da hipotese
# ---------------------------------------------------------------------------------------
def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def validar_dor(valor) -> tuple:
    """`pain_statement` e NOT NULL no DDL: enunciado ausente RECUSA e enunciado longo tambem.

    Diferente do titulo do sinal (coluna nullable, onde o longo era descartado e o fato seguia):
    aqui o enunciado E' a hipotese — truncar seria inventar outra frase. Por isso os dois casos
    sao RECUSA, com motivos distintos.
    """
    texto = _texto(valor)
    if not texto:
        return None, "SEM_DOR_DECLARADA"
    if len(texto) > LIMITE_DOR:
        return None, "DOR_ACIMA_DO_LIMITE"
    return texto, None


def validar_categoria(valor) -> tuple:
    """Categoria do vocabulario da baseline (doc 01 §5). Ausente fica NULL; fora da lista RECUSA."""
    texto = _texto(valor).upper()
    if not texto:
        return None, None
    if len(texto) > LIMITE_CATEGORIA:
        return None, "CATEGORIA_ACIMA_DO_LIMITE"
    if texto not in CATEGORIAS_DE_DOR:
        return None, "CATEGORIA_DE_DOR_DESCONHECIDA"
    return texto, None


def validar_resumo(valor) -> tuple:
    texto = _texto(valor)
    if not texto:
        return None, None
    if len(texto) > LIMITE_RESUMO:
        return None, "RESUMO_ACIMA_DO_LIMITE"
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
    """Vinculo com o DDL: aqui ha FK de verdade. O FORMATO e conferido aqui; a EXISTENCIA (e o
    dono), no banco."""
    texto = _texto(valor)
    if not texto:
        return None, None
    if not _UUID_V4.match(texto):
        return None, "RESEARCH_RUN_ID_INVALIDO"
    return texto.lower(), None


def validar_evidencias(valor) -> tuple:
    """>=1 evidencia declarada, cada uma `{tipo, id}` do vocabulario, com id UUID v4.

    O que e' problema de FORMA RECUSA a hipotese inteira (fail-closed): a evidencia e' a substancia
    da inferencia, nao um detalhe da linha. O que se resolve no BANCO (existencia e dono) e'
    descarte com motivo, tratado mais tarde por `evidencias_existentes`.
    """
    if valor in (None, "", []):
        return [], ["SEM_EVIDENCIA_DECLARADA"], []
    if not isinstance(valor, list):
        return [], ["EVIDENCIAS_ILEGIVEIS"], []
    normalizadas = []
    problemas = []
    descartados = []
    for posicao, item in enumerate(valor):
        if not isinstance(item, dict):
            problemas.append("EVIDENCIA_ILEGIVEL")
            continue
        tipo = _texto(item.get("tipo")).upper()
        if not tipo:
            problemas.append("EVIDENCIA_SEM_TIPO")
            continue
        if tipo not in TIPOS_DE_EVIDENCIA:
            problemas.append("TIPO_DE_EVIDENCIA_DESCONHECIDO")
            continue
        identificador = _texto(item.get("id")).lower()
        if not identificador:
            problemas.append("EVIDENCIA_SEM_ID")
            continue
        if not _UUID_V4.match(identificador):
            problemas.append("EVIDENCIA_COM_ID_INVALIDO")
            continue
        if any(i["tipo"] == tipo and i["id"] == identificador for i in normalizadas):
            descartados.append({"campo": "evidencias[%d]" % posicao, "motivo": "EVIDENCIA_DUPLICADA",
                                "valor": {"tipo": tipo, "id": identificador}})
            continue
        for campo in item:
            if campo not in CAMPOS_DA_EVIDENCIA:
                descartados.append({"campo": "evidencias[%d].%s" % (posicao, campo),
                                    "motivo": "CAMPO_NAO_DECLARADO", "valor": item.get(campo)})
        normalizadas.append({"tipo": tipo, "id": identificador})
    if problemas:
        return normalizadas, problemas, descartados
    if not normalizadas:
        return [], ["SEM_EVIDENCIA_DECLARADA"], descartados
    return normalizadas, [], descartados


def validar_hipotese(hipotese, fortes: tuple, identidade) -> dict:
    """Validacao fora do banco: identidade, dor, categoria, evidencias, resumo, confianca, vinculo.

    Nao decide veredito (isso e `decidir_veredito`). O que e' invalido mas nao impede o registro
    (resumo longo, confianca fora de 0-1, vinculo torto, campo derivado declarado, campo inventado)
    vira DESCARTE COM MOTIVO.
    """
    if not isinstance(hipotese, dict):
        return {"problemas": ["HIPOTESE_ILEGIVEL"], "validos": [], "declarados": [],
                "dor": None, "categoria": None, "evidencias": [], "resumo": None,
                "confianca": None, "research_run_id": None, "descartados": []}
    problemas = []
    dor, motivo_dor = validar_dor(hipotese.get("dor"))
    if motivo_dor:
        problemas.append(motivo_dor)
    categoria, motivo_categoria = validar_categoria(hipotese.get("categoria"))
    if motivo_categoria:
        problemas.append(motivo_categoria)
    evidencias, problemas_evidencias, descartados_evidencias = validar_evidencias(
        hipotese.get("evidencias"))
    problemas.extend(problemas_evidencias)
    descartados = list(descartados_evidencias)
    resumo, motivo_resumo = validar_resumo(hipotese.get("resumo_da_evidencia"))
    if motivo_resumo:
        descartados.append({"campo": "resumo_da_evidencia", "motivo": motivo_resumo})
    confianca, motivo_confianca = validar_confianca(hipotese.get("confianca"))
    if motivo_confianca:
        descartados.append({"campo": "confianca", "motivo": motivo_confianca,
                            "valor": hipotese.get("confianca")})
    research_run_id, motivo_run = validar_research_run_id(hipotese.get("research_run_id"))
    if motivo_run:
        descartados.append({"campo": "research_run_id", "motivo": motivo_run,
                            "valor": hipotese.get("research_run_id")})
    # Campos DERIVADOS: status/validacao/impacto sao do agente ou ato humano, nao da fonte.
    for campo in CAMPO_DERIVADOS_NA_ENTRADA:
        if campo in hipotese and _texto(hipotese.get(campo)):
            descartados.append({"campo": campo, "motivo": "DERIVADO_NAO_ACEITO",
                                "valor": hipotese.get(campo)})
    # Identidade: o agente NAO cria empresa (quem cria/ingere empresa e o Scout).
    organizacao = hipotese.get("organizacao") or {}
    if not isinstance(organizacao, dict):
        organizacao = {}
    validadores = {
        "cnpj": (identidade.cnpj_valido, identidade.normalizar_cnpj),
        "domain": (identidade.domain_valido, identidade.normalizar_domain),
        "linkedin_url": (identidade.linkedin_valido, identidade.normalizar_linkedin),
    }
    validos = []
    declarados = []
    for tipo in fortes:
        bruto = _texto(organizacao.get(tipo))
        if not bruto:
            continue
        declarados.append(tipo)
        valida, normaliza = validadores[tipo]
        if valida(bruto):
            valor = normaliza(bruto)
            if all(valor != v for _, v in validos):
                validos.append((tipo, valor))
    if declarados and not validos:
        problemas.append("IDENTIFICADOR_FORTE_INVALIDO")
    elif not validos:
        problemas.append("SEM_IDENTIFICADOR_FORTE")
    for campo in hipotese:
        if campo not in CAMPOS_DA_HIPOTESE:
            descartados.append({"campo": campo, "motivo": "CAMPO_NAO_DECLARADO",
                                "valor": hipotese.get(campo)})
    return {"problemas": problemas, "validos": validos, "declarados": declarados, "dor": dor,
            "categoria": categoria, "evidencias": evidencias, "resumo": resumo,
            "confianca": confianca, "research_run_id": research_run_id, "descartados": descartados}


def hash_da_entrada(identidades: list, validacao: dict) -> str:
    """Chave da idempotencia: a ENTRADA DECLARADA (normalizada), nao a rodada nem o pos-banco.

    Ordem de chaves fixa e JSON sem espacos: a MESMA hipotese apresentada de novo tem de produzir o
    MESMO hash — e' isso que faz o retry nao criar duplicata. O hash e' calculado sobre o que a
    fonte pediu (evidencia declarada, vinculo declarado), nao sobre o que sobrou dos descartes: a
    hipotese reapresentada com a mesma evidencia continua sendo o mesmo pedido.
    """
    canonico = {
        "identidades": [[t, v] for t, v in identidades],
        "dor": validacao["dor"],
        "categoria": validacao["categoria"],
        "evidencias": [{"tipo": e["tipo"], "id": e["id"]} for e in validacao["evidencias"]],
        "resumo_da_evidencia": validacao["resumo"],
        "confianca": validacao["confianca"],
        "research_run_id": validacao["research_run_id"],
    }
    return _sha256(json.dumps(canonico, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def chave_idempotencia(organizacao_id: str, entrada_hash: str) -> str:
    return "pain:org:%s:%s" % (organizacao_id, entrada_hash)


def chave_da_revisao(organizacoes_ids: list, entrada_hash: str) -> str:
    """Chave da requisicao na fila humana: as candidatas ordenadas + a entrada (retry nao duplica)."""
    return "pain:revisao:%s:%s" % (",".join(sorted(organizacoes_ids)), entrada_hash)


def decidir_veredito(problemas: list, validos: list, organizacoes_casadas: list,
                     evidencias_validas: int = 0) -> tuple:
    """Decisao pura (testavel sem banco) — a regra vive aqui e so aqui.

    O agente NAO cria empresa: identidade que nao casa e recusa, nao descoberta. Ambiguidade (dois
    ou mais casamentos distintos) vai para a fila humana, nunca e resolvida por heuristica
    (`dedup.rule` do contrato). E hipotese sem lastro NAO se grava: sem evidencia que exista de
    verdade (e seja da mesma empresa) o veredito e RECUSADA (`SEM_EVIDENCIA_VALIDA`).
    """
    if problemas:
        return RECUSADA, problemas[0]
    if not validos:
        return RECUSADA, "SEM_IDENTIFICADOR_FORTE"
    if len(organizacoes_casadas) == 0:
        return RECUSADA, "ORGANIZACAO_NAO_ENCONTRADA"
    if len(organizacoes_casadas) > 1:
        return REVISAO, "CONFLITO_DE_IDENTIDADE_FORTE"
    if evidencias_validas == 0:
        return RECUSADA, "SEM_EVIDENCIA_VALIDA"
    return REGISTRADA, None


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

    Enunciado de dor e' prosa: um trecho com a palavra "drop" ('queda de receita') faria a guarda
    recusar hipotese legitima como se fosse DDL. Quem escreve dado e o `lit()`, que escapa
    apostrofo dobrando — entao a varredura olha o codigo, nao a prosa.
    """
    return _LITERAL.sub("''", sql)


def colunas_do_insert(sql: str, tabela: str) -> list:
    """Listas de colunas de cada `INSERT INTO <tabela> (...)` do SQL (para a guarda da hipotese)."""
    padrao = re.compile(_INSERT_COLUNAS.pattern % re.escape(tabela), re.I)
    return [[c.strip().lower() for c in bloco.split(",") if c.strip()]
            for bloco in padrao.findall(sql)]


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL e escrita fora do declarado.

    Alem das regras de tabela/operacao, o INSERT de `pain_hypotheses` tem duas exigencias de
    DESENHO: (a) declara `organization_id` e `pain_statement` (NOT NULL do DDL — hipotese sem
    empresa ou sem enunciado nao existe); (b) NAO declara nenhuma coluna de impacto/validacao
    (`business_impact_score`, `estimated_impact_description`, `validated_at` — score sem formula
    homologada e validacao sao ato humano). As duas sao medidas por mutacao na suite e pelo dente
    do aceite.
    """
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL nao e permitido ao agente Pain Hypothesis")
    for colunas in colunas_do_insert(codigo, TABELA_HIPOTESES):
        faltando = [c for c in ("organization_id", "pain_statement") if c not in colunas]
        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT de hipotese sem coluna obrigatoria do DDL: %s" % ", ".join(faltando))
        proibidas = [c for c in colunas if c in COLUNAS_PROIBIDAS]
        if proibidas:
            raise GuardaDeEscritaViolada(
                "INSERT de hipotese com coluna de impacto/validacao: %s" % ", ".join(proibidas))
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela == TABELA_ORGANIZACOES:
                raise GuardaDeEscritaViolada(
                    "%s em organizations nao e permitido: a hipotese de dor nao escreve na empresa "
                    "(o registro e aditivo — quem enriquece a organizacao e o Research)"
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
def _lista_de_ids(ids: list) -> str:
    return ", ".join(lit(i) for i in ids)


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


def sql_sinais_existentes(ids: list) -> str:
    """SINAIS declarados: existencia, DONO e o fato de origem (tipo, categoria, data).

    O `organization_id` vem junto de proposito: evidencia de OUTRA empresa nao sustenta esta
    hipotese, e o dono e' conferido em Python contra a empresa resolvida.
    """
    return (
        "SELECT id::text, COALESCE(organization_id::text, ''), COALESCE(signal_type, ''), "
        "COALESCE(signal_category, ''), COALESCE(event_date::text, '') "
        "FROM %s WHERE id IN (%s) ORDER BY id;" % (TABELA_SINAIS, _lista_de_ids(ids))
    )


def sql_research_runs_existentes(ids: list) -> str:
    """PESQUISAS declaradas (e o vinculo de origem): existencia, DONO e o tipo de pesquisa."""
    return (
        "SELECT id::text, COALESCE(organization_id::text, ''), COALESCE(research_type, ''), "
        "COALESCE(status, '') FROM %s WHERE id IN (%s) ORDER BY id;"
        % (TABELA_RESEARCH_RUNS, _lista_de_ids(ids))
    )


def montar_linha_hipotese(hipotese_id: str, valores: dict) -> tuple:
    """Colunas x valores do INSERT de `pain_hypotheses` — paridade conferida contra o DDL na suite."""
    colunas = list(COLUNAS_DA_HIPOTESE)
    expressoes = [
        lit(hipotese_id),                            # id
        lit(valores["organization_id"]),             # organization_id (empresa que JA existe)
        lit(valores["research_run_id"]) if valores["research_run_id"] is not None else "NULL",
        lit(valores["pain_category"]) if valores["pain_category"] is not None else "NULL",
        lit(valores["pain_statement"]),              # pain_statement (NOT NULL)
        lit(valores["evidence_summary"]) if valores["evidence_summary"] is not None else "NULL",
        lit_json(valores["evidence"]),               # evidence (lastro + inferencia marcada)
        lit(valores["confidence"]) if valores["confidence"] is not None else "NULL",
        lit(valores["status"]),                      # status (SEMPRE o inicial do contrato)
        "now()",                                     # created_at
    ]
    if len(expressoes) != len(colunas):
        raise ValueError("montagem do INSERT de pain_hypotheses divergente: %d colunas x %d valores"
                         % (len(colunas), len(expressoes)))
    return colunas, expressoes


def sql_ingerir(hipotese_id: str, sync_event_id: str, chave: str,
                valores: dict, payload: dict) -> str:
    """Uma transacao, DOIS comandos de escrita: claim+INSERT da hipotese -> fechamento ancorado.

    Por que dois comandos e nao um so: as CTEs de escrita e a instrucao principal rodam no MESMO
    snapshot, entao a instrucao principal NAO enxerga a linha que a CTE acabou de inserir (defeito
    medido no aceite E2E do Scout). Aqui:
      1. `WITH claim AS (INSERT sync_events ... ON CONFLICT DO NOTHING RETURNING id)` +
         `INSERT pain_hypotheses SELECT ... FROM claim ON CONFLICT (id) DO NOTHING RETURNING id` —
         replay nao insere hipotese nenhuma;
      2. `UPDATE sync_events SET status='SUCCESS' ... AND EXISTS (SELECT 1 FROM pain_hypotheses
         WHERE id = <hipotese desta rodada>) RETURNING 'PAIN_REGISTRADA'` — o fechamento so marca
         sucesso se a hipotese DESTA rodada existe; ausente = replay (a chave ja estava
         reivindicada).

    Nao existe terceiro comando: a hipotese e ADITIVA — o registro nao escreve nenhuma coluna de
    empresa nem de sinal. E' o desenho, nao uma economia.
    """
    colunas, expressoes = montar_linha_hipotese(hipotese_id, valores)
    comandos = [
        "BEGIN;",
        "WITH claim AS (\n"
        "  INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, created_at)\n"
        "  VALUES ({sevid}, 'pain_hypothesis', {hid}, 'pain_hypothesis', 'postgresql', {operacao}, "
        "{versao}, {chave}, 'PENDING', {payload}, now())\n"
        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {hipoteses} ({cols})\n"
        "SELECT {vals} FROM claim\n"
        "ON CONFLICT (id) DO NOTHING\n"
        "RETURNING id;".format(
            sync=TABELA_SYNC_EVENTS, hipoteses=TABELA_HIPOTESES, sevid=lit(sync_event_id),
            hid=lit(hipotese_id), operacao=lit(OPERACAO_SYNC), versao=lit(VERSAO),
            chave=lit(chave), payload=lit_json(payload), cols=", ".join(colunas),
            vals=", ".join(expressoes)),
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(),\n"
        "  response_payload = jsonb_build_object('pain_hypothesis_id', {hid}, 'veredito', "
        "'REGISTRADA', 'idempotency_key', {chave})\n"
        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {hipoteses} WHERE id = {hid})\n"
        "RETURNING {marca};".format(sync=TABELA_SYNC_EVENTS, hipoteses=TABELA_HIPOTESES,
                                    hid=lit(hipotese_id), chave=lit(chave),
                                    marca=lit(MARCA_REGISTRADA)),
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


def sql_pedir_revisao(approval_id: str, sync_event_id: str, chave: str, hipotese: dict, motivo: str,
                      casadas: list, correlation_id: str) -> str:
    """Requisicao da fila humana — mesmo desenho idempotente da hipotese (claim + fechamento).

    A chave e' a da hipotese com o sufixo `:revisao`: a MESMA ambiguidade reapresentada nao abre
    pedido duplicado para o humano (o claim ja existe), e o fechamento so' marca sucesso quando a
    linha da fila existe de verdade — auditoria nao conta narrativa, conta linha.
    """
    proposta = {
        "motivo": motivo,
        "agente": "%s/%s" % (AGENTE, VERSAO),
        "correlation_id": correlation_id,
        "hipotese": hipotese,
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
             aid=lit(approval_id), acao=lit(ACTION_TYPE_REVISAO),
             quem=lit("%s/%s" % (AGENTE, VERSAO)), tipo_revisao=lit(ENTITY_TYPE_REVISAO),
             operacao=lit(OPERACAO_REVISAO), origem=lit("hermes"), versao=lit(VERSAO),
             chave=lit(chave), payload=lit_json(payload), proposta=lit_json(proposta),
             resposta=lit_json(resposta), marca=lit(MARCA_REVISAO))


def sql_selecionar_da_rodada(correlation_id: str) -> str:
    """O que a rodada criou: as hipoteses (veredito REGISTRADA) e os sync_events delas."""
    return (
        "SELECT jsonb_build_object(\n"
        "  'pain_hypothesis_id', h.id::text,\n"
        "  'organization_id', h.organization_id::text,\n"
        "  'sync_event_id', e.id::text\n"
        ")::text\n"
        "FROM {hipoteses} h\n"
        "JOIN {sync} e ON e.entity_id = h.id AND e.operation = {operacao}\n"
        "JOIN {audit} a ON (a.output ->> 'pain_hypothesis_id') = h.id::text\n"
        "WHERE a.correlation_id = {corr} AND a.agent_name = {agente}\n"
        "  AND a.output ->> 'veredito' = {veredito}\n"
        "ORDER BY h.id;\n"
    ).format(hipoteses=TABELA_HIPOTESES, sync=TABELA_SYNC_EVENTS, audit=TABELA_AGENT_RUNS,
             operacao=lit(OPERACAO_SYNC), corr=lit(correlation_id), agente=lit(AGENTE),
             veredito=lit(REGISTRADA))


def sql_desfazer(itens: list, correlation_id: str, sync_event_id: str) -> str:
    """Apaga o que a rodada criou (hipoteses + sync_events delas). Nada em empresa, nada em auditoria.

    Nao ha valor ANTERIOR a restaurar: a hipotese e uma LINHA NOVA (aditivo). O que prova a posse
    da linha pela rodada e o `sync_events` da chave — e' por ele que o DELETE chega na hipotese
    certa.
    """
    ids_hipoteses = ", ".join(lit(i["pain_hypothesis_id"]) for i in itens
                              if i.get("pain_hypothesis_id"))
    ids_sync = ", ".join(lit(i["sync_event_id"]) for i in itens if i.get("sync_event_id"))
    comandos = ["BEGIN;"]
    if ids_hipoteses:
        comandos.append("DELETE FROM {hipoteses} WHERE id IN ({ids});".format(
            hipoteses=TABELA_HIPOTESES, ids=ids_hipoteses))
    if ids_sync:
        comandos.append("DELETE FROM {sync} WHERE id IN ({ids});".format(
            sync=TABELA_SYNC_EVENTS, ids=ids_sync))
    payload = {"motivo": "desfazer da rodada de hipotese de dor",
               "correlation_id": correlation_id,
               "pain_hypotheses": [i["pain_hypothesis_id"] for i in itens]}
    comandos.append(
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, operation, "
        "source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'pain_hypothesis', NULL, 'pain_hypothesis', 'postgresql', 'ROLLBACK', "
        "{versao}, {chave}, 'SUCCESS', {payload}, now());".format(
            sync=TABELA_SYNC_EVENTS, sevid=lit(sync_event_id), versao=lit(VERSAO),
            chave=lit("pain:rollback:%s" % correlation_id), payload=lit_json(payload)))
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
        "motivo": "v1 do Pain Hypothesis e deterministica: nenhuma chamada de LLM e feita",
    }


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resumo_da_hipotese(validacao: dict, evidencias: list) -> str:
    """Resumo deterministico: nenhuma frase e nova inferencia, e o que nao foi escrito esta dito."""
    return (
        "Hipotese de dor %s (inferencia, deterministica, sem LLM): %d evidencia(s) com lastro "
        "conferido no banco; %d descarte(s) com motivo; confianca %s."
        % (validacao["categoria"] or "SEM_CATEGORIA", len(evidencias),
           len(validacao["descartados"]),
           validacao["confianca"] if validacao["confianca"] is not None else "NULL")
    )


# ---------------------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------------------
class PainHypothesis:
    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None,
                 identidade=None):
        self.raiz = Path(raiz or RAIZ_PADRAO)
        self.contrato = carregar_contrato_do_agente(self.raiz)
        self.fortes = fortes_do_contrato_de_dados(self.raiz)
        if list(self.fortes) != list(self.contrato["identidade"]["fortes_por_prioridade"]):
            raise ValueError("divergencia entre o contrato do agente e o Data Contract V1.0: %s x %s"
                             % (list(self.fortes),
                                self.contrato["identidade"]["fortes_por_prioridade"]))
        if list(self.contrato["categorias_de_dor"]) != list(CATEGORIAS_DE_DOR):
            raise ValueError("divergencia no vocabulario de categorias de dor")
        if list(self.contrato["tipos_de_evidencia"]) != list(TIPOS_DE_EVIDENCIA):
            raise ValueError("divergencia no vocabulario de tipos de evidencia")
        if list(self.contrato["escrita_hipotese"]["colunas_proibidas"]) != list(COLUNAS_PROIBIDAS):
            raise ValueError("divergencia nas colunas proibidas (impacto/validacao)")
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
                "Pain Hypothesis v1 nao escreve em prod (ADR-005): a promocao exige card proprio "
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

    # -- leitura do banco -------------------------------------------------------------
    def organizacoes_casadas(self, validos: list) -> list:
        if not validos:
            return []
        rc, saida, erro = self.porta.executar(sql_consultar_organizacao(validos))
        if rc != 0:
            raise PortaIndisponivel("consulta de organizacao falhou: %s" % (erro or saida))
        return _linhas_json(saida, chave="id")

    def _linhas_por_id(self, sql: str, operacao: str, campos_extra: tuple) -> dict:
        rc, saida, erro = self.porta.executar(sql)
        if rc != 0:
            raise PortaIndisponivel("%s falhou: %s" % (operacao, erro or saida))
        return _mapa_por_id(saida, campos_extra)

    def evidencias_existentes(self, evidencias: list, organizacao_id: str) -> tuple:
        """Confere a EXISTENCIA e o DONO das evidencias declaradas (fail-closed na porta).

        Devolve (validas, descartados): a evidencia que existe e e' da MESMA empresa resolvida
        entra como lastro (com o fato de origem conservado); a que nao existe e' descartada com
        `EVIDENCIA_NAO_ENCONTRADA` e a de outra empresa com `EVIDENCIA_DE_OUTRA_ORGANIZACAO`.
        Evidencia de outra empresa e' o caso que mais engana: o id existe (a leitura passaria), mas
        o lastro seria de outro cliente.
        """
        ids_sinais = [e["id"] for e in evidencias if e["tipo"] == "SINAL"]
        ids_pesquisas = [e["id"] for e in evidencias if e["tipo"] == "PESQUISA"]
        sinais = self._linhas_por_id(sql_sinais_existentes(ids_sinais),
                                     "consulta de sinais",
                                     CAMPOS_DE_SINAL_LIDOS) if ids_sinais else {}
        pesquisas = self._linhas_por_id(sql_research_runs_existentes(ids_pesquisas),
                                        "consulta de pesquisa",
                                        CAMPOS_DE_RESEARCH_LIDOS) if ids_pesquisas else {}
        validas = []
        descartados = []
        for evidencia in evidencias:
            base = sinais if evidencia["tipo"] == "SINAL" else pesquisas
            registro = base.get(evidencia["id"])
            if registro is None:
                descartados.append({"campo": "evidencias", "motivo": "EVIDENCIA_NAO_ENCONTRADA",
                                    "valor": {"tipo": evidencia["tipo"], "id": evidencia["id"]}})
                continue
            if registro.get("organization_id") != organizacao_id:
                descartados.append({"campo": "evidencias",
                                    "motivo": "EVIDENCIA_DE_OUTRA_ORGANIZACAO",
                                    "valor": {"tipo": evidencia["tipo"], "id": evidencia["id"],
                                              "organization_id": registro.get("organization_id")}})
                continue
            if evidencia["tipo"] == "SINAL":
                validas.append({"tipo": "SINAL", "id": evidencia["id"],
                                "organization_id": registro.get("organization_id"),
                                "signal_type": registro.get("signal_type") or None,
                                "signal_category": registro.get("signal_category") or None,
                                "event_date": registro.get("event_date") or None})
            else:
                validas.append({"tipo": "PESQUISA", "id": evidencia["id"],
                                "organization_id": registro.get("organization_id"),
                                "research_type": registro.get("research_type") or None,
                                "status": registro.get("status") or None})
        return validas, descartados

    def research_run_do_dono(self, run_id: str, organizacao_id: str) -> tuple:
        """Vinculo com FK: a existencia E' conferida antes de gravar (vinculo quebrado nao passa).

        Devolve (run_id_ou_None, motivo_do_descarte): inexistente -> `RESEARCH_RUN_NAO_ENCONTRADO`;
        existente de outra empresa -> `RESEARCH_RUN_DE_OUTRA_ORGANIZACAO`.
        """
        registros = self._linhas_por_id(sql_research_runs_existentes([run_id]),
                                        "consulta de research_run", CAMPOS_DE_RESEARCH_LIDOS)
        registro = registros.get(run_id)
        if registro is None:
            return None, "RESEARCH_RUN_NAO_ENCONTRADO"
        if registro.get("organization_id") != organizacao_id:
            return None, "RESEARCH_RUN_DE_OUTRA_ORGANIZACAO"
        return run_id, None

    def planejar(self, hipotese: dict) -> dict:
        """Modo sem banco: valida e diz o que FARIA."""
        validacao = validar_hipotese(hipotese, self.fortes, self.identidade)
        veredito = PLANEJADO_RECUSAR if validacao["problemas"] else PLANEJADO_REGISTRAR
        identidades = validacao["validos"]
        return {"veredito": veredito, "motivos": validacao["problemas"], "dor": validacao["dor"],
                "categoria": validacao["categoria"],
                "identidades_validas": [{"tipo": t, "valor": v} for t, v in identidades],
                "identidades_declaradas": validacao["declarados"],
                "evidencias": validacao["evidencias"], "descartados": validacao["descartados"],
                "confianca": validacao["confianca"],
                "research_run_id": validacao["research_run_id"],
                "input_hash": hash_da_entrada(identidades, validacao)
                if identidades and validacao["dor"] else None}

    def processar(self, hipotese: dict) -> dict:
        inicio = self.relogio()
        entrada = {"hipotese": hipotese, "ambiente": self.ambiente,
                   "correlation_id": self.correlation_id}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": ERRO, "motivos": [], "organization_id": None,
                     "pain_hypothesis_id": None, "idempotency_key": None, "categoria": None,
                     "evidencias": [], "descartados": [], "confianca": None,
                     "research_run_id": None}
        try:
            validacao = validar_hipotese(hipotese, self.fortes, self.identidade)
            resultado["categoria"] = validacao["categoria"]
            resultado["descartados"] = list(validacao["descartados"])
            resultado["confianca"] = validacao["confianca"]
            resultado["identidades_validas"] = [{"tipo": t, "valor": v}
                                                for t, v in validacao["validos"]]
            casadas = (self.organizacoes_casadas(validacao["validos"])
                       if validacao["validos"] else [])
            evidencias = []
            if not validacao["problemas"] and len(casadas) == 1:
                # So faz sentido conferir lastro quando ha UMA empresa resolvida: com zero nao ha
                # hipotese e com duas a decisao e' humana.
                evidencias, descartes = self.evidencias_existentes(validacao["evidencias"],
                                                                   casadas[0]["id"])
                resultado["descartados"].extend(descartes)
            veredito, motivo = decidir_veredito(validacao["problemas"], validacao["validos"],
                                                [c["id"] for c in casadas], len(evidencias))
            resultado["veredito"] = veredito
            resultado["motivos"] = list(validacao["problemas"]) or ([motivo] if motivo else [])
            resultado["evidencias"] = evidencias
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
                    sql_pedir_revisao(aprovacao_id, sync_event_id, chave_revisao, hipotese,
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
            elif veredito == REGISTRADA:
                organizacao_id = casadas[0]["id"]
                research_run_id = validacao["research_run_id"]
                if research_run_id:
                    research_run_id, motivo_vinculo = self.research_run_do_dono(research_run_id,
                                                                               organizacao_id)
                    if motivo_vinculo:
                        # Vinculo quebrado (ou de outra empresa): descarta o VINCULO com motivo
                        # (a hipotese segue) — a coluna e' FK de verdade, entao escrever um id
                        # inexistente derrubaria o INSERT inteiro.
                        resultado["descartados"].append({"campo": "research_run_id",
                                                         "motivo": motivo_vinculo,
                                                         "valor": validacao["research_run_id"]})
                resultado["research_run_id"] = research_run_id
                entrada_hash = hash_da_entrada(validacao["validos"], validacao)
                chave = chave_idempotencia(organizacao_id, entrada_hash)
                hipotese_id = str(uuid.uuid4())
                sync_event_id = str(uuid.uuid4())
                evidence = {
                    "inferencia": True,
                    "marcada_como_inferencia": True,
                    "evidencias": evidencias,
                    "evidencia_primaria": evidencias[0],
                    "resumo_da_evidencia": validacao["resumo"],
                    "descartados": resultado["descartados"],
                    "correlation_id": self.correlation_id,
                    "input_hash": entrada_hash,
                    "deterministico": True,
                    "llm": {"executado": False,
                            "motivo": "v1 do Pain Hypothesis e deterministica (o gate do JEV "
                                      "existe e e fail-closed)"},
                    "regra": "hipotese sem lastro nao e gravada; inferencia marcada como inferencia;"
                             " nenhum impacto/score calculado aqui e nenhuma validacao de status",
                }
                payload = {"origem": AGENTE, "agente": "%s/%s" % (AGENTE, VERSAO),
                           "correlation_id": self.correlation_id,
                           "pain_hypothesis_id": hipotese_id,
                           "organization_id": organizacao_id,
                           "pain_category": validacao["categoria"],
                           "evidencias": evidencias, "input_hash": entrada_hash,
                           "idempotency_key": chave, "descartados": resultado["descartados"],
                           "identidades": [{"tipo": t, "valor": v}
                                           for t, v in validacao["validos"]]}
                valores = {
                    "organization_id": organizacao_id,
                    "research_run_id": research_run_id,
                    "pain_category": validacao["categoria"],
                    "pain_statement": validacao["dor"],
                    "evidence_summary": validacao["resumo"],
                    "evidence": evidence,
                    "confidence": validacao["confianca"],
                    "status": STATUS_INICIAL,
                }
                rc, saida, erro = self.porta.executar(
                    sql_ingerir(hipotese_id, sync_event_id, chave, valores, payload))
                if rc != 0:
                    raise PortaIndisponivel("registro da hipotese falhou: %s" % (erro or saida))
                marcas = [l.strip() for l in saida.splitlines()]
                resultado["organization_id"] = organizacao_id
                resultado["idempotency_key"] = chave
                if MARCA_REGISTRADA in marcas:
                    resultado["pain_hypothesis_id"] = hipotese_id
                    resultado["sync_event_id"] = sync_event_id
                else:
                    # a chave ja existia: nada foi duplicado (retry idempotente)
                    resultado["veredito"] = JA_REGISTRADA
                    resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        saida_auditoria = {k: v for k, v in resultado.items() if k != "hipotese"}
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

    def rodar(self, hipoteses: list, planejar: bool = False) -> dict:
        resultados = []
        for hipotese in hipoteses:
            resultados.append(self.planejar(hipotese) if planejar
                              else self.processar(hipotese))
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
        itens = _linhas_json(saida, chave="pain_hypothesis_id")
        if not confirmo:
            return {"correlation_id": correlation_id, "confirmo": False,
                    "pain_hypotheses": [i["pain_hypothesis_id"] for i in itens],
                    "organizacoes": sorted({i["organization_id"] for i in itens}),
                    "apagados": 0, "dry_run": True}
        if itens:
            rc, saida, erro = self.porta.executar(
                sql_desfazer(itens, correlation_id, str(uuid.uuid4())), permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
        return {"correlation_id": correlation_id, "confirmo": True,
                "pain_hypotheses": [i["pain_hypothesis_id"] for i in itens],
                "organizacoes": sorted({i["organization_id"] for i in itens}),
                "apagados": len(itens), "dry_run": False}


# ---------------------------------------------------------------------------------------
# Leitura das linhas da porta (o formato nao e' prometido: `id|coluna|coluna`)
# ---------------------------------------------------------------------------------------
def _linhas_uteis(saida: str) -> list:
    linhas = []
    for linha in (saida or "").splitlines():
        linha = linha.strip()
        if not linha or linha in ("BEGIN", "COMMIT"):
            continue
        linhas.append(linha)
    return linhas


def _linhas_json(saida: str, chave: str) -> list:
    registros = []
    for linha in _linhas_uteis(saida):
        try:
            registro = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if not registro.get(chave):
            continue
        registros.append(registro)
    return registros


def _mapa_por_id(saida: str, campos_extra: tuple) -> dict:
    """`id|coluna|coluna` -> {id: {...}} (a porta devolve texto separado por `|`).

    `campos_extra` e declarado por consulta: o mesmo leitor serve as duas leituras do agente
    (`signals` e `research_runs`) sem inventar nome de coluna que a consulta nao pediu.
    """
    mapa = {}
    for linha in _linhas_uteis(saida):
        campos = linha.split("|")
        if not campos or not campos[0]:
            continue
        registro = {"id": campos[0]}
        for posicao, nome in enumerate(campos_extra, start=1):
            registro[nome] = campos[posicao] if len(campos) > posicao else None
        mapa[campos[0]] = registro
    return mapa


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_hipoteses(caminho: str) -> list:
    if caminho == "-":
        texto = sys.stdin.read()
    else:
        p = Path(caminho)
        if not p.is_file():
            raise SystemExit(EXIT_FONTE)
        texto = p.read_text(encoding="utf-8")
    hipoteses = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        try:
            hipoteses.append(json.loads(linha))
        except json.JSONDecodeError as exc:
            raise SystemExit("FALHOU linha %d da fonte nao e JSON: %s" % (numero, exc))
    return hipoteses


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agente Pain Hypothesis v1 (TRE-W4-E04-T01)")
    p.add_argument("--fonte", help="arquivo jsonl com as hipoteses ('-' = stdin)")
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
    agente = PainHypothesis(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
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
            hipoteses = ler_hipoteses(args.fonte)
            if args.planejar:
                relatorio = agente.rodar(hipoteses, planejar=True)
            else:
                agente.identificar_alvo()
                relatorio = agente.rodar(hipoteses)
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
        print("  %-20s %-20s %s" % (r["veredito"], (r.get("categoria") or "-"),
                                    ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
