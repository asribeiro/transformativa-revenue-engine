#!/usr/bin/env bash
# teste_automation_fit_aceite.sh [--prova-de-dente] [--manter] [--codigo <automation_fit.py>]
#
# ACEITE E2E do AUTOMATION FIT SCORE v1 (card TRE-W5-E02-T01) — PostgreSQL descartavel.
#
# Roda na VPS (o container do Hermes nao tem daemon Docker). NUNCA toca `pg-sales-dev`,
# `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio (`pg-automation-fit-acc`),
# aplica a migration 0001 e, no fim, remove o container e o diretorio de trabalho. Se o container ja
# existir, ABORTA em vez de mexer no que nao e' dele.
#
# O que este aceite mede por EXECUCAO REAL (A1..A14 do doc `docs/architecture/agente-automation-fit-v1.md`):
#   1. score gravado em `scores` com score_type=AUTOMATION_FIT, score_version=automation-fit-v1,
#      valor esperado da formula declarada, cobertura e componentes na explicacao;
#   2. LE o estado (organizations/signals/pain_hypotheses) e NAO escreve em nenhuma tabela de
#      negocio — em especial `organizations.data_quality_score` (que e' o card W5-E04);
#   3. sinal/hipotese de OUTRA empresa nao entra na conta (a leitura e' filtrada por organization_id);
#   4. determinismo no banco: repetir o MESMO estado nao cria linha (JA_CALCULADO);
#   5. HISTORICO: estado novo (sinal novo) cria LINHA NOVA, preservando a anterior;
#   6. sem lastro nao ha score (`RECUSADA`/`SEM_LASTRO`) — e nada e' escrito em `scores`;
#   7. empresa inexistente, sem identificador forte e identificador invalido sao RECUSADAS;
#   8. identidade ambigua vai para a fila humana (AUTOMATION_FIT_IDENTITY_REVIEW) SEM escrever score;
#   9. discriminacao MEDIDA nas linhas gravadas: faixas distintas, margem e desvio da constante;
#  10. `prod` recusado (exit 4) sem escrita e `--planejar` sem porta nao abre conexao;
#  11. desfazer dry-run nao apaga; `--confirmo` apaga SO o que a rodada criou, preservando as
#      tabelas de negocio e a auditoria, e registra ROLLBACK;
#  12. veredito ..... ACEITE_AUTOMATION_FIT_001_OK / ACEITE_AUTOMATION_FIT_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do automation_fit.py (cobertura ignorada, score
#   constante, idempotencia sem o estado, leitura sem filtro de empresa, prod liberado) e exige que
#   o aceite reprove O ITEM ESPERADO de cada uma — nao basta "o aceite falhou".
#
# Licoes ja pagas (mantidas aqui de proposito): todo `docker exec -i` que nao le stdin leva
# `</dev/null`, senao ele CONSOME o stdin do laco de mutacoes e mata as iteracoes seguintes; as
# mutacoes sao lidas numa LISTA antes do laco; `pg_isready` mente no inicio, entao a espera e' por
# `SELECT 1` funcionando DUAS vezes.
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_AUTOMATION_FIT_CONTAINER (default pg-automation-fit-acc), TRE_AUTOMATION_FIT_TRABALHO.
# Exit: 0 = ACEITE_AUTOMATION_FIT_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_AUTOMATION_FIT_CONTAINER:-pg-automation-fit-acc}"
TRABALHO="${TRE_AUTOMATION_FIT_TRABALHO:-/tmp/automation-fit-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="automation-fit-aceite-descartavel"
CODIGO="$RAIZ/hermes/agents/automation_fit/automation_fit.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; CODIGO="${1:?--codigo exige caminho}" ;;
    --codigo=*) CODIGO="${1#--codigo=}" ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <automation_fit.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <automation_fit.py>]"; exit 2 ;;
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
[ -f "$CODIGO" ] || { echo "FALHOU componente ausente: $CODIGO"; exit 2; }
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
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
contagem() { "${PSQL[@]}" -c "$1" </dev/null | tr -d '[:space:]'; }

