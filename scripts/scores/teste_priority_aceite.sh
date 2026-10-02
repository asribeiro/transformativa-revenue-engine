#!/usr/bin/env bash
# teste_priority_aceite.sh [--prova-de-dente] [--manter] [--codigo <modulo.py>] [--raiz <dir>]
#
# ACEITE E2E do PRIORITY SCORE v1 (card TRE-W5-E05-T01) — PostgreSQL descartavel.
#
# Roda onde existe daemon Docker (a VPS do ambiente: o container do Hermes nao tem). NUNCA toca
# `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio
# (`pg-priority-acc`), aplica a migration 0001, mede e, no fim, remove o container e o diretorio de
# trabalho. Se o container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (A1..A10 de `docs/architecture/score-priority-v1.md`):
#   1. score legitimo gravado em `scores` com score_type/score_version/inputs/explanation/valid_until
#      e o VALOR do contrato (0,35*94 + 0,30*76 + 0,25*83 + 0,10*100 = 86,45);
#   2. a gravacao NAO toca os scores dos componentes nem `organizations` (o PRIORITY agrega, nao mede);
#   3. `prod` e recusado (exit 4) SEM escrita; `--planejar` nao abre conexao;
#   4. replay da MESMA entrada nao duplica (idempotencia) e componente NOVO gera linha NOVA (historico);
#   5. componente AUSENTE recusa com SEM_LASTRO_COMPLETO e nao escreve (nao ha renormalizacao);
#   6. componente VENCIDO conta como ausente; vencimento e LIDO do banco, nao suposto;
#   7. empresa inexistente e RECUSADA sem escrever score;
#   8. desfazer dry-run nao apaga; `--confirmo` apaga SO o que a rodada criou;
#   9. `agent_runs` (auditoria) e `sync_events` (trava) ficam registrados;
#  10. veredito ..... ACEITE_PRIORITY_001_OK / ACEITE_PRIORITY_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do modulo e exige que o aceite reprove O ITEM
#   ESPERADO de cada uma (nao basta "o aceite falhou").
#
# Variaveis: TRE_RAIZ, TRE_FIXTURE_IMAGEM (default postgres:16), TRE_PRIORITY_CONTAINER,
#            TRE_PRIORITY_TRABALHO.
# Exit: 0 = ACEITE_PRIORITY_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_PRIORITY_CONTAINER:-pg-priority-acc}"
TRABALHO="${TRE_PRIORITY_TRABALHO:-/tmp/priority-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="priority-aceite-descartavel"
MODULO="$RAIZ/hermes/scores/priority/priority_score.py"
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
# Container descartavel + migration + massa (empresas e os QUATRO scores de componente)
# ---------------------------------------------------------------------------------------
docker run -d --name "$CONTAINER" \
  -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
  "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
# `pg_isready` mente no inicio: a espera e por `SELECT 1` funcionando DUAS vezes.
for _ in $(seq 1 60); do
  psql_t -c "SELECT 1;" >/dev/null 2>&1 && psql_t -c "SELECT 1;" >/dev/null 2>&1 && break
  sleep 1
done
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }

ORG_A="11111111-1111-4111-8111-111111111111"   # os quatro componentes, todos validos
ORG_B="22222222-2222-4222-8222-222222222222"   # falta DATA_QUALITY
ORG_C="33333333-3333-4333-8333-333333333333"   # DATA_QUALITY vencido
ORG_FANTASMA="99999999-9999-4999-8999-999999999999"
CID1="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CID2="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

componente() { # <org> <tipo> <valor> <dias_de_validade|NULL>
  local validade="NULL"
  [ "$4" != "NULL" ] && validade="NOW() + INTERVAL '$4 days'"
  psql_t -c "INSERT INTO sales_intelligence.scores
      (id, organization_id, score_type, score_value, score_version, inputs, explanation, calculated_at, valid_until)
    VALUES (gen_random_uuid(), '$1', '$2', $3, '${2,,}-v1', '{}'::jsonb, '{}'::jsonb,
            NOW() - INTERVAL '1 day', $validade);" >/dev/null
}

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, status, created_at)
VALUES ('$ORG_A', 'Distribuidora A LTDA', 'A', 'Pesquisado', NOW()),
       ('$ORG_B', 'Industria B LTDA', 'B', 'Pesquisado', NOW()),
       ('$ORG_C', 'Servicos C LTDA', 'C', 'Pesquisado', NOW());
