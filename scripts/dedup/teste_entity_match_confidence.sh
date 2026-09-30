#!/usr/bin/env bash
# =====================================================================================
# TRE-W1-E04-T02 — prova do campo `entity_match_confidence`: calculado, por faixa e
# PERSISTIDO (lido de volta do banco), coerente com o limiar de merge do E04-T01.
#
# Nesta ordem:
#   1) o modelo de faixas e impresso: a fronteira da faixa de merge tem de ser o limiar do
#      CONTRATO (0,95) e o piso de candidatura 0,80 (decisao D4 do E04-T01);
#   2) a suite do motor passa (itens de faixa, de score, de coerencia score<->faixa<->decisao
#      e de persistencia no registro auditado);
#   3) prova negativa: sabotar a persistencia, a coerencia e o limiar TEM de reprovar a suite
#      (sabotagem que nao reprova = teste que passa por construcao);
#   4) cenario REAL no ambiente (default dev): o campo e lido DE VOLTA do banco no registro
#      auditado do merge (`sync_events`) e na pendencia da fila humana (`human_approvals`).
#
# Roda na VPS do ambiente (ADR-0008). Nao toca producao nem homologacao: o motor recusa
# `--ambiente prod` (ADR-005) e o cenario limpa a propria massa.
#
# Uso: bash scripts/dedup/teste_entity_match_confidence.sh [dev]
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

echo "== 1) faixas de entity_match_confidence (fronteira derivada do contrato)"
SAIDA_FAIXAS="$(python3 "$MOTOR" --faixas 2>&1)"
RC_FAIXAS=$?
echo "$SAIDA_FAIXAS"
registrar "modelo de faixas impresso pelo motor (exit 0)" "$([ "$RC_FAIXAS" -eq 0 ] && echo 0 || echo 1)" "exit=$RC_FAIXAS"
registrar "faixa de merge comeca no limiar do contrato (MERGE_AUTOMATICO [0.95, 1.00])" \
  "$(echo "$SAIDA_FAIXAS" | grep -q 'MERGE_AUTOMATICO  \[0.95, 1.00\]' && echo 0 || echo 1)"
registrar "faixa de revisao e [0.80, 0.95) com REVIEW_REQUIRED (piso de candidatura do E04-T01)" \
  "$(echo "$SAIDA_FAIXAS" | grep -q 'REVISAO_HUMANA    \[0.80, 0.95)' && echo 0 || echo 1)"
registrar "teste de faixa do aceite no ponto exato: 0,94 nao mescla e 0,95 mescla" \
  "$(echo "$SAIDA_FAIXAS" | grep -q '0,94 cai em REVISAO_HUMANA (REVIEW_REQUIRED); 0,95 cai em MERGE_AUTOMATICO (MERGE)' && echo 0 || echo 1)"

echo "== 2) suite do motor (faixa, score, coerencia e persistencia)"
SAIDA_SUITE="$(python3 "$MOTOR" --autoteste 2>&1)"
RC_SUITE=$?
echo "$SAIDA_SUITE"
registrar "suite passa no alvo integro (exit 0)" "$([ "$RC_SUITE" -eq 0 ] && echo 0 || echo 1)" "exit=$RC_SUITE"
registrar "suite roda itens de verdade (nao 'sem output')" \
  "$(echo "$SAIDA_SUITE" | grep -q 'RESULTADO: TESTE_OK' && echo 0 || echo 1)" "sem RESULTADO na saida"
registrar "a suite cobre a faixa do aceite (0,94 nao mescla / 0,95 mescla, no caminho do merge)" \
  "$(echo "$SAIDA_SUITE" | grep -q 'aceite (faixa): no ponto exato' && echo 0 || echo 1)"
registrar "a suite cobre a persistencia do campo no registro auditado" \
  "$(echo "$SAIDA_SUITE" | grep -q 'persistencia: o registro de MERGE carrega entity_match_confidence' && echo 0 || echo 1)"
registrar "a suite cobre a coerencia score <-> faixa <-> decisao" \
  "$(echo "$SAIDA_SUITE" | grep -q 'coerencia: em todo par avaliado' && echo 0 || echo 1)"

echo "-- prova negativa: sabotar o alvo e exigir que a suite REPROVE"
for SABOTAGEM in persistencia coerencia limiar; do
  LOG="$(python3 "$MOTOR" --autoteste --sabotar "$SABOTAGEM" 2>&1)"
  RC=$?
  if [ "$RC" -ne 0 ] && echo "$LOG" | grep -q 'RESULTADO: TESTE_FALHOU'; then
    registrar "sabotagem '$SABOTAGEM' detectada pela suite (exit != 0)" 0
  else
    registrar "sabotagem '$SABOTAGEM' detectada pela suite (exit != 0)" 1 \
      "exit=$RC — a suite passou com o alvo quebrado (passaria por construcao)"
  fi
done

echo "== 3) cenario REAL no ambiente '$AMB' (campo lido de volta do banco)"
SAIDA_CEN="$(bash "$DIR/teste_dedup_ambiente.sh" "$AMB" 2>&1)"
RC_CEN=$?
echo "$SAIDA_CEN"
registrar "cenario no ambiente '$AMB' passa (exit 0)" "$([ "$RC_CEN" -eq 0 ] && echo 0 || echo 1)" "exit=$RC_CEN"
registrar "campo lido DE VOLTA do banco no merge auditado (entity_match_confidence + faixa)" \
  "$(echo "$SAIDA_CEN" | grep -q 'campo persistido e lido DE VOLTA do banco' && echo 0 || echo 1)"
registrar "campo canonico == alias do E04-T01 no registro persistido" \
  "$(echo "$SAIDA_CEN" | grep -q 'campo canonico e alias' && echo 0 || echo 1)"
registrar "campo persistido na pendencia da fila humana (0,94 na faixa REVISAO_HUMANA)" \
  "$(echo "$SAIDA_CEN" | grep -q 'o campo canonico persistido na pendencia e 0,94 na faixa REVISAO_HUMANA' && echo 0 || echo 1)"
registrar "guarda de ambiente no cenario: 'prod' recusado com ADR-005" \
  "$(echo "$SAIDA_CEN" | grep -q 'ambiente .prod. recusado pelo motor' && echo 0 || echo 1)"
registrar "ambiente volta ao estado anterior depois do cenario (contagem identica)" \
  "$(echo "$SAIDA_CEN" | grep -q 'ambiente volta ao estado anterior' && echo 0 || echo 1)"

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_ENTITY_MATCH_CONFIDENCE_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: TESTE_ENTITY_MATCH_CONFIDENCE_FALHOU ($ITENS itens, $FALHAS falhas)"
exit 1
