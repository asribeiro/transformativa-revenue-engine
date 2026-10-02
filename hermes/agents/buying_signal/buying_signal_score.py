#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Buying Signal Score v1 — força e atualidade dos sinais de mudança/demanda (card TRE-W5-E03-T01).

O que este componente FAZ (e só isto): lê os `signals` de UMA empresa que já existe em
`sales_intelligence.organizations` (o produtor do sinal é o Signal Detector, TRE-W4-E03-T01),
calcula o score `BUYING_SIGNAL` pela fórmula V1 congelada neste componente e GRAVA o resultado em
`sales_intelligence.scores` com `score_type` + `score_version` (score sem versão não é reprodutível
e é recusado pelo Data Contract V1.0 §8).

O que ele NÃO faz, por desenho (declarado em `agente-buying-signal-v1.json` -> lacunas):
  - não ESCREVE em `signals`/`organizations`: o score é derivado, aditivo e histórico. A guarda de
    escrita recusa qualquer INSERT/UPDATE/DELETE fora de `scores` (mais `agent_runs` e
    `sync_events`, que são a auditoria e a trava de idempotência da rodada);
  - não ATUALIZA score: score é histórico, não mutável (contrato §8). Recalcular INSERE uma linha
    nova; a rodada idêntica (mesmo conjunto de sinais e mesma fórmula) não insere de novo;
  - não emite evento de outbox, não chama Odoo/Titan/n8n e não faz requisição de rede nenhuma;
  - não usa LLM: a fórmula é determinística (nenhuma frase do score é inferência de modelo);
  - não escreve em produção (ADR-005): `--ambiente dev|homolog`, `prod` é recusado com exit 4.

Fórmula V1 (congelada; `buying-signal-v1`), por sinal observado:

    pontos = peso_do_tipo(tipo) * confianca_efetiva * decaimento(idade)
    decaimento = 0.5 ** (idade_dias / meia_vida_dias(categoria))
    forca = 1 - PROD(1 - pontos)          # saturação: muitos sinais fracos não inventam 100
    score_value = 100 * forca             # 0..100, 2 decimais

Regras que sustentam o número (todas medidas pela suite):
  - **peso por tipo** é tabela declarada e cobre o vocabulário fechado `signal_type` do contrato
    exatamente uma vez: tipo fora da lista é RECUSA, não aviso;
  - **meia-vida por categoria** (a categoria é derivada pelo detector): sinal de tecnologia
    esfria mais rápido que sinal corporativo;
  - **confiança ausente** não vira fato: usa a confiança padrão declarada e o fato é registrado
    (`confianca_padrao_usada`);
  - **data futura** não decai (idade 0) e é registrada; **sem data nenhuma** (nem `event_date` nem
    `detected_at`) o sinal é DESCARTADO com motivo — não se inventa idade;
  - **limite de sinais**: no máximo os 10 sinais mais fortes contribuem; o excedente fica
    registrado (`sinais_ignorados`) — o score não é um contador de volume;
  - **sem sinal algum** o score é 0,00 com motivo `SEM_SINAIS`: ausência de sinal é informação, não
    erro (o Priority Score precisa de número).

