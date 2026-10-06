#!/usr/bin/env bash
# Instalacao do n8n no ambiente DEV do TRE (VPS Contabo `vmi3619453`).
#
# Runbook: docs/runbooks/n8n-dev.md. Par nao-secreto: deploy/environments/dev-n8n.env
# (na VPS, /opt/tre/dev/compose/n8n.env).
#
# Roda NA VPS (precisa de `docker` e da arvore /opt/tre); o container do Hermes apenas orquestra
# por SSH (ADR-0008).
#
# O que este script garante (e o verificador cobra depois):
#   * ambiente alvo e' o dev e SO ele: compose sob /opt/tre/dev, segredos em /etc/tre/n8n-dev,
#     container `n8n-dev` — e um teste de isolamento do COMPOSE RESOLVIDO (falha se o artefato
#     apontar para nome de container/rede de outro ambiente);
#   * a chave de criptografia do n8n nasce NA VPS (`openssl rand`), vai para arquivo 600 e nunca
#     aparece em argumento, saida ou log. Chave existente NAO e' reescrita: trocar a chave
#     invalida as credenciais ja cifradas nos workflows (por isso a instalacao e' idempotente,
#     nao "recomecavel");
#   * a versao/digest vem do par nao-secreto (o que esta no ar e' o digest registrado);
#   * HOME inteiro do n8n e' um bind mount em /home/node (armadilha medida: o n8n escreve
#     `~/.n8n` E `~/.cache`).
#
# Uso (na VPS):
#   bash instalar-n8n-dev.sh
#
# Variaveis:
#   TRE_N8N_COMPOSE    (padrao /opt/tre/dev/compose/n8n.yml)
#   TRE_N8N_ENV        (padrao /opt/tre/dev/compose/n8n.env)
#   TRE_N8N_SEGREDOS   (padrao /etc/tre/n8n-dev)
#   TRE_N8N_HOME       (padrao /opt/tre/dev/n8n/home)
#   TRE_N8N_RECRIAR=1  permite reexecutar sobre instalacao que ja existe
#   TRE_N8N_ESPERA     segundos de espera pelo readiness (padrao 120)
set -euo pipefail

COMPOSE="${TRE_N8N_COMPOSE:-/opt/tre/dev/compose/n8n.yml}"
ENVFILE="${TRE_N8N_ENV:-/opt/tre/dev/compose/n8n.env}"
SEGREDOS="${TRE_N8N_SEGREDOS:-/etc/tre/n8n-dev}"
HOME_N8N="${TRE_N8N_HOME:-/opt/tre/dev/n8n/home}"
RECRIAR="${TRE_N8N_RECRIAR:-0}"
ESPERA="${TRE_N8N_ESPERA:-120}"
CONTAINER="n8n-dev"
REDE="tre-odoo-dev"
UID_CONTAINER="1000:1000"

falhar() { echo "FALHOU $*" >&2; exit 1; }
compose() { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }

# Uso: [--ensaio]. O `--ensaio` imprime o PLANO e sai sem criar nada: nenhum container, volume, rede,
# diretorio, segredo ou arquivo; nao baixa imagem e nao publica artefato. Flag desconhecida RECUSA.
# Por que o flag existe: ate 06/10/2026 este instalador nao aceitava argumento nenhum, entao `--ensaio`
# era engolido EM SILENCIO e a instalacao executava de verdade — o mesmo defeito ja medido no
# instalador do Odoo, que agora tambem recusa flag desconhecida (fail-closed).
ENSAIO=0
for arg in "$@"; do
  case "$arg" in
    --ensaio) ENSAIO=1 ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) falhar "argumento desconhecido: '$arg' (uso: $0 [--ensaio])" ;;
  esac
done

