#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do AGENTE PAIN HYPOTHESIS v1 (card TRE-W4-E04-T01) — um comando, um veredito.

    python3 scripts/agentes/verificar_agente_pain_hypothesis.py
    python3 scripts/agentes/verificar_agente_pain_hypothesis.py --autoteste
    python3 scripts/agentes/verificar_agente_pain_hypothesis.py --codigo <caminho de outro pain_hypothesis.py>

O que ela prova (SEM banco e SEM rede): o contrato do agente existe e nao divergiu do Data
Contract V1.0 (fortes por prioridade, vocabulario de status, source_of_truth da hipotese); o
vocabulario de categorias de dor e o do doc 01 §5 (as cinco areas) e o de tipos de evidencia e o
do contrato do agente; a empresa e resolvida por identificador FORTE e o agente NAO cria empresa
nem escreve NENHUMA coluna de `organizations`; a hipotese grava so as colunas declaradas (nenhuma
coluna de impacto/validacao — score sem formula homologada e validacao e ato humano) e a guarda de
escrita recusa DDL, tabela fora da lista, escrita em `organizations`, INSERT de hipotese com coluna
de impacto/validacao, INSERT sem `organization_id`/`pain_statement` e `DELETE` fora do desfazer;
hipotese SEM LASTRO nao e gravada (evidencia inexistente ou de outra empresa e descartada com
motivo; sem lastro sobrando o veredito e RECUSADA); a idempotencia esta no SQL (claim + fechamento
ancorado na hipotese DESTA rodada, nao na prosa); o gate do JEV e fail-closed; e o fluxo completo
(registrar, replay idempotente, revisao com fila humana, recusar, auditoria, vínculo de pesquisa,
desfazer) se comporta como o contrato do card — medido numa PORTA DE ROTEIRO (implementacao da
porta declarada, nao duble de biblioteca).

