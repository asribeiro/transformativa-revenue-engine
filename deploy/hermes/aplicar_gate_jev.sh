#!/usr/bin/env bash
# APLICA o codigo do ENCAIXE do gate JEV no kernel do board (card TRE-W0-E04-T05).
#
#   bash deploy/hermes/aplicar_gate_jev.sh              # instala/atualiza
#   bash deploy/hermes/aplicar_gate_jev.sh --check      # so relata (nao escreve)
#   bash deploy/hermes/aplicar_gate_jev.sh --reverter   # desfaz as edicoes
#
# POR QUE ESTE SCRIPT EXISTE SEPARADO DO `scripts/instalar_gate_jev.sh`:
# /opt/hermes e do root e o agente do Hermes nao tem sudo. Entao a metade que
# precisa de root vive aqui e roda como UMA LINHA do operador; a configuracao
# (`kanban.jev_gate`) fica no outro script, que o agente aplica sozinho.
#
# O que ele faz, em ordem, com backup datado de cada arquivo antes de escrever:
#   1. instala `deploy/hermes/kanban_jev_gate.py` como
#      /opt/hermes/hermes_cli/kanban_jev_gate.py (adaptador);
#   2. insere o encaixe ancorado em `kanban_db.claim_task` (claim manual E
#      dispatch) e em `kanban_db_dispatch._dispatch_lane_task` (spawn);
#   3. acrescenta o contador `skipped_jev_gate` no DispatchResult, na lista de
#      atividade do tick e na saida do CLI (`kanban_ops.py`);
#   4. roda `--check` no fim e reporta OK/FALHOU por item.
#
# Idempotente: rodar duas vezes nao duplica nada. Se uma ancora nao for
# encontrada, ele FALHA e nao edita (o kernel mudou de forma; revisar o patch).
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${HERMES_PY:-/opt/hermes/.venv/bin/python}"
EDITOR="$RAIZ/deploy/hermes/editar_core_do_gate.py"

if [ ! -f "$EDITOR" ]; then
  echo "FALHOU editor do encaixe nao encontrado: $EDITOR"
  exit 1
fi

"$PY" "$EDITOR" "$@"
SAIDA=$?

echo
echo "conferencia final:"
"$PY" "$EDITOR" --check | sed 's/^/  /'
exit "$SAIDA"
