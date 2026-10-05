#!/usr/bin/env bash
# Aceite de ponta da pontuacao preditiva (`pontuacao-preditiva-v1`) — card TRE-W9-E02-T01.
#
# Mede o componente contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-analytics-pred` (127.0.0.1) + migration 0001 + base semeada com sinal conhecido,
# e a CALIBRACAO (W9-E01-T01) rodando ANTES como dependencia medida — a previsao usa os pesos que
# o relatorio da calibracao publica, nao um peso escolhido a mao.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4) antes de ler contrato; porta remota RECUSA em dev; --planejar/--conferir sem banco;
#   2. suite offline do componente (itens + dentes) verde;
#   3. DEPENDENCIA: sem --calibracao RECUSA (exit 3); com corte ajuste/validacao divergente RECUSA (exit 3);
#   4. rodada com VOLUME sobre 50 organizacoes (40 com desfecho, 10 EM ABERTO): previsao gerada com os
#      numeros conferidos A MAO (gate, blocos PAVA monotonicos, AUC/Brier/Brier skill na validacao,
#      previsao por organizacao em aberto, faixa do Data Contract);
#   5. VOLUME INSUFICIENTE: contrato com minimo 41 => ABSTEVE exit 6 e `modelo: null` (nenhuma previsao);
#   6. LEITURA PURA: snapshot das 12 tabelas antes/depois igual e a transacao READ ONLY recusando escrita;
#   7. determinismo (duas rodadas -> mesmo hash_do_relatorio), saida sem PII e HTML auto-contido;
#   8. PREVISAO NAO APLICADA: `aplicado=false` e o Data Contract intacto (sha256 antes/depois).
#   9. HIGIENE: o aceite mede o PROPRIO rastro — teardown com `docker rm -f -v` e nenhum
#      volume anonimo novo no fim (o baseline de docker nao pode mentir por causa do aceite).
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_pontuacao_preditiva_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w9e02t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-analytics-pred
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/agentes/analytics/pontuacao_preditiva.py"
CONTRATO="hermes/agentes/analytics/pontuacao-preditiva-v1.json"
CALIB="hermes/agentes/analytics/calibracao_score.py"
MANTER=0
for arg in "$@"; do [ "$arg" = "--manter" ] && MANTER=1; done

item() { # item <nome> <0|1> [detalhe]
  if [ "$2" = "0" ]; then echo "OK    $1"; OK=$((OK+1)); else echo "FALHOU $1 ${3:-}"; FALHAS=$((FALHAS+1)); fi
}
psql_q() { docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -t -A -c "$1" 2>/dev/null; }
volumes_anonimos() { docker volume ls -q 2>/dev/null | grep -E '^[0-9a-f]{64}$' | sort; }
limpar() { docker rm -f -v "$PG" >/dev/null 2>&1; }
[ "$MANTER" = "1" ] || trap limpar EXIT

rm -rf "$BASE"; mkdir -p "$BASE/out"
cd "$REPO" || exit 1
ANON_ANTES="$(volumes_anonimos | tr '\n' ' ')"

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
for arquivo in "$COMPONENTE" "$CONTRATO" "$CALIB" scripts/agentes/verificar_pontuacao_preditiva.py; do
  [ -f "$arquivo" ] && item "presente no repo: $arquivo" 0 || item "presente no repo: $arquivo" 1
done

echo "== 1. guardas de ambiente (sem banco)"
python3 "$COMPONENTE" --ambiente prod >"$BASE/out/prod.out" 2>&1
RC=$?
[ "$RC" = "4" ] && item "prod RECUSA por desenho (exit 4)" 0 || item "prod RECUSA por desenho (exit 4)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente prod --calibracao /tmp/nao-existe.json >"$BASE/out/prod2.out" 2>&1
RC=$?
[ "$RC" = "4" ] && item "prod RECUSA antes de ler calibracao/contrato (exit 4)" 0 \
  || item "prod RECUSA antes de ler calibracao/contrato (exit 4)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente dev --porta-banco "ssh root@10.0.0.1 psql" --calibracao /tmp/x.json \
  >"$BASE/out/remoto.out" 2>&1
