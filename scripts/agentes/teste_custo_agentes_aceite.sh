#!/usr/bin/env bash
# Aceite de ponta do custo de agentes (`custo-agentes-v1`) — card TRE-W8-E05-T01 (W8 / Analytics).
#
# Mede o componente contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-custo-acc` (127.0.0.1) + migration 0001 + o componente
# `hermes/agentes/analytics/custo_agentes.py` lendo a auditoria de execucao de agente (agent_runs).
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4); porta remota RECUSA (BANCO_NAO_E_DEV); --planejar sem banco;
#   2. suite offline (29 itens + 8 dentes) verde;
#   3. as metricas de custo, token, latencia e desfecho conferidas A MAO sobre base semeada;
#   4. DENTE custo NULO nao vira zero (agente sem custo declarado nao tem custo 0.000000 medido);
#   5. DENTE status fora do vocabulario (TIMEOUT) nao vira falha nem sucesso;
#   6. DENTE custo negativo nao entra na soma;
#   7. DENTE latencia invertida (fim < inicio) fica fora da estatistica;
#   8. DENTE o ranking NAO coroa quem declara custo parcial (nem o mais caro por execucao, se sem amostra);
#   9. LEITURA PURA: snapshot das 12 tabelas antes/depois igual e a transacao READ ONLY recusando escrita;
#  10. janela por started_at recorta o recorte (9 de 16 execucoes);
#  11. determinismo: duas rodadas -> mesmo hash_do_relatorio;
#  12. saida sem PII e dashboard HTML auto-contido + CSV com uma linha por agente.
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_custo_agentes_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w8e05t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-custo-acc
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/agentes/analytics/custo_agentes.py"
CONTRATO="hermes/agentes/analytics/custo-agentes-v1.json"
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
if [ -f "$COMPONENTE" ] && [ -f "$CONTRATO" ] && [ -f scripts/agentes/verificar_custo_agentes.py ]; then
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
if [ "$RC" = "0" ] && grep -q "estimated_cost" "$BASE/out/plano.out"; then
  item "--planejar declara o contrato sem banco (exit 0)" 0
else
  item "--planejar declara o contrato sem banco (exit 0)" 1 "exit=$RC"
fi

echo "== 2. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_custo_agentes.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_CUSTO_AGENTES_PASS" "$BASE/out/verificador.out"; then
  item "suite offline 30 itens + 8 dentes verde" 0
else
  item "suite offline 30 itens + 8 dentes verde" 1 "$(tail -3 "$BASE/out/verificador.out")"
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