AUTOTESTE (`--autoteste`): cada mutacao e aplicada a uma COPIA do arquivo apontado por `--codigo`
(o canonico, por padrao — as duas opcoes andam juntas de proposito: mutar o canonical enquanto se
testa outro arquivo e' prova contra alvo errado) e a suite tem de REPROVAR o item correspondente —
mutacao que passa em silencio e buraco de verificacao. Mutacao que nao se aplica (ancora de texto
mudou), que nao declara item nenhum ou que declara item INEXISTENTE na suite tambem reprova: e
buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = PAIN_SUITE_OK · 1 = PAIN_SUITE_FALHOU (o log aponta o item) · 2 = uso
incorreto. Guarda de confiabilidade: etapa que roda 0 item REPROVA.
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
CODIGO_PADRAO = RAIZ / "hermes/agents/pain_hypothesis/pain_hypothesis.py"
CONTRATO_AGENTE = RAIZ / "hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO_FONTE = RAIZ / "hermes/agents/pain_hypothesis/exemplos/hipoteses-exemplo.jsonl"
MODULO_SCOUT = RAIZ / "hermes/agents/scout/scout.py"
SUITE = RAIZ / "scripts/agentes/verificar_agente_pain_hypothesis.py"
# doc 01 §5: o vocabulario de categorias de dor vive no documento versionado do projeto. Quando
# acessivel, a suite confere contra ele; o contrato DESTE agente e' a fonte sempre disponivel.
DOC_01 = Path("/data/obsidian-vault/Comercial Transformativa/01_CONTEXTO_DO_PROJETO.md")
# Artefatos canonicos do card (os dois de documentacao e o aceite sao do autor do doc/aceite).
ARTEFATOS_DO_CARD = (
    "hermes/agents/pain_hypothesis/pain_hypothesis.py",
    "hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json",
    "hermes/agents/pain_hypothesis/exemplos/hipoteses-exemplo.jsonl",
    "scripts/agentes/verificar_agente_pain_hypothesis.py",
    "scripts/agentes/teste_pain_hypothesis_aceite.sh",
    "docs/architecture/agente-pain-hypothesis-v1.md",
    "docs/runbooks/agente-pain-hypothesis.md",
)

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "aaaaaaaa-0000-4000-8000-000000000002"
CNPJ_A = "11.222.333/0001-81"
CNPJ_B = "45.723.174/0001-10"
SIG_A = "11111111-0000-4000-8000-000000000001"
SIG_X = "11111111-0000-4000-8000-000000000099"
RUN_A = "33333333-0000-4000-8000-000000000001"
RUN_B = "33333333-0000-4000-8000-000000000002"
HID = "44444444-0000-4000-8000-000000000001"
SEV = "55555555-0000-4000-8000-000000000001"

ITENS = []


def item(nome):
    def decorador(funcao):
        ITENS.append((nome, funcao))
        return funcao
    return decorador


def _plano(texto: str) -> str:
    """Texto sem acento e em minusculas: conferencia de doc nao depende de acentuacao."""
    return "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c)) \
        .lower()


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
        self.agente = modulo.PainHypothesis(porta=PortaRoteiro(rota=[]), raiz=raiz)
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

    def valores_da_hipotese(self, **extra):
        valores = {"organization_id": ORG_A, "research_run_id": None,
                   "pain_category": "FINANCEIRO", "pain_statement": "dor de conciliacao manual",
                   "evidence_summary": None,
                   "evidence": {"inferencia": True, "evidencias": []},
                   "confidence": 0.7, "status": self.modulo.STATUS_INICIAL}
        valores.update(extra)
        return valores

    def sql_ingestao(self):
        return self.modulo.sql_ingerir(
            HID, SEV, "pain:org:%s:%s" % (ORG_A, "a" * 64),
            self.valores_da_hipotese(), {"origem": "pain_hypothesis"})


class PortaRoteiro:
    """Porta de teste: responde o roteiro declarado, GUARDA o SQL e roda a guarda do agente.

    Ela implementa a porta do agente (mesma interface) — nao substitui o alvo por duble de
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
    spec = importlib.util.spec_from_file_location("pain_hypothesis_sob_teste", str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def hipotese(**campos):
    base = {"organizacao": {"cnpj": CNPJ_A},
            "dor": "A conciliacao de recebiveis e manual e trava o fechamento mensal",
            "categoria": "FINANCEIRO",
            "evidencias": [{"tipo": "SINAL", "id": SIG_A}],
            "confianca": 0.7}
    base.update(campos)
    return base


def linha_organizacao(org_id: str, **valores) -> str:
    registro = {"id": org_id, "status": valores.pop("status", "DISCOVERED"),
                "legal_name": valores.pop("legal_name", "Empresa Teste Ltda"),
                "trade_name": valores.pop("trade_name", "Empresa Teste")}
    return json.dumps(registro, ensure_ascii=False)


def linha_sinal(sig_id: str, org_id: str, tipo="HIRING", categoria="EXPANSAO",
                data="2026-09-28") -> str:
    return "|".join([sig_id, org_id, tipo, categoria, data])


def linha_research(run_id: str, org_id: str, tipo="PESQUISA", status="COMPLETED") -> str:
    return "|".join([run_id, org_id, tipo, status])


def texto_do_sql(chamadas) -> str:
    return "\n".join(chamadas)


def agente_com_roteiro(ctx, rota, **kwargs):
    porta = PortaRoteiro(rota=rota, modulo=ctx.modulo)
    agente = ctx.modulo.PainHypothesis(porta=porta, raiz=ctx.raiz, **kwargs)
    return agente, porta


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


# ---------------------------------------------------------------------------------------
# 1. Contrato e artefatos
# ---------------------------------------------------------------------------------------
@item("agente-existe-e-importa")
def _(ctx):
    assert ctx.modulo.AGENTE == "pain_hypothesis"
    assert ctx.modulo.PAPEL == "pain_hypothesis"
    assert ctx.modulo.VERSAO == ctx.contrato["versao"]
    assert ctx.modulo.WORKFLOW == "hipotese-de-dor"


@item("raiz-vem-do-marcador-nao-da-profundidade")
def _(ctx):
    """A copia roda de qualquer cwd e de qualquer profundidade: a raiz sai do MARCADOR."""
    raso = Path(tempfile.mkdtemp(prefix="pain-raso-"))
    dentro = ctx.raiz / ("tmp-prova-raiz-%d" % os.getpid())
    antigo = Path.cwd()
    try:
        copia_rasa = raso / "mut" / "pain_hypothesis.py"
        copia_rasa.parent.mkdir(parents=True, exist_ok=True)
        copia_rasa.write_text(ctx.codigo, encoding="utf-8")
        copia_profunda = dentro / "n1" / "n2" / "n3" / "n4" / "pain_hypothesis.py"
        copia_profunda.parent.mkdir(parents=True, exist_ok=True)
        copia_profunda.write_text(ctx.codigo, encoding="utf-8")
        os.chdir(raso)
        modulo = carregar_modulo(copia_profunda)
        assert modulo.descobrir_raiz_padrao() == ctx.raiz, \
            "a raiz nao foi achada pelo marcador a 4 niveis: %s" % modulo.descobrir_raiz_padrao()
        agente = modulo.PainHypothesis(porta=PortaRoteiro(rota=[]))
        assert agente.raiz == ctx.raiz and agente.contrato["card"] == "TRE-W4-E04-T01"
        copia = carregar_modulo(copia_rasa)
        assert copia.descobrir_raiz_padrao() != ctx.raiz, \
            "copia fora da arvore nao pode herdar silenciosamente a raiz do repo"
        try:
            copia.PainHypothesis(porta=PortaRoteiro(rota=[]))
        except Exception as exc:  # noqa: BLE001 — qualquer recusa serve, desde que cite o contrato
            assert "agente-pain-hypothesis-v1.json" in str(exc), exc
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
    assert c["card"] == "TRE-W4-E04-T01"
    assert set(c["vereditos"]) == set(ctx.modulo.VEREDITOS)
    assert c["status_de_agent_runs"] == ctx.modulo.STATUS_AGENT_RUNS
    assert list(c["categorias_de_dor"]) == list(ctx.modulo.CATEGORIAS_DE_DOR)
    assert list(c["tipos_de_evidencia"]) == list(ctx.modulo.TIPOS_DE_EVIDENCIA)
    assert list(c["escrita_hipotese"]["colunas_escritas"]) == list(ctx.modulo.COLUNAS_DA_HIPOTESE)
    assert list(c["escrita_hipotese"]["colunas_proibidas"]) == list(ctx.modulo.COLUNAS_PROIBIDAS)
    assert set(c["escrita"]["tabelas_permitidas"]) == set(ctx.modulo.TABELAS_PERMITIDAS)
    assert set(c["escrita"]["tabelas_lidas"]) == set(ctx.modulo.TABELAS_LIDAS)
    assert set(c["identidade"]["fortes_por_prioridade"]) == set(ctx.fortes)


@item("identidade-forte-vem-do-contrato-de-dados")
def _(ctx):
    assert list(ctx.fortes) == list(ctx.contrato_dados["dedup"]["strong"])
    assert list(ctx.fortes) == list(ctx.contrato["identidade"]["fortes_por_prioridade"])
    assert list(ctx.fortes) == ["cnpj", "domain", "linkedin_url"], ctx.fortes


@item("categorias-de-dor-cobrem-o-vocabulario-do-doc-01")
def _(ctx):
    """As cinco areas de dor prioritaria (doc 01 §5) — nao se inventa categoria nova."""
    esperado = ["FINANCEIRO", "COMERCIAL", "ATENDIMENTO", "OPERACOES", "DOCUMENTOS"]
    assert list(ctx.modulo.CATEGORIAS_DE_DOR) == esperado, ctx.modulo.CATEGORIAS_DE_DOR
    assert len(set(ctx.modulo.CATEGORIAS_DE_DOR)) == 5, "categoria repetida no vocabulario"
    assert list(ctx.contrato["categorias_de_dor"]) == esperado
    for categoria in esperado:
        assert ctx.modulo.validar_categoria(categoria) == (categoria, None), categoria
    if DOC_01.is_file():
        texto = DOC_01.read_text(encoding="utf-8")
        secao = texto.split("## 5.", 1)[-1].split("\n## ", 1)[0]
        areas = [re.sub(r"^#+\s*", "", l).strip() for l in secao.splitlines()
                 if l.strip().startswith("### ")]
        assert [_plano(a).upper() for a in areas] == esperado, \
            "vocabulario divergiu do doc 01 §5: %s" % areas


@item("tipos-de-evidencia-cobrem-sinal-e-pesquisa")
def _(ctx):
    assert list(ctx.modulo.TIPOS_DE_EVIDENCIA) == ["SINAL", "PESQUISA"]
    assert list(ctx.contrato["tipos_de_evidencia"]) == ["SINAL", "PESQUISA"]


@item("status-escrito-e-o-inicial-do-contrato")
def _(ctx):
    """`status` e SEMPRE o inicial (`vocabularies.pain_hypotheses.status[0]`); validacao e humana."""
    vocabulario = ctx.contrato_dados["vocabularies"]["pain_hypotheses.status"]
    assert ctx.modulo.STATUS_INICIAL == vocabulario[0] == "HYPOTHESIS", ctx.modulo.STATUS_INICIAL
    assert ctx.modulo.STATUS_INICIAL in vocabulario
    sql = ctx.sql_ingestao()
    assert "status" in ctx.modulo.COLUNAS_DA_HIPOTESE
    assert ctx.modulo.lit(ctx.modulo.STATUS_INICIAL) in sql, \
        "o INSERT nao escreve o status inicial do contrato"
    for humano in ("VALIDATED", "PARTIALLY_VALIDATED", "REJECTED", "STALE"):
        assert humano not in sql, "status humano %s escrito pelo agente" % humano


@item("contrato-declara-que-nao-cria-organizacao-e-nao-valida")
def _(ctx):
    identidade = ctx.contrato["identidade"]
    assert identidade["cria_organizacao"] is False
    assert identidade["escreve_em_organizations"] is False
    assert ctx.contrato["outbox"]["emite_evento"] is False
    assert ctx.contrato["lacunas_declaradas"]
    proibido = " ".join(_plano(p) for p in ctx.contrato["escrita"]["proibido"])
    for promessa in ("organizations", "business_impact_score", "validated_at", "ddl"):
        assert promessa in proibido, "o contrato nao proibe: %s" % promessa


@item("contrato-do-agente-declara-as-lacunas-da-v1")
def _(ctx):
    """Sem doc/runbook versionado nesta rodada, a fonte e' o proprio contrato do agente."""
    lacunas = _plano(" ".join(ctx.contrato["lacunas_declaradas"]))
    for promessa in ("sem llm", "sem score", "validated_at", "contacts", "outbox"):
        assert promessa in lacunas, "lacuna nao declarada no contrato: %s" % promessa
    assert ctx.contrato["guardrails"]["llm_exige_recibo_jev"] is True
    assert ctx.contrato["guardrails"]["sem_http_smtp_socket"]


@item("artefatos-do-card-existem-e-gate-declara-a-onda-w4")
def _(ctx):
    """Os artefatos-fonte do card existem no repo; o gate segue a convencao da W4.

    O bloco do pain_hypothesis no `scripts/verificar_estrutura.sh` e' do autor do card (o gate
    esta congelado nesta rodada): a suite confere a existencia em disco e, SE o bloco existir,
    exige os sete artefatos canonicos. Enquanto nao existir, confere que a convencao da onda
    (scout/research/signal) esta declarada no gate.
    """
    for artefato in ("hermes/agents/pain_hypothesis/pain_hypothesis.py",
                     "hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json",
                     "hermes/agents/pain_hypothesis/exemplos/hipoteses-exemplo.jsonl",
                     "scripts/agentes/verificar_agente_pain_hypothesis.py"):
        assert (ctx.raiz / artefato).is_file(), "artefato ausente no repo: %s" % artefato
    gate = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    for artefato in ("hermes/agents/scout/scout.py", "hermes/agents/research/research.py",
                     "hermes/agents/signal/signal.py"):
        assert artefato in gate, "o gate nao declara o agente da onda: %s" % artefato
    if "hermes/agents/pain_hypothesis/pain_hypothesis.py" in gate:
        for artefato in ARTEFATOS_DO_CARD:
            assert artefato in gate, "bloco do card incompleto no gate: %s" % artefato


@item("colunas-da-hipotese-existem-no-ddl")
def _(ctx):
    colunas = ctx.colunas("sales_intelligence.pain_hypotheses")
    faltando = [c for c in ctx.modulo.COLUNAS_DA_HIPOTESE if c not in colunas]
    assert not faltando, "colunas escritas fora do DDL: %s" % faltando


@item("colunas-proibidas-de-impacto-e-validacao-existem-no-ddl")
def _(ctx):
    """business_impact_score/estimated_impact_description/validated_at existem, mas nao sao da v1."""
    do_ddl = ctx.colunas("sales_intelligence.pain_hypotheses")
    for coluna in ctx.modulo.COLUNAS_PROIBIDAS:
        assert coluna in do_ddl, "coluna proibida inexistente no DDL: %s" % coluna
    assert not (set(ctx.modulo.COLUNAS_PROIBIDAS) & set(ctx.modulo.COLUNAS_DA_HIPOTESE)), \
        "o agente nao pode escrever coluna de impacto/validacao"
    assert set(ctx.modulo.COLUNAS_PROIBIDAS) == set(
        ctx.contrato["escrita_hipotese"]["colunas_proibidas"])


@item("escrita-da-hipotese-casa-com-o-ddl")
def _(ctx):
    colunas, expressoes = ctx.modulo.montar_linha_hipotese(HID, ctx.valores_da_hipotese())
    assert len(colunas) == len(expressoes), "colunas x valores divergentes"
    do_ddl = ctx.colunas("sales_intelligence.pain_hypotheses")
    faltando = [c for c in colunas if c not in do_ddl]
    assert not faltando, "colunas do INSERT fora do DDL: %s" % faltando
    for obrigatoria in ("organization_id", "pain_statement"):
        assert obrigatoria in colunas, "INSERT sem a coluna NOT NULL %s" % obrigatoria
    assert set(colunas) == set(ctx.modulo.COLUNAS_DA_HIPOTESE)


@item("escrita-de-agent-runs-casa-com-o-ddl")
def _(ctx):
    sql = ctx.modulo.sql_registrar_execucao(SEV, "corr", ORG_A, "COMPLETED", {}, {}, "t0", "t1")
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


@item("escrita-de-human-approvals-casa-com-o-ddl")
def _(ctx):
    sql = ctx.modulo.sql_pedir_revisao(HID, SEV, "pain:revisao:x", hipotese(), "MOTIVO", [], "corr")
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
        "a fila humana precisa do mesmo claim idempotente da hipotese"
    assert ctx.modulo.MARCA_REVISAO in sql, "o fechamento precisa provar a linha da fila"


@item("escrita-de-sync-events-casa-com-o-ddl")
def _(ctx):
    do_ddl = ctx.colunas("sales_intelligence.sync_events")
    desfazer = ctx.modulo.sql_desfazer(
        [{"pain_hypothesis_id": HID, "organization_id": ORG_A, "sync_event_id": SEV}], "corr", "3")
    for sql in (ctx.sql_ingestao(), desfazer):
        for bloco in re.findall(r"INSERT INTO %s \(([^)]*)\)" % ctx.modulo.TABELA_SYNC_EVENTS,
                                sql):
            colunas = [c.strip() for c in bloco.split(",")]
            assert not [c for c in colunas if c not in do_ddl], colunas


@item("source-of-truth-do-contrato-manda-a-hipotese-para-o-postgresql")
def _(ctx):
    contrato = ctx.contrato_dados
    assert contrato["source_of_truth"]["hipóteses IA"] == "PostgreSQL"
    assert {"name": "pain_hypotheses", "purpose": "hipotese de dor (inferencia)"} in contrato["tables"]


@item("exemplos-da-fonte-sao-validos-e-cobrem-5-categorias-e-2-tipos")
def _(ctx):
    categorias = set()
    tipos = set()
    linhas = [l for l in EXEMPLO_FONTE.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(linhas) >= 5, "a fonte de exemplo precisa de pelo menos 5 hipoteses"
    for linha in linhas:
        validacao = ctx.modulo.validar_hipotese(json.loads(linha), ctx.fortes, ctx.agente.identidade)
        assert not validacao["problemas"], "exemplo invalido: %s -> %s" % (linha[:60],
                                                                          validacao["problemas"])
        assert not validacao["descartados"], "exemplo com descarte: %s" % validacao["descartados"]
        assert validacao["dor"] and validacao["evidencias"], validacao
        categorias.add(validacao["categoria"])
        tipos.update(e["tipo"] for e in validacao["evidencias"])
    assert categorias == set(ctx.modulo.CATEGORIAS_DE_DOR), \
        "os exemplos nao cobrem as 5 categorias: %s" % sorted(categorias)
    assert tipos == set(ctx.modulo.TIPOS_DE_EVIDENCIA), \
        "os exemplos nao cobrem os 2 tipos: %s" % sorted(tipos)


# ---------------------------------------------------------------------------------------
# 2. Identidade e validacao da hipotese
# ---------------------------------------------------------------------------------------
@item("identidade-forte-na-prioridade-do-contrato")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(
        hipotese(organizacao={"linkedin_url": "https://www.linkedin.com/company/acme",
                              "domain": "acme.com.br", "cnpj": CNPJ_A}),
        ctx.fortes, ctx.agente.identidade)
    assert [t for t, _ in validacao["validos"]] == ["cnpj", "domain", "linkedin_url"], \
        validacao["validos"]
    assert validacao["validos"][0][1] == "11222333000181"
    assert validacao["validos"][1][1] == "acme.com.br"
    assert validacao["declarados"] == ["cnpj", "domain", "linkedin_url"]


@item("cnpj-com-digito-verificador-errado-e-recusado")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(hipotese(organizacao={"cnpj": "11.222.333/0001-00"}),
                                            ctx.fortes, ctx.agente.identidade)
    assert validacao["validos"] == [], validacao["validos"]
    assert "IDENTIFICADOR_FORTE_INVALIDO" in validacao["problemas"], validacao["problemas"]


@item("sem-identificador-forte-e-recusado")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(hipotese(organizacao={"city": "Santos"}),
                                            ctx.fortes, ctx.agente.identidade)
    assert validacao["declarados"] == []
    assert "SEM_IDENTIFICADOR_FORTE" in validacao["problemas"], validacao["problemas"]
    veredito, motivo = ctx.modulo.decidir_veredito(validacao["problemas"], validacao["validos"], [])
    assert veredito == ctx.modulo.RECUSADA and motivo == "SEM_IDENTIFICADOR_FORTE"


@item("dor-obrigatoria-e-acima-do-limite-recusa")
def _(ctx):
    assert ctx.modulo.validar_dor("")[1] == "SEM_DOR_DECLARADA"
    assert ctx.modulo.validar_dor(None)[1] == "SEM_DOR_DECLARADA"
    assert ctx.modulo.validar_dor("x" * (ctx.modulo.LIMITE_DOR + 1))[1] == "DOR_ACIMA_DO_LIMITE"
    assert ctx.modulo.validar_dor("  dor  ") == ("dor", None)
    sem = ctx.modulo.validar_hipotese(hipotese(dor=""), ctx.fortes, ctx.agente.identidade)
    assert "SEM_DOR_DECLARADA" in sem["problemas"], sem["problemas"]
    longa = ctx.modulo.validar_hipotese(hipotese(dor="x" * (ctx.modulo.LIMITE_DOR + 1)),
                                        ctx.fortes, ctx.agente.identidade)
    assert "DOR_ACIMA_DO_LIMITE" in longa["problemas"], longa["problemas"]
    veredito, motivo = ctx.modulo.decidir_veredito(["SEM_DOR_DECLARADA"], [("cnpj", "x")], [ORG_A], 1)
    assert veredito == ctx.modulo.RECUSADA and motivo == "SEM_DOR_DECLARADA"


@item("categoria-desconhecida-recusa-e-ausente-fica-nula")
def _(ctx):
    assert ctx.modulo.validar_categoria("") == (None, None)
    assert ctx.modulo.validar_categoria(None) == (None, None)
    assert ctx.modulo.validar_categoria("financeiro") == ("FINANCEIRO", None)
    assert ctx.modulo.validar_categoria("FINANCEIRO_INVENTADO")[1] == "CATEGORIA_DE_DOR_DESCONHECIDA"
    assert ctx.modulo.validar_categoria("x" * (ctx.modulo.LIMITE_CATEGORIA + 1))[1] == \
        "CATEGORIA_ACIMA_DO_LIMITE"
    validacao = ctx.modulo.validar_hipotese(hipotese(categoria=""), ctx.fortes,
                                            ctx.agente.identidade)
    assert validacao["categoria"] is None and not validacao["problemas"], validacao


@item("resumo-acima-do-limite-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(hipotese(resumo_da_evidencia="r" * (ctx.modulo.LIMITE_RESUMO + 1)),
                                            ctx.fortes, ctx.agente.identidade)
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    assert motivos.get("resumo_da_evidencia") == "RESUMO_ACIMA_DO_LIMITE", validacao["descartados"]
    assert not validacao["problemas"], "resumo longo nao recusa a hipotese inteira"
    bom = ctx.modulo.validar_hipotese(hipotese(resumo_da_evidencia="ok"), ctx.fortes,
                                      ctx.agente.identidade)
    assert bom["resumo"] == "ok" and not bom["descartados"], bom


@item("confianca-fora-da-faixa-e-descartada-e-ausente-fica-nula")
def _(ctx):
    for ruim, motivo in ((1.5, "CONFIANCA_FORA_DA_FAIXA"), (-0.2, "CONFIANCA_FORA_DA_FAIXA"),
                         ("muito", "CONFIANCA_NAO_E_NUMERO")):
        validacao = ctx.modulo.validar_hipotese(hipotese(confianca=ruim), ctx.fortes,
                                                ctx.agente.identidade)
        assert validacao["confianca"] is None, (ruim, validacao["confianca"])
        assert any(d["campo"] == "confianca" and d["motivo"] == motivo
                   for d in validacao["descartados"]), (motivo, validacao["descartados"])
    sem = ctx.modulo.validar_hipotese(hipotese(confianca=None), ctx.fortes, ctx.agente.identidade)
    assert sem["confianca"] is None and not sem["descartados"], sem
    arredondada = ctx.modulo.validar_hipotese(hipotese(confianca=0.123456), ctx.fortes,
                                              ctx.agente.identidade)
    assert arredondada["confianca"] == 0.1235, arredondada["confianca"]


@item("research-run-id-de-formato-invalido-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(hipotese(research_run_id="nao-e-uuid"), ctx.fortes,
                                            ctx.agente.identidade)
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    assert motivos.get("research_run_id") == "RESEARCH_RUN_ID_INVALIDO", validacao["descartados"]
    assert validacao["research_run_id"] is None
    bom = ctx.modulo.validar_hipotese(hipotese(research_run_id=RUN_A), ctx.fortes,
                                      ctx.agente.identidade)
    assert bom["research_run_id"] == RUN_A and not bom["descartados"], bom


@item("campos-derivados-na-entrada-sao-descartados-com-derivado-nao-aceito")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(
        hipotese(status="VALIDATED", id="x", validated_at="2026-01-01",
                 business_impact_score=90, estimated_impact_description="alto"),
        ctx.fortes, ctx.agente.identidade)
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    for campo in ("status", "id", "validated_at", "business_impact_score",
                  "estimated_impact_description"):
        assert motivos.get(campo) == "DERIVADO_NAO_ACEITO", validacao["descartados"]
    assert not validacao["problemas"], validacao["problemas"]


@item("campo-nao-declarado-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(hipotese(employee_count=120, segmento="varejo"),
                                            ctx.fortes, ctx.agente.identidade)
    motivos = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    for campo in ("employee_count", "segmento"):
        assert motivos.get(campo) == "CAMPO_NAO_DECLARADO", validacao["descartados"]
    assert not validacao["problemas"], validacao["problemas"]


@item("evidencia-sem-lista-tipo-id-recusa")
def _(ctx):
    casos = ((None, "SEM_EVIDENCIA_DECLARADA"), ([], "SEM_EVIDENCIA_DECLARADA"),
             ("texto", "EVIDENCIAS_ILEGIVEIS"), ([42], "EVIDENCIA_ILEGIVEL"),
             ([{}], "EVIDENCIA_SEM_TIPO"),
             ([{"tipo": "PANFLETO", "id": SIG_A}], "TIPO_DE_EVIDENCIA_DESCONHECIDO"),
             ([{"tipo": "SINAL"}], "EVIDENCIA_SEM_ID"),
             ([{"tipo": "SINAL", "id": "nao-e-uuid"}], "EVIDENCIA_COM_ID_INVALIDO"))
    for evidencias, esperado in casos:
        validacao = ctx.modulo.validar_hipotese(hipotese(evidencias=evidencias), ctx.fortes,
                                                ctx.agente.identidade)
        assert esperado in validacao["problemas"], (esperado, validacao["problemas"])


@item("evidencia-duplicada-e-descartada")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(
        hipotese(evidencias=[{"tipo": "SINAL", "id": SIG_A}, {"tipo": "SINAL", "id": SIG_A}]),
        ctx.fortes, ctx.agente.identidade)
    assert len(validacao["evidencias"]) == 1, validacao["evidencias"]
    assert any(d["motivo"] == "EVIDENCIA_DUPLICADA" for d in validacao["descartados"]), \
        validacao["descartados"]
    assert not validacao["problemas"], validacao["problemas"]


@item("evidencia-com-campo-inventado-e-descartada")
def _(ctx):
    validacao = ctx.modulo.validar_hipotese(
        hipotese(evidencias=[{"tipo": "SINAL", "id": SIG_A, "peso": 9}]), ctx.fortes,
        ctx.agente.identidade)
    assert any(d["campo"] == "evidencias[0].peso" and d["motivo"] == "CAMPO_NAO_DECLARADO"
               for d in validacao["descartados"]), validacao["descartados"]
    assert len(validacao["evidencias"]) == 1 and not validacao["problemas"], validacao


@item("hipotese-ilegivel-e-recusada")
def _(ctx):
    for bruta in ("texto solto", 42, None):
        validacao = ctx.modulo.validar_hipotese(bruta, ctx.fortes, ctx.agente.identidade)
        assert validacao["problemas"] == ["HIPOTESE_ILEGIVEL"], (bruta, validacao["problemas"])
    veredito, motivo = ctx.modulo.decidir_veredito(["HIPOTESE_ILEGIVEL"], [], [])
    assert veredito == ctx.modulo.RECUSADA and motivo == "HIPOTESE_ILEGIVEL"


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
    for proibido in ("def cnpj_valido", "def normalizar_cnpj", "def normalizar_domain",
                     "def normalizar_linkedin", "def domain_valido", "def linkedin_valido"):
        assert proibido not in ctx.codigo, "o agente reimplementou a regra de identidade: %s" % proibido
    assert modulo.lit("a'b") == identidade.lit("a'b")
    assert modulo._LITERAIS[0].__code__.co_code == identidade.lit.__code__.co_code, \
        "o escape de literal nao e' o do produtor"
    assert modulo._LITERAIS[1].__code__.co_code == identidade.lit_json.__code__.co_code


# ---------------------------------------------------------------------------------------
# 3. Decisao de veredito e chave de idempotencia
# ---------------------------------------------------------------------------------------
@item("veredito-registra-uma-unica-casada")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], [ORG_A], 1)
    assert veredito == ctx.modulo.REGISTRADA and motivo is None, (veredito, motivo)


