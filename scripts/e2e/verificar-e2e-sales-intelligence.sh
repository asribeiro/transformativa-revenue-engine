#!/usr/bin/env bash
# =====================================================================================
# verificar-e2e-sales-intelligence.sh [--prova-de-dente] [--manter]
#
# ACEITE E2E "SALES INTELLIGENCE" — card TRE-W4-E06-T01 (W4 · EPIC E06 · doc 07 §7).
# Roda NA VPS do ambiente (ADR-0008: o PostgreSQL vive la; o container do Hermes so'
# orquestra por SSH). Tudo acontece num UNICO container PostgreSQL DESCARTAVEL proprio
# (`pg-e2e-si-acc`): os cinco agentes da onda W4 rodam em SEQUENCIA no MESMO banco, com o
# que um escreve entrando como entrada do proximo. Medir cada agente no proprio container
# (como fazem os aceites de origem) NAO prova encadeamento; aqui e' o mesmo banco do
# primeiro ao ultimo passo.
#
# GATE DA ONDA (doc 07 §7): "empresa → research/signals/hypothesis/contacts".
# O que este aceite mede, e o que ele NAO mede (leia antes de citar o veredito):
#   * MEDE a cadeia W4-A (Hermes Sales Intelligence): Scout cria a empresa -> Research
#     pesquisa/enriquece a MESMA empresa -> Signal grava o fato datado ANCORADO na
#     pesquisa daquela empresa -> Pain Hypothesis grava a hipotese com LASTRO nos sinais e
#     na pesquisa daquela empresa -> Contact Research grava o contato comercial daquela
#     empresa. Tudo no mesmo banco, com o id produzido por um agente lido pelo outro.
#   * NAO MEDE a onda W5 (score/tier/NBA), o outbox/Odoo/Titan (W3/W6) nem promocao a
#     producao. Onde o doc 08 §3 cita score/tier/NBA, o item aqui e' de ZERO/vazio —
#     declarado, nunca OK: a cadeia W4 nao calcula score por desenho.
#
# CRITERIOS DE ACEITACAO (definidos no inicio da execucao do card e registrados na thread):
#   AC1  os cinco agentes rodam em SEQUENCIA no mesmo banco, cada um lendo o que o anterior
#        escreveu (id de empresa, de pesquisa e de sinal), sem semeacao manual de resultado;
#   AC2  Scout: 3 candidatas -> 3 empresas, trilha em sync_events, auditoria por pedido;
#   AC3  Research: enriquece coluna VAZIA e NAO sobrescreve o que o Scout escreveu
#        (industria/porte do Scout preservados, website vazio preenchido);
#   AC4  Signal: o sinal da empresa aponta para o research_run DAQUELA empresa; vinculo com
#        research_run inexistente e' DESCARTADO com motivo (o fato nao se perde);
#   AC5  Pain Hypothesis: lastro tem de existir E ser da MESMA empresa — evidencia de outra
#        empresa e' RECUSADA (this is the cross-agent integrity tooth);
#   AC6  Contact Research: contato gravado na empresa do Scout, identidade ambigua vai para a
#        fila humana SEM escrever contato, empresa inexistente e' RECUSADA;
#   AC7  ZERO DUPLICATAS: repetir as cinco rodadas com as MESMAS fontes nao cria linha nova
#        e devolve o veredito de replay de cada agente;
#   AC8  fail-closed: `--ambiente prod` recusado (exit 4) nos CINCO agentes, sem escrita, e
#        `--planejar` nao abre conexao (prefixo de container inexistente);
#   AC9  ROLLBACK da cadeia inteira na ordem inversa (contato -> hipotese -> sinal ->
#        pesquisa -> scout) devolve o banco ao estado inicial, preserva auditoria e fila
#        humana e registra um sync_events de ROLLBACK por rodada;
#   AC10 ambiente: container descartavel proprio, os 4 containers persistentes do TRE
#        intactos, nada de producao; sha256 do codigo sob teste fixado na evidencia.
#
# TEST PLAN (por execucao real; ver o runbook docs/runbooks/e2e-sales-intelligence.md):
#   passo 0  suites offline dos CINCO agentes (sem banco) no MESMO commit do aceite;
#   guardas  docker/python3/migration/agentes presentes; o nome do container NAO pode existir
#            (se existir, ABORTA em vez de mexer no que nao e' dele);
#   sobe     container novo + migration 0001 em schema limpo;
#   A        Scout: 3 candidatas -> 3 empresas (o id de cada uma sai do RELATORIO);
#   B        Research: 4 pedidos nas MESMAS empresas -> 4 research_runs (id sai do relatorio);
#   C        Signal: 4 observacoes, 2 delas com o research_run_id produzido em B;
#   D        Pain: 3 hipoteses com lastro em B+C + 1 com lastro de OUTRA empresa (recusada);
#   E        Contact: 2 contatos + 1 conflito de identidade + 1 empresa inexistente;
#   F        Replay das cinco rodadas: zero duplicata em todas as tabelas de negocio;
#   G        Guardas: `prod` recusado nos cinco, `--planejar` sem porta, foto de contagens;
#   H        Desfazer a cadeia na ordem inversa: banco volta ao estado inicial;
#   fim      resumo em uma linha e exit code.
#
# --prova-de-dente: aplica UMA mutacao por agente (5 no total) em COPIA do codigo sob teste
#   e exige que o aceite REPROVE o ITEM ESPERADO de cada mutacao — "o aceite falhou" sozinho
#   NAO conta (mutacao que quebra a importacao contaria como detectada). Baseline verde antes
#   e depois; mutacao que nao se aplica na ancora tambem reprova (e' buraco de verificacao).
#
# Licoes ja' pagas (mantidas aqui de proposito): todo `docker exec -i` que nao le stdin leva
# `</dev/null`, senao ele CONSOME o stdin do laco de mutacoes; as mutacoes sao lidas numa LISTA
# antes do laco; `pg_isready` mente no inicio, entao a espera e' `SELECT 1` funcionando DUAS
# vezes com intervalo.
#
# Variaveis: TRE_E2E_RAIZ, TRE_E2E_IMAGEM (default postgres:16), TRE_E2E_SI_CONTAINER
# (default pg-e2e-si-acc), TRE_E2E_SI_TRABALHO, e um caminho de codigo por agente:
# TRE_E2E_SCOUT_PY, TRE_E2E_RESEARCH_PY, TRE_E2E_SIGNAL_PY, TRE_E2E_PAIN_PY,
# TRE_E2E_CONTACT_PY (default = o codigo versionado do repo).
# Exit: 0 = ACEITE_E2E_SALES_INTELLIGENCE_001_OK · 1 = FALHOU · 2 = uso/guarda.
# =====================================================================================
set -uo pipefail

