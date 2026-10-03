#!/usr/bin/env bash
# Aceite de ponta da efetividade do score (`efetividade-score-v1`) — card TRE-W8-E03-T01.
#
# Mede o componente contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-analytics-efet` (127.0.0.1) + migration 0001 + base semeada cobrindo todas as
# faixas, os endpoints e as lacunas + o proprio aceite do card PAI (funil, W8-E01-T01) como prova
# de que a exposicao do alcance por organizacao NAO mudou o relatorio `funil-v1`.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4) antes de ler contrato; porta remota RECUSA; --planejar sem banco;
#   2. suite offline (22 itens + 8 dentes) verde;
#   3. numeros conferidos A MAO sobre base semeada: cobertura 11/15, taxa por faixa, endpoints, lift,
#      monotonicidade (achado), adesao a formula (9/8/1), quartis por componente e lacunas nomeadas;
#   4. INTEGRACAO com o pai: os totais por faixa fecham com o resumo do funil; `--por-organizacao`
#      entrega o MESMO alcance que o relatorio usa;
#   5. LEITURA PURA: snapshot das 12 tabelas antes/depois igual e a transacao READ ONLY recusando escrita;
#   6. determinismo: duas rodadas -> mesmo hash_do_relatorio;
#   7. saida sem PII e dashboard HTML auto-contido;
#   8. REGRESSAO DO PAI: o aceite do funil (34 itens) continua verde com o funil estendido.
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_efetividade_score_aceite.sh [--manter] [--sem-pai]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w8e03t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-analytics-efet
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/agentes/analytics/efetividade_score.py"
CONTRATO="hermes/agentes/analytics/efetividade-score-v1.json"
FUNIL="hermes/agentes/analytics/funil.py"
MANTER=0; SEM_PAI=0
for arg in "$@"; do
  [ "$arg" = "--manter" ] && MANTER=1
  [ "$arg" = "--sem-pai" ] && SEM_PAI=1
done

item() { # item <nome> <0|1> [detalhe]
  if [ "$2" = "0" ]; then echo "OK    $1"; OK=$((OK+1)); else echo "FALHOU $1 ${3:-}"; FALHAS=$((FALHAS+1)); fi
}
psql_q() { docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -t -A -c "$1" 2>/dev/null; }
limpar() { docker rm -f "$PG" >/dev/null 2>&1; }
[ "$MANTER" = "1" ] || trap limpar EXIT

rm -rf "$BASE"; mkdir -p "$BASE/out"
cd "$REPO" || exit 1

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
for arquivo in "$COMPONENTE" "$CONTRATO" scripts/agentes/verificar_efetividade_score.py; do
  [ -f "$arquivo" ] && item "presente no repo: $arquivo" 0 || item "presente no repo: $arquivo" 1
done

echo "== 1. guardas de ambiente (sem banco)"
python3 "$COMPONENTE" --ambiente prod --saida "$BASE/out/prod" >"$BASE/out/prod.out" 2>&1
RC=$?
[ "$RC" = "4" ] && item "prod RECUSA por desenho (exit 4)" 0 || item "prod RECUSA por desenho (exit 4)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente prod --contrato /tmp/nao-existe.json >"$BASE/out/prod2.out" 2>&1
RC=$?
[ "$RC" = "4" ] && item "prod RECUSA antes de ler contrato (exit 4)" 0 || item "prod RECUSA antes de ler contrato (exit 4)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente dev --porta-banco "ssh root@10.0.0.1 psql" >"$BASE/out/remoto.out" 2>&1
RC=$?
[ "$RC" = "3" ] && item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 0 \
  || item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente dev --planejar >"$BASE/out/plano.out" 2>&1
if [ "$?" = "0" ] && grep -q "A+" "$BASE/out/plano.out" && grep -q "DATA_QUALITY" "$BASE/out/plano.out"; then
  item "--planejar declara faixas e pesos do Data Contract sem banco (exit 0)" 0
else
  item "--planejar declara faixas e pesos do Data Contract sem banco (exit 0)" 1 "$(tail -2 "$BASE/out/plano.out")"
fi

echo "== 2. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_efetividade_score.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_EFETIVIDADE_PASS" "$BASE/out/verificador.out"; then
  item "suite offline 22 itens + 8 dentes verde" 0
