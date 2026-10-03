#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Le o resultado de UMA rodada do job de RECONCILIACAO a partir da saida do n8n.

O workflow termina no no' `Relatorio` (no-op): o item que chega nele E' o resultado. Este
leitor tira da saida (`n8n execute --rawOutput`) o veredito, as divergencias nomeadas e a
linha do relatorio, no formato `chave=valor` que o aceite compara item a item:

    veredito=DIVERGENTE
    divergencias=E1,I1
    linha=VEREDITO: DIVERGENTE — 1 entidades medidas, ...
    leitura=ok | nao_medido | ausente

Nao ha' leitura otimista: saida que nao da' para entender sai como `leitura=ilegivel` e o
aceite reprova (uma rodada cujo resultado nao se consegue ler NAO e' uma rodada OK).

Uso: ler_resultado_reconciliacao.py <saida-do-n8n>
"""
import json
import re
import sys


def objeto_do_resultado(texto):
    """Devolve o primeiro objeto JSON de resultado presente na saida, se houver."""
    try:
        carga = json.loads(texto)
    except json.JSONDecodeError:
        carga = None
    if isinstance(carga, dict) and "veredito" in carga:
        return carga
    if isinstance(carga, list):
        for item in carga:
            if isinstance(item, dict) and "veredito" in item:
                return item
            if isinstance(item, dict) and isinstance(item.get("json"), dict) and "veredito" in item["json"]:
                return item["json"]
    # Saida com varias linhas: procura cada objeto balanceado e fica com o ULTIMO que tem
    # `veredito` (o n8n imprime o resultado depois dos logs, e logs podem citar JSON).
    candidatos = []
    for inicio in [m.start() for m in re.finditer(r"\{", texto)]:
        profundidade = 0
        dentro_de_texto = False
        escapado = False
        for i in range(inicio, len(texto)):
            caractere = texto[i]
            if dentro_de_texto:
                if escapado:
                    escapado = False
                elif caractere == "\\":
                    escapado = True
                elif caractere == '"':
                    dentro_de_texto = False
                continue
            if caractere == '"':
                dentro_de_texto = True
            elif caractere == "{":
                profundidade += 1
            elif caractere == "}":
                profundidade -= 1
                if profundidade == 0:
                    trecho = texto[inicio:i + 1]
                    if '"veredito"' in trecho:
                        try:
                            candidatos.append(json.loads(trecho))
                        except json.JSONDecodeError:
                            pass
                    break
    if candidatos:
        return candidatos[-1]
    return None


def do_texto(texto):
    """Ultimo recurso: tira os campos por expressao regular (saida truncada/embrulhada)."""
    resultado = {}
    vereditos = re.findall(r'"veredito"\s*:\s*"([A-Z_]+)"', texto)
    if vereditos:
        resultado["veredito"] = vereditos[-1]
    tipos = re.findall(r'"id"\s*:\s*"([EIP]\d)"\s*,\s*"tipo"\s*:\s*"([a-z_]+)"', texto)
    if tipos:
        resultado["divergencias"] = [identificador for identificador, _ in tipos]
    relatorios = re.findall(r'"relatorio"\s*:\s*"((?:[^"\\]|\\.)*)"', texto)
    if relatorios:
        try:
            resultado["relatorio"] = json.loads('"%s"' % relatorios[-1])
        except json.JSONDecodeError:
            resultado["relatorio"] = relatorios[-1]
    return resultado


def main():
    if len(sys.argv) != 2:
        print("uso: ler_resultado_reconciliacao.py <saida-do-n8n>", file=sys.stderr)
        return 2
    texto = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    objeto = objeto_do_resultado(texto)
    if objeto is None:
        objeto = do_texto(texto)
    if not objeto.get("veredito"):
        print("leitura=ilegivel")
        print("divergencias=")
        print("linha=")
        return 1

    divergencias = objeto.get("divergencias")
    if isinstance(divergencias, list):
        identificadores = [d.get("id", "?") if isinstance(d, dict) else str(d) for d in divergencias]
    else:
        identificadores = []
    relatorio = objeto.get("relatorio") or ""
    linhas = [linha for linha in relatorio.splitlines() if linha.strip()]
    linha_resumo = linhas[1] if len(linhas) > 1 else (linhas[0] if linhas else "")

    print("leitura=ok")
    print("veredito=%s" % objeto.get("veredito"))
    print("divergencias=%s" % ",".join(sorted(identificadores)))
    print("contagens=%s/%s/%s" % (objeto.get("contagens", {}).get("divergencias", "?"),
                                  objeto.get("contagens", {}).get("observacoes", "?"),
                                  objeto.get("contagens", {}).get("indeterminacoes", "?")))
    print("linha=%s" % linha_resumo)
    print("observacoes=%s" % ",".join(o.get("id", "?") for o in (objeto.get("observacoes") or [])))
    print("indeterminacoes=%s" % ",".join((o.get("regra", "?") if isinstance(o, dict) else str(o))
                                          for o in (objeto.get("indeterminacoes") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