@item("veredito-recusa-zero-casadas")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], [], 0)
    assert veredito == ctx.modulo.RECUSADA and motivo == "ORGANIZACAO_NAO_ENCONTRADA"


@item("veredito-vai-para-revisao-com-duas-ou-mais-casadas")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")],
                                                   [ORG_A, ORG_B], 1)
    assert veredito == ctx.modulo.REVISAO and motivo == "CONFLITO_DE_IDENTIDADE_FORTE"


@item("problema-de-entrada-vence-a-casada")
def _(ctx):
    """Hipotese invalida nao segue nem quando a identidade casaria: recusa, nao aviso."""
    veredito, motivo = ctx.modulo.decidir_veredito(["SEM_DOR_DECLARADA"],
                                                   [("cnpj", "11222333000181")], [ORG_A], 5)
    assert veredito == ctx.modulo.RECUSADA and motivo == "SEM_DOR_DECLARADA"


@item("veredito-recusa-sem-evidencia-valida")
def _(ctx):
    """Inferencia sem evidencia e' opiniao: sem lastro valido nao ha REGISTRADA."""
    veredito, motivo = ctx.modulo.decidir_veredito([], [("cnpj", "11222333000181")], [ORG_A], 0)
    assert veredito == ctx.modulo.RECUSADA and motivo == "SEM_EVIDENCIA_VALIDA"


