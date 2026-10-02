#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do AGENTE SIGNAL DETECTOR v1 (card TRE-W4-E03-T01) — um comando, um veredito.

    python3 scripts/agentes/verificar_agente_signal.py
    python3 scripts/agentes/verificar_agente_signal.py --autoteste
    python3 scripts/agentes/verificar_agente_signal.py --codigo <caminho de outro signal.py>

O que ela prova (SEM banco e SEM rede): o contrato do agente existe e nao divergiu do Data
Contract V1.0 (vocabulario de 18 tipos de sinal, tabela de categorias derivadas, vocabulario de
canais igual ao dos outros agentes da W4); a empresa e resolvida por identificador FORTE e a
deteccao NAO cria empresa nem escreve NENHUMA coluna de `organizations`; o sinal grava so as
colunas declaradas (nenhuma coluna de score/decaimento — isso e W5) e a guarda de escrita recusa
DDL, tabela fora da lista, escrita em `organizations`, sinal sem `organization_id`/`signal_type`,
coluna de score e `DELETE` fora do desfazer; a categoria e DERIVADA do tipo (a da fonte e
descartada); a idempotencia esta no SQL (nao na prosa); o gate do JEV e fail-closed; e o fluxo
completo (detectar, replay idempotente, revisao, recusar, auditoria, desfazer) se comporta como o
contrato do card — medido numa PORTA DE ROTEIRO (implementacao da porta declarada, nao dublê de
biblioteca).

