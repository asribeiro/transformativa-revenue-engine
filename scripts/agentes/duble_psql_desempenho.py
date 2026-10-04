#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Duble de porta de banco (psql) para a medicao OFFLINE do desempenho de mensagens
(card TRE-W8-E04-T01, `desempenho-mensagens-v1`).

NAO e banco: e um duble de teste que responde a UNICA consulta de leitura do componente, aplicando o
mesmo WHERE do SQL (recorte + direcao/referencia). Existe para medir a ANALISE sem PostgreSQL — o que
se mede aqui e atribuicao, classes, taxas, normalizacao de canal e guardrails. O que NAO se mede aqui e
o PostgreSQL de verdade: isso e o aceite E2E (`scripts/agentes/teste_desempenho_mensagens_aceite.sh`).

Guarda de leitura: qualquer statement que nao comece por SELECT/WITH ou que traga verbo de escrita
RECUSA com exit 42 e registra a tentativa. O estado fica num JSON (`{"interacoes": [...]}`) e cada
chamada e anotada em `<estado>.chamadas.jsonl` (para provar que so houve leitura).

Uso: python3 scripts/agentes/duble_psql_desempenho.py <estado.json> [-c "<sql>"]   (ou SQL por stdin)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

VERBOS_DE_ESCRITA = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE",
                     "GRANT", "REVOKE", "COPY", "CALL", "DO", "MERGE", "VACUUM", "REFRESH")


def recusar(estado: Path, motivo: str) -> int:
    try:
        with estado.with_suffix(estado.suffix + ".chamadas.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"recusado": motivo}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(f"DUBLE_RECUSA_ESCRITA {motivo}", file=sys.stderr)
    return 42


def ler_sql(argv: list[str]) -> str:
    if "-c" in argv:
        return argv[argv.index("-c") + 1]
    return sys.stdin.read()


def carregar(estado: Path) -> list[dict]:
    if not estado.exists():
        return []
    dados = json.loads(estado.read_text(encoding="utf-8"))
    return dados.get("interacoes", [])


def instantes(sql: str) -> list[str]:
    return re.findall(r"TIMESTAMPTZ\s*'([^']+)'", sql)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("uso: duble_psql_desempenho.py <estado.json> [-c <sql>]", file=sys.stderr)
        return 2
    estado = Path(argv[1])
    sql = ler_sql(argv[2:])
    primeiro = (re.split(r"\s+", sql.strip(), maxsplit=1)[0] if sql.strip() else "").upper()
    if primeiro not in ("SELECT", "WITH"):
        return recusar(estado, f"statement nao e leitura: {primeiro or '<vazio>'}")
    for verbo in VERBOS_DE_ESCRITA:
        if re.search(r"\b%s\b" % verbo, sql.upper()):
            return recusar(estado, f"verbo de escrita: {verbo}")

    limites = instantes(sql)
    desde, fim = (limites[0], limites[1]) if len(limites) >= 2 else ("", "9999-12-31T23:59:59Z")
    linhas = []
    for i in carregar(estado):
        ocorrido = i["occurred_at"]
        if not (desde <= ocorrido <= fim):
            continue
        direcao = (i.get("direction") or "").strip().upper()
        referencia = i.get("content_reference") or ""
        if direcao == "OUTBOUND":
            if not referencia.startswith("envio:"):
                continue
        elif direcao != "INBOUND":
            continue
        campos = [i.get("id", ""), i.get("organization_id", ""), i.get("contact_id") or "",
                  i.get("channel", ""), direcao, i.get("interaction_type") or "",
                  ocorrido, referencia, i.get("response_category") or ""]
        linhas.append((ocorrido, i.get("id", ""), "|".join(str(c) for c in campos)))
    linhas.sort(key=lambda t: (t[0], t[1]))
    for _, _, linha in linhas:
        print(linha)
    with estado.with_suffix(estado.suffix + ".chamadas.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"statement": sql.strip().split("\n")[0][:80], "linhas": len(linhas)},
                            ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
