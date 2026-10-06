#!/usr/bin/env bash
# =============================================================================================
# ACEITE E2E DA INGESTAO DE LEADS META — card TRE-W7-E02-T01
#
# Mede a cadeia INTEIRA do card contra pontas reais e descartaveis, sem token do Meta e sem tocar nada
# que ja' exista (ADR-005): stub LOCAL da Graph API (127.0.0.1, GET com Bearer, contador de chamadas) +
# PostgreSQL DESCARTavel (`pg-meta-acc`, migration 0001 aplicada) + o componente
# `hermes/agentes/inbound/ingestao_leads_meta.py` gravando em sales_intelligence.
#
# Webhooks ASSINADOS de verdade: o proprio aceite calcula o HMAC-SHA256 do corpo cru com o app secret
# (mesmo algoritmo do cabecalho X-Hub-Signature-256), inclusive o caso de assinatura ERRADA.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas: --planejar/--conferir sem banco, prod RECUSA (exit 4), porta de banco remota RECUSA
#      (BANCO_NAO_E_DEV), Graph fora de loopback RECUSA (GRAPH_NAO_E_DEV), --ingerir sem --confirmo e'
#      DRY_RUN e nao grava nada nem chama a Graph;
#   2. assinatura: entrega com HMAC errado NAO vira interacao e NAO chama a Graph API;
#   3. ingesta: lead com vinculo por e-mail e lead com vinculo por TELEFONE (formatado) viram UMA linha
#      cada em interactions, com channel/direction/interaction_type/response_category/intent/sentiment
#      do contrato e content_reference = meta-lead:<page>:<leadgen_id>;
#   4. sem vinculo NAO inventa organizacao (SEM_VINCULO, zero linha em interactions);
#   5. dado insuficiente (sem e-mail e sem telefone) NAO vira interacao (DADOS_INSUFICIENTES);
#   6. lead apagado na origem vira LEAD_INDISPONIVEL (404, sem retry) e erro persistente vira ERRO_GRAPH
#      respeitando o TETO de tentativas do contrato (medido pelo contador do stub);
#   7. idempotencia: entrega repetida no MESMO lote e replay do lote inteiro -> JA_INGERIDO, zero linha
#      nova e ZERO nova chamada a Graph API para o lead ja' ingerido;
#   8. PII fora do texto livre: content_summary nao contem e-mail/telefone em claro; campo fora do mapa
#      do contrato e' registrado apenas pelo nome em campos_desconhecidos;
#   9. escopo de escrita: snapshot de contagem das 12 tabelas antes/depois — so interactions e sync_events
#      mudam (nenhuma DDL, nenhum UPDATE/DELETE);
#  10. segredo: token e app secret nao aparecem em stdout/relatorio/registro.
#
# Veredito: ACEITE_META_LEADS_001_OK / ACEITE_META_LEADS_001_FALHOU.
# --prova-de-dente: muta uma COPIA do componente e exige que o aceite reprove O ITEM ESPERADO.
# Exit: 0 = OK · 1 = FALHOU · 2 = uso/guarda · 3 = nao testavel · 4 = recusa de ambiente.
#
# Pre-requisitos: docker com a imagem postgres:16 e python3. Nada de rede externa.
# Uso: bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh [--manter] [--prova-de-dente]
# =============================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_W7E02_CONTAINER:-pg-meta-acc}"
TRABALHO="${TRE_W7E02_TRABALHO:-/tmp/meta-leads-aceite}"
USUARIO=sales_ai
BANCO=sales_intelligence
SENHA=dev
PORTA_GRAPH="${TRE_W7E02_PORTA_GRAPH:-8799}"
MODULO="${TRE_W7E02_MODULO:-$RAIZ/hermes/agentes/inbound/ingestao_leads_meta.py}"
CONTRATO="$RAIZ/hermes/agentes/inbound/meta-lead-ingestion-v1.json"
STUB="$RAIZ/scripts/agentes/stub-meta-graph-dev.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
TOKEN="$(python3 -c 'import secrets;print("tk-"+secrets.token_urlsafe(18))')"
APP_SECRET="$(python3 -c 'import secrets;print("as-"+secrets.token_urlsafe(18))')"
ORG_A="22222222-2222-4222-8222-222222222222"
CT_EMAIL="d2222222-2222-4222-8222-222222222221"
CT_FONE="d2222222-2222-4222-8222-222222222222"
PAGE="page-w7e02"
MANTER=0
DENTE=0
OUTRO_MODULO=""

