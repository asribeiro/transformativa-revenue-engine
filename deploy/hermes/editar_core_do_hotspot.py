#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aplica/reverte o ENCAIXE ARQUIVO QUENTE no kernel do board (card TRE-W3-E01-T03-D01).

Faz duas coisas, as duas idempotentes e ancoradas:

  1. instala o adaptador versionado (`deploy/hermes/kanban_hotspot_gate.py`) como
     `<hermes>/hermes_cli/kanban_hotspot_gate.py`;
  2. insere o encaixe nos DOIS pontos de estrangulamento do ciclo do board:
       * `kanban_db.claim_task`                     -> claim manual E despachante
       * `kanban_db_dispatch._dispatch_lane_task`   -> vaga de spawn do tick
     mais os campos de contagem/telemetria (`DispatchResult`,
     `_TICK_ACTIVITY_FIELDS`, `kanban_ops`).

NAO toca no adaptador do gate JEV, na politica JEV, no roteador nem no schema do
board. As ancoras foram escolhidas FORA das regioes que as 7 edicoes do encaixe
JEV usam: os dois editores continuam idempotentes lado a lado.

O encaixe e INERTE enquanto `kanban.hotspot_gate` nao estiver ligado — e e isso
que permite a prova negativa (defeito reproduzido com o encaixe desligado) e o
desligamento de emergencia em um comando.

Uso:
    python3 deploy/hermes/editar_core_do_hotspot.py --check     # so relata
    python3 deploy/hermes/editar_core_do_hotspot.py --aplicar   # instala/atualiza
    python3 deploy/hermes/editar_core_do_hotspot.py --reverter  # desfaz as edicoes

`HOTSPOT_HERMES_HOME` troca a raiz do Hermes (usado pela suite para editar um
overlay em diretorio temporario, nunca a instalacao viva).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import time

RAIZ_DO_REPO = pathlib.Path(__file__).resolve().parents[2]
HERMES = pathlib.Path(os.environ.get("HOTSPOT_HERMES_HOME") or "/opt/hermes")
CLI = HERMES / "hermes_cli"
ORIGEM_DO_ADAPTADOR = RAIZ_DO_REPO / "deploy/hermes/kanban_hotspot_gate.py"
DESTINO_DO_ADAPTADOR = CLI / "kanban_hotspot_gate.py"
MARCA = "ENCAIXE ARQUIVO QUENTE (ASR/Transformativa)"
SUFIXO_DE_BACKUP = ".before-hotspot"

# ---------------------------------------------------------------------------
# Edicoes ancoradas: (arquivo, ancora_antiga, ancora_nova)
# A ancora nova CONTEM a antiga; se ela ja estiver no arquivo, a edicao e
# considerada aplicada (idempotencia).
# ---------------------------------------------------------------------------
EDICAO_ATIVIDADE = (
    "kanban_db.py",
    '    "skipped_nonspawnable",\n'
    '    # GATE JEV (ASR/Transformativa) — card retido pelo roteador JEV neste tick.\n',
    '    # ENCAIXE ARQUIVO QUENTE (ASR/Transformativa) — card parkeado neste tick: outro\n'
    '    # card EM VOO declara o mesmo arquivo quente (TRE-W3-E01-T03-D01).\n'
    '    "skipped_hotspot",\n'
    '    "skipped_nonspawnable",\n'
    '    # GATE JEV (ASR/Transformativa) — card retido pelo roteador JEV neste tick.\n',
)

EDICAO_CLAIM = (
    "kanban_db.py",
    '        # Single enforcement point: never ready -> running with an undone\n'
    '        # parent, whichever writer set \'ready\'. Demote to \'todo\';\n',
    '        # ENCAIXE ARQUIVO QUENTE (ASR/Transformativa) — card TRE-W3-E01-T03-D01.\n'
    '        # Ponto de estrangulamento por onde passam TODOS os caminhos de execucao\n'
    '        # (claim manual E despachante): enquanto outro card EM VOO (running/review)\n'
    '        # declara o mesmo arquivo quente, ESTE card fica em `ready` — parkeado, nao\n'
    '        # perdido, e o tick seguinte tenta de novo. Nao ha deadlock: o detentor ja\n'
    '        # esta rodando e nao espera por ninguem. A leitura e do proprio board (sem\n'
    '        # subprocesso) e fica junto do CAS, para o veredito nao envelhecer entre a\n'
    '        # checagem e a reivindicacao. Encaixe ausente => inerte.\n'
    '        from hermes_cli import kanban_hotspot_gate as _hotspot\n'
    '        _impedimento_hotspot = _hotspot.impedimento(conn, task_id)\n'
    '        if _impedimento_hotspot is not None:\n'
    '            _hotspot.registrar(conn, task_id, _impedimento_hotspot, origem="claim")\n'
    '            return None\n'
    '        # Single enforcement point: never ready -> running with an undone\n'
    '        # parent, whichever writer set \'ready\'. Demote to \'todo\';\n',
)

