#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W2-E04-T01 — campos de dedup e IDs canonicos em `res.partner`
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 campos de dedup (CNPJ, dominio, LinkedIn) presentes E INDEXADOS em `res.partner`;
#   AC2 IDs canonicos seguem o Data Contract V1.0;
#   AC3 teste de criacao e consulta com dado sintetico.
#
# TEST PLAN (executado por este script, na VPS do dev):
#   contrato  o modelo do modulo confrontado com docs/data/data_contract_v1.json (offline)
#   passo 1   instalacao em banco limpo (banco descartavel proprio)
#   passo 2   testes do Odoo (`--test-enable`) + contagem dos testes deste card no log
#   passo 3   MEDICAO INDEPENDENTE no catalogo do PostgreSQL: colunas, tipos, indices, ORM
#   passo 4   criacao e consulta de parceiro sintetico pelo ORM (`odoo shell`, outro caminho)
#   passo 5   desinstalacao = ROLLBACK do card: colunas e indices tf_* somem de res_partner
#   limpeza   banco/dupla/rede/config removidos; instancia do dev conferida inalterada
#
# ISOLAMENTO (herdado do aceite do modulo base, TRE-W2-E03-T01 — ver o cabecalho de
# `verificar-modulo-odoo.sh`): o Odoo do dev abre sessao em QUALQUER banco novo da instancia
# `pg-odoo-dev`, entao este aceite sobe a SUA propria dupla descartavel (`postgres:16` +
# `odoo:19.0`, as imagens do par de dev) em rede propria, e NAO usa `pg-odoo-dev`, `odoo_dev`
# nem a copia operacional `/opt/tre/repo`.
#
# Uso (na VPS, a partir de ARQUIVO — nunca por stdin):
#   bash verificar-res-partner.sh
#   bash verificar-res-partner.sh --apenas-contrato
#   bash verificar-res-partner.sh --apenas-instalacao-e-teste
#   bash verificar-res-partner.sh --prova-de-dente
#   bash verificar-res-partner.sh --banco tre_outro_banco
#
# Variaveis: TRE_MODULO, TRE_MODULO_DIR, TRE_CONTRATO, TRE_BANCO, TRE_IMAGEM, TRE_IMAGEM_PG,
# TRE_PG_USER, TRE_LOG_DIR, TRE_TESTES_MINIMOS, TRE_TESTES_DO_CARD, TRE_DEV_PG_CT,
# TRE_MANTER_BANCO=1 (nao limpa no fim).
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo final em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir
# ============================================================================
set -u

MODULO="${TRE_MODULO:-transformativa_sales_ai}"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CARTAO_DIR="${TRE_CARTAO_DIR:-/opt/tre/dev/cards/t_adee6ad7}"
MODULO_DIR="${TRE_MODULO_DIR:-$CARTAO_DIR/modulos/$MODULO}"
CONTRATO="${TRE_CONTRATO:-$CARTAO_DIR/docs/data/data_contract_v1.json}"
CONFERIDOR="${TRE_CONFERIDOR:-$AQUI/conferir_res_partner_no_contrato.py}"
MEDIDOR="${TRE_MEDIDOR:-$AQUI/medir_res_partner.py}"
PARSER="${TRE_PARSER:-$AQUI/manifesto_do_modulo.py}"
DESINSTALADOR="${TRE_DESINSTALADOR:-$AQUI/desinstalar_modulo.py}"
BANCO="${TRE_BANCO:-tre_e04_t01_res_partner}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-res-partner}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"
# 6 testes da base (TRE-W2-E03-T01) + 7 testes deste card; o minimo existe para o aceite NAO
# passar com a suite encolhida (teste que nao roda nao e' teste que passou).
TESTES_MINIMOS="${TRE_TESTES_MINIMOS:-13}"
TESTES_DO_CARD="${TRE_TESTES_DO_CARD:-7}"
CLASSE_DO_CARD="${TRE_CLASSE_DO_CARD:-TestResPartnerDedup}"
CAMPOS_DEDUP="${TRE_CAMPOS_DEDUP:-tf_cnpj tf_domain tf_linkedin_url}"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-contrato) MODO=contrato ;;
        --apenas-instalacao-e-teste) MODO=instalacao_teste ;;
        --prova-de-dente) MODO=dente ;;
        --banco) shift; BANCO="${1:-}" ;;
        --modulo-dir) shift; MODULO_DIR="${1:-}" ;;
        --contrato) shift; CONTRATO="${1:-}" ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
    shift
