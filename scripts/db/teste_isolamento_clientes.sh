#!/usr/bin/env bash
# =====================================================================================
# teste_isolamento_clientes.sh [dev|homolog|prod] [--prefixo '<prefixo psql>'] [--prova-de-dente]
#
# Aceite TRE-W1-E05-T01 (criterio 2), na forma REFORMULADA pela decisao do dono de
# 30/09/2026 (opcao A, card t_e340c29b, registrada em
# `docs/operations/registro-de-aprovacoes.md` e no `docs/data/DATA_CONTRACT_V1.md`):
#
#     "NAO existem dois clientes no mesmo banco"   (isolamento FISICO: um banco por cliente)
#
# Substitui, NA SUITE, o teste `teste_tenant_rls.sh` (que media a forma ANTIGA — "consulta
# sem filtro de tenant nao devolve dado de outro cliente" — e contra o Data Contract V1.0,
# que nao tem dimensao de cliente, so podia devolver NAO_TESTAVEL; o V1 nao tem o que
# filtrar). O instrumento antigo continua no repo como instrumento do V2 (decisao do dono).
#
# O QUE ESTE TESTE PROVA — e por que assim:
#   O isolamento deixa de ser barreira de SCHEMA (o V1 nao tem dimensao de cliente) e passa
#   a ser regra de PROVISIONAMENTO, por decisao do dono. O teste mede, no alvo real, os
#   lugares onde "dois clientes no mesmo banco" apareceria:
#     1. o alvo responde                               (sem alvo nao ha veredito: fail-closed)
#     2. o schema existe no alvo                       (alvo errado nao da veredito)
#     3. dimensao de cliente no schema do alvo          (base que PODE co-locar 2 clientes)
#     4. bases de aplicacao na instancia do alvo        (2 bases = 2 clientes no mesmo servidor)
#     5. base provisionada no host servindo o schema    (medido POR CONVENCAO DE NOME `pg-*`:
#        o que for provisionado fora da convencao NAO e medido por este item — limite
#        declarado no runbook 8, e o que fica fora da convencao e impresso como informativo)
#   Nada disso e prosa: cada item sai de consulta ao catalogo/`pg_database`/`docker ps`.
#
# LEITURA QUE FALHA NAO E "0 COLUNA" (fail-closed): leitura vazia/erro e distinguida de
# leitura que respondeu zero. Item 3 com leitura vazia/falha -> NAO_TESTAVEL (exit 3, nunca
# verde); item 4 com leitura vazia/falha -> reprovacao. Foi o defeito medido na revisao
# independente: sem checar o rc da leitura, catalogo mudo virava "0 coluna" (verde falso).
#
# SUPERFICIE DO DETECTOR (item 3): coluna cujo nome contenha o token
# `tenant|tenants|cliente|clientes|client|clients` no inicio, no fim ou entre `_`,
# CASE-INSENSITIVE (`~*`): pega `tenant_id`, `tenant_uuid`, `conta_cliente`, `conta_Cliente`.
# Coluna de cliente com OUTRA grafia (ex. `customer_id`, `conta_id`) fica fora desta
# superficie e e reprovada pela ETAPA 1 da suite (as colunas do banco tem de ser exatamente
# as do contrato — `sobram=[...]`): defesa em profundidade, declarada no runbook 8, com caso
# de dente provando o comportamento que o runbook afirma.
#
# VEREDITO DE TRES VALORES (nunca verde por engano):
#   0 = ISOLAMENTO_OK ............... um cliente por base, medido em TODOS os itens
#   1 = ISOLAMENTO_FALHOU ........... dois clientes no mesmo banco / base co-locada /
#                                     dimensao de cliente sem a decisao do V2
#   3 = ISOLAMENTO_NAO_TESTAVEL ..... item que nao pode ser medido no ambiente (docker
#                                     ausente, catalogo ilegivel) — NAO e verde
#   2 = uso incorreto
#
# `--prova-de-dente` prova, em containers DESCARTÁVEIS, que a MESMA medicao REPROVA as
# formas reais de co-locacao e volta a APROVAR quando a mutacao e desfeita:
#   a)  dimensao de cliente com linhas de 2 clientes, grafia `tenant_id`  -> exit 1
#   a2) a MESMA co-locacao com a grafia `tenant_uuid`                     -> exit 1
#   a3) a MESMA co-locacao com grafia mista `conta_Cliente` (maiuscula)   -> exit 1
#   b)  segunda base de aplicacao na mesma instancia                      -> exit 1
#   c)  segundo servico de base (`pg-*`) com o schema no host             -> exit 1
#   d)  medicao impossivel (docker ausente)                               -> exit 3 (nunca verde)
#   e)  catalogo ilegivel (alvo responde, a leitura do catalogo falha)    -> exit 3 (nunca verde)
# Sem isso o teste poderia estar passando por construcao.
#
# REGRA DE AMBIENTE (ADR-005/0008): roda na VPS do ambiente, por `docker exec`, SO LEITURA
# no alvo. As mutacoes da prova de dente rodam em containers descartaveis, removidos no fim.
#
# Variaveis: TRE_RAIZ, TRE_FIXTURE_IMAGEM (padrao postgres:16), TRE_PG_SERVICO/USER/DB.
#            TRE_ISOLAMENTO_SEM_DOCKER=1 — SO teste do proprio roteiro: finge docker ausente
#            para provar que a medicao impossivel vira NAO_TESTAVEL (nunca verde).
# =====================================================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="${TRE_RAIZ:-$(cd "$DIR/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
CONTROLE="public.tre_schema_migrations"
SCHEMA="sales_intelligence"
# Coluna de cliente/tenant e o marcador de co-locacao. Mesmo regex do instrumento do V2
# (teste_tenant_rls.sh): token `tenant|cliente|client` (e plurais) no inicio, no fim ou entre
# `_`, CASE-INSENSITIVE — aplicado com `~*`. A superficie coberta e a declarada no runbook 8.
REGEX_CLIENTE="(^|_)(tenant|tenants|cliente|clientes|client|clients)(_|$)"

