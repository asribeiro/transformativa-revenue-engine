#!/usr/bin/env bash
# teste_pain_hypothesis_aceite.sh [--prova-de-dente] [--manter] [--codigo <pain_hypothesis.py>]
#
# ACEITE E2E do AGENTE PAIN HYPOTHESIS v1 (card TRE-W4-E04-T01) — PostgreSQL descartavel.
#
# Roda na VPS (o container do Hermes nao tem daemon Docker). NUNCA toca `pg-sales-dev`,
# `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio (`pg-pain-acc`), aplica a
# migration 0001 e, no fim, remove o container e o diretorio de trabalho. Se o container ja existir,
# ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (contrato `agente-pain-hypothesis-v1.json`):
#   1. hipotese COM lastro grava `pain_hypotheses` marcada como INFERENCIA, com status inicial,
#      categoria do vocabulario e o lastro amarrado as origens reais (signal_type/signal_category/
#      event_date e research_type/status conservados no evidence);
#   2. hipotese SEM lastro NAO e gravada (`SEM_EVIDENCIA_VALIDA`) e nada e escrito em
#      `organizations` / `signals` / `research_runs` (o registro e aditivo);
#   3. nenhuma coluna proibida e escrita (business_impact_score / estimated_impact_description /
#      validated_at ficam NULL em TODAS) e o status e SEMPRE `HYPOTHESIS`;
#   4. descartes com motivo (evidencia inexistente, evidencia de OUTRA empresa, research_run
#      inexistente, confianca fora da faixa, derivado declarado, campo inventado, resumo acima do
#      limite) ficam medidos no proprio evidence;
#   5. retry nao duplica (rodada 2) e replay com a hipotese ausente nao vira erro (rodada 3);
#   6. `prod` e recusado sem escrita (exit 4) e `--planejar` nao abre conexao;
#   7. desfazer dry-run nao apaga; `--confirmo` apaga SO o que a rodada criou e ainda existe,
#      registra ROLLBACK, preservando `organizations`, `research_runs`, `signals`, `agent_runs`
#      e `human_approvals`;
#   8. veredito ..... ACEITE_PAIN_001_OK / ACEITE_PAIN_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do pain_hypothesis.py (idempotencia, fechamento sem
#   ancora na hipotese, lastro nao conferido, lastro de outra empresa aceito, status chumbado) e
#   exige que o aceite reprove O ITEM ESPERADO de cada uma — nao basta "o aceite falhou".
#
# Licoes ja pagas (mantidas aqui de proposito): todo `docker exec -i` que nao le stdin leva
# `</dev/null`, senao ele CONSOME o stdin do laco de mutacoes e mata as iteracoes seguintes; as
# mutacoes sao lidas numa LISTA antes do laco; `pg_isready` mente no inicio, entao a espera e por
# `SELECT 1` funcionando DUAS vezes.
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_PAIN_CONTAINER (default pg-pain-acc), TRE_PAIN_TRABALHO.
# Exit: 0 = ACEITE_PAIN_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_PAIN_CONTAINER:-pg-pain-acc}"
TRABALHO="${TRE_PAIN_TRABALHO:-/tmp/pain-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="pain-aceite-descartavel"
PAIN_PY="$RAIZ/hermes/agents/pain_hypothesis/pain_hypothesis.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; PAIN_PY="${1:?--codigo exige caminho}" ;;
    --codigo=*) PAIN_PY="${1#--codigo=}" ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <pain_hypothesis.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <pain_hypothesis.py>]"; exit 2 ;;
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
[ -f "$PAIN_PY" ] || { echo "FALHOU agente ausente: $PAIN_PY"; exit 2; }
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

