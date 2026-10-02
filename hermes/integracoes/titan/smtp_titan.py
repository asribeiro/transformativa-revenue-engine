#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMTP Titan v1 (`titan-smtp-v1`) — configuracao validada e primitivo de envio guardado (card TRE-W6-E01-T01).

O que este componente FAZ (e so isto): transforma a CONFIGURACAO do SMTP do Titan (variaveis de
ambiente `TRE_TITAN_*`) em uma sessao SMTP validada e guardada — completa, coerente com a matriz
porta x TLS do provedor, permitida no ambiente em que esta rodando e sem nunca expor o segredo — e
expoe o PRIMITIVO de envio de UMA mensagem que o workflow de envio (W6-E04) vai chamar. A prova de
conexao (`--provar`) mede EHLO, negociacao TLS, AUTH e NOOP sem enviar e-mail nenhum.

O que ele NAO faz, por desenho (declarado no contrato `titan-smtp-v1.json` -> lacunas):
  - **nao gera o texto do outreach** (W6-E02): a mensagem entra pronta (`--assunto`/`--corpo`); a v1
    nao chama LLM e nao faz requisicao de rede alem da propria sessao SMTP;
  - **nao decide Human Approval** (W6-E03) e nao substitui o workflow de envio (W6-E04): quem chama o
    primitivo tem de trazer a aprovacao. Aqui a aprovacao e uma ENTRADA (`TRE_TITAN_APROVACAO_HUMANA`,
    o identificador do registro em `docs/operations/registro-de-aprovacoes.md`) que o componente
    CONFERE e cita na trilha — nunca concede;
  - **nao ingere resposta** (W6-E05): isso e IMAP (`TRE_TITAN_IMAP_*`), card irmao TRE-W6-E01-T02;
  - **nao escreve em `sales_intelligence`**: o unico estado que este componente grava e a propria
    trilha append-only (`--registro`, JSONL) fora do banco. Sem DDL, sem migration, sem tabela nova;
  - **nao envia nada em desenvolvimento**: o papel `dev-harness` nao tem credencial Titan
    (`hermes/policies/dev-harness.yaml` -> `credenciais_proibidas: TRE_TITAN_*`) e nao pode enviar
    e-mail em nome da Transformativa. Por isso, em `dev` o componente RECUSA host que nao seja sink
    local (`HOST_NAO_E_DEV`) e remetente que nao seja do dominio de desenvolvimento
    (`REMETENTE_NAO_DEV`) — o aceite mede a configuracao real contra um sink descartavel em
    `127.0.0.1`, e a provar contra smtp.titan.email fica em `homolog`, com aprovacao registrada.

Matriz porta x TLS (o provedor define; o componente confere, nao inventa):
  - `465` -> `implicit_tls` (TLS na conexao);
  - `587` -> `starttls` (TLS por STARTTLS);
  - `25`  -> RECUSA (`PORTA_NAO_AUTORIZADA`): o provedor nao autoriza relay na 25; aceitar seria
    configurar justamente o caminho que nao funciona (ou que degrada para texto claro);
  - outra porta -> RECUSA (`PORTA_NAO_PREVISTA`).
  Excecao unica e declarada: em `dev`, com host LOOPBACK (o sink de teste), porta fora da matriz e
  aceita desde que a seguranca venha EXPLICITA (`TRE_TITAN_SMTP_SEGURANCA`) — porta de teste nao
  carrega expectativa de provedor, e inferir TLS de porta de teste seria inventar regra. Ainda assim
  a porta 25 continua recusada.
  Divergencia entre a porta e a seguranca declarada RECUSA (`CONFIG_INCOERENTE`) em vez de "consertar
  sozinho": configuracao que se corrige silenciosamente e configuracao que ninguem sabe qual e.

Guardas de ambiente (ADR-005 — nada nasce em producao):
  - `dev`: host LOOPBACK obrigatorio (sink local) e remetente/destino no dominio de dev
    (`TRE_TITAN_DOMINIO_DEV`, padrao `dev.local`). Host real RECUSA `HOST_NAO_E_DEV` (exit 3);
  - `homolog`: host real permitido, mas exige `TRE_TITAN_APROVACAO_HUMANA` e lista explicita de
    destinos (`TRE_TITAN_DESTINOS_PERMITIDOS`) — fora da lista RECUSA `DESTINO_NAO_PERMITIDO`;
  - `prod`: RECUSA por desenho (exit 4) — a promocao a producao e decisao humana registrada, nao
    efeito de um card de execucao (regra do card + ADR-005).
  `--confirmo` e obrigatorio para o envio sair: sem ele, `--enviar` e DRY_RUN e NAO abre conexao.

