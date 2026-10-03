#!/usr/bin/env bash
# teste_envio_outbound_aceite.sh — ACEITE E2E do envio outbound v1 (card TRE-W6-E04-T01).
#
# Mede o caminho REAL, ponta a ponta, com o card irmao fazendo o que faz e sem credencial real:
#   1. Postgres DESCARTavel (docker, imagem postgres:16) + db/migrations/0001;
#   2. massa minima (organizacao/contato/pesquisa/dor/sinal/score/recomendacao);
#   3. o pedido nasce do GERADOR do card W6-E03-T01 e e aprovado pelo `approval_workflow.py`
#      (nada de pedido fabricado a mao: o portao tem de aceitar o que o irmao gravou);
#   4. o envio roda contra um SINK SMTP local descartavel (127.0.0.1, TLS proprio, ADR-005) —
#      nenhuma credencial Titan, nenhum destino real;
#   5. conferido no banco o que o envio deixou: `interactions` (fato, canal/direcao/referencia),
#      `sync_events` (claim ENVIANDO -> ENVIADO ligado a interaction, ou FALHOU), contagens das
#      outras tabelas intocadas, idempotencia (replay = JA_ENVIADO), portao (pedido rejeitado
#      NAO envia), prod (exit 4), --desfazer (dry-run x --confirmo preservando o fato).
# Veredito: ACEITE_ENVIO_OUTBOUND_001_OK / ACEITE_ENVIO_OUTBOUND_001_FALHOU.
# --prova-de-dente: muta uma COPIA do modulo e exige que o aceite reprove O ITEM ESPERADO.
# Exit: 0 = OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_ENVIO_CONTAINER:-pg-envio-acc}"
TRABALHO="${TRE_ENVIO_TRABALHO:-/tmp/envio-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="envio-aceite-descartavel"
MODULO="$RAIZ/hermes/agents/outreach/send_workflow.py"
APROVACAO="$RAIZ/hermes/agents/outreach/approval_workflow.py"
GERADOR="$RAIZ/hermes/agents/outreach/outreach_generator.py"
POLITICA="$RAIZ/hermes/agents/outreach/politica-envio-v1.json"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
SINK="$RAIZ/scripts/integracoes/sink-smtp-dev.py"
OPERADOR="Anderson Ribeiro"
DOMINIO_DEV="dev.local"
PORTA_SINK=2465
MODO="implicit_tls"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; MODULO="${1:?--codigo exige caminho}" ;;
    --codigo=*) MODULO="${1#--codigo=}" ;;
    *) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <modulo.py>]"; exit 2 ;;
  esac
  shift
done

ITENS_OK=0
ITENS_FALHOU=0
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then echo "OK     $1 ($3)"; ITENS_OK=$((ITENS_OK + 1))
  else echo "FALHOU $1 (esperado=$2 obtido=$3)"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); fi
}

command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
command -v openssl >/dev/null 2>&1 || { echo "NAO_TESTAVEL openssl ausente (sem TLS proprio nao ha prova)"; exit 3; }
for f in "$MIGRATION" "$MODULO" "$APROVACAO" "$GERADOR" "$POLITICA" "$SINK"; do
  [ -f "$f" ] || { echo "FALHOU arquivo ausente: $f"; exit 2; }
done
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
rm -rf "$TRABALHO"; mkdir -p "$TRABALHO/caixa" "$TRABALHO/ca"
CAIXA="$TRABALHO/caixa/mensagens.jsonl"; : >"$CAIXA"
CA_PEM="$TRABALHO/ca/dev.pem"; CA_KEY="$TRABALHO/ca/dev.key"
SENHA_SINK="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"

limpar() {
  local rc=$?
  [ -f "$TRABALHO/sink.pid" ] && kill "$(cat "$TRABALHO/sink.pid")" >/dev/null 2>&1
  if [ "$MANTER" -eq 0 ]; then docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  else echo "== --manter: container $CONTAINER e $TRABALHO ficaram de pe"; fi
  exit $rc
}
trap limpar EXIT

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
contar() { psql_t -c "SELECT count(*) FROM $1;" | tr -d ' '; }
contar_caixa() { grep -c '"evento": "MENSAGEM"' "$CAIXA" 2>/dev/null || true; }
foto() {
  echo "$(contar sales_intelligence.organizations)|$(contar sales_intelligence.contacts)|$(contar sales_intelligence.recommendations)|$(contar sales_intelligence.scores)|$(contar sales_intelligence.signals)|$(contar sales_intelligence.pain_hypotheses)|$(contar sales_intelligence.research_runs)|$(contar sales_intelligence.outbox_events)"
}
tabelas() { psql_t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence';" | tr -d ' '; }

# ------------------------------------------------------------------ container + migration + massa
docker run -d --name "$CONTAINER" -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" \
  -e "POSTGRES_DB=$BANCO" "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
for _ in $(seq 1 60); do
  psql_t -c "SELECT 1;" >/dev/null 2>&1 && psql_t -c "SELECT 1;" >/dev/null 2>&1 && break
  sleep 1
done
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }

