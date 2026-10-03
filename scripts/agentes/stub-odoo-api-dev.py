#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stub LOCAL da API controlada do Odoo (dev/aceite) — card TRE-W6-E06-T01.

Nao e' o Odoo: e' o SINK do aceite, com o MESMO envelope e as MESMAS declaracoes da politica v1.3.0
(`POST /tf/api/v1/<operacao>?tf.api.ambiente=<amb>`, bearer, `idempotency_key` na escrita,
`correlation_id` ecoado, `dry_run`). Existe porque o dev nao tem chave de API do usuario de integracao
do Odoo: a prova em dev e' contra este sink em LOOPBACK, e a prova contra `odoo-dev` e' do card E2E
(TRE-W6-E07-T01).

Registra TODA requisicao em JSONL (sem a chave) para o aceite medir o que saiu do componente:
`{"operacao","corpo","authorization_presente","quando"}`.

Uso:
  TRE_ODOO_STUB_CHAVE=... TRE_ODOO_STUB_REGISTRO=/tmp/stub.jsonl [TRE_ODOO_STUB_LEADS=leads.json] \
      python3 scripts/agentes/stub-odoo-api-dev.py --porta 8799
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

OPERACOES = {
    "sistema_capacidades": {"tipo": "leitura", "requer_idempotency_key": False},
    "crm_registros_ler": {"tipo": "leitura", "requer_idempotency_key": False},
    "contato_upsert": {"tipo": "escrita", "requer_idempotency_key": True},
    "oportunidade_upsert": {"tipo": "escrita", "requer_idempotency_key": True},
    "atividade_criar": {"tipo": "escrita", "requer_idempotency_key": True},
}
CAMPOS = {
    "oportunidade_upsert": ["name", "type", "partner_id", "tf_opportunity_id", "tf_priority_score",
                            "tf_icp_score", "tf_automation_fit_score", "tf_buying_signal_score",
                            "tf_data_quality_score", "tf_score_version", "tf_next_best_action",
                            "tf_correlation_id", "tf_idempotency_key", "tf_last_sync_at",
                            "tf_last_event_type"],
    "contato_upsert": ["name", "email", "is_company", "function", "phone"],
    "atividade_criar": ["res_model", "res_id", "activity_type_id", "summary", "date_deadline",
                        "user_id", "tf_idempotency_key", "tf_correlation_id"],
    "crm_registros_ler": ["id", "name", "tf_opportunity_id", "tf_next_best_action"],
}
CHAVE = os.environ.get("TRE_ODOO_STUB_CHAVE") or ""
REGISTRO = os.environ.get("TRE_ODOO_STUB_REGISTRO") or ""
LEADS = os.environ.get("TRE_ODOO_STUB_LEADS") or ""
CONTADOR = {"atividade": 5000, "lead": 4000}


def leads() -> dict:
    if not LEADS or not os.path.isfile(LEADS):
        return {}
    with open(LEADS, "r", encoding="utf-8") as fh:
        return {str(k): v for k, v in json.load(fh).items()}


def gravar(linha: dict) -> None:
    if not REGISTRO:
        return
    linha["quando"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(REGISTRO, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(linha, ensure_ascii=False, sort_keys=True) + "\n")


class Sink(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):  # silencio: o registro e' o JSONL
        return

    def _responder(self, http: int, corpo: dict) -> None:
        texto = json.dumps(corpo, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(http)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(texto)))
        self.end_headers()
        self.wfile.write(texto)

    def do_POST(self):  # noqa: N802 — nome do contrato HTTP
        partes = urlparse(self.path)
        if not partes.path.startswith("/tf/api/v1/"):
            self._responder(404, {"ok": False, "codigo": "rota_nao_declarada"})
            return
        operacao = partes.path.rsplit("/", 1)[-1]
        ambiente = (parse_qs(partes.query).get("tf.api.ambiente") or [""])[0]
        autorizacao = self.headers.get("Authorization") or ""
        bruto = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        try:
            corpo = json.loads(bruto) if bruto else {}
        except ValueError:
            self._responder(400, {"ok": False, "codigo": "payload_invalido"})
            return
        gravar({"operacao": operacao, "ambiente": ambiente, "corpo": corpo,
                "authorization_presente": bool(autorizacao),
                "bearer_confere": autorizacao == ("Bearer " + CHAVE)})
        declaracao = OPERACOES.get(operacao)
        def recusar(http, codigo, mensagem):
            self._responder(http, {"ok": False, "codigo": codigo, "mensagem": mensagem,
                                   "operacao": operacao, "ambiente": ambiente,
                                   "correlation_id": corpo.get("correlation_id")})
        if declaracao is None:
            recusar(404, "operacao_nao_declarada", f"operacao {operacao} nao declarada na politica")
            return
        if ambiente != "dev":
            recusar(503, "ambiente_nao_declarado", f"a politica desta versao atende dev; pedido: {ambiente!r}")
            return
        if CHAVE and autorizacao != ("Bearer " + CHAVE):
            recusar(401, "credencial_invalida", "bearer ausente ou invalido")
            return
        if declaracao["requer_idempotency_key"] and not corpo.get("idempotency_key"):
            recusar(422, "idempotency_key_ausente", "escrita exige idempotency_key")
            return
        parametros = corpo.get("parametros") or {}
        modelo = parametros.get("modelo")
        if operacao == "crm_registros_ler":
            registros = []
            dom = parametros.get("filtro") or []
            for condicao in dom:
                if isinstance(condicao, list) and len(condicao) == 3 and condicao[0] == "id":
                    lead = leads().get(str(condicao[2]))
                    if lead:
                        registros.append(lead)
            dados = {"registros": registros, "total": len(registros)}
        else:
            valores = parametros.get("valores") or {}
            fora = [campo for campo in valores if campo not in (CAMPOS.get(operacao) or [])]
            if fora:
                recusar(422, "campo_nao_declarado", f"campos fora da politica: {sorted(fora)}")
                return
            if corpo.get("dry_run"):
                dados = {"dry_run": True, "acao_efetiva": "atualizar", "id": CONTADOR["lead"],
                         "atualizaria": sorted(valores)}
            else:
                if operacao == "atividade_criar":
                    CONTADOR["atividade"] += 1
                    dados = {"acao_efetiva": "criar", "ids": [CONTADOR["atividade"]]}
                else:
                    CONTADOR["lead"] += 1
                    dados = {"acao_efetiva": "atualizar", "ids": [CONTADOR["lead"]]}
        self._responder(200, {
            "ok": True, "operacao": operacao, "acao": "ler" if declaracao["tipo"] == "leitura" else modelo,
            "ambiente": ambiente, "politica_versao": "1.3.0",
            "correlation_id": corpo.get("correlation_id"), "idempotency_key": corpo.get("idempotency_key"),
            "dry_run": bool(corpo.get("dry_run")), "dados": dados,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="Sink local da API controlada do Odoo (stub de dev)")
    parser.add_argument("--porta", type=int, default=8799)
    args = parser.parse_args()
    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), Sink)
    print(json.dumps({"stub": "odoo-api-dev", "endereco": f"127.0.0.1:{args.porta}",
                      "registro": REGISTRO or "(sem registro)", "politica_versao": "1.3.0"}))
    servidor.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
