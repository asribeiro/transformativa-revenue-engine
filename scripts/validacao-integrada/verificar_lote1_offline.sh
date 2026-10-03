#!/usr/bin/env bash
# =============================================================================
# Validacao integrada OFFLINE — lote 1 das entregas `done` do board TRE.
#
# Roda os portoes DETERMINISTICOS e OFFLINE das entregas que NAO exigem ambiente
# vivo, credencial ou producao, e reporta — sem passar por omissao — a entrega
# de migracao cujo aceite exige o banco de dev (nao provada aqui: BLOCKED).
#
# Nao toca banco, rede, credencial, producao nem runtime vivo: le arquivos do
# repositorio e faz contas. Duas passadas tem de produzir saida IDENTICA.
#
# Cada card do lote, seu eixo de risco e o portao:
#   t_722b6cbd  DADO        verificar_contrato_dados.py (Data Contract V1.0)
#   t_595dc9be  DADO        teste_dedup_sintetico.sh (dedup strong identifiers)
#   t_4be20bcc  CREDENCIAL  secret_scan.sh + hook pre-commit (gestao de secrets)
#   t_967965f0  ISOLAMENTO  verificar_estrutura.sh (verificador sem codigo morto)
#   t_39838c5b  MIGRACAO    exige `aplicar_migracoes.sh dev` -> BLOCKED offline
#
# Uso: bash scripts/validacao-integrada/verificar_lote1_offline.sh
# =============================================================================
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$RAIZ"

FALHAS=0
APROVADOS=0
log() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------- t_722b6cbd
log "## t_722b6cbd (DADO) — scripts/verificar_contrato_dados.py"
SAIDA_A="$(python3 scripts/verificar_contrato_dados.py 2>&1)"; RC_A=$?
log "$SAIDA_A"
if [ "$RC_A" -eq 0 ] && printf '%s' "$SAIDA_A" | grep -q '^RESULTADO: PASS (26 itens, 0 falhas)$'; then
  R_A="PASS"; APROVADOS=$((APROVADOS+1))
else
  R_A="FAIL"; FALHAS=$((FALHAS+1))
fi

# ---------------------------------------------------------------- t_595dc9be
log "## t_595dc9be (DADO) — scripts/dedup/teste_dedup_sintetico.sh"
SAIDA_B="$(bash scripts/dedup/teste_dedup_sintetico.sh 2>&1)"; RC_B=$?
log "$SAIDA_B"
if [ "$RC_B" -eq 0 ] && printf '%s' "$SAIDA_B" | grep -q '^RESULTADO: TESTE_DEDUP_SINTETICO_OK'; then
  R_B="PASS"; APROVADOS=$((APROVADOS+1))
else
  R_B="FAIL"; FALHAS=$((FALHAS+1))
fi

# ---------------------------------------------------------------- t_4be20bcc
log "## t_4be20bcc (CREDENCIAL) — scripts/secret_scan.sh + hook pre-commit"
SAIDA_C="$(bash scripts/secret_scan.sh 2>&1)"; RC_C=$?
log "$SAIDA_C"
PROBE="_probe_secret_lote1.txt"
# Token montado em RUNTIME: o literal com forma de segredo (ghp_+30) nao fica no
# arquivo — senao o proprio hook/secret_scan (que varre o diff/arvore) barraria
# este verificador como se fosse segredo. O comportamento medido e o mesmo.
TOKEN="ghp_$(printf 'A%.0s' $(seq 1 30))"
printf 'PROBE=%s\n' "$TOKEN" > "$PROBE"
# Indice temporario: o verificador NAO toca no indice do repositorio (o alvo).
IDX="$(mktemp)"; export GIT_INDEX_FILE="$IDX"
git read-tree HEAD >/dev/null 2>&1
git add --intent-to-add "$PROBE" >/dev/null 2>&1
git add "$PROBE" >/dev/null 2>&1
SAIDA_HOOK="$(bash scripts/hooks/pre-commit 2>&1)"; RC_HOOK=$?
unset GIT_INDEX_FILE; rm -f "$IDX" "$PROBE"
log "hook pre-commit com segredo no staged: exit=$RC_HOOK (esperado 1)"
if [ "$RC_C" -eq 0 ] && printf '%s' "$SAIDA_C" | grep -q 'RESULTADO: PASS (nenhum segredo versionado)' \
   && [ "$RC_HOOK" -ne 0 ]; then
  R_C="PASS"; APROVADOS=$((APROVADOS+1))
else
  R_C="FAIL"; FALHAS=$((FALHAS+1))
fi

# ---------------------------------------------------------------- t_967965f0
log "## t_967965f0 (ISOLAMENTO) — scripts/verificar_estrutura.sh"
SAIDA_D="$(bash scripts/verificar_estrutura.sh 2>&1)"; RC_D=$?
log "$SAIDA_D"
OK_D=$(printf '%s\n' "$SAIDA_D" | grep -c '^OK')
# Prova de que o FIM do script NAO esta morto (era o defeito D04): o ultimo bloco
# de artefato versionado tem de aparecer na saida.
if [ "$RC_D" -eq 0 ] && printf '%s' "$SAIDA_D" | grep -q '^RESULTADO: PASS (0 falhas)$' \
   && printf '%s' "$SAIDA_D" | grep -q 'versionado docs/runbooks/pontuacao-preditiva.md'; then
  R_D="PASS"; APROVADOS=$((APROVADOS+1))
else
  R_D="FAIL"; FALHAS=$((FALHAS+1))
fi

# ---------------------------------------------------------------- t_39838c5b
log "## t_39838c5b (MIGRACAO) — BLOCKED offline"
SHA_MIGR="$(sha256sum db/migrations/0001_sales_intelligence_v1.sql | cut -d' ' -f1)"
log "sha256 do arquivo da migration (parte offline): $SHA_MIGR"
log "aceite exige \`scripts/db/aplicar_migracoes.sh dev --somente-checar\` em dev vivo -> NAO rodado"
R_E="BLOCKED"

# ------------------------------------------------------------------- resumo
log "=== RESULTADO DA VALIDACAO INTEGRADA OFFLINE (lote 1) ==="
printf 't_722b6cbd  DADO        %s\n' "$R_A"
printf 't_595dc9be  DADO        %s\n' "$R_B"
printf 't_4be20bcc  CREDENCIAL  %s\n' "$R_C"
printf 't_967965f0  ISOLAMENTO  %s\n' "$R_D"
printf 't_39838c5b  MIGRACAO    %s\n' "$R_E"
log "OK em verificar_estrutura.sh: $OK_D"
if [ "$FALHAS" -eq 0 ]; then
  log "RESULTADO: LOTE1_OK ($APROVADOS PASS, 0 FAIL, 1 BLOCKED)"
  exit 0
fi
log "RESULTADO: LOTE1_FALHOU ($FALHAS falhas)"
exit 1
