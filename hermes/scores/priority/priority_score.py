#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Priority Score v1 — priorizacao de trabalho (card TRE-W5-E05-T01, W5/EPIC E05).

O que este componente FAZ (e so isto): le o ULTIMO score de cada um dos QUATRO componentes que ja
existem em `sales_intelligence.scores` para UMA empresa (ICP = TRE-W5-E01-T01, AUTOMATION_FIT =
TRE-W5-E02-T01, BUYING_SIGNAL = TRE-W5-E03-T01 e DATA_QUALITY = TRE-W5-E04-T01), aplica a formula
V1 do Data Contract §8 (PRIORITY = 0,35*ICP + 0,30*AUTOMATION_FIT + 0,25*BUYING_SIGNAL +
0,10*DATA_QUALITY, pesos que somam exatamente 1,00) e GRAVA a LINHA do score em `scores` com
`score_type='PRIORITY'` e `score_version='priority-v1'` (score sem versao nao e reprodutivel: doc 12 §8).

O que ele NAO faz, por desenho (declarado em `score-priority-v1.json` -> lacunas):
  - nao CALCULA nem REESCREVE os scores irmaos: os componentes sao entradas, nao saida. UPDATE em
    `scores` e recusa SEMPRE (score e historico, nao mutavel); recalcular INSERE linha nova;
  - nao escreve em `organizations`, `signals`, `pain_hypotheses` ou `research_runs`: a guarda de
    escrita recusa qualquer INSERT/UPDATE/DELETE fora de `scores`, `agent_runs` e `sync_events`;
  - nao define tier (`A+`/`A`/`B`/`C`/`Nurture`): tiering e o card W5-E06-T01, que LE este score.
    Aqui nao existe faixa de tier, nem `next_best_action`, nem evento de outbox;
  - nao faz requisicao de rede e nao chama LLM: a v1 e aritmetica declarada, deterministica;
  - nao escreve em producao (ADR-005): `--ambiente dev|homolog`, `prod` e recusado com exit 4.

Formula V1 (`priority-v1`) — pesos LIDOS do Data Contract (`scores.priority_weights`), nenhum peso
em forma executavel no codigo (item proprio da suite reprova se um aparecer):

    PRIORITY = 0.35 * ICP + 0.30 * AUTOMATION_FIT + 0.25 * BUYING_SIGNAL + 0.10 * DATA_QUALITY

Regras que sustentam o numero (todas cobertas por suite):
  - **componente ausente nao pontua**: com a formula da casa, "componente que falta" nao pode ser
    lido como zero (isso puniria a empresa por um score que ninguem calculou). O Priority Score V1 e
    **fail-closed**: exige os QUATRO componentes (cobertura 1,00 = os pesos somam 1,00) e RECUSA com
    motivo `SEM_LASTRO_COMPLETO` + a lista nominal dos que faltam. Renormalizar a formula para
    cobrir ausencia criaria um NUMERO NOVO (nao seria a formula do contrato) e inflaria a prioridade
    de quem tem menos evidencia — fica declarado como `priority-v2`, decisao do dono, nao do worker;
  - **componente vencido nao e evidenccia**: componente com `valid_until` no passado e tratado como
    AUSENTE (motivo `COMPONENTE_VENCIDO`). E aqui que a politica de validade do score nasce (o ICP
    deixou `valid_until` NULL declarando que a politica era deste card): o PRIORITY vence em 30 dias
    (`valid_until = calculated_at + 30d`) e componente vencido nao sustenta prioridade;
  - **o ULTIMO de cada componente, nao a media**: le a linha mais recente por `score_type`
    (`calculated_at DESC, id DESC`). Historico antigo nao entra: media de evidencia velha com nova
    nao e um numero que alguem consiga reconstruir;
  - **deterministico de verdade**: Decimal com ROUND_HALF_UP em 2 casas (o DDL pede NUMERIC(5,2)),
    nunca float solto; mesma entrada -> mesmo valor, bit a bit.

