#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE da previsao do melhor canal (`previsao-canal-v1`) — card TRE-W9-E03-T01.

Mede contrato, dependencia (funil.py do card W8-E01-T01) e componente SEM banco: derivacao pura sobre
base sintetica conferida A MAO, vocabulario de canal lido do contrato, guardas de ambiente, auditoria
da fonte, pre-condicao "dados multicanal", efetividade por canal, ranking, previsao por organizacao
com desempate declarado, opt-out como bloqueio, integracao com o relatorio do pai, determinismo,
HTML auto-contido e ausencia de PII. Cada item imprime OK/FALHOU.

Uso:
  python3 scripts/agentes/verificar_previsao_canal.py                  # mede o repo
  python3 scripts/agentes/verificar_previsao_canal.py --alvo-dir <dir> # mede uma copia (dentes)
  python3 scripts/agentes/verificar_previsao_canal.py --autoteste      # itens + N mutacoes

Exit 0 = PASS com autoteste 100%; 1 = FALHOU.
"""

import argparse
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_PADRAO = os.path.abspath(os.path.join(AQUI, "..", ".."))
REL_COMPONENTE = os.path.join("hermes", "agentes", "analytics", "previsao_canal.py")
REL_CONTRATO = os.path.join("hermes", "agentes", "analytics", "previsao-canal-v1.json")
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
    mod = carregar_modulo(os.path.join(alvo, REL_COMPONENTE), "previsao_alvo")
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
    """UUID falso, deterministico, de teste (nunca de base real)."""
    return "000000%02d-0000-0000-0000-000000000000" % i


def B(i):
    return "000000b%d-0000-0000-0000-000000000000" % i if i < 10 else "000000bb-0000-0000-0000-000000000000"


# --------------------------------------------------------------------------------------------
# Cenario A — conferido A MAO (os numeros esperados estao nos itens, nao aqui)
#   EMAIL:    abordadas O1,O2,O3,O4 -> atingiram O1,O3 = 2/4 = 50,00%
#   WHATSAPP: abordadas O5,O6,O7    -> atingiram O5,O6,O7 = 3/3 = 100,00%
#   LINKEDIN: abordadas O2,O8       -> base insuficiente (2 < 3), fora do ranking
#   taxa-base (uniao) = 5/8 = 62,50% -> lift EMAIL 0,80x e WHATSAPP 1,60x
# --------------------------------------------------------------------------------------------
INTERACOES_A = [
    # org|canal|direcao|total|respondidas|primeira|ultima
    ("1", "EMAIL", "OUTBOUND", 2, 0), ("1", "EMAIL", "INBOUND", 1, 1),
    ("1", "SMS", "OUTBOUND", 2, 0),                      # canal fora do vocabulario -> lacuna
    ("2", "EMAIL", "OUTBOUND", 1, 0), ("2", "LINKEDIN", "OUTBOUND", 1, 0),
    ("3", "EMAIL", "OUTBOUND", 3, 0), ("3", "EMAIL", "INBOUND", 2, 2),
    ("3", "EMAIL", "INTERNAL", 1, 0),                    # direcao fora do vocabulario -> lacuna
    ("4", "EMAIL", "OUTBOUND", 1, 0),
    ("5", "WHATSAPP", "OUTBOUND", 2, 0), ("5", "WHATSAPP", "INBOUND", 1, 1),
    ("6", "WHATSAPP", "OUTBOUND", 1, 0),
    ("7", "WHATSAPP", "OUTBOUND", 2, 0), ("7", "WHATSAPP", "INBOUND", 1, 0),  # inbound sem classe
    ("8", "LINKEDIN", "OUTBOUND", 1, 0),
    ("9", "EMAIL", "OUTBOUND", 1, 0),                    # organizacao desconhecida -> lacuna
]
# desfecho pela trilha Odoo -> PostgreSQL (o funil le' daqui)
TRILHA_A = {1: ("Reunião", "STAGE_CHANGED"), 3: ("Won", "OPPORTUNITY_WON"),
            5: ("Reunião", "STAGE_CHANGED"), 6: ("Lost", "OPPORTUNITY_LOST"),
            7: ("Proposta", "STAGE_CHANGED")}
# contatos: org -> (contatos, do_not_contact, opt_out_email, opt_out_whatsapp, canais_preferidos)
CONTATOS_A = {
    "1": (2, 0, 0, 0, "WHATSAPP"), "2": (1, 0, 1, 0, ""), "3": (1, 1, 0, 0, ""),
    "5": (1, 0, 0, 1, ""), "6": (1, 0, 0, 0, "EMAIL"), "7": (1, 0, 1, 0, ""), "8": (1, 0, 0, 0, ""),
}

CENARIO_B = {  # empates: EMAIL e WHATSAPP com 1/4 = 25,00% cada
    "interacoes": [
        ("b1", "EMAIL", "OUTBOUND", 1, 0), ("b1", "EMAIL", "INBOUND", 1, 1),
        ("b2", "EMAIL", "OUTBOUND", 1, 0),
        ("b3", "EMAIL", "OUTBOUND", 1, 0), ("b3", "WHATSAPP", "OUTBOUND", 1, 0),
        ("b4", "WHATSAPP", "OUTBOUND", 1, 0), ("b4", "WHATSAPP", "INBOUND", 1, 1),
        ("b5", "EMAIL", "OUTBOUND", 1, 0), ("b5", "WHATSAPP", "OUTBOUND", 1, 0),
        ("b6", "WHATSAPP", "OUTBOUND", 1, 0),
    ],
    "trilha": {"b1": ("Reunião", "STAGE_CHANGED"), "b4": ("Reunião", "STAGE_CHANGED")},
    "contatos": {"b1": (1, 0, 0, 0, ""), "b2": (1, 0, 0, 0, ""), "b3": (1, 0, 0, 0, "WHATSAPP"),
                 "b4": (1, 0, 0, 0, "EMAIL"), "b5": (1, 0, 0, 0, ""), "b6": (1, 0, 0, 0, "")},
}

CENARIO_C = {  # pre-condicao NAO atendida: 1 canal com base (2 orgs) e 2 organizacoes
    "interacoes": [("c1", "EMAIL", "OUTBOUND", 1, 0), ("c2", "EMAIL", "OUTBOUND", 1, 0)],
    "trilha": {"c1": ("Reunião", "STAGE_CHANGED")},
    "contatos": {"c1": (1, 0, 0, 0, ""), "c2": (1, 0, 0, 0, "")},
}


def _linha_interacao(chave, canal, direcao, total, respondidas):
    return "%s|%s|%s|%d|%d|2026-09-01T10:00:00+00|2026-09-02T10:00:00+00" % (
        _org(chave), canal, direcao, total, respondidas)


def _org(chave):
    if isinstance(chave, str) and chave.startswith("b"):
        return B(int(chave[1:]))
    if isinstance(chave, str) and chave.startswith("c"):
        return "000000c0-0000-0000-0000-00000000000%s" % chave[1:]
    return O(int(chave))


def _brutas(cenario):
    interacoes, trilha, contatos = cenario["interacoes"], cenario["trilha"], cenario["contatos"]
    orgs = sorted({_org(k) for k in cenario["orgs"]})
    funil = {
        "BASE_ORGANIZACOES": ["%s|SITE|DISCOVERED" % o for o in orgs],
        "PESQUISA_CONCLUIDA": [], "SINAIS": [], "SCORE_PRIORITY": [], "CONTATO_COM_CANAL": [],
        "INTERACAO_OUTBOUND": sorted({_org(i[0]) for i in interacoes if i[2] == "OUTBOUND"}),
        "INTERACAO_INBOUND_CLASSIFICADA": sorted({_org(i[0]) for i in interacoes
                                                  if i[2] == "INBOUND" and i[4] > 0}),
        "RECOMENDACAO_REUNIAO": [],
        "TRILHA_ESTAGIO": ["%s|%s|%s" % (_org(k), v[0], v[1]) for k, v in sorted(trilha.items())],
    }
    proprias = {
        "INTERACOES_POR_CANAL": [_linha_interacao(*i) for i in interacoes],
        "BLOQUEIOS_POR_ORGANIZACAO": ["%s|%d|%d|%d|%d|%s" % ((_org(k),) + v)
                                      for k, v in sorted(contatos.items())],
    }
    return funil, proprias


def cenario_a():
    return _brutas({"orgs": [str(i) for i in range(1, 9)], "interacoes": INTERACOES_A,
                    "trilha": TRILHA_A, "contatos": CONTATOS_A})


def cenario_b():
    c = dict(CENARIO_B)
    c["orgs"] = ["b%d" % i for i in range(1, 7)]
    return _brutas(c)


def cenario_c():
    c = dict(CENARIO_C)
    c["orgs"] = ["c1", "c2"]
    return _brutas(c)


def relatorio(mod, alvo, cenario=None, contrato=None, dados=None, contrato_funil=None, funil=None):
    if contrato is None or dados is None or contrato_funil is None or funil is None:
        contrato, dados, contrato_funil = carregar_contratos(alvo)
        funil = carregar_modulo(os.path.join(alvo, REL_FUNIL_MOD), "funil_dep")
    brutas_funil, brutas_proprias = cenario or cenario_a()
    return mod.derivar(contrato, dados, contrato_funil, funil, brutas_funil, brutas_proprias,
                       REFERENCIA, "dev", {"desde": None, "ate": None},
                       gerado_em="2026-10-03T00:00:00Z", sha_contrato="a" * 64,
                       sha_contrato_funil="b" * 64)


def _cli(alvo, args):
    comando = [sys.executable, os.path.join(alvo, REL_COMPONENTE), "--raiz", alvo] + args
    proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
    return proc.returncode


def itens(alvo):
    mod = carregar_componente(alvo)
    contrato, dados, contrato_funil = carregar_contratos(alvo)
    funil = carregar_modulo(os.path.join(alvo, REL_FUNIL_MOD), "funil_dep")
    rel = relatorio(mod, alvo, contrato=contrato, dados=dados, contrato_funil=contrato_funil, funil=funil)
    lac = rel["lacunas"]
    canais = {c["canal"]: c for c in rel["por_canal"]}

    # 1 --------------------------------------------------------------------------------------
    item("1. contrato declara o card, a onda e o vocabulario de canal com o bloqueio de cada canal",
         rel["card"] == "TRE-W9-E03-T01" and rel["versao"] == "previsao-canal-v1"
         and rel["parametros"]["vocabulario_de_canal"] == ["EMAIL", "WHATSAPP", "LINKEDIN"]
         and rel["parametros"]["bloqueio_por_canal"] == {"EMAIL": "opt_out_email",
                                                         "WHATSAPP": "opt_out_whatsapp",
                                                         "LINKEDIN": "do_not_contact"},
         rel["parametros"])

    # 2 --------------------------------------------------------------------------------------
    pre = rel["pre_condicao_dados_multicanal"]
    item("2. PRE-CONDICAO 'dados multicanal': atendida com 2 canais suficientes e 8 organizacoes com interacao",
         pre["atendida"] is True and pre["canais_suficientes"] == 2 and pre["canais_com_base"] == 3
         and pre["organizacoes_com_interacao"] == 8 and pre["faltando"] == []
         and pre["exigidos"] == {"minimo_canais_com_base": 2, "minimo_organizacoes_por_canal": 3,
                                 "minimo_organizacoes_com_evidencia": 4}, pre)

    # 3 --------------------------------------------------------------------------------------
    item("3. efetividade por canal conferida A MAO: EMAIL 2/4 = 50,00% e WHATSAPP 3/3 = 100,00%",
         canais["EMAIL"]["organizacoes_abordadas"] == 4 and canais["EMAIL"]["avanco_pct"] == 50.0
         and canais["WHATSAPP"]["organizacoes_abordadas"] == 3 and canais["WHATSAPP"]["avanco_pct"] == 100.0,
         {k: (v["organizacoes_abordadas"], v["avanco_pct"]) for k, v in canais.items()})

    # 4 --------------------------------------------------------------------------------------
    item("4. taxa-base 62,50%, lift EMAIL 0,80x e WHATSAPP 1,60x (canal comparado com a coorte inteira)",
         rel["resumo"]["taxa_base_avanco_pct"] == 62.5 and canais["EMAIL"]["lift_avanco"] == 0.8
         and canais["WHATSAPP"]["lift_avanco"] == 1.6,
         (rel["resumo"]["taxa_base_avanco_pct"], canais["EMAIL"]["lift_avanco"],
          canais["WHATSAPP"]["lift_avanco"]))

    # 5 --------------------------------------------------------------------------------------
    item("5. LINKEDIN fica FORA: base insuficiente (2 organizacoes < 3) declarada, sem entrar no ranking",
         canais["LINKEDIN"]["base_suficiente"] is False
         and canais["LINKEDIN"]["elegivel_para_previsao"] is False
         and lac["canal_sem_base_suficiente"] == ["LINKEDIN"]
         and rel["ranking_de_canais"] == ["WHATSAPP", "EMAIL"],
         (canais["LINKEDIN"]["organizacoes_abordadas"], lac["canal_sem_base_suficiente"],
          rel["ranking_de_canais"]))

    # 6 --------------------------------------------------------------------------------------
    item("6. taxa de resposta e Won/Lost por canal: EMAIL 3/7 = 42,86% com 1 Won; WHATSAPP 1/5 = 20,00% com 1 Lost",
         canais["EMAIL"]["taxa_de_resposta_pct"] == 42.86 and canais["EMAIL"]["ganharam"] == 1
         and canais["EMAIL"]["perderam"] == 0 and canais["WHATSAPP"]["taxa_de_resposta_pct"] == 20.0
         and canais["WHATSAPP"]["ganharam"] == 0 and canais["WHATSAPP"]["perderam"] == 1,
         {k: (v["taxa_de_resposta_pct"], v["ganharam"], v["perderam"]) for k, v in canais.items()})

    # 7 --------------------------------------------------------------------------------------
    item("7. endpoints do canal: WHATSAPP Reunião 100,00% e Won 0,00% (1 Lost); EMAIL Won 25,00% (1 Won)",
         next(e for e in canais["WHATSAPP"]["endpoints"] if e["nome"] == "Reunião")["taxa_pct"] == 100.0
         and next(e for e in canais["WHATSAPP"]["endpoints"] if e["nome"] == "Won")["taxa_pct"] == 0.0
         and next(e for e in canais["EMAIL"]["endpoints"] if e["nome"] == "Won")["taxa_pct"] == 25.0,
         canais["WHATSAPP"]["endpoints"])

    # 8 --------------------------------------------------------------------------------------
    item("8. previsao por organizacao: 7 previsoes, WHATSAPP 6 e EMAIL 1 (canal_mais_efetivo = WHATSAPP 100,00%)",
         rel["resumo"]["organizacoes_com_previsao"] == 7 and rel["resumo"]["previsao_emitida"] is True
         and rel["resumo"]["distribuicao_das_previsoes"] == {"EMAIL": 1, "WHATSAPP": 6}
         and rel["resumo"]["canal_mais_efetivo"] == "WHATSAPP"
         and rel["resumo"]["avanco_do_canal_mais_efetivo_pct"] == 100.0, rel["resumo"])

    # 9 --------------------------------------------------------------------------------------
    prev = {p["organization_id"]: p for p in rel["previsoes"]}
    item("9. previsao por maior taxa de avanco: O1, O4, O6 e O8 -> WHATSAPP com desempate 'taxa_de_avanco'",
         prev[O(1)]["canal_previsto"] == "WHATSAPP" and prev[O(1)]["empate_desfeito_por"] == "taxa_de_avanco"
         and prev[O(4)]["canal_previsto"] == "WHATSAPP" and prev[O(6)]["canal_previsto"] == "WHATSAPP"
         and prev[O(8)]["canal_previsto"] == "WHATSAPP" and prev[O(1)]["preferido"] is True,
         {k[:8]: (v["canal_previsto"], v["empate_desfeito_por"]) for k, v in prev.items()})

    # 10 -------------------------------------------------------------------------------------
    item("10. OPT-OUT E' BLOQUEIO: O2 e O7 (opt_out_email) e O5 (opt_out_whatsapp) tem o canal bloqueado nomeado",
         prev[O(2)]["canais_bloqueados"] == [{"canal": "EMAIL", "motivo": "opt_out_email"}]
         and prev[O(2)]["canal_previsto"] == "WHATSAPP"
         and prev[O(7)]["canal_previsto"] == "WHATSAPP"
         and prev[O(5)]["canal_previsto"] == "EMAIL"
         and prev[O(5)]["canais_bloqueados"] == [{"canal": "WHATSAPP", "motivo": "opt_out_whatsapp"}],
         {k[:8]: (v["canal_previsto"], v["canais_bloqueados"]) for k, v in prev.items()})

    # 11 -------------------------------------------------------------------------------------
    item("11. do_not_contact bloqueia TODOS os canais: O3 sem previsao e com os 3 canais bloqueados nomeados",
         O(3) not in prev and lac["organizacao_sem_canal_elegivel"] == 1
         and lac["canal_bloqueado_por_do_not_contact"] == 3
         and lac["canal_bloqueado_por_opt_out"] == {"opt_out_email": 2, "opt_out_whatsapp": 1}, lac)

    # 12 -------------------------------------------------------------------------------------
    item("12. preferencia NAO vence taxa: O6 prefere EMAIL e e' previsto WHATSAPP (preferencia e' desempate)",
         prev[O(6)]["preferido"] is False and prev[O(6)]["canal_previsto"] == "WHATSAPP"
         and prev[O(1)]["preferido"] is True and prev[O(1)]["empate_desfeito_por"] == "taxa_de_avanco"
         and prev[O(6)]["empate_desfeito_por"] == "taxa_de_avanco", prev[O(6)])

    # 13 -------------------------------------------------------------------------------------
    rel_b = relatorio(mod, alvo, cenario=cenario_b(), contrato=contrato, dados=dados,
                      contrato_funil=contrato_funil, funil=funil)
    prev_b = {p["organization_id"]: p for p in rel_b["previsoes"]}
    desempates = {p["organization_id"]: p["empate_desfeito_por"] for p in rel_b["previsoes"]}
    item("13. DESEMPATE declarado em empate de taxa: resposta propria, interacao propria, preferencia e ordem",
         desempates[B(1)] == "resposta_propria" and desempates[B(2)] == "interacao_propria"
         and desempates[B(3)] == "preferencia_declarada" and desempates[B(4)] == "resposta_propria"
         and desempates[B(5)] == "ordem_do_vocabulario" and desempates[B(6)] == "interacao_propria"
         and prev_b[B(3)]["canal_previsto"] == "WHATSAPP" and prev_b[B(5)]["canal_previsto"] == "EMAIL"
         and prev_b[B(1)]["canal_previsto"] == "EMAIL" and prev_b[B(6)]["canal_previsto"] == "WHATSAPP"
         and rel_b["por_canal"][0]["avanco_pct"] == rel_b["por_canal"][1]["avanco_pct"] == 25.0,
         desempates)

    # 14 -------------------------------------------------------------------------------------
    rel_c = relatorio(mod, alvo, cenario=cenario_c(), contrato=contrato, dados=dados,
                      contrato_funil=contrato_funil, funil=funil)
    item("14. FAIL-CLOSED da pre-condicao: base insuficiente -> previsao_emitida=False, zero previsoes e 'faltando'",
         rel_c["pre_condicao_dados_multicanal"]["atendida"] is False and rel_c["previsoes"] == []
         and rel_c["resumo"]["previsao_emitida"] is False
         and len(rel_c["pre_condicao_dados_multicanal"]["faltando"]) == 2
         and rel_c["lacunas"]["previsao_nao_emitida"] == 1
         and rel_c["por_canal"][0]["avanco_pct"] == 50.0,
         rel_c["pre_condicao_dados_multicanal"])

    # 15 -------------------------------------------------------------------------------------
    item("15. canal fora do vocabulario, organizacao desconhecida, direcao estranha e inbound sem classe viram lacuna",
         lac["canal_fora_do_vocabulario"] == {"SMS": 1}
         and lac["interacao_de_organizacao_desconhecida"] == 1
         and lac["direcao_fora_do_vocabulario"] == 1 and lac["inbound_sem_classificacao"] == 1
         and lac["organizacao_sem_contato"] == 1, lac)

    # 16 -------------------------------------------------------------------------------------
    contrato_dup = copy.deepcopy(contrato)
    contrato_dup["vocabulario_de_canal"]["canais"][2]["nome"] = "EMAIL"
    contrato_bloq = copy.deepcopy(contrato)
    contrato_bloq["vocabulario_de_canal"]["canais"][0]["bloqueio"] = "opt_out_telefone"
    contrato_min = copy.deepcopy(contrato)
    contrato_min["parametros"]["minimo_canais_com_base"] = 9
    dados_sem = copy.deepcopy(dados)
    dados_sem["compliance"]["required_fields"] = ["legal_basis", "source"]
    contrato_ep = copy.deepcopy(contrato)
    contrato_ep["parametros"]["endpoint_principal"]["nivel_minimo"] = 9
    funil_sem = copy.deepcopy(contrato_funil)
    funil_sem["fontes"]["FONTE_INVENTADA"] = {}
    recusas = []
    for rotulo, (c, d, f) in {"canal duplicado": (contrato_dup, dados, contrato_funil),
                              "bloqueio desconhecido": (contrato_bloq, dados, contrato_funil),
                              "minimo incoerente": (contrato_min, dados, contrato_funil),
                              "compliance sem campo": (contrato, dados_sem, contrato_funil),
                              "endpoint divergente do pai": (contrato_ep, dados, contrato_funil),
                              "fonte do pai inventada": (contrato, dados, funil_sem)}.items():
        try:
            mod.validar_contrato(c, d, f)
            recusas.append((rotulo, "ACEITOU"))
        except mod.Recusa as exc:
            recusas.append((rotulo, exc.motivo))
    item("16. contrato incoerente RECUSA antes de ler o banco (canal duplicado, bloqueio, minimo, compliance, "
         "endpoint, fontes do pai)",
         all(motivo != "ACEITOU" for _, motivo in recusas)
         and dict(recusas)["canal duplicado"] == "CANAL_SEM_NOME_OU_DUPLICADO"
         and dict(recusas)["bloqueio desconhecido"] == "BLOQUEIO_DE_CANAL_DESCONHECIDO"
         and dict(recusas)["compliance sem campo"] == "CONTRATO_DE_DADOS_SEM_CAMPO_DE_COMPLIANCE", recusas)

    # 17 -------------------------------------------------------------------------------------
    rel_funil = funil.montar_relatorio(contrato_funil, cenario_a()[0], "dev",
                                       {"desde": None, "ate": None}, gerado_em="2026-10-03T00:00:00Z")
    estagios = {e["nome"]: e for e in rel_funil["estagios"]}
    soma_atingiu = sum(next(e for e in c["endpoints"]
                            if e["nome"] == rel["resumo"]["endpoint_principal"])["atingiram"]
                       for c in rel["por_canal"])
    item("17. INTEGRACAO com o pai: avanco de Reuniao bate com o relatorio do funil e Won/Lost batem no resumo",
         soma_atingiu == estagios["Reunião"]["alcancadas"] == 5
         and sum(c["ganharam"] for c in rel["por_canal"]) == rel_funil["resumo"]["won"] == 1
         and sum(c["perderam"] for c in rel["por_canal"]) == rel_funil["resumo"]["lost"] == 1
         and rel["dependencia"]["funcao_do_desfecho"] == "alcance_por_organizacao"
         and rel["dependencia"]["contrato"] == "funil-v1",
         (soma_atingiu, estagios["Reunião"]["alcancadas"], rel_funil["resumo"]))

    # 18 -------------------------------------------------------------------------------------
    outro = relatorio(mod, alvo, contrato=contrato, dados=dados, contrato_funil=contrato_funil, funil=funil)
    outro["gerado_em"] = "2027-01-01T00:00:00Z"
    outro["referencia_temporal"] = "2027-01-01T00:00:00Z"
    item("18. determinismo: gerado_em e referencia_temporal NAO entram no hash; mesma base -> mesmo hash",
         rel["hash_do_relatorio"] == outro["hash_do_relatorio"] == mod.hash_do_relatorio(rel)
         and len(rel["hash_do_relatorio"]) == 64 and rel["contrato"]["sha256"] == "a" * 64,
         rel["hash_do_relatorio"][:16])

    # 19 -------------------------------------------------------------------------------------
    html = mod.emitir_html(rel)
    item("19. HTML auto-contido (sem http/https/script/link) com pre-condicao, canais, ranking e previsoes",
         "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
         and len(html) > 900 and all(c["canal"] in html for c in rel["por_canal"])
         and "Pre-condicao" in html and "Ranking de canais elegiveis" in html
         and "Previsao por organizacao" in html and "Lacunas medidas" in html, len(html))

    # 20 -------------------------------------------------------------------------------------
    texto = json.dumps(rel, ensure_ascii=False, default=str) + html
    achados = [p for p in ["@", "mailto:", "Empresa Um", "um.test"] if p.lower() in texto.lower()]
    sqls = " ".join(mod.montar_consultas_proprias(contrato, None, None).values()).lower()
    proibidas = [c for c in ("email", "phone", "full_name", "cnpj", "legal_name", "domain")
                 if re.search(r"\b%s\b" % c, sqls)]
    item("20. saida sem PII e SQL das fontes proprias sem coluna de contato direto (so' CONTAGEM e canal)",
         achados == [] and proibidas == [] and sqls.count("count(*)") >= 4, (achados, proibidas))

    # 21 -------------------------------------------------------------------------------------
    problemas = []
    for fid, sql in mod.montar_consultas_proprias(contrato, None, None).items():
        if not sql.upper().lstrip().startswith("SELECT"):
            problemas.append(fid)
    try:
        funil.auditar_fonte({"INTERACOES_POR_CANAL":
                             "SELECT 1; DELETE FROM sales_intelligence.interactions"})
        problemas.append("auditoria_aceitou_escrita")
    except funil.Recusa as exc:
        if exc.motivo != "ESCRITA_NO_CODIGO":
            problemas.append("motivo_inesperado=%s" % exc.motivo)
    item("21. auditoria da fonte: toda consulta propria e' SELECT e a escrita injetada RECUSA (ESCRITA_NO_CODIGO)",
         problemas == [], problemas)

    # 22 -------------------------------------------------------------------------------------
    rc_prod = _cli(alvo, ["--ambiente", "prod"])
    rc_prod_contrato = _cli(alvo, ["--ambiente", "prod", "--contrato", "/tmp/nao-existe.json"])
    rc_sem_porta = _cli(alvo, ["--ambiente", "dev"])
    rc_remota = _cli(alvo, ["--ambiente", "dev", "--porta-banco", "ssh root@10.0.0.1 psql"])
    rc_conferir = _cli(alvo, ["--ambiente", "dev", "--conferir"])
    rc_planejar = _cli(alvo, ["--ambiente", "dev", "--planejar"])
    rc_agora = _cli(alvo, ["--ambiente", "dev", "--porta-banco", "docker exec -i pg-analytics-x psql",
                           "--agora", "ontem"])
    item("22. guardas e modos sem banco: prod RECUSA (4) antes do contrato; dev sem porta e porta remota "
         "RECUSAM (3); --conferir e --planejar rodam (0); janela invalida RECUSA (2)",
         rc_prod == 4 and rc_prod_contrato == 4 and rc_sem_porta == 3 and rc_remota == 3
         and rc_conferir == 0 and rc_planejar == 0 and rc_agora == 2,
         (rc_prod, rc_prod_contrato, rc_sem_porta, rc_remota, rc_conferir, rc_planejar, rc_agora))

    # 23 -------------------------------------------------------------------------------------
    item("23. lacunas declaradas no relatorio (>= 5) e nenhuma delas some da saida",
         len(rel["lacunas_declaradas"]) >= 5 and any("multicanal" in l or "vocabulario" in l or "canal" in l
                                                    for l in rel["lacunas_declaradas"])
         and rel["janela"] == {"desde": None, "ate": None}, rel["lacunas_declaradas"])


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
    {"nome": "d1 bloqueio do EMAIL por opt-out desligado no contrato",
     "arquivo": REL_CONTRATO, "de": '"nome": "EMAIL", "bloqueio": "opt_out_email"',
     "para": '"nome": "EMAIL", "bloqueio": "do_not_contact"'},
    {"nome": "d2 pre-condicao afrouxada para 3 canais (base nao sustenta e nao pode prever)",
     "arquivo": REL_CONTRATO, "de": '"minimo_canais_com_base": 2', "para": '"minimo_canais_com_base": 3'},
    {"nome": "d3 guarda de producao desligada", "arquivo": REL_COMPONENTE,
     "de": 'if args.ambiente == "prod":', "para": "if False:"},
    {"nome": "d4 endpoint principal ausente da lista de endpoints do contrato",
     "arquivo": REL_CONTRATO, "de": '{ "nome": "Reunião", "nivel_minimo": 6 },',
     "para": '{ "nome": "Engajamento", "nivel_minimo": 5 },'},
    {"nome": "d5 qualquer organizacao com evidencia 'atingiu' (nivel ignorado)",
     "arquivo": REL_COMPONENTE, "de": "return nivel is not None and nivel >= int(ep[\"nivel_minimo\"])",
     "para": "return nivel is not None"},
    {"nome": "d6 ranking invertido (pior canal como mais efetivo)", "arquivo": REL_COMPONENTE,
     "de": 'ranking = sorted(elegiveis, key=lambda c: (-(c["avanco_pct"] or 0.0), c["canal"]))',
     "para": 'ranking = sorted(elegiveis, key=lambda c: ((c["avanco_pct"] or 0.0), c["canal"]))'},
    {"nome": "d7 bloqueio ignorado na escolha do canal (previsao passa por opt-out)",
     "arquivo": REL_COMPONENTE,
     "de": 'candidatos = [c for c in elegiveis if c["canal"] not in set_bloqueados]',
     "para": "candidatos = [c for c in elegiveis]"},
    {"nome": "d8 nome de canal duplicado no vocabulario do contrato",
     "arquivo": REL_CONTRATO, "de": '{ "nome": "LINKEDIN", "bloqueio": "do_not_contact",',
     "para": '{ "nome": "EMAIL", "bloqueio": "do_not_contact",'},
]


def _rodar_verificador(alvo):
    proc = subprocess.run([sys.executable, os.path.join(AQUI, "verificar_previsao_canal.py"),
                           "--alvo-dir", alvo], capture_output=True, text=True, timeout=300)
    nomes = []
    for linha in proc.stdout.splitlines():
        if linha.startswith("VERIFICADOR_PREVISAO_CANAL_FALHOU"):
            try:
                nomes = json.loads(linha[len("VERIFICADOR_PREVISAO_CANAL_FALHOU "):])
            except json.JSONDecodeError:
                nomes = []
    return proc.returncode, nomes


def autoteste(alvo, falhas_base):
    print("== autoteste: %d mutacoes, cada uma tem de REPROVAR um item que o alvo limpo nao reprova"
          % len(MUTACOES))
    detectadas = 0
    for mut in MUTACOES:
        with tempfile.TemporaryDirectory(prefix="dente-canal-") as tmp:
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
    parser = argparse.ArgumentParser(description="Verificador offline da previsao do melhor canal (TRE-W9-E03-T01)")
    parser.add_argument("--alvo-dir", default=RAIZ_PADRAO)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    alvo = os.path.abspath(args.alvo_dir)

    try:
        itens(alvo)
    except Exception as exc:  # noqa: BLE001 — item isolado: excecao nao tratada e' FALHA, nunca silencio
        item("0. suite roda ate' o fim sem excecao nao tratada", False,
             "%s: %s" % (type(exc).__name__, str(exc)[:200]))
    autoteste_ok = True
    if args.autoteste:
        autoteste_ok = autoteste(alvo, set(FALHAS_ITENS))

    print("RESULTADO: %s (%d itens, %d falhas)" % ("PASS" if FALHAS == 0 else "FALHOU", OK, FALHAS))
    if FALHAS == 0 and autoteste_ok:
        print("VERIFICADOR_PREVISAO_CANAL_PASS")
        return 0
    print("VERIFICADOR_PREVISAO_CANAL_FALHOU " + json.dumps(FALHAS_ITENS, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
