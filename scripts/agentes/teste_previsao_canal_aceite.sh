#!/usr/bin/env bash
# Aceite de ponta da previsao do melhor canal (`previsao-canal-v1`) — card TRE-W9-E03-T01.
#
# Mede o componente contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-analytics-canal` (127.0.0.1) + migration 0001 + base semeada cobrindo os tres canais,
# os bloqueios de opt-out, a organizacao sem contato, o canal fora do vocabulario e o desfecho do pai.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4) antes de ler contrato; porta remota RECUSA; --planejar sem banco;
#   2. suite offline (23 itens + 8 dentes) verde;
#   3. PRE-CONDICAO "dados multicanal" medida na base (2 canais com base, 8 organizacoes com interacao);
#   4. numeros conferidos A MAO: efetividade por canal, taxa-base, lift, ranking e as 7 previsoes;
#   5. OPT-OUT E' BLOQUEIO: canal bloqueado nunca e' previsto e o motivo aparece nomeado;
#   6. INTEGRACAO com o pai: o avanco por canal fecha com o relatorio do funil (sem segunda verdade);
#   7. LEITURA PURA: snapshot das 12 tabelas antes/depois igual e a transacao READ ONLY recusando escrita;
#   8. determinismo, saida sem PII e dashboard HTML auto-contido.
#   9. HIGIENE: o aceite mede o PROPRIO rastro — teardown com `docker rm -f -v` e nenhum
#      volume anonimo ORFAO novo no fim (sem container que o referencie) — o baseline de docker
#      nao pode mentir por causa do aceite.
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_previsao_canal_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w9e03t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-analytics-canal
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/agentes/analytics/previsao_canal.py"
CONTRATO="hermes/agentes/analytics/previsao-canal-v1.json"
FUNIL="hermes/agentes/analytics/funil.py"
MANTER=0
for arg in "$@"; do
  [ "$arg" = "--manter" ] && MANTER=1
done

item() { # item <nome> <0|1> [detalhe]
  if [ "$2" = "0" ]; then echo "OK    $1"; OK=$((OK+1)); else echo "FALHOU $1 ${3:-}"; FALHAS=$((FALHAS+1)); fi
}
psql_q() { docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -t -A -c "$1" 2>/dev/null; }
volumes_anonimos() { docker volume ls -qf dangling=true 2>/dev/null | grep -E '^[0-9a-f]{64}$' | sort; }
limpar() { docker rm -f -v "$PG" >/dev/null 2>&1; }
[ "$MANTER" = "1" ] || trap limpar EXIT

rm -rf "$BASE"; mkdir -p "$BASE/out"
cd "$REPO" || exit 1
ANON_ANTES="$(volumes_anonimos | tr '\n' ' ')"

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
for arquivo in "$COMPONENTE" "$CONTRATO" scripts/agentes/verificar_previsao_canal.py; do
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
if [ "$?" = "0" ] && grep -q "EMAIL" "$BASE/out/plano.out" && grep -q "opt_out_email" "$BASE/out/plano.out"; then
  item "--planejar declara o vocabulario e os bloqueios do contrato sem banco (exit 0)" 0
else
  item "--planejar declara o vocabulario e os bloqueios do contrato sem banco (exit 0)" 1 "$(tail -2 "$BASE/out/plano.out")"
fi

echo "== 2. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_previsao_canal.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_PREVISAO_CANAL_PASS" "$BASE/out/verificador.out"; then
  item "suite offline $(grep -o 'PASS ([0-9]* itens, 0 falhas)' "$BASE/out/verificador.out" | head -1) + $(grep -o 'AUTOTESTE [0-9]*/[0-9]* mutacoes detectadas' "$BASE/out/verificador.out" | head -1)" 0
else
  item "suite offline verde com autoteste" 1 "$(tail -3 "$BASE/out/verificador.out")"
fi

echo "== 3. PostgreSQL descartavel + migration 0001"
docker rm -f -v "$PG" >/dev/null 2>&1
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