@item("status-de-agent-runs-cobre-todos-os-vereditos")
def _(ctx):
    for veredito in ctx.modulo.VEREDITOS:
        assert veredito in ctx.modulo.STATUS_AGENT_RUNS, veredito
        assert veredito in ctx.contrato["vereditos"], veredito


@item("hash-da-entrada-e-estavel-e-sensivel-ao-conteudo")
def _(ctx):
    def h(hip):
        v = ctx.modulo.validar_hipotese(hip, ctx.fortes, ctx.agente.identidade)
        return ctx.modulo.hash_da_entrada(v["validos"], v)

    primeiro = h(hipotese())
    assert len(primeiro) == 64, primeiro
    assert h(hipotese()) == primeiro, "a MESMA hipotese produziu hash diferente"
    assert h(hipotese(dor="outra dor completamente diferente")) != primeiro, \
        "trocar a dor nao mudou a chave de idempotencia"
    assert h(hipotese(categoria="COMERCIAL")) != primeiro, "trocar a categoria nao mudou o hash"
    assert h(hipotese(evidencias=[{"tipo": "SINAL", "id": SIG_X}])) != primeiro, \
        "trocar a evidencia nao mudou o hash"
    assert h(hipotese(confianca=0.1)) != primeiro, "trocar a confianca nao mudou o hash"
    assert h(hipotese(research_run_id=RUN_A)) != primeiro, \
        "trocar o vinculo research_run_id nao mudou o hash"
    # ordem das chaves nao importa (JSON canonico)
    assert h({"dor": hipotese()["dor"], "categoria": "FINANCEIRO", "confianca": 0.7,
              "organizacao": {"cnpj": CNPJ_A},
              "evidencias": [{"tipo": "SINAL", "id": SIG_A}]}) == primeiro


@item("chave-de-idempotencia-e-chave-de-revisao-tem-a-forma-declarada")
def _(ctx):
    hash_entrada = "b" * 64
    chave = ctx.modulo.chave_idempotencia(ORG_A, hash_entrada)
    assert chave == "pain:org:%s:%s" % (ORG_A, hash_entrada), chave
    assert chave.startswith("pain:org:")
    revisao = ctx.modulo.chave_da_revisao([ORG_B, ORG_A], hash_entrada)
    assert revisao == "pain:revisao:%s,%s:%s" % (ORG_A, ORG_B, hash_entrada), revisao
    assert ctx.modulo.chave_da_revisao([ORG_A, ORG_B], hash_entrada) == revisao, \
        "a chave da revisao nao e' estavel a ordem das candidatas"