else
  item "suite offline 22 itens + 8 dentes verde" 1 "$(tail -3 "$BASE/out/verificador.out")"
fi

echo "== 3. PostgreSQL descartavel + migration 0001"
docker rm -f "$PG" >/dev/null 2>&1
docker run -d --name "$PG" -e POSTGRES_PASSWORD=dev -e POSTGRES_USER=postgres postgres:16 >/dev/null 2>&1
item "container descartavel $PG criado" $?
pronto=1
for _ in $(seq 1 60); do
  prontos=$(docker logs "$PG" 2>&1 | grep -c "database system is ready to accept connections")
  if [ "${prontos:-0}" -ge 2 ] && docker exec "$PG" psql -U postgres -d postgres -tAc "select 1" >/dev/null 2>&1; then
    pronto=0; break
  fi
  sleep 1
done
item "postgres pronto (init concluido: 2x 'ready to accept connections')" "$pronto"
criado=1
for _ in $(seq 1 15); do
  if docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE sales_ai LOGIN PASSWORD 'dev';" \
     >"$BASE/out/pg-role.out" 2>&1; then criado=0; break; fi
  sleep 2
done
item "role sales_ai criado" "$criado"
docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE sales_intelligence OWNER sales_ai;" \
  >"$BASE/out/pg-db.out" 2>&1
item "database sales_intelligence criada" $?
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q -v ON_ERROR_STOP=1 \
  < db/migrations/0001_sales_intelligence_v1.sql >"$BASE/out/migration.out" 2>&1
item "migration 0001 aplicada" $?
TABELAS=$(psql_q "select count(*) from information_schema.tables where table_schema='sales_intelligence';")
if [ "$TABELAS" = "12" ]; then
  item "schema com 12 tabelas" 0
else
  item "schema com 12 tabelas" 1 "(obtido '$TABELAS')"
fi

echo "== 4. base semeada: 15 organizacoes cobrindo as 5 faixas, os endpoints e as 4 lacunas"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source) VALUES
 ('00000001-0000-0000-0000-000000000000','Empresa Um','um.test','DISCOVERED','SITE'),
 ('00000002-0000-0000-0000-000000000000','Empresa Dois','dois.test','DISCOVERED','SITE'),
 ('00000003-0000-0000-0000-000000000000','Empresa Tres','tres.test','DISCOVERED','SITE'),
 ('00000004-0000-0000-0000-000000000000','Empresa Quatro','quatro.test','DISCOVERED','SITE'),
 ('00000005-0000-0000-0000-000000000000','Empresa Cinco','cinco.test','DISCOVERED','META'),
 ('00000006-0000-0000-0000-000000000000','Empresa Seis','seis.test','DISCOVERED','SITE'),
 ('00000007-0000-0000-0000-000000000000','Empresa Sete','sete.test','DISCOVERED','SITE'),
 ('00000008-0000-0000-0000-000000000000','Empresa Oito','oito.test','DISCOVERED','SITE'),
 ('00000009-0000-0000-0000-000000000000','Empresa Nove','nove.test','DISCOVERED','SITE'),
 ('00000010-0000-0000-0000-000000000000','Empresa Dez','dez.test','DISCOVERED','EVENTO_CAPTURA'),
 ('00000011-0000-0000-0000-000000000000','Empresa Onze','onze.test','DISCOVERED','SITE'),
 ('00000012-0000-0000-0000-000000000000','Empresa Doze','doze.test','DISCOVERED','SITE'),
 ('00000013-0000-0000-0000-000000000000','Empresa Treze','treze.test','DISCOVERED','SITE'),
 ('00000014-0000-0000-0000-000000000000','Empresa Quatorze','quatorze.test','DISCOVERED','SITE'),
 ('00000015-0000-0000-0000-000000000000','Empresa Quinze','quinze.test','DISCOVERED','SITE');
