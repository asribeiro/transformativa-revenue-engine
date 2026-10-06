#!/usr/bin/env bash
# =====================================================================================
# aplicar_migracoes.sh [ambiente] [--somente-checar]
#
# Runner de migracoes do Data Contract V1.0 (TRE-W1). Aplica `db/migrations/*.sql` EM
# ORDEM LEXICOGRAFICA no ambiente pedido, dentro do container do ambiente.
#
# Por que roda dentro do container (ADR-0008): o Hermes nao tem rota de rede ate o banco
# do TRE nem cliente `psql`; o trabalho de banco roda NA VPS, por SSH, via `docker exec`.
# O script NAO depende de stdin (a migration entra no container por `docker cp`) de
# proposito: quem orquestra por SSH nao pode ter o script comido pelo stdin de um
# `docker exec -i`.
#
# Idempotente e imutavel:
#   - versao ja aplicada com o MESMO sha256  -> PULADO (nao reaplica);
#   - versao ja aplicada com sha256 DIFERENTE -> FALHOU (migration aplicada nunca e editada);
#   - arquivo sem prefixo numerico de versao  -> FALHOU (fail-closed no nome).
#
# Rastro: `public.tre_schema_migrations` (versao, arquivo, sha256, aplicada_em, aplicada_por).
# Fica FORA do schema `sales_intelligence` de proposito: e controle operacional, nao
# tabela do contrato. Nenhum segredo passa por aqui — a conexao usa o socket local do
# container, sem senha em argumento, arquivo ou log.
#
# Log da aplicacao (defeito D02 do TRE-W1-E01-T01): o log do `psql` vive em um DIRETORIO
# TEMPORARIO POR EXECUCAO (`mktemp -d`), nunca em caminho fixo em `/tmp`. Caminho fixo
# (`/tmp/tre_migracao_<versao>.log`) quebra a execucao seguinte quando o arquivo ja existe
# e e de OUTRO dono: `/tmp` e sticky (`1777`) e o host tem `fs.protected_regular=2`, entao o
# `O_CREAT` do redirecionamento da EACCES — inclusive para root. O arquivo precisa existir
# ANTES do `psql`, por isso o caminho e uma VARIAVEL por execucao, e o diretorio e removido
# no fim (inclusive em falha).
#
# REGRA DE AMBIENTE (ADR-005 — nenhuma DDL nasce em producao):
#   dev      -> aplica
#   homolog  -> aplica
#   prod     -> RECUSA por padrao. So passa com TODAS as condicoes:
#               (i)   TRE_APROVACAO_HUMANA=<arquivo do registro de aprovacao> existente e nao-vazio;
#               (ii)  container de homolog existente E toda versao pendente ja registrada
#                     em homolog (sequencia dev -> homolog -> producao imposta pelo script,
#                     nao por combinado verbal).
#
# Uso:
#   scripts/db/aplicar_migracoes.sh dev
#   scripts/db/aplicar_migracoes.sh dev --somente-checar
#   scripts/db/aplicar_migracoes.sh homolog
#   TRE_APROVACAO_HUMANA=/caminho/registro.md scripts/db/aplicar_migracoes.sh prod
#
# Variaveis de ambiente — PRECEDENCIA: a variavel do operador VENCE o arquivo versionado
# `deploy/environments/<ambiente>.env` (o arquivo e default, nao override):
#   TRE_PG_SERVICO, TRE_PG_USER, TRE_PG_DB      — alvo no ambiente pedido
#   TRE_PG_SERVICO_HOMOLOG / TRE_PG_USER_HOMOLOG / TRE_PG_DB_HOMOLOG — alvo de homolog
#   TRE_APROVACAO_HUMANA, TRE_RAIZ
# =====================================================================================
set -uo pipefail

AMB="${1:-dev}"
MODO="${2:-aplicar}"
RAIZ="${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
DIR_MIGRACOES="$RAIZ/db/migrations"
ITENS=0
FALHAS=0
VERSAO_CONTROLE="public.tre_schema_migrations"
LOG_CONSULTA="$(mktemp)"   # stderr da ultima consulta (diagnostico honesto)

case "$MODO" in
  aplicar|--somente-checar) ;;
  *) echo "uso: $0 [dev|homolog|prod] [--somente-checar]"; exit 2 ;;
esac
case "$AMB" in
  dev|homolog|prod) ;;
  *) echo "FALHOU ambiente desconhecido: '$AMB' (esperado dev|homolog|prod)"; exit 2 ;;
esac

