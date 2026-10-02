#!/usr/bin/env bash
# teste_tiering_aceite.sh [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--raiz <dir>]
#
# ACEITE E2E do TIERING v1 (card TRE-W5-E06-T01) — PostgreSQL descartavel.
#
# Roda onde existe daemon Docker (a VPS do ambiente: o container do Hermes nao tem). NUNCA toca
# `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio
# (`pg-tier-acc`), aplica a migration 0001, mede e, no fim, remove o container e o diretorio de
# trabalho. Se o container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (A1..A9 de `docs/architecture/score-tiering-v1.md`):
#   1. a classificacao LEGITIMA e registrada (operation=TIER em sync_events) com tier, faixa,
#      faixas vigentes e a identidade do PRIORITY lido; o tier e o da faixa do contrato;
#   2. a rodada NAO toca `scores` (o tier nao e score) nem `organizations`;
#   3. `prod` e recusado (exit 4) SEM escrita; `--planejar` e `--faixas` nao abrem conexao;
#   4. replay da MESMA entrada nao duplica; PRIORITY NOVO gera REGISTRO NOVO (historico preservado);
#   5. empresa SEM PRIORITY RECUSA (SEM_PRIORITY) e nao registra nada (Nurture nao e default);
#   6. PRIORITY VENCIDO RECUSA; o vencimento e LIDO do banco, nao suposto;
#   7. o tier e do ULTIMO PRIORITY, nao da media do historico;
#   8. empresa inexistente e RECUSADA sem registrar;
#   9. desfazer dry-run nao apaga; `--confirmo` apaga SO o registro da rodada (auditoria fica);
#  10. veredito ..... ACEITE_TIERING_001_OK / ACEITE_TIERING_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do modulo e exige que o aceite reprove O ITEM
#   ESPERADO de cada uma (nao basta "o aceite falhou").
#
# Variaveis: TRE_RAIZ, TRE_FIXTURE_IMAGEM (default postgres:16), TRE_TIERING_CONTAINER,
#            TRE_TIERING_TRABALHO.
# Exit: 0 = ACEITE_TIERING_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_TIERING_CONTAINER:-pg-tier-acc}"
TRABALHO="${TRE_TIERING_TRABALHO:-/tmp/tiering-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="tiering-aceite-descartavel"
MODULO="$RAIZ/hermes/scores/tiering/tiering.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
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

command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
[ -f "$MIGRATION" ] || { echo "FALHOU migration ausente: $MIGRATION"; exit 2; }
[ -f "$MODULO" ] || { echo "FALHOU modulo ausente: $MODULO"; exit 2; }
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
mkdir -p "$TRABALHO"

limpar() {
  local rc=$?
  if [ "$MANTER" -eq 0 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1
    rm -rf "$TRABALHO"
  else
    echo "== --manter: container $CONTAINER e $TRABALHO ficaram de pe"
  fi
  exit $rc
}
trap limpar EXIT

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }

# ---------------------------------------------------------------------------------------
# Container descartavel + migration + massa (empresas, PRIORITY e um score irmao de prova)
# ---------------------------------------------------------------------------------------
docker run -d --name "$CONTAINER" \
  -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
  "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
for _ in $(seq 1 60); do
  psql_t -c "SELECT 1;" >/dev/null 2>&1 && psql_t -c "SELECT 1;" >/dev/null 2>&1 && break
  sleep 1
done
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }

ORG_A="11111111-1111-4111-8111-111111111111"   # PRIORITY 86.45 -> A
ORG_B="22222222-2222-4222-8222-222222222222"   # PRIORITY antigo 40.00 e novo 92.00 -> A+ (ultimo)
ORG_C="33333333-3333-4333-8333-333333333333"   # PRIORITY 55.00 VENCIDO -> recusa
ORG_D="44444444-4444-4444-8444-444444444444"   # sem PRIORITY -> recusa
ORG_E="55555555-5555-4555-8555-555555555555"   # PRIORITY 65.00 -> B (fronteira)
ORG_FANTASMA="99999999-9999-4999-8999-999999999999"
CID1="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CID2="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, status, created_at)
VALUES ('$ORG_A', 'Distribuidora A LTDA', 'A', 'Pesquisado', NOW()),
       ('$ORG_B', 'Industria B LTDA', 'B', 'Pesquisado', NOW()),
       ('$ORG_C', 'Servicos C LTDA', 'C', 'Pesquisado', NOW()),
       ('$ORG_D', 'Comercio D LTDA', 'D', 'Pesquisado', NOW()),
       ('$ORG_E', 'Logistica E LTDA', 'E', 'Pesquisado', NOW());
SQL

