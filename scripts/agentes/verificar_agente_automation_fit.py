#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do AUTOMATION FIT SCORE v1 (card TRE-W5-E02-T01) — um comando, um veredito.

    python3 scripts/agentes/verificar_agente_automation_fit.py
    python3 scripts/agentes/verificar_agente_automation_fit.py --autoteste
    python3 scripts/agentes/verificar_agente_automation_fit.py --codigo <caminho de outro automation_fit.py>

O que ela prova (SEM banco e SEM rede): o contrato do componente existe e nao divergiu do codigo
(pesos, tipos de sinal, colunas de escrita, score_type/score_version) nem do Data Contract V1.0
(`scores.types` tem AUTOMATION_FIT; o vocabulario de tipos e o de `employee_band` sao os do
contrato); os pesos somam exatamente 1,00; a FORMULA e' deterministica e reprodutivel, com
componente AUSENTE fora do numerador e do denominador (o score nao vira um segundo Data Quality
Score), limiar de cobertura fail-closed (`SEM_LASTRO`), derivacao do porte pela MESMA regra do
Scout, tipos/hipoteses fora do vocabulario sem voto e sem efeito no hash; o score TEM PODER DE
DISCRIMINACAO medido no corpus (faixas, margem e desvio da constante); o `input_hash` cobre o
estado que entra na formula (sinal novo => linha nova; replay => nada); a guarda de escrita recusa
DDL, tabela nao declarada, escrita nas tabelas de negocio do W4 (inclusive
`organizations.data_quality_score`), `UPDATE` em `scores` (score e' historico), INSERT sem as
colunas obrigatorias, coluna nao declarada e DELETE fora do desfazer; o fluxo completo (calcular,
replay idempotente, sem lastro, empresa inexistente, revisao de identidade, auditoria, desfazer)
se comporta como o contrato do card — medido numa PORTA DE ROTEIRO (implementacao da porta
declarada, nao dublê de biblioteca).

AUTOTESTE (`--autoteste`): cada mutacao e' aplicada a uma COPIA do arquivo apontado por `--codigo`
(o canonico, por padrao — as duas opcoes andam juntas de proposito: mutar o canonico enquanto se
testa outro arquivo e' prova contra alvo errado) e a suite tem de REPROVAR o item correspondente —
mutacao que passa em silencio e' buraco de verificacao. Mutacao que nao se aplica (ancora de texto
mudou), que nao declara item nenhum ou que declara item INEXISTENTE na suite tambem reprova: e'
buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = AUTOMATION_FIT_SUITE_OK · 1 = AUTOMATION_FIT_SUITE_FALHOU (o log aponta o
item) · 2 = uso incorreto. Guarda de confiabilidade: etapa que roda 0 item REPROVA.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_PADRAO = RAIZ / "hermes/agents/automation_fit/automation_fit.py"
CONTRATO_AGENTE = RAIZ / "hermes/agents/automation_fit/agente-automation-fit-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC_ARQUITETURA = RAIZ / "docs/architecture/agente-automation-fit-v1.md"
RUNBOOK = RAIZ / "docs/runbooks/agente-automation-fit.md"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO_FONTE = RAIZ / "hermes/agents/automation_fit/exemplos/perfis-exemplo.jsonl"
ACEITE = RAIZ / "scripts/agentes/teste_automation_fit_aceite.sh"

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "aaaaaaaa-0000-4000-8000-000000000002"
CNPJ_A = "11.222.333/0001-81"

ITENS = []


def item(nome):
    def decorador(funcao):
        ITENS.append((nome, funcao))
        return funcao
    return decorador


# ---------------------------------------------------------------------------------------
# Corpus declarado: e' com ele que o PODER DE DISCRIMINACAO do score e' medido (nao com o numero
# que a gente gostaria de ver). Perfis sinteticos, dez casos, cobrindo o vocabulario do contrato.
# ---------------------------------------------------------------------------------------
CORPUS = [
    {"nome": "p1", "perfil": {"employee_band": "300_499", "unit_count": 4},
     "sinais": ["PROCESS_COMPLEXITY", "SERVICE_VOLUME"], "impactos": ["90"]},
    {"nome": "p2", "perfil": {"employee_band": "150_299", "unit_count": 2},
     "sinais": ["AI_INITIATIVE", "DIGITAL_TRANSFORMATION"], "impactos": []},
    {"nome": "p3", "perfil": {"employee_band": "LT_70", "unit_count": 1},
     "sinais": [], "impactos": []},
    {"nome": "p4", "perfil": {"employee_band": "GT_1000", "unit_count": 12},
     "sinais": ["COST_REDUCTION"], "impactos": ["40"]},
    {"nome": "p5", "perfil": {"employee_band": "70_149", "unit_count": None},
     "sinais": ["CRM_CHANGE"], "impactos": ["70"]},
    {"nome": "p6", "perfil": {"employee_band": "700_1000", "unit_count": 3},
     "sinais": ["PROCESS_COMPLEXITY", "EFFICIENCY_PROGRAM", "TECH_ADOPTION"], "impactos": ["100"]},
    {"nome": "p7", "perfil": {"employee_band": "UNKNOWN", "employee_count": 250, "unit_count": 6},
     "sinais": ["SERVICE_VOLUME", "AI_INITIATIVE"], "impactos": ["60"]},
    {"nome": "p8", "perfil": {"employee_band": "500_699", "unit_count": 1},
     "sinais": ["HIRING"], "impactos": []},
    {"nome": "p9", "perfil": {"employee_band": "150_299", "unit_count": 5},
     "sinais": ["PROCESS_COMPLEXITY", "AI_INITIATIVE", "EFFICIENCY_PROGRAM"], "impactos": ["80"]},
    {"nome": "p10", "perfil": {"employee_band": "LT_70", "unit_count": 2},
     "sinais": ["COST_REDUCTION", "CRM_CHANGE"], "impactos": ["50"]},
]
# Limiares do aceite de discriminacao (declarados ANTES de medir; a suite compara com o medido).
MIN_FAIXAS = 3          # faixas de 20 pontos distintas
MIN_MARGEM = 30.0       # pontos entre o maior e o menor score do corpus
MIN_DESVIO_DA_CONSTANTE = 10.0   # |score - 50| medio: empate com a constante e' ruido calibrado
MIN_DESVIO_PADRAO = 8.0


def caso_do_corpus(entrada: dict) -> tuple:
    sinais = [{"id": "s%03d" % j, "signal_type": t} for j, t in enumerate(entrada["sinais"])]
    hipoteses = [{"id": "h%03d" % j, "business_impact_score": v}
                 for j, v in enumerate(entrada["impactos"])]
    return entrada["perfil"], sinais, hipoteses


# ---------------------------------------------------------------------------------------
# Contexto e utilitarios
# ---------------------------------------------------------------------------------------
class Contexto:
    def __init__(self, modulo, raiz):
        self.modulo = modulo
        self.raiz = Path(raiz)
        self.contrato = json.loads(CONTRATO_AGENTE.read_text(encoding="utf-8"))
        self.contrato_dados = json.loads(CONTRATO_DADOS.read_text(encoding="utf-8"))
        self.ddl = MIGRATION.read_text(encoding="utf-8")
        # o codigo SOB TESTE (a copia mutada quando ha' prova de dente), nao o arquivo canonico
        self.codigo = Path(modulo.__file__).read_text(encoding="utf-8")
        self.agente = modulo.AutomationFitScore(porta=PortaRoteiro(rota=[]), raiz=raiz)
        self.fortes = self.agente.fortes

    def colunas(self, tabela: str):
        """Colunas declaradas no DDL da migration congelada (o alvo real do INSERT)."""
        bloco = re.search(r"CREATE TABLE %s \((.*?)\n\);" % re.escape(tabela), self.ddl, re.S)
        if not bloco:
            return set()
        colunas = set()
        for linha in bloco.group(1).splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("--"):
                continue
            m = re.match(r"([a-z_][a-z0-9_]*)\s", linha)
            if m and m.group(1).upper() not in ("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE"):
                colunas.add(m.group(1))
        return colunas

    def valores_do_score(self, **extra):
        valores = {"organization_id": ORG_A, "score_value": Decimal("85.00"),
                   "inputs": {"input_hash": "a" * 64}, "explanation": {"formula": "x"},
                   "calculated_at": "2026-10-02T00:00:00+00:00"}
        valores.update(extra)
        return valores

    def sql_ingestao(self):
        """O SQL de ingestao que o componente GERA (o alvo real da guarda e do aceite)."""
        return self.modulo.sql_ingerir(
            "11111111-0000-4000-8000-000000000001",
            "22222222-0000-4000-8000-000000000001",
            "score:AUTOMATION_FIT:org:%s:%s" % (ORG_A, "a" * 64),
            self.valores_do_score(), {"origem": "automation_fit"})

    def perfil_bom(self, **extra):
        perfil = {"id": ORG_A, "employee_band": "300_499", "employee_count": 420,
                  "unit_count": 4, "status": "DISCOVERED"}
        perfil.update(extra)
        return perfil

    def sinais_bons(self):
        return [{"id": "s001", "signal_type": "PROCESS_COMPLEXITY"},
                {"id": "s002", "signal_type": "SERVICE_VOLUME"}]

    def hipoteses_boas(self):
        return [{"id": "h001", "business_impact_score": "90", "confidence": "0.7"}]


class PortaRoteiro:
    """Porta de teste: responde o roteiro por TRECHO de SQL e roda a guarda do componente.

    Ela implementa a porta do agente (mesma interface) — nao substitui o alvo por dublê de
    biblioteca, e todo SQL passa pela MESMA guarda que roda em producao. O roteiro e' casado por
    trecho (nao por posicao) para o item ficar legivel e nao quebrar ao acrescentar uma consulta.
    """

    def __init__(self, rota=None, modulo=None, respondedor=None):
        self.rota = list(rota or [])
        self.respondedor = respondedor
        self.modulo = modulo
        self.chamadas = []
        self.remocoes = []

    def executar(self, sql, permitir_remocao=False):
        if self.modulo is not None:
            self.modulo.validar_sql(sql, permitir_remocao=permitir_remocao)
        self.chamadas.append(sql)
        self.remocoes.append(permitir_remocao)
        if self.respondedor is not None:
            return self.respondedor(sql, permitir_remocao)
        if not self.rota:
            return 0, "", ""
        resposta = self.rota.pop(0)
        if callable(resposta):
            return resposta(sql)
        return resposta

    def sql_de(self, trecho: str) -> list:
        return [s for s in self.chamadas if trecho in s]


def responder(roteiro: dict):
    """Responde por trecho de SQL; trecho nao declarado devolve vazio (sem erro de porta)."""
    def _respondedor(sql, permitir_remocao=False):
        for trecho, resposta in roteiro.items():
            if trecho in sql:
                return resposta(sql) if callable(resposta) else resposta
        return 0, "", ""
    return _respondedor


def carregar_modulo(caminho):
    spec = importlib.util.spec_from_file_location("automation_fit_sob_teste", str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def jinha(registro: dict) -> str:
    return json.dumps(registro, ensure_ascii=False)


def gerar_corpus(modulo):
    """Roda o corpus declarado na funcao PURA e devolve os valores calculados."""
    valores = []
    for entrada in CORPUS:
        perfil, sinais, hipoteses = caso_do_corpus(entrada)
        resultado = modulo.calcular(perfil, sinais, hipoteses)
        if resultado["veredito"] == modulo.CALCULADO:
            valores.append(float(resultado["valor"]))
    return valores


def agente_com_roteiro(ctx, roteiro, **kwargs):
    porta = PortaRoteiro(modulo=ctx.modulo, respondedor=responder(roteiro))
    agente = ctx.modulo.AutomationFitScore(porta=porta, raiz=ctx.raiz, **kwargs)
    return agente, porta


# ---------------------------------------------------------------------------------------
# 1. Contrato, artefatos e vocabulario
# ---------------------------------------------------------------------------------------
@item("componente-existe-e-importa")
def _(ctx):
    assert ctx.modulo.AGENTE == "automation_fit"
    assert ctx.modulo.PAPEL == "automation_scoring"
    assert ctx.modulo.VERSAO == ctx.contrato["versao"]
    assert ctx.modulo.SCORE_TYPE == "AUTOMATION_FIT"
    assert ctx.modulo.SCORE_VERSION == ctx.contrato["score_version"]
    assert ctx.modulo.CALCULADO in ctx.modulo.VEREDITOS


@item("raiz-vem-do-marcador-nao-da-profundidade")
def _(ctx):
    raso = Path(tempfile.mkdtemp(prefix="automation-fit-raso-"))
    dentro = ctx.raiz / ("tmp-prova-raiz-%d" % os.getpid())
    antigo = Path.cwd()
    try:
        copia_rasa = raso / "mut" / "automation_fit.py"
        copia_rasa.parent.mkdir(parents=True, exist_ok=True)
        copia_rasa.write_text(ctx.codigo, encoding="utf-8")
        copia_profunda = dentro / "n1" / "n2" / "n3" / "n4" / "automation_fit.py"
        copia_profunda.parent.mkdir(parents=True, exist_ok=True)
        copia_profunda.write_text(ctx.codigo, encoding="utf-8")
        os.chdir(raso)
        modulo = carregar_modulo(copia_profunda)
        assert modulo.descobrir_raiz_padrao() == ctx.raiz, \
            "a raiz nao foi achada pelo marcador a 4 niveis: %s" % modulo.descobrir_raiz_padrao()
        agente = modulo.AutomationFitScore(porta=PortaRoteiro(rota=[]))
        assert agente.raiz == ctx.raiz and agente.contrato["card"] == "TRE-W5-E02-T01"
        copia = carregar_modulo(copia_rasa)
        assert copia.descobrir_raiz_padrao() != ctx.raiz, \
            "copia fora da arvore nao pode herdar silenciosamente a raiz do repo"
        try:
            copia.AutomationFitScore(porta=PortaRoteiro(rota=[]))
        except Exception as exc:  # noqa: BLE001 — qualquer recusa serve, desde que cite o contrato
            assert "agente-automation-fit-v1.json" in str(exc), exc
        else:
            raise AssertionError("contrato ausente nao foi recusado (fail-closed)")
    finally:
        os.chdir(antigo)
        shutil.rmtree(raso, ignore_errors=True)
        shutil.rmtree(dentro, ignore_errors=True)


@item("contrato-do-componente-bate-com-o-codigo")
def _(ctx):
    assert ctx.contrato["card"] == "TRE-W5-E02-T01"
    assert {k: Decimal(str(v)) for k, v in ctx.contrato["formula"]["pesos"].items()} \
        == dict(ctx.modulo.PESOS), "pesos do contrato x pesos do codigo divergiram"
    assert Decimal(str(ctx.contrato["formula"]["cobertura_minima"])) \
        == ctx.modulo.COBERTURA_MINIMA
    assert tuple(ctx.contrato["formula"]["tipos_de_sinal"]) == ctx.modulo.TIPOS_DE_SINAL
    assert tuple(ctx.contrato["escrita_score"]["colunas_escritas"]) == ctx.modulo.COLUNAS_DO_SCORE
    assert set(ctx.contrato["escrita"]["tabelas_permitidas"]) == set(ctx.modulo.TABELAS_PERMITIDAS)
    for tabela in ("sales_intelligence.organizations", "sales_intelligence.signals",
                   "sales_intelligence.pain_hypotheses"):
        assert tabela not in ctx.contrato["escrita"]["tabelas_permitidas"], \
            "o contrato do componente liberou escrita em %s" % tabela
    assert ctx.contrato["leitura"]["tabelas"], "contrato nao declara o que le"


@item("score-type-e-o-vocabulario-do-data-contract")
def _(ctx):
    scores = ctx.contrato_dados["scores"]
    assert ctx.modulo.SCORE_TYPE in scores["types"], "AUTOMATION_FIT nao esta em scores.types"
    assert scores["requires_version_column"] == "score_version"
    assert ctx.modulo.SCORE_VERSION, "score sem versao nao e' reprodutivel (doc 12 §8)"
    assert len(ctx.modulo.SCORE_VERSION) <= 30, "score_version acima do VARCHAR(30) do DDL"
    assert ctx.contrato_dados["source_of_truth"]["scores"] == "PostgreSQL"


@item("peso-do-automation-fit-no-priority-nao-e-usado-aqui")
def _(ctx):
    """O 0,30 do AUTOMATION_FIT e' do PRIORITY (W5-E05). Aqui ele so' pode ser DECLARADO."""
    pesos = ctx.contrato_dados["scores"]["priority_weights"]
    assert Decimal(str(pesos["AUTOMATION_FIT"])) == Decimal("0.30")
    assert Decimal(str(pesos["ICP"])) + Decimal(str(pesos["AUTOMATION_FIT"])) \
        + Decimal(str(pesos["BUYING_SIGNAL"])) + Decimal(str(pesos["DATA_QUALITY"])) == Decimal("1.00")
    assert "priority_weights" not in ctx.contrato["formula"], \
        "o contrato do componente nao pode carregar os pesos do Priority Score (card de outro)"
    assert "PRIORITY" not in ctx.modulo.SCORE_TYPE
    assert "priority_weight" not in ctx.codigo, \
        "o componente carrega o peso do Priority Score: isso e' o card W5-E05"


@item("pesos-somam-um")
def _(ctx):
    soma = sum(ctx.modulo.PESOS.values(), Decimal("0"))
    assert soma == Decimal("1.00"), "os pesos somam %s, nao 1,00" % soma
    assert ctx.modulo.SOMA_DOS_PESOS == Decimal("1.00")
    assert soma == ctx.modulo.SOMA_DOS_PESOS
    assert set(ctx.modulo.PESOS) == {"porte", "pressao_operacional", "prontidao_tecnologica",
                                     "dispersao_de_processos", "dor_quantificada"}
    ctx.modulo.conferir_pesos()  # o proprio componente tem de saber conferir


@item("tipos-de-sinal-sao-do-vocabulario-do-contrato")
def _(ctx):
    vocabulario = set(ctx.contrato_dados["vocabularies"]["signal_type"])
    assert set(ctx.modulo.TIPOS_DE_SINAL) == vocabulario, \
        "TIPOS_DE_SINAL divergiu do vocabularies.signal_type do contrato"
    for tabela in (ctx.modulo.PONTOS_PRESSAO, ctx.modulo.PONTOS_TECNOLOGIA):
        fora = sorted(set(tabela) - vocabulario)
        assert fora == [], "tipo de sinal fora do contrato na tabela de pontos: %s" % fora
        for tipo, pontos in tabela.items():
            assert Decimal("0") < Decimal(pontos) <= Decimal("1"), (tipo, pontos)
    # nenhum tipo do vocabulario pode ficar de fora do snapshot: quem nao pontua nao muda o score,
    # mas o TIPO e' reconhecido (senao um sinal legitimo viraria "inventado").
    assert set(ctx.modulo.PONTOS_PRESSAO) <= vocabulario
    assert set(ctx.modulo.TIPOS_DE_SINAL) == vocabulario


@item("faixas-de-porte-cobrem-o-vocabulario-do-contrato")
def _(ctx):
    faixas_contrato = set(ctx.contrato_dados["vocabularies"]["employee_band"])
    assert set(ctx.modulo.FATOR_POR_FAIXA) == faixas_contrato, \
        "FATOR_POR_FAIXA x vocabularies.employee_band divergiram: %s" % (
            set(ctx.modulo.FATOR_POR_FAIXA) ^ faixas_contrato)
    assert ctx.modulo.FATOR_POR_FAIXA["UNKNOWN"] is None, \
        "UNKNOWN tem de ser AUSENTE (None), nunca fator 0"
    for faixa, fator in ctx.modulo.FATOR_POR_FAIXA.items():
        if fator is None:
            continue
        assert Decimal("0") < Decimal(fator) <= Decimal("1"), (faixa, fator)
    # o sweet spot do contrato (150-700) tem de pontuar no maximo do componente
    for faixa in ("150_299", "300_499", "500_699"):
        assert ctx.modulo.FATOR_POR_FAIXA[faixa] == Decimal("1.00"), faixa
    assert ctx.contrato_dados["scores"]["icp_context"]["sweet_spot"] == "150-700"


@item("identidade-nao-tem-segunda-copia-no-agente")
def _(ctx):
    """A regra de identidade (e a faixa de funcionarios) e' IMPORTADA do Scout, nao recopiada."""
    assert ctx.modulo.MODULO_IDENTIDADE == "hermes/agents/scout/scout.py"
    for proibido in ("def cnpj_valido", "def normalizar_cnpj", "def normalizar_domain",
                     "def normalizar_linkedin", "def domain_valido", "def linkedin_valido"):
        assert proibido not in ctx.codigo, "segunda copia da regra de identidade: %s" % proibido
    identidade = ctx.modulo._identidade_para_regras()
    assert identidade._faixa_de_empregados(420) == "300_499"
    assert ctx.modulo.faixa_de_funcionarios(69) == "LT_70"
    assert ctx.modulo.faixa_de_funcionarios(1200) == "GT_1000"
    assert ctx.modulo.faixa_de_funcionarios(None) is None


@item("sem-rede-e-sem-llm")
def _(ctx):
    for proibido in ("import requests", "import urllib", "import socket", "urlopen", "http.client"):
        assert proibido not in ctx.codigo, "o componente carrega acesso de rede: %s" % proibido
    for proibido in ("openai", "anthropic", "httpx", "litellm"):
        assert proibido not in ctx.codigo, "o componente carrega cliente de LLM: %s" % proibido
    assert "llm" in ctx.codigo, "o contrato do card exige declarar que a v1 nao usa LLM"


# ---------------------------------------------------------------------------------------
# 2. A formula (funcao pura)
# ---------------------------------------------------------------------------------------
@item("calculo-e-deterministico")
def _(ctx):
    perfil, sinais, hipoteses = ctx.perfil_bom(), ctx.sinais_bons(), ctx.hipoteses_boas()
    primeiro = ctx.modulo.calcular(perfil, sinais, hipoteses)
    segundo = ctx.modulo.calcular(perfil, sinais, hipoteses)
    assert primeiro["valor"] == segundo["valor"] is not None
    assert primeiro["input_hash"] == segundo["input_hash"]
    assert primeiro["veredito"] == ctx.modulo.CALCULADO
    # estado medido em outra ORDEM (o banco nao promete ordem) da' o mesmo numero e o mesmo hash
    embaralhado = ctx.modulo.calcular(perfil, list(reversed(sinais)), list(reversed(hipoteses)))
    assert embaralhado["valor"] == primeiro["valor"]
    assert embaralhado["input_hash"] == primeiro["input_hash"]


@item("arredondamento-em-2-casas-com-half-up")
def _(ctx):
    assert ctx.modulo.arredondar(Decimal("1.005")) == Decimal("1.01"), \
        "ROUND_HALF_UP nao esta aplicado (1,005 -> 1,01)"
    assert ctx.modulo.arredondar(Decimal("2.675")) == Decimal("2.68")
    assert ctx.modulo.arredondar(Decimal("33.333333")) == Decimal("33.33")
    resultado = ctx.modulo.calcular(ctx.perfil_bom(), ctx.sinais_bons(), ctx.hipoteses_boas())
    texto = str(resultado["valor"])
    assert "." in texto and len(texto.split(".")[1]) <= 2, \
        "score_value precisa caber em NUMERIC(5,2): %s" % texto
    assert Decimal(texto) == Decimal(texto).quantize(Decimal("0.01"))


@item("teto-e-piso-do-score")
def _(ctx):
    """Com TODOS os componentes presentes e no máximo, o score e' 100,00; no mínimo, o piso."""
    perfil = {"employee_band": "300_499", "unit_count": 11}
    tudo_maximo = ctx.modulo.calcular(
        perfil,
        [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"},
         {"id": "s2", "signal_type": "EFFICIENCY_PROGRAM"},
         {"id": "s3", "signal_type": "SERVICE_VOLUME"},
         {"id": "s4", "signal_type": "AI_INITIATIVE"},
         {"id": "s5", "signal_type": "DIGITAL_TRANSFORMATION"},
         {"id": "s6", "signal_type": "ERP_CHANGE"}],
        [{"id": "h1", "business_impact_score": 100}])
    assert tudo_maximo["cobertura"] == Decimal("1.00")
    assert tudo_maximo["valor"] == Decimal("100.00"), tudo_maximo["valor"]
    tudo_minimo = ctx.modulo.calcular(
        {"employee_band": "LT_70", "unit_count": 1},
        [{"id": "s1", "signal_type": "REGULATORY_CHANGE"},
         {"id": "s2", "signal_type": "CRM_CHANGE"}],
        [{"id": "h1", "business_impact_score": 0}])
    assert tudo_minimo["cobertura"] == Decimal("1.00")
    assert tudo_minimo["valor"] == Decimal("18.75"), tudo_minimo["valor"]


@item("componente-ausente-nao-vota")
def _(ctx):
    """Sem prontidao tecnologica e sem dor, o score e' a media dos componentes PRESENTES."""
    sem_dois = ctx.modulo.calcular({"employee_band": "300_499", "unit_count": 10},
                                   [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}], [])
    assert sem_dois["presentes"] == ["dispersao_de_processos", "porte", "pressao_operacional"]
    assert sem_dois["ausentes"] == ["dor_quantificada", "prontidao_tecnologica"]
    assert sem_dois["cobertura"] == Decimal("0.65"), sem_dois["cobertura"]
    assert sem_dois["valor"] == Decimal("70.00"), sem_dois["valor"]
    # o MESMO estado com o ausente votando ZERO daria 45,50 (numerador / 1,00): o ausente saiu do
    # denominador de verdade — se ele votasse, o score seria outro.
    assert sem_dois["valor"] != Decimal("45.50")
    # e ausente nao e' voto "neutro": com os dois presentes zerados, o valor cai
    com_zero = ctx.modulo.calcular(
        {"employee_band": "300_499", "unit_count": 10},
        [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}],
        [{"id": "h1", "business_impact_score": 0}])
    assert com_zero["valor"] < sem_dois["valor"]


