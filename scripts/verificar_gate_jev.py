#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do ENCAIXE do gate JEV no dispatch do board — card TRE-W0-E04-T05.

O que esta suite prova (e como), sem tocar em nada de producao:

  * o encaixe roda ANTES de reivindicar/despachar e o card retido pelo roteador
    NAO executa por caminho nenhum: nem o tick do despachante (spawn real, com um
    binario `hermes` FALSO para nao nascer worker de verdade), nem o
    `hermes kanban claim` manual;
  * card com codigo canonico declarado executa (o encaixe nao e bloqueio geral);
  * codigo PROIBIDO declarado bloqueia e marca exige_aprovacao_humana;
  * RECIBO gravado em toda consulta, com os 13 campos do contrato da politica;
  * gate quebrado (comando ausente / saida fora do contrato / timeout) NAO executa
    (fail-closed) e registra o evento no card;
  * PROVA NEGATIVA: sem o encaixe (config desligada) o MESMO card volta a
    executar;
  * PROVA NEGATIVA 2 (mutacao): com o encaixe ligado, remover a edicao do kernel
    faz o card voltar a executar — e o codigo do ponto de estrangulamento, nao a
    configuracao, que segura.

Como a suite roda o kernel REAL sem escrever em /opt/hermes (root): monta um
overlay de `hermes_cli/` num diretorio temporario (symlink para tudo, copia real
dos modulos que o encaixe edita, com as edicoes aplicadas) e roda os subprocessos
com `PYTHONPATH=<overlay>`. O board de teste vive em `HERMES_KANBAN_HOME` temporario.

Uso:  /opt/hermes/.venv/bin/python scripts/verificar_gate_jev.py [--manter]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
HERMES = pathlib.Path("/opt/hermes")
DIR_CLI = HERMES / "hermes_cli"
PY = sys.executable
GATE = RAIZ / "hermes/jev/gate/gate_jev.py"
ADAPTADOR_VERSIONADO = RAIZ / "deploy/hermes/kanban_jev_gate.py"
ADAPTADOR_INSTALADO = DIR_CLI / "kanban_jev_gate.py"
EDITOR = RAIZ / "deploy/hermes/editar_core_do_gate.py"
POLITICA = RAIZ / "hermes/jev/policy_v1.yaml"
DECLARACOES_DO_REPO = RAIZ / "hermes/jev/acoes-declaradas.yaml"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
BOARD = "gate-teste"
PERFIL = "default"
# Variaveis do worker/dispatcher que NAO podem vazar para o experimento: com
# HERMES_KANBAN_DB pinado, o board de teste cai no board de verdade.
ENV_PROIBIDO = (
    "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT",
    "HERMES_KANBAN_RUN_ID", "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_KANBAN_TASK",
    "HERMES_KANBAN_WORKSPACE", "HERMES_SESSION_ID",
)
MODULOS_COM_ENCAIXE = ("kanban_db.py", "kanban_db_dispatch.py", "kanban_ops.py")


class Itens:
    """Contador PASS/FALHOU no formato das outras suites do repo."""

    def __init__(self):
        self.total = 0
        self.falhas = []

    def checar(self, descricao: str, condicao: bool, detalhe: str = "") -> bool:
        self.total += 1
        if condicao:
            print(f"PASS {self.total:3d}  {descricao}")
            return True
        print(f"FALHOU {self.total:3d}  {descricao}" + (f"  [{detalhe}]" if detalhe else ""))
        self.falhas.append(descricao)
        return False

    def pular(self, descricao: str, detalhe: str) -> None:
        print(f"PULADO    {descricao}  [{detalhe}]")