@item("planejar-nao-toca-a-porta")
def _(ctx):
    porta = PortaRoteiro(rota=[], modulo=ctx.modulo)
    agente = ctx.modulo.PainHypothesis(porta=porta, raiz=ctx.raiz)
    plano = agente.planejar(hipotese())
    assert plano["veredito"] == ctx.modulo.PLANEJADO_REGISTRAR, plano
    assert plano["categoria"] == "FINANCEIRO" and plano["input_hash"], plano
    assert porta.chamadas == [], "o modo --planejar falou com o banco: %s" % porta.chamadas
    recusado = agente.planejar(hipotese(dor=""))
    assert recusado["veredito"] == ctx.modulo.PLANEJADO_RECUSAR, recusado
    assert "SEM_DOR_DECLARADA" in recusado["motivos"], recusado["motivos"]


# ---------------------------------------------------------------------------------------
# 4. SQL e guarda de escrita
# ---------------------------------------------------------------------------------------
@item("sql-de-consulta-de-organizacao-casa-identidade-por-forte")
def _(ctx):
    sql = ctx.modulo.sql_consultar_organizacao([("cnpj", "11222333000181")])
    assert "sales_intelligence.organizations" in sql
    assert "cnpj = '11222333000181'" in sql, sql
    assert "deleted_at IS NULL" in sql, "a consulta pode casar empresa apagada"
    assert "ORDER BY id" in sql, sql
    multi = ctx.modulo.sql_consultar_organizacao([("cnpj", "11222333000181"),
                                                  ("domain", "acme.com.br")])
    assert " OR " in multi and "domain = 'acme.com.br'" in multi, multi


@item("sql-de-sinais-conserva-o-dono-e-o-fato")
def _(ctx):
    sql = ctx.modulo.sql_sinais_existentes([SIG_A])
    assert "sales_intelligence.signals" in sql, sql
    for coluna in ("organization_id", "signal_type", "signal_category", "event_date"):
        assert coluna in sql, "a leitura do sinal perdeu %s" % coluna
    assert "'%s'" % SIG_A in sql and "ORDER BY id" in sql, sql


@item("sql-de-research-runs-conserva-o-dono-e-o-tipo")
def _(ctx):
    sql = ctx.modulo.sql_research_runs_existentes([RUN_A])
    assert "sales_intelligence.research_runs" in sql, sql
    for coluna in ("organization_id", "research_type", "status"):
        assert coluna in sql, "a leitura da pesquisa perdeu %s" % coluna
    assert "'%s'" % RUN_A in sql and "ORDER BY id" in sql, sql


@item("sql-de-ingestao-tem-o-claim-idempotente")
def _(ctx):
    sql = ctx.sql_ingestao()
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql, sql
    assert "INSERT INTO %s" % ctx.modulo.TABELA_SYNC_EVENTS in sql
    assert "idempotency_key" in sql and "request_payload" in sql
    assert "INSERT INTO sales_intelligence.pain_hypotheses" in sql


@item("sql-de-ingestao-ancora-o-fechamento-na-hipotese-desta-rodada")
def _(ctx):
    sql = ctx.sql_ingestao()
    assert "AND EXISTS (SELECT 1 FROM sales_intelligence.pain_hypotheses WHERE id = " in sql, sql
    assert "'PAIN_REGISTRADA'" in sql, "o fechamento perdeu a marca da rodada"
    assert "response_payload" in sql and "status = 'SUCCESS'" in sql


@item("sql-de-ingestao-nao-escreve-em-organizations")
def _(ctx):
    sql = ctx.sql_ingestao() + ctx.modulo.sql_desfazer(
        [{"pain_hypothesis_id": HID, "organization_id": ORG_A, "sync_event_id": SEV}], "corr", "3")
    assert "INSERT INTO sales_intelligence.pain_hypotheses" in sql
    assert "INSERT INTO sales_intelligence.signals" not in sql
    for operacao, padrao in ctx.modulo._ESCRITA:
        for tabela in padrao.findall(sql):
            assert tabela.lower() != ctx.modulo.TABELA_ORGANIZACOES, \
                "%s em organizations no SQL do agente" % operacao


@item("sql-gerado-passa-na-propria-guarda")
def _(ctx):
    ctx.modulo.validar_sql(ctx.sql_ingestao())
    ctx.modulo.validar_sql(ctx.modulo.sql_registrar_execucao(SEV, "corr", ORG_A, "COMPLETED",
                                                             {}, {}, "t0", "t1"))
    ctx.modulo.validar_sql(ctx.modulo.sql_pedir_revisao(HID, SEV, "pain:revisao:x", hipotese(),
                                                        "M", [], "corr"))
    ctx.modulo.validar_sql(ctx.modulo.sql_selecionar_da_rodada("corr"))
    ctx.modulo.validar_sql(ctx.modulo.sql_desfazer(
        [{"pain_hypothesis_id": HID, "organization_id": ORG_A, "sync_event_id": SEV}], "corr", "3"),
        permitir_remocao=True)


@item("insert-de-hipotese-declara-as-colunas-obrigatorias")
def _(ctx):
    colunas = ctx.modulo.colunas_do_insert(ctx.sql_ingestao(), ctx.modulo.TABELA_HIPOTESES)
    assert len(colunas) == 1, colunas
    assert sorted(colunas[0]) == sorted(ctx.modulo.COLUNAS_DA_HIPOTESE), colunas[0]
    assert "organization_id" in colunas[0]
    assert "pain_statement" in colunas[0]


