#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IMAP Titan v1 (`titan-imap-v1`) — leitura validada e guardada da caixa (card TRE-W6-E01-T02).

O que este componente FAZ (e so isto): transforma a CONFIGURACAO do IMAP do Titan (variaveis de
ambiente `TRE_TITAN_*`) em uma sessao IMAP validada — completa, coerente com a matriz porta x TLS do
provedor, permitida no ambiente em que esta rodando e sem nunca expor o segredo — e expoe o PRIMITIVO
de LEITURA da caixa que o workflow de resposta (W6-E05) vai chamar: listar envelopes e INGERIR as
mensagens novas uma unica vez cada, com trilha append-only.

A prova de conexao (`--provar`) mede saudacao, TLS, LOGIN, CAPACIDADE, EXAMINE e NOOP sem trazer
corpo de mensagem nenhuma.

INVARIANTE DE LEITURA (o que distingue este componente do SMTP irmao): o IMAP e o protocolo em que
ler costuma ESCREVER — buscar o corpo SEM PEEK marca a mensagem como lida, e um cliente desavisado
apaga, move e expurga. Este componente nao faz nada disso, e a regra e verificavel em tres camadas:
  1. a caixa e aberta com EXAMINE (`select(..., readonly=True)`), nunca com SELECT de escrita;
  2. o corpo vem sempre de `BODY.PEEK` — o servidor nao altera a flag `\\Seen`;
  3. o proprio componente AUDITA a fonte antes de qualquer conexao: se encontrar no proprio codigo
     um comando de escrita (STORE, EXPUNGE, DELETE, COPY, MOVE, APPEND) ou uma busca sem PEEK, ele
     RECUSA com `ESCRITA_NO_CODIGO` (exit 3) em vez de conectar. A auditoria monta os padroes em
     tempo de execucao justamente para nao se encontrar a si mesma, e o aceite tem um dente que
     prova que ela pega a violacao (busca sem PEEK -> item "nada marcado como lido" reprova).
  A medicao de ponta acontece no sink: as flags das mensagens sao comparadas antes e depois, e
  nenhuma pode aparecer com `\\Seen`.

O que ele NAO faz, por desenho (declarado no contrato `titan-imap-v1.json` -> lacunas):
  - **nao classifica nem responde** (W6-E05): a mensagem sai crua (cabecalhos + corpo) para o diretorio
    de saida; decidir se e resposta, bounce, opt-out ou ruido e do card seguinte. Aqui nao ha LLM;
  - **nao concede Human Approval** (W6-E03): a aprovacao e uma ENTRADA (`TRE_TITAN_APROVACAO_HUMANA`,
    o identificador do registro em `docs/operations/registro-de-aprovacoes.md`), conferida e citada;
  - **nao escreve no servidor** e **nao escreve em `sales_intelligence`**: o unico estado que este
    componente grava e local (diretorio de mensagens + trilha append-only JSONL). Sem DDL;
  - **nao ingere em desenvolvimento contra o provedor**: o papel `dev-harness` nao tem credencial
    Titan (`hermes/policies/dev-harness.yaml` -> `credenciais_proibidas: TRE_TITAN_*`) e nao pode
    contatar lead/cliente. Em `dev` o componente RECUSA host que nao seja sink local
    (`HOST_NAO_E_DEV`) e login fora do dominio de dev (`USUARIO_NAO_DEV`); a prova contra
    `imap.titan.email` fica em `homolog`, com aprovacao registrada.

Matriz porta x TLS do provedor (o provedor define; o componente confere, nao inventa):
  - `993` -> `implicit_tls` (TLS na conexao) — caminho principal;
  - `143` -> `starttls` (TLS por STARTTLS) — alternativa, e so com STARTTLS negociado;
  - portas de OUTRO protocolo -> RECUSA (`PORTA_DE_OUTRO_PROTOCOLO`): `25`/`465`/`587` sao SMTP e
    `110`/`995` sao POP3; aceitar seria configurar exatamente o caminho que nao fala IMAP;
  - outra porta -> RECUSA (`PORTA_NAO_PREVISTA`).
  Excecao unica e declarada: em `dev`, com host LOOPBACK (o sink de teste), porta fora da matriz e
  aceita desde que a seguranca venha EXPLICITA (`TRE_TITAN_IMAP_SEGURANCA`) — porta de teste nao
  carrega expectativa de provedor, e inferir TLS de porta de teste seria inventar regra. Ainda assim
  as portas de outro protocolo continuam recusadas.
  Divergencia entre a porta e a seguranca declarada RECUSA (`CONFIG_INCOERENTE`) em vez de "consertar
  sozinho": configuracao que se corrige silenciosamente e configuracao que ninguem sabe qual e.

Guardas de ambiente (ADR-005 — nada nasce em producao):
  - `dev`: host LOOPBACK obrigatorio (sink local) e login no dominio de dev (`TRE_TITAN_DOMINIO_DEV`,
    padrao `dev.local`). Host real RECUSA `HOST_NAO_E_DEV` (exit 3); login corporativo RECUSA
    `USUARIO_NAO_DEV` (exit 3);
  - `homolog`: host real permitido, mas exige `TRE_TITAN_APROVACAO_HUMANA` e lista explicita de caixas
    (`TRE_TITAN_CAIXAS_PERMITIDAS`) — caixa fora da lista RECUSA `CAIXA_NAO_PERMITIDA`;
  - `prod`: RECUSA por desenho (exit 4) — a promocao a producao e decisao humana registrada.
  `--confirmo` e obrigatorio para a ingesta: sem ele, `--ingerir` e DRY_RUN e NAO abre conexao.

Segredo (doc `docs/operations/gestao-de-secrets.md` §1/§6): a senha entra por `TRE_TITAN_PASSWORD`
(a mesma caixa do SMTP), vive so na memoria do processo, nunca entra em argumento de linha de comando,
nunca aparece na saida, no relatorio JSON nem na trilha (`mascarar()`), e o proprio componente confere
o que vai gravar antes de gravar: se o valor da senha aparecer, ele RECUSA gravar e sai com exit 5
(`SENHA_VAZADA`) — falha alta, em vez de log contaminado em silencio.

