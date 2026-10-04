#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE do custo de agentes (`custo-agentes-v1`) — card TRE-W8-E05-T01 (W8 / Analytics).

Mede o contrato e o componente SEM banco: derivacao pura, guardas de ambiente, auditoria da fonte,
contrato x DDL congelada, determinismo, HTML/CSV auto-contidos, ausencia de PII. Cada item imprime
OK/FALHOU.

Uso:
  python3 scripts/agentes/verificar_custo_agentes.py                    # mede o repo (raiz = 2 niveis acima)
  python3 scripts/agentes/verificar_custo_agentes.py --alvo-dir <dir>    # mede uma copia (usado pelos dentes)
  python3 scripts/agentes/verificar_custo_agentes.py --autoteste         # itens + N mutacoes, cada uma tem de REPROVAR

Exit 0 = PASS com autoteste 100%; 1 = FALHOU.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_PADRAO = os.path.abspath(os.path.join(AQUI, "..", ".."))
REL_COMPONENTE = os.path.join("hermes", "agentes", "analytics", "custo_agentes.py")
REL_CONTRATO = os.path.join("hermes", "agentes", "analytics", "custo-agentes-v1.json")
REL_DADOS = os.path.join("docs", "data", "data_contract_v1.json")
REL_DDL = os.path.join("db", "migrations", "0001_sales_intelligence_v1.sql")

OK = 0
FALHAS = 0
FALHAS_ITENS = []


def item(nome, condicao, detalhe=""):
    global OK, FALHAS
    if condicao:
        print("OK    %s" % nome)
        OK += 1
    else:
        print("FALHOU %s %s" % (nome, detalhe))
        FALHAS += 1
        FALHAS_ITENS.append(nome)


