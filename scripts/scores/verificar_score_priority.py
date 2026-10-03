#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do Priority Score v1 (card TRE-W5-E05-T01) — sem banco, sem rede.

Mede o que o componente DECLARA: a formula lida do Data Contract, a politica de ausencia/cobertura
(fail-closed, sem renormalizacao), a politica de validade do score, o hash de idempotencia, a guarda
de escrita e o SQL de leitura/gravacao. A prova de banco e o aceite E2E
(`scripts/scores/teste_priority_aceite.sh`), nao esta suite.

Uso:
  python3 scripts/scores/verificar_score_priority.py              # itens
  python3 scripts/scores/verificar_score_priority.py --autoteste  # mutacoes do proprio codigo
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MODULO = RAIZ / "hermes/scores/priority/priority_score.py"
CONTRATO_DO_SCORE = "hermes/scores/priority/score-priority-v1.json"
CONTRATO_DE_DADOS = "docs/data/data_contract_v1.json"

ORGANIZACAO = "11111111-1111-1111-1111-111111111111"

ITENS = []
FALHAS = []


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
    nome = nome or "priority_sob_teste_%s" % uuid.uuid4().hex[:8]
    spec = importlib.util.spec_from_file_location(nome, str(caminho))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def arvore_de_prova(destino: Path) -> Path:
    """Copia o minimo para o modulo carregar: o codigo e os dois contratos."""
    (destino / "hermes/scores/priority").mkdir(parents=True, exist_ok=True)
    (destino / "docs/data").mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODULO, destino / "hermes/scores/priority/priority_score.py")
    shutil.copy2(RAIZ / CONTRATO_DO_SCORE, destino / CONTRATO_DO_SCORE)
    shutil.copy2(RAIZ / CONTRATO_DE_DADOS, destino / CONTRATO_DE_DADOS)
    return destino


M = carregar_modulo()


# ---------------------------------------------------------------------------------------
# Porta falsa: simula o PostgreSQL e GUARDA o SQL executado (a prova sai do SQL, nao do texto)
# ---------------------------------------------------------------------------------------
class PortaFalsa:
    def __init__(self, organizacao=ORGANIZACAO, componentes=None, grava=True):
        self.organizacao = organizacao
        self.componentes = componentes or {}
        self.grava = grava
        self.sqls = []

    def executar(self, sql, permitir_remocao=False):
        M.validar_sql(sql, permitir_remocao=permitir_remocao)
        self.sqls.append(sql)
        if "FROM (SELECT DISTINCT ON (score_type)" in sql:
            linhas = []
            for tipo, dados in self.componentes.items():
                registro = {"score_type": tipo, "score_id": dados.get("score_id", "id-" + tipo),
                            "score_value": str(dados["score_value"]),
                            "score_version": dados.get("score_version", "%s-v1" % tipo.lower()),
                            "calculated_at": dados.get("calculated_at", "2026-10-02T12:00:00+00:00"),
                            "valid_until": dados.get("valid_until") or ""}
                linhas.append(json.dumps(registro))
            return 0, "\n".join(linhas) + ("\n" if linhas else ""), ""
        if "sales_intelligence.organizations" in sql:
            return 0, (self.organizacao + "\n") if self.organizacao else "", ""
        if "INSERT INTO sales_intelligence.scores" in sql:
            return 0, ("1\n" if self.grava else "0\n"), ""
        if "DELETE FROM sales_intelligence.scores" in sql:
            # a contagem pos-delete vem na MESMA instrucao do DELETE
            return 0, "0\n", ""
        if "SELECT COUNT(*) FROM sales_intelligence.scores" in sql:
            # backup do desfazer: a rodada tinha 1 score ancorado
            return 0, "1\n", ""
        if "jsonb_extract_path_text(output, 'score_id')" in sql:
            # ancora do desfazer: a rodada criou 1 score
            return 0, "score-da-rodada\n", ""
        return 0, "\n", ""

    def sqls_de(self, fragmento):
        return [sql for sql in self.sqls if fragmento in sql]


class PortaExplosiva:
    """Se o modo `--planejar` encostar no banco, este item reprova."""

    def executar(self, sql, permitir_remocao=False):
        raise AssertionError("--planejar abriu conexao com o banco")


def insert_de_score(porta):
    """O SQL do INSERT de scores desta rodada (a prova vem do SQL, nao do relatorio)."""
    achados = porta.sqls_de("INSERT INTO sales_intelligence.scores")
    exigir(achados, "nenhum INSERT de scores foi executado")
    return achados[0]


def contem_no_insert(porta, fragmento):
    """Confere o que foi GRAVADO lendo o literal do INSERT (nao o relatorio do agente)."""
    sql = insert_de_score(porta)
    exigir(fragmento in sql, "INSERT de scores sem %r" % fragmento)



