#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stub LOCAL da Graph API do Meta (dev/aceite) — card TRE-W7-E02-T01.

NAO e' o Meta: e' o SINK do aceite, com o MESMO contrato de leitura que o primitivo do componente usa
(`GET <base>/<versao>/<leadgen_id>?fields=...`, token no cabecalho `Authorization: Bearer`). Existe
porque o dev nao tem token real de pagina: a prova em dev e' contra este stub em LOOPBACK (ADR-005) e
a prova contra a Graph API real e' do card de homologacao, declarada no runbook.

Registra TODA requisicao em JSONL (sem o token) para o aceite medir o que saiu do componente:
`{"leadgen_id","caminho","authorization_presente","quando"}` — e' assim que o aceite prova que o
replay idempotente NAO re-chama a API.

Respostas:
  - token ausente/errado  -> 401 (erro DEFINITIVO: o componente nao pode retentar);
  - lead desconhecido     -> 404 (LEAD_INDISPONIVEL, sem retry);
  - `--falhar-primeira <leadgen_id>` -> 500 na PRIMEIRA chamada daquele lead e 200 depois (mede o
    retry limitado do componente, uma vez so');
  - `--falhar-sempre <leadgen_id>` -> 500 em TODA chamada (mede o TETO de tentativas do componente).

Uso:
  TRE_META_STUB_TOKEN=... TRE_META_STUB_LEADS=leads.json TRE_META_STUB_REGISTRO=/tmp/graph.jsonl \\
      python3 scripts/agentes/stub-meta-graph-dev.py --porta 8799 [--falhar-primeira lead-2]
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TOKEN = os.environ.get("TRE_META_STUB_TOKEN") or ""
LEADS = os.environ.get("TRE_META_STUB_LEADS") or ""
REGISTRO = os.environ.get("TRE_META_STUB_REGISTRO") or ""
FALHAR_PRIMEIRA = os.environ.get("TRE_META_STUB_FALHAR_PRIMEIRA") or ""
FALHAR_SEMPRE = os.environ.get("TRE_META_STUB_FALHAR_SEMPRE") or ""
CONTADOR = {}
TRAVA = threading.Lock()


def leads() -> dict:
    if not LEADS or not os.path.isfile(LEADS):
        return {}
    with open(LEADS, "r", encoding="utf-8") as fh:
        return {str(k): v for k, v in json.load(fh).items()}


def gravar(linha: dict) -> None:
    if not REGISTRO:
        return
    os.makedirs(os.path.dirname(os.path.abspath(REGISTRO)), exist_ok=True)
    with open(REGISTRO, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(linha, ensure_ascii=False, sort_keys=True) + "\n")


class Manipulador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):  # silencio: o registro e' o JSONL
        return

    def _responder(self, http: int, corpo: dict) -> None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(http)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):  # noqa: N802 — nome do contrato HTTP
        partes = urlparse(self.path)
        caminho = [p for p in partes.path.split("/") if p]
        leadgen_id = caminho[-1] if caminho else ""
        autorizacao = self.headers.get("Authorization") or ""
        token_ok = bool(TOKEN) and autorizacao == f"Bearer {TOKEN}"
        with TRAVA:
            CONTADOR[leadgen_id] = CONTADOR.get(leadgen_id, 0) + 1
            chamada = CONTADOR[leadgen_id]
        gravar({"leadgen_id": leadgen_id, "caminho": partes.path, "chamada": chamada,
                "authorization_presente": bool(autorizacao), "token_ok": token_ok,
                "quando": datetime.now(timezone.utc).isoformat()})
        if not token_ok:
            self._responder(401, {"error": {"message": "Invalid OAuth access token.",
                                            "type": "OAuthException", "code": 190}})
            return
        if FALHAR_SEMPRE and leadgen_id == FALHAR_SEMPRE:
            self._responder(500, {"error": {"message": "Internal error", "code": 1}})
            return
        if FALHAR_PRIMEIRA and leadgen_id == FALHAR_PRIMEIRA and chamada == 1:
            self._responder(500, {"error": {"message": "Internal error", "code": 1}})
            return
        payload = leads().get(leadgen_id)
        if payload is None:
            self._responder(404, {"error": {"message": "Unsupported get request.",
                                            "type": "GraphMethodException", "code": 100}})
            return
        self._responder(200, payload)


def main() -> int:
    global FALHAR_PRIMEIRA, FALHAR_SEMPRE
    parser = argparse.ArgumentParser(description="Stub local da Graph API do Meta (sink de dev)")
    parser.add_argument("--porta", type=int, default=8799)
    parser.add_argument("--falhar-primeira", default=FALHAR_PRIMEIRA,
                        help="leadgen_id que devolve 500 na primeira chamada (mede o retry)")
    parser.add_argument("--falhar-sempre", default=FALHAR_SEMPRE,
                        help="leadgen_id que devolve 500 em toda chamada (mede o teto de tentativas)")
    args = parser.parse_args()
    FALHAR_PRIMEIRA = args.falhar_primeira or ""
    FALHAR_SEMPRE = args.falhar_sempre or ""
    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), Manipulador)
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
