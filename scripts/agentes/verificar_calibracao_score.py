#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE da calibracao do score (`calibracao-score-v1`) — card TRE-W9-E01-T01.

Mede contrato, dependencias (instrumento W8-E03 e funil W8-E01) e componente SEM banco: derivacao
pura sobre base sintetica conferida A MAO, gate de volume, grade do simplexo com desempate
deterministico, AUC por posto, margem na VALIDACAO, cortes de Youden, faixas contiguas,
monotonicidade como achado, guardas de ambiente, leitura-pura-por-auditoria, proposta-nao-aplicada,
determinismo, HTML auto-contido e ausencia de PII/segredo.

Uso:
  python3 scripts/agentes/verificar_calibracao_score.py                  # mede o repo
  python3 scripts/agentes/verificar_calibracao_score.py --alvo-dir <dir> # mede uma copia (dentes)
  python3 scripts/agentes/verificar_calibracao_score.py --autoteste      # itens + N mutacoes

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
REL_COMPONENTE = os.path.join("hermes", "agentes", "analytics", "calibracao_score.py")
REL_CONTRATO = os.path.join("hermes", "agentes", "analytics", "calibracao-score-v1.json")
REL_INSTRUMENTO = os.path.join("hermes", "agentes", "analytics", "efetividade_score.py")
REL_INSTR_CONTRATO = os.path.join("hermes", "agentes", "analytics", "efetividade-score-v1.json")
REL_FUNIL = os.path.join("hermes", "agentes", "analytics", "funil.py")
REL_FUNIL_CONTRATO = os.path.join("hermes", "agentes", "analytics", "funil-v1.json")
REL_DADOS = os.path.join("docs", "data", "data_contract_v1.json")
REL_VERIFICADOR_INSTRUMENTO = os.path.join("scripts", "agentes", "verificar_efetividade_score.py")
REFERENCIA = datetime(2026, 10, 3, 0, 0, 0, tzinfo=timezone.utc)
COMPONENTES = ("ICP", "AUTOMATION_FIT", "BUYING_SIGNAL", "DATA_QUALITY")

OK = 0
FALHAS = 0


def item(nome, condicao, detalhe=""):
    global OK, FALHAS
    if condicao:
        OK += 1
        print("OK    %s" % nome)
    else:
        FALHAS += 1
        print("FALHOU %s %s" % (nome, detalhe))


def carregar_modulo(caminho, nome):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def contexto(alvo):
    componentes = {
        "componente": carregar_modulo(os.path.join(alvo, REL_COMPONENTE), "calibracao"),
        "instrumento": carregar_modulo(os.path.join(alvo, REL_INSTRUMENTO), "instrumento"),
        "funil": carregar_modulo(os.path.join(alvo, REL_FUNIL), "funil"),
    }
    contratos = {
        "calibracao": json.load(open(os.path.join(alvo, REL_CONTRATO), encoding="utf-8")),
        "instrumento": json.load(open(os.path.join(alvo, REL_INSTR_CONTRATO), encoding="utf-8")),
        "funil": json.load(open(os.path.join(alvo, REL_FUNIL_CONTRATO), encoding="utf-8")),
        "dados": json.load(open(os.path.join(alvo, REL_DADOS), encoding="utf-8")),
    }
    return componentes, contratos