# ---------------------------------------------------------------------------
# Overlay: kernel real + edicoes do encaixe, fora de /opt/hermes
# ---------------------------------------------------------------------------
def _carregar_editor():
    spec = importlib.util.spec_from_file_location("editar_core_do_gate", EDITOR)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def construir_overlay(destino: pathlib.Path, *, edicoes_revertidas: tuple = ()) -> pathlib.Path:
    """Cria `<destino>/hermes_cli` = copia do instalado com o encaixe aplicado.

    `edicoes_revertidas` (nomes de `EDICOES` do editor) sai SEM o encaixe — e o que
    permite a prova por mutacao.
    """
    editor = _carregar_editor()
    pacote = destino / "hermes_cli"
    pacote.mkdir(parents=True, exist_ok=True)
    editados = set()
    for nome, (arquivo, antiga, nova) in editor.EDICOES:
        if nome in edicoes_revertidas:
            continue
        editados.add(arquivo)

    for entrada in sorted(DIR_CLI.iterdir()):
        alvo = pacote / entrada.name
        if alvo.exists() or alvo.is_symlink():
            continue
        if entrada.name in editados and entrada.is_file():
            continue
        os.symlink(entrada, alvo)

    # modulos editados: copia real + edicoes aplicadas
    for arquivo in sorted(editados):
        origem = DIR_CLI / arquivo
        texto = origem.read_text(encoding="utf-8")
        for nome, (arq, antiga, nova) in editor.EDICOES:
            if arq != arquivo or nome in edicoes_revertidas:
                continue
            if nova in texto:
                continue
            if antiga not in texto:
                raise RuntimeError(f"ancora do encaixe ausente em {arquivo}: {nome}")
            texto = texto.replace(antiga, nova, 1)
        (pacote / arquivo).write_text(texto, encoding="utf-8")
    # adaptador: copia do versionado
    shutil.copy2(ADAPTADOR_VERSIONADO, pacote / "kanban_jev_gate.py")
    return pacote


# ---------------------------------------------------------------------------
# Ambiente do experimento
# ---------------------------------------------------------------------------
def ambiente(base: pathlib.Path, *, gate: str = "ligado", boards: str = BOARD,
              overlay: pathlib.Path, recibos: pathlib.Path, declaracoes: pathlib.Path,
              timeout: int = 30, hermes_bin: pathlib.Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ENV_PROIBIDO}
    env["HERMES_KANBAN_HOME"] = str(base)
    env["PYTHONPATH"] = f"{overlay}{os.pathsep}{HERMES}"
    env["HERMES_BIN"] = str(hermes_bin)
    env["HERMES_JEV_GATE_BOARDS"] = boards
    env["HERMES_JEV_GATE_TIMEOUT"] = str(timeout)
    env["JEV_RECIBOS_DIR"] = str(recibos)
    env["JEV_DECLARACOES"] = str(declaracoes)
    if gate == "ligado":
        env["HERMES_JEV_GATE"] = str(GATE)
    elif gate == "desligado":
        env["HERMES_JEV_GATE"] = "off"
    elif gate == "quebrado":
        env["HERMES_JEV_GATE"] = "/caminho/que/nao/existe/gate_jev.py"
    else:
        env["HERMES_JEV_GATE"] = gate  # caminho/comando explicito
    return env


def rodar(cmd, env, cwd=None, timeout=180):
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)


