#!/usr/bin/env bash
# teste_buying_signal_aceite.sh [--prova-de-dente] [--manter] [--codigo <modulo.py>]
#
# ACEITE E2E do BUYING SIGNAL SCORE v1 (card TRE-W5-E03-T01) — PostgreSQL descartavel.
#
# Roda onde existe daemon Docker (a VPS do ambiente: o container do Hermes nao tem). NUNCA toca
# `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio
# (`pg-buying-acc`), aplica a migration 0001, mede e, no fim, remove o container e o diretorio de
# trabalho. Se o container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (A1..A10 do doc `docs/architecture/buying-signal-score-v1.md`):
#   1. score legitimo gravado em `scores` com score_type/score_version/inputs/explanation/valid_until;
#   2. a gravacao NAO toca `signals` nem `organizations` (nem as colunas de score do sinal);
#   3. `prod` e recusado (exit 4) SEM escrita; `--planejar` nao abre conexao;
#   4. replay da MESMA entrada nao duplica (idempotencia) e sinal novo gera linha NOVA (historico);
#   5. sem sinal utilizavel o score e 0,00 com motivo SEM_SINAIS (empresa sem sinal ainda tem score);
#   6. empresa inexistente e RECUSADA sem escrever score;
#   7. desfazer dry-run nao apaga; `--confirmo` apaga SO o que a rodada criou;
#   8. `agent_runs` (auditoria) e `sync_events` (trava) ficam registrados;
#   9. veredito ..... ACEITE_BSS_001_OK / ACEITE_BSS_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do modulo e exige que o aceite reprove O ITEM
#   ESPERADO de cada uma (nao basta "o aceite falhou").
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_BSS_CONTAINER (default pg-buying-acc), TRE_BSS_TRABALHO.
# Exit: 0 = ACEITE_BSS_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_BSS_CONTAINER:-pg-buying-acc}"
TRABALHO="${TRE_BSS_TRABALHO:-/tmp/bss-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="bss-aceite-descartavel"
MODULO="$RAIZ/hermes/agents/buying_signal/buying_signal_score.py"
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
    *) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <modulo.py>]"; exit 2 ;;
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
# Container descartavel + migration + massa minima (empresa e sinais do detector)
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

ORG="11111111-1111-4111-8111-111111111111"
ORG_VAZIA="22222222-2222-4222-8222-222222222222"
ORG_FANTASMA="33333333-3333-4333-8333-333333333333"
CID1="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CID2="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, status, created_at)
VALUES ('$ORG', 'Distribuidora Aceite LTDA', 'Aceite', 'Pesquisado', NOW()),
       ('$ORG_VAZIA', 'Empresa Sem Sinal LTDA', 'Sem Sinal', 'Descoberto', NOW());
INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, signal_category,
  title, source_type, event_date, detected_at, confidence)
VALUES ('44444444-4444-4444-8444-444444444444','$ORG','ERP_CHANGE','TECNOLOGIA',
        'Troca de ERP anunciada','WEB', NOW() - INTERVAL '10 days', NOW() - INTERVAL '9 days', 0.90),
       ('55555555-5555-4555-8555-555555555555','$ORG','HIRING','EXPANSAO',
        'Vagas para time de operacoes','LINKEDIN', NOW() - INTERVAL '60 days', NOW() - INTERVAL '60 days', 0.70),
       ('66666666-6666-4666-8666-666666666666','$ORG','AI_INITIATIVE','TECNOLOGIA',
        'Programa de IA anunciado','WEB', NULL, NOW() - INTERVAL '30 days', NULL);
SQL

conta() { psql_t -c "SELECT $1;" | head -1; }
rodar() { # <cid> <args...>
  local cid="$1"; shift
  ( cd "$RAIZ" && python3 "$MODULO" --ambiente dev --correlation-id "$cid" \
      --prefixo "$PREFIXO" --raiz "$RAIZ" "$@" ) 2>&1
}