Historico e idempotencia (doc 06 §7: "retry nao pode criar duplicata"): a chave e a ENTRADA —
`score:PRIORITY:<org>:<entrada_hash>`, gravada em `sync_events.idempotency_key` (UNIQUE). O
`entrada_hash` carrega a IDENTIDADE dos quatro componentes usados (tipo, id do score, versao, valor,
`calculated_at`) e os pesos — nunca o relogio da rodada. Rodada igual = replay (`JA_CALCULADO`, nada
escrito); componente novo ou valor diferente = LINHA NOVA com a anterior preservada. A gravacao e UMA
transacao: claim da chave + INSERT ancorado no claim -> fechamento do `sync_events` ancorado no score
DESTA rodada. A historia da rodada fica em `agent_runs` com o `correlation_id` do lote.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/scores/priority/priority_score.py --planejar
  python3 hermes/scores/priority/priority_score.py --ambiente dev --organizacao <uuid> \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \\
      --relatorio /tmp/priority-rodada.json
  python3 hermes/scores/priority/priority_score.py --ambiente dev --fonte organizacoes.jsonl \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
  python3 hermes/scores/priority/priority_score.py --desfazer <correlation_id>
  python3 hermes/scores/priority/priority_score.py --desfazer <correlation_id> --confirmo
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
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do componente (espelha `hermes/scores/priority/score-priority-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "priority_score"
PAPEL = "score_aggregation"
VERSAO = "1.0.0"
WORKFLOW = "scoring-priority"
WORKFLOW_VERSAO = "v1"

SCORE_TYPE = "PRIORITY"
SCORE_VERSION = "priority-v1"

# Ordem DECLARADA dos componentes (a formula do contrato §8). Os PESOS nao aparecem aqui de
# proposito: eles vem do Data Contract (`scores.priority_weights`), que e o dono da formula.
COMPONENTES = ("ICP", "AUTOMATION_FIT", "BUYING_SIGNAL", "DATA_QUALITY")

FORMULA_LITERAL = "SUM(peso_do_componente * score_do_componente) para os componentes do Data Contract"

TABELA_SCORES = "sales_intelligence.scores"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"

# O score ESCREVE apenas nestas tres: o resultado, a auditoria da rodada e a trava de idempotencia.
TABELAS_PERMITIDAS = (TABELA_SCORES, TABELA_AGENT_RUNS, TABELA_SYNC_EVENTS)
# Leitura declarada: a empresa (existencia) e os scores dos componentes (materia-prima).
TABELAS_LIDAS = (TABELA_ORGANIZACOES, TABELA_SCORES)
# Tabelas de negocio que NAO podem ser tocadas (mensagem de recusa explicita).
TABELAS_DE_OUTROS = ("sales_intelligence.signals", "sales_intelligence.pain_hypotheses",
                     "sales_intelligence.research_runs", "sales_intelligence.contacts",
                     "sales_intelligence.interactions")

OPERACAO_SYNC = "SCORE"
ENTITY_TYPE_SCORE = "score"

# Cobertura: os pesos do contrato somam 1,00 e este card NAO renormaliza (fail-closed).
COBERTURA_MINIMA = Decimal("1.00")

# Politica de validade DESTE card (lacuna declarada pelos cards irmaos): o PRIORITY vence.
VALIDADE_DIAS = 30

MOTIVO_AUSENTE = "COMPONENTE_AUSENTE"
MOTIVO_VENCIDO = "COMPONENTE_VENCIDO"
MOTIVO_SEM_LASTRO = "SEM_LASTRO_COMPLETO"
MOTIVO_NAO_ENCONTRADA = "ORGANIZACAO_NAO_ENCONTRADA"
MOTIVO_JA_CALCULADO = "ENTRADA_JA_CALCULADA"
MOTIVO_SCORE_ILEGIVEL = "SCORE_DO_COMPONENTE_ILEGIVEL"
MOTIVO_FORA_DA_FAIXA = "SCORE_DO_COMPONENTE_FORA_DA_FAIXA"

CALCULADO = "CALCULADO"
JA_CALCULADO = "JA_CALCULADO"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (CALCULADO, JA_CALCULADO, RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    CALCULADO: "COMPLETED",
    JA_CALCULADO: "COMPLETED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

PLANEJADO_CALCULAR = "PLANEJADO_CALCULAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_DO_SCORE_PADRAO = "hermes/scores/priority/score-priority-v1.json"

# Colunas de `scores` que o componente ESCREVE (ordem do INSERT).
COLUNAS_DO_SCORE = (
    "id",
    "organization_id",
    "score_type",
    "score_value",
    "score_version",
    "inputs",
    "explanation",
    "calculated_at",
    "valid_until",
)

_DATA_ISO = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d{1,6})?(Z|[+-]\d{2}(:?\d{2})?)?)?$")


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR (o contrato DESTE score), nunca pela profundidade do arquivo."""
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
    """Constante do código divergente do contrato do score ou do Data Contract."""


# ---------------------------------------------------------------------------------------
# Contratos (o Data Contract e a fonte dos pesos)
# ---------------------------------------------------------------------------------------
def carregar_json(caminho) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_contrato_do_score(raiz) -> dict:
    return carregar_json(Path(raiz) / CONTRATO_DO_SCORE_PADRAO)


def pesos_do_data_contract(raiz) -> dict:
    """Os pesos do PRIORITY vem do Data Contract V1.0 (`scores.priority_weights`)."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    pesos = (contrato.get("scores") or {}).get("priority_weights") or {}
    return {str(k): Decimal(str(v)) for k, v in pesos.items()}


def tipos_do_data_contract(raiz) -> tuple:
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    return tuple((contrato.get("scores") or {}).get("types") or ())


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
    aplicada e a DESTE score (`validar_sql`): quem agrega score nao escreve componente, e quem
    escreve componente nao agrega prioridade. Guarda de um agente nao vale como guarda de outro.
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
    # O texto do psql usa "+00" (fuso sem minutos): fromisoformat recusa essa forma.
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
# O calculo (puro: nenhuma conexao, nenhum relogio escondido — o "agora" entra por parametro)
# ---------------------------------------------------------------------------------------
def calcular_priority(componentes: dict, pesos: dict, agora_iso: str) -> dict:
    """Score de UMA empresa a partir do ULTIMO score de cada componente. Puro e determinístico.

    `componentes` = {tipo: {score_id, score_value, score_version, calculated_at, valid_until}}.
    """
    agora_dt = _parse_instante(agora_iso)
    if agora_dt is None:
        raise ValueError("agora inválido: %r" % agora_iso)
    faltando = [t for t in COMPONENTES if t not in pesos]
    if faltando:
        raise ContratoDivergente("Data Contract sem peso para: %s" % ", ".join(faltando))

    detalhe, presentes, ausentes, vencidos, motivos = {}, [], [], [], []
    total = Decimal("0")
    for tipo in COMPONENTES:
        peso = pesos[tipo]
        bruto = componentes.get(tipo)
        if bruto is None:
            ausentes.append(tipo)
            motivos.append("%s:%s" % (MOTIVO_AUSENTE, tipo))
            detalhe[tipo] = {"presente": False, "motivo": MOTIVO_AUSENTE, "peso": str(peso)}
            continue
        try:
            valor = _decimal(bruto.get("score_value"))
        except Exception:
            ausentes.append(tipo)
            motivos.append("%s:%s" % (MOTIVO_SCORE_ILEGIVEL, tipo))
            detalhe[tipo] = {"presente": False, "motivo": MOTIVO_SCORE_ILEGIVEL,
                             "peso": str(peso), "valor_bruto": str(bruto.get("score_value"))}
            continue
        if valor < 0 or valor > 100:
            ausentes.append(tipo)
            motivos.append("%s:%s" % (MOTIVO_FORA_DA_FAIXA, tipo))
            detalhe[tipo] = {"presente": False, "motivo": MOTIVO_FORA_DA_FAIXA,
                             "peso": str(peso), "valor_bruto": str(bruto.get("score_value"))}
            continue
        limite = _parse_instante(bruto.get("valid_until"))
        vencido = limite is not None and limite <= agora_dt
        if vencido:
            vencidos.append(tipo)
            motivos.append("%s:%s" % (MOTIVO_VENCIDO, tipo))
            detalhe[tipo] = {"presente": False, "motivo": MOTIVO_VENCIDO, "peso": str(peso),
                             "score_id": bruto.get("score_id"), "score_value": str(valor),
                             "valid_until": bruto.get("valid_until")}
            continue
        parcela = _decimal(peso * valor)
        presentes.append(tipo)
        total += peso * valor
        detalhe[tipo] = {
            "presente": True,
            "peso": str(peso),
            "score_id": bruto.get("score_id"),
            "score_version": bruto.get("score_version"),
            "score_value": str(valor),
            "calculated_at": bruto.get("calculated_at"),
            "valid_until": bruto.get("valid_until"),
            "parcela": str(parcela),
        }
    cobertura = sum((pesos[t] for t in presentes), Decimal("0"))
    cobertura = _decimal(cobertura)
    ultimo = ({t: v["calculated_at"] for t, v in detalhe.items() if v["presente"] and v["calculated_at"]}
              or None)
    base = {
        "componentes": detalhe,
        "presentes": presentes,
        "ausentes": ausentes,
        "vencidos": vencidos,
        "cobertura": str(cobertura),
        "cobertura_minima": str(COBERTURA_MINIMA),
        "pesos": {t: str(p) for t, p in pesos.items()},
        "motivos": motivos,
    }
    if cobertura < COBERTURA_MINIMA:
        faltam = sorted(set(ausentes + vencidos), key=COMPONENTES.index)
        base.update({
            "score_value": None,
            "motivo": MOTIVO_SEM_LASTRO,
            "componentes_faltantes": faltam,
            "soma_das_parcelas": str(_decimal(total)),
            "resumo": ("RECUSADA: lastro incompleto (cobertura %s < %s). Falta %s. Sem os quatro "
                       "componentes o numero nao e o do contrato — nao se renormaliza para "
                       "preencher ausencia."
                       % (cobertura, COBERTURA_MINIMA, ", ".join(faltam) or "nada")),
            "reconstrucao": "sem valor: nada foi gravado",
        })
        return base
    valor_final = _decimal(total)
    if valor_final < 0 or valor_final > 100:
        raise ContratoDivergente("PRIORITY fora de 0..100: %s" % valor_final)
    base.update({
        "score_value": float(valor_final),
        "motivo": None,
        "componentes_faltantes": [],
        "soma_das_parcelas": str(valor_final),
        "resumo": ("%s = %s (cobertura %s dos %d componentes): %s."
                   % ("PRIORITY", valor_final, cobertura, len(COMPONENTES),
                      " + ".join("%s*%s" % (detalhe[t]["peso"], detalhe[t].get("score_value", "-"))
                                 for t in COMPONENTES))),
        "reconstrucao": ("peso x valor de cada componente em explanation.componentes; a identidade de "
                         "cada componente usado esta em inputs.componentes"),
    })
    return base


def hash_das_entradas(organizacao_id: str, componentes: dict, pesos: dict) -> str:
    """A chave e a ENTRADA: empresa + IDENTIDADE dos componentes usados (nunca o relogio).

    Carregar o "agora" ou o valor final tornaria a chave unica a cada rodada: sem replay nao ha
    idempotencia nenhuma (defeito medido no card irmao do BUYING_SIGNAL).
    """
    material = json.dumps({
        "organizacao": organizacao_id,
        "score_type": SCORE_TYPE,
        "score_version": SCORE_VERSION,
        "componentes": sorted(COMPONENTES),
        "pesos": {t: str(p) for t, p in pesos.items()},
        "cobertura_minima": str(COBERTURA_MINIMA),
        "entradas": {t: {"score_id": str(c.get("score_id")), "score_version": str(c.get("score_version")),
                         "score_value": str(_decimal(c.get("score_value"))),
                         "calculated_at": str(c.get("calculated_at")),
                         "valid_until": str(c.get("valid_until"))}
                     for t, c in sorted(componentes.items())},
    }, ensure_ascii=False, sort_keys=True)
    return _sha256(material)


def chave_idempotencia(organizacao_id: str, entrada_hash: str) -> str:
    return "score:%s:%s:%s" % (SCORE_TYPE, organizacao_id, entrada_hash)


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o score pode e nao pode escrever)
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

    Exigencias de DESENHO do INSERT de `scores` (medidas por mutacao na suite):
    (a) declara `organization_id`, `score_type`, `score_value` e `score_version` (NOT NULL do DDL e
    a versao exigida pelo contrato §8); (b) o `score_type` gravado e EXATAMENTE o declarado — o
    agregador nao pode gravar linha de outro tipo; (c) UPDATE em `scores` NUNCA e permitido (score e
    historico: recalcular insere linha nova).
    """
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL não é permitido ao Priority Score")
    for colunas in colunas_do_insert(codigo, TABELA_SCORES):
        faltando = [c for c in ("id", "organization_id", "score_type", "score_value",
                                "score_version") if c not in colunas]
        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT de score sem coluna obrigatória do DDL/contrato: %s" % ", ".join(faltando))
    if lit(SCORE_TYPE) not in sql and ("INSERT INTO %s" % TABELA_SCORES) in sql:
        raise GuardaDeEscritaViolada(
            "INSERT em %s sem o score_type declarado (%r): o agregador grava so a linha do "
            "PRIORITY" % (TABELA_SCORES, SCORE_TYPE))
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela in TABELAS_DE_OUTROS:
                raise GuardaDeEscritaViolada(
                    "%s em %s não é permitido: os componentes e a empresa são ENTRADA — quem os "
                    "escreve são os cards W5-E01..E04 e a onda W4" % (operacao.upper(), tabela))
            if tabela not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela não declarada: %s" % tabela)
            if operacao == "update" and tabela == TABELA_SCORES:
                raise GuardaDeEscritaViolada(
                    "UPDATE em %s não é permitido: o score é histórico, não mutável" % tabela)
            if operacao == "delete" and not permitir_remocao:
                raise GuardaDeEscritaViolada("DELETE fora do desfazer explícito: %s" % tabela)


def sql_organizacao_existe(organizacao_id: str) -> str:
    return ("SELECT id::text FROM %s WHERE id = %s AND deleted_at IS NULL;"
            % (TABELA_ORGANIZACOES, lit(organizacao_id)))


def sql_componentes_da_organizacao(organizacao_id: str) -> str:
    """O ULTIMO score de cada componente, em JSON: o texto do score nunca quebra a leitura.

    `DISTINCT ON (score_type)` com `ORDER BY score_type, calculated_at DESC, id DESC` e a leitura
    declarada de "ultimo por componente" — deterministica, com o id como desempate.
    """
    tipos = ", ".join(lit(t) for t in COMPONENTES)
    campos = ["'score_type'", "score_type",
              "'score_id'", "id::text",
              "'score_value'", "score_value::text",
              "'score_version'", "COALESCE(score_version, '')",
              "'calculated_at'", "COALESCE(calculated_at::text, '')",
              "'valid_until'", "COALESCE(valid_until::text, '')"]
    return ("SELECT jsonb_build_object(%s)::text FROM ("
            "SELECT DISTINCT ON (score_type) * FROM %s WHERE organization_id = %s "
            "AND score_type IN (%s) ORDER BY score_type, calculated_at DESC, id DESC) t "
            "ORDER BY score_type;"
            % (", ".join(campos), TABELA_SCORES, lit(organizacao_id), tipos))


def montar_linha_score(score_id: str, valores: dict) -> tuple:
    colunas = list(COLUNAS_DO_SCORE)
    expressoes = [
        lit(score_id),
        lit(valores["organization_id"]),
        lit(SCORE_TYPE),
        repr(float(valores["score_value"])),
        lit(SCORE_VERSION),
        lit_json(valores["inputs"]),
        lit_json(valores["explanation"]),
        lit(valores["calculated_at"]),
        lit(valores["valid_until"]),
    ]
    if len(colunas) != len(expressoes):
        raise ContratoDivergente("colunas x valores do INSERT de score fora de paridade")
    return tuple(colunas), tuple(expressoes)


def sql_gravar_score(score_id: str, sync_event_id: str, chave: str, valores: dict) -> str:
    """UMA transacao: claim da chave (replay nao insere) + INSERT ancorado no claim + fechamento.

    O `sync_events` e a TRAVA (idempotency_key UNIQUE) e a ancora: sem o claim desta rodada o INSERT
    nao acontece, e sem o INSERT o fechamento nao marca PROCESSED. A ultima consulta conta o score
    DESTA rodada — e a prova da gravacao (sem ela o agente recusa).
    """
    colunas, expressoes = montar_linha_score(score_id, valores)
    return (
        "BEGIN;\n"
        "INSERT INTO %s (id, entity_type, entity_id, operation, idempotency_key, status, "
        "created_at) SELECT %s, %s, %s, %s, %s, 'REGISTERED', NOW() "
        "WHERE NOT EXISTS (SELECT 1 FROM %s WHERE idempotency_key = %s);\n"
        "INSERT INTO %s (%s) SELECT %s WHERE EXISTS (SELECT 1 FROM %s WHERE idempotency_key = %s "
        "AND status = 'REGISTERED');\n"
        "UPDATE %s SET status = 'PROCESSED', completed_at = NOW() "
        "WHERE idempotency_key = %s AND EXISTS (SELECT 1 FROM %s WHERE id = %s);\n"
        "SELECT COUNT(*) FROM %s WHERE id = %s;\n"
        "COMMIT;"
        % (TABELA_SYNC_EVENTS, lit(sync_event_id), lit(ENTITY_TYPE_SCORE), lit(score_id),
           lit(OPERACAO_SYNC), lit(chave), TABELA_SYNC_EVENTS, lit(chave),
           TABELA_SCORES, ", ".join(colunas), ", ".join(expressoes), TABELA_SYNC_EVENTS, lit(chave),
           TABELA_SYNC_EVENTS, lit(chave), TABELA_SCORES, lit(score_id),
           TABELA_SCORES, lit(score_id))
    )


def sql_registrar_execucao(run_id: str, correlation_id: str, organizacao_id: str, status: str,
                           resumo: str, entrada_hash: str, score_value, score_id: str) -> str:
    """A historia da rodada em `agent_runs` — e a ANCORA do desfazer (o `scores` nao tem
    `correlation_id`: o vinculo e declarado no `output` da execucao, que e auditavel)."""
    saida = json.dumps({"score_id": score_id, "entrada_hash": entrada_hash,
                        "score_value": score_value, "resumo": resumo[:400],
                        "score_type": SCORE_TYPE, "score_version": SCORE_VERSION},
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


def sql_scores_da_rodada(correlation_id: str) -> str:
    """IDs dos scores que ESTA rodada criou (via ancora em agent_runs.output)."""
    return ("SELECT jsonb_extract_path_text(output, 'score_id') FROM %s "
            "WHERE correlation_id = %s AND agent_name = %s ORDER BY id;"
            % (TABELA_AGENT_RUNS, lit(correlation_id), lit(AGENTE)))


def sql_rodada_existe(correlation_id: str) -> str:
    return ("SELECT id::text FROM %s WHERE correlation_id = %s ORDER BY id;"
            % (TABELA_AGENT_RUNS, lit(correlation_id)))


def sql_desfazer(scores: list) -> str:
    """Desfazer apaga SO o que a rodada criou (e so com `--confirmo`)."""
    if not scores:
        return "SELECT 0;\nSELECT 0;"
    lista = ", ".join(lit(s) for s in scores)
    return (
        "BEGIN;\n"
        "DELETE FROM %s WHERE id IN (%s);\n"
        "SELECT COUNT(*) FROM %s WHERE id IN (%s);\n"
        "COMMIT;\n"
        "SELECT COUNT(*) FROM %s WHERE id IN (%s);\n"
        % (TABELA_SCORES, lista, TABELA_SCORES, lista, TABELA_SCORES, lista)
    )


def sql_backup_do_desfazer(scores: list) -> str:
    """Leitura ANTES do delete: o que existia (para provar o que a rodada criou)."""
    if not scores:
        return "SELECT 0;"
    lista = ", ".join(lit(s) for s in scores)
    return "SELECT COUNT(*) FROM %s WHERE id IN (%s);" % (TABELA_SCORES, lista)


# ---------------------------------------------------------------------------------------
# Componente
# ---------------------------------------------------------------------------------------
class PriorityScore:
    """Calcula e grava o score PRIORITY de UMA empresa por rodada."""

    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None):
        self.raiz = Path(raiz) if raiz else RAIZ_PADRAO
        self.relogio = relogio
        self.ambiente = ambiente
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.porta = porta if porta is not None else PortaAusente()

    def contrato(self) -> dict:
        return carregar_contrato_do_score(self.raiz)

    def pesos(self) -> dict:
        return pesos_do_data_contract(self.raiz)

    def conferir_contrato(self) -> dict:
        """Codigo e contrato do score nao podem divergir; os pesos sao os do Data Contract."""
        contrato = self.contrato()
        modelo = contrato.get("modelo") or {}
        pesos = self.pesos()
        divergencias = []
        if contrato.get("score_type") != SCORE_TYPE:
            divergencias.append("score_type")
        if contrato.get("score_version") != SCORE_VERSION:
            divergencias.append("score_version")
        if modelo.get("nome") != SCORE_VERSION:
            divergencias.append("modelo.nome")
        if tuple(modelo.get("componentes") or ()) != COMPONENTES:
            divergencias.append("modelo.componentes")
        if Decimal(str(modelo.get("cobertura_minima"))) != COBERTURA_MINIMA:
            divergencias.append("modelo.cobertura_minima")
        if modelo.get("validade_dias") != VALIDADE_DIAS:
            divergencias.append("modelo.validade_dias")
        if tuple(sorted(pesos)) != tuple(sorted(COMPONENTES)):
            divergencias.append("pesos_vs_componentes")
        if sum(pesos.values()) != Decimal("1.00"):
            divergencias.append("pesos_nao_somam_1")
        if SCORE_TYPE not in tipos_do_data_contract(self.raiz):
            divergencias.append("score_type_fora_do_data_contract")
        if {t: str(v) for t, v in pesos.items()} != {t: str(Decimal(str(v)))
                                                     for t, v in (modelo.get("pesos") or {}).items()}:
            divergencias.append("pesos_vs_contrato_do_score")
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

    def ler_componentes(self, organizacao_id: str) -> dict:
        linhas = self._consultar(sql_componentes_da_organizacao(organizacao_id))
        componentes = {}
        for linha in linhas:
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                raise PortaIndisponivel("linha de score ilegível: %r" % linha[:120])
            componentes[str(registro.get("score_type"))] = registro
        return componentes

    def planejar(self) -> dict:
        """Plano declarado: o que seria lido e a regra aplicada. NAO abre conexao, NAO calcula."""
        contrato = self.conferir_contrato()
        pesos = self.pesos()
        return {
            "agente": AGENTE,
            "versao": VERSAO,
            "score_type": SCORE_TYPE,
            "score_version": SCORE_VERSION,
            "ambiente": None,
            "correlation_id": self.correlation_id,
            "total": 0,
            "planejamento": True,
            "contrato_do_score": contrato.get("nome") or SCORE_VERSION,
            "por_veredito": {PLANEJADO_CALCULAR: 0},
            "gravados": 0,
            "plano": {
                "componentes": list(COMPONENTES),
                "pesos": {t: str(p) for t, p in pesos.items()},
                "soma_dos_pesos": str(sum(pesos.values())),
                "cobertura_minima": str(COBERTURA_MINIMA),
                "leitura": "ultimo score por score_type (calculated_at DESC, id DESC)",
                "regra_de_ausencia": ("componente ausente ou vencido NAO pontua e RECUSA o score "
                                      "(SEM_LASTRO_COMPLETO); nao ha renormalizacao"),
                "validade_dias": VALIDADE_DIAS,
                "conexao": "nenhuma: --planejar nao fala com o banco",
            },
            "resultados": [],
        }

    def rodar(self, organizacao_id: str) -> dict:
        contrato_agente = self.conferir_contrato()
        pesos = self.pesos()
        componentes = self.ler_componentes(organizacao_id)
        instante = self.relogio()
        calculo = calcular_priority(componentes, pesos, instante)
        entrada_hash = hash_das_entradas(organizacao_id, componentes, pesos)
        chave = chave_idempotencia(organizacao_id, entrada_hash)
        base = {
            "agente": AGENTE,
            "versao": VERSAO,
            "score_type": SCORE_TYPE,
            "score_version": SCORE_VERSION,
            "contrato_do_score": contrato_agente.get("nome") or SCORE_VERSION,
            "ambiente": self.ambiente,
            "correlation_id": self.correlation_id,
            "alvo": organizacao_id,
            "total": 1,
            "entrada_hash": entrada_hash,
            "chave_idempotencia": chave,
            "cobertura": calculo["cobertura"],
            "componentes_presentes": calculo["presentes"],
            "componentes_ausentes": calculo["ausentes"] + calculo["vencidos"],
        }

        def recusar(motivos, resumo):
            # A RECUSA tambem e auditada: falha nao e engolida (contrato -> agent_runs REJECTED).
            run_id = str(uuid.uuid4())
            self._consultar(sql_registrar_execucao(run_id, self.correlation_id, organizacao_id,
                                                   STATUS_AGENT_RUNS[RECUSADA], resumo, entrada_hash,
                                                   None, ""))
            base["por_veredito"] = {RECUSADA: 1}
            base["gravados"] = 0
            base["resultados"] = [{"organizacao_id": organizacao_id, "veredito": RECUSADA,
                                   "score_value": None, "motivo": calculo["motivo"],
                                   "motivos": motivos, "resumo": resumo}]
            return base

        if not self.organizacao_existe(organizacao_id):
            return recusar([MOTIVO_NAO_ENCONTRADA], "empresa ausente: nada é calculado")
        if calculo["score_value"] is None:
            return recusar(calculo["motivos"], calculo["resumo"])

        score_id = str(uuid.uuid4())
        sync_event_id = str(uuid.uuid4())
        instante_dt = _parse_instante(instante)
        if instante_dt is None:
            raise ContratoDivergente("relógio devolveu instante ilegível: %r" % instante)
        limite = instante_dt + timedelta(days=VALIDADE_DIAS)
        valores = {
            "organization_id": organizacao_id,
            "score_value": calculo["score_value"],
            "calculated_at": instante,
            "valid_until": limite.isoformat(),
            "inputs": {
                "score_type": SCORE_TYPE,
                "score_version": SCORE_VERSION,
                "componentes": {t: v for t, v in calculo["componentes"].items() if v.get("presente")},
                "componentes_ausentes": calculo["ausentes"],
                "componentes_vencidos": calculo["vencidos"],
                "cobertura": calculo["cobertura"],
                "cobertura_minima": calculo["cobertura_minima"],
                "pesos": calculo["pesos"],
                "entrada_hash": entrada_hash,
                "correlation_id": self.correlation_id,
                "gerado_em": instante,
            },
            "explanation": {
                "resumo": calculo["resumo"],
                "motivo": calculo["motivo"],
                "formula": FORMULA_LITERAL,
                "componentes": calculo["componentes"],
                "soma_das_parcelas": calculo["soma_das_parcelas"],
                "cobertura": calculo["cobertura"],
                "regras_declaradas": {
                    "cobertura_minima": calculo["cobertura_minima"],
                    "sem_renormalizacao": True,
                    "leitura_do_componente": "ultimo score por score_type",
                    "validade_dias": VALIDADE_DIAS,
                },
                "reconstrucao": calculo["reconstrucao"],
                "deterministico": True,
                "llm": {"executado": False, "motivo": "v1 é aritmética declarada"},
            },
        }
        saida = self._consultar(sql_gravar_score(score_id, sync_event_id, chave, valores))
        prova = saida[-1].strip() if saida else ""
        gravado = prova == "1"
        run_id = str(uuid.uuid4())
        status = STATUS_AGENT_RUNS[CALCULADO if gravado else JA_CALCULADO]
        self._consultar(sql_registrar_execucao(run_id, self.correlation_id, organizacao_id, status,
                                               calculo["resumo"], entrada_hash,
                                               calculo["score_value"], score_id))
        veredito = CALCULADO if gravado else JA_CALCULADO
        base["por_veredito"] = {veredito: 1}
        base["gravados"] = 1 if gravado else 0
        base["prova_da_gravacao"] = prova
        base["resultados"] = [{
            "organizacao_id": organizacao_id,
            "veredito": veredito,
            "score_value": calculo["score_value"],
            "motivo": calculo["motivo"],
            "resumo": calculo["resumo"],
            "motivos": [MOTIVO_JA_CALCULADO] if not gravado else [],
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
            por_veredito = {PLANEJADO_CALCULAR: 0}
        base["total"] = len(organizacoes)
        base["por_veredito"] = por_veredito
        base["gravados"] = gravados
        base["resultados"] = resultados
        return base

    def desfazer(self, correlation_id: str, confirmo: bool = False) -> dict:
        self.conferir_contrato()
        self.conferir_ambiente()
        rodada = self._consultar(sql_rodada_existe(correlation_id))
        linhas = self._consultar(sql_scores_da_rodada(correlation_id))
        itens = [linha.strip() for linha in linhas if linha.strip()]
        base = {
            "agente": AGENTE,
            "versao": VERSAO,
            "score_type": SCORE_TYPE,
            "score_version": SCORE_VERSION,
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
                                   "resumo": "%d score(s) seriam apagados" % len(itens)}]
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
                               "resumo": "%d score(s) apagado(s) (existiam %d, restaram %d)"
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
    p = argparse.ArgumentParser(description="Priority Score v1 (TRE-W5-E05-T01)")
    p.add_argument("--organizacao", help="UUID da empresa (organizations.id)")
    p.add_argument("--fonte", help="arquivo JSONL com as empresas a priorizar (só o sujeito)")
    p.add_argument("--ambiente", help="dev | homolog (prod é recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true", help="plano declarado; NÃO toca o banco")
    p.add_argument("--relatorio", help="caminho do relatório JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrão é dry-run)")
    p.add_argument("--raiz", default=str(RAIZ_PADRAO), help="raiz do repo (contratos)")
    return p


def main(argv=None) -> int:
    args = montar_parser().parse_args(argv)
    if not args.organizacao and not args.fonte and not args.desfazer and not args.planejar:
        print("uso: --organizacao <uuid> | --fonte <jsonl> | --planejar | --desfazer <cid>")
        return EXIT_USO
    porta = None
    if not args.planejar:
        try:
            porta = PortaPsql(args.prefixo)
        except PortaIndisponivel as exc:
            print("FALHOU %s" % exc)
            return EXIT_USO
    agente = PriorityScore(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
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
    print(json.dumps({"agente": relatorio.get("agente"), "score_type": relatorio["score_type"],
                      "score_version": relatorio["score_version"],
                      "ambiente": relatorio.get("ambiente"),
                      "correlation_id": relatorio["correlation_id"],
                      "total": relatorio.get("total"),
                      "por_veredito": relatorio.get("por_veredito"),
                      "gravados": relatorio.get("gravados"),
                      "apagados": relatorio.get("apagados"), "dry_run": relatorio.get("dry_run")},
                     ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-20s score=%-7s %s %s" % (r["veredito"], r.get("score_value", "-"),
                                            r.get("motivo") or "-",
                                            ", ".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
