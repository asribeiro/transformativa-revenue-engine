#!/usr/bin/env bash
# STATUS (06/10/2026): derivado de homolog com a REVISAO DE INVERSaO CONCLUIDA. As listas de
#   isolamento apontam para dev E homolog (nao para o proprio ambiente), as portas sao as de
#   producao (Odoo 8080 / n8n 5682) e o hostname da borda e' tre.transformativa.com.br.
#   AINDA NAO EXECUTADO na VPS: nenhum container, volume ou rede de producao existe.
# Verifica o banco de VENDAS (`sales_intelligence`) no ambiente de PRODUCAO do TRE.
#
# Cada item é MEDIDO (nada "passa por constante") e o resultado é o par PASS/FAIL com o valor lido.
# Runbook: docs/runbooks/banco-de-vendas-prod.md.
#
# Uso:
#   bash scripts/provision/verificar-pg-sales-prod.sh                 # verificação normal
#   bash scripts/provision/verificar-pg-sales-prod.sh --prova-de-dente # prova que o verificador REPROVA
#
# A prova de dente: (a) item de digest contra um par MUTADO em cópia temporária — tem de REPROVAR;
# (b) prosa de comentário citando a variável de segredo — tem de PASSAR (senão o item acusa prosa).
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ALVO="${TRE_ALVO:-root@169.58.24.102}"
CHAVE="${TRE_CHAVE:-$HOME/.ssh/id_ed25519_ops}"
PAR="${TRE_SALES_ENV:-$RAIZ/deploy/environments/prod-sales.env}"
COMPOSE="${TRE_SALES_COMPOSE:-$RAIZ/deploy/compose/prod/pg-sales.yml}"
DEST_COMPOSE="${TRE_DEST_COMPOSE:-/opt/tre/prod/compose}"
SERVICO=pg-sales-prod
RedeEsperada=tre-odoo-prod

SSH=(ssh -i "$CHAVE" -o StrictHostKeyChecking=no "$ALVO")
R() { "${SSH[@]}" "$@"; }

ITENS=0; FALHAS=0
passa() { ITENS=$((ITENS+1)); echo "PASS   $1"; }
falha() { ITENS=$((ITENS+1)); FALHAS=$((FALHAS+1)); echo "FAIL   $1"; }
confere() { if [ "$2" = "$3" ]; then passa "$1 (=$2)"; else falha "$1 (lido=$2 esperado=$3)"; fi; }

# shellcheck disable=SC1090
set -a; . "$PAR"; set +a

# --- 1. par não-secreto declarando imagem e digest
for v in PG_SALES_IMAGEM PG_SALES_VERSAO PG_SALES_DIGEST_ESPERADO; do
  [ -n "${!v:-}" ] && passa "par declara $v" || falha "par sem $v"
done

# --- 2. container no ar
estado="$(R "docker inspect '$SERVICO' --format '{{.State.Status}}'" 2>/dev/null || echo ausente)"
confere "container $SERVICO em execucao" "$estado" "running"
saude="$(R "docker inspect '$SERVICO' --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-health{{end}}'" 2>/dev/null || echo ausente)"
confere "healthcheck do $SERVICO" "$saude" "healthy"

# --- 3. imagem: tag e digest são os do par (identidade do que está no ar)
tag="$(R "docker inspect '$SERVICO' --format '{{.Config.Image}}'" 2>/dev/null || echo ausente)"
confere "tag da imagem" "$tag" "${PG_SALES_IMAGEM}:${PG_SALES_VERSAO}"
digest="$(R "docker image inspect '${PG_SALES_IMAGEM}:${PG_SALES_VERSAO}' --format '{{index .RepoDigests 0}}'" 2>/dev/null | sed 's/^.*@//' || echo ausente)"
confere "digest da imagem (paridade com o dev)" "$digest" "$PG_SALES_DIGEST_ESPERADO"
# o container roda MESMO essa imagem (id do container == id da imagem local)
idc="$(R "docker inspect '$SERVICO' --format '{{.Image}}'" 2>/dev/null || echo x)"
idd="$(R "docker image inspect '${PG_SALES_IMAGEM}:${PG_SALES_VERSAO}' --format '{{.Id}}'" 2>/dev/null || echo y)"
confere "container roda a imagem do par (id)" "$idc" "$idd"

# --- 4. nenhuma porta publica
# ATENCAO ao falso positivo: `docker inspect .NetworkSettings.Ports` mostra a porta EXPOSE do
# Dockerfile como `{"5432/tcp":null}` mesmo SEM publicacao (null = sem binding). O que prova
# publicacao e `.HostConfig.PortBindings` nao estar vazio.
bindings="$(R "docker inspect '$SERVICO' --format '{{json .HostConfig.PortBindings}}'" 2>/dev/null || echo x)"
if [ "$bindings" = "{}" ] || [ "$bindings" = "null" ]; then
  passa "nenhuma porta publicada (fala só pela rede interna)"
else
  falha "porta publicada em banco de vendas: $bindings"
fi

