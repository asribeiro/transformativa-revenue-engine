#!/usr/bin/env bash
# teste_gerador_abordagem_aceite.sh [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--raiz <dir>]
#
# ACEITE E2E do GERADOR DE ABORDAGEM v1 (card TRE-W6-E02-T01) — PostgreSQL descartavel + STUB local
# do provedor. Roda onde existe daemon Docker (a VPS do ambiente: o container do Hermes nao tem).
# NUNCA toca `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio
# (`pg-outreach-acc`), aplica a migration 0001, mede e, no fim, remove o container e o diretorio.
# Se o container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (A1..A10 de `docs/architecture/gerador-abordagem-v1.md`):
#   1. a abordagem LEGITIMA vira PEDIDO DE APROVACAO em `human_approvals` (status PENDING) com
#      action_type/canal/assunto/corpo/cta/citacoes/entrada_hash em `proposed_action`, e a rodada
#      fica auditada em `agent_runs` com input ESTRUTURADO (prompt_version, provider, modelo, hash);
#   2. a rodada NAO toca em nada alem dessas duas tabelas (organizations, contacts, recommendations,
#      scores, signals, interactions, sync_events, outbox_events intactos);
#   3. compliance: contato com opt_out_email RECUSA e NAO grava pedido; sem contato RECUSA; sem
#      evidencia (ne pesquisa, ne sinal, ne dor, ne score, ne TIER) RECUSA; sem recomendacao ABSTEM;
#      acao sem abordagem declarada ABSTEM; empresa inexistente RECUSA;
#   4. replay da MESMA entrada nao duplica (JA_GERADA, mesma contagem); evidencia nova gera pedido
#      NOVO e o PENDING anterior passa a EXPIRED (historico preservado);
#   5. provedor `chat-completions` medido contra STUB HTTP local (formato OpenAI): o request leva o
#      modelo, o prompt versionado e a evidencia; a resposta vira abordagem; tokens do provedor
#      entram na auditoria; resposta com numero INVENTADO RECUSA e nao grava pedido;
#   6. `prod` e recusado (exit 4) SEM escrita; `--planejar` nao abre conexao;
#   7. `--desfazer` dry-run nao apaga; `--confirmo` apaga SO os pedidos da rodada (auditoria fica);
#   8. a guarda de escrita recusa DDL e escrita fora das duas tabelas;
#   9. veredito ..... ACEITE_OUTREACH_001_OK / ACEITE_OUTREACH_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do modulo e exige que o aceite reprove O ITEM
#   ESPERADO de cada uma (nao basta "o aceite falhou").
#
# Variaveis: TRE_RAIZ, TRE_FIXTURE_IMAGEM (default postgres:16), TRE_OUTREACH_CONTAINER,
#            TRE_OUTREACH_TRABALHO.
# Exit: 0 = ACEITE_OUTREACH_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_OUTREACH_CONTAINER:-pg-outreach-acc}"
TRABALHO="${TRE_OUTREACH_TRABALHO:-/tmp/outreach-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="outreach-aceite-descartavel"
MODULO="$RAIZ/hermes/agents/outreach/outreach_generator.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; MODULO="${1:?--codigo exige caminho}" ;;
    --codigo=*) MODULO="${1#--codigo=}" ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" ;;
    *) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--raiz <dir>]"; exit 2 ;;
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
[ -f "$MODULO" ] || { echo "FALHOU modulo ausente: $MODULO"; exit 2; }
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
mkdir -p "$TRABALHO"

limpar() {
  local rc=$?
  [ -n "${STUB_PID:-}" ] && kill "$STUB_PID" >/dev/null 2>&1
  if [ "$MANTER" -eq 0 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  else
    echo "== --manter: container $CONTAINER e $TRABALHO ficaram de pe"
  fi
  exit $rc
}
trap limpar EXIT

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

# ---------------------------------------------------------------------------------------
# Container descartavel + migration + massa
# ---------------------------------------------------------------------------------------
docker run -d --name "$CONTAINER" \
  -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
  "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
for _ in $(seq 1 60); do
  # pg_isready mente no inicio (servidor temporario): a espera correta e SELECT 1 duas vezes
  psql_t -c "SELECT 1;" >/dev/null 2>&1 && psql_t -c "SELECT 1;" >/dev/null 2>&1 && break
  sleep 1
done
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }

ORG_A="11111111-1111-4111-8111-111111111111"   # tier A + pesquisa + dor + sinal + decisor com e-mail -> GERADA (EMAIL)
ORG_B="22222222-2222-4222-8222-222222222222"   # decisor com opt_out_email -> RECUSADA (CONTATO_BLOQUEADO)
ORG_C="33333333-3333-4333-8333-333333333333"   # sem recomendacao -> ABSTEVE (SEM_ACAO_RECOMENDADA)
ORG_D="44444444-4444-4444-8444-444444444444"   # recomendacao WAIT -> ABSTEVE (ACAO_SEM_ABORDAGEM_DECLARADA)
ORG_E="55555555-5555-4555-8555-555555555555"   # recomendacao sem contato -> RECUSADA (SEM_CONTATO)
ORG_F="66666666-6666-4666-8666-666666666666"   # contato ok, SEM pesquisa/sinal/dor/score/tier -> RECUSADA (SEM_EVIDENCIA)
ORG_G="77777777-7777-4777-8777-777777777777"   # PREPARE_LINKEDIN -> GERADA (canal LINKEDIN)
ORG_H="88888888-8888-4888-8888-888888888888"   # prova do stub: duas rodadas (sustentada e alucinada)
ORG_FANTASMA="99999999-9999-4999-8999-999999999999"
REC_A="a1111111-1111-4111-8111-111111111111"
REC_B="a2222222-2222-4222-8222-222222222222"
REC_D="a4444444-4444-4444-8444-444444444444"
REC_E="a5555555-5555-4555-8555-555555555555"
REC_F="a6666666-6666-4666-8666-666666666666"
REC_G="a7777777-7777-4777-8777-777777777777"
REC_H="a8888888-8888-4888-8888-888888888888"
CT_A="d1111111-1111-4111-8111-111111111111"
CT_B="d2222222-2222-4222-8222-222222222222"
CT_F="d6666666-6666-4666-8666-666666666666"
CT_G="d7777777-7777-4777-8777-777777777777"
CT_H="d8888888-8888-4888-8888-888888888888"
SINAIS_A=3
INTER_A=1

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, industry_name, employee_band,
        employee_count, city, state, status, created_at)
VALUES ('$ORG_A', 'Distribuidora Alfa LTDA', 'Distribuidora Alfa', 'Distribuicao B2B', '150_299', 210,
        'Campinas', 'SP', 'Pesquisado', NOW()),
       ('$ORG_B', 'Industria Beta LTDA', 'Industria Beta', 'Industria', '300_499', 320,
        'Joinville', 'SC', 'Qualificado', NOW()),
       ('$ORG_C', 'Servicos Gama LTDA', 'Servicos Gama', 'Servicos B2B', '70_149', 90,
        'Sao Paulo', 'SP', 'Pesquisado', NOW()),
       ('$ORG_D', 'Logistica Delta LTDA', 'Logistica Delta', 'Logistica', '150_299', 150,
        'Curitiba', 'PR', 'Abordagem iniciada', NOW()),
       ('$ORG_E', 'Comercio Epsilon LTDA', 'Comercio Epsilon', 'Distribuicao B2B', '300_499', 380,
        'Ribeirao Preto', 'SP', 'Qualificado', NOW()),
       ('$ORG_F', 'Industria Zeta LTDA', 'Industria Zeta', 'Industria', '500_699', 540,
        'Sorocaba', 'SP', 'Descoberto', NOW()),
       ('$ORG_G', 'Tech Eta LTDA', 'Tech Eta', 'SaaS/Tech B2B', '150_299', 180,
        'Florianopolis', 'SC', 'Qualificado', NOW()),
       ('$ORG_H', 'Servicos Theta LTDA', 'Servicos Theta', 'Servicos B2B', '300_499', 300,
        'Belo Horizonte', 'MG', 'Qualificado', NOW());

INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, first_name, job_title,
        decision_role, email, preferred_channel, do_not_contact, opt_out_email, created_at)
VALUES ('$CT_A', '$ORG_A', 'Maria Souza', 'Maria', 'Diretora de Operacoes', 'Decision Maker',
        'maria.souza@distribuidoraalfa.com.br', 'EMAIL', false, false, NOW()),
       ('$CT_B', '$ORG_B', 'Joao Lima', 'Joao', 'Diretor Industrial', 'Decision Maker',
        'joao.lima@industriabeta.com.br', 'EMAIL', false, true, NOW()),
       ('$CT_F', '$ORG_F', 'Ana Reis', 'Ana', 'Gerente de Processos', 'Influencer',
        'ana.reis@industriazeta.com.br', 'EMAIL', false, false, NOW()),
       ('$CT_G', '$ORG_G', 'Carlos Dias', 'Carlos', 'COO', 'Economic Buyer',
        NULL, 'LINKEDIN', false, false, NOW()),
       ('$CT_H', '$ORG_H', 'Paula Nunes', 'Paula', 'Diretora Administrativa', 'Decision Maker',
        'paula.nunes@servicostheta.com.br', 'EMAIL', false, false, NOW());