@item("cobertura-nova-dilui-o-numero")
def _(ctx):
    """PROPRIEDADE DECLARADA: componente novo entra com o valor DELE e pode diluir a media.

    Nao e' defeito, e' o desenho (media ponderada da evidencia presente): se a primeira evidencia de
    tecnologia vale 0,40 e as evidencias que ja' existiam valiam mais, o score cai. Quem pune
    ausencia de evidencia e' o DATA_QUALITY/PRIORITY (W5-E04/E05). O que NAO pode acontecer e' a
    falta de evidencia virar zero — disso cuida o item `componente-ausente-nao-vota`.
    """
    base = ctx.modulo.calcular({"employee_band": "300_499", "unit_count": 4},
                               [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"},
                                {"id": "s2", "signal_type": "SERVICE_VOLUME"}],
                               [{"id": "h1", "business_impact_score": 90}])
    assert base["valor"] == Decimal("85.00"), base["valor"]
    com_tecnologia = ctx.modulo.calcular({"employee_band": "300_499", "unit_count": 4},
                                         [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"},
                                          {"id": "s2", "signal_type": "SERVICE_VOLUME"},
                                          {"id": "s3", "signal_type": "AI_INITIATIVE"}],
                                         [{"id": "h1", "business_impact_score": 90}])
    assert com_tecnologia["cobertura"] == Decimal("1.00")
    assert com_tecnologia["valor"] == Decimal("76.00"), com_tecnologia["valor"]
    assert com_tecnologia["valor"] < base["valor"], "a diluicao declarada deixou de existir"


