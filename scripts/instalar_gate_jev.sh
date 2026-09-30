#!/usr/bin/env bash
# Instala o ENCAIXE do gate JEV no ciclo do board (card TRE-W0-E04-T05).
#
# O encaixe tem duas metades:
#   (A) a CONFIGURACAO (`kanban.jev_gate` + `kanban.jev_gate_boards`) — vive em
#       /opt/data/config.yaml, gravavel pelo agente. E o que LIGA o encaixe.
#   (B) o CODIGO do ponto de estrangulamento em /opt/hermes/hermes_cli (adaptador
#       + edicao de `claim_task` e de `_dispatch_lane_task`) — precisa de root,
#       porque /opt/hermes e do root e o agente nao tem sudo.
#
# Este script faz (A) e CONFERE (B), imprimindo a linha unica que o operador
# precisa rodar para aplicar (B). Sem (A) o encaixe e inerte; sem (B) a config
# nao tem onde ser lida.
#
# Uso:
#   bash scripts/instalar_gate_jev.sh            # aplica a config e confere
#   bash scripts/instalar_gate_jev.sh --check    # so relata (nao escreve)
#
# Reverter: bash scripts/remover_gate_jev.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="$RAIZ/hermes/jev/gate/gate_jev.py"
PY="${HERMES_PY:-/opt/hermes/.venv/bin/python}"
BOARD="${KANBAN_BOARD:-transformativa-revenue-engine}"
TIMEOUT="${JEV_GATE_TIMEOUT:-60}"
CHECK=0
for a in "$@"; do [ "$a" = "--check" ] && CHECK=1; done

FALHAS=0
ok()   { printf '  OK      %s\n' "$1"; }
erro() { printf '  FALHOU  %s\n' "$1"; FALHAS=$((FALHAS+1)); }

echo "Encaixe do gate JEV — board $BOARD"

# 1. o hook existe e roda?
if [ -f "$GATE" ]; then
  ok "hook presente: $GATE"
else
  erro "hook ausente: $GATE"
fi

# 2. a config liga o encaixe
if [ "$CHECK" -eq 1 ]; then
  ATUAL="$(hermes config get kanban.jev_gate 2>/dev/null || true)"
  if [ "$ATUAL" = "$GATE" ]; then ok "config kanban.jev_gate ja aponta para o hook"; \
  else echo "  PENDENTE config kanban.jev_gate = ${ATUAL:-<vazio>}"; fi
  ATUAL_B="$(hermes config get kanban.jev_gate_boards 2>/dev/null || true)"
  case "$ATUAL_B" in *"$BOARD"*) ok "config kanban.jev_gate_boards inclui $BOARD";; \
    *) echo "  PENDENTE config kanban.jev_gate_boards = ${ATUAL_B:-<vazio>}";; esac
else
  if hermes config set kanban.jev_gate "$GATE" --force >/dev/null 2>&1; then
    ok "config kanban.jev_gate = $GATE"
  else
    erro "nao gravei kanban.jev_gate"
  fi
  if hermes config set kanban.jev_gate_boards "[\"$BOARD\"]" --force >/dev/null 2>&1; then
    ok "config kanban.jev_gate_boards = [\"$BOARD\"] (escopo explicito)"
  else
    erro "nao gravei kanban.jev_gate_boards"
  fi
  if hermes config set kanban.jev_gate_timeout "$TIMEOUT" --force >/dev/null 2>&1; then
    ok "config kanban.jev_gate_timeout = $TIMEOUT s (timeout = abstencao, nunca permissao)"
  else
    erro "nao gravei kanban.jev_gate_timeout"
  fi
fi

# 3. o codigo do ponto de estrangulamento (precisa de root)
ESTADO="$("$PY" "$RAIZ/deploy/hermes/editar_core_do_gate.py" --check 2>&1)"
if printf '%s' "$ESTADO" | grep -q "PENDENTE"; then
  erro "codigo do encaixe ainda NAO aplicado em /opt/hermes (precisa de root)"
  printf '%s\n' "$ESTADO" | sed 's/^/      /'
  echo
  echo "  LINHA UNICA PARA O OPERADOR (root, no container):"
  printf '  bash %s/deploy/hermes/aplicar_gate_jev.sh\n' "$RAIZ"
else
  ok "codigo do encaixe aplicado em /opt/hermes (adaptador + 2 pontos de estrangulamento)"
  printf '%s\n' "$ESTADO" | sed 's/^/      /'
  if "$PY" "$RAIZ/deploy/hermes/editar_core_do_gate.py" --check 2>&1 \
      | grep -q "adaptador\.*PENDENTE"; then
    echo "  AVISO   adaptador instalado difere do versionado: rode a linha do operador acima"
  fi
fi

# 4. o despachante EM EXECUCAO so pega o codigo novo depois de reiniciar
if pgrep -f "hermes gateway run" >/dev/null 2>&1; then
  echo "  AVISO   ha um despachante (gateway) em execucao: ele so passa a aplicar o encaixe"
  echo "          depois de reiniciar. Ate la, o board segue despachando como antes."
fi

if [ "$FALHAS" -gt 0 ]; then
  echo "FALHOU ($FALHAS itens)"
  exit 1
fi
echo "OK (configuracao do encaixe aplicada e conferida)"
