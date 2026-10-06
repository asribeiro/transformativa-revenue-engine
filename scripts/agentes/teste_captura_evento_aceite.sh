#!/usr/bin/env bash
# Aceite de ponta do componente `captura_evento` (card TRE-W7-E06-T01 — event lead capture).
#
# Mede a cadeia INTEIRA contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-evt-acc` (127.0.0.1, porta do proprio docker) + migration 0001 + o componente
# `hermes/agentes/inbound/captura_evento.py` gravando em `sales_intelligence`.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: --planejar/--conferir sem banco; prod RECUSA (exit 4); --coleta sem porta local RECUSA;
#      prefixo de banco remoto RECUSA em dev (BANCO_NAO_E_DEV);
#   2. sem --confirmo a rodada e' DRY_RUN: snapshot das 12 tabelas antes/depois igual;
#   3. coleta valida de evento: 1 organization (source EVENTO_CAPTURA, status DISCOVERED), 1 contact
#      (legal_basis/source/bloqueios) e 1 interaction (EVENTO/INBOUND/CAPTURA_EVENTO) com UUID canonico,
#      occurred_at = capturado_em do evento e o vinculo do evento no resumo/referencia, + trilha CAPTURADO
#      com a chave `evento:<event_id>:<captura_id>`;
#   4. identificador FORTE (CNPJ) casa a organizacao existente: REUSO, zero organizacao nova;
#   5. identificador FRACO (nome+cidade) NAO faz merge: REVIEW_REQUIRED, zero cadastro novo;
#   6. consentimento e' barreira: sem opt-in/base legal/forma valida nada e' cadastrado (so' trilha);
#   7. idempotencia: reentrega da MESMA coleta -> JA_CAPTURADO, zero linha nova; e a MESMA captura_id em
#      OUTRO evento e' coleta distinta (o event_id entra na chave);
#   8. escopo de escrita: so' organizations/contacts/interactions/sync_events mudam (nenhum DDL/UPDATE/DELETE);
#   9. privacidade: e-mail/telefone do lead nao aparecem crus na evidencia do aceite;
#  10. dentes de ponta medidos no BANCO: sem event_id e com capture_method inventado nao viram cadastro
#      (barreira do vinculo do evento), e base legal inventada nao vira cadastro (barreira de consentimento).
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa (pontas em 127.0.0.1).
# Uso (na VPS, no repo): bash scripts/agentes/teste_captura_evento_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w7e06t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-evt-acc
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
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
COMPONENTE="python3 hermes/agentes/inbound/captura_evento.py"

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
[ -f hermes/agentes/inbound/captura_evento.py ] && [ -f hermes/agentes/inbound/captura-evento-v1.json ] \
  && item "componente e contrato presentes no repo" 0 || item "componente e contrato presentes no repo" 1

echo "== 1. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_captura_evento.py --autoteste >"$BASE/out/verificador.out" 2>&1
grep -q "VERIFICADOR_CAPTURA_EVENTO_PASS" "$BASE/out/verificador.out" \
  && item "suite offline + dentes verde" 0 || item "suite offline + dentes verde" 1 "$(tail -3 "$BASE/out/verificador.out")"

echo "== 2. PostgreSQL descartavel + migration 0001"
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
item "postgres pronto (init concluido: 2x 'ready to accept connections')" $pronto
criado=1
for _ in $(seq 1 15); do
  if docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE sales_ai LOGIN PASSWORD 'dev';" \
     >"$BASE/out/pg-role.out" 2>&1; then criado=0; break; fi
  sleep 2
done
item "role sales_ai criado" $criado
docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE sales_intelligence OWNER sales_ai;" \
  >"$BASE/out/pg-db.out" 2>&1
item "database sales_intelligence criada" $?
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q -v ON_ERROR_STOP=1 \
  < db/migrations/0001_sales_intelligence_v1.sql >"$BASE/out/migration.out" 2>&1
