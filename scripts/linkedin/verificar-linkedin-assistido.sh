#!/usr/bin/env bash
# =============================================================================================
# ACEITE — LINKEDIN ASSISTIDO v1 — card TRE-W7-E04-T01 (onda W7, epic E04)
#
# Mede, por execucao real, o que o card entrega: um workflow ASSISTIDO do canal LinkedIn em que a
# maquina PREPARA (rascunho + pedido de aprovacao) e REGISTRA o engajamento, e o HUMANO decide e
# publica. O aceite prova tambem o que a maquina NUNCA faz (publicar/comentar/reagir/seguir/convidar/
# enviar DM/mencionar/responder/agendar/automatizar navegador/usar API do LinkedIn) — cada proibicao
# com exit 5 e zero escrita de efeito.
#
# ADR-005: nada nasce em producao. O banco do aceite e' um container DESCARTAREL (`pg-lk-e04`) com a
# migration 0001 aplicada. Nao ha ponta externa: o canal LinkedIn nao tem rede neste componente (o
# aceite mede isso por grep no codigo). Os containers do ambiente (pg-sales-dev, pg-odoo-dev,
# odoo-dev, proxy-dev) NAO sao tocados: se o container do aceite ja existir, o aceite ABORTA.
#
# Veredito: ACEITE_LINKEDIN_ASSISTIDO_OK / ACEITE_LINKEDIN_ASSISTIDO_FALHOU.
# --prova-de-dente: muta uma COPIA do modulo e exige que o aceite reprove O ITEM ESPERADO.
# Exit: 0 = OK · 1 = FALHOU · 2 = uso/guarda · 3 = nao testavel · 4 = recusa de ambiente.
# =============================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_LK_CONTAINER:-pg-lk-e04}"
TRABALHO="${TRE_LK_TRABALHO:-/tmp/linkedin-assistido-e04}"
USUARIO=sales_ai
BANCO=sales_intelligence
SENHA=dev
MODULO="${TRE_LK_MODULO:-$RAIZ/hermes/agents/linkedin/linkedin_assistido.py}"
POLITICA="${TRE_LK_POLITICA:-$RAIZ/hermes/agents/linkedin/linkedin-assistido-v1.json}"
CONTRATO="$RAIZ/docs/data/data_contract_v1.json"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
DOMINIO_DEV="${TRE_LK_DOMINIO_DEV:-cliente-demo.test}"
OPERADOR="Anderson Ribeiro"

ORG_A=22222222-2222-4222-8222-222222222222   # com evidencia, contato limpo
CT_A=d2222222-2222-4222-8222-222222222222
REC_A=55555555-5555-4555-8555-555555555555
ORG_B=33333333-3333-4333-8333-333333333333   # sem evidencia nenhuma
REC_B=66666666-6666-4666-8666-666666666666
ORG_C=44444444-4444-4444-8444-444444444444   # contato com do_not_contact
CT_C=d4444444-4444-4444-8444-444444444444
REC_C=77777777-7777-4777-8777-777777777777

DENTE=0
MANTER=0
SUB_RUN=0
DENTE_ROTULO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --manter) MANTER=1 ;;
    --prova-de-dente) DENTE=1 ;;
    --dente) shift; DENTE=1; DENTE_ROTULO="${1:?--dente exige rotulo}" ;;
    --dente=*) DENTE=1; DENTE_ROTULO="${1#--dente=}" ;;
    --sub-run) SUB_RUN=1 ;;
    --modulo) shift; MODULO="${1:?--modulo exige caminho}" ;;
    --modulo=*) MODULO="${1#--modulo=}" ;;
    *) echo "uso: $0 [--manter] [--prova-de-dente] [--dente <rotulo>] [--sub-run] [--modulo <py>]"; exit 2 ;;
  esac
  shift
done

ITENS_OK=0
ITENS_FALHOU=0
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then echo "OK     $1 ($3)"; ITENS_OK=$((ITENS_OK + 1))
  else echo "FALHOU $1 (esperado=$2 obtido=$3)"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); fi
}
item_sim() { item "$1" sim "$2"; }
sim_nao() { [ "${1:-0}" != "0" ] && echo sim || echo nao; }

for c in docker python3; do
  command -v "$c" >/dev/null 2>&1 || { echo "NAO_TESTAVEL $c ausente (rode na VPS do ambiente)"; exit 3; }
