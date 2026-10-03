#!/usr/bin/env bash
# =============================================================================================
# ACEITE E2E OUTBOUND #002 — card TRE-W6-E07-T01
#
# Cenario do doc 08 §4 (baseline V1.1.0), os 10 passos num UNICO trio descartavel:
#   1. lead A+ elegivel                  -> massa + registro TIER (W5)
#   2. Hermes gera Next Best Action      -> hermes/scores/nba/nba.py (W5-E07)
#   3. GPT cria draft                    -> hermes/agents/outreach/outreach_generator.py (W6-E02)
#   4. Human Approval                    -> hermes/agents/outreach/approval_workflow.py (W6-E03)
#   5. Titan envia                       -> hermes/agents/outreach/send_workflow.py (W6-E04) + SINK SMTP
#   6. interaction registrada            -> interactions + sync_events (W6-E04)
#   7. reply recebido                    -> hermes/agentes/respostas/ingestao_respostas.py (W6-E05) + SINK IMAP
#   8. GPT classifica                    -> response_category/intent/sentiment medidos no banco
#   9. Odoo atualizado                   -> hermes/agentes/respostas/atualizacao_odoo.py (W6-E06) + STUB da API
#  10. nova NBA criada                   -> NBA de novo: evidencia nova SUPERSEDE a anterior
#
# ADR-005: nada nasce em producao. Toda ponta externa e' descartavel e local (127.0.0.1): sink SMTP,
# sink IMAP, stub da API controlada do Odoo e PostgreSQL proprio (`pg-resp-e2e002`). Nenhuma credencial
# Titan, nenhum destino real, nenhuma chave de API de verdade. `prod` e' medido nos 4 componentes e
# RECUSA (exit 4). Os containers que ja existem (pg-sales-dev, pg-odoo-dev, odoo-dev, proxy-dev) NAO
# sao tocados: se o container do aceite ja existir, o aceite ABORTA em vez de mexer no que nao e' dele.
# O nome `pg-resp-e2e002` NAO e' capricho: as guardas de dev de W6-E05/W6-E06 so aceitam porta de banco
# em container `pg-(sales|odoo|resp|respostas|e06|aceite)...` — `pg-resp-...` e' a forma que casa nas
# duas (regra declarada nos modulos, nao afrouxada por este aceite).
#
# Veredito: ACEITE_E2E_OUTBOUND_002_OK / ACEITE_E2E_OUTBOUND_002_FALHOU.
# --prova-de-dente: muta uma COPIA de um componente e exige que o aceite reprove O ITEM ESPERADO.
# Exit: 0 = OK · 1 = FALHOU · 2 = uso/guarda · 3 = nao testavel · 4 = recusa de ambiente.
# =============================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_E2E002_CONTAINER:-pg-resp-e2e002}"
TRABALHO="${TRE_E2E002_TRABALHO:-/tmp/e2e-outbound-002}"
USUARIO=sales_ai
BANCO=sales_intelligence
SENHA=dev
DOMINIO_DEV="${TRE_E2E002_DOMINIO_DEV:-cliente-demo.test}"
OPERADOR="Anderson Ribeiro"
ORG_A="11111111-1111-4111-8111-111111111111"
CT_A="d1111111-1111-4111-8111-111111111111"
LEAD_ODOO=9101
PORTA_SMTP=2465
PORTA_IMAP=2993
PORTA_API=8799
MODO=implicit_tls
ENVIO=$(printf '%s' "${TRE_E2E002_MODULO_ENVIO:-$RAIZ/hermes/agents/outreach/send_workflow.py}")
INGESTAO="${TRE_E2E002_MODULO_INGESTAO:-$RAIZ/hermes/agentes/respostas/ingestao_respostas.py}"
NBA="${TRE_E2E002_MODULO_NBA:-$RAIZ/hermes/scores/nba/nba.py}"
GERADOR="$RAIZ/hermes/agents/outreach/outreach_generator.py"
APROVACAO="$RAIZ/hermes/agents/outreach/approval_workflow.py"
ODOO="$RAIZ/hermes/agentes/respostas/atualizacao_odoo.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
SINK_SMTP="$RAIZ/scripts/integracoes/sink-smtp-dev.py"
SINK_IMAP="$RAIZ/scripts/integracoes/sink-imap-dev.py"
STUB="$RAIZ/scripts/agentes/stub-odoo-api-dev.py"
DENTE=0
MANTER=0
DENTE_ROTULO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --manter) MANTER=1 ;;
    --prova-de-dente) DENTE=1 ;;
    --dente) shift; DENTE=1; DENTE_ROTULO="${1:?--dente exige rotulo}" ;;
    --dente=*) DENTE=1; DENTE_ROTULO="${1#--dente=}" ;;
    --sub-run) DENTE=0 ;;
    --envio) shift; ENVIO="${1:?}" ;;
    --ingestao) shift; INGESTAO="${1:?}" ;;
    --nba) shift; NBA="${1:?}" ;;
    *) echo "uso: $0 [--manter] [--prova-de-dente] [--dente <rotulo>] [--envio <py>] [--ingestao <py>] [--nba <py>]"; exit 2 ;;
  esac
  shift
done

