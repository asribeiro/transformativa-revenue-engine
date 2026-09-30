#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GATE JEV — encaixe do roteador JEV no dispatch do board (card TRE-W0-E04-T05).

Este arquivo E o "hook": o processo que o board chama ANTES de reivindicar ou
despachar um card. Ele:

  1. le o card direto do board (tabela `tasks`);
  2. resolve o CODIGO CANONICO da acao declarado para o card
     (`hermes/jev/acoes-declaradas.yaml`, campo `acao_codigo`) — via PRINCIPAL
     do contrato do roteador; sem declaracao a acao NAO executa (escala);
  3. chama o roteador (`hermes/jev/routing/router.py::decidir`);
  4. GRAVA O RECIBO sempre (13 campos, sem segredo) em `hermes/jev/receipts/`;
  5. responde em `--resposta` (JSON) e em codigo de saida:

       0 = pode executar
       2 = escala (nao executa)
       3 = bloqueia (nao executa)
       1 = falha do proprio gate -> o board trata como BLOQUEIO (fail-closed)

Contrato estavel do encaixe (nao muda sem versionar `GATE_VERSION`):
  * o codigo canonico e PASSADO pelo dispatch; nunca se casa prosa;
  * sem codigo canonico conhecido nao executa (postura estrita homologada pelo
    Anderson em 29/09/2026) — NAO afrouxar o defeito D07 para fazer o encaixe
    funcionar;
  * ausencia de resposta do gate e abstencao, nunca permissao;
  * recibo gravado em toda consulta, inclusive quando a resposta e "executar".

Aprovacao humana COM executor (desenho `docs/architecture/aprovacao-humana-com-executor.md`,
homologado por Anderson Ribeiro em 30/09/2026, decisoes 1 a 4): DEPOIS da decisao do
roteador e ANTES de gravar o recibo, o gate faz a UNICA pergunta nova — *o card tem
aprovacao registrada, valida, com o hash do texto aprovado e que cobre os dominios desta
decisao?* (`hermes/jev/gate/aprovacoes.py`). Respondendo SIM, o desfecho `ESCALATE` vira
`PASS` com `origem: aprovacao_humana_registrada` no recibo e na resposta. Respondendo NAO,
nada muda no desfecho (fail-closed intacto) e o motivo entra no recibo como
`aprovacao_motivo` QUANDO ha registro para consultar. Sem arquivo de aprovacoes o recibo
continua com os 13 campos exatos do contrato da politica: o encaixe e ADITIVO e nao muda
de versao. Registro em `hermes/jev/aprovacoes-humanas.yaml` (variavel `JEV_APROVACOES`) e
dupla entrada conferida em `docs/operations/registro-de-aprovacoes.md` (variavel
`JEV_REGISTRO_DE_APROVACOES`); as duas existem para a suite rodar em diretorio temporario.

Uso (o board chama assim; tambem serve para uso manual):

  /opt/hermes/.venv/bin/python hermes/jev/gate/gate_jev.py \
      --board transformativa-revenue-engine --card t_6d326367 \
      --kanban-db /opt/data/kanban/boards/transformativa-revenue-engine/kanban.db \
      --resposta /tmp/resposta.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import traceback

GATE_VERSION = "jev-gate-v1"
GATE_NAME = "gate_jev"

RAIZ_DO_REPO = pathlib.Path(__file__).resolve().parents[3]
CAMINHO_DO_ROTEADOR = RAIZ_DO_REPO / "hermes/jev/routing/router.py"
CAMINHO_DAS_DECLARACOES = RAIZ_DO_REPO / "hermes/jev/acoes-declaradas.yaml"
DIRETORIO_DE_RECIBOS = RAIZ_DO_REPO / "hermes/jev/receipts"
# Aprovacao humana COM executor (desenho homologado de 30/09/2026).
CAMINHO_DO_MODULO_DE_APROVACOES = pathlib.Path(__file__).resolve().parent / "aprovacoes.py"
CAMINHO_DAS_APROVACOES = RAIZ_DO_REPO / "hermes/jev/aprovacoes-humanas.yaml"
CAMINHO_DO_REGISTRO_DE_APROVACOES = RAIZ_DO_REPO / "docs/operations/registro-de-aprovacoes.md"
ORIGEM_APROVACAO_HUMANA = "aprovacao_humana_registrada"
CAMPOS_DA_APROVACAO = ("aprovador", "canal", "validade", "hash")