AUTOTESTE (`--autoteste`): cada mutacao e aplicada a uma COPIA do arquivo apontado por `--codigo`
(o canonico, por padrao — as duas opcoes andam juntas de proposito: mutar o canonical enquanto se
testa outro arquivo e' prova contra alvo errado) e a suite tem de REPROVAR o item correspondente —
mutacao que passa em silencio e buraco de verificacao. Mutacao que nao se aplica (ancora de texto
mudou), que nao declara item nenhum ou que declara item INEXISTENTE na suite tambem reprova: e
buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = SIGNAL_SUITE_OK · 1 = SIGNAL_SUITE_FALHOU (o log aponta o item) ·
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
CODIGO_PADRAO = RAIZ / "hermes/agents/signal/signal.py"
CONTRATO_AGENTE = RAIZ / "hermes/agents/signal/agente-signal-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC_ARQUITETURA = RAIZ / "docs/architecture/agente-signal-v1.md"
RUNBOOK = RAIZ / "docs/runbooks/agente-signal.md"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO_FONTE = RAIZ / "hermes/agents/signal/exemplos/observacoes-exemplo.jsonl"
CONTRATO_AGENTE_SCOUT = RAIZ / "hermes/agents/scout/agente-scout-v1.json"
CONTRATO_AGENTE_RESEARCH = RAIZ / "hermes/agents/research/agente-research-v1.json"
MODULO_SCOUT = RAIZ / "hermes/agents/scout/scout.py"
ACEITE = RAIZ / "scripts/agentes/teste_signal_aceite.sh"

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "aaaaaaaa-0000-4000-8000-000000000002"
CNPJ_A = "11.222.333/0001-81"
CNPJ_B = "45.723.174/0001-10"
RUN_ID = "33333333-0000-4000-8000-000000000001"

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
        self.contrato_scout = json.loads(CONTRATO_AGENTE_SCOUT.read_text(encoding="utf-8"))
        self.contrato_research = json.loads(CONTRATO_AGENTE_RESEARCH.read_text(encoding="utf-8"))
        self.ddl = MIGRATION.read_text(encoding="utf-8")
        self.agente = modulo.Signal(porta=PortaRoteiro(rota=[]), raiz=raiz)
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

    def valores_do_sinal(self, **extra):
        valores = {"organization_id": ORG_A,
                   "signal_type": "HIRING", "signal_category": "EXPANSAO",
                   "title": "40 vagas abertas", "description": None, "source_type": "LINKEDIN",
                   "source_url": "https://exemplo.test/vagas", "event_date": "2026-09-28",
                   "detected_at": "2026-09-29T00:00:00+00:00", "confidence": 0.8,
                   "evidence": {"fontes": [{"tipo": "LINKEDIN", "url": None, "trecho": "t"}]},
                   "research_run_id": None}
        valores.update(extra)
        return valores

    def sql_ingestao(self):
        """O SQL de ingestao que o agente GERA (o alvo real da guarda e do aceite)."""
        return self.modulo.sql_ingerir(
            "11111111-0000-4000-8000-000000000001",
            "22222222-0000-4000-8000-000000000001",
            "signal:org:%s:HIRING:%s" % (ORG_A, "a" * 64),
            self.valores_do_sinal(), {"origem": "signal"})


class PortaRoteiro:
    """Porta de teste: responde o roteiro declarado, GUARDA o SQL e roda a guarda do agente.

    Ela implementa a porta do agente (mesma interface) — nao substitui o alvo por dublê de
    biblioteca, e o SQL passa pela mesma guarda que roda em producao.
    """

    def __init__(self, rota=None, modulo=None):
        self.rota = list(rota or [])
        self.chamadas = []
        self.remocoes = []
        self.modulo = modulo

    def executar(self, sql, permitir_remocao=False):
        if self.modulo is not None:
            self.modulo.validar_sql(sql, permitir_remocao=permitir_remocao)
        self.chamadas.append(sql)
        self.remocoes.append(permitir_remocao)
        if not self.rota:
            return 0, "", ""
        resposta = self.rota.pop(0)
        if callable(resposta):
            return resposta(sql)
        return resposta


def carregar_modulo(caminho):
    spec = importlib.util.spec_from_file_location("signal_sob_teste", str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def observacao(**campos):
    base = {"organizacao": {"cnpj": CNPJ_A}, "tipo": "HIRING",
            "titulo": "40 vagas de logistica abertas em 30 dias",
            "fontes": [{"tipo": "LINKEDIN", "url": "https://exemplo.test/vagas",
                        "trecho": "40 vagas abertas no ultimo mes"}],
            "data_do_evento": "2026-09-28", "confianca": 0.8}
    base.update(campos)
    return base


def linha_organizacao(org_id: str, **valores) -> str:
    registro = {"id": org_id, "status": valores.pop("status", "DISCOVERED"),
                "legal_name": valores.pop("legal_name", "Empresa Teste Ltda"),
                "trade_name": valores.pop("trade_name", "Empresa Teste")}
    return json.dumps(registro, ensure_ascii=False)


def texto_do_sql(chamadas) -> str:
    return "\n".join(chamadas)


def agente_com_roteiro(ctx, rota, **kwargs):
    porta = PortaRoteiro(rota=rota, modulo=ctx.modulo)
    agente = ctx.modulo.Signal(porta=porta, raiz=ctx.raiz,
                               **kwargs)
    return agente, porta


# ---------------------------------------------------------------------------------------
# 1. Contrato e artefatos
# ---------------------------------------------------------------------------------------
@item("agente-existe-e-importa")
def _(ctx):
    assert ctx.modulo.AGENTE == "signal"
    assert ctx.modulo.PAPEL == "signal_detection"
    assert ctx.modulo.VERSAO == ctx.contrato["versao"]


@item("raiz-vem-do-marcador-nao-da-profundidade")
def _(ctx):
    """A copia roda de qualquer cwd e de qualquer profundidade: a raiz sai do MARCADOR."""
    raso = Path(tempfile.mkdtemp(prefix="signal-raso-"))
    dentro = ctx.raiz / ("tmp-prova-raiz-%d" % os.getpid())
    antigo = Path.cwd()
    try:
        copia_rasa = raso / "mut" / "signal.py"
        copia_rasa.parent.mkdir(parents=True, exist_ok=True)
        copia_rasa.write_text(ctx.codigo, encoding="utf-8")
        copia_profunda = dentro / "n1" / "n2" / "n3" / "n4" / "signal.py"
        copia_profunda.parent.mkdir(parents=True, exist_ok=True)
        copia_profunda.write_text(ctx.codigo, encoding="utf-8")
        os.chdir(raso)
        modulo = carregar_modulo(copia_profunda)
        assert modulo.descobrir_raiz_padrao() == ctx.raiz, \
            "a raiz nao foi achada pelo marcador a 4 niveis: %s" % modulo.descobrir_raiz_padrao()
        agente = modulo.Signal(porta=PortaRoteiro(rota=[]))
        assert agente.raiz == ctx.raiz and agente.contrato["card"] == "TRE-W4-E03-T01"
        copia = carregar_modulo(copia_rasa)
        assert copia.descobrir_raiz_padrao() != ctx.raiz, \
            "copia fora da arvore nao pode herdar silenciosamente a raiz do repo"
        try:
            copia.Signal(porta=PortaRoteiro(rota=[]))
        except Exception as exc:  # noqa: BLE001 — qualquer recusa serve, desde que cite o contrato
            assert "agente-signal-v1.json" in str(exc), exc
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
    assert c["card"] == "TRE-W4-E03-T01"
    assert set(c["vereditos"]) == set(ctx.modulo.VEREDITOS)
    assert c["status_de_agent_runs"] == ctx.modulo.STATUS_AGENT_RUNS
    assert set(c["fontes"]) == set(ctx.modulo.FONTES)
    assert list(c["tipos_de_sinal"]) == list(ctx.modulo.TIPOS_DE_SINAL)
    assert c["categorias_por_tipo"] == ctx.modulo.CATEGORIAS_POR_TIPO
    assert list(c["escrita_sinal"]["colunas_escritas"]) == list(ctx.modulo.COLUNAS_DO_SINAL)
    assert list(c["escrita_sinal"]["colunas_proibidas"]) == list(ctx.modulo.COLUNAS_PROIBIDAS)
    assert c["escrita"]["tabelas_permitidas"] and \
        set(ctx.modulo.TABELAS_PERMITIDAS) == set(c["escrita"]["tabelas_permitidas"])


@item("identidade-forte-vem-do-contrato-de-dados")
def _(ctx):
    assert list(ctx.fortes) == list(ctx.contrato_dados["dedup"]["strong"])
    assert list(ctx.fortes) == list(ctx.contrato["identidade"]["fortes_por_prioridade"])


@item("vocabulario-de-tipos-de-sinal-e-o-do-contrato-de-dados")
def _(ctx):
    """Os 18 tipos vem de `vocabularies.signal_type` (doc 03 §7) — vocabulario FECHADO."""
    assert set(ctx.modulo.TIPOS_DE_SINAL) == set(
        ctx.contrato_dados["vocabularies"]["signal_type"])
    assert len(ctx.modulo.TIPOS_DE_SINAL) == 18
    assert set(ctx.modulo.TIPOS_DE_SINAL) == set(ctx.contrato["tipos_de_sinal"])


@item("categorias-cobrem-o-vocabulario-exatamente-uma-vez")
def _(ctx):
    """A categoria e derivada: a tabela tem de cobrir o vocabulario, uma categoria por tipo."""
    tabela = ctx.modulo.CATEGORIAS_POR_TIPO
    assert set(tabela) == set(ctx.modulo.TIPOS_DE_SINAL), \
        "tabela de categorias nao cobre o vocabulario: %s" % sorted(set(ctx.modulo.TIPOS_DE_SINAL)
                                                                   ^ set(tabela))
    assert all(tabela[t] for t in tabela), "categoria vazia na tabela derivada"
    for tipo in ctx.modulo.TIPOS_DE_SINAL:
        assert ctx.modulo.categoria_do_tipo(tipo) == tabela[tipo]
        assert ctx.modulo.categoria_do_tipo("TIPO_QUE_NAO_EXISTE") is None


@item("vocabulario-de-fontes-e-o-mesmo-dos-tres-agentes")
def _(ctx):
    """A fonte e o canal por onde o dado entrou (doc 02 §1): descoberta, pesquisa e sinal falam o
    mesmo vocabulario — canal novo em um agente so' e' divergencia esperando acontecer."""
    assert list(ctx.contrato["fontes"]) == list(ctx.contrato_scout["fontes"])
    assert list(ctx.modulo.FONTES) == list(ctx.contrato_research["fontes"])


@item("contrato-declara-que-nao-cria-organizacao-e-nao-avalia-score")
def _(ctx):
    identidade = ctx.contrato["identidade"]
    assert identidade["cria_organizacao"] is False
    assert identidade["escreve_em_organizations"] is False
    assert ctx.contrato["outbox"]["emite_evento"] is False
    assert ctx.contrato["lacunas_declaradas"]
    assert "W5" in ctx.contrato["escrita_sinal"]["motivo_das_proibidas"] or \
        "TRE-W5-E03-T01" in ctx.contrato["escrita_sinal"]["motivo_das_proibidas"]


@item("colunas-do-sinal-existem-no-ddl")
def _(ctx):
    colunas = ctx.colunas("sales_intelligence.signals")
    faltando = [c for c in ctx.modulo.COLUNAS_DO_SINAL if c not in colunas]
    assert not faltando, "colunas escritas fora do DDL: %s" % faltando


@item("colunas-proibidas-sao-de-score-e-estao-no-ddl")
def _(ctx):
    """Pontos/relevancia/decaimento/expiracao existem no DDL, mas sao do Buying Signal Score."""
    do_ddl = ctx.colunas("sales_intelligence.signals")
    for coluna in ctx.modulo.COLUNAS_PROIBIDAS:
        assert coluna in do_ddl, "coluna proibida inexistente no DDL: %s" % coluna
    assert not (set(ctx.modulo.COLUNAS_PROIBIDAS) & set(ctx.modulo.COLUNAS_DO_SINAL)), \
        "o detector nao pode escrever coluna de score/decaimento"
    assert set(ctx.modulo.COLUNAS_PROIBIDAS) == set(ctx.contrato["escrita_sinal"]["colunas_proibidas"])


@item("escrita-de-signals-casa-com-o-ddl")
def _(ctx):
    colunas, expressoes = ctx.modulo.montar_linha_sinal(
        "11111111-0000-4000-8000-000000000001", ctx.valores_do_sinal())
    assert len(colunas) == len(expressoes), "colunas x valores divergentes"
    do_ddl = ctx.colunas("sales_intelligence.signals")
    faltando = [c for c in colunas if c not in do_ddl]
    assert not faltando, "colunas do INSERT fora do DDL: %s" % faltando
    for obrigatoria in ("organization_id", "signal_type"):
        assert obrigatoria in colunas, "INSERT sem a coluna NOT NULL %s" % obrigatoria


@item("escrita-de-agent-runs-casa-com-o-ddl")
def _(ctx):
    sql = ctx.modulo.sql_registrar_execucao(
        "11111111-0000-4000-8000-000000000001", "corr", ORG_A, "COMPLETED", {}, {}, "t0", "t1")
    colunas = re.search(r"INSERT INTO \S+ \(([^)]*)\)", sql).group(1)
    colunas = [c.strip() for c in colunas.split(",")]
    do_ddl = ctx.colunas("sales_intelligence.agent_runs")
    faltando = [c for c in colunas if c not in do_ddl]
    assert not faltando, "colunas do INSERT de auditoria fora do DDL: %s" % faltando
    assert "agent_name" in colunas, "o INSERT de auditoria perdeu a coluna NOT NULL agent_name"
    expressoes = sql.split("VALUES", 1)[1].strip().rstrip(";")
    itens = _itens_de_topo(expressoes)
    assert len(itens) == len(colunas), \
        "colunas x valores divergentes no agent_runs: %d x %d" % (len(colunas), len(itens))


def _itens_de_topo(texto: str) -> list:
    """Separa a lista de valores por virgula de TOPO (virgula dentro de literal nao separa)."""
    itens, atual, dentro, i = [], [], False, 0
    while i < len(texto):
        char = texto[i]
        if char == "'":
            if dentro and i + 1 < len(texto) and texto[i + 1] == "'":
                atual.append("''")
                i += 2
                continue
            dentro = not dentro
            atual.append(char)
        elif char == "," and not dentro:
            itens.append("".join(atual).strip())
            atual = []
        else:
            atual.append(char)
        i += 1
    if atual:
        itens.append("".join(atual).strip())
    return [x for x in itens if x]


@item("escrita-de-human-approvals-casa-com-o-ddl")
def _(ctx):
    sql = ctx.modulo.sql_pedir_revisao(
        "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002",
        "signal:revisao:x", observacao(), "MOTIVO", [], "corr")
    bloco = re.search(r"INSERT INTO \S*human_approvals\s*\(([^)]*)\)\s*SELECT(.*?)RETURNING",
                      sql, re.S)
    assert bloco, "a requisicao da fila humana nao esta no SQL gerado"
    colunas = [c.strip() for c in bloco.group(1).split(",")]
    faltando = [c for c in colunas if c not in ctx.colunas("sales_intelligence.human_approvals")]
    assert not faltando, "colunas fora do DDL de human_approvals: %s" % faltando
    assert "proposed_action" in colunas and "requested_by" in colunas
    valores = _itens_de_topo(bloco.group(2).split("FROM claim")[0].strip())
    assert len(valores) == len(colunas), "colunas x valores divergentes: %d x %d" % (
        len(colunas), len(valores))
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql, \
        "a fila humana precisa do mesmo claim idempotente do sinal"
    assert ctx.modulo.MARCA_REVISAO in sql, "o fechamento precisa provar a linha da fila"


@item("escrita-de-sync-events-casa-com-o-ddl")
def _(ctx):
    do_ddl = ctx.colunas("sales_intelligence.sync_events")
    desfazer = ctx.modulo.sql_desfazer(
        [{"signal_id": "1", "organization_id": ORG_A, "sync_event_id": "2"}], "corr", "3")
    for sql in (ctx.sql_ingestao(), desfazer):
        for bloco in re.findall(r"INSERT INTO %s \(([^)]*)\)" % ctx.modulo.TABELA_SYNC_EVENTS,
                                sql):
            colunas = [c.strip() for c in bloco.split(",")]
            assert not [c for c in colunas if c not in do_ddl], colunas


@item("source-of-truth-do-contrato-manda-o-sinal-para-o-postgresql")
def _(ctx):
    contrato = ctx.contrato_dados
    assert contrato["source_of_truth"]["sinais"] == "PostgreSQL"
    assert {"name": "signals", "purpose": "sinal de mudanca/demanda"} in contrato["tables"]
    for indice in ("signals(organization_id)", "signals(signal_type)", "signals(detected_at DESC)"):
        assert indice in contrato["indexes"], "indice declarado ausente: %s" % indice


@item("exemplos-da-fonte-sao-validos-e-cobrem-varios-tipos")
def _(ctx):
    tipos = set()
    linhas = [l for l in EXEMPLO_FONTE.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(linhas) >= 4, "a fonte de exemplo precisa de pelo menos 4 observacoes"
    for linha in linhas:
        validacao = ctx.modulo.validar_observacao(json.loads(linha), ctx.fortes,
                                                  ctx.agente.identidade)
        assert not validacao["problemas"], "exemplo invalido: %s -> %s" % (linha[:60],
                                                                          validacao["problemas"])
        assert not validacao["descartados"], "exemplo com descarte: %s" % validacao["descartados"]
        tipos.add(validacao["tipo"])
    assert len(tipos) >= 4, "os exemplos nao cobrem tipos suficientes: %s" % sorted(tipos)


@item("artefatos-do-card-estao-no-gate-de-estrutura")
def _(ctx):
    gate = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    for artefato in ("hermes/agents/signal/signal.py",
                     "hermes/agents/signal/agente-signal-v1.json",
                     "hermes/agents/signal/exemplos/observacoes-exemplo.jsonl",
                     "scripts/agentes/verificar_agente_signal.py",
                     "scripts/agentes/teste_signal_aceite.sh",
                     "docs/architecture/agente-signal-v1.md",
                     "docs/runbooks/agente-signal.md"):
        assert artefato in gate, "ausente do gate de estrutura: %s" % artefato
    assert "teste_signal_aceite.sh" in gate


def _plano(texto: str) -> str:
    """Texto sem acento e em minusculas: a conferencia do doc nao pode depender de acentuacao."""
    return "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c)) \
        .lower()


@item("doc-e-runbook-declararam-as-lacunas-e-as-regras-da-v1")
def _(ctx):
    doc = _plano(DOC_ARQUITETURA.read_text(encoding="utf-8"))
    runbook = _plano(RUNBOOK.read_text(encoding="utf-8"))
    for texto in (doc, runbook):
        assert "tre-w4-e03-t01" in texto
        assert "aceite_signal_001" in texto
        assert "signals" in texto
    for tabela in ("sync_events", "human_approvals", "organizations"):
        assert tabela in doc, "o doc de arquitetura nao cita a tabela %s" % tabela
    for promessa in ("nao escreve", "buying signal score", "derivada"):
        assert promessa in doc, "o doc de arquitetura nao declara: %s" % promessa
    assert "w5" in doc, "o doc nao declara que o score e do W5"
    assert "sem llm" in doc
    assert "prod" in doc and "recusado" in doc
    assert "desfazer" in runbook and "confirmo" in runbook
    assert "rollback" in runbook


# ---------------------------------------------------------------------------------------
# 2. Identidade e validacao da observacao
# ---------------------------------------------------------------------------------------
@item("identidade-forte-na-prioridade-do-contrato")
def _(ctx):
    validos = ctx.modulo.identificadores_validos(
        observacao(organizacao={"linkedin_url": "https://www.linkedin.com/company/acme",
                                "domain": "acme.com.br", "cnpj": CNPJ_A}),
        ctx.fortes, ctx.agente.identidade)
    assert [t for t, _ in validos] == ["cnpj", "domain", "linkedin_url"], validos
    assert validos[0][1] == "11222333000181"
    assert validos[1][1] == "acme.com.br"


@item("cnpj-com-digito-verificador-errado-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_observacao(observacao(organizacao={"cnpj": "11.222.333/0001-00"}),
                                              ctx.fortes, ctx.agente.identidade)
    assert validacao["validos"] == [], validacao["validos"]
    assert "IDENTIFICADOR_FORTE_INVALIDO" in validacao["problemas"], validacao["problemas"]


@item("sem-identificador-forte-e-recusado")
def _(ctx):
    validacao = ctx.modulo.validar_observacao(observacao(organizacao={"city": "Santos"}),
                                              ctx.fortes, ctx.agente.identidade)
    assert validacao["declarados"] == []
    assert "SEM_IDENTIFICADOR_FORTE" in validacao["problemas"], validacao["problemas"]
    veredito, motivo = ctx.modulo.decidir_veredito(validacao["problemas"], validacao["validos"], [])
    assert veredito == ctx.modulo.RECUSADA and motivo == "SEM_IDENTIFICADOR_FORTE"


@item("tipo-de-sinal-e-do-vocabulario-fechado")
def _(ctx):
    vazio = ctx.modulo.validar_observacao(observacao(tipo=""), ctx.fortes, ctx.agente.identidade)
    assert "SEM_TIPO_DE_SINAL" in vazio["problemas"], vazio["problemas"]
    fora = ctx.modulo.validar_observacao(observacao(tipo="SINAL_INVENTADO"), ctx.fortes,
                                        ctx.agente.identidade)
    assert "TIPO_DE_SINAL_DESCONHECIDO" in fora["problemas"], fora["problemas"]
    minusculo = ctx.modulo.validar_observacao(observacao(tipo="hiring"), ctx.fortes,
                                              ctx.agente.identidade)
    assert minusculo["tipo"] == "HIRING" and not minusculo["problemas"], minusculo


@item("fonte-e-obrigatoria-e-do-vocabulario")
def _(ctx):
    casos = ((observacao(fontes=[]), "SEM_FONTE"),
             (observacao(fontes=[{"tipo": "PANFLETO", "trecho": "x"}]), "FONTE_DESCONHECIDA"),
             (observacao(fontes=["nao e objeto"]), "FONTE_ILEGIVEL"))
    for obs, esperado in casos:
        validacao = ctx.modulo.validar_observacao(obs, ctx.fortes, ctx.agente.identidade)
        assert esperado in validacao["problemas"], (esperado, validacao["problemas"])


@item("fonte-invalida-de-forma-e-recusada")
def _(ctx):
    for fonte, esperado in ((
            {"tipo": "WEB", "url": "ftp://exemplo.test/x"}, "URL_DE_FONTE_INVALIDA"),
            ({"tipo": "WEB", "trecho": "x" * 2001}, "TRECHO_ACIMA_DO_LIMITE")):
        validacao = ctx.modulo.validar_observacao(observacao(fontes=[fonte]), ctx.fortes,
                                                  ctx.agente.identidade)
        assert esperado in validacao["problemas"], (esperado, validacao["problemas"])
        assert validacao["fontes"] == [], validacao["fontes"]


@item("fonte-primaria-e-a-primeira-declarada")
def _(ctx):
    obs = observacao(fontes=[{"tipo": "EVENTOS", "trecho": "palestra"},
                             {"tipo": "LINKEDIN", "url": "https://exemplo.test/x",
                              "trecho": "post"}])
    validacao = ctx.modulo.validar_observacao(obs, ctx.fortes, ctx.agente.identidade)
    assert [f["tipo"] for f in validacao["fontes"]] == ["EVENTOS", "LINKEDIN"], validacao["fontes"]
    assert validacao["fontes"][0]["tipo"] == "EVENTOS"


@item("categoria-declarada-pela-fonte-e-descartada")
def _(ctx):
    validacao = ctx.modulo.validar_observacao(observacao(categoria="EXPANSAO"),
                                              ctx.fortes, ctx.agente.identidade)
    assert validacao["categoria"] == "EXPANSAO"   # derivada do tipo HIRING
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    assert motivos.get("categoria") == "DERIVADO_NAO_ACEITO", validacao["descartados"]


@item("campo-nao-declarado-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_observacao(
        observacao(buying_signal_points=90, relevance_score=1.0, id="xyz"), ctx.fortes,
        ctx.agente.identidade)
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    for campo in ("buying_signal_points", "relevance_score", "id"):
        assert motivos.get(campo) == "CAMPO_NAO_DECLARADO", validacao["descartados"]
    assert not validacao["problemas"], validacao["problemas"]


@item("data-invalida-e-descartada-e-data-valida-passa")
def _(ctx):
    for fora_do_formato in ("28/09/2026", "20260928"):
        invalida = ctx.modulo.validar_observacao(observacao(data_do_evento=fora_do_formato),
                                                 ctx.fortes, ctx.agente.identidade)
        assert invalida["data_do_evento"] is None, (fora_do_formato, invalida["data_do_evento"])
        assert {"campo": "data_do_evento", "motivo": "DATA_INVALIDA",
                "valor": fora_do_formato} in invalida["descartados"], invalida["descartados"]
    torta = ctx.modulo.validar_observacao(observacao(data_do_evento="2026-02-31"), ctx.fortes,
                                         ctx.agente.identidade)
    assert torta["data_do_evento"] is None, torta["data_do_evento"]
    for boa in ("2026-09-28", "2026-09-28T14:00:00Z", "2026-09-28 14:00:00+00:00"):
        validacao = ctx.modulo.validar_observacao(observacao(data_do_evento=boa), ctx.fortes,
                                                  ctx.agente.identidade)
        assert validacao["data_do_evento"] == boa, (boa, validacao["data_do_evento"])
    ausente = ctx.modulo.validar_observacao(observacao(data_do_evento=None), ctx.fortes,
                                            ctx.agente.identidade)
    assert ausente["data_do_evento"] is None


@item("confianca-fora-da-faixa-e-descartada-e-ausente-fica-nula")
def _(ctx):
    for ruim in (1.5, -0.2, "muito"):
        validacao = ctx.modulo.validar_observacao(observacao(confianca=ruim), ctx.fortes,
                                                 ctx.agente.identidade)
        assert validacao["confianca"] is None, (ruim, validacao["confianca"])
        assert any(d["campo"] == "confianca" for d in validacao["descartados"]), validacao
    sem = ctx.modulo.validar_observacao(observacao(confianca=None), ctx.fortes,
                                        ctx.agente.identidade)
    assert sem["confianca"] is None and not sem["descartados"], sem
    arredondada = ctx.modulo.validar_observacao(observacao(confianca=0.123456), ctx.fortes,
                                                ctx.agente.identidade)
    assert arredondada["confianca"] == 0.1235, arredondada["confianca"]


@item("titulo-e-descricao-acima-do-limite-sao-descartados")
def _(ctx):
    validacao = ctx.modulo.validar_observacao(
        observacao(titulo="t" * 501, descricao="d" * 8001), ctx.fortes, ctx.agente.identidade)
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    assert motivos.get("titulo") == "TITULO_ACIMA_DO_LIMITE", validacao["descartados"]
    assert motivos.get("descricao") == "DESCRICAO_ACIMA_DO_LIMITE", validacao["descartados"]
    assert validacao["titulo"] is None and validacao["descricao"] is None


@item("research-run-id-de-formato-invalido-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_observacao(observacao(research_run_id="nao-e-uuid"),
                                              ctx.fortes, ctx.agente.identidade)
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    assert motivos.get("research_run_id") == "RESEARCH_RUN_ID_INVALIDO", validacao["descartados"]
    assert validacao["research_run_id"] is None
    bom = ctx.modulo.validar_observacao(observacao(research_run_id=RUN_ID), ctx.fortes,
                                        ctx.agente.identidade)
    assert bom["research_run_id"] == RUN_ID and not bom["descartados"], bom


@item("observacao-ilegivel-e-recusada")
def _(ctx):
    for bruta in ("texto solto", 42, None):
        validacao = ctx.modulo.validar_observacao(bruta, ctx.fortes, ctx.agente.identidade)
        assert validacao["problemas"] == ["OBSERVACAO_ILEGIVEL"], (bruta, validacao["problemas"])
    veredito, motivo = ctx.modulo.decidir_veredito(["OBSERVACAO_ILEGIVEL"], [], [])
    assert veredito == ctx.modulo.RECUSADA and motivo == "OBSERVACAO_ILEGIVEL"


@item("identidade-nao-tem-segunda-copia-no-agente")
def _(ctx):
    """Normalizacao/validacao de CNPJ, dominio e LinkedIn: UMA implementacao (a do produtor).

    `lit`/`lit_json` existem no agente como ENCAMINHAMENTO ao produtor (o escape de literal e o
    mesmo objeto) — o que a suite proibe e' uma SEGUNDA implementacao da regra de identidade.
    """
    modulo = ctx.modulo
    identidade = modulo.carregar_identidade(ctx.raiz)
    for nome in ("cnpj_valido", "normalizar_cnpj", "domain_valido", "normalizar_domain",
                 "linkedin_valido", "normalizar_linkedin"):
        assert not hasattr(modulo, nome), "segunda implementacao de regra do produtor: %s" % nome
    codigo = ctx.codigo
    for proibido in ("def cnpj_valido", "def normalizar_cnpj", "def normalizar_domain",
                     "def normalizar_linkedin", "def domain_valido", "def linkedin_valido"):
        assert proibido not in codigo, "o agente reimplementou a regra de identidade: %s" % proibido
    assert modulo.lit("a'b") == identidade.lit("a'b")
    assert modulo._LITERAIS[0].__code__.co_code == identidade.lit.__code__.co_code, \
        "o escape de literal nao e o do produtor"
    assert modulo._LITERAIS[1].__code__.co_code == identidade.lit_json.__code__.co_code


# ---------------------------------------------------------------------------------------
# 3. Decisao de veredito e chave de idempotencia
# ---------------------------------------------------------------------------------------
@item("veredito-detecta-uma-unica-casada")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], [ORG_A])
    assert veredito == ctx.modulo.DETECTADO and motivo is None, (veredito, motivo)


@item("veredito-recusa-zero-casadas")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], [])
    assert veredito == ctx.modulo.RECUSADA and motivo == "ORGANIZACAO_NAO_ENCONTRADA"


