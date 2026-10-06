#!/usr/bin/env bash
# =====================================================================================
# verificar_fixture_smoke.sh '<prefixo do psql do alvo>'
#
# Aceite TRE-W1-E02-T01 (criterio 3): prova que a CONTAGEM POR TABELA do alvo confere com
# o ESPERADO DO FIXTURE `db/fixtures/smoke_dev.sql`, tabela a tabela.
#
# O esperado NAO e constante escrita a mao (constante envelhece e vira carimbo): este
# script sobe um PostgreSQL DESCARTAVEL, aplica a migration + o fixture, CONTA as 12
# tabelas ali dentro e usa essa contagem como linha de base. Depois conta as mesmas 12
# tabelas no alvo (prefixo psql informado) e compara. Se o fixture mudar, o esperado muda
# junto com ele na mesma execucao.
#
# Roda onde existe Docker com o alvo alcancavel (na VPS do ambiente — ADR-0008). So LE o
# alvo: nenhum DDL/DML e executado nele (as escritas ficam no container descartavel).
#
# Uso:
#   scripts/db/verificar_fixture_smoke.sh 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'
#
# Variaveis: TRE_FIXTURE_IMAGEM (padrao postgres:16), TRE_RAIZ
# =====================================================================================
set -uo pipefail

PREFIXO="${1:-}"
RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
FIXTURE="$RAIZ/db/fixtures/smoke_dev.sql"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
BASE="tre-fixture-base-$$-$RANDOM"
SENHA="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-fixture-XXXXXX")"
ITENS=0
FALHAS=0

# As 12 tabelas do Data Contract V1.0, na ordem do contrato. A lista e conferida contra o
# proprio fixture (item "todas tem INSERT"): tabela sem INSERT nao entraria na conta.
TABELAS=(organizations contacts signals research_runs pain_hypotheses scores interactions
         recommendations agent_runs outbox_events sync_events human_approvals)

ok()  { ITENS=$((ITENS + 1)); echo "OK     $*"; }
ko()  { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }
morrer() { echo "$*"; echo "RESULTADO: FIXTURE_FALHOU"; exit 1; }

limpar() { docker rm -f -v "$BASE" >/dev/null 2>&1; }
trap limpar EXIT

if [ -z "$PREFIXO" ]; then
  echo "uso: $0 '<prefixo do psql do alvo>'"
  echo "     ex.: $0 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'"
  exit 2
fi
[ -f "$FIXTURE" ]   || morrer "FALHOU fixture ausente: $FIXTURE"
[ -f "$MIGRATION" ] || morrer "FALHOU migration ausente: $MIGRATION"
command -v docker >/dev/null 2>&1 || morrer "FALHOU docker ausente: a linha de base precisa de container descartavel"

echo "=================================================================="
echo "-- CONTAGEM POR TABELA vs ESPERADO DO FIXTURE (TRE-W1-E02-T01)"
echo "-- alvo:    $PREFIXO"
echo "-- fixture: ${FIXTURE#"$RAIZ"/}"
echo "-- base:    container descartavel $BASE ($IMAGEM)"
echo "=================================================================="

# ------------------------------------------------------------------ lista x fixture
# O INSERT do fixture quebra linha antes da lista de colunas, entao a conferencia casa o
# nome da tabela com fronteira (nao aceita `organizations_x` no lugar de `organizations`).
sem_insert=()
for t in "${TABELAS[@]}"; do
  grep -qE "INSERT INTO sales_intelligence\.$t([^a-z_]|$)" "$FIXTURE" || sem_insert+=("$t")
done
chk_lista="as ${#TABELAS[@]} tabelas do contrato tem INSERT no fixture"
if [ "${#sem_insert[@]}" -eq 0 ]; then
  ok "$chk_lista"
else
  morrer "FALHOU tabela sem INSERT no fixture: ${sem_insert[*]}"
fi

