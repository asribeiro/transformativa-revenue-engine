#!/usr/bin/env bash
# teste_nba_aceite.sh [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--politica <json>] [--raiz <dir>]
#
# ACEITE E2E do NEXT BEST ACTION v1 (card TRE-W5-E07-T01) — PostgreSQL descartavel.
#
# Roda onde existe daemon Docker (a VPS do ambiente: o container do Hermes nao tem). NUNCA toca
# `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio (`pg-nba-acc`),
# aplica a migration 0001, mede e, no fim, remove o container e o diretorio de trabalho. Se o
# container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (A1..A11 de `docs/architecture/next-best-action-v1.md`):
#   1. a recomendacao LEGITIMA e gravada em `recommendations` (status OPEN) com a acao do contrato,
#      prioridade/prazos da politica, contato escolhido e rationale com a evidencia; auditoria em
#      `agent_runs` sem LLM (model/tokens/custo NULL) e sem `confidence` inventada;
#   2. a rodada NAO toca score, tier, contato nem interacao: so `recommendations` e `agent_runs`;
#   3. `prod` e recusado (exit 4) SEM escrita; `--planejar` e `--regras` nao abrem conexao;
#   4. replay da MESMA entrada nao duplica (id deterministico); evidencia nova gera recomendacao
#      NOVA e SUPERSEDE a anterior (historico preservado);
#   5. a TABELA DE DECISAO e a da politica: cada estado de evidencia produz a acao esperada
#      (SEND_EMAIL, PREPARE_LINKEDIN, WAIT, FOLLOW_UP, CREATE_MEETING, NURTURE, DISQUALIFY,
#      RESEARCH_MORE, FIND_DECISION_MAKER) e a PRIMEIRA regra que casa vence (compliance antes do tier);
#   6. empresa SEM registro TIER RECUSA (SEM_TIER) e nao grava recomendacao (o NBA nao adivinha tier);
#   7. empresa inexistente e RECUSADA sem gravar;
#   8. desfazer dry-run nao apaga; `--confirmo` apaga SO as recomendacoes da rodada (auditoria fica);
#   9. veredito ..... ACEITE_NBA_001_OK / ACEITE_NBA_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do modulo e exige que o aceite reprove O ITEM
#   ESPERADO de cada uma (nao basta "o aceite falhou").
#
# Variaveis: TRE_RAIZ, TRE_FIXTURE_IMAGEM (default postgres:16), TRE_NBA_CONTAINER,
#            TRE_NBA_TRABALHO.
# Exit: 0 = ACEITE_NBA_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_NBA_CONTAINER:-pg-nba-acc}"
TRABALHO="${TRE_NBA_TRABALHO:-/tmp/nba-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="nba-aceite-descartavel"
MODULO="$RAIZ/hermes/scores/nba/nba.py"
POLITICA=""
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; MODULO="${1:?--codigo exige caminho}" ;;
    --codigo=*) MODULO="${1#--codigo=}" ;;
    --politica) shift; POLITICA="${1:?--politica exige caminho}" ;;
    --politica=*) POLITICA="${1#--politica=}" ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" ;;
    *) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--politica <json>] [--raiz <dir>]"; exit 2 ;;
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
  if [ "$MANTER" -eq 0 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1
    rm -rf "$TRABALHO"
  else
    echo "== --manter: container $CONTAINER e $TRABALHO ficaram de pe"
  fi
  exit $rc
}
trap limpar EXIT

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }

# ---------------------------------------------------------------------------------------
# Container descartavel + migration + massa (empresas, registro TIER, contatos, interacoes)
# ---------------------------------------------------------------------------------------
docker run -d --name "$CONTAINER" \
  -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
  "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
for _ in $(seq 1 60); do
  psql_t -c "SELECT 1;" >/dev/null 2>&1 && psql_t -c "SELECT 1;" >/dev/null 2>&1 && break
  sleep 1
done
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }

ORG_A="11111111-1111-4111-8111-111111111111"   # tier A+ / decisor com e-mail / sem interacao -> SEND_EMAIL
ORG_B="22222222-2222-4222-8222-222222222222"   # abordada ONTEM, sem resposta -> WAIT
ORG_C="33333333-3333-4333-8333-333333333333"   # abordada 5 dias atras, sem resposta -> FOLLOW_UP
ORG_D="44444444-4444-4444-8444-444444444444"   # respondeu POSITIVO -> CREATE_MEETING
ORG_E="55555555-5555-4555-8555-555555555555"   # tier Nurture -> NURTURE (R03)
ORG_F="66666666-6666-4666-8666-666666666666"   # unico contato BLOQUEADO (e sem pesquisa) -> NURTURE (R01 vence R04)
ORG_G="77777777-7777-4777-8777-777777777777"   # dor REJEITADA e 0 sinal ativo -> DISQUALIFY
ORG_H="88888888-8888-4888-8888-888888888888"   # sem pesquisa concluida -> RESEARCH_MORE
ORG_I="99999999-9999-4999-8999-999999999999"   # contato 'User' (nao e decisor) -> FIND_DECISION_MAKER
ORG_J="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"   # decisor SEM e-mail, preferido LinkedIn -> PREPARE_LINKEDIN
ORG_K="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"   # SEM registro TIER -> RECUSA SEM_TIER
ORG_FANTASMA="cccccccc-cccc-4ccc-8ccc-cccccccccccc"
CT_A="d1111111-1111-4111-8111-111111111111"
CT_B="d2222222-2222-4222-8222-222222222222"
CT_C="d3333333-3333-4333-8333-333333333333"
CT_D="d4444444-4444-4444-8444-444444444444"
CT_F="d6666666-6666-4666-8666-666666666666"
CT_I="d9999999-9999-4999-8999-999999999999"
CT_J="daaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CID1="e1111111-1111-4111-8111-111111111111"
CID2="e2222222-2222-4222-8222-222222222222"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, status, created_at)
VALUES ('$ORG_A', 'Distribuidora A LTDA', 'A', 'Pesquisado', NOW()),
       ('$ORG_B', 'Industria B LTDA', 'B', 'Abordagem iniciada', NOW()),
       ('$ORG_C', 'Servicos C LTDA', 'C', 'Abordagem iniciada', NOW()),
       ('$ORG_D', 'Comercio D LTDA', 'D', 'Engajamento', NOW()),
       ('$ORG_E', 'Comercio E LTDA', 'E', 'Descoberto', NOW()),
       ('$ORG_F', 'Comercio F LTDA', 'F', 'Descoberto', NOW()),
       ('$ORG_G', 'Industria G LTDA', 'G', 'Pesquisado', NOW()),
       ('$ORG_H', 'Logistica H LTDA', 'H', 'Descoberto', NOW()),
       ('$ORG_I', 'Servicos I LTDA', 'I', 'Pesquisado', NOW()),
       ('$ORG_J', 'SaaS J LTDA', 'J', 'Pesquisado', NOW()),
       ('$ORG_K', 'Comercio K LTDA', 'K', 'Descoberto', NOW());
SQL

