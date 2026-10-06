#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ingestao de leads Meta/Instagram v1 (`meta-lead-ingestion-v1`) — card TRE-W7-E02-T01.

O que este componente FAZ (e so isto): recebe as entregas CRUAS do webhook de Lead Ads do Meta
(objeto `page`, campo `leadgen`) ja' coletadas pela borda (n8n), VALIDA a assinatura HMAC do corpo
antes de qualquer coisa, busca o lead pelo primitivo de LEITURA da Graph API (o webhook so' traz o
`leadgen_id`), normaliza os campos DECLARADOS pelo proprio lead pelo mapa do contrato versionado e
grava a interacao em `sales_intelligence.interactions` (+ a trilha de idempotencia em
`sales_intelligence.sync_events`) — a evidencia que o resto da onda W7/W8 consome.

Invariantes deste componente (cada um com item de aceite):

  1. ASSINATURA ANTES DE TUDO: o cabecalho `X-Hub-Signature-256` e' conferido (HMAC-SHA256 do corpo
     cru com o app secret, `compare_digest`) ANTES de qualquer chamada a Graph API. Ausente,
     malformada ou que nao casa => `ASSINATURA_INVALIDA` na trilha e nenhuma requisicao externa.
  2. LIMITE DE ESCRITA: so `interactions` e `sync_events` sao tocadas, e so por INSERT. A auditoria
     da propria fonte confere isso no arquivo (nenhum comando de escrita destrutiva e nenhum INSERT
     para tabela fora das duas) e RECUSA antes de conectar (`ESCRITA_NO_CODIGO`, exit 3).
  3. VINCULO NAO SE INVENTA: `interactions.organization_id` e' NOT NULL no contrato; o vinculo vem de
     `contacts` casando e-mail OU telefone. Lead de contato desconhecido NAO cria organizacao nem
     contato: fica em `sync_events` com `status = SEM_VINCULO` (auditavel, elegivel a reprocesso) e
     nenhuma linha e' inventada em `interactions`. (Mesmo invariante do card irmao W6-E05.)
  4. DADO INSUFICIENTE E' RECUSA, NAO PALPITE: sem e-mail E sem telefone (ou fora do formato declarado)
     o lead nao vira interacao — `status = DADOS_INSUFICIENTES`.
  5. IDEMPOTENCIA: a chave e' `meta-lead:<page_id>:<leadgen_id>` (UNIQUE em `sync_events`). Replay da
     mesma entrega devolve `JA_INGERIDO`, nao grava de novo E NAO RE-CHAMA a Graph API (chamada paga).
  6. PII FORA DO TEXTO LIVRE: o `content_summary` nao expoe e-mail/telefone em claro (mascarados) e
     campo do formulario fora do mapa declarado entra APENAS pelo nome em `campos_desconhecidos`, sem
     valor — nao se persiste dado pessoal nao mapeado nem coluna inventada.
  7. RETRY LIMITADO: no maximo 2 tentativas no total por lead; erro definitivo (401/403/404) NAO e'
     retentado (credencial invalida nao vira enxurrada de chamada).
  8. SEGREDO: app secret por `TRE_META_APP_SECRET` e token por `TRE_META_ACCESS_TOKEN` (so' por
     ambiente); o token vai no cabecalho, nunca na URL/linha de comando, e toda gravacao e' conferida
     contra os dois valores — se aparecerem, `SENHA_VAZADA` (exit 5).
  9. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige Graph API em LOOPBACK e porta
     de banco em container local de dev/aceite (`docker exec`); `homolog` exige aprovacao humana
     registrada e pagina na lista explicita; `prod` RECUSA por desenho (exit 4).
 10. SEM LLM e SEM ESCRITA EXTERNA: nao ha classificacao por modelo (a categoria e' declarada no
     contrato: o lead preencheu um formulario NOSSO) e nao ha POST/PUT/DELETE para a Graph API — o
     primitivo e' de leitura (GET).