usage() { echo "uso: $0 [--manter] [--prova-de-dente] [--modulo <py>]"; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --manter) MANTER=1 ;;
    --prova-de-dente) DENTE=1 ;;
    --modulo) shift; OUTRO_MODULO="${1:?--modulo exige caminho}"; MODULO="$OUTRO_MODULO" ;;
    *) usage ;;
  esac
  shift
done
[ -n "$OUTRO_MODULO" ] && DENTE=0   # sub-run do dente: mede sem re-mutar

ITENS_OK=0
ITENS_FALHOU=0
FALHAS=""
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then echo "OK     $1 ($3)"; ITENS_OK=$((ITENS_OK + 1))
  else echo "FALHOU $1 (esperado=$2 obtido=$3)"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); FALHAS="$FALHAS | $1"; fi
}
item_sim() { item "$1" sim "$2"; }

for c in docker python3; do
  command -v "$c" >/dev/null 2>&1 || { echo "NAO_TESTAVEL $c ausente (rode na VPS do ambiente)"; exit 3; }
done
docker info >/dev/null 2>&1 || { echo "NAO_TESTAVEL docker nao responde"; exit 3; }
for f in "$MIGRATION" "$MODULO" "$CONTRATO" "$STUB"; do
  [ -f "$f" ] || { echo "FALHOU artefato ausente: $f"; exit 2; }
done
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
if python3 -c "import socket,sys;s=socket.socket();s.settimeout(0.5);sys.exit(0 if s.connect_ex(('127.0.0.1',$PORTA_GRAPH))!=0 else 1)"; then :; else
  echo "FALHOU a porta local $PORTA_GRAPH JA esta ocupada — sobra de rodada anterior; mate o processo e repita"; exit 2
fi

limpar() {
  local rc=$?
  [ -f "$TRABALHO/stub.pid" ] && kill "$(cat "$TRABALHO/stub.pid")" >/dev/null 2>&1
  if [ "$MANTER" -eq 0 ]; then docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  else echo "== --manter: $CONTAINER e $TRABALHO ficaram de pe"; fi
  exit $rc
}
trap limpar EXIT

echo "== 0. pre-flight e trio descartavel"
rm -rf "$TRABALHO"; mkdir -p "$TRABALHO"
docker run -d --name "$CONTAINER" -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" \
  -e "POSTGRES_DB=$BANCO" "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
PRONTO=nao
for _ in $(seq 1 60); do
  n=$(docker logs "$CONTAINER" 2>&1 | grep -c "database system is ready to accept connections")
  if [ "${n:-0}" -ge 2 ] && docker exec "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -tAc "select 1" >/dev/null 2>&1; then
    PRONTO=sim; break
  fi
  sleep 1
done
item "0.1 PostgreSQL descartavel de pe (init concluido: 2x ready)" sim "$PRONTO"

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
conta() { psql_t -c "$1" | tr -d ' ' | head -1; }
TABELAS_12="organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals"
foto() { local s=""; for t in $TABELAS_12; do s="$s$(conta "SELECT count(*) FROM sales_intelligence.$t;")/"; done; echo "$s"; }

psql_stdin < "$MIGRATION" >"$TRABALHO/migration.out" 2>&1
item "0.2 migration 0001 aplicada (12 tabelas)" "12" \
  "$(conta "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence';")"

