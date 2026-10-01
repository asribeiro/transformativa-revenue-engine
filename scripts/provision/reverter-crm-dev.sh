#!/usr/bin/env bash
# ROLLBACK do CRM basico no ambiente DEV do TRE.
#
# Card: TRE-W2-E02-T01 (`t_adea8e6b`). Runbook: docs/runbooks/odoo-crm-dev.md §7.
#
# Dois niveis, escolhidos por variavel:
#
#   (padrao)                        Volta as ETAPAS ao padrao anterior: remove as etapas do funil
#                                   da Transformativa e o ramo lateral Nurture e repoe as etapas
#                                   do proprio modulo `crm` (New, Qualified, Proposition, Won)
#                                   com os valores literais de crm_stage_data.xml. O modulo
#                                   continua instalado.
#   TRE_CRM_DESINSTALAR=1           Rollback TOTAL: desinstala o modulo `crm` — volta ao estado
#                                   medido ANTES deste card (crm uninstalled, sem tabelas crm_*).
#
# Roda NA VPS. Nao toca homologacao nem producao.
# Uso (na VPS):  bash reverter-crm-dev.sh   [TRE_CRM_DESINSTALAR=1 bash reverter-crm-dev.sh]
set -euo pipefail

COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/dev/compose/odoo.yml}"
ENVFILE="${TRE_ODOO_ENV:-/opt/tre/dev/compose/odoo.env}"
YAML="${TRE_CRM_YAML:-/opt/tre/dev/odoo/crm/funil-transformativa.yaml}"
ORM="${TRE_CRM_UNDO:-/opt/tre/dev/scripts/desfazer_funil_crm.py}"
REPOR="${TRE_CRM_REPOR:-/opt/tre/dev/scripts/repor_etapas_padrao_crm.py}"
BANCO="${TRE_ODOO_BANCO:-odoo_dev}"
DESINSTALAR="${TRE_CRM_DESINSTALAR:-0}"
EVID="/opt/tre/dev/evidencias/t_adea8e6b"

falhar() { echo "FALHOU $*" >&2; exit 1; }
compose() { docker compose --env-file "$ENVFILE" -f "$COMPOSE" "$@"; }
psql_odoo() { docker exec -i pg-odoo-dev psql -U odoo -d "$BANCO" -tA -f - ; }

SERVICO_PARADO=0
restaurar_servico() {
  if [ "$SERVICO_PARADO" = "1" ]; then
    compose up -d odoo-dev >/dev/null 2>&1 || true
    SERVICO_PARADO=0
    echo "(servico odoo-dev religado pelo trap de saida)"
  fi
}
trap restaurar_servico EXIT

