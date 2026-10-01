#!/usr/bin/env bash
# =====================================================================================
# teste-backup-restore.sh [raiz_do_repo] [--ambiente dev|homolog]
#
# PROVA o ciclo completo de backup/restore. Dois modos:
#
#   MODO DESCARTAVEL (padrao, sem --ambiente) — a prova do TRE-W0-E01-T03, reproduzivel
#   em qualquer maquina com Docker: sobe um PostgreSQL descartavel, aplica a migration do
#   Data Contract V1 + a massa de smoke (as 12 tabelas) e roda o ciclo inteiro nele.
#
#   MODO AMBIENTE REAL (--ambiente dev|homolog) — exigido pelo card TRE-W1-E06-T01: o
#   backup e feito do banco do AMBIENTE (ex.: container pg-sales-dev, trio declarado em
#   deploy/environments/<ambiente>.env), nao de um container descartavel. Este modo NAO
#   cria container de origem: se o container do ambiente nao existir ou o schema estiver
#   vazio, FALHA — nunca cai para container descartavel em silencio. O RESTORE continua
#   acontecendo num PostgreSQL descartavel: restaurar por cima do ambiente seria
#   destrutivo e nao faz parte da prova. O artefato vai para TRE_BACKUP_DIR (padrao
#   /opt/tre/backup) e, com TRE_BACKUP_EXTERNO configurado, sobe ao bucket — o manifesto
#   tem de registrar `externo: enviado`.
#
# SEGURANCA: producao e recusada (ADR-005). Este teste nunca aponta para prod.
#
# Ciclo (igual nos dois modos):
#   1. origem pronta
#   2. backup de verdade (dump + globais + contagens + sha256 + manifesto [+ destino externo])
#   3. restore REAL num container novo, comparando tabela a tabela
#   4. TESTE NEGATIVO: dump truncado tem de ser REPROVADO (senao o verificador e carimbo)
#   5. reverificacao do dump bom (o teste negativo nao pode ter corrompido nada)
#   6. derruba o que criou (inclusive em falha)
#
# Variaveis: TRE_BACKUP_IMAGEM (padrao postgres:16), TRE_PG_SERVICO, TRE_PG_USER,
#            TRE_PG_DB, TRE_BACKUP_DIR, TRE_BACKUP_EXTERNO, RCLONE_CONFIG
# =====================================================================================
set -uo pipefail

RAIZ_REPO=""
AMBIENTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ambiente) AMBIENTE="${2:-}"; shift 2 ;;
    --ambiente=*) AMBIENTE="${1#*=}"; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) RAIZ_REPO="$1"; shift ;;
  esac
done
RAIZ_REPO="${RAIZ_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -n "$AMBIENTE" ] || AMBIENTE="${TRE_TESTE_AMBIENTE:-}"

MODO="descartavel"
if [ -n "$AMBIENTE" ]; then
  case "$AMBIENTE" in
    dev|homolog) MODO="ambiente" ;;
    prod)
      echo "FALHOU modo ambiente: 'prod' e RECUSADO por ADR-005 — teste nao executa backup/restore em producao"
      echo "RESULTADO: TESTE_FALHOU (modo ambiente recusado)"
      exit 2 ;;
    *)
      echo "FALHOU modo ambiente desconhecido: '$AMBIENTE' (aceito: dev, homolog)"
      echo "RESULTADO: TESTE_FALHOU (modo ambiente invalido)"
      exit 2 ;;
  esac
fi

# O modo ambiente NAO pode ser servido em silencio pelo descartavel: o trio de nomes vem
# do arquivo nao-secreto do ambiente (deploy/environments/<amb>.env), mas variavel de
# ambiente declarada pelo operador SEMPRE vence o arquivo (defeito ja visto no TRE-W1-E01-T01).
if [ "$MODO" = "ambiente" ]; then
  _op_servico="${TRE_PG_SERVICO:-}"; _op_user="${TRE_PG_USER:-}"; _op_db="${TRE_PG_DB:-}"
  ARQ_AMB="$RAIZ_REPO/deploy/environments/$AMBIENTE.env"
  if [ -f "$ARQ_AMB" ]; then
    # shellcheck disable=SC1090
    . "$ARQ_AMB"
    [ -n "$_op_servico" ] && TRE_PG_SERVICO="$_op_servico"
    [ -n "$_op_user" ] && TRE_PG_USER="$_op_user"
    [ -n "$_op_db" ] && TRE_PG_DB="$_op_db"
  fi