-- PRIORITY: 9 conformes + 1 divergente (09) + 5 que viram lacuna (10 ausente nao entra aqui; 11 sem versao,
-- 12 vencido, 13 fora da escala) + 14 e 15 validos
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at, valid_until) VALUES
 ('000000a1-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000000','PRIORITY',100.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000002','00000002-0000-0000-0000-000000000000','PRIORITY', 90.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000003','00000003-0000-0000-0000-000000000000','PRIORITY', 80.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000004','00000004-0000-0000-0000-000000000000','PRIORITY', 70.75,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000005','00000005-0000-0000-0000-000000000000','PRIORITY', 55.50,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000006','00000006-0000-0000-0000-000000000000','PRIORITY', 49.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000007','00000007-0000-0000-0000-000000000000','PRIORITY', 39.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000008','00000008-0000-0000-0000-000000000000','PRIORITY', 29.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000009','00000009-0000-0000-0000-000000000000','PRIORITY', 79.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000011','00000011-0000-0000-0000-000000000000','PRIORITY', 70.00,'','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000012','00000012-0000-0000-0000-000000000000','PRIORITY', 95.00,'v1','2026-01-01T12:00:00+00','2020-01-01T00:00:00+00'),
 ('000000a1-0000-0000-0000-000000000013','00000013-0000-0000-0000-000000000000','PRIORITY',150.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000014','00000014-0000-0000-0000-000000000000','PRIORITY', 55.00,'v1','2026-09-30T12:00:00+00',NULL),
 ('000000a1-0000-0000-0000-000000000015','00000015-0000-0000-0000-000000000000','PRIORITY', 66.00,'v1','2026-09-30T12:00:00+00',NULL),
 -- historico: a organizacao 2 tem um PRIORITY antigo (o mais recente vale; o resto e' historico)
 ('000000a1-0000-0000-0000-0000000000b2','00000002-0000-0000-0000-000000000000','PRIORITY', 70.00,'v1','2026-08-01T12:00:00+00',NULL);
-- componentes dos 9 conformes + 15 em v2 (versao divergente) + 14 sem DATA_QUALITY
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at) VALUES
 ('000000b1-0000-0000-0000-000000000101','00000001-0000-0000-0000-000000000000','ICP',100.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000102','00000001-0000-0000-0000-000000000000','AUTOMATION_FIT',100.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000103','00000001-0000-0000-0000-000000000000','BUYING_SIGNAL',100.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000104','00000001-0000-0000-0000-000000000000','DATA_QUALITY',100.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000201','00000002-0000-0000-0000-000000000000','ICP',90.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000202','00000002-0000-0000-0000-000000000000','AUTOMATION_FIT',90.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000203','00000002-0000-0000-0000-000000000000','BUYING_SIGNAL',90.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000204','00000002-0000-0000-0000-000000000000','DATA_QUALITY',90.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000301','00000003-0000-0000-0000-000000000000','ICP',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000302','00000003-0000-0000-0000-000000000000','AUTOMATION_FIT',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000303','00000003-0000-0000-0000-000000000000','BUYING_SIGNAL',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000304','00000003-0000-0000-0000-000000000000','DATA_QUALITY',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000401','00000004-0000-0000-0000-000000000000','ICP',85.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000402','00000004-0000-0000-0000-000000000000','AUTOMATION_FIT',70.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000403','00000004-0000-0000-0000-000000000000','BUYING_SIGNAL',60.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000404','00000004-0000-0000-0000-000000000000','DATA_QUALITY',50.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000501','00000005-0000-0000-0000-000000000000','ICP',70.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000502','00000005-0000-0000-0000-000000000000','AUTOMATION_FIT',60.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000503','00000005-0000-0000-0000-000000000000','BUYING_SIGNAL',50.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000504','00000005-0000-0000-0000-000000000000','DATA_QUALITY',5.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000601','00000006-0000-0000-0000-000000000000','ICP',60.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000602','00000006-0000-0000-0000-000000000000','AUTOMATION_FIT',50.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000603','00000006-0000-0000-0000-000000000000','BUYING_SIGNAL',40.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000604','00000006-0000-0000-0000-000000000000','DATA_QUALITY',30.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000701','00000007-0000-0000-0000-000000000000','ICP',50.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000702','00000007-0000-0000-0000-000000000000','AUTOMATION_FIT',40.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000703','00000007-0000-0000-0000-000000000000','BUYING_SIGNAL',30.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000704','00000007-0000-0000-0000-000000000000','DATA_QUALITY',20.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000801','00000008-0000-0000-0000-000000000000','ICP',40.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000802','00000008-0000-0000-0000-000000000000','AUTOMATION_FIT',30.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000803','00000008-0000-0000-0000-000000000000','BUYING_SIGNAL',20.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000804','00000008-0000-0000-0000-000000000000','DATA_QUALITY',10.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000901','00000009-0000-0000-0000-000000000000','ICP',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000902','00000009-0000-0000-0000-000000000000','AUTOMATION_FIT',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000903','00000009-0000-0000-0000-000000000000','BUYING_SIGNAL',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000904','00000009-0000-0000-0000-000000000000','DATA_QUALITY',80.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000e01','00000014-0000-0000-0000-000000000000','ICP',55.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000e02','00000014-0000-0000-0000-000000000000','AUTOMATION_FIT',50.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000e03','00000014-0000-0000-0000-000000000000','BUYING_SIGNAL',55.00,'v1','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000f01','00000015-0000-0000-0000-000000000000','ICP',66.00,'v2','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000f02','00000015-0000-0000-0000-000000000000','AUTOMATION_FIT',60.00,'v2','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000f03','00000015-0000-0000-0000-000000000000','BUYING_SIGNAL',88.00,'v2','2026-09-30T12:00:00+00'),
 ('000000b1-0000-0000-0000-000000000f04','00000015-0000-0000-0000-000000000000','DATA_QUALITY',66.00,'v2','2026-09-30T12:00:00+00');
-- desfecho: 4 e' Abordagem iniciada (OUTBOUND); 6 e' Engajamento (INBOUND classificado);
-- 1 Proposta, 2/14 Reuniao, 3 Lost, 5 Won pela trilha Odoo -> PostgreSQL
INSERT INTO sales_intelligence.interactions (id, organization_id, channel, direction, interaction_type, occurred_at, response_category) VALUES
 ('000000c1-0000-0000-0000-000000000004','00000004-0000-0000-0000-000000000000','EMAIL','OUTBOUND','ENVIO',NOW(),NULL),
 ('000000c1-0000-0000-0000-000000000006','00000006-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA',NOW(),'INTERESSE');
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, request_payload) VALUES
 ('000000d1-0000-0000-0000-000000000001','crm.lead','00000001-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:1','COMPLETED','{"payload":{"estagio_novo":"Proposta"}}'),
 ('000000d1-0000-0000-0000-000000000002','crm.lead','00000002-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:2','COMPLETED','{"payload":{"estagio_novo":"Reunião"}}'),
 ('000000d1-0000-0000-0000-000000000003','crm.lead','00000003-0000-0000-0000-000000000000','odoo','postgres','OPPORTUNITY_LOST','odoo:lost:3','COMPLETED','{"payload":{"motivo":"preco"}}'),
 ('000000d1-0000-0000-0000-000000000005','crm.lead','00000005-0000-0000-0000-000000000000','odoo','postgres','OPPORTUNITY_WON','odoo:won:5','COMPLETED','{"payload":{"valor":1000}}'),
 ('000000d1-0000-0000-0000-000000000014','crm.lead','00000014-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:14','COMPLETED','{"payload":{"estagio_novo":"Reunião"}}');
SQL
item "base semeada (organizations/scores/interactions/sync_events)" $?

echo "== 5. snapshot das 12 tabelas ANTES da rodada (prova de leitura pura)"
snapshot() {
  psql_q "select md5(string_agg(t || '=' || n, ',')) from (
    select 'organizations' t, count(*) n from sales_intelligence.organizations union all
    select 'contacts', count(*) from sales_intelligence.contacts union all
    select 'signals', count(*) from sales_intelligence.signals union all
    select 'research_runs', count(*) from sales_intelligence.research_runs union all
    select 'pain_hypotheses', count(*) from sales_intelligence.pain_hypotheses union all
    select 'scores', count(*) from sales_intelligence.scores union all
    select 'interactions', count(*) from sales_intelligence.interactions union all
    select 'recommendations', count(*) from sales_intelligence.recommendations union all
    select 'agent_runs', count(*) from sales_intelligence.agent_runs union all
    select 'outbox_events', count(*) from sales_intelligence.outbox_events union all
    select 'sync_events', count(*) from sales_intelligence.sync_events union all
    select 'human_approvals', count(*) from sales_intelligence.human_approvals) x"
  psql_q "select md5(string_agg(x::text, ',')) from sales_intelligence.scores x"
}
ANTES=$(snapshot | tr '\n' ' ')