@item("guarda-recusa-ddl")
def _(ctx):
    for sql in ("CREATE TABLE sales_intelligence.x (id int);",
                "ALTER TABLE sales_intelligence.pain_hypotheses ADD COLUMN y int;",
                "DROP TABLE sales_intelligence.pain_hypotheses;",
                "TRUNCATE sales_intelligence.pain_hypotheses;"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("a guarda aceitou DDL: %s" % sql)


@item("guarda-recusa-tabela-nao-declarada")
def _(ctx):
    for sql in ("INSERT INTO sales_intelligence.scores (id) VALUES ('x');",
                "INSERT INTO sales_intelligence.research_runs (id) VALUES ('x');",
                "UPDATE sales_intelligence.outbox_events SET status = 'PENDING';",
                "INSERT INTO sales_intelligence.signals (id) VALUES ('x');"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("a guarda aceitou escrita fora da lista: %s" % sql)


@item("guarda-recusa-escrita-em-organizations")
def _(ctx):
    """A hipotese nao escreve na empresa: a recusa nomeia a REGRA, nao so a lista de tabelas."""
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


@item("guarda-recusa-coluna-de-impacto-no-insert")
def _(ctx):
    for coluna in ctx.modulo.COLUNAS_PROIBIDAS:
        sql = ("INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id, "
               "pain_statement, %s) VALUES ('x', 'y', 'z', 10);" % coluna)
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert "impacto" in str(exc) or "validacao" in str(exc), exc
        else:
            raise AssertionError("a guarda aceitou coluna de impacto/validacao: %s" % coluna)


@item("guarda-recusa-insert-de-hipotese-sem-organization-ou-pain")
def _(ctx):
    casos = ("INSERT INTO sales_intelligence.pain_hypotheses (id, pain_statement) "
             "VALUES ('x', 'dor');",
             "INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id) "
             "VALUES ('x', 'y');")
    for sql in casos:
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada as exc:
            assert "obrigatoria" in str(exc), exc
        else:
            raise AssertionError("a guarda aceitou hipotese sem coluna NOT NULL: %s" % sql)


@item("guarda-recusa-delete-fora-do-desfazer")
def _(ctx):
    for sql in ("DELETE FROM sales_intelligence.pain_hypotheses WHERE id = 'x';",
                "DELETE FROM sales_intelligence.sync_events WHERE id = 'x';"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("a guarda aceitou DELETE fora do desfazer: %s" % sql)
    ctx.modulo.validar_sql("DELETE FROM sales_intelligence.pain_hypotheses WHERE id IN ('x');",
                           permitir_remocao=True)


@item("guarda-nao-confunde-prosa-com-ddl")
def _(ctx):
    """Enunciado da dor e' prosa: 'queda de receita'/'drop table' nao derruba hipotese legitima."""
    valores = ctx.valores_da_hipotese(
        pain_statement="a queda de receita veio depois do drop table organizacoes no processo",
        evidence={"inferencia": True,
                  "evidencias": [{"tipo": "SINAL", "id": SIG_A,
                                  "descricao": "a empresa disse: drop table"}]})
    sql = ctx.modulo.sql_ingerir(HID, SEV, "pain:org:%s:%s" % (ORG_A, "a" * 64), valores,
                                 {"origem": "pain_hypothesis"})
    ctx.modulo.validar_sql(sql)   # nao pode levantar


@item("literal-escapa-apostrofo-pelo-produtor")
def _(ctx):
    identidade = ctx.agente.identidade
    assert ctx.modulo.lit("O'Brien") == identidade.lit("O'Brien")
    valores = ctx.valores_da_hipotese(pain_statement="O'Brien Servicos tem dor de conciliacao")
    sql = ctx.modulo.sql_ingerir(HID, SEV, "pain:org:%s:%s" % (ORG_A, "a" * 64), valores,
                                 {"origem": "pain_hypothesis"})
    assert "O''Brien" in sql, "apostrofo nao foi escapado"


@item("sync-event-da-hipotese-aponta-a-hipotese")
def _(ctx):
    """O `sync_events` da hipotese aponta a HIPOTESE (entity_type/entity_id), nao a empresa."""
    sql = ctx.sql_ingestao()
    assert "  VALUES ('%s', 'pain_hypothesis', '%s', 'pain_hypothesis', 'postgresql', " \
        "'PAIN_HYPOTHESIS', " % (SEV, HID) in sql, sql
    desfazer = ctx.modulo.sql_desfazer(
        [{"pain_hypothesis_id": HID, "organization_id": ORG_A, "sync_event_id": SEV}], "corr", "3")
    assert "VALUES ('3', 'pain_hypothesis', NULL, 'pain_hypothesis', 'postgresql', 'ROLLBACK'" \
        in desfazer, desfazer


@item("desfazer-seleciona-so-o-que-a-rodada-criou")
def _(ctx):
    corr = "dddddddd-0000-4000-8000-000000000001"
    sql = ctx.modulo.sql_selecionar_da_rodada(corr)
    assert "sales_intelligence.pain_hypotheses" in sql
    assert "(a.output ->> 'pain_hypothesis_id') = h.id::text" in sql, sql
    assert "e.entity_id = h.id" in sql, "o vinculo com o sync_event da chave nao esta na consulta"
    assert "'%s'" % corr in sql and "a.agent_name = 'pain_hypothesis'" in sql, sql
    desfazer = ctx.modulo.sql_desfazer(
        [{"pain_hypothesis_id": HID, "organization_id": ORG_A, "sync_event_id": SEV}], corr, "3")
    assert "DELETE FROM sales_intelligence.pain_hypotheses WHERE id IN ('%s');" % HID in desfazer, \
        desfazer
    assert "DELETE FROM sales_intelligence.sync_events WHERE id IN ('%s');" % SEV in desfazer
    assert "ROLLBACK" in desfazer and "pain:rollback:%s" % corr in desfazer
    ctx.modulo.validar_sql(desfazer, permitir_remocao=True)


# ---------------------------------------------------------------------------------------
# 5. Fluxo completo na porta de roteiro
# ---------------------------------------------------------------------------------------
@item("fluxo-registra-hipotese-com-lastro")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),                     # SELECT organizations
        (0, linha_sinal(SIG_A, ORG_A), ""),                    # SELECT signals (lastro)
        (0, ctx.modulo.MARCA_REGISTRADA, ""),                  # claim + INSERT + fechamento
        (0, "", ""),                                           # agent_runs
    ])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.REGISTRADA, resultado
    assert resultado["organization_id"] == ORG_A
    assert resultado["pain_hypothesis_id"] and resultado["sync_event_id"], resultado
    assert resultado["idempotency_key"].startswith("pain:org:%s:" % ORG_A), resultado
    assert resultado["status_agent_runs"] == "COMPLETED"
    assert resultado["evidencias"] and resultado["evidencias"][0]["tipo"] == "SINAL"
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.pain_hypotheses" in sql
    assert "pain_statement" in sql and "evidence" in sql and "inferencia" in sql
    assert "'HYPOTHESIS'" in sql, "a hipotese nao foi gravada no status inicial do contrato"
    for proibida in ctx.modulo.COLUNAS_PROIBIDAS:
        assert proibida not in sql, "coluna proibida escrita: %s" % proibida
    assert "INSERT INTO sales_intelligence.agent_runs" in sql
    assert resultado["evidencias"][0]["signal_type"] == "HIRING"


@item("fluxo-nao-escreve-em-organizations")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, linha_sinal(SIG_A, ORG_A), ""),
        (0, ctx.modulo.MARCA_REGISTRADA, ""), (0, "", "")])
    agente.processar(hipotese())
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.organizations" not in sql
    assert "UPDATE sales_intelligence.organizations" not in sql
    assert "DELETE FROM sales_intelligence.organizations" not in sql
    assert "sales_intelligence.organizations" in sql, "a consulta de identidade sumiu"


@item("fluxo-replay-nao-duplica-e-nao-marca-sucesso")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, linha_sinal(SIG_A, ORG_A), ""),
        (0, "", ""),                                           # claim ja existia: nada inserido
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.JA_REGISTRADA, resultado
    assert "IDEMPOTENCIA_REPLAY" in resultado["motivos"], resultado["motivos"]
    assert resultado.get("pain_hypothesis_id") is None, "replay nao pode devolver hipotese nova"
    assert resultado["status_agent_runs"] == "COMPLETED"
    assert "INSERT INTO sales_intelligence.pain_hypotheses" in texto_do_sql(porta.chamadas)


@item("fluxo-revisao-registra-fila-humana")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B), ""),
        (0, ctx.modulo.MARCA_REVISAO, ""),                     # claim + fila humana + fechamento
        (0, "", ""),                                           # agent_runs
    ])
    resultado = agente.processar(hipotese(organizacao={"cnpj": CNPJ_A, "domain": "acme.com.br"}))
    assert resultado["veredito"] == ctx.modulo.REVISAO, resultado
    assert resultado["motivos"] == ["CONFLITO_DE_IDENTIDADE_FORTE"], resultado["motivos"]
    assert resultado["human_approval_id"], resultado
    assert resultado["revisao_registrada"] is True, resultado
    assert resultado["idempotency_key"].startswith("pain:revisao:"), resultado
    assert resultado["status_agent_runs"] == "REVIEW_REQUIRED"
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.human_approvals" in sql
    assert "'PAIN_IDENTITY_REVIEW'" in sql and "'PENDING'" in sql
    assert "INSERT INTO sales_intelligence.sync_events" in sql, \
        "a fila humana tambem passa pelo claim de idempotencia"
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert ctx.modulo.OPERACAO_REVISAO in sql, "a requisicao de revisao e' rastreavel"
    assert "INSERT INTO sales_intelligence.pain_hypotheses" not in sql, \
        "revisao nao pode gravar hipotese"
    assert len(porta.chamadas) == 3, porta.chamadas


@item("fluxo-revisao-reapresentada-nao-duplica-pedido")
def _(ctx):
    """Mesma ambiguidade reapresentada: claim ja existente => nenhum pedido novo para o humano."""
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B), ""),
        (0, "", ""),                                           # claim volta vazio (0 linhas)
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese(organizacao={"cnpj": CNPJ_A, "domain": "acme.com.br"}))
    assert resultado["veredito"] == ctx.modulo.REVISAO, resultado
    assert resultado["revisao_registrada"] is False, resultado
    assert "IDEMPOTENCIA_REPLAY" in resultado["motivos"], resultado["motivos"]


@item("fluxo-fila-humana-que-nao-registra-vira-erro")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B), ""),
        (1, "", "permission denied"),                          # human_approvals falhou
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese(organizacao={"cnpj": CNPJ_A,
                                                       "domain": "acme.com.br"}))
    assert resultado["veredito"] == ctx.modulo.ERRO, resultado
    assert any("fila humana" in m for m in resultado["motivos"]), resultado["motivos"]
    assert "INSERT INTO sales_intelligence.pain_hypotheses" not in texto_do_sql(porta.chamadas)


@item("fluxo-recusa-nao-escreve-e-audita")
def _(ctx):
    """Hipotese invalida nao vira linha nem quando a identidade casaria: recusa, nao aviso."""
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, "", "")])
    resultado = agente.processar(hipotese(evidencias=[{"tipo": "PANFLETO", "id": SIG_A}]))
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    assert resultado["status_agent_runs"] == "REJECTED"
    assert resultado["motivos"] == ["TIPO_DE_EVIDENCIA_DESCONHECIDO"], resultado["motivos"]
    sql = texto_do_sql(porta.chamadas)
    assert "INSERT INTO sales_intelligence.pain_hypotheses" not in sql, "recusa escreveu hipotese"
    assert "INSERT INTO sales_intelligence.agent_runs" in sql


@item("fluxo-sem-identificador-forte-nao-consulta-o-banco")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [(0, "", "")])
    resultado = agente.processar(hipotese(organizacao={"city": "Santos"}))
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    assert "SEM_IDENTIFICADOR_FORTE" in resultado["motivos"], resultado["motivos"]
    assert len(porta.chamadas) == 1, "sem identidade nao ha o que consultar: %s" % porta.chamadas
    assert "INSERT INTO sales_intelligence.agent_runs" in porta.chamadas[0]


