#!/usr/bin/env bash
# =============================================================================================
# ACEITE E2E — TRE-W6-E06-T01 (atualizar Odoo a partir das respostas)
#
# Roda NA VPS (ADR-0008: e' la' que vive o Docker do TRE). Pontas REAIS:
#   * PostgreSQL descartavel `pg-e06-acc` com a migration 0001 aplicada e fixtures de respostas
#     ja' classificadas (a materia-prima do card anterior, W6-E05-T01);
#   * STUB local da API controlada do Odoo em 127.0.0.1 (o dev nao tem chave de API do Odoo:
#     a prova contra `odoo-dev` e' do card E2E W6-E07-T01, com credencial do Sales AI);
#   * o componente de verdade (`hermes/agentes/respostas/atualizacao_odoo.py`) e a trilha em
#     `sync_events`.
#
# Nada em producao: prod e' medido e RECUSA (exit 4). O container e' descartavel e sai no fim.
#
#   bash scripts/agentes/teste_atualizacao_odoo_aceite.sh [BASE]
# =============================================================================================
set -uo pipefail

BASE="${1:-/tmp/tre-e06t01-$$}"
PG="pg-e06-acc"
PORTA_API=8799
RACIOCINIO_LOG="$BASE/aceite.out"

OK=0
FALHOU=0
mkdir -p "$BASE"
exec > >(tee "$RACIOCINIO_LOG") 2>&1

item() { # item "<nome>" <rc>
  if [ "$2" -eq 0 ]; then OK=$((OK+1)); echo "OK $OK. $1"; else FALHOU=$((FALHOU+1)); echo "FALHOU $FALHOU. $1"; fi
}
veredito() {
  echo
  if [ "$FALHOU" -eq 0 ]; then echo "ACEITE_ATUALIZACAO_ODOO_RESPOSTAS_001_OK ($OK itens, 0 falhas)"; exit 0; fi
  echo "ACEITE_ATUALIZACAO_ODOO_RESPOSTAS_001_FALHOU ($OK OK, $FALHOU falhas)"; exit 1
}
limpar() {
  [ -n "${STUB_PID:-}" ] && kill "$STUB_PID" 2>/dev/null
  docker rm -f "$PG" >/dev/null 2>&1
}
trap limpar EXIT

cd "$(dirname "$0")/../.." || exit 1
echo "== 0. pre-requisitos =="
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
command -v docker  >/dev/null 2>&1; item "docker disponivel (VPS do TRE)" $?
docker image inspect postgres:16 >/dev/null 2>&1; item "imagem postgres:16 presente" $?
[ -f db/migrations/0001_sales_intelligence_v1.sql ]; item "migration 0001 no checkout" $?
[ -f hermes/agentes/respostas/atualizacao_odoo.py ]; item "componente no checkout" $?

PIN=dev
psql_q() { docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -t -A -c "$1" 2>/dev/null; }

echo "== 1. PostgreSQL descartavel + migration 0001 =="
docker rm -f "$PG" >/dev/null 2>&1
docker run -d --name "$PG" -e POSTGRES_PASSWORD="$PIN" -e POSTGRES_USER=postgres postgres:16 >/dev/null 2>&1
item "container $PG subiu" $?
PRONTO=1
# o cluster oficial sobe um servidor TEMPORARIO na inicializacao: esperar 'ready' DUAS vezes
for _ in $(seq 1 30); do
  if docker logs "$PG" 2>&1 | grep -c "ready to accept connections" | grep -q "^2$"; then PRONTO=0; break; fi
  sleep 1
done
item "cluster respondeu 'ready' duas vezes (sem corrida de boot)" $PRONTO
for _ in $(seq 1 20); do
  docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE sales_ai LOGIN PASSWORD '$PIN' CREATEDB SUPERUSER;" >/dev/null 2>&1 && break
  sleep 1
done
item "role sales_ai criado" $?
docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE sales_intelligence OWNER sales_ai;" >/dev/null 2>&1
item "database sales_intelligence criado" $?
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q -v ON_ERROR_STOP=1 \
  < db/migrations/0001_sales_intelligence_v1.sql >"$BASE/migration.out" 2>&1
item "migration 0001 aplicada" $?
TABELAS=$(psql_q "select count(*) from information_schema.tables where table_schema='sales_intelligence';")
item "12 tabelas no schema sales_intelligence (medido: $TABELAS)" $([ "$TABELAS" = "12" ]; echo $?)

echo "== 2. fixtures: respostas JA classificadas (W6-E05) + vinculos de CRM =="
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q -v ON_ERROR_STOP=1 >>"$BASE/fixtures.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain, odoo_partner_id, status)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'Cliente Alfa LTDA', 'Cliente Alfa', 'alfa.com.br', 34, 'ACTIVE');
INSERT INTO sales_intelligence.contacts (id, organization_id, odoo_partner_id, full_name, email, do_not_contact)
VALUES ('bbbbbbbb-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000001', 12, 'Marina Alfa', 'marina@alfa.com.br', false),
       ('bbbbbbbb-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000001', NULL, 'Sem Espelho', 'sem@alfa.com.br', false);
