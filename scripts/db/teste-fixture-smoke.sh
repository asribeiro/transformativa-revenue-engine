#!/usr/bin/env bash
# =====================================================================================
# teste-fixture-smoke.sh [raiz_do_repo]
#
# PROVA, numa maquina com Docker e SEM tocar em nenhum ambiente real, que a aplicacao da
# massa de smoke e a conferencia de contagem por tabela (TRE-W1-E02-T01) tem dente:
#   1. sobe um PostgreSQL descartavel
#   2. aplica a migration + o fixture (o mesmo `db/fixtures/smoke_dev.sql` do ambiente)
#   3. verificador contra esse container -> FIXTURE_OK (contagem bate)
#   4. TESTE NEGATIVO 1: linha A MAIS no alvo      -> verificador REPROVA (exit 1)
#   5. TESTE NEGATIVO 2: linha A MENOS no alvo     -> verificador REPROVA (exit 1)
#   6. reaplicar o fixture desfaz a remocao        -> verificador APROVA de novo
#   7. rollback da massa (smoke_dev_rollback.sql)  -> 0 linhas nas 12 tabelas
#   8. reaplicar o fixture depois do rollback      -> volta a APROVAR (reversivel)
#   9. derruba tudo (inclusive em falha)
#
# Sem os passos 4 e 5 o verificador seria carimbo: passaria sempre.
#
# Variaveis: TRE_FIXTURE_IMAGEM (padrao postgres:16), TRE_RAIZ
# =====================================================================================
set -uo pipefail

RAIZ_REPO="${1:-${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
SERVICO="tre-fixture-teste-$$-$RANDOM"
SENHA="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-fixture-teste-XXXXXX")"
PREFIXO="docker exec $SERVICO psql -U tre -d sales_intelligence"
ITENS=0
FALHAS=0

ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

limpar() { docker rm -f -v "$SERVICO" >/dev/null 2>&1; echo; echo "artefatos do teste em: $TMP"; }
trap limpar EXIT

echo "=================================================================="
echo "-- TESTE DA MASSA DE SMOKE (TRE-W1-E02-T01)"
echo "-- repo:    $RAIZ_REPO"
echo "-- imagem:  $IMAGEM"
echo "-- alvo:    $SERVICO (descartavel)"
echo "=================================================================="

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

aplicar() {  # <arquivo-do-host> <log>
  local destino="/tmp/tre_fixture_$(basename "$1")"
  docker cp "$1" "$SERVICO:$destino" >/dev/null 2>&1 || return 1
  docker exec "$SERVICO" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -f "$destino" >"$2" 2>&1
  local rc=$?
  docker exec "$SERVICO" rm -f "$destino" >/dev/null 2>&1
  return $rc
}

verificar() {  # <log>  -> deixa o exit code do verificador
  bash "$RAIZ_REPO/scripts/db/verificar_fixture_smoke.sh" "$PREFIXO" >"$1" 2>&1
}

# 1. container descartavel
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

# 2. migration + fixture (os mesmos arquivos versionados que o ambiente recebe)
if aplicar "$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql" "$TMP/migration.log"; then
  ok "migration aplicada"
else
  ko "migration falhou: $(tail -3 "$TMP/migration.log" | tr '\n' ' ')"
  echo "RESULTADO: TESTE_FALHOU"; exit 1
fi
if aplicar "$RAIZ_REPO/db/fixtures/smoke_dev.sql" "$TMP/fixture.log"; then
  ok "fixture aplicado sem erro (ON_ERROR_STOP=1)"
else
  ko "fixture falhou: $(tail -3 "$TMP/fixture.log" | tr '\n' ' ')"
  echo "RESULTADO: TESTE_FALHOU"; exit 1
fi
if aplicar "$RAIZ_REPO/db/fixtures/smoke_dev.sql" "$TMP/fixture2.log"; then
  ok "fixture e IDEMPOTENTE (segunda aplicacao sem erro)"
else
  ko "segunda aplicacao do fixture falhou: $(tail -3 "$TMP/fixture2.log" | tr '\n' ' ')"
fi