@item("veredito-vai-para-revisao-com-duas-ou-mais-casadas")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")],
                                                   [ORG_A, ORG_B])
    assert veredito == ctx.modulo.REVISAO and motivo == "CONFLITO_DE_IDENTIDADE_FORTE"


@item("problema-de-entrada-vence-a-casada")
def _(ctx):
    """Observacao invalida nao segue nem quando a identidade casaria: recusa, nao aviso."""
    veredito, motivo = ctx.modulo.decidir_veredito(["TIPO_DE_SINAL_DESCONHECIDO"],
                                                   [("cnpj", "11222333000181")], [ORG_A])
    assert veredito == ctx.modulo.RECUSADA and motivo == "TIPO_DE_SINAL_DESCONHECIDO"


@item("hash-da-entrada-e-estavel-e-sensivel-ao-conteudo")
def _(ctx):
    validacao = ctx.modulo.validar_observacao(observacao(), ctx.fortes, ctx.agente.identidade)
    identidades = validacao["validos"]
    primeiro = ctx.modulo.hash_da_entrada(identidades, validacao)
    assert len(primeiro) == 64, primeiro
    outra_ordem = observacao()
    outro = ctx.modulo.validar_observacao(outra_ordem, ctx.fortes, ctx.agente.identidade)
    assert ctx.modulo.hash_da_entrada(outro["validos"], outro) == primeiro
    mudada = ctx.modulo.validar_observacao(observacao(titulo="outro titulo"), ctx.fortes,
                                           ctx.agente.identidade)
    assert ctx.modulo.hash_da_entrada(mudada["validos"], mudada) != primeiro, \
        "titulo diferente produziu a MESMA chave de idempotencia"
    outra_data = ctx.modulo.validar_observacao(observacao(data_do_evento="2026-09-27"), ctx.fortes,
                                               ctx.agente.identidade)
    assert ctx.modulo.hash_da_entrada(outra_data["validos"], outra_data) != primeiro
    outra_descricao = ctx.modulo.validar_observacao(observacao(descricao="descricao nova"),
                                                    ctx.fortes, ctx.agente.identidade)
    assert ctx.modulo.hash_da_entrada(outra_descricao["validos"], outra_descricao) != primeiro


