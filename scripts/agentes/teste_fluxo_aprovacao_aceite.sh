#!/usr/bin/env bash
# teste_fluxo_aprovacao_aceite.sh [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--raiz <dir>]
#
# ACEITE E2E do WORKFLOW DE APROVACAO HUMANA (card TRE-W6-E03-T01) — PostgreSQL descartavel.
# Roda onde existe daemon Docker (a VPS do ambiente: o container do Hermes nao tem).
# NUNCA toca `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio
# (`pg-aprovacao-acc`), aplica a migration 0001, mede e, no fim, remove o container e o diretorio.
# Se o container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O aceite e a CADEIA REAL, nao uma simulacao: o gerador do card irmao (TRE-W6-E02-T01, presente
# no repo) cria os pedidos PENDING em `human_approvals` e ESTE componente os decide.
#
# O que mede (A1..A14 de `docs/architecture/aprovacao-humana-v1.md`):
#   1. a fila le os pedidos PENDING e monta a notificacao (codigo curto, empresa, contato, acao,
#      canal, texto e os tres comandos), com marcador nenhum pendurado;
#   2. a notificacao e IDEMPOTENTE: rodar de novo nao renotifica o mesmo pedido (mesmo texto_hash);
#   3. aprovar: status APPROVED, decided_by/decided_at/decision_notes registrados e o hash do texto
#      aprovado carimbado em proposed_action.decisao;
#   4. replay do MESMO voto = JA_DECIDIDO (nada reescrito); voto DIFERENTE = RECUSA por conflito;
#   5. rejeitar: status REJECTED e o pedido NAO reabre (aprovar depois RECUSA);
#   6. editar: texto revisado validado pelo gerador irmao, aprovado, original preservado;
#   7. editar com FATO NAO SUSTENTADO (numero fora da evidencia) RECUSA e NAO grava;
#   8. operador ausente/nao autorizado/maquina RECUSAM (exit 3) sem tocar no pedido;
#   9. contato que virou opt-out DEPOIS do pedido RECUSA a aprovacao (compliance na hora da decisao);
#  10. pedido EXPIRADO nao aceita decisao (motivo proprio, sem escrita);
#  11. recomendacao fora de OPEN RECUSA a aprovacao;
#  12. --expirar: PENDING mais velho que o TTL vira EXPIRED com decided_by VAZIO; os novos ficam;
#  13. --consultar e o PORTAO: libera so o aprovado com hash conferido e contato limpo; negativo nos outros;
#  14. prod exit 4 sem escrita; guarda de escrita recusa DDL e escrita fora das duas tabelas;
#      --desfazer (dry-run x --confirmo) reabre o pedido e preserva a auditoria; nada fora das duas
#      tabelas e tocado em rodada nenhuma.
#   9. veredito ..... ACEITE_APROVACAO_001_OK / ACEITE_APROVACAO_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do modulo e exige que o aceite reprove O ITEM
#   ESPERADO de cada uma (nao basta "o aceite falhou").
#
# Variaveis: TRE_RAIZ, TRE_FIXTURE_IMAGEM (default postgres:16), TRE_APROVACAO_CONTAINER,
#            TRE_APROVACAO_TRABALHO.
# Exit: 0 = ACEITE_APROVACAO_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_APROVACAO_CONTAINER:-pg-aprovacao-acc}"
TRABALHO="${TRE_APROVACAO_TRABALHO:-/tmp/aprovacao-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="aprovacao-aceite-descartavel"
MODULO="$RAIZ/hermes/agents/outreach/approval_workflow.py"
GERADOR="$RAIZ/hermes/agents/outreach/outreach_generator.py"
POLITICA="$RAIZ/hermes/agents/outreach/politica-aprovacao-v1.json"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
OPERADOR="Anderson Ribeiro"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; MODULO="${1:?--codigo exige caminho}" ;;
    --codigo=*) MODULO="${1#--codigo=}" ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" ;;
    *) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--raiz <dir>]"; exit 2 ;;
  esac
  shift
done

ITENS_OK=0
ITENS_FALHOU=0
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then
    echo "OK     $1 ($3)"; ITENS_OK=$((ITENS_OK + 1))
  else
    echo "FALHOU $1 (esperado=$2 obtido=$3)"; ITENS_FALHOU=$((ITENS_FALHOU + 1))
  fi
}
tem() { # <texto> <arquivo> -> sim/nao
  if grep -q -- "$1" "$2"; then echo sim; else echo nao; fi
}

command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
[ -f "$MIGRATION" ] || { echo "FALHOU migration ausente: $MIGRATION"; exit 2; }
[ -f "$MODULO" ] || { echo "FALHOU modulo ausente: $MODULO"; exit 2; }
[ -f "$GERADOR" ] || { echo "FALHOU gerador irmao ausente: $GERADOR"; exit 2; }
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
mkdir -p "$TRABALHO"

limpar() {
  local rc=$?
  if [ "$MANTER" -eq 0 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1
  else
    echo "== --manter: container $CONTAINER e $TRABALHO ficaram de pe"
  fi
  exit $rc
}
trap limpar EXIT

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

# ---------------------------------------------------------------------------------------
# Container descartavel + migration + massa
# ---------------------------------------------------------------------------------------
docker run -d --name "$CONTAINER" \
  -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
  "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
for _ in $(seq 1 60); do
  # pg_isready mente no inicio (servidor temporario): a espera correta e SELECT 1 duas vezes
  psql_t -c "SELECT 1;" >/dev/null 2>&1 && psql_t -c "SELECT 1;" >/dev/null 2>&1 && break
  sleep 1
done
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }

ORG_A="11111111-1111-4111-8111-111111111111"   # SEND_EMAIL -> pedido A1: fila/aprovar/replay/conflito/consultar/desfazer
ORG_B="22222222-2222-4222-8222-222222222222"   # contato vira opt-out DEPOIS do pedido -> RECUSA na aprovacao
ORG_C="33333333-3333-4333-8333-333333333333"   # editar (revisao valida) e editar com fato inventado
ORG_D="44444444-4444-4444-8444-444444444444"   # recomendacao fora de OPEN -> RECUSA
ORG_E="55555555-5555-4555-8555-555555555555"   # pedido velho -> --expirar
ORG_G="77777777-7777-4777-8777-777777777777"   # operador nao autorizado / maquina

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, industry_name, employee_band,
        employee_count, city, state, status, created_at)
VALUES ('$ORG_A', 'Distribuidora Alfa LTDA', 'Distribuidora Alfa', 'Distribuicao B2B', '150_299', 210,
        'Campinas', 'SP', 'Qualificado', NOW()),
       ('$ORG_B', 'Industria Beta LTDA', 'Industria Beta', 'Industria', '300_499', 320,
        'Joinville', 'SC', 'Qualificado', NOW()),
       ('$ORG_C', 'Servicos Gama LTDA', 'Servicos Gama', 'Servicos B2B', '70_149', 90,
        'Sao Paulo', 'SP', 'Qualificado', NOW()),
       ('$ORG_D', 'Logistica Delta LTDA', 'Logistica Delta', 'Logistica', '150_299', 150,
        'Curitiba', 'PR', 'Qualificado', NOW()),
       ('$ORG_E', 'Comercio Epsilon LTDA', 'Comercio Epsilon', 'Distribuicao B2B', '300_499', 380,
        'Ribeirao Preto', 'SP', 'Qualificado', NOW()),
       ('$ORG_G', 'Tech Eta LTDA', 'Tech Eta', 'SaaS/Tech B2B', '150_299', 180,
        'Florianopolis', 'SC', 'Qualificado', NOW());

INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, first_name, job_title,
        decision_role, email, preferred_channel, do_not_contact, opt_out_email, created_at)
SELECT gen_random_uuid(), o.id, 'Maria Souza ' || left(o.trade_name, 3), 'Maria',
       'Diretora de Operacoes', 'Decision Maker',
       'maria.souza@' || lower(left(o.trade_name, 12)) || '.com.br', 'EMAIL', false, false, NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_C', '$ORG_D', '$ORG_E');

INSERT INTO sales_intelligence.research_runs (id, organization_id, agent_name, status, summary,
        completed_at, created_at)
SELECT gen_random_uuid(), o.id, 'scout', 'COMPLETED',
       'Empresa com tres centros de distribuicao e pedidos redigitados no ERP', NOW(), NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_C', '$ORG_D', '$ORG_E');

INSERT INTO sales_intelligence.pain_hypotheses (id, organization_id, pain_category, pain_statement,
        status, created_at)
SELECT gen_random_uuid(), o.id, 'PROCESSO_MANUAL',
       'Pedidos chegam por e-mail e sao redigitados no ERP', 'PARTIALLY_VALIDATED', NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_C', '$ORG_D', '$ORG_E');

INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, title, description,
        detected_at, relevance_score)
SELECT gen_random_uuid(), o.id, 'EFFICIENCY_PROGRAM', 'Programa de eficiencia anunciado',
       'Empresa anunciou revisao de processos internos', NOW(), 70
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_C', '$ORG_D', '$ORG_E');

INSERT INTO sales_intelligence.scores (id, organization_id, score_type, score_value, score_version,
        calculated_at)
SELECT gen_random_uuid(), o.id, 'PRIORITY', 82.5, 'priority-v1', NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_C', '$ORG_D', '$ORG_E');

INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system,
        operation, source_version, idempotency_key, status, request_payload, created_at)
SELECT gen_random_uuid(), 'organization', o.id, 'hermes', 'postgres', 'TIER', 'tiering-v1',
       'tier:TIER:' || o.id, 'SUCCESS', jsonb_build_object('tier', 'A'), NOW()
FROM sales_intelligence.organizations o
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_C', '$ORG_D', '$ORG_E');

INSERT INTO sales_intelligence.recommendations (id, organization_id, contact_id, recommendation_type,
        action, rationale, status, priority, created_at)
SELECT gen_random_uuid(), o.id, c.id, 'NEXT_BEST_ACTION', 'SEND_EMAIL',
       'tier A com decisor contactavel', 'OPEN', 1, NOW()
FROM sales_intelligence.organizations o
JOIN sales_intelligence.contacts c ON c.organization_id = o.id
WHERE o.id IN ('$ORG_A', '$ORG_B', '$ORG_C', '$ORG_D', '$ORG_E');
SQL

