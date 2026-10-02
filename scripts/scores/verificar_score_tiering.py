#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do Tiering v1 (card TRE-W5-E06-T01) — sem banco, sem rede.

Mede o que o componente DECLARA: as faixas lidas do Data Contract (nenhum limite em forma
executavel no codigo), a classificacao deterministica (fronteiras fechadas), a politica de ausencia
(sem PRIORITY nao existe tier), o vencimento do PRIORITY, o hash de idempotencia, a guarda de escrita
(tier NAO escreve em scores) e o SQL de leitura/gravacao. A prova de banco e o aceite E2E
(`scripts/scores/teste_tiering_aceite.sh`), nao esta suite.

Uso:
  python3 scripts/scores/verificar_score_tiering.py              # itens
  python3 scripts/scores/verificar_score_tiering.py --autoteste  # mutacoes do proprio codigo
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MODULO = RAIZ / "hermes/scores/tiering/tiering.py"
CONTRATO_DO_SCORE = "hermes/scores/tiering/score-tiering-v1.json"
CONTRATO_DE_DADOS = "docs/data/data_contract_v1.json"
DOC_CONTRATO = RAIZ / "docs/data/DATA_CONTRACT_V1.md"

ORGANIZACAO = "11111111-1111-1111-1111-111111111111"
AGORA = "2026-10-02T12:00:00+00:00"

ITENS = []


def item(codigo, descricao):
    def decorador(funcao):
        ITENS.append((codigo, descricao, funcao))
        return funcao
    return decorador


def exigir(condicao, mensagem):
    if not condicao:
        raise AssertionError(mensagem)


# ---------------------------------------------------------------------------------------
# Carga do modulo sob teste (e de copias mutadas)
# ---------------------------------------------------------------------------------------
def carregar_modulo(caminho=MODULO, nome=None):
    nome = nome or "tiering_sob_teste_%s" % uuid.uuid4().hex[:8]
    spec = importlib.util.spec_from_file_location(nome, str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def arvore_de_prova(destino: Path) -> Path:
    """Copia o minimo para o modulo carregar: o codigo e os dois contratos."""
    (destino / "hermes/scores/tiering").mkdir(parents=True, exist_ok=True)
    (destino / "docs/data").mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODULO, destino / "hermes/scores/tiering/tiering.py")
    shutil.copy2(RAIZ / CONTRATO_DO_SCORE, destino / CONTRATO_DO_SCORE)
    shutil.copy2(RAIZ / CONTRATO_DE_DADOS, destino / CONTRATO_DE_DADOS)
    return destino


M = carregar_modulo()


# ---------------------------------------------------------------------------------------
# Porta falsa: simula o PostgreSQL e GUARDA o SQL executado (a prova sai do SQL, nao do texto)
# ---------------------------------------------------------------------------------------
class PortaFalsa:
    def __init__(self, organizacao=ORGANIZACAO, prioridade=None, grava=True):
        self.organizacao = organizacao
        self.prioridade = prioridade
        self.grava = grava
        self.sqls = []

    def executar(self, sql, permitir_remocao=False):
        M.validar_sql(sql, permitir_remocao=permitir_remocao)
        self.sqls.append(sql)
        if "jsonb_build_object" in sql and "score_type = 'PRIORITY'" in sql:
            if self.prioridade is None:
                return 0, "", ""
            return 0, json.dumps(self.prioridade) + "\n", ""
        if "INSERT INTO sales_intelligence.sync_events" in sql:
            return 0, ("1\n" if self.grava else "0\n"), ""
        if "INSERT INTO sales_intelligence.agent_runs" in sql:
            return 0, "1\n", ""
        if "sales_intelligence.organizations" in sql:
            return 0, (self.organizacao + "\n") if self.organizacao else "", ""
        if "jsonb_extract_path_text(output, 'registro_id')" in sql:
            return 0, "registro-da-rodada\n", ""
        if "SELECT COUNT(*) FROM sales_intelligence.sync_events" in sql:
            return 0, "1\n", ""
        return 0, "\n", ""

    def sqls_de(self, fragmento):
        return [sql for sql in self.sqls if fragmento in sql]

    def insert_do_registro(self):
        achados = self.sqls_de("INSERT INTO sales_intelligence.sync_events")
        exigir(achados, "nenhum INSERT do registro de tier foi executado")
        return achados[0]


