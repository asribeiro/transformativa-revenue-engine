#!/usr/bin/env bash
# =====================================================================================
# teste-log-migracao.sh [raiz_do_repo]
#
# PROVA, com Docker e SEM tocar em ambiente real nenhum, que o log da aplicacao do runner
# de migracoes e POR EXECUCAO — correcao do defeito D02 do TRE-W1-E01-T01 (o runner gravava
# em caminho FIXO /tmp/tre_migracao_<versao>.log, deixava o arquivo no host e a execucao
# seguinte, de OUTRO usuario, morria com diagnostico VAZIO: /tmp e sticky 1777 e o host tem
# fs.protected_regular=2, entao o O_CREAT de um arquivo regular de outro dono da EACCES,
# inclusive para root).
#
#   1. monta o cenario do defeito: /tmp/tre_migracao_0001.log VAZIO com dono de OUTRO usuario
#      (exatamente a sobra que o defeito deixou no host), e confirma que abrir esse caminho da
#      EACCES (sem o cenario, o teste nao discrimina nada);
#   2. runner atual, no mesmo cenario, contra container DESCARTAVEL -> MIGRACAO_OK exit 0 e o
#      arquivo plantado NAO e tocado (o runner nao usa mais caminho fixo);
#   3. (opcional, `TRE_D02_RUNNER_ANTIGO`) o runner ANTERIOR a correcao, no MESMO cenario,
#      falha com diagnostico VAZIO e nenhuma DDL — e o comparativo antes/depois;
#   4. o proprio runner diz qual diretorio de log por execucao usou, e o remove no fim;
#   5. duas execucoes CONCORRENTES no mesmo host -> cada uma com diretorio de log PROPRIO;
#   6. migration que falha -> diagnostico EXPLICITO (nunca vazio) e fail-closed (sem DDL,
#      sem linha de versao);
#   7. TMPDIR nao gravavel -> mensagem explicita e exit 1 (sem log confiavel, nao roda);
#   8. limpeza: containers descartaveis removidos e o caminho fixo devolvido ao estado anterior.
#
# Sem os passos 1, 3 e 6 o teste seria carimbo. Os containers sao proprios deste teste
# (tre-d02-*), criados e removidos por ele; o runner aponta para eles por variavel de
# ambiente (que vence o arquivo versionado), e as copias temporarias do repo NAO carregam
# `deploy/environments/` justamente para que o ambiente dev real nunca seja alvo.
#
# Requer: docker, root (para proprietario do arquivo plantado). Variaveis:
#   TRE_D02_IMAGEM (padrao postgres:16), TRE_D02_OUTRO_USUARIO (padrao tre-deploy),
#   TRE_D02_RUNNER_ANTIGO (opcional: runner ANTERIOR a correcao, para o comparativo
#   antes/depois — sem ele o comparativo nao roda), TRE_RAIZ
# =====================================================================================
set -uo pipefail

RAIZ_REPO="${1:-${TRE_RAIZ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
IMAGEM="${TRE_D02_IMAGEM:-postgres:16}"
OUTRO="${TRE_D02_OUTRO_USUARIO:-tre-deploy}"
USUARIO="sales_ai"
BANCO="sales_intelligence"
ARQ_FIXO="/tmp/tre_migracao_0001.log"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-d02-teste-XXXXXX")"
ITENS=0
FALHAS=0
SERVICOS=()

ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

limpar() {
  local s
  for s in "${SERVICOS[@]:-}"; do [ -n "$s" ] && docker rm -f "$s" >/dev/null 2>&1; done
  # devolve o caminho fixo ao estado anterior (o defeito deixou esse arquivo no host)
  if [ -f "$TMP/plantado_original" ]; then
    cp -a "$TMP/plantado_original" "$ARQ_FIXO" 2>/dev/null
  else
    rm -f "$ARQ_FIXO" 2>/dev/null
  fi
  echo
  echo "artefatos do teste em: $TMP"
}
trap limpar EXIT

