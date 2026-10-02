#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automation Fit Score v1 — probabilidade de existir oportunidade relevante de automacao/IA
(card TRE-W5-E02-T01, W5/EPIC E02; doc 03 §3, doc 04 §8, doc 07 §8, doc 12 §8).

O que este componente FAZ (e so isto): recebe o(s) identificador(es) FORTES de empresas que JA
existem no PostgreSQL (quem as produz e o Scout, TRE-W4-E01-T01; quem as enriquece e o Research,
TRE-W4-E02-T01; quem produz os sinais e o Signal Detector, TRE-W4-E03-T01; quem produz a hipotese
com lastro e o Pain Hypothesis, TRE-W4-E04-T01), LÊ o estado real dessas tabelas e CALCULA o score
`AUTOMATION_FIT` por uma formula declarada, deterministica e versionada, gravando a LINHA do score
em `sales_intelligence.scores` (score_type='AUTOMATION_FIT', score_version='automation-fit-v1').

O que ele NAO faz, por desenho (declarado em `agente-automation-fit-v1.json` -> lacunas):
  - nao CRIA nem ESCREVE em organizacao, sinal, hipotese ou pesquisa: o score e ADITIVO e le o
    que a onda W4 escreveu (doc 12 §1: empresa/sinais/pesquisa sao sources of truth do PostgreSQL,
    mas de OUTROS produtores). A guarda de escrita recusa qualquer INSERT/UPDATE/DELETE fora de
    `scores`, `agent_runs`, `sync_events` e `human_approvals`;
  - nao SOBRESCREVE score: `scores` e HISTORICO (doc 12 §8: "o score e historico, nao mutavel").
    UPDATE em `scores` e recusa; estado novo gera LINHA NOVA e o replay do mesmo estado nao
    duplica (idempotency_key em `sync_events`);
  - nao CALCULA os scores irmaos: ICP (W5-E01), BUYING_SIGNAL (W5-E03), DATA_QUALITY (W5-E04) e
    PRIORITY (W5-E05) tem card e formula proprios. Em particular NAO escreve a coluna
    `organizations.data_quality_score` nem as colunas de forca/decaimento de `signals`;
  - nao faz requisicao de rede nenhuma e nao chama LLM: a v1 e aritmetica declarada (sem crawler,
    sem modelo). Nao toca Odoo/Titan/n8n e nao escreve em producao (ADR-005).

Formula V1 (`automation-fit-v1`) — pesos declarados, somando exatamente 1,00:

    valor = 100 * SUM(peso_i * componente_i) / SUM(peso_i)   para os componentes PRESENTES

  componente `porte`                  peso 0,25  faixa de funcionarios -> capacidade/volume
  componente `pressao_operacional`    peso 0,30  sinais de pressao/eficiencia (categories do doc 03 §7)
  componente `prontidao_tecnologica`  peso 0,20  sinais de tecnologia/IA (categories do doc 03 §7)
  componente `dispersao_de_processos` peso 0,10  unidades operacionais (unit_count)
  componente `dor_quantificada`       peso 0,15  impacto de negocio da hipotese de dor (0-100)

Regras que sustentam o numero (todas cobertas por suite):
  - **componente AUSENTE nao e voto**: ele sai do numerador E do denominador. A cobertura (soma dos
    pesos presentes) e publicada em `explanation.cobertura` e em `inputs.cobertura` — assim o score
    mede a FORCA da oportunidade dada a evidencia e NAO vira um segundo Data Quality Score;
  - **sem lastro nao ha score** (fail-closed): cobertura < 0,40 -> RECUSADA motivo `SEM_LASTRO`,
    nada escrito em `scores`. Score calculado majoritariamente sobre ausencia seria ruido;
  - **nada e inventado**: o tipo de sinal que nao existe no vocabulario do contrato nao pontua; o
    porte so usa a faixa de funcionarios (derivada pela MESMA regra do Scout, importada) e o
    unit_count; a hipotese so entra pelo `business_impact_score` medido — nenhum casamento por
    prosa (a licao do roteador JEV: casar texto por sinonimo nao e mecanismo);
  - **deterministico de verdade**: mesma entrada -> mesmo valor, bit a bit. Aritmetica em Decimal
    com ROUND_HALF_UP em 2 casas (o DDL pede NUMERIC(5,2)), nunca float solto.

