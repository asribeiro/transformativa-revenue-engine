#!/usr/bin/env bash
# =====================================================================================
# teste-backup-restore.sh [raiz_do_repo]
#
# PROVA o ciclo completo numa maquina com Docker, sem tocar em nenhum ambiente real:
#   1. sobe um PostgreSQL descartavel
#   2. aplica a migration do Data Contract V1 + a massa de smoke (as 12 tabelas)
#   3. roda o backup de verdade
#   4. roda o TESTE DE RESTORE num container novo e compara tabela por tabela
#   5. TESTE NEGATIVO: dump truncado tem de ser REPROVADO (senao o verificador e carimbo)
#   6. derruba tudo (inclusive em falha)
#
# Variaveis: TRE_BACKUP_IMAGEM (padrao postgres:16)
# =====================================================================================
set -uo pipefail

RAIZ_REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IMAGEM="${TRE_BACKUP_IMAGEM:-postgres:16}"
SERVICO="tre-smoke-src-$$-$RANDOM"
SENHA="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-smoke-backup-XXXXXX")"
ITENS=0
FALHAS=0

ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

# Espera ROBUSTA pelo PostgreSQL: a imagem oficial sobe um servidor TEMPORARIO durante a
# inicializacao e o derruba em seguida. `pg_isready` responde OK nesse servidor temporario —
# quem confiar nele aplica migration no vazio ("the database system is shutting down").
# Regra: SELECT 1 tem de funcionar DUAS vezes, com intervalo, para valer.
esperar_postgres() {
  local nome="$1" usuario="$2" banco="$3" tentativas=0
  while [ "$tentativas" -lt 90 ]; do
    tentativas=$((tentativas + 1))
    if docker exec "$nome" psql -U "$usuario" -d "$banco" -tAc 'SELECT 1' >/dev/null 2>&1; then
      sleep 3
      docker exec "$nome" psql -U "$usuario" -d "$banco" -tAc 'SELECT 1' >/dev/null 2>&1 && return 0
    fi
    sleep 1
  done
  return 1
}

limpar() {
  docker rm -f "$SERVICO" >/dev/null 2>&1
  echo
  echo "artefatos do teste em: $TMP"
}
trap limpar EXIT

echo "=================================================================="
echo "-- TESTE DE BACKUP E RESTORE (TRE-W0-E01-T03)"
echo "-- repo:      $RAIZ_REPO"
echo "-- imagem:    $IMAGEM"
echo "-- origem:    $SERVICO (descartavel)"
echo "-- destino:   $TMP"
echo "=================================================================="

# 1. origem descartavel
if docker run -d --name "$SERVICO" -e POSTGRES_PASSWORD="$SENHA" -e POSTGRES_USER=tre \
     -e POSTGRES_DB=sales_intelligence "$IMAGEM" >/dev/null 2>&1; then
  ok "container de origem criado"
else
  ko "nao consegui criar o container de origem"
  echo "RESULTADO: TESTE_FALHOU"; exit 1
fi

if esperar_postgres "$SERVICO" tre sales_intelligence; then
  ok "origem pronta (servidor definitivo, nao o temporario da inicializacao)"
else
  ko "origem nao ficou pronta"; echo "RESULTADO: TESTE_FALHOU"; exit 1
fi

# 2. migration + massa de smoke (com retentativa: a origem pode estar saindo do ciclo de init)
migracao_ok=0
for tentativa in 1 2 3 4 5; do
  if docker exec -i "$SERVICO" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 \
       <"$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql" >"$TMP/migration.log" 2>&1; then
    migracao_ok=1; break
  fi
  sleep 3
done
if [ "$migracao_ok" = "1" ]; then
  ok "migration aplicada em PostgreSQL real (12 tabelas + indices)"
else
  ko "migration falhou: $(tail -3 "$TMP/migration.log" | tr '\n' ' ')"
  echo "RESULTADO: TESTE_FALHOU (sem migration nao ha o que provar)"; exit 1
fi
if docker exec -i "$SERVICO" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 \
     <"$RAIZ_REPO/db/fixtures/smoke_dev.sql" >"$TMP/fixture.log" 2>&1; then
  ok "massa de smoke inserida nas 12 tabelas"
else
  ko "massa de smoke falhou: $(tail -3 "$TMP/fixture.log" | tr '\n' ' ')"
fi
docker exec "$SERVICO" psql -U tre -d sales_intelligence -c 'ANALYZE' >/dev/null 2>&1

# 3. backup de verdade
if TRE_PG_SERVICO="$SERVICO" TRE_PG_USER=tre TRE_PG_DB=sales_intelligence \
     TRE_BACKUP_DIR="$TMP/backup" bash "$RAIZ_REPO/scripts/backup/backup-tre.sh" dev \
     >"$TMP/backup.log" 2>&1; then
  ok "backup concluido (BACKUP_OK)"
else
  ko "backup falhou: $(grep FALHOU "$TMP/backup.log" | head -3 | tr '\n' ' ')"
fi

DIR="$(ls -d "$TMP"/backup/tre_dev_* 2>/dev/null | head -1)"
[ -n "$DIR" ] && ok "artefato do backup gerado ($(basename "$DIR"))" || ko "nenhum artefato de backup encontrado"

# 4. TESTE DE RESTORE (container novo)
if bash "$RAIZ_REPO/scripts/backup/verificar-backup.sh" "$DIR" >"$TMP/verificacao.log" 2>&1; then
  ok "teste de restore APROVADO (RESTORE_OK)"
  sed -n '/^OK    /p' "$TMP/verificacao.log" | sed 's/^/        /'
else
  ko "teste de restore REPROVADO"
  tail -20 "$TMP/verificacao.log" | sed 's/^/        /'
fi

# 5. TESTE NEGATIVO — o verificador precisa reprovar um dump corrompido
mkdir -p "$TMP/corrompido"
head -c 2048 "$DIR"/*.dump >"$TMP/corrompido/sales_intelligence.dump"
cp "$DIR/contagens.txt" "$DIR/manifest.txt" "$TMP/corrompido/" 2>/dev/null
if bash "$RAIZ_REPO/scripts/backup/verificar-backup.sh" "$TMP/corrompido" >"$TMP/negativo.log" 2>&1; then
  ko "teste negativo: um dump TRUNCADO foi aprovado — o verificador nao vale nada"
else
  ok "teste negativo: dump truncado foi REPROVADO, como devia ($(grep -c FALHOU "$TMP/negativo.log") falha(s) apontada(s))"
fi

# 6. o dump bom continua valido depois do teste negativo?
if bash "$RAIZ_REPO/scripts/backup/verificar-backup.sh" "$DIR" >"$TMP/reverificacao.log" 2>&1; then
  ok "reverificacao do dump bom segue APROVADA (nada foi corrompido no caminho)"
else
  ko "reverificacao do dump bom falhou"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_OK ($ITENS itens, 0 falhas)"
  exit 0
else
  echo "RESULTADO: TESTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
