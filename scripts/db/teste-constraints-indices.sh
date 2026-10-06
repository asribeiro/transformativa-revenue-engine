#!/usr/bin/env bash
# =====================================================================================
# teste-constraints-indices.sh [raiz_do_repo]
#
# PROVA, numa maquina com Docker e SEM tocar em nenhum ambiente real, que o verificador de
# constraints e indices (TRE-W1-E03-T01, `scripts/db/verificar_constraints_indices.py`) tem
# DENTE: ele nao e carimbo.
#   1. sobe um PostgreSQL descartavel e aplica a migration congelada
#   2. verificador contra o alvo integro           -> PASS (30 indices, PK/FK/UNIQUE do contrato)
#   3. MUTACOES que o contrato proibe, uma a uma, cada uma DESFEITA e reverificada:
#        a) DROP INDEX idx_organizations_domain                    -> REPROVA (indice nomeado faltando)
#        b) RENAME idx_contacts_email                              -> REPROVA (nome fora do contrato)
#        c) mesmo nome, coluna errada (organizations(state) no lugar de (state, city))
#                                                                 -> REPROVA (colunas do indice)
#        d) DROP CONSTRAINT contacts_organization_id_fkey          -> REPROVA (FK faltando)
#        e) DROP CONSTRAINT sync_events_idempotency_key_key        -> REPROVA (UNIQUE faltando)
#        f) DROP CONSTRAINT organizations_pkey                     -> REPROVA (PK faltando)
#        g) CREATE INDEX extra (organizations(city))               -> REPROVA (indice sobrando)
#   4. derruba tudo (inclusive em falha)
#
# Sem o passo 3 o verificador passaria sempre e nao mediria nada. Sem o passo 2 ele poderia
# reprovar qualquer coisa.
#
# Variaveis: TRE_FIXTURE_IMAGEM (padrao postgres:16), TRE_RAIZ
# =====================================================================================
set -uo pipefail

RAIZ_REPO="${1:-${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
MIGRATION="$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql"
VERIF="$RAIZ_REPO/scripts/db/verificar_constraints_indices.py"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
SERVICO="tre-constraints-teste-$$-$RANDOM"
SENHA="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-constraints-teste-XXXXXX")"
PREFIXO="docker exec $SERVICO psql -U tre -d sales_intelligence"
ITENS=0
FALHAS=0

ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

limpar() { docker rm -f -v "$SERVICO" >/dev/null 2>&1; echo; echo "artefatos do teste em: $TMP"; }
trap limpar EXIT

echo "=================================================================="
echo "-- TESTE DE CONSTRAINTS E INDICES (TRE-W1-E03-T01)"
echo "-- repo:    $RAIZ_REPO"
echo "-- imagem:  $IMAGEM"
echo "-- alvo:    $SERVICO (descartavel)"
echo "=================================================================="
[ -f "$MIGRATION" ] || { ko "migration ausente: $MIGRATION"; echo "RESULTADO: TESTE_FALHOU"; exit 1; }
[ -f "$VERIF" ] || { ko "verificador ausente: $VERIF"; echo "RESULTADO: TESTE_FALHOU"; exit 1; }
command -v docker >/dev/null 2>&1 || { ko "docker ausente"; echo "RESULTADO: TESTE_FALHOU"; exit 1; }

esperar_postgres() {  # <nome> <usuario> <banco>
  local tentativas=0
  while [ "$tentativas" -lt 90 ]; do
    tentativas=$((tentativas + 1))
    if docker exec "$1" psql -U "$2" -d "$3" -tAc 'SELECT 1' >/dev/null 2>&1; then
      sleep 3
      docker exec "$1" psql -U "$2" -d "$3" -tAc 'SELECT 1' >/dev/null 2>&1 && return 0
    fi
    sleep 1
  done
  return 1
}

# 1. container descartavel + migration congelada
if docker run -d --name "$SERVICO" -e POSTGRES_PASSWORD="$SENHA" -e POSTGRES_USER=tre \
     -e POSTGRES_DB=sales_intelligence "$IMAGEM" >/dev/null 2>&1; then
  ok "container descartavel criado"
else
  ko "nao consegui criar o container descartavel"; echo "RESULTADO: TESTE_FALHOU"; exit 1
fi
if esperar_postgres "$SERVICO" tre sales_intelligence; then
  ok "container pronto (servidor definitivo, confirmado duas vezes)"
else
  ko "container nao ficou pronto"; echo "RESULTADO: TESTE_FALHOU"; exit 1
fi

destino="/tmp/tre_constraints_$(basename "$MIGRATION")"
if docker cp "$MIGRATION" "$SERVICO:$destino" >/dev/null 2>&1 && \
   docker exec "$SERVICO" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -f "$destino" \
     >"$TMP/migration.log" 2>&1; then
  ok "migration congelada aplicada (o alvo nasce do proprio contrato)"