ORG_A="11111111-1111-4111-8111-111111111111"   # pedido enviado de verdade
ORG_B="22222222-2222-4222-8222-222222222222"   # pedido rejeitado pelo operador -> nao pode enviar

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, industry_name, employee_band,
        employee_count, city, state, status, created_at)
VALUES ('$ORG_A', 'Distribuidora Alfa LTDA', 'Distribuidora Alfa', 'Distribuicao B2B', '150_299', 210,
        'Campinas', 'SP', 'Qualificado', NOW()),
       ('$ORG_B', 'Industria Beta LTDA', 'Industria Beta', 'Industria', '300_499', 320,
        'Joinville', 'SC', 'Qualificado', NOW());
INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, first_name, job_title,
        decision_role, email, preferred_channel, do_not_contact, opt_out_email, created_at)
VALUES (gen_random_uuid(), '$ORG_A', 'Maria Souza', 'Maria', 'Diretora de Operacoes',
        'Decision Maker', 'maria.souza@$DOMINIO_DEV', 'EMAIL', false, false, NOW()),
       (gen_random_uuid(), '$ORG_B', 'Joao Lima', 'Joao', 'Diretor Industrial',
        'Decision Maker', 'joao.lima@$DOMINIO_DEV', 'EMAIL', false, false, NOW());
INSERT INTO sales_intelligence.research_runs (id, organization_id, agent_name, status, summary,
        completed_at, created_at)
SELECT gen_random_uuid(), o.id, 'scout', 'COMPLETED',
       'Empresa com tres centros de distribuicao e pedidos redigitados no ERP', NOW(), NOW()
FROM sales_intelligence.organizations o WHERE o.id IN ('$ORG_A', '$ORG_B');
INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id, pain_category, pain_statement,
        status, created_at)
SELECT gen_random_uuid(), o.id, 'PROCESSO_MANUAL',
       'Pedidos chegam por e-mail e sao redigitados no ERP', 'PARTIALLY_VALIDATED', NOW()
FROM sales_intelligence.organizations o WHERE o.id IN ('$ORG_A', '$ORG_B');
INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, title, description,
        detected_at, relevance_score)
SELECT gen_random_uuid(), o.id, 'EFFICIENCY_PROGRAM', 'Programa de eficiencia anunciado',
       'Empresa anunciou revisao de processos internos', NOW(), 70
FROM sales_intelligence.organizations o WHERE o.id IN ('$ORG_A', '$ORG_B');
INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version,
        calculated_at)
SELECT gen_random_uuid(), o.id, 'PRIORITY', 82.5, 'priority-v1', NOW()
FROM sales_intelligence.organizations o WHERE o.id IN ('$ORG_A', '$ORG_B');
INSERT INTO sales_intelligence.recommendations (id, organization_id, contact_id, recommendation_type,
        action, rationale, status, priority, created_at)
SELECT gen_random_uuid(), o.id, c.id, 'NEXT_BEST_ACTION', 'SEND_EMAIL',
       'tier A com decisor contactavel', 'OPEN', 1, NOW()
FROM sales_intelligence.organizations o JOIN sales_intelligence.contacts c ON c.organization_id = o.id
WHERE o.id IN ('$ORG_A', '$ORG_B');
SQL
item "0.1 migration aplicada e massa no banco (2 organizacoes)" "2" "$(contar sales_intelligence.organizations)"
TABELAS_ANTES="$(tabelas)"
FOTO_ANTES="$(foto)"