Historico e idempotencia (doc 06 §7: "retry nao pode criar duplicata"):
  - a chave e a ENTRADA, nao a rodada: `score:AUTOMATION_FIT:org:<uuid>:<input_hash>`, gravada em
    `sync_events.idempotency_key` (UNIQUE), com o `input_hash` calculado sobre o SNAPSHOT canonico
    que entra na formula (porte + unit_count + ids/tipos dos sinais + ids/impacto das hipoteses).
    A ingestao e UMA transacao com dois comandos: claim da chave + INSERT do score ancorado no
    claim (replay nao insere) -> fechamento do sync_event ANCORADO no score DESTA rodada.
  - o que NAO entra no hash nao versiona o score: como o sinal e a hipotese entram por id, um sinal
    novo (ou uma hipotese nova) muda o hash e produz LINHA NOVA — e' exatamente o que se quer de um
    score historico;
  - a historia de cada pedido fica em `agent_runs` (uma linha por empresa pedida, com o
    `correlation_id` do lote) — auditoria nao depende da narrativa de quem rodou.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e' a VPS):

  python3 hermes/agents/automation_fit/automation_fit.py --planejar --fonte perfis.jsonl
  python3 hermes/agents/automation_fit/automation_fit.py --ambiente dev --fonte perfis.jsonl \
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
      --relatorio /tmp/automation-fit-rodada.json
  python3 hermes/agents/automation_fit/automation_fit.py --desfazer <correlation_id>
  python3 hermes/agents/automation_fit/automation_fit.py --desfazer <correlation_id> --confirmo
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
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do componente (espelha `hermes/agents/automation_fit/agente-automation-fit-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "automation_fit"
PAPEL = "automation_scoring"
VERSAO = "1.0.0"
WORKFLOW = "calculo-de-automation-fit"
WORKFLOW_VERSAO = "v1"

# `score_type` e o valor do contrato (docs/data/data_contract_v1.json#scores.types); `score_version`
# e a versao da FORMULA deste componente (doc 12 §8: score sem versao nao e reprodutivel e e
# recusado). Mudar peso/faixa/vocabulario exige versao nova — nunca edicao silenciosa.
SCORE_TYPE = "AUTOMATION_FIT"
SCORE_VERSION = "automation-fit-v1"

TABELA_SCORES = "sales_intelligence.scores"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_SINAIS = "sales_intelligence.signals"
TABELA_HIPOTESES = "sales_intelligence.pain_hypotheses"
TABELA_PESQUISAS = "sales_intelligence.research_runs"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"
TABELA_APPROVALS = "sales_intelligence.human_approvals"

# Tabelas de ESCRITA declaradas. Tudo fora desta lista e recusa (fail-closed), e as tabelas de
# NEGOCIO da onda W4 aparecem em `TABELAS_DE_OUTROS` so para a mensagem de recusa ser explicita.
TABELAS_PERMITIDAS = (TABELA_SCORES, TABELA_AGENT_RUNS, TABELA_SYNC_EVENTS, TABELA_APPROVALS)
TABELAS_DE_OUTROS = (TABELA_ORGANIZACOES, TABELA_SINAIS, TABELA_HIPOTESES, TABELA_PESQUISAS)
TABELAS_LIDAS = (TABELA_ORGANIZACOES, TABELA_SINAIS, TABELA_HIPOTESES)

ACTION_TYPE_REVISAO = "AUTOMATION_FIT_IDENTITY_REVIEW"
OPERACAO_SYNC = "AUTOMATION_FIT"
MARCA_CALCULADO = "AUTOMATION_FIT_CALCULADO"
OPERACAO_REVISAO = "AUTOMATION_FIT_REVIEW"
ENTITY_TYPE_REVISAO = "automation_fit_review"
MARCA_REVISAO = "AUTOMATION_FIT_REVISAO_REGISTRADA"

# ---------------------------------------------------------------------------------------
# Formula V1: pesos, faixas e tabelas de pontos. Tudo DECLARADO aqui e conferido contra o contrato
# do agente e contra o Data Contract pelo contrato (__init__) e pela suite (offline).
# ---------------------------------------------------------------------------------------
PESOS = {
    "porte": Decimal("0.25"),
    "pressao_operacional": Decimal("0.30"),
    "prontidao_tecnologica": Decimal("0.20"),
    "dispersao_de_processos": Decimal("0.10"),
    "dor_quantificada": Decimal("0.15"),
}
SOMA_DOS_PESOS = Decimal("1.00")
COBERTURA_MINIMA = Decimal("0.40")


def conferir_pesos() -> None:
    """Guarda de desenho: pesos que nao somam 1,00 nao sao formula, sao bug.

    Chamada no `__init__` (o componente se recusa a nascer torto) E pela suite: um peso editado a
    mao sem versao nova derruba o proprio componente, aqui, e nao no numero do cliente.
    """
    soma = sum(PESOS.values(), Decimal("0"))
    if soma != SOMA_DOS_PESOS:
        raise ValueError("pesos dos componentes somam %s, e nao %s" % (soma, SOMA_DOS_PESOS))

# Porte: fator por FAIXA de funcionarios (vocabulario fechado `employee_band` do contrato, doc 03 §5).
# O sweet spot do contrato e' 150-700; faixas fora dele existem e pontuam menos — nao sao erro.
# `UNKNOWN` e' AUSENTE (nao e' fator 0: porte desconhecido nao vota).
FATOR_POR_FAIXA = {
    "LT_70": Decimal("0.25"),
    "70_149": Decimal("0.60"),
    "150_299": Decimal("1.00"),
    "300_499": Decimal("1.00"),
    "500_699": Decimal("1.00"),
    "700_1000": Decimal("0.80"),
    "GT_1000": Decimal("0.50"),
    "UNKNOWN": None,
}

# Pressao operacional e eficiencia: pontos por TIPO de sinal (vocabulario fechado do doc 03 §7).
# A presenca do componente e' ter >=1 sinal DESTA tabela; a soma e' limitada a 1,00.
PONTOS_PRESSAO = {
    "PROCESS_COMPLEXITY": Decimal("0.40"),
    "EFFICIENCY_PROGRAM": Decimal("0.35"),
    "SERVICE_VOLUME": Decimal("0.30"),
    "COST_REDUCTION": Decimal("0.30"),
    "CUSTOMER_COMPLAINT": Decimal("0.20"),
    "REGULATORY_CHANGE": Decimal("0.15"),
}

# Prontidao tecnologica: o sinal de que a empresa JA mexe com tecnologia/IA encurta o caminho da
# automacao (a automacao se apoia no que ja existe; ausencia disso nao e veto, e' ausencia de sinal).
PONTOS_TECNOLOGIA = {
    "DIGITAL_TRANSFORMATION": Decimal("0.40"),
    "AI_INITIATIVE": Decimal("0.40"),
    "ERP_CHANGE": Decimal("0.30"),
    "TECH_ADOPTION": Decimal("0.25"),
    "CRM_CHANGE": Decimal("0.25"),
}

# Dispersao de processos: quantas unidades operacionais a empresa mantem (processo replicado em N
# lugares e' candidato classico de automacao). Faixas declaradas; ausente quando unit_count e NULL/0.
FAIXAS_DE_UNIDADES = ((1, Decimal("0.30")), (3, Decimal("0.60")), (10, Decimal("0.85")))

FATOR_UNIDADES_ACIMA = Decimal("1.00")

# Tipos de sinal do vocabulario do contrato (conferidos no __init__ contra o proprio contrato do
# agente): nenhum tipo fora daqui pontua; tipo fora do vocabulario nao entra no snapshot.
TIPOS_DE_SINAL = (
    "GROWTH", "HIRING", "NEW_EXECUTIVE", "NEW_LOCATION", "ERP_CHANGE", "CRM_CHANGE",
    "DIGITAL_TRANSFORMATION", "AI_INITIATIVE", "M_AND_A", "NEW_PRODUCT", "TECH_ADOPTION",
    "FUNDING", "PROCESS_COMPLEXITY", "CUSTOMER_COMPLAINT", "SERVICE_VOLUME", "REGULATORY_CHANGE",
    "EFFICIENCY_PROGRAM", "COST_REDUCTION",
)

# Colunas de `scores` que o componente ESCREVE (ordem do INSERT). Fora desta lista a guarda recusa:
# coluna nao escrita aqui e' coluna que o dono da tabela nao autorizou.
COLUNAS_DO_SCORE = (
    "id",
    "organization_id",
    "score_type",
    "score_value",
    "score_version",
    "inputs",
    "explanation",
    "calculated_at",
)

# Colunas NOT NULL do DDL: INSERT sem qualquer uma delas e' recusa (score sem empresa, sem tipo,
# sem valor ou sem versao nao e' score).
COLUNAS_OBRIGATORIAS_DO_SCORE = ("id", "organization_id", "score_type", "score_value", "score_version")

# Campos aceitos no pedido de score. Qualquer outro campo e' descartado com motivo.
CAMPOS_DO_PEDIDO = ("organizacao",)

CALCULADO = "CALCULADO"
JA_CALCULADO = "JA_CALCULADO"
REVISAO = "REVISAO_IDENTIDADE"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (CALCULADO, JA_CALCULADO, REVISAO, RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    CALCULADO: "COMPLETED",
    JA_CALCULADO: "COMPLETED",
    REVISAO: "REVIEW_REQUIRED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

# Vereditos do modo `--planejar` (nenhuma conexao de banco e feita nele)
PLANEJADO_CALCULAR = "PLANEJADO_CALCULAR"
PLANEJADO_REVISAR = "PLANEJADO_REVISAR"
PLANEJADO_RECUSAR = "PLANEJADO_RECUSAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4
EXIT_FONTE = 5

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_AGENTE_PADRAO = "hermes/agents/automation_fit/agente-automation-fit-v1.json"
# A identidade de empresa e' UMA regra so neste projeto: o score IMPORTA a regra do produtor da
# empresa (Scout) em vez de manter uma segunda copia dela. `lit`/`lit_json` (escape de literal) e a
# faixa de funcionarios tambem vem de la — copia de regra e' divergencia esperando acontecer.
MODULO_IDENTIDADE = "hermes/agents/scout/scout.py"


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR — nunca pela profundidade do arquivo.

    A copia mutada (autoteste) e o teste de importacao vivem em diretorio raso (as vezes `/tmp`),
    onde `Path(__file__).resolve().parents[3]` estoura `IndexError` ja na importacao. A raiz e' o
    primeiro ancestral que contem o contrato DESTE componente; o diretorio de trabalho entra como
    segunda tentativa, porque a copia sob teste nao esta na arvore do repo.
    """
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():
                return pasta
    return Path.cwd()


RAIZ_PADRAO = descobrir_raiz_padrao()


class RecusaDeAmbiente(Exception):
    """Ambiente nao permitido (ADR-005): nada e' escrito."""


class GuardaDeEscritaViolada(Exception):
    """SQL tentando escrever fora das tabelas/colunas/operacoes declaradas."""


class PortaIndisponivel(Exception):
    """A porta de banco falhou."""


# ---------------------------------------------------------------------------------------
# Contratos carregados do repo
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

    Import tardio de proposito: o modulo e' localizado pela raiz do repo, entao a copia do
    componente sob teste em diretorio raso continua importavel.
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
# Normalizacao e leitura do pedido
# ---------------------------------------------------------------------------------------
def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def arredondar(valor: Decimal) -> Decimal:
    """2 casas com ROUND_HALF_UP — o DDL pede NUMERIC(5,2) e float solto nao e' reprodutivel."""
    return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal(valor):
    """Converte o que veio do banco (int/str/Decimal) em Decimal, sem passar por float."""
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except Exception:  # noqa: BLE001 - valor nao numerico e' AUSENTE, nao erro fatal
        return None


def identificadores_validos(pedido: dict, fortes: tuple, identidade) -> list:
    """[(tipo, valor_normalizado)] na ordem de prioridade do contrato; invalido e' descartado."""
    organizacao = pedido.get("organizacao") or {}
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


def identificadores_declarados(pedido: dict, fortes: tuple) -> list:
    organizacao = pedido.get("organizacao") or {}
    if not isinstance(organizacao, dict):
        return []
    return [t for t in fortes if _texto(organizacao.get(t))]


def campos_nao_declarados(pedido: dict) -> list:
    return sorted(chave for chave in pedido if chave not in CAMPOS_DO_PEDIDO)


# ---------------------------------------------------------------------------------------
# A FORMULA (funcao pura: sem banco, sem rede, sem relogio — e' o alvo da suite offline)
# ---------------------------------------------------------------------------------------
def fator_de_porte(faixa, quantidade) -> tuple:
    """(fator, detalhe) do componente `porte`. Faixa desconhecida e' AUSENTE, nao fator 0.

    Ordem declarada: a FAIXA gravada manda quando ela esta no vocabulario do contrato e tem fator
    proprio; `UNKNOWN` (ou faixa fora do vocabulario) NAO vira fator 0 — ela tenta a derivacao a
    partir do `employee_count` MEDIDO, e sem medicao o componente e' ausente. Nunca inventado.
    """
    faixa_texto = _texto(faixa).upper() or None
    if faixa_texto in FATOR_POR_FAIXA and FATOR_POR_FAIXA[faixa_texto] is not None:
        return FATOR_POR_FAIXA[faixa_texto], {"faixa": faixa_texto, "origem": "employee_band"}
    quantidade_int = _decimal(quantidade)
    if quantidade_int is None:
        return None, {"faixa": faixa_texto, "origem": None}
    medida = int(quantidade_int)
    derivada = faixa_de_funcionarios(medida)
    fator = FATOR_POR_FAIXA.get(derivada)
    if fator is None:
        return None, {"faixa": derivada, "origem": "derivada_de_employee_count",
                      "employee_count": medida}
    return fator, {"faixa": derivada, "origem": "derivada_de_employee_count",
                   "employee_count": medida}


def _identidade_para_regras():
    """Identidade do Scout para as regras IMPORTADAS (faixa de funcionarios, escape de literal).

    `preparar_literais()` a registra quando o componente nasce; se a funcao pura for chamada sozinha
    (suite, REPL, import de terceiro), a regra e' carregada do repo por marcador — nunca copiada
    para ca: uma segunda implementacao da faixa de funcionarios divergiria da do Scout em silencio.
    """
    global _IDENTIDADE
    if _IDENTIDADE is None:
        _IDENTIDADE = carregar_identidade(RAIZ_PADRAO)
    return _IDENTIDADE


def faixa_de_funcionarios(quantidade):
    """Faixa de funcionarios pela regra do Scout (importada) — uma regra so' no projeto."""
    return _identidade_para_regras()._faixa_de_empregados(quantidade)


def pontos_do_grupo(tipos: list, tabela: dict) -> tuple:
    """(soma limitada a 1,00, tipos que pontuaram) — tipo fora do vocabulario nao pontua."""
    usados = [t for t in tipos if t in tabela]
    soma = Decimal("0")
    for tipo in usados:
        soma += tabela[tipo]
    return min(soma, Decimal("1.00")), sorted(set(usados))


def fator_de_unidades(quantidade) -> tuple:
    """(fator, detalhe) do componente `dispersao_de_processos`. NULL/0 e' AUSENTE."""
    numero = _decimal(quantidade)
    if numero is None or numero <= 0:
        return None, {"unit_count": None if numero is None else int(numero)}
    inteiro = int(numero)
    for limite, fator in FAIXAS_DE_UNIDADES:
        if inteiro <= limite:
            return fator, {"unit_count": inteiro}
    return FATOR_UNIDADES_ACIMA, {"unit_count": inteiro}


def calcular(perfil: dict, sinais: list, hipoteses: list) -> dict:
    """Calcula o AUTOMATION_FIT v1 a partir do estado medido. FUNCAO PURA (o coracao do score).

    A conta e o `input_hash` usam OS MESMOS filtros (`sinais_que_pontuam`/`hipoteses_que_pontuam`):
    se a conta e o hash divergissem, o mesmo numero viraria duas linhas (ou dois numeros, uma
    linha) e a idempotencia mentiria.

    Devolve:
      veredito   CALCULADO ou RECUSADA (com `motivos`)
      valor      Decimal com 2 casas, ou None quando recusado
      componentes dict {nome: {presente, valor, peso, detalhe}}
      cobertura  soma dos pesos dos componentes presentes
      input_hash sha256 do snapshot canonico que ENTRA na formula
    """
    componentes = {}

    fator, detalhe = fator_de_porte(perfil.get("employee_band"), perfil.get("employee_count"))
    componentes["porte"] = {"presente": fator is not None,
                            "valor": fator, "peso": PESOS["porte"], "detalhe": detalhe}

    validos = sinais_que_pontuam(sinais)
    tipos = [s["signal_type"] for s in validos]
    soma_pressao, usados_pressao = pontos_do_grupo(tipos, PONTOS_PRESSAO)
    componentes["pressao_operacional"] = {
        "presente": bool(usados_pressao), "valor": soma_pressao if usados_pressao else None,
        "peso": PESOS["pressao_operacional"],
        "detalhe": {"tipos": usados_pressao, "soma_bruta": str(soma_pressao)}}

    soma_tec, usados_tec = pontos_do_grupo(tipos, PONTOS_TECNOLOGIA)
    componentes["prontidao_tecnologica"] = {
        "presente": bool(usados_tec), "valor": soma_tec if usados_tec else None,
        "peso": PESOS["prontidao_tecnologica"],
        "detalhe": {"tipos": usados_tec, "soma_bruta": str(soma_tec)}}

    fator_un, detalhe_un = fator_de_unidades(perfil.get("unit_count"))
    componentes["dispersao_de_processos"] = {
        "presente": fator_un is not None, "valor": fator_un,
        "peso": PESOS["dispersao_de_processos"], "detalhe": detalhe_un}

    com_impacto = hipoteses_que_pontuam(hipoteses)
    impactos = [Decimal(h["impacto"]) for h in com_impacto]
    dor = (max(impactos) / Decimal("100")) if impactos else None
    componentes["dor_quantificada"] = {
        "presente": dor is not None, "valor": dor, "peso": PESOS["dor_quantificada"],
        "detalhe": {"hipoteses_com_impacto": len(impactos),
                    "impacto_maximo": str(max(impactos)) if impactos else None}}

    presentes = [nome for nome in PESOS if componentes[nome]["presente"]]
    cobertura = sum((PESOS[n] for n in presentes), Decimal("0"))
    saida = {
        "score_type": SCORE_TYPE,
        "score_version": SCORE_VERSION,
        "componentes": componentes,
        "presentes": sorted(presentes),
        "ausentes": sorted(n for n in PESOS if n not in presentes),
        "cobertura": cobertura,
        "cobertura_minima": COBERTURA_MINIMA,
        "pesos": {nome: str(peso) for nome, peso in PESOS.items()},
        "formula": "100 * SUM(peso_i * componente_i) / SUM(peso_i), i em componentes PRESENTES",
        "valor": None,
        "veredito": RECUSADA,
        "motivos": [],
        "input_hash": hash_da_entrada(perfil, sinais, hipoteses),
    }
    if cobertura < COBERTURA_MINIMA:
        saida["motivos"] = ["SEM_LASTRO"]
        return saida

    numerador = sum((PESOS[n] * componentes[n]["valor"] for n in presentes), Decimal("0"))
    saida["valor"] = arredondar(Decimal("100") * numerador / cobertura)
    saida["veredito"] = CALCULADO
    return saida


def sinais_que_pontuam(sinais: list) -> list:
    """Sinais cujo tipo existe no vocabulario do contrato — so' esses entram na conta E no hash."""
    validos = []
    for sinal in sinais:
        if not isinstance(sinal, dict):
            continue
        tipo = _texto(sinal.get("signal_type")).upper()
        if tipo not in TIPOS_DE_SINAL:
            continue
        validos.append({"id": _texto(sinal.get("id")), "signal_type": tipo})
    return sorted(validos, key=lambda s: (s["id"], s["signal_type"]))


def hipoteses_que_pontuam(hipoteses: list) -> list:
    """Hipoteses com impacto de negocio MEDIDO (0-100) — as outras nao votam nem entram no hash."""
    validas = []
    for hipotese in hipoteses:
        if not isinstance(hipotese, dict):
            continue
        impacto = _decimal(hipotese.get("business_impact_score"))
        if impacto is None:
            continue
        validas.append({"id": _texto(hipotese.get("id")),
                        "impacto": str(min(max(impacto, Decimal("0")), Decimal("100")))})
    return sorted(validas, key=lambda h: h["id"])


def snapshot_canonico(perfil: dict, sinais: list, hipoteses: list) -> dict:
    """O que ENTRA na formula, em forma canonica — e' o que o `input_hash` cobre.

    So' o que muda o NUMERO entra: porte (faixa/contagem medida), unit_count, os sinais de tipo
    valido e o impacto medido das hipoteses. Razao social/cidade/industria NAO entram: mudar o nome
    nao muda o score e nao deve criar linha nova; um sinal novo (id novo) muda o hash e cria.
    """
    contagem = _decimal(perfil.get("employee_count"))
    unidades = _decimal(perfil.get("unit_count"))
    return {
        "employee_band": _texto(perfil.get("employee_band")).upper() or None,
        "employee_count": int(contagem) if contagem is not None else None,
        "unit_count": int(unidades) if unidades is not None else None,
        "sinais": sinais_que_pontuam(sinais),
        "hipoteses": hipoteses_que_pontuam(hipoteses),
    }


def hash_da_entrada(perfil: dict, sinais: list, hipoteses: list) -> str:
    """SHA-256 do snapshot canonico: o ESTADO que entra na formula, nao a rodada."""
    canonico = snapshot_canonico(perfil, sinais, hipoteses)
    return _sha256(json.dumps(canonico, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def chave_idempotencia(organizacao_id: str, entrada_hash: str) -> str:
    return "score:%s:org:%s:%s" % (SCORE_TYPE, organizacao_id, entrada_hash)


def chave_da_revisao(organizacoes_ids: list, entrada_hash: str) -> str:
    return "score:%s:revisao:%s:%s" % (SCORE_TYPE, ",".join(sorted(organizacoes_ids)), entrada_hash)


def decidir_veredito(problemas: list, validos: list, organizacoes_casadas: list) -> tuple:
    """(veredito, motivo) — a mesma regra dos produtores da W4 (dedup.rule do contrato)."""
    if problemas:
        return RECUSADA, problemas[0]
    if not validos:
        return RECUSADA, "SEM_IDENTIFICADOR_FORTE"
    if not organizacoes_casadas:
        return RECUSADA, "ORGANIZACAO_NAO_ENCONTRADA"
    if len(organizacoes_casadas) > 1:
        return REVISAO, "IDENTIDADE_AMBIGUA"
    return CALCULADO, None


def validar_pedido(pedido: dict, fortes: tuple, identidade=None) -> dict:
    """Valida o pedido de score: identidade forte valida + nenhum campo nao declarado."""
    problemas = []
    descartados = []
    for campo in campos_nao_declarados(pedido):
        descartados.append({"campo": campo, "motivo": "CAMPO_NAO_DECLARADO"})
    validos = identificadores_validos(pedido, fortes, identidade)
    declarados = identificadores_declarados(pedido, fortes)
    if declarados and not validos:
        problemas.append("IDENTIFICADOR_FORTE_INVALIDO")
    return {"validos": validos, "declarados": declarados, "problemas": problemas,
            "descartados": descartados}


# ---------------------------------------------------------------------------------------
# Guarda de escrita (fail-closed)
# ---------------------------------------------------------------------------------------
_IDENTIDADE = None
_LITERAIS = None


def preparar_literais(identidade) -> None:
    """`lit`/`lit_json` sao importados do produtor: uma unica implementacao de escape de literal."""
    global _LITERAIS, _IDENTIDADE
    _LITERAIS = (identidade.lit, identidade.lit_json)
    _IDENTIDADE = identidade


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

    Motivo de horas de sono (licao do Signal Detector): um dado com a palavra "drop" ('queda de
    receita') fazia a guarda recusar SQL legitimo como se fosse DDL. Quem escreve dado e' o
    `lit()`, que escapa apostrofo dobrando — entao a varredura olha o codigo, nao a prosa.
    """
    return _LITERAL.sub("''", sql)


def colunas_do_insert(sql: str, tabela: str) -> list:
    """Listas de colunas de cada `INSERT INTO <tabela> (...)` do SQL (para a guarda do score)."""
    padrao = re.compile(_INSERT_COLUNAS.pattern % re.escape(tabela), re.I)
    return [[c.strip().lower() for c in bloco.split(",") if c.strip()]
            for bloco in padrao.findall(sql)]


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL, escrita fora do declarado e MUTACAO de score.

    Tres exigencias de DESENHO medidas por mutacao na suite:
    (a) o INSERT de `scores` declara as colunas NOT NULL do DDL e SOMENTE colunas declaradas;
    (b) `UPDATE` em `scores` e' recusa dura — score e' HISTORICO, nao mutavel (doc 12 §8);
    (c) nenhuma escrita nas tabelas de negocio do W4 (organizations/signals/pain_hypotheses,
        inclusive a coluna `organizations.data_quality_score`, que e' do card W5-E04).
    """
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL nao e' permitido ao Automation Fit Score")
    for colunas in colunas_do_insert(codigo, TABELA_SCORES):
        faltando = [c for c in COLUNAS_OBRIGATORIAS_DO_SCORE if c not in colunas]
        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT de score sem coluna obrigatoria do DDL: %s" % ", ".join(faltando))
        fora = [c for c in colunas if c not in COLUNAS_DO_SCORE]
        if fora:
            raise GuardaDeEscritaViolada(
                "INSERT de score com coluna nao declarada pelo componente: %s" % ", ".join(fora))
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if operacao == "update" and tabela == TABELA_SCORES:
                raise GuardaDeEscritaViolada(
                    "UPDATE em scores nao e' permitido: o score e' HISTORICO (estado novo gera "
                    "linha nova; correcao e' rollback explicito do --desfazer)")
            if tabela in TABELAS_DE_OUTROS:
                raise GuardaDeEscritaViolada(
                    "%s em %s nao e' permitido: o score LE o estado da onda W4 e nao escreve nele "
                    "(quem produz empresa/pesquisa/sinal/hipotese sao os agentes W4)"
                    % (operacao.upper(), tabela))
            if tabela not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela nao declarada: %s" % tabela)
            if operacao == "delete" and not permitir_remocao:
                raise GuardaDeEscritaViolada("DELETE fora do desfazer explicito: %s" % tabela)
            if operacao == "delete" and tabela != TABELA_SCORES and tabela != TABELA_SYNC_EVENTS:
                raise GuardaDeEscritaViolada("DELETE fora do escopo do desfazer: %s" % tabela)


# ---------------------------------------------------------------------------------------
# Porta de banco (ADR-0008: o SQL roda na VPS; a porta e' o prefixo psql)
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
        # `-q` evita que os carimbos de comando (BEGIN/COMMIT) poluam a saida; o resultado e'
        # conferido LINHA a linha, porque a porta nao promete formato.
        comando = self.prefixo + ["-v", "ON_ERROR_STOP=1", "-q", "-tA", "-F", "|"]
        try:
            p = subprocess.run(comando, input=sql, capture_output=True, text=True,
                               timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PortaIndisponivel("porta psql falhou: %s" % exc)
        return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------------------
# SQL do componente
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


def sql_perfil(organizacao_id: str) -> str:
    """O PERFIL que entra na formula: porte e unidades. Nenhuma coluna de score e' lida daqui."""
    return (
        "SELECT jsonb_build_object(\n"
        "  'id', id::text,\n"
        "  'employee_band', employee_band,\n"
        "  'employee_count', employee_count,\n"
        "  'unit_count', unit_count,\n"
        "  'status', status\n"
        ")::text FROM %s WHERE id = %s;" % (TABELA_ORGANIZACOES, lit(organizacao_id))
    )


def sql_sinais(organizacao_id: str) -> str:
    """Os sinais DA EMPRESA (so' os tipos do vocabulario entram na conta; o resto nao pontua)."""
    return (
        "SELECT jsonb_build_object('id', id::text, 'signal_type', signal_type)::text\n"
        "FROM %s WHERE organization_id = %s ORDER BY detected_at, id;"
        % (TABELA_SINAIS, lit(organizacao_id))
    )


def sql_hipoteses(organizacao_id: str) -> str:
    """As hipoteses DA EMPRESA com impacto de negocio medido (o resto nao vota)."""
    return (
        "SELECT jsonb_build_object('id', id::text, 'business_impact_score', business_impact_score,"
        " 'confidence', confidence)::text\n"
        "FROM %s WHERE organization_id = %s ORDER BY id;"
        % (TABELA_HIPOTESES, lit(organizacao_id))
    )


def montar_linha_score(score_id: str, valores: dict) -> tuple:
    """Colunas x valores do INSERT de `scores` — a paridade e' conferida contra o DDL pela suite."""
    colunas = list(COLUNAS_DO_SCORE)
    expressoes = [
        lit(score_id),                     # id
        lit(valores["organization_id"]),    # organization_id
        lit(SCORE_TYPE),                    # score_type
        str(arredondar(valores["score_value"])),  # score_value (NUMERIC(5,2))
        lit(SCORE_VERSION),                 # score_version
        lit_json(valores["inputs"]),        # inputs (snapshot medido)
        lit_json(valores["explanation"]),   # explanation (componentes, cobertura, formula)
        lit(valores["calculated_at"]),      # calculated_at
    ]
    if len(expressoes) != len(colunas):
        raise ValueError("montagem do INSERT de scores divergente: %d colunas x %d valores"
                         % (len(colunas), len(expressoes)))
    return colunas, expressoes


def sql_ingerir(score_id: str, sync_event_id: str, chave: str,
                valores: dict, payload: dict) -> str:
    """Uma transacao, DOIS comandos de escrita: claim+INSERT do score -> fechamento ancorado.

    Por que dois comandos e nao um so (defeito medido no aceite E2E do Scout): as CTEs de escrita e
    a instrucao principal rodam no MESMO snapshot, entao a instrucao principal NAO enxerga a linha
    que a CTE acabou de inserir. Aqui:
      1. `WITH claim AS (INSERT sync_events ... ON CONFLICT DO NOTHING RETURNING id)` +
         `INSERT scores SELECT ... FROM claim ON CONFLICT (id) DO NOTHING RETURNING id` — replay do
         MESMO estado nao insere score nenhum (o claim volta vazio e o SELECT nao produz linha);
      2. `UPDATE sync_events SET status='SUCCESS' ... AND EXISTS (SELECT 1 FROM scores WHERE
         id = <score desta rodada>) RETURNING 'AUTOMATION_FIT_CALCULADO'` — o fechamento so' marca
         sucesso se a linha do score DESTA rodada existe; ausente = replay.
    """
    colunas, expressoes = montar_linha_score(score_id, valores)
    comandos = [
        "BEGIN;",
        "WITH claim AS (\n"
        "  INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, created_at)\n"
        "  VALUES ({sevid}, 'score', {sid}, 'automation_fit', 'postgresql', {operacao}, "
        "{versao}, {chave}, 'PENDING', {payload}, now())\n"
        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {scores} ({cols})\n"
        "SELECT {vals} FROM claim\n"
        "ON CONFLICT (id) DO NOTHING\n"
        "RETURNING id;".format(
            sync=TABELA_SYNC_EVENTS, scores=TABELA_SCORES, sevid=lit(sync_event_id),
            sid=lit(score_id), operacao=lit(OPERACAO_SYNC), versao=lit(VERSAO),
            chave=lit(chave), payload=lit_json(payload), cols=", ".join(colunas),
            vals=", ".join(expressoes)),
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(),\n"
        "  response_payload = jsonb_build_object('score_id', {sid}, 'veredito', "
        "'CALCULADO', 'idempotency_key', {chave})\n"
        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {scores} WHERE id = {sid})\n"
        "RETURNING {marca};".format(sync=TABELA_SYNC_EVENTS, scores=TABELA_SCORES,
                                    sid=lit(score_id), chave=lit(chave),
                                    marca=lit(MARCA_CALCULADO)),
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


def sql_pedir_revisao(approval_id: str, sync_event_id: str, chave: str, pedido: dict, motivo: str,
                      casadas: list, correlation_id: str) -> str:
    """Requisicao da fila humana — mesmo desenho idempotente do score (claim + fechamento).

    A chave leva o sufixo de revisao: a MESMA ambiguidade reapresentada nao abre pedido duplicado
    para o humano (o claim ja existe), e o fechamento so' marca sucesso quando a linha da fila
    existe de verdade.
    """
    proposta = {
        "motivo": motivo,
        "componente": "%s/%s" % (AGENTE, VERSAO),
        "score_type": SCORE_TYPE,
        "correlation_id": correlation_id,
        "pedido": pedido,
        "organizacoes_casadas": casadas,
        "regra": "ambiguidade nao e resolvida por heuristica: e' reportada (dedup.rule do contrato)",
    }
    payload = {"origem": AGENTE, "componente": "%s/%s" % (AGENTE, VERSAO), "motivo": motivo,
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
             quem=lit("%s/%s" % (AGENTE, VERSAO)),
             tipo_revisao=lit(ENTITY_TYPE_REVISAO), operacao=lit(OPERACAO_REVISAO),
             origem=lit("hermes"), versao=lit(VERSAO), chave=lit(chave),
             payload=lit_json(payload), proposta=lit_json(proposta), resposta=lit_json(resposta),
             marca=lit(MARCA_REVISAO))


def sql_selecionar_da_rodada(correlation_id: str) -> str:
    """O que a rodada criou: as linhas de `scores` (veredito CALCULADO) e os sync_events delas."""
    return (
        "SELECT jsonb_build_object(\n"
        "  'score_id', s.id::text,\n"
        "  'organization_id', s.organization_id::text,\n"
        "  'sync_event_id', e.id::text\n"
        ")::text\n"
        "FROM {scores} s\n"
        "JOIN {sync} e ON e.entity_id = s.id AND e.operation = {operacao}\n"
        "JOIN {audit} a ON (a.output ->> 'score_id') = s.id::text\n"
        "WHERE a.correlation_id = {corr} AND a.agent_name = {agente}\n"
        "  AND a.output ->> 'veredito' = {veredito}\n"
        "ORDER BY s.id;\n"
    ).format(scores=TABELA_SCORES, sync=TABELA_SYNC_EVENTS, audit=TABELA_AGENT_RUNS,
             operacao=lit(OPERACAO_SYNC), corr=lit(correlation_id), agente=lit(AGENTE),
             veredito=lit(CALCULADO))


def sql_desfazer(itens: list, correlation_id: str, sync_event_id: str) -> str:
    """Apaga o que a rodada criou (linhas de score + sync_events delas). Auditoria preservada.

    Nao ha valor ANTERIOR a restaurar: o score e' uma LINHA NOVA (historico). O que prova a posse da
    linha pela rodada e' o `sync_events` da chave — e' por ele que o DELETE chega no score certo.
    """
    ids_scores = ", ".join(lit(i["score_id"]) for i in itens if i.get("score_id"))
    ids_sync = ", ".join(lit(i["sync_event_id"]) for i in itens if i.get("sync_event_id"))
    comandos = ["BEGIN;"]
    if ids_scores:
        comandos.append("DELETE FROM {scores} WHERE id IN ({ids});".format(
            scores=TABELA_SCORES, ids=ids_scores))
    if ids_sync:
        comandos.append("DELETE FROM {sync} WHERE id IN ({ids});".format(
            sync=TABELA_SYNC_EVENTS, ids=ids_sync))
    payload = {"motivo": "desfazer da rodada de calculo de automation fit",
               "correlation_id": correlation_id,
               "scores": [i["score_id"] for i in itens]}
    comandos.append(
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, operation, "
        "source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'score', NULL, 'automation_fit', 'postgresql', 'ROLLBACK', {versao}, "
        "{chave}, 'SUCCESS', {payload}, now());".format(
            sync=TABELA_SYNC_EVENTS, sevid=lit(sync_event_id), versao=lit(VERSAO),
            chave=lit("score:%s:rollback:%s" % (SCORE_TYPE, correlation_id)),
            payload=lit_json(payload)))
    comandos.append("COMMIT;")
    return "\n".join(comandos) + "\n"


# ---------------------------------------------------------------------------------------
# O componente
# ---------------------------------------------------------------------------------------
class AutomationFitScore:
    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None,
                 identidade=None):
        self.raiz = Path(raiz or RAIZ_PADRAO)
        self.contrato = carregar_contrato_do_agente(self.raiz)
        self.fortes = fortes_do_contrato_de_dados(self.raiz)
        if list(self.fortes) != list(self.contrato["identidade"]["fortes_por_prioridade"]):
            raise ValueError("divergencia entre o contrato do componente e o Data Contract V1.0: "
                             "%s x %s" % (list(self.fortes),
                                          self.contrato["identidade"]["fortes_por_prioridade"]))
        if self.contrato["score_type"] != SCORE_TYPE or self.contrato["score_version"] != SCORE_VERSION:
            raise ValueError("divergencia no score_type/score_version do contrato")
        if {k: Decimal(str(v)) for k, v in self.contrato["formula"]["pesos"].items()} != dict(PESOS):
            raise ValueError("divergencia nos pesos do contrato do componente")
        if tuple(self.contrato["formula"]["tipos_de_sinal"]) != TIPOS_DE_SINAL:
            raise ValueError("divergencia no vocabulario de tipos de sinal")
        if tuple(self.contrato["escrita_score"]["colunas_escritas"]) != COLUNAS_DO_SCORE:
            raise ValueError("divergencia nas colunas de escrita do score")
        conferir_pesos()
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
                "Automation Fit Score v1 nao escreve em prod (ADR-005): a promocao exige card "
                "proprio com aprovacao humana registrada")
        if self.ambiente not in AMBIENTES_PERMITIDOS:
            raise RecusaDeAmbiente("ambiente desconhecido: %r" % self.ambiente)
        return self.ambiente

    def identificar_alvo(self) -> dict:
        """Registra a identidade do alvo medido (sem afirmar nome de ambiente)."""
        rc, saida, erro = self.porta.executar(
            "SELECT current_database(), current_user, version();")
        if rc != 0:
            raise PortaIndisponivel("nao consegui ler a identidade do alvo: %s" % (erro or saida))
        campos = saida.strip().split("|")
        self.alvo = {"banco": campos[0] if campos else None,
                     "usuario": campos[1] if len(campos) > 1 else None,
                     "servidor": (campos[2] if len(campos) > 2 else "")[:80]}
        return self.alvo

    # -- leitura do estado ------------------------------------------------------------
    def _ler_json(self, sql: str, contexto: str) -> list:
        rc, saida, erro = self.porta.executar(sql)
        if rc != 0:
            raise PortaIndisponivel("%s falhou: %s" % (contexto, erro or saida))
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

    def organizacoes_casadas(self, validos: list) -> list:
        if not validos:
            return []
        return [r for r in self._ler_json(sql_consultar_organizacao(validos),
                                          "consulta de organizacao") if r.get("id")]

    def ler_estado(self, organizacao_id: str) -> tuple:
        """(perfil, sinais, hipoteses) — o estado REAL da empresa que entra na formula."""
        perfil = self._ler_json(sql_perfil(organizacao_id), "leitura do perfil")
        sinais = self._ler_json(sql_sinais(organizacao_id), "leitura dos sinais")
        hipoteses = self._ler_json(sql_hipoteses(organizacao_id), "leitura das hipoteses")
        if not perfil:
            raise PortaIndisponivel("empresa %s resolvida mas ausente na releitura" % organizacao_id)
        return perfil[0], sinais, hipoteses

    # -- modos ------------------------------------------------------------------------
    def planejar(self, pedido: dict) -> dict:
        """Modo sem banco: valida e diz o que FARIA (nao le estado: nao ha banco).

        A recusa por ausencia de identificador forte e' a MESMA do caminho com banco (`decidir_
        veredito`): planejar e' ensaio do que aconteceria, nao um caminho paralelo com regra propria.
        """
        validacao = validar_pedido(pedido, self.fortes, self.identidade)
        motivos = list(validacao["problemas"])
        if not motivos and not validacao["validos"]:
            motivos = ["SEM_IDENTIFICADOR_FORTE"]
        return {"veredito": PLANEJADO_RECUSAR if motivos else PLANEJADO_CALCULAR,
                "motivos": motivos,
                "score_type": SCORE_TYPE, "score_version": SCORE_VERSION,
                "identidades_validas": [{"tipo": t, "valor": v} for t, v in validacao["validos"]],
                "identidades_declaradas": validacao["declarados"],
                "descartados": validacao["descartados"],
                "pesos": {nome: str(peso) for nome, peso in PESOS.items()},
                "cobertura_minima": str(COBERTURA_MINIMA)}

    def processar(self, pedido: dict) -> dict:
        inicio = self.relogio()
        entrada = {"pedido": pedido, "ambiente": self.ambiente,
                   "correlation_id": self.correlation_id}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": ERRO, "motivos": [], "organization_id": None, "score_id": None,
                     "score_value": None, "score_type": SCORE_TYPE, "score_version": SCORE_VERSION,
                     "idempotency_key": None, "cobertura": None, "componentes": {}, "descartados": []}
        try:
            validacao = validar_pedido(pedido, self.fortes, self.identidade)
            resultado["descartados"] = list(validacao["descartados"])
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
                candidatas = sorted(c["id"] for c in casadas)
                chave_revisao = chave_da_revisao(candidatas, _sha256(json.dumps(
                    {"pedido": pedido, "correlation_id": self.correlation_id},
                    ensure_ascii=False, sort_keys=True)))
                resultado["idempotency_key"] = chave_revisao
                resultado["candidatas"] = candidatas
                rc_rev, saida_rev, erro_rev = self.porta.executar(
                    sql_pedir_revisao(aprovacao_id, sync_event_id, chave_revisao, pedido,
                                      resultado["motivos"][0],
                                      [{"organization_id": c} for c in candidatas],
                                      self.correlation_id))
                if rc_rev != 0:
                    # Fail-closed: sem a linha em human_approvals a ambiguidade NAO foi reportada —
                    # entao nao se pode dizer REVISAO_IDENTIDADE.
                    raise PortaIndisponivel("fila humana nao registrada: %s" % (erro_rev or saida_rev))
                marcas = [l.strip() for l in (saida_rev or "").splitlines()]
                resultado["human_approval_id"] = aprovacao_id
                resultado["revisao_registrada"] = MARCA_REVISAO in marcas
                if not resultado["revisao_registrada"]:
                    resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]
            elif veredito == CALCULADO:
                organizacao_id = casadas[0]["id"]
                resultado["organization_id"] = organizacao_id
                perfil, sinais, hipoteses = self.ler_estado(organizacao_id)
                calculo = calcular(perfil, sinais, hipoteses)
                resultado["cobertura"] = str(calculo["cobertura"])
                resultado["componentes"] = {
                    nome: {"presente": dados["presente"],
                           "valor": None if dados["valor"] is None else str(dados["valor"]),
                           "peso": str(dados["peso"])}
                    for nome, dados in calculo["componentes"].items()}
                resultado["ausentes"] = calculo["ausentes"]
                if calculo["veredito"] != CALCULADO:
                    # Fail-closed: sem lastro suficiente nao existe score. Nada e' escrito.
                    resultado["veredito"] = RECUSADA
                    resultado["motivos"] = list(calculo["motivos"])
                else:
                    entrada_hash = calculo["input_hash"]
                    chave = chave_idempotencia(organizacao_id, entrada_hash)
                    score_id = str(uuid.uuid4())
                    sync_event_id = str(uuid.uuid4())
                    calculado_em = self.relogio()
                    inputs = dict(snapshot_canonico(perfil, sinais, hipoteses))
                    inputs.update({"input_hash": entrada_hash,
                                   "cobertura": str(calculo["cobertura"]),
                                   "presentes": calculo["presentes"],
                                   "ausentes": calculo["ausentes"],
                                   "formula": calculo["formula"],
                                   "pesos": calculo["pesos"],
                                   "correlation_id": self.correlation_id,
                                   "organization_id": organizacao_id})
                    explanation = {
                        "score_type": SCORE_TYPE, "score_version": SCORE_VERSION,
                        "formula": calculo["formula"],
                        "pesos": calculo["pesos"],
                        "cobertura": str(calculo["cobertura"]),
                        "cobertura_minima": str(calculo["cobertura_minima"]),
                        "componentes": {
                            nome: {"presente": dados["presente"],
                                   "valor": None if dados["valor"] is None else str(dados["valor"]),
                                   "peso": str(dados["peso"]),
                                   "detalhe": dados["detalhe"]}
                            for nome, dados in calculo["componentes"].items()},
                        "deterministico": True,
                        "llm": {"executado": False,
                                "motivo": "v1 do Automation Fit Score e' aritmetica declarada"},
                        "regra": "componente ausente nao vota (normaliza pelos presentes); sem "
                                 "cobertura minima nao ha score; score e' historico, nunca mutado",
                    }
                    payload = {"origem": AGENTE, "componente": "%s/%s" % (AGENTE, VERSAO),
                               "correlation_id": self.correlation_id, "score_id": score_id,
                               "organization_id": organizacao_id, "score_type": SCORE_TYPE,
                               "score_version": SCORE_VERSION,
                               "score_value": str(calculo["valor"]),
                               "cobertura": str(calculo["cobertura"]),
                               "input_hash": entrada_hash, "idempotency_key": chave}
                    valores = {"organization_id": organizacao_id,
                               "score_value": calculo["valor"],
                               "inputs": inputs, "explanation": explanation,
                               "calculated_at": calculado_em}
                    rc, saida, erro = self.porta.executar(
                        sql_ingerir(score_id, sync_event_id, chave, valores, payload))
                    if rc != 0:
                        raise PortaIndisponivel("calculo falhou: %s" % (erro or saida))
                    marcas = [l.strip() for l in (saida or "").splitlines()]
                    resultado["idempotency_key"] = chave
                    resultado["input_hash"] = entrada_hash
                    if MARCA_CALCULADO in marcas:
                        resultado["score_id"] = score_id
                        resultado["sync_event_id"] = sync_event_id
                        resultado["score_value"] = str(calculo["valor"])
                    else:
                        # A chave do MESMO estado ja existia: nada foi duplicado. O valor devolvido
                        # e' o desta medicao (identico ao gravado — o hash cobre o estado).
                        resultado["veredito"] = JA_CALCULADO
                        resultado["score_value"] = str(calculo["valor"])
                        resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        status = STATUS_AGENT_RUNS[resultado["veredito"]]
        rc_run, saida_run, erro_run = self.porta.executar(sql_registrar_execucao(
            run_id, self.correlation_id, resultado.get("organization_id"), status,
            entrada, {k: v for k, v in resultado.items()}, inicio, fim, resultado.get("erro")))
        if rc_run != 0:
            # Fail-closed: a execucao NAO pode ser reportada como concluida quando a propria
            # auditoria nao foi escrita.
            resultado["veredito"] = ERRO
            resultado["motivos"] = list(resultado.get("motivos") or []) + \
                ["AUDITORIA_NAO_REGISTRADA: %s" % (erro_run or saida_run)]
            resultado["erro"] = {"tipo": "PortaIndisponivel",
                                 "mensagem": "auditoria nao registrada: %s" % (erro_run or saida_run)}
            resultado["auditoria_registrada"] = False
            resultado["agent_run_id"] = run_id
            resultado["status_agent_runs"] = STATUS_AGENT_RUNS[ERRO]
            return resultado
        resultado["agent_run_id"] = run_id
        resultado["auditoria_registrada"] = True
        resultado["status_agent_runs"] = status
        return resultado

    def rodar(self, pedidos: list, planejar: bool = False) -> dict:
        resultados = []
        for pedido in pedidos:
            resultados.append(self.planejar(pedido) if planejar else self.processar(pedido))
        resumo = {"agente": "%s/%s" % (AGENTE, VERSAO), "score_type": SCORE_TYPE,
                  "score_version": SCORE_VERSION, "ambiente": self.ambiente,
                  "correlation_id": self.correlation_id, "alvo": self.alvo,
                  "planejar": planejar, "total": len(resultados),
                  "por_veredito": {}, "resultados": resultados}
        for r in resultados:
            resumo["por_veredito"][r["veredito"]] = resumo["por_veredito"].get(r["veredito"], 0) + 1
        return resumo

    def desfazer(self, correlation_id: str, confirmo: bool = False) -> dict:
        itens = [i for i in self._ler_json(sql_selecionar_da_rodada(correlation_id),
                                           "listagem da rodada") if i.get("score_id")]
        if not confirmo:
            return {"correlation_id": correlation_id, "confirmo": False,
                    "scores": [i["score_id"] for i in itens],
                    "organizacoes": sorted({i["organization_id"] for i in itens}),
                    "apagados": 0, "dry_run": True}
        if itens:
            rc, saida, erro = self.porta.executar(
                sql_desfazer(itens, correlation_id, str(uuid.uuid4())), permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
        return {"correlation_id": correlation_id, "confirmo": True,
                "scores": [i["score_id"] for i in itens],
                "organizacoes": sorted({i["organization_id"] for i in itens}),
                "apagados": len(itens), "dry_run": False}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_pedidos(caminho: str) -> list:
    if caminho == "-":
        texto = sys.stdin.read()
    else:
        p = Path(caminho)
        if not p.is_file():
            raise SystemExit(EXIT_FONTE)
        texto = p.read_text(encoding="utf-8")
    pedidos = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        try:
            pedidos.append(json.loads(linha))
        except json.JSONDecodeError as exc:
            raise SystemExit("FALHOU linha %d da fonte nao e' JSON: %s" % (numero, exc))
    return pedidos


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Automation Fit Score v1 (TRE-W5-E02-T01)")
    p.add_argument("--fonte", help="arquivo jsonl com os pedidos de score ('-' = stdin)")
    p.add_argument("--ambiente", help="dev | homolog (prod e' recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true", help="valida e planeja; NAO toca o banco")
    p.add_argument("--relatorio", help="caminho do relatorio JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrao e' dry-run)")
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
    agente = AutomationFitScore(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                                correlation_id=args.correlation_id)
    # O ambiente e' exigido em todo modo que ESCREVE. No modo de planejamento (que nao abre conexao
    # nenhuma) ele e' opcional: se declarado, e' conferido; se ausente, nada e' escrito.
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
            pedidos = ler_pedidos(args.fonte)
            if args.planejar:
                relatorio = agente.rodar(pedidos, planejar=True)
            else:
                agente.identificar_alvo()
                relatorio = agente.rodar(pedidos)
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
        print("  %-20s %-24s %s" % (r["veredito"], (r.get("score_value") or "-"),
                                    ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
