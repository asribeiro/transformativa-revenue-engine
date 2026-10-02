#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador do BUYING SIGNAL SCORE v1 (card TRE-W5-E03-T01) — suite offline + prova de dente.

Roda SEM banco e SEM rede: mede o que e puro (formula, vocabulario, guarda de escrita, contrato,
idempotencia da chave, CLI/ambiente). O comportamento contra PostgreSQL de verdade e medido pelo
aceite E2E (`scripts/agentes/teste_buying_signal_aceite.sh`).

Uso:
  python3 scripts/agentes/verificar_buying_signal_score.py [--raiz <repo>] [--codigo <modulo.py>]
  python3 scripts/agentes/verificar_buying_signal_score.py --prova-de-dente

Exit: 0 = suite verde (ou dente reprovando como esperado) · 1 = falha · 2 = uso.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

OK = 0
FALHOU = 0
ITENS = []


def item(nome, esperado, obtido):
    global OK, FALHOU
    igual = esperado == obtido
    ITENS.append((nome, esperado, obtido, igual))
    if igual:
        OK += 1
        print("OK     %s (%s)" % (nome, obtido))
    else:
        FALHOU += 1
        print("FALHOU %s (esperado=%r obtido=%r)" % (nome, esperado, obtido))


def carregar(caminho: Path):
    spec = importlib.util.spec_from_file_location("bss_sob_teste", str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class PortaSemente:
    """Porta de banco de mentira: devolve respostas CANNED e registra o SQL que passou pela guarda.

    Nao substitui o aceite E2E: existe para medir o FLUXO do agente (veredito, prova da gravacao,
    desfazer) sem banco, e para provar que toda instrucao passa pela guarda antes de sair.
    """

    def __init__(self, respostas, modulo):
        self.respostas = list(respostas)
        self.modulo = modulo
        self.sqls = []

    def executar(self, sql, permitir_remocao=False):
        self.modulo.validar_sql(sql, permitir_remocao=permitir_remocao)
        self.sqls.append(sql)
        resposta = self.respostas.pop(0) if self.respostas else ""
        if isinstance(resposta, tuple):
            return resposta
        return (0, resposta, "")


def sinais_de_exemplo(agora: datetime):
    def iso(dias):
        return (agora - timedelta(days=dias)).isoformat(timespec="seconds")

    return [
        {"id": "s1", "signal_type": "ERP_CHANGE", "confidence": "0.9", "event_date": iso(10),
         "detected_at": iso(9)},
        {"id": "s2", "signal_type": "HIRING", "confidence": "0.7", "event_date": iso(60),
         "detected_at": iso(60)},
        {"id": "s3", "signal_type": "AI_INITIATIVE", "confidence": None, "event_date": None,
         "detected_at": iso(30)},
    ]


def suite(raiz: Path, caminho_codigo: Path) -> int:
    modulo = carregar(caminho_codigo)
    agora = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    agora_iso = agora.isoformat(timespec="seconds")

    # --- identidade e contrato -----------------------------------------------------------
    contrato = modulo.carregar_contrato_do_agente(raiz)
    item("contrato: score_type", "BUYING_SIGNAL", modulo.SCORE_TYPE)
    item("contrato: score_version", "buying-signal-v1", modulo.SCORE_VERSION)
    item("contrato: score_type casa com o contrato do agente", modulo.SCORE_TYPE,
         contrato["score_type"])
    item("contrato: score_version casa com o contrato do agente", modulo.SCORE_VERSION,
         contrato["score_version"])
    item("contrato: formula casa", modulo.FORMULA, contrato["formula"])
    item("contrato: confianca_padrao casa", modulo.CONFIANCA_PADRAO, contrato["confianca_padrao"])
    item("contrato: limite_sinais casa", modulo.LIMITE_SINAIS, contrato["limite_sinais"])
    item("contrato: validade_dias casa", modulo.VALIDADE_DIAS, contrato["validade_dias"])
    item("contrato: pesos casa", modulo.PESOS_POR_TIPO, contrato["pesos_por_tipo"])
    item("contrato: meia-vida casa", modulo.MEIA_VIDA_DIAS, contrato["meia_vida_dias"])
    agente = modulo.BuyingSignalScore(raiz=raiz, ambiente="dev")
    try:
        agente.conferir_contrato()
        divergencia = "-"
    except Exception as exc:  # noqa: BLE001
        divergencia = str(exc)
    item("contrato: conferir_contrato sem divergencia", "-", divergencia)

    # --- vocabulario fechado (Data Contract) ---------------------------------------------
    tipos_contrato = tuple(json.loads((raiz / "docs/data/data_contract_v1.json").read_text(
        encoding="utf-8"))["vocabularies"]["signal_type"])
    item("vocabulario: 18 tipos no Data Contract", 18, len(tipos_contrato))
    item("vocabulario: pesos cobrem o vocabulario exatamente", sorted(tipos_contrato),
         sorted(modulo.PESOS_POR_TIPO))
    item("vocabulario: categorias cobrem o vocabulario exatamente", sorted(tipos_contrato),
         sorted(modulo.CATEGORIAS_POR_TIPO))
    item("vocabulario: toda categoria tem meia-vida", sorted(set(modulo.CATEGORIAS_POR_TIPO.values())),
         sorted(modulo.MEIA_VIDA_DIAS))
    item("vocabulario: todo peso dentro de (0, 1]",
         True, all(0.0 < p <= 1.0 for p in modulo.PESOS_POR_TIPO.values()))
    item("tipo fora do vocabulario RECUSA",
         "ContratoDivergente", _tipo_fora(modulo, agora))

    # --- formula: forca, decaimento, saturacao ------------------------------------------
    linhas = sinais_de_exemplo(agora)
    calculo = modulo.calcular_score(linhas, agora_iso)
    item("calculo: 3 sinais utilizados", 3, len(calculo["sinais_utilizados"]))
    item("calculo: nada descartado", 0, len(calculo["descartados"]))
    item("calculo: score no intervalo 0..100", True, 0.0 <= calculo["score_value"] <= 100.0)
    item("calculo: sem motivo quando ha sinal", None, calculo["motivo"])
    item("calculo: determinismo (mesma entrada, mesmo score)", calculo["score_value"],
         modulo.calcular_score(linhas, agora_iso)["score_value"])
    item("calculo: entrada vazia -> 0,00", 0.0, modulo.calcular_score([], agora_iso)["score_value"])
    item("calculo: entrada vazia -> motivo SEM_SINAIS", "SEM_SINAIS",
         modulo.calcular_score([], agora_iso)["motivo"])
    item("calculo: confianca ausente usa o padrao", True,
         all(c["confianca"] == modulo.CONFIANCA_PADRAO
             for c in calculo["sinais_utilizados"] if c["confianca_padrao_usada"]))
    item("calculo: confianca padrao contada", 1, calculo["confianca_padrao_usada"])
    item("calculo: soma dos pontos igual a soma declarada", True,
         abs(sum(c["pontos"] for c in calculo["sinais_utilizados"])
             - sum(r["pontos"] for r in modulo.calcular_score(linhas, agora_iso)["sinais_utilizados"])) < 1e-9)
    item("calculo: saturacao (forca < soma dos pontos)", True,
         calculo["score_value"] < 100 * sum(c["pontos"] for c in calculo["sinais_utilizados"]))
    # monotonicidade: acrescentar sinal NAO reduz o score
    mais = linhas + [{"id": "s4", "signal_type": "FUNDING", "confidence": "0.9",
                      "event_date": agora.isoformat(timespec="seconds"), "detected_at": None}]
    item("calculo: mais sinal nunca reduz o score", True,
         modulo.calcular_score(mais, agora_iso)["score_value"] >= calculo["score_value"])
    # decaimento: sinal antigo vale menos que o mesmo sinal novo
    velho = [{"id": "a", "signal_type": "ERP_CHANGE", "confidence": "1",
              "event_date": (agora - timedelta(days=720)).isoformat(timespec="seconds"),
              "detected_at": None}]
    novo = [dict(velho[0], id="b", event_date=agora.isoformat(timespec="seconds"))]
    item("decaimento: 720 dias vale menos que hoje", True,
         modulo.calcular_score(velho, agora_iso)["score_value"]
         < modulo.calcular_score(novo, agora_iso)["score_value"])
    item("decaimento: 1 meia-vida ~ metade dos pontos", True,
         abs(modulo.decaimento(120, "TECNOLOGIA") - 0.5) < 1e-9)
    item("decaimento: idade 0 -> 1.0", 1.0, modulo.decaimento(0, "TECNOLOGIA"))
    item("decaimento: data futura nao decai", 1.0,
         modulo.calcular_score([{"id": "f", "signal_type": "GROWTH", "confidence": "1",
                                 "event_date": (agora + timedelta(days=5)).isoformat(timespec="seconds"),
                                 "detected_at": None}], agora_iso)["sinais_utilizados"][0]["decaimento"])
    item("decaimento: data futura registrada", True,
         modulo.calcular_score([{"id": "f", "signal_type": "GROWTH", "confidence": "1",
                                 "event_date": (agora + timedelta(days=5)).isoformat(timespec="seconds"),
                                 "detected_at": None}], agora_iso)["sinais_utilizados"][0]["data_futura"])
    item("categoria sem meia-vida RECUSA",
         "ContratoDivergente", _categoria_sem_meia_vida(modulo))

    # --- descartes e limite --------------------------------------------------------------
    sem_data = [{"id": "x", "signal_type": "GROWTH", "confidence": "1",
                 "event_date": None, "detected_at": None}]
    d = modulo.calcular_score(sem_data, agora_iso)
    item("descarte: sem data nenhuma e descartado", 1, len(d["descartados"]))
    item("descarte: motivo DATA_AUSENTE", "DATA_AUSENTE", d["descartados"][0]["motivo"])
    item("descarte: sem sinal utilizavel o motivo e SEM_SINAIS", "SEM_SINAIS", d["motivo"])
    conf = [{"id": "y", "signal_type": "GROWTH", "confidence": "1.7",
             "event_date": agora.isoformat(timespec="seconds"), "detected_at": None}]
    item("descarte: confianca fora de 0..1", "CONFIANCA_FORA_DA_FAIXA",
         modulo.calcular_score(conf, agora_iso)["descartados"][0]["motivo"])
    muitos = [{"id": "m%02d" % i, "signal_type": "ERP_CHANGE", "confidence": "1",
               "event_date": agora.isoformat(timespec="seconds"), "detected_at": None}
              for i in range(25)]
    m = modulo.calcular_score(muitos, agora_iso)
    item("limite: no maximo 10 sinais contribuem", 10, len(m["sinais_utilizados"]))
    item("limite: excedente registrado", 15, len(m["sinais_ignorados"]))
    item("limite: score nunca passa de 100", True, m["score_value"] <= 100.0)
    item("limite: os mais fortes entram primeiro", True,
         all(a["pontos"] >= b["pontos"] for a, b in
             zip(m["sinais_utilizados"], m["sinais_utilizados"][1:])))

    # --- guarda de escrita ---------------------------------------------------------------
    caso = _guarda(modulo)
    item("guarda: DDL recusado", "recusado", caso["ddl"])
    item("guarda: INSERT em signals recusado", "recusado", caso["insert_signals"])
    item("guarda: INSERT em organizations recusado", "recusado", caso["insert_orgs"])
    item("guarda: UPDATE em scores recusado (score historico)", "recusado", caso["update_scores"])
    item("guarda: DELETE sem --confirmo recusado", "recusado", caso["delete_sem_confirmo"])
    item("guarda: DELETE com --confirmo aceito", "aceito", caso["delete_com_confirmo"])
    item("guarda: INSERT de score sem score_version recusado", "recusado", caso["sem_versao"])
    item("guarda: INSERT de score sem organization_id recusado", "recusado", caso["sem_organizacao"])
    item("guarda: INSERT completo aceito", "aceito", caso["completo"])
    item("guarda: 'drop' dentro de um texto nao e DDL", "aceito", caso["texto_com_drop"])
    item("guarda: escrita em tabela nao declarada recusada", "recusado", caso["tabela_estranha"])

    # --- SQL montado x DDL ---------------------------------------------------------------
    ddl = (raiz / "db/migrations/0001_sales_intelligence_v1.sql").read_text(encoding="utf-8")
    colunas_scores = _colunas_ddl(ddl, "scores")
    colunas_agent_runs = _colunas_ddl(ddl, "agent_runs")
    colunas_sync = _colunas_ddl(ddl, "sync_events")
    valores = {"organization_id": "org-1", "score_value": 71.5,
               "calculated_at": agora_iso, "valid_until": agora_iso,
               "inputs": {}, "explanation": {}}
    colunas, expressoes = modulo.montar_linha_score("score-1", valores)
    item("SQL: colunas do INSERT de scores existem no DDL", sorted(colunas), sorted(
        c for c in colunas if c in colunas_scores))
    item("SQL: INSERT de scores tem score_version (contrato 8)", True, "score_version" in colunas)
    item("SQL: INSERT de scores tem inputs e explanation do DDL", True,
         "inputs" in colunas and "explanation" in colunas)
    item("SQL: paridade colunas x valores", len(colunas), len(expressoes))
    sql_gravacao = modulo.sql_gravar_score("score-1", "sync-1", "chave-1", valores)
    item("SQL: gravacao e uma transacao (BEGIN/COMMIT)", True,
         sql_gravacao.strip().startswith("BEGIN;") and sql_gravacao.strip().endswith("COMMIT;"))
    item("SQL: gravacao passa pela guarda", "aceito", _tenta(modulo.validar_sql, sql_gravacao))
    item("SQL: gravacao ancora no sync_events (claim)", True, "idempotency_key" in sql_gravacao)
    item("SQL: colunas do sync_events existem no DDL", sorted(
        set(re.findall(r"INSERT INTO sales_intelligence\.sync_events \(([^)]*)\)", sql_gravacao)[0]
            .replace("\n", " ").split(", "))), sorted(
        set(re.findall(r"INSERT INTO sales_intelligence\.sync_events \(([^)]*)\)", sql_gravacao)[0]
            .replace("\n", " ").split(", "))) if False else sorted(
        c for c in set(re.findall(r"INSERT INTO sales_intelligence\.sync_events \(([^)]*)\)",
                                  sql_gravacao)[0].replace("\n", " ").split(", ")) if c in colunas_sync))
    sql_execucao = modulo.sql_registrar_execucao("r1", "cid-1", "org-1", "COMPLETED", "resumo",
                                                "hash", 71.5, "score-1")
    item("SQL: agent_runs guarda a ancora do score", True, '"score_id"' in sql_execucao)
    item("SQL: colunas do agent_runs existem no DDL", True, all(
        c.strip() in colunas_agent_runs for c in
        re.findall(r"INSERT INTO sales_intelligence\.agent_runs \(([^)]*)\)", sql_execucao)[0]
        .replace("\n", " ").split(",")))
    item("SQL: leitura dos sinais nao e escrita",
         "aceito", _tenta(modulo.validar_sql, modulo.sql_sinais_da_organizacao("org-1")))
    item("SQL: desfazer exige DELETE (recusado sem permissao)",
         "recusado", _tenta(modulo.validar_sql, modulo.sql_desfazer(["s1"], "cid")))

    # --- chave de idempotencia -----------------------------------------------------------
    h1 = modulo.hash_das_entradas("org-1", calculo)
    h2 = modulo.hash_das_entradas("org-1", modulo.calcular_score(linhas, agora_iso))
    item("idempotencia: hash determinista", h1, h2)
    item("idempotencia: hash muda com sinal novo", True,
         h1 != modulo.hash_das_entradas("org-1", modulo.calcular_score(mais, agora_iso)))
    item("idempotencia: chave carrega tipo, empresa e hash", "score:BUYING_SIGNAL:org-1:" + h1,
         modulo.chave_idempotencia("org-1", h1))

    # --- ambiente e CLI ------------------------------------------------------------------
    item("ambiente: prod recusado",
         "RecusaDeAmbiente", _tenta_ambiente(modulo, "prod", raiz))
    item("ambiente: ambiente nao declarado recusado",
         "RecusaDeAmbiente", _tenta_ambiente(modulo, "qa", raiz))
    item("ambiente: homolog aceito", "homolog", _tenta_ambiente(modulo, "homolog", raiz))
    item("ambiente: dev aceito", "dev", _tenta_ambiente(modulo, "dev", raiz))
    codigo_prod = modulo.main(["--ambiente", "prod", "--organizacao", "org-1",
                               "--prefixo", "psql", "--raiz", str(raiz)])
    item("CLI: prod sai com exit 4", 4, codigo_prod)
    codigo_uso = modulo.main(["--raiz", str(raiz)])
    item("CLI: sem alvo sai com exit 2 (uso)", 2, codigo_uso)
    codigo_planejar = modulo.main(["--planejar", "--organizacao", "org-1", "--raiz", str(raiz),
                                  "--relatorio", str(Path(tempfile.mkdtemp()) / "p.json")])
    item("CLI: --planejar calcula sem porta", 0, codigo_planejar)

    # --- fluxo com porta de mentira (sem banco) -----------------------------------------
    from_morta = PortaSemente(["0", "1", "1"], modulo)
    ag = modulo.BuyingSignalScore(porta=from_morta, raiz=raiz, ambiente="dev",
                                 correlation_id="11111111-1111-4111-8111-111111111111",
                                 relogio=lambda: agora_iso)
    ag.porta = from_morta
    ag._ler_sinais = lambda organizacao_id: linhas  # leitura injetada: quem le e o E2E
    ag.identificar_alvo = lambda organizacao_id: {"encontrada": True,
                                                 "organizacao_id": organizacao_id}
    rel = ag.rodar("org-1")
    item("fluxo: veredito CALCULADO", "CALCULADO", rel["resultados"][0]["veredito"])
    item("fluxo: prova da gravacao lida", "1", rel["prova_da_gravacao"])
    item("fluxo: toda instrucao passou pela guarda", True,
         all("INSERT" in s or "SELECT" in s or "BEGIN" in s or "COMMIT" in s or "UPDATE" in s
             for s in from_morta.sqls))
    item("fluxo: nenhuma escrita em signals/organizations",
         True, not any(re.search(r"(INSERT INTO|UPDATE|DELETE FROM)\s+sales_intelligence\.(signals|organizations)",
                                s, re.I) for s in from_morta.sqls))
    from_morta2 = PortaSemente(["0", "0"], modulo)
    ag2 = modulo.BuyingSignalScore(porta=from_morta2, raiz=raiz, ambiente="dev",
                                  correlation_id="11111111-1111-4111-8111-111111111111",
                                  relogio=lambda: agora_iso)
    ag2._ler_sinais = lambda organizacao_id: linhas
    ag2.identificar_alvo = lambda organizacao_id: {"encontrada": True,
                                                  "organizacao_id": organizacao_id}
    rel2 = ag2.rodar("org-1")
    item("fluxo: replay nao grava de novo", "JA_CALCULADO", rel2["resultados"][0]["veredito"])
    item("fluxo: replay com 0 gravados", 0, rel2["gravados"])
    item("fluxo: empresa ausente RECUSA",
         "RECUSADA", _empresa_ausente(modulo, raiz, agora_iso, PortaSemente(["", ], modulo)))

    # --- codigo sem rede e sem LLM -------------------------------------------------------
    fonte = caminho_codigo.read_text(encoding="utf-8")
    item("codigo: sem requests/urllib/socket", False,
         bool(re.search(r"\b(import\s+requests|import\s+urllib|import\s+socket|http\.client)", fonte)))
    item("codigo: um unico subprocess.run (a porta)", 1, len(re.findall(r"subprocess\.run", fonte)))
    item("codigo: nenhuma chamada de LLM", False, bool(re.search(r"(openai|anthropic|completions)", fonte, re.I)))
    item("codigo: nenhuma leitura de variavel de ambiente secreta", False,
         bool(re.search(r"os\.environ", fonte)))

    print("\n== SUITE %d itens / %d falhas" % (OK + FALHOU, FALHOU))
    return 0 if FALHOU == 0 else 1


# ---------------------------------------------------------------------------------------
# Auxiliares de medicao
# ---------------------------------------------------------------------------------------
def _tenta(funcao, *args, **kwargs):
    try:
        funcao(*args, **kwargs)
        return "aceito"
    except Exception:  # noqa: BLE001
        return "recusado"


def _tenta_ambiente(modulo, ambiente, raiz):
    agente = modulo.BuyingSignalScore(raiz=raiz, ambiente=ambiente)
    try:
        return agente.conferir_ambiente()
    except modulo.RecusaDeAmbiente:
        return "RecusaDeAmbiente"


def _tipo_fora(modulo, agora):
    try:
        modulo.calcular_score([{"id": "z", "signal_type": "MOON_SHOT", "confidence": "1",
                                "event_date": agora.isoformat(timespec="seconds"),
                                "detected_at": None}], agora.isoformat(timespec="seconds"))
        return "-"
    except modulo.ContratoDivergente:
        return "ContratoDivergente"


def _categoria_sem_meia_vida(modulo):
    try:
        modulo.decaimento(10, "CATEGORIA_INVENTADA")
        return "-"
    except modulo.ContratoDivergente:
        return "ContratoDivergente"


def _guarda(modulo):
    resultados = {}
    resultados["ddl"] = _tenta(modulo.validar_sql, "ALTER TABLE sales_intelligence.scores ADD COLUMN x INT;")
    resultados["insert_signals"] = _tenta(modulo.validar_sql, "INSERT INTO sales_intelligence.signals (id) VALUES ('a');")
    resultados["insert_orgs"] = _tenta(modulo.validar_sql, "INSERT INTO sales_intelligence.organizations (id) VALUES ('a');")
    resultados["update_scores"] = _tenta(modulo.validar_sql, "UPDATE sales_intelligence.scores SET score_value = 1;")
    resultados["delete_sem_confirmo"] = _tenta(modulo.validar_sql, "DELETE FROM sales_intelligence.scores WHERE id = 'a';")
    resultados["delete_com_confirmo"] = _tenta(modulo.validar_sql, "DELETE FROM sales_intelligence.scores WHERE id = 'a';", permitir_remocao=True)
    resultados["sem_versao"] = _tenta(modulo.validar_sql, "INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value) VALUES ('a','b','BUYING_SIGNAL',1);")
    resultados["sem_organizacao"] = _tenta(modulo.validar_sql, "INSERT INTO sales_intelligence.scores (id, score_type, score_value, score_version) VALUES ('a','BUYING_SIGNAL',1,'v');")
    resultados["completo"] = _tenta(modulo.validar_sql, "INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version) VALUES ('a','b','BUYING_SIGNAL',1,'buying-signal-v1');")
    resultados["texto_com_drop"] = _tenta(modulo.validar_sql, "INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version) VALUES ('a','b','BUYING_SIGNAL',1,'buying-signal-v1'); -- 'drop de receita'")
    resultados["tabela_estranha"] = _tenta(modulo.validar_sql, "INSERT INTO public.outra (id) VALUES ('a');")
    return resultados


def _colunas_ddl(ddl: str, tabela: str) -> set:
    bloco = re.search(r"CREATE TABLE sales_intelligence\.%s \((.*?)\n\);" % tabela, ddl, re.S)
    if not bloco:
        raise SystemExit("FALHOU tabela %s ausente do DDL" % tabela)
    return {linha.strip().split()[0] for linha in bloco.group(1).splitlines() if linha.strip()}


def _empresa_ausente(modulo, raiz, agora_iso, porta):
    agente = modulo.BuyingSignalScore(porta=porta, raiz=raiz, ambiente="dev",
                                      relogio=lambda: agora_iso)
    agente.identificar_alvo = lambda organizacao_id: {"encontrada": False,
                                                     "organizacao_id": organizacao_id}
    return agente.rodar("org-1")["resultados"][0]["veredito"]


# ---------------------------------------------------------------------------------------
# Prova de dente: mutacao tem de REPROVAR o item esperado
# ---------------------------------------------------------------------------------------
MUTACOES = (
    ("peso-de-tipo-zero", "PESOS_POR_TIPO[\"ERP_CHANGE\"] = 0.95",
     'PESOS_POR_TIPO["ERP_CHANGE"] = 0.0', "calculo: score no intervalo 0..100"),
    ("limite-de-sinais-infinito", "LIMITE_SINAIS = 10", "LIMITE_SINAIS = 1000",
     "limite: excedente registrado"),
    ("decaimento-ignorado", "return 0.5 ** (idade_dias / meia_vida)", "return 1.0",
     "decaimento: 720 dias vale menos que hoje"),
    ("score-deixa-de-saturar", "forca = 1.0 - restante", "forca = sum(c[\"pontos\"] for c in usados)",
     "limite: score nunca passa de 100"),
    ("versao-sem-verificacao", "if not 0.0 <= confianca <= 1.0:", "if False:",
     "descarte: confianca fora de 0..1"),
    ("guarda-aceita-update", 'if operacao == "update":', 'if operacao == "insert_nunca":',
     "guarda: UPDATE em scores recusado (score historico)"),
    ("guarda-aceita-signals", "if tabela in (TABELA_SINAIS, TABELA_ORGANIZACOES):",
     "if tabela in ():", "guarda: INSERT em signals recusado"),
    ("prod-permitido", 'AMBIENTES_PERMITIDOS = ("dev", "homolog")',
     'AMBIENTES_PERMITIDOS = ("dev", "homolog", "prod")', "CLI: prod sai com exit 4"),
    ("insert-sem-versao", '"score_type", "score_value",\n                                "score_version") if c not in colunas',
     '"score_type", "score_value") if c not in colunas', "guarda: INSERT de score sem score_version recusado"),
    ("data-ausente-aceita", 'raise ValueError("DATA_AUSENTE")', "raise ValueError(\"X\")",
     "descarte: motivo DATA_AUSENTE"),
)


def prova_de_dente(raiz: Path, caminho_codigo: Path) -> int:
    fonte = caminho_codigo.read_text(encoding="utf-8")
    dir_tmp = Path(tempfile.mkdtemp(prefix="bss-dente-"))
    reprovacoes = 0
    for nome, de, para, item_esperado in MUTACOES:
        if de not in fonte:
            print("FALHOU dente %s: ancora da mutacao nao encontrada no codigo" % nome)
            reprovacoes += 1
            continue
        copia = dir_tmp / ("mut_%s.py" % nome)
        copia.write_text(fonte.replace(de, para, 1), encoding="utf-8")
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--raiz", str(raiz),
                            "--codigo", str(copia)], capture_output=True, text=True)
        linha = [l for l in p.stdout.splitlines() if l.startswith("FALHOU %s " % item_esperado)]
        if p.returncode != 0 and linha:
            print("OK     dente %s: reprovou o item esperado (%s)" % (nome, item_esperado))
        else:
            print("FALHOU dente %s: NAO reprovou o item esperado %s (exit=%d)"
                  % (nome, item_esperado, p.returncode))
            reprovacoes += 1
    # Controle negativo: mutacao INERTE (nao muda comportamento) nao pode reprovar a suite
    inerte = dir_tmp / "mut_inerte.py"
    inerte.write_text(fonte.replace("CONFIANCA_PADRAO = 0.5", "CONFIANCA_PADRAO = 0.5  # inerte", 1),
                      encoding="utf-8")
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--raiz", str(raiz),
                        "--codigo", str(inerte)], capture_output=True, text=True)
    if p.returncode == 0:
        print("OK     controle negativo: mutacao inerte NAO reprova a suite")
    else:
        print("FALHOU controle negativo: mutacao inerte reprovou a suite (dente nao e' honesto)")
        reprovacoes += 1
    shutil.rmtree(dir_tmp, ignore_errors=True)
    print("\n== DENTE %d/%d%s" % (len(MUTACOES) + 1 - reprovacoes, len(MUTACOES) + 1,
                                  " OK" if reprovacoes == 0 else " FALHOU"))
    return 0 if reprovacoes == 0 else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Verificador do Buying Signal Score v1")
    p.add_argument("--raiz", default=str(Path(__file__).resolve().parents[2]))
    p.add_argument("--codigo", default=None)
    p.add_argument("--prova-de-dente", action="store_true")
    args = p.parse_args(argv)
    raiz = Path(args.raiz)
    caminho = Path(args.codigo) if args.codigo else \
        raiz / "hermes/agents/buying_signal/buying_signal_score.py"
    if not caminho.is_file():
        print("FALHOU codigo ausente: %s" % caminho)
        return 2
    if args.prova_de_dente:
        return prova_de_dente(raiz, caminho)
    return suite(raiz, caminho)


if __name__ == "__main__":
    sys.exit(main())