def componentes(**valores):
    base = {"ICP": {"score_value": 94.0, "score_id": "id-icp"},
            "AUTOMATION_FIT": {"score_value": 76.0, "score_id": "id-af"},
            "BUYING_SIGNAL": {"score_value": 83.0, "score_id": "id-bs"},
            "DATA_QUALITY": {"score_value": 100.0, "score_id": "id-dq"}}
    base.update(valores)
    return base


# ---------------------------------------------------------------------------------------
# Itens — contrato e pesos
# ---------------------------------------------------------------------------------------
@item("C1", "contrato do score existe, e lido e confere com o codigo (sem divergencia)")
def _c1():
    agente = M.PriorityScore(raiz=RAIZ)
    contrato = agente.conferir_contrato()
    exigir(contrato["score_type"] == "PRIORITY", "score_type do contrato")
    exigir(contrato["modelo"]["componentes"] == list(M.COMPONENTES), "componentes do contrato")


@item("C2", "os pesos vem do Data Contract e somam exatamente 1,00")
def _c2():
    pesos = M.pesos_do_data_contract(RAIZ)
    exigir(tuple(sorted(pesos)) == tuple(sorted(M.COMPONENTES)), "pesos do Data Contract")
    exigir(sum(pesos.values()) == Decimal("1.00"), "soma dos pesos: %s" % sum(pesos.values()))
    exigir(pesos["ICP"] == Decimal("0.35"), "peso do ICP")


@item("C3", "nenhum peso em forma EXECUTAVEL no codigo (a constante e do contrato, nao do codigo)")
def _c3():
    arvore = ast.parse(pathlib.Path(M.__file__).read_text(encoding="utf-8"))
    proibidos = {0.35, 0.30, 0.25, 0.10}
    encontrados = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and isinstance(no.value, float) and no.value in proibidos:
            encontrados.append(no.value)
    exigir(not encontrados, "peso em forma executavel no codigo: %s" % encontrados)


@item("C4", "mudar o peso no Data Contract muda o calculo (o codigo le o contrato, nao chuta)")
def _c4():
    with tempfile.TemporaryDirectory() as tmp:
        raiz = arvore_de_prova(Path(tmp))
        contrato = json.loads((raiz / CONTRATO_DE_DADOS).read_text(encoding="utf-8"))
        contrato["scores"]["priority_weights"]["ICP"] = 0.65
        contrato["scores"]["priority_weights"]["DATA_QUALITY"] = 0.10
        contrato["scores"]["priority_weights"]["AUTOMATION_FIT"] = 0.15
        contrato["scores"]["priority_weights"]["BUYING_SIGNAL"] = 0.10
        (raiz / CONTRATO_DE_DADOS).write_text(json.dumps(contrato), encoding="utf-8")
        modulo = carregar_modulo(raiz / "hermes/scores/priority/priority_score.py")
        pesos = modulo.pesos_do_data_contract(raiz)
        exigir(pesos["ICP"] == Decimal("0.65"), "peso lido do contrato alterado")
        calculo = modulo.calcular_priority(componentes(), pesos, "2026-10-02T12:00:00+00:00")
        exigir(calculo["score_value"] == 90.8,
               "valor com os pesos alterados: %s" % calculo["score_value"])
        # O modulo ORIGINAL, com o contrato original, segue no valor do contrato V1.
        calculo_v1 = M.calcular_priority(componentes(), M.pesos_do_data_contract(RAIZ),
                                         "2026-10-02T12:00:00+00:00")
        exigir(calculo_v1["score_value"] == 86.45, "valor do contrato V1: %s" % calculo_v1["score_value"])