priority() { # <org> <valor> <dias_atras> <dias_de_validade|NULL>
  local validade="NULL"
  [ "$4" != "NULL" ] && validade="NOW() + INTERVAL '$4 days'"
  psql_t -c "INSERT INTO sales_intelligence.scores
      (id, organization_id, score_type, score_value, score_version, inputs, explanation, calculated_at, valid_until)
    VALUES (gen_random_uuid(), '$1', 'PRIORITY', $2, 'priority-v1', '{}'::jsonb, '{}'::jsonb,
            NOW() - INTERVAL '$3 days', $validade);" >/dev/null
}
# score de OUTRO tipo: prova que o tier nao toca score nenhum (nem o proprio PRIORITY)
irmao() {
  psql_t -c "INSERT INTO sales_intelligence.scores
      (id, organization_id, score_type, score_value, score_version, inputs, explanation, calculated_at)
    VALUES (gen_random_uuid(), '$1', 'ICP', 70.00, 'icp-v1', '{}'::jsonb, '{}'::jsonb, NOW() - INTERVAL '1 day');" >/dev/null
}

priority "$ORG_A" 86.45 1 30
priority "$ORG_B" 40.00 5 30
priority "$ORG_B" 92.00 1 30
priority "$ORG_C" 55.00 1 -1
priority "$ORG_E" 65.00 1 30
irmao "$ORG_A"

conta() { psql_t -c "$1" | head -1; }
rodar() { # <cid> <args...>
  local cid="$1"; shift
  ( cd "$RAIZ" && python3 "$MODULO" --ambiente dev --correlation-id "$cid" \
      --prefixo "$PREFIXO" --raiz "$RAIZ" "$@" ) 2>&1
}

SCORES_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")
# Impressao digital dos scores: contagem + soma + versao (a rodada nao pode mexer em NENHUM)
DIGITAL_ANTES=$(conta "SELECT md5(string_agg(score_type || ':' || score_value || ':' || score_version, ',' ORDER BY id)) FROM sales_intelligence.scores;")
ORGS_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.organizations;")