echo "== 4. base semeada: 16 execucoes (5 agentes + 1 orfa) cobrindo os desfechos e as bordas"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.agent_runs
 (id, agent_name, agent_role, agent_version, workflow, workflow_version, organization_id, triggered_by, model,
  started_at, finished_at, status, tokens_input, tokens_output, estimated_cost) VALUES
 ('eeeeeeee-0000-0000-0000-000000000001','research','research','v1','pesquisa','v1','00000001-0000-0000-0000-000000000001','job','gpt-x',  '2026-10-01T10:00:00Z','2026-10-01T10:01:00Z','COMPLETED',NULL,NULL,NULL),
 ('eeeeeeee-0000-0000-0000-000000000002','research','research','v1','pesquisa','v1','00000002-0000-0000-0000-000000000002','job','gpt-x',  '2026-10-01T11:00:00Z','2026-10-01T11:02:00Z','FAILED',NULL,NULL,NULL),
 ('eeeeeeee-0000-0000-0000-000000000003','research','research','v1','pesquisa',NULL,NULL,NULL,NULL,NULL::timestamptz,NULL::timestamptz,NULL,NULL,NULL,NULL),
 ('eeeeeeee-0000-0000-0000-000000000004','outreach','outreach','v1','abordagem','v1','00000001-0000-0000-0000-000000000001','job','gpt-x','2026-10-02T09:00:00Z','2026-10-02T09:01:00Z','COMPLETED',100,50,0.0012),
 ('eeeeeeee-0000-0000-0000-000000000005','outreach','outreach','v1','abordagem','v1','00000001-0000-0000-0000-000000000001','job','gpt-x','2026-10-02T10:00:00Z','2026-10-02T10:01:30Z','COMPLETED',120,60,0.0018),
 ('eeeeeeee-0000-0000-0000-000000000006','outreach','outreach','v1','abordagem','v1','00000002-0000-0000-0000-000000000002','job','gpt-x','2026-10-02T11:00:00Z','2026-10-02T11:00:30Z','COMPLETED',80,40,0.0006),
 ('eeeeeeee-0000-0000-0000-000000000007','outreach','outreach','v1','abordagem','v1','00000003-0000-0000-0000-000000000003','job','gpt-x','2026-10-02T12:00:00Z','2026-10-02T12:00:20Z','REJECTED',10,5,0.0006),
 ('eeeeeeee-0000-0000-0000-000000000008','scout','scout','v2','prospeccao','v1','00000001-0000-0000-0000-000000000001',NULL,'gpt-mini','2026-10-01T08:00:00Z','2026-10-01T08:00:10Z','COMPLETED',200,100,0.0020),
 ('eeeeeeee-0000-0000-0000-000000000009','scout','scout','v2','prospeccao','v1','00000002-0000-0000-0000-000000000002',NULL,'gpt-mini','2026-10-01T10:00:00Z','2026-10-01T10:00:20Z','COMPLETED',200,100,0.0020),
 ('eeeeeeee-0000-0000-0000-00000000000a','scout','scout','v2','prospeccao','v1','00000003-0000-0000-0000-000000000003',NULL,'gpt-mini','2026-10-01T12:00:00Z','2026-10-01T12:00:40Z','COMPLETED',200,100,0.0020),
 ('eeeeeeee-0000-0000-0000-00000000000b','scout','scout','v2','prospeccao','v1','00000004-0000-0000-0000-000000000004',NULL,'gpt-mini','2026-10-01T13:10:00Z','2026-10-01T13:00:00Z','COMPLETED',200,100,0.0020),
 ('eeeeeeee-0000-0000-0000-00000000000c','icp_score','scoring','v1','icp','v1','00000001-0000-0000-0000-000000000001','job','gpt-x','2026-10-02T14:00:00Z','2026-10-02T14:05:00Z','COMPLETED',500,300,0.0090),
 ('eeeeeeee-0000-0000-0000-00000000000d','icp_score','scoring','v1','icp','v1','00000002-0000-0000-0000-000000000002','job','gpt-x','2026-10-02T15:00:00Z','2026-10-02T15:00:05Z','COMPLETED',10,2,0),
 ('eeeeeeee-0000-0000-0000-00000000000e','nba','scoring','v1','nba','v1','00000001-0000-0000-0000-000000000001','job','gpt-x','2026-10-02T16:00:00Z','2026-10-02T16:00:45Z','TIMEOUT',10,10,0.0012),
 ('eeeeeeee-0000-0000-0000-00000000000f','nba','scoring','v1','nba','v1','00000002-0000-0000-0000-000000000002','job','gpt-x','2026-10-02T17:00:00Z','2026-10-02T17:00:10Z','REVIEW_REQUIRED',10,10,-0.5),
 ('eeeeeeee-0000-0000-0000-000000000010','',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'2026-10-02T18:00:00Z','2026-10-02T18:00:01Z','COMPLETED',NULL,NULL,NULL);
SQL
item "base semeada (16 execucoes em agent_runs)" $?
SEMEADAS=$(psql_q "select count(*) from sales_intelligence.agent_runs;")
if [ "$SEMEADAS" = "16" ]; then
  item "16 execucoes no banco" 0
else
  item "16 execucoes no banco" 1 "(obtido '$SEMEADAS')"
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
  psql_q "select md5(string_agg(x::text, ',')) from sales_intelligence.agent_runs x"
}
ANTES=$(snapshot | tr '\n' ' ')

