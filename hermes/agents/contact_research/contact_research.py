#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agente Contact Research v1 — contato comercial da empresa ja pesquisada (card TRE-W4-E05-T01).

O que este agente FAZ (e so isto): recebe pedidos de CONTATO sobre empresas que JA existem no
PostgreSQL (o produtor da empresa e o Scout, TRE-W4-E01-T01; quem a pesquisa e o Research,
TRE-W4-E02-T01), resolve a EMPRESA pelos identificadores fortes do Data Contract V1.0
(cnpj -> domain -> linkedin_url), resolve o CONTATO dentro dela pelo identificador natural do
contato (`email` normalizado, a mesma identidade que a operacao `contato_upsert` da API
controlada do Odoo usa — TRE-W3-E01-T03) e registra/atualiza a linha em
`sales_intelligence.contacts`: o terceiro estado do funil, "Contato identificado"
(doc 03 §2, doc 07 §7 — gate da W4: empresa -> research/signals/hypothesis/contacts).

O que ele NAO faz, por desenho (declarado em `hermes/agents/contact_research/agente-contact-research-v1.json`):
  - nao CRIA empresa (identidade de empresa que nao casa e RECUSADA com motivo);
  - nao escreve em `organizations` (nenhuma coluna, nem para enriquecer): empresa e do Research;
  - nao escreve `odoo_partner_id` (identidade externa e do espelho/consumidor), nem `id`,
    nem `organization_id` por UPDATE, nem `email` por UPDATE (identidade nao se reescreve);
  - nao escreve score nenhum (`influence_score`, `contactability_score`, `relationship_score` —
    score e a W5) nem `email_status` (verificacao de entregabilidade que a v1 nao faz);
  - nao escreve, e nunca SOBRESCREVE, os campos sensiveis do titular (`do_not_contact`,
    `opt_out_email`, `opt_out_whatsapp`): opt-out e fato do titular/operador, e limpar esse
    campo seria o oposto de "nao contatar opt-out" (doc 12 §8);
  - nao emite evento de outbox: o evento `DECISION_MAKER_FOUND` e do contrato (doc 06 §5), mas
    o consumidor de outbox versionado NAO o declara hoje — emitir criaria dead-letter nomeada.
    A lacuna e declarada e a elegibilidade do evento e medida e registrada (compliance: nao
    esconder o que nao se fez);
  - nao faz requisicao de rede nenhuma (sem crawler, sem LLM na v1) e nao toca Odoo/Titan/n8n;
  - nao escreve em producao (ADR-005).

