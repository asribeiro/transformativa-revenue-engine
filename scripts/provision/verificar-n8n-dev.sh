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
# Diretorio do artefato publicado varrido em busca de segredo vazado. Parametrizado de proposito:
# e' por ele que a prova de dente aponta para uma COPIA com a chave plantada, sem tocar no ambiente.
ARTEFATO_DIR="${TRE_N8N_ARTEFATO_DIR:-/opt/tre/dev/compose}"
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

  # Identidade da imagem em DUAS pontas, porque sao duas coisas diferentes e confundi-las gera
  # falso positivo (medido em 06/10/2026):
  #   * o digest do REGISTRY (RepoDigest do tag) e' o do manifesto multi-arquitetura — e' o que o
  #     par registra como identidade do que foi baixado;
  #   * `.ImageManifestDescriptor.Digest` do container e' o manifesto POR-PLATAFORMA (diferente,
  #     por desenho); a prova de "o container roda exatamente este tag" e' o ID da imagem.
  DIGESTO_TAG="$(docker image inspect "${IMAGEM}:${VERSAO}" --format '{{index .RepoDigests 0}}' 2>/dev/null | sed 's/^.*@//')"
  [ "$DIGESTO_TAG" = "$DIGESTO_ESPERADO" ] && ok "digest do registry do tag pinado == registrado no par" \
    || falhou "digest do tag pinado ($DIGESTO_TAG) != registrado no par ($DIGESTO_ESPERADO)"

  ID_TAG="$(docker image inspect "${IMAGEM}:${VERSAO}" --format '{{.Id}}' 2>/dev/null)"
  ID_CT="$(docker inspect "$CONTAINER" --format '{{.Image}}' 2>/dev/null)"
  [ -n "$ID_TAG" ] && [ "$ID_CT" = "$ID_TAG" ] && ok "o container roda exatamente a imagem do tag (id ${ID_TAG:0:19})" \
    || falhou "imagem local do tag ($ID_TAG) != imagem do container ($ID_CT)"

  SAUDE="$(docker inspect "$CONTAINER" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-healthcheck{{end}}')"
  [ "$SAUDE" = "healthy" ] && ok "healthcheck do container: healthy" || falhou "healthcheck do container: $SAUDE"
fi

# ---------------------------------------------------------------------------
# Porta: publica SO em loopback
# ---------------------------------------------------------------------------
PUBLICADAS="$(docker inspect "$CONTAINER" --format '{{json .NetworkSettings.Ports}}' 2>/dev/null || echo '{}')"
# O JSON do docker e' {"5678/tcp":[{"HostIp":"127.0.0.1","HostPort":"5680"}]}: procurar a string
# "127.0.0.1:5680" nao casa com nada (falso positivo medido). Aqui as duas chaves sao conferidas.
if grep -q "\"HostPort\":\"${PORTA}\"" <<<"$PUBLICADAS" && grep -q '"HostIp":"127.0.0.1"' <<<"$PUBLICADAS"; then
  ok "porta publicada so em loopback (127.0.0.1:$PORTA -> 5678)"
else
  falhou "porta do n8n nao esta publicada em 127.0.0.1:$PORTA ($PUBLICADAS)"
fi
if grep -qE '"HostIp":"0\.0\.0\.0"|HostIp":"::' <<<"$PUBLICADAS"; then
  falhou "ha publicacao em 0.0.0.0/:: no container"
else
  ok "nenhuma publicacao em 0.0.0.0/::"
fi
if [ -n "$PORTA" ] && ss -lntH "sport = :$PORTA" 2>/dev/null | grep -q "127.0.0.1:$PORTA"; then
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

CODIGO="$(curl -sS -o /dev/null -m 15 -w '%{http_code}' "http://127.0.0.1:$PORTA/healthz/readiness" 2>/dev/null || true)"
CODIGO="${CODIGO:-000}"
[ "$CODIGO" = "200" ] && ok "readiness 200 pelo loopback publicado (127.0.0.1:$PORTA)" \
  || falhou "readiness pelo loopback devolveu $CODIGO (esperado 200)"

