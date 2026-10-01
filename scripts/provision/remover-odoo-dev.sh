#!/usr/bin/env bash
# ROLLBACK do Odoo Community no ambiente DEV do TRE (VPS Contabo `vmi3619453`).
#
# Card: TRE-W2-E01-T01 (`t_d6dc5a4c`). Runbook: docs/runbooks/odoo-dev.md §5.
#
# Derruba os containers do Odoo no dev, remove os volumes do Odoo (`pgdata-odoo-dev`,
# `odoo-data-dev`), a rede `tre-odoo-dev` e os segredos do ambiente (/etc/tre/odoo-dev).
# NAO toca: `pg-sales-dev` (Sales Intelligence), homologacao, producao, o par nao-secreto, o
# compose versionado nem a UFW.
#
# Uso (na VPS):
#   TRE_ODOO_CONFIRMAR_REMOCAO=1 bash remover-odoo-dev.sh
set -euo pipefail

COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/dev/compose/odoo.yml}"
ENVFILE="${TRE_ODOO_ENV:-/opt/tre/dev/compose/odoo.env}"
SEGREDOS="${TRE_ODOO_SEGREDOS:-/etc/tre/odoo-dev}"
VOLUMES_ODOO="pgdata-odoo-dev odoo-data-dev"

falhar() { echo "FALHOU $*" >&2; exit 1; }

# --- guardas: so o dev, so o Odoo, e so com confirmacao explicita ----------------------
[ "${TRE_ODOO_CONFIRMAR_REMOCAO:-0}" = "1" ] \
  || falhar "remocao exige confirmacao explicita: TRE_ODOO_CONFIRMAR_REMOCAO=1 (nada foi tocado)"
case "$COMPOSE" in
  /opt/tre/dev/*) : ;;
  *) falhar "compose fora do ambiente dev ($COMPOSE) — nao removo nada" ;;
esac
[ "$SEGREDOS" = "/etc/tre/odoo-dev" ] || falhar "caminho de segredos inesperado ($SEGREDOS) — nao removo nada"
case " $VOLUMES_ODOO " in
  *" pgdata-sales-dev "*|*" sales_intelligence "*) falhar "lista de volumes contem volume da Sales Intelligence — nao removo nada" ;;
esac
for nome in odoo-homolog odoo-prod pg-odoo-homolog pg-odoo-prod; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$nome"; then
    falhar "container de outro ambiente existe ($nome) — nao removo nada"
  fi
done

echo "ANTES"
docker ps -a --format '  container: {{.Names}} | {{.Status}}'
for v in $VOLUMES_ODOO; do
  docker volume inspect "$v" >/dev/null 2>&1 && echo "  volume:    $v" || echo "  volume:    $v (ausente)"
done
[ -d "$SEGREDOS" ] && echo "  segredos:  $(ls -1 "$SEGREDOS" | tr '\n' ' ')" || echo "  segredos:  ausentes"

echo "--- derrubando containers e rede"
docker compose --env-file "$ENVFILE" -f "$COMPOSE" down

echo "--- removendo volumes do Odoo (os dados do Odoo vao junto; e o rollback declarado)"
for v in $VOLUMES_ODOO; do
  if docker volume inspect "$v" >/dev/null 2>&1; then
    docker volume rm "$v" >/dev/null
    echo "  removido: $v"
  else
    echo "  ja ausente: $v"
  fi
done

echo "--- removendo segredos do ambiente"
if [ -d "$SEGREDOS" ]; then
  rm -rf "$SEGREDOS"
  echo "  removido: $SEGREDOS"
else
  echo "  ja ausente: $SEGREDOS"
fi

echo "DEPOIS"
docker ps -a --format '  container: {{.Names}} | {{.Status}}'
for v in $VOLUMES_ODOO; do
  docker volume inspect "$v" >/dev/null 2>&1 && echo "  volume:    $v (AINDA EXISTE)" || echo "  volume:    $v (ausente)"
done
echo "  integro:   pg-sales-dev $(docker inspect pg-sales-dev --format '{{.State.Status}}' 2>/dev/null || echo ausente)"
echo
echo "RESULTADO: ODOO_DEV_REMOVIDO (containers, volumes do Odoo e segredos do ambiente; pg-sales-dev intocado)"
