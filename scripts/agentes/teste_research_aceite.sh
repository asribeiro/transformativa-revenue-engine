#!/usr/bin/env bash
# =====================================================================================
# teste_research_aceite.sh [--prova-de-dente] [--manter] [--codigo <research.py>]
#
# ACEITE DO AGENTE RESEARCH v1 — TRE-W4-E02-T01. Roda ON DE A VPS do ambiente (ADR-0008:
# o PostgreSQL vive la; o container do Hermes so orquestra por SSH).
#
# O que ele faz, em um container PostgreSQL DESCARTÁVEL (nunca toca pg-sales-dev,
# pg-odoo-dev nem qualquer outro container):
#   0. guardas ....... docker/python3/migration presentes; o nome do container NAO pode
#                      existir (se existir, ABORTA em vez de mexer no que nao e dele)
#   1. sobe ......... container novo com a migration 0001 + 3 organizacoes pre-existentes
#                      (uma delas com industry_name JA preenchido: e o dado curado)
#   2. rodada 1 ..... 12 pedidos sinteticos: 6 pesquisas legitimas (4 tipos), 1 organizacao
#                      inexistente, 1 sem identificador forte, 1 com CNPJ invalido, 1 com
#                      fonte fora do vocabulario, 1 sem fonte e 1 conflito de identidade
#   3. rodada 2 ..... a MESMA fonte de novo: 0 research_run novo (retry nao cria duplicata)
#   4. rodada 3 ..... replay com a chave JA reivindicada e o research_run ausente (prova o
#                      ON CONFLICT (idempotency_key) DO NOTHING)
#   5. guardas ...... --ambiente prod recusado; --planejar com prefixo inexistente nao conecta
#   6. desfazer ..... dry-run nao apaga; --confirmo restaura o valor anterior DAS COLUNAS da
#                      rodada, apaga research_runs/sync_events da rodada e registra ROLLBACK
#   7. veredito ..... ACEITE_RESEARCH_001_OK / ACEITE_RESEARCH_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do research.py (idempotencia, formato que nao
#   sobrescreve, fronteira de tipo de pesquisa) e exige que o aceite REPROVE **o item esperado
#   de cada mutacao**; baseline verde antes e depois. Mutacao que nao se aplica na ancora
#   tambem reprova (e buraco de verificacao, nao alivio), e o veredito imprime a contagem MEDIDA.
#
# NOTA (defeito medido na revisao independente do card irmao, rodada 1): o corpo do laco de
#   mutacoes chama `docker exec -i`, que CONSOME o stdin do laco. Aqui as mutacoes sao lidas numa
#   LISTA antes do laco (o laco nao depende de stdin) e todo `docker exec` que nao le stdin leva
#   `</dev/null`.
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_RESEARCH_CONTAINER (default pg-research-acc), TRE_RESEARCH_TRABALHO.
# Exit: 0 = ACEITE_RESEARCH_001_OK · 1 = FALHOU · 2 = uso/guarda.
# =====================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_RESEARCH_CONTAINER:-pg-research-acc}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="research-aceite-descartavel"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
RESEARCH_PY="$RAIZ/hermes/agents/research/research.py"
TRABALHO="${TRE_RESEARCH_TRABALHO:-$(mktemp -d /tmp/research-aceite-XXXXXX)}"

MANTER=0
DENTE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter)         MANTER=1 ;;
    --codigo)         RESEARCH_PY="$2"; shift ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <research.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <research.py>]"; exit 2 ;;
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
[ -f "$RESEARCH_PY" ] || { echo "FALHOU agente ausente: $RESEARCH_PY"; exit 2; }
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

preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
  psql_stdin >/dev/null <<'SQL'
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, cnpj, domain, linkedin_url, industry_name, status, source,
   created_at, updated_at)
VALUES
  ('aaaaaaaa-0000-4000-8000-000000000001', 'Metalurgica Vale Forte Ltda', 'Vale Forte',
   '11222333000181', 'valeforte.com.br', NULL, 'Metalurgia', 'DISCOVERED', 'LINKEDIN', now(), now()),
  ('aaaaaaaa-0000-4000-8000-000000000002', 'AgroSmart Analytics Ltda', 'AgroSmart Analytics',
   NULL, 'agrosmart-analytics.com.br', NULL, NULL, 'DISCOVERED', 'WEB', now(), now()),
  ('aaaaaaaa-0000-4000-8000-000000000003', 'Clinica Sao Lucas S.A.', 'Clinica Sao Lucas',
   NULL, NULL, 'https://www.linkedin.com/company/clinica-sao-lucas', NULL, 'DISCOVERED',
   'EVENTOS', now(), now());
