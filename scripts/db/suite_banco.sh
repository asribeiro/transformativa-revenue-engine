#!/usr/bin/env bash
# =====================================================================================
# suite_banco.sh [ambiente] [--somente-leitura] [--prova-de-dente]
#
# SUITE DE TESTE DO BANCO — TRE-W1-E05-T01. Um comando unico, um veredito:
#
#     bash scripts/db/suite_banco.sh dev
#
# Etapas (na ordem; o alvo e O MESMO para todas — resolvido uma vez, no inicio):
#   0. ambiente ............ identidade do alvo (usuario@banco) + estado do schema + sha da
#                            migration registrada pelo runner (read-only)
#   1. contrato ............ colunas/PK/FK/NOT NULL contra o Data Contract V1.0
#                            (`scripts/verificar_contrato_dados.py --banco`)
#   2. constraints/indices . os 30 indices e PK/FK/UNIQUE item a item
#                            (`scripts/db/verificar_constraints_indices.py --banco`)
#   3. dedup sintetico ..... identificadores fortes, limite 0,94/0,95, auditoria e governanca
#                            do limiar, com sabotagem que TEM de reprovar
#                            (`scripts/dedup/teste_dedup_sintetico.sh`)
#   4. dedup ambiente ...... cenario real no alvo: detecta, mergeia, audita, desfaz e limpa;
#                            estado tem de voltar ao anterior (`deduplicar_organizacoes.py`)
#   5. tenant/RLS .......... isolamento entre clientes: consulta sem filtro devolve vazio ou
#                            erro, NUNCA material de outro cliente
#                            (`scripts/db/teste_tenant_rls.sh`)
#
# VOCABULARIO DE EXIT (o exit code e a resposta, nao o texto):
#   0 = SUITE_OK .......... todas as etapas passaram e executaram itens
#   1 = SUITE_FALHOU ...... alguma etapa reprovou (o log aponta o item)
#   2 = uso incorreto
#   3 = SUITE_NAO_TESTAVEL  nenhuma reprovacao, mas algum criterio homologado NAO e testavel
#                           contra o artefato (hoje: tenant/RLS no contrato V1.0). NAO e verde.
#
# Guarda de confiabilidade do exit code: etapa que termina sem linha `RESULTADO:` ou que
# executa 0 item REPROVA a suite — "sem output" nunca vale como verde.
#
# --somente-leitura: nao escreve no alvo (a etapa 4 vira varredura `--detectar`); e o modo
#   permitido para `prod`. Sem ele, `prod` e recusado (ADR-005).
# --prova-de-dente: prova, em container descartavel, que a suite REPROVA alvo divergente
#   (coluna removida / indice a mais) e volta a aprovar quando a divergencia e desfeita.
#
# REGRA DE AMBIENTE (ADR-0008): roda na VPS do ambiente, por `docker exec`.
# Variaveis: TRE_PG_SERVICO/USER/DB (venc em o arquivo do ambiente), TRE_RAIZ,
#            TRE_FIXTURE_IMAGEM.
# =====================================================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="${TRE_RAIZ:-$(cd "$DIR/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"

AMB="dev"
MODO="aplicar"
while [ $# -gt 0 ]; do
  case "$1" in
    --somente-leitura) MODO="leitura" ;;
    --prova-de-dente)  MODO="dente" ;;
    --*) echo "uso: $0 [dev|homolog|prod] [--somente-leitura] [--prova-de-dente]"; exit 2 ;;
    *)   AMB="$1" ;;
  esac
  shift
done

case "$AMB" in
  dev|homolog|prod) ;;
  *) echo "FALHOU ambiente desconhecido: '$AMB' (esperado dev|homolog|prod)"; exit 2 ;;
esac
if [ "$AMB" = "prod" ] && [ "$MODO" = "aplicar" ]; then
  echo "FALHOU ADR-005: a suite escreve no alvo (etapa 4) — em producao so com --somente-leitura."
  echo "RESULTADO: SUITE_FALHOU (0 itens, 1 falha)"
  exit 1
fi

