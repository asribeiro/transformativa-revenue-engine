#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do ENCAIXE ARQUIVO QUENTE no despacho do board — card TRE-W3-E01-T03-D01.

O defeito que esta suite mede (e a correcao que ela prova)
----------------------------------------------------------
Medido em 02/10/2026: o despacho promoveu dois cards IRMAOS no mesmo tick
(`TRE-W3-E01-T02` e `TRE-W3-E01-T03`) que editam OS MESMOS arquivos; o `git status`
do worktree do T02 listava os 7 arquivos que o T03 obrigatoriamente toca. Os dois
estavam "certos" e o resultado foi divergencia no vocabulario da politica da API
(`campos_de_identidade` em lista x `campo_de_identidade` singular). O promotor
decidia por dependencia e prioridade e nao olhava ARQUIVO.

O que esta suite prova (e como), sem tocar em nada de producao:

  * DEFEITO (prova negativa): encaixe desligado, dois cards que declaram o MESMO
    arquivo quente sobem OS DOIS no mesmo tick — o quadro medido;
  * CORRECAO: com o encaixe ligado, o segundo NAO sobe; fica `ready` (parkeado, nao
    perdido), o tick o reporta no balde nomeado `skipped_hotspot` e o card recebe o
    evento `hotspot_wait` com detentor e arquivo;
  * NAO E DEADLOCK: quando o detentor fecha (`complete`), o tick seguinte promove o
    parqueado. Com tres irmaos no mesmo arquivo, sobe UM por vez;
  * SEM FALSO POSITIVO: card sem declaracao, card com outro arquivo e card sem
    declaracao nenhuma (prosa que apenas MENCIONA a palavra "hotspot" — os corpos
    reais do lote) nao sao parkeados;
  * ESCOPO: fora dos boards de `kanban.hotspot_gate_boards` nada e parkeado;
  * CAMINHO UNICO: o `hermes kanban claim` manual tambem e recusado (o encaixe esta
    no ponto de estrangulamento por onde passam TODOS os caminhos de execucao);
  * PROVA POR MUTACAO: encaixe ligado com as edicoes do kernel desfeitas => os dois
    sobem de novo (o codigo do ponto de estrangulamento e a trava, nao a config);
  * VERIFICACAO NAO ALTERA O RUNTIME: `--check` do editor nao escreve em /opt/hermes.

Como a suite roda o kernel REAL sem escrever em /opt/hermes (root): monta um
overlay de `hermes_cli/` em diretorio temporario (symlink para o que a suite nao
toca, copia real dos modulos editados e do adaptador, com as edicoes APLICADAS — ou
DESFEITAS, na prova por mutacao) e roda os subprocessos com `PYTHONPATH=<overlay>`.
O board de teste vive em `HERMES_KANBAN_HOME` temporario e o `hermes` que o
despachante spawna e um binario FALSO que dorme (worker VIVO de verdade nenhum) —
dormir e o que mantem o claim do detentor vivo entre dois ticks, como no defeito.

Uso:  /opt/hermes/.venv/bin/python scripts/verificar_hotspot_gate.py [--manter]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# Raiz do Hermes a sobrepor. Parametrizavel para a suite NUNCA precisar escrever no
# runtime instalado (root) — a verificacao nao altera o runtime que ela mede.
HERMES = pathlib.Path(os.environ.get("HOTSPOT_HERMES_HOME") or "/opt/hermes")
DIR_CLI = HERMES / "hermes_cli"
PY = sys.executable
ADAPTADOR_VERSIONADO = RAIZ / "deploy/hermes/kanban_hotspot_gate.py"
ADAPTADOR_INSTALADO = DIR_CLI / "kanban_hotspot_gate.py"
EDITOR = RAIZ / "deploy/hermes/editar_core_do_hotspot.py"
BOARD = "hotspot-teste"
OUTRO_BOARD = "outro-board"
PERFIL = "default"
# Board real do TRE: usado para ler os CORPOS MEDIDOS do lote (prova de que prosa
# que menciona "hotspot" nao vira declaracao). Leitura read-only; ausente => PULADO.
BOARD_REAL_DB = pathlib.Path(
    os.environ.get("TRE_KANBAN_DB")
    or "/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")
CARDS_MEDIDOS = ("t_8b2ed1b7", "t_cdc21b43", "t_cb615018", "t_de461d14", "t_6c8ad8bb")
# Variaveis do worker/dispatcher que NAO podem vazar para o experimento: com
# HERMES_KANBAN_DB pinado, o board de teste cai no board de verdade.
ENV_PROIBIDO = (
    "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT",
    "HERMES_KANBAN_RUN_ID", "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_KANBAN_TASK",
    "HERMES_KANBAN_WORKSPACE", "HERMES_SESSION_ID", "HERMES_HOTSPOT_GATE",
    "HERMES_HOTSPOT_GATE_BOARDS",
)
MODULOS_COM_ENCAIXE = ("kanban_db.py", "kanban_db_dispatch.py", "kanban_ops.py")
ARQUIVO_QUENTE = "api/motor.py"
ARQUIVO_QUENTE_2 = "api/politica_api.json"
OUTRO_ARQUIVO = "docs/runbooks/odoo-api-controlada.md"

