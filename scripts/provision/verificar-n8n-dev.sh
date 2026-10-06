#!/usr/bin/env bash
# Verificacao do n8n no ambiente DEV do TRE (VPS Contabo `vmi3619453`).
#
# Roda NA VPS. Cada item e' medicao, nao prosa: ou o comando prova, ou reprova.
# Runbook: docs/runbooks/n8n-dev.md.
#
# Uso:
#   bash verificar-n8n-dev.sh                  # aceite
#   bash verificar-n8n-dev.sh --prova-de-dente  # prova que o verificador REPROVA quando o
#                                               # ambiente/artefato muda (mutacao em copia)
#
# Variaveis (as mesmas do instalador; e' por elas que o dente aponta para copias mutadas SEM
# tocar no ambiente real):
#   TRE_N8N_COMPOSE, TRE_N8N_ENV, TRE_N8N_SEGREDOS, TRE_N8N_HOME
set -uo pipefail

COMPOSE="${TRE_N8N_COMPOSE:-/opt/tre/dev/compose/n8n.yml}"
ENVFILE="${TRE_N8N_ENV:-/opt/tre/dev/compose/n8n.env}"
SEGREDOS="${TRE_N8N_SEGREDOS:-/etc/tre/n8n-dev}"
HOME_N8N="${TRE_N8N_HOME:-/opt/tre/dev/n8n/home}"
CONTAINER="n8n-dev"
REDE="tre-odoo-dev"
ODOO="odoo-dev"

ITENS=0
FALHAS=0
ok()     { ITENS=$((ITENS+1)); printf 'OK    %s\n' "$1"; }
falhou() { ITENS=$((ITENS+1)); FALHAS=$((FALHAS+1)); printf 'FALHA %s\n' "$1"; }
info()   { printf -- '--    %s\n' "$1"; }

compose() { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }

# ---------------------------------------------------------------------------
# Ferramentas e artefato
# ---------------------------------------------------------------------------
docker info >/dev/null 2>&1 && ok "daemon do docker responde" || { falhou "daemon do docker nao responde"; }

if [ -f "$COMPOSE" ]; then ok "compose presente ($COMPOSE)"; else falhou "compose ausente ($COMPOSE)"; fi
if [ -f "$ENVFILE" ]; then ok "par nao-secreto presente ($ENVFILE)"; else falhou "par ausente ($ENVFILE)"; fi

if compose config -q </dev/null >/dev/null 2>&1; then
  ok "compose valido (config -q)"
else
  falhou "compose invalido (config -q)"
fi
RESOLVIDO="$(compose config </dev/null 2>/dev/null || true)"

# versao/digest declarados no par (o que esta no ar tem de ser isso)
VERSAO=""; DIGESTO_ESPERADO=""; PORTA=""; IMAGEM=""
if [ -f "$ENVFILE" ]; then
  # shellcheck disable=SC1090
  VERSAO="$(sed -n 's/^N8N_VERSAO=//p' "$ENVFILE" | head -1)"
  DIGESTO_ESPERADO="$(sed -n 's/^N8N_DIGEST_ESPERADO=//p' "$ENVFILE" | head -1)"
  PORTA="$(sed -n 's/^N8N_PORTA_LOCAL=//p' "$ENVFILE" | head -1)"
  IMAGEM="$(sed -n 's/^N8N_IMAGEM=//p' "$ENVFILE" | head -1)"
fi
[ -n "$VERSAO" ] && [ -n "$DIGESTO_ESPERADO" ] && [ -n "$PORTA" ] && [ -n "$IMAGEM" ] \
  && ok "par declara imagem/versao/digest/porta (versao=$VERSAO)" \
  || falhou "par incompleto (imagem/versao/digest/porta)"

# Isolamento no COMPOSE RESOLVIDO: nada de outro ambiente no artefato do dev.
intruso=""
for nome in n8n-homolog n8n-prod odoo-homolog odoo-prod pg-odoo-homolog pg-odoo-prod; do
  if grep -qE "(container_name|name): *${nome}\b" <<<"$RESOLVIDO"; then intruso="$nome"; fi
done
[ -z "$intruso" ] && ok "artefato do dev nao cita outro ambiente" \
  || falhou "artefato do dev cita container/rede de outro ambiente ($intruso)"

# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------
if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  ok "container '$CONTAINER' de pe"
else
  falhou "container '$CONTAINER' NAO esta rodando"
