#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monta o workflow da observabilidade de sync a partir dos artefatos versionados.

Card TRE-W3-E05-T01; board transformativa-revenue-engine. O workflow versionado
(`n8n/workflows/TRE-observabilidade-sync.json`) NAO e' escrito a mao: ele e' o
resultado deste montador sobre artefatos que tem um dono cada um:

  * `n8n/contracts/observabilidade-sync.v1.json` — a declaracao (metricas,
    limiares, vocabularios, regras de fail-closed, detalhes, credenciais por
    id/nome, grafo e saida);
  * `n8n/codigo/observabilidade-sync.js` — a decisao pura (roda no n8n e no node);
  * `n8n/sql/observabilidade-sync.sql` — a medicao (uma linha por metrica);
  * `n8n/sql/observabilidade-sync-dead-letters.sql` — os detalhes com motivo.

Assim "o workflow embute o nucleo/o SQL/o contrato" deixa de ser afirmacao de
leitura: o texto do Code node e' o arquivo + um adaptador marcado, e a lente
estrutural (`scripts/n8n/conferir_observabilidade.py`) reprova quando qualquer um
dos dois lados anda sozinho. `--conferir` faz essa comparacao sem escrever nada.

Uso:
    python3 scripts/n8n/montar_workflow_observabilidade.py            # escreve o workflow
    python3 scripts/n8n/montar_workflow_observabilidade.py --saida /tmp/x.json
    python3 scripts/n8n/montar_workflow_observabilidade.py --conferir  # compara com o que esta em disco