ITENS_OK=0
ITENS_FALHOU=0
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then echo "OK     $1 ($3)"; ITENS_OK=$((ITENS_OK + 1))
  else echo "FALHOU $1 (esperado=$2 obtido=$3)"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); fi
}
item_sim() { # <nome> <sim|nao>
  item "$1" sim "$2"
}

for c in docker python3 openssl; do
  command -v "$c" >/dev/null 2>&1 || { echo "NAO_TESTAVEL $c ausente (rode na VPS do ambiente)"; exit 3; }
done
for f in "$MIGRATION" "$ENVIO" "$INGESTAO" "$NBA" "$GERADOR" "$APROVACAO" "$ODOO" \
         "$SINK_SMTP" "$SINK_IMAP" "$STUB"; do
  [ -f "$f" ] || { echo "FALHOU arquivo ausente: $f"; exit 2; }
done
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
# Porta ocupada = sobra de rodada anterior (processo que nao morreu): o aceite ABORTA em vez de medir
# contra o sink/stub de outra rodada — foi o defeito medido na rodada 3 deste aceite (stub velho com a
# chave velha respondendo no lugar do novo).
for p in "$PORTA_SMTP" "$PORTA_IMAP" "$PORTA_API"; do
  if ! python3 -c "import socket,sys;s=socket.socket();s.settimeout(0.5);sys.exit(0 if s.connect_ex(('127.0.0.1',$p))!=0 else 1)"; then
    echo "FALHOU a porta local $p JA esta ocupada — sobra de rodada anterior; mate o processo e repita"; exit 2
  fi
done

echo "== 0. pre-flight e trio descartavel"
ARTEFATOS=$(( $(for f in "$MIGRATION" "$ENVIO" "$INGESTAO" "$NBA" "$GERADOR" "$APROVACAO" "$ODOO" "$SINK_SMTP" "$SINK_IMAP" "$STUB"; do [ -f "$f" ] && echo x; done | grep -c x) ))
item "0.0 os 10 artefatos do encadeamento existem no checkout" "10" "$ARTEFATOS"
rm -rf "$TRABALHO"; mkdir -p "$TRABALHO/ca"
CA_PEM="$TRABALHO/ca/dev.pem"; CA_KEY="$TRABALHO/ca/dev.key"
SENHA_SINK="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
SENHA_API="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
# correlation_id e' coluna UUID no banco canonico: id de mentira (string) derruba a auditoria de
# QUALQUER componente — foi o defeito medido na rodada 1 deste aceite.
CORR_NBA1="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_GER1="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_FILA1="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_FILA2="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_DEC1="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_CONS1="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_DRY="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_ENVIO="$(python3 -c 'import uuid;print(uuid.uuid4())')"
CORR_NBA2="$(python3 -c 'import uuid;print(uuid.uuid4())')"
: >"$TRABALHO/caixa-smtp.jsonl"; : >"$TRABALHO/stub.jsonl"