Regras que sustentam o registro do contato:
  - **a identidade do contato e o e-mail NORMALIZADO** (minusculo, sem espaco nas pontas). A
    comparacao no SQL normaliza os DOIS lados (`lower(email) = <normalizado>`), entao linha
    antiga gravada com maiuscula nao vira contato duplicado;
  - **a coluna JA preenchida nunca e sobrescrita** — e o SQL que garante, nao a prosa:
    `COALESCE(NULLIF(col, ''), valor)` para as colunas de texto. A guarda de escrita exige
    exatamente esse formato (medido por mutacao no autoteste);
  - dado invalido ou fora da declaracao e DESCARTADO com motivo (`agent_runs.output.descartados`),
    nunca escrito: campo fora da declaracao, `decision_role` fora do vocabulario do contrato,
    valor vazio ou acima do limite da coluna;
  - `legal_basis` e `full_name` sao exigidos pela declaracao (o contrato §compliance lista
    `legal_basis` entre os campos minimos; a operacao de espelho exige `name`): pedido sem eles
    e RECUSADA, nao gravado com buraco;
  - `source` e DERIVADO da primeira fonte declarada do pedido (a lista completa de fontes fica
    na evidencia): a fonte nao precisa se repetir no campo.

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"):
  - a chave e a ENTRADA, nao a rodada: `contact:org:<uuid-da-empresa>:<input_hash>`, gravada em
    `sync_events.idempotency_key` (UNIQUE). A rodada e UMA transacao com instrucoes proprias:
    claim da chave -> escrita (INSERT do contato novo OU UPDATE de enriquecimento do contato que
    ja existe) -> fechamento do sync_event com a marca `CONTACT_IDENTIFICADO`. Cada instrucao se
    ancora no sync_event DESTA rodada (`id = <sevid> AND status = 'PENDING'`), nunca na chave:
    ancorar na chave faria o replay do pedido devolver "identificado" sem nada ter sido escrito
    (o defeito que o fechamento por `research_run` evitava no Research). Replay: nenhuma
    instrucao casa, nada e inserido nem enriquecido e o veredito vira JA_IDENTIFICADO.
  - a historia de cada pedido fica em `agent_runs` (uma linha por pedido, com o `correlation_id`
    do lote) — auditoria nao depende da narrativa de quem rodou.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/agents/contact_research/contact_research.py --planejar --fonte contatos.jsonl
  python3 hermes/agents/contact_research/contact_research.py --ambiente dev --fonte contatos.jsonl \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \\
      --relatorio /tmp/contact-research-rodada.json
  python3 hermes/agents/contact_research/contact_research.py --desfazer <correlation_id>
  python3 hermes/agents/contact_research/contact_research.py --desfazer <correlation_id> --confirmo
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
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do agente (espelha `hermes/agents/contact_research/agente-contact-research-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "contact_research"
PAPEL = "contact_research"
VERSAO = "1.0.0"
WORKFLOW = "pesquisa-contato"
WORKFLOW_VERSAO = "v1"

TABELA_CONTATOS = "sales_intelligence.contacts"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"
TABELA_APPROVALS = "sales_intelligence.human_approvals"
TABELAS_PERMITIDAS = (TABELA_CONTATOS, TABELA_ORGANIZACOES, TABELA_AGENT_RUNS,
                      TABELA_SYNC_EVENTS, TABELA_APPROVALS)

ACTION_TYPE_REVISAO = "CONTACT_IDENTITY_REVIEW"
OPERACAO_SYNC = "CONTACT_RESEARCH"
MARCA_IDENTIFICADO = "CONTACT_IDENTIFICADO"

# Colunas de `contacts` que o contato PODE receber no INSERT (uniao declarada; ordem fixa).
# Fora desta lista a guarda de escrita recusa — inclusive `odoo_partner_id`, os scores, os
# flags de opt-out e `email_status`.
COLUNAS_DO_CONTATO = (
    "full_name",
    "first_name",
    "last_name",
    "job_title",
    "department",
    "seniority",
    "decision_role",
    "linkedin_url",
    "email",
    "phone",
    "whatsapp",
    "preferred_channel",
    "legal_basis",
    "source",
)

# Colunas que o INSERT de `contacts` pode listar: as declaradas MAIS as administrativas do
# contrato (chave e carimbos de tempo) e `organization_id`, que vem da EMPRESA resolvida.
COLUNAS_DE_INSERT = ("id", "organization_id") + COLUNAS_DO_CONTATO + ("created_at", "updated_at")

# Colunas de `contacts` que o ENRIQUECIMENTO (UPDATE) pode preencher quando estao vazias. `email` NAO entra:
# identidade nao se reescreve. `organization_id`/`odoo_partner_id`/scores/opt-out tambem nao.
COLUNAS_ENRIQUECIMENTO = (
    "full_name",
    "first_name",
    "last_name",
    "job_title",
    "department",
    "seniority",
    "decision_role",
    "linkedin_url",
    "phone",
    "whatsapp",
    "preferred_channel",
    "legal_basis",
    "source",
)

# Tipo de cada coluna decide o formato do COALESCE da guarda. Todas as colunas de
# enriquecimento de contato sao de TEXTO (o contrato V1 nao define coluna numerica de contato
# que este agente escreva); a tabela existe para o formato ser conferido por nome.
TIPO_POR_COLUNA = {coluna: "texto" for coluna in COLUNAS_DO_CONTATO}

LIMITE_TEXTO = {
    "first_name": 120,
    "last_name": 120,
    "full_name": 255,
    "job_title": 255,
    "department": 100,
    "seniority": 50,
    "decision_role": 50,
    "email": 320,
    "phone": 50,
    "whatsapp": 50,
    "preferred_channel": 30,
    "legal_basis": 50,
    "source": 100,
}

FONTES = (
    "LINKEDIN",
    "WEB",
    "GOOGLE",
    "META",
    "WHATSAPP",
    "TITAN",
    "EVENTOS",
    "DADOS_PUBLICOS",
)

# Identidade do contato = e-mail. Palavra do contrato: `contacts(email)` e o indice do
# identificador, e a operacao `contato_upsert` identifica por `email` (W3-E01-T03 §1).
REGEX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
LIMITE_EMAIL = 320

IDENTIFICADO = "IDENTIFICADO"
JA_IDENTIFICADO = "JA_IDENTIFICADO"
REVISAO = "REVISAO_IDENTIDADE"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (IDENTIFICADO, JA_IDENTIFICADO, REVISAO, RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    IDENTIFICADO: "COMPLETED",
    JA_IDENTIFICADO: "COMPLETED",
    REVISAO: "REVIEW_REQUIRED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

# Vereditos do modo `--planejar` (nenhuma conexao de banco e feita nele)
PLANEJADO_IDENTIFICAR = "PLANEJADO_IDENTIFICAR"
PLANEJADO_REVISAR = "PLANEJADO_REVISAR"
PLANEJADO_RECUSAR = "PLANEJADO_RECUSAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

# Vocabulario do recibo do JEV (hermes/jev/routing/router.py: OUTCOME_*)
JEV_EXECUTAR = "PASS"
LANES = ("small", "medium", "high", "critical")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4
EXIT_FONTE = 5

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_AGENTE_PADRAO = "hermes/agents/contact_research/agente-contact-research-v1.json"
# A identidade da EMPRESA e UMA regra so neste projeto (normalizacao e digito verificador do
# CNPJ, dominio canonico, LinkedIn canonico). O contato IMPORTA a regra do produtor da empresa
# (Scout) em vez de manter uma segunda copia dela — copia de regra de identidade e' divergencia
# esperando acontecer, e a suite reprova se aparecer uma segunda implementacao aqui. A regra de
# identidade do CONTATO (e-mail) e' deste agente, porque este agente e' o produtor do contato.
MODULO_IDENTIDADE = "hermes/agents/scout/scout.py"

MODO_ENRIQUECIMENTO = "enriquecimento"
MODO_RESTAURACAO = "restauracao"
MODOS_CONTATOS = (MODO_ENRIQUECIMENTO, MODO_RESTAURACAO)


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR — nunca pela profundidade do arquivo.

    A copia mutada (autoteste) e o teste de importacao vivem em diretorio raso (as vezes `/tmp`),
    onde `Path(__file__).resolve().parents[3]` estoura `IndexError` ja na importacao. A raiz e o
    primeiro ancestral que contem o contrato DESTE agente; o diretorio de trabalho entra como
    segunda tentativa, porque a copia sob teste nao esta na arvore do repo.
    """
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():
                return pasta
    return Path.cwd()


RAIZ_PADRAO = descobrir_raiz_padrao()


class RecusaDeAmbiente(Exception):
    """Ambiente nao permitido (ADR-005): nada e escrito."""


class ReciboJEVInvalido(Exception):
    """Chamada de LLM sem recibo valido do JEV: fail-closed (doc 07 §7, W4)."""


class GuardaDeEscritaViolada(Exception):
    """SQL tentando escrever fora das tabelas/colunas/operacoes declaradas."""


class PortaIndisponivel(Exception):
    """A porta de banco falhou."""


class RollbackRecusado(Exception):
    """O desfazer foi recusado porque desfaria um fato que ja saiu daqui."""


# ---------------------------------------------------------------------------------------
# Contratos e regra de identidade importada do produtor da empresa
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

    Import tardio de proposito: o modulo e localizado pela raiz do repo, entao a copia do agente
    sob teste em diretorio raso continua importavel.
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
# Normalizacao e validacao
# ---------------------------------------------------------------------------------------
def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def normalizar_email(valor) -> str:
    """Identidade do contato: minusculo e sem espaco nas pontas (idempotente por construcao)."""
    return _texto(valor).lower()


def email_valido(valor) -> bool:
    email = normalizar_email(valor)
    if not email or len(email) > LIMITE_EMAIL:
        return False
    return bool(REGEX_EMAIL.match(email))


def identificadores_validos(pedido: dict, fortes: tuple, identidade) -> list:
    """[(tipo, valor_normalizado)] da EMPRESA, na ordem do contrato; invalido e descartado."""
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


def validar_fontes(pedido: dict) -> tuple:
    """>=1 fonte do vocabulario, com evidencia. Devolve (fontes_normalizadas, problemas)."""
    fontes = pedido.get("fontes")
    if not isinstance(fontes, list) or not fontes:
        return [], ["SEM_FONTE"]
    normalizadas = []
    problemas = []
    for fonte in fontes:
        if not isinstance(fonte, dict):
            problemas.append("FONTE_ILEGIVEL")
            continue
        tipo = _texto(fonte.get("tipo")).upper()
        if tipo not in FONTES:
            problemas.append("FONTE_DESCONHECIDA")
            continue
        normalizadas.append({"tipo": tipo, "url": _texto(fonte.get("url")) or None,
                             "trecho": _texto(fonte.get("trecho")) or None})
    if not normalizadas:
        return [], problemas or ["SEM_FONTE"]
    return normalizadas, problemas


def validar_contato(contato, contrato: dict) -> tuple:
    """(aceitos, descartados) do objeto `contato` do pedido.

    Campo fora da declaracao, valor vazio, valor acima do limite da coluna e `decision_role`
    fora do vocabulario fechado sao DESCARTADOS com motivo — nunca escritos. `email` e a
    identidade e tem tratamento proprio (recusa, nao descarte): ver `validar_pedido`.
    """
    if not isinstance(contato, dict):
        return {}, [{"campo": "*", "motivo": "CONTATO_ILEGIVEL"}]
    vocabulario_papel = contrato["vocabulario_decision_role"]
    aceitos, descartados = {}, []
    for campo, valor in contato.items():
        if campo == "email":
            continue                      # identidade: validada em `validar_pedido`
        if campo not in COLUNAS_DO_CONTATO:
            descartados.append({"campo": campo, "motivo": "CAMPO_NAO_DECLARADO", "valor": valor})
            continue
        texto = _texto(valor)
        if not texto:
            descartados.append({"campo": campo, "motivo": "VALOR_VAZIO", "valor": valor})
            continue
        if campo == "decision_role" and texto not in vocabulario_papel:
            descartados.append({"campo": campo, "motivo": "PAPEL_FORA_DO_VOCABULARIO",
                                "valor": valor})
            continue
        if campo == "linkedin_url" and not re.match(r"^https?://[^\s]+$", texto):
            descartados.append({"campo": campo, "motivo": "URL_INVALIDA", "valor": valor})
            continue
        if len(texto) > LIMITE_TEXTO.get(campo, 255):
            descartados.append({"campo": campo, "motivo": "EXCEDE_O_LIMITE_DA_COLUNA",
                                "valor": valor})
            continue
        aceitos[campo] = texto
    return aceitos, descartados


def validar_pedido(pedido: dict, fortes: tuple, contrato: dict, identidade) -> dict:
    """Validacao fora do banco: identidade da empresa, contato, fontes e confianca."""
    problemas = []
    vazio = {"problemas": ["PEDIDO_ILEGIVEL"], "validos": [], "declarados": [], "fontes": [],
             "contato": {}, "descartados": [], "email": None, "confianca": None,
             "problemas_do_contato": ["PEDIDO_ILEGIVEL"]}
    if not isinstance(pedido, dict):
        return vazio
    fontes, problemas_fontes = validar_fontes(pedido)
    problemas.extend(problemas_fontes)
    declarados = identificadores_declarados(pedido, fortes)
    validos = identificadores_validos(pedido, fortes, identidade)
    if declarados and not validos:
        problemas.append("IDENTIFICADOR_FORTE_INVALIDO")
    elif not validos:
        problemas.append("SEM_IDENTIFICADOR_FORTE")
    contato = pedido.get("contato")
    aceitos, descartados = validar_contato(contato, contrato)
    problemas_do_contato = []
    email = None
    if not isinstance(contato, dict):
        problemas_do_contato.append("SEM_CONTATO")
    else:
        if not _texto(contato.get("email")):
            problemas_do_contato.append("SEM_IDENTIFICADOR_DE_CONTATO")
        elif not email_valido(contato.get("email")):
            problemas_do_contato.append("IDENTIFICADOR_DE_CONTATO_INVALIDO")
        else:
            email = normalizar_email(contato.get("email"))
        if not _texto(aceitos.get("full_name")):
            problemas_do_contato.append("SEM_NOME")
        if not _texto(aceitos.get("legal_basis")):
            problemas_do_contato.append("SEM_BASE_LEGAL")
    problemas.extend(problemas_do_contato)
    confianca = pedido.get("confianca")
    if confianca not in (None, ""):
        try:
            confianca = float(confianca)
        except (TypeError, ValueError):
            confianca = None
            descartados.append({"campo": "confianca", "motivo": "NAO_E_NUMERO",
                                "valor": pedido.get("confianca")})
        else:
            if not 0 <= confianca <= 1:
                descartados.append({"campo": "confianca", "motivo": "FORA_DA_FAIXA",
                                    "valor": pedido.get("confianca")})
                confianca = None
    else:
        confianca = None
    # A coluna `email` e a identidade normalizada — nao a forma como veio na fonte.
    if email:
        aceitos["email"] = email
    return {"problemas": problemas, "validos": validos, "declarados": declarados, "fontes": fontes,
            "contato": aceitos, "descartados": descartados, "email": email,
            "confianca": confianca, "problemas_do_contato": problemas_do_contato}


def decidir_veredito(problemas: list, organizacoes_casadas: list, contatos_casados: list) -> tuple:
    """Decisao pura (testavel sem banco) — a regra vive aqui e so aqui.

    A empresa tem de EXISTIR (o contato nao descobre empresa) e o contato nao pode ser ambiguo:
    dois contatos distintos com a mesma identidade na MESMA empresa vao para a fila humana,
    nunca sao resolvidos por heuristica (`dedup.rule` do contrato). Contato que nao existe ainda
    e' criado — este agente e' o produtor do contato comercial na inteligencia.
    """
    if problemas:
        return RECUSADA, problemas[0]
    if len(organizacoes_casadas) == 0:
        return RECUSADA, "ORGANIZACAO_NAO_ENCONTRADA"
    if len(organizacoes_casadas) > 1:
        return REVISAO, "CONFLITO_DE_IDENTIDADE_FORTE"
    if len(contatos_casados) > 1:
        return REVISAO, "CONFLITO_DE_IDENTIDADE_DE_CONTATO"
    return IDENTIFICADO, None


def hash_da_entrada(identidades: list, email: str, contato: dict, fontes: list) -> str:
    """Chave da idempotencia: a ENTRADA canonica (nao a rodada).

    Ordem de chaves fixa e JSON sem espacos: a mesma entrada apresentada de novo tem de produzir
    o MESMO hash — e' isso que faz o retry nao criar duplicata.
    """
    canonico = {
        "identidades": [[t, v] for t, v in identidades],
        "email": email,
        "contato": {k: contato[k] for k in sorted(contato)},
        "fontes": [{"tipo": f["tipo"], "url": f["url"], "trecho": f["trecho"]} for f in fontes],
    }
    return _sha256(json.dumps(canonico, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")))


def chave_idempotencia(organizacao_id: str, entrada_hash: str) -> str:
    return "contact:org:%s:%s" % (organizacao_id, entrada_hash)


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o agente pode e nao pode escrever)
# ---------------------------------------------------------------------------------------
_LITERAIS = None  # preenchido por `preparar_literais` (lit/lit_json vem do modulo de identidade)


def preparar_literais(identidade) -> None:
    """`lit`/`lit_json` sao importados do produtor: uma unica implementacao de escape de literal."""
    global _LITERAIS
    _LITERAIS = (identidade.lit, identidade.lit_json)


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
_SET = re.compile(r"\bUPDATE\s+([A-Za-z_][\w\.]*)\s+SET\s+(.*?)\s+WHERE\b", re.I | re.S)
_INSERT_COLUNAS = re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][\w\.]*)\s*\(([^)]*)\)", re.I | re.S)

_EXPR_TEXTO = r"^{c}\s*=\s*COALESCE\s*\(\s*NULLIF\s*\(\s*{c}\s*,\s*''\s*\)\s*,"


def _sem_literais(sql: str) -> str:
    """A instrucao SEM o conteudo dos literais — so o codigo SQL.

    Nome de pessoa nao e instrucao: um literal ('Drop Silva') faz a guarda recusar contato
    legitimo como se fosse DDL. Quem escreve dado e o `lit()`, que escapa apostrofo dobrando —
    entao a varredura olha o codigo, nao a prosa.
    """
    return _LITERAL.sub("''", sql)


def _itens_do_set(texto: str) -> list:
    """Separa os itens do SET por virgula de TOPO (virgula dentro de parenteses nao separa)."""
    itens, atual, dentro, profundidade, i = [], [], False, 0, 0
    while i < len(texto):
        char = texto[i]
        if char == "'":
            if dentro and i + 1 < len(texto) and texto[i + 1] == "'":
                atual.append("''")
                i += 2
                continue
            dentro = not dentro
            atual.append(char)
        elif not dentro and char == "(":
            profundidade += 1
            atual.append(char)
        elif not dentro and char == ")":
            profundidade -= 1
            atual.append(char)
        elif char == "," and not dentro and profundidade == 0:
            itens.append("".join(atual).strip())
            atual = []
        else:
            atual.append(char)
        i += 1
    if atual:
        itens.append("".join(atual).strip())
    return [x for x in itens if x]


def _coluna_da_expressao(expressao: str) -> str:
    m = re.match(r"^([a-z_][a-z0-9_]*)\s*=", expressao)
    return m.group(1) if m else ""


def conferir_enriquecimento(expressao: str) -> None:
    """Cada item do SET de `contacts` tem de ser coluna declarada e na forma que NAO sobrescreve.

    Guarda de desenho, nao de prosa: se alguem trocar `COALESCE(NULLIF(col,''), valor)` por
    `col = valor`, a guarda recusa e o contato nao e escrito (medido por mutacao no autoteste).
    """
    if expressao.lower().replace(" ", "") == "updated_at=now()":
        return
    coluna = _coluna_da_expressao(expressao)
    if coluna not in COLUNAS_ENRIQUECIMENTO:
        raise GuardaDeEscritaViolada(
            "enriquecimento fora das colunas declaradas: %r" % (coluna or expressao))
    padrao = re.compile(_EXPR_TEXTO.format(c=re.escape(coluna)))
    if not padrao.match(expressao):
        raise GuardaDeEscritaViolada(
            "coluna sem COALESCE(NULLIF(%s,''), ...): %s" % (coluna, expressao))


def conferir_restauracao(expressao: str) -> None:
    """A restauracao devolve valor CONHECIDO (o anterior, gravado no sync_event da rodada)."""
    if expressao.lower().replace(" ", "") == "updated_at=now()":
        return
    coluna = _coluna_da_expressao(expressao)
    if coluna not in COLUNAS_ENRIQUECIMENTO:
        raise GuardaDeEscritaViolada(
            "restauracao fora das colunas declaradas: %r" % (coluna or expressao))


def conferir_insert_de_contato(sql: str) -> None:
    """O INSERT de `contacts` so' lista as colunas declaradas — nem `odoo_partner_id`, nem opt-out."""
    for tabela, bruto in _INSERT_COLUNAS.findall(_sem_literais(sql)):
        if tabela.lower() != TABELA_CONTATOS:
            continue
        colunas = [c.strip().lower() for c in bruto.split(",") if c.strip()]
        fora = [c for c in colunas if c not in COLUNAS_DE_INSERT]
        if fora:
            raise GuardaDeEscritaViolada(
                "INSERT de contacts fora das colunas declaradas: %s" % ", ".join(fora))


def validar_sql(sql: str, modo_contatos=None, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL, tabela nao declarada e escrita fora do declarado.

    `contacts` aceita INSERT (contato novo) e UPDATE nos dois modos declarados
    (enriquecimento com COALESCE / restauracao do desfazer); DELETE em `contacts` NUNCA e'
    permitido fora do `--desfazer --confirmo`. `organizations` e SOMENTE LEITURA para este
    agente: qualquer escrita nela e recusa, inclusive o enriquecimento que o Research faz.
    """
    if modo_contatos is not None and modo_contatos not in MODOS_CONTATOS:
        raise GuardaDeEscritaViolada("modo de escrita em contacts desconhecido: %r" % modo_contatos)
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL nao e' permitido ao agente Contact Research")
    conferir_insert_de_contato(codigo)
    for item in _SET.finditer(codigo):
        tabela = item.group(1).lower()
        if tabela != TABELA_CONTATOS:
            continue
        if modo_contatos is None:
            raise GuardaDeEscritaViolada(
                "UPDATE em contacts fora do modo declarado (enriquecimento/restauracao)")
        for expressao in _itens_do_set(item.group(2) + ","):
            if modo_contatos == MODO_ENRIQUECIMENTO:
                conferir_enriquecimento(expressao)
            else:
                conferir_restauracao(expressao)
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela nao declarada: %s" % tabela)
            if tabela == TABELA_ORGANIZACOES:
                raise GuardaDeEscritaViolada(
                    "escrita em organizations nao e' permitida ao Contact Research: a empresa e' "
                    "do Scout/Research (somente leitura aqui)")
            if tabela == TABELA_CONTATOS and operacao == "update" and modo_contatos is None:
                raise GuardaDeEscritaViolada("UPDATE em contacts sem modo declarado")
            if operacao == "delete" and not permitir_remocao:
                raise GuardaDeEscritaViolada("DELETE fora do desfazer explicito: %s" % tabela)


# ---------------------------------------------------------------------------------------
# Porta de banco (ADR-0008: o SQL roda na VPS; a porta e o prefixo psql)
# ---------------------------------------------------------------------------------------
class PortaSQL:
    def executar(self, sql: str, modo_contatos=None, permitir_remocao: bool = False) -> tuple:
        raise NotImplementedError


class PortaAusente(PortaSQL):
    """Sem porta configurada: qualquer tentativa de falar com o banco RECUSA."""

    def executar(self, sql: str, modo_contatos=None, permitir_remocao: bool = False) -> tuple:
        raise PortaIndisponivel(
            "nenhuma porta de banco configurada (use --prefixo ou TRE_PSQL_PREFIXO)")


class PortaPsql(PortaSQL):
    """Executa SQL pelo prefixo psql informado (ex.: 'docker exec -i pg-sales-dev psql -U ..')."""

    def __init__(self, prefixo: str, timeout: int = 120):
        if not prefixo:
            raise PortaIndisponivel("prefixo psql vazio (use --prefixo ou TRE_PSQL_PREFIXO)")
        self.prefixo = shlex.split(prefixo)
        self.timeout = timeout

    def executar(self, sql: str, modo_contatos=None, permitir_remocao: bool = False) -> tuple:
        validar_sql(sql, modo_contatos=modo_contatos, permitir_remocao=permitir_remocao)
        # `-q` evita que os carimbos de comando (BEGIN/COMMIT) poluam a saida; o resultado e
        # conferido LINHA a linha, porque a porta nao promete formato.
        comando = self.prefixo + ["-v", "ON_ERROR_STOP=1", "-q", "-tA", "-F", "|"]
        try:
            p = subprocess.run(comando, input=sql, capture_output=True, text=True,
                               timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PortaIndisponivel("porta psql falhou: %s" % exc)
        return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------------------
# SQL do agente
# ---------------------------------------------------------------------------------------
def sql_consultar_organizacao(validos: list) -> str:
    """Identidade forte -> organizacoes casadas, em JSON (o texto da empresa nao quebra a leitura)."""
    termos = ["(%s IS NOT NULL AND %s = %s)" % (tipo, tipo, lit(valor)) for tipo, valor in validos]
    campos = ["'id'", "id::text", "'status'", "COALESCE(status, '')",
              "'legal_name'", "COALESCE(legal_name, '')", "'trade_name'", "COALESCE(trade_name, '')"]
    return (
        "SELECT jsonb_build_object(%s)::text FROM %s "
        "WHERE deleted_at IS NULL AND (%s) ORDER BY id;" % (
            ", ".join(campos), TABELA_ORGANIZACOES, " OR ".join(termos))
    )


def sql_consultar_contatos(organizacao_id: str, email: str) -> str:
    """Identidade do contato -> contatos casados NA EMPRESA, com todas as colunas declaradas.

    A comparacao normaliza os DOIS lados (`lower(email)`): linha antiga gravada com maiuscula
    nao vira contato duplicado. O escopo e' a empresa resolvida — a mesma pessoa em duas
    empresas sao DOIS contatos legitimos no modelo V1 (`contacts.organization_id`).
    """
    campos = ["'id'", "id::text", "'organization_id'", "organization_id::text",
              "'odoo_partner_id'", "COALESCE(odoo_partner_id::text, '')"]
    for coluna in COLUNAS_DO_CONTATO:
        campos.extend(["'%s'" % coluna, "COALESCE(%s::text, '')" % coluna])
    return (
        "SELECT jsonb_build_object(%s)::text FROM %s "
        "WHERE organization_id = %s AND lower(email) = %s ORDER BY id;" % (
            ", ".join(campos), TABELA_CONTATOS, lit(organizacao_id), lit(email))
    )


def expressao_enriquecimento(coluna: str, valor) -> str:
    """A forma que NAO sobrescreve: a coluna preenchida vence o que a fonte trouxe."""
    return "%s = COALESCE(NULLIF(%s, ''), %s)" % (coluna, coluna, lit(valor))


def montar_linha_contato(contato_id: str, organizacao_id: str, valores: dict) -> tuple:
    """Colunas x valores do INSERT do contato — paridade conferida contra o DDL pela suite."""
    colunas = ["id", "organization_id"] + list(COLUNAS_DO_CONTATO) + ["created_at", "updated_at"]
    expressoes = [lit(contato_id), lit(organizacao_id)]
    for coluna in COLUNAS_DO_CONTATO:
        valor = valores["contato"].get(coluna)
        expressoes.append(lit(valor) if valor not in (None, "") else "NULL")
    expressoes.extend(["now()", "now()"])
    if len(expressoes) != len(colunas):
        raise ValueError("montagem do INSERT de contacts divergente: %d colunas x %d valores"
                         % (len(colunas), len(expressoes)))
    return colunas, expressoes


def _sql_claim(sync_event_id: str, contato_id: str, chave: str, payload: dict) -> str:
    """O claim da chave: a PRIMEIRA instrucao da rodada, e a ancora de todas as outras.

    Toda instrucao da rodada exige `id = <sevid> AND status = 'PENDING'` do PROPRIO sync_event.
    Ancorar na chave faria o replay devolver "identificado" sem nada ter sido escrito; ancorar
    no contato faria o mesmo quando o contato ja existisse de uma rodada anterior.
    """
    return (
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'contact', {cid}, {origem}, 'postgresql', {operacao}, {versao}, "
        "{chave}, 'PENDING', {payload}, now())\n"
        "ON CONFLICT (idempotency_key) DO NOTHING\n"
        "RETURNING id;".format(sync=TABELA_SYNC_EVENTS, sevid=lit(sync_event_id),
                               cid=lit(contato_id), origem=lit(AGENTE),
                               operacao=lit(OPERACAO_SYNC), versao=lit(VERSAO),
                               chave=lit(chave), payload=lit_json(payload))
    )


def _sql_fechamento(sync_event_id: str, contato_id: str, chave: str, acao: str) -> str:
    """A marca da rodada: so fecha o sync_event DESTA rodada (status ainda PENDING)."""
    return (
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(),\n"
        "  response_payload = jsonb_build_object('contact_id', {cid}, 'veredito', "
        "'IDENTIFICADO', 'acao', {acao}, 'idempotency_key', {chave})\n"
        "WHERE id = {sevid} AND idempotency_key = {chave} AND status = 'PENDING'\n"
        "RETURNING {marca};".format(sync=TABELA_SYNC_EVENTS, cid=lit(contato_id),
                                    acao=lit(acao), chave=lit(chave), sevid=lit(sync_event_id),
                                    marca=lit(MARCA_IDENTIFICADO))
    )


def sql_ingerir_criar(contato_id: str, organizacao_id: str, sync_event_id: str, chave: str,
                      valores: dict, payload: dict) -> str:
    """Rodada que CRIA o contato: claim -> INSERT do contato -> fechamento com a marca."""
    colunas, expressoes = montar_linha_contato(contato_id, organizacao_id, valores)
    return "\n".join([
        "BEGIN;",
        _sql_claim(sync_event_id, contato_id, chave, payload),
        "INSERT INTO {tabela} ({cols})\n"
        "SELECT {vals} FROM {sync} WHERE id = {sevid} AND status = 'PENDING'\n"
        "ON CONFLICT (id) DO NOTHING\n"
        "RETURNING id;".format(tabela=TABELA_CONTATOS, cols=", ".join(colunas),
                               vals=", ".join(expressoes), sync=TABELA_SYNC_EVENTS,
                               sevid=lit(sync_event_id)),
        _sql_fechamento(sync_event_id, contato_id, chave, "criado"),
        "COMMIT;",
    ]) + "\n"


def sql_ingerir_enriquecer(contato_id: str, organizacao_id: str, sync_event_id: str, chave: str,
                           colunas_enriquecidas: list, valores: dict, payload: dict) -> str:
    """Rodada que ENRIQUECE o contato que ja existe: claim -> UPDATE das vazias -> fechamento."""
    comandos = ["BEGIN;", _sql_claim(sync_event_id, contato_id, chave, payload)]
    if colunas_enriquecidas:
        itens = [expressao_enriquecimento(c, valores["contato"][c]) for c in colunas_enriquecidas]
        itens.append("updated_at = now()")
        comandos.append(
            "UPDATE {tabela} SET {itens}\n"
            "WHERE id = {cid} AND EXISTS (SELECT 1 FROM {sync} WHERE id = {sevid} "
            "AND status = 'PENDING')\n"
            "RETURNING id;".format(tabela=TABELA_CONTATOS, itens=", ".join(itens),
                                   cid=lit(contato_id), sync=TABELA_SYNC_EVENTS,
                                   sevid=lit(sync_event_id)))
    comandos.append(_sql_fechamento(sync_event_id, contato_id, chave, "enriquecido"))
    comandos.append("COMMIT;")
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


def sql_pedir_revisao(approval_id: str, pedido: dict, motivo: str, organizacoes: list,
                      contatos: list, correlation_id: str) -> str:
    proposta = {
        "motivo": motivo,
        "agente": "%s/%s" % (AGENTE, VERSAO),
        "correlation_id": correlation_id,
        "pedido": pedido,
        "organizacoes_casadas": organizacoes,
        "contatos_casados": contatos,
        "regra": "ambiguidade nao e' resolvida por heuristica: e' reportada (dedup.rule do contrato)",
    }
    return (
        "INSERT INTO {tabela} (id, action_type, entity_type, entity_id, requested_by, "
        "proposed_action, status, requested_at)\n"
        "VALUES ({aid}, {acao}, 'contact', NULL, {quem}, {proposta}, 'PENDING', now());\n"
    ).format(tabela=TABELA_APPROVALS, aid=lit(approval_id), acao=lit(ACTION_TYPE_REVISAO),
             quem=lit("%s/%s" % (AGENTE, VERSAO)), proposta=lit_json(proposta))


def sql_selecionar_da_rodada(correlation_id: str) -> str:
    """O que a rodada escreveu: o contato, a acao e os valores ANTES/DEPOIS de cada coluna."""
    return (
        "SELECT jsonb_build_object(\n"
        "  'contact_id', s.entity_id::text,\n"
        "  'sync_event_id', s.id::text,\n"
        "  'organization_id', s.request_payload ->> 'organization_id',\n"
        "  'acao', s.request_payload ->> 'acao',\n"
        "  'antes', s.request_payload -> 'antes',\n"
        "  'depois', s.request_payload -> 'depois',\n"
        "  'espelhado', (SELECT COALESCE(c.odoo_partner_id::text, '') FROM {contatos} c\n"
        "                WHERE c.id = s.entity_id),\n"
        "  'existe', EXISTS (SELECT 1 FROM {contatos} c WHERE c.id = s.entity_id)\n"
        ")::text\n"
        "FROM {sync} s\n"
        "WHERE s.operation = {operacao} AND s.request_payload ->> 'correlation_id' = {corr}\n"
        "ORDER BY s.id;\n"
    ).format(contatos=TABELA_CONTATOS, sync=TABELA_SYNC_EVENTS, operacao=lit(OPERACAO_SYNC),
             corr=lit(correlation_id))


def lit_restauracao(coluna: str, valor) -> str:
    """O valor ANTERIOR volta como texto (todas as colunas de contato sao de texto)."""
    if valor in (None, ""):
        return "NULL"
    return lit(valor)


def sql_desfazer(itens: list, correlation_id: str, sync_event_id: str) -> str:
    """Devolve os valores anteriores, apaga o que a rodada CRIOU e registra o ROLLBACK.

    Só entra aqui o que a guarda de `desfazer` autorizou (nenhum contato da rodada ja espelhado).
    """
    comandos = ["BEGIN;"]
    for item in itens:
        if not item.get("existe"):
            continue
        if item.get("acao") == "criado":
            continue                       # contato criado pela rodada: apagado mais abaixo
        colunas = [c for c in item.get("depois", {}) if item.get("depois", {}).get(c) is not None]
        if not colunas:
            continue
        itens_set = ["%s = %s" % (c, lit_restauracao(c, item["antes"].get(c))) for c in colunas]
        itens_set.append("updated_at = now()")
        comandos.append(
            "UPDATE {contatos} SET {itens} WHERE id = {cid} RETURNING id;".format(
                contatos=TABELA_CONTATOS, itens=", ".join(itens_set),
                cid=lit(item["contact_id"])))
    criados = [i["contact_id"] for i in itens if i.get("acao") == "criado" and i.get("existe")]
    if criados:
        comandos.append("DELETE FROM {contatos} WHERE id IN ({ids});".format(
            contatos=TABELA_CONTATOS, ids=", ".join(lit(i) for i in criados)))
    ids_sync = ", ".join(lit(i["sync_event_id"]) for i in itens if i.get("sync_event_id"))
    if ids_sync:
        comandos.append("DELETE FROM {sync} WHERE id IN ({ids});".format(
            sync=TABELA_SYNC_EVENTS, ids=ids_sync))
    payload = {"motivo": "desfazer da rodada de contato", "correlation_id": correlation_id,
               "contatos_criados": criados,
               "contatos_restaurados": [i["contact_id"] for i in itens
                                        if i.get("acao") != "criado" and i.get("existe")]}
    comandos.append(
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, operation, "
        "source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'contact', NULL, {origem}, 'postgresql', 'ROLLBACK', {versao}, "
        "{chave}, 'SUCCESS', {payload}, now());".format(
            sync=TABELA_SYNC_EVENTS, sevid=lit(sync_event_id), origem=lit(AGENTE),
            versao=lit(VERSAO), chave=lit("contact:rollback:%s" % correlation_id),
            payload=lit_json(payload)))
    comandos.append("COMMIT;")
    return "\n".join(comandos) + "\n"


# ---------------------------------------------------------------------------------------
# Gate do JEV (doc 07 §7: antes de chamada de LLM em tarefa elegivel, o Hermes consulta o JEV)
# ---------------------------------------------------------------------------------------
def validar_recibo_jev(recibo) -> dict:
    if not recibo:
        raise ReciboJEVInvalido("sem recibo de decisao do JEV: abstencao e' recusa, nao permissao")
    if not isinstance(recibo, dict):
        raise ReciboJEVInvalido("recibo do JEV ilegivel")
    faltando = [c for c in ("decision_id", "lane", "outcome") if not recibo.get(c)]
    if faltando:
        raise ReciboJEVInvalido("recibo do JEV incompleto: %s" % ", ".join(faltando))
    if recibo["outcome"] != JEV_EXECUTAR:
        raise ReciboJEVInvalido("recibo do JEV com outcome %r (exige %r)"
                                % (recibo["outcome"], JEV_EXECUTAR))
    if recibo["lane"] not in LANES:
        raise ReciboJEVInvalido("lane fora do vocabulario: %r" % recibo["lane"])
    return recibo


def chamar_llm(prompt: str, recibo=None, provedor=None, modelo=None) -> dict:
    """v1 NAO executa LLM — o gate existe e e' fail-closed. Sem recibo valido, nao passa."""
    recibo = validar_recibo_jev(recibo)
    return {
        "autorizado": True,
        "decision_id": recibo["decision_id"],
        "lane": recibo["lane"],
        "provedor": provedor,
        "modelo": modelo,
        "prompt_sha256": _sha256(prompt),
        "executado": False,
        "motivo": "v1 do Contact Research e' deterministica: nenhuma chamada de LLM e' feita",
    }


def elegibilidade_do_evento(contato_efetivo: dict, contrato: dict) -> dict:
    """O evento de espelho do contrato (`DECISION_MAKER_FOUND`) e' MEDIDO, nao emitido na v1.

    Regra de elegibilidade (declarada no contrato do agente): o contato efetivo depois da
    escrita tem `decision_role` em `papeis_de_decisao` E tem `email` E tem `full_name` — os tres
    campos que o mapeamento do consumidor exige para entregar `contato_upsert` no CRM.
    """
    papel = _texto(contato_efetivo.get("decision_role"))
    papeis = tuple(contrato["papeis_de_decisao"])
    faltando = [campo for campo in ("email", "full_name") if not _texto(contato_efetivo.get(campo))]
    elegivel = papel in papeis and not faltando
    return {
        "event_type": contrato["espelho"]["event_type"],
        "emitido": False,
        "elegivel": elegivel,
        "motivo_de_nao_emitir": contrato["espelho"]["motivo_de_nao_emitir"],
        "o_que_faltaria": (["papel_de_decisao"] if papel not in papeis else []) + faltando,
    }


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------------------
class ContactResearch:
    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None,
                 identidade=None):
        self.raiz = Path(raiz or RAIZ_PADRAO)
        self.contrato = carregar_contrato_do_agente(self.raiz)
        self.fortes = fortes_do_contrato_de_dados(self.raiz)
        if list(self.fortes) != list(self.contrato["identidade"]["fortes_por_prioridade"]):
            raise ValueError("divergencia entre o contrato do agente e o Data Contract V1.0: %s x %s"
                             % (list(self.fortes),
                                self.contrato["identidade"]["fortes_por_prioridade"]))
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
                "Contact Research v1 nao escreve em prod (ADR-005): a promocao exige card proprio "
                "com aprovacao humana registrada")
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

    # -- pedido ----------------------------------------------------------------------
    def _linhas_json(self, sql: str, rotulo: str) -> list:
        rc, saida, erro = self.porta.executar(sql)
        if rc != 0:
            raise PortaIndisponivel("%s falhou: %s" % (rotulo, erro or saida))
        registros = []
        for linha in saida.splitlines():
            linha = linha.strip()
            if not linha or linha in ("BEGIN", "COMMIT"):
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if registro.get("id") or registro.get("contact_id"):
                registros.append(registro)
        return registros

    def organizacoes_casadas(self, validos: list) -> list:
        if not validos:
            return []
        return self._linhas_json(sql_consultar_organizacao(validos), "consulta de organizacao")

    def contatos_casados(self, organizacao_id: str, email: str) -> list:
        return self._linhas_json(sql_consultar_contatos(organizacao_id, email),
                                 "consulta de contato")

    def colunas_a_enriquecer(self, registro: dict, aceitos: dict) -> tuple:
        """Separa o que sera escrito do que o contato JA sabia (nunca sobrescreve)."""
        a_enriquecer, preservadas = [], []
        for coluna in COLUNAS_ENRIQUECIMENTO:
            if coluna not in aceitos:
                continue
            atual = registro.get(coluna)
            if atual in (None, ""):
                a_enriquecer.append(coluna)
            else:
                preservadas.append(coluna)
        return a_enriquecer, preservadas

    def planejar(self, pedido: dict) -> dict:
        """Modo sem banco: valida e diz o que FARIA."""
        validacao = validar_pedido(pedido, self.fortes, self.contrato, self.identidade)
        veredito = PLANEJADO_RECUSAR if validacao["problemas"] else PLANEJADO_IDENTIFICAR
        return {"veredito": veredito, "motivos": validacao["problemas"],
                "identidades_validas": [{"tipo": t, "valor": v} for t, v in validacao["validos"]],
                "identidades_declaradas": validacao["declarados"],
                "contato": validacao["contato"], "email": validacao["email"],
                "fontes": validacao["fontes"], "descartados": validacao["descartados"],
                "input_hash": hash_da_entrada(validacao["validos"], validacao["email"],
                                              validacao["contato"], validacao["fontes"])
                if validacao["email"] and not validacao["problemas"] else None}

    def processar(self, pedido: dict) -> dict:
        inicio = self.relogio()
        entrada = {"pedido": pedido, "ambiente": self.ambiente,
                   "correlation_id": self.correlation_id}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": ERRO, "motivos": [], "organization_id": None,
                     "contact_id": None, "idempotency_key": None, "acao": None,
                     "fontes": [], "colunas_enriquecidas": [], "colunas_preservadas": [],
                     "descartados": [], "contato_aceito": {}}
        try:
            validacao = validar_pedido(pedido, self.fortes, self.contrato, self.identidade)
            resultado["fontes"] = validacao["fontes"]
            resultado["descartados"] = validacao["descartados"]
            resultado["contato_aceito"] = validacao["contato"]
            resultado["identidades_validas"] = [{"tipo": t, "valor": v}
                                                for t, v in validacao["validos"]]
            organizacoes = (self.organizacoes_casadas(validacao["validos"])
                            if validacao["validos"] else [])
            contatos = []
            if len(organizacoes) == 1 and validacao["email"] and not validacao["problemas"]:
                contatos = self.contatos_casados(organizacoes[0]["id"], validacao["email"])
            veredito, motivo = decidir_veredito(validacao["problemas"], organizacoes, contatos)
            resultado["veredito"] = veredito
            resultado["motivos"] = list(validacao["problemas"]) or ([motivo] if motivo else [])
            if veredito == IDENTIFICADO:
                self._escrever(validacao, organizacoes[0], contatos[0] if contatos else None,
                               resultado)
            elif veredito == REVISAO:
                aprovacao_id = str(uuid.uuid4())
                rc_rev, saida_rev, erro_rev = self.porta.executar(
                    sql_pedir_revisao(aprovacao_id, pedido, resultado["motivos"][0],
                                      [{"organization_id": o["id"]} for o in organizacoes],
                                      [{"contact_id": c["id"]} for c in contatos],
                                      self.correlation_id))
                if rc_rev != 0:
                    # Fail-closed: sem a linha em human_approvals a ambiguidade NAO foi
                    # reportada — entao nao se pode dizer REVISAO_IDENTIDADE.
                    raise PortaIndisponivel("fila humana nao registrada: %s"
                                            % (erro_rev or saida_rev))
                resultado["human_approval_id"] = aprovacao_id
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        saida_auditoria = {k: v for k, v in resultado.items() if k != "contato_aceito"}
        status = STATUS_AGENT_RUNS[resultado["veredito"]]
        rc_run, saida_run, erro_run = self.porta.executar(sql_registrar_execucao(
            run_id, self.correlation_id, resultado.get("organization_id"), status,
            entrada, saida_auditoria, inicio, fim, resultado.get("erro")))
        if rc_run != 0:
            # Fail-closed: a execucao NAO pode ser reportada como concluida quando a propria
            # auditoria nao foi escrita.
            resultado["veredito"] = ERRO
            resultado["motivos"] = list(resultado.get("motivos") or []) + \
                ["AUDITORIA_NAO_REGISTRADA: %s" % (erro_run or saida_run)]
            resultado["erro"] = {"tipo": "PortaIndisponivel",
                                 "mensagem": "auditoria nao registrada: %s"
                                             % (erro_run or saida_run)}
            resultado["auditoria_registrada"] = False
            resultado["agent_run_id"] = run_id
            resultado["status_agent_runs"] = STATUS_AGENT_RUNS[ERRO]
            return resultado
        resultado["agent_run_id"] = run_id
        resultado["auditoria_registrada"] = True
        resultado["status_agent_runs"] = status
        return resultado

    def _escrever(self, validacao: dict, organizacao: dict, contato_existente, resultado: dict):
        """A escrita da rodada: cria o contato novo ou enriquece o que ja existe."""
        organizacao_id = organizacao["id"]
        entrada_hash = hash_da_entrada(validacao["validos"], validacao["email"],
                                       validacao["contato"], validacao["fontes"])
        chave = chave_idempotencia(organizacao_id, entrada_hash)
        contato_id = contato_existente["id"] if contato_existente else str(uuid.uuid4())
        sync_event_id = str(uuid.uuid4())
        if contato_existente:
            a_enriquecer, preservadas = self.colunas_a_enriquecer(contato_existente,
                                                                  validacao["contato"])
            acao = "enriquecido"
        else:
            a_enriquecer = [c for c in COLUNAS_DO_CONTATO if validacao["contato"].get(c)]
            preservadas = []
            acao = "criado"
        antes = {c: (contato_existente.get(c) if contato_existente else None)
                 for c in COLUNAS_DO_CONTATO}
        depois = {c: (validacao["contato"][c] if c in a_enriquecer else None)
                  for c in COLUNAS_DO_CONTATO}
        efetivo = {c: (validacao["contato"].get(c) or (contato_existente or {}).get(c) or "")
                   for c in COLUNAS_DO_CONTATO}
        espelho = elegibilidade_do_evento(efetivo, self.contrato)
        resumo = resumo_do_contato(validacao, acao, a_enriquecer, preservadas)
        payload = {"origem": AGENTE, "agente": "%s/%s" % (AGENTE, VERSAO),
                   "correlation_id": self.correlation_id, "contato_id": contato_id,
                   "organization_id": organizacao_id, "acao": acao,
                   "identidade": {"email": validacao["email"]}, "fontes": validacao["fontes"],
                   "input_hash": entrada_hash, "idempotency_key": chave, "antes": antes,
                   "depois": depois, "evento_de_espelho": espelho, "resumo": resumo,
                   "identidades": [{"tipo": t, "valor": v} for t, v in validacao["validos"]]}
        valores = {"contato": validacao["contato"]}
        if acao == "criado":
            sql = sql_ingerir_criar(contato_id, organizacao_id, sync_event_id, chave, valores,
                                    payload)
            modo = None
        else:
            sql = sql_ingerir_enriquecer(contato_id, organizacao_id, sync_event_id, chave,
                                         a_enriquecer, valores, payload)
            modo = MODO_ENRIQUECIMENTO if a_enriquecer else None
        rc, saida, erro = self.porta.executar(sql, modo_contatos=modo)
        if rc != 0:
            raise PortaIndisponivel("registro do contato falhou: %s" % (erro or saida))
        marcas = [linha.strip() for linha in saida.splitlines()]
        resultado["organization_id"] = organizacao_id
        resultado["idempotency_key"] = chave
        resultado["acao"] = acao
        resultado["colunas_enriquecidas"] = a_enriquecer
        resultado["colunas_preservadas"] = preservadas
        resultado["evento_de_espelho"] = espelho
        resultado["resumo"] = resumo
        if MARCA_IDENTIFICADO in marcas:
            resultado["contact_id"] = contato_id
            resultado["sync_event_id"] = sync_event_id
        else:
            # a chave ja existia: nada foi duplicado (retry idempotente)
            resultado["veredito"] = JA_IDENTIFICADO
            resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]

    def rodar(self, pedidos: list, planejar: bool = False) -> dict:
        resultados = []
        for pedido in pedidos:
            resultados.append(self.planejar(pedido) if planejar else self.processar(pedido))
        resumo = {"agente": "%s/%s" % (AGENTE, VERSAO), "ambiente": self.ambiente,
                  "correlation_id": self.correlation_id, "alvo": self.alvo,
                  "planejar": planejar, "total": len(resultados),
                  "por_veredito": {}, "resultados": resultados}
        for r in resultados:
            resumo["por_veredito"][r["veredito"]] = resumo["por_veredito"].get(r["veredito"], 0) + 1
        return resumo

    def desfazer(self, correlation_id: str, confirmo: bool = False) -> dict:
        itens = self._linhas_json(sql_selecionar_da_rodada(correlation_id),
                                  "leitura da rodada")
        espelhados = [i for i in itens if i.get("acao") == "criado" and i.get("espelhado")]
        resumo = {"correlation_id": correlation_id, "confirmo": confirmo,
                  "contatos": [i["contact_id"] for i in itens if i.get("existe")],
                  "criados": [i["contact_id"] for i in itens if i.get("acao") == "criado"],
                  "restaurados": [i["contact_id"] for i in itens
                                  if i.get("acao") != "criado" and i.get("existe")],
                  "espelhados": [i["contact_id"] for i in espelhados],
                  "apagadas": 0, "dry_run": True}
        if not confirmo:
            resumo["recusaria"] = bool(espelhados)
            resumo["motivo"] = ("CONTATO_JA_ESPELHADO" if espelhados else None)
            return resumo
        if espelhados:
            # Fail-closed: apagar no PostgreSQL um contato que ja existe no CRM desfaz a rodada
            # deixando as duas pontas discordando. Quem resolve isso e' o operador.
            raise RollbackRecusado(
                "a rodada tem contato ja espelhado no CRM (%s): desfazer aqui deixaria as duas "
                "pontas discordando" % ", ".join(i["contact_id"] for i in espelhados))
        if itens:
            rc, saida, erro = self.porta.executar(
                sql_desfazer(itens, correlation_id, str(uuid.uuid4())),
                modo_contatos=MODO_RESTAURACAO, permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
        resumo["dry_run"] = False
        resumo["apagadas"] = len(resumo["criados"])
        return resumo


def resumo_do_contato(validacao: dict, acao: str, enriquecidas: list, preservadas: list) -> str:
    """Resumo deterministico: nenhuma frase e' inferencia, e o que nao foi escrito esta declarado."""
    return (
        "Contato %s (deterministico, sem LLM): %d fonte(s) [%s]; %d campo(s) aceito(s); "
        "%d coluna(s) enriquecida(s) [%s]; %d coluna(s) preservada(s) [%s]; "
        "%d campo(s) descartado(s); base legal %s."
        % (acao, len(validacao["fontes"]),
           ", ".join(f["tipo"] for f in validacao["fontes"]) or "-",
           len(validacao["contato"]), len(enriquecidas), ", ".join(enriquecidas) or "-",
           len(preservadas), ", ".join(preservadas) or "-",
           len(validacao["descartados"]), validacao["contato"].get("legal_basis") or "-")
    )


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_contatos(caminho: str) -> list:
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
    p = argparse.ArgumentParser(description="Agente Contact Research v1 (TRE-W4-E05-T01)")
    p.add_argument("--fonte", help="arquivo jsonl com os pedidos de contato ('-' = stdin)")
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
    agente = ContactResearch(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                             correlation_id=args.correlation_id)
    # O ambiente e' exigido em todo modo que ESCREVE. No modo de planejamento (que nao abre
    # conexao nenhuma) ele e' opcional: se declarado, e' conferido; se ausente, nada e' escrito.
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
            pedidos = ler_contatos(args.fonte)
            if args.planejar:
                relatorio = agente.rodar(pedidos, planejar=True)
            else:
                agente.identificar_alvo()
                relatorio = agente.rodar(pedidos)
    except (PortaIndisponivel, RollbackRecusado) as exc:
        print("FALHOU %s" % exc)
        return EXIT_FALHOU
    if args.relatorio:
        Path(args.relatorio).write_text(json.dumps(relatorio, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({"agente": relatorio.get("agente"), "ambiente": relatorio.get("ambiente"),
                      "correlation_id": relatorio["correlation_id"],
                      "alvo": relatorio.get("alvo"), "total": relatorio.get("total"),
                      "por_veredito": relatorio.get("por_veredito"),
                      "apagadas": relatorio.get("apagadas"), "dry_run": relatorio.get("dry_run")},
                     ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-20s %-10s %s" % (r["veredito"], (r.get("acao") or "-"),
                                    ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