RAIZ="${TRE_E2E_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IMAGEM="${TRE_E2E_IMAGEM:-postgres:16}"
CONTAINER="${TRE_E2E_SI_CONTAINER:-pg-e2e-si-acc}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="e2e-si-descartavel"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
AGENTE_SCOUT="${TRE_E2E_SCOUT_PY:-$RAIZ/hermes/agents/scout/scout.py}"
AGENTE_RESEARCH="${TRE_E2E_RESEARCH_PY:-$RAIZ/hermes/agents/research/research.py}"
AGENTE_SIGNAL="${TRE_E2E_SIGNAL_PY:-$RAIZ/hermes/agents/signal/signal.py}"
AGENTE_PAIN="${TRE_E2E_PAIN_PY:-$RAIZ/hermes/agents/pain_hypothesis/pain_hypothesis.py}"
AGENTE_CONTACT="${TRE_E2E_CONTACT_PY:-$RAIZ/hermes/agents/contact_research/contact_research.py}"
TRABALHO="${TRE_E2E_SI_TRABALHO:-$(mktemp -d /tmp/e2e-si-XXXXXX)}"

DENTE=0
MANTER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter)         MANTER=1 ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter]"; exit 2 ;;
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
for agente in "$AGENTE_SCOUT" "$AGENTE_RESEARCH" "$AGENTE_SIGNAL" "$AGENTE_PAIN" "$AGENTE_CONTACT"; do
  [ -f "$agente" ] || { echo "FALHOU agente ausente: $agente"; exit 2; }
done
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
  # `pg_isready` mente no inicio (servidor temporario da inicializacao): a espera e' `SELECT 1`
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

# ---------------------------------------------------------------------------------------
# Fontes (a mesma nos cinco agentes — e' a cadeia que o aceite mede)
# ---------------------------------------------------------------------------------------
preparar_banco() {
  psql_t -c "DROP SCHEMA IF EXISTS sales_intelligence CASCADE;" >/dev/null
  psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicar migration"; exit 2; }
}

escrever_candidatas() { # <arquivo> — o Scout descobrin as TRES empresas da cadeia
  cat > "$1" <<'JSONL'
{"legal_name":"Metalurgica Vale Forte Ltda","trade_name":"Vale Forte","cnpj":"11.222.333/0001-81","domain":"valeforte.com.br","industry_name":"Metalurgia","employee_count":420,"city":"Sao Bernardo do Campo","state":"SP","business_model":"B2B","source":"LINKEDIN","evidence":{"url":"https://www.linkedin.com/company/vale-forte","coletado_em":"2026-10-02"}}
{"trade_name":"AgroSmart Analytics","domain":"agrosmart-analytics.com.br","city":"Campinas","state":"SP","employee_count":95,"source":"WEB","evidence":{"url":"https://agrosmart-analytics.com.br/sobre","coletado_em":"2026-10-02"}}
{"legal_name":"Clinica Sao Lucas S.A.","trade_name":"Sao Lucas","linkedin_url":"https://br.linkedin.com/company/clinica-sao-lucas","industry_name":"Saude","employee_count":180,"city":"Curitiba","state":"PR","source":"EVENTOS","evidence":{"evento":"Sebrae SP - encontro de PMEs","coletado_em":"2026-10-01"}}
JSONL
}

escrever_pesquisas() { # <arquivo> — pesquisa as MESMAS empresas, por identificador forte
  cat > "$1" <<'JSONL'
{"organizacao":{"cnpj":"11.222.333/0001-81"},"tipo":"COMPANY_PROFILE","fontes":[{"tipo":"WEB","url":"https://valeforte.com.br/sobre","trecho":"Metalurgica com 40 anos de mercado"}],"achados":{"industry_name":"Metalurgia e Fundicao","employee_count":470,"city":"Santo Andre","website_url":"https://valeforte.com.br","business_model":"B2B"},"confianca":0.82}
{"organizacao":{"domain":"valeforte.com.br"},"tipo":"SIZE_AND_STRUCTURE","fontes":[{"tipo":"DADOS_PUBLICOS","url":"https://empresas.example/vale-forte","trecho":"470 colaboradores; 2 unidades"}],"achados":{"employee_band":"400_499","unit_count":2,"revenue_estimate":18500000},"confianca":0.7}
{"organizacao":{"domain":"agrosmart-analytics.com.br"},"tipo":"INDUSTRY","fontes":[{"tipo":"WEB","url":"https://agrosmart-analytics.com.br/sobre","trecho":"Agtech de analytics para o agro"}],"achados":{"industry_code":"A","industry_name":"Agronegocio","business_model":"B2B"},"confianca":0.75}
{"organizacao":{"linkedin_url":"https://br.linkedin.com/company/clinica-sao-lucas"},"tipo":"DIGITAL_PRESENCE","fontes":[{"tipo":"LINKEDIN","url":"https://www.linkedin.com/company/clinica-sao-lucas","trecho":"portal institucional ativo"}],"achados":{"website_url":"https://saolucas.example"},"confianca":0.65}
JSONL
}

escrever_contatos() { # <arquivo> — o contato comercial das empresas da cadeia
  cat > "$1" <<'JSONL'
{"organizacao":{"cnpj":"11.222.333/0001-81"},"contato":{"full_name":"Camila Souza","email":"camila.souza@valeforte.com.br","job_title":"Diretora de Operacoes","department":"Operacoes","seniority":"Diretoria","decision_role":"Decision Maker","preferred_channel":"EMAIL","legal_basis":"LEGITIMATE_INTEREST"},"fontes":[{"tipo":"LINKEDIN","url":"https://www.linkedin.com/in/camila-souza","trecho":"Diretora de Operacoes na Vale Forte"}],"confianca":0.8}
{"organizacao":{"domain":"agrosmart-analytics.com.br"},"contato":{"full_name":"Joao Bertoldo","email":"joao.bertoldo@agrosmart-analytics.com.br","job_title":"Head Comercial","department":"Comercial","seniority":"Diretoria","decision_role":"Decision Maker","legal_basis":"PUBLIC_DATA"},"fontes":[{"tipo":"DADOS_PUBLICOS","url":"https://empresas.example/agrosmart/qsa","trecho":"Joao Bertoldo - Administrador"}],"confianca":0.7}
{"organizacao":{"cnpj":"11.222.333/0001-81","domain":"agrosmart-analytics.com.br"},"contato":{"full_name":"Conflito Identidade","email":"conflito@example.com","legal_basis":"CONSENT"},"fontes":[{"tipo":"WEB","trecho":"dois identificadores fortes de empresas diferentes"}],"confianca":0.5}
{"organizacao":{"domain":"inexistente.example"},"contato":{"full_name":"Alguem Silva","email":"alguem@inexistente.example","legal_basis":"CONSENT"},"fontes":[{"tipo":"WEB","trecho":"empresa que nao existe na base"}],"confianca":0.5}
JSONL
}