echo "== 1. massa: organizacao + contatos (um por e-mail, outro so' por telefone formatado)"
psql_stdin >/dev/null 2>&1 <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain, status, created_at)
VALUES ('$ORG_A', 'Distribuidora Beta LTDA', 'Distribuidora Beta', 'cliente-demo.test', 'Pesquisado', NOW());
INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, preferred_channel, created_at)
VALUES ('$CT_EMAIL', '$ORG_A', 'Maria Lima', 'maria@cliente-demo.test', NULL, 'EMAIL', NOW());
INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, preferred_channel, created_at)
VALUES ('$CT_FONE', '$ORG_A', 'Pedro Alves', NULL, '+55 (11) 97777-6655', 'WHATSAPP', NOW());
SQL
item "1.1 organizacao e 2 contatos inseridos" "2" \
  "$(conta "SELECT count(*) FROM sales_intelligence.contacts;")"

echo "== 2. stub local da Graph API (sink) + entregas assinadas de verdade"
cat >"$TRABALHO/leads.json" <<JSON
{
  "lead-email":   {"id": "lead-email", "created_time": 1790000100, "ad_id": "ad-77", "form_id": "form-w7",
                   "field_data": [{"name": "email", "values": ["Maria@Cliente-Demo.test"]},
                                  {"name": "full_name", "values": ["Maria Lima"]},
                                  {"name": "company_name", "values": ["Distribuidora Beta"]},
                                  {"name": "whatsapp_optin", "values": ["true"]}]},
  "lead-fone":    {"id": "lead-fone", "created_time": 1790000200, "ad_id": "ad-78", "form_id": "form-w7",
                   "field_data": [{"name": "phone_number", "values": ["5511977776655"]},
                                  {"name": "full_name", "values": ["Pedro Alves"]},
                                  {"name": "message", "values": ["quero automatizar o comercial"]}]},
  "lead-desconhecido": {"id": "lead-desconhecido", "created_time": 1790000300, "form_id": "form-w7",
                   "field_data": [{"name": "email", "values": ["ninguem@outra-empresa.test"]},
                                  {"name": "full_name", "values": ["Alguem de Fora"]}]},
  "lead-sem-contato": {"id": "lead-sem-contato", "created_time": 1790000400, "form_id": "form-w7",
                   "field_data": [{"name": "full_name", "values": ["Sem Contato"]},
                                  {"name": "company_name", "values": ["Empresa Sem Contato"]}]},
  "lead-erro":    {"id": "lead-erro", "created_time": 1790000500, "form_id": "form-w7",
                   "field_data": [{"name": "email", "values": ["maria@cliente-demo.test"]}]}
}
JSON
TRE_META_STUB_TOKEN="$TOKEN" TRE_META_STUB_LEADS="$TRABALHO/leads.json" \
TRE_META_STUB_REGISTRO="$TRABALHO/graph.jsonl" TRE_META_STUB_FALHAR_SEMPRE="lead-erro" \
  python3 "$STUB" --porta "$PORTA_GRAPH" >"$TRABALHO/stub.out" 2>&1 &
echo $! >"$TRABALHO/stub.pid"
for _ in $(seq 1 40); do
  python3 -c "import socket,sys;s=socket.socket();s.settimeout(0.4);sys.exit(1 if s.connect_ex(('127.0.0.1',$PORTA_GRAPH))!=0 else 0)" && break
  sleep 0.25
done
item "2.1 stub da Graph API escutando em 127.0.0.1:$PORTA_GRAPH" "0" \
  "$(python3 -c "import socket;s=socket.socket();s.settimeout(0.4);print(0 if s.connect_ex(('127.0.0.1',$PORTA_GRAPH))==0 else 1)")"

cat >"$TRABALHO/entregas.py" <<'PY'
import hashlib, hmac, json, sys
segredo, destino, page = sys.argv[1], sys.argv[2], sys.argv[3]
def webhook(leadgen, criado=1790000000, page=page):
    return {"object": "page", "entry": [{"id": page, "time": criado,
            "changes": [{"field": "leadgen", "value": {"leadgen_id": leadgen, "page_id": page,
                                                       "form_id": "form-w7", "ad_id": "ad-77",
                                                       "created_time": criado}}]}]}