RC=$?
[ "$RC" = "3" ] && grep -q "BANCO_NAO_E_DEV" "$BASE/out/remoto.out" \
  && item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 0 \
  || item "porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)" 1 "exit=$RC $(tail -1 "$BASE/out/remoto.out")"
python3 "$COMPONENTE" --ambiente dev --planejar >"$BASE/out/plano.out" 2>&1
if [ "$?" = "0" ] && grep -q "minimo_de_coorte=30" "$BASE/out/plano.out" && grep -q "PAVA" "$BASE/out/plano.out"; then
  item "--planejar declara gate e curva sem banco (exit 0)" 0
else
  item "--planejar declara gate e curva sem banco (exit 0)" 1 "$(tail -2 "$BASE/out/plano.out")"
fi
python3 "$COMPONENTE" --ambiente dev --conferir >"$BASE/out/conferir.out" 2>&1
[ "$?" = "0" ] && item "--conferir valida contrato e dependencias sem banco (exit 0)" 0 \
  || item "--conferir valida contrato e dependencias sem banco (exit 0)" 1 "exit=$?"

echo "== 2. suite offline do componente"
python3 scripts/agentes/verificar_pontuacao_preditiva.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_PONTUACAO_PASS" "$BASE/out/verificador.out"; then
  item "suite offline verde (itens + dentes)" 0
else
  item "suite offline verde (itens + dentes)" 1 "$(tail -3 "$BASE/out/verificador.out")"
fi

python3 scripts/agentes/verificar_calibracao_score.py --autoteste >"$BASE/out/verificador-calib.out" 2>&1
if grep -q "VERIFICADOR_CALIBRACAO_PASS" "$BASE/out/verificador-calib.out"; then
  item "regressao da dependencia: suite offline da calibracao (W9-E01-T01) verde" 0
else
  item "regressao da dependencia: suite offline da calibracao (W9-E01-T01) verde" 1 \
    "$(tail -2 "$BASE/out/verificador-calib.out")"
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
[ "$TABELAS" = "12" ] && item "schema com 12 tabelas" 0 || item "schema com 12 tabelas" 1 "(obtido '$TABELAS')"

echo "== 4. base semeada: 50 organizacoes (40 com desfecho, 10 EM ABERTO)"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source)
SELECT ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'Empresa ' || i, 'e' || i || '.test',
       'DISCOVERED', 'SITE'
FROM generate_series(1, 50) i;

-- componentes: ICP ANTI-correlacionado (90 nos perdidos, 10 nos ganhos), AUTOMATION_FIT e
-- BUYING_SIGNAL constantes (50) e DATA_QUALITY correlacionado (41..60) => o peso em vigor
-- (0.35 no ICP) aponta para o LADO ERRADO e a calibracao tem o que propor.
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at, valid_until)
SELECT ('0b000000-0000-0000-0000-' || lpad((i * 10 + k)::text, 12, '0'))::uuid,
       ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid,
       (ARRAY['ICP','AUTOMATION_FIT','BUYING_SIGNAL','DATA_QUALITY'])[k],
       (CASE WHEN k = 1 THEN (CASE WHEN i > 20 THEN 10.00 ELSE 90.00 END)
             WHEN k = 4 THEN (40 + i)::numeric
             ELSE 50.00 END),
       'v1', TIMESTAMPTZ '2026-09-30T12:00:00+00', NULL
FROM generate_series(1, 50) i, generate_series(1, 4) k;