gerar_observacoes() { # <r-research.json> <arquivo> — o SINAL ancorado na PESQUISA da mesma empresa
  python3 - "$1" "$2" <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
runs = [x.get("research_run_id") for x in rel.get("resultados", [])]
if len(runs) < 4 or not all(runs[:3]):
    print("FALHOU relatorio de pesquisa sem os 4 research_run_id"); sys.exit(3)
linhas = [
    {"organizacao": {"cnpj": "11.222.333/0001-81"}, "tipo": "HIRING",
     "titulo": "40 vagas de logistica abertas em 30 dias",
     "descricao": "A empresa abriu 40 vagas de operador logistico no ultimo mes.",
     "fontes": [{"tipo": "LINKEDIN", "url": "https://www.linkedin.com/company/vale-forte/jobs",
                 "trecho": "40 vagas abertas no ultimo mes"}],
     "data_do_evento": "2026-09-28", "confianca": 0.8, "research_run_id": runs[0]},
    {"organizacao": {"domain": "agrosmart-analytics.com.br"}, "tipo": "ERP_CHANGE",
     "titulo": "Selecao de novo ERP anunciada",
     "fontes": [{"tipo": "WEB", "url": "https://agrosmart-analytics.com.br/noticias",
                 "trecho": "Selecao de novo ERP para 2027"}],
     "data_do_evento": "2026-09-15", "confianca": 0.65, "research_run_id": runs[2]},
    {"organizacao": {"linkedin_url": "https://br.linkedin.com/company/clinica-sao-lucas"},
     "tipo": "DIGITAL_TRANSFORMATION",
     "titulo": "Programa de transformacao digital com foco em atendimento",
     "fontes": [{"tipo": "EVENTOS", "trecho": "Palestra no evento de saude digital de Curitiba"}],
     "data_do_evento": "2026-09-30T14:00:00Z", "confianca": 0.7},
    {"organizacao": {"cnpj": "11.222.333/0001-81"}, "tipo": "PROCESS_COMPLEXITY",
     "titulo": "Empresa descreve processo de faturamento manual em 4 sistemas",
     "fontes": [{"tipo": "WEB", "url": "https://valeforte.com.br/carreiras",
                 "trecho": "Vaga exige conciliacao manual entre 4 sistemas"}],
     "confianca": 0.55, "research_run_id": "99999999-0000-4000-8000-0000000000ff"},
]
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    for linha in linhas:
        fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
PY
}

gerar_hipoteses() { # <r-research.json> <r-signal.json> <arquivo>
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
pesquisa = json.load(open(sys.argv[1], encoding="utf-8"))
sinal = json.load(open(sys.argv[2], encoding="utf-8"))
runs = [x.get("research_run_id") for x in pesquisa.get("resultados", [])]
sinais = [x.get("signal_id") for x in sinal.get("resultados", [])]
if len(runs) < 4 or len(sinais) < 4 or not all(runs[:3]) or not all(sinais):
    print("FALHOU relatorios sem os ids necessarios (research_run_id/signal_id)"); sys.exit(3)
linhas = [
    {"organizacao": {"cnpj": "11.222.333/0001-81"},
     "dor": "A conciliacao de recebiveis e manual em quatro sistemas e trava o fechamento mensal",
     "categoria": "FINANCEIRO",
     "evidencias": [{"tipo": "SINAL", "id": sinais[0]}, {"tipo": "PESQUISA", "id": runs[0]}],
     "resumo_da_evidencia": "Vaga exige conciliacao manual entre quatro sistemas; o perfil da empresa registra o processo",
     "confianca": 0.8, "research_run_id": runs[0]},
    {"organizacao": {"domain": "agrosmart-analytics.com.br"},
     "dor": "A troca de ERP sem plano de migracao trava o comercial no periodo de transicao",
     "categoria": "COMERCIAL",
     "evidencias": [{"tipo": "SINAL", "id": sinais[1]}, {"tipo": "PESQUISA", "id": runs[2]}],
     "resumo_da_evidencia": "Sinal de selecao de novo ERP ancora a hipotese no mesmo fato que a pesquisa descreve",
     "confianca": 0.7, "research_run_id": runs[2]},
    {"organizacao": {"linkedin_url": "https://br.linkedin.com/company/clinica-sao-lucas"},
     "dor": "O atendimento recebe grande volume de WhatsApp e responde as mesmas perguntas varias vezes ao dia",
     "categoria": "ATENDIMENTO",
     "evidencias": [{"tipo": "SINAL", "id": sinais[2]}],
     "resumo_da_evidencia": "O programa de transformacao digital com foco em atendimento e o fato de origem",
     "confianca": 0.65},
    {"organizacao": {"cnpj": "11.222.333/0001-81"},
     "dor": "As transferencias entre areas e as aprovacoes acontecem por planilha e e-mail, sem trilha unica",
     "categoria": "OPERACOES",
     "evidencias": [{"tipo": "SINAL", "id": sinais[1]}],
     "resumo_da_evidencia": "Evidencia DECLARADA de OUTRA empresa (sinal do AgroSmart): tem de ser recusada",
     "confianca": 0.55},
]
with open(sys.argv[3], "w", encoding="utf-8") as fh:
    for linha in linhas:
        fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
PY
}

# ---------------------------------------------------------------------------------------
# Execucao dos agentes e leitura dos relatorios
# ---------------------------------------------------------------------------------------
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

rodar_agente() { # <agente.py> <relatorio> <args...>
  local agente="$1" relatorio="$2"; shift 2
  # `</dev/null`: o agente nunca le stdin e a porta psql usa `input=` — nada aqui pode
  # consumir o stdin de um laco que chame rodar_agente.
  python3 "$agente" --raiz "$RAIZ" --relatorio "$relatorio" "$@" </dev/null
}

veredito_do_relatorio() { # <relatorio> <veredito>
  python3 - "$1" "$2" <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
print(rel.get("por_veredito", {}).get(sys.argv[2], 0))
PY
}

agente_por_nome() { # <scout|research|signal|pain|contact> -> caminho do codigo sob teste
  # Estes cinco AGENTE_* sao os globais que a prova de dente troca pela COPIA mutada.
  case "$1" in
    scout)    echo "$AGENTE_SCOUT" ;;
    research) echo "$AGENTE_RESEARCH" ;;
    signal)   echo "$AGENTE_SIGNAL" ;;
    pain)     echo "$AGENTE_PAIN" ;;
    contact)  echo "$AGENTE_CONTACT" ;;
    *) echo "FALHOU agente desconhecido: $1" >&2; return 3 ;;
  esac
}

campo_do_relatorio() { # <relatorio> <campo> [indice 1-based]
  python3 - "$1" "$2" "${3:-}" <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
valores = [("" if x.get(sys.argv[2]) is None else str(x.get(sys.argv[2])))
           for x in rel.get("resultados", [])]
if len(sys.argv) > 3 and sys.argv[3]:
    i = int(sys.argv[3]) - 1
    print(valores[i] if 0 <= i < len(valores) else "")
else:
    print("\n".join(valores))
PY
}

