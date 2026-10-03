#!/usr/bin/env bash
# =====================================================================================
# teste_contact_research_aceite.sh [--prova-de-dente] [--manter] [--codigo <agente.py>]
#
# ACEITE DO AGENTE CONTACT RESEARCH v1 — TRE-W4-E05-T01. Roda ON DE A VPS do ambiente
# (ADR-0008: o PostgreSQL vive la; o container do Hermes so' orquestra por SSH).
#
# O que ele faz, em um container PostgreSQL DESCARTÁVEL (nunca toca pg-sales-dev,
# pg-odoo-dev nem qualquer outro container):
#   0. guardas ....... docker/python3/migration presentes; o nome do container NAO pode
#                      existir (se existir, ABORTA em vez de mexer no que nao e' dele)
#   1. sobe ......... container novo com a migration 0001 + 3 organizacoes pre-existentes
#                      (uma delas com industry_name JA preenchido: e' o dado curado) + 1
#                      contato pre-existente com e-mail em CAIXA ALTA, cargo curado e
#                      do_not_contact/opt_out_email TRUE
#   2. rodada 1 ..... 11 pedidos sinteticos: 4 contatos novos legitimos, 1 que ENRIQUECE o
#                      contato semeado (identidade em caixa alta casa sem diferenca de caixa),
#                      1 organizacao inexistente, 1 sem identificador forte, 1 com e-mail
#                      invalido, 1 sem base legal, 1 sem fonte e 1 conflito de identidade
#   3. rodada 2 ..... a MESMA fonte de novo: 0 contato novo (retry nao cria duplicata)
#   4. rodada 3 ..... replay com a chave JA reivindicada e o contato ausente (prova o
#                      ON CONFLICT (idempotency_key) DO NOTHING e o fechamento ancorado no
#                      sync_event DESTA rodada)
#   5. guardas ...... --ambiente prod recusado; --planejar com prefixo inexistente nao conecta
#   6. evento ....... a elegibilidade de DECISION_MAKER_FOUND e' MEDIDA na evidencia e a fila
#                      de outbox fica VAZIA (o consumidor versionado nao cobre o evento)
#   7. desfazer ..... dry-run nao apaga; contato criado JA ESPELHADO recusa o desfazer;
#                      --confirmo restaura o valor anterior das colunas da rodada, apaga os
#                      contatos CRIADOS pela rodada e registra ROLLBACK, preservando o contato
#                      semeado (cargo curado e opt-out do titular)
#   8. veredito ..... ACEITE_CONTACT_RESEARCH_001_OK / ACEITE_CONTACT_RESEARCH_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do agente (idempotencia, formato que nao
#   sobrescreve, identidade sem lower() e guarda do rollback espelhado) e exige que o aceite
#   REPROVE **o item esperado de cada mutacao**; baseline verde antes e depois. Mutacao que nao
#   se aplica na ancora tambem reprova (e' buraco de verificacao, nao alivio), e o veredito
#   imprime a contagem MEDIDA.
#
# NOTA (defeito medido na revisao independente do card irmao, rodada 1): o corpo do laco de
#   mutacoes chama `docker exec -i`, que CONSOME o stdin do laco. Aqui as mutacoes sao lidas numa
#   LISTA antes do laco (o laco nao depende de stdin) e todo `docker exec` que nao le stdin leva
#   `</dev/null`.
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_CONTACT_CONTAINER (default pg-contact-acc), TRE_CONTACT_TRABALHO.
# Exit: 0 = ACEITE_CONTACT_RESEARCH_001_OK · 1 = FALHOU · 2 = uso/guarda.
# =====================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_CONTACT_CONTAINER:-pg-contact-acc}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="contact-aceite-descartavel"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
AGENTE_PY="$RAIZ/hermes/agents/contact_research/contact_research.py"
TRABALHO="${TRE_CONTACT_TRABALHO:-$(mktemp -d /tmp/contact-aceite-XXXXXX)}"

MANTER=0
DENTE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter)         MANTER=1 ;;
    --codigo)         AGENTE_PY="$2"; shift ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <agente.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <agente.py>]"; exit 2 ;;
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