INSERT INTO sales_intelligence.interactions
  (id, organization_id, contact_id, odoo_lead_id, channel, direction, interaction_type, occurred_at,
   subject, content_summary, sentiment, intent, response_category, ai_confidence)
VALUES
  ('11111111-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000001',
   'bbbbbbbb-0000-0000-0000-000000000001', 9001, 'email', 'INBOUND', 'EMAIL_RESPOSTA', NOW(),
   'Podemos conversar?', 'Lead pediu conversa', 'POSITIVO', 'PEDIDO_DE_CONVERSA', 'INTERESSE', 0.7500),
  ('11111111-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000001',
   'bbbbbbbb-0000-0000-0000-000000000001', 9002, 'email', 'INBOUND', 'EMAIL_RESPOSTA', NOW(),
   'Remova meu contato', 'Pedido de descadastro', 'NEGATIVO', 'DESCADASTRO', 'OPT_OUT', 0.9000),
  ('11111111-0000-0000-0000-000000000003', 'aaaaaaaa-0000-0000-0000-000000000001',
   'bbbbbbbb-0000-0000-0000-000000000001', 9003, 'email', 'INBOUND', 'EMAIL_RESPOSTA', NOW(),
   'Mail delivery failed', 'Bounce', 'NEGATIVO', 'FALHA_DE_ENTREGA', 'BOUNCE', 0.9700),
  ('11111111-0000-0000-0000-000000000004', 'aaaaaaaa-0000-0000-0000-000000000001',
   'bbbbbbbb-0000-0000-0000-000000000002', NULL, 'email', 'INBOUND', 'EMAIL_RESPOSTA', NOW(),
   'Quero saber mais', 'Interesse sem lead no CRM', 'POSITIVO', 'PEDIDO_DE_CONVERSA', 'INTERESSE', 0.7500),
  ('11111111-0000-0000-0000-000000000005', 'aaaaaaaa-0000-0000-0000-000000000001',
   'bbbbbbbb-0000-0000-0000-000000000001', 9004, 'email', 'INBOUND', 'EMAIL_RESPOSTA', NOW(),
   'Sem interesse no momento', 'Recusa educada', 'NEGATIVO', 'RECUSA', 'SEM_INTERESSE', 0.8000);
SQL
item "fixtures inseridas (5 respostas, 1 sem lead, 1 bounce)" $?
LIDAS=$(psql_q "select count(*) from sales_intelligence.interactions;")
item "5 interacoes na materia-prima (medido: $LIDAS)" $([ "$LIDAS" = "5" ]; echo $?)

cat >"$BASE/leads.json" <<'JSON'
{"9001": {"id": 9001, "name": "Cliente Alfa — oportunidade", "tf_opportunity_id": "0f0f0f0f-1111-2222-3333-444455556666", "tf_next_best_action": null},
 "9002": {"id": 9002, "name": "Cliente Alfa — opt-out", "tf_opportunity_id": "0f0f0f0f-1111-2222-3333-444455556667", "tf_next_best_action": null},
 "9004": {"id": 9004, "name": "Cliente Alfa — recusa", "tf_opportunity_id": "0f0f0f0f-1111-2222-3333-444455556668", "tf_next_best_action": null}}
JSON

echo "== 3. stub da API controlada em loopback (mesmo envelope da politica v1.3.0) =="
CHAVE="chave-aceite-$(openssl rand -hex 8)"
export TRE_ODOO_STUB_CHAVE="$CHAVE" TRE_ODOO_STUB_REGISTRO="$BASE/stub.jsonl" TRE_ODOO_STUB_LEADS="$BASE/leads.json"
: >"$BASE/stub.jsonl"
python3 scripts/agentes/stub-odoo-api-dev.py --porta "$PORTA_API" >"$BASE/stub.saida" 2>&1 &
STUB_PID=$!
for _ in $(seq 1 30); do
  python3 -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:$PORTA_API/tf/api/v1/sistema_capacidades?tf.api.ambiente=dev', data=b'{}', headers={'Authorization': 'Bearer $CHAVE', 'Content-Type': 'application/json'}), timeout=2)" 2>/dev/null && break
  sleep 0.3
done
item "stub respondeu em 127.0.0.1:$PORTA_API" $?

export TRE_AMBIENTE=dev
export TRE_ODOO_API_URL="http://127.0.0.1:$PORTA_API"
export TRE_ODOO_API_KEY="$CHAVE"
export TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA=1
export TRE_ODOO_RESPOSTAS_PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
export TRE_ODOO_RESPOSTAS_CORRELACAO="aceite-e06-t01"
COMPONENTE="python3 hermes/agentes/respostas/atualizacao_odoo.py"