echo "== 4. base semeada: 8 organizacoes, 3 canais, bloqueios de opt-out e desfecho"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source) VALUES
 ('00000001-0000-0000-0000-000000000000','Empresa Um','um.test','DISCOVERED','SITE'),
 ('00000002-0000-0000-0000-000000000000','Empresa Dois','dois.test','DISCOVERED','SITE'),
 ('00000003-0000-0000-0000-000000000000','Empresa Tres','tres.test','DISCOVERED','SITE'),
 ('00000004-0000-0000-0000-000000000000','Empresa Quatro','quatro.test','DISCOVERED','SITE'),
 ('00000005-0000-0000-0000-000000000000','Empresa Cinco','cinco.test','DISCOVERED','META'),
 ('00000006-0000-0000-0000-000000000000','Empresa Seis','seis.test','DISCOVERED','SITE'),
 ('00000007-0000-0000-0000-000000000000','Empresa Sete','sete.test','DISCOVERED','SITE'),
 ('00000008-0000-0000-0000-000000000000','Empresa Oito','oito.test','DISCOVERED','SITE');
-- contatos: a organizacao 4 NAO tem contato (lacuna declarada: sem bloqueio e sem preferencia)
-- org2 e org7 tem opt_out_email; org5 tem opt_out_whatsapp; org3 tem do_not_contact (bloqueia TUDO)
INSERT INTO sales_intelligence.contacts (id, organization_id, first_name, last_name, email, preferred_channel,
                                         do_not_contact, opt_out_email, opt_out_whatsapp) VALUES
 ('000000f1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','A','Um','a@um.test','WHATSAPP',FALSE,FALSE,FALSE),
 ('000000f2-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','B','Dois','b@dois.test',NULL,FALSE,TRUE,FALSE),
 ('000000f3-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','C','Tres','c@tres.test',NULL,TRUE,FALSE,FALSE),
 ('000000f5-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','E','Cinco','e@cinco.test',NULL,FALSE,FALSE,TRUE),
 ('000000f6-0000-0000-0000-000000000000','00000006-0000-0000-0000-000000000000','F','Seis','f@seis.test','EMAIL',FALSE,FALSE,FALSE),
 ('000000f7-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','G','Sete','g@sete.test',NULL,FALSE,TRUE,FALSE),
 ('000000f8-0000-0000-0000-000000000000','00000008-0000-0000-0000-000000000000','H','Oito','h@oito.test',NULL,FALSE,FALSE,FALSE);
-- EMAIL aborda org1..org4 (4 organizacoes) | WHATSAPP aborda org5..org7 (3) | LINKEDIN so' org2 e org8 (2, sem base)
INSERT INTO sales_intelligence.interactions (id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, response_category) VALUES
 ('000000e1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000e2-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-02T10:00:00+00',NULL),
 ('000000e3-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA','2026-09-03T10:00:00+00','INTERESSE'),
 ('000000e4-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000',NULL,'SMS','OUTBOUND','OUTBOUND_SMS','2026-09-04T10:00:00+00',NULL),
 ('000000e5-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','000000f2-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000e6-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','000000f2-0000-0000-0000-000000000000','LINKEDIN','OUTBOUND','OUTBOUND_LINKEDIN','2026-09-02T10:00:00+00',NULL),
 ('000000e7-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000e8-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-02T10:00:00+00',NULL),
 ('000000e9-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-03T10:00:00+00',NULL),
 ('000000ea-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA','2026-09-04T10:00:00+00','INTERESSE'),
 ('000000eb-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA','2026-09-05T10:00:00+00','INTERESSE'),
 ('000000ec-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000',NULL,'EMAIL','INTERNAL','NOTA','2026-09-06T10:00:00+00',NULL),
 ('000000ed-0000-0000-0000-000000000000','00000004-0000-0000-0000-000000000000',NULL,'EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000ee-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','000000f5-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-01T10:00:00+00',NULL),
 ('000000ef-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','000000f5-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-02T10:00:00+00',NULL),
 ('000000f0-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','000000f5-0000-0000-0000-000000000000','WHATSAPP','INBOUND','RESPOSTA','2026-09-03T10:00:00+00','INTERESSE'),
 ('00000011-0000-0000-0000-000000000000','00000006-0000-0000-0000-000000000000','000000f6-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-01T10:00:00+00',NULL),
 ('00000012-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','000000f7-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-01T10:00:00+00',NULL),
 ('00000013-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','000000f7-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-02T10:00:00+00',NULL),
 ('00000014-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','000000f7-0000-0000-0000-000000000000','WHATSAPP','INBOUND','RESPOSTA','2026-09-03T10:00:00+00',NULL),
 ('00000015-0000-0000-0000-000000000000','00000008-0000-0000-0000-000000000000','000000f8-0000-0000-0000-000000000000','LINKEDIN','OUTBOUND','OUTBOUND_LINKEDIN','2026-09-01T10:00:00+00',NULL);
