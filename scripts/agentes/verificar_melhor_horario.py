#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE do melhor horario de contato — `melhor-horario-v1` (card TRE-W9-E04-T01).

Mede `hermes/analytics/melhor_horario.py` SEM PostgreSQL: a porta de banco e' o MESMO duble do irmao
(`scripts/agentes/duble_psql_desempenho.py`) — o componente reusa a consulta do irmao, entao o duble
serve aos dois e a coincidencia e' medida, nao presumida.

O que se mede: conversao do instante UTC para a janela (dia x faixa) no fuso DECLARADO (incluindo a
virada de dia as 23h locais), cobertura e fechamento da grade (7 dias x N faixas, soma == total),
marginais, amostra minima, ranking deterministico, COERENCIA com o irmao de desempenho (a atribuicao
resposta->envio e' uma so'), guardas (prod recusado, somente leitura, fail-closed de contrato/grade/fuso)
e nao-exposicao de PII.

NAO se mede aqui: o PostgreSQL de verdade. Isso e' o aceite E2E
(`scripts/agentes/teste_melhor_horario_aceite.sh`, container descartavel na VPS).

Uso:
  python3 scripts/agentes/verificar_melhor_horario.py [--raiz <dir>] [--codigo <modulo.py>] [--autoteste]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONTRATO_REL = "hermes/analytics/melhor-horario-v1.json"
MODULO_REL = "hermes/analytics/melhor_horario.py"
DUBLE_REL = "scripts/agentes/duble_psql_desempenho.py"
CONTRATO_IRMAO_REL = "hermes/analytics/desempenho-mensagens-v1.json"
IRMAO_IRMAO_REL = "hermes/agentes/respostas/ingestao-respostas-v1.json"
MODULO_IRMAO_REL = "hermes/analytics/desempenho_mensagens.py"

ORG_A, ORG_B, ORG_C, ORG_D, ORG_E = (
    "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555")
DESDE = "2026-09-01T00:00:00Z"
ATE = "2026-09-10T00:00:00Z"
ARGS = ["--desde", DESDE, "--ate", ATE, "--limite-amostra", "5"]

ITENS: list[tuple[str, bool]] = []


def item(nome: str, esperado, obtido) -> None:
    ok = esperado == obtido
    ITENS.append((nome, ok))
    print(("OK     " if ok else "FALHOU ") + f"{nome} (esperado={esperado!r} obtido={obtido!r})")


def envio(id_, org, quando, ref, canal="EMAIL", tipo="OUTBOUND_EMAIL"):
    return {"id": id_, "organization_id": org, "contact_id": "", "channel": canal, "direction": "OUTBOUND",
            "interaction_type": tipo, "occurred_at": quando, "content_reference": ref, "response_category": ""}


def resposta(id_, org, quando, categoria):
    return {"id": id_, "organization_id": org, "contact_id": "", "channel": "email", "direction": "INBOUND",
            "interaction_type": "EMAIL_RESPOSTA", "occurred_at": quando, "content_reference": "UIDVALIDITY:99",
            "response_category": categoria}


def fixture() -> list[dict]:
    """Corpus declarado: cada linha existe para medir UMA coisa nomeada na bateria.

    Fuso declarado = -03:00, entao hora local = UTC - 3h.
    """
    linhas: list[dict] = []
    # T1 (ORG A): 5 envios em TERCA 09:00-11:00 locais (12:00-14:00Z) -> celula terca/manha; 2 INTERESSE -> 0.4.
    for n, hora in ((1, "12:00"), (2, "12:30"), (3, "13:00"), (4, "13:30"), (5, "14:00")):
        linhas.append(envio(f"a{n}", ORG_A, f"2026-09-01T{hora}:00Z", f"envio:pa{n}:T1"))
    linhas.append(resposta("ar1", ORG_A, "2026-09-01T12:10:00Z", "INTERESSE"))   # credita a1
    linhas.append(resposta("ar2", ORG_A, "2026-09-01T12:40:00Z", "INTERESSE"))   # credita a2
    linhas.append(resposta("ar3", ORG_A, "2026-09-01T20:00:00Z", "AUTO_RESPOSTA"))  # descartada, nao conta
    # T2 (ORG B): 5 envios em QUARTA 14:00-16:00 locais (17:00-19:00Z) -> celula quarta/tarde; 1 INTERESSE -> 0.2.
    for n, hora in ((1, "17:00"), (2, "17:30"), (3, "18:00"), (4, "18:30"), (5, "19:00")):
        linhas.append(envio(f"b{n}", ORG_B, f"2026-09-02T{hora}:00Z", f"envio:pb{n}:T2"))
    linhas.append(resposta("br1", ORG_B, "2026-09-02T19:10:00Z", "INTERESSE"))
    # T3 (ORG C): 2 envios de SEXTA 02:00/02:30Z = QUINTA 23:00/23:30 locais -> virada de dia, celula quinta/noite.
    #    2 envios < limite de amostra -> a celula existe e NAO pode ser coroada.
    linhas.append(envio("c1", ORG_C, "2026-09-04T02:00:00Z", "envio:pc1:T3"))
    linhas.append(envio("c2", ORG_C, "2026-09-04T02:30:00Z", "envio:pc2:T3"))
    linhas.append(resposta("cr1", ORG_C, "2026-09-04T03:00:00Z", "INTERESSE"))
    # T4 (ORG D): janela INCLUSIVA no limite: envio 12:00Z, resposta exatamente em +6h (18:00Z) CONTA.
    linhas.append(envio("d1", ORG_D, "2026-09-05T12:00:00Z", "envio:pd1:T4"))
    linhas.append(resposta("dr1", ORG_D, "2026-09-05T18:00:00Z", "INTERESSE"))
    # T5 (ORG E): resposta 1s depois do limite NAO conta (mesma forma, so' o segundo a mais).
    linhas.append(envio("e1", ORG_E, "2026-09-06T12:00:00Z", "envio:pe1:T5"))
    linhas.append(resposta("er1", ORG_E, "2026-09-06T18:00:01Z", "INTERESSE"))
    return linhas


