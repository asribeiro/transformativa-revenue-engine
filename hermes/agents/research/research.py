#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agente Research v1 — pesquisa/enriquecimento da empresa ja descoberta (card TRE-W4-E02-T01).

O que este agente FAZ (e so isto): recebe pedidos de PESQUISA sobre empresas que JA existem no
PostgreSQL (o produtor da empresa e o Scout, TRE-W4-E01-T01), resolve a organizacao pelos
identificadores FORTES do Data Contract V1.0 (cnpj -> domain -> linkedin_url), grava a execucao
em `research_runs` (com evidencias, `input_hash` e `source_count`) e ENRIQUECE as colunas vazias
da organizacao com o que a fonte mostrou — o segundo estado do funil, "Pesquisado"
(doc 03 §2, doc 06 §2).

O que ele NAO faz, por desenho (declarado em `agente-research-v1.json` -> lacunas):
  - nao CRIA organizacao (identidade que nao casa e RECUSADA com motivo);
  - nao escreve identificador FORTE (cnpj/domain/linkedin_url), nem `status`, nem
    `data_quality_score`: identidade e do Scout/dedup, estagio e do Odoo (doc 12 §1) e score e W5;
  - nao detecta sinais, nao cria hipotese de dor e nao cria contato (W4-E03/E04/E05);
  - nao emite evento de outbox (o contrato nao tem evento de pesquisa em doc 06 §5);
  - nao faz requisicao de rede nenhuma (sem crawler, sem LLM na v1) e nao toca Odoo/Titan/n8n;
  - nao escreve em producao (ADR-005).

Regras que sustentam o enriquecimento:
  - **a coluna JA preenchida nunca e sobrescrita** — e o SQL que garante, nao a prosa:
    `COALESCE(NULLIF(col, ''), valor)` para texto e `COALESCE(col, valor)` para numero. A guarda
    de escrita exige exatamente esse formato (medido por mutacao no autoteste);
  - achado invalido e DESCARTADO com motivo (`structured_output.descartados`), nunca escrito;
  - achado publico e registrado como veio: `employee_band` e sempre DERIVADA de `employee_count`
    pelo vocabulario do contrato (dado derivado nao se aceita da fonte);
  - so se escrevem colunas do TIPO declarado do pedido (`tipos_de_pesquisa` do contrato do agente).

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"):
  - a chave e a ENTRADA, nao a rodada: `research:org:<uuid>:<tipo>:<input_hash>`, gravada em
    `sync_events.idempotency_key` (UNIQUE). A ingestao e UMA transacao com tres comandos:
    claim da chave + INSERT do research_run -> UPDATE de enriquecimento (ancorado no research_run
    DESTA rodada) -> fechamento do sync_event com a marca `RESEARCH_PESQUISADA`. Replay: o claim
    volta vazio, nada e inserido nem enriquecido e o veredito vira JA_PESQUISADO.
  - a historia de cada pedido fica em `agent_runs` (uma linha por pedido, com o `correlation_id`
    do lote) — auditoria nao depende da narrativa de quem rodou.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/agents/research/research.py --planejar --fonte pesquisas.jsonl
  python3 hermes/agents/research/research.py --ambiente dev --fonte pesquisas.jsonl \
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
      --relatorio /tmp/research-rodada.json
  python3 hermes/agents/research/research.py --desfazer <correlation_id>
  python3 hermes/agents/research/research.py --desfazer <correlation_id> --confirmo
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
# Identidade do agente (espelha `hermes/agents/research/agente-research-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "research"
PAPEL = "research"
VERSAO = "1.0.0"
WORKFLOW = "pesquisa-empresa"
WORKFLOW_VERSAO = "v1"

TABELA_RESEARCH_RUNS = "sales_intelligence.research_runs"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"
TABELA_APPROVALS = "sales_intelligence.human_approvals"
TABELAS_PERMITIDAS = (TABELA_RESEARCH_RUNS, TABELA_ORGANIZACOES, TABELA_AGENT_RUNS,
                      TABELA_SYNC_EVENTS, TABELA_APPROVALS)

ACTION_TYPE_REVISAO = "RESEARCH_IDENTITY_REVIEW"
OPERACAO_SYNC = "RESEARCH"

# Colunas de `organizations` que a pesquisa PODE enriquecer (uniao dos tipos; ordem declarada).
# Fora desta lista a guarda de escrita recusa — inclusive identidade e status.
COLUNAS_ENRIQUECIMENTO = (
    "industry_code",
    "industry_name",
    "business_model",
    "website_url",
    "city",
    "state",
    "country_code",
    "employee_count",
    "employee_band",
    "unit_count",
    "revenue_estimate",
)

# Tipo de cada coluna decide o formato do COALESCE da guarda e a validacao do achado.
TIPO_POR_COLUNA = {
    "industry_code": "texto",
    "industry_name": "texto",
    "business_model": "texto",
    "website_url": "texto",
    "city": "texto",
    "state": "texto",
    "country_code": "texto",
    "employee_count": "inteiro",
    "employee_band": "texto",
    "unit_count": "inteiro",
    "revenue_estimate": "numero",
}

LIMITE_TEXTO = {
    "industry_code": 100,
    "industry_name": 255,
    "business_model": 50,
    "website_url": 512,
    "city": 120,
    "state": 80,
    "country_code": 2,
    "employee_band": 30,
}

# Faixas de funcionarios: doc 03 §5 + `vocabularies.employee_band` do Data Contract V1.0.
FAIXAS_DE_EMPREGADOS = ((70, "LT_70"), (150, "70_149"), (300, "150_299"), (500, "300_499"),
                        (700, "500_699"), (1000, "700_1000"))
FAIXA_ACIMA = "GT_1000"
FAIXA_SEM_VALOR = "UNKNOWN"