# ------------------------------------------------------------------ par do ambiente
PRESERVADO_SERVICO="${TRE_PG_SERVICO:-}"
PRESERVADO_USUARIO="${TRE_PG_USER:-}"
PRESERVADO_BANCO="${TRE_PG_DB:-}"
ARQ_AMB="$RAIZ/deploy/environments/$AMB.env"
[ -f "$ARQ_AMB" ] && . "$ARQ_AMB"
SERVICO="${PRESERVADO_SERVICO:-${TRE_PG_SERVICO:-pg-$AMB}}"
USUARIO="${PRESERVADO_USUARIO:-${TRE_PG_USER:-tre}}"
BANCO="${PRESERVADO_BANCO:-${TRE_PG_DB:-sales_intelligence}}"
PREFIXO="docker exec $SERVICO psql -U $USUARIO -d $BANCO"
# o mesmo alvo para TODAS as etapas: os filhos herdam o par resolvido aqui
export TRE_PG_SERVICO="$SERVICO" TRE_PG_USER="$USUARIO" TRE_PG_DB="$BANCO"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-suite-banco-XXXXXX")"
ITENS_SUITE=0
FALHAS=0
NAO_TESTAVEIS=0
RESUMO=""
VEREDITO_FINAL=""

ok() { echo "OK     $*"; }
ko() { echo "FALHOU $*"; }
nt() { echo "NAO_TESTAVEL $*"; }

# ------------------------------------------------------------------ verificacao de etapa
# <nome> <rc> <log>: exige RESULTADO + itens > 0 e classifica o exit code
verificar_etapa() {
  local nome="$1" rc="$2" log="$3" resumo itens veredito
  resumo="$(grep -E '^RESULTADO:' "$log" | tail -1)"
  # Prova negativa da propria suite (AC3): TRE_SUITE_SABOTAGEM faz a etapa PARECER sem saida ou sem
  # item executado — a suite TEM de reprovar. Quem usa isso e o modo --prova-de-dente; so produz
  # reprovacao, nunca verde, entao nao pode gerar aceite falso.
  case "${TRE_SUITE_SABOTAGEM:-}" in
    sem-saida)  resumo="" ;;
    zero-itens) resumo="RESULTADO: SABOTAGEM (0 itens, 0 falhas)" ;;
  esac
  if [ -z "$resumo" ]; then
    ko "$nome: etapa terminou SEM linha RESULTADO (exit $rc) — 'sem output' nunca e verde"
    FALHAS=$((FALHAS + 1))
    RESUMO="$RESUMO
   $nome ............... FALHOU (sem RESULTADO)"
    return 1
  fi
  itens="$(printf '%s' "$resumo" | grep -oE '\(([0-9]+ de )?[0-9]+ itens' | grep -oE '[0-9]+' | tail -1)"
  itens="${itens:-0}"
  if [ "$itens" -eq 0 ]; then
    ko "$nome: etapa nao executou item nenhum (0 itens) — 'sem output' nunca e verde"
    FALHAS=$((FALHAS + 1))
    RESUMO="$RESUMO
   $nome ............... FALHOU (0 itens)"
    return 1
  fi
  ITENS_SUITE=$((ITENS_SUITE + itens))
  if [ "$rc" -eq 0 ]; then
    ok "$nome: $resumo"
    veredito="OK"
  elif [ "$rc" -eq 3 ]; then
    nt "$nome: $resumo"
    veredito="NAO_TESTAVEL"
    NAO_TESTAVEIS=$((NAO_TESTAVEIS + 1))
  else
    ko "$nome: $resumo (exit $rc)"
    veredito="FALHOU"
    FALHAS=$((FALHAS + 1))
  fi
  RESUMO="$RESUMO
   $nome ............... $veredito ($itens itens)"
  return 0
}

etapa() {  # <nome> <rotulo> <comando...>
  local nome="$1" rot="$2"; shift 2
  local log="$TMP/etapa_$nome.log" rc
  echo
  echo "=================================================================="
  echo "== ETAPA $nome — $rot"
  echo "=================================================================="
  "$@" >"$log" 2>&1
  rc=$?
  cat "$log"                     # evidencia: a saida completa da etapa, sem filtro
  verificar_etapa "$nome" "$rc" "$log"
}

