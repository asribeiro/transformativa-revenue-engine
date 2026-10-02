#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do AGENTE RESEARCH v1 (card TRE-W4-E02-T01) — um comando, um veredito.

    python3 scripts/agentes/verificar_agente_research.py
    python3 scripts/agentes/verificar_agente_research.py --autoteste
    python3 scripts/agentes/verificar_agente_research.py --codigo <caminho de outro research.py>

O que ela prova (SEM banco e SEM rede): o contrato do agente existe e nao divergiu do Data
Contract V1.0; a organizacao e resolvida por identificador FORTE e a pesquisa NAO cria empresa;
o enriquecimento so escreve colunas declaradas do tipo do pedido, com o formato que NAO
sobrescreve; achado invalido e DESCARTADO com motivo; a idempotencia esta no SQL (nao na prosa);
a guarda de escrita recusa DDL, tabela nao declarada, UPDATE de organizacao sem modo/coluna
proibida/sem COALESCE e DELETE em organizacao; o gate do JEV e fail-closed; e o fluxo completo
(pesquisar, replay idempotente, revisao, recusar, auditoria, desfazer) se comporta como o
contrato do card — medido numa PORTA DE ROTEIRO (implementacao da porta declarada, nao dublê de
biblioteca).

AUTOTESTE (`--autoteste`): cada mutacao e aplicada a uma COPIA do `research.py` e a suite tem de
REPROVAR o item correspondente — mutacao que passa em silencio e buraco de verificacao. Mutacao
que nao se aplica (ancora de texto mudou) tambem reprova: e buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = RESEARCH_SUITE_OK · 1 = RESEARCH_SUITE_FALHOU (o log aponta o item) ·
2 = uso incorreto. Guarda de confiabilidade: etapa que roda 0 item REPROVA.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_PADRAO = RAIZ / "hermes/agents/research/research.py"
CONTRATO_AGENTE = RAIZ / "hermes/agents/research/agente-research-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC_ARQUITETURA = RAIZ / "docs/architecture/agente-research-v1.md"
RUNBOOK = RAIZ / "docs/runbooks/agente-research.md"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO_FONTE = RAIZ / "hermes/agents/research/exemplos/pesquisas-exemplo.jsonl"
CONTRATO_AGENTE_SCOUT = RAIZ / "hermes/agents/scout/agente-scout-v1.json"
MODULO_SCOUT = RAIZ / "hermes/agents/scout/scout.py"
ACEITE = RAIZ / "scripts/agentes/teste_research_aceite.sh"

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "aaaaaaaa-0000-4000-8000-000000000002"
CNPJ_A = "11.222.333/0001-81"
CNPJ_B = "45.723.174/0001-10"

ITENS = []


def item(nome):
    def decorador(funcao):
        ITENS.append((nome, funcao))
        return funcao
    return decorador


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
        self.agente = modulo.Research(porta=PortaRoteiro(rota=[]), raiz=raiz)
        # o codigo SOB TESTE (a copia mutada quando ha' prova de dente), nao o arquivo canonico
        self.codigo = Path(modulo.__file__).read_text(encoding="utf-8")
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

    def sql_ingestao(self, enriquecidas=("industry_name",)):
        """O SQL de ingestao que o agente GERA (o alvo real da guarda e do aceite)."""
        depois = {c: ("Metalurgia" if c in enriquecidas else None)
                  for c in self.modulo.COLUNAS_ENRIQUECIMENTO}
        return self.modulo.sql_ingerir(
            ORG_A, "11111111-0000-4000-8000-000000000001",
            "22222222-0000-4000-8000-000000000001",
            "research:org:%s:COMPANY_PROFILE:%s" % (ORG_A, "a" * 64),
            {"research_type": "COMPANY_PROFILE", "started_at": "t0", "completed_at": "t1",
             "status": "COMPLETED", "source_count": 1, "confidence": None, "summary": "s",
             "structured_output": {}, "input_hash": "a" * 64},
            list(enriquecidas),
            {"antes": {c: None for c in self.modulo.COLUNAS_ENRIQUECIMENTO}, "depois": depois},
            {"origem": "research"})


class PortaRoteiro:
    """Porta de teste: responde o roteiro declarado, GUARDA o SQL e roda a guarda do agente.

    Ela implementa a porta do agente (mesma interface) — nao substitui o alvo por dublê de
    biblioteca, e o SQL passa pela mesma guarda que roda em producao.
    """

    def __init__(self, rota=None, modulo=None):
        self.rota = list(rota or [])
        self.chamadas = []
        self.modos = []
        self.modulo = modulo

    def executar(self, sql, modo_organizacoes=None, permitir_remocao=False):
        if self.modulo is not None:
            self.modulo.validar_sql(sql, modo_organizacoes=modo_organizacoes,
                                    permitir_remocao=permitir_remocao)
        self.chamadas.append(sql)
        self.modos.append(modo_organizacoes)
        if not self.rota:
            return 0, "", ""
        resposta = self.rota.pop(0)
        if callable(resposta):
            return resposta(sql)
        return resposta