Segredo (doc `docs/operations/gestao-de-secrets.md` §1/§6): a senha entra por `TRE_TITAN_PASSWORD`,
vive so na memoria do processo, nunca entra em argumento de linha de comando, nunca aparece na saida,
no relatorio JSON nem na trilha (`mascarar()`), e o proprio componente confere a saida antes de
grava-la: se o valor da senha aparecer no relatorio, ele RECUSA gravar e sai com exit 5
(`SENHA_VAZADA`) — falha alta, em vez de log contaminado em silencio.

Historico e idempotencia (doc 06 §7: "retry nao pode criar duplicata"): a chave e a ENTRADA —
`--chave-idempotencia`, obrigatoria no envio, gravada na trilha. Mesma chave ja vista com
`resultado=ENVIADO` -> `JA_ENVIADO` (replay: nada novo sai); chave nova -> envio novo. `--desfazer`
nao cancela e-mail entregue (isso nao existe): marca a entrada da trilha como `DESFEITO` (dry-run ate
`--confirmo`), preservando a auditoria — a mesma regra de `--desfazer` dos componentes W5.

Uso (dev nao tem credencial Titan: quem prova contra o provedor e `homolog`, com aprovacao):

  python3 hermes/integracoes/titan/smtp_titan.py --planejar
  python3 hermes/integracoes/titan/smtp_titan.py --conferir
  TRE_TITAN_SMTP_HOST=127.0.0.1 TRE_TITAN_SMTP_PORT=2465 TRE_TITAN_SMTP_SEGURANCA=implicit_tls \
    TRE_TITAN_USER=sink TRE_TITAN_PASSWORD=*** TRE_TITAN_CA=/tmp/dev-ca.pem \
    python3 hermes/integracoes/titan/smtp_titan.py --provar
  python3 hermes/integracoes/titan/smtp_titan.py --enviar --para caixa@dev.local \
      --assunto "prova" --corpo "corpo" --chave-idempotencia "prova:1" [--confirmo]
  python3 hermes/integracoes/titan/smtp_titan.py --desfazer "prova:1" [--confirmo]

Exit: 0 = OK/DRY_RUN/replay · 1 = falha de execucao (conexao/AUTH/envio) · 2 = uso · 3 = recusa de
guarda/configuracao · 4 = recusa de producao · 5 = senha vazada (recusa de gravacao).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

VERSAO = "titan-smtp-v1"

CODIGO_OK = 0
CODIGO_FALHA = 1
CODIGO_USO = 2
CODIGO_RECUSA = 3
CODIGO_PRODUCAO = 4
CODIGO_SEGREDO = 5

AMBIENTES = ("dev", "homolog", "prod")

# Matriz do provedor. Porta -> seguranca esperada. O provedor e o dono da matriz; o componente confere.
MATRIZ_PORTA_TLS = {465: "implicit_tls", 587: "starttls"}
PORTAS_RECUSADAS = {25: "PORTA_NAO_AUTORIZADA"}
SEGURANCAS = ("implicit_tls", "starttls", "nenhuma")

DOMINIO_DEV_PADRAO = "dev.local"
HOSTS_LOOPBACK = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