def carregar_componente(alvo):
    caminho = os.path.join(alvo, REL_COMPONENTE)
    spec = importlib.util.spec_from_file_location("custo_agentes_alvo", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONTRATO_PADRAO = os.path.join(alvo, REL_CONTRATO)
    return mod


def carregar_contratos(alvo):
    with open(os.path.join(alvo, REL_CONTRATO), "r", encoding="utf-8") as fh:
        contrato = json.load(fh)
    with open(os.path.join(alvo, REL_DADOS), "r", encoding="utf-8") as fh:
        dados = json.load(fh)
    return contrato, dados


# -------------------------------------------------------------------------------------------- #
# Fixture: 16 execucoes cobrindo todos os desfechos, os tres jeitos de nao ter custo e as
# bordas de latencia. Os numeros esperados abaixo foram conferidos A MAO e sao reafirmados
# no aceite (sobre a base semeada em PostgreSQL) — a conta e' a mesma nos dois lugares.
# Campos: agente|papel|versao|workflow|wf_versao|modelo|disparado|organizacao|inicio|fim|status|tokens_in|tokens_out|custo
# -------------------------------------------------------------------------------------------- #
FIXTURE = [
    ("research", "research", "v1", "pesquisa", "v1", "gpt-x", "job", "ORG_A",
     "2026-10-01T10:00:00", "2026-10-01T10:01:00", "COMPLETED", "", "", ""),
    ("research", "research", "v1", "pesquisa", "v1", "gpt-x", "job", "ORG_B",
     "2026-10-01T11:00:00", "2026-10-01T11:02:00", "FAILED", "", "", ""),
    ("research", "research", "v1", "pesquisa", "", "", "", "",
     "", "", "", "", "", ""),
    ("outreach", "outreach", "v1", "abordagem", "v1", "gpt-x", "job", "ORG_A",
     "2026-10-02T09:00:00", "2026-10-02T09:01:00", "COMPLETED", "100", "50", "0.0012"),
    ("outreach", "outreach", "v1", "abordagem", "v1", "gpt-x", "job", "ORG_A",
     "2026-10-02T10:00:00", "2026-10-02T10:01:30", "COMPLETED", "120", "60", "0.0018"),
    ("outreach", "outreach", "v1", "abordagem", "v1", "gpt-x", "job", "ORG_B",
     "2026-10-02T11:00:00", "2026-10-02T11:00:30", "COMPLETED", "80", "40", "0.0006"),
    ("outreach", "outreach", "v1", "abordagem", "v1", "gpt-x", "job", "ORG_C",
     "2026-10-02T12:00:00", "2026-10-02T12:00:20", "REJECTED", "10", "5", "0.0006"),
    ("scout", "scout", "v2", "prospeccao", "v1", "gpt-mini", "", "ORG_A",
     "2026-10-01T08:00:00", "2026-10-01T08:00:10", "COMPLETED", "200", "100", "0.0020"),
    ("scout", "scout", "v2", "prospeccao", "v1", "gpt-mini", "", "ORG_B",
     "2026-10-01T10:00:00", "2026-10-01T10:00:20", "COMPLETED", "200", "100", "0.0020"),
    ("scout", "scout", "v2", "prospeccao", "v1", "gpt-mini", "", "ORG_C",
     "2026-10-01T12:00:00", "2026-10-01T12:00:40", "COMPLETED", "200", "100", "0.0020"),
    ("scout", "scout", "v2", "prospeccao", "v1", "gpt-mini", "", "ORG_D",
     "2026-10-01T13:10:00", "2026-10-01T13:00:00", "COMPLETED", "200", "100", "0.0020"),
    ("icp_score", "scoring", "v1", "icp", "v1", "gpt-x", "job", "ORG_A",
     "2026-10-02T14:00:00", "2026-10-02T14:05:00", "COMPLETED", "500", "300", "0.0090"),
    ("icp_score", "scoring", "v1", "icp", "v1", "gpt-x", "job", "ORG_B",
     "2026-10-02T15:00:00", "2026-10-02T15:00:05", "COMPLETED", "10", "2", "0"),
    ("nba", "scoring", "v1", "nba", "v1", "gpt-x", "job", "ORG_A",
     "2026-10-02T16:00:00", "2026-10-02T16:00:45", "TIMEOUT", "10", "10", "0.0012"),
    ("nba", "scoring", "v1", "nba", "v1", "gpt-x", "job", "ORG_B",
     "2026-10-02T17:00:00", "2026-10-02T17:00:10", "REVIEW_REQUIRED", "10", "10", "-0.5"),
    ("", "", "", "", "", "", "", "",
     "2026-10-02T18:00:00", "2026-10-02T18:00:01", "COMPLETED", "", "", ""),
]


def linhas_fixture():
    return ["|".join(campos) for campos in FIXTURE]


def relatorio_fixture(mod, contrato, limite=3, gerado_em="2026-10-03T00:00:00Z"):
    return mod.montar_relatorio(contrato, {"EXECUCOES_DE_AGENTE": linhas_fixture()}, "dev",
                                {"desde": None, "ate": None}, limite_amostra=limite, gerado_em=gerado_em,
                                sha_contrato="b" * 64)


def _grupo(rel, chave, valor):
    campo = {"por_agente": "agente", "por_modelo": "modelo", "por_workflow": "workflow"}[chave]
    for g in rel[chave]:
        if g[campo] == valor:
            return g
    return None


def _cli(alvo, args):
    comando = [sys.executable, os.path.join(alvo, REL_COMPONENTE), "--raiz", alvo,
               "--contrato", os.path.join(alvo, REL_CONTRATO)] + args
    proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


# -------------------------------------------------------------------------------------------- #
def itens(alvo):
    mod = carregar_componente(alvo)
    contrato, dados = carregar_contratos(alvo)
    with open(os.path.join(alvo, REL_DDL), "r", encoding="utf-8") as fh:
        ddl = fh.read()

    # 1 -------------------------------------------------------------------------------------
    item("1. contrato legivel, versao e card declarados",
         contrato.get("versao") == "custo-agentes-v1" and contrato.get("card") == "TRE-W8-E05-T01",
         "versao=%s card=%s" % (contrato.get("versao"), contrato.get("card")))

    # 2 -------------------------------------------------------------------------------------
    colunas = contrato["colunas_de_leitura"]["selecionadas"]
    na_ddl = mod.colunas_da_ddl(ddl)
    item("2. toda coluna lida existe no CREATE TABLE de agent_runs da DDL congelada",
         all(c in na_ddl for c in colunas) and len(na_ddl) >= 20,
         "faltando=%s" % [c for c in colunas if c not in na_ddl])

    # 3 -------------------------------------------------------------------------------------
    classes = contrato["vocabulario_status"]["classes"]
    vistas = [v for valores in classes.values() for v in valores]
    item("3. as 4 classes particionam o vocabulario medido dos irmaos (sem sobra, sem repeticao)",
         set(classes) == {"CONCLUIDA", "FALHA", "RECUSADA", "REVISAO"}
         and sorted(vistas) == ["COMPLETED", "FAILED", "REJECTED", "REVIEW_REQUIRED"],
         str(classes))

    # 4 -------------------------------------------------------------------------------------
    proibidas = contrato["colunas_de_leitura"]["proibidas_no_select"]
    consultas = mod.montar_consultas(contrato)
    sql = consultas["EXECUCOES_DE_AGENTE"]
    cabeca = sql.split(" FROM ")[0]
    import re as _re
    vazamento = [c for c in proibidas if _re.search(r"\b%s\b" % c, cabeca, _re.IGNORECASE)]
    item("4. input/output/error/correlation_id/id/created_at NAO sao selecionados (nem citados no SELECT)",
         not (set(proibidas) & set(colunas)) and not vazamento, "vazamento=%s" % vazamento)

    # 5 -------------------------------------------------------------------------------------
    achados = mod.auditar_sql(sql)
    item("5. a fonte e' UMA e e' SELECT puro (sem verbo de escrita, sem `*`, com as 14 colunas)",
         set(consultas) == set(contrato["fontes"]) and not achados
         and sql.count("|| '|' ||") == 13 and "*" not in sql
         and all(_re.search(r"\b%s\b" % c, sql) for c in colunas),
         "achados=%s campos=%d" % (achados, sql.count("|| '|' ||") + 1))

    # 6 -------------------------------------------------------------------------------------
    rel = relatorio_fixture(mod, contrato)
    r = rel["resumo"]
    ok6 = (r["runs"] == 16 and r["concluidas"] == 11 and r["falhas"] == 1 and r["recusadas"] == 1
           and r["revisao"] == 1 and r["sem_status"] == 2 and r["classificadas"] == 14
           and r["taxa_de_falha"] == 0.0714 and r["taxa_de_recusa"] == 0.0714)
    item("6. resumo conferido a mao (16 execucoes: 11 concluidas, 1 falha, 1 recusa, 1 revisao, 2 sem status)",
         ok6, json.dumps(r, ensure_ascii=False)[:220])

    # 7 -------------------------------------------------------------------------------------
    research = _grupo(rel, "por_agente", "research")
    item("7. NULO NAO E' ZERO: agente sem custo declarado tem custo_total 0.000000, media null e 3 sem custo",
         research["custo_total"] == "0.000000" and research["custo_medio_por_execucao"] is None
         and research["runs_sem_custo"] == 3 and research["custo_por_execucao_concluida"] is None,
         json.dumps({k: research[k] for k in ("custo_total", "custo_medio_por_execucao",
                                              "runs_sem_custo", "custo_por_execucao_concluida")}))

    # 8 -------------------------------------------------------------------------------------
    outreach = _grupo(rel, "por_agente", "outreach")
    ok8 = (outreach["runs"] == 4 and outreach["concluidas"] == 3 and outreach["recusadas"] == 1
           and outreach["falhas"] == 0 and outreach["taxa_de_recusa"] == 0.25
           and outreach["custo_total"] == "0.004200" and outreach["custo_medio_por_execucao"] == "0.001050"
           and outreach["custo_por_execucao_concluida"] == "0.001400"
           and outreach["tokens_totais"] == 465 and outreach["organizacoes"] == 3)
    item("8. por_agente conferido a mao (outreach 4 execucoes: custo 0.004200, 0.001050 por execucao, 0.001400 por sucesso)",
         ok8, json.dumps(outreach, ensure_ascii=False)[:220])

    # 9 -------------------------------------------------------------------------------------
    nba = _grupo(rel, "por_agente", "nba")
    item("9. status fora do vocabulario NAO vira desfecho (TIMEOUT fica em lacuna; nba nao tem falha)",
         nba["sem_status"] == 1 and nba["falhas"] == 0 and nba["concluidas"] == 0
         and nba["revisao"] == 1 and nba["classificadas"] == 1
         and rel["lacunas"]["status_fora_do_vocabulario"] == {"TIMEOUT": 1},
         json.dumps({"nba": nba["sem_status"], "lac": rel["lacunas"]["status_fora_do_vocabulario"]}))

    # 10 ------------------------------------------------------------------------------------
    item("10. recusa declarada NAO e' falha: REJECTED conta em recusadas e a taxa de falha do outreach fica 0.0",
         outreach["taxa_de_falha"] == 0.0 and outreach["recusadas"] == 1 and r["recusadas"] == 1,
         "%s %s" % (outreach["taxa_de_falha"], outreach["recusadas"]))

    # 11 ------------------------------------------------------------------------------------
    scout = _grupo(rel, "por_agente", "scout")
    ok11 = (scout["latencia_media_s"] == 23.33 and scout["latencia_mediana_s"] == 20.0
            and scout["latencia_p95_s"] == 40.0 and scout["runs_sem_latencia"] == 1
            and r["latencia_media_s"] == 57.93 and r["latencia_mediana_s"] == 30.0
            and r["latencia_p95_s"] == 300.0 and r["runs_sem_latencia"] == 2
            and rel["lacunas"]["latencia_invertida"] == 1 and rel["lacunas"]["latencia_incompleta"] == 1)
    item("11. latencia conferida a mao; invertida e incompleta ficam FORA (e em lacuna)",
         ok11, "scout=%s/%s/%s resumo=%s/%s/%s" % (scout["latencia_media_s"], scout["latencia_mediana_s"],
                                                   scout["latencia_p95_s"], r["latencia_media_s"],
                                                   r["latencia_mediana_s"], r["latencia_p95_s"]))

    # 12 ------------------------------------------------------------------------------------
    item("12. custo negativo NAO entra na soma (nba total 0.001200) e e' contado como lacuna",
         nba["custo_total"] == "0.001200" and rel["lacunas"]["runs_custo_negativo"] == 1
         and nba["custo_medio_por_execucao"] == "0.001200",
         "%s %s" % (nba["custo_total"], rel["lacunas"]["runs_custo_negativo"]))

    # 13 ------------------------------------------------------------------------------------
    icp = _grupo(rel, "por_agente", "icp_score")
    item("13. custo ZERO declarado conta como declarado (nao vira ausencia) e e' medido",
         icp["runs_com_custo"] == 2 and rel["lacunas"]["runs_custo_zero"] == 1
         and icp["custo_total"] == "0.009000" and icp["custo_medio_por_execucao"] == "0.004500",
         "%s %s" % (icp["custo_total"], rel["lacunas"]["runs_custo_zero"]))

    # 14 ------------------------------------------------------------------------------------
    item("14. tokens somados so' quando os DOIS lados existem (research fica com 3 sem token e soma 0)",
         research["runs_sem_tokens"] == 3 and research["tokens_totais"] == 0
         and r["tokens_input"] == 1640 and r["tokens_output"] == 877 and r["tokens_totais"] == 2517,
         "%s/%s/%s" % (r["tokens_input"], r["tokens_output"], r["tokens_totais"]))

    # 15 ------------------------------------------------------------------------------------
    texto = json.dumps(rel, ensure_ascii=False) + json.dumps(rel["por_agente"], ensure_ascii=False)
    item("15. organizacoes contadas distintas (4) e o UUID NUNCA vai para a saida",
         r["organizacoes"] == 4 and "ORG_A" not in texto and "organization_id" not in texto,
         "orgs=%s" % r["organizacoes"])

    # 16 ------------------------------------------------------------------------------------
    modelos = sorted(g["modelo"] for g in rel["por_modelo"])
    fluxos = sorted(g["workflow"] for g in rel["por_workflow"])
    item("16. por_modelo e por_workflow agrupam, e execucao sem chave NAO vira grupo",
         modelos == ["gpt-mini", "gpt-x"] and fluxos == ["abordagem", "icp", "nba", "pesquisa", "prospeccao"]
         and rel["lacunas"]["runs_sem_modelo"] == 2 and rel["lacunas"]["runs_sem_workflow"] == 1
         and rel["lacunas"]["runs_sem_agente"] == 1,
         "%s %s" % (modelos, fluxos))

    # 17 ------------------------------------------------------------------------------------
    ranking = rel["ranking"]
    item("17. ranking coroa so' quem declara custo COMPLETO e tem amostra (scout 0.002000); os outros ficam FORA",
         ranking["vencedor"] == "scout" and ranking["custo_por_execucao_concluida"] == "0.002000"
         and ranking["agentes_fora_do_ranking"] == ["icp_score", "nba", "research"],
         json.dumps(ranking, ensure_ascii=False)[:200])

    # 18 ------------------------------------------------------------------------------------
    pequena = mod.montar_relatorio(contrato, {"EXECUCOES_DE_AGENTE": linhas_fixture()[:8]}, "dev",
                                   {"desde": None, "ate": None}, limite_amostra=9,
                                   gerado_em="2026-10-03T00:00:00Z", sha_contrato="b" * 64)
    item("18. amostra insuficiente NAO coroa ninguem (motivo AMOSTRA_INSUFICIENTE ou CUSTO_NAO_DECLARADO_EM_PARTE)",
         pequena["ranking"]["vencedor"] is None
         and pequena["ranking"]["motivo"] in ("AMOSTRA_INSUFICIENTE", "CUSTO_NAO_DECLARADO_EM_PARTE"),
         pequena["ranking"]["motivo"])

    # 19 ------------------------------------------------------------------------------------
    sem_custo = mod.montar_relatorio(contrato, {"EXECUCOES_DE_AGENTE": linhas_fixture()[:3]}, "dev",
                                     {"desde": None, "ate": None}, limite_amostra=1,
                                     gerado_em="2026-10-03T00:00:00Z", sha_contrato="b" * 64)
    item("19. nenhum agente declara custo -> vencedor null com motivo SEM_CUSTO_DECLARADO",
         sem_custo["ranking"]["vencedor"] is None and sem_custo["ranking"]["motivo"] == "SEM_CUSTO_DECLARADO",
         sem_custo["ranking"]["motivo"])

    # 20 ------------------------------------------------------------------------------------
    vazio = mod.montar_relatorio(contrato, {"EXECUCOES_DE_AGENTE": []}, "dev",
                                 {"desde": None, "ate": None}, gerado_em="2026-10-03T00:00:00Z",
                                 sha_contrato="b" * 64)
    item("20. recorte vazio: veredito SEM_EXECUCOES, somas 0 e medias null (nunca 0.0 fingindo que mediu)",
         vazio["veredito"] == "SEM_EXECUCOES" and vazio["resumo"]["runs"] == 0
         and vazio["resumo"]["custo_total"] == "0.000000"
         and vazio["resumo"]["latencia_media_s"] is None
         and vazio["ranking"]["motivo"] == "SEM_EXECUCOES"
         and vazio["por_agente"] == [])

    # 21 ------------------------------------------------------------------------------------
    outro = relatorio_fixture(mod, contrato, gerado_em="2030-01-01T00:00:00Z")
    item("21. determinismo: mesma base -> mesmo hash; gerado_em nao entra no hash",
         rel["hash_do_relatorio"] == outro["hash_do_relatorio"] and len(rel["hash_do_relatorio"]) == 64
         and rel["gerado_em"] != outro["gerado_em"])

    # 22 ------------------------------------------------------------------------------------
    html = mod.emitir_html(rel, contrato.get("lacunas_declaradas"))
    csv = mod.emitir_csv(rel)
    item("22. HTML auto-contido (sem recurso externo) e CSV com uma linha por agente",
         "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
         and "src=" not in html and "Custo de agentes" in html and "scout" in html
         and len(html) > 800 and len(csv.splitlines()) == len(rel["por_agente"]) + 1,
         "html=%d csv=%d" % (len(html), len(csv.splitlines())))

    # 23 ------------------------------------------------------------------------------------
    padroes = [_re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), _re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}"),
               _re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"), _re.compile(r"\+?\d{2}[\s-]9?\d{4}[\s-]?\d{4}")]
    achados_pii = [p.pattern for p in padroes if p.search(html) or p.search(csv) or p.search(texto)]
    item("23. saida sem PII (nenhum padrao de e-mail/CPF/CNPJ/telefone)", not achados_pii, str(achados_pii))

    # 24 ------------------------------------------------------------------------------------
    rc_plan, _ = _cli(alvo, ["--ambiente", "dev", "--planejar"])
    rc_conf, out_conf = _cli(alvo, ["--ambiente", "dev", "--conferir"])
    item("24. --planejar e --conferir funcionam sem banco (exit 0) e a conferencia cita a tabela",
         rc_plan == 0 and rc_conf == 0 and "CUSTO_AGENTES_CONFERIR_OK" in out_conf
         and "sales_intelligence.agent_runs" in out_conf,
         "plan=%s conf=%s %s" % (rc_plan, rc_conf, out_conf.strip()[:80]))

    # 25 ------------------------------------------------------------------------------------
    rc_prod, _ = _cli(alvo, ["--ambiente", "prod", "--saida", "/tmp/nao-deve-existir"])
    rc_remoto, out_remoto = _cli(alvo, ["--ambiente", "dev", "--porta-banco", "ssh root@10.0.0.1 psql"])
    rc_janela, _ = _cli(alvo, ["--ambiente", "dev", "--porta-banco",
                               "docker exec -i pg-custo-acc psql", "--desde", "ontem"])
    item("25. guardas de ambiente: prod RECUSA (exit 4), porta remota RECUSA em dev (exit 3), janela invalida exit 2",
         rc_prod == 4 and rc_remoto == 3 and "BANCO_NAO_E_DEV" in out_remoto and rc_janela == 2,
         "prod=%s remoto=%s janela=%s" % (rc_prod, rc_remoto, rc_janela))

    # 26 ------------------------------------------------------------------------------------
    rc_formato = None
    try:
        mod.extrair_execucoes(["a|b|c"], contrato)
    except mod.Recusa as exc:
        rc_formato = exc.motivo
    item("26. linha fora do formato de 14 campos RECUSA (fail-closed, exit 3)",
         rc_formato == "LINHA_FORA_DO_FORMATO", str(rc_formato))

    # 27 ------------------------------------------------------------------------------------
    item("27. as 5 lacunas declaradas existem no contrato e viajam no relatorio",
         len(contrato.get("lacunas_declaradas") or []) == 5
         and rel["lacunas_declaradas"] == contrato["lacunas_declaradas"])

    # 28 ------------------------------------------------------------------------------------
    texto_ddl_quebrado = ddl.replace("estimated_cost NUMERIC(12,6),", "custo NUMERIC(12,6),")
    motivo_ddl = None
    try:
        mod.validar_contrato(contrato, dados, texto_ddl_quebrado)
    except mod.Recusa as exc:
        motivo_ddl = exc.motivo
    item("28. contrato x DDL: coluna de medicao ausente na DDL RECUSA (fail-closed, exit 3)",
         motivo_ddl in ("CONTRATO_DIVERGE_DA_DDL", "DDL_SEM_COLUNA_DE_MEDICAO"), str(motivo_ddl))

    # 29 ------------------------------------------------------------------------------------
    motivo_prod = motivo_container = None
    try:
        mod.validar_ambiente("prod")
    except mod.Recusa as exc:
        motivo_prod = (exc.motivo, exc.codigo)
    try:
        mod.validar_ambiente("dev", "docker exec -i pg-outro-banco psql")
    except mod.Recusa as exc:
        motivo_container = (exc.motivo, exc.codigo)
    item("29. a GUARDA em si recusa: prod exit 4 e container de dev fora da lista exit 3 (unidade, alem do CLI)",
         motivo_prod == ("PRODUCAO_RECUSADA", 4)
         and motivo_container == ("CONTAINER_NAO_LOCAL_DE_DEV", 3),
         "%s %s" % (motivo_prod, motivo_container))

    # 30 ------------------------------------------------------------------------------------
    sem_carimbo = relatorio_fixture(mod, contrato)
    sem_carimbo.pop("gerado_em", None)
    try:
        html_sem = mod.emitir_html(sem_carimbo, contrato.get("lacunas_declaradas"))
        ok30 = "(sem carimbo)" in html_sem
    except KeyError:
        ok30 = False
        html_sem = ""
    item("30. relatorio SEM carimbo (o padrao) ainda renderiza o HTML e o hash nao muda "
         "(defeito medido pelo aceite antes da entrega)",
         ok30 and mod.hash_do_relatorio(sem_carimbo) == rel["hash_do_relatorio"])

    return contrato


