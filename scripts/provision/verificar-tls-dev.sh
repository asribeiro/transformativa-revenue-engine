#!/usr/bin/env bash
# Verificador do aceite do card TRE-W2-E01-T02 — TLS, reverse proxy e hardening do Odoo no DEV.
#
# Card: TRE-W2-E01-T02 (`t_1acf11f2`). Roda NA VPS. Confere item a item, com exit code:
#    1. `docker compose config` valido com o par nao-secreto versionado
#    2. container `proxy-dev` de pe, com restart=unless-stopped e rede host
#    3. identidade do artefato: imagem e DIGEST iguais aos registrados no par
#    4. as portas publicas sao SO as decididas (80/443 escutando em endereco publico)
#    5. HTTP 80 redireciona para HTTPS (nada de texto claro servido)
#    6. HTTPS no hostname com a CADEIA VALIDADA contra a ancora da CA (nunca `-k`) -> 200
#    7. a validacao e real: SEM a ancora o mesmo pedido FALHA (prova de que nao ha auto-engano)
#    8. cabecalhos de hardening presentes e `Server` removido
#    9. `/web/database*` (gerenciador de bases) -> 403 mesmo com a credencial do proxy
#   10. o caminho do challenge ACME NAO esta atras do basic auth (404, nunca 401)
#   11. SEM a credencial do proxy o conteudo do Odoo NAO e servido (401; dev nao esta publico)
#   12. Host desconhecido na 443 nao recebe o Odoo
#   13. porta administrativa: 8069 continua SO loopback (docker port + ss) e sem regra na UFW
#   14. UFW libera EXATAMENTE as portas decididas (nem uma a mais)
#   15. o Odoo continua respondendo 200 em loopback (o proxy nao quebrou o servico)
#
# Uso (na VPS): bash verificar-tls-dev.sh
#               bash verificar-tls-dev.sh --prova-de-dente   (muta o comportamento e exige REPROVAR)
#
# Variaveis (a prova de dente usa para apontar o verificador a um alvo mutado):
#   TRE_TLS_BASE_URL  (padrao https://<hostname>:<porta>)   TRE_TLS_ANCORA (PEM da ancora)
#   TRE_TLS_RESOLVE   (padrao 127.0.0.1)                     TRE_TLS_SEM_CREDENCIAL=1 (nao usa -u)
set -uo pipefail

COMPOSE="${TRE_PROXY_COMPOSE:-/opt/tre/dev/compose/proxy.yml}"
ENVFILE="${TRE_PROXY_ENV:-/opt/tre/dev/compose/proxy.env}"
SEGREDOS="${TRE_PROXY_SEGREDOS:-/etc/tre/proxy-dev}"
ODOO_ENV="${TRE_ODOO_ENV:-/opt/tre/dev/compose/odoo.env}"
ODOO_CONF="${TRE_ODOO_CONF:-/etc/tre/odoo-dev/odoo.conf}"

FALHAS=0; ITENS=0
ok()     { ITENS=$((ITENS+1)); echo "OK    $*"; }
falhou() { ITENS=$((ITENS+1)); FALHAS=$((FALHAS+1)); echo "FALHOU $*"; }

le_env() { sed -n "s/^$1=//p" "$ENVFILE" | head -1; }

[ -f "$COMPOSE" ] || { echo "FALHOU compose ausente em $COMPOSE"; exit 2; }
[ -f "$ENVFILE" ] || { echo "FALHOU par nao-secreto ausente em $ENVFILE"; exit 2; }
HOSTNAME_PROXY="$(le_env PROXY_HOSTNAME)"; P_HTTP="$(le_env PROXY_PORTA_HTTP)"; P_HTTPS="$(le_env PROXY_PORTA_HTTPS)"
IMAGEM="$(le_env PROXY_IMAGEM)"; DIGESTO="$(le_env PROXY_DIGEST_ESPERADO)"; USUARIO="$(le_env PROXY_USUARIO)"
PORTA_ODOO="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$ODOO_ENV" 2>/dev/null | head -1)"; PORTA_ODOO="${PORTA_ODOO:-8069}"

PORTA_HTTPS_EFETIVA="${TRE_TLS_PORTA_OVERRIDE:-$P_HTTPS}"
BASE_URL="https://$HOSTNAME_PROXY:$PORTA_HTTPS_EFETIVA"
RESOLVE_HOST="${TRE_TLS_RESOLVE:-127.0.0.1}"

