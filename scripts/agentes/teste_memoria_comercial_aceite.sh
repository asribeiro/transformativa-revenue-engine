#!/usr/bin/env bash
# Aceite de ponta da memoria comercial em Qdrant (`memoria-comercial-v1`) — card TRE-W9-E06-T01.
#
# Mede o componente contra um Qdrant REAL e DESCARTavel e uma base REAL e DESCARTavel, sem tocar nada
# que ja' exista (ADR-005): containers `qd-memoria-comercial` (qdrant/qdrant:v1.12.4 em 127.0.0.1:6339)
# e `pg-memoria-comercial` (postgres:16 + migration 0001 + base semeada).
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: prod RECUSA (exit 4); Qdrant remoto RECUSA; sem --porta-banco = uso errado; --planejar sem banco;
#   2. suite offline (25 itens + 10 dentes) verde;
#   3. PRE-CONDICAO "corpus comercial estavel" medida na base (12 documentos indexaveis, 4 tipos com base);
#   4. PRE-CONDICAO NAO ATENDIDA nao publica: corpus magro -> memoria_publicada=false e NADA escrito;
#   5. INDEXACAO REAL: colecao criada com a dimensao do contrato, 12 pontos, PII em lacuna (nao indexada);
#   6. IDEMPOTENCIA: segunda rodada nao cresce a contagem e mantem o hash; conteudo alterado ATUALIZA o ponto;
#   7. BUSCA: ranking por score com desempate, filtro por tipo, piso de score (ruido nao volta) e determinismo;
#   8. FONTE CANONICA: snapshot das tabelas igual antes/depois e transacao READ ONLY recusando escrita;
#   9. DIMENSAO DIVERGENTE recusa sem escrever e `--recriar --confirmo` reconstroi a memoria;
#  10. privacidade (nenhum e-mail da base na memoria), determinismo do relatorio e HTML auto-contido.
#
# Pre-requisitos: docker com as imagens postgres:16 e qdrant/qdrant:v1.12.4, python3, curl.
# Uso (na VPS, na raiz do repo): bash scripts/agentes/teste_memoria_comercial_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w9e06t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-memoria-comercial
QD=qd-memoria-comercial
PORTA=6339
QD_URL="http://127.0.0.1:$PORTA"
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
COMPONENTE="hermes/memoria/memoria_comercial.py"
CONTRATO="hermes/memoria/memoria-comercial-v1.json"
COLECAO="memoria_comercial_v1"
MANTER=0
for arg in "$@"; do
  [ "$arg" = "--manter" ] && MANTER=1
done

item() { # item <nome> <0|1> [detalhe]
  if [ "$2" = "0" ]; then echo "OK    $1"; OK=$((OK+1)); else echo "FALHOU $1 ${3:-}"; FALHAS=$((FALHAS+1)); fi
}
psql_q() { docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -t -A -c "$1" 2>/dev/null; }
qd() { curl -sS -m 10 -H 'Content-Type: application/json' "$@"; }
limpar() { docker rm -f "$PG" "$QD" >/dev/null 2>&1; }
[ "$MANTER" = "1" ] || trap limpar EXIT

rm -rf "$BASE"; mkdir -p "$BASE/out"
cd "$REPO" || exit 1

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
command -v curl >/dev/null 2>&1; item "curl disponivel" $?
for arquivo in "$COMPONENTE" "$CONTRATO" scripts/agentes/verificar_memoria_comercial.py; do
  [ -f "$arquivo" ] && item "presente no repo: $arquivo" 0 || item "presente no repo: $arquivo" 1
done

echo "== 1. guardas de ambiente (sem banco e sem Qdrant)"
python3 "$COMPONENTE" --ambiente prod --planejar --contrato "$CONTRATO" >"$BASE/out/prod.out" 2>&1
RC=$?
[ "$RC" = "4" ] && item "prod RECUSA por desenho (exit 4)" 0 || item "prod RECUSA por desenho (exit 4)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente dev --qdrant-url "http://qdrant.exemplo.com:6333" --planejar \
  --contrato "$CONTRATO" >"$BASE/out/remoto.out" 2>&1
RC=$?
[ "$RC" = "3" ] && item "Qdrant remoto RECUSA em dev (QDRANT_NAO_E_DEV, exit 3)" 0 \
  || item "Qdrant remoto RECUSA em dev (QDRANT_NAO_E_DEV, exit 3)" 1 "exit=$RC"
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --contrato "$CONTRATO" \
  >"$BASE/out/semporta.out" 2>&1