def carregar_modulo(caminho):
    spec = importlib.util.spec_from_file_location("research_sob_teste", str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def pedido(**campos):
    base = {"organizacao": {"cnpj": CNPJ_A}, "tipo": "COMPANY_PROFILE",
            "fontes": [{"tipo": "WEB", "url": "https://exemplo.test/p", "trecho": "trecho"}],
            "achados": {"industry_name": "Metalurgia"}}
    base.update(campos)
    return base


def linha_organizacao(org_id: str, **valores) -> str:
    registro = {"id": org_id, "status": valores.pop("status", "DISCOVERED"),
                "legal_name": valores.pop("legal_name", "Empresa Teste Ltda"),
                "trade_name": valores.pop("trade_name", "Empresa Teste")}
    for coluna in ("industry_code", "industry_name", "business_model", "website_url", "city",
                   "state", "country_code", "employee_count", "employee_band", "unit_count",
                   "revenue_estimate"):
        registro[coluna] = valores.pop(coluna, "")
    return json.dumps(registro, ensure_ascii=False)


def texto_do_sql(chamadas) -> str:
    return "\n".join(chamadas)


# ---------------------------------------------------------------------------------------
# 1. Contrato e artefatos
# ---------------------------------------------------------------------------------------
@item("agente-existe-e-importa")
def _(ctx):
    assert ctx.modulo.AGENTE == "research"
    assert ctx.modulo.PAPEL == "research"
    assert ctx.modulo.VERSAO == ctx.contrato["versao"]


@item("raiz-vem-do-marcador-nao-da-profundidade")
def _(ctx):
    """A copia roda de qualquer cwd e de qualquer profundidade: a raiz sai do MARCADOR."""
    raso = Path(tempfile.mkdtemp(prefix="research-raso-"))
    dentro = ctx.raiz / ("tmp-prova-raiz-%d" % os.getpid())
    antigo = Path.cwd()
    try:
        copia_rasa = raso / "mut" / "research.py"
        copia_rasa.parent.mkdir(parents=True, exist_ok=True)
        copia_rasa.write_text(ctx.codigo, encoding="utf-8")
        copia_profunda = dentro / "n1" / "n2" / "n3" / "n4" / "research.py"
        copia_profunda.parent.mkdir(parents=True, exist_ok=True)
        copia_profunda.write_text(ctx.codigo, encoding="utf-8")
        os.chdir(raso)
        modulo = carregar_modulo(copia_profunda)
        assert modulo.descobrir_raiz_padrao() == ctx.raiz, \
            "a raiz nao foi achada pelo marcador a 4 niveis: %s" % modulo.descobrir_raiz_padrao()
        agente = modulo.Research(porta=PortaRoteiro(rota=[]))
        assert agente.raiz == ctx.raiz and agente.contrato["card"] == "TRE-W4-E02-T01"
        copia = carregar_modulo(copia_rasa)
        assert copia.descobrir_raiz_padrao() != ctx.raiz, \
            "copia fora da arvore nao pode herdar silenciosamente a raiz do repo"
        try:
            copia.Research(porta=PortaRoteiro(rota=[]))
        except Exception as exc:  # noqa: BLE001 — qualquer recusa serve, desde que cite o contrato
            assert "agente-research-v1.json" in str(exc), exc
        else:
            raise AssertionError("contrato ausente nao foi recusado (fail-closed)")
    finally:
        os.chdir(antigo)
        shutil.rmtree(raso, ignore_errors=True)
        shutil.rmtree(dentro, ignore_errors=True)


@item("contrato-do-agente-casa-com-o-codigo")
def _(ctx):
    c = ctx.contrato
    assert c["agente"] == ctx.modulo.AGENTE
    assert c["papel"] == ctx.modulo.PAPEL
    assert c["versao"] == ctx.modulo.VERSAO
    assert c["card"] == "TRE-W4-E02-T01"
    assert set(c["vereditos"]) == set(ctx.modulo.VEREDITOS)
    assert c["status_de_agent_runs"] == ctx.modulo.STATUS_AGENT_RUNS
    assert set(c["fontes"]) == set(ctx.modulo.FONTES)
    assert set(c["tipos_de_pesquisa"]) == set(ctx.modulo.TIPOS_DE_PESQUISA)
    assert set(c["enriquecimento"]["colunas_permitidas"]) == set(ctx.modulo.COLUNAS_ENRIQUECIMENTO)
    assert c["enriquecimento"]["tipos_por_coluna"] == ctx.modulo.TIPO_POR_COLUNA
    assert c["escrita"]["tabelas_permitidas"] and \
        set(ctx.modulo.TABELAS_PERMITIDAS) == set(c["escrita"]["tabelas_permitidas"])


@item("identidade-forte-vem-do-contrato-de-dados")
def _(ctx):
    assert list(ctx.fortes) == list(ctx.contrato_dados["dedup"]["strong"])
    assert list(ctx.fortes) == list(ctx.contrato["identidade"]["fortes_por_prioridade"])


@item("vocabulario-de-fontes-e-o-mesmo-dos-dois-agentes")
def _(ctx):
    """A fonte e o canal por onde o dado entrou (doc 02 §1): descoberta e pesquisa falam o mesmo."""
    scout = json.loads(CONTRATO_AGENTE_SCOUT.read_text(encoding="utf-8"))
    assert list(ctx.contrato["fontes"]) == list(scout["fontes"])


@item("contrato-declara-que-nao-cria-organizacao")
def _(ctx):
    identidade = ctx.contrato["identidade"]
    assert identidade["cria_organizacao"] is False
    assert identidade["escreve_identificador_forte"] is False
    assert ctx.contrato["outbox"]["emite_evento"] is False
    assert ctx.contrato["lacunas_declaradas"]


@item("colunas-de-enriquecimento-existem-no-ddl")
def _(ctx):
    colunas = ctx.colunas("sales_intelligence.organizations")
    faltando = [c for c in ctx.modulo.COLUNAS_ENRIQUECIMENTO if c not in colunas]
    assert not faltando, "colunas de enriquecimento fora do DDL: %s" % faltando


@item("colunas-de-enriquecimento-nao-tocam-identidade-e-estagio")
def _(ctx):
    proibidas = set(ctx.contrato["enriquecimento"]["colunas_proibidas"])
    assert {"id", "cnpj", "domain", "linkedin_url", "status", "data_quality_score",
            "created_at", "deleted_at", "odoo_partner_id"} <= proibidas
    assert not (set(ctx.modulo.COLUNAS_ENRIQUECIMENTO) & proibidas)


@item("escrita-de-research-runs-casa-com-o-ddl")
def _(ctx):
    colunas, expressoes = ctx.modulo.montar_linha_research_run(
        "11111111-0000-4000-8000-000000000001", ORG_A,
        {"research_type": "COMPANY_PROFILE", "started_at": "t0", "completed_at": "t1",
         "status": "COMPLETED", "source_count": 1, "confidence": 0.5, "summary": "s",
         "structured_output": {}, "input_hash": "a" * 64})
    assert len(colunas) == len(expressoes), "colunas x valores divergentes"
    do_ddl = ctx.colunas("sales_intelligence.research_runs")
    faltando = [c for c in colunas if c not in do_ddl]
    assert not faltando, "colunas do INSERT fora do DDL: %s" % faltando
    assert len(colunas) == len(do_ddl), "o INSERT nao declara todas as colunas do contrato"


@item("escrita-de-agent-runs-casa-com-o-ddl")
def _(ctx):
    sql = ctx.modulo.sql_registrar_execucao(
        "11111111-0000-4000-8000-000000000001", "corr", ORG_A, "COMPLETED", {}, {}, "t0", "t1")
    colunas = re.search(r"INSERT INTO \S+ \(([^)]*)\)", sql).group(1)
    colunas = [c.strip() for c in colunas.split(",")]
    do_ddl = ctx.colunas("sales_intelligence.agent_runs")
    assert not [c for c in colunas if c not in do_ddl]
    expressoes = sql.split("VALUES", 1)[1].strip().rstrip(";")
    if expressoes.startswith("(") and expressoes.endswith(")"):
        # construtor de linha: o splitter separa por virgula de TOPO, entao a casca sai antes
        expressoes = expressoes[1:-1]
    assert len(ctx.modulo._itens_do_set(expressoes + ",")) == len(colunas), \
        "colunas x valores divergentes no agent_runs"


@item("escrita-de-human-approvals-casa-com-o-ddl")
def _(ctx):
    sql = ctx.modulo.sql_pedir_revisao("11111111-0000-4000-8000-000000000001", pedido(), "MOTIVO",
                                       [], "corr")
    colunas = re.search(r"INSERT INTO \S+ \(([^)]*)\)", sql).group(1)
    colunas = [c.strip() for c in colunas.split(",")]
    assert not [c for c in colunas if c not in ctx.colunas(
        "sales_intelligence.human_approvals")]


@item("escrita-de-sync-events-casa-com-o-ddl")
def _(ctx):
    do_ddl = ctx.colunas("sales_intelligence.sync_events")
    for sql in (ctx.sql_ingestao(), ctx.modulo.sql_desfazer(
            [{"research_run_id": "1", "organization_id": ORG_A, "sync_event_id": "2",
              "antes": {}, "depois": {}}], "corr", "3")):
        for bloco in re.findall(r"INSERT INTO %s \(([^)]*)\)" % ctx.modulo.TABELA_SYNC_EVENTS,
                               sql):
            colunas = [c.strip() for c in bloco.split(",")]
            assert not [c for c in colunas if c not in do_ddl], colunas


@item("faixas-de-empregados-cobrem-o-vocabulario-do-contrato")
def _(ctx):
    vocabulario = set(ctx.contrato_dados["vocabularies"]["employee_band"])
    faixas = {f for _, f in ctx.modulo.FAIXAS_DE_EMPREGADOS} | {ctx.modulo.FAIXA_ACIMA,
                                                                ctx.modulo.FAIXA_SEM_VALOR}
    assert faixas == vocabulario, "faixas %s x vocabulario %s" % (sorted(faixas), sorted(vocabulario))
    limites = ctx.contrato["faixas_de_empregados"]["limites"]
    assert [(l["abaixo_de"], l["faixa"]) for l in limites] == list(ctx.modulo.FAIXAS_DE_EMPREGADOS)
    casos = ((1, "LT_70"), (69, "LT_70"), (70, "70_149"), (149, "70_149"), (150, "150_299"),
             (299, "150_299"), (300, "300_499"), (499, "300_499"), (500, "500_699"),
             (699, "500_699"), (700, "700_1000"), (999, "700_1000"), (1000, "GT_1000"),
             (5000, "GT_1000"))
    for numero, esperado in casos:
        obtido = ctx.modulo.faixa_de_empregados(numero)
        assert obtido == esperado, "%s -> %s (esperado %s)" % (numero, obtido, esperado)
    assert ctx.modulo.faixa_de_empregados(None) == "UNKNOWN"


@item("exemplos-da-fonte-sao-validos-e-cobrem-os-tipos")
def _(ctx):
    tipos = set()
    linhas = [l for l in EXEMPLO_FONTE.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(linhas) >= 4
    for linha in linhas:
        validacao = ctx.modulo.validar_pedido(json.loads(linha), ctx.fortes, ctx.contrato,
                                              ctx.agente.identidade)
        assert not validacao["problemas"], "exemplo invalido: %s -> %s" % (linha[:60],
                                                                          validacao["problemas"])
        tipos.add(validacao["tipo"])
    assert tipos == set(ctx.modulo.TIPOS_DE_PESQUISA), tipos


@item("artefatos-do-card-estao-no-gate-de-estrutura")
def _(ctx):
    gate = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    for artefato in ("hermes/agents/research/research.py",
                     "hermes/agents/research/agente-research-v1.json",
                     "hermes/agents/research/exemplos/pesquisas-exemplo.jsonl",
                     "scripts/agentes/verificar_agente_research.py",
                     "scripts/agentes/teste_research_aceite.sh",
                     "docs/architecture/agente-research-v1.md",
                     "docs/runbooks/agente-research.md"):
        assert artefato in gate, "ausente do gate de estrutura: %s" % artefato


def _plano(texto: str) -> str:
    """Texto sem acento e em minusculas: a conferencia do doc nao pode depender de acentuacao."""
    return "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c)) \
        .lower()


@item("doc-e-runbook-declararam-as-lacunas-e-as-regras-da-v1")
def _(ctx):
    doc = _plano(DOC_ARQUITETURA.read_text(encoding="utf-8"))
    runbook = _plano(RUNBOOK.read_text(encoding="utf-8"))
    for texto in (doc, runbook):
        assert "tre-w4-e02-t01" in texto
        assert "aceite_research_001" in texto
        assert "research_runs" in texto
    for tabela in ("organizations", "sync_events", "human_approvals"):
        assert tabela in doc, "o doc de arquitetura nao cita a tabela %s" % tabela
    for tabela in ("sync_events", "human_approvals", "agent_runs"):
        assert tabela in runbook, "o runbook nao cita a tabela %s" % tabela
    for promessa in ("nao sobrescreve", "nunca e sobrescrita", "coalesce"):
        assert promessa in doc, "o doc de arquitetura nao declara: %s" % promessa
    assert "sem llm" in doc
    assert "prod" in doc and "recusado" in doc
    assert "desfazer" in runbook and "confirmo" in runbook
    assert "rollback" in runbook


# ---------------------------------------------------------------------------------------
# 2. Identidade e validacao do pedido
# ---------------------------------------------------------------------------------------
@item("identidade-forte-na-prioridade-do-contrato")
def _(ctx):
    validos = ctx.modulo.identificadores_validos(
        pedido(organizacao={"linkedin_url": "https://www.linkedin.com/company/acme",
                            "domain": "acme.com.br", "cnpj": CNPJ_A}),
        ctx.fortes, ctx.agente.identidade)
    assert [t for t, _ in validos] == ["cnpj", "domain", "linkedin_url"], validos
    assert validos[0][1] == "11222333000181"
    assert validos[1][1] == "acme.com.br"


@item("cnpj-com-digito-verificador-errado-e-descartado")
def _(ctx):
    validos = ctx.modulo.identificadores_validos(pedido(organizacao={"cnpj": "11.222.333/0001-00"}),
                                                 ctx.fortes, ctx.agente.identidade)
    assert validos == []


@item("forte-invalido-sozinho-recusa")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(pedido(organizacao={"cnpj": "11.222.333/0001-00"}),
                                          ctx.fortes, ctx.contrato, ctx.agente.identidade)
    assert validacao["problemas"] == ["IDENTIFICADOR_FORTE_INVALIDO"], validacao["problemas"]


@item("sem-forte-declarado-recusa")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(pedido(organizacao={"city": "Santos"}), ctx.fortes,
                                          ctx.contrato, ctx.agente.identidade)
    assert validacao["problemas"] == ["SEM_IDENTIFICADOR_FORTE"], validacao["problemas"]


@item("tipo-de-pesquisa-fora-do-vocabulario-recusa")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(pedido(tipo="ESPIONAGEM"), ctx.fortes, ctx.contrato,
                                          ctx.agente.identidade)
    assert validacao["problemas"] == ["TIPO_DE_PESQUISA_DESCONHECIDO"], validacao["problemas"]


@item("fonte-obrigatoria-e-do-vocabulario")
def _(ctx):
    sem_fonte = ctx.modulo.validar_pedido(pedido(fontes=[]), ctx.fortes, ctx.contrato,
                                          ctx.agente.identidade)
    assert sem_fonte["problemas"] == ["SEM_FONTE"], sem_fonte["problemas"]
    estranha = ctx.modulo.validar_pedido(pedido(fontes=[{"tipo": "PANFLETO"}]), ctx.fortes,
                                         ctx.contrato, ctx.agente.identidade)
    assert estranha["problemas"] == ["FONTE_DESCONHECIDA"], estranha["problemas"]
    normalizada = ctx.modulo.validar_pedido(pedido(fontes=[{"tipo": "web"}]), ctx.fortes,
                                            ctx.contrato, ctx.agente.identidade)
    assert normalizada["fontes"] == [{"tipo": "WEB", "url": None, "trecho": None}]
    assert not normalizada["problemas"]


@item("confianca-fora-da-faixa-nao-e-inventada")
def _(ctx):
    alta = ctx.modulo.validar_pedido(pedido(confianca=1.4), ctx.fortes, ctx.contrato,
                                     ctx.agente.identidade)
    assert alta["confianca"] is None
    assert any(d["motivo"] == "FORA_DA_FAIXA" for d in alta["descartados"]), alta["descartados"]
    ausente = ctx.modulo.validar_pedido(pedido(), ctx.fortes, ctx.contrato, ctx.agente.identidade)
    assert ausente["confianca"] is None


@item("identidade-nao-tem-segunda-copia-no-agente")
def _(ctx):
    """A regra de identidade e UMA: a pesquisa importa a do produtor (Scout), nao reimplementa."""
    codigo = ctx.codigo
    for assinatura in ("def cnpj_valido", "def normalizar_cnpj", "def normalizar_domain",
                       "def normalizar_linkedin", "def domain_valido", "def linkedin_valido"):
        assert assinatura not in codigo, "segunda copia da regra de identidade: %s" % assinatura
    scout = carregar_modulo(MODULO_SCOUT)
    assert Path(ctx.agente.identidade.__file__).resolve() == MODULO_SCOUT.resolve()
    for nome in ("cnpj_valido", "normalizar_cnpj", "normalizar_domain", "normalizar_linkedin",
                 "lit", "lit_json"):
        assert getattr(ctx.agente.identidade, nome).__code__.co_code == \
            getattr(scout, nome).__code__.co_code, "implementacao diferente de %s" % nome


# ---------------------------------------------------------------------------------------
# 3. Achados: o que entra, o que e derivado e o que e descartado
# ---------------------------------------------------------------------------------------
@item("achado-fora-do-tipo-do-pedido-e-descartado")
def _(ctx):
    achados, descartados = ctx.modulo.validar_achados(
        pedido(tipo="COMPANY_PROFILE", achados={"unit_count": 3, "city": "Recife"}),
        "COMPANY_PROFILE", ctx.contrato)
    assert "unit_count" not in achados
    assert achados == {"city": "Recife"}
    assert {"campo": "unit_count", "motivo": "COLUNA_FORA_DO_TIPO", "valor": 3} in descartados


@item("identificador-forte-achado-nao-e-escrito")
def _(ctx):
    achados, descartados = ctx.modulo.validar_achados(
        pedido(tipo="COMPANY_PROFILE", achados={"cnpj": CNPJ_A, "domain": "acme.com.br",
                                                "industry_name": "Metalurgia"}),
        "COMPANY_PROFILE", ctx.contrato)
    assert set(achados) == {"industry_name"}
    motivos = {d["campo"]: d["motivo"] for d in descartados}
    assert motivos["cnpj"] == "IDENTIFICADOR_FORTE_NAO_ESCRITO_PELA_PESQUISA"
    assert motivos["domain"] == "IDENTIFICADOR_FORTE_NAO_ESCRITO_PELA_PESQUISA"


@item("faixa-vinda-da-fonte-e-descartada-e-derivada-do-numero")
def _(ctx):
    achados, descartados = ctx.modulo.validar_achados(
        pedido(tipo="SIZE_AND_STRUCTURE", achados={"employee_count": 210,
                                                   "employee_band": "150_299"}),
        "SIZE_AND_STRUCTURE", ctx.contrato)
    assert achados["employee_count"] == 210
    assert achados["employee_band"] == "150_299"  # derivada do numero, nao copiada da fonte
    assert any(d["campo"] == "employee_band" and d["motivo"] == "DERIVADO_NAO_ACEITO"
               for d in descartados), descartados


@item("campo-nao-declarado-e-descartado")
def _(ctx):
    _, descartados = ctx.modulo.validar_achados(
        pedido(tipo="COMPANY_PROFILE", achados={"faturamento_secreto": 10, "city": "Osasco"}),
        "COMPANY_PROFILE", ctx.contrato)
    assert {"campo": "faturamento_secreto", "motivo": "CAMPO_NAO_DECLARADO", "valor": 10} \
        in descartados


@item("valores-invalidos-sao-descartados-com-motivo")
def _(ctx):
    casos = (
        ("employee_count", "nao informado", "SIZE_AND_STRUCTURE", "NAO_E_INTEIRO"),
        ("employee_count", 0, "SIZE_AND_STRUCTURE", "FORA_DA_FAIXA"),
        ("unit_count", -3, "SIZE_AND_STRUCTURE", "FORA_DA_FAIXA"),
        ("revenue_estimate", -1, "SIZE_AND_STRUCTURE", "FORA_DA_FAIXA"),
        ("country_code", "BRA", "COMPANY_PROFILE", "PAIS_INVALIDO"),
        ("website_url", "ftp://acme.com.br", "COMPANY_PROFILE", "URL_INVALIDA"),
        ("industry_name", "x" * 300, "COMPANY_PROFILE", "EXCEDE_O_LIMITE_DA_COLUNA"),
        ("city", "   ", "COMPANY_PROFILE", "VALOR_VAZIO"),
    )
    for campo, valor, tipo, motivo in casos:
        achados, descartados = ctx.modulo.validar_achados(pedido(tipo=tipo, achados={campo: valor}),
                                                          tipo, ctx.contrato)
        assert campo not in achados, "%s=%r foi aceito" % (campo, valor)
        assert any(d["motivo"] == motivo for d in descartados), \
            "%s=%r -> %s (esperado %s)" % (campo, valor, descartados, motivo)


@item("valores-validos-saem-normalizados")
def _(ctx):
    aceitos, descartados = ctx.modulo.validar_achados(
        pedido(tipo="COMPANY_PROFILE", achados={"country_code": "br", "industry_code": "  Q  ",
                                                "business_model": "B2B"}),
        "COMPANY_PROFILE", ctx.contrato)
    assert aceitos == {"country_code": "BR", "industry_code": "Q", "business_model": "B2B"}
    assert descartados == []


# ---------------------------------------------------------------------------------------
# 4. Decisao de veredito (pura, sem banco)
# ---------------------------------------------------------------------------------------
@item("veredito-recusa-organizacao-inexistente")
def _(ctx):
    assert ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], []) == \
        ("RECUSADA", "ORGANIZACAO_NAO_ENCONTRADA")