subir_container() {
  docker run -d --name "$CONTAINER" \
    -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
    "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
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

ORG_A="aaaaaaaa-0000-4000-8000-000000000001"
ORG_B="aaaaaaaa-0000-4000-8000-000000000002"
ORG_C="aaaaaaaa-0000-4000-8000-000000000003"
ORG_D="aaaaaaaa-0000-4000-8000-000000000004"
ORG_E="aaaaaaaa-0000-4000-8000-000000000005"
SIG_A1="11111111-0000-4000-8000-0000000000a1"
SIG_A2="11111111-0000-4000-8000-0000000000a2"
SIG_A3="11111111-0000-4000-8000-0000000000a3"
SIG_B1="11111111-0000-4000-8000-0000000000b1"
SIG_B2="11111111-0000-4000-8000-0000000000b2"
SIG_B3="11111111-0000-4000-8000-0000000000b3"
SIG_D1="11111111-0000-4000-8000-0000000000d1"
SIG_D2="11111111-0000-4000-8000-0000000000d2"
SIG_D3="11111111-0000-4000-8000-0000000000d3"
SIG_D4="11111111-0000-4000-8000-0000000000d4"
SIG_E1="11111111-0000-4000-8000-0000000000e1"
HIP_A1="22222222-0000-4000-8000-0000000000a1"
HIP_B1="22222222-0000-4000-8000-0000000000b1"
HIP_D1="22222222-0000-4000-8000-0000000000d1"
HIP_E1="22222222-0000-4000-8000-0000000000e1"
CNPJ_A="11.222.333/0001-81"
CNPJ_B="45.723.174/0001-10"
CNPJ_D="19.131.243/0001-97"
CNPJ_E="28.213.728/0001-10"
CNPJ_INVALIDO="11.222.333/0001-00"
SINAIS_SEMEADOS=11
SINAIS_APOS_A3=12

preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
  # `updated_at` FIXO num passado distante de proposito: se o score encostar em qualquer coluna de
  # `organizations`, o valor muda e o aceite acusa. `data_quality_score` fica NULL: a coluna e' do
  # card W5-E04 e o Automation Fit nao tem o direito de escrever nela.
  psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, cnpj, domain, linkedin_url, employee_band, employee_count,
   unit_count, industry_name, status, source, data_quality_score, created_at, updated_at)
VALUES
  ('$ORG_A', 'Metalurgica Vale Forte Ltda', 'Vale Forte', '11222333000181', 'valeforte.com.br',
   NULL, '300_499', 420, 4, 'Metalurgia', 'DISCOVERED', 'LINKEDIN', NULL,
   '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z'),
  ('$ORG_B', 'Clinica Sao Lucas S.A.', 'Clinica Sao Lucas', '45723174000110',
   'clinicasl.com.br', NULL, 'GT_1000', 1500, 12, 'Saude', 'DISCOVERED', 'WEB', NULL,
   '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z'),
  ('$ORG_C', 'Distribuidora Litoral Ltda', 'Distribuidora Litoral', NULL, NULL,
   'https://www.linkedin.com/company/distribuidora-litoral', 'LT_70', 40, 1, 'Distribuicao',
   'DISCOVERED', 'EVENTOS', NULL, '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z'),
  ('$ORG_D', 'AgroSmart Analytics Ltda', 'AgroSmart Analytics', '19131243000197',
   'agrosmart-analytics.com.br', NULL, '150_299', 250, 2, 'Agtech', 'DISCOVERED', 'WEB', NULL,
   '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z'),
  ('$ORG_E', 'Transportes Serra Azul Ltda', 'Serra Azul', '28213728000110', 'serraazul.com.br',
   NULL, '70_149', 95, 1, 'Logistica', 'DISCOVERED', 'DADOS_PUBLICOS', NULL,
   '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z');

INSERT INTO sales_intelligence.signals
  (id, organization_id, signal_type, signal_category, source_type, detected_at, created_at)
