#!/usr/bin/env bash
# CRM basico no ambiente PROD do TRE: instala o modulo `crm` do Odoo e aplica a declaracao do
# funil comercial (pipeline + etapas) no banco `odoo_prod`.
#
# Espelho de scripts/provision/configurar-crm-dev.sh para o ambiente de PRODUCAO (alvo deste script).
# Runbook: docs/runbooks/odoo-prod.md (secao do funil comercial).
# Declaracao (fonte unica das etapas, nada de etapa no codigo):
#   odoo/crm/funil-transformativa.yaml  ->  na VPS: /opt/tre/prod/repo/odoo/crm/funil-transformativa.yaml
#
# Roda NA VPS (precisa de `docker` e da arvore /opt/tre/prod); o container do Hermes apenas
# orquestra por SSH (ADR-0008). Nao toca o dev nem o homolog: por construcao so opera
# /opt/tre/prod e o isolamento e' o caminho do compose (o dev original recusava se achasse outro ambiente).
#
# Uso (na VPS):
#   bash configurar-crm-prod.sh
#
# Variaveis (opcionais):
#   TRE_ODOO_COMPOSE  (padrao /opt/tre/prod/compose/odoo.yml)
#   TRE_ODOO_ENV      (padrao /opt/tre/prod/compose/odoo.env)
#   TRE_CRM_YAML      (padrao /opt/tre/prod/repo/odoo/crm/funil-transformativa.yaml)
#   TRE_CRM_ORM       (padrao /opt/tre/prod/repo/scripts/provision/aplicar_funil_crm.py)
#   TRE_ODOO_BANCO    (padrao odoo_prod)
set -euo pipefail

COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/prod/compose/odoo.yml}"
ENVFILE="${TRE_ODOO_ENV:-/opt/tre/prod/compose/odoo.env}"
YAML="${TRE_CRM_YAML:-/opt/tre/prod/repo/odoo/crm/funil-transformativa.yaml}"
ORM="${TRE_CRM_ORM:-/opt/tre/prod/repo/scripts/provision/aplicar_funil_crm.py}"
BANCO="${TRE_ODOO_BANCO:-odoo_prod}"
EVID="/opt/tre/prod/evidencias/funil-crm-20261006"

falhar() { echo "FALHOU $*" >&2; exit 1; }
compose() { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }
psql_odoo() { docker exec -i pg-odoo-prod psql -U odoo -d "$BANCO" -tA -f - ; }

SERVICO_PARADO=0
restaurar_servico() {
  if [ "$SERVICO_PARADO" = "1" ]; then
    compose up -d odoo-prod >/dev/null 2>&1 || true
    SERVICO_PARADO=0
    echo "(servico odoo-prod religado pelo trap de saida)"
  fi
}
trap restaurar_servico EXIT

# ---------------------------------------------------------------------------
# 1. Guardas (fail-closed: se qualquer uma nao passa, nada e' tocado)
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || falhar "docker nao esta no PATH (este script roda NA VPS do TRE)"
docker info >/dev/null 2>&1 || falhar "daemon do docker nao responde"
[ -f "$COMPOSE" ] || falhar "compose nao encontrado em $COMPOSE"
[ -f "$ENVFILE" ] || falhar "par nao-secreto nao encontrado em $ENVFILE"
[ -f "$YAML" ] || falhar "declaracao do funil nao encontrada em $YAML"
[ -f "$ORM" ] || falhar "script ORM nao encontrado em $ORM"