@item("veredito-de-ambiguidade-vai-para-revisao")
def _(ctx):
    assert ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], [ORG_A, ORG_B]) == \
        ("REVISAO_IDENTIDADE", "CONFLITO_DE_IDENTIDADE_FORTE")


@item("veredito-de-um-casamento-e-pesquisa")
def _(ctx):
    assert ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], [ORG_A]) == \
        ("PESQUISADA", None)


@item("veredito-de-problema-de-forma-vence-a-identidade")
def _(ctx):
    assert ctx.modulo.decidir_veredito(["FONTE_DESCONHECIDA"], [], []) == \
        ("RECUSADA", "FONTE_DESCONHECIDA")


# ---------------------------------------------------------------------------------------
# 5. SQL e guarda de escrita
# ---------------------------------------------------------------------------------------
@item("sql-de-consulta-so-le")
def _(ctx):
    sql = ctx.modulo.sql_consultar_organizacao([("cnpj", "11222333000181")])
    assert sql.strip().upper().startswith("SELECT")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", sql, re.I)
    assert "deleted_at IS NULL" in sql


@item("sql-de-ingestao-tem-o-claim-idempotente")
def _(ctx):
    sql = ctx.sql_ingestao()
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert sql.strip().startswith("BEGIN;")
    assert sql.strip().endswith("COMMIT;")
    assert ctx.modulo.MARCA_PESQUISADA in sql