VALUES
  ('$SIG_A1', '$ORG_A', 'PROCESS_COMPLEXITY', 'PRESSAO_OPERACIONAL', 'WEB',
   '2026-09-01T10:00:00Z', '2026-09-01T10:00:00Z'),
  ('$SIG_A2', '$ORG_A', 'SERVICE_VOLUME', 'PRESSAO_OPERACIONAL', 'LINKEDIN',
   '2026-09-02T10:00:00Z', '2026-09-02T10:00:00Z'),
  ('$SIG_B1', '$ORG_B', 'COST_REDUCTION', 'EFICIENCIA', 'WEB',
   '2026-09-03T10:00:00Z', '2026-09-03T10:00:00Z'),
  ('$SIG_B2', '$ORG_B', 'CUSTOMER_COMPLAINT', 'PRESSAO_OPERACIONAL', 'WEB',
   '2026-09-04T10:00:00Z', '2026-09-04T10:00:00Z'),
  ('$SIG_B3', '$ORG_B', 'CRM_CHANGE', 'TECNOLOGIA', 'LINKEDIN',
   '2026-09-05T10:00:00Z', '2026-09-05T10:00:00Z'),
  ('$SIG_D1', '$ORG_D', 'AI_INITIATIVE', 'TECNOLOGIA', 'LINKEDIN',
   '2026-09-06T10:00:00Z', '2026-09-06T10:00:00Z'),
  ('$SIG_D2', '$ORG_D', 'DIGITAL_TRANSFORMATION', 'TECNOLOGIA', 'WEB',
   '2026-09-07T10:00:00Z', '2026-09-07T10:00:00Z'),
  ('$SIG_D3', '$ORG_D', 'PROCESS_COMPLEXITY', 'PRESSAO_OPERACIONAL', 'WEB',
   '2026-09-08T10:00:00Z', '2026-09-08T10:00:00Z'),
  ('$SIG_D4', '$ORG_D', 'SERVICE_VOLUME', 'PRESSAO_OPERACIONAL', 'DADOS_PUBLICOS',
   '2026-09-09T10:00:00Z', '2026-09-09T10:00:00Z'),
  ('$SIG_E1', '$ORG_E', 'REGULATORY_CHANGE', 'REGULATORIO', 'DADOS_PUBLICOS',
   '2026-09-10T10:00:00Z', '2026-09-10T10:00:00Z'),
  ('$SIG_A3', '$ORG_A', 'HIRING', 'EXPANSAO', 'LINKEDIN',
   '2026-09-11T10:00:00Z', '2026-09-11T10:00:00Z');

INSERT INTO sales_intelligence.pain_hypotheses
  (id, organization_id, pain_category, pain_statement, business_impact_score, confidence, status,
   created_at)
VALUES
  ('$HIP_A1', '$ORG_A', 'OPERACIONAL', 'Fila manual no fechamento de pedidos', 90, 0.70,
   'HYPOTHESIS', '2026-09-12T10:00:00Z'),
  ('$HIP_B1', '$ORG_B', 'FINANCEIRO', 'Conciliacao manual de repasses', 40, 0.50,
   'HYPOTHESIS', '2026-09-12T10:00:00Z'),
  ('$HIP_D1', '$ORG_D', 'OPERACIONAL', 'Triagem manual de pedidos no WhatsApp', 100, 0.80,
   'HYPOTHESIS', '2026-09-12T10:00:00Z'),
  ('$HIP_E1', '$ORG_E', 'FINANCEIRO', 'Conferencia manual de fretes', 10, 0.40,
   'HYPOTHESIS', '2026-09-12T10:00:00Z');
SQL
}

# Gera um CNPJ com digito verificador VALIDO que NAO existe na base: e' a prova de
# ORGANIZACAO_NAO_ENCONTRADA (identificador bem formado, empresa inexistente).
cnpj_inexistente() {
  python3 - <<'PY'
def digito(base):
    pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    nums = [int(d) for d in base]
    usados = pesos[len(pesos) - len(nums):]
    resto = sum(n * p for n, p in zip(nums, usados)) % 11
    return "0" if resto < 2 else str(11 - resto)
base = "19876543" + "0001"
print(base + digito(base) + digito(base + digito(base)))
PY
}

# A fonte e' escrita por python (JSON valido, legivel e sem pegadinha de quoting do shell).
escrever_fonte() { # <arquivo> <cnpj-inexistente>
  python3 - "$1" "$2" <<PY
import json, sys
destino, cnpj_inexistente = sys.argv[1], sys.argv[2]
linhas = []
# L1 — ORG_A por dominio: 4 componentes presentes (porte, pressao, unidades, dor).
linhas.append({"organizacao": {"domain": "valeforte.com.br"}})
# L2 — ORG_B por CNPJ (com pontuacao): prova a normalizacao da identidade.
linhas.append({"organizacao": {"cnpj": "$CNPJ_B"}})
# L3 — ORG_C por LinkedIn "sujo": SEM LASTRO (so' porte 0,25 + unidades 0,10 = 0,35 < 0,40).
linhas.append({"organizacao": {"linkedin_url": "https://br.linkedin.com/company/distribuidora-litoral/"}})
# L4 — ORG_D por CNPJ: muitos sinais de tecnologia e pressao.
linhas.append({"organizacao": {"cnpj": "$CNPJ_D"}})
# L5 — ORG_E com CAMPOS NAO DECLARADOS: porte/impacto declarados na fonte nao entram na conta.
linhas.append({"organizacao": {"cnpj": "$CNPJ_E"}, "employee_band": "GT_1000", "unit_count": 99,
               "business_impact_score": 100, "score_value": 99})
# L6 — CNPJ valido de empresa que nao esta na base: RECUSADA ORGANIZACAO_NAO_ENCONTRADA.
linhas.append({"organizacao": {"cnpj": cnpj_inexistente}})
# L7 — sem identificador forte (nome/cidade e' dedup FRACO): RECUSADA SEM_IDENTIFICADOR_FORTE.
linhas.append({"organizacao": {"cidade": "Santos", "razao_social": "Litoral Ltda"}})
# L8 — CNPJ com digito verificador errado: RECUSADA IDENTIFICADOR_FORTE_INVALIDO.
linhas.append({"organizacao": {"cnpj": "$CNPJ_INVALIDO"}})
# L9 — dois fortes de empresas DIFERENTES: REVISAO_IDENTIDADE (dedup.rule: reportar, nao chutar).
linhas.append({"organizacao": {"cnpj": "$CNPJ_A", "domain": "clinicasl.com.br"}})
# L10 — copia EXATA da L1 dentro do MESMO lote: replay idempotente (mesmo estado, mesma chave).
linhas.append({"organizacao": {"domain": "valeforte.com.br"}})
with open(destino, "w", encoding="utf-8") as fh:
    for linha in linhas:
        fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
print(len(linhas))
PY
}