# ------------------------------------------------------------------ sink SMTP descartavel
# Certificado proprio do sink. NAO uso `-addext`: neste host o openssl le um configuracao de sistema
# quebrada (`/etc/ssl/openssl.conf.d` ausente) e o `-addext` falha — com `-config` proprio o SAN entra.
cat > "$TRABALHO/ca/openssl.cnf" <<CONF
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
DNS.2 = sink-smtp-dev
CONF
if ! openssl req -x509 -newkey rsa:2048 -nodes -keyout "$CA_KEY" -out "$CA_PEM" -days 2 \
  -config "$TRABALHO/ca/openssl.cnf" -extensions v3 >"$TRABALHO/openssl.log" 2>&1; then
  echo "NAO_TESTAVEL: nao consegui gerar o certificado do sink"; sed -n '1,5p' "$TRABALHO/openssl.log"; exit 3
fi
item "0.2 certificado do sink tem SAN de IP (a prova de TLS e real)" "sim" \
  "$(openssl x509 -in "$CA_PEM" -noout -text 2>/dev/null | grep -q 'IP Address:127.0.0.1' && echo sim || echo nao)"

python3 "$SINK" --porta "$PORTA_SINK" --modo "$MODO" --cert "$CA_PEM" --chave "$CA_KEY" \
  --captura "$CAIXA" --pronto "$TRABALHO/pronto-sink" --pidfile "$TRABALHO/sink.pid" \
  >"$TRABALHO/sink.log" 2>&1 &
for _ in $(seq 1 50); do [ -f "$TRABALHO/pronto-sink" ] && break; sleep 0.2; done
item "0.3 sink SMTP local de pe (pronto)" "sim" "$([ -f "$TRABALHO/pronto-sink" ] && echo sim || echo nao)"

export TRE_TITAN_SMTP_HOST=127.0.0.1 TRE_TITAN_SMTP_PORT="$PORTA_SINK" \
  TRE_TITAN_SMTP_SEGURANCA="$MODO" TRE_TITAN_USER=sink-dev TRE_TITAN_PASSWORD="$SENHA_SINK" \
  TRE_TITAN_FROM="no-reply@$DOMINIO_DEV" TRE_TITAN_DOMINIO_DEV="$DOMINIO_DEV" \
  TRE_TITAN_TIMEOUT=10 TRE_TITAN_CA="$CA_PEM" TRE_TITAN_APROVACAO_HUMANA=

# ------------------------------------------------------------------ cadeia real: gerar -> decidir
CORR_GER="$(python3 -c 'import uuid;print(uuid.uuid4())')"
python3 "$GERADOR" --ambiente dev --prefixo "$PREFIXO" --organizacao "$ORG_A" --correlation-id "$CORR_GER" \
  >"$TRABALHO/geracao_A.json" 2>"$TRABALHO/geracao_A.err"
item "1.1 gerador (card irmao) criou o pedido" "0" "$?"
item "1.2 o pedido nasceu PENDING" "1" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status='PENDING' AND entity_id IN (SELECT id FROM sales_intelligence.contacts WHERE organization_id='$ORG_A');" | tr -d ' ')"
PEDIDO_A="$(psql_t -c "SELECT id::text FROM sales_intelligence.human_approvals WHERE status='PENDING' AND entity_id IN (SELECT id FROM sales_intelligence.contacts WHERE organization_id='$ORG_A') ORDER BY requested_at DESC LIMIT 1;" | tr -d ' ')"
python3 "$APROVACAO" --ambiente dev --prefixo "$PREFIXO" --decidir "$PEDIDO_A" --decisao aprovar \
  --por "$OPERADOR" --correlation-id "$(python3 -c 'import uuid;print(uuid.uuid4())')" \
  >"$TRABALHO/decisao_A.json" 2>&1
item "1.3 aprovacao (card irmao) gravou APPROVED com operador" "1" "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE id='$PEDIDO_A' AND status='APPROVED' AND decided_by='$OPERADOR';" | tr -d ' ')"
TEXTO_HASH="$(psql_t -c "SELECT proposed_action->'decisao'->>'texto_hash' FROM sales_intelligence.human_approvals WHERE id='$PEDIDO_A';" | tr -d ' ')"
item "1.4 o pedido aprovado carrega o hash do texto aprovado" "sim" "$([ -n "$TEXTO_HASH" ] && echo sim || echo nao)"

# ------------------------------------------------------------------ 2. dry-run
ANTES="$(contar_caixa)"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --enviar "$PEDIDO_A" --correlation-id \
  "$(python3 -c 'import uuid;print(uuid.uuid4())')" >"$TRABALHO/envio_dry.json" 2>&1