SQL
}

escrever_fonte() { # <arquivo>
  cat > "$1" <<'JSONL'
{"organizacao":{"domain":"valeforte.com.br"},"tipo":"COMPANY_PROFILE","fontes":[{"tipo":"WEB","url":"https://valeforte.com.br/sobre","trecho":"Metalurgica com 40 anos de mercado"},{"tipo":"LINKEDIN","url":"https://www.linkedin.com/company/vale-forte","trecho":"Setor: Metalurgia - Sede: Sao Bernardo do Campo/SP"}],"achados":{"industry_name":"Industria Metalurgica","city":"Sao Bernardo do Campo","state":"SP","business_model":"B2B","cnpj":"11.222.333/0001-81"},"confianca":0.82}
{"organizacao":{"cnpj":"45.723.174/0001-10"},"tipo":"SIZE_AND_STRUCTURE","fontes":[{"tipo":"DADOS_PUBLICOS","url":"https://empresas.example/nova-alpha","trecho":"210 colaboradores; 2 unidades"}],"achados":{"employee_count":210,"unit_count":2},"confianca":0.7}
{"organizacao":{"domain":"agrosmart-analytics.com.br"},"tipo":"SIZE_AND_STRUCTURE","fontes":[{"tipo":"DADOS_PUBLICOS","url":"https://empresas.example/agrosmart","trecho":"85 colaboradores; 1 unidade; receita estimada 1,2 mi"}],"achados":{"employee_count":85,"unit_count":1,"revenue_estimate":1200000,"employee_band":"150_299"},"confianca":0.75}
{"organizacao":{"linkedin_url":"https://br.linkedin.com/company/clinica-sao-lucas/"},"tipo":"INDUSTRY","fontes":[{"tipo":"LINKEDIN","url":"https://www.linkedin.com/company/clinica-sao-lucas","trecho":"Healthcare - 320 funcionarios"}],"achados":{"industry_name":"Saude","industry_code":"Q","city":"Curitiba"}}
{"organizacao":{"domain":"agrosmart-analytics.com.br"},"tipo":"DIGITAL_PRESENCE","fontes":[{"tipo":"WEB","url":"https://agrosmart-analytics.com.br","trecho":"portal institucional ativo"}],"achados":{"website_url":"https://agrosmart-analytics.com.br"},"confianca":0.9}
{"organizacao":{"city":"Santos"},"tipo":"COMPANY_PROFILE","fontes":[{"tipo":"WEB","url":"https://example.com","trecho":"sem identificador forte"}],"achados":{"city":"Santos","state":"SP"}}
{"organizacao":{"domain":"valeforte.com.br"},"tipo":"COMPANY_PROFILE","fontes":[{"tipo":"PANFLETO","trecho":"fonte fora do vocabulario"}],"achados":{"city":"Osasco"}}
{"organizacao":{"cnpj":"11.222.333/0001-00"},"tipo":"COMPANY_PROFILE","fontes":[{"tipo":"WEB","url":"https://example.com","trecho":"cnpj com digito verificador errado"}],"achados":{"industry_name":"Metalurgia"}}
{"organizacao":{"cnpj":"11.222.333/0001-81","domain":"agrosmart-analytics.com.br"},"tipo":"COMPANY_PROFILE","fontes":[{"tipo":"WEB","url":"https://example.com","trecho":"dois fortes de empresas diferentes"}],"achados":{"state":"SP"}}
{"organizacao":{"linkedin_url":"https://www.linkedin.com/company/clinica-sao-lucas"},"tipo":"INDUSTRY","fontes":[],"achados":{"industry_name":"Saude"}}
{"organizacao":{"linkedin_url":"https://www.linkedin.com/company/clinica-sao-lucas"},"tipo":"COMPANY_PROFILE","fontes":[{"tipo":"GOOGLE","url":"https://www.google.com/search?q=clinica+sao+lucas","trecho":"perfil institucional"}],"achados":{"employee_count":300,"industry_name":"Saude II"},"confianca":0.6}
{"organizacao":{"cnpj":"11222333000181"},"tipo":"SIZE_AND_STRUCTURE","fontes":[{"tipo":"DADOS_PUBLICOS","url":"https://empresas.example/vale-forte","trecho":"420 colaboradores"}],"achados":{"employee_count":420},"confianca":0.8}
JSONL
}

rodar_research() { # <relatorio> <args...>
  local relatorio="$1"; shift
  # `</dev/null`: o agente nunca le stdin e a porta psql usa `input=` — nada aqui pode
  # consumir o stdin de um laco que chame rodar_research.
  python3 "$RESEARCH_PY" --raiz "$RAIZ" --relatorio "$relatorio" "$@" </dev/null
}

