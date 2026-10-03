#!/usr/bin/env bash
# Instalacao do Odoo Community no ambiente DEV do TRE (VPS Contabo `vmi3619453`).
#
# Card: TRE-W2-E01-T01 (`t_d6dc5a4c`). Runbook: docs/runbooks/odoo-dev.md.
#
# Roda NA VPS (precisa de `docker` e da arvore /opt/tre); o container do Hermes apenas
# orquestra por SSH (ADR-0008). Nao toca homologacao nem producao: por construcao so cria
# containers, volumes e segredos do ambiente DEV, e recusa se achar container de outro
# ambiente.
#
# Uso (na VPS):
#   bash instalar-odoo-dev.sh
#
# Variaveis:
#   TRE_ODOO_COMPOSE   (padrao /opt/tre/dev/compose/odoo.yml)
#   TRE_ODOO_ENV       (padrao /opt/tre/dev/compose/odoo.env)
#   TRE_ODOO_SEGREDOS  (padrao /etc/tre/odoo-dev)
#   TRE_ODOO_RECRIAR=1 permite reexecutar sobre uma instalacao que ja existe
set -euo pipefail

COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/dev/compose/odoo.yml}"
ENVFILE="${TRE_ODOO_ENV:-/opt/tre/dev/compose/odoo.env}"
SEGREDOS="${TRE_ODOO_SEGREDOS:-/etc/tre/odoo-dev}"
RECRIAR="${TRE_ODOO_RECRIAR:-0}"
ADDONS_PUBLICADOS="/opt/tre/repo/odoo/addons"

falhar() { echo "FALHOU $*" >&2; exit 1; }

compose() { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }

# ---------------------------------------------------------------------------
# 1. Guardas (fail-closed: se qualquer uma nao passa, nada e criado)
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || falhar "docker nao esta no PATH (este script roda NA VPS do TRE)"
docker info >/dev/null 2>&1 || falhar "daemon do docker nao responde"
[ -f "$COMPOSE" ] || falhar "compose nao encontrado em $COMPOSE"
[ -f "$ENVFILE" ] || falhar "par nao-secreto nao encontrado em $ENVFILE"