# tier() <org> <tier> <dias_atras>: o registro do card E06 (sync_events operation=TIER)
tier() {
  psql_t -c "INSERT INTO sales_intelligence.sync_events
      (id, entity_type, entity_id, source_system, target_system, operation, source_version,
       idempotency_key, status, request_payload, created_at)
    VALUES (gen_random_uuid(), 'organization', '$1', 'postgresql', 'odoo', 'TIER', 'tiering-v1',
            NULL, 'PROCESSED', jsonb_build_object('tier', '$2', 'score_lido', jsonb_build_object('score_value', 90.00)),
            NOW() - INTERVAL '$3 days');" >/dev/null
}
contato() { # <id> <org> <papel> <email|NULL> <canal|NULL> <bloqueado> <linkedin|NULL>
  local email="NULL" canal="NULL" linkedin="NULL"
  [ "$4" != "NULL" ] && email="'$4'"
  [ "$5" != "NULL" ] && canal="'$5'"
  [ "$7" != "NULL" ] && linkedin="'$7'"
  psql_t -c "INSERT INTO sales_intelligence.contacts
      (id, organization_id, full_name, decision_role, email, preferred_channel, linkedin_url,
       do_not_contact, opt_out_email, created_at)
    VALUES ('$1', '$2', 'Contato $1', '$3', $email, $canal, $linkedin, $6, false, NOW() - INTERVAL '2 days');" >/dev/null
}
pesquisa() { # <org>
  psql_t -c "INSERT INTO sales_intelligence.research_runs
      (id, organization_id, agent_name, agent_version, status, created_at)
    VALUES (gen_random_uuid(), '$1', 'research', '1.0.0', 'COMPLETED', NOW() - INTERVAL '2 days');" >/dev/null
}
interacao() { # <org> <dias_atras> <direcao> <sentimento|NULL>
  local sent="NULL"
  [ "$4" != "NULL" ] && sent="'$4'"
  psql_t -c "INSERT INTO sales_intelligence.interactions
      (id, organization_id, channel, direction, interaction_type, occurred_at, sentiment, created_at)
    VALUES (gen_random_uuid(), '$1', 'EMAIL', '$3', 'TESTE', NOW() - INTERVAL '$2 days', $sent, NOW());" >/dev/null
}
hipotese() { # <org> <status>
  psql_t -c "INSERT INTO sales_intelligence.pain_hypotheses
      (id, organization_id, pain_statement, status, created_at)
    VALUES (gen_random_uuid(), '$1', 'Dor medida no aceite', '$2', NOW() - INTERVAL '1 day');" >/dev/null
}

tier "$ORG_A" "A+" 1; tier "$ORG_B" "A+" 1; tier "$ORG_C" "B" 1; tier "$ORG_D" "B" 1
tier "$ORG_E" "Nurture" 1; tier "$ORG_F" "A" 1; tier "$ORG_G" "A" 1; tier "$ORG_H" "A+" 1
tier "$ORG_I" "A" 1; tier "$ORG_J" "B" 1
contato "$CT_A" "$ORG_A" "Economic Buyer" "cfo@a.example" "EMAIL" false NULL
contato "$CT_B" "$ORG_B" "Decision Maker" "coo@b.example" "EMAIL" false NULL
contato "$CT_C" "$ORG_C" "Decision Maker" "diretor@c.example" "EMAIL" false NULL
contato "$CT_D" "$ORG_D" "Champion" "cx@d.example" "EMAIL" false NULL
contato "$CT_F" "$ORG_F" "Decision Maker" "bloqueado@f.example" "EMAIL" true NULL
contato "$CT_I" "$ORG_I" "User" "usuario@i.example" "EMAIL" false NULL
contato "$CT_J" "$ORG_J" "Decision Maker" NULL "LINKEDIN" false "https://linkedin.example/j"
pesquisa "$ORG_A"; pesquisa "$ORG_B"; pesquisa "$ORG_C"; pesquisa "$ORG_D"; pesquisa "$ORG_G"
pesquisa "$ORG_I"; pesquisa "$ORG_J"
interacao "$ORG_B" 1 "OUTBOUND" NULL
interacao "$ORG_C" 5 "OUTBOUND" NULL
interacao "$ORG_D" 4 "OUTBOUND" NULL
interacao "$ORG_D" 3 "INBOUND" "POSITIVO"
hipotese "$ORG_G" "REJECTED"

conta() { psql_t -c "$1" | head -1; }
rodar() { # <cid> <args...>
  local cid="$1"; shift
  local extra=()
  [ -n "$POLITICA" ] && extra=(--politica "$POLITICA")
  ( cd "$RAIZ" && python3 "$MODULO" --ambiente dev --correlation-id "$cid" \
      --prefixo "$PREFIXO" --raiz "$RAIZ" "${extra[@]}" "$@" ) 2>&1
}

REC_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations;")
CONTATOS_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.contacts;")
INTER_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.interactions;")
ORGS_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.organizations;")
DIGITAL_TIER=$(conta "SELECT md5(string_agg(entity_id || ':' || COALESCE(request_payload->>'tier',''), ',' ORDER BY id)) FROM sales_intelligence.sync_events WHERE operation='TIER';")