-- desfecho pela trilha Odoo -> PostgreSQL: org1 Reuniao, org3 Won, org5 Reuniao, org6 Lost, org7 Proposta
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, request_payload) VALUES
 ('00000091-0000-0000-0000-000000000000','crm.lead','00000001-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:1','COMPLETED','{"payload":{"estagio_novo":"Reunião"}}'),
 ('00000093-0000-0000-0000-000000000000','crm.lead','00000003-0000-0000-0000-000000000000','odoo','postgres','OPPORTUNITY_WON','odoo:won:3','COMPLETED','{"payload":{"valor":1000}}'),
 ('00000095-0000-0000-0000-000000000000','crm.lead','00000005-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:5','COMPLETED','{"payload":{"estagio_novo":"Reunião"}}'),
 ('00000096-0000-0000-0000-000000000000','crm.lead','00000006-0000-0000-0000-000000000000','odoo','postgres','OPPORTUNITY_LOST','odoo:lost:6','COMPLETED','{"payload":{"motivo":"preco"}}'),
 ('00000097-0000-0000-0000-000000000000','crm.lead','00000007-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:7','COMPLETED','{"payload":{"estagio_novo":"Proposta"}}');
SQL
item "base semeada (organizations/contacts/interactions/sync_events)" $?

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
  psql_q "select md5(string_agg(x::text, ',')) from sales_intelligence.interactions x"
}
ANTES=$(snapshot | tr '\n' ' ')

echo "== 6. rodada do componente (dev, porta de banco local) + prova de leitura pura"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out" >"$BASE/out/previsao.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/previsao-canal.json" ]; then
  item "componente gerou o relatorio JSON + HTML (exit 0)" 0
else
  item "componente gerou o relatorio JSON + HTML (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/previsao.out")"
fi
DEPOIS=$(snapshot | tr '\n' ' ')
if [ -n "$ANTES" ] && [ "$ANTES" = "$DEPOIS" ]; then
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 0
else
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 1 "antes=$ANTES depois=$DEPOIS"
fi
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q \
  -c "SET default_transaction_read_only = on" \
  -c "INSERT INTO sales_intelligence.interactions (id, organization_id, channel, direction, occurred_at) VALUES ('00000000-0000-0000-0000-0000000000ff','00000001-0000-0000-0000-000000000000','EMAIL','OUTBOUND',NOW())" \
  >"$BASE/out/readonly.out" 2>&1
if [ $? -ne 0 ] && grep -qi "read-only" "$BASE/out/readonly.out"; then
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 0
else
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 1 "$(tail -2 "$BASE/out/readonly.out")"
fi
NAO_ENTROU=$(psql_q "select count(*) from sales_intelligence.interactions where id='00000000-0000-0000-0000-0000000000ff';")
if [ "$NAO_ENTROU" = "0" ]; then
  item "escrita recusada nao deixou linha" 0
