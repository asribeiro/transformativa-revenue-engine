#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ingestao e classificacao de respostas v1 (`ingestao-respostas-v1`) — card TRE-W6-E05-T01.

O que este componente FAZ (e so isto): pega as mensagens que o primitivo de LEITURA do Titan IMAP
(`hermes/integracoes/titan/imap_titan.py`, card TRE-W6-E01-T02) traz da caixa, decide o que cada uma E
(resposta comercial, recusa, descadastro, bounce, auto-resposta, ruido comercial ou indefinido) por
REGRA DECLARADA em contrato versionado — sem LLM — e grava o resultado em
`sales_intelligence.interactions` (+ a trilha de idempotencia em `sales_intelligence.sync_events`),
que e a evidencia que o card seguinte (W6-E06, atualizar o Odoo a partir das respostas) consome.

Invariantes deste componente (cada um com item de aceite):

  1. LEITURA NAO ESCREVE (herdado de W6-E01-T02): a caixa e aberta com EXAMINE (`readonly=True`), todo
     conteudo vem de `BODY.PEEK` e a auditoria da propria fonte RECUSA (`ESCRITA_NO_CODIGO`, exit 3)
     antes de conectar. Este componente NAO chama STORE/EXPUNGE/COPY/MOVE/DELETE/APPEND — nem para
     "marcar como processada": a identidade da mensagem vive na trilha de idempotencia, nao na caixa.
  2. CLASSIFICACAO DECLARADA, NUNCA INVENTADA: as categorias, intencoes, sentimentos e os padroes de
     cada regra estao no contrato `hermes/agentes/respostas/ingestao-respostas-v1.json`. O codigo nao
     carrega vocabulario proprio: categoria fora da lista fechada, regra sem categoria valida ou
     contrato ausente RECUSAM (`CONTRATO_INVALIDO`, exit 3) em vez de "achar parecido".
  3. LIMITE DE ESCRITA: so `interactions` e `sync_events` sao tocadas, e so por INSERT (a trilha de
     idempotencia e append). UPDATE/DELETE/TRUNCATE/DDL nao existem no modulo — a auditoria de fonte
     procura esses comandos e RECUSA antes de qualquer conexao (`DDL_NO_CODIGO`, exit 3).
  4. VINCULO NAO SE INVENTA: a resposta e ligada a uma `organization_id` (NOT NULL no contrato) pelo
     e-mail do remetente em `contacts`; remetente desconhecido NAO vira organizacao nova — a mensagem
     fica registrada em `sync_events` com `status = SEM_VINCULO` (auditavel, elegivel a reprocesso) e
     nenhuma linha e inventada em `interactions`.
  5. IDEMPOTENCIA: a chave e `resposta:<UIDVALIDITY:UID>` (indice unico em `sync_events`). Replay da
     mesma mensagem devolve `JA_INGERIDO`, nao grava de novo e **nao relê o corpo** da caixa.
  6. SEGREDO: senha do IMAP so por `TRE_TITAN_PASSWORD`; a porta de banco vem de variavel de ambiente
     (`TRE_RESPOSTAS_PORTA_BANCO`) ou de `--porta-banco`, nunca com valor de senha na linha de comando
     do proprio componente; a gravacao confere e RECUSA se o valor da senha do IMAP aparecer
     (`SENHA_VAZADA`, exit 5).
  7. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige sink IMAP em LOOPBACK e login
     do dominio de dev (`HOST_NAO_E_DEV` / `USUARIO_NAO_DEV`) e porta de banco LOCAL (apenas container
     de dev/aceite via `docker exec` — `BANCO_NAO_E_DEV` recusa prefixo remoto); `homolog` exige
     aprovacao humana registrada (`TRE_TITAN_APROVACAO_HUMANA`) e caixa na lista explicita; `prod`
     RECUSA por desenho (exit 4).

O que ele NAO faz, por desenho (declarado no contrato -> `lacunas`):
  - **nao responde** nada e **nao aprova** nada: envio e W6-E04, decisao humana e W6-E03;
  - **nao escreve no Odoo** (W6-E06) e **nao muda `contacts.opt_out_email`/`do_not_contact`**: o dono
    operacional do contato e o Odoo (Data Contract V1, doc 05 §4). Aqui o descadastro e CLASSIFICADO
    (`response_category = OPT_OUT`) e fica na `interactions` — a propagacao e do card seguinte;
  - **nao apaga nem marca nada no servidor de e-mail** (invariante 1);
  - **nao usa LLM**: a classificacao e por regra declarada com padrao, prioridade e confianca
    explicita; sem regra que case, a categoria e `INDEFINIDO` com confianca 0 (nunca um palpite).

Deteccao antes do palpite (nesta ordem, definida no contrato):
  1. `BOUNCE` — cabecalho de relatorio de entrega (`multipart/report`, `MAILER-DAEMON`/`postmaster`,
     assunto de falha de entrega);
  2. `AUTO_RESPOSTA` — `Auto-Submitted: auto-replied|auto-generated`, `X-Autoreply`, assunto de ausencia;
  3. `OPT_OUT` — pedido de descadastro/nao-contato no corpo SEM citacao (conservador: vence sobre
     qualquer sinal de interesse; e o caminho em que errar para menos custa compliance);
  4. `SEM_INTERESSE` — recusa explicita;
  5. `INTERESSE` — pedido de conversa/proposta/mais informacao;
  6. `RUIDO` — newsletter, propaganda nao solicitada, aviso de sistema;
  7. `INDEFINIDO` — nada casou.
  Antes das regras, a EXCLUSAO `NAO_RESPOSTA` tira da conta o que nao e resposta de lead (remetente do
  proprio dominio, remetente vazio) — nao vira `interactions`.
  A classificacao roda sobre o corpo LIMPO (citacao removida, assinatura removida, HTML convertido em
  texto) e sobre o assunto: um "nao quero receber" que so existe na citacao do nosso proprio e-mail
  (historico) NAO classifica a resposta como descadastro — e o dente que prova o limite.