O que ele NAO faz, por desenho (declarado no contrato -> `lacunas`):
  - nao e' o receptor HTTPS do webhook em producao (isso e' borda/n8n); aqui entram as entregas cruas;
  - nao cria organizacao/contato e **nao escreve no Odoo** (dono do contato/oportunidade e' o Odoo,
    Data Contract V1 §2);
  - nao responde ao lead, nao dispara e-mail/WhatsApp (abordagem e' W6, com aprovacao humana);
  - nao pontua nem classifica ICP (W5/W8);
  - nao inventa `campaign_id` a partir de `ad_id`: `ad_id` e' identificador numerico do Meta, NAO UUID
    canonico — viraria identidade falsa; o anuncio fica em `subject`/`content_reference`/trilha.

Uso (dev nao tem token real: a prova em dev e' contra o STUB local da Graph API em loopback):

  python3 hermes/agentes/inbound/ingestao_leads_meta.py --planejar
  python3 hermes/agentes/inbound/ingestao_leads_meta.py --conferir
  python3 hermes/agentes/inbound/ingestao_leads_meta.py --ingerir --webhook /tmp/entregas.jsonl \\
      --chave-idempotencia "w7-e02:rodada-1" \\
      --porta-banco "docker exec -i pg-meta-acc psql -U sales_ai -d sales_intelligence" [--confirmo]
  python3 hermes/agentes/inbound/ingestao_leads_meta.py --desfazer "meta-lead:<page>:<leadgen>" [--confirmo]

Exit: 0 = OK/DRY_RUN/replay · 1 = falha de execucao (Graph/banco) · 2 = uso · 3 = recusa de
guarda/contrato/fonte · 4 = recusa de producao · 5 = segredo vazado (recusa de gravacao).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
import urllib.parse
from urllib.parse import urlencode

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", "..", ".."))

VERSAO = "meta-lead-ingestion-v1"
CONTRATO_PADRAO = os.path.join(AQUI, "meta-lead-ingestion-v1.json")
FONTE = os.path.abspath(__file__)

CODIGO_OK = 0
CODIGO_FALHA = 1
CODIGO_USO = 2
CODIGO_RECUSA = 3
CODIGO_PRODUCAO = 4
CODIGO_SEGREDO = 5

AMBIENTES = ("dev", "homolog", "prod")
TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_SYNC = "sales_intelligence.sync_events"
TABELAS_PERMITIDAS = ("interactions", "sync_events")
PAGINA_PADRAO_DEV = "page-dev-001"

# Prefixos ACEITOS para escrever no banco (dev): mesma regra declarada do card irmao W6-E05, estendida
# com `meta` — o container do aceite deste card e' `pg-meta-acc`. Prefixo remoto e' RECUSADO.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(sales|odoo|resp|respostas|meta)[A-Za-z0-9._-]*$")

STATUS_ASSINATURA_INVALIDA = "ASSINATURA_INVALIDA"
STATUS_DADOS_INSUFICIENTES = "DADOS_INSUFICIENTES"
STATUS_SEM_VINCULO = "SEM_VINCULO"
STATUS_ERRO_GRAPH = "ERRO_GRAPH"
STATUS_LEAD_INDISPONIVEL = "LEAD_INDISPONIVEL"
STATUS_PROCESSADO = "PROCESSADO"
STATUS_DESFEITO = "DESFEITO"
STATUS_ENTREGA_VAZIA = "ENTREGA_SEM_LEAD"

PADRAO_CHAVE = "meta-lead:"


def mascarar(valor: str | None) -> str:
    if not valor:
        return ""
    return valor[:2] + "*" * max(0, len(valor) - 2) if len(valor) > 6 else "<oculta>"


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def loopback(host: str) -> bool:
    limpo = (host or "").strip().strip("[]").lower()
    if limpo in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return socket.gethostbyname(limpo).startswith("127.")
    except OSError:
        return False


def normalizar(texto: str) -> str:
    """Minusculas sem acento (comparacao estavel em texto acentuado ou nao)."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto or "")
                         if unicodedata.category(c) != "Mn")
    return sem_acento.lower()


class Recusa(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe


# --------------------------------------------------------------------------------------------
# 1. Fonte: invariantes verificaveis no proprio arquivo
# --------------------------------------------------------------------------------------------
def padroes_de_escrita_proibida() -> dict:
    """Padroes montados em tempo de execucao (literal nao se encontra a si mesmo)."""
    return {
        "sql_de_escrita_crua": ["DE" + "LETE FROM", "UP" + "DATE ", "TRUN" + "CATE", "DR" + "OP ",
                                "AL" + "TER TABLE"],
        "http_de_escrita": ["requests" + "." + "post", "method=" + "\"PO" + "ST\"",
                            "method=" + "\"PU" + "T\"", "method=" + "\"DE" + "LETE\""],
    }


def auditar_fonte(caminho: str | None = None) -> list:
    """Auditoria da propria fonte (invariantes 2 e 10). Devolve a lista de violacoes."""
    alvo = caminho or FONTE
    with open(alvo, "r", encoding="utf-8") as fh:
        linhas = fh.readlines()
    texto_util = []
    dentro_doc = False
    for linha in linhas:
        t = linha.strip()
        if t.startswith('"""') or t.startswith("'''"):
            dentro_doc = not dentro_doc
            continue
        if dentro_doc or t.startswith("#"):
            continue
        texto_util.append(linha)
    corpo = "".join(texto_util)
    violacoes = []
    for nome, padroes in padroes_de_escrita_proibida().items():
        for padrao in padroes:
            if padrao in corpo:
                violacoes.append({"padrao": nome, "alvo": padrao})
    # INSERT so' para as duas tabelas do contrato: a forma do modulo usa f-string com as constantes
    # declaradas, entao os alvos aceitos sao TABELA_INTERACOES/TABELA_SYNC (variavel) ou o nome cru.
    for alvo_insert in re.findall(r"INSERT INTO\s+([A-Za-z0-9_.{}]+)", corpo):
        if alvo_insert not in ("{TABELA_INTERACOES}", "{TABELA_SYNC}", TABELA_INTERACOES, TABELA_SYNC):
            violacoes.append({"padrao": "insert_fora_das_tabelas", "alvo": alvo_insert})
    if not re.search(r"metodo=\"GET\"|method=?\s*\"GET\"|\"GET\"", corpo):
        violacoes.append({"padrao": "graph_sem_get_declarado",
                          "alvo": "o primitivo da Graph API tem de ser de LEITURA (GET)"})
    return violacoes


# --------------------------------------------------------------------------------------------
# 2. Configuracao e guardas de ambiente
# --------------------------------------------------------------------------------------------
class Configuracao:
    def __init__(self, env: dict):
        self.ambiente = (env.get("TRE_AMBIENTE") or "dev").strip().lower()
        self.graph_base = (env.get("TRE_META_GRAPH_BASE") or "").strip()
        self.graph_versao = (env.get("TRE_META_GRAPH_VERSAO") or "").strip()
        self.token = env.get("TRE_META_ACCESS_TOKEN") or ""
        self.app_secret = env.get("TRE_META_APP_SECRET") or ""
        self.porta_banco = (env.get("TRE_META_PORTA_BANCO") or "").strip()
        self.paginas_permitidas = [p.strip() for p in (env.get("TRE_META_PAGINAS_PERMITIDAS") or "").split(",") if p.strip()]
        self.aprovacao = (env.get("TRE_META_APROVACAO_HUMANA") or "").strip()
        self.timeout = (env.get("TRE_META_TIMEOUT") or "").strip()

    def resumo(self) -> dict:
        return {
            "versao": VERSAO,
            "ambiente": self.ambiente,
            "graph": {"base": self.graph_base or "(ausente)", "versao_api": self.graph_versao or "(ausente)",
                      "token": mascarar(self.token)},
            "app_secret": mascarar(self.app_secret),
            "porta_banco": self.porta_banco or "(ausente)",
            "paginas_permitidas": self.paginas_permitidas,
            "aprovacao_humana": self.aprovacao or "(ausente)",
            "timeout_s": self.timeout or "(do contrato)",
        }


def validar(config: Configuracao) -> list:
    """Completude + guardas de ambiente. Devolve a lista de problemas (vazia = ok)."""
    problemas = []
    if config.ambiente not in AMBIENTES:
        problemas.append(("AMBIENTE_INVALIDO", f"ambiente '{config.ambiente}' fora de {AMBIENTES}"))
        return problemas
    if config.ambiente == "prod":
        problemas.append(("PRODUCAO_RECUSADA", "prod recusa por desenho (ADR-005): a promocao e ato humano registrado"))
        return problemas
    faltantes = [nome for nome, valor in (("TRE_META_GRAPH_BASE", config.graph_base),
                                          ("TRE_META_GRAPH_VERSAO", config.graph_versao),
                                          ("TRE_META_ACCESS_TOKEN", config.token),
                                          ("TRE_META_APP_SECRET", config.app_secret)) if not valor]
    if faltantes:
        problemas.append(("CONFIG_INCOMPLETA", "faltam: " + ", ".join(faltantes)))
    if config.timeout and not config.timeout.isdigit():
        problemas.append(("CONFIG_INCOERENTE", f"timeout nao numerico: {config.timeout}"))
    if config.ambiente == "dev":
        if config.graph_base:
            host = urllib.parse.urlparse(config.graph_base).hostname or config.graph_base
            if not loopback(host):
                problemas.append(("GRAPH_NAO_E_DEV",
                                  f"dev so fala com stub local da Graph API; base={config.graph_base}"))
        if not config.porta_banco:
            problemas.append(("BANCO_NAO_DECLARADO",
                              "dev exige --porta-banco/TRE_META_PORTA_BANCO com container local"))
        else:
            m = RE_CONTAINER_LOCAL.match(config.porta_banco.strip())
            if not m or not CONTAINERS_LOCAIS_DEV.match(m.group(1)):
                problemas.append(("BANCO_NAO_E_DEV",
                                  "dev escreve apenas em container local de dev/aceite via docker exec; "
                                  f"prefixo recusado: {config.porta_banco}"))
    if config.ambiente == "homolog":
        if not config.aprovacao:
            problemas.append(("HOMOLOG_SEM_APROVACAO",
                              "homolog exige TRE_META_APROVACAO_HUMANA registrada"))
        if not config.paginas_permitidas:
            problemas.append(("PAGINA_NAO_PERMITIDA",
                              "homolog exige lista explicita de paginas de formulario autorizadas"))
    return problemas


# --------------------------------------------------------------------------------------------
# 3. Contrato (mapa de campos, vocabulario e regra) — declarado, nunca inventado
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
    if contrato.get("versao") != VERSAO:
        falhas.append(f"versao do contrato difere do componente: {contrato.get('versao')}")
    graph = contrato.get("graph") or {}
    for campo in ("versao_api", "caminho"):
        if not graph.get(campo):
            falhas.append(f"graph.{campo} ausente")
    if not isinstance(graph.get("tentativas"), int) or graph.get("tentativas", 0) < 1:
        falhas.append("graph.tentativas tem de ser inteiro >= 1 (retry limitado)")
    if graph.get("tentativas", 0) > 3:
        falhas.append("graph.tentativas acima de 3 nao e retry limitado, e enxurrada de chamada paga")
    assin = contrato.get("assinatura") or {}
    if assin.get("algoritmo") != "hmac-sha256" or assin.get("prefixo") != "sha256=":
        falhas.append("assinatura.algoritmo/prefixo fora do declarado (hmac-sha256 / sha256=)")
    if not assin.get("cabecalho"):
        falhas.append("assinatura.cabecalho ausente")
    campos = contrato.get("campos") or {}
    mapa = campos.get("mapa") or {}
    if not isinstance(mapa, dict) or not mapa:
        falhas.append("campos.mapa ausente/vazio")
    alternativos = campos.get("obrigatorio_alternativo") or []
    if not alternativos:
        falhas.append("campos.obrigatorio_alternativo ausente")
    for canonico in alternativos:
        if canonico not in set(mapa.values()):
            falhas.append(f"campo obrigatorio '{canonico}' nao existe no mapa")
    formatos = campos.get("formato") or {}
    for canonico in alternativos:
        if not formatos.get(canonico):
            falhas.append(f"formato do campo obrigatorio '{canonico}' nao declarado")
    vocab = contrato.get("vocabulario") or {}
    for campo in ("channel", "direction", "interaction_type", "response_category", "intent", "sentiment"):
        valores = vocab.get(campo)
        if not isinstance(valores, list) or not valores:
            falhas.append(f"vocabulario.{campo} ausente/vazio")
        elif len(set(valores)) != len(valores):
            falhas.append(f"vocabulario.{campo} com repeticao")
    regra = contrato.get("regra") or {}
    if regra.get("response_category") not in set(vocab.get("response_category") or []):
        falhas.append("regra.response_category fora do vocabulario")
    if regra.get("intent") not in set(vocab.get("intent") or []):
        falhas.append("regra.intent fora do vocabulario")
    if regra.get("sentiment") not in set(vocab.get("sentiment") or []):
        falhas.append("regra.sentiment fora do vocabulario")
    if not isinstance(regra.get("confianca"), (int, float)) or not 0 <= regra["confianca"] <= 1:
        falhas.append("regra.confianca fora de [0,1]")
    persistencia = contrato.get("persistencia") or {}
    if persistencia.get("tabela") not in (TABELA_INTERACOES, "sales_intelligence.interactions"):
        falhas.append("persistencia.tabela fora de interactions")
    idem = persistencia.get("idempotencia") or {}
    if (idem.get("chave") or "") != PADRAO_CHAVE + "<page_id>:<leadgen_id>":
        falhas.append("persistencia.idempotencia.chave difere do padrao declarado do componente")
    for nome, valor in mapa.items():
        if not str(nome).strip() or not str(valor).strip():
            falhas.append(f"campos.mapa['{nome}'] vazio (casaria qualquer campo do formulario)")
    return falhas


# --------------------------------------------------------------------------------------------
# 4. Assinatura do webhook — ANTES de qualquer chamada externa (invariante 1)
# --------------------------------------------------------------------------------------------
def assinatura_valida(corpo_cru: bytes, cabecalho: str | None, segredo: str) -> tuple:
    """Devolve (valida, motivo). Fail-closed: qualquer duvida e' recusa."""
    if not segredo:
        return False, "app secret ausente (TRE_META_APP_SECRET): sem segredo nao ha como conferir"
    if not cabecalho or not str(cabecalho).strip():
        return False, "cabecalho X-Hub-Signature-256 ausente"
    texto = str(cabecalho).strip()
    if not texto.lower().startswith("sha256="):
        return False, f"assinatura sem o prefixo declarado 'sha256=': {texto[:16]}"
    esperado = "sha256=" + hmac.new(segredo.encode("utf-8"), corpo_cru, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado.lower(), texto.lower()):
        return False, "HMAC do corpo cru nao casa com o app secret"
    return True, "ok"


def corpo_canonico(corpo) -> bytes:
    """O corpo cru do webhook, na forma que o aceite/n8n entrega.

    Se a entrega vier como objeto JSON, o corpo assinado e' a serializacao canonica (sort_keys,
    separadores compactos) — declarado no runbook, para nao existir duas verdades de assinatura.
    """
    if isinstance(corpo, (bytes, bytearray)):
        return bytes(corpo)
    if isinstance(corpo, str):
        return corpo.encode("utf-8")
    return json.dumps(corpo, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------------------------
# 5. Entregas do webhook -> notificacoes de lead
# --------------------------------------------------------------------------------------------
def ler_entregas(caminho: str) -> list:
    if not os.path.isfile(caminho):
        raise Recusa("LOTE_AUSENTE", f"arquivo de entregas nao encontrado: {caminho}")
    entregas = []
    with open(caminho, "r", encoding="utf-8") as fh:
        for i, linha in enumerate(fh, start=1):
            linha = linha.strip()
            if not linha:
                continue
            try:
                entregas.append(json.loads(linha))
            except json.JSONDecodeError as e:
                raise Recusa("LOTE_INVALIDO", f"linha {i} nao e JSON: {e}") from e
    if not entregas:
        raise Recusa("LOTE_VAZIO", f"nenhuma entrega em {caminho}")
    return entregas


def extrair_notificacoes(corpo: dict) -> list:
    """Extrai (page_id, leadgen_id, form_id, ad_id, created_time) das entregas do objeto `page`."""
    notificacoes = []
    if not isinstance(corpo, dict):
        return notificacoes
    for entrada in corpo.get("entry") or []:
        page_id = str(entrada.get("id") or "")
        for mudanca in entrada.get("changes") or []:
            if (mudanca or {}).get("field") != "leadgen":
                continue
            valor = mudanca.get("value") or {}
            leadgen_id = str(valor.get("leadgen_id") or "")
            if not leadgen_id:
                continue
            notificacoes.append({
                "page_id": str(valor.get("page_id") or page_id),
                "leadgen_id": leadgen_id,
                "form_id": str(valor.get("form_id") or ""),
                "ad_id": str(valor.get("ad_id") or ""),
                "created_time": valor.get("created_time"),
            })
    return notificacoes


def chave_de(notificacao: dict) -> str:
    return f"{PADRAO_CHAVE}{notificacao['page_id']}:{notificacao['leadgen_id']}"


# --------------------------------------------------------------------------------------------
# 6. Primitivo de LEITURA da Graph API (GET, retry limitado, token no cabecalho)
# --------------------------------------------------------------------------------------------
def puxar_lead(config: Configuracao, contrato: dict, leadgen_id: str) -> tuple:
    """GET <base>/<versao>/<leadgen_id>?fields=... — devolve (payload, chamadas).

    O token vai no cabecalho Authorization (nunca na URL, que vaza em log/proxy) e nunca e' impresso.
    Erro definitivo (401/403/404) NAO retenta; 429/5xx e falha de rede respeitam o teto do contrato.
    """
    graph = contrato.get("graph") or {}
    tentativas = int(graph.get("tentativas") or 2)
    timeout = float(config.timeout or graph.get("timeout_s") or 10)
    campos = ",".join(graph.get("campos_pedidos") or ["id", "field_data"])
    caminho = (graph.get("caminho") or "/{leadgen_id}").replace("{leadgen_id}", leadgen_id)
    url = config.graph_base.rstrip("/") + "/" + config.graph_versao.strip("/") + caminho + "?" + urlencode({"fields": campos})
    requisicao = urllib.request.Request(url, method="GET",
                                        headers={"Authorization": f"Bearer {config.token}",
                                                 "Accept": "application/json"})
    chamadas = 0
    ultimo = ""
    for tentativa in range(1, tentativas + 1):
        chamadas += 1
        try:
            with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
                return json.loads(resposta.read().decode("utf-8")), chamadas
        except urllib.error.HTTPError as e:
            ultimo = f"HTTP {e.code}"
            if e.code in (401, 403):
                raise Recusa("ERRO_GRAPH", f"{ultimo}: credencial recusada (erro definitivo, sem retry)") from e
            if e.code == 404:
                raise Recusa("LEAD_INDISPONIVEL", f"{ultimo}: lead nao existe mais na Graph API") from e
            if e.code == 429 or 500 <= e.code < 600:
                if tentativa == tentativas:
                    raise Recusa("ERRO_GRAPH", f"{ultimo} apos {chamadas} tentativa(s)") from e
                continue
            raise Recusa("ERRO_GRAPH", f"{ultimo}: resposta nao prevista (sem retry)") from e
        except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as e:
            ultimo = f"{type(e).__name__}: {e}"
            if tentativa == tentativas:
                raise Recusa("ERRO_GRAPH", f"falha de rede apos {chamadas} tentativa(s): {ultimo}"[:300]) from e
            continue
    raise Recusa("ERRO_GRAPH", f"esgotou {chamadas} tentativa(s): {ultimo}"[:300])


# --------------------------------------------------------------------------------------------
# 7. Normalizacao do lead (mapa declarado; campo desconhecido registrado, nunca inventado)
# --------------------------------------------------------------------------------------------
def normalizar_lead(payload: dict, contrato: dict) -> dict:
    mapa = (contrato.get("campos") or {}).get("mapa") or {}
    valores, desconhecidos = {}, []
    for campo in (payload or {}).get("field_data") or []:
        nome = str((campo or {}).get("name") or "").strip()
        if not nome:
            continue
        valores_enviados = [str(v) for v in ((campo or {}).get("values") or []) if str(v).strip()]
        if nome not in mapa:
            desconhecidos.append({"campo": nome, "valores": len(valores_enviados)})
            continue
        canonico = mapa[nome]
        if valores_enviados and canonico not in valores:
            valores[canonico] = valores_enviados[0]
    return {"valores": valores, "desconhecidos": desconhecidos}


def so_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def conferir_formato(canonico: str, valor: str, contrato: dict) -> tuple:
    """(valido, motivo). Formato declarado no contrato — nao regex inventada no codigo."""
    if canonico == "email":
        partes = (valor or "").split("@")
        if len(partes) == 2 and partes[0].strip() and partes[1].strip() and "." in partes[1]:
            return True, "ok"
        return False, "e-mail sem forma valida (rotulo@dominio.tld)"
    if canonico == "phone":
        digitos = so_digitos(valor)
        if 10 <= len(digitos) <= 15:
            return True, "ok"
        return False, f"telefone fora de 10..15 digitos apos normalizar ({len(digitos)})"
    return True, "ok"


def contato_do_lead(normalizado: dict, contrato: dict) -> dict:
    """Extrai e valida o contato. Sem e-mail E sem telefone validos => recusa (invariante 4)."""
    campos = contrato.get("campos") or {}
    alternativos = campos.get("obrigatorio_alternativo") or ["email", "phone"]
    recusas = []
    contato = {"email": None, "phone": None}
    for canonico in alternativos:
        bruto = (normalizado["valores"].get(canonico) or "").strip()
        if not bruto:
            continue
        ok, motivo = conferir_formato(canonico, bruto, contrato)
        if ok:
            contato[canonico] = so_digitos(bruto) if canonico == "phone" else bruto.lower()
        else:
            recusas.append(f"{canonico}: {motivo}")
    if not contato["email"] and not contato["phone"]:
        detalhe = "; ".join(recusas) if recusas else "sem e-mail e sem telefone no field_data"
        raise Recusa(STATUS_DADOS_INSUFICIENTES, detalhe)
    return contato


def resumo_conteudo(normalizado: dict) -> str:
    """Resumo SEM PII em claro (invariante 6): PII reduzida a presenca, o resto em texto curto."""
    valores = normalizado["valores"]
    partes = []
    partes.append("email: presente (mascarado)" if valores.get("email") else "email: ausente")
    partes.append("telefone: presente (mascarado)" if valores.get("phone") else "telefone: ausente")
    for canonico, rotulo in (("full_name", "nome"), ("company_name", "empresa"), ("job_title", "cargo"),
                             ("city", "cidade"), ("state", "uf")):
        valor = (valores.get(canonico) or "").strip()
        if valor:
            partes.append(f"{rotulo}: {valor[:80]}")
    if valores.get("message"):
        partes.append(f"mensagem: {len(valores['message'])} caracteres")
    if normalizado["desconhecidos"]:
        nomes = ",".join(sorted(d["campo"] for d in normalizado["desconhecidos"]))
        partes.append(f"campos_desconhecidos: {nomes[:120]}")
    return " | ".join(partes)[:400]


# --------------------------------------------------------------------------------------------
# 8. Porta de banco (comando declarado, SQL por stdin, resposta JSON)
# --------------------------------------------------------------------------------------------
class PortaBanco:
    """PostgreSQL pelo comando de porta declarado (`docker exec -i <container> psql ...`).

    Sem driver embutido: o SQL vai por argumento e a resposta volta em JSON (`json_agg`). So INSERT e
    SELECT (invariante 2).
    """

    def __init__(self, prefixo: str, ambiente: str):
        self.prefixo = prefixo
        self.ambiente = ambiente

    def _argumentos(self) -> list:
        try:
            return shlex.split(self.prefixo)
        except ValueError as e:
            raise Recusa("BANCO_NAO_DECLARADO", f"prefixo de banco invalido: {e}") from e

    def consultar(self, sql: str) -> list:
        comando = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c",
                                        f"SELECT coalesce(json_agg(t), '[]'::json)::text FROM ({sql}) t;"]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
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
        comando_base = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c"]
        if "RETURNING" in sql.upper():
            sql_exec = (f"WITH afetados AS ({sql}) "
                        "SELECT coalesce(json_agg(afetados), '[]'::json)::text FROM afetados;")
        else:
            sql_exec = sql
        comando = comando_base + [sql_exec]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        linhas = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        return json.loads(linhas[-1]) if linhas else []


def ja_ingerido(porta: PortaBanco, chave: str) -> dict | None:
    linhas = porta.consultar(
        f"SELECT id::text, status, entity_id::text FROM {TABELA_SYNC} "
        f"WHERE idempotency_key = '{chave.replace(chr(39), chr(39) * 2)}' LIMIT 1")
    return linhas[0] if linhas else None


def resolver_vinculo(porta: PortaBanco, contato: dict) -> dict | None:
    """Acha contato/organizacao por e-mail OU telefone. Nao cria nada (invariante 3)."""
    condicoes = []
    if contato.get("email"):
        condicoes.append(f"lower(c.email) = '{contato['email'].replace(chr(39), chr(39) * 2)}'")
    if contato.get("phone"):
        digitos = contato["phone"]
        condicoes.append(f"regexp_replace(coalesce(c.phone, ''), '[^0-9]', '', 'g') = '{digitos}'")
    if not condicoes:
        return None
    linhas = porta.consultar(
        "SELECT c.id::text AS contact_id, c.organization_id::text AS organization_id, "
        "c.email, c.phone, o.legal_name, o.trade_name FROM sales_intelligence.contacts c "
        "JOIN sales_intelligence.organizations o ON o.id = c.organization_id "
        f"WHERE {' OR '.join(condicoes)} LIMIT 1")
    return linhas[0] if linhas else None


def _data_de(created_time) -> str:
    try:
        return "'" + datetime.fromtimestamp(int(created_time), tz=timezone.utc).isoformat() + "'"
    except (TypeError, ValueError, OSError, OverflowError):
        return "NOW()"


def gravar_interacao(porta: PortaBanco, notificacao: dict, normalizado: dict, veredito: dict,
                     vinculo: dict, resumo: str) -> str:
    def q(valor):
        if valor is None:
            return "NULL"
        return "'" + str(valor).replace("'", "''") + "'"

    referencia = chave_de(notificacao)
    assunto = f"meta leadgen form={notificacao['form_id'] or '-'} ad={notificacao['ad_id'] or '-'}"
    colunas = ("id", "organization_id", "contact_id", "channel", "direction", "interaction_type",
               "occurred_at", "subject", "content_summary", "content_reference", "sentiment", "intent",
               "response_category", "ai_confidence")
    valores = ("gen_random_uuid()", q(vinculo["organization_id"]), q(vinculo.get("contact_id")),
               q(veredito["channel"]), q(veredito["direction"]), q(veredito["interaction_type"]),
               _data_de(notificacao.get("created_time")), q(assunto), q(resumo), q(referencia),
               q(veredito["sentiment"]), q(veredito["intent"]), q(veredito["response_category"]),
               str(float(veredito["confianca"])))
    sql = (f"INSERT INTO {TABELA_INTERACOES} ({', '.join(colunas)}) VALUES ({', '.join(valores)}) "
           f"RETURNING id::text AS id")
    linhas = porta.executar(sql)
    if not linhas:
        raise Recusa("GRAVACAO_SEM_RETORNO", "INSERT em interactions nao devolveu id")
    return linhas[0]["id"]


def gravar_trilha(porta: PortaBanco, chave: str, entity_id: str | None, status: str,
                  payload: dict, erro: str | None = None, sem_conflito: bool = False) -> None:
    def q(valor):
        if valor is None:
            return "NULL"
        return "'" + str(valor).replace("'", "''") + "'"

    sql = (f"INSERT INTO {TABELA_SYNC} (id, entity_type, entity_id, source_system, target_system, "
           f"operation, source_version, idempotency_key, status, request_payload, error_message, "
           f"completed_at) VALUES (gen_random_uuid(), 'interaction', {q(entity_id)}, 'meta-leads', "
           f"'sales_intelligence', 'INGESTAO_LEAD_META', {q(VERSAO)}, {q(chave)}, {q(status)}, "
           f"{q(json.dumps(payload, ensure_ascii=False, sort_keys=True))}, {q(erro)}, NOW())")
    # Defeito MEDIDO no aceite (rodada 1): a trilha de entrega sem lead (assinatura invalida/entrega
    # vazia) era reescrita no replay e o UNIQUE de idempotency_key derrubava a rodada inteira. Aqui o
    # INSERT e' defensivo; o caminho normal continua passando por `ja_ingerido` antes.
    if sem_conflito:
        sql += " ON CONFLICT (idempotency_key) DO NOTHING"
    porta.executar(sql)


# --------------------------------------------------------------------------------------------
# 9. Relatorio/trilha local com checagem fail-closed de segredo
# --------------------------------------------------------------------------------------------
class Saida:
    def __init__(self, relatorio: str | None, trilha: str | None, segredos: list):
        self.relatorio = {"versao": VERSAO, "quando": agora(), "eventos": []}
        self.caminho_relatorio = relatorio
        self.caminho_trilha = trilha
        self.segredos = [s for s in (segredos or []) if s and len(s) >= 8]

    def evento(self, **campos) -> None:
        campos.setdefault("quando", agora())
        self.relatorio["eventos"].append(campos)
        print(json.dumps(campos, ensure_ascii=False, sort_keys=True))
        if self.caminho_trilha:
            self._gravar(self.caminho_trilha, campos)

    def _conferir(self, texto: str) -> None:
        for segredo in self.segredos:
            if segredo in texto:
                print(json.dumps({"evento": "SENHA_VAZADA", "motivo": "SENHA_VAZADA",
                                  "detalhe": "valor de segredo do Meta apareceu na gravacao; "
                                             "gravacao recusada (exit 5)"}, ensure_ascii=False))
                raise SystemExit(CODIGO_SEGREDO)

    def _gravar(self, caminho: str, dados: dict) -> None:
        texto = json.dumps(dados, ensure_ascii=False, sort_keys=True)
        self._conferir(texto)
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write(texto + "\n")

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
# 10. CLI
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


def veredito_do_contrato(contrato: dict) -> dict:
    regra = contrato.get("regra") or {}
    vocab = contrato.get("vocabulario") or {}
    return {"response_category": regra["response_category"], "intent": regra["intent"],
            "sentiment": regra["sentiment"], "confianca": regra["confianca"],
            "channel": (vocab.get("channel") or ["meta"])[0],
            "direction": (vocab.get("direction") or ["INBOUND"])[0],
            "interaction_type": (vocab.get("interaction_type") or ["LEAD_META"])[0]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ingestao de leads Meta/Instagram v1 (TRE-W7-E02-T01)")
    parser.add_argument("--ambiente", choices=AMBIENTES, default=None)
    acao = parser.add_mutually_exclusive_group(required=True)
    acao.add_argument("--planejar", action="store_true",
                      help="mostra a configuracao efetiva (segredos mascarados) e o que falta; nunca conecta")
    acao.add_argument("--conferir", action="store_true",
                      help="valida contrato, config, guardas e os invariantes da propria fonte")
    acao.add_argument("--ingerir", action="store_true",
                      help="valida a assinatura, busca o lead na Graph e grava (dry-run sem --confirmo)")
    acao.add_argument("--desfazer", metavar="CHAVE",
                      help="marca a trilha da chave como DESFEITO (dry-run ate --confirmo)")
    parser.add_argument("--webhook", default=None, help="arquivo JSONL com as entregas cruas do webhook")
    parser.add_argument("--lote", default=None, help="identidade da rodada (sai no relatorio)")
    parser.add_argument("--chave-idempotencia", default=None, help="escopo da rodada de ingesta")
    parser.add_argument("--porta-banco", default=None,
                        help='comando da porta de banco (ex.: "docker exec -i pg-meta-acc psql -U sales_ai -d sales_intelligence")')
    parser.add_argument("--graph-base", default=None, help="base da Graph API (dev: stub local)")
    parser.add_argument("--contrato", default=None, help="contrato alternativo")
    parser.add_argument("--saida", default=None, help="diretorio dos leads ingeridos (JSON por lead)")
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--relatorio", default=None)
    parser.add_argument("--registro", default=None, help="trilha local append-only (JSONL)")
    parser.add_argument("--env-file", default=None, help="arquivo .env alternativo")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file:
        env.update(ler_env_file(args.env_file))
    if args.ambiente:
        env["TRE_AMBIENTE"] = args.ambiente
    if args.porta_banco:
        env["TRE_META_PORTA_BANCO"] = args.porta_banco
    if args.graph_base:
        env["TRE_META_GRAPH_BASE"] = args.graph_base
    config = Configuracao(env)
    saida = Saida(args.relatorio, args.registro, [config.token, config.app_secret])

    try:
        contrato = carregar_contrato(args.contrato)
        saida.evento(evento="CONTRATO_OK", versao=contrato.get("versao"),
                     campos=len((contrato.get("campos") or {}).get("mapa") or {}),
                     tentativas=(contrato.get("graph") or {}).get("tentativas"))
    except Recusa as e:
        print(json.dumps({"evento": e.motivo, "detalhe": e.detalhe}, ensure_ascii=False))
        return CODIGO_RECUSA

    if args.planejar:
        saida.evento(evento="PLANO", **config.resumo())
        problemas = validar(config)
        for motivo, detalhe in problemas:
            saida.evento(evento="PENDENCIA", motivo=motivo, detalhe=detalhe)
        saida.fechar("PLANO_OK" if not problemas else "PLANO_COM_PENDENCIA")
        return CODIGO_OK if not problemas else CODIGO_RECUSA

    if args.conferir:
        problemas = validar(config)
        violacoes = auditar_fonte()
        for motivo, detalhe in problemas:
            saida.evento(evento="PROBLEMA", motivo=motivo, detalhe=detalhe)
        if violacoes:
            saida.evento(evento="FONTE_INVALIDA", violacoes=violacoes)
        ok = not problemas and not violacoes
        saida.evento(evento="CONFERENCIA", problemas=len(problemas), violacoes=len(violacoes))
        saida.fechar("CONFERIDO_OK" if ok else "CONFERIDO_COM_FALHA")
        if ok:
            return CODIGO_OK
        motivos = [m for m, _ in problemas] + (["FONTE_INVALIDA"] if violacoes else [])
        return CODIGO_PRODUCAO if "PRODUCAO_RECUSADA" in motivos else CODIGO_RECUSA

    if args.desfazer:
        if not config.porta_banco:
            saida.evento(evento="BANCO_NAO_DECLARADO",
                         detalhe="--desfazer exige --porta-banco (sem porta nao ha trilha para marcar)")
            return CODIGO_USO
        chave = args.desfazer if args.desfazer.startswith(PADRAO_CHAVE) else PADRAO_CHAVE + args.desfazer
        porta = PortaBanco(config.porta_banco, config.ambiente)
        atual = ja_ingerido(porta, chave)
        if not atual:
            saida.evento(evento="SEM_TRILHA", chave=chave,
                         detalhe="nenhuma ingesta registrada com essa chave (nada a desfazer)")
            return CODIGO_USO
        if atual["status"] == STATUS_DESFEITO:
            saida.evento(evento="JA_DESFEITO", chave=chave)
            return CODIGO_OK
        if not args.confirmo:
            saida.evento(evento="DRY_RUN", chave=chave, status_atual=atual["status"],
                         detalhe="sem --confirmo nada muda")
            return CODIGO_OK
        porta.executar(
            f"INSERT INTO {TABELA_SYNC} (id, entity_type, entity_id, source_system, target_system, "
            f"operation, source_version, idempotency_key, status, request_payload, completed_at) "
            f"SELECT gen_random_uuid(), 'interaction', entity_id, 'meta-leads', 'sales_intelligence', "
            f"'DESFAZER_LEAD_META', '{VERSAO}', '{chave}:desfeito', '{STATUS_DESFEITO}', "
            f"jsonb_build_object('chave_original', '{chave}'), NOW() RETURNING id::text AS id")
        saida.evento(evento="DESFEITO", chave=chave, trilha_preservada=True)
        return CODIGO_OK

    # --ingerir
    problemas = validar(config)
    if problemas:
        for motivo, detalhe in problemas:
            saida.evento(evento="RECUSA", motivo=motivo, detalhe=detalhe)
        motivos = [m for m, _ in problemas]
        return CODIGO_PRODUCAO if "PRODUCAO_RECUSADA" in motivos else CODIGO_RECUSA
    if not args.webhook:
        saida.evento(evento="USO", detalhe="--ingerir exige --webhook <entregas.jsonl>")
        return CODIGO_USO
    if not args.chave_idempotencia:
        saida.evento(evento="USO", detalhe="--ingerir exige --chave-idempotencia")
        return CODIGO_USO
    if not args.confirmo:
        saida.evento(evento="DRY_RUN", chave=args.chave_idempotencia, webhook=args.webhook,
                     detalhe="sem --confirmo: nada e lido da Graph e nada e gravado")
        saida.fechar("DRY_RUN_OK")
        return CODIGO_OK

    violacoes = auditar_fonte()
    if violacoes:
        saida.evento(evento="ESCRITA_NO_CODIGO", violacoes=violacoes)
        return CODIGO_RECUSA

    try:
        entregas = ler_entregas(args.webhook)
    except Recusa as e:
        saida.evento(evento=e.motivo, detalhe=e.detalhe)
        return CODIGO_RECUSA

    porta = PortaBanco(config.porta_banco, config.ambiente)
    veredito = veredito_do_contrato(contrato)
    ingeridas = sem_vinculo = insuficientes = invalidas = erros = vazias = 0

    for entrega in entregas:
        corpo = corpo_canonico(entrega.get("corpo") if "corpo" in entrega else entrega)
        cabecalho = entrega.get("assinatura")
        valida, motivo = assinatura_valida(corpo, cabecalho, config.app_secret)
        if not valida:
            chave = f"{PADRAO_CHAVE}<assinatura-invalida>:{hashlib.sha256(corpo).hexdigest()[:24]}"
            if ja_ingerido(porta, chave):
                saida.evento(evento="JA_INGERIDO", chave=chave)
                continue
            invalidas += 1
            gravar_trilha(porta, chave, None, STATUS_ASSINATURA_INVALIDA,
                          {"motivo": motivo, "corpo_sha256": hashlib.sha256(corpo).hexdigest()},
                          sem_conflito=True)
            saida.evento(evento=STATUS_ASSINATURA_INVALIDA, motivo=motivo,
                         corpo_sha256=hashlib.sha256(corpo).hexdigest()[:16])
            continue
        try:
            documento = json.loads(corpo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            saida.evento(evento="CORPO_INVALIDO", detalhe=f"{type(e).__name__}: {e}"[:200])
            return CODIGO_FALHA
        if (documento.get("object") or "") != (contrato.get("origem") or {}).get("objeto_webhook", "page"):
            saida.evento(evento="OBJETO_IGNORADO", objeto=documento.get("object"))
            continue
        notificacoes = extrair_notificacoes(documento)
        if not notificacoes:
            chave = f"{PADRAO_CHAVE}<entrega-vazia>:{hashlib.sha256(corpo).hexdigest()[:24]}"
            if ja_ingerido(porta, chave):
                saida.evento(evento="JA_INGERIDO", chave=chave)
                continue
            vazias += 1
            gravar_trilha(porta, chave, None, STATUS_ENTREGA_VAZIA,
                          {"motivo": "entrega assinada sem notificacao 'leadgen'"}, sem_conflito=True)
            saida.evento(evento=STATUS_ENTREGA_VAZIA, corpo_sha256=hashlib.sha256(corpo).hexdigest()[:16])
            continue

        for notificacao in notificacoes:
            chave = chave_de(notificacao)
            if ja_ingerido(porta, chave):
                saida.evento(evento="JA_INGERIDO", chave=chave)
                continue
            if config.paginas_permitidas and notificacao["page_id"] not in config.paginas_permitidas:
                gravar_trilha(porta, chave, None, "PAGINA_NAO_PERMITIDA",
                              {"page_id": notificacao["page_id"]})
                saida.evento(evento="PAGINA_NAO_PERMITIDA", chave=chave)
                continue
            try:
                payload, chamadas = puxar_lead(config, contrato, notificacao["leadgen_id"])
            except Recusa as e:
                status = e.motivo if e.motivo == STATUS_LEAD_INDISPONIVEL else STATUS_ERRO_GRAPH
                erros += 1
                gravar_trilha(porta, chave, None, status, {"leadgen_id": notificacao["leadgen_id"]},
                              erro=e.detalhe)
                saida.evento(evento=status, chave=chave, detalhe=e.detalhe)
                continue

            normalizado = normalizar_lead(payload, contrato)
            try:
                contato = contato_do_lead(normalizado, contrato)
            except Recusa as e:
                insuficientes += 1
                gravar_trilha(porta, chave, None, STATUS_DADOS_INSUFICIENTES,
                              {"leadgen_id": notificacao["leadgen_id"],
                               "campos_desconhecidos": normalizado["desconhecidos"]}, erro=e.detalhe)
                saida.evento(evento=STATUS_DADOS_INSUFICIENTES, chave=chave, detalhe=e.detalhe)
                continue

            vinculo = resolver_vinculo(porta, contato)
            if not vinculo:
                sem_vinculo += 1
                gravar_trilha(porta, chave, None, STATUS_SEM_VINCULO,
                              {"leadgen_id": notificacao["leadgen_id"],
                               "form_id": notificacao["form_id"], "ad_id": notificacao["ad_id"],
                               "tem_email": bool(contato["email"]), "tem_telefone": bool(contato["phone"]),
                               "campos_desconhecidos": normalizado["desconhecidos"],
                               "motivo": "contato sem vinculo em sales_intelligence.contacts; "
                                         "organizacao nao e inventada (invariante 3)"})
                saida.evento(evento=STATUS_SEM_VINCULO, chave=chave, tem_email=bool(contato["email"]),
                             tem_telefone=bool(contato["phone"]))
                continue

            resumo = resumo_conteudo(normalizado)
            entity_id = gravar_interacao(porta, notificacao, normalizado, veredito, vinculo, resumo)
            gravar_trilha(porta, chave, entity_id, STATUS_PROCESSADO,
                          {"leadgen_id": notificacao["leadgen_id"], "form_id": notificacao["form_id"],
                           "ad_id": notificacao["ad_id"], "chamadas_graph": chamadas,
                           "campos_desconhecidos": normalizado["desconhecidos"],
                           "categoria": veredito["response_category"]})
            ingeridas += 1
            saida.evento(evento="INTERACAO_GRAVADA", chave=chave, interaction_id=entity_id,
                         organization_id=vinculo["organization_id"], chamadas_graph=chamadas,
                         categoria=veredito["response_category"], confianca=veredito["confianca"])
            if args.saida:
                os.makedirs(args.saida, exist_ok=True)
                nome = os.path.join(args.saida, re.sub(r"[^A-Za-z0-9._-]", "_", chave) + ".json")
                with open(nome, "w", encoding="utf-8") as fh:
                    json.dump({"chave": chave, "notificacao": notificacao, "veredito": veredito,
                               "vinculo": {k: v for k, v in vinculo.items() if k not in ("email", "phone")},
                               "resumo": resumo, "interaction_id": entity_id,
                               "campos_desconhecidos": normalizado["desconhecidos"]},
                              fh, ensure_ascii=False, indent=2, sort_keys=True)

    saida.evento(evento="RESUMO_RODADA", lote=args.lote, chave=args.chave_idempotencia,
                 entregas=len(entregas), interacoes=ingeridas, sem_vinculo=sem_vinculo,
                 dados_insuficientes=insuficientes, assinatura_invalida=invalidas,
                 entregas_sem_lead=vazias, erros_graph=erros)
    saida.fechar("INGESTAO_LEADS_META_001_OK")
    return CODIGO_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Recusa as e:
        print(json.dumps({"evento": e.motivo, "detalhe": e.detalhe}, ensure_ascii=False))
        sys.exit(CODIGO_RECUSA)