fi

IMAGEM="${TRE_BACKUP_IMAGEM:-postgres:16}"
SERVICO=""
SENHA=""
USUARIO_ORIGEM="tre"
BANCO="sales_intelligence"
DEST_ARTEFATO=""
AMB_BACKUP="dev"
if [ "$MODO" = "descartavel" ]; then
  SERVICO="tre-smoke-src-$$-$RANDOM"
  SENHA="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
else
  SERVICO="${TRE_PG_SERVICO:-pg-$AMBIENTE}"
  USUARIO_ORIGEM="${TRE_PG_USER:-sales_ai}"
  BANCO="${TRE_PG_DB:-sales_intelligence}"
  DEST_ARTEFATO="${TRE_BACKUP_DIR:-/opt/tre/backup}"
  AMB_BACKUP="$AMBIENTE"
  # o arquivo do ambiente (deploy/environments/<amb>.env) e carregado com `.` e NAO exporta
  # sozinho: sem este export o backup-tre.sh cairia no padrao `pg-<amb>` e pularia o ambiente.
  export TRE_PG_SERVICO="$SERVICO" TRE_PG_USER="$USUARIO_ORIGEM" TRE_PG_DB="$BANCO"
  export TRE_BACKUP_DIR="$DEST_ARTEFATO"
fi

# Destino externo sem RCLONE_CONFIG apontado e a configuracao do cofre existir: usa a do
# cofre (/etc/tre/rclone.conf, 600) — senao "externo: enviado" viraria falha de ambiente
# e nao do artefato.
if [ -n "${TRE_BACKUP_EXTERNO:-}" ] && [ -z "${RCLONE_CONFIG:-}" ] && [ -r /etc/tre/rclone.conf ]; then
  export RCLONE_CONFIG=/etc/tre/rclone.conf
fi

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

# no modo ambiente o container de origem NAO e nosso: nada dele e removido
if [ "$MODO" = "descartavel" ]; then
  trap 'docker rm -f "$SERVICO" >/dev/null 2>&1; echo; echo "artefatos do teste em: $TMP"' EXIT
else
  trap 'echo; echo "artefatos do teste em: $TMP"' EXIT
fi

echo "=================================================================="
echo "-- TESTE DE BACKUP E RESTORE"
echo "-- card:      TRE-W1-E06-T01 (modo ambiente real) / TRE-W0-E01-T03 (descartavel)"
echo "-- repo:      $RAIZ_REPO"
echo "-- modo:      $MODO${AMBIENTE:+ (ambiente $AMBIENTE)}"
echo "-- imagem:    $IMAGEM"
echo "-- origem:    $SERVICO"
if [ "$MODO" = "descartavel" ]; then
  echo "-- destino:   $TMP"
else
  echo "-- destino:   $DEST_ARTEFATO"
  echo "-- externo:   ${TRE_BACKUP_EXTERNO:-<vazio>}"
  echo "-- rclone:    ${RCLONE_CONFIG:-<padrao do usuario>}"
fi
echo "=================================================================="