@item("fluxo-organizacao-inexistente-e-recusada")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [(0, "", ""), (0, "", "")])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    assert "ORGANIZACAO_NAO_ENCONTRADA" in resultado["motivos"], resultado["motivos"]
    assert "INSERT INTO sales_intelligence.pain_hypotheses" not in texto_do_sql(porta.chamadas)


@item("fluxo-evidencia-inexistente-e-descartada")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, "", ""),                                           # signals: nao existe
        (0, "", ""),                                           # agent_runs
    ])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    assert any(d["motivo"] == "EVIDENCIA_NAO_ENCONTRADA" for d in resultado["descartados"]), \
        resultado["descartados"]
    assert "INSERT INTO sales_intelligence.pain_hypotheses" not in texto_do_sql(porta.chamadas)


@item("fluxo-recusa-hipotese-sem-lastro")
def _(ctx):
    """Hipotese sem evidencia que exista de verdade nao e' gravada (inferencia sem lastro = opiniao)."""
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, "", ""),
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    assert "SEM_EVIDENCIA_VALIDA" in resultado["motivos"], resultado["motivos"]
    assert resultado["status_agent_runs"] == "REJECTED"
    assert "INSERT INTO sales_intelligence.pain_hypotheses" not in texto_do_sql(porta.chamadas)


@item("fluxo-evidencia-de-outra-empresa-e-descartada")
def _(ctx):
    """O id existe (a leitura passaria), mas o lastro seria de outro cliente: descarta com motivo."""
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, linha_sinal(SIG_A, ORG_B), ""),                    # sinal existe, mas e' da ORG_B
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.RECUSADA, resultado
    descartes = [d["motivo"] for d in resultado["descartados"]]
    assert "EVIDENCIA_DE_OUTRA_ORGANIZACAO" in descartes, resultado["descartados"]
    assert "SEM_EVIDENCIA_VALIDA" in resultado["motivos"], resultado["motivos"]


@item("fluxo-evidencia-valida-e-invalida-misturadas")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, linha_sinal(SIG_A, ORG_A), ""),                    # so' a valida volta do banco
        (0, ctx.modulo.MARCA_REGISTRADA, ""),
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese(evidencias=[{"tipo": "SINAL", "id": SIG_A},
                                                      {"tipo": "SINAL", "id": SIG_X}]))
    assert resultado["veredito"] == ctx.modulo.REGISTRADA, resultado
    assert len(resultado["evidencias"]) == 1, resultado["evidencias"]
    assert any(d["motivo"] == "EVIDENCIA_NAO_ENCONTRADA" for d in resultado["descartados"]), \
        resultado["descartados"]


@item("fluxo-pesquisa-como-lastro-conserva-o-tipo-e-o-status")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, linha_research(RUN_A, ORG_A, "PESQUISA", "COMPLETED"), ""),  # lastro PESQUISA
        (0, ctx.modulo.MARCA_REGISTRADA, ""),
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese(evidencias=[{"tipo": "PESQUISA", "id": RUN_A}]))
    assert resultado["veredito"] == ctx.modulo.REGISTRADA, resultado
    evidencia = resultado["evidencias"][0]
    assert evidencia["tipo"] == "PESQUISA" and evidencia["research_type"] == "PESQUISA"
    assert evidencia["status"] == "COMPLETED", evidencia
    assert "research_type" in texto_do_sql(porta.chamadas)


@item("fluxo-vinculo-com-research-run-inexistente-e-descartado")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, linha_sinal(SIG_A, ORG_A), ""),
        (0, "", ""),                                           # research_runs: nao existe
        (0, ctx.modulo.MARCA_REGISTRADA, ""),
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese(research_run_id=RUN_A))
    assert resultado["veredito"] == ctx.modulo.REGISTRADA, resultado
    assert resultado["research_run_id"] is None
    assert {"campo": "research_run_id", "motivo": "RESEARCH_RUN_NAO_ENCONTRADO",
            "valor": RUN_A} in resultado["descartados"], resultado["descartados"]
    assert "NULL" in porta.chamadas[3], "o vinculo quebrado nao pode ser escrito"


@item("fluxo-vinculo-com-research-run-de-outra-empresa-e-descartado")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, linha_sinal(SIG_A, ORG_A), ""),
        (0, linha_research(RUN_A, ORG_B), ""),                 # run existe, mas de outra empresa
        (0, ctx.modulo.MARCA_REGISTRADA, ""),
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese(research_run_id=RUN_A))
    assert resultado["veredito"] == ctx.modulo.REGISTRADA, resultado
    assert resultado["research_run_id"] is None
    assert any(d["motivo"] == "RESEARCH_RUN_DE_OUTRA_ORGANIZACAO"
               for d in resultado["descartados"]), resultado["descartados"]


@item("fluxo-vinculo-com-research-run-real-e-escrito")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""),
        (0, linha_sinal(SIG_A, ORG_A), ""),
        (0, linha_research(RUN_A, ORG_A), ""),
        (0, ctx.modulo.MARCA_REGISTRADA, ""),
        (0, "", ""),
    ])
    resultado = agente.processar(hipotese(research_run_id=RUN_A))
    assert resultado["veredito"] == ctx.modulo.REGISTRADA, resultado
    assert resultado["research_run_id"] == RUN_A
    assert "'%s'" % RUN_A in porta.chamadas[3], porta.chamadas[3]


@item("fluxo-auditoria-com-o-correlation-id-do-lote")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, linha_sinal(SIG_A, ORG_A), ""),
        (0, ctx.modulo.MARCA_REGISTRADA, ""), (0, "", "")],
        correlation_id="dddddddd-0000-4000-8000-000000000001")
    resultado = agente.processar(hipotese())
    assert resultado["agent_run_id"] and resultado["auditoria_registrada"] is True
    auditoria = porta.chamadas[-1]
    assert "INSERT INTO sales_intelligence.agent_runs" in auditoria
    assert "dddddddd-0000-4000-8000-000000000001" in auditoria
    assert "COMPLETED" in auditoria and '"veredito": "REGISTRADA"' in auditoria
    assert resultado["pain_hypothesis_id"] in auditoria, \
        "a auditoria nao aponta a hipotese (desfazer depende)"


@item("fluxo-auditoria-que-nao-registra-vira-erro")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (0, linha_organizacao(ORG_A), ""), (0, linha_sinal(SIG_A, ORG_A), ""),
        (0, ctx.modulo.MARCA_REGISTRADA, ""), (1, "", "permission denied")])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.ERRO, resultado
    assert resultado["auditoria_registrada"] is False
    assert any("AUDITORIA_NAO_REGISTRADA" in m for m in resultado["motivos"]), resultado["motivos"]


@item("fluxo-porta-indisponivel-vira-erro-sem-escrever")
def _(ctx):
    agente, porta = agente_com_roteiro(ctx, [
        (1, "", "connection refused"), (0, "", "")])
    resultado = agente.processar(hipotese())
    assert resultado["veredito"] == ctx.modulo.ERRO, resultado
    assert resultado["erro"]["tipo"] == "PortaIndisponivel", resultado.get("erro")
    assert "INSERT INTO sales_intelligence.pain_hypotheses" not in texto_do_sql(porta.chamadas)


@item("fluxo-desfazer-dry-run-e-confirmo")
def _(ctx):
    item_da_rodada = json.dumps({"pain_hypothesis_id": HID, "organization_id": ORG_A,
                                 "sync_event_id": SEV})
    agente, porta = agente_com_roteiro(ctx, [(0, item_da_rodada, "")])
    seco = agente.desfazer("dddddddd-0000-4000-8000-000000000001")
    assert seco["dry_run"] is True and seco["apagados"] == 0, seco
    assert seco["pain_hypotheses"] == [HID], seco
    assert "DELETE" not in texto_do_sql(porta.chamadas), "dry-run apagou"
    agente2, porta2 = agente_com_roteiro(ctx, [(0, item_da_rodada, ""), (0, "", "")])
    aplicado = agente2.desfazer("dddddddd-0000-4000-8000-000000000001", confirmo=True)
    assert aplicado["dry_run"] is False and aplicado["apagados"] == 1, aplicado
    sql = texto_do_sql(porta2.chamadas)
    assert "DELETE FROM sales_intelligence.pain_hypotheses WHERE id IN" in sql
    assert "DELETE FROM sales_intelligence.sync_events WHERE id IN" in sql
    assert "ROLLBACK" in sql
    assert "UPDATE sales_intelligence.organizations" not in sql
    assert porta2.remocoes[-1] is True, "o DELETE do desfazer saiu sem --confirmo declarado"
    sem_itens, porta3 = agente_com_roteiro(ctx, [(0, "", "")])
    vazio = sem_itens.desfazer("dddddddd-0000-4000-8000-000000000002", confirmo=True)
    assert vazio["apagados"] == 0 and len(porta3.chamadas) == 1, (vazio, porta3.chamadas)