# ---- fixture: 3 organizacoes, 3 research_runs e 4 signals (UUID fixo) -------------------
ORG_A="aaaaaaaa-0000-4000-8000-000000000001"
ORG_B="aaaaaaaa-0000-4000-8000-000000000002"
ORG_C="aaaaaaaa-0000-4000-8000-000000000003"
RES_A="eeeeeeee-0000-4000-8000-0000000000a1"
RES_B="eeeeeeee-0000-4000-8000-0000000000b1"
RES_C="eeeeeeee-0000-4000-8000-0000000000c1"
SIG_A1="11111111-0000-4000-8000-0000000000a1"
SIG_A2="11111111-0000-4000-8000-0000000000a2"
SIG_B1="22222222-0000-4000-8000-0000000000b1"
SIG_C1="33333333-0000-4000-8000-0000000000c1"
# Evidencias/vinculos DECLARADOS na fonte que NAO existem no banco (bem formados, empresa "fantasma").
RES_FANTASMA="eeeeeeee-0000-4000-8000-0000000000ff"
EVID_PESQ_FANTASMA="77777777-0000-4000-8000-0000000000f1"
EVID_SINAL_FANTASMA="88888888-0000-4000-8000-0000000000f1"
EVID_SINAL_SEM_LASTRO="99999999-0000-4000-8000-0000000000f1"
CNPJ_ORG_A="11.222.333/0001-81"
CNPJ_ORG_B="45.723.174/0001-10"
CNPJ_INVALIDO="11.222.333/0001-00"

preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
  # `updated_at` FIXO num passado distante de proposito: se o agente encostar em qualquer coluna de
  # `organizations`, o valor muda e o aceite acusa.
  psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, cnpj, domain, linkedin_url, industry_name, status, source,
   created_at, updated_at)
VALUES
  ('$ORG_A', 'Metalurgica Vale Forte Ltda', 'Vale Forte', '11222333000181', 'valeforte.com.br',
   NULL, 'Metalurgia', 'DISCOVERED', 'LINKEDIN', '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z'),
  ('$ORG_B', 'AgroSmart Analytics Ltda', 'AgroSmart Analytics', '45723174000110',
   'agrosmart-analytics.com.br', NULL, NULL, 'DISCOVERED', 'WEB',
   '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z'),
  ('$ORG_C', 'Clinica Sao Lucas S.A.', 'Clinica Sao Lucas', NULL, NULL,
   'https://www.linkedin.com/company/clinica-sao-lucas', NULL, 'DISCOVERED', 'EVENTOS',
   '2026-01-05T10:00:00Z', '2000-01-01T00:00:00Z');

INSERT INTO sales_intelligence.research_runs
  (id, organization_id, agent_name, agent_version, research_type, status, started_at, completed_at,
   source_count, structured_output, created_at)
VALUES
  ('$RES_A', '$ORG_A', 'research', '1.0.0', 'COMPANY_PROFILE', 'COMPLETED',
   '2026-01-06T09:00:00Z', '2026-01-06T09:05:00Z', 1, '{"fontes": []}'::jsonb,
   '2026-01-06T09:00:00Z'),
  ('$RES_B', '$ORG_B', 'research', '1.0.0', 'COMPANY_PROFILE', 'COMPLETED',
   '2026-01-06T09:10:00Z', '2026-01-06T09:15:00Z', 1, '{"fontes": []}'::jsonb,
   '2026-01-06T09:10:00Z'),
  ('$RES_C', '$ORG_C', 'research', '1.0.0', 'COMPANY_PROFILE', 'COMPLETED',
   '2026-01-06T09:20:00Z', '2026-01-06T09:25:00Z', 1, '{"fontes": []}'::jsonb,
   '2026-01-06T09:20:00Z');

INSERT INTO sales_intelligence.signals
  (id, organization_id, signal_type, signal_category, title, event_date, detected_at, created_at)
VALUES
  ('$SIG_A1', '$ORG_A', 'HIRING', 'EXPANSAO', '40 vagas de logistica',
   '2026-09-28T00:00:00Z', '2026-09-29T10:00:00Z', '2026-09-29T10:00:00Z'),
  ('$SIG_A2', '$ORG_A', 'ERP_CHANGE', 'TECNOLOGIA', 'Migracao de ERP',
   '2026-09-20T00:00:00Z', '2026-09-21T10:00:00Z', '2026-09-21T10:00:00Z'),
  ('$SIG_B1', '$ORG_B', 'AI_INITIATIVE', 'TECNOLOGIA', 'Projeto de IA',
   '2026-09-25T00:00:00Z', '2026-09-26T10:00:00Z', '2026-09-26T10:00:00Z'),
  ('$SIG_C1', '$ORG_C', 'COST_REDUCTION', 'EFICIENCIA', 'Programa de reducao de custos',
   '2026-09-22T00:00:00Z', '2026-09-23T10:00:00Z', '2026-09-23T10:00:00Z');
SQL
}

