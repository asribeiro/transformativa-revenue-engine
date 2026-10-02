#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do AGENTE ICP SCORE v1 (card TRE-W5-E01-T01) — um comando, um veredito.

    python3 scripts/agentes/verificar_agente_icp_score.py
    python3 scripts/agentes/verificar_agente_icp_score.py --autoteste
    python3 scripts/agentes/verificar_agente_icp_score.py --codigo <caminho de outro icp_score.py>

O que ela prova (SEM banco e SEM rede): o contrato do agente existe e nao divergiu do Data
Contract V1.0; o MODELO V1 e o que o contrato e o documento dizem (pesos somando 1,00, sem
peso literal no codigo, faixas de porte dentro do vocabulario, versao unica); o calculo puro
bate caso a caso (sweet spot, fora do ICP, B2C, ausencia que NAO vira fit, faixa derivada do
`employee_count`, casamento por inicio de palavra, ambiguidade de segmento registrada); a
guarda de escrita recusa DDL, escrita fora das tres tabelas e UPDATE em `scores`; o gate do
JEV e fail-closed; e o fluxo completo (calcular, replay idempotente, recusar, nao deixar a
fonte contaminar o dado, desfazer) se comporta como o contrato do card — medido numa PORTA DE
ROTEIRO (implementacao da porta declarada, nao substituicao do alvo: a suite nao usa duble de
biblioteca nenhuma).

AUTOTESTE (`--autoteste`): cada mutacao e aplicada a uma COPIA do `icp_score.py` e a suite tem
de REPROVAR o item correspondente — mutacao que passa em silencio e buraco de verificacao.
Mutacao que nao se aplica (ancora de texto mudou) tambem reprova: e buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = ICP_SCORE_SUITE_OK · 1 = ICP_SCORE_SUITE_FALHOU (o log aponta o item) ·
2 = uso incorreto. Guarda de confiabilidade: etapa que roda 0 item REPROVA.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CODIGO_PADRAO = RAIZ / "hermes/agents/icp_score/icp_score.py"
CONTRATO_AGENTE = RAIZ / "hermes/agents/icp_score/agente-icp-score-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC_ARQUITETURA = RAIZ / "docs/architecture/agente-icp-score-v1.md"
RUNBOOK = RAIZ / "docs/runbooks/agente-icp-score.md"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO_FONTE = RAIZ / "hermes/agents/icp_score/exemplos/organizacoes-exemplo.jsonl"

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
        # O texto analisado e' o do MODULO SOB TESTE (no autoteste, a copia mutada): item que
        # lê o arquivo do repo nao enxerga mutacao e vira buraco de verificacao.
        origem = Path(getattr(modulo, "__file__", CODIGO_PADRAO))
        self.codigo = origem.read_text(encoding="utf-8")

    @property
    def modelo(self):
        return self.contrato["modelo"]

    @property
    def faixas(self):
        return tuple(self.contrato_dados["vocabularies"]["employee_band"])

    def agente(self, porta=None, ambiente="dev", **kwargs):
        return self.modulo.IcpScore(porta=porta if porta is not None else PortaRoteiro(),
                                    raiz=RAIZ, ambiente=ambiente, **kwargs)

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


class PortaRoteiro:
    """Porta de teste: responde o roteiro declarado e GUARDA o SQL executado.

    Ela implementa a porta do agente (mesma interface) e roda a mesma guarda de escrita — nao
    substitui o alvo por duble de biblioteca.
    """

    def __init__(self, respostas=None, modulo=None):
        self.respostas = list(respostas or [])
        self.chamadas = []
        self.modulo = modulo

    def executar(self, sql, permitir_remocao=False):
        if self.modulo is not None:
            self.modulo.validar_sql(sql, permitir_remocao=permitir_remocao)
        self.chamadas.append(sql)
        if not self.respostas:
            return 0, "", ""
        return self.respostas.pop(0)


