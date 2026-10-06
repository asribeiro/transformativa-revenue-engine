#!/usr/bin/env bash
# =====================================================================================
# teste_tenant_rls.sh [ambiente] [--prefixo '<prefixo psql>'] [--papel-app <papel>]
#                     [--guc-tenant <nome>] [--prova-de-dente]
#
# INSTRUMENTO DO V2 — NAO e mais chamado pela suite (`scripts/db/suite_banco.sh`).
#
# O criterio 2 do TRE-W1-E05-T01 foi REFORMULADO pela decisao do dono de 30/09/2026 (opcao A —
# isolamento FISICO, um banco por cliente; card t_e340c29b, registrado em
# `docs/operations/registro-de-aprovacoes.md` e em `docs/data/DATA_CONTRACT_V1.md`):
#
#     de:   "consulta sem filtro de tenant devolve vazio ou erro — NUNCA material de outro
#            cliente"  (que este script mede, e que contra o Data Contract V1.0 — sem dimensao
#            de cliente, RLS desligada e papel superuser+bypassrls — so pode dar NAO_TESTAVEL)
#     para: "NAO existem dois clientes no mesmo banco"
#
# O criterio NA FORMA NOVA e medido por `scripts/db/teste_isolamento_clientes.sh` (etapa
# `isolamento` da suite). ESTE script continua versionado como instrumento para o dia em que
# tenant/RLS voltar como dimensao de primeira classe (V2, se houver multi-cliente no mesmo
# banco): nesse cenario o contrato ganha a dimensao e este teste passa a ser o que prove o
# fail-closed. Ele NAO deve ser wireado na suite antes da decisao do V2.
#
# Aceite original TRE-W1-E05-T01 (criterio 2, homologado por Anderson em 29/09/2026):
#   "consulta sem filtro de tenant devolve vazio ou erro — NUNCA material de outro cliente".
#
# O que este teste faz, e por que assim:
#   * MEDE o alvo (o ambiente do card) e da um veredito de TRES valores:
#       0 = testavel e o isolamento passou
#       1 = testavel e o isolamento FALHOU (vazou material de outro cliente)
#       3 = NAO TESTAVEL contra este artefato — a suite nunca chama isso de verde
#   * O veredito NAO e narrativa: sai de consultas ao catalogo (dimensao de cliente/tenant,
#     RLS ligada, policies existentes, atributos do papel da aplicacao) e de consultas de
#     LEITURA no alvo, com o papel da aplicacao.
#   * `--prova-de-dente` prova, em container DESCARTÁVEL, que a MESMA funcao de veredito
#     REPROVA quando a protecao e derrubada — e que ela aprova quando a protecao esta de pe.
#     Sem isso o teste poderia estar passando por construcao.
#
# Convencao assumida (documentada no runbook): o tenant da sessao vem do GUC `app.tenant_id`
# e a policy usa `current_setting('app.tenant_id', true)` (fail-closed: sem GUC, zero linhas).
# Ambiente que adote outro nome passa `--guc-tenant <nome>`.
#
# REGRA DE AMBIENTE (ADR-005/0008): roda na VPS do ambiente, por `docker exec`, só LEITURA.
# A prova de dente roda em container descartavel e nao toca ambiente nenhum.
#
# Variaveis: TRE_RAIZ, TRE_FIXTURE_IMAGEM (padrao postgres:16), TRE_PG_SERVICO/USER/DB.
# =====================================================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="${TRE_RAIZ:-$(cd "$DIR/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
REGEX_TENANT="(^|_)(tenant|tenants|cliente|clientes|client|clients)(_|$)"
# mesmo regex do teste do criterio vigente (teste_isolamento_clientes.sh), aplicado com `~*`:
# token tenant|cliente|client (+ plurais) em qualquer posicao, case-insensitive.

AMB="dev"
MODO="medir"
PREFIXO_TXT=""
PAPEL_APP=""
GUC_TENANT="app.tenant_id"

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) MODO="dente" ;;
    --prefixo)        PREFIXO_TXT="${2:-}"; shift ;;
    --papel-app)      PAPEL_APP="${2:-}"; shift ;;
    --guc-tenant)     GUC_TENANT="${2:-}"; shift ;;
    --*) echo "uso: $0 [dev|homolog] [--prefixo '<prefixo psql>'] [--papel-app <papel>] [--guc-tenant <nome>] [--prova-de-dente]"; exit 2 ;;
    *)   AMB="$1" ;;
  esac
  shift
