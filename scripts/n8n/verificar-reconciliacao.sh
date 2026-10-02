#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E04-T01 — job de RECONCILIACAO (board transformativa-revenue-engine,
# card t_2ee17829).
#
# Criterios de aceitacao (declarados no card e no runbook docs/runbooks/n8n-reconciliacao.md):
#   AC1 job DECLARADO: o workflow versionado e' o resultado dos artefatos versionados
#       (contrato + nucleo + SQL) — o Code node embute o nucleo byte a byte e o contrato
#       igual, o SQL dos nos e' o arquivo, e nada foi escrito a mao por fora deles;
#   AC2 porta unica: a UNICA chamada e' POST /tf/api/v1/crm_registros_ler (operacao de
#       LEITURA declarada) por $env.TRE_API_BASE com a credencial declarada por id/nome;
#       o pedido vai dentro do involucro `{parametros: {...}}`; nenhum caminho paralelo,
#       nenhum host literal, nenhum segredo no versionado;
#   AC3 comparacoes NOMEADAS: cada defeito declarado (espelho ausente/arquivado, identidade
#       forte divergente, ID cruzado divergente/duplicado/sem volta/fora do lote, evento
#       pendente alem da janela, evento entregue ainda na fila, evento no teto, recusa na
#       fila) e' medido em EXECUCAO REAL contra PostgreSQL + Odoo e aparece com o seu nome;
#   AC4 fail-closed: rodada sem medicao (porta unica fora do ar, consulta sem linha de
#       cobertura, leitura recusada) fecha INDETERMINADO — nunca OK;
#   AC5 somente-leitura: o job NAO escreve nada — digest das tres tabelas do PostgreSQL e
#       contagem de parceiros do Odoo medidos antes e depois de CADA rodada;
#   AC6 a rodada saudavel fecha OK com ZERO divergencia (o job nao alarma sozinho) e a
#       ordem do veredito e' OK < DIVERGENTE < INDETERMINADO;
#   AC7 sem segredo em claro: a chave da API vive em arquivo 600 do diretorio descartavel e
#       nao aparece em arquivo de cofre nem no versionado.
#
# TEST PLAN (executado por ESTE script, na VPS, por execucao real):
#   passo 0   lente estrutural (python puro) + suite do nucleo (node na imagem do n8n)
#             + montador --conferir (o workflow e' artefato DERIVADO)
#   passo 0d  prova de dente: baseline verde e cada mutante reprovando O ITEM que ele quebra
#   guardas   docker, imagens, ferramentas, artefatos em disco, banco descartavel, dev antes
#   trio      postgres descartavel (schema + modulo Odoo instalado) + servidor da porta unica
#             com chave de API + n8n descartavel com as duas credenciais do contrato
#   estados   A espelho saude -> OK | B espelho ausente -> E1 | C id cruzado -> I1
#             D identidade forte -> E4 | E arquivado -> E2 | F fila x trilha -> P1..P4
#             G porta unica fora do ar -> INDETERMINADO (fail-closed em execucao real)
#   fecho     sha256 reconferido, ambiente do dev depois, segredo em claro, limpeza
#
# Uso (NA VPS):
#   bash scripts/n8n/verificar-reconciliacao.sh                 (completo)
#   bash scripts/n8n/verificar-reconciliacao.sh --apenas-codigo (lente + suite + montador + dente)
#   bash scripts/n8n/verificar-reconciliacao.sh --prova-de-dente
#   bash scripts/n8n/verificar-reconciliacao.sh --manter         (nao limpa o trio no fim)
# Variaveis: TRE_WORKFLOW, TRE_MODULO_DIR, TRE_BANCO, TRE_BANCO_SI, TRE_IMAGEM,
#            TRE_IMAGEM_PG, TRE_IMAGEM_N8N, TRE_LOG_DIR, TRE_PG_USER, TRE_DEV_PG_CT,
#            TRE_DEV_PG_USER, TRE_MANTER_BANCO.
#
# Saida: um item por linha (OK/FALHOU) e o resumo
#   RESULTADO: RECONCILIACAO_OK|FALHOU (...); exit 0 = cumprido, 1 = falhou, 2 = uso errado.
# ============================================================================
set -u

RAIZ_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKFLOW="${TRE_WORKFLOW:-$RAIZ_REPO/n8n/workflows/TRE-reconciliation.json}"
CONTRATO="$RAIZ_REPO/n8n/contracts/reconciliation-job.v1.json"
NUCLEO="$RAIZ_REPO/n8n/codigo/nucleo-reconciliacao.js"
SQL_ORIGEM="$RAIZ_REPO/n8n/sql/reconciliacao-origem.sql"
SQL_PENDENTES="$RAIZ_REPO/n8n/sql/reconciliacao-pendentes.sql"
MASSA="$RAIZ_REPO/scripts/n8n/massa-reconciliacao.sql"
MASSA_ODOO="$RAIZ_REPO/scripts/odoo/massa_reconciliacao.py"
MONTADOR="$RAIZ_REPO/scripts/n8n/montar_workflow_reconciliacao.py"
MUTADOR="$RAIZ_REPO/scripts/n8n/mutar_reconciliacao.py"
LENTE="$RAIZ_REPO/scripts/n8n/conferir_reconciliacao.py"
SUITE="$RAIZ_REPO/scripts/n8n/testar_nucleo_reconciliacao.js"
LEITOR="$RAIZ_REPO/scripts/n8n/ler_resultado_reconciliacao.py"
MIGRATION="$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql"
PREPARADOR="$RAIZ_REPO/scripts/odoo/preparar_api_teste.py"
MODULO="transformativa_sales_ai"
MODULO_DIR="${TRE_MODULO_DIR:-$RAIZ_REPO/odoo/addons/$MODULO}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
IMAGEM_N8N="${TRE_IMAGEM_N8N:-n8nio/n8n:latest}"
PG_USER="${TRE_PG_USER:-tre}"
ID_WORKFLOW="${TRE_ID_WORKFLOW:-TRERECONCILIA01}"
BANCO_SI="${TRE_BANCO_SI:-sales_intelligence}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-reconciliacao}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
# O dono do `pg-odoo-dev` e' o USUARIO DO PROPRIO DEV (`POSTGRES_USER=odoo`), nao o usuario do banco
# descartavel (`TRE_PG_USER`, default `tre`): medir o dev com `tre` da' `FATAL: role "tre" does not
# exist`, as duas pontas ficam vazias e `[ "" = "" ]` fechava OK sem medir nada (defeito D01).
DEV_PG_USER="${TRE_DEV_PG_USER:-odoo}"
MANTER="${TRE_MANTER_BANCO:-0}"
SUFIXO="$$$RANDOM"
BANCO="${TRE_BANCO:-tre_reconc_$SUFIXO}"
ESTADOS="A B C D E F G"

