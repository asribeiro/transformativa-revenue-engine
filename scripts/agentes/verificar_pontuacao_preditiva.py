#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline da pontuacao preditiva (`pontuacao-preditiva-v1`) — card TRE-W9-E02-T01.

Roda SEM banco: contrato, aritmetica pura (binos, PAVA, Brier, log-loss, faixas), guardas de
dependencia (relatorio da calibracao) e guardas de ambiente por subprocesso (prod/remoto/ausencia
de calibracao). `--autoteste` aplica MUTACOES em copias temporarias do componente e exige que a
suite PEGUE cada uma — mutacao nao aplicada (ancora de texto mudou) tambem e' falha.

Uso: python3 scripts/agentes/verificar_pontuacao_preditiva.py [--autoteste]
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal


def D(v):
    return Decimal(str(v))

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(AQUI, "..", ".."))
COMPONENTE = os.path.join(REPO, "hermes", "agentes", "analytics", "pontuacao_preditiva.py")
CONTRATO = os.path.join(REPO, "hermes", "agentes", "analytics", "pontuacao-preditiva-v1.json")
CONTRATO_CALIB = os.path.join(REPO, "hermes", "agentes", "analytics", "calibracao-score-v1.json")
MODULO_CALIB = os.path.join(REPO, "hermes", "agentes", "analytics", "calibracao_score.py")
DADOS = os.path.join(REPO, "docs", "data", "data_contract_v1.json")

ITENS = []


def item(nome, condicao, detalhe=""):
    ITENS.append((nome, bool(condicao), detalhe))


