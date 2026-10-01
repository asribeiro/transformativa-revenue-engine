#!/usr/bin/env bash
# Prova de dente do verificador do card TRE-W2-E01-T02.
#
# Para que serve: um verificador que nunca reprova nao vale nada. Aqui o COMPORTAMENTO e mutado
# de proposito e o verificador tem de REPROVAR — e, desfeita a mutacao, voltar a aprovar.
# Mesma disciplina usada nos cards anteriores (T01, E04, E05): o item nasce com a prova de que
# tem dente.
#
# Mutações:
#   D1. `docker port odoo-dev` passa a dizer 0.0.0.0 -> item 13 (porta administrativa) tem de reprovar
#   D2. `ufw status` passa a mostrar a 8069 liberada -> item 13/14 (UFW) tem de reprovar
#   D3. proxy MUTANTE sem basic auth, em porta separada -> item 11 (dev sem protecao) tem de reprovar
#   D4. (no roteiro de execucao, nao aqui) verificador rodado depois do rollback tem de reprovar
#
# Uso (na VPS): bash prova-de-dente-tls-dev.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFICADOR="$RAIZ/verificar-tls-dev.sh"
[ -f "$VERIFICADOR" ] || { echo "FALHOU verificador nao encontrado em $VERIFICADOR"; exit 2; }

ENVFILE="${TRE_PROXY_ENV:-/opt/tre/dev/compose/proxy.env}"
IMAGEM="$(sed -n 's/^PROXY_IMAGEM=//p' "$ENVFILE" | head -1)"
HOSTNAME_PROXY="$(sed -n 's/^PROXY_HOSTNAME=//p' "$ENVFILE" | head -1)"

FALHAS=0; ITENS=0
ok()     { ITENS=$((ITENS+1)); echo "OK    $*"; }
falhou() { ITENS=$((ITENS+1)); FALHAS=$((FALHAS+1)); echo "FALHOU $*"; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAKE="$TMP/fake"; mkdir -p "$FAKE"
DOCKER_REAL="$(command -v docker)"; UFW_REAL="$(command -v ufw)"

# O verificador tem de estar verde ANTES de qualquer mutacao: sem isso, um exit 1 depois nao
# prova nada (poderia ser o alvo que ja estava quebrado).
echo "== controle: verificador no alvo integro =="
if bash "$VERIFICADOR" > "$TMP/base.out" 2>&1; then
  ok "controle: alvo integro -> $(grep '^RESULTADO' "$TMP/base.out")"
else
  falhou "controle: alvo integro JA reprova — mutar agora nao provaria nada"
  tail -5 "$TMP/base.out"
fi

# ---------------------------------------------------------------------------------------
# D1 — `docker port odoo-dev` diz 0.0.0.0
# ---------------------------------------------------------------------------------------
echo "== D1: docker falso reportando o Odoo em 0.0.0.0 =="
cat > "$FAKE/docker" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "port" ] && [ "\$2" = "odoo-dev" ]; then
  echo "8069/tcp -> 0.0.0.0:8069"; exit 0
fi
exec "$DOCKER_REAL" "\$@"
EOF
chmod +x "$FAKE/docker"
if PATH="$FAKE:$PATH" bash "$VERIFICADOR" > "$TMP/d1.out" 2>&1; then
  falhou "D1: o verificador APROVOU com a porta administrativa publica (item 13 sem dente)"
else
  grep -q 'FALHOU odoo-dev publica endereco publico' "$TMP/d1.out" \
    && ok "D1: reprovou pelo item certo ($(grep '^RESULTADO' "$TMP/d1.out"))" \
    || { falhou "D1: reprovou, mas nao pelo motivo esperado"; grep '^FALHOU' "$TMP/d1.out" | head -3; }
fi

# ---------------------------------------------------------------------------------------
# D2 — `ufw status` com a porta administrativa liberada
# ---------------------------------------------------------------------------------------
echo "== D2: ufw falso com a 8069 liberada =="
cat > "$FAKE/ufw" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "status" ]; then
  printf 'Status: active\n\n     To                         Action      From\n     --                         ------      ----\n[ 1] 22/tcp                     ALLOW IN    Anywhere\n[ 2] 80/tcp                     ALLOW IN    Anywhere\n[ 3] 443/tcp                    ALLOW IN    Anywhere\n[ 4] 8069/tcp                   ALLOW IN    Anywhere\n'
  exit 0
fi
exec "$UFW_REAL" "\$@"
EOF
chmod +x "$FAKE/ufw"
if PATH="$FAKE:$PATH" bash "$VERIFICADOR" > "$TMP/d2.out" 2>&1; then
  falhou "D2: o verificador APROVOU com a 8069 liberada na UFW (item de UFW sem dente)"