# ids fixos do aceite (nenhum deles existe fora do banco descartavel)
ORG_A="aaaaaaaa-0000-4000-8000-0000000000a1"
ORG_C="cccccccc-0000-4000-8000-0000000000c1"
TRILHA_A="11111111-1111-4111-8111-1111111111a1"
LINHA_P2="22222222-2222-4222-8222-2222222222b2"
LINHA_P4="44444444-4444-4444-8444-4444444444b4"
EVENTO_P1="11111111-1111-4111-8111-1111111111f1"
EVENTO_P2="22222222-2222-4222-8222-2222222222f2"
EVENTO_P3="33333333-3333-4333-8333-3333333333f3"
EVENTO_P4="44444444-4444-4444-8444-4444444444f4"
PARCEIRO_INEXISTENTE=990000001

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-codigo)  MODO=codigo ;;
        --prova-de-dente) MODO=dente ;;
        --manter)         MANTER=1 ;;
        --workflow)       shift; WORKFLOW="${1:-}" ;;
        --log-dir)        shift; LOG_DIR="${1:-}" ;;
        --banco)          shift; BANCO="${1:-}" ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
    shift
done

ITENS=0
FALHAS=0
PG_TMP="e04t01-pg-$SUFIXO"
API_CT="e04t01-api-$SUFIXO"
NET_TMP="e04t01-net-$SUFIXO"
DESC_DIR=""
N8N_HOME=""
mkdir -p "$LOG_DIR"

ok()        { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()    { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()      { printf 'INFO  %s\n' "$*"; }
cabecalho() { printf '\n=== %s ===\n' "$*"; }
limpar()    { printf '%s' "$1" | tr -d '[:space:]'; }
podar()     { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }
# Bancos do PostgreSQL do DEV, medidos DE VERDADE: devolve a lista (e rc 0) so' quando o container
# esta' de pe, o psql responde com o USUARIO DO PROPRIO DEV e a consulta traz linha. Vazio nao e'
# medicao — quem chama fecha FALHOU nomeado, nunca OK (`"" = ""` era o defeito D01).
medir_bancos_do_dev() {
    local saida
    [ "$(docker inspect -f '{{.State.Running}}' "$DEV_PG_CT" 2>/dev/null)" = "true" ] || return 1
    saida="$(docker exec "$DEV_PG_CT" psql -U "$DEV_PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)" || return 1
    saida="$(podar "$saida")"
    [ -n "$saida" ] || return 1
    printf '%s' "$saida"
}
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: RECONCILIACAO_OK ($ITENS itens, 0 falhas) banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG+$IMAGEM_N8N workflow=$WORKFLOW"
        exit 0
    fi
    echo "RESULTADO: RECONCILIACAO_FALHOU ($ITENS itens, $FALHAS falha(s)) banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG+$IMAGEM_N8N workflow=$WORKFLOW"
    exit 1
}

sha256_dos_artefatos() { # $1 = arquivo onde gravar
    local destino="$1" arquivo
    : >"$destino"
    for arquivo in "$CONTRATO" "$NUCLEO" "$SQL_ORIGEM" "$SQL_PENDENTES" "$WORKFLOW" \
                   "$MONTADOR" "$MUTADOR" "$LENTE" "$SUITE" "$LEITOR" "$MASSA" "$MASSA_ODOO"; do
        printf '%s  %s\n' "$(sha256sum "$arquivo" | cut -d' ' -f1)" "${arquivo#"$RAIZ_REPO"/}" >>"$destino"
    done
}

# ---------------------------------------------------------------------------
# passo 0 — lente estrutural + suite do nucleo + montador (o workflow e' derivado)
# ---------------------------------------------------------------------------
passo_codigo() {
    cabecalho "passo 0 — lente estrutural (contrato x SQL x nucleo x workflow)"
    SAIDA_LENTE="$(python3 "$LENTE" --raiz "$RAIZ_REPO" 2>&1)"
    printf '%s\n' "$SAIDA_LENTE" >"$LOG_DIR/0-lente.out"
    RESULTADO_LENTE="$(printf '%s\n' "$SAIDA_LENTE" | grep -E '^RESULTADO: ' | tail -1)"
    case "$RESULTADO_LENTE" in
        *RECONCILIACAO_LENTE_OK*) ok "lente estrutural: $RESULTADO_LENTE" ;;
        *) falhou "lente estrutural NAO passou: ${RESULTADO_LENTE:-sem linha de resultado}" ;;
    esac

    cabecalho "passo 0b — suite do nucleo (node, dentro da imagem do n8n)"
    SAIDA_SUITE="$(docker run --rm -v "$RAIZ_REPO":/repo:ro -v "$(dirname "$WORKFLOW")":/mutado:ro \
        --entrypoint node "$IMAGEM_N8N" /repo/scripts/n8n/testar_nucleo_reconciliacao.js \
        --workflow "/mutado/$(basename "$WORKFLOW")" 2>&1)"
    printf '%s\n' "$SAIDA_SUITE" >"$LOG_DIR/0b-suite.out"
    RESULTADO_SUITE="$(printf '%s\n' "$SAIDA_SUITE" | grep -E '^RESULTADO: ' | tail -1)"
    case "$RESULTADO_SUITE" in
        *RECONCILIACAO_NUCLEO_OK*) ok "suite do nucleo: $RESULTADO_SUITE" ;;
        *) falhou "suite do nucleo NAO passou: ${RESULTADO_SUITE:-sem linha de resultado}" ;;
    esac

    cabecalho "passo 0c — montador: o workflow em disco e' o montado agora"
    if python3 "$MONTADOR" --conferir >"$LOG_DIR/0c-montador.out" 2>&1; then
        ok "montador: $(cat "$LOG_DIR/0c-montador.out")"
    else
        falhou "processo derivado divergente: $(cat "$LOG_DIR/0c-montador.out")"
    fi
    printf 'FASE_CODIGO_OK\n'
}