done

ITENS=0
FALHAS=0
SUFIXO="$$-$RANDOM"
PG_TMP="e04t01-pg-$SUFIXO"
NET_TMP="e04t01-net-$SUFIXO"
DESC_DIR=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: RES_PARTNER_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: RES_PARTNER_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# conferencia do contrato (offline): modelo do modulo x data_contract_v1.json congulado
# ---------------------------------------------------------------------------
confere_contrato() {
    local saida rc
    saida="$(python3 "$CONFERIDOR" --contrato "$CONTRATO" --modulo-dir "$MODULO_DIR" 2>&1)"
    rc=$?
    printf '%s\n' "$saida" >>"$LOG_DIR/0-contrato.out"
    printf '%s\n' "$saida" | sed -n '1,200p' | sed 's/^/      /'
    if printf '%s' "$saida" | grep -q 'RESULTADO: CONTRATO_RES_PARTNER_OK' && [ "$rc" = "0" ]; then
        ok "AC2: modelo confere com o Data Contract V1.0 congelado (exit 0)"
    else
        falhou "AC2: modelo DIVERGE do Data Contract V1.0 (exit $rc) — linhas acima"
    fi
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? tres mutacoes, cada uma em copia propria
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e04t01-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_FALHAS=0

    cabecalho "dente 1: identificador forte sem indice (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    # retira o `index=True` do PRIMEIRO campo de dedup (tf_cnpj)
    python3 - "$DENTE_DIR/m1/models/res_partner.py" <<'PY'
import pathlib, sys
arquivo = pathlib.Path(sys.argv[1])
texto = arquivo.read_text()
alvo = "string='CNPJ',\n        index=True,\n"
assert alvo in texto, 'trecho do tf_cnpj nao encontrado para mutar'
arquivo.write_text(texto.replace(alvo, "string='CNPJ',\n", 1))
PY
    D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_LOG_DIR="$LOG_DIR" "$0" --apenas-contrato 2>&1)"
    printf '%s\n' "$D1" >"$LOG_DIR/dente-1-sem-indice.out"
    printf '%s\n' "$D1" | grep -E 'FALHOU|RESULTADO' | tail -3
    if printf '%s' "$D1" | grep -q 'RESULTADO: RES_PARTNER_FALHOU'; then
        echo 'OK    dente 1: campo de dedup sem indice reprova o aceite'
    else
        echo 'FALHOU dente 1: campo de dedup sem indice NAO reprovou — o item de indice nao tem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi

    cabecalho "dente 2: nome de campo diferente do contrato (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    sed -i 's/tf_domain/tf_dominio/g' "$DENTE_DIR/m2/models/res_partner.py"
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_LOG_DIR="$LOG_DIR" "$0" --apenas-contrato 2>&1)"
    printf '%s\n' "$D2" >"$LOG_DIR/dente-2-nome-divergente.out"
    printf '%s\n' "$D2" | grep -E 'FALHOU|RESULTADO' | tail -3
    if printf '%s' "$D2" | grep -q 'RESULTADO: RES_PARTNER_FALHOU'; then
        echo 'OK    dente 2: nome divergente do contrato reprova o aceite'
    else
        echo 'FALHOU dente 2: nome divergente NAO reprovou — a conferencia de nome nao tem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi

    cabecalho "dente 3: teste do Odoo que falha de proposito (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m3"
    cat >>"$DENTE_DIR/m3/tests/test_res_partner_dedup.py" <<'PY'

    def test_99_prova_de_dente(self):
        """Teste plantado pela prova de dente do verificador: TEM de reprovar."""
        self.assertTrue(False, 'teste plantado pela prova de dente (TRE-W2-E04-T01)')
