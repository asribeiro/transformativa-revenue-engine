#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do AGENTE CONTACT RESEARCH v1 (card TRE-W4-E05-T01) — um comando, um veredito.

    python3 scripts/agentes/verificar_agente_contact_research.py
    python3 scripts/agentes/verificar_agente_contact_research.py --autoteste
    python3 scripts/agentes/verificar_agente_contact_research.py --codigo <caminho de outro .py>

O que ela prova (SEM banco e SEM rede): o contrato do agente existe e nao divergiu do Data
Contract V1.0 (fortes da empresa, vocabulario de `decision_role` e o evento `DECISION_MAKER_FOUND`
declarado em `events.pg_to_odoo`); a EMPRESA e' resolvida por identificador FORTE e este agente
NAO cria nem escreve empresa; o CONTATO e' resolvido pelo e-mail normalizado (comparacao que
normaliza os DOIS lados) e o que a fonte traz de invalido e' DESCARTADO com motivo; a coluna JA
preenchida nunca e' sobrescrita e a guarda de escrita recusa DDL, escrita em `organizations`,
enriquecimento sem COALESCE, DELETE fora do desfazer e coluna nao declarada no INSERT; o evento de
espelho e' MEDIDO e nao emitido (a v1 nao alimenta a dead-letter do consumidor); nenhum SQL toca
os flags de opt-out do titular; e o fluxo completo (criar, replay idempotente, enriquecer,
revisao, recusar, auditoria, desfazer) se comporta como o contrato do card — medido numa PORTA DE
ROTEIRO (implementacao da porta declarada, nao duble de biblioteca).

AUTOTESTE (`--autoteste`): cada mutacao e' aplicada a uma COPIA do arquivo apontado por `--codigo`
(o canonico, por padrao — as duas opcoes andam juntas de proposito: mutar o canonico enquanto se
testa outro arquivo e' prova contra alvo errado) e a suite tem de REPROVAR o item correspondente —
mutacao que passa em silencio e' buraco de verificacao. Mutacao que nao se aplica (ancora de texto
mudou), que nao declara item nenhum ou que declara item INEXISTENTE na suite tambem reprova: e'
buraco, nao alivio.

