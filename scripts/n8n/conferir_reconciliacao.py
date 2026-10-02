#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lente estrutural da RECONCILIACAO — card TRE-W3-E04-T01.

Roda em python3 PURO (sem docker, sem n8n): e' a lente ESTRUTURAL do aceite. Ela
nao testa comportamento (isso e' `scripts/n8n/testar_nucleo_reconciliacao.js`, e o
aceite ponta a ponta e' `scripts/n8n/verificar-reconciliacao.sh`); ela testa que o
workflow versionado e' MESMO o resultado dos artefatos declarados, e que nada foi
escrito a mao por fora deles:

  * o contrato declara tudo o que decide (comparacoes nomeadas com motivo, lote,
    janela, vocabulario da fila e da trilha, leituras do destino com campos e
    operador, regras de fail-closed, proibicoes, relatorio);
  * as DUAS consultas so' LEEM as tabelas declaradas — nenhum INSERT/UPDATE/DELETE/
    DDL/lock/COPY (o job nao corrige: doc 06 §8) — e cada uma devolve a linha de
    COBERTURA (sem ela, base vazia e consulta quebrada viram a mesma coisa);
  * o nucleo nao carrega limiar, vocabulario nem nome de comparacao literal (a decisao
    vive no contrato) e nao importa nada;
  * o workflow em disco == o montado agora; o Code node embute o nucleo byte a byte e
    o contrato embutido e' o arquivo; os nos Postgres embutem o SQL byte a byte; os
    nos, as conexoes, a credencial (por id/nome) e a inatividade sao os declarados;
    e o workflow NAO chama operacao de ESCRITA da porta unica;
  * os espelhos com os vizinhos batem: teto de tentativas, vocabulario da fila e da
    trilha e chave derivada do consumidor (n8n/contracts/outbox-consumer.v1.json) e a
    politica da API (odoo/addons/transformativa_sales_ai/api/politica_api.json:
    operacao de leitura, campos, filtros e limites).

Uso: python3 scripts/n8n/conferir_reconciliacao.py [--raiz <dir do repo>]
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


def sem_comentario_sql(texto):
    """Texto sem as linhas de comentario SQL (`--`) — para medir o que a consulta FAZ,
    e nao o que ela DOCUMENTA (o cabecalho cita INSERT/UPDATE de proposito, para dizer
    que nao os tem)."""
    return "\n".join(linha for linha in texto.splitlines()
                     if not linha.lstrip().startswith("--"))


def sem_comentario_js(texto):
    """Texto JS sem comentarios de bloco e de linha — o cabecalho do nucleo cita os
    status e o numero do teto para EXPLICAR a regra; o que se mede e' o codigo."""
    sem_bloco = re.sub(r"/\*.*?\*/", " ", texto, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", sem_bloco, flags=re.MULTILINE)


def carregar_json(caminho):
    if not caminho.exists():
        return None
    try:
        return json.loads(ler(caminho))
    except json.JSONDecodeError as erro:
        falhou("JSON invalido", "%s: %s" % (caminho, erro))
        return None


MARCADOR = "/* --- adaptador do Code node (fora do nucleo versionado) --- */"


def importar_montador(raiz):
    caminho = raiz / "scripts" / "n8n" / "montar_workflow_reconciliacao.py"
    spec = importlib.util.spec_from_file_location("montador_reconciliacao", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raiz", default=str(PADRAO_RAIZ))
    parser.add_argument("--workflow", default=None,
                        help="workflow a conferir (default: o versionado no repo). A prova de dente "
                             "aponta para a COPIA mutada: a lente tem que reprovar o item que a "
                             "mutacao quebrou")
    args = parser.parse_args()
    raiz = pathlib.Path(args.raiz).resolve()

    arq_contrato = raiz / "n8n" / "contracts" / "reconciliation-job.v1.json"
    arq_consumidor = raiz / "n8n" / "contracts" / "outbox-consumer.v1.json"
    arq_politica = raiz / "odoo" / "addons" / "transformativa_sales_ai" / "api" / "politica_api.json"
    arq_migration = raiz / "db" / "migrations" / "0001_sales_intelligence_v1.sql"
    arq_sql_origem = raiz / "n8n" / "sql" / "reconciliacao-origem.sql"
    arq_sql_pendentes = raiz / "n8n" / "sql" / "reconciliacao-pendentes.sql"
    arq_nucleo = raiz / "n8n" / "codigo" / "nucleo-reconciliacao.js"
    arq_workflow = pathlib.Path(args.workflow) if args.workflow else raiz / "n8n" / "workflows" / "TRE-reconciliation.json"

    for arquivo in (arq_contrato, arq_consumidor, arq_politica, arq_migration, arq_sql_origem,
                    arq_sql_pendentes, arq_nucleo, arq_workflow):
        if not arquivo.exists():
            falhou("artefato em disco", "%s ausente" % arquivo)
    if FALHAS:
        print("RESULTADO: RECONCILIACAO_LENTE_FALHOU (%d itens, %d falha(s))" % (ITENS, FALHAS))
        return 1

    contrato = carregar_json(arq_contrato)
    consumidor = carregar_json(arq_consumidor)
    politica = carregar_json(arq_politica)
    if contrato is None or consumidor is None or politica is None:
        print("RESULTADO: RECONCILIACAO_LENTE_FALHOU (%d itens, %d falha(s))" % (ITENS, FALHAS))
        return 1

    sql_origem = ler(arq_sql_origem)
    sql_pendentes = ler(arq_sql_pendentes)
    nucleo = ler(arq_nucleo)
    workflow_texto = ler(arq_workflow)
    workflow = json.loads(workflow_texto)
    migration = ler(arq_migration)

    # ------------------------------------------------------------------ A. contrato
    print("--- contrato declarado ---")
    igual("contrato: esquema", contrato.get("esquema"), "1")
    igual("contrato: id", contrato.get("id"), "reconciliation-job")
    confere("contrato: versao presente", bool(contrato.get("versao")), repr(contrato.get("versao")))
    confere("contrato: card citado no titulo", "TRE-W3-E04-T01" in (contrato.get("titulo") or ""))

    comparacoes = contrato.get("comparacoes") or []
    confere("contrato: comparacoes nomeadas (id+tipo+motivo)",
            len(comparacoes) >= 8 and all(c.get("id") and c.get("tipo") and c.get("motivo") for c in comparacoes),
            "n=%d" % len(comparacoes))
    ids = [c.get("id") for c in comparacoes]
    esperados = ["E1", "E2", "E4", "I1", "I2", "I3", "I4", "P1", "P2", "P3", "P4"]
    igual("contrato: as comparacoes declaradas cobrem entidades, IDs cruzados e pendentes",
          sorted(ids), sorted(esperados))
    observacoes = contrato.get("observacoes") or []
    confere("contrato: observacao informativa declarada (nao decide veredito)",
            len(observacoes) == 1 and observacoes[0].get("informativa") is True)
    igual("contrato: ordem do veredito", contrato["veredito"]["ordem"], ["OK", "DIVERGENTE", "INDETERMINADO"])
    confere("contrato: regras de fail-closed declaradas",
            len(contrato.get("regras_de_fail_closed") or {}) >= 5)
    confere("contrato: proibicao de corrigir/escrever declarada",
            "nunca" in json.dumps(contrato.get("proibicoes") or {}, ensure_ascii=False))
    confere("contrato: relatorio declara o que NAO mostrar",
            "request_payload" in (contrato["relatorio"].get("nao_mostrar") or []))
    lacunas = (contrato.get("autoridade", {}).get("fora_do_escopo") or []) + (contrato.get("lacunas_declaradas") or [])
    confere("contrato: lacunas declaradas (nada silenciado)", len(lacunas) >= 4, "n=%d" % len(lacunas))
    confere("contrato: SQL nomeado confere com o disco",
            contrato["sql"]["origem"].endswith("reconciliacao-origem.sql")
            and contrato["sql"]["pendentes"].endswith("reconciliacao-pendentes.sql"))

    leituras = contrato.get("leituras_do_destino") or []
    igual("contrato: duas leituras do destino (existencia + unicidade/ida-e-volta)", len(leituras), 2)
    campos_da_leitura = set()
    for leitura in leituras:
        campos_da_leitura.update(leitura.get("campos") or [])
    confere("contrato: leitura declara campos, filtro, operador, valores, ordem e no",
            all(l.get("campos") and l.get("campo_de_filtro") and l.get("operador")
                and l.get("valores_de") and l.get("ordem") and l.get("no") for l in leituras))
    confere("contrato: leitura declara o mapa dos parametros extras -> declaracao da politica",
            all(isinstance(l.get("parametros_declarados_na_politica"), dict) for l in leituras))
    confere("contrato: uma leitura parte da ponta dos PARCEIROS (id) e outra das ORGANIZACOES",
            sorted(l.get("valores_de") for l in leituras) == ["odoo_partner_id", "organizacoes.id"])

    # ------------------------------------------------------------------ B. SQL somente leitura
    print("--- SQL somente leitura e com linha de cobertura ---")
    proibidos = ["insert", "update", "delete", "create", "alter", "drop", "truncate", "grant",
                 "revoke", "lock", "copy", "vacuum", "analyze", "merge", "call", "do "]
    for nome_arquivo, texto in (("origem", sql_origem), ("pendentes", sql_pendentes)):
        corpo = sem_comentario_sql(texto).lower()
        achados = [p for p in proibidos if re.search(r"(?<![a-z_])%s(?![a-z_])" % p.strip(), corpo)]
        confere("SQL %s: somente leitura (sem %s)" % (nome_arquivo, "/".join(p.strip() for p in proibidos)),
                not achados, "encontrado: %s" % ",".join(achados))
        confere("SQL %s: comeca por WITH/SELECT" % nome_arquivo,
                re.match(r"^\s*(with|select)\b", corpo) is not None)
        confere("SQL %s: devolve linha de cobertura" % nome_arquivo, "'cobertura'" in corpo)
        confere("SQL %s: LIMIT declarado" % nome_arquivo, "limit" in corpo)

    tabelas_origem = set(re.findall(r"sales_intelligence\.([a-z_]+)", sem_comentario_sql(sql_origem).lower()))
    tabelas_pendentes = set(re.findall(r"sales_intelligence\.([a-z_]+)", sem_comentario_sql(sql_pendentes).lower()))
    igual("SQL origem: le so' as tabelas declaradas", sorted(tabelas_origem), ["organizations", "sync_events"])
    igual("SQL pendentes: le so' as tabelas declaradas", sorted(tabelas_pendentes), ["outbox_events", "sync_events"])
    for operacao in contrato.get("operacoes_de_espelho") or []:
        confere("SQL origem: espelha a operacao de espelho declarada %s" % operacao, operacao in sql_origem)
    for status in contrato["status_da_fila"]["entrada"]:
        confere("SQL pendentes: espelha o status de entrada declarado %s" % status, status in sql_pendentes)
    vinculo = contrato["vinculo_da_trilha"]
    confere("SQL origem: espelha a direcao e a operacao da trilha (%s->%s/%s)"
            % (vinculo["direcao"]["source_system"], vinculo["direcao"]["target_system"], vinculo["operacao"]),
            vinculo["direcao"]["source_system"] in sql_origem and vinculo["direcao"]["target_system"] in sql_origem
            and vinculo["operacao"] in sql_origem)
    confere("SQL origem: espelha o status de sucesso da trilha (%s)" % vinculo["status_de_sucesso"],
            vinculo["status_de_sucesso"] in sql_origem)
    confere("SQL origem: espelha o limite do lote (%s)" % contrato["lote"]["limite_de_entidades"],
            str(contrato["lote"]["limite_de_entidades"]) in sql_origem)
    confere("SQL pendentes: espelha o limite da leitura da fila (%s)" % contrato["lote"]["limite_de_eventos"],
            str(contrato["lote"]["limite_de_eventos"]) in sql_pendentes)
    corpo_pendentes = sem_comentario_sql(sql_pendentes)
    confere("SQL pendentes: a cobertura conta a FILA INTEIRA (consulta a tabela, nao o recorte limitado)",
            "count(*) FROM sales_intelligence.outbox_events" in corpo_pendentes
            and "count(*) FROM fila" not in corpo_pendentes)

    # ------------------------------------------------------------------ C. nucleo
    print("--- nucleo: sem literal de decisao ---")
    igual("nucleo: cita o contrato que o governa",
          "n8n/contracts/reconciliation-job.v1.json" in nucleo, True)
    igual("nucleo: cita o card", "TRE-W3-E04-T01" in nucleo, True)
    confere("nucleo: nao importa nada (sandbox de Code node)",
            not re.search(r"^\s*(require|import)\s*\(", sem_comentario_js(nucleo), flags=re.MULTILINE))
    codigo_js = sem_comentario_js(nucleo)
    for vocabulo in (["PENDING", "RETRY", "DEAD_LETTER", "COMPLETED", "REFUSED", "FAILED", "UPSERT"]
                     + (contrato.get("operacoes_de_espelho") or [])):
        confere("nucleo: sem o vocabulo literal %s (vem do contrato)" % vocabulo, vocabulo not in codigo_js)
    # So' os numeros que DECIDEM entram nesta medida; 200 aparece legitimamente na faixa de
    # status HTTP, entao a barreira e' o valor de decisao (a janela e o teto).
    for numero_literal in (str(contrato["janela_de_pendencia_s"]), str(contrato["teto_de_tentativas"])):
        confere("nucleo: sem o numero literal %s (vem do contrato)" % numero_literal,
                re.search(r"(?<![0-9A-Za-z_.])%s(?![0-9A-Za-z_.])" % numero_literal, codigo_js) is None)
    for caminho in ("lote.limite_de_entidades", "lote.limite_de_eventos", "janela_de_pendencia_s",
                    "teto_de_tentativas", "status_da_trilha", "status_da_fila", "leituras_do_destino",
                    "relatorio.tamanho_maximo_do_motivo", "comparacoes"):
        confere("nucleo: le `contrato.%s` (o limiar/vocabulario vem do contrato)" % caminho,
                ("contrato." + caminho) in nucleo or ("contrato && contrato." + caminho) in nucleo)
    confere("nucleo: declara a cobertura da FILA (a leitura da fila tambem tem janela)",
            "fila_leitura_completa" in nucleo and "limite_da_fila_sem_medicao" in nucleo)
    confere("contrato: declara os numeros que decidem (lote, fila, janela, teto, motivo)",
            all(isinstance(contrato.get(k), int) for k in ("janela_de_pendencia_s", "teto_de_tentativas"))
            and isinstance(contrato["lote"].get("limite_de_entidades"), int)
            and isinstance(contrato["lote"].get("limite_de_eventos"), int)
            and isinstance(contrato["relatorio"].get("tamanho_maximo_do_motivo"), int))
    confere("contrato: a linha de resumo declara as DUAS janelas (lote e fila)",
            "fila_" in contrato["relatorio"].get("linha_de_resumo", "")
            and "janela_" in contrato["relatorio"].get("linha_de_resumo", ""))
    confere("contrato: cobertura declarada inclui a janela da fila",
            "fila_leitura_completa" in (contrato["relatorio"].get("cobertura_declarada") or []))

    # ------------------------------------------------------------------ D. workflow derivado
    print("--- workflow: derivado dos artefatos ---")
    try:
        montador = importar_montador(raiz)
        montado = montador.texto_do_workflow(montador.montar())
    except Exception as erro:  # noqa: BLE001 - a lente relata, nao explode
        montado = None
        falhou("montador executa", "%s" % erro)
    confere("workflow em disco == montado agora", montado == workflow_texto)
    igual("workflow: inativo (ADR-005)", workflow.get("active"), False)
    igual("workflow: id estavel do contrato", workflow.get("id"), contrato["workflow"]["id_estavel"])

    nos = workflow.get("nodes") or []
    por_nome = {no.get("name"): no for no in nos}
    declarados = contrato["workflow"]["nos"]
    for chave, nome in declarados.items():
        confere("workflow: no declarado `%s` presente" % nome, nome in por_nome)
    for leitura in leituras:
        confere("workflow: no da leitura `%s` presente" % leitura["no"], leitura["no"] in por_nome)

    # Code nodes: o nucleo byte a byte + contrato + adaptador marcado
    for nome_do_no in (declarados["preparar_leitura"], declarados["avaliar"]):
        no = por_nome.get(nome_do_no) or {}
        codigo = (no.get("parameters") or {}).get("jsCode") or ""
        corte = codigo.find(MARCADOR)
        confere("workflow: Code node `%s` embute o nucleo byte a byte antes do marcador" % nome_do_no,
                corte > 0 and codigo[:corte] == nucleo.rstrip() + "\n\n")
        confere("workflow: Code node `%s` embute o contrato do arquivo" % nome_do_no,
                json.dumps(contrato, ensure_ascii=False, indent=2) in codigo)

    # SQL dos nos Postgres byte a byte
    for nome_do_no, texto_sql in ((declarados["origem"], sql_origem),
                                  (declarados["pendentes"], sql_pendentes)):
        no = por_nome.get(nome_do_no) or {}
        consulta = (no.get("parameters") or {}).get("query") or ""
        confere("workflow: no `%s` embute o SQL byte a byte" % nome_do_no, consulta == texto_sql.rstrip() + "\n")
        confere("workflow: no `%s` e' executeOnce (medicao da rodada, nao passo por item)" % nome_do_no,
                no.get("executeOnce") is True)

    # Conexoes declaradas
    conexoes = workflow.get("connections") or {}
    def alvos(origem):
        return [destino["node"] for destino in (conexoes.get(origem, {}).get("main") or [[]])[0]]
    igual("workflow: agenda -> origem", alvos(declarados["agenda"]), [declarados["origem"]])
    igual("workflow: execucao sob demanda -> origem", alvos(declarados["sob_demanda"]), [declarados["origem"]])
    igual("workflow: origem -> preparar leitura", alvos(declarados["origem"]), [declarados["preparar_leitura"]])
    igual("workflow: preparar -> primeira leitura", alvos(declarados["preparar_leitura"]), [leituras[0]["no"]])
    igual("workflow: primeira -> segunda leitura", alvos(leituras[0]["no"]), [leituras[1]["no"]])
    igual("workflow: segunda leitura -> pendentes", alvos(leituras[1]["no"]), [declarados["pendentes"]])
    igual("workflow: pendentes -> avaliar", alvos(declarados["pendentes"]), [declarados["avaliar"]])
    igual("workflow: avaliar -> relatorio", alvos(declarados["avaliar"]), [declarados["relatorio"]])
    igual("workflow: o relatorio e' a saida (no-op sem saida)", alvos(declarados["relatorio"]), [])

    # Porta unica: somente a operacao de leitura declarada; credencial por id/nome; sem segredo
    on_error_declarado = (contrato["fontes"]["destino"]["envelope_da_requisicao"]
                          .get("no_que_nao_mede", {}).get("onError"))
    confere("contrato: declara o onError do no' que nao mede (a falha vira item, nao aborta a rodada)",
            on_error_declarado == "continueRegularOutput", "onError=%r" % on_error_declarado)
    operacoes_chamadas = set()
    for no in nos:
        parametros = no.get("parameters") or {}
        url = parametros.get("url") or ""
        if no.get("type") == "n8n-nodes-base.httpRequest":
            achados = re.findall(r"/tf/api/v1/([A-Za-z0-9_]+)", url)
            operacoes_chamadas.update(achados)
            confere("workflow: `%s` e' POST pela base de ambiente" % no.get("name"),
                    parametros.get("method") == "POST" and "$env." in url)
            chave_do_involucro = contrato["fontes"]["destino"]["envelope_da_requisicao"]["chave_dos_parametros"]
            confere("workflow: `%s` CONTINUA o fluxo quando a porta unica nao responde (`onError`=%s)"
                    % (no.get("name"), contrato["fontes"]["destino"]["envelope_da_requisicao"]["no_que_nao_mede"]["onError"]),
                    no.get("onError") == contrato["fontes"]["destino"]["envelope_da_requisicao"]["no_que_nao_mede"]["onError"])
            confere("workflow: `%s` nao trata erro de HTTP como erro de execucao (neverError)`" % no.get("name"),
                    "neverError" in (parametros.get("options") or {}).get("response", {}).get("response", {}))
            confere("workflow: `%s` manda o pedido dentro do involucro declarado (`%s`)" % (no.get("name"), chave_do_involucro),
                    ("{" + chave_do_involucro + ":") in (parametros.get("jsonBody") or ""))
            leitura_do_no = next((l for l in leituras if l["no"] == no.get("name")), None)
            if leitura_do_no:
                confere("workflow: `%s` pede os campos declarados da leitura %s" % (no.get("name"), leitura_do_no["id"]),
                        ("pedidos." + leitura_do_no["id"]) in (parametros.get("jsonBody") or ""))
                if leitura_do_no["valores_de"] != "odoo_partner_id":
                    confere("workflow: `%s` referencia o preparador (nao o item corrente, que ja' e' resposta da leitura anterior)"
                            % no.get("name"),
                            declarados["preparar_leitura"] in (parametros.get("jsonBody") or ""))
            credencial = (no.get("credentials") or {}).get("httpHeaderAuth") or {}
            igual("workflow: `%s` aponta a credencial da API por id do contrato" % no.get("name"),
                  credencial.get("id"), contrato["credenciais"]["api"]["id"])
    igual("workflow: a UNICA operacao chamada e' a leitura declarada",
          sorted(operacoes_chamadas), [contrato["fontes"]["destino"]["operacao_de_leitura"]])
    for no in nos:
        if no.get("type") == "n8n-nodes-base.postgres":
            credencial = (no.get("credentials") or {}).get("postgres") or {}
            igual("workflow: `%s` aponta a credencial do banco por id do contrato" % no.get("name"),
                  credencial.get("id"), contrato["credenciais"]["postgres"]["id"])
    confere("workflow: nenhum no' de ESCRITA no banco (set/insert/update)",
            not re.search(r"n8n-nodes-base\.(postgres.*insert|postgres.*update|set|edit)", workflow_texto))
    confere("workflow: nenhum segredo no versionado (Bearer/token/password com valor)",
            not re.search(r"(Bearer\s+[A-Za-z0-9._-]{8,}|password\"?\s*:\s*\"[^\"]+\")", workflow_texto))

    # ------------------------------------------------------------------ E. espelhos
    print("--- espelhos com os contratos vizinhos ---")
    igual("espelho: teto de tentativas == contrato do consumidor",
          contrato["teto_de_tentativas"], consumidor["retry"]["teto_de_tentativas"])
    igual("espelho: vocabulario da trilha == contrato do consumidor",
          contrato["status_da_trilha"]["sucesso"], consumidor["trilha"]["status"]["sucesso"])
    igual("espelho: status de falha transitoria da trilha",
          contrato["status_da_trilha"]["falha_transitoria"], consumidor["trilha"]["status"]["falha_transitoria"])
    igual("espelho: status de recusa da trilha",
          contrato["status_da_trilha"]["recusa"], consumidor["trilha"]["status"]["recusa"])
    igual("espelho: status de entrada da fila == contrato do consumidor",
          contrato["status_da_fila"]["entrada"], consumidor["status"]["entrada"])
    igual("espelho: status de recusa da fila == contrato do consumidor",
          contrato["status_da_fila"]["recusa"], consumidor["status"]["recusa"])
    igual("espelho: direcao da trilha (source/target) == contrato do consumidor",
          [contrato["vinculo_da_trilha"]["direcao"]["source_system"],
           contrato["vinculo_da_trilha"]["direcao"]["target_system"]],
          [consumidor["trilha"]["source_system"], consumidor["trilha"]["target_system"]])
    igual("espelho: operacao da trilha == contrato do consumidor",
          contrato["vinculo_da_trilha"]["operacao"], consumidor["trilha"]["operation"])
    confere("espelho: a chave derivada declarada e' a do contrato do consumidor",
            consumidor["trilha"]["chave_de_idempotencia"]["derivacao"]
            == "outbox:<outbox_events.id>:<event_type>"
            and contrato["vinculo_da_trilha"]["chave_de_idempotencia"]["prefixo"] == "outbox:")

    operacao_de_leitura = next((op for op in politica["operacoes"]
                               if op.get("nome") == contrato["fontes"]["destino"]["operacao_de_leitura"]), None)
    confere("espelho: a operacao de leitura existe na politica da API", operacao_de_leitura is not None)
    if operacao_de_leitura:
        igual("espelho: a operacao da API e' de LEITURA", operacao_de_leitura.get("tipo"), "leitura")
        igual("espelho: a operacao da API e' somente_leitura", operacao_de_leitura.get("somente_leitura"), True)
        igual("espelho: limite do lote == limite_de_registros da operacao",
              contrato["lote"]["limite_de_entidades"], operacao_de_leitura["limite_de_registros"])
        modelo = operacao_de_leitura["modelos"]["res.partner"]
        for leitura in leituras:
            confere("espelho: campo de filtro `%s` declarado na politica da API" % leitura["campo_de_filtro"],
                    leitura["campo_de_filtro"] in modelo["campos_de_filtro"])
            confere("espelho: operador `%s` declarado na politica da API" % leitura["operador"],
                    leitura["operador"] in operacao_de_leitura["operadores_de_dominio"])
            confere("espelho: limite da leitura `%s` cabe no teto da operacao" % leitura["id"],
                    leitura["limite"] <= operacao_de_leitura["limite_de_registros"])
        for campo in sorted(campos_da_leitura):
            confere("espelho: campo lido `%s` declarado na politica da API" % campo,
                    campo in modelo["campos"])
        for leitura in leituras:
            for parametro, declaracao_na_politica in (leitura.get("parametros_declarados_na_politica") or {}).items():
                confere("espelho: a leitura %s pede `%s` e a politica declara `%s` como verdadeiro"
                        % (leitura["id"], parametro, declaracao_na_politica),
                        leitura.get("parametros", {}).get(parametro) is True
                        and operacao_de_leitura.get(declaracao_na_politica) is True,
                        "contrato=%r politica=%r" % (leitura.get("parametros", {}).get(parametro),
                                                    operacao_de_leitura.get(declaracao_na_politica)))
        for forte in contrato["identificadores_fortes"]:
            confere("espelho: identificador forte `%s` e campo lido do destino" % forte["destino"],
                    forte["destino"] in campos_da_leitura)
            confere("espelho: coluna `%s` da origem existe na migration" % forte["origem"],
                    re.search(r"\b%s\b" % re.escape(forte["origem"]), migration) is not None)
    confere("espelho: o vinculo declarado (tf_company_id) e campo lido do destino",
            contrato["campos_do_vinculo"]["id_canonico_no_destino"] in campos_da_leitura)
    confere("espelho: a ponta declarada no PG (odoo_partner_id) existe na migration",
            re.search(r"\bodoo_partner_id\b", migration) is not None)

    if FALHAS == 0:
        print("RESULTADO: RECONCILIACAO_LENTE_OK (%d itens, 0 falhas)" % ITENS)
        return 0
    print("RESULTADO: RECONCILIACAO_LENTE_FALHOU (%d itens, %d falha(s))" % (ITENS, FALHAS))
    return 1


if __name__ == "__main__":
    sys.exit(main())