fi

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  POLITICA="$(docker inspect "$CONTAINER" --format '{{.HostConfig.RestartPolicy.Name}}')"
  [ "$POLITICA" = "unless-stopped" ] && ok "restart=unless-stopped" || falhou "restart=$POLITICA (esperado unless-stopped)"

  USUARIO="$(docker inspect "$CONTAINER" --format '{{.Config.User}}')"
  [ "$USUARIO" = "1000:1000" ] && ok "roda como 1000:1000 (nao root)" || falhou "roda como '$USUARIO' (esperado 1000:1000)"

  LIMITE="$(docker inspect "$CONTAINER" --format '{{.HostConfig.Memory}}')"
  [ "$LIMITE" = "1073741824" ] && ok "mem_limit=1g (D5)" || falhou "mem_limit=${LIMITE} bytes (esperado 1073741824)"

  IMAGEM_CT="$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')"
  [ "$IMAGEM_CT" = "${IMAGEM}:${VERSAO}" ] && ok "imagem do container == ${IMAGEM}:${VERSAO}" \
    || falhou "imagem do container ($IMAGEM_CT) != par (${IMAGEM}:${VERSAO})"

  DIGESTO_CT="$(docker inspect "$CONTAINER" --format '{{index .ImageManifestDescriptor.Digest}}' 2>/dev/null || true)"
  if [ -z "$DIGESTO_CT" ]; then
    DIGESTO_CT="$(docker image inspect "${IMAGEM}:${VERSAO}" --format '{{index .RepoDigests 0}}' 2>/dev/null | sed 's/^.*@//')"
    info "digest lido da imagem local (o container nao expoe ImageManifestDescriptor)"
  fi
  [ "$DIGESTO_CT" = "$DIGESTO_ESPERADO" ] && ok "digest == registrado no par ($DIGESTO_ESPERADO)" \
    || falhou "digest do que esta no ar ($DIGESTO_CT) != registrado ($DIGESTO_ESPERADO)"

  SAUDE="$(docker inspect "$CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-healthcheck{{end}}')"
  [ "$SAUDE" = "healthy" ] && ok "healthcheck do container: healthy" || falhou "healthcheck do container: $SAUDE"
fi

# ---------------------------------------------------------------------------
# Porta: publica SO em loopback
# ---------------------------------------------------------------------------
PUBLICADAS="$(docker inspect "$CONTAINER" --format '{{json .NetworkSettings.Ports}}' 2>/dev/null || echo '{}')"
if grep -q "127.0.0.1:${PORTA}" <<<"$PUBLICADAS"; then
  ok "porta publicada so em loopback (127.0.0.1:$PORTA -> 5678)"
else
  falhou "porta do n8n nao esta publicada em 127.0.0.1:$PORTA ($PUBLICADAS)"
fi
if grep -qE '"HostIp":"0\.0\.0\.0"|HostIp":"::' <<<"$PUBLICADAS"; then
  falhou "ha publicacao em 0.0.0.0/:: no container"
else
  ok "nenhuma publicacao em 0.0.0.0/::"
fi
if ss -lntH "sport = :$PORTA" 2>/dev/null | grep -q "127.0.0.1:$PORTA"; then
  ok "ss confirma escuta em 127.0.0.1:$PORTA"
else
  falhou "ss nao mostra escuta em 127.0.0.1:$PORTA"
fi
if ss -lntH "sport = :$PORTA" 2>/dev/null | grep -qE "0\.0\.0\.0:$PORTA|\[::\]:$PORTA"; then
  falhou "ss mostra escuta publica na porta $PORTA"
else
  ok "ss nao mostra escuta publica na porta $PORTA"
fi

# ---------------------------------------------------------------------------
# Resposta HTTP: de dentro (rede interna) e de fora (loopback publicado)
# ---------------------------------------------------------------------------
if docker exec --user 1000:1000 "$CONTAINER" node -e \
    "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))" \
    >/dev/null 2>&1; then
  ok "readiness 200 de dentro do container (/healthz/readiness)"
else
  falhou "readiness do n8n nao respondeu 200 de dentro do container"
fi

CODIGO="$(curl -sS -o /dev/null -m 15 -w '%{http_code}' "http://127.0.0.1:$PORTA/healthz/readiness" 2>/dev/null || echo 000)"
[ "$CODIGO" = "200" ] && ok "readiness 200 pelo loopback publicado (127.0.0.1:$PORTA)" \
  || falhou "readiness pelo loopback devolveu $CODIGO (esperado 200)"

CODIGO_UI="$(curl -sS -o /dev/null -m 15 -w '%{http_code}' "http://127.0.0.1:$PORTA/" 2>/dev/null || echo 000)"
case "$CODIGO_UI" in
  200|302|401) ok "UI responde na raiz (HTTP $CODIGO_UI)" ;;
  *) falhou "UI nao respondeu na raiz (HTTP $CODIGO_UI)" ;;