# Nomes de variavel -> campo. `senha` e o unico campo secreto.
VARIAVEIS = {
    "TRE_TITAN_SMTP_HOST": "host",
    "TRE_TITAN_SMTP_PORT": "porta",
    "TRE_TITAN_SMTP_SEGURANCA": "seguranca",
    "TRE_TITAN_USER": "usuario",
    "TRE_TITAN_PASSWORD": "senha",
    "TRE_TITAN_FROM": "remetente",
    "TRE_TITAN_TIMEOUT": "timeout",
    "TRE_TITAN_CA": "ca",
    "TRE_TITAN_DOMINIO_DEV": "dominio_dev",
    "TRE_TITAN_DESTINOS_PERMITIDOS": "destinos_permitidos",
    "TRE_TITAN_APROVACAO_HUMANA": "aprovacao_humana",
}
OBRIGATORIAS = ("host", "porta", "usuario", "senha")


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
    """Configuracao efetiva do SMTP. O objeto sabe se exibir SEM a senha (`publica()`)."""

    def __init__(self, valores: dict, faltantes: list, inferencias: list):
        self.host = valores.get("host")
        self.porta = valores.get("porta")
        self.seguranca = valores.get("seguranca")
        self.usuario = valores.get("usuario")
        self._senha = valores.get("senha")
        self.remetente = valores.get("remetente") or self.usuario
        self.timeout = valores.get("timeout")
        self.ca = valores.get("ca")
        self.dominio_dev = (valores.get("dominio_dev") or DOMINIO_DEV_PADRAO).lower()
        self.destinos_permitidos = [
            d.strip().lower() for d in (valores.get("destinos_permitidos") or "").split(",") if d.strip()
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
            "usuario": self.usuario,
            "senha": "<oculta>" if self._senha else "<vazio>",
            "remetente": self.remetente,
            "timeout": self.timeout,
            "ca": self.ca or "<CA do sistema>",
            "dominio_dev": self.dominio_dev,
            "destinos_permitidos": self.destinos_permitidos,
            "aprovacao_humana": self.aprovacao_humana or "<ausente>",
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

    if not valores.get("remetente") and valores.get("usuario"):
        valores["remetente"] = valores["usuario"]
        inferencias.append("remetente assumiu o usuario (TRE_TITAN_FROM ausente)")

    if not valores.get("timeout"):
        valores["timeout"] = 15
        inferencias.append("timeout padrao de 15s")
    else:
        try:
            valores["timeout"] = int(valores["timeout"])
        except ValueError:
            pass  # a validacao recusa com motivo nominal

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
    elif config.porta in PORTAS_RECUSADAS:
        recusas.append(Recusa(PORTAS_RECUSADAS[config.porta],
                              f"porta {config.porta} nao autorizada pelo provedor "
                              f"(use 465 implicit_tls ou 587 starttls)"))
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
                                  f"TRE_TITAN_SMTP_SEGURANCA explicita"))
    else:
        recusas.append(Recusa("PORTA_NAO_PREVISTA",
                              f"porta {config.porta} fora da matriz do provedor (465/587)"))

    if config.seguranca not in SEGURANCAS:
        recusas.append(Recusa("SEGURANCA_INVALIDA",
                              f"seguranca '{config.seguranca}' fora de {list(SEGURANCAS)}"))

    if config.seguranca == "nenhuma" and not loopback(config.host or ""):
        recusas.append(Recusa("TLS_OBRIGATORIO",
                              f"texto claro so e aceito em sink local; host '{config.host}' nao e loopback"))

    if not isinstance(config.timeout, int) or config.timeout <= 0:
        recusas.append(Recusa("TIMEOUT_INVALIDO", f"timeout '{config.timeout}' invalido"))

    if config.ca and not os.path.isfile(config.ca):
        recusas.append(Recusa("CA_AUSENTE", f"CA declarada nao existe: {config.ca}"))

    if config.usuario and " " in config.usuario:
        recusas.append(Recusa("USUARIO_INVALIDO", "usuario com espaco nao e endereco de caixa"))

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
        if config.remetente and dominio(config.remetente) not in (config.dominio_dev, "localhost"):
            recusas.append(Recusa("REMETENTE_NAO_DEV",
                                  f"dev usa remetente @{config.dominio_dev}; "
                                  f"'{dominio(config.remetente)}' nao e dominio de desenvolvimento"))
    if ambiente == "homolog" and not config.aprovacao_humana:
        recusas.append(Recusa("HOMOLOG_SEM_APROVACAO",
                              "homolog contra o provedor exige TRE_TITAN_APROVACAO_HUMANA registrada"))

    return recusas