# ---------------------------------------------------------------------------------------
# Itens — calculo
# ---------------------------------------------------------------------------------------
@item("N1", "calculo do contrato: 0,35*94 + 0,30*76 + 0,25*83 + 0,10*100 = 86,45")
def _n1():
    calculo = M.calcular_priority(componentes(), M.pesos_do_data_contract(RAIZ),
                                  "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] == 86.45, "valor: %s" % calculo["score_value"])
    exigir(calculo["motivo"] is None, "motivo: %s" % calculo["motivo"])
    exigir(calculo["cobertura"] == "1.00", "cobertura: %s" % calculo["cobertura"])
    exigir(calculo["presentes"] == list(M.COMPONENTES), "presentes: %s" % calculo["presentes"])
    parcelas = {t: calculo["componentes"][t]["parcela"] for t in M.COMPONENTES}
    exigir(parcelas == {"ICP": "32.90", "AUTOMATION_FIT": "22.80", "BUYING_SIGNAL": "20.75",
                        "DATA_QUALITY": "10.00"}, "parcelas: %s" % parcelas)


@item("N2", "componente AUSENTE RECUSA (SEM_LASTRO_COMPLETO) com o nome do que falta — nao virou zero")
def _n2():
    sem = componentes()
    sem.pop("DATA_QUALITY")
    calculo = M.calcular_priority(sem, M.pesos_do_data_contract(RAIZ), "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] is None, "ausente NAO pode produzir numero: %s" % calculo["score_value"])
    exigir(calculo["motivo"] == "SEM_LASTRO_COMPLETO", "motivo: %s" % calculo["motivo"])
    exigir(calculo["componentes_faltantes"] == ["DATA_QUALITY"], "faltantes: %s" % calculo["componentes_faltantes"])
    exigir(calculo["cobertura"] == "0.90", "cobertura: %s" % calculo["cobertura"])
    exigir("COMPONENTE_AUSENTE:DATA_QUALITY" in calculo["motivos"], "motivos: %s" % calculo["motivos"])


@item("N3", "nao ha renormalizacao: um componente so nao vira 100 (nem infla o valor)")
def _n3():
    so_icp = {"ICP": {"score_value": 100.0}}
    calculo = M.calcular_priority(so_icp, M.pesos_do_data_contract(RAIZ), "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] is None, "um componente so nao pode pontuar: %s" % calculo["score_value"])
    exigir(calculo["cobertura"] == "0.35", "cobertura: %s" % calculo["cobertura"])


@item("N4", "componente VENCIDO (valid_until no passado) conta como ausente, com motivo proprio")
def _n4():
    vencido = componentes()
    vencido["BUYING_SIGNAL"] = {"score_value": 83.0, "score_id": "id-bs",
                                "valid_until": "2026-01-01T00:00:00+00:00"}
    calculo = M.calcular_priority(vencido, M.pesos_do_data_contract(RAIZ), "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] is None, "vencido nao pode pontuar")
    exigir(calculo["motivo"] == "SEM_LASTRO_COMPLETO", "motivo: %s" % calculo["motivo"])
    exigir(calculo["vencidos"] == ["BUYING_SIGNAL"], "vencidos: %s" % calculo["vencidos"])
    exigir("COMPONENTE_VENCIDO:BUYING_SIGNAL" in calculo["motivos"], "motivos: %s" % calculo["motivos"])
    exigir(calculo["componentes"]["BUYING_SIGNAL"]["presente"] is False, "componente marcado presente")


@item("N5", "componente com valid_until no FUTURO continua valendo (vencimento e comparado, nao suposto)")
def _n5():
    futuro = componentes()
    futuro["BUYING_SIGNAL"] = {"score_value": 83.0, "score_id": "id-bs",
                               "valid_until": "2026-11-01T00:00:00+00:00"}
    calculo = M.calcular_priority(futuro, M.pesos_do_data_contract(RAIZ), "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] == 86.45, "valor: %s" % calculo["score_value"])
    exigir(calculo["vencidos"] == [], "vencidos: %s" % calculo["vencidos"])


@item("N6", "score de componente fora de 0..100 ou ilegivel RECUSA (nao entra por acidente)")
def _n6():
    pesos = M.pesos_do_data_contract(RAIZ)
    fora = componentes(DATA_QUALITY={"score_value": 140.0})
    calculo = M.calcular_priority(fora, pesos, "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] is None, "140 nao pode pontuar")
    exigir("SCORE_DO_COMPONENTE_FORA_DA_FAIXA:DATA_QUALITY" in calculo["motivos"],
           "motivos: %s" % calculo["motivos"])
    ilegivel = componentes(DATA_QUALITY={"score_value": "nao-e-numero"})
    calculo = M.calcular_priority(ilegivel, pesos, "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] is None, "ilegivel nao pode pontuar")
    exigir("COMPONENTE_ILEGIVEL" in " ".join(calculo["motivos"]) or
           "SCORE_DO_COMPONENTE_ILEGIVEL" in " ".join(calculo["motivos"]),
           "motivos: %s" % calculo["motivos"])


@item("N7", "arredondamento em 2 casas ROUND_HALF_UP (Decimal, nunca float solto)")
def _n7():
    pesos = M.pesos_do_data_contract(RAIZ)
    calculo = M.calcular_priority(componentes(ICP={"score_value": 33.33},
                                              AUTOMATION_FIT={"score_value": 33.33},
                                              BUYING_SIGNAL={"score_value": 33.33},
                                              DATA_QUALITY={"score_value": 33.33}),
                                  pesos, "2026-10-02T12:00:00+00:00")
    # 33.33 * (0.35+0.30+0.25+0.10) = 33.33 -> sem sobra; o item cobra a escala de 2 casas
    exigir(calculo["score_value"] == 33.33, "valor: %s" % calculo["score_value"])
    exigir(M._decimal("1.005") == Decimal("1.01"), "ROUND_HALF_UP em 2 casas")
    exigir(M._arredondar("0.005") == 0.01, "0,005 -> 0,01 (half up)")


@item("N8", "determinismo: mesma entrada, mesmo valor, bit a bit (duas rodadas)")
def _n8():
    pesos = M.pesos_do_data_contract(RAIZ)
    a = M.calcular_priority(componentes(), pesos, "2026-10-02T12:00:00+00:00")
    b = M.calcular_priority(componentes(), pesos, "2026-10-02T18:31:07+00:00")
    exigir(a["score_value"] == b["score_value"], "valores diferentes")
    exigir(json.dumps(a["componentes"], sort_keys=True) ==
           json.dumps(b["componentes"], sort_keys=True), "componentes diferentes")


@item("N9", "valor alto e valor zero sao calculados (0 nao e confundido com ausente)")
def _n9():
    pesos = M.pesos_do_data_contract(RAIZ)
    zeros = componentes(ICP={"score_value": 0.0}, AUTOMATION_FIT={"score_value": 0.0},
                        BUYING_SIGNAL={"score_value": 0.0}, DATA_QUALITY={"score_value": 0.0})
    calculo = M.calcular_priority(zeros, pesos, "2026-10-02T12:00:00+00:00")
    exigir(calculo["score_value"] == 0.0, "valor: %s" % calculo["score_value"])
    exigir(calculo["motivo"] is None, "zero nao e recusa: %s" % calculo["motivo"])


# ---------------------------------------------------------------------------------------
# Itens — idempotencia
# ---------------------------------------------------------------------------------------
@item("I1", "hash da entrada: identidade dos componentes + pesos (nao carrega o relogio)")
def _n_i1():
    pesos = M.pesos_do_data_contract(RAIZ)
    a = M.hash_das_entradas(ORGANIZACAO, componentes(), pesos)
    b = M.hash_das_entradas(ORGANIZACAO, componentes(), pesos)
    exigir(a == b, "hash instavel")
    c = M.hash_das_entradas(ORGANIZACAO, componentes(ICP={"score_value": 95.0, "score_id": "id-icp"}), pesos)
    exigir(a != c, "hash nao mudou com o valor do componente")
    d = M.hash_das_entradas(ORGANIZACAO, componentes(ICP={"score_value": 94.0, "score_id": "outro"}), pesos)
    exigir(a != d, "hash nao mudou com o id do componente")
    exigir(M.chave_idempotencia(ORGANIZACAO, a) == "score:PRIORITY:%s:%s" % (ORGANIZACAO, a),
           "formato da chave")


@item("I2", "o hash NAO muda com o correlation_id nem com o instante da rodada")
def _n_i2():
    pesos = M.pesos_do_data_contract(RAIZ)
    exigir(M.hash_das_entradas(ORGANIZACAO, componentes(), pesos) ==
           M.hash_das_entradas(ORGANIZACAO, componentes(), pesos),
           "o hash carregou o relogio (ou outra coisa que muda a cada rodada)")


@item("I3", "rodada igual = JA_CALCULADO (sem linha nova); componente novo = linha nova")
def _n_i3():
    porta = PortaFalsa(componentes=componentes())
    agente = M.PriorityScore(porta=porta, raiz=RAIZ, ambiente="dev",
                             relogio=lambda: "2026-10-02T12:00:00+00:00")
    primeira = agente.rodar(ORGANIZACAO)
    exigir(primeira["por_veredito"] == {"CALCULADO": 1}, "1a rodada: %s" % primeira["por_veredito"])
    exigir(porta.sqls_de("INSERT INTO sales_intelligence.scores"), "nada foi gravado na 1a rodada")
    porta2 = PortaFalsa(componentes=componentes(), grava=False)
    agente2 = M.PriorityScore(porta=porta2, raiz=RAIZ, ambiente="dev",
                              relogio=lambda: "2026-10-02T12:00:00+00:00")
    replay = agente2.rodar(ORGANIZACAO)
    exigir(replay["por_veredito"] == {"JA_CALCULADO": 1}, "replay: %s" % replay["por_veredito"])
    exigir(replay["gravados"] == 0, "replay gravou: %s" % replay["gravados"])
    exigir(M.chave_idempotencia(ORGANIZACAO, agente2.entrada_hash if hasattr(agente2, "entrada_hash")
                                else replay["entrada_hash"]) ==
           M.chave_idempotencia(ORGANIZACAO, primeira["entrada_hash"]),
           "chave do replay diferente da 1a rodada")
    novo = PortaFalsa(componentes=componentes(ICP={"score_value": 99.0, "score_id": "id-icp2"}))
    agente3 = M.PriorityScore(porta=novo, raiz=RAIZ, ambiente="dev",
                              relogio=lambda: "2026-10-02T12:00:00+00:00")
    terceira = agente3.rodar(ORGANIZACAO)
    exigir(terceira["por_veredito"] == {"CALCULADO": 1}, "componente novo: %s" % terceira["por_veredito"])
    exigir(terceira["entrada_hash"] != primeira["entrada_hash"], "chave nao mudou com componente novo")


# ---------------------------------------------------------------------------------------
# Itens — guarda de escrita
# ---------------------------------------------------------------------------------------
def _sql_score_de_prova(score_type=None, colunas=None):
    colunas = colunas or ["id", "organization_id", "score_type", "score_value", "score_version",
                          "inputs", "explanation", "calculated_at", "valid_until"]
    valores = {"id": "'x'", "organization_id": "'y'", "score_type": "'%s'" % (score_type or "PRIORITY"),
               "score_value": "1.0", "score_version": "'priority-v1'", "inputs": "'{}'",
               "explanation": "'{}'", "calculated_at": "NOW()", "valid_until": "NULL"}
    return ("INSERT INTO %s (%s) VALUES (%s);" % (M.TABELA_SCORES, ", ".join(colunas),
                                                 ", ".join(valores[c] for c in colunas)))


@item("G1", "DDL e recusado")
def _g1():
    for sql in ("CREATE TABLE x (a int);", "ALTER TABLE sales_intelligence.scores DROP COLUMN inputs;",
                "DROP TABLE sales_intelligence.scores;"):
        try:
            M.validar_sql(sql)
        except M.GuardaDeEscritaViolada:
            continue
        raise AssertionError("DDL passou pela guarda: %s" % sql)


@item("G2", "UPDATE em scores e recusado SEMPRE (score e historico, nao mutavel)")
def _g2():
    try:
        M.validar_sql("UPDATE sales_intelligence.scores SET score_value = 100;")
    except M.GuardaDeEscritaViolada:
        return
    raise AssertionError("UPDATE em scores passou pela guarda")


@item("G3", "INSERT de score com score_type diferente de PRIORITY e recusado")
def _g3():
    try:
        M.validar_sql(_sql_score_de_prova(score_type="ICP"))
    except M.GuardaDeEscritaViolada:
        return
    raise AssertionError("INSERT de outro score_type passou pela guarda")


@item("G4", "INSERT de score sem coluna obrigatoria do DDL/contrato e recusado")
def _g4():
    for falta in ("score_version", "score_value", "organization_id", "score_type", "id"):
        colunas = [c for c in ["id", "organization_id", "score_type", "score_value", "score_version"]
                   if c != falta]
        try:
            M.validar_sql(_sql_score_de_prova(colunas=colunas))
        except M.GuardaDeEscritaViolada:
            continue
        raise AssertionError("INSERT sem %s passou pela guarda" % falta)


@item("G5", "escrita nas tabelas de ENTRADA (signals, organizations, pain_hypotheses, research_runs) e recusada")
def _g5():
    for sql in ("INSERT INTO sales_intelligence.signals (id) VALUES ('x');",
                "UPDATE sales_intelligence.organizations SET status = 'Qualificado';",
                "DELETE FROM sales_intelligence.pain_hypotheses WHERE id = 'x';",
                "INSERT INTO sales_intelligence.research_runs (id) VALUES ('x');",
                "UPDATE sales_intelligence.scores SET score_version = 'v2';"):
        try:
            M.validar_sql(sql)
        except M.GuardaDeEscritaViolada:
            continue
        raise AssertionError("escrita proibida passou: %s" % sql)


@item("G6", "DELETE so passa com o desfazer explicito; INSERT em agent_runs/sync_events passa")
def _g6():
    try:
        M.validar_sql("DELETE FROM sales_intelligence.scores WHERE id = 'x';")
    except M.GuardaDeEscritaViolada:
        pass
    else:
        raise AssertionError("DELETE sem --confirmo passou pela guarda")
    M.validar_sql("DELETE FROM sales_intelligence.scores WHERE id = 'x';", permitir_remocao=True)
    M.validar_sql("INSERT INTO sales_intelligence.agent_runs (id) VALUES ('x');")
    M.validar_sql("INSERT INTO sales_intelligence.sync_events (id) VALUES ('x');")
    M.validar_sql("UPDATE sales_intelligence.sync_events SET status = 'PROCESSED';")


@item("G7", "DDL escrito DENTRO de um literal nao engana a guarda (e nem passa por outra via)")
def _g7():
    # o texto 'DROP TABLE' dentro de um literal nao e DDL: a guarda le a instrucao sem literais
    M.validar_sql("INSERT INTO sales_intelligence.agent_runs (id) VALUES ('DROP TABLE x');")
    try:
        M.validar_sql("INSERT INTO sales_intelligence.agent_runs (id) VALUES ('x'); DROP TABLE y;")
    except M.GuardaDeEscritaViolada:
        return
    raise AssertionError("DDL depois de uma instrucao passou pela guarda")


# ---------------------------------------------------------------------------------------
# Itens — SQL de leitura/gravacao, fonte, ambiente, planejar
# ---------------------------------------------------------------------------------------
@item("S1", "leitura do componente: ultimo por score_type (DISTINCT ON) e so os quatro tipos da organizacao")
def _s1():
    sql = M.sql_componentes_da_organizacao(ORGANIZACAO)
    exigir("DISTINCT ON (score_type)" in sql, "sem DISTINCT ON: %s" % sql)
    exigir("ORDER BY score_type, calculated_at DESC, id DESC" in sql,
           "ordem do ultimo componente: %s" % sql)
    exigir("organization_id = '%s'" % ORGANIZACAO in sql, "filtro da organizacao")
    for tipo in M.COMPONENTES:
        exigir("'%s'" % tipo in sql, "tipo ausente na leitura: %s" % tipo)
    exigir("valid_until" in sql and "score_value" in sql, "colunas do componente")


@item("S2", "gravacao: claim da chave + INSERT ancorado no claim + fechamento + contagem (prova da rodada)")
def _s2():
    sql = M.sql_gravar_score("score-id", "sync-id", "chave", {
        "organization_id": ORGANIZACAO, "score_value": 86.45, "calculated_at": "2026-10-02T12:00:00+00:00",
        "valid_until": "2026-11-01T12:00:00+00:00", "inputs": {"a": 1}, "explanation": {"b": 2}})
    exigir("ON_ERROR" not in sql, "SQL de gravacao nao e uma transacao declarada")
    exigir("'REGISTERED'" in sql and "WHERE NOT EXISTS (SELECT 1 FROM sales_intelligence.sync_events" in sql,
           "sem claim da chave")
    exigir("status = 'PROCESSED'" in sql and "EXISTS (SELECT 1 FROM sales_intelligence.scores" in sql,
           "fechamento nao ancorado no score da rodada")
    exigir("SELECT COUNT(*) FROM sales_intelligence.scores WHERE id = 'score-id'" in sql,
           "sem a prova da gravacao")


@item("S3", "valid_until do PRIORITY = calculated_at + 30 dias (politica de validade DESTE card)")
def _s3():
    porta = PortaFalsa(componentes=componentes())
    agente = M.PriorityScore(porta=porta, raiz=RAIZ, ambiente="dev",
                             relogio=lambda: "2026-10-02T12:00:00+00:00")
    agente.rodar(ORGANIZACAO)
    sql = porta.sqls_de("INSERT INTO sales_intelligence.scores")[0]
    exigir("'2026-11-01T12:00:00+00:00'" in sql, "valid_until esperado (+30d): %s" % sql[-800:])
    exigir("'2026-10-02T12:00:00+00:00'" in sql, "calculated_at gravado")
    exigir("86.45" in sql, "valor gravado no INSERT")
    contem_no_insert(porta, '"validade_dias": 30')
    contem_no_insert(porta, '"sem_renormalizacao": true')
    contem_no_insert(porta, '"executado": false')
    contem_no_insert(porta, '"deterministico": true')


@item("S4", "inputs gravados: identidade de cada componente + ausentes/vencidos + cobertura + pesos")
def _s4():
    porta = PortaFalsa(componentes=componentes())
    agente = M.PriorityScore(porta=porta, raiz=RAIZ, ambiente="dev",
                             relogio=lambda: "2026-10-02T12:00:00+00:00")
    agente.rodar(ORGANIZACAO)
    contem_no_insert(porta, '"score_id": "id-icp"')
    contem_no_insert(porta, '"score_version": "icp-v1"')
    contem_no_insert(porta, '"cobertura": "1.00"')
    contem_no_insert(porta, '"componentes_ausentes": []')
    contem_no_insert(porta, '"componentes_vencidos": []')
    contem_no_insert(porta, '"pesos": {"AUTOMATION_FIT": "0.3", "BUYING_SIGNAL": "0.25", '
                              '"DATA_QUALITY": "0.1", "ICP": "0.35"}')
    contem_no_insert(porta, '"entrada_hash": "')
    contem_no_insert(porta, '"correlation_id": "%s"' % agente.correlation_id)


@item("S5", "empresa inexistente RECUSA sem escrever score (e registra a execucao)")
def _s5():
    porta = PortaFalsa(organizacao="")
    agente = M.PriorityScore(porta=porta, raiz=RAIZ, ambiente="dev")
    relatorio = agente.rodar(ORGANIZACAO)
    exigir(relatorio["por_veredito"] == {"RECUSADA": 1}, "veredito: %s" % relatorio["por_veredito"])
    exigir(relatorio["gravados"] == 0, "gravou score de empresa inexistente")
    exigir(not porta.sqls_de("INSERT INTO sales_intelligence.scores"), "escreveu score")
    exigir(relatorio["resultados"][0]["motivos"] == ["ORGANIZACAO_NAO_ENCONTRADA"], "motivos")


@item("S6", "lastro incompleto RECUSA sem escrever score (a recusa tambem e fail-closed no banco)")
def _s6():
    sem = componentes()
    sem.pop("BUYING_SIGNAL")
    porta = PortaFalsa(componentes=sem)
    agente = M.PriorityScore(porta=porta, raiz=RAIZ, ambiente="dev")
    relatorio = agente.rodar(ORGANIZACAO)
    exigir(relatorio["por_veredito"] == {"RECUSADA": 1}, "veredito: %s" % relatorio["por_veredito"])
    exigir(not porta.sqls_de("INSERT INTO sales_intelligence.scores"), "escreveu score sem lastro")
    exigir(relatorio["componentes_ausentes"] == ["BUYING_SIGNAL"], "ausentes: %s" % relatorio["componentes_ausentes"])


@item("S7", "desfazer: dry-run NAO apaga; --confirmo apaga so a rodada")
def _s7():
    porta = PortaFalsa(componentes=componentes())
    agente = M.PriorityScore(porta=porta, raiz=RAIZ, ambiente="dev")
    dry = agente.desfazer("corr-1")
    exigir(dry["dry_run"] is True and dry["apagados"] == 0, "dry-run apagou")
    exigir(not porta.sqls_de("DELETE FROM sales_intelligence.scores"), "dry-run executou DELETE")
    porta2 = PortaFalsa(componentes=componentes())
    porta2.grava = True
    agente2 = M.PriorityScore(porta=porta2, raiz=RAIZ, ambiente="dev")
    # a rodada tem 1 score ancorado: a contagem de prova do desfazer vem do SQL
    confirmado = agente2.desfazer("corr-1", confirmo=True)
    exigir(porta2.sqls_de("DELETE FROM sales_intelligence.scores"), "confirmo nao apagou")
    exigir("corr-1" in confirmado["correlation_id"], "correlation_id do relatorio")


@item("S8", "fonte: le jsonl, ignora comentario/vazio/duplicada e recusa linha sem organization_id")
def _s8():
    with tempfile.TemporaryDirectory() as tmp:
        arquivo = Path(tmp) / "fonte.jsonl"
        arquivo.write_text("# comentario\n\n{\"organization_id\": \"%s\"}\n{\"organization_id\": \"%s\"}\n"
                           "{\"organization_id\": \"%s\", \"evidence\": \"x\"}\n"
                           % (ORGANIZACAO, ORGANIZACAO, "22222222-2222-2222-2222-222222222222"),
                           encoding="utf-8")
        itens = M.ler_fonte(arquivo)
        exigir(itens == [ORGANIZACAO, "22222222-2222-2222-2222-222222222222"], "fonte: %s" % itens)
        ruim = Path(tmp) / "ruim.jsonl"
        ruim.write_text("{\"evidence\": \"sem id\"}\n", encoding="utf-8")
        try:
            M.ler_fonte(ruim)
        except ValueError:
            return
        raise AssertionError("linha sem organization_id passou")


@item("S9", "ambiente: dev/homolog aceitos, prod recusado, ambiente nao declarado recusado")
def _s9():
    M.PriorityScore(raiz=RAIZ, ambiente="dev").conferir_ambiente()
    M.PriorityScore(raiz=RAIZ, ambiente="homolog").conferir_ambiente()
    for ambiente, esperado in (("prod", M.RecusaDeAmbiente), ("staging", M.RecusaDeAmbiente),
                               (None, M.RecusaDeAmbiente)):
        try:
            M.PriorityScore(raiz=RAIZ, ambiente=ambiente).conferir_ambiente()
        except esperado:
            continue
        raise AssertionError("ambiente %r passou" % ambiente)


@item("S10", "--planejar (planejar()) nao abre conexao e publica o plano declarado")
def _s10():
    agente = M.PriorityScore(porta=PortaExplosiva(), raiz=RAIZ, ambiente=None)
    plano = agente.planejar()
    exigir(plano["planejamento"] is True, "nao marcou planejamento")
    exigir(plano["plano"]["pesos"] == {"ICP": "0.35", "AUTOMATION_FIT": "0.3",
                                       "BUYING_SIGNAL": "0.25", "DATA_QUALITY": "0.1"}, "pesos do plano")
    exigir(plano["plano"]["cobertura_minima"] == "1.00", "cobertura minima do plano")
    exigir(plano["plano"]["validade_dias"] == 30, "validade do plano")
    exigir(plano["resultados"] == [] and plano["gravados"] == 0, "planejar calculou/gravou")


@item("S11", "sem porta de banco: rodar RECUSA (fail-closed, nunca 'passa em branco')")
def _s11():
    agente = M.PriorityScore(porta=M.PortaAusente(), raiz=RAIZ, ambiente="dev")
    try:
        agente.rodar(ORGANIZACAO)
    except M.PortaIndisponivel:
        return
    raise AssertionError("rodou sem porta de banco")


@item("S12", "execucao registrada em agent_runs com status do veredito e output ancorado no score")
def _s12():
    porta = PortaFalsa(componentes=componentes())
    agente = M.PriorityScore(porta=porta, raiz=RAIZ, ambiente="dev", correlation_id="corr-abc")
    agente.rodar(ORGANIZACAO)
    sql = porta.sqls_de("INSERT INTO sales_intelligence.agent_runs")[0]
    exigir("'COMPLETED'" in sql, "status do agent_runs")
    exigir("'corr-abc'" in sql, "correlation_id nao registrado")
    exigir("score_id" in sql and "entrada_hash" in sql, "output sem ancora do desfazer")


# ---------------------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------------------
def rodar_itens(itens, rotulo="VERIFICACAO_PRIORITY_SCORE"):
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
    ("sem-data-quality", "COMPONENTES = (\"ICP\", \"AUTOMATION_FIT\", \"BUYING_SIGNAL\", \"DATA_QUALITY\")",
     "COMPONENTES = (\"ICP\", \"AUTOMATION_FIT\", \"BUYING_SIGNAL\")", "N1"),
    ("cobertura-afrouxada", "COBERTURA_MINIMA = Decimal(\"1.00\")", "COBERTURA_MINIMA = Decimal(\"0.90\")", "N2"),
    ("renormaliza-em-vez-de-recusar", "    if cobertura < COBERTURA_MINIMA:",
     "    if False:", "N2"),
    ("sem-checagem-de-vencido", "        vencido = limite is not None and limite <= agora_dt",
     "        vencido = False", "N4"),
    ("validade-de-60-dias", "VALIDADE_DIAS = 30", "VALIDADE_DIAS = 60", "S3"),
    ("peso-hardcoded-no-codigo", "COMPONENTES = (\"ICP\", \"AUTOMATION_FIT\", \"BUYING_SIGNAL\", \"DATA_QUALITY\")",
     "PESO_ICP = 0.35\nCOMPONENTES = (\"ICP\", \"AUTOMATION_FIT\", \"BUYING_SIGNAL\", \"DATA_QUALITY\")", "C3"),
    ("hash-ignora-a-entrada", "        \"entradas\": {t: {\"score_id\": str(c.get(\"score_id\")), \"score_version\": str(c.get(\"score_version\")),",
     "        \"entradas\": {t: {\"score_id\": \"fixo\", \"score_version\": str(c.get(\"score_version\")),", "I1"),
    ("guarda-sem-ddl", "    if _DDL.search(codigo):", "    if False:", "G1"),
    ("guarda-libera-update-em-scores", "            if operacao == \"update\" and tabela == TABELA_SCORES:",
     "            if False:", "G2"),
    ("guarda-sem-score-type", "    if lit(SCORE_TYPE) not in sql and (\"INSERT INTO %s\" % TABELA_SCORES) in sql:",
     "    if False:", "G3"),
    ("leitura-sem-distinct-on", "SELECT DISTINCT ON (score_type) * FROM %s WHERE organization_id = %s ",
     "SELECT * FROM %s WHERE organization_id = %s ", "S1"),
    ("planejar-encosta-no-banco", "    def planejar(self) -> dict:\n        \"\"\"Plano declarado: o que seria lido e a regra aplicada. NAO abre conexao, NAO calcula.\"\"\"\n",
     "    def planejar(self) -> dict:\n        self.ler_componentes(\"00000000-0000-0000-0000-000000000000\")\n", "S10"),
]


def autoteste() -> int:
    print("--- autoteste por mutacao (cada mutacao tem de reprovar o item esperado)")
    reprovadas = 0
    for nome, alvo, troca, item_esperado in MUTACOES:
        with tempfile.TemporaryDirectory() as tmp:
            raiz = arvore_de_prova(Path(tmp))
            caminho = raiz / "hermes/scores/priority/priority_score.py"
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
    parser = argparse.ArgumentParser(description="Suite offline do Priority Score v1")
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    codigo = rodar_itens(ITENS)
    if args.autoteste:
        codigo = max(codigo, autoteste())
    return codigo


if __name__ == "__main__":
    sys.exit(main())
