#!/usr/bin/env bash
# =====================================================================================
# aplicar_fixture_smoke.sh [dev|homolog]
#
# Aceite TRE-W1-E02-T01 (criterio 2): aplica a massa minima de smoke
# `db/fixtures/smoke_dev.sql` no ambiente pedido e, no mesmo passo, confere a CONTAGEM POR
# TABELA contra o esperado do proprio fixture (criterio 3), chamando
# `scripts/db/verificar_fixture_smoke.sh`.
#
# Por que roda dentro do container (ADR-0008): o Hermes nao tem rota de rede ate o banco do
# TRE nem cliente `psql`; o trabalho de banco roda NA VPS, via `docker exec`. O fixture entra
# no container por `docker cp` + `psql -f` — NAO depende de stdin de proposito: quem
# orquestra por SSH nao pode ter o script comido pelo stdin de um `docker exec -i`
# (aprendizado do TRE-W1-E01-T01).
#
# O fixture e IDEMPOTENTE (ON CONFLICT DO NOTHING): rodar duas vezes nao duplica massa.
#
# REGRA DE AMBIENTE (ADR-005 — nada nasce em producao): `prod` e RECUSADO aqui; massa de
# smoke nasce em dev e, quando for o caso, homologacao. Sequencia dev -> homolog -> producao.
#
# Uso:
#   scripts/db/aplicar_fixture_smoke.sh dev
#   scripts/db/aplicar_fixture_smoke.sh homolog
#
# Variaveis de ambiente — PRECEDENCIA: a variavel do operador VENCE o arquivo versionado
# `deploy/environments/<ambiente>.env` (o arquivo e default, nao override):
#   TRE_PG_SERVICO, TRE_PG_USER, TRE_PG_DB, TRE_RAIZ
# =====================================================================================
set -uo pipefail

AMB="${1:-dev}"
RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
FIXTURE="$RAIZ/db/fixtures/smoke_dev.sql"
ITENS=0
FALHAS=0

case "$AMB" in
  dev|homolog) ;;
  prod) echo "FALHOU ADR-005: massa de smoke nao nasce em producao. Nada foi tocado em producao."; exit 1 ;;
  *) echo "uso: $0 [dev|homolog]"; exit 2 ;;
esac

ok()  { ITENS=$((ITENS + 1)); echo "OK     $*"; }
ko()  { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }
morrer() { echo "$*"; echo "RESULTADO: FIXTURE_FALHOU"; exit 1; }

consulta() {  # consulta <servico> <usuario> <banco> <sql>
  docker exec "$1" psql -U "$2" -d "$3" -tAc "$4" 2>/dev/null
}

# ------------------------------------------------------------------ par do ambiente
PRESERVADO_SERVICO="${TRE_PG_SERVICO:-}"
PRESERVADO_USUARIO="${TRE_PG_USER:-}"
PRESERVADO_BANCO="${TRE_PG_DB:-}"
ARQ_AMB="$RAIZ/deploy/environments/$AMB.env"
if [ -f "$ARQ_AMB" ]; then
  # shellcheck disable=SC1090
  . "$ARQ_AMB"
fi
SERVICO="${PRESERVADO_SERVICO:-${TRE_PG_SERVICO:-pg-$AMB}}"
USUARIO="${PRESERVADO_USUARIO:-${TRE_PG_USER:-tre}}"
BANCO="${PRESERVADO_BANCO:-${TRE_PG_DB:-sales_intelligence}}"

echo "=================================================================="
echo "-- MASSA DE SMOKE DO DATA CONTRACT — ambiente: $AMB"
echo "-- repo:    $RAIZ"
echo "-- fixture: ${FIXTURE#"$RAIZ"/}"
echo "-- alvo:    container '$SERVICO' | usuario '$USUARIO' | banco '$BANCO'"
echo "=================================================================="

[ -f "$FIXTURE" ] || morrer "FALHOU fixture ausente: $FIXTURE"
ok "fixture encontrado ($(wc -c <"$FIXTURE") bytes, $(grep -c 'INSERT INTO' "$FIXTURE") INSERTs)"

command -v docker >/dev/null 2>&1 || morrer "FALHOU docker ausente: este script roda na VPS do ambiente (ADR-0008)"
if ! docker inspect "$SERVICO" >/dev/null 2>&1; then
  morrer "FALHOU container '$SERVICO' nao existe no host $(hostname) — ambiente '$AMB' nao esta provisionado"
fi
ok "container '$SERVICO' presente"

# Espera ROBUSTA: SELECT 1 tem de funcionar DUAS vezes, com intervalo (a imagem oficial sobe
# um servidor TEMPORARIO durante a inicializacao; `pg_isready` mente nesse momento).
esperar_postgres() {
  local tentativas=0
  while [ "$tentativas" -lt 30 ]; do
    tentativas=$((tentativas + 1))
    if consulta "$1" "$2" "$3" 'SELECT 1' | grep -q '^1$'; then
      sleep 2
      if consulta "$1" "$2" "$3" 'SELECT 1' | grep -q '^1$'; then
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}
if esperar_postgres "$SERVICO" "$USUARIO" "$BANCO"; then
  ok "postgres responde em '$SERVICO' (servidor definitivo, confirmado duas vezes)"
else
  morrer "FALHOU postgres nao responde em '$SERVICO' — massa abortada para nao escrever no vazio"
fi

# ------------------------------------------------------------------ aplicacao
# O fixture entra no container por `docker cp` + `psql -f` (sem stdin).
antes=0
for t in organizations contacts signals research_runs pain_hypotheses scores interactions \
         recommendations agent_runs outbox_events sync_events human_approvals; do
  n="$(consulta "$SERVICO" "$USUARIO" "$BANCO" "SELECT count(*) FROM sales_intelligence.$t")"
  [ -n "$n" ] && antes=$((antes + n))
done
echo "-- linhas no schema ANTES da aplicacao: $antes"

DESTINO="/tmp/tre_fixture_$(basename "$FIXTURE")"
if docker cp "$FIXTURE" "$SERVICO:$DESTINO" >/dev/null 2>&1; then
  ok "fixture copiado para dentro do container ($DESTINO)"
else
  morrer "FALHOU nao consegui copiar o fixture para '$SERVICO'"
fi
if docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -q -f "$DESTINO" \
     >/tmp/tre_fixture_aplicacao.log 2>&1; then
  ok "fixture aplicado sem erro (psql -v ON_ERROR_STOP=1, exit 0)"
else
  docker exec "$SERVICO" rm -f "$DESTINO" >/dev/null 2>&1
  morrer "FALHOU fixture retornou erro: $(tail -3 /tmp/tre_fixture_aplicacao.log | tr '\n' ' ')"
fi
docker exec "$SERVICO" rm -f "$DESTINO" >/dev/null 2>&1
ok "copia temporaria removida do container"

# ------------------------------------------------------------------ contagem (delegada)
PREFIXO="docker exec $SERVICO psql -U $USUARIO -d $BANCO"
if bash "$RAIZ/scripts/db/verificar_fixture_smoke.sh" "$PREFIXO"; then
  ok "contagem por tabela confere com o esperado do fixture"
else
  ko "contagem por tabela NAO confere com o esperado do fixture (ver itens acima)"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: FIXTURE_APLICADO_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: FIXTURE_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