echo "== 6. rodada do componente (dev, porta de banco local) + prova de leitura pura"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out" >"$BASE/out/efetividade.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/efetividade-score.json" ]; then
  item "componente gerou o relatorio JSON + HTML (exit 0)" 0
else
  item "componente gerou o relatorio JSON + HTML (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/efetividade.out")"
fi
DEPOIS=$(snapshot | tr '\n' ' ')
if [ -n "$ANTES" ] && [ "$ANTES" = "$DEPOIS" ]; then
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 0
else
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 1 "antes=$ANTES depois=$DEPOIS"
fi
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q \
  -c "SET default_transaction_read_only = on" \
  -c "INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version) VALUES ('00000000-0000-0000-0000-0000000000ff','00000001-0000-0000-0000-000000000000','PRIORITY',10.00,'v1')" \
  >"$BASE/out/readonly.out" 2>&1
if [ $? -ne 0 ] && grep -qi "read-only" "$BASE/out/readonly.out"; then
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 0
else
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 1 "$(tail -2 "$BASE/out/readonly.out")"
fi
NAO_ENTROU=$(psql_q "select count(*) from sales_intelligence.scores where id='00000000-0000-0000-0000-0000000000ff';")
if [ "$NAO_ENTROU" = "0" ]; then
  item "escrita recusada nao deixou linha" 0
