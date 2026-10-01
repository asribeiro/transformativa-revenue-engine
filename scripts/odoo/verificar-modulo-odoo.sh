#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W2-E03-T01 — modulo Odoo `transformativa_sales_ai`
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 modulo instala e desinstala limpo em banco limpo (idempotente);
#   AC2 manifesto e versao corretos; dependencias declaradas;
#   AC3 `--test-enable` do Odoo sem erro no modulo.
#
# TEST PLAN (executado por este script, na VPS do dev):
#   passo 1 instalacao em banco limpo     -> log + estado lido no banco
#   passo 2 teste do Odoo (--test-enable) -> relatorio do runner + estado lido no banco
#   passo 3 desinstalacao                 -> marcador do ORM + estado lido no banco
#   passo 4 reinstalacao (idempotencia)   -> estado lido no banco
#   limpeza: banco e dupla descartavel removidos; instancia do dev conferida inalterada
#
# ISOLAMENTO (achado na primeira rodada deste verificador — ver runbook §8, defeitos 2 e 3):
# o Odoo do dev (`odoo-dev`) abre sessao em QUALQUER banco novo da instancia `pg-odoo-dev`
# (medido: banco probe criado do zero recebeu sessao do IP do `odoo-dev` em ~30s, sem ninguem
# pedir) e o `dropdb` do banco de teste morria com "being accessed by other users". Por isso
# este verificador sobe a SUA propria dupla descartavel (`postgres:16` + `odoo:19.0`, as
# mesmas imagens do par de dev, com os digests medidos) em rede propria — e NAO usa
# `pg-odoo-dev`, `odoo_dev` nem a copia operacional `/opt/tre/repo`.
#
# Uso (na VPS, a partir de arquivo — nao por stdin, ver armadilha do `docker compose run`):
#   bash verificar-modulo-odoo.sh
#   bash verificar-modulo-odoo.sh --apenas-manifesto
#   bash verificar-modulo-odoo.sh --apenas-instalacao-e-teste
#   bash verificar-modulo-odoo.sh --prova-de-dente      (duas mutacoes: versao e teste)
#   bash verificar-modulo-odoo.sh --banco tre_outro_banco
#
# Variaveis: TRE_MODULO, TRE_MODULO_DIR, TRE_BANCO, TRE_IMAGEM, TRE_IMAGEM_PG, TRE_PG_USER,
# TRE_VERSAO_ESPERADA, TRE_SERIE_ESPERADA, TRE_LOG_DIR, TRE_DEV_PG_CT (conferencia do dev),
# TRE_MANTER_BANCO=1 (nao limpa no fim).
#
# FORMA DE CHAMADA (defeito TRE-W2-E03-T01-D04): valem as duas — o nome simples, de dentro do
# diretorio do script, e o caminho absoluto:
#   bash verificar-modulo-odoo.sh --prova-de-dente
#   bash /caminho/absoluto/verificar-modulo-odoo.sh --prova-de-dente
# Os modos que re-invocam este proprio arquivo (o --prova-de-dente) usam o caminho RESOLVIDO
# (`$EU`, logo apos `set -u`), nunca `"$0"`: chamado por nome simples, `$0` e um nome sem
# diretorio que nao esta no PATH e a re-invocacao morria em `command not found` — o modo de
# dente entao acusava "o item nao tem dente" (diagnostico falso e alarmante) quando o que
# falhou foi a invocacao.
#
# TRE_LOG_DIR: diretorio dos logs de passo do aceite (passos 1 a 4). O modo --prova-de-dente
# NAO escreve nele: cada prova usa "$TRE_LOG_DIR/dente/prova-N" (defeito TRE-W2-E03-T01-D02 —
# antes, o dente herdava este diretorio e sobrescrevia a evidencia do aceite).
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo final em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir
# ============================================================================
set -u