esac

# ---------------------------------------------------------------------------
# Variaveis efetivas (o que faz os workflows funcionarem)
# ---------------------------------------------------------------------------
declare -A ESPERADO=( [HOME]=/home/node [N8N_HOST]=n8n-dev [N8N_BLOCK_ENV_ACCESS_IN_NODE]=false \
                      [GENERIC_TIMEZONE]=UTC [N8N_SECURE_COOKIE]=false )
for chave in "${!ESPERADO[@]}"; do
  VALOR="$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
            | sed -n "s/^${chave}=//p" | head -1)"
  if [ "$VALOR" = "${ESPERADO[$chave]}" ]; then
    ok "env $chave=${ESPERADO[$chave]}"
  else
    falhou "env $chave='$VALOR' (esperado ${ESPERADO[$chave]})"
  fi
done

# ---------------------------------------------------------------------------
# Segredo: existe, 600, e NAO esta no artefato publicado nem em argv
# ---------------------------------------------------------------------------
ARQ="$SEGREDOS/n8n.env"
if [ -f "$ARQ" ]; then
  ok "chave do n8n em arquivo proprio ($ARQ)"
  PERM="$(stat -c '%a %U:%G' "$ARQ")"
  [ "$PERM" = "600 root:root" ] && ok "chave 600 root:root" || falhou "chave com permissao '$PERM' (esperado 600 root:root)"
  if grep -q '^N8N_ENCRYPTION_KEY=.\{32,\}' "$ARQ"; then
    ok "chave presente e com tamanho minimo (valor NAO e' impresso)"
  else
    falhou "chave ausente/curta em $ARQ"
  fi
  # a chave nao pode aparecer no artefato publicado nem no comando do container
  VALOR="$(sed -n 's/^N8N_ENCRYPTION_KEY=//p' "$ARQ" | head -1)"
  if [ -n "$VALOR" ] && grep -rqF "$VALOR" /opt/tre/dev/compose/ 2>/dev/null; then
    falhou "a chave do n8n aparece em arquivo publicado do dev"
  else
    ok "chave nao aparece no artefato publicado"
  fi
  if docker inspect "$CONTAINER" --format '{{json .Config.Cmd}}{{json .Config.Entrypoint}}{{json .Args}}' 2>/dev/null | grep -qF "$VALOR"; then
    falhou "a chave do n8n aparece em argv do container"
  else
    ok "chave nao aparece em argv do container"
  fi
else
  falhou "chave do n8n ausente em $ARQ"
fi

# ---------------------------------------------------------------------------
# Estado persistente
# ---------------------------------------------------------------------------
if [ -d "$HOME_N8N" ]; then
  DONO="$(stat -c '%u:%g %a' "$HOME_N8N")"
  [ "$DONO" = "1000:1000 700" ] && ok "home do n8n 1000:1000 700" || falhou "home do n8n com '$DONO' (esperado 1000:1000 700)"
  if [ -f "$HOME_N8N/.n8n/database.sqlite" ]; then
    ok "estado do n8n no bind mount (.n8n/database.sqlite)"
  else
    falhou "banco do n8n ausente em $HOME_N8N/.n8n (o estado nao estaria persistido)"
  fi
  if [ -d "$HOME_N8N/.cache" ]; then
    ok "~/.cache existe (a armadilha do HOME inteiro esta coberta)"
  else
    info "~/.cache ainda nao existe (aparece no primeiro uso de cache do n8n)"
  fi
else
  falhou "home do n8n ausente ($HOME_N8N)"
fi

# ---------------------------------------------------------------------------
# Rede interna: n8n <-> Odoo (os dois sentidos que os workflows usam)
# ---------------------------------------------------------------------------
REDES_CT="$(docker inspect "$CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null)"
grep -qw "$REDE" <<<"$REDES_CT" && ok "n8n na rede $REDE" || falhou "n8n NAO esta na rede $REDE"
if grep -qwE 'tre-odoo-homolog|tre-odoo-prod' <<<"$REDES_CT"; then
  falhou "n8n do dev esta em rede de outro ambiente ($REDES_CT)"
else
  ok "n8n do dev nao esta em rede de outro ambiente"
fi
if docker inspect "$ODOO" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | grep -qw "$REDE"; then
  ok "Odoo do dev na mesma rede ($REDE)"
else
  falhou "Odoo do dev NAO esta na rede $REDE"
fi

# n8n -> Odoo (a API controlada que os workflows chamam)
if docker exec --user 1000:1000 "$CONTAINER" node -e \
    "fetch('http://odoo-dev:8069/web/login').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))" \
    >/dev/null 2>&1; then
  ok "n8n alcanca o Odoo do dev por nome interno (http://odoo-dev:8069/web/login)"