ORG_A="aaaaaaaa-0000-4000-8000-000000000001"
ORG_B="aaaaaaaa-0000-4000-8000-000000000002"
ORG_C="aaaaaaaa-0000-4000-8000-000000000003"
CONTATO_SEMEADO="cccccccc-0000-4000-8000-00000000000f"
EMAIL_SEMEADO_CAIXA_ALTA="MARINA.ALVES@VALEFORTE.COM.BR"
EMAIL_SEMEADO_MINUSCULO="marina.alves@valeforte.com.br"

preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
  psql_stdin >/dev/null <<SQL
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, cnpj, domain, linkedin_url, industry_name, status, source,
   created_at, updated_at)
VALUES
  ('$ORG_A', 'Metalurgica Vale Forte Ltda', 'Vale Forte',
   '11222333000181', 'valeforte.com.br', NULL, 'Metalurgia', 'DISCOVERED', 'LINKEDIN', now(), now()),
  ('$ORG_B', 'AgroSmart Analytics Ltda', 'AgroSmart Analytics',
   NULL, 'agrosmart-analytics.com.br', NULL, NULL, 'DISCOVERED', 'WEB', now(), now()),
  ('$ORG_C', 'Clinica Sao Lucas S.A.', 'Clinica Sao Lucas',
   NULL, NULL, 'https://www.linkedin.com/company/clinica-sao-lucas', NULL, 'DISCOVERED',
   'EVENTOS', now(), now());

-- Contato pre-existente: identidade em CAIXA ALTA, cargo curado e flags do titular LIGADOS.
-- Ele e' o alvo de tres provas: casamento sem diferenca de caixa, cargo que nao se sobrescreve
-- e opt-out que o agente nunca escreve (nem para limpar).
INSERT INTO sales_intelligence.contacts
  (id, organization_id, full_name, job_title, email, legal_basis, do_not_contact, opt_out_email,
   opt_out_whatsapp, source, created_at, updated_at)
VALUES
  ('$CONTATO_SEMEADO', '$ORG_A', 'Marina Alves', 'Gerente de Operacoes (curado)',
   '$EMAIL_SEMEADO_CAIXA_ALTA', 'CONSENT', TRUE, TRUE, FALSE, 'SEED', now(), now());
SQL
}

escrever_fonte() { # <arquivo>
  cat > "$1" <<'JSONL'
{"organizacao":{"cnpj":"11222333000181"},"contato":{"full_name":"Camila Souza","email":"camila.souza@valeforte.com.br","job_title":"Head de Operacoes","department":"Operacoes","seniority":"Diretoria","decision_role":"Decision Maker","preferred_channel":"EMAIL","legal_basis":"LEGITIMATE_INTEREST"},"fontes":[{"tipo":"LINKEDIN","url":"https://www.linkedin.com/in/camila-souza","trecho":"Head de Operacoes na Vale Forte"}],"confianca":0.8}
{"organizacao":{"domain":"valeforte.com.br"},"contato":{"full_name":"Paulo Lima","email":"paulo.lima@valeforte.com.br","department":"Compras","legal_basis":"LEGITIMATE_INTEREST"},"fontes":[{"tipo":"WEB","url":"https://valeforte.com.br/fornecedores","trecho":"Compras - Paulo Lima"}],"confianca":0.5}
{"organizacao":{"domain":"agrosmart-analytics.com.br"},"contato":{"full_name":"Joao Bertoldo","email":"joao.bertoldo@agrosmart-analytics.com.br","job_title":"Chefe Geral","decision_role":"Chefe Geral","legal_basis":"PUBLIC_DATA"},"fontes":[{"tipo":"DADOS_PUBLICOS","url":"https://empresas.example/agrosmart/qsa","trecho":"Joao Bertoldo - Administrador"}]}
{"organizacao":{"cnpj":"11222333000181"},"contato":{"full_name":"Marina Alves","email":"  Marina.Alves@ValeForte.Com.BR  ","job_title":"Diretora de Operacoes","seniority":"Diretoria","legal_basis":"CONSENT"},"fontes":[{"tipo":"LINKEDIN","url":"https://www.linkedin.com/in/marina-alves","trecho":"Diretora de Operacoes desde 2021"}],"confianca":0.9}
{"organizacao":{"cnpj":"11222333000181"},"contato":{"full_name":"Rita Nogueira","email":"rita.nogueira@valeforte.com.br","job_title":"Coordenadora","legal_basis":"LEGITIMATE_INTEREST","odoo_partner_id":77,"influence_score":9.9},"fontes":[{"tipo":"EVENTOS","trecho":"lista de presenca do evento de transformacao digital"}]}
{"organizacao":{"domain":"inexistente.example"},"contato":{"full_name":"Alguem Silva","email":"alguem@inexistente.example","legal_basis":"CONSENT"},"fontes":[{"tipo":"WEB","trecho":"empresa que nao existe na base"}]}
{"organizacao":{"city":"Santos"},"contato":{"full_name":"Sem Forte","email":"sem.forte@example.com","legal_basis":"CONSENT"},"fontes":[{"tipo":"WEB","trecho":"sem identificador forte de empresa"}]}
{"organizacao":{"cnpj":"11222333000181"},"contato":{"full_name":"Email Torto","email":"email.torto@valeforte","legal_basis":"CONSENT"},"fontes":[{"tipo":"WEB","trecho":"e-mail sem TLD"}]}
{"organizacao":{"cnpj":"11222333000181"},"contato":{"full_name":"Sem Base Legal","email":"sem.base@valeforte.com.br","job_title":"Gerente"},"fontes":[{"tipo":"WEB","trecho":"contato sem base legal declarada"}]}
{"organizacao":{"cnpj":"11222333000181","domain":"agrosmart-analytics.com.br"},"contato":{"full_name":"Conflito Identidade","email":"conflito@example.com","legal_basis":"CONSENT"},"fontes":[{"tipo":"WEB","trecho":"dois fortes de empresas diferentes"}]}
{"organizacao":{"cnpj":"11222333000181"},"contato":{"full_name":"Sem Fonte","email":"sem.fonte@valeforte.com.br","legal_basis":"CONSENT"},"fontes":[]}
JSONL
}

