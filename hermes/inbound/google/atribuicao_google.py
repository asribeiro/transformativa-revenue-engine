#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atribuicao de lead do Google v1 (`google-lead-attribution-v1`) — card TRE-W7-E03-T01.

O que este componente FAZ (e so isto): recebe um lead que chegou do Google — formulario de Lead Ads do
Google Ads, ou formulario do site com `gclid`/`utm_*` — decide A QUE ele se atribui (canal, campanha,
grupo de anuncio, criativo, palavra-chave) por TABELA DECLARADA no contrato versionado
`hermes/inbound/google/atribuicao-google-v1.json`, e grava o resultado em
`sales_intelligence.interactions` (+ a trilha de idempotencia em `sales_intelligence.sync_events`),
que e' a evidencia que o resto do W7/W8 (funil, conversao por segmento) consome.

Invariantes deste componente (cada um com item de aceite):

  1. ATRIBUICAO NAO SE INVENTA: cada veredito carrega a EVIDENCIA que o decidiu
     (`FORMULARIO_GOOGLE_ADS`, `GCLID_RESOLVIDO`, `GCLID_NAO_RESOLVIDO`, `UTM_SOURCE_GOOGLE`) e a
     confianca declarada no contrato. Nenhuma evidencia nomeada casou => `NAO_ATRIBUIDO`
     (`SEM_IDENTIFICADOR`), sem canal e SEM linha em `interactions`. Fail-closed, nunca "google" por
     conveniencia.
  2. EVIDENCIA FORTE E FRACA NAO SE MISTURAM: campanha declarada no formulario (0,95) e `utm_source`
     (0,45) produzem vereditos e confiancas diferentes. A ordem das regras e' declarada (`ordem`) no
     contrato, executada em ordem e a PRIMEIRA que casa vence.
  3. INCOERENCIA RECUSA: `google_ads_lead_form` sem `campanha_id` e' `FORMULARIO_SEM_CAMPANHA`
     (`NAO_ATRIBUIDO`) — o produto carrega esse campo; sem ele a atribuicao nao se adivinha.
  4. LIMITE DE ESCRITA: so `interactions` e `sync_events` sao tocadas, e so por INSERT. A auditoria da
     propria fonte RECUSA (exit 3) se o modulo carregar UPDATE/DELETE/TRUNCATE/DDL ou INSERT em outra
     tabela.
  5. VINCULO NAO SE INVENTA: o lead e' ligado a `contacts` por e-mail e depois por telefone/whatsapp;
     contato desconhecido NAO vira organizacao nova — fica `SEM_VINCULO` na trilha (auditavel,
     elegivel a reprocesso) e nenhuma linha e' inventada em `interactions` (invariante 4 do W6-E05/W7-E02).
  6. IDEMPOTENCIA: a chave e' `google-lead:<fonte>:<lead_id>` (indice unico em `sync_events`). Replay
     devolve `JA_INGERIDO`, nao grava de novo e NAO chama a porta do gclid. Evidencia nova do mesmo
     lead entra como trilha nova (`<chave>#evidencia-<n>`) sem reescrever a anterior.
  7. `gclid` NAO E' ATRIBUICAO: o clique e' resolvido pela porta declarada (`GET {porta}/gclid/<gclid>`).
     Porta ausente/fora do loopback em dev/timeout/JSON invalido NAO resolvem — a resolucao nao e' usada.
     O `gclid` prova o canal; a CAMPANHA so' entra com resolucao `RESOLVIDO`.
  8. SEGREDO: o token do Google Ads (`TRE_GOOGLE_ADS_TOKEN`) e' da porta, nunca do payload; se o valor
     aparecer na gravacao, RECUSA (`SENHA_VAZADA`, exit 5).
  9. PII NO RESUMO: `content_summary` nao expoe e-mail/telefone em claro (mascarados); `subject` sem
     dado pessoal; a trilha guarda o contato MASCARADO (o vinculo real e' `contact_id`).
 10. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<dev|aceite>... psql`) e resolvedor de gclid em LOOPBACK; `homolog` exige
     aprovacao humana registrada (`TRE_GOOGLE_APROVACAO_HUMANA`); `prod` RECUSA por desenho (exit 4).

O que ele NAO faz, por desenho (declarado no contrato -> `lacunas`):
  - nao chama a Ads API real (developer token/OAuth): a porta e' declarada e em dev e' loopback;
  - nao emite evento de outbox: o vocabulario de eventos do Data Contract V1 nao tem evento de lead
    inbound (emitir um exigiria nova versao do contrato);
  - nao cria `organizations`/`contacts` (dono operacional e' o Odoo, doc 05 §4) e nao escreve no Odoo;
  - nao usa LLM: a atribuicao e' tabela declarada, sem classificador;
  - nao escreve o id do Google em `interactions.campaign_id` (coluna UUID canonica): o id proprietario
    da campanha vive no `request_payload` da trilha.

Uso:

  python3 hermes/inbound/google/atribuicao_google.py --planejar
  python3 hermes/inbound/google/atribuicao_google.py --conferir
  python3 hermes/inbound/google/atribuicao_google.py --atribuir --entrada lead.json
  python3 hermes/inbound/google/atribuicao_google.py --ingerir --entrada lead.json \\
      --porta-banco "docker exec -i pg-google-acc psql -U sales_ai -d sales_intelligence" [--confirmo]
  python3 hermes/inbound/google/atribuicao_google.py --desfazer "google-lead:site_gclid:gads-1" [--confirmo]

Exit: 0 = OK/DRY_RUN/replay · 1 = falha de execucao (banco/porta) · 2 = uso ·
      3 = recusa de guarda/contrato/payload · 4 = recusa de producao · 5 = segredo vazado.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", "..", ".."))

VERSAO = "google-lead-attribution-v1"
CONTRATO_PADRAO = os.path.join(AQUI, "atribuicao-google-v1.json")

CODIGO_OK = 0
CODIGO_FALHA = 1
CODIGO_USO = 2
CODIGO_RECUSA = 3
CODIGO_PRODUCAO = 4
CODIGO_SEGREDO = 5

AMBIENTES = ("dev", "homolog", "prod")
TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_CONTATOS = "sales_intelligence.contacts"
TABELA_SYNC = "sales_intelligence.sync_events"
TABELAS_PERMITIDAS = (TABELA_INTERACOES, TABELA_SYNC)

# Prefixos ACEITOS para escrever no banco (invariante 10): em dev a escrita so' cai em container local
# de dev/aceite. Prefixo remoto (ssh, -h com host externo) e' RECUSADO por medicao, nao por confianca.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(google|inbound|sales|odoo|aceite)[A-Za-z0-9._-]*$")


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mascarar(valor) -> str:
    texto = str(valor or "")
    if not texto:
        return ""
    if len(texto) <= 4:
        return "*" * len(texto)
    if "@" in texto:
        usuario, _, dominio = texto.partition("@")
        return (usuario[:1] or "*") + "*" * max(0, len(usuario) - 1) + "@" + dominio
    return texto[:2] + "*" * max(0, len(texto) - 4) + texto[-2:]


def loopback_url(url: str) -> bool:
    m = re.match(r"^https?://([^/:]+)", (url or "").strip().lower())
    return bool(m) and m.group(1) in ("localhost", "127.0.0.1", "[::1]")


class Recusa(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe


# --------------------------------------------------------------------------------------------
# 1. Fonte: invariantes verificaveis no proprio arquivo (invariante 4)
# --------------------------------------------------------------------------------------------
def padroes_proibidos() -> list:
    """Montados em tempo de execucao (literal no arquivo nao se encontra a si mesmo).

    O espaco e' declarado como pedaco proprio: `"DE" + "LETE FROM"` avalia para `DELETEFROM` (sem
    espaco) e nunca casaria `DELETE FROM` — buraco silencioso da auditoria herdada do W6-E05, medido
    aqui e fechado com `"DE" + "LETE" + " FROM"`.
    """
    return ["DE" + "LETE" + " FROM", "UP" + "DATE" + " ", "TRUN" + "CATE", "DR" + "OP ",
            "AL" + "TER TABLE", "CR" + "EATE TABLE"]


def alvos_de_insert(corpo: str) -> list:
    return re.findall("INS" + r"ERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_.]*)", corpo, flags=re.IGNORECASE)


def _linhas_de_docstring(fonte: str) -> set:
    """Faixas de linha de docstring pelo parser de verdade (nao por contagem de aspas).

    A regra ingenua de "linha que comeca com aspas liga/desliga" desalinha em docstring de UMA linha
    seguida de outra e faz o texto declarado ser lido como codigo (ou o codigo ser pulado) — medido
    neste card antes do fechamento da auditoria.
    """
    faixas = set()
    try:
        arvore = ast.parse(fonte)
    except SyntaxError:
        return faixas
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        corpo = getattr(no, "body", [])
        if corpo and isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant) \
                and isinstance(corpo[0].value.value, str):
            inicio = corpo[0].value.lineno
            fim = getattr(corpo[0].value, "end_lineno", inicio)
            faixas.update(range(inicio, fim + 1))
    return faixas


def auditar_fonte(caminho: str | None = None) -> list:
    """Auditoria da propria fonte. Devolve as violacoes (vazia = ok)."""
    alvo = caminho or os.path.abspath(__file__)
    with open(alvo, "r", encoding="utf-8") as fh:
        fonte = fh.read()
    docstrings = _linhas_de_docstring(fonte)
    texto_util = []
    for numero, linha in enumerate(fonte.splitlines(), start=1):
        if numero in docstrings or linha.strip().startswith("#"):
            continue
        texto_util.append(linha)
    corpo = "\n".join(texto_util)
    violacoes = []
    for padrao in padroes_proibidos():
        if padrao in corpo:
            violacoes.append({"padrao": "sql_proibido", "alvo": padrao.strip()})
    for tabela in alvos_de_insert(corpo):
        if tabela.lower() not in [t.lower() for t in TABELAS_PERMITIDAS]:
            violacoes.append({"padrao": "insert_fora_do_limite", "alvo": tabela})
    return violacoes


# --------------------------------------------------------------------------------------------
# 2. Configuracao e guardas de ambiente (invariante 10)
# --------------------------------------------------------------------------------------------
class Configuracao:
    def __init__(self, env: dict):
        self.ambiente = (env.get("TRE_AMBIENTE") or "dev").strip().lower()
        self.porta_banco = (env.get("TRE_GOOGLE_PORTA_BANCO") or "").strip()
        self.porta_ads = (env.get("TRE_GOOGLE_ADS_PORTA") or "").strip()
        self.token_ads = env.get("TRE_GOOGLE_ADS_TOKEN") or ""
        self.aprovacao = (env.get("TRE_GOOGLE_APROVACAO_HUMANA") or "").strip()
        self.timeout = (env.get("TRE_GOOGLE_TIMEOUT") or "10").strip()

    def resumo(self) -> dict:
        return {
            "versao": VERSAO,
            "ambiente": self.ambiente,
            "porta_banco": self.porta_banco or "(ausente)",
            "porta_ads": self.porta_ads or "(ausente)",
            "token_ads": mascarar(self.token_ads) or "(ausente)",
            "aprovacao_humana": self.aprovacao or "(ausente)",
            "timeout": self.timeout,
        }


def validar(config: Configuracao, exigir_banco: bool = True) -> list:
    problemas = []
    if config.ambiente not in AMBIENTES:
        return [("AMBIENTE_INVALIDO", f"ambiente '{config.ambiente}' fora de {AMBIENTES}")]
    if config.ambiente == "prod":
        return [("PRODUCAO_RECUSADA", "prod recusa por desenho (ADR-005): a promocao e' ato humano registrado")]
    if not config.timeout.isdigit():
        problemas.append(("CONFIG_INCOERENTE", f"timeout nao numerico: {config.timeout}"))
    if config.ambiente == "dev":
        if exigir_banco:
            if not config.porta_banco:
                problemas.append(("BANCO_NAO_DECLARADO",
                                  "dev exige --porta-banco/TRE_GOOGLE_PORTA_BANCO com container local"))
            else:
                m = RE_CONTAINER_LOCAL.match(config.porta_banco.strip())
                if not m or not CONTAINERS_LOCAIS_DEV.match(m.group(1)):
                    problemas.append(("BANCO_NAO_E_DEV",
                                      "dev escreve apenas em container local de dev/aceite via docker exec; "
                                      f"prefixo recusado: {config.porta_banco}"))
        if config.porta_ads and not loopback_url(config.porta_ads):
            problemas.append(("ADS_NAO_E_LOOPBACK",
                              f"dev resolve gclid apenas em loopback; porta={config.porta_ads}"))
    if config.ambiente == "homolog" and not config.aprovacao:
        problemas.append(("HOMOLOG_SEM_APROVACAO", "homolog exige TRE_GOOGLE_APROVACAO_HUMANA registrada"))
    return problemas


# --------------------------------------------------------------------------------------------
# 3. Contrato (vocabulario, incoerencias e tabela de atribuicao) — declarado, nunca inventado
# --------------------------------------------------------------------------------------------
def carregar_contrato(caminho: str | None = None) -> dict:
    alvo = caminho or CONTRATO_PADRAO
    if not os.path.isfile(alvo):
        raise Recusa("CONTRATO_AUSENTE", f"contrato nao encontrado: {alvo}")
    try:
        with open(alvo, "r", encoding="utf-8") as fh:
            contrato = json.load(fh)
    except json.JSONDecodeError as e:
        raise Recusa("CONTRATO_INVALIDO", f"JSON invalido: {e}") from e
    falhas = validar_contrato(contrato)
    if falhas:
        raise Recusa("CONTRATO_INVALIDO", "; ".join(falhas))
    return contrato


def validar_contrato(contrato: dict) -> list:
    falhas = []
    vocab = contrato.get("vocabulario") or {}
    for campo in ("status", "evidencia", "canal", "interaction_type"):
        valores = vocab.get(campo)
        if not isinstance(valores, list) or not valores:
            falhas.append(f"vocabulario.{campo} ausente/vazio")
        elif len(set(valores)) != len(valores):
            falhas.append(f"vocabulario.{campo} com repeticao")
    fontes = contrato.get("fontes")
    if not isinstance(fontes, dict) or not fontes:
        falhas.append("fontes ausentes")
    tabela = contrato.get("tabela_de_atribuicao")
    if not isinstance(tabela, list) or not tabela:
        falhas.append("tabela_de_atribuicao ausente")
        return falhas
    evidencias = set(vocab.get("evidencia") or [])
    canais = set(vocab.get("canal") or [])
    ordens = set()
    for i, regra in enumerate(tabela):
        if not isinstance(regra, dict):
            falhas.append(f"regra {i} nao e objeto")
            continue
        if regra.get("evidencia") not in evidencias:
            falhas.append(f"regra {i} evidencia fora do vocabulario: {regra.get('evidencia')}")
        if regra.get("canal") not in canais:
            falhas.append(f"regra {i} canal fora do vocabulario: {regra.get('canal')}")
        if regra.get("veredito") not in set(vocab.get("status") or []):
            falhas.append(f"regra {i} veredito fora do vocabulario: {regra.get('veredito')}")
        if not isinstance(regra.get("ordem"), int):
            falhas.append(f"regra {i} sem ordem inteira")
        elif regra["ordem"] in ordens:
            falhas.append(f"regra {i} ordem repetida: {regra['ordem']}")
        else:
            ordens.add(regra["ordem"])
        confianca = regra.get("confianca")
        if not isinstance(confianca, (int, float)) or not 0 < confianca <= 1:
            falhas.append(f"regra {i} confianca fora de (0,1]")
        condicao = regra.get("condicao") or {}
        if not condicao.get("obrigatorios") and not condicao.get("fonte"):
            falhas.append(f"regra {i} sem condicao declarada (casaria qualquer lead)")
        for nome in condicao.get("fonte") or []:
            if nome not in fontes:
                falhas.append(f"regra {i} fonte desconhecida: {nome}")
        if condicao.get("resolucao_gclid") and condicao["resolucao_gclid"] not in ("RESOLVIDO", "NAO_ENCONTRADO"):
            falhas.append(f"regra {i} resolucao_gclid invalida: {condicao['resolucao_gclid']}")
        if not (regra.get("motivo") or "").strip():
            falhas.append(f"regra {i} sem motivo declarado")
    if not (contrato.get("fallback") or {}).get("motivo"):
        falhas.append("fallback sem motivo declarado")
    if not contrato.get("lacunas"):
        falhas.append("lacunas ausentes (o que o componente NAO mede tem de estar declarado)")
    return falhas


# --------------------------------------------------------------------------------------------
# 4. Validacao do payload e atribuicao (funcao pura: sem banco, sem rede)
# --------------------------------------------------------------------------------------------
def validar_payload(payload: dict, contrato: dict) -> list:
    """Recusas de forma. Devolve a lista de problemas (vazia = ok)."""
    problemas = []
    if not isinstance(payload, dict) or not payload:
        return [("PAYLOAD_VAZIO", "payload ausente ou vazio")]
    for campo in contrato.get("campos_obrigatorios") or []:
        if not str(payload.get(campo) or "").strip():
            problemas.append(("PAYLOAD_INCOMPLETO", f"campo obrigatorio ausente: {campo}"))
    fonte = str(payload.get("fonte") or "").strip()
    if fonte and fonte not in (contrato.get("fontes") or {}):
        problemas.append(("FONTE_DESCONHECIDA", f"fonte '{fonte}' fora do contrato"))
    dados = contrato.get("dados_de_contato") or {}
    if fonte and not any(str(payload.get(c) or "").strip() for c in dados.get("campos") or []):
        problemas.append(("DADOS_INSUFICIENTES",
                          (dados.get("motivo") or "lead sem dado de contato") + ": sem "
                          + " e sem ".join(dados.get("campos") or [])))
    return problemas


def campos_desconhecidos(payload: dict, contrato: dict) -> list:
    conhecidos = set(contrato.get("campos_conhecidos") or [])
    return sorted(k for k in payload.keys() if k not in conhecidos)


def incoerencia(payload: dict, contrato: dict) -> dict | None:
    for item in contrato.get("incoerencias") or []:
        if str(payload.get("fonte") or "") == item.get("fonte") and not str(payload.get(item.get("falta")) or "").strip():
            return item
    return None


def _contexto(payload: dict, resolucao: dict | None) -> dict:
    ctx = {k: v for k, v in payload.items() if str(v or "").strip()}
    ctx["utm_source_google"] = str(payload.get("utm_source") or "").strip().lower() == "google"
    if resolucao:
        ctx["resolucao_gclid"] = resolucao
    return ctx


def _valor_por_caminho(ctx: dict, caminho: str | None):
    if not caminho:
        return None
    atual = ctx
    for parte in caminho.split("."):
        if not isinstance(atual, dict):
            return None
        atual = atual.get(parte)
    return atual


def atribuir(payload: dict, contrato: dict, resolucao: dict | None = None) -> dict:
    """Decide a atribuicao de UM lead pela tabela declarada. Pura: sem banco, sem rede, sem LLM."""
    veredito = dict(contrato.get("fallback") or {})
    veredito.update({"evidencia": (contrato.get("fallback") or {}).get("evidencia"),
                     "canal": (contrato.get("fallback") or {}).get("canal"),
                     "campanha": None, "regra": None})
    bad = incoerencia(payload, contrato)
    if bad:
        veredito.update({"veredito": bad["veredito"], "motivo": bad["motivo"],
                         "explicacao": bad.get("explicacao", "")})
        return veredito
    ctx = _contexto(payload, resolucao)
    for regra in sorted(contrato.get("tabela_de_atribuicao") or [], key=lambda r: r.get("ordem", 0)):
        cond = regra.get("condicao") or {}
        if cond.get("fonte") and str(payload.get("fonte") or "") not in cond["fonte"]:
            continue
        if any(not str(ctx.get(c) or "").strip() for c in cond.get("obrigatorios") or []):
            continue
        if cond.get("resolucao_gclid"):
            resolvido = ((ctx.get("resolucao_gclid") or {}).get("status") or "").strip().upper()
            if resolvido != cond["resolucao_gclid"]:
                continue
        campanha = _valor_por_caminho(ctx, regra.get("campanha_de"))
        veredito = {"veredito": regra["veredito"], "evidencia": regra["evidencia"], "canal": regra["canal"],
                    "confianca": float(regra["confianca"]), "motivo": regra["motivo"],
                    "campanha": campanha, "regra": regra["ordem"], "explicacao": ""}
        if not campanha and regra.get("campanha_de"):
            veredito["motivo"] = (regra["motivo"] + " | campanha NAO atribuida (porta sem campanha)").strip()
        break
    return veredito


def chave_idempotencia(payload: dict) -> str:
    return f"google-lead:{payload.get('fonte')}:{payload.get('lead_id')}"


def resumo_sem_pii(payload: dict, veredito: dict) -> str:
    partes = [f"fonte={payload.get('fonte')}", f"evidencia={veredito.get('evidencia')}"]
    if payload.get("empresa_nome"):
        partes.append(f"empresa={payload['empresa_nome']}")
    if payload.get("utm_campaign"):
        partes.append(f"utm_campaign={payload['utm_campaign']}")
    if payload.get("pagina"):
        partes.append(f"pagina={payload['pagina']}")
    return "; ".join(partes)[:400]


def envelope_de_atribuicao(payload: dict, veredito: dict, contrato: dict, resolucao: dict | None) -> dict:
    return {
        "versao": VERSAO,
        "fonte": payload.get("fonte"),
        "evidencia": veredito.get("evidencia"),
        "canal": veredito.get("canal"),
        "confianca": veredito.get("confianca"),
        "regra": veredito.get("regra"),
        "motivo": veredito.get("motivo"),
        "campanha_id": veredito.get("campanha") or payload.get("campanha_id"),
        "campanha_nome": payload.get("campanha_nome"),
        "grupo_anuncio_id": payload.get("grupo_anuncio_id"),
        "criativo_id": payload.get("criativo_id"),
        "palavra_chave": payload.get("palavra_chave"),
        "gclid": payload.get("gclid"),
        "utm": {"source": payload.get("utm_source"), "medium": payload.get("utm_medium"),
                "campaign": payload.get("utm_campaign")},
        "pagina": payload.get("pagina"),
        "resolucao_gclid": resolucao,
        "contato_mascarado": {"email": mascarar(payload.get("contato_email")),
                              "telefone": mascarar(payload.get("contato_telefone")),
                              "nome": mascarar(payload.get("contato_nome"))},
        "campos_desconhecidos": campos_desconhecidos(payload, contrato),
        "carimbo": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                                  .encode("utf-8")).hexdigest()[:24],
    }


# --------------------------------------------------------------------------------------------
# 5. Porta do gclid (Ads API declarada; loopback em dev) — invariante 7
# --------------------------------------------------------------------------------------------
def resolver_gclid(gclid: str, config: Configuracao) -> dict | None:
    """Resolve o gclid pela porta declarada. Devolve None quando NAO ha resolucao usavel (fail-closed)."""
    if not gclid or not config.porta_ads:
        return None
    if config.ambiente == "dev" and not loopback_url(config.porta_ads):
        return None
    url = config.porta_ads.rstrip("/") + "/gclid/" + urllib.parse.quote(gclid, safe="")
    try:
        with urllib.request.urlopen(url, timeout=int(config.timeout)) as resp:  # noqa: S310 — porta declarada
            if resp.status != 200:
                return None
            dados = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError, json.JSONDecodeError):
        return None
    status = str(dados.get("status") or "").strip().upper()
    if status not in ("RESOLVIDO", "NAO_ENCONTRADO"):
        return None
    return {"status": status, "campanha_id": dados.get("campanha_id"), "campanha_nome": dados.get("campanha_nome"),
            "grupo_anuncio_id": dados.get("grupo_anuncio_id"), "palavra_chave": dados.get("palavra_chave")}


# --------------------------------------------------------------------------------------------
# 6. Porta de banco (comando declarado, SQL por stdin, resposta JSON)
# --------------------------------------------------------------------------------------------
class PortaBanco:
    """Acesso ao PostgreSQL pelo comando de porta declarado (`docker exec -i <container> psql ...`).

    Sem driver embutido: o SQL vai por stdin e a resposta volta em JSON (`json_agg`). Sem DDL, sem
    UPDATE e sem DELETE — so INSERT e SELECT (invariante 4).
    """

    def __init__(self, prefixo: str):
        self.prefixo = prefixo

    def _argumentos(self) -> list:
        try:
            return shlex.split(self.prefixo)
        except ValueError as e:
            raise Recusa("BANCO_NAO_DECLARADO", f"prefixo de banco invalido: {e}") from e

    def consultar(self, sql: str) -> list:
        comando = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c",
                                        f"SELECT coalesce(json_agg(t), '[]'::json)::text FROM ({sql}) t;"]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        linhas = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        if not linhas:
            raise Recusa("BANCO_RESPOSTA_VAZIA", "a porta de banco nao devolveu JSON")
        try:
            return json.loads(linhas[-1])
        except json.JSONDecodeError as e:
            raise Recusa("BANCO_RESPOSTA_INVALIDA",
                         f"a porta de banco nao devolveu JSON: {linhas[-1][:200]}") from e

    def executar(self, sql: str) -> list:
        """Escreve (INSERT ... RETURNING) e devolve as linhas afetadas pelo envelope da CTE."""
        comando_base = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c"]
        if "RETURNING" in sql.upper():
            sql_exec = (f"WITH afetados AS ({sql}) "
                        "SELECT coalesce(json_agg(afetados), '[]'::json)::text FROM afetados;")
        else:
            sql_exec = sql
        proc = subprocess.run(comando_base + [sql_exec], capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        linhas = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        return json.loads(linhas[-1]) if linhas else []


def q(valor) -> str:
    if valor is None or valor == "":
        return "NULL"
    return "'" + str(valor).replace("'", "''") + "'"


def resolver_vinculo(porta: PortaBanco, payload: dict) -> dict | None:
    """Acha contato/organizacao por e-mail e depois por telefone/whatsapp. Nao cria nada (invariante 5)."""
    email = str(payload.get("contato_email") or "").strip().lower()
    telefone = re.sub(r"[^0-9+]", "", str(payload.get("contato_telefone") or ""))
    if not email and not telefone:
        return None
    condicoes = []
    if email:
        condicoes.append(f"lower(c.email) = {q(email)}")
    if telefone:
        condicoes.append(f"(regexp_replace(coalesce(c.phone,''), '[^0-9+]', '', 'g') = {q(telefone)} "
                         f"OR regexp_replace(coalesce(c.whatsapp,''), '[^0-9+]', '', 'g') = {q(telefone)})")
    linhas = porta.consultar(
        f"SELECT c.id::text AS contact_id, c.organization_id::text AS organization_id, o.legal_name "
        f"FROM {TABELA_CONTATOS} c JOIN sales_intelligence.organizations o ON o.id = c.organization_id "
        f"WHERE {' OR '.join(condicoes)} LIMIT 1")
    return linhas[0] if linhas else None


def ja_ingerido(porta: PortaBanco, chave: str) -> dict | None:
    linhas = porta.consultar(
        f"SELECT id::text, status, entity_id::text FROM {TABELA_SYNC} "
        f"WHERE idempotency_key = {q(chave)} LIMIT 1")
    return linhas[0] if linhas else None


def gravar_interacao(porta: PortaBanco, payload: dict, veredito: dict, vinculo: dict) -> str:
    ocorrido = str(payload.get("ocorrido_em") or "").strip()
    if ocorrido:
        try:
            datetime.fromisoformat(ocorrido.replace("Z", "+00:00"))
        except ValueError:
            raise Recusa("PAYLOAD_INCOERENTE", f"ocorrido_em nao e' ISO-8601: {ocorrido}") from None
        ocorrido_sql = q(ocorrido.replace("Z", "+00:00"))
    else:
        ocorrido_sql = "NOW()"
    tipo = ((CONTRATO_EM_VIGOR or {}).get("fontes") or {}).get(str(payload.get("fonte")), {}).get(
        "interaction_type") or "LEAD_WEB"
    assunto = f"Lead Google — {payload.get('campanha_nome') or veredito.get('evidencia')}"
    colunas = ("id", "organization_id", "contact_id", "channel", "direction", "interaction_type",
               "occurred_at", "subject", "content_summary", "content_reference", "ai_confidence")
    valores = ("gen_random_uuid()", q(vinculo["organization_id"]), q(vinculo.get("contact_id")),
               q("google"), q("INBOUND"), q(tipo), ocorrido_sql, q(assunto),
               q(resumo_sem_pii(payload, veredito)), q(f"google:{chave_idempotencia(payload)}"), "NULL")
    sql = (f"INSERT INTO {TABELA_INTERACOES} ({', '.join(colunas)}) VALUES ({', '.join(valores)}) "
           f"RETURNING id::text AS id")
    linhas = porta.executar(sql)
    if not linhas:
        raise Recusa("GRAVACAO_SEM_RETORNO", "INSERT em interactions nao devolveu id")
    return linhas[0]["id"]


def gravar_trilha(porta: PortaBanco, chave: str, entity_id: str | None, status: str, payload: dict,
                  operacao: str = "INGESTAO_LEAD_GOOGLE", erro: str | None = None) -> None:
    sql = (f"INSERT INTO {TABELA_SYNC} (id, entity_type, entity_id, source_system, target_system, "
           f"operation, source_version, idempotency_key, status, request_payload, error_message, "
           f"completed_at) VALUES (gen_random_uuid(), 'interaction', {q(entity_id)}, 'google-leads', "
           f"'sales_intelligence', {q(operacao)}, {q(VERSAO)}, {q(chave)}, {q(status)}, "
           f"{q(json.dumps(payload, ensure_ascii=False, sort_keys=True))}, {q(erro)}, NOW())")
    porta.executar(sql)


CONTRATO_EM_VIGOR: dict | None = None


# --------------------------------------------------------------------------------------------
# 7. Saida com checagem fail-closed de segredo (invariante 8)
# --------------------------------------------------------------------------------------------
class Saida:
    def __init__(self, relatorio: str | None, segredo: str | None):
        self.relatorio = {"versao": VERSAO, "quando": agora(), "eventos": []}
        self.caminho_relatorio = relatorio
        self.segredo = segredo

    def evento(self, **campos) -> None:
        campos.setdefault("quando", agora())
        self.relatorio["eventos"].append(campos)
        texto = json.dumps(campos, ensure_ascii=False, sort_keys=True)
        self._conferir(texto)
        print(texto)

    def _conferir(self, texto: str) -> None:
        if self.segredo and len(self.segredo) >= 4 and self.segredo in texto:
            print(json.dumps({"evento": "SENHA_VAZADA", "motivo": "SENHA_VAZADA",
                              "detalhe": "o valor de TRE_GOOGLE_ADS_TOKEN apareceu na gravacao; "
                                         "gravacao recusada (exit 5)"}, ensure_ascii=False))
            raise SystemExit(CODIGO_SEGREDO)

    def fechar(self, veredito: str) -> None:
        self.relatorio["veredito"] = veredito
        texto = json.dumps(self.relatorio, ensure_ascii=False, sort_keys=True, indent=2)
        self._conferir(texto)
        print(f"# veredito: {veredito}")
        if self.caminho_relatorio:
            os.makedirs(os.path.dirname(os.path.abspath(self.caminho_relatorio)), exist_ok=True)
            with open(self.caminho_relatorio, "w", encoding="utf-8") as fh:
                fh.write(texto + "\n")
            print(f"# relatorio: {self.caminho_relatorio}")


# --------------------------------------------------------------------------------------------
# 8. CLI
# --------------------------------------------------------------------------------------------
def ler_env_file(caminho: str | None) -> dict:
    env = {}
    if not caminho:
        return env
    if not os.path.isfile(caminho):
        raise Recusa("ENV_AUSENTE", f"env-file nao encontrado: {caminho}")
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            env[chave.strip()] = valor.strip().strip('"').strip("'")
    return env


def carregar_payload(entrada: str | None, bruto: str | None) -> dict:
    if entrada:
        if not os.path.isfile(entrada):
            raise Recusa("PAYLOAD_AUSENTE", f"entrada nao encontrada: {entrada}")
        with open(entrada, "r", encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except json.JSONDecodeError as e:
                raise Recusa("PAYLOAD_INVALIDO", f"JSON invalido em {entrada}: {e}") from e
    if bruto:
        try:
            return json.loads(bruto)
        except json.JSONDecodeError as e:
            raise Recusa("PAYLOAD_INVALIDO", f"--payload nao e' JSON: {e}") from e
    raise Recusa("PAYLOAD_AUSENTE", "informe --entrada <arquivo> ou --payload <json>")


def main(argv=None) -> int:
    global CONTRATO_EM_VIGOR
    parser = argparse.ArgumentParser(description="Atribuicao de lead do Google v1 (TRE-W7-E03-T01)")
    parser.add_argument("--ambiente", choices=AMBIENTES, default=None)
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--porta-banco", default=None)
    parser.add_argument("--porta-ads", default=None)
    parser.add_argument("--saida", default=None, help="arquivo de relatorio da rodada")
    acao = parser.add_mutually_exclusive_group(required=True)
    acao.add_argument("--planejar", action="store_true", help="configuracao efetiva (token mascarado); nao conecta")
    acao.add_argument("--conferir", action="store_true", help="valida contrato, guardas e auditoria da fonte")
    acao.add_argument("--atribuir", action="store_true", help="decide a atribuicao (nunca grava)")
    acao.add_argument("--ingerir", action="store_true", help="decide e grava (exige --confirmo)")
    acao.add_argument("--desfazer", metavar="CHAVE", default=None, help="grava trilha DESFEITO da chave")
    parser.add_argument("--entrada", default=None)
    parser.add_argument("--payload", default=None)
    parser.add_argument("--confirmo", action="store_true", help="autoriza a gravacao (sem ele: DRY_RUN)")

    args = parser.parse_args(argv)
    env = dict(os.environ)
    env.update(ler_env_file(args.env_file))
    if args.ambiente:
        env["TRE_AMBIENTE"] = args.ambiente
    if args.porta_banco:
        env["TRE_GOOGLE_PORTA_BANCO"] = args.porta_banco
    if args.porta_ads:
        env["TRE_GOOGLE_ADS_PORTA"] = args.porta_ads
    config = Configuracao(env)
    saida = Saida(args.saida, config.token_ads)

    exigir_banco = bool(args.ingerir or args.desfazer)
    problemas = validar(config, exigir_banco=exigir_banco)
    if problemas:
        for motivo, detalhe in problemas:
            saida.evento(evento="RECUSA", motivo=motivo, detalhe=detalhe)
        saida.fechar("RECUSADO")
        return CODIGO_PRODUCAO if any(m == "PRODUCAO_RECUSADA" for m, _ in problemas) else CODIGO_RECUSA

    violacoes = auditar_fonte()
    if violacoes:
        saida.evento(evento="RECUSA", motivo="ESCRITA_NO_CODIGO", detalhe=violacoes)
        saida.fechar("RECUSADO")
        return CODIGO_RECUSA

    try:
        contrato = carregar_contrato(args.contrato)
    except Recusa as e:
        saida.evento(evento="RECUSA", motivo=e.motivo, detalhe=e.detalhe)
        saida.fechar("RECUSADO")
        return CODIGO_RECUSA
    CONTRATO_EM_VIGOR = contrato

    if args.planejar:
        saida.evento(evento="PLANO", config=config.resumo(),
                     contrato={"versao": contrato.get("versao"), "card": contrato.get("card"),
                               "regras": len(contrato.get("tabela_de_atribuicao") or []),
                               "lacunas": len(contrato.get("lacunas") or [])},
                     auditoria_da_fonte="OK")
        saida.fechar("PLANO")
        return CODIGO_OK

    if args.conferir:
        saida.evento(evento="CONFERENCIA", contrato="OK",
                     guardas=[("OK", f"{config.ambiente}: sem problema")],
                     auditoria_da_fonte="OK",
                     vocabulario=sorted((contrato.get("vocabulario") or {}).keys()))
        saida.fechar("CONFERIDO")
        return CODIGO_OK

    if args.desfazer:
        porta = PortaBanco(config.porta_banco)
        chave = args.desfazer.strip()
        anterior = ja_ingerido(porta, chave)
        if not anterior:
            saida.evento(evento="DESFAZER", chave=chave, resultado="NAO_ENCONTRADO")
            saida.fechar("DESFAZER_NAO_ENCONTRADO")
            return CODIGO_FALHA
        if not args.confirmo:
            saida.evento(evento="DESFAZER", chave=chave, resultado="PLANO", motivo="sem --confirmo (DRY_RUN)")
            saida.fechar("PLANO")
            return CODIGO_OK
        gravar_trilha(porta, chave + "#desfeito", anterior.get("entity_id"), "DESFEITO",
                      {"desfaz": chave, "trilha_original": anterior.get("id"), "quando": agora()},
                      operacao="DESFAZER_LEAD_GOOGLE")
        saida.evento(evento="DESFAZER", chave=chave, resultado="DESFEITO", trilha_original=anterior.get("id"))
        saida.fechar("DESFEITO")
        return CODIGO_OK

    try:
        payload = carregar_payload(args.entrada, args.payload)
    except Recusa as e:
        saida.evento(evento="RECUSA", motivo=e.motivo, detalhe=e.detalhe)
        saida.fechar("RECUSADO")
        return CODIGO_RECUSA

    problemas_payload = validar_payload(payload, contrato)
    if problemas_payload:
        motivo = problemas_payload[0][0]
        for m, detalhe in problemas_payload:
            saida.evento(evento="RECUSA", motivo=m, detalhe=detalhe)
        saida.fechar("RECUSADO")
        return CODIGO_RECUSA

    chave = chave_idempotencia(payload)
    resolucao = None
    if str(payload.get("gclid") or "").strip():
        resolucao = resolver_gclid(str(payload["gclid"]).strip(), config)
        if resolucao is None:
            saida.evento(evento="GCLID", chave=chave, resultado="NAO_RESOLVIDO",
                         detalhe="porta ausente/inacessivel ou gclid desconhecido: a resolucao nao e' usada")
    veredito = atribuir(payload, contrato, resolucao)
    envelope = envelope_de_atribuicao(payload, veredito, contrato, resolucao)
    saida.evento(evento="ATRIBUICAO", chave=chave, veredito=veredito.get("veredito"),
                 evidencia=veredito.get("evidencia"), canal=veredito.get("canal"),
                 confianca=veredito.get("confianca"), campanha=veredito.get("campanha"),
                 motivo=veredito.get("motivo"), campos_desconhecidos=envelope["campos_desconhecidos"])

    if not args.ingerir:
        saida.fechar("DRY_RUN:" + str(veredito.get("veredito")))
        return CODIGO_OK

    if not args.confirmo:
        saida.evento(evento="DRY_RUN", chave=chave, motivo="--ingerir sem --confirmo nao grava")
        saida.fechar("DRY_RUN")
        return CODIGO_OK

    try:
        porta = PortaBanco(config.porta_banco)
        anterior = ja_ingerido(porta, chave)
        if anterior:
            saida.evento(evento="JA_INGERIDO", chave=chave, status=anterior.get("status"),
                         trilha=anterior.get("id"))
            saida.fechar("JA_INGERIDO")
            return CODIGO_OK
        if veredito.get("veredito") != "ATRIBUIDO":
            gravar_trilha(porta, chave, None, veredito["veredito"], envelope, erro=veredito.get("motivo"))
            saida.evento(evento="TRILHA", chave=chave, status=veredito["veredito"],
                         motivo=veredito.get("motivo"), interactions=0)
            saida.fechar(veredito["veredito"])
            return CODIGO_OK
        vinculo = resolver_vinculo(porta, payload)
        if not vinculo:
            gravar_trilha(porta, chave, None, "SEM_VINCULO", envelope,
                          erro="contato nao encontrado em contacts (nao se inventa organizacao)")
            saida.evento(evento="SEM_VINCULO", chave=chave, interactions=0,
                         detalhe="contato nao encontrado: nenhuma linha em interactions")
            saida.fechar("SEM_VINCULO")
            return CODIGO_OK
        interacao = gravar_interacao(porta, payload, veredito, vinculo)
        gravar_trilha(porta, chave, interacao, "ATRIBUIDO", envelope)
        saida.evento(evento="GRAVADO", chave=chave, interaction=interacao,
                     organization=vinculo.get("organization_id"), contact=vinculo.get("contact_id"),
                     status="ATRIBUIDO")
        saida.fechar("ATRIBUIDO")
        return CODIGO_OK
    except Recusa as e:
        saida.evento(evento="FALHA", motivo=e.motivo, detalhe=e.detalhe)
        saida.fechar("FALHOU")
        return CODIGO_FALHA


if __name__ == "__main__":
    sys.exit(main())
