#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE do Next Best Action v1 (card TRE-W5-E07-T01) — sem banco, sem rede.

Mede o que e medivel sem PostgreSQL: a coerencia politica x contrato, a abstencao e a recusa da
politica (fail-closed), a ORDEM da tabela de decisao, a idempotencia do id, a guarda de escrita e o
fato de o codigo NAO carregar a decisao (nem a lista de acoes, nem os ids das regras, nem os papeis
de decisao) — a decisao vem da politica e o vocabulario do Data Contract.

  python3 scripts/scores/verificar_nba.py
  python3 scripts/scores/verificar_nba.py --autoteste

Exit: 0 = VERIFICACAO_NBA_OK · 1 = reprovou · 2 = uso.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
POLITICA_PADRAO = "hermes/scores/nba/politica-nba-v1.json"
MODULO_PADRAO = "hermes/scores/nba/nba.py"

ITENS_OK = 0
ITENS_FALHOU = 0
FALHAS: list[str] = []


def item(nome: str, esperado, obtido) -> None:
    global ITENS_OK, ITENS_FALHOU
    if esperado == obtido:
        ITENS_OK += 1
        print(f"OK     {nome} ({obtido})")
    else:
        ITENS_FALHOU += 1
        FALHAS.append(nome)
        print(f"FALHOU {nome} (esperado={esperado} obtido={obtido})")


def carregar_modulo(caminho: Path):
    spec = importlib.util.spec_from_file_location("nba_sob_teste", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def descobrir_raiz_padrao() -> Path:
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / POLITICA_PADRAO).is_file():
                return pasta
    return Path.cwd()


