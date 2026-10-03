#!/usr/bin/env bash
# Aceite de ponta do componente `whatsapp_lead` (card TRE-W7-E05-T01 — WhatsApp engaged-lead workflow).
#
# Mede a cadeia INTEIRA contra uma base REAL e DESCARTavel, sem tocar nada que ja' exista (ADR-005):
# container `pg-whatsapp-acc` (127.0.0.1, porta do proprio docker) + migration 0001 + o componente
# `hermes/agentes/inbound/whatsapp_lead.py` gravando em `sales_intelligence`.
#
# Cenario (um contato por jornada, numeros distintos para nao cruzar as jornadas):
#   c1 Marina    (+55 11 98888-7777)  lead engajado -> mensagem com interesse / replay
#   c2 Fulano    (+55 11 96666-2222)  do_not_contact = true -> bloqueio
#   c3 Joana     (+55 11 97777-0000)  pedido de descadastro (PARAR)
#   c4 Caio      (+55 11 95555-0000)  ultima entrada 3 dias atras -> janela fechada
#   c5/c6        (+55 11 94444-1111 / 5511944441111) mesmo nucleo -> ambiguidade
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: --planejar/--conferir sem banco; prod RECUSA (exit 4); prefixo de banco remoto em dev
#      RECUSA (BANCO_NAO_E_DEV, exit 3);
#   2. sem --confirmo a rodada e' DRY_RUN: snapshot das 12 tabelas antes/depois igual;
#   3. mensagem de lead engajado: 1 interaction (WHATSAPP/INBOUND/WHATSAPP_MENSAGEM) ligada ao contato e
#      a organizacao resolvidos por telefone (formas nacional e com o 55) + trilha RECEBIDO;
#   4. idempotencia: reentrega da MESMA mensagem -> JA_RECEBIDO, zero linha nova;
#   5. telefone fora da base -> SEM_VINCULO: nenhuma organizacao/contato inventado, so' trilha;
#   6. telefone ambiguo (dois contatos com o mesmo nucleo) -> REVIEW_REQUIRED, zero interacao;
#   7. bloqueio: contato com do_not_contact -> BLOQUEADO_POR_BLOQUEIO, nenhum proximo passo, e a linha
#      de contacts segue INTOCADA (dono operacional e' o Odoo);
#   8. janela de atendimento: primeira entrada -> RESPOSTA_LIVRE_SUGERIDA; ultima entrada 3 dias atras
#      -> REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA (proposta, nunca envio; outbox_events vazia);
#   9. escopo de escrita: so' interactions e sync_events mudam (nenhum DDL/UPDATE/DELETE);
#  10. privacidade: telefone do lead nao aparece cru na evidencia do aceite;
#  11. dente de ponta medido no BANCO: descadastro por WhatsApp vira response_category=OPT_OUT na
#      interacao e trilha BLOQUEADO_POR_BLOQUEIO (nao so' no codigo).
#
# Pre-requisitos: docker com imagem postgres:16, python3. Nada de rede externa (pontas em 127.0.0.1).
# Uso (na VPS, no repo): bash scripts/agentes/teste_whatsapp_lead_aceite.sh [--manter]
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-w7e05t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-whatsapp-acc
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
COMPONENTE="python3 hermes/agentes/inbound/whatsapp_lead.py"

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
[ -f hermes/agentes/inbound/whatsapp_lead.py ] && [ -f hermes/agentes/inbound/whatsapp-lead-v1.json ] \
  && item "componente e contrato presentes no repo" 0 || item "componente e contrato presentes no repo" 1

echo "== 1. suite offline do componente (com prova de dente)"
python3 scripts/agentes/verificar_whatsapp_lead.py --autoteste >"$BASE/out/verificador.out" 2>&1
grep -q "VERIFICADOR_WHATSAPP_LEAD_PASS" "$BASE/out/verificador.out" \
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