item "migration 0001 aplicada" $?
TABELAS=$(psql_q "select count(*) from information_schema.tables where table_schema='sales_intelligence';")
[ "$TABELAS" = "12" ] && item "schema com 12 tabelas" 0 || item "schema com 12 tabelas" 1 "(obtido '$TABELAS')"

echo "== 3. organizacao/contato existentes (destino do identificador forte e do fraco)"
psql_q "INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain, cnpj, city, state, status, source)
  VALUES ('11111111-1111-1111-1111-111111111111','Distribuidora Aurora LTDA','Distribuidora Aurora','aurora.test','12345678000190','Campinas','SP','DISCOVERED','EVENTO_CAPTURA');
  INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, legal_basis, source)
  VALUES ('22222222-2222-2222-2222-222222222222','11111111-1111-1111-1111-111111111111','Fulano Antigo','fulano@aurora.test','+55 19 90000-0000','CONSENTIMENTO','EVENTO_CAPTURA');" >/dev/null 2>&1
item "organizacao + contato de referencia inseridos" $?

echo "== 4. fixtures das coletas de evento"
python3 - "$BASE" <<'PY'
import json, sys, os
base = sys.argv[1]
fx = os.path.join(base, "fx"); os.makedirs(fx, exist_ok=True)

def coleta(cid, event_id="evento-pmi-sp-2026-10", metodo="QR_CODE", **kw):
    c = {
        "captura_id": cid,
        "origem_evento": {"event_id": event_id, "evento_nome": "Encontro PMI Sao Paulo — Gestao e IA",
                          "evento_inicio": "2026-10-02", "local": "Sao Paulo/SP", "stand": "A-12",
                          "capture_method": metodo, "capturado_em": "2026-10-02T17:40:00Z"},
        "consentimento": {"aceito": True, "legal_basis": "CONSENTIMENTO", "forma": "TERMO_DIGITAL",
                          "texto_versao": "privacidade-evento-v1"},
        "empresa": {"nome": "Metalurgica Bandeirante LTDA", "dominio": "bandeirante.test", "cidade": "Osasco",
                    "estado": "SP", "website_url": "https://bandeirante.test"},
        "contato": {"nome": "Marina Prado", "email": "marina@bandeirante.test", "telefone": "+55 11 97777-6655",
                    "cargo": "COO", "preferred_channel": "email"},
    }
    c.update(kw)
    return c

casos = {
    "nova.json": coleta("evt-0001"),
    "replay.json": coleta("evt-0001"),
    "outro_evento.json": coleta("evt-0001", event_id="evento-sebrae-2026-11", metodo="CRACHA"),
    "forte.json": coleta("evt-0002", empresa={"nome": "Distribuidora Aurora LTDA", "cnpj": "12.345.678/0001-90",
                                             "cidade": "Campinas", "estado": "SP"}),
    "fraca.json": coleta("evt-0003", empresa={"nome": "Distribuidora Aurora", "cidade": "Campinas",
                                             "estado": "SP"}),
    "sem_consentimento.json": coleta("evt-0004", consentimento={"aceito": False, "legal_basis": "CONSENTIMENTO",
                                                               "forma": "FICHA_ASSINADA"}),
    "base_invalida.json": coleta("evt-0005", consentimento={"aceito": True, "legal_basis": "BASE_INVENTADA",
                                                           "forma": "FICHA_ASSINADA"}),
    "sem_evento.json": coleta("evt-0006", event_id=""),
    "metodo_inventado.json": coleta("evt-0007", metodo="DRONE"),
    "sem_capturado_em.json": coleta("evt-0008"),
}
casos["sem_capturado_em.json"]["origem_evento"]["capturado_em"] = ""
for nome, corpo in casos.items():
    with open(os.path.join(fx, nome), "w", encoding="utf-8") as fh:
        json.dump(corpo, fh, ensure_ascii=False, indent=2)
print(f"{len(casos)} coletas geradas")
PY
item "10 coletas de evento geradas" $?

export TRE_AMBIENTE=dev TRE_EVENTO_PORTA_BANCO="$PORTA_BANCO"