@item("ambiente-recusado-por-desenho")
def _(ctx):
    for ambiente, recusa in (("prod", True), ("", True), (None, True), ("staging", True),
                             ("dev", False), ("homolog", False)):
        agente = ctx.modulo.PainHypothesis(porta=PortaRoteiro(rota=[]), raiz=ctx.raiz,
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
                     "import socket", "urlopen"):
        assert proibido not in codigo, "o agente carrega acesso de rede: %s" % proibido
    for proibido in ("openai", "anthropic", "httpx"):
        assert proibido not in codigo, "o agente carrega cliente de LLM: %s" % proibido
    assert "executado\": False" in codigo, "o gate do JEV nao declara que nao executa"


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
     r'''        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {hipoteses} ({cols})\n"''',
     r'''        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {hipoteses} ({cols})\n"''',
     ["sql-de-ingestao-tem-o-claim-idempotente"]),
    ("remocao-da-ancora-do-fechamento",
     r'''        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {hipoteses} WHERE id = {hid})\n"''',
     r'''        "WHERE idempotency_key = {chave}\n"''',
     ["sql-de-ingestao-ancora-o-fechamento-na-hipotese-desta-rodada"]),
    ("lastro-nao-conferido",
     r'''            registro = base.get(evidencia["id"])
            if registro is None:''',
     r'''            registro = base.get(evidencia["id"]) or {"organization_id": organizacao_id}
            if False:''',
     ["fluxo-recusa-hipotese-sem-lastro"]),
    ("lastro-de-outra-empresa-aceito",
     r'''            if registro.get("organization_id") != organizacao_id:''',
     r'''            if False:''',
     ["fluxo-evidencia-de-outra-empresa-e-descartada"]),
    ("status-chumbado-como-validado",
     r'''STATUS_INICIAL = "HYPOTHESIS"''',
     r'''STATUS_INICIAL = "VALIDATED"''',
     ["status-escrito-e-o-inicial-do-contrato"]),
    ("dor-vazia-aceita",
     r'''    if not texto:
        return None, "SEM_DOR_DECLARADA"''',
     r'''    if False:
        return None, "SEM_DOR_DECLARADA"''',
     ["dor-obrigatoria-e-acima-do-limite-recusa"]),
    ("categoria-desconhecida-aceita",
     r'''    if texto not in CATEGORIAS_DE_DOR:''',
     r'''    if False:''',
     ["categoria-desconhecida-recusa-e-ausente-fica-nula"]),
    ("proibida-de-impacto-removida-da-guarda",
     r'''        if proibidas:''',
     r'''        if False:''',
     ["guarda-recusa-coluna-de-impacto-no-insert"]),
    ("guarda-deixa-passar-ddl",
     r'''    if _DDL.search(codigo):''',
     r'''    if False and _DDL.search(codigo):''',
     ["guarda-recusa-ddl"]),
    ("guarda-aceita-tabela-nao-declarada",
     r'''            if tabela not in TABELAS_PERMITIDAS:''',
     r'''            if False:''',
     ["guarda-recusa-tabela-nao-declarada"]),
    ("guarda-aceita-escrita-em-organizations",
     r'''            if tabela == TABELA_ORGANIZACOES:''',
     r'''            if False:''',
     ["guarda-recusa-escrita-em-organizations"]),
    ("guarda-aceita-delete-fora-do-desfazer",
     r'''            if operacao == "delete" and not permitir_remocao:''',
     r'''            if False:''',
     ["guarda-recusa-delete-fora-do-desfazer"]),
    ("guarda-aceita-insert-sem-coluna-obrigatoria",
     r'''        if faltando:
            raise GuardaDeEscritaViolada(
                "INSERT de hipotese sem coluna obrigatoria do DDL: %s" % ", ".join(faltando))''',
     r'''        if False:
            raise GuardaDeEscritaViolada(
                "INSERT de hipotese sem coluna obrigatoria do DDL: %s" % ", ".join(faltando))''',
     ["guarda-recusa-insert-de-hipotese-sem-organization-ou-pain"]),
    ("guarda-le-o-literal-como-codigo",
     r'''    return _LITERAL.sub("''", sql)''',
     r'''    return sql''',
     ["guarda-nao-confunde-prosa-com-ddl"]),
    ("campo-nao-declarado-liberado",
     r'''    for campo in hipotese:
        if campo not in CAMPOS_DA_HIPOTESE:''',
     r'''    for campo in hipotese:
        if False:''',
     ["campo-nao-declarado-e-descartado"]),
    ("derivado-liberado",
     r'''        if campo in hipotese and _texto(hipotese.get(campo)):''',
     r'''        if False:''',
     ["campos-derivados-na-entrada-sao-descartados-com-derivado-nao-aceito"]),
    ("confianca-fora-da-faixa-aceita",
     r'''    if not 0 <= numero <= 1:''',
     r'''    if False:''',
     ["confianca-fora-da-faixa-e-descartada-e-ausente-fica-nula"]),
    ("research-run-id-invalido-aceito",
     r'''    if not _UUID_V4.match(texto):
        return None, "RESEARCH_RUN_ID_INVALIDO"''',
     r'''    if False:
        return None, "RESEARCH_RUN_ID_INVALIDO"''',
     ["research-run-id-de-formato-invalido-e-descartado"]),
    ("evidencia-duplicada-aceita",
     r'''        if any(i["tipo"] == tipo and i["id"] == identificador for i in normalizadas):''',
     r'''        if False:''',
     ["evidencia-duplicada-e-descartada"]),
    ("sem-evidencia-valida-aceita",
     r'''    if evidencias_validas == 0:
        return RECUSADA, "SEM_EVIDENCIA_VALIDA"''',
     r'''    if False:
        return RECUSADA, "SEM_EVIDENCIA_VALIDA"''',
     ["veredito-recusa-sem-evidencia-valida"]),
    ("revisao-nao-desviada",
     r'''    if len(organizacoes_casadas) > 1:''',
     r'''    if False:''',
     ["veredito-vai-para-revisao-com-duas-ou-mais-casadas"]),
    ("zero-casadas-aceito",
     r'''    if len(organizacoes_casadas) == 0:''',
     r'''    if False:''',
     ["veredito-recusa-zero-casadas"]),
    ("problema-nao-vence-a-casada",
     r'''    if problemas:
        return RECUSADA, problemas[0]''',
     r'''    if False:
        return RECUSADA, problemas[0]''',
     ["problema-de-entrada-vence-a-casada"]),
    ("fila-humana-sem-conferir-o-rc",
     r'''                if rc_rev != 0:''',
     r'''                if False:''',
     ["fluxo-fila-humana-que-nao-registra-vira-erro"]),
    ("auditoria-sem-conferir-o-rc",
     r'''        if rc_run != 0:''',
     r'''        if False:''',
     ["fluxo-auditoria-que-nao-registra-vira-erro"]),
    ("raiz-por-profundidade-do-arquivo",
     r'''    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():
                return pasta
    return Path.cwd()''',
     r'''    return Path(__file__).resolve().parents[3]''',
     ["raiz-vem-do-marcador-nao-da-profundidade"]),
    ("segunda-copia-da-regra-de-identidade",
     r'''def validar_dor(valor) -> tuple:''',
     r'''def cnpj_valido(valor):
    return True


def validar_dor(valor) -> tuple:''',
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
    temporario = Path(tempfile.mkdtemp(prefix="pain-autoteste-"))
    try:
        for mutacao in MUTACOES:
            nome = mutacao[0]
            # A copia vive um nivel ABAIXO do tempdir: assim ela importa de qualquer TMPDIR.
            copia = temporario / "mut" / ("pain-%s.py" % nome)
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
    p = argparse.ArgumentParser(
        description="Suite do agente Pain Hypothesis v1 (TRE-W4-E04-T01)")
    p.add_argument("--codigo", default=str(CODIGO_PADRAO),
                   help="caminho de outro pain_hypothesis.py")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not ITENS:
        print("FALHOU a suite nao executou nenhum item")
        return 1
    modulo = carregar_modulo(Path(args.codigo))
    print("=== SUITE DO AGENTE PAIN HYPOTHESIS v1 (%s) ===" % args.codigo)
    ok, falhas, _ = executar_suite(modulo)
    print("\nRESULTADO: PAIN_SUITE_%s (%d itens, %d falhas)"
          % ("OK" if falhas == 0 else "FALHOU", ok + falhas, falhas))
    sucesso = falhas == 0
    if args.autoteste:
        sucesso = autoteste(Path(args.codigo)) and sucesso
        print("RESULTADO FINAL: PAIN_%s" % ("OK" if sucesso else "FALHOU"))
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