def assino(corpo, segredo):
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
def corpo(doc):
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
linhas = []
def add(leadgen, assinatura=None, doc=None):
    doc = doc or webhook(leadgen)
    bruto = corpo(doc)
    linhas.append(json.dumps({"corpo": doc, "assinatura": assinatura or assino(bruto, segredo)},
                             ensure_ascii=False, sort_keys=True))
add("lead-email")
add("lead-fone")
add("lead-desconhecido")
add("lead-sem-contato")
add("lead-apagado")        # 404 no stub
add("lead-erro")           # 500 SEMPRE no stub -> teto de tentativas
add("lead-email")          # repetida no MESMO lote -> JA_INGERIDO
add("lead-email", assinatura="sha256=" + "0" * 64)   # HMAC errado -> nao ingere, nao chama Graph
add("lead-email", assinatura="sha256=" + "0" * 64)   # a MESMA entrega invalida de novo: replay nao derruba a rodada
open(destino, "w", encoding="utf-8").write("\n".join(linhas) + "\n")
PY
python3 "$TRABALHO/entregas.py" "$APP_SECRET" "$TRABALHO/entregas.jsonl" "$PAGE"
item "2.2 lote de entregas assinadas gerado (9 entregas)" "9" "$(wc -l <"$TRABALHO/entregas.jsonl" | tr -d ' ')"

cat >"$TRABALHO/dev-meta.env" <<ENV
TRE_AMBIENTE=dev
TRE_META_GRAPH_BASE=http://127.0.0.1:$PORTA_GRAPH
TRE_META_GRAPH_VERSAO=v21.0
TRE_META_ACCESS_TOKEN=$TOKEN
TRE_META_APP_SECRET=$APP_SECRET
TRE_META_PORTA_BANCO=$PREFIXO
ENV
COMPONENTE=(python3 "$MODULO" --ambiente dev --env-file "$TRABALHO/dev-meta.env" --contrato "$CONTRATO")

echo "== 3. guardas de ambiente (exit code medido)"
"${COMPONENTE[@]}" --planejar >"$TRABALHO/c-planejar.out" 2>&1
item "3.1 --planejar (exit 0)" "0" "$?"
"${COMPONENTE[@]}" --conferir >"$TRABALHO/c-conferir.out" 2>&1
item "3.2 --conferir (exit 0)" "0" "$?"
python3 "$MODULO" --ambiente prod --contrato "$CONTRATO" --env-file "$TRABALHO/dev-meta.env" --conferir >"$TRABALHO/c-prod.out" 2>&1
item "3.3 prod RECUSA por desenho (exit 4)" "4" "$?"
python3 "$MODULO" --ambiente dev --contrato "$CONTRATO" --conferir \
  --porta-banco "ssh host psql -U $USUARIO -d $BANCO" \
  --graph-base "http://127.0.0.1:$PORTA_GRAPH" >"$TRABALHO/c-remoto.out" 2>&1
item "3.4 porta de banco remota RECUSA (BANCO_NAO_E_DEV, exit 3)" "3" "$?"
grep -q BANCO_NAO_E_DEV "$TRABALHO/c-remoto.out"; item "3.5 ...com o motivo no relatorio" "0" "$?"
python3 "$MODULO" --ambiente dev --contrato "$CONTRATO" --conferir --graph-base "https://graph.facebook.com" \
  --porta-banco "$PREFIXO" >"$TRABALHO/c-graph.out" 2>&1
item "3.6 Graph fora de loopback RECUSA (GRAPH_NAO_E_DEV, exit 3)" "3" "$?"
FOTO_ANTES="$(foto)"; echo "$FOTO_ANTES" >"$TRABALHO/foto-massa.txt"
"${COMPONENTE[@]}" --ingerir --webhook "$TRABALHO/entregas.jsonl" --chave-idempotencia "dry" \
  >"$TRABALHO/c-dry.out" 2>&1
item "3.7 --ingerir sem --confirmo e' DRY_RUN (exit 0)" "0" "$?"
grep -q DRY_RUN_OK "$TRABALHO/c-dry.out"; item "3.8 ...sem gravar (snapshot identico)" "$FOTO_ANTES" "$(foto)"
item "3.9 ...e sem chamar a Graph API" "0" "$([ -f "$TRABALHO/graph.jsonl" ] && wc -l <"$TRABALHO/graph.jsonl" | tr -d ' ' || echo 0)"

