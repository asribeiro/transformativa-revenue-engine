#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do AGENTE SCOUT v1 (card TRE-W4-E01-T01) — um comando, um veredito.

    python3 scripts/agentes/verificar_agente_scout.py
    python3 scripts/agentes/verificar_agente_scout.py --autoteste
    python3 scripts/agentes/verificar_agente_scout.py --codigo <caminho de outro scout.py>

O que ela prova (SEM banco e SEM rede): o contrato do agente existe e nao divergiu do Data
Contract V1.0; as regras de identidade (identificador FORTE, prioridade, invalido
descartado, ambiguidade reportada) valem item por item; a idempotencia esta no SQL, nao na
prosa; a guarda de escrita recusa DDL e escrita fora das 4 tabelas declaradas; o gate do JEV
e fail-closed; e o fluxo completo (criar, replay idempotente, revisao, recusar, desfazer)
se comporta como o contrato do card — medido numa PORTA DE ROTEIRO (implementacao da porta
declarada, nao substituicao do alvo: a suite nao usa dublê de biblioteca nenhuma).

AUTOTESTE (`--autoteste`): cada mutacao e aplicada a uma COPIA do `scout.py` e a suite tem
de REPROVAR o item correspondente — mutacao que passa em silencio e buraco de verificacao.
Mutacao que nao se aplica (ancora de texto mudou) tambem reprova: e buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = SCOUT_SUITE_OK · 1 = SCOUT_SUITE_FALHOU (o log aponta o item) ·
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
CODIGO_PADRAO = RAIZ / "hermes/agents/scout/scout.py"
CONTRATO_AGENTE = RAIZ / "hermes/agents/scout/agente-scout-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC_ARQUITETURA = RAIZ / "docs/architecture/agente-scout-v1.md"
RUNBOOK = RAIZ / "docs/runbooks/agente-scout.md"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO_FONTE = RAIZ / "hermes/agents/scout/exemplos/candidatas-exemplo.jsonl"

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

    Ela implementa a porta do agente (mesma interface) e roda a mesma guarda de escrita —
    nao substitui o alvo por dublê de biblioteca.
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
        rc, saida, erro = self.respostas.pop(0)
        return rc, saida, erro