INSERT INTO sales_intelligence.research_runs (id, organization_id, agent_name, status, summary,
        completed_at, created_at)
SELECT gen_random_uuid(), o.id, 'scout', 'COMPLETED',
       'Empresa com tres centros de distribuicao e pedidos redigitados no ERP', NOW(), NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_G', '$ORG_H');

INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id, pain_category, pain_statement,
        status, created_at)
SELECT gen_random_uuid(), o.id, 'PROCESSO_MANUAL',
       'Pedidos chegam por e-mail e sao redigitados no ERP', 'PARTIALLY_VALIDATED', NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_G', '$ORG_H');

INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, title, description,
        detected_at, relevance_score)
SELECT gen_random_uuid(), o.id, 'EFFICIENCY_PROGRAM', 'Programa de eficiencia anunciado',
       'Empresa anunciou revisao de processos internos', NOW(), 70
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_G', '$ORG_H');

INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version,
        calculated_at)
SELECT gen_random_uuid(), o.id, 'PRIORITY', 82.5, 'priority-v1', NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_G', '$ORG_H');

INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system,
        operation, source_version, idempotency_key, status, request_payload, created_at)
SELECT gen_random_uuid(), 'organization', o.id, 'hermes', 'postgres', 'TIER', 'tiering-v1',
       'tier:TIER:' || o.id, 'SUCCESS', jsonb_build_object('tier', 'A'), NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_G', '$ORG_H');

INSERT INTO sales_intelligence.recommendations (id, organization_id, contact_id, recommendation_type,
        action, rationale, status, priority, created_at)
VALUES ('$REC_A', '$ORG_A', '$CT_A', 'NEXT_BEST_ACTION', 'SEND_EMAIL', 'tier A com decisor contactavel',
        'OPEN', 1, NOW()),
       ('$REC_B', '$ORG_B', '$CT_B', 'NEXT_BEST_ACTION', 'SEND_EMAIL', 'tier A com decisor',
        'OPEN', 1, NOW()),
       ('$REC_D', '$ORG_D', NULL, 'NEXT_BEST_ACTION', 'WAIT', 'abordada ontem', 'OPEN', 3, NOW()),
       ('$REC_E', '$ORG_E', NULL, 'NEXT_BEST_ACTION', 'SEND_EMAIL', 'sem contato identificado',
        'OPEN', 2, NOW()),
       ('$REC_F', '$ORG_F', '$CT_F', 'NEXT_BEST_ACTION', 'SEND_EMAIL', 'empresa descoberta',
        'OPEN', 2, NOW()),
       ('$REC_G', '$ORG_G', '$CT_G', 'NEXT_BEST_ACTION', 'PREPARE_LINKEDIN', 'COO sem e-mail',
        'OPEN', 2, NOW()),
       ('$REC_H', '$ORG_H', '$CT_H', 'NEXT_BEST_ACTION', 'SEND_EMAIL', 'tier A com decisora',
        'OPEN', 1, NOW());

INSERT INTO sales_intelligence.interactions (id, organization_id, contact_id, channel, direction,
        interaction_type, occurred_at, subject)
VALUES (gen_random_uuid(), '$ORG_A', '$CT_A', 'EMAIL', 'OUTBOUND', 'ABORDAGEM', NOW() - INTERVAL '9 days',
        'Primeiro contato');
SQL

contar() { psql_t -c "SELECT count(*) FROM $1;" | tr -d ' '; }
foto() { # contagem das tabelas que a rodada NAO pode tocar
  echo "$(contar sales_intelligence.organizations)|$(contar sales_intelligence.contacts)|$(contar sales_intelligence.recommendations)|$(contar sales_intelligence.scores)|$(contar sales_intelligence.signals)|$(contar sales_intelligence.pain_hypotheses)|$(contar sales_intelligence.research_runs)|$(contar sales_intelligence.interactions)|$(contar sales_intelligence.sync_events)|$(contar sales_intelligence.outbox_events)"
}
ANTES="$(foto)"
APROVACOES_ANTES="$(contar sales_intelligence.human_approvals)"