done
for f in "$MODULO" "$POLITICA" "$CONTRATO" "$MIGRATION"; do
  [ -f "$f" ] || { echo "FALHOU arquivo ausente: $f"; exit 2; }
done

rm -rf "$TRABALHO"; mkdir -p "$TRABALHO"

# ---------------------------------------------------------------------------------------------
# --prova-de-dente: muta uma COPIA do modulo e exige que o aceite reprove O ITEM ESPERADO.
# ---------------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ] && [ "$SUB_RUN" -eq 0 ]; then
  echo "== prova de dente: 3 mutacoes, cada uma tem que derrubar 1 item"
  DENTES_OK=0; DENTES_TOTAL=0
  mutar() { # <rotulo> <alvo> <troca>
    MUT_ALVO="$2" MUT_TROCA="$3" MUT_SAIDA="$TRABALHO/mut-$1.py" MUT_ORIGEM="$MODULO" python3 - <<'PY'
import os, pathlib, sys
origem = pathlib.Path(os.environ["MUT_ORIGEM"]).read_text(encoding="utf-8")
alvo, troca = os.environ["MUT_ALVO"], os.environ["MUT_TROCA"]
if alvo not in origem:
    print("MUTACAO_NAO_APLICAVEL", file=sys.stderr); sys.exit(9)
pathlib.Path(os.environ["MUT_SAIDA"]).write_text(origem.replace(alvo, troca, 1), encoding="utf-8")
PY
  }
  dente() { # <rotulo> <item esperado>
    DENTES_TOTAL=$((DENTES_TOTAL + 1))
    local saida="$TRABALHO/dente-$1.out"
    TRE_LK_TRABALHO="$TRABALHO/dente-$1" bash "$0" --sub-run --modulo "$TRABALHO/mut-$1.py" >"$saida" 2>&1
    local rc=$?
    if grep -qF "FALHOU $2" "$saida" && [ "$rc" = "1" ]; then
      echo "DENTE_OK $1 — o aceite reprovou o item esperado: $2"
      DENTES_OK=$((DENTES_OK + 1))
    else
      echo "DENTE_FALHOU $1 — esperava reprovar '$2' (exit 1), veio exit=$rc:"
      grep -E "^(FALHOU|ACEITE_)" "$saida" | head -5
    fi
  }
  mutar publica '    proibidas = politica["acoes_declaradas"]["maquina_proibidas"]' \
    '    proibidas = politica["acoes_declaradas"]["maquina_proibidas"]
    return {"veredito": PLANO, "acao": acao, "publica_a_maquina": True}'
  mutar sem-aprovacao '        if item["status"] == "APPROVED":' '        if True:'
  mutar evidencia '    if total_evidencia < int(politica["evidencia"]["minima_para_preparar"]):' '    if False:'
  dente publica "9.1 publicar pela maquina RECUSA (exit 5)"
  dente sem-aprovacao "7.2 zero entregavel sem aprovacao"
  dente evidencia "5.1 sem evidencia = SEM_EVIDENCIA (exit 1, nada entregue)"
  echo "-- dentes OK=$DENTES_OK de $DENTES_TOTAL"
  [ "$DENTES_OK" = "$DENTES_TOTAL" ] && { echo "PROVA_DE_DENTE_OK"; exit 0; }
  echo "PROVA_DE_DENTE_FALHOU"; exit 1
fi

if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi

limpar() {
  local rc=$?
  if [ "$MANTER" -eq 0 ]; then docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  else echo "== --manter: $CONTAINER e $TRABALHO ficaram de pe"; fi
  exit $rc
}
trap limpar EXIT

echo "== 0. pre-flight e banco descartarel"
item_sim "0.0 modulo do workflow existe" "$([ -f "$MODULO" ] && echo sim || echo nao)"
item_sim "0.0 politica do componente existe" "$([ -f "$POLITICA" ] && echo sim || echo nao)"
item_sim "0.0 contrato de dados existe" "$([ -f "$CONTRATO" ] && echo sim || echo nao)"

docker run -d --name "$CONTAINER" -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" \
  -e "POSTGRES_DB=$BANCO" "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
PRONTO=nao
for _ in $(seq 1 90); do
  n=$(docker logs "$CONTAINER" 2>&1 | grep -c "database system is ready to accept connections")
  if [ "${n:-0}" -ge 2 ] && docker exec "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -tAc "select 1" >/dev/null 2>&1; then
    PRONTO=sim; break
  fi
  sleep 1