echo "== 3. base de referencia (4 organizacoes e os contatos de cada jornada)"
psql_q "INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain, status, source)
  VALUES ('a0000000-0000-4000-8000-000000000001','Metalurgica Bandeirante LTDA','Metalurgica Bandeirante','bandeirante.test','DISCOVERED','OUTBOUND'),
         ('a0000000-0000-4000-8000-000000000002','Distribuidora Aurora LTDA','Distribuidora Aurora','aurora.test','DISCOVERED','OUTBOUND'),
         ('a0000000-0000-4000-8000-000000000003','Servicos Vale Verde LTDA','Servicos Vale Verde','valeverde.test','DISCOVERED','OUTBOUND'),
         ('a0000000-0000-4000-8000-000000000004','Industria Serra Azul LTDA','Industria Serra Azul','serraazul.test','DISCOVERED','OUTBOUND');
  INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, whatsapp, legal_basis, source, do_not_contact)
  VALUES ('c0000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001','Marina Prado','marina@bandeirante.test','+55 11 98888-7777','+55 11 98888-7777','CONSENTIMENTO','OUTBOUND',false),
         ('c0000000-0000-4000-8000-000000000002','a0000000-0000-4000-8000-000000000002','Fulano Bloqueado','fulano@aurora.test','+55 11 96666-2222',NULL,'CONSENTIMENTO','OUTBOUND',true),
         ('c0000000-0000-4000-8000-000000000003','a0000000-0000-4000-8000-000000000003','Joana Descadastro','joana@valeverde.test','+55 11 97777-0000','+55 11 97777-0000','CONSENTIMENTO','OUTBOUND',false),
         ('c0000000-0000-4000-8000-000000000004','a0000000-0000-4000-8000-000000000004','Caio Janela','caio@serraazul.test','+55 11 95555-0000','+55 11 95555-0000','CONSENTIMENTO','OUTBOUND',false);" >/dev/null 2>&1
item "base de referencia inserida (4 organizacoes, 4 contatos)" $?
N_CONTATOS=$(psql_q "select count(*) from sales_intelligence.contacts;")
[ "$N_CONTATOS" = "4" ] && item "4 contatos na base" 0 || item "4 contatos na base" 1 "(obtido $N_CONTATOS)"
N_BLOQ=$(psql_q "select count(*) from sales_intelligence.contacts where do_not_contact = true;")
[ "$N_BLOQ" = "1" ] && item "1 contato marcado do_not_contact" 0 || item "1 contato marcado do_not_contact" 1

echo "== 4. fixtures dos eventos inbound"
python3 - "$BASE" <<'PY'
import json, sys, os
base = sys.argv[1]
fx = os.path.join(base, "fx"); os.makedirs(fx, exist_ok=True)

def evento(mid, telefone, texto="podemos conversar amanha?", tipo="TEXTO"):
    return {
        "message_id": mid,
        "recebido_em": "2026-10-03T18:12:00Z",
        "remetente": {"telefone": telefone, "nome_perfil": "Marina"},
        "mensagem": {"tipo": tipo, "texto": texto},
        "canal": {"origem": "provedor_whatsapp", "numero_destino": "+55 11 3333-1000"},
    }

casos = {
    "engajado.json": evento("wamid-0001", "+55 11 98888-7777"),
    "replay.json": evento("wamid-0001", "+55 11 98888-7777"),
    "sem_vinculo.json": evento("wamid-0002", "+55 11 93333-9999"),
    "ambiguo.json": evento("wamid-0003", "+55 11 94444-1111"),
    "bloqueado.json": evento("wamid-0004", "11 96666-2222"),
    "descadastro.json": evento("wamid-0005", "+55 11 97777-0000", texto="PARAR"),
    "janela_fechada.json": evento("wamid-0006", "+55 11 95555-0000", texto="podemos agendar uma conversa?"),
    "tipo_invalido.json": evento("wamid-0007", "+55 11 95555-0000", texto="", tipo="ENQUETE"),
    "sem_telefone.json": evento("wamid-0008", "988887777"),
}
for nome, corpo in casos.items():
    with open(os.path.join(fx, nome), "w", encoding="utf-8") as fh:
        json.dump(corpo, fh, ensure_ascii=False, indent=2)
print(f"{len(casos)} eventos gerados")
PY
item "9 eventos gerados" $?

echo "== 5. jornada 'ambiguo': dois contatos com o mesmo nucleo nacional (94444-1111)"
psql_q "INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, whatsapp, legal_basis, source, do_not_contact)
  VALUES ('c0000000-0000-4000-8000-000000000005','a0000000-0000-4000-8000-000000000002','Sergio Um','sergio1@aurora.test','+55 11 94444-1111',NULL,'CONSENTIMENTO','OUTBOUND',false),
         ('c0000000-0000-4000-8000-000000000006','a0000000-0000-4000-8000-000000000003','Sergio Dois','sergio2@valeverde.test','5511944441111',NULL,'CONSENTIMENTO','OUTBOUND',false);" >/dev/null 2>&1