echo "== 5. guardas antes de escrever"
$COMPONENTE --planejar >"$BASE/out/planejar.out" 2>&1; item "--planejar sem banco (exit 0)" $?
$COMPONENTE --conferir >"$BASE/out/conferir.out" 2>&1; item "--conferir (exit 0)" $?
$COMPONENTE --coleta "$BASE/fx/nova.json" --ambiente prod >"$BASE/out/prod.out" 2>&1
[ $? -eq 4 ] && item "prod RECUSA a captura (exit 4)" 0 || item "prod RECUSA a captura (exit 4)" 1
TRE_EVENTO_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence" \
  $COMPONENTE --coleta "$BASE/fx/nova.json" --confirmo >"$BASE/out/remoto.out" 2>&1
[ $? -eq 3 ] && grep -q BANCO_NAO_E_DEV "$BASE/out/remoto.out" \
  && item "dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV, exit 3)" 0 \
  || item "dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV, exit 3)" 1

echo "== 6. snapshot das 12 tabelas ANTES"
ANTES="$BASE/out/antes.txt"; : > "$ANTES"
for t in organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >> "$ANTES"
done

echo "== 7. dry-run sem --confirmo nao escreve"
$COMPONENTE --coleta "$BASE/fx/nova.json" >"$BASE/out/dry.out" 2>&1
item "dry-run concluido (exit 0)" $?
grep -q DRY_RUN "$BASE/out/dry.out" && item "dry-run declarado na saida (nada gravado)" 0 \
  || item "dry-run declarado na saida (nada gravado)" 1
N_ORG=$(psql_q "select count(*) from sales_intelligence.organizations;")
[ "$N_ORG" = "1" ] && item "dry-run: nenhuma organizacao nova (1)" 0 || item "dry-run: nenhuma organizacao nova" 1 "(obtido $N_ORG)"

echo "== 8. captura valida de empresa nova"
$COMPONENTE --coleta "$BASE/fx/nova.json" --confirmo --relatorio "$BASE/out/nova-relatorio.json" \
  --trilha "$BASE/out/nova-trilha.jsonl" >"$BASE/out/nova.out" 2>&1
item "captura executada (exit 0)" $?
grep -q '"status": "CAPTURADO"' "$BASE/out/nova.out" && item "veredito CAPTURADO" 0 || item "veredito CAPTURADO" 1
ORG_NOVA=$(psql_q "select count(*) from sales_intelligence.organizations where trade_name='Metalurgica Bandeirante LTDA' and source='EVENTO_CAPTURA' and status='DISCOVERED';")
[ "$ORG_NOVA" = "1" ] && item "1 organizacao nova com source/status do contrato" 0 \
  || item "1 organizacao nova com source/status do contrato" 1 "(obtido $ORG_NOVA)"
CT=$(psql_q "select count(*) from sales_intelligence.contacts where email='marina@bandeirante.test' and legal_basis='CONSENTIMENTO' and source='EVENTO_CAPTURA' and coalesce(do_not_contact,false)=false;")
[ "$CT" = "1" ] && item "1 contato com legal_basis/source/bloqueio corretos" 0 || item "1 contato com legal_basis/source/bloqueio corretos" 1 "(obtido $CT)"
IT=$(psql_q "select count(*) from sales_intelligence.interactions where channel='EVENTO' and direction='INBOUND' and interaction_type='CAPTURA_EVENTO' and content_reference='evt-0001';")
[ "$IT" = "1" ] && item "1 interacao EVENTO/INBOUND/CAPTURA_EVENTO ligada a coleta" 0 \
  || item "1 interacao EVENTO/INBOUND/CAPTURA_EVENTO ligada a coleta" 1 "(obtido $IT)"
VINC=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='evt-0001' and content_summary like '%evento-pmi-sp-2026-10%' and content_summary like '%metodo=QR_CODE%';")
[ "$VINC" = "1" ] && item "vinculo do evento no resumo da interacao (sem coluna nova)" 0 \
  || item "vinculo do evento no resumo da interacao (sem coluna nova)" 1 "(obtido $VINC)"