# ---------------------------------------------------------------------------
# passo 0d — prova de dente: cada mutante TEM de reprovar o item que ele quebra
# ---------------------------------------------------------------------------
juizo_do_dente() { # $1=saida  $2=item esperado (vazio = qualquer FALHOU) -> CUMPRIDO|NAO_CUMPRIDO
    local saida="$1" esperado="$2"
    if [ -z "$esperado" ]; then
        printf '%s\n' "$saida" | grep -q '^FALHOU ' && echo CUMPRIDO || echo NAO_CUMPRIDO
        return
    fi
    printf '%s\n' "$saida" | grep '^FALHOU ' | grep -qF -- "$esperado" && echo CUMPRIDO || echo NAO_CUMPRIDO
}

passo_dente() {
    cabecalho "passo 0d — prova de dente (baseline verde + cada mutante reprovando o SEU item)"
    BASELINE_LENTE="$(python3 "$LENTE" --raiz "$RAIZ_REPO" 2>&1)"
    case "$(printf '%s\n' "$BASELINE_LENTE" | grep -E '^RESULTADO: ' | tail -1)" in
        *LENTE_OK*) ok "baseline: lente estrutural verde (sem mutacao)" ;;
        *) falhou "baseline da lente NAO ficou verde: a prova de dente nao teria juiz" ;;
    esac
    BASELINE_SUITE="$(docker run --rm -v "$RAIZ_REPO":/repo:ro -v "$(dirname "$WORKFLOW")":/mutado:ro \
        --entrypoint node "$IMAGEM_N8N" /repo/scripts/n8n/testar_nucleo_reconciliacao.js \
        --workflow "/mutado/$(basename "$WORKFLOW")" 2>&1)"
    case "$(printf '%s\n' "$BASELINE_SUITE" | grep -E '^RESULTADO: ' | tail -1)" in
        *NUCLEO_OK*) ok "baseline: suite do nucleo verde (sem mutacao)" ;;
        *) falhou "baseline da suite NAO ficou verde: a prova de dente nao teria juiz" ;;
    esac

    MUT_DIR="$LOG_DIR/mutantes"
    mkdir -p "$MUT_DIR"
    LISTA_MUTACOES="$(python3 "$MUTADOR" --listar 2>&1)"
    if [ -z "$LISTA_MUTACOES" ]; then falhou "mutador nao listou as mutacoes"; fi
    while IFS="$(printf '\t')" read -r mutacao ALVO _descricao; do
        [ -z "$mutacao" ] && continue
        SAIDA_MUTADOR="$(python3 "$MUTADOR" --mutacao "$mutacao" --entrada "$WORKFLOW" \
            --saida "$MUT_DIR/$mutacao.json" 2>&1)"
        if [ $? -ne 0 ]; then
            falhou "mutacao $mutacao nao aplicou: $SAIDA_MUTADOR"
            continue
        fi
        case "$ALVO" in
            nucleo)
                SAIDA="$(docker run --rm -v "$RAIZ_REPO":/repo:ro -v "$MUT_DIR":/mutado:ro \
                    --entrypoint node "$IMAGEM_N8N" /repo/scripts/n8n/testar_nucleo_reconciliacao.js \
                    --workflow "/mutado/$mutacao.json" 2>&1)"
                ITEM="$(printf '%s\n' "$SAIDA" | grep '^FALHOU ' | head -1)"
                printf '%s\n' "$SAIDA" >"$MUT_DIR/$mutacao.out"
                case "$(juizo_do_dente "$SAIDA" "")" in
                    CUMPRIDO) ok "dente da mutacao $mutacao (alvo nucleo): $ITEM" ;;
                    NAO_CUMPRIDO) falhou "mutacao $mutacao NAO foi detectada pela suite do nucleo (dente nao cumpriu)" ;;
                esac
                ;;
            lente:*)
                ESPERADO="${ALVO#lente:}"
                SAIDA="$(python3 "$LENTE" --raiz "$RAIZ_REPO" --workflow "$MUT_DIR/$mutacao.json" 2>&1)"
                printf '%s\n' "$SAIDA" >"$MUT_DIR/$mutacao.out"
                ITEM="$(printf '%s\n' "$SAIDA" | grep '^FALHOU ' | grep -F -- "$ESPERADO" | head -1)"
                case "$(juizo_do_dente "$SAIDA" "$ESPERADO")" in
                    CUMPRIDO) ok "dente da mutacao $mutacao (alvo lente): $ITEM" ;;
                    NAO_CUMPRIDO) falhou "mutacao $mutacao NAO reprovou o item esperado da lente: $ESPERADO" ;;
                esac
                ;;
            *)
                falhou "mutacao $mutacao com alvo desconhecido: $ALVO"
                ;;
        esac
    done <<< "$LISTA_MUTACOES"
    printf 'FASE_DENTE_OK\n'
}

if [ "$MODO" = "dente" ]; then
    passo_dente
    resumo
fi

# ---------------------------------------------------------------------------
# guardas do ambiente (fail-closed: sem ambiente medido nao existe aceite)
# ---------------------------------------------------------------------------
cabecalho "guardas do ambiente"
if docker info >/dev/null 2>&1; then ok "docker responde"; else falhou "docker nao responde"; resumo; fi
for img in "$IMAGEM" "$IMAGEM_PG" "$IMAGEM_N8N"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
        ok "imagem $img presente ($(docker image inspect -f '{{index .RepoDigests 0}}' "$img" 2>/dev/null))"
    else
        falhou "imagem $img ausente (nada a medir)"; resumo
    fi