# 3. verificador contra alvo integro
if verificar "$TMP/positivo.log"; then
  ok "verificador APROVA alvo integro ($(grep -c '^OK ' "$TMP/positivo.log") itens OK)"
else
  ko "verificador REPROVOU um alvo integro — verificador quebrado"
  tail -20 "$TMP/positivo.log" | sed 's/^/        /'
fi

# 4. negativo: linha A MAIS no alvo
if docker exec "$SERVICO" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -c \
     "INSERT INTO sales_intelligence.organizations (id, legal_name) \
      VALUES ('ffffffff-ffff-ffff-ffff-ffffffffffff', 'Linha Extra Do Teste Negativo')" >/dev/null 2>&1; then
  if verificar "$TMP/negativo_mais.log"; then
    ko "teste negativo: linha A MAIS foi aprovada — o verificador nao vale nada"
  else
    ok "teste negativo: linha A MAIS REPROVADA ($(grep -c '^FALHOU' "$TMP/negativo_mais.log") falha(s) apontada(s))"
  fi
  docker exec "$SERVICO" psql -U tre -d sales_intelligence -q -c \
    "DELETE FROM sales_intelligence.organizations WHERE id='ffffffff-ffff-ffff-ffff-ffffffffffff'" >/dev/null 2>&1
else
  ko "nao consegui inserir a linha extra do teste negativo"
fi

# 5. negativo: linha A MENOS no alvo
if docker exec "$SERVICO" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -c \
     "DELETE FROM sales_intelligence.scores WHERE id='88888888-8888-8888-8888-888888888888'" >/dev/null 2>&1; then
  if verificar "$TMP/negativo_menos.log"; then
    ko "teste negativo: linha A MENOS foi aprovada — o verificador nao vale nada"
  else
    ok "teste negativo: linha A MENOS REPROVADA"
  fi
else
  ko "nao consegui remover a linha do teste negativo"
fi

# 6. a massa volta a bater depois de desfazer a mutacao? O fixture e idempotente e
# REINSERE a linha removida (o INSERT e o mesmo; ON CONFLICT DO NOTHING so evita duplicar).
if aplicar "$RAIZ_REPO/db/fixtures/smoke_dev.sql" "$TMP/fixture3.log"; then
  if verificar "$TMP/reverificacao.log"; then
    ok "reaplicacao do fixture desfaz a remocao e a contagem volta a APROVAR"
  else
    ko "reverificacao do alvo voltou a reprovar depois de reaplicar o fixture"
    tail -20 "$TMP/reverificacao.log" | sed 's/^/        /'
  fi
else
  ko "reaplicacao do fixture falhou: $(tail -3 "$TMP/fixture3.log" | tr '\n' ' ')"
fi

# 7. rollback da massa (db/fixtures/smoke_dev_rollback.sql): some tudo, sem violar FK
contar_total() {
  local t n total=0
  for t in organizations contacts signals research_runs pain_hypotheses scores interactions \
           recommendations agent_runs outbox_events sync_events human_approvals; do
    n=$(docker exec "$SERVICO" psql -U tre -d sales_intelligence -Atc \
        "SELECT count(*) FROM sales_intelligence.$t" 2>/dev/null)
    total=$((total + ${n:-0}))
  done
  echo "$total"
}
if aplicar "$RAIZ_REPO/db/fixtures/smoke_dev_rollback.sql" "$TMP/rollback.log"; then
  ok "rollback da massa executou sem erro (ordem de DELETE respeita as FKs)"
else
  ko "rollback da massa falhou: $(tail -3 "$TMP/rollback.log" | tr '\n' ' ')"
fi
t_depois="$(contar_total)"
if [ "$t_depois" = "0" ]; then
  ok "depois do rollback: 0 linhas nas 12 tabelas"
else
  ko "depois do rollback sobraram $t_depois linha(s)"
fi
if aplicar "$RAIZ_REPO/db/fixtures/smoke_dev.sql" "$TMP/fixture4.log" && verificar "$TMP/reverificacao2.log"; then
  ok "massa reaplicada depois do rollback: contagem volta a APROVAR (rollback reversivel)"
else
  ko "massa nao voltou a bater depois do rollback"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: TESTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
