#!/usr/bin/env bash
# =====================================================================================
# verificar-ultimo-backup.sh [dev|homolog|prod|todos]
#
# Verifica o artefato de backup MAIS RECENTE de cada ambiente, fazendo um RESTORE REAL
# num container descartavel. E o que transforma "backup existe" em "backup presta":
# backup nunca restaurado nao e backup, e esperanca.
#
# Ambiente cujo container existe mas NAO tem backup = FALHA (nao e "pulado").
# Ambiente ainda nao provisionado = PULADO (honesto: nao ha o que copiar).
# =====================================================================================
set -uo pipefail

ALVO="${1:-todos}"
DEST="${TRE_BACKUP_DIR:-/opt/tre/backup}"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FALHAS=0
ITENS=0
ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

verificar_ambiente() {
  local amb="$1"
  local servico="${TRE_PG_SERVICO:-pg-$amb}"
  echo "------------------------------------------------------------------"
  echo "-- ambiente: $amb"
  local ultimo
  ultimo="$(ls -d "$DEST/tre_${amb}_"* 2>/dev/null | sort | tail -1)"

  if [ -z "$ultimo" ]; then
    if docker inspect "$servico" >/dev/null 2>&1; then
      ko "ambiente '$amb' esta provisionado (container $servico) mas NAO tem nenhum backup"
    else
      echo "PULADO ambiente '$amb' ainda nao provisionado (sem container $servico e sem backup)"
    fi
    return 0
  fi

  local idade_h
  idade_h="$(python3 - "$ultimo" <<'PY' 2>/dev/null || echo "?"
import os, sys, time
print(int((time.time() - os.path.getmtime(sys.argv[1])) // 3600))
PY
)"
  echo "   artefato mais recente: $(basename "$ultimo") (${idade_h}h de idade)"
  if bash "$AQUI/verificar-backup.sh" "$ultimo"; then
    ok "restore do ultimo backup de '$amb' aprovado"
  else
    ko "restore do ultimo backup de '$amb' REPROVADO"
  fi

  # backup velho tambem e falha: se a rotina diaria parou, quero saber aqui
  if [ "${idade_h:-0}" != "?" ] && [ "${idade_h:-0}" -gt 48 ] 2>/dev/null; then
    ko "ultimo backup tem ${idade_h}h (> 48h) — a rotina de backup parou?"
  fi
}

if [ "$ALVO" = "todos" ]; then
  for amb in dev homolog prod; do verificar_ambiente "$amb"; done
else
  verificar_ambiente "$ALVO"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: VERIFICACAO_OK ($ITENS itens)"
  exit 0
else
  echo "RESULTADO: VERIFICACAO_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
