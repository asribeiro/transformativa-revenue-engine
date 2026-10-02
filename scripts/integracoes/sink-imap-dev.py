#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sink IMAP de desenvolvimento (`sink-imap-dev`, card TRE-W6-E01-T02).

Servidor IMAP minimo, SÓ stdlib, que existe para uma coisa: dar ao aceite do card uma ponta REAL
onde o componente `hermes/integracoes/titan/imap_titan.py` possa conectar, negociar TLS, autenticar e
LER uma caixa com mensagens — sem credencial Titan, sem tocar o provedor e sem nada em producao
(ADR-005; o papel `dev-harness` nao tem TRE_TITAN_*, `hermes/policies/dev-harness.yaml`).

O que ele NAO e: nao e o Titan, nao e um servidor de correio, nao entrega nada, nao escuta fora do
loopback por padrao e nao registra a senha: o unico credencial que ele anota e o USUARIO autenticado
(a senha e comparada e descartada na hora — por isso o aceite pode procurar o valor da senha em todos
os artefatos e exigir zero ocorrencia).

Por que ele e um sink ESTRITO: o objetivo do aceite e medir a INTENCAO do cliente. O RFC 3501 diz que
EXAMINE (caixa read-only) descarta mudanca de flag; um sink fiel a isso mascararia um cliente que usa
`BODY[]` em vez de `BODY.PEEK[]` — a flag nao apareceria por acidente da modalidade de selecao. Este
sink, por isso, APLICA `\\Seen` quando o cliente pede uma busca sem PEEK (mesmo em selecao read-only
declarada) e registra o fato em duas marcas: `buscas_sem_peek` e `mensagens_marcadas_lidas`. Assim o
aceite mede as duas coisas — o que o cliente pediu (registro do comando) e o efeito que aquilo teria
no provedor real. O resto do protocolo e tratado como o provedor trataria: EXAMINE nao apaga flag
nenhuma por si so.

Uso (o aceite sobe e derruba este processo):

  python3 scripts/integracoes/sink-imap-dev.py --porta 2993 --modo implicit_tls \\
      --cert /tmp/ca/dev.pem --chave /tmp/ca/dev.key --senha senha-do-sink \\
      --captura /tmp/sink/comandos.jsonl --pronto /tmp/sink/pronto --pidfile /tmp/sink/pid
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
import ssl
import sys
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

CAPTURA = None
MODO = "nenhuma"
CONTEXTO = None
SENHA = ""
MENSAGENS = []
LIDAS_INICIAIS = 0
LOCK = threading.Lock()
# Contadores ACUMULADOS entre conexoes: o aceite precisa do total da rodada inteira, nao so do que a
# ultima conexao fez (cada conexao do componente abre e fecha a sua propria sessao).
ACUMULADO = {"buscas_sem_peek": 0, "corpos_buscados": 0, "comandos_de_escrita": [], "selecoes": [],
             "logins": []}


def acumular(chave: str, valor=None) -> None:
    with LOCK:
        if chave in ("comandos_de_escrita", "selecoes", "logins"):
            if valor not in ACUMULADO[chave]:
                ACUMULADO[chave].append(valor)
        else:
            ACUMULADO[chave] += valor


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def registrar(evento: dict) -> None:
    if not CAPTURA:
        return
    with LOCK:
        with open(CAPTURA, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(evento, ensure_ascii=False, sort_keys=True) + "\n")


def montar_mensagens(quantidade: int, lidas: int) -> list:
    """Mensagens de fixture deterministicas: mesmos UIDs, Message-IDs e corpos em toda rodada."""
    mensagens = []
    for i in range(1, quantidade + 1):
        msg = EmailMessage()
        msg["From"] = f"lead{i}@cliente-dev.local"
        msg["To"] = "anderson.ribeiro@transformativa.com.br"
        msg["Subject"] = f"Resposta do prospect #{i} (fixture de dev)"
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = f"<fixture-{i}@cliente-dev.local>"
        msg.set_content(f"Corpo da mensagem {i} da fixture de desenvolvimento do TRE.\n"
                        f"Marcador: FIXTURE-{i}.\n")
        flags = {"\\Seen"} if i <= lidas else set()
        mensagens.append({"uid": i, "raw": msg.as_bytes(), "flags": flags})
    return mensagens