-- PRIORITY gravado pela formula V1: perdas caem em 63,1..65,0 e ganhos em 37,1..39,0 (invertido)
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at, valid_until)
SELECT ('0a000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid,
       ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'PRIORITY',
       ROUND(0.35 * (CASE WHEN i > 20 THEN 10.00 ELSE 90.00 END) + 0.30 * 50 + 0.25 * 50
             + 0.10 * (40 + i), 2),
       'v1', TIMESTAMPTZ '2026-09-30T12:00:00+00', NULL
FROM generate_series(1, 50) i;

-- desfecho das organizacoes 1..40 (a 41..50 fica EM ABERTO: e' a populacao PREVISTA), com TRES
-- excecoes (i=5 e i=15 Won, i=25 Lost) para o desfecho nao ser perfeitamente separavel.
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, request_payload)
SELECT ('0d000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'crm.lead',
       ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'odoo', 'postgres',
       CASE WHEN (i > 20 AND i <> 25) OR i IN (5, 15) THEN 'OPPORTUNITY_WON' ELSE 'OPPORTUNITY_LOST' END,
       'odoo:acc:' || i, 'COMPLETED',
       '{"payload":{"valor":1000}}'::jsonb
FROM generate_series(1, 40) i;
SQL
item "base semeada (organizations/scores/sync_events)" $?

echo "== 5. snapshot das 12 tabelas ANTES + sha256 do Data Contract"
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
CONTRATO_ANTES=$(sha256sum "$CONTRATO" | cut -d' ' -f1)

echo "== 6. DEPENDENCIA: sem calibracao RECUSA; corte divergente RECUSA"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" >"$BASE/out/semcalib.out" 2>&1
RC=$?
[ "$RC" = "3" ] && grep -q "DEPENDENCIA_CALIBRACAO" "$BASE/out/semcalib.out" \
  && item "sem --calibracao RECUSA (DEPENDENCIA_CALIBRACAO, exit 3)" 0 \
  || item "sem --calibracao RECUSA (DEPENDENCIA_CALIBRACAO, exit 3)" 1 "exit=$RC $(tail -1 "$BASE/out/semcalib.out")"

echo "== 7. calibracao (W9-E01-T01) roda ANTES: e' a dependencia medida"
python3 "$CALIB" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out" >"$BASE/out/calibracao.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/calibracao-score.json" ]; then
  item "relatorio da calibracao gerado (exit 0)" 0
else
  item "relatorio da calibracao gerado (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/calibracao.out")"
fi
python3 - "$BASE/out/calibracao-score.json" <<'PY' >"$BASE/out/calib-corte.out" 2>&1
import json, sys
c = json.load(open(sys.argv[1]))
c["parametros"]["fracao_de_ajuste"] = {"numerador": 1, "denominador": 2}
json.dump(c, open(sys.argv[1].replace(".json", "-corte-divergente.json"), "w"))
PY
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" \
  --calibracao "$BASE/out/calibracao-score-corte-divergente.json" >"$BASE/out/cortediv.out" 2>&1
RC=$?
[ "$RC" = "3" ] && grep -q "CORTE_DIVERGENTE" "$BASE/out/cortediv.out" \
  && item "corte ajuste/validacao divergente do relatorio RECUSA (CORTE_DIVERGENTE, exit 3)" 0 \
  || item "corte ajuste/validacao divergente do relatorio RECUSA (CORTE_DIVERGENTE, exit 3)" 1 "exit=$RC $(tail -1 "$BASE/out/cortediv.out")"

echo "== 8. rodada com VOLUME (50 organizacoes: 40 com desfecho + 10 em aberto)"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" \
  --calibracao "$BASE/out/calibracao-score.json" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out" >"$BASE/out/pontuacao.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/pontuacao-preditiva.json" ]; then
  item "componente gerou o relatorio JSON + HTML com volume (exit 0)" 0
else
  item "componente gerou o relatorio JSON + HTML com volume (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/pontuacao.out")"
fi
grep -q "PONTUACAO_PREDITIVA_OK" "$BASE/out/pontuacao.out" \
  && item "resumo declara PONTUACAO_PREDITIVA_OK" 0 || item "resumo declara PONTUACAO_PREDITIVA_OK" 1