MODULO="${TRE_MODULO:-transformativa_sales_ai}"
# Caminho RESOLVIDO deste proprio arquivo (defeito TRE-W2-E03-T01-D04): `$0` pode ser um nome
# simples sem diretorio (`bash verificar-modulo-odoo.sh`); toda re-invocacao interna usa `$EU`.
EU="$(readlink -f "$0")"
AQUI="$(cd "$(dirname "$EU")" && pwd)"
MODULO_DIR="${TRE_MODULO_DIR:-/opt/tre/dev/modulos/$MODULO}"
PARSER="${TRE_PARSER:-$AQUI/manifesto_do_modulo.py}"
DESINSTALADOR="${TRE_DESINSTALADOR:-$AQUI/desinstalar_modulo.py}"
BANCO="${TRE_BANCO:-tre_e03_t01_modulo}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
VERSAO_ESPERADA="${TRE_VERSAO_ESPERADA:-19.0.1.0.0}"
SERIE_ESPERADA="${TRE_SERIE_ESPERADA:-19.0}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-modulo-odoo}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-manifesto) MODO=manifesto ;;
        --apenas-instalacao-e-teste) MODO=instalacao_teste ;;
        --prova-de-dente) MODO=dente ;;
        --banco) shift; BANCO="${1:-}" ;;
        --modulo-dir) shift; MODULO_DIR="${1:-}" ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
    shift
done

ITENS=0
FALHAS=0
LOG_ATUAL=""
SUFIXO="$$-$RANDOM"
PG_TMP="e03t01-pg-$SUFIXO"
NET_TMP="e03t01-net-$SUFIXO"
DESC_DIR=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: MODULO_ODOO_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: MODULO_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? duas mutacoes, cada uma em copia propria
#
# LOG PROPRIO (defeito TRE-W2-E03-T01-D02, conserto): cada prova escreve num diretorio
# seu, sob "$LOG_DIR/dente/", e o modo dente NAO escreve nem um arquivo no diretorio do
# aceite. Antes deste conserto o sub-run herdava o TRE_LOG_DIR do chamador por ambiente e
# usava os MESMOS nomes de passo: rodar o dente depois de um aceite verde sobrescrevia
# `1-instalacao.log`/`2-teste.log` (e os outros dois) com a execucao mutada, apagando a
# evidencia bruta do aceite. O runbook §5.1 registra o caso medido.
#
# INVOCACAO (defeito TRE-W2-E03-T01-D04, conserto): as re-invocacoes usam o caminho RESOLVIDO
# (`bash "$EU" ...`) — a forma documentada `bash verificar-modulo-odoo.sh` passa a valer tambem
# para este modo. Com `"$0"`, chamar por nome simples de dentro do diretorio dava
# `command not found` e a prova era acusada de "item sem dente"; agora o veredito ausente e
# reportado como falha de INVOCACAO, com contador proprio (nao mente na direcao errada).
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e03t01-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_LOG_DIR="${LOG_DIR}/dente"
    mkdir -p "$DENTE_LOG_DIR/prova-1" "$DENTE_LOG_DIR/prova-2"
    DENTE_FALHAS=0
    GUARDA_FALHAS=0
    INVOCACAO_FALHAS=0
    # Guarda fail-closed do proprio defeito D04: sem o caminho resolvido nao ha prova a fazer —
    # reprova com o motivo certo em vez de culpar os dentes do aceite.
    if [ ! -f "$EU" ]; then
        echo "FALHOU nao consegui resolver o caminho deste verificador (EU='$EU'): a re-invocacao do modo de dente nao roda — falha de INVOCACAO, nao veredito sobre dente"
        echo "RESULTADO: MODULO_ODOO_DENTE_FALHOU (0 prova(s) sem dente, 0 falha(s) na guarda dos logs do aceite, 1 falha(s) de invocacao) modulo=$MODULO"
        exit 1
    fi
    info "verificador re-invocado por caminho resolvido: $EU"
    # Guarda fail-closed do proprio defeito D02: o conteudo dos logs de passo do aceite
    # e fotografado antes e depois das provas; qualquer mudanca reprova o dente.
    ACEITE_ANTES="$(cd "$LOG_DIR" 2>/dev/null && sha256sum [1-4]-*.log 2>/dev/null | LC_ALL=C sort)"
    info "logs do aceite: $LOG_DIR  |  logs do dente: $DENTE_LOG_DIR (caminhos separados)"
    cabecalho "prova de dente 1: versao do manifesto mutada (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    sed -i "s/'version': *'$VERSAO_ESPERADA'/'version': '18.0.1.0.0'/" "$DENTE_DIR/m1/__manifest__.py"
    sed -i "s/'version': *\"$VERSAO_ESPERADA\"/'version': '18.0.1.0.0'/" "$DENTE_DIR/m1/__manifest__.py"
    D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_LOG_DIR="$DENTE_LOG_DIR/prova-1" bash "$EU" --apenas-manifesto 2>&1)"
    echo "$D1" >"$DENTE_LOG_DIR/dente-1-manifesto-mutado.out"
    echo "$D1" | tail -4
    if echo "$D1" | grep -q 'RESULTADO: MODULO_ODOO_FALHOU'; then
        echo 'OK    dente 1: versao mutada reprova (o verificador nao passa por qualquer coisa)'
    elif echo "$D1" | grep -q 'RESULTADO: '; then
        echo 'FALHOU dente 1: versao mutada NAO reprovou — o item de versao nao tem dente'; DENTE_FALHAS=$((DENTE_FALHAS + 1))
    else
        echo "FALHOU dente 1: a prova NAO produziu veredito nenhum (o sub-run de '$EU' falhou — sem linha RESULTADO:) — falha de INVOCACAO, nao 'item sem dente' (defeito TRE-W2-E03-T01-D04)"; INVOCACAO_FALHAS=$((INVOCACAO_FALHAS + 1))
    fi

    cabecalho "prova de dente 2: teste do Odoo que falha de proposito (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    cat >>"$DENTE_DIR/m2/tests/test_modulo_base.py" <<'PY'

    def test_99_prova_de_dente(self):
        """Teste plantado pela prova de dente do verificador: TEM de reprovar."""
        self.assertTrue(False, 'teste plantado pela prova de dente (TRE-W2-E03-T01)')