@item("chave-de-idempotencia-tem-a-forma-declarada")
def _(ctx):
    chave = ctx.modulo.chave_idempotencia(ORG_A, "HIRING", "b" * 64)
    assert chave == "signal:org:%s:HIRING:%s" % (ORG_A, "b" * 64), chave
    assert chave.startswith("signal:org:")


@item("status-de-agent-runs-cobre-todos-os-vereditos")
def _(ctx):
    for veredito in ctx.modulo.VEREDITOS:
        assert veredito in ctx.modulo.STATUS_AGENT_RUNS, veredito
        assert veredito in ctx.contrato["vereditos"], veredito


@item("planejar-nao-toca-a-porta")
def _(ctx):
    porta = PortaRoteiro(rota=[], modulo=ctx.modulo)
    agente = ctx.modulo.Signal(porta=porta, raiz=ctx.raiz)
    plano = agente.planejar(observacao())
    assert plano["veredito"] == ctx.modulo.PLANEJADO_DETECTAR, plano
    assert plano["categoria_derivada"] == "EXPANSAO", plano
    assert porta.chamadas == [], "o modo --planejar falou com o banco: %s" % porta.chamadas
    recusado = agente.planejar(observacao(tipo="INVENTADO"))
    assert recusado["veredito"] == ctx.modulo.PLANEJADO_RECUSAR, recusado