# CNPJ com digito verificador VALIDO que NAO existe na base: e a prova de ORGANIZACAO_NAO_ENCONTRADA.
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

# A fonte (14 linhas) e' escrita por python; as variaveis de bash (UUIDs/CNPJ) entram por expansao.
escrever_fonte() { # <arquivo> <cnpj-inexistente>
  python3 - "$1" "$2" <<PY
import json, sys
destino, cnpj_inexistente = sys.argv[1], sys.argv[2]
linhas = []
# L1 — ORG_A por domain, FINANCEIRO, lastro [SINAL SIG_A1, PESQUISA RES_A], vinculo com RES_A.
l1 = {"organizacao": {"domain": "valeforte.com.br"},
      "dor": "A conciliacao de recebiveis e manual em 4 sistemas e trava o fechamento mensal",
      "categoria": "FINANCEIRO",
      "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"},
                     {"tipo": "PESQUISA", "id": "$RES_A"}],
      "resumo_da_evidencia": "Vaga exige conciliacao manual entre 4 sistemas",
      "confianca": 0.8, "research_run_id": "$RES_A"}
linhas.append(l1)
# L2 — ORG_B por CNPJ com pontuacao (prova a normalizacao da identidade).
linhas.append({"organizacao": {"cnpj": "$CNPJ_ORG_B"},
               "dor": "CRM desatualizado e follow-up manual fazem perder leads",
               "categoria": "COMERCIAL",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_B1"}], "confianca": 0.7})
# L3 — ORG_C por LinkedIn "sujo" (www + br + barra final); lastro e' PESQUISA.
linhas.append({"organizacao": {"linkedin_url": "https://br.linkedin.com/company/clinica-sao-lucas/"},
               "dor": "Grande volume de WhatsApp com respostas repetitivas e SLA elevado",
               "categoria": "ATENDIMENTO",
               "evidencias": [{"tipo": "PESQUISA", "id": "$RES_C"}], "confianca": 0.6})
# L4 — TUDO o que se descarta: evidencia inexistente, evidencia de OUTRA empresa, confianca fora da
# faixa, status derivado declarado, campo inventado, resumo acima do limite e research_run inexistente.
# Sobra SO o SINAL SIG_A2 (ORG_A): a hipotese SEGUE.
linhas.append({"organizacao": {"domain": "valeforte.com.br"},
               "dor": "Planilhas e transferencias manuais entre areas travam a operacao",
               "categoria": "OPERACOES",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A2"},
                              {"tipo": "PESQUISA", "id": "$EVID_PESQ_FANTASMA"},
                              {"tipo": "SINAL", "id": "$SIG_B1"},
                              {"tipo": "SINAL", "id": "$EVID_SINAL_FANTASMA"}],
               "resumo_da_evidencia": "R" * 4001, "confianca": 1.7, "status": "VALIDATED",
               "employee_count": 50, "research_run_id": "$RES_FANTASMA"})
# L5 — so evidencia inexistente: RECUSADA SEM_EVIDENCIA_VALIDA, sem escrever linha.
linhas.append({"organizacao": {"domain": "valeforte.com.br"},
               "dor": "Contratos e NFs conferidos manualmente um a um",
               "categoria": "DOCUMENTOS",
               "evidencias": [{"tipo": "SINAL", "id": "$EVID_SINAL_SEM_LASTRO"}]})
# L6 — copia EXATA da L1 dentro do MESMO lote: replay idempotente.
linhas.append(dict(l1))
# L7..L14 — as 8 recusas e a revisao de identidade.
linhas.append({"organizacao": {"city": "Santos"},
               "dor": "Sem identificador forte nao ha empresa resolvida",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"}]})
linhas.append({"organizacao": {"domain": "valeforte.com.br"},
               "dor": "Categoria de dor fora do vocabulario da baseline",
               "categoria": "FINANCEIRO_INVENTADO",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"}]})
linhas.append({"organizacao": {"domain": "valeforte.com.br"},
               "categoria": "FINANCEIRO",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"}]})
linhas.append({"organizacao": {"cnpj": "$CNPJ_INVALIDO"},
               "dor": "CNPJ com digito verificador errado",
               "categoria": "FINANCEIRO",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"}]})
linhas.append({"organizacao": {"cnpj": cnpj_inexistente},
               "dor": "CNPJ valido de empresa que nao esta na base",
               "categoria": "FINANCEIRO",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"}]})
linhas.append({"organizacao": {"cnpj": "$CNPJ_ORG_A", "domain": "agrosmart-analytics.com.br"},
               "dor": "Dois identificadores fortes de empresas diferentes",
               "categoria": "FINANCEIRO",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"}]})
linhas.append({"organizacao": {"domain": "valeforte.com.br"},
               "dor": "D" * 4500, "categoria": "FINANCEIRO",
               "evidencias": [{"tipo": "SINAL", "id": "$SIG_A1"}]})
linhas.append({"organizacao": {"domain": "valeforte.com.br"},
               "dor": "Tipo de evidencia fora do vocabulario",
               "categoria": "FINANCEIRO",
               "evidencias": [{"tipo": "PANFLETO", "id": "$SIG_A1"}]})
with open(destino, "w", encoding="utf-8") as fh:
    for linha in linhas:
        fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
print(len(linhas))
PY
}

rodar_pain() { # <relatorio> <args...>
  local relatorio="$1"; shift
  python3 "$PAIN_PY" --raiz "$RAIZ" --relatorio "$relatorio" "$@" </dev/null
}

veredito_do_relatorio() { # <relatorio> <veredito>
  python3 - "$1" "$2" <<'PY'
import json, sys
relatorio = json.load(open(sys.argv[1], encoding="utf-8"))
print(relatorio.get("por_veredito", {}).get(sys.argv[2], 0))
PY
}

alvo_do_relatorio() { # <relatorio> <chave>
  python3 - "$1" "$2" <<'PY'
import json, sys
relatorio = json.load(open(sys.argv[1], encoding="utf-8"))
print((relatorio.get("alvo") or {}).get(sys.argv[2]) or "")
PY
}

CORR_R1="dddddddd-0000-4000-8000-000000000001"
CORR_R2="dddddddd-0000-4000-8000-000000000002"
CORR_R3="dddddddd-0000-4000-8000-000000000003"
CORR_R4="dddddddd-0000-4000-8000-000000000004"
FONTE="$TRABALHO/hipoteses.jsonl"

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== aceite do codigo sob teste: $PAIN_PY ($rotulo)"
  preparar_banco
  PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
  local cnpj_fora
  cnpj_fora="$(cnpj_inexistente)"
  escrever_fonte "$FONTE" "$cnpj_fora" >/dev/null

  # ---- rodada 1 (14 linhas) ---------------------------------------------------------
  rodar_pain "$TRABALHO/r1.json" --ambiente dev --correlation-id "$CORR_R1" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r1.out" 2>&1
  item "rodada1-exit-0" "0" "$?"
  item "rodada1-vereditos" \
    "REGISTRADA=4 JA_REGISTRADA=1 REVISAO_IDENTIDADE=1 RECUSADA=8 ERRO=0" \
    "REGISTRADA=$(veredito_do_relatorio "$TRABALHO/r1.json" REGISTRADA) JA_REGISTRADA=$(veredito_do_relatorio "$TRABALHO/r1.json" JA_REGISTRADA) REVISAO_IDENTIDADE=$(veredito_do_relatorio "$TRABALHO/r1.json" REVISAO_IDENTIDADE) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r1.json" RECUSADA) ERRO=$(veredito_do_relatorio "$TRABALHO/r1.json" ERRO)"
  # Identidade do alvo medido (vem do relatorio do agente).
  item "rodada1-alvo-banco" "$BANCO" "$(alvo_do_relatorio "$TRABALHO/r1.json" banco)"
  item "rodada1-alvo-usuario" "$USUARIO" "$(alvo_do_relatorio "$TRABALHO/r1.json" usuario)"
  # 1..4: as 4 hipoteses gravadas — dono, enunciado, UUID v4 e status inicial.
  item "rodada1-hipoteses-total" "4" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  item "rodada1-org-a" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_A';")"
  item "rodada1-org-b" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_B';")"
  item "rodada1-org-c" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_C';")"
  item "rodada1-pain-statement-preenchido" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_statement IS NOT NULL AND pain_statement <> '';")"
  item "rodada1-uuid-v4-no-produtor" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\$';")"
  item "rodada1-status-inicial" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE status='HYPOTHESIS';")"
  # Categoria do vocabulario da baseline (doc 01 §5) e sem nenhuma inventada.
  item "rodada1-categoria-no-vocabulario" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category IN ('FINANCEIRO','COMERCIAL','ATENDIMENTO','OPERACOES','DOCUMENTOS');")"
  item "rodada1-categoria-fora-do-vocabulario" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category IS NOT NULL AND pain_category NOT IN ('FINANCEIRO','COMERCIAL','ATENDIMENTO','OPERACOES','DOCUMENTOS');")"
  item "rodada1-categoria-financeiro" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category='FINANCEIRO';")"
  item "rodada1-categoria-comercial" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category='COMERCIAL';")"
  item "rodada1-categoria-atendimento" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category='ATENDIMENTO';")"
  item "rodada1-categoria-operacoes" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category='OPERACOES';")"
  # 3: NENHUMA coluna de impacto/validacao escrita, em hipotese nenhuma.
  item "rodada1-nenhuma-coluna-proibida" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE business_impact_score IS NOT NULL OR estimated_impact_description IS NOT NULL OR validated_at IS NOT NULL;")"
  # A inferencia vai MARCADA como inferencia, com o lastro e o input_hash na propria linha.
  item "rodada1-evidence-marcada-inferencia" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE (evidence->>'inferencia')::boolean IS TRUE AND (evidence->>'marcada_como_inferencia')::boolean IS TRUE;")"
  item "rodada1-evidence-com-chaves" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE evidence ? 'evidencias' AND evidence ? 'evidencia_primaria' AND evidence ? 'input_hash';")"
  item "rodada1-evidencia-primaria-presente" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE jsonb_typeof(evidence->'evidencia_primaria') = 'object';")"
  # 1/2: o lastro de cada hipotese aponta as ORIGENS SEMEADAS (ids reais, com o fato conservado).
  item "rodada1-lastro-l1-sinal" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_A' AND pain_category='FINANCEIRO' AND evidence->'evidencias' @> '[{\"tipo\":\"SINAL\",\"id\":\"$SIG_A1\",\"signal_type\":\"HIRING\",\"signal_category\":\"EXPANSAO\"}]'::jsonb;")"
  item "rodada1-lastro-l1-pesquisa" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_A' AND pain_category='FINANCEIRO' AND evidence->'evidencias' @> '[{\"tipo\":\"PESQUISA\",\"id\":\"$RES_A\"}]'::jsonb;")"
  item "rodada1-lastro-l1-data-conservada" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_A' AND pain_category='FINANCEIRO' AND (evidence->'evidencias'->0->>'event_date') LIKE '2026-09-28%';")"
  item "rodada1-vinculo-research-run-real" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category='FINANCEIRO' AND research_run_id='$RES_A';")"
  item "rodada1-lastro-l2-sinal" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_B' AND pain_category='COMERCIAL' AND evidence->'evidencias' @> '[{\"tipo\":\"SINAL\",\"id\":\"$SIG_B1\",\"signal_type\":\"AI_INITIATIVE\"}]'::jsonb;")"
  item "rodada1-lastro-l3-pesquisa-fato" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_C' AND pain_category='ATENDIMENTO' AND evidence->'evidencias' @> '[{\"tipo\":\"PESQUISA\",\"id\":\"$RES_C\",\"research_type\":\"COMPANY_PROFILE\",\"status\":\"COMPLETED\"}]'::jsonb;")"
  item "rodada1-lastro-l4-so-o-valido" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_A' AND pain_category='OPERACOES' AND evidence->'evidencias' @> '[{\"tipo\":\"SINAL\",\"id\":\"$SIG_A2\"}]'::jsonb AND NOT (evidence->'evidencias' @> '[{\"id\":\"$SIG_B1\"}]'::jsonb);")"
  item "rodada1-lastro-l4-sem-vinculo-quebrado" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE pain_category='OPERACOES' AND research_run_id IS NULL AND evidence->'descartados' @> '[{\"motivo\":\"RESEARCH_RUN_NAO_ENCONTRADO\"}]'::jsonb;")"
  # 4: descartes com motivo, medidos na propria linha (nao na narrativa de quem rodou).
  item "rodada1-descarte-de-evidencia-de-outra-empresa" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE evidence->'descartados' @> '[{\"motivo\":\"EVIDENCIA_DE_OUTRA_ORGANIZACAO\"}]'::jsonb;")"
  item "rodada1-descarte-de-evidencia-nao-encontrada" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE evidence->'descartados' @> '[{\"motivo\":\"EVIDENCIA_NAO_ENCONTRADA\"}]'::jsonb;")"
  item "rodada1-descarte-de-confianca-fora-da-faixa" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE evidence->'descartados' @> '[{\"motivo\":\"CONFIANCA_FORA_DA_FAIXA\"}]'::jsonb;")"
  item "rodada1-descarte-de-derivado" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE evidence->'descartados' @> '[{\"motivo\":\"DERIVADO_NAO_ACEITO\"}]'::jsonb;")"
  item "rodada1-descarte-de-campo-nao-declarado" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE evidence->'descartados' @> '[{\"motivo\":\"CAMPO_NAO_DECLARADO\"}]'::jsonb;")"
  item "rodada1-descarte-de-resumo-acima-do-limite" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE evidence->'descartados' @> '[{\"motivo\":\"RESUMO_ACIMA_DO_LIMITE\"}]'::jsonb;")"
  # L5: RECUSADA `SEM_EVIDENCIA_VALIDA`, sem escrever linha nenhuma.
  item "rodada1-recusa-sem-lastro" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND output->>'veredito'='RECUSADA' AND output->'motivos' @> '[\"SEM_EVIDENCIA_VALIDA\"]'::jsonb;")"
  item "rodada1-sem-lastro-nao-gravou" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  # 2: a empresa NAO foi criada nem tocada; signals/research_runs intactos.
  item "rodada1-organizacoes-total" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "rodada1-nao-cria-organizacao" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id NOT IN ('$ORG_A','$ORG_B','$ORG_C');")"
  item "rodada1-nao-toca-a-empresa" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE updated_at = '2000-01-01T00:00:00Z' AND status='DISCOVERED';")"
  item "rodada1-research-runs-total" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "rodada1-signals-total" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "rodada1-nenhuma-outra-tabela-escrita" "0" \
    "$(contagem "SELECT (SELECT count(*) FROM sales_intelligence.contacts) + (SELECT count(*) FROM sales_intelligence.scores) + (SELECT count(*) FROM sales_intelligence.interactions) + (SELECT count(*) FROM sales_intelligence.recommendations) + (SELECT count(*) FROM sales_intelligence.outbox_events);")"
  # Auditoria: uma linha por hipotese processada (14), com os status do contrato.
  item "rodada1-agent-runs-total" "14" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='pain_hypothesis' AND agent_role='pain_hypothesis' AND agent_version='1.0.0' AND correlation_id='$CORR_R1';")"
  item "rodada1-agent-runs-completed" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='COMPLETED';")"
  item "rodada1-agent-runs-rejected" "8" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='REJECTED';")"
  item "rodada1-agent-runs-review-required" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='REVIEW_REQUIRED';")"
  item "rodada1-agent-runs-failed" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='FAILED';")"
  item "rodada1-auditoria-aponta-a-hipotese" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs a JOIN sales_intelligence.pain_hypotheses h ON (a.output->>'pain_hypothesis_id') = h.id::text WHERE a.correlation_id='$CORR_R1';")"
  # Claim de sincronizacao: um por hipotese gravada, amarrado a hipotese (base do desfazer).
  item "rodada1-sync-events-pain-hypothesis" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='PAIN_HYPOTHESIS' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_R1';")"
  item "rodada1-sync-event-amarra-a-hipotese" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events e JOIN sales_intelligence.pain_hypotheses h ON e.entity_id = h.id WHERE e.operation='PAIN_HYPOTHESIS' AND e.status='SUCCESS';")"
  item "rodada1-idempotency-key-formato" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key LIKE 'pain:org:%';")"
  # Identidade ambigua vai para a fila humana, com claim proprio.
  item "rodada1-sync-events-pain-review" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='PAIN_REVIEW' AND status='SUCCESS';")"
  item "rodada1-fila-humana-pendente" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='PAIN_IDENTITY_REVIEW' AND status='PENDING';")"
  item "rodada1-fila-humana-duas-casadas" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='PAIN_IDENTITY_REVIEW' AND jsonb_array_length(proposed_action->'organizacoes_casadas')=2;")"

  # ---- rodada 2 (mesma fonte: retry nao duplica) -----------------------------------
  rodar_pain "$TRABALHO/r2.json" --ambiente dev --correlation-id "$CORR_R2" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r2.out" 2>&1
  item "rodada2-exit-0" "0" "$?"
  item "rodada2-nada-novo" "0" "$(veredito_do_relatorio "$TRABALHO/r2.json" REGISTRADA)"
  item "rodada2-cinco-replays" "5" "$(veredito_do_relatorio "$TRABALHO/r2.json" JA_REGISTRADA)"
  item "rodada2-nao-duplica" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  item "rodada2-sem-novo-claim" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R2';")"
  item "rodada2-fila-humana-nao-duplica" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='PAIN_IDENTITY_REVIEW';")"
  item "rodada2-empresa-intocada" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE updated_at = '2000-01-01T00:00:00Z';")"

  # ---- rodada 3: apaga UMA hipotese por fora e reapresenta a linha dela -------------
  # Sem o `ON CONFLICT (idempotency_key) DO NOTHING` o reenvio estoura UNIQUE e vira ERRO; sem a
  # ancora na hipotese DESTA rodada, o fechamento marcaria sucesso sem hipotese — os dois defeitos
  # sao medidos aqui (e a prova de dente os muta).
  psql_t -c "DELETE FROM sales_intelligence.pain_hypotheses WHERE organization_id='$ORG_B';" >/dev/null
  sed -n '2p' "$FONTE" > "$TRABALHO/replay.jsonl"
  rodar_pain "$TRABALHO/r3.json" --ambiente dev --correlation-id "$CORR_R3" \
    --fonte "$TRABALHO/replay.jsonl" --prefixo "$PREFIXO" > "$TRABALHO/r3.out" 2>&1
  item "rodada3-exit-0" "0" "$?"
  item "rodada3-sem-erro" "0" "$(veredito_do_relatorio "$TRABALHO/r3.json" ERRO)"
  item "rodada3-ja-registrada-um" "1" "$(veredito_do_relatorio "$TRABALHO/r3.json" JA_REGISTRADA)"
  item "rodada3-nao-recria" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  item "rodada3-sem-novo-claim" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R3';")"

  # ---- guarda de ambiente (ADR-005) e modo sem banco ------------------------------
  rodar_pain "$TRABALHO/r4.json" --ambiente prod --correlation-id "$CORR_R4" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r4.out" 2>&1
  item "prod-recusado-exit-4" "4" "$?"
  item "prod-nao-escreveu-hipotese" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  item "prod-nao-registrou-execucao" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R4';")"
  python3 "$PAIN_PY" --raiz "$RAIZ" --planejar --fonte "$FONTE" \
    --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" \
    > "$TRABALHO/r5.out" 2>&1
  item "planejar-exit-0-sem-conectar" "0" "$?"
  item "planejar-nao-escreveu" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"

  # ---- desfazer -------------------------------------------------------------------
  rodar_pain "$TRABALHO/dry.json" --desfazer "$CORR_R1" --ambiente dev \
    --prefixo "$PREFIXO" > "$TRABALHO/dry.out" 2>&1
  item "desfazer-dry-run-exit-0" "0" "$?"
  item "desfazer-dry-run-nao-apagou" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  rodar_pain "$TRABALHO/del.json" --desfazer "$CORR_R1" --ambiente dev --confirmo \
    --prefixo "$PREFIXO" > "$TRABALHO/del.out" 2>&1
  item "desfazer-confirmo-exit-0" "0" "$?"
  item "desfazer-apagou-so-a-rodada" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.pain_hypotheses;")"
  # A hipotese apagada por fora na rodada 3 deixa o `sync_events` dela ORFAO (visivel de proposito):
  # o desfazer alcanca o que a rodada criou E ainda existe. Dos 4 claims de hipotese, 3 sao apagados
  # com as hipoteses e 1 fica como trilha do que foi removido por fora.
  item "desfazer-deixa-o-claim-orfao-visivel" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='PAIN_HYPOTHESIS';")"
  item "desfazer-nao-deixou-hipotese-orfa-de-claim" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events e WHERE e.operation='PAIN_HYPOTHESIS' AND NOT EXISTS (SELECT 1 FROM sales_intelligence.pain_hypotheses h WHERE h.id = e.entity_id);")"
  item "desfazer-registrou-rollback" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='ROLLBACK';")"
  item "desfazer-preservou-a-auditoria" "14" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1';")"
  item "desfazer-preservou-a-fila-humana" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='PAIN_IDENTITY_REVIEW';")"
  item "desfazer-preservou-o-claim-da-revisao" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='PAIN_REVIEW';")"
  item "desfazer-preservou-a-empresa" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE status='DISCOVERED' AND updated_at = '2000-01-01T00:00:00Z';")"
  item "desfazer-preservou-a-pesquisa" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "desfazer-preservou-o-lastro" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"

  echo "RESULTADO: ACEITE_PAIN_001_$( [ "$ITENS_FALHOU" -eq 0 ] && echo OK || echo FALHOU ) ($((ITENS_OK + ITENS_FALHOU)) itens, $ITENS_FALHOU falhas)"
  [ "$ITENS_FALHOU" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
# Prova de dente: mutacao em copia do agente TEM de reprovar o item esperado
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
  # Formato: nome|alvo|substituto|itens-esperados. Em alvo/substituto, `\n` vira quebra de linha
  # real e `\\n` fica o par barra+n do codigo-fonte (as ancoras de varias linhas precisam disso).
  local linhas=() linha
  while IFS= read -r linha; do
    [ -n "$linha" ] && linhas+=("$linha")
  done <<'EOF'
sem-idempotencia|        "  ON CONFLICT (idempotency_key) DO NOTHING\\n"\n        "  RETURNING id\\n"\n        ")\\n"\n        "INSERT INTO {hipoteses} ({cols})\\n"|        "  RETURNING id\\n"\n        ")\\n"\n        "INSERT INTO {hipoteses} ({cols})\\n"|rodada3-sem-erro
fechamento-sem-ancora-na-hipotese|        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {hipoteses} WHERE id = {hid})\\n"|        "WHERE idempotency_key = {chave}\\n"|rodada3-ja-registrada-um
lastro-nao-conferido|            registro = base.get(evidencia["id"])\n            if registro is None:\n                descartados.append({"campo": "evidencias", "motivo": "EVIDENCIA_NAO_ENCONTRADA",\n                                    "valor": {"tipo": evidencia["tipo"], "id": evidencia["id"]}})\n                continue|            registro = base.get(evidencia["id"]) or {"organization_id": organizacao_id}|rodada1-recusa-sem-lastro
lastro-de-outra-empresa-aceito|            if registro.get("organization_id") != organizacao_id:\n                descartados.append({"campo": "evidencias",\n                                    "motivo": "EVIDENCIA_DE_OUTRA_ORGANIZACAO",|            if False:\n                descartados.append({"campo": "evidencias",\n                                    "motivo": "EVIDENCIA_DE_OUTRA_ORGANIZACAO",|rodada1-descarte-de-evidencia-de-outra-empresa
status-chumbado-validado|                    "status": STATUS_INICIAL,|                    "status": "VALIDATED",|rodada1-status-inicial
EOF

  local total="${#linhas[@]}" detectadas=0 falhas=0
  local nome alvo substituto esperados destino guardado faltando esperado
  for linha in "${linhas[@]}"; do
    IFS='|' read -r nome alvo substituto esperados <<< "$linha"
    [ -z "$nome" ] && continue
    alvo="$(printf '%b' "$alvo")"
    substituto="$(printf '%b' "$substituto")"
    destino="$TRABALHO/mut-$nome/pain_hypothesis.py"
    mkdir -p "$(dirname "$destino")"
    if ! aplicar_mutacao "$PAIN_PY" "$destino" "$alvo" "$substituto" | grep -q MUTACAO_APLICADA; then
      echo "FALHOU mutacao $nome NAO se aplicou (ancora mudou) — buraco de verificacao"
      falhas=$((falhas + 1)); continue
    fi
    echo
    echo "-- mutacao: $nome (tem de reprovar: $(echo "$esperados" | tr ',' ' '))"
    guardado="$PAIN_PY"
    PAIN_PY="$destino"
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
    PAIN_PY="$guardado"
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
  echo "== ACEITE DO AGENTE PAIN HYPOTHESIS v1 (TRE-W4-E04-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0
  if rodar_aceite "principal"; then ok=1; fi
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_PAIN_001_OK"
    return 0
  fi
  echo "ACEITE_PAIN_001_FALHOU"
  return 1
}

principal