item "2.1 dry-run exit 0" "0" "$?"
item "2.2 dry-run e PLANO" "PLANO" "$(python3 -c 'import json;print(json.load(open("'"$TRABALHO"'/envio_dry.json"))["envios"][0]["veredito"])')"
item "2.3 dry-run NAO entrega" "$ANTES" "$(contar_caixa)"
item "2.4 dry-run NAO reclama chave" "0" "$(contar sales_intelligence.sync_events)"

# ------------------------------------------------------------------ 3. envio confirmado
CORR_ENV="$(python3 -c 'import uuid;print(uuid.uuid4())')"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --enviar "$PEDIDO_A" --confirmo \
  --correlation-id "$CORR_ENV" >"$TRABALHO/envio_real.json" 2>&1
RC_ENVIO=$?
item "3.1 envio --confirmo exit 0" "0" "$RC_ENVIO"
item "3.2 veredito do envio" "ENVIADO" "$(python3 -c 'import json;print(json.load(open("'"$TRABALHO"'/envio_real.json"))["envios"][0]["veredito"])')"
item "3.3 UMA mensagem chegou ao sink" "1" "$(contar_caixa)"
item "3.4 a mensagem foi entregue sob TLS (modo da sessao na captura)" "1" \
  "$(grep -c '"modo": "implicit_tls"' "$CAIXA" || true)"
item "3.5 destino = contato do pedido (nunca da linha de comando)" "1" \
  "$(grep -c "\"rcpt_to\": \[\"maria.souza@$DOMINIO_DEV\"\]" "$CAIXA" || true)"
item "3.6 o AUTH do sink registrou o usuario e nao a senha" "sim" \
  "$(grep -q '"autenticado_como": "sink-dev"' "$CAIXA" && ! grep -q "$SENHA_SINK" "$CAIXA" && echo sim || echo nao)"
python3 - "$CAIXA" >"$TRABALHO/corpo.txt" <<'PY'
import json, sys, email
linhas = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if '"MENSAGEM"' in l]
msg = email.message_from_string(linhas[-1]["dados"])
corpo = msg.get_payload(decode=True).decode("utf-8", "replace")
print(corpo.replace("\r\n", "\n").strip())
PY
perl -0pi -e 's/\n\z//' "$TRABALHO/corpo.txt"
ESPERADO="$(psql_t -c "SELECT (proposed_action->>'corpo') || E'\n\n' || (proposed_action->>'cta') FROM sales_intelligence.human_approvals WHERE id='$PEDIDO_A';" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
item "3.7 corpo entregue = texto APROVADO + CTA (lido do banco)" "sim" \
  "$([ "$(cat "$TRABALHO/corpo.txt")" = "$ESPERADO" ] && echo sim || echo nao)"
ASSUNTO_SINK="$(python3 - "$CAIXA" <<'PYFIM'
import email, json, sys
from email.header import decode_header
linhas = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if '"MENSAGEM"' in l]
msg = email.message_from_string(linhas[-1]["dados"])
print("".join((t.decode(e or "utf-8", "replace") if isinstance(t, bytes) else t)
              for t, e in decode_header(msg["Subject"] or "")))
PYFIM
)"
ASSUNTO_APROVADO="$(psql_t -c "SELECT proposed_action->>'assunto' FROM sales_intelligence.human_approvals WHERE id='$PEDIDO_A';" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
item "3.8 assunto entregue = assunto APROVADO" "sim" \
  "$([ "$ASSUNTO_SINK" = "$ASSUNTO_APROVADO" ] && echo sim || echo nao)"

# ------------------------------------------------------------------ 4. o que ficou no banco
item "4.1 UMA interaction gravada" "1" "$(contar sales_intelligence.interactions)"
item "4.2 a interaction tem o canal, a direcao e o tipo do contrato" "EMAIL|OUTBOUND|OUTBOUND_EMAIL" \
  "$(psql_t -c "SELECT channel||'|'||direction||'|'||interaction_type FROM sales_intelligence.interactions LIMIT 1;" | tr -d ' ')"
item "4.3 a referencia aponta o pedido e o TEXTO aprovado" "envio:$PEDIDO_A:$TEXTO_HASH" \
  "$(psql_t -c "SELECT content_reference FROM sales_intelligence.interactions LIMIT 1;" | tr -d ' ')"
item "4.4 UMA linha em sync_events, ENVIADO" "1|ENVIADO" \
  "$(psql_t -c "SELECT count(*)||'|'||max(status) FROM sales_intelligence.sync_events WHERE source_version='envio-outbound-v1';" | tr -d ' ')"
