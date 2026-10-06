#!/usr/bin/env bash
# Aceite de ponta da conversao por segmento (`conversao-segmento-v1`) — card TRE-W8-E02-T01 (W8).
#
# Mede o componente contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-analytics-seg-acc` (nome dentro dos prefixos de dev aceitos pela guarda reusada do
# funil) + migration 0001 + a base canonica semeada.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4); porta remota RECUSA (BANCO_NAO_E_DEV); --planejar/--conferir sem banco;
#   2. suite offline (34 itens + 12 dentes) verde;
#   3. recorte por faixa de funcionarios e por tier conferido A MAO sobre base semeada;
#   4. COERENCIA COM O FUNIL: o recorte da base inteira e' IGUAL ao funil do card W8-E01-T01 na mesma
#      base (mesmos estagios, alcance, conversoes, won/lost) — prova que nao ha segunda regra de funil;
#   5. sentinelas: faixa quase-identica ao vocabulario NAO vira segmento; sem dado e' coisa diferente;
#   6. tier DERIVADO da pontuacao vigente (a mais recente), nao da maior historica;
#   7. cobertura por eixo declarada e soma dos buckets fechando com a base;
#   8. LEITURA PURA: snapshot das 12 tabelas antes/depois igual e a transacao READ ONLY recusando escrita;
#   9. determinismo: duas rodadas -> mesmo hash_do_relatorio;
#  10. saida sem PII e sem organizacao nominal, e dashboard HTML auto-contido.
#  11. HIGIENE: o aceite mede o PROPRIO rastro — teardown com `docker rm -f -v` e nenhum
#      volume anonimo ORFAO novo no fim (sem container que o referencie) — o baseline de docker
#      nao pode mentir por causa do aceite.
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_conversao_segmento_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w8e02t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-analytics-seg-acc
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/agentes/analytics/conversao_segmento.py"
CONTRATO="hermes/agentes/analytics/conversao-segmento-v1.json"
FUNIL="hermes/agentes/analytics/funil.py"
MANTER=0
[ "${1:-}" = "--manter" ] && MANTER=1

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
if [ -f "$COMPONENTE" ] && [ -f "$CONTRATO" ] && [ -f "$FUNIL" ] \
   && [ -f scripts/agentes/verificar_conversao_segmento.py ]; then
  item "componente, contrato, funil reusado e verificador presentes no repo" 0
else
  item "componente, contrato, funil reusado e verificador presentes no repo" 1
fi

echo "== 1. guardas de ambiente (sem banco)"
python3 "$COMPONENTE" --ambiente prod --saida "$BASE/out/prod" >"$BASE/out/prod.out" 2>&1
RC=$?
if [ "$RC" = "4" ] && grep -q "PRODUCAO_RECUSADA" "$BASE/out/prod.out"; then
  item "prod RECUSA por desenho (exit 4)" 0
else
  item "prod RECUSA por desenho (exit 4)" 1 "exit=$RC $(tail -1 "$BASE/out/prod.out")"
fi
python3 "$COMPONENTE" --ambiente dev --porta-banco "ssh root@10.0.0.1 psql" >"$BASE/out/remoto.out" 2>&1
RC=$?
if [ "$RC" = "3" ] && grep -q "BANCO_NAO_E_DEV" "$BASE/out/remoto.out"; then
  item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 0
else
  item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 1 "exit=$RC"
fi
python3 "$COMPONENTE" --ambiente dev --planejar >"$BASE/out/plano.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && grep -q "faixa_funcionarios" "$BASE/out/plano.out" \
   && grep -q "tier_prioridade" "$BASE/out/plano.out" && grep -q "SEM_DADO" "$BASE/out/plano.out"; then
  item "--planejar declara os eixos e as sentinelas sem banco (exit 0)" 0
else
  item "--planejar declara os eixos e as sentinelas sem banco (exit 0)" 1 "exit=$RC"
fi
python3 "$COMPONENTE" --ambiente dev --conferir >"$BASE/out/conferir.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && grep -q "CONVERSAO_SEGMENTO_CONFERIR_OK" "$BASE/out/conferir.out"; then
  item "--conferir valida recorte x funil x contrato de dados (exit 0)" 0