def executar(raiz: Path, modulo: Path, estado: Path, *extra: str) -> subprocess.CompletedProcess:
    duble = raiz / DUBLE_REL
    prefixo = f"{sys.executable} {duble} {estado}"
    return subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "dev",
                           "--prefixo", prefixo, *ARGS, *extra],
                          capture_output=True, text=True, timeout=180)


def json_ou_vazio(texto: str) -> dict:
    try:
        return json.loads(texto)
    except Exception:  # noqa: BLE001
        return {}


def celula(relatorio: dict, dia: str, faixa: str) -> dict:
    for linha in relatorio.get("por_janela", []):
        if linha["dia"] == dia and linha["faixa"] == faixa:
            return linha
    return {}


def medir(raiz: Path, modulo: Path, trabalho: Path) -> None:
    estado = trabalho / "estado.json"
    estado.write_text(json.dumps({"interacoes": fixture()}, ensure_ascii=False), encoding="utf-8")

    # ---- V1..V9: a grade e o fuso
    proc = executar(raiz, modulo, estado)
    rel = json_ou_vazio(proc.stdout)
    if not rel:
        item("V0 a analise roda contra o duble (exit 0 com relatorio)", "0/relatorio", f"{proc.returncode}/{proc.stderr[-120:]}")
        return
    item("V0 a analise roda contra o duble (exit 0)", 0, proc.returncode)
    item("V1 grade completa: 7 dias x 7 faixas = 49 celulas", 49, len(rel["por_janela"]))
    item("V2 soma das celulas fecha com o total lido", rel["totais"]["enviadas"],
         sum(c["enviadas"] for c in rel["por_janela"]))
    item("V3 marginais fecham com o total", [rel["totais"]["enviadas"]] * 2,
         [sum(c["enviadas"] for c in rel["por_dia"]), sum(c["enviadas"] for c in rel["por_faixa"])])
    item("V4 fuso -03:00 com virada de dia: sexta 02:00Z = quinta 23:00 local",
         2, celula(rel, "quinta", "noite")["enviadas"])
    item("V5 celula terca/manha (09:00-11:00 locais): 5 enviadas, 2 positivas, taxa 0.4",
         [5, 2, 0.4], [celula(rel, "terca", "manha")["enviadas"], celula(rel, "terca", "manha")["positivas"],
                       celula(rel, "terca", "manha")["taxa_de_interesse"]])
    item("V6 celula quarta/tarde (16:00-18:00 locais): 5 enviadas, 1 positiva, taxa 0.2",
         [5, 1, 0.2], [celula(rel, "quarta", "tarde")["enviadas"], celula(rel, "quarta", "tarde")["positivas"],
                       celula(rel, "quarta", "tarde")["taxa_de_interesse"]])
    item("V7 melhor janela e' a de MAIOR taxa de interesse (terca/manha 0.4 > quarta/tarde 0.2)",
         ["terca", "manha"], [rel["melhor_janela"]["dia"], rel["melhor_janela"]["faixa"]]
         if rel["melhor_janela"] else None)
    item("V8 melhor dia e melhor faixa seguem a mesma regra",
         ["terca", "manha"], [rel["melhor_dia"]["dia"], rel["melhor_faixa"]["faixa"]]
         if rel["melhor_dia"] and rel["melhor_faixa"] else None)
    item("V9 celula com 2 envios NAO tem amostra suficiente (e nao pode ser coroada)",
         [False, 2], [celula(rel, "quinta", "noite")["amostra_suficiente"], celula(rel, "quinta", "noite")["enviadas"]])
    item("V10 celula vazia na saida: taxas null, nunca 0.0", [0, None],
         [celula(rel, "domingo", "noite")["enviadas"], celula(rel, "domingo", "noite")["taxa_de_interesse"]])
    item("V11 celula terca/manha respondeu 2 (comerciais); auto-resposta contada como descarte",
         [2, 1], [celula(rel, "terca", "manha")["respondidas"], rel["totais"]["respostas_descartadas"]])

    # ---- V12/V13: a atribuicao e' a do IRMAO (coerencia medida, nao presumida)
    irmao = subprocess.run([sys.executable, str(raiz / MODULO_IRMAO_REL), "--raiz", str(raiz),
                            "--ambiente", "dev", "--prefixo", f"{sys.executable} {raiz / DUBLE_REL} {estado}",
                            *ARGS], capture_output=True, text=True, timeout=180)
    rel_irmao = json_ou_vazio(irmao.stdout)
    item("V12 o irmao de desempenho roda na MESMA base (exit 0)", 0, irmao.returncode)
    chaves = ("enviadas", "respondidas", "positivas", "negativas", "opt_outs", "respostas_descartadas")
    item("V13 atribuicao unica: totais do melhor-horario == totais do irmao de desempenho",
         {k: rel_irmao["totais"][k] for k in chaves}, {k: rel["totais"][k] for k in chaves})

    # ---- V14/V15: janela de credito inclusiva no limite
    p6 = executar(raiz, modulo, estado, "--janela-dias", "0.25")
    r6 = json_ou_vazio(p6.stdout)
    item("V14 resposta EXATAMENTE no limite da janela (6h) CONTA", 1, celula(r6, "sabado", "manha")["positivas"])
    item("V15 resposta 1s depois do limite NAO conta", 0, celula(r6, "domingo", "manha")["positivas"])

    # ---- V16..V19: determinismo, amostra global e guardas
    p16 = executar(raiz, modulo, estado)
    r16 = json_ou_vazio(p16.stdout)
    item("V16 saida reproduzivel (duas rodadas iguais, sem carimbo)", True, r16 == rel)
    p17 = executar(raiz, modulo, estado, "--limite-amostra", "10")
    r17 = json_ou_vazio(p17.stdout)
    item("V17 nenhuma celula com amostra -> ABSTEM (AMOSTRA_INSUFICIENTE)",
         [None, "AMOSTRA_INSUFICIENTE"], [r17["melhor_janela"], r17["melhor_janela_motivo"]])
    prod = subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "prod",
                           "--prefixo", "psql"], capture_output=True, text=True)
    item("V18 prod RECUSADO exit 4 com motivo declarado", [4, "PROD_RECUSADO"],
         [prod.returncode, json_ou_vazio(prod.stdout).get("motivo")])
    sem = subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--prefixo", "psql"],
                         capture_output=True, text=True)
    item("V19 ambiente nao declarado RECUSA exit 2", [2, "AMBIENTE_NAO_DECLARADO"],
         [sem.returncode, json_ou_vazio(sem.stdout).get("motivo")])
    semp = subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "dev"],
                          capture_output=True, text=True)
    item("V20 porta de banco ausente RECUSA exit 2", [2, "PORTA_DE_BANCO_AUSENTE"],
         [semp.returncode, json_ou_vazio(semp.stdout).get("motivo")])

    # ---- V21/V22: somente leitura (unidade + log do duble)
    sys.path.insert(0, str(raiz / "hermes/analytics"))
    import melhor_horario as mh  # noqa: E402
    import desempenho_mensagens as irmao_mod  # noqa: E402
    try:
        mh.irmao.afirmar_somente_leitura("INSERT INTO sales_intelligence.interactions VALUES (1)")
        recusa = None
    except irmao_mod.Recusa as exc:
        recusa = exc.motivo
    item("V21 escrita RECUSADA na unidade (ESCRITA_RECUSADA)", "ESCRITA_RECUSADA", recusa)
    log = estado.with_suffix(estado.suffix + ".chamadas.jsonl")
    registros = [json.loads(linha) for linha in log.read_text(encoding="utf-8").splitlines() if linha.strip()]
    item("V22 o duble so' recebeu SELECT e nunca recusou escrita",
         [True, 0], [all(r.get("statement", "").upper().startswith("SELECT") for r in registros),
                     sum(1 for r in registros if "recusado" in r)])

    # ---- V23..V26: fail-closed de contrato
    contrato = json.loads((raiz / CONTRATO_REL).read_text(encoding="utf-8"))
    quebrado = trabalho / "grade-com-buraco.json"
    faixas = [dict(f) for f in contrato["faixas"]]
    faixas[3]["de"] = faixas[3]["de"] + 1  # abre buraco de 1h
    contrato["faixas"] = faixas
    quebrado.write_text(json.dumps(contrato, ensure_ascii=False), encoding="utf-8")
    p23 = executar(raiz, modulo, estado, "--contrato", str(quebrado))
    item("V23 grade com buraco -> RECUSA exit 3", [3, True],
         [p23.returncode, json_ou_vazio(p23.stdout).get("motivo", "").startswith("GRADE")])

    fuso_ruim = json.loads((raiz / CONTRATO_REL).read_text(encoding="utf-8"))
    fuso_ruim["fuso"] = {"nome": "America/Sao_Paulo", "offset_utc": "-3"}
    arquivo_fuso = trabalho / "fuso-ruim.json"
    arquivo_fuso.write_text(json.dumps(fuso_ruim, ensure_ascii=False), encoding="utf-8")
    p24 = executar(raiz, modulo, estado, "--contrato", str(arquivo_fuso))
    item("V24 fuso invalido -> RECUSA exit 3 (FUSO_INVALIDO)", [3, "FUSO_INVALIDO"],
         [p24.returncode, json_ou_vazio(p24.stdout).get("motivo")])

    dono = json.loads((raiz / IRMAO_IRMAO_REL).read_text(encoding="utf-8"))
    dono["vocabulario"]["response_category"] = [c for c in dono["vocabulario"]["response_category"]
                                                if c != "OPT_OUT"]
    dono_quebrado = trabalho / "dono-sem-categoria.json"
    dono_quebrado.write_text(json.dumps(dono, ensure_ascii=False), encoding="utf-8")
    p25 = executar(raiz, modulo, estado, "--contrato-do-irmao-irmao", str(dono_quebrado))
    item("V25 vocabulario do dono sem uma categoria -> CONTRATO_INCOERENTE exit 3",
         [3, "CONTRATO_INCOERENTE"], [p25.returncode, json_ou_vazio(p25.stdout).get("motivo")])

    # ---- V26/V27: regras e PII
    p26 = subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "dev", "--regras"],
                         capture_output=True, text=True)
    regras = json_ou_vazio(p26.stdout)
    item("V26 --regras traz o fuso declarado e a cobertura da grade",
         ["-03:00", True], [regras.get("fuso", {}).get("offset_utc"),
                            "PARTICIONAR" in regras.get("cobertura_da_grade", {}).get("regra", "")])
    texto = json.dumps(rel, ensure_ascii=False)
    vazamentos = [t for t in (ORG_A[:8], ORG_B[:8], "pa1", "pb1", "envio:") if t in texto]
    item("V27 saida agregada: sem organization_id, sem approval_id, sem referencia de envio", [], vazamentos)

    # ---- V28: recorte sem envios
    vazio = trabalho / "vazio.json"
    vazio.write_text(json.dumps({"interacoes": []}), encoding="utf-8")
    p28 = executar(raiz, modulo, vazio)
    r28 = json_ou_vazio(p28.stdout)
    item("V28 base sem envio: veredito SEM_ENVIOS e ranking abstem",
         ["SEM_ENVIOS", None, "SEM_ENVIOS"], [r28["veredito"], r28["melhor_janela"], r28["melhor_janela_motivo"]])


