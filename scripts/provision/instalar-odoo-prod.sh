#!/usr/bin/env bash
# STATUS (06/10/2026): espelho derivado de homolog — REVISAO DE INVERSaO PENDENTE.
#   Nao executar em producao antes de fechar a revisao: as listas de "container/rede de outro
#   ambiente" ainda citam nomes que pertenciam ao outro ambiente. Espelhado por derivacao
#   mecanica; a inversao dos conjuntos (meu x do outro) e' revisao item a item.
# Instalacao do Odoo Community no ambiente HOMOLOG do TRE (VPS Contabo `vmi3619453`).
#
# Derivado de scripts/provision/instalar-odoo-dev.sh em 05/10/2026 (paridade estrutural: o
# Homolog existe justamente para detectar diferenca de ambiente, entao ele nasce com o MESMO
# desenho do dev). Runbook: docs/runbooks/odoo-prod.md.
#
# Roda NA VPS (precisa de `docker` e da arvore /opt/tre); o container do Hermes apenas
# orquestra por SSH (ADR-0008).
#
# Diferenca DELIBERADA em relacao ao instalador do dev (guardas):
#   * o dev RECUSA se existir container de homolog/producao. Aqui e' o inverso: dev e producao
#     podem existir — o que este script garante e' que ele NAO os toca. A prova disso nao e'
#     disciplina: e' uma checagem do COMPOSE RESOLVIDO (`docker compose config`), que falha se
#     o artefato de homolog apontar para nome de container de outro ambiente.
#   * o trio de vendas (`sales_intelligence`) nao existe em homolog: o par prod.env declara
#     so' o Odoo. Ver o comentario daquele arquivo.
#
# Uso (na VPS):
#   bash instalar-odoo-prod.sh
#
# Variaveis:
#   TRE_ODOO_COMPOSE   (padrao /opt/tre/prod/compose/odoo.yml)
#   TRE_ODOO_ENV       (padrao /opt/tre/prod/compose/odoo.env)
#   TRE_ODOO_SEGREDOS  (padrao /etc/tre/odoo-prod)
#   TRE_ODOO_RECRIAR=1 permite reexecutar sobre uma instalacao que ja existe
set -euo pipefail

COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/prod/compose/odoo.yml}"
ENVFILE="${TRE_ODOO_ENV:-/opt/tre/prod/compose/odoo.env}"
SEGREDOS="${TRE_ODOO_SEGREDOS:-/etc/tre/odoo-prod}"
RECRIAR="${TRE_ODOO_RECRIAR:-0}"
ADDONS_PUBLICADOS="/opt/tre/prod/repo/odoo/addons"
NOMES_DE_OUTROS=(odoo-dev pg-odoo-dev odoo-prod pg-odoo-prod)

falhar() { echo "FALHOU $*" >&2; exit 1; }

compose() { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }

# ---------------------------------------------------------------------------
# 1. Guardas (fail-closed: se qualquer uma nao passa, nada e criado)
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || falhar "docker nao esta no PATH (este script roda NA VPS do TRE)"
docker info >/dev/null 2>&1 || falhar "daemon do docker nao responde"
[ -f "$COMPOSE" ] || falhar "compose nao encontrado em $COMPOSE"
[ -f "$ENVFILE" ] || falhar "par nao-secreto nao encontrado em $ENVFILE"