# Prosa MEDIDA nos corpos reais do lote (W2/W3): a palavra "hotspot" aparece, o
# marcador `HOTSPOT:` (com caminho) NAO. Nenhum destes pode virar declaracao.
PROSA_MEDIDA = (
    "**CAUSA RAIZ:** o promotor da onda decide por **dependencia e prioridade**, mas nao "
    "tem **mapa de hotspot de arquivo**. Dois cards que escrevem nos mesmos arquivos sobem juntos.",
    "- arquivo unico em jogo: `scripts/odoo/verificar-modulo-odoo.sh` (hotspot: D01/D02/D03/D04 "
    "editam este arquivo na mesma base `fe26aa5`; a regiao quente do merge e o bloco do "
    "`--prova-de-dente`, D02 x D03);",
    "HOTSPOT - fazer UMA vez, no ponto de integracao: os cards paralelos `TRE-W2-E04-T02` "
    "(`crm.lead`) e `TRE-W2-E05-T01` (`tf.process.opportunity`) acrescentam artefatos ao MESMO "
    "modulo e vao querer o mesmo arquivo `scripts/verificar_estrutura.sh`",
    "**APRENDIZADO:** promocao de onda precisa olhar **arquivo**, nao so dependencia.",
    "2. **Correcao do promotor** (a deste card): mapa de hotspot declarado por arquivo, no maximo "
    "um card por arquivo quente em voo por vez.",
)


class Itens:
    """Contador PASS/FALHOU no formato das outras suites do repo."""

    def __init__(self):
        self.total = 0
        self.falhas = []
        self.pulados = 0

    def checar(self, descricao: str, condicao: bool, detalhe: str = "") -> bool:
        self.total += 1
        if condicao:
            print(f"PASS {self.total:3d}  {descricao}")
            return True
        print(f"FALHOU {self.total:3d}  {descricao}" + (f"  [{detalhe}]" if detalhe else ""))
        self.falhas.append(descricao)
        return False

    def pular(self, descricao: str, detalhe: str) -> None:
        self.pulados += 1
        print(f"PULADO    {descricao}  [{detalhe}]")


# ---------------------------------------------------------------------------
# Corpo de card com declaracao de arquivo quente
# ---------------------------------------------------------------------------
def corpo(titulo: str, arquivos, *, forma: str = "inline", extra: str = "") -> str:
    linhas = [f"**{titulo}**", "", "CONTATO: politica da API (`api/politica_api.json`).", ""]
    if arquivos:
        if forma == "inline":
            linhas += ["HOTSPOT: " + ", ".join(arquivos), ""]
        else:
            linhas += ["**HOTSPOT:**"] + [f"- `{a}`" for a in arquivos] + [""]
    linhas += [extra or "ACEITE: a escrita declara identidade e valor fixo da politica da API.", ""]
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Overlay: kernel real + edicoes do encaixe, fora de /opt/hermes
# ---------------------------------------------------------------------------
def _carregar_editor():
    spec = importlib.util.spec_from_file_location("editar_core_do_hotspot", EDITOR)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def construir_overlay(destino: pathlib.Path, *, edicoes_revertidas: tuple = ()) -> pathlib.Path:
    """Cria `<destino>/hermes_cli` = copia do instalado com o encaixe aplicado.

    `edicoes_revertidas` (nomes de `EDICOES` do editor) sai SEM o encaixe naquele
    ponto — e a prova por mutacao. Todo modulo tocado por alguma edicao vira COPIA
    REAL (nunca symlink): com symlink, o `open(dst,'wb')` seguiria o link e a suite
    escreveria no arquivo INSTALADO.
    """
    editor = _carregar_editor()
    pacote = destino / "hermes_cli"
    pacote.mkdir(parents=True, exist_ok=True)
    editados = {arquivo for _, (arquivo, _, _) in editor.EDICOES}
    nomes_que_viram_copia = editados | {ADAPTADOR_VERSIONADO.name}
    for entrada in sorted(DIR_CLI.iterdir()):
        alvo = pacote / entrada.name
        if alvo.exists() or alvo.is_symlink():
            continue
        if entrada.name in nomes_que_viram_copia and entrada.is_file():
            continue
        os.symlink(entrada, alvo)

    for arquivo in sorted(editados):
        texto = (DIR_CLI / arquivo).read_text(encoding="utf-8")
        for nome, (arq, antiga, nova) in editor.EDICOES:
            if arq != arquivo:
                continue
            if nome in edicoes_revertidas:
                if nova in texto:
                    texto = texto.replace(nova, antiga, 1)
                continue
            if nova in texto:
                continue
            if antiga not in texto:
                raise RuntimeError(f"ancora do encaixe ausente em {arquivo}: {nome}")
            texto = texto.replace(antiga, nova, 1)
        (pacote / arquivo).write_text(texto, encoding="utf-8")
    shutil.copy2(ADAPTADOR_VERSIONADO, pacote / "kanban_hotspot_gate.py")
    return pacote


# ---------------------------------------------------------------------------
# Ambiente do experimento
# ---------------------------------------------------------------------------
def ambiente(base: pathlib.Path, *, encaixe: str, boards: str, overlay: pathlib.Path,
             hermes_bin: pathlib.Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ENV_PROIBIDO}
    env["HERMES_KANBAN_HOME"] = str(base)
    env["PYTHONPATH"] = f"{overlay}{os.pathsep}{HERMES}"
    env["HERMES_BIN"] = str(hermes_bin)
    # O gate JEV nao participa desta medicao (o encaixe medido e o de arquivo quente).
    env["HERMES_JEV_GATE"] = "off"
    env["HERMES_HOTSPOT_GATE_BOARDS"] = boards
    if encaixe == "ligado":
        env["HERMES_HOTSPOT_GATE"] = "on"
    else:
        env["HERMES_HOTSPOT_GATE"] = "off"
    return env