done
command -v openssl >/dev/null 2>&1 && ok "openssl disponivel (segredo do descartavel)" || { falhou "openssl ausente"; resumo; }
python3 --version >/dev/null 2>&1 && ok "python3 disponivel (lente, montador, leitor)" || { falhou "python3 ausente"; resumo; }
for arquivo in "$WORKFLOW" "$CONTRATO" "$NUCLEO" "$SQL_ORIGEM" "$SQL_PENDENTES" "$MASSA" "$MASSA_ODOO" \
               "$MONTADOR" "$MUTADOR" "$LENTE" "$SUITE" "$LEITOR" "$MIGRATION" "$PREPARADOR" \
               "$MODULO_DIR/__manifest__.py" "$MODULO_DIR/api/politica_api.json"; do
    if [ -f "$arquivo" ]; then ok "em disco: ${arquivo#"$RAIZ_REPO"/}"; else falhou "ausente: $arquivo"; fi
done
if [ "$FALHAS" -gt 0 ]; then resumo; fi
info "sha256 dos artefatos sob teste (fixado agora e reconferido no fecho):"
sha256_dos_artefatos "$LOG_DIR/sha256-antes.txt"
sed 's/^/      /' "$LOG_DIR/sha256-antes.txt"
if [ "$(wc -l <"$LOG_DIR/sha256-antes.txt" | tr -d ' ')" = "12" ]; then
    ok "sha256 dos 12 artefatos sob teste fixado (base da reconferencia do fecho)"
else
    falhou "nao consegui fixar o sha256 dos 12 artefatos"
fi
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e' do ambiente — so' banco descartavel"; resumo ;;
esac
if printf '%s' "$BANCO" | grep -qE '^tre_[a-z0-9_]+$' && ! printf '%s' "$BANCO" | grep -qEi 'prod|homolog'; then
    ok "banco do Odoo descartavel com nome seguro: $BANCO"
else
    falhou "nome de banco fora do padrao descartavel (^tre_[a-z0-9_]+$): $BANCO"; resumo
fi
DEV_PG_ANTES=""
if DEV_PG_ANTES="$(medir_bancos_do_dev)"; then
    info "instancia do dev ($DEV_PG_CT) ANTES: $DEV_PG_ANTES (usuario $DEV_PG_USER)"
else
    DEV_PG_ANTES=""
    info "instancia do dev ($DEV_PG_CT) NAO MEDIDA antes (container parado ou psql sem resposta para o usuario $DEV_PG_USER) — o item de fecho FALHA nomeado, medicao vazia nao vira OK"
fi
printf 'FASE_GUARDAS_OK\n'

if [ "$MODO" = "codigo" ]; then
    passo_codigo
    passo_dente
    resumo
fi

passo_codigo
passo_dente

# ---------------------------------------------------------------------------
# trio descartavel proprio (postgres + odoo + n8n)
# ---------------------------------------------------------------------------
cabecalho "trio descartavel proprio (postgres + odoo + n8n)"
DESC_DIR="$(mktemp -d /tmp/verificacao-reconciliacao-XXXXXX)"
chmod 700 "$DESC_DIR"
N8N_HOME="$DESC_DIR/n8n-home"
mkdir -p "$N8N_HOME"
chmod 700 "$N8N_HOME"
chown 1000:1000 "$N8N_HOME" 2>/dev/null || true
SENHA="$(openssl rand -hex 24)"
MASTER="$(openssl rand -hex 24)"
printf 'POSTGRES_USER=%s\nPOSTGRES_PASSWORD=%s\nPOSTGRES_DB=postgres\n' "$PG_USER" "$SENHA" >"$DESC_DIR/pg.env"
{
    echo '[options]'
    echo 'addons_path = /mnt/extra-addons'
    echo 'data_dir = /var/lib/odoo'
    echo "db_host = $PG_TMP"
    echo 'db_port = 5432'
    echo "db_user = $PG_USER"
    CHAVE_SENHA_BANCO='db_password'
    CHAVE_SENHA_MESTRE='admin_passwd'
    printf '%s = %s\n' "$CHAVE_SENHA_BANCO" "$SENHA"
    printf '%s = %s\n' "$CHAVE_SENHA_MESTRE" "$MASTER"
    echo 'without_demo = all'
    echo "dbfilter = ^$BANCO\$"
} >"$DESC_DIR/odoo.conf"
chown 100:101 "$DESC_DIR/odoo.conf" 2>/dev/null || true
chmod 600 "$DESC_DIR/odoo.conf" "$DESC_DIR/pg.env"

docker network create "$NET_TMP" >/dev/null 2>&1 && ok "rede descartavel $NET_TMP criada" \
    || { falhou "nao consegui criar a rede $NET_TMP"; resumo; }
docker run -d --rm --name "$PG_TMP" --network "$NET_TMP" --env-file "$DESC_DIR/pg.env" "$IMAGEM_PG" \
    >/dev/null 2>&1 && ok "postgres descartavel $PG_TMP no ar ($IMAGEM_PG)" \
    || { falhou "nao consegui subir o postgres descartavel $PG_TMP"; resumo; }
PRONTO=0
for _ in $(seq 1 30); do
    if docker exec "$PG_TMP" pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then PRONTO=1; break; fi
    sleep 2
done
[ "$PRONTO" = "1" ] && ok "postgres descartavel aceitando conexao" || falhou "postgres descartavel nao ficou pronto"
if [ "$FALHAS" -gt 0 ]; then resumo; fi