# ---------------------------------------------------------------------------------------
# O aceite propriamente dito (uma vez por codigo sob teste)
# ---------------------------------------------------------------------------------------
SL="sales_intelligence"
TABELAS_NEGOCIO="organizations research_runs signals pain_hypotheses contacts"
foto_do_negocio() { # assinatura das tabelas de NEGOCIO (5), para comparar antes/depois
  # `||` e' CONCATENACAO em SQL — `|` sozinho e' OR bit a bit e a foto mentiria (7 em vez de
  # "3|4|4|3|2"): o item passaria comparando um numero sem sentido com ele mesmo.
  local expr="" tabela
  for tabela in $TABELAS_NEGOCIO; do
    expr="${expr}${expr:+ || '|' || }(SELECT count(*) FROM ${SL}.${tabela})"
  done
  contagem "SELECT $expr;"
}
fila_humana_pendente() { contagem "SELECT count(*) FROM ${SL}.human_approvals WHERE status='PENDING';"; }
ZERO_TABELAS_DE_SAIDA="SELECT (SELECT count(*) FROM ${SL}.scores) + (SELECT count(*) FROM ${SL}.outbox_events) + (SELECT count(*) FROM ${SL}.interactions) + (SELECT count(*) FROM ${SL}.recommendations);"

rodar_aceite() { # <rotulo>
  local rotulo="$1"
  ITENS_OK=0; ITENS_FALHOU=0
  echo "== E2E SALES INTELLIGENCE (TRE-W4-E06-T01) — rotulo: $rotulo"
  echo "   scout=$AGENTE_SCOUT"
  echo "   research=$AGENTE_RESEARCH"
  echo "   signal=$AGENTE_SIGNAL"
  echo "   pain=$AGENTE_PAIN"
  echo "   contact=$AGENTE_CONTACT"
  # Impressao digital do codigo sob teste DESTA rodada (no dente, os caminhos ja apontam para a
  # copia mutada): sem isso a evidencia nao diz em que fonte o numero foi medido.
  echo "-- codigo sob teste (sha256 dos cinco fontes usados): $(sha256sum "$AGENTE_SCOUT" \
       "$AGENTE_RESEARCH" "$AGENTE_SIGNAL" "$AGENTE_PAIN" "$AGENTE_CONTACT" 2>/dev/null \
       | cut -d' ' -f1 | sha256sum | cut -d' ' -f1)"
  preparar_banco
  escrever_candidatas "$TRABALHO/candidatas.jsonl"
  escrever_pesquisas "$TRABALHO/pesquisas.jsonl"
  escrever_contatos "$TRABALHO/contatos.jsonl"

  # ===== A. SCOUT: a empresa nasce da descoberta (nao de semeacao manual) =================
  rodar_agente "$AGENTE_SCOUT" "$TRABALHO/r-scout.json" --ambiente dev \
    --correlation-id "$CORR_SCOUT" --fonte "$TRABALHO/candidatas.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r-scout.out" 2>&1
  item "scout-rodada1-exit-0" "0" "$?"
  item "scout-rodada1-criadas" "3" "$(veredito_do_relatorio "$TRABALHO/r-scout.json" CRIADA)"
  item "scout-rodada1-organizacoes" "3" "$(contagem "SELECT count(*) FROM ${SL}.organizations;")"
  item "scout-rodada1-status-inicial" "3" "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE status='DISCOVERED';")"
  item "scout-rodada1-auditoria-por-pedido" "3" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE agent_name='scout' AND correlation_id='$CORR_SCOUT';")"
  item "scout-rodada1-trilha" "3" "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='INSERT' AND entity_type='organization' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_SCOUT';")"
  item "scout-rodada1-sem-outras-tabelas" "0" "$(contagem "$ZERO_TABELAS_DE_SAIDA")"
  local org_vf org_agro org_sl
  org_vf="$(campo_do_relatorio "$TRABALHO/r-scout.json" organization_id 1)"
  org_agro="$(campo_do_relatorio "$TRABALHO/r-scout.json" organization_id 2)"
  org_sl="$(campo_do_relatorio "$TRABALHO/r-scout.json" organization_id 3)"
  item "cadeia-empresa-da-descoberta-tem-uuid-v4" "3" \
    "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\$';")"
  item "cadeia-empresa-da-descoberta-e-a-do-relatorio" "3" \
    "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE id IN ('$org_vf','$org_agro','$org_sl');")"

  # ===== B. RESEARCH: pesquisa a empresa QUE JA EXISTE e enriquece sem sobrescrever ========
  rodar_agente "$AGENTE_RESEARCH" "$TRABALHO/r-research.json" --ambiente dev \
    --correlation-id "$CORR_RESEARCH" --fonte "$TRABALHO/pesquisas.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r-research.out" 2>&1
  item "pesquisa-rodada1-exit-0" "0" "$?"
  item "pesquisa-rodada1-veredito" "4" "$(veredito_do_relatorio "$TRABALHO/r-research.json" PESQUISADA)"
  item "pesquisa-rodada1-research-runs" "4" "$(contagem "SELECT count(*) FROM ${SL}.research_runs;")"
  item "pesquisa-rodada1-nao-cria-organizacao" "3" "$(contagem "SELECT count(*) FROM ${SL}.organizations;")"
  item "pesquisa-rodada1-run-aponta-a-empresa-do-scout" "4" \
    "$(contagem "SELECT count(*) FROM ${SL}.research_runs WHERE organization_id IN ('$org_vf','$org_agro','$org_sl');")"
  # O Scout escreveu industria/porte/cidade; a pesquisa traz OUTROS valores para os mesmos
  # campos — preservar aqui e' a prova de que a cadeia nao se atropela.
  item "pesquisa-rodada1-nao-sobrescreve-o-scout" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE id='$org_vf' AND industry_name='Metalurgia' AND employee_count=420 AND city='Sao Bernardo do Campo';")"
  item "pesquisa-rodada1-enriquece-coluna-vazia" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE id='$org_vf' AND website_url='https://valeforte.com.br' AND unit_count=2 AND revenue_estimate=18500000;")"
  # O contrato do Research recusa DERIVADO declarado pela fonte (employee_band vem do porte):
  # o achado e' descartado com motivo e a coluna continua com o derivado do contrato.
  item "pesquisa-rodada1-derivado-descartado-com-motivo" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_RESEARCH' AND output->'descartados' @> '[{\"campo\":\"employee_band\",\"motivo\":\"DERIVADO_NAO_ACEITO\"}]';")"
  item "pesquisa-rodada1-trilha" "4" \
    "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='RESEARCH' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_RESEARCH';")"
  local run_vf run_agro
  run_vf="$(campo_do_relatorio "$TRABALHO/r-research.json" research_run_id 1)"
  run_agro="$(campo_do_relatorio "$TRABALHO/r-research.json" research_run_id 3)"
  item "cadeia-pesquisa-devolve-o-id-da-rodada" "2" \
    "$(contagem "SELECT count(*) FROM ${SL}.research_runs WHERE id IN ('$run_vf','$run_agro');")"

  # ===== C. SIGNAL: o fato datado ANCORADO na pesquisa daquela empresa ====================
  gerar_observacoes "$TRABALHO/r-research.json" "$TRABALHO/observacoes.jsonl" > "$TRABALHO/obs.out" 2>&1 \
    || { echo "FALHOU geracao das observacoes a partir da rodada de pesquisa"; cat "$TRABALHO/obs.out"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); }
  rodar_agente "$AGENTE_SIGNAL" "$TRABALHO/r-signal.json" --ambiente dev \
    --correlation-id "$CORR_SIGNAL" --fonte "$TRABALHO/observacoes.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r-signal.out" 2>&1
  item "sinal-rodada1-exit-0" "0" "$?"
  item "sinal-rodada1-veredito" "4" "$(veredito_do_relatorio "$TRABALHO/r-signal.json" DETECTADO)"
  item "sinal-rodada1-signals" "4" "$(contagem "SELECT count(*) FROM ${SL}.signals;")"
  item "sinal-rodada1-nao-cria-organizacao" "3" "$(contagem "SELECT count(*) FROM ${SL}.organizations;")"
  item "sinal-rodada1-sem-score" "0" \
    "$(contagem "SELECT count(*) FROM ${SL}.signals WHERE relevance_score IS NOT NULL OR buying_signal_points IS NOT NULL OR expires_at IS NOT NULL;")"
  item "cadeia-sinal-vincula-a-pesquisa-da-mesma-empresa" "2" \
    "$(contagem "SELECT count(*) FROM ${SL}.signals s WHERE s.research_run_id IS NOT NULL AND EXISTS (SELECT 1 FROM ${SL}.research_runs r WHERE r.id = s.research_run_id AND r.organization_id = s.organization_id);")"
  item "cadeia-sinal-run-inexistente-descartado" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_SIGNAL' AND output->'descartados' @> '[{\"campo\":\"research_run_id\",\"motivo\":\"RESEARCH_RUN_NAO_ENCONTRADO\"}]';")"
  item "cadeia-sinal-run-invalido-nao-anexado" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.signals WHERE organization_id='$org_vf' AND signal_type='PROCESS_COMPLEXITY' AND research_run_id IS NULL;")"
  local sid_vf sid_agro sid_sl sid_vf_b
  sid_vf="$(campo_do_relatorio "$TRABALHO/r-signal.json" signal_id 1)"
  sid_agro="$(campo_do_relatorio "$TRABALHO/r-signal.json" signal_id 2)"
  sid_sl="$(campo_do_relatorio "$TRABALHO/r-signal.json" signal_id 3)"
  sid_vf_b="$(campo_do_relatorio "$TRABALHO/r-signal.json" signal_id 4)"
  item "cadeia-sinal-do-relatorio-existe-no-banco" "4" \
    "$(contagem "SELECT count(*) FROM ${SL}.signals WHERE id IN ('$sid_vf','$sid_agro','$sid_sl','$sid_vf_b');")"

  # ===== D. PAIN HYPOTHESIS: lastro tem de existir E ser da MESMA empresa =================
  gerar_hipoteses "$TRABALHO/r-research.json" "$TRABALHO/r-signal.json" "$TRABALHO/hipoteses.jsonl" > "$TRABALHO/hip.out" 2>&1 \
    || { echo "FALHOU geracao das hipoteses a partir dos sinais"; cat "$TRABALHO/hip.out"; ITENS_FALHOU=$((ITENS_FALHOU + 1)); }
  rodar_agente "$AGENTE_PAIN" "$TRABALHO/r-pain.json" --ambiente dev \
    --correlation-id "$CORR_PAIN" --fonte "$TRABALHO/hipoteses.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r-pain.out" 2>&1
  item "hipotese-rodada1-exit-0" "0" "$?"
  item "hipotese-rodada1-veredito" "REGISTRADA=3 RECUSADA=1" \
    "REGISTRADA=$(veredito_do_relatorio "$TRABALHO/r-pain.json" REGISTRADA) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r-pain.json" RECUSADA)"
  item "hipotese-rodada1-total" "3" "$(contagem "SELECT count(*) FROM ${SL}.pain_hypotheses;")"
  item "cadeia-hipotese-lastro-de-sinal-verdadeiro" "3" \
    "$(contagem "SELECT count(*) FROM ${SL}.pain_hypotheses h WHERE h.evidence->'evidencia_primaria'->>'tipo'='SINAL' AND EXISTS (SELECT 1 FROM ${SL}.signals s WHERE s.id = (h.evidence->'evidencia_primaria'->>'id')::uuid AND s.organization_id = h.organization_id);")"
  item "cadeia-hipotese-lastro-de-pesquisa" "2" \
    "$(contagem "SELECT count(*) FROM ${SL}.pain_hypotheses h WHERE h.research_run_id IS NOT NULL AND EXISTS (SELECT 1 FROM ${SL}.research_runs r WHERE r.id = h.research_run_id AND r.organization_id = h.organization_id);")"
  item "cadeia-hipotese-evidencia-de-outra-empresa-recusada" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE correlation_id='$CORR_PAIN' AND output->'descartados' @> '[{\"campo\":\"evidencias\",\"motivo\":\"EVIDENCIA_DE_OUTRA_ORGANIZACAO\"}]';")"
  item "hipotese-rodada1-sem-impacto-e-sem-validacao" "3" \
    "$(contagem "SELECT count(*) FROM ${SL}.pain_hypotheses WHERE status='HYPOTHESIS' AND business_impact_score IS NULL AND estimated_impact_description IS NULL AND validated_at IS NULL;")"
  item "hipotese-rodada1-trilha" "3" \
    "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='PAIN_HYPOTHESIS' AND status='SUCCESS' AND request_payload->>'correlation_id'='$CORR_PAIN';")"

  # ===== E. CONTACT RESEARCH: o contato comercial da empresa da cadeia ====================
  rodar_agente "$AGENTE_CONTACT" "$TRABALHO/r-contact.json" --ambiente dev \
    --correlation-id "$CORR_CONTACT" --fonte "$TRABALHO/contatos.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r-contact.out" 2>&1
  item "contato-rodada1-exit-0" "0" "$?"
  item "contato-rodada1-veredito" "IDENTIFICADO=2 REVISAO_IDENTIDADE=1 RECUSADA=1" \
    "IDENTIFICADO=$(veredito_do_relatorio "$TRABALHO/r-contact.json" IDENTIFICADO) REVISAO_IDENTIDADE=$(veredito_do_relatorio "$TRABALHO/r-contact.json" REVISAO_IDENTIDADE) RECUSADA=$(veredito_do_relatorio "$TRABALHO/r-contact.json" RECUSADA)"
  item "contato-rodada1-contatos" "2" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  item "cadeia-contato-na-empresa-do-scout" "2" \
    "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE organization_id IN ('$org_vf','$org_agro','$org_sl');")"
  item "cadeia-contato-casa-empresa-e-pesquisa" "2" \
    "$(contagem "SELECT count(*) c FROM ${SL}.contacts WHERE EXISTS (SELECT 1 FROM ${SL}.signals s WHERE s.organization_id = contacts.organization_id) AND EXISTS (SELECT 1 FROM ${SL}.research_runs r WHERE r.organization_id = contacts.organization_id);")"
  item "contato-rodada1-fila-humana-pendente" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.human_approvals WHERE action_type='CONTACT_IDENTITY_REVIEW' AND status='PENDING';")"
  item "contato-rodada1-fila-sem-contato-escrito" "0" \
    "$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE email='conflito@example.com';")"

  # ===== F. CADEIA COMPLETA: o estado final de UMA empresa passando pelos cinco agentes ===
  item "cadeia-estado-final" "3|4|4|3|2" \
    "$(contagem "SELECT (SELECT count(*) FROM ${SL}.organizations) || '|' || (SELECT count(*) FROM ${SL}.research_runs) || '|' || (SELECT count(*) FROM ${SL}.signals) || '|' || (SELECT count(*) FROM ${SL}.pain_hypotheses) || '|' || (SELECT count(*) FROM ${SL}.contacts);")"
  item "cadeia-vale-forte-atraves-dos-cinco-agentes" "1" \
    "$(contagem "SELECT CASE WHEN (SELECT count(*) FROM ${SL}.organizations WHERE id='$org_vf') = 1 AND (SELECT count(*) FROM ${SL}.research_runs WHERE organization_id='$org_vf') = 2 AND (SELECT count(*) FROM ${SL}.signals WHERE organization_id='$org_vf') = 2 AND (SELECT count(*) FROM ${SL}.pain_hypotheses WHERE organization_id='$org_vf') = 1 AND (SELECT count(*) FROM ${SL}.contacts WHERE organization_id='$org_vf') = 1 THEN 1 ELSE 0 END;")"
  item "cadeia-zero-em-saida-e-score" "0" "$(contagem "$ZERO_TABELAS_DE_SAIDA")"
  item "cadeia-nenhuma-chamada-de-llm" "0" \
    "$(contagem "SELECT count(*) FROM ${SL}.agent_runs WHERE model IS NOT NULL OR tokens_input IS NOT NULL OR estimated_cost IS NOT NULL;")"
  item "cadeia-trilha-dos-cinco-agentes" "5" \
    "$(contagem "SELECT count(DISTINCT operation) FROM ${SL}.sync_events WHERE operation IN ('INSERT','RESEARCH','SIGNAL','PAIN_HYPOTHESIS','CONTACT_RESEARCH') AND status='SUCCESS';")"

  # ===== G. REPLAY: repetir as cinco rodadas com as MESMAS fontes = zero duplicata ========
  local antes depois fila_antes_replay
  antes="$(foto_do_negocio)"
  fila_antes_replay="$(fila_humana_pendente)"
  rodar_agente "$AGENTE_SCOUT" "$TRABALHO/r2-scout.json" --ambiente dev \
    --correlation-id "$CORR_SCOUT" --fonte "$TRABALHO/candidatas.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r2-scout.out" 2>&1
  item "replay-scout-exit-0" "0" "$?"
  item "replay-scout-nao-duplica" "JA_EXISTE=3" \
    "JA_EXISTE=$(veredito_do_relatorio "$TRABALHO/r2-scout.json" JA_EXISTE)"
  rodar_agente "$AGENTE_RESEARCH" "$TRABALHO/r2-research.json" --ambiente dev \
    --correlation-id "$CORR_RESEARCH" --fonte "$TRABALHO/pesquisas.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r2-research.out" 2>&1
  item "replay-pesquisa-exit-0" "0" "$?"
  item "replay-pesquisa-nao-duplica" "JA_PESQUISADO=4" \
    "JA_PESQUISADO=$(veredito_do_relatorio "$TRABALHO/r2-research.json" JA_PESQUISADO)"
  rodar_agente "$AGENTE_SIGNAL" "$TRABALHO/r2-signal.json" --ambiente dev \
    --correlation-id "$CORR_SIGNAL" --fonte "$TRABALHO/observacoes.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r2-signal.out" 2>&1
  item "replay-sinal-exit-0" "0" "$?"
  item "replay-sinal-nao-duplica" "JA_DETECTADO=4" \
    "JA_DETECTADO=$(veredito_do_relatorio "$TRABALHO/r2-signal.json" JA_DETECTADO)"
  rodar_agente "$AGENTE_PAIN" "$TRABALHO/r2-pain.json" --ambiente dev \
    --correlation-id "$CORR_PAIN" --fonte "$TRABALHO/hipoteses.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r2-pain.out" 2>&1
  item "replay-hipotese-exit-0" "0" "$?"
  item "replay-hipotese-nao-duplica" "JA_REGISTRADA=3" \
    "JA_REGISTRADA=$(veredito_do_relatorio "$TRABALHO/r2-pain.json" JA_REGISTRADA)"
  rodar_agente "$AGENTE_CONTACT" "$TRABALHO/r2-contact.json" --ambiente dev \
    --correlation-id "$CORR_CONTACT" --fonte "$TRABALHO/contatos.jsonl" \
    --prefixo "$PREFIXO" > "$TRABALHO/r2-contact.out" 2>&1
  item "replay-contato-exit-0" "0" "$?"
  item "replay-contato-nao-duplica" "JA_IDENTIFICADO=2" \
    "JA_IDENTIFICADO=$(veredito_do_relatorio "$TRABALHO/r2-contact.json" JA_IDENTIFICADO)"
  depois="$(foto_do_negocio)"
  item "replay-zero-duplicata-em-todas-as-tabelas" "$antes" "$depois"
  # A fila humana NAO e' tabela de negocio: a ambiguidade de identidade e' REPORTADA de novo a
  # cada rodada que a encontra (comportamento do agente, medido) e NADA de contato e' escrito —
  # por isso o item mede as duas coisas juntas em vez de fingir "zero duplicata" na fila.
  item "replay-re-reporta-a-ambiguidade-sem-escrever-contato" "$((fila_antes_replay + 1))|0" \
    "$(fila_humana_pendente)|$(contagem "SELECT count(*) FROM ${SL}.contacts WHERE email='conflito@example.com';")"

  # ===== H. GUARDAS: prod recusado nos cinco e --planejar sem porta ======================
  local antes_prod fila_antes_prod rc_prod=() rc_planejar=() par agente fonte
  antes_prod="$(foto_do_negocio)"
  fila_antes_prod="$(fila_humana_pendente)"
  for par in "scout=$TRABALHO/candidatas.jsonl" "research=$TRABALHO/pesquisas.jsonl" \
             "signal=$TRABALHO/observacoes.jsonl" "pain=$TRABALHO/hipoteses.jsonl" \
             "contact=$TRABALHO/contatos.jsonl"; do
    agente="${par%%=*}"; fonte="${par#*=}"
    python3 "$(agente_por_nome "$agente")" --raiz "$RAIZ" --ambiente prod --fonte "$fonte" \
      --prefixo "$PREFIXO" >/dev/null 2>&1 </dev/null
    rc_prod+=("$?")
  done
  item "prod-recusado-nos-cinco-agentes" "4|4|4|4|4" "$(IFS='|'; echo "${rc_prod[*]}")"
  for par in "scout=$TRABALHO/candidatas.jsonl" "research=$TRABALHO/pesquisas.jsonl" \
             "signal=$TRABALHO/observacoes.jsonl" "pain=$TRABALHO/hipoteses.jsonl" \
             "contact=$TRABALHO/contatos.jsonl"; do
    agente="${par%%=*}"; fonte="${par#*=}"
    python3 "$(agente_por_nome "$agente")" --raiz "$RAIZ" --planejar --fonte "$fonte" \
      --prefixo "docker exec -i container-que-nao-existe psql -U ninguem -d nada" \
      >/dev/null 2>&1 </dev/null
    rc_planejar+=("$?")
  done
  item "planejar-nao-conecta-nos-cinco-agentes" "0|0|0|0|0" "$(IFS='|'; echo "${rc_planejar[*]}")"
  item "prod-nao-escreveu" "$antes_prod|$fila_antes_prod" "$(foto_do_negocio)|$(fila_humana_pendente)"

  # ===== I. DESFAZER A CADEIA NA ORDEM INVERSA ===========================================
  local auditoria_antes fila_antes
  auditoria_antes="$(contagem "SELECT count(*) FROM ${SL}.agent_runs;")"
  fila_antes="$(contagem "SELECT count(*) FROM ${SL}.human_approvals WHERE status='PENDING';")"
  rodar_agente "$AGENTE_CONTACT" "$TRABALHO/d-contact.json" --desfazer "$CORR_CONTACT" \
    --ambiente dev --prefixo "$PREFIXO" > "$TRABALHO/d-contact.out" 2>&1
  item "desfazer-contato-dry-run-exit-0" "0" "$?"
  item "desfazer-contato-dry-run-nao-apaga" "2" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  rodar_agente "$AGENTE_CONTACT" "$TRABALHO/d-contact.json" --desfazer "$CORR_CONTACT" --confirmo \
    --ambiente dev --prefixo "$PREFIXO" > "$TRABALHO/d-contact-aplicado.out" 2>&1
  item "desfazer-contato-apaga-so-a-rodada" "0" "$(contagem "SELECT count(*) FROM ${SL}.contacts;")"
  rodar_agente "$AGENTE_PAIN" "$TRABALHO/d-pain.json" --desfazer "$CORR_PAIN" --confirmo \
    --ambiente dev --prefixo "$PREFIXO" > "$TRABALHO/d-pain.out" 2>&1
  item "desfazer-hipotese-apaga-so-a-rodada" "0" "$(contagem "SELECT count(*) FROM ${SL}.pain_hypotheses;")"
  rodar_agente "$AGENTE_SIGNAL" "$TRABALHO/d-signal.json" --desfazer "$CORR_SIGNAL" --confirmo \
    --ambiente dev --prefixo "$PREFIXO" > "$TRABALHO/d-signal.out" 2>&1
  item "desfazer-sinal-apaga-so-a-rodada" "0" "$(contagem "SELECT count(*) FROM ${SL}.signals;")"
  rodar_agente "$AGENTE_RESEARCH" "$TRABALHO/d-research.json" --desfazer "$CORR_RESEARCH" --confirmo \
    --ambiente dev --prefixo "$PREFIXO" > "$TRABALHO/d-research.out" 2>&1
  item "desfazer-pesquisa-apaga-so-a-rodada" "0" "$(contagem "SELECT count(*) FROM ${SL}.research_runs;")"
  item "desfazer-pesquisa-restaura-a-coluna-enriquecida" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE id='$org_vf' AND website_url IS NULL AND unit_count IS NULL AND revenue_estimate IS NULL;")"
  item "desfazer-pesquisa-preservou-o-que-o-scout-escreveu" "1" \
    "$(contagem "SELECT count(*) FROM ${SL}.organizations WHERE id='$org_vf' AND industry_name='Metalurgia' AND employee_count=420 AND status='DISCOVERED';")"
  rodar_agente "$AGENTE_SCOUT" "$TRABALHO/d-scout.json" --desfazer "$CORR_SCOUT" --confirmo \
    --ambiente dev --prefixo "$PREFIXO" > "$TRABALHO/d-scout.out" 2>&1
  item "desfazer-scout-apaga-so-a-rodada" "0|0" \
    "$(contagem "SELECT (SELECT count(*) FROM ${SL}.organizations) || '|' || (SELECT count(*) FROM ${SL}.sync_events WHERE operation IN ('RESEARCH','SIGNAL','PAIN_HYPOTHESIS','CONTACT_RESEARCH'));")"
  item "desfazer-preserva-a-auditoria" "$auditoria_antes" "$(contagem "SELECT count(*) FROM ${SL}.agent_runs;")"
  item "desfazer-preserva-a-fila-humana" "$fila_antes" \
    "$(contagem "SELECT count(*) FROM ${SL}.human_approvals WHERE status='PENDING';")"
  item "desfazer-registra-rollback-por-rodada" "5" \
    "$(contagem "SELECT count(*) FROM ${SL}.sync_events WHERE operation='ROLLBACK' AND status='SUCCESS';")"
  item "desfazer-devolve-o-banco-ao-estado-inicial" "0|0|0|0|0|$fila_antes" \
    "$(contagem "SELECT (SELECT count(*) FROM ${SL}.organizations) || '|' || (SELECT count(*) FROM ${SL}.research_runs) || '|' || (SELECT count(*) FROM ${SL}.signals) || '|' || (SELECT count(*) FROM ${SL}.pain_hypotheses) || '|' || (SELECT count(*) FROM ${SL}.contacts) || '|' || (SELECT count(*) FROM ${SL}.human_approvals WHERE status='PENDING');")"

  echo "-- $rotulo: $ITENS_OK OK / $ITENS_FALHOU FALHOU"
  [ "$ITENS_FALHOU" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
# Passo 0 — regressao das CINCO suites offline no MESMO commit (nao toca o banco)
# ---------------------------------------------------------------------------------------
regressao_das_suites() {
  # As suites offline dos cinco agentes reprovam contrato de agente quebrado (paridade de coluna,
  # vocabulario, guarda de escrita, idempotencia). Elas nao tocam o banco: rodam aqui para que o
  # veredito do E2E diga em que COMMIT a cadeia foi medida, e nao so' que "os agentes conversaram".
  local pares=("scout:verificar_agente_scout.py" "research:verificar_agente_research.py"
               "signal:verificar_agente_signal.py" "pain:verificar_agente_pain_hypothesis.py"
               "contact:verificar_agente_contact_research.py")
  local par agente script ok=0 falhou=0 ultima
  echo "-- passo 0: suites offline dos cinco agentes no mesmo commit (sem banco)"
  for par in "${pares[@]}"; do
    agente="${par%%:*}"; script="$RAIZ/scripts/agentes/${par#*:}"
    if [ ! -f "$script" ]; then
      echo "FALHOU suite ausente $script"; falhou=$((falhou + 1)); continue
    fi
    if python3 "$script" > "$TRABALHO/suite-$agente.out" 2>&1 </dev/null \
       && grep -qE '[0-9]+ itens, 0 falhas' "$TRABALHO/suite-$agente.out"; then
      ultima="$(grep '^RESULTADO:' "$TRABALHO/suite-$agente.out" | tail -1)"
      echo "OK     suites-offline-$agente ($ultima)"; ok=$((ok + 1))
    else
      echo "FALHOU suites-offline-$agente"
      grep -E '^RESULTADO:|^FALHOU' "$TRABALHO/suite-$agente.out" | tail -3
      falhou=$((falhou + 1))
    fi
  done
  echo "-- suites offline: $ok OK / $falhou FALHOU"
  [ "$falhou" -eq 0 ]
}

# ---------------------------------------------------------------------------------------
# Prova de dente: uma mutacao por agente, cada uma pelo ITEM ESPERADO
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

  # Mutacoes lidas numa LISTA antes do laco: o corpo chama `docker exec -i` (por psql_t/contagem),
  # que consome o stdin do laco — com here-string as iteracoes 2+ morriam.
  # Formato: nome|agente|alvo|substituto|itens-esperados (o dente exige o ITEM, nao so' "falhou").
  #
  # Por que a mutacao do E2E e' por VINCULO e nao "sobrescrever": o "nao sobrescreve" do Research tem
  # DUAS camadas (a escolha de coluna vazia e o `COALESCE(NULLIF(...))` exigido pela guarda). Trocar o
  # COALESCE por atribuicao direta num ponto so' faz a guarda RECUSAR a escrita (a rodada inteira vira
  # ERRO) — nao ha mutacao de um ponto que produza sobrescrita; a propriedade e' medida pelo item do
  # aceite que le os VALORES reais e pelo autoteste do proprio agente. Aqui a mutacao mira o que so' o
  # E2E mede: o vinculo entre o que um agente escreve e o que o proximo resolve.
  local linhas=() linha
  while IFS= read -r linha; do
    [ -n "$linha" ] && linhas+=("$linha")
  done <<'EOF'
scout-escreve-empresa-sem-identidade|scout|        valores[tipo] = valor|        valores[tipo] = None|pesquisa-rodada1-run-aponta-a-empresa-do-scout
pesquisa-run-sem-organizacao|research|        lit(organizacao_id),                          # organization_id|        "NULL",                                       # organization_id|pesquisa-rodada1-run-aponta-a-empresa-do-scout
sinal-anexa-run-inexistente|signal|                if research_run_id and not self.research_run_existe(research_run_id):|                if False:|cadeia-sinal-run-invalido-nao-anexado
hipotese-aceita-lastro-de-outra-empresa|pain|            if registro.get("organization_id") != organizacao_id:|            if False:|cadeia-hipotese-evidencia-de-outra-empresa-recusada
contato-sem-idempotencia|contact|        "ON CONFLICT (idempotency_key) DO NOTHING\n"|        "\n"|replay-contato-nao-duplica
EOF

  local total="${#linhas[@]}" detectadas=0 falhas=0
  local nome agente alvo substituto esperados destino guardado faltando esperado
  local guardado_scout="$AGENTE_SCOUT" guardado_research="$AGENTE_RESEARCH" guardado_signal="$AGENTE_SIGNAL"
  local guardado_pain="$AGENTE_PAIN" guardado_contact="$AGENTE_CONTACT"
  for linha in "${linhas[@]}"; do
    IFS='|' read -r nome agente alvo substituto esperados <<< "$linha"
    [ -z "$nome" ] && continue
    case "$agente" in
      scout)    origem="$guardado_scout" ;;
      research) origem="$guardado_research" ;;
      signal)   origem="$guardado_signal" ;;
      pain)     origem="$guardado_pain" ;;
      contact)  origem="$guardado_contact" ;;
      *) echo "FALHOU mutacao $nome com agente desconhecido ($agente)"; falhas=$((falhas + 1)); continue ;;
    esac
    destino="$TRABALHO/mut-$nome/$(basename "$origem")"
    mkdir -p "$(dirname "$destino")"
    if ! aplicar_mutacao "$origem" "$destino" "$alvo" "$substituto" | grep -q MUTACAO_APLICADA; then
      echo "FALHOU mutacao $nome NAO se aplicou (ancora mudou) — buraco de verificacao"
      falhas=$((falhas + 1)); continue
    fi
    echo
    echo "-- mutacao: $nome (agente $agente; tem de reprovar: $(echo "$esperados" | tr ',' ' '))"
    case "$agente" in
      scout)    AGENTE_SCOUT="$destino" ;;
      research) AGENTE_RESEARCH="$destino" ;;
      signal)   AGENTE_SIGNAL="$destino" ;;
      pain)     AGENTE_PAIN="$destino" ;;
      contact)  AGENTE_CONTACT="$destino" ;;
    esac
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
    AGENTE_SCOUT="$guardado_scout"; AGENTE_RESEARCH="$guardado_research"; AGENTE_SIGNAL="$guardado_signal"
    AGENTE_PAIN="$guardado_pain"; AGENTE_CONTACT="$guardado_contact"
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
  echo "== ACEITE E2E SALES INTELLIGENCE (TRE-W4-E06-T01) — container descartavel $CONTAINER ($IMAGEM)"
  subir_container
  echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  local ok=0 suites_ok=1
  regressao_das_suites || suites_ok=0
  echo
  if rodar_aceite "principal"; then ok=1; fi
  [ "$suites_ok" -eq 1 ] || ok=0
  if [ "$DENTE" -eq 1 ]; then
    if prova_de_dente; then :; else ok=0; fi
  fi
  echo
  if [ "$ok" -eq 1 ]; then
    echo "ACEITE_E2E_SALES_INTELLIGENCE_001_OK"
    return 0
  fi
  echo "ACEITE_E2E_SALES_INTELLIGENCE_001_FALHOU"
  return 1
}

CORR_SCOUT="eeeeeeee-0000-4000-8000-000000000001"
CORR_RESEARCH="eeeeeeee-0000-4000-8000-000000000002"
CORR_SIGNAL="eeeeeeee-0000-4000-8000-000000000003"
CORR_PAIN="eeeeeeee-0000-4000-8000-000000000004"
CORR_CONTACT="eeeeeeee-0000-4000-8000-000000000005"

principal