@item("cobertura-abaixo-do-minimo-recusa")
def _(ctx):
    so_porte = ctx.modulo.calcular({"employee_band": "300_499"}, [], [])
    assert so_porte["cobertura"] == Decimal("0.25")
    assert so_porte["veredito"] == ctx.modulo.RECUSADA
    assert so_porte["valor"] is None
    assert so_porte["motivos"] == ["SEM_LASTRO"]
    # 0,25 + 0,10 = 0,35 continua abaixo do limiar de 0,40
    porte_e_unidades = ctx.modulo.calcular({"employee_band": "300_499", "unit_count": 4}, [], [])
    assert porte_e_unidades["veredito"] == ctx.modulo.RECUSADA
    # 0,25 + 0,20 = 0,45 passa o limiar
    porte_e_tecnologia = ctx.modulo.calcular(
        {"employee_band": "300_499"}, [{"id": "s1", "signal_type": "TECH_ADOPTION"}], [])
    assert porte_e_tecnologia["cobertura"] == Decimal("0.45")
    assert porte_e_tecnologia["veredito"] == ctx.modulo.CALCULADO


@item("monotonico-no-conjunto-presente")
def _(ctx):
    """Com o MESMO conjunto de componentes presentes, mais evidencia nunca derruba o score."""
    base_sinais = [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}]
    base = ctx.modulo.calcular({"employee_band": "300_499", "unit_count": 4}, base_sinais, [])
    mais_um = ctx.modulo.calcular(
        {"employee_band": "300_499", "unit_count": 4},
        base_sinais + [{"id": "s2", "signal_type": "SERVICE_VOLUME"}], [])
    assert mais_um["valor"] >= base["valor"], (base["valor"], mais_um["valor"])
    mais_impacto = ctx.modulo.calcular(
        {"employee_band": "300_499", "unit_count": 4}, base_sinais,
        [{"id": "h1", "business_impact_score": 80}])
    assert mais_impacto["valor"] > base["valor"]