limpeza() {
    if [ "$MANTER" = "1" ]; then
        info "--manter: trio preservado (containers $PG_TMP/$API_CT, rede $NET_TMP, diretorio $DESC_DIR)"
        return 0
    fi
    docker rm -f "$API_CT" >/dev/null 2>&1
    docker rm -f "$PG_TMP" >/dev/null 2>&1
    [ -n "$N8N_HOME" ] && rm -rf "$N8N_HOME"
    docker network rm "$NET_TMP" >/dev/null 2>&1
    [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR"
}
trap limpeza EXIT

si()       { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -tAc "$1" 2>/dev/null; }
odoo_db()  { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -tAc "$1" 2>/dev/null; }
logs_api() { docker logs "$API_CT" 2>&1; }
odoo_ci()  { # $1=log ; restantes = args do odoo (container descartavel, --stop-after-init)
    local log="$1"; shift
    docker run --rm --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        --entrypoint odoo "$IMAGEM" "$@" --stop-after-init --log-level=info >"$log" 2>&1
    echo $?
}
massa_odoo() { # $1=estado  $2=log : roda a massa do Odoo no trio (a massa LIMPA e recria)
    docker run --rm -i --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -v "$DESC_DIR":/preparo \
        -e "TRE_MASSA_ESTADO=$1" -e "TRE_MASSA_ORGS=$ORG_A,$ORG_C" \
        --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$MASSA_ODOO" >"$2" 2>&1
    echo $?
}

cabecalho "passo 1 — schema do contrato e instalacao do modulo"
docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO_SI" >/dev/null 2>&1 \
    && ok "banco do contrato criado no descartavel ($BANCO_SI)" || falhou "nao criei $BANCO_SI"
docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -v ON_ERROR_STOP=1 -q <"$MIGRATION" >"$LOG_DIR/1-migration.log" 2>&1 \
    && ok "migration do contrato aplicada em $BANCO_SI" || falhou "migration nao aplicou (ver $LOG_DIR/1-migration.log)"
docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO" >/dev/null 2>&1 \
    && ok "banco do Odoo criado no descartavel ($BANCO)" || falhou "nao criei $BANCO"

RC="$(odoo_ci "$LOG_DIR/1-instalacao.log" -d "$BANCO" -i "$MODULO" --without-demo=all --max-cron-threads=0)"
[ "$RC" = "0" ] && ok "instalacao do modulo $MODULO terminou com exit 0" || falhou "instalacao do modulo terminou com exit $RC (ver $LOG_DIR/1-instalacao.log)"

# Este card MEXE no modulo (leitura de arquivados na porta unica): a suite do MODULO roda aqui, com
# `--test-tags` (sem isso o Odoo roda a suite do CORE inteira — medido: nao fecha em tempo util).
# Piso de testes explicito: suite que nao roda nada nao conta como prova.
RC="$(odoo_ci "$LOG_DIR/1b-suite-modulo.log" -d "$BANCO" -u "$MODULO" --test-enable --test-tags "/$MODULO" --max-cron-threads=0)"
RESUMO_TESTES="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests' "$LOG_DIR/1b-suite-modulo.log" | tail -1)"
TESTES_RODADOS="$(printf '%s' "$RESUMO_TESTES" | grep -oE 'of [0-9]+ tests' | grep -oE '[0-9]+')"
[ "$RC" = "0" ] && ok "suite do modulo $MODULO rodou com exit 0 ($RESUMO_TESTES)" || falhou "suite do modulo terminou com exit $RC (ver $LOG_DIR/1b-suite-modulo.log)"
case "$RESUMO_TESTES" in
    0" failed, 0 error(s) of "*) ok "suite do modulo fechou limpa: $RESUMO_TESTES" ;;
    *) falhou "suite do modulo NAO fechou limpa: ${RESUMO_TESTES:-sem linha de resumo} (ver $LOG_DIR/1b-suite-modulo.log)" ;;
esac
if [ -n "$TESTES_RODADOS" ] && [ "$TESTES_RODADOS" -ge 30 ]; then
    ok "suite do modulo rodou $TESTES_RODADOS testes (piso 30) — a prova nao e' vazia"
else
    falhou "suite do modulo rodou ${TESTES_RODADOS:-0} testes (piso 30): prova vazia nao conta"
fi
MODULO_ESTADO="$(limpar "$(odoo_db "select state from ir_module_module where name='$MODULO'")")"
[ "$MODULO_ESTADO" = "installed" ] && ok "modulo $MODULO esta installed no banco descartavel" \
    || falhou "modulo nao ficou installed (estado: ${MODULO_ESTADO:-?})"

cabecalho "passo 2 — servidor da porta unica e chave de API"
docker run -d --name "$API_CT" --network "$NET_TMP" \
    -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
    -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
    -v "$DESC_DIR":/preparo \
    --entrypoint odoo "$IMAGEM" -d "$BANCO" --max-cron-threads=0 --db-filter="^$BANCO\$" \
    >/dev/null 2>&1 && ok "servidor Odoo descartavel no ar ($API_CT)" || { falhou "nao subi o servidor Odoo"; resumo; }
API_PRONTA=0
for _ in $(seq 1 40); do
    if docker exec "$API_CT" python3 -c "import socket;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',8069))" 2>/dev/null; then API_PRONTA=1; break; fi
    sleep 3
done
[ "$API_PRONTA" = "1" ] && ok "porta unica ouvindo em 8069 dentro da rede do trio" || falhou "servidor Odoo nao abriu a 8069"

chown 100:101 "$DESC_DIR" 2>/dev/null || true
docker run --rm -i --network "$NET_TMP" \
    -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
    -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
    -v "$DESC_DIR":/preparo \
    -e "TRE_API_AMBIENTE=dev" \
    -e "TRE_API_ARQUIVO_CHAVE=/preparo/chave.txt" \
    --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$PREPARADOR" >"$LOG_DIR/2-preparo-api.log" 2>&1
if [ -s "$DESC_DIR/chave.txt" ] && [ "$(stat -c '%a' "$DESC_DIR/chave.txt")" = "600" ]; then
    ok "chave da API preparada em arquivo 600 (a chave nao entra em log nem em argumento)"
else
    falhou "chave da API ausente ou com modo diferente de 600 (ver $LOG_DIR/2-preparo-api.log)"
fi