else
  item "escrita recusada nao deixou linha" 1
fi

echo "== 7. numeros conferidos A MAO sobre o relatorio"
RC_ASSERT=0
python3 - "$BASE/out/previsao-canal.json" >"$BASE/out/assert.out" 2>&1 <<'PY' || RC_ASSERT=$?
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
def perto(a, b, tol=0.01):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol
canais = {c["canal"]: c for c in rel["por_canal"]}
pre = rel["pre_condicao_dados_multicanal"]
prev = {p["organization_id"]: p for p in rel["previsoes"]}
def O(i):
    return "000000%02d-0000-0000-0000-000000000000" % i

item("pre-condicao 'dados multicanal' ATENDIDA: 2 canais suficientes e 8 organizacoes com interacao",
     pre["atendida"] is True and pre["canais_suficientes"] == 2 and pre["canais_com_base"] == 3
     and pre["organizacoes_com_interacao"] == 8 and pre["faltando"] == [], pre)
item("efetividade por canal: EMAIL 4 abordadas 50,00% | WHATSAPP 3 abordadas 100,00% | LINKEDIN sem base (2)",
     canais["EMAIL"]["organizacoes_abordadas"] == 4 and canais["EMAIL"]["avanco_pct"] == 50.0
     and canais["WHATSAPP"]["organizacoes_abordadas"] == 3 and canais["WHATSAPP"]["avanco_pct"] == 100.0
     and canais["LINKEDIN"]["base_suficiente"] is False
     and canais["LINKEDIN"]["organizacoes_abordadas"] == 2,
     {k: (v["organizacoes_abordadas"], v["avanco_pct"]) for k, v in canais.items()})
item("taxa-base 62,50% e lift EMAIL 0,80x / WHATSAPP 1,60x",
     rel["resumo"]["taxa_base_avanco_pct"] == 62.5 and canais["EMAIL"]["lift_avanco"] == 0.8
     and canais["WHATSAPP"]["lift_avanco"] == 1.6,
     (rel["resumo"]["taxa_base_avanco_pct"], canais["EMAIL"]["lift_avanco"], canais["WHATSAPP"]["lift_avanco"]))
item("taxa de resposta: EMAIL 3/7 = 42,86% | WHATSAPP 1/5 = 20,00%",
     canais["EMAIL"]["taxa_de_resposta_pct"] == 42.86 and canais["WHATSAPP"]["taxa_de_resposta_pct"] == 20.0,
     {k: v["taxa_de_resposta_pct"] for k, v in canais.items()})
item("ranking de canais elegiveis: WHATSAPP (100,00%) antes de EMAIL (50,00%)",
     rel["ranking_de_canais"] == ["WHATSAPP", "EMAIL"]
     and rel["resumo"]["canal_mais_efetivo"] == "WHATSAPP"
     and rel["resumo"]["avanco_do_canal_mais_efetivo_pct"] == 100.0, rel["ranking_de_canais"])
item("7 previsoes emitidas: WHATSAPP 6 e EMAIL 1",
     rel["resumo"]["previsao_emitida"] is True and rel["resumo"]["organizacoes_com_previsao"] == 7
     and rel["resumo"]["distribuicao_das_previsoes"] == {"EMAIL": 1, "WHATSAPP": 6}, rel["resumo"])
item("previsoes conferidas: O1/O2/O4/O6/O7/O8 -> WHATSAPP e O5 -> EMAIL",
     all(prev[O(i)]["canal_previsto"] == "WHATSAPP" for i in (1, 2, 4, 6, 7, 8))
     and prev[O(5)]["canal_previsto"] == "EMAIL"
     and all(prev[O(i)]["empate_desfeito_por"] == "taxa_de_avanco" for i in (1, 4, 6, 8)),
     {k[:8]: (v["canal_previsto"], v["empate_desfeito_por"]) for k, v in prev.items()})