@item("porte-derivado-do-count-medido")
def _(ctx):
    por_faixa = ctx.modulo.calcular({"employee_band": "150_299", "unit_count": 3}, [], [])
    por_count = ctx.modulo.calcular({"employee_band": "UNKNOWN", "employee_count": 250,
                                     "unit_count": 3}, [], [])
    assert por_faixa["componentes"]["porte"]["valor"] == Decimal("1.00")
    assert por_count["componentes"]["porte"]["valor"] == Decimal("1.00")
    assert por_count["componentes"]["porte"]["detalhe"]["origem"] == "derivada_de_employee_count"
    assert por_count["componentes"]["porte"]["detalhe"]["employee_count"] == 250
    # faixa desconhecida SEM medicao e' AUSENTE (nao e' fator 0, nao e' chute)
    sem_porte = ctx.modulo.calcular({"employee_band": "UNKNOWN"}, [], [])
    assert sem_porte["componentes"]["porte"]["presente"] is False
    assert sem_porte["componentes"]["porte"]["valor"] is None
    nulo = ctx.modulo.calcular({}, [], [])
    assert nulo["componentes"]["porte"]["presente"] is False


@item("tipo-fora-do-vocabulario-nao-pontua")
def _(ctx):
    com_inventado = ctx.modulo.calcular(
        {"employee_band": "300_499", "unit_count": 4},
        [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"},
         {"id": "s2", "signal_type": "AUTOMACAO_QUANTICA"}], [])
    sem_inventado = ctx.modulo.calcular(
        {"employee_band": "300_499", "unit_count": 4},
        [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}], [])
    assert com_inventado["valor"] == sem_inventado["valor"]
    assert com_inventado["componentes"]["pressao_operacional"]["detalhe"]["tipos"] \
        == ["PROCESS_COMPLEXITY"]
    # e o lixo NAO entra no hash: o mesmo estado tem de devolver o mesmo input_hash
    perfil = {"employee_band": "300_499", "unit_count": 4}
    assert ctx.modulo.hash_da_entrada(perfil, [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"},
                                               {"id": "s2", "signal_type": "AUTOMACAO_QUANTICA"}], []) \
        == ctx.modulo.hash_da_entrada(perfil, [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}], []), \
        "sinal de tipo fora do vocabulario entrou no hash: o mesmo estado geraria chave nova"
    assert ctx.modulo.sinais_que_pontuam(
        [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"},
         {"id": "s2", "signal_type": "AUTOMACAO_QUANTICA"}]) \
        == [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}]
    assert "AUTOMACAO_QUANTICA" not in ctx.codigo


@item("hipotese-sem-impacto-nao-vota")
def _(ctx):
    sem_impacto = ctx.modulo.calcular(
        {"employee_band": "300_499", "unit_count": 4},
        [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}],
        [{"id": "h1", "business_impact_score": None}, {"id": "h2"}])
    assert sem_impacto["componentes"]["dor_quantificada"]["presente"] is False
    assert "dor_quantificada" in sem_impacto["ausentes"]
    com_impacto = ctx.modulo.calcular(
        {"employee_band": "300_499", "unit_count": 4},
        [{"id": "s1", "signal_type": "PROCESS_COMPLEXITY"}],
        [{"id": "h1", "business_impact_score": None}, {"id": "h2", "business_impact_score": 60}])
    assert com_impacto["componentes"]["dor_quantificada"]["valor"] == Decimal("0.6")
    # ausencia de impacto NAO pode virar zero: 0 medido e' evidencia, ausencia nao e' medida
    assert sem_impacto["valor"] != com_impacto["valor"]


@item("impacto-e-capado-na-faixa-do-contrato")
def _(ctx):
    acima = ctx.modulo.calcular({"employee_band": "300_499"}, [],
                                [{"id": "h1", "business_impact_score": 175}])
    assert acima["componentes"]["dor_quantificada"]["valor"] == Decimal("1")
    abaixo = ctx.modulo.calcular({"employee_band": "300_499"}, [],
                                 [{"id": "h1", "business_impact_score": -20}])
    assert abaixo["componentes"]["dor_quantificada"]["valor"] == Decimal("0")
    texto = ctx.modulo.calcular({"employee_band": "300_499"}, [],
                                [{"id": "h1", "business_impact_score": "abc"}])
    assert texto["componentes"]["dor_quantificada"]["presente"] is False, \
        "impacto nao numerico e' AUSENTE, nao zero"


@item("dispersao-por-unidades-declarada")
def _(ctx):
    esperado = {1: Decimal("0.30"), 2: Decimal("0.60"), 3: Decimal("0.60"),
                4: Decimal("0.85"), 10: Decimal("0.85"), 11: Decimal("1.00")}
    for unidades, fator in esperado.items():
        resultado = ctx.modulo.calcular({"employee_band": "300_499", "unit_count": unidades}, [], [])
        assert resultado["componentes"]["dispersao_de_processos"]["valor"] == fator, \
            (unidades, resultado["componentes"]["dispersao_de_processos"]["valor"])
    for vazio in (None, 0):
        resultado = ctx.modulo.calcular({"employee_band": "300_499", "unit_count": vazio}, [], [])
        assert resultado["componentes"]["dispersao_de_processos"]["presente"] is False, vazio