def rodar(cmd, env, cwd=None, timeout=180):
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)


def binario_falso(destino: pathlib.Path, log: pathlib.Path, pids: pathlib.Path) -> pathlib.Path:
    """`hermes` falso: registra a linha de comando e DORME.

    Dormir (em vez de sair) e o que mantem o PID do worker VIVO entre dois ticks: o
    detentor continua EM VOO, como o worker real que estaba editando os arquivos
    quando o defeito foi medido. Nenhum worker de verdade nasce.

    O PID vai para um arquivo ANTES do `exec sleep`: o `exec` mantem o PID, e o
    arquivo e o que permite encerrar TODOS os falsos workers no fim — inclusive os
    de cards que fecharam (`complete` limpa `worker_pid` da linha do board, entao a
    tabela nao serve como registro do que foi spawnado).
    """
    caminho = destino / "hermes"
    caminho.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        f"printf '%s\\n' \"$$\" >> {pids}\n"
        "exec sleep 900\n",
        encoding="utf-8",
    )
    caminho.chmod(caminho.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return caminho


def _json_do_dispatch(proc) -> dict:
    texto = (proc.stdout or "").strip()
    inicio = texto.find("{")
    if inicio < 0:
        return {}
    try:
        return json.loads(texto[inicio:])
    except Exception:
        return {}


class Cenario:
    """Um board temporario + ambiente montado para um caso."""

    def __init__(self, raiz: pathlib.Path, nome: str, *, encaixe: str = "ligado",
                 boards: str = BOARD, edicoes_revertidas: tuple = ()):
        self.raiz = raiz
        self.nome = nome
        self.base = raiz / nome / "home"
        self.log = raiz / nome / "spawn.log"
        self.pids = raiz / nome / "worker.pids"
        self.overlay = raiz / nome / "overlay"
        for d in (self.base, raiz / nome):
            d.mkdir(parents=True, exist_ok=True)
        self.log.write_text("", encoding="utf-8")
        self.pids.write_text("", encoding="utf-8")
        construir_overlay(self.overlay, edicoes_revertidas=edicoes_revertidas)
        self.hermes_falso = binario_falso(raiz / nome, self.log, self.pids)
        self.env = ambiente(self.base, encaixe=encaixe, boards=boards,
                            overlay=self.overlay, hermes_bin=self.hermes_falso)

    # -- board -------------------------------------------------------------
    @property
    def db(self) -> pathlib.Path:
        return self.base / "kanban" / "boards" / BOARD / "kanban.db"

    def board(self) -> None:
        r = rodar(["hermes", "kanban", "boards", "create", BOARD], self.env)
        if r.returncode != 0 and "already" not in (r.stdout + r.stderr).lower():
            raise RuntimeError(f"nao criei o board de teste: {r.stdout} {r.stderr}")

    def card(self, titulo: str, arquivos=None, *, forma: str = "inline", extra: str = "",
             corpo_pronto: str = None) -> str:
        caminho = self.raiz / self.nome / f"corpo-{int(time.time() * 1000)}-{abs(hash(titulo))}.md"
        caminho.write_text(corpo_pronto if corpo_pronto is not None
                           else corpo(titulo, arquivos, forma=forma, extra=extra),
                           encoding="utf-8")
        r = rodar(["hermes", "kanban", "--board", BOARD, "create", titulo,
                   "--assignee", PERFIL, "--body-file", str(caminho), "--json"], self.env)
        if r.returncode != 0:
            raise RuntimeError(f"nao criei o card: {r.stdout} {r.stderr}")
        dados = json.loads(r.stdout[r.stdout.index("{"):])
        return (dados.get("task") or dados)["id"]

    # -- acoes -------------------------------------------------------------
    def despachar(self, *extra):
        return rodar(["hermes", "kanban", "--board", BOARD, "dispatch", "--json",
                      "--max", "8", *extra], self.env)

    def claim(self, card_id: str):
        return rodar(["hermes", "kanban", "--board", BOARD, "claim", card_id], self.env)

    def completar(self, card_id: str):
        return rodar(["hermes", "kanban", "--board", BOARD, "complete", card_id,
                      "--force", "--summary", "fechado pela suite do encaixe"], self.env)

    def revisar(self, card_id: str):
        return rodar(["hermes", "kanban", "--board", BOARD, "request-review", card_id,
                      "--force", "--summary", "revisao pela suite do encaixe"], self.env)

    # -- leitura -----------------------------------------------------------
    def _con(self):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def ler_card(self, card_id: str) -> dict:
        con = self._con()
        try:
            linha = con.execute("SELECT * FROM tasks WHERE id = ?", (card_id,)).fetchone()
            return dict(linha) if linha else {}
        finally:
            con.close()

    def status(self, card_id: str) -> str:
        return str(self.ler_card(card_id).get("status") or "")

    def eventos(self, card_id: str, kind: str) -> list:
        con = self._con()
        try:
            linhas = con.execute(
                "SELECT payload FROM task_events WHERE task_id = ? AND kind = ? ORDER BY id",
                (card_id, kind)).fetchall()
            return [json.loads(l["payload"] or "{}") for l in linhas]
        finally:
            con.close()

    def spawnou(self, card_id: str) -> bool:
        return f"work kanban task {card_id}" in self.log.read_text(encoding="utf-8")

    def linhas_de_spawn(self) -> list:
        return [l for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]

    def vivos(self) -> list:
        """PIDs dos falsos workers deste cenario (arquivo + linhas do board).

        O arquivo e a fonte principal: `complete`/`request-review` limpam
        `worker_pid` da linha do board, entao a tabela nao registra tudo o que foi
        spawnado. A tabela entra como reforco (worker ainda em `running`).
        """
        pids = []
        try:
            for linha in self.pids.read_text(encoding="utf-8").splitlines():
                if linha.strip().isdigit():
                    pids.append(int(linha.strip()))
        except OSError:
            pass
        con = self._con()
        try:
            pids += [int(l["worker_pid"]) for l in con.execute(
                "SELECT worker_pid FROM tasks WHERE worker_pid IS NOT NULL").fetchall()]
        finally:
            con.close()
        return sorted(set(pids))

    def encerrar(self) -> None:
        """Mata TODOS os falsos workers (sleep) spawnados neste cenario.

        O laudo de uso real e o teste que nao deixa processo para tras: sem isto a
        suite deixa dezenas de `sleep 900` orfaos no host a cada rodada.
        """
        for pid in self.vivos():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


# ---------------------------------------------------------------------------
def executar_suite(raiz: pathlib.Path) -> Itens:
    itens = Itens()
    cenarios = []
    try:
        _declaracao(itens)
        _defeito_reproduzido(itens, raiz, cenarios)
        _correcao(itens, raiz, cenarios)
        _escopo(itens, raiz, cenarios)
        _caminhos_de_execucao(itens, raiz, cenarios)
        _instalacao(itens, raiz)
    finally:
        for c in cenarios:
            try:
                c.encerrar()
            except Exception:
                pass
    return itens


# ---------------------------------------------------------------------------
# A) A DECLARACAO: o que liga e o que NAO liga o encaixe
# ---------------------------------------------------------------------------
def _declaracao(itens: Itens) -> None:
    spec = importlib.util.spec_from_file_location("hotspot_gate", ADAPTADOR_VERSIONADO)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    arquivos, ignorados = gate.declarar_hotspot(corpo("T02", [ARQUIVO_QUENTE, ARQUIVO_QUENTE_2]))
    itens.checar("A1 forma inline `HOTSPOT: a, b` vira 2 arquivos declarados",
                 arquivos == [ARQUIVO_QUENTE, ARQUIVO_QUENTE_2] and not ignorados,
                 f"{arquivos} ignorados={ignorados}")

    arquivos, _ = gate.declarar_hotspot(corpo("T03", [ARQUIVO_QUENTE], forma="lista"))
    itens.checar("A2 forma em lista (`**HOTSPOT:**` + itens com crase) vira 1 arquivo",
                 arquivos == [ARQUIVO_QUENTE], f"{arquivos}")

    arquivos, _ = gate.declarar_hotspot(corpo("x", ["./api//motor.py"]))
    itens.checar("A3 normalizacao: `./api//motor.py` == `api/motor.py`",
                 arquivos == [ARQUIVO_QUENTE], f"{arquivos}")

    arquivos, ignorados = gate.declarar_hotspot(corpo("x", [], extra="HOTSPOT: nenhum"))
    itens.checar("A4 token que nao e caminho vai para `ignorados`, nao para `arquivos`",
                 arquivos == [] and ignorados == ["nenhum"], f"{arquivos} {ignorados}")

    itens.checar("A5 diretorio declarado (`.../api/`) casa arquivo abaixo dele",
                 gate.casa("odoo/addons/x/api/", "odoo/addons/x/api/motor.py")
                 and gate.casa("odoo/addons/x/api/motor.py", "odoo/addons/x/api/")
                 and not gate.casa("odoo/addons/x/api/", "odoo/addons/x/models/motor.py"))

    falsos = []
    for prosa in PROSA_MEDIDA:
        arquivos, _ = gate.declarar_hotspot(prosa)
        if arquivos:
            falsos.append((prosa[:60], arquivos))
    itens.checar("A6 PROSA medida que so MENCIONA \"hotspot\" NAO vira declaracao (5 amostras)",
                 not falsos, f"{falsos}")

    if BOARD_REAL_DB.is_file():
        con = sqlite3.connect(f"file:{BOARD_REAL_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        medidos, com_declaracao = 0, []
        try:
            for card_id in CARDS_MEDIDOS:
                linha = con.execute("SELECT body FROM tasks WHERE id = ?", (card_id,)).fetchone()
                if linha is None:
                    continue
                medidos += 1
                arquivos, _ = gate.declarar_hotspot(linha["body"])
                if arquivos:
                    com_declaracao.append((card_id, arquivos))
        finally:
            con.close()
        itens.checar(
            f"A7 corpos REAIS do lote lidos do board ({medidos} cards): nenhum ganha declaracao "
            "por engano (o encaixe nao liga sozinho em card antigo)",
            medidos > 0 and not com_declaracao, f"declararam={com_declaracao}")
    else:
        itens.pular("A7 corpos reais do lote", f"board real ausente ({BOARD_REAL_DB})")


# ---------------------------------------------------------------------------
# B) O DEFEITO, REPRODUZIDO no board de verdade (encaixe DESLIGADO)
# ---------------------------------------------------------------------------
def _defeito_reproduzido(itens: Itens, raiz: pathlib.Path, cenarios: list) -> None:
    c = Cenario(raiz, "b1-defeito", encaixe="desligado", boards=BOARD)
    cenarios.append(c)
    c.board()
    t02 = c.card("TRE-W3-E01-T02 — company upsert", [ARQUIVO_QUENTE, ARQUIVO_QUENTE_2])
    t03 = c.card("TRE-W3-E01-T03 — contact upsert", [ARQUIVO_QUENTE])
    r = c.despachar()
    d = _json_do_dispatch(r)
    itens.checar("B1 DEFEITO (encaixe desligado): os DOIS irmaos sobem no MESMO tick",
                 c.spawnou(t02) and c.spawnou(t03),
                 f"spawn={c.linhas_de_spawn()} json={json.dumps(d, ensure_ascii=False)[:200]}")
    itens.checar("B2 DEFEITO: os dois ficam EM VOO ao mesmo tempo, nos mesmos arquivos",
                 c.status(t02) == "running" and c.status(t03) == "running",
                 f"{t02}={c.status(t02)} {t03}={c.status(t03)}")
    itens.checar("B3 DEFEITO: o tick nao tem balde nenhum para esse caso (nenhum `skipped_hotspot`)",
                 not d.get("skipped_hotspot"), json.dumps(d.get("skipped_hotspot"), ensure_ascii=False))


# ---------------------------------------------------------------------------
# C) A CORRECAO (encaixe LIGADO): um por arquivo quente em voo, sem deadlock
# ---------------------------------------------------------------------------
def _correcao(itens: Itens, raiz: pathlib.Path, cenarios: list) -> None:
    c = Cenario(raiz, "c1-correcao")
    cenarios.append(c)
    c.board()
    t02 = c.card("TRE-W3-E01-T02 — company upsert", [ARQUIVO_QUENTE, ARQUIVO_QUENTE_2])
    t03 = c.card("TRE-W3-E01-T03 — contact upsert", [ARQUIVO_QUENTE],
                 extra="O T03 toca `api/motor.py` — declarado acima.")
    r1 = c.despachar()
    d1 = _json_do_dispatch(r1)
    itens.checar("C1 encaixe ligado: o PRIMEIRO card sobe (spawn + running)",
                 c.spawnou(t02) and c.status(t02) == "running",
                 f"status={c.status(t02)} log={c.linhas_de_spawn()}")
    itens.checar("C2 encaixe ligado: o SEGUNDO nao sobe (nenhum spawn)",
                 not c.spawnou(t03), f"log={c.linhas_de_spawn()}")
    itens.checar("C3 o parqueado continua `ready` — parkeado, nao perdido nem bloqueado",
                 c.status(t03) == "ready", f"status={c.status(t03)}")
    balde = d1.get("skipped_hotspot") or []
    itens.checar("C4 o tick reporta o parqueado no balde nomeado `skipped_hotspot`",
                 any(x.get("task_id") == t03 for x in balde),
                 json.dumps(balde, ensure_ascii=False)[:240])
    itens.checar("C5 o motivo do balde nomeia o DETENTOR e o ARQUIVO",
                 any(t02 in str(x.get("motivo")) and ARQUIVO_QUENTE in str(x.get("motivo"))
                     for x in balde),
                 json.dumps(balde, ensure_ascii=False)[:240])
    eventos = c.eventos(t03, "hotspot_wait")
    itens.checar("C6 o card parqueado recebe o evento `hotspot_wait` com detentor e arquivo",
                 bool(eventos) and eventos[0].get("detentores", [{}])[0].get("task_id") == t02
                 and ARQUIVO_QUENTE in (eventos[0].get("arquivos") or []),
                 json.dumps(eventos[:1], ensure_ascii=False)[:300])

    c.despachar()
    itens.checar("C7 o card CONTINUA sem subir no tick seguinte (nao ha promocao por espera)",
                 not c.spawnou(t03) and c.status(t03) == "ready")
    itens.checar("C8 o evento nao se repete a cada tick (uma vez por espera distinta)",
                 len(c.eventos(t03, "hotspot_wait")) == 1,
                 f"eventos={len(c.eventos(t03, 'hotspot_wait'))}")

    c.completar(t02)
    itens.checar("C9 o detentor fecha (`complete`)", c.status(t02) == "done", f"status={c.status(t02)}")
    c.despachar()
    itens.checar("C10 LIBERA: o parqueado sobe no tick seguinte ao fechamento (sem deadlock)",
                 c.spawnou(t03) and c.status(t03) == "running",
                 f"status={c.status(t03)} log={c.linhas_de_spawn()}")

    # Tres irmaos no mesmo arquivo: UM por vez.
    c3 = Cenario(raiz, "c2-tres-irmaos")
    cenarios.append(c3)
    c3.board()
    a = c3.card("TRE-W3-E01-T02 — company upsert", [ARQUIVO_QUENTE])
    b = c3.card("TRE-W3-E01-T04 — opportunity upsert", [ARQUIVO_QUENTE])
    d = c3.card("TRE-W3-E01-T05 — activity create", [ARQUIVO_QUENTE])
    c3.despachar()
    itens.checar("C11 tres irmaos no mesmo arquivo: sobe UM e os outros dois ficam parkeados",
                 c3.spawnou(a) and not c3.spawnou(b) and not c3.spawnou(d)
                 and c3.status(b) == "ready" and c3.status(d) == "ready",
                 f"spawn={c3.linhas_de_spawn()} {b}={c3.status(b)} {d}={c3.status(d)}")
    c3.completar(a)
    c3.despachar()
    vivos = [x for x in (b, d) if c3.status(x) == "running"]
    itens.checar("C12 depois do primeiro fechar, sobe EXATAMENTE UM dos dois parqueados",
                 len(vivos) == 1 and sum(1 for x in (b, d) if c3.status(x) == "ready") == 1,
                 f"{b}={c3.status(b)} {d}={c3.status(d)}")


# ---------------------------------------------------------------------------
# D) ESCOPO: o que NAO pode ser parkeado
# ---------------------------------------------------------------------------
def _escopo(itens: Itens, raiz: pathlib.Path, cenarios: list) -> None:
    c = Cenario(raiz, "d1-escopo")
    cenarios.append(c)
    c.board()
    dono = c.card("T02 — detentor do arquivo quente", [ARQUIVO_QUENTE])
    sem = c.card("T06 — card que nao declara arquivo nenhum", [])
    outro = c.card("T07 — card que declara OUTRO arquivo", [OUTRO_ARQUIVO])
    pai = c.card("T08 — card que declara o DIRETORIO api/", ["api/"])
    c.despachar()
    itens.checar("D1 o detentor sobe", c.spawnou(dono) and c.status(dono) == "running")
    itens.checar("D2 SEM FALSO POSITIVO: card sem declaracao NAO e parkeado",
                 c.spawnou(sem) and c.status(sem) == "running",
                 f"status={c.status(sem)}")
    itens.checar("D3 a regra e POR ARQUIVO, nao por onda/epico: card com outro arquivo sobe",
                 c.spawnou(outro) and c.status(outro) == "running",
                 f"status={c.status(outro)}")
    itens.checar("D4 diretorio declarado (`api/`) casa o arquivo do detentor e e parkeado",
                 not c.spawnou(pai) and c.status(pai) == "ready",
                 f"status={c.status(pai)} log={c.linhas_de_spawn()}")

    c2 = Cenario(raiz, "d2-fora-do-escopo", boards=OUTRO_BOARD)
    cenarios.append(c2)
    c2.board()
    x = c2.card("T02 — fora do escopo do encaixe", [ARQUIVO_QUENTE])
    y = c2.card("T03 — fora do escopo do encaixe", [ARQUIVO_QUENTE])
    c2.despachar()
    itens.checar(f"D5 fora de `hotspot_gate_boards` ({OUTRO_BOARD}) nada e parkeado",
                 c2.spawnou(x) and c2.spawnou(y)
                 and c2.status(x) == "running" and c2.status(y) == "running",
                 f"log={c2.linhas_de_spawn()}")


# ---------------------------------------------------------------------------
# E) CAMINHOS DE EXECUCAO: ponto de estrangulamento unico + prova por mutacao
# ---------------------------------------------------------------------------
def _caminhos_de_execucao(itens: Itens, raiz: pathlib.Path, cenarios: list) -> None:
    # E1/E2: claim MANUAL do parqueado
    c = Cenario(raiz, "e1-claim-manual")
    cenarios.append(c)
    c.board()
    dono = c.card("T02 — detentor", [ARQUIVO_QUENTE])
    parqueado = c.card("T03 — parqueado", [ARQUIVO_QUENTE])
    c.despachar()
    r = c.claim(parqueado)
    itens.checar("E1 `hermes kanban claim` manual do parqueado e RECUSADO",
                 r.returncode != 0 and c.status(parqueado) == "ready",
                 f"exit={r.returncode} status={c.status(parqueado)} "
                 f"saida={(r.stdout + r.stderr).strip()[:160]}")
    itens.checar("E2 o claim manual recusado tambem registra o evento `hotspot_wait`",
                 bool(c.eventos(parqueado, "hotspot_wait")))

    # E3: PROVA POR MUTACAO — encaixe ligado, kernel sem as edicoes
    c2 = Cenario(raiz, "e3-mutacao-total", edicoes_revertidas=("claim_task", "dispatch_lane"))
    cenarios.append(c2)
    c2.board()
    a = c2.card("T02 — kernel sem o encaixe", [ARQUIVO_QUENTE])
    b = c2.card("T03 — kernel sem o encaixe", [ARQUIVO_QUENTE])
    c2.despachar()
    itens.checar("E3 MUTACAO: encaixe ligado + kernel SEM as edicoes => os dois sobem "
                 "(o codigo do ponto de estrangulamento e a trava, nao a config)",
                 c2.spawnou(a) and c2.spawnou(b), f"log={c2.linhas_de_spawn()}")

    # E4: mutacao parcial — sem a edicao do claim_task, o despachante ainda segura
    c3 = Cenario(raiz, "e4-mutacao-parcial", edicoes_revertidas=("claim_task",))
    cenarios.append(c3)
    c3.board()
    a3 = c3.card("T02 — kernel sem a edicao do claim", [ARQUIVO_QUENTE])
    b3 = c3.card("T03 — kernel sem a edicao do claim", [ARQUIVO_QUENTE])
    c3.despachar()
    itens.checar("E4 com o claim_task livre, o DESPACHANTE ainda segura o parqueado "
                 "(a edicao de `_dispatch_lane_task` vale sozinha)",
                 c3.spawnou(a3) and not c3.spawnou(b3) and c3.status(b3) == "ready",
                 f"log={c3.linhas_de_spawn()} status={c3.status(b3)}")
    r4 = c3.claim(b3)
    itens.checar("E5 e o claim manual volta a funcionar com essa edicao revertida "
                 "(a edicao e mesmo a trava do caminho manual)",
                 r4.returncode == 0 and c3.status(b3) == "running",
                 f"exit={r4.returncode} status={c3.status(b3)}")

    # E6: detentor em `review` continua ocupando o arquivo quente.
    #
    # Cuidado medido: `request-review` poe o card em `review`, mas o PROPRIO
    # despachante reivindica a lane de revisao no tick seguinte — o card do detentor
    # volta a `running` (agora com o worker REVISOR). As duas pontas contam como
    # trabalho EM VOO ("review" parado e "running" com o revisor), e o parqueado
    # continua parqueado nas duas. A prova e o parqueado, nao o status do detentor.
    c4 = Cenario(raiz, "e6-review")
    cenarios.append(c4)
    c4.board()
    d = c4.card("T02 — vai para revisao", [ARQUIVO_QUENTE])
    p = c4.card("T03 — parqueado pela revisao", [ARQUIVO_QUENTE])
    c4.despachar()
    c4.revisar(d)
    c4.despachar()
    c4.despachar()
    do_detentor = [l for l in c4.linhas_de_spawn() if f"work kanban task {d}" in l]
    linha_revisao = [l for l in do_detentor if "--skills" in l]
    itens.checar("E6 detentor em revisao (trabalho ainda nao integrado) tambem segura o arquivo "
                 "quente: o parqueado continua parkeado",
                 c4.status(d) in ("review", "running") and not c4.spawnou(p)
                 and c4.status(p) == "ready",
                 f"{d}={c4.status(d)} {p}={c4.status(p)} "
                 f"spawns_do_detentor={len(do_detentor)} revisao={len(linha_revisao)} "
                 f"log={c4.linhas_de_spawn()}")


# ---------------------------------------------------------------------------
# F) INSTALACAO: --check nao altera o runtime, e o adaptador instalado == versionado
# ---------------------------------------------------------------------------
def _instalacao(itens: Itens, raiz: pathlib.Path) -> None:
    def hashes() -> dict:
        saida = {}
        for modulo in MODULOS_COM_ENCAIXE:
            caminho = DIR_CLI / modulo
            if caminho.is_file():
                saida[modulo] = hashlib.sha256(caminho.read_bytes()).hexdigest()
        return saida

    antes = hashes()
    r = rodar([PY, str(EDITOR), "--check"], dict(os.environ))
    depois = hashes()
    saida = (r.stdout or "") + (r.stderr or "")
    itens.checar("F1 `--check` (raiz viva %s) relata o estado sem alterar o runtime" % DIR_CLI,
                 r.returncode == 0 and antes == depois,
                 f"exit={r.returncode} mudou={[k for k in antes if antes[k] != depois.get(k)]} "
                 f"saida={saida.strip()[:200]}")
    itens.checar("F2 `--check` cobre as 6 ancoras do encaixe + o adaptador",
                 all(nome in saida for nome, _ in _carregar_editor().EDICOES),
                 saida.strip()[:300])

    if ADAPTADOR_INSTALADO.is_file():
        itens.checar("F3 adaptador instalado em %s == versionado no repo" % DIR_CLI,
                     ADAPTADOR_INSTALADO.read_bytes() == ADAPTADOR_VERSIONADO.read_bytes())
    else:
        itens.pular("F3 adaptador instalado == versionado no repo",
                    f"ainda nao instalado ({ADAPTADOR_INSTALADO}) — precisa de root; "
                    "ver deploy/hermes/aplicar_hotspot.sh")

    # Isolamento do harness: o adaptador DENTRO do overlay e copia real, nunca symlink.
    c = Cenario(raiz, "f4-isolamento")
    try:
        c.encerrar()
    except Exception:
        pass
    no_overlay = c.overlay / "hermes_cli" / ADAPTADOR_VERSIONADO.name
    itens.checar("F4 isolamento: adaptador do overlay e copia real (nao symlink) e igual ao versionado",
                 no_overlay.is_file() and not no_overlay.is_symlink()
                 and no_overlay.read_bytes() == ADAPTADOR_VERSIONADO.read_bytes(),
                 f"existe={no_overlay.is_file()} symlink={no_overlay.is_symlink()}")

    _coexistencia(itens, raiz)
    _ciclo_do_editor(itens, raiz)


def _coexistencia(itens: Itens, raiz: pathlib.Path) -> None:
    """Os dois encaixes (gate JEV e ARQUIVO QUENTE) convivem no MESMO kernel.

    O risco que este item mede: um editor inserir texto DENTRO da regiao ancorada do
    outro. Como cada editor decide "ja aplicado" procurando a sua propria ancora nova
    no arquivo, isso faria o outro achar que o encaixe dele nao esta aplicado — e
    reaplicar (duplicando) ou se recusar a editar. As ancoras do ARQUIVO QUENTE foram
    escolhidas fora das 7 do gate JEV; aqui isso deixa de ser intencao e vira medicao,
    com as duas edicoes no mesmo kernel.
    """
    editor_jev = RAIZ / "deploy/hermes/editar_core_do_gate.py"
    if not editor_jev.is_file():
        itens.pular("G1 coexistencia com o gate JEV", f"editor ausente: {editor_jev}")
        return
    spec = importlib.util.spec_from_file_location("editar_core_do_gate", editor_jev)
    jev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jev)
    overlay = construir_overlay(raiz / "g1-coexistencia" / "overlay")
    perdidas = []
    for nome, (arquivo, _antiga, nova) in jev.EDICOES:
        alvo = overlay / arquivo
        if not alvo.is_file() or nova not in alvo.read_text(encoding="utf-8"):
            perdidas.append(nome)
    itens.checar("G1 coexistencia: com as edicoes do ARQUIVO QUENTE aplicadas no kernel, as 6 "
                 "ancoras do gate JEV continuam reconhecidas pelo editor dele (a idempotencia do "
                 "outro encaixe nao e quebrada por este)",
                 not perdidas, f"ancoras do gate JEV perdidas: {perdidas}")


def _raiz_limpa(destino: pathlib.Path) -> pathlib.Path:
    """Kernel instalado copiado SEM o encaixe — o material do teste do editor.

    Os modulos que alguma edicao toca sao COPIA REAL (o editor escreve neles);
    o resto e symlink. Nunca o contrario: `write_text` num symlink escreveria
    ATRAVES dele, no kernel instalado.
    """
    editor = _carregar_editor()
    editados = {arquivo for _, (arquivo, _, _) in editor.EDICOES}
    pacote = destino / "hermes_cli"
    pacote.mkdir(parents=True, exist_ok=True)
    for entrada in sorted(DIR_CLI.iterdir()):
        alvo = pacote / entrada.name
        if alvo.exists() or alvo.is_symlink():
            continue
        if entrada.name in editados and entrada.is_file():
            shutil.copy2(entrada, alvo)
            continue
        os.symlink(entrada, alvo)
    return pacote


def _ciclo_do_editor(itens: Itens, raiz: pathlib.Path) -> None:
    """O caminho do OPERADOR (`aplicar_hotspot.sh` -> editor), ponta a ponta.

    A suite edita o proprio overlay em `F1`/`F4`; aqui roda-se o script de verdade
    sobre uma raiz limpa, para provar que instalar E REVERTER funcionam — rollback
    testado antes de precisar dele.
    """
    destino = raiz / "f5-editor"
    pacote = _raiz_limpa(destino)
    env_editor = dict(os.environ)
    env_editor["HOTSPOT_HERMES_HOME"] = str(destino)
    editor = _carregar_editor()
    antes = {m: (pacote / m).read_bytes() for m in MODULOS_COM_ENCAIXE}

    r1 = rodar([PY, str(EDITOR), "--aplicar"], env_editor)
    aplicado = all(nova in (pacote / arq).read_text(encoding="utf-8")
                   for _, (arq, _, nova) in editor.EDICOES)
    adaptador_ok = ((pacote / ADAPTADOR_VERSIONADO.name).is_file()
                    and (pacote / ADAPTADOR_VERSIONADO.name).read_bytes()
                    == ADAPTADOR_VERSIONADO.read_bytes())
    itens.checar("F5 o editor `--aplicar` (caminho do operador) instala as 6 ancoras + o adaptador",
                 r1.returncode == 0 and aplicado and adaptador_ok,
                 f"exit={r1.returncode} ancoras={aplicado} adaptador={adaptador_ok} "
                 f"saida={(r1.stdout + r1.stderr).strip()[:200]}")

    compila, erro = True, ""
    for modulo in MODULOS_COM_ENCAIXE:
        try:
            compile((pacote / modulo).read_text(encoding="utf-8"), modulo, "exec")
        except SyntaxError as exc:
            compila, erro = False, f"{modulo}: {exc}"
    itens.checar("F6 o kernel com o encaixe aplicado COMPILA", compila, erro)

    r2 = rodar([PY, str(EDITOR), "--check"], env_editor)
    itens.checar("F7 `--check` depois de aplicar reporta tudo OK (idempotente, sem reescrever)",
                 r2.returncode == 0 and "PENDENTE" not in (r2.stdout or ""),
                 f"exit={r2.returncode} saida={(r2.stdout or '').strip()[:200]}")

    r3 = rodar([PY, str(EDITOR), "--reverter"], env_editor)
    depois = {m: (pacote / m).read_bytes() for m in MODULOS_COM_ENCAIXE}
    r4 = rodar([PY, str(EDITOR), "--check", "--reverter"], env_editor)
    itens.checar("F8 ROLLBACK: `--reverter` devolve os 3 modulos BYTE A BYTE ao estado de antes "
                 "e remove o adaptador (rollback testado, nao prometido)",
                 r3.returncode == 0 and antes == depois
                 and not (pacote / ADAPTADOR_VERSIONADO.name).exists()
                 and r4.returncode == 0 and "PENDENTE" not in (r4.stdout or ""),
                 f"exit={r3.returncode} iguais={antes == depois} "
                 f"adaptador_existe={(pacote / ADAPTADOR_VERSIONADO.name).exists()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Suite do encaixe ARQUIVO QUENTE no despacho (TRE-W3-E01-T03-D01).")
    parser.add_argument("--manter", action="store_true", help="nao apaga o diretorio temporario")
    args = parser.parse_args(argv)

    raiz = pathlib.Path(tempfile.mkdtemp(prefix="hotspot-gate-"))
    print(f"diretorio da suite: {raiz}")
    try:
        itens = executar_suite(raiz)
    finally:
        if not args.manter:
            shutil.rmtree(raiz, ignore_errors=True)
        else:
            print(f"(mantido: {raiz})")

    if itens.falhas:
        print(f"\nFALHOU ({itens.total} itens, {len(itens.falhas)} falhas, {itens.pulados} pulados)")
        for f in itens.falhas:
            print(f"  - {f}")
        return 1
    print(f"\nPASS ({itens.total} itens, 0 falhas, {itens.pulados} pulados)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