echo "== 6. rodada do componente (dev, porta de banco local) + prova de leitura pura"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out" \
  >"$BASE/out/custo.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/custo-agentes.json" ] && [ -f "$BASE/out/custo-agentes.csv" ] \
   && [ -f "$BASE/out/custo-agentes.html" ]; then
  item "componente gerou JSON + CSV + HTML (exit 0)" 0
else
  item "componente gerou JSON + CSV + HTML (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/custo.out")"
fi
DEPOIS=$(snapshot | tr '\n' ' ')
if [ -n "$ANTES" ] && [ "$ANTES" = "$DEPOIS" ]; then
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 0
else
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 1 "antes=$ANTES depois=$DEPOIS"
fi
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q \
  -c "SET default_transaction_read_only = on" \
  -c "INSERT INTO sales_intelligence.agent_runs (id, agent_name, status) VALUES ('00000000-0000-0000-0000-0000000000ff','nao-deve-entrar','COMPLETED')" \
  >"$BASE/out/readonly.out" 2>&1
if [ $? -ne 0 ] && grep -qi "read-only" "$BASE/out/readonly.out"; then
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 0
else
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 1 "$(tail -2 "$BASE/out/readonly.out")"
fi
NAO_ENTROU=$(psql_q "select count(*) from sales_intelligence.agent_runs where id='00000000-0000-0000-0000-0000000000ff';")
if [ "$NAO_ENTROU" = "0" ]; then
  item "escrita recusada nao deixou linha" 0
else
  item "escrita recusada nao deixou linha" 1
fi

echo "== 7. metricas conferidas A MAO + os 5 dentes medidos no proprio banco"
python3 - "$BASE/out/custo-agentes.json" "$BASE/out/custo-agentes.csv" "$BASE/out/custo-agentes.html" \
  "$(psql_q "select count(*) from sales_intelligence.agent_runs where estimated_cost is null;")" \
  "$(psql_q "select count(*) from sales_intelligence.agent_runs where estimated_cost < 0;")" \
  "$(psql_q "select count(*) from sales_intelligence.agent_runs where started_at is not null and finished_at is not null and finished_at < started_at;")" \
  "$(psql_q "select count(*) from sales_intelligence.agent_runs where status not in ('COMPLETED','FAILED','REJECTED','REVIEW_REQUIRED');")" \
  "$(psql_q "select count(*) from sales_intelligence.agent_runs where agent_name='research' and estimated_cost is not null;")" \
  >"$BASE/out/assert.out" 2>&1 <<'PY'
import json, re, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
csv = open(sys.argv[2], encoding="utf-8").read()
html = open(sys.argv[3], encoding="utf-8").read()
db_sem_custo, db_negativo, db_invertida, db_fora_vocab, db_research_com_custo = [int(x) for x in sys.argv[4:9]]
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
def grupo(nome):
    return next(g for g in rel["por_agente"] if g["agente"] == nome)
r = rel["resumo"]
item("fontes: a porta devolveu 16 linhas de 14 campos (o formato do contrato chegou inteiro)",
     rel["fontes"]["EXECUCOES_DE_AGENTE"] == 16 and r["runs"] == 16, rel["fontes"])
item("resumo a mao: 11 concluidas, 1 falha, 1 recusa, 1 revisao, 2 sem status, taxas 0.0714/0.0714",
     (r["concluidas"], r["falhas"], r["recusadas"], r["revisao"], r["sem_status"],
      r["classificadas"], r["taxa_de_falha"], r["taxa_de_recusa"]) == (11, 1, 1, 1, 2, 14, 0.0714, 0.0714),
     (r["concluidas"], r["falhas"], r["recusadas"], r["revisao"], r["sem_status"], r["taxa_de_falha"]))
item("tokens a mao: 1640 de entrada, 877 de saida, 2517 no total; 4 execucoes sem token",
     (r["tokens_input"], r["tokens_output"], r["tokens_totais"], rel["lacunas"]["runs_sem_tokens"]) == (1640, 877, 2517, 4),
     (r["tokens_input"], r["tokens_output"], r["tokens_totais"]))
