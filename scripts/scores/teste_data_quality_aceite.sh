#!/usr/bin/env bash
# teste_data_quality_aceite.sh [--prova-de-dente] [--manter] [--codigo <data_quality.py>]
#
# ACEITE E2E do SCORE DATA QUALITY v1 (card TRE-W5-E04-T01) — PostgreSQL descartavel.
#
# Roda na VPS (o container do Hermes nao tem daemon Docker). NUNCA toca `pg-sales-dev`,
# `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: sobe um container proprio (`pg-dq-acc`), aplica a
# migration 0001, cria a massa DESTE card e, no fim, remove o container e o diretorio de trabalho.
# Se o container ja existir, ABORTA em vez de mexer no que nao e dele.
#
# O que este aceite mede (AC1..AC13 de `docs/architecture/score-data-quality-v1.md`):
#   1. o score mede a organizacao que JA existe (identidade forte e UUID) e RECUSA quem nao existe;
#   2. grava UMA linha por organizacao em `scores` (DATA_QUALITY/v1.0) e espelha o valor ATUAL em
#      `organizations.data_quality_score` — sem tocar NENHUMA outra coluna (nem `updated_at`);
#   3. os valores medidos sao EXATOS para tres perfis conferiveis na mao (100.00 / 77.00 / 0.00) e
#      o quarto confere com a medicao da referencia fixa;
#   4. replay NAO duplica (o veredito vira JA_EXISTE e a contagem de linhas nao muda);
#   5. dado novo gera MEDICAO NOVA (mesmo valor ou nao, o hash muda e nasce linha nova);
#   6. `--planejar` mede e NAO escreve; `--ambiente prod` e recusado sem escrever (exit 4);
#   7. organizacao soft-deleted fica fora da rodada;
#   8. desfazer e frio por padrao e, com `--confirmo`, apaga SO a rodada e devolve o espelho ao valor
#      anterior — preservando `agent_runs`;
#   9. veredito ..... ACEITE_DATA_QUALITY_001_OK / ACEITE_DATA_QUALITY_001_FALHOU
#
# --prova-de-dente: aplica mutacoes em COPIA do data_quality.py (idempotencia fora do SQL, espelho
#   gravado nulo, prod aceito, versao errada no INSERT, confiabilidade creditando sem pesquisa) e
#   exige que o aceite reprove O ITEM ESPERADO de cada uma — nao basta "o aceite falhou".
#
# Licoes ja pagas (mantidas de proposito): todo `docker exec -i` que nao le stdin leva `</dev/null`,
# senao ele CONSOME o stdin do laco de mutacoes e mata as iteracoes seguintes; as mutacoes sao lidas
# numa LISTA antes do laco; `pg_isready` mente no inicio, entao a espera e por `SELECT 1` funcionando
# DUAS vezes; dentro do SQL, `|` e OU bit a bit — concatenacao e `||`.
#
# Variaveis: TRE_RAIZ (raiz do repo), TRE_FIXTURE_IMAGEM (default postgres:16),
#            TRE_DQ_CONTAINER (default pg-dq-acc), TRE_DQ_TRABALHO.
# Exit: 0 = ACEITE_DATA_QUALITY_001_OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_DQ_CONTAINER:-pg-dq-acc}"
TRABALHO="${TRE_DQ_TRABALHO:-/tmp/dq-aceite-trabalho}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
SENHA="dq-aceite-descartavel"
MODULO="$RAIZ/hermes/scores/data_quality/data_quality.py"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
REFERENCIA="2026-10-02"
DENTE=0
MANTER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --codigo) shift; MODULO="${1:?--codigo exige caminho}" ;;
    --codigo=*) MODULO="${1#--codigo=}" ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" ;;
    --*) echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <data_quality.py>]"; exit 2 ;;
    *)   echo "uso: $0 [--prova-de-dente] [--manter] [--codigo <data_quality.py>]"; exit 2 ;;
  esac
  shift
done

