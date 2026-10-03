#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiering v1 (`tiering-v1`) — classificacao de UMA empresa na faixa de tier (card TRE-W5-E06-T01).

O que este componente FAZ (e so isto): le o ULTIMO score `PRIORITY` (TRE-W5-E05-T01) da empresa em
`sales_intelligence.scores`, aplica as FAIXAS de tier do Data Contract V1.0 (`scores.tiers`) e grava a
CLASSIFICACAO no registro auditado da rodada. As faixas (A+ / A / B / C / Nurture) NAO existem em
forma executavel no codigo: elas sao LIDAS do Data Contract, que e o dono da escala — a suite reprova
se um limite de faixa aparecer escrito aqui.

O que ele NAO faz, por desenho (declarado em `score-tiering-v1.json` -> lacunas):
  - **nao cria score_type e nao escreve em `scores`**: tier nao e score. A guarda de escrita RECUSA
    qualquer INSERT/UPDATE/DELETE em `scores` (e em `organizations` e nas tabelas de negocio). Isso e
    deliberado: `scores.types` do contrato lista CINCO tipos (ICP, AUTOMATION_FIT, BUYING_SIGNAL,
    DATA_QUALITY, PRIORITY) e criar um sexto — ou uma coluna `tier` — e mudanca de contrato
    (governanca §10 + ADR-0004), decisao do dono, nao do worker. Fica declarado como lacuna;
  - **nao recalcula o PRIORITY** nem agrega os componentes: o score e a ENTRADA, lido do banco. Sem
    PRIORITY (ou com PRIORITY vencido) NAO existe tier: a rodada RECUSA (`SEM_PRIORITY` /
    `PRIORITY_VENCIDO`) e nada e gravado. "Nurture" e a faixa dos scores BAIXOS, nunca o rotulo de
    quem nao tem evidencia — inventar tier para empresa sem score seria a invencao mais cara deste
    card (a empresa entraria no funil de baixa prioridade por falta de dado, nao por fit);
  - nao faz requisicao de rede e nao chama LLM: a v1 e aritmetica declarada, deterministica;
  - nao escreve em producao (ADR-005): `--ambiente dev|homolog`, `prod` e recusado com exit 4.

Onde o tier e persistido (decisao DESTE card, declarada): nas duas estruturas que o contrato ja
governa e que o componente tem direito de escrever —
  - `sync_events` — o REGISTRO da classificacao: `operation='TIER'`, `entity_type='organization'`,
    `idempotency_key` unica e `request_payload` com o tier, a faixa usada, a identidade do PRIORITY
    lido, a tabela de faixas VIGENTE e o modelo. E a mesma trilha auditada que o card irmao
    `entity_match_confidence` (TRE-W1-E04-T02) usou pelo mesmo motivo: sem coluna nova;
  - `agent_runs` — a auditoria da rodada (inclusive da RECUSA: falha nao e engolida).

Regras que sustentam a classificacao (todas cobertas por suite):
  - **faixa lida do contrato, nunca escrita no codigo**: `scores.tiers` do Data Contract V1.0; as
    fronteiras sao conferidas a cada rodada (cobertura de 0 a 100 sem lacuna e sem sobreposicao, na
    granularidade de 2 casas do `NUMERIC(5,2)`). Contrato que nao represente a escala RECUSA
    (`CONTRATO_INCOERENTE`) em vez de classificar com regra propria;
  - **o ULTIMO PRIORITY, nao a media**: le a linha mais recente (`calculated_at DESC, id DESC`);
  - **PRIORITY vencido nao classifica**: `valid_until` no passado e lido do banco e RECUSA. A politica
    de validade do PRIORITY nasceu no card E05 (30 dias); aqui ela e apenas RESPEITADA;
  - **deterministico de verdade**: Decimal com ROUND_HALF_UP em 2 casas; mesma entrada -> mesmo tier.

