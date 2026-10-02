#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do SCORE DATA QUALITY v1 (card TRE-W5-E04-T01) — um comando, um veredito.

    python3 scripts/scores/verificar_score_data_quality.py
    python3 scripts/scores/verificar_score_data_quality.py --autoteste
    python3 scripts/scores/verificar_score_data_quality.py --codigo <caminho de outro data_quality.py>

O que ela prova (SEM banco e SEM rede): o modelo v1 declarado no contrato do score nao divergiu do
codigo (pesos dos componentes, campos da completude, parcelas da confiabilidade, colunas escritas);
o score_type/score_version existe no Data Contract V1.0 e as colunas escritas existem no DDL
congelado; a completude perde EXATAMENTE o peso do campo ausente e campo vazio (ou so espacos) conta
como ausente, enquanto zero NUMERICO conta como dado; a validade julga SOMENTE o que existe (sem
dupla punicao) e marca o motivo do reprovado, sem corrigir o dado que mede; a confiabilidade e
LASTRO medido no banco (research COMPLETED + source_count) e nao prosa; a atualidade usa a data do
DADO e nao `updated_at` (o score nao rejuvenesce a si mesmo); a medicao e reproduzivel (mesmo estado
+ mesma referencia => mesmo valor e mesmo hash) e o replay NAO duplica porque a idempotencia esta no
SQL; a guarda de escrita recusa DDL, tabela fora da lista, UPDATE de outra coluna de organizations,
UPDATE sem WHERE, UPDATE/DELETE em scores, INSERT sem `score_version` e INSERT de OUTRO score; o
`prod` e recusado e o fluxo completo (medir, espelhar, replay, recusar identidade, desfazer) se
comporta como o contrato do card — medido numa PORTA DE ROTEIRO (implementacao da porta declarada,
nao dublê de biblioteca).