# Contagem por ARQUIVO, nao por variavel: `ciclo` roda dentro de `$( )` (subshell) e atribuicao
# feita la' morre com o subshell — medido: a primeira versao imprimia "0 itens, 0 falhas" e saia com
# exit 0 mesmo com item reprovado. Item que nao conta e' item que nao existe.
# O caminho fica no ESCOPO GLOBAL: `ciclo` roda em subshell e a atribuicao feita la' dentro nao
# volta — medido (o veredito lia um caminho vazio depois de 35 itens verdes). O subshell so TRUNCA.
ARQUIVO_ITENS="$TRABALHO/itens.txt"
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then
    echo "OK     $1 ($3)"; printf 'OK|%s\n' "$1" >> "$ARQUIVO_ITENS"
  else
    echo "FALHOU $1 (esperado=$2 obtido=$3)"; printf 'FALHOU|%s\n' "$1" >> "$ARQUIVO_ITENS"
  fi
}
contar_itens() { # <OK|FALHOU> — 0 quando o arquivo ainda nao existe
  [ -f "$ARQUIVO_ITENS" ] || { echo 0; return; }
  grep -c "^$1|" "$ARQUIVO_ITENS" || true
}

# ---------------------------------------------------------------------------------------
# Guardas e ciclo de vida do container descartavel
# ---------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
[ -f "$MIGRATION" ] || { echo "FALHOU migration ausente: $MIGRATION"; exit 2; }
[ -f "$MODULO" ] || { echo "FALHOU score ausente: $MODULO"; exit 2; }
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
PREFIXO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
psql_t() { "${PSQL[@]}" "$@" </dev/null; }
psql_stdin() { "${PSQL[@]}" -q -f -; }
consulta() { "${PSQL[@]}" -c "$1" </dev/null | tr -d '[:space:]'; }

subir_container() {
  docker run -d --name "$CONTAINER" \
    -e "POSTGRES_USER=$USUARIO" -e "POSTGRES_PASSWORD=$SENHA" -e "POSTGRES_DB=$BANCO" \
    "$IMAGEM" >/dev/null || { echo "FALHOU docker run"; exit 2; }
  local tentativa repetiu=0
  for tentativa in $(seq 1 60); do
    if "${PSQL[@]}" -c "SELECT 1" </dev/null >/dev/null 2>&1; then
      repetiu=$((repetiu + 1))
      [ "$repetiu" -ge 2 ] && return 0
    else
      repetiu=0
    fi
    sleep 1
  done
  echo "FALHOU o container nao ficou pronto"
  exit 2
}

# ---------------------------------------------------------------------------------------
# Massa do card (determinista) — tres perfis conferiveis na mao + um quarto com defeito de dado
# ---------------------------------------------------------------------------------------
ORG1="aaaaaaaa-0000-4000-8000-000000000001"
ORG2="aaaaaaaa-0000-4000-8000-000000000002"
ORG3="aaaaaaaa-0000-4000-8000-000000000003"
ORG4="aaaaaaaa-0000-4000-8000-000000000004"
ORG5="aaaaaaaa-0000-4000-8000-000000000005"
CNPJ1="11.222.333/0001-81"
CNPJ_INVALIDO="11.222.333/0001-99"