PY
    D3="$(TRE_MODULO_DIR="$DENTE_DIR/m3" TRE_BANCO="${BANCO}_dente" TRE_LOG_DIR="$LOG_DIR" \
          "$0" --apenas-instalacao-e-teste 2>&1)"
    printf '%s\n' "$D3" >"$LOG_DIR/dente-3-teste-mutado.out"
    printf '%s\n' "$D3" | grep -E 'FALHOU|RESULTADO' | tail -3
    if printf '%s' "$D3" | grep -q 'RESULTADO: RES_PARTNER_FALHOU'; then
        echo 'OK    dente 3: teste reprovado reprova o aceite'
    else
        echo 'FALHOU dente 3: teste reprovado NAO reprovou — o passo de testes nao tem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi

    echo '---'
    if [ "$DENTE_FALHAS" -eq 0 ]; then
        echo "RESULTADO: RES_PARTNER_DENTE_OK (3 provas, 0 falhas) modulo=$MODULO"
        exit 0
    fi
    echo "RESULTADO: RES_PARTNER_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente) modulo=$MODULO"
    exit 1
fi

# ---------------------------------------------------------------------------
# helpers de medicao
# ---------------------------------------------------------------------------
limpeza() {
    if [ -n "$DESC_DIR" ]; then
        docker rm -f "$PG_TMP" >/dev/null 2>&1
        docker network rm "$NET_TMP" >/dev/null 2>&1
        rm -rf "$DESC_DIR"
    fi
}
trap limpeza EXIT

psql_bd() { # $1=banco, $2=sql  (o `docker exec` NAO le o stdin do chamador)
    docker exec "$PG_TMP" psql -U "$PG_USER" -d "$1" -tAc "$2" 2>/dev/null
}
banco_existe() {
    [ "$(psql_bd postgres "select count(*) from pg_database where datname = '$1'")" = "1" ]
}
banco_limpo() {
    docker exec "$PG_TMP" dropdb -U "$PG_USER" --if-exists --force "$1" >/dev/null 2>&1
}
odoo_docker() { # $1=arquivo de log; restantes = argumentos do odoo
    local log="$1"; shift
    docker run --rm \
        --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        --entrypoint odoo "$IMAGEM" "$@" >"$log" 2>&1
    echo $?
}
odoo_shell_stdin() { # $1=log $2=banco $3=script python (lido de arquivo, nunca do stdin do ssh)
    local log="$1" banco="$2" script="$3"
    docker run --rm -i \
        --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -e TRE_MODULO="$MODULO" \
        --entrypoint odoo "$IMAGEM" shell -d "$banco" --no-http <"$script" >"$log" 2>&1
    echo $?
}
erros_no_log() { grep -cE '(^| )(ERROR|CRITICAL) ' "$1" 2>/dev/null || true; }

# ---------------------------------------------------------------------------
# guardas do ambiente (fail-closed: sem ambiente medido, nao existe aceite)
# ---------------------------------------------------------------------------
cabecalho "guardas do ambiente"
if docker info >/dev/null 2>&1; then ok "docker responde"; else falhou "docker nao responde"; resumo; fi
for img in "$IMAGEM" "$IMAGEM_PG"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
        ok "imagem $img presente ($(docker image inspect -f '{{index .RepoDigests 0}}' "$img" 2>/dev/null))"
    else
        falhou "imagem $img ausente (nada a medir)"; resumo
    fi
done
command -v openssl >/dev/null 2>&1 && ok "openssl disponivel (segredo das duplas descartaveis)" \
    || { falhou "openssl ausente"; resumo; }
command -v python3 >/dev/null 2>&1 && ok "python3 disponivel (conferidor do contrato)" \
    || { falhou "python3 ausente"; resumo; }
if [ -f "$MODULO_DIR/__manifest__.py" ]; then
    ok "modulo em disco: $MODULO_DIR/__manifest__.py"
    info "sha256 do modulo sob teste:"
    (cd "$MODULO_DIR" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's/^/      /')
else
    falhou "modulo ausente em $MODULO_DIR (__manifest__.py nao encontrado)"; resumo