# Cada dente tem de REPROVAR o item que nomeia (mutacao aplicada em COPIA temporaria).
MUTACOES = [
    ("offset-ignorado", MODULO_REL,
     [('local = momento.astimezone(timezone.utc) + contrato["_offset"]',
       'local = momento.astimezone(timezone.utc)')],
     "V4 fuso -03:00 com virada de dia: sexta 02:00Z = quinta 23:00 local"),
    ("amostra-desligada", MODULO_REL,
     [('elegiveis = [g for g in grupos if g["amostra_suficiente"]]', "elegiveis = list(grupos)")],
     "V17 nenhuma celula com amostra -> ABSTEM (AMOSTRA_INSUFICIENTE)"),
    ("grade-aceita-buraco", MODULO_REL,
     [("        if de != cursor:", "        if False:")],
     "V23 grade com buraco -> RECUSA exit 3"),
    ("fuso-sem-validacao", MODULO_REL,
     [('if not casamento or int(casamento.group(2)) > 14 or int(casamento.group(3)) > 59:',
       "if False:")],
     "V24 fuso invalido -> RECUSA exit 3 (FUSO_INVALIDO)"),
    ("ranking-por-chave", MODULO_REL,
     [('key=lambda g: tuple([-(g["taxa_de_interesse"] or 0.0), -(g["taxa_de_resposta"] or 0.0), -g["enviadas"]]\n'
       '                            + [g[campo] for campo in campos_de_desempate]),',
       'key=lambda g: tuple([g[campo] for campo in campos_de_desempate]),')],
     "V7 melhor janela e' a de MAIOR taxa de interesse (terca/manha 0.4 > quarta/tarde 0.2)"),
    ("segunda-regra-de-credito", MODULO_REL,
     [('creditos = irmao.creditar(envios, respostas, dados["janela"])',
       'creditos = {}\n'
       '    for _e in envios:\n'
       '        for _r in respostas:\n'
       '            if (_r["organization_id"] == _e["organization_id"] and _r["momento"] > _e["momento"]\n'
       '                    and _r["momento"] - _e["momento"] <= dados["janela"]):\n'
       '                creditos.setdefault(_e["id"], []).append(_r)')],
     "V13 atribuicao unica: totais do melhor-horario == totais do irmao de desempenho"),
]