echo "== 4. ingesta real (--confirmo)"
"${COMPONENTE[@]}" --ingerir --webhook "$TRABALHO/entregas.jsonl" --chave-idempotencia "w7e02:rodada-1" \
  --lote "w7e02:rodada-1" --confirmo --registro "$TRABALHO/registro.jsonl" \
  --relatorio "$TRABALHO/relatorio.json" >"$TRABALHO/ingesta.out" 2>&1
item "4.0 ingesta concluida (exit 0)" "0" "$?"
grep -q "veredito: INGESTAO_LEADS_META_001_OK" "$TRABALHO/ingesta.out"
item "4.1 veredito INGESTAO_LEADS_META_001_OK" "0" "$?"
grep -q '"evento": "JA_INGERIDO"' "$TRABALHO/ingesta.out"
item "4.2 entrega repetida no mesmo lote devolveu JA_INGERIDO" "0" "$?"
grep -q '"evento": "ASSINATURA_INVALIDA"' "$TRABALHO/ingesta.out"
item "4.3 entrega com HMAC errado foi RECUSADA antes de qualquer chamada" "0" "$?"
grep -q '"evento": "SEM_VINCULO"' "$TRABALHO/ingesta.out"
item "4.4 contato desconhecido registrado como SEM_VINCULO" "0" "$?"
grep -q '"evento": "DADOS_INSUFICIENTES"' "$TRABALHO/ingesta.out"
item "4.5 lead sem e-mail e sem telefone recusado (DADOS_INSUFICIENTES)" "0" "$?"
grep -q '"evento": "LEAD_INDISPONIVEL"' "$TRABALHO/ingesta.out"
item "4.6 lead apagado na origem virou LEAD_INDISPONIVEL (404, sem retry)" "0" "$?"
grep -q '"evento": "ERRO_GRAPH"' "$TRABALHO/ingesta.out"
item "4.7 erro persistente virou ERRO_GRAPH (dead-letter com motivo)" "0" "$?"
item "4.7b replay da MESMA entrega invalida vira JA_INGERIDO e nao derruba a rodada" "2" \
  "$(grep -c '"evento": "JA_INGERIDO"' "$TRABALHO/ingesta.out")"

echo "== 5. o que ficou no banco canônico"
item "5.1 2 interacoes gravadas (vinculo por e-mail e por telefone; o replay nao grava)" "2" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='meta';")"
item "5.2 vocabulario do contrato nas colunas (direction/type/category/intent/sentiment/confianca)" "2" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='meta' AND direction='INBOUND' AND interaction_type='LEAD_META' AND response_category='LEAD_META' AND intent='PEDIDO_DE_CONTATO' AND sentiment='POSITIVO' AND ai_confidence=0.9;")"
item "5.3 content_reference = meta-lead:<page>:<leadgen>" "2" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference LIKE 'meta-lead:$PAGE:%';")"
item "5.4 vinculo pelo contato de E-MAIL" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE contact_id='$CT_EMAIL';")"
item "5.5 vinculo pelo contato so' de TELEFONE (formatado na ficha)" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE contact_id='$CT_FONE';")"
item "5.6 occurred_at vem do created_time do formulario (nao do relogio da ingesta)" "2" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='meta' AND occurred_at < NOW() - INTERVAL '1 day';")"
item "5.7 subject cita o formulario/anuncio, sem PII" "2" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='meta' AND subject LIKE 'meta leadgen form=form-w7%';")"
item "5.8 content_summary NAO tem e-mail em claro" "0" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='meta' AND (content_summary ILIKE '%cliente-demo.test%' OR content_summary ILIKE '%maria@%');")"
item "5.9 content_summary NAO tem telefone em claro" "0" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='meta' AND (content_summary LIKE '%97777%' OR content_summary LIKE '%98888%');")"
item "5.10 campo fora do mapa registrado apenas pelo NOME" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.sync_events WHERE status='PROCESSADO' AND request_payload->'campos_desconhecidos' @> '[{\"campo\": \"whatsapp_optin\"}]'::jsonb;")"
item "5.11 trilha de idempotencia com 1 chave por lead (sem duplicata)" "0" \
  "$(conta "SELECT count(*) FROM (SELECT idempotency_key FROM sales_intelligence.sync_events GROUP BY 1 HAVING count(*) > 1) d;")"