# ------------------------------------------------------------------ MODO prova de dente
if [ "$MODO" = "dente" ]; then
  command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente"; echo "RESULTADO: SUITE_DENTE_FALHOU (1 itens, 1 falha)"; exit 1; }
  SERVICO_DESCART="tre-suite-banco-$$-$RANDOM"
  SENHA_DESCART="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  limpar() { docker rm -f "$SERVICO_DESCART" >/dev/null 2>&1; echo; echo "artefatos do teste em: $TMP"; }
  trap limpar EXIT

  esperar_postgres() {
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
  rodar_contra() {  # <log> : roda a suite contra o alvo descartavel; imprime o exit code
    TRE_PG_SERVICO="$SERVICO_DESCART" TRE_PG_USER=tre TRE_PG_DB=sales_intelligence \
      bash "$0" dev >"$1" 2>&1
    echo $?
  }

  echo "=================================================================="
  echo "-- PROVA DE DENTE DA SUITE (TRE-W1-E05-T01): alvo descartavel $SERVICO_DESCART"
  echo "=================================================================="
  if docker run -d --name "$SERVICO_DESCART" -e POSTGRES_PASSWORD="$SENHA_DESCART" -e POSTGRES_USER=tre \
       -e POSTGRES_DB=sales_intelligence "$IMAGEM" >/dev/null 2>&1; then
    echo "OK     container descartavel criado"; ITENS_SUITE=$((ITENS_SUITE + 1))
  else
    echo "FALHOU nao consegui criar o container descartavel"; echo "RESULTADO: SUITE_DENTE_FALHOU (1 itens, 1 falha)"; exit 1
  fi
  if esperar_postgres "$SERVICO_DESCART" tre sales_intelligence; then
    echo "OK     container pronto (servidor definitivo, confirmado duas vezes)"; ITENS_SUITE=$((ITENS_SUITE + 1))
  else
    echo "FALHOU container nao ficou pronto"; echo "RESULTADO: SUITE_DENTE_FALHOU ($ITENS_SUITE itens, 1 falha)"; exit 1
  fi
  if docker cp "$MIGRATION" "$SERVICO_DESCART:/tmp/m.sql" >/dev/null 2>&1 && \
     docker exec "$SERVICO_DESCART" psql -U tre -d sales_intelligence -v ON_ERROR_STOP=1 -q -f /tmp/m.sql >"$TMP/migration.log" 2>&1; then
    echo "OK     migration congelada aplicada no alvo descartavel"; ITENS_SUITE=$((ITENS_SUITE + 1))
  else
    echo "FALHOU migration falhou: $(tail -3 "$TMP/migration.log" | tr '\n' ' ')"; echo "RESULTADO: SUITE_DENTE_FALHOU ($ITENS_SUITE itens, 1 falha)"; exit 1
  fi

  prova_suite() {  # <n> <rotulo> <mutacao|-> <restauracao|-> <exit esperado> <regex esperada>
    local n="$1" rot="$2" mut="$3" rest="$4" esperado="$5" regex="$6" log rc
    if [ "$mut" != "-" ]; then
      if docker exec "$SERVICO_DESCART" psql -U tre -d sales_intelligence -q -v ON_ERROR_STOP=1 -c "$mut" >/dev/null 2>&1; then
        echo "OK     $n. $rot: divergencia injetada no alvo descartavel"; ITENS_SUITE=$((ITENS_SUITE + 1))
      else
        echo "FALHOU $n. $rot: nao consegui injetar a divergencia"; FALHAS=$((FALHAS + 1)); ITENS_SUITE=$((ITENS_SUITE + 1))
      fi
    fi
    log="$TMP/dente_$n.log"
    rc="$(rodar_contra "$log")"
    echo "-- suite contra o alvo ($rot) -> exit $rc"
    grep -E '^RESULTADO:|^FALHOU ' "$log" | sed 's/^/        /'
    if [ "$rc" -ne "$esperado" ]; then
      echo "FALHOU $n. $rot: exit $rc (esperado $esperado)"; FALHAS=$((FALHAS + 1))
    elif ! grep -qE "$regex" "$log"; then
      echo "FALHOU $n. $rot: exit $rc correto, mas a saida nao aponta '$regex'"; FALHAS=$((FALHAS + 1))
    else
      echo "OK     $n. $rot: exit $rc e motivo declarado"; ITENS_SUITE=$((ITENS_SUITE + 1))
    fi
    if [ "$rest" != "-" ]; then
      if docker exec "$SERVICO_DESCART" psql -U tre -d sales_intelligence -q -v ON_ERROR_STOP=1 -c "$rest" >/dev/null 2>&1; then
        echo "OK     $n. $rot: divergencia desfeita"; ITENS_SUITE=$((ITENS_SUITE + 1))
      else
        echo "FALHOU $n. $rot: nao consegui desfazer a divergencia"; FALHAS=$((FALHAS + 1)); ITENS_SUITE=$((ITENS_SUITE + 1))
      fi
    fi
  }

  # AC3: guarda do exit code — etapa que nao produz saida (ou nao executa item) nunca vira verde
  prova_guarda() {  # <sabotagem> <regex esperada na saida>
    local sab="$1" regex="$2" log rc
    log="$TMP/guarda_$sab.log"
    TRE_SUITE_SABOTAGEM="$sab" TRE_PG_SERVICO="$SERVICO_DESCART" TRE_PG_USER=tre TRE_PG_DB=sales_intelligence \
      bash "$0" dev >"$log" 2>&1
    rc=$?
    echo "-- guarda do exit code, sabotagem '$sab' -> exit $rc"
    grep -E '^FALHOU |^RESULTADO:' "$log" | sed 's/^/        /'
    if [ "$rc" -eq 1 ] && grep -qE "$regex" "$log"; then
      echo "OK     guarda: etapa '$sab' reprovou a suite (exit 1) e apontou o motivo"; ITENS_SUITE=$((ITENS_SUITE + 1))
    else
      echo "FALHOU guarda: etapa '$sab' NAO reprovou a suite (exit $rc) — 'sem output' viraria verde"; FALHAS=$((FALHAS + 1)); ITENS_SUITE=$((ITENS_SUITE + 1))
    fi
  }

  # alvo integro: a suite roda inteira e o unico veredito nao-verde e o criterio nao testavel
  prova_suite 1 "alvo integro (nasce do contrato congelado)" - - 3 "SUITE_NAO_TESTAVEL"

  # AC3: a guarda do exit code tem dente (etapa sem RESULTADO / com 0 item reprova a suite)
  prova_guarda sem-saida "SEM linha RESULTADO"
  prova_guarda zero-itens "nao executou item nenhum"

  # AC1: schema divergindo do contrato -> exit != 0, apontando o item
  # (a restauracao tem de recriar TAMBEM o indice da coluna: DROP COLUMN leva o indice junto —
  #  defeito do proprio roteiro de prova, achado pela prova de dente)
  prova_suite 2 "divergencia: coluna do contrato removida (organizations.cnpj)" \
    "ALTER TABLE sales_intelligence.organizations DROP COLUMN cnpj" \
    "ALTER TABLE sales_intelligence.organizations ADD COLUMN cnpj VARCHAR(20); CREATE INDEX idx_organizations_cnpj ON sales_intelligence.organizations (cnpj)" \
    1 "FALHOU contrato"
  prova_suite 2b "divergencia da coluna desfeita: a suite volta ao veredito NAO_TESTAVEL (sem reprovacao)" - - 3 "SUITE_NAO_TESTAVEL"
  prova_suite 3 "divergencia: indice a mais no schema (fora do contrato)" \
    "CREATE INDEX idx_intruso_suite ON sales_intelligence.organizations (city)" \
    "DROP INDEX sales_intelligence.idx_intruso_suite" \
    1 "FALHOU constraints"
  prova_suite 3b "divergencia do indice desfeita: a suite volta ao veredito NAO_TESTAVEL (sem reprovacao)" - - 3 "SUITE_NAO_TESTAVEL"

  echo
  if [ "$FALHAS" -eq 0 ]; then
    echo "RESULTADO: SUITE_DENTE_OK ($ITENS_SUITE itens, 0 falhas)"
    exit 0
  fi
  echo "RESULTADO: SUITE_DENTE_FALHOU ($ITENS_SUITE itens, $FALHAS falha(s))"
  exit 1
fi

# ------------------------------------------------------------------ cabecalho
echo "=================================================================="
echo "-- SUITE DE TESTE DO BANCO — TRE-W1-E05-T01 (ambiente: $AMB, modo: $MODO)"
echo "-- repo:    $RAIZ"
echo "-- alvo:    container '$SERVICO' | usuario '$USUARIO' | banco '$BANCO'"
echo "-- exit:    0 verde · 1 reprovado · 2 uso · 3 criterio nao testavel (nao e verde)"
echo "=================================================================="

# ------------------------------------------------------------------ etapa 0: ambiente
echo
echo "=================================================================="
echo "== ETAPA ambiente — identidade do alvo e estado do schema (read-only)"
echo "=================================================================="
IDENT="$(docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -tAc "SELECT current_user||'@'||current_database()" 2>&1 | tr -d '[:space:]')"
if printf '%s' "$IDENT" | grep -qE '^[A-Za-z0-9_]+@[A-Za-z0-9_]+$'; then
  ok "ambiente: alvo responde como $IDENT"
  ITENS_SUITE=$((ITENS_SUITE + 1))
else
  ko "ambiente: alvo nao respondeu (identidade: '$IDENT') — sem alvo nao ha suite (fail-closed)"
  FALHAS=$((FALHAS + 1))
  echo "RESULTADO: SUITE_FALHOU ($ITENS_SUITE itens, $FALHAS falha(s))"
  exit 1
fi
bash "$RAIZ/scripts/db/estado_do_ambiente.sh" "$AMB" >"$TMP/estado.log" 2>&1
cat "$TMP/estado.log"
if grep -qE '[0-9]+ tabelas \| [0-9]+ indices' "$TMP/estado.log"; then
  ok "ambiente: estado do schema lido ($(grep -oE '[0-9]+ tabelas \| [0-9]+ indices' "$TMP/estado.log" | head -1))"
  ITENS_SUITE=$((ITENS_SUITE + 1))
else
  ko "ambiente: nao consegui ler o estado do schema"
  FALHAS=$((FALHAS + 1))
fi
if [ -f "$MIGRATION" ]; then
  SHA_REPO="$(sha256sum "$MIGRATION" | cut -d' ' -f1)"
  SHA_ALVO="$(docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -tAc \
    "SELECT sha256 FROM public.tre_schema_migrations WHERE versao='0001'" 2>/dev/null | tr -d '[:space:]')"
  if [ -z "$SHA_ALVO" ]; then
    echo "-- aviso: sem registro do runner em public.tre_schema_migrations (alvo aplicado fora do runner?)"
  elif [ "$SHA_ALVO" = "$SHA_REPO" ]; then
    ok "ambiente: migration registrada no alvo == migration do repo (${SHA_REPO:0:12}…)"
    ITENS_SUITE=$((ITENS_SUITE + 1))
  else
    ko "ambiente: migration do alvo (${SHA_ALVO:0:12}…) DIVERGE da do repo (${SHA_REPO:0:12}…)"
    FALHAS=$((FALHAS + 1))
  fi
fi
RESUMO="   ambiente ............. OK"

# ------------------------------------------------------------------ etapas 1..5
etapa contrato "colunas, PK, FK e NOT NULL contra o Data Contract V1.0" \
  python3 "$RAIZ/scripts/verificar_contrato_dados.py" --banco "$PREFIXO"

etapa constraints "os 30 indices e as constraints PK/FK/UNIQUE, item a item" \
  python3 "$RAIZ/scripts/db/verificar_constraints_indices.py" --banco "$PREFIXO"

etapa dedup_sintetico "identificadores fortes, limite 0,94/0,95 e governanca do limiar (sem banco)" \
  bash "$RAIZ/scripts/dedup/teste_dedup_sintetico.sh"

etapa_dedup_leitura() {  # varredura somente-leitura + veredito proprio (o motor nao emite RESULTADO aqui)
  python3 "$RAIZ/scripts/dedup/deduplicar_organizacoes.py" --detectar --ambiente "$AMB" --prefixo "$PREFIXO"
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "RESULTADO: DEDUP_VARREDURA_OK (1 itens, 0 falhas)"
  else
    echo "RESULTADO: DEDUP_VARREDURA_FALHOU (1 itens, 1 falhas)"
  fi
  return $rc
}

if [ "$MODO" = "leitura" ]; then
  etapa dedup_ambiente "deduplicacao no alvo (varredura somente-leitura)" etapa_dedup_leitura
else
  etapa dedup_ambiente "cenario real de deduplicacao no alvo (semeia, mergeia, audita, desfaz, limpa)" \
    python3 "$RAIZ/scripts/dedup/deduplicar_organizacoes.py" --cenario-ambiente --ambiente "$AMB" --prefixo "$PREFIXO"
fi

etapa tenant_rls "isolamento entre clientes: consulta sem filtro de tenant" \
  bash "$RAIZ/scripts/db/teste_tenant_rls.sh" "$AMB" --prefixo "$PREFIXO"

# ------------------------------------------------------------------ veredito da suite
echo
echo "=================================================================="
echo "-- RESUMO DA SUITE (ambiente: $AMB)"
echo "$RESUMO"
echo "-- itens executados: $ITENS_SUITE | reprovacoes: $FALHAS | criterios nao testaveis: $NAO_TESTAVEIS"
echo "=================================================================="
if [ "$FALHAS" -gt 0 ]; then
  echo "RESULTADO: SUITE_FALHOU ($ITENS_SUITE itens, $FALHAS falha(s), $NAO_TESTAVEIS nao testavel(is))"
  exit 1
fi
if [ "$NAO_TESTAVEIS" -gt 0 ]; then
  echo "RESULTADO: SUITE_NAO_TESTAVEL ($ITENS_SUITE itens, 0 reprovacoes, $NAO_TESTAVEIS criterio(s) nao testavel(is) — NAO e verde)"
  exit 3
fi
if [ "$ITENS_SUITE" -eq 0 ]; then
  echo "RESULTADO: SUITE_FALHOU (0 itens executados — 'sem output' nunca e verde)"
  exit 1
fi
echo "RESULTADO: SUITE_OK ($ITENS_SUITE itens, 0 falhas)"
exit 0