@item("sql-de-ingestao-ancora-o-enriquecimento-no-run-desta-rodada")
def _(ctx):
    """O enriquecimento e o fechamento so valem se o research_run DESTA rodada existe."""
    sql = ctx.sql_ingestao()
    trechos = sql.split(";")
    up_org = [t for t in trechos if re.search(r"UPDATE \S*organizations", t)]
    assert len(up_org) == 1, "esperado 1 UPDATE de organizations, achei %d" % len(up_org)
    assert "EXISTS (SELECT 1 FROM %s WHERE id =" % ctx.modulo.TABELA_RESEARCH_RUNS in up_org[0]
    up_sync = [t for t in trechos if re.search(r"UPDATE \S*sync_events", t)]
    assert len(up_sync) == 1
    assert "EXISTS (SELECT 1 FROM %s WHERE id =" % ctx.modulo.TABELA_RESEARCH_RUNS in up_sync[0]
    assert "status = 'SUCCESS'" in up_sync[0]


@item("sql-de-ingestao-sem-enriquecimento-nao-toca-organizations")
def _(ctx):
    sql = ctx.sql_ingestao(enriquecidas=())
    assert not re.search(r"UPDATE \S*organizations", sql)


@item("sql-gerado-passa-na-propria-guarda")
def _(ctx):
    ctx.modulo.validar_sql(ctx.sql_ingestao(), modo_organizacoes=ctx.modulo.MODO_ENRIQUECIMENTO)
    ctx.modulo.validar_sql(
        ctx.modulo.sql_desfazer([{"research_run_id": "1", "organization_id": ORG_A,
                                  "sync_event_id": "2", "antes": {"city": None},
                                  "depois": {"city": "Recife"}}], "corr", "3"),
        modo_organizacoes=ctx.modulo.MODO_RESTAURACAO, permitir_remocao=True)