def constantes_de_texto(caminho: Path) -> list[str]:
    """Toda string literal do codigo (AST), inclusive as de dentro de f-strings."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    textos = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            textos.append(no.value)
    return textos


def casos_dos_fatos() -> list[tuple[str, str, dict]]:
    """(regra esperada, acao esperada, fatos) — um caso por regra da politica."""
    base = {"tier": "A", "organizacao": "org-1", "tier_registro_id": "reg-1", "tier_version": "tiering-v1"}
    casos = [
        ("R01", "NURTURE", {"contatos_bloqueados": 1, "contatos_contactaveis": 0,
                            "decisor_contactavel": False}),
        ("R02", "DISQUALIFY", {"dor_rejeitada": True, "sinais_ativos": 0, "pesquisada": True}),
        ("R03", "NURTURE", {"tier": "Nurture"}),
        ("R04", "RESEARCH_MORE", {"pesquisada": False}),
        ("R05", "CREATE_MEETING", {"pesquisada": True, "respondeu": True, "sentimento_da_resposta": "POSITIVO",
                                   "contatos_contactaveis": 1, "decisor_contactavel": True,
                                   "contato_tem_email_escolhido": True}),
        ("R06", "NURTURE", {"pesquisada": True, "respondeu": True, "sentimento_da_resposta": "NEGATIVO"}),
        ("R07", "FOLLOW_UP", {"pesquisada": True, "respondeu": True, "sentimento_da_resposta": "NEUTRO"}),
        ("R08", "WAIT", {"pesquisada": True, "abordada": True, "dias_desde_a_abordagem": 1}),
        ("R09", "FOLLOW_UP", {"pesquisada": True, "abordada": True, "dias_desde_a_abordagem": 5}),
        ("R10", "FIND_DECISION_MAKER", {"pesquisada": True, "decisor_contactavel": False,
                                        "contatos_contactaveis": 1}),
        ("R11", "PREPARE_LINKEDIN", {"pesquisada": True, "decisor_contactavel": True,
                                     "contato_tem_email_escolhido": False}),
        ("R12", "SEND_EMAIL", {"pesquisada": True, "decisor_contactavel": True,
                               "contato_tem_email_escolhido": True}),
    ]
    return [(regra, acao, {**base, **fatos}) for regra, acao, fatos in casos]


def mutar(caminho_origem: Path, caminho_destino: Path, de: str, para: str) -> None:
    texto = caminho_origem.read_text(encoding="utf-8")
    if de not in texto:
        raise SystemExit(f"ancora ausente na mutacao: {de!r}")
    caminho_destino.write_text(texto.replace(de, para, 1), encoding="utf-8")


def verificar(raiz: Path, caminho_modulo: Path, caminho_politica: Path) -> int:
    global ITENS_OK, ITENS_FALHOU, FALHAS
    ITENS_OK = 0
    ITENS_FALHOU = 0
    FALHAS = []
    caminho_contrato = raiz / CONTRATO_DADOS_PADRAO
    modulo = carregar_modulo(caminho_modulo)
    politica = json.loads(caminho_politica.read_text(encoding="utf-8"))
    contrato = json.loads(caminho_contrato.read_text(encoding="utf-8"))
    vocabulario = contrato["vocabularies"]["next_best_action"]
    textos = constantes_de_texto(caminho_modulo)

    # ---------------------------------------------------------------- A: identidade e coerencia
    item("A1 identidade do componente", ("nba", "1.0.0", "nba-v1"),
         (modulo.AGENTE, modulo.VERSAO, modulo.NBA_VERSION))
    item("A2 politica padrao e o arquivo do card", POLITICA_PADRAO, modulo.POLITICA_PADRAO)
    item("A2 contrato do componente versionado", True,
         (raiz / modulo.CONTRATO_DO_COMPONENTE_PADRAO).is_file())
    carregada, _, lido = _carregar_ou_reportar(modulo, caminho_politica, caminho_contrato)
    if carregada is None:
        print()
        print(f"RESULTADO: VERIFICACAO_NBA_FALHOU ({ITENS_OK} itens, {ITENS_FALHOU} falhas)")
        return 1
    item("A9 politica do card carrega e valida (fail-closed)", "carregavel", "carregavel")
    item("A3 vocabulario lido do Data Contract", sorted(vocabulario), sorted(lido))
    item("A4 toda acao da politica existe no contrato", [], 
         sorted({r["acao"] for r in carregada["regras"]} - set(vocabulario)))
    item("A5 cobertura: acao do contrato sem regra e declarada", [],
         sorted(set(vocabulario) - {r["acao"] for r in carregada["regras"]}
                - set(carregada.get("nao_alcancadas") or [])))
    fatos_declarados = set(carregada["fatos"]) - {"origem"}
    usados = {c["fato"] for r in carregada["regras"] for c in r["quando"]}
    item("A6 todo fato usado esta declarado", [], sorted(usados - fatos_declarados))
    item("A7 todo operador usado e conhecido", [], 
         sorted({c["op"] for r in carregada["regras"] for c in r["quando"]
                 if c["op"] not in modulo.OPERADORES}))
    item("A8 ids de regra unicos", len(carregada["regras"]),
         len({r["id"] for r in carregada["regras"]}))
    item("A8 politica tem 12 regras", 12, len(carregada["regras"]))
    item("A8 papeis de decisao dentro do vocabulario do contrato", [],
         sorted(set(carregada["papeis_de_decisao"]) - set(contrato["vocabularies"]["decision_role"])))

    # ---------------------------------------------------------------- B: o codigo NAO carrega a decisao
    literais = set(vocabulario) & set(textos)
    item("C1 nenhuma acao do contrato escrita no codigo", set(), literais)
    papeis_literais = set(carregada["papeis_de_decisao"]) & set(textos)
    item("C2 nenhum papel de decisao escrito no codigo", set(), papeis_literais)
    ids_literais = {r["id"] for r in carregada["regras"]} & set(textos)
    item("C3 nenhum id de regra escrito no codigo", set(), ids_literais)
    item("C3 o limiar de espera nao esta no codigo", False,
         str(carregada["dias_de_espera_antes_do_follow_up"]) in textos)

    # ---------------------------------------------------------------- C: recusa da politica (fail-closed)
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        contrato_mudo = temp / "contrato-sem-vocabulario.json"
        contrato_mudo.write_text(json.dumps({"vocabularies": {}}), encoding="utf-8")
        item("B1 contrato sem vocabulario RECUSA", True,
             recusa(modulo, caminho_politica, contrato_mudo))
        acao_estranha = temp / "politica-acao-estranha.json"
        mutar(caminho_politica, acao_estranha, '"acao": "SEND_EMAIL",', '"acao": "LIGAR_PARA_O_CFO",')
        item("B1 acao fora do contrato RECUSA", True, recusa(modulo, acao_estranha, caminho_contrato))
        buraco = temp / "politica-buraco.json"
        mutar(caminho_politica, buraco, '"acao": "SEND_EMAIL",', '"acao": "NURTURE",')
        item("B2 acao do contrato sem regra RECUSA", True, recusa(modulo, buraco, caminho_contrato))
        operador = temp / "politica-operador.json"
        mutar(caminho_politica, operador, '"op": "menor_que"', '"op": "mais_ou_menos"')
        item("B3 operador desconhecido RECUSA", True, recusa(modulo, operador, caminho_contrato))
        fato = temp / "politica-fato.json"
        mutar(caminho_politica, fato, '{"fato": "tier", "op": "igual"', '{"fato": "tier_fantasma", "op": "igual"')
        item("B4 fato nao declarado RECUSA", True, recusa(modulo, fato, caminho_contrato))
        sem_motivo = temp / "politica-sem-motivo.json"
        mutar(caminho_politica, sem_motivo, '"motivo": "TIER_NURTURE",', '"motivo": "",')
        item("B5 regra sem campo obrigatorio RECUSA", True, recusa(modulo, sem_motivo, caminho_contrato))

    # ---------------------------------------------------------------- D: guarda de escrita
    item("D1 DDL recusado pela guarda", True,
         recusa_escrita(modulo, "CREATE TABLE sales_intelligence.nova (id UUID);"))
    item("D2 escrita em scores recusada", True,
         recusa_escrita(modulo, "INSERT INTO sales_intelligence.scores (id) VALUES ('x');"))
    item("D3 escrita em organizations recusada", True,
         recusa_escrita(modulo, "UPDATE sales_intelligence.organizations SET status = 'x';"))
    item("D4 DELETE sem confirmacao recusado", True,
         recusa_escrita(modulo, "DELETE FROM sales_intelligence.recommendations WHERE id IS NULL;"))
    item("D5 DELETE com confirmacao e aceito", False,
         recusa_escrita(modulo, "DELETE FROM sales_intelligence.recommendations WHERE id IS NULL;",
                        permitir_delete=True))
    item("D5 DELETE em OUTRA tabela continua recusado com confirmacao", True,
         recusa_escrita(modulo, "DELETE FROM sales_intelligence.contacts WHERE id IS NULL;",
                        permitir_delete=True))
    item("D5 INSERT da recomendacao e aceito", False,
         recusa_escrita(modulo, "INSERT INTO sales_intelligence.recommendations (id) VALUES ('x');"))
    item("D5 INSERT da auditoria e aceito", False,
         recusa_escrita(modulo, "INSERT INTO sales_intelligence.agent_runs (id) VALUES ('x');"))
    item("D5 escrita em sync_events recusada (integracao e W3/W6)", True,
         recusa_escrita(modulo, "INSERT INTO sales_intelligence.sync_events (id) VALUES ('x');"))

    # ---------------------------------------------------------------- E: tabela de decisao (puro)
    for regra_esperada, acao_esperada, fatos in casos_dos_fatos():
        regra, _ = modulo.decidir(fatos, carregada)
        item(f"E3 {regra_esperada} -> {acao_esperada}", (regra_esperada, acao_esperada),
             (regra["id"] if regra else None, regra["acao"] if regra else None))
    item("E2 sem tier nao decide (abstem)", (None, None), modulo.decidir({"tier": None}, carregada))
    item("E1 primeira regra que casa vence (compliance antes de pesquisa)",
         ("R01", "NURTURE"),
         _tupla(modulo.decidir({"tier": "A", "contatos_bloqueados": 2, "contatos_contactaveis": 0,
                                "decisor_contactavel": False, "pesquisada": False}, carregada)))
    item("E4 maior/menor sao estritos", True,
         modulo.avaliar_condicao({"n": 3}, {"fato": "n", "op": "menor_que", "valor": 3}) is False
         and modulo.avaliar_condicao({"n": 3}, {"fato": "n", "op": "maior_ou_igual", "valor": 3}) is True)
    item("E5 negado inverte a condicao", True,
         modulo.avaliar_condicao({"n": None}, {"fato": "n", "op": "nulo", "negado": True}) is False)
    item("E6 fato ausente nao casa", False,
         modulo.avaliar_condicao({}, {"fato": "n", "op": "igual", "valor": False}))
    item("E6 fato ausente nao casa em contagem", False,
         modulo.avaliar_condicao({}, {"fato": "n", "op": "maior_que", "valor": 0}))

    # ---------------------------------------------------------------- F: idempotencia por conteudo
    regra, fatos = None, None
    for regra_esperada, _, fatos_caso in casos_dos_fatos():
        if regra_esperada == "R12":
            regra, fatos = modulo.decidir(fatos_caso, carregada)
    h1 = modulo.identidade_da_entrada(fatos, carregada, regra, regra["acao"])
    h2 = modulo.identidade_da_entrada(fatos, carregada, regra, regra["acao"])
    item("F1 entrada identica => mesmo hash", h1, h2)
    id1 = modulo.id_da_recomendacao(fatos["organizacao"], h1)
    id2 = modulo.id_da_recomendacao(fatos["organizacao"], h2)
    item("F1 id deterministico", id1, id2)
    item("F1 id e uuid5 previsivel", True, id1 == modulo.id_da_recomendacao(
        fatos["organizacao"], h1))
    outra = dict(fatos)
    outra["contato_escolhido_id"] = "outro-contato"
    item("F2 contato diferente => id diferente", True,
         modulo.id_da_recomendacao(fatos["organizacao"],
                                   modulo.identidade_da_entrada(outra, carregada, regra, regra["acao"])) != id1)
    com_relogio = dict(fatos)
    com_relogio["correlation_id"] = "e2222222-2222-4222-8222-222222222222"
    com_relogio["gerado_em"] = "2026-10-03T00:00:00Z"
    item("F4 relogio/correlacao nao entram no hash", h1,
         modulo.identidade_da_entrada(com_relogio, carregada, regra, regra["acao"]))
    regra_wait, fatos_wait = None, None
    for regra_esperada, _, fatos_caso in casos_dos_fatos():
        if regra_esperada == "R08":
            regra_wait, fatos_wait = modulo.decidir(fatos_caso, carregada)
    contador_igual = dict(fatos_wait)
    contador_igual["dias_desde_a_abordagem"] = 2
    item("F3 contador numerico nao muda o id (mesma condicao)",
         modulo.identidade_da_entrada(fatos_wait, carregada, regra_wait, regra_wait["acao"]),
         modulo.identidade_da_entrada(contador_igual, carregada, regra_wait, regra_wait["acao"]))
    contador_estourado = dict(fatos_wait)
    contador_estourado["dias_desde_a_abordagem"] = 9
    item("F3 contador fora da condicao muda a decisao (R09, nao R08)", "R09",
         (modulo.decidir(contador_estourado, carregada)[0] or {}).get("id"))

    # ---------------------------------------------------------------- G: CLI sem banco
    sem_porta = ["docker", "exec", "-i", "container-que-nao-existe"]
    rc, saida, _ = rodar_cli(["--planejar", "--prefixo", " ".join(sem_porta), "--raiz", str(raiz)])
    item("G1 --planejar exit 0 sem conexao", 0, rc)
    item("G1 --planejar declara llm nao executado", False, json.loads(saida)["llm"]["executado"])
    rc, saida, _ = rodar_cli(["--regras", "--raiz", str(raiz)])
    item("G2 --regras exit 0 sem conexao", 0, rc)
    regras_cli = json.loads(saida)
    item("G2 --regras lista as 12 regras", 12, len(regras_cli["regras"]))
    item("G2 --regras sem acao descoberta", [], regras_cli["acoes_do_contrato_sem_regra"])
    rc, _, _ = rodar_cli(["--ambiente", "prod", "--organizacao", "x", "--prefixo", " ".join(sem_porta),
                          "--raiz", str(raiz)])
    item("G3 prod recusado (exit 4)", 4, rc)
    rc, _, _ = rodar_cli(["--organizacao", "x", "--prefixo", " ".join(sem_porta), "--raiz", str(raiz)])
    item("G4 sem --ambiente e uso invalido (exit 2)", 2, rc)
    rc, _, _ = rodar_cli(["--ambiente", "dev", "--organizacao", "x", "--raiz", str(raiz)])
    item("G4 sem --prefixo e uso invalido (exit 2)", 2, rc)

    # ---------------------------------------------------------------- H: declaracoes do contrato
    componente = json.loads((raiz / modulo.CONTRATO_DO_COMPONENTE_PADRAO).read_text(encoding="utf-8"))
    item("H1 contrato do componente declara as tabelas escritas",
         ["sales_intelligence.recommendations", "sales_intelligence.agent_runs"],
         componente["persistencia"]["tabelas_escritas"])
    item("H2 contrato declara a confianca NULL", True, "NULL" in componente["modelo"]["confianca"])
    item("H2 contrato declara as lacunas", True, len(componente["lacunas"]) >= 3)
    item("H3 contrato do componente cita a fonte das acoes", True,
         "vocabularies.next_best_action" in componente["modelo"]["fonte_das_acoes"])
    item("H3 politica cita a fonte do vocabulario", True,
         "vocabularies.next_best_action" in politica["vocabulario_das_acoes"])

    print()
    print(f"RESULTADO: {'VERIFICACAO_NBA_OK' if ITENS_FALHOU == 0 else 'VERIFICACAO_NBA_FALHOU'} "
          f"({ITENS_OK} itens, {ITENS_FALHOU} falhas)")
    return 0 if ITENS_FALHOU == 0 else 1


def _tupla(par):
    regra, _ = par
    return (regra["id"], regra["acao"]) if regra else (None, None)


def _carregar_ou_reportar(modulo, caminho_politica: Path, caminho_contrato: Path):
    """Carrega a politica do card. Se ela nao valer, o ITEM sobre a propria carga reprova (e o
    verificador para ali: sem politica coerente nao ha o que medir)."""
    try:
        return modulo.carregar_politica(caminho_politica, caminho_contrato)
    except modulo.RecusaDePolitica as recusa_politica:
        item("A9 politica do card carrega e valida (fail-closed)", "carregavel",
             f"RECUSADA {recusa_politica}")
        return None, None, None


def recusa(modulo, caminho_politica: Path, caminho_contrato: Path) -> bool:
    try:
        modulo.carregar_politica(caminho_politica, caminho_contrato)
    except modulo.RecusaDePolitica:
        return True
    except Exception:  # noqa: BLE001 - qualquer outro erro nao e a recusa esperada
        return False
    return False


def recusa_escrita(modulo, sql: str, permitir_delete: bool = False) -> bool:
    try:
        modulo.validar_sql(sql, permitir_delete=permitir_delete)
    except modulo.RecusaDeEscrita:
        return True
    return False


def rodar_cli(args: list[str], modulo: Path | None = None) -> tuple[int, str, str]:
    caminho = str(modulo or MODULO_SOB_TESTE)
    processo = subprocess.run([sys.executable, caminho, *args], capture_output=True, text=True)
    return processo.returncode, processo.stdout, processo.stderr


MODULO_SOB_TESTE = Path(MODULO_PADRAO)
POLITICA_SOB_TESTE = Path(POLITICA_PADRAO)

DENTES = [
    # tipo | nome | ancora | troca | item que PRECISA reprovar
    ("politica", "acao-fora-do-contrato", '"acao": "SEND_EMAIL",', '"acao": "LIGAR_PARA_O_CFO",',
     "A9 politica do card carrega e valida (fail-closed)"),
    ("politica", "buraco-de-cobertura", '"acao": "SEND_EMAIL",', '"acao": "NURTURE",',
     "A9 politica do card carrega e valida (fail-closed)"),
    ("codigo", "acao-hardcoded-no-codigo", 'OPERACAO_TIER = "TIER"', 'OPERACAO_TIER = "SEND_EMAIL"',
     "C1 nenhuma acao do contrato escrita no codigo"),
    ("codigo", "guarda-liberada",
     'PADRAO_DDL = re.compile(r"\\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT\\s+ON)\\b", re.IGNORECASE)',
     'PADRAO_DDL = re.compile(r"ZZZ_NAO_EXISTE", re.IGNORECASE)', "D1 DDL recusado pela guarda"),
    ("codigo", "ordem-invertida", 'for regra in politica["regras"]:',
     'for regra in list(politica["regras"])[::-1]:', "E1 primeira regra que casa vence"),
    ("codigo", "id-nao-deterministico",
     'return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NBA_VERSION}:{organization_id}:{entrada_hash}"))',
     'return str(uuid.uuid4())', "F1 id deterministico"),
    ("codigo", "contador-no-hash", 'itens[fato] = True if numerico else valor', 'itens[fato] = valor',
     "F3 contador numerico nao muda o id"),
]


def autoteste(raiz: Path, caminho_modulo: Path, caminho_politica: Path) -> int:
    print("== autoteste por mutacao (cada mutacao tem de reprovar o ITEM esperado)")
    falhas = 0
    for tipo, nome, de, para, item_esperado in DENTES:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            mut_modulo = temp / "nba-mutado.py"
            mut_politica = temp / "politica-mutada.json"
            if tipo == "politica":
                mutar(caminho_politica, mut_politica, de, para)
                politica_do_teste = mut_politica
                modulo_do_teste = caminho_modulo
            else:
                mutar(caminho_modulo, mut_modulo, de, para)
                politica_do_teste = caminho_politica
                modulo_do_teste = mut_modulo
            processo = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--raiz", str(raiz),
                 "--codigo", str(modulo_do_teste), "--politica", str(politica_do_teste)],
                capture_output=True, text=True)
            if f"FALHOU {item_esperado} " in processo.stdout and "VERIFICACAO_NBA_OK" not in processo.stdout:
                print(f"OK     dente {nome}: reprovou o item esperado ({item_esperado})")
            else:
                print(f"FALHOU dente {nome}: NAO reprovou o item esperado ({item_esperado})")
                falhas += 1
    print()
    print(f"RESULTADO: {'AUTOTESTE OK' if falhas == 0 else 'AUTOTESTE FALHOU'} "
          f"({len(DENTES) - falhas}/{len(DENTES)} mutacoes detectadas, cada uma pelo item esperado)")
    return 0 if falhas == 0 else 1


def main(argv: list[str] | None = None) -> int:
    global MODULO_SOB_TESTE, POLITICA_SOB_TESTE
    parser = argparse.ArgumentParser(description="verificador offline do NBA v1 (TRE-W5-E07-T01)")
    parser.add_argument("--raiz", default=None)
    parser.add_argument("--codigo", default=None)
    parser.add_argument("--politica", default=None)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve() if args.raiz else descobrir_raiz_padrao()
    MODULO_SOB_TESTE = Path(args.codigo).resolve() if args.codigo else raiz / MODULO_PADRAO
    POLITICA_SOB_TESTE = Path(args.politica).resolve() if args.politica else raiz / POLITICA_PADRAO
    if args.autoteste:
        return autoteste(raiz, MODULO_SOB_TESTE, POLITICA_SOB_TESTE)
    return verificar(raiz, MODULO_SOB_TESTE, POLITICA_SOB_TESTE)


if __name__ == "__main__":
    sys.exit(main())