echo "== 9. numeros conferidos A MAO no relatorio"
python3 - "$BASE/out/pontuacao-preditiva.json" <<'PY' >"$BASE/out/numeros.out" 2>&1
import json, sys
r = json.load(open(sys.argv[1]))
g, m, av = r["gate_de_volume"], r["modelo"], r["avaliacao"]
prev = r["previsao_por_organizacao"]
falhas = []
def checar(nome, cond, obtido=""):
    if not cond:
        falhas.append("%s (%s)" % (nome, obtido))
checar("gate: 50 organizacoes, 50 na coorte valida, 40 com desfecho, 10 em aberto",
       (g["organizacoes"], g["coorte_valida"], g["com_desfecho"], g["sem_desfecho"]) == (50, 50, 40, 10),
       json.dumps(g))
checar("gate: won 21 / lost 19 e base_suficiente",
       (g["won"], g["lost"]) == (21, 19) and g["base_suficiente"], json.dumps(g))
checar("pesos em uso vem da PROPOSTA da calibracao", r["calibracao"]["origem_dos_pesos"] == "proposta_de_calibracao",
       r["calibracao"]["origem_dos_pesos"])
checar("pesos em uso somam 100 (pontos percentuais inteiros)",
       sum(int(round(float(v) * 100)) for v in r["calibracao"]["pesos"].values()) == 100,
       json.dumps(r["calibracao"]["pesos"]))
checar("modelo: 10 binos declarados", m["bins"] == 10, str(m["bins"]))
checar("modelo: blocos PAVA monotonicos nao-decrescentes",
       all(m["blocos"][i]["probabilidade"] <= m["blocos"][i + 1]["probabilidade"]
           for i in range(len(m["blocos"]) - 1)),
       json.dumps([b["probabilidade"] for b in m["blocos"]]))
checar("modelo: blocos cobrem so' bins com base e a soma de n = lado do ajuste",
       sum(b["n"] for b in m["blocos"]) == av["ajuste"]["organizacoes"],
       json.dumps([b["n"] for b in m["blocos"]]))
primeiro = next((l["bino"] for l in r["confiabilidade"] if l["previsto"] is not None), None)
checar("modelo: bin antes do primeiro bloco NAO recebe previsao; do primeiro em diante recebe",
       primeiro is not None
       and all(l["previsto"] is None for l in r["confiabilidade"] if l["bino"] < primeiro)
       and all(l["previsto"] is not None for l in r["confiabilidade"] if l["bino"] >= primeiro),
       json.dumps([(l["bino"], l["previsto"]) for l in r["confiabilidade"]]))
checar("avaliacao: AUC de validacao > 0.8 (o peso proposto separa fora da amostra)",
       (av["validacao"]["auc"] or 0) > 0.8, str(av["validacao"]["auc"]))
checar("avaliacao: Brier de validacao menor que o da taxa-base (skill > 0)",
       av["validacao"]["brier"] < av["validacao"]["brier_de_base"] and av["validacao"]["brier_skill"] > 0,
       json.dumps({"brier": av["validacao"]["brier"], "base": av["validacao"]["brier_de_base"],
                   "skill": av["validacao"]["brier_skill"]}))
checar("avaliacao: cobertura 100% nos dois lados (todo bino tem bloco herdado)",
       av["ajuste"]["cobertura_pct"] == 100.0 and av["validacao"]["cobertura_pct"] == 100.0,
       json.dumps([av["ajuste"]["cobertura_pct"], av["validacao"]["cobertura_pct"]]))
checar("previsao: 10 organizacoes em aberto previstas, sem lacuna de base",
       prev["total"] == 10 and prev["sem_base"] == 0 and prev["organizacoes_em_aberto"] == 10, json.dumps(prev["total"]))
checar("previsao: todo item traz UUID, score, bino, faixa do Data Contract e probabilidade em [0,1]",
       all(i["organizacao"] and i["score"] is not None and i["bino"] in range(10)
           and i["faixa"] in ("Nurture", "C", "B", "A", "A+") and 0.0 <= i["probabilidade_de_ganho"] <= 1.0
           for i in prev["itens"]), json.dumps(prev["itens"][:2]))