item("custo a mao: 0.022400 no total, 0.002036 por execucao com custo; media por sucesso NULA (nem todo mundo declara)",
     r["custo_total"] == "0.022400" and r["custo_medio_por_execucao"] == "0.002036"
     and r["custo_por_execucao_concluida"] is None and r["runs_com_custo"] == 11,
     (r["custo_total"], r["custo_medio_por_execucao"], r["custo_por_execucao_concluida"]))
item("latencia a mao: media 57.93, mediana 30.0, p95 300.0; 2 execucoes fora (1 invertida, 1 incompleta)",
     (r["latencia_media_s"], r["latencia_mediana_s"], r["latencia_p95_s"], r["runs_sem_latencia"]) == (57.93, 30.0, 300.0, 2),
     (r["latencia_media_s"], r["latencia_mediana_s"], r["latencia_p95_s"]))
item("agrupamentos: 5 agentes, 2 modelos, 5 workflows, 4 organizacoes distintas",
     (r["agentes"], r["modelos"], r["workflows"], r["organizacoes"]) == (5, 2, 5, 4),
     (r["agentes"], r["modelos"], r["workflows"], r["organizacoes"]))
out = grupo("outreach")
item("outreach a mao: 4 execucoes (3 concluidas, 1 recusada), custo 0.004200, 0.001400 por sucesso, 3 organizacoes",
     (out["runs"], out["concluidas"], out["recusadas"], out["falhas"], out["taxa_de_falha"], out["taxa_de_recusa"],
      out["custo_total"], out["custo_por_execucao_concluida"], out["organizacoes"]) ==
     (4, 3, 1, 0, 0.0, 0.25, "0.004200", "0.001400", 3), dict(out))
sc = grupo("scout")
item("scout a mao: 4 concluidas, custo 0.008000, 0.002000 por sucesso, latencias 23.33/20.0/40.0",
     (sc["concluidas"], sc["custo_total"], sc["custo_por_execucao_concluida"], sc["latencia_media_s"],
      sc["latencia_mediana_s"], sc["latencia_p95_s"]) == (4, "0.008000", "0.002000", 23.33, 20.0, 40.0), dict(sc))

item("DENTE 1 (banco conta %d sem custo): custo NULO NAO vira zero — research com 0.000000, media NULA e 3 sem custo"
     % db_sem_custo,
     db_sem_custo == 4 and rel["lacunas"]["runs_sem_custo"] == 4
     and grupo("research")["custo_total"] == "0.000000" and grupo("research")["custo_medio_por_execucao"] is None
     and grupo("research")["runs_sem_custo"] == 3,
     dict(grupo("research")))
item("DENTE 2 (banco conta %d status fora do vocabulario): TIMEOUT nao vira falha nem sucesso — nba sem_status=1 e 0 falhas"
     % db_fora_vocab,
     db_fora_vocab == 1 and rel["lacunas"]["status_fora_do_vocabulario"] == {"TIMEOUT": 1}
     and grupo("nba")["sem_status"] == 1 and grupo("nba")["falhas"] == 0,
     rel["lacunas"]["status_fora_do_vocabulario"])
item("DENTE 3 (banco conta %d custo negativo): o negativo NAO entra na soma — nba fecha em 0.001200"
     % db_negativo,
     db_negativo == 1 and rel["lacunas"]["runs_custo_negativo"] == 1
     and grupo("nba")["custo_total"] == "0.001200" and grupo("nba")["runs_sem_custo"] == 1,
     grupo("nba")["custo_total"])
item("DENTE 4 (banco conta %d latencia invertida): fim < inicio fica FORA — scout mede 23.33 sobre 3 execucoes"
     % db_invertida,
     db_invertida == 1 and rel["lacunas"]["latencia_invertida"] == 1 and sc["runs_sem_latencia"] == 1
     and sc["latencia_media_s"] == 23.33,
     (sc["latencia_media_s"], rel["lacunas"]["latencia_invertida"]))