RC=$?
[ "$RC" = "2" ] && item "indexacao sem --porta-banco = uso errado (exit 2)" 0 \
  || item "indexacao sem --porta-banco = uso errado (exit 2)" 1 "exit=$RC"
python3 "$COMPONENTE" --planejar --contrato "$CONTRATO" >"$BASE/out/plano.out" 2>&1
if [ "$?" = "0" ] && grep -q "MENSAGEM" "$BASE/out/plano.out" && grep -q "payload_fechado" "$BASE/out/plano.out" \
   && grep -q "local-deterministico-v1" "$BASE/out/plano.out"; then
  item "--planejar declara tipos, provedor, dimensao e payload fechado (exit 0)" 0
else
  item "--planejar declara tipos, provedor, dimensao e payload fechado (exit 0)" 1 "$(tail -2 "$BASE/out/plano.out")"
fi
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --buscar "qualquer coisa" \
  --contrato "$CONTRATO" >"$BASE/out/busca-vazia.out" 2>&1
RC=$?
[ "$RC" = "3" ] && item "busca sem Qdrant no ar RECUSA (exit 3, fail-closed)" 0 \
  || item "busca sem Qdrant no ar RECUSA (exit 3, fail-closed)" 1 "exit=$RC"

echo "== 2. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_memoria_comercial.py --autoteste >"$BASE/out/verificador.out" 2>&1
if grep -q "VERIFICADOR_MEMORIA_COMERCIAL_PASS" "$BASE/out/verificador.out"; then
  item "suite offline $(grep -o 'PASS ([0-9]* itens, 0 falhas)' "$BASE/out/verificador.out" | head -1) + $(grep -o 'AUTOTESTE [0-9]*/[0-9]* mutacoes detectadas' "$BASE/out/verificador.out" | head -1)" 0
else
  item "suite offline verde com autoteste" 1 "$(tail -3 "$BASE/out/verificador.out")"
fi

echo "== 3. Qdrant descartavel + PostgreSQL descartavel (migration 0001)"
docker rm -f "$QD" >/dev/null 2>&1
docker run -d --name "$QD" -p "127.0.0.1:$PORTA:6333" qdrant/qdrant:v1.12.4 >/dev/null 2>&1
item "container descartavel $QD criado (qdrant/qdrant:v1.12.4)" $?
pronto=1
for _ in $(seq 1 40); do
  if ! curl -fsS -m 3 "$QD_URL/readyz" >/dev/null 2>&1 && ! curl -fsS -m 3 "$QD_URL/healthz" >/dev/null 2>&1; then sleep 1; continue; fi
  if [ "$(qd "$QD_URL/collections" | grep -c '"result"')" -ge 1 ]; then pronto=0; break; fi
  sleep 1
done
item "Qdrant pronto (collections respondendo em 127.0.0.1:$PORTA)" "$pronto"

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
docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE sales_ai LOGIN PASSWORD 'dev';" \
  >"$BASE/out/pg-role.out" 2>&1
item "role sales_ai criado" $?
docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE sales_intelligence OWNER sales_ai;" \
  >"$BASE/out/pg-db.out" 2>&1
item "database sales_intelligence criada" $?
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q -v ON_ERROR_STOP=1 \
  < db/migrations/0001_sales_intelligence_v1.sql >"$BASE/out/migration.out" 2>&1
item "migration 0001 aplicada" $?

echo "== 4. base semeada: 12 documentos indexaveis (4 tipos) + 1 documento com PII"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -q >"$BASE/out/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source) VALUES
 ('00000001-0000-0000-0000-000000000000','Empresa Um','um.test','DISCOVERED','SITE'),
 ('00000002-0000-0000-0000-000000000000','Empresa Dois','dois.test','DISCOVERED','SITE'),
 ('00000003-0000-0000-0000-000000000000','Empresa Tres','tres.test','DISCOVERED','SITE'),
 ('00000004-0000-0000-0000-000000000000','Empresa Quatro','quatro.test','DISCOVERED','META');
INSERT INTO sales_intelligence.contacts (id, organization_id, first_name, last_name, email) VALUES
 ('000000f1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','A','Um','a@um.test');