checar("previsao NAO aplicada (aplicado/exige_versao_nova/aprovacao humana)",
       r["aplicacao"]["aplicado"] is False and r["aplicacao"]["exige_versao_nova"] is True
       and r["aplicacao"]["aprovacao_humana"] == "pendente", json.dumps(r["aplicacao"]))
checar("lacunas: 10 organizacoes sem desfecho nomeadas", r["lacunas"]["sem_desfecho"] == 10,
       str(r["lacunas"]["sem_desfecho"]))
print("NUMEROS_A_MAO %s" % ("OK" if not falhas else "FALHOU: " + "; ".join(falhas)))
PY
cat "$BASE/out/numeros.out"
grep -q "NUMEROS_A_MAO OK" "$BASE/out/numeros.out" \
  && item "numeros conferidos a mao (gate, blocos, AUC/Brier/classe, previsao, nao aplicada)" 0 \
  || item "numeros conferidos a mao (gate, blocos, AUC/Brier/classe, previsao, nao aplicada)" 1 \
     "$(tail -2 "$BASE/out/numeros.out")"

echo "== 10. VOLUME INSUFICIENTE: contrato com minimo 41 => ABSTEVE (exit 6)"
python3 - "$CONTRATO" "$BASE/out/contrato-min41.json" <<'PY' >"$BASE/out/min41.out" 2>&1
import json, sys
c = json.load(open(sys.argv[1]))
c["parametros"]["minimo_de_coorte"] = 41
json.dump(c, open(sys.argv[2], "w"), ensure_ascii=False)
PY
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" \
  --calibracao "$BASE/out/calibracao-score.json" --contrato "$BASE/out/contrato-min41.json" \
  --saida "$BASE/out/absteve" --agora 2026-10-03T00:00:00Z >"$BASE/out/absteve.out" 2>&1
RC=$?
[ "$RC" = "6" ] && grep -q "PONTUACAO_ABSTEVE_VOLUME" "$BASE/out/absteve.out" \
  && item "coorte abaixo do minimo ABSTEVE (exit 6)" 0 \
  || item "coorte abaixo do minimo ABSTEVE (exit 6)" 1 "exit=$RC $(tail -1 "$BASE/out/absteve.out")"
python3 - "$BASE/out/absteve/pontuacao-preditiva.json" <<'PY' >"$BASE/out/absteve-modelo.out" 2>&1
import json, sys
r = json.load(open(sys.argv[1]))
ok = (r["modelo"] is None and r["previsao_por_organizacao"] is None and r["avaliacao"] is None
      and "COORTE_COM_DESFECHO_ABAIXO_DO_MINIMO" in r["gate_de_volume"]["motivos"])
print("SEM_MODELO" if ok else "TEM_MODELO %s" % json.dumps(r["gate_de_volume"]["motivos"]))
PY
grep -q "SEM_MODELO" "$BASE/out/absteve-modelo.out" \
  && item "abstencao NAO produz modelo nem previsao (modelo: null)" 0 \
  || item "abstencao NAO produz modelo nem previsao (modelo: null)" 1 "$(cat "$BASE/out/absteve-modelo.out")"

echo "== 11. leitura pura, nao-aplicacao e determinismo"
DEPOIS=$(snapshot | tr '\n' ' ')
if [ -n "$ANTES" ] && [ "$ANTES" = "$DEPOIS" ]; then
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 0
else
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 1 "antes=$ANTES depois=$DEPOIS"
fi
CONTRATO_DEPOIS=$(sha256sum "$CONTRATO" | cut -d' ' -f1)
[ "$CONTRATO_ANTES" = "$CONTRATO_DEPOIS" ] \
  && item "PREVISAO NAO APLICADA: Data Contract intacto (sha256 antes/depois)" 0 \
  || item "PREVISAO NAO APLICADA: Data Contract intacto (sha256 antes/depois)" 1
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q \
  -c "SET default_transaction_read_only = on" \
  -c "INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version) VALUES ('00000000-0000-0000-0000-0000000000ff','00000000-0000-0000-0000-000000000001','PRIORITY',10.00,'v1')" \
  >"$BASE/out/readonly.out" 2>&1
