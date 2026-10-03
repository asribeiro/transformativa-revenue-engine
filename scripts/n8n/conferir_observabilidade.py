#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lente estrutural da observabilidade de sync — card TRE-W3-E05-T01.

Roda em python3 PURO (sem docker, sem n8n): e' a lente ESTRUTURAL do aceite. Ela nao
testa comportamento (isso e' `scripts/n8n/testar_observabilidade_sync.js`, e o aceite
ponta a ponta e' `scripts/n8n/verificar-observabilidade-sync.sh`); ela testa que o
workflow versionado e' MESMO o resultado dos artefatos declarados, e que nada foi
escrito a mao por fora deles:

  * o contrato declara tudo o que decide (metrica, limiar, direcao, ausencia,
    vocabulario, conferencia cruzada) — nada implicito;
  * as DUAS consultas so' LEEM as duas tabelas declaradas, nunca payload, e cobrem
    exatamente as metricas declaradas (nos dois sentidos: contrato sem SQL e SQL sem
    contrato reprovam);
  * o nucleo nao carrega lista de metrica nem limiar literal (a decisao vive no
    contrato) e nao importa nada fora do adaptador de linha de comando;
  * o workflow em disco == o montado agora; o Code node embute o nucleo byte a byte e
    o contrato embutido e' o arquivo; os nos Postgres embutem o SQL byte a byte; os nos,
    as conexoes, a credencial (por id/nome) e a inatividade sao os declarados;
  * os espelhos com os contratos vizinhos batem: a chave derivada e o teto de tentativas
    do consumidor (n8n/contracts/outbox-consumer.v1.json) e as direcoes/status das duas
    portas (consumidor + n8n/contracts/odoo-events-ingest.v1.json).

Uso: python3 scripts/n8n/conferir_observabilidade.py [--raiz <dir do repo>]
Saida: OK/FALHOU por item + RESULTADO; exit 0 = tudo OK, 1 = falhou, 2 = uso errado.
"""
import argparse
import importlib.util
import json
import pathlib
import re
import sys

AQUI = pathlib.Path(__file__).resolve().parent
PADRAO_RAIZ = AQUI.parents[1]

ITENS = 0
FALHAS = 0


def ok(nome):
    global ITENS
    ITENS += 1
    print("OK    %s" % nome)


def falhou(nome, detalhe=""):
    global ITENS, FALHAS
    ITENS += 1
    FALHAS += 1
    print("FALHOU %s%s" % (nome, ("  [%s]" % detalhe) if detalhe else ""))


def confere(nome, condicao, detalhe=""):
    ok(nome) if condicao else falhou(nome, detalhe)


def igual(nome, obtido, esperado):
    if obtido == esperado:
        ok(nome)
    else:
        falhou(nome, "obtido=%r esperado=%r" % (obtido, esperado))


def ler(caminho):
    return caminho.read_text(encoding="utf-8")


def sem_comentario(texto):
    """Texto sem as linhas de comentario SQL (`--`) — para medir o que a consulta FAZ,
    e nao o que ela DOCUMENTA (o cabecalho dos arquivos cita payload de proposito)."""
    return "\n".join(linha for linha in texto.splitlines() if not linha.lstrip().startswith("--"))


def carregar_json(caminho):
    if not caminho.exists():
        return None
    try:
        return json.loads(ler(caminho))
    except json.JSONDecodeError as erro:
        falhou("JSON invalido", "%s: %s" % (caminho, erro))
        return None


def importar_montador(raiz):
    caminho = raiz / "scripts" / "n8n" / "montar_workflow_observabilidade.py"
    spec = importlib.util.spec_from_file_location("montador_obs", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


MARCADOR = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"
MARCADOR_CLI = "Adaptador de linha de comando (FORA do nucleo versionado)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raiz", default=str(PADRAO_RAIZ))
    parser.add_argument("--ingest", default=None,
                        help="contrato da PORTA DE INGESTAO quando ela nao esta' no base "
                             "(chega com TRE-W3-E03-T01): com ele a lente confere o espelho de verdade")
    args = parser.parse_args()
    raiz = pathlib.Path(args.raiz).resolve()

    arq_contrato = raiz / "n8n" / "contracts" / "observabilidade-sync.v1.json"
    arq_consumidor = raiz / "n8n" / "contracts" / "outbox-consumer.v1.json"
    arq_ingest = raiz / "n8n" / "contracts" / "odoo-events-ingest.v1.json"
    arq_sql_metricas = raiz / "n8n" / "sql" / "observabilidade-sync.sql"
    arq_sql_detalhes = raiz / "n8n" / "sql" / "observabilidade-sync-dead-letters.sql"
    arq_nucleo = raiz / "n8n" / "codigo" / "observabilidade-sync.js"
    arq_workflow = raiz / "n8n" / "workflows" / "TRE-observabilidade-sync.json"

    for arquivo in (arq_contrato, arq_sql_metricas, arq_sql_detalhes, arq_nucleo, arq_workflow,
                    arq_consumidor):
        if not arquivo.exists():
            falhou("artefato em disco", "%s ausente" % arquivo)
    if FALHAS:
        print("RESULTADO: OBSERVABILIDADE_LENTE_FALHOU (%d itens, %d falha(s))" % (ITENS, FALHAS))
        return 1

    contrato = carregar_json(arq_contrato)
    consumidor = carregar_json(arq_consumidor)
    ingest = carregar_json(pathlib.Path(args.ingest)) if args.ingest else (
        carregar_json(arq_ingest) if arq_ingest.exists() else None)
    sql_metricas = ler(arq_sql_metricas)
    sql_detalhes = ler(arq_sql_detalhes)
    nucleo = ler(arq_nucleo)

    # ------------------------------------------------------------ contrato declarado
    print("--- contrato: nada implicito ---")
    for chave in ("esquema", "versao", "id", "titulo", "descricao", "autoridade", "fontes",
                  "vocabulario_da_trilha", "observado", "veredito", "regras_de_fail_closed",
                  "metricas", "detalhes", "conferencias_cruzadas", "relatorio", "workflow",
                  "credenciais", "ambiente", "segredos", "lacunas_declaradas", "governanca"):
        confere("contrato declara `%s`" % chave, chave in contrato)

    metricas = contrato.get("metricas", [])
    confere("contrato declara metricas", len(metricas) >= 10, "declaradas: %d" % len(metricas))
    ids = [m.get("id") for m in metricas]
    igual("ids de metrica sao unicos", len(set(ids)), len(ids))

    incompletas = [m.get("id") for m in metricas
                   if not all(m.get(c) for c in ("id", "titulo", "fonte", "unidade", "direcao", "ausencia"))]
    igual("toda metrica declara id/titulo/fonte/unidade/direcao/ausencia", incompletas, [])
    fontes = sorted({m.get("fonte") for m in metricas})
    igual("toda metrica vem de uma fonte declarada (outbox|trilha)", fontes,
          sorted(contrato["fontes"].keys() & set(fontes)) or fontes)

    decisoes = [m for m in metricas if m.get("informativa") is not True]
    ruins = [m["id"] for m in decisoes
             if not isinstance((m.get("limites") or {}).get("alerta"), (int, float))
             or not isinstance((m.get("limites") or {}).get("critico"), (int, float))]
    igual("toda metrica que decide veredito declara alerta e critico", ruins, [])
    invertidos = [m["id"] for m in decisoes
                  if (m["limites"].get("critico") or 0) < (m["limites"].get("alerta") or 0)]
    igual("nenhum limiar invertido", invertidos, [])
    abaixo_de_um = [m["id"] for m in decisoes if m["limites"]["alerta"] < 1 or m["limites"]["critico"] < 1]
    igual("nenhum limiar abaixo de 1 (0 tornaria o estado saudavel em alerta)", abaixo_de_um, [])
    informativas_com_limite = [m["id"] for m in metricas
                               if m.get("informativa") is True and m.get("limites") is not None]
    igual("metrica informativa nao declara limiar", informativas_com_limite, [])

    dimensionais = [m for m in metricas if m.get("dimensional")]
    confere("ha metrica dimensional declarada", len(dimensionais) == 1,
            "declaradas: %s" % [m["id"] for m in dimensionais])
    for m in dimensionais:
        d = m["dimensional"]
        confere("metricas dimensionais declaram campos/valores/combinacao_ausente/direcoes (%s)" % m["id"],
                all(c in d for c in ("campos", "valores", "combinacao_ausente", "direcoes")))
        confere("toda dimensao tem vocabulario declarado (%s)" % m["id"],
                all(campo in d["valores"] for campo in d["campos"]))

    tipos_de_detalhe = set(contrato["detalhes"]["tipos"].keys())
    confere("contrato declara os detalhes com motivo (>= 3 tipos)", len(tipos_de_detalhe) >= 3,
            str(sorted(tipos_de_detalhe)))
    declara_payload_fora = any("payload" in item for item in contrato["detalhes"]["nao_mostrar"])
    confere("contrato PROIBE payload no relatorio", declara_payload_fora)

    regras = contrato["regras_de_fail_closed"]
    for regra in ("metrica_declarada_sem_linha", "valor_nao_numerico", "metrica_nao_declarada",
                  "limiar_ausente", "direcao_desconhecida", "dimensao_desconhecida",
                  "conferencia_cruzada_divergente", "ausencia"):
        confere("contrato declara a regra de fail-closed `%s`" % regra, regra in regras)
    confere("as regras de fail-closed fecham em INDETERMINADO (nunca OK)",
            all("INDETERMINADO" in str(regras[r]) for r in regras if r != "ausencia"))
    igua = contrato["regras_de_fail_closed"]["ausencia"]
    confere("a regra de ausencia declara o padrao fail-closed",
            igua.get("padrao") == "erro" and "erro" in igua.get("valores", []))

    for conf in contrato["conferencias_cruzadas"]:
        confere("conferencia cruzada `%s` aponta metrica declarada" % conf["id"],
                conf["metrica"] in ids)
        confere("conferencia cruzada `%s` aponta tipo de detalhe declarado" % conf["id"],
                conf["tipo_de_detalhe"] in tipos_de_detalhe)

    # ------------------------------------------------------------ SQL das metricas
    print("--- SQL das metricas: so' le, e cobre o que o contrato declara ---")
    proibidos = contrato["fontes"]["somente_leitura"]["proibido"]

    def sem_palavra_proibida(nome, texto):
        corpo = sem_comentario(texto).upper()
        achados = [p for p in proibidos if re.search(r"\b%s\b" % p.upper(), corpo)]
        igual("%s nao usa nenhuma construcao proibida (somente leitura)" % nome, achados, [])

    sem_palavra_proibida("a consulta de metricas", sql_metricas)
    sem_palavra_proibida("a consulta de detalhes", sql_detalhes)
    igual("a consulta de metricas le a tabela da fila declarada",
          contrato["fontes"]["outbox"]["tabela"] in sql_metricas, True)
    igual("a consulta de metricas le a tabela da trilha declarada",
          contrato["fontes"]["trilha"]["tabela"] in sql_metricas, True)

    corpo_metricas = sem_comentario(sql_metricas)
    fracao = sorted(set(re.findall(r"SELECT\s+'([a-z_][a-z0-9_]*)'", corpo_metricas)))
    declaradas = set(ids)
    igual("toda metrica declarada aparece na consulta", sorted(declaradas - set(fracao)), [])
    igual("a consulta nao traz metrica que o contrato nao declara", sorted(set(fracao) - declaradas), [])
    igual("a consulta usa a tabela da fila pelo nome do contrato",
          "sales_intelligence.outbox_events" in corpo_metricas, True)

    for direcao in contrato["vocabulario_da_trilha"]["direcoes_declaradas"]:
        confere("a direcao declarada `%s` aparece no vocabulario do SQL" % direcao["id"],
                ("('%s', '%s')" % (direcao["source_system"], direcao["target_system"])) in corpo_metricas)
    for status in contrato["vocabulario_da_trilha"]["status"]:
        confere("o status declarado `%s` aparece na consulta" % status, "'%s'" % status in corpo_metricas)
    # "ausencia de ATIVIDADE" (zero sincronizacao = informacao) nao pode chegar ao nucleo
    # como "ausencia de MEDICAO" (linha faltando = INDETERMINADO): a grade do vocabulario
    # declarado tem de ser EMITIDA pela consulta, com a trilha entrando por LEFT JOIN.
    confere("a metrica dimensional emite a GRADE declarada (direcoes x status, com LEFT JOIN)",
            re.search(r"CROSS JOIN\s*\(\s*VALUES", corpo_metricas) is not None
            and "LEFT JOIN" in corpo_metricas)

    # ------------------------------------------------------------------ espelho do teto
    print("--- espelhos com os contratos vizinhos ---")
    teto_obs = contrato["observado"]["teto_de_tentativas"]
    teto_consumidor = consumidor["retry"]["teto_de_tentativas"]
    igual("o teto de tentativas observado espelha o do contrato do consumidor",
          teto_obs, teto_consumidor)
    confere("o teto declarado aparece no SQL (fila no teto)",
            re.search(r"attempts,\s*0\)\s*>=\s*%d" % teto_obs, corpo_metricas) is not None,
            "nao achei `>= %d` sobre attempts" % teto_obs)
    igual("a chave derivada declarada espelha a do consumidor",
          contrato["observado"]["chave_da_trilha"]["derivacao"],
          consumidor["trilha"]["chave_de_idempotencia"]["derivacao"])
    igual("os status de sucesso/recusa da fila espelham o consumidor",
          [contrato["observado"]["status_da_fila"]["sucesso"],
           contrato["observado"]["status_da_fila"]["recusa"]],
          [consumidor["status"]["sucesso"], consumidor["status"]["recusa"]])

    portas = {(d["source_system"], d["target_system"])
              for d in contrato["vocabulario_da_trilha"]["direcoes_declaradas"]}
    lacunas_texto = " ".join(contrato["lacunas_declaradas"])
    for direcao in contrato["vocabulario_da_trilha"]["direcoes_declaradas"]:
        arquivo_dono = (direcao.get("dono") or "").split(" ")[0]
        caminho_dono = raiz / arquivo_dono
        if caminho_dono.exists():
            dono = carregar_json(caminho_dono)
            igual("a direcao `%s` e' a porta declarada no contrato dono (%s)" % (direcao["id"], arquivo_dono),
                  [direcao["source_system"], direcao["target_system"]],
                  [dono["trilha"]["source_system"], dono["trilha"]["target_system"]])
        else:
            # Contrato dono fora deste base (chega com outro card): a ausencia tem de estar
            # DECLARADA — silenciar a ausencia seria pior que nao conferir.
            confere("a direcao `%s` vem de contrato fora deste base e a ausencia esta declarada" % direcao["id"],
                    caminho_dono.name in lacunas_texto)

    status_consumidor = set(consumidor["trilha"]["status"].values())
    meus_status = set(contrato["vocabulario_da_trilha"]["status"])
    origem = contrato["vocabulario_da_trilha"]["origem_dos_status"]
    igual("todo status da trilha tem origem declarada (e nenhuma origem orfa)",
          sorted(set(origem) ^ meus_status), [])
    if ingest:
        igual("o vocabulario de status cobre (e nao inventa) os dois contratos vizinhos",
              sorted(meus_status), sorted(status_consumidor | set(ingest["trilha"]["status"].values())))
    else:
        igual("o vocabulario de status cobre os do contrato do consumidor",
              sorted(status_consumidor - meus_status), [])
    for status in sorted(meus_status):
        if status in status_consumidor:
            confere("o status `%s` aponta o contrato do consumidor como dono" % status,
                    "outbox-consumer" in origem.get(status, ""))
        elif ingest:
            igual("o status `%s` confere com a porta de ingestao" % status,
                  status in set(ingest["trilha"]["status"].values()), True)
        else:
            confere("o status `%s` (porta fora deste base) aponta a ingestao e a lacuna declarada" % status,
                    "odoo-events-ingest" in origem.get(status, "") and "odoo-events-ingest" in lacunas_texto)

    # ------------------------------------------------------------ SQL dos detalhes
    print("--- SQL dos detalhes: motivo, sem payload, dentro do teto ---")
    corpo_detalhes = sem_comentario(sql_detalhes)
    for proibido in ("request_payload", "response_payload", "payload", "token", "Bearer"):
        confere("a consulta de detalhes nao seleciona `%s`" % proibido, proibido not in corpo_detalhes)
    tipos_no_sql = sorted(set(re.findall(r"SELECT\s+'([a-z_][a-z0-9_]*)'", corpo_detalhes)))
    igual("a consulta de detalhes traz exatamente os tipos declarados",
          tipos_no_sql, sorted(tipos_de_detalhe))
    confere("a consulta de detalhes le o motivo das duas fontes (evento e trilha)",
            "last_error" in corpo_detalhes and "error_message" in corpo_detalhes)
    limite = contrato["detalhes"]["limite_de_linhas"]
    confere("a consulta de detalhes respeita o teto de linhas declarado (%d)" % limite,
            re.search(r"LIMIT\s+%d\b" % limite, corpo_detalhes.upper()) is not None
            or re.search(r"LIMIT\s+%d\b" % limite, corpo_detalhes) is not None)
    igual("a consulta de detalhes usa as duas tabelas declaradas",
          all(t in corpo_detalhes for t in ("sales_intelligence.outbox_events",
                                           "sales_intelligence.sync_events")), True)

    # ------------------------------------------------------------ nucleo
    print("--- nucleo: decisao vem do contrato, nada literal ---")
    corpo_nucleo = nucleo.split(MARCADOR_CLI)[0] if MARCADOR_CLI in nucleo else nucleo
    literais = [i for i in ids if i in corpo_nucleo]
    igual("nenhum id de metrica aparece literal no nucleo", literais, [])
    limites = sorted({v for m in metricas for v in (m.get("limites") or {}).values()})
    # Limiar de UM digito colide com constante estrutural do proprio codigo (indice de
    # array, cardinalidade: `minhas.length > 1`). O que nao pode existir e' o limiar que
    # DECIDE escrito no corpo — e todo limiar com dois digitos ou mais tem de estar no
    # contrato, nunca aqui.
    achados = [v for v in limites if v >= 10 and re.search(r"(?<![\w.])%d(?![\w.])" % v, corpo_nucleo)]
    igual("nenhum limiar declarado (>= 10) aparece literal no nucleo", achados, [])
    confere("a comparacao de limiar no nucleo passa pelos limites do contrato",
            "limites.critico" in corpo_nucleo and "limites.alerta" in corpo_nucleo
            and "avaliarValor(valor, limites)" in corpo_nucleo)
    confere("o teto do motivo no nucleo vem do contrato (sem numero de reserva no codigo)",
            "contrato.relatorio.tamanho_maximo_do_motivo" in corpo_nucleo
            and not re.search(r"tamanho_maximo_do_motivo\s*\)\s*\|\|", corpo_nucleo))
    confere("o nucleo nao importa nada no corpo versionado (o adaptador de linha de comando e a fronteira)",
            "require(" not in corpo_nucleo)
    confere("o nucleo nao carrega payload no texto", "request_payload" not in nucleo
            and "response_payload" not in nucleo)
    for exportado in ("NUCLEO_VERSAO", "avaliar", "medirMetrica", "formatarRelatorio",
                      "conferenciasCruzadas", "sanitizar"):
        confere("o nucleo exporta `%s`" % exportado,
                re.search(r"%s\s*:" % exportado, nucleo) is not None)
    confere("nenhum segredo no nucleo (Bearer/token literal)",
            re.search(r"Bearer\s+[A-Za-z0-9]{16,}", nucleo) is None)

    # ------------------------------------------------------------ workflow derivado
    print("--- workflow: artefato derivado, nao escrito a mao ---")
    montador = importar_montador(raiz)
    montado = json.dumps(montador.montar(), ensure_ascii=False, indent=2) + "\n"
    em_disco = ler(arq_workflow)
    igual("o workflow em disco e' o montado agora (montador deterministico)", em_disco == montado, True)
    workflow = carregar_json(arq_workflow)

    igual("o id estavel do workflow e' o declarado", workflow["id"],
          contrato["workflow"]["id_estavel"])
    igual("o nome do workflow e' o declarado", workflow["name"], contrato["workflow"]["nome"])
    igual("o workflow nasce INATIVO (publicar e' passo de operador)", bool(workflow["active"]), False)

    nos = {n["name"]: n for n in workflow["nodes"]}
    igual("os nos do workflow sao exatamente os declarados (nomes e ordem)",
          [n["name"] for n in workflow["nodes"]], contrato["workflow"]["grafo"])

    no_codigo = [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.code"]
    igual("ha exatamente um Code node", len(no_codigo), 1)
    js = no_codigo[0]["parameters"]["jsCode"]
    corte = js.find(MARCADOR)
    confere("o Code node tem o marcador do adaptador", corte > 0)
    igual("o Code node embute o nucleo versionado (arquivo inteiro, sem edicao a mao)",
          js[:corte].rstrip("\n"), nucleo.rstrip())

    # o contrato embutido e' conferido por chaves balanceadas (mesmo metodo da suite node)
    inicio = js.index("const CONTRATO = ") + len("const CONTRATO = ")
    abertura = js.index("{", inicio)
    profundidade = 0
    for i in range(abertura, len(js)):
        if js[i] == "{":
            profundidade += 1
        elif js[i] == "}":
            profundidade -= 1
            if profundidade == 0:
                fim = i + 1
                break
    else:
        falhou("o contrato embutido fecha as chaves")
        fim = None
    if fim:
        igual("o contrato embutido e' o arquivo em disco", json.loads(js[abertura:fim]), contrato)

    no_metricas = nos.get("Metricas", {})
    no_detalhes = nos.get("Detalhes", {})
    igual("o no Metricas embute o SQL da medicao byte a byte",
          no_metricas.get("parameters", {}).get("query"), ler(arq_sql_metricas).rstrip() + "\n")
    igual("o no Detalhes embute o SQL dos detalhes byte a byte",
          no_detalhes.get("parameters", {}).get("query"), ler(arq_sql_detalhes).rstrip() + "\n")
    igual("o no Detalhes tem alwaysOutputData (rodada saudavel nao para a cadeia)",
          no_detalhes.get("alwaysOutputData"), True)
    # `executeOnce`: a consulta e' a MEDICAO da rodada, nao um passo por linha de entrada.
    # Sem isto o n8n roda o no' uma vez por item que chega (as 20 linhas de metrica) e a
    # lista de detalhes sai multiplicada — defeito medido no primeiro aceite real.
    igual("as duas consultas rodam UMA vez por rodada (executeOnce), nao uma por linha de entrada",
          [nos["Metricas"].get("executeOnce"), nos["Detalhes"].get("executeOnce")], [True, True])
    confere("o contrato declara a regra `consulta_uma_vez`", "consulta_uma_vez" in contrato["workflow"])
    confere("o contrato declara o que e' linha vazia de detalhe (placeholder do alwaysOutputData)",
            "linha_vazia" in contrato["detalhes"])
    for nome in ("Metricas", "Detalhes"):
        cred = nos[nome].get("credentials", {}).get("postgres", {})
        igual("o no %s usa a credencial declarada por id/nome" % nome,
              [cred.get("id"), cred.get("name")],
              [contrato["credenciais"]["postgres"]["id"], contrato["credenciais"]["postgres"]["nome"]])

    gatilhos = [n["name"] for n in workflow["nodes"]
                if n["type"] in ("n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.manualTrigger")]
    igual("os dois gatilhos declarados estao no workflow", sorted(gatilhos),
          sorted([contrato["workflow"]["gatilho"]["no"], contrato["workflow"]["gatilho"]["sob_demanda"]["no"]]))
    alvos = {a["node"] for saida in workflow["connections"].values()
             for ramo in saida["main"] for a in ramo}
    orfaos = [n["name"] for n in workflow["nodes"] if n["name"] not in alvos and n["name"] not in gatilhos]
    igual("nenhum no orfao", orfaos, [])
    for gatilho in gatilhos:
        igual("o gatilho `%s` alimenta a medicao" % gatilho,
              workflow["connections"][gatilho]["main"][0][0]["node"], "Metricas")
    cadeia = ["Metricas", "Detalhes", "Avaliar", "Relatorio"]
    for origem, destino in zip(cadeia, cadeia[1:]):
        igual("a cadeia segue o grafo declarado: %s -> %s" % (origem, destino),
              workflow["connections"][origem]["main"][0][0]["node"], destino)

    texto_workflow = json.dumps(workflow, ensure_ascii=False)
    confere("nenhum host literal/URL no workflow",
            re.search(r"https?://(?!\S*\$env)", texto_workflow) is None)
    confere("nenhum segredo no workflow", re.search(r"Bearer\s+[A-Za-z0-9]{16,}", texto_workflow) is None
            and "password" not in texto_workflow.lower())

    print("RESULTADO: OBSERVABILIDADE_LENTE_OK (%d itens, 0 falhas)" % ITENS if FALHAS == 0
          else "RESULTADO: OBSERVABILIDADE_LENTE_FALHOU (%d itens, %d falha(s))" % (ITENS, FALHAS))
    return 0 if FALHAS == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