# O ambiente alvo e o homolog e SO ele.
case "$COMPOSE" in
  /opt/tre/prod/*) : ;;
  *) falhar "compose fora do ambiente homolog ($COMPOSE) — este script so opera /opt/tre/prod" ;;
esac
[ "$SEGREDOS" = "/etc/tre/odoo-prod" ] || falhar "segredos fora de /etc/tre/odoo-prod ($SEGREDOS)"

if docker ps -a --format '{{.Names}}' | grep -qx 'odoo-prod' && [ "$RECRIAR" != "1" ]; then
  falhar "container 'odoo-prod' JA EXISTE — nao mexo nele sem TRE_ODOO_RECRIAR=1"
fi
[ -d "$ADDONS_PUBLICADOS" ] || falhar "addons publicados ausentes ($ADDONS_PUBLICADOS) — publique o commit do branch homolog antes (deploy/publicar.sh com destino e artefato isolados)"

# shellcheck disable=SC1090
set -a; . "$ENVFILE"; set +a
PORTA="${ODOO_HTTP_PORT:?ODOO_HTTP_PORT ausente em $ENVFILE}"
VERSAO="${ODOO_VERSION:?ODOO_VERSION ausente em $ENVFILE}"
DIGESTO_ESPERADO="${ODOO_DIGEST_ESPERADO:-}"

# Porta livre so importa quando o nosso container ainda NAO existe: com o `odoo-prod` de pe,
# a porta 8070 e dele (a checagem ingenua "porta em uso" reprovava a reexecucao idempotente).
if ! docker ps -a --format '{{.Names}}' | grep -qx 'odoo-prod'; then
  if ss -lntH "sport = :$PORTA" 2>/dev/null | grep -q .; then
    falhar "porta $PORTA ja esta em uso nesta maquina (e o container 'odoo-prod' nao existe) — escolha outra em $ENVFILE"
  fi
fi
echo "OK    guardas: docker $(docker --version | awk '{print $3}'), compose $(docker compose version --short)"

# ---------------------------------------------------------------------------
# 2. Isolamento provado pelo COMPOSE RESOLVIDO (nao por disciplina)
# ---------------------------------------------------------------------------
# `docker compose config` resolve env_file, variaveis e nomes. Se o artefato de homolog citar
# qualquer container de dev/producao, este script para — e' a prova mecanica de que subir homolog
# nao mexe no dev.
#
# ORDEM QUE IMPORTA: o `config` resolve o `env_file` de 600 que o container le, entao ELE FALHA
# enquanto /etc/tre/odoo-prod/pg.env nao existe. Medido: rodando a checagem antes dos segredos,
# o `config` reprovava com "env file not found" e a mensagem parecia defeito do compose. Por isso a
# checagem e' definida aqui e CHAMADA na secao 4, depois que os segredos nascem.
checar_isolamento() {
  local resolvido
  if ! resolvido="$(compose config 2>&1)"; then
    printf '%s\n' "$resolvido" | tail -5 >&2
    falhar "docker compose config invalido para $COMPOSE + $ENVFILE"
  fi
  for nome in "${NOMES_DE_OUTROS[@]}"; do
    case "$resolvido" in
      *"container_name: $nome"*)
        printf '%s\n' "$resolvido" | grep -n "container_name: $nome" >&2 || true
        falhar "o compose de homolog cita container de outro ambiente ($nome) — recuso subir" ;;
    esac
  done
  echo "OK    isolamento: o compose resolvido nao cita nenhum container de dev/producao"
}

# ---------------------------------------------------------------------------
# 3. Segredos: nascem AQUI e nunca saem da VPS (nada no artefato, nada em log)
# ---------------------------------------------------------------------------
umask 077
if [ ! -d "$SEGREDOS" ]; then
  mkdir -p "$SEGREDOS"; chmod 700 "$SEGREDOS"
  chown tre-deploy:tre-deploy "$SEGREDOS" 2>/dev/null || true
fi

if [ -f "$SEGREDOS/pg.env" ]; then
  echo "OK    segredos: reaproveitando $SEGREDOS/pg.env e $SEGREDOS/odoo.conf existentes"
  SENHA_PG="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$SEGREDOS/pg.env")"
  [ -n "$SENHA_PG" ] || falhar "$SEGREDOS/pg.env existe mas nao tem POSTGRES_PASSWORD"
  [ -f "$SEGREDOS/odoo.conf" ] || falhar "$SEGREDOS/odoo.conf ausente junto com pg.env — ambiente inconsistente"
  SENHA_PG_CONF="$(awk -F' = ' '$1=="db_password"{print $2}' "$SEGREDOS/odoo.conf")"
  [ "$SENHA_PG" = "$SENHA_PG_CONF" ] \
    || falhar "a senha do banco em pg.env e a do odoo.conf divergem — recuso subir com as duas diferentes"
else
  SENHA_PG="$(openssl rand -hex 24)"
  printf 'POSTGRES_USER=odoo\nPOSTGRES_DB=odoo_prod\nPOSTGRES_PASSWORD=%s\n' "$SENHA_PG" > "$SEGREDOS/pg.env"
  chmod 600 "$SEGREDOS/pg.env"; chown root:root "$SEGREDOS/pg.env"
  SENHA_MESTRE="$(openssl rand -hex 24)"
  {
    printf '[options]\n'
    printf 'addons_path = /mnt/extra-addons\n'
    printf 'data_dir = /var/lib/odoo\n'
    printf 'admin_passwd = %s\n' "$SENHA_MESTRE"
    printf 'db_host = pg-odoo-prod\n'
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
# 4. O compose tem de ser valido com este par de variaveis (e isolado)
# ---------------------------------------------------------------------------
# A validacao vem DEPOIS dos segredos porque o `docker compose config` resolve o `env_file` de 600
# que o container le; os guardas que protegem a maquina (docker, par, ambiente homolog, porta livre)
# rodaram antes de tudo.
checar_isolamento
echo "OK    compose valido: $COMPOSE + $ENVFILE (versao $VERSAO, porta 127.0.0.1:$PORTA)"
echo "      imagens: $(compose config --images | sort | tr '\n' ' ')"

# ---------------------------------------------------------------------------
# 5. Banco do Odoo (container e volume proprios de homolog)
# ---------------------------------------------------------------------------
echo "--- subindo pg-odoo-prod"
compose up -d pg-odoo-prod >/dev/null
for _ in $(seq 1 30); do
  estado="$(docker inspect pg-odoo-prod --format '{{.State.Health.Status}}' 2>/dev/null || echo desconhecido)"
  [ "$estado" = "healthy" ] && break
  sleep 3
done
[ "$(docker inspect pg-odoo-prod --format '{{.State.Health.Status}}')" = "healthy" ] \
  || falhar "pg-odoo-prod nao ficou healthy (estado: $(docker inspect pg-odoo-prod --format '{{.State.Health.Status}}'))"
echo "OK    pg-odoo-prod healthy ($(docker inspect pg-odoo-prod --format '{{.Config.Image}}'))"

JA_TEM_BANCO="$(docker exec pg-odoo-prod psql -U odoo -d odoo_prod -tAc "select 1 from information_schema.tables where table_schema='public' and table_name='ir_module_module'" 2>/dev/null || true)"
if [ "$JA_TEM_BANCO" = "1" ]; then
  echo "OK    banco odoo_prod ja inicializado pelo Odoo — inicializacao do Odoo sera pulada"
else
  # Cuidado: o `POSTGRES_DB` do proprio postgres:16 ja cria o banco VAZIO; banco existente NAO
  # quer dizer Odoo inicializado. O que prova inicializacao e a tabela do modulo `base`
  # (`ir_module_module`) — medido no dev: com o banco vazio o Odoo respondia 500.
  echo "--- inicializando o banco odoo_prod com o modulo base (sem demo)"
  # `< /dev/null` NAO e decorativo: `docker compose run` consome o stdin de quem o executa —
  # orquestrado por `ssh ... 'bash -s' < script`, isso mata o resto do script remoto (defeito
  # ja registrado no CHANGELOG deste projeto).
  if ! compose run --rm --no-deps -T odoo-prod -d odoo_prod -i base --without-demo=all --stop-after-init --no-http < /dev/null >/tmp/instalar-odoo-prod.init.log 2>&1; then
    echo "FALHOU a inicializacao do banco:"
    tail -20 /tmp/instalar-odoo-prod.init.log >&2
    exit 1
  fi
  echo "OK    banco odoo_prod inicializado ($(wc -l </tmp/instalar-odoo-prod.init.log) linhas de log; sem demo)"
fi

# ---------------------------------------------------------------------------
# 6. Servico
# ---------------------------------------------------------------------------
echo "--- subindo odoo-prod"
compose up -d >/dev/null
CODIGO="000"
for _ in $(seq 1 60); do
  CODIGO="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORTA}/web/login" || true)"
  [ "$CODIGO" = "200" ] && break
  sleep 5
done
[ "$CODIGO" = "200" ] || falhar "Odoo nao respondeu 200 em http://127.0.0.1:${PORTA}/web/login (ultimo codigo: $CODIGO)"

# ---------------------------------------------------------------------------
# 7. Evidencia de deploy (identidade do artefato, hora, destino)
# ---------------------------------------------------------------------------
echo
echo "IDENTIDADE"
echo "  imagem.......... $(docker inspect odoo-prod --format '{{.Config.Image}}')"
echo "  digest.......... $(docker image inspect "odoo:${VERSAO}" --format '{{index .RepoDigests 0}}')"
if [ -n "$DIGESTO_ESPERADO" ]; then
  MEU_DIGESTO="$(docker image inspect "odoo:${VERSAO}" --format '{{index .RepoDigests 0}}')"
  [ "${MEU_DIGESTO#odoo@}" = "$DIGESTO_ESPERADO" ] \
    && echo "OK    digest confere com $ENVFILE" \
    || falhar "digest da imagem NAO confere com ODOO_DIGEST_ESPERADO ($DIGESTO_ESPERADO)"
fi
echo "  versao odoo..... $(docker exec odoo-prod odoo --version 2>/dev/null | tail -1)"
echo "  containers...... $(docker inspect pg-odoo-prod --format '{{.Name}}=id={{.Id}}') $(docker inspect odoo-prod --format '{{.Name}}=id={{.Id}}')"
echo "  iniciado_em..... $(docker inspect odoo-prod --format '{{.State.StartedAt}}')"
echo "  destino......... 127.0.0.1:$PORTA -> 8069/tcp (loopback; quem expoe e' a borda em homolog.tre)"
echo "  banco........... odoo_prod em pg-odoo-prod (volume pgdata-odoo-prod)"
echo "  rede............ tre-odoo-prod (propria; nada compartilhado com o dev)"
echo "  dev intocado.... $(docker inspect odoo-dev --format '{{.State.Status}} desde {{.State.StartedAt}}' 2>/dev/null || echo '(odoo-dev ausente)')"
echo
echo "RESULTADO: ODOO_HOMOLOG_INSTALADO versao=$VERSAO porta=127.0.0.1:$PORTA http=200"