"""
import argparse
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ARQ_CONTRATO = RAIZ / "n8n" / "contracts" / "observabilidade-sync.v1.json"
ARQ_NUCLEO = RAIZ / "n8n" / "codigo" / "observabilidade-sync.js"
ARQ_SQL_METRICAS = RAIZ / "n8n" / "sql" / "observabilidade-sync.sql"
ARQ_SQL_DETALHES = RAIZ / "n8n" / "sql" / "observabilidade-sync-dead-letters.sql"
ARQ_WORKFLOW = RAIZ / "n8n" / "workflows" / "TRE-observabilidade-sync.json"

# O adaptador e' a FRONTEIRA declarada: o que esta antes e' o arquivo versionado, o que
# esta depois e' a cola do n8n. A lente corta exatamente neste marcador.
MARCADOR = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"

NOTAS = {
    "metricas": ("Primeira consulta da observabilidade (n8n/sql/observabilidade-sync.sql): a MEDICAO, "
                 "uma linha por metrica declarada. Somente leitura — a observabilidade que escreve no "
                 "que observa nao serve para julgar o que ela propria mexeu."),
    "detalhes": ("Segunda consulta (n8n/sql/observabilidade-sync-dead-letters.sql): os detalhes COM "
                 "MOTIVO (dead-letter, falha e recusa da trilha). Sem payload no resultado. "
                 "`alwaysOutputData`: sem isso, uma rodada SAUDAVEL (zero detalhe) deixaria o no sem "
                 "itens, a cadeia pararia e o relatorio — que existe justamente para dizer que esta' "
                 "tudo bem — nunca seria produzido."),
    "avaliar": ("Code node: roda o NUCLEO VERSIONADO (n8n/codigo/observabilidade-sync.js) sobre as duas "
                "consultas e devolve o veredito, as metricas medidas, os detalhes sanitizados e o "
                "RELATORIO em texto. Limiar, metrica e regra de fail-closed vem do contrato embutido: "
                "sem contrato o nucleo nao decide nada (INDETERMINADO)."),
    "relatorio": ("Saida do workflow: o resultado desta execucao E o relatorio (o que o operador le). "
                  "O destino do ALERTA (canal de plantao) e' decisao do dono — declarado em "
                  "`autoridade.fora_do_escopo` do contrato."),
    "agenda": ("Gatilho declarado (cadencia padrao do contrato). Quem ATIVA ajusta o intervalo ao "
               "plantao real: o card nao escolhe a janela de observacao."),
    "sob_demanda": ("Mesma cadeia, disparada a mao: e' o caminho que o aceite e o operador usam "
                    "(`n8n execute --id=<id>` executa o workflow a partir deste gatilho)."),
}


def ler(caminho):
    return caminho.read_text(encoding="utf-8")


def carregar_contrato_texto():
    """Devolve o contrato como JSON normalizado (o mesmo texto vai para o Code node)."""
    contrato = json.loads(ler(ARQ_CONTRATO))
    return contrato, json.dumps(contrato, ensure_ascii=False, indent=2)


def js_code(adaptador, contrato_texto):
    return "\n".join([
        ler(ARQ_NUCLEO).rstrip(),
        "",
        MARCADOR,
        "const CONTRATO = %s;" % contrato_texto,
        "",
        adaptador,
        "",
    ])


def no_de_pg(ident, nome, sql, contrato, posicao, nota, extra=None):
    no = {
        "parameters": {"operation": "executeQuery", "query": sql.rstrip() + "\n", "options": {}},
        "id": ident,
        "name": nome,
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.7,
        "position": posicao,
        "credentials": {"postgres": {"id": contrato["credenciais"]["postgres"]["id"],
                                     "name": contrato["credenciais"]["postgres"]["nome"]}},
        "notes": nota,
        "notesInFlow": False,
    }
    if extra:
        no.update(extra)
    return no


def montar():
    contrato, contrato_texto = carregar_contrato_texto()
    sql_metricas = ler(ARQ_SQL_METRICAS)
    sql_detalhes = ler(ARQ_SQL_DETALHES)
    gatilho = contrato["workflow"]["gatilho"]
    if gatilho["unidade"] != "hours":
        raise SystemExit("gatilho declarado com unidade nao montada: %s" % gatilho["unidade"])

    avaliar = js_code(
        "const metricas = $('Metricas').all().map(function (item) { return item.json; });\n"
        "const detalhes = $input.all().map(function (item) { return item.json; });\n"
        "return [{ json: avaliar(CONTRATO, metricas, detalhes, new Date().toISOString()) }];",
        contrato_texto)

    nos = [
        {
            "parameters": {"rule": {"interval": [{"field": "hours",
                                                  "hoursInterval": gatilho["intervalo"]}]}},
            "id": "tre-observ-agenda",
            "name": "Agenda",
            "type": gatilho["tipo"],
            "typeVersion": 1.2,
            "position": [-520, -80],
            "notes": NOTAS["agenda"],
        },
        {
            "parameters": {},
            "id": "tre-observ-sob-demanda",
            "name": "Executar agora",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [-520, 120],
            "notes": NOTAS["sob_demanda"],
        },
        no_de_pg("tre-observ-metricas", "Metricas", sql_metricas, contrato, [-280, 40], NOTAS["metricas"]),
        no_de_pg("tre-observ-detalhes", "Detalhes", sql_detalhes, contrato, [-40, 40], NOTAS["detalhes"],
                 extra={"alwaysOutputData": True}),
        {
            "parameters": {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": avaliar},
            "id": "tre-observ-avaliar",
            "name": "Avaliar",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [200, 40],
            "notes": NOTAS["avaliar"],
        },
        {
            "parameters": {},
            "id": "tre-observ-relatorio",
            "name": "Relatorio",
            "type": "n8n-nodes-base.noOp",
            "typeVersion": 1,
            "position": [440, 40],
            "notes": NOTAS["relatorio"],
        },
    ]

    def aresta(destino):
        return {"main": [[{"node": destino, "type": "main", "index": 0}]]}

    conexoes = {
        "Agenda": aresta("Metricas"),
        "Executar agora": aresta("Metricas"),
        "Metricas": aresta("Detalhes"),
        "Detalhes": aresta("Avaliar"),
        "Avaliar": aresta("Relatorio"),
    }

    return {
        "id": contrato["workflow"]["id_estavel"],
        "name": contrato["workflow"]["nome"],
        "active": False,
        "nodes": nos,
        "connections": conexoes,
        "settings": {"executionOrder": "v1"},
        "staticData": None,
        "meta": {
            "card": "TRE-W3-E05-T01",
            "contrato": "n8n/contracts/observabilidade-sync.v1.json",
            "montado_por": "scripts/n8n/montar_workflow_observabilidade.py",
        },
        "tags": [],
    }


def texto_do_workflow(workflow):
    return json.dumps(workflow, ensure_ascii=False, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--saida", default=str(ARQ_WORKFLOW))
    parser.add_argument("--conferir", action="store_true",
                        help="compara o workflow montado com o que esta' em disco, sem escrever")
    args = parser.parse_args()

    texto = texto_do_workflow(montar())
    destino = pathlib.Path(args.saida)

    if args.conferir:
        if not destino.exists():
            print("FALHOU workflow ausente: %s" % destino)
            return 1
        if destino.read_text(encoding="utf-8") == texto:
            print("OK    workflow em disco == workflow montado agora (%s)" % destino)
            return 0
        print("FALHOU workflow em disco DIVERGE do montado agora: %s" % destino)
        return 1

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    print("workflow escrito: %s (%d bytes)" % (destino, len(texto.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
