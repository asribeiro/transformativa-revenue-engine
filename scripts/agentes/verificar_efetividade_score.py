#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE da efetividade do score (`efetividade-score-v1`) — card TRE-W8-E03-T01.

Mede contrato, dependencia (funil.py do card pai) e componente SEM banco: derivacao pura com base
sintetica conferida A MAO, faixas e pesos lidos do Data Contract, guardas de ambiente, auditoria da
fonte, adesao a formula, quartis por componente, integracao com o relatorio do pai, determinismo,
HTML auto-contido e ausencia de PII. Cada item imprime OK/FALHOU.

Uso:
  python3 scripts/agentes/verificar_efetividade_score.py                  # mede o repo
  python3 scripts/agentes/verificar_efetividade_score.py --alvo-dir <dir> # mede uma copia (dentes)
  python3 scripts/agentes/verificar_efetividade_score.py --autoteste      # itens + N mutacoes

Exit 0 = PASS com autoteste 100%; 1 = FALHOU.
"""

import argparse
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_PADRAO = os.path.abspath(os.path.join(AQUI, "..", ".."))
REL_COMPONENTE = os.path.join("hermes", "agentes", "analytics", "efetividade_score.py")
REL_CONTRATO = os.path.join("hermes", "agentes", "analytics", "efetividade-score-v1.json")
REL_FUNIL_MOD = os.path.join("hermes", "agentes", "analytics", "funil.py")
REL_FUNIL_CONTRATO = os.path.join("hermes", "agentes", "analytics", "funil-v1.json")
REL_DADOS = os.path.join("docs", "data", "data_contract_v1.json")
REFERENCIA = datetime(2026, 10, 3, 0, 0, 0, tzinfo=timezone.utc)

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


def carregar_modulo(caminho, nome):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def carregar_componente(alvo):
    mod = carregar_modulo(os.path.join(alvo, REL_COMPONENTE), "efetividade_alvo")
    mod.CONTRATO_PADRAO = os.path.join(alvo, REL_CONTRATO)
    mod.FUNIL_PADRAO = os.path.join(alvo, REL_FUNIL_MOD)
    mod.CONTRATO_FUNIL_PADRAO = os.path.join(alvo, REL_FUNIL_CONTRATO)
    return mod


def carregar_contratos(alvo):
    with open(os.path.join(alvo, REL_CONTRATO), "r", encoding="utf-8") as fh:
        contrato = json.load(fh)
    with open(os.path.join(alvo, REL_DADOS), "r", encoding="utf-8") as fh:
        dados = json.load(fh)
    with open(os.path.join(alvo, REL_FUNIL_CONTRATO), "r", encoding="utf-8") as fh:
        contrato_funil = json.load(fh)
    return contrato, dados, contrato_funil


def O(i):
    """UUID falso, deterministico, de teste (nunca de base real). Nome curto nos itens: O<i>."""
    return "000000%02d-0000-0000-0000-000000000000" % i


# --------------------------------------------------------------------------------------------
# Base sintetica conferida A MAO (os numeros esperados estao nos itens, nao aqui)
# --------------------------------------------------------------------------------------------
COMPONENTES_POR_ORG = {
    1: ("100", "100", "100", "100"), 2: ("90", "90", "90", "90"), 3: ("80", "80", "80", "80"),
    4: ("85", "70", "60", "50"), 5: ("70", "60", "50", "5"), 6: ("60", "50", "40", "30"),
    7: ("50", "40", "30", "20"), 8: ("40", "30", "20", "10"), 9: ("80", "80", "80", "80"),
    14: ("55", "50", "55", None), 15: ("66", "60", "88", "66"),
}
# PRIORITY armazenado: (valor, versao, vencimento)
PRIORITY_POR_ORG = {
    1: ("100.00", "v1", ""), 2: ("90.00", "v1", ""), 3: ("80.00", "v1", ""), 4: ("70.75", "v1", ""),
    5: ("55.50", "v1", ""), 6: ("49.00", "v1", ""), 7: ("39.00", "v1", ""), 8: ("29.00", "v1", ""),
    9: ("79.00", "v1", ""),  # divergente: componentes somam 80.00
    11: ("70.00", "", ""),   # sem versao -> lacuna
    12: ("95.00", "v1", "2020-01-01T00:00:00+00"),  # vencido -> lacuna
    13: ("150.00", "v1", ""),  # fora da escala -> lacuna
    14: ("55.00", "v1", ""), 15: ("66.00", "v1", ""),
}
# desfecho (alcance do funil) por fonte declarada no contrato do pai
TRILHA = {1: "Proposta", 2: "Reunião", 3: "Lost", 5: "Won", 6: "Engajamento", 14: "Reunião"}
OPERACAO = {3: "OPPORTUNITY_LOST", 5: "OPPORTUNITY_WON"}
INBOUND = [6]        # Engajamento (INTERACAO_INBOUND_CLASSIFICADA)
OUTBOUND = [4]       # Abordagem iniciada (INTERACAO_OUTBOUND)
TODAS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
COORTE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 14, 15]  # com PRIORITY valido (11)


def fixture():
    """Linhas cruas no formato de cada fonte, prontas para `derivar` (sem banco)."""
    funil = {
        "BASE_ORGANIZACOES": ["%s|SITE|DISCOVERED" % O(i) for i in TODAS],
        "PESQUISA_CONCLUIDA": [], "SINAIS": [],
        # o SCORE_PRIORITY do pai conta PRIORITY com versao (por isso todo mundo com versao >= Qualificado)
        "SCORE_PRIORITY": [O(i) for i, (_, versao, _) in PRIORITY_POR_ORG.items() if versao],
        "CONTATO_COM_CANAL": [],
        "INTERACAO_OUTBOUND": [O(i) for i in OUTBOUND],
        "INTERACAO_INBOUND_CLASSIFICADA": [O(i) for i in INBOUND],
        "RECOMENDACAO_REUNIAO": [],
        "TRILHA_ESTAGIO": ["%s|%s|%s" % (O(i), TRILHA[i], OPERACAO.get(i, "STAGE_CHANGED"))
                           for i in sorted(TRILHA)],
    }
    prioridade = []
    for i, (valor, versao, vence) in sorted(PRIORITY_POR_ORG.items()):
        prioridade.append("%s|%s|%s|%s|2026-09-30T12:00:00+00|%s|1"
                          % (O(i), "score-%02d" % i, valor, versao, vence))
    componentes = []
    for i, (icp, af, bs, dq) in sorted(COMPONENTES_POR_ORG.items()):
        versao = "v2" if i == 15 else "v1"
        for tipo, valor in (("ICP", icp), ("AUTOMATION_FIT", af), ("BUYING_SIGNAL", bs), ("DATA_QUALITY", dq)):
            if valor is None:
                continue
            componentes.append("%s|%s|%s.00|%s|" % (O(i), tipo, valor, versao))
    return funil, {"PRIORITY_ULTIMO": prioridade, "COMPONENTES_ULTIMOS": componentes}


def relatorio(mod, alvo):
    contrato, dados, contrato_funil = carregar_contratos(alvo)
    funil, _ = mod.carregar_dependencia_funil(os.path.join(alvo, REL_FUNIL_MOD),
                                              os.path.join(alvo, REL_FUNIL_CONTRATO))
    brutas_funil, brutas_proprias = fixture()
    return mod.derivar(contrato, dados, contrato_funil, funil, brutas_funil, brutas_proprias, REFERENCIA,
                       "dev", {"desde": None, "ate": None},
                       gerado_em="2026-10-03T00:00:00Z", sha_contrato="a" * 64,
                       sha_contrato_funil="b" * 64), contrato, dados, contrato_funil, funil, \
        brutas_funil, brutas_proprias


def faixa_de(rel, nome):
    return next(f for f in rel["efetividade_por_faixa"] if f["faixa"] == nome)


def endpoint_de(faixa, nome):
    return next(e for e in faixa["endpoints"] if e["nome"] == nome)


def perto(a, b, tol=0.01):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# --------------------------------------------------------------------------------------------
def itens(alvo):
    mod = carregar_componente(alvo)
    contrato, dados, contrato_funil = carregar_contratos(alvo)
    rel, _, _, _, funil_mod, brutas_funil, _ = relatorio(mod, alvo)

    # 1 --------------------------------------------------------------------------------------
    item("1. contrato legivel, versao e card declarados",
         contrato.get("versao") == "efetividade-score-v1" and contrato.get("card") == "TRE-W8-E03-T01",
         "versao=%s card=%s" % (contrato.get("versao"), contrato.get("card")))

    # 2 --------------------------------------------------------------------------------------
    try:
        validado = mod.validar_contrato(contrato, dados, contrato_funil)
        ok2 = (validado["faixas"] == ["A+", "A", "B", "C", "Nurture"]
               and validado["pesos"]["ICP"] == Decimal("0.35")
               and validado["pesos"]["DATA_QUALITY"] == Decimal("0.10")
               and validado["escala"] == {"minimo": "0.0", "maximo": "100.0"})
        detalhe = validado["faixas"]
    except Exception as exc:  # noqa: BLE001
        ok2, detalhe = False, "%s: %s" % (type(exc).__name__, str(exc)[:100])
    item("2. faixas e pesos LIDOS do Data Contract (A+ 90-100 ... Nurture 0-49.99; 0.35/0.30/0.25/0.10)",
         ok2, detalhe)

    # 3 --------------------------------------------------------------------------------------
    mut_peso = copy.deepcopy(dados)
    mut_peso["scores"]["priority_weights"]["ICP"] = 0.40
    mut_faixa = copy.deepcopy(dados)
    for faixa in mut_faixa["scores"]["tiers"]:
        if faixa["name"] == "C":
            faixa["min"] = 51.0
    motivos = []
    for nome, mutado in (("peso", mut_peso), ("faixa", mut_faixa)):
        try:
            mod.validar_contrato(contrato, mutado, contrato_funil)
            motivos.append("%s=NAO_RECUSOU" % nome)
        except mod.Recusa as exc:
            motivos.append("%s=%s" % (nome, exc.motivo))
    item("3. contrato de dados incoerente RECUSA (peso que nao soma 1 -> PESOS_NAO_SOMAM_1; "
         "faixa com lacuna -> FAIXAS_NAO_COBREM_ESCALA)",
         motivos == ["peso=PESOS_NAO_SOMAM_1", "faixa=FAIXAS_NAO_COBREM_ESCALA"], motivos)

    # 4 --------------------------------------------------------------------------------------
    rc = {
        "prod": _cli(alvo, ["--ambiente", "prod", "--saida", "/tmp/nao-deve-existir"]),
        # prod RECUSA ANTES de ler contrato/base: com contrato inexistente ainda tem de sair 4
        "prod_antes_do_contrato": _cli(alvo, ["--ambiente", "prod", "--contrato", "/tmp/nao-existe.json"]),
        "remoto": _cli(alvo, ["--ambiente", "dev", "--porta-banco", "ssh root@10.0.0.1 psql"]),
        "sem_porta": _cli(alvo, ["--ambiente", "dev"]),
        "homolog_sem_confirmo": _cli(alvo, ["--ambiente", "homolog", "--porta-banco",
                                            "docker exec -i pg-analytics-x psql"]),
    }
    item("4. guardas: prod RECUSA por desenho (4) ANTES de ler contrato/base; dev recusa porta remota "
         "e ausente (3); homolog sem --confirmo RECUSA (3)",
         rc == {"prod": 4, "prod_antes_do_contrato": 4, "remoto": 3, "sem_porta": 3,
                "homolog_sem_confirmo": 3},
         json.dumps(rc))

    # 5 --------------------------------------------------------------------------------------
    consultas = mod.montar_consultas_proprias(contrato, None, None)
    funil_dep = relatorio(mod, alvo)[4]
    so_select = all(sql.strip().upper().startswith("SELECT") for sql in consultas.values()) \
        and all(not funil_dep.VERBOS_DE_ESCRITA.search(sql) for sql in consultas.values())
    recusou = None
    try:
        funil_dep.auditar_fonte({"FONTE_MUTADA":
                                 "SELECT 1; INSERT INTO sales_intelligence.scores VALUES (1)"})
    except Exception as exc:  # noqa: BLE001
        recusou = getattr(exc, "motivo", type(exc).__name__)
    item("5. auditoria da fonte: fontes proprias sao SO' SELECT e verbo de escrita e' detectado (ESCRITA_NO_CODIGO)",
         so_select and recusou == "ESCRITA_NO_CODIGO" and sorted(consultas) ==
         ["COMPONENTES_ULTIMOS", "PRIORITY_ULTIMO"],
         "so_select=%s recusou=%s fontes=%s" % (so_select, recusou, sorted(consultas)))

    # 6 --------------------------------------------------------------------------------------
    _, _, _, _, funil_dep, _, _ = relatorio(mod, alvo)
    contrato_funil_ruim = copy.deepcopy(contrato_funil)
    contrato_funil_ruim["versao"] = "funil-v2"
    motivo_dep = None
    with tempfile.TemporaryDirectory(prefix="dep-efet-") as tmp:
        copia = os.path.join(tmp, "funil-v2.json")
        with open(copia, "w", encoding="utf-8") as fh:
            json.dump(contrato_funil_ruim, fh)
        try:
            mod.carregar_dependencia_funil(os.path.join(alvo, REL_FUNIL_MOD), copia)
        except mod.Recusa as exc:
            motivo_dep = exc.motivo
    item("6. dependencia do pai conferida: funil-v1 com `alcance_por_organizacao`; versao diferente RECUSA",
         hasattr(funil_dep, "alcance_por_organizacao") and motivo_dep == "DEPENDENCIA_VERSAO_INCOMPATIVEL",
         "tem_funcao=%s motivo=%s" % (hasattr(funil_dep, "alcance_por_organizacao"), motivo_dep))

    # 7 --------------------------------------------------------------------------------------
    cob = rel["cobertura"]
    lac = rel["lacunas"]
    item("7. cobertura e lacunas nomeadas conferidas a mao (11 de 15 = 73.33%; ausente/sem versao/vencida/fora da escala = 1 cada)",
         cob["organizacoes"] == 15 and cob["com_priority_valido"] == 11 and perto(cob["taxa_pct"], 73.33)
         and lac["prioridade_ausente"] == 1 and lac["prioridade_sem_versao"] == 1
         and lac["prioridade_vencida"] == 1 and lac["prioridade_fora_da_escala"] == 1
         and lac["historico_ignorado"] == {"organizacoes": 0, "linhas": 0}, (cob, lac))

    # 8 --------------------------------------------------------------------------------------
    taxas = {f["faixa"]: f["avanco_pct"] for f in rel["efetividade_por_faixa"]}
    base = rel["resumo"]["taxa_base_avanco_pct"]
    item("8. taxa de avanco por faixa conferida a mao (A+ 100 / A 100 / B 0 / C 100 / Nurture 0; base 45.45%)",
         taxas == {"A+": 100.0, "A": 100.0, "B": 0.0, "C": 100.0, "Nurture": 0.0} and perto(base, 45.45),
         (taxas, base))

    # 9 --------------------------------------------------------------------------------------
    c = faixa_de(rel, "C")
    a = faixa_de(rel, "A")
    b = faixa_de(rel, "B")
    item("9. Won/Lost e taxa de vitoria por faixa (C: 1 won/0 lost = 100%; A: 0/1 = 0%; faixa sem terminal = null)",
         c["ganharam"] == 1 and c["perderam"] == 0 and perto(c["taxa_de_vitoria_pct"], 100.0)
         and a["ganharam"] == 0 and a["perderam"] == 1 and perto(a["taxa_de_vitoria_pct"], 0.0)
         and b["taxa_de_vitoria_pct"] is None, (c["ganharam"], c["perderam"], a["taxa_de_vitoria_pct"]))

    # 10 -------------------------------------------------------------------------------------
    aplus = faixa_de(rel, "A+")
    item("10. endpoints do tier conferidos a mao (A+: Engajamento 100%, Proposta 50%, Won 0%; "
         "B: Engajamento 0% e Proposta 0%)",
         perto(endpoint_de(aplus, "Engajamento")["taxa_pct"], 100.0)
         and perto(endpoint_de(aplus, "Proposta")["taxa_pct"], 50.0)
         and perto(endpoint_de(aplus, "Won")["taxa_pct"], 0.0)
         and perto(endpoint_de(b, "Engajamento")["taxa_pct"], 0.0)
         and perto(endpoint_de(b, "Proposta")["taxa_pct"], 0.0),
         [e["taxa_pct"] for e in aplus["endpoints"]])

    # 11 -------------------------------------------------------------------------------------
    item("11. lift contra a taxa-base (A+ 2.20x no endpoint principal; B 0.00x) e lift null quando a base e' zero",
         perto(aplus["lift_avanco"], 2.2) and perto(b["lift_avanco"], 0.0)
         and mod._lift(50.0, 0) is None, (aplus["lift_avanco"], b["lift_avanco"]))

    # 12 -------------------------------------------------------------------------------------
    resumo = rel["resumo"]
    item("12. monotonicidade e' ACHADO medido: B (0%) abaixo de C (100%) -> monotonico=False com a violacao nomeada",
         resumo["monotonico"] is False and len(resumo["violacoes"]) == 1
         and resumo["violacoes"][0]["obtido"] == "B=0.0 < C=100.0", resumo["violacoes"])

    # 13 -------------------------------------------------------------------------------------
    item("13. melhor/pior faixa, amplitude e base insuficiente declarada (nenhuma faixa com 5 organizacoes)",
         resumo["melhor_faixa"] == "A+" and perto(resumo["melhor_faixa_avanco_pct"], 100.0)
         and resumo["pior_faixa"] == "B" and perto(resumo["pior_faixa_avanco_pct"], 0.0)
         and perto(resumo["amplitude_pct"], 100.0)
         and resumo["base_suficiente_para_conclusao"] is False
         and resumo["faixas_sem_base_suficiente"] == ["A+", "A", "B", "C", "Nurture"],
         (resumo["melhor_faixa"], resumo["pior_faixa"], resumo["faixas_sem_base_suficiente"]))

    # 14 -------------------------------------------------------------------------------------
    limites = {f["name"]: (Decimal(str(f["min"])), Decimal(str(f["max"]))) for f in dados["scores"]["tiers"]}
    ordem = [f["name"] for f in sorted(dados["scores"]["tiers"], key=lambda f: f["min"])]
    bordas = {"90": "A+", "89.99": "A", "80": "A", "79.99": "B", "65": "B", "64.99": "C",
              "50": "C", "49.99": "Nurture", "0": "Nurture", "100": "A+"}
    obtido = {v: mod.faixa_do_valor(limites, ordem, Decimal(v)) for v in bordas}
    item("14. faixa derivada do valor na borda (90->A+, 89.99->A, 65->B, 64.99->C, 50->C, 49.99->Nurture, 100->A+)",
         obtido == bordas, obtido)

    # 15 -------------------------------------------------------------------------------------
    adesao = rel["adesao_a_formula"]
    item("15. adesao a formula: 9 comparaveis, 8 conformes, 1 divergente (1.00 de desvio), taxa 88.89%",
         adesao["comparaveis"] == 9 and adesao["conformes"] == 8 and adesao["divergentes"] == 1
         and perto(adesao["taxa_de_conformidade_pct"], 88.89)
         and adesao["desvio_maximo"] == "1.00" and adesao["tolerancia"] == "0.01"
         and adesao["pesos_usados"]["ICP"] == "0.35" and len(adesao["exemplos_de_divergencia"]) == 1,
         adesao)

    # 16 -------------------------------------------------------------------------------------
    item("16. fora da conta da adesao vai para lacuna nomeada (DATA_QUALITY ausente = 1; versao divergente = 1)",
         lac["componente_ausente"] == {"DATA_QUALITY": 1} and lac["versoes_divergentes"] == 1
         and lac["componente_vencido"] == {} and lac["componente_sem_versao"] == {}, lac)

    # 17 -------------------------------------------------------------------------------------
    por_comp = {c["score_type"]: c for c in rel["por_componente"]}
    icp = por_comp["ICP"]
    dq = por_comp["DATA_QUALITY"]
    item("17. quartis por POSTO (Q1 = maiores valores): ICP Q1={O1,O2,O4} Q4={O7,O8}; DQ Q1={O1,O2,O3} Q4={O8,O5} com lift 2.0x",
         icp["quartis"][0]["organizacoes"] == 3 and icp["comparaveis"] == 11
         and perto(icp["quartis"][0]["taxa_pct"], 66.67) and perto(icp["quartis"][3]["taxa_pct"], 0.0)
         and dq["comparaveis"] == 10 and perto(dq["quartis"][0]["taxa_pct"], 100.0)
         and perto(dq["quartis"][3]["taxa_pct"], 50.0) and perto(dq["lift_q1_vs_q4"], 2.0),
         (icp["comparaveis"], [q["taxa_pct"] for q in icp["quartis"]],
          [q["taxa_pct"] for q in dq["quartis"]], dq["lift_q1_vs_q4"]))

    # 18 -------------------------------------------------------------------------------------
    rel_funil = funil_mod.montar_relatorio(contrato_funil, brutas_funil, "dev",
                                           {"desde": None, "ate": None}, gerado_em="2026-10-03T00:00:00Z")
    estagios = {e["nome"]: e for e in rel_funil["estagios"]}
    soma_tiers = sum(f["organizacoes"] for f in rel["efetividade_por_faixa"])
    soma_avanco = sum(endpoint_de(f, rel["resumo"]["endpoint_principal"])["atingiram"]
                      for f in rel["efetividade_por_faixa"])
    soma_won = sum(f["ganharam"] for f in rel["efetividade_por_faixa"])
    soma_lost = sum(f["perderam"] for f in rel["efetividade_por_faixa"])
    item("18. INTEGRACAO com o pai: coorte fecha com as faixas; Won/Lost e o avanco batem com o relatorio do funil "
         "(sem segunda verdade para o alcance)",
         soma_tiers == rel["cobertura"]["com_priority_valido"] == 11
         and soma_avanco == estagios["Reunião"]["alcancadas"] == 5
         and soma_won == rel_funil["resumo"]["won"] == 1
         and soma_lost == rel_funil["resumo"]["lost"] == 1
         and rel["dependencia"]["funcao_do_desfecho"] == "alcance_por_organizacao",
         (soma_tiers, soma_avanco, estagios["Reunião"]["alcancadas"], soma_won, soma_lost))

    # 19 -------------------------------------------------------------------------------------
    contrato2 = copy.deepcopy(contrato)
    outro, _, _, _, _, _, _ = relatorio(mod, alvo)
    outro["gerado_em"] = "2027-01-01T00:00:00Z"
    outro["referencia_temporal"] = "2027-01-01T00:00:00Z"
    item("19. determinismo: gerado_em e referencia_temporal NAO entram no hash; mesma base -> mesmo hash",
         rel["hash_do_relatorio"] == outro["hash_do_relatorio"] == mod.hash_do_relatorio(rel)
         and len(rel["hash_do_relatorio"]) == 64 and rel["contrato"]["sha256"] == "a" * 64,
         rel["hash_do_relatorio"][:16])

    # 20 -------------------------------------------------------------------------------------
    html = mod.emitir_html(rel)
    item("20. HTML auto-contido (sem http/https/script/link) com faixas, adesao e lacunas",
         "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
         and len(html) > 800 and all(f["faixa"] in html for f in rel["efetividade_por_faixa"])
         and "Adesão à fórmula" in html and "Lacunas medidas" in html,
         len(html))

    # 21 -------------------------------------------------------------------------------------
    texto = json.dumps(rel, ensure_ascii=False, default=str) + html
    achados = [p for p in ["@", "mailto:", "Org Um", "Contato Um"] if p.lower() in texto.lower()]
    sqls = " ".join(mod.montar_consultas_proprias(contrato, None, None).values()).lower()
    colunas_pii = [c for c in ("email", "phone", "full_name", "whatsapp", "cnpj") if c in sqls]
    item("21. saida sem PII (nenhum e-mail/nome) e nenhuma coluna de PII no SQL das fontes proprias",
         achados == [] and colunas_pii == [], (achados, colunas_pii))

    # 22 -------------------------------------------------------------------------------------
    rc_conferir = _cli(alvo, ["--ambiente", "dev", "--conferir"])
    rc_planejar = _cli(alvo, ["--ambiente", "dev", "--planejar"])
    rc_agora = _cli(alvo, ["--ambiente", "dev", "--porta-banco", "docker exec -i pg-analytics-x psql",
                           "--agora", "ontem"])
    item("22. --conferir e --planejar rodam sem banco (0) e janela invalida RECUSA (2) antes de tocar o banco",
         rc_conferir == 0 and rc_planejar == 0 and rc_agora == 2,
         (rc_conferir, rc_planejar, rc_agora))

    return contrato


def _cli(alvo, args):
    comando = [sys.executable, os.path.join(alvo, REL_COMPONENTE), "--raiz", alvo] + args
    proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
    return proc.returncode


# --------------------------------------------------------------------------------------------
def _aplicar_mutacao(origem, destino, mutacao):
    for rel_dir in (os.path.dirname(REL_COMPONENTE), os.path.dirname(REL_DADOS)):
        os.makedirs(os.path.join(destino, rel_dir), exist_ok=True)
    for rel in (REL_COMPONENTE, REL_CONTRATO, REL_FUNIL_MOD, REL_FUNIL_CONTRATO, REL_DADOS):
        shutil.copy2(os.path.join(origem, rel), os.path.join(destino, rel))
    alvo_arquivo = os.path.join(destino, mutacao["arquivo"])
    with open(alvo_arquivo, "r", encoding="utf-8") as fh:
        texto = fh.read()
    if mutacao["de"] not in texto:
        return False
    with open(alvo_arquivo, "w", encoding="utf-8") as fh:
        fh.write(texto.replace(mutacao["de"], mutacao["para"]))
    return True


MUTACOES = [
    {"nome": "d1 peso do Data Contract alterado (0.35 -> 0.40)", "arquivo": REL_DADOS,
     "de": '"ICP": 0.35', "para": '"ICP": 0.4'},
    {"nome": "d2 faixa do Data Contract com lacuna (C comeca em 51)", "arquivo": REL_DADOS,
     "de": '"name": "C", "min": 50.0', "para": '"name": "C", "min": 51.0'},
    {"nome": "d3 guarda de producao desligada", "arquivo": REL_COMPONENTE,
     "de": 'if args.ambiente == "prod":', "para": "if False:"},
    {"nome": "d4 endpoint principal rebaixado para Engajamento no contrato", "arquivo": REL_CONTRATO,
     "de": '"nome": "Reunião",\n      "nivel_minimo": 6', "para": '"nome": "Engajamento",\n      "nivel_minimo": 5'},
    {"nome": "d5 alcance por organizacao do pai quebrado (nivel sempre null)", "arquivo": REL_FUNIL_MOD,
     "de": '"nivel": max(niveis) if niveis else None,', "para": '"nivel": None,'},
    {"nome": "d6 adesao a formula sempre conforme (tolerancia ignorada)", "arquivo": REL_COMPONENTE,
     "de": "if delta <= tolerancia:", "para": "if True:"},
    {"nome": "d7 faixa derivada sem limite superior", "arquivo": REL_COMPONENTE,
     "de": "if minimo <= valor <= maximo:", "para": "if minimo <= valor:"},
    {"nome": "d8 quartis invertidos (Q1 = menores valores)", "arquivo": REL_COMPONENTE,
     "de": "elegiveis.sort(key=lambda par: (-par[0], par[1]))",
     "para": "elegiveis.sort(key=lambda par: (par[0], par[1]))"},
]


def _rodar_verificador(alvo):
    proc = subprocess.run([sys.executable, os.path.join(AQUI, "verificar_efetividade_score.py"),
                           "--alvo-dir", alvo], capture_output=True, text=True, timeout=300)
    nomes = []
    for linha in proc.stdout.splitlines():
        if linha.startswith("VERIFICADOR_EFETIVIDADE_FALHOU"):
            try:
                nomes = json.loads(linha[len("VERIFICADOR_EFETIVIDADE_FALHOU "):])
            except json.JSONDecodeError:
                nomes = []
    return proc.returncode, nomes


def autoteste(alvo, falhas_base):
    print("== autoteste: %d mutacoes, cada uma tem de REPROVAR um item que o alvo limpo nao reprova"
          % len(MUTACOES))
    detectadas = 0
    for mut in MUTACOES:
        with tempfile.TemporaryDirectory(prefix="dente-efetividade-") as tmp:
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
    parser = argparse.ArgumentParser(description="Verificador offline da efetividade do score (TRE-W8-E03-T01)")
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
        print("VERIFICADOR_EFETIVIDADE_PASS")
        return 0
    print("VERIFICADOR_EFETIVIDADE_FALHOU " + json.dumps(FALHAS_ITENS, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