if [ "$MODO" = "descartavel" ]; then
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
else
  # 1. origem = o ambiente REAL (nenhum container de origem e criado aqui)
  if docker inspect "$SERVICO" >/dev/null 2>&1; then
    ok "origem e o container do ambiente $AMBIENTE: $SERVICO (nenhum container descartavel de origem)"
  else
    ko "container de origem '$SERVICO' NAO existe no ambiente $AMBIENTE — o modo ambiente nao cai para container descartavel; suba o ambiente antes de testar"
    echo "RESULTADO: TESTE_FALHOU"; exit 1
  fi

  if esperar_postgres "$SERVICO" "$USUARIO_ORIGEM" "$BANCO"; then
    ok "postgres do ambiente $AMBIENTE responde ($(docker exec "$SERVICO" psql -U "$USUARIO_ORIGEM" -d "$BANCO" -tAc 'SHOW server_version' | tr -d '[:space:]'))"
  else
    ko "postgres do ambiente nao responde em '$SERVICO' ($USUARIO_ORIGEM@$BANCO)"
    echo "RESULTADO: TESTE_FALHOU"; exit 1
  fi

  tabelas_dev="$(docker exec "$SERVICO" psql -U "$USUARIO_ORIGEM" -d "$BANCO" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE'" 2>/dev/null | tr -d '[:space:]')"
  if [ "${tabelas_dev:-0}" -ge 1 ] 2>/dev/null; then
    linhas_dev="$(docker exec "$SERVICO" psql -U "$USUARIO_ORIGEM" -d "$BANCO" -tAc \
      "SELECT COALESCE(sum(c),0) FROM (SELECT count(*) c FROM sales_intelligence.organizations UNION ALL SELECT count(*) FROM sales_intelligence.scores) x" 2>/dev/null | tr -d '[:space:]')"
    ok "origem tem conteudo real: $tabelas_dev tabelas em $BANCO (amostra: $linhas_dev linhas)"
  else
    ko "schema sales_intelligence ausente/vazio em $BANCO — nao ha o que provar (migration nao aplicada?)"
    echo "RESULTADO: TESTE_FALHOU"; exit 1
  fi

  ID_DEV_ANTES="$(docker inspect -f '{{.Id}}' "$SERVICO" 2>/dev/null)"
  INICIO_DEV_ANTES="$(docker inspect -f '{{.State.StartedAt}}' "$SERVICO" 2>/dev/null)"
fi

# 3. backup de verdade (no modo ambiente, no destino real e com o destino externo herdado)
if [ "$MODO" = "descartavel" ]; then
  TRE_PG_SERVICO="$SERVICO" TRE_PG_USER=tre TRE_PG_DB=sales_intelligence \
    TRE_BACKUP_DIR="$TMP/backup" bash "$RAIZ_REPO/scripts/backup/backup-tre.sh" dev \
    >"$TMP/backup.log" 2>&1 \
    && ok "backup concluido (BACKUP_OK)" \
    || ko "backup falhou: $(grep FALHOU "$TMP/backup.log" | head -3 | tr '\n' ' ')"
  BASE_ARTEFATO="$TMP/backup"
else
  bash "$RAIZ_REPO/scripts/backup/backup-tre.sh" "$AMBIENTE" >"$TMP/backup.log" 2>&1 \
    && ok "backup do ambiente $AMBIENTE concluido (BACKUP_OK)" \
    || ko "backup falhou: $(grep FALHOU "$TMP/backup.log" | head -3 | tr '\n' ' ')"
  BASE_ARTEFATO="$DEST_ARTEFATO"
fi

DIR="$(ls -d "$BASE_ARTEFATO"/tre_"$AMB_BACKUP"_* 2>/dev/null | sort | tail -1)"
[ -n "${DIR:-}" ] && ok "artefato do backup gerado ($(basename "$DIR"))" || ko "nenhum artefato de backup encontrado em $BASE_ARTEFATO"