# ---------------------------------------------------------------------------------------
# 4. SQL e guarda de escrita
# ---------------------------------------------------------------------------------------
@item("sql-de-consulta-casa-identidade-por-forte")
def _(ctx):
    sql = ctx.modulo.sql_consultar_organizacao([("cnpj", "11222333000181")])
    assert "sales_intelligence.organizations" in sql
    assert "cnpj = '11222333000181'" in sql, sql
    assert "deleted_at IS NULL" in sql, "a consulta pode casar empresa apagada"
    multi = ctx.modulo.sql_consultar_organizacao([("cnpj", "11222333000181"),
                                                  ("domain", "acme.com.br")])
    assert "OR" in multi and "domain = 'acme.com.br'" in multi, multi


@item("sql-de-ingestao-tem-o-claim-idempotente")
def _(ctx):
    sql = ctx.sql_ingestao()
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql, sql
    assert "INSERT INTO %s" % ctx.modulo.TABELA_SYNC_EVENTS in sql
    assert "idempotency_key" in sql and "request_payload" in sql


@item("sql-de-ingestao-ancora-o-fechamento-no-sinal-desta-rodada")
def _(ctx):
    sql = ctx.sql_ingestao()
    assert "AND EXISTS (SELECT 1 FROM sales_intelligence.signals WHERE id = " in sql, sql
    assert "'SIGNAL_DETECTADO'" in sql, "o fechamento perdeu a marca da rodada"
    assert "INSERT INTO sales_intelligence.signals" in sql


@item("sql-de-ingestao-nao-escreve-em-organizations")
def _(ctx):
    sql = ctx.sql_ingestao() + ctx.modulo.sql_desfazer(
        [{"signal_id": "1", "organization_id": ORG_A, "sync_event_id": "2"}], "corr", "3")
    for operacao, padrao in ctx.modulo._ESCRITA:
        for tabela in padrao.findall(sql):
            assert tabela.lower() != ctx.modulo.TABELA_ORGANIZACOES, \
                "%s em organizations no SQL do agente" % operacao


@item("sql-gerado-passa-na-propria-guarda")
def _(ctx):
    ctx.modulo.validar_sql(ctx.sql_ingestao())
    ctx.modulo.validar_sql(ctx.modulo.sql_registrar_execucao(
        "11111111-0000-4000-8000-000000000001", "corr", ORG_A, "COMPLETED", {}, {}, "t0", "t1"))
    ctx.modulo.validar_sql(ctx.modulo.sql_pedir_revisao(
        "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002",
        "signal:revisao:x", observacao(), "M", [], "corr"))
    ctx.modulo.validar_sql(ctx.modulo.sql_desfazer(
        [{"signal_id": "1", "organization_id": ORG_A, "sync_event_id": "2"}], "corr", "3"),
        permitir_remocao=True)


@item("insert-de-sinal-declara-as-colunas-obrigatorias")
def _(ctx):
    colunas = ctx.modulo.colunas_do_insert(ctx.sql_ingestao(), ctx.modulo.TABELA_SINAIS)
    assert len(colunas) == 1, colunas
    assert sorted(colunas[0]) == sorted(ctx.modulo.COLUNAS_DO_SINAL), colunas[0]


