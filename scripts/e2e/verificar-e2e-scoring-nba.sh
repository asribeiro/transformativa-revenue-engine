#!/usr/bin/env bash
# verificar-e2e-scoring-nba.sh [--prova-de-dente] [--manter] [--raiz <dir>]
#
# ACEITE E2E DA CADEIA W5 — Scoring + Next Best Action (card TRE-W5-E08-T01) — PostgreSQL descartavel.
#
# Roda onde existe daemon Docker (a VPS do ambiente: o container do Hermes nao tem) e NUNCA toca
# `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio (`pg-w5-acc`),
# aplica a migration 0001, roda a CADEIA inteira e, no fim, remove o container e o diretorio de
# trabalho. Se o container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (a cadeia W5 amarrada ponta a ponta):
#   0. as sete suites/verificadores OFFLINE no MESMO commit (contrato quebrado reprova antes do banco);
#   1. ICP (W5-E01) -> 2. AUTOMATION_FIT (W5-E02) -> 3. BUYING_SIGNAL (W5-E03) ->
#      4. DATA_QUALITY (W5-E04) -> 5. PRIORITY (W5-E05) -> 6. TIER (W5-E06) -> 7. NBA (W5-E07);
#   8. COMPOSICAO: o artefato de cada etapa e o insumo da seguinte, medido no banco — o PRIORITY e a
#      formula do contrato sobre os QUATRO scores gravados, o registro TIER cita o PRIORITY lido, a
#      recomendacao cita o TIER gravado pelo tiering;
#   9. FAIL-CLOSED na cadeia: a empresa SEM LASTRO nao ganha score/tier/recomendacao — cada etapa do
#      caminho curto RECUSA com motivo nominal (SEM_LASTRO / SEM_LASTRO_COMPLETO / SEM_PRIORITY /
#      SEM_TIER) e nada e gravado;
#  10. ESCOPO: a cadeia so escreve onde pode; as tabelas de negocio de entrada ficam intactas, a
#      outbox fica vazia (evento e W3/W6) e nenhuma rodada chama LLM (model/tokens/custo NULL);
#  11. REPLAY: repetir a cadeia com as MESMAS entradas nao duplica linha nenhuma;
#  12. GUARDAS: `prod` recusado (exit 4) sem escrita nos sete componentes; `--planejar`/`--regras`
#      exit 0 SEM conexao; `--desfazer` e dry-run ate o `--confirmo`;
#  13. veredito ..... ACEITE_E2E_SCORING_NBA_001_OK / ACEITE_E2E_SCORING_NBA_001_FALHOU
#
# --prova-de-dente: muta COPIA de modulo e exige que o aceite reprove O ITEM ESPERADO de cada
#   mutacao (nao basta "o aceite falhou").
#
# Variaveis: TRE_W5_RAIZ, TRE_W5_IMAGEM (default postgres:16), TRE_W5_CONTAINER, TRE_W5_TRABALHO,
#            TRE_W5_PULAR_SUITES=1 (pula as suites offline), e um caminho de codigo por componente
#            (TRE_W5_ICP_PY, TRE_W5_AF_PY, TRE_W5_BS_PY, TRE_W5_DQ_PY, TRE_W5_PR_PY, TRE_W5_TIER_PY,
#            TRE_W5_NBA_PY) — sao elas que a prova de dente usa para apontar o aceite para as copias
#            mutadas.
# Exit: 0 = ACEITE_E2E_SCORING_NBA_001_OK · 1 = FALHOU · 2 = uso/guarda.
#
# LIMITES DECLARADOS (medidos, nao escondidos):
#   - o CNPJ da COLUNA precisa estar normalizado (so digitos) para a resolucao por CNPJ achar a
#     empresa: fonte com "11.222.333/0001-81" casa a coluna "11222333000181", nao a pontuada
#     (medido no probe de 02/10/2026). Por isso a massa grava o CNPJ em digitos e a fonte usa
#     digitos; DOMAIN e o identificador forte usado nas outras duas empresas;
#   - DATA_QUALITY --planejar exige a porta do banco (recusa sem ela); o item mede a recusa e a
#     ausencia de escrita;
#   - o tier NAO e persistido como score: `sync_events` operation='TIER' e a trilha auditada (o
#     contrato lista cinco score_type e criar o sexto e decisao do dono — lacuna do W5-E06).
set -uo pipefail

RAIZ="${TRE_W5_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_W5_IMAGEM:-postgres:16}"
CONTAINER="${TRE_W5_CONTAINER:-pg-w5-acc}"
TRABALHO="${TRE_W5_TRABALHO:-/tmp/w5-scoring-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="w5-aceite-descartavel"
ICP_PY="${TRE_W5_ICP_PY:-$RAIZ/hermes/agents/icp_score/icp_score.py}"
AF_PY="${TRE_W5_AF_PY:-$RAIZ/hermes/agents/automation_fit/automation_fit.py}"
BS_PY="${TRE_W5_BS_PY:-$RAIZ/hermes/agents/buying_signal/buying_signal_score.py}"
DQ_PY="${TRE_W5_DQ_PY:-$RAIZ/hermes/scores/data_quality/data_quality.py}"
PR_PY="${TRE_W5_PR_PY:-$RAIZ/hermes/scores/priority/priority_score.py}"
TIER_PY="${TRE_W5_TIER_PY:-$RAIZ/hermes/scores/tiering/tiering.py}"
NBA_PY="${TRE_W5_NBA_PY:-$RAIZ/hermes/scores/nba/nba.py}"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
DENTE=0
MANTER=0