limpar() {
  local rc=$?
  for p in "$TRABALHO"/*.pid; do [ -f "$p" ] && kill "$(cat "$p")" >/dev/null 2>&1; done
  if [ "$MANTER" -eq 0 ]; then docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  else echo "== --manter: $CONTAINER e $TRABALHO ficaram de pe"; fi
  exit $rc
}
trap limpar EXIT

cat >"$TRABALHO/ca/openssl.cnf" <<CONF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = 127.0.0.1
[v3]
subjectAltName = @alt
[alt]
IP.1 = 127.0.0.1
DNS.1 = localhost
CONF
if ! openssl req -x509 -newkey rsa:2048 -nodes -keyout "$CA_KEY" -out "$CA_PEM" -days 2 \
     -config "$TRABALHO/ca/openssl.cnf" -extensions v3 >"$TRABALHO/openssl.log" 2>&1; then
  echo "NAO_TESTAVEL: nao consegui gerar o certificado proprio"; sed -n '1,5p' "$TRABALHO/openssl.log"; exit 3
fi
item "0.1 certificado proprio do dev com SAN de IP" sim \
  "$(openssl x509 -in "$CA_PEM" -noout -text 2>/dev/null | grep -q 'IP Address:127.0.0.1' && echo sim || echo nao)"

docker run -d --name "$CONTAINER" -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" \
  -e "POSTGRES_DB=$BANCO" "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
PRONTO=nao
for _ in $(seq 1 60); do
  # o cluster oficial sobe um servidor TEMPORARIO no boot: 'ready' uma vez nao basta (corrida medida)
  n=$(docker logs "$CONTAINER" 2>&1 | grep -c "database system is ready to accept connections")
  if [ "${n:-0}" -ge 2 ] && docker exec "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -tAc "select 1" >/dev/null 2>&1; then
    PRONTO=sim; break
  fi
  sleep 1
done
item "0.2 PostgreSQL descartavel de pe (init concluido: 2x ready)" sim "$PRONTO"

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
conta() { psql_t -c "$1" | tr -d ' ' | head -1; }
TABELAS_12="organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals"
foto() { local s=""; for t in $TABELAS_12; do s="$s$(conta "SELECT count(*) FROM sales_intelligence.$t;")/"; done; echo "$s"; }

psql_stdin < "$MIGRATION" >"$TRABALHO/migration.out" 2>&1
item "0.3 migration 0001 aplicada" "12" "$(conta "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence';")"
FOTO_ANTES_MASSA="$(foto)"
TABELAS_ANTES="$(conta "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence';")"

# ---------------------------------------------------------------------------------------------
echo "== 1. lead A+ elegivel (massa + registro TIER do W5)"
psql_stdin >/dev/null 2>&1 <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain, odoo_partner_id,
        industry_name, employee_band, employee_count, city, state, status, created_at)
VALUES ('$ORG_A', 'Distribuidora Alfa LTDA', 'Distribuidora Alfa', '$DOMINIO_DEV', 34,
        'Distribuicao B2B', '150_299', 210, 'Campinas', 'SP', 'Pesquisado', NOW());
INSERT INTO sales_intelligence.contacts (id, organization_id, odoo_partner_id, full_name, first_name,
        job_title, decision_role, email, preferred_channel, do_not_contact, opt_out_email, created_at)
VALUES ('$CT_A', '$ORG_A', 12, 'Joao Souza', 'Joao', 'Diretor de Operacoes', 'Economic Buyer',
        'joao@$DOMINIO_DEV', 'EMAIL', false, false, NOW());
INSERT INTO sales_intelligence.research_runs (id, organization_id, agent_name, agent_version, status,
        summary, completed_at, created_at)
VALUES (gen_random_uuid(), '$ORG_A', 'research', '1.0.0', 'COMPLETED',
        'Distribuidora com tres centros de distribuicao e pedidos redigitados no ERP', NOW(), NOW());
INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id, pain_category, pain_statement,
        status, created_at)
VALUES (gen_random_uuid(), '$ORG_A', 'PROCESSO_MANUAL',
        'Pedidos chegam por e-mail e sao redigitados no ERP', 'PARTIALLY_VALIDATED', NOW());
INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, title, description,
        detected_at, relevance_score)
VALUES (gen_random_uuid(), '$ORG_A', 'EFFICIENCY_PROGRAM', 'Programa de eficiencia anunciado',
        'Empresa anunciou revisao de processos internos com meta de reduzir custo operacional',
        NOW(), 70);
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system,
        operation, source_version, idempotency_key, status, request_payload, created_at)
VALUES (gen_random_uuid(), 'organization', '$ORG_A', 'postgresql', 'odoo', 'TIER', 'tiering-v1',
        NULL, 'PROCESSED', jsonb_build_object('tier', 'A+', 'score_lido',
        jsonb_build_object('score_value', 94.00)), NOW() - INTERVAL '1 day');
SQL
item "1.1 lead com pesquisa, dor, sinal e decisor contactavel" "sim" \
  "$(conta "SELECT CASE WHEN (SELECT count(*) FROM sales_intelligence.research_runs WHERE organization_id='$ORG_A' AND status='COMPLETED')=1 AND (SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_A')=1 AND (SELECT count(*) FROM sales_intelligence.signals WHERE organization_id='$ORG_A')=1 AND (SELECT count(*) FROM sales_intelligence.contacts WHERE organization_id='$ORG_A' AND email IS NOT NULL AND do_not_contact IS FALSE AND opt_out_email IS FALSE)=1 THEN 'sim' ELSE 'nao' END;")"
item "1.2 registro TIER A+ do W5 presente" "A+|PROCESSED" \
  "$(conta "SELECT request_payload->>'tier'||'|'||status FROM sales_intelligence.sync_events WHERE operation='TIER' AND entity_id='$ORG_A';")"

# ---------------------------------------------------------------------------------------------
echo "== 2. Hermes gera Next Best Action (W5-E07)"
python3 "$NBA" --ambiente dev --prefixo "$PREFIXO" --organizacao "$ORG_A" --raiz "$RAIZ" \
  --relatorio "$TRABALHO/nba1.json" --correlation-id "$CORR_NBA1" >"$TRABALHO/nba1.out" 2>&1
RC_NBA1=$?
item "2.1 NBA exit 0 com veredito RECOMENDADA" "0|sim" \
  "$RC_NBA1|$(grep -q '"veredito": "RECOMENDADA"' "$TRABALHO/nba1.out" && echo sim || echo nao)"
item "2.2 recomendacao OPEN com acao SEND_EMAIL" "NEXT_BEST_ACTION|SEND_EMAIL|OPEN" \
  "$(conta "SELECT recommendation_type||'|'||action||'|'||status FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"
item "2.3 contato escolhido e o decisor do lead" "$CT_A" \
  "$(conta "SELECT contact_id FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND status='OPEN';")"
item "2.4 rationale cita o tier lido" "sim" \
  "$(conta "SELECT CASE WHEN rationale LIKE '%tier=A+%' THEN 'sim' ELSE 'nao' END FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND status='OPEN';")"
item "2.5 auditoria da rodada (agent_runs, sem LLM)" "COMPLETED|0" \
  "$(conta "SELECT status||'|'||(SELECT count(*) FROM sales_intelligence.agent_runs WHERE organization_id='$ORG_A' AND model IS NOT NULL) FROM sales_intelligence.agent_runs WHERE organization_id='$ORG_A';")"

# ---------------------------------------------------------------------------------------------
echo "== 3. GPT cria o draft (W6-E02)"
python3 "$GERADOR" --ambiente dev --prefixo "$PREFIXO" --organizacao "$ORG_A" --raiz "$RAIZ" \
  --correlation-id "$CORR_GER1" --relatorio "$TRABALHO/ger1.json" >"$TRABALHO/ger1.out" 2>&1
RC_GER1=$?
item "3.1 gerador exit 0 e veredito GERADA" "0|sim" \
  "$RC_GER1|$(grep -q 'veredito=GERADA' "$TRABALHO/ger1.out" && echo sim || echo nao)"
PEDIDO="$(conta "SELECT id::text FROM sales_intelligence.human_approvals WHERE status='PENDING' AND entity_id='$CT_A' ORDER BY requested_at DESC LIMIT 1;")"
item "3.2 pedido PENDING gravado para o contato do lead" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.human_approvals WHERE id='$PEDIDO' AND status='PENDING';")"
item "3.3 rascunho com assunto, corpo e CTA" "3" \
  "$(conta "SELECT (length(proposed_action->>'assunto')>0)::int + (length(proposed_action->>'corpo')>0)::int + (length(proposed_action->>'cta')>0)::int FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")"
item "3.4 o pedido cita a recomendacao OPEN do NBA (nao inventa acao)" "sim" \
  "$(conta "SELECT CASE WHEN p.action_type='SEND_EMAIL' AND p.proposed_action->>'recommendation_id' = r.id::text THEN 'sim' ELSE 'nao' END FROM sales_intelligence.human_approvals p JOIN sales_intelligence.recommendations r ON r.organization_id='$ORG_A' AND r.status='OPEN' WHERE p.id='$PEDIDO';")"

# ---------------------------------------------------------------------------------------------
echo "== 4. Human Approval (W6-E03)"
python3 "$APROVACAO" --ambiente dev --prefixo "$PREFIXO" --fila --notificacoes "$TRABALHO/fila1.txt" \
  --relatorio "$TRABALHO/fila1.json" --correlation-id "$CORR_FILA1" >"$TRABALHO/fila1.out" 2>&1
RC_FILA1=$?
item "4.1 fila exit 0" "0" "$RC_FILA1"
item "4.2 a fila notificou 1 pedido novo" "NOTIFICADO|1" \
  "$(python3 - "$TRABALHO/fila1.json" <<'PY'
import json, sys
f = json.load(open(sys.argv[1], encoding="utf-8"))["fila"]
print(f"{f.get('veredito')}|{f.get('novos')}")
PY
)"
python3 "$APROVACAO" --ambiente dev --prefixo "$PREFIXO" --fila --relatorio "$TRABALHO/fila2.json" \
  --correlation-id "$CORR_FILA2" >"$TRABALHO/fila2.out" 2>&1
item "4.3 segunda fila NAO renotifica" "JA_NOTIFICADO|0" \
  "$(python3 - "$TRABALHO/fila2.json" <<'PY'
import json, sys
f = json.load(open(sys.argv[1], encoding="utf-8"))["fila"]
print(f"{f.get('veredito')}|{f.get('novos')}")
PY
)"
python3 "$APROVACAO" --ambiente dev --prefixo "$PREFIXO" --decidir "$PEDIDO" --decisao aprovar \
  --por "$OPERADOR" --correlation-id "$CORR_DEC1" >"$TRABALHO/dec1.out" 2>&1
item "4.4 aprovacao do operador humano gravada" "APPROVED|AndersonRibeiro" \
  "$(conta "SELECT status||'|'||replace(decided_by,' ','') FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")"
TEXTO_HASH="$(conta "SELECT proposed_action->'decisao'->>'texto_hash' FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")"
item "4.5 o texto aprovado tem hash carimbado" "sim" "$([ -n "$TEXTO_HASH" ] && echo sim || echo nao)"
python3 "$APROVACAO" --ambiente dev --prefixo "$PREFIXO" --consultar "$PEDIDO" \
  --relatorio "$TRABALHO/consulta.json" --correlation-id "$CORR_CONS1" >"$TRABALHO/consulta.out" 2>&1
item "4.6 o portao libera o pedido aprovado (pode_enviar true)" "sim" \
  "$(grep -q '"pode_enviar": true' "$TRABALHO/consulta.out" "$TRABALHO/consulta.json" && echo sim || echo nao)"

# ---------------------------------------------------------------------------------------------
echo "== 5. Titan envia (W6-E04 + sink SMTP local)"
python3 "$SINK_SMTP" --porta "$PORTA_SMTP" --modo "$MODO" --cert "$CA_PEM" --chave "$CA_KEY" \
  --captura "$TRABALHO/caixa-smtp.jsonl" --pronto "$TRABALHO/smtp.pronto" --pidfile "$TRABALHO/smtp.pid" \
  >"$TRABALHO/sink-smtp.log" 2>&1 &
for _ in $(seq 1 50); do [ -f "$TRABALHO/smtp.pronto" ] && break; sleep 0.2; done
item "5.0 sink SMTP local de pe" sim "$([ -f "$TRABALHO/smtp.pronto" ] && echo sim || echo nao)"
export TRE_TITAN_SMTP_HOST=127.0.0.1 TRE_TITAN_SMTP_PORT="$PORTA_SMTP" TRE_TITAN_SMTP_SEGURANCA="$MODO" \
  TRE_TITAN_USER=sink-dev TRE_TITAN_PASSWORD="$SENHA_SINK" TRE_TITAN_FROM="no-reply@$DOMINIO_DEV" \
  TRE_TITAN_DOMINIO_DEV="$DOMINIO_DEV" TRE_TITAN_TIMEOUT=10 TRE_TITAN_CA="$CA_PEM" \
  TRE_TITAN_APROVACAO_HUMANA=
conta_caixa_smtp() { grep -c '"evento": "MENSAGEM"' "$TRABALHO/caixa-smtp.jsonl" 2>/dev/null || true; }
python3 "$ENVIO" --ambiente dev --prefixo "$PREFIXO" --enviar "$PEDIDO" --trilha "$TRABALHO/envio-dry.jsonl" \
  --relatorio "$TRABALHO/envio-dry.json" --correlation-id "$CORR_DRY" >"$TRABALHO/envio-dry.out" 2>&1
RC_DRY=$?
item "5.1 dry-run exit 0 e veredito PLANO" "0|PLANO" \
  "$RC_DRY|$(python3 - "$TRABALHO/envio-dry.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["envios"][0]["veredito"])
PY
)"
item "5.2 dry-run NAO entrega" "0" "$(conta_caixa_smtp)"
python3 "$ENVIO" --ambiente dev --prefixo "$PREFIXO" --enviar "$PEDIDO" --confirmo \
  --trilha "$TRABALHO/envio.jsonl" --relatorio "$TRABALHO/envio.json" --correlation-id "$CORR_ENVIO" \
  >"$TRABALHO/envio.out" 2>&1
RC_ENVIO=$?
item "5.3 envio --confirmo exit 0 e veredito ENVIADO" "0|ENVIADO" \
  "$RC_ENVIO|$(python3 - "$TRABALHO/envio.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["envios"][0]["veredito"])
PY
)"
item "5.4 UMA mensagem chegou ao sink sob TLS" "1" "$(conta_caixa_smtp)"
item "5.5 destino = contato do lead (nunca da linha de comando)" "sim" \
  "$(grep -q "\"rcpt_to\": \[\"joao@$DOMINIO_DEV\"\]" "$TRABALHO/caixa-smtp.jsonl" && echo sim || echo nao)"
item "5.6 o AUTH do sink registra o usuario e nao a senha" "sim" \
  "$(grep -q '"autenticado_como": "sink-dev"' "$TRABALHO/caixa-smtp.jsonl" \
     && ! grep -q "$SENHA_SINK" "$TRABALHO/caixa-smtp.jsonl" && echo sim || echo nao)"
CORPO_SINK="$(python3 - "$TRABALHO/caixa-smtp.jsonl" <<'PY'
import email, json, sys
linhas = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if '"MENSAGEM"' in l]
msg = email.message_from_string(linhas[-1]["dados"])
print(msg.get_payload(decode=True).decode("utf-8", "replace").replace("\r\n", "\n").strip())
PY
)"
ESPERADO="$(psql_t -c "SELECT (proposed_action->>'corpo') || E'\n\n' || (proposed_action->>'cta') FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';" \
  | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
item "5.7 o corpo entregue e o texto APROVADO + CTA" "sim" \
  "$([ "$CORPO_SINK" = "$ESPERADO" ] && echo sim || echo nao)"

# ---------------------------------------------------------------------------------------------
echo "== 6. interaction outbound registrada (W6-E04)"
item "6.1 UMA interaction OUTBOUND com a referencia envio:<pedido>:<hash>" "1|envio:$PEDIDO:$TEXTO_HASH" \
  "$(conta "SELECT count(*)||'|'||max(content_reference) FROM sales_intelligence.interactions WHERE direction='OUTBOUND';")"
item "6.2 canal/direcao/tipo do contrato" "EMAIL|OUTBOUND|OUTBOUND_EMAIL" \
  "$(conta "SELECT channel||'|'||direction||'|'||interaction_type FROM sales_intelligence.interactions WHERE direction='OUTBOUND';")"
item "6.3 sync_events ENVIADO ligado a interaction, com o primitivo na trilha" "sim|1" \
  "$(conta "SELECT CASE WHEN request_payload->>'primitivo' LIKE '%smtp_titan%' THEN 'sim' ELSE 'nao' END FROM sales_intelligence.sync_events WHERE source_version='envio-outbound-v1' LIMIT 1;")|$(conta "SELECT count(*) FROM sales_intelligence.sync_events s JOIN sales_intelligence.interactions i ON s.response_payload->>'interaction_id' = i.id::text WHERE s.source_version='envio-outbound-v1' AND s.status='ENVIADO';")"
FOTO_POS_ENVIO="$(foto)"

# ---------------------------------------------------------------------------------------------
echo "== 7. reply recebido (W6-E05 + sink IMAP local, corpus de 1 resposta do lead)"
python3 - "$TRABALHO/respostas.jsonl" <<'PY'
import base64, json, sys
from email.message import EmailMessage
from email.utils import formatdate
msg = EmailMessage()
msg["From"] = "joao@cliente-demo.test"
msg["To"] = "anderson.ribeiro@transformativa.com.br"
msg["Subject"] = "Re: Eficiencia operacional com IA"
msg["Date"] = formatdate(localtime=False)
msg["Message-ID"] = "<e2e002-resposta-1@cliente-demo.test>"
msg.set_content("Oi Anderson, podemos conversar na quinta? Tenho interesse em entender melhor o escopo.")
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"uid": 1, "flags": [], "rotulo": "interesse_e2e002", "esperado": "INTERESSE",
                         "remetente_vinculado": True, "assunto": "Re: Eficiencia operacional com IA",
                         "raw_base64": base64.b64encode(msg.as_bytes()).decode("ascii")},
                        ensure_ascii=False, sort_keys=True) + "\n")
PY
item "7.1 fixture da resposta do lead gerada" "1" "$(grep -c . "$TRABALHO/respostas.jsonl")"
python3 "$SINK_IMAP" --porta "$PORTA_IMAP" --modo "$MODO" --cert "$CA_PEM" --chave "$CA_KEY" \
  --senha "$SENHA_SINK" --fixtures "$TRABALHO/respostas.jsonl" --captura "$TRABALHO/sink-imap.jsonl" \
  --pronto "$TRABALHO/imap.pronto" --pidfile "$TRABALHO/imap.pid" >"$TRABALHO/sink-imap.log" 2>&1 &
for _ in $(seq 1 40); do [ -s "$TRABALHO/imap.pronto" ] && break; sleep 0.25; done
item "7.2 sink IMAP local de pe com a resposta do lead" sim "$([ -s "$TRABALHO/imap.pronto" ] && echo sim || echo nao)"
export TRE_AMBIENTE=dev TRE_TITAN_IMAP_HOST=127.0.0.1 TRE_TITAN_IMAP_PORT="$PORTA_IMAP" \
  TRE_TITAN_IMAP_SEGURANCA="$MODO" TRE_TITAN_IMAP_CAIXA=INBOX TRE_TITAN_USER="sink-dev@$DOMINIO_DEV" \
  TRE_TITAN_PASSWORD="$SENHA_SINK" TRE_TITAN_CA="$CA_PEM" TRE_TITAN_DOMINIO_DEV="$DOMINIO_DEV" \
  TRE_RESPOSTAS_PORTA_BANCO="$PREFIXO"
python3 "$INGESTAO" --ingerir --confirmo --chave-idempotencia e2e002-rodada-1 --saida "$TRABALHO/ingeridas" \
  --porta-banco "$PREFIXO" --relatorio "$TRABALHO/ingestao.json" --registro "$TRABALHO/ingestao.jsonl" \
  >"$TRABALHO/ingestao.out" 2>&1
item "7.3 ingesta exit 0 com veredito proprio" "0|sim" \
  "$?|$(grep -q INGESTAO_RESPOSTAS_001_OK "$TRABALHO/ingestao.out" && echo sim || echo nao)"
item "7.4 UMA interaction INBOUND com a identidade UIDVALIDITY:UID" "1|sim" \
  "$(conta "SELECT count(*)||'|'||CASE WHEN max(content_reference) ~ '^[0-9]+:1$' THEN 'sim' ELSE 'nao' END FROM sales_intelligence.interactions WHERE direction='INBOUND';")"
item "7.5 invariante de leitura medido no sink (EXAMINE/PEEK/0 escrita/\\Seen)" "sim" \
  "$(python3 - "$TRABALHO/sink-imap.jsonl" <<'PY'
import json, sys
ev = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
f = [e for e in ev if e.get("evento") == "ESTADO_FINAL"]
if not f:
    print("nao"); raise SystemExit(0)
e = f[-1]
ok = (all("EXAMINE" in s for s in (e.get("selecoes") or [])) and (e.get("selecoes") or [])
      and e.get("total_buscas_sem_peek") == 0 and e.get("total_comandos_de_escrita") == []
      and e.get("mensagens_marcadas_lidas") == [])
print("sim" if ok else "nao")
PY
)"

# ---------------------------------------------------------------------------------------------
echo "== 8. GPT classifica a resposta (W6-E05)"
item "8.1 categoria INTERESSE na interaction INBOUND" "INTERESSE" \
  "$(conta "SELECT response_category FROM sales_intelligence.interactions WHERE direction='INBOUND';")"
item "8.2 intent e sentiment coerentes com o contrato" "PEDIDO_DE_CONVERSA|POSITIVO" \
  "$(conta "SELECT intent||'|'||sentiment FROM sales_intelligence.interactions WHERE direction='INBOUND';")"
item "8.3 a resposta foi vinculada ao lead pelo remetente (nao inventou organizacao)" "$ORG_A|$CT_A" \
  "$(conta "SELECT organization_id||'|'||contact_id FROM sales_intelligence.interactions WHERE direction='INBOUND';")"
python3 "$INGESTAO" --ingerir --confirmo --chave-idempotencia e2e002-rodada-1 --porta-banco "$PREFIXO" \
  >"$TRABALHO/ingestao-replay.out" 2>&1
RC_REPLAY=$?
item "8.4 replay da mesma chave = JA_INGERIDO e sem linha nova" "0|1|2" \
  "$RC_REPLAY|$(grep -c JA_INGERIDO "$TRABALHO/ingestao-replay.out")|$(conta "SELECT count(*) FROM sales_intelligence.interactions;")"

# ---------------------------------------------------------------------------------------------
echo "== 9. Odoo atualizado (W6-E06 + stub da API controlada em loopback)"
python3 - "$TRABALHO/leads.json" "$LEAD_ODOO" <<'PY'
import json, sys
lead = {"id": int(sys.argv[2]), "name": "Distribuidora Alfa — oportunidade",
        "tf_opportunity_id": "0f0f0f0f-1111-2222-3333-444455556666", "tf_next_best_action": None}
json.dump({str(lead["id"]): lead}, open(sys.argv[1], "w", encoding="utf-8"))
PY
export TRE_ODOO_STUB_CHAVE="$SENHA_API" TRE_ODOO_STUB_REGISTRO="$TRABALHO/stub.jsonl" \
  TRE_ODOO_STUB_LEADS="$TRABALHO/leads.json"
python3 "$STUB" --porta "$PORTA_API" >"$TRABALHO/stub.saida" 2>&1 &
echo $! >"$TRABALHO/stub.pid"
for _ in $(seq 1 30); do
  python3 -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:$PORTA_API/tf/api/v1/sistema_capacidades?tf.api.ambiente=dev', data=b'{}', headers={'Authorization': 'Bearer $SENHA_API', 'Content-Type': 'application/json'}), timeout=2)" 2>/dev/null && break
  sleep 0.3
done
item "9.0 stub da API do Odoo respondendo em loopback" "0" "$?"
CHAMADAS_BASE=$(wc -l <"$TRABALHO/stub.jsonl")
export TRE_ODOO_API_URL="http://127.0.0.1:$PORTA_API" TRE_ODOO_API_KEY="$SENHA_API" \
  TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA=1 TRE_ODOO_RESPOSTAS_PORTA_BANCO="$PREFIXO" \
  TRE_ODOO_RESPOSTAS_CORRELACAO="e2e002"
python3 "$ODOO" --propagar --confirmo --saida "$TRABALHO/odoo-sem-vinculo" >"$TRABALHO/odoo-sem-vinculo.out" 2>&1
RC_SEM_VINCULO=$?
item "9.1 SEM o vinculo do lead o CRM responde SEM_VINCULO (lacuna medida, nao escondida)" "0|1" \
  "$RC_SEM_VINCULO|$(conta "SELECT count(*) FROM sales_intelligence.sync_events WHERE source_version='atualizacao-odoo-respostas-v1' AND status='SEM_VINCULO';")"
# PONTE DECLARADA (papel do E2E #001 / fundacao W3-W4): o lead nasce no CRM e o id volta para a
# interacao. NENHUM componente da onda W6 grava interactions.odoo_lead_id — e' por isso que o aceite
# mede o SEM_VINCULO acima antes de aplicar a ponte. A ponte e' do HARNESS, nao do componente.
psql_t -c "UPDATE sales_intelligence.interactions SET odoo_lead_id = $LEAD_ODOO WHERE organization_id='$ORG_A' AND direction='INBOUND';" >/dev/null
item "9.2 ponte do lead declarada e medida (a resposta com odoo_lead_id)" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE odoo_lead_id=$LEAD_ODOO AND direction='INBOUND';")"
python3 "$ODOO" --propagar --confirmo --saida "$TRABALHO/odoo" >"$TRABALHO/odoo.out" 2>&1
item "9.3 propagacao para o CRM exit 0" "0" "$?"
item "9.4 a resposta positiva virou ATUALIZADO na trilha do banco canonico" "1|ATUALIZADO" \
  "$(conta "SELECT count(*)||'|'||max(status) FROM sales_intelligence.sync_events WHERE source_version='atualizacao-odoo-respostas-v1' AND idempotency_key='odoo-resposta:$(conta "SELECT id::text FROM sales_intelligence.interactions WHERE direction='INBOUND';")';")"
item "9.5 o CRM recebeu RESPOSTA_INTERESSE + RESPONDER_AGORA" "sim" \
  "$(grep -q RESPOSTA_INTERESSE "$TRABALHO/stub.jsonl" && grep -q RESPONDER_AGORA "$TRABALHO/stub.jsonl" && echo sim || echo nao)"
item "9.6 atividade criada no contato do lead (res_id 12) e idempotency_key em toda escrita" "sim" \
  "$(python3 - "$TRABALHO/stub.jsonl" <<'PY'
import json, sys
linhas = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
escritas = [l for l in linhas if l["operacao"] in ("oportunidade_upsert", "atividade_criar")]
atividades = [l for l in linhas if l["operacao"] == "atividade_criar"]
res_ids = {(l["corpo"].get("parametros", {}).get("valores", {}) or {}).get("res_id") for l in atividades}
ok = all(l["ambiente"] == "dev" for l in linhas) and all(l.get("bearer_confere") for l in linhas) \
     and all(l["corpo"].get("idempotency_key") for l in escritas) and 12 in res_ids
print("sim" if ok else "nao")
PY
)"
item "9.7 a chave da API nao aparece na saida do componente" "0" \
  "$(grep -c "$SENHA_API" "$TRABALHO/odoo.out" "$TRABALHO/odoo"/* 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')"

# ---------------------------------------------------------------------------------------------
echo "== 10. nova NBA criada (evidencia nova do reply)"
python3 "$NBA" --ambiente dev --prefixo "$PREFIXO" --organizacao "$ORG_A" --raiz "$RAIZ" \
  --relatorio "$TRABALHO/nba2.json" --correlation-id "$CORR_NBA2" >"$TRABALHO/nba2.out" 2>&1
RC_NBA2=$?
item "10.1 segunda rodada do NBA exit 0" "0" "$RC_NBA2"
item "10.2 resposta positiva gera acao nova CREATE_MEETING (uma unica OPEN)" "CREATE_MEETING|1" \
  "$(conta "SELECT action||'|'||count(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND status='OPEN' GROUP BY action;")"
item "10.3 a recomendacao anterior virou SUPERSEDED e o historico foi preservado" "SEND_EMAIL|1" \
  "$(conta "SELECT action||'|'||count(*) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A' AND status='SUPERSEDED' GROUP BY action;")"
item "10.4 duas recomendacoes distintas (id novo, nao reescrita)" "2" \
  "$(conta "SELECT count(DISTINCT id) FROM sales_intelligence.recommendations WHERE organization_id='$ORG_A';")"

# ---------------------------------------------------------------------------------------------
echo "== 11. guardas de ambiente, escopo de escrita e segredos"
TRE_AMBIENTE=prod python3 "$ENVIO" --ambiente prod --enviar "$PEDIDO" --prefixo "$PREFIXO" --confirmo >/dev/null 2>&1
RC_A=$?
TRE_AMBIENTE=prod python3 "$INGESTAO" --ambiente prod --ingerir --chave-idempotencia x >/dev/null 2>&1
RC_B=$?
TRE_AMBIENTE=prod python3 "$ODOO" --ambiente prod --propagar --confirmo >/dev/null 2>&1
RC_C=$?
TRE_AMBIENTE=prod python3 "$NBA" --ambiente prod --organizacao "$ORG_A" >/dev/null 2>&1
RC_D=$?
item "11.1 prod RECUSADO nos 4 componentes (exit 4, por desenho)" "4/4/4/4" "$RC_A/$RC_B/$RC_C/$RC_D"
item "11.2 nenhuma tabela nova (12 tabelas antes e depois)" "$TABELAS_ANTES" \
  "$(conta "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence';")"
item "11.3 a senha do sink nao aparece em relatorio, trilha nem saida" "0" \
  "$(grep -rc "$SENHA_SINK" "$TRABALHO" 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')"

# ---------------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== 12. prova de dente (mutacao em COPIA de componente: o aceite tem de reprovar O ITEM ESPERADO)"
  # O sub-run do dente e' um aceite INTEIRO: ele precisa das 3 portas locais (sink SMTP, sink IMAP e
  # stub). As pontas do aceite-pai ainda estao de pe neste ponto — libera antes de medir.
  for p in "$TRABALHO"/*.pid; do [ -f "$p" ] && kill "$(cat "$p")" >/dev/null 2>&1; done
  rm -f "$TRABALHO"/*.pid
  sleep 1
  DENTES_FALHAS=0
  dente() { # <rotulo> <flag> <modulo_origem> <de> <para> <item_esperado>
    local rotulo="$1" flag="$2" origem="$3" de="$4" para="$5" alvo="$6"
    local copia="$TRABALHO/dente-$rotulo.py"
    cp "$origem" "$copia"
    if ! grep -qF -- "$de" "$copia"; then
      echo "ABORTA dente $rotulo: ancora nao encontrada — mutacao que nao aplica e' buraco"
      ITENS_FALHOU=$((ITENS_FALHOU + 1)); return
    fi
    python3 - "$copia" "$de" "$para" <<'PY'
import sys
p, de, para = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8").write(t.replace(de, para, 1))
PY
    TRE_E2E002_CONTAINER="$CONTAINER-dente-$rotulo" TRE_E2E002_TRABALHO="$TRABALHO/dente-$rotulo" \
      bash "$0" --sub-run "$flag" "$copia" >"$TRABALHO/dente-$rotulo.out" 2>&1
    if grep -qF "FALHOU $alvo" "$TRABALHO/dente-$rotulo.out"; then
      echo "OK     dente $rotulo -> reprovou '$alvo'"
    else
      echo "FALHOU dente $rotulo: o item '$alvo' continuou verde"; DENTES_FALHAS=$((DENTES_FALHAS + 1))
    fi
  }
  dente sem-cta-na-mensagem --envio "$ENVIO" "    if cta:" "    if False:" \
    "5.7 o corpo entregue e o texto APROVADO + CTA"
  dente sem-supersessao --nba "$NBA" \
    "SET status = {lit(STATUS_SUPERSEDIDA)}" "SET status = status" \
    "10.3 a recomendacao anterior virou SUPERSEDED e o historico foi preservado"
  dente primeira-regra-sempre --nba "$NBA" \
    "if all(avaliar_condicao(fatos, c) for c in regra[\"quando\"]):" "if True:" \
    "2.2 recomendacao OPEN com acao SEND_EMAIL"
  ITENS_FALHOU=$((ITENS_FALHOU + DENTES_FALHAS))
fi

echo
echo "== ACEITE $ITENS_OK OK / $ITENS_FALHOU FALHOU"
if [ "$ITENS_FALHOU" -eq 0 ]; then
  echo "ACEITE_E2E_OUTBOUND_002_OK"
  exit 0
fi
echo "ACEITE_E2E_OUTBOUND_002_FALHOU"
exit 1
