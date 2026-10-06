#!/usr/bin/env bash
# Instalacao do proxy TLS + hardening do Odoo no ambiente DEV do TRE (VPS Contabo `vmi3619453`).
#
# Card: TRE-W2-E01-T02 (`t_1acf11f2`). Runbook: docs/runbooks/odoo-dev-tls.md.
#
# Roda NA VPS (precisa de `docker`, `ufw` e da arvore /opt/tre); o container do Hermes apenas
# orquestra por SSH (ADR-0008). Nao toca homologacao nem producao: por construcao so opera o
# ambiente DEV e recusa se achar container de outro ambiente.
#
# O que faz, em ordem:
#   1. guardas (fail-closed: se qualquer uma nao passa, NADA e criado nem aberto)
#   2. segredos do proxy: usuario/senha/hash do basic auth nascem AQUI e nunca saem da VPS
#   3. UFW: registra o estado ANTERIOR (base do rollback) e libera SO as portas decididas
#   4. Odoo pronto para ficar atras do proxy (`proxy_mode`), com restart so se mudou
#   5. compose + subida do proxy, com espera de resposta real
#   6. evidencia de deploy (identidade do artefato, hora, destino, portas)
#
# Uso (na VPS):
#   bash instalar-proxy-dev.sh
#
# Variaveis:
#   TRE_PROXY_COMPOSE  (padrao /opt/tre/dev/compose/proxy.yml)
#   TRE_PROXY_ENV      (padrao /opt/tre/dev/compose/proxy.env)
#   TRE_PROXY_SEGREDOS (padrao /etc/tre/proxy-dev)
#   TRE_PROXY_ESTADO   (padrao /opt/tre/dev/proxy)
#   TRE_PROXY_RECRIAR=1 permite reexecutar sobre uma instalacao que ja existe
set -euo pipefail

COMPOSE="${TRE_PROXY_COMPOSE:-/opt/tre/dev/compose/proxy.yml}"
ENVFILE="${TRE_PROXY_ENV:-/opt/tre/dev/compose/proxy.env}"
SEGREDOS="${TRE_PROXY_SEGREDOS:-/etc/tre/proxy-dev}"
ESTADO="${TRE_PROXY_ESTADO:-/opt/tre/dev/proxy}"
RECRIAR="${TRE_PROXY_RECRIAR:-0}"
ODOO_COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/dev/compose/odoo.yml}"
ODOO_ENV="${TRE_ODOO_ENV:-/opt/tre/dev/compose/odoo.env}"
ODOO_CONF="${TRE_ODOO_CONF:-/etc/tre/odoo-dev/odoo.conf}"
UMA_PORTA_FORA=0   # rastreado para o estado final da mensagem de erro

falhar() { echo "FALHOU $*" >&2; exit 1; }

compose()  { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }
compose_odoo() { docker compose --env-file "$ODOO_ENV" -f "$ODOO_COMPOSE" "$@"; }

# ---------------------------------------------------------------------------------------
# 1. Guardas
# ---------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || falhar "docker nao esta no PATH (este script roda NA VPS do TRE)"
docker info >/dev/null 2>&1 || falhar "daemon do docker nao responde"
command -v ufw >/dev/null 2>&1 || falhar "ufw nao esta instalado nesta maquina"
[ -f "$COMPOSE" ] || falhar "compose nao encontrado em $COMPOSE"
[ -f "$ENVFILE" ] || falhar "par nao-secreto nao encontrado em $ENVFILE"
[ -f "$ODOO_COMPOSE" ] || falhar "compose do Odoo nao encontrado em $ODOO_COMPOSE (o T01 e dependencia deste card)"
[ -f "$ODOO_ENV" ] || falhar "par do Odoo nao encontrado em $ODOO_ENV"
[ -f /opt/tre/dev/compose/Caddyfile ] || falhar "Caddyfile nao encontrado em /opt/tre/dev/compose/Caddyfile"

