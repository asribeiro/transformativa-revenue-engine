#!/usr/bin/env bash
# =====================================================================================
# teste_scout_aceite.sh [--prova-de-dente] [--manter] [--codigo <scout.py>]
#
# ACEITE DO AGENTE SCOUT v1 — TRE-W4-E01-T01. Roda ON DE A VPS do ambiente (ADR-0008:
# o PostgreSQL vive la; o container do Hermes so orquestra por SSH).
#
# O que ele faz, em um container PostgreSQL DESCARTÁVEL (nunca toca pg-sales-dev,
# pg-odoo-dev nem qualquer outro container):
#   0. guardas ....... docker/python3/migration presentes; o nome do container NAO pode
#                      existir (se existir, ABORTA em vez de mexer no que nao e dele)
#   1. sobe ......... container novo com a migration 0001 + 2 organizacoes pre-existentes
#   2. rodada 1 ..... 10 candidatas sinteticas: 3 novas, 2 que casam forte com as
#                      pre-existentes, 1 com fortes CONFLITANTES, 1 sem identificador forte,
#                      1 com CNPJ invalido, 1 sem nome, 1 com fonte fora do vocabulario
#   3. rodada 2 ..... a MESMA fonte de novo: 0 organizacao nova (retry nao cria duplicata)
#   4. guardas ...... --ambiente prod recusado; --planejar com prefixo inexistente nao conecta
#   5. desfazer ..... dry-run nao apaga; --confirmo apaga SO o que a rodada 1 criou
#   6. veredito ..... ACEITE_SCOUT_001_OK / ACEITE_SCOUT_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do scout.py (idempotencia, forte obrigatorio,
#   recusa de prod) e exige que o aceite REPROVE; baseline verde antes e depois. Mutacao que
#   nao se aplica na ancora tambem reprova (e buraco de verificacao, nao alivio).
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_SCOUT_CONTAINER (default pg-scout-acc), TRE_SCOUT_TRABALHO (dir de trabalho).
# Exit: 0 = ACEITE_SCOUT_001_OK · 1 = FALHOU · 2 = uso/guarda.
# =====================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_SCOUT_CONTAINER:-pg-scout-acc}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="scout-aceite-descartavel"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
SCOUT_PY="$RAIZ/hermes/agents/scout/scout.py"
TRABALHO="${TRE_SCOUT_TRABALHO:-$(mktemp -d /tmp/scout-aceite-XXXXXX)}"

MANTER=0
DENTE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter)         MANTER=1 ;;
    --codigo)         SCOUT_PY="$2"; shift ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <scout.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <scout.py>]"; exit 2 ;;
  esac
  shift
done

ITENS_OK=0
ITENS_FALHOU=0
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then
    echo "OK     $1 ($3)"; ITENS_OK=$((ITENS_OK + 1))
  else
    echo "FALHOU $1 (esperado=$2 obtido=$3)"; ITENS_FALHOU=$((ITENS_FALHOU + 1))
  fi
}
contem() { # <nome> <texto> <trecho>
  case "$2" in
    *"$3"*) echo "OK     $1 (contem '$3')"; ITENS_OK=$((ITENS_OK + 1)) ;;
    *)      echo "FALHOU $1 (nao contem '$3')"; ITENS_FALHOU=$((ITENS_FALHOU + 1)) ;;
  esac
}

# ---------------------------------------------------------------------------------------
# Guardas e ciclo de vida do container descartavel
# ---------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
[ -f "$MIGRATION" ] || { echo "FALHOU migration ausente: $MIGRATION"; exit 2; }
[ -f "$SCOUT_PY" ] || { echo "FALHOU agente ausente: $SCOUT_PY"; exit 2; }
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"
  exit 2
fi
mkdir -p "$TRABALHO"