# credencial do proxy: lida SEM imprimir. Sem o arquivo, os itens que dependem dela viram
# nao-medidos (e nao-medido nao e aprovado).
SENHA=""; HASH=""
if [ -f "$SEGREDOS/basicauth.env" ]; then
  SENHA="$(sed -n 's/^TRE_PROXY_SENHA=//p' "$SEGREDOS/basicauth.env" | head -1 | sed "s/^'//; s/'\$//")"
  HASH="$(sed -n 's/^TRE_PROXY_HASH=//p' "$SEGREDOS/basicauth.env" | head -1 | sed "s/^'//; s/'\$//")"
fi
CRED=(); [ "${TRE_TLS_SEM_CREDENCIAL:-0}" = "1" ] && CRED=() || { [ -n "$SENHA" ] && CRED=(-u "$USUARIO:$SENHA"); }

# Ancora da cadeia: por padrao a raiz da CA LOCAL do proxy. Nunca `-k`.
ANCORA="${TRE_TLS_ANCORA:-}"
if [ -z "$ANCORA" ]; then
  ANCORA="$(mktemp)"; chmod 600 "$ANCORA"
  docker exec proxy-dev cat /data/caddy/pki/authorities/local/root.crt > "$ANCORA" 2>/dev/null || true
fi
TEM_ANCORA=0; [ -s "$ANCORA" ] && TEM_ANCORA=1

pedido() { # pedido <caminho> <porta> [com_ancora=1]
  local caminho="$1" porta="$2" ancora="${3:-$TEM_ANCORA}"
  local args=(-s -o /dev/null -m 10 -w '%{http_code}')
  [ "$ancora" = "1" ] && [ "$TEM_ANCORA" = "1" ] && args+=(--cacert "$ANCORA")
  args+=(--resolve "$HOSTNAME_PROXY:$porta:$RESOLVE_HOST")
  curl "${args[@]}" "${CRED[@]}" "https://$HOSTNAME_PROXY:$porta$caminho" 2>/dev/null || true
}

corpo() { # corpo <caminho> <porta> [com_ancora=1] — devolve o corpo (para conferir o que foi servido)
  local caminho="$1" porta="$2" ancora="${3:-$TEM_ANCORA}"
  local args=(-s -m 10)
  [ "$ancora" = "1" ] && [ "$TEM_ANCORA" = "1" ] && args+=(--cacert "$ANCORA")
  args+=(--resolve "$HOSTNAME_PROXY:$porta:$RESOLVE_HOST")
  curl "${args[@]}" "${CRED[@]}" "https://$HOSTNAME_PROXY:$porta$caminho" 2>/dev/null || true
}

echo "== 1. compose =="
if docker compose --env-file "$ENVFILE" -f "$COMPOSE" config -q 2>/dev/null; then
  ok "compose config valido ($COMPOSE + $ENVFILE)"
else
  falhou "compose config invalido ($COMPOSE + $ENVFILE)"
fi
IMAGENS="$(docker compose --env-file "$ENVFILE" -f "$COMPOSE" config --images 2>/dev/null | sort | tr '\n' ' ')"
case "$IMAGENS" in
  *"$IMAGEM"*) ok "imagem declarada no compose: $IMAGEM (tag pinada, nao flutuante)" ;;
  *) falhou "compose nao declara $IMAGEM (imagens: $IMAGENS)" ;;
esac

echo "== 2. container =="
if [ "$(docker inspect proxy-dev --format '{{.State.Running}}' 2>/dev/null)" = "true" ]; then
  ok "proxy-dev em execucao (id $(docker inspect proxy-dev --format '{{.Id}}' | cut -c1-12), restart=$(docker inspect proxy-dev --format '{{.HostConfig.RestartPolicy.Name}}'))"
else
  falhou "proxy-dev nao esta em execucao"
fi
REDE="$(docker inspect proxy-dev --format '{{.HostConfig.NetworkMode}}' 2>/dev/null)"
[ "$REDE" = "host" ] && ok "proxy-dev em rede host (precisa da interface publica e do loopback do Odoo)" \
  || falhou "proxy-dev em rede '$REDE' (esperado host — sem isso o proxy nao alcanca 127.0.0.1:8069)"

echo "== 3. identidade do artefato =="
IMG_CONTAINER="$(docker inspect proxy-dev --format '{{.Config.Image}}' 2>/dev/null)"
[ "$IMG_CONTAINER" = "$IMAGEM" ] && ok "container roda a imagem declarada ($IMG_CONTAINER)" \
  || falhou "container roda '$IMG_CONTAINER' e o par declara '$IMAGEM'"