cat >"$DESC_DIR/sonda.js" <<'JS'
const fs = require('fs');
const chave = fs.readFileSync('/prep/chave.txt', 'utf8').trim();
const corpo = JSON.stringify({parametros: {modelo: 'res.partner', campos: ['id'], limite: 1}});
fetch('http://' + process.env.API_HOST + ':8069/tf/api/v1/crm_registros_ler', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + chave},
  body: corpo,
}).then(async (r) => {
  console.log('SONDA_HTTP', r.status, (await r.text()).slice(0, 200));
  process.exit(r.status === 200 ? 0 : 1);
}).catch((e) => { console.log('SONDA_ERRO', e.message); process.exit(1); });
JS
RC_SONDA=1
for _ in $(seq 1 10); do
    if docker run --rm --user 100:101 --network "$NET_TMP" -v "$DESC_DIR":/prep:ro -e API_HOST="$API_CT" \
        --entrypoint node "$IMAGEM_N8N" /prep/sonda.js >>"$LOG_DIR/2-sonda-chave.log" 2>&1; then
        RC_SONDA=0; break
    fi
    sleep 3
done
[ "$RC_SONDA" = "0" ] && ok "sonda da leitura na porta unica: HTTP 200 (a credencial e o involucro valem)" \
    || falhou "sonda da leitura nao devolveu 200 (ver $LOG_DIR/2-sonda-chave.log)"

cabecalho "passo 3 — n8n descartavel (cofre + workflow importado)"
python3 - "$DESC_DIR/credenciais.json" "$CONTRATO" "$PG_TMP" "$BANCO_SI" "$PG_USER" "$SENHA" "$DESC_DIR/chave.txt" <<'PY'
import json, sys
saida, arq_contrato, host, banco, usuario, senha, arq_chave = sys.argv[1:8]
contrato = json.load(open(arq_contrato))
chave = open(arq_chave).read().strip()
postgres = contrato["credenciais"]["postgres"]
api = contrato["credenciais"]["api"]
credenciais = [
    {"id": postgres["id"], "name": postgres["nome"], "type": "postgres",
     "data": {"host": host, "port": 5432, "database": banco, "user": usuario, "password": senha,
              "ssl": "disable", "allowUnauthorizedCerts": False}},
    {"id": api["id"], "name": api["nome"], "type": "httpHeaderAuth",
     "data": {"name": "Authorization", "value": "Bearer " + chave}},
]
open(saida, "w").write(json.dumps(credenciais, ensure_ascii=False, indent=2))
print("credenciais escritas com o id/nome do contrato (valores so' no diretorio 700)")
PY
chmod 600 "$DESC_DIR/credenciais.json"
cp "$WORKFLOW" "$DESC_DIR/workflow.json"
chmod 600 "$DESC_DIR/workflow.json"

n8n_cli() {
    docker run --rm --network "$NET_TMP" -v "$N8N_HOME":/home/node/.n8n \
        -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_LOG_LEVEL=error \
        --entrypoint n8n "$IMAGEM_N8N" "$@" 2>&1
}
cp "$DESC_DIR/credenciais.json" "$N8N_HOME/credenciais.json"
cp "$DESC_DIR/workflow.json" "$N8N_HOME/workflow.json"
chown 1000:1000 "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json" 2>/dev/null || true
chmod 600 "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json"
if n8n_cli import:credentials --input=/home/node/.n8n/credenciais.json >"$LOG_DIR/3-credenciais.log"; then
    ok "credenciais (postgres + API) importadas com o id/nome do contrato"
else
    falhou "import das credenciais falhou (ver $LOG_DIR/3-credenciais.log)"
fi
if n8n_cli import:workflow --input=/home/node/.n8n/workflow.json >"$LOG_DIR/3-workflow.log"; then
    ok "workflow da reconciliacao importado no n8n descartavel"
else
    falhou "import do workflow falhou (ver $LOG_DIR/3-workflow.log)"
fi
n8n_cli export:workflow --id="$ID_WORKFLOW" --output=/home/node/.n8n/exportado.json >"$LOG_DIR/3-export.log" 2>&1
if [ -s "$N8N_HOME/exportado.json" ] && grep -q "$ID_WORKFLOW" "$N8N_HOME/exportado.json"; then
    ok "o workflow importado e' exportavel pelo id estavel $ID_WORKFLOW"
else
    falhou "workflow $ID_WORKFLOW nao esta no cofre do n8n (ver $LOG_DIR/3-export.log)"
fi
rm -f "$N8N_HOME/exportado.json" "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json"