done
item "0.1 PostgreSQL descartarel de pe (init concluido: 2x ready)" sim "$PRONTO"
[ "$PRONTO" = sim ] || exit 2

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
conta() { psql_t -c "$1" | tr -d ' ' | head -1; }

psql_stdin < "$MIGRATION" >"$TRABALHO/migration.out" 2>&1
item "0.2 migration 0001 aplicada (12 tabelas)" "12" \
  "$(conta "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence';")"

INTOCAVEIS="organizations contacts signals research_runs pain_hypotheses scores recommendations outbox_events"
foto_intocaveis() { local s=""; for t in $INTOCAVEIS; do s="$s$(conta "SELECT count(*) FROM sales_intelligence.$t;")/"; done; echo "$s"; }

# ---------------------------------------------------------------------------------------------
echo "== 1. politica/contrato do componente"
POL="$POLITICA" CON="$CONTRATO" python3 - <<'PY' > "$TRABALHO/politica.out"
import json, os
p = json.load(open(os.environ["POL"]))
c = json.load(open(os.environ["CON"]))
proibidas = p["acoes_declaradas"]["maquina_proibidas"]
print("canal", p["canal"])
print("liberadas", len(p["acoes_declaradas"]["maquina_liberadas"]))
print("proibidas", len(proibidas))
print("todas_proibidas", int(all(a in proibidas for a in
      ["PUBLICAR", "AGENDAR_PUBLICACAO", "COMENTAR", "REAGIR", "SEGUIR", "ENVIAR_CONVITE",
       "ENVIAR_DM", "MENCIONAR", "RESPONDER_COMENTARIO", "AUTOMACAO_DE_NAVEGADOR", "USAR_API_DO_LINKEDIN"])))
print("approval_obrigatoria", int(bool(p["aprovacao_humana"]["obrigatoria"])))
print("action_type", p["aprovacao_humana"]["action_type"])
print("prepare_linkedin_no_contrato", int("PREPARE_LINKEDIN" in c["vocabularies"]["next_best_action"]))
print("status_do_vocabulario", int(all(s in c["vocabularies"]["human_approvals.status"]
      for s in p["aprovacao_humana"]["status_do_vocabulario"])))
print("tabelas_de_escrita", len(p["guarda_de_escrita"]["tabelas"]))
print("prod_recusado", int(p["ambiente"]["recusado"] == "prod"))
PY
pol() { grep -m1 "^$1 " "$TRABALHO/politica.out" | cut -d' ' -f2- ; }
item "1.1 canal do componente" "LINKEDIN" "$(pol canal)"
item "1.2 acoes que a maquina PODE executar (preparar/pedir/registrar)" "3" "$(pol liberadas)"
item "1.3 as 11 acoes humanas exclusivas estao declaradas como proibidas" "1" "$(pol todas_proibidas)"
item "1.4 aprovacao humana obrigatoria (ADR-0004)" "1" "$(pol approval_obrigatoria)"
item "1.5 action_type do pedido" "LINKEDIN_RASCUNHO" "$(pol action_type)"
item "1.6 PREPARE_LINKEDIN e' acao do vocabulario do contrato de dados" "1" "$(pol prepare_linkedin_no_contrato)"
item "1.7 status usados estao no vocabulario human_approvals.status do contrato" "1" "$(pol status_do_vocabulario)"
item "1.8 guarda de escrita: 4 tabelas" "4" "$(pol tabelas_de_escrita)"
item "1.9 prod declarado como recusado (ADR-005)" "1" "$(pol prod_recusado)"

# ---------------------------------------------------------------------------------------------
echo "== 2. massa: duas empresas com evidencia, uma sem, um contato bloqueado"
psql_stdin >/dev/null 2>&1 <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain, linkedin_url, status, created_at)
VALUES ('$ORG_A', 'Distribuidora Alfa LTDA', 'Distribuidora Alfa', '$DOMINIO_DEV',
        'https://www.linkedin.com/company/distribuidora-alfa', 'Pesquisado', NOW()),
       ('$ORG_B', 'Beta Servicos ME', 'Beta Servicos', 'beta-demo.test',
        'https://www.linkedin.com/company/beta-servicos', 'Pesquisado', NOW()),
       ('$ORG_C', 'Gama Logistica SA', 'Gama Logistica', 'gama-demo.test',
        'https://www.linkedin.com/company/gama-logistica', 'Pesquisado', NOW());
INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, job_title, decision_role,
        email, preferred_channel, linkedin_url, do_not_contact, opt_out_email, created_at)
VALUES ('$CT_A', '$ORG_A', 'Joao Souza', 'Diretor de Operacoes', 'Economic Buyer', 'joao@$DOMINIO_DEV',
        'LINKEDIN', 'https://www.linkedin.com/in/joao-souza', false, false, NOW()),
       ('$CT_C', '$ORG_C', 'Ana Lima', 'Gerente de Logistica', 'Decision Maker', 'ana@gama-demo.test',
        'LINKEDIN', 'https://www.linkedin.com/in/ana-lima', true, false, NOW());
INSERT INTO sales_intelligence.research_runs (id, organization_id, agent_name, agent_version, status,
        summary, completed_at, created_at)
VALUES (gen_random_uuid(), '$ORG_A', 'research', '1.0.0', 'COMPLETED',
        'Distribuidora com tres centros e pedidos redigitados no ERP', NOW(), NOW()),
       (gen_random_uuid(), '$ORG_C', 'research', '1.0.0', 'COMPLETED',
        'Transportadora com volume crescente na regiao', NOW(), NOW());
INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id, pain_category, pain_statement,
        status, created_at)
VALUES (gen_random_uuid(), '$ORG_A', 'PROCESSO_MANUAL',
        'Pedidos chegam por e-mail e sao redigitados no ERP', 'PARTIALLY_VALIDATED', NOW());
INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, title, description,
        detected_at, created_at)
VALUES (gen_random_uuid(), '$ORG_A', 'PROCESS_COMPLEXITY',
        'Tres centros de distribuicao sem processo unico de pedidos',
        'Operacao cresceu e o processo nao acompanhou', NOW(), NOW());
INSERT INTO sales_intelligence.recommendations (id, organization_id, contact_id, recommendation_type,
        action, description, rationale, confidence, priority, status, created_at)
VALUES ('$REC_A', '$ORG_A', '$CT_A', 'ABORDAGEM', 'PREPARE_LINKEDIN',
        'Preparar abordagem no LinkedIn para o decisor', 'Decisor alcancavel, sem e-mail valido', 0.8000, 1, 'OPEN', NOW()),
       ('$REC_B', '$ORG_B', NULL, 'ABORDAGEM', 'PREPARE_LINKEDIN',
        'Preparar abordagem no LinkedIn', 'Sem e-mail localizado', 0.5000, 2, 'OPEN', NOW()),
       ('$REC_C', '$ORG_C', '$CT_C', 'ABORDAGEM', 'PREPARE_LINKEDIN',
        'Preparar abordagem no LinkedIn', 'Decisor mapeado', 0.7000, 3, 'OPEN', NOW());
SQL
item "2.1 massa carregada (3 organizacoes)" "3" "$(conta "SELECT count(*) FROM sales_intelligence.organizations;")"
item "2.2 3 recomendacoes PREPARE_LINKEDIN OPEN" "3" \
  "$(conta "SELECT count(*) FROM sales_intelligence.recommendations WHERE action='PREPARE_LINKEDIN' AND status='OPEN';")"
# a foto da guarda de escrita e' tirada DEPOIS da massa: daqui em diante, as tabelas fora do card
# nao podem mudar (a massa e' insumo do aceite, nao efeito do componente).
FOTO_INTOCAVEIS_ANTES="$(foto_intocaveis)"

