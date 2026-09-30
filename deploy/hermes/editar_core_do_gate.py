#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aplica/reverte o ENCAIXE DO GATE JEV no kernel do board (card TRE-W0-E04-T05).

Faz duas coisas, as duas idempotentes e ancoradas:

  1. instala o adaptador versionado (`deploy/hermes/kanban_jev_gate.py`) como
     `/opt/hermes/hermes_cli/kanban_jev_gate.py`;
  2. insere o encaixe nos DOIS pontos de estrangulamento do ciclo do board:
       * `kanban_db.claim_task`            -> claim manual E dispatch
       * `kanban_db_dispatch._dispatch_lane_task` -> spawn do despachante
     mais os campos de contagem/telemetria (`DispatchResult`, `_TICK_ACTIVITY_FIELDS`,
     `kanban_ops`).

NAO mexe na politica JEV, no roteador nem no schema do board. O encaixe e INERTE
enquanto `kanban.jev_gate` nao estiver configurado — e e isso que permite a prova
negativa: removido o encaixe, o card volta a executar.

Uso:
    python3 deploy/hermes/editar_core_do_gate.py --check     # so relata (nao escreve)
    python3 deploy/hermes/editar_core_do_gate.py --aplicar   # instala/atualiza
    python3 deploy/hermes/editar_core_do_gate.py --reverter  # desfaz as edicoes
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys
import time

RAIZ_DO_REPO = pathlib.Path(__file__).resolve().parents[2]
HERMES = pathlib.Path("/opt/hermes")
CLI = HERMES / "hermes_cli"
ORIGEM_DO_ADAPTADOR = RAIZ_DO_REPO / "deploy/hermes/kanban_jev_gate.py"
DESTINO_DO_ADAPTADOR = CLI / "kanban_jev_gate.py"
MARCA = "GATE JEV (ASR/Transformativa)"

# ---------------------------------------------------------------------------
# Edicoes ancoradas: (arquivo, ancora_antiga, ancora_nova)
# A ancora nova comeca com o marcador do encaixe; se ela ja estiver no arquivo, a
# edicao e considerada aplicada (idempotencia).
# ---------------------------------------------------------------------------
EDICAO_ATIVIDADE = (
    "kanban_db.py",
    '    "skipped_nonspawnable",\n)',
    '    "skipped_nonspawnable",\n'
    '    # GATE JEV (ASR/Transformativa) — card retido pelo roteador JEV neste tick.\n'
    '    "skipped_jev_gate",\n)',
)

EDICAO_CLAIM = (
    "kanban_db.py",
    '    already claimed (or is not in ``ready`` status).\n'
    '    """\n'
    '    now = int(time.time())\n'
    '    lock = claimer or _claimer_id()\n'
    '    expires = now + _resolve_claim_ttl_seconds(ttl_seconds)\n'
    '    with write_txn(conn):\n',
    '    already claimed (or is not in ``ready`` status).\n'
    '    """\n'
    '    now = int(time.time())\n'
    '    lock = claimer or _claimer_id()\n'
    '    expires = now + _resolve_claim_ttl_seconds(ttl_seconds)\n'
    '    # GATE JEV (ASR/Transformativa) — card TRE-W0-E04-T05. Este e o ponto de\n'
    '    # estrangulamento por onde passam TODOS os caminhos de execucao (claim\n'
    '    # manual e dispatch): consultar o roteador aqui garante que card retido\n'
    '    # nao executa por caminho nenhum. A consulta roda em subprocesso e por\n'
    '    # isso fica FORA do write_txn (nao pode segurar o lock de escrita do\n'
    '    # board). Encaixe ausente => chamada inerte, board como antes.\n'
    '    from hermes_cli import kanban_jev_gate as _jev_gate\n'
    '    if _jev_gate.rejeicao(conn, task_id, origem="claim") is not None:\n'
    '        return None\n'
    '    with write_txn(conn):\n',
)