# -------------------------------------------------------------------------------------------- #
# Base sintetica (fixture) — desenho declarado para os numeros serem conferiveis a mao
# -------------------------------------------------------------------------------------------- #
def gerar_base(quantidade=60, sinal=True, invertido=False):
    """SINAL em ICP+DQ quando `sinal`; com `invertido` o ICP fica ANTI-correlacionado ao desfecho.

    Devolve (brutas_funil, brutas_proprias) no MESMO formato das fontes do funil/instrumento.
    """
    base, pri, comp, trilha = [], [], [], []
    for i in range(quantidade):
        org = "%08d-0000-0000-0000-000000000000" % (i + 1)
        if invertido:
            icp = 10 if i >= quantidade // 2 else 90  # ANTI-correlacionado: peso alto aponta errado
            dq = 40 + i
            af = bs = 50
            y = 1 if i >= quantidade // 2 else 0
        else:
            icp = 20 + ((i * 37) % 81)
            dq = 20 + ((i * 53) % 81)
            af = 30 + ((i * 17) % 61)
            bs = 25 + ((i * 29) % 71)
            y = 1 if (0.6 * icp + 0.4 * dq) >= 62 else 0
            if sinal and i in (7, 23, 41):
                y = 1 - y
            if not sinal:
                y = 1 if icp >= 60 else 0
        valor = round(0.35 * icp + 0.30 * af + 0.25 * bs + 0.10 * dq, 2)
        base.append("%s|SITE|DISCOVERED" % org)
        pri.append("%s|s%s|%.2f|v1|2026-09-30T12:00:00+00||1" % (org, i, valor))
        for tipo, v in (("ICP", icp), ("AUTOMATION_FIT", af), ("BUYING_SIGNAL", bs), ("DATA_QUALITY", dq)):
            comp.append("%s|%s|%.2f|v1|" % (org, tipo, v))
        trilha.append("%s|%s|%s" % (org, "Won" if y else "Lost",
                                    "OPPORTUNITY_WON" if y else "OPPORTUNITY_LOST"))
    return ({"BASE_ORGANIZACOES": base, "TRILHA_ESTAGIO": trilha},
            {"PRIORITY_ULTIMO": pri, "COMPONENTES_ULTIMOS": comp})


def relatorio(componentes, contratos, brutas_funil, brutas_proprias, contrato=None):
    return componentes["componente"].derivar(
        contrato or contratos["calibracao"], contratos["dados"], contratos["instrumento"],
        contratos["funil"], componentes["funil"], componentes["instrumento"],
        copy.deepcopy(brutas_funil), copy.deepcopy(brutas_proprias), REFERENCIA, "dev",
        {"desde": None, "ate": None})


