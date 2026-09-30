#!/usr/bin/env bash
# Remove o ENCAIXE do gate JEV do ciclo do board (reversao do card TRE-W0-E04-T05).
#
#   bash scripts/remover_gate_jev.sh            # desliga o encaixe (config)
#   bash scripts/remover_gate_jev.sh --check    # so relata
#   bash scripts/remover_gate_jev.sh --kernel   # tambem reverte o codigo (precisa de root)
#
# Desligar a config JA desarma o encaixe: o adaptador devolve "sem encaixe" e o
# board volta a despachar como antes. E exatamente essa reversao que a suite usa
# como PROVA NEGATIVA (scripts/verificar_gate_jev.py, cenario S5).
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${HERMES_PY:-/opt/hermes/.venv/bin/python}"
CHECK=0; KERNEL=0
for a in "$@"; do
  [ "$a" = "--check" ] && CHECK=1
  [ "$a" = "--kernel" ] && KERNEL=1
done

FALHAS=0
ok()   { printf '  OK      %s\n' "$1"; }
erro() { printf '  FALHOU  %s\n' "$1"; FALHAS=$((FALHAS+1)); }

if [ "$CHECK" -eq 1 ]; then
  ATUAL="$(hermes config get kanban.jev_gate 2>/dev/null || true)"
  if [ -z "$ATUAL" ]; then ok "kanban.jev_gate ja esta vazio (encaixe desarmado)"; \
  else echo "  PENDENTE kanban.jev_gate = $ATUAL"; fi
else
  for chave in kanban.jev_gate kanban.jev_gate_boards kanban.jev_gate_timeout; do
    if hermes config unset "$chave" >/dev/null 2>&1; then ok "removi $chave"; \
    else erro "nao removi $chave"; fi
  done
  echo "  AVISO   o codigo do adaptador continua em /opt/hermes (inerte sem config);"
  echo "          para tirar tambem o codigo, rode a linha do operador com --reverter."
  echo "  LINHA UNICA PARA O OPERADOR (root, no container):"
  printf '  bash %s/deploy/hermes/aplicar_gate_jev.sh --reverter\n' "$RAIZ"
fi

if [ "$KERNEL" -eq 1 ]; then
  if [ "$CHECK" -eq 1 ]; then
    "$PY" "$RAIZ/deploy/hermes/editar_core_do_gate.py" --reverter --check
  else
    "$PY" "$RAIZ/deploy/hermes/editar_core_do_gate.py" --reverter || erro "nao reverti o codigo"
  fi
fi

if [ "$FALHAS" -gt 0 ]; then
  echo "FALHOU ($FALHAS itens)"
  exit 1
fi
echo "OK (encaixe desarmado)"
