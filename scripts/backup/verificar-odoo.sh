#!/usr/bin/env bash
# =====================================================================================
# verificar-odoo.sh <diretorio_de_backup> — RESTORE REAL do Odoo em alvo DESCARTAVEL.
#
# Backup nunca restaurado nao e backup, e esperanca. Este script nao confere "o arquivo
# existe": ele MONTA o restore inteiro do Odoo do ambiente `dev` num alvo descartavel e
# so aceita quando o Odoo RESPONDE:
#
#   1. conferencia de identidade dos arquivos do artefato (sha256 do dump e do filestore
#      contra o manifesto) e do filestore (numero de arquivos do tar == manifesto);
#   2. PostgreSQL DESCARTAVEL (`postgres:16`, sem porta publicada, servidor definitivo
#      esperado por `SELECT 1` duas vezes — `pg_isready` mente no init da imagem);
#   3. `pg_restore` do `odoo_dev.dump` nele e comparacao **linha a linha** das contagens
#      por tabela do schema `public` contra `odoo-contagens.txt` do backup;
#   4. filestore desempacotado do `odoo-filestore.tar.gz` no data_dir do Odoo descartavel;
#   5. Odoo DESCARTAVEL (`odoo:<versao do manifesto>`) subindo contra o banco restaurado,
#      na imagem de digest registrado: HTTP 200 em `/web/login`, corpo com a pagina do
#      Odoo, binario respondendo a versao e o modulo `base` INSTALADO no banco restaurado;
#   6. derruba tudo (trap: containers, rede e diretorio temporario), inclusive em falha.
#
# O que NAO e tocado: `odoo-dev`, `pg-odoo-dev`, os volumes `pgdata-odoo-dev` /
# `odoo-data-dev`, `/etc/tre/odoo-dev`, `pg-sales-dev` e producao (nao provisionada).
# Os nomes criados aqui tem o prefixo `tre-verif-odoo-`.
#
# Uso:
#   scripts/backup/verificar-odoo.sh /opt/tre/backup/tre_dev_20261001T130000Z
#   scripts/backup/verificar-odoo.sh /opt/tre/backup/tre_dev_... odoo_dev.dump
#
# Variaveis: TRE_ODOO_IMAGEM (default: o do manifesto), TRE_VERIF_ODOO_TIMEOUT_S (180),
#            TRE_BACKUP_IMAGEM (postgres:16 do PostgreSQL descartavel).
#
# Saida: OK/FALHOU/NOTA item a item; `RESULTADO: RESTORE_ODOO_OK` exit 0 ou
#        `RESULTADO: RESTORE_ODOO_FALHOU` exit 1.
# =====================================================================================
set -uo pipefail

ALVO="${1:-}"
DUMP_FORA="${2:-}"
if [ -z "$ALVO" ]; then
  echo "uso: $0 <diretorio_de_backup> [arquivo.dump]"
  exit 2
fi

if [ -d "$ALVO" ]; then DIR="$ALVO"; else DIR="$(cd "$(dirname "$ALVO")" && pwd)"; fi
MAN="$DIR/odoo-manifest.txt"

ITENS=0
FALHAS=0
ok()   { ITENS=$((ITENS + 1)); echo "OK     $*"; }
ko()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }
nota() { ITENS=$((ITENS + 1)); echo "NOTA   $*"; }

le_manifesto() { # <chave> [default]
  local v
  v="$(awk -F': ' -v k="$1" '$1==k{print $2; exit}' "$MAN" 2>/dev/null | tr -d '[:space:]')"
  printf '%s' "${v:-${2:-}}"
}

# ---------------------------------------------------------------- 0. o que vamos restaurar
# Ilegivel por PERMISSAO nao e "artefato sem Odoo". Caso real medido na rodada 2 da revisao
# independente deste card: artefato gravado por execucao manual como root (`root:root 700`) e
# o verificador do timer rodando como `tre-deploy` — a saida era "artefato sem Odoo?"/
# RESTORE_ODOO_FALHOU para um artefato que TEM o bloco do Odoo e esta integro.
if [ -d "$DIR" ] && { [ ! -r "$DIR" ] || [ ! -x "$DIR" ]; }; then
  ko "artefato '$DIR' existe mas NAO e legivel por '$(id -un)': dono $(stat -c '%U:%G' "$DIR" 2>/dev/null || echo n/d), modo $(stat -c '%a' "$DIR" 2>/dev/null || echo n/d) — e PERMISSAO, nao 'artefato sem Odoo'; a rotina de backup tem de entregar o artefato com dono do usuario de servico"
  echo; echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"; exit 1