@item("guarda-recusa-ddl")
def _(ctx):
    for sql in ("CREATE TABLE sales_intelligence.x (id int);",
                "ALTER TABLE sales_intelligence.signals ADD COLUMN y int;",
                "DROP TABLE sales_intelligence.signals;"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("a guarda aceitou DDL: %s" % sql)


@item("guarda-recusa-tabela-nao-declarada")
def _(ctx):
    for sql in ("INSERT INTO sales_intelligence.scores (id) VALUES ('x');",
                "INSERT INTO sales_intelligence.research_runs (id) VALUES ('x');",
                "UPDATE sales_intelligence.outbox_events SET status = 'PENDING';"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("a guarda aceitou escrita fora da lista: %s" % sql)


@item("guarda-recusa-escrita-em-organizations")
def _(ctx):
    """A deteccao nao escreve na empresa: a recusa nomeia a REGRA, nao so a lista de tabelas."""
    casos = ("UPDATE sales_intelligence.organizations SET city = 'Osasco' WHERE id = 'x';",
             "INSERT INTO sales_intelligence.organizations (id) VALUES ('x');",
             "DELETE FROM sales_intelligence.organizations WHERE id = 'x';")
    for sql in casos:
        try:
            ctx.modulo.validar_sql(sql, permitir_remocao=True)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert "organizations" in str(exc), exc
            assert "aditivo" in str(exc), \
                "a guarda recusou pelo motivo errado (regra de desenho ausente): %s" % exc
        else:
            raise AssertionError("a guarda aceitou escrita em organizations: %s" % sql)


@item("guarda-recusa-coluna-de-score-no-insert")
def _(ctx):
    for coluna in ctx.modulo.COLUNAS_PROIBIDAS:
        sql = ("INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, %s) "
               "VALUES ('x', 'y', 'HIRING', 10);" % coluna)
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert "score" in str(exc) or "decaimento" in str(exc), exc
        else:
            raise AssertionError("a guarda aceitou coluna de score no sinal: %s" % coluna)


@item("guarda-recusa-insert-de-sinal-sem-organizacao-ou-tipo")
def _(ctx):
    casos = ("INSERT INTO sales_intelligence.signals (id, signal_type) "
             "VALUES ('x', 'HIRING');",
             "INSERT INTO sales_intelligence.signals (id, organization_id) "
             "VALUES ('x', 'y');")
    for sql in casos:
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert "obrigatoria" in str(exc), exc
        else:
            raise AssertionError("a guarda aceitou sinal sem coluna NOT NULL: %s" % sql)


@item("guarda-recusa-delete-fora-do-desfazer")
def _(ctx):
    for sql in ("DELETE FROM sales_intelligence.signals WHERE id = 'x';",
                "DELETE FROM sales_intelligence.sync_events WHERE id = 'x';"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("a guarda aceitou DELETE fora do desfazer: %s" % sql)
    ctx.modulo.validar_sql("DELETE FROM sales_intelligence.signals WHERE id IN ('x');",
                           permitir_remocao=True)


@item("guarda-nao-confunde-prosa-com-ddl")
def _(ctx):
    """Titulo/descricao/trecho sao DADO, nao instrucao: prosa com 'drop' nao derruba sinal."""
    valores = ctx.valores_do_sinal(
        title="Drop Solucoes Ltda anuncia alter table no processo",
        evidence={"fontes": [{"tipo": "WEB", "url": None,
                              "trecho": "a empresa disse: drop table organizacoes"}]})
    sql = ctx.modulo.sql_ingerir("11111111-0000-4000-8000-000000000001",
                                 "22222222-0000-4000-8000-000000000001",
                                 "signal:org:%s:HIRING:%s" % (ORG_A, "a" * 64), valores,
                                 {"origem": "signal"})
    ctx.modulo.validar_sql(sql)   # nao pode levantar


@item("literal-escapa-apostrofo-pelo-produtor")
def _(ctx):
    identidade = ctx.agente.identidade
    assert ctx.modulo.lit("O'Brien") == identidade.lit("O'Brien")
    valores = ctx.valores_do_sinal(title="O'Brien Servicos", source_url=None)
    sql = ctx.modulo.sql_ingerir("11111111-0000-4000-8000-000000000001",
                                 "22222222-0000-4000-8000-000000000001",
                                 "signal:org:%s:HIRING:%s" % (ORG_A, "a" * 64), valores,
                                 {"origem": "signal"})
    assert "O''Brien" in sql, "apostrofo nao foi escapado"


@item("sync-event-do-sinal-aponta-o-sinal")
def _(ctx):
    """A programacao do `sync_events` e' do SINAL (entity_type/entity_id), nao da empresa.

    Defeito medido no aceite E2E: com `entity_type='organization'`/`entity_id=<empresa>` a consulta
    do desfazer (`e.entity_id = s.id`) nao achava nada e o desfazer nao apagava sinal nenhum.
    """
    sql = ctx.sql_ingestao()
    sinal_id = "11111111-0000-4000-8000-000000000001"
    assert "  VALUES ('22222222-0000-4000-8000-000000000001', 'signal', '%s', 'signal', " \
        "'postgresql', 'SIGNAL'," % sinal_id in sql, sql
    desfazer = ctx.modulo.sql_desfazer(
        [{"signal_id": "1", "organization_id": ORG_A, "sync_event_id": "2"}], "corr", "3")
    assert "VALUES ('3', 'signal', NULL, 'signal', 'postgresql', 'ROLLBACK'" in desfazer, desfazer


@item("desfazer-seleciona-so-o-que-a-rodada-criou")
def _(ctx):
    sql = ctx.modulo.sql_selecionar_da_rodada("dddddddd-0000-4000-8000-000000000001")
    assert "sales_intelligence.signals" in sql
    assert "(a.output ->> 'signal_id') = s.id::text" in sql, sql
    assert "e.entity_id = s.id" in sql, "o vinculo com o sync_event da chave nao esta na consulta"
    desfazer = ctx.modulo.sql_desfazer(
        [{"signal_id": "1", "organization_id": ORG_A, "sync_event_id": "2"}], "corr", "3")
    assert "DELETE FROM sales_intelligence.signals WHERE id IN ('1');" in desfazer, desfazer
    assert "DELETE FROM sales_intelligence.sync_events WHERE id IN ('2');" in desfazer
    assert "operation, source_version, idempotency_key, status" in desfazer.replace("\n", " ")
    assert "ROLLBACK" in desfazer and "signal:rollback:corr" in desfazer
    ctx.modulo.validar_sql(desfazer, permitir_remocao=True)


# ---------------------------------------------------------------------------------------
# 5. Fluxo completo na porta de roteiro
# ---------------------------------------------------------------------------------------
@item("fluxo-detecta-e-grava-o-sinal")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),          # SELECT organizations
        (0, "SIGNAL_DETECTADO", ""),                # ingestao do sinal
        (0, "", ""),                                # agent_runs
    ])
    resultado = agente.processar(observacao())
    assert resultado["veredito"] == ctx.modulo.DETECTADO, resultado
    assert resultado["organization_id"] == ORG_A
    assert resultado["signal_id"] and resultado["sync_event_id"], resultado
    assert resultado["idempotency_key"].startswith("signal:org:%s:HIRING:" % ORG_A)
    assert resultado["categoria"] == "EXPANSAO" and resultado["status_agent_runs"] == "COMPLETED"
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.signals" in sql
    assert "signal_category" in sql and "'EXPANSAO'" in sql
    assert "buying_signal_points" not in sql and "relevance_score" not in sql
    assert "INSERT INTO sales_intelligence.agent_runs" in sql
    assert "correlation_id" in sql


@item("fluxo-nao-escreve-em-organizations")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, "SIGNAL_DETECTADO", ""), (0, "", "")])
    agente.processar(observacao())
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.organizations" not in sql
    assert "UPDATE sales_intelligence.organizations" not in sql
    assert "DELETE FROM sales_intelligence.organizations" not in sql
    assert "SET " not in sql.split("UPDATE sales_intelligence.sync_events")[0], \
        "o detector nao tem SET em empresa: o sinal e aditivo"
    assert "sales_intelligence.organizations" in sql, "a consulta de identidade sumiu"


@item("fluxo-replay-nao-duplica-e-nao-marca-sucesso")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, "", ""),                                # claim ja existia: nada foi inserido
        (0, "", ""),
    ])
    resultado = agente.processar(observacao())
    assert resultado["veredito"] == ctx.modulo.JA_DETECTADO, resultado
    assert "IDEMPOTENCIA_REPLAY" in resultado["motivos"], resultado["motivos"]
    assert resultado.get("signal_id") is None, "replay nao pode devolver sinal novo"
    assert resultado["status_agent_runs"] == "COMPLETED"
    assert "INSERT INTO sales_intelligence.signals" in texto_do_sql(porta.chamadas)