else
  if grep -q 'FALHOU UFW tem regra para a porta administrativa' "$TMP/d2.out" || \
     grep -q 'FALHOU UFW libera' "$TMP/d2.out"; then
    ok "D2: reprovou pelo item certo ($(grep '^RESULTADO' "$TMP/d2.out"))"
  else
    falhou "D2: reprovou, mas nao pelo motivo esperado"; grep '^FALHOU' "$TMP/d2.out" | head -3
  fi
fi

# ---------------------------------------------------------------------------------------
# D3 — proxy mutante SEM basic auth, em porta separada
# ---------------------------------------------------------------------------------------
echo "== D3: proxy mutante sem basic auth (porta 8443) =="
PORTA_MUT=8443
cat > "$TMP/Caddyfile.mut" <<EOF
{
	admin off
}

$HOSTNAME_PROXY:$PORTA_MUT {
	tls internal
	reverse_proxy 127.0.0.1:8069
}
EOF
docker rm -f proxy-dente >/dev/null 2>&1 || true
docker rm -f dente-dados >/dev/null 2>&1 || true
if docker run -d --name proxy-dente --network host \
     -e "TRE_DEV_HOSTNAME=$HOSTNAME_PROXY" \
     -v "$TMP/Caddyfile.mut:/etc/caddy/Caddyfile:ro" \
     "$IMAGEM" >/dev/null 2>&1; then
  ORIGEM="$(mktemp)"; chmod 600 "$ORIGEM"
  for _ in $(seq 1 20); do
    docker exec proxy-dente cat /data/caddy/pki/authorities/local/root.crt > "$ORIGEM" 2>/dev/null || true
    [ -s "$ORIGEM" ] && break
    sleep 2
  done
  if [ -s "$ORIGEM" ]; then
    if TRE_TLS_ANCORA="$ORIGEM" TRE_TLS_PORTA_OVERRIDE="$PORTA_MUT" bash "$VERIFICADOR" > "$TMP/d3.out" 2>&1; then
      falhou "D3: o verificador APROVOU um proxy SEM basic auth (item 11 sem dente)"
    else
      grep -q 'dev exposto sem protecao' "$TMP/d3.out" \
        && ok "D3: reprovou pelo item certo ($(grep '^RESULTADO' "$TMP/d3.out"))" \
        || { falhou "D3: reprovou, mas nao pelo motivo esperado"; grep '^FALHOU' "$TMP/d3.out" | head -3; }
    fi
  else
    falhou "D3: o proxy mutante nao subiu (nao consegui a raiz da CA dele) — mutacao NAO MEDIDA"
  fi
  rm -f "$ORIGEM"
else
  falhou "D3: nao consegui subir o proxy mutante — mutacao NAO MEDIDA"
fi
docker rm -f proxy-dente >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------------------
# D4 — `proxy_mode` removido de verdade (mutacao real, com restart)
# ---------------------------------------------------------------------------------------
echo "== D4: odoo.conf sem proxy_mode (mutacao real) =="
CONF="${TRE_ODOO_CONF:-/etc/tre/odoo-dev/odoo.conf}"
GUARDA="$TMP/odoo.conf.guarda"
cp "$CONF" "$GUARDA"
grep -vE '^[[:space:]]*proxy_mode[[:space:]]*=[[:space:]]*True[[:space:]]*$' "$GUARDA" > "$TMP/odoo.conf.mut"
cat "$TMP/odoo.conf.mut" > "$CONF"; chmod 600 "$CONF"; chown 100:101 "$CONF"
docker restart odoo-dev >/dev/null
for _ in $(seq 1 40); do
  [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8069/web/login || true)" = "200" ] && break
  sleep 2
done
if bash "$VERIFICADOR" > "$TMP/d4.out" 2>&1; then
  falhou "D4: o verificador APROVOU com o proxy_mode removido (item 16 sem dente)"
else
  grep -q 'NAO honrou o X-Forwarded-For' "$TMP/d4.out" \
    && ok "D4: reprovou pelo item certo ($(grep '^RESULTADO' "$TMP/d4.out"))" \
    || { falhou "D4: reprovou, mas nao pelo motivo esperado"; grep '^FALHOU' "$TMP/d4.out" | head -3; }
fi
echo "    restaurando o odoo.conf e reiniciando"
cat "$GUARDA" > "$CONF"; chmod 600 "$CONF"; chown 100:101 "$CONF"
docker restart odoo-dev >/dev/null
for _ in $(seq 1 40); do
  [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8069/web/login || true)" = "200" ] && break
  sleep 2
done
if bash "$VERIFICADOR" > "$TMP/d4-volta.out" 2>&1; then
  ok "D4: desfeita a mutacao, o verificador volta a aprovar ($(grep '^RESULTADO' "$TMP/d4-volta.out"))"
else
  falhou "D4: depois de restaurar, o verificador CONTINUA reprovando — o alvo ficou quebrado"
  grep '^FALHOU' "$TMP/d4-volta.out" | head -3
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TLS_DENTE_OK ($ITENS itens, 0 falhas) — o verificador reprova o que tem de reprovar"
  exit 0
fi
echo "RESULTADO: TLS_DENTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