grep -q "read-only transaction" "$BASE/out/readonly.out" \
  && item "transacao READ ONLY recusa escrita (o PostgreSQL barra)" 0 \
  || item "transacao READ ONLY recusa escrita (o PostgreSQL barra)" 1 "$(tail -1 "$BASE/out/readonly.out")"
mkdir -p "$BASE/out/rodada2"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" \
  --calibracao "$BASE/out/calibracao-score.json" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out/rodada2" >"$BASE/out/rodada2.out" 2>&1
H1=$(python3 -c "import json;print(json.load(open('$BASE/out/pontuacao-preditiva.json'))['hash_do_relatorio'])")
H2=$(python3 -c "import json;print(json.load(open('$BASE/out/rodada2/pontuacao-preditiva.json'))['hash_do_relatorio'])")
[ "$H1" = "$H2" ] && item "determinismo: duas rodadas -> mesmo hash_do_relatorio" 0 \
  || item "determinismo: duas rodadas -> mesmo hash_do_relatorio" 1 "$H1 != $H2"

echo "== 12. saida: sem PII, HTML auto-contido, sem SQL proprio no componente"
if grep -qiE "email|telefone|whatsapp|cnpj|legal_name|contact_name" "$BASE/out/pontuacao-preditiva.json"; then
  item "saida sem PII (nenhum campo de contato/nome)" 1 "$(grep -oiE 'email|telefone|whatsapp|cnpj|legal_name|contact_name' "$BASE/out/pontuacao-preditiva.json" | sort -u | tr '\n' ' ')"
else
  item "saida sem PII (nenhum campo de contato/nome)" 0
fi
if grep -qE "<script|src=|http://|https://" "$BASE/out/pontuacao-preditiva.html"; then
  item "HTML auto-contido (sem script e sem recurso externo)" 1
else
  item "HTML auto-contido (sem script e sem recurso externo)" 0
fi
if grep -qE "SELECT |INSERT |UPDATE |DELETE " "$COMPONENTE"; then
  item "componente sem SQL proprio (leitura e' do instrumento)" 1
else
  item "componente sem SQL proprio (leitura e' do instrumento)" 0
fi

echo "== 13. higiene: o aceite prova que NAO deixa volume anonimo novo"
# O teardown e' o MESMO do trap (`docker rm -f -v`): o aceite mede o PROPRIO rastro. Sem o `-v` o
# container descartavel deixa o volume anonimo da imagem `postgres:16` para tras (defeito t_3148dbbf:
# cada rodada de aceite vazava 1 volume e o baseline de docker mentia em silencio).
if [ "$MANTER" = "1" ]; then
  item "higiene: pulado em --manter (container mantido de proposito)" 0
else
  limpar   # a MESMA limpeza do trap: aqui o aceite mede o rastro que ELE deixa
  novos=0
  for v in $(volumes_anonimos); do
    case " $ANON_ANTES " in
      *" $v "*) ;;
      *) novos=$((novos + 1)) ;;
    esac
  done
  if [ "$novos" = "0" ]; then
    item "higiene: nenhum volume anonimo novo depois do teardown com -v" 0
  else
    item "higiene: nenhum volume anonimo novo depois do teardown com -v" 1 \
      "$novos volume(s) novo(s): $(volumes_anonimos | tr '\n' ' ')"
  fi
fi

echo
echo "ACEITE_PONTUACAO_PREDITIVA $([ "$FALHAS" = "0" ] && echo OK || echo FALHOU) ($OK itens OK, $FALHAS falhas)"
[ "$FALHAS" = "0" ] || exit 1