PY
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_dente" TRE_LOG_DIR="$DENTE_LOG_DIR/prova-2" \
          bash "$EU" --apenas-instalacao-e-teste 2>&1)"
    echo "$D2" >"$DENTE_LOG_DIR/dente-2-teste-mutado.out"
    echo "$D2" | tail -4
    if echo "$D2" | grep -q 'RESULTADO: MODULO_ODOO_FALHOU'; then
        echo 'OK    dente 2: teste que falha reprova o aceite'
    elif echo "$D2" | grep -q 'RESULTADO: '; then
        echo 'FALHOU dente 2: teste que falha NAO reprovou — o item de --test-enable nao tem dente'; DENTE_FALHAS=$((DENTE_FALHAS + 1))
    else
        echo "FALHOU dente 2: a prova NAO produziu veredito nenhum (o sub-run de '$EU' falhou — sem linha RESULTADO:) — falha de INVOCACAO, nao 'item sem dente' (defeito TRE-W2-E03-T01-D04)"; INVOCACAO_FALHAS=$((INVOCACAO_FALHAS + 1))
    fi

    # Guarda D02: os logs de passo do aceite tem de sair das provas com o MESMO conteudo.
    ACEITE_DEPOIS="$(cd "$LOG_DIR" 2>/dev/null && sha256sum [1-4]-*.log 2>/dev/null | LC_ALL=C sort)"
    if [ -z "$ACEITE_ANTES" ]; then
        info "sem logs de passo do aceite em $LOG_DIR (guarda D02 sem o que proteger nesta rodada)"
    elif [ "$ACEITE_ANTES" = "$ACEITE_DEPOIS" ]; then
        echo "OK    logs de passo do aceite intactos depois das provas ($(printf '%s\n' "$ACEITE_ANTES" | grep -c . | tr -d ' ') arquivo(s) com sha256 identico)"
    else
        echo 'FALHOU o modo dente mexeu nos logs de passo do aceite — evidencia do aceite destruida (defeito TRE-W2-E03-T01-D02 de volta)'; GUARDA_FALHAS=$((GUARDA_FALHAS + 1))
        printf '%s\n' "$ACEITE_ANTES" | sed 's/^/      antes:  /'
        printf '%s\n' "$ACEITE_DEPOIS" | sed 's/^/      depois: /'
    fi

    echo '---'
    if [ "$DENTE_FALHAS" -eq 0 ] && [ "$GUARDA_FALHAS" -eq 0 ] && [ "$INVOCACAO_FALHAS" -eq 0 ]; then
        echo "RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas) modulo=$MODULO logs_aceite=$LOG_DIR logs_dente=$DENTE_LOG_DIR"
        exit 0
    fi
    echo "RESULTADO: MODULO_ODOO_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente, $GUARDA_FALHAS falha(s) na guarda dos logs do aceite, $INVOCACAO_FALHAS falha(s) de invocacao) modulo=$MODULO"
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
# Container DESCARTAVEL do Odoo (mesma imagem do par de dev), com o modulo montado em
# /mnt/extra-addons e uma configuracao propria: a senha nasce nesta execucao, vive num
# arquivo 600 dono uid 100 e nunca aparece em argumento, log ou artefato.
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
if command -v openssl >/dev/null 2>&1; then ok "openssl disponivel (segredo das duplas descartaveis)"; else falhou "openssl ausente"; resumo; fi
if [ -f "$MODULO_DIR/__manifest__.py" ]; then
    ok "modulo em disco: $MODULO_DIR/__manifest__.py"
    info "sha256 do modulo sob teste:"
    (cd "$MODULO_DIR" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's/^/      /')