item("DENTE 5 (banco conta %d execucoes do research com custo): o ranking NAO coroa quem nao declara custo"
     % db_research_com_custo,
     db_research_com_custo == 0 and rel["ranking"]["vencedor"] == "scout"
     and rel["ranking"]["custo_por_execucao_concluida"] == "0.002000"
     and rel["ranking"]["agentes_fora_do_ranking"] == ["icp_score", "nba", "research"],
     rel["ranking"])
item("o mais caro por execucao (icp_score, 0.004500) fica FORA por amostra — nao se coroa com 2 execucoes",
     grupo("icp_score")["custo_por_execucao_concluida"] == "0.004500"
     and not grupo("icp_score")["amostra_suficiente"])
texto = json.dumps(rel, ensure_ascii=False)
item("saida sem PII: nenhum organization_id (o UUID) e nenhum e-mail/telefone na saida",
     "@" not in texto and "organization_id" not in texto and "00000001-0000-0000-0000-000000000001" not in texto)
item("CSV: uma linha por agente (5) + cabecalho",
     len(csv.strip().splitlines()) == 6 and csv.splitlines()[0].startswith("agente;"))
item("HTML auto-contido (sem http/https/script/link/src) e com a tabela por agente",
     "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
     and "src=" not in html and "Custo de agentes" in html and "scout" in html)
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/assert.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/assert.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/assert.out")))

echo "== 8. janela por started_at recorta o recorte"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" \
  --desde "2026-10-02T00:00:00Z" --ate "2026-10-02T23:59:59Z" --saida "$BASE/out/janela" \
  >"$BASE/out/janela.out" 2>&1
python3 - "$BASE/out/janela/custo-agentes.json" >"$BASE/out/janela-assert.out" 2>&1 <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
r = rel["resumo"]
falhas = 0
def item(nome, cond, det=""):
    global falhas
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas += 1
item("janela 02/10: 9 de 16 execucoes (as 7 de 01/10 ficam fora), 0 falhas, custo 0.014400",
     r["runs"] == 9 and r["falhas"] == 0 and r["recusadas"] == 1 and r["revisao"] == 1
     and r["sem_status"] == 1 and r["concluidas"] == 6 and r["custo_total"] == "0.014400",
     (r["runs"], r["concluidas"], r["falhas"], r["custo_total"]))
item("janela exclui os agentes de 01/10 (research e scout fora da lista)",
     [g["agente"] for g in rel["por_agente"]] == ["icp_score", "nba", "outreach"],
     [g["agente"] for g in rel["por_agente"]])
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/janela-assert.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/janela-assert.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/janela-assert.out")))

echo "== 9. determinismo (duas rodadas na MESMA base)"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --saida "$BASE/out2" \
  >"$BASE/out/custo2.out" 2>&1
python3 - "$BASE/out/custo-agentes.json" "$BASE/out2/custo-agentes.json" >"$BASE/out/det.out" 2>&1 <<'PY'
import json, sys
r1_bytes = open(sys.argv[1], "rb").read()
r2_bytes = open(sys.argv[2], "rb").read()
r1 = json.loads(r1_bytes)
r2 = json.loads(r2_bytes)
ok = (r1["hash_do_relatorio"] == r2["hash_do_relatorio"] and len(r1["hash_do_relatorio"]) == 64)
print(("OK    " if ok else "FALHOU ") + "determinismo: duas rodadas -> mesmo hash_do_relatorio")
ok2 = r1_bytes == r2_bytes and b"gerado_em" not in r1_bytes
print(("OK    " if ok2 else "FALHOU ") + "sem --com-carimbo a saida e' BYTE a BYTE identica (nao ha carimbo de tempo)")
sys.exit(0 if (ok and ok2) else 1)
PY
cat "$BASE/out/det.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/det.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/det.out")))

echo
echo "RESULTADO: $([ "$FALHAS" = "0" ] && echo PASS || echo FALHOU) ($OK itens, $FALHAS falhas)"
if [ "$FALHAS" = "0" ]; then
  echo "ACEITE_CUSTO_AGENTES_OK"
  exit 0
fi
echo "ACEITE_CUSTO_AGENTES_FALHOU"
exit 1