# -------------------------------------------------------------------------------------------- #
def _aplicar_mutacao(origem, destino, mutacao):
    for rel in (REL_COMPONENTE, REL_CONTRATO, REL_DADOS, REL_DDL):
        destino_arquivo = os.path.join(destino, rel)
        os.makedirs(os.path.dirname(destino_arquivo), exist_ok=True)
        shutil.copy2(os.path.join(origem, rel), destino_arquivo)
    alvo_arquivo = os.path.join(destino, REL_CONTRATO if mutacao["arquivo"] == "contrato" else REL_COMPONENTE)
    with open(alvo_arquivo, "r", encoding="utf-8") as fh:
        texto = fh.read()
    if mutacao["de"] not in texto:
        return False
    texto = texto.replace(mutacao["de"], mutacao["para"])
    with open(alvo_arquivo, "w", encoding="utf-8") as fh:
        fh.write(texto)
    return True


MUTACOES = [
    {"nome": "d1 status em duas classes no contrato", "arquivo": "contrato",
     "de": '"FALHA": ["FAILED"]', "para": '"FALHA": ["FAILED", "REVIEW_REQUIRED"]'},
    {"nome": "d2 coluna declarada que nao existe na DDL", "arquivo": "contrato",
     "de": '"agent_version",', "para": '"agent_version_x",'},
    {"nome": "d3 verbo de escrita injetado no SQL", "arquivo": "componente",
     "de": 'sql = "SELECT " + " || \'|\' || ".join(expressao)',
     "para": ('sql = "SELECT \'x\'; INSERT INTO sales_intelligence.agent_runs (id) VALUES '
              '(gen_random_uuid()); SELECT " + " || \'|\' || ".join(expressao)')},
    {"nome": "d4 guarda de producao desligada", "arquivo": "componente",
     "de": 'if ambiente == "prod":', "para": 'if False:'},
    {"nome": "d5 guarda de container local de dev desligada", "arquivo": "componente",
     "de": "if not CONTAINERS_LOCAIS_DEV.match(m.group(1)):", "para": "if False:"},
    {"nome": "d6 nulo virando zero no custo", "arquivo": "componente",
     "de": ('        if e["custo"] is None:\n            m["runs_sem_custo"] += 1\n        else:\n'
            '            custo_total += e["custo"]\n            m["runs_com_custo"] += 1'),
     "para": ('        if e["custo"] is None:\n            custo_total += Decimal("0")\n'
              '            m["runs_com_custo"] += 1\n        else:\n'
              '            custo_total += e["custo"]\n            m["runs_com_custo"] += 1')},
    {"nome": "d7 ranking aceitando quem nao declara custo", "arquivo": "componente",
     "de": 'if v["runs_sem_custo"] == 0 and v["runs_com_custo"] > 0}', "para": "}"},
    {"nome": "d8 status fora do vocabulario virando falha", "arquivo": "componente",
     "de": '            m["sem_status"] += 1', "para": '            m["falhas"] += 1'},
]


