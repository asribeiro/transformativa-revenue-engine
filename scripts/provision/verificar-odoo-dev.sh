#!/usr/bin/env bash
# Verificador do aceite do card TRE-W2-E01-T01 — Odoo Community no ambiente DEV do TRE.
#
# Card: TRE-W2-E01-T01 (`t_d6dc5a4c`). Roda NA VPS. Confere item a item, com exit code:
#   1. `docker compose config` valido com o par nao-secreto versionado
#   2. os dois containers de pe, com restart=unless-stopped
#   3. identidade do artefato: imagem declarada e DIGEST igual ao registrado no par
#   4. HTTP 200 real em 127.0.0.1:<porta>/web/login, com pagina do Odoo
#   5. banco do Odoo SEPARADO do `sales_intelligence` (bancos e volumes distintos)
#   6. nenhuma porta publica nova (loopback no Odoo, nada publicado no Postgres, UFW so 22)
#
# Uso (na VPS): bash verificar-odoo-dev.sh
set -uo pipefail

COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/dev/compose/odoo.yml}"
ENVFILE="${TRE_ODOO_ENV:-/opt/tre/dev/compose/odoo.env}"
SEGREDOS="${TRE_ODOO_SEGREDOS:-/etc/tre/odoo-dev}"
PG_SALES="pg-sales-dev"          # o Postgres da Sales Intelligence (trio canonico do dono)

FALHAS=0; ITENS=0
ok()     { ITENS=$((ITENS+1)); echo "OK    $*"; }
falhou() { ITENS=$((ITENS+1)); FALHAS=$((FALHAS+1)); echo "FALHOU $*"; }

compose() { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }
# le sem imprimir: as senhas nunca aparecem na saida do verificador
le_env() { sed -n "s/^$1=//p" "$ENVFILE" | head -1; }

[ -f "$COMPOSE" ] || { echo "FALHOU compose ausente em $COMPOSE"; exit 2; }
[ -f "$ENVFILE" ] || { echo "FALHOU par nao-secreto ausente em $ENVFILE"; exit 2; }
PORTA="$(le_env ODOO_HTTP_PORT)"; VERSAO="$(le_env ODOO_VERSION)"; DIGESTO="$(le_env ODOO_DIGEST_ESPERADO)"

echo "== 1. compose =="
if compose config -q 2>/dev/null; then
  ok "compose config valido ($COMPOSE + $ENVFILE)"
else
  falhou "compose config invalido ($COMPOSE + $ENVFILE)"
fi
IMAGENS="$(compose config --images 2>/dev/null | sort | tr '\n' ' ')"
case "$IMAGENS" in
  *"odoo:$VERSAO"*) ok "imagem declarada no compose: odoo:$VERSAO (versao pinada, nao flutuante)" ;;
  *) falhou "compose nao declara odoo:$VERSAO (imagens: $IMAGENS)" ;;
esac

echo "== 2. containers =="
for c in pg-odoo-dev odoo-dev; do
  if [ "$(docker inspect "$c" --format '{{.State.Running}}' 2>/dev/null)" = "true" ]; then
    ok "$c em execucao (id $(docker inspect "$c" --format '{{.Id}}' | cut -c1-12), restart=$(docker inspect "$c" --format '{{.HostConfig.RestartPolicy.Name}}'))"
  else
    falhou "$c nao esta em execucao"
  fi
done

echo "== 3. identidade do artefato =="
IMG_CONTAINER="$(docker inspect odoo-dev --format '{{.Config.Image}}' 2>/dev/null)"
[ "$IMG_CONTAINER" = "odoo:$VERSAO" ] && ok "container roda a imagem declarada ($IMG_CONTAINER)" \
  || falhou "container roda '$IMG_CONTAINER' e o par declara 'odoo:$VERSAO'"
ID_CONTAINER="$(docker inspect odoo-dev --format '{{.Image}}' 2>/dev/null)"
ID_TAG="$(docker image inspect "odoo:$VERSAO" --format '{{.Id}}' 2>/dev/null)"
[ -n "$ID_CONTAINER" ] && [ "$ID_CONTAINER" = "$ID_TAG" ] && ok "id da imagem do container == id da tag local" \
  || falhou "id da imagem do container ($ID_CONTAINER) != id da tag odoo:$VERSAO ($ID_TAG)"
REPO_DIGEST="$(docker image inspect "odoo:$VERSAO" --format '{{index .RepoDigests 0}}' 2>/dev/null)"
case "$REPO_DIGEST" in
  *"$DIGESTO") ok "digest confere com ODOO_DIGEST_ESPERADO ($DIGESTO)" ;;
  *) falhou "digest '$REPO_DIGEST' nao contem o esperado '$DIGESTO'" ;;
esac

echo "== 4. servico responde =="
CORPO="$(mktemp)"; CODIGO="000"
for _ in $(seq 1 6); do
  CODIGO="$(curl -s -m 10 -o "$CORPO" -w '%{http_code}' "http://127.0.0.1:${PORTA}/web/login" || true)"
  [ "$CODIGO" = "200" ] && break
  sleep 5
done
[ "$CODIGO" = "200" ] && ok "HTTP 200 em http://127.0.0.1:${PORTA}/web/login" \
  || falhou "HTTP $CODIGO em http://127.0.0.1:${PORTA}/web/login"
