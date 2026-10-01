#!/usr/bin/env bash
# =====================================================================================
# restore-tre.sh <ambiente> <diretorio_de_backup|arquivo.dump> --confirmo [--banco NOME] [--manter]
#
# RESTAURACAO OPERACIONAL em um ambiente (dev|homolog|prod). Destrutivo por natureza:
# derruba e recria o banco alvo. Exige --confirmo explicito (nada de acidente por descuido).
# Depois de restaurar, compara as contagens com o backup e imprime o veredito.
#
# Para PRODUCAO: a politica do projeto exige aprovacao humana registrada
# (docs/operations/registro-de-aprovacoes.md). Este script nao substitui essa aprovacao.
#
# Variaveis: TRE_PG_SERVICO, TRE_PG_USER, TRE_PG_DB, TRE_PG_ADMIN (default: postgres)
# =====================================================================================
set -uo pipefail

AMBIENTE="${1:-}"
ALVO="${2:-}"
CONFIRMO=0
BANCO_ALVO="${TRE_PG_DB:-sales_intelligence}"
MANTER=0

shift || true
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --confirmo) CONFIRMO=1 ;;
    --banco)    BANCO_ALVO="$2"; shift ;;
    --manter)   MANTER=1 ;;
    *) echo "argumento desconhecido: $1"; exit 2 ;;
  esac
  shift
done

if [ -z "$AMBIENTE" ] || [ -z "$ALVO" ]; then
  echo "uso: $0 <dev|homolog|prod> <diretorio_de_backup|arquivo.dump> --confirmo [--banco NOME] [--manter]"
  exit 2
fi
if [ "$CONFIRMO" != "1" ]; then
  echo "RECUSADO: restauracao derruba o banco alvo. Repita com --confirmo se e isso mesmo que voce quer."
  exit 3
fi

SERVICO="${TRE_PG_SERVICO:-pg-$AMBIENTE}"
USUARIO="${TRE_PG_USER:-tre}"
ADMIN="${TRE_PG_ADMIN:-postgres}"
ITENS=0
FALHAS=0
ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

if [ -d "$ALVO" ]; then DIR="$ALVO"; else DIR="$(cd "$(dirname "$ALVO")" && pwd)"; fi
# O artefato pode carregar DOIS *.dump (o do trio + odoo_dev.dump, card TRE-W2-E01-T01-F01):
# `ls *.dump | head -1` devolveria o do Odoo em ordem alfabetica. O dump do trio e o que o
# manifesto declara em `banco:`; sem manifesto, o glob ignora os dumps do Odoo.
BANCO_ARTEFATO="$(awk -F': ' '/^banco:/{print $2; exit}' "$DIR/manifest.txt" 2>/dev/null | tr -d '[:space:]')"
if [ -n "$BANCO_ARTEFATO" ] && [ -s "$DIR/$BANCO_ARTEFATO.dump" ]; then
  DUMP="$DIR/$BANCO_ARTEFATO.dump"
else
  DUMP="$(ls "$DIR"/*.dump 2>/dev/null | grep -v '/odoo_' | head -1)"
fi

echo "=================================================================="
echo "-- RESTORE  ambiente: $AMBIENTE   servico: $SERVICO   banco: $BANCO_ALVO"
echo "-- origem:  ${DUMP:-<nenhum dump>}"
echo "=================================================================="

[ -s "${DUMP:-}" ] && ok "dump encontrado ($(du -h "$DUMP" | cut -f1))" || { ko "dump ausente em $DIR"; echo "RESULTADO: RESTORE_FALHOU"; exit 1; }
docker inspect "$SERVICO" >/dev/null 2>&1 && ok "container do ambiente existe" || { ko "container '$SERVICO' nao existe"; echo "RESULTADO: RESTORE_FALHOU"; exit 1; }
docker exec "$SERVICO" pg_isready -U "$USUARIO" >/dev/null 2>&1 && ok "postgres responde" || ko "postgres nao responde"

# 1. snapshot de seguranca do estado atual (para reverter a propria restauracao)
SEGURANCA="/tmp/tre_antes_restore_${AMBIENTE}_$(date -u +%Y%m%dT%H%M%SZ).dump"
if docker exec "$SERVICO" pg_dump -U "$USUARIO" -d "$BANCO_ALVO" -Fc >"$SEGURANCA" 2>/dev/null && [ -s "$SEGURANCA" ]; then
  ok "snapshot do estado atual guardado em $SEGURANCA"
else
  ko "nao consegui guardar o snapshot do estado atual (abortando por seguranca)"
  rm -f "$SEGURANCA"
  echo "RESULTADO: RESTORE_FALHOU"
  exit 1
fi

# 2. derruba e recria o banco alvo
if docker exec "$SERVICO" psql -U "$ADMIN" -d postgres -c "DROP DATABASE IF EXISTS $BANCO_ALVO WITH (FORCE)" >/dev/null 2>&1; then
  ok "banco alvo removido (DROP DATABASE)"
else
  ko "falha ao remover o banco alvo"
fi
if docker exec "$SERVICO" psql -U "$ADMIN" -d postgres -c "CREATE DATABASE $BANCO_ALVO OWNER $USUARIO" >/dev/null 2>&1; then
  ok "banco alvo recriado"
else
  ko "falha ao recriar o banco alvo"
fi

# 3. restaura
if docker exec -i "$SERVICO" pg_restore -U "$USUARIO" -d "$BANCO_ALVO" --no-owner --no-privileges \
     <"$DUMP" 2>"$DIR/restore_${AMBIENTE}.err"; then
  ok "pg_restore concluido"
else
  ko "pg_restore com erro: $(head -c 300 "$DIR/restore_${AMBIENTE}.err" | tr '\n' ' ')"
fi

# 4. confere contagens contra o backup (UNION ALL montado dentro do SQL, via string_agg)
uniao="$(docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO_ALVO" -tAc \
  "SELECT string_agg('SELECT '''||table_name||''' AS tabela, count(*)::bigint AS linhas FROM sales_intelligence.'||table_name, ' UNION ALL ' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE'" 2>/dev/null)"
restauradas="$(docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO_ALVO" -tAF'|' -c "$uniao ORDER BY 1" 2>/dev/null | tr -d '\r')"
origem="$(tr -d '\r' <"$DIR/contagens.txt" 2>/dev/null)"
if [ -n "$restauradas" ] && [ "$restauradas" = "$origem" ]; then
  ok "contagens por tabela conferem com o backup"
else
  ko "contagens divergem apos o restore"
  diff <(echo "$origem") <(echo "$restauradas") | sed 's/^/      /'
fi

if [ "$MANTER" = "0" ]; then
  echo "nota: o snapshot de seguranca do estado anterior segue em $SEGURANCA"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: RESTORE_OK ($ITENS itens)"
  exit 0
else
  echo "RESULTADO: RESTORE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