done

ITENS=0
FALHAS=0
NAO_TESTAVEIS=0
ok() { ITENS=$((ITENS + 1)); echo "OK     $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }
nt() { ITENS=$((ITENS + 1)); NAO_TESTAVEIS=$((NAO_TESTAVEIS + 1)); echo "NAO_TESTAVEL $*"; }

# ------------------------------------------------------------------ par do ambiente
if [ -z "$PREFIXO_TXT" ]; then
  PRESERVADO_SERVICO="${TRE_PG_SERVICO:-}"
  PRESERVADO_USUARIO="${TRE_PG_USER:-}"
  PRESERVADO_BANCO="${TRE_PG_DB:-}"
  ARQ_AMB="$RAIZ/deploy/environments/$AMB.env"
  [ -f "$ARQ_AMB" ] && . "$ARQ_AMB"
  SERVICO="${PRESERVADO_SERVICO:-${TRE_PG_SERVICO:-pg-$AMB}}"
  USUARIO="${PRESERVADO_USUARIO:-${TRE_PG_USER:-tre}}"
  BANCO="${PRESERVADO_BANCO:-${TRE_PG_DB:-sales_intelligence}}"
  PREFIXO_TXT="docker exec $SERVICO psql -U $USUARIO -d $BANCO"
fi
read -r -a PREFIXO <<< "$PREFIXO_TXT"
# -q: sem isso o psql imprime o TAG dos comandos ("SET") e contamina saidas de multiplos
# comandos por -c (defeito achado pela prova de dente: 'SETapp_cliente' != 'app_cliente').
psql_alvo() { "${PREFIXO[@]}" -q "$@" 2>&1; }

# =====================================================================================
# MODO `--prova-de-dente`: fixture multi-cliente em container descartavel
# =====================================================================================
SERVICO_DESCART=""
TMP_DESCART=""
limpar_descartavel() { docker rm -f -v "$SERVICO_DESCART" >/dev/null 2>&1; echo; echo "artefatos do teste em: $TMP_DESCART"; }

prova_de_dente() {
  command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (a prova de dente roda onde ha Docker)"; echo "RESULTADO: TENANT_RLS_DENTE_FALHOU (1 itens, 1 falha)"; exit 1; }
  local senha
  SERVICO_DESCART="tre-tenant-rls-$$-$RANDOM"
  senha="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  TMP_DESCART="$(mktemp -d "${TMPDIR:-/tmp}/tre-tenant-rls-XXXXXX")"
  trap limpar_descartavel EXIT

  echo "=================================================================="
  echo "-- PROVA DE DENTE: isolamento tenant/RLS (TRE-W1-E05-T01, criterio 2)"
  echo "-- alvo descartavel: $SERVICO_DESCART (imagem $IMAGEM)"
  echo "-- fixture: 2 clientes (A/B), RLS ligada, papel 'app_cliente' sem bypass"
  echo "=================================================================="

  esperar_postgres() {  # <servico> <usuario> <banco>
    local tentativas=0
    while [ "$tentativas" -lt 90 ]; do
      tentativas=$((tentativas + 1))
      if docker exec "$1" psql -U "$2" -d "$3" -tAc 'SELECT 1' >/dev/null 2>&1; then
        sleep 3
        docker exec "$1" psql -U "$2" -d "$3" -tAc 'SELECT 1' >/dev/null 2>&1 && return 0
      fi
      sleep 1
    done
    return 1
  }

  # roda o MODO REAL contra o alvo descartavel e confere exit + motivo declarado
  prova() {  # <n> <rotulo> <mutacao|-> <restauracao|-> <exit esperado> <regex esperada> [<papel-app|-=sem flag>]
    local n="$1" rot="$2" mut="$3" rest="$4" esperado="$5" regex="$6" papel="${7:-app_cliente}"
    local saida rc extra=()
    [ "$papel" != "-" ] && extra=(--papel-app "$papel")
    if [ "$mut" != "-" ]; then
      if docker exec "$SERVICO_DESCART" psql -U tre -d sales_intelligence -q -v ON_ERROR_STOP=1 -c "$mut" >/dev/null 2>&1; then
        ok "$n. $rot: mutacao aplicada no alvo descartavel"
      else
        ko "$n. $rot: nao consegui aplicar a mutacao no alvo descartavel"
      fi
    fi
    saida="$(bash "$0" "$AMB" --prefixo "docker exec $SERVICO_DESCART psql -U tre -d sales_intelligence" ${extra[@]+"${extra[@]}"} --guc-tenant "$GUC_TENANT" 2>&1)"
    rc=$?
    echo "$saida" | sed 's/^/        /'
    if [ "$rc" -ne "$esperado" ]; then
      ko "$n. $rot: exit $rc (esperado $esperado) — o veredito nao bate"
    elif ! printf '%s' "$saida" | grep -qiE "$regex"; then
      ko "$n. $rot: exit $rc correto, mas a saida nao aponta '$regex' — decide pelo motivo errado"
    else
      ok "$n. $rot: exit $rc e motivo declarado ($(printf '%s' "$saida" | grep -cE '^(OK|FALHOU|NAO_TESTAVEL)') itens medidos)"
    fi
    if [ "$rest" != "-" ]; then
      if docker exec "$SERVICO_DESCART" psql -U tre -d sales_intelligence -q -v ON_ERROR_STOP=1 -c "$rest" >/dev/null 2>&1; then
        ok "$n. $rot: mutacao desfeita"
      else
        ko "$n. $rot: nao consegui desfazer a mutacao"
      fi
    fi
  }

  if docker run -d --name "$SERVICO_DESCART" -e POSTGRES_PASSWORD="$senha" -e POSTGRES_USER=tre \
       -e POSTGRES_DB=sales_intelligence "$IMAGEM" >/dev/null 2>&1; then
    ok "container descartavel criado ($SERVICO_DESCART)"
  else
    ko "nao consegui criar o container descartavel"; echo "RESULTADO: TENANT_RLS_DENTE_FALHOU ($ITENS itens, 1 falha)"; exit 1
  fi
  if esperar_postgres "$SERVICO_DESCART" tre sales_intelligence; then
    ok "container pronto (servidor definitivo, confirmado duas vezes)"
  else
    ko "container nao ficou pronto"; echo "RESULTADO: TENANT_RLS_DENTE_FALHOU ($ITENS itens, 1 falha)"; exit 1
  fi

  local destino="/tmp/tre_tenant_$(basename "$MIGRATION")"
  if docker cp "$MIGRATION" "$SERVICO_DESCART:$destino" >/dev/null 2>&1 && \
     docker exec "$SERVICO_DESCART" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -f "$destino" >"$TMP_DESCART/migration.log" 2>&1; then
    ok "migration congelada aplicada (alvo nasce do contrato: 12 tabelas, sem tenant e sem RLS)"
  else
    ko "migration falhou: $(tail -3 "$TMP_DESCART/migration.log" | tr '\n' ' ')"
    echo "RESULTADO: TENANT_RLS_DENTE_FALHOU ($ITENS itens, 1 falha)"; exit 1
  fi

  # 1) alvo SEM dimensao de cliente (o proprio contrato V1.0): veredito 3, nunca 0
  #    (sem --papel-app: no alvo recem-criado o papel da aplicacao ainda nao existe)
  prova 1 "alvo sem dimensao de tenant/RLS (o contrato V1.0 medido hoje)" - - 3 "TENANT_RLS_NAO_TESTAVEL" -

  # fixture multi-cliente
  cat >"$TMP_DESCART/fixture_tenant.sql" <<'SQL'
\set ON_ERROR_STOP on
-- Dimensao de cliente/tenant + RLS, em cima do schema congelado (só no alvo descartavel).
ALTER TABLE sales_intelligence.organizations ADD COLUMN tenant_id uuid;

CREATE TABLE sales_intelligence.metricas_cliente (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    valor numeric(10,2)
);

CREATE ROLE app_cliente LOGIN NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA sales_intelligence TO app_cliente;
GRANT SELECT ON ALL TABLES IN SCHEMA sales_intelligence TO app_cliente;

CREATE POLICY p_organizations_tenant ON sales_intelligence.organizations
    FOR SELECT TO app_cliente
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
ALTER TABLE sales_intelligence.organizations ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_metricas_tenant ON sales_intelligence.metricas_cliente
    FOR SELECT TO app_cliente
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
ALTER TABLE sales_intelligence.metricas_cliente ENABLE ROW LEVEL SECURITY;

INSERT INTO sales_intelligence.organizations (id, legal_name, tenant_id, status) VALUES
 ('ee000001-0000-4000-8000-000000000001', 'CLIENTE_A Alfa Ltda', 'aa000001-0000-4000-8000-00000000000a', 'DISCOVERED'),
 ('ee000002-0000-4000-8000-000000000002', 'CLIENTE_A Beta Ltda', 'aa000001-0000-4000-8000-00000000000a', 'DISCOVERED'),
 ('ee000003-0000-4000-8000-000000000003', 'CLIENTE_B Gama Ltda', 'bb000002-0000-4000-8000-00000000000b', 'DISCOVERED');

INSERT INTO sales_intelligence.metricas_cliente (id, tenant_id, valor) VALUES
 ('cc000001-0000-4000-8000-000000000001', 'aa000001-0000-4000-8000-00000000000a', 10.00),
 ('cc000002-0000-4000-8000-000000000002', 'bb000002-0000-4000-8000-00000000000b', 20.00);
SQL
  if docker cp "$TMP_DESCART/fixture_tenant.sql" "$SERVICO_DESCART:/tmp/fixture_tenant.sql" >/dev/null 2>&1 && \
     docker exec "$SERVICO_DESCART" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -f /tmp/fixture_tenant.sql >"$TMP_DESCART/fixture.log" 2>&1; then
    ok "fixture multi-cliente aplicada (2 clientes: 2 linhas para A, 1 para B; RLS ligada nas 2 tabelas)"
  else
    ko "fixture multi-cliente falhou: $(tail -3 "$TMP_DESCART/fixture.log" | tr '\n' ' ')"
    echo "RESULTADO: TENANT_RLS_DENTE_FALHOU ($ITENS itens, 1 falha)"; exit 1
  fi

  # 2) alvo protegido: APROVA (exit 0)
  prova 2 "alvo protegido (RLS + policy + papel sem bypass)" - - 0 "TENANT_RLS_OK"

  # 3) mutacao: policy permissiva (USING true) = o defeito real de quem "esquece o filtro" -> REPROVA
  #    (sem a mutacao, uma policy so derrubada denegaria por padrao e nao provaria nada)
  prova 3 "mutacao 'policy permissiva (USING true)': o vazamento tem de ser REPROVADO" \
    "DROP POLICY p_organizations_tenant ON sales_intelligence.organizations; CREATE POLICY p_organizations_tenant ON sales_intelligence.organizations FOR SELECT TO app_cliente USING (true)" \
    "DROP POLICY p_organizations_tenant ON sales_intelligence.organizations; CREATE POLICY p_organizations_tenant ON sales_intelligence.organizations FOR SELECT TO app_cliente USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)" \
    1 "material de outro cliente"
  prova 3b "mutacao da policy desfeita: o alvo volta a APROVAR" - - 0 "TENANT_RLS_OK"

  # 4) mutacao: papel da aplicacao passa a contornar RLS -> REPROVA
  prova 4 "mutacao 'BYPASSRLS no papel da aplicacao': tem de ser REPROVADA" \
    "ALTER ROLE app_cliente BYPASSRLS" "ALTER ROLE app_cliente NOBYPASSRLS" 1 "contorna RLS"
  prova 4b "mutacao 'BYPASSRLS' desfeita: o alvo volta a APROVAR" - - 0 "TENANT_RLS_OK"

  # 5) mutacao: RLS desligada na tabela -> REPROVA
  prova 5 "mutacao 'DISABLE ROW LEVEL SECURITY': tem de ser REPROVADA" \
    "ALTER TABLE sales_intelligence.metricas_cliente DISABLE ROW LEVEL SECURITY" \
    "ALTER TABLE sales_intelligence.metricas_cliente ENABLE ROW LEVEL SECURITY" \
    1 "material de outro cliente"
  prova 5b "mutacao 'DISABLE RLS' desfeita: o alvo volta a APROVAR" - - 0 "TENANT_RLS_OK"

  echo
  if [ "$FALHAS" -eq 0 ]; then
    echo "RESULTADO: TENANT_RLS_DENTE_OK ($ITENS itens, 0 falhas)"
    exit 0
  fi
  echo "RESULTADO: TENANT_RLS_DENTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
}

if [ "$MODO" = "dente" ]; then
  prova_de_dente
fi

# =====================================================================================
# MODO REAL: mede o alvo e da o veredito sobre o criterio 2
# =====================================================================================
echo "=================================================================="
echo "-- ISOLAMENTO TENANT/RLS — criterio 2 do TRE-W1-E05-T01"
echo "-- alvo:  $PREFIXO_TXT"
echo "-- GUC do tenant da sessao: $GUC_TENANT"
echo "=================================================================="

RESPOSTA="$(psql_alvo -tAc 'SELECT 1' | tr -d '[:space:]')"
if [ "$RESPOSTA" != "1" ]; then
  ko "alvo nao respondeu (SELECT 1 -> '$RESPOSTA') — sem alvo nao ha veredito (fail-closed)"
  echo "RESULTADO: TENANT_RLS_FALHOU ($ITENS itens, $FALHAS falhas)"
  exit 1
fi
ok "alvo responde (SELECT 1)"

CONEXAO="$(psql_alvo -tAc 'SELECT current_user' | tr -d '[:space:]')"
PAPEL="${PAPEL_APP:-$CONEXAO}"
echo "-- usuario da conexao: $CONEXAO | papel da aplicacao sob teste: $PAPEL"

leitura() { psql_alvo -tAc "$1" | tr -d '[:space:]'; }
TABELAS="$(leitura "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE'")"
QTD_COLUNAS_TENANT="$(leitura "SELECT count(*) FROM information_schema.columns WHERE table_schema='sales_intelligence' AND column_name ~* '$REGEX_TENANT'")"
COLUNAS_TENANT="$(psql_alvo -tAc "SELECT coalesce(string_agg(table_name||'.'||column_name, ', ' ORDER BY table_name), '(nenhuma)') FROM information_schema.columns WHERE table_schema='sales_intelligence' AND column_name ~* '$REGEX_TENANT'")"
QTD_TABELAS_RLS="$(leitura "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='sales_intelligence' AND c.relkind='r' AND c.relrowsecurity")"
TABELAS_RLS_NOMES="$(psql_alvo -tAc "SELECT coalesce(string_agg(c.relname, ' ' ORDER BY c.relname), '') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='sales_intelligence' AND c.relkind='r' AND c.relrowsecurity")"
QTD_POLICIES="$(leitura "SELECT count(*) FROM pg_policies WHERE schemaname='sales_intelligence'")"
ATRIBUTOS="$(leitura "SELECT r.rolsuper||'|'||r.rolbypassrls||'|'||(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='sales_intelligence' AND c.relkind='r' AND c.relrowsecurity AND NOT c.relforcerowsecurity AND c.relowner=r.oid) FROM pg_roles r WHERE r.rolname='$PAPEL'")"

if [ -z "$ATRIBUTOS" ]; then
  ko "papel '$PAPEL' nao existe no alvo — impossivel medir o isolamento do ponto de vista da aplicacao"
  echo "RESULTADO: TENANT_RLS_FALHOU ($ITENS itens, $FALHAS falhas)"
  exit 1
fi
SUPER="$(printf '%s' "$ATRIBUTOS" | cut -d'|' -f1)"
BYPASS="$(printf '%s' "$ATRIBUTOS" | cut -d'|' -f2)"
DONO_SEM_FORCE="$(printf '%s' "$ATRIBUTOS" | cut -d'|' -f3)"

echo "-- medicao read-only do alvo:"
echo "   tabelas no schema sales_intelligence ....: $TABELAS"
echo "   colunas de cliente/tenant ..............: $QTD_COLUNAS_TENANT ($COLUNAS_TENANT)"
echo "   tabelas com RLS habilitada .............: $QTD_TABELAS_RLS"
echo "   policies de isolamento .................: $QTD_POLICIES"
echo "   papel '$PAPEL': superuser=$SUPER bypassrls=$BYPASS dono-de-tabela-com-RLS-sem-FORCE=$DONO_SEM_FORCE"

if [ "$QTD_COLUNAS_TENANT" -eq 0 ]; then
  nt "schema NAO tem dimensao de cliente/tenant (0 coluna em $TABELAS tabela(s)) — o criterio 2 nao e expressavel contra este contrato"
else
  ok "schema tem dimensao de cliente/tenant ($QTD_COLUNAS_TENANT coluna(s): $COLUNAS_TENANT)"
fi
if [ "$QTD_TABELAS_RLS" -eq 0 ]; then
  echo "-- aviso: nenhuma das $TABELAS tabelas do schema tem RLS habilitada"
else
  ok "RLS habilitada em $QTD_TABELAS_RLS tabela(s) do schema"
fi
if [ "$QTD_POLICIES" -eq 0 ]; then
  echo "-- aviso: 0 policy de isolamento no schema (RLS sem policy = deny-by-default: a consulta devolve vazio)"
else
  ok "$QTD_POLICIES policy(ies) de isolamento registrada(s)"
fi

if [ "$QTD_COLUNAS_TENANT" -eq 0 ]; then
  echo "-- veredito: NAO_TESTAVEL. O criterio 2 exige, no ambiente do card, que a consulta sem filtro de"
  echo "   tenant devolva vazio ou erro. Sem coluna de cliente nao existe o que provar — e a ausencia e"
  echo "   justamente o risco: quem consultar sem filtro le o schema inteiro."
  echo "   Este veredito NAO e verde: o criterio volta ao Analista de Requisitos (mudanca de schema exige"
  echo "   nova versao do Data Contract + aprovacao humana — docs/data/DATA_CONTRACT_V1.md §10)."
  echo "RESULTADO: TENANT_RLS_NAO_TESTAVEL ($ITENS itens, 0 reprovacoes, $NAO_TESTAVEIS criterio(s) nao testavel(is))"
  exit 3
fi

# --- a partir daqui o alvo tem dimensao de cliente: o criterio e testavel de verdade
# PostgreSQL devolve boolean como texto 'true'/'false' (nao 't'/'f') — comparar com o texto.
if [ "$SUPER" = "true" ] || [ "$BYPASS" = "true" ]; then
  ko "papel da aplicacao '$PAPEL' contorna RLS (superuser=$SUPER bypassrls=$BYPASS) — a policy nao se aplica a ele"
elif [ "$DONO_SEM_FORCE" -gt 0 ]; then
  ko "papel da aplicacao '$PAPEL' e dono de $DONO_SEM_FORCE tabela(s) com RLS sem FORCE ROW LEVEL SECURITY — o dono contorna a policy"
else
  ok "papel da aplicacao '$PAPEL' NAO contorna RLS (nao e superuser, nao tem BYPASSRLS, nao e dono de tabela com RLS sem FORCE)"
fi

TABELAS_TENANT="$(psql_alvo -tAc "SELECT coalesce(string_agg(col.table_name||'|'||col.column_name, ' ' ORDER BY col.table_name), '') FROM information_schema.columns col WHERE col.table_schema='sales_intelligence' AND col.column_name ~* '$REGEX_TENANT'")"
if [ -z "$TABELAS_TENANT" ]; then
  nt "nenhuma tabela tem coluna de cliente/tenant — o isolamento nao e por cliente (criterio nao expressavel)"
  echo "RESULTADO: TENANT_RLS_NAO_TESTAVEL ($ITENS itens, 0 reprovacoes, $NAO_TESTAVEIS criterio(s) nao testavel(is))"
  exit 3
fi
QTD_TABELAS_TENANT="$(printf '%s' "$TABELAS_TENANT" | wc -w | tr -d ' ')"

# Policy em tabela com RLS desligada e INERTE: a consulta sem filtro leria todos os clientes.
SEM_RLS=""
for par in $TABELAS_TENANT; do
  tab="${par%%|*}"
  case " $TABELAS_RLS_NOMES " in *" $tab "*) ;; *) SEM_RLS="$SEM_RLS $tab" ;; esac
