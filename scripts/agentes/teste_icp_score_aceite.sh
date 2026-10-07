#!/usr/bin/env bash
# =====================================================================================
# teste_icp_score_aceite.sh [--prova-de-dente] [--manter] [--codigo <icp_score.py>]
#
# ACEITE DO AGENTE ICP SCORE v1 — TRE-W5-E01-T01. Roda ON DE A VPS do ambiente (ADR-0008: o
# PostgreSQL vive la; o container do Hermes so orquestra por SSH).
#
# O que ele faz, em um container PostgreSQL DESCARTÁVEL (nunca toca pg-sales-dev,
# pg-odoo-dev, odoo-dev nem proxy-dev):
#   0. guardas ....... docker/python3/migration/agente presentes; o nome do container NAO pode
#                      existir (se existir, ABORTA em vez de mexer no que nao e dele)
#   1. sobe ......... container novo com a migration 0001 + 8 organizacoes sinteticas de perfis
#                      diferentes (sweet spot com os tres sinais, FORA DE SP, 49 funcionarios no
#                      corte de porte, sem dado, B2B2C com um sinal, porte derivado do count,
#                      sinais SEM FONTE declarada e uma APAGADA) e um uuid valido que nao existe
#                      no banco; os sinais vem de sales_intelligence.signals (so' leitura)
#   2. rodada 1 ..... 7 scores CALCULADO + 2 RECUSADA, com os valores MEDIDOS um a um
#                      (100,00 / 0,00 / 0,00 / 0,00 / 82,17 / 100,00 / 80,00), os cortes e os
#                      cinco criterios nomeados conferidos contra o valor e auditoria sem LLM
#   3. rodada 2 ..... a MESMA fonte: 0 score novo (retry nao cria duplicata)
#   3b. rodada 2b ... a fonte MENTE os campos (dado no banco e' outro): replay, o score nao
#                      se move — a fonte escolhe o sujeito, nao o dado
#   4. rodada 3 ..... o dado da organizacao MUDA no banco: score novo gravado e o anterior
#                      preservado (score e' historico, nao mutavel)
#   5. guardas ...... --ambiente prod recusado sem escrever; --planejar nao conecta
#   6. desfazer ..... dry-run nao apaga; --confirmo apaga SO os scores da rodada 1, preserva a
#                      auditoria e o score da rodada 3, e registra ROLLBACK
#   7. ambiente ..... os quatro containers persistentes intactos; nada em producao
#   8. veredito ..... ACEITE_ICP_SCORE_001_OK / ACEITE_ICP_SCORE_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do icp_score.py (ausencia que vira fit, faixa
#   derivada desligada, idempotencia removida, fingerprint constante, prod liberado, fonte
#   contaminando, auditoria apagada no desfazer, peso do modelo zerado, organizacao inexistente
#   criando score e as tres regras do modelo 1.1: corte de porte desligado, corte de geografia
#   desligado e credito de intencao sem fonte declarada) e exige que o aceite REPROVE **o item
#   esperado de cada mutacao** — nao basta "o aceite falhou". Mutacao que nao se aplica na
#   ancora tambem reprova (e buraco de verificacao, nao alivio), e o veredito imprime a
#   contagem MEDIDA.
#
# NOTA (defeito medido na W4): `docker exec -i` CONSOME o stdin de quem o chamou. Aqui as
#   mutacoes sao lidas numa LISTA antes do laco e todo `docker exec` que nao le stdin leva
#   `</dev/null`.
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_ICP_CONTAINER (default pg-icp-acc), TRE_ICP_TRABALHO (dir de trabalho).
# Exit: 0 = ACEITE_ICP_SCORE_001_OK · 1 = FALHOU · 2 = uso/guarda.
# =====================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_ICP_CONTAINER:-pg-icp-acc}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="icp-aceite-descartavel"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
AGENTE_PY="$RAIZ/hermes/agents/icp_score/icp_score.py"
EXEMPLO_FONTE="$RAIZ/hermes/agents/icp_score/exemplos/organizacoes-exemplo.jsonl"
TRABALHO="${TRE_ICP_TRABALHO:-$(mktemp -d /tmp/icp-aceite-XXXXXX)}"

MANTER=0
DENTE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter)         MANTER=1 ;;
    --codigo)         AGENTE_PY="$2"; shift ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <icp_score.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <icp_score.py>]"; exit 2 ;;
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

# ---------------------------------------------------------------------------------------
# Guardas e ciclo de vida do container descartavel
# ---------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
[ -f "$MIGRATION" ] || { echo "FALHOU migration ausente: $MIGRATION"; exit 2; }
[ -f "$AGENTE_PY" ] || { echo "FALHOU agente ausente: $AGENTE_PY"; exit 2; }
[ -f "$EXEMPLO_FONTE" ] || { echo "FALHOU fonte de exemplo ausente: $EXEMPLO_FONTE"; exit 2; }
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"
  exit 2