REPO_DIGEST="$(docker image inspect "$IMAGEM" --format '{{index .RepoDigests 0}}' 2>/dev/null)"
case "$REPO_DIGEST" in
  *"$DIGESTO") ok "digest confere com PROXY_DIGEST_ESPERADO ($DIGESTO)" ;;
  *) falhou "digest '$REPO_DIGEST' nao contem o esperado '$DIGESTO'" ;;
esac

echo "== 4. portas publicas =="
for p in "$P_HTTPS" "$P_HTTP"; do
  ESCUTA="$(ss -lntH "sport = :$p" 2>/dev/null | awk '{print $4}' | sort -u | tr '\n' ' ')"
  case "$ESCUTA" in
    *0.0.0.0*|*"::"*|*"*:"*) ok "porta $p escutando em endereco publico ($ESCUTA) — e onde o proxy atende" ;;
    "") falhou "nada escutando na porta $p" ;;
    *) falhou "porta $p escuta apenas em '$ESCUTA' (esperado endereco publico)" ;;
  esac
done

echo "== 5. HTTP redireciona para HTTPS =="
RED="$(curl -s -o /dev/null -m 10 -w '%{http_code} %{redirect_url}' --resolve "$HOSTNAME_PROXY:$P_HTTP:$RESOLVE_HOST" "http://$HOSTNAME_PROXY/web/login" 2>/dev/null || true)"
case "$RED" in
  30[0-9]*"https://"*) ok "http://$HOSTNAME_PROXY responde $RED (texto claro nao serve conteudo)" ;;
  *) falhou "http://$HOSTNAME_PROXY respondeu '$RED' (esperado redirecionamento 3xx para https)" ;;
esac

echo "== 6. HTTPS com a cadeia validada =="
if [ "$TEM_ANCORA" != "1" ]; then
  falhou "nao consegui obter a ancora da CA do proxy — item NAO MEDIDO (nao-medido nao e aprovado)"
else
  CODIGO="$(pedido "/web/login" "$PORTA_HTTPS_EFETIVA" 1)"
  [ "$CODIGO" = "200" ] && ok "HTTPS 200 em $BASE_URL/web/login com a cadeia validada contra a ancora ($(basename "$ANCORA"))" \
    || falhou "HTTPS $CODIGO em $BASE_URL/web/login (esperado 200)"
fi

echo "== 7. a validacao e real (sem a ancora FALHA) =="
# DEFEITO 2 (achado executando): a primeira versao desta checagem estava INVERTIDA — tratava
# "curl falhou sem a ancora" (que e o resultado BOM) como reprovacao. Medido no alvo: sem a
# ancora o curl devolve RC=60 ("unable to get local issuer certificate"). Agora a logica diz o
# que quer dizer: passou-sem-ancora e que reprova.
SEM_ANCORA_PASSOU=0
curl -s -o /dev/null -m 10 --resolve "$HOSTNAME_PROXY:$PORTA_HTTPS_EFETIVA:$RESOLVE_HOST" "${CRED[@]}" "$BASE_URL/web/login" 2>/dev/null \
  && SEM_ANCORA_PASSOU=1
if [ "$SEM_ANCORA_PASSOU" = "0" ]; then
  ok "sem a ancora o pedido FALHA (a validacao do item 6 nao e auto-engano)"
else
  falhou "o pedido passou SEM a ancora — a cadeia nao esta sendo validada de verdade"
fi

echo "== 8. cabecalhos de hardening =="
if [ "$TEM_ANCORA" != "1" ]; then
  falhou "sem ancora da CA nao consigo ler os cabecalhos com a cadeia validada — item NAO MEDIDO"
else
  CAB="$(curl -s -D - -o /dev/null -m 10 --cacert "$ANCORA" --resolve "$HOSTNAME_PROXY:$PORTA_HTTPS_EFETIVA:$RESOLVE_HOST" "${CRED[@]}" "$BASE_URL/web/login" 2>/dev/null || true)"
  if [ -z "$CAB" ]; then
    falhou "nao obtive resposta para conferir os cabecalhos de hardening"
  else
    for h in 'Strict-Transport-Security' 'X-Content-Type-Options' 'X-Frame-Options' 'Referrer-Policy'; do
      grep -qi "^$h:" <<<"$CAB" && ok "cabecalho $h presente" || falhou "cabecalho $h ausente"
    done
    grep -qi '^Server:' <<<"$CAB" && falhou "cabecalho Server exposto (versao do servidor entregue de graca)" \
      || ok "cabecalho Server removido"
  fi
