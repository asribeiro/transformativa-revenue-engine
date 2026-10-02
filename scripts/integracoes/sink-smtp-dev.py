#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sink SMTP de desenvolvimento (`sink-smtp-dev`, card TRE-W6-E01-T01).

Servidor SMTP minimo, SÓ stdlib, que existe para uma coisa: dar ao aceite do card uma ponta REAL
onde o componente `hermes/integracoes/titan/smtp_titan.py` possa conectar, negociar TLS, autenticar e
ENTREGAR uma mensagem em desenvolvimento — sem credencial Titan, sem e-mail saindo para a internet e
sem nada em producao (ADR-005; o papel `dev-harness` nao pode enviar e-mail em nome da Transformativa).

O que ele NAO e: nao e o Titan, nao e um MTA, nao faz retry, nao entrega em lugar nenhum, nao escuta
fora do loopback (a menos que alguem peca explicitamente por `--host 0.0.0.0`, o que a guarda do
componente reprova em dev) e nao registra senha nenhuma: o unico credencial que ele anota e o USUARIO
autenticado — a senha e descartada na hora (por isso o aceite pode procurar o valor da senha em todos
os artefatos e exigir zero ocorrencia).

Uso (o aceite sobe e derruba este processo):

  python3 scripts/integracoes/sink-smtp-dev.py --porta 2465 --modo implicit_tls \\
      --cert /tmp/ca/dev.pem --chave /tmp/ca/dev.key --captura /tmp/sink/mensagens.jsonl \\
      --pronto /tmp/sink/pronto --pidfile /tmp/sink/pid
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import socketserver
import ssl
import sys
import threading
from datetime import datetime, timezone

CAPTURA = None
MODO = "nenhuma"
CONTEXTO = None
LOCK = threading.Lock()


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def registrar(evento: dict) -> None:
    if not CAPTURA:
        return
    with LOCK:
        with open(CAPTURA, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(evento, ensure_ascii=False, sort_keys=True) + "\n")
        with open(CAPTURA, "r", encoding="utf-8") as fh:
            contagem = sum(1 for linha in fh if linha.strip())
    print(f"[sink] {evento.get('evento')} total={contagem}", flush=True)


def assunto_de(dados: str) -> str:
    for linha in dados.splitlines():
        if linha.lower().startswith("subject:"):
            return linha.split(":", 1)[1].strip()
    return ""


def extrair_endereco(comando: str) -> str:
    """`MAIL FROM:<a@b> size=305` -> `a@b`. O parametro do ESMTP fica fora do endereco."""
    resto = comando.split(":", 1)[1] if ":" in comando else comando
    if "<" in resto and ">" in resto:
        return resto.split("<", 1)[1].split(">", 1)[0].strip()
    return resto.strip().split(" ", 1)[0].strip()


class Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def responder(self, texto: str) -> None:
        self.wfile.write(texto.encode("utf-8") + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        sessao = {"autenticado_como": None, "tls": MODO, "versao_tls": None}
        try:
            if MODO == "implicit_tls":
                self.connection = CONTEXTO.wrap_socket(self.connection, server_side=True)
                self.rfile = self.connection.makefile("rb", self.rbufsize)
                self.wfile = self.connection.makefile("wb", self.wbufsize)
            sessao["versao_tls"] = (
                self.connection.version() if isinstance(self.connection, ssl.SSLSocket) else None
            )
            self.responder("220 sink-smtp-dev ESMTP pronto")
            estado = {"de": None, "para": [], "dados": False, "buffer": [], "auth_pendente": None,
                      "auth_usuario": None}
            while True:
                linha = self.rfile.readline()
                if not linha:
                    return
                if estado["dados"]:
                    texto = linha.decode("utf-8", "replace")
                    if texto.rstrip("\r\n") == ".":
                        self._capturar(estado, sessao)
                        estado["dados"] = False
                        estado["buffer"] = []
                        self.responder("250 2.0.0 Ok: mensagem capturada pelo sink de dev")
                        continue
                    estado["buffer"].append(texto)
                    continue

                comando = linha.decode("utf-8", "replace").strip()
                alto = comando.upper()

                if estado["auth_pendente"] == "usuario":
                    estado["auth_usuario"] = base64.b64decode(comando).decode("utf-8", "replace")
                    estado["auth_pendente"] = "senha"
                    self.responder("334 UGFzc3dvcmQ6")
                    continue
                if estado["auth_pendente"] == "senha":
                    # A senha NAO e guardada — nem em variavel depois desta linha.
                    estado["auth_pendente"] = None
                    self.responder("235 2.7.0 Autenticacao bem-sucedida")
                    continue
                if estado["auth_pendente"] == "plain":
                    estado["auth_pendente"] = None
                    partes = base64.b64decode(comando).split(b"\x00")
                    estado["auth_usuario"] = partes[-2].decode("utf-8", "replace") if len(partes) > 2 else ""
                    self.responder("235 2.7.0 Autenticacao bem-sucedida")
                    continue

                if alto.startswith("EHLO") or alto.startswith("HELO"):
                    self.wfile.write(b"250-sink-smtp-dev\r\n")
                    self.wfile.write(b"250-8BITMIME\r\n")
                    self.wfile.write(b"250-SIZE 10485760\r\n")
                    self.wfile.write(b"250-AUTH LOGIN PLAIN\r\n")
                    if MODO == "starttls":
                        self.wfile.write(b"250-STARTTLS\r\n")
                    self.wfile.write(b"250 OK\r\n")
                    self.wfile.flush()
                elif alto == "STARTTLS":
                    if MODO != "starttls":
                        self.responder("502 STARTTLS nao anunciado")
                        continue
                    self.responder("220 2.0.0 Pronto para STARTTLS")
                    self.connection = CONTEXTO.wrap_socket(self.connection, server_side=True)
                    self.rfile = self.connection.makefile("rb", self.rbufsize)
                    self.wfile = self.connection.makefile("wb", self.wbufsize)
                    sessao["tls"] = "starttls"
                    sessao["versao_tls"] = self.connection.version()
                elif alto.startswith("AUTH LOGIN"):
                    resto = comando[len("AUTH LOGIN"):].strip()
                    if resto:
                        # smtplib manda o usuario como resposta inicial: AUTH LOGIN <b64(usuario)>
                        estado["auth_usuario"] = base64.b64decode(resto).decode("utf-8", "replace")
                        estado["auth_pendente"] = "senha"
                        self.responder("334 UGFzc3dvcmQ6")
                    else:
                        estado["auth_pendente"] = "usuario"
                        self.responder("334 VXNlcm5hbWU6")
                elif alto.startswith("AUTH PLAIN"):
                    pacote = comando.split(" ", 2)
                    if len(pacote) == 3:
                        partes = base64.b64decode(pacote[2]).split(b"\x00")
                        estado["auth_usuario"] = partes[-2].decode("utf-8", "replace") if len(partes) > 2 else ""
                        self.responder("235 2.7.0 Autenticacao bem-sucedida")
                    else:
                        estado["auth_pendente"] = "plain"
                        self.responder("334 ")
                elif alto.startswith("MAIL FROM"):
                    estado["de"] = extrair_endereco(comando)
                    self.responder("250 2.1.0 Remetente aceito")
                elif alto.startswith("RCPT TO"):
                    estado["para"].append(extrair_endereco(comando))
                    self.responder("250 2.1.5 Destinatario aceito")
                elif alto == "DATA":
                    estado["dados"] = True
                    self.responder("354 Envie os dados e finalize com <CRLF>.<CRLF>")
                elif alto in ("NOOP", "RSET"):
                    if alto == "RSET":
                        estado["de"], estado["para"] = None, []
                    self.responder("250 2.0.0 OK")
                elif alto == "QUIT":
                    self.responder("221 2.0.0 Ate logo")
                    return
                else:
                    self.responder("502 5.5.2 Comando nao implementado")
        except (ssl.SSLError, ConnectionError, socket.timeout, OSError) as e:
            print(f"[sink] conexao encerrada: {type(e).__name__}", flush=True)

    def _capturar(self, estado: dict, sessao: dict) -> None:
        dados = "".join(estado["buffer"])
        registrar({
            "evento": "MENSAGEM",
            "recebido_em": agora(),
            "modo": MODO,
            "tls": sessao.get("tls"),
            "versao_tls": sessao.get("versao_tls"),
            "autenticado_como": estado.get("auth_usuario"),
            "mail_from": estado.get("de"),
            "rcpt_to": estado.get("para"),
            "assunto": assunto_de(dados),
            "tamanho_bytes": len(dados.encode("utf-8")),
            "dados": dados,
        })


class Servidor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    global CAPTURA, MODO, CONTEXTO
    p = argparse.ArgumentParser(description="Sink SMTP de desenvolvimento (TRE-W6-E01-T01)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--porta", type=int, default=2465)
    p.add_argument("--modo", choices=("implicit_tls", "starttls", "nenhuma"), default="implicit_tls")
    p.add_argument("--cert", default=None)
    p.add_argument("--chave", default=None)
    p.add_argument("--captura", required=True)
    p.add_argument("--pronto", required=True)
    p.add_argument("--pidfile", default=None)
    args = p.parse_args()

    CAPTURA, MODO = args.captura, args.modo
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
                             "quando": agora()}))
    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    print(f"[sink] escutando em {args.host}:{servidor.server_address[1]} modo={args.modo}", flush=True)
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
