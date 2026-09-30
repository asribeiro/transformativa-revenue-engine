#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador da JEV Decision Policy v1.1 (card TRE-W0-E04-T06).

Mesmo rito da v1.0 (`scripts/verificar_jev_policy.py`): documento e arquivo legivel por
maquina NAO podem divergir, e o verificador prova isso item por item, com autoteste por
mutacao em COPIA temporaria (o arquivo versionado nunca e tocado).

O que este verificador acrescenta — e por que cada item existe:

  PARTE 1 — a bateria da v1.0, inteira, sobre a v1.1. A v1.1 nao tem bateria propria de
    coerencia: ela tem de passar nos MESMOS 42 itens, com a versao parametrizada. Item
    novo nunca substitui item antigo (nao ha "a v1.1 muda tudo").

  PARTE 2 — o contrato novo, declarado:
    * `limiares.lane_conservadora` existe, aponta lane declarada e CONCORDA com as duas
      formas em prosa (`fallback.acao` e o parentetico de `limiares.empate`);
    * a regra de lane por ambiente esta declarada de forma ESTRUTURADA (dois ramos, valores
      enumerados, lane minima por ramo, aprovacao humana no ramo vivo, ramo do ambiente
      nao declarado) e o documento diz a mesma coisa;
    * as metricas declaradas em `metricas.acompanhar` tem instrumentacao: o que e medivel,
      o que NAO e medivel e COM QUE MOTIVO, e os limites que nao podem ser esquecidos
      (o custo nao enxerga rebaixamento `critical -> high` porque as duas lanes tem a mesma
      classe de custo);
    * o rascunho se declara rascunho, e ninguem pode marca-lo homologado sem o registro
      do Anderson em `docs/operations/registro-de-aprovacoes.md`.

  PARTE 3 — as provas de COMPORTAMENTO contra o roteador em vigor (`hermes/jev/routing/router.py`).
    Prosa em YAML nao prova nada; estas provas so valem se exercitarem o codigo:
    B1. com a chave explicita presente e as DUAS prosas REMOVIDAS, o roteador ainda resolve a
        lane conservadora — e o que torna a chave "legivel por maquina" de fato. Hoje a v1.0
        nao tem a chave: sem prosa, nao ha lane (e o que esta provado em B1b).
    B2. chave discordando da prosa = politica inconsistente = recusa (fail-closed).
    B3. a v1.1 e RECUSADA pelo roteador em vigor (versao desconhecida): o rascunho e inerte por
        construcao, nao por promessa.
    B4. o vocabulario de ambiente declarado na regra bate, VALOR A VALOR, com o guardrail de DDL
        do roteador (valores do ramo dev nao acionam; valores do ramo vivo acionam; ambiente
        nao declarado aciona).
    B5. enquanto `execucao.roteador_que_a_executa` for null, o piso NAO esta ativo: um card com
        DDL declarada em ambiente vivo NAO e elevado a `critical` pelo roteador em vigor.

Uso:
    python3 scripts/verificar_jev_policy_v1_1.py                # verifica
    python3 scripts/verificar_jev_policy_v1_1.py --autoteste     # verifica + mutacoes