def parsear_tokens(texto: str) -> list:
    """Tokenizer de IMAP suficiente para este sink: aspas, parenteses e colchetes como tokens."""
    tokens, atual, i, em_aspas = [], "", 0, False
    while i < len(texto):
        c = texto[i]
        if em_aspas:
            if c == "\\" and i + 1 < len(texto):
                atual += texto[i + 1]
                i += 2
                continue
            if c == '"':
                em_aspas = False
                i += 1
                continue
            atual += c
        elif c == '"':
            em_aspas = True
        elif c in " ()[]":
            if atual:
                tokens.append(atual)
                atual = ""
            if c not in " ":
                tokens.append(c)
        else:
            atual += c
        i += 1
    if atual:
        tokens.append(atual)
    return tokens


class Caixa:
    """Estado da caixa do sink: mensagens, flags e as marcas que o aceite le."""

    def __init__(self):
        self.mensagens = MENSAGENS
        self.selecao = None          # "EXAMINE" | "SELECT" | None
        self.autenticado_como = None
        self.comandos = []
        self.escritas = []
        self.buscas_sem_peek = 0
        self.corpos_buscados = 0
        self.versao_tls = None

    def resumo(self, evento: str = "ESTADO") -> dict:
        registradas = [m["uid"] for m in self.mensagens]
        lidas = [m["uid"] for m in self.mensagens if "\\Seen" in m["flags"]]
        return {
            "evento": evento,
            "quando": agora(),
            "modo_tls": MODO,
            "versao_tls": self.versao_tls,
            "autenticado_como": self.autenticado_como,
            "selecao": self.selecao,
            "mensagens": len(self.mensagens),
            "uids": registradas,
            "mensagens_marcadas_lidas": lidas,
            "buscas_sem_peek": self.buscas_sem_peek,
            "corpos_buscados": self.corpos_buscados,
            "comandos": self.comandos,
            "comandos_de_escrita": self.escritas,
            "total_buscas_sem_peek": ACUMULADO["buscas_sem_peek"],
            "total_corpos_buscados": ACUMULADO["corpos_buscados"],
            "total_comandos_de_escrita": ACUMULADO["comandos_de_escrita"],
            "selecoes": ACUMULADO["selecoes"],
            "logins": ACUMULADO["logins"],
        }


class Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def responder(self, texto: str) -> None:
        self.wfile.write(texto.encode("utf-8") + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        caixa = Caixa()
        caixa.versao_tls = (self.connection.version() if isinstance(self.connection, ssl.SSLSocket)
                            else None)
        try:
            if MODO == "implicit_tls":
                self.connection = CONTEXTO.wrap_socket(self.connection, server_side=True)
                self.rfile = self.connection.makefile("rb", self.rbufsize)
                self.wfile = self.connection.makefile("wb", self.wbufsize)
                caixa.versao_tls = self.connection.version()
            self.responder("* OK [CAPABILITY IMAP4rev1 STARTTLS AUTH=PLAIN] sink-imap-dev pronto")
            while True:
                linha, encerrar = self.ler_comando()
                if linha is None or encerrar:
                    return
                if not linha.strip():
                    continue
                if not self.despachar(caixa, linha):
                    registrar(caixa.resumo("ESTADO_FINAL"))
                    return
        except (ssl.SSLError, ConnectionError, socket.timeout, OSError) as e:
            registrar(caixa.resumo("ESTADO_FINAL"))
            print(f"[sink-imap] conexao encerrada: {type(e).__name__}", flush=True)

    def ler_comando(self):
        """Le um comando completo (suporta literal {n} no meio) e devolve (texto, encerrar)."""
        buffer = b""
        while True:
            linha = self.rfile.readline()
            if not linha:
                return None, True
            buffer += linha
            if linha.endswith(b"}\r\n") or linha.endswith(b"}\n"):
                achado = linha.rstrip(b"\r\n").rsplit(b"{", 1)
                try:
                    tamanho = int(achado[1].rstrip(b"}\r\n"))
                except (IndexError, ValueError):
                    break
                dados = self.rfile.read(tamanho)
                buffer += dados
                continue
            break
        return buffer.decode("utf-8", "replace").rstrip("\r\n"), False

    def despachar(self, caixa: Caixa, linha: str) -> bool:
        """Executa um comando. Devolve False quando a conexao deve encerrar."""
        partes = parsear_tokens(linha)
        if len(partes) < 2:
            self.responder("* BAD comando incompleto")
            return True
        tag, comando = partes[0], partes[1].upper()
        argumentos = " ".join(partes[2:])
        registro = f"{comando} {argumentos}".strip()[:300]
        if comando == "LOGIN":
            # A senha NUNCA entra na captura: ela e comparada e descartada. O defeito de registrar o
            # comando cru foi medido na rodada 1 (a captura guardava "LOGIN <usuario> <senha>").
            usuario = partes[2] if len(partes) > 2 else ""
            registro = f"LOGIN {usuario} <senha-oculta>"
            acumular("logins", usuario)
        caixa.comandos.append(registro)
        if comando in ("STORE", "EXPUNGE", "DELETE", "COPY", "MOVE", "APPEND", "CREATE", "SUBSCRIBE",
                       "UNSUBSCRIBE", "RENAME", "SETACL"):
            caixa.escritas.append(registro[:200])
            acumular("comandos_de_escrita", registro[:200])
        if comando in ("EXAMINE", "SELECT"):
            acumular("selecoes", registro)

        if comando == "CAPABILITY":
            capacidades = "IMAP4rev1 AUTH=PLAIN UIDPLUS"
            if MODO == "starttls":
                capacidades = "IMAP4rev1 STARTTLS AUTH=PLAIN UIDPLUS"
            self.responder(f"* CAPABILITY {capacidades}")
            self.responder(f"{tag} OK CAPABILITY pronto")
        elif comando == "STARTTLS":
            if MODO != "starttls":
                self.responder(f"{tag} NO STARTTLS nao anunciado por este sink")
                return True
            self.responder(f"{tag} OK inicie a negociacao TLS")
            self.connection = CONTEXTO.wrap_socket(self.connection, server_side=True)
            self.rfile = self.connection.makefile("rb", self.rbufsize)
            self.wfile = self.connection.makefile("wb", self.wbufsize)
            caixa.versao_tls = self.connection.version()
            caixa.comandos.append("STARTTLS negociado")
        elif comando == "NOOP":
            self.responder(f"{tag} OK NOOP pronto")
        elif comando == "LOGOUT":
            self.responder("* BYE sink-imap-dev encerrando")
            self.responder(f"{tag} OK LOGOUT pronto")
            return False
        elif comando == "LOGIN":
            usuario = partes[2] if len(partes) > 2 else ""
            senha_recebida = partes[3] if len(partes) > 3 else ""
            if SENHA and senha_recebida != SENHA:
                self.responder(f"{tag} NO [AUTHENTICATIONFAILED] credencial recusada pelo sink")
            else:
                caixa.autenticado_como = usuario
                self.responder(f"{tag} OK [CAPABILITY IMAP4rev1 UIDPLUS] LOGIN pronto")
        elif comando in ("EXAMINE", "SELECT"):
            if len(partes) < 3:
                self.responder(f"{tag} BAD falta a caixa")
                return True
            caixa.selecao = comando
            marca = "READ-ONLY" if comando == "EXAMINE" else "READ-WRITE"
            self.responder(f"* {len(caixa.mensagens)} EXISTS")
            self.responder("* OK [UIDVALIDITY 999] UIDs validos")
            nao_lidas = sum(1 for m in caixa.mensagens if "\\Seen" not in m["flags"])
            self.responder(f"* OK [UNSEEN {nao_lidas}] primeira nao lida")
            self.responder("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
            self.responder(f"{tag} OK [{marca}] {comando} concluido")
        elif comando == "STATUS":
            nao_lidas = sum(1 for m in caixa.mensagens if "\\Seen" not in m["flags"])
            self.responder(f'* STATUS {partes[2] if len(partes) > 2 else "INBOX"} '
                           f'(MESSAGES {len(caixa.mensagens)} UNSEEN {nao_lidas} UIDVALIDITY 999)')
            self.responder(f"{tag} OK STATUS pronto")
        elif comando == "LIST":
            self.responder('* LIST (\\HasNoChildren) "/" "INBOX"')
            self.responder('* LIST (\\HasNoChildren) "/" "Sent"')
            self.responder(f"{tag} OK LIST pronto")
        elif comando == "UID":
            sub = partes[2].upper() if len(partes) > 2 else ""
            if sub == "SEARCH":
                uids = " ".join(str(m["uid"]) for m in caixa.mensagens)
                self.responder(f"* SEARCH {uids}".rstrip())
                self.responder(f"{tag} OK UID SEARCH pronto")
            elif sub == "FETCH":
                self.uid_fetch(caixa, tag, partes)
            else:
                self.responder(f"{tag} BAD UID {sub} nao implementado neste sink")
        elif comando in ("STORE", "EXPUNGE", "DELETE", "COPY", "MOVE", "APPEND", "CREATE",
                         "SUBSCRIBE", "UNSUBSCRIBE"):
            # Implementados minimamente para que a tentativa de escrita apareca na captura: o aceite
            # exige `comandos_de_escrita: []` — o componente NAO escreve na caixa.
            self.responder(f"{tag} OK {comando} registrado pelo sink")
        else:
            self.responder(f"{tag} BAD {comando} nao implementado neste sink")
        return True

    def uid_fetch(self, caixa: Caixa, tag: str, partes: list) -> None:
        uid = partes[3] if len(partes) > 3 else ""
        itens = " ".join(partes[4:]).upper()
        alvo = [m for m in caixa.mensagens if str(m["uid"]) == str(uid)]
        if not alvo:
            self.responder(f"{tag} OK UID FETCH sem mensagem correspondente")
            return
        mensagem = alvo[0]
        marca_peek = "BODY.PEEK" in itens
        pediu_corpo = "BODY" in itens
        pediu_cabecalho = "HEADER.FIELDS" in itens
        if pediu_corpo and not marca_peek:
            # Sink ESTRITO: a busca sem PEEK marca \\Seen e fica registrada (ver docstring).
            caixa.buscas_sem_peek += 1
            acumular("buscas_sem_peek", 1)
            mensagem["flags"].add("\\Seen")
        flags = " ".join(sorted(mensagem["flags"]))
        meta = f"UID {mensagem['uid']} FLAGS ({flags})"
        if pediu_corpo:
            conteudo = self.recortar_cabecalho(mensagem["raw"]) if pediu_cabecalho else mensagem["raw"]
            if not pediu_cabecalho:
                caixa.corpos_buscados += 1
                acumular("corpos_buscados", 1)
            nome = "BODY[HEADER.FIELDS (MESSAGE-ID FROM TO SUBJECT DATE)]" if pediu_cabecalho else "BODY[]"
            self.wfile.write(f"* {mensagem['uid']} FETCH ({meta} {nome} {{{len(conteudo)}}}\r\n"
                             .encode("utf-8"))
            self.wfile.write(conteudo)
            self.wfile.write(b")\r\n")
            self.wfile.flush()
        else:
            self.responder(f"* {mensagem['uid']} FETCH ({meta})")
        self.responder(f"{tag} OK UID FETCH pronto")

    @staticmethod
    def recortar_cabecalho(bruto: bytes) -> bytes:
        posicao = bruto.find(b"\r\n\r\n")
        return bruto[: posicao + 2] if posicao > 0 else bruto


class Servidor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    global CAPTURA, MODO, CONTEXTO, SENHA, MENSAGENS, LIDAS_INICIAIS
    p = argparse.ArgumentParser(description="Sink IMAP de desenvolvimento (TRE-W6-E01-T02)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--porta", type=int, default=2993)
    p.add_argument("--modo", choices=("implicit_tls", "starttls", "nenhuma"), default="implicit_tls")
    p.add_argument("--cert", default=None)
    p.add_argument("--chave", default=None)
    p.add_argument("--senha", default="", help="senha que o sink aceita (vazia = aceita qualquer uma)")
    p.add_argument("--mensagens", type=int, default=3)
    p.add_argument("--lidas", type=int, default=0, help="quantas mensagens ja vem marcadas como lidas")
    p.add_argument("--captura", required=True)
    p.add_argument("--pronto", required=True)
    p.add_argument("--pidfile", default=None)
    args = p.parse_args()

    CAPTURA, MODO, SENHA = args.captura, args.modo, args.senha
    LIDAS_INICIAIS = args.lidas
    MENSAGENS = montar_mensagens(args.mensagens, args.lidas)
    if args.modo in ("implicit_tls", "starttls"):
        CONTEXTO = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        if not (args.cert and args.chave):
            print("modo com TLS exige --cert e --chave", file=sys.stderr)
            return 2
        CONTEXTO.load_cert_chain(args.cert, args.chave)

    os.makedirs(os.path.dirname(os.path.abspath(args.captura)), exist_ok=True)
    if not os.path.isfile(args.captura):
        open(args.captura, "a", encoding="utf-8").close()

    servidor = Servidor((args.host, args.porta), Handler)
    with open(args.pronto, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"escutando": f"{args.host}:{servidor.server_address[1]}", "modo": args.modo,
                             "mensagens": args.mensagens, "quando": agora()}))
    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    print(f"[sink-imap] escutando em {args.host}:{servidor.server_address[1]} modo={args.modo} "
          f"mensagens={args.mensagens}", flush=True)
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