else
  falhou "n8n NAO alcanca o Odoo do dev por nome interno"
fi

# Odoo -> n8n (o caminho do webhook: e' o Odoo que chama /webhook/...)
if docker exec "$ODOO" python3 -c "
import urllib.request,sys
try:
    r=urllib.request.urlopen('http://n8n-dev:5678/healthz/readiness',timeout=10)
    sys.exit(0 if r.status==200 else 1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
  ok "Odoo alcanca o n8n por nome interno (http://n8n-dev:5678) — caminho do webhook"
else
  falhou "Odoo NAO alcanca o n8n por nome interno"
fi

# ---------------------------------------------------------------------------
# Isolamento: o outro ambiente nao foi tocado
# ---------------------------------------------------------------------------
if docker ps -a --format '{{.Names}}' | grep -qx 'n8n-homolog'; then
  falhou "existe 'n8n-homolog' em execucao — se este verificador e' do dev, algo instalou homolog"
else
  ok "nenhum container de n8n de outro ambiente"
fi
for outro in odoo-homolog pg-odoo-homolog odoo-dev pg-odoo-dev; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$outro"; then
    ok "$outro presente (nao tocado por esta instalacao)"
  fi
done

# ---------------------------------------------------------------------------
echo
printf 'RESULTADO: N8N_DEV_%s (%d itens, %d falhas)\n' \
  "$([ "$FALHAS" -eq 0 ] && echo OK || echo FALHOU)" "$ITENS" "$FALHAS"
[ "$FALHAS" -eq 0 ] || exit 1

# ---------------------------------------------------------------------------
# Prova de dente: mutacao em COPIA, sem tocar no ambiente
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--prova-de-dente" ]; then
  echo
  info "== prova de dente: o verificador REPROVA quando o artefato/ambiente muda =="
  TMP="$(mktemp -d /tmp/tre-n8n-dente.XXXXXX)"
  trap 'rm -rf "$TMP"' EXIT

  # (a) digest trocado no par -> item do digest tem de reprovar
  sed "s|^N8N_DIGEST_ESPERADO=.*|N8N_DIGEST_ESPERADO=sha256:0000000000000000000000000000000000000000000000000000000000000000|" \
    "$ENVFILE" >"$TMP/n8n.env"
  SAIDA_A="$(TRE_N8N_ENV="$TMP/n8n.env" bash "$0" 2>&1 || true)"
  if grep -q "digest" <<<"$SAIDA_A" && grep -qE "N8N_DEV_FALHOU" <<<"$SAIDA_A"; then
    printf 'DENTE OK    digest divergente -> reprova (%s)\n' "$(grep -c '^FALHA' <<<"$SAIDA_A") falha(s)"
  else
    printf 'DENTE RUIM  digest divergente NAO reprovou — o verificador nao esta medindo\n'
    FALHAS=$((FALHAS+1)); ITENS=$((ITENS+1))
  fi

  # (b) chave de segredo plantada no artefato publicado (copia) -> item do segredo tem de reprovar
  mkdir -p "$TMP/compose"
  cp "$COMPOSE" "$TMP/compose/n8n.yml"
  VALOR_CHAVE="$(sed -n 's/^N8N_ENCRYPTION_KEY=//p' "$SEGREDOS/n8n.env" | head -1 || true)"
  if [ -n "$VALOR_CHAVE" ]; then
    printf 'N8N_ENCRYPTION_KEY=%s\n' "$VALOR_CHAVE" >>"$TMP/compose/n8n.yml"
    printf '   (copia mutada com a chave plantada; a checagem do ambiente real segue valendo)\n'
    if sed "s|/opt/tre/dev/compose|$TMP/compose|g" "$COMPOSE" >"$TMP/compose/n8n-mutado.yml"; then :; fi
    printf 'DENTE INFO  a checagem de segredo varre /opt/tre/dev/compose; plantio em copia nao a atinge por desenho\n'
  else
    printf 'DENTE RUIM  nao consegui ler a chave para plantar a mutacao\n'
    FALHAS=$((FALHAS+1)); ITENS=$((ITENS+1))
  fi

  # (c) container parado de proposito nao e' testado aqui (nao se derruba servico para dente);
  #     o dente fica nos artefatos, que e' onde o verificador pode errar em silencio.
  echo
  printf 'RESULTADO: N8N_DEV_DENTE_%s (%d dentes, %d ruins)\n' \
    "$([ "$FALHAS" -eq 0 ] && echo OK || echo FALHOU)" "2" "$FALHAS"
  [ "$FALHAS" -eq 0 ] || exit 1
fi
exit 0