# ---------------------------------------------------------------------------------------------
echo "== 3. PREPARAR_RASCUNHO + PEDIR_APROVACAO (org com evidencia e contato limpo)"
rodar_mod() { python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" "$@"; }
jcampo() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(eval("d"+sys.argv[2]))' "$1" "$2" 2>/dev/null; }
CORR1="$(python3 -c 'import uuid;print(uuid.uuid4())')"
rodar_mod --correlation-id "$CORR1" --preparar "$REC_A" --relatorio "$TRABALHO/prep1.json" >"$TRABALHO/prep1.out" 2>&1
EXIT1=$?
item "3.0 preparar rascunho termina OK (exit 0)" "0" "$EXIT1"
item "3.1 veredito do preparo" "PEDIDO_CRIADO" "$(jcampo "$TRABALHO/prep1.json" '["veredito"]')"
PEDIDO="$(jcampo "$TRABALHO/prep1.json" '["preparos"][0]["approval_id"]')"
item "3.2 pedido gravado em human_approvals" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.human_approvals WHERE id='$PEDIDO' AND action_type='LINKEDIN_RASCUNHO';")"
item "3.3 pedido nasce PENDING (quem decide e' o humano)" "PENDING" \
  "$(conta "SELECT status FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")"
item "3.4 o pedido carrega a recomendacao de origem" "$REC_A" \
  "$(conta "SELECT proposed_action->>'recommendation_id' FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")"
item_sim "3.5 o pedido carrega texto_hash (texto aprovado e' carimbado)" \
  "$(sim_nao "$(conta "SELECT length(proposed_action->>'texto_hash') FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")")"
item_sim "3.6 o rascunho cita a evidencia lida (nao inventa fato)" \
  "$(sim_nao "$(conta "SELECT jsonb_array_length(proposed_action->'rascunho'->'evidencia_citada') FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")")"
item "3.7 publicacao declarada como exclusiva do humano no proprio pedido" "EXCLUSIVA_DO_HUMANO" \
  "$(conta "SELECT proposed_action->>'publicacao' FROM sales_intelligence.human_approvals WHERE id='$PEDIDO';")"
item "3.8 auditoria em agent_runs da rodada" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR1' AND agent_name='linkedin' AND status='PEDIDO_CRIADO';")"

echo "== 4. idempotencia: a mesma rodada nao cria segundo pedido"
ANTES_PEDIDOS="$(conta "SELECT count(*) FROM sales_intelligence.human_approvals;")"
rodar_mod --preparar "$REC_A" --relatorio "$TRABALHO/prep2.json" >/dev/null 2>&1
item "4.1 segunda rodada = JA_PEDIDO" "JA_PEDIDO" "$(jcampo "$TRABALHO/prep2.json" '["preparos"][0]["veredito"]')"
item "4.2 zero pedido novo" "$ANTES_PEDIDOS" "$(conta "SELECT count(*) FROM sales_intelligence.human_approvals;")"

echo "== 5. guarda de evidencia: sem evidencia nao existe rascunho"
rodar_mod --preparar "$REC_B" --relatorio "$TRABALHO/prepB.json" >/dev/null 2>&1
EXITB=$?
item "5.1 sem evidencia = SEM_EVIDENCIA (exit 1, nada entregue)" "SEM_EVIDENCIA" \
  "$(jcampo "$TRABALHO/prepB.json" '["preparos"][0]["motivo"]')"
item "5.2 nenhum pedido criado para a empresa sem evidencia" "0" \
  "$(conta "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id='$REC_B';")"
item "5.3 a recusa fica auditada" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE status='SEM_EVIDENCIA';")"
item "5.4 exit da rodada sem evidencia" "1" "$EXITB"

echo "== 6. guarda de contato: do_not_contact BLOQUEIA"
rodar_mod --preparar "$REC_C" --relatorio "$TRABALHO/prepC.json" >/dev/null 2>&1
EXITC=$?
item "6.1 contato com do_not_contact = BLOQUEADO" "CONTATO_BLOQUEADO" \
  "$(jcampo "$TRABALHO/prepC.json" '["preparos"][0]["motivo"]')"
item "6.2 nenhum pedido criado para o contato bloqueado" "0" \
  "$(conta "SELECT count(*) FROM sales_intelligence.human_approvals WHERE entity_id='$REC_C';")"
item "6.3 exit da rodada bloqueada" "1" "$EXITC"

# ---------------------------------------------------------------------------------------------
echo "== 7. fila: antes da aprovacao nada e' entregavel ao humano"
rodar_mod --fila --relatorio "$TRABALHO/fila1.json" >/dev/null 2>&1
item "7.1 um pedido AGUARDANDO_APROVACAO" "1" "$(jcampo "$TRABALHO/fila1.json" '["fila"]["aguardando"]')"
item "7.2 zero entregavel sem aprovacao" "0" "$(jcampo "$TRABALHO/fila1.json" '["fila"]["entregaveis"]')"
item_sim "7.3 a fila declara que a maquina nao publica" \
  "$(python3 -c 'import json,sys;print("sim" if json.load(open(sys.argv[1]))["publica_a_maquina"] is False else "nao")' "$TRABALHO/fila1.json")"

echo "== 8. decisao humana simulada pelo aceite (dono da decisao: W6-E03) e entrega ao humano"
psql_t -c "UPDATE sales_intelligence.human_approvals SET status='APPROVED', decided_at=NOW(), decided_by='$OPERADOR', decision_notes='aprovado no aceite do card TRE-W7-E04-T01' WHERE id='$PEDIDO';" >/dev/null
rodar_mod --fila --relatorio "$TRABALHO/fila2.json" >/dev/null 2>&1
item "8.1 aprovado vira ENTREGAVEL_AO_HUMANO" "ENTREGAVEL_AO_HUMANO" \
  "$(jcampo "$TRABALHO/fila2.json" '["fila"]["entregaveis_ao_humano"][0]["veredito"]')"
item_sim "8.2 o entregavel carrega o texto aprovado para o humano colar" \
  "$(sim_nao "$(jcampo "$TRABALHO/fila2.json" '["fila"]["entregaveis_ao_humano"][0]["corpo"]')")"
item_sim "8.3 o entregavel carrega o texto_hash (mesmo texto aprovado)" \
  "$(sim_nao "$(jcampo "$TRABALHO/fila2.json" '["fila"]["entregaveis_ao_humano"][0]["texto_hash"]')")"
item "8.4 zero pedido ainda aguardando depois da decisao" "0" \
  "$(jcampo "$TRABALHO/fila2.json" '["fila"]["aguardando"]')"

# ---------------------------------------------------------------------------------------------
echo "== 9. o que a maquina NUNCA faz: publicar/comentar/reagir/seguir/convidar/DM/mencionar/responder"
INTERACOES_ANTES="$(conta "SELECT count(*) FROM sales_intelligence.interactions;")"
PEDIDOS_ANTES="$(conta "SELECT count(*) FROM sales_intelligence.human_approvals;")"
rodar_mod --tentar-publicar "$PEDIDO" --relatorio "$TRABALHO/pub.json" >/dev/null 2>&1
EXITPUB=$?
item "9.1 publicar pela maquina RECUSA (exit 5)" "5" "$EXITPUB"
item "9.2 motivo da recusa de publicacao" "PUBLICACAO_HUMANA_EXCLUSIVA" \
  "$(jcampo "$TRABALHO/pub.json" '["recusa_publicacao"]["motivo"]')"
item_sim "9.3 a recusa fica auditada (agent_runs)" \
  "$(sim_nao "$(conta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE status='RECUSADA' AND output->>'motivo'='PUBLICACAO_HUMANA_EXCLUSIVA';")")"

N_RECUSAS=0
for acao in COMENTAR REAGIR SEGUIR ENVIAR_CONVITE ENVIAR_DM MENCIONAR RESPONDER_COMENTARIO AGENDAR_PUBLICACAO AUTOMACAO_DE_NAVEGADOR USAR_API_DO_LINKEDIN; do
  case "$acao" in PUBLICAR|AGENDAR_PUBLICACAO) esperado="PUBLICACAO_HUMANA_EXCLUSIVA" ;; *) esperado="ACAO_HUMANA_EXCLUSIVA" ;; esac
  rodar_mod --tentar-acao "$acao" --relatorio "$TRABALHO/acao-$acao.json" >/dev/null 2>&1
  e=$?
  v="$(jcampo "$TRABALHO/acao-$acao.json" '["recusa_acao"]["motivo"]')"
  item "9.4.$acao RECUSA com exit 5 e motivo nomeado" "$esperado" "$([ "$e" = "5" ] && echo "$v" || echo "exit=$e")"
  [ "$e" = "5" ] && [ "$v" = "$esperado" ] && N_RECUSAS=$((N_RECUSAS + 1))