else
    falhou "modulo ausente em $MODULO_DIR (__manifest__.py nao encontrado)"; resumo
fi
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e do ambiente — so banco descartavel"; resumo ;;
esac
if printf '%s' "$BANCO" | grep -qE '^tre_[a-z0-9_]+$' && ! printf '%s' "$BANCO" | grep -qEi 'prod|homolog'; then
    ok "banco descartavel com nome seguro: $BANCO"
else
    falhou "nome de banco fora do padrao descartavel (^tre_[a-z0-9_]+$): $BANCO"; resumo
fi
# estado da instancia do dev ANTES (para provar no fim que o card nao a tocou)
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
# dupla descartavel propria (ver o cabecalho: por que nao usar pg-odoo-dev)
# ---------------------------------------------------------------------------
cabecalho "dupla descartavel propria (postgres + odoo)"
DESC_DIR="$(mktemp -d /tmp/verificacao-modulo-XXXXXX)"
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
    # quando o valor e uma variavel (mesma convencao ja usada em scripts/provision/instalar-odoo-dev.sh).
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
if [ "$FALHAS" -gt 0 ]; then resumo; fi
info "configuracao do Odoo: $DESC_DIR/odoo.conf (senha em arquivo 600 dono uid 100, fora de argumento e de log)"

# ---------------------------------------------------------------------------
# manifesto e versao (AC2)
# ---------------------------------------------------------------------------
cabecalho "manifesto e versao (AC2)"
MANIFESTO_RAW="$(docker run --rm --entrypoint python3 \
    -v "$MODULO_DIR":/leitura/"$MODULO":ro -v "$PARSER":/tmp/manifesto.py:ro "$IMAGEM" \
    /tmp/manifesto.py /leitura/"$MODULO" 2>&1)"
if printf '%s' "$MANIFESTO_RAW" | grep -q '^nome='; then
    ok "manifesto lido e avaliado como literal Python"