else
  item "escrita recusada nao deixou linha" 1
fi

echo "== 7. numeros conferidos A MAO sobre o relatorio"
python3 - "$BASE/out/efetividade-score.json" >"$BASE/out/assert.out" 2>&1 <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
def perto(a, b, tol=0.01):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol
faixa = {f["faixa"]: f for f in rel["efetividade_por_faixa"]}
def ep(f, nome):
    return next(e for e in f["endpoints"] if e["nome"] == nome)

item("cobertura: 15 organizacoes, 11 com PRIORITY valido (73.33%)", rel["cobertura"] == {
    "organizacoes": 15, "com_priority_valido": 11, "taxa_pct": 73.33}, rel["cobertura"])
item("lacunas nomeadas: sem PRIORITY=1, sem versao=1, vencida=1, fora da escala=1",
     rel["lacunas"]["prioridade_ausente"] == 1 and rel["lacunas"]["prioridade_sem_versao"] == 1
     and rel["lacunas"]["prioridade_vencida"] == 1 and rel["lacunas"]["prioridade_fora_da_escala"] == 1
     and rel["lacunas"]["historico_ignorado"] == {"organizacoes": 1, "linhas": 1}, rel["lacunas"])
item("taxa de avanco por faixa: A+ 100 / A 100 / B 0 / C 100 / Nurture 0 e taxa-base 45.45%",
     {k: v["avanco_pct"] for k, v in faixa.items()} == {"A+": 100.0, "A": 100.0, "B": 0.0,
                                                        "C": 100.0, "Nurture": 0.0}
     and perto(rel["resumo"]["taxa_base_avanco_pct"], 45.45), rel["resumo"]["taxa_base_avanco_pct"])
item("Won/Lost por faixa (C 1 won; A 1 lost) e taxa de vitoria 100% / 0%",
     faixa["C"]["ganharam"] == 1 and faixa["C"]["perderam"] == 0 and perto(faixa["C"]["taxa_de_vitoria_pct"], 100.0)
     and faixa["A"]["ganharam"] == 0 and faixa["A"]["perderam"] == 1 and perto(faixa["A"]["taxa_de_vitoria_pct"], 0.0))
item("endpoints do tier: A+ Engajamento 100% / Proposta 50% / Won 0%",
     perto(ep(faixa["A+"], "Engajamento")["taxa_pct"], 100.0)
     and perto(ep(faixa["A+"], "Proposta")["taxa_pct"], 50.0)
     and perto(ep(faixa["A+"], "Won")["taxa_pct"], 0.0))
item("lift no endpoint principal: A+ 2.20x e B 0.00x", perto(faixa["A+"]["lift_avanco"], 2.2)
     and perto(faixa["B"]["lift_avanco"], 0.0))
item("ACHADO de monotonicidade: B (0%) abaixo de C (100%) -> monotonico=False com 1 violacao nomeada",
     rel["resumo"]["monotonico"] is False and len(rel["resumo"]["violacoes"]) == 1
     and rel["resumo"]["violacoes"][0]["obtido"] == "B=0.0 < C=100.0", rel["resumo"]["violacoes"])