def carregar(caminho):
    spec = importlib.util.spec_from_file_location("mod_%s" % abs(hash(caminho)), caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _contrato_valido_preparado(mod, calib_relatorio):
    contrato = json.load(open(CONTRATO, encoding="utf-8"))
    dados = json.load(open(DADOS, encoding="utf-8"))
    return contrato, dados, mod.validar_contrato(contrato, dados, calib_relatorio)


def relatorio_de_calibracao_de_mentira(mod, **mudancas):
    """Relatorio minimo que satisfaz o que a dependencia exige — cada teste muda UMA coisa."""
    base = {
        "versao": "calibracao-score-v1", "card": "TRE-W9-E01-T01",
        "hash_do_relatorio": "a" * 64,
        "gate_de_volume": {"base_suficiente": True, "com_desfecho": 40, "won": 21, "lost": 19},
        "parametros": {"fracao_de_ajuste": {"numerador": 2, "denominador": 3},
                       "pesos_em_vigor": {"ICP": "0.35", "AUTOMATION_FIT": "0.3",
                                          "BUYING_SIGNAL": "0.25", "DATA_QUALITY": "0.1"},
                       "faixas_ascendentes": ["Nurture", "C", "B", "A", "A+"]},
        "proposta": {"status": "PROPOSTA_GERADA",
                     "pesos": {"aprovada": True,
                               "pesos": {"ICP": "0", "AUTOMATION_FIT": "0.3",
                                         "BUYING_SIGNAL": "0.25", "DATA_QUALITY": "0.45"}}},
    }
    for chave, valor in mudancas.items():
        if valor is None:
            base.pop(chave, None)
        elif isinstance(valor, dict) and isinstance(base.get(chave), dict):
            base[chave] = dict(base[chave], **valor)
        else:
            base[chave] = valor
    return base


def suite():
    ITENS.clear()
    mod = carregar(COMPONENTE)
    calib = carregar(MODULO_CALIB)
    fonte = open(COMPONENTE, encoding="utf-8").read()
    contrato = json.load(open(CONTRATO, encoding="utf-8"))
    dados = json.load(open(DADOS, encoding="utf-8"))

    # ---- contrato ----
    item("contrato declara a versao do componente", contrato["versao"] == mod.VERSAO,
         "%s != %s" % (contrato.get("versao"), mod.VERSAO))
    item("contrato e' do card TRE-W9-E02-T01", contrato.get("card") == "TRE-W9-E02-T01")
    for campo in ("componente", "autoridade", "dependencia", "unidade", "parametros", "metricas",
                  "guardas", "saida", "lacunas", "lacunas_declaradas", "pre_condicao_do_card"):
        item("contrato traz o campo `%s`" % campo, campo in contrato)
    item("contrato declara a dependencia da calibracao (versao e card)",
         contrato["dependencia"]["calibracao"]["versao_exigida"] == "calibracao-score-v1"
         and contrato["dependencia"]["calibracao"]["card_exigido"] == "TRE-W9-E01-T01")
    item("contrato declara as guardas de ambiente, leitura pura, segredo, pii e nao-aplicacao",
         all(g in contrato["guardas"] for g in ("ambiente", "leitura_pura", "segredo", "pii",
                                                "previsao_nao_aplicada", "contrato")))
    item("contrato declara os codigos de saida 0,2,3,4,5,6",
         all(c in contrato["saida"]["codigos_de_saida"] for c in ("0 =", "2 =", "3 =", "4 =", "5 =", "6 =")))
    item("contrato lista >= 6 lacunas declaradas", len(contrato["lacunas_declaradas"]) >= 6)

    # ---- componente: invariantes por leitura do texto ----
    item("componente nao tem SQL proprio (a leitura e' do instrumento)",
         not any(v in fonte for v in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")))
    item("componente nao emite campo de PII (chave de saida nenhuma e' nome/contato/e-mail)",
         not any(v in fonte for v in ('"legal_name"', '"contact_name"', '"contact_email"', '"email"',
                                      '"cnpj"', '"telefone"', '"phone"')))
    item("componente nao mocka o proprio alvo", not any(v in fonte for v in ("unittest.mock", "monkeypatch")))
    item("componente usa ler_tudo do modulo da calibracao (instrumento transitivo)",
         "calib.ler_tudo" in fonte and "efetividade_score" in fonte)

    # ---- aritmetica pura ----
    pesos_ok = {"ICP": "0.35", "AUTOMATION_FIT": "0.3", "BUYING_SIGNAL": "0.25", "DATA_QUALITY": "0.1"}
    item("pesos_em_centesimos converte e exige soma 100",
         mod.pesos_em_centesimos(pesos_ok) == (35, 30, 25, 10),
         str(mod.pesos_em_centesimos(pesos_ok)))
    item("pesos_em_centesimos RECUSA soma diferente de 100",
         _recusa(mod, lambda: mod.pesos_em_centesimos(dict(pesos_ok, ICP="0.5"))))
    item("pesos_em_centesimos RECUSA componente faltando",
         _recusa(mod, lambda: mod.pesos_em_centesimos({"ICP": "1.0"})))
    item("bin_do_score: extremos e monotonicidade",
         mod.bin_do_score(0, 10) == 0 and mod.bin_do_score(10000, 10) == 9
         and list(map(lambda s: mod.bin_do_score(s, 10), (0, 1000, 2000, 9999, 10000)))
         == sorted(map(lambda s: mod.bin_do_score(s, 10), (0, 1000, 2000, 9999, 10000))))
    item("bin_do_score limita valores fora da escala",
         mod.bin_do_score(-500, 10) == 0 and mod.bin_do_score(99999, 10) == 9)

    itens_curva = [("o%d" % i, {"ICP": D("90.0"), "AUTOMATION_FIT": D("50.0"),
                                "BUYING_SIGNAL": D("50.0"), "DATA_QUALITY": D(40 + i)}, y)
                   for i, y in enumerate([1, 1, 1, 1, 0, 1, 0, 1, 1, 1], start=1)]
    pesos_cents = (35, 30, 25, 10)
    blocos, contagens = mod.blocos_da_curva(itens_curva, pesos_cents, 10, calib)
    item("blocos_da_curva: tudo que existe esta' em algum bino",
         sum(c[0] for c in contagens) == len(itens_curva),
         json.dumps(contagens))
    item("blocos_da_curva: blocos monotonicos nao-decrescentes",
         all(blocos[i]["probabilidade"] <= blocos[i + 1]["probabilidade"]
             for i in range(len(blocos) - 1)),
         json.dumps([b["probabilidade"] for b in blocos]))
    violador = [("x%d" % i, {"ICP": D("50.0"), "AUTOMATION_FIT": D("50.0"), "BUYING_SIGNAL": D("50.0"),
                             "DATA_QUALITY": D(v)}, y)
                for i, (v, y) in enumerate([(10.0, 1), (20.0, 1), (30.0, 1), (40.0, 1),
                                            (50.0, 1), (60.0, 0), (70.0, 0), (80.0, 0),
                                            (90.0, 0), (100.0, 0)])]
    blocos_v, contagens_v = mod.blocos_da_curva(violador, pesos_cents, 10, calib)
    item("PAVA funde violadores (a taxa que cai vira UM bloco)",
         len(blocos_v) == 1 and blocos_v[0]["n"] == 10 and blocos_v[0]["won"] == 5,
         json.dumps(blocos_v))
    item("PAVA pondera por n ao fundir (probabilidade = won total / n total)",
         abs(blocos_v[0]["probabilidade"] - 0.5) < 1e-9, str(blocos_v[0]["probabilidade"]))
    item("probabilidade_do_bin: bino com base devolve a probabilidade do bloco",
         mod.probabilidade_do_bin([{"de": 3, "ate": 5, "n": 4, "won": 3, "probabilidade": 0.75}], 4) == 0.75)
    item("probabilidade_do_bin: bino sem base herda o bloco ANTERIOR",
         mod.probabilidade_do_bin([{"de": 3, "ate": 3, "n": 4, "won": 3, "probabilidade": 0.75},
                                   {"de": 7, "ate": 9, "n": 2, "won": 0, "probabilidade": 0.0}], 5) == 0.75)
    item("probabilidade_do_bin: antes do primeiro bloco NAO ha' previsao (None)",
         mod.probabilidade_do_bin([{"de": 4, "ate": 6, "n": 4, "won": 3, "probabilidade": 0.75}], 1) is None)
    item("brier: erro quadratico medio conhecido",
         abs(mod.brier([(0.5, 1), (0.5, 0), (1.0, 1), (0.0, 0)]) - 0.125) < 1e-9,
         str(mod.brier([(0.5, 1), (0.5, 0), (1.0, 1), (0.0, 0)])))
    item("brier: sem pontos devolve None", mod.brier([]) is None)
    item("log_loss: p=0.5 e' log(2)", abs(mod.log_loss([(0.5, 1), (0.5, 0)]) - 0.693147) < 1e-6,
         str(mod.log_loss([(0.5, 1), (0.5, 0)])))
    item("log_loss: previsao certa e' menor que previsao errada",
         mod.log_loss([(0.99, 1)]) < mod.log_loss([(0.01, 1)]))
    limites = {p["name"]: (int(p["min"] * 100), int(p["max"] * 100)) for p in dados["scores"]["tiers"]}
    faixas = [p for p in sorted(limites, key=lambda n: limites[n][0])]
    item("faixa_do_valor: 95 -> A+, 55 -> C, 10 -> Nurture",
         (mod.faixa_do_valor(limites, faixas, 9500), mod.faixa_do_valor(limites, faixas, 5500),
          mod.faixa_do_valor(limites, faixas, 1000)) == ("A+", "C", "Nurture"))
    item("_limites_do_contrato aceita as faixas do Data Contract (5, contiguas, 0..100)",
         len(mod._limites_do_contrato(dados)) == 5)
    dados_lacuna = json.loads(json.dumps(dados))
    dados_lacuna["scores"]["tiers"][1]["min"] = 85.0
    item("_limites_do_contrato RECUSA faixa com lacuna",
         _recusa(mod, lambda: mod._limites_do_contrato(dados_lacuna)))

    # ---- dependencia da calibracao ----
    bom = relatorio_de_calibracao_de_mentira(mod)
    item("validar_contrato aceita relatorio de calibracao coerente",
         mod.validar_contrato(contrato, dados, bom)["bins"] == 10)
    item("validar_contrato RECUSA relatorio cuja versao nao e' a exigida",
         _recusa(mod, lambda: mod.validar_contrato(contrato, dados,
                                                   relatorio_de_calibracao_de_mentira(mod, versao="outra-v9")),
                 ("DEPENDENCIA_CALIBRACAO", "VENDA_DIVERGENTE")))
    item("validar_contrato RECUSA relatorio de OUTRO card",
         _recusa(mod, lambda: mod.validar_contrato(contrato, dados,
                                                   relatorio_de_calibracao_de_mentira(mod, card="TRE-W9-E09-T99")),
                 ("DEPENDENCIA_CALIBRACAO",)))
    item("validar_contrato RECUSA relatorio sem hash_do_relatorio valido",
         _recusa(mod, lambda: mod.validar_contrato(contrato, dados,
                                                   relatorio_de_calibracao_de_mentira(mod, hash_do_relatorio="curto")),
                 ("DEPENDENCIA_CALIBRACAO",)))
    item("validar_contrato RECUSA relatorio sem gate_de_volume medido",
         _recusa(mod, lambda: mod.validar_contrato(contrato, dados,
                                                   relatorio_de_calibracao_de_mentira(mod, gate_de_volume=None)),
                 ("DEPENDENCIA_CALIBRACAO",)))
    item("validar_contrato RECUSA corte ajuste/validacao divergente (CORTE_DIVERGENTE)",
         _recusa(mod, lambda: mod.validar_contrato(
             contrato, dados, relatorio_de_calibracao_de_mentira(
                 mod, parametros={"fracao_de_ajuste": {"numerador": 1, "denominador": 2}})),
             ("CORTE_DIVERGENTE",)))
    item("validar_contrato RECUSA faixas ascendentes divergentes do Data Contract",
         _recusa(mod, lambda: mod.validar_contrato(
             contrato, dados, relatorio_de_calibracao_de_mentira(
                 mod, parametros={"faixas_ascendentes": ["Baixa", "Media", "Alta"]})),
             ("FAIXA_DIVERGENTE",)))
    contrato_versao = dict(contrato, versao="pontuacao-preditiva-v9")
    item("validar_contrato RECUSA contrato deste componente com versao diferente",
         _recusa(mod, lambda: mod.validar_contrato(contrato_versao, dados, bom)))
    contrato_bins = json.loads(json.dumps(contrato))
    contrato_bins["parametros"]["bins_da_curva"] = 1
    item("validar_contrato RECUSA bins_da_curva fora de 2..100",
         _recusa(mod, lambda: mod.validar_contrato(contrato_bins, dados, bom)))
    contrato_fracao = json.loads(json.dumps(contrato))
    contrato_fracao["parametros"]["fracao_de_ajuste"] = {"numerador": 3, "denominador": 2}
    item("validar_contrato RECUSA fracao de ajuste invalida",
         _recusa(mod, lambda: mod.validar_contrato(contrato_fracao, dados, bom)))
    validado = mod.validar_contrato(contrato, dados, bom)
    item("origem dos pesos = proposta da calibracao quando ela propos",
         validado["origem_dos_pesos"] == "proposta_de_calibracao"
         and validado["pesos_cents"] == (0, 30, 25, 45), json.dumps(validado["pesos_cents"]))
    abdicou = relatorio_de_calibracao_de_mentira(mod, proposta={"pesos": {"aprovada": False, "pesos": None}})
    validado2 = mod.validar_contrato(contrato, dados, abdicou)
    item("origem dos pesos = contrato em vigor quando a calibracao nao propos",
         validado2["origem_dos_pesos"] == "contrato_em_vigor"
         and validado2["pesos_cents"] == (35, 30, 25, 10), json.dumps(validado2["pesos_cents"]))

    # ---- hash ----
    item("hash_do_relatorio ignora gerado_em e referencia_temporal",
         mod.hash_do_relatorio({"a": 1, "gerado_em": "x"}) == mod.hash_do_relatorio({"a": 1, "gerado_em": "y"}))
    item("hash_do_relatorio muda com o corpo medido",
         mod.hash_do_relatorio({"a": 1}) != mod.hash_do_relatorio({"a": 2}))

    # ---- avaliacao (usa a AUC do instrumento da calibracao) ----
    pesos_propostos = (0, 30, 25, 45)
    blocos_a, _c = mod.blocos_da_curva(itens_curva, pesos_propostos, 10, calib)
    av = mod.avaliar_com_pesos(itens_curva, blocos_a, 10, calib, pesos_propostos, 0.5)
    item("avaliar_com_pesos mede AUC, Brier, Brier de base, skill e log-loss",
         all(av.get(k) is not None for k in ("auc", "brier", "brier_de_base", "brier_skill", "log_loss")),
         json.dumps(av))
    item("avaliar_com_pesos: cobertura 100% quando todo bino tem bloco",
         av["com_previsao"] == av["organizacoes"] and av["cobertura_pct"] == 100.0, json.dumps(av))
    item("avaliar_com_pesos: skill = 1 - brier/brier_de_base",
         abs(av["brier_skill"] - round(1 - av["brier"] / av["brier_de_base"], 6)) < 1e-9, json.dumps(av))
    conf = mod.confiabilidade(itens_curva, blocos_a, 10, calib, pesos_propostos)
    item("confiabilidade cobre os 10 binos e marca com_base",
         len(conf) == 10 and all("com_base" in l and "desvio" in l for l in conf))
    item("confiabilidade: previsto menos observado = desvio",
         all(l["desvio"] is None or abs(l["desvio"] - round(l["previsto"] - l["observado"], 6)) < 1e-9
             for l in conf))

    # ---- guardas por subprocesso ----
    amb = [sys.executable]
    rc_prod = subprocess.run(amb + [COMPONENTE, "--ambiente", "prod"], capture_output=True, text=True).returncode
    item("guarda: prod RECUSA por desenho (exit 4)", rc_prod == 4, "exit=%s" % rc_prod)
    rc_remoto = subprocess.run(amb + [COMPONENTE, "--ambiente", "dev", "--porta-banco",
                                      "ssh root@10.0.0.1 psql", "--calibracao", "/tmp/x.json"],
                               capture_output=True, text=True)
    item("guarda: porta remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)",
         rc_remoto.returncode == 3 and "BANCO_NAO_E_DEV" in rc_remoto.stdout,
         "exit=%s %s" % (rc_remoto.returncode, rc_remoto.stdout.strip()))
    rc_sem_calib = subprocess.run(amb + [COMPONENTE, "--ambiente", "dev", "--porta-banco",
                                         "docker exec -i pg-analytics-pred psql"],
                                  capture_output=True, text=True)
    item("guarda: sem --calibracao RECUSA (DEPENDENCIA_CALIBRACAO, exit 3)",
         rc_sem_calib.returncode == 3 and "DEPENDENCIA_CALIBRACAO" in rc_sem_calib.stdout,
         "exit=%s" % rc_sem_calib.returncode)
    rc_plano = subprocess.run(amb + [COMPONENTE, "--ambiente", "dev", "--planejar"],
                              capture_output=True, text=True)
    item("--planejar funciona sem banco (exit 0, declara PAVA e gate)",
         rc_plano.returncode == 0 and "PAVA" in rc_plano.stdout and "minimo_de_coorte=30" in rc_plano.stdout,
         "exit=%s" % rc_plano.returncode)
    rc_conferir = subprocess.run(amb + [COMPONENTE, "--ambiente", "dev", "--conferir"],
                                 capture_output=True, text=True)
    item("--conferir funciona sem banco (exit 0)", rc_conferir.returncode == 0,
         "exit=%s %s" % (rc_conferir.returncode, rc_conferir.stdout.strip()[:120]))
    item("contrato declara que a previsao nao e' aplicada",
         "aplicado" in contrato["guardas"]["previsao_nao_aplicada"]
         and "false" in contrato["guardas"]["previsao_nao_aplicada"])
    item("contrato da calibracao (dependencia) continua o vigente",
         json.load(open(CONTRATO_CALIB, encoding="utf-8"))["versao"] == "calibracao-score-v1")
    return ITENS


def _recusa(mod, chamada, motivos_esperados=None):
    try:
        chamada()
    except mod.Recusa as exc:
        return motivos_esperados is None or exc.motivo in motivos_esperados
    except Exception:
        return False
    return False


# -------------------------------------------------------------------------------------------- #
# Autoteste por mutacao (copias temporarias; a suite roda contra o MUTANTE e tem de reprovar)
# -------------------------------------------------------------------------------------------- #
DENTES = [
    ("bino do score sempre 0", "return min(indice, quantidade_bins - 1)", "return 0",
     lambda m: m.bin_do_score(10000, 10) != 9),
    ("PAVA sem monotonicidade", 'while blocos and blocos[-1]["probabilidade"] > bloco["probabilidade"]:',
     "while False:",
     lambda m: not _monotono(m.blocos_da_curva(
         [("x%d" % i, {"ICP": D("50.0"), "AUTOMATION_FIT": D("50.0"), "BUYING_SIGNAL": D("50.0"),
                       "DATA_QUALITY": D(v)}, y)
          for i, (v, y) in enumerate([(10.0, 1), (20.0, 1), (40.0, 1), (60.0, 0), (80.0, 0), (100.0, 0)])],
         (35, 30, 25, 10), 10, carregar(MODULO_CALIB))[0])),
    ("bino sem base devolve zero em vez de None",
     'return anterior["probabilidade"] if anterior else None', 'return 0.0 if anterior is None else anterior["probabilidade"]',
     lambda m: m.probabilidade_do_bin([{"de": 4, "ate": 6, "n": 4, "won": 3, "probabilidade": 0.75}], 1) is not None),
    ("producao recusada com codigo errado", "codigo=4)", "codigo=3)",
     lambda m: (_codigo_da_producao(m) != 4)),
    ("hash deixa de ignorar gerado_em",
     'if k not in ("gerado_em", "referencia_temporal", "hash_do_relatorio")', "if True",
     lambda m: m.hash_do_relatorio({"a": 1, "gerado_em": "x"}) != m.hash_do_relatorio({"a": 1, "gerado_em": "y"})),
    ("peso que nao soma 100 passa", "if sum(vetor) != 100:", "if False:",
     lambda m: not _recusa(m, lambda: m.pesos_em_centesimos({"ICP": "0.9", "AUTOMATION_FIT": "0.3",
                                                             "BUYING_SIGNAL": "0.25", "DATA_QUALITY": "0.1"}))),
    ("bins fora de 2..100 passam", "if not 2 <= bins <= 100:", "if False:",
     lambda m: not _recusa(m, lambda: m.validar_contrato(
         json.loads(json.dumps(json.load(open(CONTRATO, encoding="utf-8")))) if False else
         dict(json.load(open(CONTRATO, encoding="utf-8")),
              parametros=dict(json.load(open(CONTRATO, encoding="utf-8"))["parametros"], bins_da_curva=1)),
         json.load(open(DADOS, encoding="utf-8")),
         relatorio_de_calibracao_de_mentira(m)))),
    ("versao errada do relatorio de calibracao passa",
     'if calib_relatorio.get("versao") != VERSAO_CALIBRACAO_EXIGIDA:', "if False:",
     lambda m: not _recusa(m, lambda: m.validar_contrato(
         json.load(open(CONTRATO, encoding="utf-8")), json.load(open(DADOS, encoding="utf-8")),
         relatorio_de_calibracao_de_mentira(m, versao="outra-v9")))),
]


def _monotono(blocos):
    return all(blocos[i]["probabilidade"] <= blocos[i + 1]["probabilidade"] for i in range(len(blocos) - 1))


def _codigo_da_producao(mod):
    try:
        mod.recusar_producao()
    except mod.Recusa as exc:
        return exc.codigo
    return None


def autoteste():
    fonte = open(COMPONENTE, encoding="utf-8").read()
    pegos = 0
    for nome, antigo, novo, predicado in DENTES:
        if antigo not in fonte:
            print("FALHOU dente nao aplicado (ancora mudou): %s" % nome)
            continue
        with tempfile.TemporaryDirectory() as pasta:
            destino = os.path.join(pasta, "mutante.py")
            with open(destino, "w", encoding="utf-8") as fh:
                fh.write(fonte.replace(antigo, novo, 1))
            try:
                modulo = carregar(destino)
                detectado = bool(predicado(modulo))
            except Exception as exc:  # mutante que explode = defeito detectado
                detectado = True
                print("      (mutante %s explodiu: %s)" % (nome, exc))
            if detectado:
                pegos += 1
                print("OK    dente pego: %s" % nome)
            else:
                print("FALHOU dente NAO pego (buraco na suite): %s" % nome)
    return pegos, len(DENTES)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    itens = suite()
    falhas = 0
    for nome, ok, detalhe in itens:
        if ok:
            print("OK    %s" % nome)
        else:
            falhas += 1
            print("FALHOU %s %s" % (nome, detalhe))
    dentes_ok = dentes_total = 0
    if args.autoteste:
        dentes_ok, dentes_total = autoteste()
    if falhas or (args.autoteste and dentes_ok != dentes_total):
        print("VERIFICADOR_PONTUACAO_FALHOU (%d itens, %d falhas)" % (len(itens), falhas))
        return 1
    sufixo = " + autoteste OK (%d/%d dentes)" % (dentes_ok, dentes_total) if args.autoteste else ""
    print("VERIFICADOR_PONTUACAO_PASS (%d itens, 0 falhas)%s" % (len(itens), sufixo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