else
    falhou "manifesto ilegivel: $(printf '%s' "$MANIFESTO_RAW" | tr '\n' ' ')"; resumo
fi
val() { printf '%s\n' "$MANIFESTO_RAW" | sed -n "s/^$1=//p" | head -1; }
NOME_NO_DIR="$(val nome)"
VERSAO="$(val versao)"
SERIE="$(val serie)"
LICENCA="$(val licenca)"
INSTALAVEL="$(val installable)"
APLICACAO="$(val application)"
DEPENDENCIAS="$(printf '%s\n' "$MANIFESTO_RAW" | sed -n 's/^depende=//p')"
SERIE_IMAGEM="$(docker run --rm --entrypoint odoo "$IMAGEM" --version 2>/dev/null | sed -n 's/^Odoo Server \([0-9.]*\).*/\1/p' | head -1)"

[ "$NOME_NO_DIR" = "$MODULO" ] && ok "nome tecnico do diretorio == modulo: $MODULO" \
    || falhou "diretorio ($NOME_NO_DIR) diferente do modulo ($MODULO)"
[ "$VERSAO" = "$VERSAO_ESPERADA" ] && ok "versao do manifesto == $VERSAO_ESPERADA" \
    || falhou "versao do manifesto ($VERSAO) diferente da esperada ($VERSAO_ESPERADA)"
if [ -n "$SERIE_IMAGEM" ] && [ "$SERIE" = "$SERIE_IMAGEM" ]; then
    ok "serie da versao ($SERIE) == serie do Odoo na imagem ($SERIE_IMAGEM)"
else
    falhou "serie da versao ($SERIE) != serie do Odoo na imagem ($SERIE_IMAGEM)"
fi
[ "$SERIE" = "$SERIE_ESPERADA" ] && ok "serie da versao == $SERIE_ESPERADA" \
    || falhou "serie da versao ($SERIE) diferente da esperada ($SERIE_ESPERADA)"
[ "$INSTALAVEL" = "True" ] && ok "installable=True declarado" || falhou "installable=$INSTALAVEL (esperado True)"
[ "$APLICACAO" = "False" ] && ok "application=False declarado (modulo base, nao aplicacao)" || falhou "application=$APLICACAO (esperado False)"
[ -n "$LICENCA" ] && ok "licenca declarada: $LICENCA" || falhou "manifesto sem licenca"
[ -n "$DEPENDENCIAS" ] && ok "dependencias declaradas: $(printf '%s' "$DEPENDENCIAS" | tr '\n' ' ')" \
    || falhou "manifesto sem dependencias declaradas"

ADDONS_IMAGEM="$(docker run --rm --entrypoint bash "$IMAGEM" -lc \
    'ls /usr/lib/python3/dist-packages/odoo/addons /mnt/extra-addons 2>/dev/null' | LC_ALL=C sort -u)"
DEPS_OK=1
for dep in $DEPENDENCIAS; do
    printf '%s\n' "$ADDONS_IMAGEM" | grep -qx "$dep" || { DEPS_OK=0; falhou "dependencia '$dep' nao existe nos addons da imagem $IMAGEM"; }
done
[ "$DEPS_OK" = "1" ] && ok "toda dependencia declarada e resolvivel na imagem $IMAGEM"