# ---------------------------------------------------------------------------------------
# A1 — recomendacao gravada, com a acao, os prazos e a auditoria da rodada
# ---------------------------------------------------------------------------------------
SAIDA1=$(rodar "$CID1" --organizacao "$ORG_A" --relatorio "$TRABALHO/rodada1.json")
RC1=$?
echo "$SAIDA1" | sed 's/^/  /' | head -12
item "A1 rodada1 exit 0" 0 "$RC1"
item "A1 veredito RECOMENDADA" 1 "$(echo "$SAIDA1" | grep -c '"veredito": "RECOMENDADA"')"
item "A1 acao SEND_EMAIL na saida" 1 "$(echo "$SAIDA1" | grep -c 'acao=SEND_EMAIL')"
item "A1 relatorio escrito" 1 "$([ -s "$TRABALHO/rodada1.json" ] && echo 1 || echo 0)"
item "A1 uma recomendacao para a empresa" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 recommendation_type NEXT_BEST_ACTION" "NEXT_BEST_ACTION" "$(conta "SELECT recommendation_type FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 action e a do contrato" "SEND_EMAIL" "$(conta "SELECT action FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 status OPEN" "OPEN" "$(conta "SELECT status FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 prioridade da politica (R12 = 2)" 2 "$(conta "SELECT priority FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 confidence NULL (nao inventa confianca)" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND confidence IS NOT NULL;")"
item "A1 due_at e expires_at gravados" 2 "$(conta "SELECT (due_at IS NOT NULL)::int + (expires_at IS NOT NULL)::int FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 contato escolhido e o decisor" "$CT_A" "$(conta "SELECT contact_id FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 rationale traz o motivo da regra" 1 "$(conta "SELECT CASE WHEN rationale LIKE '%DECISOR_COM_EMAIL%' THEN 1 ELSE 0 END FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 rationale traz o tier lido" 1 "$(conta "SELECT CASE WHEN rationale LIKE '%tier=A+%' THEN 1 ELSE 0 END FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A1 rationale traz o registro TIER (identidade)" 1 "$(conta "SELECT CASE WHEN rationale LIKE '%registro %' THEN 1 ELSE 0 END FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A9 auditoria da rodada gravada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1' AND organization_id='$ORG_A';")"
item "A9 auditoria COMPLETED" "COMPLETED" "$(conta "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1' AND organization_id='$ORG_A';")"
item "A1 auditoria sem LLM (modelo/tokens/custo nulos)" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1' AND (model IS NOT NULL OR tokens_input IS NOT NULL OR estimated_cost IS NOT NULL);")"
item "A1 auditoria guarda o id da recomendacao" 1 "$(conta "SELECT CASE WHEN output->'recomendacoes'->>0 IS NOT NULL THEN 1 ELSE 0 END FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1' AND organization_id='$ORG_A';")"

# ---------------------------------------------------------------------------------------
# A2 — replay da MESMA entrada nao duplica
# ---------------------------------------------------------------------------------------
SAIDA2=$(rodar "$CID2" --organizacao "$ORG_A")
item "A2 replay: veredito JA_RECOMENDADA" 1 "$(echo "$SAIDA2" | grep -c '"veredito": "JA_RECOMENDADA"')"
item "A2 replay: nada gravado" 1 "$(echo "$SAIDA2" | grep -c '"ja_existia": 1')"
item "A2 replay: continua 1 recomendacao" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A2 replay: nenhuma supersedida ainda" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND status='SUPERSEDED';")"

# ---------------------------------------------------------------------------------------
# A3 — a TABELA DE DECISAO: cada estado de evidencia produz a acao esperada
# ---------------------------------------------------------------------------------------
SAIDA3=$(rodar "$CID2" --organizacao "$ORG_B" --organizacao "$ORG_C" --organizacao "$ORG_D" \
  --organizacao "$ORG_E" --organizacao "$ORG_F" --organizacao "$ORG_G" --organizacao "$ORG_H" \
  --organizacao "$ORG_I" --organizacao "$ORG_J")
acao_de() { conta "SELECT action FROM sales_intelligence.recommendations WHERE organization_id='$1' AND status='OPEN';"; }
motivo_de() { conta "SELECT rationale FROM sales_intelligence.recommendations WHERE organization_id='$1' AND status='OPEN';"; }
item "A3 B abordada ontem -> WAIT (R08)" "WAIT" "$(acao_de "$ORG_B")"
item "A3 B motivo ABORDAGEM_RECENTE" 1 "$(motivo_de "$ORG_B" | grep -c 'ABORDAGEM_RECENTE')"
item "A3 B prioridade 3" 3 "$(conta "SELECT priority FROM sales_intelligence.recommendations WHERE organization_id='$ORG_B' AND status='OPEN';")"
item "A3 C sem resposta ha 5 dias -> FOLLOW_UP (R09)" "FOLLOW_UP" "$(acao_de "$ORG_C")"
item "A3 C motivo SEM_RESPOSTA" 1 "$(motivo_de "$ORG_C" | grep -c 'SEM_RESPOSTA')"
item "A3 D resposta positiva -> CREATE_MEETING (R05)" "CREATE_MEETING" "$(acao_de "$ORG_D")"
item "A3 D contato escolhido e o Champion" "$CT_D" "$(conta "SELECT contact_id FROM sales_intelligence.recommendations WHERE organization_id='$ORG_D' AND status='OPEN';")"
item "A3 E tier Nurture -> NURTURE (R03)" "NURTURE" "$(acao_de "$ORG_E")"
item "A3 E motivo TIER_NURTURE" 1 "$(motivo_de "$ORG_E" | grep -c 'TIER_NURTURE')"
item "A3 F contato bloqueado -> NURTURE (R01)" "NURTURE" "$(acao_de "$ORG_F")"
item "A3 F motivo COMPLIANCE_SEM_CANAL" 1 "$(motivo_de "$ORG_F" | grep -c 'COMPLIANCE_SEM_CANAL')"
item "A3 F compliance vence a falta de pesquisa (ordem)" "0|NURTURE" "$(conta "SELECT COUNT(*) FROM sales_intelligence.research_runs WHERE organization_id='$ORG_F';")|$(acao_de "$ORG_F")"
item "A3 G dor rejeitada sem sinal -> DISQUALIFY (R02)" "DISQUALIFY" "$(acao_de "$ORG_G")"
item "A3 H sem pesquisa -> RESEARCH_MORE (R04)" "RESEARCH_MORE" "$(acao_de "$ORG_H")"
item "A3 I nao ha decisor -> FIND_DECISION_MAKER (R10)" "FIND_DECISION_MAKER" "$(acao_de "$ORG_I")"
item "A3 I contato_id nulo (nao inventa contato)" 1 "$(conta "SELECT (contact_id IS NULL)::int FROM sales_intelligence.recommendations WHERE organization_id='$ORG_I' AND status='OPEN';")"
item "A3 J decisor sem e-mail -> PREPARE_LINKEDIN (R11)" "PREPARE_LINKEDIN" "$(acao_de "$ORG_J")"
item "A3 J contato escolhido e o decisor de LinkedIn" "$CT_J" "$(conta "SELECT contact_id FROM sales_intelligence.recommendations WHERE organization_id='$ORG_J' AND status='OPEN';")"
item "A3 cada empresa com UMA recomendacao aberta" 9 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE status='OPEN' AND organization_id IN ('$ORG_B','$ORG_C','$ORG_D','$ORG_E','$ORG_F','$ORG_G','$ORG_H','$ORG_I','$ORG_J');")"

# ---------------------------------------------------------------------------------------
# A4/A5 — sem registro TIER RECUSA; empresa inexistente RECUSA
# ---------------------------------------------------------------------------------------
SAIDA4=$(rodar "$CID2" --organizacao "$ORG_K")
echo "$SAIDA4" | sed 's/^/  /' | head -4
item "A6 sem TIER RECUSADA" 1 "$(echo "$SAIDA4" | grep -c 'veredito=RECUSADA motivo=SEM_TIER')"
item "A6 sem TIER: zero recomendacao" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_K';")"
item "A6 auditoria registrou a recusa" "REJECTED" "$(conta "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id='$CID2' AND organization_id='$ORG_K';")"
SAIDA5=$(rodar "$CID2" --organizacao "$ORG_FANTASMA")
item "A7 empresa inexistente RECUSADA" 1 "$(echo "$SAIDA5" | grep -c 'veredito=RECUSADA motivo=ORGANIZACAO_NAO_ENCONTRADA')"
item "A7 nada gravado para a fantasma" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_FANTASMA';")"

# ---------------------------------------------------------------------------------------
# A8 — prod recusado sem escrita; --planejar e --regras sem conexao
# ---------------------------------------------------------------------------------------
cd "$RAIZ" && python3 "$MODULO" --ambiente prod --organizacao "$ORG_A" --prefixo "$PREFIXO" --raiz "$RAIZ" >/dev/null 2>&1
item "A3 prod recusado (exit 4)" 4 "$?"
item "A3 prod nao gravou recomendacao" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
cd "$RAIZ" && python3 "$MODULO" --planejar \
  --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" --raiz "$RAIZ" >"$TRABALHO/planejar.json" 2>&1
item "A3 --planejar exit 0 sem conexao" 0 "$?"
cd "$RAIZ" && python3 "$MODULO" --regras --raiz "$RAIZ" >"$TRABALHO/regras.json" 2>&1
item "A3 --regras exit 0 sem conexao" 0 "$?"
item "A3 --regras traz as 12 regras" 12 "$(grep -c '"motivo"' "$TRABALHO/regras.json")"
item "A3 --regras cobre as 9 acoes do contrato" 1 "$(grep -c '"acoes_do_contrato_sem_regra": \[\]' "$TRABALHO/regras.json")"
item "A3 --planejar declara LLM nao executado" 1 "$(grep -c '"executado": false' "$TRABALHO/planejar.json")"

# ---------------------------------------------------------------------------------------
# A10 — evidencia nova gera recomendacao NOVA e SUPERSEDE a anterior
# ---------------------------------------------------------------------------------------
interacao "$ORG_A" 0 "INBOUND" "POSITIVO"
INTER_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.interactions;")
SAIDA10=$(rodar "$CID2" --organizacao "$ORG_A" --relatorio "$TRABALHO/rodada2.json")
item "A10 evidencia nova: veredito RECOMENDADA" 1 "$(echo "$SAIDA10" | grep -c '"veredito": "RECOMENDADA"')"
item "A10 acao nova CREATE_MEETING" 1 "$(echo "$SAIDA10" | grep -c 'acao=CREATE_MEETING')"
item "A10 duas recomendacoes no historico" 2 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "A10 uma aberta" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND status='OPEN';")"
item "A10 anterior SUPERSEDED" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND status='SUPERSEDED';")"
item "A10 historico preservado (SEND_EMAIL intacta)" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND action='SEND_EMAIL' AND status='SUPERSEDED';")"
item "A10 id novo, nao reescrita" 2 "$(conta "SELECT COUNT(DISTINCT id) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"

# ---------------------------------------------------------------------------------------
# A11 — a rodada nao toca nada fora de recommendations/agent_runs
# ---------------------------------------------------------------------------------------
item "A11 contacts intactos" "$CONTATOS_ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.contacts;")"
item "A11 interactions intactas" "$INTER_ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.interactions;")"
item "A11 organizations intactas" "$ORGS_ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.organizations;")"
item "A11 registro TIER intacto" "$DIGITAL_TIER" "$(conta "SELECT md5(string_agg(entity_id || ':' || COALESCE(request_payload->>'tier',''), ',' ORDER BY id)) FROM sales_intelligence.sync_events WHERE operation='TIER';")"
item "A11 nenhum sync_events novo (integracao e W3/W6)" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE operation <> 'TIER';")"
item "A11 nenhuma linha em scores" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"
item "A11 nenhuma outbox (evento e W3/W6)" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.outbox_events;")"