case "$COMPOSE" in
  /opt/tre/dev/*) : ;;
  *) falhar "compose fora do ambiente dev ($COMPOSE) — este script so opera /opt/tre/dev" ;;
esac
[ "$SEGREDOS" = "/etc/tre/proxy-dev" ] || falhar "caminho de segredos inesperado ($SEGREDOS)"
for nome in odoo-homolog odoo-prod pg-odoo-homolog pg-odoo-prod proxy-homolog proxy-prod; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$nome"; then
    falhar "container de outro ambiente existe ($nome) — este script so opera o dev"
  fi
done
docker ps --format '{{.Names}}' | grep -qx 'odoo-dev' \
  || falhar "container 'odoo-dev' nao esta de pe — o proxy sobe na frente do Odoo (rode o T01 antes)"
# O dev e compartilhado por mais de um card do perfil `devops`; um `compose run` de outro card
# pode deixar o `odoo-dev` parado por alguns segundos. Espera curta e EXPLICITA, para o guarda
# nao reprovar por um instante transitorio (medido em 01/10/2026) — e ainda assim fail-closed.
if [ "$(docker inspect odoo-dev --format '{{.State.Running}}' 2>/dev/null)" != "true" ]; then
  echo "    aguardando o 'odoo-dev' subir (container compartilhado do dev)…"
  for _ in $(seq 1 30); do
    sleep 3
    [ "$(docker inspect odoo-dev --format '{{.State.Running}}' 2>/dev/null)" = "true" ] && break
  done
  [ "$(docker inspect odoo-dev --format '{{.State.Running}}' 2>/dev/null)" = "true" ] \
    || falhar "container 'odoo-dev' continua parado depois de 90s"
fi
if docker ps -a --format '{{.Names}}' | grep -qx 'proxy-dev' && [ "$RECRIAR" != "1" ]; then
  falhar "container 'proxy-dev' JA EXISTE — nao mexo nele sem TRE_PROXY_RECRIAR=1"
fi

# shellcheck disable=SC1090
set -a; . "$ENVFILE"; set +a
HOSTNAME_PROXY="${PROXY_HOSTNAME:?PROXY_HOSTNAME ausente em $ENVFILE}"
P_HTTP="${PROXY_PORTA_HTTP:?PROXY_PORTA_HTTP ausente em $ENVFILE}"
P_HTTPS="${PROXY_PORTA_HTTPS:?PROXY_PORTA_HTTPS ausente em $ENVFILE}"
IMAGEM="${PROXY_IMAGEM:?PROXY_IMAGEM ausente em $ENVFILE}"
DIGESTO_ESPERADO="${PROXY_DIGEST_ESPERADO:-}"
USUARIO="${PROXY_USUARIO:-tre-dev}"

# Porta ocupada so importa quando o NOSSO container ainda nao existe (a licao do T01: a
# checagem ingenua reprovava a reexecucao idempotente porque o proprio proxy segurava a porta).
if ! docker ps -a --format '{{.Names}}' | grep -qx 'proxy-dev'; then
  for p in "$P_HTTP" "$P_HTTPS"; do
    if ss -lntH "sport = :$p" 2>/dev/null | grep -q .; then
      falhar "porta $p ja esta em uso nesta maquina (e o container 'proxy-dev' nao existe) — escolha outra em $ENVFILE"
    fi
  done
fi

echo "OK    guardas: docker $(docker --version | awk '{print $3}' | tr -d ','), compose $(docker compose version --short), ufw $(ufw version | head -1 | awk '{print $2}')"

# ---------------------------------------------------------------------------------------
# 2. Segredos do proxy (nascem AQUI; nao vao para artefato, argumento nem log)
# ---------------------------------------------------------------------------------------
umask 077
if [ ! -d "$SEGREDOS" ]; then
  mkdir -p "$SEGREDOS"; chmod 700 "$SEGREDOS"
  chown tre-deploy:tre-deploy "$SEGREDOS" 2>/dev/null || true
fi

if [ -f "$SEGREDOS/basicauth.env" ]; then
  echo "OK    segredos: reaproveitando $SEGREDOS/basicauth.env existente"
  # DEFEITO 3 (achado executando): NAO dar `source` neste arquivo. O hash do basic auth e um
  # bcrypt (`$2a$14$...`): o shell tentaria expandir `$2` e, sob `set -u`, o script morre com
  # "unbound variable" na REexecucao. Le-se por `sed`, como o T01 ja faz com os segredos dele.
  # As aspas simples do arquivo saem na leitura (ver DEFEITO 4, na escrita).
  le_segredo() { sed -n "s/^$1=//p" "$SEGREDOS/basicauth.env" | head -1 | sed "s/^'//; s/'\$//"; }
  SENHA="$(le_segredo TRE_PROXY_SENHA)"
  HASH="$(le_segredo TRE_PROXY_HASH)"
  [ -n "$SENHA" ] || falhar "$SEGREDOS/basicauth.env existe mas nao tem TRE_PROXY_SENHA"
  [ -n "$HASH" ] || falhar "$SEGREDOS/basicauth.env existe mas nao tem TRE_PROXY_HASH"
  case "$HASH" in
    '$2'*) : ;;
    *) falhar "hash do basic auth em $SEGREDOS/basicauth.env nao parece bcrypt — recuso subir com ele" ;;
  esac
else
  SENHA="$(openssl rand -hex 16)"
  # O hash e gerado pelo proprio caddy, com a senha por STDIN: nada de senha em argumento de
  # comando (policy de secrets V1) e nada de hash "na mao" que o caddy recuse depois.
  # DEFEITO 1 (achado executando): o `caddy hash-password` le uma LINHA do stdin — sem o `\n`
  # final ele morre com `Error: EOF`; e a primeira versao mandava o stderr para /dev/null e o
  # script terminava sem dizer por que. Agora o erro e capturado e MOSTRADO.
  ERRO_HASH="$(mktemp)"
  HASH="$(printf '%s\n' "$SENHA" | docker run -i --name proxy-hash-dev --entrypoint caddy "$IMAGEM" hash-password 2>"$ERRO_HASH" | tail -1)" || true
  docker rm -f proxy-hash-dev >/dev/null 2>&1 || true
  case "$HASH" in
    '$2'*) : ;;
    *)
      echo "FALHOU nao consegui gerar o hash do basic auth" >&2
      sed 's/^/      /' "$ERRO_HASH" >&2
      rm -f "$ERRO_HASH"
      exit 1
      ;;
  esac
  rm -f "$ERRO_HASH"
  USUARIO_CHAVE='TRE_PROXY_USUARIO'
  SENHA_CHAVE='TRE_PROXY_SENHA'
  HASH_CHAVE='TRE_PROXY_HASH'
  # DEFEITO 4 (achado executando, e o mais traicoeiro): o `docker compose` INTERPOLA `$` tambem
  # nos valores de `env_file`. Um hash bcrypt cru (`$2a$14$...`) tem o sal lido como nome de
  # variavel e EXPANDIDO PARA VAZIO — o container recebia um hash truncado ("hashedSecret too
  # short") e o basic auth recusava TUDO. Pior: e INTERMITENTE, porque depende do primeiro
  # caractere do sal sorteado (com digito sobrevive, com letra e comido). Por isso o valor vai
  # entre ASPAS SIMPLES: medido no alvo (reproducao minima), o compose passa o literal intacto e
  # o container recebe o valor SEM as aspas. Quem le, tira as aspas (ver `le_segredo`).
  {
    printf "%s='%s'\n" "$USUARIO_CHAVE" "$USUARIO"
    printf "%s='%s'\n" "$SENHA_CHAVE" "$SENHA"
    printf "%s='%s'\n" "$HASH_CHAVE" "$HASH"
  } > "$SEGREDOS/basicauth.env"
  chmod 600 "$SEGREDOS/basicauth.env"; chown root:root "$SEGREDOS/basicauth.env"
  echo "OK    segredos gerados: $SEGREDOS/basicauth.env (600; nenhum valor sai da VPS)"
fi

# ---------------------------------------------------------------------------------------
# 3. UFW — registra o estado ANTERIOR (base do rollback) e libera so as portas decididas
# ---------------------------------------------------------------------------------------
mkdir -p "$ESTADO"
if [ ! -f "$ESTADO/ufw-antes.txt" ]; then
  ufw status | awk 'NR>1' > "$ESTADO/ufw-antes.txt"
  echo "OK    UFW: estado anterior registrado em $ESTADO/ufw-antes.txt (base do rollback)"
else
  echo "OK    UFW: estado anterior ja registrado em $ESTADO/ufw-antes.txt (reaproveitado)"
fi
for p in "$P_HTTP" "$P_HTTPS"; do
  ufw allow "$p/tcp" >/dev/null
done
echo "OK    UFW: liberadas $P_HTTP/tcp e $P_HTTPS/tcp (as unicas entradas novas; 8069 continua loopback)"

# ---------------------------------------------------------------------------------------
# 4. Odoo pronto para ficar atras de proxy
# ---------------------------------------------------------------------------------------
MUDOU_CONF=0
if [ ! -f "$ODOO_CONF" ]; then
  falhar "odoo.conf ausente em $ODOO_CONF (dependencia do T01)"
fi
if ! grep -qE '^[[:space:]]*proxy_mode[[:space:]]*=' "$ODOO_CONF"; then
  printf 'proxy_mode = True\n' >> "$ODOO_CONF"
  MUDOU_CONF=1
  echo "OK    odoo.conf: acrescentado 'proxy_mode = True' (o Odoo passa a honrar X-Forwarded-*)"
else
  echo "OK    odoo.conf: 'proxy_mode' ja configurado"
fi
if [ "$MUDOU_CONF" = "1" ]; then
  # `compose up -d` NAO recria o container quando so o arquivo montado mudou; o processo le o
  # odoo.conf no start, entao quem aplica a mudanca e o restart.
  docker restart odoo-dev >/dev/null
  for _ in $(seq 1 30); do
    C="$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8069/web/login || true)"
    [ "$C" = "200" ] && break
    sleep 3
  done
  [ "${C:-000}" = "200" ] || falhar "Odoo nao voltou a responder 200 em loopback depois do restart (codigo $C)"
  echo "OK    odoo-dev reiniciado e respondendo 200 em 127.0.0.1:8069"
fi

# ---------------------------------------------------------------------------------------
# 5. Compose valido + subida do proxy
# ---------------------------------------------------------------------------------------
compose config -q || falhar "docker compose config invalido para $COMPOSE + $ENVFILE"
echo "OK    compose valido: $COMPOSE + $ENVFILE (hostname $HOSTNAME_PROXY, portas $P_HTTP/$P_HTTPS)"

docker rm -f proxy-dev >/dev/null 2>&1 || true
compose up -d >/dev/null

# A prova de que subiu e o SERVICO respondendo no nome esperado, nao o log do compose.
CODIGO="000"
for _ in $(seq 1 40); do
  CODIGO="$(curl -s -o /dev/null -m 8 -w '%{http_code}' \
             --cacert <(docker exec proxy-dev cat /data/caddy/pki/authorities/local/root.crt 2>/dev/null) \
             --resolve "$HOSTNAME_PROXY:$P_HTTPS:127.0.0.1" \
             -u "$USUARIO:$SENHA" "https://$HOSTNAME_PROXY/web/login" 2>/dev/null || true)"
  [ "$CODIGO" = "200" ] && break
  sleep 3
done
[ "$CODIGO" = "200" ] || falhar "proxy nao respondeu 200 em https://$HOSTNAME_PROXY/web/login com a ancora da CA local (ultimo codigo: $CODIGO)"

# ---------------------------------------------------------------------------------------
# 6. Evidencia de deploy (identidade do artefato, hora, destino)
# ---------------------------------------------------------------------------------------
echo
echo "IDENTIDADE"
echo "  imagem.......... $(docker inspect proxy-dev --format '{{.Config.Image}}')"
echo "  digest.......... $(docker image inspect "$IMAGEM" --format '{{index .RepoDigests 0}}')"
if [ -n "$DIGESTO_ESPERADO" ]; then
  MEU_DIGESTO="$(docker image inspect "$IMAGEM" --format '{{index .RepoDigests 0}}')"
  [ "${MEU_DIGESTO#*@}" = "$DIGESTO_ESPERADO" ] \
    && echo "OK    digest confere com $ENVFILE" \
    || falhar "digest da imagem NAO confere com PROXY_DIGEST_ESPERADO ($DIGESTO_ESPERADO)"
fi
echo "  versao caddy.... $(docker exec proxy-dev caddy version 2>/dev/null)"
echo "  container....... $(docker inspect proxy-dev --format '{{.Name}}=id={{.Id}}')"
echo "  iniciado_em..... $(docker inspect proxy-dev --format '{{.State.StartedAt}}')"
echo "  destino......... $HOSTNAME_PROXY:$P_HTTPS (publico) -> 127.0.0.1:8069 (loopback, o Odoo)"
echo "  rede............ host (o proxy precisa da interface publica e do loopback do Odoo)"
echo "  cert atual...... $(docker exec proxy-dev cat /data/caddy/pki/authorities/local/root.crt 2>/dev/null | openssl x509 -noout -subject 2>/dev/null || echo 'CA local (emitida sob demanda)')"
echo "  ufw............. $(ufw status | awk 'NR>1 && NF' | tr '\n' ' ')"
echo
echo "RESULTADO: PROXY_DEV_INSTALADO hostname=$HOSTNAME_PROXY portas=$P_HTTP,$P_HTTPS https=200 odoo=127.0.0.1:8069"