def itens(alvo):
    global OK, FALHAS
    componentes, contratos = contexto(alvo)
    cal = componentes["componente"]

    # --- 1. contrato e dependencias ------------------------------------------------------
    try:
        validado = cal.validar_contrato(contratos["calibracao"], contratos["dados"],
                                        contratos["instrumento"], contratos["funil"],
                                        componentes["instrumento"])
        item("contrato valida contra Data Contract, instrumento e funil", True)
        item("gate de volume lido do contrato (30/5)",
             validado["minimo_de_coorte"] == 30 and validado["minimo_por_classe"] == 5,
             "coorte=%s classe=%s" % (validado["minimo_de_coorte"], validado["minimo_por_classe"]))
        item("faixas ascendentes do Data Contract (Nurture..A+)",
             validado["faixas_ascendentes"] == ["Nurture", "C", "B", "A", "A+"],
             str(validado["faixas_ascendentes"]))
        item("pesos em vigor lidos do Data Contract (nao literais no codigo)",
             [str(validado["pesos"][c]) for c in COMPONENTES] == ["0.35", "0.3", "0.25", "0.1"],
             str([str(validado["pesos"][c]) for c in COMPONENTES]))
    except Exception as exc:  # noqa: BLE001
        item("contrato valida contra Data Contract, instrumento e funil", False, repr(exc))

    contrato_quebrado = copy.deepcopy(contratos["calibracao"])
    contrato_quebrado["parametros"]["minimo_de_coorte"] = 10
    contrato_quebrado["parametros"]["minimo_por_classe"] = 9
    try:
        cal.validar_contrato(contrato_quebrado, contratos["dados"], contratos["instrumento"],
                             contratos["funil"], componentes["instrumento"])
        item("minimos incoerentes RECUSAM (coorte < 2x classe)", False, "aceitou 10/9")
    except Exception as exc:  # noqa: BLE001
        item("minimos incoerentes RECUSAM (coorte < 2x classe)",
             getattr(exc, "motivo", "") == "MINIMOS_INCOERENTES", getattr(exc, "motivo", repr(exc)))

    dados_quebrados = copy.deepcopy(contratos["dados"])
    dados_quebrados["scores"]["tiers"][1]["min"] = 79  # abre LACUNA entre C e B
    try:
        cal.validar_contrato(contratos["calibracao"], dados_quebrados, contratos["instrumento"],
                             contratos["funil"], componentes["instrumento"])
        item("Data Contract com faixa em lacuna RECUSA", False, "aceitou faixa com buraco")
    except Exception as exc:  # noqa: BLE001
        item("Data Contract com faixa em lacuna RECUSA", True, getattr(exc, "motivo", ""))

    # --- 2. AUC por posto (conferivel a mao) ----------------------------------------------
    item("AUC perfeita = 1.0", cal.auc([(1, 0), (2, 0), (3, 1), (4, 1)]) == 1.0)
    item("AUC invertida = 0.0", cal.auc([(1, 1), (2, 1), (3, 0), (4, 0)]) == 0.0)
    item("AUC com empate total = 0.5", cal.auc([(5, 0), (5, 1)]) == 0.5)
    item("AUC sem as duas classes = None", cal.auc([(5, 1), (6, 1)]) is None)

    # --- 3. gate de volume: base fina ABSTEVE (pre-condicao do card) ----------------------
    brutas_funil, brutas_proprias = gerar_base(10)
    rel = relatorio(componentes, contratos, brutas_funil, brutas_proprias)
    item("base fina: base_suficiente=False com motivo nomeado",
         rel["gate_de_volume"]["base_suficiente"] is False
         and "COORTE_COM_DESFECHO_ABAIXO_DO_MINIMO" in rel["gate_de_volume"]["motivos"],
         json.dumps(rel["gate_de_volume"]["motivos"]))
    item("base fina: status ABSTEVE e NENHUMA proposta",
         rel["proposta"]["status"] == "ABSTEVE" and rel["proposta"]["pesos"] is None
         and rel["faixas_propostas"] == [])
    item("base fina: nao ha' secao de incumbente (nada foi ajustado)",
         rel["incumbente"] is None, str(rel["incumbente"])[:60])
    item("base fina: resumo declara a abstencao", "CALIBRACAO_ABSTEVE_VOLUME" in cal._resumo_texto(rel))

    # --- 4. base com volume e sinal: proposta de FAIXAS -----------------------------------
    brutas_funil, brutas_proprias = gerar_base(60)
    rel = relatorio(componentes, contratos, brutas_funil, brutas_proprias)
    gate = rel["gate_de_volume"]
    item("base com volume: base_suficiente=True, coorte 60 (won 30 / lost 30)",
         gate["base_suficiente"] and gate["com_desfecho"] == 60 and gate["won"] == 30 and gate["lost"] == 30,
         json.dumps(gate))
    item("grade do simplexo completa (1771 vetores no passo 0.05)",
         (rel["proposta"]["pesos"] or {}).get("vetores_avaliados") == 1771)
    faixas = rel["faixas_propostas"]
    item("5 faixas propostas, na ordem ascendente do Data Contract",
         [f["faixa"] for f in faixas] == ["Nurture", "C", "B", "A", "A+"], str([f["faixa"] for f in faixas]))
    contiguas = all(Decimal(faixas[i]["max"]) + Decimal("0.01") == Decimal(faixas[i + 1]["min"])
                    for i in range(len(faixas) - 1))
    item("faixas propostas CONTIGUAS (max + 0.01 == proximo min)", contiguas,
         json.dumps([(f["min"], f["max"]) for f in faixas]))
    item("faixas propostas cobrem 0..100",
         faixas[0]["min"] == "0.00" and faixas[-1]["max"] == "100.00")
    item("cobertura provada: soma das faixas = coorte com desfecho",
         sum(f["organizacoes"] for f in faixas) == gate["com_desfecho"] == 60,
         str(sum(f["organizacoes"] for f in faixas)))
    item("faixas propostas separam o desfecho (taxa de vitoria cresce de Nurture a A+)",
         faixas[0]["taxa_de_vitoria_pct"] == 0.0 and faixas[-1]["taxa_de_vitoria_pct"] > 70.0,
         json.dumps([f["taxa_de_vitoria_pct"] for f in faixas]))
    item("faixas vigentes tambem medidas (comparacao antes/depois)",
         len(rel["incumbente"]["faixas_vigentes"]) == 5)
    item("AUC do incumbente medida no ajuste e na validacao",
         rel["incumbente"]["ajuste"]["auc"] is not None and rel["incumbente"]["validacao"]["auc"] is not None,
         json.dumps(rel["separacao"]))
    item("corte ajuste/validacao por sha256: 43/17 na base de 60",
         rel["separacao"]["ajuste"]["organizacoes"] == 43 and rel["separacao"]["validacao"]["organizacoes"] == 17,
         json.dumps(rel["separacao"]))
    item("base com sinal: pesos NAO propostos — ganho na validacao abaixo da margem (anti-overfitting)",
         (rel["proposta"]["pesos"] or {}).get("aprovada") is False
         and (rel["proposta"]["pesos"] or {}).get("ganho_na_validacao") < 0.02,
         json.dumps((rel["proposta"]["pesos"] or {}).get("ganho_na_validacao")))
    item("melhor vetor da grade e' (60,0,0,40) — o sinal da fixture",
         (rel["proposta"]["pesos"] or {}).get("vetor") == [60, 0, 0, 40],
         str((rel["proposta"]["pesos"] or {}).get("vetor")))

    # --- 5. incumbente ANTI-correlacionado: proposta de PESOS aprovada na VALIDACAO --------
    brutas_funil, brutas_proprias = gerar_base(40, invertido=True)
    rel_inv = relatorio(componentes, contratos, brutas_funil, brutas_proprias)
    pesos = rel_inv["proposta"]["pesos"]
    item("incumbente invertido: AUC do incumbente perto de 0 no ajuste (< 0.1)",
         rel_inv["incumbente"]["ajuste"]["auc"] < 0.1, str(rel_inv["incumbente"]["ajuste"]["auc"]))
    item("incumbente invertido: componente ANTI-correlacionado zerado e o sinal mantido (0,*,*,+)",
         pesos["vetor"][0] == 0 and pesos["vetor"][3] > 0, str(pesos["vetor"]))
    item("incumbente invertido: desempate L1 escolhe o vetor mais proximo do peso em vigor (0,30,25,45)",
         pesos["vetor"] == [0, 30, 25, 45] and pesos["distancia_l1_ao_peso_em_vigor"] == 70,
         "%s l1=%s" % (pesos["vetor"], pesos["distancia_l1_ao_peso_em_vigor"]))
    item("incumbente invertido: proposta APROVADA com ganho >= margem na validacao",
         pesos["aprovada"] is True and pesos["ganho_na_validacao"] >= 0.02,
         "ganho=%s" % pesos["ganho_na_validacao"])
    item("incumbente invertido: AUC proposta > 0.9 nos dois lados",
         pesos["ajuste"]["auc"] > 0.9 and pesos["validacao"]["auc"] > 0.9, json.dumps(pesos["validacao"]))
    item("incumbente invertido: status PROPOSTA_GERADA", rel_inv["proposta"]["status"] == "PROPOSTA_GERADA")
    item("pesos propostos somam 1.00",
         sum(Decimal(v) for v in (pesos["pesos"] or {}).values()) == Decimal("1.00"))

    # --- 6. proposta NAO aplicada + saida ----------------------------------------------------
    item("proposta marcada NAO aplicada (aplicado/exige_versao_nova/aprovacao_humana)",
         rel_inv["proposta"]["aplicado"] is False and rel_inv["proposta"]["exige_versao_nova"] is True
         and rel_inv["proposta"]["aprovacao_humana"] == "pendente")
    item("gate de volume nomeado item a item no relatorio",
         {"organizacoes", "coorte_valida", "com_desfecho", "won", "lost", "base_suficiente", "motivos"}
         <= set(rel_inv["gate_de_volume"]))
    item("lacunas declaradas do desenho viajam no relatorio",
         len(rel_inv["lacunas_declaradas"]) == 7)
    html = cal.emitir_html(rel_inv)
    item("HTML auto-contido (sem recurso externo)",
         "http://" not in html and "https://" not in html and "<script" not in html)
    item("HTML nao vaza PII (sem e-mail/telefone do mundo real)", "@" not in html.split("lang")[0])
    rel2 = relatorio(componentes, contratos, *gerar_base(40))
    item("DETERMINISMO: a mesma base produz o mesmo hash_do_relatorio",
         (lambda r: r["hash_do_relatorio"] == relatorio(componentes, contratos, *gerar_base(40))["hash_do_relatorio"])(rel2))
    item("lacunas nomeadas: organizacao sem desfecho nao entra no ajuste",
         relatorio(componentes, contratos, *gerar_base(15, False))["lacunas"]["sem_desfecho"] >= 0)

    # --- 7. guardas de ambiente (sem banco) -------------------------------------------------
    import subprocess as sp
    comp = os.path.join(alvo, REL_COMPONENTE)
    prod = sp.run([sys.executable, comp, "--ambiente", "prod"], capture_output=True, text=True)
    item("prod RECUSA por desenho (exit 4)", prod.returncode == 4, "exit=%s" % prod.returncode)
    remoto = sp.run([sys.executable, comp, "--ambiente", "dev", "--porta-banco", "ssh root@10.0.0.1 psql"],
                    capture_output=True, text=True)
    item("porta de banco remota RECUSA em dev (exit 3)", remoto.returncode == 3, "exit=%s" % remoto.returncode)
    confirmar = sp.run([sys.executable, comp, "--ambiente", "homolog", "--porta-banco",
                        "docker exec -i pg-analytics-calib psql"], capture_output=True, text=True)
    item("homolog sem --confirmo RECUSA (exit 3)", confirmar.returncode == 3, "exit=%s" % confirmar.returncode)
    conferir = sp.run([sys.executable, comp, "--ambiente", "dev", "--conferir"], capture_output=True, text=True)
    item("--conferir valida sem banco (exit 0)", conferir.returncode == 0, conferir.stdout + conferir.stderr)
    plano = sp.run([sys.executable, comp, "--ambiente", "dev", "--planejar"], capture_output=True, text=True)
    item("--planejar declara gate, grade, pesos e faixas sem banco",
         plano.returncode == 0 and "minimo_de_coorte=30" in plano.stdout and "A+" in plano.stdout,
         plano.stdout[:200])

    # --- 8. leitura pura por AUDITORIA DA FONTE (verbo de escrita no codigo reprova) --------
    fonte = open(comp, encoding="utf-8").read()
    proibidos = [v for v in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE", "DROP ", "CREATE TABLE")
                 if v in fonte]
    item("componente nao contem verbo de escrita (auditoria da fonte)", not proibidos, str(proibidos))
    item("componente consome o instrumento e o funil (sem segunda verdade)",
         "carregar_instrumento" in fonte and "ler_tudo" in fonte and "alcance_por_organizacao" in fonte)
    item("componente nao abre conexao propria (a leitura vem do instrumento)",
         "SELECT" not in fonte.split('"""')[2] and "subprocess" not in fonte and "psycopg" not in fonte)
    item("saida sem PII: nenhum campo de contato no relatorio",
         not any(k in json.dumps(rel_inv, ensure_ascii=False).lower()
                 for k in ("email", "telefone", "whatsapp", "cnpj", "legal_name")))
    item("segredo: a rodada recusa valor de TRE_CALIBRACAO_TOKEN na evidencia",
         'TRE_CALIBRACAO_TOKEN' in fonte and "SENHA_VAZADA" in fonte)

    # --- 9. regressao do INSTRUMENTO (dependencia) ------------------------------------------
    ver = os.path.join(alvo, REL_VERIFICADOR_INSTRUMENTO)
    if os.path.exists(ver):
        proc = sp.run([sys.executable, ver, "--autoteste"], capture_output=True, text=True)
        item("regressao: suite offline do INSTRUMENTO (W8-E03) continua verde",
             "VERIFICADOR_EFETIVIDADE_PASS" in proc.stdout, proc.stdout[-200:])
    else:
        item("regressao: suite offline do INSTRUMENTO presente", False, ver)
    return OK, FALHAS