# Espera ROBUSTA: a imagem oficial sobe um servidor TEMPORARIO durante a inicializacao e o
# derruba em seguida — quem confia em `pg_isready` aplica migration no vazio. Regra: SELECT 1
# tem de funcionar DUAS vezes, com intervalo.
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

# ------------------------------------------------------------------ linha de base
docker rm -f -v "$BASE" >/dev/null 2>&1
if docker run -d --name "$BASE" -e POSTGRES_PASSWORD="$SENHA" -e POSTGRES_USER=tre \
     -e POSTGRES_DB=sales_intelligence "$IMAGEM" >/dev/null 2>&1; then
  ok "container descartavel de linha de base criado"
else
  morrer "FALHOU nao consegui criar o container de linha de base"
fi
if esperar_postgres "$BASE" tre sales_intelligence; then
  ok "linha de base pronta (servidor definitivo, confirmado duas vezes)"
else
  morrer "FALHOU linha de base nao ficou pronta"
fi

aplicar_no_container() {  # <nome> <arquivo-do-host> <log>
  local destino="/tmp/tre_fixture_$(basename "$2")"
  docker cp "$2" "$1:$destino" >/dev/null 2>&1 || return 1
  docker exec "$1" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -f "$destino" >"$3" 2>&1
  local rc=$?
  docker exec "$1" rm -f "$destino" >/dev/null 2>&1
  return $rc
}

if aplicar_no_container "$BASE" "$MIGRATION" "$TMP/base_migration.log"; then
  ok "migration aplicada na linha de base (schema de verdade, nao vazio)"
else
  morrer "FALHOU migration na linha de base: $(tail -3 "$TMP/base_migration.log" | tr '\n' ' ')"
fi
if aplicar_no_container "$BASE" "$FIXTURE" "$TMP/base_fixture.log"; then
  ok "fixture aplicado na linha de base (o esperado sai daqui)"
else
  morrer "FALHOU fixture na linha de base: $(tail -3 "$TMP/base_fixture.log" | tr '\n' ' ')"
fi

declare -A ESPERADO=()
total_esperado=0
for t in "${TABELAS[@]}"; do
  n="$(docker exec "$BASE" psql -U tre -d sales_intelligence -Atc \
       "SELECT count(*) FROM sales_intelligence.$t" 2>/dev/null)"
  if ! [[ "$n" =~ ^[0-9]+$ ]]; then
    morrer "FALHOU nao consegui contar a linha de base em $t (obtido: '$n')"
  fi
  ESPERADO["$t"]="$n"
  total_esperado=$((total_esperado + n))
done
ok "linha de base contada: 12 tabelas, $total_esperado linhas no total"

# ------------------------------------------------------------------ alvo
read -r -a CMD <<< "$PREFIXO"
psql_alvo() { "${CMD[@]}" "$@" 2>/dev/null; }

resposta="$(psql_alvo -Atc 'SELECT current_database()')"
if [ -z "$resposta" ]; then
  morrer "FALHOU o alvo nao respondeu ao prefixo informado: $PREFIXO"
fi
ok "alvo respondeu (banco: $resposta)"

total_alvo=0
for t in "${TABELAS[@]}"; do
  n="$(psql_alvo -Atc "SELECT count(*) FROM sales_intelligence.$t")"
  if ! [[ "$n" =~ ^[0-9]+$ ]]; then
    ko "tabela $t: nao consegui contar no alvo (obtido: '$n')"
    continue
  fi
  total_alvo=$((total_alvo + n))
  if [ "$n" = "${ESPERADO[$t]}" ]; then
    ok "tabela $t: alvo=$n == esperado do fixture=${ESPERADO[$t]}"
  else
    ko "tabela $t: alvo=$n != esperado do fixture=${ESPERADO[$t]}"
  fi
done

if [ "$total_alvo" = "$total_esperado" ]; then
  ok "total de linhas por tabela: alvo=$total_alvo == esperado do fixture=$total_esperado"
else
  ko "total de linhas por tabela: alvo=$total_alvo != esperado do fixture=$total_esperado"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: FIXTURE_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: FIXTURE_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