executar_rodada() { # $1=prefixo ; define RC_RODADA
    docker run --rm --network "$NET_TMP" -v "$N8N_HOME":/home/node/.n8n \
        -e TRE_API_BASE="http://$API_CT:8069" -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false \
        -e N8N_DIAGNOSTICS_ENABLED=false -e GENERIC_TIMEZONE=UTC \
        --entrypoint n8n "$IMAGEM_N8N" execute --id="$ID_WORKFLOW" --rawOutput >"$1.out" 2>&1
    RC_RODADA=$?
}
semear_pg() { # $1=arquivo sql ; $2..=pares var=valor  (massa que nao aplica nao vira estado)
    docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -v ON_ERROR_STOP=1 -q "$@" <"$1" \
        >"$LOG_DIR/semear-$(basename "$1").log" 2>&1
}
retrato_pg() { # digest das TRES tabelas + contagens: prova de somente-leitura em execucao real
    limpar "$(si "select coalesce(md5(
        coalesce((select string_agg(x::text, '|' order by x.id) from sales_intelligence.organizations x), '')
        || '||' || coalesce((select string_agg(y::text, '|' order by y.id) from sales_intelligence.outbox_events y), '')
        || '||' || coalesce((select string_agg(z::text, '|' order by z.id) from sales_intelligence.sync_events z), '')
      ), '-')
      || '|' || (select count(*) from sales_intelligence.organizations)
      || '|' || (select count(*) from sales_intelligence.outbox_events)
      || '|' || (select count(*) from sales_intelligence.sync_events)")"
}
retrato_odoo() { limpar "$(odoo_db "select count(*) || '|' || coalesce(md5(string_agg(id::text, ',' order by id)), '-') from res_partner where tf_company_id in ('$ORG_A','$ORG_C')")"; }
valor_de() { awk -F'=' -v k="$2" '$1 == k {print substr($0, length(k) + 2); exit}' "$1"; }
confere_veredito() { # $1=prefixo $2=esperado $3=rotulo
    local obtido; obtido="$(valor_de "$1.valores" veredito)"
    [ "$obtido" = "$2" ] && ok "$3: veredito=$obtido" || falhou "$3: veredito obtido=${obtido:-ilegivel} esperado=$2"
}
confere_divergencias() { # $1=prefixo $2=lista esperada (ordenada, vazia = nenhuma) $3=rotulo
    local obtido; obtido="$(valor_de "$1.valores" divergencias)"
    [ "$obtido" = "$2" ] && ok "$3: divergencias=$2" || falhou "$3: divergencias obtidas=${obtido:-?} esperadas=$2"
}
confere_linha() { # $1=prefixo $2=trecho $3=rotulo
    local linha; linha="$(valor_de "$1.valores" linha)"
    case "$linha" in
        *"$2"*) ok "$3: a linha-resumo carrega '$2'" ;;
        *) falhou "$3: a linha-resumo nao carrega '$2' (linha: ${linha:-vazia})" ;;
    esac
}
semear_estado() { # $1=letra -> massa do Odoo (limpa e recria) + massa do PostgreSQL
    # NUNCA referenciar na mesma linha um irmao de `local`: o bash expande todas as palavras ANTES
    # de atribuir, entao `$letra` aqui resolveria o escopo de FORA (dinamico) e, com `set -u`,
    # morreria quando a funcao e' chamada do topo. Medido: `letra: unbound variable` na primeira
    # chamada direta (estado G) — os estados A..F passavam porque vinham de `rodada_do_estado`,
    # que tem um `local letra` proprio no escopo de cima.
    local letra="$1" parceiro_a="NULL" bloco=""
    bloco="$LOG_DIR/estado-$letra.sql"
    massa_odoo "$letra" "$LOG_DIR/odoo-$letra.log" >/dev/null
    parceiro_a="$(limpar "$(awk '$1 == "PARCEIRO" && $2 == "a" {print $3}' "$LOG_DIR/odoo-$letra.log")")"
    if [ "$letra" != "B" ] && [ -z "$parceiro_a" ]; then
        falhou "estado $letra: a massa do Odoo nao devolveu o id do parceiro (ver $LOG_DIR/odoo-$letra.log)"
        return 1
    fi
    local trecho="$letra"
    [ "$letra" = "G" ] && trecho="A"
    bloco="$LOG_DIR/estado-$letra.sql"
    sed -n "/^-- ESTADO $trecho\$/,/^-- FIM ESTADO $trecho\$/p" "$MASSA" | grep -v '^-- \(ESTADO\|FIM ESTADO\)' >"$bloco"
    if ! semear_pg "$bloco" -v "org_a=$ORG_A" -v "org_c=$ORG_C" -v "parceiro_a=$parceiro_a" \
            -v "parceiro_inexistente=$PARCEIRO_INEXISTENTE" -v "trilha_a=$TRILHA_A" \
            -v "linha_p2=$LINHA_P2" -v "linha_p4=$LINHA_P4" -v "evento_p1=$EVENTO_P1" \
            -v "evento_p2=$EVENTO_P2" -v "evento_p3=$EVENTO_P3" -v "evento_p4=$EVENTO_P4"; then
        falhou "estado $letra: a massa do PostgreSQL nao aplicou (ver $LOG_DIR/semear-estado-$letra.sql.log)"
        return 1
    fi
    local medido="$(limpar "$(si "select count(*) from sales_intelligence.organizations")")"
    [ "$medido" -ge 1 ] && ok "estado $letra: massa aplicada ($medido organizacao(oes) no lote, parceiro_a=$parceiro_a)" \
        || falhou "estado $letra: massa nao deixou organizacao no lote"
    return 0
}

rodada_do_estado() { # $1=letra -> semeia, mede antes, roda, mede depois, escreve <prefixo>.valores
    local letra="$1" prefixo="$LOG_DIR/estado-$1"
    local retrato_antes_pg retrato_antes_odoo
    semear_estado "$letra" || return 1
    retrato_antes_pg="$(retrato_pg)"
    retrato_antes_odoo="$(retrato_odoo)"
    executar_rodada "$prefixo"
    if [ "$RC_RODADA" != "0" ]; then
        falhou "estado $letra: o workflow terminou com exit $RC_RODADA (ver $prefixo.out)"
    fi
    python3 "$LEITOR" "$prefixo.out" >"$prefixo.valores" 2>"$prefixo.leitura.log"
    if [ "$(valor_de "$prefixo.valores" leitura)" != "ok" ]; then
        falhou "estado $letra: o resultado da rodada NAO foi legivel (ver $prefixo.out)"
    fi
    [ "$(retrato_pg)" = "$retrato_antes_pg" ] && ok "estado $letra: SOMENTE-LEITURA no PostgreSQL (digest identico antes/depois)" \
        || falhou "estado $letra: o digest das tabelas do PostgreSQL MUDOU (o job escreveu)"
    [ "$(retrato_odoo)" = "$retrato_antes_odoo" ] && ok "estado $letra: SOMENTE-LEITURA no Odoo (parceiros identicos antes/depois)" \
        || falhou "estado $letra: os parceiros do Odoo mudaram (o job escreveu)"
    return 0
}

cabecalho "estado A — espelho saudavel (a rodada nao pode alarmar)"
if rodada_do_estado A; then
    confere_veredito "$LOG_DIR/estado-A" OK "estado A"
    confere_divergencias "$LOG_DIR/estado-A" "" "estado A"
    confere_linha "$LOG_DIR/estado-A" "janela_completa, fila_completa" "estado A"
fi
cabecalho "estado B — espelho ausente no destino"
if rodada_do_estado B; then
    confere_veredito "$LOG_DIR/estado-B" DIVERGENTE "estado B"
    confere_divergencias "$LOG_DIR/estado-B" "E1" "estado B"