CONTATO_A="$(psql_t -c "SELECT id FROM sales_intelligence.contacts WHERE organization_id = '$ORG_A';" | tr -d ' ')"
CONTATO_B="$(psql_t -c "SELECT id FROM sales_intelligence.contacts WHERE organization_id = '$ORG_B';" | tr -d ' ')"
CONTATO_C="$(psql_t -c "SELECT id FROM sales_intelligence.contacts WHERE organization_id = '$ORG_C';" | tr -d ' ')"
CONTATO_E="$(psql_t -c "SELECT id FROM sales_intelligence.contacts WHERE organization_id = '$ORG_E';" | tr -d ' ')"

contar() { psql_t -c "SELECT count(*) FROM $1;" | tr -d ' '; }
# le uma coluna preservando os espacos internos (nomes e notas tem mais de uma palavra)
psql_limpo() { "${PSQL[@]}" "$@" </dev/null | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'; }
foto() { # contagem das tabelas que as rodadas NAO podem tocar
  echo "$(contar sales_intelligence.organizations)|$(contar sales_intelligence.contacts)|$(contar sales_intelligence.recommendations)|$(contar sales_intelligence.scores)|$(contar sales_intelligence.signals)|$(contar sales_intelligence.pain_hypotheses)|$(contar sales_intelligence.research_runs)|$(contar sales_intelligence.interactions)|$(contar sales_intelligence.sync_events)|$(contar sales_intelligence.outbox_events)"
}

gerar() { # <label> <org> -> cria o pedido PENDING pelo gerador do card IRMAO (cadeia real)
  local rotulo="$1" org="$2"
  python3 "$GERADOR" --ambiente dev --prefixo "$PREFIXO" --organizacao "$org" \
    --correlation-id "$(python3 -c 'import uuid;print(uuid.uuid4())')" \
    >"$TRABALHO/geracao_$rotulo.txt" 2>"$TRABALHO/geracao_$rotulo.err"
  echo $?
}

decidir() { # <label> <id> <verbo> <operador> [args extras]
  local rotulo="$1" pedido="$2" verbo="$3" operador="$4"; shift 4
  python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --decidir "$pedido" --decisao "$verbo" \
    --por "$operador" "$@" --correlation-id "$(python3 -c 'import uuid;print(uuid.uuid4())')" \
    >"$TRABALHO/decisao_$rotulo.txt" 2>"$TRABALHO/decisao_$rotulo.err"
  echo $?
}

veredito() { grep -o "\"veredito\": \"[A-Z_]*\"" "$1" | head -1 | cut -d'"' -f4; }
motivo() { grep -o "\"motivo\": \"[A-Z_]*\"" "$1" | head -1 | cut -d'"' -f4; }

# ---------------------------------------------------------------------------------------
# A0: a cadeia real — o GERADOR cria os pedidos PENDING
# ---------------------------------------------------------------------------------------
RC=$(gerar A "$ORG_A");   item "A0 gerador cria o pedido da Alfa (exit 0)" 0 "$RC"
RC=$(gerar B "$ORG_B");   item "A0 gerador cria o pedido da Beta (exit 0)" 0 "$RC"
RC=$(gerar C "$ORG_C");   item "A0 gerador cria o pedido da Gama (exit 0)" 0 "$RC"
RC=$(gerar D "$ORG_D");   item "A0 gerador cria o pedido da Delta (exit 0)" 0 "$RC"
RC=$(gerar E "$ORG_E");   item "A0 gerador cria o pedido da Epsilon (exit 0)" 0 "$RC"
item "A0 cinco pedidos PENDING foram gravados pelo gerador" "5" \
  "$(contar sales_intelligence.human_approvals)"
item "A0 todos nasceram PENDING" "5" \
  "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status = 'PENDING';" | tr -d ' ')"

PEDIDO_A="$(psql_t -c "SELECT id FROM sales_intelligence.human_approvals WHERE entity_id = '$CONTATO_A';" | tr -d ' ')"
PEDIDO_B="$(psql_t -c "SELECT id FROM sales_intelligence.human_approvals WHERE entity_id = '$CONTATO_B';" | tr -d ' ')"
PEDIDO_C="$(psql_t -c "SELECT id FROM sales_intelligence.human_approvals WHERE entity_id = '$CONTATO_C';" | tr -d ' ')"
PEDIDO_D="$(psql_t -c "SELECT id FROM sales_intelligence.human_approvals WHERE entity_id IN (SELECT id FROM sales_intelligence.contacts WHERE organization_id = '$ORG_D');" | tr -d ' ')"
PEDIDO_E="$(psql_t -c "SELECT id FROM sales_intelligence.human_approvals WHERE entity_id = '$CONTATO_E';" | tr -d ' ')"
FOTO_ANTES="$(foto)"

# ---------------------------------------------------------------------------------------
# A1: fila + notificacao (codigo curto, texto, os tres comandos) e idempotencia
# ---------------------------------------------------------------------------------------
CORR_FILA="$(python3 -c 'import uuid;print(uuid.uuid4())')"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --fila --notificacoes "$TRABALHO/notificacoes.txt" \
  --correlation-id "$CORR_FILA" >"$TRABALHO/fila1.json" 2>"$TRABALHO/fila1.err"
item "A1 --fila exit 0" 0 "$?"
item "A1 fila notifica os 5 PENDING" "NOTIFICADO" "$(veredito "$TRABALHO/fila1.json")"
item "A1 a fila ve os 5 pedidos" "5" \
  "$(psql_t -c "SELECT (output->>'pendentes')::int FROM sales_intelligence.agent_runs WHERE correlation_id = '$CORR_FILA';" | tr -d ' ')"
