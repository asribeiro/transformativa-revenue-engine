#!/usr/bin/env bash
# teste_signal_aceite.sh [--prova-de-dente] [--manter] [--codigo <signal.py>]
#
# ACEITE E2E do AGENTE SIGNAL DETECTOR v1 (card TRE-W4-E03-T01) — PostgreSQL descartavel.
#
# Roda na VPS (o container do Hermes nao tem daemon Docker). NUNCA toca `pg-sales-dev`,
# `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio (`pg-signal-acc`), aplica a
# migration 0001 e, no fim, remove o container e o diretorio de trabalho. Se o container ja existir,
# ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (A1..A13 do doc `docs/architecture/agente-signal-v1.md`):
#   1. deteccao legitima grava `signals` com categoria DERIVADA do tipo, evidencia e carimbos;
#   2. a deteccao NAO cria organizacao e NAO escreve coluna nenhuma de `organizations`;
#   3. nenhum score e escrito (buying_signal_points/relevance_score/decay_factor/expires_at);
#   4. descartes com motivo (data, confianca, categoria da fonte, campo nao declarado, titulo,
#      vinculo com research_run inexistente) ficam na evidencia;
#   5. retry nao duplica (rodada 2) e replay com o sinal ausente nao vira erro (rodada 3);
#   6. `prod` e recusado sem escrita e `--planejar` nao abre conexao;
#   7. desfazer dry-run nao apaga; `--confirmo` apaga SO o que a rodada criou e registra ROLLBACK,
#      preservando `organizations`, `research_runs`, `agent_runs` e `human_approvals`;
#   8. veredito ..... ACEITE_SIGNAL_001_OK / ACEITE_SIGNAL_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do signal.py (idempotencia, fechamento sem ancora,
#   categoria chumbada, vinculo quebrado aceito) e exige que o aceite reprove O ITEM ESPERADO de
#   cada uma — nao basta "o aceite falhou".
#
# Licoes ja pagas (mantidas aqui de proposito): todo `docker exec -i` que nao le stdin leva
# `</dev/null`, senao ele CONSOME o stdin do laco de mutacoes e mata as iteracoes seguintes; as
# mutacoes sao lidas numa LISTA antes do laco; `pg_isready` mente no inicio, entao a espera e por
# `SELECT 1` funcionando DUAS vezes.
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_SIGNAL_CONTAINER (default pg-signal-acc), TRE_SIGNAL_TRABALHO.
# Exit: 0 = ACEITE_SIGNAL_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_SIGNAL_CONTAINER:-pg-signal-acc}"
TRABALHO="${TRE_SIGNAL_TRABALHO:-/tmp/signal-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="signal-aceite-descartavel"
SIGNAL_PY="$RAIZ/hermes/agents/signal/signal.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; SIGNAL_PY="${1:?--codigo exige caminho}" ;;
    --codigo=*) SIGNAL_PY="${1#--codigo=}" ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <signal.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <signal.py>]"; exit 2 ;;
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
[ -f "$SIGNAL_PY" ] || { echo "FALHOU agente ausente: $SIGNAL_PY"; exit 2; }
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
RES_RUN="eeeeeeee-0000-4000-8000-000000000001"
CNPJ_ORG_A="11.222.333/0001-81"
CNPJ_ORG_B="45.723.174/0001-10"
CNPJ_INVALIDO="11.222.333/0001-00"

preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
  # `updated_at` FIXO num passado distante de proposito: se a deteccao encostar em qualquer coluna
  # de `organizations`, o valor muda e o aceite acusa (A3).
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
  ('$RES_RUN', '$ORG_A', 'research', '1.0.0', 'COMPANY_PROFILE', 'COMPLETED',
   '2026-01-06T09:00:00Z', '2026-01-06T09:05:00Z', 1, '{"fontes": []}'::jsonb,
   '2026-01-06T09:00:00Z');
SQL
}

# Gera um CNPJ com digito verificador VALIDO que NAO existe na base: e a prova de
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