item("base insuficiente DECLARADA: nenhuma faixa com 5 organizacoes -> conclusao nao sustentada",
     rel["resumo"]["base_suficiente_para_conclusao"] is False
     and rel["resumo"]["faixas_sem_base_suficiente"] == ["A+", "A", "B", "C", "Nurture"],
     rel["resumo"]["faixas_sem_base_suficiente"])
item("adesao a formula: 9 comparaveis, 8 conformes, 1 divergente (desvio 1.00), 88.89%",
     rel["adesao_a_formula"]["comparaveis"] == 9 and rel["adesao_a_formula"]["conformes"] == 8
     and rel["adesao_a_formula"]["divergentes"] == 1 and rel["adesao_a_formula"]["desvio_maximo"] == "1.00"
     and perto(rel["adesao_a_formula"]["taxa_de_conformidade_pct"], 88.89)
     and rel["adesao_a_formula"]["exemplos_de_divergencia"][0]["organization_id"]
         == "00000009-0000-0000-0000-000000000000", rel["adesao_a_formula"])
item("componentes fora da conta vao para lacuna: DATA_QUALITY ausente=1 e versao divergente=1",
     rel["lacunas"]["componente_ausente"] == {"DATA_QUALITY": 1} and rel["lacunas"]["versoes_divergentes"] == 1,
     (rel["lacunas"]["componente_ausente"], rel["lacunas"]["versoes_divergentes"]))
comp = {c["score_type"]: c for c in rel["por_componente"]}
item("quartis por posto: ICP Q1 66.67% / Q4 0%; DATA_QUALITY Q1 100% / Q4 50% (lift 2.0x)",
     perto(comp["ICP"]["quartis"][0]["taxa_pct"], 66.67) and perto(comp["ICP"]["quartis"][3]["taxa_pct"], 0.0)
     and perto(comp["DATA_QUALITY"]["quartis"][0]["taxa_pct"], 100.0)
     and perto(comp["DATA_QUALITY"]["quartis"][3]["taxa_pct"], 50.0)
     and perto(comp["DATA_QUALITY"]["lift_q1_vs_q4"], 2.0), comp["DATA_QUALITY"]["quartis"])
item("contrato, dependencia e tamanho da coorte declarados no relatorio",
     rel["contrato"]["versao"] == "efetividade-score-v1" and len(rel["contrato"]["sha256"]) == 64
     and rel["dependencia"]["contrato"] == "funil-v1"
     and rel["dependencia"]["funcao_do_desfecho"] == "alcance_por_organizacao"
     and len(rel["lacunas_declaradas"]) == 6 and rel["janela"] == {"desde": None, "ate": None}, rel["dependencia"])
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/assert.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/assert.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/assert.out")))

echo "== 8. INTEGRACAO com o pai: relatorio do funil + exportacao por organizacao"
python3 "$FUNIL" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out/pai" \
  --por-organizacao "$BASE/out/pai/alcance.json" >"$BASE/out/pai.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/pai/funil.json" ] && [ -f "$BASE/out/pai/alcance.json" ]; then
  item "pai (funil.py) gera o relatorio E a exportacao por organizacao (exit 0)" 0
else
  item "pai (funil.py) gera o relatorio E a exportacao por organizacao (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/pai.out")"
fi
python3 - "$BASE/out/efetividade-score.json" "$BASE/out/pai/funil.json" "$BASE/out/pai/alcance.json" \
  >"$BASE/out/integracao.out" 2>&1 <<'PY'
