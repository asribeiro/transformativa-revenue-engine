#!/usr/bin/env bash
# Aceite de ponta do funil (`funil-v1`) — card TRE-W8-E01-T01 (W8 / Analytics).
#
# Mede o componente contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-funil-acc` (127.0.0.1) + migration 0001 + o componente
# `hermes/agentes/analytics/funil.py` lendo a base canonica e a trilha Odoo -> PostgreSQL.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4); porta remota RECUSA (BANCO_NAO_E_DEV); --planejar sem banco;
#   2. suite offline (24 itens + 8 dentes) verde;
#   3. funil conferido A MAO sobre base semeada: alcance cumulativo por organizacao, terminal Won/Lost
#      separado, Nurture lateral, conversoes;
#   4. contagem por ORGANIZACAO (nao por linha): 3 interacoes de resposta em 2 organizacoes nao inflam;
#   5. rotulo de estagio FORA do vocabulario nao vira estagio (lacuna medida);
#   6. evento Odoo sem organizacao atribuivel (OPPORTUNITY_LOST da oportunidade) NAO entra no estagio;
#   7. LEITURA PURA: snapshot das 12 tabelas antes/depois igual e a transacao READ ONLY recusando escrita;
#   8. determinismo: duas rodadas -> mesmo hash_do_relatorio;
#   9. saida sem PII (@ e sequencias longas de digitos ausentes) e dashboard HTML auto-contido.
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_funil_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w8e01t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-funil-acc
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/agentes/analytics/funil.py"
CONTRATO="hermes/agentes/analytics/funil-v1.json"
MANTER=0
[ "${1:-}" = "--manter" ] && MANTER=1

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
if [ -f "$COMPONENTE" ] && [ -f "$CONTRATO" ] && [ -f scripts/agentes/verificar_funil.py ]; then
  item "componente, contrato e verificador presentes no repo" 0
else
  item "componente, contrato e verificador presentes no repo" 1
fi

echo "== 1. guardas de ambiente (sem banco)"
python3 "$COMPONENTE" --ambiente prod --saida "$BASE/out/prod" >"$BASE/out/prod.out" 2>&1
RC=$?
[ "$RC" = "4" ] && item "prod RECUSA por desenho (exit 4)" 0 || item "prod RECUSA por desenho (exit 4)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente dev --porta-banco "ssh root@10.0.0.1 psql" >"$BASE/out/remoto.out" 2>&1
RC=$?
[ "$RC" = "3" ] && item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 0 \
  || item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente dev --planejar >"$BASE/out/plano.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && grep -q "Descoberto" "$BASE/out/plano.out"; then
  item "--planejar declara o funil sem banco (exit 0)" 0
else
  item "--planejar declara o funil sem banco (exit 0)" 1 "exit=$RC"
fi

echo "== 2. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_funil.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_FUNIL_PASS" "$BASE/out/verificador.out"; then
  item "suite offline 24 itens + 8 dentes verde" 0
else
  item "suite offline 24 itens + 8 dentes verde" 1 "$(tail -3 "$BASE/out/verificador.out")"
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

echo "== 4. base semeada: 9 organizacoes cobrindo todos os niveis + 3 casos de borda"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source) VALUES
 ('00000001-0000-0000-0000-000000000001','Org Um','um.test','DISCOVERED','SITE'),
 ('00000002-0000-0000-0000-000000000002','Org Dois','dois.test','DISCOVERED','SITE'),
 ('00000003-0000-0000-0000-000000000003','Org Tres','tres.test','DISCOVERED','META'),
 ('00000004-0000-0000-0000-000000000004','Org Quatro','quatro.test','Won','SITE'),
 ('00000005-0000-0000-0000-000000000005','Org Cinco','cinco.test','Lost','GOOGLE'),
 ('00000006-0000-0000-0000-000000000006','Org Seis','seis.test','Nurture','EVENTO_CAPTURA'),
 ('00000007-0000-0000-0000-000000000007','Org Sete','sete.test','ESTAGIO_INVENTADO','SITE'),
 ('00000008-0000-0000-0000-000000000008','Org Oito','oito.test','DISCOVERED','WHATSAPP'),
 ('00000009-0000-0000-0000-000000000009','Org Nove','nove.test','DISCOVERED','SITE');
INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, legal_basis, source) VALUES
 ('00000011-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','Contato Um','um@um.test',NULL,'CONSENTIMENTO','SITE'),
 ('00000022-0000-0000-0000-000000000002','00000002-0000-0000-0000-000000000002','Contato Dois','dois@dois.test',NULL,'CONSENTIMENTO','SITE'),
 ('00000033-0000-0000-0000-000000000003','00000003-0000-0000-0000-000000000003','Contato Tres',NULL,'+55 11 90000-0003','CONSENTIMENTO','META');
INSERT INTO sales_intelligence.research_runs (id, organization_id, agent_name, status, completed_at) VALUES
 ('00000044-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','research','COMPLETED',NOW()),
 ('00000044-0000-0000-0000-000000000002','00000002-0000-0000-0000-000000000002','research','COMPLETED',NOW());
INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, detected_at) VALUES
 ('00000055-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','GROWTH',NOW());
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at) VALUES
 ('00000066-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','PRIORITY',80.0,'v1',NOW()),
 ('00000066-0000-0000-0000-000000000002','00000002-0000-0000-0000-000000000002','PRIORITY',70.0,'v1',NOW()),
 ('00000066-0000-0000-0000-000000000003','00000003-0000-0000-0000-000000000003','PRIORITY',60.0,'',NOW());
INSERT INTO sales_intelligence.interactions (id, organization_id, channel, direction, interaction_type, occurred_at, response_category) VALUES
 ('00000077-0000-0000-0000-000000000001','00000001-0000-0000-0000-000000000001','EMAIL','OUTBOUND','ENVIO',NOW(),NULL),
 ('00000077-0000-0000-0000-000000000002','00000001-0000-0000-0000-000000000001','EMAIL','INBOUND','RESPOSTA',NOW(),'INTERESSE'),
 ('00000077-0000-0000-0000-000000000003','00000001-0000-0000-0000-000000000001','WHATSAPP','INBOUND','RESPOSTA',NOW(),'INTERESSE'),
 ('00000077-0000-0000-0000-000000000004','00000002-0000-0000-0000-000000000002','EMAIL','OUTBOUND','ENVIO',NOW(),NULL),
 ('00000077-0000-0000-0000-000000000005','00000002-0000-0000-0000-000000000002','EMAIL','INBOUND','RESPOSTA',NOW(),'INTERESSE');
INSERT INTO sales_intelligence.recommendations (id, organization_id, action, status, created_at) VALUES
 ('00000088-0000-0000-0000-000000000002','00000002-0000-0000-0000-000000000002','CREATE_MEETING','EXECUTED',NOW());
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, request_payload) VALUES
 ('00000099-0000-0000-0000-000000000003','crm.lead','00000003-0000-0000-0000-000000000003','odoo','postgres','STAGE_CHANGED','odoo:sc:3','COMPLETED','{"payload":{"estagio_novo":"Proposta"}}'),
 ('00000099-0000-0000-0000-000000000004','crm.lead','00000004-0000-0000-0000-000000000004','odoo','postgres','OPPORTUNITY_WON','odoo:won:4','COMPLETED','{"payload":{"valor":1000}}'),
 ('00000099-0000-0000-0000-000000000005','crm.lead','00000005-0000-0000-0000-000000000005','odoo','postgres','OPPORTUNITY_LOST','odoo:lost:5','COMPLETED','{"payload":{"motivo":"preco"}}'),
 ('00000099-0000-0000-0000-000000000008','crm.lead','00000008-0000-0000-0000-000000000008','odoo','postgres','STAGE_CHANGED','odoo:sc:8','COMPLETED','{"payload":{"estagio_novo":"Reuniao do zap"}}'),
 ('00000099-0000-0000-0000-000000000009','crm.lead','aaaaaaaa-0000-0000-0000-000000000009','odoo','postgres','OPPORTUNITY_LOST','odoo:lost:9','COMPLETED','{"payload":{"motivo":"sem verba"}}');
SQL
item "base semeada (organizations/contacts/research/signals/scores/interactions/recommendations/sync_events)" $?

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

echo "== 6. rodada do funil (dev, porta de banco local) + prova de leitura pura"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out" \
  >"$BASE/out/funil.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/funil.json" ]; then
  item "componente gerou o relatorio JSON + HTML (exit 0)" 0
else
  item "componente gerou o relatorio JSON + HTML (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/funil.out")"
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
if [ "$NAO_ENTROU" = "0" ]; then
  item "escrita recusada nao deixou linha" 0
else
  item "escrita recusada nao deixou linha" 1
fi

echo "== 7. numeros do funil conferidos A MAO + dentes de ponta medidos no banco"
python3 - "$BASE/out/funil.json" "$(psql_q "select count(*) from sales_intelligence.interactions where direction='INBOUND' and response_category is not null;")" >"$BASE/out/assert.out" 2>&1 <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
db_inbound = int(sys.argv[2])
por = {e["nome"]: e for e in rel["estagios"]}
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
esperado = {"Descoberto": 9, "Pesquisado": 5, "Qualificado": 5, "Contato identificado": 5,
            "Abordagem iniciada": 5, "Engajamento": 5, "Reunião": 4, "Diagnóstico": 3,
            "Proposta": 3, "Negociação": 2, "Won": 1, "Lost": 1, "Nurture": 1}
obtido = {k: v["alcancadas"] for k, v in por.items()}
item("alcance por estagio bate com a conta a mao", obtido == esperado, obtido)
item("terminal Won/Lost separados (organizacao perdida NAO conta em Won)",
     por["Won"]["alcancadas"] == 1 and por["Lost"]["alcancadas"] == 1,
     (por["Won"]["alcancadas"], por["Lost"]["alcancadas"]))