@item("fluxo-revisao-registra-fila-humana")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B), ""),
        (0, ctx.modulo.MARCA_REVISAO, ""),           # claim + fila humana + fechamento
        (0, "", ""),                                # agent_runs
    ])
    obs = observacao(organizacao={"cnpj": CNPJ_A, "domain": "acme.com.br"})
    resultado = agente.processar(obs)
    assert resultado["veredito"] == ctx.modulo.REVISAO, resultado
    assert resultado["motivos"] == ["CONFLITO_DE_IDENTIDADE_FORTE"], resultado["motivos"]
    assert resultado["human_approval_id"], resultado
    assert resultado["revisao_registrada"] is True, resultado
    assert resultado["idempotency_key"].startswith("signal:revisao:"), resultado
    assert resultado["status_agent_runs"] == "REVIEW_REQUIRED"
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.human_approvals" in sql
    assert "'SIGNAL_IDENTITY_REVIEW'" in sql and "'PENDING'" in sql
    assert "INSERT INTO sales_intelligence.sync_events" in sql, \
        "a fila humana tambem passa pelo claim de idempotencia"
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert ctx.modulo.OPERACAO_REVISAO in sql, "a requisicao de revisao e' rastreavel"
    assert "INSERT INTO sales_intelligence.signals" not in sql, \
        "revisao nao pode gravar sinal"
    assert len(porta.chamadas) == 3, porta.chamadas


@item("fluxo-revisao-reapresentada-nao-duplica-pedido")
def _(ctx):
    """Mesma ambiguidade reapresentada: claim ja existente => nenhum pedido novo para o humano."""
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B), ""),
        (0, "", ""),                                 # claim volta vazio (0 linhas na fila)
        (0, "", ""),                                 # agent_runs
    ])
    resultado = agente.processar(observacao(organizacao={"cnpj": CNPJ_A, "domain": "acme.com.br"}))
    assert resultado["veredito"] == ctx.modulo.REVISAO, resultado
    assert resultado["revisao_registrada"] is False, resultado
    assert "IDEMPOTENCIA_REPLAY" in resultado["motivos"], resultado["motivos"]


@item("fluxo-fila-humana-que-nao-registra-vira-erro")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B), ""),
        (1, "", "permission denied"),               # human_approvals falhou
        (0, "", ""),
    ])
    resultado = agente.processar(observacao(organizacao={"cnpj": CNPJ_A,
                                                        "domain": "acme.com.br"}))
    assert resultado["veredito"] == ctx.modulo.ERRO, resultado
    assert any("fila humana" in m for m in resultado["motivos"]), resultado["motivos"]
    assert "INSERT INTO sales_intelligence.signals" not in texto_do_sql(porta.chamadas)


@item("fluxo-recusa-nao-escreve-e-audita")
def _(ctx):
    """Observacao invalida nao vira sinal nem quando a identidade casaria: recusa, nao aviso."""
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, "", "")])
    resultado = agente.processar(observacao(tipo="SINAL_INVENTADO"))
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    assert resultado["status_agent_runs"] == "REJECTED"
    assert resultado["motivos"] == ["TIPO_DE_SINAL_DESCONHECIDO"], resultado["motivos"]
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.signals" not in sql, "recusa escreveu sinal"
    assert "INSERT INTO sales_intelligence.agent_runs" in sql
    sem_forte, porta2 = agente_com_roteiro(ctx, [(0, "", "")])
    recusada = sem_forte.processar(observacao(organizacao={"city": "Santos"}))
    assert recusada["veredito"] == ctx.modulo.RECUSADA, recusada
    assert "SEM_IDENTIFICADOR_FORTE" in recusada["motivos"], recusada["motivos"]
    assert len(porta2.chamadas) == 1, "sem identidade nao ha o que consultar: %s" % porta2.chamadas
    assert "INSERT INTO sales_intelligence.agent_runs" in porta2.chamadas[0]


@item("fluxo-organizacao-inexistente-e-recusada")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [(0, "", ""), (0, "", "")])
    resultado = agente.processar(observacao())
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    assert "ORGANIZACAO_NAO_ENCONTRADA" in resultado["motivos"], resultado["motivos"]
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.signals" not in sql


@item("fluxo-vinculo-com-research-run-inexistente-e-descartado")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),          # organizacoes
        (0, "", ""),                                # research_runs: nao existe
        (0, "SIGNAL_DETECTADO", ""),                # ingestao
        (0, "", ""),                                # auditoria
    ])
    resultado = agente.processar(observacao(research_run_id=RUN_ID))
    assert resultado["veredito"] == ctx.modulo.DETECTADO, resultado
    assert resultado["research_run_id"] is None
    assert {"campo": "research_run_id", "motivo": "RESEARCH_RUN_NAO_ENCONTRADO",
            "valor": RUN_ID} in resultado["descartados"], resultado["descartados"]
    assert "SELECT id::text FROM sales_intelligence.research_runs" in porta.chamadas[1]
    assert "NULL" in porta.chamadas[2], "o vinculo quebrado nao pode ser escrito"


@item("fluxo-vinculo-com-research-run-real-e-escrito")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, RUN_ID, ""),                            # research_runs: existe
        (0, "SIGNAL_DETECTADO", ""),
        (0, "", ""),
    ])
    resultado = agente.processar(observacao(research_run_id=RUN_ID))
    assert resultado["veredito"] == ctx.modulo.DETECTADO, resultado
    assert resultado["research_run_id"] == RUN_ID
    assert not resultado["descartados"], resultado["descartados"]
    assert "'%s'" % RUN_ID in porta.chamadas[2], porta.chamadas[2]


@item("fluxo-auditoria-por-observacao-com-o-correlation-id-do-lote")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, "SIGNAL_DETECTADO", ""), (0, "", "")],
        correlation_id="dddddddd-0000-4000-8000-000000000001")
    resultado = agente.processar(observacao())
    assert resultado["agent_run_id"] and resultado["auditoria_registrada"] is True
    auditoria = porta.chamadas[-1]
    assert "INSERT INTO sales_intelligence.agent_runs" in auditoria
    assert "dddddddd-0000-4000-8000-000000000001" in auditoria
    assert "DETECTADO" in auditoria and "COMPLETED" in auditoria
    assert resultado["signal_id"] in auditoria, "a auditoria nao aponta o sinal (desfazer depende)"


@item("fluxo-auditoria-que-nao-registra-vira-erro")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, "SIGNAL_DETECTADO", ""),
        (1, "", "permission denied")])
    resultado = agente.processar(observacao())
    assert resultado["veredito"] == ctx.modulo.ERRO, resultado
    assert resultado["auditoria_registrada"] is False
    assert any("AUDITORIA_NAO_REGISTRADA" in m for m in resultado["motivos"]), resultado["motivos"]


@item("fluxo-porta-indisponivel-vira-erro-sem-sinal")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (1, "", "connection refused"), (0, "", "")])
    resultado = agente.processar(observacao())
    assert resultado["veredito"] == ctx.modulo.ERRO, resultado
    assert resultado["erro"]["tipo"] == "PortaIndisponivel", resultado.get("erro")
    assert "INSERT INTO sales_intelligence.signals" not in texto_do_sql(porta.chamadas)


@item("fluxo-desfazer-dry-run-e-confirmo")
def _(ctx):
    item_da_rodada = json.dumps({"signal_id": "11111111-0000-4000-8000-000000000001",
                                 "organization_id": ORG_A,
                                 "sync_event_id": "22222222-0000-4000-8000-000000000001"})
    agente, porta = agente_com_roteiro(ctx, [(0, item_da_rodada, "")])
    seco = agente.desfazer("dddddddd-0000-4000-8000-000000000001")
    assert seco["dry_run"] is True and seco["apagados"] == 0, seco
    assert seco["signals"] == ["11111111-0000-4000-8000-000000000001"], seco
    assert "DELETE" not in texto_do_sql(porta.chamadas), "dry-run apagou"
    agente2, porta2 = agente_com_roteiro(ctx, [(0, item_da_rodada, ""), (0, "", "")])
    aplicado = agente2.desfazer("dddddddd-0000-4000-8000-000000000001", confirmo=True)
    assert aplicado["dry_run"] is False and aplicado["apagados"] == 1, aplicado
    sql = texto_do_sql(porta2.chamadas)
    assert "DELETE FROM sales_intelligence.signals WHERE id IN" in sql
    assert "ROLLBACK" in sql
    assert porta2.remocoes[-1] is True, "o DELETE do desfazer saiu sem --confirmo declarado"
    sem_itens, porta3 = agente_com_roteiro(ctx, [(0, "", "")])
    vazio = sem_itens.desfazer("dddddddd-0000-4000-8000-000000000002", confirmo=True)
    assert vazio["apagados"] == 0 and len(porta3.chamadas) == 1, (vazio, porta3.chamadas)