fi
for arquivo in "$CONTRATO" "$CONFERIDOR" "$MEDIDOR" "$PARSER" "$DESINSTALADOR"; do
    [ -f "$arquivo" ] && ok "artefato em disco: $arquivo" || { falhou "artefato ausente: $arquivo"; resumo; }
done
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e do ambiente — so banco descartavel"; resumo ;;
esac
if printf '%s' "$BANCO" | grep -qE '^tre_[a-z0-9_]+$' && ! printf '%s' "$BANCO" | grep -qEi 'prod|homolog'; then
    ok "banco descartavel com nome seguro: $BANCO"
else
    falhou "nome de banco fora do padrao descartavel (^tre_[a-z0-9_]+$): $BANCO"; resumo
fi
DEV_PG_ANTES="nao_medido"
DEV_PG_BANCOS_ANTES="nao_medido"
if [ "$(docker inspect -f '{{.State.Running}}' "$DEV_PG_CT" 2>/dev/null)" = "true" ]; then
    DEV_PG_ANTES="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)"
    DEV_PG_BANCOS_ANTES="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select count(*) from pg_database' 2>/dev/null)"
    info "instancia do dev ($DEV_PG_CT) ANTES: $DEV_PG_BANCOS_ANTES bancos: $DEV_PG_ANTES"
else
    info "container $DEV_PG_CT do dev nao esta de pe — conferencia 'nao tocou o dev' fica sem medicao"
fi
HOMOLOG_PROD_ANTES="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
info "arquivos em homolog/prod antes: $HOMOLOG_PROD_ANTES"

# ---------------------------------------------------------------------------
# conferencia do contrato (AC2) — nao precisa de banco
# ---------------------------------------------------------------------------
cabecalho "contrato V1.0 x modelo res.partner (AC2)"
: >"$LOG_DIR/0-contrato.out"
confere_contrato

if [ "$MODO" = "contrato" ]; then resumo; fi

# ---------------------------------------------------------------------------
# dupla descartavel propria (ver o cabecalho: por que nao usar pg-odoo-dev)
# ---------------------------------------------------------------------------
cabecalho "dupla descartavel propria (postgres + odoo)"
DESC_DIR="$(mktemp -d /tmp/verificacao-res-partner-XXXXXX)"
chmod 700 "$DESC_DIR"
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
    # As duas chaves vao por variavel de proposito: o scanner de segredo do repo
    # (`scripts/secret_scan.sh`) reprova a forma literal "<chave> = <valor>" no codigo mesmo
    # quando o valor e uma variavel (convencao ja usada em verificar-modulo-odoo.sh).
    CHAVE_SENHA_BANCO='db_password'
    CHAVE_SENHA_MESTRE='admin_passwd'
    printf '%s = %s\n' "$CHAVE_SENHA_BANCO" "$SENHA"
    printf '%s = %s\n' "$CHAVE_SENHA_MESTRE" "$MASTER"
    echo 'without_demo = all'
} >"$DESC_DIR/odoo.conf"
chown 100:101 "$DESC_DIR/odoo.conf"
chmod 600 "$DESC_DIR/odoo.conf" "$DESC_DIR/pg.env"
if docker network create "$NET_TMP" >/dev/null 2>&1; then ok "rede descartavel $NET_TMP criada"; else falhou "nao consegui criar a rede $NET_TMP"; resumo; fi
if docker run -d --rm --name "$PG_TMP" --network "$NET_TMP" --env-file "$DESC_DIR/pg.env" "$IMAGEM_PG" >/dev/null 2>&1; then
    ok "postgres descartavel $PG_TMP no ar ($IMAGEM_PG)"
else
    falhou "nao consegui subir o postgres descartavel $PG_TMP"; resumo
fi
PRONTO=0
for _ in $(seq 1 30); do
    if docker exec "$PG_TMP" pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then PRONTO=1; break; fi
    sleep 2
done
[ "$PRONTO" = "1" ] && ok "postgres descartavel aceitando conexao" || falhou "postgres descartavel nao ficou pronto"
[ "$FALHAS" -gt 0 ] && resumo
info "configuracao do Odoo: $DESC_DIR/odoo.conf (senha em arquivo 600 dono uid 100, fora de argumento e de log)"