item "4.5 a interaction aponta o pedido aprovado e a organizacao" "$PEDIDO_A|$ORG_A" \
  "$(psql_t -c "SELECT (content_reference IS NOT NULL)::text, organization_id::text FROM sales_intelligence.interactions LIMIT 1;" >/dev/null 2>&1; psql_t -c "SELECT regexp_replace(content_reference, '^envio:([^:]+):.*$', '\1')||'|'||organization_id::text FROM sales_intelligence.interactions LIMIT 1;" | tr -d ' ')"
item "4.6 sync_events liga a chave a interaction" "sim" \
  "$(psql_t -c "SELECT count(*) FROM sales_intelligence.sync_events s JOIN sales_intelligence.interactions i ON s.response_payload->>'interaction_id' = i.id::text WHERE s.source_version='envio-outbound-v1' AND s.status='ENVIADO';" | grep -q '^1$' && echo sim || echo nao)"
item "4.7 a trilha guarda o primitivo usado" "sim" \
  "$(psql_t -c "SELECT request_payload->>'primitivo' FROM sales_intelligence.sync_events WHERE source_version='envio-outbound-v1' LIMIT 1;" | grep -qi 'smtp_titan' && echo sim || echo nao)"
item "4.8 NENHUMA tabela nova" "$TABELAS_ANTES" "$(tabelas)"
item "4.9 as outras tabelas ficaram intocadas" "$FOTO_ANTES" "$(foto)"

# ------------------------------------------------------------------ 5. idempotencia
ANTES="$(contar_caixa)"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --enviar "$PEDIDO_A" --confirmo --correlation-id \
  "$(python3 -c 'import uuid;print(uuid.uuid4())')" >"$TRABALHO/envio_replay.json" 2>&1
item "5.1 replay da MESMA chave responde JA_ENVIADO" "JA_ENVIADO" \
  "$(python3 -c 'import json;print(json.load(open("'"$TRABALHO"'/envio_replay.json"))["envios"][0]["motivo"])')"
item "5.2 replay NAO entrega de novo" "$ANTES" "$(contar_caixa)"
item "5.3 replay NAO cria fato novo" "1" "$(contar sales_intelligence.interactions)"

# ------------------------------------------------------------------ 6. portao: pedido rejeitado
python3 "$GERADOR" --ambiente dev --prefixo "$PREFIXO" --organizacao "$ORG_B" --correlation-id \
  "$(python3 -c 'import uuid;print(uuid.uuid4())')" >"$TRABALHO/geracao_B.json" 2>&1
PEDIDO_B="$(psql_t -c "SELECT id::text FROM sales_intelligence.human_approvals WHERE status='PENDING' AND entity_id IN (SELECT id FROM sales_intelligence.contacts WHERE organization_id='$ORG_B') ORDER BY requested_at DESC LIMIT 1;" | tr -d ' ')"
python3 "$APROVACAO" --ambiente dev --prefixo "$PREFIXO" --decidir "$PEDIDO_B" --decisao rejeitar \
  --por "$OPERADOR" --correlation-id "$(python3 -c 'import uuid;print(uuid.uuid4())')" \
  >"$TRABALHO/decisao_B.json" 2>&1
ANTES="$(contar_caixa)"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --enviar "$PEDIDO_B" --confirmo --correlation-id \
  "$(python3 -c 'import uuid;print(uuid.uuid4())')" >"$TRABALHO/envio_B.json" 2>&1
item "6.1 pedido REJEITADO nao passa no portao" "PORTAO_NAO_LIBEROU" \
  "$(python3 -c 'import json;print(json.load(open("'"$TRABALHO"'/envio_B.json"))["envios"][0].get("motivo"))')"
item "6.2 pedido recusado NAO entrega" "$ANTES" "$(contar_caixa)"
item "6.3 pedido recusado NAO cria fato" "1" "$(contar sales_intelligence.interactions)"

# ------------------------------------------------------------------ 7. prod
ANTES_SYNC="$(contar sales_intelligence.sync_events)"
python3 "$MODULO" --ambiente prod --prefixo "$PREFIXO" --enviar "$PEDIDO_A" --confirmo \
  >"$TRABALHO/envio_prod.json" 2>&1
item "7.1 prod RECUSA com exit 4" "4" "$?"
item "7.2 prod NAO escreve nada" "$ANTES_SYNC" "$(contar sales_intelligence.sync_events)"