EDICAO_CAMPO_RESULTADO = (
    "kanban_db_dispatch.py",
    '    crashed: list[str] = field(default_factory=list)\n'
    '    """Task ids reclaimed because their worker PID disappeared."""\n',
    '    skipped_hotspot: list[tuple[str, str]] = field(default_factory=list)\n'
    '    """``(task_id, motivo)`` parkeados pelo ENCAIXE ARQUIVO QUENTE\n'
    '    (ASR/Transformativa, TRE-W3-E01-T03-D01): outro card EM VOO declara o mesmo\n'
    '    arquivo quente e a regra e no maximo UM por arquivo em voo. OPERATOR-ACTIONABLE\n'
    '    (nao e "corretamente ocioso"): o card anda sozinho quando o detentor fechar —\n'
    '    `hermes kanban tail` mostra o evento `hotspot_wait` com detentor e arquivo."""\n'
    '    crashed: list[str] = field(default_factory=list)\n'
    '    """Task ids reclaimed because their worker PID disappeared."""\n',
)

EDICAO_DISPATCH = (
    "kanban_db_dispatch.py",
    '    guard_reason = check_respawn_guard(conn, task_id, lane=lane)\n',
    '    # ENCAIXE ARQUIVO QUENTE (ASR/Transformativa) — card TRE-W3-E01-T03-D01: o\n'
    '    # promotor decide ANTES de reservar a vaga de spawn. Card com arquivo quente\n'
    '    # ocupado por outro card EM VOO nao sobe worker neste tick. Encaixe ausente\n'
    '    # => inerte (nenhuma consulta, nenhum balde novo).\n'
    '    from hermes_cli import kanban_hotspot_gate as _hotspot\n'
    '    _impedimento_hotspot = _hotspot.impedimento(conn, task_id, board=board)\n'
    '    if _impedimento_hotspot is not None:\n'
    '        result.skipped_hotspot.append((task_id, _hotspot.resumo(_impedimento_hotspot)))\n'
    '        _hotspot.registrar(conn, task_id, _impedimento_hotspot, origem="dispatch")\n'
    '        return False\n'
    '    guard_reason = check_respawn_guard(conn, task_id, lane=lane)\n',
)

EDICAO_OPS_JSON = (
    "kanban_ops.py",
    '            "auto_assigned_default": res.auto_assigned_default,\n',
    '            "skipped_hotspot": [\n'
    '                {"task_id": tid, "motivo": motivo}\n'
    '                for (tid, motivo) in res.skipped_hotspot\n'
    '            ],\n'
    '            "auto_assigned_default": res.auto_assigned_default,\n',
)

EDICAO_OPS_TEXTO = (
    "kanban_ops.py",
    '    if res.rate_limited:\n'
    '        print(f"Rate-limited (released to ready, no failure counted): '
    '{\', \'.join(res.rate_limited)}")\n',
    '    for tid, motivo in res.skipped_hotspot:\n'
    '        print(f"Parkeado por arquivo quente: {tid} — {motivo}")\n'
    '    if res.rate_limited:\n'
    '        print(f"Rate-limited (released to ready, no failure counted): '
    '{\', \'.join(res.rate_limited)}")\n',
)

EDICOES = [
    ("tick_activity", EDICAO_ATIVIDADE),
    ("claim_task", EDICAO_CLAIM),
    ("dispatch_result", EDICAO_CAMPO_RESULTADO),
    ("dispatch_lane", EDICAO_DISPATCH),
    ("kanban_ops_json", EDICAO_OPS_JSON),
    ("kanban_ops_texto", EDICAO_OPS_TEXTO),
]


def _aplicada(texto: str, nova: str) -> bool:
    return nova in texto


def _conta(texto: str, agulha: str) -> int:
    return texto.count(agulha)


def _conferir_sintaxe_previa(caminho: pathlib.Path, texto: str) -> bool:
    try:
        compile(texto, str(caminho), "exec")
    except SyntaxError:
        return False
    return True


