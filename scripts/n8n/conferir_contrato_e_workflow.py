#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Confere o consumidor de outbox contra o proprio contrato — card TRE-W3-E02-T01.

Roda em python3 PURO (sem docker, sem n8n): e' a lente ESTRUTURAL do aceite. Ela nao
testa comportamento (isso e' `scripts/n8n/testar_nucleo_consumidor.js`); ela testa que
o workflow versionado e' MESMO o resultado dos artefatos declarados e que nada foi
escrito a mao por fora deles:

  * o workflow em disco == o montado agora por `scripts/n8n/montar_workflow.py`;
  * os tres textos (nucleo, SQL da fila, SQL do registro) aparecem EMBUTIDOS, byte a
    byte, nos nos correspondentes;
  * o SQL da fila so' LE, respeita a tabela, os status e o teto de eventos do contrato;
  * o SQL do registro tem exatamente os 12 parametros declarados, e a lista que o
    workflow passa (queryReplacement) casa campo a campo, na ordem;
  * nenhum no escreve no Odoo por caminho paralelo (XML-RPC/JSON-RPC) nem tem host
    literal: a unica saida e' a porta unica, por `$env.<base_env>` + rota do contrato;
  * nenhum no ficou orfao (todo no alcancavel a partir dos dois gatilhos).

Uso: python3 scripts/n8n/conferir_contrato_e_workflow.py [--raiz <dir do repo>]
Saida: OK/FALHOU por item + RESUMO; exit 0 = tudo OK, 1 = falhou, 2 = uso errado.
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
    if condicao:
        ok(nome)
    else:
        falhou(nome, detalhe)


def igual(nome, obtido, esperado):
    if obtido == esperado:
        ok(nome)
    else:
        falhou(nome, "obtido=%r esperado=%r" % (obtido, esperado))


def carregar_montador(raiz):
    caminho = raiz / "scripts" / "n8n" / "montar_workflow.py"
    spec = importlib.util.spec_from_file_location("montar_workflow", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def texto(caminho):
    return caminho.read_text(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Confere o consumidor de outbox contra o contrato.")
    ap.add_argument("--raiz", default=str(PADRAO_RAIZ))
    ap.add_argument("--workflow", default="", help="workflow sob teste (padrao: o versionado do repo)")
    args = ap.parse_args()
    raiz = pathlib.Path(args.raiz).resolve()

    montador = carregar_montador(raiz)
    contrato = json.loads(texto(raiz / "n8n" / "contracts" / "outbox-consumer.v1.json"))
    nucleo = texto(raiz / "n8n" / "codigo" / "nucleo-outbox-consumer.js")
    sql_ler = texto(raiz / "n8n" / "sql" / "ler-pendentes.sql")
    sql_registrar = texto(raiz / "n8n" / "sql" / "registrar-resultado.sql")
    sql_trilha = texto(raiz / "n8n" / "sql" / "ler-trilha.sql")
    sql_replay = texto(raiz / "n8n" / "sql" / "registrar-replay.sql")
    alvo = pathlib.Path(args.workflow).resolve() if args.workflow \
        else raiz / "n8n" / "workflows" / "TRE-outbox-consumer.json"
    workflow = json.loads(texto(alvo))

    # ---------------------------------------------------------------- montagem
    esperado = montador.serializar(montador.montar())
    confere("o workflow sob teste e' o montado a partir dos artefatos versionados",
            texto(alvo) == esperado,
            "rode: python3 scripts/n8n/montar_workflow.py")
    igual("id do workflow e' estavel (o publicador e os recibos apontam para ele)",
          workflow["id"], montador.ID_DO_WORKFLOW)
    confere("o workflow nasce inativo (nada roda sozinho ao importar)", workflow["active"] is False)

    por_nome = {n["name"]: n for n in workflow["nodes"]}
    igual("ids dos nos sao unicos", len(por_nome), len(workflow["nodes"]))
    igual("ids dos nos sao unicos (por id)", len({n["id"] for n in workflow["nodes"]}), len(workflow["nodes"]))

    # ------------------------------------------------------- texto embutido
    def embutido(nome_no, arquivo, chave, sub_chave=None):
        no = por_nome.get(nome_no)
        if not no:
            falhou("no %s existe" % nome_no)
            return
        valor = no["parameters"][chave]
        if sub_chave:
            valor = valor.get(sub_chave, "")
        esperado_texto = arquivo.rstrip("\n")
        confere("no '%s' embute %s byte a byte" % (nome_no, chave),
                valor.rstrip("\n") == esperado_texto,
                "o texto do no difere do arquivo versionado")

    embutido("Ler pendentes (outbox)", sql_ler, "query")
    for no in ("Registrar entrega (outbox + trilha)", "Registrar recusa (outbox + trilha)"):
        embutido(no, sql_registrar, "query")
    embutido("Ler trilha (chaves entregues)", sql_trilha, "query")
    embutido("Registrar replay (outbox)", sql_replay, "query")

    marcador = montador.MARCADOR
    for no in ("Nucleo: validar e decidir", "Classificar resposta", "Chaves do lote (nucleo)"):
        js = por_nome[no]["parameters"]["jsCode"]
        partes = js.split(marcador)
        confere("Code node '%s' tem nucleo + adaptador separados pelo marcador" % no, len(partes) == 2)
        if len(partes) == 2:
            confere("Code node '%s' embute o nucleo versionado byte a byte" % no,
                    partes[0].rstrip() == nucleo.rstrip())
            embutido_contrato = re.search(r"const CONTRATO = ([\s\S]*?);\n", partes[1])
            confere("Code node '%s' embute o contrato versionado (JSON igual)" % no,
                    embutido_contrato is not None and
                    json.loads(embutido_contrato.group(1)) == contrato)

    # ------------------------------------------------------------- SQL da fila
    tabela_origem = contrato["origem"]["tabela"]
    confere("SQL da fila le a tabela do contrato (%s)" % tabela_origem, tabela_origem in sql_ler)

    def sem_comentarios(sql):
        """O que vale e' o COMANDO, nao o comentario: o check roda sobre o SQL limpo."""
        linhas = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
        return "\n".join(linhas).strip()

    comando_ler = sem_comentarios(sql_ler)
    igual("SQL da fila limita o ciclo ao declarado no contrato",
          int(re.search(r"LIMIT\s+(\d+)", comando_ler).group(1)), contrato["origem"]["limite_por_ciclo"])
    status_lidos = re.search(r"status\s*=\s*ANY\s*\(\s*ARRAY\[([^\]]*)\]", comando_ler)
    confere("SQL da fila filtra pelos status declarados no contrato",
            status_lidos is not None and
            [s.strip().strip("'") for s in status_lidos.group(1).split(",")] == contrato["origem"]["status_lidos"],
            status_lidos.group(1) if status_lidos else "sem filtro de status")
    confere("SQL da fila e' somente leitura (SELECT; nenhum INSERT/UPDATE/DELETE)",
            comando_ler.upper().startswith("SELECT") and
            not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|DROP)\b", comando_ler.upper()),
            comando_ler[:60])
    for coluna in contrato["origem"]["colunas_lidas"]:
        confere("SQL da fila devolve a coluna %s" % coluna, coluna in sql_ler)

    # --------------------------------------------------------- SQL do registro
    parametros = sorted({int(m) for m in re.findall(r"\$(\d+)", sql_registrar)})
    igual("SQL do registro usa exatamente $1..$12 (sem buraco nem sobra)", parametros, list(range(1, 13)))
    confere("SQL do registro grava a trilha na tabela do contrato",
            contrato["trilha"]["tabela"] in sql_registrar)
    confere("SQL do registro nao duplica trilha (ON CONFLICT pelo idempotency_key UNIQUE)",
            "ON CONFLICT (idempotency_key)" in sql_registrar)
    confere("SQL do registro so' marca processed_at no status de sucesso do contrato",
            "CASE WHEN $1 = '%s' THEN NOW()" % contrato["status"]["sucesso"] in sql_registrar)
    confere("SQL do registro declara a operacao da trilha do contrato (%s)" % contrato["trilha"]["operation"],
            "'%s'" % contrato["trilha"]["operation"] in sql_registrar)
    confere("SQL do registro incrementa tentativas pelo parametro (nao por literal)",
            "attempts = attempts + $2::int" in sql_registrar)
    confere("SQL do registro deixa o motivo visivel (last_error/error_message parametrizados)",
            "last_error = $3" in sql_registrar and "$12" in sql_registrar)

    # ---------------------------------------------------- dedup por chave (T02)
    # A consulta da chave e o registro do replay sao artefatos DECLARADOS no contrato: o
    # verificador exige que os caminhos declarados existam e que o conteudo case com o criterio
    # declarado (status de sucesso, coluna da chave, fila de entrada) — sem isso o "mesmo
    # efeito" seria afirmacao de leitura.
    dedup = contrato["dedup"]
    criterio = dedup["criterio_de_replay"]
    tabela_trilha = contrato["trilha"]["tabela"]
    confere("o contrato declara a consulta da chave do dedup (%s)" % dedup["consulta_da_chave"],
            (raiz / dedup["consulta_da_chave"]).is_file(), dedup["consulta_da_chave"])
    confere("o contrato declara o registro do replay (%s)" % dedup["registro_do_replay"],
            (raiz / dedup["registro_do_replay"]).is_file(), dedup["registro_do_replay"])

    comando_trilha = sem_comentarios(sql_trilha)
    confere("SQL da trilha le a tabela da trilha do contrato (%s)" % tabela_trilha, tabela_trilha in comando_trilha)
    confere("SQL da trilha e' somente leitura (SELECT; nenhum INSERT/UPDATE/DELETE)",
            comando_trilha.upper().startswith("SELECT") and
            not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|DROP)\b", comando_trilha.upper()),
            comando_trilha[:60])
    confere("SQL da trilha consulta pela coluna de chave do contrato (%s)" % criterio["coluna_de_chave"],
            re.search(r"\b%s\s*=\s*ANY" % re.escape(criterio["coluna_de_chave"]), comando_trilha) is not None,
            comando_trilha[-90:])
    for coluna in (criterio["coluna_de_chave"], "status", "completed_at", "response_payload"):
        confere("SQL da trilha devolve %s (e' com isso que o nucleo decide o replay)" % coluna,
                coluna in sql_trilha)

    parametros_replay = sorted({int(m) for m in re.findall(r"\$(\d+)", sql_replay)})
    igual("SQL do replay usa exatamente $1..$2 (sem buraco nem sobra)", parametros_replay, [1, 2])
    comando_replay = sem_comentarios(sql_replay)
    confere("SQL do replay finaliza o evento no status de sucesso do contrato (%s)"
            % contrato["status"]["sucesso"],
            "status = '%s'" % contrato["status"]["sucesso"] in comando_replay)
    confere("SQL do replay exige trilha com o status de sucesso do contrato (%s) — guarda fail-closed"
            % criterio["status_trilha"],
            re.search(r"EXISTS\s*\([\s\S]*?status\s*=\s*'%s'" % criterio["status_trilha"], comando_replay) is not None)
    confere("SQL do replay so' finaliza evento DA FILA (status.entrada do contrato)",
            all("'%s'" % s in comando_replay for s in contrato["status"]["entrada"]), comando_replay[:200])
    confere("SQL do replay NAO escreve na trilha (a linha e' reaproveitada, nunca recriada)",
            not re.search(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+%s" % re.escape(tabela_trilha),
                          comando_replay, re.I))
    confere("SQL do replay NAO incrementa tentativas (replay nao e' tentativa de entrega)",
            "attempts + " not in comando_replay)
    confere("SQL do replay casa o evento pelo id parametrizado (nao por literal)",
            re.search(r"WHERE\s+id\s*=\s*\$2::uuid", comando_replay) is not None)

    # ---------------------------------------------------- dedup: o grafo do workflow
    # O no da consulta PRECISA emitir saida mesmo quando nao ha chave entregue: no sem itens nao
    # deixa o no seguinte rodar, e o consumidor pararia de entregar no PRIMEIRO ciclo (fila cheia,
    # trilha vazia). O aceite mede isso; aqui a propriedade e' travada na estrutura.
    no_trilha = por_nome.get("Ler trilha (chaves entregues)")
    confere("o no da consulta da trilha existe", no_trilha is not None)
    if no_trilha:
        confere("o no da trilha emite saida mesmo sem chave entregue (alwaysOutputData)",
                no_trilha.get("alwaysOutputData") is True)
    igual("a consulta da trilha passa UMA lista de chaves do lote (nao uma chave por parametro)",
          re.findall(r"\$json\.([A-Za-z_][A-Za-z0-9_]*)",
                     por_nome["Ler trilha (chaves entregues)"]["parameters"]["options"]["queryReplacement"]),
          list(montador.CAMPOS_DO_SQL_TRILHA))
    igual("a lista de parametros do no 'Registrar replay (outbox)' casa o SQL (2 campos, na ordem)",
          re.findall(r"\$json\.([A-Za-z_][A-Za-z0-9_]*)",
                     por_nome["Registrar replay (outbox)"]["parameters"]["options"]["queryReplacement"]),
          list(montador.CAMPOS_DO_SQL_REPLAY))

    adaptador_nucleo = por_nome["Nucleo: validar e decidir"]["parameters"]["jsCode"].split(marcador)[-1]
    confere("o adaptador do nucleo le os EVENTOS do no da fila (nao o $input da trilha)",
            "$('Ler pendentes (outbox)').all()" in adaptador_nucleo, adaptador_nucleo.strip())
    confere("o adaptador do nucleo passa a TRILHA lida para a decisao pura",
            "trilhaPorChave($input.all())" in adaptador_nucleo, adaptador_nucleo.strip())
    adaptador_chaves = por_nome["Chaves do lote (nucleo)"]["parameters"]["jsCode"].split(marcador)[-1]
    confere("o adaptador das chaves deriva a lista do lote pelo nucleo (mesma derivacao da decisao)",
            "chavesDoLoteComoItem($input.all(), CONTRATO)" in adaptador_chaves, adaptador_chaves.strip())

    for literal in ("'COMPLETED'", "'PROCESSED'", "'FAILED'", "'REFUSED'"):
        confere("o nucleo nao tem %s literal (o status vem do contrato)" % literal, literal not in nucleo)

    def alvos_de(origem):
        return [a["node"] for ramo in workflow["connections"].get(origem, {}).get("main", []) for a in ramo]

    igual("a cadeia do dedup e' Ler pendentes -> Chaves do lote -> Ler trilha -> Nucleo",
          [alvos_de("Ler pendentes (outbox)"), alvos_de("Chaves do lote (nucleo)"),
           alvos_de("Ler trilha (chaves entregues)")],
          [["Chaves do lote (nucleo)"], ["Ler trilha (chaves entregues)"], ["Nucleo: validar e decidir"]])
    igual("o IF do replay compara com o codigo de decisao declarado no contrato",
          por_nome["Decisao: replay?"]["parameters"]["conditions"]["conditions"][0]["rightValue"], dedup["decisao"])
    igual("o ramo sem entrega da decisao de entrega vai para a decisao de replay",
          alvos_de("Decisao: entregar?")[1], "Decisao: replay?")
    igual("a decisao de replay manda o replay para o registro do replay",
          alvos_de("Decisao: replay?")[0], "Registrar replay (outbox)")
    igual("só a decisao de entrega alimenta a porta unica (o replay nao tem caminho para o POST)",
          [n["name"] for n in workflow["nodes"]
           if any(a["node"] == "Chamar API controlada (porta unica)"
                  for ramo in workflow["connections"].get(n["name"], {}).get("main", []) for a in ramo)],
          ["Decisao: entregar?"])
    igual("o no do registro do replay termina o caminho (nao alimenta nenhum outro no)",
          workflow["connections"].get("Registrar replay (outbox)"), None)

    # ------------------------------------ a lista que o workflow passa para o SQL
    campos_montador = list(montador.CAMPOS_DO_SQL_REGISTRAR)
    for no in ("Registrar entrega (outbox + trilha)", "Registrar recusa (outbox + trilha)"):
        expressao = por_nome[no]["parameters"]["options"]["queryReplacement"]
        campos_do_no = re.findall(r"\$json\.([A-Za-z_][A-Za-z0-9_]*)", expressao)
        igual("a lista de parametros do no '%s' casa o SQL (12 campos, na ordem)" % no,
              campos_do_no, campos_montador)
    confere("a lista e' uma LISTA (array), nao string separada por virgula",
            all(por_nome[n]["parameters"]["options"]["queryReplacement"].startswith("={{ [")
                for n in ("Registrar entrega (outbox + trilha)", "Registrar recusa (outbox + trilha)")))

    # ---------------------------------------------------- a unica porta de saida
    portas = [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
    igual("existe exatamente UMA chamada HTTP no consumidor", len(portas), 1)
    if portas:
        url = portas[0]["parameters"]["url"]
        confere("a URL da porta unica vem do ambiente (%s)" % contrato["destino"]["base_env"],
                "$env.%s" % contrato["destino"]["base_env"] in url, url)
        confere("a URL da porta unica usa a rota do contrato (%s)" % contrato["destino"]["rota"],
                contrato["destino"]["rota"] in url, url)
        confere("a chamada e' POST", portas[0]["parameters"]["method"] == "POST")
        igual("a porta unica usa a credencial declarada (id/nome, nunca valor)",
              portas[0]["credentials"]["httpHeaderAuth"],
              {"id": contrato["credenciais"]["api"]["id"], "name": contrato["credenciais"]["api"]["nome"]})
        # Sem `authentication`/`genericAuthType` o n8n IGNORA a credencial: o pedido sai sem o
        # cabecalho e a porta unica devolve 401 — o aceite pegou exatamente isso na primeira rodada.
        igual("a chamada usa credencial generica (sem isso o n8n ignora a credencial)",
              portas[0]["parameters"].get("authentication"), "genericCredentialType")
        igual("a credencial da chamada e' do tipo httpHeaderAuth",
              portas[0]["parameters"].get("genericAuthType"), "httpHeaderAuth")
        # Entrega SERIALIZADA (declarada no contrato): sem `batching.batch.batchSize = 1` o no
        # dispara o lote em paralelo e a identidade canonica pode duplicar.
        serial = contrato["destino"].get("entrega_serializada") or {}
        lote = (portas[0]["parameters"].get("options") or {}).get("batching", {}).get("batch", {})
        confere("o contrato declara a entrega serializada (batch e intervalo)",
                serial.get("batch_size") and serial.get("intervalo_ms") is not None)
        igual("a entrega e' uma por vez, como o contrato declara (batching.batchSize)",
              lote.get("batchSize"), serial.get("batch_size"))
        igual("o intervalo entre chamadas e' o declarado no contrato (batching.batchInterval)",
              lote.get("batchInterval"), serial.get("intervalo_ms"))
    texto_do_workflow = json.dumps(workflow, ensure_ascii=False)
    for proibido in ("xmlrpc", "execute_kw", "/jsonrpc", "psycopg", "res_partner"):
        confere("nenhum caminho paralelo para o Odoo no workflow (%s)" % proibido, proibido not in texto_do_workflow)
    # Nenhum host literal: a base da porta unica TEM de vir do ambiente. O item vale para o
    # texto INTEIRO do workflow (inclusive o nucleo/SQL embutidos) e tambem para o parametro
    # `url` das portas — que ja' e' medido acima por conter `$env.<base_env>` + a rota do
    # contrato. A versao anterior so' reprovava `http://127.0.0.1`, o que deixava o item MORTO
    # (medido com `http://host-literal.example:8069/tf/api/v1/...` gravado no parametro `url`:
    # imprimia OK); corrigido na rodada 2.
    fora_das_portas = texto_do_workflow
    for porta in portas:
        fora_das_portas = fora_das_portas.replace(porta["parameters"].get("url") or "", "")
    literais_fora = sorted(set(re.findall(r"https?://[^\s\"'\\]+", fora_das_portas)))
    literais_na_porta = sorted({achado for porta in portas
                                for achado in re.findall(r"https?://[^\s\"'\\]+",
                                                         porta["parameters"].get("url") or "")})
    confere("nenhum host literal no workflow (nem fora da porta unica, nem dentro dela)",
            not literais_fora and not literais_na_porta,
            (("fora: %s " % ", ".join(literais_fora[:3])) if literais_fora else "") +
            (("porta: %s" % ", ".join(literais_na_porta[:3])) if literais_na_porta else ""))

    # ---------------------------------------------------------- grafo do no
    alvos = set()
    for saida in workflow["connections"].values():
        for ramo in saida["main"]:
            for aresta in ramo:
                alvos.add(aresta["node"])
    gatilhos = [n["name"] for n in workflow["nodes"]
                if n["type"] in ("n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.manualTrigger")]
    igual("os dois gatilhos declarados estao no workflow", sorted(gatilhos),
          ["Entrada por agenda (poll)", "Entrada sob demanda"])
    orfaos = [n["name"] for n in workflow["nodes"] if n["name"] not in alvos and n["name"] not in gatilhos]
    igual("nenhum no orfao (todo no alcancavel a partir dos gatilhos)", orfaos, [])
    for nome_gatilho in gatilhos:
        confere("gatilho '%s' alimenta a leitura da fila" % nome_gatilho,
                workflow["connections"][nome_gatilho]["main"][0][0]["node"] == "Ler pendentes (outbox)")

    print("RESULTADO: CONTRATO_WORKFLOW_OK (%d itens, 0 falhas)" % ITENS if FALHAS == 0
          else "RESULTADO: CONTRATO_WORKFLOW_FALHOU (%d itens, %d falha(s))" % (ITENS, FALHAS))
    return 0 if FALHAS == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
