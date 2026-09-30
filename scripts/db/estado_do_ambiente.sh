#!/usr/bin/env bash
# =====================================================================================
# estado_do_ambiente.sh [ambiente]
#
# Relatorio READ-ONLY do estado do schema no ambiente: quantas tabelas e indices o
# schema `sales_intelligence` tem de fato, o `\dt` do psql (a listagem que os criterios
# de aceite do TRE-W1 usam como evidencia) e o conteudo da tabela de controle do runner.
#
# Nao escreve nada no banco (nenhum DDL/DML): serve para MEDIR o ambiente antes e depois
# do runner. Roda na VPS do ambiente (ADR-0008).
#
# Variaveis: TRE_PG_SERVICO, TRE_PG_USER, TRE_PG_DB, TRE_RAIZ (mesmo par do runner).
# =====================================================================================
set -uo pipefail

AMB="${1:-dev}"
RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PRESERVADO_SERVICO="${TRE_PG_SERVICO:-}"
PRESERVADO_USUARIO="${TRE_PG_USER:-}"
PRESERVADO_BANCO="${TRE_PG_DB:-}"
ARQ_AMB="$RAIZ/deploy/environments/$AMB.env"
[ -f "$ARQ_AMB" ] && . "$ARQ_AMB"
SERVICO="${PRESERVADO_SERVICO:-${TRE_PG_SERVICO:-pg-$AMB}}"
USUARIO="${PRESERVADO_USUARIO:-${TRE_PG_USER:-tre}}"
BANCO="${PRESERVADO_BANCO:-${TRE_PG_DB:-sales_intelligence}}"
CONTROLE="public.tre_schema_migrations"

echo "== estado do ambiente '$AMB' — container '$SERVICO', banco '$BANCO', usuario '$USUARIO'"
echo "-- tabelas e indices no schema sales_intelligence:"
docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -Atc \
  "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE') || ' tabelas | ' ||
          (SELECT count(*) FROM pg_indexes WHERE schemaname='sales_intelligence') || ' indices'"
echo "-- psql \\dt (search_path = sales_intelligence):"
docker exec -e PGOPTIONS="-c search_path=sales_intelligence" "$SERVICO" \
  psql -U "$USUARIO" -d "$BANCO" -c '\dt'
echo "-- tabela de controle do runner ($CONTROLE):"
docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -Atc \
  "SELECT to_regclass('$CONTROLE') IS NOT NULL" | grep -q '^t$' \
  && docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -c "TABLE $CONTROLE" \
  || echo "(tabela de controle ainda nao existe — nenhuma migration registrada)"