veredito_do_relatorio() { # <relatorio> <veredito>
  python3 - "$1" "$2" <<'PY'
import json, sys
relatorio = json.load(open(sys.argv[1], encoding="utf-8"))
print(relatorio.get("por_veredito", {}).get(sys.argv[2], 0))
PY
}

CORR_R1="dddddddd-0000-4000-8000-000000000001"
CORR_R2="dddddddd-0000-4000-8000-000000000002"
CORR_R3="dddddddd-0000-4000-8000-000000000003"
CORR_R4="dddddddd-0000-4000-8000-000000000004"
FONTE="$TRABALHO/pesquisas.jsonl"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
ORG_A="aaaaaaaa-0000-4000-8000-000000000001"
ORG_B="aaaaaaaa-0000-4000-8000-000000000002"
ORG_C="aaaaaaaa-0000-4000-8000-000000000003"

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== aceite do codigo sob teste: $RESEARCH_PY ($rotulo)"
  preparar_banco
  escrever_fonte "$FONTE"

  # ---- rodada 1 --------------------------------------------------------------------
  rodar_research "$TRABALHO/r1.json" --ambiente dev --correlation-id "$CORR_R1" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r1.out" 2>&1
  item "rodada1-exit-0" "0" "$?"
  item "rodada1-organizacoes-total" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "rodada1-nao-cria-organizacao" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id NOT IN ('$ORG_A','$ORG_B','$ORG_C');")"
  item "rodada1-research-runs" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "rodada1-research-runs-completed" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE agent_name='research' AND agent_version='1.0.0' AND status='COMPLETED';")"
  item "rodada1-research-runs-com-carimbos" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE started_at IS NOT NULL AND completed_at IS NOT NULL AND created_at IS NOT NULL AND completed_at >= started_at;")"
  item "rodada1-uuid-v4-no-produtor" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$';")"
  item "rodada1-source-count-medido" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE source_count = jsonb_array_length(structured_output->'fontes') AND source_count > 0;")"
  item "rodada1-input-hash-presente" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE input_hash ~ '^[0-9a-f]{64}\$';")"
  item "rodada1-sem-llm-na-execucao" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE model_name IS NULL AND model_provider IS NULL AND tokens_input IS NULL AND estimated_cost IS NULL AND structured_output->'llm'->>'executado' = 'false';")"
  item "rodada1-agent-runs-por-pedido" "12" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='research' AND agent_role='research' AND correlation_id='$CORR_R1';")"
  item "rodada1-agent-runs-completed" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='COMPLETED';")"
  item "rodada1-sync-events-success" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='RESEARCH' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_R1';")"
  item "rodada1-fila-humana-pendente" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='RESEARCH_IDENTITY_REVIEW' AND status='PENDING';")"
  item "rodada1-relatorio-vereditos" "PESQUISADA=6 JA_PESQUISADO=0 REVISAO_IDENTIDADE=1 RECUSADA=5" \
    "PESQUISADA=$(veredito_do_relatorio "$TRABALHO/r1.json" PESQUISADA) JA_PESQUISADO=$(veredito_do_relatorio "$TRABALHO/r1.json" JA_PESQUISADO) REVISAO_IDENTIDADE=$(veredito_do_relatorio "$TRABALHO/r1.json" REVISAO_IDENTIDADE) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r1.json" RECUSADA)"
  # A3: a coluna JA preenchida nao e sobrescrita (industry_name curada continua 'Metalurgia').
  item "rodada1-nao-sobrescreve-o-que-ja-existia" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_A' AND industry_name='Metalurgia';")"
  # A2: coluna VAZIA e enriquecida com o achado da fonte.
  item "rodada1-enriquece-coluna-vazia" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_A' AND city='Sao Bernardo do Campo' AND state='SP' AND business_model='B2B';")"
  item "rodada1-faixa-derivada-do-numero" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_A' AND employee_count=420 AND employee_band='300_499';")"
  item "rodada1-segunda-organizacao-enriquecida" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_B' AND employee_count=85 AND employee_band='70_149' AND unit_count=1 AND revenue_estimate=1200000 AND website_url='https://agrosmart-analytics.com.br';")"
  # A8: coluna FORA do tipo declarado nao e escrita (employee_count num pedido COMPANY_PROFILE).
  item "rodada1-nao-escreve-coluna-fora-do-tipo" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_C' AND employee_count IS NULL AND employee_band IS NULL AND city IS NULL;")"
  # A8: o estagio do funil e do Odoo — a pesquisa nao mexe em status nem em identidade.
  item "rodada1-status-intocado" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE status='DISCOVERED';")"
  item "rodada1-identidade-intocada" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_B' AND cnpj IS NULL AND domain='agrosmart-analytics.com.br';")"
  item "rodada1-descarte-de-identificador-forte" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE structured_output->'achados_descartados' @> '[{\"motivo\":\"IDENTIFICADOR_FORTE_NAO_ESCRITO_PELA_PESQUISA\"}]';")"
  item "rodada1-descarte-de-derivado" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE structured_output->'achados_descartados' @> '[{\"motivo\":\"DERIVADO_NAO_ACEITO\"}]';")"
  item "rodada1-descarte-fora-do-tipo" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE structured_output->'achados_descartados' @> '[{\"motivo\":\"COLUNA_FORA_DO_TIPO\"}]';")"
  item "rodada1-research-runs-apontam-organizacao-casada" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs WHERE organization_id IN ('$ORG_A','$ORG_B','$ORG_C');")"
  item "rodada1-nenhuma-outra-tabela-escrita" "0" "$(contagem "SELECT (SELECT count(*) FROM sales_intelligence.contacts) + (SELECT count(*) FROM sales_intelligence.signals) + (SELECT count(*) FROM sales_intelligence.pain_hypotheses) + (SELECT count(*) FROM sales_intelligence.scores) + (SELECT count(*) FROM sales_intelligence.interactions) + (SELECT count(*) FROM sales_intelligence.recommendations) + (SELECT count(*) FROM sales_intelligence.outbox_events);")"

  # ---- rodada 2 (mesma fonte: retry nao cria duplicata) ----------------------------
  rodar_research "$TRABALHO/r2.json" --ambiente dev --correlation-id "$CORR_R2" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r2.out" 2>&1
  item "rodada2-exit-0" "0" "$?"
  item "rodada2-nao-duplica" "6" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "rodada2-criadas-zero" "0" "$(veredito_do_relatorio "$TRABALHO/r2.json" PESQUISADA)"
  item "rodada2-ja-pesquisado-seis" "6" "$(veredito_do_relatorio "$TRABALHO/r2.json" JA_PESQUISADO)"
  item "rodada2-sem-novo-claim" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R2';")"
  item "rodada2-valores-intocados" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_A' AND industry_name='Metalurgia' AND employee_count=420;")"

  # ---- rodada 3: a MESMA chave reapresentada com o research_run ausente ------------
  # Cenario real de concorrencia/retentativa: o claim do sync_event ja existe, mas o
  # research_run nao (foi removido/renomeado depois do sync). Sem o
  # `ON CONFLICT (idempotency_key) DO NOTHING` o reenvio estoura UNIQUE e vira ERRO; com a
  # guarda, e replay silencioso: nao duplica e nao marca sucesso de pesquisa.
  psql_t -c "DELETE FROM sales_intelligence.research_runs WHERE research_type='DIGITAL_PRESENCE' AND organization_id='$ORG_B';" >/dev/null
  printf '%s\n' "$(sed -n '5p' "$FONTE")" > "$TRABALHO/replay.jsonl"
  rodar_research "$TRABALHO/r5.json" --ambiente dev --correlation-id "$CORR_R3" \
    --fonte "$TRABALHO/replay.jsonl" --prefixo "$PREFIXO" > "$TRABALHO/r5.out" 2>&1
  item "rodada3-exit-0" "0" "$?"
  item "rodada3-sem-erro" "0" "$(veredito_do_relatorio "$TRABALHO/r5.json" ERRO)"
  item "rodada3-nao-duplica" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "rodada3-ja-pesquisado-um" "1" "$(veredito_do_relatorio "$TRABALHO/r5.json" JA_PESQUISADO)"
  item "rodada3-sem-novo-claim" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R3';")"
  item "rodada3-organizacao-intocada" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_B' AND website_url='https://agrosmart-analytics.com.br' AND employee_count=85;")"

  # ---- guarda de ambiente (ADR-005) e modo sem banco ------------------------------
  rodar_research "$TRABALHO/r3.json" --ambiente prod --correlation-id "$CORR_R4" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r3.out" 2>&1
  item "prod-recusado-exit-4" "4" "$?"
  item "prod-nao-escreveu-research-run" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "prod-nao-registrou-execucao" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R4';")"
  python3 "$RESEARCH_PY" --raiz "$RAIZ" --planejar --fonte "$FONTE" \
    --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" \
    > "$TRABALHO/r4.out" 2>&1
  item "planejar-exit-0-sem-conectar" "0" "$?"
  item "planejar-nao-escreveu" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"

  # ---- desfazer -------------------------------------------------------------------
  rodar_research "$TRABALHO/dry.json" --desfazer "$CORR_R1" --ambiente dev \
    --prefixo "$PREFIXO" > "$TRABALHO/dry.out" 2>&1
  item "desfazer-dry-run-exit-0" "0" "$?"
  item "desfazer-dry-run-nao-apagou" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  rodar_research "$TRABALHO/del.json" --desfazer "$CORR_R1" --ambiente dev --confirmo \
    --prefixo "$PREFIXO" > "$TRABALHO/del.out" 2>&1
  item "desfazer-confirmo-exit-0" "0" "$?"
  item "desfazer-apagou-so-a-rodada" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "desfazer-restaurou-colunas-semeadas" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_A' AND city IS NULL AND state IS NULL AND business_model IS NULL AND employee_count IS NULL AND employee_band IS NULL;")"
  item "desfazer-preservou-dado-curado" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_A' AND industry_name='Metalurgia' AND cnpj='11222333000181' AND status='DISCOVERED';")"
  item "desfazer-restaurou-segunda-organizacao" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id='$ORG_B' AND employee_count IS NULL AND unit_count IS NULL AND revenue_estimate IS NULL;")"
  item "desfazer-preservou-a-base" "3" "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "desfazer-preservou-auditoria" "12" "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1';")"
  item "desfazer-preservou-a-fila-humana" "2" "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='RESEARCH_IDENTITY_REVIEW';")"
  item "desfazer-registrou-rollback" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='ROLLBACK';")"

  echo "RESULTADO: ACEITE_RESEARCH_001_$( [ "$ITENS_FALHOU" -eq 0 ] && echo OK || echo FALHOU ) ($((ITENS_OK + ITENS_FALHOU)) itens, $ITENS_FALHOU falhas)"
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
    grep '^FALHOU' "$TRABALHO/dente-baseline.out" | head -5
    return 1
  fi

  # As mutacoes sao lidas numa LISTA antes do laco: o corpo chama `docker exec -i` (por
  # psql_t/contagem), que consome o stdin do laco — com here-string as iteracoes 2+ morriam.
  # Formato: nome|alvo|substituto|itens-esperados (o dente exige o item, nao so "falhou").
  local linhas=() linha
  while IFS= read -r linha; do
    [ -n "$linha" ] && linhas+=("$linha")
  done <<'EOF'