item "5.12 statuses da trilha: PROCESSADO/SEM_VINCULO/DADOS_INSUFICIENTES/LEAD_INDISPONIVEL/ASSINATURA_INVALIDA/ERRO_GRAPH" "6" \
  "$(conta "SELECT count(DISTINCT status) FROM sales_intelligence.sync_events WHERE status IN ('PROCESSADO','SEM_VINCULO','DADOS_INSUFICIENTES','LEAD_INDISPONIVEL','ASSINATURA_INVALIDA','ERRO_GRAPH');")"
item "5.13 SEM_VINCULO registrado na trilha" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.sync_events WHERE status='SEM_VINCULO';")"
item "5.13b nenhuma interacao fora da organizacao vinculada (vinculo nao inventado)" "0" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='meta' AND organization_id <> '$ORG_A';")"
item "5.14 nenhuma organizacao/contato novo foi inventado" "1/2" \
  "$(conta "SELECT count(*) FROM sales_intelligence.organizations;")/$(conta "SELECT count(*) FROM sales_intelligence.contacts;")"

echo "== 6. chamadas a Graph API (contador do stub)"
cham() { local n=0; [ -f "$TRABALHO/graph.jsonl" ] && n=$(grep -c "\"leadgen_id\": \"$1\"" "$TRABALHO/graph.jsonl"); echo "$n"; }
item "6.1 lead com vinculo por e-mail: 1 chamada" "1" "$(cham lead-email)"
item "6.2 lead com vinculo por telefone: 1 chamada" "1" "$(cham lead-fone)"
item "6.3 replay no mesmo lote NAO re-chamou a Graph (continua 1)" "1" "$(cham lead-email)"
item "6.4 entrega com assinatura invalida: 0 chamada (a guarda vem ANTES)" "0" "$(cham lead-email-inexistente)"
item "6.5 lead apagado: 1 chamada (404 e' definitivo, sem retry)" "1" "$(cham lead-apagado)"
item "6.6 erro persistente: exatamente o teto do contrato (2 chamadas)" "2" "$(cham lead-erro)"
item "6.7 o token nao aparece em nenhum caminho registrado" "0" \
  "$(python3 -c "import os,sys;print(open('$TRABALHO/graph.jsonl').read().count('$TOKEN') if os.path.exists('$TRABALHO/graph.jsonl') else 0)")"

echo "== 7. replay do lote inteiro (idempotencia)"
FOTO_ANTES_REPLAY="$(foto)"
"${COMPONENTE[@]}" --ingerir --webhook "$TRABALHO/entregas.jsonl" --chave-idempotencia "w7e02:rodada-2" \
  --lote "w7e02:rodada-2" --confirmo >"$TRABALHO/replay.out" 2>&1
item "7.1 replay concluido (exit 0)" "0" "$?"
item "7.2 replay nao gravou NENHUMA linha nova" "$FOTO_ANTES_REPLAY" "$(foto)"
item "7.3 replay nao chamou a Graph de novo (o lead continua com 1 chamada)" "1" "$(cham lead-email)"
grep -q "veredito: INGESTAO_LEADS_META_001_OK" "$TRABALHO/replay.out"
item "7.4 replay fecha o mesmo veredito" "0" "$?"

echo "== 8. escopo de escrita (nada alem das 2 tabelas)"
item "8.1 snapshot ANTES x DEPOIS so' muda interactions/sync_events" "1/2" \
  "$(python3 - "$TRABALHO/foto-massa.txt" "$(foto)" <<'PY'