Historico e idempotencia (doc 06 §7: "retry nao pode criar duplicata"): a identidade da mensagem e
`UIDVALIDITY:UID` (o `UIDVALIDITY` protege contra a reutilizacao de UID quando a caixa e recriada).
`--chave-idempotencia` e obrigatoria na ingesta e nomeia o ESCOPO da rodada; mensagem ja registrada
com `resultado=INGERIDO` nao e buscada de novo nem reescrita (`JA_INGERIDO`). `--desfazer`
`<identidade>` nao apaga nada do servidor (isso nao existe aqui): marca a entrada da trilha como
`DESFEITO` (dry-run ate `--confirmo`), preservando a auditoria — a mensagem volta a ser elegivel.

Uso (dev nao tem credencial Titan: quem prova contra o provedor e `homolog`, com aprovacao):

  python3 hermes/integracoes/titan/imap_titan.py --planejar
  python3 hermes/integracoes/titan/imap_titan.py --conferir
  TRE_TITAN_IMAP_HOST=127.0.0.1 TRE_TITAN_IMAP_PORT=2993 TRE_TITAN_IMAP_SEGURANCA=implicit_tls \\
    TRE_TITAN_USER=sink-dev@dev.local TRE_TITAN_PASSWORD=*** TRE_TITAN_CA=/tmp/dev-ca.pem \\
    python3 hermes/integracoes/titan/imap_titan.py --provar
  python3 hermes/integracoes/titan/imap_titan.py --listar
  python3 hermes/integracoes/titan/imap_titan.py --ingerir --saida /tmp/mensagens \\
      --chave-idempotencia "w6-e05:rodada-1" [--confirmo] --registro /tmp/trilha.jsonl
  python3 hermes/integracoes/titan/imap_titan.py --desfazer "UIDVALIDITY:UID" [--confirmo]