EDICAO_CAMPO_RESULTADO = (
    "kanban_db_dispatch.py",
    '    telemetry can tell "stuck" from "correctly idle"."""\n'
    '    skipped_per_profile_capped: list[tuple[str, str, int]] = field(default_factory=list)\n',
    '    telemetry can tell "stuck" from "correctly idle"."""\n'
    '    skipped_jev_gate: list[tuple[str, str]] = field(default_factory=list)\n'
    '    """``(task_id, motivo)`` retidos pelo GATE JEV (ASR/Transformativa): o\n'
    '    roteador JEV nao liberou a execucao deste card neste tick. OPERATOR-ACTIONABLE\n'
    '    (nao e "corretamente ocioso"): o card so anda depois de o codigo canonico da\n'
    '    acao ser declarado (`hermes/jev/acoes-declaradas.yaml`) ou de o encaixe ser\n'
    '    removido (`kanban.jev_gate`)."""\n'
    '    skipped_per_profile_capped: list[tuple[str, str, int]] = field(default_factory=list)\n',
)

EDICAO_DISPATCH = (
    "kanban_db_dispatch.py",
    '    if _gate_row is not None and not int(_gate_row["d"]):\n'
    '        result.skipped_nonspawnable.append(task_id)\n'
    '        return False\n'
    '    # Non-profile assignees (control-plane lanes that pull via ``claim_task``)\n',
    '    if _gate_row is not None and not int(_gate_row["d"]):\n'
    '        result.skipped_nonspawnable.append(task_id)\n'
    '        return False\n'
    '    # GATE JEV (ASR/Transformativa) — card TRE-W0-E04-T05: o roteador JEV decide\n'
    '    # ANTES de reservar a vaga de spawn. Encaixe ausente => inerte.\n'
    '    from hermes_cli import kanban_jev_gate as _jev_gate\n'
    '    _rejeicao_jev = _jev_gate.rejeicao(conn, task_id, board=board, origem="dispatch")\n'
    '    if _rejeicao_jev is not None:\n'
    '        result.skipped_jev_gate.append((task_id, _jev_gate.resumo(_rejeicao_jev)))\n'
    '        return False\n'
    '    # Non-profile assignees (control-plane lanes that pull via ``claim_task``)\n',
)

EDICAO_OPS_JSON = (
    "kanban_ops.py",
    '            "skipped_nonspawnable": res.skipped_nonspawnable,\n',
    '            "skipped_nonspawnable": res.skipped_nonspawnable,\n'
    '            "skipped_jev_gate": [\n'
    '                {"task_id": tid, "motivo": motivo}\n'
    '                for (tid, motivo) in res.skipped_jev_gate\n'
    '            ],\n',
)

EDICAO_OPS_TEXTO = (
    "kanban_ops.py",
    '    for tid, reason in res.respawn_guarded:\n',
    '    for tid, motivo in res.skipped_jev_gate:\n'
    '        print(f"Retido pelo gate JEV: {tid} — {motivo}")\n'
    '    for tid, reason in res.respawn_guarded:\n',
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
                            DESTINO_DO_ADAPTADOR.suffix + f".before-gate-jev-{int(time.time())}")
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
        backup = caminho.with_suffix(caminho.suffix + f".before-gate-jev-{int(time.time())}")
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
        print(f"  {'OK':24s} {'limpeza':20s} __pycache__ de kanban_jev_gate")
        for pyc in CLI.glob("__pycache__/kanban_jev_gate.*.pyc"):
            pyc.unlink(missing_ok=True)
    return 0


def _conferir_sintaxe_previa(caminho: pathlib.Path, texto: str) -> bool:
    try:
        compile(texto, str(caminho), "exec")
    except SyntaxError:
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Encaixe do gate JEV no kernel do board (TRE-W0-E04-T05).")
    grupo = parser.add_mutually_exclusive_group(required=False)
    grupo.add_argument("--aplicar", action="store_true", help="instala/atualiza o encaixe (padrao)")
    grupo.add_argument("--reverter", action="store_true", help="desfaz as edicoes")
    parser.add_argument("--check", action="store_true",
                        help="so relata o estado, nao escreve (combinavel com --reverter)")
    args = parser.parse_args(argv)
    return aplicar(reverter=args.reverter, apenas_conferir=args.check)


if __name__ == "__main__":
    sys.exit(main())