AUTOTESTE (`--autoteste`): cada mutacao e aplicada a uma COPIA do arquivo apontado por `--codigo`
(o canonico, por padrao — as duas opcoes andam juntas de proposito: mutar o canonico enquanto se
testa outro arquivo e' prova contra alvo errado) e a suite tem de REPROVAR o item correspondente —
mutacao que passa em silencio e buraco de verificacao. Mutacao que nao se aplica (ancora de texto
mudou), que nao declara item nenhum ou que declara item INEXISTENTE na suite tambem reprova: e
buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = DQ_SUITE_OK · 1 = DQ_SUITE_FALHOU (o log aponta o item) · 2 = uso
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
from datetime import date
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_PADRAO = RAIZ / "hermes/scores/data_quality/data_quality.py"
CONTRATO_SCORE = RAIZ / "hermes/scores/data_quality/score-data-quality-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC_ARQUITETURA = RAIZ / "docs/architecture/score-data-quality-v1.md"
RUNBOOK = RAIZ / "docs/runbooks/score-data-quality.md"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO = RAIZ / "hermes/scores/data_quality/exemplos/organizacoes-exemplo.jsonl"
ACEITE = RAIZ / "scripts/scores/teste_data_quality_aceite.sh"

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "aaaaaaaa-0000-4000-8000-000000000002"
CNPJ_A = "11.222.333/0001-81"
REFERENCIA = date(2026, 10, 2)

ITENS = []


def item(nome):
    def decorador(funcao):
        ITENS.append((nome, funcao))
        return funcao
    return decorador


def organizacao_completa(**extra):
    base = {
        "id": ORG_A, "legal_name": "ACME Distribuidora LTDA", "trade_name": "ACME",
        "cnpj": CNPJ_A, "domain": "acme.com.br", "website_url": "https://acme.com.br",
        "linkedin_url": "https://www.linkedin.com/company/acme",
        "industry_name": "Distribuidora", "industry_code": "G46",
        "employee_count": 420, "employee_band": "300_499", "revenue_estimate": 1200000,
        "unit_count": 3, "city": "Sao Paulo", "state": "SP", "country_code": "BR",
        "business_model": "B2B", "status": "DISCOVERED", "source": "WEB",
        "data_quality_score": None, "created_at": "2026-09-01T10:00:00+00:00",
    }
    base.update(extra)
    return base


def run_completed(source_count=7, completed_at="2026-09-20T10:00:00+00:00"):
    return {"id": "33333333-0000-4000-8000-000000000001", "status": "COMPLETED",
            "source_count": source_count, "completed_at": completed_at, "confidence": 0.9}


class PortaRoteiro:
    """Porta de banco roteirizada: responde o que a rota manda e registra TODO SQL executado.

    Nao e duble de biblioteca: e a implementacao da porta declarada pelo modulo (mesma interface),
    com respostas roteirizadas — o que se mede e o SQL que o score GERA e o que ele FAZ com a
    resposta.
    """

    def __init__(self, modulo, rota=None):
        self.modulo = modulo
        self.rota = list(rota or [])
        self.executados = []

    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        self.modulo.validar_sql(sql, permitir_remocao=permitir_remocao)
        self.executados.append(sql)
        for chave, resposta in self.rota:
            if chave in sql:
                return resposta
        # A auditoria sempre existe no roteiro: a rodada grava uma linha por organizacao e a
        # suite mede QUANTAS foram gravadas, nao se a porta falhou.
        if "INSERT INTO sales_intelligence.agent_runs" in sql:
            return (0, "INSERT 0 1\n", "")
        return (1, "", "rota ausente para: %s" % " ".join(sql.split())[:80])

    def escritas(self) -> list:
        return [s for s in self.executados
                if re.search(r"\b(INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM)\b", s, re.I)]

    def contem(self, pedaco: str) -> bool:
        return any(pedaco in s for s in self.executados)


def ok(texto: str) -> tuple:
    return (0, texto + "\n", "")


class Contexto:
    def __init__(self, modulo, raiz=RAIZ):
        self.modulo = modulo
        self.raiz = Path(raiz)
        self.contrato = json.loads(CONTRATO_SCORE.read_text(encoding="utf-8"))
        self.contrato_dados = json.loads(CONTRATO_DADOS.read_text(encoding="utf-8"))
        self.ddl = MIGRATION.read_text(encoding="utf-8")
        self.identidade = modulo.carregar_identidade(self.raiz)
        modulo.preparar_literais(self.identidade)
        # o codigo SOB TESTE (a copia mutada quando ha' prova de dente), nao o arquivo canonico
        self.codigo = Path(modulo.__file__).read_text(encoding="utf-8")
        self.agente = modulo.ScoreDataQuality(porta=PortaRoteiro(modulo), raiz=self.raiz,
                                              referencia=REFERENCIA, ambiente="dev")
        self.fortes = self.agente.fortes

    def colunas(self, tabela: str) -> set:
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

    def medir(self, organizacao, runs=None, referencia=REFERENCIA):
        return self.modulo.calcular(organizacao, runs if runs is not None else [],
                                    referencia, self.identidade)

    def agente_com(self, rota):
        return self.modulo.ScoreDataQuality(porta=PortaRoteiro(self.modulo, rota), raiz=self.raiz,
                                            referencia=REFERENCIA, ambiente="dev",
                                            correlation_id="44444444-0000-4000-8000-000000000001")


# ---------------------------------------------------------------------------------------
# ITENS
# ---------------------------------------------------------------------------------------
@item("contrato-do-score-existe-e-e-json")
def _(ctx):
    assert CONTRATO_SCORE.is_file(), "contrato do score ausente: %s" % CONTRATO_SCORE
    assert ctx.contrato["card"] == "TRE-W5-E04-T01"
    assert ctx.contrato["identidade_do_score"]["score_type"] == ctx.modulo.SCORE_TIPO
    assert ctx.contrato["identidade_do_score"]["score_version"] == ctx.modulo.SCORE_VERSAO


@item("modelo-declara-pesos-que-somam-1")
def _(ctx):
    componentes = ctx.contrato["modelo_v1"]["componentes"]
    completude = ctx.contrato["modelo_v1"]["completude_campos"]
    parcelas = ctx.contrato["modelo_v1"]["confiabilidade"]["parcelas"]
    for nome, bloco in (("componentes", componentes), ("completude_campos", completude),
                        ("parcelas de confiabilidade", parcelas)):
        soma = sum(Decimal(str(v)) for v in bloco.values())
        assert soma == Decimal("1.00"), "%s soma %s (exige 1.00)" % (nome, soma)


@item("codigo-igual-ao-contrato-do-score")
def _(ctx):
    assert dict(ctx.contrato["modelo_v1"]["componentes"]) == \
        {k: float(v) for k, v in ctx.modulo.COMPONENTES}
    assert dict(ctx.contrato["modelo_v1"]["completude_campos"]) == \
        {k: float(v) for k, v in ctx.modulo.COMPLETUDE_CAMPOS}
    assert dict(ctx.contrato["modelo_v1"]["confiabilidade"]["parcelas"]) == \
        {k: float(v) for k, v in ctx.modulo.CONFIABILIDADE_PARCELAS}
    assert ctx.contrato["modelo_v1"]["confiabilidade"]["fontes_para_cobertura_maxima"] == \
        ctx.modulo.FONTES_PARA_COBERTURA_MAXIMA
    assert ctx.contrato["modelo_v1"]["atualidade"]["fresco_dias"] == ctx.modulo.ATUALIDADE_FRESCO_DIAS
    assert ctx.contrato["modelo_v1"]["atualidade"]["expirado_dias"] == \
        ctx.modulo.ATUALIDADE_EXPIRADO_DIAS
    assert ctx.contrato["escrita"]["colunas_scores"] == list(ctx.modulo.COLUNAS_SCORES)
    assert ctx.contrato["escrita"]["colunas_organizations_escritas"] == \
        list(ctx.modulo.COLUNAS_ORGANIZACOES_ESCRITAS)
    assert ctx.contrato["guardrails"]["ambientes_permitidos"] == list(ctx.modulo.AMBIENTES_PERMITIDOS)


@item("score-type-e-versao-existentes-no-contrato-de-dados")
def _(ctx):
    scores = ctx.contrato_dados["scores"]
    assert ctx.modulo.SCORE_TIPO in scores["types"], "score_type fora do contrato de dados"
    assert scores["requires_version_column"] == "score_version"
    assert Decimal("0.10") == Decimal(str(scores["priority_weights"]["DATA_QUALITY"]))


@item("colunas-escritas-existentes-no-ddl-congelado")
def _(ctx):
    scores = ctx.colunas("sales_intelligence.scores")
    organizations = ctx.colunas("sales_intelligence.organizations")
    agent_runs = ctx.colunas("sales_intelligence.agent_runs")
    faltando = [c for c in ctx.modulo.COLUNAS_SCORES if c not in scores]
    assert not faltando, "coluna escrita em scores fora do DDL: %s" % faltando
    for obrigatoria in ctx.modulo.COLUNAS_SCORES_OBRIGATORIAS:
        assert obrigatoria in ctx.modulo.COLUNAS_SCORES, obrigatoria
    for coluna in ctx.modulo.COLUNAS_ORGANIZACOES_ESCRITAS:
        assert coluna in organizations, "coluna escrita em organizations fora do DDL: %s" % coluna
    assert "updated_at" not in ctx.modulo.COLUNAS_ORGANIZACOES_ESCRITAS
    faltando_runs = [c for c in ctx.modulo.COLUNAS_AGENT_RUNS if c not in agent_runs]
    assert not faltando_runs, "coluna escrita em agent_runs fora do DDL: %s" % faltando_runs


@item("completude-perde-exatamente-o-peso-do-campo-ausente")
def _(ctx):
    cheia = ctx.medir(organizacao_completa())
    assert cheia["componentes"]["completude"] == "1.0000", cheia["componentes"]
    for campo, peso in (("cnpj", "0.15"), ("domain", "0.15"), ("business_model", "0.05")):
        organizacao = organizacao_completa(**{campo: None})
        medicao = ctx.medir(organizacao)
        esperado = Decimal("1.00") - Decimal(peso)
        obtido = Decimal(medicao["componentes"]["completude"])
        assert obtido == esperado, "%s: completude %s (esperado %s)" % (campo, obtido, esperado)


@item("campo-vazio-e-ausente-mas-zero-e-dado")
def _(ctx):
    vazio = ctx.medir(organizacao_completa(cnpj="   ", domain=""))
    assert Decimal(vazio["componentes"]["completude"]) == Decimal("1.00") - Decimal("0.30")
    zero = ctx.medir(organizacao_completa(unit_count=0))
    assert zero["componentes"]["completude"] == "1.0000", \
        "zero numerico virou ausencia (nao e: zero e dado)"


@item("validade-nao-pune-duas-vezes-o-campo-ausente")
def _(ctx):
    # cnpj AUSENTE: ja foi punido na completude e NAO pode derrubar a validade (sem dupla punicao)
    sem_cnpj = organizacao_completa(cnpj=None)
    # cnpj INVALIDO: existe, entao a validade julga e reprova
    invalido = organizacao_completa(cnpj="11.222.333/0001-99")
    a = Decimal(ctx.medir(sem_cnpj)["componentes"]["validade"])
    b = Decimal(ctx.medir(invalido)["componentes"]["validade"])
    assert a == Decimal("1.0000"), "campo ausente derrubou a validade (dupla punicao): %s" % a
    assert b < a, "cnpj invalido nao derrubou a validade: %s x %s" % (b, a)


@item("validade-marca-o-motivo-sem-corrigir-o-dado")
def _(ctx):
    medicao = ctx.medir(organizacao_completa(cnpj="11.222.333/0001-99"))
    checks = {c["check"]: c for c in medicao["inputs"]["validade"]["checks"]}
    assert checks["cnpj"]["aprovado"] is False
    assert checks["cnpj"]["motivo"] == "CNPJ_INVALIDO"
    assert medicao["inputs"]["completude"]["valores"]["cnpj"] == "11.222.333/0001-99", \
        "o score alterou o dado que mede (ele mede, nao conserta)"
    # nenhuma escrita toca o dado medido: a unica coluna de organizations escrita e o espelho
    agente = ctx.agente_com([])
    sql = ctx.modulo.sql_gravar_score(ORG_A, ORG_A, medicao, "2026-10-02T12:00:00+00:00")
    assert ctx.modulo.COLUNAS_ORGANIZACOES_ESCRITAS == ("data_quality_score",)
    assert "SET data_quality_score = " in sql
    assert "legal_name" not in sql and "updated_at" not in sql
    assert "SET data_quality_score = " in sql.split("UPDATE sales_intelligence.organizations", 1)[1]
    assert agente is not None


@item("validade-julga-coerencia-de-porte-e-de-dominio")
def _(ctx):
    incoerente = ctx.medir(organizacao_completa(employee_count=420, employee_band="150_299"))
    checks = {c["check"]: c for c in incoerente["inputs"]["validade"]["checks"]}
    assert checks["porte_x_contagem"]["aprovado"] is False
    assert checks["porte_x_contagem"]["motivo"] == "BANDA_INCOERENTE_COM_CONTAGEM"
    unknown = ctx.medir(organizacao_completa(employee_count=420, employee_band="UNKNOWN"))
    checks = {c["check"]: c for c in unknown["inputs"]["validade"]["checks"]}
    assert checks["porte_x_contagem"]["aprovado"] is False, \
        "UNKNOWN com contagem conhecida e incoerencia"
    outro_host = ctx.medir(organizacao_completa(website_url="https://outra.com.br"))
    checks = {c["check"]: c for c in outro_host["inputs"]["validade"]["checks"]}
    assert checks["domain_x_website"]["aprovado"] is False
    subdominio = ctx.medir(organizacao_completa(website_url="https://www.acme.com.br"))
    checks = {c["check"]: c for c in subdominio["inputs"]["validade"]["checks"]}
    assert checks["domain_x_website"]["aprovado"] is True


@item("confiabilidade-e-lastro-no-banco-nao-prosa")
def _(ctx):
    sem_pesquisa = ctx.medir(organizacao_completa(), runs=[])
    conf = sem_pesquisa["inputs"]["confiabilidade"]
    assert Decimal(conf["parcelas"]["pesquisa_existente"]) == Decimal("0")
    assert Decimal(conf["parcelas"]["cobertura_de_fontes"]) == Decimal("0")
    assert Decimal(sem_pesquisa["componentes"]["confiabilidade"]) == Decimal("0.40"), \
        "sem pesquisa o score so pode creditar a fonte declarada"
    fonte_fora = ctx.medir(organizacao_completa(source="BLOG"), runs=[run_completed()])
    conf = fonte_fora["inputs"]["confiabilidade"]
    assert conf["fonte_no_vocabulario"] is False
    assert Decimal(conf["parcelas"]["fonte_declarada"]) == Decimal("0")
    uma_fonte = ctx.medir(organizacao_completa(), runs=[run_completed(source_count=1)])
    assert Decimal(uma_fonte["inputs"]["confiabilidade"]["parcelas"]["cobertura_de_fontes"]) == \
        Decimal("1") / Decimal("3")
    muitas = ctx.medir(organizacao_completa(), runs=[run_completed(source_count=9)])
    assert Decimal(muitas["inputs"]["confiabilidade"]["parcelas"]["cobertura_de_fontes"]) == \
        Decimal("1"), "a cobertura de fontes tem de saturar (nao cresce sem limite)"
    pendente = ctx.medir(organizacao_completa(), runs=[dict(run_completed(), status="PENDING")])
    assert Decimal(pendente["inputs"]["confiabilidade"]["parcelas"]["pesquisa_existente"]) == \
        Decimal("0"), "pesquisa nao concluida nao e lastro"


@item("atualidade-usa-a-data-do-dado-e-nunca-updated-at")
def _(ctx):
    assert "updated_at" not in ctx.modulo.COLUNAS_SCORES
    assert "updated_at" not in ctx.modulo.COLUNAS_ORGANIZACOES_ESCRITAS
    assert "updated_at" not in ctx.modulo._CAMPOS_ORGANIZACAO, \
        "o score le updated_at: isso faria a propria escrita rejuvenescer a nota"
    assert "updated_at" not in ctx.modulo.sql_ler_organizacao(ORG_A)
    fresco = ctx.medir(organizacao_completa(created_at="2026-09-20T10:00:00+00:00"), runs=[])
    assert fresco["componentes"]["atualidade"] == "1.0000"
    velho = ctx.medir(organizacao_completa(created_at="2024-01-01T00:00:00+00:00"), runs=[])
    assert velho["componentes"]["atualidade"] == "0.0000", velho["componentes"]
    # a data do DADO mais recente manda: pesquisa recente rejuvenesce a empresa antiga
    com_pesquisa = ctx.medir(organizacao_completa(created_at="2025-01-01T00:00:00+00:00"),
                             runs=[run_completed(completed_at="2026-09-30T00:00:00+00:00")])
    assert com_pesquisa["inputs"]["atualidade"]["referencia_de_dados"] == "2026-09-30"
    assert com_pesquisa["componentes"]["atualidade"] == "1.0000"
    meio = ctx.medir(organizacao_completa(created_at="2026-01-01T00:00:00+00:00"), runs=[])
    valor = Decimal(meio["componentes"]["atualidade"])
    assert Decimal("0") < valor < Decimal("1"), valor


@item("medicao-e-reproduzivel-e-o-hash-cobre-o-que-importa")
def _(ctx):
    um = ctx.medir(organizacao_completa(), runs=[run_completed()])
    dois = ctx.medir(organizacao_completa(), runs=[run_completed()])
    assert um["valor"] == dois["valor"] and um["inputs_sha256"] == dois["inputs_sha256"]
    outro_dado = ctx.medir(organizacao_completa(city="Campinas"), runs=[run_completed()])
    assert outro_dado["inputs_sha256"] != um["inputs_sha256"], "mudar o dado tem de mudar o hash"
    outra_data = ctx.medir(organizacao_completa(), runs=[run_completed()],
                           referencia=date(2026, 10, 3))
    assert outra_data["inputs_sha256"] != um["inputs_sha256"], \
        "mudar a referencia de calculo tem de mudar o hash (a medicao e datada)"
    assert um["inputs_sha256"] == um["explanation"]["inputs_sha256"]


@item("valor-na-faixa-e-monotono-no-dado")
def _(ctx):
    amostras = [organizacao_completa(), organizacao_completa(cnpj=None, domain=None, city=None),
                {"id": ORG_B, "status": "DISCOVERED", "created_at": "2026-09-30T00:00:00+00:00"}]
    for amostra in amostras:
        medicao = ctx.medir(amostra, runs=[run_completed()])
        valor = Decimal(medicao["valor"])
        assert Decimal("0") <= valor <= Decimal("100"), valor
        assert re.match(r"^\d{1,3}\.\d{2}$", medicao["valor"]), medicao["valor"]
    cheia = Decimal(ctx.medir(organizacao_completa(), runs=[run_completed()])["valor"])
    magra = Decimal(ctx.medir(organizacao_completa(cnpj=None, domain=None, city=None, state=None,
                                                  website_url=None, linkedin_url=None,
                                                  industry_name=None, employee_count=None,
                                                  employee_band=None, revenue_estimate=None,
                                                  unit_count=None, business_model=None),
                             runs=[run_completed()])["valor"])
    assert cheia > magra, "mais dado preenchido tem de valer mais: %s x %s" % (cheia, magra)


@item("gravacao-insere-medicao-e-espelha-sem-tocar-updated-at")
def _(ctx):
    medicao = ctx.medir(organizacao_completa(), runs=[run_completed()])
    sql = ctx.modulo.sql_gravar_score(ORG_A, ORG_A, medicao, "2026-10-02T12:00:00+00:00")
    ctx.modulo.validar_sql(sql)
    assert "INSERT INTO sales_intelligence.scores" in sql
    assert "UPDATE sales_intelligence.organizations" in sql
    assert "SET data_quality_score = " in sql
    assert "updated_at" not in sql
    assert "'DATA_QUALITY'" in sql and "'v1.0'" in sql
    guarda = "WHERE NOT EXISTS (\n    SELECT 1 FROM sales_intelligence.scores s"
    assert guarda in sql and "inputs_sha256" in sql, \
        "a idempotencia do replay tem de viver na GUARDA do SQL (nao na prosa do relatorio)"


@item("fluxo-escreve-medicao-e-espelho-e-e-idempotente-no-replay")
def _(ctx):
    rota = [("SELECT json_build_object", ok(json.dumps(organizacao_completa()))),
            ("FROM sales_intelligence.research_runs", ok(json.dumps([run_completed()]))),
            ("SELECT json_build_object('id', s.id", ok("")),
            ("WITH novo AS", ok("ESCRITO"))]
    agente = ctx.agente_com(rota)
    resultado = agente.processar({"organizacao_id": ORG_A})
    assert resultado["veredito"] == "ESCRITO", resultado
    assert resultado["valor"] and resultado["score_id"]
    assert any("INSERT INTO sales_intelligence.scores" in s for s in agente.porta.escritas())
    assert any("UPDATE sales_intelligence.organizations" in s for s in agente.porta.escritas())
    assert any("INSERT INTO sales_intelligence.agent_runs" in s for s in agente.porta.escritas())
    # Replay: a guarda do SQL (NOT EXISTS sobre o ULTIMO DATA_QUALITY/v1.0 da empresa + o mesmo
    # inputs_sha256) e quem barra a segunda linha — o banco responde JA_EXISTE e o veredito muda.
    # ZERO DUPLICATA se mede no banco de verdade (aceite, itens `replay-nao-duplica-campos-*`);
    # aqui se mede que a rodada NAO inventa escrita quando a guarda barra e que a guarda existe.
    rota_replay = list(rota[:-1]) + [("WITH novo AS", ok("JA_EXISTE"))]
    agente2 = ctx.agente_com(rota_replay)
    resultado2 = agente2.processar({"organizacao_id": ORG_A})
    assert resultado2["veredito"] == "JA_EXISTE", resultado2
    assert "IDEMPOTENCIA_REPLAY" in resultado2["motivos"]
    write_sql = [s for s in agente2.porta.executados if "WITH novo AS" in s]
    assert len(write_sql) == 1, "mais de uma instrucao de gravacao por organizacao"
    assert "NOT EXISTS" in write_sql[0] and "inputs_sha256" in write_sql[0], \
        "a gravacao precisa carregar a guarda de idempotencia no proprio SQL"
    assert resultado2["status_agent_runs"] == "COMPLETED"


@item("fluxo-recusa-identidade-desconhecida-e-ambigua-sem-fila-humana")
def _(ctx):
    rota = [("SELECT json_build_object", ok(""))]
    agente = ctx.agente_com(rota)
    resultado = agente.processar({"organizacao": {"cnpj": CNPJ_A}})
    assert resultado["veredito"] == "RECUSADA", resultado
    assert "ORGANIZACAO_NAO_ENCONTRADA" in resultado["motivos"]
    assert not any("INSERT INTO sales_intelligence.scores" in s for s in agente.porta.escritas())
    duas = [json.dumps(organizacao_completa()),
            json.dumps(organizacao_completa(id=ORG_B, domain="acme.com"))]
    agente2 = ctx.agente_com([("SELECT json_build_object", ok("\n".join(duas)))])
    resultado2 = agente2.processar({"organizacao": {"domain": "acme.com.br"}})
    assert resultado2["veredito"] == "RECUSADA"
    assert any(m.startswith("IDENTIDADE_AMBIGUA") for m in resultado2["motivos"]), resultado2["motivos"]
    assert not any("human_approvals" in s for s in agente2.porta.executados), \
        "ambiguidade de identidade nao e fila humana do score (e do dedup/Scout)"


@item("auditoria-uma-linha-por-organizacao-processada")
def _(ctx):
    rota = [("SELECT id::text FROM sales_intelligence.organizations", ok(ORG_A + "\n" + ORG_B)),
            ("SELECT json_build_object", ok(json.dumps(organizacao_completa()))),
            ("FROM sales_intelligence.research_runs", ok(json.dumps([run_completed()]))),
            ("SELECT json_build_object('id', s.id", ok("")),
            ("WITH novo AS", ok("ESCRITO"))]
    agente = ctx.agente_com(rota)
    relatorio = agente.rodar([{"organizacao_id": ORG_A}, {"organizacao_id": ORG_B}])
    assert relatorio["total"] == 2
    auditorias = [s for s in agente.porta.executados
                  if "INSERT INTO sales_intelligence.agent_runs" in s]
    assert len(auditorias) == 2, "uma linha de auditoria por organizacao: %d" % len(auditorias)
    assert "score_id" in auditorias[0] and "ESCRITO" in auditorias[0]
    assert relatorio["por_veredito"].get("ESCRITO") == 2


@item("planejar-mede-e-nao-escreve")
def _(ctx):
    rota = [("SELECT json_build_object", ok(json.dumps(organizacao_completa()))),
            ("FROM sales_intelligence.research_runs", ok(json.dumps([run_completed()]))),
            ("SELECT json_build_object('id', s.id", ok("")),
            ("WITH novo AS", ok("ESCRITO"))]
    agente = ctx.agente_com(rota)
    relatorio = agente.rodar([{"organizacao_id": ORG_A}], planejar=True)
    assert relatorio["resultados"][0]["veredito"] == "PLANEJADO"
    medicao = relatorio["resultados"][0]["organizacoes"][0]
    assert medicao["veredito"] == "PLANEJADO_ESCREVER", medicao
    assert medicao["valor"], medicao
    assert not agente.porta.escritas(), "o modo de planejamento escreveu: %s" % agente.porta.escritas()


@item("guarda-recusa-escrita-fora-do-contrato")
def _(ctx):
    recusas = [
        ("DRO" + "P TABLE sales_intelligence.scores", False),
        ("TRUNCATE sales_intelligence.scores", False),
        ("INSERT INTO sales_intelligence.scores (id) VALUES ('x')", False),
        ("INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value) "
         "VALUES ('1','2','DATA_QUALITY',1)", False),
        ("INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, "
         "score_version) VALUES ('1','2','ICP',1,'v1.0')", False),
        ("INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, "
         "score_version, inventada) VALUES ('1','2','DATA_QUALITY',1,'v1.0',1)", False),
        ("INSERT INTO sales_intelligence.organizations (id) VALUES ('1')", False),
        ("UPDATE sales_intelligence.organizations SET legal_name = 'x' WHERE id = '1'", False),
        ("UPDATE sales_intelligence.organizations SET updated_at = now() WHERE id = '1'", False),
        ("UPDATE sales_intelligence.organizations SET data_quality_score = 1", False),
        ("UPDATE sales_intelligence.scores SET score_value = 1 WHERE id = '1'", False),
        ("UPDATE sales_intelligence.agent_runs SET status = 'x' WHERE id = '1'", False),
        ("DELETE FROM sales_intelligence.scores WHERE id = '1'", False),
        ("DELETE FROM sales_intelligence.research_runs WHERE id = '1'", False),
        ("INSERT INTO sales_intelligence.signals (id) VALUES ('1')", False),
        ("UPDATE sales_intelligence.organizations SET data_quality_score = 1 WHERE id = '1'", True),
        ("DELETE FROM sales_intelligence.scores WHERE id = '1'", True),
    ]
    for sql, permitido in recusas:
        try:
            ctx.modulo.validar_sql(sql, permitir_remocao=permitido)
            resultado = True
        except ctx.modulo.GuardaDeEscritaViolada:
            resultado = False
        assert resultado == permitido, "guarda %s para: %s" % ("liberou" if resultado else "barrou",
                                                              " ".join(sql.split())[:70])
    # o proprio SQL do score passa pela guarda (a guarda nao pode ser mais frouxa que o produtor)
    ctx.modulo.validar_sql(ctx.modulo.sql_gravar_score(
        ORG_A, ORG_A, ctx.medir(organizacao_completa(), runs=[run_completed()]),
        "2026-10-02T12:00:00+00:00"), permitir_remocao=True)


@item("ambiente-recusado-em-prod-e-exigido-para-escrever")
def _(ctx):
    for ambiente in ("prod", None, ""):
        agente = ctx.modulo.ScoreDataQuality(porta=PortaRoteiro(ctx.modulo), raiz=ctx.raiz,
                                             ambiente=ambiente, referencia=REFERENCIA)
        try:
            agente.conferir_ambiente()
            raise AssertionError("ambiente %r foi aceito" % ambiente)
        except ctx.modulo.RecusaDeAmbiente:
            pass
    for ambiente in ("dev", "homolog"):
        agente = ctx.modulo.ScoreDataQuality(porta=PortaRoteiro(ctx.modulo), raiz=ctx.raiz,
                                             ambiente=ambiente, referencia=REFERENCIA)
        assert agente.conferir_ambiente() == ambiente


@item("sem-llm-sem-rede-e-gate-do-jev-fail-closed")
def _(ctx):
    for proibido in ("import requests", "import socket", "import urllib", "import http.client",
                     "subprocess.run([\"curl", "os.system("):
        assert proibido not in ctx.codigo, "o score nao faz rede: %s" % proibido
    assert "executado\": False" in ctx.codigo or "\"executado\": False" in ctx.codigo
    for recibo in (None, {}, {"decision_id": "d", "lane": "high"},
                   {"decision_id": "d", "lane": "high", "outcome": "BLOCK"},
                   {"decision_id": "d", "lane": "high", "outcome": "ESCALATE"},
                   {"decision_id": "d", "lane": "turbo", "outcome": "PASS"}):
        try:
            ctx.modulo.validar_recibo_jev(recibo)
            raise AssertionError("recibo %r foi aceito" % recibo)
        except ctx.modulo.ReciboJEVInvalido:
            pass
    resposta = ctx.modulo.chamar_llm("prompt", recibo={"decision_id": "d", "lane": "high",
                                                       "outcome": "PASS"})
    assert resposta["executado"] is False and resposta["autorizado"] is True


@item("desfazer-e-dry-run-por-padrao-e-restaura-o-valor-anterior")
def _(ctx):
    item_rodada = json.dumps({"score_id": ORG_A, "organization_id": ORG_A, "valor": "75.25",
                              "veredito": "ESCRITO"})
    rota = [("FROM sales_intelligence.agent_runs a WHERE a.correlation_id", ok(item_rodada)),
            ("WITH alvos AS", ok("APAGADO|" + ORG_A + "\nRESTAURADO|" + ORG_A))]
    agente = ctx.agente_com(rota)
    frio = agente.desfazer("44444444-0000-4000-8000-000000000001")
    assert frio["dry_run"] is True and frio["apagados"] == 0
    assert not any("DELETE FROM" in s for s in agente.porta.executados), "dry-run apagou"
    assert frio["scores"] == [ORG_A]
    quente = agente.desfazer("44444444-0000-4000-8000-000000000001", confirmo=True)
    assert quente["apagados"] == 1 and quente["restaurados"] == 1
    sql = [s for s in agente.porta.executados if "WITH alvos AS" in s][0]
    assert "DELETE FROM sales_intelligence.scores" in sql
    assert "valor_anterior" in sql and "SET data_quality_score" in sql
    assert "agent_runs" in sql and "DELETE FROM sales_intelligence.agent_runs" not in sql, \
        "auditoria nao se apaga"


@item("contrato-do-card-e-artefatos-versionados")
def _(ctx):
    assert DOC_ARQUITETURA.is_file(), "doc de arquitetura ausente"
    assert RUNBOOK.is_file(), "runbook ausente"
    arquitetura = DOC_ARQUITETURA.read_text(encoding="utf-8")
    for secao in ("ACCEPTANCE", "TEST", "ROLLBACK", "RISK"):
        assert secao in arquitetura, "secao %s ausente no doc de arquitetura do card" % secao
    gate = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    for artefato in ("hermes/scores/data_quality/data_quality.py",
                     "hermes/scores/data_quality/score-data-quality-v1.json",
                     "scripts/scores/verificar_score_data_quality.py",
                     "scripts/scores/teste_data_quality_aceite.sh",
                     "docs/architecture/score-data-quality-v1.md",
                     "docs/runbooks/score-data-quality.md"):
        assert artefato in gate, "artefato do card fora do gate de estrutura: %s" % artefato
    assert ACEITE.is_file() and os.access(ACEITE, os.X_OK), "aceite ausente ou nao executavel"
    linhas = [l for l in EXEMPLO.read_text(encoding="utf-8").splitlines()
              if l.strip() and not l.lstrip().startswith("#")]
    assert linhas, "exemplo da fonte vazio"
    for linha in linhas:
        registro = json.loads(linha)
        assert "organizacao" in registro or "organizacao_id" in registro


# ---------------------------------------------------------------------------------------
# Autoteste por mutacao
# ---------------------------------------------------------------------------------------
# (nome, ancora antiga, ancora nova, item que DEVE reprovar)
MUTACOES = (
    # Dente de CARGA: trocar o peso no codigo sem trocar o contrato e RECUSADO pelo proprio modulo
    # (o `__init__` compara contrato x codigo). O que se prova aqui e a defesa, nao um item.
    ("peso-da-completude-trocado",
     '("cnpj", Decimal("0.15")),', '("cnpj", Decimal("0.10")),',
     "carga:divergencia-nos-pesos"),
    ("validade-sempre-aprovada",
     'def componente_validade(organizacao: dict, identidade) -> tuple:',
     'def componente_validade(organizacao: dict, identidade) -> tuple:\n'
     '    return Decimal("1"), {"checks": []}',
     "validade-marca-o-motivo-sem-corrigir-o-dado"),
    ("validade-pune-campo-ausente",
     '    return Decimal(aprovados) / Decimal(len(checks)), {"checks": checks}',
     '    return Decimal(aprovados) / Decimal(len(checks) + 1), {"checks": checks}',
     "validade-nao-pune-duas-vezes-o-campo-ausente"),
    ("updated-at-entra-na-leitura",
     '"data_quality_score", "created_at",', '"data_quality_score", "created_at", "updated_at",',
     "atualidade-usa-a-data-do-dado-e-nunca-updated-at"),
    ("pesquisa-nao-concluida-vira-lastro",
     'concluidas = [r for r in research_runs if _texto(r.get("status")).upper() == "COMPLETED"]',
     'concluidas = list(research_runs)',
     "confiabilidade-e-lastro-no-banco-nao-prosa"),
    ("idempotencia-fora-do-sql",
     '        "  WHERE NOT EXISTS (\\n"', '        "  WHERE true OR NOT EXISTS (\\n"',
     "gravacao-insere-medicao-e-espelha-sem-tocar-updated-at"),
    ("hash-fixo-ignora-o-estado",
     '    canonico = json.dumps(', '    canonico = "fixo" or json.dumps(',
     "medicao-e-reproduzivel-e-o-hash-cobre-o-que-importa"),
    ("guarda-libera-coluna-de-organizations",
     '            fora = [c for c in colunas if c not in COLUNAS_ORGANIZACOES_ESCRITAS]',
     '            fora = []',
     "guarda-recusa-escrita-fora-do-contrato"),
    ("guarda-esquece-a-permissao-do-delete",
     '                if not permitir_remocao:\n'
     '                    raise GuardaDeEscritaViolada("DELETE fora do desfazer explicito: %s" % tabela)',
     '                pass',
     "guarda-recusa-escrita-fora-do-contrato"),
    # A recusa de prod tem DUAS camadas (a lista de ambientes e a recusa explicita): mutar uma so
    # e inerte — medido. O dente mira a funcao inteira devolvendo o ambiente sem conferir nada.
    ("prod-passa-a-ser-aceito",
     '    def conferir_ambiente(self) -> str:\n        if self.ambiente in (None, ""):',
     '    def conferir_ambiente(self) -> str:\n        return self.ambiente\n'
     '        if self.ambiente in (None, ""):',
     "ambiente-recusado-em-prod-e-exigido-para-escrever"),
    ("auditoria-nao-e-registrada",
     '        rc_run, saida_run, erro_run = self.porta.executar(sql_registrar_execucao(',
     '        rc_run, saida_run, erro_run = (0, "OK", "")\n        _ = (sql_registrar_execucao(',
     "auditoria-uma-linha-por-organizacao-processada"),
    # Dente de CARGA: tirar `score_version` das colunas escritas e RECUSADO pelo modulo (contrato x
    # codigo) — a defesa e que se prova; o item de guarda cobre o INSERT montado a mao.
    ("score-version-fora-do-insert",
     'COLUNAS_SCORES = ("id", "organization_id", "score_type", "score_value", "score_version",',
     'COLUNAS_SCORES = ("id", "organization_id", "score_type", "score_value",',
     "carga:divergencia-nas-colunas-de-scores"),
    ("planejar-passa-a-escrever",
     '            resultados.append(self.planejar(entrada) if planejar',
     '            resultados.append(self.processar(entrada) if planejar',
     "planejar-mede-e-nao-escreve"),
    ("identidade-ambigua-vira-escrita",
     '        if len(casadas) > 1:\n            return [], VER_RECUSADA, '
     '["IDENTIDADE_AMBIGUA: %d organizacoes casadas" % len(casadas)]',
     '        if len(casadas) > 99:\n            return [], VER_RECUSADA, '
     '["IDENTIDADE_AMBIGUA: %d organizacoes casadas" % len(casadas)]',
     "fluxo-recusa-identidade-desconhecida-e-ambigua-sem-fila-humana"),
)


def carregar_modulo(caminho: Path):
    spec = importlib.util.spec_from_file_location("dq_sob_teste", str(caminho))
    if spec is None or spec.loader is None:
        raise ValueError("nao consegui carregar o modulo sob teste: %s" % caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def rodar_itens(ctx, somente=None):
    resultados = []
    for nome, funcao in ITENS:
        if somente and nome not in somente:
            continue
        try:
            funcao(ctx)
            resultados.append((nome, True, ""))
        except Exception as exc:  # noqa: BLE001 - a suite reporta o motivo real do item
            resultados.append((nome, False, "%s: %s" % (type(exc).__name__, exc)))
    return resultados


def imprimir(resultados):
    falhas = 0
    for nome, passou, motivo in resultados:
        if passou:
            print("OK     %s" % nome)
        else:
            falhas += 1
            print("FALHOU %s — %s" % (nome, motivo))
    return falhas


def autoteste(codigo: Path, modulo):
    """Muta a COPIA do codigo sob teste e exige que o ITEM esperado reprove."""
    if not MUTACOES:
        print("FALHOU nenhuma mutacao declarada (buraco de verificacao)")
        return 1
    nomes_itens = {nome for nome, _ in ITENS}
    falhas = 0
    for nome, antiga, nova, item_esperado in MUTACOES:
        if not item_esperado.startswith("carga:") and item_esperado not in nomes_itens:
            print("FALHOU mutacao %s aponta item inexistente: %s" % (nome, item_esperado))
            falhas += 1
            continue
        with tempfile.TemporaryDirectory() as tmp:
            copia = Path(tmp) / ("%s.py" % codigo.stem)
            shutil.copy2(codigo, copia)
            texto = copia.read_text(encoding="utf-8")
            if antiga not in texto:
                print("FALHOU mutacao %s nao se aplica (ancora de texto mudou)" % nome)
                falhas += 1
                continue
            copia.write_text(texto.replace(antiga, nova, 1), encoding="utf-8")
            if item_esperado.startswith("carga:"):
                # Dente de CARGA: a mutacao TEM de ser recusada na propria carga do modulo (o
                # `__init__` compara contrato x codigo). Recusar e o comportamento esperado —
                # carga que passa em silencio e que seria buraco.
                try:
                    # importar NAO basta: a divergencia contrato x codigo mora no `__init__`
                    Contexto(carregar_modulo(copia), raiz=codigo.parents[3]
                             if len(codigo.parents) > 3 else RAIZ)
                except Exception as exc:  # noqa: BLE001 - recusa e o esperado
                    print("OK     dente %s -> carga recusada (%s)" % (nome, str(exc)[:70]))
                    continue
                print("FALHOU dente %s: o modulo mutado carregou (a divergencia contrato x codigo "
                      "nao foi recusada)" % nome)
                falhas += 1
                continue
            try:
                modulo_mutado = carregar_modulo(copia)
                contexto = Contexto(modulo_mutado, raiz=codigo.parents[3]
                                    if len(codigo.parents) > 3 else RAIZ)
            except Exception as exc:  # noqa: BLE001 - mutacao que nao importa e buraco
                print("FALHOU mutacao %s nao carregou: %s" % (nome, exc))
                falhas += 1
                continue
            resultados = rodar_itens(contexto)
            reprovados = [n for n, passou, _ in resultados if not passou]
            if item_esperado in reprovados:
                print("OK     dente %s -> %s reprovou" % (nome, item_esperado))
            else:
                print("FALHOU dente %s NAO reprovou %s (reprovados: %s)"
                      % (nome, item_esperado, ", ".join(reprovados) or "nenhum"))
                falhas += 1
    return falhas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Suite do Score Data Quality v1 (TRE-W5-E04-T01)")
    parser.add_argument("--codigo", default=str(CODIGO_PADRAO))
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    codigo = Path(args.codigo)
    if not codigo.is_file():
        print("FALHOU codigo sob teste ausente: %s" % codigo)
        return 2
    modulo = carregar_modulo(codigo)
    raiz = codigo.parents[3] if len(codigo.parents) > 3 else RAIZ
    contexto = Contexto(modulo, raiz=raiz)
    resultados = rodar_itens(contexto)
    if not resultados:
        print("FALHOU nenhum item rodou (guarda de confiabilidade da suite)")
        return 1
    falhas = imprimir(resultados)
    if args.autoteste:
        falhas += autoteste(codigo, modulo)
    if falhas:
        print("RESULTADO: DQ_SUITE_FALHOU (%d itens, %d falhas)" % (len(resultados), falhas))
        return 1
    print("RESULTADO: DQ_SUITE_OK (%d itens, 0 falhas)" % len(resultados))
    return 0


if __name__ == "__main__":
    sys.exit(main())