def aplicar(reverter: bool = False, apenas_conferir: bool = False) -> int:
    falhas = []
    relatorio = []

    # 1. adaptador
    if reverter:
        if DESTINO_DO_ADAPTADOR.exists():
            if apenas_conferir:
                relatorio.append(("adaptador (remocao)", "PENDENTE", str(DESTINO_DO_ADAPTADOR)))
            else:
                DESTINO_DO_ADAPTADOR.unlink()
                relatorio.append(("adaptador (remocao)", "OK", str(DESTINO_DO_ADAPTADOR)))
        else:
            relatorio.append(("adaptador (remocao)", "OK (ja ausente)", str(DESTINO_DO_ADAPTADOR)))
    else:
        if not ORIGEM_DO_ADAPTADOR.is_file():
            falhas.append(f"adaptador versionado nao encontrado: {ORIGEM_DO_ADAPTADOR}")
        else:
            igual = False
            if DESTINO_DO_ADAPTADOR.is_file():
                igual = (DESTINO_DO_ADAPTADOR.read_bytes() == ORIGEM_DO_ADAPTADOR.read_bytes())
            if apenas_conferir:
                relatorio.append(("adaptador", "OK (identico)" if igual else "PENDENTE",
                                  str(DESTINO_DO_ADAPTADOR)))
            else:
                if igual:
                    relatorio.append(("adaptador", "OK (ja identico)", str(DESTINO_DO_ADAPTADOR)))
                else:
                    if DESTINO_DO_ADAPTADOR.is_file():
                        backup = DESTINO_DO_ADAPTADOR.with_suffix(
                            DESTINO_DO_ADAPTADOR.suffix + f"{SUFIXO_DE_BACKUP}-{int(time.time())}")
                        shutil.copy2(DESTINO_DO_ADAPTADOR, backup)
                    shutil.copy2(ORIGEM_DO_ADAPTADOR, DESTINO_DO_ADAPTADOR)
                    relatorio.append(("adaptador", "OK (instalado)", str(DESTINO_DO_ADAPTADOR)))

    # 2. edicoes ancoradas
    for nome, (arquivo, antiga, nova) in EDICOES:
        caminho = CLI / arquivo
        if not caminho.is_file():
            falhas.append(f"{nome}: arquivo ausente {caminho}")
            continue
        texto = caminho.read_text(encoding="utf-8")
        if reverter:
            if not _aplicada(texto, nova):
                relatorio.append((nome, "OK (ja revertido)", arquivo))
                continue
            n = _conta(texto, nova)
            if n != 1:
                falhas.append(f"{nome}: ancora nova aparece {n}x em {arquivo} (nao reverti)")
                continue
            if apenas_conferir:
                relatorio.append((nome, "PENDENTE (reverter)", arquivo))
                continue
            caminho.write_text(texto.replace(nova, antiga), encoding="utf-8")
            relatorio.append((nome, "OK (revertido)", arquivo))
            continue

        if _aplicada(texto, nova):
            relatorio.append((nome, "OK (ja aplicado)", arquivo))
            continue
        n = _conta(texto, antiga)
        if n != 1:
            falhas.append(f"{nome}: ancora antiga aparece {n}x em {arquivo} (esperado 1) — "
                          f"o kernel mudou de forma; NAO editei")
            continue
        if apenas_conferir:
            relatorio.append((nome, "PENDENTE (aplicar)", arquivo))
            continue
        if not _conferir_sintaxe_previa(caminho, texto.replace(antiga, nova, 1)):
            falhas.append(f"{nome}: a edicao nao compila em {arquivo}; NAO escrevi")
            continue
        backup = caminho.with_suffix(caminho.suffix + f"{SUFIXO_DE_BACKUP}-{int(time.time())}")
        shutil.copy2(caminho, backup)
        caminho.write_text(texto.replace(antiga, nova, 1), encoding="utf-8")
        relatorio.append((nome, "OK (aplicado)", arquivo))

    for nome, estado, alvo in relatorio:
        print(f"  {estado:24s} {nome:20s} {alvo}")
    if falhas:
        for f in falhas:
            print(f"FALHOU: {f}", file=sys.stderr)
        return 1
    if not reverter and not apenas_conferir:
        for pyc in CLI.glob("__pycache__/kanban_hotspot_gate.*.pyc"):
            pyc.unlink(missing_ok=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Encaixe ARQUIVO QUENTE no kernel do board (TRE-W3-E01-T03-D01).")
    grupo = parser.add_mutually_exclusive_group(required=False)
    grupo.add_argument("--aplicar", action="store_true", help="instala/atualiza o encaixe (padrao)")
    grupo.add_argument("--reverter", action="store_true", help="desfaz as edicoes")
    parser.add_argument("--check", action="store_true",
                        help="so relata o estado, nao escreve (combinavel com --reverter)")
    args = parser.parse_args(argv)
    return aplicar(reverter=args.reverter, apenas_conferir=args.check)


if __name__ == "__main__":
    sys.exit(main())