ok()  { ITENS=$((ITENS + 1)); echo "OK     $*"; }
ko()  { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }
pula(){ ITENS=$((ITENS + 1)); echo "PULADO $*"; }
morrer() { echo "$*"; echo "RESULTADO: MIGRACAO_FALHOU"; exit 1; }

consulta() {  # consulta <servico> <usuario> <banco> <sql>
  docker exec "$1" psql -U "$2" -d "$3" -tAc "$4" 2>"$LOG_CONSULTA"
  # stderr guardado (nao descartado): leitura que falha NAO pode virar "vazio = nao registrado".
  # Defeito medido em 06/10/2026 no 1o provisionamento de producao: usuario default 'tre' nao existia
  # no cluster de vendas -> psql falhava em silencio -> producao era recusada com a mensagem FALSA
  # "versao ainda nao registrada em homolog" (falhar estava certo; a causa dita, nao).
}

# ------------------------------------------------------------------ par do ambiente
# Par NAO-SECRETO versionado no repo e DEFAULT; variavel de ambiente do operador vence.
PRESERVADO_SERVICO="${TRE_PG_SERVICO:-}"
PRESERVADO_USUARIO="${TRE_PG_USER:-}"
PRESERVADO_BANCO="${TRE_PG_DB:-}"
ARQ_AMB="$RAIZ/deploy/environments/$AMB.env"
if [ -f "$ARQ_AMB" ]; then
  # shellcheck disable=SC1090
  . "$ARQ_AMB"
fi
SERVICO="${PRESERVADO_SERVICO:-${TRE_PG_SERVICO:-pg-$AMB}}"
USUARIO="${PRESERVADO_USUARIO:-${TRE_PG_USER:-tre}}"
BANCO="${PRESERVADO_BANCO:-${TRE_PG_DB:-sales_intelligence}}"
SERVICO_HOMOLOG="${TRE_PG_SERVICO_HOMOLOG:-pg-homolog}"
USUARIO_HOMOLOG="${TRE_PG_USER_HOMOLOG:-tre}"
BANCO_HOMOLOG="${TRE_PG_DB_HOMOLOG:-sales_intelligence}"

echo "=================================================================="
echo "-- MIGRACOES DO DATA CONTRACT — ambiente: $AMB ($MODO)"
echo "-- repo:     $RAIZ"
echo "-- alvo:     container '$SERVICO' | usuario '$USUARIO' | banco '$BANCO'"
echo "-- rastro:   $VERSAO_CONTROLE"
echo "=================================================================="

# ------------------------------------------------------------------ lista de migrations
if [ ! -d "$DIR_MIGRACOES" ]; then
  morrer "FALHOU diretorio de migrations ausente: $DIR_MIGRACOES"
fi
MAPA=()
while IFS= read -r f; do MAPA+=("$f"); done < <(find "$DIR_MIGRACOES" -maxdepth 1 -type f -name '*.sql' | sort)
if [ "${#MAPA[@]}" -eq 0 ]; then
  morrer "FALHOU nenhum arquivo .sql em $DIR_MIGRACOES"
fi
for f in "${MAPA[@]}"; do
  base="$(basename "$f")"
  case "$base" in
    [0-9][0-9][0-9][0-9]_*) ;;
    *) morrer "FALHOU migration sem prefixo de versao de 4 digitos: '$base'" ;;
  esac