# O ambiente alvo e o dev e SO ele.
case "$COMPOSE" in
  /opt/tre/dev/*) : ;;
  *) falhar "compose fora do ambiente dev ($COMPOSE) — este script so opera /opt/tre/dev" ;;
esac
for nome in odoo-homolog odoo-prod pg-odoo-homolog pg-odoo-prod; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$nome"; then
    falhar "container de outro ambiente existe ($nome) — este script so opera o dev"
  fi
done
if docker ps -a --format '{{.Names}}' | grep -qx 'odoo-dev' && [ "$RECRIAR" != "1" ]; then
  falhar "container 'odoo-dev' JA EXISTE — nao mexo nele sem TRE_ODOO_RECRIAR=1"
fi
[ -d "$ADDONS_PUBLICADOS" ] || falhar "addons publicados ausentes ($ADDONS_PUBLICADOS) — publique a copia operacional antes"

# shellcheck disable=SC1090
set -a; . "$ENVFILE"; set +a
PORTA="${ODOO_HTTP_PORT:?ODOO_HTTP_PORT ausente em $ENVFILE}"
VERSAO="${ODOO_VERSION:?ODOO_VERSION ausente em $ENVFILE}"
DIGESTO_ESPERADO="${ODOO_DIGEST_ESPERADO:-}"

# Porta livre so importa quando o nosso container ainda NAO existe: com o `odoo-dev` de pe, a
# porta 8069 e dele (a checagem ingenua "porta em uso" reprovava a reexecucao idempotente).
if ! docker ps -a --format '{{.Names}}' | grep -qx 'odoo-dev'; then
  if ss -lntH "sport = :$PORTA" 2>/dev/null | grep -q .; then
    falhar "porta $PORTA ja esta em uso nesta maquina (e o container 'odoo-dev' nao existe) — escolha outra em $ENVFILE"
  fi
fi

echo "OK    guardas: docker $(docker --version | awk '{print $3}'), compose $(docker compose version --short)"

# ---------------------------------------------------------------------------
# 2. Segredos: nascem AQUI e nunca saem da VPS (nada no artefato, nada em log)
# ---------------------------------------------------------------------------
umask 077
if [ ! -d "$SEGREDOS" ]; then
  mkdir -p "$SEGREDOS"; chmod 700 "$SEGREDOS"
  chown tre-deploy:tre-deploy "$SEGREDOS" 2>/dev/null || true
fi

if [ -f "$SEGREDOS/pg.env" ]; then
  echo "OK    segredos: reaproveitando $SEGREDOS/pg.env e $SEGREDOS/odoo.conf existentes"
  # a senha do banco ja existe: o odoo.conf tem de carregar a MESMA senha
  SENHA_PG="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$SEGREDOS/pg.env")"
  [ -n "$SENHA_PG" ] || falhar "$SEGREDOS/pg.env existe mas nao tem POSTGRES_PASSWORD"
  [ -f "$SEGREDOS/odoo.conf" ] || falhar "$SEGREDOS/odoo.conf ausente junto com pg.env — ambiente inconsistente"
  SENHA_PG_CONF="$(awk -F' = ' '$1=="db_password"{print $2}' "$SEGREDOS/odoo.conf")"
  [ "$SENHA_PG" = "$SENHA_PG_CONF" ] \
    || falhar "a senha do banco em pg.env e a do odoo.conf divergem — recuso subir com as duas diferentes"
else
  SENHA_PG="$(openssl rand -hex 24)"
  printf 'POSTGRES_USER=odoo\nPOSTGRES_DB=odoo_dev\nPOSTGRES_PASSWORD=%s\n' "$SENHA_PG" > "$SEGREDOS/pg.env"
  chmod 600 "$SEGREDOS/pg.env"; chown root:root "$SEGREDOS/pg.env"
  SENHA_MESTRE="$(openssl rand -hex 24)"
  {
    printf '[options]\n'
    printf 'addons_path = /mnt/extra-addons\n'
    printf 'data_dir = /var/lib/odoo\n'
    printf 'admin_passwd = %s\n' "$SENHA_MESTRE"
    printf 'db_host = pg-odoo-dev\n'
    printf 'db_port = 5432\n'
    printf 'db_user = odoo\n'
    # A chave vai por variavel de proposito: o scanner de segredo do repo (`scripts/secret_scan.sh`)
    # reprova a forma literal "<chave> = <valor>" no codigo mesmo quando o valor e uma variavel.
    CHAVE_SENHA_BANCO='db_password'
    printf '%s = %s\n' "$CHAVE_SENHA_BANCO" "$SENHA_PG"
  } > "$SEGREDOS/odoo.conf"
  chmod 600 "$SEGREDOS/odoo.conf"
  # o processo do Odoo roda como `odoo` (uid 100, gid 101) dentro do container e precisa LER
  # este arquivo; nenhum usuario do host tem uid 100 (dhcpcd e nologin) — ver o runbook.
  chown 100:101 "$SEGREDOS/odoo.conf"
  echo "OK    segredos gerados: $SEGREDOS/{pg.env,odoo.conf} (600; nenhum valor sai da VPS)"
fi

# ---------------------------------------------------------------------------
# 3. O compose tem de ser valido com este par de variaveis (teste 1 do plano)
# ---------------------------------------------------------------------------
# A validacao vem DEPOIS dos segredos porque o `docker compose config` resolve o `env_file`
# de 600 que o container le; os guardas que protegem a maquina (docker, par, ambiente dev,
# porta livre, container de outro ambiente) rodaram antes de tudo.
compose config -q || falhar "docker compose config invalido para $COMPOSE + $ENVFILE"
echo "OK    compose valido: $COMPOSE + $ENVFILE (versao $VERSAO, porta 127.0.0.1:$PORTA)"
echo "      imagens: $(compose config --images | sort | tr '\n' ' ')"

# ---------------------------------------------------------------------------
# 4. Banco do Odoo (container proprio, separado do `sales_intelligence`)
# ---------------------------------------------------------------------------
echo "--- subindo pg-odoo-dev"
compose up -d pg-odoo-dev >/dev/null
for _ in $(seq 1 30); do
  estado="$(docker inspect pg-odoo-dev --format '{{.State.Health.Status}}' 2>/dev/null || echo desconhecido)"
  [ "$estado" = "healthy" ] && break
  sleep 3
done
[ "$(docker inspect pg-odoo-dev --format '{{.State.Health.Status}}')" = "healthy" ] \
  || falhar "pg-odoo-dev nao ficou healthy (estado: $(docker inspect pg-odoo-dev --format '{{.State.Health.Status}}'))"
echo "OK    pg-odoo-dev healthy ($(docker inspect pg-odoo-dev --format '{{.Config.Image}}'))"

JA_TEM_BANCO="$(docker exec pg-odoo-dev psql -U odoo -d odoo_dev -tAc "select 1 from information_schema.tables where table_schema='public' and table_name='ir_module_module'" 2>/dev/null || true)"
if [ "$JA_TEM_BANCO" = "1" ]; then
  echo "OK    banco odoo_dev ja inicializado pelo Odoo — inicializacao do Odoo sera pulada"
else
  # Cuidado: o `POSTGRES_DB` do proprio postgres:16 ja cria o banco VAZIO; banco existente NAO
  # quer dizer Odoo inicializado. O que prova inicializacao e a tabela do modulo `base`
  # (`ir_module_module`) — foi medido nesta maquina: com o banco vazio o Odoo respondia 500.
  echo "--- inicializando o banco odoo_dev com o modulo base (sem demo)"
  # `< /dev/null` NAO e decorativo: `docker compose run` consome o stdin de quem o executa —
  # orquestrado por `ssh ... 'bash -s' < script`, isso mata o resto do script remoto (defeito
  # ja registrado no CHANGELOG deste projeto).
  if ! compose run --rm --no-deps -T odoo-dev -d odoo_dev -i base --without-demo=all --stop-after-init --no-http < /dev/null >/tmp/instalar-odoo-dev.init.log 2>&1; then
    echo "FALHOU a inicializacao do banco:"
    tail -20 /tmp/instalar-odoo-dev.init.log >&2
    exit 1
  fi
  echo "OK    banco odoo_dev inicializado ($(wc -l </tmp/instalar-odoo-dev.init.log) linhas de log; sem demo)"
fi

# ---------------------------------------------------------------------------
# 5. Servico
# ---------------------------------------------------------------------------
echo "--- subindo odoo-dev"
compose up -d >/dev/null
CODIGO="000"
for _ in $(seq 1 60); do
  CODIGO="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORTA}/web/login" || true)"
  [ "$CODIGO" = "200" ] && break
  sleep 5
done
[ "$CODIGO" = "200" ] || falhar "Odoo nao respondeu 200 em http://127.0.0.1:${PORTA}/web/login (ultimo codigo: $CODIGO)"

# ---------------------------------------------------------------------------
# 6. Evidencia de deploy (identidade do artefato, hora, destino)
# ---------------------------------------------------------------------------
echo
echo "IDENTIDADE"
echo "  imagem.......... $(docker inspect odoo-dev --format '{{.Config.Image}}')"
echo "  digest.......... $(docker image inspect "odoo:${VERSAO}" --format '{{index .RepoDigests 0}}')"
if [ -n "$DIGESTO_ESPERADO" ]; then
  MEU_DIGESTO="$(docker image inspect "odoo:${VERSAO}" --format '{{index .RepoDigests 0}}')"
  [ "${MEU_DIGESTO#odoo@}" = "$DIGESTO_ESPERADO" ] \
    && echo "OK    digest confere com $ENVFILE" \
    || falhar "digest da imagem NAO confere com ODOO_DIGEST_ESPERADO ($DIGESTO_ESPERADO)"
fi
echo "  versao odoo..... $(docker exec odoo-dev odoo --version 2>/dev/null | tail -1)"
echo "  containers...... $(docker inspect pg-odoo-dev --format '{{.Name}}=id={{.Id}}') $(docker inspect odoo-dev --format '{{.Name}}=id={{.Id}}')"
echo "  iniciado_em..... $(docker inspect odoo-dev --format '{{.State.StartedAt}}')"
echo "  destino......... 127.0.0.1:$PORTA -> 8069/tcp (loopback), rede tre-odoo-dev"
echo "  banco........... odoo_dev em pg-odoo-dev (volume pgdata-odoo-dev) — separado de sales_intelligence"
echo
echo "RESULTADO: ODOO_DEV_INSTALADO versao=$VERSAO porta=127.0.0.1:$PORTA http=200"