@item("sql-de-restauracao-devolve-o-tipo-da-coluna")
def _(ctx):
    assert ctx.modulo.lit_restauracao("employee_count", "420") == "420"
    assert ctx.modulo.lit_restauracao("revenue_estimate", "1200.5") == "1200.5"
    assert ctx.modulo.lit_restauracao("city", None) == "NULL"
    assert ctx.modulo.lit_restauracao("city", "Osasco") == "'Osasco'"


@item("guarda-recusa-ddl")
def _(ctx):
    for sql in ("CREATE TABLE x (id int);", "ALTER TABLE sales_intelligence.organizations ADD a int;",
                "DROP TABLE sales_intelligence.research_runs;"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("DDL passou na guarda: %s" % sql)


@item("guarda-recusa-escrita-em-tabela-nao-declarada")
def _(ctx):
    for sql in ("INSERT INTO sales_intelligence.contacts (id) VALUES ('1');",
                "UPDATE sales_intelligence.signals SET title = 'x';"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("escrita fora do declarado passou: %s" % sql)


@item("guarda-recusa-update-de-organizacao-sem-modo")
def _(ctx):
    try:
        ctx.modulo.validar_sql("UPDATE sales_intelligence.organizations SET city = 'X' WHERE id = '1';")
    except ctx.modulo.GuardaDeEscritaViolada:
        return
    raise AssertionError("UPDATE de organizations passou sem modo declarado")


@item("guarda-recusa-coluna-fora-do-enriquecimento")
def _(ctx):
    modos = ((ctx.modulo.MODO_ENRIQUECIMENTO,
              "UPDATE sales_intelligence.organizations SET status = 'PESQUISADO' WHERE id = '1';"),
             (ctx.modulo.MODO_ENRIQUECIMENTO,
              "UPDATE sales_intelligence.organizations SET cnpj = '11222333000181' WHERE id = '1';"),
             (ctx.modulo.MODO_RESTAURACAO,
              "UPDATE sales_intelligence.organizations SET data_quality_score = 90 WHERE id = '1';"))
    for modo, sql in modos:
        try:
            ctx.modulo.validar_sql(sql, modo_organizacoes=modo)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("coluna proibida passou (%s): %s" % (modo, sql))


@item("guarda-recusa-enriquecimento-sem-coalesce")
def _(ctx):
    sql = ("UPDATE sales_intelligence.organizations SET city = 'Recife' WHERE id = '1';")
    try:
        ctx.modulo.validar_sql(sql, modo_organizacoes=ctx.modulo.MODO_ENRIQUECIMENTO)
    except ctx.modulo.GuardaDeEscritaViolada:
        return
    raise AssertionError("enriquecimento por atribuicao direta passou (sobrescreveria)")


@item("guarda-aceita-enriquecimento-na-forma-declarada")
def _(ctx):
    sql = ("UPDATE sales_intelligence.organizations SET "
           "city = COALESCE(NULLIF(city, ''), 'Recife'), "
           "employee_count = COALESCE(employee_count, 85), updated_at = now() WHERE id = '1';")
    ctx.modulo.validar_sql(sql, modo_organizacoes=ctx.modulo.MODO_ENRIQUECIMENTO)


@item("guarda-recusa-delete-em-organizacao")
def _(ctx):
    try:
        ctx.modulo.validar_sql("DELETE FROM sales_intelligence.organizations WHERE id = '1';",
                               modo_organizacoes=ctx.modulo.MODO_RESTAURACAO,
                               permitir_remocao=True)
    except ctx.modulo.GuardaDeEscritaViolada:
        return
    raise AssertionError("DELETE em organizations passou")


@item("guarda-recusa-delete-fora-do-desfazer")
def _(ctx):
    try:
        ctx.modulo.validar_sql("DELETE FROM sales_intelligence.research_runs WHERE id = '1';")
    except ctx.modulo.GuardaDeEscritaViolada:
        return
    raise AssertionError("DELETE de research_runs passou fora do desfazer")


@item("guarda-nao-confunde-nome-de-empresa-com-ddl")
def _(ctx):
    sql = ("UPDATE sales_intelligence.organizations SET "
           "industry_name = COALESCE(NULLIF(industry_name, ''), 'Drop Solucoes Ltda'), "
           "city = COALESCE(NULLIF(city, ''), 'Create Tecnologia ME') WHERE id = '1';")
    ctx.modulo.validar_sql(sql, modo_organizacoes=ctx.modulo.MODO_ENRIQUECIMENTO)


@item("guarda-separa-itens-do-set-com-parenteses")
def _(ctx):
    itens = ctx.modulo._itens_do_set(
        "industry_code = COALESCE(NULLIF(industry_code, ''), 'Q'), city = COALESCE(NULLIF(city, ''), "
        "'Osasco'), employee_count = COALESCE(employee_count, 420), updated_at = now(),")
    assert len(itens) == 4, itens


# ---------------------------------------------------------------------------------------
# 6. Fluxo completo na porta de roteiro
# ---------------------------------------------------------------------------------------
@item("fluxo-pesquisa-escreve-sincronia-run-e-enriquecimento")
def _(ctx):
    porta = PortaRoteiro(rota=[(0, linha_organizacao(ORG_A), ""),
                               (0, ctx.modulo.MARCA_PESQUISADA + "\n", ""), (0, "", "")],
                         modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido(achados={"industry_name": "Metalurgia", "city": "Recife"}))
    assert resultado["veredito"] == "PESQUISADA", resultado
    assert resultado["organization_id"] == ORG_A
    assert resultado["research_run_id"] and resultado["status_agent_runs"] == "COMPLETED"
    assert resultado["colunas_enriquecidas"] == ["industry_name", "city"], resultado
    assert resultado["idempotency_key"].startswith("research:org:%s:COMPANY_PROFILE:" % ORG_A)
    assert len(porta.chamadas) == 3
    assert porta.modos[1] == ctx.modulo.MODO_ENRIQUECIMENTO
    assert "INSERT INTO sales_intelligence.agent_runs" in porta.chamadas[2]


@item("fluxo-nao-sobrescreve-coluna-ja-preenchida")
def _(ctx):
    porta = PortaRoteiro(rota=[(0, linha_organizacao(ORG_A, industry_name="Metalurgia"), ""),
                               (0, ctx.modulo.MARCA_PESQUISADA + "\n", ""), (0, "", "")],
                         modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido(achados={"industry_name": "Industria Metalurgica",
                                                 "city": "Recife"}))
    assert resultado["colunas_enriquecidas"] == ["city"], resultado
    assert resultado["colunas_preservadas"] == ["industry_name"]
    sql = porta.chamadas[1]
    assert "SET city = COALESCE(NULLIF(city, ''), 'Recife'), updated_at = now()" in sql
    assert "industry_name" not in sql.split("SET", 1)[1]


@item("fluxo-replay-vira-ja-pesquisado")
def _(ctx):
    """A chave ja existia: nada e inserido, nada e enriquecido e o veredito muda."""
    porta = PortaRoteiro(rota=[(0, linha_organizacao(ORG_A), ""), (0, "", ""), (0, "", "")],
                         modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido(achados={"city": "Recife"}))
    assert resultado["veredito"] == "JA_PESQUISADO", resultado
    assert "IDEMPOTENCIA_REPLAY" in resultado["motivos"]
    assert resultado["research_run_id"] is None
    assert resultado["status_agent_runs"] == "COMPLETED"


@item("fluxo-ambiguidade-registra-a-fila-humana")
def _(ctx):
    duplas = linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B) + "\n"
    porta = PortaRoteiro(rota=[(0, duplas, ""), (0, "", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido())
    assert resultado["veredito"] == "REVISAO_IDENTIDADE", resultado
    assert resultado["motivos"] == ["CONFLITO_DE_IDENTIDADE_FORTE"]
    assert resultado["human_approval_id"]
    assert "RESEARCH_IDENTITY_REVIEW" in porta.chamadas[1]
    assert resultado["status_agent_runs"] == "REVIEW_REQUIRED"


@item("fluxo-fila-humana-que-nao-registra-vira-erro")
def _(ctx):
    porta = PortaRoteiro(rota=[(0, linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B), ""),
                               (1, "", "permission denied"), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido())
    assert resultado["veredito"] == "ERRO", resultado
    assert "fila humana nao registrada" in resultado["motivos"][0]
    assert resultado["status_agent_runs"] == "FAILED"


@item("fluxo-recusa-nao-escreve-alem-da-auditoria")
def _(ctx):
    porta = PortaRoteiro(rota=[(0, "", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido(organizacao={"cnpj": "11.222.333/0001-00"}))
    assert resultado["veredito"] == "RECUSADA", resultado
    assert len(porta.chamadas) == 1, "recusa sem identidade valida nao deve consultar o banco"
    assert "INSERT INTO sales_intelligence.agent_runs" in porta.chamadas[0]


@item("fluxo-organizacao-inexistente-recusa-sem-escrever")
def _(ctx):
    porta = PortaRoteiro(rota=[(0, "", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido())
    assert resultado["veredito"] == "RECUSADA", resultado
    assert resultado["motivos"] == ["ORGANIZACAO_NAO_ENCONTRADA"]
    assert len(porta.chamadas) == 2
    assert "INSERT INTO sales_intelligence.research_runs" not in porta.chamadas[1]


@item("fluxo-auditoria-que-nao-registra-vira-erro")
def _(ctx):
    porta = PortaRoteiro(rota=[(0, linha_organizacao(ORG_A), ""),
                               (0, ctx.modulo.MARCA_PESQUISADA + "\n", ""),
                               (1, "", "sem permissao de escrita")], modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido(achados={"city": "Recife"}))
    assert resultado["veredito"] == "ERRO", resultado
    assert resultado["auditoria_registrada"] is False
    assert "AUDITORIA_NAO_REGISTRADA" in resultado["motivos"][-1]


@item("fluxo-falha-de-porta-vira-erro-com-status-failed")
def _(ctx):
    porta = PortaRoteiro(rota=[(1, "", "connection refused"), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.processar(pedido())
    assert resultado["veredito"] == "ERRO", resultado
    assert resultado["status_agent_runs"] == "FAILED"
    assert resultado["erro"]["tipo"] == "PortaIndisponivel"


@item("planejar-nao-toca-a-porta")
def _(ctx):
    agente = ctx.modulo.Research(raiz=ctx.raiz)  # porta ausente: qualquer uso RECUSA
    relatorio = agente.rodar([pedido(), pedido(tipo="NAO_EXISTE")], planejar=True)
    assert relatorio["por_veredito"] == {"PLANEJADO_PESQUISAR": 1, "PLANEJADO_RECUSAR": 1}
    assert relatorio["resultados"][0]["colunas_do_tipo"]


@item("desfazer-e-dry-run-por-padrao")
def _(ctx):
    linha = json.dumps({"research_run_id": "11111111-0000-4000-8000-000000000001",
                        "organization_id": ORG_A, "sync_event_id": "22222222-0000-4000-8000-000000000001",
                        "antes": {"city": None}, "depois": {"city": "Recife"}})
    porta = PortaRoteiro(rota=[(0, linha, "")], modulo=ctx.modulo)
    agente = ctx.modulo.Research(porta=porta, raiz=ctx.raiz)
    resultado = agente.desfazer("corr")
    assert resultado["dry_run"] is True and resultado["restauradas"] == 0
    assert len(porta.chamadas) == 1
    porta2 = PortaRoteiro(rota=[(0, linha, ""), (0, "", "")], modulo=ctx.modulo)
    agente2 = ctx.modulo.Research(porta=porta2, raiz=ctx.raiz)
    resultado2 = agente2.desfazer("corr", confirmo=True)
    assert resultado2["dry_run"] is False and resultado2["apagadas"] == 1
    assert porta2.modos[1] == ctx.modulo.MODO_RESTAURACAO
    sql = porta2.chamadas[1]
    assert "DELETE FROM sales_intelligence.research_runs" in sql
    assert "DELETE FROM sales_intelligence.organizations" not in sql
    assert "operation, " and "'ROLLBACK'" in sql


# ---------------------------------------------------------------------------------------
# 7. Guardrails de ambiente, JEV e ausencia de rede
# ---------------------------------------------------------------------------------------
@item("ambiente-recusado-por-desenho")
def _(ctx):
    for ambiente, excecao in (("prod", True), ("", True), (None, True), ("staging", True),
                              ("dev", False), ("homolog", False)):
        agente = ctx.modulo.Research(porta=PortaRoteiro(rota=[]), raiz=ctx.raiz,
                                     ambiente=ambiente)
        try:
            agente.conferir_ambiente()
        except ctx.modulo.RecusaDeAmbiente:
            assert excecao, "ambiente %r recusado indevidamente" % ambiente
            continue
        assert not excecao, "ambiente %r deveria ser recusado" % ambiente


@item("gate-do-jev-e-fail-closed")
def _(ctx):
    bom = {"decision_id": "dec-1", "lane": "high", "outcome": "PASS"}
    assert ctx.modulo.chamar_llm("prompt", recibo=bom)["executado"] is False
    for ruim in (None, {}, {"decision_id": "d", "lane": "high"},
                 {"decision_id": "d", "lane": "turbo", "outcome": "PASS"},
                 {"decision_id": "d", "lane": "high", "outcome": "ESCALATE"},
                 {"decision_id": "d", "lane": "high", "outcome": "BLOCK"}):
        try:
            ctx.modulo.chamar_llm("prompt", recibo=ruim)
        except ctx.modulo.ReciboJEVInvalido:
            continue
        raise AssertionError("recibo %r passou no gate" % (ruim,))


@item("agente-nao-faz-rede-nem-llm")
def _(ctx):
    codigo = ctx.codigo
    for proibido in ("import requests", "import urllib", "import http", "import smtplib",
                     "import socket", "urlopen", "subprocess.*curl"):
        assert proibido not in codigo, "o agente carrega acesso de rede: %s" % proibido
    assert "executado\": False" in codigo or "executado" in codigo


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
        sys.stdout = open("/dev/null", "w", encoding="utf-8")
        _, _, resultados = executar_suite(modulo, raiz)
    finally:
        sys.stdout.close()
        sys.stdout = suprimido
    return resultados


MUTACOES = [
        ("sem-idempotencia",
     '        "  ON CONFLICT (idempotency_key) DO NOTHING\\n"\n',
     '        "\\n"\n',
     ["sql-de-ingestao-tem-o-claim-idempotente"]),
        ("fechamento-sem-ancora-no-run",
     '        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {runs} WHERE id = {runid})\\n"\n',
     '        "WHERE idempotency_key = {chave}\\n"\n',
     ["sql-de-ingestao-ancora-o-enriquecimento-no-run-desta-rodada"]),
    ("enriquecimento-sem-coalesce",
     '        return "%s = COALESCE(NULLIF(%s, \'\'), %s)" % (coluna, coluna, lit(valor))',
     '        return "%s = %s" % (coluna, lit(valor))',
     ["sql-gerado-passa-na-propria-guarda"]),
    ("cnpj-passa-a-ser-coluna-de-enriquecimento",
     'COLUNAS_ENRIQUECIMENTO = (\n    "industry_code",',
     'COLUNAS_ENRIQUECIMENTO = (\n    "cnpj",\n    "industry_code",',
     ["colunas-de-enriquecimento-nao-tocam-identidade-e-estagio"]),
    ("guarda-deixa-passar-ddl",
     "    if _DDL.search(codigo):",
     "    if False and _DDL.search(codigo):",
     ["guarda-recusa-ddl"]),
    ("guarda-le-o-literal-como-codigo",
     '    return _LITERAL.sub("\'\'", sql)',
     "    return sql",
     ["guarda-nao-confunde-nome-de-empresa-com-ddl"]),
    ("guarda-aceita-enriquecimento-sem-coalesce",
     "        padrao = re.compile(_EXPR_TEXTO.format(c=re.escape(coluna)))\n"
     "        if not padrao.match(expressao):",
     "        padrao = re.compile(_EXPR_TEXTO.format(c=re.escape(coluna)))\n"
     "        if False:",
     ["guarda-recusa-enriquecimento-sem-coalesce"]),
    ("guarda-aceita-delete-de-organizacao",
     '            if tabela == TABELA_ORGANIZACOES and operacao != "update":',
     "            if False:",
     ["guarda-recusa-delete-em-organizacao"]),
    ("coluna-fora-do-tipo-liberada",
     "        if campo not in do_tipo:",
     "        if False:",
     ["achado-fora-do-tipo-do-pedido-e-descartado"]),
    ("fila-humana-sem-conferir-o-rc",
     "                if rc_rev != 0:",
     "                if False:",
     ["fluxo-fila-humana-que-nao-registra-vira-erro"]),
    ("auditoria-sem-conferir-o-rc",
     "        if rc_run != 0:",
     "        if False:",
     ["fluxo-auditoria-que-nao-registra-vira-erro"]),
    ("raiz-por-profundidade-do-arquivo",
     "    for base in (Path(__file__).resolve().parent, Path.cwd()):\n"
     "        for pasta in (base,) + tuple(base.parents):\n"
     "            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():\n"
     "                return pasta\n"
     "    return Path.cwd()",
     "    return Path(__file__).resolve().parents[3]",
     ["agente-importa-de-diretorio-fora-da-arvore"]),
    ("forte-invalido-aceito",
     "        if valida(bruto):",
     "        if True:",
     ["cnpj-com-digito-verificador-errado-e-descartado"]),
    ("faixa-ignora-o-numero",
     "    for limite, faixa in FAIXAS_DE_EMPREGADOS:",
     "    for limite, faixa in ():",
     ["faixas-de-empregados-cobrem-o-vocabulario-do-contrato"]),
    ("segunda-copia-da-regra-de-identidade",
     'def faixa_de_empregados(quantidade):',
     'def cnpj_valido(valor):\n    return True\n\n\ndef faixa_de_empregados(quantidade):',
     ["identidade-nao-tem-segunda-copia-no-agente"]),
]


def aplicar_mutacao(mutacao, destino: Path):
    nome, alvo, substituto, itens_esperados = mutacao
    texto = CODIGO_PADRAO.read_text(encoding="utf-8")
    if texto.count(alvo) != 1:
        return None, "ancora da mutacao nao casa exatamente 1 vez (%d)" % texto.count(alvo)
    destino.write_text(texto.replace(alvo, substituto), encoding="utf-8")
    return itens_esperados, "aplicada"


def autoteste():
    print("\n=== AUTOTESTE (mutacoes em COPIA do research.py) ===")
    ok_total = falhas_total = 0
    temporario = Path(tempfile.mkdtemp(prefix="research-autoteste-"))
    try:
        for mutacao in MUTACOES:
            nome = mutacao[0]
            # A copia vive um nivel ABAIXO do tempdir: assim ela importa de qualquer TMPDIR
            # (a prova de que o agente importa de diretorio raso e item proprio da suite).
            copia = temporario / "mut" / ("research-%s.py" % nome)
            copia.parent.mkdir(parents=True, exist_ok=True)
            esperados, detalhe = aplicar_mutacao(mutacao, copia)
            if esperados is None:
                falhas_total += 1
                print("FALHOU mutacao %s -> %s (mutacao nao aplicada e buraco de verificacao)"
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
    p = argparse.ArgumentParser(description="Suite do agente Research v1 (TRE-W4-E02-T01)")
    p.add_argument("--codigo", default=str(CODIGO_PADRAO), help="caminho de outro research.py")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not ITENS:
        print("FALHOU a suite nao executou nenhum item")
        return 1
    modulo = carregar_modulo(Path(args.codigo))
    print("=== SUITE DO AGENTE RESEARCH v1 (%s) ===" % args.codigo)
    ok, falhas, _ = executar_suite(modulo)
    print("\nRESULTADO: RESEARCH_SUITE_%s (%d itens, %d falhas)"
          % ("OK" if falhas == 0 else "FALHOU", ok + falhas, falhas))
    sucesso = falhas == 0
    if args.autoteste:
        sucesso = autoteste() and sucesso
        print("RESULTADO FINAL: RESEARCH_%s" % ("OK" if sucesso else "FALHOU"))
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