Idempotência (contrato §6: retry não pode criar duplicata): a chave é a ENTRADA, não a rodada —
`score:BUYING_SIGNAL:<org>:<hash_das_entradas>`, gravada em `sync_events.idempotency_key` (UNIQUE).
A gravação é UMA transação com dois comandos: claim da chave + INSERT do score ancorado no claim
(replay não insere) -> fechamento do `sync_events` ANCORADO no score DESTA rodada (replay não marca
sucesso). A história de cada rodada fica em `agent_runs` com o `correlation_id` do lote.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele é a VPS):

  python3 hermes/agents/buying_signal/buying_signal_score.py --planejar --organizacao <uuid>
  python3 hermes/agents/buying_signal/buying_signal_score.py --ambiente dev --organizacao <uuid> \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \\
      --relatorio /tmp/buying-signal-rodada.json
  python3 hermes/agents/buying_signal/buying_signal_score.py --desfazer <correlation_id>
  python3 hermes/agents/buying_signal/buying_signal_score.py --desfazer <correlation_id> --confirmo
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do componente (espelha `hermes/agents/buying_signal/agente-buying-signal-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "buying_signal"
PAPEL = "score_calculation"
VERSAO = "1.0.0"
WORKFLOW = "scoring-buying-signal"
WORKFLOW_VERSAO = "v1"

SCORE_TYPE = "BUYING_SIGNAL"
SCORE_VERSION = "buying-signal-v1"
FORMULA = "1 - PROD(1 - peso_do_tipo * confianca * decaimento)"

TABELA_SCORES = "sales_intelligence.scores"
TABELA_SINAIS = "sales_intelligence.signals"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"

# O score ESCREVE apenas nas três: o resultado, a auditoria da rodada e a trava de idempotência.
TABELAS_PERMITIDAS = (TABELA_SCORES, TABELA_AGENT_RUNS, TABELA_SYNC_EVENTS)
# Leitura declarada: a empresa (existência) e os sinais (matéria-prima do score).
TABELAS_LIDAS = (TABELA_ORGANIZACOES, TABELA_SINAIS)

OPERACAO_SYNC = "SCORE"
MARCA_GRAVADO = "SCORE_GRAVADO"
ENTITY_TYPE_SCORE = "score"

# Peso por tipo de sinal: tabela FECHADA sobre o vocabulário do contrato (`signal_type`, 18 valores).
# Não é "quanto gostamos do tipo": é o quanto o tipo indica mudança/demanda comprável.
PESOS_POR_TIPO = {
    "ERP_CHANGE": 0.95,
    "AI_INITIATIVE": 0.90,
    "DIGITAL_TRANSFORMATION": 0.85,
    "M_AND_A": 0.85,
    "CRM_CHANGE": 0.80,
    "FUNDING": 0.80,
    "EFFICIENCY_PROGRAM": 0.75,
    "NEW_EXECUTIVE": 0.70,
    "COST_REDUCTION": 0.70,
    "GROWTH": 0.65,
    "TECH_ADOPTION": 0.60,
    "HIRING": 0.60,
    "REGULATORY_CHANGE": 0.55,
    "NEW_PRODUCT": 0.55,
    "NEW_LOCATION": 0.50,
    "SERVICE_VOLUME": 0.50,
    "PROCESS_COMPLEXITY": 0.45,
    "CUSTOMER_COMPLAINT": 0.40,
}

# Categoria DERIVADA do tipo pelo Signal Detector (tabela do detector, espelhada aqui para a
# meia-vida). A suite confere a cobertura contra o vocabulário do contrato.
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

# Meia-vida (dias) por categoria: quanto tempo metade da força do sinal se perde.
MEIA_VIDA_DIAS = {
    "TECNOLOGIA": 120,
    "CORPORATIVO": 180,
    "EXPANSAO": 150,
    "EFICIENCIA": 240,
    "PRESSAO_OPERACIONAL": 90,
    "REGULATORIO": 300,
}

CONFIANCA_PADRAO = 0.5
LIMITE_SINAIS = 10
VALIDADE_DIAS = 30

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

MOTIVOS_DESCARTE = (
    "TIPO_FORA_DO_VOCABULARIO",
    "CONFIANCA_FORA_DA_FAIXA",
    "DATA_AUSENTE",
    "DATA_INVALIDA",
)

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
PLANEJADO_RECUSAR = "PLANEJADO_RECUSAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_AGENTE_PADRAO = "hermes/agents/buying_signal/agente-buying-signal-v1.json"
MIGRATION_PADRAO = "db/migrations/0001_sales_intelligence_v1.sql"
# A porta de banco (ADR-0008) é a MESMA do detector: uma única implementação de "falar com o
# PostgreSQL da VPS". Cópia de porta é divergência esperando acontecer.
MODULO_PORTA = "hermes/agents/signal/signal.py"

_DATA_ISO = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d{1,6})?(Z|[+-]\d{2}:?\d{2})?)?$")


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR (o contrato DESTE agente), nunca pela profundidade do arquivo."""
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():
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
    """Constante do código divergente do contrato do componente ou do Data Contract."""


# ---------------------------------------------------------------------------------------
# Contratos e porta de banco importada do detector
# ---------------------------------------------------------------------------------------
def carregar_json(caminho) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_contrato_do_agente(raiz) -> dict:
    return carregar_json(Path(raiz) / CONTRATO_AGENTE_PADRAO)


def tipos_do_contrato_de_dados(raiz) -> tuple:
    """O vocabulário fechado vem do Data Contract V1.0, não do código."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    return tuple(contrato["vocabularies"]["signal_type"])


def carregar_porta(raiz):
    """Carrega a PORTA de banco do detector (PortaSQL/PortaAusente/PortaPsql/PortaIndisponivel).

    Import tardio de propósito: o módulo é localizado pela raiz do repo, então a cópia do
    componente sob teste em diretório raso continua importável.
    """
    caminho = Path(raiz) / MODULO_PORTA
    if not caminho.is_file():
        raise ValueError("módulo da porta de banco ausente: %s" % caminho)
    spec = importlib.util.spec_from_file_location("signal_porta", str(caminho))
    if spec is None or spec.loader is None:
        raise ValueError("não consegui carregar a porta: %s" % caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_instante(valor):
    """Data do evento -> datetime UTC. None quando não é data legível."""
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


def _arredondar(valor: float) -> float:
    return round(float(valor), 2)


# ---------------------------------------------------------------------------------------
# O cálculo (puro: nenhuma conexão, nenhum relógio escondido — o "agora" entra por parâmetro)
# ---------------------------------------------------------------------------------------
def decaimento(idade_dias: float, categoria: str) -> float:
    """Meia-vida por categoria. Idade negativa (evento futuro) não decai: é 1,0."""
    meia_vida = MEIA_VIDA_DIAS.get(categoria)
    if meia_vida is None:
        raise ContratoDivergente("categoria sem meia-vida declarada: %r" % categoria)
    if idade_dias <= 0:
        return 1.0
    return 0.5 ** (idade_dias / meia_vida)


def pontos_do_sinal(linha: dict, agora_dt: datetime) -> dict:
    """Pontos de UM sinal + os fatos que o explicam. Recusa tipo fora do vocabulário."""
    tipo = (linha.get("signal_type") or "").strip().upper()
    if tipo not in PESOS_POR_TIPO:
        raise ContratoDivergente("tipo fora do vocabulário: %r" % linha.get("signal_type"))
    categoria = CATEGORIAS_POR_TIPO[tipo]
    peso = PESOS_POR_TIPO[tipo]
    confianca_bruta = linha.get("confidence")
    padrao_usado = confianca_bruta is None
    if padrao_usado:
        confianca = CONFIANCA_PADRAO
    else:
        try:
            confianca = float(confianca_bruta)
        except (TypeError, ValueError):
            raise ValueError("CONFIANCA_FORA_DA_FAIXA")
        if not 0.0 <= confianca <= 1.0:
            raise ValueError("CONFIANCA_FORA_DA_FAIXA")
    instante = _parse_instante(linha.get("event_date")) or _parse_instante(linha.get("detected_at"))
    if instante is None:
        raise ValueError("DATA_AUSENTE")
    idade_dias = (agora_dt - instante).total_seconds() / 86400.0
    futuro = idade_dias < 0
    if futuro:
        idade_dias = 0.0
    decai = decaimento(idade_dias, categoria)
    return {
        "id": linha.get("id"),
        "tipo": tipo,
        "categoria": categoria,
        "peso_do_tipo": peso,
        "confianca": confianca,
        "confianca_padrao_usada": padrao_usado,
        "event_date": linha.get("event_date"),
        "detected_at": linha.get("detected_at"),
        "idade_dias": round(idade_dias, 3),
        "meia_vida_dias": MEIA_VIDA_DIAS[categoria],
        "data_futura": futuro,
        "decaimento": round(decai, 6),
        "pontos": round(peso * confianca * decai, 6),
    }


def calcular_score(linhas: list, agora_iso: str) -> dict:
    """Score de UMA empresa a partir dos sinais dela. Puro e determinístico."""
    agora_dt = _parse_instante(agora_iso)
    if agora_dt is None:
        raise ValueError("agora inválido: %r" % agora_iso)
    considerados, descartados = [], []
    for linha in linhas:
        try:
            considerados.append(pontos_do_sinal(linha, agora_dt))
        except ContratoDivergente:
            raise
        except ValueError as exc:
            descartados.append({"id": linha.get("id"), "motivo": str(exc)})
    considerados.sort(key=lambda c: (-c["pontos"], str(c["id"])))
    usados = considerados[:LIMITE_SINAIS]
    ignorados = [c["id"] for c in considerados[LIMITE_SINAIS:]]
    restante = 1.0
    for c in usados:
        restante *= (1.0 - c["pontos"])
    forca = 1.0 - restante
    valor = max(0.0, min(100.0, 100.0 * forca))
    if usados:
        resumo = ("%d sinal(is) somando %s pontos: força agregada %.4f (saturação), score %s; "
                  "%d descartado(s) com motivo; %d sinal(is) acima do limite de %d."
                  % (len(usados), _arredondar(sum(c["pontos"] for c in usados)), forca,
                     _arredondar(valor), len(descartados), len(ignorados), LIMITE_SINAIS))
        motivo = None
    else:
        resumo = ("Nenhum sinal utilizável: score 0,00 por AUSÊNCIA de sinal "
                  "(%d descartado(s) com motivo)." % len(descartados))
        motivo = "SEM_SINAIS"
    return {
        "score_type": SCORE_TYPE,
        "score_version": SCORE_VERSION,
        "score_value": _arredondar(valor),
        "formula": FORMULA,
        "motivo": motivo,
        "sinais_utilizados": [{k: c[k] for k in ("id", "tipo", "categoria", "pontos", "confianca",
                                                 "confianca_padrao_usada", "decaimento",
                                                 "idade_dias", "meia_vida_dias", "data_futura")}
                              for c in usados],
        "sinais_ignorados": ignorados,
        "limite_sinais": LIMITE_SINAIS,
        "confianca_padrao_usada": sum(1 for c in usados if c["confianca_padrao_usada"]),
        "descartados": descartados,
        "resumo": resumo,
        "total_de_sinais_lidos": len(linhas),
    }


def hash_das_entradas(organizacao_id: str, calculo: dict) -> str:
    """A chave é a ENTRADA: empresa + conjunto de sinais que ENTRARAM (não a rodada)."""
    material = json.dumps({
        "organizacao": organizacao_id,
        "score_type": SCORE_TYPE,
        "score_version": SCORE_VERSION,
        "formula": FORMULA,
        "limite_sinais": LIMITE_SINAIS,
        "confianca_padrao": CONFIANCA_PADRAO,
        "pesos": PESOS_POR_TIPO,
        "meia_vida": MEIA_VIDA_DIAS,
        "sinais": [{"id": c["id"], "pontos": c["pontos"], "tipo": c["tipo"]}
                   for c in calculo["sinais_utilizados"]],
        "ignorados": sorted(str(i) for i in calculo["sinais_ignorados"]),
        "descartados": sorted(({"id": str(d["id"]), "motivo": d["motivo"]}
                               for d in calculo["descartados"]),
                              key=lambda d: (d["id"], d["motivo"])),
    }, ensure_ascii=False, sort_keys=True)
    return _sha256(material)


def chave_idempotencia(organizacao_id: str, entrada_hash: str) -> str:
    return "score:%s:%s:%s" % (SCORE_TYPE, organizacao_id, entrada_hash)


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o score pode e não pode escrever)
# ---------------------------------------------------------------------------------------
_LITERAIS = None  # reservado: `lit`/`lit_json` são a única porta de dado para dentro do SQL


def _escapar(valor) -> str:
    """Literal SQL a partir de um valor Python (única porta de entrada de dado na instrução)."""
    if valor is None:
        return "NULL"
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, float)):
        return repr(valor)
    return "'%s'" % str(valor).replace("'", "''")


_ESCRITA = (
    ("insert", re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][\w\.]*)", re.I)),
    ("update", re.compile(r"\bUPDATE\s+([A-Za-z_][\w\.]*)", re.I)),
    ("delete", re.compile(r"\bDELETE\s+FROM\s+([A-Za-z_][\w\.]*)", re.I)),
)
_DDL = re.compile(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT\s+ON)\b", re.I)
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_INSERT_COLUNAS = re.compile(r"INSERT\s+INTO\s+%s\s*\(([^)]*)\)")


def _sem_literais(sql: str) -> str:
    """A instrução SEM o conteúdo dos literais: `drop` dentro de um texto não é DDL."""
    return _LITERAL.sub("''", sql)


def _escapar(valor) -> str:
    """Literal SQL a partir de um valor Python (única porta de entrada de dado na instrução)."""
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


def colunas_do_insert(sql: str, tabela: str) -> list:
    padrao = re.compile(_INSERT_COLUNAS.pattern % re.escape(tabela), re.I)
    return [[c.strip().lower() for c in bloco.split(",") if c.strip()]
            for bloco in padrao.findall(_sem_literais(sql))]


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL e escrita fora do declarado.

    Exigências de DESENHO do INSERT de `scores` (medidas por mutação na suite):
    (a) declara `organization_id`, `score_type`, `score_value` e `score_version` (NOT NULL do DDL
    e a versão exigida pelo contrato §8); (b) o UPDATE de `scores` NUNCA é permitido — score é
    histórico (recalcular insere linha nova).
    """
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL não é permitido ao Buying Signal Score")
    for colunas in colunas_do_insert(codigo, TABELA_SCORES):
        faltando = [c for c in ("id", "organization_id", "score_type", "score_value",
                                "score_version") if c not in colunas]
        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT de score sem coluna obrigatória do DDL/contrato: %s" % ", ".join(faltando))
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela in (TABELA_SINAIS, TABELA_ORGANIZACOES):
                raise GuardaDeEscritaViolada(
                    "%s em %s não é permitido: o score é DERIVADO — quem escreve sinal é o "
                    "detector e quem escreve a empresa é o Scout" % (operacao.upper(), tabela))
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


def sql_sinais_da_organizacao(organizacao_id: str) -> str:
    """A matéria-prima do score, em JSON: o texto do sinal nunca quebra a leitura da linha."""
    campos = ["'id'", "id::text", "'signal_type'", "COALESCE(signal_type, '')",
              "'signal_category'", "COALESCE(signal_category, '')",
              "'confidence'", "COALESCE(confidence::text, '')",
              "'event_date'", "COALESCE(event_date::text, '')",
              "'detected_at'", "COALESCE(detected_at::text, '')",
              "'title'", "COALESCE(title, '')"]
    return ("SELECT jsonb_build_object(%s)::text FROM %s WHERE organization_id = %s "
            "ORDER BY id;" % (", ".join(campos), TABELA_SINAIS, lit(organizacao_id)))


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
    """UMA transação: claim da chave (replay não insere) + INSERT ancorado no claim + fechamento.

    O `sync_events` é a TRAVA (idempotency_key UNIQUE) e a âncora: sem o claim desta rodada o
    INSERT não acontece, e sem o INSERT o fechamento não marca PROCESSED. A última consulta conta
    o score DESTA rodada — é a prova da gravação (sem ela o agente recusa).
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
    """A história da rodada em `agent_runs` — e a ÂNCORA do desfazer (o `scores` não tem
    `correlation_id`: o vínculo é declarado no `output` da execução, que é auditável)."""
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
    """IDs dos scores que ESTA rodada criou (via âncora em agent_runs.output)."""
    return ("SELECT jsonb_extract_path_text(output, 'score_id') FROM %s "
            "WHERE correlation_id = %s AND agent_name = %s ORDER BY id;"
            % (TABELA_AGENT_RUNS, lit(correlation_id), lit(AGENTE)))


def sql_rodada_existe(correlation_id: str) -> str:
    return ("SELECT id::text FROM %s WHERE correlation_id = %s ORDER BY id;"
            % (TABELA_AGENT_RUNS, lit(correlation_id)))


def sql_desfazer(scores: list, correlation_id: str) -> str:
    """Desfazer apaga SÓ o que a rodada criou (e só com `--confirmo`).

    O que a rodada NÃO criou fica: o desfazer não é limpeza geral, é desfazer de uma rodada.
    """
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
class BuyingSignalScore:
    """Calcula e grava o score BUYING_SIGNAL de UMA empresa por rodada."""

    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None,
                 porta_modulo=None):
        self.raiz = Path(raiz) if raiz else RAIZ_PADRAO
        self.relogio = relogio
        self.ambiente = ambiente
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.alvo = None
        self.porta_modulo = porta_modulo
        self.porta = porta if porta is not None else self._porta_padrao()

    def _porta_padrao(self):
        try:
            modulo = carregar_porta(self.raiz)
        except ValueError:
            return None
        self.porta_modulo = modulo
        return modulo.PortaAusente()

    def contrato(self) -> dict:
        return carregar_contrato_do_agente(self.raiz)

    def conferir_contrato(self) -> dict:
        """Código e contrato do componente não podem divergir (medido pela suite)."""
        contrato = self.contrato()
        divergencias = []
        if contrato.get("score_type") != SCORE_TYPE:
            divergencias.append("score_type")
        if contrato.get("score_version") != SCORE_VERSION:
            divergencias.append("score_version")
        if contrato.get("formula") != FORMULA:
            divergencias.append("formula")
        if contrato.get("pesos_por_tipo") != PESOS_POR_TIPO:
            divergencias.append("pesos_por_tipo")
        if contrato.get("meia_vida_dias") != MEIA_VIDA_DIAS:
            divergencias.append("meia_vida_dias")
        if contrato.get("limite_sinais") != LIMITE_SINAIS:
            divergencias.append("limite_sinais")
        if contrato.get("confianca_padrao") != CONFIANCA_PADRAO:
            divergencias.append("confianca_padrao")
        if contrato.get("validade_dias") != VALIDADE_DIAS:
            divergencias.append("validade_dias")
        tipos = tipos_do_contrato_de_dados(self.raiz)
        if tuple(contrato.get("vocabulario_coberto") or ()) != tuple(sorted(tipos)):
            divergencias.append("vocabulario_coberto")
        if sorted(PESOS_POR_TIPO) != sorted(tipos):
            divergencias.append("pesos_vs_data_contract")
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

    def identificar_alvo(self, organizacao_id: str) -> dict:
        self.conferir_contrato()
        linhas = self._consultar(sql_organizacao_existe(organizacao_id))
        if not linhas:
            self.alvo = None
            return {"encontrada": False, "organizacao_id": organizacao_id}
        self.alvo = {"organizacao_id": organizacao_id}
        return {"encontrada": True, "organizacao_id": organizacao_id}

    def _consultar(self, sql: str, permitir_remocao: bool = False) -> list:
        if self.porta is None:
            raise PortaIndisponivel("nenhuma porta de banco configurada")
        codigo, saida, erro = self.porta.executar(sql, permitir_remocao=permitir_remocao)
        if codigo != 0:
            raise PortaIndisponivel("psql saiu com %d: %s" % (codigo, erro.strip()[:400]))
        return [linha for linha in saida.splitlines() if linha.strip()]

    def _ler_sinais(self, organizacao_id: str) -> list:
        linhas = self._consultar(sql_sinais_da_organizacao(organizacao_id))
        sinais = []
        for linha in linhas:
            try:
                sinais.append(json.loads(linha))
            except json.JSONDecodeError:
                raise PortaIndisponivel("linha de sinal ilegível: %r" % linha[:120])
        return sinais

    def rodar(self, organizacao_id: str, planejar: bool = False) -> dict:
        contrato_agente = self.conferir_contrato()
        sinais = self._ler_sinais(organizacao_id) if not planejar else []
        calculo = calcular_score(sinais, self.relogio())
        entrada_hash = hash_das_entradas(organizacao_id, calculo)
        chave = chave_idempotencia(organizacao_id, entrada_hash)
        base = {
            "agente": AGENTE,
            "versao": VERSAO,
            "score_type": SCORE_TYPE,
            "score_version": SCORE_VERSION,
            "ambiente": self.ambiente,
            "correlation_id": self.correlation_id,
            "alvo": organizacao_id,
            "total": 1,
            "entrada_hash": entrada_hash,
            "chave_idempotencia": chave,
        }
        if planejar:
            base["por_veredito"] = {PLANEJADO_CALCULAR: 1}
            base["resultados"] = [{
                "organizacao_id": organizacao_id,
                "veredito": PLANEJADO_CALCULAR,
                "score_value": calculo["score_value"],
                "motivo": calculo["motivo"],
                "resumo": calculo["resumo"],
                "motivos": [],
            }]
            base["planejamento"] = True
            base["contrato_do_agente"] = contrato_agente["agente"]
            return base

        self.conferir_ambiente()
        alvo = self.identificar_alvo(organizacao_id)
        if not alvo["encontrada"]:
            base["por_veredito"] = {RECUSADA: 1}
            base["resultados"] = [{"organizacao_id": organizacao_id, "veredito": RECUSADA,
                                   "motivos": ["ORGANIZACAO_NAO_ENCONTRADA"],
                                   "resumo": "empresa ausente: nada é calculado"}]
            base["gravados"] = 0
            return base

        score_id = str(uuid.uuid4())
        sync_event_id = str(uuid.uuid4())
        instante = self.relogio()
        valores = {
            "organization_id": organizacao_id,
            "score_value": calculo["score_value"],
            "calculated_at": instante,
            "valid_until": (_parse_instante(instante) + timedelta(days=VALIDADE_DIAS)).isoformat(),
            "inputs": {
                "score_type": SCORE_TYPE,
                "score_version": SCORE_VERSION,
                "formula": FORMULA,
                "limite_sinais": LIMITE_SINAIS,
                "confianca_padrao": CONFIANCA_PADRAO,
                "sinais_utilizados": calculo["sinais_utilizados"],
                "sinais_ignorados": calculo["sinais_ignorados"],
                "descartados": calculo["descartados"],
                "total_de_sinais_lidos": calculo["total_de_sinais_lidos"],
                "entrada_hash": entrada_hash,
                "gerado_em": instante,
            },
            "explanation": {
                "resumo": calculo["resumo"],
                "motivo": calculo["motivo"],
                "formula": FORMULA,
                "componentes": {"soma_dos_pontos":
                                _arredondar(sum(c["pontos"] for c in calculo["sinais_utilizados"])),
                                "saturacao": True,
                                "score_value": calculo["score_value"]},
                "regras_declaradas": {
                    "confianca_padrao": CONFIANCA_PADRAO,
                    "limite_sinais": LIMITE_SINAIS,
                    "validade_dias": VALIDADE_DIAS,
                    "meia_vida_dias": MEIA_VIDA_DIAS,
                },
            },
        }
        sql_gravar = sql_gravar_score(score_id, sync_event_id, chave, valores)
        saida = self._consultar(sql_gravar)
        # A prova da gravação é o COUNT do score DESTA rodada logo após o INSERT ancorado.
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
            "motivos": ["ENTRADA_JA_CALCULADA"] if not gravado else [],
        }]
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
            "ambiente": self.ambiente,
            "correlation_id": correlation_id,
            "alvo": self.alvo,
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
        saida = self._consultar(sql_desfazer(itens, correlation_id), permitir_remocao=True)
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
# CLI
# ---------------------------------------------------------------------------------------
def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Buying Signal Score v1 (TRE-W5-E03-T01)")
    p.add_argument("--organizacao", help="UUID da empresa (organizations.id)")
    p.add_argument("--ambiente", help="dev | homolog (prod é recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true", help="calcula e planeja; NÃO toca o banco")
    p.add_argument("--relatorio", help="caminho do relatório JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrão é dry-run)")
    p.add_argument("--raiz", default=str(RAIZ_PADRAO), help="raiz do repo (contratos)")
    return p


def main(argv=None) -> int:
    args = montar_parser().parse_args(argv)
    if not args.organizacao and not args.desfazer:
        print("uso: --organizacao <uuid> [--ambiente <amb>] [--planejar] | --desfazer <cid>")
        return EXIT_USO
    porta = None
    if not args.planejar:
        modulo = carregar_porta(args.raiz)
        try:
            porta = modulo.PortaPsql(args.prefixo)
        except modulo.PortaIndisponivel as exc:
            print("FALHOU %s" % exc)
            return EXIT_USO
    agente = BuyingSignalScore(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
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
        else:
            if args.planejar:
                relatorio = agente.rodar(args.organizacao, planejar=True)
            else:
                relatorio = agente.rodar(args.organizacao)
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
        print("  %-20s score=%-7s %s" % (r["veredito"], r.get("score_value", "-"),
                                         ", ".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