# A fonte e' escrita por python para o titulo gigante do descarte ficar legivel (JSON valido).
escrever_fonte() { # <arquivo> <cnpj-inexistente>
  python3 - "$1" "$2" <<PY
import json, sys
destino, cnpj_inexistente = sys.argv[1], sys.argv[2]
linhas = []
# L1 — legítima, com vinculo REAL com a pesquisa e duas fontes (a primeira e' a primaria).
l1 = {"organizacao": {"domain": "valeforte.com.br"}, "tipo": "HIRING",
      "titulo": "40 vagas de logistica abertas em 30 dias",
      "descricao": "A empresa abriu 40 vagas de operador logistico no ultimo mes.",
      "fontes": [{"tipo": "LINKEDIN", "url": "https://www.linkedin.com/company/vale-forte/jobs",
                  "trecho": "40 vagas abertas no ultimo mes"},
                 {"tipo": "WEB", "url": "https://valeforte.com.br/carreiras",
                  "trecho": "vagas de operador logistico"}],
      "data_do_evento": "2026-09-28", "confianca": 0.8, "research_run_id": "$RES_RUN"}
linhas.append(l1)
# L2 — casa por CNPJ com pontuacao (prova normalizacao da identidade).
linhas.append({"organizacao": {"cnpj": "$CNPJ_ORG_B"}, "tipo": "ERP_CHANGE",
               "titulo": "Migracao de ERP anunciada",
               "fontes": [{"tipo": "DADOS_PUBLICOS",
                           "url": "https://empresas.example/agrosmart-erp",
                           "trecho": "aquisicao de novo ERP em 2026"}], "confianca": 0.7})
# L3 — LinkedIn declarado no formato "sujo" (www + br + barra final).
linhas.append({"organizacao": {"linkedin_url": "https://br.linkedin.com/company/clinica-sao-lucas/"},
               "tipo": "AI_INITIATIVE", "titulo": "Projeto de IA anunciado",
               "fontes": [{"tipo": "LINKEDIN",
                           "url": "https://www.linkedin.com/company/clinica-sao-lucas/posts",
                           "trecho": "anuncio de projeto de IA"}], "confianca": 0.6})
# L4 — TUDO o que se descarta: data fora do formato, confianca fora da faixa, categoria declarada
# (derivada), campo nao declarado e vinculo com research_run inexistente. O sinal SEGUE.
linhas.append({"organizacao": {"domain": "valeforte.com.br"}, "tipo": "COST_REDUCTION",
               "titulo": "Programa de reducao de custos",
               "fontes": [{"tipo": "WEB", "url": "https://valeforte.com.br/ri/notas",
                           "trecho": "programa de eficiencia 2026"}],
               "data_do_evento": "2026-02-31", "confianca": 1.7, "categoria": "EXPANSAO",
               "employee_count": 50,
               "research_run_id": "eeeeeeee-0000-4000-8000-0000000000ff"})
# L5 — titulo acima do limite do DDL (500): descartado o TITULO, nao o sinal.
linhas.append({"organizacao": {"domain": "agrosmart-analytics.com.br"}, "tipo": "NEW_LOCATION",
               "titulo": "N" * 600,
               "fontes": [{"tipo": "WEB", "url": "https://agrosmart-analytics.com.br/unidades",
                           "trecho": "nova unidade em Campinas"}], "confianca": 0.5})
# L6 — copia EXATA da L1 dentro do MESMO lote: replay idempotente.
linhas.append(dict(l1))
# L7..L12 — as recusas e a revisao de identidade.
linhas.append({"organizacao": {"city": "Santos"}, "tipo": "HIRING",
               "fontes": [{"tipo": "WEB", "url": "https://example.com",
                           "trecho": "sem identificador forte"}]})
linhas.append({"organizacao": {"domain": "valeforte.com.br"}, "tipo": "SINAL_INVENTADO",
               "fontes": [{"tipo": "WEB", "url": "https://example.com",
                           "trecho": "tipo fora do vocabulario"}]})
linhas.append({"organizacao": {"domain": "valeforte.com.br"}, "tipo": "HIRING",
               "fontes": [{"tipo": "PANFLETO", "trecho": "canal fora do vocabulario"}]})
linhas.append({"organizacao": {"cnpj": "$CNPJ_INVALIDO"}, "tipo": "HIRING",
               "fontes": [{"tipo": "WEB", "url": "https://example.com",
                           "trecho": "cnpj com digito verificador errado"}]})
linhas.append({"organizacao": {"cnpj": cnpj_inexistente}, "tipo": "HIRING",
               "fontes": [{"tipo": "DADOS_PUBLICOS", "url": "https://empresas.example/nao-existe",
                           "trecho": "cnpj valido de empresa que nao esta na base"}]})
linhas.append({"organizacao": {"cnpj": "$CNPJ_ORG_A", "domain": "agrosmart-analytics.com.br"},
               "tipo": "HIRING",
               "fontes": [{"tipo": "WEB", "url": "https://example.com",
                           "trecho": "dois fortes de empresas diferentes"}]})
with open(destino, "w", encoding="utf-8") as fh:
    for linha in linhas:
        fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
print(len(linhas))
PY
}