item "par ambiguo inserido (mesmo nucleo em formas diferentes)" $?
AMBIGUOS=$(psql_q "select count(*) from sales_intelligence.contacts where right(regexp_replace(coalesce(phone,''), '[^0-9]', '', 'g'), 11) = '11944441111';")
[ "$AMBIGUOS" = "2" ] && item "nucleo 11944441111 presente em 2 contatos" 0 \
  || item "nucleo 11944441111 presente em 2 contatos" 1 "(obtido $AMBIGUOS)"

echo "== 6. jornada 'janela fechada': ultima entrada do contato c4 ha 3 dias"
psql_q "INSERT INTO sales_intelligence.interactions (id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, content_reference)
  VALUES ('d0000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000004','c0000000-0000-4000-8000-000000000004','WHATSAPP','INBOUND','WHATSAPP_MENSAGEM', NOW() - interval '3 days','wamid-0000');" >/dev/null 2>&1
item "entrada antiga do contato da janela inserida" $?
N_ANTIGA=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0000';")
[ "$N_ANTIGA" = "1" ] && item "1 interacao de referencia (3 dias atras)" 0 || item "1 interacao de referencia" 1

export TRE_AMBIENTE=dev TRE_WHATSAPP_PORTA_BANCO="$PORTA_BANCO"

echo "== 7. guardas antes de escrever"
$COMPONENTE --planejar >"$BASE/out/planejar.out" 2>&1; item "--planejar sem banco (exit 0)" $?
$COMPONENTE --conferir >"$BASE/out/conferir.out" 2>&1; item "--conferir (exit 0)" $?
$COMPONENTE --evento "$BASE/fx/engajado.json" --ambiente prod >"$BASE/out/prod.out" 2>&1
[ $? -eq 4 ] && item "prod RECUSA o evento (exit 4)" 0 || item "prod RECUSA o evento (exit 4)" 1
TRE_WHATSAPP_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence" \
  $COMPONENTE --evento "$BASE/fx/engajado.json" --confirmo >"$BASE/out/remoto.out" 2>&1
[ $? -eq 3 ] && grep -q BANCO_NAO_E_DEV "$BASE/out/remoto.out" \
  && item "dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV, exit 3)" 0 \
  || item "dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV, exit 3)" 1

echo "== 8. snapshot das 12 tabelas ANTES"
ANTES="$BASE/out/antes.txt"; : > "$ANTES"
for t in organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >> "$ANTES"
done

echo "== 9. dry-run sem --confirmo nao escreve"
$COMPONENTE --evento "$BASE/fx/engajado.json" >"$BASE/out/dry.out" 2>&1
item "dry-run concluido (exit 0)" $?
grep -q DRY_RUN "$BASE/out/dry.out" && item "dry-run declarado na saida (nada gravado)" 0 \
  || item "dry-run declarado na saida (nada gravado)" 1
N_INT=$(psql_q "select count(*) from sales_intelligence.interactions;")
[ "$N_INT" = "1" ] && item "dry-run: nenhuma interacao nova (1 = a de referencia)" 0 \
  || item "dry-run: nenhuma interacao nova" 1 "(obtido $N_INT)"

echo "== 10. contato com do_not_contact (telefone na forma nacional, sem o 55)"
$COMPONENTE --evento "$BASE/fx/bloqueado.json" --confirmo --relatorio "$BASE/out/bloqueado-rel.json" \
  --trilha "$BASE/out/bloqueado-trilha.jsonl" >"$BASE/out/bloqueado.out" 2>&1
item "mensagem processada (exit 0)" $?
grep -q '"status": "BLOQUEADO_POR_BLOQUEIO"' "$BASE/out/bloqueado.out" \
  && item "veredito BLOQUEADO_POR_BLOQUEIO" 0 \
  || item "veredito BLOQUEADO_POR_BLOQUEIO" 1 "$(tail -2 "$BASE/out/bloqueado.out")"