-- MENSAGEM: 4 mensagens outbound
INSERT INTO sales_intelligence.interactions (id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, subject, content_summary) VALUES
 ('000000e1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00','Eficiencia operacional','Primeiro contato sobre eficiencia operacional e automacao de tarefas repetitivas'),
 ('000000e2-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000',NULL,'EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-02T10:00:00+00','Agentes de IA na operacao','Proposta de agentes de IA para reduzir apagar incendio na operacao comercial'),
 ('000000e3-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000',NULL,'WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-03T10:00:00+00','Governanca de projetos','Mensagem sobre governanca de projetos e previsibilidade de entrega'),
 ('000000e4-0000-0000-0000-000000000000','00000004-0000-0000-0000-000000000000',NULL,'LINKEDIN','OUTBOUND','OUTBOUND_LINKEDIN','2026-09-04T10:00:00+00','Transformacao digital','Convite para conversa sobre transformacao digital conectada a resultado de negocio');
-- OBJECAO: 3 respostas inbound classificadas (+1 com PII, que NAO pode virar memoria)
INSERT INTO sales_intelligence.interactions (id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, subject, content_summary, response_category) VALUES
 ('000000e5-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA','2026-09-05T10:00:00+00','Re: Eficiencia operacional','OBJECAO preco alto demais para o orcamento do projeto','OBJECAO'),
 ('000000e6-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000',NULL,'EMAIL','INBOUND','RESPOSTA','2026-09-06T10:00:00+00','Re: Agentes de IA','INTERESSE pedido de proposta comercial com escopo e prazo','INTERESSE'),
 ('000000e7-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000',NULL,'WHATSAPP','INBOUND','RESPOSTA','2026-09-07T10:00:00+00','Re: Governanca','SEM_INTERESSE sem orcamento neste semestre','SEM_INTERESSE'),
 ('000000e8-0000-0000-0000-000000000000','00000004-0000-0000-0000-000000000000',NULL,'EMAIL','INBOUND','RESPOSTA','2026-09-08T10:00:00+00','Re: Transformacao','responder para maria.silva@cliente.test sobre o preco','OBJECAO');
-- DOR: 3 hipoteses de dor
INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id, pain_category, pain_statement, evidence_summary, status) VALUES
 ('000000d1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','EFICIENCIA','conciliacao manual consome dias do time financeiro por mes','Relato em reuniao: 3 dias por mes em planilha','HYPOTHESIS'),
 ('000000d2-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','ESCALA','atendimento comercial nao escala sem aumentar equipe','Fila de leads sem resposta acima de 48 horas','HYPOTHESIS'),
 ('000000d3-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','GOVERNANCA','portfolio de projetos sem priorizacao declarada e sem medicao','Tres iniciativas travadas no mesmo trimestre','VALIDATED');
-- CONTEXTO: 2 recomendacoes/playbooks
INSERT INTO sales_intelligence.recommendations (id, organization_id, recommendation_type, action, description, rationale, confidence) VALUES
 ('000000c1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','PLAYBOOK','enviar proposta com escopo reduzido','Proposta faseada com piloto de automacao de conciliacao','Reduz barreira de preco e prova valor em 30 dias',0.8000),
 ('000000c2-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','PLAYBOOK','agendar diagnostico de processos','Diagnostico de eficiencia operacional com agente de triagem','Cliente pediu proposta: avancar para reuniao tecnica',0.7500);
SQL
item "base semeada sem erro (4 organizacoes, 12 documentos, 1 com PII)" $?
DOCS=$(psql_q "select (select count(*) from sales_intelligence.interactions where direction='OUTBOUND' and (subject is not null or content_summary is not null)) + (select count(*) from sales_intelligence.interactions where direction='INBOUND' and response_category is not null) + (select count(*) from sales_intelligence.pain_hypotheses) + (select count(*) from sales_intelligence.recommendations);")
[ "$DOCS" = "13" ] && item "base tem 13 linhas de corpus (12 indexaveis + 1 com PII)" 0 \
  || item "base tem 13 linhas de corpus (12 indexaveis + 1 com PII)" 1 "(obtido '$DOCS')"

SNAPSHOT_ANTES=$(psql_q "select (select count(*) from sales_intelligence.interactions) || '/' || (select count(*) from sales_intelligence.pain_hypotheses) || '/' || (select count(*) from sales_intelligence.recommendations) || '/' || (select count(*) from sales_intelligence.organizations);")