fi
mkdir -p "$TRABALHO"

limpar() {
  local rc=$?
  if [ "$MANTER" -eq 0 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1
    rm -rf "$TRABALHO"
  else
    echo "== mantido: container $CONTAINER e diretorio $TRABALHO"
  fi
  return $rc
}
trap limpar EXIT

PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
# `</dev/null` em TODA chamada que nao le stdin: `docker exec -i` herda (e consome) o stdin de
# quem o chamou. Dentro do laco de mutacoes isso matava as iteracoes seguintes.
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
contagem() { "${PSQL[@]}" -c "$1" </dev/null | tr -d '[:space:]'; }
valor() { "${PSQL[@]}" -c "$1" </dev/null | tr -d '[:space:]'; }

subir_container() {
  docker run -d --name "$CONTAINER" \
    -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
    "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
  # O `pg_isready` mente no inicio (servidor temporario da inicializacao): espera SELECT 1
  # funcionar DUAS vezes, com intervalo — licao medida na W1.
  local tentativa repetiu=0
  for tentativa in $(seq 1 60); do
    if "${PSQL[@]}" -c "SELECT 1" </dev/null >/dev/null 2>&1; then
      repetiu=$((repetiu + 1))
      [ "$repetiu" -ge 2 ] && return 0
      sleep 2
    else
      repetiu=0
      sleep 1
    fi
  done
  echo "FALHOU o container nao ficou pronto"; exit 2
}

# Organizacoes sinteticas: uma por regra do modelo. ORG_G existe mas esta apagada (deleted_at);
# ORG_X e' um uuid valido que NAO existe no banco.
ORG_A="aaaaaaaa-0000-4000-8000-000000000001"   # SP, 210, Distribuidores B2B, 3 sinais -> 100,00
ORG_B="aaaaaaaa-0000-4000-8000-000000000002"   # FORA DE SP (PR), 900, Logistica, 3 sinais -> 0,00
ORG_C="aaaaaaaa-0000-4000-8000-000000000003"   # SP, 49 funcionarios, B2C: CORTE de porte  -> 0,00
ORG_D="aaaaaaaa-0000-4000-8000-000000000004"   # sem dado nenhum                          -> 0,00
ORG_E="aaaaaaaa-0000-4000-8000-000000000005"   # SP, 300, B2B2C, 1 sinal                  -> 82,17
ORG_F="aaaaaaaa-0000-4000-8000-000000000006"   # SP, 640 (band derivado), 3 sinais        -> 100,00
ORG_H="aaaaaaaa-0000-4000-8000-000000000008"   # SP, 210, B2B, 3 sinais SEM FONTE         -> 80,00
ORG_G="aaaaaaaa-0000-4000-8000-000000000007"   # APAGADA (deleted_at)                 -> RECUSADA
ORG_X="aaaaaaaa-0000-4000-8000-000000000009"   # uuid valido, inexistente             -> RECUSADA
# Os containers persistentes do dev medidos em 07/10/2026 (o antigo `proxy-dev` virou
# `proxy-edge`; a n8n do dev entra na lista porque o aceite nao pode derrubar o ambiente).
PERSISTENTES="pg-sales-dev|pg-odoo-dev|odoo-dev|n8n-dev|proxy-edge"

preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
  psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, industry_code, industry_name, employee_count, employee_band,
   business_model, city, state, country_code, status, source, created_at, updated_at, deleted_at)