fi
if [ -e "$MAN" ] && [ ! -r "$MAN" ]; then
  ko "manifesto do Odoo existe em $MAN e NAO e legivel por '$(id -un)' (dono $(stat -c '%U:%G' "$MAN" 2>/dev/null || echo n/d), modo $(stat -c '%a' "$MAN" 2>/dev/null || echo n/d)) — e PERMISSAO, nao 'manifesto ausente'"
  echo; echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"; exit 1
fi
[ -s "$MAN" ] || { ko "manifesto do Odoo ausente em $DIR/odoo-manifest.txt — artefato sem Odoo?"; echo; echo "RESULTADO: RESTORE_ODOO_FALHOU (0/1)"; exit 1; }

BANCO="$(le_manifesto odoo_banco odoo_dev)"
USUARIO="$(le_manifesto odoo_usuario odoo)"
IMAGEM="$(le_manifesto odoo_imagem_restore "${TRE_ODOO_IMAGEM:-odoo:19.0}")"
DIGEST="$(le_manifesto odoo_imagem_digest)"
SHA_DUMP="$(le_manifesto odoo_sha256_dump)"
SHA_FS="$(le_manifesto odoo_filestore_sha256)"
ARQ_FS_MAN="$(le_manifesto odoo_filestore_arquivos)"
TABELAS_MAN="$(le_manifesto odoo_tabelas)"
IMAGEM_AUX="${TRE_BACKUP_IMAGEM:-postgres:16}"
TIMEOUT="${TRE_VERIF_ODOO_TIMEOUT_S:-180}"

if [ -n "$DUMP_FORA" ] && [ -s "$DUMP_FORA" ]; then DUMP="$DUMP_FORA"; else DUMP="$DIR/$BANCO.dump"; fi
FS="$DIR/odoo-filestore.tar.gz"

echo "=================================================================="
echo "-- RESTORE DO ODOO EM ALVO DESCARTAVEL"
echo "-- artefato: $DIR"
echo "-- banco:    $BANCO (usuario $USUARIO)"
echo "-- imagens:  odoo=$IMAGEM / postgres=$IMAGEM_AUX"
echo "=================================================================="

# ---------------------------------------------------------------- 1. identidade do artefato
if [ -s "$DUMP" ]; then
  ok "dump presente ($(du -h "$DUMP" | cut -f1))"
else
  ko "dump ausente ou vazio: $DUMP"
  echo; echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"; exit 1
fi
if [ -n "$SHA_DUMP" ]; then
  SHA_AGORA="$(sha256sum "$DUMP" | cut -d' ' -f1)"
  [ "$SHA_AGORA" = "$SHA_DUMP" ] && ok "sha256 do dump confere com o manifesto" \
    || ko "sha256 do dump NAO confere (manifesto $SHA_DUMP, arquivo $SHA_AGORA)"
else
  ko "manifesto sem odoo_sha256_dump — nao da para provar a identidade do dump"
fi
if [ -s "$FS" ]; then
  ok "filestore presente ($(du -h "$FS" | cut -f1))"
  if [ -n "$SHA_FS" ]; then
    SHA_AGORA="$(sha256sum "$FS" | cut -d' ' -f1)"
    [ "$SHA_AGORA" = "$SHA_FS" ] && ok "sha256 do filestore confere com o manifesto" \
      || ko "sha256 do filestore NAO confere (manifesto $SHA_FS, arquivo $SHA_AGORA)"
  else
    ko "manifesto sem odoo_filestore_sha256"
  fi
else
  ko "filestore ausente ou vazio: $FS"
fi
# a imagem que vai RESPONDER tem de ser a registrada (identidade do artefato que sobe)
if docker image inspect "$IMAGEM" >/dev/null 2>&1; then
  REPO_DIGEST="$(docker image inspect "$IMAGEM" --format '{{index .RepoDigests 0}}' 2>/dev/null)"
  if [ -n "$DIGEST" ] && [ "$DIGEST" != "n/d" ]; then
    case "$REPO_DIGEST" in
      *"$DIGEST") ok "digest da imagem confere com o registrado ($DIGEST)" ;;
      *) ko "imagem '$IMAGEM' tem RepoDigest '$REPO_DIGEST' e o manifesto registra '$DIGEST'" ;;
    esac
  else
    nota "manifesto sem digest da imagem — identidade nao conferida (imagem '$IMAGEM')"
  fi