criar_massa() {
  psql_stdin <<SQL
TRUNCATE sales_intelligence.scores, sales_intelligence.agent_runs,
         sales_intelligence.research_runs, sales_intelligence.organizations CASCADE;
INSERT INTO sales_intelligence.organizations
  (id, legal_name, trade_name, domain, website_url, linkedin_url, cnpj, industry_name,
   industry_code, employee_count, employee_band, revenue_estimate, unit_count, city, state,
   business_model, status, source, data_quality_score, created_at)
VALUES
  -- ORG1: tudo preenchido, valido, com pesquisa concluida e fresco => 100.00
  ('$ORG1', 'ACME Distribuidora LTDA', 'ACME', 'acme.com.br', 'https://acme.com.br',
   'https://www.linkedin.com/company/acme', '$CNPJ1', 'Distribuidora', 'G46', 420, '300_499',
   1200000, 3, 'Sao Paulo', 'SP', 'B2B', 'DISCOVERED', 'WEB', NULL, '2026-09-20T10:00:00Z'),
  -- ORG2: parcial (dominio + site + cidade + unit_count=0), pesquisa com 1 fonte => 65.71
  ('$ORG2', 'Parcial Servicos ME', 'Parcial', 'parcial.com.br', 'https://parcial.com.br',
   NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 'Campinas', NULL, NULL, 'DISCOVERED', 'LINKEDIN',
   NULL, '2026-06-01T00:00:00Z'),
  -- ORG3: so a razao social, fonte fora do vocabulario, dado velho => 0.00
  ('$ORG3', 'Magra Comercio ME', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
   NULL, NULL, NULL, 'DISCOVERED', 'BLOG', NULL, '2025-01-01T00:00:00Z'),
  -- ORG4: cnpj invalido, banda incoerente, site de outro dominio, pesquisa sem fonte => 77.00
  ('$ORG4', 'Invalida Industria SA', NULL, 'invalida.com.br', 'https://outro-host.net',
   'https://www.linkedin.com/company/invalida', '$CNPJ_INVALIDO', 'Servicos', NULL, 420, '150_299',
   NULL, NULL, 'Sao Paulo', 'SP', 'B2B', 'DISCOVERED', 'TITAN', NULL, '2026-09-28T00:00:00Z'),
  -- ORG5: soft-deleted — NAO pode entrar na rodada
  ('$ORG5', 'Excluida Logistica ME', NULL, 'excluida.com.br', NULL, NULL, NULL, NULL, NULL, NULL,
   NULL, NULL, NULL, 'Osasco', NULL, NULL, 'DISCOVERED', 'WEB', NULL, '2026-09-20T10:00:00Z');
UPDATE sales_intelligence.organizations SET deleted_at = now() WHERE id = '$ORG5';
INSERT INTO sales_intelligence.research_runs
  (id, organization_id, agent_name, agent_version, workflow_name, status, source_count,
   completed_at, confidence)
VALUES
  ('bbbbbbbb-0000-4000-8000-000000000001', '$ORG1', 'research', '1.0.0', 'pesquisa-empresa',
   'COMPLETED', 3, '2026-09-25T10:00:00Z', 0.9),
  ('bbbbbbbb-0000-4000-8000-000000000002', '$ORG2', 'research', '1.0.0', 'pesquisa-empresa',
   'COMPLETED', 1, '2026-06-10T00:00:00Z', 0.7),
  ('bbbbbbbb-0000-4000-8000-000000000003', '$ORG3', 'research', '1.0.0', 'pesquisa-empresa',
   'PENDING', 0, NULL, NULL),
  ('bbbbbbbb-0000-4000-8000-000000000004', '$ORG4', 'research', '1.0.0', 'pesquisa-empresa',
   'COMPLETED', 0, '2026-09-29T00:00:00Z', 0.5);
SQL
}

fonte_forte() { # <arquivo> <cnpj|domain|linkedin>
  printf '{"organizacao": {%s}}\n' "$2" > "$TRABALHO/$1"
}