item "A1 notificacao traz o codigo curto do pedido" "1" \
  "$(grep -c "APR-$(echo "$PEDIDO_A" | tr -d '-' | cut -c1-8)" "$TRABALHO/notificacoes.txt")"
item "A1 notificacao traz empresa e acao" "sim" "$(tem 'Distribuidora Alfa' "$TRABALHO/notificacoes.txt")"
item "A1 notificacao traz os tres comandos de decisao" "3" \
  "$(grep -o -- '--decisao \(aprovar\|rejeitar\|editar\)' "$TRABALHO/notificacoes.txt" | sort -u | wc -l | tr -d ' ')"
item "A1 nenhum marcador pendurado na notificacao" "0" \
  "$(grep -c '{{' "$TRABALHO/notificacoes.txt")"
item "A1 notificacao identifica o pedido pelo uuid inteiro" "sim" "$(tem "$PEDIDO_A" "$TRABALHO/notificacoes.txt")"

CORR_FILA2="$(python3 -c 'import uuid;print(uuid.uuid4())')"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --fila --correlation-id "$CORR_FILA2" \
  >"$TRABALHO/fila2.json" 2>&1
item "A1 segunda fila nao renotifica" "JA_NOTIFICADO" "$(veredito "$TRABALHO/fila2.json")"
item "A1 segunda fila conta 0 novos" "0" \
  "$(psql_t -c "SELECT jsonb_array_length(output->'notificados') FROM sales_intelligence.agent_runs WHERE correlation_id = '$CORR_FILA2';" | tr -d ' ')"
item "A1 notificacao nao carrega credencial" "0" \
  "$(grep -ci 'api_key\|bearer\|senha' "$TRABALHO/notificacoes.txt")"

# ---------------------------------------------------------------------------------------
# A2: aprovar — estado, operador registrado e hash do texto carimbado
# ---------------------------------------------------------------------------------------
HASH_A="$(psql_t -c "SELECT proposed_action->>'entrada_hash' FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
RC=$(decidir A2 "$PEDIDO_A" aprovar "$OPERADOR" --nota "texto lido e aprovado")
item "A2 aprovar exit 0" 0 "$RC"
item "A2 veredito APROVADO" "APROVADO" "$(veredito "$TRABALHO/decisao_A2.txt")"
item "A2 pedido ficou APPROVED" "APPROVED" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A2 operador humano registrado" "$OPERADOR" \
  "$(psql_limpo -c "SELECT decided_by FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';")"
item "A2 nota da decisao registrada" "texto lido e aprovado" \
  "$(psql_limpo -c "SELECT decision_notes FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';")"