VOCABULARIO DE EXIT: 0 = CONTACT_RESEARCH_SUITE_OK · 1 = CONTACT_RESEARCH_SUITE_FALHOU (o log
aponta o item) · 2 = uso incorreto. Guarda de confiabilidade: etapa que roda 0 item REPROVA.
"""

from __future__ import annotations

import argparse
import copy
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
CODIGO_PADRAO = RAIZ / "hermes/agents/contact_research/contact_research.py"
CONTRATO_AGENTE = RAIZ / "hermes/agents/contact_research/agente-contact-research-v1.json"
CONTRATO_DADOS = RAIZ / "docs/data/data_contract_v1.json"
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC_ARQUITETURA = RAIZ / "docs/architecture/agente-contact-research-v1.md"
RUNBOOK = RAIZ / "docs/runbooks/agente-contact-research.md"
VERIFICADOR_ESTRUTURA = RAIZ / "scripts/verificar_estrutura.sh"
EXEMPLO_FONTE = RAIZ / "hermes/agents/contact_research/exemplos/contatos-exemplo.jsonl"
CONTRATO_CONSUMIDOR = RAIZ / "n8n/contracts/outbox-consumer.v1.json"
MODULO_SCOUT = RAIZ / "hermes/agents/scout/scout.py"
ACEITE = RAIZ / "scripts/agentes/teste_contact_research_aceite.sh"

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "aaaaaaaa-0000-4000-8000-000000000002"
CONTATO_A = "cccccccc-0000-4000-8000-000000000001"
CNPJ_A = "11.222.333/0001-81"
EMAIL_A = "marina.alves@valeforte.com.br"
COLUNAS_PROIBIDAS = ("id", "organization_id", "odoo_partner_id", "email_status",
                     "influence_score", "contactability_score", "relationship_score",
                     "do_not_contact", "opt_out_email", "opt_out_whatsapp", "created_at",
                     "updated_at", "deleted_at")
# Identidade do contato: a coluna pela qual ele e' identificado. O enriquecimento nunca a lista
# (escrever identidade seria mudar a chave do dado, nao preencher lacuna).
COLUNAS_DE_IDENTIDADE = ("email",)

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
        self.agente = modulo.ContactResearch(porta=PortaRoteiro(rota=[], modulo=modulo), raiz=raiz)
        # o codigo SOB TESTE (a copia mutada quando ha' prova de dente), nao o arquivo canonico
        self.codigo = Path(modulo.__file__).read_text(encoding="utf-8")

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

    def sql_criacao(self, contato=None):
        """O SQL de CRIACAO que o agente GERA (o alvo real da guarda e do aceite)."""
        return self.modulo.sql_ingerir_criar(
            CONTATO_A, ORG_A, "22222222-0000-4000-8000-000000000001",
            "contact:org:%s:%s" % (ORG_A, "a" * 64),
            {"contato": dict(contato or contato_valido())}, {"origem": "contact_research"})

    def sql_enriquecimento(self, colunas=("job_title",), contato=None):
        return self.modulo.sql_ingerir_enriquecer(
            CONTATO_A, ORG_A, "22222222-0000-4000-8000-000000000001",
            "contact:org:%s:%s" % (ORG_A, "a" * 64), list(colunas),
            {"contato": dict(contato or contato_valido())}, {"origem": "contact_research"})


class PortaRoteiro:
    """Porta de teste: responde o roteiro declarado, GUARDA o SQL e roda a guarda do agente.

    Ela implementa a porta do agente (mesma interface) — nao substitui o alvo por duble de
    biblioteca, e o SQL passa pela mesma guarda que roda em producao.
    """

    def __init__(self, rota=None, modulo=None):
        self.rota = list(rota or [])
        self.chamadas = []
        self.modos = []
        self.remocoes = []
        self.modulo = modulo

    def executar(self, sql, modo_contatos=None, permitir_remocao=False):
        if self.modulo is not None:
            self.modulo.validar_sql(sql, modo_contatos=modo_contatos,
                                    permitir_remocao=permitir_remocao)
        self.chamadas.append(sql)
        self.modos.append(modo_contatos)
        self.remocoes.append(permitir_remocao)
        if not self.rota:
            return 0, "", ""
        resposta = self.rota.pop(0)
        if callable(resposta):
            return resposta(sql)
        if isinstance(resposta, str):
            # atalho declarado: string e' saida de sucesso; erro e' declarado como tupla
            return 0, resposta, ""
        return resposta


def carregar_modulo(caminho, nome="contact_research_sob_teste"):
    spec = importlib.util.spec_from_file_location(nome, str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def contato_valido(**campos):
    base = {"full_name": "Marina Alves", "email": EMAIL_A, "job_title": "Diretora de Operacoes",
            "decision_role": "Economic Buyer", "legal_basis": "LEGITIMATE_INTEREST"}
    base.update(campos)
    return base


def pedido(**campos):
    base = {"organizacao": {"cnpj": CNPJ_A}, "contato": contato_valido(),
            "fontes": [{"tipo": "LINKEDIN", "url": "https://www.linkedin.com/in/marina-alves",
                        "trecho": "Diretora de Operacoes na Vale Forte"}]}
    base.update(campos)
    return base


def linha_organizacao(org_id: str, **valores) -> str:
    registro = {"id": org_id, "status": valores.pop("status", "DISCOVERED"),
                "legal_name": valores.pop("legal_name", "Empresa Teste Ltda"),
                "trade_name": valores.pop("trade_name", "Empresa Teste")}
    return json.dumps(registro, ensure_ascii=False)


def linha_contato(contato_id: str = CONTATO_A, **valores) -> str:
    registro = {"id": contato_id, "organization_id": valores.pop("organization_id", ORG_A),
                "odoo_partner_id": valores.pop("odoo_partner_id", "")}
    for coluna in ("full_name", "first_name", "last_name", "job_title", "department", "seniority",
                   "decision_role", "linkedin_url", "email", "phone", "whatsapp",
                   "preferred_channel", "legal_basis", "source"):
        registro[coluna] = valores.pop(coluna, "")
    return json.dumps(registro, ensure_ascii=False)


def sql_de(porta) -> str:
    return "\n".join(porta.chamadas)


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def exigir(condicao, mensagem):
    if not condicao:
        raise AssertionError(mensagem)


# ---------------------------------------------------------------------------------------
# 1. Contrato e artefatos
# ---------------------------------------------------------------------------------------
@item("contrato-do-agente-e-json-valido-com-versao")
def _(ctx):
    contrato = ctx.contrato
    exigir(contrato["card"] == "TRE-W4-E05-T01", "card do contrato diferente do card do agente")
    exigir(contrato["agente"] == ctx.modulo.AGENTE, "nome do agente divergente do contrato")
    exigir(contrato["versao"] == ctx.modulo.VERSAO, "versao do contrato divergente do codigo")
    exigir(contrato["papel"] == ctx.modulo.PAPEL, "papel do contrato divergente do codigo")


@item("contrato-nao-divergiu-dos-fortes-do-data-contract")
def _(ctx):
    fortes = tuple(ctx.contrato_dados["dedup"]["strong"])
    exigir(tuple(ctx.contrato["identidade"]["fortes_por_prioridade"]) == fortes,
           "fortes do contrato do agente diferentes do Data Contract V1.0")
    exigir(tuple(ctx.agente.fortes) == fortes, "o codigo nao le' os fortes do Data Contract")


@item("contrato-mira-o-vocabulario-de-decision-role-do-contrato")
def _(ctx):
    do_contrato = list(ctx.contrato_dados["vocabularies"]["decision_role"])
    do_agente = list(ctx.contrato["vocabulario_decision_role"])
    exigir(do_agente == do_contrato,
           "vocabulario de decision_role do agente != Data Contract V1.0: %s x %s"
           % (do_agente, do_contrato))


@item("contrato-mira-o-evento-pg-para-odoo-do-contrato")
def _(ctx):
    eventos = list(ctx.contrato_dados["events"]["pg_to_odoo"])
    exigir(ctx.contrato["espelho"]["event_type"] in eventos,
           "evento do espelho nao esta' em events.pg_to_odoo do contrato")


@item("contrato-declara-que-nao-emite-evento-e-por-que")
def _(ctx):
    exigir(ctx.contrato["espelho"]["emite_evento"] is False, "a v1 nao pode declarar emissao")
    exigir(ctx.contrato["outbox"]["emite_evento"] is False, "outbox da v1 tem de ser false")
    exigir(len(ctx.contrato["espelho"]["motivo_de_nao_emitir"]) > 40,
           "motivo de nao emitir tem de ser declarado, nao vazio")
    contrato_consumidor = json.loads(CONTRATO_CONSUMIDOR.read_text(encoding="utf-8"))
    eventos_do_consumidor = {e["event_type"] for e in contrato_consumidor["eventos"]}
    exigir(ctx.contrato["espelho"]["event_type"] not in eventos_do_consumidor,
           "o consumidor versionado passou a cobrir o evento: a declaracao de lacuna ficou velha")


@item("artefatos-do-card-existentes-e-versionaveis")
def _(ctx):
    for caminho in (ACEITE, DOC_ARQUITETURA, RUNBOOK, EXEMPLO_FONTE):
        exigir(caminho.is_file(), "artefato do card ausente: %s" % caminho)
    exigir(os.access(ACEITE, os.X_OK), "o aceite E2E tem de ser executavel")
    texto = VERIFICADOR_ESTRUTURA.read_text(encoding="utf-8")
    for alvo in ("hermes/agents/contact_research/contact_research.py",
                 "scripts/agentes/teste_contact_research_aceite.sh",
                 "docs/runbooks/agente-contact-research.md"):
        exigir(alvo in texto, "portao de estrutura nao cobre %s" % alvo)


# ---------------------------------------------------------------------------------------
# 2. Identidade da empresa (importada) e do contato
# ---------------------------------------------------------------------------------------
@item("identidade-nao-tem-segunda-copia-no-agente")
def _(ctx):
    scout = carregar_modulo(MODULO_SCOUT, nome="scout_identidade_suite")
    for nome in ("cnpj_valido", "normalizar_cnpj", "domain_valido", "normalizar_domain",
                 "linkedin_valido", "normalizar_linkedin"):
        exigir(hasattr(ctx.modulo, nome) is False or
               getattr(ctx.modulo, nome, None).__code__.co_code ==
               getattr(scout, nome).__code__.co_code,
               "o agente reimplementou %s (a regra de identidade e' do Scout)" % nome)
        exigir(nome not in ctx.codigo.replace("identidade." + nome, ""),
               "o agente tem definicao propria de %s no proprio arquivo" % nome)


@item("cnpj-com-digito-verificador-errado-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(pedido(organizacao={"cnpj": "11.222.333/0001-00"}),
                                          ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    exigir("IDENTIFICADOR_FORTE_INVALIDO" in validacao["problemas"],
           "CNPJ invalido tinha de virar problema nomeado")
    exigir(validacao["validos"] == [], "CNPJ invalido nao pode virar identidade valida")


@item("sem-forte-declarado-e-recusado")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(pedido(organizacao={"city": "Santos"}),
                                          ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    exigir("SEM_IDENTIFICADOR_FORTE" in validacao["problemas"], "sem forte tinha de ser problema")


@item("email-e-normalizado-para-minusculo")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(
        pedido(contato=contato_valido(email="  Marina.Alves@ValeForte.Com.BR  ")),
        ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    exigir(validacao["email"] == EMAIL_A,
           "e-mail nao foi normalizado para minusculo/sem espaco: %r" % validacao["email"])
    exigir(validacao["contato"]["email"] == EMAIL_A,
           "a coluna email tem de receber a identidade NORMALIZADA")
    exigir(not validacao["problemas"], "e-mail valido com maiuscula nao pode virar problema")


@item("email-invalido-e-recusado")
def _(ctx):
    for bruto in ("marina.alves", "marina.alves@", "@valeforte.com.br", "marina alves@vf.com.br",
                  "marina@valeforte", ""):
        validacao = ctx.modulo.validar_pedido(pedido(contato=contato_valido(email=bruto)),
                                              ctx.agente.fortes, ctx.contrato,
                                              ctx.agente.identidade)
        exigir(validacao["problemas"], "e-mail invalido aceito: %r" % bruto)
        if bruto:
            exigir("IDENTIFICADOR_DE_CONTATO_INVALIDO" in validacao["problemas"]
                   or "SEM_IDENTIFICADOR_DE_CONTATO" in validacao["problemas"],
                   "e-mail invalido %r sem motivo nomeado: %s" % (bruto, validacao["problemas"]))


@item("email-acima-do-limite-e-recusado")
def _(ctx):
    email = "a" * 320 + "@valeforte.com.br"
    exigir(not ctx.modulo.email_valido(email), "e-mail acima de 320 nao pode ser valido")
    validacao = ctx.modulo.validar_pedido(pedido(contato=contato_valido(email=email)),
                                          ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    exigir("IDENTIFICADOR_DE_CONTATO_INVALIDO" in validacao["problemas"],
           "e-mail acima do limite tinha de ser recusado com motivo")


# ---------------------------------------------------------------------------------------
# 3. Validacao do contato (o que e' descartado com motivo)
# ---------------------------------------------------------------------------------------
@item("campo-nao-declarado-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(
        pedido(contato=contato_valido(odoo_partner_id=42, email_status="VALID",
                                      do_not_contact=False, influence_score=9.9,
                                      campo_inventado="x")),
        ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    descartados = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    for campo in ("odoo_partner_id", "email_status", "do_not_contact", "influence_score",
                  "campo_inventado"):
        exigir(descartados.get(campo) == "CAMPO_NAO_DECLARADO",
               "campo %s nao foi descartado com motivo: %s" % (campo, descartados))
    exigir(not validacao["problemas"], "descarte nao pode virar recusa do pedido")


@item("papel-fora-do-vocabulario-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(
        pedido(contato=contato_valido(decision_role="Chefe Geral")),
        ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    motivos = [d["motivo"] for d in validacao["descartados"]]
    exigir("PAPEL_FORA_DO_VOCABULARIO" in motivos, "papel fora do vocabulario tinha de ser descartado")
    exigir("decision_role" not in validacao["contato"], "papel invalido nao pode ser aceito")


@item("valor-vazio-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(
        pedido(contato=contato_valido(job_title="   ", department=None)),
        ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    descartados = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    exigir(descartados.get("job_title") == "VALOR_VAZIO", "job_title vazio tinha de ser descartado")
    exigir(descartados.get("department") == "VALOR_VAZIO", "department nulo tinha de ser descartado")


@item("valor-acima-do-limite-da-coluna-e-descartado")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(
        pedido(contato=contato_valido(job_title="D" * 300, seniority="S" * 60)),
        ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    descartados = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    exigir(descartados.get("job_title") == "EXCEDE_O_LIMITE_DA_COLUNA",
           "job_title acima de 255 tinha de ser descartado: %s" % descartados)
    exigir(descartados.get("seniority") == "EXCEDE_O_LIMITE_DA_COLUNA",
           "seniority acima de 50 tinha de ser descartado")


@item("url-de-linkedin-invalida-e-descartada")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(
        pedido(contato=contato_valido(linkedin_url="linkedin.com/in/marina")),
        ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    descartados = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    exigir(descartados.get("linkedin_url") == "URL_INVALIDA",
           "linkedin sem esquema tinha de ser descartado: %s" % descartados)


@item("sem-nome-e-recusado")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(pedido(contato=contato_valido(full_name="")),
                                          ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    exigir("SEM_NOME" in validacao["problemas"], "contato sem nome tinha de ser recusado")


@item("sem-base-legal-e-recusado")
def _(ctx):
    sem_base = contato_valido()
    sem_base.pop("legal_basis")
    validacao = ctx.modulo.validar_pedido(pedido(contato=sem_base),
                                          ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    exigir("SEM_BASE_LEGAL" in validacao["problemas"],
           "contato sem base legal tinha de ser recusado (compliance do contrato)")


@item("sem-fonte-e-fonte-fora-do-vocabulario-sao-recusadas")
def _(ctx):
    vazio = ctx.modulo.validar_pedido(pedido(fontes=[]), ctx.agente.fortes, ctx.contrato,
                                      ctx.agente.identidade)
    exigir("SEM_FONTE" in vazio["problemas"], "pedido sem fonte tinha de ser recusado")
    fora = ctx.modulo.validar_pedido(pedido(fontes=[{"tipo": "PANFLETO", "trecho": "x"}]),
                                     ctx.agente.fortes, ctx.contrato, ctx.agente.identidade)
    exigir("FONTE_DESCONHECIDA" in fora["problemas"], "fonte fora do vocabulario tinha de recusar")


@item("confianca-fora-da-faixa-e-descartada")
def _(ctx):
    validacao = ctx.modulo.validar_pedido(pedido(confianca=1.7), ctx.agente.fortes, ctx.contrato,
                                          ctx.agente.identidade)
    descartados = {d["campo"]: d["motivo"] for d in validacao["descartados"]}
    exigir(descartados.get("confianca") == "FORA_DA_FAIXA", "confianca 1.7 tinha de ser descartada")
    exigir(validacao["confianca"] is None, "confianca invalida nao pode virar numero")


# ---------------------------------------------------------------------------------------
# 4. Decisao (pura, sem banco)
# ---------------------------------------------------------------------------------------
@item("veredito-sem-organizacao-e-recusado")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [], [])
    exigir((veredito, motivo) == ("RECUSADA", "ORGANIZACAO_NAO_ENCONTRADA"),
           "empresa inexistente tinha de recusar: %s/%s" % (veredito, motivo))


@item("veredito-com-duas-organizacoes-e-revisao")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [{"id": ORG_A}, {"id": ORG_B}], [])
    exigir((veredito, motivo) == ("REVISAO_IDENTIDADE", "CONFLITO_DE_IDENTIDADE_FORTE"),
           "ambiguidade de empresa tinha de ir para a fila humana: %s/%s" % (veredito, motivo))


@item("veredito-com-dois-contatos-e-revisao")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [{"id": ORG_A}],
                                                   [{"id": CONTATO_A}, {"id": ORG_B}])
    exigir((veredito, motivo) == ("REVISAO_IDENTIDADE", "CONFLITO_DE_IDENTIDADE_DE_CONTATO"),
           "ambiguidade de contato tinha de ir para a fila humana: %s/%s" % (veredito, motivo))


@item("veredito-sem-contato-e-identificado")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito([], [{"id": ORG_A}], [])
    exigir((veredito, motivo) == ("IDENTIFICADO", None),
           "contato novo + empresa resolvida tinha de identificar: %s/%s" % (veredito, motivo))


@item("veredito-com-problema-manda-o-problema")
def _(ctx):
    veredito, motivo = ctx.modulo.decidir_veredito(["SEM_BASE_LEGAL"], [{"id": ORG_A}], [])
    exigir((veredito, motivo) == ("RECUSADA", "SEM_BASE_LEGAL"),
           "problema tem de vencer a decisao: %s/%s" % (veredito, motivo))


# ---------------------------------------------------------------------------------------
# 5. SQL e guarda de escrita
# ---------------------------------------------------------------------------------------
@item("sql-de-organizacao-usa-o-forte-normalizado")
def _(ctx):
    sql = ctx.modulo.sql_consultar_organizacao([("cnpj", "11222333000181")])
    exigir("cnpj = '11222333000181'" in sql, "consulta nao usa o forte normalizado: %s" % sql)
    exigir("deleted_at IS NULL" in sql, "consulta tem de ignorar organizacao apagada")


@item("sql-de-contato-normaliza-os-dois-lados")
def _(ctx):
    sql = ctx.modulo.sql_consultar_contatos(ORG_A, EMAIL_A)
    exigir("lower(email) = '%s'" % EMAIL_A in sql,
           "consulta de contato nao normaliza os DOIS lados: %s" % sql)
    exigir("organization_id = '%s'" % ORG_A in sql,
           "consulta de contato tem de ser escopada na empresa")
    exigir("odoo_partner_id" in sql, "a leitura da rodada precisa saber se o contato ja' foi espelhado")


@item("sql-de-criacao-ancora-toda-instrucao-no-sync-event-da-rodada")
def _(ctx):
    sql = ctx.sql_criacao()
    trechos = [linha for linha in sql.splitlines() if linha.strip()]
    ancoradas = [l for l in trechos if "id = '" in l and "status = 'PENDING'" in l]
    exigir(len(ancoradas) >= 2,
           "as instrucoes da rodada tem de se ancorar no sync_event DELA: %s" % sql)
    exigir("FROM sales_intelligence.sync_events WHERE id = '" in sql,
           "o INSERT do contato nao se ancora no sync_event da rodada")


@item("sql-de-criacao-tem-o-claim-idempotente")
def _(ctx):
    sql = ctx.sql_criacao()
    exigir("ON CONFLICT (idempotency_key) DO NOTHING" in sql,
           "o claim da chave nao esta' no SQL (idempotencia na prosa nao vale)")
    exigir("INSERT INTO sales_intelligence.contacts" in sql, "o SQL de criacao nao insere contato")
    exigir("'CONTACT_IDENTIFICADO'" in sql, "a marca da rodada nao esta' no fechamento")


@item("sql-de-enriquecimento-passa-na-propria-guarda")
def _(ctx):
    sql = ctx.sql_enriquecimento(colunas=("job_title",))
    ctx.modulo.validar_sql(sql, modo_contatos=ctx.modulo.MODO_ENRIQUECIMENTO)
    exigir("COALESCE(NULLIF(job_title, ''), 'Diretora de Operacoes')" in sql,
           "enriquecimento nao usa a forma que NAO sobrescreve: %s" % sql)


@item("sql-de-enriquecimento-so-toca-colunas-declaradas-e-nunca-identidade")
def _(ctx):
    sql = ctx.sql_enriquecimento(colunas=("job_title", "phone"),
                                 contato=contato_valido(phone="+55 11 98888-0001"))
    exigir("email" not in sql, "o enriquecimento nao pode escrever a IDENTIDADE (email)")
    for coluna in ("odoo_partner_id", "do_not_contact", "opt_out_email", "opt_out_whatsapp",
                   "influence_score", "email_status"):
        exigir(coluna not in sql, "o enriquecimento escreveu coluna proibida: %s" % coluna)
    for coluna in ctx.modulo.COLUNAS_ENRIQUECIMENTO:
        exigir(coluna not in COLUNAS_PROIBIDAS, "coluna proibida na lista de enriquecimento")
        exigir(coluna not in COLUNAS_DE_IDENTIDADE,
               "o enriquecimento nunca pode listar a IDENTIDADE: %s" % coluna)
    # Nem a pedido explicito: a guarda recusa enriquecer a coluna de identidade.
    try:
        ctx.modulo.conferir_enriquecimento("email = COALESCE(NULLIF(email, ''), 'x')")
    except ctx.modulo.GuardaDeEscritaViolada:
        pass
    else:
        raise AssertionError("a guarda aceitou enriquecer a identidade (email)")


@item("insert-de-contato-cobre-so-colunas-do-ddl-e-nenhuma-proibida")
def _(ctx):
    colunas_ddl = ctx.colunas("sales_intelligence.contacts")
    exigir(colunas_ddl, "DDL de contacts nao encontrado na migration congelada")
    colunas_insert, _ = ctx.modulo.montar_linha_contato(CONTATO_A, ORG_A,
                                                        {"contato": contato_valido()})
    fora = [c for c in colunas_insert if c not in colunas_ddl]
    exigir(not fora, "INSERT escreve coluna que o DDL nao tem: %s" % fora)
    proibidas = [c for c in colunas_insert if c in COLUNAS_PROIBIDAS
                 and c not in ("id", "organization_id", "created_at", "updated_at")]
    exigir(not proibidas, "INSERT escreve coluna proibida: %s" % proibidas)
    exigir("odoo_partner_id" not in colunas_insert, "INSERT nao pode escrever identidade externa")
    exigir(len(colunas_insert) == len(set(colunas_insert)), "INSERT com coluna repetida")


@item("guarda-recusa-ddl")
def _(ctx):
    for sql in ("ALTER TABLE sales_intelligence.contacts ADD COLUMN x int;",
                "DROP TABLE sales_intelligence.contacts;",
                "CREATE INDEX i ON sales_intelligence.contacts (email);"):
        try:
            ctx.modulo.validar_sql(sql)
        except ctx.modulo.GuardaDeEscritaViolada:
            continue
        raise AssertionError("a guarda aceitou DDL: %s" % sql)


@item("guarda-recusa-escrita-em-organizations")
def _(ctx):
    sql = ("UPDATE sales_intelligence.organizations SET city = COALESCE(NULLIF(city, ''), 'X') "
           "WHERE id = '%s';" % ORG_A)
    try:
        ctx.modulo.validar_sql(sql, modo_contatos=ctx.modulo.MODO_ENRIQUECIMENTO)
    except ctx.modulo.GuardaDeEscritaViolada:
        return
    raise AssertionError("a guarda aceitou escrita em organizations (empresa nao e' deste agente)")


@item("guarda-recusa-enriquecimento-sem-coalesce")
def _(ctx):
    sql = ("UPDATE sales_intelligence.contacts SET job_title = 'Diretora' WHERE id = '%s';"
           % CONTATO_A)
    try:
        ctx.modulo.validar_sql(sql, modo_contatos=ctx.modulo.MODO_ENRIQUECIMENTO)
    except ctx.modulo.GuardaDeEscritaViolada:
        return
    raise AssertionError("a guarda aceitou UPDATE por atribuicao direta (sobrescreveria o dado)")


@item("guarda-recusa-delete-de-contato-fora-do-desfazer")
def _(ctx):
    sql = "DELETE FROM sales_intelligence.contacts WHERE id = '%s';" % CONTATO_A
    try:
        ctx.modulo.validar_sql(sql)
    except ctx.modulo.GuardaDeEscritaViolada:
        pass
    else:
        raise AssertionError("a guarda aceitou DELETE de contato fora do desfazer")
    ctx.modulo.validar_sql(sql, permitir_remocao=True)   # o desfazer explicito e' o unico caminho


@item("guarda-recusa-coluna-nao-declarada-no-insert")
def _(ctx):
    sql = ("INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, "
           "odoo_partner_id, do_not_contact) VALUES ('a', 'b', 'c', 1, true);")
    try:
        ctx.modulo.validar_sql(sql)
    except ctx.modulo.GuardaDeEscritaViolada:
        return
    raise AssertionError("a guarda aceitou INSERT com coluna fora da declaracao")


@item("guarda-nao-confunde-nome-de-pessoa-com-ddl")
def _(ctx):
    sql = ctx.sql_criacao(contato=contato_valido(full_name="Drop Table Ltda Me",
                                                 job_title="Alter Data"))
    ctx.modulo.validar_sql(sql)     # nome de pessoa nao e' instrucao
    exigir("'Drop Table Ltda Me'" in sql, "o nome declarado tem de chegar ao SQL")


# ---------------------------------------------------------------------------------------
# 6. Fluxos (porta de roteiro)
# ---------------------------------------------------------------------------------------
def agente_com_rota(ctx, rota):
    return ctx.modulo.ContactResearch(porta=PortaRoteiro(rota=rota, modulo=ctx.modulo),
                                      raiz=str(ctx.raiz), ambiente="dev",
                                      correlation_id="dddddddd-0000-4000-8000-000000000001")


@item("fluxo-cria-contato-e-fecha-o-sync-event")
def _(ctx):
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A), "", "CONTACT_IDENTIFICADO", ""],
                         modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["veredito"] == "IDENTIFICADO", "veredito: %s" % resultado["veredito"])
    exigir(resultado["acao"] == "criado", "acao tinha de ser criado: %s" % resultado["acao"])
    exigir(resultado["organization_id"] == ORG_A, "empresa resolvida errada")
    exigir(resultado["contact_id"] and resultado["sync_event_id"],
           "contato/sync_event sem id depois da escrita")
    exigir(resultado["status_agent_runs"] == "COMPLETED", "status de auditoria errado")
    sql = porta.chamadas[2]
    exigir("UPDATE sales_intelligence.contacts" not in sql,
           "criar contato nao pode passar por UPDATE")
    exigir("email = " not in sql, "a identidade nao pode ser escrita por UPDATE")


@item("fluxo-cria-com-uuid-v4-e-organizacao-resolvida")
def _(ctx):
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A), "", "CONTACT_IDENTIFICADO", ""],
                         modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    agente.processar(pedido())
    sql = porta.chamadas[2]
    exigir(re.search(r"'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'", sql),
           "o id do contato tem de ser UUID v4 gerado no produtor")
    exigir("'%s'" % ORG_A in sql, "o organization_id do INSERT tem de vir da empresa resolvida")


@item("fluxo-replay-devolve-ja-identificado-sem-contato")
def _(ctx):
    # o claim nao devolve linha (a chave ja' existia): nada e' inserido, nada e' enriquecido
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A), "", "", ""], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["veredito"] == "JA_IDENTIFICADO",
           "replay tinha de devolver JA_IDENTIFICADO: %s" % resultado["veredito"])
    exigir("IDEMPOTENCIA_REPLAY" in resultado["motivos"], "replay sem motivo declarado")
    exigir(resultado["contact_id"] is None, "replay nao pode anunciar contato escrito")


@item("fluxo-enriquece-contato-existente-sem-sobrescrever")
def _(ctx):
    existente = linha_contato(full_name="Marina Alves", email=EMAIL_A,
                              job_title="Gerente de Operacoes", legal_basis="CONSENT")
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A), existente, "CONTACT_IDENTIFICADO", ""],
                         modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["veredito"] == "IDENTIFICADO", "veredito: %s" % resultado["veredito"])
    exigir(resultado["acao"] == "enriquecido", "acao tinha de ser enriquecido")
    exigir("job_title" in resultado["colunas_preservadas"],
           "job_title preenchido tinha de ser preservado: %s" % resultado["colunas_preservadas"])
    exigir("legal_basis" in resultado["colunas_preservadas"],
           "legal_basis preenchido tinha de ser preservado")
    sql = porta.chamadas[2]
    exigir("COALESCE(NULLIF(job_title, '')" not in sql, "coluna preservada foi escrita no SET")
    exigir("UPDATE sales_intelligence.contacts" in sql, "contato existente nao foi atualizado")
    exigir("INSERT INTO sales_intelligence.contacts" not in sql, "contato existente nao pode ser criado de novo")


@item("fluxo-contato-existente-sem-coluna-vazia-nao-emite-update")
def _(ctx):
    existente = linha_contato(**contato_valido())
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A), existente, "CONTACT_IDENTIFICADO", ""],
                         modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["acao"] == "enriquecido", "acao: %s" % resultado["acao"])
    exigir(resultado["colunas_enriquecidas"] == [], "nada havia a enriquecer")
    exigir(resultado["veredito"] == "IDENTIFICADO", "veredito: %s" % resultado["veredito"])
    sql = porta.chamadas[2]
    exigir("UPDATE sales_intelligence.contacts SET" not in sql,
           "sem coluna vazia nao pode haver UPDATE de enriquecimento")


@item("fluxo-identidade-ambigua-vai-para-a-fila-humana")
def _(ctx):
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B),
                               "", ""], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["veredito"] == "REVISAO_IDENTIDADE", "veredito: %s" % resultado["veredito"])
    sql = sql_de(porta)
    exigir("sales_intelligence.human_approvals" in sql, "ambiguidade nao foi para a fila humana")
    exigir("'CONTACT_IDENTITY_REVIEW'" in sql, "action_type errado na fila humana")
    exigir("INSERT INTO sales_intelligence.contacts" not in sql, "ambiguidade nao pode escrever contato")
    exigir(resultado["status_agent_runs"] == "REVIEW_REQUIRED", "status de auditoria errado")


@item("fluxo-fila-humana-que-nao-registra-vira-erro")
def _(ctx):
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A) + "\n" + linha_organizacao(ORG_B),
                               ("1", "", "fila humana fora do ar"), ""], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["veredito"] == "ERRO",
           "fila humana nao registrada tinha de virar ERRO: %s" % resultado["veredito"])
    exigir(resultado["status_agent_runs"] == "FAILED", "status de auditoria errado")


@item("fluxo-auditoria-que-nao-registra-vira-erro")
def _(ctx):
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A), "", "CONTACT_IDENTIFICADO",
                               ("1", "", "auditoria fora do ar")], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["veredito"] == "ERRO",
           "auditoria nao registrada tinha de virar ERRO: %s" % resultado["veredito"])
    exigir(resultado["auditoria_registrada"] is False, "auditoria_registrada devia ser False")
    exigir(any("AUDITORIA_NAO_REGISTRADA" in m for m in resultado["motivos"]),
           "motivo da auditoria nao declarado")


@item("fluxo-recusa-nao-escreve-contato")
def _(ctx):
    porta = PortaRoteiro(rota=["", ""], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    exigir(resultado["veredito"] == "RECUSADA", "veredito: %s" % resultado["veredito"])
    exigir("ORGANIZACAO_NAO_ENCONTRADA" in resultado["motivos"], "motivo da recusa errado")
    sql = sql_de(porta)
    exigir("INSERT INTO sales_intelligence.contacts" not in sql, "recusa nao pode escrever contato")
    exigir("UPDATE" not in sql, "recusa nao pode atualizar nada")
    exigir(resultado["status_agent_runs"] == "REJECTED", "status de auditoria errado")


@item("fluxo-opt-out-do-titular-nunca-e-escrito")
def _(ctx):
    sql = ctx.sql_criacao() + ctx.sql_enriquecimento(colunas=("job_title",))
    for coluna in ("do_not_contact", "opt_out_email", "opt_out_whatsapp"):
        exigir(coluna not in sql, "o agente escreveu o flag do titular: %s" % coluna)
        exigir(coluna not in ctx.modulo.COLUNAS_DO_CONTATO, "%s na lista de INSERT" % coluna)
        exigir(coluna not in ctx.modulo.COLUNAS_ENRIQUECIMENTO, "%s na lista de enriquecimento" % coluna)


@item("fluxo-evidencia-o-evento-elegivel-e-nao-emite")
def _(ctx):
    porta = PortaRoteiro(rota=[linha_organizacao(ORG_A), "", "CONTACT_IDENTIFICADO", ""],
                         modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resultado = agente.processar(pedido())
    espelho = resultado["evento_de_espelho"]
    exigir(espelho["event_type"] == "DECISION_MAKER_FOUND", "evento errado: %s" % espelho)
    exigir(espelho["elegivel"] is True, "Economic Buyer com nome e e-mail e' elegivel")
    exigir(espelho["emitido"] is False, "a v1 nao emite evento")
    exigir(sql_de(porta).count("outbox_events") == 0, "nenhum SQL pode tocar a fila de outbox")
    exigir("sales_intelligence.outbox_events" not in ctx.modulo.TABELAS_PERMITIDAS,
           "outbox_events nao pode estar nas tabelas permitidas")
    sem_papel = ctx.modulo.elegibilidade_do_evento(
        {"email": EMAIL_A, "full_name": "Marina", "decision_role": "Unknown"}, ctx.contrato)
    exigir(sem_papel["elegivel"] is False, "Unknown nao pode ser elegivel")
    exigir("papel_de_decisao" in sem_papel["o_que_faltaria"], "faltante nao declarado")


@item("fluxo-elegibilidade-vem-do-contrato-nao-de-literal")
def _(ctx):
    variante = copy.deepcopy(ctx.contrato)
    variante["papeis_de_decisao"] = []
    efetivo = {"email": EMAIL_A, "full_name": "Marina Alves", "decision_role": "Economic Buyer"}
    exigir(ctx.modulo.elegibilidade_do_evento(efetivo, ctx.contrato)["elegivel"] is True,
           "Economic Buyer tinha de ser elegivel com o contrato real")
    exigir(ctx.modulo.elegibilidade_do_evento(efetivo, variante)["elegivel"] is False,
           "a elegibilidade nao pode ter lista literal no codigo (o contrato e' a fonte)")


@item("fluxo-gate-do-jev-e-fail-closed")
def _(ctx):
    for recibo in (None, {}, {"outcome": "BLOCK", "decision_id": "d", "lane": "high"},
                   {"outcome": "ESCALATE", "decision_id": "d", "lane": "high"},
                   {"outcome": "PASS", "lane": "voando", "decision_id": "d"},
                   {"outcome": "PASS", "lane": "high"}):
        try:
            ctx.modulo.chamar_llm("prompt", recibo=recibo)
        except ctx.modulo.ReciboJEVInvalido:
            continue
        raise AssertionError("recibo invalido foi aceito: %r" % (recibo,))
    aprovado = ctx.modulo.chamar_llm("prompt", recibo={"outcome": "PASS", "lane": "high",
                                                       "decision_id": "dec-1"})
    exigir(aprovado["executado"] is False, "a v1 e' deterministica: nenhuma LLM executada")
    exigir(aprovado["decision_id"] == "dec-1", "decision_id nao foi propagado")


@item("fluxo-desfazer-dry-run-nao-apaga")
def _(ctx):
    rodada = json.dumps({"contact_id": CONTATO_A, "sync_event_id": "s1", "organization_id": ORG_A,
                         "acao": "criado", "antes": {}, "depois": {"full_name": "Marina"},
                         "espelhado": "", "existe": True})
    porta = PortaRoteiro(rota=[rodada], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resumo = agente.desfazer("dddddddd-0000-4000-8000-000000000009")
    exigir(resumo["dry_run"] is True, "sem --confirmo o desfazer e' dry-run")
    exigir(resumo["apagadas"] == 0, "dry-run nao apaga")
    exigir(len(porta.chamadas) == 1, "dry-run nao pode escrever SQL nenhum")


@item("fluxo-desfazer-restaura-e-apaga-o-que-a-rodada-criou")
def _(ctx):
    criado = json.dumps({"contact_id": CONTATO_A, "sync_event_id": "s1", "organization_id": ORG_A,
                         "acao": "criado", "antes": {}, "depois": {"full_name": "Marina Alves"},
                         "espelhado": "", "existe": True})
    enriquecido = json.dumps({"contact_id": ORG_B, "sync_event_id": "s2", "organization_id": ORG_A,
                              "acao": "enriquecido", "antes": {"job_title": None},
                              "depois": {"job_title": "Diretora"}, "espelhado": "",
                              "existe": True})
    porta = PortaRoteiro(rota=[criado + "\n" + enriquecido], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    resumo = agente.desfazer("dddddddd-0000-4000-8000-000000000009", confirmo=True)
    exigir(resumo["dry_run"] is False, "com --confirmo o desfazer aplica")
    exigir(resumo["apagadas"] == 1, "so' o contato CRIADO pela rodada pode ser apagado")
    sql = porta.chamadas[-1]
    exigir("DELETE FROM sales_intelligence.contacts WHERE id IN ('%s')" % CONTATO_A in sql,
           "o contato criado pela rodada nao foi apagado: %s" % sql)
    exigir("UPDATE sales_intelligence.contacts SET job_title = NULL" in sql,
           "a coluna enriquecida nao voltou ao valor anterior: %s" % sql)
    exigir("'ROLLBACK'" in sql, "a rodada de desfazer nao registra o ROLLBACK")
    exigir(porta.modos[-1] == ctx.modulo.MODO_RESTAURACAO, "modo de restauracao nao declarado")
    exigir(porta.remocoes[-1] is True, "o DELETE do desfazer tem de ser declarado")


@item("fluxo-desfazer-recusa-contato-ja-espelhado")
def _(ctx):
    espelhado = json.dumps({"contact_id": CONTATO_A, "sync_event_id": "s1",
                            "organization_id": ORG_A, "acao": "criado", "antes": {},
                            "depois": {"full_name": "Marina Alves"}, "espelhado": "4242",
                            "existe": True})
    porta = PortaRoteiro(rota=[espelhado], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="dev")
    seco = agente.desfazer("dddddddd-0000-4000-8000-000000000009")
    exigir(seco["recusaria"] is True, "o dry-run tinha de avisar que recusaria")
    porta2 = PortaRoteiro(rota=[espelhado], modulo=ctx.modulo)
    agente2 = ctx.modulo.ContactResearch(porta=porta2, raiz=str(ctx.raiz), ambiente="dev")
    try:
        agente2.desfazer("dddddddd-0000-4000-8000-000000000009", confirmo=True)
    except ctx.modulo.RollbackRecusado:
        return
    raise AssertionError("desfazer contato ja' espelhado tinha de ser recusado (fail-closed)")


# ---------------------------------------------------------------------------------------
# 7. Ambiente, importacao e raiz
# ---------------------------------------------------------------------------------------
@item("ambiente-prod-recusado-e-nenhum-escrito")
def _(ctx):
    porta = PortaRoteiro(rota=[], modulo=ctx.modulo)
    agente = ctx.modulo.ContactResearch(porta=porta, raiz=str(ctx.raiz), ambiente="prod")
    try:
        agente.conferir_ambiente()
    except ctx.modulo.RecusaDeAmbiente:
        pass
    else:
        raise AssertionError("prod tinha de ser recusado (ADR-005)")
    exigir(porta.chamadas == [], "ambiente recusado nao pode falar com o banco")


@item("ambiente-nao-declarado-e-recusado-homolog-e-permitido")
def _(ctx):
    try:
        ctx.modulo.ContactResearch(porta=PortaRoteiro(), raiz=str(ctx.raiz)).conferir_ambiente()
    except ctx.modulo.RecusaDeAmbiente:
        pass
    else:
        raise AssertionError("ambiente nao declarado tinha de recusar (fail-closed)")
    exigir(ctx.modulo.ContactResearch(porta=PortaRoteiro(), raiz=str(ctx.raiz),
                                      ambiente="homolog").conferir_ambiente() == "homolog",
           "homolog e' ambiente permitido")
    exigir("prod" not in ctx.modulo.AMBIENTES_PERMITIDOS, "prod nao pode estar em permitidos")


@item("raiz-vem-do-marcador-nao-da-profundidade")
def _(ctx):
    fundo = ctx.raiz / "d1/d2/d3/d4/d5/d6"
    fundo.mkdir(parents=True, exist_ok=True)
    copia = fundo / "contact_research_fundo.py"
    copia.write_text(Path(ctx.modulo.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    try:
        modulo = carregar_modulo(copia, nome="contact_research_fundo")
        exigir(modulo.RAIZ_PADRAO == ctx.raiz,
               "a copia funda nao achou a raiz pelo marcador: %s" % modulo.RAIZ_PADRAO)
        agente = modulo.ContactResearch(porta=PortaRoteiro(), raiz=None)
        exigir(agente.raiz == ctx.raiz, "a instancia nao herdou a raiz do marcador")
    finally:
        shutil.rmtree(ctx.raiz / "d1", ignore_errors=True)


@item("agente-recusa-quando-nao-acha-o-contrato")
def _(ctx):
    fora = Path(tempfile.mkdtemp(prefix="contact-research-fora-"))
    copia = fora / "contact_research_fora.py"
    copia.write_text(Path(ctx.modulo.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    cwd = os.getcwd()
    try:
        os.chdir(fora)
        modulo = carregar_modulo(copia, nome="contact_research_fora")
        for funcao in (lambda: modulo.ContactResearch(porta=PortaRoteiro()),
                       lambda: modulo.ContactResearch(porta=PortaRoteiro(), raiz=str(fora))):
            try:
                funcao()
            except (FileNotFoundError, ValueError):
                continue
            raise AssertionError("fora da arvore o agente tem de recusar (fail-closed)")
    finally:
        os.chdir(cwd)
        shutil.rmtree(fora, ignore_errors=True)


@item("autoteste-recusa-mutacao-sem-item-medido")
def _(ctx):
    exigir(ctx.modulo is not None, "contexto sem modulo")
    exigir(problemas_em_mutacoes([("m1", "a", "b", [])]),
           "mutacao sem item declarado tem de ser buraco de verificacao")
    exigir(problemas_em_mutacoes([("m2", "a", "b", ["item-que-nao-existe-na-suite"])]),
           "item esperado inexistente tem de ser buraco de verificacao")
    exigir(problemas_em_mutacoes() == [], "as mutacoes declaradas estao coerentes com a suite")


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
    finally:
        sys.stdout.close()
        sys.stdout = suprimido
    return resultados


MUTACOES = [
    ("sem-idempotencia",
     '        "ON CONFLICT (idempotency_key) DO NOTHING\\n"\n        "RETURNING id;".format(',
     '        "RETURNING id;".format(',
     ["sql-de-criacao-tem-o-claim-idempotente"]),
    ("escrita-sem-ancora-no-sync-event",
     '        "SELECT {vals} FROM {sync} WHERE id = {sevid} AND status = \'PENDING\'\\n"',
     '        "SELECT {vals} FROM {sync} WHERE id = {sevid}\\n"',
     ["sql-de-criacao-ancora-toda-instrucao-no-sync-event-da-rodada"]),
    ("enriquecimento-sem-coalesce",
     '    return "%s = COALESCE(NULLIF(%s, \'\'), %s)" % (coluna, coluna, lit(valor))',
     '    return "%s = %s" % (coluna, lit(valor))',
     ["sql-de-enriquecimento-passa-na-propria-guarda"]),
    ("email-como-coluna-de-enriquecimento",
     'COLUNAS_ENRIQUECIMENTO = (\n    "full_name",',
     'COLUNAS_ENRIQUECIMENTO = (\n    "email",\n    "full_name",',
     ["sql-de-enriquecimento-so-toca-colunas-declaradas-e-nunca-identidade"]),
    ("cnpj-passa-a-ser-coluna-de-contato",
     'COLUNAS_DO_CONTATO = (\n    "full_name",',
     'COLUNAS_DO_CONTATO = (\n    "odoo_partner_id",\n    "full_name",',
     ["insert-de-contato-cobre-so-colunas-do-ddl-e-nenhuma-proibida"]),
    ("guarda-deixa-passar-ddl",
     "    if _DDL.search(codigo):",
     "    if False and _DDL.search(codigo):",
     ["guarda-recusa-ddl"]),
    ("guarda-le-o-literal-como-codigo",
     '    return _LITERAL.sub("\'\'", sql)',
     "    return sql",
     ["guarda-nao-confunde-nome-de-pessoa-com-ddl"]),
    ("guarda-aceita-escrita-em-organizations",
     "            if tabela == TABELA_ORGANIZACOES:\n                raise GuardaDeEscritaViolada(",
     "            if False:\n                raise GuardaDeEscritaViolada(",
     ["guarda-recusa-escrita-em-organizations"]),
    ("guarda-aceita-enriquecimento-sem-coalesce",
     "    padrao = re.compile(_EXPR_TEXTO.format(c=re.escape(coluna)))\n    if not padrao.match(expressao):",
     "    padrao = re.compile(_EXPR_TEXTO.format(c=re.escape(coluna)))\n    if False:",
     ["guarda-recusa-enriquecimento-sem-coalesce"]),
    ("guarda-aceita-delete-de-contato",
     '            if operacao == "delete" and not permitir_remocao:',
     "            if False:",
     ["guarda-recusa-delete-de-contato-fora-do-desfazer"]),
    ("insert-sem-guarda-de-coluna",
     "        if fora:",
     "        fora = [c for c in colunas if c not in COLUNAS_DO_CONTATO]\n        if False:",
     ["guarda-recusa-coluna-nao-declarada-no-insert"]),
    ("papel-sem-validacao-de-vocabulario",
     '        if campo == "decision_role" and texto not in vocabulario_papel:',
     "        if False:",
     ["papel-fora-do-vocabulario-e-descartado"]),
    ("email-sem-validacao-de-formato",
     "    return bool(REGEX_EMAIL.match(email))",
     "    return bool(email)",
     ["email-invalido-e-recusado"]),
    ("identidade-sem-normalizacao",
     '    return _texto(valor).lower()',
     "    return _texto(valor)",
     ["email-e-normalizado-para-minusculo"]),
    ("comparacao-de-contato-caso-sensivel",
     '        "WHERE organization_id = %s AND lower(email) = %s ORDER BY id;" % (',
     '        "WHERE organization_id = %s AND email = %s ORDER BY id;" % (',
     ["sql-de-contato-normaliza-os-dois-lados"]),
    ("sem-guarda-de-base-legal",
     '        if not _texto(aceitos.get("legal_basis")):\n            problemas_do_contato.append("SEM_BASE_LEGAL")',
     '        if not _texto(aceitos.get("legal_basis")):\n            pass',
     ["sem-base-legal-e-recusado"]),
    ("fila-humana-sem-conferir-o-rc",
     "                if rc_rev != 0:",
     "                if False:",
     ["fluxo-fila-humana-que-nao-registra-vira-erro"]),
    ("auditoria-sem-conferir-o-rc",
     "        if rc_run != 0:",
     "        if False:",
     ["fluxo-auditoria-que-nao-registra-vira-erro"]),
    ("replay-anuncia-contato-escrito",
     '        if MARCA_IDENTIFICADO in marcas:\n            resultado["contact_id"] = contato_id',
     '        if MARCA_IDENTIFICADO in marcas or True:\n            resultado["contact_id"] = contato_id',
     ["fluxo-replay-devolve-ja-identificado-sem-contato"]),
    ("espelho-sempre-elegivel",
     "    elegivel = papel in papeis and not faltando",
     "    elegivel = True",
     ["fluxo-evidencia-o-evento-elegivel-e-nao-emite"]),
    ("elegibilidade-com-lista-literal",
     '    papeis = tuple(contrato["papeis_de_decisao"])',
     '    papeis = ("Economic Buyer", "Decision Maker", "Champion")',
     ["fluxo-elegibilidade-vem-do-contrato-nao-de-literal"]),
    ("outbox-nas-tabelas-permitidas",
     'TABELAS_PERMITIDAS = (TABELA_CONTATOS, TABELA_ORGANIZACOES,',
     'TABELAS_PERMITIDAS = ("sales_intelligence.outbox_events", TABELA_CONTATOS, TABELA_ORGANIZACOES,',
     ["fluxo-evidencia-o-evento-elegivel-e-nao-emite"]),
    ("rollback-sem-guarda-de-espelho",
     "        if espelhados:\n            # Fail-closed: apagar no PostgreSQL um contato que ja existe no CRM desfaz a rodada",
     "        if False:\n            # Fail-closed: apagar no PostgreSQL um contato que ja existe no CRM desfaz a rodada",
     ["fluxo-desfazer-recusa-contato-ja-espelhado"]),
    ("desfazer-apaga-contato-nao-criado",
     '    criados = [i["contact_id"] for i in itens if i.get("acao") == "criado" and i.get("existe")]',
     '    criados = [i["contact_id"] for i in itens if i.get("existe")]',
     ["fluxo-desfazer-restaura-e-apaga-o-que-a-rodada-criou"]),
    ("raiz-por-profundidade-do-arquivo",
     "    for base in (Path(__file__).resolve().parent, Path.cwd()):\n"
     "        for pasta in (base,) + tuple(base.parents):\n"
     "            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():\n"
     "                return pasta\n"
     "    return Path.cwd()",
     "    return Path(__file__).resolve().parents[3]",
     ["raiz-vem-do-marcador-nao-da-profundidade"]),
]


def problemas_em_mutacoes(mutacoes=None):
    """Guarda da PROPRIA prova: cada mutacao declara >=1 item e todo item declarado existe.

    Item esperado que nao existe no resultado da suite faz `resultados.get(nome)` devolver None,
    entao ele nunca entra em `nao_reprovados` e a mutacao passa sem medicao alguma — dente mudo.
    Nome inexistente e lista vazia sao BURACO DE VERIFICACAO, nunca alivio.
    """
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
    temporario = Path(tempfile.mkdtemp(prefix="contact-research-autoteste-"))
    try:
        for mutacao in MUTACOES:
            nome = mutacao[0]
            # A copia vive um nivel ABAIXO do tempdir: assim ela importa de qualquer TMPDIR
            # (a prova de que o agente importa de diretorio raso e' item proprio da suite).
            copia = temporario / "mut" / ("contact-research-%s.py" % nome)
            copia.parent.mkdir(parents=True, exist_ok=True)
            esperados, detalhe = aplicar_mutacao(mutacao, copia, codigo)
            if esperados is None:
                falhas_total += 1
                print("FALHOU mutacao %s -> %s (mutacao nao aplicada e' buraco de verificacao)"
                      % (nome, detalhe))
                continue
            try:
                modulo = carregar_modulo(copia, nome="contact_research_mut_%s" % nome.replace("-", "_"))
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
    p = argparse.ArgumentParser(description="Suite do agente Contact Research v1 (TRE-W4-E05-T01)")
    p.add_argument("--codigo", default=str(CODIGO_PADRAO), help="caminho de outro .py do agente")
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not ITENS:
        print("FALHOU a suite nao executou nenhum item")
        return 1
    modulo = carregar_modulo(Path(args.codigo))
    print("=== SUITE DO AGENTE CONTACT RESEARCH v1 (%s) ===" % args.codigo)
    ok, falhas, _ = executar_suite(modulo)
    print("\nRESULTADO: CONTACT_RESEARCH_SUITE_%s (%d itens, %d falhas)"
          % ("OK" if falhas == 0 else "FALHOU", ok + falhas, falhas))
    sucesso = falhas == 0
    if args.autoteste:
        sucesso = autoteste(Path(args.codigo)) and sucesso
        print("RESULTADO FINAL: CONTACT_RESEARCH_%s" % ("OK" if sucesso else "FALHOU"))
    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