else
  item "--conferir valida recorte x funil x contrato de dados (exit 0)" 1 "exit=$RC"
fi

echo "== 2. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_conversao_segmento.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_CONVERSAO_PASS" "$BASE/out/verificador.out" \
   && grep -q "AUTOTESTE 12/12" "$BASE/out/verificador.out"; then
  item "suite offline 34 itens + 12 dentes verde" 0
else
  item "suite offline 34 itens + 12 dentes verde" 1 "$(tail -3 "$BASE/out/verificador.out")"
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
if [ "$TABELAS" = "12" ]; then item "schema com 12 tabelas" 0; else item "schema com 12 tabelas" 1 "(obtido '$TABELAS')"; fi

echo "== 4. base semeada: 12 organizacoes (2 eixos, sentinelas e bordas)"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source, employee_band) VALUES
 ('00000001-0000-0000-0000-000000000001','Org Um','um.test','DISCOVERED','SITE','150_299'),
 ('00000002-0000-0000-0000-000000000002','Org Dois','dois.test','DISCOVERED','SITE','150_299'),
 ('00000003-0000-0000-0000-000000000003','Org Tres','tres.test','DISCOVERED','SITE','150_299'),
 ('00000004-0000-0000-0000-000000000004','Org Quatro','quatro.test','DISCOVERED','SITE','150_299'),
 ('00000005-0000-0000-0000-000000000005','Org Cinco','cinco.test','Nurture','SITE','150_299'),
 ('00000006-0000-0000-0000-000000000006','Org Seis','seis.test','DISCOVERED','SITE','150_299'),
 ('00000007-0000-0000-0000-000000000007','Org Sete','sete.test','DISCOVERED','SITE','LT_70'),
 ('00000008-0000-0000-0000-000000000008','Org Oito','oito.test','DISCOVERED','SITE','LT_70'),
 ('00000009-0000-0000-0000-000000000009','Org Nove','nove.test','DISCOVERED','SITE','LT_70'),
 ('0000000a-0000-0000-0000-00000000000a','Org Dez','dez.test','DISCOVERED','SITE','150_299X'),
 ('0000000b-0000-0000-0000-00000000000b','Org Onze','onze.test','DISCOVERED','SITE',NULL),
 ('0000000c-0000-0000-0000-00000000000c','Org Doze','doze.test','DISCOVERED','SITE','GT_1000');
INSERT INTO sales_intelligence.research_runs (id, organization_id, agent_name, status, completed_at) VALUES
 ('000000a1-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-000000000002','00000002-0000-0000-0000-000000000002','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-000000000003','00000003-0000-0000-0000-000000000003','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-000000000005','00000005-0000-0000-0000-000000000005','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-000000000006','00000006-0000-0000-0000-000000000006','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-000000000007','00000007-0000-0000-0000-000000000007','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-000000000008','00000008-0000-0000-0000-000000000008','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-00000000000a','0000000a-0000-0000-0000-00000000000a','research','COMPLETED',NOW()),
 ('000000a1-0000-0000-0000-00000000000b','0000000b-0000-0000-0000-00000000000b','research','COMPLETED',NOW());
INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, detected_at) VALUES
 ('000000b1-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','GROWTH',NOW()),
 ('000000b1-0000-0000-0000-000000000007','00000007-0000-0000-0000-000000000007','HIRING',NOW());
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at) VALUES
 ('000000c1-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','PRIORITY',92.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-000000000002','00000002-0000-0000-0000-000000000002','PRIORITY',85.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-000000000003','00000003-0000-0000-0000-000000000003','PRIORITY',95.0,'v1',NOW() - INTERVAL '60 days'),
 ('000000c1-0000-0000-0000-000000000004','00000003-0000-0000-0000-000000000003','PRIORITY',70.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-000000000005','00000004-0000-0000-0000-000000000004','PRIORITY',99.0,'',NOW()),
 ('000000c1-0000-0000-0000-000000000006','00000005-0000-0000-0000-000000000005','PRIORITY',30.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-000000000007','00000006-0000-0000-0000-000000000006','PRIORITY',55.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-000000000008','00000007-0000-0000-0000-000000000007','PRIORITY',88.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-000000000009','00000008-0000-0000-0000-000000000008','PRIORITY',61.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-00000000000a','0000000a-0000-0000-0000-00000000000a','PRIORITY',95.0,'v1',NOW()),
 ('000000c1-0000-0000-0000-00000000000b','0000000b-0000-0000-0000-00000000000b','PRIORITY',45.0,'v1',NOW());
INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, legal_basis, source) VALUES
 ('000000d1-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','Contato Um','um@um.test',NULL,'CONSENTIMENTO','SITE'),
 ('000000d1-0000-0000-0000-000000000006','00000006-0000-0000-0000-000000000006','Contato Seis',NULL,'+55 11 90000-0006','CONSENTIMENTO','SITE');
INSERT INTO sales_intelligence.interactions (id, organization_id, channel, direction, interaction_type, occurred_at, response_category) VALUES
 ('000000e1-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','EMAIL','OUTBOUND','ENVIO',NOW(),NULL),
 ('000000e1-0000-0000-0000-000000000002','00000001-0000-0000-0000-000000000001','EMAIL','INBOUND','RESPOSTA',NOW(),'INTERESSE');
INSERT INTO sales_intelligence.recommendations (id, organization_id, action, status, created_at) VALUES
 ('000000f1-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','CREATE_MEETING','EXECUTED',NOW());
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, request_payload) VALUES
 ('00000099-0000-0000-0000-000000000001','crm.lead','00000001-0000-0000-0000-000000000001','odoo','postgres','STAGE_CHANGED','odoo:sc:1','COMPLETED','{"payload":{"estagio_novo":"Proposta"}}'),
 ('00000099-0000-0000-0000-000000000002','crm.lead','00000002-0000-0000-0000-000000000002','odoo','postgres','OPPORTUNITY_WON','odoo:won:2','COMPLETED','{"payload":{"valor":1000}}'),
 ('00000099-0000-0000-0000-000000000003','crm.lead','00000003-0000-0000-0000-000000000003','odoo','postgres','OPPORTUNITY_LOST','odoo:lost:3','COMPLETED','{"payload":{"motivo":"preco"}}'),
 ('00000099-0000-0000-0000-000000000007','crm.lead','00000007-0000-0000-0000-000000000007','odoo','postgres','OPPORTUNITY_WON','odoo:won:7','COMPLETED','{"payload":{"valor":2000}}'),
 ('00000099-0000-0000-0000-000000000008','crm.lead','00000008-0000-0000-0000-000000000008','odoo','postgres','STAGE_CHANGED','odoo:sc:8','COMPLETED','{"payload":{"estagio_novo":"Reuniao do zap"}}'),
 ('00000099-0000-0000-0000-00000000000a','crm.lead','0000000a-0000-0000-0000-00000000000a','odoo','postgres','OPPORTUNITY_WON','odoo:won:a','COMPLETED','{"payload":{"valor":3000}}'),
 ('00000099-0000-0000-0000-0000000000ff','crm.lead','ffffffff-0000-0000-0000-0000000000ff','odoo','postgres','OPPORTUNITY_WON','odoo:won:ff','COMPLETED','{"payload":{"valor":9000}}');
SQL
SEED_OK=$?
item "base semeada (12 organizacoes, 8 tabelas, eixos + bordas)" "$SEED_OK"
ORGS=$(psql_q "select count(*) from sales_intelligence.organizations;")
EVENTOS=$(psql_q "select count(*) from sales_intelligence.sync_events;")
SCORES=$(psql_q "select count(*) from sales_intelligence.scores;")
if [ "$ORGS" = "12" ] && [ "$EVENTOS" = "7" ] && [ "$SCORES" = "11" ]; then
  item "semente conferida por contagem (12 organizacoes, 7 eventos, 11 scores)" 0
else
  item "semente conferida por contagem (12 organizacoes, 7 eventos, 11 scores)" 1 "orgs=$ORGS eventos=$EVENTOS scores=$SCORES"