class PortaExplosiva:
    """Se `--planejar`/`--faixas` encostarem no banco, este item reprova."""

    def executar(self, sql, permitir_remocao=False):
        raise AssertionError("modo sem conexao abriu conexao com o banco")


def prioridade(valor="86.45", score_id="score-prio-1", validade="2026-11-01T12:00:00+00:00"):
    return {"score_type": "PRIORITY", "score_id": score_id, "score_value": valor,
            "score_version": "priority-v1", "calculated_at": "2026-10-02T11:00:00+00:00",
            "valid_until": validade}


def faixas_do_contrato(raiz=RAIZ):
    return M.faixas_do_data_contract(raiz)


# ---------------------------------------------------------------------------------------
# Itens — contrato e faixas
# ---------------------------------------------------------------------------------------
@item("C1", "codigo e contrato do card nao divergem (conferir_contrato)")
def _c1():
    agente = M.Tiering(porta=PortaFalsa(), raiz=RAIZ)
    contrato = agente.conferir_contrato()
    exigir(contrato.get("tier_version") == "tiering-v1", "tier_version errado no contrato do card")
    exigir(contrato.get("score_lido") == "PRIORITY", "o contrato do card nao declara PRIORITY")


@item("C2", "faixas = as do Data Contract (5 faixas, os limites do doc §8)")
def _c2():
    faixas = faixas_do_contrato()
    exigir([f["nome"] for f in faixas] == ["A+", "A", "B", "C", "Nurture"],
           "faixas do Data Contract fora do esperado: %s" % [f["nome"] for f in faixas])
    esperado = [("A+", Decimal("90"), Decimal("100")), ("A", Decimal("80"), Decimal("89.99")),
                ("B", Decimal("65"), Decimal("79.99")), ("C", Decimal("50"), Decimal("64.99")),
                ("Nurture", Decimal("0"), Decimal("49.99"))]
    obtido = [(f["nome"], f["min"], f["max"]) for f in faixas]
    exigir(obtido == esperado, "limites das faixas divergem do contrato: %s" % obtido)
    doc = DOC_CONTRATO.read_text(encoding="utf-8")
    for nome in ("A+", "Nurture"):
        exigir(nome in doc, "faixa %r nao aparece no Data Contract escrito" % nome)


@item("C3", "nenhum limite de faixa (nem nome de faixa) em forma executavel no codigo")
def _c3():
    arvore = ast.parse(Path(M.__file__).read_text(encoding="utf-8"))
    limites = {str(f["min"]) for f in faixas_do_contrato()} | {str(f["max"]) for f in faixas_do_contrato()}
    proibidos = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and isinstance(no.value, (int, float)) \
                and not isinstance(no.value, bool):
            texto = str(no.value)
            if texto in limites or texto.rstrip("0").rstrip(".") in {l.rstrip("0").rstrip(".") for l in limites}:
                proibidos.add("numero %s" % texto)
        if isinstance(no, ast.Constant) and isinstance(no.value, str) \
                and no.value in {"A+", "A", "B", "C", "Nurture"}:
            proibidos.add("nome de faixa %r" % no.value)
    exigir(not proibidos, "limite/nome de faixa escrito no codigo: %s" % sorted(proibidos))


@item("C4", "escala derivada das faixas do contrato (nao escrita no codigo)")
def _c4():
    minimo, maximo = M.escala_das_faixas(faixas_do_contrato())
    exigir((minimo, maximo) == (Decimal("0"), Decimal("100")),
           "escala derivada inesperada: %s..%s" % (minimo, maximo))
    fonte = Path(M.__file__).read_text(encoding="utf-8")
    exigir("ESCOLA" not in fonte.upper() and "LIMITES" not in fonte.upper(),
           "constante de escala/limite declarada no codigo")


@item("C5", "faixas com lacuna ou sobreposicao RECUSAM (contrato incoerente)")
def _c5():
    quebradas = [{"nome": "A+", "min": Decimal("90"), "max": Decimal("100")},
                 {"nome": "A", "min": Decimal("80"), "max": Decimal("89.99")},
                 {"nome": "Nurture", "min": Decimal("0"), "max": Decimal("49.99")}]
    try:
        M.conferir_faixas(quebradas)
    except M.ContratoDivergente as exc:
        exigir("lacuna" in str(exc) or "sobreposicao" in str(exc), "recusa sem motivo claro: %s" % exc)
        return
    raise AssertionError("faixas com lacuna passaram pela conferencia")