# (re)calcula os caminhos padrao a partir de $RAIZ SEM sobrescrever o que veio por variavel de
# ambiente — e por aqui que a prova de dente aponta o aceite para as copias mutadas.
definir_modulos() {
  [ -n "${TRE_W5_ICP_PY:-}" ]  || ICP_PY="$RAIZ/hermes/agents/icp_score/icp_score.py"
  [ -n "${TRE_W5_AF_PY:-}" ]   || AF_PY="$RAIZ/hermes/agents/automation_fit/automation_fit.py"
  [ -n "${TRE_W5_BS_PY:-}" ]   || BS_PY="$RAIZ/hermes/agents/buying_signal/buying_signal_score.py"
  [ -n "${TRE_W5_DQ_PY:-}" ]   || DQ_PY="$RAIZ/hermes/scores/data_quality/data_quality.py"
  [ -n "${TRE_W5_PR_PY:-}" ]   || PR_PY="$RAIZ/hermes/scores/priority/priority_score.py"
  [ -n "${TRE_W5_TIER_PY:-}" ] || TIER_PY="$RAIZ/hermes/scores/tiering/tiering.py"
  [ -n "${TRE_W5_NBA_PY:-}" ]  || NBA_PY="$RAIZ/hermes/scores/nba/nba.py"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"; definir_modulos ;;
    --raiz=*) RAIZ="${1#--raiz=}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"; definir_modulos ;;
    *) echo "uso: $0 [--prova-de-dente] [--manter] [--raiz <dir>]"; exit 2 ;;
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

command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
[ -f "$MIGRATION" ] || { echo "FALHOU migration ausente: $MIGRATION"; exit 2; }
for par in "ICP:$ICP_PY" "AF:$AF_PY" "BS:$BS_PY" "DQ:$DQ_PY" "PR:$PR_PY" "TIER:$TIER_PY" "NBA:$NBA_PY"; do
  [ -f "${par#*:}" ] || { echo "FALHOU modulo ${par%%:*} ausente: ${par#*:}"; exit 2; }
done
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
mkdir -p "$TRABALHO"

limpar() {
  local rc=$?
  if [ "$MANTER" -eq 0 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1
    rm -rf "$TRABALHO"
  else
    echo "== --manter: container $CONTAINER e $TRABALHO ficaram de pe"
  fi
  exit $rc
}
trap limpar EXIT

# ---------------------------------------------------------------------------------------
# 0. Suites offline dos SETE componentes (mesmo commit) — sem banco nenhum
# ---------------------------------------------------------------------------------------
if [ "${TRE_W5_PULAR_SUITES:-0}" != "1" ]; then
  echo "== 0. suites offline dos sete componentes (mesmo commit)"
  for par in "ICP:scripts/agentes/verificar_agente_icp_score.py" \
             "AUTOMATION_FIT:scripts/agentes/verificar_agente_automation_fit.py" \
             "BUYING_SIGNAL:scripts/agentes/verificar_buying_signal_score.py" \
             "DATA_QUALITY:scripts/scores/verificar_score_data_quality.py" \
             "PRIORITY:scripts/scores/verificar_score_priority.py" \
             "TIER:scripts/scores/verificar_score_tiering.py" \
             "NBA:scripts/scores/verificar_nba.py"; do
    comp="${par%%:*}"; script="$RAIZ/${par#*:}"
    if [ ! -f "$script" ]; then
      item "S0 suite offline $comp existe" 1 0; continue
    fi
    ( cd "$RAIZ" && python3 "$script" > "$TRABALHO/suite_$comp.out" 2>&1 )
    item "S0 suite offline $comp exit 0" 0 "$?"
  done
fi

# ---------------------------------------------------------------------------------------
# 1. Container descartavel + migration + massa da cadeia
# ---------------------------------------------------------------------------------------
echo "== 1. banco descartavel e massa da cadeia"
docker run -d --name "$CONTAINER" \
  -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
  "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
conta() { psql_t -c "$1" | head -1; }
# tem() <texto> <arquivo>: 1 se o texto aparece no arquivo, 0 se nao. O motivo aparece DUAS vezes
# na saida do modulo (linha e relatorio JSON) — por isso "quanto aparece" nunca e o item; o item e
# "aparece ou nao aparece".
tem() { grep -qF "$1" "$2" && echo 1 || echo 0; }

for _ in $(seq 1 60); do
  psql_t -c "SELECT 1;" >/dev/null 2>&1 && psql_t -c "SELECT 1;" >/dev/null 2>&1 && break
  sleep 1
done
psql_t -c "SELECT 1;" >/dev/null 2>&1 || { echo "FALHOU o container $CONTAINER nao respondeu"; exit 2; }
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

# A: fit alto, decisor com e-mail, nenhuma interacao -> deve chegar ao fim da cadeia
# B: fit medio, abordada 5 dias atras sem resposta -> cadeia completa, outro proximo passo
# C: SEM LASTRO (sem sinal, sem hipotese) -> a cadeia RECUSA e nao inventa score/tier/recomendacao
ORG_A="a0000000-0000-4000-8000-000000000001"
ORG_B="b0000000-0000-4000-8000-000000000002"
ORG_C="c0000000-0000-4000-8000-000000000003"
CT_A="a0000000-0000-4000-8000-0000000000c1"
CT_B="b0000000-0000-4000-8000-0000000000c2"
CNPJ_A="11222333000181"
DOM_A="valeforte.com.br"
DOM_B="rotacerta.com.br"
DOM_C="paoquente.com.br"

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, domain, website_url, cnpj, industry_name, industry_code,
   employee_count, unit_count, city, state, business_model, status, source, created_at)