echo "=================================================================="
echo "-- TESTE DO LOG POR EXECUCAO DO RUNNER (defeito D02 / TRE-W1-E01-T01)"
echo "-- repo:   $RAIZ_REPO"
echo "-- imagem: $IMAGEM"
echo "-- alvo:   containers descartaveis tre-d02-* (criados e removidos aqui)"
echo "=================================================================="

if [ "$(id -u)" != "0" ]; then
  echo "FALHOU este teste precisa de root para plantar '/tmp' de outro dono"
  echo "RESULTADO: TESTE_FALHOU"
  exit 1
fi
if ! id -u "$OUTRO" >/dev/null 2>&1; then
  ko "usuario '$OUTRO' nao existe neste host (use TRE_D02_OUTRO_USUARIO=<outro-usuario>)"
  echo "RESULTADO: TESTE_FALHOU"; exit 1
fi

# ------------------------------------------------------------------ copias do repo
# Copia do runner e das migrations SO para um diretorio temporario: o teste nunca aponta
# para o ambiente real. Sem `deploy/environments/` de proposito (o alvo vem do ambiente).
preparar_repo() {  # <destino>
  mkdir -p "$1/scripts/db" "$1/db/migrations"
  cp "$RAIZ_REPO/scripts/db/aplicar_migracoes.sh" "$1/scripts/db/aplicar_migracoes.sh"
}
REPO_A="$TMP/repo_a"
preparar_repo "$REPO_A"
cp "$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql" "$REPO_A/db/migrations/"
SHA_0001="$(sha256sum "$REPO_A/db/migrations/0001_sales_intelligence_v1.sql" | cut -d' ' -f1)"