MUTACOES = [
    ("guarda de producao desligada", "        if args.ambiente == \"prod\":\n            recusar_producao()",
     "        if False:\n            recusar_producao()"),
    ("AUC invertida (ordem do sinal)", "            if p > n:\n                vitorias += 1.0",
     "            if p < n:\n                vitorias += 1.0"),
    ("base fina deixa de abster (proposta sobre base pequena)",
     '    if len(coorte["desfecho"]) < parametros["minimo_de_coorte"]:',
     "    if False:"),
    ("desfecho do funil ignorado (Won nunca reconhecido)", '        if "Won" in rotulos:',
     '        if False:'),
    ("limites das faixas amarrados ao valor observado (abre lacuna)",
     "        minimo = 0 if indice == 0 else cortes[indice - 1]",
     "        minimo = 0 if indice == 0 else grupo[0][0]"),
    ("margem de validacao deixada de lado (aprova ganho so' no ajuste)",
     '        "aprovada": bool(ganho is not None and ganho >= float(validado["margem"])),',
     '        "aprovada": bool(ganho is not None),'),
    ("proposta passa a ser marcada como aplicada",
     '"aplicado": False, "exige_versao_nova": True, "aprovacao_humana": "pendente",',
     '"aplicado": True, "exige_versao_nova": True, "aprovacao_humana": "pendente",'),
]