# ---------------------------------------------------------------------------------------
# A9 — desfazer: dry-run nao apaga; --confirmo apaga SO as recomendacoes da rodada
# ---------------------------------------------------------------------------------------
ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations;")
rodar "$CID1" --desfazer "$CID1" >"$TRABALHO/desfazer-dry.json" 2>&1
item "A9 dry-run nao apaga" "$ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations;")"
item "A9 dry-run informa quantas seriam apagadas" 1 "$(grep -c '"seriam_apagadas": 1' "$TRABALHO/desfazer-dry.json")"
rodar "$CID1" --desfazer "$CID1" --confirmo >"$TRABALHO/desfazer.json" 2>&1
item "A9 --confirmo apagou so a recomendacao da rodada 1" "$((ANTES - 1))" "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations;")"
item "A9 auditoria preservada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
rodar "$CID2" --desfazer "ffffffff-ffff-4fff-8fff-ffffffffffff" --confirmo >/dev/null 2>&1
item "A9 rodada inexistente nao apaga" "$((ANTES - 1))" "$(conta "SELECT COUNT(*) FROM sales_intelligence.recommendations;")"

# ---------------------------------------------------------------------------------------
# A12 — dente por vinculo: mutacoes na COPIA do modulo reprovam o item esperado
# ---------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente (mutacoes em copia)"
  D=$(mktemp -d "$TRABALHO/dente.XXXXXX")
  while IFS='|' read -r nome de para item_esperado; do
    [ -z "$nome" ] && continue
    if ! grep -qF "$de" "$MODULO"; then
      echo "FALHOU dente $nome: ancora ausente"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); continue
    fi
    python3 - "$MODULO" "$D/mut_$nome.py" "$de" "$para" <<'PY'
