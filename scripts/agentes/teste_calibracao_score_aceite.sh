#!/usr/bin/env bash
# Aceite de ponta da calibracao do score (`calibracao-score-v1`) — card TRE-W9-E01-T01.
#
# Mede o componente contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-analytics-calib` (127.0.0.1) + migration 0001 + base semeada com sinal conhecido,
# mais a suite offline do INSTRUMENTO (W8-E03) como regressao da dependencia.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4) antes de ler contrato; porta remota RECUSA em dev; --planejar/--conferir sem banco;
#   2. suite offline do componente (51 itens + 7 dentes) verde;
#   3. VOLUME: base de 40 organizacoes com desfecho => base_suficiente e PROPOSTA_GERADA, com os
#      numeros conferidos A MAO (incumbente invertido: AUC ~0; proposto zera o componente anti-
#      correlacionado); base fina (contrato com minimo 41) => ABSTEVE exit 6 e proposta nenhuma;
#   4. FAIXAS: 5 faixas contiguas cobrindo 0..100, soma igual a coorte;
#   5. LEITURA PURA: snapshot das 12 tabelas antes/depois igual e a transacao READ ONLY recusando escrita;
#   6. determinismo (duas rodadas -> mesmo hash_do_relatorio), saida sem PII e HTML auto-contido;
#   7. PROPOSTA NAO APLICADA: `aplicado=false` e o Data Contract intacto (sha256 antes/depois igual);
#   8. REGRESSAO da suite offline do INSTRUMENTO (W8-E03).
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_calibracao_score_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w9e01t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-analytics-calib
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/agentes/analytics/calibracao_score.py"
CONTRATO="hermes/agentes/analytics/calibracao-score-v1.json"
MANTER=0
for arg in "$@"; do [ "$arg" = "--manter" ] && MANTER=1; done

item() { # item <nome> <0|1> [detalhe]
  if [ "$2" = "0" ]; then echo "OK    $1"; OK=$((OK+1)); else echo "FALHOU $1 ${3:-}"; FALHAS=$((FALHAS+1)); fi
}
psql_q() { docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -t -A -c "$1" 2>/dev/null; }
limpar() { docker rm -f -v "$PG" >/dev/null 2>&1; }
[ "$MANTER" = "1" ] || trap limpar EXIT

rm -rf "$BASE"; mkdir -p "$BASE/out"
cd "$REPO" || exit 1

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
for arquivo in "$COMPONENTE" "$CONTRATO" scripts/agentes/verificar_calibracao_score.py; do
  [ -f "$arquivo" ] && item "presente no repo: $arquivo" 0 || item "presente no repo: $arquivo" 1
done

echo "== 1. guardas de ambiente (sem banco)"
python3 "$COMPONENTE" --ambiente prod >"$BASE/out/prod.out" 2>&1
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
if [ "$?" = "0" ] && grep -q "minimo_de_coorte=30" "$BASE/out/plano.out" && grep -q "A+" "$BASE/out/plano.out"; then
  item "--planejar declara gate, pesos e faixas do Data Contract sem banco (exit 0)" 0
else
  item "--planejar declara gate, pesos e faixas do Data Contract sem banco (exit 0)" 1 "$(tail -2 "$BASE/out/plano.out")"
fi
python3 "$COMPONENTE" --ambiente dev --conferir >"$BASE/out/conferir.out" 2>&1
[ "$?" = "0" ] && item "--conferir valida contrato e dependencias sem banco (exit 0)" 0 \
  || item "--conferir valida contrato e dependencias sem banco (exit 0)" 1 "exit=$?"

echo "== 2. suite offline do componente (51 itens + 7 dentes)"
python3 scripts/agentes/verificar_calibracao_score.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_CALIBRACAO_PASS" "$BASE/out/verificador.out"; then
  item "suite offline verde (51 itens + 7 dentes)" 0
else
  item "suite offline verde (51 itens + 7 dentes)" 1 "$(tail -3 "$BASE/out/verificador.out")"
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

echo "== 4. base semeada: 40 organizacoes, desfecho empurrado pelo DATA_QUALITY"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source)
SELECT ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'Empresa ' || i, 'e' || i || '.test',
       'DISCOVERED', 'SITE'
FROM generate_series(1, 40) i;