def carregar_modulo(caminho):
    spec = importlib.util.spec_from_file_location("icp_sob_teste", str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


ORG_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ORG_B = "bbbbbbbb-0000-4000-8000-00000000000b"
ORG_C = "cccccccc-0000-4000-8000-00000000000c"


def linha_organizacao(org_id=ORG_A, **campos):
    """Linha no formato do SELECT do agente (11 colunas separadas por `|`)."""
    base = {
        "legal_name": "Empresa Teste Ltda", "trade_name": "Empresa Teste",
        "industry_code": "4711", "industry_name": "Distribuidora de materiais de construcao",
        "employee_count": "210", "employee_band": "150_299", "business_model": "B2B",
        "country_code": "BR", "status": "DISCOVERED", "updated_at": "2026-10-02T12:00:00",
    }
    base.update(campos)
    return "|".join([org_id, base["legal_name"], base["trade_name"], base["industry_code"],
                     base["industry_name"], str(base["employee_count"] if base["employee_count"]
                                                is not None else ""), base["employee_band"],
                     base["business_model"], base["country_code"], base["status"],
                     base["updated_at"]])


def organizacao(**campos):
    """Organizacao para o calculo puro (chaves do agente, sem banco)."""
    base = {"organization_id": ORG_A, "industry_code": "4711",
            "industry_name": "Distribuidora de materiais de construcao", "employee_count": 210,
            "employee_band": "150_299", "business_model": "B2B", "country_code": "BR",
            "status": "DISCOVERED", "legal_name": "Empresa Teste Ltda",
            "trade_name": "Empresa Teste", "updated_at": "2026-10-02T12:00:00"}
    base.update(campos)
    return base


def calcular(ctx, **campos):
    return ctx.modulo.calcular_icp(organizacao(**campos), ctx.modelo, ctx.faixas)


def sub_score(ctx, calculo, nome):
    for c in calculo["explanation"]["componentes"]:
        if c["nome"] == nome:
            return c["sub_score"]
    raise AssertionError("componente ausente na explicacao: %s" % nome)


def veredito(porta, resultado):
    """SQLs do fluxo, sem os de auditoria (agent_runs)."""
    return [sql for sql in porta.chamadas if "INSERT INTO sales_intelligence.agent_runs" not in sql]


# ---------------------------------------------------------------------------------------
# 1. Contrato e artefatos
# ---------------------------------------------------------------------------------------
@item("agente-existe-e-importa")
def _(ctx):
    m = ctx.modulo
    return (m.AGENTE == "icp_score" and m.PAPEL == "scoring" and m.SCORE_TYPE == "ICP",
            "modulo carregado (%s/%s)" % (m.AGENTE, m.VERSAO))


@item("raiz-do-agente-nao-depende-da-profundidade-do-arquivo")
def _(ctx):
    fonte = ctx.codigo
    tem_marcador = "CONTRATO_AGENTE_PADRAO).is_file()" in fonte
    tem_profundidade = "parents[3]" in fonte or "parents[2]" in fonte
    return tem_marcador and not tem_profundidade, "raiz achada por marcador"


@item("contrato-do-agente-e-o-do-card")
def _(ctx):
    c = ctx.contrato
    return (c["agente"] == "icp_score" and c["card"] == "TRE-W5-E01-T01" and c["onda"] == "W5"
            and c["papel"] == "scoring"), "%s/%s" % (c["agente"], c["versao"])


@item("score-type-ICP-existe-no-data-contract")
def _(ctx):
    tipos = ctx.contrato_dados["scores"]["types"]
    return (ctx.modulo.SCORE_TYPE in tipos
            and ctx.contrato["persistencia"]["score_type"] == ctx.modulo.SCORE_TYPE), \
        "ICP entre %s" % tipos


@item("score-sempre-com-versao")
def _(ctx):
    coluna = ctx.contrato_dados["scores"]["requires_version_column"]
    return (coluna == "score_version" and "score_version" in ctx.colunas("sales_intelligence.scores")
            and "score_version" in ctx.modulo.sql_gravar_score(
                "sid", "seid", "chave", ORG_A, {"score_value": 1.0, "inputs": {}, "explanation": {}},
                "icp-v9.9.9", {})), "coluna %s preenchida no INSERT" % coluna


@item("pesos-somam-um")
def _(ctx):
    soma = round(sum(float(p) for p in ctx.modelo["pesos"].values()), 6)
    return soma == 1.0, "soma=%s" % soma


@item("pesos-e-componentes-coincidem")
def _(ctx):
    return (set(ctx.modelo["pesos"]) == set(ctx.modelo["componentes"])
            and set(ctx.modelo["pesos"]) == {"segmento", "porte", "modelo_b2b"}), \
        "%s" % sorted(ctx.modelo["pesos"])


@item("peso-nao-esta-literal-no-codigo")
def _(ctx):
    """Peso e policy: mora no contrato, nunca no corpo do agente.

    O teste olha o valor EXECUTAVEL (forma decimal com ponto). O docstring do agente cita a
    formula com virgula decimal, justamente para nao virar literal copiavel — e ha' item
    separado exigindo que o codigo leia os pesos do contrato.
    """
    literais = [str(p) for p in ctx.modelo["pesos"].values()]
    achados = [lit for lit in literais if re.search(r"(?<![0-9])%s(?![0-9])" % re.escape(lit),
                                                    ctx.codigo)]
    return not achados, "nenhum peso executavel no codigo (%s)" % literais


@item("versao-do-score-nao-esta-literal-no-codigo")
def _(ctx):
    """A versao do score tem UMA fonte: `modelo.nome` do contrato."""
    nome = ctx.modelo["nome"]
    return nome not in ctx.codigo, "modelo.nome=%s ausente do codigo" % nome


@item("codigo-le-os-pesos-do-contrato")
def _(ctx):
    return ("modelo[\"pesos\"]" in ctx.codigo and "carregar_contrato_do_agente" in ctx.codigo
            and "validar_modelo" in ctx.codigo), "pesos lidos do contrato e validados"


@item("documento-tem-os-quatro-campos-do-contrato")
def _(ctx):
    """A secao 2 do doc 11 exige ACCEPTANCE/TEST/ROLLBACK/RISK no documento do card."""
    if not DOC_ARQUITETURA.is_file():
        return False, "documento ausente: %s" % DOC_ARQUITETURA
    texto = DOC_ARQUITETURA.read_text(encoding="utf-8")
    faltando = [c for c in ("ACCEPTANCE", "TEST", "ROLLBACK", "RISK") if c not in texto]
    return not faltando, "documento com os quatro campos"


@item("documento-e-modelo-nao-diverge")
def _(ctx):
    """A tabela do documento tem de bater com os pesos do contrato (mudanca silenciosa nao passa)."""
    texto = DOC_ARQUITETURA.read_text(encoding="utf-8")
    lidos = {}
    for linha in texto.splitlines():
        m = re.match(r"\|\s*(segmento|porte|modelo_b2b)\s*\|\s*([0-9]+,[0-9]+)\s*\|", linha.strip())
        if m:
            lidos[m.group(1)] = round(float(m.group(2).replace(",", ".")), 6)
    esperado = {k: round(float(v), 6) for k, v in ctx.modelo["pesos"].items()}
    return lidos == esperado, "documento=%s contrato=%s" % (lidos, esperado)


@item("documento-declara-o-modelo-e-o-status")
def _(ctx):
    texto = DOC_ARQUITETURA.read_text(encoding="utf-8")
    return (ctx.modelo["nome"] in texto and ctx.modelo["status"] in texto), \
        "%s / %s" % (ctx.modelo["nome"], ctx.modelo["status"])


@item("faixas-de-porte-estao-no-vocabulario-do-contrato")
def _(ctx):
    bandas = set(ctx.modelo["componentes"]["porte"]["sub_scores"])
    return bandas.issubset(set(ctx.faixas)), "faixas %s dentro do vocabulario" % sorted(bandas)


@item("vocabularios-fechados-do-contrato-sao-cobertos")
def _(ctx):
    """Todo segmento declarado e um ICP do Data Contract; todo ICP do contrato tem segmento."""
    icps = set(ctx.contrato_dados["scores"]["icp_context"]["icps"])
    declarados = {s["nome"] for s in ctx.modelo["componentes"]["segmento"]["segmentos"]}
    return declarados == icps, "declarados=%s contrato=%s" % (sorted(declarados), sorted(icps))


@item("vocabulario-de-vereditos-e-status-completo")
def _(ctx):
    m = ctx.modulo
    return (set(m.VEREDITOS) == set(m.STATUS_AGENT_RUNS) and len(m.VEREDITOS) == 4
            and m.VER_CALCULADO in m.VEREDITOS), "%s" % sorted(m.VEREDITOS)


@item("entrada-declara-o-sujeito-e-o-que-e-do-modo-planejar")
def _(ctx):
    e = ctx.contrato["entrada"]
    return (e["campo_obrigatorio"] == ["organization_id"] and e["uma_organizacao_por_linha"]
            and "organization_id" in ctx.codigo and len(e["campos_do_modo_planejar"]) >= 6), \
        "campos do modo planejar: %d" % len(e["campos_do_modo_planejar"])


@item("modelo-nao-promete-o-que-nao-faz")
def _(ctx):
    lacunas = " ".join(ctx.contrato["lacunas"]).lower()
    guardrails = ctx.contrato["guardrails"]
    return ("nba" in lacunas or "next best action" in lacunas) and \
        ("nenhuma chamada de llm" in guardrails["llm"].lower()) and \
        ("nenhum evento de outbox" in guardrails["outbox"].lower()), "lacunas declaradas"


# ---------------------------------------------------------------------------------------
# 2. Modelo puro (sem banco)
# ---------------------------------------------------------------------------------------
@item("sweet-spot-distribuidor-b2b-pontua-100")
def _(ctx):
    c = calcular(ctx, employee_band="150_299")
    return (c["score_value"] == 100.0 and not c["motivos"]), c["score_value"]


@item("logistica-700-1000-pontua-86")
def _(ctx):
    c = calcular(ctx, industry_name="Logistica e transporte de cargas", employee_band="700_1000",
                 business_model="B2B")
    return (c["score_value"] == 86.0 and c["motivos"] == ["PORTE_FORA_DO_SWEET_SPOT"]), \
        "%s %s" % (c["score_value"], c["motivos"])


@item("b2c-e-abaixo-do-icp-pontua-0-com-motivos")
def _(ctx):
    c = calcular(ctx, industry_name="Comercio varejista de alimentos", employee_band="LT_70",
                 business_model="B2C")
    return (c["score_value"] == 0.0 and len(c["motivos"]) == 3
            and "SEGMENTO_NAO_RECONHECIDO" in c["motivos"]
            and "PORTE_ABAIXO_DO_ICP" in c["motivos"]
            and "MODELO_B2C_FORA_DO_ICP" in c["motivos"]), "%s %s" % (c["score_value"], c["motivos"])


@item("sem-dado-pontua-0-e-nao-vira-fit")
def _(ctx):
    c = calcular(ctx, industry_code="", industry_name="", employee_count=None,
                 employee_band="", business_model="")
    esperado = ["SEGMENTO_NAO_INFORMADO", "PORTE_NAO_INFORMADO", "MODELO_DE_NEGOCIO_NAO_INFORMADO"]
    return (c["score_value"] == 0.0 and c["motivos"] == esperado
            and all(x["sub_score"] == 0.0 for x in c["explanation"]["componentes"])), \
        "%s %s" % (c["score_value"], c["motivos"])


@item("b2b2c-e-parcial-nao-fora-do-icp")
def _(ctx):
    c = calcular(ctx, business_model="B2B2C")
    return (sub_score(ctx, c, "modelo_b2b") == 70.0
            and c["motivos"] == ["MODELO_PARCIALMENTE_B2B"]), c["score_value"]


@item("modelo-de-negocio-desconhecido-nao-vira-fit")
def _(ctx):
    c = calcular(ctx, business_model="ONG")
    return (sub_score(ctx, c, "modelo_b2b") == 0.0
            and c["motivos"] == ["MODELO_DE_NEGOCIO_NAO_INFORMADO"]), c["motivos"]


@item("faixa-derivada-do-employee-count")
def _(ctx):
    casos = {210: "150_299", 30: "LT_70", 640: "500_699", 5000: "GT_1000", None: "UNKNOWN",
             "abc": "UNKNOWN"}
    obtido = {k: ctx.modulo.faixa_de_empregados(k, ctx.faixas) for k in casos}
    return obtido == casos, "%s" % obtido


@item("faixa-derivada-pontua-como-a-faixa-do-banco")
def _(ctx):
    c = calcular(ctx, employee_count=30, employee_band="")
    return (sub_score(ctx, c, "porte") == 0.0 and c["inputs"]["origem_do_porte"] == "employee_count"
            and c["inputs"]["faixa_efetiva"] == "LT_70"), "%s %s" % (
                c["inputs"]["faixa_efetiva"], c["inputs"]["origem_do_porte"])


@item("faixa-do-banco-tem-precedencia-sobre-o-count")
def _(ctx):
    c = calcular(ctx, employee_count=210, employee_band="GT_1000")
    return (sub_score(ctx, c, "porte") == 0.0 and c["motivos"] == ["PORTE_ACIMA_DO_ICP"]
            and c["inputs"]["origem_do_porte"] == "employee_band"), "%s" % c["motivos"]


@item("casamento-por-inicio-de-palavra")
def _(ctx):
    """'descarga' nao e' 'carga', 'fintech' nao e' 'tech': prefixo de palavra, nao substring."""
    f = ctx.modulo.casa_termo
    return (f("servico de descarga", "carga") is False and f("transporte de carga", "carga") is True
            and f("consultoria em marketing", "tech") is False
            and f("empresa de tecnologia", "tecnolog") is True), "prefixo com fronteira de palavra"


@item("ambiguidade-de-segmento-e-registrada")
def _(ctx):
    c = calcular(ctx, industry_name="Consultoria de tecnologia da informacao")
    comp = [x for x in c["explanation"]["componentes"] if x["nome"] == "segmento"][0]
    return (comp["segmento_casado"] == comp["segmentos_casados"][0]
            and len(comp["segmentos_casados"]) == 2
            and set(comp["segmentos_casados"]) == {"Serviços B2B", "SaaS/Tech B2B"}), \
        "%s" % comp["segmentos_casados"]


@item("segmento-fora-do-vocabulario-nao-pontua")
def _(ctx):
    c = calcular(ctx, industry_name="Comercio varejista de autopecas")
    return (sub_score(ctx, c, "segmento") == 0.0
            and c["motivos"] == ["SEGMENTO_NAO_RECONHECIDO"]), c["motivos"]


@item("score-em-duas-decimais-dentro-da-escala")
def _(ctx):
    c = calcular(ctx, employee_band="70_149", business_model="B2B2C")
    valor = c["score_value"]
    decimais = len(("%.10f" % valor).rstrip("0").split(".")[1])
    return (0.0 <= valor <= 100.0 and decimais <= 2), "%s (%d decimais)" % (valor, decimais)


@item("explicacao-tem-contribuicao-e-pesos-que-somam-um")
def _(ctx):
    c = calcular(ctx)
    e = c["explanation"]
    soma_contribuicao = round(sum(x["contribuicao"] for x in e["componentes"]), 6)
    return (e["pesos_somam"] == 1.0 and soma_contribuicao == c["score_value"]
            and len(e["componentes"]) == 3 and e["modelo"] == ctx.modelo["nome"]), \
        "contribuicao=%s score=%s" % (soma_contribuicao, c["score_value"])


@item("inputs-guardam-o-que-foi-lido")
def _(ctx):
    c = calcular(ctx, employee_band="700_1000")
    campos = c["inputs"]["campos"]
    return (campos["employee_band"] == "700_1000" and campos["industry_name"] != ""
            and campos["business_model"] == "B2B" and c["inputs"]["faixa_efetiva"] == "700_1000"
            and len(c["inputs"]["fingerprint"]) == 64), "inputs com os campos lidos"


@item("fingerprint-muda-quando-o-dado-muda")
def _(ctx):
    base = calcular(ctx, employee_count=210, employee_band="150_299")
    mudou_faixa = calcular(ctx, employee_count=210, employee_band="GT_1000")
    mudou_modelo = calcular(ctx, business_model="B2C")
    mudou_segmento = calcular(ctx, industry_name="Transportadora rodoviaria de cargas")
    igual = calcular(ctx, legal_name="Outro nome", status="QUALIFIED",
                     updated_at="2026-11-01T09:00:00")
    fingerprints = {base["fingerprint"], mudou_faixa["fingerprint"], mudou_modelo["fingerprint"],
                    mudou_segmento["fingerprint"]}
    return (len(fingerprints) == 4 and igual["fingerprint"] == base["fingerprint"]), \
        "%d fingerprints distintos; nome/status/carimbo nao mudam" % len(fingerprints)


@item("chave-de-idempotencia-identifica-org-modelo-e-dado")
def _(ctx):
    c = calcular(ctx)
    chave = ctx.modulo.chave_idempotencia(ORG_A, ctx.modelo["nome"], c["fingerprint"])
    pedacos = chave.split(":")
    return (pedacos[0] == "icp" and pedacos[1] == "score" and pedacos[2] == ORG_A
            and pedacos[3] == ctx.modelo["nome"] and len(pedacos[4]) == 16
            and len(chave) <= 255), chave


@item("exemplo-da-fonte-bate-com-o-modelo")
def _(ctx):
    """A fonte de exemplo e' medida: os valores dela sao os do modelo, nao promessa do arquivo."""
    linhas = ctx.modulo.ler_linhas(str(EXEMPLO_FONTE))
    agente = ctx.agente(PortaRoteiro())
    relatorio = agente.rodar(linhas, planejar=True)
    scores = [r["score_value"] for r in relatorio["resultados"]
              if r["veredito"] == ctx.modulo.PLANEJADO_CALCULAR]
    return (scores == [100.0, 86.0, 94.0, 0.0, 0.0]
            and relatorio["por_veredito"].get(ctx.modulo.PLANEJADO_RECUSAR) == 1), "%s" % scores


# ---------------------------------------------------------------------------------------
# 3. Guardas de escrita e SQL
# ---------------------------------------------------------------------------------------
def _recusa(ctx, sql, permitir_remocao=False):
    try:
        ctx.modulo.validar_sql(sql, permitir_remocao=permitir_remocao)
        return False
    except ctx.modulo.GuardaDeEscritaViolada:
        return True


@item("guarda-recusa-ddl")
def _(ctx):
    for sql in ("DROP TABLE sales_intelligence.scores;",
                "ALTER TABLE sales_intelligence.scores ADD COLUMN x int;",
                "TRUNCATE sales_intelligence.scores;"):
        if not _recusa(ctx, sql):
            return False, "passou: %s" % sql
    return True, "DDL recusado"


@item("guarda-recusa-escrita-fora-do-declarado")
def _(ctx):
    recusadas = [_recusa(ctx, "INSERT INTO sales_intelligence.recommendations (id) VALUES ('x');"),
                 _recusa(ctx, "INSERT INTO sales_intelligence.outbox_events (id) VALUES ('x');"),
                 _recusa(ctx, "UPDATE sales_intelligence.organizations SET status = 'X';")]
    return all(recusadas), "recommendations/outbox/organizations recusados"


@item("guarda-recusa-update-em-scores")
def _(ctx):
    return (_recusa(ctx, "UPDATE sales_intelligence.scores SET score_value = 100.0;")
            and not _recusa(ctx, "INSERT INTO sales_intelligence.scores (id) VALUES ('x');")), \
        "score e imutavel no codigo, nao so' na prosa"


@item("guarda-recusa-delete-fora-do-desfazer")
def _(ctx):
    return (_recusa(ctx, "DELETE FROM sales_intelligence.scores WHERE id = 'x';")
            and not _recusa(ctx, "DELETE FROM sales_intelligence.scores WHERE id = 'x';",
                            permitir_remocao=True)), "DELETE so' com permitir_remocao"


@item("guarda-nao-confunde-nome-de-empresa-com-ddl")
def _(ctx):
    sql = ("INSERT INTO sales_intelligence.agent_runs (id, input) VALUES "
           "('x', '{\"legal_name\": \"Create Drop Solucoes Ltda\"}');")
    return not _recusa(ctx, sql), "prosa com 'Drop'/'Create' nao e' DDL"


@item("sql-de-gravacao-exige-a-chave-unica")
def _(ctx):
    sql = ctx.modulo.sql_gravar_score("sid", "seid", "chave", ORG_A,
                                      calcular(ctx), ctx.modelo["nome"], {})
    return ("ON CONFLICT (idempotency_key) DO NOTHING" in sql
            and "INSERT INTO sales_intelligence.sync_events" in sql), "claim da chave no SQL"


@item("sql-de-gravacao-nao-fecha-o-evento-sem-o-score")
def _(ctx):
    sql = ctx.modulo.sql_gravar_score("sid", "seid", "chave", ORG_A,
                                      calcular(ctx), ctx.modelo["nome"], {})
    return ("EXISTS (SELECT 1 FROM sales_intelligence.scores WHERE id = 'sid')" in sql
            and "SET status = 'SUCCESS'" in sql), "fechamento condicionado ao score existir"


@item("sql-de-gravacao-escreve-so-o-declarado")
def _(ctx):
    sql = ctx.modulo.sql_gravar_score("sid", "seid", "chave", ORG_A,
                                      calcular(ctx), ctx.modelo["nome"], {})
    tabelas = set()
    for operacao, padrao in ctx.modulo._ESCRITA:
        tabelas.update(t.lower() for t in padrao.findall(sql))
    auditoria = ctx.modulo.sql_registrar_execucao("rid", "corr", ORG_A, "COMPLETED", {}, {},
                                                  "t0", "t1")
    # a gravacao escreve score + trilha da chave; a auditoria tem instrucao propria.
    return (tabelas == {ctx.modulo.TABELA_SCORES, ctx.modulo.TABELA_SYNC_EVENTS}
            and ctx.modulo.TABELA_AGENT_RUNS in auditoria
            and not any(t in ctx.modulo.TABELAS_PROIBIDAS for t in tabelas)), "%s" % sorted(tabelas)


@item("sql-de-auditoria-nao-escreve-modelo-nem-tokens")
def _(ctx):
    """v1 nao tem LLM: a auditoria grava NULL em model e nao toca nas colunas de token."""
    sql = ctx.modulo.sql_registrar_execucao("rid", "corr", ORG_A, "COMPLETED", {}, {}, "t0", "t1")
    return ("NULL, 't0'" in sql.replace("{ini}", "'t0'") or "NULL, {ini}" in sql
            or "saida}, NULL," in sql or re.search(r"\}, NULL, ", sql) is not None), \
        "model NULL no INSERT de agent_runs"


@item("tabelas-proibidas-nao-aparecem-no-codigo")
def _(ctx):
    achados = [t for t in ("recommendations", "outbox_events", "interactions")
               if re.search(r"INSERT INTO sales_intelligence\.%s" % t, ctx.codigo)]
    return not achados, "nenhuma escrita em %s" % ["recommendations", "outbox_events",
                                                   "interactions"]


# ---------------------------------------------------------------------------------------
# 4. Fluxo (porta de roteiro)
# ---------------------------------------------------------------------------------------
@item("fluxo-calcula-e-grava")
def _(ctx):
    porta = PortaRoteiro([(0, linha_organizacao(), ""), (0, "ICP_SCORE_GRAVADO", ""),
                          (0, "INSERT 0 1", "")], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.processar({"organization_id": ORG_A})
    sqls = veredito(porta, r)
    return (r["veredito"] == ctx.modulo.VER_CALCULADO and r["score_value"] == 100.0
            and r["score_id"] and r["auditoria_registrada"] is True
            and "INSERT INTO sales_intelligence.scores" in sqls[1]
            and "score_type" in sqls[1] and "'ICP'" in sqls[1]
            and ctx.modelo["nome"] in sqls[1]), "%s score=%s" % (r["veredito"], r["score_value"])


@item("fluxo-replay-nao-cria-linha-nova")
def _(ctx):
    porta = PortaRoteiro([(0, linha_organizacao(), ""), (0, "", ""), (0, "INSERT 0 1", "")],
                         modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.processar({"organization_id": ORG_A})
    return (r["veredito"] == ctx.modulo.VER_JA_EXISTE
            and "IDEMPOTENCIA_REPLAY" in r["motivos"] and r["score_id"] is None
            and r["status_agent_runs"] == "COMPLETED"), "%s %s" % (r["veredito"], r["motivos"])


@item("fluxo-organizacao-inexistente-recusa-sem-escrever")
def _(ctx):
    porta = PortaRoteiro([(0, "", ""), (0, "INSERT 0 1", "")], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.processar({"organization_id": ORG_A})
    escritas = [sql for sql in porta.chamadas if "INSERT INTO sales_intelligence.scores" in sql]
    return (r["veredito"] == ctx.modulo.VER_RECUSADA
            and r["motivos"] == ["ORGANIZACAO_NAO_ENCONTRADA"] and not escritas
            and r["status_agent_runs"] == "REJECTED"), "%s %s" % (r["veredito"], r["status_agent_runs"])


@item("fluxo-id-ilegivel-recusa-sem-ir-ao-banco")
def _(ctx):
    porta = PortaRoteiro([], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.processar({"organization_id": "nao-e-uuid"})
    return (r["veredito"] == ctx.modulo.VER_RECUSADA
            and r["motivos"] == ["ORGANIZATION_ID_INVALIDO"]
            and len(veredito(porta, r)) == 0), "recusa de forma antes da leitura (0 SQL de negocio)"


@item("fonte-nao-contamina-o-score")
def _(ctx):
    """A fonte escolhe o SUJEITO: campo de score vindo dela e' ignorado no modo real."""
    linha_banco = linha_organizacao(industry_name="Logistica e transporte de cargas",
                                    employee_band="GT_1000", business_model="B2B")
    porta = PortaRoteiro([(0, linha_banco, ""), (0, "ICP_SCORE_GRAVADO", ""), (0, "INSERT 0 1", "")],
                         modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.processar({"organization_id": ORG_A, "employee_band": "150_299",
                          "industry_name": "Distribuidora de materiais", "business_model": "B2B",
                          "score_value": 100.0})
    esperado = ctx.modulo.calcular_icp(
        {"organization_id": ORG_A, "industry_code": "4711",
         "industry_name": "Logistica e transporte de cargas",
         "employee_count": "210", "employee_band": "GT_1000", "business_model": "B2B"},
        ctx.modelo, ctx.faixas)
    return (r["score_value"] == esperado["score_value"] == 65.0
            and r["fingerprint"] == esperado["fingerprint"]), \
        "%s (se a fonte mandasse, seria 100,00)" % r["score_value"]


@item("fluxo-sem-ambiente-ou-prod-recusa-antes-de-qualquer-escrita")
def _(ctx):
    porta = PortaRoteiro([], modulo=ctx.modulo)
    recusas = 0
    for ambiente in (None, "", "prod", "staging"):
        agente = ctx.modulo.IcpScore(porta=porta, raiz=RAIZ, ambiente=ambiente)
        try:
            agente.conferir_ambiente()
        except ctx.modulo.RecusaDeAmbiente:
            recusas += 1
    ok_planejar_sem_ambiente = ctx.modulo.IcpScore(
        porta=porta, raiz=RAIZ, ambiente=None).planejar({"organization_id": ORG_A})["veredito"]
    return (recusas == 4 and len(porta.chamadas) == 0
            and ok_planejar_sem_ambiente == ctx.modulo.PLANEJADO_CALCULAR), \
        "4 recusas, 0 SQL"


@item("planejar-nao-usa-a-porta-de-banco")
def _(ctx):
    agente = ctx.agente(PortaAusenteRoteiro(), ambiente=None)
    relatorio = agente.rodar([{"organization_id": ORG_A, "employee_count": 900,
                               "industry_name": "Transportadora rodoviaria de cargas",
                               "business_model": "B2B"}], planejar=True)
    r = relatorio["resultados"][0]
    return (r["veredito"] == ctx.modulo.PLANEJADO_CALCULAR and r["score_value"] == 86.0
            and r["origem_do_dado"].startswith("fonte")), "%s" % r["score_value"]


@item("auditoria-que-nao-registra-vira-erro")
def _(ctx):
    porta = PortaRoteiro([(0, linha_organizacao(), ""), (0, "ICP_SCORE_GRAVADO", ""),
                          (1, "", "permission denied")], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.processar({"organization_id": ORG_A})
    return (r["veredito"] == ctx.modulo.VER_ERRO and r["auditoria_registrada"] is False
            and any("AUDITORIA_NAO_REGISTRADA" in m for m in r["motivos"])), "%s" % r["motivos"]


@item("falha-da-porta-vira-erro-nao-sucesso")
def _(ctx):
    porta = PortaRoteiro([(1, "", "connection refused"), (0, "INSERT 0 1", "")], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.processar({"organization_id": ORG_A})
    return (r["veredito"] == ctx.modulo.VER_ERRO and r["score_id"] is None
            and r["status_agent_runs"] == "FAILED"), "%s %s" % (r["veredito"], r["motivos"][:1])


@item("desfazer-dry-run-nao-apaga")
def _(ctx):
    porta = PortaRoteiro([(0, "sid-1\nsid-2", "")], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.desfazer("corr-1")
    deletes = [sql for sql in porta.chamadas if "DELETE FROM" in sql]
    return (r["dry_run"] is True and r["apagados"] == 0 and r["scores"] == ["sid-1", "sid-2"]
            and not deletes), "%s %s" % (r["scores"], r["dry_run"])


@item("desfazer-apaga-so-os-scores-da-rodada")
def _(ctx):
    porta = PortaRoteiro([(0, "sid-1", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    r = agente.desfazer("corr-1", confirmo=True)
    sql = porta.chamadas[-1]
    return (r["apagados"] == 1 and "DELETE FROM sales_intelligence.scores WHERE id IN ('sid-1')" in sql
            and "'ROLLBACK'" in sql and "correlation_id" in sql), "%d apagado" % r["apagados"]


@item("desfazer-nao-apaga-auditoria")
def _(ctx):
    sql = ctx.modulo.sql_desfazer(["sid-1"], "corr-1", "seid-1")
    return ("agent_runs" not in sql and "organizations" not in sql
            and "sync_events" in sql), "auditoria preservada no desfazer"


@item("desfazer-exige-o-caminho-explicito")
def _(ctx):
    porta = PortaRoteiro([(0, "sid-1", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.agente(porta)
    agente.desfazer("corr-1", confirmo=True)
    return any(s == "DELETE FROM sales_intelligence.scores" or "DELETE FROM" in s
               for s in porta.chamadas), "DELETE so' com --confirmo"


# ---------------------------------------------------------------------------------------
# 5. Contrato do modelo (fail-closed)
# ---------------------------------------------------------------------------------------
@item("modelo-com-pesos-que-nao-somam-1-e-recusado")
def _(ctx):
    modelo = json.loads(json.dumps(ctx.modelo))
    modelo["pesos"]["segmento"] = 0.50
    try:
        ctx.modulo.validar_modelo(modelo, ctx.faixas)
        return False, "modelo invalido aceito"
    except ctx.modulo.ModeloInvalido:
        return True, "recusado"


@item("modelo-com-faixa-fora-do-contrato-e-recusado")
def _(ctx):
    modelo = json.loads(json.dumps(ctx.modelo))
    modelo["componentes"]["porte"]["sub_scores"]["150_2999"] = 100.0
    try:
        ctx.modulo.validar_modelo(modelo, ctx.faixas)
        return False, "faixa fora do contrato aceita"
    except ctx.modulo.ModeloInvalido:
        return True, "recusado"


@item("modelo-sem-versao-e-recusado")
def _(ctx):
    modelo = json.loads(json.dumps(ctx.modelo))
    modelo["nome"] = ""
    try:
        ctx.modulo.validar_modelo(modelo, ctx.faixas)
        return False, "modelo sem nome aceito"
    except ctx.modulo.ModeloInvalido:
        return True, "recusado (score sem versao e' recusado pelo Data Contract)"


@item("modelo-nome-que-nao-serve-de-score-version-e-recusado")
def _(ctx):
    contrato = json.loads(json.dumps(ctx.contrato))
    contrato["modelo"]["nome"] = "proposta a homologar pelo Anderson (texto, nao versao)"
    caminho = Path(tempfile.mkdtemp(prefix="icp-contrato-")) / "agente-icp-score-v1.json"
    caminho.write_text(json.dumps(contrato), encoding="utf-8")
    salvo = ctx.modulo.CONTRATO_AGENTE_PADRAO
    ctx.modulo.CONTRATO_AGENTE_PADRAO = str(caminho)
    try:
        try:
            ctx.modulo.IcpScore(porta=PortaRoteiro(), raiz=RAIZ, ambiente="dev")
            return False, "nome de modelo em prosa aceito"
        except (ctx.modulo.ModeloInvalido, ValueError):
            return True, "recusado"
    finally:
        ctx.modulo.CONTRATO_AGENTE_PADRAO = salvo
        shutil.rmtree(caminho.parent, ignore_errors=True)


@item("contrato-de-agente-de-outro-agente-e-recusado")
def _(ctx):
    caminho = Path(tempfile.mkdtemp(prefix="icp-contrato-")) / "agente-icp-score-v1.json"
    caminho.write_text(json.dumps({"agente": "scout"}), encoding="utf-8")
    salvo = ctx.modulo.CONTRATO_AGENTE_PADRAO
    ctx.modulo.CONTRATO_AGENTE_PADRAO = str(caminho)
    try:
        try:
            ctx.modulo.IcpScore(porta=PortaRoteiro(), raiz=RAIZ, ambiente="dev")
            return False, "contrato de outro agente aceito"
        except (ValueError, ctx.modulo.ModeloInvalido):
            return True, "recusado"
    finally:
        ctx.modulo.CONTRATO_AGENTE_PADRAO = salvo
        shutil.rmtree(caminho.parent, ignore_errors=True)


@item("jev-e-fail-closed-e-nao-executa-llm")
def _(ctx):
    try:
        ctx.modulo.chamar_llm("prompt sem recibo")
        return False, "passou sem recibo"
    except ctx.modulo.ReciboJEVInvalido:
        pass
    bloqueado = {"decision_id": "d1", "lane": "high", "outcome": "BLOCK"}
    try:
        ctx.modulo.chamar_llm("prompt", recibo=bloqueado)
        return False, "recibo bloqueado aceito"
    except ctx.modulo.ReciboJEVInvalido:
        pass
    valido = {"decision_id": "d1", "lane": "high", "outcome": "PASS"}
    r = ctx.modulo.chamar_llm("prompt", recibo=valido)
    return (r["executado"] is False and r["autorizado"] is True), "gate existe e nao executa LLM"


@item("estrutura-do-repo-conhece-os-artefatos-do-card")
def _(ctx):
    texto = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    faltando = [a for a in ("hermes/agents/icp_score/icp_score.py",
                            "hermes/agents/icp_score/agente-icp-score-v1.json",
                            "scripts/agentes/verificar_agente_icp_score.py",
                            "scripts/agentes/teste_icp_score_aceite.sh",
                            "docs/architecture/agente-icp-score-v1.md",
                            "docs/runbooks/agente-icp-score.md") if a not in texto]
    return not faltando, ("portao cita os 6 artefatos do card" if not faltando
                          else "faltando %s" % faltando)


@item("runbook-cita-a-operacao-e-o-desfazer")
def _(ctx):
    texto = RUNBOOK.read_text(encoding="utf-8")
    return (all(t in texto for t in ("icp_score.py", "--planejar", "--desfazer", "--confirmo",
                                     "--ambiente")), "runbook operacional")


class PortaAusenteRoteiro:
    """Porta que RECUSA: usada para provar que `--planejar` nao fala com banco."""

    def __init__(self):
        self.chamadas = []

    def executar(self, sql, permitir_remocao=False):
        self.chamadas.append(sql)
        raise AssertionError("o modo planejar nao pode falar com o banco: %s" % sql[:60])


# ---------------------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------------------
def executar_suite(modulo, raiz=RAIZ):
    ctx = Contexto(modulo, raiz)
    ok = falhas = 0
    resultados = {}
    for nome, funcao in ITENS:
        try:
            passou, detalhe = funcao(ctx)
        except Exception as exc:  # noqa: BLE001 — falha de item e falha, nao excecao na tela
            passou, detalhe = False, "EXCECAO %s: %s" % (type(exc).__name__, exc)
        resultados[nome] = bool(passou)
        if passou:
            ok += 1
            print("OK     %s (%s)" % (nome, detalhe))
        else:
            falhas += 1
            print("FALHOU %s -> %s" % (nome, detalhe))
    return ok, falhas, resultados


MUTACOES = [
    ("sem-faixa-derivada-do-count",
     "    for inicio, fim, faixa in numericas:",
     "    for inicio, fim, faixa in ():",
     ["faixa-derivada-do-employee-count"]),
    ("sub-score-de-porte-ignora-a-faixa",
     'sub_port = _sub_score(port["sub_scores"], faixa_efetiva, port["sub_score_ausente"])',
     'sub_port = float(port["sub_score_ausente"])',
     ["sweet-spot-distribuidor-b2b-pontua-100", "logistica-700-1000-pontua-86"]),
    ("ausencia-de-porte-vira-fit",
     '        sub_port, motivo_port = float(port["sub_score_ausente"]), port["motivos"]["sem_dado"]',
     '        sub_port, motivo_port = 100.0, None',
     ["sem-dado-pontua-0-e-nao-vira-fit"]),
    ("segmento-casa-substring-no-meio-da-palavra",
     '    return re.search(r"\\b" + re.escape(termo), texto_normalizado) is not None',
     '    return termo in texto_normalizado',
     ["casamento-por-inicio-de-palavra"]),
    ("fingerprint-constante",
     "    fingerprint = hashlib.sha256(",
     '    fingerprint = ("0" * 64) or hashlib.sha256(',
     ["fingerprint-muda-quando-o-dado-muda"]),
    ("pesos-nao-validados",
     "    if soma != 1.0:",
     "    if False:",
     ["modelo-com-pesos-que-nao-somam-1-e-recusado"]),
    ("faixa-fora-do-contrato-nao-validada",
     "    if not bandas.issubset(set(faixas_do_contrato)):",
     "    if False:",
     ["modelo-com-faixa-fora-do-contrato-e-recusado"]),
    ("modelo-sem-versao-aceito",
     '    if not isinstance(modelo, dict) or not modelo.get("nome"):',
     "    if False:",
     ["modelo-sem-versao-e-recusado"]),
    ("nome-do-modelo-nao-validado",
     '    if not re.fullmatch(r"[A-Za-z0-9._-]{1,30}", self.modelo_nome):',
     "    if False:",
     ["modelo-nome-que-nao-serve-de-score-version-e-recusado"]),
    ("guarda-deixa-passar-ddl",
     "    if _DDL.search(codigo):",
     "    if False and _DDL.search(codigo):",
     ["guarda-recusa-ddl"]),
    ("guarda-le-o-literal-como-codigo",
     "    codigo = _sem_literais(sql)",
     "    codigo = sql",
     ["guarda-nao-confunde-nome-de-empresa-com-ddl"]),
    ("escrita-fora-do-declarado-liberada",
     "            if tabela.lower() not in TABELAS_PERMITIDAS:",
     "            if False:",
     ["guarda-recusa-escrita-fora-do-declarado"]),
    ("update-em-scores-liberado",
     '            if operacao == "update" and tabela.lower() == TABELA_SCORES:',
     "            if False:",
     ["guarda-recusa-update-em-scores"]),
    ("fonte-contamina-o-score",
     "                    calculo = calcular_icp(organizacao, self.modelo, self.faixas)",
     "                    calculo = calcular_icp({**organizacao, **{k: v for k, v in linha.items() "
     "if k != 'organization_id'}}, self.modelo, self.faixas)",
     ["fonte-nao-contamina-o-score"]),
    ("organizacao-inexistente-vira-erro-de-porta",
     '                if organizacao is None:\n'
     '                    resultado["veredito"] = VER_RECUSADA\n'
     '                    resultado["motivos"] = ["ORGANIZACAO_NAO_ENCONTRADA"]',
     "                if False:\n                    pass",
     ["fluxo-organizacao-inexistente-recusa-sem-escrever"]),
    ("auditoria-sem-conferir-o-rc",
     "        if rc_run != 0:",
     "        if False:",
     ["auditoria-que-nao-registra-vira-erro"]),
    ("sql-fecha-o-evento-sem-o-score",
     "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {scores} WHERE id = {sid})\\n",
     "WHERE idempotency_key = {chave}\\n",
     ["sql-de-gravacao-nao-fecha-o-evento-sem-o-score"]),
    ("sem-idempotencia-no-sql",
     "  ON CONFLICT (idempotency_key) DO NOTHING\\n",
     "\\n",
     ["sql-de-gravacao-exige-a-chave-unica"]),
    ("desfazer-apaga-a-auditoria",
     '        "DELETE FROM {scores} WHERE id IN ({ids});\\n"',
     '        "DELETE FROM {scores} WHERE id IN ({ids});\\n"\n'
     '        "DELETE FROM sales_intelligence.agent_runs WHERE correlation_id = \'x\';\\n"',
     ["desfazer-nao-apaga-auditoria"]),
    ("jev-deixa-de-ser-fail-closed",
     "    recibo = validar_recibo_jev(recibo)\n    return {",
     "    return {",
     ["jev-e-fail-closed-e-nao-executa-llm"]),
    ("raiz-por-profundidade-do-arquivo",
     "    for base in (Path(__file__).resolve().parent, Path.cwd()):\n"
     "        for pasta in (base,) + tuple(base.parents):\n"
     "            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():\n"
     "                return pasta\n"
     "    return Path.cwd()",
     "    return Path(__file__).resolve().parents[3]",
     ["raiz-do-agente-nao-depende-da-profundidade-do-arquivo"]),
]


def aplicar_mutacao(mutacao, destino: Path):
    nome, alvo, substituto, itens_esperados = mutacao
    texto = CODIGO_PADRAO.read_text(encoding="utf-8")
    if texto.count(alvo) != 1:
        return None, "ancora da mutacao nao casa exatamente 1 vez (%d)" % texto.count(alvo)
    destino.write_text(texto.replace(alvo, substituto), encoding="utf-8")
    return itens_esperados, "aplicada"


def autoteste():
    print("\n=== AUTOTESTE (mutacoes em COPIA do icp_score.py) ===")
    ok_total = falhas_total = 0
    temporario = Path(tempfile.mkdtemp(prefix="icp-autoteste-"))
    try:
        for mutacao in MUTACOES:
            nome = mutacao[0]
            # A copia vive um nivel ABAIXO do tempdir: assim ela importa de qualquer TMPDIR
            # (a prova de que o agente importa de diretorio raso e item proprio da suite).
            copia = temporario / "mut" / ("icp-%s.py" % nome)
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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Suite do agente ICP Score v1 (TRE-W5-E01-T01)")
    p.add_argument("--codigo", default=str(CODIGO_PADRAO), help="caminho de outro icp_score.py")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not ITENS:
        print("FALHOU a suite nao executou nenhum item")
        return 1
    modulo = carregar_modulo(Path(args.codigo))
    print("=== SUITE DO AGENTE ICP SCORE v1 (%s) ===" % args.codigo)
    ok, falhas, resultados = executar_suite(modulo)
    print("\nRESULTADO: ICP_SCORE_SUITE_%s (%d itens, %d falhas)"
          % ("OK" if falhas == 0 else "FALHOU", ok + falhas, falhas))
    sucesso = falhas == 0
    if args.autoteste:
        sucesso = autoteste() and sucesso
        print("RESULTADO FINAL: ICP_SCORE_%s" % ("OK" if sucesso else "FALHOU"))
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