# ---------------------------------------------------------------------------------------
# A1/A2 — rodada 1: classificacao registrada e nada tocado em scores/organizations
# ---------------------------------------------------------------------------------------
SAIDA1=$(rodar "$CID1" --organizacao "$ORG_A" --relatorio "$TRABALHO/rodada1.json")
RC1=$?
echo "$SAIDA1" | sed 's/^/  /'
item "A1 rodada1 exit 0" 0 "$RC1"
item "A1 veredito CLASSIFICADO" 1 "$(echo "$SAIDA1" | grep -c '"gravados": 1')"
item "A1 um registro TIER para a empresa" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 tier A na saida" 1 "$(echo "$SAIDA1" | grep -c 'tier=A ')"
item "A1 tier no request_payload" "A" "$(conta "SELECT request_payload->>'tier' FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 faixa gravada com os limites do contrato" "80.0|89.99" "$(conta "SELECT request_payload->'faixa'->>'min', request_payload->'faixa'->>'max' FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 cinco faixas vigentes no registro" 5 "$(conta "SELECT jsonb_array_length(request_payload->'faixas_vigentes') FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 tier_version no registro" "tiering-v1" "$(conta "SELECT source_version FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 fonte das faixas declarada (scores.tiers)" 1 "$(conta "SELECT CASE WHEN request_payload->>'fonte_das_faixas' LIKE '%scores.tiers%' THEN 1 ELSE 0 END FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 identidade do PRIORITY lido no payload" 1 "$(conta "SELECT CASE WHEN request_payload->'score_lido'->>'score_id' IS NOT NULL THEN 1 ELSE 0 END FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 entrada_hash no payload" 1 "$(conta "SELECT CASE WHEN request_payload->>'entrada_hash' <> '' THEN 1 ELSE 0 END FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A1 sem LLM (tokens nulos na auditoria)" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1' AND (model IS NOT NULL OR tokens_input IS NOT NULL OR estimated_cost IS NOT NULL);")"
item "A2 a rodada nao escreveu em scores" "$SCORES_ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"
item "A2 digital dos scores intacta (valor+versao)" "$DIGITAL_ANTES" "$(conta "SELECT md5(string_agg(score_type || ':' || score_value || ':' || score_version, ',' ORDER BY id)) FROM sales_intelligence.scores;")"
item "A2 nenhum score_type TIER nasceu" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE score_type='TIER';")"
item "A2 organizations intactas" "$ORGS_ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.organizations;")"
item "A9 agent_runs com a rodada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
item "A9 agent_runs status COMPLETED" "COMPLETED" "$(conta "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
item "A9 registro fechado (PROCESSED)" "PROCESSED" "$(conta "SELECT status FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"

# ---------------------------------------------------------------------------------------
# A3 — prod recusado sem escrita; --planejar e --faixas sem conexao
# ---------------------------------------------------------------------------------------
cd "$RAIZ" && python3 "$MODULO" --ambiente prod --organizacao "$ORG_A" --prefixo "$PREFIXO" --raiz "$RAIZ" >/dev/null 2>&1
item "A3 prod recusado (exit 4)" 4 "$?"
item "A3 prod nao registrou nada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
cd "$RAIZ" && python3 "$MODULO" --planejar \
  --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" --raiz "$RAIZ" >"$TRABALHO/planejar.json" 2>&1
item "A3 --planejar exit 0 sem conexao" 0 "$?"
cd "$RAIZ" && python3 "$MODULO" --faixas --raiz "$RAIZ" >"$TRABALHO/faixas.json" 2>&1
item "A3 --faixas exit 0 sem conexao" 0 "$?"
item "A3 --faixas traz as 5 faixas do contrato" 5 "$(grep -c '"nome"' "$TRABALHO/faixas.json")"
item "A3 nada novo registrado" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"

# ---------------------------------------------------------------------------------------
# A4 — replay nao duplica; PRIORITY novo gera registro novo (historico)
# ---------------------------------------------------------------------------------------
SAIDA2=$(rodar "$CID2" --organizacao "$ORG_A")
item "A4 replay: nenhum gravado" 1 "$(echo "$SAIDA2" | grep -c '"gravados": 0')"
item "A4 replay: continua 1 registro TIER" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
priority "$ORG_A" 95.00 0 30
SAIDA3=$(rodar "$CID2" --organizacao "$ORG_A")
item "A4 PRIORITY novo grava registro NOVO" 2 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER';")"
item "A4 tier novo (95.00) e A+" "A+" "$(conta "SELECT request_payload->>'tier' FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND operation='TIER' ORDER BY created_at DESC, id DESC LIMIT 1;")"
item "A4 registro anterior preservado (86.45 -> A)" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_A' AND request_payload->>'tier' = 'A';")"

# ---------------------------------------------------------------------------------------
# A5 — empresa SEM PRIORITY RECUSA e nao registra (Nurture nao e default)
# ---------------------------------------------------------------------------------------
SAIDA5=$(rodar "$CID2" --organizacao "$ORG_D")
echo "$SAIDA5" | sed 's/^/  /'
item "A5 sem PRIORITY RECUSADA" 1 "$(echo "$SAIDA5" | grep -c 'SEM_PRIORITY')"
item "A5 nenhum registro para a empresa" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_D' AND operation='TIER';")"
item "A5 auditoria registrou a recusa" "REJECTED" "$(conta "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id='$CID2' AND organization_id='$ORG_D';")"
priority "$ORG_D" 49.99 1 30
rodar "$CID2" --organizacao "$ORG_D" >/dev/null
item "A5 com PRIORITY, 49.99 cai em Nurture" "Nurture" "$(conta "SELECT request_payload->>'tier' FROM sales_intelligence.sync_events WHERE entity_id='$ORG_D' AND operation='TIER';")"

# ---------------------------------------------------------------------------------------
# A6 — PRIORITY VENCIDO conta como ausente; o vencimento e LIDO do banco
# ---------------------------------------------------------------------------------------
SAIDA6=$(rodar "$CID2" --organizacao "$ORG_C")
echo "$SAIDA6" | sed 's/^/  /'
item "A6 vencido RECUSADA" 1 "$(echo "$SAIDA6" | grep -c 'PRIORITY_VENCIDO')"
item "A6 nao registrou nada" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_C' AND operation='TIER';")"
psql_t -c "UPDATE sales_intelligence.scores SET valid_until = NOW() + INTERVAL '10 days' WHERE organization_id='$ORG_C' AND score_type='PRIORITY';" >/dev/null
rodar "$CID2" --organizacao "$ORG_C" >/dev/null
item "A6 dentro da validade, classifica (55.00 -> C)" "C" "$(conta "SELECT request_payload->>'tier' FROM sales_intelligence.sync_events WHERE entity_id='$ORG_C' AND operation='TIER';")"

# ---------------------------------------------------------------------------------------
# A7 — o tier e do ULTIMO PRIORITY, nao da media do historico
# ---------------------------------------------------------------------------------------
rodar "$CID2" --organizacao "$ORG_B" >/dev/null
item "A7 ultimo PRIORITY (92.00 -> A+), nao a media" "A+" "$(conta "SELECT request_payload->>'tier' FROM sales_intelligence.sync_events WHERE entity_id='$ORG_B' AND operation='TIER';")"
item "A7 o PRIORITY lido e o mais recente" 1 "$(conta "SELECT CASE WHEN request_payload->'score_lido'->>'score_value' = '92.00' THEN 1 ELSE 0 END FROM sales_intelligence.sync_events WHERE entity_id='$ORG_B' AND operation='TIER';")"
rodar "$CID2" --organizacao "$ORG_E" >/dev/null
item "A7 fronteira 65.00 -> B" "B" "$(conta "SELECT request_payload->>'tier' FROM sales_intelligence.sync_events WHERE entity_id='$ORG_E' AND operation='TIER';")"

# ---------------------------------------------------------------------------------------
# A8 — empresa inexistente e recusada sem registrar
# ---------------------------------------------------------------------------------------
SAIDA8=$(rodar "$CID2" --organizacao "$ORG_FANTASMA")
item "A8 RECUSADA (empresa inexistente)" 1 "$(echo "$SAIDA8" | grep -c 'ORGANIZACAO_NAO_ENCONTRADA')"
item "A8 nada registrado para a fantasma" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events WHERE entity_id='$ORG_FANTASMA';")"

# ---------------------------------------------------------------------------------------
# A9 — desfazer: dry-run nao apaga; --confirmo apaga SO o registro da rodada
# ---------------------------------------------------------------------------------------
ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events;")
SCORES_MEIO=$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")
rodar "$CID1" --desfazer "$CID1" >"$TRABALHO/desfazer-dry.json" 2>&1
item "A9 dry-run nao apaga" "$ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events;")"
rodar "$CID1" --desfazer "$CID1" --confirmo >"$TRABALHO/desfazer.json" 2>&1
item "A9 --confirmo apagou so a rodada 1" "$((ANTES - 1))" "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events;")"
item "A9 scores intocados pelo desfazer" "$SCORES_MEIO" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"
item "A9 auditoria preservada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
rodar "cccccccc-cccc-4ccc-8ccc-cccccccccccc" --desfazer "cccccccc-cccc-4ccc-8ccc-cccccccccccc" --confirmo >/dev/null 2>&1
item "A9 rodada inexistente nao apaga" "$((ANTES - 1))" "$(conta "SELECT COUNT(*) FROM sales_intelligence.sync_events;")"