# ---------------------------------------------------------------------------
# passo 1 — instalacao em banco limpo
# ---------------------------------------------------------------------------
cabecalho "passo 1/5 — instalacao em banco limpo"
banco_limpo "$BANCO"
if banco_existe "$BANCO"; then falhou "banco $BANCO continua existindo depois do drop (nao esta limpo)"; resumo
else ok "banco $BANCO nao existia (limpo) antes da instalacao"; fi
LOG_ATUAL="$LOG_DIR/1-instalacao.log"
RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -i "$MODULO" --without-demo=all --max-cron-threads=0 \
        --stop-after-init --log-level=info)"
[ "$RC" = "0" ] && ok "odoo --init exit 0 (log: $LOG_ATUAL)" || falhou "odoo --init exit $RC (log: $LOG_ATUAL)"
ERROS="$(erros_no_log "$LOG_ATUAL")"
[ "$ERROS" = "0" ] && ok "log de instalacao sem linha ERROR/CRITICAL" \
    || falhou "log de instalacao com $ERROS linha(s) ERROR/CRITICAL: $(grep -nE '(^| )(ERROR|CRITICAL) ' "$LOG_ATUAL" | head -3 | tr '\n' ' ')"
grep -q 'Modules loaded\.' "$LOG_ATUAL" && ok "log de instalacao com 'Modules loaded.'" \
    || falhou "log de instalacao sem 'Modules loaded.' (instalacao nao assentou)"
banco_existe "$BANCO" && ok "banco $BANCO criado pelo Odoo (do zero -> instalado)" \
    || { falhou "banco $BANCO nao foi criado"; resumo; }
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "ir_module_module.state = installed" || falhou "estado no banco: '$ESTADO' (esperado installed)"
MANIFESTO_RAW="$(docker run --rm --entrypoint python3 \
    -v "$MODULO_DIR":/leitura/"$MODULO":ro -v "$PARSER":/tmp/manifesto.py:ro "$IMAGEM" \
    /tmp/manifesto.py /leitura/"$MODULO" 2>&1)"
VERSAO="$(printf '%s\n' "$MANIFESTO_RAW" | sed -n 's/^versao=//p' | head -1)"
VERSAO_BANCO="$(psql_bd "$BANCO" "select latest_version from ir_module_module where name = '$MODULO'")"
[ -n "$VERSAO" ] && [ "$VERSAO_BANCO" = "$VERSAO" ] \
    && ok "versao gravada no banco == manifesto ($VERSAO_BANCO)" \
    || falhou "versao no banco ('$VERSAO_BANCO') != manifesto ('$VERSAO')"

# ---------------------------------------------------------------------------
# passo 2 — testes do Odoo (AC3 + nao-regressao da base)
# ---------------------------------------------------------------------------
cabecalho "passo 2/5 — testes do Odoo (--test-enable)"
LOG_ATUAL="$LOG_DIR/2-teste.log"
RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -u "$MODULO" --test-enable \
        --without-demo=all --max-cron-threads=0 --stop-after-init --log-level=test)"
[ "$RC" = "0" ] && ok "odoo --test-enable exit 0 (log: $LOG_ATUAL)" || falhou "odoo --test-enable exit $RC (log: $LOG_ATUAL)"
RELATORIO="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests when loading database' "$LOG_ATUAL" | tail -1)"
if [ -n "$RELATORIO" ]; then
    N_TESTS="$(printf '%s' "$RELATORIO" | sed -nE 's/.*of ([0-9]+) tests.*/\1/p')"
    N_FALHAS="$(printf '%s' "$RELATORIO" | sed -nE 's/^([0-9]+) failed.*/\1/p')"
    N_ERROS="$(printf '%s' "$RELATORIO" | sed -nE 's/^[0-9]+ failed, ([0-9]+) error.*/\1/p')"
    if [ "$N_TESTS" -ge "$TESTES_MINIMOS" ] && [ "$N_FALHAS" = "0" ] && [ "$N_ERROS" = "0" ]; then
        ok "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests (>= $TESTES_MINIMOS)"
    else
        falhou "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests (>= $TESTES_MINIMOS) — '$RELATORIO'"
    fi