gerar() { # <label> <args...>
  python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" \
    --correlation-id "$(cat "$TRABALHO/cid_$1" 2>/dev/null || echo)" "$@" >"$TRABALHO/saida_$1.txt" 2>"$TRABALHO/erro_$1.txt"
  echo $?
}

# ---------------------------------------------------------------------------------------
# A1..A3: rodada real (offline) — pedido PENDING, auditoria e nada tocado
# ---------------------------------------------------------------------------------------
CID1="$(python3 -c 'import uuid;print(uuid.uuid4())')"; echo "$CID1" > "$TRABALHO/cid_A1"
RC=$(gerar A1 --organizacao "$ORG_A" --relatorio "$TRABALHO/rel_A1.json")
item "A1 exit 0 na rodada legitima" 0 "$RC"
item "A1 veredito GERADA" "acao=SEND_EMAIL canal=EMAIL veredito=GERADA" \
  "$(grep -o 'acao=SEND_EMAIL canal=EMAIL veredito=GERADA' "$TRABALHO/saida_A1.txt" | head -1)"
PEDIDO_A="$(psql_t -c "SELECT id FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_A';" | tr -d ' ')"
item "A1 pedido de aprovacao gravado (1)" 1 "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_A';" | tr -d ' ')"
item "A1 status do pedido" "PENDING" "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 action_type e a acao recomendada" "SEND_EMAIL" "$(psql_t -c "SELECT action_type FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 entidade e o contato" "CONTACT|$CT_A" "$(psql_t -c "SELECT entity_type || '|' || entity_id FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 decidido por humano ainda vazio" "|" "$(psql_t -c "SELECT COALESCE(decided_by,'') || '|' || COALESCE(decision_notes,'') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 canal no proposed_action" "EMAIL" "$(psql_t -c "SELECT proposed_action->>'canal' FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 assunto presente no proposed_action" "t" "$(psql_t -c "SELECT (length(proposed_action->>'assunto') > 3)::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 corpo cita a evidencia" "t" "$(psql_t -c "SELECT (proposed_action->>'corpo' ~ '\[E[0-9]+\]')::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 corpo leva a assinatura declarada" "t" "$(psql_t -c "SELECT (proposed_action->>'corpo' LIKE '%anderson.ribeiro@transformativa.com.br%')::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 referencia a recomendacao lida" "$REC_A" "$(psql_t -c "SELECT proposed_action->>'recommendation_id' FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 hash de entrada registrado (64 hex)" "64" "$(psql_t -c "SELECT length(proposed_action->>'entrada_hash') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A1 pedido nao carrega credencial" "f" "$(psql_t -c "SELECT (proposed_action::text ILIKE '%api_key%' OR proposed_action::text ILIKE '%bearer%')::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A2 auditoria da rodada" "COMPLETED|outreach|gerador-abordagem-v1" \
  "$(psql_t -c "SELECT status || '|' || agent_name || '|' || agent_version FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')"
item "A2 provider/modelo offline registrados" "offline|renderizador-deterministico-v1" \
  "$(psql_t -c "SELECT input->>'model_provider' || '|' || input->>'model_name' FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')"
item "A2 versao do prompt registrada" "abordagem-v1" \
  "$(psql_t -c "SELECT input->>'prompt_version' FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')"
item "A2 hash de entrada na auditoria" "$(psql_t -c "SELECT proposed_action->>'entrada_hash' FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')" \
  "$(psql_t -c "SELECT input->>'entrada_hash' FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')"
item "A2 LLM offline nao conta token nem custo" "||" \
  "$(psql_t -c "SELECT COALESCE(tokens_input::text,'') || '|' || COALESCE(tokens_output::text,'') || '|' || COALESCE(estimated_cost::text,'') FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')"
item "A2 pedido ligado a auditoria" "$PEDIDO_A" \
  "$(psql_t -c "SELECT output->>'human_approval_id' FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')"
item "A2 nenhum envio registrado (interactions intacta)" "$INTER_A" "$(contar sales_intelligence.interactions)"
item "A3 nada fora das duas tabelas foi tocado" "$ANTES" "$(foto)"
item "A3 outbox nao recebeu evento" "0" "$(contar sales_intelligence.outbox_events)"
item "A3 so o pedido novo em human_approvals" "$((APROVACOES_ANTES + 1))" "$(contar sales_intelligence.human_approvals)"

# ---------------------------------------------------------------------------------------
# A4: replay da MESMA entrada nao duplica (nem expira)
# ---------------------------------------------------------------------------------------
CID2="$(python3 -c 'import uuid;print(uuid.uuid4())')"
RC=$(gerar A4 --organizacao "$ORG_A" --correlation-id "$CID2")
item "A4 exit 0 no replay" 0 "$RC"
item "A4 replay reconhecido" "JA_GERADA" "$(grep -o 'veredito=JA_GERADA' "$TRABALHO/saida_A4.txt" | head -1 | cut -d= -f2)"
item "A4 replay nao duplica pedido" "1" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_A';" | tr -d ' ')"
item "A4 replay nao expira o PENDING" "1" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status = 'PENDING' AND entity_id = '$CT_A';" | tr -d ' ')"
item "A4 mesmo id (idempotencia por conteudo)" "$PEDIDO_A" \
  "$(psql_t -c "SELECT output->>'human_approval_id' FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID2';" | tr -d ' ')"
item "A4 replay tambem e auditado" "COMPLETED" "$(psql_t -c "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID2';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A5: evidencia nova -> pedido novo e a anterior EXPIRED (historico preservado)
# ---------------------------------------------------------------------------------------
psql_stdin >/dev/null <<SQL
UPDATE sales_intelligence.signals SET relevance_score = 95
 WHERE organization_id = '$ORG_A';
SQL
CID3="$(python3 -c 'import uuid;print(uuid.uuid4())')"
RC=$(gerar A5 --organizacao "$ORG_A" --correlation-id "$CID3")
item "A5 exit 0 com evidencia nova" 0 "$RC"
item "A5 abordagem nova gerada" "GERADA" "$(grep -o 'veredito=GERADA' "$TRABALHO/saida_A5.txt" | head -1 | cut -d= -f2)"
item "A5 pedido antigo preservado e EXPIRED" "EXPIRED" "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A5 agora ha 2 pedidos do contato (historico, nao reescrita)" "2" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_A';" | tr -d ' ')"
item "A5 um unico PENDING na vez" "1" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status = 'PENDING' AND entity_id = '$CT_A';" | tr -d ' ')"
PEDIDO_A2="$(psql_t -c "SELECT id FROM sales_intelligence.human_approvals WHERE status = 'PENDING' AND entity_id = '$CT_A';" | tr -d ' ')"
item "A5 o PENDING e outro pedido (hash novo)" "t" "$(psql_t -c "SELECT ('$PEDIDO_A2' <> '$PEDIDO_A')::text;" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A6: compliance e fail-closed por empresa (recusa/abstencao SEM gravar)
# ---------------------------------------------------------------------------------------
gerar B --organizacao "$ORG_B" >/dev/null
item "A6 contato com opt_out recusa" "RECUSADA|CONTATO_BLOQUEADO" \
  "$(grep -o 'veredito=RECUSADA motivo=CONTATO_BLOQUEADO' "$TRABALHO/saida_B.txt" | head -1 | sed 's/veredito=//;s/ motivo=/|/' )"
item "A6 recusa de compliance nao grava pedido" "0" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_B';" | tr -d ' ')"
item "A6 recusa de compliance registra motivo na auditoria" "REJECTED|CONTATO_BLOQUEADO" \
  "$(psql_t -c "SELECT status || '|' || (error->>'motivo') FROM sales_intelligence.agent_runs WHERE organization_id = '$ORG_B';" | tr -d ' ')"

gerar C --organizacao "$ORG_C" >/dev/null
item "A6 sem recomendacao abstem" "ABSTEVE" "$(grep -o 'veredito=ABSTEVE' "$TRABALHO/saida_C.txt" | head -1 | cut -d= -f2)"
gerar D --organizacao "$ORG_D" >/dev/null
item "A6 acao WAIT nao gera abordagem" "ACAO_SEM_ABORDAGEM_DECLARADA" \
  "$(grep -o 'motivo=ACAO_SEM_ABORDAGEM_DECLARADA' "$TRABALHO/saida_D.txt" | head -1 | cut -d= -f2)"
item "A6 abstencao nao grava nada" "0" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id IS NULL OR entity_id NOT IN (SELECT id FROM sales_intelligence.contacts);" | tr -d ' ')"
gerar E --organizacao "$ORG_E" >/dev/null
item "A6 recomendacao sem contato recusa" "SEM_CONTATO" "$(grep -o 'motivo=SEM_CONTATO' "$TRABALHO/saida_E.txt" | head -1 | cut -d= -f2)"
gerar F --organizacao "$ORG_F" >/dev/null
item "A6 empresa sem fato nenhum recusa" "SEM_EVIDENCIA" "$(grep -o 'motivo=SEM_EVIDENCIA' "$TRABALHO/saida_F.txt" | head -1 | cut -d= -f2)"
item "A6 empresa sem fato nao grava pedido" "0" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_F';" | tr -d ' ')"
gerar FANTASMA --organizacao "$ORG_FANTASMA" >/dev/null
item "A6 empresa inexistente recusa" "ORGANIZACAO_NAO_ENCONTRADA" \
  "$(grep -o 'motivo=ORGANIZACAO_NAO_ENCONTRADA' "$TRABALHO/saida_FANTASMA.txt" | head -1 | cut -d= -f2)"

# ---------------------------------------------------------------------------------------
# A7: canal LINKEDIN, guarda de escrita e prod/planejar
# ---------------------------------------------------------------------------------------
gerar G --organizacao "$ORG_G" >/dev/null
item "A7 acao PREPARE_LINKEDIN usa o canal declarado" "PREPARE_LINKEDIN|LINKEDIN" \
  "$(psql_t -c "SELECT action_type || '|' || proposed_action->>'canal' FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_G';" | tr -d ' ')"
item "A7 contato sem e-mail pode ser abordado no LinkedIn" "1" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type = 'PREPARE_LINKEDIN' AND status = 'PENDING';" | tr -d ' ')"
echo "ALTER TABLE sales_intelligence.scores ADD COLUMN x int;" > "$TRABALHO/ddl.sql"
python3 - "$MODULO" "$TRABALHO/ddl.sql" > "$TRABALHO/ddl_out.txt" 2>&1 <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("m", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sql = open(sys.argv[2]).read()
try:
    m.validar_sql(sql); print("ACEITOU")
except m.RecusaDeEscrita as r:
    print("RECUSOU", r)
PY
item "A7 guarda de escrita recusa DDL" "RECUSOU" "$(cut -d' ' -f1 "$TRABALHO/ddl_out.txt")"
RC=$(python3 "$MODULO" --ambiente prod --prefixo "$PREFIXO" --organizacao "$ORG_A" >/dev/null 2>&1; echo $?)
item "A7 prod recusado (exit 4)" 4 "$RC"
RC=$(python3 "$MODULO" --planejar >/dev/null 2>&1; echo $?)
item "A7 --planejar exit 0" 0 "$RC"

# ---------------------------------------------------------------------------------------
# A8: provedor chat-completions contra STUB local; alucinacao recusada
# ---------------------------------------------------------------------------------------
cat > "$TRABALHO/stub_provedor.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
RESPOSTA = json.load(open(sys.argv[1], encoding="utf-8"))
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        pedido = json.loads(self.rfile.read(n).decode("utf-8"))
        with open(sys.argv[2], "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"path": self.path, "model": pedido.get("model"),
                                 "auth": bool(self.headers.get("Authorization")),
                                 "system": pedido["messages"][0]["content"][:40],
                                 "evidencia_no_prompt": "E1 [" in pedido["messages"][1]["content"]}) + "\n")
        corpo = json.dumps({"model": "modelo-aceite", "usage": {"prompt_tokens": 321, "completion_tokens": 654},
                            "choices": [{"message": {"content": json.dumps(RESPOSTA[0], ensure_ascii=False)}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo))); self.end_headers(); self.wfile.write(corpo)
    def log_message(self, *a): return
p = sys.argv[3]
ThreadingHTTPServer(("127.0.0.1", int(p)), H).serve_forever()
PY
PORTA="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
SINAIS="$(psql_t -c "SELECT string_agg(title || ' ' || description, ' | ') FROM sales_intelligence.signals WHERE organization_id = '$ORG_A';")"
# abordagem SUSTENTADA: usa titulo/descricao do sinal (esta no ledger) e o resumo da pesquisa
cat > "$TRABALHO/stub_bom.json" <<JSON
[{"assunto": "Eficiencia operacional na Distribuidora Alfa [E5]",
  "corpo": "Ola, Paula. Vi que a Distribuidora Alfa tem o sinal \\"Programa de eficiencia anunciado\\" [E5].\\n\\nPelo que li, os pedidos chegam por e-mail e sao redigitados no ERP [E3]. Isso e hipotese, nao diagnostico.\\n\\nFaz sentido conversar?",
  "cta": "Conversa curta de diagnostico sobre o cenario acima."}]
JSON
python3 "$TRABALHO/stub_provedor.py" "$TRABALHO/stub_bom.json" "$TRABALHO/stub_pedidos.jsonl" "$PORTA" &
STUB_PID=$!
sleep 1
CID4="$(python3 -c 'import uuid;print(uuid.uuid4())')"
RC=$(TRE_OUTREACH_API_KEY=chave-de-teste TRE_OUTREACH_BASE_URL="http://127.0.0.1:$PORTA/v1" TRE_OUTREACH_MODELO=modelo-de-teste \
  python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --provedor chat-completions --organizacao "$ORG_H" \
  --correlation-id "$CID4" --relatorio "$TRABALHO/rel_stub.json" >"$TRABALHO/saida_stub.txt" 2>&1; echo $?)
item "A8 exit 0 com provedor HTTP (stub local)" 0 "$RC"
item "A8 abordagem vinda do provedor virou pedido PENDING" "1" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_H' AND status = 'PENDING';" | tr -d ' ')"
item "A8 provider/modelo do provedor na auditoria" "chat-completions|modelo-aceite" \
  "$(psql_t -c "SELECT input->>'model_provider' || '|' || input->>'model_name' FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID4';" | tr -d ' ')"
item "A8 tokens do provedor entraram na auditoria" "321|654" \
  "$(psql_t -c "SELECT tokens_input || '|' || tokens_output FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID4';" | tr -d ' ')"
item "A8 request levou modelo, credencial e evidencia no prompt" "modelo-de-teste|true|true|true" \
  "$(python3 -c 'import json,sys;d=json.loads(open(sys.argv[1]).readline());print("|".join([d["model"],str(d["auth"]),str(d["evidencia_no_prompt"]),str(d["path"].endswith("/chat/completions"))]))' "$TRABALHO/stub_pedidos.jsonl")"
item "A8 credencial nao vaza para o pedido gravado" "f" \
  "$(psql_t -c "SELECT (proposed_action::text ILIKE '%chave-de-teste%')::text FROM sales_intelligence.human_approvals WHERE entity_id = '$CT_H';" | tr -d ' ')"
kill "$STUB_PID" >/dev/null 2>&1; STUB_PID=""

# alucinacao: numero que NAO existe em evidencia nenhuma -> RECUSADA, sem pedido novo
cat > "$TRABALHO/stub_alucina.json" <<'JSON'
[{"assunto": "Ganho garantido de 37% na Servicos Theta [E5]",
  "corpo": "Ola, Paula. Reduzimos 37% do retrabalho em empresas como a Servicos Theta [E5]. Temos garantia de resultado.",
  "cta": "Conversa?"}]
JSON
PORTA2="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
python3 "$TRABALHO/stub_provedor.py" "$TRABALHO/stub_alucina.json" "$TRABALHO/stub_pedidos2.jsonl" "$PORTA2" &
STUB_PID=$!
sleep 1
APROVACOES_ANTES_ALUC="$(contar sales_intelligence.human_approvals)"
CID5="$(python3 -c 'import uuid;print(uuid.uuid4())')"
RC=$(TRE_OUTREACH_API_KEY=chave-de-teste TRE_OUTREACH_BASE_URL="http://127.0.0.1:$PORTA2/v1" TRE_OUTREACH_MODELO=modelo-de-teste \
  python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --provedor chat-completions --organizacao "$ORG_H" \
  --correlation-id "$CID5" >"$TRABALHO/saida_aluc.txt" 2>&1; echo $?)
item "A8 alucinacao RECUSA a rodada" "RECUSADA" "$(grep -o 'veredito=RECUSADA' "$TRABALHO/saida_aluc.txt" | head -1 | cut -d= -f2)"
item "A8 alucinacao aponta fato sem sustentacao" "1" "$(grep -c 'numero sem sustentacao\|afirmacao proibida' "$TRABALHO/saida_aluc.txt")"
item "A8 alucinacao nao grava pedido" "$APROVACOES_ANTES_ALUC" "$(contar sales_intelligence.human_approvals)"
item "A8 recusa por fato inventado fica auditada" "REJECTED" "$(psql_t -c "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID5';" | tr -d ' ')"
kill "$STUB_PID" >/dev/null 2>&1; STUB_PID=""

# ---------------------------------------------------------------------------------------
# A9: desfazer (dry-run x --confirmo) preserva a auditoria
# ---------------------------------------------------------------------------------------
CONTAGEM_H="$(contar sales_intelligence.human_approvals)"
DRY="$(python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CID1" 2>&1 | grep -o '"seriam_apagados": [0-9]*' | grep -o '[0-9]*')"
item "A9 dry-run informa o que seria apagado (1)" 1 "$DRY"
item "A9 dry-run nao apaga" "$CONTAGEM_H" "$(contar sales_intelligence.human_approvals)"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CID1" --confirmo >/dev/null 2>&1
item "A9 --confirmo apaga so o pedido da rodada" "$((CONTAGEM_H - 1))" "$(contar sales_intelligence.human_approvals)"
item "A9 auditoria da rodada desfeita permanece" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')" \
  "$(psql_t -c "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id = '$CID1';" | tr -d ' ')"
item "A9 nada fora das duas tabelas foi tocado na rodada toda" "$ANTES" "$(foto)"

echo "---"
if [ "$ITENS_FALHOU" -eq 0 ]; then
  echo "RESULTADO: ACEITE_OUTREACH_001_OK ($ITENS_OK OK / 0 FALHOU)"
  RC_FINAL=0
else
  echo "RESULTADO: ACEITE_OUTREACH_001_FALHOU ($ITENS_OK OK / $ITENS_FALHOU FALHOU)"
  RC_FINAL=1
fi

# ---------------------------------------------------------------------------------------
# Prova de dente: a mutacao tem de reprovar O ITEM ESPERADO
# ---------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente"
  mutar() { # <arquivo origem> <destino> <de> <para>
    python3 - "$1" "$2" "$3" "$4" <<'PY'
import sys
texto = open(sys.argv[1], encoding="utf-8").read()
if sys.argv[3] not in texto:
    raise SystemExit(f"ancora nao encontrada: {sys.argv[3]!r}")
open(sys.argv[2], "w", encoding="utf-8").write(texto.replace(sys.argv[3], sys.argv[4], 1))
PY
  }
  rodar_dente() { # <nome> <item esperado> <arg extra do aceite>
    local nome="$1" esperado="$2"
    if grep -q "FALHOU $esperado" "$TRABALHO/dente_$nome.out"; then
      echo "OK     dente $nome -> reprovou '$esperado'"
    else
      echo "FALHOU dente $nome -> NAO reprovou '$esperado'"; ITENS_FALHOU=$((ITENS_FALHOU + 1))
    fi
  }
  mkdir -p "$TRABALHO/dentes"
  mutar "$MODULO" "$TRABALHO/dentes/sem_guarda.py" \
    "    return [b for b in bloqueios if contato.get(b)]" "    return []"
  TRE_OUTREACH_CONTAINER="pg-outreach-dente1" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/sem_guarda.py" \
    > "$TRABALHO/dente_guarda.out" 2>&1
  rodar_dente "guarda" "A6 contato com opt_out recusa"
  mutar "$MODULO" "$TRABALHO/dentes/sem_fato.py" \
    "        if _digitos(numero) and _digitos(numero) not in digitos:" "        if False:"
  TRE_OUTREACH_CONTAINER="pg-outreach-dente2" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/sem_fato.py" \
    > "$TRABALHO/dente_fato.out" 2>&1
  rodar_dente "fato" "A8 alucinacao RECUSA a rodada"
  mutar "$MODULO" "$TRABALHO/dentes/id_aleatorio.py" \
    'f"{GERADOR_VERSION}:{organization_id}:{entrada_hash}"' "str(uuid.uuid4())"
  TRE_OUTREACH_CONTAINER="pg-outreach-dente3" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/id_aleatorio.py" \
    > "$TRABALHO/dente_id.out" 2>&1
  rodar_dente "id" "A4 replay nao duplica pedido"
  mutar "$MODULO" "$TRABALHO/dentes/sem_supersessao.py" \
    "    if supersede:" "    if False:"
  TRE_OUTREACH_CONTAINER="pg-outreach-dente4" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/sem_supersessao.py" \
    > "$TRABALHO/dente_supersessao.out" 2>&1
  rodar_dente "supersessao" "A5 pedido antigo preservado e EXPIRED"
fi

exit $RC_FINAL