SEM_COLUNA=$(psql_q "select count(*) from information_schema.columns where table_schema='sales_intelligence' and table_name='interactions' and column_name in ('event_id','capture_method');")
[ "$SEM_COLUNA" = "0" ] && item "nenhuma coluna de evento inventada no schema" 0 \
  || item "nenhuma coluna de evento inventada no schema" 1 "(obtido $SEM_COLUNA)"
OCORRIDO=$(psql_q "select to_char(occurred_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from sales_intelligence.interactions where content_reference='evt-0001';")
[ "$OCORRIDO" = "2026-10-02T17:40:00Z" ] && item "occurred_at vem do capturado_em do evento" 0 \
  || item "occurred_at vem do capturado_em do evento" 1 "(obtido $OCORRIDO)"
TR=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='evento:evento-pmi-sp-2026-10:evt-0001' and status='CAPTURADO' and operation='CAPTURA_LEAD_EVENTO';")
[ "$TR" = "1" ] && item "trilha de idempotencia CAPTURADO (evento:<event_id>:<captura_id>)" 0 \
  || item "trilha de idempotencia CAPTURADO (evento:<event_id>:<captura_id>)" 1 "(obtido $TR)"
PAYLOAD=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='evento:evento-pmi-sp-2026-10:evt-0001' and request_payload::text like '%evento-pmi-sp-2026-10%' and request_payload::text like '%QR_CODE%';")
[ "$PAYLOAD" = "1" ] && item "payload da trilha carrega o event_id e o capture_method" 0 \
  || item "payload da trilha carrega o event_id e o capture_method" 1 "(obtido $PAYLOAD)"

echo "== 9. idempotencia (reentrega da mesma coleta)"
$COMPONENTE --coleta "$BASE/fx/replay.json" --confirmo >"$BASE/out/replay.out" 2>&1
item "reentrega executada (exit 0)" $?
grep -q JA_CAPTURADO "$BASE/out/replay.out" && item "reentrega devolve JA_CAPTURADO" 0 || item "reentrega devolve JA_CAPTURADO" 1
IT2=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='evt-0001';")
TR2=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='evento:evento-pmi-sp-2026-10:evt-0001';")
[ "$IT2" = "1" ] && [ "$TR2" = "1" ] && item "reentrega nao duplicou interacao nem trilha" 0 \
  || item "reentrega nao duplicou interacao nem trilha" 1 "(interacoes=$IT2 trilha=$TR2)"

echo "== 9b. a mesma captura_id em OUTRO evento e' coleta distinta"
$COMPONENTE --coleta "$BASE/fx/outro_evento.json" --confirmo >"$BASE/out/outro.out" 2>&1
item "coleta do outro evento executada (exit 0)" $?
grep -q '"status": "CAPTURADO"' "$BASE/out/outro.out" && item "outro evento CAPTURADO (nao e' replay)" 0 \
  || item "outro evento CAPTURADO (nao e' replay)" 1
IT3=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='evt-0001';")
[ "$IT3" = "2" ] && item "2 interacoes com a mesma captura_id (eventos distintos)" 0 \
  || item "2 interacoes com a mesma captura_id (eventos distintos)" 1 "(obtido $IT3)"
TR3=$(psql_q "select count(*) from sales_intelligence.sync_events where operation='CAPTURA_LEAD_EVENTO' and status='CAPTURADO';")
[ "$TR3" = "2" ] && item "2 trilhas CAPTURADO (chaves distintas por event_id)" 0 \
  || item "2 trilhas CAPTURADO (chaves distintas por event_id)" 1 "(obtido $TR3)"

echo "== 10. identificador FORTE (CNPJ) reusa a organizacao"
$COMPONENTE --coleta "$BASE/fx/forte.json" --confirmo >"$BASE/out/forte.out" 2>&1
item "captura do forte executada (exit 0)" $?
grep -q '\"identidade\": \"REUSO\"' "$BASE/out/forte.out" && item "veredito REUSO pelo identificador forte" 0 \
  || item "veredito REUSO pelo identificador forte" 1