# ---------------------------------------------------------------------------------------
# A1/A2 — rodada 1: score gravado, sem tocar em signals/organizations
# ---------------------------------------------------------------------------------------
SAIDA1=$(rodar "$CID1" --organizacao "$ORG" --relatorio "$TRABALHO/rodada1.json")
RC1=$?
echo "$SAIDA1" | sed 's/^/  /'
item "A1 rodada1 exit 0" 0 "$RC1"
item "A1 veredito CALCULADO" 1 "$(echo "$SAIDA1" | grep -c '"gravados": 1')"
item "A1 uma linha em scores" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A1 score_type" "BUYING_SIGNAL" "$(conta "SELECT score_type FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A1 score_version" "buying-signal-v1" "$(conta "SELECT score_version FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A1 score_value > 0" 1 "$(conta "SELECT CASE WHEN score_value > 0 AND score_value <= 100 THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A1 3 sinais no inputs" 3 "$(conta "SELECT jsonb_array_length(inputs->'sinais_utilizados') FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A1 confianca padrao registrada" 1 "$(conta "SELECT (inputs->>'confianca_padrao_usada')::int FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A1 explanation com resumo" 1 "$(conta "SELECT CASE WHEN length(explanation->>'resumo') > 10 THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A1 valid_until = calculated_at + 30 dias" 1 "$(conta "SELECT CASE WHEN valid_until = calculated_at + INTERVAL '30 days' THEN 1 ELSE 0 END FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A2 signals intactos (3 linhas)" 3 "$(conta "SELECT COUNT(*) FROM sales_intelligence.signals WHERE organization_id='$ORG';")"
item "A2 nenhuma coluna de score do sinal preenchida" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.signals WHERE organization_id='$ORG' AND (relevance_score IS NOT NULL OR buying_signal_points IS NOT NULL OR expires_at IS NOT NULL);")"
item "A2 organizations sem UPDATE (trade_name intacto)" "Aceite" "$(conta "SELECT trade_name FROM sales_intelligence.organizations WHERE id='$ORG';")"
item "A8 agent_runs com a rodada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
item "A8 agent_runs status COMPLETED" "COMPLETED" "$(conta "SELECT status FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"
item "A8 sync_events fechado" "PROCESSED" "$(conta "SELECT status FROM sales_intelligence.sync_events WHERE operation='SCORE' ORDER BY created_at DESC LIMIT 1;")"

# ---------------------------------------------------------------------------------------
# A3 — prod recusado sem escrita e --planejar sem conexao
# ---------------------------------------------------------------------------------------
SAIDA_PROD=$( cd "$RAIZ" && python3 "$MODULO" --ambiente prod --organizacao "$ORG" --prefixo "$PREFIXO" --raiz "$RAIZ" 2>&1 )
RC_PROD=$?
item "A3 prod recusado (exit 4)" 4 "$RC_PROD"
item "A3 prod nao escreveu score" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
SAIDA_PLAN=$( cd "$RAIZ" && python3 "$MODULO" --planejar --organizacao "$ORG" \
  --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" --raiz "$RAIZ" 2>&1 )
item "A3 --planejar exit 0 sem conexao" 0 "$?"
item "A3 --planejar nao escreveu" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG';")"

# ---------------------------------------------------------------------------------------
# A4 — replay da mesma entrada nao duplica; sinal novo vira linha nova (historico)
# ---------------------------------------------------------------------------------------
SAIDA2=$(rodar "$CID2" --organizacao "$ORG")
item "A4 replay: nenhum gravado" 1 "$(echo "$SAIDA2" | grep -c '"gravados": 0')"
item "A4 replay: continua 1 score" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
VALOR1=$(conta "SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG';")
psql_t -c "INSERT INTO sales_intelligence.signals (id, organization_id, signal_type, signal_category, title, source_type, event_date, detected_at, confidence) VALUES ('77777777-7777-4777-8777-777777777777','$ORG','FUNDING','EXPANSAO','Rodada de investimento','WEB', NOW(), NOW(), 0.95);" >/dev/null
SAIDA3=$(rodar "$CID2" --organizacao "$ORG")
item "A4 sinal novo grava linha NOVA" 2 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG';")"
item "A4 score mudou com sinal novo" 1 "$(conta "SELECT CASE WHEN (SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG' ORDER BY calculated_at DESC LIMIT 1) > $VALOR1 THEN 1 ELSE 0 END;")"
item "A4 score antigo preservado (historico)" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG' AND score_value = $VALOR1;")"

# ---------------------------------------------------------------------------------------
# A5 — empresa sem sinal: score 0,00 com motivo SEM_SINAIS (ausencia e informacao)
# ---------------------------------------------------------------------------------------
SAIDA5=$(rodar "$CID2" --organizacao "$ORG_VAZIA")
item "A5 sem sinal: grava 0,00" "0.00" "$(conta "SELECT score_value FROM sales_intelligence.scores WHERE organization_id='$ORG_VAZIA';")"
item "A5 motivo SEM_SINAIS" "SEM_SINAIS" "$(conta "SELECT explanation->>'motivo' FROM sales_intelligence.scores WHERE organization_id='$ORG_VAZIA';")"

# ---------------------------------------------------------------------------------------
# A6 — empresa inexistente e recusada sem escrever
# ---------------------------------------------------------------------------------------
SAIDA6=$(rodar "$CID2" --organizacao "$ORG_FANTASMA")
item "A6 RECUSADA" 1 "$(echo "$SAIDA6" | grep -c 'ORGANIZACAO_NAO_ENCONTRADA')"
item "A6 nada escrito para a fantasma" 0 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_FANTASMA';")"

# ---------------------------------------------------------------------------------------
# A7 — desfazer: dry-run nao apaga; --confirmo apaga SO o que a rodada criou
# ---------------------------------------------------------------------------------------
ANTES=$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")
DRY=$(rodar "$CID1" --desfazer "$CID1")
item "A7 dry-run nao apaga" 1 "$(echo "$DRY" | grep -c '"dry_run": true')"
item "A7 dry-run preserva" "$ANTES" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"
CONF=$(rodar "$CID1" --desfazer "$CID1" --confirmo)
item "A7 --confirmo apagou 1" 1 "$(echo "$CONF" | grep -c '"apagados": 1')"
item "A7 so a rodada 1 saiu (2 restantes)" 2 "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"
item "A7 signals preservados" 4 "$(conta "SELECT COUNT(*) FROM sales_intelligence.signals WHERE organization_id='$ORG';")"
item "A7 organizations preservadas" 2 "$(conta "SELECT COUNT(*) FROM sales_intelligence.organizations;")"
item "A7 auditoria preservada" 1 "$(conta "SELECT COUNT(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CID1';")"

# ---------------------------------------------------------------------------------------
# A9 — desfazer de rodada inexistente nao apaga nada
# ---------------------------------------------------------------------------------------
V=$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")
rodar "cccccccc-cccc-4ccc-8ccc-cccccccccccc" --desfazer "cccccccc-cccc-4ccc-8ccc-cccccccccccc" --confirmo >/dev/null
item "A9 rodada inexistente nao apaga" "$V" "$(conta "SELECT COUNT(*) FROM sales_intelligence.scores;")"

# ---------------------------------------------------------------------------------------
# A10 — dente por vinculo: mutacoes na COPIA do modulo reprovam o item esperado
# ---------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente (mutacoes em copia)"
  D=$(mktemp -d "$TRABALHO/dente.XXXXXX")
  # Lista lida ANTES do laco (o corpo roda docker, que consome stdin se nao for redirecionado).
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
    # Cada mutacao roda em container proprio: sem estado compartilhado entre medidas.
    SAIDA=$(TRE_BSS_CONTAINER="pg-buying-dente" TRE_BSS_TRABALHO="$D/t$nome" \
      bash "$0" --codigo "$D/mut_$nome.py" 2>&1) </dev/null
    if echo "$SAIDA" | grep -q "FALHOU $item_esperado " && ! echo "$SAIDA" | grep -q "ACEITE_BSS_001_OK"; then
      echo "OK     dente $nome: reprovou o item esperado ($item_esperado)"
      ITENS_OK=$((ITENS_OK + 1))
    else
      echo "FALHOU dente $nome: NAO reprovou o item esperado $item_esperado"
      ITENS_FALHOU=$((ITENS_FALHOU + 1))
    fi
  done <<'LISTA'
sem-versao|lit(SCORE_VERSION)|lit("SEM_VERSAO")|A1 score_version
sem-teto|valor = max(0.0, min(100.0, 100.0 * forca))|valor = 100.0 * forca * 3|A1 score_value > 0
sem-idempotencia|WHERE NOT EXISTS (SELECT 1 FROM sales_intelligence.sync_events WHERE idempotency_key = '|WHERE NOT EXISTS (SELECT 1 FROM sales_intelligence.sync_events WHERE idempotency_key <> '|A4 replay: continua 1 score
sem-guarda-signals|if tabela in (TABELA_SINAIS, TABELA_ORGANIZACOES):|if tabela in ():|A2 nenhuma coluna de score do sinal preenchida
LISTA
  rm -rf "$D"
fi

echo
echo "== ACEITE $ITENS_OK OK / $ITENS_FALHOU FALHOU"
if [ "$ITENS_FALHOU" -eq 0 ]; then echo "ACEITE_BSS_001_OK"; exit 0; fi
echo "ACEITE_BSS_001_FALHOU"; exit 1