Historico e idempotencia (doc 06 §7: "retry nao pode criar duplicata"): a chave e a ENTRADA —
`tier:TIER:<org>:<entrada_hash>`, gravada em `sync_events.idempotency_key` (UNIQUE). O `entrada_hash`
carrega a IDENTIDADE do PRIORITY lido (score_id, versao, valor, calculated_at, valid_until) e as
FAIXAS vigentes — nunca o relogio da rodada. Mesma entrada = replay (`JA_CLASSIFICADO`, nada novo);
PRIORITY novo = registro NOVO com o tier vigente, e o anterior preservado.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/scores/tiering/tiering.py --planejar
  python3 hermes/scores/tiering/tiering.py --faixas
  python3 hermes/scores/tiering/tiering.py --ambiente dev --organizacao <uuid> \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \\
      --relatorio /tmp/tiering-rodada.json
  python3 hermes/scores/tiering/tiering.py --desfazer <correlation_id> [--confirmo]
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
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do componente (espelha `hermes/scores/tiering/score-tiering-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "tiering"
PAPEL = "score_tiering"
VERSAO = "1.0.0"
WORKFLOW = "scoring-tiering"
WORKFLOW_VERSAO = "v1"

TIER_VERSION = "tiering-v1"
# O score que este componente LE. Nao agrega, nao recalcula: classifica o que existe.
SCORE_LIDO = "PRIORITY"
FORMULA_LITERAL = "faixa_de(scores.tiers do Data Contract) que contem o PRIORITY lido"

TABELA_SCORES = "sales_intelligence.scores"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"

# O tier ESCREVE apenas nestas duas: o registro da classificacao e a auditoria da rodada.
# `scores` NAO esta aqui de proposito (tier nao e score) — a guarda recusa com mensagem propria.
TABELAS_PERMITIDAS = (TABELA_SYNC_EVENTS, TABELA_AGENT_RUNS)
# Leitura declarada: a empresa (existencia) e o score PRIORITY (materia-prima).
TABELAS_LIDAS = (TABELA_ORGANIZACOES, TABELA_SCORES)
# Tabelas que NAO podem ser tocadas (mensagem de recusa explicita).
TABELAS_DE_OUTROS = (TABELA_SCORES, TABELA_ORGANIZACOES, "sales_intelligence.signals",
                     "sales_intelligence.pain_hypotheses", "sales_intelligence.research_runs",
                     "sales_intelligence.contacts", "sales_intelligence.interactions")

OPERACAO_SYNC = "TIER"
ENTITY_TYPE = "organization"
# Colunas de `sync_events` que o componente ESCREVE (ordem do INSERT).
COLUNAS_DO_REGISTRO = ("id", "entity_type", "entity_id", "source_system", "target_system",
                       "operation", "source_version", "idempotency_key", "status",
                       "request_payload", "created_at")

MOTIVO_SEM_PRIORITY = "SEM_PRIORITY"
MOTIVO_PRIORITY_VENCIDO = "PRIORITY_VENCIDO"
MOTIVO_PRIORITY_ILEGIVEL = "PRIORITY_ILEGIVEL"
MOTIVO_PRIORITY_FORA_DA_FAIXA = "PRIORITY_FORA_DA_FAIXA"
MOTIVO_NAO_ENCONTRADA = "ORGANIZACAO_NAO_ENCONTRADA"
MOTIVO_CONTRATO_INCOERENTE = "CONTRATO_INCOERENTE"
MOTIVO_JA_CLASSIFICADO = "ENTRADA_JA_CLASSIFICADA"

CLASSIFICADO = "CLASSIFICADO"
JA_CLASSIFICADO = "JA_CLASSIFICADO"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (CLASSIFICADO, JA_CLASSIFICADO, RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    CLASSIFICADO: "COMPLETED",
    JA_CLASSIFICADO: "COMPLETED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

PLANEJADO_CLASSIFICAR = "PLANEJADO_CLASSIFICAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_DO_SCORE_PADRAO = "hermes/scores/tiering/score-tiering-v1.json"

# Granularidade da escala: o DDL pede NUMERIC(5,2) — a faixa fechada em 2 casas e o passo do contrato.
PASSO_DA_ESCALA = Decimal("0.01")

_DATA_ISO = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d{1,6})?(Z|[+-]\d{2}(:?\d{2})?)?)?$")


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR (o contrato DESTE componente), nunca pela profundidade do arquivo."""
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_DO_SCORE_PADRAO).is_file():
                return pasta
    return Path.cwd()


RAIZ_PADRAO = descobrir_raiz_padrao()


class RecusaDeAmbiente(Exception):
    """Ambiente não permitido (ADR-005): nada é escrito."""


class GuardaDeEscritaViolada(Exception):
    """SQL tentando escrever fora das tabelas/colunas/operações declaradas."""


class PortaIndisponivel(Exception):
    """A porta de banco falhou."""


class ContratoDivergente(Exception):
    """Constante do código divergente do contrato do tier ou do Data Contract."""


# ---------------------------------------------------------------------------------------
# Contratos (o Data Contract e a fonte das faixas de tier)
# ---------------------------------------------------------------------------------------
def carregar_json(caminho) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_contrato_do_score(raiz) -> dict:
    return carregar_json(Path(raiz) / CONTRATO_DO_SCORE_PADRAO)


def faixas_do_data_contract(raiz) -> list:
    """As FAIXAS de tier vem do Data Contract V1.0 (`scores.tiers`) — nenhuma faixa no codigo."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    bruto = (contrato.get("scores") or {}).get("tiers") or []
    faixas = []
    for registro in bruto:
        faixas.append({
            "nome": str(registro.get("name")),
            "min": Decimal(str(registro.get("min"))),
            "max": Decimal(str(registro.get("max"))),
        })
    return faixas


def conferir_faixas(faixas: list) -> list:
    """Faixas tem de cobrir a escala sem lacuna e sem sobreposicao (mesma regra do verificador do
    contrato: `verificar_contrato_dados.py`). Contrato que nao represente a escala RECUSA."""
    if not faixas:
        raise ContratoDivergente("Data Contract sem `scores.tiers`: nao ha escala para classificar")
    nomes = [f["nome"] for f in faixas]
    if len(set(nomes)) != len(nomes):
        raise ContratoDivergente("faixas com nome repetido: %s" % nomes)
    ordenadas = sorted(faixas, key=lambda f: (f["min"], f["max"]))
    for f in ordenadas:
        if f["min"] > f["max"]:
            raise ContratoDivergente("faixa %r com min > max" % f["nome"])
    lacuna = ["%s->%s" % (a["nome"], b["nome"]) for a, b in zip(ordenadas, ordenadas[1:])
              if (b["min"] - a["max"]) > PASSO_DA_ESCALA or b["min"] <= a["max"]]
    if lacuna:
        raise ContratoDivergente(
            "faixas com lacuna ou sobreposicao (a escala nao esta coberta): %s" % ", ".join(lacuna))
    return list(faixas)


def escala_das_faixas(faixas: list) -> tuple:
    """A escala e DERIVADA das faixas do contrato (nada de 0/100 escrito no codigo)."""
    conferidas = conferir_faixas(faixas)
    return (min(f["min"] for f in conferidas), max(f["max"] for f in conferidas))


def classificar_tier(valor: Decimal, faixas: list) -> dict:
    """A faixa que contem o valor. Sem faixa aplicavel o contrato nao representa a escala -> RECUSA."""
    minimo, maximo = escala_das_faixas(faixas)
    if valor < minimo or valor > maximo:
        raise ContratoDivergente("valor %s fora da escala do contrato [%s, %s]" % (valor, minimo, maximo))
    for faixa in conferir_faixas(faixas):
        if faixa["min"] <= valor <= faixa["max"]:
            return faixa
    raise ContratoDivergente("valor %s nao cai em nenhuma faixa do contrato" % valor)


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_instante(valor):
    """Data -> datetime UTC. None quando não é data legível."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    texto = str(valor).strip()
    if not texto or not _DATA_ISO.match(texto):
        return None
    normalizado = texto.replace(" ", "T")
    if normalizado.endswith("Z"):
        normalizado = normalizado[:-1] + "+00:00"
    normalizado = re.sub(r"([+-]\d{2})$", r"\1:00", normalizado)
    try:
        instante = datetime.fromisoformat(normalizado)
    except ValueError:
        return None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc)


def _decimal(valor, casas="0.01") -> Decimal:
    return Decimal(str(valor)).quantize(Decimal(casas), rounding=ROUND_HALF_UP)


def _arredondar(valor) -> float:
    return float(_decimal(valor))


# ---------------------------------------------------------------------------------------
# A classificacao (pura: nenhuma conexao, nenhum relogio escondido — o "agora" entra por parametro)
# ---------------------------------------------------------------------------------------
def classificar(prioridade, faixas, agora_iso) -> dict:
    """Tier de UMA empresa a partir do ULTIMO score PRIORITY. Pura e deterministica.

    `prioridade` = {score_id, score_value, score_version, calculated_at, valid_until} ou None.
    """
    agora_dt = _parse_instante(agora_iso)
    if agora_dt is None:
        raise ValueError("agora inválido: %r" % agora_iso)
    ordenadas = conferir_faixas(faixas)
    minimo, maximo = escala_das_faixas(ordenadas)
    base = {
        "tier": None,
        "faixa": None,
        "score_lido": None,
        "escala": {"minimo": str(minimo), "maximo": str(maximo)},
        "faixas_vigentes": [{"nome": f["nome"], "min": str(f["min"]), "max": str(f["max"])}
                            for f in ordenadas],
        "motivo": None,
    }
    if prioridade is None:
        base["motivo"] = MOTIVO_SEM_PRIORITY
        base["resumo"] = ("RECUSADA: a empresa nao tem score PRIORITY — sem score nao existe faixa. "
                          "Nurture e a faixa dos scores BAIXOS, nao o rotulo de quem nao tem dado.")
        return base
    try:
        valor = _decimal(prioridade.get("score_value"))
    except Exception:
        base["motivo"] = MOTIVO_PRIORITY_ILEGIVEL
        base["resumo"] = ("RECUSADA: score_value do PRIORITY ilegivel (%r)"
                          % prioridade.get("score_value"))
        return base
    if valor < minimo or valor > maximo:
        base["motivo"] = MOTIVO_PRIORITY_FORA_DA_FAIXA
        base["resumo"] = ("RECUSADA: PRIORITY %s fora da escala do contrato [%s, %s]"
                          % (valor, minimo, maximo))
        return base
    limite = _parse_instante(prioridade.get("valid_until"))
    if limite is not None and limite <= agora_dt:
        base["motivo"] = MOTIVO_PRIORITY_VENCIDO
        base["score_lido"] = _resumo_do_score(prioridade, valor)
        base["resumo"] = ("RECUSADA: PRIORITY vencido em %s — evidencia velha nao classifica (a "
                          "politica de validade e do card E05)" % prioridade.get("valid_until"))
        return base
    faixa = classificar_tier(valor, ordenadas)
    base["tier"] = faixa["nome"]
    base["faixa"] = {"nome": faixa["nome"], "min": str(faixa["min"]), "max": str(faixa["max"])}
    base["score_lido"] = _resumo_do_score(prioridade, valor)
    base["resumo"] = ("%s = %s cai em %s [%s, %s] (score PRIORITY %s de %s)."
                      % (SCORE_LIDO, valor, faixa["nome"], faixa["min"], faixa["max"],
                         prioridade.get("score_id"), prioridade.get("calculated_at")))
    base["reconstrucao"] = ("tier derivado de `scores.tiers` do Data Contract V1.0 aplicado ao "
                            "PRIORITY lido (identity em inputs/request_payload)")
    return base


def _resumo_do_score(prioridade: dict, valor: Decimal) -> dict:
    return {
        "score_type": SCORE_LIDO,
        "score_id": str(prioridade.get("score_id")),
        "score_version": str(prioridade.get("score_version")),
        "score_value": str(valor),
        "calculated_at": str(prioridade.get("calculated_at")),
        "valid_until": str(prioridade.get("valid_until")),
    }


def hash_da_entrada(organizacao_id: str, prioridade, faixas: list) -> str:
    """A chave e a ENTRADA: empresa + IDENTIDADE do PRIORITY lido + as FAIXAS vigentes.

    Carregar o "agora" ou o tier final tornaria a chave unica a cada rodada: sem replay nao ha
    idempotencia nenhuma.
    """
    material = json.dumps({
        "organizacao": organizacao_id,
        "tier_version": TIER_VERSION,
        "score_lido": SCORE_LIDO,
        "faixas": [{"nome": f["nome"], "min": str(f["min"]), "max": str(f["max"])}
                   for f in conferir_faixas(faixas)],
        "entrada": None if prioridade is None else {
            "score_id": str(prioridade.get("score_id")),
            "score_version": str(prioridade.get("score_version")),
            "score_value": str(_decimal(prioridade.get("score_value")))
            if prioridade.get("score_value") is not None else "",
            "calculated_at": str(prioridade.get("calculated_at")),
            "valid_until": str(prioridade.get("valid_until")),
        },
    }, ensure_ascii=False, sort_keys=True)
    return _sha256(material)


def chave_idempotencia(organizacao_id: str, entrada_hash: str) -> str:
    return "tier:%s:%s:%s" % (OPERACAO_SYNC, organizacao_id, entrada_hash)


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o tier pode e nao pode escrever)
# ---------------------------------------------------------------------------------------
def _escapar(valor) -> str:
    """Literal SQL a partir de um valor Python (unica porta de entrada de dado na instrucao)."""
    if valor is None:
        return "NULL"
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, float)):
        return repr(valor)
    return "'%s'" % str(valor).replace("'", "''")


def lit(valor) -> str:
    return _escapar(valor)


def lit_json(objeto) -> str:
    return _escapar(json.dumps(objeto, ensure_ascii=False, sort_keys=True, default=str))


_ESCRITA = (
    ("insert", re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][\w\.]*)", re.I)),
    ("update", re.compile(r"\bUPDATE\s+([A-Za-z_][\w\.]*)", re.I)),
    ("delete", re.compile(r"\bDELETE\s+FROM\s+([A-Za-z_][\w\.]*)", re.I)),
)
_DDL = re.compile(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT\s+ON)\b", re.I)
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_INSERT_COLUNAS = re.compile(r"INSERT\s+INTO\s+%s\s*\(([^)]*)\)")


def _sem_literais(sql: str) -> str:
    """A instrucao SEM o conteudo dos literais: `drop` dentro de um texto nao e DDL."""
    return _LITERAL.sub("''", sql)


def colunas_do_insert(sql: str, tabela: str) -> list:
    padrao = re.compile(_INSERT_COLUNAS.pattern % re.escape(tabela), re.I)
    return [[c.strip().lower() for c in bloco.split(",") if c.strip()]
            for bloco in padrao.findall(_sem_literais(sql))]


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL e escrita fora do declarado.

    Exigencias de DESENHO do registro de tier (medidas por mutacao na suite):
    (a) o INSERT de `sync_events` declara `entity_id`, `operation`, `idempotency_key` e
    `request_payload`; (b) grava a operation declarada (`TIER`) — nao registra outro tipo de evento;
    (c) escrita em `scores` NUNCA e permitida: tier nao e score e criar um score_type novo (ou uma
    coluna) e mudanca de contrato, decisao do dono (governanca §10 + ADR-0004).
    """
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL não é permitido ao Tiering")
    for colunas in colunas_do_insert(codigo, TABELA_SYNC_EVENTS):
        faltando = [c for c in ("id", "entity_id", "operation", "idempotency_key",
                                "request_payload") if c not in colunas]
        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT do registro de tier sem coluna obrigatória do DDL: %s" % ", ".join(faltando))
    if lit(OPERACAO_SYNC) not in sql and ("INSERT INTO %s" % TABELA_SYNC_EVENTS) in sql:
        raise GuardaDeEscritaViolada(
            "INSERT em %s sem a operation declarada (%r): este componente registra so o TIER"
            % (TABELA_SYNC_EVENTS, OPERACAO_SYNC))
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela == TABELA_SCORES:
                raise GuardaDeEscritaViolada(
                    "escrita em %s não é permitida: tier NÃO é score — este componente não cria "
                    "score_type nem linha em scores (coluna/score_type de tier exige contrato novo)"
                    % tabela)
            if tabela in TABELAS_DE_OUTROS:
                raise GuardaDeEscritaViolada(
                    "%s em %s não é permitido: a empresa e o score PRIORITY são ENTRADA — quem os "
                    "escreve são a onda W4 e o card W5-E05" % (operacao.upper(), tabela))
            if tabela not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela não declarada: %s" % tabela)
            if operacao == "update" and tabela == TABELA_AGENT_RUNS:
                raise GuardaDeEscritaViolada(
                    "UPDATE em %s não é permitido: a auditoria é append-only" % tabela)
            if operacao == "delete" and not permitir_remocao:
                raise GuardaDeEscritaViolada("DELETE fora do desfazer explícito: %s" % tabela)


def sql_organizacao_existe(organizacao_id: str) -> str:
    return ("SELECT id::text FROM %s WHERE id = %s AND deleted_at IS NULL;"
            % (TABELA_ORGANIZACOES, lit(organizacao_id)))


def sql_ultimo_priority(organizacao_id: str) -> str:
    """O ULTIMO score PRIORITY da empresa, em JSON: o texto do score nunca quebra a leitura.

    `ORDER BY calculated_at DESC, id DESC LIMIT 1` e a leitura declarada de "ultimo" —
    deterministica, com o id como desempate.
    """
    campos = ["'score_type'", lit(SCORE_LIDO),
              "'score_id'", "id::text",
              "'score_value'", "score_value::text",
              "'score_version'", "COALESCE(score_version, '')",
              "'calculated_at'", "COALESCE(calculated_at::text, '')",
              "'valid_until'", "COALESCE(valid_until::text, '')"]
    return ("SELECT jsonb_build_object(%s)::text FROM %s WHERE organization_id = %s "
            "AND score_type = %s ORDER BY calculated_at DESC, id DESC LIMIT 1;"
            % (", ".join(campos), TABELA_SCORES, lit(organizacao_id), lit(SCORE_LIDO)))


def montar_linha_registro(registro_id: str, valores: dict) -> tuple:
    colunas = list(COLUNAS_DO_REGISTRO)
    expressoes = [
        lit(registro_id),
        lit(ENTITY_TYPE),
        lit(valores["organization_id"]),
        lit(valores["source_system"]),
        lit(valores["target_system"]),
        lit(OPERACAO_SYNC),
        lit(TIER_VERSION),
        lit(valores["chave"]),
        lit("REGISTERED"),
        lit_json(valores["request_payload"]),
        lit(valores["gerado_em"]),
    ]
    if len(colunas) != len(expressoes):
        raise ContratoDivergente("colunas x valores do INSERT do registro fora de paridade")
    return tuple(colunas), tuple(expressoes)


def sql_gravar_registro(registro_id: str, valores: dict) -> str:
    """UMA transacao: claim do registro (replay nao insere) + fechamento do MEU claim + prova.

    O `sync_events` e o REGISTRO da classificacao e a trava de idempotencia (`idempotency_key`
    UNIQUE). O fechamento exige a linha do id DESTA rodada: e a prova de que fui eu que registrei
    (replay devolve 0 na ultima consulta e o agente reporta JA_CLASSIFICADO sem gravar nada).
    """
    colunas, expressoes = montar_linha_registro(registro_id, valores)
    return (
        "BEGIN;\n"
        "INSERT INTO %s (%s) SELECT %s WHERE NOT EXISTS "
        "(SELECT 1 FROM %s WHERE idempotency_key = %s);\n"
        "UPDATE %s SET status = 'PROCESSED', completed_at = NOW() "
        "WHERE id = %s AND idempotency_key = %s AND status = 'REGISTERED';\n"
        "SELECT COUNT(*) FROM %s WHERE id = %s AND status = 'PROCESSED';\n"
        "COMMIT;"
        % (TABELA_SYNC_EVENTS, ", ".join(colunas), ", ".join(expressoes),
           TABELA_SYNC_EVENTS, lit(valores["chave"]),
           TABELA_SYNC_EVENTS, lit(registro_id), lit(valores["chave"]),
           TABELA_SYNC_EVENTS, lit(registro_id))
    )


def sql_registrar_execucao(run_id: str, correlation_id: str, organizacao_id: str, status: str,
                           resumo: str, entrada_hash: str, tier, registro_id: str) -> str:
    """A historia da rodada em `agent_runs` — e a ANCORA do desfazer (o `sync_events` nao tem
    `correlation_id`: o vinculo e declarado no `output` da execucao, que e auditavel)."""
    saida = json.dumps({"tier": tier, "registro_id": registro_id, "entrada_hash": entrada_hash,
                        "resumo": resumo[:400], "tier_version": TIER_VERSION,
                        "score_lido": SCORE_LIDO},
                       ensure_ascii=False, sort_keys=True)
    return (
        "INSERT INTO %s (id, agent_name, agent_role, agent_version, workflow, workflow_version, "
        "correlation_id, organization_id, status, started_at, finished_at, output) "
        "SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s "
        "WHERE NOT EXISTS (SELECT 1 FROM %s WHERE id = %s);\n"
        % (TABELA_AGENT_RUNS, lit(run_id), lit(AGENTE), lit(PAPEL), lit(VERSAO), lit(WORKFLOW),
           lit(WORKFLOW_VERSAO), lit(correlation_id), lit(organizacao_id), lit(status),
           lit(saida), TABELA_AGENT_RUNS, lit(run_id))
    )


def sql_registros_da_rodada(correlation_id: str) -> str:
    """IDs dos registros que ESTA rodada criou (via ancora em agent_runs.output)."""
    return ("SELECT jsonb_extract_path_text(output, 'registro_id') FROM %s "
            "WHERE correlation_id = %s AND agent_name = %s ORDER BY id;"
            % (TABELA_AGENT_RUNS, lit(correlation_id), lit(AGENTE)))


def sql_rodada_existe(correlation_id: str) -> str:
    return ("SELECT id::text FROM %s WHERE correlation_id = %s ORDER BY id;"
            % (TABELA_AGENT_RUNS, lit(correlation_id)))


def sql_desfazer(registros: list) -> str:
    """Desfazer apaga SO o registro que a rodada criou (e so com `--confirmo`).

    A auditoria (`agent_runs`) NAO e apagada: a rodada continua auditavel depois de desfeita.
    """
    if not registros:
        return "SELECT 0;\nSELECT 0;"
    lista = ", ".join(lit(r) for r in registros)
    return (
        "BEGIN;\n"
        "DELETE FROM %s WHERE id IN (%s);\n"
        "SELECT COUNT(*) FROM %s WHERE id IN (%s);\n"
        "COMMIT;\n"
        "SELECT COUNT(*) FROM %s WHERE id IN (%s);\n"
        % (TABELA_SYNC_EVENTS, lista, TABELA_SYNC_EVENTS, lista, TABELA_SYNC_EVENTS, lista)
    )


def sql_backup_do_desfazer(registros: list) -> str:
    """Leitura ANTES do delete: o que existia (para provar o que a rodada criou)."""
    if not registros:
        return "SELECT 0;"
    lista = ", ".join(lit(r) for r in registros)
    return "SELECT COUNT(*) FROM %s WHERE id IN (%s);" % (TABELA_SYNC_EVENTS, lista)


# ---------------------------------------------------------------------------------------
# Porta de banco (ADR-0008: o SQL roda na VPS; a porta e o prefixo psql)
# ---------------------------------------------------------------------------------------
class PortaSQL:
    """Interface da porta: quem fala com o PostgreSQL da VPS."""

    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        raise NotImplementedError


class PortaAusente(PortaSQL):
    """Sem porta configurada: qualquer tentativa de falar com o banco RECUSA."""

    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        raise PortaIndisponivel(
            "nenhuma porta de banco configurada (use --prefixo ou TRE_PSQL_PREFIXO)")


class PortaPsql(PortaSQL):
    """Executa SQL pelo prefixo psql informado (ex.: 'docker exec -i pg-sales-dev psql -U ..').

    O transporte e o MESMO dos cards irmaos (mesma forma de saida lida linha a linha), mas a guarda
    aplicada e a DESTE componente (`validar_sql`): quem classifica tier nao escreve score, e quem
    escreve score nao classifica tier. Guarda de um agente nao vale como guarda de outro.
    """

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
# Componente
# ---------------------------------------------------------------------------------------
class Tiering:
    """Classifica UMA empresa na faixa de tier do contrato, por rodada."""

    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None):
        self.raiz = Path(raiz) if raiz else RAIZ_PADRAO
        self.relogio = relogio
        self.ambiente = ambiente
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.porta = porta if porta is not None else PortaAusente()

    def contrato(self) -> dict:
        return carregar_contrato_do_score(self.raiz)

    def faixas(self) -> list:
        return faixas_do_data_contract(self.raiz)

    def conferir_contrato(self) -> dict:
        """Codigo e contrato do score nao podem divergir; as faixas sao as do Data Contract."""
        contrato = self.contrato()
        modelo = contrato.get("modelo") or {}
        faixas = self.faixas()
        nomes = [f["nome"] for f in conferir_faixas(faixas)]
        divergencias = []
        if contrato.get("tier_version") != TIER_VERSION:
            divergencias.append("tier_version")
        if contrato.get("score_lido") != SCORE_LIDO:
            divergencias.append("score_lido")
        if modelo.get("nome") != TIER_VERSION:
            divergencias.append("modelo.nome")
        if modelo.get("score_lido") != SCORE_LIDO:
            divergencias.append("modelo.score_lido")
        if sorted(str(n) for n in (modelo.get("faixas_esperadas") or ())) != sorted(nomes):
            divergencias.append("modelo.faixas_esperadas")
        if list(modelo.get("persistencia") or ()) != [TABELA_SYNC_EVENTS, TABELA_AGENT_RUNS]:
            divergencias.append("modelo.persistencia")
        if divergencias:
            raise ContratoDivergente("divergência código x contrato: %s" % ", ".join(divergencias))
        return contrato

    def conferir_ambiente(self) -> str:
        ambiente = (self.ambiente or "").strip().lower()
        if ambiente == AMBIENTE_RECUSADO:
            raise RecusaDeAmbiente(
                "ambiente %r recusado: nada nasce em produção (ADR-005)" % ambiente)
        if ambiente not in AMBIENTES_PERMITIDOS:
            raise RecusaDeAmbiente(
                "ambiente %r não declarado (use dev | homolog)" % (self.ambiente,))
        return ambiente

    def _consultar(self, sql: str, permitir_remocao: bool = False) -> list:
        if self.porta is None:
            raise PortaIndisponivel("nenhuma porta de banco configurada")
        codigo, saida, erro = self.porta.executar(sql, permitir_remocao=permitir_remocao)
        if codigo != 0:
            raise PortaIndisponivel("psql saiu com %d: %s" % (codigo, erro.strip()[:400]))
        return [linha for linha in saida.splitlines() if linha.strip()]

    def organizacao_existe(self, organizacao_id: str) -> bool:
        return bool(self._consultar(sql_organizacao_existe(organizacao_id)))

    def ler_ultimo_priority(self, organizacao_id: str):
        linhas = self._consultar(sql_ultimo_priority(organizacao_id))
        if not linhas:
            return None
        try:
            return json.loads(linhas[0])
        except json.JSONDecodeError:
            raise PortaIndisponivel("linha de PRIORITY ilegível: %r" % linhas[0][:120])

    def faixas_vigentes(self) -> dict:
        """Leitura pura do contrato: a tabela de faixas que a rodada aplicaria. Nao toca o banco."""
        self.conferir_contrato()
        ordenadas = conferir_faixas(self.faixas())
        minimo, maximo = escala_das_faixas(ordenadas)
        return {
            "agente": AGENTE,
            "tier_version": TIER_VERSION,
            "score_lido": SCORE_LIDO,
            "fonte_das_faixas": CONTRATO_DADOS_PADRAO + "#scores.tiers",
            "escala": {"minimo": str(minimo), "maximo": str(maximo)},
            "faixas": [{"nome": f["nome"], "min": str(f["min"]), "max": str(f["max"])}
                       for f in ordenadas],
            "conexao": "nenhuma: --faixas le o contrato",
        }

    def planejar(self) -> dict:
        """Plano declarado: o que seria lido e a regra aplicada. NAO abre conexao, NAO classifica."""
        contrato = self.conferir_contrato()
        faixas = conferir_faixas(self.faixas())
        minimo, maximo = escala_das_faixas(faixas)
        return {
            "agente": AGENTE,
            "versao": VERSAO,
            "tier_version": TIER_VERSION,
            "score_lido": SCORE_LIDO,
            "ambiente": None,
            "correlation_id": self.correlation_id,
            "total": 0,
            "planejamento": True,
            "contrato_do_score": contrato.get("nome") or TIER_VERSION,
            "por_veredito": {PLANEJADO_CLASSIFICAR: 0},
            "gravados": 0,
            "plano": {
                "leitura": "ultimo score PRIORITY (calculated_at DESC, id DESC)",
                "fonte_das_faixas": CONTRATO_DADOS_PADRAO + "#scores.tiers",
                "faixas": [{"nome": f["nome"], "min": str(f["min"]), "max": str(f["max"])}
                           for f in faixas],
                "escala": {"minimo": str(minimo), "maximo": str(maximo)},
                "regra_de_ausencia": ("empresa sem PRIORITY (ou com PRIORITY vencido) RECUSA "
                                      "(SEM_PRIORITY / PRIORITY_VENCIDO): Nurture e faixa de score "
                                      "baixo, nunca rotulo de quem nao tem dado"),
                "persistencia": "%s (registro) + %s (auditoria); NADA em scores"
                                % (TABELA_SYNC_EVENTS, TABELA_AGENT_RUNS),
                "conexao": "nenhuma: --planejar nao fala com o banco",
            },
            "resultados": [],
        }

    def rodar(self, organizacao_id: str) -> dict:
        contrato_agente = self.conferir_contrato()
        faixas = self.faixas()
        ordenadas = conferir_faixas(faixas)
        prioridade = self.ler_ultimo_priority(organizacao_id)
        instante = self.relogio()
        calculo = classificar(prioridade, ordenadas, instante)
        entrada_hash = hash_da_entrada(organizacao_id, prioridade, ordenadas)
        chave = chave_idempotencia(organizacao_id, entrada_hash)
        base = {
            "agente": AGENTE,
            "versao": VERSAO,
            "tier_version": TIER_VERSION,
            "score_lido": SCORE_LIDO,
            "contrato_do_score": contrato_agente.get("nome") or TIER_VERSION,
            "ambiente": self.ambiente,
            "correlation_id": self.correlation_id,
            "alvo": organizacao_id,
            "total": 1,
            "entrada_hash": entrada_hash,
            "chave_idempotencia": chave,
            "faixas_vigentes": calculo["faixas_vigentes"],
            "escala": calculo["escala"],
        }

        def recusar(motivo, resumo):
            # A RECUSA tambem e auditada: falha nao e engolida (contrato -> agent_runs REJECTED).
            run_id = str(uuid.uuid4())
            self._consultar(sql_registrar_execucao(run_id, self.correlation_id, organizacao_id,
                                                   STATUS_AGENT_RUNS[RECUSADA], resumo, entrada_hash,
                                                   None, ""))
            base["por_veredito"] = {RECUSADA: 1}
            base["gravados"] = 0
            base["resultados"] = [{"organizacao_id": organizacao_id, "veredito": RECUSADA,
                                   "tier": None, "motivo": motivo, "motivos": [motivo] if motivo else [],
                                   "resumo": resumo}]
            return base

        if not self.organizacao_existe(organizacao_id):
            return recusar(MOTIVO_NAO_ENCONTRADA, "empresa ausente: nada é classificado")
        if calculo["tier"] is None:
            return recusar(calculo["motivo"], calculo["resumo"])

        registro_id = str(uuid.uuid4())
        valores = {
            "organization_id": organizacao_id,
            "source_system": "postgresql",
            "target_system": "odoo",
            "chave": chave,
            "gerado_em": instante,
            "request_payload": {
                "tier": calculo["tier"],
                "faixa": calculo["faixa"],
                "faixas_vigentes": calculo["faixas_vigentes"],
                "escala": calculo["escala"],
                "score_lido": calculo["score_lido"],
                "tier_version": TIER_VERSION,
                "modelo": FORMULA_LITERAL,
                "fonte_das_faixas": CONTRATO_DADOS_PADRAO + "#scores.tiers",
                "entrada_hash": entrada_hash,
                "correlation_id": self.correlation_id,
                "gerado_em": instante,
                "persistencia": {
                    "em": TABELA_SYNC_EVENTS + ".request_payload",
                    "motivo": ("nao existe coluna nem score_type de tier no Data Contract V1.0; "
                               "criar exige versao nova do contrato (governanca §10 + ADR-0004)"),
                },
                "llm": {"executado": False},
            },
        }
        saida = self._consultar(sql_gravar_registro(registro_id, valores))
        prova = saida[-1].strip() if saida else ""
        gravado = prova == "1"
        run_id = str(uuid.uuid4())
        status = STATUS_AGENT_RUNS[CLASSIFICADO if gravado else JA_CLASSIFICADO]
        self._consultar(sql_registrar_execucao(run_id, self.correlation_id, organizacao_id, status,
                                               calculo["resumo"], entrada_hash, calculo["tier"],
                                               registro_id))
        veredito = CLASSIFICADO if gravado else JA_CLASSIFICADO
        base["por_veredito"] = {veredito: 1}
        base["gravados"] = 1 if gravado else 0
        base["prova_da_gravacao"] = prova
        base["resultados"] = [{
            "organizacao_id": organizacao_id,
            "veredito": veredito,
            "tier": calculo["tier"],
            "faixa": calculo["faixa"],
            "score_lido": calculo["score_lido"],
            "motivo": calculo["motivo"],
            "resumo": calculo["resumo"],
            "motivos": [MOTIVO_JA_CLASSIFICADO] if not gravado else [],
        }]
        return base

    def rodar_lote(self, organizacoes: list) -> dict:
        """Uma rodada com UM correlation_id para varias empresas (o relatorio e a soma)."""
        base, resultados, por_veredito, gravados = None, [], {}, 0
        for organizacao_id in organizacoes:
            parcial = self.rodar(organizacao_id)
            if base is None:
                base = {k: v for k, v in parcial.items()
                        if k not in ("resultados", "por_veredito", "gravados")}
            for veredito, quantos in parcial["por_veredito"].items():
                por_veredito[veredito] = por_veredito.get(veredito, 0) + quantos
            gravados += parcial.get("gravados") or 0
            resultados.extend(parcial["resultados"])
        if base is None:
            base = self.planejar()
            por_veredito = {PLANEJADO_CLASSIFICAR: 0}
        base["total"] = len(organizacoes)
        base["por_veredito"] = por_veredito
        base["gravados"] = gravados
        base["resultados"] = resultados
        return base

    def desfazer(self, correlation_id: str, confirmo: bool = False) -> dict:
        self.conferir_contrato()
        self.conferir_ambiente()
        rodada = self._consultar(sql_rodada_existe(correlation_id))
        linhas = self._consultar(sql_registros_da_rodada(correlation_id))
        itens = [linha.strip() for linha in linhas if linha.strip()]
        base = {
            "agente": AGENTE,
            "versao": VERSAO,
            "tier_version": TIER_VERSION,
            "score_lido": SCORE_LIDO,
            "ambiente": self.ambiente,
            "correlation_id": correlation_id,
            "total": len(itens),
            "dry_run": not confirmo,
            "apagados": 0,
            "rodada_existe": len(rodada) > 0,
            "resultados": [],
        }
        if not confirmo:
            base["resultados"] = [{"veredito": "DRY_RUN", "motivos": [],
                                   "resumo": "%d registro(s) seriam apagados" % len(itens)}]
            return base
        antes = self._consultar(sql_backup_do_desfazer(itens))
        saida = self._consultar(sql_desfazer(itens), permitir_remocao=True)
        try:
            existiam = int((antes[0] if antes else "0").strip() or 0)
            restaram = int((saida[-1] if saida else "0").strip() or 0)
        except ValueError:
            existiam, restaram = 0, 0
        base["apagados"] = max(0, existiam - restaram)
        base["restantes_na_rodada"] = str(restaram)
        base["resultados"] = [{"veredito": "DESFEITO", "motivos": [],
                               "resumo": "%d registro(s) apagado(s) (existiam %d, restaram %d)"
                                         % (base["apagados"], existiam, restaram)}]
        return base


# ---------------------------------------------------------------------------------------
# Fonte (escolhe o SUJEITO, nao o dado: no modo real o dado vem do banco)
# ---------------------------------------------------------------------------------------
def ler_fonte(caminho) -> list:
    itens, vistos = [], set()
    for numero, linha in enumerate(Path(caminho).read_text(encoding="utf-8").splitlines(), 1):
        texto = linha.strip()
        if not texto or texto.startswith("#"):
            continue
        try:
            registro = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ValueError("linha %d da fonte não é JSON: %s" % (numero, exc))
        organizacao_id = str(registro.get("organization_id") or "").strip()
        if not organizacao_id:
            raise ValueError("linha %d da fonte sem organization_id" % numero)
        if organizacao_id in vistos:
            continue
        vistos.add(organizacao_id)
        itens.append(organizacao_id)
    return itens


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Tiering v1 (TRE-W5-E06-T01)")
    p.add_argument("--organizacao", help="UUID da empresa (organizations.id)")
    p.add_argument("--fonte", help="arquivo JSONL com as empresas a classificar (só o sujeito)")
    p.add_argument("--ambiente", help="dev | homolog (prod é recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true", help="plano declarado; NÃO toca o banco")
    p.add_argument("--faixas", action="store_true",
                   help="tabela de faixas vigente lida do contrato; NÃO toca o banco")
    p.add_argument("--relatorio", help="caminho do relatório JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrão é dry-run)")
    p.add_argument("--raiz", default=str(RAIZ_PADRAO), help="raiz do repo (contratos)")
    return p


def main(argv=None) -> int:
    args = montar_parser().parse_args(argv)
    if not args.organizacao and not args.fonte and not args.desfazer and not args.planejar \
            and not args.faixas:
        print("uso: --organizacao <uuid> | --fonte <jsonl> | --planejar | --faixas | --desfazer <cid>")
        return EXIT_USO
    if args.faixas:
        agente = Tiering(porta=None, raiz=args.raiz)
        try:
            print(json.dumps(agente.faixas_vigentes(), ensure_ascii=False, sort_keys=True, indent=1))
        except ContratoDivergente as exc:
            print("FALHOU %s" % exc)
            return EXIT_FALHOU
        return EXIT_OK
    porta = None
    if not args.planejar:
        try:
            porta = PortaPsql(args.prefixo)
        except PortaIndisponivel as exc:
            print("FALHOU %s" % exc)
            return EXIT_USO
    agente = Tiering(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                     correlation_id=args.correlation_id)
    if args.ambiente or not args.planejar:
        try:
            agente.conferir_ambiente()
        except RecusaDeAmbiente as exc:
            print("RECUSADO_AMBIENTE %s" % exc)
            return EXIT_RECUSOU_AMBIENTE
    try:
        if args.desfazer:
            relatorio = agente.desfazer(args.desfazer, confirmo=args.confirmo)
        elif args.planejar:
            relatorio = agente.planejar()
        else:
            if args.fonte:
                organizacoes = ler_fonte(args.fonte)
            else:
                organizacoes = [args.organizacao]
            relatorio = agente.rodar_lote(organizacoes)
    except (PortaIndisponivel, ContratoDivergente) as exc:
        print("FALHOU %s" % exc)
        return EXIT_FALHOU
    except GuardaDeEscritaViolada as exc:
        print("GUARDA_VIOLADA %s" % exc)
        return EXIT_FALHOU
    if args.relatorio:
        Path(args.relatorio).write_text(json.dumps(relatorio, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({"agente": relatorio.get("agente"),
                      "tier_version": relatorio["tier_version"],
                      "score_lido": relatorio["score_lido"],
                      "ambiente": relatorio.get("ambiente"),
                      "correlation_id": relatorio["correlation_id"],
                      "total": relatorio.get("total"),
                      "por_veredito": relatorio.get("por_veredito"),
                      "gravados": relatorio.get("gravados"),
                      "apagados": relatorio.get("apagados"), "dry_run": relatorio.get("dry_run")},
                     ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-16s tier=%-8s %s %s" % (r["veredito"], r.get("tier") or "-",
                                           r.get("motivo") or "-",
                                           ", ".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