N_ORG2=$(psql_q "select count(*) from sales_intelligence.organizations;")
[ "$N_ORG2" = "2" ] && item "nenhuma organizacao nova pelo forte (2 no total)" 0 \
  || item "nenhuma organizacao nova pelo forte (2 no total)" 1 "(obtido $N_ORG2)"
LIG=$(psql_q "select count(*) from sales_intelligence.interactions i join sales_intelligence.organizations o on o.id=i.organization_id where o.cnpj='12345678000190' and i.content_reference='evt-0002';")
[ "$LIG" = "1" ] && item "interacao do forte ligada a organizacao existente" 0 \
  || item "interacao do forte ligada a organizacao existente" 1 "(obtido $LIG)"

echo "== 11. identificador FRACO nao faz merge (fila humana)"
ANTES_FRACO=$(psql_q "select count(*) from sales_intelligence.interactions;")
$COMPONENTE --coleta "$BASE/fx/fraca.json" --confirmo >"$BASE/out/fraca.out" 2>&1
item "captura fraca executada (exit 0)" $?
grep -q REVIEW_REQUIRED "$BASE/out/fraca.out" && item "veredito REVIEW_REQUIRED" 0 || item "veredito REVIEW_REQUIRED" 1
DEPOIS_FRACO=$(psql_q "select count(*) from sales_intelligence.interactions;")
[ "$ANTES_FRACO" = "$DEPOIS_FRACO" ] && item "fraco nao criou interacao (nada cadastrado)" 0 \
  || item "fraco nao criou interacao (nada cadastrado)" 1 "($ANTES_FRACO -> $DEPOIS_FRACO)"
TR_FRACO=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='evento:evento-pmi-sp-2026-10:evt-0003' and status='REVIEW_REQUIRED';")
[ "$TR_FRACO" = "1" ] && item "trilha REVIEW_REQUIRED registrada" 0 || item "trilha REVIEW_REQUIRED registrada" 1

echo "== 12. consentimento e' barreira (medido no banco)"
ANTES_CONS=$(psql_q "select count(*) from sales_intelligence.organizations;")
$COMPONENTE --coleta "$BASE/fx/sem_consentimento.json" --confirmo >"$BASE/out/semcons.out" 2>&1
item "coleta sem opt-in processada (exit 0)" $?
grep -q RECUSADO_CONSENTIMENTO "$BASE/out/semcons.out" && item "veredito RECUSADO_CONSENTIMENTO" 0 \
  || item "veredito RECUSADO_CONSENTIMENTO" 1
DEPOIS_CONS=$(psql_q "select count(*) from sales_intelligence.organizations;")
[ "$ANTES_CONS" = "$DEPOIS_CONS" ] && item "sem opt-in: nenhum cadastro novo" 0 \
  || item "sem opt-in: nenhum cadastro novo" 1 "($ANTES_CONS -> $DEPOIS_CONS)"
TR_CONS=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='evento:evento-pmi-sp-2026-10:evt-0004' and status='RECUSADO_CONSENTIMENTO';")
[ "$TR_CONS" = "1" ] && item "trilha RECUSADO_CONSENTIMENTO registrada" 0 || item "trilha RECUSADO_CONSENTIMENTO registrada" 1

echo "== 13. dentes de ponta do canal (medidos no BANCO)"
ANTES_D=$(psql_q "select count(*) from sales_intelligence.contacts;")
$COMPONENTE --coleta "$BASE/fx/base_invalida.json" --confirmo >"$BASE/out/baseinvalida.out" 2>&1
grep -q RECUSADO_CONSENTIMENTO "$BASE/out/baseinvalida.out" && item "base legal fora do vocabulario RECUSA" 0 \
  || item "base legal fora do vocabulario RECUSA" 1
DEPOIS_D=$(psql_q "select count(*) from sales_intelligence.contacts;")
[ "$ANTES_D" = "$DEPOIS_D" ] && item "nenhum contato novo com base legal inventada" 0 \
  || item "nenhum contato novo com base legal inventada" 1 "($ANTES_D -> $DEPOIS_D)"