def conferir_destino(config: Configuracao, ambiente: str, destino: str) -> None:
    """Recusa destinatario fora da politica do ambiente. Nao devolve nada: recusa ou passa."""
    if "@" not in destino:
        raise Recusa("DESTINO_INVALIDO", f"destinatario sem @: '{destino}'")
    if ambiente == "dev":
        if dominio(destino) not in (config.dominio_dev, "localhost"):
            raise Recusa("DESTINO_NAO_PERMITIDO",
                         f"dev so entrega no dominio @{config.dominio_dev}; "
                         f"'{dominio(destino)}' nao e dominio de desenvolvimento")
    elif config.destinos_permitidos and destino.lower() not in config.destinos_permitidos:
        raise Recusa("DESTINO_NAO_PERMITIDO",
                     f"destino '{destino}' fora de TRE_TITAN_DESTINOS_PERMITIDOS")
    elif not config.destinos_permitidos:
        raise Recusa("DESTINO_NAO_PERMITIDO",
                     "sem TRE_TITAN_DESTINOS_PERMITIDOS o componente nao envia para ninguem")


def contexto_tls(config: Configuracao) -> ssl.SSLContext:
    if config.ca:
        return ssl.create_default_context(cafile=config.ca)
    return ssl.create_default_context()


def conectar(config: Configuracao):
    """Abre a sessao SMTP conforme a matriz porta x TLS. Devolve (sessao, medidas)."""
    medidas = {"tls": config.seguranca, "ehlo": None, "capacidades": [], "versao_tls": None}
    ctx = contexto_tls(config)
    if config.seguranca == "implicit_tls":
        sessao = smtplib.SMTP_SSL(config.host, config.porta, timeout=config.timeout, context=ctx)
    else:
        sessao = smtplib.SMTP(config.host, config.porta, timeout=config.timeout)
    codigo, saudacao = sessao.ehlo()
    medidas["ehlo"] = codigo
    medidas["capacidades"] = sorted(set(getattr(sessao, "esmtp_features", {}) or {}))
    if config.seguranca == "starttls":
        sessao.starttls(context=ctx)
        sessao.ehlo()
    # Leitura defensiva: em "nenhuma" o socket e TCP puro e NAO tem version() (defeito real medido pelo
    # item 3.3 do aceite: `sock.version()` estourava AttributeError e matava a rodada sem relatorio).
    sock = getattr(sessao, "sock", None)
    medidas["versao_tls"] = sock.version() if isinstance(sock, ssl.SSLSocket) else None
    medidas["saudacao"] = saudacao.decode("utf-8", "replace") if isinstance(saudacao, bytes) else str(saudacao)
    return sessao, medidas


def autenticar(sessao, config: Configuracao) -> str:
    codigo, resposta = sessao.login(config.usuario, config.senha)
    if codigo != 235:
        raise Recusa("AUTH_RECUSADO", f"servidor respondeu {codigo}: {resposta}")
    texto = resposta.decode("utf-8", "replace") if isinstance(resposta, bytes) else str(resposta)
    return f"{codigo} {texto}"


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


def ja_enviado(caminho: str, chave: str) -> dict | None:
    for evento in reversed(ler_trilha(caminho)):
        if evento.get("chave_idempotencia") == chave and evento.get("resultado") == "ENVIADO" \
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
            self._gravar(self.caminho_trilha, campos, append=True)

    def _gravar(self, caminho: str, dados: dict, append: bool) -> None:
        texto = json.dumps(dados, ensure_ascii=False, sort_keys=True, indent=None)
        self._conferir_segredo(texto)
        modo = "a" if append else "w"
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        with open(caminho, modo, encoding="utf-8") as fh:
            fh.write(texto + ("\n" if append else ""))
            if not append:
                fh.write("\n")

    def _conferir_segredo(self, texto: str) -> None:
        """Fail-closed: se o valor da senha aparecer no que vai ser gravado, nao grava e sai 5."""
        if self.segredo and len(self.segredo) >= 4 and self.segredo in texto:
            print(json.dumps({"evento": "SENHA_VAZADA",
                              "motivo": "SENHA_VAZADA",
                              "detalhe": "o valor de TRE_TITAN_PASSWORD apareceu no registro; "
                                         "gravacao recusada (exit 5)"}, ensure_ascii=False))
            raise SystemExit(CODIGO_SEGREDO)

    def fechar(self) -> None:
        self.relatorio["veredito"] = self.relatorio.get("veredito") or "SMTP_TITAN_SEM_VEREDITO"
        texto = json.dumps(self.relatorio, ensure_ascii=False, sort_keys=True, indent=2)
        self._conferir_segredo(texto)
        if self.caminho_relatorio:
            os.makedirs(os.path.dirname(os.path.abspath(self.caminho_relatorio)), exist_ok=True)
            with open(self.caminho_relatorio, "w", encoding="utf-8") as fh:
                fh.write(texto + "\n")
            print(f"# relatorio: {self.caminho_relatorio}")