# ---------------------------------------------------------------------------------------
# Itens — classificacao (fronteiras fechadas, determinismo)
# ---------------------------------------------------------------------------------------
@item("N1", "fronteira de cada faixa classifica certo (90=A+, 89.99=A, 80=A, 79.99=B, 65=B, 64.99=C, 50=C, 49.99=Nurture, 0=Nurture, 100=A+)")
def _n1():
    faixas = faixas_do_contrato()
    esperado = [("90", "A+"), ("89.99", "A"), ("80", "A"), ("79.99", "B"), ("65", "B"),
                ("64.99", "C"), ("50", "C"), ("49.99", "Nurture"), ("0", "Nurture"),
                ("100", "A+"), ("86.45", "A")]
    for valor, tier in esperado:
        calculo = M.classificar(prioridade(valor), faixas, AGORA)
        exigir(calculo["tier"] == tier,
               "valor %s devia cair em %s e caiu em %s" % (valor, tier, calculo["tier"]))
        exigir(calculo["faixa"]["nome"] == tier, "faixa do calculo != tier")


@item("N2", "empresa SEM PRIORITY RECUSA (SEM_PRIORITY) e nao registra nada")
def _n2():
    porta = PortaFalsa(prioridade=None)
    relatorio = M.Tiering(porta=porta, raiz=RAIZ, ambiente="dev").rodar(ORGANIZACAO)
    exigir(relatorio["por_veredito"] == {"RECUSADA": 1}, "sem PRIORITY nao recusou: %s" % relatorio["por_veredito"])
    exigir(relatorio["resultados"][0]["motivo"] == "SEM_PRIORITY", "motivo != SEM_PRIORITY")
    exigir(relatorio["gravados"] == 0, "gravou registro sem PRIORITY")
    exigir(not porta.sqls_de("INSERT INTO sales_intelligence.sync_events"),
           "escreveu registro sem PRIORITY")
    exigir(porta.sqls_de("INSERT INTO sales_intelligence.agent_runs"), "a recusa nao foi auditada")


@item("N3", "PRIORITY VENCIDO RECUSA (PRIORITY_VENCIDO): evidencia velha nao classifica")
def _n3():
    vencido = prioridade(validade="2026-09-01T12:00:00+00:00")
    porta = PortaFalsa(prioridade=vencido)
    relatorio = M.Tiering(porta=porta, raiz=RAIZ, ambiente="dev").rodar(ORGANIZACAO)
    exigir(relatorio["resultados"][0]["motivo"] == "PRIORITY_VENCIDO",
           "vencido nao recusou: %s" % relatorio["resultados"][0])
    exigir(relatorio["gravados"] == 0, "gravou com PRIORITY vencido")


@item("N4", "PRIORITY fora da escala do contrato RECUSA (nao inventa faixa)")
def _n4():
    calculo = M.classificar(prioridade("101"), faixas_do_contrato(), AGORA)
    exigir(calculo["motivo"] == "PRIORITY_FORA_DA_FAIXA", "101 nao recusou: %s" % calculo["motivo"])
    exigir(calculo["tier"] is None, "101 recebeu tier")


@item("N5", "PRIORITY ilegivel RECUSA (nao classifica texto)")
def _n5():
    calculo = M.classificar(prioridade("oitenta"), faixas_do_contrato(), AGORA)
    exigir(calculo["motivo"] == "PRIORITY_ILEGIVEL", "valor ilegivel nao recusou: %s" % calculo["motivo"])


@item("N6", "deterministico: Decimal de 2 casas, mesma entrada -> mesmo tier")
def _n6():
    faixas = faixas_do_contrato()
    a = M.classificar(prioridade("89.994"), faixas, AGORA)
    b = M.classificar(prioridade("89.994"), faixas, AGORA)
    exigir(a["tier"] == b["tier"], "duas rodadas iguais deram tiers diferentes")
    exigir(a["score_lido"]["score_value"] == "89.99", "arredondamento != 2 casas (ROUND_HALF_UP)")
    exigir(a["tier"] == "A", "89.99 devia ser A: %s" % a["tier"])