fi

echo "== 9. gerenciador de bases bloqueado =="
CODIGO="$(pedido "/web/database/manager" "$PORTA_HTTPS_EFETIVA" 1)"
[ "$CODIGO" = "403" ] && ok "/web/database/manager -> 403 (porta administrativa do Odoo fora do ar publico)" \
  || falhou "/web/database/manager -> $CODIGO (esperado 403)"

echo "== 10. caminho do challenge ACME livre =="
CODIGO="$(pedido "/.well-known/acme-challenge/probe-t02" "$PORTA_HTTPS_EFETIVA" 1)"
[ "$CODIGO" = "401" ] && falhou "o basic auth intercepta o challenge ACME (401) — a emissao publica do certificado nao vai funcionar" \
  || ok "challenge ACME nao e interceptado pelo basic auth (HTTP $CODIGO, esperado 404)"

echo "== 11. sem a credencial do proxy o Odoo NAO e servido =="
CORPO_SEM_CRED="$(mktemp)"
# Sem `-u`: e exatamente o que um terceiro na internet faz.
CODIGO="$(curl -s -o "$CORPO_SEM_CRED" -m 10 -w '%{http_code}' --cacert "${ANCORA:-/dev/null}" \
          --resolve "$HOSTNAME_PROXY:$PORTA_HTTPS_EFETIVA:$RESOLVE_HOST" \
          "https://$HOSTNAME_PROXY:$PORTA_HTTPS_EFETIVA/web/login" 2>/dev/null || true)"
if [ "$CODIGO" = "401" ]; then
  ok "sem credencial do proxy -> 401 (o dev nao esta publico sem protecao)"
elif [ "$CODIGO" = "200" ] && grep -qi 'odoo' "$CORPO_SEM_CRED"; then
  falhou "sem credencial do proxy o Odoo FOI servido ($CODIGO, corpo com pagina do Odoo) — dev exposto sem protecao"
else
  falhou "sem credencial do proxy -> $CODIGO (esperado 401)"
fi
rm -f "$CORPO_SEM_CRED"

echo "== 12. Host desconhecido nao recebe o Odoo =="
CORPO_INTRUSO="$(mktemp)"
CODIGO="$(curl -s -o "$CORPO_INTRUSO" -m 10 -w '%{http_code}' --cacert "${ANCORA:-/dev/null}" \
          --resolve "intruso-t02.example:$PORTA_HTTPS_EFETIVA:$RESOLVE_HOST" \
          "https://intruso-t02.example:$PORTA_HTTPS_EFETIVA/web/login" 2>/dev/null || true)"
if [ "$CODIGO" = "200" ] && grep -qi 'odoo' "$CORPO_INTRUSO"; then
  falhou "Host desconhecido recebeu 200 com a pagina do Odoo — o proxy serve o Odoo para qualquer nome"
elif [ "$CODIGO" = "000" ]; then
  ok "Host desconhecido -> handshake TLS recusado (nenhum certificado para esse nome; nao entrega o Odoo)"
else
  ok "Host desconhecido -> HTTP $CODIGO (nao entrega o Odoo)"
fi
rm -f "$CORPO_INTRUSO"

echo "== 13. porta administrativa continua loopback =="
PUBLICO="$(docker port odoo-dev 2>/dev/null)"
case "$PUBLICO" in
  *"-> 0.0.0.0:"*|*"-> [::]:"*) falhou "odoo-dev publica endereco publico ($PUBLICO)" ;;
  *"-> 127.0.0.1:$PORTA_ODOO"*) ok "odoo-dev publica so em loopback ($PUBLICO)" ;;
  "") falhou "odoo-dev nao publica porta nenhuma (esperado 127.0.0.1:$PORTA_ODOO)" ;;
  *) falhou "odoo-dev publica em '$PUBLICO' (esperado 127.0.0.1:$PORTA_ODOO)" ;;
esac
ESCUTA="$(ss -lntH "sport = :$PORTA_ODOO" 2>/dev/null | awk '{print $4}' | sort -u | tr '\n' ' ')"
case "$ESCUTA" in
  *0.0.0.0*|*"*:"*) falhou "porta $PORTA_ODOO escutando em endereco publico ($ESCUTA)" ;;
  "") falhou "nada escutando na porta $PORTA_ODOO" ;;
  *) ok "porta $PORTA_ODOO escuta apenas em $ESCUTA (loopback)" ;;
