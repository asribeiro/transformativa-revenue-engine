#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Muta o workflow da observabilidade de sync — card TRE-W3-E05-T01.

A prova de dente do aceite pergunta o que o usuario cobra sempre: *os itens mordem?*
Um item que nunca reprova e' verde decorativo. Este mutador aplica CADA defeito numa
COPIA do workflow versionado (nunca no arquivo em disco) e o aceite roda a copia: o
item esperado tem de aparecer FALHOU. Sem anchor encontrada o mutador sai != 0
(`MUTACAO_NAO_APLICADA`) — mutacao que nao aplica nao vira dente, vira buraco.

As mutacoes cobrem os DOIS lados do artefato derivado:
  * SQL (metricas e detalhes embutidos no workflow): a medicao deixa de contar o que
    tem de contar, ou o detalhe perde o motivo;
  * contrato embutido: o limiar e' afrouxado (o numero deixa de significar o que o
    contrato diz);
  * nucleo embutido: o fail-closed e a sanitizacao do relatorio sao removidos.

Uso:
    python3 scripts/n8n/mutar_workflow_observabilidade.py \
        --mutacao <nome> --entrada <workflow.json> --saida <mutado.json>
    python3 scripts/n8n/mutar_workflow_observabilidade.py --listar
"""
import argparse
import json
import pathlib
import re
import sys

NUCLEO_ORIGINAL = pathlib.Path(__file__).resolve().parents[2] / "n8n" / "codigo" / "observabilidade-sync.js"


# ------------------------------------------------------------------ utilidades de mutacao

def carregar(caminho):
    return json.loads(pathlib.Path(caminho).read_text(encoding="utf-8"))


def codigo_do_workflow(workflow):
    for no in workflow["nodes"]:
        if no["type"] == "n8n-nodes-base.code":
            return no["parameters"]["jsCode"], no
    raise SystemExit("workflow sem Code node")


def sql_do_no(workflow, nome):
    for no in workflow["nodes"]:
        if no.get("name") == nome:
            return no["parameters"]["query"], no
    raise SystemExit("workflow sem o no %s" % nome)


def sub_bloco_sql(sql, metrica, novo_corpo):
    """Troca o corpo de UMA metrica da consulta de metricas (ancora = o propio nome)."""
    ancora = "'%s'" % metrica
    inicio = sql.index(ancora)
    fim = sql.index("'{}'::jsonb", inicio)
    return sql[:inicio] + "'%s',\n           %s::double precision,\n           " % (metrica, novo_corpo) + sql[fim:]


def sub_texto(texto, antigo, novo, rotulo):
    if antigo not in texto:
        raise SystemExit("MUTACAO_NAO_APLICADA (ancora ausente: %s)" % rotulo)
    return texto.replace(antigo, novo, 1)


def contrato_do_codigo(codigo):
    inicio = codigo.index("const CONTRATO = ") + len("const CONTRATO = ")
    fim = codigo.index("};", inicio) + 1
    return json.loads(codigo[inicio:fim]), inicio, fim


def trocar_contrato(codigo, novo_contrato):
    _, inicio, fim = contrato_do_codigo(codigo)
    texto = json.dumps(novo_contrato, ensure_ascii=False, indent=2)
    return codigo[:inicio] + texto + codigo[fim:]


# ------------------------------------------------------------------ as mutacoes nomeadas

def m_dead_letter_sem_motivo_nao_conta(workflow):
    sql, no = sql_do_no(workflow, "Metricas")
    no["parameters"]["query"] = sub_bloco_sql(sql, "dead_letter_sem_motivo", "0")


def m_processado_sem_trilha_nao_conta(workflow):
    sql, no = sql_do_no(workflow, "Metricas")
    no["parameters"]["query"] = sub_bloco_sql(sql, "outbox_processado_sem_trilha", "0")


def m_direcao_nao_declarada_nao_conta(workflow):
    sql, no = sql_do_no(workflow, "Metricas")
    no["parameters"]["query"] = sub_bloco_sql(sql, "trilha_direcao_nao_declarada", "0")


def m_falha_da_trilha_nao_conta(workflow):
    sql, no = sql_do_no(workflow, "Metricas")
    no["parameters"]["query"] = sub_bloco_sql(sql, "trilha_falhas", "0")


def m_detalhe_perde_o_motivo(workflow):
    sql, no = sql_do_no(workflow, "Detalhes")
    no["parameters"]["query"] = sub_texto(
        sql,
        "NULLIF(btrim(coalesce(f.last_error,",
        "NULLIF(btrim(coalesce(NULL,",
        "motivo do dead-letter na consulta de detalhes")


def m_contrato_afrouxa_o_limiar(workflow):
    codigo, no = codigo_do_workflow(workflow)
    contrato, _, _ = contrato_do_codigo(codigo)
    for metrica in contrato["metricas"]:
        if metrica["id"] == "dead_letter_sem_motivo":
            metrica["limites"] = {"alerta": 99, "critico": 99}
            break
    else:
        raise SystemExit("MUTACAO_NAO_APLICADA (metrica dead_letter_sem_motivo ausente do contrato)")
    no["parameters"]["jsCode"] = trocar_contrato(codigo, contrato)


def _mutar_nucleo(workflow, antigo, novo, rotulo):
    codigo, no = codigo_do_workflow(workflow)
    no["parameters"]["jsCode"] = sub_texto(codigo, antigo, novo, rotulo)


def m_sem_fail_closed_de_metrica_ausente(workflow):
    """A metrica declarada que nao veio passa a valer zero (fail-open classico)."""
    _mutar_nucleo(
        workflow,
        "    if (minhas.length === 0) {\n"
        "        indeterminados.push({ id: metrica.id, motivo: 'metrica_declarada_sem_linha' });\n"
        "        return { avaliacoes: avaliacoes, indeterminados: indeterminados };\n"
        "    }",
        "    if (minhas.length === 0) {\n"
        "        avaliacoes.push(avaliacaoDaMetrica(metrica.id, metrica.titulo, metrica.unidade, 'OK', 0, null, null));\n"
        "        return { avaliacoes: avaliacoes, indeterminados: indeterminados };\n"
        "    }",
        "ramo de metrica declarada sem linha")


def m_sanitizacao_removida(workflow):
    _mutar_nucleo(
        workflow,
        # corpo VIGENTE de `sanitizar` no nucleo versionado (teto declarado + marca do corte)
        "    var limite = numero(teto);\n"
        "    var t = texto(motivo).replace(/[\\r\\n\\t]+/g, ' ').replace(/[\\u0000-\\u001f\\u007f]/g, ' ');\n"
        "    t = t.replace(/\\s{2,}/g, ' ');\n"
        "    if (limite === null || limite <= 0) return t;\n"
        "    if (t.length > limite) return t.slice(0, limite) + '...';\n"
        "    return t;",
        "    return texto(motivo);",
        "sanitizacao do motivo")


def m_sem_conferencia_cruzada(workflow):
    _mutar_nucleo(
        workflow,
        "    var conferencias = (contrato && contrato.conferencias_cruzadas) || [];",
        "    var conferencias = [];",
        "conferencia cruzada metrica x lista")


def m_metrica_nao_declarada_ignorada(workflow):
    _mutar_nucleo(
        workflow,
        "        if (id !== '' && !metricaDeclarada(contrato, id)) {\n"
        "            indeterminados.push({ id: id, motivo: 'metrica_nao_declarada' });\n"
        "        }",
        "        if (false) {\n"
        "            indeterminados.push({ id: id, motivo: 'metrica_nao_declarada' });\n"
        "        }",
        "checagem de metrica nao declarada")


def m_sem_conclusao_nao_conta(workflow):
    """A trilha fechada sem `completed_at` deixa de ser contada."""
    sql, no = sql_do_no(workflow, "Metricas")
    no["parameters"]["query"] = sub_bloco_sql(sql, "trilha_sem_conclusao", "0")


def m_placeholder_vira_indeterminado(workflow):
    """O item vazio do `alwaysOutputData` passa a ser tratado como detalhe (fail-open ao contrario)."""
    _mutar_nucleo(
        workflow,
        "        if (linhaDeDetalheVazia(linha)) { vazias++; continue; }\n",
        "",
        "tratamento da linha vazia da consulta de detalhes")


# nome -> (funcao, fase do aceite, item que TEM de reprovar, por que a mutacao quebra o item)
MUTACOES = {
    "dead_letter_sem_motivo_nao_conta": (
        m_dead_letter_sem_motivo_nao_conta, "medicao",
        "pelo dead-letter SEM motivo",
        "com a contagem zerada o estado C deixa de fechar CRITICO"),
    "processado_sem_trilha_nao_conta": (
        m_processado_sem_trilha_nao_conta, "medicao",
        "pelo PROCESSED sem trilha de sucesso",
        "sem a conferencia de chave o sucesso sem prova nao e medido"),
    "direcao_nao_declarada_nao_conta": (
        m_direcao_nao_declarada_nao_conta, "medicao",
        "pela direcao fora do vocabulario",
        "com a contagem zerada a linha fora das portas declaradas some do relatorio"),
    "sem_conclusao_nao_conta": (
        m_sem_conclusao_nao_conta, "medicao",
        "pela trilha COMPLETED sem conclusao",
        "sem a contagem de conclusao a trilha fechada sem completed_at passa em branco"),
    "falha_da_trilha_nao_conta": (
        m_falha_da_trilha_nao_conta, "medicao",
        "pela falha transitoria da trilha",
        "sem a contagem de FAILED o estado B deixa de fechar ATENCAO"),
    "detalhe_perde_o_motivo": (
        m_detalhe_perde_o_motivo, "medicao",
        "MOTIVO do dead-letter",
        "sem last_error na consulta o dead-letter vira linha sem motivo"),
    "contrato_afrouxa_o_limiar": (
        m_contrato_afrouxa_o_limiar, "medicao",
        "pelo dead-letter SEM motivo",
        "com o limiar afrouxado no contrato embutido o mesmo numero deixa de ser critico"),
    "sem_fail_closed_de_metrica_ausente": (
        m_sem_fail_closed_de_metrica_ausente, "codigo",
        "metrica declarada sem linha fecha INDETERMINADO",
        "metrica ausente virando zero e o fail-open que a observabilidade existe para nao ter"),
    "sanitizacao_removida": (
        m_sanitizacao_removida, "codigo",
        "o texto integral do motivo nao vaza para o relatorio",
        "sem sanitizacao o motivo carrega controle e texto integral para o relatorio"),
    "sem_conferencia_cruzada": (
        m_sem_conferencia_cruzada, "codigo",
        "metrica e lista de detalhes divergentes fecham INDETERMINADO",
        "sem a conferencia, metrica e lista podem contar historias diferentes em silencio"),
    "metrica_nao_declarada_ignorada": (
        m_metrica_nao_declarada_ignorada, "codigo",
        "nao declarada no contrato fecha INDETERMINADO",
        "metrica do SQL fora do contrato passa a ser ignorada em silencio"),
    "placeholder_vira_indeterminado": (
        m_placeholder_vira_indeterminado, "codigo",
        "linha VAZIA do alwaysOutputData nao vira indeterminado",
        "sem distinguir item vazio de detalhe quebrado, toda rodada saudavel grita INDETERMINADO"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutacao")
    parser.add_argument("--entrada")
    parser.add_argument("--saida")
    parser.add_argument("--listar", action="store_true")
    args = parser.parse_args()

    if args.listar:
        # TAB entre os campos: o aceite parseia esta lista (`--prova-de-dente`) e o item
        # esperado tem espacos — separador por espaco quebraria o contrato entre scripts.
        for nome, (_, fase, esperado, porque) in MUTACOES.items():
            print("%s\t%s\t%s\t%s" % (nome, fase, esperado, porque))
        return 0

    if not args.mutacao or not args.entrada or not args.saida:
        print("uso: --mutacao <nome> --entrada <workflow.json> --saida <mutado.json>", file=sys.stderr)
        return 2
    if args.mutacao not in MUTACOES:
        print("mutacao desconhecida: %s" % args.mutacao, file=sys.stderr)
        return 2

    workflow = carregar(args.entrada)
    antes = json.dumps(workflow, ensure_ascii=False, sort_keys=True)
    MUTACOES[args.mutacao][0](workflow)
    depois = json.dumps(workflow, ensure_ascii=False, sort_keys=True)
    if antes == depois:
        print("MUTACAO_NAO_APLICADA (o workflow nao mudou)", file=sys.stderr)
        return 1

    destino = pathlib.Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