sem-idempotencia|ON CONFLICT (idempotency_key) DO NOTHING||rodada3-exit-0,rodada3-sem-erro
enriquecimento-sem-coalesce|        return "%s = COALESCE(NULLIF(%s, ''), %s)" % (coluna, coluna, lit(valor))|        return "%s = %s" % (coluna, lit(valor))|rodada1-research-runs,rodada1-enriquece-coluna-vazia
coluna-fora-do-tipo-liberada|        if campo not in do_tipo:|        if False:|rodada1-nao-escreve-coluna-fora-do-tipo
numero-fora-do-tipo-liberado|        if "employee_count" not in COLUNAS_ENRIQUECIMENTO or "employee_count" not in do_tipo:|        if False:|rodada1-nao-escreve-coluna-fora-do-tipo,rodada1-descarte-fora-do-tipo
EOF

  local total="${#linhas[@]}" detectadas=0 falhas=0
  local nome alvo substituto esperados destino guardado faltando esperado
  for linha in "${linhas[@]}"; do
    IFS='|' read -r nome alvo substituto esperados <<< "$linha"
    [ -z "$nome" ] && continue
    destino="$TRABALHO/mut-$nome/research.py"
    mkdir -p "$(dirname "$destino")"
    if ! aplicar_mutacao "$RESEARCH_PY" "$destino" "$alvo" "$substituto" | grep -q MUTACAO_APLICADA; then
      echo "FALHOU mutacao $nome NAO se aplicou (ancora mudou) — buraco de verificacao"
      falhas=$((falhas + 1)); continue
    fi
    echo
    echo "-- mutacao: $nome (tem de reprovar: $(echo "$esperados" | tr ',' ' '))"
    guardado="$RESEARCH_PY"
    RESEARCH_PY="$destino"
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
    RESEARCH_PY="$guardado"
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
  echo "== ACEITE DO AGENTE RESEARCH v1 (TRE-W4-E02-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0
  if rodar_aceite "principal"; then ok=1; fi
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_RESEARCH_001_OK"
    return 0
  fi
  echo "ACEITE_RESEARCH_001_FALHOU"
  return 1
}

principal
