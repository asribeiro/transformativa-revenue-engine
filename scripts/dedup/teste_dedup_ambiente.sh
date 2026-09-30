#!/usr/bin/env bash
# =====================================================================================
# TRE-W1-E04-T01 — cenario REAL de deduplicacao no ambiente (default dev).
#
# Roda na VPS do ambiente (ADR-0008: quem fala com o PostgreSQL e a VPS, via docker exec).
# O cenario semeia massa sintetica marcada, mede deteccao/decisao/merge/auditoria/
# fila humana/rollback e LIMPA tudo, provando que o ambiente volta ao estado anterior.
#
# Uso: bash scripts/dedup/teste_dedup_ambiente.sh [dev]
# =====================================================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOTOR="$DIR/deduplicar_organizacoes.py"
AMB="${1:-dev}"
FALHAS=0
ITENS=0

registrar() { # nome, ok(0/1), detalhe
  ITENS=$((ITENS + 1))
  if [ "$2" -eq 0 ]; then
    echo "OK     $1"
  else
    echo "FALHOU $1" >&2
    [ -n "${3:-}" ] && echo "       $3" >&2
    FALHAS=$((FALHAS + 1))
  fi
}

echo "== guardrail de ambiente (ADR-005): producao e recusada"
SAIDA_PROD="$(python3 "$MOTOR" --cenario-ambiente --ambiente prod 2>&1)"
RC_PROD=$?
echo "$SAIDA_PROD"
registrar "ambiente 'prod' recusado pelo motor (exit != 0)" "$([ "$RC_PROD" -ne 0 ] && echo 0 || echo 1)" "exit=$RC_PROD"
registrar "a recusa cita a ADR-005 (nada nasce em producao)" \
  "$(echo "$SAIDA_PROD" | grep -q 'ADR-005' && echo 0 || echo 1)" "saida: $SAIDA_PROD"

echo "== cenario real no ambiente '$AMB'"
SAIDA_CEN="$(python3 "$MOTOR" --cenario-ambiente --ambiente "$AMB" 2>&1)"
RC_CEN=$?
echo "$SAIDA_CEN"
registrar "cenario no ambiente '$AMB' passa (exit 0)" "$([ "$RC_CEN" -eq 0 ] && echo 0 || echo 1)" "exit=$RC_CEN"
registrar "o cenario roda itens de verdade (nao 'sem output')" \
  "$(echo "$SAIDA_CEN" | grep -q 'RESULTADO: CENARIO_OK' && echo 0 || echo 1)" "sem RESULTADO na saida"

echo "-- estado do ambiente depois do cenario (read-only)"
if [ -f "$DIR/../db/estado_do_ambiente.sh" ]; then
  bash "$DIR/../db/estado_do_ambiente.sh" "$AMB" 2>&1 | tail -20
fi

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_DEDUP_AMBIENTE_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: TESTE_DEDUP_AMBIENTE_FALHOU ($ITENS itens, $FALHAS falhas)"
exit 1