# ---------------------------------------------------------------------------------------
# A10 — dente por vinculo: mutacoes na COPIA do modulo reprovam o item esperado
# ---------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente (mutacoes em copia)"
  D=$(mktemp -d "$TRABALHO/dente.XXXXXX")
  while IFS='|' read -r nome de para item_esperado; do
    [ -z "$nome" ] && continue
    if ! grep -qF "$de" "$MODULO"; then
      echo "FALHOU dente $nome: ancora ausente"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); continue
    fi
    python3 - "$MODULO" "$D/mut_$nome.py" "$de" "$para" <<'PY'
import sys, pathlib
origem, destino, de, para = sys.argv[1:5]
texto = pathlib.Path(origem).read_text(encoding="utf-8")
pathlib.Path(destino).write_text(texto.replace(de, para, 1), encoding="utf-8")
PY
    SAIDA=$(TRE_TIERING_CONTAINER="pg-tier-dente" TRE_TIERING_TRABALHO="$D/t$nome" \
      bash "$0" --codigo "$D/mut_$nome.py" --raiz "$RAIZ" 2>&1) </dev/null
    if echo "$SAIDA" | grep -qF "FALHOU $item_esperado " && ! echo "$SAIDA" | grep -qF "ACEITE_TIERING_001_OK"; then
      echo "OK     dente $nome: reprovou o item esperado ($item_esperado)"
      ITENS_OK=$((ITENS_OK + 1))
    else
      echo "FALHOU dente $nome: NAO reprovou o item esperado $item_esperado"
      ITENS_FALHOU=$((ITENS_FALHOU + 1))
    fi
  done <<'LISTA'
ausencia-vira-registro|    if calculo["tier"] is None:|    if False:|A5 nenhum registro para a empresa
sem-checagem-de-vencido|    if limite is not None and limite <= agora_dt:|    if False:|A6 vencido RECUSADA
fronteira-aberta|        if faixa["min"] <= valor <= faixa["max"]:|        if faixa["min"] < valor < faixa["max"]:|A7 fronteira 65.00 -> B
sem-idempotencia|SELECT COUNT(*) FROM %s WHERE id = %s AND status = 'PROCESSED';|SELECT 1;|A4 replay: continua 1 registro TIER
ultimo-vira-primeiro|ORDER BY calculated_at DESC, id DESC LIMIT 1;|ORDER BY calculated_at ASC, id ASC LIMIT 1;|A7 o PRIORITY lido e o mais recente
operation-trocada|OPERACAO_SYNC = "TIER"|OPERACAO_SYNC = "TIERS"|A1 um registro TIER para a empresa
LISTA
  rm -rf "$D"
fi

echo
echo "== ACEITE $ITENS_OK OK / $ITENS_FALHOU FALHOU"
if [ "$ITENS_FALHOU" -eq 0 ]; then echo "ACEITE_TIERING_001_OK"; exit 0; fi
echo "ACEITE_TIERING_001_FALHOU"; exit 1
