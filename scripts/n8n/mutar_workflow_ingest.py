#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutacoes nomeadas do workflow do ingestor — prova de dente do card TRE-W3-E03-T01.

A prova de dente do aceite roda o aceite INTEIRO sobre uma COPIA MUTADA do workflow e exige que o
item que aquela mutacao quebra REPROVE. Cada mutacao e' um texto declarado aqui que tem de ser
encontrado no artefato: se a ancora sumiu (o codigo mudou de forma), a resposta e'
`MUTACAO_NAO_APLICADA` e o dente NAO CONTA — mutacao que nao se aplica e' buraco do teste, nunca
verde.

Uso:
    python3 scripts/n8n/mutar_workflow_ingest.py --mutacao <nome> --saida <arquivo>
    python3 scripts/n8n/mutar_workflow_ingest.py --listar
Saida: 0 = mutacao aplicada · 3 = ancora ausente (nao aplicada) · 2 = uso errado.
"""
import argparse
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = RAIZ / "n8n" / "workflows" / "TRE-odoo-events-ingest.json"

NO_NUCLEO = "Nucleo: decidir ingestao"
NO_INGERIR = "Postgres: ingerir evento (trilha)"

# nome -> (no alvo, ancora, substituto, item que a mutacao deve reprovar)
MUTACOES = {
    "sem_versao": {
        "no": NO_NUCLEO,
        "ancora": ("if (ehVazio(envelope.event_version)) return { ok: false, "
                   "motivo: 'envelope_sem_versao' };"),
        "troca": ("if (false) return { ok: false, "
                  "motivo: 'envelope_sem_versao' };"),
        "item": "envelope sem event_version e recusado (422 + REFUSED na trilha)",
        "porque": "sem a checagem de versao a porta aceitaria envelope sem `event_version`",
    },
    "sem_formato_da_chave": {
        "no": NO_NUCLEO,
        "ancora": "if (!formatoChave.test(texto(envelope.idempotency_key))) {",
        "troca": "if (false) {",
        "item": "chave de idempotencia fora do formato e recusada (422 + REFUSED)",
        "porque": "sem a checagem de formato a porta aceitaria chave torta",
    },
    "sem_campos_exigidos": {
        "no": NO_NUCLEO,
        "ancora": "if (ehVazio(envelope.payload[campo])) {",
        "troca": "if (false) {",
        "item": "campo exigido ausente e recusado (422 + REFUSED)",
        "porque": "sem a checagem de campo exigido a porta aceitaria fato incompleto",
    },
    "sem_on_conflict": {
        "no": NO_INGERIR,
        "ancora": "    ON CONFLICT (idempotency_key) DO NOTHING\n",
        "troca": "",
        "item": "reenvio do mesmo fato nao cria segunda linha na trilha (duplicado: true)",
        "porque": "sem ON CONFLICT o reenvio estoura a UNIQUE em vez de responder duplicado",
    },
}


def main():
    ap = argparse.ArgumentParser(description="Aplica uma mutacao nomeada no workflow do ingestor.")
    ap.add_argument("--mutacao")
    ap.add_argument("--saida")
    ap.add_argument("--listar", action="store_true")
    args = ap.parse_args()

    if args.listar:
        for nome, dados in sorted(MUTACOES.items()):
            print("%s -> reprova: %s (%s)" % (nome, dados["item"], dados["porque"]))
        return 0
    if not args.mutacao or not args.saida:
        print("uso: --mutacao <nome> --saida <arquivo> | --listar")
        return 2
    dados = MUTACOES.get(args.mutacao)
    if dados is None:
        print("mutacao desconhecida: %s" % args.mutacao)
        return 2

    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    alvo = None
    for no in workflow["nodes"]:
        if no["name"] == dados["no"]:
            alvo = no
            break
    if alvo is None:
        print("MUTACAO_NAO_APLICADA %s (no %s ausente)" % (args.mutacao, dados["no"]))
        return 3
    chave = "jsCode" if "jsCode" in alvo["parameters"] else "query"
    texto = alvo["parameters"][chave]
    if texto.count(dados["ancora"]) != 1:
        print("MUTACAO_NAO_APLICADA %s (ancora aparece %sx)" % (args.mutacao, texto.count(dados["ancora"])))
        return 3
    alvo["parameters"][chave] = texto.replace(dados["ancora"], dados["troca"])

    destino = pathlib.Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    print("MUTACAO_APLICADA %s em %s -> %s" % (args.mutacao, dados["no"], destino))
    return 0


if __name__ == "__main__":
    sys.exit(main())