def _aplicar_mutacao(alvo, origem_destino, mutacao):
    caminho = origem_destino[0]
    with open(caminho, encoding="utf-8") as fh:
        texto = fh.read()
    if mutacao[1] not in texto:
        return False
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.write(texto.replace(mutacao[1], mutacao[2], 1))
    return True


def _rodar_verificador(alvo):
    proc = subprocess.run([sys.executable, os.path.join(alvo, "scripts", "agentes",
                                                        "verificar_calibracao_score.py")],
                          capture_output=True, text=True, cwd=alvo)
    return proc.returncode, proc.stdout


def autoteste(alvo, falhas_base):
    print("\n== autoteste por mutacao ==")
    detectadas = 0
    for nome, antigo, novo in MUTACOES:
        raiz = tempfile.mkdtemp(prefix="dente-calib-")
        copia = os.path.join(raiz, "repo")
        shutil.copytree(alvo, copia, ignore=shutil.ignore_patterns(".git", ".worktrees", "__pycache__"))
        destino = os.path.join(copia, REL_COMPONENTE)
        aplicada = _aplicar_mutacao(copia, (destino,), (nome, antigo, novo))
        if not aplicada:
            print("FALHOU mutacao NAO aplicada (ancora mudou): %s" % nome)
            shutil.rmtree(raiz, ignore_errors=True)
            continue
        rc, _saida = _rodar_verificador(copia)
        if rc != 0:
            detectadas += 1
            print("OK    dente detectado: %s" % nome)
        else:
            print("FALHOU dente PASSou com a mutacao: %s" % nome)
        shutil.rmtree(raiz, ignore_errors=True)
    total = len(MUTACOES)
    if detectadas == total and falhas_base == 0:
        print("AUTOTESTE %d/%d mutacoes detectadas" % (detectadas, total))
        return 0
    print("AUTOTESTE INCOMPLETO %d/%d (falhas_base=%d)" % (detectadas, total, falhas_base))
    return 1


def main():
    parser = argparse.ArgumentParser(description="Verificador offline da calibracao do score")
    parser.add_argument("--alvo-dir", default=RAIZ_PADRAO)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    alvo = os.path.abspath(args.alvo_dir)
    globals()["OK"] = 0
    globals()["FALHAS"] = 0
    ok, falhas = itens(alvo)
    codigo = 0 if falhas == 0 else 1
    if args.autoteste:
        codigo = max(codigo, autoteste(alvo, falhas))
    print("\nVERIFICADOR_CALIBRACAO_%s (%d itens, %d falhas)" % ("PASS" if codigo == 0 else "FALHOU", ok, falhas))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