else
  ko "imagem '$IMAGEM' nao existe nesta maquina (o restore subiria outra coisa)"
fi

# ---------------------------------------------------------------- 2. alvo descartavel
SUFIXO="$$-$RANDOM"
PG="tre-verif-odoo-pg-$SUFIXO"
APP="tre-verif-odoo-app-$SUFIXO"
REDE="tre-verif-odoo-net-$SUFIXO"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-verif-odoo.XXXXXX")"
PORTA=""

limpar() {
  docker rm -f "$APP" >/dev/null 2>&1 || true
  docker rm -f "$PG"  >/dev/null 2>&1 || true
  docker network rm "$REDE" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap limpar EXIT INT TERM HUP

docker network create "$REDE" >/dev/null 2>&1 \
  && ok "rede descartavel criada ($REDE)" || { ko "nao consegui criar a rede descartavel"; echo; echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"; exit 1; }

# `POSTGRES_HOST_AUTH_METHOD=trust`: o alvo e descartavel e nao publica porta — assim o
# restore nao precisa de senha nenhuma (nada de segredo em argumento, arquivo ou log).
if docker run -d --name "$PG" --network "$REDE" \
     -e POSTGRES_USER="$USUARIO" -e POSTGRES_DB="$BANCO" -e POSTGRES_HOST_AUTH_METHOD=trust \
     "$IMAGEM_AUX" >/dev/null 2>&1; then
  ok "postgres descartavel no ar ($PG, imagem $IMAGEM_AUX, sem porta publicada)"
else
  ko "nao consegui subir o postgres descartavel ($IMAGEM_AUX)"
  echo; echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"; exit 1
fi

# O init da imagem oficial derruba um servidor TEMPORARIO: `SELECT 1` responder duas vezes
# (com pausa) e o unico sinal de que o servidor definitivo esta de pe.
pronto=0
for _ in $(seq 1 90); do
  if docker exec "$PG" psql -U "$USUARIO" -d "$BANCO" -tAc 'SELECT 1' >/dev/null 2>&1; then
    sleep 3
    if docker exec "$PG" psql -U "$USUARIO" -d "$BANCO" -tAc 'SELECT 1' >/dev/null 2>&1; then pronto=1; break; fi
  fi
  sleep 1
done
if [ "$pronto" = "1" ]; then
  ok "postgres de destino pronto ($(docker exec "$PG" psql -U "$USUARIO" -d "$BANCO" -tAc 'SHOW server_version' | tr -d '[:space:]'))"
else
  ko "postgres de destino nao ficou pronto"
  docker logs --tail 15 "$PG" 2>&1 | sed 's/^/       /'
  echo; echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"; exit 1
fi

# ---------------------------------------------------------------- 3. restore do banco
objetos="$(docker exec -i "$PG" pg_restore --list <"$DUMP" 2>/dev/null | grep -cE '^[0-9]+;')"
[ "${objetos:-0}" -gt 0 ] && ok "dump legivel pelo pg_restore ($objetos objetos no indice)" \
  || ko "o dump nao pode ser lido pelo pg_restore (arquivo corrompido ou formato errado)"

if docker exec -i "$PG" pg_restore -U "$USUARIO" -d "$BANCO" --no-owner --no-privileges \
     <"$DUMP" 2>"$TMP/pg_restore.err"; then
  ok "pg_restore concluido"
else
  ko "pg_restore retornou erro: $(head -c 300 "$TMP/pg_restore.err" | tr '\n' ' ')"
fi

tabelas_restauradas="$(docker exec "$PG" psql -U "$USUARIO" -d "$BANCO" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" 2>/dev/null | tr -d '[:space:]')"
if [ -n "${TABELAS_MAN:-}" ] && [ "$tabelas_restauradas" = "$TABELAS_MAN" ]; then
  ok "tabelas em public: $tabelas_restauradas (igual ao backup)"
else
  ko "tabelas em public: restaurado=${tabelas_restauradas:-n/d} backup=${TABELAS_MAN:-n/d}"
fi
if [ "${TABELAS_MAN:-0}" -ge 1 ] 2>/dev/null; then
  ok "o backup tem conteudo ($TABELAS_MAN tabelas registradas)"
else
  ko "backup sem tabela nenhuma registrada — nao ha o que restaurar"
fi

# contagens por tabela: o comparativo que realmente prova a restauracao
uniao="$(docker exec "$PG" psql -U "$USUARIO" -d "$BANCO" -tAc \
  "SELECT string_agg('SELECT '''||table_name||''' AS tabela, count(*)::bigint AS linhas FROM public.'||quote_ident(table_name), ' UNION ALL ' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" 2>/dev/null)"
restauradas="$(docker exec "$PG" psql -U "$USUARIO" -d "$BANCO" -tAF'|' -c "$uniao ORDER BY 1" 2>/dev/null | tr -d '\r')"
origem="$(tr -d '\r' <"$DIR/odoo-contagens.txt" 2>/dev/null)"
if [ -n "$restauradas" ] && [ "$restauradas" = "$origem" ]; then
  ok "contagens por tabela: todas as $(wc -l <<<"$restauradas") batem (linha a linha)"
else
  ko "contagens divergem — diferencas:"
  diff <(echo "$origem") <(echo "$restauradas") | sed 's/^/       /'
fi

if docker exec "$PG" psql -U "$USUARIO" -d "$BANCO" -tAc \
     "select count(*) from ir_module_module where name='base' and state='installed'" 2>/dev/null | grep -qx 1; then
  ok "modulo 'base' instalado no banco restaurado (e o Odoo, nao um banco vazio)"
else
  ko "modulo 'base' NAO esta instalado no banco restaurado"
fi

# ---------------------------------------------------------------- 4. filestore
DADOS="$TMP/dados"
mkdir -p "$DADOS"
if tar -xzf "$FS" -C "$DADOS" 2>"$TMP/tar.err"; then
  ok "filestore desempacotado"
else
  ko "nao consegui desempacotar o filestore: $(head -c 200 "$TMP/tar.err" | tr '\n' ' ')"
fi
arquivos_desempacotados="$(find "$DADOS" -type f 2>/dev/null | wc -l)"
if [ -n "${ARQ_FS_MAN:-}" ] && [ "$arquivos_desempacotados" = "$ARQ_FS_MAN" ]; then
  ok "arquivos do filestore: $arquivos_desempacotados (igual ao manifesto)"
else
  ko "arquivos do filestore: desempacotado=$arquivos_desempacotados manifesto=${ARQ_FS_MAN:-n/d}"
fi
if [ -d "$DADOS/filestore/$BANCO" ]; then
  ok "diretorio filestore/$BANCO presente no filestore restaurado"
else
  ko "diretorio filestore/$BANCO AUSENTE no filestore restaurado (anexos do banco nao voltariam)"
fi
# o Odoo roda como uid 100 dentro do container: o alvo descartavel tem de ser gravavel por ele
chmod -R a+rwX "$DADOS" 2>/dev/null || true

# ---------------------------------------------------------------- 5. Odoo contra o banco restaurado
# `--entrypoint /usr/bin/odoo`: o entrypoint da imagem (`/entrypoint.sh`) ACRESCENTA os
# argumentos de banco DEPOIS dos nossos — `odoo "$@" "${DB_ARGS[@]}"` — e o HOST default e
# `db`. Como a ultima ocorrencia vence, o Odoo descartavel tentava `db` e morria com
# "could not translate host name db" (medido em 01/10/2026). Chamando o binario direto,
# os argumentos abaixo sao os unicos e a identidade do que sobe e exatamente esta linha.
if docker run -d --name "$APP" --network "$REDE" \
     -v "$DADOS:/var/lib/odoo" -p 127.0.0.1::8069 \
     --entrypoint /usr/bin/odoo \
     "$IMAGEM" --db_host="$PG" --db_port=5432 --db_user="$USUARIO" \
     --database="$BANCO" --data-dir=/var/lib/odoo --http-port=8069 \
     >/dev/null 2>&1; then
  ok "Odoo descartavel no ar ($APP)"
else
  ko "nao consegui subir o Odoo descartavel ($IMAGEM)"
  echo; echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"; exit 1
fi
PORTA="$(docker port "$APP" 8069 2>/dev/null | head -1 | sed 's/.*://')"
if [ -n "$PORTA" ]; then
  case "$(docker port "$APP" 8069 2>/dev/null)" in
    127.0.0.1:*) ok "publicado SO em loopback (127.0.0.1:$PORTA)" ;;
    *) ko "porta publicada fora do loopback: $(docker port "$APP" 8069)" ;;
  esac
else
  ko "nao consegui medir a porta publicada do Odoo descartavel"
fi

CORPO="$TMP/login.html"
CODIGO=000
if [ -n "$PORTA" ]; then
  for _ in $(seq 1 "$((TIMEOUT / 5))"); do
    CODIGO="$(curl -s -m 10 -o "$CORPO" -w '%{http_code}' "http://127.0.0.1:$PORTA/web/login" || true)"
    [ "$CODIGO" = "200" ] && break
    # processo morto nao vai responder: falha agora, com o log na mao, em vez de esperar o timeout
    if [ "$(docker inspect "$APP" --format '{{.State.Running}}' 2>/dev/null)" != "true" ]; then
      ko "o Odoo descartavel MORREU antes de responder (exit $(docker inspect "$APP" --format '{{.State.ExitCode}}' 2>/dev/null))"
      docker logs --tail 25 "$APP" 2>&1 | sed 's/^/       /'
      break
    fi
    sleep 5
  done
fi
if [ "$CODIGO" = "200" ]; then
  ok "HTTP 200 em http://127.0.0.1:$PORTA/web/login (Odoo RESTAURADO respondendo)"
else
  ko "HTTP $CODIGO em http://127.0.0.1:$PORTA/web/login — o Odoo nao respondeu depois do restore"
  docker logs --tail 25 "$APP" 2>&1 | sed 's/^/       /'
fi
if [ -s "$CORPO" ] && grep -qi 'odoo' "$CORPO"; then
  ok "corpo da resposta e a pagina do Odoo ($(wc -c <"$CORPO") bytes)"
else
  ko "corpo da resposta nao parece a pagina do Odoo"
fi
BINARIO="$(docker exec "$APP" odoo --version 2>/dev/null | tail -1)"
case "$BINARIO" in
  *"$(printf '%s' "$IMAGEM" | sed 's/^odoo://')"*) ok "binario do Odoo restaurado responde '$BINARIO'" ;;
  *) ko "binario responde '${BINARIO:-nada}' e o manifesto registra a imagem '$IMAGEM'" ;;
esac

# o Odoo que responde e o do banco RESTAURADO: a base servida tem de ser a restaurada.
# `version_info` e JSON-RPC (POST) — GET devolve 415 Unsupported Media Type (medido em
# 01/10/2026), o que reprovava um Odoo que estava respondendo certo.
if [ -n "$PORTA" ]; then
  VERSION_INFO="$(curl -s -m 10 -X POST -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","method":"call","params":{}}' \
    "http://127.0.0.1:$PORTA/web/webclient/version_info" 2>/dev/null || true)"
  case "$VERSION_INFO" in
    *server_version*) ok "JSON-RPC /web/webclient/version_info respondeu do Odoo restaurado" ;;
    *) ko "endpoint /web/webclient/version_info nao respondeu o esperado: $(printf '%s' "$VERSION_INFO" | head -c 120)" ;;
  esac
  # a tela de login do BANCO restaurado (nao de outro) responde 200
  CODIGO_DB="$(curl -s -m 10 -o "$TMP/login-db.html" -w '%{http_code}' \
    "http://127.0.0.1:$PORTA/web/login?db=$BANCO" || true)"
  [ "$CODIGO_DB" = "200" ] && ok "tela de login do banco restaurado '$BANCO' responde 200" \
    || ko "tela de login do banco '$BANCO' respondeu HTTP $CODIGO_DB"
fi

# ---------------------------------------------------------------- 6. o ambiente dev ficou intacto
for alvo in odoo-dev pg-odoo-dev pg-sales-dev; do
  if docker inspect "$alvo" >/dev/null 2>&1; then
    ESTADO="$(docker inspect "$alvo" --format '{{.State.Status}}')"
    if [ "$ESTADO" = "running" ]; then ok "ambiente preservado: $alvo continua running"
    else ko "ambiente alterado: $alvo esta '$ESTADO' (era para continuar running)"; fi
  fi
done

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: RESTORE_ODOO_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: RESTORE_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