rodar_score() { # <relatorio> <args...>
  local relatorio="$1"; shift
  python3 "$CODIGO" --raiz "$RAIZ" --relatorio "$relatorio" "$@" </dev/null
}

veredito_do_relatorio() { # <relatorio> <veredito>
  python3 - "$1" "$2" <<'PY'
import json, sys
relatorio = json.load(open(sys.argv[1], encoding="utf-8"))
print(relatorio.get("por_veredito", {}).get(sys.argv[2], 0))
PY
}

valor_da_org() { # <organization_id> [linha]  (default: a ultima)
  local linha="${2:-1}"
  contagem "SELECT score_value FROM (SELECT score_value, row_number() OVER (ORDER BY calculated_at DESC, id) AS rn FROM sales_intelligence.scores WHERE organization_id='$1') t WHERE rn=$linha;"
}

CORR_R1="dddddddd-0000-4000-8000-000000000001"
CORR_R2="dddddddd-0000-4000-8000-000000000002"
CORR_R3="dddddddd-0000-4000-8000-000000000003"
CORR_R4="dddddddd-0000-4000-8000-000000000004"
FONTE="$TRABALHO/perfis.jsonl"

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== aceite do codigo sob teste: $CODIGO ($rotulo)"
  echo "== sha256 do codigo sob teste: $(sha256sum "$CODIGO" | cut -d' ' -f1)"
  echo "== sha256 do aceite: $(sha256sum "$0" | cut -d' ' -f1)"
  preparar_banco
  PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
  local cnpj_fora
  cnpj_fora="$(cnpj_inexistente)"
  escrever_fonte "$FONTE" "$cnpj_fora" >/dev/null

  # ---- rodada 1: o lote completo ---------------------------------------------------
  rodar_score "$TRABALHO/r1.json" --ambiente dev --correlation-id "$CORR_R1" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r1.out" 2>&1
  item "rodada1-exit-0" "0" "$?"
  item "rodada1-vereditos" \
    "CALCULADO=4 JA_CALCULADO=1 REVISAO_IDENTIDADE=1 RECUSADA=4 ERRO=0" \
    "CALCULADO=$(veredito_do_relatorio "$TRABALHO/r1.json" CALCULADO) JA_CALCULADO=$(veredito_do_relatorio "$TRABALHO/r1.json" JA_CALCULADO) REVISAO_IDENTIDADE=$(veredito_do_relatorio "$TRABALHO/r1.json" REVISAO_IDENTIDADE) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r1.json" RECUSADA) ERRO=$(veredito_do_relatorio "$TRABALHO/r1.json" ERRO)"
  item "rodada1-scores-total" "4" "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "rodada1-uuid-v4-no-produtor" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\$';")"
  # A1: score_type/score_version do contrato, valor da formula declarada, carimbos.
  item "rodada1-tipo-e-versao" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='AUTOMATION_FIT' AND score_version='automation-fit-v1' AND calculated_at IS NOT NULL;")"
  item "rodada1-valor-org-a" "85.00" "$(valor_da_org "$ORG_A")"
  item "rodada1-valor-org-b" "48.50" "$(valor_da_org "$ORG_B")"
  item "rodada1-valor-org-d" "83.00" "$(valor_da_org "$ORG_D")"
  item "rodada1-valor-org-e-ignora-a-fonte" "30.00" "$(valor_da_org "$ORG_E")"
  item "rodada1-valid-until-nulo" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE valid_until IS NULL;")"
  # A3: explicacao auditavel — cobertura, componentes com peso e o snapshot medido.
  item "rodada1-explicacao-completa" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE explanation ? 'componentes' AND explanation ? 'cobertura' AND explanation ? 'pesos' AND explanation->>'formula' LIKE '100 * SUM%';")"
  item "rodada1-cinco-componentes" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE (SELECT count(*) FROM jsonb_object_keys(explanation->'componentes')) = 5;")"
  item "rodada1-cobertura-org-a" "0.80" \
    "$(contagem "SELECT explanation->>'cobertura' FROM sales_intelligence.scores WHERE organization_id='$ORG_A';")"
  item "rodada1-cobertura-org-b" "1.00" \
    "$(contagem "SELECT explanation->>'cobertura' FROM sales_intelligence.scores WHERE organization_id='$ORG_B';")"
  item "rodada1-ausente-nao-vota-org-a" "prontidao_tecnologica" \
    "$(contagem "SELECT inputs->>'ausentes' FROM sales_intelligence.scores WHERE organization_id='$ORG_A';" | tr -d '[]\"')"
  item "rodada1-inputs-snapshot-e-hash" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE inputs->>'input_hash' ~ '^[0-9a-f]{64}\$' AND inputs->>'cobertura' IS NOT NULL AND inputs->>'organization_id' IS NOT NULL;")"
  item "rodada1-inputs-sem-dado-de-outra-empresa" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE inputs->>'organization_id' <> organization_id::text;")"
  # A2: sinal/hipotese de OUTRA empresa nao entra na conta (leitura filtrada por organization_id).
  item "rodada1-le-so-os-sinais-da-empresa" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND (SELECT count(*) FROM jsonb_array_elements(inputs->'sinais')) = 3 AND inputs->'sinais' @> '[{\"id\":\"$SIG_A1\"},{\"id\":\"$SIG_A2\"},{\"id\":\"$SIG_A3\"}]';")"
  item "rodada1-tipo-sem-pontos-nao-soma" "85.00" "$(valor_da_org "$ORG_A")"
  item "rodada1-nao-le-sinal-de-terceiro" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND inputs->'sinais' @> '[{\"id\":\"$SIG_B1\"}]';")"
  item "rodada1-le-so-as-hipoteses-da-empresa" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A' AND (SELECT count(*) FROM jsonb_array_elements(inputs->'hipoteses')) = 1 AND inputs->'hipoteses' @> '[{\"id\":\"$HIP_A1\"}]' AND (inputs->'hipoteses'->0->>'impacto')::numeric = 90 AND NOT (inputs->'hipoteses' @> '[{\"id\":\"$HIP_B1\"}]');")"
  # A4/A10: auditoria por pedido, claim de sincronizacao amarrado ao score, fila humana.
  item "rodada1-agent-runs-por-pedido" "10" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='automation_fit' AND agent_role='automation_scoring' AND agent_version='1.0.0' AND correlation_id='$CORR_R1';")"
  item "rodada1-agent-runs-completed" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='COMPLETED';")"
  item "rodada1-agent-runs-rejected" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='REJECTED';")"
  item "rodada1-agent-runs-review-required" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='REVIEW_REQUIRED';")"
  item "rodada1-auditoria-aponta-o-score" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs a JOIN sales_intelligence.scores s ON (a.output->>'score_id') = s.id::text WHERE a.correlation_id='$CORR_R1';")"
  item "rodada1-descartados-na-auditoria" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND output->'descartados' @> '[{\"motivo\":\"CAMPO_NAO_DECLARADO\"}]';")"
  item "rodada1-sync-events-success" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='AUTOMATION_FIT' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_R1';")"
  item "rodada1-sync-event-amarra-o-score" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events e JOIN sales_intelligence.scores s ON e.entity_id = s.id WHERE e.operation='AUTOMATION_FIT' AND e.status='SUCCESS';")"
  item "rodada1-fila-humana-pendente" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='AUTOMATION_FIT_IDENTITY_REVIEW' AND status='PENDING';")"
  item "rodada1-fila-humana-com-duas-casadas" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='AUTOMATION_FIT_IDENTITY_REVIEW' AND jsonb_array_length(proposed_action->'organizacoes_casadas')=2;")"
  item "rodada1-sync-events-review" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='AUTOMATION_FIT_REVIEW' AND status='SUCCESS';")"
  # A6: NENHUMA escrita no estado que o score LE (organizations/signals/pain_hypotheses).
  item "rodada1-organizacoes-total" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "rodada1-nao-toca-a-empresa" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE updated_at = '2000-01-01T00:00:00Z' AND status='DISCOVERED';")"
  item "rodada1-nao-escreve-data-quality-score" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE data_quality_score IS NOT NULL;")"
  item "rodada1-porte-intacto" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE (id='$ORG_A' AND employee_band='300_499' AND unit_count=4) OR (id='$ORG_B' AND employee_band='GT_1000') OR (id='$ORG_D' AND employee_band='150_299') OR (id='$ORG_E' AND employee_band='70_149' AND unit_count=1) OR (id='$ORG_C' AND employee_band='LT_70');")"
  item "rodada1-sinais-intactos" "$SINAIS_SEMEADOS" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "rodada1-hipoteses-intactas" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  item "rodada1-nenhuma-outra-tabela-escrita" "0" \
    "$(contagem "SELECT (SELECT count(*) FROM sales_intelligence.research_runs) + (SELECT count(*) FROM sales_intelligence.contacts) + (SELECT count(*) FROM sales_intelligence.interactions) + (SELECT count(*) FROM sales_intelligence.recommendations) + (SELECT count(*) FROM sales_intelligence.outbox_events);")"
  # A6/AC5: a recusa por SEM_LASTRO e' explicita e nao escreveu nada.
  item "rodada1-recusa-sem-lastro" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND output->'motivos' @> '[\"SEM_LASTRO\"]';")"
  item "rodada1-nao-escreve-score-da-org-c" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_C';")"
  item "rodada1-recusa-empresa-inexistente" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND output->'motivos' @> '[\"ORGANIZACAO_NAO_ENCONTRADA\"]';")"
  item "rodada1-recusa-sem-forte-e-invalido" "2" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND (output->'motivos' @> '[\"SEM_IDENTIFICADOR_FORTE\"]' OR output->'motivos' @> '[\"IDENTIFICADOR_FORTE_INVALIDO\"]');")"

  # ---- rodada 2: MESMO estado — replay nao duplica ---------------------------------
  rodar_score "$TRABALHO/r2.json" --ambiente dev --correlation-id "$CORR_R2" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r2.out" 2>&1
  item "rodada2-exit-0" "0" "$?"
  item "rodada2-replay-nao-duplica" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "rodada2-vereditos" "5" \
    "$(veredito_do_relatorio "$TRABALHO/r2.json" JA_CALCULADO)"
  item "rodada2-sync-events-nao-duplica" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='AUTOMATION_FIT' AND status='SUCCESS';")"

  # ---- rodada 3: ESTADO NOVO (sinal novo) => LINHA NOVA (score e' historico) --------
  psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.signals
  (id, organization_id, signal_type, signal_category, source_type, detected_at, created_at)
VALUES ('33333333-0000-4000-8000-0000000000a9', '$ORG_A', 'AI_INITIATIVE', 'TECNOLOGIA',
        'LINKEDIN', '2026-09-20T10:00:00Z', '2026-09-20T10:00:00Z');
SQL
  rodar_score "$TRABALHO/r3.json" --ambiente dev --correlation-id "$CORR_R3" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r3.out" 2>&1
  item "rodada3-exit-0" "0" "$?"
  item "rodada3-estado-novo-cria-linha" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "rodada3-vereditos" "CALCULADO=1 JA_CALCULADO=4" \
    "CALCULADO=$(veredito_do_relatorio "$TRABALHO/r3.json" CALCULADO) JA_CALCULADO=$(veredito_do_relatorio "$TRABALHO/r3.json" JA_CALCULADO)"
  item "rodada3-historico-org-a" "2" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id='$ORG_A';")"
  item "rodada3-valor-novo-org-a" "76.00" "$(valor_da_org "$ORG_A")"
  item "rodada3-valor-antigo-preservado" "85.00" "$(valor_da_org "$ORG_A" 2)"
  item "rodada3-cobertura-nova-org-a" "1.00" \
    "$(contagem "SELECT explanation->>'cobertura' FROM sales_intelligence.scores WHERE organization_id='$ORG_A' ORDER BY calculated_at DESC LIMIT 1;")"

  # ---- rodada 4: MESMO estado da rodada 3 — replay ---------------------------------
  rodar_score "$TRABALHO/r4.json" --ambiente dev --correlation-id "$CORR_R4" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r4.out" 2>&1
  item "rodada4-exit-0" "0" "$?"
  item "rodada4-replay-nao-duplica" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "rodada4-todos-replay" "0" \
    "$(veredito_do_relatorio "$TRABALHO/r4.json" CALCULADO)"

  # ---- A9: discriminacao MEDIDA nas linhas gravadas (a ultima por empresa) ---------
  # Estado medido aqui: A=76,00 (faixa 3) · B=48,50 (2) · D=83,00 (4) · E=30,00 (1).
  item "discriminacao-faixas-distintas" "4" \
    "$(contagem "SELECT count(DISTINCT floor(score_value/20)) FROM (SELECT DISTINCT ON (organization_id) score_value FROM sales_intelligence.scores ORDER BY organization_id, calculated_at DESC) t;" )"
  item "discriminacao-margem-minima" "SIM" \
    "$(contagem "SELECT CASE WHEN (max(score_value)-min(score_value)) >= 30 THEN 'SIM' ELSE 'NAO' END FROM (SELECT DISTINCT ON (organization_id) score_value FROM sales_intelligence.scores ORDER BY organization_id, calculated_at DESC) t;")"
  item "discriminacao-desvio-da-constante" "SIM" \
    "$(contagem "SELECT CASE WHEN avg(abs(score_value-50)) >= 10 THEN 'SIM' ELSE 'NAO' END FROM (SELECT DISTINCT ON (organization_id) score_value FROM sales_intelligence.scores ORDER BY organization_id, calculated_at DESC) t;")"

  # ---- guardas: prod recusado e --planejar sem porta -------------------------------
  local antes antes_runs
  antes="$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  antes_runs="$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs;")"
  rodar_score "$TRABALHO/prod.json" --ambiente prod --fonte "$FONTE" \
    --prefixo "$PREFIXO" > "$TRABALHO/prod.out" 2>&1
  item "prod-recusado-exit-4" "4" "$?"
  # "sem escrita" mede as DUAS coisas que prod poderia sujar: linha de score E auditoria. O score
  # sozinho nao basta como testemunha: no replay a chave ja existe e nada seria inserido mesmo com a
  # guarda desligada — a auditoria (agent_runs) pega a rodada que rodou onde nao devia.
  item "prod-recusado-sem-escrita" "$antes|$antes_runs" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")|$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs;")"
  grep -q "ADR-005" "$TRABALHO/prod.out" && item "prod-recusado-cita-adr" "1" "1" \
    || item "prod-recusado-cita-adr" "1" "0"
  # --planejar NAO abre conexao: sem --prefixo (e com um prefixo que nao existe) ele nao estoura.
  python3 "$CODIGO" --raiz "$RAIZ" --planejar --fonte "$FONTE" </dev/null > "$TRABALHO/planejar.out" 2>&1
  item "planejar-exit-0-sem-porta" "0" "$?"
  item "planejar-planeja-os-validos" "8" \
    "$(python3 - "$TRABALHO/planejar.out" <<'PY'
