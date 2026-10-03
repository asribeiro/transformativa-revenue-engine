#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normaliza a MEDICAO DIRETA (psql) para o mesmo formato da medicao pelo workflow.

Entrada (stdin): linhas do psql (`-tA -F'|'`) da consulta de metricas, no formato
    metrica|valor|dimensao(jsonb)
com a dimensao vazia (`{}`) nas metricas escalares. Saida (stdout): as MESMAS
linhas no formato comparavel com o que o nucleo mediu:

    metrica|observacao|valor

com `observacao` = `<source_system>-><target_system>/<status>` na metrica
dimensional (o mesmo rotulo que o nucleo usa) e valor com 3 casas. Ordenado.

Serve para o aceite medir a MESMA consulta por DOIS caminhos independentes (psql e
workflow) e comparar: e' assim que "o numero do relatorio" deixa de ser palavra do
proximo no' e vira medida replicavel.
"""
import json
import sys


def formatar(valor):
    try:
        return "%.3f" % float(valor)
    except (TypeError, ValueError):
        return ""


def main():
    linhas = []
    for bruta in sys.stdin:
        bruta = bruta.rstrip("\n")
        if not bruta.strip():
            continue
        partes = bruta.split("|")
        if len(partes) < 3:
            print("linha inesperada na medicao direta: %r" % bruta, file=sys.stderr)
            return 1
        metrica, valor, dimensao = partes[0], partes[1], "|".join(partes[2:])
        observacao = ""
        if dimensao.strip() not in ("", "{}"):
            dim = json.loads(dimensao)
            if dim.get("status"):
                observacao = "%s->%s/%s" % (dim.get("source_system", ""), dim.get("target_system", ""),
                                            dim.get("status", ""))
        linhas.append("%s|%s|%s" % (metrica, observacao, formatar(valor)))
    linhas.sort()
    sys.stdout.write("\n".join(linhas) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