Saida: OK/FALHOU por item + RESULTADO final. Codigo de saida 0 so quando tudo passa
(inclusive o autoteste, quando pedido).
"""
from __future__ import annotations

import copy
import importlib.util
import os
import pathlib
import re
import site
import sqlite3
import sys
import tempfile

# ---------------------------------------------------------------------------
# PyYAML: mesma descoberta de ambiente dos outros verificadores do repo.
# ---------------------------------------------------------------------------
try:
    import yaml
except ModuleNotFoundError:
    for _candidato in ("/opt/hermes/.venv/lib/python3.13/site-packages",
                       "/opt/data/.venv/lib/python3.13/site-packages"):
        if pathlib.Path(_candidato).is_dir():
            site.addsitedir(_candidato)
    try:
        import yaml
    except ModuleNotFoundError:
        print("FALHOU PyYAML nao encontrado. Rode com o python do ambiente Hermes:\n"
              "  /opt/hermes/.venv/bin/python scripts/verificar_jev_policy_v1_1.py")
        sys.exit(2)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
YAML_V10 = RAIZ / "hermes/jev/policy_v1.yaml"
DOC_V10 = RAIZ / "docs/architecture/jev-decision-policy-v1.md"
YAML_V11 = RAIZ / "hermes/jev/policy_v1_1.yaml"
DOC_V11 = RAIZ / "docs/architecture/jev-decision-policy-v1.1.md"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
REGISTRO_DE_APROVACOES = RAIZ / "docs/operations/registro-de-aprovacoes.md"

VERSAO_V10 = "jev-policy-v1.0"
VERSAO_V11 = "jev-policy-v1.1"

# Ramo da regra por ambiente -> (lane minima esperada, aprovacao humana esperada).
RAMOS_ESPERADOS = (
    ("novo_ou_dev", "high", False),
    ("vivo_ou_producao", "critical", True),
)

# Marcador do limite que a instrumentacao de custo NAO pode esquecer.
MARCADOR_DO_LIMITE_DE_CUSTO = "critical"
MARCADOR_DA_CEGUEIRA = "alto"


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------
def carregar_modulo(caminho: pathlib.Path, nome: str):
    especificacao = importlib.util.spec_from_file_location(nome, str(caminho))
    assert especificacao is not None and especificacao.loader is not None
    modulo = importlib.util.module_from_spec(especificacao)
    sys.modules[nome] = modulo
    especificacao.loader.exec_module(modulo)
    return modulo


class Itens:
    def __init__(self):
        self.lista: list[tuple[str, bool, str]] = []

    def add(self, nome, ok, detalhe=""):
        self.lista.append((str(nome), bool(ok), str(detalhe)))

    def checar(self, nome, funcao):
        try:
            ok, detalhe = funcao()
        except Exception as erro:  # noqa: BLE001 - a suite precisa registrar tudo
            ok, detalhe = False, f"excecao: {type(erro).__name__}: {erro}"
        self.add(nome, ok, detalhe)

    def falhas(self):
        return sum(1 for _, ok, _ in self.lista if not ok)

    def extrair(self, inicia_com):
        """Itens cujo nome comeca com o prefixo — para reusar a bateria da v1.0."""
        return [i for i in self.lista if i[0].startswith(inicia_com)]


def texto_de_yaml(dado: dict) -> str:
    return yaml.safe_dump(dado, allow_unicode=True, sort_keys=False)


def _card_do_board(card_id: str):
    """(ok, detalhe): o card citado pela politica existe no board de verdade?

    Le o SQLite do board em modo somente-leitura (o mesmo caminho que o roteador usa).
    Board ausente neste host = item nao aplicavel (deterministico e declarado, nunca
    "passou porque nao olhei"): o host autoritativo do TRE tem o board, e la o item mede.
    """
    caminho = pathlib.Path(os.environ.get("JEV_BOARDS_DIR", "/opt/data/kanban/boards")) \
        / "transformativa-revenue-engine" / "kanban.db"
    if not caminho.is_file():
        return True, f"board ausente neste host ({caminho}): item nao aplicavel"
    if not card_id:
        return False, "a politica nao cita card de implementacao"
    conexao = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    try:
        linha = conexao.execute("SELECT status, assignee, title FROM tasks WHERE id = ?",
                                (card_id,)).fetchone()
    finally:
        conexao.close()
    if linha is None:
        return False, f"card {card_id!r} nao existe no board {caminho}"
    return True, f"{card_id}: status={linha[0]!r} assignee={linha[1]!r} titulo={str(linha[2])[:48]!r}"


# ---------------------------------------------------------------------------
# PARTE 1 — a bateria da v1.0 sobre a v1.1
# ---------------------------------------------------------------------------
def itens_da_bateria_da_v1_0(itens: Itens, yaml_v11: str, doc_v11: str, base) -> None:
    """Os MESMOS itens da v1.0, com a versao esperada parametrizada."""
    for nome, ok, detalhe in base.verificar(yaml_v11, doc_v11, VERSAO_V11):
        itens.add(f"v1.0-bateria: {nome}", ok, detalhe)


# ---------------------------------------------------------------------------
# PARTE 2 — o contrato novo
# ---------------------------------------------------------------------------
def itens_do_contrato(itens: Itens, dado: dict, yaml_v11: str, doc_v11: str) -> None:
    lanes = list((dado.get("lanes") or {}).keys())
    limiares = dado.get("limiares") or {}

    # ---- 2.1 chave explicita da lane conservadora --------------------------
    chave = limiares.get("lane_conservadora")
    itens.add("v1.1: `limiares.lane_conservadora` declarado (a v1.0 nao tinha a chave)",
              bool(chave), f"lane_conservadora={chave!r}")
    itens.add("v1.1: lane conservadora e uma lane declarada",
              chave in lanes, f"{chave!r} em {lanes}")

    prosa_fallback = ""
    for item in (dado.get("fallback") or {}).get("acao") or []:
        m = re.search(r"lane conservadora configurada:\s*([A-Za-z0-9_-]+)", str(item))
        if m:
            prosa_fallback = m.group(1)
    m_empate = re.search(r"\(\s*([A-Za-z0-9_-]+)\s*\)", str(limiares.get("empate", "")))
    prosa_empate = m_empate.group(1) if m_empate else ""

    itens.add("v1.1: as duas prosas antigas seguem declaradas (conferencia, nao fonte)",
              bool(prosa_fallback) and bool(prosa_empate),
              f"fallback.acao={prosa_fallback!r} empate={prosa_empate!r}")
    itens.add("v1.1: chave e `fallback.acao` CONCORDAM",
              bool(prosa_fallback) and prosa_fallback == chave,
              f"chave={chave!r} fallback.acao={prosa_fallback!r}")
    itens.add("v1.1: chave e o parentetico de `limiares.empate` CONCORDAM",
              bool(prosa_empate) and prosa_empate == chave,
              f"chave={chave!r} empate={prosa_empate!r}")
    itens.add("v1.1: os tres campos (chave, fallback.acao, empate) apontam a MESMA lane",
              chave in lanes and chave == prosa_fallback == prosa_empate,
              f"chave={chave!r} fallback={prosa_fallback!r} empate={prosa_empate!r}")

    # ---- 2.2 a v1.0 continua sendo a versao em vigor ------------------------
    itens.add("v1.1: a v1.0 continua existindo e se declarando v1.0 (nada foi sobrescrito)",
              YAML_V10.is_file() and (yaml.safe_load(YAML_V10.read_text(encoding="utf-8")) or {}).get("versao") == VERSAO_V10,
              f"versao do arquivo em vigor={(yaml.safe_load(YAML_V10.read_text(encoding='utf-8')) or {}).get('versao')!r}")
    itens.add("v1.1: o rascunho declara `substitui: jev-policy-v1.0`",
              dado.get("substitui") == VERSAO_V10, f"substitui={dado.get('substitui')!r}")
    itens.add("v1.1: o rascunho se declara rascunho (nao homologado)",
              dado.get("estado") == "rascunho-nao-homologado", f"estado={dado.get('estado')!r}")
    motivos = dado.get("motivo_da_versao") or []
    itens.add("v1.1: motivo da versao registrado (>= 3 motivos declarados)",
              isinstance(motivos, list) and len(motivos) >= 3, f"{len(motivos)} motivos")
    texto_motivos = " ".join(str(x) for x in motivos).lower()
    for tema in ("lane_conservadora", "ambiente", "metric"):
        itens.add(f"v1.1: motivo da versao cobre {tema!r}", tema in texto_motivos)

    # ---- 2.3 rito de homologacao -------------------------------------------
    hom = dado.get("homologacao") or {}
    itens.add("v1.1: homologacao declarada com estado e registro",
              str(hom.get("estado") or "") in ("pendente", "aprovada")
              and bool(hom.get("registro")), f"homologacao={hom}")
    itens.add("v1.1: o registro de aprovacoes apontado existe",
              (RAIZ / str(hom.get("registro") or "")).is_file(),
              str(hom.get("registro") or ""))
    registrada_em = hom.get("registrada_em")
    registro_txt = REGISTRO_DE_APROVACOES.read_text(encoding="utf-8") if REGISTRO_DE_APROVACOES.is_file() else ""
    # Guarda do rito: nao basta a string "v1.1" aparecer em qualquer lugar do registro — tem de
    # existir uma LINHA de aprovacao que nomeie a versao exata e quem aprovou. Na forma anterior
    # (qualquer "v1.1" no texto) a checagem virou tautologia no dia em que a propria homologacao
    # foi registrada: o registro passou a citar a politica por outros motivos e o item parou de medir.
    linha_homologacao = re.search(
        r"^\|(?=[^\n]*Anderson Ribeiro)(?=[^\n]*" + re.escape(VERSAO_V11) + r")[^\n]*$",
        registro_txt, re.M)
    registro_cita = linha_homologacao is not None
    # Ninguem marca o rascunho como homologado sem o registro do dono: e o item que
    # impede a politica de "entrar em vigor por edicao de arquivo".
    itens.add("v1.1: NAO se declara homologada sem registro do Anderson",
              registrada_em is None or registro_cita,
              f"registrada_em={registrada_em!r} registro_cita_a_versao={registro_cita}")
    itens.add("v1.1: registrada_em preenchido exige `homologacao.estado` coerente (nao pendente)",
              registrada_em is None or str(hom.get("estado") or "") != "pendente",
              f"estado={hom.get('estado')!r} registrada_em={registrada_em!r}")
    itens.add("v1.1: com a homologacao pendente, `congelada_em` e nulo",
              registrada_em is not None or dado.get("congelada_em") in (None, ""),
              f"congelada_em={dado.get('congelada_em')!r}")

    # ---- 2.4 regra de lane por ambiente ------------------------------------
    regra = dado.get("regra_de_lane_por_ambiente") or {}
    itens.add("v1.1: regra de lane por ambiente declarada", bool(regra))
    itens.add("v1.1: a regra declara a operacao que ela governa",
              bool(regra.get("operacao")), f"operacao={regra.get('operacao')!r}")
    itens.add("v1.1: a regra carrega a origem (convencao do dono, com data)",
              "2026-09-29" in str(regra.get("origem") or ""), f"origem={str(regra.get('origem'))[:70]!r}")
    itens.add("v1.1: a regra e piso (so eleva, nunca rebaixa)",
              str(regra.get("direcao") or "") == "piso_so_eleva", f"direcao={regra.get('direcao')!r}")

    ramos = {str(r.get("ambiente")): r for r in (regra.get("ambientes") or [])}
    itens.add("v1.1: a regra declara exatamente os dois ramos (novo/dev e vivo/producao)",
              set(ramos) == {n for n, _, _ in RAMOS_ESPERADOS}, f"ramos={sorted(ramos)}")
    for nome_ramo, lane_esperada, aprovacao_esperada in RAMOS_ESPERADOS:
        ramo = ramos.get(nome_ramo) or {}
        itens.add(f"v1.1: ramo {nome_ramo}: lane minima = {lane_esperada}",
                  ramo.get("lane_minima") == lane_esperada, f"lane_minima={ramo.get('lane_minima')!r}")
        itens.add(f"v1.1: ramo {nome_ramo}: lane minima e uma lane declarada",
                  ramo.get("lane_minima") in lanes, f"{ramo.get('lane_minima')!r} em {lanes}")
        itens.add(f"v1.1: ramo {nome_ramo}: aprovacao humana registrada = {aprovacao_esperada}",
                  bool(ramo.get("aprovacao_humana_registrada")) is aprovacao_esperada,
                  f"aprovacao_humana_registrada={ramo.get('aprovacao_humana_registrada')!r}")
        valores = [str(v) for v in (ramo.get("valores") or [])]
        itens.add(f"v1.1: ramo {nome_ramo}: valores de ambiente enumerados", bool(valores),
                  f"valores={valores}")
        itens.add(f"v1.1: ramo {nome_ramo}: vocabulario sem acento e em minuscula",
                  all(v == v.strip().lower() and v.isascii() for v in valores), f"valores={valores}")
    dev = ramos.get("novo_ou_dev") or {}
    vivo = ramos.get("vivo_ou_producao") or {}
    itens.add("v1.1: os dois ramos nao compartilham valor de ambiente",
              not (set(dev.get("valores") or []) & set(vivo.get("valores") or [])),
              f"dev={dev.get('valores')} vivo={vivo.get('valores')}")
    itens.add("v1.1: o ramo vivo cobre a producao e o dev nao",
              "producao" in [str(v) for v in (vivo.get("valores") or [])]
              and "producao" not in [str(v) for v in (dev.get("valores") or [])])
    itens.add("v1.1: ambiente nao declarado cai no ramo MAIS conservador",
              str(regra.get("ambiente_nao_declarado") or "").startswith("cai no ramo mais conservador")
              and "vivo_ou_producao" in str(regra.get("ambiente_nao_declarado") or ""),
              f"ambiente_nao_declarado={str(regra.get('ambiente_nao_declarado'))[:90]!r}")

    execucao = regra.get("execucao") or {}
    itens.add("v1.1: a execucao da regra e declarada (contrato != enforcement)",
              "roteador_que_a_executa" in execucao,
              f"execucao={execucao}")
    # Decisao que nao viaja para o card seguinte se perde na primeira pressao: a v1.1 tem de
    # apontar NOMINALMENTE para o card que a executa, e o card tem de existir no board.
    card_do_piso = str(execucao.get("card_de_implementacao") or "").strip()
    itens.add("v1.1: a regra aponta o card que a implementa (id do board)",
              bool(re.fullmatch(r"t_[0-9a-f]{8}", card_do_piso)), f"card={card_do_piso!r}")
    itens.add("v1.1: o id do card do piso aparece tambem na prosa de `entra_em_vigor_com`",
              bool(card_do_piso) and card_do_piso in str(execucao.get("entra_em_vigor_com") or ""),
              f"entra_em_vigor_com={str(execucao.get('entra_em_vigor_com'))[:80]!r}")
    itens.checar("v1.1: o card citado EXISTE no board (a decisao viaja para quem executa)",
                 lambda: _card_do_board(card_do_piso))

    # ---- 2.5 metricas instrumentadas ---------------------------------------
    met = dado.get("metricas") or {}
    acompanhar = [str(x) for x in (met.get("acompanhar") or [])]
    for nome in ("custo_por_lane", "latencia_por_lane", "falso_rebaixamento"):
        itens.add(f"v1.1: `{nome}` consta em metricas.acompanhar", nome in acompanhar,
                  f"acompanhar={acompanhar}")
    instrumentacao = met.get("instrumentacao") or {}
    instrumento = str(instrumentacao.get("instrumento") or "")
    itens.add("v1.1: o instrumento declarado existe no repo",
              bool(instrumento) and (RAIZ / instrumento).is_file(), f"instrumento={instrumento!r}")
    for campo in ("unidade_de_custo", "fonte_do_custo", "fonte_da_latencia", "fonte_das_decisoes"):
        itens.add(f"v1.1: instrumentacao declara `{campo}`", bool(instrumentacao.get(campo)))
    medivel = [str(x) for x in (instrumentacao.get("medivel") or [])]
    nao_medivel = instrumentacao.get("nao_medivel") or {}
    itens.add("v1.1: o que e medivel e subconjunto do que se acompanha",
              bool(medivel) and set(medivel) <= set(acompanhar),
              f"medivel={medivel} acompanhar={acompanhar}")
    itens.add("v1.1: custo_por_lane e latencia_por_lane declaradas mediveis",
              {"custo_por_lane", "latencia_por_lane"} <= set(medivel), f"medivel={medivel}")
    for nome in ("custo_por_card_verified", "regressao_ou_retrabalho"):
        itens.add(f"v1.1: `{nome}` declarada NAO medivel COM motivo (nunca silenciada)",
                  bool(str(nao_medivel.get(nome) or "").strip()),
                  f"motivo={str(nao_medivel.get(nome))[:70]!r}")
    limites = " ".join(str(x) for x in (instrumentacao.get("limites") or []))
    itens.add("v1.1: o limite da cegueira de custo esta declarado (high e critical tem a mesma classe)",
              MARCADOR_DO_LIMITE_DE_CUSTO in limites and MARCADOR_DA_CEGUEIRA in limites
              and "falso_rebaixamento" in limites,
              f"limites={str(instrumentacao.get('limites'))[:120]!r}")
    itens.add("v1.1: o limite 'custo nao e dinheiro' esta declarado",
              "dinheiro" in limites)
    itens.add("v1.1: a latencia medida e declarada como da camada de decisao",
              "camada de decis" in limites or "camada de decis" in str(instrumentacao.get("fonte_da_latencia")))
    itens.add("v1.1: lane sem decisao reporta null, nunca zero",
              "null" in limites and "zero" in limites)

    # ---- 2.6 recibo: o contrato nao cresceu por conveniencia ---------------
    recibo = dado.get("recibo") or {}
    itens.add("v1.1: o recibo segue com os 13 campos (nenhum campo novo entrou)",
              len(recibo.get("campos") or []) == 13, f"{len(recibo.get('campos') or [])} campos")
    fora = recibo.get("campos_fora_do_contrato") or []
    itens.add("v1.1: latencia e custo declarados FORA do recibo (e por que)",
              len(fora) >= 2 and any("latencia" in str(x) for x in fora)
              and any("custo" in str(x) for x in fora), f"{len(fora)} itens")

    # ---- 2.7 o documento diz a mesma coisa --------------------------------
    doc_low = doc_v11.lower()
    itens.add("v1.1-doc: o documento cita a chave explicita",
              "lane_conservadora" in doc_v11)
    itens.add("v1.1-doc: o documento declara a regra por ambiente com os dois ramos",
              "regra_de_lane_por_ambiente" in doc_v11 and "novo/dev" in doc_low
              and "vivo" in doc_low)
    for lane in ("high", "critical"):
        itens.add(f"v1.1-doc: o documento diz a lane {lane} na tabela de ambientes", lane in doc_v11)
    itens.add("v1.1-doc: o documento cita a convencao do Anderson de 29/09/2026",
              "29/09/2026" in doc_v11 or "2026-09-29" in doc_v11)
    itens.add("v1.1-doc: o documento diz que a regra ainda NAO esta em execucao",
              "não executada" in doc_v11 or "nao executada" in doc_v11)
    itens.add("v1.1-doc: o documento nomeia o instrumento das metricas",
              "medir_metricas_por_lane.py" in doc_v11)
    itens.add("v1.1-doc: o documento avisa que o rascunho nao esta em vigor",
              "não homologado" in doc_v11 or "nao homologado" in doc_v11
              or "RASCUNHO" in doc_v11)
    itens.add("v1.1-doc: o documento manda o rito da v1.0 para a homologacao",
              "registro-de-aprovacoes" in doc_v11 and "homologa" in doc_low)


# ---------------------------------------------------------------------------
# PARTE 3 — provas de comportamento contra o roteador em vigor
# ---------------------------------------------------------------------------
def _politica_temporaria(raiz: pathlib.Path, mutador) -> pathlib.Path:
    """Copia a politica EM VIGOR (v1.0) para um diretorio temporario e aplica a mutacao.

    A copia mantem `versao: jev-policy-v1.0` de proposito: o que se prova aqui e a
    SEMANTICA da chave explicita no schema que o roteador conhece hoje. O arquivo
    versionado nunca e tocado.

    O dump do YAML perde os comentarios, e a v1.0 declara o vocabulario de resultado
    (`PASS | RETRY | ESCALATE | BLOCK`) justamente num comentario — vocabulario que o
    roteador EXIGE encontrar no texto da politica. A copia recebe o mesmo vocabulario
    como cabecalho: sem isso o mutante nem carrega e a prova mediria a coisa errada.
    """
    dado = yaml.safe_load(YAML_V10.read_text(encoding="utf-8"))
    destino = raiz / "policy_copia.yaml"
    destino.write_text(
        "# copia de prova (mutacao em diretorio temporario)\n"
        "# vocabulario de resultado: PASS | RETRY | ESCALATE | BLOCK\n"
        + texto_de_yaml(mutador(dado)), encoding="utf-8")
    return destino


def _recusa(erro) -> str:
    """Motivos da recusa, em texto — para conferir que ela veio pelo motivo CERTO."""
    return "; ".join(getattr(erro, "motivos", []) or [str(erro)])


def _prosa_sem_lane(dado: dict) -> dict:
    """Remove as duas fontes em prosa da lane conservadora, preservando o resto."""
    copia = copy.deepcopy(dado)
    copia["fallback"]["acao"] = [x for x in copia["fallback"]["acao"]
                                 if "lane conservadora configurada" not in str(x)]
    copia["limiares"]["empate"] = "lane mais conservadora declarada em limiares.lane_conservadora"
    return copia


def itens_de_comportamento(itens: Itens, dado: dict, modulo) -> None:
    """Prova no ROTEADOR o que o YAML sozinho nao prova.

    Cada prova roda sobre uma COPIA temporaria da politica EM VIGOR (v1.0, com a versao
    preservada) ou sobre a propria v1.1 no disco. O arquivo versionado nunca e tocado, e
    a pasta temporaria e descartada no fim.
    """
    temporario = pathlib.Path(tempfile.mkdtemp(prefix="jev-v11-"))

    # ---- B1: a chave explicita SOZINHA basta --------------------------
    def com_chave_e_sem_prosa(d):
        copia = _prosa_sem_lane(d)
        copia["limiares"]["lane_conservadora"] = "high"
        return copia

    copia_com_chave = _politica_temporaria(temporario, com_chave_e_sem_prosa)
    try:
        politica = modulo.carregar_politica(copia_com_chave)
        itens.add("B1a comportamento: com a chave e SEM prosa, o roteador resolve a lane conservadora",
                  politica.get("_lane_conservadora") == "high",
                  f"lane_conservadora={politica.get('_lane_conservadora')!r}")
    except Exception as erro:  # noqa: BLE001
        itens.add("B1a comportamento: com a chave e SEM prosa, o roteador resolve a lane conservadora",
                  False, f"recusou: {type(erro).__name__}: {erro}")

    # B1b: o mesmo arquivo SEM a chave e SEM prosa => nao ha de onde ler a lane (falha fechada).
    #      E o que prova que a v1.0 de hoje depende de prosa.
    copia_sem_nada = _politica_temporaria(temporario, _prosa_sem_lane)
    try:
        politica = modulo.carregar_politica(copia_sem_nada)
        itens.add("B1b comportamento: SEM chave e SEM prosa nao ha lane conservadora (recusa)",
                  False, f"aceitou e devolveu {politica.get('_lane_conservadora')!r}")
    except modulo.PoliticaInvalida as erro:
        motivos = _recusa(erro)
        itens.add("B1b comportamento: SEM chave e SEM prosa nao ha lane conservadora (recusa)",
                  "nao foi possivel resolver a lane conservadora" in motivos
                  and "nao declara o resultado" not in motivos,
                  f"{motivos[:130]}")

    # ---- B2: chave discordando da prosa = recusa -------------------------
    def com_divergencia(d):
        copia = copy.deepcopy(d)
        copia["limiares"]["lane_conservadora"] = "medium"
        return copia

    try:
        politica = modulo.carregar_politica(_politica_temporaria(temporario, com_divergencia))
        itens.add("B2 comportamento: chave discordando da prosa e RECUSADA (fail-closed)",
                  False, f"aceitou e devolveu {politica.get('_lane_conservadora')!r}")
    except modulo.PoliticaInvalida as erro:
        motivos = _recusa(erro)
        itens.add("B2 comportamento: chave discordando da prosa e RECUSADA (fail-closed)",
                  "divergentes" in motivos and "nao declara o resultado" not in motivos,
                  f"{motivos[:130]}")

    # ---- B3: a v1.1 e recusada pelo roteador em vigor --------------------
    itens.add("B3 comportamento: a v1.1 e RECUSADA pelo roteador em vigor (rascunho inerte)",
              VERSAO_V11 not in modulo.VERSOES_DE_POLITICA_SUPORTADAS,
              f"versao do roteador={modulo.ROUTER_VERSION} suportadas={sorted(modulo.VERSOES_DE_POLITICA_SUPORTADAS)}")
    # Sensibilidade do item acima, medida: com o portao de versao ABERTO em memoria, a v1.1
    # CARREGA e resolve a lane conservadora. Ou seja, o que impede a v1.1 de rodar hoje e
    # exatamente o portao de versao — e nao um defeito de schema. Consequencia que o teste
    # deixa explicita: abrir o portao sem implementar o piso da regra por ambiente faria o
    # roteador IGNORAR em silencio uma regra declarada (pior que recusar). A troca e em
    # memoria: o modulo e recarregado do arquivo, e o arquivo versionado nao e tocado.
    suportadas_originais = modulo.VERSOES_DE_POLITICA_SUPORTADAS
    try:
        modulo.VERSOES_DE_POLITICA_SUPORTADAS = frozenset(set(suportadas_originais) | {VERSAO_V11})
        politica_v11 = modulo.carregar_politica(YAML_V11)
        itens.add("B3-sensibilidade: com o portao de versao aberto, a v1.1 CARREGA (o portao e "
                  "o unico impedimento) e resolve a mesma lane conservadora",
                  politica_v11.get("_lane_conservadora") == dado["limiares"].get("lane_conservadora"),
                  f"lane_conservadora={politica_v11.get('_lane_conservadora')!r} "
                  f"chave no YAML={dado['limiares'].get('lane_conservadora')!r}")
    except Exception as erro:  # noqa: BLE001
        itens.add("B3-sensibilidade: com o portao de versao aberto, a v1.1 CARREGA (o portao e "
                  "o unico impedimento) e resolve a mesma lane conservadora",
                  False, f"nao carregou nem com o portao aberto: {type(erro).__name__}: {erro}")
    finally:
        modulo.VERSOES_DE_POLITICA_SUPORTADAS = suportadas_originais
    try:
        modulo.carregar_politica(YAML_V11)
        itens.add("B3b comportamento: carregar a v1.1 de verdade levanta PoliticaInvalida",
                  False, "carregou a v1.1 — o rascunho deixou de ser inerte")
    except modulo.PoliticaInvalida as erro:
        itens.add("B3b comportamento: carregar a v1.1 de verdade levanta PoliticaInvalida",
                  "versao de politica desconhecida" in str(erro), f"{str(erro)[:110]}")

    # ---- B4: vocabulario de ambiente bate com o guardrail de DDL ---------
    regra = dado.get("regra_de_lane_por_ambiente") or {}
    ramos = {str(r.get("ambiente")): r for r in (regra.get("ambientes") or [])}
    politica_em_vigor = modulo.carregar_politica(YAML_V10)

    def guardrail_de_ddl_aciona(ambiente) -> bool:
        tarefa = {"card_id": "t_v11_ambiente", "titulo": "aplicar ddl de esquema",
                  "descricao": "aplicar ddl de esquema", "acao": "aplicar ddl de esquema",
                  "acao_codigo": "migracao_de_esquema", "ambiente_alvo": ambiente,
                  "lane_proposta": "high", "confianca": 0.95}
        decisao = modulo.decidir(tarefa, politica=politica_em_vigor)["decisao"]
        return "ddl_fora_de_producao" in (decisao.get("guardrails_acionados") or [])

    for valor in (ramos.get("novo_ou_dev") or {}).get("valores") or []:
        itens.add(f"B4 comportamento: ambiente declarado {valor!r} e lado DEV no roteador "
                  "(guardrail de DDL NAO aciona)",
                  guardrail_de_ddl_aciona(valor) is False,
                  f"guardrail acionou={guardrail_de_ddl_aciona(valor)}")
    for valor in (ramos.get("vivo_ou_producao") or {}).get("valores") or []:
        itens.add(f"B4 comportamento: ambiente declarado {valor!r} e lado VIVO no roteador "
                  "(guardrail de DDL aciona)",
                  guardrail_de_ddl_aciona(valor) is True,
                  f"guardrail acionou={guardrail_de_ddl_aciona(valor)}")
    itens.add("B4 comportamento: ambiente NAO declarado e lado vivo (guardrail de DDL aciona)",
              guardrail_de_ddl_aciona("") is True and guardrail_de_ddl_aciona(None) is True,
              f"vazio={guardrail_de_ddl_aciona('')} nulo={guardrail_de_ddl_aciona(None)}")

    # ---- B5: o piso NAO esta ativo --------------------------------------
    execucao = regra.get("execucao") or {}
    roteador_declarado = execucao.get("roteador_que_a_executa")
    itens.add("B5: a execucao declarada e coerente com o roteador real",
              roteador_declarado in (None, modulo.ROUTER_VERSION),
              f"declarado={roteador_declarado!r} roteador real={modulo.ROUTER_VERSION!r}")
    tarefa_viva = {"card_id": "t_v11_piso", "titulo": "aplicar migration em tabela viva",
                   "descricao": "aplicar migration em tabela viva",
                   "acao": "aplicar migration em tabela viva", "acao_codigo": "migracao_de_esquema",
                   "ambiente_alvo": "vivo", "lane_proposta": "medium", "confianca": 0.95}
    decisao = modulo.decidir(tarefa_viva, politica=politica_em_vigor)["decisao"]
    if roteador_declarado is None:
        itens.add("B5 comportamento: com `roteador_que_a_executa: null` o piso NAO eleva "
                  "DDL em ambiente vivo a critical",
                  decisao["lane"] != "critical",
                  f"lane registrada={decisao['lane']!r} (a regra declarada manda critical)")
    else:
        itens.add("B5 comportamento: com a regra declarada em execucao, DDL em ambiente vivo "
                  "chega a critical",
                  decisao["lane"] == "critical",
                  f"lane registrada={decisao['lane']!r}")


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
def verificar(yaml_v11: str, doc_v11: str, base, modulo) -> Itens:
    itens = Itens()
    try:
        dado = yaml.safe_load(yaml_v11)
    except Exception as erro:  # noqa: BLE001
        itens.add("v1.1 YAML valido", False, f"nao parseia: {erro}")
        return itens
    itens.add("v1.1 YAML valido", isinstance(dado, dict),
              f"{len(dado)} chaves de topo" if isinstance(dado, dict) else "raiz nao e mapa")
    if not isinstance(dado, dict):
        return itens
    itens_do_contrato(itens, dado, yaml_v11, doc_v11)
    itens_da_bateria_da_v1_0(itens, yaml_v11, doc_v11, base)
    itens_de_comportamento(itens, dado, modulo)
    return itens


def imprimir(itens: Itens) -> int:
    for nome, ok, detalhe in itens.lista:
        marca = "OK   " if ok else "FALHOU"
        print(f"{marca} {nome}" + (f"  [{detalhe}]" if detalhe else ""))
    return itens.falhas()


def autoteste(base, modulo, yaml_v11: str, doc_v11: str) -> bool:
    """Cada mutacao TEM de ser detectada — em copia temporaria, nunca no arquivo versionado."""
    print("\n=== AUTOTESTE: mutacoes que o verificador precisa reprovar ===")
    base_dado = yaml.safe_load(yaml_v11)
    mutacoes = []

    def mut(nome, funcao_dado=None, doc=None):
        d = base_dado if funcao_dado is None else funcao_dado(copy.deepcopy(base_dado))
        mutacoes.append((nome, texto_de_yaml(d) if funcao_dado is not None else yaml_v11,
                         doc if doc is not None else doc_v11))

    # chave explicita
    mut("chave `lane_conservadora` removida",
        lambda d: (d["limiares"].pop("lane_conservadora"), d)[1])
    mut("chave `lane_conservadora` divergindo do `fallback.acao`",
        lambda d: (d["limiares"].__setitem__("lane_conservadora", "medium"), d)[1])
    mut("chave `lane_conservadora` divergindo do parentetico de `limiares.empate`",
        lambda d: (d["limiares"].__setitem__("lane_conservadora", "small"), d)[1])
    mut("chave `lane_conservadora` apontando lane inexistente",
        lambda d: (d["limiares"].__setitem__("lane_conservadora", "ultrafino"), d)[1])
    mut("`fallback.acao` apontando lane barata (small) contra a chave",
        lambda d: (d["fallback"].__setitem__("acao",
                   ["aplicar guardrails deterministicos primeiro",
                    "seguir pela lane conservadora configurada: small"]), d)[1])

    # ambiente
    mut("regra de lane por ambiente removida",
        lambda d: (d.pop("regra_de_lane_por_ambiente"), d)[1])
    mut("ramo dev apontando lane minima `critical`",
        lambda d: (d["regra_de_lane_por_ambiente"]["ambientes"][0].__setitem__("lane_minima", "critical"), d)[1])
    mut("ramo vivo sem aprovacao humana registrada",
        lambda d: (d["regra_de_lane_por_ambiente"]["ambientes"][1].__setitem__(
            "aprovacao_humana_registrada", False), d)[1])
    mut("ramo vivo apontando lane minima `high`",
        lambda d: (d["regra_de_lane_por_ambiente"]["ambientes"][1].__setitem__("lane_minima", "high"), d)[1])
    mut("ramo dev com valor de ambiente da producao",
        lambda d: (d["regra_de_lane_por_ambiente"]["ambientes"][0]["valores"].append("producao"), d)[1])
    mut("ambiente nao declarado caindo no ramo DEV",
        lambda d: (d["regra_de_lane_por_ambiente"].__setitem__(
            "ambiente_nao_declarado", "cai no ramo mais conservador (novo_ou_dev)"), d)[1])
    mut("regra deixa de ser piso e passa a poder rebaixar",
        lambda d: (d["regra_de_lane_por_ambiente"].__setitem__("direcao", "teto_so_rebaixa"), d)[1])
    mut("a regra se declara executada por um roteador que nao existe",
        lambda d: (d["regra_de_lane_por_ambiente"]["execucao"].__setitem__(
            "roteador_que_a_executa", "jev-router-v9.9"), d)[1])
    mut("card de implementacao removido da regra",
        lambda d: (d["regra_de_lane_por_ambiente"]["execucao"].pop("card_de_implementacao"), d)[1])
    mut("card de implementacao com id malformado (nao e id do board)",
        lambda d: (d["regra_de_lane_por_ambiente"]["execucao"].__setitem__(
            "card_de_implementacao", "TRE-W0-E04-T08"), d)[1])
    mut("card de implementacao fora da prosa de `entra_em_vigor_com`",
        lambda d: (d["regra_de_lane_por_ambiente"]["execucao"].__setitem__(
            "entra_em_vigor_com", "homologacao do Anderson + implementacao do piso"), d)[1])
    # A mutacao do card INEXISTENTE so vale onde o board existe: a prova de que "o card
    # citado existe" nao pode depender de o host ter o board (o autoteste precisa ser
    # honesto nos dois hosts, em vez de verde por acidente).
    if _card_do_board("t_d36c7d0f")[1].startswith("t_d36c7d0f"):
        mut("card de implementacao apontando card que nao existe no board",
            lambda d: (d["regra_de_lane_por_ambiente"]["execucao"].__setitem__(
                "card_de_implementacao", "t_deadbeef"), d)[1])

    # homologacao
    # "Declarar-se homologado sem registro" so tem objeto enquanto NAO houver registro do dono. Com a
    # homologacao ja registrada, manter esta mutacao daria verde por acidente — mesma regra da mutacao
    # do card inexistente: o autoteste tem de ser honesto nos dois estados, nao verde por sorte.
    _reg_real = (REGISTRO_DE_APROVACOES.read_text(encoding="utf-8")
                 if REGISTRO_DE_APROVACOES.is_file() else "")
    _tem_registro = re.search(
        r"^\|(?=[^\n]*Anderson Ribeiro)(?=[^\n]*" + re.escape(VERSAO_V11) + r")[^\n]*$",
        _reg_real, re.M)
    if not _tem_registro:
        mut("rascunho se declara homologado sem registro",
            lambda d: (d["homologacao"].__setitem__("registrada_em", "2026-09-30"), d)[1])
    mut("registrada_em preenchido com homologacao ainda pendente",
        lambda d: (d["homologacao"].__setitem__("registrada_em", "2026-09-30"),
                   d["homologacao"].__setitem__("estado", "pendente"), d)[1])
    mut("estado trocado para homologado sem registro",
        lambda d: (d["estado"].__str__() and d.__setitem__("estado", "homologado"), d)[1])
    mut("rascunho deixa de declarar o que substitui",
        lambda d: (d.pop("substitui"), d)[1])
    mut("motivo da versao esvaziado",
        lambda d: (d.__setitem__("motivo_da_versao", []), d)[1])

    # metricas
    mut("`custo_por_lane` fora de metricas.acompanhar",
        lambda d: (d["metricas"]["acompanhar"].remove("custo_por_lane"), d)[1])
    mut("`latencia_por_lane` fora de metricas.acompanhar",
        lambda d: (d["metricas"]["acompanhar"].remove("latencia_por_lane"), d)[1])
    mut("instrumentacao apontando script inexistente",
        lambda d: (d["metricas"]["instrumentacao"].__setitem__(
            "instrumento", "scripts/instrumento_que_nao_existe.py"), d)[1])
    mut("`custo_por_card_verified` deixa de declarar por que nao e medivel",
        lambda d: (d["metricas"]["instrumentacao"]["nao_medivel"].pop("custo_por_card_verified"), d)[1])
    mut("limite da cegueira de custo (high x critical) removido",
        lambda d: (d["metricas"]["instrumentacao"].__setitem__("limites", ["custo e unidade relativa"]), d)[1])
    mut("recibo deixa de declarar o que fica fora do contrato",
        lambda d: (d["recibo"].pop("campos_fora_do_contrato"), d)[1])
    mut("recibo ganha um 14o campo",
        lambda d: (d["recibo"]["campos"].append("latencia"), d)[1])

    # documento
    mut("documento deixa de declarar a regra por ambiente", None,
        doc_v11.replace("regra_de_lane_por_ambiente", "a definir"))
    mut("documento deixa de citar a chave explicita", None,
        doc_v11.replace("lane_conservadora", "a lane conservadora de sempre"))
    mut("documento deixa de citar a convencao de 29/09/2026", None,
        doc_v11.replace("29/09/2026", "em data a registrar"))
    mut("documento deixa de dizer que a regra nao esta em execucao", None,
        doc_v11.replace("não executada", "já executada").replace("nao executada", "ja executada"))
    mut("documento deixa de citar o instrumento", None,
        doc_v11.replace("medir_metricas_por_lane.py", "um script qualquer"))

    detectadas = 0
    for nome, y, dd in mutacoes:
        itens = verificar(y, dd, base, modulo)
        falhas = itens.falhas()
        if falhas > 0:
            detectadas += 1
            print(f"OK    detectada: {nome}  ({falhas} item(ns) reprovado(s))")
        else:
            print(f"FALHOU NAO detectada: {nome}  <-- buraco no verificador")
    print(f"\nautoteste: {detectadas}/{len(mutacoes)} mutacoes detectadas")
    return detectadas == len(mutacoes)


def main() -> int:
    faltando = [p for p in (YAML_V11, DOC_V11, YAML_V10, DOC_V10, ROTEADOR) if not p.is_file()]
    if faltando:
        print("FALHOU arquivo ausente: " + ", ".join(str(p) for p in faltando))
        return 1

    base = carregar_modulo(RAIZ / "scripts/verificar_jev_policy.py", "verificador_v10")
    modulo = carregar_modulo(ROTEADOR, "roteador_da_suite_v11")

    print("=" * 72)
    print("VERIFICADOR DA JEV DECISION POLICY v1.1 (rascunho, nao homologado)")
    print(f"  politica:  {YAML_V11.relative_to(RAIZ)}")
    print(f"  documento: {DOC_V11.relative_to(RAIZ)}")
    print(f"  roteador em vigor: {modulo.ROUTER_VERSION} "
          f"(suporta {sorted(modulo.VERSOES_DE_POLITICA_SUPORTADAS)})")
    print("=" * 72)

    yaml_v11 = YAML_V11.read_text(encoding="utf-8")
    doc_v11 = DOC_V11.read_text(encoding="utf-8")
    itens = verificar(yaml_v11, doc_v11, base, modulo)
    falhas = imprimir(itens)

    teste_ok = True
    if "--autoteste" in sys.argv:
        teste_ok = autoteste(base, modulo, yaml_v11, doc_v11)

    print()
    if falhas == 0 and teste_ok:
        print(f"RESULTADO: PASS ({len(itens.lista)} itens, 0 falhas) + autoteste OK")
        return 0
    print(f"RESULTADO: FALHOU ({len(itens.lista)} itens, {falhas} falha(s))"
          + ("" if teste_ok else " + autoteste com buraco"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