import json, sys
efet = json.load(open(sys.argv[1], encoding="utf-8"))
pai = json.load(open(sys.argv[2], encoding="utf-8"))
alcance = json.load(open(sys.argv[3], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
por_faixa = efet["efetividade_por_faixa"]
estagios = {e["nome"]: e for e in pai["estagios"]}
soma_tiers = sum(f["organizacoes"] for f in por_faixa)
soma_avanco = sum(next(e for e in f["endpoints"] if e["nome"] == efet["resumo"]["endpoint_principal"])["atingiram"]
                  for f in por_faixa)
soma_won = sum(f["ganharam"] for f in por_faixa)
soma_lost = sum(f["perderam"] for f in por_faixa)
item("coorte fecha com as faixas (11) e o avanco bate com `Reunião` do funil (%d)" % estagios["Reunião"]["alcancadas"],
     soma_tiers == efet["cobertura"]["com_priority_valido"] == 11 and soma_avanco == estagios["Reunião"]["alcancadas"] == 5,
     (soma_tiers, soma_avanco, estagios["Reunião"]["alcancadas"]))
item("Won/Lost por faixa batem com o resumo do funil (1 won, 1 lost) e o terminal NAO se soma",
     soma_won == pai["resumo"]["won"] == 1 and soma_lost == pai["resumo"]["lost"] == 1
     and estagios["Won"]["alcancadas"] == 1 and estagios["Lost"]["alcancadas"] == 1,
     (soma_won, soma_lost, pai["resumo"]))
item("exportacao por organizacao entrega o MESMO alcance usado no relatorio (org 1 nivel 8, org 5 com rótulo Won)",
     alcance["organizacoes"]["00000001-0000-0000-0000-000000000000"]["nivel"] == 8
     and "Won" in alcance["organizacoes"]["00000005-0000-0000-0000-000000000000"]["rotulos"]
     and len(alcance["organizacoes"]) == 15 and len(alcance["hash_do_alcance"]) == 16,
     alcance["organizacoes"]["00000001-0000-0000-0000-000000000000"])
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/integracao.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/integracao.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/integracao.out")))

echo "== 9. determinismo, privacidade e dashboard"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out2" >"$BASE/out/efetividade2.out" 2>&1
python3 - "$BASE/out/efetividade-score.json" "$BASE/out2/efetividade-score.json" \
  "$BASE/out/efetividade-score.html" >"$BASE/out/priv.out" 2>&1 <<'PY'
import json, sys
r1 = json.load(open(sys.argv[1], encoding="utf-8"))
r2 = json.load(open(sys.argv[2], encoding="utf-8"))
html = open(sys.argv[3], encoding="utf-8").read()
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
item("determinismo: duas rodadas na MESMA base e na MESMA referencia -> mesmo hash_do_relatorio",
     r1["hash_do_relatorio"] == r2["hash_do_relatorio"] and len(r1["hash_do_relatorio"]) == 64,
     (r1["hash_do_relatorio"][:16], r2["hash_do_relatorio"][:16]))
texto = json.dumps(r1, ensure_ascii=False) + html
literais = ["um@um.test", "Empresa Um", "Empresa Quinze", "@"]
achados = [l for l in literais if l.lower() in texto.lower()]
item("saida sem PII: nenhum e-mail e nenhum nome de empresa da base semeada", achados == [], achados)
item("dashboard HTML auto-contido (sem http/https/script/link) com faixas, adesão e lacunas",
     "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
     and len(html) > 1000 and all(f["faixa"] in html for f in r1["efetividade_por_faixa"])
     and "Adesão à fórmula" in html and "Efetividade por componente" in html, len(html))
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/priv.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/priv.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/priv.out")))

if [ "$SEM_PAI" = "0" ]; then
  echo "== 10. REGRESSAO DO PAI: aceite do funil (W8-E01-T01) com o funil estendido"
  # base propria do pai (o padrao /tmp/aceite-w8e01t01 pode pertencer a root de uma rodada anterior)
  mkdir -p "$BASE/pai"
  TRE_ACEITE_BASE="$BASE/pai" bash scripts/agentes/teste_funil_aceite.sh >"$BASE/out/aceite-pai.out" 2>&1
  if grep -q "ACEITE_FUNIL_OK" "$BASE/out/aceite-pai.out"; then
    item "aceite do funil continua verde com o pai estendido ($(grep -o 'PASS ([0-9]* itens, 0 falhas)' "$BASE/out/aceite-pai.out" | head -1))" 0
  else
    item "aceite do funil continua verde com o pai estendido" 1 "$(tail -3 "$BASE/out/aceite-pai.out")"
  fi
fi

echo
echo "RESULTADO: $([ "$FALHAS" = "0" ] && echo PASS || echo FALHOU) ($OK itens, $FALHAS falhas)"
if [ "$FALHAS" = "0" ]; then
  echo "ACEITE_EFETIVIDADE_SCORE_OK"
  exit 0
fi
echo "ACEITE_EFETIVIDADE_SCORE_FALHOU"
exit 1
