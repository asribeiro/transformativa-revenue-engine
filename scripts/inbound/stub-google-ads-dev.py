#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stub LOCAL do resolvedor de `gclid` da Ads API — apoio do aceite do card TRE-W7-E03-T01.

Nao e' a Ads API: e' a PORTA declarada do card (`GET {porta}/gclid/<gclid>`) respondida em loopback,
para medir o caminho da atribuicao sem developer token nem OAuth (ADR-005: nada nasce em producao e
nenhuma credencial real entra). O unico `gclid` conhecido responde `RESOLVIDO` com uma campanha; os
outros respondem `NAO_ENCONTRADO` — que e' o caso em que o canal e' google mas a campanha NAO e'
atribuida (fail-closed medido no aceite).

Guardas: so' escuta em loopback; ABORTA se a porta ja' estiver ocupada (sobra de rodada anterior da
falso negativo) e grava pidfile no diretorio de trabalho para o aceite derrubar o processo.

Uso: python3 scripts/inbound/stub-google-ads-dev.py --porta 8899 --gclid-bom <gclid> --dir /tmp/x
Exit: 0 = serviu e saiu · 3 = guarda/ambiente recusou.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

CODIGO_RECUSA = 3


class Resolvedor(BaseHTTPRequestHandler):
    gclid_bom = ""
    campanha = {"campanha_id": "cmp-900", "campanha_nome": "PMEs SP - Agentes IA",
                "grupo_anuncio_id": "ag-9", "palavra_chave": "agentes ia"}

    def log_message(self, *args):  # assinatura da stdlib: o aceite mede a resposta, nao o log
        return

    def do_GET(self):  # noqa: N802 — assinatura da stdlib
        if not self.path.startswith("/gclid/"):
            self._responder(404, {"status": "ROTA_DESCONHECIDA"})
            return
        gclid = self.path.split("/gclid/", 1)[1]
        if gclid == self.gclid_bom:
            self._responder(200, dict(self.campanha, status="RESOLVIDO"))
            return
        self._responder(200, {"status": "NAO_ENCONTRADO", "campanha_id": None, "campanha_nome": None,
                              "grupo_anuncio_id": None, "palavra_chave": None})

    def _responder(self, codigo: int, corpo: dict) -> None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)


def porta_livre(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", porta))
            return True
        except OSError:
            return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stub local do resolvedor de gclid (aceite TRE-W7-E03-T01)")
    parser.add_argument("--porta", type=int, required=True)
    parser.add_argument("--gclid-bom", required=True)
    parser.add_argument("--dir", required=True, help="diretorio do pidfile")
    args = parser.parse_args(argv)

    if not porta_livre(args.porta):
        print(json.dumps({"evento": "RECUSA", "motivo": "PORTA_OCUPADA", "porta": args.porta}))
        return CODIGO_RECUSA

    Resolvedor.gclid_bom = args.gclid_bom
    os.makedirs(args.dir, exist_ok=True)
    servidor = HTTPServer(("127.0.0.1", args.porta), Resolvedor)
    with open(os.path.join(args.dir, "stub.pid"), "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    print(json.dumps({"evento": "STUB_NO_AR", "endereco": f"http://127.0.0.1:{args.porta}",
                      "gclid_conhecido": args.gclid_bom[:12] + "...", "pid": os.getpid()}))
    sys.stdout.flush()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