TIPOS_DE_PESQUISA = ("COMPANY_PROFILE", "SIZE_AND_STRUCTURE", "INDUSTRY", "DIGITAL_PRESENCE")

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

PESQUISADA = "PESQUISADA"
JA_PESQUISADO = "JA_PESQUISADO"
REVISAO = "REVISAO_IDENTIDADE"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (PESQUISADA, JA_PESQUISADO, REVISAO, RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    PESQUISADA: "COMPLETED",
    JA_PESQUISADO: "COMPLETED",
    REVISAO: "REVIEW_REQUIRED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

STATUS_RESEARCH_RUN = {"CONCLUIDA": "COMPLETED", "FALHA": "FAILED"}

# Vereditos do modo `--planejar` (nenhuma conexao de banco e feita nele)
PLANEJADO_PESQUISAR = "PLANEJADO_PESQUISAR"
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
CONTRATO_AGENTE_PADRAO = "hermes/agents/research/agente-research-v1.json"
# A identidade de empresa e UMA regra so neste projeto (normalizacao e digito verificador do
# CNPJ, dominio canonico, LinkedIn canonico). A pesquisa IMPORTA a regra do produtor (Scout) em
# vez de manter uma segunda copia dela — copia de regra de identidade e' divergencia esperando
# acontecer, e a suite reprova se aparecer uma segunda implementacao aqui.
MODULO_IDENTIDADE = "hermes/agents/scout/scout.py"

MARCA_PESQUISADA = "RESEARCH_PESQUISADA"


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


# ---------------------------------------------------------------------------------------
# Contratos e regra de identidade importada do produtor
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


def faixa_de_empregados(quantidade):
    """Faixa do vocabulario fechado — derivada SEMPRE de employee_count, nunca aceita da fonte."""
    if quantidade is None:
        return FAIXA_SEM_VALOR
    n = int(quantidade)
    for limite, faixa in FAIXAS_DE_EMPREGADOS:
        if n < limite:
            return faixa
    return FAIXA_ACIMA


def identificadores_validos(pedido: dict, fortes: tuple, identidade) -> list:
    """[(tipo, valor_normalizado)] na ordem de prioridade do contrato; invalido e descartado."""
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


def colunas_do_tipo(tipo: str, contrato: dict) -> list:
    return list(contrato["tipos_de_pesquisa"].get(tipo, []))


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


def validar_valor(coluna: str, valor, tipo_coluna: str) -> tuple:
    """(valor_normalizado | None, motivo). Achado invalido nao e escrito — e descartado."""
    if tipo_coluna == "texto":
        texto = _texto(valor)
        if not texto:
            return None, "VALOR_VAZIO"
        if coluna == "country_code":
            codigo = texto.upper()
            if not re.fullmatch(r"[A-Z]{2}", codigo):
                return None, "PAIS_INVALIDO"
            return codigo, None
        if coluna == "website_url" and not re.match(r"^https?://[^\s]+$", texto):
            return None, "URL_INVALIDA"
        if len(texto) > LIMITE_TEXTO.get(coluna, 255):
            return None, "EXCEDE_O_LIMITE_DA_COLUNA"
        return texto, None
    if tipo_coluna == "inteiro":
        try:
            numero = int(str(valor).strip())
        except (TypeError, ValueError):
            return None, "NAO_E_INTEIRO"
        limites = {"employee_count": (1, 1000000), "unit_count": (1, 100000)}
        minimo, maximo = limites.get(coluna, (1, 1000000))
        if not minimo <= numero <= maximo:
            return None, "FORA_DA_FAIXA"
        return numero, None
    if tipo_coluna == "numero":
        try:
            numero = float(str(valor).strip())
        except (TypeError, ValueError):
            return None, "NAO_E_NUMERO"
        if not 0 <= numero <= 1e15:
            return None, "FORA_DA_FAIXA"
        return round(numero, 2), None
    return None, "TIPO_DE_COLUNA_DESCONHECIDO"


def validar_achados(pedido: dict, tipo: str, contrato: dict) -> tuple:
    """(aceitos, descartados). SO entra coluna declarada para o TIPO do pedido."""
    bruto = pedido.get("achados")
    if bruto in (None, ""):
        return {}, []
    if not isinstance(bruto, dict):
        return {}, [{"campo": "*", "motivo": "ACHADOS_ILEGIVEIS"}]
    do_tipo = colunas_do_tipo(tipo, contrato)
    tipos_coluna = contrato["enriquecimento"]["tipos_por_coluna"]
    fortes = tuple(contrato["identidade"]["fortes_por_prioridade"])
    aceitos, descartados = {}, []
    if "employee_count" in bruto:
        # O numero entra primeiro porque a FAIXA e derivada dele — mas so depois de passar pelas
        # MESMAS fronteiras do laco (tipo declarado e coluna declarada). Sem isto o
        # `employee_count` de um pedido COMPANY_PROFILE era aceito: medido no aceite E2E.
        if "employee_count" not in COLUNAS_ENRIQUECIMENTO or "employee_count" not in do_tipo:
            descartados.append({"campo": "employee_count", "motivo": "COLUNA_FORA_DO_TIPO",
                                "valor": bruto.get("employee_count")})
        else:
            valor, motivo = validar_valor("employee_count", bruto.get("employee_count"),
                                          tipos_coluna["employee_count"])
            if valor is None:
                descartados.append({"campo": "employee_count", "motivo": motivo,
                                    "valor": bruto.get("employee_count")})
            else:
                aceitos["employee_count"] = valor
    for campo, valor in bruto.items():
        if campo == "employee_count":
            continue
        if campo in fortes:
            descartados.append({"campo": campo,
                                "motivo": "IDENTIFICADOR_FORTE_NAO_ESCRITO_PELA_PESQUISA",
                                "valor": valor})
            continue
        if campo == "employee_band":
            descartados.append({"campo": campo, "motivo": "DERIVADO_NAO_ACEITO", "valor": valor})
            continue
        if campo not in COLUNAS_ENRIQUECIMENTO:
            descartados.append({"campo": campo, "motivo": "CAMPO_NAO_DECLARADO", "valor": valor})
            continue
        if campo not in do_tipo:
            descartados.append({"campo": campo, "motivo": "COLUNA_FORA_DO_TIPO", "valor": valor})
            continue
        if campo in aceitos:
            continue
        valor_normalizado, motivo = validar_valor(campo, valor, tipos_coluna[campo])
        if valor_normalizado is None:
            descartados.append({"campo": campo, "motivo": motivo, "valor": valor})
        else:
            aceitos[campo] = valor_normalizado
    # A faixa e DERIVADA: so existe quando o tipo declara a coluna e o numero foi aceito.
    if "employee_band" in do_tipo and "employee_count" in aceitos:
        aceitos["employee_band"] = faixa_de_empregados(aceitos["employee_count"])
    return aceitos, descartados


def hash_da_entrada(identidades: list, tipo: str, achados: dict, fontes: list) -> str:
    """Chave da idempotencia: a ENTRADA canonica (nao a rodada).

    Ordem de chaves fixa e JSON sem espacos: a mesma entrada apresentada de novo tem de produzir
    o MESMO hash — e' isso que faz o retry nao criar duplicata.
    """
    canonico = {
        "identidades": [[t, v] for t, v in identidades],
        "tipo": tipo,
        "achados": {k: achados[k] for k in sorted(achados)},
        "fontes": [{"tipo": f["tipo"], "url": f["url"], "trecho": f["trecho"]} for f in fontes],
    }
    return _sha256(json.dumps(canonico, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def chave_idempotencia(organizacao_id: str, tipo: str, entrada_hash: str) -> str:
    return "research:org:%s:%s:%s" % (organizacao_id, tipo, entrada_hash)


def validar_pedido(pedido: dict, fortes: tuple, contrato: dict, identidade) -> dict:
    """Validacao fora do banco: identidade, tipo, fontes e confianca. Nao decide veredito."""
    problemas = []
    if not isinstance(pedido, dict):
        return {"problemas": ["PEDIDO_ILEGIVEL"], "validos": [], "declarados": [], "tipo": "",
                "fontes": [], "achados": {}, "descartados": [], "confianca": None}
    tipo = _texto(pedido.get("tipo")).upper()
    if not tipo:
        problemas.append("SEM_TIPO_DE_PESQUISA")
    elif tipo not in contrato["tipos_de_pesquisa"]:
        problemas.append("TIPO_DE_PESQUISA_DESCONHECIDO")
    fontes, problemas_fontes = validar_fontes(pedido)
    problemas.extend(problemas_fontes)
    declarados = identificadores_declarados(pedido, fortes)
    validos = identificadores_validos(pedido, fortes, identidade)
    if declarados and not validos:
        problemas.append("IDENTIFICADOR_FORTE_INVALIDO")
    elif not validos:
        problemas.append("SEM_IDENTIFICADOR_FORTE")
    achados, descartados = validar_achados(pedido, tipo, contrato) if tipo else ({}, [])
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
    return {"problemas": problemas, "validos": validos, "declarados": declarados, "tipo": tipo,
            "fontes": fontes, "achados": achados, "descartados": descartados,
            "confianca": confianca}


def decidir_veredito(problemas: list, validos: list, organizacoes_casadas: list) -> tuple:
    """Decisao pura (testavel sem banco) — a regra vive aqui e so aqui.

    A pesquisa NAO cria organizacao: identidade que nao casa e recusa, nao descoberta. Ambiguidade
    (dois ou mais casamentos distintos) para na fila humana, nunca e resolvida por heuristica
    (`dedup.rule` do contrato).
    """
    if problemas:
        return RECUSADA, problemas[0]
    if not validos:
        return RECUSADA, "SEM_IDENTIFICADOR_FORTE"
    if len(organizacoes_casadas) == 0:
        return RECUSADA, "ORGANIZACAO_NAO_ENCONTRADA"
    if len(organizacoes_casadas) == 1:
        return PESQUISADA, None
    return REVISAO, "CONFLITO_DE_IDENTIDADE_FORTE"


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

# Modos do UPDATE em `organizations` (nomes de modo, nao booleanos soltos):
#   `enriquecimento` = o caminho normal: cada coluna com COALESCE (nunca sobrescreve);
#   `restauracao`    = o --desfazer <cid> --confirmo: devolve o valor ANTERIOR gravado na rodada.
MODO_ENRIQUECIMENTO = "enriquecimento"
MODO_RESTAURACAO = "restauracao"
MODOS_ORGANIZACOES = (MODO_ENRIQUECIMENTO, MODO_RESTAURACAO)

_EXPR_TEXTO = r"^{c}\s*=\s*COALESCE\s*\(\s*NULLIF\s*\(\s*{c}\s*,\s*''\s*\)\s*,"
_EXPR_NUMERO = r"^{c}\s*=\s*COALESCE\s*\(\s*{c}\s*,"


def _sem_literais(sql: str) -> str:
    """A instrucao SEM o conteudo dos literais — so o codigo SQL.

    Nome de empresa nao e instrucao: um literal ('Drop Solucoes Ltda') faz a guarda recusar
    pesquisa legitima como se fosse DDL. Quem escreve dado e o `lit()`, que escapa apostrofo
    dobrando — entao a varredura olha o codigo, nao a prosa.
    """
    return _LITERAL.sub("''", sql)


def _itens_do_set(texto: str) -> list:
    """Separa os itens do SET por virgula de TOPO.

    Virgula dentro de parenteses NAO separa item: `industry_code = COALESCE(NULLIF(industry_code,
    ''), 'Q')` tem uma virgula dentro do NULLIF e outra depois dele — um split ingenuo partia a
    expressao em duas e a guarda recusava pesquisa legitima.
    """
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


def conferir_enriquecimento(expressao: str) -> None:
    """Cada item do SET de `organizations` tem de ser coluna declarada na forma que NAO sobrescreve.

    Guarda de desenho, nao de prosa: se alguem trocar `COALESCE(NULLIF(col,''), valor)` por
    `col = valor`, a guarda recusa e a pesquisa nao roda (medido por mutacao no autoteste).
    """
    if expressao.lower().replace(" ", "") == "updated_at=now()":
        return
    m = re.match(r"^([a-z_][a-z0-9_]*)\s*=", expressao)
    coluna = m.group(1) if m else ""
    if coluna not in COLUNAS_ENRIQUECIMENTO:
        raise GuardaDeEscritaViolada(
            "enriquecimento fora das colunas declaradas: %r" % (coluna or expressao))
    if TIPO_POR_COLUNA[coluna] == "texto":
        padrao = re.compile(_EXPR_TEXTO.format(c=re.escape(coluna)))
        if not padrao.match(expressao):
            raise GuardaDeEscritaViolada(
                "coluna de texto sem COALESCE(NULLIF(%s,''), ...): %s" % (coluna, expressao))
    else:
        padrao = re.compile(_EXPR_NUMERO.format(c=re.escape(coluna)))
        if not padrao.match(expressao):
            raise GuardaDeEscritaViolada(
                "coluna numerica sem COALESCE(%s, ...): %s" % (coluna, expressao))


def conferir_restauracao(expressao: str) -> None:
    """A restauracao devolve valor CONHECIDO (o anterior, gravado no sync_event da rodada)."""
    if expressao.lower().replace(" ", "") == "updated_at=now()":
        return
    m = re.match(r"^([a-z_][a-z0-9_]*)\s*=", expressao)
    coluna = m.group(1) if m else ""
    if coluna not in COLUNAS_ENRIQUECIMENTO:
        raise GuardaDeEscritaViolada(
            "restauracao fora das colunas declaradas: %r" % (coluna or expressao))


def validar_sql(sql: str, modo_organizacoes=None, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL e escrita fora do declarado.

    `organizations` so aceita UPDATE e so nos dois modos declarados (enriquecimento com COALESCE
    / restauracao do desfazer); DELETE em `organizations` NUNCA e' permitido — a pesquisa nao
    apaga empresa. Tabela nao declarada e recusa, mesmo em INSERT.
    """
    if modo_organizacoes is not None and modo_organizacoes not in MODOS_ORGANIZACOES:
        raise GuardaDeEscritaViolada("modo de escrita em organizations desconhecido: %r"
                                     % modo_organizacoes)
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL nao e permitido ao agente Research")
    for item in _SET.finditer(codigo):
        tabela = item.group(1).lower()
        if tabela != TABELA_ORGANIZACOES:
            continue
        if modo_organizacoes is None:
            raise GuardaDeEscritaViolada(
                "UPDATE em organizations fora do modo declarado (enriquecimento/restauracao)")
        for expressao in _itens_do_set(item.group(2) + ","):
            if modo_organizacoes == MODO_ENRIQUECIMENTO:
                conferir_enriquecimento(expressao)
            else:
                conferir_restauracao(expressao)
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            tabela = tabela.lower()
            if tabela not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela nao declarada: %s" % tabela)
            if tabela == TABELA_ORGANIZACOES and operacao != "update":
                raise GuardaDeEscritaViolada(
                    "%s em organizations nao e permitido: a pesquisa nao cria nem apaga empresa"
                    % operacao.upper())
            if tabela == TABELA_ORGANIZACOES and operacao == "update" and modo_organizacoes is None:
                raise GuardaDeEscritaViolada("UPDATE em organizations sem modo declarado")
            if operacao == "delete" and not permitir_remocao:
                raise GuardaDeEscritaViolada("DELETE fora do desfazer explicito: %s" % tabela)


# ---------------------------------------------------------------------------------------
# Porta de banco (ADR-0008: o SQL roda na VPS; a porta e o prefixo psql)
# ---------------------------------------------------------------------------------------
class PortaSQL:
    def executar(self, sql: str, modo_organizacoes=None,
                 permitir_remocao: bool = False) -> tuple:
        raise NotImplementedError


class PortaAusente(PortaSQL):
    """Sem porta configurada: qualquer tentativa de falar com o banco RECUSA."""

    def executar(self, sql: str, modo_organizacoes=None,
                 permitir_remocao: bool = False) -> tuple:
        raise PortaIndisponivel(
            "nenhuma porta de banco configurada (use --prefixo ou TRE_PSQL_PREFIXO)")


class PortaPsql(PortaSQL):
    """Executa SQL pelo prefixo psql informado (ex.: 'docker exec -i pg-sales-dev psql -U ..')."""

    def __init__(self, prefixo: str, timeout: int = 120):
        if not prefixo:
            raise PortaIndisponivel("prefixo psql vazio (use --prefixo ou TRE_PSQL_PREFIXO)")
        self.prefixo = shlex.split(prefixo)
        self.timeout = timeout

    def executar(self, sql: str, modo_organizacoes=None,
                 permitir_remocao: bool = False) -> tuple:
        validar_sql(sql, modo_organizacoes=modo_organizacoes, permitir_remocao=permitir_remocao)
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
_COLUNAS_RESEARCH_RUNS = (
    "id, organization_id, agent_name, agent_version, workflow_name, workflow_version, "
    "model_provider, model_name, prompt_version, research_type, started_at, completed_at, status, "
    "source_count, confidence, summary, structured_output, input_hash, tokens_input, tokens_output, "
    "estimated_cost, error_code, error_message, created_at"
)


def sql_consultar_organizacao(validos: list) -> str:
    """Identidade -> organizacoes casadas, em JSON (o texto da empresa nao quebra a leitura)."""
    termos = ["(%s IS NOT NULL AND %s = %s)" % (tipo, tipo, lit(valor)) for tipo, valor in validos]
    campos = ["'id'", "id::text", "'status'", "COALESCE(status, '')",
              "'legal_name'", "COALESCE(legal_name, '')", "'trade_name'", "COALESCE(trade_name, '')"]
    for coluna in COLUNAS_ENRIQUECIMENTO:
        campos.extend(["'%s'" % coluna, "COALESCE(%s::text, '')" % coluna])
    pares = ", ".join(campos)
    return (
        "SELECT jsonb_build_object(%s)::text FROM %s "
        "WHERE deleted_at IS NULL AND (%s) ORDER BY id;" % (
            pares, TABELA_ORGANIZACOES, " OR ".join(termos))
    )


def expressao_enriquecimento(coluna: str, valor) -> str:
    """A forma que NAO sobrescreve: a coluna preenchida vence o achado da pesquisa."""
    if TIPO_POR_COLUNA[coluna] == "texto":
        return "%s = COALESCE(NULLIF(%s, ''), %s)" % (coluna, coluna, lit(valor))
    return "%s = COALESCE(%s, %s)" % (coluna, coluna, lit(valor))


def montar_linha_research_run(run_id: str, organizacao_id: str, valores: dict) -> tuple:
    """Colunas x valores do research_run — a paridade e conferida contra o DDL pela suite."""
    colunas = [c.strip() for c in _COLUNAS_RESEARCH_RUNS.split(",")]
    expressoes = [
        lit(run_id),                                  # id
        lit(organizacao_id),                          # organization_id
        lit(AGENTE),                                  # agent_name
        lit(VERSAO),                                  # agent_version
        lit(WORKFLOW),                                # workflow_name
        lit(WORKFLOW_VERSAO),                         # workflow_version
        "NULL",                                       # model_provider  (v1 sem LLM)
        "NULL",                                       # model_name
        "NULL",                                       # prompt_version
        lit(valores["research_type"]),                # research_type
        lit(valores["started_at"]),                   # started_at
        lit(valores["completed_at"]),                 # completed_at
        lit(valores["status"]),                       # status
        lit(valores["source_count"]),                 # source_count
        lit(valores["confidence"]),                   # confidence
        lit(valores["summary"]),                      # summary
        lit_json(valores["structured_output"]),       # structured_output
        lit(valores["input_hash"]),                   # input_hash
        "NULL",                                       # tokens_input
        "NULL",                                       # tokens_output
        "NULL",                                       # estimated_cost
        "NULL",                                       # error_code
        "NULL",                                       # error_message
        "now()",                                      # created_at
    ]
    if len(expressoes) != len(colunas):
        raise ValueError("montagem do INSERT de research_runs divergente: %d colunas x %d valores"
                         % (len(colunas), len(expressoes)))
    return colunas, expressoes


def sql_ingerir(organizacao_id: str, run_id: str, sync_event_id: str, chave: str,
                valores: dict, colunas_enriquecidas: list, enriquecimento: dict,
                payload: dict) -> str:
    """Uma transacao, TRES comandos: claim+research_run -> enriquecimento -> fechamento com marca.

    Por que tres comandos e nao um so: as CTEs de escrita e a instrucao principal rodam no MESMO
    snapshot, entao a instrucao principal NAO enxerga a linha que a CTE acabou de inserir (defeito
    medido no aceite E2E do Scout). Aqui:
      1. `WITH claim AS (INSERT sync_events ... ON CONFLICT DO NOTHING RETURNING id)` +
         `INSERT research_runs SELECT ... FROM claim` — replay nao insere nada;
      2. `UPDATE organizations ... WHERE id = <org> AND EXISTS (SELECT 1 FROM research_runs
         WHERE id = <run>)` — o enriquecimento so acontece se o research_run DESTA rodada existe
         (replay nao reaplica nada);
      3. `UPDATE sync_events SET status='SUCCESS' ... AND EXISTS (...research_runs...) RETURNING
         'RESEARCH_PESQUISADA'` — a marca de sucesso desta rodada; ausente = replay.
    """
    colunas, expressoes = montar_linha_research_run(run_id, organizacao_id, valores)
    comandos = [
        "BEGIN;",
        "WITH claim AS (\n"
        "  INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, created_at)\n"
        "  VALUES ({sevid}, 'organization', {orgid}, 'research', 'postgresql', {operacao}, "
        "{versao}, {chave}, 'PENDING', {payload}, now())\n"
        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {runs} ({cols})\n"
        "SELECT {vals} FROM claim\n"
        "ON CONFLICT (id) DO NOTHING\n"
        "RETURNING id;".format(
            sync=TABELA_SYNC_EVENTS, runs=TABELA_RESEARCH_RUNS, sevid=lit(sync_event_id),
            orgid=lit(organizacao_id), operacao=lit(OPERACAO_SYNC), versao=lit(VERSAO),
            chave=lit(chave), payload=lit_json(payload), cols=", ".join(colunas),
            vals=", ".join(expressoes)),
    ]
    if colunas_enriquecidas:
        itens = [expressao_enriquecimento(c, enriquecimento["depois"][c])
                 for c in colunas_enriquecidas]
        itens.append("updated_at = now()")
        comandos.append(
            "UPDATE {orgs} SET {itens}\n"
            "WHERE id = {orgid} AND EXISTS (SELECT 1 FROM {runs} WHERE id = {runid})\n"
            "RETURNING id;".format(orgs=TABELA_ORGANIZACOES, itens=", ".join(itens),
                                   orgid=lit(organizacao_id), runs=TABELA_RESEARCH_RUNS,
                                   runid=lit(run_id)))
    comandos.append(
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(),\n"
        "  response_payload = jsonb_build_object('research_run_id', {runid}, 'veredito', "
        "'PESQUISADA', 'idempotency_key', {chave})\n"
        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {runs} WHERE id = {runid})\n"
        "RETURNING {marca};".format(sync=TABELA_SYNC_EVENTS, runs=TABELA_RESEARCH_RUNS,
                                    runid=lit(run_id), chave=lit(chave),
                                    marca=lit(MARCA_PESQUISADA)))
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


def sql_pedir_revisao(approval_id: str, pedido: dict, motivo: str, casadas: list,
                      correlation_id: str) -> str:
    proposta = {
        "motivo": motivo,
        "agente": "%s/%s" % (AGENTE, VERSAO),
        "correlation_id": correlation_id,
        "pedido": pedido,
        "organizacoes_casadas": casadas,
        "regra": "ambiguidade nao e resolvida por heuristica: e reportada (dedup.rule do contrato)",
    }
    return (
        "INSERT INTO {tabela} (id, action_type, entity_type, entity_id, requested_by, "
        "proposed_action, status, requested_at)\n"
        "VALUES ({aid}, {acao}, 'organization', NULL, {quem}, {proposta}, 'PENDING', now());\n"
    ).format(tabela=TABELA_APPROVALS, aid=lit(approval_id), acao=lit(ACTION_TYPE_REVISAO),
             quem=lit("%s/%s" % (AGENTE, VERSAO)), proposta=lit_json(proposta))


def sql_selecionar_da_rodada(correlation_id: str) -> str:
    """O que a rodada criou: research_runs (veredito PESQUISADA) + os valores ANTES/DEPOIS."""
    return (
        "SELECT jsonb_build_object(\n"
        "  'research_run_id', r.id::text,\n"
        "  'organization_id', r.organization_id::text,\n"
        "  'sync_event_id', s.id::text,\n"
        "  'antes', s.request_payload -> 'antes',\n"
        "  'depois', s.request_payload -> 'depois'\n"
        ")::text\n"
        "FROM {runs} r\n"
        "JOIN {sync} s ON (s.request_payload ->> 'research_run_id') = r.id::text\n"
        "  AND s.operation = {operacao}\n"
        "JOIN {audit} a ON (a.output ->> 'research_run_id') = r.id::text\n"
        "WHERE a.correlation_id = {corr} AND a.agent_name = {agente}\n"
        "  AND a.output ->> 'veredito' = {veredito}\n"
        "ORDER BY r.id;\n"
    ).format(runs=TABELA_RESEARCH_RUNS, sync=TABELA_SYNC_EVENTS, audit=TABELA_AGENT_RUNS,
             operacao=lit(OPERACAO_SYNC), corr=lit(correlation_id), agente=lit(AGENTE),
             veredito=lit(PESQUISADA))


def lit_restauracao(coluna: str, valor) -> str:
    """O valor ANTERIOR volta com o tipo da coluna (nao como texto entre apostrofos).

    `employee_count = '85'` funciona por conversao implicita, mas restauracao nao e lugar de
    confiar em conversao: o valor anterior sai com o tipo que a coluna declara — e valor ilegivel
    para o tipo e recusa (fail-closed), nao escrita torta.
    """
    if valor in (None, ""):
        return "NULL"
    tipo = TIPO_POR_COLUNA.get(coluna)
    if tipo == "inteiro":
        try:
            return repr(int(str(valor).strip()))
        except (TypeError, ValueError):
            raise GuardaDeEscritaViolada("valor anterior nao e inteiro: %r" % valor)
    if tipo == "numero":
        try:
            return repr(round(float(str(valor).strip()), 2))
        except (TypeError, ValueError):
            raise GuardaDeEscritaViolada("valor anterior nao e numero: %r" % valor)
    return lit(valor)


def sql_desfazer(itens: list, correlation_id: str, sync_event_id: str) -> str:
    """Devolve os valores anteriores e apaga o que a rodada criou. Nada de DELETE em empresa."""
    comandos = ["BEGIN;"]
    for item in itens:
        colunas = [c for c in item.get("depois", {}) if item.get("depois", {}).get(c) is not None]
        if not colunas:
            continue
        itens_set = ["%s = %s" % (c, lit_restauracao(c, item["antes"].get(c))) for c in colunas]
        itens_set.append("updated_at = now()")
        comandos.append(
            "UPDATE {orgs} SET {itens} WHERE id = {orgid} AND deleted_at IS NULL "
            "RETURNING id;".format(orgs=TABELA_ORGANIZACOES, itens=", ".join(itens_set),
                                   orgid=lit(item["organization_id"])))
    ids_runs = ", ".join(lit(i["research_run_id"]) for i in itens)
    ids_sync = ", ".join(lit(i["sync_event_id"]) for i in itens if i.get("sync_event_id"))
    if ids_sync:
        comandos.append("DELETE FROM {sync} WHERE id IN ({ids});".format(
            sync=TABELA_SYNC_EVENTS, ids=ids_sync))
    if ids_runs:
        comandos.append("DELETE FROM {runs} WHERE id IN ({ids});".format(
            runs=TABELA_RESEARCH_RUNS, ids=ids_runs))
    payload = {"motivo": "desfazer da rodada de pesquisa", "correlation_id": correlation_id,
               "research_runs": [i["research_run_id"] for i in itens]}
    comandos.append(
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, operation, "
        "source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'organization', NULL, 'research', 'postgresql', 'ROLLBACK', {versao}, "
        "{chave}, 'SUCCESS', {payload}, now());".format(
            sync=TABELA_SYNC_EVENTS, sevid=lit(sync_event_id), versao=lit(VERSAO),
            chave=lit("research:rollback:%s" % correlation_id), payload=lit_json(payload)))
    comandos.append("COMMIT;")
    return "\n".join(comandos) + "\n"


# ---------------------------------------------------------------------------------------
# Gate do JEV (doc 07 §7: antes de chamada de LLM em tarefa elegivel, o Hermes consulta o JEV)
# ---------------------------------------------------------------------------------------
def validar_recibo_jev(recibo) -> dict:
    if not recibo:
        raise ReciboJEVInvalido("sem recibo de decisao do JEV: abstencao e recusa, nao permissao")
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
    """v1 NAO executa LLM — o gate existe e e fail-closed. Sem recibo valido, nao passa."""
    recibo = validar_recibo_jev(recibo)
    return {
        "autorizado": True,
        "decision_id": recibo["decision_id"],
        "lane": recibo["lane"],
        "provedor": provedor,
        "modelo": modelo,
        "prompt_sha256": _sha256(prompt),
        "executado": False,
        "motivo": "v1 do Research e deterministica: nenhuma chamada de LLM e feita",
    }


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------------------
class Research:
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
                "Research v1 nao escreve em prod (ADR-005): a promocao exige card proprio com "
                "aprovacao humana registrada")
        if self.ambiente not in AMBIENTES_PERMITIDOS:
            raise RecusaDeAmbiente("ambiente desconhecido: %r" % self.ambiente)
        return self.ambiente

    def identificar_alvo(self) -> dict:
        """Registra a identidade do alvo medido (sem afirmar nome de ambiente — a convencao de
        nome divergiu no dev e alinhar isso e decisao do dono, nao do agente)."""
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
    def organizacoes_casadas(self, validos: list) -> list:
        if not validos:
            return []
        rc, saida, erro = self.porta.executar(sql_consultar_organizacao(validos))
        if rc != 0:
            raise PortaIndisponivel("consulta de organizacao falhou: %s" % (erro or saida))
        casadas = []
        for linha in saida.splitlines():
            linha = linha.strip()
            if not linha or linha in ("BEGIN", "COMMIT"):
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if not registro.get("id"):
                continue
            casadas.append(registro)
        return casadas

    def colunas_a_enriquecer(self, registro: dict, aceitos: dict) -> tuple:
        """Separa o que sera escrito do que a organizacao JA sabia (nunca sobrescreve)."""
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
        if validacao["problemas"]:
            veredito = PLANEJADO_RECUSAR
        else:
            veredito = PLANEJADO_PESQUISAR
        identidades = validacao["validos"]
        return {"veredito": veredito, "motivos": validacao["problemas"], "tipo": validacao["tipo"],
                "identidades_validas": [{"tipo": t, "valor": v} for t, v in identidades],
                "identidades_declaradas": validacao["declarados"],
                "fontes": validacao["fontes"], "achados_aceitos": validacao["achados"],
                "descartados": validacao["descartados"],
                "colunas_do_tipo": colunas_do_tipo(validacao["tipo"], self.contrato),
                "input_hash": hash_da_entrada(identidades, validacao["tipo"],
                                              validacao["achados"], validacao["fontes"])
                if identidades and validacao["tipo"] else None}

    def processar(self, pedido: dict) -> dict:
        inicio = self.relogio()
        entrada = {"pedido": pedido, "ambiente": self.ambiente,
                   "correlation_id": self.correlation_id}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": ERRO, "motivos": [], "organization_id": None,
                     "research_run_id": None, "idempotency_key": None, "tipo": None,
                     "fontes": [], "colunas_enriquecidas": [], "colunas_preservadas": [],
                     "descartados": [], "achados_aceitos": {}}
        try:
            validacao = validar_pedido(pedido, self.fortes, self.contrato, self.identidade)
            resultado["tipo"] = validacao["tipo"]
            resultado["fontes"] = validacao["fontes"]
            resultado["descartados"] = validacao["descartados"]
            resultado["achados_aceitos"] = validacao["achados"]
            resultado["identidades_validas"] = [{"tipo": t, "valor": v}
                                                for t, v in validacao["validos"]]
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
                rc_rev, saida_rev, erro_rev = self.porta.executar(
                    sql_pedir_revisao(aprovacao_id, pedido, resultado["motivos"][0],
                                      [{"organization_id": c["id"]} for c in casadas],
                                      self.correlation_id))
                if rc_rev != 0:
                    # Fail-closed: sem a linha em human_approvals a ambiguidade NAO foi
                    # reportada — entao nao se pode dizer REVISAO_IDENTIDADE.
                    raise PortaIndisponivel("fila humana nao registrada: %s"
                                            % (erro_rev or saida_rev))
                resultado["human_approval_id"] = aprovacao_id
            elif veredito == PESQUISADA:
                registro = casadas[0]
                organizacao_id = registro["id"]
                a_enriquecer, preservadas = self.colunas_a_enriquecer(registro,
                                                                     validacao["achados"])
                entrada_hash = hash_da_entrada(validacao["validos"], validacao["tipo"],
                                               validacao["achados"], validacao["fontes"])
                chave = chave_idempotencia(organizacao_id, validacao["tipo"], entrada_hash)
                research_run_id = str(uuid.uuid4())
                sync_event_id = str(uuid.uuid4())
                antes = {c: (registro.get(c) if registro.get(c) not in ("",) else None)
                         for c in COLUNAS_ENRIQUECIMENTO}
                depois = {c: (validacao["achados"][c] if c in a_enriquecer else None)
                          for c in COLUNAS_ENRIQUECIMENTO}
                payload = {"origem": AGENTE, "agente": "%s/%s" % (AGENTE, VERSAO),
                           "correlation_id": self.correlation_id,
                           "research_run_id": research_run_id, "research_type": validacao["tipo"],
                           "fontes": validacao["fontes"], "input_hash": entrada_hash,
                           "idempotency_key": chave, "antes": antes, "depois": depois,
                           "identidades": [{"tipo": t, "valor": v}
                                           for t, v in validacao["validos"]]}
                estruturado = {
                    "correlation_id": self.correlation_id,
                    "tipo_de_pesquisa": validacao["tipo"],
                    "fontes": validacao["fontes"],
                    "achados_aceitos": validacao["achados"],
                    "achados_descartados": validacao["descartados"],
                    "colunas_enriquecidas": a_enriquecer,
                    "colunas_preservadas": preservadas,
                    "organizacao_status": registro.get("status"),
                    "identidade_do_produtor": {"agente": "scout",
                                               "identidade": resultado["identidades_validas"]},
                    "deterministico": True,
                    "llm": {"executado": False,
                            "motivo": "v1 do Research e deterministica (o gate do JEV existe e e "
                                      "fail-closed)"},
                    "regra_de_enriquecimento": "COALESCE: a coluna JA preenchida nunca e "
                                               "sobrescrita; identificador forte nunca e escrito",
                }
                valores = {
                    "research_type": validacao["tipo"],
                    "started_at": inicio,
                    "completed_at": self.relogio(),
                    "status": STATUS_RESEARCH_RUN["CONCLUIDA"],
                    "source_count": len(validacao["fontes"]),
                    "confidence": validacao["confianca"],
                    "summary": resumo_da_pesquisa(validacao, a_enriquecer, preservadas),
                    "structured_output": estruturado,
                    "input_hash": entrada_hash,
                }
                enriquecimento = {"antes": antes, "depois": depois}
                rc, saida, erro = self.porta.executar(
                    sql_ingerir(organizacao_id, research_run_id, sync_event_id, chave, valores,
                                a_enriquecer, enriquecimento, payload),
                    modo_organizacoes=MODO_ENRIQUECIMENTO if a_enriquecer else None)
                if rc != 0:
                    raise PortaIndisponivel("pesquisa falhou: %s" % (erro or saida))
                marcas = [l.strip() for l in saida.splitlines()]
                resultado["organization_id"] = organizacao_id
                resultado["idempotency_key"] = chave
                resultado["colunas_enriquecidas"] = a_enriquecer
                resultado["colunas_preservadas"] = preservadas
                if MARCA_PESQUISADA in marcas:
                    resultado["research_run_id"] = research_run_id
                    resultado["sync_event_id"] = sync_event_id
                else:
                    # a chave ja existia: nada foi duplicado (retry idempotente)
                    resultado["veredito"] = JA_PESQUISADO
                    resultado["motivos"] = resultado["motivos"] + ["IDEMPOTENCIA_REPLAY"]
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        saida_auditoria = {k: v for k, v in resultado.items() if k != "achados_aceitos"}
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
        rc, saida, erro = self.porta.executar(sql_selecionar_da_rodada(correlation_id))
        if rc != 0:
            raise PortaIndisponivel("nao consegui listar a rodada: %s" % (erro or saida))
        itens = []
        for linha in saida.splitlines():
            linha = linha.strip()
            if not linha or linha in ("BEGIN", "COMMIT"):
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if registro.get("research_run_id"):
                itens.append(registro)
        if not confirmo:
            return {"correlation_id": correlation_id, "confirmo": False,
                    "research_runs": [i["research_run_id"] for i in itens],
                    "organizacoes": sorted({i["organization_id"] for i in itens}),
                    "restauradas": 0, "apagadas": 0, "dry_run": True}
        if itens:
            rc, saida, erro = self.porta.executar(
                sql_desfazer(itens, correlation_id, str(uuid.uuid4())),
                modo_organizacoes=MODO_RESTAURACAO, permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
        return {"correlation_id": correlation_id, "confirmo": True,
                "research_runs": [i["research_run_id"] for i in itens],
                "organizacoes": sorted({i["organization_id"] for i in itens}),
                "restauradas": len({i["organization_id"] for i in itens}),
                "apagadas": len(itens), "dry_run": False}


def resumo_da_pesquisa(validacao: dict, enriquecidas: list, preservadas: list) -> str:
    """Resumo determinístico: nenhuma frase e inferencia, e o que nao foi escrito esta declarado."""
    return (
        "Pesquisa %s (deterministica, sem LLM): %d fonte(s); %d campo(s) aceito(s); "
        "%d coluna(s) enriquecida(s) [%s]; %d coluna(s) preservada(s) [%s]; "
        "%d achado(s) descartado(s)."
        % (validacao["tipo"], len(validacao["fontes"]), len(validacao["achados"]),
           len(enriquecidas), ", ".join(enriquecidas) or "-",
           len(preservadas), ", ".join(preservadas) or "-",
           len(validacao["descartados"]))
    )


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_pesquisas(caminho: str) -> list:
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
            raise SystemExit("FALHOU linha %d da fonte nao e JSON: %s" % (numero, exc))
    return pedidos


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agente Research v1 (TRE-W4-E02-T01)")
    p.add_argument("--fonte", help="arquivo jsonl com os pedidos de pesquisa ('-' = stdin)")
    p.add_argument("--ambiente", help="dev | homolog (prod e recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true", help="valida e planeja; NAO toca o banco")
    p.add_argument("--relatorio", help="caminho do relatorio JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrao e dry-run)")
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
    agente = Research(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                      correlation_id=args.correlation_id)
    # O ambiente e exigido em todo modo que ESCREVE. No modo de planejamento (que nao abre conexao
    # nenhuma) ele e opcional: se declarado, e conferido; se ausente, nada e escrito.
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
            pedidos = ler_pesquisas(args.fonte)
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
                      "apagadas": relatorio.get("apagadas"), "dry_run": relatorio.get("dry_run")},
                     ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-20s %-20s %s" % (r["veredito"], (r.get("tipo") or "-"),
                                    ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
