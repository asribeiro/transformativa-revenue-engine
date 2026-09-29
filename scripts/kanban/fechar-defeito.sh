#!/usr/bin/env bash
# Fecha um card de DEFEITO e so entao libera o card que o originou (a ordem importa).
# Regra do Anderson (29/09/2026) — ver docs/kanban/processo-de-defeitos.md.
#
# Uso:  scripts/kanban/fechar-defeito.sh <card-do-defeito> [arquivo-do-resultado]
# Sem arquivo de resultado, exige --sem-evidencia (nao se fecha defeito sem prova).
#
# O que acontece, na ordem:
#   1. fecha o defeito COM a evidencia (complete; de 'todo' o CLI recusa, entao promote+complete na
#      MESMA linha — o despachante so reivindica no proximo tick, entao o worker nunca nasce);
#   2. libera os cards que estavam esperando por ele (os FILHOS do defeito, ou seja, as origens):
#      - filho em 'blocked' -> unblock (volta a ready);
#      - filho em outro estado -> o gate de dependencia acabou de ser satisfeito, nada a desbloquear.
set -euo pipefail

BOARD="${KANBAN_BOARD:-transformativa-revenue-engine}"
PY="${HERMES_PY:-/opt/hermes/.venv/bin/python}"
DEF="${1:-}"; EVIDENCIA="${2:-}"
SEM_EVIDENCIA=0
for a in "$@"; do [ "$a" = "--sem-evidencia" ] && SEM_EVIDENCIA=1; done

if [ -z "$DEF" ]; then
  echo "FALHOU uso: fechar-defeito.sh <card-do-defeito> [arquivo-do-resultado]" >&2
  exit 1
fi
if [ -z "$EVIDENCIA" ] && [ "$SEM_EVIDENCIA" -eq 0 ]; then
  echo "FALHOU defeito nao se fecha sem evidencia. Passe o arquivo do resultado," >&2
  echo "      ou use --sem-evidencia de forma consciente (fica registrado que nao houve prova)." >&2
  exit 1
fi
[ -n "$EVIDENCIA" ] && [ ! -f "$EVIDENCIA" ] && { echo "FALHOU evidencia nao encontrada: $EVIDENCIA" >&2; exit 1; }

# Quem espera por este defeito sao os FILHOS dele — nao confiar em argumento digitado.
FILHOS="$(hermes kanban --board "$BOARD" show "$DEF" --json 2>/dev/null | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print(" ".join(d.get("children") or []))' 2>/dev/null || true)"
[ -z "$FILHOS" ] && echo "AVISO: nenhum card esta ligado a este defeito (sem origem para liberar)" >&2

if [ -n "$EVIDENCIA" ]; then
  RESULTADO="$(cat "$EVIDENCIA")"
else
  RESULTADO="DEFEITO RESOLVIDO (fechado sem arquivo de evidencia)"
fi

# 1) fecha o defeito
if hermes kanban --board "$BOARD" complete "$DEF" --result "$RESULTADO" --force >/dev/null 2>&1; then
  echo "OK    defeito $DEF fechado"
  STATUS_DEF="done"
else
  if hermes kanban --board "$BOARD" promote "$DEF" "fechamento do defeito com evidencia" >/dev/null 2>&1 \
     && hermes kanban --board "$BOARD" complete "$DEF" --result "$RESULTADO" --force >/dev/null 2>&1; then
    echo "OK    defeito $DEF fechado (via promote+complete, sem worker)"
    STATUS_DEF="done"
  else
    STATUS_DEF="$(hermes kanban --board "$BOARD" show "$DEF" --json 2>/dev/null | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print((d.get("task") or d).get("status",""))' 2>/dev/null || echo "?")"
    echo "FALHOU nao consegui fechar o defeito $DEF (estado: $STATUS_DEF) — nada foi liberado" >&2
    exit 1
  fi
fi

# 2) so agora quem esperava volta a andar.
for c in $FILHOS; do
  STATUS_FILHO="$(hermes kanban --board "$BOARD" show "$c" --json 2>/dev/null | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print((d.get("task") or d).get("status",""))' 2>/dev/null || true)"
  if [ "$STATUS_FILHO" = "blocked" ]; then
    if hermes kanban --board "$BOARD" unblock "$c" --reason "defeito $DEF fechado — card liberado para retomar" >/dev/null 2>&1; then
      echo "OK    origem $c desbloqueada (agora em '$(hermes kanban --board "$BOARD" show "$c" --json 2>/dev/null | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print((d.get("task") or d).get("status",""))' 2>/dev/null || echo "?")')"
    else
      echo "ATENCAO: nao consegui desbloquear a origem $c — faca manualmente antes de retomar" >&2
    fi
  elif [ -n "$STATUS_FILHO" ]; then
    echo "OK    origem $c segue em '$STATUS_FILHO' — dependencia satisfeita, nada a desbloquear"
  else
    echo "ATENCAO: nao consegui ler o estado da origem $c" >&2
  fi
done

echo "PROXIMO: retomar o card origem e registrar no corpo dele o que o defeito ensinou"