item("OPT-OUT E' BLOQUEIO: O2/O7 bloqueados em EMAIL (opt_out_email), O5 bloqueado em WHATSAPP",
     prev[O(2)]["canais_bloqueados"] == [{"canal": "EMAIL", "motivo": "opt_out_email"}]
     and prev[O(7)]["canais_bloqueados"] == [{"canal": "EMAIL", "motivo": "opt_out_email"}]
     and prev[O(5)]["canais_bloqueados"] == [{"canal": "WHATSAPP", "motivo": "opt_out_whatsapp"}],
     {k[:8]: v["canais_bloqueados"] for k, v in prev.items()})
item("do_not_contact bloqueia TODOS os canais: O3 fora das previsoes (3 canais bloqueados)",
     O(3) not in prev and rel["lacunas"]["canal_bloqueado_por_do_not_contact"] == 3
     and rel["lacunas"]["organizacao_sem_canal_elegivel"] == 1, rel["lacunas"])
item("lacunas nomeadas: SMS fora do vocabulario=1, direcao estranha=1, inbound sem classe=1, sem contato=1",
     rel["lacunas"]["canal_fora_do_vocabulario"] == {"SMS": 1}
     and rel["lacunas"]["direcao_fora_do_vocabulario"] == 1
     and rel["lacunas"]["inbound_sem_classificacao"] == 1
     and rel["lacunas"]["organizacao_sem_contato"] == 1
     and rel["lacunas"]["canal_sem_base_suficiente"] == ["LINKEDIN"], rel["lacunas"])
item("contrato, dependencia e tamanho da base declarados no relatorio",
     rel["contrato"]["versao"] == "previsao-canal-v1" and len(rel["contrato"]["sha256"]) == 64
     and rel["dependencia"]["contrato"] == "funil-v1"
     and rel["dependencia"]["funcao_do_desfecho"] == "alcance_por_organizacao"
     and rel["base"]["organizacoes"] == 8 and len(rel["lacunas_declaradas"]) >= 5
     and rel["janela"] == {"desde": None, "ate": None}, rel["dependencia"])
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/assert.out"
if [ "$RC_ASSERT" = "0" ]; then
  item "bloco de numeros conferidos a mao rodou ate' o fim (exit 0)" 0
else
  item "bloco de numeros conferidos a mao rodou ate' o fim (exit 0)" 1 "$(tail -2 "$BASE/out/assert.out")"
fi
OK=$((OK + $(grep -c "^OK    " "$BASE/out/assert.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/assert.out")))

echo "== 8. INTEGRACAO com o pai: relatorio do funil sobre a MESMA base"
python3 "$FUNIL" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out/pai" \
  >"$BASE/out/pai.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/pai/funil.json" ]; then
  item "pai (funil.py) gera o relatorio sobre a mesma base (exit 0)" 0
else
  item "pai (funil.py) gera o relatorio sobre a mesma base (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/pai.out")"
