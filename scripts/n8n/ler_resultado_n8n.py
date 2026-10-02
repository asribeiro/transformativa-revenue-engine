#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Le a saida de `n8n execute --id=<id> --rawOutput` e extrai o RESULTADO da rodada.

A saida do n8n e' um JSON grande (com log do container antes e depois) e o resultado
da observabilidade fica dentro dele, no item do ultimo no'. Em vez de depender do
caminho exato (`data.resultData.runData.<no>[0].data.main[0][0].json`), que muda
entre versoes do n8n, o leitor procura o objeto que TEM `veredito` + `metricas`:
varre as ocorrencias de `"veredito"`, tenta o objeto que a contem por chaves
balanceadas (respeitando strings) e fica com o primeiro que carrega como JSON e
tem a cara do resultado. Se nao achar, sai != 0 — medicao ausente nao vira medicao
zero.

Uso: ler_resultado_n8n.py <entrada(log/texto)> <prefixo-de-saida>
Escreve: <prefixo>.valores   -> uma linha por metrica: `metrica|observacao|valor`
         <prefixo>.relatorio -> o relatorio em texto (o que o operador le)
Imprime: `veredito=<v>` e `saida=<n>`
"""
import json
import pathlib
import sys


def extrair_resultado(texto):
    """Devolve o primeiro objeto JSON com `veredito` e `metricas` dentro do texto."""
    alvo = '"veredito"'
    pos = texto.find(alvo)
    while pos >= 0:
        for abertura in range(pos, -1, -1):
            if texto[abertura] != "{":
                continue
            profundidade = 0
            em_string = False
            escape = False
            for i in range(abertura, len(texto)):
                c = texto[i]
                if em_string:
                    if escape:
                        escape = False
                    elif c == "\\":
                        escape = True
                    elif c == '"':
                        em_string = False
                    continue
                if c == '"':
                    em_string = True
                elif c == "{":
                    profundidade += 1
                elif c == "}":
                    profundidade -= 1
                    if profundidade == 0:
                        try:
                            objeto = json.loads(texto[abertura:i + 1])
                        except json.JSONDecodeError:
                            objeto = None
                        if isinstance(objeto, dict) and objeto.get("veredito") and "metricas" in objeto:
                            return objeto
                        break
            if abertura == 0:
                break
        pos = texto.find(alvo, pos + 1)
    return None


def formatar(valor):
    try:
        return "%.3f" % float(valor)
    except (TypeError, ValueError):
        return ""


def main():
    if len(sys.argv) != 3:
        print("uso: ler_resultado_n8n.py <entrada> <prefixo-de-saida>", file=sys.stderr)
        return 2
    caminho, prefixo = sys.argv[1], sys.argv[2]
    texto = pathlib.Path(caminho).read_text(encoding="utf-8", errors="replace")

    resultado = extrair_resultado(texto)
    if resultado is None:
        print("FALHOU o resultado da rodada nao esta na saida do n8n (sem `veredito`+`metricas`)", file=sys.stderr)
        return 1

    linhas = []
    for metrica in resultado.get("metricas", []):
        linhas.append("%s|%s|%s" % (metrica.get("id", ""), metrica.get("observacao") or "",
                                    formatar(metrica.get("valor"))))
    linhas.sort()
    pathlib.Path(prefixo + ".valores").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    pathlib.Path(prefixo + ".relatorio").write_text((resultado.get("relatorio") or "") + "\n", encoding="utf-8")
    print("veredito=%s" % resultado.get("veredito"))
    print("saida=%s" % resultado.get("saida"))
    print("metricas=%d" % len(linhas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
