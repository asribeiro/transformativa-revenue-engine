#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutacao NOMEADA da reconciliacao — prova de dente do aceite TRE-W3-E04-T01.

Cada mutacao quebra UMA propriedade declarada e deixa o resto intacto. O aceite roda a
copia mutada e exige que a checagem que sustenta essa propriedade REPROVE:

  * alvo `nucleo` — a suite de comportamento (`testar_nucleo_reconciliacao.js
    --workflow <copia>`) tem que falhar: a mutacao mexeu na DECISAO;
  * alvo `lente:<item>` — a lente estrutural (`conferir_reconciliacao.py
    --workflow <copia>`) tem que reprovar AQUELE item.

Se o item continuar OK, a propriedade nao estava sendo medida por ninguem — e a prova
reprova o aceite em vez de virar "dente cumprido".

Uso: mutar_reconciliacao.py --mutacao <nome> --entrada <workflow.json> --saida <copia.json>
Saida: uma linha `MUTACAO <nome> <trocas> alvo=<alvo>`.
Exit 0 = aplicou; 2 = a ancora mudou (a prova de dente precisa saber disso, nao pode
virar dente cumprido); 1 = uso errado.
"""
import argparse
import json
import sys

MUTACOES = {}


def mutacao(nome, alvo, descricao):
    def registrar(funcao):
        MUTACOES[nome] = {"alvo": alvo, "descricao": descricao, "aplicar": funcao}
        return funcao
    return registrar


def nos(workflow, sufixo):
    return [no for no in workflow["nodes"] if no["type"].endswith(sufixo)]


def trocar_em_nos(workflow, sufixo, alvo, novo, chave="query"):
    trocas = 0
    for no in nos(workflow, sufixo):
        texto = no["parameters"].get(chave) or ""
        if alvo in texto:
            no["parameters"][chave] = texto.replace(alvo, novo)
            trocas += 1
    return trocas


def trocar_no_codigo(workflow, alvo, novo, somente_nucleo=True):
    """Troca dentro do `jsCode` dos Code nodes. `somente_nucleo=True` limita a busca ao que
    esta' ANTES do marcador do adaptador (o nucleo versionado); `False` alcanca tambem o
    contrato embutido, que fica depois do marcador."""
    trocas = 0
    marcador = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"
    for no in nos(workflow, "code"):
        codigo = no["parameters"].get("jsCode") or ""
        corte = codigo.find(marcador)
        regiao = codigo[:corte] if (somente_nucleo and corte > 0) else codigo
        if alvo in regiao:
            no["parameters"]["jsCode"] = codigo.replace(alvo, novo)
            trocas += 1
    return trocas


@mutacao("janela_de_pendencia_desligada", "nucleo",
         "a janela de pendencia deixa de ser aplicada: evento velho na fila passa como saudavel")
def janela_desligada(workflow):
    return trocar_no_codigo(workflow, "} else if (janela !== null && idade >= janela) {",
                            "} else if (false && janela !== null && idade >= janela) {")


@mutacao("comparacoes_de_entidade_desligadas", "nucleo",
         "as comparacoes de entidade deixam de rodar: esperada ausente, arquivada e ID cruzado somem")
def comparacoes_de_entidade_desligadas(workflow):
    return trocar_no_codigo(workflow, "        if (!entidade.esperada) continue;",
                            "        if (!entidade.esperada || true) continue;")


@mutacao("cobertura_da_fila_desligada", "nucleo",
         "fila sem total por status passa a valer como medida: recorte pode ser lido como fila inteira")
def cobertura_da_fila_desligada(workflow):
    return trocar_no_codigo(workflow, "    if (totalDaFila === null || limiteDaFila === null) {",
                            "    if (false && (totalDaFila === null || limiteDaFila === null)) {")


@mutacao("recusa_da_trilha_invisivel", "nucleo",
         "recusa definitiva na trilha deixa de ser divergencia: recusa da fila passa como pendencia normal")
def recusa_desligada(workflow):
    return trocar_no_codigo(workflow, "            } else if (statusTrilha === vocabulario.recusa) {",
                            "            } else if (false && statusTrilha === vocabulario.recusa) {")


@mutacao("cobertura_da_origem_sem_rotulo", "lente:workflow: no `Origem (PG)` embute o SQL byte a byte",
         "a linha de cobertura da origem perde o rotulo: cobertura deixa de ser reconhecida")
def cobertura_sem_rotulo(workflow):
    return trocar_em_nos(workflow, "postgres", "'cobertura'::text", "'resumo_da_base'::text")


@mutacao("escrita_na_porta_controlada", "lente:workflow: a UNICA operacao chamada e' a leitura declarada",
         "o job passa a chamar uma operacao de ESCRITA da porta unica em vez da leitura declarada")
def escrita_na_porta(workflow):
    return trocar_em_nos(workflow, "httpRequest", "/tf/api/v1/crm_registros_ler",
                         "/tf/api/v1/contato_upsert", chave="url")


@mutacao("contrato_mutado_dentro_do_code_node", "lente:Code node `Preparar leitura (nucleo)` embute o contrato do arquivo",
         "o contrato EMBUTIDO no Code node diverge do arquivo versionado (decisao por fora do contrato)")
def contrato_mutado(workflow):
    # O contrato fica DEPOIS do marcador do adaptador: aqui a busca alcanca o codigo inteiro.
    return trocar_no_codigo(workflow, '"janela_de_pendencia_s": 900', '"janela_de_pendencia_s": 86400',
                            somente_nucleo=False)


@mutacao("nucleo_mutado_dentro_do_code_node", "lente:Code node `Preparar leitura (nucleo)` embute o nucleo byte a byte",
         "o nucleo EMBUTIDO no Code node diverge do arquivo versionado (decisao por fora do nucleo)")
def nucleo_mutado(workflow):
    return trocar_no_codigo(workflow, "var veredito = 'OK';", "var veredito = 'OK ';var naoUsado = 1;")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutacao", choices=sorted(MUTACOES))
    parser.add_argument("--entrada")
    parser.add_argument("--saida")
    parser.add_argument("--listar", action="store_true",
                        help="imprime '<nome>\t<alvo>\t<descricao>' por mutacao e sai: o aceite nao "
                             "duplica a lista (mutacao nova entra na prova de dente sem editar o aceite)")
    args = parser.parse_args()

    if args.listar:
        for nome in sorted(MUTACOES):
            print("%s\t%s\t%s" % (nome, MUTACOES[nome]["alvo"], MUTACOES[nome]["descricao"]))
        return 0
    if not (args.mutacao and args.entrada and args.saida):
        print("uso: --mutacao <nome> --entrada <workflow> --saida <copia>  (ou --listar)", file=sys.stderr)
        return 1

    with open(args.entrada, encoding="utf-8") as arquivo:
        workflow = json.load(arquivo)

    escolhida = MUTACOES[args.mutacao]
    trocas = escolhida["aplicar"](workflow)
    if trocas == 0:
        print("MUTACAO %s 0 alvo=%s (ancora nao encontrada)" % (args.mutacao, escolhida["alvo"]))
        return 2

    with open(args.saida, "w", encoding="utf-8") as arquivo:
        json.dump(workflow, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")

    print("MUTACAO %s %d alvo=%s" % (args.mutacao, trocas, escolhida["alvo"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
