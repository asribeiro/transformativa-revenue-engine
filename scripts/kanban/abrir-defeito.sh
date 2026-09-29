#!/usr/bin/env bash
# Abre um card de DEFEITO e faz o card que o originou ESPERAR por ele.
# Regra do Anderson (29/09/2026) — ver docs/kanban/processo-de-defeitos.md.
#
# Uso:  scripts/kanban/abrir-defeito.sh <card-origem> "<titulo>" <arquivo-do-corpo> [--retroativo]
#
# O que acontece, na ordem:
#   1. cria o card de defeito (sem despachar worker);
#   2. linka o defeito como PRE-REQUISITO da origem — a partir daqui o board nao deixa a origem ser
#      reivindicada nem concluida antes de o defeito fechar (gate real, nao combinado verbal);
#   3. se a origem estiver em estado bloqueavel (ready/running/review), marca tambem o bloqueio
#      explicito com --kind dependency — o registro visivel de "bloqueie o card pai";
#   4. se a origem estiver em todo/triage, o proprio gate segura o card e o CLI recusa o bloqueio
#      ("cannot block"): nesse caso nao se inventa estado.
#
# Por que o defeito e PRE-REQUISITO e nao filho: neste board o FILHO so pode ser concluido depois de o
# pai estar done (gate em hermes_cli/kanban.py: "unsatisfied parent dependencies"). Defeito criado como
# filho da origem fica congelado enquanto a origem estiver aberta — nao daria para fechar durante a
# correcao. Como pre-requisito, o gate trabalha A FAVOR da regra: a origem nao anda enquanto o defeito
# existir. A rastreabilidade fica no corpo do card (ORIGEM: card X) e no proprio vinculo.
set -euo pipefail

BOARD="${KANBAN_BOARD:-transformativa-revenue-engine}"
PY="${HERMES_PY:-/opt/hermes/.venv/bin/python}"
ORIGEM="${1:-}"; TITULO="${2:-}"; CORPO="${3:-}"; PRIORIDADE="${4:-}"

RETROATIVO=0
for a in "$@"; do [ "$a" = "--retroativo" ] && RETROATIVO=1; done
[ "$RETROATIVO" -eq 1 ] && PRIORIDADE=""

if [ -z "$ORIGEM" ] || [ -z "$TITULO" ] || [ -z "$CORPO" ]; then
  echo "FALHOU uso: abrir-defeito.sh <card-origem> \"<titulo>\" <arquivo-do-corpo> [--retroativo]" >&2
  exit 1
fi
[ -f "$CORPO" ] || { echo "FALHOU corpo do defeito nao encontrado: $CORPO" >&2; exit 1; }

status_de() {
  hermes kanban --board "$BOARD" show "$1" --json 2>/dev/null | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print((d.get("task") or d).get("status",""))' 2>/dev/null || true
}

# Card origem tem de existir: defeito orfao nao entra no board.
if ! hermes kanban --board "$BOARD" show "$ORIGEM" --json >/dev/null 2>&1; then
  echo "FALHOU card origem nao encontrado no board $BOARD: $ORIGEM" >&2
  exit 1
fi
STATUS_ORIGEM="$(status_de "$ORIGEM")"

# 1. cria o card de defeito
ARGS=(--board "$BOARD" create "$TITULO" --body-file "$CORPO" --no-dispatch --json)
[ -n "$PRIORIDADE" ] && ARGS+=(--priority "$PRIORIDADE")
SAIDA="$(hermes kanban "${ARGS[@]}" 2>&1)" || { echo "FALHOU ao criar o card de defeito: $SAIDA" >&2; exit 1; }
ID="$(printf '%s' "$SAIDA" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print((d.get("task") or d).get("id",""))' 2>/dev/null || true)"
[ -n "$ID" ] || { echo "FALHOU nao consegui ler o ID do card criado. Saida: $SAIDA" >&2; exit 1; }

# 2. o defeito passa a segurar a origem (gate do proprio board)
if hermes kanban --board "$BOARD" link "$ID" "$ORIGEM" >/dev/null 2>&1; then
  GATE="defeito $ID ligado como pre-requisito de $ORIGEM"
else
  GATE="ATENCAO: nao consegui ligar o defeito $ID a origem $ORIGEM — faca o link manualmente"
fi

# 3. bloqueio explicito, quando o estado da origem permite
BLOQ="origem $ORIGEM em '$STATUS_ORIGEM': bloqueio explicito nao se aplica (o gate de dependencia segura)"
case " $STATUS_ORIGEM " in
  *" ready "*|*" running "*|*" review "*)
    if hermes kanban --board "$BOARD" block "$ORIGEM" "defeito $ID aberto: $TITULO" --kind dependency >/dev/null 2>&1; then
      BLOQ="origem $ORIGEM bloqueada (kind=dependency) — agora em '$(status_de "$ORIGEM")'"
    else
      BLOQ="ATENCAO: nao consegui bloquear a origem $ORIGEM — confira o estado dela"
    fi
    ;;
esac

echo "$ID"
echo "OK    defeito $ID criado (origem: $ORIGEM, que estava em '$STATUS_ORIGEM')" >&2
echo "OK    $GATE" >&2
echo "OK    $BLOQ" >&2
echo "PROXIMO: resolver, provar e rodar scripts/kanban/fechar-defeito.sh $ID <evidencia.txt>" >&2