echo "== 4. guardas de ambiente (medidas, nao presumidas) =="
TRE_AMBIENTE=prod $COMPONENTE --propagar --confirmo >"$BASE/guarda-prod.out" 2>&1
item "prod RECUSA por desenho (exit 4)" $([ $? -eq 4 ]; echo $?)
TRE_ODOO_API_URL="http://odoo-dev.interno:8069" $COMPONENTE --propagar --confirmo >"$BASE/guarda-host.out" 2>&1
item "dev recusa API fora de loopback (exit 3)" $([ $? -eq 3 ]; echo $?)
TRE_ODOO_API_URL="http://127.0.0.1:$PORTA_API" TRE_ODOO_RESPOSTAS_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence" \
  $COMPONENTE --propagar --confirmo >"$BASE/guarda-banco.out" 2>&1
item "dev recusa porta de banco remota (exit 3)" $([ $? -eq 3 ]; echo $?)
ANTES_GUARDA=$(psql_q "select count(*) from sales_intelligence.sync_events;")
item "nenhuma guarda escreveu trilha (medido: $ANTES_GUARDA linhas)" $([ "$ANTES_GUARDA" = "0" ]; echo $?)

echo "== 5. rodada de verdade: dry-run e depois --confirmo =="
for t in organizations contacts signals scores recommendations agent_runs outbox_events human_approvals interactions; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >>"$BASE/antes.txt"
done
$COMPONENTE --propagar --saida "$BASE/saida-dry" >"$BASE/dry.out" 2>&1
item "dry-run conclui (exit 0)" $([ $? -eq 0 ]; echo $?)
item "dry-run nao escreve trilha" $([ "$(psql_q "select count(*) from sales_intelligence.sync_events;")" = "0" ]; echo $?)
DRY_CHAMADAS=$(wc -l <"$BASE/stub.jsonl")
item "dry-run nao chama a API (medido: $DRY_CHAMADAS chamadas)" $([ "$DRY_CHAMADAS" = "0" ]; echo $?)

$COMPONENTE --propagar --confirmo --saida "$BASE/saida" >"$BASE/rodada.out" 2>&1
RC_RODADA=$?
item "rodada confirmada conclui (exit 0)" $([ $RC_RODADA -eq 0 ]; echo $?)
ATUALIZADOS=$(psql_q "select count(*) from sales_intelligence.sync_events where status='ATUALIZADO';")
item "2 respostas propagadas (INTERESSE + OPT_OUT + SEM_INTERESSE): medido $ATUALIZADOS" $([ "$ATUALIZADOS" = "3" ]; echo $?)
SEM_ATO=$(psql_q "select count(*) from sales_intelligence.sync_events where status='SEM_ATO';")
item "BOUNCE registrado como SEM_ATO (medido: $SEM_ATO)" $([ "$SEM_ATO" = "1" ]; echo $?)
SEM_VINCULO=$(psql_q "select count(*) from sales_intelligence.sync_events where status='SEM_VINCULO';")
item "INTERESSE sem lead registrado como SEM_VINCULO (medido: $SEM_VINCULO)" $([ "$SEM_VINCULO" = "1" ]; echo $?)
TOTAL_TRILHA=$(psql_q "select count(*) from sales_intelligence.sync_events;")
item "uma linha de trilha por interaction (medido: $TOTAL_TRILHA)" $([ "$TOTAL_TRILHA" = "5" ]; echo $?)