item("Nurture e' lateral (converte de Qualificado e nao entra na linear)",
     por["Nurture"]["lateral"] and por["Nurture"]["conversao_de"] == "Qualificado"
     and por["Nurture"]["conversao_da_anterior_pct"] == 20.0, por["Nurture"])
conv = {k: v["conversao_da_anterior_pct"] for k, v in por.items()}
item("conversoes calculadas (55.56 / 80.0 / 75.0 / 66.67 / 50.0)",
     conv["Pesquisado"] == 55.56 and conv["Reunião"] == 80.0 and conv["Diagnóstico"] == 75.0
     and conv["Negociação"] == 66.67 and conv["Won"] == 50.0, conv)
item("resumo: 9 organizacoes, won=1, lost=1, nurture=1, em aberto=6",
     rel["resumo"]["total_organizacoes"] == 9 and rel["resumo"]["won"] == 1
     and rel["resumo"]["lost"] == 1 and rel["resumo"]["nurture"] == 1
     and rel["resumo"]["em_aberto"] == 6, rel["resumo"])
item("contagem e' por ORGANIZACAO: %d interacoes de resposta no banco viram %s organizacao(oes) no funil"
     % (db_inbound, por["Engajamento"]["evidencia_propria"]),
     db_inbound == 3 and rel["fontes"]["INTERACAO_INBOUND_CLASSIFICADA"] == 2
     and por["Engajamento"]["evidencia_propria"] == 2,
     "banco=%d fonte=%s propria=%s" % (db_inbound, rel["fontes"]["INTERACAO_INBOUND_CLASSIFICADA"],
                                       por["Engajamento"]["evidencia_propria"]))
item("DENTE rotulo fora do vocabulario NAO vira estagio (org 8 fica so' em Descoberto)",
     rel["lacunas"]["estagios_desconhecidos"] == 1 and por["Reunião"]["evidencia_propria"] == 1
     and "reuniao do zap" in rel["lacunas"]["rotulos_nao_declarados"], rel["lacunas"])
item("DENTE evento sem organizacao atribuivel nao entra no estagio (lacuna nomeada por operacao)",
     rel["lacunas"]["eventos_sem_atribuicao"] == {"OPPORTUNITY_LOST": 1}
     and por["Lost"]["alcancadas"] == 1, rel["lacunas"]["eventos_sem_atribuicao"])
item("DENTE status de organizacao nao declarado fica em lacuna (nao vira estagio)",
     rel["lacunas"]["status_nao_declarado"] == 1
     and "estagio_inventado" in rel["lacunas"]["rotulos_nao_declarados"], rel["lacunas"])
item("score SEM versao nao qualifica (contrato §8): org 3 nao infla o Qualificado",
     rel["fontes"]["SCORE_PRIORITY"] == 2, rel["fontes"]["SCORE_PRIORITY"])
item("contrato e janela declarados no relatorio",
     rel["contrato"]["versao"] == "funil-v1" and len(rel["contrato"]["sha256"]) == 64
     and rel["janela"] == {"desde": None, "ate": None}, rel["contrato"])
item("5 lacunas declaradas carregadas no relatorio", len(rel["lacunas_declaradas"]) == 5)
item("monotonicidade do funil linear", all(
    por[a]["alcancadas"] >= por[b]["alcancadas"] for a, b in zip(
        ["Descoberto", "Pesquisado", "Qualificado", "Contato identificado", "Abordagem iniciada",
         "Engajamento", "Reunião", "Diagnóstico", "Proposta"],
        ["Pesquisado", "Qualificado", "Contato identificado", "Abordagem iniciada", "Engajamento",
         "Reunião", "Diagnóstico", "Proposta", "Negociação"])))
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/assert.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/assert.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/assert.out")))

echo "== 8. determinismo, privacidade e dashboard"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out2" \
  >"$BASE/out/funil2.out" 2>&1
python3 - "$BASE/out/funil.json" "$BASE/out2/funil.json" "$BASE/out/funil.html" \
  >"$BASE/out/priv.out" 2>&1 <<'PY'
import json, re, sys, hashlib
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
literais_pii = ["um@um.test", "dois@dois.test", "+55 11 90000-0003", "Org Um", "Contato Um", "Org Nove"]
item("saida sem PII: nenhum e-mail e nenhum literal de contato da base semeada",
     "@" not in texto and not any(l in texto for l in literais_pii),
     [l for l in literais_pii if l in texto])
item("dashboard HTML auto-contido (sem http/https/script/link) e com o funil",
     "http://" not in html and "https://" not in html and "<script" not in html
     and "<link" not in html and len(html) > 500 and "Estágio" in html
     and all(e["nome"] in html for e in r1["estagios"]))
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/priv.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/priv.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/priv.out")))

echo
echo "RESULTADO: $([ "$FALHAS" = "0" ] && echo PASS || echo FALHOU) ($OK itens, $FALHAS falhas)"
if [ "$FALHAS" = "0" ]; then
  echo "ACEITE_FUNIL_OK"
  exit 0
fi
echo "ACEITE_FUNIL_FALHOU"
exit 1