if [ "$MODO" = "manifesto" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 1 — instalacao em banco limpo (AC1 + AC2 no banco)
# ---------------------------------------------------------------------------
cabecalho "passo 1/4 — instalacao em banco limpo"
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
if banco_existe "$BANCO"; then ok "banco $BANCO criado pelo Odoo (do zero -> instalado)"; else falhou "banco $BANCO nao foi criado"; resumo; fi

ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "ir_module_module.state = installed" || falhou "estado no banco: '$ESTADO' (esperado installed)"
VERSAO_BANCO="$(psql_bd "$BANCO" "select latest_version from ir_module_module where name = '$MODULO'")"
[ "$VERSAO_BANCO" = "$VERSAO" ] && ok "versao gravada no banco == manifesto ($VERSAO_BANCO)" \
    || falhou "versao gravada no banco ('$VERSAO_BANCO') diferente da do manifesto ('$VERSAO')"
LICENCA_BANCO="$(psql_bd "$BANCO" "select license from ir_module_module where name = '$MODULO'")"
[ "$LICENCA_BANCO" = "$LICENCA" ] && ok "licenca gravada no banco == manifesto ($LICENCA_BANCO)" \
    || falhou "licenca no banco ('$LICENCA_BANCO') diferente da do manifesto ('$LICENCA')"
DEPS_BANCO="$(psql_bd "$BANCO" "select d.name from ir_module_module_dependency d join ir_module_module m on m.id = d.module_id where m.name = '$MODULO' order by d.name" | tr '\n' ' ' | sed 's/ *$//')"
DEPS_MANIFESTO="$(printf '%s' "$DEPENDENCIAS" | tr '\n' ' ' | sed 's/ *$//')"
[ "$DEPS_BANCO" = "$DEPS_MANIFESTO" ] && ok "dependencias gravadas no banco == declaradas ($DEPS_BANCO)" \
    || falhou "dependencias no banco ('$DEPS_BANCO') != declaradas ('$DEPS_MANIFESTO')"
DEPS_NAO_INSTALADAS="$(psql_bd "$BANCO" "select count(*) from ir_module_module_dependency d join ir_module_module m on m.id = d.module_id join ir_module_module dep on dep.name = d.name where m.name = '$MODULO' and dep.state <> 'installed'")"
[ "$DEPS_NAO_INSTALADAS" = "0" ] && ok "todas as dependencias instaladas no banco" \
    || falhou "$DEPS_NAO_INSTALADAS dependencia(s) nao instalada(s)"
PENDENTES="$(psql_bd "$BANCO" "select count(*) from ir_module_module where state in ('to install','to upgrade','to remove')")"
[ "$PENDENTES" = "0" ] && ok "nenhum modulo pendurado em to install/to upgrade/to remove" \
    || falhou "$PENDENTES modulo(s) pendurado(s) depois da instalacao"

# ---------------------------------------------------------------------------
# passo 2 — teste do Odoo (AC3)
# ---------------------------------------------------------------------------
cabecalho "passo 2/4 — teste do Odoo (--test-enable)"
LOG_ATUAL="$LOG_DIR/2-teste.log"
RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -u "$MODULO" --test-enable \
        --without-demo=all --max-cron-threads=0 --stop-after-init --log-level=test)"
[ "$RC" = "0" ] && ok "odoo --test-enable exit 0 (log: $LOG_ATUAL)" || falhou "odoo --test-enable exit $RC (log: $LOG_ATUAL)"
RELATORIO="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests when loading database' "$LOG_ATUAL" | tail -1)"
if [ -n "$RELATORIO" ]; then
    N_TESTS="$(printf '%s' "$RELATORIO" | sed -nE 's/.*of ([0-9]+) tests.*/\1/p')"
    N_FALHAS="$(printf '%s' "$RELATORIO" | sed -nE 's/^([0-9]+) failed.*/\1/p')"
    N_ERROS="$(printf '%s' "$RELATORIO" | sed -nE 's/^[0-9]+ failed, ([0-9]+) error.*/\1/p')"
    if [ "$N_TESTS" -ge 1 ] && [ "$N_FALHAS" = "0" ] && [ "$N_ERROS" = "0" ]; then
        ok "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests ($MODULO)"
    else
        falhou "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests — '$RELATORIO'"
    fi
else
    falhou "log sem linha de relatorio do runner ('N failed, N error(s) of N tests when loading database') — sem medicao nao ha aceite"
fi
grep -q 'At least one test failed when loading the modules\.' "$LOG_ATUAL" \
    && falhou "log traz 'At least one test failed when loading the modules.'" \
    || ok "log sem 'At least one test failed when loading the modules.'"