@item("ambiente-recusado-por-desenho")
def _(ctx):
    for ambiente, recusa in (("prod", True), ("", True), (None, True), ("staging", True),
                             ("dev", False), ("homolog", False)):
        agente = ctx.modulo.Signal(porta=PortaRoteiro(rota=[]), raiz=ctx.raiz,
                                   ambiente=ambiente)
        try:
            agente.conferir_ambiente()
        except ctx.modulo.RecusaDeAmbiente:
            assert recusa, "ambiente %r recusado indevidamente" % ambiente
            continue
        assert not recusa, "ambiente %r deveria ser recusado" % ambiente


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
    for proibido in ("openai", "anthropic", "httpx"):
        assert proibido not in codigo, "o agente carrega cliente de LLM: %s" % proibido


# ---------------------------------------------------------------------------------------
# 6. Guarda da propria prova (quem testa o teste)
# ---------------------------------------------------------------------------------------
@item("autoteste-recusa-mutacao-sem-item-medido")
def _(ctx):
    """Dente com item esperado INEXISTENTE (ou sem item) tem de reprovar o autoteste.

    `resultados.get(nome_inexistente)` devolve None, entao o nome nunca entra em `nao_reprovados`
    e a mutacao passaria contada como detectada sem nada reprovar — dente mudo. Mutacao que nao
    declara item nenhum ou declara item que a suite nao tem e BURACO, nunca alivio.
    """
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
        sys.stdout = open("/dev/null", "w", encoding="utf-8")
        _, _, resultados = executar_suite(modulo, raiz)
    finally:
        sys.stdout.close()
        sys.stdout = suprimido
    return resultados


MUTACOES = [
    ("sem-idempotencia",
     '        "  ON CONFLICT (idempotency_key) DO NOTHING\\n"\n        "  RETURNING id\\n"\n'
     '        ")\\n"\n        "INSERT INTO {sinais} ({cols})\\n"',
     '        "  RETURNING id\\n"\n        ")\\n"\n'
     '        "INSERT INTO {sinais} ({cols})\\n"',
     ["sql-de-ingestao-tem-o-claim-idempotente"]),
    ("fechamento-sem-ancora-no-sinal",
     '        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {sinais} WHERE id = '
     '{sid})\\n"\n',
     '        "WHERE idempotency_key = {chave}\\n"\n',
     ["sql-de-ingestao-ancora-o-fechamento-no-sinal-desta-rodada"]),
    ("sync-event-aponta-a-empresa",
     "        \"  VALUES ({sevid}, 'signal', {sid}, 'signal', 'postgresql', {operacao}, \"",
     "        \"  VALUES ({sevid}, 'organization', {sid}, 'signal', 'postgresql', {operacao}, \"",
     ["sync-event-do-sinal-aponta-o-sinal"]),
    ("guarda-deixa-passar-ddl",
     "    if _DDL.search(codigo):",
     "    if False and _DDL.search(codigo):",
     ["guarda-recusa-ddl"]),
    ("guarda-le-o-literal-como-codigo",
     '    return _LITERAL.sub("\'\'", sql)',
     "    return sql",
     ["guarda-nao-confunde-prosa-com-ddl"]),
    ("guarda-aceita-coluna-de-score",
     "        if proibidas:",
     "        if False:",
     ["guarda-recusa-coluna-de-score-no-insert"]),
    ("guarda-aceita-sinal-sem-coluna-obrigatoria",
     "        if faltando:",
     "        if False:",
     ["guarda-recusa-insert-de-sinal-sem-organizacao-ou-tipo"]),
    ("guarda-aceita-tabela-nao-declarada",
     "            if tabela not in TABELAS_PERMITIDAS:",
     "            if False:",
     ["guarda-recusa-tabela-nao-declarada"]),
    ("guarda-aceita-escrita-em-organizations",
     "            if tabela == TABELA_ORGANIZACOES:",
     "            if False:",
     ["guarda-recusa-escrita-em-organizations"]),
    ("guarda-aceita-delete-fora-do-desfazer",
     '            if operacao == "delete" and not permitir_remocao:',
     "            if False:",
     ["guarda-recusa-delete-fora-do-desfazer"]),
    ("categoria-da-fonte-liberada",
     '    if "categoria" in observacao and _texto(observacao.get("categoria")):',
     "    if False:",
     ["categoria-declarada-pela-fonte-e-descartada"]),
    ("campo-nao-declarado-liberado",
     "        if campo not in CAMPOS_DA_OBSERVACAO:",
     "        if False:",
     ["campo-nao-declarado-e-descartado"]),
    ("data-invalida-aceita",
     "    if not _DATA_ISO.match(texto):",
     "    if False:",
     ["data-invalida-e-descartada-e-data-valida-passa"]),
    ("confianca-fora-da-faixa-aceita",
     "    if not 0 <= numero <= 1:",
     "    if False:",
     ["confianca-fora-da-faixa-e-descartada-e-ausente-fica-nula"]),
    ("tipo-desconhecido-aceito",
     "    elif tipo not in TIPOS_DE_SINAL:",
     "    elif False:",
     ["tipo-de-sinal-e-do-vocabulario-fechado"]),
    ("forte-invalido-aceito",
     "        if valida(bruto):",
     "        if True:",
     ["cnpj-com-digito-verificador-errado-e-descartado"]),
    ("vinculo-quebrado-aceito",
     "                if research_run_id and not self.research_run_existe(research_run_id):",
     "                if False:",
     ["fluxo-vinculo-com-research-run-inexistente-e-descartado"]),
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
     ["raiz-vem-do-marcador-nao-da-profundidade"]),
    ("segunda-copia-da-regra-de-identidade",
     "def categoria_do_tipo(tipo: str):",
     "def cnpj_valido(valor):\n    return True\n\n\ndef categoria_do_tipo(tipo: str):",
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
    temporario = Path(tempfile.mkdtemp(prefix="signal-autoteste-"))
    try:
        for mutacao in MUTACOES:
            nome = mutacao[0]
            # A copia vive um nivel ABAIXO do tempdir: assim ela importa de qualquer TMPDIR.
            copia = temporario / "mut" / ("signal-%s.py" % nome)
            copia.parent.mkdir(parents=True, exist_ok=True)
            esperados, detalhe = aplicar_mutacao(mutacao, copia, codigo)
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
    p = argparse.ArgumentParser(description="Suite do agente Signal Detector v1 (TRE-W4-E03-T01)")
    p.add_argument("--codigo", default=str(CODIGO_PADRAO), help="caminho de outro signal.py")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not ITENS:
        print("FALHOU a suite nao executou nenhum item")
        return 1
    modulo = carregar_modulo(Path(args.codigo))
    print("=== SUITE DO AGENTE SIGNAL DETECTOR v1 (%s) ===" % args.codigo)
    ok, falhas, _ = executar_suite(modulo)
    print("\nRESULTADO: SIGNAL_SUITE_%s (%d itens, %d falhas)"
          % ("OK" if falhas == 0 else "FALHOU", ok + falhas, falhas))
    sucesso = falhas == 0
    if args.autoteste:
        sucesso = autoteste(Path(args.codigo)) and sucesso
        print("RESULTADO FINAL: SIGNAL_%s" % ("OK" if sucesso else "FALHOU"))
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