def _rodar_verificador(alvo):
    proc = subprocess.run([sys.executable, os.path.join(AQUI, "verificar_custo_agentes.py"), "--alvo-dir", alvo],
                          capture_output=True, text=True, timeout=240)
    nomes = []
    for linha in proc.stdout.splitlines():
        if linha.startswith("VERIFICADOR_CUSTO_AGENTES_FALHOU"):
            try:
                nomes = json.loads(linha[len("VERIFICADOR_CUSTO_AGENTES_FALHOU "):])
            except json.JSONDecodeError:
                nomes = []
    return proc.returncode, nomes


def autoteste(alvo, falhas_base):
    print("== autoteste: %d mutacoes, cada uma tem de REPROVAR um item que o alvo limpo nao reprova =="
          % len(MUTACOES))
    detectadas = 0
    for mut in MUTACOES:
        with tempfile.TemporaryDirectory(prefix="dente-custo-") as tmp:
            copia = os.path.join(tmp, "repo")
            if not _aplicar_mutacao(alvo, copia, mut):
                print("FALHOU %s (mutacao NAO aplicada — ancora de texto mudou)" % mut["nome"])
                continue
            rc, nomes = _rodar_verificador(copia)
            novos = [n for n in nomes if n not in falhas_base]
            if rc != 0 and novos:
                detectadas += 1
                print("OK    %s -> REPROVOU (%s)" % (mut["nome"], novos[0][:80]))
            else:
                print("FALHOU %s -> passou com a mutacao aplicada (buraco na suite)" % mut["nome"])
    print("AUTOTESTE %d/%d mutacoes detectadas" % (detectadas, len(MUTACOES)))
    return detectadas == len(MUTACOES)


def main():
    parser = argparse.ArgumentParser(description="Verificador offline do custo de agentes (TRE-W8-E05-T01)")
    parser.add_argument("--alvo-dir", default=RAIZ_PADRAO)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    alvo = os.path.abspath(args.alvo_dir)

    try:
        itens(alvo)
    except Exception as exc:  # noqa: BLE001 — item isolado: excecao nao tratada e' FALHA, nunca silencio
        item("0. suite roda ate' o fim sem excecao nao tratada", False,
             "%s: %s" % (type(exc).__name__, str(exc)[:160]))
    autoteste_ok = True
    if args.autoteste:
        autoteste_ok = autoteste(alvo, set(FALHAS_ITENS))

    print("RESULTADO: %s (%d itens, %d falhas)" % ("PASS" if FALHAS == 0 else "FALHOU", OK, FALHAS))
    if FALHAS == 0 and autoteste_ok:
        print("VERIFICADOR_CUSTO_AGENTES_PASS")
        return 0
    print("VERIFICADOR_CUSTO_AGENTES_FALHOU " + json.dumps(FALHAS_ITENS, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