Uso (dev nao tem credencial Titan: a prova em dev e contra sink local; contra o provedor e em homolog):

  python3 hermes/agentes/respostas/ingestao_respostas.py --planejar
  python3 hermes/agentes/respostas/ingestao_respostas.py --conferir
  python3 hermes/agentes/respostas/ingestao_respostas.py --classificar "--texto: Podemos conversar?"
  python3 hermes/agentes/respostas/ingestao_respostas.py --ingerir --saida /tmp/respostas \\
      --chave-idempotencia "w6-e05:rodada-1" --porta-banco "docker exec -i pg-resp-acc psql -U sales_ai -d sales_intelligence" [--confirmo]
  python3 hermes/agentes/respostas/ingestao_respostas.py --desfazer "UIDVALIDITY:UID" [--confirmo]

Exit: 0 = OK/DRY_RUN/replay · 1 = falha de execucao (IMAP/banco) · 2 = uso · 3 = recusa de
guarda/contrato/fonte · 4 = recusa de producao · 5 = senha vazada (recusa de gravacao).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", "..", ".."))
sys.path.insert(0, os.path.join(RAIZ, "hermes", "integracoes", "titan"))
sys.path.insert(0, os.path.join(RAIZ, "scripts", "integracoes"))

VERSAO = "ingestao-respostas-v1"
CONTRATO_PADRAO = os.path.join(AQUI, "ingestao-respostas-v1.json")
FONTE_IMAP = os.path.join(RAIZ, "hermes", "integracoes", "titan", "imap_titan.py")

CODIGO_OK = 0
CODIGO_FALHA = 1
CODIGO_USO = 2
CODIGO_RECUSA = 3
CODIGO_PRODUCAO = 4
CODIGO_SEGREDO = 5

AMBIENTES = ("dev", "homolog", "prod")
TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_SYNC = "sales_intelligence.sync_events"
DOMINIO_DEV_PADRAO = "dev.local"
CAIXA_PADRAO = "INBOX"

# Prefixos ACEITOS para escrever no banco (invariante 7): em dev a escrita so pode cair em container
# local de dev/aceite. Prefixo remoto (ssh, host=, -h com host externo) e RECUSADO por medicao, nao
# por confianca: o aceite mede o caso nao-local e exige BANCO_NAO_E_DEV.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(sales|odoo|resp|respostas)[A-Za-z0-9._-]*$")


def mascarar(valor: str | None) -> str:
    if not valor:
        return ""
    return valor[:2] + "*" * max(0, len(valor) - 2) if len(valor) > 6 else "<oculta>"


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def loopback(host: str) -> bool:
    return (host or "").strip().lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def dominio(endereco: str) -> str:
    m = re.search(r"@([A-Za-z0-9._-]+)", endereco or "")
    return m.group(1).lower() if m else ""