done
item "9.4.b as 10 acoes humanas exclusivas RECUSADAS de fato" "10" "$N_RECUSAS"
item "9.5 nenhuma interacao foi escrita por tentativa de acao" "$INTERACOES_ANTES" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions;")"
item "9.6 nenhum pedido mudou por tentativa de acao" "$PEDIDOS_ANTES" \
  "$(conta "SELECT count(*) FROM sales_intelligence.human_approvals;")"
item "9.7 zero referencia a rede do LinkedIn no codigo (API/HTTP/navegador)" "0" \
  "$(grep -ciE 'linkedin\.com|import requests|import urllib|selenium|playwright|webdriver' "$MODULO" || true)"

echo "== 10. engajamento inbound registrado com inferencia MARCADA como inferencia"
rodar_mod --engajamento "$ORG_A" --tipo LINKEDIN_CURTIDA --direction INBOUND \
  --ocorrido-em 2026-10-03T10:00:00Z --resumo "curtiu o post sobre eficiencia operacional" \
  --intent INTERESSE --ai-confidence 0.8000 --relatorio "$TRABALHO/eng.json" >/dev/null 2>&1
item "10.1 veredito do engajamento" "ENGAJAMENTO_REGISTRADO" \
  "$(jcampo "$TRABALHO/eng.json" '["engajamento"]["veredito"]')"