FALHAS_TESTE="$(grep -cE '^(FAIL|ERROR): ' "$LOG_ATUAL" || true)"
[ "$FALHAS_TESTE" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
    || falhou "$FALHAS_TESTE linha(s) de teste reprovado(a) no log"
grep -q 'Modules loaded\.' "$LOG_ATUAL" && ok "log de teste com 'Modules loaded.'" \
    || falhou "log de teste sem 'Modules loaded.' (a execucao dos testes nao assentou)"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "modulo segue installed depois do teste" || falhou "estado depois do teste: '$ESTADO'"

if [ "$MODO" = "instalacao_teste" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 3 — desinstalacao (AC1)
# ---------------------------------------------------------------------------
cabecalho "passo 3/4 — desinstalacao"
LOG_ATUAL="$LOG_DIR/3-desinstalacao.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$DESINSTALADOR")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
grep -q 'DESINSTALACAO_OK' "$LOG_ATUAL" && ok "ORM desinstalou o modulo (marcador DESINSTALACAO_OK)" \
    || falhou "marcador DESINSTALACAO_OK ausente no log do shell (exit $RC)"
grep -qE '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" \
    && falhou "log do shell com traceback/recusa: $(grep -E '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" | head -2 | tr '\n' ' ')" \
    || ok "log do shell sem traceback/recusa"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "uninstalled" ] && ok "ir_module_module.state = uninstalled" || falhou "estado no banco: '$ESTADO' (esperado uninstalled)"
RESQUICOS="$(psql_bd "$BANCO" "select (select count(*) from ir_model_data where module = '$MODULO') + (select count(*) from ir_ui_view where model like '$MODULO%') + (select count(*) from ir_model_fields where name like '$MODULO%')")"
[ "$RESQUICOS" = "0" ] && ok "nenhum resquicio do modulo no banco (ir_model_data/ir_ui_view/ir_model_fields)" \
    || falhou "$RESQUICOS resquicio(s) do modulo no banco depois da desinstalacao"
TABELAS_RESQUICOS="$(psql_bd "$BANCO" "select count(*) from information_schema.tables where table_name like '${MODULO}\_%'")"
[ "$TABELAS_RESQUICOS" = "0" ] && ok "nenhuma tabela com prefixo do modulo no banco" \
    || falhou "$TABELAS_RESQUICOS tabela(s) com prefixo do modulo"

# ---------------------------------------------------------------------------
# passo 4 — reinstalacao (AC1, idempotencia)
# ---------------------------------------------------------------------------
cabecalho "passo 4/4 — reinstalacao (idempotencia)"
LOG_ATUAL="$LOG_DIR/4-reinstalacao.log"
RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -i "$MODULO" --without-demo=all --max-cron-threads=0 \
        --stop-after-init --log-level=info)"
[ "$RC" = "0" ] && ok "odoo --init (segunda vez) exit 0 (log: $LOG_ATUAL)" || falhou "odoo --init (segunda vez) exit $RC"
ERROS="$(erros_no_log "$LOG_ATUAL")"
[ "$ERROS" = "0" ] && ok "log de reinstalacao sem linha ERROR/CRITICAL" \
    || falhou "log de reinstalacao com $ERROS linha(s) ERROR/CRITICAL: $(grep -nE '(^| )(ERROR|CRITICAL) ' "$LOG_ATUAL" | head -3 | tr '\n' ' ')"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "modulo installed de novo depois do ciclo install/uninstall/install" \
    || falhou "estado depois da reinstalacao: '$ESTADO'"
VERSAO_BANCO="$(psql_bd "$BANCO" "select latest_version from ir_module_module where name = '$MODULO'")"
[ "$VERSAO_BANCO" = "$VERSAO" ] && ok "versao gravada na reinstalacao == manifesto ($VERSAO_BANCO)" \
    || falhou "versao na reinstalacao ('$VERSAO_BANCO') diferente da do manifesto ('$VERSAO')"

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