VALUES
  ('$ORG_A', 'Vale Forte Distribuidora LTDA', 'Vale Forte', '$DOM_A', 'https://$DOM_A', '$CNPJ_A',
   'Distribuidora de materiais de construcao', 'G46', 420, 3, 'Sao Paulo', 'SP', 'B2B',
   'Pesquisado', 'E2E_W5', NOW() - INTERVAL '3 days'),
  ('$ORG_B', 'Rota Certa Transportes LTDA', 'Rota Certa', '$DOM_B', 'https://$DOM_B', NULL,
   'Logistica e transporte de cargas', 'H49', 900, 5, 'Curitiba', 'PR', 'B2B',
   'Abordagem iniciada', 'E2E_W5', NOW() - INTERVAL '3 days'),
  ('$ORG_C', 'Pao Quente Comercio ME', 'Pao Quente', '$DOM_C', 'https://$DOM_C', NULL,
   'Comercio varejista de alimentos', 'G47', 30, 1, 'Santos', 'SP', 'B2C',
   'Pesquisado', 'E2E_W5', NOW() - INTERVAL '3 days');

-- pesquisa concluida (insumo do DATA_QUALITY e do NBA), hipotese com impacto medido (insumo do
-- AUTOMATION_FIT), sinais com categoria e tipo do vocabulario do contrato (insumo do BUYING_SIGNAL)
INSERT INTO sales_intelligence.research_runs
  (id, organization_id, agent_name, agent_version, research_type, status, source_count,
   completed_at, created_at)