def _caminho_efetivo(explicito, env, padrao):
    """Explicito > variavel de ambiente > padrao do repo.

    As duas variaveis (`JEV_DECLARACOES`, `JEV_RECIBOS_DIR`) existem para a suite
    do encaixe rodar sobre board e declaracoes temporarios sem escrever no repo.
    """
    import os

    if explicito:
        return pathlib.Path(explicito)
    do_ambiente = os.environ.get(env, "").strip()
    if do_ambiente:
        return pathlib.Path(do_ambiente)
    return pathlib.Path(padrao)


# ---------------------------------------------------------------------------
# Carga do roteador por caminho (mesmo padrao dos outros scripts do repo)
# ---------------------------------------------------------------------------
def carregar_roteador(caminho=None):
    caminho = pathlib.Path(caminho or CAMINHO_DO_ROTEADOR)
    if not caminho.is_file():
        raise RuntimeError(f"roteador nao encontrado: {caminho}")
    spec = importlib.util.spec_from_file_location("jev_router_gate", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - defensivo
        raise RuntimeError(f"nao consegui carregar o roteador: {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _yaml():
    try:
        import yaml  # noqa: PLC0415
        return yaml
    except ImportError:  # pragma: no cover - o venv do Hermes tem PyYAML
        return None


# ---------------------------------------------------------------------------
# Declaracao do codigo canonico por card
# ---------------------------------------------------------------------------
def carregar_declaracoes(caminho=None) -> dict:
    """Le `hermes/jev/acoes-declaradas.yaml` -> {card_id: declaracao}.

    Arquivo AUSENTE nao e erro: sem declaracao nenhuma o resultado e o mesmo —
    todo card sem codigo escala (fail-closed). YAML ILEGIVEL e outra coisa: vira
    falha do gate, e falha do gate e BLOQUEIO (nunca permissao silenciosa).
    """
    caminho = _caminho_efetivo(caminho, "JEV_DECLARACOES", CAMINHO_DAS_DECLARACOES)
    if not caminho.exists():
        return {}
    yaml = _yaml()
    if yaml is None:
        raise RuntimeError("PyYAML indisponivel: nao consigo ler as declaracoes de acao")
    try:
        bruto = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except Exception as erro:  # pragma: no cover - erro de sintaxe do arquivo
        raise RuntimeError(f"declaracoes ilegiveis ({caminho}): {erro}") from erro
    if not isinstance(bruto, dict):
        raise RuntimeError(f"declaracoes fora do contrato (esperado mapa): {caminho}")
    entradas = bruto.get("declaracoes") or []
    if not isinstance(entradas, list):
        raise RuntimeError(f"`declaracoes` fora do contrato (esperado lista): {caminho}")
    mapa = {}
    for entrada in entradas:
        if not isinstance(entrada, dict):
            raise RuntimeError(f"declaracao fora do contrato (esperado mapa): {entrada!r}")
        card_id = str(entrada.get("card_id") or "").strip()
        if not card_id:
            raise RuntimeError(f"declaracao sem `card_id`: {entrada!r}")
        mapa[card_id] = dict(entrada)
    return mapa


def tarefa_do_card(roteador, card: dict, politica, declaracao) -> dict:
    """Tarefa sintetica do card + o CODIGO CANONICO declarado (via principal).

    Politica indisponivel NAO vira falha do gate: a decisao roda em modo degradado
    e o roteador ainda grava o recibo (ausencia de resposta e abstencao, nunca
    permissao). Por isso o card e passado adiante mesmo sem politica.
    """
    if politica is not None:
        tarefa = roteador.tarefa_a_partir_do_card(card, politica)
    else:
        tarefa = dict(card)
        tarefa["acao"] = card.get("titulo") or ""
    if declaracao:
        codigo = str(declaracao.get("acao_codigo") or "").strip()
        if codigo:
            tarefa[roteador.CAMPO_DO_CODIGO_DE_ACAO] = codigo
        sinais = declaracao.get("sinais")
        if isinstance(sinais, dict) and sinais:
            tarefa["sinais"] = dict(sinais)
        if declaracao.get("ambiente_alvo"):
            tarefa["ambiente_alvo"] = declaracao["ambiente_alvo"]
        if declaracao.get("override"):
            tarefa["override"] = declaracao["override"]
    return tarefa


# ---------------------------------------------------------------------------
# Recibo
# ---------------------------------------------------------------------------
def gravar_recibo(roteador, recibo: dict, card_id: str, diretorio=None) -> str:
    diretorio = _caminho_efetivo(diretorio, "JEV_RECIBOS_DIR", DIRETORIO_DE_RECIBOS)
    nome = f"{card_id}--{recibo.get('decision_id') or 'sem-id'}.json"
    caminho = roteador.escrever_recibo(recibo, diretorio / nome)
    return str(caminho)


def gravar_registro_de_falha(card_id: str, erro: str, diretorio=None) -> str:
    """Registro de falha do PROPRIO gate. NAO e um recibo de decisao.

    Existe para que a regra "recibo gravado sempre" nao vire "silencio quando o
    gate quebra": o arquivo diz explicitamente que nao houve decisao.
    """
    diretorio = _caminho_efetivo(diretorio, "JEV_RECIBOS_DIR", DIRETORIO_DE_RECIBOS)
    diretorio.mkdir(parents=True, exist_ok=True)
    caminho = diretorio / f"{card_id}--FALHA-DO-GATE.json"
    conteudo = {"gate": GATE_VERSION, "gate_versao": GATE_VERSION, "card_id": card_id,
                "decisao": None, "erro": erro,
                "observacao": "falha do gate: o board trata como BLOQUEIO (fail-closed); "
                              "este arquivo NAO e um recibo de 13 campos"}
    caminho.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(caminho)


# ---------------------------------------------------------------------------
# Aprovacao humana COM executor (desenho homologado, decisoes 1 a 4)
# ---------------------------------------------------------------------------
def carregar_modulo_de_aprovacoes(caminho=None):
    """Carrega `aprovacoes.py` por caminho (mesmo padrao do roteador)."""
    caminho = pathlib.Path(caminho or CAMINHO_DO_MODULO_DE_APROVACOES)
    if not caminho.is_file():
        raise RuntimeError(f"modulo de aprovacoes nao encontrado: {caminho}")
    spec = importlib.util.spec_from_file_location("jev_aprovacoes_gate", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - defensivo
        raise RuntimeError(f"nao consegui carregar o modulo de aprovacoes: {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def ambiente_alvo_do_card(tarefa) -> str:
    """Ambiente alvo DECLARADO no card/catalogo; "" quando nao ha declaracao.

    Nada e presumido: sem declaracao o valor e vazio e a camada de aprovacao trata a
    ausencia como ramo conservador — a mesma postura do piso de lane do roteador.
    """
    for campo in ("ambiente_alvo", "ambiente"):
        valor = str((tarefa or {}).get(campo) or "").strip()
        if valor:
            return valor
    return ""


def consultar_aprovacao(modulo, *, card: dict, tarefa: dict, decisao: dict,
                        caminho=None, caminho_registro=None) -> dict:
    """Pergunta a camada de aprovacao e devolve `{aplicavel, motivo, dados, ha_registro}`.

    NUNCA levanta: falha ao consultar e ausencia de aprovacao (fail-closed), com o
    motivo registrado — a consulta nova nao pode derrubar o gate nem liberar o card.

    `ha_registro` diz se HAVIA registro no caminho consultado. Ele existe para a regra
    de mudanca minima: sem arquivo de aprovacoes nao ha o que consultar e o recibo
    continua exatamente o de hoje (13 campos do contrato da politica).
    """
    if modulo is None:
        return {"aplicavel": False, "ha_registro": False, "dados": {},
                "motivo": "camada de aprovacao indisponivel (modulo nao carregado)"}
    alvo = _caminho_efetivo(caminho, "JEV_APROVACOES", CAMINHO_DAS_APROVACOES)
    alvo_registro = _caminho_efetivo(caminho_registro, "JEV_REGISTRO_DE_APROVACOES",
                                     CAMINHO_DO_REGISTRO_DE_APROVACOES)
    ha_registro = bool(alvo.is_file())
    try:
        resultado = modulo.avaliar(
            card_id=str(card.get("card_id") or ""),
            titulo=str(card.get("titulo") or ""),
            corpo=str(card.get("descricao") or ""),
            dominios=list(decisao.get("dominios_sensiveis") or []),
            ambiente_alvo=ambiente_alvo_do_card(tarefa) or None,
            sinais=dict(tarefa.get("sinais") or {}),
            caminho=str(alvo),
            caminho_registro=str(alvo_registro),
        )
    except Exception as erro:  # fail-closed: falha da consulta NAO e aprovacao
        return {"aplicavel": False, "ha_registro": ha_registro, "dados": {},
                "motivo": f"falha ao consultar o registro de aprovacoes: "
                          f"{type(erro).__name__}: {erro}"}
    if not isinstance(resultado, dict) or "aplicavel" not in resultado:
        return {"aplicavel": False, "ha_registro": ha_registro, "dados": {},
                "motivo": "camada de aprovacao respondeu fora do contrato"}
    dados = {chave: resultado[chave] for chave in CAMPOS_DA_APROVACAO if resultado.get(chave)}
    return {"aplicavel": bool(resultado.get("aplicavel")),
            "ha_registro": ha_registro, "dados": dados,
            "motivo": str(resultado.get("motivo") or "")}


# ---------------------------------------------------------------------------
# Decisao
# ---------------------------------------------------------------------------
def decidir_card(card_id: str, *, board=None, kanban_db=None, politica_path=None,
                 declaracoes_path=None, recibos_dir=None, roteador=None,
                 aprovacoes_path=None, registro_aprovacoes_path=None,
                 caminho_modulo_aprovacoes=None, aprovacoes_modulo=None) -> dict:
    """Roda o gate para um card e devolve o mapa de resposta (sem gravar nada)."""
    roteador = roteador or carregar_roteador()
    card = roteador.carregar_card_do_board(card_id, slug=board, caminho_do_banco=kanban_db)
    declaracoes = carregar_declaracoes(declaracoes_path)
    declaracao = declaracoes.get(card_id)

    politica = None
    motivo_politica = None
    try:
        politica = roteador.carregar_politica(politica_path)
    except roteador.PoliticaInvalida as erro:
        motivo_politica = f"politica indisponivel/invalida: {erro}"

    tarefa = tarefa_do_card(roteador, card, politica, declaracao)
    papeis = (politica.get("_papeis") or {}) if politica is not None else {}
    resultado = roteador.decidir(tarefa, politica=politica, motivo_politica=motivo_politica,
                                 politicas_papel=papeis)
    recibo = resultado["recibo"]
    decisao = resultado["decisao"]

    # ---- Aprovacao humana COM executor (decisoes 1 a 4) ---------------------
    # Depois da decisao do roteador (que decide como sempre, fail-closed) e ANTES de
    # gravar o recibo: se o desfecho e ESCALATE, a unica pergunta nova e se o card tem
    # aprovacao registrada, valida, com o hash do texto aprovado e que cobre exatamente
    # os dominios DESTA decisao. Sim -> o desfecho vira PASS COM RASTRO. Nao -> nada
    # muda no desfecho.
    origem = None
    campos_da_aprovacao: dict = {}
    aprovacao_motivo = None
    escalar = str(getattr(roteador, "OUTCOME_ESCALAR", "ESCALATE"))
    if str(decisao.get("outcome")) == escalar:
        modulo = aprovacoes_modulo
        if modulo is None:
            try:
                modulo = carregar_modulo_de_aprovacoes(caminho_modulo_aprovacoes)
            except Exception as erro:  # fail-closed: sem camada, sem aprovacao
                modulo = None
                aprovacao_motivo = (f"camada de aprovacao indisponivel: "
                                    f"{type(erro).__name__}: {erro}")
        aprovacao = consultar_aprovacao(
            modulo, card=card, tarefa=tarefa, decisao=decisao,
            caminho=aprovacoes_path, caminho_registro=registro_aprovacoes_path)
        aprovacao_motivo = aprovacao["motivo"]
        if aprovacao["aplicavel"]:
            origem = ORIGEM_APROVACAO_HUMANA
            campos_da_aprovacao = dict(aprovacao["dados"])
            decisao["outcome"] = getattr(roteador, "OUTCOME_EXECUTAR", "PASS")
            decisao["decidido"] = "executar"
            decisao["pode_executar"] = True
            # O motivo PRIMARIO passa a ser o da aprovacao (e o que o board grava no
            # evento `jev_gate_allowed` e mostra no `kanban tail`): um card que executou
            # nao pode carregar como primeiro motivo a razao da escalacao. Os motivos do
            # roteador continuam todos, logo abaixo, no mesmo rastro.
            decisao["motivos"] = [
                f"{origem}: aprovacao de {campos_da_aprovacao.get('aprovador')} "
                f"(canal {campos_da_aprovacao.get('canal')}, validade "
                f"{campos_da_aprovacao.get('validade')}) cobre os dominios desta decisao"
            ] + list(decisao.get("motivos") or [])
            recibo["outcome"] = decisao["outcome"]
            # O recibo tem contrato FECHADO de 13 campos (policy_v1_2.yaml) e a suite do
            # gate reprova campo a mais. O rastro da aprovacao entra DENTRO do campo
            # `override`, que existe exatamente para registrar excecao com razao; nenhum
            # campo novo e criado no recibo.
            rastro = {"origem": origem,
                      "aprovador": campos_da_aprovacao.get("aprovador"),
                      "canal": campos_da_aprovacao.get("canal"),
                      "validade": campos_da_aprovacao.get("validade"),
                      "hash": campos_da_aprovacao.get("hash")}
            anterior = recibo.get("override")
            if isinstance(anterior, dict):
                recibo["override"] = {**anterior, "aprovacao_humana": rastro}
            elif anterior:
                recibo["override"] = {"anterior": anterior, "aprovacao_humana": rastro}
            else:
                recibo["override"] = {"aprovacao_humana": rastro}
        elif aprovacao["ha_registro"]:
            # Ha registro para consultar e a resposta foi NAO: NADA e acrescentado ao
            # recibo, que segue com os 13 campos exatos. O motivo viaja na resposta do
            # gate — e a resposta e o que o board grava no evento e o `kanban tail` mostra.
            pass

    caminho_recibo = gravar_recibo(roteador, recibo, card_id, recibos_dir)

    resposta = {
        "gate": GATE_VERSION,
        "gate_versao": GATE_VERSION,
        "board": board,
        "card_id": card_id,
        "allow": bool(decisao["pode_executar"]),
        "outcome": decisao["outcome"],
        "decidido": decisao["decidido"],
        "lane": decisao.get("lane"),
        "motivo": (decisao.get("motivos") or [""])[0],
        "motivos": list(decisao.get("motivos") or []),
        "exige_aprovacao_humana": bool(decisao.get("exige_aprovacao_humana")),
        "guardrails_acionados": list(decisao.get("guardrails_acionados") or []),
        "codigo_de_acao": decisao.get("codigo_de_acao"),
        "origem_do_codigo_de_acao": decisao.get("origem_do_codigo_de_acao"),
        "dominios_sensiveis": list(decisao.get("dominios_sensiveis") or []),
        "card_id_do_recibo": recibo.get("card_id"),
        "decision_id": recibo.get("decision_id"),
        "receipt_path": caminho_recibo,
        "politica_lida_de": decisao.get("politica_lida_de"),
        "declaracao_usada": bool(declaracao),
        "origem": origem,
        "aprovacao_motivo": aprovacao_motivo,
    }
    resposta.update(campos_da_aprovacao)
    return resposta


def _resposta_de_falha(card_id: str, erro: str, recibos_dir=None) -> dict:
    return {
        "gate": GATE_VERSION,
        "gate_versao": GATE_VERSION,
        "card_id": card_id,
        "allow": False,
        "outcome": "GATE_INDISPONIVEL",
        "decidido": "falha_do_gate",
        "lane": None,
        "motivo": f"falha do gate JEV: {erro}",
        "motivos": [f"falha do gate JEV: {erro}"],
        "exige_aprovacao_humana": True,
        "guardrails_acionados": [],
        "codigo_de_acao": None,
        "origem_do_codigo_de_acao": None,
        "dominios_sensiveis": [],
        "decision_id": None,
        "receipt_path": gravar_registro_de_falha(card_id, erro, recibos_dir),
        "declaracao_usada": False,
        "origem": None,
        "aprovacao_motivo": None,
    }


def codigo_de_saida(resposta: dict) -> int:
    if resposta.get("allow"):
        return 0
    if resposta.get("outcome") == "BLOCK" or str(resposta.get("outcome")) == "BLOCK":
        return 3
    if str(resposta.get("outcome")) == "ESCALATE":
        return 2
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parser_de_argumentos(argv=None):
    parser = argparse.ArgumentParser(
        description="Gate JEV: decide se um card do board pode executar (encaixe do card TRE-W0-E04-T05).")
    parser.add_argument("--card", required=True, help="id do card no board (tabela tasks)")
    parser.add_argument("--board", default=None, help="slug do board")
    parser.add_argument("--kanban-db", dest="kanban_db", default=None,
                        help="caminho do kanban.db (o dispatch passa o caminho resolvido)")
    parser.add_argument("--politica", default=None, help="caminho da politica do JEV")
    parser.add_argument("--declaracoes", default=None, help="caminho do acoes-declaradas.yaml")
    parser.add_argument("--recibos-dir", dest="recibos_dir", default=None,
                        help="diretorio dos recibos (padrao: hermes/jev/receipts)")
    parser.add_argument("--aprovacoes", default=None,
                        help="caminho do aprovacoes-humanas.yaml (padrao: hermes/jev/aprovacoes-humanas.yaml)")
    parser.add_argument("--registro-aprovacoes", dest="registro_aprovacoes", default=None,
                        help="caminho do registro-de-aprovacoes.md (dupla entrada)")
    parser.add_argument("--resposta", default=None, help="grava a resposta do gate neste arquivo JSON")
    return parser


def main(argv=None) -> int:
    args = parser_de_argumentos(argv).parse_args(argv)
    try:
        resposta = decidir_card(args.card, board=args.board, kanban_db=args.kanban_db,
                                politica_path=args.politica, declaracoes_path=args.declaracoes,
                                recibos_dir=args.recibos_dir,
                                aprovacoes_path=args.aprovacoes,
                                registro_aprovacoes_path=args.registro_aprovacoes)
    except Exception as erro:  # fail-closed: falha do gate NAO e permissao
        detalhe = f"{type(erro).__name__}: {erro}"
        resposta = _resposta_de_falha(args.card, detalhe, args.recibos_dir)
        if args.resposta:
            pathlib.Path(args.resposta).write_text(
                json.dumps(resposta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"GATE JEV FALHOU card={args.card}: {detalhe}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print(json.dumps(resposta, ensure_ascii=False), flush=True)
        return 1

    if args.resposta:
        pathlib.Path(args.resposta).write_text(
            json.dumps(resposta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(resposta, ensure_ascii=False), flush=True)
    return codigo_de_saida(resposta)


if __name__ == "__main__":
    sys.exit(main())