def identidade_da_config(config: Configuracao) -> str:
    """Impressao digital da CONFIGURACAO efetiva — sem a senha."""
    base = "|".join([str(config.host), str(config.porta), str(config.seguranca), str(config.usuario),
                     str(config.remetente), str(config.timeout), str(config.ca or "")])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def montar_mensagem(config: Configuracao, destino: str, assunto: str, corpo: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = config.remetente
    msg["To"] = destino
    msg["Subject"] = assunto
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=dominio(config.remetente) or "dev.local")
    msg["X-TRE-Card"] = "TRE-W6-E01-T01"
    msg.set_content(corpo)
    return msg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Titan SMTP v1 — configuracao validada e primitivo de envio guardado (TRE-W6-E01-T01)")
    parser.add_argument("--ambiente", choices=AMBIENTES, default="dev")
    acao = parser.add_mutually_exclusive_group(required=True)
    acao.add_argument("--planejar", action="store_true",
                      help="mostra a configuracao efetiva (senha mascarada) e o que falta; nunca conecta")
    acao.add_argument("--conferir", action="store_true",
                      help="valida completude, matriz porta x TLS e guardas do ambiente; nunca conecta")
    acao.add_argument("--provar", action="store_true",
                      help="conecta e mede EHLO/TLS/AUTH/NOOP sem enviar mensagem")
    acao.add_argument("--enviar", action="store_true", help="envia UMA mensagem (dry-run sem --confirmo)")
    acao.add_argument("--desfazer", metavar="CHAVE",
                      help="marca a chave da trilha como DESFEITO (dry-run ate --confirmo)")
    parser.add_argument("--para", action="append", default=[], help="destinatario (repetivel)")
    parser.add_argument("--assunto", default="(sem assunto)")
    parser.add_argument("--corpo", default="")
    parser.add_argument("--chave-idempotencia", default=None)
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

    if args.planejar:
        # Planejar NUNCA conecta e NUNCA falha por falta de config: ele declara o estado.
        recusas = validar(config, args.ambiente)
        emitir("PLANEJAR", "PLANO",
               identidade_config=identidade_da_config(config),
               recusas=[{"motivo": r.motivo, "detalhe": r.detalhe} for r in recusas])
        saida.relatorio["veredito"] = "CONFIG_COMPLETA" if config.completa else "CONFIG_INCOMPLETA"
        saida.fechar()
        return CODIGO_OK

    if args.desfazer:
        if not args.confirmo:
            emitir("DESFAZER", "DRY_RUN", chave_idempotencia=args.desfazer,
                   detalhe="sem --confirmo nada e gravado; um e-mail ja entregue nao volta")
            saida.relatorio["veredito"] = "DESFAZER_DRY_RUN"
            saida.fechar()
            return CODIGO_OK
        registros = ler_trilha(args.registro or "")
        if not any(e.get("chave_idempotencia") == args.desfazer for e in registros):
            emitir("DESFAZER", "NAO_ENCONTRADO", chave_idempotencia=args.desfazer)
            saida.relatorio["veredito"] = "DESFAZER_NAO_ENCONTRADO"
            saida.fechar()
            return CODIGO_FALHA
        emitir("DESFAZER", "DESFEITO", chave_idempotencia=args.desfazer,
               detalhe="trilha marcada; a auditoria original e preservada")
        saida.relatorio["veredito"] = "DESFEITO_REGISTRADO"
        saida.fechar()
        return CODIGO_OK

    recusas = validar(config, args.ambiente)
    if recusas:
        for r in recusas:
            emitir(r.motivo, "RECUSADO", detalhe=r.detalhe)
        saida.relatorio["veredito"] = "RECUSADO"
        saida.fechar()
        return max(r.codigo for r in recusas)

    if args.conferir:
        emitir("CONFERIR", "OK", identidade_config=identidade_da_config(config))
        saida.relatorio["veredito"] = "CONFIGURACAO_OK"
        saida.fechar()
        return CODIGO_OK

    def recusas_de_destino() -> list:
        achados = []
        for destino in args.para:
            try:
                conferir_destino(config, args.ambiente, destino)
            except Recusa as r:
                achados.append(r)
        return achados

    if args.enviar and not args.confirmo:
        recusas = recusas_de_destino()
        if recusas:
            for r in recusas:
                emitir(r.motivo, "RECUSADO", detalhe=r.detalhe)
            saida.relatorio["veredito"] = "RECUSADO"
            saida.fechar()
            return max(r.codigo for r in recusas)
        emitir("ENVIAR", "DRY_RUN", destinos=args.para,
               chave_idempotencia=args.chave_idempotencia,
               detalhe="sem --confirmo NADA e enviado e nenhuma conexao e aberta")
        saida.relatorio["veredito"] = "DRY_RUN"
        saida.fechar()
        return CODIGO_OK

    if args.enviar:
        if not args.chave_idempotencia:
            emitir("ENVIAR", "RECUSADO", detalhe="--chave-idempotencia e obrigatoria (retry nao duplica)")
            saida.relatorio["veredito"] = "RECUSADO"
            saida.fechar()
            return CODIGO_USO
        recusas = recusas_de_destino()
        if recusas:
            for r in recusas:
                emitir(r.motivo, "RECUSADO", detalhe=r.detalhe)
            saida.relatorio["veredito"] = "RECUSADO"
            saida.fechar()
            return max(r.codigo for r in recusas)
        anterior = ja_enviado(args.registro or "", args.chave_idempotencia)
        if anterior:
            emitir("ENVIAR", "JA_ENVIADO", chave_idempotencia=args.chave_idempotencia,
                   destinos=args.para,
                   detalhe=f"replay: chave ja vista em {anterior.get('quando')}; nada novo saiu")
            saida.relatorio["veredito"] = "JA_ENVIADO"
            saida.fechar()
            return CODIGO_OK

    sessao = None
    try:
        sessao, medidas = conectar(config)
        auth = autenticar(sessao, config)
        if args.provar:
            sessao.noop()
            emitir("PROVAR", "OK", **medidas, autenticacao=auth,
                   identidade_config=identidade_da_config(config))
            saida.relatorio["veredito"] = "CONEXAO_OK"
            saida.fechar()
            return CODIGO_OK

        msg = montar_mensagem(config, args.para[0], args.assunto, args.corpo)
        recusados = sessao.send_message(msg, from_addr=config.remetente, to_addrs=args.para)
        emitir("ENVIAR", "ENVIADO", **medidas, autenticacao=auth,
               chave_idempotencia=args.chave_idempotencia, destinos=args.para,
               message_id=msg["Message-ID"], assunto=args.assunto,
               tamanho_bytes=len(bytes(msg)), recusados_por_servidor=sorted(recusados.keys()),
               identidade_config=identidade_da_config(config))
        saida.relatorio["veredito"] = "ENVIADO"
        saida.fechar()
        return CODIGO_OK
    except Recusa as r:
        emitir(r.motivo, "RECUSADO", detalhe=r.detalhe)
        saida.relatorio["veredito"] = "RECUSADO"
        saida.fechar()
        return r.codigo
    except (OSError, smtplib.SMTPException, ssl.SSLError) as e:
        # A mensagem de erro do provedor nao carrega a senha; se carregasse, `_conferir_segredo`
        # barra a gravacao antes de qualquer log com segredo.
        emitir("FALHA", "FALHOU", erro=type(e).__name__, detalhe=str(e)[:300])
        saida.relatorio["veredito"] = "FALHOU"
        saida.fechar()
        return CODIGO_FALHA
    finally:
        if sessao is not None:
            try:
                sessao.quit()
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
    except Recusa as recusa:  # recusa levantada fora do try de sessao (ex.: destino)
        print(json.dumps({"evento": recusa.motivo, "resultado": "RECUSADO", "detalhe": recusa.detalhe},
                         ensure_ascii=False))
        sys.exit(recusa.codigo)