VALUES
  (gen_random_uuid(), '$ORG_A', 'research', '1.0.0', 'DIGITAL_PRESENCE', 'COMPLETED', 4,
   NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days'),
  (gen_random_uuid(), '$ORG_B', 'research', '1.0.0', 'DIGITAL_PRESENCE', 'COMPLETED', 3,
   NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days'),
  (gen_random_uuid(), '$ORG_C', 'research', '1.0.0', 'DIGITAL_PRESENCE', 'COMPLETED', 1,
   NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days');

INSERT INTO sales_intelligence.pain_hypotheses
  (id, organization_id, pain_category, pain_statement, business_impact_score, status, created_at)
VALUES
  (gen_random_uuid(), '$ORG_A', 'Eficiencia operacional',
   'Conferencia manual de pedidos e estoque', 85.00, 'VALIDATED', NOW() - INTERVAL '2 days'),
  (gen_random_uuid(), '$ORG_B', 'Eficiencia operacional',
   'Roteirizacao manual de cargas', 60.00, 'VALIDATED', NOW() - INTERVAL '2 days');

INSERT INTO sales_intelligence.signals
  (id, organization_id, signal_type, signal_category, title, event_date, detected_at, confidence)
VALUES
  (gen_random_uuid(), '$ORG_A', 'ERP_CHANGE', 'TECNOLOGIA',
   'Troca de ERP anunciada', NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days', 0.9000),
  (gen_random_uuid(), '$ORG_A', 'EFFICIENCY_PROGRAM', 'EFICIENCIA',
   'Programa de eficiencia publicado', NOW() - INTERVAL '10 days', NOW() - INTERVAL '10 days', 0.8000),
  (gen_random_uuid(), '$ORG_B', 'HIRING', 'CORPORATIVO',
   'Vaga de coordenador de operacoes', NOW() - INTERVAL '5 days', NOW() - INTERVAL '5 days', 0.7000);

INSERT INTO sales_intelligence.contacts
  (id, organization_id, full_name, decision_role, email, preferred_channel, do_not_contact,
   opt_out_email, created_at)
VALUES
  ('$CT_A', '$ORG_A', 'Contato Decisor A', 'Economic Buyer', 'cfo@$DOM_A', 'EMAIL', false, false,
   NOW() - INTERVAL '2 days'),
  ('$CT_B', '$ORG_B', 'Contato Decisor B', 'Decision Maker', 'coo@$DOM_B', 'EMAIL', false, false,
   NOW() - INTERVAL '2 days');

INSERT INTO sales_intelligence.interactions
  (id, organization_id, channel, direction, interaction_type, occurred_at, created_at)
VALUES
  (gen_random_uuid(), '$ORG_B', 'EMAIL', 'OUTBOUND', 'E2E_W5', NOW() - INTERVAL '5 days', NOW());
SQL

fontes() {
  : > "$TRABALHO/fonte-icp.jsonl"
  printf '{"organization_id": "%s"}\n' "$ORG_A" "$ORG_B" "$ORG_C" >> "$TRABALHO/fonte-icp.jsonl"
  : > "$TRABALHO/fonte-af.jsonl"
  printf '{"organizacao": {"cnpj": "%s"}}\n' "$CNPJ_A" >> "$TRABALHO/fonte-af.jsonl"
  printf '{"organizacao": {"domain": "%s"}}\n' "$DOM_B" >> "$TRABALHO/fonte-af.jsonl"
  printf '{"organizacao": {"domain": "%s"}}\n' "$DOM_C" >> "$TRABALHO/fonte-af.jsonl"
}
fontes

CID_ICP="1a000000-0000-4000-8000-000000000001"
CID_AF="1a000000-0000-4000-8000-000000000002"
CID_BS="1a000000-0000-4000-8000-000000000003"
CID_DQ="1a000000-0000-4000-8000-000000000004"
CID_PR="1a000000-0000-4000-8000-000000000005"
CID_TIER="1a000000-0000-4000-8000-000000000006"
CID_NBA="1a000000-0000-4000-8000-000000000007"

# ---------------------------------------------------------------------------------------
# 2. Fotos do estado ANTES da cadeia (as tabelas de NEGOCIO nao podem ser tocadas)
# ---------------------------------------------------------------------------------------
foto_negocio() {
  conta "SELECT md5(string_agg(
      (SELECT count(*) FROM sales_intelligence.organizations) || '|' ||
      (SELECT count(*) FROM sales_intelligence.contacts) || '|' ||
      (SELECT count(*) FROM sales_intelligence.interactions) || '|' ||
      (SELECT count(*) FROM sales_intelligence.signals) || '|' ||
      (SELECT count(*) FROM sales_intelligence.pain_hypotheses) || '|' ||
      (SELECT count(*) FROM sales_intelligence.research_runs) || '|' ||
      (SELECT md5(string_agg(legal_name || COALESCE(trade_name,''), ',' ORDER BY id))
         FROM sales_intelligence.organizations), ''));"
}
NEGOCIO_ANTES="$(foto_negocio)"
ORGS_ANTES=$(conta "SELECT count(*) FROM sales_intelligence.organizations;")

# ---------------------------------------------------------------------------------------
# 3. A CADEIA — cada etapa roda o modulo REAL, em sequencia, no MESMO banco
# ---------------------------------------------------------------------------------------
rodar() { # <py> <cid> <relatorio> <args...>
  local py="$1" cid="$2" rel="$3"; shift 3
  ( cd "$RAIZ" && python3 "$py" --ambiente dev --correlation-id "$cid" \
      --prefixo "$PREFIXO" --raiz "$RAIZ" --relatorio "$rel" "$@" ) 2>&1
}

echo "== 2. cadeia: ICP -> AUTOMATION_FIT -> BUYING_SIGNAL -> DATA_QUALITY"
S_ICP=$(rodar "$ICP_PY" "$CID_ICP" "$TRABALHO/icp.json" --fonte "$TRABALHO/fonte-icp.jsonl"); R_ICP=$?
echo "$S_ICP" | sed 's/^/  /' | head -6
item "2.1 ICP exit 0" 0 "$R_ICP"
item "2.1 ICP gravou ICP para as TRES empresas" 3 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='ICP';")"
item "2.1 ICP com score_version do modelo" 3 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='ICP' AND score_version <> '';")"

S_AF=$(rodar "$AF_PY" "$CID_AF" "$TRABALHO/af.json" --fonte "$TRABALHO/fonte-af.jsonl"); R_AF=$?
echo "$S_AF" | sed 's/^/  /' | head -8
item "2.2 AUTOMATION_FIT exit 0" 0 "$R_AF"
item "2.2 AUTOMATION_FIT gravou para as empresas COM lastro" 2 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='AUTOMATION_FIT';")"
item "2.2 AUTOMATION_FIT RECUSOU a empresa SEM LASTRO (SEM_LASTRO)" 1 \
  "$(tem 'SEM_LASTRO' "$TRABALHO/af.json")"
item "2.2 AUTOMATION_FIT publicou a cobertura" 1 \
  "$(conta "SELECT CASE WHEN count(*) > 0 THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE score_type='AUTOMATION_FIT' AND explanation::text LIKE '%cobertura%';")"
item "2.2 AUTOMATION_FIT leu os sinais e a hipotese do banco" 1 \
  "$(conta "SELECT CASE WHEN count(*) > 0 THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE score_type='AUTOMATION_FIT' AND inputs::text LIKE '%\"sinais\"%';")"

for org in "$ORG_A" "$ORG_B" "$ORG_C"; do
  S_BS=$(rodar "$BS_PY" "$CID_BS" "$TRABALHO/bs-$org.json" --organizacao "$org"); R=$?
  item "2.3 BUYING_SIGNAL exit 0 ($org)" 0 "$R"
done
item "2.3 BUYING_SIGNAL gravou as TRES" 3 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='BUYING_SIGNAL';")"
item "2.3 empresa SEM sinal tem BUYING_SIGNAL 0.00 (ausencia e informacao)" "0.00" \
  "$(conta "SELECT score_value FROM sales_intelligence.scores WHERE score_type='BUYING_SIGNAL' AND organization_id='$ORG_C';")"
item "2.3 empresa COM sinal forte tem BUYING_SIGNAL > 0" 1 \
  "$(conta "SELECT CASE WHEN score_value > 0 THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE score_type='BUYING_SIGNAL' AND organization_id='$ORG_A';")"

for org in "$ORG_A" "$ORG_B" "$ORG_C"; do
  S_DQ=$(rodar "$DQ_PY" "$CID_DQ" "$TRABALHO/dq-$org.json" --organizacao "$org"); R=$?
  item "2.4 DATA_QUALITY exit 0 ($org)" 0 "$R"
done
item "2.4 DATA_QUALITY gravou as TRES" 3 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='DATA_QUALITY';")"
item "2.4 DATA_QUALITY espelhou o valor na organizacao" 3 \
  "$(conta "SELECT count(*) FROM sales_intelligence.organizations WHERE data_quality_score IS NOT NULL;")"

echo "== 3. cadeia: PRIORITY -> TIER -> NBA"
S_PR=$(rodar "$PR_PY" "$CID_PR" "$TRABALHO/pr.json" --fonte "$TRABALHO/fonte-icp.jsonl"); R_PR=$?
echo "$S_PR" | sed 's/^/  /' | head -8
item "3.1 PRIORITY exit 0" 0 "$R_PR"
item "3.1 PRIORITY gravou para as empresas COM os quatro componentes" 2 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='PRIORITY';")"
item "3.1 PRIORITY RECUSOU a empresa sem os quatro (SEM_LASTRO_COMPLETO)" 1 \
  "$(tem 'SEM_LASTRO_COMPLETO' "$TRABALHO/pr.json")"
item "3.1 PRIORITY tem validade (politica de 30 dias nasceu no E05)" 2 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='PRIORITY' AND valid_until IS NOT NULL;")"

# COMPOSICAO 1: o PRIORITY e a formula do contrato sobre os QUATRO scores GRAVADOS (nao sobre fixture)
FORMULA_OK=$(conta "SELECT (
    SELECT score_value FROM sales_intelligence.scores
     WHERE organization_id='$ORG_A' AND score_type='PRIORITY'
     ORDER BY calculated_at DESC, id DESC LIMIT 1)
  = (round(
      0.35 * (SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='ICP' ORDER BY calculated_at DESC, id DESC LIMIT 1) +
      0.30 * (SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='AUTOMATION_FIT' ORDER BY calculated_at DESC, id DESC LIMIT 1) +
      0.25 * (SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='BUYING_SIGNAL' ORDER BY calculated_at DESC, id DESC LIMIT 1) +
      0.10 * (SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='DATA_QUALITY' ORDER BY calculated_at DESC, id DESC LIMIT 1), 2))::numeric(5,2);")
item "3.1 COMPOSICAO: PRIORITY = formula do contrato sobre os 4 scores gravados" t "$FORMULA_OK"

S_TIER=$(rodar "$TIER_PY" "$CID_TIER" "$TRABALHO/tier.json" --fonte "$TRABALHO/fonte-icp.jsonl"); R_TIER=$?
echo "$S_TIER" | sed 's/^/  /' | head -8
item "3.2 TIER exit 0" 0 "$R_TIER"
item "3.2 TIER registrou as empresas com PRIORITY" 2 \
  "$(conta "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='TIER';")"
item "3.2 TIER nao criou score_type nenhum (tier nao e score)" 0 \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type NOT IN ('ICP','AUTOMATION_FIT','BUYING_SIGNAL','DATA_QUALITY','PRIORITY');")"
item "3.2 TIER RECUSOU a empresa SEM PRIORITY (SEM_PRIORITY)" 1 \
  "$(tem 'SEM_PRIORITY' "$TRABALHO/tier.json")"
# COMPOSICAO 2: o registro TIER cita o PRIORITY lido do banco (identidade do score, nao a rodada)
TIER_CITA_PRIORITY=$(conta "SELECT (
    SELECT (e.request_payload->'score_lido'->>'score_id') = (
             SELECT s.id::text FROM sales_intelligence.scores s
              WHERE s.organization_id = e.entity_id AND s.score_type='PRIORITY'
              ORDER BY s.calculated_at DESC, s.id DESC LIMIT 1)
      FROM sales_intelligence.sync_events e
     WHERE e.operation='TIER' AND e.entity_id='$ORG_A');")
item "3.2 COMPOSICAO: registro TIER cita o PRIORITY lido (score_id)" t "$TIER_CITA_PRIORITY"

S_NBA=$(rodar "$NBA_PY" "$CID_NBA" "$TRABALHO/nba.json" --jsonl "$TRABALHO/fonte-icp.jsonl"); R_NBA=$?
echo "$S_NBA" | sed 's/^/  /' | head -8
item "3.3 NBA exit 0" 0 "$R_NBA"
item "3.3 NBA recomendou as empresas COM tier" 2 \
  "$(conta "SELECT count(*) FROM sales_intelligence.recommendations WHERE status='OPEN';")"
item "3.3 NBA RECUSOU a empresa SEM tier (SEM_TIER)" 1 \
  "$(tem 'SEM_TIER' "$TRABALHO/nba.json")"
item "3.3 NBA usa o vocabulario do contrato" 2 \
  "$(conta "SELECT count(*) FROM sales_intelligence.recommendations WHERE action IN ('RESEARCH_MORE','FIND_DECISION_MAKER','SEND_EMAIL','PREPARE_LINKEDIN','WAIT','FOLLOW_UP','CREATE_MEETING','NURTURE','DISQUALIFY');")"
item "3.3 NBA nao inventa confidence" 0 \
  "$(conta "SELECT count(*) FROM sales_intelligence.recommendations WHERE confidence IS NOT NULL;")"
# COMPOSICAO 3: a recomendacao cita o TIER que o tiering gravou (nao um tier proprio)
NBA_CITA_TIER=$(conta "SELECT count(*) FROM sales_intelligence.recommendations r
  JOIN sales_intelligence.sync_events e ON e.entity_id = r.organization_id AND e.operation='TIER'
  WHERE r.status='OPEN' AND r.rationale LIKE '%tier=' || COALESCE(e.request_payload->>'tier','') || '%';")
item "3.3 COMPOSICAO: recomendacao cita o TIER gravado pelo tiering" 2 "$NBA_CITA_TIER"

# ---------------------------------------------------------------------------------------
# 4. ESCOPO: nada fora do que a cadeia pode escrever; sem LLM; outbox vazia
# ---------------------------------------------------------------------------------------
echo "== 4. escopo da cadeia e estado final"
item "4.1 tabelas de negocio INTACTAS (foto identica)" "$NEGOCIO_ANTES" "$(foto_negocio)"
item "4.1 nenhuma organizacao criada pela cadeia" "$ORGS_ANTES" \
  "$(conta "SELECT count(*) FROM sales_intelligence.organizations;")"
item "4.1 nenhuma outbox (evento e W3/W6)" 0 \
  "$(conta "SELECT count(*) FROM sales_intelligence.outbox_events;")"
item "4.1 nenhuma chamada de LLM (model NULL)" 0 \
  "$(conta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE model IS NOT NULL;")"
item "4.1 nenhum custo/token inventado" 0 \
  "$(conta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE tokens_input IS NOT NULL OR tokens_output IS NOT NULL OR estimated_cost IS NOT NULL;")"
item "4.2 auditoria da cadeia gravada" 7 \
  "$(conta "SELECT count(DISTINCT agent_name) FROM sales_intelligence.agent_runs;")"
item "4.2 tres empresas atravessaram a cadeia ate onde havia lastro" "2|2|2" \
  "$(conta "SELECT (SELECT count(*) FROM sales_intelligence.scores WHERE score_type='PRIORITY') || '|' || (SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='TIER') || '|' || (SELECT count(*) FROM sales_intelligence.recommendations);")"

# ---------------------------------------------------------------------------------------
# 5. REPLAY: a cadeia inteira de novo, com as MESMAS entradas
# ---------------------------------------------------------------------------------------
echo "== 5. replay da cadeia (mesmas entradas)"
SCORES_ANTES=$(conta "SELECT count(*) FROM sales_intelligence.scores;")
TIER_ANTES=$(conta "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='TIER';")
REC_ANTES=$(conta "SELECT count(*) FROM sales_intelligence.recommendations;")
rodar "$ICP_PY" "$CID_ICP" "$TRABALHO/icp-r.json" --fonte "$TRABALHO/fonte-icp.jsonl" >/dev/null
rodar "$AF_PY" "$CID_AF" "$TRABALHO/af-r.json" --fonte "$TRABALHO/fonte-af.jsonl" >/dev/null
rodar "$BS_PY" "$CID_BS" "$TRABALHO/bs-r.json" --organizacao "$ORG_A" >/dev/null
rodar "$DQ_PY" "$CID_DQ" "$TRABALHO/dq-r.json" --organizacao "$ORG_A" >/dev/null
rodar "$PR_PY" "$CID_PR" "$TRABALHO/pr-r.json" --fonte "$TRABALHO/fonte-icp.jsonl" >/dev/null
rodar "$TIER_PY" "$CID_TIER" "$TRABALHO/tier-r.json" --fonte "$TRABALHO/fonte-icp.jsonl" >/dev/null
rodar "$NBA_PY" "$CID_NBA" "$TRABALHO/nba-r.json" --jsonl "$TRABALHO/fonte-icp.jsonl" >/dev/null
item "5.1 replay nao duplica score" "$SCORES_ANTES" \
  "$(conta "SELECT count(*) FROM sales_intelligence.scores;")"
item "5.1 replay nao duplica registro TIER" "$TIER_ANTES" \
  "$(conta "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='TIER';")"
item "5.1 replay nao duplica recomendacao" "$REC_ANTES" \
  "$(conta "SELECT count(*) FROM sales_intelligence.recommendations;")"
item "5.1 replay nao cria outbox" 0 "$(conta "SELECT count(*) FROM sales_intelligence.outbox_events;")"

# ---------------------------------------------------------------------------------------
# 6. GUARDAS: prod recusado sem escrita; --planejar/--regras sem conexao
# ---------------------------------------------------------------------------------------
echo "== 6. guardas (ADR-005 e modo declarado)"
FOTO_ANTES_PROD=$(conta "SELECT (SELECT count(*) FROM sales_intelligence.recommendations) || '|' || (SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='TIER') || '|' || (SELECT count(*) FROM sales_intelligence.scores WHERE score_type='PRIORITY') || '|' || (SELECT count(*) FROM sales_intelligence.scores);")
for par in "ICP:$ICP_PY:--fonte $TRABALHO/fonte-icp.jsonl" \
           "AUTOMATION_FIT:$AF_PY:--fonte $TRABALHO/fonte-af.jsonl" \
           "BUYING_SIGNAL:$BS_PY:--organizacao $ORG_A" \
           "DATA_QUALITY:$DQ_PY:--organizacao $ORG_A" \
           "PRIORITY:$PR_PY:--organizacao $ORG_A" \
           "TIER:$TIER_PY:--organizacao $ORG_A" \
           "NBA:$NBA_PY:--organizacao $ORG_A"; do
  comp="${par%%:*}"; resto="${par#*:}"; py="${resto%%:*}"; args="${resto#*:}"
  ( cd "$RAIZ" && python3 "$py" --ambiente prod --prefixo "$PREFIXO" --raiz "$RAIZ" $args >/dev/null 2>&1 )
  item "6.1 $comp recusa prod (exit 4)" 4 "$?"
done
item "6.1 prod nao escreveu nada" "$FOTO_ANTES_PROD" \
  "$(conta "SELECT (SELECT count(*) FROM sales_intelligence.recommendations) || '|' || (SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='TIER') || '|' || (SELECT count(*) FROM sales_intelligence.scores WHERE score_type='PRIORITY') || '|' || (SELECT count(*) FROM sales_intelligence.scores);")"

FANTASMA="docker exec -i pg-nao-existe psql -U nao -d nao"
for par in "ICP:$ICP_PY:--fonte $TRABALHO/fonte-icp.jsonl" \
           "AUTOMATION_FIT:$AF_PY:--fonte $TRABALHO/fonte-af.jsonl" \
           "BUYING_SIGNAL:$BS_PY:--organizacao $ORG_A" \
           "PRIORITY:$PR_PY:--organizacao $ORG_A" \
           "TIER:$TIER_PY:--organizacao $ORG_A"; do
  comp="${par%%:*}"; resto="${par#*:}"; py="${resto%%:*}"; args="${resto#*:}"
  ( cd "$RAIZ" && python3 "$py" --planejar --prefixo "$FANTASMA" --raiz "$RAIZ" $args >/dev/null 2>&1 )
  item "6.2 $comp --planejar exit 0 sem conexao" 0 "$?"
done
# DATA_QUALITY e' o unico componente cujo --planejar exige a PORTA do banco declarada (medido: sem
# porta ele RECUSA com "nenhuma porta de banco configurada", exit 1). O item mede o que importa:
# recusa explicita e ZERO escrita — nao se inventa um "exit 0" que o componente nao da.
FOTO_DQ=$(conta "SELECT count(*) FROM sales_intelligence.scores;")
( cd "$RAIZ" && python3 "$DQ_PY" --planejar --prefixo "$FANTASMA" --raiz "$RAIZ" --organizacao "$ORG_A" >"$TRABALHO/dq-planejar.out" 2>&1 ); RC_DQ_PLANEJAR=$?
item "6.2 DATA_QUALITY --planejar recusa sem porta (exit 1) e NAO escreve" "1|$FOTO_DQ" \
  "$RC_DQ_PLANEJAR|$(conta "SELECT count(*) FROM sales_intelligence.scores;")"
( cd "$RAIZ" && python3 "$NBA_PY" --regras --prefixo "$FANTASMA" --raiz "$RAIZ" >/dev/null 2>&1 )
item "6.2 NBA --regras exit 0 sem conexao" 0 "$?"
( cd "$RAIZ" && python3 "$NBA_PY" --planejar --prefixo "$FANTASMA" --raiz "$RAIZ" --organizacao "$ORG_A" >/dev/null 2>&1 )
item "6.2 NBA --planejar exit 0 sem conexao" 0 "$?"

# ---------------------------------------------------------------------------------------
# 7. DESFAZER: dry-run nao apaga; --confirmo apaga SO o que a rodada gravou
# ---------------------------------------------------------------------------------------
echo "== 7. desfazer do NBA (dry-run e --confirmo)"
ANTES_REC=$(conta "SELECT count(*) FROM sales_intelligence.recommendations;")
rodar "$NBA_PY" "$CID_NBA" "$TRABALHO/desf-dry.json" --desfazer "$CID_NBA" >/dev/null
item "7.1 dry-run nao apaga" "$ANTES_REC" \
  "$(conta "SELECT count(*) FROM sales_intelligence.recommendations;")"
rodar "$NBA_PY" "$CID_NBA" "$TRABALHO/desf.json" --desfazer "$CID_NBA" --confirmo >/dev/null
item "7.1 --confirmo apaga as recomendacoes da rodada" 0 \
  "$(conta "SELECT count(*) FROM sales_intelligence.recommendations WHERE status='OPEN';")"
item "7.1 auditoria da rodada preservada" 1 \
  "$(conta "SELECT CASE WHEN count(*) > 0 THEN 1 ELSE 0 END FROM sales_intelligence.agent_runs WHERE correlation_id='$CID_NBA';")"

# ---------------------------------------------------------------------------------------
# 8. Dente por vinculo: mutacoes em COPIA reprovam o ITEM ESPERADO
# ---------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== 8. prova de dente (mutacoes em copia do codigo)"
  D=$(mktemp -d "$TRABALHO/dente.XXXXXX")
  while IFS='|' read -r nome env_mod modulo de para item_esperado; do
    [ -z "$nome" ] && continue
    if ! grep -qF "$de" "$modulo"; then
      echo "FALHOU dente $nome: ancora ausente em $modulo"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); continue
    fi
    python3 - "$modulo" "$D/mut_$nome.py" "$de" "$para" <<'PY'