limpar() {
  local rc=$?
  if [ "$MANTER" -eq 0 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1
    rm -rf "$TRABALHO"
  else
    echo "== mantido: container $CONTAINER e diretorio $TRABALHO"
  fi
  return $rc
}
trap limpar EXIT

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@"; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
contagem() { "${PSQL[@]}" -c "$1" | tr -d '[:space:]'; }

subir_container() {
  docker run -d --name "$CONTAINER" \
    -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
    "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
  # O `pg_isready` mente no inicio (servidor temporario da inicializacao): espera SELECT 1
  # funcionar DUAS vezes, com intervalo — licao medida na W1.
  local tentativa repetiu=0
  for tentativa in $(seq 1 60); do
    if "${PSQL[@]}" -c "SELECT 1" >/dev/null 2>&1; then
      repetiu=$((repetiu + 1))
      [ "$repetiu" -ge 2 ] && return 0
      sleep 2
    else
      repetiu=0
      sleep 1
    fi
  done
  echo "FALHOU o container nao ficou pronto"; exit 2
}

preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
  psql_stdin >/dev/null <<'SQL'
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, cnpj, domain, status, source, created_at, updated_at)
VALUES
  ('aaaaaaaa-0000-4000-8000-000000000001', 'Metalurgica Vale Forte Ltda', 'Vale Forte',
   '11222333000181', 'valeforte.com.br', 'DISCOVERED', 'LINKEDIN', now(), now()),
  ('aaaaaaaa-0000-4000-8000-000000000002', 'AgroSmart Analytics Ltda', 'AgroSmart Analytics',
   NULL, 'agrosmart-analytics.com.br', 'DISCOVERED', 'WEB', now(), now());
SQL
}

escrever_fonte() { # <arquivo>
  cat > "$1" <<'JSONL'
{"legal_name":"Nova Alpha Servicos Ltda","domain":"nova-alpha.com.br","cnpj":"45.723.174/0001-10","city":"Osasco","state":"SP","source":"LINKEDIN","employee_count":210,"evidence":{"url":"https://www.linkedin.com/company/nova-alpha"}}
{"trade_name":"Nova Beta Tecnologia","domain":"https://www.nova-beta.com.br/sobre","city":"Recife","state":"PE","source":"WEB","employee_count":85}
{"legal_name":"Clinica Sao Lucas S.A.","linkedin_url":"https://br.linkedin.com/company/clinica-sao-lucas/","city":"Curitiba","state":"PR","source":"EVENTOS"}
{"trade_name":"Vale Forte (mesma empresa da base)","cnpj":"11222333000181","source":"WEB"}
{"trade_name":"AgroSmart (mesma empresa da base)","domain":"agrosmart-analytics.com.br","source":"WEB"}
{"trade_name":"Conflito de Identidade Ltda","cnpj":"11.222.333/0001-81","domain":"agrosmart-analytics.com.br","source":"WEB"}
{"trade_name":"Empresa Sem Identificador Forte","city":"Santos","state":"SP","source":"EVENTOS"}
{"legal_name":"Empresa So Com CNPJ Invalido","cnpj":"11.222.333/0001-00","source":"WEB"}
{"legal_name":"","trade_name":"","domain":"sem-nome.com.br","source":"WEB"}
{"trade_name":"Fonte Fora Do Vocabulario","domain":"fonte-estranha.com.br","source":"PANFLETO"}
JSONL
}

rodar_scout() { # <relatorio> <args...>
  local relatorio="$1"; shift
  python3 "$SCOUT_PY" --raiz "$RAIZ" --relatorio "$relatorio" "$@"
}

veredito_do_relatorio() { # <relatorio> <veredito>
  python3 - "$1" "$2" <<'PY'
import json, sys
relatorio = json.load(open(sys.argv[1], encoding="utf-8"))
print(relatorio.get("por_veredito", {}).get(sys.argv[2], 0))
PY
}