# ---------------------------------------------------------------------------------------
# Itens — idempotencia e SQL
# ---------------------------------------------------------------------------------------
@item("I1", "a chave de idempotencia e a ENTRADA (muda com o score, nao com o relogio)")
def _i1():
    faixas = faixas_do_contrato()
    p1 = prioridade("86.45", score_id="a")
    p2 = prioridade("86.45", score_id="b")
    h1 = M.hash_da_entrada(ORGANIZACAO, p1, faixas)
    exigir(h1 == M.hash_da_entrada(ORGANIZACAO, p1, faixas), "hash instavel para a mesma entrada")
    exigir(h1 != M.hash_da_entrada(ORGANIZACAO, p2, faixas), "hash nao muda com o score_id")
    exigir(M.chave_idempotencia(ORGANIZACAO, h1) == "tier:TIER:%s:%s" % (ORGANIZACAO, h1),
           "prefixo da chave fora do declarado")
    exigir("2026-10-02T12:00:00+00:00" not in h1 and h1 == M.hash_da_entrada(ORGANIZACAO, p1, faixas),
           "o relogio entrou no hash")


@item("I2", "replay: prova 0 na gravacao -> JA_CLASSIFICADO e gravados=0")
def _i2():
    porta = PortaFalsa(prioridade=prioridade(), grava=False)
    relatorio = M.Tiering(porta=porta, raiz=RAIZ, ambiente="dev").rodar(ORGANIZACAO)
    exigir(relatorio["por_veredito"] == {"JA_CLASSIFICADO": 1},
           "replay nao detectado: %s" % relatorio["por_veredito"])
    exigir(relatorio["gravados"] == 0, "replay contou gravacao")
    exigir(relatorio["prova_da_gravacao"] == "0", "prova da gravacao != 0")


@item("S1", "a leitura e o ULTIMO PRIORITY (calculated_at DESC, id DESC LIMIT 1)")
def _s1():
    sql = M.sql_ultimo_priority(ORGANIZACAO)
    exigir("score_type = 'PRIORITY'" in sql, "leitura nao filtra score_type PRIORITY")
    exigir("ORDER BY calculated_at DESC, id DESC LIMIT 1" in sql, "leitura nao pega o ultimo")
    exigir("DISTINCT ON" not in sql and "AVG(" not in sql.upper(), "leitura agrega historico")


@item("S2", "registro grava operation=TIER com tier, faixas vigentes e entrada_hash")
def _s2():
    porta = PortaFalsa(prioridade=prioridade("92.00"))
    relatorio = M.Tiering(porta=porta, raiz=RAIZ, ambiente="dev").rodar(ORGANIZACAO)
    exigir(relatorio["resultados"][0]["tier"] == "A+", "92.00 devia ser A+")
    sql = porta.insert_do_registro()
    for fragmento in ("'TIER'", "'organization'", "'tiering-v1'", "request_payload",
                      "idempotency_key"):
        exigir(fragmento in sql, "INSERT do registro sem %s" % fragmento)
    exigir('"tier": "A+"' in sql, "payload sem o tier calculado")
    exigir('"faixas_vigentes"' in sql, "payload sem a tabela de faixas vigente")
    exigir("entrada_hash" in sql, "payload sem a identidade da entrada")


@item("S3", "a prova da gravacao conta a linha DESTA rodada (id do claim + PROCESSED)")
def _s3():
    sql = M.sql_gravar_registro("00000000-0000-4000-8000-000000000000",
                                {"organization_id": ORGANIZACAO, "source_system": "postgresql",
                                 "target_system": "odoo", "chave": "tier:TIER:x:y",
                                 "gerado_em": AGORA, "request_payload": {"tier": "A"}})
    exigir("WHERE NOT EXISTS" in sql, "claim sem guarda de replay")
    exigir("id = '00000000-0000-4000-8000-000000000000'" in sql, "fechamento sem o id do claim")
    contagem = "SELECT COUNT(*) FROM sales_intelligence.sync_events"
    exigir(contagem in sql and sql.index(contagem) > sql.index("UPDATE sales_intelligence.sync_events"),
           "a prova da gravacao nao e contada depois do fechamento do claim")
    exigir("status = 'PROCESSED'" in sql, "a prova nao exige o fechamento PROCESSED")


@item("S4", "PRIORITY NULL valid_until nao vence por si (so vence com data lida)")
def _s4():
    calculo = M.classificar(prioridade("70.00", validade=None), faixas_do_contrato(), AGORA)
    exigir(calculo["tier"] == "B", "sem valid_until devia classificar (B): %s" % calculo["tier"])


@item("S5", "sem PRIORITY o tier e None (nao ha default silencioso)")
def _s5():
    calculo = M.classificar(None, faixas_do_contrato(), AGORA)
    exigir(calculo["tier"] is None, "ausencia produziu tier %r" % calculo["tier"])
    exigir(calculo["motivo"] == "SEM_PRIORITY", "ausencia sem motivo nominal")