class Recusa(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe


# --------------------------------------------------------------------------------------------
# 1. Fonte: invariantes verificaveis no proprio arquivo (como no card do IMAP)
# --------------------------------------------------------------------------------------------
def padroes_de_escrita_proibida() -> dict:
    """Monta os padroes em tempo de execucao (montados literalmente nao se encontram a si mesmos)."""
    return {
        "sql_de_escrita_crua": ["DE" + "LETE FROM", "UP" + "DATE ", "TRUN" + "CATE", "DR" + "OP ",
                                "AL" + "TER TABLE"],
        "comando_imap_de_escrita": ["ST" + "ORE", "EXPU" + "NGE", "APPE" + "ND"],
        "busca_sem_peek": ["BODY[" + "]"],
    }


def auditar_fonte(caminho: str | None = None) -> list:
    """Auditoria da propria fonte (invariantes 1 e 3). Devolve a lista de violacoes."""
    alvo = caminho or os.path.abspath(__file__)
    with open(alvo, "r", encoding="utf-8") as fh:
        linhas = fh.readlines()
    texto_util = []
    dentro_doc = False
    for linha in linhas:
        t = linha.strip()
        # docstrings e comentarios DECLARAM as regras; nao podem ser confundidos com a regra quebrada.
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
    if "readonly=True" not in corpo and "auditar_sem_escrita" not in corpo:
        violacoes.append({"padrao": "examine_readonly",
                          "alvo": "nem readonly=True nem a auditoria do primitivo (auditar_sem_escrita) no modulo"})
    return violacoes


# --------------------------------------------------------------------------------------------
# 2. Configuracao (IMAP herdada + porta de banco) e guardas de ambiente
# --------------------------------------------------------------------------------------------
class Configuracao:
    def __init__(self, env: dict):
        self.ambiente = (env.get("TRE_AMBIENTE") or "dev").strip().lower()
        self.imap_host = (env.get("TRE_TITAN_IMAP_HOST") or "").strip()
        self.imap_porta = (env.get("TRE_TITAN_IMAP_PORT") or "").strip()
        self.imap_seguranca = (env.get("TRE_TITAN_IMAP_SEGURANCA") or "").strip()
        self.imap_caixa = (env.get("TRE_TITAN_IMAP_CAIXA") or CAIXA_PADRAO).strip() or CAIXA_PADRAO
        self.usuario = (env.get("TRE_TITAN_USER") or "").strip()
        self.senha = env.get("TRE_TITAN_PASSWORD") or ""
        self.ca = (env.get("TRE_TITAN_CA") or "").strip()
        self.limite = (env.get("TRE_TITAN_IMAP_LIMITE") or "50").strip()
        self.timeout = (env.get("TRE_TITAN_TIMEOUT") or "10").strip()
        self.dominio_dev = (env.get("TRE_TITAN_DOMINIO_DEV") or DOMINIO_DEV_PADRAO).strip()
        self.caixas_permitidas = [c.strip() for c in (env.get("TRE_TITAN_CAIXAS_PERMITIDAS") or "").split(",") if c.strip()]
        self.aprovacao = (env.get("TRE_TITAN_APROVACAO_HUMANA") or "").strip()
        self.porta_banco = (env.get("TRE_RESPOSTAS_PORTA_BANCO") or "").strip()
        self.remetente_proprio = (env.get("TRE_RESPOSTAS_REMETENTE_PROPRIO") or "transformativa.com.br").strip()

    def resumo(self) -> dict:
        return {
            "versao": VERSAO,
            "ambiente": self.ambiente,
            "imap": {"host": self.imap_host, "porta": self.imap_porta, "seguranca": self.imap_seguranca,
                     "caixa": self.imap_caixa, "usuario": self.usuario, "senha": mascarar(self.senha),
                     "ca": self.ca or "(sem CA propria)", "limite": self.limite,
                     "aprovacao_humana": self.aprovacao or "(ausente)",
                     "caixas_permitidas": self.caixas_permitidas},
            "porta_banco": self.porta_banco or "(ausente)",
            "remetente_proprio": self.remetente_proprio,
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
    faltantes = [nome for nome, valor in (("TRE_TITAN_IMAP_HOST", config.imap_host),
                                          ("TRE_TITAN_IMAP_PORT", config.imap_porta),
                                          ("TRE_TITAN_IMAP_SEGURANCA", config.imap_seguranca),
                                          ("TRE_TITAN_USER", config.usuario),
                                          ("TRE_TITAN_PASSWORD", config.senha)) if not valor]
    if faltantes:
        problemas.append(("CONFIG_INCOMPLETA", "faltam: " + ", ".join(faltantes)))
    if config.imap_porta and not config.imap_porta.isdigit():
        problemas.append(("CONFIG_INCOERENTE", f"porta IMAP nao numerica: {config.imap_porta}"))
    if config.imap_seguranca and config.imap_seguranca not in ("implicit_tls", "starttls", "nenhuma"):
        problemas.append(("CONFIG_INCOERENTE", f"seguranca IMAP invalida: {config.imap_seguranca}"))
    if config.ambiente == "dev":
        if config.imap_host and not loopback(config.imap_host):
            problemas.append(("HOST_NAO_E_DEV", f"dev so fala com sink local; host={config.imap_host}"))
        if config.usuario and dominio(config.usuario) != config.dominio_dev:
            problemas.append(("USUARIO_NAO_DEV",
                              f"dev so autentica no dominio {config.dominio_dev}; login={config.usuario}"))
        if not config.porta_banco:
            problemas.append(("BANCO_NAO_DECLARADO",
                              "dev exige --porta-banco/TRE_RESPOSTAS_PORTA_BANCO com container local"))
        else:
            m = RE_CONTAINER_LOCAL.match(config.porta_banco.strip())
            if not m or not CONTAINERS_LOCAIS_DEV.match(m.group(1)):
                problemas.append(("BANCO_NAO_E_DEV",
                                  "dev escreve apenas em container local de dev/aceite via docker exec; "
                                  f"prefixo recusado: {config.porta_banco}"))
    if config.ambiente == "homolog":
        if not config.aprovacao:
            problemas.append(("HOMOLOG_SEM_APROVACAO",
                              "homolog exige TRE_TITAN_APROVACAO_HUMANA registrada"))
        if not config.caixas_permitidas:
            problemas.append(("CAIXA_NAO_PERMITIDA", "homolog exige lista explicita de caixas"))
        elif config.imap_caixa not in config.caixas_permitidas:
            problemas.append(("CAIXA_NAO_PERMITIDA", f"caixa {config.imap_caixa} fora da lista"))
    return problemas


# --------------------------------------------------------------------------------------------
# 3. Contrato (vocabulario e regras) — declarado, nunca inventado
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
    for campo in ("response_category", "intent", "sentiment"):
        valores = vocab.get(campo)
        if not isinstance(valores, list) or not valores:
            falhas.append(f"vocabulario.{campo} ausente/vazio")
        elif len(set(valores)) != len(valores):
            falhas.append(f"vocabulario.{campo} com repeticao")
    regras = contrato.get("regras")
    if not isinstance(regras, list) or not regras:
        falhas.append("regras ausentes")
        return falhas
    categorias = set(vocab.get("response_category") or [])
    ordens = set()
    for i, regra in enumerate(regras):
        if not isinstance(regra, dict):
            falhas.append(f"regra {i} nao e objeto")
            continue
        if regra.get("categoria") not in categorias:
            falhas.append(f"regra {i} categoria fora do vocabulario: {regra.get('categoria')}")
        if not isinstance(regra.get("ordem"), int):
            falhas.append(f"regra {i} sem ordem inteira")
        elif regra["ordem"] in ordens:
            falhas.append(f"regra {i} ordem repetida: {regra['ordem']}")
        else:
            ordens.add(regra["ordem"])
        if regra.get("intent") and regra["intent"] not in set(vocab.get("intent") or []):
            falhas.append(f"regra {i} intent fora do vocabulario: {regra['intent']}")
        if regra.get("sentiment") and regra["sentiment"] not in set(vocab.get("sentiment") or []):
            falhas.append(f"regra {i} sentiment fora do vocabulario: {regra['sentiment']}")
        if not isinstance(regra.get("confianca"), (int, float)) or not 0 <= regra["confianca"] <= 1:
            falhas.append(f"regra {i} confianca fora de [0,1]")
        deteccao = regra.get("deteccao") or {}
        if not deteccao.get("padroes") and not deteccao.get("cabecalhos") and not deteccao.get("assunto"):
            falhas.append(f"regra {i} sem padrao, cabecalho ou assunto declarado")
        # Padrao VAZIO casa qualquer texto (`"" in "..."`): aceitar isso seria uma regra que classifica
        # tudo. O contrato RECUSA; presenca de cabecalho se declara com "*".
        for campo in ("padroes", "assunto"):
            for j, valor in enumerate(deteccao.get(campo) or []):
                if not str(valor).strip():
                    falhas.append(f"regra {i} {campo}[{j}] vazio (casaria qualquer mensagem)")
                elif campo == "padroes":
                    try:
                        re.compile(normalizar(str(valor)))
                    except re.error as e:
                        falhas.append(f"regra {i} padrao invalido '{valor}': {e}")
        for chave, valores in (deteccao.get("cabecalhos") or {}).items():
            for j, valor in enumerate(valores or []):
                if not str(valor).strip():
                    falhas.append(f"regra {i} cabecalhos[{chave}][{j}] vazio (casaria qualquer mensagem)")
    for i, exclusao in enumerate(contrato.get("exclusoes") or []):
        if not isinstance(exclusao, dict) or not exclusao.get("motivo"):
            falhas.append(f"exclusao {i} sem motivo declarado")
    return falhas


def normalizar(texto: str) -> str:
    """Minusculas sem acento: o padrao do contrato casa igual em texto acentuado ou nao."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto or "")
                         if unicodedata.category(c) != "Mn")
    return sem_acento.lower()


def limpar_assinatura(texto: str) -> str:
    linhas = []
    for linha in (texto or "").splitlines():
        if linha.strip() in ("--", "-- ", "- --") or linha.strip().startswith("-- "):
            break
        linhas.append(linha)
    return "\n".join(linhas)


def remover_citacao(texto: str) -> str:
    """Tira a citacao e o historico: o que o lead ESCREVEU e o que vem antes deles."""
    linhas = []
    for linha in (texto or "").splitlines():
        t = linha.strip()
        if t.startswith(">"):
            continue
        if re.match(r"^(em|on)\s.{0,200}(escreveu|wrote):\s*$", normalizar(t)):
            break
        if re.match(r"^-{2,}\s*(mensagem original|original message|forwarded message|repasse)\b", normalizar(t)):
            break
        if re.match(r"^(de|from|enviada em|sent):\s", normalizar(t)) and len(linhas) > 0:
            break
        linhas.append(linha)
    return "\n".join(linhas).strip()


def html_para_texto(corpo: str) -> str:
    texto = re.sub(r"(?is)<(script|style).*?</\1>", " ", corpo or "")
    texto = re.sub(r"(?i)<br\s*/?>", "\n", texto)
    texto = re.sub(r"(?i)</p\s*>", "\n", texto)
    texto = re.sub(r"(?s)<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    # Espaço em branco colapsado: markup (`<b>`, `<span>`) quebra palavra no meio e o padrao do
    # contrato casa TEXTO, nao markup.
    return re.sub(r"[ \t]+", " ", "\n".join(l.strip() for l in texto.splitlines()))


def partes_da_mensagem(bruto: bytes) -> tuple:
    """Devolve (cabecalhos, texto, html, anexos) da mensagem crua, em stdlib."""
    msg = message_from_bytes(bruto, policy=policy.default)
    cabecalhos = {k: str(v) for k, v in msg.items()}
    textos, htmls, anexos = [], [], []
    if msg.is_multipart():
        for parte in msg.walk():
            tipo = parte.get_content_type()
            disp = (parte.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                anexos.append({"tipo": tipo, "nome": parte.get_filename() or ""})
                continue
            if tipo == "text/plain":
                try:
                    textos.append(parte.get_content())
                except (LookupError, ValueError, KeyError):
                    textos.append(parte.get_payload(decode=True).decode("utf-8", "replace"))
            elif tipo == "text/html":
                try:
                    htmls.append(parte.get_content())
                except (LookupError, ValueError, KeyError):
                    htmls.append(parte.get_payload(decode=True).decode("utf-8", "replace"))
    else:
        try:
            conteudo = msg.get_content()
        except (LookupError, ValueError, KeyError):
            conteudo = (msg.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if msg.get_content_type() == "text/html":
            htmls.append(conteudo)
        else:
            textos.append(conteudo)
    return cabecalhos, "\n".join(textos), "\n".join(htmls), anexos


def classificar(mensagem: dict, contrato: dict, remetente_proprio: str,
                aplicar_exclusoes: bool = True) -> dict:
    """Classifica UMA mensagem pelas regras do contrato. Devolve o veredito com a evidencia.

    `mensagem` = {"de", "assunto", "texto", "html", "cabecalhos", "tem_anexo"}.
    Ordem: exclusoes -> regras por `ordem` (menor vence) -> INDEFINIDO (confianca 0).
    """
    de = (mensagem.get("de") or "").strip()
    assunto = mensagem.get("assunto") or ""
    texto_bruto = mensagem.get("texto") or ""
    if (not texto_bruto or not texto_bruto.strip()) and (mensagem.get("html") or "").strip():
        texto_bruto = html_para_texto(mensagem["html"])
    corpo_limpo = limpar_assinatura(remover_citacao(texto_bruto))
    texto_norm = normalizar(corpo_limpo)
    assunto_norm = normalizar(assunto)

    for i, exclusao in enumerate(contrato.get("exclusoes") or []):
        if not aplicar_exclusoes:
            break
        campos = exclusao.get("campos") or []
        if "remetente_proprio" in campos and remetente_proprio and \
                dominio(de).endswith(remetente_proprio.lower()):
            return {"categoria": "NAO_RESPOSTA", "intent": None, "sentiment": None, "confianca": 0.0,
                    "excluida": True, "motivo": f"{exclusao['motivo']} (exclusao {i})",
                    "regra": None, "campos_que_casaram": ["remetente_proprio"]}
        if "remetente_vazio" in campos and not de:
            return {"categoria": "NAO_RESPOSTA", "intent": None, "sentiment": None, "confianca": 0.0,
                    "excluida": True, "motivo": f"{exclusao['motivo']} (exclusao {i})",
                    "regra": None, "campos_que_casaram": ["remetente_vazio"]}

    cabecalhos = {normalizar(k): str(v) for k, v in (mensagem.get("cabecalhos") or {}).items()}
    for regra in sorted(contrato["regras"], key=lambda r: r["ordem"]):
        deteccao = regra.get("deteccao") or {}
        casados = []
        for chave, valores in (deteccao.get("cabecalhos") or {}).items():
            atual = cabecalhos.get(normalizar(chave), "")
            for valor in valores:
                if not valor:
                    # Valor vazio casaria QUALQUER mensagem (`"" in "..."` e sempre verdade): o
                    # contrato valida e RECUSA padrao vazio; aqui ele nunca e aceito em silencio.
                    continue
                if valor == "*":
                    if atual:
                        casados.append(f"cabecalho {chave} presente")
                elif valor.lower() in atual.lower():
                    casados.append(f"cabecalho {chave}~{valor}")
        for valor in deteccao.get("assunto") or []:
            if re.search(normalizar(valor), assunto_norm):
                casados.append(f"assunto~{valor}")
        for valor in deteccao.get("padroes") or []:
            campos = deteccao.get("campos") or ["corpo"]
            alvos = {"corpo": texto_norm, "assunto": assunto_norm, "assunto_corpo": assunto_norm + "\n" + texto_norm}
            for campo in campos:
                if re.search(normalizar(valor), alvos.get(campo, "")):
                    casados.append(f"{campo}~{valor}")
        minimo = int(deteccao.get("minimo", len(deteccao.get("padroes") or []) > 0 and 1 or 1))
        if len(casados) >= minimo and (casados or deteccao.get("minimo") == 0):
            if not casados:
                continue
            confianca = float(regra["confianca"])
            if deteccao.get("diminui_com_citacao") and texto_bruto.strip() != corpo_limpo.strip():
                confianca = round(max(0.0, confianca - 0.1), 4)
            return {"categoria": regra["categoria"], "intent": regra.get("intent"),
                    "sentiment": regra.get("sentiment"), "confianca": confianca, "excluida": False,
                    "motivo": regra.get("descricao", ""), "regra": regra["ordem"],
                    "campos_que_casaram": casados}
    return {"categoria": "INDEFINIDO", "intent": None, "sentiment": None, "confianca": 0.0,
            "excluida": False, "motivo": "nenhuma regra do contrato casou (fail-closed: sem palpite)",
            "regra": None, "campos_que_casaram": []}


# --------------------------------------------------------------------------------------------
# 4. Porta de banco (comando declarado, SQL por stdin, resposta JSON)
# --------------------------------------------------------------------------------------------
class PortaBanco:
    """Acesso ao PostgreSQL pelo comando de porta declarado (`docker exec -i <container> psql ...`).

    Nao existe driver embutido: o SQL vai por stdin e a resposta volta em JSON (`json_agg`), de modo
    que a mesma porta serve para dev local (`docker exec`), para o aceite (container descartavel) e
    para o host da VPS. Sem DDL, sem UPDATE e sem DELETE — so INSERT e SELECT (invariante 3).
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
        """Escreve (INSERT ... RETURNING) e devolve as linhas afetadas."""
        comando = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c",
                                        f"SELECT coalesce(json_agg(t), '[]'::json)::text FROM ({sql}) t;"]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        linhas = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        return json.loads(linhas[-1]) if linhas else []


def resolver_vinculo(porta: PortaBanco, email: str) -> dict | None:
    """Acha contato/organizacao pelo e-mail do remetente. Nao cria nada (invariante 4)."""
    if not email:
        return None
    seguro = email.replace("'", "''").lower()
    linhas = porta.consultar(
        f"SELECT c.id::text AS contact_id, c.organization_id::text AS organization_id, "
        f"o.legal_name, o.trade_name FROM {TABELA_INTERACOES.replace('interactions', 'contacts')} c "
        f"JOIN sales_intelligence.organizations o ON o.id = c.organization_id "
        f"WHERE lower(c.email) = '{seguro}' LIMIT 1")
    return linhas[0] if linhas else None


def chave_de(mensagem: dict) -> str:
    identidade = mensagem.get("identidade_mensagem") or ""
    return "resposta:" + (identidade or hashlib.sha256(
        (mensagem.get("message_id") or mensagem.get("assunto") or "").encode("utf-8")).hexdigest()[:24])


def ja_ingerido(porta: PortaBanco, chave: str) -> dict | None:
    linhas = porta.consultar(
        f"SELECT id::text, status, entity_id::text FROM {TABELA_SYNC} "
        f"WHERE idempotency_key = '{chave.replace(chr(39), chr(39) * 2)}' LIMIT 1")
    return linhas[0] if linhas else None


def gravar_interacao(porta: PortaBanco, mensagem: dict, veredito: dict, vinculo: dict) -> str:
    def q(valor):
        if valor is None:
            return "NULL"
        return "'" + str(valor).replace("'", "''") + "'"

    ocorrido = "NOW()"
    data = mensagem.get("data") or ""
    if data:
        try:
            ocorrido = "'" + parsedate_to_datetime(data).astimezone(timezone.utc).isoformat() + "'"
        except (TypeError, ValueError, IndexError):
            ocorrido = "NOW()"
    resumo = (mensagem.get("corpo_limpo") or "")[:400]
    colunas = ("id", "organization_id", "contact_id", "channel", "direction", "interaction_type",
               "occurred_at", "subject", "content_summary", "content_reference", "sentiment", "intent",
               "response_category", "ai_confidence")
    valores = ("gen_random_uuid()", q(vinculo["organization_id"]), q(vinculo.get("contact_id")),
               "'email'", "'INBOUND'", "'EMAIL_RESPOSTA'", ocorrido, q(mensagem.get("assunto")),
               q(resumo), q(mensagem.get("identidade_mensagem")), q(veredito.get("sentiment")),
               q(veredito.get("intent")), q(veredito["categoria"]), str(float(veredito["confianca"])))
    sql = (f"INSERT INTO {TABELA_INTERACOES} ({', '.join(colunas)}) VALUES ({', '.join(valores)}) "
           f"RETURNING id::text AS id")
    linhas = porta.executar(sql)
    if not linhas:
        raise Recusa("GRAVACAO_SEM_RETORNO", "INSERT em interactions nao devolveu id")
    return linhas[0]["id"]


def gravar_trilha(porta: PortaBanco, chave: str, entity_id: str | None, status: str,
                  payload: dict, erro: str | None = None) -> None:
    def q(valor):
        if valor is None:
            return "NULL"
        return "'" + str(valor).replace("'", "''") + "'"

    sql = (f"INSERT INTO {TABELA_SYNC} (id, entity_type, entity_id, source_system, target_system, "
           f"operation, source_version, idempotency_key, status, request_payload, error_message, "
           f"completed_at) VALUES (gen_random_uuid(), 'interaction', {q(entity_id)}, 'titan-imap', "
           f"'sales_intelligence', 'INGESTAO_RESPOSTA', {q(VERSAO)}, {q(chave)}, {q(status)}, "
           f"{q(json.dumps(payload, ensure_ascii=False, sort_keys=True))}, {q(erro)}, NOW())")
    porta.executar(sql)


# --------------------------------------------------------------------------------------------
# 5. Relatorio/trilha local com checagem fail-closed de segredo
# --------------------------------------------------------------------------------------------
class Saida:
    def __init__(self, relatorio: str | None, trilha: str | None, segredo: str | None):
        self.relatorio = {"versao": VERSAO, "quando": agora(), "eventos": []}
        self.caminho_relatorio = relatorio
        self.caminho_trilha = trilha
        self.segredo = segredo

    def evento(self, **campos) -> None:
        campos.setdefault("quando", agora())
        self.relatorio["eventos"].append(campos)
        print(json.dumps(campos, ensure_ascii=False, sort_keys=True))
        if self.caminho_trilha:
            self._gravar(self.caminho_trilha, campos)

    def _conferir(self, texto: str) -> None:
        if self.segredo and len(self.segredo) >= 4 and self.segredo in texto:
            print(json.dumps({"evento": "SENHA_VAZADA", "motivo": "SENHA_VAZADA",
                              "detalhe": "o valor de TRE_TITAN_PASSWORD apareceu na gravacao; "
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
        if self.caminho_relatorio:
            os.makedirs(os.path.dirname(os.path.abspath(self.caminho_relatorio)), exist_ok=True)
            with open(self.caminho_relatorio, "w", encoding="utf-8") as fh:
                fh.write(texto + "\n")
            print(f"# relatorio: {self.caminho_relatorio}")


# --------------------------------------------------------------------------------------------
# 6. Ligacao com o primitivo de leitura do IMAP (W6-E01-T02)
# --------------------------------------------------------------------------------------------
def importar_imap():
    try:
        import imap_titan  # noqa: PLC0415  (import tardio: o modulo irmao vive em hermes/integracoes/titan)
        return imap_titan
    except ImportError as e:
        raise Recusa("IMAP_INDISPONIVEL",
                     f"primitivo de leitura do IMAP ausente (TRE-W6-E01-T02): {e}") from e


def consumir_caixa(config: Configuracao) -> dict:
    """Abre a caixa pelo primitivo do card W6-E01-T02 (EXAMINE + BODY.PEEK) e devolve as mensagens.

    Nao reimplementa IMAP: usa `imap_titan` para nao duplicar (nem afrouxar) o invariante de leitura.
    """
    imap = importar_imap()
    violacoes = imap.auditar_sem_escrita()
    if violacoes:
        raise Recusa("ESCRITA_NO_CODIGO", f"auditoria do primitivo acusou: {violacoes}")
    env = dict(os.environ)
    montada = imap.montar_configuracao(env)
    problemas = imap.validar(montada, config.ambiente)
    if problemas:
        raise Recusa("CONFIG_DO_PRIMITIVO", f"o primitivo de leitura recusou a configuracao: {problemas}")
    sessao = imap.conectar(montada)
    try:
        autenticado = imap.autenticar(sessao, montada)
        abertura = imap.abrir_caixa(sessao, montada)
        uids = imap.listar_uids(sessao, montada)
        mensagens = []
        for uid in uids:
            crua = imap.ingerir_mensagem(sessao, uid, abertura.get("uidvalidity"))
            mensagens.append(crua)
        return {"autenticado_como": autenticado, "selecao": abertura.get("modo_de_abertura"),
                "uidvalidity": abertura.get("uidvalidity"), "mensagens": mensagens}
    finally:
        try:
            sessao.logout()
        except Exception:  # noqa: BLE001 — logout nunca derruba a medicao
            pass


def mensagem_para_analise(crua: dict) -> dict:
    """Converte o registro cru do primitivo (cabecalhos + corpos) no que o classificador consome.

    O primitivo entrega `corpo_texto` (text/plain), `corpo_html` e `cabecalhos_completos`; a citacao e
    a assinatura sao removidas AQUI, porque "o que o lead escreveu" e regra de classificacao, nao de
    leitura.
    """
    texto = crua.get("corpo_texto") or ""
    htmls = crua.get("corpo_html") or ""
    cabecalhos = crua.get("cabecalhos_completos") or {}
    corpo_limpo = limpar_assinatura(remover_citacao(texto or (html_para_texto(htmls) if htmls else "")))
    return {"identidade_mensagem": crua["identidade_mensagem"], "message_id": crua.get("message_id"),
            "de": cabecalhos.get("From", crua.get("de", "")), "para": cabecalhos.get("To", ""),
            "assunto": cabecalhos.get("Subject", crua.get("assunto", "")),
            "data": cabecalhos.get("Date", crua.get("data", "")), "texto": texto, "html": htmls,
            "cabecalhos": cabecalhos, "corpo_limpo": corpo_limpo,
            "corpo_bruto": texto or html_para_texto(htmls)}


# --------------------------------------------------------------------------------------------
# 7. CLI
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingestao e classificacao de respostas v1 (TRE-W6-E05-T01)")
    parser.add_argument("--ambiente", choices=AMBIENTES, default=None)
    acao = parser.add_mutually_exclusive_group(required=True)
    acao.add_argument("--planejar", action="store_true",
                      help="mostra a configuracao efetiva (senha mascarada) e o que falta; nunca conecta")
    acao.add_argument("--conferir", action="store_true",
                      help="valida contrato, config, guardas e os invariantes da propria fonte")
    acao.add_argument("--classificar", metavar="TEXTO",
                      help="classifica um texto solto (sem rede, sem banco) e imprime o veredito")
    acao.add_argument("--ingerir", action="store_true",
                      help="le a caixa pelo primitivo do IMAP, classifica e grava (dry-run sem --confirmo)")
    acao.add_argument("--desfazer", metavar="IDENTIDADE",
                      help="marca a trilha da identidade como DESFEITO (dry-run ate --confirmo)")
    parser.add_argument("--assunto", default="", help="assunto para --classificar")
    parser.add_argument("--de", dest="remetente", default="", help="remetente para --classificar")
    parser.add_argument("--saida", default=None, help="diretorio das mensagens ingeridas (JSON por mensagem)")
    parser.add_argument("--chave-idempotencia", default=None, help="escopo da rodada de ingesta")
    parser.add_argument("--porta-banco", default=None,
                        help='comando da porta de banco (ex.: "docker exec -i pg-resp-acc psql -U sales_ai -d sales_intelligence")')
    parser.add_argument("--contrato", default=None, help="contrato alternativo de classificacao")
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--relatorio", default=None)
    parser.add_argument("--registro", default=None, help="trilha local append-only (JSONL)")
    parser.add_argument("--env-file", default=None, help="arquivo .env alternativo (default: ambiente)")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file:
        env.update(ler_env_file(args.env_file))
    if args.ambiente:
        env["TRE_AMBIENTE"] = args.ambiente
    if args.porta_banco:
        env["TRE_RESPOSTAS_PORTA_BANCO"] = args.porta_banco
    config = Configuracao(env)
    saida = Saida(args.relatorio, args.registro, config.senha)

    try:
        contrato = carregar_contrato(args.contrato)
        saida.evento(evento="CONTRATO_OK", versao=contrato.get("versao"),
                     categorias=contrato["vocabulario"]["response_category"],
                     regras=len(contrato["regras"]))
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
        return CODIGO_RECUSA if "PRODUCAO_RECUSADA" not in motivos else CODIGO_PRODUCAO

    if args.classificar:
        veredito = classificar({"de": args.remetente, "assunto": args.assunto, "texto": args.classificar,
                                "html": "", "cabecalhos": {}}, contrato, config.remetente_proprio,
                               aplicar_exclusoes=False)
        saida.evento(evento="CLASSIFICACAO", texto=args.classificar[:120], **veredito)
        saida.fechar("CLASSIFICADO")
        return CODIGO_OK

    if args.desfazer:
        if not config.porta_banco:
            saida.evento(evento="BANCO_NAO_DECLARADO",
                         detalhe="--desfazer exige --porta-banco (sem porta nao ha trilha para marcar)")
            return CODIGO_USO
        chave = "resposta:" + args.desfazer
        porta = PortaBanco(config.porta_banco, config.ambiente)
        atual = ja_ingerido(porta, chave)
        if not atual:
            saida.evento(evento="SEM_TRILHA", chave=chave,
                         detalhe="nenhuma ingesta registrada com essa identidade (nada a desfazer)")
            return CODIGO_USO
        if atual["status"] == "DESFEITO":
            saida.evento(evento="JA_DESFEITO", chave=chave)
            return CODIGO_OK
        if not args.confirmo:
            saida.evento(evento="DRY_RUN", chave=chave, status_atual=atual["status"],
                         detalhe="sem --confirmo nada muda")
            return CODIGO_OK
        porta.executar(f"INSERT INTO {TABELA_SYNC} (id, entity_type, entity_id, source_system, "
                       f"target_system, operation, source_version, idempotency_key, status, "
                       f"request_payload, completed_at) SELECT gen_random_uuid(), 'interaction', "
                       f"entity_id, 'titan-imap', 'sales_intelligence', 'DESFAZER_RESPOSTA', "
                       f"'{VERSAO}', '{chave}:desfeito', 'DESFEITO', "
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
    if not args.chave_idempotencia:
        saida.evento(evento="USO", detalhe="--ingerir exige --chave-idempotencia")
        return CODIGO_USO
    if not args.confirmo:
        saida.evento(evento="DRY_RUN", chave=args.chave_idempotencia,
                     detalhe="sem --confirmo: nada e lido da caixa e nada e gravado")
        saida.fechar("DRY_RUN_OK")
        return CODIGO_OK

    violacoes = auditar_fonte()
    if violacoes:
        saida.evento(evento="ESCRITA_NO_CODIGO", violacoes=violacoes)
        return CODIGO_RECUSA

    porta = PortaBanco(config.porta_banco, config.ambiente)
    try:
        caixa = consumir_caixa(config)
    except Recusa as e:
        saida.evento(evento=e.motivo, detalhe=e.detalhe)
        return CODIGO_RECUSA if e.motivo in ("ESCRITA_NO_CODIGO", "HOST_NAO_E_DEV", "USUARIO_NAO_DEV") else CODIGO_FALHA
    except Exception as e:  # noqa: BLE001 — falha de IMAP e falha de execucao, nao recusa
        saida.evento(evento="FALHA_IMAP", detalhe=f"{type(e).__name__}: {e}"[:300])
        saida.fechar("FALHA_IMAP")
        return CODIGO_FALHA

    saida.evento(evento="CAIXA_LIDA", selecao=caixa["selecao"], uidvalidity=str(caixa["uidvalidity"]),
                 mensagens=len(caixa["mensagens"]))
    ingeridas = excluidas = sem_vinculo = 0
    for crua in caixa["mensagens"]:
        chave = chave_de(crua)
        if ja_ingerido(porta, chave):
            saida.evento(evento="JA_INGERIDO", chave=chave)
            continue
        analise = mensagem_para_analise(crua)
        veredito = classificar(analise, contrato, config.remetente_proprio)
        if veredito["excluida"]:
            excluidas += 1
            gravar_trilha(porta, chave, None, "NAO_RESPOSTA",
                          {"motivo": veredito["motivo"], "de": analise["de"]})
            saida.evento(evento="NAO_RESPOSTA", chave=chave, motivo=veredito["motivo"])
            continue
        email = analise["de"].split("<")[-1].strip("> ").strip()
        vinculo = resolver_vinculo(porta, email)
        if not vinculo:
            sem_vinculo += 1
            gravar_trilha(porta, chave, None, "SEM_VINCULO",
                          {"de": email, "categoria": veredito["categoria"],
                           "motivo": "remetente sem contato em sales_intelligence.contacts; "
                                     "organizacao nao e inventada (invariante 4)"})
            saida.evento(evento="SEM_VINCULO", chave=chave, de=email, categoria=veredito["categoria"])
            continue
        entity_id = gravar_interacao(porta, analise, veredito, vinculo)
        gravar_trilha(porta, chave, entity_id, "PROCESSADO",
                      {"categoria": veredito["categoria"], "intent": veredito["intent"],
                       "sentiment": veredito["sentiment"], "confianca": veredito["confianca"],
                       "regra": veredito["regra"], "campos_que_casaram": veredito["campos_que_casaram"]})
        ingeridas += 1
        saida.evento(evento="INTERACAO_GRAVADA", chave=chave, interaction_id=entity_id,
                     organization_id=vinculo["organization_id"], categoria=veredito["categoria"],
                     intent=veredito["intent"], sentiment=veredito["sentiment"],
                     confianca=veredito["confianca"], regra=veredito["regra"])
        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            nome = os.path.join(args.saida, re.sub(r"[^A-Za-z0-9._-]", "_", chave) + ".json")
            with open(nome, "w", encoding="utf-8") as fh:
                json.dump({"chave": chave, "mensagem": {k: v for k, v in analise.items()
                                                        if k not in ("html", "cabecalhos")},
                           "veredito": veredito, "vinculo": vinculo, "interaction_id": entity_id},
                          fh, ensure_ascii=False, indent=2, sort_keys=True)

    saida.evento(evento="RESUMO_RODADA", chave=args.chave_idempotencia, interacoes=ingeridas,
                 nao_resposta=excluidas, sem_vinculo=sem_vinculo)
    saida.fechar("INGESTAO_RESPOSTAS_001_OK")
    return CODIGO_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Recusa as e:
        print(json.dumps({"evento": e.motivo, "detalhe": e.detalhe}, ensure_ascii=False))
        sys.exit(CODIGO_RECUSA)
