#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutacao NOMEADA do workflow do consumidor — prova de dente do aceite TRE-W3-E02-T01.

Cada mutacao quebra UMA propriedade declarada do consumidor. O aceite inteiro roda numa COPIA
mutada (`verificar-outbox-consumer.sh --apenas-consumo --workflow <copia>`); se o item que
aquela propriedade sustenta continuar OK, o item nao tem dente (nao mede nada) e a prova
reprova o aceite.

Uso: mutar_workflow.py --mutacao <nome> --entrada <workflow.json> --saida <copia.json>
Sai com 0 quando aplicou (imprime quantas trocas) e 2 quando a mutacao nao achou o alvo
(ancora mudou — a prova de dente precisa saber disso, nao pode virar "dente cumprido").
"""
import argparse
import json
import re
import sys


def carregar(caminho):
    with open(caminho, encoding="utf-8") as arquivo:
        return json.load(arquivo)


def salvar(caminho, workflow):
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump(workflow, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")


def nos_do_tipo(workflow, sufixo):
    return [no for no in workflow["nodes"] if no["type"].endswith(sufixo)]


def sem_validacao_de_envelope(workflow):
    """Tira a exigencia de `event_version`: o evento sem versao passaria a ser entregue."""
    trocas = 0
    alvo = "if (ehVazio(evento[campo])) motivos.push('envelope_sem_' + campo);"
    novo = "if (false && ehVazio(evento[campo])) motivos.push('envelope_sem_' + campo);"
    for no in nos_do_tipo(workflow, "code"):
        js = no["parameters"]["jsCode"]
        if alvo in js:
            no["parameters"]["jsCode"] = js.replace(alvo, novo)
            trocas += 1
    return trocas


def sem_incremento_de_tentativas(workflow):
    """Tira o incremento de `attempts`: a falha transitoria deixa de ser visivel na fila."""
    trocas = 0
    alvo = "attempts = attempts + $2::int"
    novo = "attempts = attempts + 0::int"
    for no in nos_do_tipo(workflow, "postgres"):
        query = no["parameters"]["query"]
        if alvo in query:
            no["parameters"]["query"] = query.replace(alvo, novo)
            trocas += 1
    return trocas


def sem_teto_de_tentativas(workflow):
    """Tira o teto: evento esgotado volta para a fila para sempre."""
    trocas = 0
    alvo = "if (tentativas >= teto) {"
    novo = "if (false && tentativas >= teto) {"
    for no in nos_do_tipo(workflow, "code"):
        js = no["parameters"]["jsCode"]
        if alvo in js:
            no["parameters"]["jsCode"] = js.replace(alvo, novo)
            trocas += 1
    return trocas


def mapeamento_trocado(workflow):
    """Troca a origem do `tf_domain`: o parceiro nasce com outro valor no CRM."""
    trocas = 0
    padrao = re.compile(r'("destino":\s*"tf_domain",\s*"origem":\s*")payload\.domain(")')
    for no in nos_do_tipo(workflow, "code"):
        js = no["parameters"]["jsCode"]
        novo_js, quantas = padrao.subn(r"\1payload.name\2", js)
        if quantas:
            no["parameters"]["jsCode"] = novo_js
            trocas += quantas
    return trocas


def sem_consulta_de_trilha(workflow):
    """Tira as chaves do lote da consulta: o dedup deixa de enxergar a trilha (T02)."""
    trocas = 0
    alvo = "={{ [$json.chaves] }}"
    novo = "={{ [[]] }}"
    for no in nos_do_tipo(workflow, "postgres"):
        opcoes = no["parameters"].get("options", {})
        if opcoes.get("queryReplacement") == alvo:
            opcoes["queryReplacement"] = novo
            trocas += 1
    return trocas


def guarda_de_sucesso_afrouxada(workflow):
    """Aceita QUALQUER status de trilha como replay: uma trilha REFUSED autorizaria replay."""
    trocas = 0
    alvo = "if (texto(registro.status) !== texto(criterio.status_trilha)) return null;"
    novo = "if (false) return null;"
    for no in nos_do_tipo(workflow, "code"):
        js = no["parameters"]["jsCode"]
        if alvo in js:
            no["parameters"]["jsCode"] = js.replace(alvo, novo)
            trocas += 1
    return trocas


MUTACOES = {
    "sem_validacao_de_envelope": sem_validacao_de_envelope,
    "sem_incremento_de_tentativas": sem_incremento_de_tentativas,
    "sem_teto_de_tentativas": sem_teto_de_tentativas,
    "mapeamento_trocado": mapeamento_trocado,
    "sem_consulta_de_trilha": sem_consulta_de_trilha,
    "guarda_de_sucesso_afrouxada": guarda_de_sucesso_afrouxada,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mutacao", required=True, choices=sorted(MUTACOES))
    ap.add_argument("--entrada", required=True)
    ap.add_argument("--saida", required=True)
    args = ap.parse_args()

    workflow = carregar(args.entrada)
    trocas = MUTACOES[args.mutacao](workflow)
    if not trocas:
        print("MUTACAO_NAO_APLICADA: a ancora de '%s' nao existe mais no workflow" % args.mutacao)
        return 2
    salvar(args.saida, workflow)
    print("MUTACAO_APLICADA %s (%d troca(s) em %s)" % (args.mutacao, trocas, args.saida))
    return 0


if __name__ == "__main__":
    sys.exit(main())