grep -q '"proximo_passo": "NENHUM_FILA_HUMANA"' "$BASE/out/bloqueado.out" \
  && item "bloqueio nao gera proximo passo" 0 || item "bloqueio nao gera proximo passo" 1
IT_BLOQ=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0004' and channel='WHATSAPP' and direction='INBOUND' and interaction_type='WHATSAPP_MENSAGEM';")
[ "$IT_BLOQ" = "1" ] && item "interacao do bloqueio ainda registrada (o fato aconteceu)" 0 \
  || item "interacao do bloqueio ainda registrada" 1 "(obtido $IT_BLOQ)"
LIG_BLOQ=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0004' and contact_id='c0000000-0000-4000-8000-000000000002';")
[ "$LIG_BLOQ" = "1" ] && item "bloqueio ligado ao contato de do_not_contact" 0 \
  || item "bloqueio ligado ao contato de do_not_contact" 1
BLOQ=$(psql_q "select count(*) from sales_intelligence.contacts where id='c0000000-0000-4000-8000-000000000002' and do_not_contact = true;")
[ "$BLOQ" = "1" ] && item "contacts segue intocada (dono operacional e' o Odoo)" 0 \
  || item "contacts segue intocada" 1 "(obtido $BLOQ)"
TR_BLOQ=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0004' and status='BLOQUEADO_POR_BLOQUEIO';")
[ "$TR_BLOQ" = "1" ] && item "trilha BLOQUEADO_POR_BLOQUEIO registrada" 0 \
  || item "trilha BLOQUEADO_POR_BLOQUEIO registrada" 1 "(obtido $TR_BLOQ)"

echo "== 11. lead engajado: mensagem com interesse"
$COMPONENTE --evento "$BASE/fx/engajado.json" --confirmo >"$BASE/out/engajado.out" 2>&1
item "mensagem executada (exit 0)" $?
grep -q '"status": "RECEBIDO"' "$BASE/out/engajado.out" && item "veredito RECEBIDO" 0 \
  || item "veredito RECEBIDO" 1 "$(tail -2 "$BASE/out/engajado.out")"
grep -q '"categoria": "INTERESSE"' "$BASE/out/engajado.out" && item "categoria INTERESSE na saida" 0 \
  || item "categoria INTERESSE na saida" 1
IT=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0001' and channel='WHATSAPP' and direction='INBOUND' and interaction_type='WHATSAPP_MENSAGEM' and response_category='INTERESSE' and intent='PEDIDO_DE_CONVERSA' and sentiment='POSITIVO';")
[ "$IT" = "1" ] && item "1 interacao WHATSAPP/INBOUND/INTERESSE classificada" 0 \
  || item "1 interacao WHATSAPP/INBOUND/INTERESSE classificada" 1 "(obtido $IT)"
OCORRIDO=$(psql_q "select to_char(occurred_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from sales_intelligence.interactions where content_reference='wamid-0001';")
[ "$OCORRIDO" = "2026-10-03T18:12:00Z" ] && item "occurred_at vem do recebido_em do provedor" 0 \
  || item "occurred_at vem do recebido_em do provedor" 1 "(obtido $OCORRIDO)"
LIG=$(psql_q "select count(*) from sales_intelligence.interactions i join sales_intelligence.contacts c on c.id=i.contact_id join sales_intelligence.organizations o on o.id=i.organization_id where i.content_reference='wamid-0001' and c.id='c0000000-0000-4000-8000-000000000001' and o.id='a0000000-0000-4000-8000-000000000001';")
[ "$LIG" = "1" ] && item "interacao ligada ao contato e a organizacao resolvidos" 0 \
  || item "interacao ligada ao contato e a organizacao resolvidos" 1 "(obtido $LIG)"
TR=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0001' and status='RECEBIDO';")
[ "$TR" = "1" ] && item "trilha RECEBIDO (whatsapp:wamid-0001)" 0 || item "trilha RECEBIDO" 1 "(obtido $TR)"
PROX=$(psql_q "select request_payload->>'proximo_passo' from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0001';")
[ "$PROX" = "RESPOSTA_LIVRE_SUGERIDA" ] && item "primeira entrada: janela aberta e proposta de resposta livre" 0 \
  || item "primeira entrada: janela aberta e proposta de resposta livre" 1 "(obtido '$PROX')"