def carregar_modulo(caminho):
    spec = importlib.util.spec_from_file_location("scout_sob_teste", str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def candidata(**campos):
    base = {"trade_name": "Empresa Teste", "source": "WEB"}
    base.update(campos)
    return base


def lista_sql(texto):
    """Divide uma lista SQL por virgula respeitando strings entre apostrofos."""
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
def _ (ctx):
    return ctx.modulo.AGENTE == "scout" and ctx.modulo.PAPEL == "discovery", "modulo carregado"


@item("contrato-do-agente-tem-os-campos-exigidos")
def _ (ctx):
    exigidos = ("agente", "papel", "versao", "card", "entrada", "fontes", "identidade",
                "vereditos", "escrita", "guardrails", "auditoria", "desfazer", "exit_codes",
                "lacunas_declaradas")
    faltando = [c for c in exigidos if c not in ctx.contrato]
    return not faltando, "faltando: %s" % faltando if faltando else "14 campos presentes"


@item("versao-e-papel-espelhados")
def _ (ctx):
    return (ctx.modulo.VERSAO == ctx.contrato["versao"]
            and ctx.modulo.PAPEL == ctx.contrato["papel"]), \
        "%s/%s" % (ctx.modulo.VERSAO, ctx.modulo.PAPEL)


@item("fortes-iguais-ao-data-contract-na-ordem")
def _ (ctx):
    do_agente = tuple(ctx.modulo.fortes_do_contrato_de_dados(RAIZ))
    do_contrato = tuple(ctx.contrato_dados["dedup"]["strong"])
    do_declarado = tuple(ctx.contrato["identidade"]["fortes_por_prioridade"])
    ok = do_agente == do_contrato == do_declarado
    return ok, "agente=%s data_contract=%s declarado=%s" % (do_agente, do_contrato, do_declarado)


@item("status-discovered-e-do-vocabulario-do-contrato")
def _ (ctx):
    return (ctx.modulo.STATUS_ORGANIZACAO in
            ctx.contrato_dados["vocabularies"]["organizations.status"]), \
        ctx.modulo.STATUS_ORGANIZACAO


@item("as-4-tabelas-declaradas-existem-no-ddl")
def _ (ctx):
    faltando = [t for t in ctx.modulo.TABELAS_PERMITIDAS if ("CREATE TABLE %s" % t) not in ctx.ddl]
    return not faltando, "faltando no DDL: %s" % faltando if faltando else "4/4"


@item("fontes-espelhadas-e-nao-vazias")
def _ (ctx):
    return (tuple(ctx.modulo.FONTES) == tuple(ctx.contrato["fontes"])
            and len(ctx.modulo.FONTES) >= 6), "%d fontes" % len(ctx.modulo.FONTES)


@item("vereditos-espelhados")
def _ (ctx):
    do_contrato = set(ctx.contrato["vereditos"])
    do_modulo = set(ctx.modulo.VEREDITOS)
    return do_modulo == do_contrato, "modulo=%s contrato=%s" % (sorted(do_modulo), sorted(do_contrato))


@item("exit-codes-espelhados")
def _ (ctx):
    esperado = {ctx.modulo.EXIT_OK: "OK", ctx.modulo.EXIT_FALHOU: "FALHOU",
                ctx.modulo.EXIT_USO: "uso incorreto",
                ctx.modulo.EXIT_RECUSOU_AMBIENTE: "recusou o ambiente (prod)",
                ctx.modulo.EXIT_FONTE: "fonte ilegivel/inexistente"}
    divergentes = [codigo for codigo, rotulo in esperado.items()
                   if not str(ctx.contrato["exit_codes"].get(str(codigo), "")).startswith(rotulo)]
    return not divergentes, "divergentes: %s" % divergentes if divergentes else sorted(esperado)


@item("status-de-agent_runs-espelhados")
def _ (ctx):
    return ctx.modulo.STATUS_AGENT_RUNS == ctx.contrato["status_de_agent_runs"], \
        str(sorted(set(ctx.modulo.STATUS_AGENT_RUNS.values())))


@item("doc-de-arquitetura-existe-e-declara-as-lacunas")
def _ (ctx):
    if not DOC_ARQUITETURA.is_file():
        return False, "ausente %s" % DOC_ARQUITETURA
    texto = DOC_ARQUITETURA.read_text(encoding="utf-8")
    exigidos = ["ACCEPTANCE", "TEST", "ROLLBACK", "RISK", "Lacunas", "idempotenc"]
    faltando = [t for t in exigidos if t.lower() not in texto.lower()]
    return not faltando, "faltando: %s" % faltando if faltando else "%d bytes" % len(texto)


@item("runbook-existe-e-diz-onde-roda")
def _ (ctx):
    if not RUNBOOK.is_file():
        return False, "ausente %s" % RUNBOOK
    texto = RUNBOOK.read_text(encoding="utf-8")
    return ("VPS" in texto and "prefixo" in texto and "--desfazer" in texto), "runbook versionado"


@item("exemplo-de-fonte-versionado-e-legivel")
def _ (ctx):
    if not EXEMPLO_FONTE.is_file():
        return False, "ausente %s" % EXEMPLO_FONTE
    fortes = tuple(ctx.contrato["identidade"]["fortes_por_prioridade"])
    linhas = [l for l in EXEMPLO_FONTE.read_text(encoding="utf-8").splitlines() if l.strip()]
    validas, problemas = 0, []
    for linha in linhas:
        lida = json.loads(linha)
        avaliacao = ctx.modulo.validar_candidata(lida, fortes)
        if avaliacao["problemas"] or not avaliacao["validos"]:
            problemas.append(avaliacao["problemas"] or "sem identificador forte valido")
        else:
            validas += 1
    return (validas == len(linhas) and validas >= 3), \
        "%d/%d exemplos validos %s" % (validas, len(linhas), problemas or "")


# ---------------------------------------------------------------------------------------
# 2. Normalizacao, validacao e decisao (funcoes puras)
# ---------------------------------------------------------------------------------------
@item("cnpj-digito-verificador")
def _ (ctx):
    validos = ["11.222.333/0001-81", "45723174000110", "08.234.567/0001-34"]
    invalidos = ["11.222.333/0001-00", "11111111111111", "123", "1122233300018a"]
    erros = [c for c in validos if not ctx.modulo.cnpj_valido(c)]
    erros += [c for c in invalidos if ctx.modulo.cnpj_valido(c)]
    return not erros, "aceitos 3/3, recusados 4/4" if not erros else "erros: %s" % erros


@item("domain-normaliza-e-recusa-o-invalido")
def _ (ctx):
    casos = [("https://www.ValeForte.com.br/inicio", "valeforte.com.br"),
             ("HTTP://agrosmart-analytics.com.br:8443/x?y=1", "agrosmart-analytics.com.br"),
             ("sub.exemplo.com.", "sub.exemplo.com")]
    erros = [(bruto, esperado, ctx.modulo.normalizar_domain(bruto))
             for bruto, esperado in casos if ctx.modulo.normalizar_domain(bruto) != esperado]
    invalidos = ["sem ponto", "espaco no meio.com", "", "http://"]
    recusados = [v for v in invalidos if ctx.modulo.domain_valido(v)]
    return (not erros and not recusados), \
        "erros=%s recusados_ok=%s" % (erros, not recusados)


@item("linkedin-normaliza-empresa-e-recusa-perfil-pessoal")
def _ (ctx):
    bons = [("https://br.linkedin.com/company/clinica-sao-lucas/",
             "https://www.linkedin.com/company/clinica-sao-lucas"),
            ("linkedin.com/company/Acme-SP?trk=abc",
             "https://www.linkedin.com/company/acme-sp")]
    erros = [(b, e, ctx.modulo.normalizar_linkedin(b)) for b, e in bons
             if ctx.modulo.normalizar_linkedin(b) != e]
    ruins = ["https://www.linkedin.com/in/anderson-ribeiro", "https://linkedin.com/feed/"]
    recusados = [v for v in ruins if ctx.modulo.linkedin_valido(v)]
    return (not erros and not recusados), "erros=%s recusados_ok=%s" % (erros, not recusados)


@item("identidade-segue-a-prioridade-do-contrato")
def _ (ctx):
    fortes = tuple(ctx.contrato["identidade"]["fortes_por_prioridade"])
    c = candidata(cnpj="11.222.333/0001-81", domain="valeforte.com.br",
                  linkedin_url="https://www.linkedin.com/company/vale-forte")
    tipos = [t for t, _ in ctx.modulo.identificadores_validos(c, fortes)]
    return tipos == list(fortes), str(tipos)


@item("identificador-forte-invalido-e-descartado")
def _ (ctx):
    fortes = tuple(ctx.contrato["identidade"]["fortes_por_prioridade"])
    c = candidata(cnpj="11.222.333/0001-00", domain="valeforte.com.br")
    ids = ctx.modulo.identificadores_validos(c, fortes)
    return ids == [("domain", "valeforte.com.br")], str(ids)


@item("sem-forte-declarado-vai-para-revisao")
def _ (ctx):
    return ctx.modulo.decidir_veredito([], [], []) == \
        (ctx.modulo.VER_REVISAO, "SEM_IDENTIFICADOR_FORTE"), "REVISAO_IDENTIDADE"


@item("carrega-forte-e-nenhum-valido-recusa")
def _ (ctx):
    return ctx.modulo.decidir_veredito(["cnpj"], [], []) == \
        (ctx.modulo.VER_RECUSADA, "IDENTIFICADOR_FORTE_INVALIDO"), "RECUSADA"


@item("forte-valido-sem-casamento-cria")
def _ (ctx):
    return ctx.modulo.decidir_veredito(["cnpj"], [("cnpj", "11222333000181")], []) == \
        (ctx.modulo.VER_CRIADA, None), "CRIADA"


@item("forte-valido-com-um-casamento-nao-duplica")
def _ (ctx):
    return ctx.modulo.decidir_veredito(["cnpj"], [("cnpj", "11222333000181")],
                                       ["id-existente"]) == (ctx.modulo.VER_JA_EXISTE, None), \
        "JA_EXISTE"


@item("fortes-casando-com-duas-organizacoes-vai-para-revisao")
def _ (ctx):
    return ctx.modulo.decidir_veredito(["cnpj", "domain"],
                                       [("cnpj", "11222333000181"), ("domain", "x.com.br")],
                                       ["id-a", "id-b"]) == \
        (ctx.modulo.VER_REVISAO, "CONFLITO_DE_IDENTIDADE_FORTE"), "REVISAO por conflito"


@item("validacao-de-candidata-recusa-nome-fonte-e-identificador")
def _ (ctx):
    fortes = tuple(ctx.contrato["identidade"]["fortes_por_prioridade"])
    casos = [({"source": "WEB"}, "SEM_NOME"),
             ({"domain": "a.com.br", "trade_name": "Sem fonte"}, "SEM_FONTE"),
             (candidata(domain="a.com.br", source="PANFLETO"), "FONTE_DESCONHECIDA"),
             (candidata(cnpj="11.222.333/0001-00"), "IDENTIFICADOR_FORTE_INVALIDO")]
    erros = [(c, esperado, ctx.modulo.validar_candidata(c, fortes)["problemas"])
             for c, esperado in casos
             if esperado not in ctx.modulo.validar_candidata(c, fortes)["problemas"]]
    return not erros, "4/4 casos" if not erros else "erros: %s" % erros


@item("chave-de-idempotencia-e-deterministica-e-por-identidade")
def _ (ctx):
    a = ctx.modulo.chave_idempotencia("cnpj", "11222333000181")
    b = ctx.modulo.chave_idempotencia("cnpj", "11222333000181")
    c = ctx.modulo.chave_idempotencia("domain", "x.com.br")
    return (a == b and a != c and a.startswith("scout:org:cnpj:")), a


@item("faixa-de-empregados-segue-o-vocabulario")
def _ (ctx):
    bandas = ctx.contrato_dados["vocabularies"]["employee_band"]
    casos = [(420, "300_499"), (95, "70_149"), (150, "150_299"), (1001, "GT_1000"), (None, None)]
    erros = [(n, e, ctx.modulo._faixa_de_empregados(n)) for n, e in casos
             if ctx.modulo._faixa_de_empregados(n) != e]
    usadas = [ctx.modulo._faixa_de_empregados(n) for n, _ in casos if n is not None]
    fora = [b for b in usadas if b not in bandas]
    return (not erros and not fora), "erros=%s fora_do_vocabulario=%s" % (erros, fora)


# ---------------------------------------------------------------------------------------
# 3. SQL, guarda de escrita e idempotencia no SQL
# ---------------------------------------------------------------------------------------
@item("sql-de-ingestao-exige-a-chave-unica")
def _ (ctx):
    valores = {"legal_name": "X", "trade_name": None, "domain": "x.com.br", "linkedin_url": None,
               "cnpj": None, "website_url": None, "industry_code": None, "industry_name": None,
               "employee_count": None, "employee_band": None, "revenue_estimate": None,
               "unit_count": None, "city": None, "state": None, "country_code": "BR",
               "business_model": None, "status": "DISCOVERED", "source": "WEB"}
    sql = ctx.modulo.sql_ingerir("11111111-1111-1111-1111-111111111111",
                                 "22222222-2222-2222-2222-222222222222",
                                 "scout:org:domain:x.com.br", valores, {"origem": "scout"})
    exigidos = ["ON CONFLICT (idempotency_key) DO NOTHING", "RETURNING 'SCOUT_CRIADA'",
                "sales_intelligence.sync_events", "sales_intelligence.organizations",
                "'DISCOVERED'", "BEGIN;", "COMMIT;", "ON CONFLICT (id) DO NOTHING"]
    faltando = [t for t in exigidos if t not in sql]
    return not faltando, "faltando: %s" % faltando if faltando else "%d clausulas" % len(exigidos)


@item("ingestao-fecha-a-sincronia-em-instrucao-propria")
def _ (ctx):
    # As CTEs de escrita e a instrucao principal rodam no MESMO snapshot: fechar o sync_event
    # dentro da mesma instrucao do INSERT nao enxerga a linha recem-inserida (medido no aceite).
    # Por isso o fechamento TEM de ser uma instrucao propria, depois de um `;`.
    linha = ctx.modulo.montar_linha_organizacao(
        candidata(domain="x.com.br"), {"fonte": "WEB", "validos": [("domain", "x.com.br")]})
    sql = ctx.modulo.sql_ingerir("11111111-1111-1111-1111-111111111111",
                                 "22222222-2222-2222-2222-222222222222",
                                 "scout:org:domain:x.com.br", linha, {"origem": "scout"})
    ok = re.search(r"RETURNING id;\s*\nUPDATE sales_intelligence\.sync_events", sql) is not None
    return ok, "fechamento em instrucao propria" if ok else "fechamento dentro da CTE (snapshot errado)"


@item("consulta-de-identidade-e-somente-leitura")
def _ (ctx):
    sql = ctx.modulo.sql_consultar_identidade([("cnpj", "11222333000181")])
    proibido = [t for t in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER", "DROP")
                if re.search(r"\b%s\b" % t, sql.upper())]
    return (sql.strip().upper().startswith("SELECT") and not proibido
            and "deleted_at IS NULL" in sql), \
        "somente SELECT (com filtro de excluida); proibidos: %s" % proibido


@item("consulta-de-identidade-escopa-por-forte-presente")
def _ (ctx):
    sql = ctx.modulo.sql_consultar_identidade([("cnpj", "11222333000181"),
                                               ("domain", "x.com.br")])
    exigidos = ["cnpj IS NOT NULL AND cnpj = '11222333000181'",
                "domain IS NOT NULL AND domain = 'x.com.br'",
                " OR "]
    faltando = [t for t in exigidos if t not in sql]
    return not faltando, "faltando: %s" % faltando if faltando else "2 termos com OR agrupado"


@item("nenhum-sql-atualiza-organizations")
def _ (ctx):
    valores = {"status": "DISCOVERED", "source": "WEB"}
    gerados = [
        ctx.modulo.sql_ingerir("1", "2", "scout:org:domain:x.com.br", valores, {"origem": "scout"}),
        ctx.modulo.sql_registrar_execucao("r", "c", None, "COMPLETED", {}, {}, "t0", "t1"),
        ctx.modulo.sql_pedir_revisao("a", {"trade_name": "X"}, "SEM_IDENTIFICADOR_FORTE", [], "c"),
        ctx.modulo.sql_consultar_identidade([("domain", "x.com.br")]),
    ]
    proibidos = [s[:60] for s in gerados if "UPDATE sales_intelligence.organizations" in s.upper()]
    return not proibidos, "0 UPDATE em organizations em %d SQLs gerados" % len(gerados)


@item("insert-de-organizations-casa-colunas-com-valores")
def _ (ctx):
    linha = ctx.modulo.montar_linha_organizacao(
        candidata(domain="x.com.br"), {"fonte": "WEB", "validos": [("domain", "x.com.br")]})
    sql = ctx.modulo.sql_ingerir("11111111-1111-1111-1111-111111111111",
                                 "22222222-2222-2222-2222-222222222222",
                                 "scout:org:domain:x.com.br", linha, {"origem": "scout"})
    m = re.search(r"INSERT INTO sales_intelligence\.organizations \(([^)]*)\)\s*\n\s*"
                  r"SELECT (.*?)\s+FROM claim", sql, re.S)
    if not m:
        return False, "nao achei INSERT ... SELECT FROM claim no SQL gerado"
    colunas, valores = lista_sql(m.group(1)), lista_sql(m.group(2))
    return len(colunas) == len(valores), "%d colunas x %d valores" % (len(colunas), len(valores))


@item("colunas-dos-inserts-existem-no-ddl")
def _ (ctx):
    problemas = []
    colunas_org = [c.strip() for c in ctx.modulo._COLUNAS_ORGANIZACAO.split(",")]
    fora = [c for c in colunas_org if c not in ctx.colunas("sales_intelligence.organizations")]
    if fora:
        problemas.append("organizations: %s" % fora)
    valores = {"status": "DISCOVERED", "source": "WEB"}
    sqls = {
        "sales_intelligence.organizations": ctx.modulo.sql_ingerir(
            "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
            "scout:org:domain:x.com.br", valores, {}),
        "sales_intelligence.agent_runs": ctx.modulo.sql_registrar_execucao(
            "33333333-3333-3333-3333-333333333333", "corr", None, "COMPLETED", {}, {}, "t0", "t1"),
        "sales_intelligence.sync_events": ctx.modulo.sql_desfazer(["id-1"], "corr", "se"),
        "sales_intelligence.human_approvals": ctx.modulo.sql_pedir_revisao("a", {}, "m", [], "c"),
    }
    for tabela, sql in sqls.items():
        listas = re.findall(r"INSERT INTO %s\s*\(([^)]*)\)" % re.escape(tabela), sql, re.I)
        if not listas:
            problemas.append("%s: nenhum INSERT encontrado para conferir" % tabela)
        for lista in listas:
            fora = [c.strip() for c in lista.split(",") if c.strip() not in ctx.colunas(tabela)]
            if fora:
                problemas.append("%s: %s" % (tabela, fora))
    return not problemas, "sem coluna inventada" if not problemas else str(problemas)


@item("guarda-recusa-ddl")
def _ (ctx):
    recusados = 0
    for sql in ("DROP TABLE sales_intelligence.organizations;",
                "ALTER TABLE sales_intelligence.organizations ADD COLUMN x int;",
                "TRUNCATE sales_intelligence.organizations;"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            recusados += 1
    return recusados == 3, "%d/3 recusados" % recusados


@item("guarda-recusa-escrita-fora-do-declarado")
def _ (ctx):
    casos = ["INSERT INTO sales_intelligence.contacts (id) VALUES ('x');",
             "UPDATE sales_intelligence.organizations SET status='X';",
             "DELETE FROM sales_intelligence.organizations WHERE id='x';",
             "INSERT INTO public.tabela_qualquer (id) VALUES ('x');"]
    recusados = 0
    for sql in casos:
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            recusados += 1
    ok_desfazer = True
    try:
        ctx.modulo.validar_sql("DELETE FROM sales_intelligence.organizations WHERE id='x';",
                               permitir_remocao=True)
    except ctx.modulo.GuardaDeEscritaViolada:
        ok_desfazer = False
    return (recusados == 4 and ok_desfazer), \
        "4/4 recusados; desfazer explicito liberado=%s" % ok_desfazer


@item("desfazer-e-escopado-na-rodada-e-nao-apaga-o-que-nao-criou")
def _ (ctx):
    sql = ctx.modulo.sql_desfazer(["id-1", "id-2"], "corr-1", "se-1")
    exigidos = ["DELETE FROM sales_intelligence.organizations WHERE id IN ('id-1', 'id-2')",
                "status = 'DISCOVERED'", "correlation_id", "ROLLBACK"]
    faltando = [t for t in exigidos if t not in sql]
    return not faltando, "faltando: %s" % faltando if faltando else "escopo declarado"


@item("sql-de-revisao-vai-para-a-fila-humana-pendente")
def _ (ctx):
    sql = ctx.modulo.sql_pedir_revisao("ap-1", {"trade_name": "X"}, "SEM_IDENTIFICADOR_FORTE",
                                       [{"organization_id": "org-a", "tipo": "cnpj"}], "corr")
    exigidos = ["sales_intelligence.human_approvals", "'SCOUT_IDENTITY_REVIEW'", "'PENDING'",
                "SEM_IDENTIFICADOR_FORTE", "org-a"]
    faltando = [t for t in exigidos if t not in sql]
    return not faltando, "faltando: %s" % faltando if faltando else "fila humana com evidencia"


# ---------------------------------------------------------------------------------------
# 4. Gate do JEV e limites do papel
# ---------------------------------------------------------------------------------------
@item("llm-sem-recibo-e-recusada")
def _ (ctx):
    try:
        ctx.modulo.chamar_llm("prompt")
    except ctx.modulo.ReciboJEVInvalido:
        return True, "ReciboJEVInvalido"
    return False, "passou sem recibo"


@item("llm-com-recibo-incompleto-degradado-ou-bloqueado-e-recusada")
def _ (ctx):
    recusados = 0
    casos = [{}, {"decision_id": "d", "lane": "medium"}, {"decision_id": "d", "lane": "medium",
             "outcome": "ESCALATE"}, {"decision_id": "d", "lane": "medium", "outcome": "BLOCK"},
             {"decision_id": "d", "lane": "turbo", "outcome": "PASS"}]
    for caso in casos:
        try:
            ctx.modulo.chamar_llm("prompt", recibo=caso)
        except ctx.modulo.ReciboJEVInvalido:
            recusados += 1
    return recusados == 5, "%d/5 recusados" % recusados


@item("llm-com-recibo-valido-autoriza-e-nao-executa")
def _ (ctx):
    recibo = {"decision_id": "dec-1", "lane": "medium", "outcome": "PASS"}
    saida = ctx.modulo.chamar_llm("prompt", recibo=recibo, provedor="deepseek", modelo="x")
    return (saida["autorizado"] is True and saida["executado"] is False
            and len(saida["prompt_sha256"]) == 64), "autorizado, nada executado (lacuna declarada)"


@item("sem-cliente-de-banco-rede-ou-contato-externo")
def _ (ctx):
    texto = CODIGO_PADRAO.read_text(encoding="utf-8")
    proibidos = ["psycopg", "requests", "httpx", "smtplib", "imaplib", "urllib",
                 "boto3", "xmlrpc"]
    achados = [p for p in proibidos if p in texto]
    return not achados, "achados: %s" % achados if achados else "stdlib + psql apenas"


@item("a-suite-nao-usa-dublê-de-biblioteca")
def _ (ctx):
    texto = Path(__file__).read_text(encoding="utf-8")
    padroes = [r"unit" + r"test\.mock", r"import\s+" + r"mock", r"from\s+" + r"mock",
               r"monkey" + r"patch", r"mocker\."]
    achados = [p for p in padroes if re.search(p, texto)]
    return not achados, "achados: %s" % achados if achados else "porta de roteiro, sem dublê"


@item("estrutura-cobre-os-artefatos-do-card")
def _ (ctx):
    texto = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    exigidos = ["hermes/agents/scout/scout.py", "scripts/agentes/verificar_agente_scout.py",
                "docs/architecture/agente-scout-v1.md", "docs/runbooks/agente-scout.md",
                "scripts/agentes/teste_scout_aceite.sh"]
    faltando = [t for t in exigidos if t not in texto]
    return not faltando, "faltando no gate de estrutura: %s" % faltando if faltando else "5/5"


# ---------------------------------------------------------------------------------------
# 5. Fluxo (porta de roteiro)
# ---------------------------------------------------------------------------------------
@item("fluxo-cria-organizacao-e-registra-execucao")
def _ (ctx):
    porta = PortaRoteiro(respostas=[(0, "", ""), (0, "SCOUT_CRIADA\n", ""), (0, "", "")],
                         modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev",
                              correlation_id="corr-teste")
    r = agente.processar(candidata(domain="nova-empresa.com.br"))
    tipos = ["ingestao" if "WITH claim" in s else
             ("consulta" if s.strip().upper().startswith("SELECT") else "agent_runs")
             for s in porta.chamadas]
    ok = (r["veredito"] == ctx.modulo.VER_CRIADA
          and r["organization_id"] and r["sync_event_id"]
          and r["status_agent_runs"] == "COMPLETED"
          and tipos == ["consulta", "ingestao", "agent_runs"])
    return ok, "veredito=%s chamadas=%s" % (r["veredito"], tipos)


@item("fluxo-replay-idempotente-nao-duplica-nem-marca-sucesso")
def _ (ctx):
    porta = PortaRoteiro(respostas=[(0, "", ""), (0, "", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    r = agente.processar(candidata(domain="nova-empresa.com.br"))
    insercao = [s for s in porta.chamadas if "WITH claim" in s]
    return (r["veredito"] == ctx.modulo.VER_JA_EXISTE
            and "IDEMPOTENCIA_REPLAY" in r["motivos"]
            and r["organization_id"] is None and len(insercao) == 1), \
        "veredito=%s motivos=%s" % (r["veredito"], r["motivos"])


@item("fluxo-sem-ambiente-ou-prod-recusa-antes-de-qualquer-escrita")
def _ (ctx):
    resultados = []
    for ambiente in (None, "prod", "staging"):
        porta = PortaRoteiro(modulo=ctx.modulo)
        agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente=ambiente)
        try:
            agente.conferir_ambiente()
            resultados.append((ambiente, "passou"))
        except ctx.modulo.RecusaDeAmbiente:
            resultados.append((ambiente, "recusado/%d chamadas" % len(porta.chamadas)))
    ok = all(v.startswith("recusado/0") for _, v in resultados)
    return ok, str(resultados)


@item("fluxo-revisao-de-identidade-cria-aprovacao-e-nao-cria-organizacao")
def _ (ctx):
    porta = PortaRoteiro(respostas=[(0, "", ""), (0, "", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    r = agente.processar(candidata(trade_name="Sem Identificador", source="EVENTOS"))
    escritas = [s for s in porta.chamadas if "INSERT" in s.upper()]
    so_approval = all("human_approvals" in s or "agent_runs" in s for s in escritas)
    return (r["veredito"] == ctx.modulo.VER_REVISAO and r["human_approval_id"]
            and r["organization_id"] is None and so_approval
            and r["status_agent_runs"] == "REVIEW_REQUIRED"), \
        "veredito=%s escritas=%d" % (r["veredito"], len(escritas))


@item("fluxo-recusa-nao-escreve-nada-alem-da-auditoria")
def _ (ctx):
    porta = PortaRoteiro(respostas=[(0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    r = agente.processar(candidata(cnpj="11.222.333/0001-00", source="WEB"))
    escritas = [s for s in porta.chamadas if "INSERT" in s.upper()]
    return (r["veredito"] == ctx.modulo.VER_RECUSADA and len(escritas) == 1
            and "agent_runs" in escritas[0] and r["status_agent_runs"] == "REJECTED"), \
        "veredito=%s escritas=%d" % (r["veredito"], len(escritas))


@item("marca-de-criada-conta-mesmo-com-carimbo-de-comando")
def _ (ctx):
    # A porta real (psql) imprime carimbos de comando junto do RETURNING. Confundir isso com
    # replay ja custou uma rodada inteira de aceite: aqui a saida suja tem de dar CRIADA.
    porta = PortaRoteiro(respostas=[(0, "", ""), (0, "BEGIN\nSCOUT_CRIADA\nCOMMIT\n", ""),
                                    (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    r = agente.processar(candidata(domain="nova-empresa.com.br"))
    return (r["veredito"] == ctx.modulo.VER_CRIADA and r["organization_id"]
            and r["sync_event_id"]), "veredito=%s motivos=%s" % (r["veredito"], r["motivos"])


@item("candidata-invalida-nao-chega-a-insercao-no-fluxo")
def _ (ctx):
    porta = PortaRoteiro(modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    casos = [candidata(legal_name="", trade_name="", domain="sem-nome.com.br"),
             candidata(domain="x.com.br", source="PANFLETO"),
             candidata(legal_name="Sem fonte", domain="y.com.br", source=None)]
    vereditos = [agente.processar(c)["veredito"] for c in casos]
    insercoes = [s for s in porta.chamadas if "INSERT INTO sales_intelligence.organizations" in s]
    return (vereditos == [ctx.modulo.VER_RECUSADA] * 3 and not insercoes), \
        "vereditos=%s insercoes_de_organizacao=%d" % (vereditos, len(insercoes))


@item("modo-planejar-nao-toca-a-porta")
def _ (ctx):
    porta = PortaRoteiro(modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    relatorio = agente.rodar([candidata(domain="a.com.br"),
                              candidata(trade_name="Sem id", source="EVENTOS"),
                              candidata(cnpj="11.222.333/0001-00")], planejar=True)
    esperado = {ctx.modulo.PLANEJADO_CRIAR: 1, ctx.modulo.PLANEJADO_REVISAR: 1,
                ctx.modulo.PLANEJADO_RECUSAR: 1}
    return (len(porta.chamadas) == 0 and relatorio["por_veredito"] == esperado), \
        "0 chamadas; %s" % relatorio["por_veredito"]


@item("auditoria-uma-linha-por-candidata-com-correlacao-do-lote")
def _ (ctx):
    porta = PortaRoteiro(respostas=[(0, "", ""), (0, "SCOUT_CRIADA\n", ""), (0, "", ""),
                                    (0, "", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev", correlation_id="lote-1")
    agente.rodar([candidata(domain="a.com.br"), candidata(domain="b.com.br")])
    registros = [s for s in porta.chamadas if "agent_runs" in s and "INSERT" in s.upper()]
    ok = all(("'scout'" in s and "'discovery'" in s and "'lote-1'" in s) for s in registros)
    return (len(registros) == 2 and ok), "%d linhas de agent_runs, corr=lote-1" % len(registros)


@item("desfazer-e-dry-run-por-padrao-e-escopado-com-confirmo")
def _ (ctx):
    porta = PortaRoteiro(respostas=[(0, "id-1\nid-2\n", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    dry = agente.desfazer("corr-1")
    sem_escrita = len([s for s in porta.chamadas if "DELETE" in s.upper()]) == 0
    porta2 = PortaRoteiro(respostas=[(0, "id-1\nid-2\n", ""), (0, "", "")], modulo=ctx.modulo)
    agente2 = ctx.modulo.Scout(porta=porta2, raiz=RAIZ, ambiente="dev")
    cheio = agente2.desfazer("corr-1", confirmo=True)
    aplicado = [s for s in porta2.chamadas if "DELETE" in s.upper()]
    return (dry["dry_run"] is True and dry["apagadas"] == 0 and sem_escrita
            and cheio["apagadas"] == 2 and len(aplicado) == 1
            and "id-1" in aplicado[0] and "ROLLBACK" in aplicado[0]), \
        "dry-run sem DELETE; confirmo apagou 2"


@item("toda-rodada-passaria-pela-guarda-de-escrita")
def _ (ctx):
    porta = PortaRoteiro(respostas=[(0, "id-a\n", ""), (0, "", ""), (0, "", ""),
                                    (0, "SCOUT_CRIADA\n", ""), (0, "", "")], modulo=ctx.modulo)
    agente = ctx.modulo.Scout(porta=porta, raiz=RAIZ, ambiente="dev")
    agente.rodar([candidata(trade_name="Sem ident", source="EVENTOS"),
                  candidata(domain="c.com.br")])
    try:
        agente.desfazer("corr-x", confirmo=True)
    except Exception:
        pass
    violacoes = 0
    for sql in porta.chamadas:
        for operacao, padrao in ctx.modulo._ESCRITA:
            for tabela in padrao.findall(sql):
                if tabela.lower() not in ctx.modulo.TABELAS_PERMITIDAS:
                    violacoes += 1
    return violacoes == 0, "%d SQLs, 0 escrita fora das 4 tabelas" % len(porta.chamadas)


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
    ("sem-idempotencia-no-sql",
     "  ON CONFLICT (idempotency_key) DO NOTHING\\n", "\\n",
     ["sql-de-ingestao-exige-a-chave-unica"]),
    ("sem-forte-tambem-cria",
     '        if declarados:\n            return VER_RECUSADA, "IDENTIFICADOR_FORTE_INVALIDO"\n'
     '        return VER_REVISAO, "SEM_IDENTIFICADOR_FORTE"',
     '        return VER_CRIADA, None',
     ["sem-forte-declarado-vai-para-revisao", "carrega-forte-e-nenhum-valido-recusa"]),
    ("ambiente-desconhecido-deixa-de-ser-recusado",
     "        if self.ambiente not in AMBIENTES_PERMITIDOS:",
     "        if False:",
     ["fluxo-sem-ambiente-ou-prod-recusa-antes-de-qualquer-escrita"]),
    ("revisao-deixaria-de-ir-a-fila-humana",
     "            elif veredito == VER_REVISAO:",
     "            elif False:",
     ["fluxo-revisao-de-identidade-cria-aprovacao-e-nao-cria-organizacao"]),
    ("conflito-de-fortes-vira-ja-existe",
     '    return VER_REVISAO, "CONFLITO_DE_IDENTIDADE_FORTE"',
     "    return VER_JA_EXISTE, None",
     ["fortes-casando-com-duas-organizacoes-vai-para-revisao"]),
    ("cnpj-sem-digito-verificador",
     "    for pesos, pos in ((pesos1, 12), (pesos2, 13)):",
     "    for pesos, pos in ():",
     ["cnpj-digito-verificador"]),
    ("guarda-deixa-passar-ddl",
     "    if _DDL.search(sql):",
     "    if False and _DDL.search(sql):",
     ["guarda-recusa-ddl"]),
    ("escrita-em-contacts-liberada",
     "            if tabela.lower() not in TABELAS_PERMITIDAS:",
     "            if False:",
     ["guarda-recusa-escrita-fora-do-declarado"]),
]


def aplicar_mutacao(mutacao, destino: Path):
    nome, alvo, substituto, itens_esperados = mutacao
    texto = CODIGO_PADRAO.read_text(encoding="utf-8")
    if texto.count(alvo) != 1:
        return None, "ancora da mutacao nao casa exatamente 1 vez (%d)" % texto.count(alvo)
    destino.write_text(texto.replace(alvo, substituto), encoding="utf-8")
    return itens_esperados, "aplicada"


def autoteste():
    print("\n=== AUTOTESTE (mutacoes em COPIA do scout.py) ===")
    ok_total = falhas_total = 0
    temporario = Path(tempfile.mkdtemp(prefix="scout-autoteste-"))
    try:
        for mutacao in MUTACOES:
            nome = mutacao[0]
            copia = temporario / ("scout-%s.py" % nome)
            esperados, detalhe = aplicar_mutacao(mutacao, copia)
            if esperados is None:
                falhas_total += 1
                print("FALHOU mutacao %s -> %s (mutacao nao aplicada e buraco de verificacao)"
                      % (nome, detalhe))
                continue
            print("\n-- mutacao %s (tem de reprovar: %s)" % (nome, ", ".join(esperados)))
            modulo = carregar_modulo(copia)
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
    p = argparse.ArgumentParser(description="Suite do agente Scout v1 (TRE-W4-E01-T01)")
    p.add_argument("--codigo", default=str(CODIGO_PADRAO), help="caminho de outro scout.py")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not ITENS:
        print("FALHOU a suite nao executou nenhum item")
        return 1
    modulo = carregar_modulo(Path(args.codigo))
    print("=== SUITE DO AGENTE SCOUT v1 (%s) ===" % args.codigo)
    ok, falhas, resultados = executar_suite(modulo)
    print("\nRESULTADO: SCOUT_SUITE_%s (%d itens, %d falhas)"
          % ("OK" if falhas == 0 else "FALHOU", ok + falhas, falhas))
    sucesso = falhas == 0
    if args.autoteste:
        sucesso = autoteste() and sucesso
        print("RESULTADO FINAL: SCOUT_%s" % ("OK" if sucesso else "FALHOU"))
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
