#!/usr/bin/env bash
# STATUS (06/10/2026): espelho derivado de homolog — REVISAO DE INVERSaO PENDENTE.
#   Nao executar em producao antes de fechar a revisao: as listas de "container/rede de outro
#   ambiente" ainda citam nomes que pertenciam ao outro ambiente. Espelhado por derivacao
#   mecanica; a inversao dos conjuntos (meu x do outro) e' revisao item a item.
# Provisiona o banco de VENDAS (`sales_intelligence`) no ambiente HOMOLOG do TRE.
#
# O que faz, na ordem (a ordem é parte do conserto — ver "armadilhas"):
#   1. confere o par não-secreto versionado (deploy/environments/prod-sales.env);
#   2. garante o SEGREDO /etc/tre/prod-sales/pg.env (600, root, NA VPS): usuário, banco e uma
#      senha gerada lá dentro com `openssl rand -hex 24`. Se o arquivo já existe, NÃO reescreve —
#      trocar a senha de um cluster já inicializado deixa o banco inacessível;
#   3. SÓ ENTÃO valida o compose (`docker compose config` resolve o `env_file`: validar antes do
#      segredo falha com "env file not found" — armadilha medida duas vezes neste projeto);
#   4. publica par + compose na cópia do ambiente (/opt/tre/prod/compose/) e sobe o serviço;
#   5. espera o healthcheck e reporta estado — sem imprimir segredo nenhum.
#
# Idempotente: reexecutar sobre uma instalação existente não recria o cluster e não troca a senha.
#
# Uso:  bash scripts/provision/instalar-pg-sales-prod.sh [--ensaio]
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ALVO="${TRE_ALVO:-root@169.58.24.102}"
CHAVE="${TRE_CHAVE:-$HOME/.ssh/id_ed25519_ops}"
PAR="${TRE_SALES_ENV:-$RAIZ/deploy/environments/prod-sales.env}"
COMPOSE="$RAIZ/deploy/compose/prod/pg-sales.yml"
DEST_COMPOSE="${TRE_DEST_COMPOSE:-/opt/tre/prod/compose}"
DEST_SEGREDO_DIR=/etc/tre/prod-sales
DEST_SEGREDO="$DEST_SEGREDO_DIR/pg.env"
SERVICO=pg-sales-prod
ENSAIO=0
[ "${1:-}" = "--ensaio" ] && ENSAIO=1

SSH=(ssh -i "$CHAVE" -o StrictHostKeyChecking=no "$ALVO")
SCP=(scp -i "$CHAVE" -o StrictHostKeyChecking=no)
R() { "${SSH[@]}" "$@"; }

echo "== instalar $SERVICO no ambiente homolog =="

# 1. par não-secreto
[ -f "$PAR" ] || { echo "FALHOU par ausente: $PAR" >&2; exit 2; }
[ -f "$COMPOSE" ] || { echo "FALHOU compose ausente: $COMPOSE" >&2; exit 2; }
# shellcheck disable=SC1090
set -a; . "$PAR"; set +a
for v in PG_SALES_IMAGEM PG_SALES_VERSAO PG_SALES_DIGEST_ESPERADO; do
  [ -n "${!v:-}" ] || { echo "FALHOU $v vazio no par $PAR" >&2; exit 2; }
done
echo "   par:      imagem=${PG_SALES_IMAGEM}:${PG_SALES_VERSAO} digest=${PG_SALES_DIGEST_ESPERADO:0:19}…"
echo "   destino:  $ALVO:$DEST_COMPOSE (segredo em $DEST_SEGREDO, 600)"

if [ "$ENSAIO" -eq 1 ]; then
  echo "   ENSAIO: nada foi criado nem publicado."
  exit 0
fi

# 2. segredo (primeiro de tudo — ver armadilhas no cabeçalho)
#    `install -d -o <uid>` NÃO serve: o `install` só aceita NOME de usuário (getpwnam). mkdir + chmod.
R "mkdir -p '$DEST_SEGREDO_DIR' && chmod 700 '$DEST_SEGREDO_DIR' && chown root:root '$DEST_SEGREDO_DIR'
   if [ -s '$DEST_SEGREDO' ]; then
     echo '   segredo: PRESERVADO (cluster já inicializado — a senha não se reescreve)'
   else
     umask 077
     printf 'POSTGRES_USER=sales_ai\nPOSTGRES_DB=sales_intelligence\nPOSTGRES_PASSWORD=%s\n' \"\$(openssl rand -hex 24)\" > '$DEST_SEGREDO'
     chmod 600 '$DEST_SEGREDO'
     echo '   segredo: CRIADO em $DEST_SEGREDO (600 root; valor não sai da VPS)'
   fi
   stat -c '   %n modo=%a dono=%U:%G bytes=%s' '$DEST_SEGREDO'"

# 4a. publicar par + compose na cópia do ambiente
R "mkdir -p '$DEST_COMPOSE'"
"${SCP[@]}" -q "$PAR" "$ALVO:$DEST_COMPOSE/pg-sales.env"
"${SCP[@]}" -q "$COMPOSE" "$ALVO:$DEST_COMPOSE/pg-sales.yml"
R "chmod 644 '$DEST_COMPOSE/pg-sales.env' '$DEST_COMPOSE/pg-sales.yml' && echo '   publicado: $DEST_COMPOSE/pg-sales.{env,yml}'"

# 3. validação (depois do segredo) + subida
echo "== docker compose config (o par e o segredo já existem) =="
R "cd '$DEST_COMPOSE' && docker compose --env-file pg-sales.env -f pg-sales.yml config -q && echo '   config OK'" \
  || { echo "FALHOU validação do compose — nada foi criado." >&2; exit 3; }

echo "== subindo $SERVICO =="
# `docker compose` consome o stdin de quem o executa: sem `< /dev/null` o laço remoto é engolido.
R "cd '$DEST_COMPOSE' && docker compose --env-file pg-sales.env -f pg-sales.yml up -d < /dev/null 2>&1 | tail -3"

echo "== esperando o healthcheck =="
for i in $(seq 1 30); do
  estado="$(R "docker inspect '$SERVICO' --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-health{{end}}'" 2>/dev/null || echo ausente)"
  echo "   [$i] $estado"
  case "$estado" in
    running/healthy) echo "   INSTALACAO_OK $SERVICO"; exit 0;;
    ausente|exited/*) echo "FALHOU container $SERVICO em estado $estado" >&2
                      R "docker logs --tail 20 '$SERVICO' 2>&1 | sed 's/^/      /'" >&2 || true
                      exit 4;;
  esac
  sleep 4
done
echo "FALHOU timeout esperando healthy (>120s)" >&2; exit 5