item "10.2 interacao gravada no canal LINKEDIN e direcao INBOUND" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE organization_id='$ORG_A' AND channel='LINKEDIN' AND direction='INBOUND' AND interaction_type='LINKEDIN_CURTIDA';")"
item "10.3 a inferencia vem marcada com ai_confidence (inferencia nao e' fato)" "0.8000" \
  "$(conta "SELECT ai_confidence FROM sales_intelligence.interactions WHERE interaction_type='LINKEDIN_CURTIDA' ORDER BY created_at DESC LIMIT 1;")"
rodar_mod --engajamento "$ORG_A" --tipo LINKEDIN_CURTIDA --direction INBOUND \
  --ocorrido-em 2026-10-03T10:00:00Z --relatorio "$TRABALHO/eng2.json" >/dev/null 2>&1
item "10.4 repeticao do mesmo engajamento = JA_REGISTRADO (idempotente)" "JA_REGISTRADO" \
  "$(jcampo "$TRABALHO/eng2.json" '["engajamento"]["veredito"]')"
item "10.5 zero interacao duplicada" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.interactions WHERE interaction_type='LINKEDIN_CURTIDA';")"
rodar_mod --engajamento "$ORG_A" --tipo LINKEDIN_FOLLOW_EM_MASSA --ocorrido-em 2026-10-03T10:05:00Z >/dev/null 2>&1
item "10.6 tipo de engajamento fora do vocabulario declarado RECUSA (exit 1)" "1" "$?"

# ---------------------------------------------------------------------------------------------
echo "== 11. guardas finais de ambiente, escrita e segredo"
python3 "$MODULO" --ambiente prod --prefixo "$PREFIXO" --fila >/dev/null 2>&1
item "11.1 prod RECUSADO pelo modulo (exit 4, ADR-005)" "4" "$?"
python3 "$MODULO" --ambiente prod --prefixo "$PREFIXO" --tentar-publicar "$PEDIDO" >/dev/null 2>&1
item "11.2 prod recusado tambem na tentativa de publicar" "4" "$?"
item "11.3 escrita restrita: as 8 tabelas fora do card nao mudaram" "$FOTO_INTOCAVEIS_ANTES" "$(foto_intocaveis)"
VAZOU=0
for termo in senha password token secret api_key authorization; do
  if grep -qi "$termo" "$TRABALHO/prep1.json" "$TRABALHO/fila2.json" "$TRABALHO/eng.json" 2>/dev/null; then VAZOU=1; fi
done
item "11.4 nenhum termo sensivel na saida dos relatorios" "0" "$VAZOU"
CORR_DESF="$(python3 -c 'import uuid;print(uuid.uuid4())')"
rodar_mod --desfazer "$CORR_DESF" --confirmo >/dev/null 2>&1
item "11.5 o desfazer MARCA em sync_events (nao apaga e nao muda status)" "1" \
  "$(conta "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='DESFAZER';")"
item "11.6 o desfazer sem --confirmo RECUSA" "1" \
  "$(rodar_mod --desfazer "$CORR_DESF" >/dev/null 2>&1; echo $?)"

echo
echo "-- itens OK=$ITENS_OK FALHOU=$ITENS_FALHOU"
if [ "$ITENS_FALHOU" -eq 0 ]; then
  echo "ACEITE_LINKEDIN_ASSISTIDO_OK"
  exit 0
fi
echo "ACEITE_LINKEDIN_ASSISTIDO_FALHOU"
exit 1