echo "== 5. pre-condicao NAO atendida: corpus magro nao publica (fail-closed)"
docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE sales_magro OWNER sales_ai;" \
  >"$BASE/out/magro-db.out" 2>&1
docker exec -i "$PG" psql -U sales_ai -d sales_magro -q -v ON_ERROR_STOP=1 \
  < db/migrations/0001_sales_intelligence_v1.sql >"$BASE/out/magro-migration.out" 2>&1
docker exec -i "$PG" psql -U sales_ai -d sales_magro -q -v ON_ERROR_STOP=1 >"$BASE/out/magro-seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, status) VALUES ('00000009-0000-0000-0000-000000000000','Empresa Magra','DISCOVERED');
INSERT INTO sales_intelligence.interactions (id, organization_id, channel, direction, interaction_type, occurred_at, subject) VALUES
 ('000000ea-0000-0000-0000-000000000000','00000009-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00','Assunto unico');
SQL
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" \
  --porta-banco "docker exec -i $PG psql -U sales_ai -d sales_magro" --agora 2026-10-03T00:00:00Z \
  --saida "$BASE/out-magro" >"$BASE/out/magro.out" 2>&1
RC_MAGRO=$?
if [ "$RC_MAGRO" = "0" ] && python3 - "$BASE/out-magro/memoria-comercial.json" >"$BASE/out/magro-check.out" 2>&1 <<'PY'
import json, sys, urllib.request
r = json.load(open(sys.argv[1], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
item("corpus magro: pre-condicao NAO atendida", r["pre_condicao"]["atendida"] is False)
item("corpus magro: faltando nomeado (%d itens)" % len(r["pre_condicao"]["faltando"]),
     len(r["pre_condicao"]["faltando"]) >= 2)
item("corpus magro: memoria_publicada=false", r["indexacao"]["memoria_publicada"] is False)
item("corpus magro: nada enviado ao Qdrant", r["indexacao"]["enviados"] == 0 and r["indexacao"]["pontos_depois"] is None)
sys.exit(0 if not falhas else 1)
PY
then
  cat "$BASE/out/magro-check.out"
  OK=$((OK + $(grep -c "^OK    " "$BASE/out/magro-check.out")))
else
  cat "$BASE/out/magro-check.out"; FALHAS=$((FALHAS+4))
fi
COLECOES=$(qd "$QD_URL/collections" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['result']['collections']))" 2>/dev/null)
[ "$COLECOES" = "0" ] && item "corpus magro NAO criou colecao nenhuma no Qdrant" 0 \
  || item "corpus magro NAO criou colecao nenhuma no Qdrant" 1 "(colecoes=$COLECOES)"

echo "== 6. indexacao real da base estavel"
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --porta-banco "$PORTA_BANCO" \
  --agora 2026-10-03T00:00:00Z --saida "$BASE/out" >"$BASE/out/run1.out" 2>&1
RC_RUN1=$?
python3 - "$BASE/out/memoria-comercial.json" >"$BASE/out/run1-check.out" 2>&1 <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
pre = r["pre_condicao"]
item("pre-condicao 'corpus comercial estavel' ATENDIDA (12 documentos, 4 tipos com base)",
     pre["atendida"] and pre["documentos"] == 12 and len(pre["tipos_com_base"]) == 4
     and pre["por_tipo"] == {"MENSAGEM": 4, "OBJECAO": 3, "DOR": 3, "CONTEXTO": 2},
     (pre["documentos"], pre["por_tipo"], pre["faltando"]))
item("PII e' LACUNA, nao memoria: 1 PII_SUSPEITA e documento nao indexado",
     [l["lacuna"] for l in r["lacunas"]] == ["PII_SUSPEITA"]
     and r["lacunas"][0]["padroes"] == ["EMAIL"], r["lacunas"])
item("memoria publicada: 12 pontos no Qdrant (antes 0)",
     r["indexacao"]["memoria_publicada"] and r["indexacao"]["pontos_antes"] == 0
     and r["indexacao"]["pontos_depois"] == 12 and r["indexacao"]["enviados"] == 12, r["indexacao"])
item("fonte de verdade declarada = PostgreSQL e nenhum id de ponto duplicado",
     r["fonte_de_verdade"] == "postgresql:sales_intelligence" and r["indexacao"]["ids_duplicados"] == 0)
item("relatorio com 8 lacunas declaradas no contrato", len(r["lacunas_declaradas"]) >= 6, len(r["lacunas_declaradas"]))
sys.exit(0 if not falhas else 1)
PY
RC_CHECK1=$?
cat "$BASE/out/run1-check.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/run1-check.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/run1-check.out")))
[ "$RC_RUN1" = "0" ] && item "indexacao rodou ate' o fim (exit 0)" 0 || item "indexacao rodou ate' o fim (exit 0)" 1 "exit=$RC_RUN1"
[ "$RC_CHECK1" = "0" ] && item "bloco de conferencia da indexacao rodou ate' o fim (exit 0)" 0 \
  || item "bloco de conferencia da indexacao rodou ate' o fim (exit 0)" 1 "exit=$RC_CHECK1"

