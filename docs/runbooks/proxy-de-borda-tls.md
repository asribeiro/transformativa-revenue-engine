# Runbook — proxy de borda TLS (Dev, Homolog, Produção)

Decisão: D5 (complemento) — a borda é **compartilhada**; cada ambiente mantém seu Odoo, seu Postgres e
seu n8n isolados. Motivo físico: só **um** processo pode segurar 80/443 na VPS e o Let's Encrypt valida
cada nome pela porta 80. Ver `docs/operations/ambientes-e-promocao.md`.

## 1. O que está no ar

| hostname | destino | proteção |
|---|---|---|
| `dev.tre.transformativa.com.br` | Odoo do dev — `127.0.0.1:8069` | basic auth |
| `odoo-dev.transformativa.com.br` (apelido) | idem (continuidade de runbooks/bookmarks) | basic auth |
| `homolog.tre.transformativa.com.br` | Odoo do homolog — `127.0.0.1:8070` (reservado) | basic auth; `503` até a stack subir |
| `tre.transformativa.com.br` | Odoo de produção — `127.0.0.1:8071` (reservado) | público; `503` até a stack subir |
| qualquer outro Host na 443 | — | `404` (CA local) |

Artefatos versionados: `deploy/compose/edge/{Caddyfile,proxy.yml}` e `deploy/environments/edge-proxy.env`.
Na VPS: `/opt/tre/edge/compose/` (Caddyfile + proxy.yml + cópia `proxy.env`) e o segredo em
`/etc/tre/proxy-edge/basicauth.env` (600).

## 2. Migração (handover de 80/443)

Portas 80/443 **não admitem dois donos**: o handover é parada do antigo e subida do novo (segundos de
indisponibilidade no dev).

```bash
# 1. validar a configuração SEM tomar as portas (não sobe servidor)
docker run --rm --env-file /etc/tre/proxy-edge/basicauth.env \
  -e TRE_HOSTNAME_DEV=dev.tre.transformativa.com.br \
  -e TRE_HOSTNAME_DEV_APELIDO=odoo-dev.transformativa.com.br \
  -e TRE_HOSTNAME_HOMOLOG=homolog.tre.transformativa.com.br \
  -e TRE_HOSTNAME_PROD=tre.transformativa.com.br \
  -e TRE_PORTA_ODOO_DEV=8069 -e TRE_PORTA_ODOO_HOMOLOG=8070 -e TRE_PORTA_ODOO_PROD=8071 \
  -e TRE_PROXY_USUARIO=tre-dev \
  -v /opt/tre/edge/compose/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine \
  caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile

# 2. largar as portas e assumir
docker compose --env-file /opt/tre/dev/compose/proxy.env  -f /opt/tre/dev/compose/proxy.yml  stop
docker compose --env-file /opt/tre/edge/compose/proxy.env -f /opt/tre/edge/compose/proxy.yml up -d

# 3. conferir (o 401 é resposta ESPERADA; o 503 em homolog/prod também)
for n in dev.tre.transformativa.com.br odoo-dev.transformativa.com.br \
         homolog.tre.transformativa.com.br tre.transformativa.com.br; do
  printf '%-40s %s\n' "$n" "$(curl -sS -o /dev/null -m 15 -w '%{http_code}' https://$n/)"
done
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'proxy-(dev|edge)'
```

## 3. Rollback (um comando; nada é destruído)

```bash
docker compose --env-file /opt/tre/edge/compose/proxy.env -f /opt/tre/edge/compose/proxy.yml down
docker compose --env-file /opt/tre/dev/compose/proxy.env  -f /opt/tre/dev/compose/proxy.yml  start
```

O container do dev continua existindo (só parado) e os volumes `proxy-*-dev` ficam intactos — inclusive o
certificado já emitido para `odoo-dev.transformativa.com.br`.

## 4. Armadilhas medidas

- **`docker stop` é reversível; `down` remove o container.** No handover, use `stop` no lado que sai.
- **`restart: unless-stopped`**: um container parado **fica** parado depois do restart do daemon. Com
  `always`, voltaria e disputaria a porta — foi por isso que o dev usou `unless-stopped`.
- **Healthcheck tem de usar o NOME** (não `127.0.0.1`): o Caddy recusa handshake de SNI desconhecido, e
  healthcheck que sempre reprova mascara falha real. Daí o `extra_hosts: <nome>:127.0.0.1` no compose.
- **Certificado público só sai com o registro A resolvido.** Medido em 05/10/2026: `dev.tre`, `homolog.tre`
  e `tre` resolviam mas caíam no bloco catch-all `:443 { tls internal }`, e o cliente rejeitava
  (`tlsv1 alert internal error`) — parecia defeito de infraestrutura, era nome sem site configurado.
  Verificador: `scripts/verifica_dns_tre.sh` (DNS) e o passo 3 acima (TLS).
- **`503` em Homolog/Produção é desenho, não falha**: o nome existe e o TLS responde; a stack ainda não
  subiu. Erro de TLS nesses nomes seria pior — pareceria problema de infraestrutura.