if [ -n "${DIR:-}" ]; then
  # 3b. integridade do artefato, medida de forma independente do proprio backup.
  # Nome explicito do dump do trio: com o Odoo no mesmo artefato ha DOIS *.dump
  # (card TRE-W2-E01-T01-F01) e `sha256sum ./*.dump` devolveria duas linhas.
  sha_arquivo="$(cd "$DIR" && sha256sum "./${BANCO}.dump" 2>/dev/null | cut -d' ' -f1)"
  sha_manifesto="$(awk -F': ' '/^sha256:/{print $2}' "$DIR/manifest.txt" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$sha_arquivo" ] && [ "$sha_arquivo" = "$sha_manifesto" ]; then
    ok "sha256 do dump confere com o manifesto ($sha_arquivo)"
  else
    ko "sha256 divergente: dump=$sha_arquivo manifesto=${sha_manifesto:-n/d}"
  fi
  if [ -s "$DIR/${BANCO}.dump" ] && [ -s "$DIR/contagens.txt" ] && [ -s "$DIR/manifest.txt" ]; then
    ok "artefato completo (dump, contagens e manifesto nao vazios)"
  else
    ko "artefato incompleto em $DIR ($(ls "$DIR" 2>/dev/null | tr '\n' ' '))"
  fi
  if [ "$MODO" = "ambiente" ]; then
    origem_manifesto="$(awk -F': ' '/^servico:/{print $2}' "$DIR/manifest.txt" 2>/dev/null | tr -d '[:space:]')"
    if [ "$origem_manifesto" = "$SERVICO" ]; then
      ok "manifesto registra a origem real do ambiente ($origem_manifesto)"
    else
      ko "manifesto aponta origem '$origem_manifesto' (esperado '$SERVICO')"
    fi
    externo_manifesto="$(grep -m1 '^externo:' "$DIR/manifest.txt" 2>/dev/null)"
    if [ -z "${TRE_BACKUP_EXTERNO:-}" ]; then
      ko "destino externo nao configurado (TRE_BACKUP_EXTERNO vazio) — o criterio 'artefato sobe ao bucket' nao pode ser provado"
    elif printf '%s' "$externo_manifesto" | grep -q '^externo: enviado'; then
      ok "manifesto registra o envio externo ($externo_manifesto)"
    else
      ko "manifesto NAO registra 'externo: enviado' (lido: '${externo_manifesto:-nenhuma linha externo:}')"
    fi
  fi
fi

# 4. TESTE DE RESTORE (container novo) — o comparativo tabela a tabela acontece aqui
if [ -n "${DIR:-}" ] && bash "$RAIZ_REPO/scripts/backup/verificar-backup.sh" "$DIR" >"$TMP/verificacao.log" 2>&1; then
  ok "teste de restore APROVADO (RESTORE_OK)"
  sed -n '/^OK    /p' "$TMP/verificacao.log" | sed 's/^/        /'
  grep -q 'contagens por tabela' "$TMP/verificacao.log" \
    && ok "contagens conferidas linha a linha na restauracao" \
    || ko "a verificacao nao comparou as contagens por tabela"
else
  ko "teste de restore REPROVADO"
  tail -20 "$TMP/verificacao.log" 2>/dev/null | sed 's/^/        /'
fi

# 5. TESTE NEGATIVO — o verificador precisa reprovar um dump corrompido
if [ -n "${DIR:-}" ]; then
  mkdir -p "$TMP/corrompido"
  head -c 2048 "$DIR/${BANCO}.dump" >"$TMP/corrompido/${BANCO}.dump"
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
fi

# 7. o ambiente de origem nao foi tocado pelo teste (modo ambiente)
if [ "$MODO" = "ambiente" ]; then
  ID_DEV_DEPOIS="$(docker inspect -f '{{.Id}}' "$SERVICO" 2>/dev/null)"
  INICIO_DEV_DEPOIS="$(docker inspect -f '{{.State.StartedAt}}' "$SERVICO" 2>/dev/null)"
  if [ "$ID_DEV_ANTES" = "$ID_DEV_DEPOIS" ] && [ "$INICIO_DEV_ANTES" = "$INICIO_DEV_DEPOIS" ]; then
    ok "container do ambiente intacto (mesmo Id e StartedAt antes/depois do ciclo)"
  else
    ko "o container do ambiente mudou durante o teste (id/StartedAt diferentes)"
  fi
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_OK ($ITENS itens, 0 falhas)"
  exit 0
else
  echo "RESULTADO: TESTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