Exit: 0 = OK/DRY_RUN/replay · 1 = falha de execucao (conexao/LOGIN/EXAMINE/leitura) · 2 = uso ·
3 = recusa de guarda/configuracao · 4 = recusa de producao · 5 = senha vazada (recusa de gravacao).
"""

from __future__ import annotations

import argparse
import email
import hashlib
import imaplib
import json
import os
import re
import ssl
import sys
from datetime import datetime, timezone
from email.parser import BytesHeaderParser, BytesParser
from email import policy

VERSAO = "titan-imap-v1"

CODIGO_OK = 0
CODIGO_FALHA = 1
CODIGO_USO = 2
CODIGO_RECUSA = 3
CODIGO_PRODUCAO = 4
CODIGO_SEGREDO = 5

AMBIENTES = ("dev", "homolog", "prod")

# Matriz do provedor (imap.titan.email). Porta -> seguranca esperada. O provedor e o dono da matriz.
MATRIZ_PORTA_TLS = {993: "implicit_tls", 143: "starttls"}
# Portas que existem, mas nao falam IMAP: aceitar seria configurar o caminho que nao funciona.
PORTAS_DE_OUTRO_PROTOCOLO = {
    25: "SMTP", 465: "SMTP", 587: "SMTP",
    110: "POP3", 995: "POP3",
}
SEGURANCAS = ("implicit_tls", "starttls", "nenhuma")

DOMINIO_DEV_PADRAO = "dev.local"
CAIXA_PADRAO = "INBOX"
LIMITE_PADRAO = 50
LIMITE_MAXIMO = 500
HOSTS_LOOPBACK = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

# Nomes de variavel -> campo. `senha` e o unico campo secreto. O namespace e o MESMO do SMTP
# (TRE_TITAN_*): e a mesma caixa, e a politica `dev-harness.yaml` proibe o prefixo inteiro.
VARIAVEIS = {
    "TRE_TITAN_IMAP_HOST": "host",
    "TRE_TITAN_IMAP_PORT": "porta",
    "TRE_TITAN_IMAP_SEGURANCA": "seguranca",
    "TRE_TITAN_IMAP_CAIXA": "caixa",
    "TRE_TITAN_IMAP_LIMITE": "limite",
    "TRE_TITAN_USER": "usuario",
    "TRE_TITAN_PASSWORD": "senha",
    "TRE_TITAN_TIMEOUT": "timeout",
    "TRE_TITAN_CA": "ca",
    "TRE_TITAN_DOMINIO_DEV": "dominio_dev",
    "TRE_TITAN_CAIXAS_PERMITIDAS": "caixas_permitidas",
    "TRE_TITAN_APROVACAO_HUMANA": "aprovacao_humana",
}
OBRIGATORIAS = ("host", "porta", "usuario", "senha")


def padroes_de_escrita() -> dict:
    """Padroes (regex) que configuram ESCRITA na caixa — ou leitura que marca lido.

    Montados em tempo de execucao, por concatenacao, para que a auditoria NAO encontre a si mesma no
    proprio codigo: se estivessem escritos literalmente aqui, `auditar_sem_escrita()` reprovaria o
    modulo por causa da propria lista de padroes. Eles miram o RECEBEDOR da sessao (`sessao.<metodo>`)
    e o comando enviado por `uid(...)`, e nao palavras soltas: `lista.append()` de Python nao e
    escrita de IMAP, e a auditoria que confundisse os dois seria ruido, nao guarda.
    """
    recebedor = "ses" + "sao"
    return {
        "METODO_DE_ESCRITA": (rf"\b{recebedor}\.(store|expunge|delete|copy|move|append|create|"
                              rf"subscribe|unsubscribe|rename|setacl)\s*\("),
        "UID_DE_ESCRITA": r"\buid\(\s*[\"'](STORE|EXPUNGE|COPY|MOVE|DELETE)[\"']",
        "SELECAO_DE_ESCRITA": "readonly" + r"\s*=\s*" + "Fal" + "se",
        "BUSCA_SEM_PEEK": "BODY" + r"\[",
        "BUSCA_ANTIGA": "RF" + "C822",
    }


# Comando de busca do corpo: PEEK e o que impede o servidor de marcar \\Seen.
COMANDO_CORPO = "BODY.PEEK[]"
COMANDO_CABECALHO = "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO SUBJECT DATE)]"
CABECALHOS_DE_ENVELOPE = ("Message-ID", "From", "To", "Subject", "Date")


def mascarar(valor: str | None) -> str:
    """Mascara um segredo para exibicao/registro. Nunca devolve o valor."""
    if not valor:
        return "<vazio>"
    if len(valor) <= 4:
        return "*" * len(valor)
    return valor[:1] + "*" * (len(valor) - 2) + valor[-1]


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def loopback(host: str) -> bool:
    if host in HOSTS_LOOPBACK:
        return True
    if host.endswith(".dev.local") or host.endswith(".local"):
        return True
    return bool(re.match(r"^127(\.\d{1,3}){3}$", host))


def dominio(endereco: str) -> str:
    return endereco.rsplit("@", 1)[-1].lower().strip() if "@" in endereco else ""


class Recusa(Exception):
    """Recusa de guarda/configuracao: motivo nominal + codigo de saida."""

    def __init__(self, motivo: str, detalhe: str, codigo: int = CODIGO_RECUSA):
        super().__init__(f"{motivo}: {detalhe}")
        self.motivo = motivo
        self.detalhe = detalhe
        self.codigo = codigo


class Configuracao:
    """Configuracao efetiva do IMAP. O objeto sabe se exibir SEM a senha (`publica()`)."""

    def __init__(self, valores: dict, faltantes: list, inferencias: list):
        self.host = valores.get("host")
        self.porta = valores.get("porta")
        self.seguranca = valores.get("seguranca")
        self.caixa = valores.get("caixa") or CAIXA_PADRAO
        self.limite = valores.get("limite")
        self.usuario = valores.get("usuario")
        self._senha = valores.get("senha")
        self.timeout = valores.get("timeout")
        self.ca = valores.get("ca")
        self.dominio_dev = (valores.get("dominio_dev") or DOMINIO_DEV_PADRAO).lower()
        self.caixas_permitidas = [
            c.strip() for c in (valores.get("caixas_permitidas") or "").split(",") if c.strip()
        ]
        self.aprovacao_humana = valores.get("aprovacao_humana")
        self.faltantes = faltantes
        self.inferencias = inferencias

    @property
    def completa(self) -> bool:
        return not self.faltantes

    @property
    def senha(self) -> str | None:
        return self._senha

    def publica(self) -> dict:
        """Forma segura: a senha nunca entra aqui."""
        return {
            "versao": VERSAO,
            "host": self.host,
            "porta": self.porta,
            "seguranca": self.seguranca,
            "caixa": self.caixa,
            "limite": self.limite,
            "usuario": self.usuario,
            "senha": "<oculta>" if self._senha else "<vazio>",
            "timeout": self.timeout,
            "ca": self.ca or "<CA do sistema>",
            "dominio_dev": self.dominio_dev,
            "caixas_permitidas": self.caixas_permitidas,
            "aprovacao_humana": self.aprovacao_humana or "<ausente>",
            "modo_de_leitura": "EXAMINE (read-only), corpo por BODY.PEEK",
            "config_completa": self.completa,
            "faltantes": self.faltantes,
            "inferencias": self.inferencias,
        }


def ler_ambiente(env: dict) -> dict:
    return {campo: (env.get(nome) or "").strip() for nome, campo in VARIAVEIS.items()}


def montar_configuracao(env: dict) -> Configuracao:
    """Le `TRE_TITAN_*` e devolve a configuracao; NAO valida coerencia nem guarda de ambiente."""
    brutos = ler_ambiente(env)
    faltantes = [nome for nome, campo in VARIAVEIS.items()
                 if campo in OBRIGATORIAS and not brutos.get(campo)]
    inferencias = []
    valores = dict(brutos)

    porta = None
    if brutos.get("porta"):
        if not re.match(r"^\d+$", brutos["porta"]):
            valores["porta"] = brutos["porta"]  # a validacao recusa com motivo nominal
        else:
            porta = int(brutos["porta"])
            valores["porta"] = porta

    if not valores.get("seguranca") and isinstance(porta, int) and porta in MATRIZ_PORTA_TLS:
        valores["seguranca"] = MATRIZ_PORTA_TLS[porta]
        inferencias.append(f"seguranca inferida da porta {porta} ({valores['seguranca']})")

    if not valores.get("caixa"):
        valores["caixa"] = CAIXA_PADRAO
        inferencias.append(f"caixa padrao {CAIXA_PADRAO}")

    if not valores.get("limite"):
        valores["limite"] = LIMITE_PADRAO
        inferencias.append(f"limite padrao de {LIMITE_PADRAO} mensagens por rodada")
    else:
        try:
            valores["limite"] = int(valores["limite"])
        except ValueError:
            pass  # a validacao recusa com motivo nominal

    if not valores.get("timeout"):
        valores["timeout"] = 15
        inferencias.append("timeout padrao de 15s")
    else:
        try:
            valores["timeout"] = int(valores["timeout"])
        except ValueError:
            pass

    return Configuracao(valores, faltantes, inferencias)


def validar(config: Configuracao, ambiente: str) -> list:
    """Devolve as RECUSAS da configuracao (lista de Recusa). Vazio = pronta para conexao."""
    recusas = []

    if config.faltantes:
        recusas.append(Recusa("CONFIG_INCOMPLETA",
                              "sem valor para: " + ", ".join(sorted(config.faltantes))))
        return recusas  # sem os campos base nao ha o que conferir adiante

    # --- porta x TLS (matriz do provedor; sink local e a excecao declarada) ---
    porta_de_sink = isinstance(config.porta, int) and loopback(config.host or "")
    if not isinstance(config.porta, int):
        recusas.append(Recusa("PORTA_INVALIDA", f"porta '{config.porta}' nao e numerica"))
    elif config.porta in PORTAS_DE_OUTRO_PROTOCOLO:
        recusas.append(Recusa("PORTA_DE_OUTRO_PROTOCOLO",
                              f"porta {config.porta} e {PORTAS_DE_OUTRO_PROTOCOLO[config.porta]}, "
                              f"nao IMAP (use 993 implicit_tls ou 143 starttls)"))
    elif config.porta in MATRIZ_PORTA_TLS:
        if config.seguranca != MATRIZ_PORTA_TLS[config.porta]:
            recusas.append(Recusa("CONFIG_INCOERENTE",
                                  f"porta {config.porta} exige {MATRIZ_PORTA_TLS[config.porta]}, "
                                  f"declarado '{config.seguranca}'"))
    elif porta_de_sink and ambiente == "dev":
        # Sink local nao e o provedor: porta fora da matriz e legitima, mas so em loopback+dev e
        # so com a seguranca DECLARADA (aqui nao se infere TLS de porta de teste).
        if config.seguranca not in SEGURANCAS:
            recusas.append(Recusa("SEGURANCA_AUSENTE",
                                  f"porta de sink local {config.porta} exige "
                                  f"TRE_TITAN_IMAP_SEGURANCA explicita"))
    else:
        recusas.append(Recusa("PORTA_NAO_PREVISTA",
                              f"porta {config.porta} fora da matriz do provedor (993/143)"))

    if config.seguranca not in SEGURANCAS:
        recusas.append(Recusa("SEGURANCA_INVALIDA",
                              f"seguranca '{config.seguranca}' fora de {list(SEGURANCAS)}"))

    if config.seguranca == "nenhuma" and not loopback(config.host or ""):
        recusas.append(Recusa("TLS_OBRIGATORIO",
                              f"texto claro so e aceito em sink local; host '{config.host}' nao e loopback"))

    if not isinstance(config.limite, int) or not 1 <= config.limite <= LIMITE_MAXIMO:
        recusas.append(Recusa("LIMITE_INVALIDO",
                              f"limite '{config.limite}' fora de 1..{LIMITE_MAXIMO}"))

    if not isinstance(config.timeout, int) or config.timeout <= 0:
        recusas.append(Recusa("TIMEOUT_INVALIDO", f"timeout '{config.timeout}' invalido"))

    if config.ca and not os.path.isfile(config.ca):
        recusas.append(Recusa("CA_AUSENTE", f"CA declarada nao existe: {config.ca}"))

    if config.usuario and " " in config.usuario:
        recusas.append(Recusa("USUARIO_INVALIDO", "usuario com espaco nao e endereco de caixa"))

    if not re.match(r"^[A-Za-z0-9._/\[\]-]+$", config.caixa or ""):
        recusas.append(Recusa("CAIXA_INVALIDA",
                              f"nome de caixa '{config.caixa}' fora do aceito (letras, numeros, "
                              f". _ - / [ ])"))

    # --- guardas de ambiente (ADR-005) ---
    if ambiente == "prod":
        recusas.append(Recusa("PRODUCAO_NAO_E_DESTE_CARD",
                              "producao e promocao humana registrada (ADR-005); este componente "
                              "nao executa ato em producao", CODIGO_PRODUCAO))
        return recusas

    if ambiente == "dev":
        if not loopback(config.host or ""):
            recusas.append(Recusa("HOST_NAO_E_DEV",
                                  f"dev so fala com sink local; host '{config.host}' nao e loopback "
                                  f"(o dev-harness nao tem credencial Titan)"))
        if config.usuario and dominio(config.usuario) not in (config.dominio_dev, "localhost"):
            recusas.append(Recusa("USUARIO_NAO_DEV",
                                  f"dev usa login @{config.dominio_dev}; "
                                  f"'{dominio(config.usuario)}' nao e dominio de desenvolvimento"))

    if ambiente == "homolog":
        if not config.aprovacao_humana:
            recusas.append(Recusa("HOMOLOG_SEM_APROVACAO",
                                  "homolog contra o provedor exige TRE_TITAN_APROVACAO_HUMANA registrada"))
        if not config.caixas_permitidas:
            recusas.append(Recusa("CAIXA_NAO_PERMITIDA",
                                  "sem TRE_TITAN_CAIXAS_PERMITIDAS o componente nao le caixa nenhuma"))
        elif config.caixa not in config.caixas_permitidas:
            recusas.append(Recusa("CAIXA_NAO_PERMITIDA",
                                  f"caixa '{config.caixa}' fora de TRE_TITAN_CAIXAS_PERMITIDAS"))

    return recusas


def auditar_sem_escrita(caminho: str | None = None) -> list:
    """Le a PROPRIA fonte e devolve os padroes de escrita encontrados. Vazio = invariante intacta.

    E a camada 3 do invariante de leitura: antes de qualquer conexao o componente confere que ele
    mesmo nao carrega comando capaz de alterar a caixa. Se carregar, quem chama recusa (exit 3).
    """
    caminho = caminho or os.path.abspath(__file__)
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            codigo = fh.read()
    except OSError as e:
        return [f"FONTE_ILEGIVEL:{type(e).__name__}"]
    return sorted(nome for nome, padrao in padroes_de_escrita().items() if re.search(padrao, codigo))


def contexto_tls(config: Configuracao) -> ssl.SSLContext:
    if config.ca:
        return ssl.create_default_context(cafile=config.ca)
    return ssl.create_default_context()


def conectar(config: Configuracao):
    """Abre a sessao IMAP conforme a matriz porta x TLS. Devolve (sessao, medidas)."""
    medidas = {"tls": config.seguranca, "versao_tls": None, "saudacao": None, "capacidade": []}
    ctx = contexto_tls(config)
    if config.seguranca == "implicit_tls":
        sessao = imaplib.IMAP4_SSL(config.host, config.porta, ssl_context=ctx, timeout=config.timeout)
    else:
        sessao = imaplib.IMAP4(config.host, config.porta, timeout=config.timeout)
    saudacao = getattr(sessao, "welcome", b"")
    medidas["saudacao"] = saudacao.decode("utf-8", "replace") if isinstance(saudacao, bytes) else str(saudacao)
    if config.seguranca == "starttls":
        sessao.starttls(ssl_context=ctx)
        sessao.capability()
    # Leitura defensiva: em "nenhuma" o socket e TCP puro e NAO tem version() (mesmo defeito real
    # medido no card SMTP irmao: `sock.version()` estourava AttributeError e matava a rodada).
    sock = getattr(sessao, "sock", None)
    medidas["versao_tls"] = sock.version() if isinstance(sock, ssl.SSLSocket) else None
    medidas["capacidade"] = sorted(c.decode("utf-8", "replace") if isinstance(c, bytes) else str(c)
                                   for c in getattr(sessao, "capabilities", ()) or ())
    return sessao, medidas


def autenticar(sessao, config: Configuracao) -> str:
    tipo, dados = sessao.login(config.usuario, config.senha)
    if tipo != "OK":
        raise Recusa("LOGIN_RECUSADO", f"servidor respondeu {tipo}: {str(dados)[:200]}")
    # Depois do LOGIN as capacidades mudam (pre-auth x pos-auth): recarrega para medir o real.
    try:
        sessao.capability()
    except Exception:  # noqa: BLE001 — medir capacidade nao pode derrubar a sessao
        pass
    return "OK"


def abrir_caixa(sessao, config: Configuracao) -> dict:
    """Abre a caixa SOMENTE em modo leitura (EXAMINE) e devolve as medidas dela."""
    tipo, dados = sessao.select(config.caixa, readonly=True)
    if tipo != "OK":
        raise Recusa("CAIXA_INACESSIVEL", f"EXAMINE {config.caixa} respondeu {tipo}: {str(dados)[:200]}")
    nao_untagged = getattr(sessao, "untagged_responses", {}) or {}

    def untagged(chave: str):
        valor = nao_untagged.get(chave) or nao_untagged.get(chave.encode("utf-8"))
        return valor[-1] if valor else None

    def texto(valor):
        if valor is None:
            return None
        if isinstance(valor, bytes):
            return valor.decode("utf-8", "replace")
        return str(valor)

    existentes = dados[0].decode("utf-8", "replace") if dados and isinstance(dados[0], bytes) else None
    return {
        "caixa": config.caixa,
        "modo_de_abertura": "EXAMINE (read-only)",
        "mensagens_na_caixa": int(existentes) if existentes and existentes.isdigit() else None,
        "uidvalidity": texto(untagged("UIDVALIDITY")),
        "nao_lidas": texto(untagged("UNSEEN")),
    }


def partes_fetch(resposta) -> list:
    """Normaliza a resposta de UID FETCH em [(metadados, bytes)]."""
    itens = []
    for parte in resposta:
        if isinstance(parte, tuple) and len(parte) == 2:
            meta, conteudo = parte
            if isinstance(meta, bytes):
                meta = meta.decode("utf-8", "replace")
            itens.append((meta or "", conteudo if isinstance(conteudo, bytes) else b""))
    return itens


def campo_do_meta(meta: str, campo: str):
    achado = re.search(rf"{campo} (\d+)", meta or "")
    return achado.group(1) if achado else None


def flags_do_meta(meta: str) -> list:
    achado = re.search(r"FLAGS \(([^)]*)\)", meta or "")
    if not achado:
        return []
    return sorted(p for p in achado.group(1).split() if p)


def identidade_mensagem(uidvalidity: str | None, uid: str) -> str:
    return f"{uidvalidity or 'SEM-UIDVALIDITY'}:{uid}"


def cabecalhos(conteudo: bytes) -> dict:
    try:
        msg = BytesHeaderParser(policy=policy.default).parsebytes(conteudo)
    except Exception:  # noqa: BLE001 — cabecalho quebrado de terceiro nao pode derrubar a rodada
        return {}
    return {nome: (msg.get(nome) or "") for nome in CABECALHOS_DE_ENVELOPE}


def cabecalhos_completos(conteudo: bytes) -> dict:
    """Todos os cabecalhos da mensagem (aditivo, card TRE-W6-E05-T01).

    O envelope do card E01-T02 traz os cinco cabecalhos de triagem; a CLASSIFICACAO precisa de mais
    que isso: `Content-Type` de relatorio de entrega (bounce), `Auto-Submitted`/`X-Autoreply`
    (auto-resposta) e `Reply-To`/`Return-Path` sao cabecalhos, nao corpo. Nada muda no envelope
    existente — este e um campo NOVO do registro de ingesta.
    """
    try:
        msg = BytesParser(policy=policy.default).parsebytes(conteudo)
    except Exception:  # noqa: BLE001 — cabecalho quebrado de terceiro nao pode derrubar a rodada
        return {}
    return {nome: str(valor) for nome, valor in msg.items()}


def corpo_html(conteudo: bytes) -> str:
    """Texto do `text/html` da mensagem (aditivo, card TRE-W6-E05-T01).

    `corpo_texto()` devolve so `text/plain`: mensagem que so tem HTML chegava vazia ao classificador
    e era classificada como INDEFINIDO por falta de texto, nao por falta de sinal. Aqui o HTML sai
    cru (quem converte em texto e o consumidor, que declara como faz).
    """
    try:
        msg = BytesParser(policy=policy.default).parsebytes(conteudo)
    except Exception:  # noqa: BLE001
        return ""
    try:
        if msg.is_multipart():
            return "".join(parte.get_content() for parte in msg.walk()
                           if parte.get_content_type() == "text/html")
        return msg.get_content() if msg.get_content_type() == "text/html" else ""
    except Exception:  # noqa: BLE001
        return ""


def corpo_texto(conteudo: bytes) -> str:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(conteudo)
    except Exception:  # noqa: BLE001
        return ""
    try:
        if msg.is_multipart():
            pedacos = []
            for parte in msg.walk():
                if parte.get_content_type() == "text/plain":
                    pedacos.append(parte.get_content())
            return "".join(pedacos)
        return msg.get_content()
    except Exception:  # noqa: BLE001 — corpo ilegivel vira vazio, e a trilha diz que veio vazio
        return ""


def ler_trilha(caminho: str) -> list:
    if not caminho or not os.path.isfile(caminho):
        return []
    linhas = []
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha:
                continue
            try:
                linhas.append(json.loads(linha))
            except json.JSONDecodeError:
                continue
    return linhas


def ja_ingerido(caminho: str, identidade: str) -> dict | None:
    for evento in reversed(ler_trilha(caminho)):
        if evento.get("identidade_mensagem") == identidade and evento.get("resultado") == "INGERIDO" \
                and evento.get("evento") != "DESFEITO":
            return evento
    return None


class Saida:
    """Relatorio + trilha, com a checagem final de vazamento de segredo."""

    def __init__(self, caminho_relatorio: str | None, caminho_trilha: str | None, segredo: str | None):
        self.caminho_relatorio = caminho_relatorio
        self.caminho_trilha = caminho_trilha
        self.segredo = segredo
        self.relatorio = {"versao": VERSAO, "quando": agora(), "eventos": []}

    def evento(self, **campos) -> None:
        campos.setdefault("quando", agora())
        self.relatorio["eventos"].append(campos)
        print(json.dumps(campos, ensure_ascii=False, sort_keys=True))
        if self.caminho_trilha:
            self._gravar(self.caminho_trilha, campos)

    def _gravar(self, caminho: str, dados: dict) -> None:
        texto = json.dumps(dados, ensure_ascii=False, sort_keys=True)
        self._conferir_segredo(texto)
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write(texto + "\n")

    def _conferir_segredo(self, texto: str) -> None:
        """Fail-closed: se o valor da senha aparecer no que vai ser gravado, nao grava e sai 5."""
        if self.segredo and len(self.segredo) >= 4 and self.segredo in texto:
            print(json.dumps({"evento": "SENHA_VAZADA",
                              "motivo": "SENHA_VAZADA",
                              "detalhe": "o valor de TRE_TITAN_PASSWORD apareceu no registro; "
                                         "gravacao recusada (exit 5)"}, ensure_ascii=False))
            raise SystemExit(CODIGO_SEGREDO)

    def fechar(self) -> None:
        self.relatorio["veredito"] = self.relatorio.get("veredito") or "IMAP_TITAN_SEM_VEREDITO"
        texto = json.dumps(self.relatorio, ensure_ascii=False, sort_keys=True, indent=2)
        self._conferir_segredo(texto)
        if self.caminho_relatorio:
            os.makedirs(os.path.dirname(os.path.abspath(self.caminho_relatorio)), exist_ok=True)
            with open(self.caminho_relatorio, "w", encoding="utf-8") as fh:
                fh.write(texto + "\n")
            print(f"# relatorio: {self.caminho_relatorio}")


def identidade_da_config(config: Configuracao) -> str:
    """Impressao digital da CONFIGURACAO efetiva — sem a senha."""
    base = "|".join([str(config.host), str(config.porta), str(config.seguranca), str(config.caixa),
                     str(config.usuario), str(config.timeout), str(config.ca)])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def listar_uids(sessao, config: Configuracao) -> list:
    tipo, dados = sessao.uid("SEARCH", None, "ALL")
    if tipo != "OK":
        raise Recusa("BUSCA_RECUSADA", f"UID SEARCH respondeu {tipo}: {str(dados)[:200]}")
    texto = b" ".join(d for d in dados if isinstance(d, bytes)).decode("utf-8", "replace")
    uids = [u for u in texto.split() if u.isdigit()]
    return uids[: config.limite]


def envelope(sessao, uid: str, uidvalidity: str | None) -> dict:
    """Le SO os cabecalhos (BODY.PEEK) e as flags — nao traz corpo e nao altera flag nenhuma."""
    tipo, dados = sessao.uid("FETCH", uid, f"({COMANDO_CABECALHO} FLAGS)")
    tipo_flags, dados_flags = sessao.uid("FETCH", uid, "(FLAGS)")
    if tipo != "OK":
        raise Recusa("LEITURA_RECUSADA", f"UID FETCH {uid} respondeu {tipo}: {str(dados)[:200]}")
    itens = partes_fetch(dados)
    meta, conteudo = itens[0] if itens else ("", b"")
    flags = flags_do_meta(meta)
    if tipo_flags == "OK":
        itens_flags = partes_fetch(dados_flags)
        if itens_flags:
            flags = flags_do_meta(itens_flags[0][0]) or flags
    cabecalho = cabecalhos(conteudo)
    return {
        "uid": uid,
        "identidade_mensagem": identidade_mensagem(uidvalidity, uid),
        "message_id": cabecalho.get("Message-ID", ""),
        "de": cabecalho.get("From", ""),
        "para": cabecalho.get("To", ""),
        "assunto": cabecalho.get("Subject", ""),
        "data": cabecalho.get("Date", ""),
        "flags": flags,
        "marcada_como_lida": any(f.lower() == "\\seen" for f in flags),
    }


def ingerir_mensagem(sessao, uid: str, uidvalidity: str | None) -> dict:
    """Traz UMA mensagem por BODY.PEEK (sem marcar \\Seen) e devolve o registro para gravar."""
    tipo, dados = sessao.uid("FETCH", uid, f"({COMANDO_CORPO} FLAGS INTERNALDATE)")
    if tipo != "OK":
        raise Recusa("LEITURA_RECUSADA", f"UID FETCH {uid} respondeu {tipo}: {str(dados)[:200]}")
    itens = partes_fetch(dados)
    meta, conteudo = itens[0] if itens else ("", b"")
    cabecalho = cabecalhos(conteudo)
    return {
        "versao": VERSAO,
        "quando": agora(),
        "uid": uid,
        "identidade_mensagem": identidade_mensagem(uidvalidity, uid),
        "message_id": cabecalho.get("Message-ID", ""),
        "de": cabecalho.get("From", ""),
        "para": cabecalho.get("To", ""),
        "assunto": cabecalho.get("Subject", ""),
        "data": cabecalho.get("Date", ""),
        "flags": flags_do_meta(meta),
        "tamanho_bytes": len(conteudo),
        "sha256_corpo": hashlib.sha256(conteudo).hexdigest(),
        "corpo_texto": corpo_texto(conteudo),
        # Campos ADITIVOS do card TRE-W6-E05-T01 (classificacao): o envelope acima nao muda.
        "corpo_html": corpo_html(conteudo),
        "cabecalhos_completos": cabecalhos_completos(conteudo),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Titan IMAP v1 — leitura validada e guardada da caixa (TRE-W6-E01-T02)")
    parser.add_argument("--ambiente", choices=AMBIENTES, default="dev")
    acao = parser.add_mutually_exclusive_group(required=True)
    acao.add_argument("--planejar", action="store_true",
                      help="mostra a configuracao efetiva (senha mascarada) e o que falta; nunca conecta")
    acao.add_argument("--conferir", action="store_true",
                      help="valida completude, matriz porta x TLS, guardas e invariante de leitura")
    acao.add_argument("--provar", action="store_true",
                      help="conecta e mede saudacao/TLS/LOGIN/CAPACIDADE/EXAMINE/NOOP sem trazer corpo")
    acao.add_argument("--listar", action="store_true",
                      help="le os envelopes (cabecalhos) da caixa, sem trazer corpo e sem marcar lido")
    acao.add_argument("--ingerir", action="store_true",
                      help="traz as mensagens novas (dry-run sem --confirmo) para --saida")
    acao.add_argument("--desfazer", metavar="IDENTIDADE",
                      help="marca a identidade da trilha como DESFEITO (dry-run ate --confirmo)")
    parser.add_argument("--saida", default=None, help="diretorio das mensagens ingeridas (JSON por mensagem)")
    parser.add_argument("--chave-idempotencia", default=None, help="escopo da rodada de ingesta (obrigatoria)")
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--relatorio", default=None)
    parser.add_argument("--registro", default=None, help="trilha append-only (JSONL)")
    parser.add_argument("--env-file", default=None, help="arquivo .env alternativo (default: ambiente)")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file:
        env.update(ler_env_file(args.env_file))

    config = montar_configuracao(env)
    saida = Saida(args.relatorio, args.registro, config.senha)
    cfg_publica = config.publica()

    def emitir(evento: str, resultado: str, **extra) -> None:
        campos = {"evento": evento, "resultado": resultado, "ambiente": args.ambiente}
        campos.update(cfg_publica)
        campos.update(extra)
        saida.evento(**campos)

    if args.desfazer:
        if not args.confirmo:
            emitir("DESFAZER", "DRY_RUN", identidade_mensagem=args.desfazer,
                   detalhe="sem --confirmo nada e gravado; nada e apagado do servidor em nenhum caso")
            saida.relatorio["veredito"] = "DESFAZER_DRY_RUN"
            saida.fechar()
            return CODIGO_OK
        registros = ler_trilha(args.registro or "")
        # So uma INGESTA registrada pode ser desfeita: o DRY_RUN do proprio --desfazer tambem entra na
        # trilha (auditoria do que NAO aconteceu) e, se ele contasse como alvo, um "desfazer" de
        # mensagem nunca ingerida acharia a si mesmo e responderia DESFEITO — medido na rodada 1.
        ingeridos = [e for e in registros if e.get("identidade_mensagem") == args.desfazer
                     and e.get("resultado") == "INGERIDO"]
        if not ingeridos:
            emitir("DESFAZER", "NAO_ENCONTRADO", identidade_mensagem=args.desfazer,
                   detalhe="nao ha ingesta registrada com essa identidade na trilha")
            saida.relatorio["veredito"] = "DESFAZER_NAO_ENCONTRADO"
            saida.fechar()
            return CODIGO_FALHA
        if any(e.get("evento") == "DESFAZER" and e.get("resultado") == "DESFEITO"
               and e.get("identidade_mensagem") == args.desfazer for e in registros):
            emitir("DESFAZER", "JA_DESFEITO", identidade_mensagem=args.desfazer,
                   detalhe="a identidade ja estava marcada; nada novo foi gravado")
            saida.relatorio["veredito"] = "JA_DESFEITO"
            saida.fechar()
            return CODIGO_OK
        emitir("DESFAZER", "DESFEITO", identidade_mensagem=args.desfazer,
               detalhe="trilha marcada; a mensagem volta a ser elegivel e a auditoria original e preservada")
        saida.relatorio["veredito"] = "DESFEITO_REGISTRADO"
        saida.fechar()
        return CODIGO_OK

    if args.planejar:
        # Planejar NUNCA conecta e NUNCA falha por falta de config: ele declara o estado.
        recusas = validar(config, args.ambiente)
        achados = auditar_sem_escrita()
        emitir("PLANEJAR", "PLANO",
               identidade_config=identidade_da_config(config),
               auditoria_de_leitura=achados or "sem comando de escrita",
               recusas=[{"motivo": r.motivo, "detalhe": r.detalhe} for r in recusas])
        saida.relatorio["veredito"] = "CONFIG_COMPLETA" if config.completa else "CONFIG_INCOMPLETA"
        saida.fechar()
        return CODIGO_OK

    # A partir daqui toda acao conecta: o invariante de leitura e conferido ANTES, fail-closed.
    achados = auditar_sem_escrita()
    if achados:
        emitir("ESCRITA_NO_CODIGO", "RECUSADO",
               detalhe="a fonte do componente carrega comando de escrita na caixa: " + ", ".join(achados))
        saida.relatorio["veredito"] = "RECUSADO"
        saida.fechar()
        return CODIGO_RECUSA

    recusas = validar(config, args.ambiente)
    if recusas:
        for r in recusas:
            emitir(r.motivo, "RECUSADO", detalhe=r.detalhe)
        saida.relatorio["veredito"] = "RECUSADO"
        saida.fechar()
        return max(r.codigo for r in recusas)

    if args.conferir:
        emitir("CONFERIR", "OK", identidade_config=identidade_da_config(config),
               auditoria_de_leitura="sem comando de escrita")
        saida.relatorio["veredito"] = "CONFIGURACAO_OK"
        saida.fechar()
        return CODIGO_OK

    if args.ingerir and not args.confirmo:
        emitir("INGERIR", "DRY_RUN", chave_idempotencia=args.chave_idempotencia,
               saida=args.saida or "<ausente>",
               detalhe="sem --confirmo NADA e lido e nenhuma conexao e aberta")
        saida.relatorio["veredito"] = "DRY_RUN"
        saida.fechar()
        return CODIGO_OK

    if args.ingerir:
        if not args.chave_idempotencia:
            emitir("INGERIR", "RECUSADO",
                   detalhe="--chave-idempotencia e obrigatoria (retry nao duplica)")
            saida.relatorio["veredito"] = "RECUSADO"
            saida.fechar()
            return CODIGO_USO
        if not args.saida:
            emitir("INGERIR", "RECUSADO",
                   detalhe="--saida e obrigatoria (o diretorio das mensagens ingeridas)")
            saida.relatorio["veredito"] = "RECUSADO"
            saida.fechar()
            return CODIGO_USO

    sessao = None
    try:
        sessao, medidas = conectar(config)
        autenticacao = autenticar(sessao, config)
        caixa = abrir_caixa(sessao, config)
        uidvalidity = caixa.get("uidvalidity")

        if args.provar:
            tipo, _ = sessao.noop()
            emitir("PROVAR", "OK", **medidas, **caixa, autenticacao=autenticacao,
                   noop=tipo, identidade_config=identidade_da_config(config))
            saida.relatorio["veredito"] = "CONEXAO_OK"
            saida.fechar()
            return CODIGO_OK

        uids = listar_uids(sessao, config)

        if args.listar:
            envelopes = [envelope(sessao, uid, uidvalidity) for uid in uids]
            emitir("LER", "OK", **medidas, **caixa, autenticacao=autenticacao,
                   envelopes=envelopes, total=len(envelopes),
                   identidade_config=identidade_da_config(config))
            saida.relatorio["veredito"] = "LEITURA_OK"
            saida.fechar()
            return CODIGO_OK

        # --- --ingerir: so mensagem NOVA e buscada; a ja ingerida nem e lida de novo ---
        novas, repetidas = [], []
        for uid in uids:
            ident = identidade_mensagem(uidvalidity, uid)
            if ja_ingerido(args.registro or "", ident):
                repetidas.append(ident)
            else:
                novas.append(uid)

        os.makedirs(os.path.abspath(args.saida), exist_ok=True)
        registros = []
        for uid in novas:
            registro = ingerir_mensagem(sessao, uid, uidvalidity)
            caminho = os.path.join(os.path.abspath(args.saida),
                                   f"{registro['identidade_mensagem'].replace(':', '-')}.json")
            texto = json.dumps(registro, ensure_ascii=False, sort_keys=True, indent=2)
            saida._conferir_segredo(texto)  # fail-closed antes de qualquer gravacao
            with open(caminho, "w", encoding="utf-8") as fh:
                fh.write(texto + "\n")
            registros.append(registro)
            emitir("INGERIR", "INGERIDO", **medidas, **caixa, autenticacao=autenticacao,
                   chave_idempotencia=args.chave_idempotencia,
                   identidade_mensagem=registro["identidade_mensagem"],
                   message_id=registro["message_id"], assunto=registro["assunto"],
                   de=registro["de"], tamanho_bytes=registro["tamanho_bytes"],
                   sha256_corpo=registro["sha256_corpo"],
                   arquivo=caminho, marcada_como_lida=False,
                   identidade_config=identidade_da_config(config))

        if not registros and not repetidas:
            emitir("INGERIR", "NENHUMA_MENSAGEM", **caixa, total=0,
                   chave_idempotencia=args.chave_idempotencia)
            saida.relatorio["veredito"] = "NENHUMA_MENSAGEM"
            saida.fechar()
            return CODIGO_OK

        if registros:
            emitir("INGERIR", "FIM", **caixa, novas=len(registros), repetidas=len(repetidas),
                   chave_idempotencia=args.chave_idempotencia,
                   detalhe="mensagens novas gravadas uma unica vez nesta rodada")
            saida.relatorio["veredito"] = "INGERIDO"
        else:
            emitir("INGERIR", "JA_INGERIDO", **caixa, repetidas=len(repetidas),
                   chave_idempotencia=args.chave_idempotencia,
                   detalhe="replay: todas as mensagens da caixa ja constavam na trilha; nada novo foi lido")
            saida.relatorio["veredito"] = "JA_INGERIDO"
        saida.fechar()
        return CODIGO_OK
    except Recusa as r:
        emitir(r.motivo, "RECUSADO", detalhe=r.detalhe)
        saida.relatorio["veredito"] = "RECUSADO"
        saida.fechar()
        return r.codigo
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as e:
        # A mensagem de erro do provedor nao carrega a senha; se carregasse, `_conferir_segredo`
        # barraria a gravacao antes de qualquer log com segredo.
        emitir("FALHA", "FALHOU", erro=type(e).__name__, detalhe=str(e)[:300])
        saida.relatorio["veredito"] = "FALHOU"
        saida.fechar()
        return CODIGO_FALHA
    finally:
        if sessao is not None:
            try:
                sessao.logout()
            except Exception:  # noqa: BLE001 — encerrar sessao nao pode mascarar o resultado
                pass


def ler_env_file(caminho: str) -> dict:
    valores = {}
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            nome, valor = linha.split("=", 1)
            valores[nome.strip()] = valor.strip().strip('"').strip("'")
    return valores


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Recusa as recusa:  # recusa levantada fora do try de sessao (ex.: caixa/destino)
        print(json.dumps({"evento": recusa.motivo, "resultado": "RECUSADO", "detalhe": recusa.detalhe},
                         ensure_ascii=False))
        sys.exit(recusa.codigo)