fi
RC_INTEG=0
python3 - "$BASE/out/previsao-canal.json" "$BASE/out/pai/funil.json" >"$BASE/out/integracao.out" 2>&1 <<'PY' || RC_INTEG=$?
import json, sys
prev = json.load(open(sys.argv[1], encoding="utf-8"))
pai = json.load(open(sys.argv[2], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
estagios = {e["nome"]: e for e in pai["estagios"]}
avancos = sum(next(e for e in c["endpoints"] if e["nome"] == prev["resumo"]["endpoint_principal"])["atingiram"]
              for c in prev["por_canal"])
item("avanco por canal fecha com o funil: %d = Reunião.alcancadas (%d)"
     % (avancos, estagios["Reunião"]["alcancadas"]),
     avancos == estagios["Reunião"]["alcancadas"] == 5, (avancos, estagios["Reunião"]["alcancadas"]))
item("Won/Lost dos canais batem com o resumo do funil (1 e 1)",
     sum(c["ganharam"] for c in prev["por_canal"]) == pai["resumo"]["won"] == 1
     and sum(c["perderam"] for c in prev["por_canal"]) == pai["resumo"]["lost"] == 1,
     (pai["resumo"]["won"], pai["resumo"]["lost"]))
item("nenhuma previsao contradiz o bloqueio: canal previsto nunca esta' na lista de bloqueados",
     all(p["canal_previsto"] not in [b["canal"] for b in p["canais_bloqueados"]] for p in prev["previsoes"])
     and all(p["amostra_do_canal"] >= 3 for p in prev["previsoes"]), None)
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/integracao.out"
if [ "$RC_INTEG" = "0" ]; then
  item "bloco de integracao com o pai rodou ate' o fim (exit 0)" 0
else
  item "bloco de integracao com o pai rodou ate' o fim (exit 0)" 1 "$(tail -2 "$BASE/out/integracao.out")"
fi
OK=$((OK + $(grep -c "^OK    " "$BASE/out/integracao.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/integracao.out")))

echo "== 9. determinismo, privacidade e dashboard"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out2" >"$BASE/out/previsao2.out" 2>&1
RC_PRIV=0
python3 - "$BASE/out/previsao-canal.json" "$BASE/out2/previsao-canal.json" \
  "$BASE/out/previsao-canal.html" >"$BASE/out/priv.out" 2>&1 <<'PY' || RC_PRIV=$?
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
literais = ["a@um.test", "Empresa Um", "Empresa Oito", "um.test", "a@um"]
achados = [l for l in literais if l.lower() in texto.lower()]
item("saida sem PII: nenhum e-mail, dominio ou nome de empresa da base semeada", achados == [], achados)
item("dashboard HTML auto-contido (sem http/https/script/link) com pre-condicao, canais e previsoes",
     "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
     and len(html) > 1000 and all(c["canal"] in html for c in r1["por_canal"])
     and "Pre-condicao" in html and "Ranking de canais elegiveis" in html
     and "Previsao por organizacao" in html, len(html))
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/priv.out"
if [ "$RC_PRIV" = "0" ]; then
  item "bloco de determinismo/PII/dashboard rodou ate' o fim (exit 0)" 0
else
  item "bloco de determinismo/PII/dashboard rodou ate' o fim (exit 0)" 1 "$(tail -2 "$BASE/out/priv.out")"
fi
OK=$((OK + $(grep -c "^OK    " "$BASE/out/priv.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/priv.out")))

echo "== 10. higiene: o aceite prova que NAO deixa volume anonimo novo"
# O teardown e' o MESMO do trap (`docker rm -f -v`): o aceite mede o PROPRIO rastro. Sem o `-v` o
# container descartavel deixa o volume anonimo da imagem `postgres:16` para tras (defeito t_3148dbbf:
# cada rodada de aceite vazava 1 volume e o baseline de docker mentia em silencio).
if [ "$MANTER" = "1" ]; then
  item "higiene: pulado em --manter (container mantido de proposito)" 0
else
  limpar   # a MESMA limpeza do trap: aqui o aceite mede o rastro que ELE deixa
  novos=""
  for v in $(volumes_anonimos); do
    case " $ANON_ANTES " in
      *" $v "*) ;;
      *) novos="$novos $v" ;;
    esac
  done
  qtd=$(echo $novos | wc -w)
  if [ "$qtd" = "0" ]; then
    item "higiene: nenhum volume anonimo ORFAO novo depois do teardown com -v" 0
  else
    item "higiene: nenhum volume anonimo ORFAO novo depois do teardown com -v" 1 \
      "$qtd volume(s) orfao(s) novo(s):$novos"
  fi
fi

echo
echo "RESULTADO: $([ "$FALHAS" = "0" ] && echo PASS || echo FALHOU) ($OK itens, $FALHAS falhas)"
if [ "$FALHAS" = "0" ]; then
  echo "ACEITE_PREVISAO_CANAL_OK"
  exit 0
fi
echo "ACEITE_PREVISAO_CANAL_FALHOU"
exit 1