fi

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
  psql_q "select md5(string_agg(x::text, ',')) from sales_intelligence.organizations x"
}
ANTES=$(snapshot | tr '\n' ' ')

echo "== 6. rodada do recorte (dev, porta de banco local) + prova de leitura pura"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out" \
  >"$BASE/out/recorte.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/conversao-segmento.json" ] && [ -f "$BASE/out/conversao-segmento.html" ]; then
  item "componente gerou o relatorio JSON + HTML (exit 0)" 0
else
  item "componente gerou o relatorio JSON + HTML (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/recorte.out")"
fi
DEPOIS=$(snapshot | tr '\n' ' ')
if [ -n "$ANTES" ] && [ "$ANTES" = "$DEPOIS" ]; then
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 0
else
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 1 "antes=$ANTES depois=$DEPOIS"
fi
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q \
  -c "SET default_transaction_read_only = on" \
  -c "INSERT INTO sales_intelligence.organizations (id, legal_name) VALUES ('00000000-0000-0000-0000-0000000000ff','Nao Deve Entrar')" \
  >"$BASE/out/readonly.out" 2>&1
if [ $? -ne 0 ] && grep -qi "read-only" "$BASE/out/readonly.out"; then
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 0
else
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 1 "$(tail -2 "$BASE/out/readonly.out")"
fi
NAO_ENTROU=$(psql_q "select count(*) from sales_intelligence.organizations where id='00000000-0000-0000-0000-0000000000ff';")
if [ "$NAO_ENTROU" = "0" ]; then item "escrita recusada nao deixou linha" 0; else item "escrita recusada nao deixou linha" 1; fi

echo "== 7. recorte conferido A MAO + COERENCIA COM O FUNIL (mesma base, mesmo funil)"
python3 "$FUNIL" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out-funil" \
  >"$BASE/out/funil.out" 2>&1
RC=$?
[ "$RC" = "0" ] && item "funil do W8-E01-T01 rodou na mesma base (base da coerencia)" 0 \
  || item "funil do W8-E01-T01 rodou na mesma base (base da coerencia)" 1 "exit=$RC"
python3 - "$BASE/out/conversao-segmento.json" "$BASE/out-funil/funil.json" \
  "$(psql_q "select count(*) from sales_intelligence.interactions where direction='INBOUND' and response_category is not null;")" \
  >"$BASE/out/assert.out" 2>&1 <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