fi
cabecalho "estado C — ID cruzado apontando OUTRA organizacao do lote"
if rodada_do_estado C; then
    confere_veredito "$LOG_DIR/estado-C" DIVERGENTE "estado C"
    confere_divergencias "$LOG_DIR/estado-C" "I1" "estado C"
fi
cabecalho "estado D — identificador forte divergente"
if rodada_do_estado D; then
    confere_veredito "$LOG_DIR/estado-D" DIVERGENTE "estado D"
    confere_divergencias "$LOG_DIR/estado-D" "E4" "estado D"
fi
cabecalho "estado E — espelho arquivado no destino"
if rodada_do_estado E; then
    confere_veredito "$LOG_DIR/estado-E" DIVERGENTE "estado E"
    confere_divergencias "$LOG_DIR/estado-E" "E2" "estado E"
fi
cabecalho "estado F — fila x trilha (janela, entrega, teto e recusa)"
if rodada_do_estado F; then
    confere_veredito "$LOG_DIR/estado-F" DIVERGENTE "estado F"
    confere_divergencias "$LOG_DIR/estado-F" "P1,P2,P3,P4" "estado F"
fi
cabecalho "estado G — porta unica FORA DO AR (fail-closed em execucao real)"
if semear_estado G && docker stop "$API_CT" >/dev/null 2>&1; then
    RETRATO_ANTES_G="$(retrato_pg)"
    executar_rodada "$LOG_DIR/estado-G2"
    python3 "$LEITOR" "$LOG_DIR/estado-G2.out" >"$LOG_DIR/estado-G2.valores" 2>/dev/null
    confere_veredito "$LOG_DIR/estado-G2" INDETERMINADO "estado G (rodada com a porta unica parada)"
    confere_divergencias "$LOG_DIR/estado-G2" "" "estado G"
    case "$(valor_de "$LOG_DIR/estado-G2.valores" indeterminacoes)" in
        *leitura_do_destino_nao_medida*) ok "estado G: a regra que faltou medicao aparece nomeada" ;;
        *) falhou "estado G: a indeterminacao por leitura nao medida nao aparece nomeada" ;;
    esac
    [ "$(retrato_pg)" = "$RETRATO_ANTES_G" ] && ok "estado G: SOMENTE-LEITURA tambem na rodada que nao mediu" \
        || falhou "estado G: o digest das tabelas mudou (o job escreveu)"
else
    falhou "estado G: nao subi a rodada ou nao consegui parar a porta unica"
fi

cabecalho "fecho — sha256, ambiente e segredo"
sha256_dos_artefatos "$LOG_DIR/sha256-depois.txt"
if diff -q "$LOG_DIR/sha256-antes.txt" "$LOG_DIR/sha256-depois.txt" >/dev/null 2>&1; then
    ok "sha256 dos artefatos IDENTICO ao do inicio (os artefatos sob teste nao mudaram durante o aceite)"
else
    falhou "os artefatos sob teste MUDARAM durante o aceite (ver $LOG_DIR/sha256-depois.txt)"
fi
DEV_PG_DEPOIS=""
DEV_PG_DEPOIS="$(medir_bancos_do_dev)" || DEV_PG_DEPOIS=""
# Fail-closed: sem as DUAS medicoes nao se afirma "intocada". Antes desta correcao (D01) o aceite
# comparava duas pontas vazias (`[ "" = "" ]`) e imprimia OK sem ter medido nada.
if [ -z "$DEV_PG_ANTES" ] || [ -z "$DEV_PG_DEPOIS" ]; then
    falhou "instancia do dev ($DEV_PG_CT) NAO MEDIDA com o usuario $DEV_PG_USER (antes=${DEV_PG_ANTES:-vazio} depois=${DEV_PG_DEPOIS:-vazio}) — sem as duas medicoes nao se afirma INTOCADA"
elif [ "$DEV_PG_ANTES" = "$DEV_PG_DEPOIS" ]; then
    ok "instancia do dev INTOCADA (mesmos bancos antes e depois, medidos com $DEV_PG_USER): $DEV_PG_ANTES"
else
    falhou "a instancia do dev mudou: antes=$DEV_PG_ANTES depois=$DEV_PG_DEPOIS"
fi
# Segredo se mede por VALOR, nao por vocabulario: procurar a palavra "chave"/"token" acusa o proprio
# codigo do job (o comentario em pt-BR diz "chave") e o NOME da credencial, que e' declarado de
# proposito. O valor da chave desta rodada esta' no arquivo 600 do descartavel (`chave.txt`, padrao
# do E01-T01) — o aceite le de la' so' para MEDIR, e o valor nunca vai para a saida.
SEGREDO_DA_RODADA="$(cat "$DESC_DIR/chave.txt" 2>/dev/null)"
if [ -z "$SEGREDO_DA_RODADA" ]; then
    falhou "nao consegui ler a chave da rodada para medir o segredo (medicao vazia nao conta)"
elif grep -rqaF "$SEGREDO_DA_RODADA" "$N8N_HOME" "$WORKFLOW" "$LOG_DIR" 2>/dev/null; then
    falhou "o VALOR da chave de API aparece em claro no cofre do n8n, no workflow ou no log do aceite"
else
    ok "o VALOR da chave de API nao aparece em claro no cofre do n8n, no workflow nem no log do aceite"
fi
unset SEGREDO_DA_RODADA
if grep -rqiE 'bearer [A-Za-z0-9._-]{8,}' "$N8N_HOME" "$WORKFLOW" 2>/dev/null; then
    falhou "ha cabecalho Authorization com valor em claro (Bearer <valor>) em arquivo sob teste"
else
    ok "nenhum cabecalho Authorization com valor em claro (Bearer <valor>) nos arquivos sob teste"
fi
if grep -qiE '"(api_token|token|password|senha|secret|api_key)"[[:space:]]*:[[:space:]]*"[^"]{8,}"' "$WORKFLOW"; then
    falhou "o workflow versionado carrega VALOR de segredo em campo nomeado"
else
    ok "o workflow versionado nao carrega VALOR de segredo (o NOME da credencial entra por id/nome do cofre, declarado no contrato)"
fi
info "log do aceite em $LOG_DIR"

resumo