SQL
componente "$ORG_A" ICP 94.00 NULL
componente "$ORG_A" AUTOMATION_FIT 76.00 NULL
componente "$ORG_A" BUYING_SIGNAL 83.00 30
componente "$ORG_A" DATA_QUALITY 100.00 NULL
componente "$ORG_B" ICP 94.00 NULL
componente "$ORG_B" AUTOMATION_FIT 76.00 NULL
componente "$ORG_B" BUYING_SIGNAL 83.00 30
componente "$ORG_C" ICP 94.00 NULL
componente "$ORG_C" AUTOMATION_FIT 76.00 NULL
componente "$ORG_C" BUYING_SIGNAL 83.00 30
componente "$ORG_C" DATA_QUALITY 100.00 -1

conta() { psql_t -c "$1" | head -1; }
rodar() { # <cid> <args...>
  local cid="$1"; shift
  ( cd "$RAIZ" && python3 "$MODULO" --ambiente dev --correlation-id "$cid" \
      --prefixo "$PREFIXO" --raiz "$RAIZ" "$@" ) 2>&1
}

COMPONENTES_ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE score_type <> 'PRIORITY';")
# Impressao digital dos componentes: contagem + soma + versao (a rodada nao pode mexer em NENHUM)
DIGITAL_ANTES=$(conta "SELECT md5(string_agg(score_type || ':' || score_value || ':' || score_version, ',' ORDER BY id)) FROM sales_intelligence.scores WHERE score_type <> 'PRIORITY';")