esac
REGRAS_ODOO="$(ufw status 2>/dev/null | awk 'NR>1 && NF {print $1}' | sort -u | tr '\n' ' ')"
case " $REGRAS_ODOO " in
  *"/$PORTA_ODOO "*) falhou "UFW tem regra para a porta administrativa $PORTA_ODOO (regras: $REGRAS_ODOO)" ;;
  *) ok "UFW sem regra para a porta $PORTA_ODOO (a exposicao e so pelo proxy)" ;;
esac

echo "== 14. UFW libera exatamente o decidido =="
ESPERADO="$(printf '%s/tcp\n' "22" "$P_HTTP" "$P_HTTPS" | sort -u | tr '\n' ' ')"
LIBERADO="$(ufw status 2>/dev/null | awk 'NR>1 && NF && /ALLOW/ {print $1}' | sort -u | tr '\n' ' ')"
[ "$ESPERADO" = "$LIBERADO" ] && ok "UFW libera [$LIBERADO] = exatamente o registrado (22/tcp, $P_HTTP/tcp, $P_HTTPS/tcp)" \
  || falhou "UFW libera [$LIBERADO] e o decidido e [$ESPERADO]"
if command -v fail2ban-client >/dev/null 2>&1; then
  [ "$(systemctl is-active fail2ban 2>/dev/null)" = "active" ] \
    && ok "fail2ban ativo (protecao de forca bruta mantida)" \
    || falhou "fail2ban NAO esta ativo"
fi

echo "== 15. o Odoo continua de pe =="
CODIGO="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://127.0.0.1:$PORTA_ODOO/web/login" 2>/dev/null || true)"
[ "$CODIGO" = "200" ] && ok "Odoo responde 200 em loopback (http://127.0.0.1:$PORTA_ODOO/web/login) — o proxy nao quebrou o servico" \
  || falhou "Odoo respondeu $CODIGO em loopback"
grep -qE '^[[:space:]]*proxy_mode[[:space:]]*=[[:space:]]*True' "$ODOO_CONF" 2>/dev/null \
  && ok "odoo.conf com proxy_mode = True (honra X-Forwarded-* vindo do proxy)" \
  || falhou "odoo.conf SEM proxy_mode = True — o Odoo nao honra os cabecalhos do proxy"

echo "== 16. proxy_mode tem EFEITO (nao e so uma linha no arquivo) =="
# `proxy_mode` sozinho nao prova nada. O proprio Odoo 19 diz quando ele age
# (http.py: `if config['proxy_mode'] and environ.get("HTTP_X_FORWARDED_HOST"): ProxyFix(...)`):
# com proxy_mode ligado E o cabecalho presente, o Odoo passa a usar o X-Forwarded-For como
# endereco do cliente. Medido no alvo (01/10/2026): com proxy_mode -> registra o IP do cabecalho;
# sem -> registra o par do socket (172.18.0.1). Sem isso, todo acesso externo vira "o proxy" no
# log do Odoo — auditoria e qualquer protecao por IP (fail2ban/allowlist) perdem o cliente real.
MARCA="203.0.113.$(( (RANDOM % 200) + 1 ))"   # TEST-NET-3 (RFC 5737): so pode vir do cabecalho
ANTES_LOG="$(docker logs odoo-dev 2>&1 | wc -l)"
curl -s -o /dev/null -m 10 --resolve "x:$PORTA_ODOO:127.0.0.1" \
  -H "X-Forwarded-For: $MARCA" -H 'X-Forwarded-Proto: https' -H "X-Forwarded-Host: $HOSTNAME_PROXY" \
  "http://127.0.0.1:$PORTA_ODOO/web/login?verif-tls-dev=$RANDOM" 2>/dev/null || true
sleep 2
if docker logs odoo-dev 2>&1 | tail -n +$((ANTES_LOG+1)) | grep -q "$MARCA"; then
  ok "o Odoo honrou o X-Forwarded-For ($MARCA registrado) — proxy_mode tem efeito medido"
else
  falhou "o Odoo NAO honrou o X-Forwarded-For: registrou o par do socket, nao o cliente real (proxy_mode sem efeito)"
fi

[ -n "$ANCORA" ] && [ "$TEM_ANCORA" = "1" ] && [ "${TRE_TLS_ANCORA:-}" = "" ] && rm -f "$ANCORA"

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TLS_DEV_OK ($ITENS itens, 0 falhas) hostname=$HOSTNAME_PROXY portas=$P_HTTP/$P_HTTPS"
  exit 0
fi
echo "RESULTADO: TLS_DEV_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