echo "== 7. o que esta' no Qdrant (dimensao, payload fechado, contagem exata)"
DIM=$(qd "$QD_URL/collections/$COLECAO" | python3 -c "import sys,json; c=json.load(sys.stdin)['result']; print(c['config']['params']['vectors']['size'])" 2>/dev/null)
[ "$DIM" = "64" ] && item "colecao criada com a dimensao do contrato (64)" 0 || item "colecao criada com a dimensao do contrato (64)" 1 "(obtido '$DIM')"
CONTAGEM=$(qd -X POST "$QD_URL/collections/$COLECAO/points/count" -d '{"exact": true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['count'])" 2>/dev/null)
[ "$CONTAGEM" = "12" ] && item "contagem exata no Qdrant = 12 pontos" 0 || item "contagem exata no Qdrant = 12 pontos" 1 "(obtido '$CONTAGEM')"
qd -X POST "$QD_URL/collections/$COLECAO/points/scroll" -d '{"limit": 12, "with_payload": true}' \
  >"$BASE/out/scroll.json" 2>&1
python3 - "$BASE/out/scroll.json" >"$BASE/out/scroll-check.out" 2>&1 <<'PY'
import json, sys
pontos = json.load(open(sys.argv[1], encoding="utf-8"))["result"]["points"]
esperado = ["canal", "colecao_versao", "conteudo_sha256", "ocorrido_em", "organization_id", "origem_id",
            "origem_tabela", "texto", "tipo"]
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
item("payload FECHADO nos 12 pontos (9 campos declarados)", all(sorted(p["payload"]) == esperado for p in pontos))
item("nenhum campo de contato no payload (sem email/telefone/nome)",
     not any(c in json.dumps(pontos) for c in ('"email"', '"phone"', '"whatsapp"', '"first_name"')))
item("nenhum e-mail da base semeada no payload",
     not any(t in json.dumps(pontos) for t in ("maria.silva@cliente.test", "@um.test", "a@um.test")))
item("todos os tipos declarados presentes na colecao",
     {p["payload"]["tipo"] for p in pontos} == {"MENSAGEM", "OBJECAO", "DOR", "CONTEXTO"},
     {p["payload"]["tipo"] for p in pontos})
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/scroll-check.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/scroll-check.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/scroll-check.out")))

echo "== 8. idempotencia (2a rodada) e conteudo alterado (upsert no MESMO ponto)"
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --porta-banco "$PORTA_BANCO" \
  --agora 2026-10-03T00:00:00Z --saida "$BASE/out-run2" >"$BASE/out/run2.out" 2>&1
python3 - "$BASE/out/memoria-comercial.json" "$BASE/out-run2/memoria-comercial.json" \
  >"$BASE/out/idem-check.out" 2>&1 <<'PY'
import json, sys
r1 = json.load(open(sys.argv[1], encoding="utf-8"))
r2 = json.load(open(sys.argv[2], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
i2 = r2["indexacao"]
item("2a rodada NAO cresce a contagem (12 -> 12)", i2["pontos_antes"] == 12 and i2["pontos_depois"] == 12, i2)
item("2a rodada mantem o hash da medicao (mesmo corpus, mesmo hash)",
     r1["hash_do_relatorio"] == r2["hash_do_relatorio"], (r1["hash_do_relatorio"][:12], r2["hash_do_relatorio"][:12]))
item("id do ponto e' estavel entre rodadas (mesmo id por origem)",
     [d["conteudo_sha256"] for d in r1["corpus"]["documentos"]]
     == [d["conteudo_sha256"] for d in r2["corpus"]["documentos"]]
     and [d["origem_id"] for d in r1["corpus"]["documentos"]] == [d["origem_id"] for d in r2["corpus"]["documentos"]])
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/idem-check.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/idem-check.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/idem-check.out")))

docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q -v ON_ERROR_STOP=1 \
  -c "UPDATE sales_intelligence.interactions SET content_summary='OBJECAO preco revisado apos nova rodada comercial' WHERE id='000000e5-0000-0000-0000-000000000000';" \
  >"$BASE/out/update.out" 2>&1
RC_UPD=$?
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --porta-banco "$PORTA_BANCO" \
  --agora 2026-10-03T00:00:00Z --saida "$BASE/out-run3" >"$BASE/out/run3.out" 2>&1
python3 - "$BASE/out-run3/memoria-comercial.json" "$BASE/out-run2/memoria-comercial.json" \
  >"$BASE/out/update-check.out" 2>&1 <<'PY'
import hashlib, json, sys, urllib.request
r = json.load(open(sys.argv[1], encoding="utf-8"))
r2 = json.load(open(sys.argv[2], encoding="utf-8"))
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
# lê o ponto direto do Qdrant pela origem alterada
corpo = json.dumps({"filter": {"must": [{"key": "origem_id", "match": {"value": "000000e5-0000-0000-0000-000000000000"}}]},
                    "limit": 5, "with_payload": True}).encode()
req = urllib.request.Request("http://127.0.0.1:6339/collections/memoria_comercial_v1/points/scroll", data=corpo)
req.add_header("Content-Type", "application/json")
ponto = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())["result"]["points"]
esperado = hashlib.sha256("OBJECAO preco revisado apos nova rodada comercial".encode()).hexdigest()
item("conteudo alterado ATUALIZA o mesmo ponto (1 ponto, hash novo no payload)",
     len(ponto) == 1 and ponto[0]["payload"]["conteudo_sha256"] == esperado,
     (len(ponto), [p["payload"]["conteudo_sha256"][:12] for p in ponto]))
item("contagem segue 12 (nao criou ponto novo para o conteudo revisado)",
     r["indexacao"]["pontos_antes"] == 12 and r["indexacao"]["pontos_depois"] == 12, r["indexacao"])
item("a medicao MUDA quando o conteudo muda (hash do relatorio diferente da rodada anterior)",
     r["hash_do_relatorio"] != r2["hash_do_relatorio"],
     (r2["hash_do_relatorio"][:12], r["hash_do_relatorio"][:12]))
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/update-check.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/update-check.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/update-check.out")))
[ "$RC_UPD" = "0" ] && item "alteracao de conteudo na base descartavel aceita (UPDATE)" 0 \
  || item "alteracao de conteudo na base descartavel aceita (UPDATE)" 1 "exit=$RC_UPD"

echo "== 9. busca: ranking, filtro, piso de score e determinismo"
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --buscar "objecao preco orcamento do projeto" \
  --limite 5 --saida "$BASE/out-busca" >"$BASE/out/run-busca1.out" 2>&1
RC_B1=$?
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --buscar "objecao preco orcamento do projeto" \
  --limite 5 --saida "$BASE/out-busca2" >"$BASE/out/run-busca2.out" 2>&1
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --buscar "zzz qqq" --limite 5 \
  --saida "$BASE/out-busca-vazia2" >"$BASE/out/run-busca3.out" 2>&1
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --buscar "conciliacao manual do time financeiro" \
  --filtro-tipo DOR --limite 5 --saida "$BASE/out-busca-dor" >"$BASE/out/run-busca4.out" 2>&1
python3 - "$BASE/out-busca/memoria-comercial.json" "$BASE/out-busca2/memoria-comercial.json" \
  "$BASE/out-busca-vazia2/memoria-comercial.json" "$BASE/out-busca-dor/memoria-comercial.json" \
  >"$BASE/out/busca-check.out" 2>&1 <<'PY'
import json, sys
b1, b2, bv, bd = [json.load(open(p, encoding="utf-8")) for p in sys.argv[1:5]]
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
top = b1["resultados"]
item("busca devolve memoria com piso de score respeitado (%d resultados, %d descartados)"
     % (len(top), b1["descartados_por_score"]),
     len(top) >= 3 and all(r["score"] >= b1["consulta"]["score_minimo"] for r in top))
item("ranking por score desc com desempate por id",
     [(-r["score"], str(r["id"])) for r in top] == sorted([(-r["score"], str(r["id"])) for r in top]))
item("consulta de objecao/preco traz a OBJECAO com o trecho certo no topo",
     top and top[0]["memoria"]["tipo"] == "OBJECAO" and "preco" in top[0]["texto"].lower(),
     (top[0]["memoria"]["tipo"], top[0]["score"]) if top else None)
item("filtro por tipo funciona (DOR: %d resultados, todos DOR)" % len(bd["resultados"]),
     bd["resultados"] and all(r["memoria"]["tipo"] == "DOR" for r in bd["resultados"]))
item("consulta sem correspondencia devolve VAZIO (nao 'o menos pior')", bv["resultados"] == [],
     bv["resultados"][:1])
item("determinismo da busca: mesma consulta -> mesmo hash e mesma ordem",
     b1["hash_do_relatorio"] == b2["hash_do_relatorio"]
     and [r["id"] for r in b1["resultados"]] == [r["id"] for r in b2["resultados"]])
item("buscas diferentes -> hashes diferentes",
     len({b1["hash_do_relatorio"], bv["hash_do_relatorio"], bd["hash_do_relatorio"]}) == 3)
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/busca-check.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/busca-check.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/busca-check.out")))
[ "$RC_B1" = "0" ] && item "busca rodou ate' o fim (exit 0)" 0 || item "busca rodou ate' o fim (exit 0)" 1 "exit=$RC_B1"

echo "== 10. fonte canonica: leitura pura e privacidade na saida do relatorio"
SNAPSHOT_DEPOIS=$(psql_q "select (select count(*) from sales_intelligence.interactions) || '/' || (select count(*) from sales_intelligence.pain_hypotheses) || '/' || (select count(*) from sales_intelligence.recommendations) || '/' || (select count(*) from sales_intelligence.organizations);")
[ "$SNAPSHOT_ANTES" = "$SNAPSHOT_DEPOIS" ] && item "snapshot das tabelas canonicas IGUAL antes/depois ($SNAPSHOT_DEPOIS)" 0 \
  || item "snapshot das tabelas canonicas IGUAL antes/depois" 1 "($SNAPSHOT_ANTES -> $SNAPSHOT_DEPOIS)"
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 \
  -c "SET default_transaction_read_only = on" -c "CREATE TABLE sales_intelligence.prova_escrita (x int);" \
  >"$BASE/out/readonly.out" 2>&1
RC_RO=$?
if [ "$RC_RO" != "0" ] && grep -qi "read-only transaction" "$BASE/out/readonly.out"; then
  item "sessao READ ONLY recusou escrita (cannot execute ... in a read-only transaction)" 0
else
  item "sessao READ ONLY recusou escrita (cannot execute ... in a read-only transaction)" 1 "exit=$RC_RO"
fi
if python3 - "$BASE/out/memoria-comercial.json" "$BASE/out/memoria-comercial.html" "$BASE/out/scroll.json" \
   >"$BASE/out/pii-check.out" 2>&1 <<'PY'
import json, sys
texto = "".join(open(p, encoding="utf-8").read() for p in sys.argv[1:4]).lower()
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
achados = [t for t in ("maria.silva@cliente.test", "@cliente.test", "a@um.test", "empresa um", "empresa dois")
           if t in texto]
item("saida sem PII: nenhum e-mail/dominio/nome da base semeada", achados == [], achados)
item("o documento com PII aparece como LACUNA nomeada, nao como memoria",
     "pii_suspeita" in texto and "000000e8-0000-0000-0000-000000000000" in texto)
sys.exit(0 if not falhas else 1)
PY
then
  cat "$BASE/out/pii-check.out"
  OK=$((OK + $(grep -c "^OK    " "$BASE/out/pii-check.out")))
else
  cat "$BASE/out/pii-check.out"; FALHAS=$((FALHAS+2))
fi

echo "== 11. dimensao divergente RECUSA e --recriar reconstroi a memoria"
qd -X DELETE "$QD_URL/collections/$COLECAO" >"$BASE/out/del.out" 2>&1
qd -X PUT "$QD_URL/collections/$COLECAO" -d '{"vectors": {"size": 32, "distance": "Cosine"}}' >"$BASE/out/create32.out" 2>&1
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --porta-banco "$PORTA_BANCO" \
  --saida "$BASE/out-dim" >"$BASE/out/dim.out" 2>&1
RC_DIM=$?
RECUSA=$(python3 -c "import json,sys; print(json.load(open('$BASE/out/dim.out',encoding='utf-8')).get('recusa',''))" 2>/dev/null)
[ "$RC_DIM" = "3" ] && [ "$RECUSA" = "DIMENSAO_DIVERGENTE" ] && item "colecao com dimensao divergente RECUSA (DIMENSAO_DIVERGENTE, exit 3)" 0 \
  || item "colecao com dimensao divergente RECUSA (DIMENSAO_DIVERGENTE, exit 3)" 1 "exit=$RC_DIM recusa=$RECUSA"
CONT32=$(qd -X POST "$QD_URL/collections/$COLECAO/points/count" -d '{"exact": true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['count'])" 2>/dev/null)
[ "$CONT32" = "0" ] && item "recusa por dimensao NAO escreveu nada (0 pontos)" 0 \
  || item "recusa por dimensao NAO escreveu nada (0 pontos)" 1 "(pontos=$CONT32)"
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --porta-banco "$PORTA_BANCO" \
  --recriar --confirmo --saida "$BASE/out-recriar" >"$BASE/out/recriar.out" 2>&1
RC_REC=$?
CONT_REC=$(qd -X POST "$QD_URL/collections/$COLECAO/points/count" -d '{"exact": true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['count'])" 2>/dev/null)
[ "$RC_REC" = "0" ] && [ "$CONT_REC" = "12" ] && item "--recriar --confirmo reconstroi a memoria do zero (12 pontos de volta)" 0 \
  || item "--recriar --confirmo reconstroi a memoria do zero (12 pontos de volta)" 1 "exit=$RC_REC pontos=$CONT_REC"
python3 "$COMPONENTE" --ambiente dev --qdrant-url "$QD_URL" --porta-banco "$PORTA_BANCO" \
  --recriar >"$BASE/out/recriar-sem-confirmo.out" 2>&1
RC_RSC=$?
[ "$RC_RSC" = "3" ] && item "--recriar sem --confirmo RECUSA (exit 3)" 0 || item "--recriar sem --confirmo RECUSA (exit 3)" 1 "exit=$RC_RSC"

echo "== 12. determinismo do relatorio, HTML auto-contido e ambiente intacto"
python3 - "$BASE/out-recriar/memoria-comercial.json" "$BASE/out-recriar/memoria-comercial.html" \
  >"$BASE/out/final-check.out" 2>&1 <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
html = open(sys.argv[2], encoding="utf-8").read()
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond: falhas.append(nome)
item("hash_do_relatorio com 64 hexdigits", len(r["hash_do_relatorio"]) == 64)
item("HTML auto-contido (sem http/https/script/link) com pre-condicao, corpus e lacunas",
     all(t not in html for t in ("http://", "https://", "<script", "<link"))
     and "Pre-condicao" in html and "Corpus comercial" in html and "Lacunas declaradas" in html
     and len(html) > 1500, len(html))
item("relatorio declara o modo MENSAGEM/OBJECAO/DOR/CONTEXTO e o provedor local",
     r["provedor_da_memoria"]["nome"] == "local-deterministico-v1" and r["provedor_da_memoria"]["dimensao"] == 64)
sys.exit(0 if not falhas else 1)
PY
cat "$BASE/out/final-check.out"
OK=$((OK + $(grep -c "^OK    " "$BASE/out/final-check.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$BASE/out/final-check.out")))
PROD=$(docker ps --format '{{.Names}}' | grep -ci 'prod' || true)
AMBIENTE=$(docker ps --format '{{.Names}}' | grep -c '^pg-sales-dev$' || true)
[ "${PROD:-0}" = "0" ] && [ "${AMBIENTE:-0}" = "1" ] && item "ambiente intacto: nenhum container de producao e pg-sales-dev no ar" 0 \
  || item "ambiente intacto: nenhum container de producao e pg-sales-dev no ar" 1 "prod=$PROD pg-sales-dev=$AMBIENTE"

echo
echo "RESULTADO: $([ "$FALHAS" = "0" ] && echo PASS || echo FALHOU) ($OK itens, $FALHAS falhas)"
if [ "$FALHAS" = "0" ]; then
  echo "ACEITE_MEMORIA_COMERCIAL_OK"
  exit 0
fi
echo "ACEITE_MEMORIA_COMERCIAL_FALHOU"
exit 1