# ------------------------------------------------------------------ 8. desfazer
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CORR_ENV" \
  >"$TRABALHO/desfazer_dry.json" 2>&1
item "8.1 --desfazer dry-run conta 1 enviado" "1" \
  "$(python3 -c 'import json;print(json.load(open("'"$TRABALHO"'/desfazer_dry.json"))["desfazer"].get("seriam_desfeitos"))')"
item "8.2 dry-run NAO marca nada" "ENVIADO" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.sync_events WHERE source_version='envio-outbound-v1';" | tr -d ' ')"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CORR_ENV" --confirmo \
  >"$TRABALHO/desfazer_sem_por.json" 2>&1
item "8.3 --confirmo sem --por RECUSA (operador ausente)" "OPERADOR_AUSENTE" \
  "$(python3 -c 'import json;print(json.load(open("'"$TRABALHO"'/desfazer_sem_por.json"))["desfazer"].get("motivo"))')"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CORR_ENV" --confirmo --por "$OPERADOR" \
  --motivo "pedido errado" >"$TRABALHO/desfazer_real.json" 2>&1
item "8.4 --desfazer --confirmo marca DESFEITO" "DESFEITO" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.sync_events WHERE source_version='envio-outbound-v1';" | tr -d ' ')"
item "8.5 o fato em interactions NAO e apagado (auditoria preservada)" "1" "$(contar sales_intelligence.interactions)"
item "8.6 a marca carrega operador e motivo" "DESFEITO_POR:$OPERADOR:pedido errado" \
  "$(psql_t -c "SELECT error_message FROM sales_intelligence.sync_events WHERE source_version='envio-outbound-v1';" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
item "8.7 desfazer NAO apaga a mensagem ja entregue (o e-mail nao volta)" "1" "$(contar_caixa)"
item "8.8 as outras tabelas continuam intocadas" "$FOTO_ANTES" "$(foto)"

# ------------------------------------------------------------------ 9. dentes
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente (mutacao em copia do modulo: o aceite tem de reprovar O ITEM ESPERADO)"
  DENTES_FALHAS=0
  dente() { # <rotulo> <de> <para> <item_esperado>
    local rotulo="$1" de="$2" para="$3" alvo="$4" copia="$TRABALHO/dente-$1.py"
    cp "$MODULO" "$copia"
    if ! grep -qF -- "$de" "$copia"; then
      echo "ABORTA dente $rotulo: ancora nao encontrada — mutacao que nao aplica e buraco"; DENTES_FALHAS=$((DENTES_FALHAS + 1)); return
    fi
    python3 - "$copia" "$de" "$para" <<'PY'
import sys
p, de, para = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8").write(t.replace(de, para, 1))
PY
    TRE_ENVIO_CONTAINER="$CONTAINER-dente-$rotulo" TRE_ENVIO_TRABALHO="$TRABALHO/dente-$rotulo" \
      bash "$0" --codigo "$copia" >"$TRABALHO/dente-$rotulo.out" 2>&1
    if grep -qF "FALHOU $alvo" "$TRABALHO/dente-$rotulo.out"; then
      echo "OK     dente $rotulo -> reprovou '$alvo'"
    else
      echo "FALHOU dente $rotulo: o item '$alvo' continuou verde"; DENTES_FALHAS=$((DENTES_FALHAS + 1))
    fi
  }
  dente sem-claim-antes-do-smtp \
    "    reclamar_chave(politica, prefixo, chave, pedido_id, tentativas, correlation_id, triggered_by," \
    "    pass  # mutacao: sem claim" "4.4 UMA linha em sync_events, ENVIADO"
  dente sem-cta-na-mensagem "    if cta:" "    if False:" \
    "3.7 corpo entregue = texto APROVADO + CTA (lido do banco)"
  dente ignora-o-portao '    if not consulta.get("pode_enviar"):' "    if False:" \
    "6.1 pedido REJEITADO nao passa no portao"
  ITENS_FALHOU=$((ITENS_FALHOU + DENTES_FALHAS))
fi

echo "---"
if [ "$ITENS_FALHOU" -eq 0 ]; then
  echo "ACEITE_ENVIO_OUTBOUND_001_OK ($ITENS_OK itens, 0 falhas)"
  exit 0
fi
echo "ACEITE_ENVIO_OUTBOUND_001_FALHOU ($ITENS_OK ok, $ITENS_FALHOU falhas)"
exit 1
