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
    * `versao_em_vigor` declara UMA versao em vigor, o caminho que o roteador carrega por
      padrao (conferido contra o codigo, nao por leitura) e onde a v1.0 fica PRESERVADA
      PARA AUDITORIA — a versao em vigor e uma so, e a antiga continua executavel para
      reconstruir decisao antiga;
    * as metricas declaradas em `metricas.acompanhar` tem instrumentacao: o que e medivel,
      o que NAO e medivel e COM QUE MOTIVO, e os limites que nao podem ser esquecidos
      (o custo nao enxerga rebaixamento `critical -> high` porque as duas lanes tem a mesma
      classe de custo);
    * a versao se declara EM VIGOR e homologada — e ninguem pode marca-la homologada sem o
      registro do Anderson em `docs/operations/registro-de-aprovacoes.md`.

  PARTE 3 — as provas de COMPORTAMENTO contra o roteador (`hermes/jev/routing/router.py`).
    Prosa em YAML nao prova nada; estas provas so valem se exercitarem o codigo:
    B1. com a chave explicita presente e as DUAS prosas REMOVIDAS, o roteador ainda resolve a
        lane conservadora — e o que torna a chave "legivel por maquina" de fato (a v1.0 nao
        tem a chave: sem prosa, nao ha lane; e o que esta provado em B1b).
    B2. chave discordando da prosa = politica inconsistente = recusa (fail-closed).
    B3. a v1.1 e ACEITA pelo roteador em vigor (o portao abriu) e a politica declara a versao
        em vigor que o roteador carrega POR PADRAO — casamento com `CAMINHO_POLITICA_PADRAO`,
        medido no codigo. Operacao de piso que o roteador nao implementa e RECUSADA: o portao
        so foi aberto porque o piso existe.
    B4. o vocabulario de ambiente declarado na regra bate, VALOR A VALOR, com o guardrail de DDL
        do roteador (valores do ramo dev nao acionam; valores do ramo vivo acionam; ambiente
        nao declarado aciona).
    B5. com a regra declarada EM EXECUCAO, um card com DDL em ambiente vivo, proposta `medium`
        e confianca alta chega a `critical` COM aprovacao humana exigida — e o roteador e o
        `roteador_que_a_executa` declarado no YAML.
    B6. o piso entra no recibo SEM campo novo: os 13 campos do contrato continuam 13, e o piso
        e o motivo aparecem em `override.piso_por_ambiente` (onde a propria politica manda
        registrar) e no rastro da decisao.
    B7. o piso SO ELEVA: proposta `critical` em ambiente dev continua `critical` (nao desce
        para `high`), proposta `medium` em dev sobe para `high`, tarefa fora da operacao
        governada nao e tocada, e a politica v1.0 — preservada para auditoria, sem a regra —
        nao muda de comportamento.

Uso:
    python3 scripts/verificar_jev_policy_v1_1.py                # verifica
    python3 scripts/verificar_jev_policy_v1_1.py --autoteste     # verifica + mutacoes