-- componentes: ICP ANTI-correlacionado (90 nos perdidos, 10 nos ganhos), AUTOMATION_FIT e
-- BUYING_SIGNAL constantes (50) e DATA_QUALITY correlacionado (41..80) => o peso em vigor
-- (0.35 no ICP) aponta para o LADO ERRADO e a calibracao tem o que propor.
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at, valid_until)
SELECT ('0b000000-0000-0000-0000-' || lpad((i * 10 + k)::text, 12, '0'))::uuid,
       ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid,
       (ARRAY['ICP','AUTOMATION_FIT','BUYING_SIGNAL','DATA_QUALITY'])[k],
       (CASE WHEN k = 1 THEN (CASE WHEN i > 20 THEN 10.00 ELSE 90.00 END)
             WHEN k = 4 THEN (40 + i)::numeric
             ELSE 50.00 END),
       'v1', TIMESTAMPTZ '2026-09-30T12:00:00+00', NULL
FROM generate_series(1, 40) i, generate_series(1, 4) k;

-- PRIORITY gravado pela formula V1: perdas caem em 63,1..65,0 e ganhos em 37,1..39,0 (invertido)
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version, calculated_at, valid_until)
SELECT ('0a000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid,
       ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'PRIORITY',
       ROUND(0.35 * (CASE WHEN i > 20 THEN 10.00 ELSE 90.00 END) + 0.30 * 50 + 0.25 * 50 + 0.10 * (40 + i), 2),
       'v1', TIMESTAMPTZ '2026-09-30T12:00:00+00', NULL
FROM generate_series(1, 40) i;

-- desfecho: Won para i > 20, Lost para i <= 20 (a trilha Odoo -> PostgreSQL e' o caminho do funil),
-- com TRES excecoes (i=5 e i=15 Won, i=25 Lost) para o desfecho nao ser perfeitamente separavel —
-- sem elas o Youden nao teria corte a oferecer e a proposta de faixas nem existiria (achado medido).
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, request_payload)
SELECT ('0d000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'crm.lead',
       ('00000000-0000-0000-0000-' || lpad(i::text, 12, '0'))::uuid, 'odoo', 'postgres',
       CASE WHEN (i > 20 AND i <> 25) OR i IN (5, 15) THEN 'OPPORTUNITY_WON' ELSE 'OPPORTUNITY_LOST' END,
       'odoo:acc:' || i, 'COMPLETED',
       '{"payload":{"valor":1000}}'::jsonb
FROM generate_series(1, 40) i;
SQL
item "base semeada (organizations/scores/sync_events)" $?

echo "== 5. snapshot das 12 tabelas ANTES + sha256 do Data Contract (prova de leitura pura e de nada aplicado)"
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

echo "== 6. rodada com VOLUME: dev, porta local, base de 40 com desfecho"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out" >"$BASE/out/calibracao.out" 2>&1
RC=$?
if [ "$RC" = "0" ] && [ -f "$BASE/out/calibracao-score.json" ]; then
  item "componente gerou o relatorio JSON + HTML com volume (exit 0)" 0
else
  item "componente gerou o relatorio JSON + HTML com volume (exit 0)" 1 "exit=$RC $(tail -2 "$BASE/out/calibracao.out")"
fi
grep -q "CALIBRACAO_SCORE_OK" "$BASE/out/calibracao.out" \
  && item "resumo declara CALIBRACAO_SCORE_OK" 0 || item "resumo declara CALIBRACAO_SCORE_OK" 1

echo "== 7. numeros conferidos A MAO no relatorio"
python3 - "$BASE/out/calibracao-score.json" <<'PY' >"$BASE/out/numeros.out" 2>&1
import json, sys
r = json.load(open(sys.argv[1]))
g, p, inc = r["gate_de_volume"], r["proposta"], r["incumbente"]
falhas = []
def checar(nome, cond, obtido=""):
    if not cond:
        falhas.append("%s (%s)" % (nome, obtido))
checar("gate: 40 organizacoes, 40 na coorte valida, 40 com desfecho",
       (g["organizacoes"], g["coorte_valida"], g["com_desfecho"]) == (40, 40, 40), json.dumps(g))
checar("gate: won 21 / lost 19 e base_suficiente",
       (g["won"], g["lost"]) == (21, 19) and g["base_suficiente"], json.dumps(g))
checar("incumbente: AUC muito abaixo do acaso no ajuste (o peso em vigor aponta para o lado errado)",
       inc["ajuste"]["auc"] < 0.2, str(inc["ajuste"]["auc"]))
checar("incumbente: AUC muito abaixo do acaso na validacao", inc["validacao"]["auc"] < 0.2,
       str(inc["validacao"]["auc"]))
checar("status PROPOSTA_GERADA", p["status"] == "PROPOSTA_GERADA", p["status"])
pesos = p["pesos"] or {}
checar("pesos: proposta APROVADA (ganho na validacao >= 0.02)", pesos.get("aprovada") is True,
       json.dumps(pesos.get("ganho_na_validacao")))
checar("pesos: o componente anti-correlacionado (ICP) vai a ZERO", pesos.get("vetor", [None])[0] == 0,
       str(pesos.get("vetor")))
checar("pesos: o sinal (DATA_QUALITY) fica com peso > 0", pesos.get("vetor", [0, 0, 0, 0])[3] > 0,
       str(pesos.get("vetor")))
checar("pesos: AUC proposta > 0.9 nos dois lados",
       pesos["ajuste"]["auc"] > 0.9 and pesos["validacao"]["auc"] > 0.9, json.dumps(pesos.get("validacao")))
checar("pesos: somam 1.00", sum(__import__("decimal").Decimal(v) for v in pesos["pesos"].values()) == __import__("decimal").Decimal("1.00"))
faixas = r["faixas_propostas"]
checar("faixas: o relatorio traz faixas propostas", bool(faixas), str(len(faixas)))
if not faixas:  # sem faixa proposta os itens abaixo REPROVAM (com mensagem), nao explodem
    faixas = [{"faixa": "?", "min": "?", "max": "?", "organizacoes": 0, "taxa_de_vitoria_pct": -1}] * 5
checar("faixas: 5, na ordem ascendente do Data Contract",
       [f["faixa"] for f in faixas] == ["Nurture", "C", "B", "A", "A+"], str([f["faixa"] for f in faixas]))
checar("faixas: contiguas (max + 0.01 == proximo min)",
       all(__import__("decimal").Decimal(faixas[i]["max"]) + __import__("decimal").Decimal("0.01")
           == __import__("decimal").Decimal(faixas[i + 1]["min"]) for i in range(len(faixas) - 1)),
       json.dumps([(f["min"], f["max"]) for f in faixas]))
checar("faixas: cobrem 0..100", faixas[0]["min"] == "0.00" and faixas[-1]["max"] == "100.00")
checar("faixas: soma das organizacoes = coorte com desfecho",
       sum(f["organizacoes"] for f in faixas) == g["com_desfecho"], str(sum(f["organizacoes"] for f in faixas)))
checar("faixas: separam o desfecho (A+ >= 90% e melhor que Nurture)",
       faixas[-1]["taxa_de_vitoria_pct"] >= 90.0
       and faixas[-1]["taxa_de_vitoria_pct"] > faixas[0]["taxa_de_vitoria_pct"],
       json.dumps([f["taxa_de_vitoria_pct"] for f in faixas]))
checar("proposta NAO aplicada (aplicado/exige_versao_nova/aprovacao humana)",
       p["aplicado"] is False and p["exige_versao_nova"] is True and p["aprovacao_humana"] == "pendente")
checar("determinismo declarado por hash", bool(r.get("hash_do_relatorio")))
print("NUMEROS_A_MAO %s" % ("OK" if not falhas else "FALHOU: " + "; ".join(falhas)))
PY
cat "$BASE/out/numeros.out"
grep -q "NUMEROS_A_MAO OK" "$BASE/out/numeros.out" \
  && item "numeros conferidos a mao no relatorio (gate, AUC, pesos, faixas, nao aplicada)" 0 \
  || item "numeros conferidos a mao no relatorio (gate, AUC, pesos, faixas, nao aplicada)" 1 "$(tail -2 "$BASE/out/numeros.out")"

DEPOIS=$(snapshot | tr '\n' ' ')
if [ -n "$ANTES" ] && [ "$ANTES" = "$DEPOIS" ]; then
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 0
else
  item "LEITURA PURA: snapshot das 12 tabelas identico antes/depois" 1 "antes=$ANTES depois=$DEPOIS"
fi
CONTRATO_DEPOIS=$(sha256sum "$CONTRATO" | cut -d' ' -f1)
[ "$CONTRATO_ANTES" = "$CONTRATO_DEPOIS" ] \
  && item "PROPOSTA NAO APLICADA: Data Contract intacto (sha256 antes/depois)" 0 \
  || item "PROPOSTA NAO APLICADA: Data Contract intacto (sha256 antes/depois)" 1
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q \
  -c "SET default_transaction_read_only = on" \
  -c "INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version) VALUES ('00000000-0000-0000-0000-0000000000ff','00000000-0000-0000-0000-000000000001','PRIORITY',10.00,'v1')" \
  >"$BASE/out/readonly.out" 2>&1
if [ $? -ne 0 ] && grep -qi "read-only" "$BASE/out/readonly.out"; then
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 0
else
  item "READ ONLY: a propria transacao recusa escrita (mecanismo provado)" 1 "$(tail -2 "$BASE/out/readonly.out")"
fi
NAO_ENTROU=$(psql_q "select count(*) from sales_intelligence.scores where id='00000000-0000-0000-0000-0000000000ff';")
[ "$NAO_ENTROU" = "0" ] && item "escrita recusada nao deixou linha" 0 || item "escrita recusada nao deixou linha" 1

echo "== 8. determinismo e saida sem PII"
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out/r2" >"$BASE/out/calibracao2.out" 2>&1
H1=$(python3 -c "import json;print(json.load(open('$BASE/out/calibracao-score.json'))['hash_do_relatorio'])" 2>/dev/null)
H2=$(python3 -c "import json;print(json.load(open('$BASE/out/r2/calibracao-score.json'))['hash_do_relatorio'])" 2>/dev/null)
if [ -n "$H1" ] && [ "$H1" = "$H2" ]; then
  item "determinismo: duas rodadas -> mesmo hash_do_relatorio" 0
else
  item "determinismo: duas rodadas -> mesmo hash_do_relatorio" 1 "h1=$H1 h2=$H2"
fi
if grep -qE "e[0-9]+\.test|Empresa [0-9]|legal_name|email|telefone|whatsapp|cnpj" "$BASE/out/calibracao-score.json"; then
  item "saida sem PII (nenhum nome, dominio, e-mail ou telefone)" 1
else
  item "saida sem PII (nenhum nome, dominio, e-mail ou telefone)" 0
fi
grep -q "<script" "$BASE/out/calibracao-score.html" \
  && item "HTML auto-contido (sem recurso externo)" 1 || item "HTML auto-contido (sem recurso externo)" 0

echo "== 9. VOLUME INSUFICIENTE: a pre-condicao do card vira exit 6"
python3 - "$CONTRATO" "$BASE/out/contrato-minimo-41.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
c["parametros"]["minimo_de_coorte"] = 41  # acima das 40 organizacoes da base
json.dump(c, open(sys.argv[2], "w"), ensure_ascii=False, indent=2)
PY
python3 "$COMPONENTE" --ambiente dev --porta-banco "$PORTA_BANCO" --agora 2026-10-03T00:00:00Z \
  --contrato "$BASE/out/contrato-minimo-41.json" --saida "$BASE/out/absteve" >"$BASE/out/absteve.out" 2>&1
RC=$?
[ "$RC" = "6" ] && item "minimo de coorte nao atingido RECUSA com exit 6 (ABSTEVE)" 0 \
  || item "minimo de coorte nao atingido RECUSA com exit 6 (ABSTEVE)" 1 "exit=$RC $(tail -2 "$BASE/out/absteve.out")"
grep -q "CALIBRACAO_ABSTEVE_VOLUME" "$BASE/out/absteve.out" \
  && item "resumo declara ABSTEVE com o motivo nomeado" 0 || item "resumo declara ABSTEVE com o motivo nomeado" 1
python3 - "$BASE/out/absteve/calibracao-score.json" <<'PY' >"$BASE/out/absteve-analise.out" 2>&1
import json, sys
r = json.load(open(sys.argv[1]))
p, g = r["proposta"], r["gate_de_volume"]
ok = (p["status"] == "ABSTEVE" and p["pesos"] is None and r["faixas_propostas"] == []
      and g["base_suficiente"] is False and "COORTE_COM_DESFECHO_ABAIXO_DO_MINIMO" in g["motivos"]
      and r["incumbente"] is None)
print("ABSTEVE_SEM_PROPOSTA %s base_suficiente=%s motivos=%s" % (
    "OK" if ok else "FALHOU", g["base_suficiente"], ",".join(g["motivos"])))
PY
cat "$BASE/out/absteve-analise.out"
grep -q "ABSTEVE_SEM_PROPOSTA OK" "$BASE/out/absteve-analise.out" \
  && item "ABSTEVE: relatorio sai, proposta NAO (nada ajustado sobre base pequena)" 0 \
  || item "ABSTEVE: relatorio sai, proposta NAO (nada ajustado sobre base pequena)" 1 "$(tail -1 "$BASE/out/absteve-analise.out")"

echo "== 10. regressao da dependencia: suite offline do INSTRUMENTO (W8-E03)"
python3 scripts/agentes/verificar_efetividade_score.py --autoteste >"$BASE/out/instrumento.out" 2>&1
if grep -q "VERIFICADOR_EFETIVIDADE_PASS" "$BASE/out/instrumento.out"; then
  item "regressao: suite do instrumento (W8-E03) continua verde" 0
else
  item "regressao: suite do instrumento (W8-E03) continua verde" 1 "$(tail -3 "$BASE/out/instrumento.out")"
fi

echo
if [ "$FALHAS" = "0" ]; then
  echo "ACEITE_CALIBRACAO_SCORE_OK ($OK itens, 0 falhas)"
  exit 0
fi
echo "ACEITE_CALIBRACAO_SCORE_FALHOU ($OK ok, $FALHAS falhas)"
exit 1