# ---------------------------------------------------------------------------------------
# A1/A2/A9 — rodada 1: valor do contrato, sem tocar nos componentes
# ---------------------------------------------------------------------------------------
SAIDA1=$(rodar "$CID1" --organizacao "$ORG_A" --relatorio "$TRABALHO/rodada1.json")
RC1=$?
echo "$SAIDA1" | sed 's/^/  /'
item "A1 rodada1 exit 0" 0 "$RC1"
item "A1 veredito CALCULADO" 1 "$(echo "$SAIDA1" | grep -c '"gravados": 1')"
item "A1 uma linha PRIORITY" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A1 score_type" "PRIORITY" "$(conta "SELECT score_type FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A1 score_version" "priority-v1" "$(conta "SELECT score_version FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A1 valor do contrato (0,35*94+0,30*76+0,25*83+0,10*100)" "86.45" "$(conta "SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 valid_until = calculated_at + 30 dias" 1 "$(conta "SELECT CASE WHEN valid_until = calculated_at + INTERVAL '30 days' THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 cobertura 1.00 no inputs" "1.00" "$(conta "SELECT inputs->>'cobertura' FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 quatro componentes no inputs" 4 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores s, jsonb_object_keys(s.inputs->'componentes') k WHERE s.organization_id='$ORG_A' AND s.score_type='PRIORITY';")"
item "A9 identidade do componente no inputs" 1 "$(conta "SELECT CASE WHEN inputs->'componentes'->'ICP'->>'score_id' IS NOT NULL THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 versao do componente no inputs" "icp-v1" "$(conta "SELECT inputs->'componentes'->'ICP'->>'score_version' FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 pesos declarados no inputs" "0.35" "$(conta "SELECT inputs->'pesos'->>'ICP' FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 soma das parcelas explicada" "86.45" "$(conta "SELECT explanation->>'soma_das_parcelas' FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 sem LLM (tokens nulos)" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1' AND (model IS NOT NULL OR tokens_input IS NOT NULL OR estimated_cost IS NOT NULL);")"
item "A9 llm.executado=false na explanation" "false" "$(conta "SELECT explanation->'llm'->>'executado' FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A9 nao ha renormalizacao declarada" "true" "$(conta "SELECT explanation->'regras_declaradas'->>'sem_renormalizacao' FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A2 componentes intactos (nenhuma linha perdida)" "$COMPONENTES_ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE score_type <> 'PRIORITY';")"
item "A2 digital dos componentes intacta (valor+versao)" "$DIGITAL_ANTES" "$(conta "SELECT md5(string_agg(score_type || ':' || score_value || ':' || score_version, ',' ORDER BY id)) FROM sales_intelligence.scores WHERE score_type <> 'PRIORITY';")"
item "A2 organizations intactas (3)" 3 "$(conta "SELECT COUNT(*) FROM sales_intelligence.organizations;")"
item "A8 agent_runs com a rodada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
item "A8 agent_runs status COMPLETED" "COMPLETED" "$(conta "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
item "A8 sync_events fechado" "PROCESSED" "$(conta "SELECT status FROM sales_intelligence.sync_events WHERE operation='SCORE' ORDER BY created_at DESC LIMIT 1;")"

# ---------------------------------------------------------------------------------------
# A3 — prod recusado sem escrita e --planejar sem conexao
# ---------------------------------------------------------------------------------------
cd "$RAIZ" && python3 "$MODULO" --ambiente prod --organizacao "$ORG_A" --prefixo "$PREFIXO" --raiz "$RAIZ" >/dev/null 2>&1
RC_PROD=$?
item "A3 prod recusado (exit 4)" 4 "$RC_PROD"
item "A3 prod nao escreveu score novo" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
cd "$RAIZ" && python3 "$MODULO" --planejar \
  --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" --raiz "$RAIZ" >"$TRABALHO/planejar.json" 2>&1
item "A3 --planejar exit 0 sem conexao" 0 "$?"
item "A3 --planejar nao escreveu" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"

# ---------------------------------------------------------------------------------------
# A4 — replay nao duplica; componente NOVO vira linha nova (historico)
# ---------------------------------------------------------------------------------------
SAIDA2=$(rodar "$CID2" --organizacao "$ORG_A")
item "A4 replay: nenhum gravado" 1 "$(echo "$SAIDA2" | grep -c '"gravados": 0')"
item "A4 replay: continua 1 score PRIORITY" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
componente "$ORG_A" ICP 100.00 NULL
SAIDA3=$(rodar "$CID2" --organizacao "$ORG_A")
item "A4 componente novo grava linha NOVA" 2 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY';")"
item "A4 valor novo (0,35*100+0,30*76+0,25*83+0,10*100)" "88.55" "$(conta "SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY' ORDER BY calculated_at DESC LIMIT 1;")"
item "A4 score antigo preservado (historico)" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_type='PRIORITY' AND score_value = 86.45;")"

# ---------------------------------------------------------------------------------------
# A5 — lastro incompleto RECUSA e NAO escreve (sem renormalizacao)
# ---------------------------------------------------------------------------------------
SAIDA5=$(rodar "$CID2" --organizacao "$ORG_B")
echo "$SAIDA5" | sed 's/^/  /'
item "A5 lastro incompleto RECUSADA" 1 "$(echo "$SAIDA5" | grep -c 'SEM_LASTRO_COMPLETO')"
item "A5 motivo do que falta registrado" 1 "$(echo "$SAIDA5" | grep -c 'COMPONENTE_AUSENTE:DATA_QUALITY')"
item "A5 nao escreveu score" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_B' AND score_type='PRIORITY';")"
item "A5 auditoria registrou a recusa" "REJECTED" "$(conta "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id='$CID2' AND organization_id='$ORG_B';")"
# o componente que falta chega: agora calcula
componente "$ORG_B" DATA_QUALITY 100.00 NULL
SAIDA5B=$(rodar "$CID2" --organizacao "$ORG_B")
item "A5 com os quatro componentes, CALCULADO" "86.45" "$(conta "SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_B' AND score_type='PRIORITY';")"

# ---------------------------------------------------------------------------------------
# A6 — componente VENCIDO conta como ausente; o vencimento e LIDO do banco
# ---------------------------------------------------------------------------------------
SAIDA6=$(rodar "$CID2" --organizacao "$ORG_C")
echo "$SAIDA6" | sed 's/^/  /'
item "A6 vencido RECUSADA" 1 "$(echo "$SAIDA6" | grep -c 'COMPONENTE_VENCIDO:DATA_QUALITY')"
item "A6 nao escreveu score" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_C' AND score_type='PRIORITY';")"
psql_t -c "UPDATE sales_intelligence.scores SET valid_until = NOW() + INTERVAL '10 days' WHERE organization_id='$ORG_C' AND score_type='DATA_QUALITY';" >/dev/null
rodar "$CID2" --organizacao "$ORG_C" >/dev/null
item "A6 dentro da validade, CALCULADO" "86.45" "$(conta "SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_C' AND score_type='PRIORITY';")"

# ---------------------------------------------------------------------------------------
# A7 — empresa inexistente e recusada sem escrever
# ---------------------------------------------------------------------------------------
SAIDA7=$(rodar "$CID2" --organizacao "$ORG_FANTASMA")
item "A7 RECUSADA (empresa inexistente)" 1 "$(echo "$SAIDA7" | grep -c 'ORGANIZACAO_NAO_ENCONTRADA')"
item "A7 nada escrito para a fantasma" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_FANTASMA';")"

# ---------------------------------------------------------------------------------------
# A8 — desfazer: dry-run nao apaga; --confirmo apaga SO o que a rodada criou
# ---------------------------------------------------------------------------------------
ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")
componentes_antes=$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE score_type <> 'PRIORITY';")
rodar "$CID1" --desfazer "$CID1" >"$TRABALHO/desfazer-dry.json" 2>&1
item "A8 dry-run nao apaga" "$ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"
rodar "$CID1" --desfazer "$CID1" --confirmo >"$TRABALHO/desfazer.json" 2>&1
item "A8 --confirmo apagou so a rodada 1" "$((ANTES - 1))" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"
item "A8 componentes preservados" "$componentes_antes" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE score_type <> 'PRIORITY';")"
item "A8 auditoria preservada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
rodada_inexistente=$(rodar "cccccccc-cccc-4ccc-8ccc-cccccccccccc" --desfazer "cccccccc-cccc-4ccc-8ccc-cccccccccccc" --confirmo)
item "A8 rodada inexistente nao apaga" "$((ANTES - 1))" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"

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
    SAIDA=$(TRE_PRIORITY_CONTAINER="pg-priority-dente" TRE_PRIORITY_TRABALHO="$D/t$nome" \
      bash "$0" --codigo "$D/mut_$nome.py" --raiz "$RAIZ" 2>&1) </dev/null
    if echo "$SAIDA" | grep -q "FALHOU $item_esperado " && ! echo "$SAIDA" | grep -q "ACEITE_PRIORITY_001_OK"; then
      echo "OK     dente $nome: reprovou o item esperado ($item_esperado)"
      ITENS_OK=$((ITENS_OK + 1))
    else
      echo "FALHOU dente $nome: NAO reprovou o item esperado $item_esperado"
      ITENS_FALHOU=$((ITENS_FALHOU + 1))
    fi
  done <<'LISTA'
cobertura-afrouxada|COBERTURA_MINIMA = Decimal("1.00")|COBERTURA_MINIMA = Decimal("0.90")|A5 lastro incompleto RECUSADA
sem-checagem-de-vencido|vencido = limite is not None and limite <= agora_dt|vencido = False|A6 vencido RECUSADA
sem-idempotencia|AND status = 'REGISTERED');|AND status = status);|A4 replay: continua 1 score PRIORITY
sem-validade|VALIDADE_DIAS = 30|VALIDADE_DIAS = 7|A9 valid_until = calculated_at + 30 dias
sem-soma-de-um-componente|total += peso * valor|total += Decimal("0") * valor|A1 valor do contrato (0,35*94+0,30*76+0,25*83+0,10*100)
LISTA
  rm -rf "$D"
fi

echo
echo "== ACEITE $ITENS_OK OK / $ITENS_FALHOU FALHOU"
if [ "$ITENS_FALHOU" -eq 0 ]; then echo "ACEITE_PRIORITY_001_OK"; exit 0; fi
echo "ACEITE_PRIORITY_001_FALHOU"; exit 1