def binario_falso(destino: pathlib.Path, log: pathlib.Path) -> pathlib.Path:
    """`hermes` falso: registra a linha de comando e sai. Nenhum worker de verdade nasce."""
    caminho = destino / "hermes"
    caminho.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    caminho.chmod(caminho.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return caminho


def levantar_board(base: pathlib.Path, env: dict, recem: bool = True) -> None:
    if recem and (base / "kanban").exists():
        raise RuntimeError(f"base do board ja existe: {base}")
    r = rodar(["hermes", "kanban", "boards", "create", BOARD], env)
    if r.returncode != 0 and "already" not in (r.stdout + r.stderr).lower():
        raise RuntimeError(f"nao criei o board de teste: {r.stdout} {r.stderr}")


def criar_card(env: dict, titulo: str) -> str:
    r = rodar(["hermes", "kanban", "--board", BOARD, "create", titulo,
               "--assignee", PERFIL, "--json"], env)
    if r.returncode != 0:
        raise RuntimeError(f"nao criei o card: {r.stdout} {r.stderr}")
    dados = json.loads(r.stdout[r.stdout.index("{"):])
    return (dados.get("task") or dados)["id"]


def ler_card(base: pathlib.Path, card_id: str) -> dict:
    import sqlite3
    con = sqlite3.connect(f"file:{base}/kanban/boards/{BOARD}/kanban.db?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        linha = con.execute("SELECT * FROM tasks WHERE id = ?", (card_id,)).fetchone()
        return dict(linha) if linha else {}
    finally:
        con.close()


def eventos_do_card(base: pathlib.Path, card_id: str, kind: str) -> list:
    import sqlite3
    con = sqlite3.connect(f"file:{base}/kanban/boards/{BOARD}/kanban.db?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        linhas = con.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND kind = ? ORDER BY id",
            (card_id, kind)).fetchall()
        return [json.loads(l["payload"] or "{}") for l in linhas]
    finally:
        con.close()


def escrever_declaracoes(caminho: pathlib.Path, mapa: dict) -> None:
    linhas = ["versao: acoes-declaradas-v1", "declaracoes:"]
    if not mapa:
        linhas[-1] = "declaracoes: []"
    for card_id, codigo in mapa.items():
        linhas += [
            f"  - card_id: {card_id}",
            f"    acao_codigo: {codigo}",
            "    declarado_por: suite do encaixe",
            "    declarado_em: '2026-09-29'",
            "    motivo: caso da suite do encaixe (TRE-W0-E04-T05)",
        ]
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def recibos_em(diretorio: pathlib.Path) -> list:
    return sorted(p for p in diretorio.glob("*.json"))


def _json_do_dispatch(proc) -> dict:
    """Le o JSON do tick do despachante (o CLI imprime um objeto JSON)."""
    texto = (proc.stdout or "").strip()
    if not texto:
        return {}
    inicio = texto.find("{")
    if inicio < 0:
        return {}
    try:
        return json.loads(texto[inicio:])
    except Exception:
        return {}


def campos_do_recibo() -> list:
    import yaml
    return list(((yaml.safe_load(POLITICA.read_text(encoding="utf-8")) or {})
                 .get("recibo") or {}).get("campos") or [])


# ---------------------------------------------------------------------------
# Cenarios
# ---------------------------------------------------------------------------
class Cenario:
    """Um board temporario + ambiente montado para um caso."""

    def __init__(self, raiz: pathlib.Path, nome: str, *, gate: str = "ligado",
                 boards: str = BOARD, edicoes_revertidas: tuple = (),
                 declaracoes: dict = None, timeout: int = 30):
        self.raiz = raiz
        self.nome = nome
        self.base = raiz / nome / "home"
        self.recibos = raiz / nome / "recibos"
        self.declaracoes = raiz / nome / "acoes-declaradas.yaml"
        self.log = raiz / nome / "spawn.log"
        self.overlay = raiz / nome / "overlay"
        for d in (self.base, self.recibos, raiz / nome):
            d.mkdir(parents=True, exist_ok=True)
        self.log.write_text("", encoding="utf-8")
        construir_overlay(self.overlay, edicoes_revertidas=edicoes_revertidas)
        self.hermes_falso = binario_falso(raiz / nome, self.log)
        escrever_declaracoes(self.declaracoes, declaracoes or {})
        self.env = ambiente(self.base, gate=gate, boards=boards, overlay=self.overlay,
                            recibos=self.recibos, declaracoes=self.declaracoes,
                            timeout=timeout, hermes_bin=self.hermes_falso)

    def board(self) -> None:
        levantar_board(self.base, self.env)

    def card(self, titulo: str) -> str:
        return criar_card(self.env, titulo)

    def despachar(self, *extra) -> subprocess.CompletedProcess:
        return rodar(["hermes", "kanban", "--board", BOARD, "dispatch", "--json", *extra], self.env)

    def claim(self, card_id: str) -> subprocess.CompletedProcess:
        return rodar(["hermes", "kanban", "--board", BOARD, "claim", card_id], self.env)

    def spawnou(self, card_id: str) -> bool:
        texto = self.log.read_text(encoding="utf-8")
        return f"work kanban task {card_id}" in texto

    def linhas_de_spawn(self) -> list:
        return [l for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
def executar_suite(raiz: pathlib.Path) -> Itens:
    itens = Itens()
    campos = campos_do_recibo()
    print(f"contrato do recibo: {len(campos)} campos ({', '.join(campos)})")

    # ---- S1: dispatch com encaixe, card SEM codigo declarado ---------------
    c = Cenario(raiz, "s1", declaracoes={})
    c.board()
    card = c.card("card sem codigo canonico declarado")
    r1 = c.despachar()
    estado = ler_card(c.base, card)
    itens.checar("S1 dispatch: card sem codigo NAO sobe worker (nenhum spawn)",
                 not c.spawnou(card), f"log={c.linhas_de_spawn()}")
    itens.checar("S1 dispatch: card continua em `ready` (nao executa, nao some)",
                 estado.get("status") == "ready", f"status={estado.get('status')}")
    itens.checar("S1 dispatch: o tick reporta o card no balde nomeado `skipped_jev_gate`",
                 card in json.dumps(_json_do_dispatch(r1), ensure_ascii=False),
                 json.dumps(_json_do_dispatch(r1), ensure_ascii=False)[:240])
    rejeicoes = eventos_do_card(c.base, card, "jev_gate_rejected")
    itens.checar("S1 dispatch: evento `jev_gate_rejected` gravado com motivo e recibo",
                 bool(rejeicoes) and bool(rejeicoes[0].get("motivo"))
                 and bool(rejeicoes[0].get("receipt_path")),
                 json.dumps(rejeicoes[:1], ensure_ascii=False)[:300])
    itens.checar("S1 dispatch: recibo gravado (13 campos) mesmo na recusa",
                 bool(recibos_em(c.recibos)))

    # ---- S2: claim MANUAL com encaixe, card SEM codigo ---------------------
    c2 = Cenario(raiz, "s2", declaracoes={})
    c2.board()
    card2 = c2.card("card sem codigo, claim manual")
    r = c2.claim(card2)
    itens.checar("S2 claim manual: `hermes kanban claim` RECUSA o card retido",
                 r.returncode != 0 and ler_card(c2.base, card2).get("status") == "ready",
                 f"exit={r.returncode} status={ler_card(c2.base, card2).get('status')} "
                 f"saida={(r.stdout + r.stderr).strip()[:160]}")
    itens.checar("S2 claim manual: evento `jev_gate_rejected` gravado",
                 bool(eventos_do_card(c2.base, card2, "jev_gate_rejected")))

    # ---- S3: codigo comum declarado -> executa ----------------------------
    c3 = Cenario(raiz, "s3", declaracoes={})
    c3.board()
    card3 = c3.card("ajuste de texto no runbook")
    escrever_declaracoes(c3.declaracoes, {card3: "ajuste_de_texto"})
    c3.despachar()
    itens.checar("S3 dispatch: card COM codigo canonico comum EXECUTA (spawn acontece)",
                 c3.spawnou(card3), f"log={c3.linhas_de_spawn()}")
    itens.checar("S3 dispatch: evento `jev_gate_allowed` gravado",
                 bool(eventos_do_card(c3.base, card3, "jev_gate_allowed")),
                 json.dumps(eventos_do_card(c3.base, card3, "jev_gate_allowed")[:1], ensure_ascii=False)[:200])
    c3b = Cenario(raiz, "s3b", declaracoes={})
    c3b.board()
    card3b = c3b.card("ajuste de texto, claim manual")
    escrever_declaracoes(c3b.declaracoes, {card3b: "ajuste_de_texto"})
    r3b = c3b.claim(card3b)
    itens.checar("S3b claim manual: card COM codigo canonico e reivindicado",
                 r3b.returncode == 0 and ler_card(c3b.base, card3b).get("status") == "running",
                 f"exit={r3b.returncode} status={ler_card(c3b.base, card3b).get('status')}")

    # ---- S4: codigo PROIBIDO declarado -> bloqueia -------------------------
    c4 = Cenario(raiz, "s4", declaracoes={})
    c4.board()
    card4 = c4.card("texto que parece inocente")
    escrever_declaracoes(c4.declaracoes, {card4: "primeiro_contato_outbound"})
    c4.despachar()
    itens.checar("S4: codigo PROIBIDO declarado nao executa",
                 not c4.spawnou(card4) and ler_card(c4.base, card4).get("status") == "ready")
    ev4 = eventos_do_card(c4.base, card4, "jev_gate_rejected")
    itens.checar("S4: recibo marca outcome BLOCK e exige_aprovacao_humana",
                 bool(ev4) and ev4[0].get("outcome") == "BLOCK"
                 and ev4[0].get("exige_aprovacao_humana") is True,
                 json.dumps(ev4[:1], ensure_ascii=False)[:220])

    # ---- S5: PROVA NEGATIVA — sem o encaixe, o MESMO card executa ---------
    c5 = Cenario(raiz, "s5", gate="desligado", declaracoes={})
    c5.board()
    card5 = c5.card("card sem codigo, encaixe desligado")
    c5.despachar()
    itens.checar("S5 PROVA NEGATIVA: sem encaixe (HERMES_JEV_GATE=off) o card EXECUTA",
                 c5.spawnou(card5), f"log={c5.linhas_de_spawn()}")
    itens.checar("S5 PROVA NEGATIVA: nenhum evento do gate sem encaixe",
                 not eventos_do_card(c5.base, card5, "jev_gate_rejected"))

    # ---- S6: PROVA POR MUTACAO — sem as edicoes do kernel, executa ---------
    c6 = Cenario(raiz, "s6", declaracoes={},
                 edicoes_revertidas=("claim_task", "dispatch_lane"))
    c6.board()
    card6 = c6.card("card sem codigo, kernel sem encaixe")
    c6.despachar()
    itens.checar("S6 MUTACAO: encaixe ligado + kernel sem a edicao => card EXECUTA "
                 "(o codigo do ponto de estrangulamento e o que segura)",
                 c6.spawnou(card6), f"log={c6.linhas_de_spawn()}")

    c6b = Cenario(raiz, "s6b", declaracoes={}, edicoes_revertidas=("claim_task",))
    c6b.board()
    card6b = c6b.card("claim manual sem a edicao do claim_task")
    r6b = c6b.claim(card6b)
    itens.checar("S6b MUTACAO: revertendo SO a edicao de `claim_task`, o claim manual "
                 "volta a funcionar (a edicao e a trava do caminho manual)",
                 r6b.returncode == 0, f"exit={r6b.returncode}")
    card6b2 = c6b.card("dispatch sem a edicao do claim_task")
    r6b2 = c6b.despachar()
    itens.checar("S6b MUTACAO: com o claim_task livre, o DESPACHANTE ainda segura o card "
                 "(a edicao de `_dispatch_lane_task` vale sozinha)",
                 not c6b.spawnou(card6b2) and ler_card(c6b.base, card6b2).get("status") == "ready",
                 f"log={c6b.linhas_de_spawn()}")
    itens.checar("S6b: o tick reporta o card no balde nomeado `skipped_jev_gate`",
                 card6b2 in json.dumps(_json_do_dispatch(r6b2), ensure_ascii=False),
                 json.dumps(_json_do_dispatch(r6b2), ensure_ascii=False)[:220])

    c6c = Cenario(raiz, "s6c", declaracoes={}, edicoes_revertidas=("dispatch_lane",))
    c6c.board()
    card6c = c6c.card("claim manual sem a edicao do despachante")
    r6c = c6c.claim(card6c)
    itens.checar("S6c MUTACAO: revertendo SO a edicao do despachante, o claim manual "
                 "continua RECUSADO (a edicao de `claim_task` vale sozinha)",
                 r6c.returncode != 0 and ler_card(c6c.base, card6c).get("status") == "ready",
                 f"exit={r6c.returncode} status={ler_card(c6c.base, card6c).get('status')}")

    # ---- S7: gate quebrado / fora do contrato / timeout => fail-closed -----
    c7 = Cenario(raiz, "s7", gate="quebrado", declaracoes={})
    c7.board()
    card7 = c7.card("card com gate apontando para comando inexistente")
    c7.despachar()
    ev7 = eventos_do_card(c7.base, card7, "jev_gate_rejected")
    itens.checar("S7 fail-closed: comando do gate inexistente NAO executa",
                 not c7.spawnou(card7) and bool(ev7),
                 json.dumps(ev7[:1], ensure_ascii=False)[:200])
    itens.checar("S7 fail-closed: outcome GATE_INDISPONIVEL registrado",
                 bool(ev7) and ev7[0].get("outcome") == "GATE_INDISPONIVEL",
                 json.dumps(ev7[:1], ensure_ascii=False)[:200])

    lixo = raiz / "gate_lixo.py"
    lixo.write_text("print('nao escrevo resposta nenhuma')\n", encoding="utf-8")
    c7b = Cenario(raiz, "s7b", gate=str(lixo), declaracoes={})
    c7b.board()
    card7b = c7b.card("card com gate que nao responde no contrato")
    c7b.despachar()
    ev7b = eventos_do_card(c7b.base, card7b, "jev_gate_rejected")
    itens.checar("S7b fail-closed: saida fora do contrato NAO executa",
                 not c7b.spawnou(card7b) and bool(ev7b)
                 and ev7b[0].get("outcome") == "GATE_INDISPONIVEL",
                 json.dumps(ev7b[:1], ensure_ascii=False)[:220])

    dorminhoco = raiz / "gate_dorminhoco.py"
    dorminhoco.write_text("import time\ntime.sleep(8)\n", encoding="utf-8")
    c7c = Cenario(raiz, "s7c", gate=str(dorminhoco), declaracoes={}, timeout=1)
    c7c.board()
    card7c = c7c.card("card com gate que nao responde no tempo")
    c7c.despachar()
    ev7c = eventos_do_card(c7c.base, card7c, "jev_gate_rejected")
    itens.checar("S7c fail-closed: timeout do gate (1s) NAO executa — abstencao nao e permissao",
                 not c7c.spawnou(card7c) and bool(ev7c) and ev7c[0].get("outcome") == "GATE_TIMEOUT",
                 json.dumps(ev7c[:1], ensure_ascii=False)[:220])

    # ---- S8: escopo — fora do board do escopo o encaixe e inerte -----------
    c8 = Cenario(raiz, "s8", boards="outro-board-qualquer", declaracoes={})
    c8.board()
    card8 = c8.card("card aprovado mas fora do escopo do gate")
    c8.despachar()
    itens.checar("S8 escopo: fora de `jev_gate_boards` o encaixe NAO interfere (card executa)",
                 c8.spawnou(card8) and not eventos_do_card(c8.base, card8, "jev_gate_rejected"),
                 f"log={c8.linhas_de_spawn()}")

    # ---- S9: recibo — sempre, 13 campos, sem segredo -----------------------
    # A EXPECTATIVA DESTE BLOCO NAO E UM NUMERO FIXO: ela e derivada da propria
    # enumeracao abaixo. Cenario obrigatorio de SUCESSO do encaixe (o gate tomou
    # decisao) TEM de deixar o SEU recibo de decisao com os 13 campos do contrato
    # da politica; cenario de FALHA do encaixe (o gate nao decidiu nada) TEM de
    # deixar registro explicito `FALHA-DO-GATE.json` marcado com
    # `nao_e_recibo_de_decisao`. Um numero fixo aqui envelhece na primeira
    # mudanca de cenario — foi o defeito TRE-W0-E04-T05-D01: o item exigia
    # `verificados >= 6` sobre uma enumeracao que so pode produzir 5 recibos de
    # decisao (8 cenarios, 3 deles de falha do encaixe). Cada cenario de sucesso
    # sem o seu recibo continua reprovando o item: nada foi afrouxado.
    #
    # Cuidado MEDIDO (sonda em 30/09/2026, nao suposto): o numero de ARQUIVOS por
    # cenario NAO e estavel. `decision_id` embute timestamp com resolucao de
    # SEGUNDO e o caminho liberado passa pelos DOIS pontos de estrangulamento
    # (dispatch e o claim interno), entao um MESMO card deixa 2 recibos quando a
    # virada do segundo cai entre as duas consultas (medido: 2 arquivos com
    # timestamps 1s apartado e 2 eventos `jev_gate_allowed`); e card retido em
    # `ready` deixa 1 recibo POR TICK. Por isso o item nao confere CONTAGEM de
    # arquivos — o que ele confere e que cada cenario de SUCESSO obrigatorio tem
    # o SEU recibo e que todo recibo encontrado tem os 13 campos do contrato.
    CENARIOS_COM_RECIBO = (
        ("S1 dispatch recusa", c), ("S2 claim manual recusa", c2), ("S3 dispatch libera", c3),
        ("S3b claim manual libera", c3b), ("S4 codigo proibido", c4),
    )
    CENARIOS_DE_FALHA_DO_ENCAIXE = (
        ("S7 gate indisponivel", c7), ("S7b saida fora do contrato", c7b),
        ("S7c timeout", c7c),
    )
    obrigatorios = CENARIOS_COM_RECIBO + CENARIOS_DE_FALHA_DO_ENCAIXE
    esperados = len(CENARIOS_COM_RECIBO)
    com_recibo = {rotulo for rotulo, _ in CENARIOS_COM_RECIBO}

    contrato_ok, sem_segredo = True, True
    verificados = 0
    faltando_registro: list = []
    sem_recibo_de_decisao: list = []
    for rotulo, cenario in obrigatorios:
        arquivos = recibos_em(cenario.recibos)
        if not arquivos:
            faltando_registro.append(rotulo)
        recebeu_decisao = 0
        for arquivo in arquivos:
            texto = arquivo.read_text(encoding="utf-8")
            dados = json.loads(texto)
            if arquivo.name.endswith("FALHA-DO-GATE.json"):
                # falha do encaixe: registro explicito, NAO um recibo de decisao
                if "nao_e_recibo_de_decisao" not in texto:
                    contrato_ok = False
                    print(f"      registro de falha sem a marca de que nao e recibo: {arquivo.name}")
                elif rotulo in com_recibo:
                    # cenario de sucesso que so deixou falha do encaixe: a decisao
                    # que deveria ter sido tomada (e registrada) nao foi
                    contrato_ok = False
                    print(f"      cenario de sucesso so deixou falha do encaixe: {rotulo} -> {arquivo.name}")
                continue
            # recibo de DECISAO: so cenario de sucesso pode ter — e tem de ter
            if rotulo not in com_recibo:
                contrato_ok = False
                print(f"      cenario de falha do encaixe deixou recibo de decisao: {rotulo} -> {arquivo.name}")
                continue
            recebeu_decisao += 1
            verificados += 1
            if sorted(dados) != sorted(campos):
                contrato_ok = False
                print(f"      recibo fora do contrato: {arquivo.name} -> {sorted(dados)}")
            if any(padrao in texto.lower()
                   for padrao in ("api_key", "ghp_", "password", "secret", "token=")):
                sem_segredo = False
        if rotulo in com_recibo and recebeu_decisao == 0:
            sem_recibo_de_decisao.append(rotulo)
    itens.checar(f"S9 recibo: consulta ao gate SEMPRE deixa registro (recibo de 13 campos "
                 f"ou registro explicito de falha do encaixe), nos {len(obrigatorios)} cenarios",
                 not faltando_registro, f"sem registro em: {faltando_registro}")
    itens.checar(f"S9 recibo: os {esperados} cenarios obrigatorios de SUCESSO deixam, cada um, "
                 f"o seu recibo com os 13 campos exatos do contrato da politica "
                 f"(nenhum campo a mais, nenhum a menos)",
                 contrato_ok and not sem_recibo_de_decisao,
                 f"com recibo de decisao={esperados - len(sem_recibo_de_decisao)}/{esperados}, "
                 f"arquivos conferidos={verificados}, contrato_ok={contrato_ok}, "
                 f"sem recibo em: {sem_recibo_de_decisao}")
    itens.checar("S9 recibo: nenhum segredo no recibo", sem_segredo)

    # ---- S10: espelhos (adaptador instalado x versionado; codigos) ---------
    if ADAPTADOR_INSTALADO.is_file():
        itens.checar("S10 adaptador instalado em /opt/hermes == versionado no repo",
                     ADAPTADOR_INSTALADO.read_bytes() == ADAPTADOR_VERSIONADO.read_bytes())
    else:
        itens.pular("S10 adaptador instalado em /opt/hermes == versionado no repo",
                    f"ainda nao instalado ({ADAPTADOR_INSTALADO}) — precisa de root; "
                    "ver deploy/hermes/aplicar_gate_jev.sh")
    try:
        import yaml
        espec = importlib.util.spec_from_file_location("rot_gate", ROTEADOR)
        rot = importlib.util.module_from_spec(espec)
        espec.loader.exec_module(rot)
        espelho = list((yaml.safe_load(DECLARACOES_DO_REPO.read_text(encoding="utf-8")) or {})
                       .get("codigos_validos") or [])
        itens.checar("S10 `codigos_validos` do acoes-declaradas.yaml == CODIGOS_DE_ACAO_COMUNS do roteador",
                     espelho == list(rot.CODIGOS_DE_ACAO_COMUNS),
                     f"yaml={espelho} roteador={list(rot.CODIGOS_DE_ACAO_COMUNS)}")
    except Exception as erro:  # pragma: no cover
        itens.checar("S10 espelho dos codigos comuns", False, f"{type(erro).__name__}: {erro}")

    return itens


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Suite do encaixe do gate JEV (TRE-W0-E04-T05).")
    parser.add_argument("--manter", action="store_true", help="nao apaga o diretorio temporario")
    args = parser.parse_args(argv)

    raiz = pathlib.Path(tempfile.mkdtemp(prefix="gate-jev-"))
    print(f"diretorio da suite: {raiz}")
    try:
        itens = executar_suite(raiz)
    finally:
        if not args.manter:
            shutil.rmtree(raiz, ignore_errors=True)
        else:
            print(f"(mantido: {raiz})")

    if itens.falhas:
        print(f"\nFALHOU ({itens.total} itens, {len(itens.falhas)} falhas)")
        for f in itens.falhas:
            print(f"  - {f}")
        return 1
    print(f"\nPASS ({itens.total} itens, 0 falhas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