# Foto das colunas de NEGOCIO (nao inclui data_quality_score, que o score DEVE mudar).
foto_negocio() {
  consulta "SELECT md5(string_agg(id::text || '~' || COALESCE(legal_name,'') || '~' ||
           COALESCE(domain,'') || '~' || COALESCE(city,'') || '~' || COALESCE(status,'') || '~' ||
           COALESCE(updated_at::text,''), '|' ORDER BY id))
           FROM sales_intelligence.organizations;"
}
espelho() { consulta "SELECT COALESCE(data_quality_score::text,'NULL') FROM sales_intelligence.organizations WHERE id = '$1';"; }
valor_atual() {
  consulta "SELECT COALESCE(score_value::text,'NULL') FROM sales_intelligence.scores
            WHERE organization_id = '$1' AND score_type = 'DATA_QUALITY' AND score_version = 'v1.0'
            ORDER BY calculated_at DESC, id DESC LIMIT 1;"
}
linhas_scores() { consulta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type = 'DATA_QUALITY';"; }
auditoria() { consulta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name = 'data_quality';"; }

# Alvo efetivo do ciclo: a prova de dente troca pelo mutado e restaura no fim (nao se usa
# `VAR=x funcao` — em bash a atribuicao PERSISTE depois da funcao e contaminaria as rodadas).
MODULO_ATUAL="$MODULO"

rodar_score() { # <args...> — escreve o relatorio em $TRABALHO/relatorio.json
  python3 "$MODULO_ATUAL" --raiz "$RAIZ" --prefixo "$PREFIXO" \
    --relatorio "$TRABALHO/relatorio.json" "$@"
}

veredito_de() { # <veredito> — le o relatorio da ultima rodada
  python3 - "$1" "$TRABALHO/relatorio.json" <<'PY'
import json, sys
nome, caminho = sys.argv[1], sys.argv[2]
try:
    relatorio = json.load(open(caminho, encoding="utf-8"))
except Exception:
    print("-")
    sys.exit(0)
print(relatorio.get("por_veredito", {}).get(nome, 0) if "por_veredito" in relatorio
      else relatorio.get("resultados", [{}])[0].get(nome, 0))
PY
}

# ---------------------------------------------------------------------------------------
# CICLO — a medicao completa do aceite (usada uma vez e repetida por mutacao na prova de dente)
# ---------------------------------------------------------------------------------------
ciclo() {
  : > "$ARQUIVO_ITENS"
  local v

  criar_massa
  FOTO_ANTES=$(foto_negocio)

  # -- rodada A: identidade FORTE (cnpj) ------------------------------
  fonte_forte "fonte-org1.jsonl" "\"cnpj\": \"$CNPJ1\""
  RODADA_A=$(rodar_score --fonte "$TRABALHO/fonte-org1.jsonl" --ambiente dev --referencia "$REFERENCIA")
  # CNPJ gravado COM pontuacao na coluna (VARCHAR livre) e' o caso que o aceite E2E pegou quebrado
  item "fonte-forte-escreve-uma-medicao" "1" "$(veredito_de ESCRITO)"
  CID_A=$(echo "$RODADA_A" | python3 -c "import json,sys; print(json.loads(sys.stdin.read().splitlines()[0])['correlation_id'])")

  # -- rodada A2: identidade FORTE por dominio — e a MESMA empresa em outra forma de gravacao ----
  # (a coluna guarda o host; quem digita manda URL inteira: o pre-filtro aceita, o modulo decide)
  fonte_forte "fonte-org2.jsonl" "\"domain\": \"https://www.parcial.com.br/\""
  RODADA_A2=$(rodar_score --fonte "$TRABALHO/fonte-org2.jsonl" --ambiente dev --referencia "$REFERENCIA")
  item "fonte-por-dominio-em-forma-de-url" "1" "$(veredito_de ESCRITO)"
  item "linha-em-scores-apos-a-rodada-A" "2" "$(linhas_scores)"

  # -- rodada B: todas as organizacoes ATIVAS (a soft-deleted fica fora) ------------------------------
  RODADA_B=$(rodar_score --todas --ambiente dev --referencia "$REFERENCIA")
  item "todas-mede-o-restante-e-nao-re-mede-as-ja-medidas" "2" "$(veredito_de ESCRITO)"
  item "ja-existe-na-rodada-B" "2" "$(veredito_de JA_EXISTE)"
  item "linhas-em-scores-apos-a-rodada-B" "4" "$(linhas_scores)"
  item "soft-deleted-fora-da-rodada" "0" "$(consulta "SELECT count(*) FROM sales_intelligence.scores WHERE organization_id = '$ORG5';")"

  # -- valores EXATOS (tres conferiveis na mao) ------------------------------
  item "valor-org1-completa-e-100" "100.00" "$(valor_atual "$ORG1")"
  item "valor-org3-magra-e-0" "0.00" "$(valor_atual "$ORG3")"
  item "valor-org4-com-defeito-de-dado-e-77" "77.00" "$(valor_atual "$ORG4")"
  item "valor-org2-parcial-conferido" "65.71" "$(valor_atual "$ORG2")"
  item "espelho-bate-com-o-ultimo-score" "0" \
    "$(consulta "SELECT count(*) FROM sales_intelligence.organizations o WHERE o.data_quality_score IS DISTINCT FROM (SELECT s.score_value FROM sales_intelligence.scores s WHERE s.organization_id = o.id AND s.score_type = 'DATA_QUALITY' AND s.score_version = 'v1.0' ORDER BY s.calculated_at DESC, s.id DESC LIMIT 1);")"
  item "linhas-com-tipo-e-versao-do-score" "4" \
    "$(consulta "SELECT count(*) FROM sales_intelligence.scores WHERE score_type='DATA_QUALITY' AND score_version='v1.0';")"
  item "nenhuma-outra-coluna-escrita" "$FOTO_ANTES" "$(foto_negocio)"
  # Auditoria: A (1 empresa) + A2 (1) + B (4) = 6 linhas; com score_id (escritas) = 1 + 1 + 2 = 4.
  item "auditoria-uma-linha-por-organizacao-processada" "6" "$(auditoria)"
  item "auditoria-com-score-id" "4" \
    "$(consulta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='data_quality' AND output->>'score_id' IS NOT NULL;")"

  # -- rodada C: replay (banco igual) NAO duplica ------------------------------
  RODADA_C=$(rodar_score --todas --ambiente dev --referencia "$REFERENCIA")
  item "replay-nao-escreve" "0" "$(veredito_de ESCRITO)"
  item "replay-marca-ja-existe" "4" "$(veredito_de JA_EXISTE)"
  item "replay-nao-duplica-linhas" "4" "$(linhas_scores)"

  # -- rodada D: identificador forte INVALIDO e organizacao inexistente ------------------------------
  fonte_forte "fonte-invalida.jsonl" "\"cnpj\": \"$CNPJ_INVALIDO\""
  rodar_score --fonte "$TRABALHO/fonte-invalida.jsonl" --ambiente dev --referencia "$REFERENCIA" >/dev/null
  item "cnpj-invalido-e-recusado" "1" "$(veredito_de RECUSADA)"
  rodar_score --organizacao "cccccccc-0000-4000-8000-000000000009" --ambiente dev \
    --referencia "$REFERENCIA" >/dev/null
  item "organizacao-inexistente-e-recusada" "1" "$(veredito_de RECUSADA)"
  item "recusas-nao-escrevem-score" "4" "$(linhas_scores)"

  # -- rodada E: dado NOVO gera MEDICAO NOVA (mesmo com o resto igual) ------------------------------
  psql_t -c "UPDATE sales_intelligence.organizations SET industry_name = 'Logistica' WHERE id = '$ORG2';" >/dev/null
  RODADA_E=$(rodar_score --todas --ambiente dev --referencia "$REFERENCIA")
  CID_E=$(echo "$RODADA_E" | python3 -c "import json,sys; print(json.loads(sys.stdin.read().splitlines()[0])['correlation_id'])")
  item "dado-novo-vira-medicao-nova" "1" "$(veredito_de ESCRITO)"
  item "dado-novo-eleva-o-valor-da-org2" "70.21" "$(valor_atual "$ORG2")"
  item "historico-acumula-medicoes" "5" "$(linhas_scores)"

  # -- planejar: mede e NAO escreve ------------------------------
  rodar_score --todas --planejar --referencia "$REFERENCIA" >/dev/null
  item "planejar-nao-escreve" "5" "$(linhas_scores)"

  # -- prod: recusado sem escrever (exit 4) ------------------------------
  rodar_score --todas --ambiente prod --referencia "$REFERENCIA" >/dev/null 2>&1
  RC_PROD=$?
  item "prod-recusado-com-exit-4" "4" "$RC_PROD"
  item "prod-recusado-sem-escrita" "5" "$(linhas_scores)"

  # -- desfazer: frio por padrao; --confirmo apaga SO a rodada E e restaura o espelho ------------------------------
  local FRIO
  FRIO=$(rodar_score --desfazer "$CID_E" --ambiente dev)
  item "desfazer-frio-nao-apaga" "5" "$(linhas_scores)"
  item "desfazer-frio-e-dry-run" "true" "$(echo "$FRIO" | python3 -c "import json,sys; print(str(json.loads(sys.stdin.read().splitlines()[0]).get('dry_run')).lower())")"
  rodar_score --desfazer "$CID_E" --ambiente dev --confirmo >/dev/null
  item "desfazer-confirmo-apaga-a-rodada" "4" "$(linhas_scores)"
  item "desfazer-restaura-o-valor-anterior" "65.71" "$(espelho "$ORG2")"
  # Escritas (veredito ESCRITO) na auditoria: A(1) + A2(1) + B(2) + E(1) = 5 — a rodada desfeita continua
  # auditada (o DELETE chega em `scores`, nunca em `agent_runs`).
  item "desfazer-preserva-a-auditoria" "5" \
    "$(consulta "SELECT count(*) FROM sales_intelligence.agent_runs WHERE agent_name='data_quality' AND output->>'veredito'='ESCRITO';")"
  item "desfazer-nao-toca-as-outras-organizacoes" "100.00" "$(espelho "$ORG1")"
  # Auditoria no fim: A(1) + A2(1) + B(4) + C(4) + D1(1) + D2(1) + E(4) = 16 linhas — o desfazer
  # NAO apaga auditoria (por isso a contagem nao cai quando as linhas de `scores` caem).
  item "auditoria-completa-preservada" "16" "$(auditoria)"
}

# ---------------------------------------------------------------------------------------
# Prova de dente: muta uma COPIA do modulo e exige o ITEM esperado reprovado
# ---------------------------------------------------------------------------------------
# (nome, ancora antiga, ancora nova, item que DEVE reprovar)
MUTACOES=(
  "idempotencia-fora-do-sql|  WHERE NOT EXISTS (|  WHERE true OR NOT EXISTS (|replay-nao-duplica-linhas"
  "espelho-gravado-nulo|UPDATE {organizacoes} o SET data_quality_score = {valor}::numeric|UPDATE {organizacoes} o SET data_quality_score = NULL|espelho-bate-com-o-ultimo-score"
  "prod-passa-a-ser-aceito|    def conferir_ambiente(self) -> str:|    def conferir_ambiente(self) -> str:\n        return self.ambiente|prod-recusado-com-exit-4"
  "identidade-sem-normalizar-a-forma-guardada|if bruto and normalizadores[tipo](bruto) == valor:|if bruto and bruto == valor:|fonte-forte-escreve-uma-medicao"
  "versao-errada-no-insert|, {versao},|, 'v9.9',|linhas-com-tipo-e-versao-do-score"
  "confiabilidade-sem-pesquisa|        \"pesquisa_existente\": Decimal(1) if concluidas else Decimal(\"0\"),|        \"pesquisa_existente\": Decimal(1),|valor-org3-magra-e-0"
)

prova_de_dente() {
  local linha nome antiga nova item_esperado mutado falhas=0 esperado_reprovado
  local saida reprovados
  for linha in "${MUTACOES[@]}"; do
    IFS='|' read -r nome antiga nova item_esperado <<< "$linha"
    mutado="$TRABALHO/mutado-$nome.py"
    cp "$MODULO" "$mutado"
    if ! python3 - "$mutado" "$antiga" "$nova" <<'PY'
import sys
caminho, antiga, nova = sys.argv[1], sys.argv[2], sys.argv[3]
texto = open(caminho, encoding="utf-8").read()
if antiga not in texto:
    sys.exit(1)
open(caminho, "w", encoding="utf-8").write(texto.replace(antiga, nova, 1))
PY
    then
      echo "FALHOU dente $nome nao se aplica (ancora de texto mudou)"
      falhas=$((falhas + 1))
      continue
    fi
    MODULO_ATUAL="$mutado"
    saida=$(ciclo 2>&1)
    MODULO_ATUAL="$MODULO"
    reprovados=$(echo "$saida" | sed -n 's/^FALHOU \([a-z0-9-]*\) .*/\1/p' | sort -u | tr '\n' ' ')
    esperado_reprovado=$(echo "$reprovados" | grep -c "$item_esperado")
    if [ "$esperado_reprovado" -ge 1 ]; then
      echo "OK     dente $nome -> $item_esperado reprovou"
    else
      echo "FALHOU dente $nome NAO reprovou $item_esperado (reprovados: ${reprovados:-nenhum})"
      falhas=$((falhas + 1))
    fi
  done
  # a rodada de referencia (sem mutacao) tem de voltar verde depois dos dentes
  MODULO_ATUAL="$MODULO"
  saida=$(ciclo 2>&1)
  if echo "$saida" | grep -q "^FALHOU"; then
    echo "FALHOU a rodada de referencia reprovou depois dos dentes"
    falhas=$((falhas + 1))
  else
    echo "OK     rodada de referencia verde apos os dentes"
  fi
  return $falhas
}

# ---------------------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------------------
echo "== ACEITE DATA QUALITY (TRE-W5-E04-T01) — container descartavel $CONTAINER ($IMAGEM)"
subir_container
echo "== container pronto: $(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
psql_stdin < "$MIGRATION" >/dev/null || { echo "FALHOU aplicacao da migration"; exit 2; }
echo "== migration 0001 aplicada em schema limpo"

SAIDA=$(ciclo)
echo "$SAIDA"
DENTE_FALHAS=0
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente"
  prova_de_dente
  DENTE_FALHAS=$?
fi

echo "---"
ITENS_OK=$(contar_itens OK)
ITENS_FALHOU=$(contar_itens FALHOU)
if [ "$ITENS_OK" -eq 0 ]; then
  echo "FALHOU nenhum item medido na rodada de referencia — veredito vazio nao e' aprovacao"
  ITENS_FALHOU=$((ITENS_FALHOU + 1))
fi
if [ "$ITENS_FALHOU" -ne 0 ]; then
  echo "ITENS REPROVADOS:"
  grep "^FALHOU|" "$ARQUIVO_ITENS" | sed 's/^FALHOU|/  - /'
fi
if [ "$ITENS_FALHOU" -eq 0 ] && [ "$DENTE_FALHAS" -eq 0 ]; then
  echo "RESULTADO: ACEITE_DATA_QUALITY_001_OK ($ITENS_OK itens, 0 falhas, 0 dentes reprovados)"
  exit 0
fi
echo "RESULTADO: ACEITE_DATA_QUALITY_001_FALHOU ($ITENS_OK itens, $ITENS_FALHOU falhas, $DENTE_FALHAS dentes reprovados)"
exit 1
