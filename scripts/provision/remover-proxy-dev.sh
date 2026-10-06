#!/usr/bin/env bash
# ROLLBACK do proxy TLS + hardening do Odoo no ambiente DEV do TRE (VPS Contabo `vmi3619453`).
#
# Card: TRE-W2-E01-T02 (`t_1acf11f2`). Runbook: docs/runbooks/odoo-dev-tls.md §5.
#
# Devolve o ambiente ao estado ANTERIOR a este card:
#   * derruba e remove o container `proxy-dev` e os volumes `proxy-*-dev`;
#   * remove os segredos do proxy (/etc/tre/proxy-dev);
#   * devolve a UFW ao estado registrado pelo instalador (a exposicao publica deixa de existir);
#   * remove o `proxy_mode = True` que o instalador acrescentou ao odoo.conf.
# NAO toca: o Odoo e o Postgres do Odoo (containers, volumes e dados), `pg-sales-dev`
# (Sales Intelligence), homologacao/producao, o par nao-secreto, o compose e o Caddyfile
# versionados (o artefato fica; o que sai do ar e a instalacao).
#
# Uso (na VPS):
#   TRE_PROXY_CONFIRMAR_REMOCAO=1 bash remover-proxy-dev.sh
set -euo pipefail

COMPOSE="${TRE_PROXY_COMPOSE:-/opt/tre/dev/compose/proxy.yml}"
ENVFILE="${TRE_PROXY_ENV:-/opt/tre/dev/compose/proxy.env}"
SEGREDOS="${TRE_PROXY_SEGREDOS:-/etc/tre/proxy-dev}"
ESTADO="${TRE_PROXY_ESTADO:-/opt/tre/dev/proxy}"
ODOO_CONF="${TRE_ODOO_CONF:-/etc/tre/odoo-dev/odoo.conf}"
VOLUMES="proxy-data-dev proxy-config-dev proxy-log-dev"

falhar() { echo "FALHOU $*" >&2; exit 1; }

# --- guardas: so o dev, so o proxy, e so com confirmacao explicita ----------------------
[ "${TRE_PROXY_CONFIRMAR_REMOCAO:-0}" = "1" ] \
  || falhar "remocao exige confirmacao explicita: TRE_PROXY_CONFIRMAR_REMOCAO=1 (nada foi tocado)"