echo "== 12. idempotencia (reentrega da mesma mensagem)"
$COMPONENTE --evento "$BASE/fx/replay.json" --confirmo >"$BASE/out/replay.out" 2>&1
item "reentrega executada (exit 0)" $?
grep -q JA_RECEBIDO "$BASE/out/replay.out" && item "reentrega devolve JA_RECEBIDO" 0 \
  || item "reentrega devolve JA_RECEBIDO" 1
IT2=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0001';")
TR2=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0001';")
[ "$IT2" = "1" ] && [ "$TR2" = "1" ] && item "reentrega nao duplicou interacao nem trilha" 0 \
  || item "reentrega nao duplicou interacao nem trilha" 1 "(interacoes=$IT2 trilha=$TR2)"

echo "== 13. telefone fora da base: SEM_VINCULO (nada inventado)"
N_CONT_ANTES=$(psql_q "select count(*) from sales_intelligence.contacts;")
$COMPONENTE --evento "$BASE/fx/sem_vinculo.json" --confirmo >"$BASE/out/semvinculo.out" 2>&1
item "evento de telefone desconhecido processado (exit 0)" $?
grep -q SEM_VINCULO "$BASE/out/semvinculo.out" && item "veredito SEM_VINCULO" 0 || item "veredito SEM_VINCULO" 1
N_CONT_DEPOIS=$(psql_q "select count(*) from sales_intelligence.contacts;")
[ "$N_CONT_ANTES" = "$N_CONT_DEPOIS" ] && item "desconhecido nao criou contato (nada de cadastro pela metade)" 0 \
  || item "desconhecido nao criou contato" 1 "($N_CONT_ANTES -> $N_CONT_DEPOIS)"
IT_SV=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0002';")
[ "$IT_SV" = "0" ] && item "desconhecido nao gerou interacao" 0 || item "desconhecido nao gerou interacao" 1 "(obtido $IT_SV)"
TR_SV=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0002' and status='SEM_VINCULO';")
[ "$TR_SV" = "1" ] && item "trilha SEM_VINCULO registrada" 0 || item "trilha SEM_VINCULO registrada" 1

echo "== 14. telefone ambiguo: REVIEW_REQUIRED (fila humana)"
$COMPONENTE --evento "$BASE/fx/ambiguo.json" --confirmo >"$BASE/out/ambiguo.out" 2>&1
item "evento ambiguo processado (exit 0)" $?
grep -q REVIEW_REQUIRED "$BASE/out/ambiguo.out" && item "veredito REVIEW_REQUIRED" 0 \
  || item "veredito REVIEW_REQUIRED" 1 "$(tail -2 "$BASE/out/ambiguo.out")"
IT_AMB=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0003';")
[ "$IT_AMB" = "0" ] && item "ambiguidade nao gravou interacao (nenhum merge heuristico)" 0 \
  || item "ambiguidade nao gravou interacao" 1 "(obtido $IT_AMB)"
TR_AMB=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0003' and status='REVIEW_REQUIRED';")
[ "$TR_AMB" = "1" ] && item "trilha REVIEW_REQUIRED registrada" 0 || item "trilha REVIEW_REQUIRED registrada" 1

echo "== 15. descadastro por WhatsApp (dente de ponta, medido no banco)"
$COMPONENTE --evento "$BASE/fx/descadastro.json" --confirmo >"$BASE/out/descadastro.out" 2>&1
item "mensagem PARAR processada (exit 0)" $?
grep -q BLOQUEADO_POR_BLOQUEIO "$BASE/out/descadastro.out" && item "veredito BLOQUEADO_POR_BLOQUEIO" 0 \
  || item "veredito BLOQUEADO_POR_BLOQUEIO" 1
OPT=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0005' and response_category='OPT_OUT' and intent='DESCADASTRO';")
[ "$OPT" = "1" ] && item "descadastro vira response_category=OPT_OUT na interacao" 0 \
  || item "descadastro vira response_category=OPT_OUT" 1 "(obtido $OPT)"
PROX_OPT=$(psql_q "select request_payload->>'proximo_passo' from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0005';")
[ "$PROX_OPT" = "NENHUM_FILA_HUMANA" ] && item "descadastro nao gera proximo passo (nada de reengajamento)" 0 \
  || item "descadastro nao gera proximo passo" 1 "(obtido '$PROX_OPT')"