VALUES
  ('$ORG_A', 'Distribuidora Vale Forte Ltda', 'Vale Forte', '4649',
   'Distribuidora de materiais de construcao', 210, '150_299', 'B2B', 'Osasco', 'SP', 'BR',
   'DISCOVERED', 'LINKEDIN', now(), now(), NULL),
  ('$ORG_B', 'Transportes Rota Certa Ltda', 'Rota Certa', '5320',
   'Logistica e transporte de cargas', 900, '700_1000', 'B2B', 'Curitiba', 'PR', 'BR',
   'DISCOVERED', 'WEB', now(), now(), NULL),
  ('$ORG_C', 'Padaria Pao Quente Ltda', 'Pao Quente', '4721',
   'Comercio varejista de alimentos', 49, 'LT_70', 'B2C', 'Santos', 'SP', 'BR',
   'DISCOVERED', 'EVENTOS', now(), now(), NULL),
  ('$ORG_D', 'Empresa Sem Dado Ltda', 'Sem Dado', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
   'BR', 'DISCOVERED', 'WEB', now(), now(), NULL),
  ('$ORG_E', 'Solucoes Digitais Beta Ltda', 'Beta Digital', '6201',
   'Consultoria de tecnologia da informacao', 300, '300_499', 'B2B2C', 'Sao Paulo', 'SP', 'BR',
   'DISCOVERED', 'LINKEDIN', now(), now(), NULL),
  ('$ORG_F', 'Metalurgica Serra Azul Ltda', 'Serra Azul', '2411',
   'Industria metalurgica de autopecas', 640, NULL, 'B2B', 'Joinville', 'SP', 'BR',
   'DISCOVERED', 'DADOS_PUBLICOS', now(), now(), NULL),
  ('$ORG_H', 'Distribuidora Sem Fonte Ltda', 'Sem Fonte', '4649',
   'Distribuidora de materiais de construcao', 210, '150_299', 'B2B', 'Osasco', 'SP', 'BR',
   'DISCOVERED', 'INDICACAO', now(), now(), NULL),
  ('$ORG_G', 'Empresa Apagada Ltda', 'Apagada', '4649', 'Distribuidora', 210, '150_299', 'B2B',
   'Osasco', 'SP', 'BR', 'DISCOVERED', 'WEB', now(), now(), now());

INSERT INTO sales_intelligence.signals
  (id, organization_id, signal_type, source_type, source_url, event_date, evidence, created_at)