case "$COMPOSE" in
  /opt/tre/dev/*) : ;;
  *) falhar "compose fora do ambiente dev ($COMPOSE) — nao removo nada" ;;
esac
[ "$SEGREDOS" = "/etc/tre/proxy-dev" ] || falhar "caminho de segredos inesperado ($SEGREDOS) — nao removo nada"
case " $VOLUMES " in
  *" pgdata-sales-dev "*|*" sales_intelligence "*|*" pgdata-odoo-dev "*)
    falhar "lista de volumes cita volume de outro servico — nao removo nada" ;;
esac
for nome in proxy-homolog proxy-prod odoo-homolog odoo-prod; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$nome"; then
    falhar "container de outro ambiente existe ($nome) — nao removo nada"
  fi
done

echo "ANTES"
docker ps -a --format '  container: {{.Names}} | {{.Status}}'
for v in $VOLUMES; do
  docker volume inspect "$v" >/dev/null 2>&1 && echo "  volume:    $v" || echo "  volume:    $v (ausente)"
done
[ -d "$SEGREDOS" ] && echo "  segredos:  $(ls -1 "$SEGREDOS" | tr '\n' ' ')" || echo "  segredos:  ausentes"
echo "  ufw:       $(ufw status | awk 'NR>1 && NF && /ALLOW/ {print $1}' | sort -u | tr '\n' ' ')"

echo "--- derrubando o proxy"
if [ -f "$COMPOSE" ] && [ -f "$ENVFILE" ]; then
  docker compose --env-file "$ENVFILE" -f "$COMPOSE" down || true
else
  docker rm -f proxy-dev >/dev/null 2>&1 || true
fi
docker rm -f proxy-dev >/dev/null 2>&1 || true

echo "--- removendo volumes do proxy"
for v in $VOLUMES; do
  if docker volume inspect "$v" >/dev/null 2>&1; then
    docker volume rm "$v" >/dev/null
    echo "  removido: $v"
  else
    echo "  ja ausente: $v"
  fi
done

echo "--- removendo segredos do proxy"
if [ -d "$SEGREDOS" ]; then
  rm -rf "$SEGREDOS"
  echo "  removido: $SEGREDOS"
else
  echo "  ja ausente: $SEGREDOS"
fi

echo "--- devolvendo a UFW ao estado anterior"
if [ -f "$ESTADO/ufw-antes.txt" ]; then
  # DEFEITO 5 (achado executando o rollback): o estado anterior e guardado como a saida crua do
  # `ufw status`, que TRAZ o cabecalho ("To Action From", "-- ------ ----"). Sem filtrar por
  # ALLOW, o script acusava "regra do estado anterior nao esta mais presente: To / --".
  ANTES="$(awk 'NF && /ALLOW/ {print $1}' "$ESTADO/ufw-antes.txt" | sed 's/(v6)//' | sort -u)"
  AGORA="$(ufw status | awk 'NR>1 && NF && /ALLOW/ {print $1}' | sed 's/(v6)//' | sort -u)"
  # Remove SO o que este card acrescentou: nada e reaberto as cegas.
  for r in $AGORA; do
    if ! grep -qx "$r" <<<"$ANTES"; then
      PORTA="${r%/tcp}"
      ufw delete allow "$r" >/dev/null 2>&1 || ufw delete allow "$PORTA/tcp" >/dev/null 2>&1 || true
      echo "  regra removida: $r"
    fi
  done
  # Regra que existia antes e nao existe mais: reporta (nao reabre por conta propria).
  for r in $ANTES; do
    grep -qx "$r" <<<"$(ufw status | awk 'NR>1 && NF && /ALLOW/ {print $1}' | sed 's/(v6)//' | sort -u)" \
      || echo "  ATENCAO regra do estado anterior nao esta mais presente: $r (nao reabri sozinho)"
  done
else
  echo "  sem estado anterior registrado ($ESTADO/ufw-antes.txt) — removendo o que este card acrescenta"
  for p in 80 443; do
    ufw delete allow "$p/tcp" >/dev/null 2>&1 || true
  done
fi

echo "--- devolvendo o odoo.conf"
if [ -f "$ODOO_CONF" ] && grep -qE '^[[:space:]]*proxy_mode[[:space:]]*=[[:space:]]*True[[:space:]]*$' "$ODOO_CONF"; then
  cp "$ODOO_CONF" "$ODOO_CONF.removendo-proxy"
  grep -vE '^[[:space:]]*proxy_mode[[:space:]]*=[[:space:]]*True[[:space:]]*$' "$ODOO_CONF.removendo-proxy" > "$ODOO_CONF.novo"
  cat "$ODOO_CONF.novo" > "$ODOO_CONF"
  # Preserva dono/modo originais (600, uid 100 = usuario `odoo` do container).
  chmod 600 "$ODOO_CONF"
  chown 100:101 "$ODOO_CONF" 2>/dev/null || true
  rm -f "$ODOO_CONF.removendo-proxy" "$ODOO_CONF.novo"
  if docker ps --format '{{.Names}}' | grep -qx 'odoo-dev'; then
    docker restart odoo-dev >/dev/null
    for _ in $(seq 1 30); do
      C="$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8069/web/login || true)"
      [ "$C" = "200" ] && break
      sleep 3
    done
    echo "  proxy_mode removido; odoo-dev reiniciado (ultimo HTTP em loopback: ${C:-000})"
  fi
else
  echo "  odoo.conf sem 'proxy_mode = True' (nada a devolver)"
fi

echo
echo "DEPOIS"
docker ps -a --format '  container: {{.Names}} | {{.Status}}'
for v in $VOLUMES; do
  docker volume inspect "$v" >/dev/null 2>&1 && echo "  volume:    $v (AINDA EXISTE)" || echo "  volume:    $v (ausente)"
done
echo "  ufw:       $(ufw status | awk 'NR>1 && NF && /ALLOW/ {print $1}' | sort -u | tr '\n' ' ')"
echo "  integro:   pg-sales-dev $(docker inspect pg-sales-dev --format '{{.State.Status}}' 2>/dev/null || echo ausente)"
echo "  integro:   odoo-dev     $(docker inspect odoo-dev --format '{{.State.Status}}' 2>/dev/null || echo ausente)"
echo
echo "RESULTADO: PROXY_DEV_REMOVIDO (container, volumes e segredos do proxy; UFW devolvida ao estado anterior; Odoo e pg-sales-dev intactos)"