$COMPONENTE --coleta "$BASE/fx/sem_evento.json" --confirmo >"$BASE/out/semevento.out" 2>&1
grep -q EVENTO_NAO_DECLARADO "$BASE/out/semevento.out" && item "sem event_id RECUSA (EVENTO_NAO_DECLARADO)" 0 \
  || item "sem event_id RECUSA (EVENTO_NAO_DECLARADO)" 1
TR_EVT=$(psql_q "select count(*) from sales_intelligence.sync_events where status='EVENTO_NAO_DECLARADO';")
[ "$TR_EVT" = "1" ] && item "trilha EVENTO_NAO_DECLARADO registrada" 0 || item "trilha EVENTO_NAO_DECLARADO registrada" 1 "(obtido $TR_EVT)"
IT_EVT=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='evt-0006';")
[ "$IT_EVT" = "0" ] && item "coleta sem evento nao virou interacao" 0 || item "coleta sem evento nao virou interacao" 1 "(obtido $IT_EVT)"
$COMPONENTE --coleta "$BASE/fx/metodo_inventado.json" --confirmo >"$BASE/out/metodo.out" 2>&1
grep -q EVENTO_NAO_DECLARADO "$BASE/out/metodo.out" && item "capture_method inventado RECUSA" 0 \
  || item "capture_method inventado RECUSA" 1
$COMPONENTE --coleta "$BASE/fx/sem_capturado_em.json" --confirmo >"$BASE/out/seminstante.out" 2>&1
grep -q EVENTO_NAO_DECLARADO "$BASE/out/seminstante.out" && item "sem capturado_em RECUSA" 0 \
  || item "sem capturado_em RECUSA" 1
TR_EVT2=$(psql_q "select count(*) from sales_intelligence.sync_events where status='EVENTO_NAO_DECLARADO';")
[ "$TR_EVT2" = "3" ] && item "3 trilhas EVENTO_NAO_DECLARADO (sem evento, metodo inventado, sem instante)" 0 \
  || item "3 trilhas EVENTO_NAO_DECLARADO (sem evento, metodo inventado, sem instante)" 1 "(obtido $TR_EVT2)"

echo "== 14. snapshot depois: so' as 4 tabelas do componente mudam"
DEPOIS="$BASE/out/depois.txt"; : > "$DEPOIS"
for t in organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >> "$DEPOIS"
done
MUDOU=$(diff "$ANTES" "$DEPOIS" | grep '^>' | sed 's/^> //' | cut -d= -f1 | tr '\n' ' ')
case "$(echo $MUDOU)" in
  "organizations contacts interactions sync_events") item "somente organizations/contacts/interactions/sync_events mudaram ($MUDOU)" 0 ;;
  *) item "somente organizations/contacts/interactions/sync_events mudaram" 1 "(mudou: '$MUDOU')" ;;
esac

echo "== 15. privacidade: PII do lead ausente da evidencia"
if grep -rq "marina@bandeirante.test" "$BASE/out" 2>/dev/null; then
  item "e-mail do lead ausente da evidencia do aceite" 1 "(apareceu cru)"
else
  item "e-mail do lead ausente da evidencia do aceite" 0
fi
if grep -rq "97777-6655" "$BASE/out" 2>/dev/null; then
  item "telefone do lead ausente da evidencia do aceite" 1 "(apareceu cru)"
else
  item "telefone do lead ausente da evidencia do aceite" 0
fi
if grep -rq "123.456.780-190\|12345678000190" "$BASE/out/nova.out" "$BASE/out/forte.out" 2>/dev/null; then
  item "CNPJ do lead ausente da evidencia do componente" 1 "(apareceu cru)"
else
  item "CNPJ do lead ausente da evidencia do componente" 0
fi

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "ACEITE_CAPTURA_EVENTO_001_OK ($OK itens, 0 falhas)"
  exit 0
fi
echo "FALHOU ($OK itens, $FALHAS falhas)"
exit 1