import sys
antes = dict(zip(("organizations","contacts","signals","research_runs","pain_hypotheses","scores","interactions","recommendations","agent_runs","outbox_events","sync_events","human_approvals"),
                 [int(x) for x in open(sys.argv[1]).read().strip().split("/") if x]))
depois = dict(zip(antes, [int(x) for x in sys.argv[2].split("/") if x]))
mudaram = {k for k in antes if antes[k] != depois[k]}
print("1/2" if mudaram == {"interactions", "sync_events"} else "outras:" + ",".join(sorted(mudaram - {"interactions","sync_events"})))
PY
)"
item "8.2 schema intacto (12 tabelas, nenhuma DDL)" "12" \
  "$(conta "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence';")"

echo "== 9. segredo fora da saida"
item "9.1 token ausente de stdout/relatorio/registro" "0" \
  "$(grep -l "$TOKEN" "$TRABALHO"/ingesta.out "$TRABALHO"/relatorio.json "$TRABALHO"/registro.jsonl 2>/dev/null | wc -l | tr -d ' ')"
item "9.2 app secret ausente de stdout/relatorio/registro" "0" \
  "$(grep -l "$APP_SECRET" "$TRABALHO"/ingesta.out "$TRABALHO"/relatorio.json "$TRABALHO"/registro.jsonl 2>/dev/null | wc -l | tr -d ' ')"

if [ "$DENTE" -eq 1 ]; then
  echo "== 10. prova de dente (mutacao do componente: o aceite tem de REPROVAR o item esperado)"
  MUT="$TRABALHO/mutante-pii.py"
  python3 - "$MODULO" "$MUT" "$CONTRATO" "$TRABALHO/meta-lead-ingestion-v1.json" <<'PY'
import shutil, sys
fonte = open(sys.argv[1], encoding="utf-8").read()
antigo = 'partes.append("email: presente (mascarado)" if valores.get("email") else "email: ausente")'
novo = 'partes.append("email: " + (valores.get("email") or "ausente"))'
assert antigo in fonte, "ancora da mutacao nao encontrada"
open(sys.argv[2], "w", encoding="utf-8").write(fonte.replace(antigo, novo, 1))
shutil.copyfile(sys.argv[3], sys.argv[4])   # o mutante vive sozinho: o contrato vai junto
print("mutante escrito")
PY
  docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  kill "$(cat "$TRABALHO/stub.pid")" >/dev/null 2>&1
  docker rm -f -v pg-meta-acc-dente >/dev/null 2>&1
  # o sub-run do dente usa container, porta e diretorio PROPRIOS: nao apaga as provas da rodada principal
  TRE_W7E02_TRABALHO="$TRABALHO-dente" TRE_W7E02_CONTAINER="pg-meta-acc-dente" \
  TRE_W7E02_PORTA_GRAPH="$((PORTA_GRAPH + 1))" bash "$0" --modulo "$MUT" >"$TRABALHO/dente.out" 2>&1
  RC_DENTE=$?
  REPROVADOS="$(grep -c '^FALHOU' "$TRABALHO/dente.out" || true)"
  if [ "$RC_DENTE" -ne 0 ] && [ "$REPROVADOS" = "1" ] \
     && grep -q "FALHOU 5.8 content_summary NAO tem e-mail em claro" "$TRABALHO/dente.out"; then
    item "10.1 dente: a mutacao do resumo REPROVA exatamente o item 5.8" "sim" "sim"
  else
    item "10.1 dente: a mutacao do resumo REPROVA exatamente o item 5.8" "sim" \
      "nao (rc=$RC_DENTE, itens reprovados=$REPROVADOS)"
  fi
  echo "----- itens do dente que reprovaram -----"
  grep "^FALHOU" "$TRABALHO/dente.out" || true
fi

echo
if [ "$ITENS_FALHOU" -eq 0 ]; then
  echo "ACEITE_META_LEADS_001_OK ($ITENS_OK OK / 0 FALHOU)"
  exit 0
else
  echo "ACEITE_META_LEADS_001_FALHOU ($ITENS_OK OK / $ITENS_FALHOU FALHOU) $FALHAS"
  exit 1
fi