done
if [ -z "$SEM_RLS" ]; then
  ok "toda tabela com coluna de cliente/tenant tem RLS habilitada ($QTD_TABELAS_TENANT tabela(s))"
else
  ko "tabela(s) com coluna de cliente SEM RLS habilitada:$SEM_RLS — a policy nao se aplica e a consulta sem filtro le as linhas de todos os clientes"
fi

psql_papel() {  # <sql> [<valor do GUC>] — sessao com o papel da aplicacao
  local pre=""
  [ "$PAPEL" != "$CONEXAO" ] && pre="SET ROLE \"$PAPEL\"; "
  if [ -n "${2:-}" ]; then
    psql_alvo -tAc "$pre SET $GUC_TENANT = '$2'; $1"
  else
    psql_alvo -tAc "$pre $1"
  fi
}

# (0) a troca de papel funciona — impede que um erro de SET ROLE seja confundido com 'fail-closed'
EU="$(psql_papel 'SELECT current_user' | tr -d '[:space:]')"
if [ "$EU" = "$PAPEL" ]; then
  ok "sessao de teste roda como o papel da aplicacao ('$EU') — o isolamento e medido do ponto de vista certo"
else
  ko "sessao de teste NAO roda como o papel da aplicacao ('$EU' != '$PAPEL') — medicao invalida"
  echo "RESULTADO: TENANT_RLS_FALHOU ($ITENS itens, $FALHAS falhas)"
  exit 1