echo "== 6. o que saiu do componente (registro do stub) =="
CHAMADAS=$(wc -l <"$BASE/stub.jsonl")
item "3 atos x (ler + upsert + atividade) = 9 chamadas (medido: $CHAMADAS)" $([ "$CHAMADAS" = "9" ]; echo $?)
python3 - "$BASE/stub.jsonl" >"$BASE/stub-resumo.txt" <<'PY'
import json, sys
linhas = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
escritas = [l for l in linhas if l["operacao"] in ("oportunidade_upsert", "atividade_criar")]
print("operacoes=", sorted({l["operacao"] for l in linhas}))
print("todos_dev=", all(l["ambiente"] == "dev" for l in linhas))
print("todos_bearer_ok=", all(l["bearer_confere"] for l in linhas))
print("escritas_com_idempotency_key=", all(l["corpo"].get("idempotency_key") for l in escritas))
print("eventos=", sorted({(l["corpo"].get("parametros", {}).get("valores", {}) or {}).get("tf_last_event_type") for l in escritas if l["operacao"] == "oportunidade_upsert"}))
print("proximas_acoes=", sorted({(l["corpo"].get("parametros", {}).get("valores", {}) or {}).get("tf_next_best_action") for l in escritas if l["operacao"] == "oportunidade_upsert"}))
print("res_models=", sorted({(l["corpo"].get("parametros", {}).get("valores", {}) or {}).get("res_model") for l in escritas if l["operacao"] == "atividade_criar"}))
print("res_ids=", sorted({(l["corpo"].get("parametros", {}).get("valores", {}) or {}).get("res_id") for l in escritas if l["operacao"] == "atividade_criar"}))
PY
cat "$BASE/stub-resumo.txt"
grep -q "todos_dev= True" "$BASE/stub-resumo.txt"; item "toda chamada foi assinada como dev" $?
grep -q "todos_bearer_ok= True" "$BASE/stub-resumo.txt"; item "toda chamada foi autenticada por bearer" $?
grep -q "escritas_com_idempotency_key= True" "$BASE/stub-resumo.txt"; item "toda escrita levou idempotency_key" $?
grep -q "RESPOSTA_INTERESSE" "$BASE/stub-resumo.txt"; item "evento RESPOSTA_INTERESSE chegou ao CRM" $?
grep -q "RESPOSTA_OPT_OUT" "$BASE/stub-resumo.txt"; item "evento RESPOSTA_OPT_OUT chegou ao CRM" $?
grep -q "NAO_CONTATAR" "$BASE/stub-resumo.txt"; item "proxima acao NAO_CONTATAR chegou ao CRM" $?
grep -q "res_ids= \[12\]" "$BASE/stub-resumo.txt"; item "atividade criada no contato vinculado (res_id 12)" $?

echo "== 7. idempotencia (replay) e desfazer =="
$COMPONENTE --propagar --confirmo --saida "$BASE/saida2" >"$BASE/replay.out" 2>&1
item "replay conclui (exit 0)" $?
DEPOIS_TRILHA=$(psql_q "select count(*) from sales_intelligence.sync_events;")
item "replay nao duplica trilha (medido: $DEPOIS_TRILHA)" $([ "$DEPOIS_TRILHA" = "5" ]; echo $?)
DEPOIS_CHAMADAS=$(wc -l <"$BASE/stub.jsonl")
item "replay nao chama a API (medido: $DEPOIS_CHAMADAS chamadas)" $([ "$DEPOIS_CHAMADAS" = "9" ]; echo $?)
REPLAYS=$(grep -c "JA_ATUALIZADO" "$BASE/replay.out")
item "replay devolve JA_ATUALIZADO (medido: $REPLAYS)" $([ "$REPLAYS" -ge 3 ]; echo $?)

CHAVE_ALVO="odoo-resposta:11111111-0000-0000-0000-000000000002"
$COMPONENTE --desfazer "$CHAVE_ALVO" >"$BASE/desfazer-dry.out" 2>&1
item "desfazer em dry-run nao escreve" $([ "$(psql_q "select count(*) from sales_intelligence.sync_events where status='DESFEITO';")" = "0" ]; echo $?)
$COMPONENTE --desfazer "$CHAVE_ALVO" --confirmo >"$BASE/desfazer.out" 2>&1
item "desfazer --confirmo grava a marca DESFEITO" $([ "$(psql_q "select count(*) from sales_intelligence.sync_events where status='DESFEITO';")" = "1" ]; echo $?)
item "desfazer preserva a linha ATUALIZADO" $([ "$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='$CHAVE_ALVO' and status='ATUALIZADO';")" = "1" ]; echo $?)
$COMPONENTE --desfazer "odoo-resposta:nao-existe" --confirmo >"$BASE/desfazer-vazio.out" 2>&1
item "desfazer sem alvo RECUSA (exit 3)" $([ $? -eq 3 ]; echo $?)

echo "== 8. escopo: o que foi tocado no banco canonico =="
for t in organizations contacts signals scores recommendations agent_runs outbox_events human_approvals interactions; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >>"$BASE/depois.txt"
done
diff -q "$BASE/antes.txt" "$BASE/depois.txt" >/dev/null 2>&1
item "as 9 tabelas do sales_intelligence ficaram intactas" $?
item "interactions continua com 5 linhas (nada alterado na materia-prima)" $([ "$(psql_q "select count(*) from sales_intelligence.interactions;")" = "5" ]; echo $?)
SECRETO=$(grep -c "$CHAVE" "$BASE/rodada.out" "$BASE/saida"/* 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')
item "a chave da API nao aparece na saida do componente (medido: $SECRETO)" $([ "$SECRETO" = "0" ]; echo $?)

echo "== 9. verificacao de estrutura do repo =="
bash scripts/verificar_estrutura.sh >"$BASE/estrutura.out" 2>&1
item "portao de estrutura do repo PASS" $?
tail -3 "$BASE/estrutura.out"

veredito