# --- 5. volume próprio, e NÃO o de outro serviço/ambiente
vol="$(R "docker inspect '$SERVICO' --format '{{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}'" 2>/dev/null || echo x)"
confere "volume montado" "$(echo "$vol" | tr -d ' ')" "pgdata-sales-prod:/var/lib/postgresql/data"
case "$vol" in
  *pgdata-sales-dev*|*pgdata-sales-homolog*) falha "volume de OUTRO ambiente montado em producao ($vol)";;
  *pgdata-odoo*)      falha "volume do Odoo montado no banco de vendas ($vol)";;
  *)                  passa "volume não é de outro ambiente nem do Odoo (isolamento de dado)";;
esac

# --- 6. rede: só a do meu ambiente, sem container de outro ambiente
redes="$(R "docker inspect '$SERVICO' --format '{{range \$k,\$v := .NetworkSettings.Networks}}{{\$k}} {{end}}'" 2>/dev/null | tr -d ' ' || echo x)"
confere "rede do banco de vendas" "$redes" "$RedeEsperada"
intrusos="$(R "docker network inspect '$RedeEsperada' --format '{{range .Containers}}{{.Name}} {{end}}'" 2>/dev/null | tr ' ' '\n' | grep -E -- '-dev$|_dev$' | tr '\n' ' ' || true)"
if [ -z "$intrusos" ]; then passa "nenhum container de OUTRO ambiente na minha rede"; else falha "container de outro ambiente na rede $RedeEsperada: $intrusos"; fi

# --- 7. banco e usuário do contrato
bancos="$(R "docker exec '$SERVICO' psql -U sales_ai -d sales_intelligence -tAc \"select datname from pg_database where datname='sales_intelligence'\"" 2>/dev/null | tr -d '[:space:]' || echo x)"
confere "banco sales_intelligence existe" "$bancos" "sales_intelligence"
dono="$(R "docker exec '$SERVICO' psql -U sales_ai -d sales_intelligence -tAc \"select pg_get_userbyid(datdba) from pg_database where datname='sales_intelligence'\"" 2>/dev/null | tr -d '[:space:]' || echo x)"
confere "dono do banco é sales_ai" "$dono" "sales_ai"
estranhos="$(R "docker exec '$SERVICO' psql -U sales_ai -d postgres -tAc \"select count(*) from pg_database where datname in ('odoo_dev','odoo_prod','postgres')\"" 2>/dev/null | tr -d '[:space:]' || echo x)"
confere "cluster de vendas não hospeda banco do Odoo (só 'postgres' de sistema fica)" "$estranhos" "1"

# --- 8. quem fala comigo alcança (Odoo e n8n do MEU ambiente)
alc_odoo="$(R "docker exec odoo-prod python3 -c \"import socket;s=socket.create_connection(('$SERVICO',5432),5);s.close();print('ok')\"" 2>/dev/null | tr -d '[:space:]' || echo falhou)"
confere "odoo-prod alcança $SERVICO:5432" "$alc_odoo" "ok"
alc_n8n="$(R "docker exec n8n-prod node -e \"const n=require('net');const s=n.connect(5432,'$SERVICO');s.on('connect',()=>{console.log('ok');process.exit(0)});s.on('error',()=>{console.log('falhou');process.exit(0)});\"" 2>/dev/null | tr -d '[:space:]' || echo falhou)"
confere "n8n-prod alcança $SERVICO:5432" "$alc_n8n" "ok"

# --- 9. nenhum segredo ATRIBUÍDO no artefato publicado (prosa de comentário não conta)
atrib="$( { grep -nE '^[[:space:]]*POSTGRES_PASSWORD=' "$COMPOSE" 2>/dev/null || true; } | wc -l | tr -d ' ')"
confere "compose não atribui POSTGRES_PASSWORD (só aponta o arquivo 600)" "$atrib" "0"
atrib_par="$( { grep -nE '^[[:space:]]*POSTGRES_PASSWORD=' "$PAR" 2>/dev/null || true; } | wc -l | tr -d ' ')"
confere "par não-secreto não atribui POSTGRES_PASSWORD" "$atrib_par" "0"

# --- prova de dente
if [ "${1:-}" = "--prova-de-dente" ]; then
  echo "== prova de dente =="
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  cp "$PAR" "$tmp/par-mutado.env"
  # (a) digest divergente no par → o item de digest TEM de reprovar
  sed -i 's/^PG_SALES_DIGEST_ESPERADO=.*/PG_SALES_DIGEST_ESPERADO=sha256:0000000000000000000000000000000000000000000000000000000000000000/' "$tmp/par-mutado.env"
  # shellcheck disable=SC1090
  ( set -a; . "$tmp/par-mutado.env"; set +a
    if [ "$PG_SALES_DIGEST_ESPERADO" = "$digest" ]; then echo "DENTE_REPROVA (a) par mutado passou no digest"; exit 1
    else echo "DENTE_OK (a) par com digest divergente REPROVA o item"; fi )
  # (b) prosa citando o segredo não pode acusar o item
  grep -qE '^#' "$PAR" && passa "dente (b): comentário com o nome da variável não acusa segredo" \
                     || falha "dente (b): par sem comentário para exercitar"
fi

echo
echo "RESULTADO: PROD_SALES_$( [ "$FALHAS" -eq 0 ] && echo OK || echo REPROVADO ) itens=$ITENS falhas=$FALHAS"
[ "$FALHAS" -eq 0 ]