command -v docker >/dev/null 2>&1 || falhar "docker nao esta no PATH (este script roda NA VPS do TRE)"
docker info >/dev/null 2>&1 || falhar "daemon do docker nao responde"
[ -f "$COMPOSE" ] || falhar "compose nao encontrado em $COMPOSE"
[ -f "$ENVFILE" ] || falhar "par nao-secreto nao encontrado em $ENVFILE"
[ -f "$YAML" ] || falhar "declaracao do funil nao encontrada em $YAML"
[ -f "$ORM" ] || falhar "script de desfazer nao encontrado em $ORM"
[ -f "$REPOR" ] || falhar "script de reposicao do padrao nao encontrado em $REPOR"
case "$COMPOSE" in
  /opt/tre/dev/*) : ;;
  *) falhar "compose fora do ambiente dev ($COMPOSE) — este script so opera /opt/tre/dev" ;;
esac
for nome in odoo-homolog odoo-prod pg-odoo-homolog pg-odoo-prod; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$nome"; then
    falhar "container de outro ambiente existe ($nome) — este script so opera o dev"
  fi
done
docker ps -a --format '{{.Names}}' | grep -qx 'odoo-dev' || falhar "container odoo-dev nao existe"
mkdir -p "$EVID"

modulo_em() { psql_odoo <<'SQL' | tr -d '[:space:]'
select coalesce((select state from ir_module_module where name = 'crm'), 'ausente');
SQL
}

PORT="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$ENVFILE")"
[ -n "$PORT" ] || falhar "ODOO_HTTP_PORT ausente em $ENVFILE"

subir_servico() {
  compose up -d odoo-dev >/dev/null
  SERVICO_PARADO=0
  local codigo="000"
  for _ in $(seq 1 30); do
    codigo="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT}/web/login" || true)"
    [ "$codigo" = "200" ] && return 0
    sleep 3
  done
  falhar "Odoo nao respondeu 200 em http://127.0.0.1:${PORT}/web/login (ultimo codigo: $codigo)"
}

# O container deste ambiente e' COMPARTILHADO com os outros cards do dev: MEDIDO em 01/10/2026
# (card TRE-W2-E01-T02, TLS/proxy) que um reinicio do container odoo-dev no meio do `docker exec`
# mata o processo em execucao (exit 137) e o script morria no meio do rollback. Por isso cada
# passo de ORM: sobe o servico, confere HTTP 200 e so' entao executa — com ate' 3 tentativas, o
# que e' seguro porque os tres scripts de ORM sao idempotentes.
exec_odoo_shell() { # <python> <log> <marca do RESULTADO> [<arquivo a copiar para /tmp>]
  local python="$1" log="$2" marca="$3" copiar="${4:-}" tentativa codigo=0
  for tentativa in 1 2 3; do
    subir_servico
    if [ -n "$copiar" ]; then
      docker cp "$copiar" "odoo-dev:/tmp/$(basename "$copiar")" || true
    fi
    codigo=0
    docker exec -i odoo-dev odoo shell -d "$BANCO" --no-http < "$python" > "$log" 2>&1 || codigo=$?
    if grep -q "^RESULTADO: ${marca}" "$log"; then
      sed -n '1,120p' "$log"
      return 0
    fi
    echo "(tentativa $tentativa: odoo shell exit=$codigo sem 'RESULTADO: $marca'; container: \
$(docker ps --format '{{.Names}}' | grep -qx odoo-dev && echo de-pe || echo parado))" >&2
    sleep 5
  done
  sed -n '1,120p' "$log" >&2
  return 1
}

ESTADO="$(modulo_em)"
echo "--- estado do modulo crm ANTES do rollback: $ESTADO"
[ "$ESTADO" = "installed" ] || falhar "modulo crm nao esta instalado ($ESTADO) — nada a reverter"

echo "--- removendo as etapas do funil da Transformativa e o ramo lateral (ORM)"
exec_odoo_shell "$ORM" "$EVID/reverter-funil.log" CRM_DESFEITO "$YAML" \
  || falhar "o desfazer nao chegou ao RESULTADO esperado (log em $EVID/reverter-funil.log)"

if [ "$DESINSTALAR" = "1" ]; then
  echo "--- rollback TOTAL: desinstalando o modulo crm"
  cat > /tmp/desinstalar_crm.py <<'PY'
modulo = env["ir.module.module"].search([("name", "=", "crm")])
if modulo.state == "uninstalled":
    print("RESULTADO: CRM_DESINSTALADO estado=uninstalled (ja' estava desinstalado)")
    raise SystemExit(0)
if modulo.state not in ("installed", "to upgrade", "to remove"):
    print("FALHOU modulo crm em estado inesperado: %s" % modulo.state)
    raise RuntimeError("estado inesperado: %s" % modulo.state)
modulo.button_immediate_uninstall()
env.cr.commit()
print("RESULTADO: CRM_DESINSTALADO estado=%s"
      % env["ir.module.module"].search([("name", "=", "crm")]).state)
PY
  # O `odoo shell` le' o script pelo stdin: nao precisa de `docker cp` (que perdia o arquivo
  # quando outro card recriava o container no meio do caminho).
  exec_odoo_shell /tmp/desinstalar_crm.py "$EVID/reverter-desinstalar.log" CRM_DESINSTALADO \
    || falhar "a desinstalacao nao chegou ao RESULTADO esperado (log em $EVID/reverter-desinstalar.log)"
  rm -f /tmp/desinstalar_crm.py
else
  # MEDIDO nesta instancia (01/10/2026): `-u crm` NAO repoe os dados de crm_stage_data.xml
  # depois de apagados (o arquivo e' carregado no log e o pipeline continua VAZIO) — por isso a
  # reposicao e' explicita, com os valores literais do modulo, e conferida pelo proprio script.
  echo "--- repondo as etapas PADRAO do modulo crm (com o que o modulo NAO repoe sozinho)"
  exec_odoo_shell "$REPOR" "$EVID/reverter-repor-padrao.log" CRM_PADRAO_REPOSTO \
    || falhar "a reposicao do padrao nao chegou ao RESULTADO esperado (log em $EVID/reverter-repor-padrao.log)"
fi

subir_servico
echo "OK    servico odoo-dev de pe (HTTP 200 em 127.0.0.1:$PORT/web/login)"

echo
if [ "$(modulo_em)" = "installed" ]; then
  echo "ESTADO DAS ETAPAS DEPOIS DO ROLLBACK:"
  # `crm_stage.is_won` fica NULO em etapa criada fora do modulo: sem o coalesce a linha inteira
  # do `||` sai VAZIA (medido) e a medicao mente por omissao.
  psql_odoo <<'SQL'
select coalesce((select string_agg(v, ' / ' order by k) from jsonb_each_text(s.name) as x(k, v)),
               '<SEM NOME>') || ' seq=' || coalesce(s.sequence::text, '?') ||
       ' won=' || coalesce(s.is_won::text, 'false')
from crm_stage s order by s.sequence, coalesce(s.name->>'en_US', s.name::text);
SQL
else
  echo "(modulo crm ausente: as tabelas crm_* foram removidas pela desinstalacao)"
fi

echo
if [ "$DESINSTALAR" = "1" ]; then
  echo "RESULTADO: CRM_DEV_REVERTIDO_TOTAL modulo=crm estado=$(modulo_em) banco=$BANCO"
else
  echo "RESULTADO: CRM_DEV_REVERTIDO_PADRAO modulo=crm estado=$(modulo_em) banco=$BANCO"
fi