import sys, pathlib
origem, destino, de, para = sys.argv[1:5]
texto = pathlib.Path(origem).read_text(encoding="utf-8")
assert texto.count(de) >= 1, "ancora ausente"
pathlib.Path(destino).write_text(texto.replace(de, para, 1), encoding="utf-8")
PY
    SAIDA=$(TRE_NBA_CONTAINER="pg-nba-dente" TRE_NBA_TRABALHO="$D/t$nome" \
      bash "$0" --codigo "$D/mut_$nome.py" --raiz "$RAIZ" 2>&1) </dev/null
    if echo "$SAIDA" | grep -qF "FALHOU $item_esperado " && ! echo "$SAIDA" | grep -qF "ACEITE_NBA_001_OK"; then
      echo "OK     dente $nome: reprovou o item esperado ($item_esperado)"
      ITENS_OK=$((ITENS_OK + 1))
    else
      echo "FALHOU dente $nome: NAO reprovou o item esperado $item_esperado"
      ITENS_FALHOU=$((ITENS_FALHOU + 1))
    fi
  done <<'LISTA'
sem-supersessao|SET status = {lit(STATUS_SUPERSEDIDA)}|SET status = status|A10 anterior SUPERSEDED
sem-idempotencia|return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NBA_VERSION}:{organization_id}:{entrada_hash}"))|return str(uuid.uuid4())|A2 replay: continua 1 recomendacao
sem-checagem-de-tier|if not fatos.get("tier"):|if False:|A6 sem TIER RECUSADA
primeira-regra-sempre|if all(avaliar_condicao(fatos, c) for c in regra["quando"]):|if True:|A1 acao SEND_EMAIL na saida
ordem-invertida|for regra in politica["regras"]:|for regra in list(politica["regras"])[::-1]:|A3 B abordada ontem -> WAIT (R08)
contato-bloqueado-ignorado|c.email IS NOT NULL AND c.do_not_contact IS NOT TRUE|TRUE AND TRUE|A3 F contato bloqueado -> NURTURE (R01)
LISTA
  rm -rf "$D"
fi

echo
echo "== ACEITE $ITENS_OK OK / $ITENS_FALHOU FALHOU"
if [ "$ITENS_FALHOU" -eq 0 ]; then echo "ACEITE_NBA_001_OK"; exit 0; fi
echo "ACEITE_NBA_001_FALHOU"; exit 1