rodar_agente() { # <relatorio> <args...>
  local relatorio="$1"; shift
  # `</dev/null`: o agente nunca le stdin e a porta psql usa `input=` — nada aqui pode
  # consumir o stdin de um laco que chame rodar_agente.
  python3 "$AGENTE_PY" --raiz "$RAIZ" --relatorio "$relatorio" "$@" </dev/null
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
FONTE="$TRABALHO/contatos.jsonl"
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

SL="sales_intelligence"
CONTAGEM_OUTBOX="SELECT count(*) FROM ${SL}.outbox_events;"
CONTAGEM_OUTRAS="SELECT (SELECT count(*) FROM ${SL}.signals) + (SELECT count(*) FROM ${SL}.pain_hypotheses) + (SELECT count(*) FROM ${SL}.research_runs) + (SELECT count(*) FROM ${SL}.scores) + (SELECT count(*) FROM ${SL}.interactions) + (SELECT count(*) FROM ${SL}.recommendations);"

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== aceite do codigo sob teste: $AGENTE_PY ($rotulo)"
  preparar_banco
  escrever_fonte "$FONTE"

  # ---- rodada 1 --------------------------------------------------------------------
  rodar_agente "$TRABALHO/r1.json" --ambiente dev --correlation-id "$CORR_R1" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r1.out" 2>&1
  item "rodada1-exit-0" "0" "$?"
  item "rodada1-organizacoes-sem-escrita" "3" "$(contagem "SELECT count(*) FROM ${SL}.organizations;")"
  item "rodada1-organizacao-curada-intocada" "1" "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE id='$ORG_A' AND industry_name='Metalurgia' AND status='DISCOVERED';")"
  item "rodada1-contatos-total" "5" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "rodada1-contatos-criados" "4" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE id <> '$CONTATO_SEMEADO';")"
  item "rodada1-contato-com-papel-de-decisao" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE email='camila.souza@valeforte.com.br' AND decision_role='Decision Maker' AND seniority='Diretoria' AND preferred_channel='EMAIL';")"
  item "rodada1-papel-fora-do-vocabulario-descartado" "1" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND output->'descartados' @> '[{\"motivo\":\"PAPEL_FORA_DO_VOCABULARIO\"}]';")"
  item "rodada1-papel-invalido-nao-escrito" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE email='joao.bertoldo@agrosmart-analytics.com.br' AND decision_role IS NULL AND job_title='Chefe Geral';")"
  item "rodada1-campo-nao-declarado-descartado" "1" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND output->'descartados' @> '[{\"motivo\":\"CAMPO_NAO_DECLARADO\"}]';")"
  item "rodada1-campo-proibido-nao-escrito" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE email='rita.nogueira@valeforte.com.br' AND odoo_partner_id IS NULL AND influence_score IS NULL AND job_title='Coordenadora';")"
  # A identidade em CAIXA ALTA casa sem diferenca de caixa: UM contato para esse e-mail, e o
  # enriquecimento chegou nele (seniority), sem tocar no que ja' estava preenchido.
  item "rodada1-identidade-casa-sem-diferenca-de-caixa" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE lower(email)='$EMAIL_SEMEADO_MINUSCULO';")"
  item "rodada1-enriquecimento-nao-sobrescreve" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE id='$CONTATO_SEMEADO' AND job_title='Gerente de Operacoes (curado)' AND legal_basis='CONSENT' AND seniority='Diretoria';")"
  item "rodada1-identidade-nao-reescrita" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE id='$CONTATO_SEMEADO' AND email='$EMAIL_SEMEADO_CAIXA_ALTA';")"
  item "rodada1-opt-out-do-titular-preservado" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE id='$CONTATO_SEMEADO' AND do_not_contact AND opt_out_email AND NOT opt_out_whatsapp;")"
  item "rodada1-agent-runs-por-pedido" "11" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE agent_name='contact_research' AND agent_role='contact_research' AND correlation_id='$CORR_R1';")"
  item "rodada1-agent-runs-completed" "5" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND status='COMPLETED';")"
  item "rodada1-agent-runs-review" "1" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND status='REVIEW_REQUIRED';")"
  item "rodada1-agent-runs-rejected" "5" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND status='REJECTED';")"
  item "rodada1-sync-events-success" "5" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_R1';")"
  item "rodada1-input-hash-presente" "5" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH' AND request_payload->>'input_hash' ~ '^[0-9a-f]{64}\$';")"
  item "rodada1-uuid-v4-no-produtor" "5" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH' AND request_payload->>'contato_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\$';")"
  item "rodada1-antes-depois-gravados" "5" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH' AND request_payload->'antes' IS NOT NULL AND request_payload->'depois' IS NOT NULL;")"
  item "rodada1-criados-e-enriquecidos-declarados" "4|1" "$(contagem "SELECT count(*) FILTER (WHERE request_payload->>'acao'='criado') || '|' || count(*) FILTER (WHERE request_payload->>'acao'='enriquecido') FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH';")"
  item "rodada1-sem-llm-na-execucao" "11" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND model IS NULL AND tokens_input IS NULL AND tokens_output IS NULL AND estimated_cost IS NULL;")"
  item "rodada1-fila-humana-pendente" "1" "$(contagem "SELECT count(*) FROM ${SL}.human_approvals WHERE action_type='CONTACT_IDENTITY_REVIEW' AND status='PENDING';")"
  item "rodada1-fila-humana-sem-contato-escrito" "0" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE email='conflito@example.com';")"
  item "rodada1-eventos-nunca-escritos" "0" "$(contagem "$CONTAGEM_OUTBOX")"
  # A evidencia do evento existe em TODO pedido que chegou a escrever contato (5 criados/enriquecidos);
  # os pedidos recusados/revisados nao tem rodada de contato, entao nao tem espelho a medir.
  item "rodada1-evidencia-do-evento-elegivel" "1" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND output->'evento_de_espelho'->>'elegivel'='true' AND output->'evento_de_espelho'->>'emitido'='false';")"
  item "rodada1-evento-nunca-emitido-em-pedido-escrito" "5" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1' AND output->'evento_de_espelho'->>'emitido'='false';")"
  item "rodada1-nenhuma-outra-tabela-escrita" "0" "$(contagem "$CONTAGEM_OUTRAS")"
  item "rodada1-relatorio-vereditos" "IDENTIFICADO=5 JA_IDENTIFICADO=0 REVISAO_IDENTIDADE=1 RECUSADA=5" \
    "IDENTIFICADO=$(veredito_do_relatorio "$TRABALHO/r1.json" IDENTIFICADO) JA_IDENTIFICADO=$(veredito_do_relatorio "$TRABALHO/r1.json" JA_IDENTIFICADO) REVISAO_IDENTIDADE=$(veredito_do_relatorio "$TRABALHO/r1.json" REVISAO_IDENTIDADE) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r1.json" RECUSADA)"

  # ---- rodada 2 (mesma fonte: retry nao cria duplicata) -----------------------------
  rodar_agente "$TRABALHO/r2.json" --ambiente dev --correlation-id "$CORR_R2" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r2.out" 2>&1
  item "rodada2-exit-0" "0" "$?"
  item "rodada2-nao-duplica" "5" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "rodada2-identificados-zero" "0" "$(veredito_do_relatorio "$TRABALHO/r2.json" IDENTIFICADO)"
  item "rodada2-ja-identificado-cinco" "5" "$(veredito_do_relatorio "$TRABALHO/r2.json" JA_IDENTIFICADO)"
  item "rodada2-sem-novo-claim" "0" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE request_payload->>'correlation_id'='$CORR_R2';")"
  item "rodada2-sem-nova-auditoria-de-escrita" "0" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH' AND status <> 'SUCCESS';")"
  item "rodada2-contato-semeado-intocado" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE id='$CONTATO_SEMEADO' AND job_title='Gerente de Operacoes (curado)' AND seniority='Diretoria';")"

  # ---- rodada 3: a MESMA chave reapresentada com o contato ausente ------------------
  # Cenario real de concorrencia/retentativa: a chave ja' foi reivindicada, mas o contato nao
  # esta' la' (foi removido depois). Sem o `ON CONFLICT (idempotency_key) DO NOTHING` o reenvio
  # estoura UNIQUE e vira ERRO; com a guarda, e' replay silencioso: nao recria e nao marca
  # sucesso de rodada nova.
  psql_t -c "DELETE FROM ${SL}.contacts WHERE email='camila.souza@valeforte.com.br';" >/dev/null
  sed -n '1p' "$FONTE" > "$TRABALHO/replay.jsonl"
  rodar_agente "$TRABALHO/r5.json" --ambiente dev --correlation-id "$CORR_R3" \
    --fonte "$TRABALHO/replay.jsonl" --prefixo "$PREFIXO" > "$TRABALHO/r5.out" 2>&1
  item "rodada3-exit-0" "0" "$?"
  item "rodada3-sem-erro" "0" "$(veredito_do_relatorio "$TRABALHO/r5.json" ERRO)"
  item "rodada3-ja-identificado-um" "1" "$(veredito_do_relatorio "$TRABALHO/r5.json" JA_IDENTIFICADO)"
  item "rodada3-nao-recria-contato" "4" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "rodada3-sem-novo-claim" "0" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE request_payload->>'correlation_id'='$CORR_R3';")"

  # ---- guarda de ambiente (ADR-005) e modo sem banco ------------------------------
  rodar_agente "$TRABALHO/r3.json" --ambiente prod --correlation-id "$CORR_R4" \
    --fonte "$FONTE" --prefixo "$PREFIXO" > "$TRABALHO/r3.out" 2>&1
  item "prod-recusado-exit-4" "4" "$?"
  item "prod-nao-escreveu-contato" "4" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "prod-nao-registrou-execucao" "0" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R4';")"
  python3 "$AGENTE_PY" --raiz "$RAIZ" --planejar --fonte "$FONTE" \
    --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" \
    > "$TRABALHO/r4.out" 2>&1
  item "planejar-exit-0-sem-conectar" "0" "$?"
  item "planejar-nao-escreveu" "4" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"

  # ---- desfazer -------------------------------------------------------------------
  rodar_agente "$TRABALHO/dry.json" --desfazer "$CORR_R1" --ambiente dev \
    --prefixo "$PREFIXO" > "$TRABALHO/dry.out" 2>&1
  item "desfazer-dry-run-exit-0" "0" "$?"
  item "desfazer-dry-run-nao-apagou" "4" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "desfazer-dry-run-nao-registrou-rollback" "0" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='ROLLBACK';")"

  # contato criado pela rodada que JA' foi espelhado no CRM: o desfazer RECUSA a rodada
  psql_t -c "UPDATE ${SL}.contacts SET odoo_partner_id = 987 WHERE email='rita.nogueira@valeforte.com.br';" >/dev/null
  rodar_agente "$TRABALHO/espelho.json" --desfazer "$CORR_R1" --ambiente dev --confirmo \
    --prefixo "$PREFIXO" > "$TRABALHO/espelho.out" 2>&1
  item "desfazer-recusa-contato-espelhado" "1" "$?"
  item "desfazer-recusado-nao-apagou" "4" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "desfazer-recusado-sem-rollback" "0" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='ROLLBACK';")"
  item "desfazer-recusado-mantem-sync-events" "5" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH' AND request_payload->>'correlation_id'='$CORR_R1';")"
  psql_t -c "UPDATE ${SL}.contacts SET odoo_partner_id = NULL WHERE email='rita.nogueira@valeforte.com.br';" >/dev/null

  rodar_agente "$TRABALHO/del.json" --desfazer "$CORR_R1" --ambiente dev --confirmo \
    --prefixo "$PREFIXO" > "$TRABALHO/del.out" 2>&1
  item "desfazer-confirmo-exit-0" "0" "$?"
  item "desfazer-apagou-so-o-que-a-rodada-criou" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "desfazer-preservou-o-contato-semeado" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE id='$CONTATO_SEMEADO' AND job_title='Gerente de Operacoes (curado)' AND do_not_contact AND opt_out_email;")"
  item "desfazer-restaurou-a-coluna-enriquecida" "1" "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE id='$CONTATO_SEMEADO' AND seniority IS NULL;")"
  item "desfazer-apagou-sync-events-da-rodada" "0" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='CONTACT_RESEARCH' AND request_payload->>'correlation_id'='$CORR_R1';")"
  item "desfazer-registrou-rollback" "1" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='ROLLBACK';")"
  item "desfazer-preservou-auditoria" "11" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_R1';")"
  # 2 = a ambiguidade reaparece na rodada 1 e no replay da rodada 2 (a fila humana e' do conflito de
  # identidade da EMPRESA, nao da rodada): o desfazer nao apaga nenhuma delas.
  item "desfazer-preservou-a-fila-humana" "2" "$(contagem "SELECT count(*) FROM ${SL}.human_approvals WHERE action_type='CONTACT_IDENTITY_REVIEW';")"
  item "desfazer-nao-tocou-organizacoes" "3" "$(contagem "SELECT count(*) FROM ${SL}.organizations;")"
  item "desfazer-nao-escreveu-evento" "0" "$(contagem "$CONTAGEM_OUTBOX")"

  echo "RESULTADO: ACEITE_CONTACT_RESEARCH_001_$( [ "$ITENS_FALHOU" -eq 0 ] && echo OK || echo FALHOU ) ($((ITENS_OK + ITENS_FALHOU)) itens, $ITENS_FALHOU falhas)"
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
  # Formato: nome|alvo|substituto|itens-esperados (o dente exige o item, nao so' "falhou").
  local linhas=() linha
  while IFS= read -r linha; do
    [ -n "$linha" ] && linhas+=("$linha")
  done <<'EOF'