TR_OPT=$(psql_q "select count(*) from sales_intelligence.sync_events where idempotency_key='whatsapp:wamid-0005' and status='BLOQUEADO_POR_BLOQUEIO';")
[ "$TR_OPT" = "1" ] && item "trilha do descadastro bloqueada" 0 || item "trilha do descadastro bloqueada" 1

echo "== 16. janela de atendimento fechada (ultima entrada 3 dias atras)"
$COMPONENTE --evento "$BASE/fx/janela_fechada.json" --confirmo >"$BASE/out/janela.out" 2>&1
item "mensagem do contato antigo processada (exit 0)" $?
grep -q '"aberta": false' "$BASE/out/janela.out" && item "janela declarada fechada na saida" 0 \
  || item "janela declarada fechada na saida" 1 "$(tail -2 "$BASE/out/janela.out")"
grep -q REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA "$BASE/out/janela.out" \
  && item "fora da janela: proposta exige template + aprovacao humana" 0 \
  || item "fora da janela: proposta exige template + aprovacao humana" 1
OUTBOX=$(psql_q "select count(*) from sales_intelligence.outbox_events;")
[ "$OUTBOX" = "0" ] && item "nenhum evento de envio criado (outbox_events vazia)" 0 \
  || item "nenhum evento de envio criado (outbox_events vazia)" 1 "(obtido $OUTBOX)"

echo "== 17. eventos invalidos (tipo fora do vocabulario e telefone sem DDD)"
$COMPONENTE --evento "$BASE/fx/tipo_invalido.json" --confirmo >"$BASE/out/tipoinvalido.out" 2>&1
grep -q EVENTO_INVALIDO "$BASE/out/tipoinvalido.out" && item "tipo fora do vocabulario RECUSA (EVENTO_INVALIDO)" 0 \
  || item "tipo fora do vocabulario RECUSA (EVENTO_INVALIDO)" 1
IT_INV=$(psql_q "select count(*) from sales_intelligence.interactions where content_reference='wamid-0007';")
[ "$IT_INV" = "0" ] && item "evento invalido nao gerou interacao" 0 || item "evento invalido nao gerou interacao" 1
$COMPONENTE --evento "$BASE/fx/sem_telefone.json" --confirmo >"$BASE/out/semtelefone.out" 2>&1
grep -q SEM_DADOS_MINIMOS "$BASE/out/semtelefone.out" && item "telefone sem DDD RECUSA (SEM_DADOS_MINIMOS)" 0 \
  || item "telefone sem DDD RECUSA (SEM_DADOS_MINIMOS)" 1

echo "== 18. snapshot depois: so' interactions e sync_events mudam"
DEPOIS="$BASE/out/depois.txt"; : > "$DEPOIS"
for t in organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >> "$DEPOIS"
done
MUDOU=$(diff "$ANTES" "$DEPOIS" | grep '^>' | sed 's/^> //' | cut -d= -f1 | tr '\n' ' ')
case "$(echo $MUDOU)" in
  "interactions sync_events") item "somente interactions/sync_events mudaram ($MUDOU)" 0 ;;
  *) item "somente interactions/sync_events mudaram" 1 "(mudou: '$MUDOU')" ;;
esac

echo "== 19. privacidade: PII do lead ausente da evidencia"
if grep -rq "98888-7777\|988887777" "$BASE/out" 2>/dev/null; then
  item "telefone do lead ausente da evidencia do aceite" 1 "(apareceu cru)"
else
  item "telefone do lead ausente da evidencia do aceite" 0
fi
if grep -rq "96666-2222\|966662222" "$BASE/out" 2>/dev/null; then
  item "telefone do contato bloqueado ausente da evidencia" 1 "(apareceu cru)"
else
  item "telefone do contato bloqueado ausente da evidencia" 0
fi
if grep -rq "3333-1000\|1133331000" "$BASE/out" 2>/dev/null; then
  item "numero de destino ausente da evidencia" 1 "(apareceu cru)"
else
  item "numero de destino ausente da evidencia" 0
fi

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "ACEITE_WHATSAPP_LEAD_001_OK ($OK itens, 0 falhas)"
  exit 0
fi
echo "FALHOU ($OK itens, $FALHAS falhas)"
exit 1