VALUES
  ('f1111111-0000-4000-8000-000000000001', '$ORG_A', 'AI_INITIATIVE', 'FORMULARIO_SITE',
   'https://exemplo.test/formulario', '2026-09-30T10:00:00-03', '{"inferencia": false}', now()),
  ('f1111111-0000-4000-8000-000000000002', '$ORG_A', 'HIRING', 'EVENTO',
   'https://exemplo.test/evento', '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f1111111-0000-4000-8000-000000000003', '$ORG_A', 'DIGITAL_TRANSFORMATION', 'INDICACAO',
   NULL, '2026-09-10T08:00:00-03', '{"inferencia": false}', now()),
  ('f2222222-0000-4000-8000-000000000001', '$ORG_B', 'AI_INITIATIVE', 'EVENTO', NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f2222222-0000-4000-8000-000000000002', '$ORG_B', 'HIRING', 'EVENTO', NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f2222222-0000-4000-8000-000000000003', '$ORG_B', 'EFFICIENCY_PROGRAM', 'EVENTO', NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f5555555-0000-4000-8000-000000000001', '$ORG_E', 'AI_INITIATIVE', 'EVENTO', NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f6666666-0000-4000-8000-000000000001', '$ORG_F', 'AI_INITIATIVE', 'EVENTO', NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": true}', now()),
  ('f6666666-0000-4000-8000-000000000002', '$ORG_F', 'HIRING', 'EVENTO', NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f6666666-0000-4000-8000-000000000003', '$ORG_F', 'EFFICIENCY_PROGRAM', 'INDICACAO', NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f8888888-0000-4000-8000-000000000001', '$ORG_H', 'AI_INITIATIVE', NULL, NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f8888888-0000-4000-8000-000000000002', '$ORG_H', 'HIRING', NULL, NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now()),
  ('f8888888-0000-4000-8000-000000000003', '$ORG_H', 'DIGITAL_TRANSFORMATION', NULL, NULL,
   '2026-09-20T09:00:00-03', '{"inferencia": false}', now());
SQL
}

escrever_fonte() { # <arquivo> — a fonte carrega APENAS o sujeito (organization_id)
  cat > "$1" <<JSONL
{"organization_id":"$ORG_A"}
{"organization_id":"$ORG_B"}
{"organization_id":"$ORG_C"}
{"organization_id":"$ORG_D"}
{"organization_id":"$ORG_E"}
{"organization_id":"$ORG_F"}
{"organization_id":"$ORG_H"}
{"organization_id":"$ORG_G"}
{"organization_id":"$ORG_X"}
JSONL
}

escrever_fonte_mentirosa() { # <arquivo> — a fonte mente TODOS os campos de score da ORG_A
  cat > "$1" <<JSONL
{"organization_id":"$ORG_A","industry_name":"Comercio varejista de alimentos","industry_code":"4721","employee_count":10,"employee_band":"LT_70","business_model":"B2C","state":"PR","sinais":[],"score_value":0}
JSONL
}

escrever_fonte_uma() { # <arquivo> <uuid>
  printf '{"organization_id":"%s"}\n' "$2" > "$1"
}

rodar_agente() { # <relatorio> <args...>
  local relatorio="$1"; shift
  python3 "$AGENTE_PY" --raiz "$RAIZ" --relatorio "$relatorio" "$@" </dev/null
}

veredito_do_relatorio() { # <relatorio> <veredito>
  python3 - "$1" "$2" <<'PY'
import json, sys
caminho, alvo = sys.argv[1], sys.argv[2]
if caminho == "__sem_relatorio__":
    print(0); raise SystemExit
relatorio = json.load(open(caminho, encoding="utf-8"))
print(relatorio.get("por_veredito", {}).get(alvo, 0))
PY
}

CORR_R1="eeeeeeee-0000-4000-8000-000000000001"
CORR_R2="eeeeeeee-0000-4000-8000-000000000002"
CORR_R2B="eeeeeeee-0000-4000-8000-000000000003"
CORR_R3="eeeeeeee-0000-4000-8000-000000000004"
CORR_R4="eeeeeeee-0000-4000-8000-000000000005"
FONTE="$TRABALHO/fonte.jsonl"
FONTE_MENTIRA="$TRABALHO/fonte-mentira.jsonl"
FONTE_UMA="$TRABALHO/fonte-uma.jsonl"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

score_de() { # <uuid> — ultimo score da organizacao (o mais recente)
  valor "SELECT score_value::text FROM sales_intelligence.scores WHERE organization_id = '$1' ORDER BY calculated_at DESC, id DESC LIMIT 1;"
}

corte_de() { # <uuid> <corte> — nome=passou/motivo do corte registrado na explicacao
  valor "SELECT c->>'nome' || '=' || (c->>'passou') || '/' || COALESCE(c->>'motivo','sem-motivo') FROM sales_intelligence.scores s, jsonb_array_elements(s.explanation->'cortes') c WHERE s.organization_id = '$1' AND c->>'nome' = '$2' ORDER BY s.calculated_at DESC, s.id DESC LIMIT 1;"
}

sub_de() { # <uuid> <componente> — sub_score do componente na explicacao
  valor "SELECT c->>'sub_score' FROM sales_intelligence.scores s, jsonb_array_elements(s.explanation->'componentes') c WHERE s.organization_id = '$1' AND c->>'nome' = '$2' ORDER BY s.calculated_at DESC, s.id DESC LIMIT 1;"
}

sinal_de() { # <uuid> <nome do criterio> — nome=credito/fonte/motivo do sinal de intencao
  valor "SELECT x->>'nome' || '=' || (x->>'credito') || '/' || COALESCE(x->>'fonte','sem-fonte') || '/' || COALESCE(x->>'motivo','sem-motivo') FROM sales_intelligence.scores s, jsonb_array_elements(s.explanation->'componentes') c, jsonb_array_elements(c->'sinais') x WHERE s.organization_id = '$1' AND c->>'nome' = 'intencao' AND x->>'nome' = '$2' ORDER BY s.calculated_at DESC, s.id DESC LIMIT 1;"
}

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== aceite do codigo sob teste: $AGENTE_PY ($rotulo)"
  preparar_banco
  escrever_fonte "$FONTE"
  escrever_fonte_mentirosa "$FONTE_MENTIRA"

  # ---- rodada 1 --------------------------------------------------------------------
  rodar_agente "$TRABALHO/r1.json" --ambiente dev --correlation-id "$CORR_R1" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r1.out" 2>&1
  item "rodada1-exit-0" "0" "$?"
  item "rodada1-scores-gravados" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "rodada1-tipo-e-versao" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='ICP' AND score_version='icp-v1.1.0';")"
  item "rodada1-relatorio-conflito-e-casamento" "CALCULADO=7 RECUSADA=2" \
    "CALCULADO=$(veredito_do_relatorio "$TRABALHO/r1.json" CALCULADO) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r1.json" RECUSADA)"
  # valores MEDIDOS, um por regra do modelo (nada de "tem 7 linhas, logo esta certo")
  item "rodada1-valor-sweet-spot" "100.00" "$(score_de "$ORG_A")"
  item "rodada1-valor-fora-de-sp" "0.00" "$(score_de "$ORG_B")"
  item "rodada1-valor-49-no-corte-de-porte" "0.00" "$(score_de "$ORG_C")"
  item "rodada1-valor-sem-dado" "0.00" "$(score_de "$ORG_D")"
  item "rodada1-valor-b2b2c" "82.17" "$(score_de "$ORG_E")"
  item "rodada1-valor-porte-derivado" "100.00" "$(score_de "$ORG_F")"
  item "rodada1-valor-intencao-sem-fonte" "80.00" "$(score_de "$ORG_H")"
  # os CORTES da definicao do dono: porte >= 50 e state = SP (portao, nao peso)
  item "rodada1-corte-de-porte-49-nao-passa" "porte_minimo=false/CORTE_PORTE_ABAIXO_DE_50" "$(corte_de "$ORG_C" porte_minimo)"
  item "rodada1-corte-de-porte-sweet-spot-passa" "porte_minimo=true/sem-motivo" "$(corte_de "$ORG_A" porte_minimo)"
  item "rodada1-corte-de-geografia-fora-de-sp" "geografia=false/CORTE_FORA_DE_SP" "$(corte_de "$ORG_B" geografia)"
  item "rodada1-corte-de-geografia-sp-passa" "geografia=true/sem-motivo" "$(corte_de "$ORG_A" geografia)"
  item "rodada1-corte-derruba-o-score-alto" "90.00" "$(valor "SELECT (explanation->>'score_bruto')::text FROM sales_intelligence.scores WHERE organization_id='$ORG_B' ORDER BY calculated_at DESC, id DESC LIMIT 1;")"
  # os TRES SINAIS de intencao: fonte E data, senao nao ha' credito
  item "rodada1-intencao-tres-sinais-pontua-cheio" "100.00" "$(sub_de "$ORG_A" intencao)"
  item "rodada1-intencao-sem-fonte-nao-credita" "0.00" "$(sub_de "$ORG_H" intencao)"
  item "rodada1-intencao-sem-fonte-motivo" "A_BUSCA_SERVICOS_AUTOMATIZAVEIS_POR_IA=false/sem-fonte/INTENCAO_SEM_FONTE" "$(sinal_de "$ORG_H" A_BUSCA_SERVICOS_AUTOMATIZAVEIS_POR_IA)"
  item "rodada1-intencao-com-fonte-guarda-fonte-e-data" "A_BUSCA_SERVICOS_AUTOMATIZAVEIS_POR_IA=true/FORMULARIO_SITE/sem-motivo" "$(sinal_de "$ORG_A" A_BUSCA_SERVICOS_AUTOMATIZAVEIS_POR_IA)"
  item "rodada1-criterios-nomeados" "5" "$(contagem "SELECT jsonb_array_length(explanation->'criterios') FROM sales_intelligence.scores WHERE organization_id='$ORG_A' ORDER BY calculated_at DESC, id DESC LIMIT 1;")"
  item "rodada1-cinco-criterios-casados" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.scores s, jsonb_array_elements(s.explanation->'criterios') c WHERE s.organization_id='$ORG_A' AND c->>'resultado' = 'casou';")"
  item "rodada1-criterios-que-faltaram-nomeados" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.scores s, jsonb_array_elements(s.explanation->'criterios') c WHERE s.organization_id='$ORG_D' AND c->>'resultado' = 'sem_dado' AND c->>'motivo' IS NOT NULL;")"
  # a explicacao fecha a conta (bruta) com cinco componentes e cinco criterios
  item "rodada1-inputs-e-explicacao" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE inputs->>'faixa_efetiva' IS NOT NULL AND inputs->>'origem_do_porte' IN ('employee_band','employee_count','ausente') AND inputs->>'fingerprint' ~ '^[0-9a-f]{64}\$' AND explanation->>'modelo'='icp-v1.1.0' AND (explanation->>'pesos_somam')::numeric = 1.0 AND jsonb_array_length(explanation->'componentes') = 5 AND jsonb_array_length(explanation->'criterios') = 5 AND jsonb_array_length(explanation->'cortes') = 2;")"
  item "rodada1-explicacao-fecha-a-conta" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE round((SELECT sum((c->>'contribuicao')::numeric) FROM jsonb_array_elements(explanation->'componentes') c), 2) = (explanation->>'score_bruto')::numeric AND (((explanation->>'corte_aplicado')::boolean = false AND score_value = (explanation->>'score_bruto')::numeric) OR ((explanation->>'corte_aplicado')::boolean = true AND score_value = 0.00));")"
  item "rodada1-origem-do-porte-derivado" "employee_count" "$(valor "SELECT inputs->>'origem_do_porte' FROM sales_intelligence.scores WHERE organization_id='$ORG_F';")"
  item "rodada1-dado-ausente-com-motivos" "7" "$(contagem "SELECT jsonb_array_length(explanation->'motivos') FROM sales_intelligence.scores WHERE organization_id='$ORG_D';")"
  item "rodada1-dado-ausente-nao-vira-fit" "CORTE_PORTE_SEM_DADO,CORTE_GEOGRAFIA_SEM_DADO,SEGMENTO_NAO_INFORMADO,PORTE_NAO_INFORMADO,GEOGRAFIA_NAO_INFORMADA,MODELO_DE_NEGOCIO_NAO_INFORMADO,INTENCAO_SEM_DADO" "$(valor "SELECT string_agg(t.x, ',' ORDER BY t.o) FROM jsonb_array_elements_text((SELECT explanation->'motivos' FROM sales_intelligence.scores WHERE organization_id='$ORG_D')) WITH ORDINALITY AS t(x, o);")"
  item "rodada1-ambiguidade-de-segmento-registrada" "2" "$(contagem "SELECT jsonb_array_length((SELECT c->'segmentos_casados' FROM jsonb_array_elements(explanation->'componentes') c WHERE c->>'nome'='segmento')) FROM sales_intelligence.scores WHERE organization_id='$ORG_E';")"
  # recusas: apagada e inexistente NAO ganham score nem auditoria de sucesso
  item "rodada1-recusadas" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='icp_score' AND correlation_id='$CORR_R1' AND status='REJECTED';")"
  item "rodada1-inexistente-sem-score" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id IN ('$ORG_G','$ORG_X');")"
  # auditoria e trilha
  item "rodada1-auditoria-por-organizacao" "9" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='icp_score' AND agent_role='scoring' AND correlation_id='$CORR_R1';")"
  item "rodada1-auditoria-completed" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='COMPLETED';")"
  item "rodada1-sem-llm" "9" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND model IS NULL AND tokens_input IS NULL AND tokens_output IS NULL AND estimated_cost IS NULL;")"
  item "rodada1-sync-events-success" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE entity_type='score' AND operation='INSERT' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_R1';")"
  # `signals` tem massa do fixture (13 linhas) e o agente NAO escreve nela: o que ele escreve
  # e' so' o score. `contacts` e as demais continuam em zero.
  item "rodada1-signals-nao-escrito-pelo-agente" "13" "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "rodada1-nenhuma-outra-tabela-escrita" "0" "$(contagem "SELECT (SELECT count(*) FROM sales_intelligence.contacts) + (SELECT count(*) FROM sales_intelligence.research_runs) + (SELECT count(*) FROM sales_intelligence.pain_hypotheses) + (SELECT count(*) FROM sales_intelligence.interactions) + (SELECT count(*) FROM sales_intelligence.recommendations) + (SELECT count(*) FROM sales_intelligence.outbox_events);")"

  # ---- rodada 2: mesma fonte, mesmos dados -------------------------------------------
  rodar_agente "$TRABALHO/r2.json" --ambiente dev --correlation-id "$CORR_R2" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r2.out" 2>&1
  item "rodada2-exit-0" "0" "$?"
  item "rodada2-nao-duplica" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "rodada2-replay" "JA_EXISTE=7 CALCULADO=0" \
    "JA_EXISTE=$(veredito_do_relatorio "$TRABALHO/r2.json" JA_EXISTE) CALCULADO=$(veredito_do_relatorio "$TRABALHO/r2.json" CALCULADO)"
  item "rodada2-sem-novo-claim" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R2';")"

  # ---- rodada 2b: a fonte MENTE os campos (o dado do banco manda) --------------------
  rodar_agente "$TRABALHO/r2b.json" --ambiente dev --correlation-id "$CORR_R2B" \
    --fonte "$FONTE_MENTIRA" --prefixo "$PREFIXO" > "$TRABALHO/r2b.out" 2>&1
  item "rodada2b-exit-0" "0" "$?"
  item "rodada2b-fonte-nao-contamina" "JA_EXISTE=1" "JA_EXISTE=$(veredito_do_relatorio "$TRABALHO/r2b.json" JA_EXISTE)"
  item "rodada2b-score-nao-se-move" "100.00" "$(score_de "$ORG_A")"
  item "rodada2b-nao-duplica" "7" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"

  # ---- rodada 3: o dado MUDA no banco ------------------------------------------------
  psql_t -c "UPDATE sales_intelligence.organizations SET employee_count = 800, employee_band = '700_1000', updated_at = now() WHERE id = '$ORG_A';" >/dev/null
  escrever_fonte_uma "$FONTE_UMA" "$ORG_A"
  rodar_agente "$TRABALHO/r3.json" --ambiente dev --correlation-id "$CORR_R3" \
    --fonte "$FONTE_UMA" --prefixo "$PREFIXO" > "$TRABALHO/r3.out" 2>&1
  item "rodada3-exit-0" "0" "$?"
  item "rodada3-dado-novo-grava-de-novo" "CALCULADO=1" "CALCULADO=$(veredito_do_relatorio "$TRABALHO/r3.json" CALCULADO)"
  item "rodada3-valor-novo" "90.00" "$(score_de "$ORG_A")"
  item "rodada3-historico-preservado" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A';")"
  item "rodada3-valor-antigo-preservado" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND score_value = 100.00;")"
  item "rodada3-total-de-scores" "8" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"

  # ---- guarda de ambiente (ADR-005) e modo sem banco ------------------------------
  rodar_agente "$TRABALHO/r4.json" --ambiente prod --correlation-id "$CORR_R4" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r4.out" 2>&1
  item "prod-recusado-exit-4" "4" "$?"
  item "prod-nao-escreveu" "8" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "prod-nao-registrou-execucao" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R4';")"
  rodar_agente "$TRABALHO/r5.json" --planejar --fonte "$EXEMPLO_FONTE" \
    --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" \
    > "$TRABALHO/r5.out" 2>&1
  item "planejar-exit-0-sem-conectar" "0" "$?"
  item "planejar-nao-escreveu" "8" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"

  # ---- desfazer -------------------------------------------------------------------
  rodar_agente "$TRABALHO/dry.json" --desfazer "$CORR_R1" --ambiente dev \
    --prefixo "$PREFIXO" > "$TRABALHO/dry.out" 2>&1
  item "desfazer-dry-run-exit-0" "0" "$?"
  item "desfazer-dry-run-nao-apagou" "8" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  rodar_agente "$TRABALHO/del.json" --desfazer "$CORR_R1" --ambiente dev --confirmo \
    --prefixo "$PREFIXO" > "$TRABALHO/del.out" 2>&1
  item "desfazer-confirmo-exit-0" "0" "$?"
  item "desfazer-apagou-so-a-rodada" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "desfazer-preservou-o-score-de-outra-rodada" "90.00" "$(score_de "$ORG_A")"
  item "desfazer-preservou-auditoria" "9" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1';")"
  item "desfazer-registrou-rollback" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='ROLLBACK';")"

  # ---- ambiente: containers persistentes intactos ----------------------------------
  local persistentes esperados
  esperados="$(echo "$PERSISTENTES" | tr '|' '\n' | wc -l | tr -d '[:space:]')"
  persistentes="$(docker ps --format '{{.Names}}' | grep -cE "^(${PERSISTENTES})$")"
  item "ambiente-containers-intactos" "$esperados" "$persistentes"

  echo "RESULTADO: ACEITE_ICP_SCORE_001_$( [ "$ITENS_FALHOU" -eq 0 ] && echo OK || echo FALHOU ) ($((ITENS_OK + ITENS_FALHOU)) itens, $ITENS_FALHOU falhas)"
  [ "$ITENS_FALHOU" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
# Prova de dente: mutacao em copia do agente TEM de reprovar o aceite
# ---------------------------------------------------------------------------------------
aplicar_mutacao() { # <origem> <destino> <alvo> <substituto>
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import pathlib, sys
origem, destino, alvo, substituto = sys.argv[1:5]
texto = pathlib.Path(origem).read_text(encoding="utf-8")
ocorrencias = texto.count(alvo)
if ocorrencias != 1:
    print("MUTACAO_NAO_APLICAVEL (%d ocorrencias da ancora)" % ocorrencias)
    sys.exit(3)
pathlib.Path(destino).write_text(texto.replace(alvo, substituto), encoding="utf-8")
print("MUTACAO_APLICADA")
PY
}

prova_de_dente() {
  echo
  echo "=== PROVA DE DENTE (baseline verde ANTES das mutacoes) ==="
  local baseline_ok=0
  if rodar_aceite "baseline-do-dente" > "$TRABALHO/dente-baseline.out" 2>&1; then baseline_ok=1; fi
  if [ "$baseline_ok" -eq 1 ]; then
    echo "OK     baseline verde antes das mutacoes"
  else
    echo "FALHOU baseline NAO ficou verde — mutacao nao prova nada sem baseline"
    tail -5 "$TRABALHO/dente-baseline.out"
    return 1
  fi

  # As mutacoes sao lidas numa LISTA antes do laco: o corpo chama `docker exec -i` (por
  # psql_t/contagem), que consome o stdin do laco — com here-string as iteracoes 2+ morriam.
  # Formato: nome|alvo|substituto|itens-esperados (o dente exige o item, nao so "falhou").
  local linhas=() linha
  while IFS= read -r linha; do
    [ -n "$linha" ] && linhas+=("$linha")
  done <<'EOF'
ausencia-de-porte-vira-fit|        sub_port, motivo_port = float(port["sub_score_ausente"]), port["motivos"]["sem_dado"]|        sub_port, motivo_port = 100.0, None|rodada1-valor-sem-dado,rodada1-dado-ausente-nao-vira-fit
sem-faixa-derivada-do-count|    for inicio, fim, faixa in numericas:|    for inicio, fim, faixa in ():|rodada1-valor-porte-derivado
peso-do-modelo-zerado|"peso": float(modelo["pesos"]["modelo_b2b"])|"peso": 0.0|rodada1-valor-sweet-spot
sem-idempotencia-no-sql|  ON CONFLICT (idempotency_key) DO NOTHING|\n|rodada2-exit-0,rodada2-replay
fingerprint-constante|    fingerprint = hashlib.sha256(|    fingerprint = ("0" * 64) or hashlib.sha256(|rodada3-dado-novo-grava-de-novo
prod-liberado|    if args.ambiente or not args.planejar:|    if False:|prod-recusado-exit-4,prod-nao-registrou-execucao
fonte-contamina-o-score|                    calculo = calcular_icp(organizacao, self.modelo, self.faixas)|                    calculo = calcular_icp({**organizacao, **{k: v for k, v in linha.items() if k != 'organization_id'}}, self.modelo, self.faixas)|rodada2b-fonte-nao-contamina,rodada2b-score-nao-se-move
corte-de-porte-desligado|    passou_porte = valor_porte is not None and valor_porte >= float(corte_porte["minimo"])|    passou_porte = True|rodada1-corte-de-porte-49-nao-passa,rodada1-valor-49-no-corte-de-porte
corte-de-geografia-desligado|    passou_geo = bool(valor_geo) and valor_geo in estados_do_corte|    passou_geo = True|rodada1-corte-de-geografia-fora-de-sp,rodada1-valor-fora-de-sp
intencao-sem-fonte-credita|            if not fonte:|            if False:|rodada1-intencao-sem-fonte-nao-credita,rodada1-intencao-sem-fonte-motivo
desfazer-apaga-a-auditoria|        "DELETE FROM {scores} WHERE id IN ({ids});\n"|        "DELETE FROM {scores} WHERE id IN ({ids});\nDELETE FROM sales_intelligence.agent_runs WHERE 1=1;\n"|desfazer-preservou-auditoria
organizacao-inexistente-cria-score|                if organizacao is None:|                if False: pass\n                if False:|rodada1-recusadas
EOF

  local total="${#linhas[@]}" detectadas=0 falhas=0
  local nome alvo substituto esperados destino guardado faltando esperado
  for linha in "${linhas[@]}"; do
    IFS='|' read -r nome alvo substituto esperados <<< "$linha"
    [ -z "$nome" ] && continue
    destino="$TRABALHO/mut-$nome/icp_score.py"
    mkdir -p "$(dirname "$destino")"
    if ! aplicar_mutacao "$AGENTE_PY" "$destino" "$alvo" "$substituto" | grep -q MUTACAO_APLICADA; then
      echo "FALHOU mutacao $nome NAO se aplicou (ancora mudou) — buraco de verificacao"
      falhas=$((falhas + 1)); continue
    fi
    echo
    echo "-- mutacao: $nome (tem de reprovar: $(echo "$esperados" | tr ',' ' '))"
    guardado="$AGENTE_PY"
    AGENTE_PY="$destino"
    if rodar_aceite "mutacao $nome" > "$TRABALHO/mut-$nome.out" 2>&1; then
      echo "FALHOU mutacao $nome NAO foi detectada pelo aceite"
      falhas=$((falhas + 1))
    else
      # Detectada de verdade = o aceite reprovou O ITEM ESPERADO desta mutacao. "O aceite
      # falhou" sozinho nao vale: mutacao que quebra a importacao contaria como detectada.
      faltando=""
      for esperado in $(echo "$esperados" | tr ',' ' '); do
        grep -q "^FALHOU $esperado " "$TRABALHO/mut-$nome.out" || faltando="$faltando $esperado"
      done
      if [ -n "$faltando" ]; then
        echo "FALHOU mutacao $nome detectada, mas SEM o item esperado:$faltando"
        grep '^FALHOU' "$TRABALHO/mut-$nome.out" | head -3
        falhas=$((falhas + 1))
      else
        detectadas=$((detectadas + 1))
        echo "OK     mutacao $nome reprovou o(s) item(ns) esperado(s) — $(grep -c '^FALHOU' "$TRABALHO/mut-$nome.out") item(ns) reprovado(s) no total: $(grep '^FALHOU' "$TRABALHO/mut-$nome.out" | head -3 | cut -d' ' -f2 | tr '\n' ' ')"
      fi
    fi
    AGENTE_PY="$guardado"
  done

  echo
  if [ "$falhas" -eq 0 ]; then
    echo "DENTE OK ($detectadas/$total mutacoes detectadas, cada uma pelo item esperado)"
  else
    echo "DENTE FALHOU ($detectadas/$total detectadas; $falhas falha(s): nao aplicada, nao detectada ou sem o item esperado)"
  fi
  [ "$falhas" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
principal() {
  echo "== ACEITE DO AGENTE ICP SCORE v1 (TRE-W5-E01-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0
  if rodar_aceite "principal"; then ok=1; fi
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_ICP_SCORE_001_OK"
    return 0
  fi
  echo "ACEITE_ICP_SCORE_001_FALHOU"
  return 1
}

principal