if [ "$ENSAIO" = "1" ]; then
  # O ensaio roda ANTES das guardas de estado (elas protegem a execucao real; um plano nao muda nada),
  # mas exige os mesmos arquivos de entrada que a execucao real.
  [ -f "$COMPOSE" ] || falhar "compose nao encontrado em $COMPOSE"
  [ -f "$ENVFILE" ] || falhar "par nao-secreto nao encontrado em $ENVFILE"
  # shellcheck disable=SC1090
  set -a; . "$ENVFILE"; set +a
  IMAGEM="${N8N_IMAGEM:?N8N_IMAGEM ausente em $ENVFILE}"
  VERSAO_ALVO="${N8N_VERSAO:?N8N_VERSAO ausente em $ENVFILE}"
  DIGESTO_ALVO="${N8N_DIGEST_ESPERADO:-nao declarado}"
  PORTA_ALVO="${N8N_PORTA_LOCAL:?N8N_PORTA_LOCAL ausente em $ENVFILE}"
  HOST_ALVO="${N8N_HOST:?N8N_HOST ausente em $ENVFILE}"
  ODOO_IRMAO="${CONTAINER/n8n-/odoo-}"
  if [ -f "$SEGREDOS/n8n.env" ] && grep -q '^N8N_ENCRYPTION_KEY=..*' "$SEGREDOS/n8n.env"; then
    CHAVE_PLANO="ja existe em $SEGREDOS/n8n.env — preservada (reescrever invalidaria as credenciais cifradas nos workflows)"
  else
    CHAVE_PLANO="sera criada em $SEGREDOS/n8n.env (openssl rand -hex 32, 600 root) — o valor nao passa por argv"
  fi
  echo
  echo "   PLANO (ensaio: nada e' criado, baixado nem publicado)"
  printf '     imagem.......... %s:%s\n' "$IMAGEM" "$VERSAO_ALVO"
  printf '     digest esperado. %s\n' "$DIGESTO_ALVO"
  printf '     container....... %s (rede %s)\n' "$CONTAINER" "$REDE"
  printf '     destino......... 127.0.0.1:%s -> 5678/tcp (loopback; quem expoe e a borda)\n' "$PORTA_ALVO"
  printf '     host (N8N_HOST). %s (nome interno; o Odoo chama o webhook por ele)\n' "$HOST_ALVO"
  printf '     home (bind)..... %s -> /home/node (uid %s; o n8n guarda ~/.n8n E ~/.cache)\n' "$HOME_N8N" "$UID_CONTAINER"
  printf '     segredos........ %s (600, root, NA VPS)\n' "$SEGREDOS"
  printf '     chave........... %s\n' "$CHAVE_PLANO"
  printf '     ordem........... segredo nasce ANTES de `compose config` (o env_file tem de existir)\n'
  printf '     isolamento...... recusa container/rede de outros ambientes no compose RESOLVIDO\n'
  printf '     pre-requisito... rede %s de pe e container %s rodando (o n8n entra na rede dele)\n' "$REDE" "$ODOO_IRMAO"
  echo "   ENSAIO: nada foi criado, baixado nem publicado."
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. Guardas (fail-closed: se qualquer uma nao passa, nada e' criado)
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || falhar "docker nao esta no PATH (este script roda NA VPS do TRE)"
docker info >/dev/null 2>&1 || falhar "daemon do docker nao responde"
command -v openssl >/dev/null 2>&1 || falhar "openssl ausente (a chave do n8n nasce aqui)"
[ -f "$COMPOSE" ] || falhar "compose nao encontrado em $COMPOSE"
[ -f "$ENVFILE" ] || falhar "par nao-secreto nao encontrado em $ENVFILE"

case "$COMPOSE" in
  /opt/tre/dev/*) : ;;
  *) falhar "compose fora do ambiente dev ($COMPOSE) — este script so opera /opt/tre/dev" ;;
esac
[ "$SEGREDOS" = "/etc/tre/n8n-dev" ] || falhar "segredos fora de /etc/tre/n8n-dev ($SEGREDOS)"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER" && [ "$RECRIAR" != "1" ]; then
  falhar "container '$CONTAINER' JA EXISTE — nao mexo nele sem TRE_N8N_RECRIAR=1"
fi

# O n8n do dev entra na rede do Odoo do dev (e' por ela que o Odoo chama o webhook). Se a rede
# nao existe, instalar n8n aqui criaria uma rede vazia com o mesmo nome — errado e silencioso.
docker network inspect "$REDE" >/dev/null 2>&1 \
  || falhar "rede '$REDE' nao existe — instale o Odoo do dev antes (scripts/provision/instalar-odoo-dev.sh)"
docker ps --format '{{.Names}}' | grep -qx 'odoo-dev' \
  || falhar "container 'odoo-dev' nao esta de pe — o n8n do dev depende dele (rede + API/webhook)"

# shellcheck disable=SC1090
set -a; . "$ENVFILE"; set +a
IMAGEM="${N8N_IMAGEM:?N8N_IMAGEM ausente em $ENVFILE}"
VERSAO="${N8N_VERSAO:?N8N_VERSAO ausente em $ENVFILE}"
DIGESTO_ESPERADO="${N8N_DIGEST_ESPERADO:?N8N_DIGEST_ESPERADO ausente em $ENVFILE}"
PORTA="${N8N_PORTA_LOCAL:?N8N_PORTA_LOCAL ausente em $ENVFILE}"
HOST_N8N="${N8N_HOST:?N8N_HOST ausente em $ENVFILE}"

# Porta livre so importa quando o nosso container ainda nao existe (com ele de pe, a porta e dele).
if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  if ss -lntH "sport = :$PORTA" 2>/dev/null | grep -q .; then
    falhar "porta $PORTA ja esta em uso nesta maquina (e o container '$CONTAINER' nao existe) — escolha outra em $ENVFILE"
  fi
fi

# ---------------------------------------------------------------------------
# 2. Segredos do n8n (na VPS, 600, sem passar por argv)
#
# ORDEM IMPORTA (armadilha medida DUAS vezes neste projeto — dev e homolog): a validacao do
# compose resolve o `env_file`, entao `docker compose config` FALHA enquanto o arquivo de segredo
# nao existe. Segredo nasce primeiro; artefato e' validado depois.
# ---------------------------------------------------------------------------
echo "-- segredos em $SEGREDOS"
install -d -m 700 -o root -g root "$SEGREDOS"
if [ -f "$SEGREDOS/n8n.env" ]; then
  if grep -q '^N8N_ENCRYPTION_KEY=..*' "$SEGREDOS/n8n.env"; then
    echo "   chave JA EXISTE — preservada (trocar invalidaria as credenciais cifradas nos workflows)"
  else
    printf 'N8N_ENCRYPTION_KEY=%s\n' "$(openssl rand -hex 32)" >"$SEGREDOS/n8n.env"
    echo "   chave criada"
  fi
else
  printf 'N8N_ENCRYPTION_KEY=%s\n' "$(openssl rand -hex 32)" >"$SEGREDOS/n8n.env"
  echo "   chave criada"
fi
chmod 600 "$SEGREDOS/n8n.env"
chown root:root "$SEGREDOS/n8n.env"

# ---------------------------------------------------------------------------
# 3. Validacao do artefato (depois dos segredos, por causa do env_file)
# ---------------------------------------------------------------------------
echo "-- validando o artefato"
compose config -q </dev/null || falhar "compose invalido ($COMPOSE com $ENVFILE)"

RESOLVIDO="$(compose config </dev/null)"
grep -q "container_name: $CONTAINER" <<<"$RESOLVIDO" || falhar "compose resolvido nao declara container_name: $CONTAINER"

# Isolamento medido no COMPOSE RESOLVIDO (nao em prosa): nenhum container de outro ambiente pode
# aparecer no artefato do dev.
for intruso in n8n-homolog n8n-prod odoo-homolog odoo-prod; do
  if grep -qE "(container_name|name): *${intruso}\b" <<<"$RESOLVIDO"; then
    falhar "o compose do dev cita container/rede de outro ambiente ($intruso) — artefato trocado?"
  fi
done

# ---------------------------------------------------------------------------
# 4. HOME do n8n (bind mount; o container roda como 1000:1000)
# ---------------------------------------------------------------------------
echo "-- home do n8n em $HOME_N8N"
# `install -d -o` so aceita NOME de usuario (getpwnam): com uid numerico ele morre em
# "install: invalid user: '1000:1000'" — medido. Aqui o dono e' numerico de proposito (o usuario
# 1000 do host e' `ubuntu`, nao o `node` do container).
mkdir -p "$HOME_N8N"
chown -R "$UID_CONTAINER" "$HOME_N8N"
chmod 700 "$HOME_N8N"

# ---------------------------------------------------------------------------
# 5. Imagem: sobe a versao PINADA e confere o digest (fail-closed)
# ---------------------------------------------------------------------------
echo "-- imagem ${IMAGEM}:${VERSAO}"
docker pull --quiet "${IMAGEM}:${VERSAO}" >/dev/null || falhar "pull falhou (${IMAGEM}:${VERSAO})"
DIGESTO_REAL="$(docker image inspect "${IMAGEM}:${VERSAO}" --format '{{index .RepoDigests 0}}' | sed 's/^.*@//')"
if [ "$DIGESTO_REAL" != "$DIGESTO_ESPERADO" ]; then
  falhar "digest diferente do registrado no par: real=$DIGESTO_REAL esperado=$DIGESTO_ESPERADO — ATUALIZE deploy/environments/dev-n8n.env (com olho na versao) antes de subir"
fi
echo "   digest confere: $DIGESTO_REAL"

# ---------------------------------------------------------------------------
# 6. Subir e esperar o readiness
# ---------------------------------------------------------------------------
echo "-- subindo ($CONTAINER)"
compose up -d </dev/null

PRONTO=0
for _ in $(seq 1 "$ESPERA"); do
  if docker exec --user "$UID_CONTAINER" "$CONTAINER" node -e \
      "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))" \
      >/dev/null 2>&1; then PRONTO=1; break; fi
  sleep 1
done
if [ "$PRONTO" != "1" ]; then
  echo "-- ultimas 40 linhas do log do $CONTAINER:" >&2
  docker logs --tail 40 "$CONTAINER" >&2 || true
  falhar "n8n nao ficou pronto em ${ESPERA}s (/healthz/readiness)"
fi

# O estado do n8n tem de estar no bind mount (se cair, ele volta com o mesmo banco/chave).
[ -f "$HOME_N8N/.n8n/database.sqlite" ] || echo "   AVISO: $HOME_N8N/.n8n/database.sqlite ainda nao existe (o n8n cria no primeiro start completo)"

echo "RESULTADO: N8N_DEV_INSTALADO container=$CONTAINER imagem=${IMAGEM}:${VERSAO} digest=$DIGESTO_REAL host=$HOST_N8N porta=127.0.0.1:$PORTA home=$HOME_N8N segredos=$SEGREDOS/n8n.env"
exit 0
