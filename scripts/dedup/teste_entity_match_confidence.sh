#!/usr/bin/env bash
# =====================================================================================
# TRE-W1-E04-T02 — prova do campo `entity_match_confidence`: calculado, por faixa e
# PERSISTIDO (lido de volta do banco), coerente com o limiar de merge do E04-T01.
#
# Nesta ordem:
#   1) o modelo de faixas e impresso: a fronteira da faixa de merge tem de ser o limiar do
#      CONTRATO (0,95) e o piso de candidatura 0,80 (decisao D4 do E04-T01); a faixa de cada
#      ponto e conferida contra o PROPRIO modelo (nunca contra string constante) e, numa copia
#      com o limiar em 0,90, tabela e detalhe tem de se mover JUNTOS (classe do defeito D02);
#   2) a suite do motor passa (itens de faixa, de score, de coerencia score<->faixa<->decisao
#      e de persistencia no registro auditado);
#   3) prova negativa: sabotar a persistencia, a coerencia, o limiar e a linha de detalhe TEM
#      de reprovar a suite (sabotagem que nao reprova = teste que passa por construcao);
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

detalhe_do_modelo() { # <caminho-do-motor> -> "0,94 cai em <FAIXA> (<DECISAO>); 0,95 cai em ..."
  # A expectativa da faixa de cada ponto vem do PROPRIO modelo do motor (faixa_de_confianca),
  # nunca de string escrita aqui: teste que assere constante prova por construcao (defeito D02
  # do T02, mesma classe do D04 do E04-T01).
  python3 - "$1" <<'PY'
import importlib.util, sys
espec = importlib.util.spec_from_file_location("motor_dedup", sys.argv[1])
motor = importlib.util.module_from_spec(espec)
espec.loader.exec_module(motor)
partes = []
for valor in (0.94, 0.95):
    faixa = motor.faixa_de_confianca(valor)
    partes.append(f"{valor:.2f}".replace(".", ",") + f" cai em {faixa['faixa']} ({faixa['decisao']})")
print("; ".join(partes))
PY
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
DETALHE_MODELO="$(detalhe_do_modelo "$MOTOR")"
registrar "teste de faixa do aceite DERIVADO do modelo: 0,94 e 0,95 dizem o que faixa_de_confianca diz" \
  "$([ -n "$DETALHE_MODELO" ] && echo "$SAIDA_FAIXAS" | grep -qF "$DETALHE_MODELO" && echo 0 || echo 1)" \
  "esperado do modelo: $DETALHE_MODELO"

echo "== 1b) a linha de detalhe acompanha o limiar do contrato (prova da classe do defeito D02)"
TMP_FAIXAS="$(mktemp -d)"
mkdir -p "$TMP_FAIXAS/scripts/dedup" "$TMP_FAIXAS/docs/data"
cp "$MOTOR" "$TMP_FAIXAS/scripts/dedup/"
cp "$DIR/../../docs/data/data_contract_v1.json" "$TMP_FAIXAS/docs/data/"
# mutacao SO na copia: o contrato do repo nao e tocado
python3 - "$TMP_FAIXAS/docs/data/data_contract_v1.json" <<'PY'
import json, sys
caminho = sys.argv[1]
dados = json.load(open(caminho, encoding="utf-8"))
dados["dedup"]["auto_merge_threshold"] = 0.90
json.dump(dados, open(caminho, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
SAIDA_MUT="$(python3 "$TMP_FAIXAS/scripts/dedup/deduplicar_organizacoes.py" --faixas 2>&1)"
RC_MUT=$?
DETALHE_MUT="$(detalhe_do_modelo "$TMP_FAIXAS/scripts/dedup/deduplicar_organizacoes.py")"
rm -rf "$TMP_FAIXAS"
echo "-- copia com dedup.auto_merge_threshold=0.90"
echo "$SAIDA_MUT"
registrar "com o limiar em 0,90 o motor imprime as faixas movidas (exit 0)" \
  "$([ "$RC_MUT" -eq 0 ] && echo 0 || echo 1)" "exit=$RC_MUT"
registrar "a tabela move a faixa de merge para [0.90, 1.00]" \
  "$(echo "$SAIDA_MUT" | grep -q 'MERGE_AUTOMATICO  \[0.90, 1.00\]' && echo 0 || echo 1)"
registrar "e a linha de detalhe move JUNTO (0,94 na faixa que o modelo mutado diz, sem nome fixo)" \
  "$([ -n "$DETALHE_MUT" ] && echo "$SAIDA_MUT" | grep -qF "$DETALHE_MUT" && echo 0 || echo 1)" \
  "esperado do modelo mutado: $DETALHE_MUT"

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
for SABOTAGEM in persistencia coerencia limiar detalhe; do
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