# ---------------------------------------------------------------------------------------
# Itens — guarda de escrita
# ---------------------------------------------------------------------------------------
@item("G1", "DDL e recusado")
def _g1():
    for sql in ("DROP TABLE sales_intelligence.scores;", "ALTER TABLE sales_intelligence.scores ADD COLUMN tier text;"):
        try:
            M.validar_sql(sql)
        except M.GuardaDeEscritaViolada:
            continue
        raise AssertionError("DDL passou pela guarda: %s" % sql)


@item("G2", "escrita em scores e recusada (tier nao e score)")
def _g2():
    sql = ("INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, "
           "score_version) VALUES (gen_random_uuid(), 'x', 'TIER', 5, 'tiering-v1');")
    try:
        M.validar_sql(sql)
    except M.GuardaDeEscritaViolada as exc:
        exigir("tier NÃO é score" in str(exc), "recusa sem motivo claro: %s" % exc)
        return
    raise AssertionError("INSERT em scores passou pela guarda")


@item("G3", "INSERT no registro sem a operation TIER e recusado")
def _g3():
    sql = ("INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, operation, "
           "idempotency_key, request_payload) VALUES (gen_random_uuid(), 'organization', 'x', "
           "'SCORE', 'k', '{}'::jsonb);")
    try:
        M.validar_sql(sql)
    except M.GuardaDeEscritaViolada:
        return
    raise AssertionError("registro sem a operation declarada passou pela guarda")


@item("G4", "UPDATE em agent_runs e recusado (auditoria append-only)")
def _g4():
    try:
        M.validar_sql("UPDATE sales_intelligence.agent_runs SET status = 'X' WHERE id = 'y';")
    except M.GuardaDeEscritaViolada:
        return
    raise AssertionError("UPDATE em agent_runs passou pela guarda")


@item("G5", "DELETE so com o desfazer explicito (permitir_remocao)")
def _g5():
    sql = "DELETE FROM sales_intelligence.sync_events WHERE id IN ('a');"
    try:
        M.validar_sql(sql)
    except M.GuardaDeEscritaViolada:
        pass
    else:
        raise AssertionError("DELETE sem --confirmo passou pela guarda")
    M.validar_sql(sql, permitir_remocao=True)


# ---------------------------------------------------------------------------------------
# Itens — ambiente, modos sem conexao
# ---------------------------------------------------------------------------------------
@item("S9", "prod e recusado (nada nasce em producao)")
def _s9():
    agente = M.Tiering(porta=PortaFalsa(), raiz=RAIZ, ambiente="prod")
    try:
        agente.conferir_ambiente()
    except M.RecusaDeAmbiente:
        return
    raise AssertionError("prod foi aceito")


@item("S10", "--planejar nao abre conexao")
def _s10():
    agente = M.Tiering(porta=PortaExplosiva(), raiz=RAIZ)
    plano = agente.planejar()
    exigir(plano["planejamento"] is True, "planejar nao declarou o modo")
    exigir(plano["plano"]["faixas"], "plano sem as faixas vigentes")
    exigir("scores.tiers" in plano["plano"]["fonte_das_faixas"], "plano sem a fonte das faixas")


@item("S11", "--faixas le o contrato e nao abre conexao")
def _s11():
    agente = M.Tiering(porta=PortaExplosiva(), raiz=RAIZ)
    faixas = agente.faixas_vigentes()
    exigir([f["nome"] for f in faixas["faixas"]] == ["A+", "A", "B", "C", "Nurture"],
           "tabela de faixas vigente fora do contrato")
    exigir(Decimal(faixas["escala"]["minimo"]) == Decimal("0")
           and Decimal(faixas["escala"]["maximo"]) == Decimal("100"), "escala fora do contrato")


# ---------------------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------------------
def rodar_itens(itens, rotulo="VERIFICACAO_TIERING"):
    falhas = 0
    for codigo, descricao, funcao in itens:
        try:
            funcao()
            print("OK    %-5s %s" % (codigo, descricao))
        except AssertionError as exc:
            falhas += 1
            print("FALHOU %-5s %s -> %s" % (codigo, descricao, exc))
        except Exception as exc:  # noqa: BLE001 - defeito do item, nao do componente
            falhas += 1
            print("FALHOU %-5s %s -> erro inesperado: %r" % (codigo, descricao, exc))
    total = len(itens)
    if falhas:
        print("RESULTADO: %s_FALHOU (%d itens, %d falha(s))" % (rotulo, total, falhas))
        return 1
    print("RESULTADO: %s_OK (%d itens, 0 falhas)" % (rotulo, total))
    return 0