rodar_signal() { # <relatorio> <args...>
  local relatorio="$1"; shift
  python3 "$SIGNAL_PY" --raiz "$RAIZ" --relatorio "$relatorio" "$@" </dev/null
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
FONTE="$TRABALHO/observacoes.jsonl"

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== aceite do codigo sob teste: $SIGNAL_PY ($rotulo)"
  preparar_banco
  PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
  local cnpj_fora
  cnpj_fora="$(cnpj_inexistente)"
  escrever_fonte "$FONTE" "$cnpj_fora" >/dev/null

  # ---- rodada 1 --------------------------------------------------------------------
  rodar_signal "$TRABALHO/r1.json" --ambiente dev --correlation-id "$CORR_R1" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r1.out" 2>&1
  item "rodada1-exit-0" "0" "$?"
  item "rodada1-vereditos" \
    "DETECTADO=5 JA_DETECTADO=1 REVISAO_IDENTIDADE=1 RECUSADA=5 ERRO=0" \
    "DETECTADO=$(veredito_do_relatorio "$TRABALHO/r1.json" DETECTADO) JA_DETECTADO=$(veredito_do_relatorio "$TRABALHO/r1.json" JA_DETECTADO) REVISAO_IDENTIDADE=$(veredito_do_relatorio "$TRABALHO/r1.json" REVISAO_IDENTIDADE) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r1.json" RECUSADA) ERRO=$(veredito_do_relatorio "$TRABALHO/r1.json" ERRO)"
  item "rodada1-signals-total" "5" "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "rodada1-uuid-v4-no-produtor" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\$';")"
  # A2: sinal legitimo — categoria DERIVADA, fonte primaria, data da fonte, confianca e carimbos.
  item "rodada1-deteccao-hiring" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE organization_id='$ORG_A' AND signal_type='HIRING' AND signal_category='EXPANSAO' AND title IS NOT NULL AND source_type='LINKEDIN' AND source_url='https://www.linkedin.com/company/vale-forte/jobs' AND event_date::date='2026-09-28' AND confidence=0.8 AND detected_at IS NOT NULL AND created_at IS NOT NULL;")"
  item "rodada1-categoria-derivada-do-tipo" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE (signal_type,signal_category) IN (('HIRING','EXPANSAO'),('ERP_CHANGE','TECNOLOGIA'),('AI_INITIATIVE','TECNOLOGIA'),('COST_REDUCTION','EFICIENCIA'),('NEW_LOCATION','EXPANSAO'));")"
  item "rodada1-nenhuma-categoria-invalida" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE signal_category IS NULL OR signal_category NOT IN ('EXPANSAO','CORPORATIVO','TECNOLOGIA','PRESSAO_OPERACIONAL','EFICIENCIA','REGULATORIO');")"
  item "rodada1-casa-por-cnpj-com-pontuacao" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE organization_id='$ORG_B' AND signal_type='ERP_CHANGE';")"
  item "rodada1-casa-por-linkedin-sujo" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE organization_id='$ORG_C' AND signal_type='AI_INITIATIVE' AND source_type='LINKEDIN';")"
  item "rodada1-evidence-com-fontes" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE evidence ? 'fontes' AND jsonb_array_length(evidence->'fontes') >= 1 AND evidence ? 'fonte_primaria' AND evidence ? 'input_hash';")"
  item "rodada1-fontes-completas-na-evidencia" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE signal_type='HIRING' AND jsonb_array_length(evidence->'fontes')=2;")"
  # A8: NENHUM score escrito; a coluna de decaimento fica no default do contrato.
  item "rodada1-nenhum-score-escrito" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE relevance_score IS NOT NULL OR buying_signal_points IS NOT NULL OR expires_at IS NOT NULL;")"
  item "rodada1-decay-no-default" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE decay_factor = 1;")"
  # A9: uma linha de auditoria por observacao (12), com o signal_id no output.
  item "rodada1-agent-runs-por-observacao" "12" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='signal' AND agent_role='signal_detection' AND agent_version='1.0.0' AND correlation_id='$CORR_R1';")"
  item "rodada1-agent-runs-completed" "6" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='COMPLETED';")"
  item "rodada1-agent-runs-rejected" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='REJECTED';")"
  item "rodada1-agent-runs-review-required" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1' AND status='REVIEW_REQUIRED';")"
  item "rodada1-auditoria-aponta-o-sinal" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs a JOIN sales_intelligence.signals s ON (a.output->>'signal_id') = s.id::text WHERE a.correlation_id='$CORR_R1';")"
  # Claim de sincronizacao: um por sinal gravado, amarrado ao sinal (base do desfazer).
  item "rodada1-sync-events-signal-success" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='SIGNAL' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_R1';")"
  item "rodada1-sync-event-amarra-o-sinal" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events e JOIN sales_intelligence.signals s ON e.entity_id = s.id WHERE e.operation='SIGNAL' AND e.status='SUCCESS';")"
  # A5: identidade ambigua vai para a fila humana, com claim proprio.
  item "rodada1-fila-humana-pendente" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='SIGNAL_IDENTITY_REVIEW' AND status='PENDING' AND proposed_action->'organizacoes_casadas' IS NOT NULL;")"
  item "rodada1-fila-humana-com-duas-casadas" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='SIGNAL_IDENTITY_REVIEW' AND jsonb_array_length(proposed_action->'organizacoes_casadas')=2;")"
  item "rodada1-sync-events-review" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='SIGNAL_REVIEW' AND status='SUCCESS';")"
  # Vinculo logico: aponta o research_run REAL e NAO escreve o inexistente.
  item "rodada1-vinculo-com-research-run-real" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE signal_type='HIRING' AND research_run_id='$RES_RUN';")"
  item "rodada1-descarte-de-vinculo-quebrado" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE signal_type='COST_REDUCTION' AND research_run_id IS NULL AND evidence->'descartados' @> '[{\"motivo\":\"RESEARCH_RUN_NAO_ENCONTRADO\"}]';")"
  # Descartes com motivo, medidos no proprio sinal (nao na narrativa de quem rodou).
  item "rodada1-descarte-de-data" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE signal_type='COST_REDUCTION' AND event_date IS NULL AND evidence->'descartados' @> '[{\"motivo\":\"DATA_INVALIDA\"}]';")"
  item "rodada1-descarte-de-confianca" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE signal_type='COST_REDUCTION' AND confidence IS NULL AND evidence->'descartados' @> '[{\"motivo\":\"CONFIANCA_FORA_DA_FAIXA\"}]';")"
  item "rodada1-descarte-de-derivado" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE evidence->'descartados' @> '[{\"motivo\":\"DERIVADO_NAO_ACEITO\"}]';")"
  item "rodada1-descarte-de-campo-nao-declarado" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE evidence->'descartados' @> '[{\"motivo\":\"CAMPO_NAO_DECLARADO\"}]';")"
  item "rodada1-descarte-de-titulo-longo" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals WHERE signal_type='NEW_LOCATION' AND title IS NULL AND evidence->'descartados' @> '[{\"motivo\":\"TITULO_ACIMA_DO_LIMITE\"}]';")"
  # A3: a empresa NAO foi criada nem tocada (updated_at segue no valor semeado).
  item "rodada1-organizacoes-total" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations;")"
  item "rodada1-nao-cria-organizacao" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE id NOT IN ('$ORG_A','$ORG_B','$ORG_C');")"
  item "rodada1-nao-toca-a-empresa" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE updated_at = '2000-01-01T00:00:00Z' AND status='DISCOVERED';")"
  item "rodada1-nao-cria-research-run" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"
  item "rodada1-nenhuma-outra-tabela-escrita" "0" \
    "$(contagem "SELECT (SELECT count(*) FROM sales_intelligence.contacts) + (SELECT count(*) FROM sales_intelligence.pain_hypotheses) + (SELECT count(*) FROM sales_intelligence.scores) + (SELECT count(*) FROM sales_intelligence.interactions) + (SELECT count(*) FROM sales_intelligence.recommendations) + (SELECT count(*) FROM sales_intelligence.outbox_events);")"

  # ---- rodada 2 (mesma fonte: retry nao duplica) -----------------------------------
  rodar_signal "$TRABALHO/r2.json" --ambiente dev --correlation-id "$CORR_R2" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r2.out" 2>&1
  item "rodada2-exit-0" "0" "$?"
  item "rodada2-nao-duplica" "5" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "rodada2-nada-detectado-de-novo" "0" \
    "$(veredito_do_relatorio "$TRABALHO/r2.json" DETECTADO)"
  item "rodada2-seis-replays" "6" \
    "$(veredito_do_relatorio "$TRABALHO/r2.json" JA_DETECTADO)"
  item "rodada2-sem-novo-claim" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R2';")"
  item "rodada2-fila-humana-nao-duplica" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='SIGNAL_IDENTITY_REVIEW';")"
  item "rodada2-empresa-intocada" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE updated_at = '2000-01-01T00:00:00Z';")"

  # ---- rodada 3: MESMA chave, com o sinal ausente (retentativa real) ---------------
  # Sem o `ON CONFLICT (idempotency_key) DO NOTHING` o reenvio estoura UNIQUE e vira ERRO; sem a
  # ancora no sinal DESTA rodada, o fechamento marcaria sucesso sem sinal — os dois defeitos sao
  # medidos aqui (e a prova de dente os muta).
  psql_t -c "DELETE FROM sales_intelligence.signals WHERE signal_type='ERP_CHANGE';" >/dev/null
  sed -n '2p' "$FONTE" > "$TRABALHO/replay.jsonl"
  rodar_signal "$TRABALHO/r3.json" --ambiente dev --correlation-id "$CORR_R3" \
    --fonte "$TRABALHO/replay.jsonl" --prefixo "$PREFIXO" > "$TRABALHO/r3.out" 2>&1
  item "rodada3-exit-0" "0" "$?"
  item "rodada3-sem-erro" "0" "$(veredito_do_relatorio "$TRABALHO/r3.json" ERRO)"
  item "rodada3-nao-recria-o-sinal" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "rodada3-ja-detectado-um" "1" \
    "$(veredito_do_relatorio "$TRABALHO/r3.json" JA_DETECTADO)"
  item "rodada3-sem-novo-claim" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE request_payload->>'correlation_id'='$CORR_R3';")"

  # ---- guarda de ambiente (ADR-005) e modo sem banco ------------------------------
  rodar_signal "$TRABALHO/r4.json" --ambiente prod --correlation-id "$CORR_R4" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r4.out" 2>&1
  item "prod-recusado-exit-4" "4" "$?"
  item "prod-nao-escreveu-sinal" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  item "prod-nao-registrou-execucao" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R4';")"
  python3 "$SIGNAL_PY" --raiz "$RAIZ" --planejar --fonte "$FONTE" \
    --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" \
    > "$TRABALHO/r5.out" 2>&1
  item "planejar-exit-0-sem-conectar" "0" "$?"
  item "planejar-nao-escreveu" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"

  # ---- desfazer -------------------------------------------------------------------
  rodar_signal "$TRABALHO/dry.json" --desfazer "$CORR_R1" --ambiente dev \
    --prefixo "$PREFIXO" > "$TRABALHO/dry.out" 2>&1
  item "desfazer-dry-run-exit-0" "0" "$?"
  item "desfazer-dry-run-nao-apagou" "4" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  rodar_signal "$TRABALHO/del.json" --desfazer "$CORR_R1" --ambiente dev --confirmo \
    --prefixo "$PREFIXO" > "$TRABALHO/del.out" 2>&1
  item "desfazer-confirmo-exit-0" "0" "$?"
  item "desfazer-apagou-so-a-rodada" "0" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.signals;")"
  # O sinal apagado por fora na rodada 3 deixa o `sync_events` dele ORFAO (visivel de proposito, doc
  # §9): o desfazer alcanca o que a rodada criou E ainda existe. Dos 5 claims de sinal, 4 sao
  # apagados com os sinais e 1 fica como trilha do que foi removido por fora.
  item "desfazer-deixa-o-claim-orfao-visivel" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='SIGNAL';")"
  item "desfazer-nao-deixou-sinal-orfao-de-claim" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events e WHERE e.operation='SIGNAL' AND NOT EXISTS (SELECT 1 FROM sales_intelligence.signals s WHERE s.id = e.entity_id);")"
  item "desfazer-registrou-rollback" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='ROLLBACK';")"
  item "desfazer-preservou-a-auditoria" "12" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.agent_runs WHERE correlation_id='$CORR_R1';")"
  item "desfazer-preservou-a-fila-humana" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.human_approvals WHERE action_type='SIGNAL_IDENTITY_REVIEW';")"
  item "desfazer-preservou-o-claim-da-revisao" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE operation='SIGNAL_REVIEW';")"
  item "desfazer-preservou-a-empresa" "3" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.organizations WHERE status='DISCOVERED' AND updated_at = '2000-01-01T00:00:00Z';")"
  item "desfazer-preservou-a-pesquisa" "1" \
    "$(contagem "SELECT count(*) FROM sales_intelligence.research_runs;")"

  echo "RESULTADO: ACEITE_SIGNAL_001_$( [ "$ITENS_FALHOU" -eq 0 ] && echo OK || echo FALHOU ) ($((ITENS_OK + ITENS_FALHOU)) itens, $ITENS_FALHOU falhas)"
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
sem-idempotencia|        "  ON CONFLICT (idempotency_key) DO NOTHING\\n"\n        "  RETURNING id\\n"\n        ")\\n"\n        "INSERT INTO {sinais} ({cols})\\n"|        "  RETURNING id\\n"\n        ")\\n"\n        "INSERT INTO {sinais} ({cols})\\n"|rodada3-sem-erro
fechamento-sem-ancora-no-sinal|        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {sinais} WHERE id = {sid})\\n"|        "WHERE idempotency_key = {chave}\\n"|rodada3-ja-detectado-um
categoria-chumbada|    return CATEGORIAS_POR_TIPO.get(tipo)|    return "EXPANSAO"|rodada1-categoria-derivada-do-tipo
vinculo-quebrado-aceito|                if research_run_id and not self.research_run_existe(research_run_id):|                if False:|rodada1-descarte-de-vinculo-quebrado
EOF

  local total="${#linhas[@]}" detectadas=0 falhas=0
  local nome alvo substituto esperados destino guardado faltando esperado
  for linha in "${linhas[@]}"; do
    IFS='|' read -r nome alvo substituto esperados <<< "$linha"
    [ -z "$nome" ] && continue
    alvo="$(printf '%b' "$alvo")"
    substituto="$(printf '%b' "$substituto")"
    destino="$TRABALHO/mut-$nome/signal.py"
    mkdir -p "$(dirname "$destino")"
    if ! aplicar_mutacao "$SIGNAL_PY" "$destino" "$alvo" "$substituto" | grep -q MUTACAO_APLICADA; then
      echo "FALHOU mutacao $nome NAO se aplicou (ancora mudou) — buraco de verificacao"
      falhas=$((falhas + 1)); continue
    fi
    echo
    echo "-- mutacao: $nome (tem de reprovar: $(echo "$esperados" | tr ',' ' '))"
    guardado="$SIGNAL_PY"
    SIGNAL_PY="$destino"
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
    SIGNAL_PY="$guardado"
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
  echo "== ACEITE DO AGENTE SIGNAL DETECTOR v1 (TRE-W4-E03-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0
  if rodar_aceite "principal"; then ok=1; fi
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_SIGNAL_001_OK"
    return 0
  fi
  echo "ACEITE_SIGNAL_001_FALHOU"
  return 1
}

principal