def preparar_arvore(destino: Path, raiz: Path) -> None:
    for rel in ("hermes/analytics", "hermes/agentes/respostas", "scripts/agentes"):
        (destino / rel).mkdir(parents=True, exist_ok=True)
    for rel in (CONTRATO_REL, MODULO_REL, CONTRATO_IRMAO_REL, IRMAO_IRMAO_REL, MODULO_IRMAO_REL, DUBLE_REL):
        shutil.copy2(raiz / rel, destino / rel)


def autoteste(raiz: Path, modulo: Path, trabalho_base: Path) -> int:
    print("== autoteste por mutacao (cada mutacao tem de reprovar O ITEM QUE NOMEIA) ==")
    falhas = 0
    for nome, arquivo_rel, trocas, item_esperado in MUTACOES:
        destino = trabalho_base / f"mut-{nome}"
        if destino.exists():
            shutil.rmtree(destino)
        preparar_arvore(destino, raiz)
        copia = destino / arquivo_rel
        texto = copia.read_text(encoding="utf-8")
        for de, para in trocas:
            if de not in texto:
                print(f"ABORTA mutacao {nome}: ancora nao encontrada ({de[:60]!r}) — mutacao que nao aplica e' buraco")
                return 1
            texto = texto.replace(de, para, 1)
        copia.write_text(texto, encoding="utf-8")
        trabalho = destino / "trabalho"
        trabalho.mkdir(parents=True, exist_ok=True)
        ITENS.clear()
        medir(destino, destino / MODULO_REL, trabalho)
        estado_do_item = {n: ok for n, ok in ITENS}
        if item_esperado not in estado_do_item:
            print(f"ABORTA mutacao {nome}: o item {item_esperado!r} nem existe na bateria")
            return 1
        if estado_do_item[item_esperado]:
            print(f"FALHOU dente {nome}: mutou e o item {item_esperado!r} continuou VERDE")
            falhas += 1
        else:
            print(f"OK     dente {nome} -> reprovou {item_esperado!r}")
    return falhas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raiz", default=None)
    parser.add_argument("--codigo", default=None)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve() if args.raiz else Path(__file__).resolve().parents[2]
    modulo = Path(args.codigo).resolve() if args.codigo else raiz / MODULO_REL
    trabalho = Path(tempfile.mkdtemp(prefix="horario-"))
    ITENS.clear()
    medir(raiz, modulo, trabalho)
    falhas = sum(1 for _, ok in ITENS if not ok)
    dentes = 0
    if args.autoteste:
        trabalho_base = Path(tempfile.mkdtemp(prefix="horario-dentes-"))
        dentes = autoteste(raiz, modulo, trabalho_base)
        falhas += dentes
    if falhas:
        print(f"\nFALHOU (melhor horario v1: {len(ITENS)} itens, {falhas} falhas)")
        return 1
    extra = f" + autoteste OK ({len(MUTACOES)}/{len(MUTACOES)} mutacoes detectadas)" if args.autoteste else ""
    print(f"\nVERIFICADOR_MELHOR_HORARIO_PASS ({len(ITENS)} itens, 0 falhas){extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