else
  ko "migration falhou: $(tail -3 "$TMP/migration.log" 2>/dev/null | tr '\n' ' ')"
  echo "RESULTADO: TESTE_FALHOU"; exit 1
fi

read -r -a CMD <<< "$PREFIXO"
psql_alvo() { "${CMD[@]}" "$@" 2>/dev/null; }
verificar() { python3 "$VERIF" --banco "$PREFIXO" >"$1" 2>&1; }

# 2. alvo integro tem de APROVAR (senao o verificador esta quebrado)
if verificar "$TMP/positivo.log"; then
  ok "verificador APROVA alvo integro ($(grep -c '^OK ' "$TMP/positivo.log") itens OK, 30 indices)"
else
  ko "verificador REPROVOU um alvo integro — verificador quebrado"
  tail -20 "$TMP/positivo.log" | sed 's/^/        /'
fi

# 3. mutacoes: cada uma tem de REPROVAR apontando o motivo, e ser reversivel
prova() {  # <n> <rotulo> <regex esperado no log> <sql da mutacao> <sql da restauracao>
  local n="$1" rot="$2" esperado="$3" mut="$4" rest="$5"
  if ! psql_alvo -v ON_ERROR_STOP=1 -q -c "$mut" >/dev/null; then
    ko "$n. $rot: nao consegui aplicar a mutacao no container descartavel"
    return
  fi
  if verificar "$TMP/neg_${n}.log"; then
    ko "$n. $rot: o verificador APROVOU o alvo mutado — o verificador nao vale nada"
  elif grep '^FALHOU' "$TMP/neg_${n}.log" | grep -qE "$esperado"; then
    ok "$n. $rot: REPROVADO e apontou o motivo ($(grep -c '^FALHOU' "$TMP/neg_${n}.log") falha(s))"
    grep '^FALHOU' "$TMP/neg_${n}.log" | sed 's/  -> .*//' | sort -u | sed 's/^/        /'
  else
    ko "$n. $rot: REPROVADO, mas nao apontou '$esperado' — reprova pelo motivo errado"
    grep '^FALHOU' "$TMP/neg_${n}.log" | sed 's/^/        /'
  fi
  if psql_alvo -v ON_ERROR_STOP=1 -q -c "$rest" >/dev/null && verificar "$TMP/pos_${n}.log"; then
    ok "$n. $rot: mutacao desfeita, o verificador volta a APROVAR (constatacao reversivel)"
  else
    ko "$n. $rot: o alvo nao voltou a aprovar depois de desfazer a mutacao"
  fi
}

prova a "DROP INDEX idx_organizations_domain" "idx_organizations_domain" \
  "DROP INDEX sales_intelligence.idx_organizations_domain" \
  "CREATE INDEX idx_organizations_domain ON sales_intelligence.organizations (domain)"

prova b "RENAME idx_contacts_email (nome fora do contrato)" "idx_contacts_email_renomeado" \
  "ALTER INDEX sales_intelligence.idx_contacts_email RENAME TO idx_contacts_email_renomeado" \
  "ALTER INDEX sales_intelligence.idx_contacts_email_renomeado RENAME TO idx_contacts_email"

prova c "mesmo nome, coluna errada em idx_organizations_state_city" "idx_organizations_state_city" \
  "DROP INDEX sales_intelligence.idx_organizations_state_city; CREATE INDEX idx_organizations_state_city ON sales_intelligence.organizations (state)" \
  "DROP INDEX sales_intelligence.idx_organizations_state_city; CREATE INDEX idx_organizations_state_city ON sales_intelligence.organizations (state, city)"

prova d "DROP CONSTRAINT FK contacts.organization_id" "contacts.*organization_id" \
  "ALTER TABLE sales_intelligence.contacts DROP CONSTRAINT contacts_organization_id_fkey" \
  "ALTER TABLE sales_intelligence.contacts ADD CONSTRAINT contacts_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES sales_intelligence.organizations(id)"

prova e "DROP CONSTRAINT UNIQUE sync_events.idempotency_key" "sync_events.*idempotency_key" \
  "ALTER TABLE sales_intelligence.sync_events DROP CONSTRAINT sync_events_idempotency_key_key" \
  "ALTER TABLE sales_intelligence.sync_events ADD CONSTRAINT sync_events_idempotency_key_key UNIQUE (idempotency_key)"

prova f "DROP CONSTRAINT PK human_approvals" "human_approvals.*(id|pkey)" \
  "ALTER TABLE sales_intelligence.human_approvals DROP CONSTRAINT human_approvals_pkey" \
  "ALTER TABLE sales_intelligence.human_approvals ADD CONSTRAINT human_approvals_pkey PRIMARY KEY (id)"

prova g "indice a mais no alvo (organizations(city))" "idx_intruso_no_contrato" \
  "CREATE INDEX idx_intruso_no_contrato ON sales_intelligence.organizations (city)" \
  "DROP INDEX sales_intelligence.idx_intruso_no_contrato"

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: TESTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