import json, sys
linha = [l for l in open(sys.argv[1], encoding="utf-8").read().splitlines() if l.startswith("{")]
dados = json.loads(linha[0]) if linha else {}
print(dados.get("por_veredito", {}).get("PLANEJADO_CALCULAR", 0))
PY
)"
  python3 "$CODIGO" --raiz "$RAIZ" --planejar --fonte "$FONTE" \
    --prefixo "docker exec -i container-que-nao-existe psql -U x" </dev/null >/dev/null 2>&1
  item "planejar-ignora-a-porta" "0" "$?"

  # ---- desfazer: dry-run nao apaga; --confirmo apaga SO a rodada -------------------
  rodar_score "$TRABALHO/dry.json" --ambiente dev --desfazer "$CORR_R3" --prefixo "$PREFIXO" > "$TRABALHO/dry.out" 2>&1
  item "desfazer-dry-run-exit-0" "0" "$?"
  item "desfazer-dry-run-nao-apaga" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "desfazer-dry-run-lista-um" "1" \
    "$(python3 - "$TRABALHO/dry.json" <<'PY'
import json, sys
print(len(json.load(open(sys.argv[1], encoding="utf-8")).get("scores", [])))
PY
)"
  rodar_score "$TRABALHO/undo.json" --ambiente dev --desfazer "$CORR_R3" --confirmo --prefixo "$PREFIXO" > "$TRABALHO/undo.out" 2>&1
  item "desfazer-confirmo-exit-0" "0" "$?"
  item "desfazer-apagou-so-a-rodada" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.scores;")"
  item "desfazer-preserva-a-empresa" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE updated_at='2000-01-01T00:00:00Z';")"
  item "desfazer-preserva-os-sinais" "$SINAIS_APOS_A3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "desfazer-preserva-as-hipoteses" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  item "desfazer-preserva-a-auditoria" "40" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='automation_fit';")"
  item "desfazer-registra-rollback" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='ROLLBACK' AND source_version='1.0.0';")"

  echo "RESULTADO: ACEITE_AUTOMATION_FIT_001_$( [ "$ITENS_FALHOU" -eq 0 ] && echo OK || echo FALHOU ) ($((ITENS_OK + ITENS_FALHOU)) itens, $ITENS_FALHOU falhas)"
  [ "$ITENS_FALHOU" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
# Prova de dente: mutacao em copia do componente TEM de reprovar o item esperado
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
    grep '^FALHOU' "$TRABALHO/dente-baseline.out" | head -5
    return 1
  fi

  # Lista lida ANTES do laco (o corpo chama `docker exec -i`, que consome o stdin do laco).
  # Formato: nome|alvo|substituto|itens-esperados.
  local linhas=() linha
  while IFS= read -r linha; do
    [ -n "$linha" ] && linhas+=("$linha")
  done <<'EOF'
cobertura-ignorada|    if cobertura < COBERTURA_MINIMA:|    if False:|rodada1-recusa-sem-lastro,rodada1-nao-escreve-score-da-org-c
score-constante|    saida["valor"] = arredondar(Decimal("100") * numerador / cobertura)|    saida["valor"] = arredondar(Decimal("50"))|rodada1-valor-org-a
idempotencia-sem-o-estado|    return "score:%s:org:%s:%s" % (SCORE_TYPE, organizacao_id, entrada_hash)|    return "score:%s:org:%s" % (SCORE_TYPE, organizacao_id)|rodada3-estado-novo-cria-linha,rodada3-historico-org-a
leitura-sem-filtro-de-empresa|"FROM %s WHERE organization_id = %s ORDER BY detected_at, id;"|"FROM %s WHERE %s IS NOT NULL ORDER BY detected_at, id;"|rodada1-valor-org-a,rodada1-le-so-os-sinais-da-empresa
prod-liberado|        if self.ambiente == AMBIENTE_RECUSADO:\n            raise RecusaDeAmbiente(\n                "Automation Fit Score v1 nao escreve em prod (ADR-005): a promocao exige card "\n                "proprio com aprovacao humana registrada")\n        if self.ambiente not in AMBIENTES_PERMITIDOS:\n            raise RecusaDeAmbiente("ambiente desconhecido: %r" % self.ambiente)\n        return self.ambiente|        return self.ambiente|prod-recusado-exit-4,prod-recusado-sem-escrita,prod-recusado-cita-adr
EOF

  local total="${#linhas[@]}" detectadas=0 falhas=0
  local nome alvo substituto esperados destino guardado faltando esperado
  for linha in "${linhas[@]}"; do
    IFS='|' read -r nome alvo substituto esperados <<< "$linha"
    [ -z "$nome" ] && continue
    alvo="$(printf '%b' "$alvo")"
    substituto="$(printf '%b' "$substituto")"
    destino="$TRABALHO/mut-$nome/automation_fit.py"
    mkdir -p "$(dirname "$destino")"
    if ! aplicar_mutacao "$CODIGO" "$destino" "$alvo" "$substituto" | grep -q MUTACAO_APLICADA; then
      echo "FALHOU mutacao $nome NAO se aplicou (ancora mudou) — buraco de verificacao"
      falhas=$((falhas + 1)); continue
    fi
    echo
    echo "-- mutacao: $nome (tem de reprovar: $(echo "$esperados" | tr ',' ' '))"
    guardado="$CODIGO"
    CODIGO="$destino"
    if rodar_aceite "mutacao $nome" > "$TRABALHO/mut-$nome.out" 2>&1; then
      echo "FALHOU mutacao $nome NAO foi detectada pelo aceite"
      falhas=$((falhas + 1))
    else
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
    CODIGO="$guardado"
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
  echo "== ACEITE DO AUTOMATION FIT SCORE v1 (TRE-W5-E02-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0
  if rodar_aceite "principal"; then ok=1; fi
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_AUTOMATION_FIT_001_OK"
    return 0
  fi
  echo "ACEITE_AUTOMATION_FIT_001_FALHOU"
  return 1
}

principal