fun = json.load(open(sys.argv[2], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
def seg(eixo, valor):
    for e in rel["eixos"]:
        if e["nome"] == eixo:
            for s in e["segmentos"]:
                if s["segmento"] == valor:
                    return s
    return None
def eixo(nome):
    return next(e for e in rel["eixos"] if e["nome"] == nome)

# --- coerencia com o funil (uma derivacao so') ---
item("COERENCIA: o recorte da base inteira e' IGUAL ao funil na mesma base (estagios, alcance e conversoes)",
     [(e["nome"], e["alcancadas"], e["evidencia_propria"], e["conversao_da_anterior_pct"])
      for e in rel["global"]["estagios"]]
     == [(e["nome"], e["alcancadas"], e["evidencia_propria"], e["conversao_da_anterior_pct"])
         for e in fun["estagios"]], "recorte != funil")
item("COERENCIA: won/lost/nurture/em_aberto do global batem com o funil",
     (rel["global"]["won"], rel["global"]["lost"], rel["global"]["nurture"], rel["global"]["em_aberto"])
     == (fun["resumo"]["won"], fun["resumo"]["lost"], fun["resumo"]["nurture"], fun["resumo"]["em_aberto"]),
     (rel["global"], fun["resumo"]))
item("COERENCIA: as lacunas do recorte sao as MESMAS da base do funil (nao recalculadas por segmento)",
     rel["lacunas"] == fun["lacunas"], (rel["lacunas"], fun["lacunas"]))

# --- numeros do aceite, conferidos a mao ---
faixa = eixo("faixa_funcionarios")
obtido = {s["segmento"]: s["organizacoes"] for s in faixa["segmentos"]}
item("faixa de funcionarios: buckets e contagens a mao",
     {k: v for k, v in obtido.items() if v} ==
     {"150_299": 6, "LT_70": 3, "GT_1000": 1, "SEM_DADO": 1, "FORA_DO_VOCABULARIO": 1}, obtido)
item("DENTE faixa quase-identica ao vocabulario NAO vira segmento (150_299X -> FORA_DO_VOCABULARIO)",
     seg("faixa_funcionarios", "FORA_DO_VOCABULARIO")["organizacoes"] == 1
     and seg("faixa_funcionarios", "150_299")["organizacoes"] == 6, obtido)
item("DENTE sem dado e' diferente de fora do vocabulario (2 buckets distintos, 1 org cada)",
     seg("faixa_funcionarios", "SEM_DADO")["organizacoes"] == 1
     and seg("faixa_funcionarios", "FORA_DO_VOCABULARIO")["organizacoes"] == 1
     and sum(s["organizacoes"] for s in faixa["segmentos"]) == 12)
tier = eixo("tier_prioridade")
obtido_tier = {s["segmento"]: s["organizacoes"] for s in tier["segmentos"]}
item("tier: buckets e contagens a mao (A+=2 A=2 B=1 C=2 Nurture=2 SEM_DADO=3)",
     obtido_tier == {"A+": 2, "A": 2, "B": 1, "C": 2, "Nurture": 2, "SEM_DADO": 3}, obtido_tier)
item("DENTE tier vem da pontuacao VIGENTE (a recente 70 -> B), nao da maior historica (95)",
     seg("tier_prioridade", "B")["organizacoes"] == 1 and seg("tier_prioridade", "A+")["organizacoes"] == 2)
item("DENTE score SEM versao nao qualifica (org 4 com 99,0 sem versao fica em SEM_DADO)",
     seg("tier_prioridade", "SEM_DADO")["organizacoes"] == 3)
item("conversao por segmento a mao: 150_299 1/6=16,67% indice 66,68 | LT_70 1/3=33,33% indice 133,32",
     seg("faixa_funcionarios", "150_299")["taxa_conversao_pct"] == 16.67
     and seg("faixa_funcionarios", "150_299")["indice_vs_base_pct"] == 66.68
     and seg("faixa_funcionarios", "LT_70")["taxa_conversao_pct"] == 33.33
     and seg("faixa_funcionarios", "LT_70")["indice_vs_base_pct"] == 133.32,
     (seg("faixa_funcionarios", "150_299")["indice_vs_base_pct"], seg("faixa_funcionarios", "LT_70")["indice_vs_base_pct"]))
item("segmento sem organizacao aparece com taxa null (nao 0) e amostra pequena marcada",
     seg("faixa_funcionarios", "UNKNOWN")["organizacoes"] == 0
     and seg("faixa_funcionarios", "UNKNOWN")["taxa_conversao_pct"] is None
     and seg("faixa_funcionarios", "UNKNOWN")["amostra_pequena"] is True)
item("amostra pequena marcada com o corte do contrato (A+ 2 orgs < 5)",
     seg("tier_prioridade", "A+")["amostra_pequena"] is True
     and seg("faixa_funcionarios", "150_299")["amostra_pequena"] is False and rel["base"]["amostra_minima"] == 5)
item("cobertura por eixo declarada e somando com a base (faixa 10/12=83,33% | tier 9/12=75,0%)",
     faixa["classificadas"] == 10 and faixa["cobertura_pct"] == 83.33 and faixa["sem_dado"] == 1
     and faixa["fora_do_vocabulario"] == 1 and tier["classificadas"] == 9
     and tier["cobertura_pct"] == 75.0 and tier["sem_dado"] == 3,
     (faixa["cobertura_pct"], tier["cobertura_pct"]))
item("global: 12 organizacoes, 3 won, 1 lost, 1 nurture, taxa 25,0%",
     rel["global"]["total_organizacoes"] == 12 and rel["global"]["won"] == 3
     and rel["global"]["lost"] == 1 and rel["global"]["nurture"] == 1
     and rel["global"]["taxa_conversao_pct"] == 25.0, rel["global"])
item("estagios do recorte = estagios do funil, na mesma ordem (13, ordem congelada)",
     [e["nome"] for e in rel["global"]["estagios"]] == [e["nome"] for e in fun["estagios"]]
     and len(fun["estagios"]) == 13)
item("terminal Won/Lost separado DENTRO do segmento (LT_70: 1 won, 0 lost)",
     seg("faixa_funcionarios", "LT_70")["won"] == 1 and seg("faixa_funcionarios", "LT_70")["lost"] == 0)
seg_150 = {e["nome"]: e for e in seg("faixa_funcionarios", "150_299")["estagios"]}
item("alcance do recorte e' monotono e Won nao soma Lost (150_299: won=1, lost=1)",
     seg_150["Won"]["alcancadas"] == 1 and seg_150["Lost"]["alcancadas"] == 1
     and all(seg_150[a]["alcancadas"] >= seg_150[b]["alcancadas"]
             for a, b in zip(["Descoberto", "Pesquisado", "Qualificado", "Contato identificado",
                              "Abordagem iniciada", "Engajamento", "Reunião", "Diagnóstico",
                              "Proposta", "Negociação"],
                             ["Pesquisado", "Qualificado", "Contato identificado",
                              "Abordagem iniciada", "Engajamento", "Reunião", "Diagnóstico",
                              "Proposta", "Negociação", "Won"])))
item("contrato e janela declarados no relatorio (conversao-segmento-v1 + funil-v1 + sha256)",
     rel["contrato"]["versao"] == "conversao-segmento-v1" and len(rel["contrato"]["sha256"]) == 64
     and rel["contrato"]["contrato_de_funil"] == "funil-v1"
     and len(rel["contrato"]["sha256_do_funil"]) == 64
     and rel["janela"] == {"desde": None, "ate": None}, rel["contrato"])
item("10 lacunas declaradas carregadas no relatorio", len(rel["lacunas_declaradas"]) == 10)
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/assert.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/assert.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/assert.out")))

echo "== 8. determinismo, privacidade e dashboard"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out2" \
  >"$BASE/out/recorte2.out" 2>&1
python3 - "$BASE/out/conversao-segmento.json" "$BASE/out2/conversao-segmento.json" \
  "$BASE/out/conversao-segmento.html" >"$BASE/out/priv.out" 2>&1 <<'PY'
import json, sys
r1 = json.load(open(sys.argv[1], encoding="utf-8"))
r2 = json.load(open(sys.argv[2], encoding="utf-8"))
html = open(sys.argv[3], encoding="utf-8").read()
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
item("determinismo: duas rodadas na MESMA base -> mesmo hash_do_relatorio",
     r1["hash_do_relatorio"] == r2["hash_do_relatorio"] and len(r1["hash_do_relatorio"]) == 64,
     (r1["hash_do_relatorio"][:16], r2["hash_do_relatorio"][:16]))
texto = json.dumps(r1, ensure_ascii=False) + html
literais = ["um@um.test", "+55 11 90000-0006", "Org Um", "Contato Um", "Org Doze",
            "00000001-0000-0000-0000-000000000001", "0000000a-0000-0000-0000-00000000000a"]
item("saida sem PII e sem organizacao nominal (nenhum e-mail/telefone/nome/UUID da base)",
     "@" not in texto and not any(l in texto for l in literais), [l for l in literais if l in texto])
item("dashboard HTML auto-contido (sem http/https/script/link) com os dois eixos e os segmentos",
     "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
     and len(html) > 800 and "Faixa de funcionarios" in html and "Faixa de prioridade (tier)" in html
     and all(s["segmento"] in html for e in r1["eixos"] for s in e["segmentos"]))
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/priv.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/priv.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/priv.out")))

echo "== 9. higiene: o aceite prova que NAO deixa volume anonimo novo"
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
  echo "ACEITE_CONVERSAO_SEGMENTO_OK"
  exit 0
fi
echo "ACEITE_CONVERSAO_SEGMENTO_FALHOU"
exit 1