fi

# (a) sessao SEM tenant definido: consulta sem filtro tem de devolver vazio ou erro
VAZOU_SEM_TENANT=""
for par in $TABELAS_TENANT; do
  tab="${par%%|*}"
  saida="$(psql_papel "SELECT count(*) FROM sales_intelligence.$tab")"
  num="$(printf '%s' "$saida" | tr -d '[:space:]')"
  if printf '%s' "$saida" | grep -q 'ERROR'; then
    continue                      # erro e um dos dois resultados aceitos pelo criterio
  elif [ "$num" = "0" ]; then
    continue                      # vazio e o outro resultado aceito
  else
    VAZOU_SEM_TENANT="$VAZOU_SEM_TENANT $tab=$num"
  fi
done
if [ -z "$VAZOU_SEM_TENANT" ]; then
  ok "sessao sem tenant definido: consulta sem filtro devolve VAZIO ou ERRO nas $QTD_TABELAS_TENANT tabela(s) com RLS"
else
  ko "sessao sem tenant definido: consulta sem filtro devolveu linha(s) sem tenant:$VAZOU_SEM_TENANT"
fi

# (b) sessao COM o cliente X, consulta sem filtro: só material de X — nunca de outro cliente
TAB0="${TABELAS_TENANT%%|*}"
COL0="$(printf '%s' "${TABELAS_TENANT%% *}" | cut -d'|' -f2)"
TENANT_X="$(psql_alvo -tAc "SELECT $COL0 FROM sales_intelligence.$TAB0 WHERE $COL0 IS NOT NULL LIMIT 1" | tr -d '[:space:]')"
if [ -z "$TENANT_X" ]; then
  nt "nao existe linha de cliente no alvo — sem material real nao se prova que uma sessao nao ve o cliente alheio"