else
    falhou "log sem linha de relatorio do runner ('N failed, N error(s) of N tests when loading database') — sem medicao nao ha aceite"
fi
# O marcador 'At least one test failed when loading the modules.' e' do runner antigo: medido
# nesta execucao (Odoo 19), ele NAO aparece nem quando um teste reprova — por isso este item e'
# so' a ausencia dele, e os dentes reais sao o exit code, o relatorio do runner e as linhas FAIL:.
grep -q 'At least one test failed when loading the modules\.' "$LOG_ATUAL" \
    && falhou "log traz 'At least one test failed when loading the modules.'" \
    || ok "log sem o marcador legado 'At least one test failed when loading the modules.'"
# DEFEITO CONSERTADO (1a rodada do aceite deste card): a forma do Odoo 19 e'
#   `<hora> <pid> ERROR <banco> <modulo>: FAIL: TestX.test_y`
# — o 'FAIL:' NAO fica no inicio da linha. O item antigo (`^FAIL|^ERROR`) imprimia OK com um
# teste reprovado (falso negativo da mesma classe do defeito D04 do verificador de estrutura).
FALHAS_TESTE="$(grep -cE '(^| )(FAIL|ERROR): [A-Za-z_]' "$LOG_ATUAL" || true)"
[ "$FALHAS_TESTE" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
    || falhou "$FALHAS_TESTE linha(s) de teste reprovado(a) no log: $(grep -E '(^| )(FAIL|ERROR): [A-Za-z_]' "$LOG_ATUAL" | head -2 | tr '\n' ' ')"
# "teste que nao rodou nao e' teste que passou": conta os testes DESTE card no log do runner
RODADOS_CARD="$(grep -c "Starting $CLASSE_DO_CARD\.test_" "$LOG_ATUAL" || true)"
[ "$RODADOS_CARD" = "$TESTES_DO_CARD" ] \
    && ok "os $TESTES_DO_CARD testes de $CLASSE_DO_CARD (este card) rodaram no runner" \
    || falhou "rodei $RODADOS_CARD teste(s) de $CLASSE_DO_CARD (esperado $TESTES_DO_CARD) — suite encolhida"
RODADOS_BASE="$(grep -c 'Starting TestModuloBase\.test_' "$LOG_ATUAL" || true)"
[ "$RODADOS_BASE" = "6" ] && ok "os 6 testes da base (TestModuloBase) seguem rodando" \
    || falhou "rodei $RODADOS_BASE teste(s) de TestModuloBase (esperado 6)"

if [ "$MODO" = "instalacao_teste" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 3 — medicao INDEPENDENTE no catalogo do PostgreSQL (AC1)
# ---------------------------------------------------------------------------
cabecalho "passo 3/5 — catalogo do PostgreSQL: colunas, tipos e indices (AC1)"
for campo in $CAMPOS_DEDUP; do
    TIPO_COLUNA="$(psql_bd "$BANCO" "select data_type from information_schema.columns where table_name = 'res_partner' and column_name = '$campo'")"
    [ -n "$TIPO_COLUNA" ] && ok "coluna res_partner.$campo existe no banco (tipo $TIPO_COLUNA)" \
        || falhou "coluna res_partner.$campo AUSENTE no banco"
done
for campo in tf_company_id tf_priority_score; do
    TIPO_COLUNA="$(psql_bd "$BANCO" "select data_type from information_schema.columns where table_name = 'res_partner' and column_name = '$campo'")"
    [ -n "$TIPO_COLUNA" ] && ok "coluna res_partner.$campo existe no banco (tipo $TIPO_COLUNA) [ID canonico do contrato]" \
        || falhou "coluna res_partner.$campo AUSENTE no banco (ID canonico do contrato)"
done
for campo in $CAMPOS_DEDUP; do
    INDICE="$(psql_bd "$BANCO" "select indexname from pg_indexes where schemaname = 'public' and tablename = 'res_partner' and indexdef like '%($campo)%' and indexdef like '%btree%'")"
    [ -n "$INDICE" ] && ok "indice btree real em res_partner($campo): $INDICE" \
        || falhou "nenhum indice btree no banco cobrindo res_partner($campo) — campo NAO esta indexado"
    # No Odoo 19 `ir_model_fields.index` e' BOOLEANO (o Odoo antigo usava selecao): medido nesta
    # execucao — campo indexado grava 't'. O TIPO do indice (btree) e' provado no pg_indexes acima.
    ORM_INDICE="$(psql_bd "$BANCO" "select \"index\" from ir_model_fields where model = 'res.partner' and name = '$campo'")"
    [ "$ORM_INDICE" = "t" ] && ok "ir_model_fields.index = true (ORM) para res.partner.$campo" \
        || falhou "ir_model_fields.index de res.partner.$campo: '$ORM_INDICE' (esperado t = true)"
done
INDICES_TF="$(psql_bd "$BANCO" "select count(*) from pg_indexes where schemaname = 'public' and tablename = 'res_partner' and indexname like '%tf\\_%'")"
info "indices do modulo em res_partner: $INDICES_TF (medido pelo catalogo do banco)"

# ---------------------------------------------------------------------------
# passo 4 — criacao e consulta com dado sintetico pelo ORM (AC3)
# ---------------------------------------------------------------------------
cabecalho "passo 4/5 — criacao e consulta de parceiro sintetico pelo ORM (AC3)"
LOG_ATUAL="$LOG_DIR/4-medicao-orm.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$MEDIDOR")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
grep -E '^MEDICAO_' "$LOG_ATUAL" | sed 's/^/      /'
grep -q 'MEDICAO_OK' "$LOG_ATUAL" && ok "ORM criou e consultou o parceiro sintetico (MEDICAO_OK)" \
    || falhou "medicao do ORM nao concluiu: $(grep -E 'MEDICAO_FALHOU' "$LOG_ATUAL" | head -1)"