@item("discriminacao-no-corpus")
def _(ctx):
    """O score TEM de separar o corpus: estimador que empata com a constante e' ruido calibrado.

    Medido na funcao pura, sobre o corpus DECLARADO no topo desta suite (dez perfis). Limiares
    declarados ANTES da medicao: >=3 faixas de 20 pontos, margem >=30, desvio medio da constante
    (50) >=10 e desvio padrao >=8.
    """
    valores = gerar_corpus(ctx.modulo)
    assert len(valores) >= 6, "o corpus precisa de pelo menos 6 scores calculados: %d" % len(valores)
    faixas = sorted({int(valor // 20) for valor in valores})
    margem = max(valores) - min(valores)
    desvio_constante = statistics.mean(abs(valor - 50.0) for valor in valores)
    desvio_padrao = statistics.pstdev(valores)
    assert len(faixas) >= MIN_FAIXAS, "faixas distintas=%d (<%d)" % (len(faixas), MIN_FAIXAS)
    assert margem >= MIN_MARGEM, "margem=%.2f (<%.2f)" % (margem, MIN_MARGEM)
    assert desvio_constante >= MIN_DESVIO_DA_CONSTANTE, \
        "desvio medio da constante=%.2f (<%.2f)" % (desvio_constante, MIN_DESVIO_DA_CONSTANTE)
    assert desvio_padrao >= MIN_DESVIO_PADRAO, \
        "desvio padrao=%.2f (<%.2f)" % (desvio_padrao, MIN_DESVIO_PADRAO)
    assert len(set(valores)) >= 4, "o corpus produziu menos de 4 valores distintos: %s" % valores
    # o corpus tambem mede a RECUSA por falta de lastro (2 dos 10 perfis)
    recusados = [entrada["nome"] for entrada in CORPUS
                 if ctx.modulo.calcular(*caso_do_corpus(entrada))["veredito"] == ctx.modulo.RECUSADA]
    assert "p3" in recusados and "p8" in recusados, \
        "perfil sem lastro nao foi recusado: %s" % recusados
    assert "p1" in [entrada["nome"] for entrada in CORPUS], \
        "corpus declarado mudou de forma silenciosa"


# ---------------------------------------------------------------------------------------
# 3. Hash de entrada e idempotencia (offline)
# ---------------------------------------------------------------------------------------
@item("hash-cobre-o-estado-que-entra-na-formula")
def _(ctx):
    perfil, sinais, hipoteses = ctx.perfil_bom(), ctx.sinais_bons(), ctx.hipoteses_boas()
    base = ctx.modulo.hash_da_entrada(perfil, sinais, hipoteses)
    assert len(base) == 64
    # sinal NOVO (id novo) muda o hash -> estado novo, linha nova
    com_sinal_novo = ctx.modulo.hash_da_entrada(
        perfil, sinais + [{"id": "s003", "signal_type": "AI_INITIATIVE"}], hipoteses)
    assert com_sinal_novo != base
    # impacto novo muda o hash
    com_impacto = ctx.modulo.hash_da_entrada(
        perfil, sinais, hipoteses + [{"id": "h002", "business_impact_score": 55}])
    assert com_impacto != base
    # ordem nao muda o hash (o banco nao promete ordem)
    assert ctx.modulo.hash_da_entrada(perfil, list(reversed(sinais)),
                                      list(reversed(hipoteses))) == base
    # o que NAO entra na conta nao muda o hash: tipo inventado e hipotese sem impacto
    com_lixo = ctx.modulo.hash_da_entrada(
        perfil, sinais + [{"id": "s009", "signal_type": "AUTOMACAO_QUANTICA"}],
        hipoteses + [{"id": "h009", "business_impact_score": None}])
    assert com_lixo == base, "o hash versionou dado que nao entra na formula"
    # o hash e' do ESTADO, nao do nome da empresa: o perfil so' carrega o que a formula usa
    assert set(ctx.modulo.snapshot_canonico(perfil, sinais, hipoteses)) == {
        "employee_band", "employee_count", "unit_count", "sinais", "hipoteses"}


@item("chave-de-idempotencia-leva-o-estado")
def _(ctx):
    perfil, sinais, hipoteses = ctx.perfil_bom(), ctx.sinais_bons(), ctx.hipoteses_boas()
    hash_a = ctx.modulo.hash_da_entrada(perfil, sinais, hipoteses)
    hash_b = ctx.modulo.hash_da_entrada(
        perfil, sinais + [{"id": "s003", "signal_type": "AI_INITIATIVE"}], hipoteses)
    assert hash_a != hash_b
    chave_a = ctx.modulo.chave_idempotencia(ORG_A, hash_a)
    chave_b = ctx.modulo.chave_idempotencia(ORG_A, hash_b)
    assert chave_a != chave_b, "a chave nao carrega o estado: replay e estado novo colidem"
    assert hash_a in chave_a and ORG_A in chave_a
    assert ctx.modulo.SCORE_TYPE in chave_a
    assert len(chave_a) <= 255, "idempotency_key acima do VARCHAR(255) do DDL"
    # a mesma empresa com OUTRO estado nao colide; outra empresa com o mesmo estado nao colide
    assert ctx.modulo.chave_idempotencia(ORG_B, hash_a) != chave_a


@item("sql-de-ingestao-tem-o-claim-idempotente")
def _(ctx):
    sql = ctx.sql_ingestao()
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql, \
        "sem o claim da chave, retry criaria duplicata"
    assert "sync_events" in sql and "idempotency_key" in sql
    assert sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1, \
        "a ingestao tem de ser UMA transacao"
    assert "INSERT INTO {scores} ({cols})\nSELECT {vals} FROM claim".replace(
        "{scores}", "sales_intelligence.scores").replace("{cols}", "") or True
    assert "FROM claim" in sql, "o INSERT do score nao esta ancorado no claim"
    for chave in ("'score'", "'automation_fit'", "'postgresql'"):
        assert chave in sql, chave


@item("sql-de-ingestao-ancora-o-fechamento-no-score-desta-rodada")
def _(ctx):
    sql = ctx.sql_ingestao()
    assert "EXISTS (SELECT 1 FROM sales_intelligence.scores WHERE id = " in sql, \
        "o fechamento do sync_event nao esta ancorado na linha do score desta rodada"
    assert "AUTOMATION_FIT_CALCULADO" in sql
    assert "status = 'SUCCESS'" in sql
    # o fechamento nao pode marcar sucesso pelo simples fato de a chave existir
    assert "WHERE idempotency_key = " in sql


@item("sql-do-score-tem-as-colunas-do-ddl")
def _(ctx):
    colunas_ddl = ctx.colunas("sales_intelligence.scores")
    assert colunas_ddl, "nao consegui ler o DDL de scores"
    colunas, expressoes = ctx.modulo.montar_linha_score(
        "11111111-0000-4000-8000-000000000001", ctx.valores_do_score())
    assert colunas == list(ctx.modulo.COLUNAS_DO_SCORE)
    assert len(colunas) == len(expressoes)
    fora = sorted(set(colunas) - colunas_ddl)
    assert fora == [], "coluna escrita que nao existe no DDL: %s" % fora
    for obrigatoria in ("id", "organization_id", "score_type", "score_value", "score_version"):
        assert obrigatoria in colunas, obrigatoria
    assert "valid_until" not in colunas, \
        "valid_until e' lacuna declarada: o baseline nao define janela de validade"
    # paridade com os NOT NULL do DDL: coluna NOT NULL sem default tem de estar no INSERT
    bloco = re.search(r"CREATE TABLE sales_intelligence\.scores \((.*?)\n\);", ctx.ddl, re.S).group(1)
    for linha in bloco.splitlines():
        linha = linha.strip()
        if "NOT NULL" in linha and "DEFAULT" not in linha and not linha.startswith("--"):
            nome = re.match(r"([a-z_][a-z0-9_]*)", linha).group(1)
            assert nome in colunas, "coluna NOT NULL do DDL fora do INSERT: %s" % nome


@item("sql-le-so-a-empresa-medida")
def _(ctx):
    """Sinal/hipotese de OUTRA empresa nao pode entrar na conta: a leitura e' filtrada por id."""
    ctx.modulo.preparar_literais(ctx.modulo._identidade_para_regras())
    for construtor in (ctx.modulo.sql_sinais, ctx.modulo.sql_hipoteses, ctx.modulo.sql_perfil):
        sql = construtor(ORG_A)
        assert ORG_A in sql, "a leitura nao cita a empresa medida: %s" % construtor.__name__
        assert "WHERE" in sql, construtor.__name__
    assert "organization_id = " in ctx.modulo.sql_sinais(ORG_A)
    assert "organization_id = " in ctx.modulo.sql_hipoteses(ORG_A)
    # nenhuma leitura toca tabela de score (o score nao se realimenta)
    for sql in (ctx.modulo.sql_perfil(ORG_A), ctx.modulo.sql_sinais(ORG_A),
                ctx.modulo.sql_hipoteses(ORG_A)):
        assert "scores" not in sql, "o score esta se realimentando: %s" % sql
    assert "data_quality_score" not in ctx.modulo.sql_perfil(ORG_A), \
        "o score le a coluna do card W5-E04"


# ---------------------------------------------------------------------------------------
# 4. Guarda de escrita
# ---------------------------------------------------------------------------------------
@item("guarda-recusa-ddl")
def _(ctx):
    for sql in ("DROP TABLE sales_intelligence.scores;",
                "ALTER TABLE sales_intelligence.scores ADD COLUMN x INT;"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            pass
        else:
            raise AssertionError("DDL passou pela guarda: %s" % sql)


@item("guarda-nao-confunde-prosa-com-ddl")
def _(ctx):
    """Dado com 'drop' dentro ('queda de receita') nao pode ser lido como DDL."""
    sql = ctx.modulo.sql_ingerir(
        "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000001",
        "score:AUTOMATION_FIT:org:%s:%s" % (ORG_A, "a" * 64),
        ctx.valores_do_score(explanation={"regra": "queda (drop) de receita no e-commerce"}),
        {"origem": "automation_fit"})
    ctx.modulo.validar_sql(sql)  # nao pode levantar


@item("guarda-recusa-update-em-scores")
def _(ctx):
    try:
        ctx.modulo.validar_sql("UPDATE sales_intelligence.scores SET score_value = 99 WHERE id = 1;")
    except ctx.modulo.GuardaDeEscritaViolada as exc:
        assert "HISTORICO" in str(exc).upper(), exc
    else:
        raise AssertionError("UPDATE em scores passou: o score e' historico, nao mutavel")


@item("guarda-recusa-escrita-em-organizations")
def _(ctx):
    """Recusa por MOTIVO explicito: a tabela e' de outro agente, nao "tabela desconhecida"."""
    for sql in ("UPDATE sales_intelligence.organizations SET data_quality_score = 90 WHERE id = 1;",
                "INSERT INTO sales_intelligence.organizations (id) VALUES (gen_random_uuid());",
                "DELETE FROM sales_intelligence.organizations WHERE id = 1;"):
        try:
            ctx.modulo.validar_sql(sql, permitir_remocao=True)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert "nao escreve nele" in str(exc), \
                "recusa por motivo generico (%s) em vez de 'tabela de outro agente': %s" % (exc, sql)
        else:
            raise AssertionError("escrita em organizations passou: %s" % sql)
    assert "organization_id" in ctx.sql_ingestao()


@item("guarda-recusa-escrita-em-signals-e-pain-hypotheses")
def _(ctx):
    for sql in ("UPDATE sales_intelligence.signals SET relevance_score = 10 WHERE id = 1;",
                "INSERT INTO sales_intelligence.pain_hypotheses (id) VALUES (gen_random_uuid());",
                "UPDATE sales_intelligence.pain_hypotheses SET business_impact_score = 99;"):
        try:
            ctx.modulo.validar_sql(sql, permitir_remocao=True)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert "nao escreve nele" in str(exc), \
                "recusa por motivo generico (%s) em vez de 'tabela de outro agente': %s" % (exc, sql)
        else:
            raise AssertionError("escrita em tabela de negocio do W4 passou: %s" % sql)


@item("guarda-recusa-tabela-nao-declarada")
def _(ctx):
    for sql in ("INSERT INTO sales_intelligence.research_runs (id) VALUES (gen_random_uuid());",
                "INSERT INTO public.qualquer_coisa (id) VALUES (1);"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            pass
        else:
            raise AssertionError("escrita em tabela nao declarada passou: %s" % sql)


@item("guarda-recusa-delete-fora-do-desfazer")
def _(ctx):
    try:
        ctx.modulo.validar_sql("DELETE FROM sales_intelligence.scores WHERE id = 1;")
    except ctx.modulo.GuardaDeEscritaViolada:
        pass
    else:
        raise AssertionError("DELETE fora do desfazer passou")
    # dentro do desfazer explicito, passa (e so' em scores/sync_events)
    ctx.modulo.validar_sql("DELETE FROM sales_intelligence.scores WHERE id = 1;",
                           permitir_remocao=True)
    try:
        ctx.modulo.validar_sql("DELETE FROM sales_intelligence.agent_runs WHERE id = 1;",
                               permitir_remocao=True)
    except ctx.modulo.GuardaDeEscritaViolada:
        pass
    else:
        raise AssertionError("o desfazer nao pode apagar a AUDITORIA")


@item("guarda-recusa-insert-sem-coluna-obrigatoria")
def _(ctx):
    for colunas, obrigatoria in ((("id", "score_value"), "organization_id"),
                                 (("id", "organization_id", "score_value"), "score_type"),
                                 (("id", "organization_id", "score_type"), "score_value")):
        sql = "INSERT INTO sales_intelligence.scores (%s) VALUES (%s);" % (
            ", ".join(colunas), ", ".join("NULL" for _ in colunas))
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert obrigatoria in str(exc), (colunas, str(exc))
        else:
            raise AssertionError("INSERT sem %s passou pela guarda" % obrigatoria)


@item("guarda-recusa-insert-com-coluna-nao-declarada")
def _(ctx):
    sql = ("INSERT INTO sales_intelligence.scores "
           "(id, organization_id, score_type, score_value, score_version, score_version_extra) "
           "VALUES (1, 2, 'AUTOMATION_FIT', 80, 'automation-fit-v1', 'x');")
    try:
        ctx.modulo.validar_sql(sql)
    except ctx.modulo.GuardaDeEscritaViolada as exc:
        assert "nao declarada" in str(exc), exc
    else:
        raise AssertionError("coluna nao declarada passou pela guarda")


@item("guarda-aceita-o-sql-do-proprio-componente")
def _(ctx):
    ctx.modulo.validar_sql(ctx.sql_ingestao())
    ctx.modulo.validar_sql(ctx.modulo.sql_registrar_execucao(
        "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002",
        ORG_A, "COMPLETED", {"pedido": {}}, {"veredito": "CALCULADO"},
        "2026-10-02T00:00:00+00:00", "2026-10-02T00:00:01+00:00"))
    ctx.modulo.validar_sql(ctx.modulo.sql_desfazer(
        [{"score_id": "11111111-0000-4000-8000-000000000001",
          "sync_event_id": "22222222-0000-4000-8000-000000000001"}],
        "33333333-0000-4000-8000-000000000001", "44444444-0000-4000-8000-000000000001"),
        permitir_remocao=True)


# ---------------------------------------------------------------------------------------
# 5. Fluxo na porta de roteiro
# ---------------------------------------------------------------------------------------
def _roteiro_calculo():
    return {
        "FROM sales_intelligence.organizations WHERE deleted_at IS NULL":
            (0, jinha({"id": ORG_A, "status": "DISCOVERED", "legal_name": "Vale Forte",
                       "trade_name": "Vale Forte"}), ""),
        "FROM sales_intelligence.organizations WHERE id =":
            (0, jinha({"id": ORG_A, "employee_band": "300_499", "employee_count": 420,
                       "unit_count": 4, "status": "DISCOVERED"}), ""),
        "FROM sales_intelligence.signals":
            (0, "\n".join([jinha({"id": "s001", "signal_type": "PROCESS_COMPLEXITY"}),
                           jinha({"id": "s002", "signal_type": "SERVICE_VOLUME"})]), ""),
        "FROM sales_intelligence.pain_hypotheses":
            (0, jinha({"id": "h001", "business_impact_score": "90", "confidence": "0.7"}), ""),
        "INSERT INTO sales_intelligence.agent_runs": (0, "", ""),
        "INSERT INTO sales_intelligence.scores": (0, "AUTOMATION_FIT_CALCULADO", ""),
    }


@item("fluxo-calcula-e-grava-a-linha")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, _roteiro_calculo(), ambiente="dev")
    resultado = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert resultado["veredito"] == ctx.modulo.CALCULADO, resultado
    assert resultado["score_id"] and resultado["score_value"]
    assert Decimal(resultado["score_value"]) == Decimal("85.00"), resultado["score_value"]
    assert resultado["score_type"] == "AUTOMATION_FIT"
    assert resultado["cobertura"] == "0.80"
    assert resultado["ausentes"] == ["prontidao_tecnologica"], resultado["ausentes"]
    assert resultado["auditoria_registrada"] is True
    ingestao = porta.sql_de("INSERT INTO sales_intelligence.scores")
    assert len(ingestao) == 1
    assert resultado["idempotency_key"] in ingestao[0]
    assert resultado["input_hash"] in ingestao[0]
    assert "'AUTOMATION_FIT'" in ingestao[0] and "'automation-fit-v1'" in ingestao[0]


@item("fluxo-replay-devolve-ja-calculado")
def _(ctx):
    roteiro = _roteiro_calculo()
    # a chave ja existia: o claim volta vazio e o INSERT nao produz linha => sem marca de sucesso
    roteiro["INSERT INTO sales_intelligence.scores"] = (0, "", "")
    agente, porta = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    resultado = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert resultado["veredito"] == ctx.modulo.JA_CALCULADO, resultado
    assert resultado["score_id"] is None
    assert "IDEMPOTENCIA_REPLAY" in resultado["motivos"]
    assert resultado["status_agent_runs"] == "COMPLETED"


@item("fluxo-estado-novo-gera-linha-nova")
def _(ctx):
    """Sinal novo muda o input_hash: a chave muda e o INSERT encontra o claim livre.

    O valor pode SUBIR ou CAIR (a cobertura nova dilui — propriedade declarada e medida em
    `cobertura-nova-dilui-o-numero`); o que este item prova e' que o estado novo NAO cai no replay
    da chave antiga.
    """
    agente, porta = agente_com_roteiro(ctx, _roteiro_calculo(), ambiente="dev")
    primeiro = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    roteiro = _roteiro_calculo()
    roteiro["FROM sales_intelligence.signals"] = (
        0, "\n".join([jinha({"id": "s001", "signal_type": "PROCESS_COMPLEXITY"}),
                      jinha({"id": "s002", "signal_type": "SERVICE_VOLUME"}),
                      jinha({"id": "s003", "signal_type": "AI_INITIATIVE"})]), "")
    agente_2, porta_2 = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    segundo = agente_2.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert segundo["veredito"] == ctx.modulo.CALCULADO, segundo
    assert segundo["idempotency_key"] != primeiro["idempotency_key"]
    assert segundo["input_hash"] != primeiro["input_hash"]
    assert Decimal(segundo["score_value"]) != Decimal(primeiro["score_value"]), \
        "sinal novo nao entrou na conta"
    assert Decimal(segundo["cobertura"]) > Decimal(primeiro["cobertura"])
    assert Decimal(segundo["score_value"]) == Decimal("76.00"), segundo["score_value"]


@item("fluxo-recusa-sem-lastro")
def _(ctx):
    roteiro = _roteiro_calculo()
    roteiro["FROM sales_intelligence.organizations WHERE id ="] = (
        0, jinha({"id": ORG_A, "employee_band": "LT_70", "unit_count": 1,
                  "status": "DISCOVERED"}), "")
    roteiro["FROM sales_intelligence.signals"] = (0, "", "")
    roteiro["FROM sales_intelligence.pain_hypotheses"] = (0, "", "")
    agente, porta = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    resultado = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert resultado["veredito"] == ctx.modulo.RECUSADA
    assert resultado["motivos"] == ["SEM_LASTRO"]
    assert resultado["score_id"] is None
    assert porta.sql_de("INSERT INTO sales_intelligence.scores") == [], \
        "sem lastro nao pode haver escrita em scores"
    assert resultado["status_agent_runs"] == "REJECTED"


@item("fluxo-recusa-empresa-inexistente-e-sem-forte")
def _(ctx):
    roteiro = _roteiro_calculo()
    roteiro["FROM sales_intelligence.organizations WHERE deleted_at IS NULL"] = (0, "", "")
    agente, porta = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    inexistente = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert inexistente["veredito"] == ctx.modulo.RECUSADA
    assert "ORGANIZACAO_NAO_ENCONTRADA" in inexistente["motivos"]
    sem_forte = agente.processar({"organizacao": {"cidade": "Santos"}})
    assert sem_forte["veredito"] == ctx.modulo.RECUSADA
    assert "SEM_IDENTIFICADOR_FORTE" in sem_forte["motivos"]
    assert porta.sql_de("INSERT INTO sales_intelligence.scores") == []
    # nao ha leitura de estado para quem nao resolve: nenhuma consulta de perfil foi feita
    assert porta.sql_de("WHERE id =") == []


@item("fluxo-campo-nao-declarado-e-descartado")
def _(ctx):
    """Porte/impacto NAO vem da fonte: campo extra no pedido e' descartado com motivo."""
    limpo, _ = agente_com_roteiro(ctx, _roteiro_calculo(), ambiente="dev")
    referencia = limpo.processar({"organizacao": {"cnpj": CNPJ_A}})
    agente, porta = agente_com_roteiro(ctx, _roteiro_calculo(), ambiente="dev")
    resultado = agente.processar({"organizacao": {"cnpj": CNPJ_A}, "employee_band": "GT_1000",
                                  "unit_count": 9, "business_impact_score": 100})
    assert resultado["veredito"] == ctx.modulo.CALCULADO
    descartados = [d["campo"] for d in resultado["descartados"]]
    assert descartados == ["business_impact_score", "employee_band", "unit_count"], descartados
    assert all(d["motivo"] == "CAMPO_NAO_DECLARADO" for d in resultado["descartados"])
    # o numero e o hash sao os do BANCO: o porte/impacto declarado na fonte nao entrou na conta
    assert resultado["score_value"] == referencia["score_value"]
    assert resultado["input_hash"] == referencia["input_hash"]
    assert resultado["cobertura"] == referencia["cobertura"]


@item("fluxo-revisao-de-identidade-vai-para-a-fila-humana")
def _(ctx):
    roteiro = _roteiro_calculo()
    roteiro["FROM sales_intelligence.organizations WHERE deleted_at IS NULL"] = (
        0, "\n".join([jinha({"id": ORG_A}), jinha({"id": ORG_B})]), "")
    roteiro["INSERT INTO sales_intelligence.human_approvals"] = (
        0, "AUTOMATION_FIT_REVISAO_REGISTRADA", "")
    agente, porta = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    resultado = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert resultado["veredito"] == ctx.modulo.REVISAO
    assert resultado["revisao_registrada"] is True
    assert resultado["human_approval_id"]
    assert porta.sql_de("INSERT INTO sales_intelligence.scores") == []
    assert len(porta.sql_de("AUTOMATION_FIT_IDENTITY_REVIEW")) == 1
    assert resultado["status_agent_runs"] == "REVIEW_REQUIRED"
    # replay da MESMA ambiguidade nao abre pedido duplicado
    roteiro_2 = dict(roteiro)
    roteiro_2["INSERT INTO sales_intelligence.human_approvals"] = (0, "", "")
    agente_2, _ = agente_com_roteiro(ctx, roteiro_2, ambiente="dev")
    replay = agente_2.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert "IDEMPOTENCIA_REPLAY" in replay["motivos"]


@item("fluxo-fila-humana-que-nao-registra-vira-erro")
def _(ctx):
    roteiro = _roteiro_calculo()
    roteiro["FROM sales_intelligence.organizations WHERE deleted_at IS NULL"] = (
        0, "\n".join([jinha({"id": ORG_A}), jinha({"id": ORG_B})]), "")
    roteiro["INSERT INTO sales_intelligence.human_approvals"] = (1, "", "erro de banco")
    agente, _ = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    resultado = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert resultado["veredito"] == ctx.modulo.ERRO, resultado
    assert resultado["status_agent_runs"] == "FAILED"


@item("fluxo-auditoria-por-pedido-e-fail-closed")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, _roteiro_calculo(), ambiente="dev")
    agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    agente.processar({"organizacao": {"cidade": "Santos"}})
    auditorias = porta.sql_de("INSERT INTO sales_intelligence.agent_runs")
    assert len(auditorias) == 2, "uma linha de auditoria por pedido"
    assert "automation_scoring" in auditorias[0] and "'automation_fit'" in auditorias[0]
    assert "CALCULADO" in auditorias[0] and "REJECTED" in auditorias[1]
    # auditoria que NAO grava vira ERRO (nunca "concluido" sem trilha)
    roteiro = _roteiro_calculo()
    roteiro["INSERT INTO sales_intelligence.agent_runs"] = (1, "", "sem disco")
    agente_2, _ = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    resultado = agente_2.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert resultado["veredito"] == ctx.modulo.ERRO
    assert resultado["auditoria_registrada"] is False
    assert any("AUDITORIA_NAO_REGISTRADA" in m for m in resultado["motivos"])


@item("fluxo-porta-indisponivel-vira-erro-e-nao-sucesso")
def _(ctx):
    agente = ctx.modulo.AutomationFitScore(porta=ctx.modulo.PortaAusente(), raiz=ctx.raiz,
                                           ambiente="dev")
    try:
        agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    except ctx.modulo.PortaIndisponivel:
        pass
    else:
        raise AssertionError("porta ausente tinha de estourar (fail-closed)")


@item("prod-recusado-e-planejar-nao-abre-conexao")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, _roteiro_calculo(), ambiente="prod")
    try:
        agente.conferir_ambiente()
    except ctx.modulo.RecusaDeAmbiente as exc:
        assert "ADR-005" in str(exc)
    else:
        raise AssertionError("prod nao foi recusado")
    assert porta.chamadas == [], "ambiente recusado nao pode ter tocado o banco"
    sem_ambiente = ctx.modulo.AutomationFitScore(porta=ctx.modulo.PortaAusente(), raiz=ctx.raiz)
    try:
        sem_ambiente.conferir_ambiente()
    except ctx.modulo.RecusaDeAmbiente:
        pass
    else:
        raise AssertionError("ambiente nao declarado nao foi recusado")
    # --planejar com PORTA AUSENTE: se abrisse conexao, estouraria
    planejador = ctx.modulo.AutomationFitScore(porta=ctx.modulo.PortaAusente(), raiz=ctx.raiz)
    planejado = planejador.planejar({"organizacao": {"cnpj": CNPJ_A}})
    assert planejado["veredito"] == ctx.modulo.PLANEJADO_CALCULAR
    assert planejado["score_type"] == "AUTOMATION_FIT"
    invalido = planejador.planejar({"organizacao": {"cidade": "Santos"}})
    assert invalido["veredito"] == ctx.modulo.PLANEJADO_RECUSAR
    assert invalido["motivos"] == ["SEM_IDENTIFICADOR_FORTE"]


@item("fluxo-desfazer-dry-run-e-confirmo")
def _(ctx):
    item_rodada = jinha({"score_id": "11111111-0000-4000-8000-000000000001",
                         "organization_id": ORG_A,
                         "sync_event_id": "22222222-0000-4000-8000-000000000001"})
    roteiro = {
        "SELECT jsonb_build_object(": (0, item_rodada, ""),
        "DELETE FROM sales_intelligence.scores": (0, "", ""),
        "DELETE FROM sales_intelligence.sync_events": (0, "", ""),
        "INSERT INTO sales_intelligence.sync_events": (0, "", ""),
    }
    agente, porta = agente_com_roteiro(ctx, roteiro, ambiente="dev")
    seca = agente.desfazer("33333333-0000-4000-8000-000000000001")
    assert seca["dry_run"] is True and seca["apagados"] == 0
    assert seca["scores"] == ["11111111-0000-4000-8000-000000000001"]
    assert porta.sql_de("DELETE FROM sales_intelligence.scores") == [], \
        "dry-run nao pode apagar"
    agente_2, porta_2 = agente_com_roteiro(ctx, dict(roteiro), ambiente="dev")
    aplicado = agente_2.desfazer("33333333-0000-4000-8000-000000000001", confirmo=True)
    assert aplicado["dry_run"] is False and aplicado["apagados"] == 1
    sql = porta_2.sql_de("DELETE FROM sales_intelligence.scores")[0]
    assert "11111111-0000-4000-8000-000000000001" in sql
    assert porta_2.remocoes[porta_2.chamadas.index(sql)] is True, \
        "o desfazer tem de declarar a remocao explicita"
    assert "ROLLBACK" in porta_2.sql_de("INSERT INTO sales_intelligence.sync_events")[0]
    assert porta_2.sql_de("DELETE FROM sales_intelligence.agent_runs") == [], \
        "o desfazer nao toca a auditoria"
    # a rodada sem linha nenhuma nao apaga nada (e nao pode estourar)
    vazio, _ = agente_com_roteiro(ctx, {"SELECT jsonb_build_object(": (0, "", "")}, ambiente="dev")
    nada = vazio.desfazer("33333333-0000-4000-8000-000000000009", confirmo=True)
    assert nada["apagados"] == 0


@item("escopo-declarado-nao-invade-os-cards-irmaos")
def _(ctx):
    """O componente nao calcula ICP/BUYING_SIGNAL/DATA_QUALITY/PRIORITY/TIER/NBA."""
    for proibido in ("ICP", "BUYING_SIGNAL", "DATA_QUALITY", "PRIORITY", "tier", "next_best_action",
                     "recommendations", "outbox_events"):
        assert proibido not in ctx.modulo.SCORE_TYPE
    for outro in ("ICP", "BUYING_SIGNAL", "DATA_QUALITY", "PRIORITY"):
        assert outro not in ctx.contrato["formula"]["pesos"]
    assert ctx.modulo.TABELA_SCORES not in ctx.modulo.TABELAS_DE_OUTROS
    assert ctx.contrato["outbox"]["emite_evento"] is False


# ---------------------------------------------------------------------------------------
# 6. Artefatos do card e guarda da propria prova
# ---------------------------------------------------------------------------------------
@item("artefatos-do-card-estao-versionados-no-portao")
def _(ctx):
    portao = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    for artefato in ("hermes/agents/automation_fit/automation_fit.py",
                     "hermes/agents/automation_fit/agente-automation-fit-v1.json",
                     "hermes/agents/automation_fit/exemplos/perfis-exemplo.jsonl",
                     "scripts/agentes/verificar_agente_automation_fit.py",
                     "scripts/agentes/teste_automation_fit_aceite.sh",
                     "docs/architecture/agente-automation-fit-v1.md",
                     "docs/runbooks/agente-automation-fit.md"):
        assert artefato in portao, "artefato do card fora do portao: %s" % artefato
    for arquivo in (DOC_ARQUITETURA, RUNBOOK, EXEMPLO_FONTE, ACEITE):
        assert arquivo.is_file(), "artefato ausente: %s" % arquivo
    doc = DOC_ARQUITETURA.read_text(encoding="utf-8")
    for campo in ("ACCEPTANCE", "TEST", "ROLLBACK", "RISK"):
        assert campo in doc, "o contrato do card exige o campo %s" % campo
    exemplos = [json.loads(l) for l in EXEMPLO_FONTE.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.lstrip().startswith("#")]
    assert len(exemplos) >= 4
    for exemplo in exemplos:
        assert set(exemplo) == {"organizacao"}, exemplo
        assert opcional(exemplo["organizacao"], ("cnpj", "domain", "linkedin_url")) or \
            set(exemplo["organizacao"]) - set(ctx.fortes), exemplo


def opcional(objeto: dict, chaves) -> bool:
    return any(objeto.get(chave) for chave in chaves)


@item("autoteste-recusa-mutacao-sem-item-medido")
def _(ctx):
    """Dente com item esperado INEXISTENTE (ou sem item) tem de reprovar o autoteste."""
    fantasma = ("mutacao-com-item-fantasma", "ALVO_QUE_NAO_EXISTE", "SUBSTITUTO",
                ["item-que-nao-existe-na-suite"])
    sem_item = ("mutacao-sem-item-esperado", "ALVO_QUE_NAO_EXISTE", "SUBSTITUTO", [])
    problemas = problemas_em_mutacoes([fantasma, sem_item])
    assert len(problemas) == 2, "a guarda deixou passar mutacao muda: %r" % (problemas,)
    assert any("item-que-nao-existe-na-suite" in p for p in problemas), problemas
    assert any("nao declara item esperado" in p for p in problemas), problemas
    reais = problemas_em_mutacoes()
    assert reais == [], "mutacao declarada aponta item que a suite nao tem: %r" % (reais,)


# ---------------------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------------------
def executar_suite(modulo, raiz=RAIZ):
    ctx = Contexto(modulo, raiz)
    ok = falhas = 0
    resultados = {}
    for nome, funcao in ITENS:
        try:
            funcao(ctx)
        except Exception as exc:  # noqa: BLE001 — um item reprovado nao derruba a suite
            falhas += 1
            resultados[nome] = False
            print("FALHOU %s -> %s: %s" % (nome, type(exc).__name__, exc))
        else:
            ok += 1
            resultados[nome] = True
            print("OK     %s" % nome)
    return ok, falhas, resultados


def executar_suite_silencioso(modulo, raiz=RAIZ):
    resultados = {}
    suprimido = sys.stdout
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        _, _, resultados = executar_suite(modulo, raiz)
    except Exception:  # noqa: BLE001 — codigo mutado pode nem construir: vale como TUDO reprovado
        # Um componente que nem nasce (ex.: pesos guardados por `conferir_pesos()`) nao deixa item
        # passar: marcar todos como reprovados e' a leitura honesta, e mantem a prova de dente util.
        resultados = {nome: False for nome, _ in ITENS}
    finally:
        sys.stdout.close()
        sys.stdout = suprimido
    return resultados


MUTACOES = [
    ("pesos-nao-somam-um",
     'SOMA_DOS_PESOS = Decimal("1.00")',
     'SOMA_DOS_PESOS = Decimal("1.10")',
     ["pesos-somam-um"]),
    ("sem-limiar-de-cobertura",
     "    if cobertura < COBERTURA_MINIMA:\n        saida[\"motivos\"] = [\"SEM_LASTRO\"]\n"
     "        return saida",
     "    if False:\n        saida[\"motivos\"] = [\"SEM_LASTRO\"]\n        return saida",
     ["cobertura-abaixo-do-minimo-recusa", "fluxo-recusa-sem-lastro"]),
    ("score-constante",
     '    saida["valor"] = arredondar(Decimal("100") * numerador / cobertura)',
     '    saida["valor"] = arredondar(Decimal("50"))',
     ["discriminacao-no-corpus"]),
    ("componente-ausente-vota-zero",
     '    presentes = [nome for nome in PESOS if componentes[nome]["presente"]]',
     "    presentes = list(PESOS)",
     ["componente-ausente-nao-vota"]),
    ("idempotencia-sem-o-estado",
     '    return "score:%s:org:%s:%s" % (SCORE_TYPE, organizacao_id, entrada_hash)',
     '    return "score:%s:org:%s" % (SCORE_TYPE, organizacao_id)',
     ["chave-de-idempotencia-leva-o-estado", "fluxo-estado-novo-gera-linha-nova"]),
    ("sem-claim-idempotente",
     '        "  ON CONFLICT (idempotency_key) DO NOTHING\\n"\n'
     '        "  RETURNING id\\n"\n'
     '        ")\\n"\n'
     '        "INSERT INTO {scores} ({cols})\\n"',
     '        "  RETURNING id\\n"\n'
     '        ")\\n"\n'
     '        "INSERT INTO {scores} ({cols})\\n"',
     ["sql-de-ingestao-tem-o-claim-idempotente"]),
    ("fechamento-sem-ancora-no-score",
     '        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {scores} WHERE id = '
     '{sid})\\n"',
     '        "WHERE idempotency_key = {chave}\\n"',
     ["sql-de-ingestao-ancora-o-fechamento-no-score-desta-rodada"]),
    ("leitura-sem-filtro-de-empresa",
     '"FROM %s WHERE organization_id = %s ORDER BY detected_at, id;"',
     '"FROM %s WHERE %s IS NOT NULL ORDER BY detected_at, id;"',
     ["sql-le-so-a-empresa-medida"]),
    ("guarda-deixa-passar-ddl",
     "    if _DDL.search(codigo):",
     "    if False and _DDL.search(codigo):",
     ["guarda-recusa-ddl"]),
    ("guarda-aceita-update-em-scores",
     '            if operacao == "update" and tabela == TABELA_SCORES:',
     "            if False:",
     ["guarda-recusa-update-em-scores"]),
    ("guarda-aceita-escrita-em-organizations",
     "            if tabela in TABELAS_DE_OUTROS:",
     "            if False:",
     ["guarda-recusa-escrita-em-organizations",
      "guarda-recusa-escrita-em-signals-e-pain-hypotheses"]),
    ("guarda-aceita-tabela-nao-declarada",
     "            if tabela not in TABELAS_PERMITIDAS:",
     "            if False:",
     ["guarda-recusa-tabela-nao-declarada"]),
    ("guarda-aceita-delete-fora-do-desfazer",
     '            if operacao == "delete" and not permitir_remocao:',
     "            if False:",
     ["guarda-recusa-delete-fora-do-desfazer"]),
    ("guarda-aceita-insert-sem-coluna-obrigatoria",
     "        if faltando:",
     "        if False:",
     ["guarda-recusa-insert-sem-coluna-obrigatoria"]),
    ("guarda-aceita-coluna-nao-declarada",
     "        if fora:",
     "        if False:",
     ["guarda-recusa-insert-com-coluna-nao-declarada"]),
    ("sem-arredondamento",
     '    return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)',
     "    return Decimal(valor)",
     ["arredondamento-em-2-casas-com-half-up"]),
    ("hipotese-sem-impacto-vira-zero",
     '        impacto = _decimal(hipotese.get("business_impact_score"))\n'
     "        if impacto is None:\n"
     "            continue",
     '        impacto = _decimal(hipotese.get("business_impact_score")) or Decimal("0")',
     ["hipotese-sem-impacto-nao-vota"]),
    ("tipo-desconhecido-aceito",
     "        if tipo not in TIPOS_DE_SINAL:\n            continue",
     "        if False:\n            continue",
     ["tipo-fora-do-vocabulario-nao-pontua"]),
    ("fila-humana-sem-conferir-o-rc",
     "                if rc_rev != 0:",
     "                if False:",
     ["fluxo-fila-humana-que-nao-registra-vira-erro"]),
    ("auditoria-sem-conferir-o-rc",
     "        if rc_run != 0:",
     "        if False:",
     ["fluxo-auditoria-por-pedido-e-fail-closed"]),
    ("prod-liberado",
     "        if self.ambiente == AMBIENTE_RECUSADO:",
     "        if False:",
     ["prod-recusado-e-planejar-nao-abre-conexao"]),
    ("segunda-copia-da-regra-de-identidade",
     "def fator_de_porte(faixa, quantidade) -> tuple:",
     "def cnpj_valido(valor):\n    return True\n\n\n"
     "def normalizar_cnpj(valor):\n    return valor\n\n\n"
     "def fator_de_porte(faixa, quantidade) -> tuple:",
     ["identidade-nao-tem-segunda-copia-no-agente"]),
]


def problemas_em_mutacoes(mutacoes=None):
    """Guarda da PROPRIA prova: cada mutacao declara >=1 item e todo item declarado existe."""
    nomes_da_suite = {nome for nome, _ in ITENS}
    problemas = []
    for mutacao in (MUTACOES if mutacoes is None else mutacoes):
        nome, _alvo, _substituto, esperados = mutacao
        if not esperados:
            problemas.append("%s: nao declara item esperado (mutacao sem medicao)" % nome)
            continue
        for esperado in esperados:
            if esperado not in nomes_da_suite:
                problemas.append("%s: item esperado inexistente na suite: %s" % (nome, esperado))
    return problemas


def aplicar_mutacao(mutacao, destino: Path, fonte: Path = CODIGO_PADRAO):
    nome, alvo, substituto, itens_esperados = mutacao
    texto = Path(fonte).read_text(encoding="utf-8")
    if texto.count(alvo) != 1:
        return None, "ancora da mutacao nao casa exatamente 1 vez (%d)" % texto.count(alvo)
    destino.write_text(texto.replace(alvo, substituto), encoding="utf-8")
    return itens_esperados, "aplicada"


def autoteste(codigo: Path = CODIGO_PADRAO):
    print("\n=== AUTOTESTE (mutacoes em COPIA de %s) ===" % codigo)
    problemas = problemas_em_mutacoes()
    if problemas:
        for problema in problemas:
            print("FALHOU declaracao de mutacao -> %s" % problema)
        print("\nAUTOTESTE FALHOU (0/%d mutacoes detectadas)" % len(MUTACOES))
        return False
    ok_total = falhas_total = 0
    temporario = Path(tempfile.mkdtemp(prefix="automation-fit-autoteste-"))
    try:
        for mutacao in MUTACOES:
            nome = mutacao[0]
            # A copia vive um nivel ABAIXO do tempdir: assim ela importa de qualquer TMPDIR.
            copia = temporario / "mut" / ("automation_fit-%s.py" % nome)
            copia.parent.mkdir(parents=True, exist_ok=True)
            esperados, detalhe = aplicar_mutacao(mutacao, copia, codigo)
            if esperados is None:
                falhas_total += 1
                print("FALHOU mutacao %s -> %s (mutacao nao aplicada e' buraco de verificacao)"
                      % (nome, detalhe))
                continue
            try:
                modulo = carregar_modulo(copia)
            except Exception as exc:  # noqa: BLE001 — sem veredito na tela, so' falha medida
                falhas_total += 1
                print("FALHOU mutacao %s nao pode ser importada (%s: %s) — sem o item esperado "
                      "(%s) nao ha' deteccao" % (nome, type(exc).__name__, exc,
                                                 ", ".join(esperados)))
                continue
            print("\n-- mutacao %s (tem de reprovar: %s)" % (nome, ", ".join(esperados)))
            resultados = executar_suite_silencioso(modulo)
            desconhecidos = [i for i in esperados if i not in resultados]
            if desconhecidos:
                falhas_total += 1
                print("FALHOU mutacao %s declara item que NAO existe no resultado da suite: %s "
                      "(mutacao muda: nada a reprovar)" % (nome, desconhecidos))
                continue
            reprovados = sorted(i for i, v in resultados.items() if not v)
            nao_reprovados = [i for i in esperados if resultados.get(i)]
            if nao_reprovados:
                falhas_total += 1
                print("FALHOU mutacao %s nao reprovou: %s" % (nome, nao_reprovados))
            else:
                ok_total += 1
                print("OK     mutacao %s reprovada por %d item(ns): %s"
                      % (nome, len(reprovados), ", ".join(reprovados)))
    finally:
        shutil.rmtree(temporario, ignore_errors=True)
    print("\nAUTOTESTE %s (%d/%d mutacoes detectadas)"
          % ("OK" if falhas_total == 0 else "FALHOU", ok_total, len(MUTACOES)))
    return falhas_total == 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Suite do Automation Fit Score v1 (TRE-W5-E02-T01)")
    p.add_argument("--codigo", default=str(CODIGO_PADRAO),
                   help="caminho de outro automation_fit.py")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not ITENS:
        print("FALHOU a suite nao executou nenhum item")
        return 1
    modulo = carregar_modulo(Path(args.codigo))
    print("=== SUITE DO AUTOMATION FIT SCORE v1 (%s) ===" % args.codigo)
    ok, falhas, _ = executar_suite(modulo)
    print("\nRESULTADO: AUTOMATION_FIT_SUITE_%s (%d itens, %d falhas)"
          % ("OK" if falhas == 0 else "FALHOU", ok + falhas, falhas))
    sucesso = falhas == 0
    if args.autoteste:
        sucesso = autoteste(Path(args.codigo)) and sucesso
        print("RESULTADO FINAL: AUTOMATION_FIT_%s" % ("OK" if sucesso else "FALHOU"))
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