sem-idempotencia|        "ON CONFLICT (idempotency_key) DO NOTHING\n"|        "\n"|rodada2-exit-0,rodada2-ja-identificado-cinco
enriquecimento-sem-coalesce|    return "%s = COALESCE(NULLIF(%s, ''), %s)" % (coluna, coluna, lit(valor))|    return "%s = %s" % (coluna, lit(valor))|rodada1-enriquecimento-nao-sobrescreve
identidade-sem-lower|        "WHERE organization_id = %s AND lower(email) = %s ORDER BY id;" % (|        "WHERE organization_id = %s AND email = %s ORDER BY id;" % (|rodada1-identidade-casa-sem-diferenca-de-caixa
rollback-sem-guarda-de-espelho|        if espelhados:|        if False:|desfazer-recusa-contato-espelhado
EOF

  local total="${#linhas[@]}" detectadas=0 falhas=0
  local nome alvo substituto esperados destino guardado faltando esperado
  for linha in "${linhas[@]}"; do
    IFS='|' read -r nome alvo substituto esperados <<< "$linha"
    [ -z "$nome" ] && continue
    destino="$TRABALHO/mut-$nome/contact_research.py"
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
  echo "== ACEITE DO AGENTE CONTACT RESEARCH v1 (TRE-W4-E05-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0
  if rodar_aceite "principal"; then ok=1; fi
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_CONTACT_RESEARCH_001_OK"
    return 0
  fi
  echo "ACEITE_CONTACT_RESEARCH_001_FALHOU"
  return 1
}

principal