Saida: OK/FALHOU por item + RESULTADO final. Codigo de saida 0 so quando tudo passa
(inclusive o autoteste, quando pedido).
"""
from __future__ import annotations

import copy
import importlib.util
import json
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
# ANCORA:ROTEADOR_DA_VERSAO_HISTORICA (30/09/2026) — o roteador que POS a v1.1 na rua.
# A partir do jev-router-v1.2 o codigo passou a declarar suporte a v1.2 (aposentadoria do
# classificador de card, card TRE-W0-E04-T09), e a igualdade "roteador declarado ==
# ROUTER_VERSION" virou fato datado: o que se garante agora e que a v1.1 continue
# EXECUTAVEL pelo roteador no ar.
ROTEADOR_DA_V11 = "jev-router-v1.1"

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


def _normalizar_simples(texto: str) -> str:
    """minusculo e sem acento — para comparar NOME escrito a mao no YAML e no registro."""
    tabela = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                           "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")
    return str(texto).translate(tabela).lower()


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
def itens_do_contrato(itens: Itens, dado: dict, yaml_v11: str, doc_v11: str, modulo) -> None:
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

    # ---- 2.2 a versao EM VIGOR e uma so; a antiga fica preservada para auditoria ----
    itens.add("v1.1: a v1.0 continua existindo e se declarando v1.0 (nada foi sobrescrito)",
              YAML_V10.is_file() and (yaml.safe_load(YAML_V10.read_text(encoding="utf-8")) or {}).get("versao") == VERSAO_V10,
              f"versao do arquivo preservado={(yaml.safe_load(YAML_V10.read_text(encoding='utf-8')) or {}).get('versao')!r}")
    itens.add("v1.1: a versao declara `substitui: jev-policy-v1.0`",
              dado.get("substitui") == VERSAO_V10, f"substitui={dado.get('substitui')!r}")
    itens.add("v1.1: a versao se declara EM VIGOR (nao e mais rascunho)",
              dado.get("estado") == "em-vigor", f"estado={dado.get('estado')!r}")
    itens.add("v1.1: `congelada_em` preenchido (versao vigente tem data de vigencia)",
              bool(str(dado.get("congelada_em") or "").strip()), f"congelada_em={dado.get('congelada_em')!r}")
    motivos = dado.get("motivo_da_versao") or []
    itens.add("v1.1: motivo da versao registrado (>= 3 motivos declarados)",
              isinstance(motivos, list) and len(motivos) >= 3, f"{len(motivos)} motivos")
    texto_motivos = " ".join(str(x) for x in motivos).lower()
    for tema in ("lane_conservadora", "ambiente", "metric"):
        itens.add(f"v1.1: motivo da versao cobre {tema!r}", tema in texto_motivos)

    # Declaracao de vigencia: a versao em vigor e UMA, e o caminho declarado tem de ser o
    # caminho que o roteador carrega POR PADRAO — conferido contra o codigo, nao por leitura.
    # Sem isto a politica poderia se declarar em vigor enquanto o roteador carrega a antiga
    # (dizer "em vigor" e nao estar), que e a mesma classe de defeito do card TRE-W0-E04-T08.
    vig = dado.get("versao_em_vigor") or {}
    itens.add("v1.1: `versao_em_vigor` declara a versao em vigor",
              str(vig.get("versao") or "") == VERSAO_V11, f"versao={vig.get('versao')!r}")
    itens.add("v1.1: `versao_em_vigor` declara desde quando esta em vigor",
              bool(str(vig.get("desde") or "").strip()), f"desde={vig.get('desde')!r}")
    # ANCORA:VERSAO_SUCESSORA (30/09/2026) — a igualdade "caminho declarado == padrao do
    # roteador" valia enquanto a v1.1 era a versao em vigor. Com a v1.2 em vigor (card
    # TRE-W0-E04-T09) ela virou fato datado. A garantia preservada e a mesma de antes —
    # nem "em vigor" sem estar, nem sucessao silenciosa: ou a v1.1 e a padrao, ou a padrao
    # e uma versao que DECLARA `substitui: jev-policy-v1.1` (e entao a v1.1 fica preservada
    # para auditoria, ainda executavel porque o recibo grava `policy_version`).
    def _vigencia_ou_sucessora():
        caminho = str(vig.get("caminho") or "")
        if not caminho or not (RAIZ / caminho).is_file():
            return False, f"caminho declarado inexistente: {caminho!r}"
        # O caminho declarado tem de ser o arquivo DESTA versao: apontar a v1.1 para o
        # arquivo da v1.0 seria dizer "em vigor" apontando para a versao errada.
        if (RAIZ / caminho).resolve() != YAML_V11.resolve():
            return False, f"caminho declarado nao e o arquivo da propria v1.1: {caminho!r}"
        padrao = pathlib.Path(modulo.CAMINHO_POLITICA_PADRAO)
        if padrao.resolve() == (RAIZ / caminho).resolve():
            return True, f"a v1.1 e a versao padrao do roteador ({padrao.name})"
        sucessora = yaml.safe_load(padrao.read_text(encoding="utf-8")) or {}
        return (sucessora.get("substitui") == VERSAO_V11), (
            f"padrao do roteador={padrao.name} declara substitui={sucessora.get('substitui')!r} "
            f"(v1.1 preservada para auditoria)")

    itens.checar("v1.1: o caminho declarado em `versao_em_vigor` e o padrao do roteador — ou uma versao "
                 "SUCESSORA que declara substitui-la (medido no codigo)", _vigencia_ou_sucessora)
    # ANCORA:ROTEADOR_DA_VERSAO_HISTORICA — ver a nota da constante `ROTEADOR_DA_V11`.
    # O que importa nao e o numero do roteador de hoje, e sim (a) o registro historico de
    # quem pos a v1.1 na rua e (b) que o roteador ATUAL continue executando a v1.1.
    itens.add("v1.1: `versao_em_vigor.roteador` nomeia quem pos a v1.1 na rua, e o roteador atual ainda a executa",
              str(vig.get("roteador") or "") == ROTEADOR_DA_V11
              and VERSAO_V11 in set(modulo.VERSOES_DE_POLITICA_SUPORTADAS),
              f"declarado={vig.get('roteador')!r} roteador atual={modulo.ROUTER_VERSION!r} "
              f"suportadas={sorted(modulo.VERSOES_DE_POLITICA_SUPORTADAS)}")
    preservada = vig.get("preservada_para_auditoria") or {}
    itens.add("v1.1: `versao_em_vigor` diz qual versao fica preservada para auditoria",
              str(preservada.get("versao") or "") == VERSAO_V10 and bool(preservada.get("caminho")),
              f"preservada={preservada}")
    itens.add("v1.1: a versao preservada aponta o ARQUIVO v1.0 de verdade",
              (RAIZ / str(preservada.get("caminho") or "")).resolve() == YAML_V10.resolve(),
              f"caminho={preservada.get('caminho')!r}")
    itens.add("v1.1: a versao preservada NAO e a que o roteador carrega por padrao",
              (RAIZ / str(preservada.get("caminho") or "")).resolve() != pathlib.Path(modulo.CAMINHO_POLITICA_PADRAO).resolve(),
              f"preservada={preservada.get('caminho')!r} padrao={modulo.CAMINHO_POLITICA_PADRAO}")

    def _v10_ainda_carrega():
        politica = modulo.carregar_politica(YAML_V10)
        return (politica.get("versao") == VERSAO_V10 and bool(politica.get("_lane_conservadora"))), \
               f"v1.0 carregada com lane_conservadora={politica.get('_lane_conservadora')!r}"

    # Reconstruir decisao antiga exige a politica antiga EXECUTAVEL: preservar o arquivo e
    # recusar carrega-lo nao preserva nada.
    itens.checar("v1.1: a v1.0 preservada ainda CARREGA no roteador (reconstruir decisao antiga)",
                 _v10_ainda_carrega)

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
    # Ninguem marca a versao como homologada sem o registro do dono: e o item que
    # impede a politica de "entrar em vigor por edicao de arquivo".
    itens.add("v1.1: NAO se declara homologada sem registro do Anderson",
              registrada_em is None or registro_cita,
              f"registrada_em={registrada_em!r} registro_cita_a_versao={registro_cita}")
    itens.add("v1.1: registrada_em preenchido exige `homologacao.estado` coerente (nao pendente)",
              registrada_em is None or str(hom.get("estado") or "") != "pendente",
              f"estado={hom.get('estado')!r} registrada_em={registrada_em!r}")
    # A versao esta EM VIGOR: exige data e nome de quem homologou, e a vigencia so existe com
    # a homologacao aprovada. Sao os itens que impedem "entrar em vigor" por edicao de arquivo
    # sem rito — o mesmo defeito que a versao anterior evitava recusando-se a se declarar em vigor.
    itens.add("v1.1: EM VIGOR exige `homologacao.registrada_em` (a vigencia tem data)",
              bool(str(registrada_em or "").strip()), f"registrada_em={registrada_em!r}")
    itens.add("v1.1: EM VIGOR exige `homologacao.registrada_por` nomeando quem homologou",
              "anderson" in _normalizar_simples(str(hom.get("registrada_por") or "")),
              f"registrada_por={hom.get('registrada_por')!r}")
    itens.add("v1.1: EM VIGOR exige `homologacao.estado: aprovada`",
              str(hom.get("estado") or "") == "aprovada", f"estado={hom.get('estado')!r}")
    itens.add("v1.1: com a versao EM VIGOR, `congelada_em` esta preenchido",
              bool(str(dado.get("congelada_em") or "").strip()), f"congelada_em={dado.get('congelada_em')!r}")

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

    # Onde o piso entra no recibo. O recibo tem 13 campos e nao cresce por conveniencia
    # (§2.6): o piso tem de caber num campo JA declarado do contrato, e a regra diz qual.
    registrar = regra.get("registrar_no_recibo") or {}
    campo_do_registro = str(registrar.get("campo") or "")
    campos_do_recibo = [str(x) for x in ((dado.get("recibo") or {}).get("campos") or [])]
    itens.add("v1.1: a regra declara ONDE registrar o piso no recibo",
              bool(campo_do_registro), f"registrar_no_recibo={registrar}")
    itens.add("v1.1: o campo declarado para o piso existe no contrato do recibo",
              campo_do_registro in campos_do_recibo, f"campo={campo_do_registro!r} contrato={campos_do_recibo}")
    itens.add("v1.1: a regra nomeia a FORMA do registro (`piso_por_ambiente`)",
              "piso_por_ambiente" in str(registrar.get("forma") or ""),
              f"forma={str(registrar.get('forma'))[:90]!r}")
    itens.add("v1.1: a regra declara que o motivo vai para o rastro da decisao",
              "motivo" in str(registrar.get("motivo") or "").lower()
              or "rastro" in str(registrar.get("forma") or "").lower(),
              f"motivo={str(registrar.get('motivo'))[:90]!r}")

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
    itens.add("v1.1-doc: o documento diz que a regra ESTA em execucao",
              "em execução" in doc_v11.replace("\n", " ")
              and "não executada" not in doc_low and "nao executada" not in doc_low)
    itens.add("v1.1-doc: o documento nomeia o instrumento das metricas",
              "medir_metricas_por_lane.py" in doc_v11)
    itens.add("v1.1-doc: o documento declara a versao EM VIGOR e nao a chama de rascunho",
              ("EM VIGOR" in doc_v11 or "em vigor" in doc_low)
              and "rascunho" not in doc_low
              and "não homologado" not in doc_low and "nao homologado" not in doc_low)
    itens.add("v1.1-doc: o documento declara qual versao fica preservada para auditoria",
              "auditoria" in doc_low and ("v1.0" in doc_v11 or "1.0" in doc_v11)
              and "versao_em_vigor" in doc_v11)
    itens.add("v1.1-doc: o documento manda o rito da v1.0 para a homologacao",
              "registro-de-aprovacoes" in doc_v11 and "homologa" in doc_low)


# ---------------------------------------------------------------------------
# PARTE 3 — provas de comportamento contra o roteador em vigor
# ---------------------------------------------------------------------------
def _politica_temporaria(raiz: pathlib.Path, mutador, origem: pathlib.Path = YAML_V10) -> pathlib.Path:
    """Copia uma politica versionada para um diretorio temporario e aplica a mutacao.

    Por padrao a origem e a v1.0 PRESERVADA e a copia mantem `versao: jev-policy-v1.0`:
    o que se prova ali e a SEMANTICA da chave explicita no schema que o roteador conhece,
    sem depender da versao em vigor. As provas do piso passam `origem=YAML_V11` (a regra
    so existe na v1.1). O arquivo versionado nunca e tocado.

    O dump do YAML perde os comentarios, e as duas politicas declaram o vocabulario de
    resultado (`PASS | RETRY | ESCALATE | BLOCK`) justamente em comentario — vocabulario
    que o roteador EXIGE encontrar no texto da politica. A copia recebe o mesmo vocabulario
    como cabecalho: sem isso o mutante nem carrega e a prova mediria a coisa errada.
    """
    dado = yaml.safe_load(origem.read_text(encoding="utf-8"))
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

    # ---- B3: a v1.1 e ACEITA pelo roteador em vigor (o portao abriu) -----
    itens.add("B3 comportamento: a v1.1 e ACEITA pelo roteador em vigor (o portao de versao abriu)",
              VERSAO_V11 in modulo.VERSOES_DE_POLITICA_SUPORTADAS,
              f"versao do roteador={modulo.ROUTER_VERSION} suportadas={sorted(modulo.VERSOES_DE_POLITICA_SUPORTADAS)}")
    # O portao so podia abrir JUNTO com o piso implementado: abrir antes deixaria uma regra
    # declarada sendo ignorada em silencio (pior que recusar). Por isso a prova de que o piso
    # existe e a RECUSA por operacao nao implementada — fail-closed, nao promessa. As provas
    # de execucao (B5, B6, B7) provam o mesmo por comportamento.
    def com_operacao_nao_implementada(d):
        copia = copy.deepcopy(d)
        copia["regra_de_lane_por_ambiente"]["operacao"] = "backfill_de_dados"
        return copia

    try:
        politica = modulo.carregar_politica(
            _politica_temporaria(temporario, com_operacao_nao_implementada, origem=YAML_V11))
        itens.add("B3b comportamento: regra declarada para operacao que o roteador NAO implementa e RECUSADA",
                  False, f"aceitou e devolveu lane_conservadora={politica.get('_lane_conservadora')!r}")
    except modulo.PoliticaInvalida as erro:
        motivos = _recusa(erro)
        itens.add("B3b comportamento: regra declarada para operacao que o roteador NAO implementa e RECUSADA",
                  "nao implementa" in motivos and "nao declara o resultado" not in motivos,
                  f"{motivos[:150]}")

    # B3c/B3d: a versao declarada como EM VIGOR e a que o roteador carrega POR PADRAO. Uma
    # politica que se diz em vigor enquanto o roteador carrega a antiga nao esta em vigor.
    # ANCORA:VERSAO_SUCESSORA (30/09/2026) — com a v1.2 homologada e em vigor (card
    # TRE-W0-E04-T09), "a versao padrao == a v1.1" virou fato datado. O invariante que
    # continua valendo — e que era o proposito do item — e mais forte e independe de qual
    # versao venceu: o arquivo que o roteador carrega POR PADRAO tem de se declarar a SI
    # MESMO como a versao em vigor (nada de "em vigor" sem estar, e nada de sucessao
    # silenciosa: a v1.1 so sai de vigor por uma versao que declara `substitui`).
    def _padrao_e_a_versao_em_vigor():
        padrao = pathlib.Path(modulo.CAMINHO_POLITICA_PADRAO)
        carregada = modulo.carregar_politica()
        declaracao = yaml.safe_load(padrao.read_text(encoding="utf-8")) or {}
        em_vigor = (declaracao.get("versao_em_vigor") or {}).get("versao")
        ok = (carregada.get("versao") == em_vigor
              and declaracao.get("versao") == em_vigor
              and str(declaracao.get("estado")) == "em-vigor")
        return ok, (f"padrao={padrao.name} carregada={carregada.get('versao')!r} "
                    f"declara_em_vigor={em_vigor!r} estado={declaracao.get('estado')!r}")

    itens.checar("B3c comportamento: o arquivo PADRAO do roteador carrega a versao que ele mesmo "
                 "declara em vigor (rascunho nunca e o padrao)", _padrao_e_a_versao_em_vigor)
    itens.checar("B3d comportamento: a v1.1 saiu de vigor para uma versao que DECLARA `substitui: "
                 "jev-policy-v1.1` (sucessao declarada, nunca silenciosa)",
                 lambda: (pathlib.Path(modulo.CAMINHO_POLITICA_PADRAO).resolve() == YAML_V11.resolve()
                          or (yaml.safe_load(pathlib.Path(modulo.CAMINHO_POLITICA_PADRAO)
                                             .read_text(encoding="utf-8")) or {}).get("substitui") == VERSAO_V11,
                          f"padrao={modulo.CAMINHO_POLITICA_PADRAO}"))

    # ---- B4: vocabulario de ambiente bate com o guardrail de DDL ---------
    # Medido contra a politica EM VIGOR: e o vocabulario DECLARADO na v1.1 que tem de casar
    # com o guardrail do roteador, valor a valor (nao por leitura do YAML).
    regra = dado.get("regra_de_lane_por_ambiente") or {}
    ramos = {str(r.get("ambiente")): r for r in (regra.get("ambientes") or [])}
    politica_da_regra = modulo.carregar_politica(YAML_V11)

    def guardrail_de_ddl_aciona(ambiente) -> bool:
        tarefa = {"card_id": "t_v11_ambiente", "titulo": "aplicar ddl de esquema",
                  "descricao": "aplicar ddl de esquema", "acao": "aplicar ddl de esquema",
                  "acao_codigo": "migracao_de_esquema", "ambiente_alvo": ambiente,
                  "lane_proposta": "high", "confianca": 0.95}
        decisao = modulo.decidir(tarefa, politica=politica_da_regra)["decisao"]
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

    # ---- B5..B7: o piso e EXECUTADO pela politica em vigor ---------------
    politica_em_vigor = modulo.carregar_politica(YAML_V11)          # caminho padrao
    politica_preservada = modulo.carregar_politica(YAML_V10)        # auditoria
    execucao = regra.get("execucao") or {}
    roteador_declarado = execucao.get("roteador_que_a_executa")

    # ANCORA:ROTEADOR_DA_VERSAO_HISTORICA — mesmo motivo do item de `versao_em_vigor.roteador`.
    # A garantia que continua valendo e a que interessa: a regra declarada SEGUE sendo
    # executada pelo roteador no ar — e isso e medido por comportamento nos itens abaixo.
    itens.add("B5: a execucao declarada nomeia quem implementou a regra, e o roteador atual a executa",
              roteador_declarado == ROTEADOR_DA_V11
              and VERSAO_V11 in set(modulo.VERSOES_DE_POLITICA_SUPORTADAS),
              f"declarado={roteador_declarado!r} roteador atual={modulo.ROUTER_VERSION!r}")

    def decisao_de(ambiente, lane, acao="aplicar DDL/migration em qualquer ambiente",
                   codigo="migracao_de_esquema", politica=None):
        tarefa = {"card_id": "t_v11_piso", "titulo": acao, "descricao": acao, "acao": acao,
                  "acao_codigo": codigo, "lane_proposta": lane, "confianca": 0.95,
                  "status": "ready"}
        if ambiente is not None:
            tarefa["ambiente_alvo"] = ambiente
        return modulo.decidir(tarefa, politica=politica if politica is not None else politica_em_vigor)

    # DDL em ambiente vivo, proposta medium, confianca alta: o caso do criterio de aceite.
    saida_viva = decisao_de("vivo", "medium")
    decisao_viva, recibo_viva = saida_viva["decisao"], saida_viva["recibo"]
    itens.add("B5 comportamento: DDL em ambiente vivo com proposta medium chega a `critical`",
              decisao_viva["lane"] == "critical",
              f"lane registrada={decisao_viva['lane']!r} (proposta medium)")
    itens.add("B5 comportamento: o ramo vivo exige aprovacao humana REGISTRADA na decisao",
              bool(decisao_viva["exige_aprovacao_humana"]) is True,
              f"exige_aprovacao_humana={decisao_viva['exige_aprovacao_humana']!r}")
    itens.add("B5 comportamento: a decisao registra de qual RAMO veio o piso",
              (decisao_viva.get("piso_de_lane") or {}).get("ramo") == "vivo_ou_producao",
              f"piso_de_lane={decisao_viva.get('piso_de_lane')}")

    # ---- B6: o piso entra no recibo SEM campo novo -----------------------
    campos_do_contrato = [str(x) for x in ((dado.get("recibo") or {}).get("campos") or [])]
    itens.add("B6 comportamento: o recibo da decisao com piso segue com os 13 campos do contrato",
              sorted(recibo_viva) == sorted(campos_do_contrato),
              f"{len(recibo_viva)} campos: {sorted(recibo_viva)}")
    override = recibo_viva.get("override") or {}
    registro_do_piso = override.get("piso_por_ambiente") or {}
    itens.add("B6 comportamento: o piso e registrado em `override.piso_por_ambiente` (campo que a regra declara)",
              registro_do_piso.get("lane_minima") == "critical"
              and registro_do_piso.get("ramo") == "vivo_ou_producao",
              f"override={json.dumps(override, ensure_ascii=False)[:190]}")
    itens.add("B6 comportamento: o registro nomeia a ORIGEM do piso (nao sugere override humano)",
              (restringe := list(override.keys())) == ["piso_por_ambiente"],
              f"chaves do override={restringe}")
    itens.add("B6 comportamento: o motivo do piso aparece no rastro da decisao (auditavel)",
              any("piso de lane por ambiente" in str(m) for m in (decisao_viva.get("motivos") or [])),
              f"motivos={decisao_viva.get('motivos')}")
    # Override humano declarado continua entrando como esta quando NAO ha piso (o piso nao
    # substitui nem inventa override: ele so acrescenta o registro de origem dele).
    declarado = {"por": "anderson", "motivo": "revisao manual"}
    tarefa_com_override = {"card_id": "t_v11_override", "titulo": "ajuste de texto simples",
                           "acao": "ajuste de texto simples", "acao_codigo": "ajuste_de_texto",
                           "lane_proposta": "small", "confianca": 0.95, "override": declarado}
    recibo_sem_piso = modulo.decidir(tarefa_com_override, politica=politica_em_vigor)["recibo"]
    itens.add("B6 comportamento: override humano declarado entra no recibo como esta (sem piso)",
              recibo_sem_piso.get("override") == declarado,
              f"override={recibo_sem_piso.get('override')!r}")
    # ...e com piso E override humano declarados, o recibo continua com 13 campos e separa as origens
    # (`humano` = o que a tarefa declarou; `piso_por_ambiente` = o que a regra elevou). O piso nunca se
    # disfarca de override humano, e o override humano nunca e sobrescrito nem apagado pelo piso.
    tarefa_com_os_dois = {"card_id": "t_v11_ambos", "titulo": "aplicar DDL/migration em qualquer ambiente",
                          "descricao": "aplicar DDL/migration em qualquer ambiente",
                          "acao": "aplicar DDL/migration em qualquer ambiente",
                          "acao_codigo": "migracao_de_esquema", "lane_proposta": "medium",
                          "confianca": 0.95, "ambiente_alvo": "desenvolvimento", "override": declarado}
    recibo_ambos = modulo.decidir(tarefa_com_os_dois, politica=politica_em_vigor)["recibo"]
    override_ambos = recibo_ambos.get("override") or {}
    itens.add("B6 comportamento: piso E override humano no mesmo card: 13 campos e as duas origens separadas",
              sorted(recibo_ambos) == sorted(campos_do_contrato)
              and override_ambos.get("humano") == declarado
              and (override_ambos.get("piso_por_ambiente") or {}).get("ramo") == "novo_ou_dev",
              f"campos={len(recibo_ambos)} override={json.dumps(override_ambos, ensure_ascii=False)[:160]}")

    # ---- B7: o piso SO ELEVA --------------------------------------------
    itens.add("B7 comportamento: DDL em dev com proposta medium SOBE para high (piso eleva)",
              decisao_de("desenvolvimento", "medium")["decisao"]["lane"] == "high",
              f"lane={decisao_de('desenvolvimento', 'medium')['decisao']['lane']!r}")
    itens.add("B7 comportamento: DDL em dev com proposta small tambem sobe para high",
              decisao_de("desenvolvimento", "small")["decisao"]["lane"] == "high")
    elevada = decisao_de("desenvolvimento", "critical")["decisao"]
    itens.add("B7 comportamento: DDL em dev com proposta `critical` CONTINUA `critical` (o piso nao rebaixa)",
              elevada["lane"] == "critical", f"lane={elevada['lane']!r} proposta=critical")
    sem_ambiente = decisao_de(None, "medium")["decisao"]
    itens.add("B7 comportamento: sem ambiente declarado o piso cai no ramo MAIS conservador (`critical`)",
              sem_ambiente["lane"] == "critical"
              and (sem_ambiente.get("piso_de_lane") or {}).get("ramo_declarado") is False,
              f"lane={sem_ambiente['lane']!r} piso={sem_ambiente.get('piso_de_lane')}")
    fora = decisao_de("vivo", "medium", acao="ajuste de texto simples", codigo="ajuste_de_texto")["decisao"]
    itens.add("B7 comportamento: tarefa FORA da operacao governada nao e tocada pelo piso",
              fora["lane"] == "medium" and not fora.get("piso_de_lane"),
              f"lane={fora['lane']!r} piso_de_lane={fora.get('piso_de_lane')!r}")
    # A versao PRESERVADA nao tem a regra: nada muda nela. Sem isto, "preservar para auditoria"
    # poderia esconder uma mudanca de comportamento retroativa na politica antiga.
    auditavel = decisao_de("vivo", "medium", politica=politica_preservada)["decisao"]
    itens.add("B7 comportamento: a v1.0 preservada (sem a regra) NAO recebe o piso (nada muda nela)",
              auditavel["lane"] != "critical" and not auditavel.get("piso_de_lane"),
              f"lane={auditavel['lane']!r} piso={auditavel.get('piso_de_lane')!r}")


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
    # Cada parte roda isolada: excecao nao pode abortar a suite (um verificador que morre
    # nao reprova nada — o traceback vira falha declarada, com o motivo, e a suite segue).
    for rotulo, parte in (("PARTE 1 (bateria da v1.0)", lambda: itens_da_bateria_da_v1_0(itens, yaml_v11, doc_v11, base)),
                          ("PARTE 2 (contrato da v1.1)", lambda: itens_do_contrato(itens, dado, yaml_v11, doc_v11, modulo)),
                          ("PARTE 3 (comportamento do roteador)", lambda: itens_de_comportamento(itens, dado, modulo))):
        try:
            parte()
        except Exception as erro:  # noqa: BLE001
            itens.add(f"{rotulo}: roda ate o fim", False,
                      f"excecao: {type(erro).__name__}: {erro}")
    return itens


def imprimir(itens: Itens) -> int:
    for nome, ok, detalhe in itens.lista:
        marca = "OK   " if ok else "FALHOU"
        print(f"{marca} {nome}" + (f"  [{detalhe}]" if detalhe else ""))
    return itens.falhas()


def _arvore_do_roteador_mutado(codigo: str) -> pathlib.Path:
    """Repo temporario com o roteador mutado, para exercitar o MESMO encanamento.

    O roteador resolve a raiz do repo por `__file__` e carrega a politica padrao por
    `CAMINHO_POLITICA_PADRAO`; a arvore temporaria reproduz esses caminhos (politicas
    versionadas e pasta de papeis por symlink) para que a mutacao de codigo seja medida
    no roteador de verdade — sem tocar no arquivo versionado.
    """
    raiz = pathlib.Path(tempfile.mkdtemp(prefix="jev-v11-mut-"))
    (raiz / "hermes/jev/routing").mkdir(parents=True)
    (raiz / "hermes/jev/routing/router.py").write_text(codigo, encoding="utf-8")
    for nome in ("policy_v1.yaml", "policy_v1_1.yaml"):
        os.symlink(RAIZ / "hermes/jev" / nome, raiz / "hermes/jev" / nome)
    os.symlink(RAIZ / "hermes/policies", raiz / "hermes/policies")
    return raiz / "hermes/jev/routing/router.py"


def autoteste(base, modulo, yaml_v11: str, doc_v11: str) -> bool:
    """Cada mutacao TEM de ser detectada — em copia temporaria, nunca no arquivo versionado."""
    print("\n=== AUTOTESTE: mutacoes que o verificador precisa reprovar ===")
    base_dado = yaml.safe_load(yaml_v11)
    # (nome, yaml, documento, modulo) — modulo None = o roteador de verdade.
    mutacoes = []

    def mut(nome, funcao_dado=None, doc=None):
        d = base_dado if funcao_dado is None else funcao_dado(copy.deepcopy(base_dado))
        mutacoes.append((nome, texto_de_yaml(d) if funcao_dado is not None else yaml_v11,
                         doc if doc is not None else doc_v11, None))

    def mut_roteador(nome, funcao_codigo):
        """Mutacao de CODIGO do roteador: a prova de que a suite mede comportamento.

        Prova de YAML sozinha nao distingue "a regra esta declarada" de "a regra e
        executada": estas mutacoes tiram o piso (ou o invertem) do roteador e exigem que
        a suite reprove.
        """
        original = ROTEADOR.read_text(encoding="utf-8")
        mutado = funcao_codigo(original)
        if mutado == original:
            # Mutacao que nao muda o codigo seria um buraco silencioso no autoteste.
            mutacoes.append((f"{nome} [MUTACAO NAO APLICADA]", "", "", None))
            return
        caminho = _arvore_do_roteador_mutado(mutado)
        mutacoes.append((nome, yaml_v11, doc_v11,
                         carregar_modulo(caminho, f"roteador_mutado_{len(mutacoes)}")))

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
        mut("declara-se homologado sem registro do dono",
            lambda d: (d["homologacao"].__setitem__("registrada_em", "2026-09-30"), d)[1])
    mut("registrada_em preenchido com homologacao ainda pendente",
        lambda d: (d["homologacao"].__setitem__("registrada_em", "2026-09-30"),
                   d["homologacao"].__setitem__("estado", "pendente"), d)[1])
    # Estado/vigencia: uma versao EM VIGOR tem de carregar data, nome, homologacao aprovada e
    # congelamento. Qualquer um faltando volta a permitir "entrar em vigor por edicao de arquivo".
    mut("estado trocado para rascunho (regressao de vigencia)",
        lambda d: (d.__setitem__("estado", "rascunho-nao-homologado"), d)[1])
    mut("`congelada_em` esvaziado com a versao em vigor",
        lambda d: (d.__setitem__("congelada_em", None), d)[1])
    mut("EM VIGOR sem `homologacao.registrada_em`",
        lambda d: (d["homologacao"].__setitem__("registrada_em", None), d)[1])
    mut("EM VIGOR sem `homologacao.registrada_por`",
        lambda d: (d["homologacao"].pop("registrada_por"), d)[1])
    mut("EM VIGOR com `homologacao.estado: pendente`",
        lambda d: (d["homologacao"].__setitem__("estado", "pendente"), d)[1])
    mut("`homologacao.registrada_por` apontando quem NAO homologou",
        lambda d: (d["homologacao"].__setitem__("registrada_por", "ninguem"), d)[1])
    mut("a versao deixa de se declarar em vigor (estado vazio)",
        lambda d: (d.__setitem__("estado", ""), d)[1])

    # versao_em_vigor: a vigencia tem de ser ENDERECADA (versao + caminho do roteador)
    mut("bloco `versao_em_vigor` removido",
        lambda d: (d.pop("versao_em_vigor"), d)[1])
    mut("`versao_em_vigor.versao` apontando a versao antiga",
        lambda d: (d["versao_em_vigor"].__setitem__("versao", VERSAO_V10), d)[1])
    mut("`versao_em_vigor.caminho` apontando a politica ANTIGA (roteador carregaria a v1.0)",
        lambda d: (d["versao_em_vigor"].__setitem__("caminho", "hermes/jev/policy_v1.yaml"), d)[1])
    mut("`versao_em_vigor.caminho` apontando arquivo que nao existe",
        lambda d: (d["versao_em_vigor"].__setitem__("caminho", "hermes/jev/policy_v9.yaml"), d)[1])
    mut("`versao_em_vigor.roteador` apontando roteador que nao existe",
        lambda d: (d["versao_em_vigor"].__setitem__("roteador", "jev-router-v9.9"), d)[1])
    mut("`versao_em_vigor.desde` esvaziado",
        lambda d: (d["versao_em_vigor"].__setitem__("desde", ""), d)[1])
    mut("`preservada_para_auditoria` removida",
        lambda d: (d["versao_em_vigor"].pop("preservada_para_auditoria"), d)[1])
    mut("versao preservada apontando arquivo que nao e a v1.0",
        lambda d: (d["versao_em_vigor"]["preservada_para_auditoria"].__setitem__(
            "caminho", "hermes/jev/policy_v1_1.yaml"), d)[1])
    mut("versao preservada apontando a PROPRIA versao em vigor",
        lambda d: (d["versao_em_vigor"]["preservada_para_auditoria"].__setitem__("versao", VERSAO_V11), d)[1])

    # registrar_no_recibo: o piso tem de caber no contrato de 13 campos
    mut("`registrar_no_recibo` removido da regra",
        lambda d: (d["regra_de_lane_por_ambiente"].pop("registrar_no_recibo"), d)[1])
    mut("`registrar_no_recibo.campo` apontando campo FORA do contrato do recibo",
        lambda d: (d["regra_de_lane_por_ambiente"]["registrar_no_recibo"].__setitem__(
            "campo", "piso_de_lane"), d)[1])
    mut("`registrar_no_recibo.forma` sem nomear `piso_por_ambiente`",
        lambda d: (d["regra_de_lane_por_ambiente"]["registrar_no_recibo"].__setitem__(
            "forma", "um registro qualquer"), d)[1])
    mut("`registrar_no_recibo` sem o destino do motivo",
        lambda d: (d["regra_de_lane_por_ambiente"]["registrar_no_recibo"].pop("motivo"), d)[1])
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
    mut("documento deixa de dizer que a regra ESTA em execucao", None,
        doc_v11.replace("em execução", "ainda não executada"))
    mut("documento volta a se declarar RASCUNHO", None,
        doc_v11.replace("JEV Decision Policy V1.1 — em vigor", "JEV Decision Policy V1.1 — RASCUNHO")
        .replace("**EM VIGOR**", "**RASCUNHO, NÃO HOMOLOGADO**"))
    mut("documento deixa de declarar onde a v1.0 fica preservada (auditoria)", None,
        doc_v11.replace("auditoria", "arquivo").replace("versao_em_vigor", "vigencia"))
    mut("documento deixa de citar o instrumento", None,
        doc_v11.replace("medir_metricas_por_lane.py", "um script qualquer"))

    # codigo do roteador: prova de que a suite mede COMPORTAMENTO, nao declaracao
    mut_roteador("roteador sem o piso: a regra declarada volta a ser ignorada em silencio",
                 lambda c: c.replace('    plano["lane"] = _lane_com_piso(politica, tarefa, lane_final)[0]',
                                     '    plano["lane"] = lane_final'))
    mut_roteador("piso REBAIXANDO: a lane minima do ramo vira a lane final (sem comparar)",
                 lambda c: c.replace(
                     'final = lane_mais_conservadora(politica, lane, piso["lane_minima"]) or piso["lane_minima"]',
                     'final = piso["lane_minima"]'))
    mut_roteador("ambiente NAO declarado caindo no PRIMEIRO ramo (dev), nao no conservador",
                 lambda c: c.replace(
                     'escolhido = max(ramos, key=lambda r: indice_da_lane(politica, r.get("lane_minima")))',
                     'escolhido = ramos[0]'))
    mut_roteador("piso fora do recibo: `override` deixa de registrar o piso",
                 lambda c: c.replace('    plano["override"] = {"piso_por_ambiente": registro}',
                                     '    plano["override"] = None'))
    mut_roteador("ramo vivo sem exigir aprovacao humana na decisao",
                 lambda c: c.replace('    if piso["exige_aprovacao_humana"]:\n'
                                     '        plano["exige_aprovacao_humana"] = True\n',
                                     '    if False:\n'
                                     '        plano["exige_aprovacao_humana"] = True\n'))
    mut_roteador("caminho padrao do roteador revertido para a politica ANTIGA",
                 lambda c: c.replace('CAMINHO_POLITICA_PADRAO = _RAIZ_DO_REPO / "hermes/jev/policy_v1_2.yaml"',
                                     'CAMINHO_POLITICA_PADRAO = _RAIZ_DO_REPO / "hermes/jev/policy_v1.yaml"'))
    # As duas mutacoes abaixo alvo o TOKEN da versao (nao o literal inteiro do conjunto):
    # datadas em 30/09/2026 pelo card TRE-W0-E04-T10, quando a v1.3 entrou no conjunto de
    # suporte. O literal inteiro muda a cada versao nova — mutacao que casa o literal
    # inteiro vira "nao aplicada" na versao seguinte e o autoteste perde o dente em silencio.
    mut_roteador("roteador deixa de aceitar a v1.1 (portao fechado de novo para ela)",
                 lambda c: c.replace('"jev-policy-v1.1",', ''))
    mut_roteador("roteador deixa de aceitar a versao EM VIGOR (v1.2 fora do conjunto)",
                 lambda c: c.replace('"jev-policy-v1.2", ', ''))
    mut_roteador("roteador deixa de recusar regra de operacao que nao implementa (ignora em silencio)",
                 lambda c: c.replace(
                     '    if operacao not in OPERACOES_COM_PISO_IMPLEMENTADO:\n'
                     '        motivos.append(\n'
                     '            f"{CHAVE_DA_REGRA_DE_PISO} governa a operacao {operacao!r}, que este roteador "\n'
                     '            f"nao implementa (implementadas: {sorted(OPERACOES_COM_PISO_IMPLEMENTADO)}): "\n'
                     '            "recusa, em vez de executar ignorando a regra declarada")\n',
                     '    if False:\n        motivos.append("nunca")\n'))
    mut_roteador("recibo ganha 14o campo com o piso (contrato crescido por conveniencia)",
                 lambda c: c.replace('        "outcome": plano.get("outcome"),',
                                     '        "piso_de_lane": plano["demais"].get("piso_de_lane"),\n'
                                     '        "outcome": plano.get("outcome"),'))

    detectadas = 0
    for nome, y, dd, modulo_da_vez in mutacoes:
        if y == "" and dd == "":
            print(f"FALHOU NAO APLICADA: {nome}  <-- mutacao que nao altera o codigo")
            continue
        itens = verificar(y, dd, base, modulo if modulo_da_vez is None else modulo_da_vez)
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
    print("VERIFICADOR DA JEV DECISION POLICY v1.1 (em vigor, homologada em 29/09/2026)")
    print(f"  politica:  {YAML_V11.relative_to(RAIZ)}")
    print(f"  documento: {DOC_V11.relative_to(RAIZ)}")
    print(f"  roteador em vigor: {modulo.ROUTER_VERSION} "
          f"(suporta {sorted(modulo.VERSOES_DE_POLITICA_SUPORTADAS)})")
    print(f"  preservada para auditoria: {YAML_V10.relative_to(RAIZ)}")
    print("=" * 72)

    yaml_v11 = YAML_V11.read_text(encoding="utf-8")
    doc_v11 = DOC_V11.read_text(encoding="utf-8")
    itens = verificar(yaml_v11, doc_v11, base, modulo)
    falhas = imprimir(itens)

    pediu_autoteste = "--autoteste" in sys.argv
    teste_ok = True
    if pediu_autoteste:
        teste_ok = autoteste(base, modulo, yaml_v11, doc_v11)

    print()
    if falhas == 0 and teste_ok:
        # "teste que nao rodou nao e teste que passou": o autoteste so e anunciado quando
        # foi pedido — antes, a mensagem dizia "+ autoteste OK" mesmo sem roda-lo.
        extra = " + autoteste OK" if pediu_autoteste else " (autoteste nao pedido: rode com --autoteste)"
        print(f"RESULTADO: PASS ({len(itens.lista)} itens, 0 falhas){extra}")
        return 0
    print(f"RESULTADO: FALHOU ({len(itens.lista)} itens, {falhas} falha(s))"
          + ("" if teste_ok else " + autoteste com buraco"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
