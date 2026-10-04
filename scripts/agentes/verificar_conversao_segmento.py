#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE da conversao por segmento (`conversao-segmento-v1`) — card TRE-W8-E02-T01.

Mede o contrato e o componente SEM banco: validacao cruzada de contratos (recorte x funil x dados),
guardas de ambiente, montagem/auditoria da consulta, derivacao pura do recorte por eixo, sentinelas
(SEM_DADO / FORA_DO_VOCABULARIO), cobertura, indice vs base, amostra pequena, determinismo, HTML
auto-contido e ausencia de PII. Cada item imprime OK/FALHOU.

Uso:
  python3 scripts/agentes/verificar_conversao_segmento.py                # mede o repo
  python3 scripts/agentes/verificar_conversao_segmento.py --alvo-dir <d>  # mede uma copia (dentes)
  python3 scripts/agentes/verificar_conversao_segmento.py --autoteste     # itens + mutacoes

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

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_PADRAO = os.path.abspath(os.path.join(AQUI, "..", ".."))
DIR_ANALYTICS = os.path.join("hermes", "agentes", "analytics")
REL_COMPONENTE = os.path.join(DIR_ANALYTICS, "conversao_segmento.py")
REL_CONTRATO = os.path.join(DIR_ANALYTICS, "conversao-segmento-v1.json")
REL_FUNIL = os.path.join(DIR_ANALYTICS, "funil.py")
REL_CONTRATO_FUNIL = os.path.join(DIR_ANALYTICS, "funil-v1.json")
REL_DADOS = os.path.join("docs", "data", "data_contract_v1.json")

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
    # O componente importa `funil` (reuso declarado): a copia do alvo tem de vencer o cache de import,
    # senao um dente aplicado no funil da copia passaria despercebido.
    sys.modules.pop("funil", None)
    spec = importlib.util.spec_from_file_location("conversao_alvo", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONTRATO_PADRAO = os.path.join(alvo, REL_CONTRATO)
    return mod


def carregar_contratos(alvo):
    with open(os.path.join(alvo, REL_CONTRATO), "r", encoding="utf-8") as fh:
        contrato = json.load(fh)
    with open(os.path.join(alvo, REL_CONTRATO_FUNIL), "r", encoding="utf-8") as fh:
        contrato_funil = json.load(fh)
    with open(os.path.join(alvo, REL_DADOS), "r", encoding="utf-8") as fh:
        dados = json.load(fh)
    return contrato, contrato_funil, dados


def O(i):
    """UUID falso, deterministico, de teste (nunca de base real)."""
    return "0000000%d-0000-0000-0000-000000000000" % i


def fixture(contrato_funil):
    """Base sintetica: 12 organizacoes, todos os niveis, 2 eixos, e os casos de borda medidos.

    Conta conferida A MAO (e repetida no aceite, contra PostgreSQL):
      base 12 · Won global 3 (25,00%) · faixa 150_299 = 6 orgs / 1 won · LT_70 = 3 / 1
      GT_1000 = 1 / 0 · SEM_DADO = 1 / 0 · FORA_DO_VOCABULARIO = 1 / 1
      tier A+ = 2 / 1 · A = 2 / 2 · B = 1 / 0 · C = 2 / 0 · Nurture = 2 / 0 · SEM_DADO = 3 / 0
    """
    orgs = [O(i) for i in range(1, 13)]
    linhas_base = []
    status = {5: "Nurture"}
    for i, org in enumerate(orgs, start=1):
        linhas_base.append("%s|SITE|%s" % (org, status.get(i, "DISCOVERED")))
    brutas = {
        "BASE_ORGANIZACOES": linhas_base,
        "PESQUISA_CONCLUIDA": [orgs[i] for i in (0, 1, 2, 4, 5, 6, 7, 9, 10)],
        "SINAIS": [orgs[0], orgs[6]],
        "SCORE_PRIORITY": [orgs[i] for i in (0, 1, 2, 4, 5, 6, 7, 9, 10)],
        "CONTATO_COM_CANAL": [orgs[0], orgs[5]],
        "INTERACAO_OUTBOUND": [orgs[0]],
        "INTERACAO_INBOUND_CLASSIFICADA": [orgs[0]],
        "RECOMENDACAO_REUNIAO": [orgs[0]],
        "TRILHA_ESTAGIO": [
            "%s|Proposta|STAGE_CHANGED" % orgs[0],
            "%s|Won|OPPORTUNITY_WON" % orgs[1],
            "%s|Lost|OPPORTUNITY_LOST" % orgs[2],
            "%s|Won|OPPORTUNITY_WON" % orgs[6],
            "%s|Won|OPPORTUNITY_WON" % orgs[9],
            "%s|Estagio Inventado|STAGE_CHANGED" % orgs[7],
            "99999999-9999-9999-9999-999999999999|Won|OPPORTUNITY_WON",
        ],
        # --- fontes do RECORTE (eixos) ---
        "FAIXA_FUNCIONARIOS": [
            "%s|150_299" % orgs[0], "%s|150_299" % orgs[1], "%s|150_299" % orgs[2],
            "%s|150_299" % orgs[3], "%s|150_299" % orgs[4], "%s|150_299" % orgs[5],
            "%s|LT_70" % orgs[6], "%s|LT_70" % orgs[7], "%s|LT_70" % orgs[8],
            "%s|150_299X" % orgs[9],           # QUASE identico a "150_299": fora do vocabulario
            "%s|" % orgs[10],                  # sem dado
            "%s|GT_1000" % orgs[11]],
        "PONTUACAO_PRIORITY": [
            "%s|92.00|2026-06-01T10:00:00" % orgs[0],
            "%s|85.00|2026-06-01T10:00:00" % orgs[1],
            # org 3: a VIGENTE e' a mais RECENTE (70 -> tier B), nao a maior (95 -> A+):
            "%s|95.00|2026-01-01T10:00:00" % orgs[2],
            "%s|70.00|2026-06-01T10:00:00" % orgs[2],
            "%s|30.00|2026-06-01T10:00:00" % orgs[4],
            "%s|55.00|2026-06-01T10:00:00" % orgs[5],
            "%s|88.00|2026-06-01T10:00:00" % orgs[6],
            "%s|61.00|2026-06-01T10:00:00" % orgs[7],
            "%s|95.00|2026-06-01T10:00:00" % orgs[9],
            "%s|45.00|2026-06-01T10:00:00" % orgs[10]],
    }
    return brutas


def montar(mod, contrato, contrato_funil, dados, brutas, gerado_em="2026-10-03T00:00:00Z"):
    organizacoes = mod.funil.extrair_base(brutas["BASE_ORGANIZACOES"])
    evidencia, lacunas = mod.funil.resolver_evidencia(contrato_funil, brutas, organizacoes)
    return mod.calcular_relatorio(contrato, contrato_funil, dados, brutas, organizacoes, evidencia,
                                  lacunas, "dev", {"desde": None, "ate": None},
                                  gerado_em=gerado_em)


def segmento(rel, eixo_nome, valor):
    for eixo in rel["eixos"]:
        if eixo["nome"] == eixo_nome:
            for seg in eixo["segmentos"]:
                if seg["segmento"] == valor:
                    return seg
    return None


def eixo_de(rel, nome):
    return next(e for e in rel["eixos"] if e["nome"] == nome)


# -------------------------------------------------------------------------------------------- #
def itens(alvo):
    mod = carregar_componente(alvo)
    contrato, contrato_funil, dados = carregar_contratos(alvo)

    # 1 --------------------------------------------------------------------------------------- #
    try:
        ok = mod.validar_contrato(contrato, dados, contrato_funil, alvo)
    except Exception as exc:  # noqa: BLE001
        ok = "%s: %s" % (type(exc).__name__, exc)
    item("1. contrato do recorte valida contra o contrato de dados E contra o do funil", ok is True, ok)

    # 2 --------------------------------------------------------------------------------------- #
    item("2. os estagios declarados sao EXATAMENTE os do funil (nenhuma segunda lista)",
         contrato["estagios_esperados"] == [e["nome"] for e in contrato_funil["estagios"]]
         and contrato["estagios_esperados"] == dados["funnel_stages"])

    # 3 --------------------------------------------------------------------------------------- #
    divergente = json.loads(json.dumps(contrato))
    for eixo in divergente["eixos"]:
        if eixo["nome"] == "faixa_funcionarios":
            eixo["valores"] = ["150_299"]      # vocabulario proprio inventado
    try:
        mod.validar_contrato(divergente, dados, contrato_funil, alvo)
        r3 = "aceitou"
    except mod.Recusa as exc:
        r3 = exc.motivo
    item("3. eixo com vocabulario proprio (fora do contrato de dados) RECUSA", r3 == "EIXO_DIVERGE_DO_CONTRATO_DE_DADOS", r3)

    # 4 --------------------------------------------------------------------------------------- #
    sem_fonte = json.loads(json.dumps(contrato))
    sem_fonte["eixos"][1]["fonte"] = "FONTE_INVENTADA"
    try:
        mod.validar_contrato(sem_fonte, dados, contrato_funil, alvo)
        r4 = "aceitou"
    except mod.Recusa as exc:
        r4 = exc.motivo
    item("4. eixo apontando para fonte nao declarada RECUSA", r4 == "EIXO_COM_FONTE_NAO_DECLARADA", r4)

    # 5 --------------------------------------------------------------------------------------- #
    derivacao = json.loads(json.dumps(contrato))
    derivacao["eixos"][1]["derivacao"] = "chute"
    try:
        mod.validar_contrato(derivacao, dados, contrato_funil, alvo)
        r5 = "aceitou"
    except mod.Recusa as exc:
        r5 = exc.motivo
    item("5. derivacao de eixo desconhecida RECUSA", r5 == "DERIVACAO_DE_EIXO_DESCONHECIDA", r5)

    # 6 --------------------------------------------------------------------------------------- #
    estagios = json.loads(json.dumps(contrato))
    estagios["estagios_esperados"] = estagios["estagios_esperados"][:-1]
    try:
        mod.validar_contrato(estagios, dados, contrato_funil, alvo)
        r6 = "aceitou"
    except mod.Recusa as exc:
        r6 = exc.motivo
    item("6. lista de estagios divergindo do funil RECUSA", r6 == "ESTAGIOS_DIVERGEM_DO_FUNIL", r6)

    # 7 --------------------------------------------------------------------------------------- #
    fu = json.loads(json.dumps(contrato))
    fu["contrato_de_funil"] = "funil-v9"
    try:
        mod.validar_contrato(fu, dados, contrato_funil, alvo)
        r7 = "aceitou"
    except mod.Recusa as exc:
        r7 = exc.motivo
    item("7. contrato do funil NAO declarado RECUSA", r7 == "CONTRATO_DE_FUNIL_NAO_DECLARADO", r7)

    # 8 --------------------------------------------------------------------------------------- #
    try:
        consultas = mod.montar_consultas(contrato)
        r8 = all(sql.strip().upper().startswith("SELECT") for sql in consultas.values()) \
            and set(consultas) == set(contrato["fontes"])
    except Exception as exc:  # noqa: BLE001
        r8 = "%s: %s" % (type(exc).__name__, exc)
    item("8. consultas montadas do contrato: SO' SELECT e uma por fonte declarada", r8 is True, r8)

    # 9 --------------------------------------------------------------------------------------- #
    item("9. leitura pura pedida no SQL do componente e do funil reusado "
         "(default_transaction_read_only + score com versao)",
         "default_transaction_read_only" in open(os.path.join(alvo, REL_FUNIL), encoding="utf-8").read()
         and "score_version" in mod.montar_consultas(contrato)["PONTUACAO_PRIORITY"]
         and "COALESCE(score_version, '') <> ''" in mod.montar_consultas(contrato)["PONTUACAO_PRIORITY"])

    # 10 -------------------------------------------------------------------------------------- #
    try:
        mod.funil.auditar_fonte({"X": "SELECT 1; DELETE FROM sales_intelligence.organizations"})
        r10 = "aceitou"
    except mod.funil.Recusa as exc:   # a recusa do funil reusado (classe-mae da recusa daqui)
        r10 = exc.motivo
    item("10. auditoria da fonte reprova verbo de escrita (ESCRITA_NO_CODIGO)", r10 == "ESCRITA_NO_CODIGO", r10)

    # 11 -------------------------------------------------------------------------------------- #
    rc, out = _cli_out(alvo, ["--ambiente", "prod"])
    item("11. prod RECUSA por desenho (exit 4 + PRODUCAO_RECUSADA)",
         rc == 4 and "PRODUCAO_RECUSADA" in out, "exit=%s out=%s" % (rc, out[:90]))

    # 12 -------------------------------------------------------------------------------------- #
    rc, out = _cli_out(alvo, ["--ambiente", "dev", "--porta-banco", "ssh root@10.0.0.1 psql"])
    item("12. porta de banco remota RECUSA em dev (exit 3 + BANCO_NAO_E_DEV)",
         rc == 3 and "BANCO_NAO_E_DEV" in out, "exit=%s out=%s" % (rc, out[:90]))

    # 13 -------------------------------------------------------------------------------------- #
    rc, out = _cli_out(alvo, ["--ambiente", "homolog", "--porta-banco", "docker exec -i pg-analytics-seg-acc psql"])
    item("13. homolog sem --confirmo RECUSA (exit 3 + HOMOLOG_SEM_CONFIRMO)",
         rc == 3 and "HOMOLOG_SEM_CONFIRMO" in out, "exit=%s out=%s" % (rc, out[:90]))

    # 14 -------------------------------------------------------------------------------------- #
    rc = _cli(alvo, ["--ambiente", "dev", "--conferir"])
    item("14. --conferir valida os tres contratos sem banco (exit 0)", rc == 0, "exit=%s" % rc)

    # 15 -------------------------------------------------------------------------------------- #
    rc = _cli(alvo, ["--ambiente", "dev", "--planejar"])
    item("15. --planejar declara os dois eixos e as sentinelas sem banco (exit 0)", rc == 0, "exit=%s" % rc)

    # 16 -------------------------------------------------------------------------------------- #
    rc, out = _cli_out(alvo, ["--ambiente", "dev", "--porta-banco",
                              "docker exec -i pg-analytics-seg-acc psql", "--desde", "ontem"])
    item("16. janela invalida RECUSA (exit 2 + JANELA_INVALIDA) antes de tocar o banco",
         rc == 2 and "JANELA_INVALIDA" in out, "exit=%s out=%s" % (rc, out[:90]))

    # -------------------------------------------------------------------------------------- #
    # Derivacao PURA sobre a fixture (sem banco)
    brutas = fixture(contrato_funil)
    rel = montar(mod, contrato, contrato_funil, dados, brutas)

    # 17 -------------------------------------------------------------------------------------- #
    faixa = eixo_de(rel, "faixa_funcionarios")
    esperado = {"150_299": 6, "LT_70": 3, "GT_1000": 1, "SEM_DADO": 1, "FORA_DO_VOCABULARIO": 1}
    obtido = {s["segmento"]: s["organizacoes"] for s in faixa["segmentos"] if s["organizacoes"]}
    item("17. recorte por faixa de funcionarios bate com a conta a mao", obtido == esperado, obtido)

    # 18 -------------------------------------------------------------------------------------- #
    item("18. eixo tem os 8 valores declarados + os sentinelas que aparecem (nada omitido)",
         [s["segmento"] for s in faixa["segmentos"]][:8] == contrato["eixos"][0]["valores"]
         and {s["segmento"] for s in faixa["segmentos"]} == set(contrato["eixos"][0]["valores"]) | {"SEM_DADO", "FORA_DO_VOCABULARIO"})

    # 19 -------------------------------------------------------------------------------------- #
    item("19. SEM_DADO e FORA_DO_VOCABULARIO sao coisas DIFERENTES e a soma fecha com a base",
         segmento(rel, "faixa_funcionarios", "SEM_DADO")["organizacoes"] == 1
         and segmento(rel, "faixa_funcionarios", "FORA_DO_VOCABULARIO")["organizacoes"] == 1
         and sum(s["organizacoes"] for s in faixa["segmentos"]) == 12)

    # 20 -------------------------------------------------------------------------------------- #
    item("20. cobertura declarada por eixo (faixa 10/12 = 83,33% · tier 9/12 = 75,0%)",
         faixa["classificadas"] == 10 and faixa["cobertura_pct"] == 83.33
         and faixa["sem_dado"] == 1 and faixa["fora_do_vocabulario"] == 1
         and eixo_de(rel, "tier_prioridade")["classificadas"] == 9
         and eixo_de(rel, "tier_prioridade")["cobertura_pct"] == 75.0
         and eixo_de(rel, "tier_prioridade")["sem_dado"] == 3,
         (faixa["cobertura_pct"], eixo_de(rel, "tier_prioridade")["cobertura_pct"]))

    # 21 -------------------------------------------------------------------------------------- #
    tier = eixo_de(rel, "tier_prioridade")
    esperado_tier = {"A+": 2, "A": 2, "B": 1, "C": 2, "Nurture": 2, "SEM_DADO": 3}
    obtido_tier = {s["segmento"]: s["organizacoes"] for s in tier["segmentos"]}
    item("21. tier DERIVADO da pontuacao vigente (nao do maior historico): B=1 e A+ NAO fica com a org 3",
         obtido_tier == esperado_tier, obtido_tier)

    # 22 -------------------------------------------------------------------------------------- #
    org3 = segmento(rel, "tier_prioridade", "B")
    item("22. conversao por segmento e indice vs base (150_299: 1/6 = 16,67% · indice 66,68)",
         segmento(rel, "faixa_funcionarios", "150_299")["taxa_conversao_pct"] == 16.67
         and segmento(rel, "faixa_funcionarios", "150_299")["indice_vs_base_pct"] == 66.68
         and segmento(rel, "faixa_funcionarios", "LT_70")["taxa_conversao_pct"] == 33.33
         and segmento(rel, "faixa_funcionarios", "LT_70")["indice_vs_base_pct"] == 133.32
         and rel["global"]["taxa_conversao_pct"] == 25.0
         and org3["taxa_conversao_pct"] == 0.0
         and segmento(rel, "faixa_funcionarios", "GT_1000")["taxa_conversao_pct"] == 0.0
         and segmento(rel, "faixa_funcionarios", "GT_1000")["indice_vs_base_pct"] == 0.0,
         (segmento(rel, "faixa_funcionarios", "150_299")["indice_vs_base_pct"], rel["global"]["taxa_conversao_pct"]))

    # 23 -------------------------------------------------------------------------------------- #
    item("23. segmento sem nenhuma organizacao aparece com taxa null (nao 0) e amostra pequena",
         segmento(rel, "faixa_funcionarios", "UNKNOWN")["organizacoes"] == 0
         and segmento(rel, "faixa_funcionarios", "UNKNOWN")["taxa_conversao_pct"] is None
         and segmento(rel, "faixa_funcionarios", "UNKNOWN")["amostra_pequena"] is True)

    # 24 -------------------------------------------------------------------------------------- #
    item("24. amostra pequena marcada (< amostra_minima=5) e NAO marcada na base grande",
         eixo_de(rel, "tier_prioridade") is not None
         and segmento(rel, "faixa_funcionarios", "150_299")["amostra_pequena"] is False
         and segmento(rel, "faixa_funcionarios", "GT_1000")["amostra_pequena"] is True
         and contrato["amostra_minima"] == 5)

    # 25 -------------------------------------------------------------------------------------- #
    estagios_funil = [e["nome"] for e in montar_global(mod, contrato, contrato_funil, dados, brutas)["estagios"]]
    item("25. os estagios do recorte sao os MESMOS do funil, na mesma ordem (uma derivacao so')",
         [e["nome"] for e in segmento(rel, "faixa_funcionarios", "150_299")["estagios"]] == estagios_funil
         and len(estagios_funil) == 13)

    # 26 -------------------------------------------------------------------------------------- #
    seg_lt = segmento(rel, "faixa_funcionarios", "LT_70")
    por_nome = {e["nome"]: e for e in seg_lt["estagios"]}
    item("26. terminal Won/Lost continua separado dentro do segmento (perdido NAO conta em Won)",
         por_nome["Won"]["alcancadas"] == 1 and por_nome["Lost"]["alcancadas"] == 0
         and por_nome["Won"]["alcancadas"] == seg_lt["won"] and seg_lt["lost"] == 0)

    # 27 -------------------------------------------------------------------------------------- #
    linhas_linear = [(e["nome"], e["alcancadas"]) for e in seg_lt["estagios"] if not e["lateral"]]
    item("27. alcance do recorte e' monotono (invariante herdada do funil)",
         all(a[1] >= b[1] for a, b in zip(linhas_linear, linhas_linear[1:])), linhas_linear)

    # 28 -------------------------------------------------------------------------------------- #
    original = mod.classificar_eixo
    try:
        mod.classificar_eixo = lambda *a, **k: {}
        mod.calcular_relatorio(contrato, contrato_funil, dados, brutas,
                               mod.funil.extrair_base(brutas["BASE_ORGANIZACOES"]),
                               *mod.funil.resolver_evidencia(
                                   contrato_funil, brutas,
                                   mod.funil.extrair_base(brutas["BASE_ORGANIZACOES"])),
                               "dev", {"desde": None, "ate": None}, gerado_em="x")
        r28 = "aceitou"
    except mod.Recusa as exc:
        r28 = exc.motivo
    except TypeError as exc:
        r28 = "TypeError: %s" % exc
    finally:
        mod.classificar_eixo = original
    item("28. MECANISMO: bucket que nao cobre a base RECUSA (RECORTE_NAO_FECHA_COM_A_BASE)",
         r28 == "RECORTE_NAO_FECHA_COM_A_BASE", r28)

    # 29 -------------------------------------------------------------------------------------- #
    rel2 = montar(mod, contrato, contrato_funil, dados, brutas, gerado_em="2027-01-01T23:59:59Z")
    item("29. determinismo: gerado_em diferente NAO muda o hash_do_relatorio (e' a unica diferenca)",
         rel2["hash_do_relatorio"] == rel["hash_do_relatorio"] and len(rel["hash_do_relatorio"]) == 64
         and rel2["gerado_em"] != rel["gerado_em"])

    # 30 -------------------------------------------------------------------------------------- #
    html = mod.emitir_html(rel)
    item("30. HTML auto-contido (sem http/script/link) com todos os segmentos dos dois eixos",
         "http://" not in html and "https://" not in html and "<script" not in html
         and "<link" not in html and len(html) > 800
         and all(s["segmento"] in html for e in rel["eixos"] for s in e["segmentos"])
         and "Indice vs base" in html)

    # 31 -------------------------------------------------------------------------------------- #
    texto = json.dumps(rel, ensure_ascii=False) + html
    ids = [O(i) for i in range(1, 13)]
    item("31. saida sem PII e sem organizacao nominal: nenhum UUID da base, nenhum '@'",
         "@" not in texto and not any(i in texto for i in ids))

    # 32 -------------------------------------------------------------------------------------- #
    item("32. as 10 lacunas declaradas viajam no relatorio e no HTML",
         len(rel["lacunas_declaradas"]) == 10 and len(contrato["lacunas_declaradas"]) == 10
         and "Lacunas declaradas" in html)

    # 33 -------------------------------------------------------------------------------------- #
    item("33. contrato do funil registrado com sha256 proprio (a derivacao recortada e' identificavel)",
         rel["contrato"]["contrato_de_funil"] == "funil-v1" and len(rel["contrato"]["sha256"]) == 64)

    # 34 -------------------------------------------------------------------------------------- #
    janela = montar(mod, contrato, contrato_funil, dados, brutas)
    consultas = mod.montar_consultas(contrato, "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")
    item("34. janela entra na fonte do eixo e na do funil (nenhuma fonte fica sem filtro de tempo)",
         "calculated_at >= '2026-01-01T00:00:00+00:00'" in consultas["PONTUACAO_PRIORITY"]
         and "created_at >= '2026-01-01T00:00:00+00:00'" in consultas["FAIXA_FUNCIONARIOS"]
         and janela["janela"] == {"desde": None, "ate": None})

    return contrato


def montar_global(mod, contrato, contrato_funil, dados, brutas):
    rel = montar(mod, contrato, contrato_funil, dados, brutas)
    return {"estagios": rel["global"]["estagios"]}


def _cli(alvo, args):
    return _cli_out(alvo, args)[0]


def _cli_out(alvo, args):
    """(exit, saida): o MOTIVO da recusa e' medido, nao so' o codigo — codigo igual por acidente
    nao prova guarda (licao do D04/D06/D07: peneira por resultado e' peneira)."""
    comando = [sys.executable, os.path.join(alvo, REL_COMPONENTE), "--raiz", alvo,
               "--contrato", os.path.join(alvo, REL_CONTRATO)] + args
    proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# -------------------------------------------------------------------------------------------- #
def _aplicar_mutacao(origem, destino, mutacao):
    for rel in (DIR_ANALYTICS,):
        os.makedirs(os.path.join(destino, rel), exist_ok=True)
    os.makedirs(os.path.join(destino, "docs", "data"), exist_ok=True)
    shutil.copy2(os.path.join(origem, REL_DADOS), os.path.join(destino, REL_DADOS))
    # A copia leva TAMBEM o funil (dependencia declarada): senao a mutacao no funil nao seria medida.
    for rel in (REL_COMPONENTE, REL_CONTRATO, REL_FUNIL, REL_CONTRATO_FUNIL):
        shutil.copy2(os.path.join(origem, rel), os.path.join(destino, rel))
    mapa = {"contrato": REL_CONTRATO, "componente": REL_COMPONENTE, "funil": REL_FUNIL}
    trocas = mutacao.get("trocas") or [{"arquivo": mutacao["arquivo"], "de": mutacao["de"],
                                       "para": mutacao["para"]}]
    for troca in trocas:
        alvo_arquivo = os.path.join(destino, mapa[troca["arquivo"]])
        with open(alvo_arquivo, "r", encoding="utf-8") as fh:
            texto = fh.read()
        if troca["de"] not in texto:
            return False
        texto = texto.replace(troca["de"], troca["para"])
        with open(alvo_arquivo, "w", encoding="utf-8") as fh:
            fh.write(texto)
    return True


MUTACOES = [
    {"nome": "d1 valor de eixo trocado no contrato (vocabulario proprio)",
     "arquivo": "contrato", "de": '"LT_70", "70_149"', "para": '"LT_70X", "70_149"'},
    {"nome": "d2 segmento por SEMELHANCA em vez de igualdade exata no eixo",
     "arquivo": "componente",
     "de": "    for valor in eixo[\"valores\"]:\n        if funil.normalizar_rotulo(valor) == chave:\n            return valor\n    return None",
     "para": "    for valor in eixo[\"valores\"]:\n        if funil.normalizar_rotulo(valor).startswith(chave[:4]):\n            return valor\n    return None"},
    {"nome": "d3 tier deixa de ser derivado da faixa (derivacao desligada)",
     "arquivo": "componente", "de": 'if eixo["derivacao"] == "faixa_de_pontuacao":',
     "para": 'if False:'},
    {"nome": "d4 pontuacao vigente vira a MAIOR historica (ignora calculated_at)",
     "arquivo": "componente", "de": "chave = (campos[2], valor)\n        if campos[0] not in vigente or chave > vigente[campos[0]][0]:",
     "para": "chave = (valor, valor)\n        if campos[0] not in vigente or chave > vigente[campos[0]][0]:"},
    {"nome": "d5 sentinela FORA_DO_VOCABULARIO fundida com SEM_DADO",
     "arquivo": "componente", "de": 'por_org[org] = "SEM_DADO" if bruto == "" else "FORA_DO_VOCABULARIO"',
     "para": 'por_org[org] = "SEM_DADO"'},
    {"nome": "d6 guarda de producao desligada NOS DOIS pontos (a redundancia tambem e' medida)",
     "trocas": [{"arquivo": "componente", "de": 'if args.ambiente == "prod":', "para": "if False:"},
                {"arquivo": "funil", "de": 'if ambiente == "prod":', "para": "if False:"}]},
    {"nome": "d7 porta remota aceita em dev (guarda do funil reusado neutralizada)",
     "arquivo": "funil",
     "de": "m = RE_CONTAINER_LOCAL.match(porta_banco.strip())",
     "para": "m = RE_CONTAINER_LOCAL.match('docker exec -i pg-analytics-seg-acc psql')"},
    {"nome": "d8 indice vs base deixa de comparar com a base",
     "arquivo": "componente",
     "de": "else round(100.0 * pct_won / base_pct, 2)",
     "para": "else round(100.0 * pct_won / (base_pct + 1), 2)"},
    {"nome": "d9 recorte deixa de fechar com a base (guarda removida)",
     "arquivo": "componente", "de": 'if soma != total_base:', "para": "if False:"},
    {"nome": "d10 amostra_minima zerada no contrato (amostra pequena nunca marcada)",
     "arquivo": "contrato", "de": '"amostra_minima": 5', "para": '"amostra_minima": 1'},
    {"nome": "d11 gerado_em passa a entrar no hash (determinismo quebrado)",
     "arquivo": "componente", "de": 'if k not in ("gerado_em", "hash_do_relatorio")',
     "para": 'if k not in ("hash_do_relatorio",)'},
    {"nome": "d12 alcance cumulativo quebrado no funil reusado",
     "arquivo": "funil", "de": "or (nivel_max.get(org) is not None and nivel_max[org] >= nivel)",
     "para": "or False"},
]


def _rodar_verificador(alvo):
    proc = subprocess.run([sys.executable, os.path.join(AQUI, "verificar_conversao_segmento.py"),
                           "--alvo-dir", alvo], capture_output=True, text=True, timeout=300)
    nomes = []
    for linha in proc.stdout.splitlines():
        if linha.startswith("VERIFICADOR_CONVERSAO_FALHOU"):
            try:
                nomes = json.loads(linha[len("VERIFICADOR_CONVERSAO_FALHOU "):])
            except json.JSONDecodeError:
                nomes = []
    return proc.returncode, nomes


def autoteste(alvo, falhas_base):
    print("== autoteste: %d mutacoes, cada uma tem de REPROVAR um item que o alvo limpo nao reprova =="
          % len(MUTACOES))
    detectadas = 0
    for mut in MUTACOES:
        with tempfile.TemporaryDirectory(prefix="dente-segmento-") as tmp:
            copia = os.path.join(tmp, "repo")
            if not _aplicar_mutacao(alvo, copia, mut):
                print("FALHOU %s (mutacao NAO aplicada — ancora de texto mudou)" % mut["nome"])
                continue
            rc, nomes = _rodar_verificador(copia)
            novos = [n for n in nomes if n not in falhas_base]
            if rc != 0 and novos:
                detectadas += 1
                print("OK    %s -> REPROVOU (%s)" % (mut["nome"], novos[0][:70]))
            else:
                print("FALHOU %s -> passou com a mutacao aplicada (buraco na suite)" % mut["nome"])
    print("AUTOTESTE %d/%d mutacoes detectadas" % (detectadas, len(MUTACOES)))
    return detectadas == len(MUTACOES)


def main():
    parser = argparse.ArgumentParser(description="Verificador offline da conversao por segmento (TRE-W8-E02-T01)")
    parser.add_argument("--alvo-dir", default=RAIZ_PADRAO)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    alvo = os.path.abspath(args.alvo_dir)

    try:
        itens(alvo)
    except Exception as exc:  # noqa: BLE001 — excecao nao tratada e' FALHA, nunca silencio
        item("0. suite roda ate' o fim sem excecao nao tratada", False,
             "%s: %s" % (type(exc).__name__, str(exc)[:160]))
    autoteste_ok = True
    if args.autoteste:
        autoteste_ok = autoteste(alvo, set(FALHAS_ITENS))

    print("RESULTADO: %s (%d itens, %d falhas)" % ("PASS" if FALHAS == 0 else "FALHOU", OK, FALHAS))
    if FALHAS == 0 and autoteste_ok:
        print("VERIFICADOR_CONVERSAO_PASS")
        return 0
    print("VERIFICADOR_CONVERSAO_FALHOU " + json.dumps(FALHAS_ITENS, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