item "A2 decided_at preenchido" "true" \
  "$(psql_t -c "SELECT (decided_at IS NOT NULL)::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A2 hash do texto aprovado carimbado (64 hex)" "64" \
  "$(psql_t -c "SELECT length(proposed_action->'decisao'->>'texto_hash') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A2 aprovacao simples nao marca revisao" "false" \
  "$(psql_t -c "SELECT (proposed_action->'decisao'->>'revisado')::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A2 o texto do gerador NAO foi alterado" "$HASH_A" \
  "$(psql_t -c "SELECT proposed_action->>'entrada_hash' FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A2 decisao auditada em agent_runs" "DECISAO|COMPLETED|$OPERADOR" \
  "$(psql_limpo -c "SELECT (output->>'evento') || '|' || status || '|' || (output->'resultado'->>'decidido_por') FROM sales_intelligence.agent_runs WHERE output->'resultado'->>'pedido_id' = '$PEDIDO_A';")"
item "A2 nada fora das duas tabelas foi tocado" "$FOTO_ANTES" "$(foto)"

# ---------------------------------------------------------------------------------------
# A3: replay do mesmo voto vs CONFLITO de voto
# ---------------------------------------------------------------------------------------
RC=$(decidir A3 "$PEDIDO_A" aprovar "$OPERADOR")
item "A3 replay do mesmo voto exit 0" 0 "$RC"
item "A3 replay e JA_DECIDIDO (nada reescrito)" "JA_DECIDIDO" "$(veredito "$TRABALHO/decisao_A3.txt")"
item "A3 replay nao muda o status" "APPROVED" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A3 replay nao muda a nota" "texto lido e aprovado" \
  "$(psql_limpo -c "SELECT decision_notes FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';")"
RC=$(decidir A3B "$PEDIDO_A" rejeitar "$OPERADOR")
item "A3 voto diferente RECUSA (exit 1)" 1 "$RC"
item "A3 voto diferente RECUSA por conflito" "CONFLITO_DE_VOTO" "$(motivo "$TRABALHO/decisao_A3B.txt")"
item "A3 conflito nao reescreve o pedido" "APPROVED" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A3 recusa por conflito fica auditada" "REJECTED|CONFLITO_DE_VOTO" \
  "$(psql_t -c "SELECT status || '|' || (error->>'motivo') FROM sales_intelligence.agent_runs WHERE error->>'motivo' = 'CONFLITO_DE_VOTO';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A4: o portao do envio (--consultar)
# ---------------------------------------------------------------------------------------
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --consultar "$PEDIDO_A" >"$TRABALHO/consulta_A.json" 2>&1
item "A4 portao libera o pedido aprovado" "true" \
  "$(python3 -c 'import json,sys;print(str(json.load(open(sys.argv[1]))["consulta"]["pode_enviar"]).lower())' "$TRABALHO/consulta_A.json")"
item "A4 portao devolve o texto aprovado" "true" \
  "$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]))["consulta"];print(str(len(d["texto"]["corpo"])>10).lower())' "$TRABALHO/consulta_A.json")"
item "A4 portao devolve o hash do texto" "$(psql_t -c "SELECT proposed_action->'decisao'->>'texto_hash' FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')" \
  "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["consulta"]["texto_hash"])' "$TRABALHO/consulta_A.json")"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --consultar "$PEDIDO_C" >"$TRABALHO/consulta_C.json" 2>&1
item "A4 portao NEGA o pedido ainda PENDING" "false" \
  "$(python3 -c 'import json,sys;print(str(json.load(open(sys.argv[1]))["consulta"]["pode_enviar"]).lower())' "$TRABALHO/consulta_C.json")"

# ---------------------------------------------------------------------------------------
# A5: rejeitar — e o pedido recusado nao reabre
# ---------------------------------------------------------------------------------------
RC=$(decidir A5 "$PEDIDO_B" rejeitar "$OPERADOR" --nota "nao e o momento")
item "A5 rejeitar exit 0" 0 "$RC"
item "A5 pedido ficou REJECTED" "REJECTED" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_B';" | tr -d ' ')"
RC=$(decidir A5B "$PEDIDO_B" aprovar "$OPERADOR")
item "A5 pedido rejeitado NAO reabre (exit 1)" 1 "$RC"
item "A5 motivo PEDIDO_JA_REJEITADO" "PEDIDO_JA_REJEITADO" "$(motivo "$TRABALHO/decisao_A5B.txt")"

# ---------------------------------------------------------------------------------------
# A6: compliance na hora da decisao (contato virou opt-out depois do pedido)
# ---------------------------------------------------------------------------------------
psql_stdin >/dev/null <<SQL
UPDATE sales_intelligence.contacts SET opt_out_email = true WHERE id = '$CONTATO_C';
SQL
RC=$(decidir A6 "$PEDIDO_C" aprovar "$OPERADOR")
item "A6 aprovar contato que virou opt-out RECUSA (exit 1)" 1 "$RC"
item "A6 motivo CONTATO_BLOQUEADO" "CONTATO_BLOQUEADO" "$(motivo "$TRABALHO/decisao_A6.txt")"
item "A6 recusa de compliance nada escreve no pedido" "PENDING|" \
  "$(psql_t -c "SELECT status || '|' || COALESCE(decided_by,'') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"
psql_stdin >/dev/null <<SQL
UPDATE sales_intelligence.contacts SET opt_out_email = false WHERE id = '$CONTATO_C';
SQL

# ---------------------------------------------------------------------------------------
# A7: operador (ausente, nao autorizado, maquina) — exit 3 e nada escrito
# ---------------------------------------------------------------------------------------
RC=$(decidir A7 "$PEDIDO_C" aprovar "")
item "A7 sem operador RECUSA (exit 3)" 3 "$RC"
item "A7 a recusa nomeia OPERADOR_AUSENTE" "1" "$(grep -c 'OPERADOR_AUSENTE' "$TRABALHO/decisao_A7.err")"
RC=$(decidir A7B "$PEDIDO_C" aprovar "Ze da Silva")
item "A7 operador nao autorizado RECUSA (exit 3)" 3 "$RC"
item "A7 a recusa nomeia OPERADOR_NAO_AUTORIZADO" "1" "$(grep -c 'OPERADOR_NAO_AUTORIZADO' "$TRABALHO/decisao_A7B.err")"
RC=$(decidir A7C "$PEDIDO_C" aprovar "agente-hermes")
item "A7 nome de maquina RECUSA (exit 3)" 3 "$RC"
item "A7 a recusa nomeia OPERADOR_NAO_HUMANO" "1" "$(grep -c 'OPERADOR_NAO_HUMANO' "$TRABALHO/decisao_A7C.err")"
item "A7 nenhuma das recusas de operador escreveu no pedido" "PENDING|" \
  "$(psql_t -c "SELECT status || '|' || COALESCE(decided_by,'') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A8: editar — revisao valida aprovada e revisao com FATO INVENTADO recusada
# ---------------------------------------------------------------------------------------
psql_t -c "SELECT json_build_object('assunto', proposed_action->>'assunto', 'corpo', proposed_action->>'corpo', 'cta', proposed_action->>'cta') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" \
  > "$TRABALHO/texto_original_C.json"
python3 - "$TRABALHO/texto_original_C.json" "$TRABALHO/edicao_valida.json" "$TRABALHO/edicao_inventada.json" <<'PY'
import json, sys
original = json.load(open(sys.argv[1], encoding="utf-8"))
valida = dict(original)
valida["corpo"] = original["corpo"] + "\n\n(revisto pelo operador antes de enviar)"
inventada = dict(original)
inventada["corpo"] = original["corpo"] + "\n\nGarantimos reducao de 37% do retrabalho."
json.dump(valida, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
json.dump(inventada, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False)
PY
HASH_ORIG_C="$(psql_t -c "SELECT COALESCE(proposed_action->>'entrada_hash','') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"

RC=$(decidir A8 "$PEDIDO_C" editar "$OPERADOR" --edicao "$TRABALHO/edicao_inventada.json" --nota "so ajustei o texto")
item "A8 edicao com fato inventado RECUSA (exit 1)" 1 "$RC"
item "A8 motivo EDICAO_INVALIDA" "EDICAO_INVALIDA" "$(motivo "$TRABALHO/decisao_A8.txt")"
item "A8 a edicao recusada aponta o numero sem sustentacao" "1" \
  "$(grep -c 'numero sem sustentacao\|afirmacao proibida' "$TRABALHO/decisao_A8.txt")"
item "A8 edicao recusada nao grava decisao" "PENDING|" \
  "$(psql_t -c "SELECT status || '|' || COALESCE(decided_by,'') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"

RC=$(decidir A8B "$PEDIDO_C" editar "$OPERADOR" --edicao "$TRABALHO/edicao_valida.json" --nota "revisto")
item "A8 edicao valida exit 0" 0 "$RC"
item "A8 veredito EDITADO" "EDITADO" "$(veredito "$TRABALHO/decisao_A8B.txt")"
item "A8 edicao aprovada ficou APPROVED" "APPROVED" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"
item "A8 a revisao esta marcada" "true" \
  "$(psql_t -c "SELECT (proposed_action->'decisao'->>'revisado')::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"
item "A8 o texto ORIGINAL ficou preservado" "nao" \
  "$(psql_limpo -c "SELECT CASE WHEN proposed_action->'decisao'->'texto_original'->>'corpo' LIKE '%revisto pelo operador%' THEN 'sim' ELSE 'nao' END FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';")"
item "A8 o texto revisado esta no pedido" "true" \
  "$(psql_t -c "SELECT (proposed_action->>'corpo' LIKE '%revisto pelo operador%')::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"
item "A8 o hash mudou com a revisao" "true" \
  "$(psql_t -c "SELECT (proposed_action->'decisao'->>'texto_hash' <> proposed_action->'decisao'->>'texto_hash_original')::text FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"
item "A8 o portao libera o texto REVISADO (hash confere)" "true" \
  "$(python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --consultar "$PEDIDO_C" | python3 -c 'import json,sys;print(str(json.load(sys.stdin)["consulta"]["pode_enviar"]).lower())')"
item "A8 o gerador irmao validou a edicao (registro da revisao)" "politica-outreach-v1" \
  "$(psql_t -c "SELECT (proposed_action->'decisao'->'validacao'->>'politica_irma') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ' | cut -d'@' -f1)"
item "A8 a edicao nao alterou o entrada_hash do gerador" "$HASH_ORIG_C" \
  "$(psql_t -c "SELECT proposed_action->>'entrada_hash' FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_C';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A9: recomendacao fora de OPEN RECUSA a aprovacao
# ---------------------------------------------------------------------------------------
psql_stdin >/dev/null <<SQL
UPDATE sales_intelligence.recommendations SET status = 'SUPERSEDED'
 WHERE organization_id = '$ORG_D';
SQL
RC=$(decidir A9 "$PEDIDO_D" aprovar "$OPERADOR")
item "A9 recomendacao SUPERSEDED RECUSA (exit 1)" 1 "$RC"
item "A9 motivo RECOMENDACAO_NAO_ABERTA" "RECOMENDACAO_NAO_ABERTA" "$(motivo "$TRABALHO/decisao_A9.txt")"
item "A9 pedido segue PENDING" "PENDING" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_D';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A10: --expirar (TTL) — decided_by vazio, os novos ficam
# ---------------------------------------------------------------------------------------
# a Delta volta a OPEN (a recusa do A9 foi medida) e o pedido da Epsilon envelhece 100h > TTL de 72h
psql_stdin >/dev/null <<SQL
UPDATE sales_intelligence.recommendations SET status = 'OPEN' WHERE organization_id = '$ORG_D';
UPDATE sales_intelligence.human_approvals SET requested_at = NOW() - INTERVAL '100 hours'
 WHERE id = '$PEDIDO_E';
SQL
contar_h() { psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status = 'PENDING';" | tr -d ' '; }
contar_h_exp() { psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status = 'EXPIRED';" | tr -d ' '; }
PENDENTES_ANTES="$(contar_h)"
EXPIRADOS_ANTES="$(contar_h_exp)"
CORR_EXP="$(python3 -c 'import uuid;print(uuid.uuid4())')"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --expirar --correlation-id "$CORR_EXP" \
  >"$TRABALHO/expirar.json" 2>"$TRABALHO/expirar.err"
item "A10 --expirar exit 0" 0 "$?"
item "A10 --expirar marca 1 pedido vencido" "1" \
  "$(psql_t -c "SELECT jsonb_array_length(output->'pedidos') FROM sales_intelligence.agent_runs WHERE correlation_id = '$CORR_EXP';" | tr -d ' ')"
item "A10 o pedido velho virou EXPIRED" "$((EXPIRADOS_ANTES + 1))" "$(contar_h_exp)"
item "A10 a expiracao NAO inventa operador" "" \
  "$(psql_t -c "SELECT COALESCE(decided_by,'') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_E';" | tr -d ' ')"
item "A10 a expiracao registra o motivo e o TTL" "EXPIRADO_POR_TTL:72h" \
  "$(psql_t -c "SELECT decision_notes FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_E';" | tr -d ' ')"
item "A10 os pedidos novos continuam PENDING" "$((PENDENTES_ANTES - 1))" "$(contar_h)"
item "A10 o pedido ja decidido nao foi afetado" "APPROVED" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A10 a expiracao e auditada como evento proprio" "EXPIRACAO|COMPLETED" \
  "$(psql_t -c "SELECT (output->>'evento') || '|' || status FROM sales_intelligence.agent_runs WHERE correlation_id = '$CORR_EXP';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A11: pedido EXPIRADO nao aceita decisao (nem aprovacao, nem rejeicao)
# ---------------------------------------------------------------------------------------
RC=$(decidir A11 "$PEDIDO_E" aprovar "$OPERADOR")
item "A11 pedido EXPIRED nao aceita aprovacao (exit 1)" 1 "$RC"
item "A11 motivo PEDIDO_EXPIRADO" "PEDIDO_EXPIRADO" "$(motivo "$TRABALHO/decisao_A11.txt")"
RC=$(decidir A11B "$PEDIDO_E" rejeitar "$OPERADOR")
item "A11 motivo PEDIDO_EXPIRADO tambem na rejeicao" "PEDIDO_EXPIRADO" "$(motivo "$TRABALHO/decisao_A11B.txt")"
item "A11 o pedido expirado segue EXPIRED e sem operador" "EXPIRED|" \
  "$(psql_t -c "SELECT status || '|' || COALESCE(decided_by,'') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_E';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A12: ambiente, guarda de escrita e uso
# ---------------------------------------------------------------------------------------
RC=$(python3 "$MODULO" --ambiente prod --prefixo "$PREFIXO" --fila >/dev/null 2>&1; echo $?)
item "A12 prod recusado (exit 4)" 4 "$RC"
item "A12 prod recusado NAO escreveu nada" "0" \
  "$(psql_t -c "SELECT count(*) FROM sales_intelligence.agent_runs WHERE workflow = 'outbound-aprovacao' AND output->>'evento' = 'NOTIFICACAO' AND status = 'FAILED';" | tr -d ' ')"
RC=$(python3 "$MODULO" --planejar >/dev/null 2>&1; echo $?)
item "A12 --planejar exit 0" 0 "$RC"
echo "ALTER TABLE sales_intelligence.scores ADD COLUMN x int;" > "$TRABALHO/ddl.sql"
python3 - "$MODULO" "$TRABALHO/ddl.sql" > "$TRABALHO/ddl_out.txt" 2>&1 <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("m", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sql = open(sys.argv[2]).read()
try:
    m.validar_sql(sql); print("ACEITOU")
except m.RecusaDeEscrita as r:
    print("RECUSOU", r)
PY
item "A12 guarda de escrita recusa DDL" "RECUSOU" "$(cut -d' ' -f1 "$TRABALHO/ddl_out.txt")"
python3 - "$MODULO" > "$TRABALHO/fora_out.txt" 2>&1 <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("m", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
try:
    m.validar_sql("UPDATE sales_intelligence.recommendations SET status = 'X' WHERE id = 'y';"); print("ACEITOU")
except m.RecusaDeEscrita:
    print("RECUSOU")
PY
item "A12 guarda de escrita recusa escrita fora das duas tabelas" "RECUSOU" "$(cat "$TRABALHO/fora_out.txt")"

# ---------------------------------------------------------------------------------------
# A13: desfazer (dry-run x --confirmo) — reabre o pedido e preserva a auditoria
# ---------------------------------------------------------------------------------------
CORR_DESF="$(python3 -c 'import uuid;print(uuid.uuid4())')"
RC=$(decidir desf "$PEDIDO_C" rejeitar "$OPERADOR")
item "A13 segundo voto diferente RECUSA (conflito)" "CONFLITO_DE_VOTO" "$(motivo "$TRABALHO/decisao_desf.txt")"
APROVADOS_ANTES="$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status = 'APPROVED';" | tr -d ' ')"

CORR_A2="$(psql_t -c "SELECT correlation_id FROM sales_intelligence.agent_runs WHERE output->'resultado'->>'pedido_id' = '$PEDIDO_A' AND output->'resultado'->>'veredito' = 'APROVADO' LIMIT 1;" | tr -d ' ')"
DRY="$(python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CORR_A2" 2>&1 | grep -o '"seriam_revertidos": [0-9]*' | grep -o '[0-9]*')"
item "A13 dry-run informa o que seria revertido (1)" 1 "$DRY"
item "A13 dry-run nao reverte" "APPROVED" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CORR_A2" --confirmo --por "$OPERADOR" \
  >"$TRABALHO/desfazer_sem_motivo.json" 2>"$TRABALHO/desfazer_sem_motivo.err"
item "A13 --confirmo sem --motivo RECUSA" "MOTIVO_AUSENTE" \
  "$(grep -o 'MOTIVO_AUSENTE' "$TRABALHO/desfazer_sem_motivo.err" | head -1)"
python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --desfazer "$CORR_A2" --confirmo \
  --por "$OPERADOR" --motivo "aprovacao registrada por engano" >"$TRABALHO/desfazer.json" 2>&1
item "A13 --confirmo reverte a decisao" "DESFEITO" "$(veredito "$TRABALHO/desfazer.json")"
item "A13 o pedido voltou a PENDING" "PENDING" \
  "$(psql_t -c "SELECT status FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A13 decided_by voltou a ficar vazio" "" \
  "$(psql_t -c "SELECT COALESCE(decided_by,'') FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';" | tr -d ' ')"
item "A13 a reversao fica registrada" "REVERTIDO_POR:$OPERADOR:aprovacao registrada por engano" \
  "$(psql_limpo -c "SELECT decision_notes FROM sales_intelligence.human_approvals WHERE id = '$PEDIDO_A';")"
item "A13 o portao volta a NEGAR o pedido revertido" "false" \
  "$(python3 "$MODULO" --ambiente dev --prefixo "$PREFIXO" --consultar "$PEDIDO_A" | python3 -c 'import json,sys;print(str(json.load(sys.stdin)["consulta"]["pode_enviar"]).lower())')"
item "A13 a auditoria da rodada revertida permanece" "1" \
  "$(psql_t -c "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id = '$CORR_A2';" | tr -d ' ')"
item "A13 aprovados voltaram ao numero anterior (o revertido saiu)" "$((APROVADOS_ANTES - 1))" \
  "$(psql_t -c "SELECT count(*) FROM sales_intelligence.human_approvals WHERE status = 'APPROVED';" | tr -d ' ')"

# ---------------------------------------------------------------------------------------
# A14: escopo global — nada fora das duas tabelas em rodada nenhuma
# ---------------------------------------------------------------------------------------
item "A14 nada fora das duas tabelas foi tocado" "$FOTO_ANTES" "$(foto)"
item "A14 nenhum envio registrado (interactions intacta)" "0" "$(contar sales_intelligence.interactions)"
item "A14 nenhum evento de outbox criado" "0" "$(contar sales_intelligence.outbox_events)"
item "A14 o gerador irmao nao foi usado como escrita de pedido novo" "5" "$(contar sales_intelligence.human_approvals)"

echo "---"
if [ "$ITENS_FALHOU" -eq 0 ]; then
  echo "RESULTADO: ACEITE_APROVACAO_001_OK ($ITENS_OK OK / 0 FALHOU)"
  RC_FINAL=0
else
  echo "RESULTADO: ACEITE_APROVACAO_001_FALHOU ($ITENS_OK OK / $ITENS_FALHOU FALHOU)"
  RC_FINAL=1
fi

# ---------------------------------------------------------------------------------------
# Prova de dente: a mutacao tem de reprovar O ITEM ESPERADO
# ---------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente"
  mutar() { # <arquivo origem> <destino> <de> <para>
    python3 - "$1" "$2" "$3" "$4" <<'PY'
import sys
texto = open(sys.argv[1], encoding="utf-8").read()
if sys.argv[3] not in texto:
    raise SystemExit(f"ancora nao encontrada: {sys.argv[3]!r}")
open(sys.argv[2], "w", encoding="utf-8").write(texto.replace(sys.argv[3], sys.argv[4], 1))
PY
  }
  rodar_dente() { # <nome> <item esperado>
    local nome="$1" esperado="$2"
    if grep -q "FALHOU $esperado" "$TRABALHO/dente_$nome.out"; then
      echo "OK     dente $nome -> reprovou '$esperado'"
    else
      echo "FALHOU dente $nome -> NAO reprovou '$esperado'"; ITENS_FALHOU=$((ITENS_FALHOU + 1))
    fi
  }
  mkdir -p "$TRABALHO/dentes"
  mutar "$MODULO" "$TRABALHO/dentes/sem_guarda.py" \
    "    return sorted({b for b in bloqueios if pedido.get(b)})" "    return []"
  TRE_APROVACAO_CONTAINER="pg-aprovacao-dente1" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/sem_guarda.py" \
    > "$TRABALHO/dente_guarda.out" 2>&1
  rodar_dente "guarda" "A6 aprovar contato que virou opt-out RECUSA (exit 1)"
  mutar "$MODULO" "$TRABALHO/dentes/sem_validacao.py" \
    "    if problemas:\n        raise RecusaDeDecisao(MOTIVO_EDICAO_INVALIDA" \
    "    if False:\n        raise RecusaDeDecisao(MOTIVO_EDICAO_INVALIDA"
  TRE_APROVACAO_CONTAINER="pg-aprovacao-dente2" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/sem_validacao.py" \
    > "$TRABALHO/dente_validacao.out" 2>&1
  rodar_dente "validacao" "A8 edicao com fato inventado RECUSA (exit 1)"
  mutar "$MODULO" "$TRABALHO/dentes/sem_transicao.py" \
    '        if pedido.get("status") != status_pendente:' "        if False:"
  TRE_APROVACAO_CONTAINER="pg-aprovacao-dente3" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/sem_transicao.py" \
    > "$TRABALHO/dente_transicao.out" 2>&1
  rodar_dente "transicao" "A11 pedido EXPIRED nao aceita aprovacao (exit 1)"
  mutar "$MODULO" "$TRABALHO/dentes/sem_replay.py" \
    '            if pedido.get("status") == novo_status and (pedido.get("decided_by") or "").casefold() == operador.casefold():' \
    "            if False:"
  TRE_APROVACAO_CONTAINER="pg-aprovacao-dente4" bash "$0" --raiz "$RAIZ" --codigo "$TRABALHO/dentes/sem_replay.py" \
    > "$TRABALHO/dente_replay.out" 2>&1
  rodar_dente "replay" "A3 replay e JA_DECIDIDO (nada reescrito)"
fi

exit $RC_FINAL
