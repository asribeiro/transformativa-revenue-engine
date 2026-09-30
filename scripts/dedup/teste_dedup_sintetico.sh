#!/usr/bin/env bash
# =====================================================================================
# TRE-W1-E04-T01 — prova da deduplicacao por identificadores fortes, SEM banco.
#
# Duas coisas, nesta ordem:
#   1) roda a suite sintetica do motor (casos 0,94/0,95, cada identificador forte
#      isoladamente e em conjunto, negativos, auditoria e governanca do limiar);
#      espera exit 0;
#   2) quebra o alvo DE PROPOSITO em quatro frentes e EXIGE que a suite reprove
#      (exit != 0). Sabotagem que nao reprova significa teste que passa por
#      construcao — e isso aqui vira falha.
#
# Nao toca banco, rede ou producao: le o contrato e faz contas.
# =====================================================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOTOR="$DIR/deduplicar_organizacoes.py"
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

echo "== deduplicacao strong identifiers — suite sintetica ($MOTOR)"
SAIDA_SUITE="$(python3 "$MOTOR" --autoteste 2>&1)"
RC_SUITE=$?
echo "$SAIDA_SUITE"
registrar "suite sintetica passa no alvo integro (exit 0)" "$([ "$RC_SUITE" -eq 0 ] && echo 0 || echo 1)" "exit=$RC_SUITE"
registrar "suite sintetica roda itens de verdade (nao 'sem output')" \
  "$(echo "$SAIDA_SUITE" | grep -q 'RESULTADO: TESTE_OK' && echo 0 || echo 1)" "sem RESULTADO na saida"

echo "-- prova negativa: sabotar o alvo e exigir que a suite REPROVE"
for SABOTAGEM in limiar auditoria identificadores fraco; do
  LOG="$(python3 "$MOTOR" --autoteste --sabotar "$SABOTAGEM" 2>&1)"
  RC=$?
  if [ "$RC" -ne 0 ] && echo "$LOG" | grep -q 'RESULTADO: TESTE_FALHOU'; then
    registrar "sabotagem '$SABOTAGEM' detectada pela suite (exit != 0)" 0
  else
    registrar "sabotagem '$SABOTAGEM' detectada pela suite (exit != 0)" 1 \
      "exit=$RC — a suite passou com o alvo quebrado (passaria por construcao)"
  fi
done

echo "-- governanca do limiar (lido do contrato, nao do codigo)"
LIMIAR="$(python3 "$MOTOR" --limiar 2>&1)"
echo "$LIMIAR"
registrar "limiar vigente e 0,95 e vem do contrato" \
  "$(echo "$LIMIAR" | grep -q 'limiar de merge em vigor: 0.9500' && echo 0 || echo 1)" "saida: $LIMIAR"

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_DEDUP_SINTETICO_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: TESTE_DEDUP_SINTETICO_FALHOU ($ITENS itens, $FALHAS falhas)"
exit 1