else
  echo "-- cliente da sessao de teste: $TENANT_X (tabela $TAB0)"
  VAZOU_OUTRO=""
  for par in $TABELAS_TENANT; do
    tab="${par%%|*}"; col="${par##*|}"
    total="$(psql_papel "SELECT count(*) FROM sales_intelligence.$tab" "$TENANT_X" | tr -d '[:space:]')"
    proprio="$(leitura "SELECT count(*) FROM sales_intelligence.$tab WHERE $col = '$TENANT_X'")"
    existe_alheio="$(leitura "SELECT count(*) FROM sales_intelligence.$tab WHERE $col IS DISTINCT FROM '$TENANT_X'")"
    if ! printf '%s' "$total" | grep -qE '^[0-9]+$'; then
      ko "consulta sem filtro na tabela $tab falhou COM o cliente da sessao definido — saida: $total"
      continue
    fi
    printf '%s' "$proprio" | grep -qE '^[0-9]+$' || proprio=0
    vazado=$((total - proprio))
    [ "$vazado" -lt 0 ] && vazado=0
    echo "   $tab: sessao sem filtro viu=$total | material do cliente da sessao=$proprio | de outro cliente no alvo=$existe_alheio | vazado=$vazado"
    [ "$vazado" -gt 0 ] && VAZOU_OUTRO="$VAZOU_OUTRO $tab=$vazado"
  done
  if [ -n "$VAZOU_OUTRO" ]; then
    ko "consulta sem filtro COM O CLIENTE DA SESSAO devolveu material de outro cliente:$VAZOU_OUTRO"
  else
    ok "consulta sem filtro com o cliente da sessao devolveu 0 linha(s) de outro cliente nas $QTD_TABELAS_TENANT tabela(s)"
  fi
fi

echo
if [ "$FALHAS" -gt 0 ]; then
  echo "RESULTADO: TENANT_RLS_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
if [ "$NAO_TESTAVEIS" -gt 0 ]; then
  echo "RESULTADO: TENANT_RLS_NAO_TESTAVEL ($ITENS itens, 0 reprovacoes, $NAO_TESTAVEIS criterio(s) nao testavel(is))"
  exit 3
fi
echo "RESULTADO: TENANT_RLS_OK ($ITENS itens, 0 falhas)"
exit 0