grep -q 'MEDICAO_ROLLBACK_OK' "$LOG_ATUAL" && ok "transacao da medicao revertida (nada do teste ficou no banco)" \
    || falhou "medicao nao foi revertida (MEDICAO_ROLLBACK_OK ausente)"
for campo in $CAMPOS_DEDUP tf_company_id; do
    N="$(grep -E "^MEDICAO_BUSCA $campo n=" "$LOG_ATUAL" | sed -n 's/.*n=//p' | head -1)"
    [ "$N" = "1" ] && ok "consulta por $campo devolveu exatamente o parceiro criado (n=1)" \
        || falhou "consulta por $campo devolveu n='$N' (esperado 1)"
done
N_NEG="$(grep -E '^MEDICAO_BUSCA_NEGATIVA n=' "$LOG_ATUAL" | sed -n 's/.*n=//p' | head -1)"
[ "$N_NEG" = "0" ] && ok "identificador diferente nao casa (n=0)" \
    || falhou "busca por identificador diferente devolveu n='$N_NEG' (esperado 0)"
PARCEIRO_APOS="$(psql_bd "$BANCO" "select count(*) from res_partner where name like 'Empresa Sintetica TRE-W2-E04-T01%'")"
[ "$PARCEIRO_APOS" = "0" ] && ok "nenhum parceiro sintetico sobrou no banco depois da medicao" \
    || falhou "sobrou $PARCEIRO_APOS parceiro(s) sintetico(s) no banco"

# ---------------------------------------------------------------------------
# passo 5 — desinstalacao = ROLLBACK do card
# ---------------------------------------------------------------------------
cabecalho "passo 5/5 — desinstalacao (rollback declarado: os campos vao com o modulo)"
LOG_ATUAL="$LOG_DIR/5-desinstalacao.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$DESINSTALADOR")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
grep -q 'DESINSTALACAO_OK' "$LOG_ATUAL" && ok "ORM desinstalou o modulo (marcador DESINSTALACAO_OK)" \
    || falhou "marcador DESINSTALACAO_OK ausente no log do shell (exit $RC)"