CODIGO_UI="$(curl -sS -o /dev/null -m 15 -w '%{http_code}' "http://127.0.0.1:$PORTA/" 2>/dev/null || true)"
CODIGO_UI="${CODIGO_UI:-000}"
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
  if [ -n "$VALOR" ] && grep -rqF "$VALOR" "$ARTEFATO_DIR/" 2>/dev/null; then
    falhou "a chave do n8n aparece em arquivo publicado ($ARTEFATO_DIR)"
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
# Contaminacao medida: o n8n de OUTRO ambiente pode existir (cada ambiente tem o seu) — o que nao
# pode e' entrar na MINHA rede ou publicar a MINHA porta. "Existe n8n-homolog" nao e' falha; medir
# a invasao e' que e'.
CONTAMINADO=""
for outro in n8n-homolog n8n-prod; do
  docker ps -a --format '{{.Names}}' | grep -qx "$outro" || continue
  if docker inspect "$outro" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | grep -qw "$REDE"; then
    CONTAMINADO="$outro esta na minha rede ($REDE)"
  fi
  if [ -n "$PORTA" ] && docker inspect "$outro" --format '{{json .NetworkSettings.Ports}}' 2>/dev/null | grep -q "\"HostPort\":\"$PORTA\""; then
    CONTAMINADO="$outro publica a minha porta ($PORTA)"
  fi
done
[ -z "$CONTAMINADO" ] && ok "n8n de outro ambiente nao invade a rede nem a porta do dev" \
  || falhou "contaminacao entre ambientes: $CONTAMINADO"
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
  DENTES=0
  DENTES_RUINS=0

  # (a) digest trocado no par (COPIA) -> item do digest tem de reprovar
  sed "s|^N8N_DIGEST_ESPERADO=.*|N8N_DIGEST_ESPERADO=sha256:0000000000000000000000000000000000000000000000000000000000000000|" \
    "$ENVFILE" >"$TMP/n8n.env"
  SAIDA_A="$(TRE_N8N_ENV="$TMP/n8n.env" bash "$0" 2>&1 || true)"
  DENTES=$((DENTES+1))
  if grep -qE '^FALHA .*digest' <<<"$SAIDA_A" && grep -qE '^RESULTADO: N8N_DEV_FALHOU' <<<"$SAIDA_A"; then
    printf 'DENTE OK    digest divergente -> reprova (%s falha(s))\n' "$(grep -c '^FALHA' <<<"$SAIDA_A")"
  else
    printf 'DENTE RUIM  digest divergente NAO reprovou — o verificador nao esta medindo o digest\n'
    DENTES_RUINS=$((DENTES_RUINS+1))
  fi

  # (b) chave do n8n plantada no artefato publicado (COPIA, via TRE_N8N_ARTEFATO_DIR) -> o item do
  #     segredo tem de reprovar. Plantar em copia e' o que permite provar a checagem SEM sujar o
  #     ambiente real.
  mkdir -p "$TMP/artefato"
  cp "$COMPOSE" "$TMP/artefato/n8n.yml"
  VALOR_CHAVE="$(sed -n 's/^N8N_ENCRYPTION_KEY=//p' "$SEGREDOS/n8n.env" | head -1 || true)"
  DENTES=$((DENTES+1))
  if [ -n "$VALOR_CHAVE" ]; then
    printf '# valor plantado de proposito para a prova: %s\n' "$VALOR_CHAVE" >>"$TMP/artefato/n8n.yml"
    SAIDA_B="$(TRE_N8N_ARTEFATO_DIR="$TMP/artefato" bash "$0" 2>&1 || true)"
    if grep -qE '^FALHA .*chave do n8n aparece' <<<"$SAIDA_B"; then
      printf 'DENTE OK    chave plantada no artefato publicado -> reprova\n'
    else
      printf 'DENTE RUIM  chave plantada no artefato publicado NAO reprovou\n'
      DENTES_RUINS=$((DENTES_RUINS+1))
    fi
  else
    printf 'DENTE RUIM  nao consegui ler a chave para plantar a mutacao\n'
    DENTES_RUINS=$((DENTES_RUINS+1))
  fi

  # (c) par sem a porta declarada -> o verificador tem de reprovar (guarda contra par incompleto)
  sed "s|^N8N_PORTA_LOCAL=.*|N8N_PORTA_LOCAL=|" "$ENVFILE" >"$TMP/n8n-sem-porta.env"
  SAIDA_C="$(TRE_N8N_ENV="$TMP/n8n-sem-porta.env" bash "$0" 2>&1 || true)"
  DENTES=$((DENTES+1))
  if grep -qE '^FALHA .*par incompleto' <<<"$SAIDA_C"; then
    printf 'DENTE OK    par sem porta declarada -> reprova\n'
  else
    printf 'DENTE RUIM  par sem porta declarada NAO reprovou\n'
    DENTES_RUINS=$((DENTES_RUINS+1))
  fi

  echo
  printf 'RESULTADO: N8N_DEV_DENTE_%s (%d dentes, %d ruins)\n' \
    "$([ "$DENTES_RUINS" -eq 0 ] && echo OK || echo FALHOU)" "$DENTES" "$DENTES_RUINS"
  [ "$DENTES_RUINS" -eq 0 ] || exit 1
fi
exit 0