done
NOMES="$(printf '%s ' "${MAPA[@]##*/}")"
ok "migrations encontradas: ${#MAPA[@]} arquivo(s) — $NOMES"

if ! command -v docker >/dev/null 2>&1; then
  morrer "FALHOU docker ausente: este runner roda na VPS do ambiente (ADR-0008)"
fi

# ------------------------------------------------------------------ guardrail de producao
# Vem ANTES de inspecionar/escrever o ambiente alvo: em producao o veredito e do guardrail,
# nao do estado da maquina.
if [ "$AMB" = "prod" ]; then
  if [ -z "${TRE_APROVACAO_HUMANA:-}" ] || [ ! -s "${TRE_APROVACAO_HUMANA:-}" ]; then
    morrer "FALHOU ADR-005: DDL nao nasce em producao. Exige TRE_APROVACAO_HUMANA=<arquivo do registro de aprovacao> (existente e nao-vazio). Nada foi tocado em producao."
  fi
  ok "aprovacao humana declarada: $TRE_APROVACAO_HUMANA ($(wc -c <"$TRE_APROVACAO_HUMANA") bytes)"
  if ! docker inspect "$SERVICO_HOMOLOG" >/dev/null 2>&1; then
    morrer "FALHOU sequencia dev -> homolog -> producao: container de homolog '$SERVICO_HOMOLOG' nao existe. Nada foi tocado em producao."
  fi
  ok "ambiente de homolog presente ('$SERVICO_HOMOLOG')"
  if [ "$(consulta "$SERVICO_HOMOLOG" "$USUARIO_HOMOLOG" "$BANCO_HOMOLOG" "SELECT 1")" != "1" ]; then
    morrer "FALHOU nao consegui LER o rastro de homolog no container '$SERVICO_HOMOLOG' (usuario '$USUARIO_HOMOLOG', banco '$BANCO_HOMOLOG'): $(head -1 "$LOG_CONSULTA" 2>/dev/null) — a sequencia dev -> homolog -> producao NAO foi avaliada. Ajuste TRE_PG_USER_HOMOLOG/TRE_PG_DB_HOMOLOG (o par de vendas usa usuario 'sales_ai'). Nada foi tocado em producao."
  fi
  ok "rastro de homolog legivel (usuario '$USUARIO_HOMOLOG', banco '$BANCO_HOMOLOG')"
  pendentes_homolog=0
  for arq in "${MAPA[@]}"; do
    base="$(basename "$arq")"
    versao="$(printf '%s' "$base" | cut -d_ -f1)"
    sha="$(sha256sum "$arq" | cut -d' ' -f1)"
    registro="$(consulta "$SERVICO_HOMOLOG" "$USUARIO_HOMOLOG" "$BANCO_HOMOLOG" \
      "SELECT sha256 FROM $VERSAO_CONTROLE WHERE versao='$versao'")"
    if [ "$registro" != "$sha" ]; then
      echo "       pendente em homolog: $base"
      pendentes_homolog=$((pendentes_homolog + 1))
    fi
  done
  if [ "$pendentes_homolog" -ne 0 ]; then
    morrer "FALHOU sequencia dev -> homolog -> producao: $pendentes_homolog versao(oes) ainda nao registradas em homolog. Nada foi tocado em producao."
  fi
  ok "todas as versoes ja registradas em homolog — promocao permitida"
fi

# ------------------------------------------------------------------ alvo existe?
if ! docker inspect "$SERVICO" >/dev/null 2>&1; then
  morrer "FALHOU container '$SERVICO' nao existe no host $(hostname) — ambiente '$AMB' nao esta provisionado"
fi

# Espera ROBUSTA: a imagem oficial sobe um servidor TEMPORARIO durante a inicializacao e o
# derruba em seguida; `pg_isready` responde OK nesse servidor temporario (aprendizado do
# TRE-W0-E01-T03). Regra: SELECT 1 tem de funcionar DUAS vezes, com intervalo.
esperar_postgres() {
  local servico="$1" usuario="$2" banco="$3" tentativas=0
  while [ "$tentativas" -lt 30 ]; do
    tentativas=$((tentativas + 1))
    if consulta "$servico" "$usuario" "$banco" 'SELECT 1' | grep -q '^1$'; then
      sleep 2
      if consulta "$servico" "$usuario" "$banco" 'SELECT 1' | grep -q '^1$'; then
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}
if esperar_postgres "$SERVICO" "$USUARIO" "$BANCO"; then
  ok "postgres responde em '$SERVICO' (servidor definitivo, confirmado duas vezes)"
else
  morrer "FALHOU postgres nao responde em '$SERVICO' — DDL abortada para nao escrever no vazio"
fi

# ------------------------------------------------------------------ tabela de controle
if [ "$MODO" = "--somente-checar" ]; then
  ok "modo --somente-checar: nenhuma DDL sera executada"
else
  if docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -q -c \
    "CREATE TABLE IF NOT EXISTS $VERSAO_CONTROLE (versao varchar(20) PRIMARY KEY, arquivo varchar(255) NOT NULL, sha256 char(64) NOT NULL, aplicada_em timestamptz NOT NULL DEFAULT NOW(), aplicada_por varchar(120) NOT NULL);" >/dev/null 2>&1; then
    ok "tabela de controle pronta ($VERSAO_CONTROLE)"
  else
    morrer "FALHOU nao consegui criar/ler a tabela de controle $VERSAO_CONTROLE"
  fi
fi

# ------------------------------------------------------------------ log por execucao
# Criado ANTES de aplicar (o log precisa existir antes do `psql`). mktemp da nome unico e
# dono = quem executa, entao duas execucoes concorrentes no mesmo host NAO compartilham log
# e nenhuma depende de um caminho que outro usuario possa possuir. Removido no fim.
DIR_LOG=""
if [ "$MODO" != "--somente-checar" ]; then
  if ! DIR_LOG="$(mktemp -d "${TMPDIR:-/tmp}/tre_migracao.XXXXXXXXXX" 2>/dev/null)" || [ -z "$DIR_LOG" ]; then
    morrer "FALHOU nao consegui criar o diretorio de log por execucao em '${TMPDIR:-/tmp}' (permissao/espaco) — sem log confiavel a aplicacao NAO roda. Nada foi aplicado."
  fi
  if [ ! -w "$DIR_LOG" ]; then
    morrer "FALHOU diretorio de log por execucao '$DIR_LOG' nao e gravavel — sem log confiavel a aplicacao NAO roda. Nada foi aplicado."
  fi
  trap 'rm -rf "$DIR_LOG"' EXIT INT TERM HUP
  ok "log da execucao em '$DIR_LOG' (por execucao; removido no fim)"
fi

# Aplica SEM depender de stdin: o arquivo entra no container por `docker cp` e sai por
# `psql -f`. (Um `docker exec -i` aqui comeria o stdin de quem orquestra por SSH.)
# O destino DENTRO do container tambem e unico por execucao (`$$`): duas execucoes
# concorrentes no mesmo container nao disputam o mesmo arquivo temporario.
aplicar_arquivo() {  # <servico> <usuario> <banco> <arquivo-do-host> <log-da-execucao>
  local destino="/tmp/tre_aplicar_$$_$(basename "$4")" rc
  docker cp "$4" "$1:$destino" >/dev/null 2>&1 || return 1
  docker exec "$1" psql -U "$2" -d "$3" -v ON_ERROR_STOP=1 -q -f "$destino" >"$5" 2>&1
  rc=$?
  docker exec "$1" rm -f "$destino" >/dev/null 2>&1
  return $rc
}

AUTOR="$(whoami)@$(hostname)"
APLICADAS=0
PULADAS=0

for arq in "${MAPA[@]}"; do
  base="$(basename "$arq")"
  versao="$(printf '%s' "$base" | cut -d_ -f1)"
  sha="$(sha256sum "$arq" | cut -d' ' -f1)"

  ja="$(consulta "$SERVICO" "$USUARIO" "$BANCO" \
    "SELECT sha256 FROM $VERSAO_CONTROLE WHERE versao='$versao'")"
  if [ -n "$ja" ]; then
    if [ "$ja" = "$sha" ]; then
      pula "versao $versao ($base) ja aplicada com o mesmo sha256 (${sha:0:12}…)"
      PULADAS=$((PULADAS + 1))
      continue
    fi
    morrer "FALHOU versao $versao ($base) ja aplicada com sha256 ${ja:0:12}… e o arquivo atual tem ${sha:0:12}… — migration aplicada e IMUTAVEL (BRANCHING.md). Nada aplicado."
  fi

  if [ "$MODO" = "--somente-checar" ]; then
    ok "pendente: versao $versao ($base, ${sha:0:12}…)"
    APLICADAS=$((APLICADAS + 1))
    continue
  fi

  LOG_APLICACAO="$DIR_LOG/tre_migracao_${versao}.log"
  if aplicar_arquivo "$SERVICO" "$USUARIO" "$BANCO" "$arq" "$LOG_APLICACAO"; then
    if docker exec "$SERVICO" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -q -c \
      "INSERT INTO $VERSAO_CONTROLE (versao, arquivo, sha256, aplicada_por) VALUES ('$versao','$base','$sha','$AUTOR') ON CONFLICT (versao) DO NOTHING;" >/dev/null 2>&1; then
      ok "versao $versao ($base) aplicada e registrada (${sha:0:12}…)"
      APLICADAS=$((APLICADAS + 1))
    else
      ko "versao $versao ($base) aplicada mas NAO registrada na tabela de controle — registro obrigatorio"
    fi
  else
    if [ -s "$LOG_APLICACAO" ]; then
      ko "versao $versao ($base) falhou: $(tail -3 "$LOG_APLICACAO" | tr '\n' ' ')"
    else
      ko "versao $versao ($base) falhou SEM diagnostico do psql (log '$LOG_APLICACAO' ausente/vazio) — a causa NAO esta no SQL: a aplicacao nao chegou a rodar (docker cp/redirecionamento/container '$SERVICO'). Nada foi aplicado nem registrado."
    fi
  fi
done

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: MIGRACAO_OK ($MODO; $APLICADAS aplicada(s)/pendente(s), $PULADAS pulada(s), $ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: MIGRACAO_FALHOU ($FALHAS falha(s) de $ITENS itens)"
exit 1