case "$COMPOSE" in
  /opt/tre/prod/*) : ;;
  *) falhar "compose fora do ambiente prod ($COMPOSE) — este script so opera /opt/tre/prod" ;;
esac
# Diferente do script do dev, aqui NAO existe a guarda "nenhum outro ambiente pode existir":
# neste VPS os tres ambientes convivem de pe, e aquela guarda travaria o procedimento por
# construcao. O isolamento e' feito pelo caminho do compose (acima) e pelos nomes de container
# deste ambiente (abaixo) — nenhum comando daqui alcanca dev ou producao.
docker ps -a --format '{{.Names}}' | grep -qx 'odoo-prod' \
  || falhar "container odoo-prod nao existe — rode scripts/provision/instalar-odoo-prod.sh antes"
docker ps -a --format '{{.Names}}' | grep -qx 'pg-odoo-prod' \
  || falhar "container pg-odoo-prod nao existe — rode scripts/provision/instalar-odoo-prod.sh antes"
docker exec -i pg-odoo-prod psql -U odoo -d "$BANCO" -tAc "select 1" >/dev/null 2>&1 \
  || falhar "banco $BANCO nao responde em pg-odoo-prod"
mkdir -p "$EVID"

compose config -q || falhar "docker compose config invalido para $COMPOSE + $ENVFILE"
echo "OK    guardas: ambiente dev, containers de pe, par valido, declaracao presente"
echo "      declaracao..... $YAML (sha256 $(sha256sum "$YAML" | awk '{print $1}'))"
echo "      orm............ $ORM (sha256 $(sha256sum "$ORM" | awk '{print $1}'))"
echo "      banco.......... $BANCO em pg-odoo-prod"

PORT="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$ENVFILE")"
[ -n "$PORT" ] || falhar "ODOO_HTTP_PORT ausente em $ENVFILE"

subir_servico() {
  compose up -d odoo-prod >/dev/null
  SERVICO_PARADO=0
  local codigo="000"
  for _ in $(seq 1 30); do
    codigo="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT}/web/login" || true)"
    [ "$codigo" = "200" ] && return 0
    sleep 3
  done
  falhar "Odoo nao respondeu 200 em http://127.0.0.1:${PORT}/web/login (ultimo codigo: $codigo)"
}

estado_modulo() {
  psql_odoo <<'SQL' | tr -d '[:space:]'
select coalesce((select state from ir_module_module where name = 'crm'), 'ausente');
SQL
}

# `odoo shell` roda como processo proprio dentro do container (o `docker exec` exige container
# de pe — defeito medido na primeira execucao deste script). O container deste ambiente e'
# compartilhado com os outros cards do dev: MEDIDO em 01/10/2026 (card TRE-W2-E01-T02, TLS/proxy)
# que um reinicio de container no meio do `exec` mata o processo (exit 137) e o script morria.
# Por isso: cada tentativa sobe o servico, confere HTTP 200 e so' entao executa; o ORM e'
# idempotente, entao repetir e' seguro.
exec_odoo_shell() { # <python> <log> <marca do RESULTADO> [<arquivo a copiar para /tmp>]
  local python="$1" log="$2" marca="$3" copiar="${4:-}" tentativa codigo=0
  for tentativa in 1 2 3; do
    subir_servico
    if [ -n "$copiar" ]; then
      docker cp "$copiar" "odoo-prod:/tmp/$(basename "$copiar")" || true
    fi
    codigo=0
    docker exec -i odoo-prod odoo shell -d "$BANCO" --no-http < "$python" > "$log" 2>&1 || codigo=$?
    if grep -q "^RESULTADO: ${marca}" "$log"; then
      sed -n '1,200p' "$log"
      return 0
    fi
    echo "(tentativa $tentativa: odoo shell exit=$codigo sem 'RESULTADO: $marca'; container: \
$(docker ps --format '{{.Names}}' | grep -qx odoo-prod && echo de-pe || echo parado))" >&2
    sleep 5
  done
  sed -n '1,200p' "$log" >&2
  return 1
}

# ---------------------------------------------------------------------------
# 2. Modulo `crm` (o CRM basico do Odoo e' este modulo: pipeline, etapas, crm.lead)
# ---------------------------------------------------------------------------
ANTES="$(estado_modulo)"
echo "--- estado do modulo crm ANTES: $ANTES"
if [ "$ANTES" != "installed" ]; then
  echo "--- instalando o modulo crm (sem dados de demonstracao)"
  compose stop odoo-prod >/dev/null; SERVICO_PARADO=1
  # `< /dev/null` NAO e' decorativo: `docker compose run` consome o stdin de quem o executa
  # (defeito ja registrado no CHANGELOG deste projeto quando o script chega por `ssh 'bash -s'`).
  if ! compose run --rm --no-deps -T odoo-prod -d "$BANCO" -i crm --without-demo=all \
        --stop-after-init --no-http < /dev/null > "$EVID/instalar-crm.log" 2>&1; then
    echo "FALHOU a instalacao do modulo crm:" >&2
    tail -20 "$EVID/instalar-crm.log" >&2
    exit 1
  fi
  DEPOIS_MOD="$(estado_modulo)"
  [ "$DEPOIS_MOD" = "installed" ] \
    || falhar "instalacao terminou sem erro mas o modulo crm continua '$DEPOIS_MOD'"
  echo "OK    modulo crm instalado ($(wc -l < "$EVID/instalar-crm.log") linhas de log em $EVID/instalar-crm.log)"
else
  echo "OK    modulo crm ja estava instalado — instalacao pulada (idempotente)"
fi
subir_servico
echo "OK    servico odoo-prod de pe (HTTP 200 em 127.0.0.1:$PORT/web/login)"

# ---------------------------------------------------------------------------
# 3. Aplicacao da declaracao do funil (ORM, por `odoo shell`)
# ---------------------------------------------------------------------------
# `odoo shell` roda como um processo proprio DENTRO do container em execucao (o `docker exec`
# exige container de pe — defeito medido na primeira execucao deste script, que parava o
# servico antes de chamar o exec). Escrever etapa pelo ORM com o servidor no ar e' uso normal.
echo "--- aplicando a declaracao do funil pelo ORM"

# A declaracao entra no container por `docker cp`: o container NAO monta /opt/tre/prod (monta
# /opt/tre/repo/odoo/addons em /mnt/extra-addons) e escrever na copia operacional e' proibido
# (defeitos F2/F3 do card t_daca4bda). O `docker cp` e' refeito a cada tentativa, porque um
# container recriado por outro card perde o /tmp.
if ! exec_odoo_shell "$ORM" "$EVID/aplicar-funil.log" CRM_CONFIGURADO "$YAML"; then
  echo "FALHOU o ORM nao chegou ao RESULTADO esperado (log em $EVID/aplicar-funil.log)" >&2
  exit 1
fi
echo "OK    declaracao aplicada (log em $EVID/aplicar-funil.log)"

# ---------------------------------------------------------------------------
# 4. Identidade do estado final
# ---------------------------------------------------------------------------
echo
echo "ESTADO DAS ETAPAS (crm.stage):"
# `crm_stage.name` e `crm_team.name` sao traduziveis: no banco sao JSONB (medido). Sem o
# `->>` o psql reprova com "function rpad(jsonb, integer) does not exist".
psql_odoo <<'SQL'
select lpad(coalesce(s.sequence::text, '?'), 4) || '  ' ||
       rpad(coalesce((select string_agg(v, ' / ' order by k) from jsonb_each_text(s.name) as x(k, v)),
                     '<SEM NOME>'), 24) ||
       ' won=' || coalesce(s.is_won::text, 'false') ||
       '  time=' || coalesce((select string_agg(coalesce(t.name->>'pt_BR', t.name->>'en_US'),
                                                ',' order by t.id)
                              from crm_team t
                              join crm_stage_crm_team_rel r on r.crm_team_id = t.id
                              where r.crm_stage_id = s.id), '-')
from crm_stage s order by s.sequence, coalesce(s.name->>'en_US', s.name::text);
SQL

echo
echo "RESULTADO: CRM_PROD_CONFIGURADO banco=$BANCO modulo=crm etapas_declaradas=$(
  grep -c '^    - {nome:' "$YAML") evidencia=$EVID"