import sys, pathlib
origem, destino, de, para = sys.argv[1:5]
texto = pathlib.Path(origem).read_text(encoding="utf-8")
assert texto.count(de) >= 1, "ancora ausente"
pathlib.Path(destino).write_text(texto.replace(de, para, 1), encoding="utf-8")
PY
    SAIDA=$(TRE_W5_CONTAINER="pg-w5-dente-$nome" TRE_W5_TRABALHO="$D/t$nome" \
      TRE_W5_PULAR_SUITES=1 env "$env_mod=$D/mut_$nome.py" \
      bash "$0" --raiz "$RAIZ" 2>&1) </dev/null
    if echo "$SAIDA" | grep -qF "FALHOU $item_esperado " && ! echo "$SAIDA" | grep -qF "ACEITE_E2E_SCORING_NBA_001_OK"; then
      echo "OK     dente $nome: reprovou o item esperado ($item_esperado)"
      ITENS_OK=$((ITENS_OK + 1))
    else
      echo "FALHOU dente $nome: NAO reprovou o item esperado $item_esperado"
      ITENS_FALHOU=$((ITENS_FALHOU + 1))
    fi
  done <<LISTA
sem-motivo-sem-priority|TRE_W5_TIER_PY|$TIER_PY|MOTIVO_SEM_PRIORITY = "SEM_PRIORITY"|MOTIVO_SEM_PRIORITY = "MOTIVO_PROVADO"|3.2 TIER RECUSOU a empresa SEM PRIORITY (SEM_PRIORITY)
sem-motivo-sem-lastro|TRE_W5_PR_PY|$PR_PY|MOTIVO_SEM_LASTRO = "SEM_LASTRO_COMPLETO"|MOTIVO_SEM_LASTRO = "MOTIVO_PROVADO"|3.1 PRIORITY RECUSOU a empresa sem os quatro (SEM_LASTRO_COMPLETO)
sem-checagem-de-tier|TRE_W5_NBA_PY|$NBA_PY|if not fatos.get("tier"):|if False:|3.3 NBA RECUSOU a empresa SEM tier (SEM_TIER)
sem-idempotencia|TRE_W5_NBA_PY|$NBA_PY|return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NBA_VERSION}:{organization_id}:{entrada_hash}"))|return str(uuid.uuid4())|5.1 replay nao duplica recomendacao
LISTA
  rm -rf "$D"
fi

echo
echo "== ACEITE E2E SCORING/NBA $ITENS_OK OK / $ITENS_FALHOU FALHOU"
if [ "$ITENS_FALHOU" -eq 0 ]; then echo "ACEITE_E2E_SCORING_NBA_001_OK"; exit 0; fi
echo "ACEITE_E2E_SCORING_NBA_001_FALHOU"; exit 1