# Mutacoes: cada uma tem de reprovar o ITEM esperado (dente do proprio instrumento)
MUTACOES = [
    ("faixa-hardcoded-no-codigo", "OPERACAO_SYNC = \"TIER\"",
     "LIMITES = {\"A+\": 90}\nOPERACAO_SYNC = \"TIER\"", "C3"),
    ("ausencia-vira-nurture", "    if prioridade is None:\n        base[\"motivo\"] = MOTIVO_SEM_PRIORITY",
     "    if prioridade is None:\n        base[\"tier\"] = \"Nurture\"\n"
     "        base[\"motivo\"] = MOTIVO_SEM_PRIORITY", "N2"),
    ("sem-checagem-de-vencido", "    if limite is not None and limite <= agora_dt:",
     "    if False:", "N3"),
    ("fronteira-aberta", "        if faixa[\"min\"] <= valor <= faixa[\"max\"]:",
     "        if faixa[\"min\"] < valor < faixa[\"max\"]:", "N1"),
    ("hash-ignora-a-entrada", "            \"score_id\": str(prioridade.get(\"score_id\")),",
     "            \"score_id\": \"fixo\",", "I1"),
    ("leitura-sem-ultimo", "ORDER BY calculated_at DESC, id DESC LIMIT 1;",
     "ORDER BY calculated_at ASC, id ASC LIMIT 1;", "S1"),
    ("sem-prova-da-gravacao", "SELECT COUNT(*) FROM %s WHERE id = %s AND status = 'PROCESSED';",
     "SELECT 1;", "I2"),
    ("guarda-sem-ddl", "    if _DDL.search(codigo):", "    if False:", "G1"),
    ("guarda-libera-escrita-em-scores", "            if tabela == TABELA_SCORES:", "            if False:", "G2"),
    ("guarda-sem-operation", "    if lit(OPERACAO_SYNC) not in sql and (\"INSERT INTO %s\" % TABELA_SYNC_EVENTS) in sql:",
     "    if False:", "G3"),
]


def autoteste() -> int:
    print("--- autoteste por mutacao (cada mutacao tem de reprovar o item esperado)")
    reprovadas = 0
    for nome, alvo, troca, item_esperado in MUTACOES:
        with tempfile.TemporaryDirectory() as tmp:
            raiz = arvore_de_prova(Path(tmp))
            caminho = raiz / "hermes/scores/tiering/tiering.py"
            texto = caminho.read_text(encoding="utf-8")
            if alvo not in texto:
                print("FALHOU mutacao %-32s ancora nao encontrada (item esperado %s)"
                      % (nome, item_esperado))
                continue
            caminho.write_text(texto.replace(alvo, troca, 1), encoding="utf-8")
            try:
                modulo = carregar_modulo(caminho, nome="mutado_%s" % nome.replace("-", "_"))
                global M
                original, M = M, modulo
                try:
                    alvo_funcoes = [f for c, d, f in ITENS if c == item_esperado]
                    exigir(alvo_funcoes, "item %s nao existe" % item_esperado)
                    try:
                        alvo_funcoes[0]()
                    except Exception as exc:  # noqa: BLE001 - reprovar por erro tambem e reprovar
                        reprovadas += 1
                        print("OK    mutacao %-32s -> item %s reprovou (%s)"
                              % (nome, item_esperado, type(exc).__name__))
                    else:
                        print("FALHOU mutacao %-32s -> item %s NAO reprovou (mutacao inerte)"
                              % (nome, item_esperado))
                finally:
                    M = original
            except Exception as exc:  # noqa: BLE001
                print("FALHOU mutacao %-32s -> erro ao carregar: %r" % (nome, exc))
    if reprovadas != len(MUTACOES):
        print("RESULTADO: AUTOTESTE_FALHOU (%d/%d mutacoes detectadas)" % (reprovadas, len(MUTACOES)))
        return 1
    print("RESULTADO: AUTOTESTE OK (%d/%d mutacoes detectadas, cada uma pelo item esperado)"
          % (reprovadas, len(MUTACOES)))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Suite offline do Tiering v1")
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    codigo = rodar_itens(ITENS)
    if args.autoteste:
        codigo = max(codigo, autoteste())
    return codigo


if __name__ == "__main__":
    sys.exit(main())