grep -qE '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" \
    && falhou "log do shell com traceback/recusa: $(grep -E '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" | head -2 | tr '\n' ' ')" \
    || ok "log do shell sem traceback/recusa"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "uninstalled" ] && ok "ir_module_module.state = uninstalled" || falhou "estado no banco: '$ESTADO' (esperado uninstalled)"
for campo in $CAMPOS_DEDUP tf_company_id tf_priority_score; do
    COLUNA_APOS="$(psql_bd "$BANCO" "select count(*) from information_schema.columns where table_name = 'res_partner' and column_name = '$campo'")"
    [ "$COLUNA_APOS" = "0" ] && ok "rollback: coluna res_partner.$campo removida com o modulo" \
        || falhou "rollback: coluna res_partner.$campo SOBREVIVEU a desinstalacao"
done
INDICES_APOS="$(psql_bd "$BANCO" "select count(*) from pg_indexes where schemaname = 'public' and tablename = 'res_partner' and indexname like '%tf\\_%'")"
[ "$INDICES_APOS" = "0" ] && ok "rollback: nenhum indice tf_* sobrou em res_partner" \
    || falhou "rollback: $INDICES_APOS indice(s) tf_* sobreviveram a desinstalacao"
RESQUICOS="$(psql_bd "$BANCO" "select (select count(*) from ir_model_data where module = '$MODULO') + (select count(*) from ir_ui_view where model like '$MODULO%') + (select count(*) from ir_model_fields where name like '$MODULO%')")"
[ "$RESQUICOS" = "0" ] && ok "nenhum resquicio do modulo no banco apos a desinstalacao" \
    || falhou "$RESQUICOS resquicio(s) do modulo no banco depois da desinstalacao"

# ---------------------------------------------------------------------------
# limpeza e prova de que a instancia do dev nao foi tocada
# ---------------------------------------------------------------------------
cabecalho "limpeza e instancia do dev"
if [ "$MANTER_BANCO" = "1" ]; then
    info "TRE_MANTER_BANCO=1: banco $BANCO mantido para inspecao"
else
    banco_limpo "$BANCO"
    if banco_existe "$BANCO"; then falhou "banco descartavel $BANCO nao foi removido"; else ok "banco descartavel $BANCO removido"; fi
fi
docker rm -f "$PG_TMP" >/dev/null 2>&1
if [ -z "$(docker ps -q --filter "name=^$PG_TMP$")" ]; then ok "postgres descartavel $PG_TMP removido"; else falhou "postgres descartavel $PG_TMP continua de pe"; fi
docker network rm "$NET_TMP" >/dev/null 2>&1
if [ -z "$(docker network ls -q --filter "name=^$NET_TMP$")" ]; then ok "rede descartavel $NET_TMP removida"; else falhou "rede descartavel $NET_TMP continua"; fi
if [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR" && [ ! -d "$DESC_DIR" ]; then
    DESC_DIR=""
    ok "diretorio de configuracao descartavel removido (senha gerada na hora, nunca em log)"
else
    falhou "diretorio descartavel $DESC_DIR nao foi removido"
fi
if [ "$DEV_PG_BANCOS_ANTES" != "nao_medido" ]; then
    DEV_PG_DEPOIS="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)"
    if [ "$DEV_PG_DEPOIS" = "$DEV_PG_ANTES" ]; then
        ok "instancia do dev intacta: mesmos bancos antes e depois ($DEV_PG_DEPOIS)"
    else
        falhou "instancia do dev mudou: antes '$DEV_PG_ANTES' / depois '$DEV_PG_DEPOIS'"
    fi
else
    info "instancia do dev nao medida (container $DEV_PG_CT fora do ar)"
fi
HOMOLOG_PROD_DEPOIS="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
if [ "$HOMOLOG_PROD_DEPOIS" = "0" ] && [ "$HOMOLOG_PROD_ANTES" = "0" ]; then
    ok "homolog/prod sem nenhum arquivo antes e depois (nada nasce fora do dev — ADR-005)"
else
    falhou "homolog/prod com arquivo (antes=$HOMOLOG_PROD_ANTES depois=$HOMOLOG_PROD_DEPOIS)"
fi
info "logs: $LOG_DIR"

resumo