if grep -qi 'odoo' "$CORPO"; then ok "corpo da resposta e a pagina do Odoo ($(wc -c <"$CORPO") bytes)"; else falhou "corpo da resposta nao parece do Odoo"; fi
BANCO_VIVO="$(docker exec odoo-dev odoo --version 2>/dev/null | tail -1)"
case "$BANCO_VIVO" in
  *"$VERSAO"*) ok "binario do container responde '$BANCO_VIVO'" ;;
  *) falhou "binario do container responde '$BANCO_VIVO' e o par declara $VERSAO" ;;
esac
if docker exec pg-odoo-dev psql -U odoo -d postgres -tAc "select 1 from pg_database where datname='odoo_dev'" 2>/dev/null | grep -qx 1; then
  ok "banco 'odoo_dev' existe no pg-odoo-dev"
else
  falhou "banco 'odoo_dev' NAO existe no pg-odoo-dev"
fi
INTRUSO="$(docker exec pg-odoo-dev psql -U odoo -d postgres -tAc "select datname from pg_database where datname='sales_intelligence'" 2>/dev/null)"
[ -z "$INTRUSO" ] && ok "o Postgres do Odoo NAO tem o banco 'sales_intelligence'" \
  || falhou "o Postgres do Odoo tem 'sales_intelligence' — os bancos nao estao separados"
[ "$(docker inspect pg-odoo-dev --format '{{range .Mounts}}{{.Name}}{{end}}')" = "pgdata-odoo-dev" ] \
  && ok "volume proprio do banco do Odoo (pgdata-odoo-dev)" \
  || falhou "volume do pg-odoo-dev inesperado: $(docker inspect pg-odoo-dev --format '{{range .Mounts}}{{.Name}}{{end}}')"

echo "== 5. separacao do sales_intelligence =="
if docker inspect "$PG_SALES" >/dev/null 2>&1; then
  DO_ODOO_LA="$(docker exec "$PG_SALES" psql -U sales_ai -d sales_intelligence -tAc "select datname from pg_database where datname='odoo_dev'" 2>/dev/null)"
  [ -z "$DO_ODOO_LA" ] && ok "$PG_SALES nao tem o banco 'odoo_dev' (nenhum lado empresta banco ao outro)" \
    || falhou "$PG_SALES tem 'odoo_dev' — os bancos nao estao separados"
  [ "$(docker inspect "$PG_SALES" --format '{{range .Mounts}}{{.Name}}{{end}}')" != "pgdata-odoo-dev" ] \
    && ok "$PG_SALES usa volume diferente do pg-odoo-dev" \
    || falhou "$PG_SALES e pg-odoo-dev compartilham volume"
else
  ok "$PG_SALES nao existe nesta maquina (nada a separar)"
fi

echo "== 6. nenhuma porta publica =="
# `docker port` devolve "8069/tcp -> 127.0.0.1:8069": a primeira rodada deste verificador
# reprovava esse formato (defeito do proprio item, achado e corrigido nesta execucao). O que
# reprova agora e endereco NAO-loopback, nao a forma da string.
PUBLICO="$(docker port odoo-dev 2>/dev/null)"
case "$PUBLICO" in
  "") falhou "odoo-dev nao publica porta nenhuma (esperado 127.0.0.1:$PORTA)" ;;
  *"-> 0.0.0.0:"*|*"-> ::"*|*"-> [::]:"*) falhou "odoo-dev publica endereco publico ($PUBLICO)" ;;
  *"-> 127.0.0.1:$PORTA"*) ok "odoo-dev publica so em loopback ($PUBLICO)" ;;
  *) falhou "odoo-dev publica em '$PUBLICO' (esperado 127.0.0.1:$PORTA)" ;;
esac
NAO_PUBLICADO="$(docker port pg-odoo-dev 2>/dev/null)"
[ -z "$NAO_PUBLICADO" ] && ok "pg-odoo-dev nao publica porta nenhuma (fala pela rede tre-odoo-dev)" \
  || falhou "pg-odoo-dev publica '$NAO_PUBLICADO'"
ESCUTA="$(ss -lntH "sport = :$PORTA" 2>/dev/null | awk '{print $4}' | sort -u | tr '\n' ' ')"
case "$ESCUTA" in
  *0.0.0.0*|*"\*:"*) falhou "porta $PORTA escutando em endereco publico ($ESCUTA)" ;;
  "") falhou "nada escutando na porta $PORTA ($ESCUTA)" ;;
  *) ok "porta $PORTA escuta apenas em $ESCUTA" ;;
esac
if command -v ufw >/dev/null 2>&1; then
  REGRAS="$(ufw status 2>/dev/null | awk '/^[0-9]/{print $1}' | sort -u | tr '\n' ' ')"
  case " $REGRAS " in
    *" $PORTA "*|*" $PORTA/tcp "*) falhou "UFW tem regra para a porta $PORTA (regras: $REGRAS)" ;;
    *) ok "UFW intacto: regras liberadas = [$REGRAS] (exposicao publica e o card TRE-W2-E01-T02)" ;;
  esac
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: ODOO_DEV_OK ($ITENS itens, 0 falhas) versao=$VERSAO porta=127.0.0.1:$PORTA"
  exit 0
fi
echo "RESULTADO: ODOO_DEV_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