REPO_Q="$TMP/repo_q"
preparar_repo "$REPO_Q"
cat >"$REPO_Q/db/migrations/0002_quebrada.sql" <<'SQL'
-- migration de teste (nao versionada no repo): SQL invalido DE PROPOSITO
CREATE TABLE sales_intelligence.d02_quebrada (id integer PRIMARY KEY;
SELECT coluna_que_nao_existe_d02;
SQL

# ------------------------------------------------------------------ helpers de execucao
criar_container() {  # <nome>
  docker run -d --name "$1" -e POSTGRES_PASSWORD=tre -e POSTGRES_USER="$USUARIO" \
    -e POSTGRES_DB="$BANCO" "$IMAGEM" >/dev/null 2>&1
}

rodar_runner() {  # <repo> <servico> <arquivo-de-saida> [TMPDIR]
  local repo="$1" servico="$2" saida="$3" tmpdir="${4:-}"
  SERVICOS+=("$servico")
  if [ -n "$tmpdir" ]; then
    TMPDIR="$tmpdir" TRE_PG_SERVICO="$servico" TRE_PG_USER="$USUARIO" TRE_PG_DB="$BANCO" \
      bash "$repo/scripts/db/aplicar_migracoes.sh" dev >"$saida" 2>&1
  else
    TRE_PG_SERVICO="$servico" TRE_PG_USER="$USUARIO" TRE_PG_DB="$BANCO" \
      bash "$repo/scripts/db/aplicar_migracoes.sh" dev >"$saida" 2>&1
  fi
}

consultar() {  # <servico> <sql>
  docker exec "$1" psql -U "$USUARIO" -d "$BANCO" -tAc "$2" 2>/dev/null
}

# ------------------------------------------------------------------ 1. cenario do defeito
if [ -f "$ARQ_FIXO" ]; then cp -a "$ARQ_FIXO" "$TMP/plantado_original"; fi
rm -f "$ARQ_FIXO" 2>/dev/null
: >"$ARQ_FIXO" && chown "$OUTRO:$OUTRO" "$ARQ_FIXO" && chmod 0664 "$ARQ_FIXO"
if [ "$(stat -c '%U' "$ARQ_FIXO" 2>/dev/null)" = "$OUTRO" ]; then
  ok "caminho fixo plantado: '$ARQ_FIXO' (dono $OUTRO, $(stat -c '%s' "$ARQ_FIXO") bytes — a sobra exata que o defeito deixou)"
else
  ko "nao consegui plantar '$ARQ_FIXO' com dono de outro usuario"
fi
SHA_PLANTADO="$(sha256sum "$ARQ_FIXO" | cut -d' ' -f1)"
STAT_PLANTADO="$(stat -c '%U %s %Y' "$ARQ_FIXO")"

# Prova de que o cenario morde: abrir o caminho plantado (mesmo O_CREAT do redirecionamento
# antigo) tem de dar EACCES para quem executa o teste. `exec 9>>` nao escreve nada (nao
# altera o arquivo), mas exercita exatamente a abertura que falhava.
if ( exec 9>>"$ARQ_FIXO" ) 2>"$TMP/precheck.err"; then
  ko "o cenario NAO reproduz o defeito: abrir '$ARQ_FIXO' para acrescentar FUNCIONOU"
else
  ok "cenario reproduzido: abrir '$ARQ_FIXO' para acrescentar da '$(tail -1 "$TMP/precheck.err")'"
fi

# ------------------------------------------------------------------ 2. runner no cenario
SERVICO_A="tre-d02-a-$$-$RANDOM"
if criar_container "$SERVICO_A"; then
  ok "container descartavel '$SERVICO_A' criado"
else
  ko "nao consegui criar o container descartavel"; echo "RESULTADO: TESTE_FALHOU"; exit 1
fi
rodar_runner "$REPO_A" "$SERVICO_A" "$TMP/a.log"
RC_A=$?
if [ "$RC_A" -eq 0 ] && grep -q 'RESULTADO: MIGRACAO_OK' "$TMP/a.log"; then
  ok "runner no cenario do defeito: MIGRACAO_OK, exit 0 (antes da correcao isto dava exit 1 com diagnostico vazio)"
else
  ko "runner falhou no cenario do defeito (exit $RC_A): $(grep -m1 '^FALHOU' "$TMP/a.log")"
fi

DIR_LOG_A="$(sed -n "s/^OK *log da execucao em '\(.*\)' (por execucao.*/\1/p" "$TMP/a.log" | head -1)"
if [ -n "$DIR_LOG_A" ] && [ "$DIR_LOG_A" != "$ARQ_FIXO" ]; then
  ok "runner declarou o diretorio de log por execucao: '$DIR_LOG_A'"
else
  ko "runner nao declarou diretorio de log por execucao (log dir='$DIR_LOG_A')"
fi
if [ ! -e "$DIR_LOG_A" ]; then
  ok "diretorio de log da execucao foi removido no fim ('$DIR_LOG_A' nao existe mais)"
else
  ko "diretorio de log da execucao sobrou no host: '$DIR_LOG_A'"
fi
if [ "$(sha256sum "$ARQ_FIXO" | cut -d' ' -f1)" = "$SHA_PLANTADO" ] &&
   [ "$(stat -c '%U %s %Y' "$ARQ_FIXO")" = "$STAT_PLANTADO" ]; then
  ok "caminho fixo plantado INTACTO depois do runner (conteudo, dono, tamanho e mtime)"
else
  ko "o runner tocou no caminho fixo plantado '$ARQ_FIXO'"
fi

SHA_REGISTRADO="$(consultar "$SERVICO_A" "SELECT sha256 FROM public.tre_schema_migrations WHERE versao='0001'")"
TABELAS="$(consultar "$SERVICO_A" "SELECT count(*) FROM information_schema.tables WHERE table_schema='$BANCO'")"
if [ "$SHA_REGISTRADO" = "$SHA_0001" ] && [ "${TABELAS:-0}" -ge 12 ]; then
  ok "aplicacao chegou no alvo de verdade: 0001 registrada com o sha do arquivo e $TABELAS tabelas no schema"
else
  ko "alvo nao ficou como esperado (sha='$SHA_REGISTRADO', tabelas='$TABELAS')"
fi

# ------------------------------------------------------------------ 3. comparativo antes/depois
if [ -n "${TRE_D02_RUNNER_ANTIGO:-}" ] && [ -f "${TRE_D02_RUNNER_ANTIGO}" ]; then
  REPO_O="$TMP/repo_antigo"
  preparar_repo "$REPO_O"
  cp "$TRE_D02_RUNNER_ANTIGO" "$REPO_O/scripts/db/aplicar_migracoes.sh"
  cp "$REPO_A/db/migrations/0001_sales_intelligence_v1.sql" "$REPO_O/db/migrations/"
  SERVICO_O="tre-d02-o-$$-$RANDOM"
  if criar_container "$SERVICO_O"; then
    rodar_runner "$REPO_O" "$SERVICO_O" "$TMP/antigo.log"
    RC_O=$?
    LINHA_O="$(grep -m1 '^FALHOU versao 0001' "$TMP/antigo.log")"
    if [ "$RC_O" -eq 1 ] && [ -n "$LINHA_O" ] && printf '%s' "$LINHA_O" | grep -q 'falhou: *$'; then
      ok "runner ANTERIOR no MESMO cenario: exit 1 com diagnostico VAZIO ('$LINHA_O')"
    else
      ko "runner anterior nao reproduziu o defeito (exit $RC_O, linha='$LINHA_O')"
    fi
    if grep -q 'Permission denied' "$TMP/antigo.log"; then
      ok "a causa medida do defeito aparece no log: '$(grep -m1 'Permission denied' "$TMP/antigo.log" | sed 's/^.*: //')'"
    else
      ko "o log do runner anterior nao traz a causa (Permission denied) — cenario diferente do medido"
    fi
    TAB_O="$(consultar "$SERVICO_O" "SELECT count(*) FROM information_schema.tables WHERE table_schema='$BANCO'")"
    LINHA_CTRL_O="$(consultar "$SERVICO_O" "SELECT count(*) FROM public.tre_schema_migrations WHERE versao='0001'")"
    if [ "${TAB_O:-1}" = "0" ] && [ "${LINHA_CTRL_O:-1}" = "0" ]; then
      ok "runner anterior falhou FAIL-CLOSED: 0 tabela no schema e 0 linha de versao (nenhum aceite falso)"
    else
      ko "runner anterior nao ficou fail-closed (tabelas='$TAB_O', linhas='$LINHA_CTRL_O')"
    fi
  else
    ko "nao consegui criar o container do comparativo antes/depois"
  fi
else
  echo "NOTA  comparativo antes/depois NAO rodou (defina TRE_D02_RUNNER_ANTIGO=<runner anterior>)"
fi

# ------------------------------------------------------------------ 4. TMPDIR nao gravavel
rodar_runner "$REPO_A" "$SERVICO_A" "$TMP/tmpdir.log" "$TMP/nao-existe/nem-o-pai"
RC_T=$?
if [ "$RC_T" -eq 1 ] && grep -q 'nao consegui criar o diretorio de log por execucao' "$TMP/tmpdir.log"; then
  ok "TMPDIR nao gravavel: recusa com causa EXPLICITA e exit 1 ('$(grep -m1 '^FALHOU' "$TMP/tmpdir.log" | cut -c1-90)…')"
else
  ko "TMPDIR nao gravavel nao deu a causa explicita esperada (exit $RC_T)"
fi

# ------------------------------------------------------------------ 5. falha com causa
rodar_runner "$REPO_Q" "$SERVICO_A" "$TMP/quebrada.log"
RC_Q=$?
LINHA_FALHA="$(grep -m1 '^FALHOU versao 0002' "$TMP/quebrada.log")"
if [ "$RC_Q" -eq 1 ] && [ -n "$LINHA_FALHA" ]; then
  ok "migration invalida: exit 1 com linha de falha preenchida"
else
  ko "migration invalida nao falhou como esperado (exit $RC_Q)"
fi
if printf '%s' "$LINHA_FALHA" | grep -qiE 'error|erro|syntax'; then
  ok "diagnostico EXPLICITO (nunca vazio): '$(printf '%s' "$LINHA_FALHA" | cut -c1-120)…'"
else
  ko "diagnostico vazio ou sem causa apontada: '$LINHA_FALHA'"
fi
LINHA_0002="$(consultar "$SERVICO_A" "SELECT count(*) FROM public.tre_schema_migrations WHERE versao='0002'")"
TABELA_Q="$(consultar "$SERVICO_A" "SELECT count(*) FROM information_schema.tables WHERE table_schema='$BANCO' AND table_name='d02_quebrada'")"
if [ "${LINHA_0002:-1}" = "0" ] && [ "${TABELA_Q:-1}" = "0" ]; then
  ok "fail-closed na falha: 0 linha de versao para 0002 e nenhuma tabela criada por ela"
else
  ko "fail-closed violado (linhas 0002='$LINHA_0002', tabela d02_quebrada='$TABELA_Q')"
fi

# ------------------------------------------------------------------ 6. concorrencia
SERVICO_D1="tre-d02-d1-$$-$RANDOM"
SERVICO_D2="tre-d02-d2-$$-$RANDOM"
criar_container "$SERVICO_D1"; criar_container "$SERVICO_D2"
TRE_PG_SERVICO="$SERVICO_D1" TRE_PG_USER="$USUARIO" TRE_PG_DB="$BANCO" \
  bash "$REPO_A/scripts/db/aplicar_migracoes.sh" dev >"$TMP/d1.log" 2>&1 &
PID1=$!
TRE_PG_SERVICO="$SERVICO_D2" TRE_PG_USER="$USUARIO" TRE_PG_DB="$BANCO" \
  bash "$REPO_A/scripts/db/aplicar_migracoes.sh" dev >"$TMP/d2.log" 2>&1 &
PID2=$!
wait $PID1; RC_D1=$?
wait $PID2; RC_D2=$?
SERVICOS+=("$SERVICO_D1" "$SERVICO_D2")
if [ "$RC_D1" -eq 0 ] && [ "$RC_D2" -eq 0 ] &&
   grep -q 'RESULTADO: MIGRACAO_OK' "$TMP/d1.log" && grep -q 'RESULTADO: MIGRACAO_OK' "$TMP/d2.log"; then
  ok "duas execucoes CONCORRENTES no mesmo host terminaram OK (exit 0 nas duas)"
else
  ko "execucoes concorrentes falharam (exit $RC_D1/$RC_D2)"
fi
DIR_D1="$(sed -n "s/^OK *log da execucao em '\(.*\)' (por execucao.*/\1/p" "$TMP/d1.log" | head -1)"
DIR_D2="$(sed -n "s/^OK *log da execucao em '\(.*\)' (por execucao.*/\1/p" "$TMP/d2.log" | head -1)"
if [ -n "$DIR_D1" ] && [ -n "$DIR_D2" ] && [ "$DIR_D1" != "$DIR_D2" ]; then
  ok "logs NAO compartilhados: '$DIR_D1' != '$DIR_D2'"
else
  ko "as duas execucoes usaram o mesmo log (ou nenhum): '$DIR_D1' / '$DIR_D2'"
fi
if [ ! -e "$DIR_D1" ] && [ ! -e "$DIR_D2" ]; then
  ok "os dois diretorios de log foram removidos no fim"
else
  ko "sobrou diretorio de log: '$DIR_D1' / '$DIR_D2'"
fi
if [ "$(sha256sum "$ARQ_FIXO" | cut -d' ' -f1)" = "$SHA_PLANTADO" ]; then
  ok "caminho fixo plantado segue intato depois das execucoes concorrentes"
else
  ko "o caminho fixo plantado foi alterado por uma das execucoes concorrentes"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: TESTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
exit 1