CORR_R1="cccccccc-0000-4000-8000-000000000001"
CORR_R2="cccccccc-0000-4000-8000-000000000002"
CORR_R3="cccccccc-0000-4000-8000-000000000003"
CORR_R4="cccccccc-0000-4000-8000-000000000004"
FONTE="$TRABALHO/candidatas.jsonl"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== aceite do codigo sob teste: $SCOUT_PY ($rotulo)"
  preparar_banco
  escrever_fonte "$FONTE"

  # ---- rodada 1 --------------------------------------------------------------------
  rodar_scout "$TRABALHO/r1.json" --ambiente dev --correlation-id "$CORR_R1" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r1.out" 2>&1
  item "rodada1-exit-0" "0" "$?"
  item "rodada1-organizacoes-total" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "rodada1-criadas" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='scout' AND correlation_id='$CORR_R1' AND output->>'veredito'='CRIADA';")"
  item "rodada1-criadas-com-status-discovered" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id IN (SELECT (output->>'organization_id')::uuid FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND output->>'veredito'='CRIADA') AND status='DISCOVERED';")"
  item "rodada1-criadas-com-fonte-e-pais" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id IN (SELECT (output->>'organization_id')::uuid FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND output->>'veredito'='CRIADA') AND source IS NOT NULL AND country_code='BR';")"
  item "rodada1-uuid-v4-no-produtor" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id IN (SELECT (output->>'organization_id')::uuid FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND output->>'veredito'='CRIADA') AND id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$';")"
  item "rodada1-sync-events-success" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='INSERT' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_R1';")"
  item "rodada1-agent-runs-por-candidata" "10" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='scout' AND agent_role='discovery' AND correlation_id='$CORR_R1';")"
  item "rodada1-agent-runs-completed" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='COMPLETED';")"
  item "rodada1-fila-humana-pendente" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='SCOUT_IDENTITY_REVIEW' AND status='PENDING';")"
  item "rodada1-relatorio-conflito-e-casamento" "CRIADA=3 JA_EXISTE=2 REVISAO_IDENTIDADE=2 RECUSADA=3" \
    "CRIADA=$(veredito_do_relatorio "$TRABALHO/r1.json" CRIADA) JA_EXISTE=$(veredito_do_relatorio "$TRABALHO/r1.json" JA_EXISTE) REVISAO_IDENTIDADE=$(veredito_do_relatorio "$TRABALHO/r1.json" REVISAO_IDENTIDADE) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r1.json" RECUSADA)"
  item "rodada1-nenhuma-outra-tabela-escrita" "0" "$(contagem "SELECT (SELECT count(*) FROM sales_intelligence.contacts) + (SELECT count(*) FROM sales_intelligence.signals) + (SELECT count(*) FROM sales_intelligence.research_runs) + (SELECT count(*) FROM sales_intelligence.pain_hypotheses) + (SELECT count(*) FROM sales_intelligence.scores) + (SELECT count(*) FROM sales_intelligence.interactions) + (SELECT count(*) FROM sales_intelligence.recommendations) + (SELECT count(*) FROM sales_intelligence.outbox_events);")"

  # ---- rodada 2 (mesma fonte: retry nao cria duplicata) ----------------------------
  rodar_scout "$TRABALHO/r2.json" --ambiente dev --correlation-id "$CORR_R2" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r2.out" 2>&1
  item "rodada2-exit-0" "0" "$?"
  item "rodada2-nao-duplica" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "rodada2-criadas-zero" "0" "$(veredito_do_relatorio "$TRABALHO/r2.json" CRIADA)"
  item "rodada2-ja-existe-cinco" "5" "$(veredito_do_relatorio "$TRABALHO/r2.json" JA_EXISTE)"
  item "rodada2-sync-events-zero" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R2';")"
  item "rodada2-uma-unica-nova-alpha" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE domain='nova-alpha.com.br';")"

  # ---- guarda de ambiente (ADR-005) e modo sem banco ------------------------------
  rodar_scout "$TRABALHO/r3.json" --ambiente prod --correlation-id "$CORR_R3" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r3.out" 2>&1
  item "prod-recusado-exit-4" "4" "$?"
  item "prod-nao-escreveu-organizacao" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "prod-nao-registrou-execucao" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R3';")"
  python3 "$SCOUT_PY" --raiz "$RAIZ" --planejar --fonte "$FONTE" \
    --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" \
    > "$TRABALHO/r4.out" 2>&1
  item "planejar-exit-0-sem-conectar" "0" "$?"
  item "planejar-nao-escreveu" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"

  # ---- desfazer -------------------------------------------------------------------
  rodar_scout "$TRABALHO/dry.json" --desfazer "$CORR_R1" --ambiente dev \
    --prefixo "$PREFIXO" > "$TRABALHO/dry.out" 2>&1
  item "desfazer-dry-run-exit-0" "0" "$?"
  item "desfazer-dry-run-nao-apagou" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  rodar_scout "$TRABALHO/del.json" --desfazer "$CORR_R1" --ambiente dev --confirmo \
    --prefixo "$PREFIXO" > "$TRABALHO/del.out" 2>&1
  item "desfazer-confirmo-exit-0" "0" "$?"
  item "desfazer-apagou-so-a-rodada" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "desfazer-preservou-a-base" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id IN ('aaaaaaaa-0000-4000-8000-000000000001','aaaaaaaa-0000-4000-8000-000000000002');")"
  item "desfazer-registrou-rollback" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='ROLLBACK';")"
  item "desfazer-preservou-auditoria" "10" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1';")"

  echo "RESULTADO: ACEITE_SCOUT_001_$( [ "$ITENS_FALHOU" -eq 0 ] && echo OK || echo FALHOU ) ($((ITENS_OK + ITENS_FALHOU)) itens, $ITENS_FALHOU falhas)"
  [ "$ITENS_FALHOU" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
# Prova de dente: mutacao em copia do agente TEM de reprovar o aceite
# ---------------------------------------------------------------------------------------
aplicar_mutacao() { # <origem> <destino> <alvo> <substituto>
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import pathlib, sys
origem, destino, alvo, substituto = sys.argv[1:5]
texto = pathlib.Path(origem).read_text(encoding="utf-8")
ocorrencias = texto.count(alvo)
if ocorrencias != 1:
    print("MUTACAO_NAO_APLICAVEL (%d ocorrencias da ancora)" % ocorrencias)
    sys.exit(3)
pathlib.Path(destino).write_text(texto.replace(alvo, substituto), encoding="utf-8")
print("MUTACAO_APLICADA")
PY
}

prova_de_dente() {
  echo
  echo "=== PROVA DE DENTE (baseline verde ANTES das mutacoes) ==="
  local baseline_ok=0
  if rodar_aceite "baseline-do-dente" > "$TRABALHO/dente-baseline.out" 2>&1; then baseline_ok=1; fi
  if [ "$baseline_ok" -eq 1 ]; then
    echo "OK     baseline verde antes das mutacoes"
  else
    echo "FALHOU baseline NAO ficou verde — mutacao nao prova nada sem baseline"
    tail -5 "$TRABALHO/dente-baseline.out"
    return 1
  fi

  local falhas=0
  local mutacoes
  mutacoes=$(cat <<'EOF'
sem-idempotencia|ON CONFLICT (idempotency_key) DO NOTHING|
sem-forte-tambem-cria|        return VER_REVISAO, "SEM_IDENTIFICADOR_FORTE"|        return VER_CRIADA, None
prod-deixa-de-ser-recusado|        if self.ambiente == AMBIENTE_RECUSADO:|        if False:
EOF
)
  local linha nome alvo substituto destino
  while IFS='|' read -r nome alvo substituto; do
    [ -z "$nome" ] && continue
    destino="$TRABALHO/mut-$nome/scout.py"
    mkdir -p "$(dirname "$destino")"
    if ! aplicar_mutacao "$SCOUT_PY" "$destino" "$alvo" "$substituto" | grep -q MUTACAO_APLICADA; then
      echo "FALHOU mutacao $nome NAO se aplicou (ancora mudou) — buraco de verificacao"
      falhas=$((falhas + 1)); continue
    fi
    echo
    echo "-- mutacao: $nome"
    local guardado="$SCOUT_PY"
    SCOUT_PY="$destino"
    if rodar_aceite "mutacao $nome" > "$TRABALHO/mut-$nome.out" 2>&1; then
      echo "FALHOU mutacao $nome NAO foi detectada pelo aceite"
      falhas=$((falhas + 1))
    else
      echo "OK     mutacao $nome detectada: $(grep -c '^FALHOU' "$TRABALHO/mut-$nome.out") item(ns) reprovado(s) — $(grep '^FALHOU' "$TRABALHO/mut-$nome.out" | head -3 | cut -d' ' -f2 | tr '\n' ' ')"
    fi
    SCOUT_PY="$guardado"
  done <<< "$mutacoes"

  echo
  if [ "$falhas" -eq 0 ]; then
    echo "DENTE OK (3/3 mutacoes detectadas)"
  else
    echo "DENTE FALHOU ($falhas mutacao(oes) nao detectada(s))"
  fi
  [ "$falhas" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
principal() {
  echo "== ACEITE DO AGENTE SCOUT v1 (TRE-W4-E01-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0
  if rodar_aceite "principal"; then ok=1; fi
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_SCOUT_001_OK"
    return 0
  fi
  echo "ACEITE_SCOUT_001_FALHOU"
  return 1
}

principal