AMB="dev"
MODO="medir"
PREFIXO_TXT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) MODO="dente" ;;
    --prefixo)        PREFIXO_TXT="${2:-}"; shift ;;
    --*) echo "uso: $0 [dev|homolog|prod] [--prefixo '<prefixo psql>'] [--prova-de-dente]"; exit 2 ;;
    *)   AMB="$1" ;;
  esac
  shift
done

case "$AMB" in
  dev|homolog|prod) ;;
  *) echo "FALHOU ambiente desconhecido: '$AMB' (esperado dev|homolog|prod)"; exit 2 ;;
esac

ITENS=0
FALHAS=0
NAO_TESTAVEIS=0
ok() { ITENS=$((ITENS + 1)); echo "OK     $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }
nt() { ITENS=$((ITENS + 1)); NAO_TESTAVEIS=$((NAO_TESTAVEIS + 1)); echo "NAO_TESTAVEL $*"; }

docker_disponivel() {
  [ -z "${TRE_ISOLAMENTO_SEM_DOCKER:-}" ] && command -v docker >/dev/null 2>&1
}

# ------------------------------------------------------------------ par do ambiente
if [ -z "$PREFIXO_TXT" ]; then
  PRESERVADO_SERVICO="${TRE_PG_SERVICO:-}"
  PRESERVADO_USUARIO="${TRE_PG_USER:-}"
  PRESERVADO_BANCO="${TRE_PG_DB:-}"
  ARQ_AMB="$RAIZ/deploy/environments/$AMB.env"
  [ -f "$ARQ_AMB" ] && . "$ARQ_AMB"
  SERVICO="${PRESERVADO_SERVICO:-${TRE_PG_SERVICO:-pg-$AMB}}"
  USUARIO="${PRESERVADO_USUARIO:-${TRE_PG_USER:-tre}}"
  BANCO="${PRESERVADO_BANCO:-${TRE_PG_DB:-$SCHEMA}}"
  PREFIXO_TXT="docker exec $SERVICO psql -U $USUARIO -d $BANCO"
fi
read -r -a PREFIXO <<< "$PREFIXO_TXT"
SERVICO_ALVO="$(printf '%s' "$PREFIXO_TXT" | sed -nE 's/^docker exec ([^ ]+).*/\1/p')"
BANCO_ALVO="${PREFIXO[${#PREFIXO[@]}-1]}"
psql_alvo() { "${PREFIXO[@]}" -q "$@" 2>&1; }
# leitura: valor do alvo sem espacos. Exit != 0 = a LEITURA FALHOU (fail-closed: quem chama
# NAO pode tratar falha/vazio como zero — foi assim que a revisao independente mediu um verde falso).
leitura() {
  local saida
  saida="$("${PREFIXO[@]}" -tAc "$1" 2>/dev/null)" || return 1
  printf '%s' "$saida" | tr -d '[:space:]'
}
# numero valido (inteiro nao negativo) — distingue "respondeu 0" de "nao respondeu numero"
numero() { printf '%s' "$1" | grep -qE '^[0-9]+$'; }

# =====================================================================================
# MODO `--prova-de-dente`: containers descartaveis
# =====================================================================================
ALVO_DESCART=""
CO_LOCADO=""
TMP_DESCART=""
limpar_descartaveis() {
  [ -n "$CO_LOCADO" ] && docker rm -f "$CO_LOCADO" >/dev/null 2>&1
  [ -n "$ALVO_DESCART" ] && docker rm -f "$ALVO_DESCART" >/dev/null 2>&1
  echo
  echo "artefatos do teste em: ${TMP_DESCART:-/tmp}"
}

esperar_postgres() {  # <servico> <usuario> <banco>
  local t=0
  while [ "$t" -lt 90 ]; do
    t=$((t + 1))
    if docker exec "$1" psql -U "$2" -d "$3" -tAc 'SELECT 1' >/dev/null 2>&1; then
      sleep 3
      docker exec "$1" psql -U "$2" -d "$3" -tAc 'SELECT 1' >/dev/null 2>&1 && return 0
    fi
    sleep 1
  done
  return 1
}

prova_de_dente() {
  docker_disponivel || {
    echo "FALHOU docker ausente/fingido (a prova de dente roda onde ha Docker)"
    echo "RESULTADO: ISOLAMENTO_DENTE_FALHOU (1 itens, 1 falha)"
    exit 1
  }
  local senha
  ALVO_DESCART="tre-isolamento-$$-$RANDOM"
  senha="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  TMP_DESCART="$(mktemp -d "${TMPDIR:-/tmp}/tre-isolamento-XXXXXX")"
  trap limpar_descartaveis EXIT

  echo "=================================================================="
  echo "-- PROVA DE DENTE: isolamento entre clientes (TRE-W1-E05-T01, criterio 2 na forma"
  echo "-- reformulada: 'nao existem dois clientes no mesmo banco')"
  echo "-- alvo descartavel: $ALVO_DESCART (imagem $IMAGEM)"
  echo "=================================================================="

  if docker run -d --name "$ALVO_DESCART" -e POSTGRES_PASSWORD="$senha" -e POSTGRES_USER=tre \
       -e POSTGRES_DB="$SCHEMA" "$IMAGEM" >/dev/null 2>&1; then
    ok "container descartavel criado"
  else
    ko "nao consegui criar o container descartavel"
    echo "RESULTADO: ISOLAMENTO_DENTE_FALHOU ($ITENS itens, 1 falha)"; exit 1
  fi
  if esperar_postgres "$ALVO_DESCART" tre "$SCHEMA"; then
    ok "container pronto (servidor definitivo, confirmado duas vezes)"
  else
    ko "container nao ficou pronto"
    echo "RESULTADO: ISOLAMENTO_DENTE_FALHOU ($ITENS itens, 1 falha)"; exit 1
  fi
  if docker cp "$MIGRATION" "$ALVO_DESCART:/tmp/m.sql" >/dev/null 2>&1 && \
     docker exec "$ALVO_DESCART" psql -U tre -d "$SCHEMA" -v ON_ERROR_STOP=1 -q -f /tmp/m.sql >"$TMP_DESCART/migration.log" 2>&1; then
    ok "migration congelada aplicada no alvo descartavel (base de UM cliente)"
  else
    ko "migration falhou: $(tail -3 "$TMP_DESCART/migration.log" | tr '\n' ' ')"
    echo "RESULTADO: ISOLAMENTO_DENTE_FALHOU ($ITENS itens, 1 falha)"; exit 1
  fi

  alvo_psql() { docker exec "$ALVO_DESCART" psql -U tre -d "$SCHEMA" -q -v ON_ERROR_STOP=1 "$@" 2>&1; }
  rodar_prova() {  # <log> [--prefixo <texto>] [<env extra>...] — roda a MEDICAO contra o alvo descartavel
    local log="$1"; shift
    local pref="docker exec $ALVO_DESCART psql -U tre -d $SCHEMA"
    if [ "${1:-}" = "--prefixo" ]; then pref="${2:-}"; shift 2; fi
    env "$@" bash "$0" "$AMB" --prefixo "$pref" >"$log" 2>&1
    echo $?
  }
  prova() {  # <n> <rotulo> <mutacao|-> <restauracao|-> <exit esperado> <regex da saida>
    local n="$1" rot="$2" mut="$3" rest="$4" esperado="$5" regex="$6" log rc=""
    if [ "$mut" != "-" ]; then
      if alvo_psql -c "$mut" >/dev/null 2>&1; then
        ok "$n. $rot: co-locacao injetada no alvo descartavel"
      else
        ko "$n. $rot: nao consegui injetar a co-locacao ($(alvo_psql -c "$mut" | tail -1))"
      fi
    fi
    log="$TMP_DESCART/medicao_$n.log"
    rc="$(rodar_prova "$log")"
    echo "-- medicao ($rot) -> exit $rc"
    grep -E '^RESULTADO:|^FALHOU |^NAO_TESTAVEL ' "$log" | sed 's/^/        /'
    if [ "$rc" -ne "$esperado" ]; then
      ko "$n. $rot: exit $rc (esperado $esperado)"
    elif ! grep -qE "$regex" "$log"; then
      ko "$n. $rot: exit $rc correto, mas a saida nao aponta '$regex'"
    else
      ok "$n. $rot: exit $rc e motivo declarado"
    fi
    if [ "$rest" != "-" ]; then
      if alvo_psql -c "$rest" >/dev/null 2>&1; then
        ok "$n. $rot: co-locacao desfeita"
      else
        ko "$n. $rot: nao consegui desfazer a co-locacao"
      fi
    fi
  }

  # baseline: base de UM cliente, sem dimensao de cliente -> a medicao APROVA (exit 0)
  prova 1 "alvo integro (nasce do contrato congelado, um cliente por base)" - - 0 "ISOLAMENTO_OK"

  # (a) AC2: dimensao de cliente com linhas de DOIS clientes na mesma base -> REPROVA
  prova 2 "co-locacao: dimensao de cliente com linhas de 2 clientes" \
    "ALTER TABLE $SCHEMA.organizations ADD COLUMN tenant_id uuid; INSERT INTO $SCHEMA.organizations (id, legal_name, tenant_id, status) VALUES ('ee000001-0000-4000-8000-000000000001','CLIENTE_A Alfa Ltda','aa000001-0000-4000-8000-00000000000a','DISCOVERED'), ('ee000002-0000-4000-8000-000000000002','CLIENTE_B Gama Ltda','bb000002-0000-4000-8000-00000000000b','DISCOVERED')" \
    "ALTER TABLE $SCHEMA.organizations DROP COLUMN tenant_id" \
    1 "dimensao de cliente"
  prova 2b "co-locacao (a) desfeita: a medicao volta a APROVAR" - - 0 "ISOLAMENTO_OK"

  # (a2) AC2: a MESMA co-locacao com a grafia `tenant_uuid` — coluna de cliente que NAO termina em
  #      `_id` tem de ser pega. Caso medido na revisao independente: a regex antiga
  #      (`(^|_)(tenant|cliente|client)(_id)?$`, case-sensitive) nao via `tenant_uuid` e a medicao
  #      saia VERDE com a coluna presente (verde falso).
  prova 2c "co-locacao (grafia 'tenant_uuid'): a coluna de cliente tem de ser REPROVADA" \
    "ALTER TABLE $SCHEMA.organizations ADD COLUMN tenant_uuid uuid; INSERT INTO $SCHEMA.organizations (id, legal_name, tenant_uuid, status) VALUES ('ee000003-0000-4000-8000-000000000003','CLIENTE_A Alfa Ltda','aa000001-0000-4000-8000-00000000000a','DISCOVERED'), ('ee000004-0000-4000-8000-000000000004','CLIENTE_B Gama Ltda','bb000002-0000-4000-8000-00000000000b','DISCOVERED')" \
    "ALTER TABLE $SCHEMA.organizations DROP COLUMN tenant_uuid" \
    1 "dimensao de cliente"
  prova 2d "co-locacao (a2) desfeita: a medicao volta a APROVAR" - - 0 "ISOLAMENTO_OK"

  # (a3) AC2: grafia MISTA e token no MEIO do nome (`conta_Cliente`) — o detector e case-insensitive
  prova 2e "co-locacao (grafia mista 'conta_Cliente'): a coluna de cliente tem de ser REPROVADA" \
    "ALTER TABLE $SCHEMA.contacts ADD COLUMN \"conta_Cliente\" uuid" \
    "ALTER TABLE $SCHEMA.contacts DROP COLUMN \"conta_Cliente\"" \
    1 "dimensao de cliente"
  prova 2f "co-locacao (a3) desfeita: a medicao volta a APROVAR" - - 0 "ISOLAMENTO_OK"

  # (b) AC2: segunda base de aplicacao na MESMA instancia -> REPROVA
  prova 3 "co-locacao: segunda base de aplicacao na mesma instancia" \
    "CREATE DATABASE cliente_b" "DROP DATABASE cliente_b" 1 "bases de aplicacao"
  prova 3b "co-locacao (b) desfeita: a medicao volta a APROVAR" - - 0 "ISOLAMENTO_OK"

  # (c) AC2/provisionamento: segundo servico de base (`pg-*`) com o schema, no mesmo host -> REPROVA
  CO_LOCADO="pg-cliente-b-$$-$RANDOM"
  local senha2 pronto=1
  senha2="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  if docker run -d --name "$CO_LOCADO" -e POSTGRES_PASSWORD="$senha2" -e POSTGRES_USER=tre \
       -e POSTGRES_DB="$SCHEMA" "$IMAGEM" >/dev/null 2>&1; then
    esperar_postgres "$CO_LOCADO" tre "$SCHEMA" || pronto=0
    if [ "$pronto" -eq 1 ] && docker exec "$CO_LOCADO" psql -U tre -d "$SCHEMA" -q -v ON_ERROR_STOP=1 \
         -c "CREATE SCHEMA $SCHEMA" >/dev/null 2>&1; then
      ok "3. co-locacao: segundo servico de base '$CO_LOCADO' de pe, servindo o schema"
    else
      ko "3. nao consegui preparar o segundo servico de base"
    fi
  else
    ko "3. nao consegui criar o segundo servico de base"
  fi
  prova 3c "co-locacao no host: segundo servico de base servindo o schema" - - 1 "provisionada"
  if docker rm -f "$CO_LOCADO" >/dev/null 2>&1; then
    ok "3. segundo servico de base removido (co-locacao no host desfeita)"; CO_LOCADO=""
  else
    ko "3. nao consegui remover o segundo servico de base"; CO_LOCADO=""
  fi
  prova 3d "co-locacao no host (c) desfeita: a medicao volta a APROVAR" - - 0 "ISOLAMENTO_OK"

  # (d) guarda anti-verde: medicao impossivel (docker ausente) -> NAO_TESTAVEL (exit 3), nunca verde
  local log_g rc_g
  log_g="$TMP_DESCART/medicao_sem_docker.log"
  rc_g="$(rodar_prova "$log_g" TRE_ISOLAMENTO_SEM_DOCKER=1)"
  echo "-- guarda: docker ausente -> exit $rc_g"
  grep -E '^RESULTADO:|^NAO_TESTAVEL ' "$log_g" | sed 's/^/        /'
  if [ "$rc_g" -eq 3 ] && grep -qE 'NAO_TESTAVEL' "$log_g"; then
    ok "guarda anti-verde: medicao impossivel (docker ausente) NAO vira verde (exit 3)"
  else
    ko "guarda anti-verde: docker ausente virou exit $rc_g — medicao impossivel nao pode virar verde"
  fi

  # (e) guarda anti-verde: catalogo ILEGIVEL — o alvo responde, mas a leitura do catalogo falha ->
  #     NAO_TESTAVEL (exit 3), nunca verde. E o defeito medido na revisao independente: sem checar
  #     o rc da leitura, um catalogo mudo virava "0 coluna" e a medicao saia VERDE (verde falso).
  #     O prefixo e um wrapper de prova (nao um trapaca do script): responde o resto e falha so a
  #     consulta a `information_schema.columns`.
  local log_e rc_e pref_mudo
  cat >"$TMP_DESCART/psql_catalogo_mudo.sh" <<EOF
#!/usr/bin/env bash
# prova de dente (TRE-W1-E05-T01): o alvo responde, mas a leitura do catalogo falha
if printf '%s\n' "\$*" | grep -q 'information_schema.columns'; then
  echo "ERRO: catalogo indisponivel (prova de dente)" >&2
  exit 1
fi
exec docker exec $ALVO_DESCART psql -U tre -d $SCHEMA "\$@"
EOF
  chmod +x "$TMP_DESCART/psql_catalogo_mudo.sh"
  pref_mudo="bash $TMP_DESCART/psql_catalogo_mudo.sh"
  log_e="$TMP_DESCART/medicao_catalogo_mudo.log"
  rc_e="$(rodar_prova "$log_e" --prefixo "$pref_mudo")"
  echo "-- guarda: catalogo ilegivel (leitura do item 3 falha) -> exit $rc_e"
  grep -E '^RESULTADO:|^NAO_TESTAVEL |^FALHOU ' "$log_e" | sed 's/^/        /'
  if [ "$rc_e" -eq 3 ] && grep -qE 'NAO_TESTAVEL' "$log_e"; then
    ok "guarda anti-verde: leitura de catalogo que falha NAO vira verde (exit 3) — leitura vazia nao e '0 coluna'"
  else
    ko "guarda anti-verde: catalogo ilegivel virou exit $rc_e — leitura vazia nao pode virar verde"
  fi

  echo
  if [ "$FALHAS" -eq 0 ]; then
    echo "RESULTADO: ISOLAMENTO_DENTE_OK ($ITENS itens, 0 falhas)"
    exit 0
  fi
  echo "RESULTADO: ISOLAMENTO_DENTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
}

if [ "$MODO" = "dente" ]; then
  prova_de_dente
fi

# =====================================================================================
# MODO REAL (medir): le o alvo e da o veredito do criterio 2 na forma reformulada
# =====================================================================================
echo "=================================================================="
echo "-- ISOLAMENTO ENTRE CLIENTES — criterio 2 do TRE-W1-E05-T01"
echo "-- forma medida: 'nao existem dois clientes no mesmo banco' (decisao do dono, opcao A)"
echo "-- alvo:  $PREFIXO_TXT"
echo "-- exit:  0 provado · 1 violado · 2 uso · 3 nao testavel (nunca verde)"
echo "=================================================================="

# (1) o alvo responde — fail-closed: sem alvo nao ha veredito
RESPOSTA="$(leitura 'SELECT 1')"
if [ "$RESPOSTA" != "1" ]; then
  ko "alvo nao respondeu (SELECT 1 -> '$RESPOSTA') — sem alvo nao ha veredito (fail-closed)"
  echo "RESULTADO: ISOLAMENTO_FALHOU ($ITENS itens, $FALHAS falhas)"
  exit 1
fi
ok "alvo responde (SELECT 1)"

# (2) o schema existe no alvo — a base e a base de aplicacao
TABELAS="$(leitura "SELECT count(*) FROM information_schema.tables WHERE table_schema='$SCHEMA' AND table_type='BASE TABLE'")"
if printf '%s' "$TABELAS" | grep -qE '^[0-9]+$' && [ "$TABELAS" -gt 0 ]; then
  ok "schema $SCHEMA existe no alvo ($TABELAS tabela(s)) — alvo e a base de aplicacao"
else
  ko "schema $SCHEMA ausente no alvo (tabelas: '$TABELAS') — alvo errado, nao ha o que medir"
  echo "RESULTADO: ISOLAMENTO_FALHOU ($ITENS itens, $FALHAS falhas)"
  exit 1
fi

# (3) NAO existe dimensao de cliente no schema: com ela, a base PODE co-locar dois clientes
# Superficie do detector: token tenant|cliente|client (+ plurais) no inicio/fim/entre `_`,
# case-insensitive. FAIL-CLOSED: leitura vazia/erro NAO e "0 coluna" (ver o cabecalho).
CONSULTA_COLUNAS="SELECT count(*) FROM information_schema.columns WHERE table_schema='$SCHEMA' AND column_name ~* '$REGEX_CLIENTE'"
QTD_COLUNAS_CLIENTE="$(leitura "$CONSULTA_COLUNAS")"; RC_COLUNAS=$?
if [ "$RC_COLUNAS" -ne 0 ] || ! numero "$QTD_COLUNAS_CLIENTE"; then
  echo "-- dimensao de cliente/tenant no schema: NAO MEDIDO (leitura do catalogo falhou ou nao devolveu numero: '${QTD_COLUNAS_CLIENTE:-}')"
  echo "-- causa declarada pelo alvo:"
  psql_alvo -tAc "$CONSULTA_COLUNAS" 2>&1 | tail -2 | sed 's/^/        /'
  nt "nao consegui medir a dimensao de cliente no schema (leitura vazia/erro NAO e '0 coluna') — sem medicao nao ha verde"
else
  COLUNAS_CLIENTE="$(psql_alvo -tAc "SELECT coalesce(string_agg(table_name||'.'||column_name, ', ' ORDER BY table_name), '(nenhuma)') FROM information_schema.columns WHERE table_schema='$SCHEMA' AND column_name ~* '$REGEX_CLIENTE'" 2>/dev/null | tr -s '[:space:]' ' ')"
  echo "-- dimensao de cliente/tenant no schema: $QTD_COLUNAS_CLIENTE ($COLUNAS_CLIENTE)"
  if [ "$QTD_COLUNAS_CLIENTE" -eq 0 ]; then
    ok "sem dimensao de cliente no schema (0 coluna) — a base nao pode co-locar dois clientes em linhas"
  else
    ko "dimensao de cliente no schema: $QTD_COLUNAS_CLIENTE coluna(s) de cliente/tenant ($COLUNAS_CLIENTE) — base multi-cliente sem a decisao do V2; o criterio 'nao existem dois clientes no mesmo banco' foi violado"
  fi
fi
QTD_RLS="$(leitura "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='$SCHEMA' AND c.relkind='r' AND c.relrowsecurity")"
QTD_POLICIES="$(leitura "SELECT count(*) FROM pg_policies WHERE schemaname='$SCHEMA'")"
echo "-- informativo: RLS habilitada em ${QTD_RLS:-?} tabela(s); ${QTD_POLICIES:-?} policy(ies). Sob a opcao A (um banco por cliente) a barreira NAO e de RLS: e de provisionamento."

# (4) UMA base de aplicacao na instancia do alvo (duas = dois clientes no mesmo servidor)
CONSULTA_INSTANCIA="SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres')"
BASES_INSTANCIA="$(leitura "$CONSULTA_INSTANCIA")"; RC_INSTANCIA=$?
QTD_BASES_INSTANCIA="$(printf '%s' "${BASES_INSTANCIA:-}" | tr ',' '\n' | grep -c . || true)"
echo "-- bases de aplicacao na instancia: $QTD_BASES_INSTANCIA (${BASES_INSTANCIA:-nenhuma})"
if [ "$RC_INSTANCIA" -ne 0 ]; then
  ko "nao consegui medir as bases de aplicacao da instancia (leitura do catalogo falhou) — sem medicao o criterio nao pode ser dado como cumprido"
elif [ "${QTD_BASES_INSTANCIA:-0}" -eq 1 ]; then
  ok "uma base de aplicacao na instancia ($BASES_INSTANCIA) — nenhum segundo cliente co-locado na mesma instancia"
else
  ko "bases de aplicacao na mesma instancia: ${QTD_BASES_INSTANCIA:-0} (${BASES_INSTANCIA:-nenhuma}) — isolamento fisico exige UMA base por cliente, no servidor proprio"
fi

# (5) UM servico de base provisionado no host pela CONVENCAO DE NOME `pg-<cliente>-<amb>` servindo o
#     schema. LIMITE DECLARADO (runbook 8): a medicao deste item e POR CONVENCAO de nome — base
#     provisionada fora da convencao nao conta como base provisionada (e sai como informativo,
#     nunca escondida). Nada de prosa: o veredito sai de `docker ps` + consulta ao schema.
servem_o_schema() {  # <container>... -> imprime os que servem o schema (nada se nenhum)
  local c d u achou
  for c in "$@"; do
    achou=""
    for d in "$BANCO_ALVO" "$SCHEMA" postgres; do
      for u in tre sales_ai postgres; do
        if docker exec "$c" psql -U "$u" -d "$d" -tAc "SELECT to_regnamespace('$SCHEMA') IS NOT NULL" 2>/dev/null | grep -q '^t$'; then
          achou="$d"; break
        fi
      done
      [ -n "$achou" ] && break
    done
    [ -n "$achou" ] && printf '%s ' "$c"
  done
}
if ! docker_disponivel; then
  if [ -n "${TRE_ISOLAMENTO_SEM_DOCKER:-}" ]; then
    nt "medicao do provisionamento no host nao executavel (docker fingido ausente) — sem medicao nao ha verde"
  else
    nt "medicao do provisionamento no host nao executavel (docker ausente no ambiente) — sem medicao nao ha verde"
  fi
else
  SERVICOS_PG="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^pg-' | sort | tr '\n' ' ')"
  SERVICOS_FORA="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -vE '^pg-' | sort | tr '\n' ' ')"
  BASES_HOST="$(servem_o_schema $SERVICOS_PG)"
  FORA_CONVENCAO="$(servem_o_schema $SERVICOS_FORA)"
  QTD_BASES_HOST="$(printf '%s' "$BASES_HOST" | wc -w | tr -d ' ')"
  echo "-- servicos 'pg-*' de pe no host: ${SERVICOS_PG:-nenhum}"
  echo "-- destes, servindo o schema $SCHEMA: ${BASES_HOST:-nenhum}"
  echo "-- informativo (limite deste item, runbook 8): containers de pe FORA da convencao 'pg-*' servindo o schema: ${FORA_CONVENCAO:-nenhum} — nao contam como base provisionada; a convencao de nome e o que este item mede"
  if [ "$QTD_BASES_HOST" -eq 1 ]; then
    ok "uma base provisionada pela convencao de nome 'pg-*' serve o schema ($(printf '%s' "$BASES_HOST" | tr -d ' ')) — nenhuma SEGUNDA base provisionada; provisionamento fora da convencao nao e medido por este item"
  elif [ "$QTD_BASES_HOST" -eq 0 ]; then
    nt "nenhuma base 'pg-*' do host servindo o schema $SCHEMA — provisionamento nao medido (nunca verde sem medicao)"
  else
    ko "$QTD_BASES_HOST bases provisionadas no host servindo o schema ($(printf '%s' "$BASES_HOST" | tr -d ' ')) — dois clientes no mesmo host"
  fi
fi

echo
if [ "$FALHAS" -gt 0 ]; then
  echo "RESULTADO: ISOLAMENTO_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
if [ "$NAO_TESTAVEIS" -gt 0 ]; then
  echo "RESULTADO: ISOLAMENTO_NAO_TESTAVEL ($ITENS itens, 0 reprovacoes, $NAO_TESTAVEIS item(ns) nao medido(s) — NAO e verde)"
  exit 3
fi
echo "RESULTADO: ISOLAMENTO_OK ($ITENS itens, 0 falhas)"
exit 0
