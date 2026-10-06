#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W2-E07-T01 — ACLs e regras de seguranca do modulo `transformativa_sales_ai`
# (carteira x tenant).
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 regras de acesso por carteira/tenant aplicadas;
#   AC2 teste negativo: usuario de um tenant NAO ve dado de outro — vazio ou erro, nunca
#       material alheio;
#   AC3 aprovacao humana jamais concedida por maquina.
#
# TEST PLAN (executado por este script, na VPS do dev, em banco descartavel):
#   passo 1/4 instalacao em banco limpo          -> log + estado lido no banco
#   passo 2/4 testes do modulo (--test-enable)   -> relatorio do runner + classe do aceite
#   passo 3/4 regras aplicadas (lidas no banco)  -> grupos, ACL do modelo, 3 regras de registro
#   passo 4/4 prova negativa independente        -> scripts/odoo/provar_acl_modulo.py
#   limpeza: banco e dupla descartavel removidos; instancia do dev conferida inalterada
#
# POR QUE UMA PROVA ALEM DOS TESTES: o passo 2 e' a suite do proprio autor. O passo 4 monta a
# cena de novo (dois tenants, tres carteiras, usuarios de verdade) e mede pelo ORM com
# `with_user` — o ataque por busca E por leitura de id. Sem os dois, um erro nos testes do
# modulo seria invisivel.
#
# ISOLAMENTO (mesma razao do verificador do E03): o Odoo do dev abre sessao em QUALQUER banco
# novo da instancia `pg-odoo-dev`; por isso este verificador sobe a SUA propria dupla
# descartavel (`postgres:16` + `odoo:19.0`, as imagens do par de dev) em rede propria, com
# nomes prefixados por `e07t01-`, e NAO usa `pg-odoo-dev`, `odoo_dev` nem `/opt/tre/repo`.
#
# Uso (na VPS, a partir de ARQUIVO — nunca por stdin, ver armadilha do `docker compose run`):
#   bash verificar-acl-modulo.sh
#   bash verificar-acl-modulo.sh --apenas-artefatos        (sem a suite de testes do Odoo)
#   bash verificar-acl-modulo.sh --banco tre_e07t01_outro
#   bash verificar-acl-modulo.sh --prova-de-dente          (duas mutacoes do artefato)
#
# Variaveis: TRE_MODULO, TRE_MODULO_DIR, TRE_BANCO, TRE_IMAGEM, TRE_IMAGEM_PG, TRE_PG_USER,
# TRE_MIN_TESTS (minimo de testes no relatorio do runner), TRE_MIN_ITENS_PROVA, TRE_LOG_DIR,
# TRE_DEV_PG_CT, TRE_MANTER_BANCO=1 (nao limpa no fim).
#
# TRE_LOG_DIR: diretorio dos logs de passo do ACEITE (passos 1 a 4). O modo --prova-de-dente NAO
# escreve nele: cada prova usa "$TRE_LOG_DIR/dente/prova-N" e os .out dos dentes ficam em
# "$TRE_LOG_DIR/dente/". A guarda do modo dente fotografa o sha256 dos [1-4]-*.log do aceite antes
# e depois das provas e REPROVA se algum mudar (defeito TRE-W2-E03-T01-D02, reincidente na primeira
# versao deste verificador — ver runbook §8/§9).
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir
# ============================================================================
set -u

MODULO="${TRE_MODULO:-transformativa_sales_ai}"
MODELO="${TRE_MODELO:-tf.process.opportunity}"
# Superficie de ACL do modulo = ALLOW-LIST EXPLICITA (decisao do dono, 03/10/2026, opcao A do
# defeito de integracao do W2 / card t_e0b1bcbf). O card TRE-W3-E03-T01 (commit d0b8d5a) entregou
# o consumidor de outbox e o modelo `tf.evento.outbox` passou a ter ACL: a superficie do modulo
# deixou de ser so `tf.process.opportunity`. O guardrail NAO foi afrouxado — a expectativa continua
# um CONJUNTO FECHADO de modelos E de xmlids: ACL INESPERADA (fora da lista) reprova e ACL da lista
# AUSENTE tambem reprova. Nao e "qualquer superficie serve" nem contagem implicita.
SUPERFICIE_ACL_ESPERADA="${TRE_SUPERFICIE_ACL_ESPERADA:-tf.evento.outbox, tf.process.opportunity}"
ACLS_ESPERADAS="${TRE_ACLS_ESPERADAS:-access_tf_evento_outbox_manager access_tf_evento_outbox_system access_tf_evento_outbox_user access_tf_process_opportunity_manager access_tf_process_opportunity_user}"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
MODULO_DIR="${TRE_MODULO_DIR:-/opt/tre/dev/modulos/$MODULO}"
PROVA="${TRE_PROVA:-$AQUI/provar_acl_modulo.py}"
BANCO="${TRE_BANCO:-tre_e07t01_acl}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
MIN_TESTS="${TRE_MIN_TESTS:-26}"
MIN_ITENS_PROVA="${TRE_MIN_ITENS_PROVA:-20}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-acl-modulo}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-artefatos) MODO=artefatos ;;
        --prova-de-dente) MODO=dente ;;
        --banco) shift; BANCO="${1:-}" ;;
        --modulo-dir) shift; MODULO_DIR="${1:-}" ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
    shift
done

ITENS=0
FALHAS=0
SUFIXO="$$-$RANDOM"
PG_TMP="e07t01-pg-$SUFIXO"
NET_TMP="e07t01-net-$SUFIXO"
DESC_DIR=""
mkdir -p "$LOG_DIR"

ok()     { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()   { printf 'INFO  %s\n' "$*"; }
cabecalho() { printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: ACL_OK ($ITENS itens, 0 falhas) modulo=$MODULO modelo=$MODELO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: ACL_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO modelo=$MODELO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? duas mutacoes, cada uma em copia propria
#   dente 1: a regra de CARTEIRA vira "(1,'=',1)" (o vendedor passa a ver tudo) -> tem de reprovar
#   dente 2: uma ACL plantada da o grupo do vendedor poder de escrita em `res.users`  -> tem de reprovar
#
# LOG PROPRIO (defeito TRE-W2-E03-T01-D02, reincidente na 1a versao deste verificador): cada prova
# escreve num diretorio seu, sob "$LOG_DIR/dente/", e o modo dente nao escreve nem um arquivo no
# diretorio do aceite. Na forma anterior, o sub-run herdava o TRE_LOG_DIR do chamador por ambiente e
# usava os MESMOS nomes de passo: encadear aceite -> dente na mesma sessao (a forma do runbook §3)
# sobrescrevia 1-instalacao.log/2-teste.log/4-prova-negativa.log do aceite com a execucao mutada,
# apagando a evidencia bruta do aceite. A guarda abaixo e' fail-closed: fotografa os [1-4]-*.log do
# aceite antes e depois e reprova o dente se algum mudar.
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e07t01-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_LOG_DIR="${LOG_DIR}/dente"
    mkdir -p "$DENTE_LOG_DIR/prova-1" "$DENTE_LOG_DIR/prova-2"
    DENTE_FALHAS=0
    GUARDA_FALHAS=0
    ACEITE_ANTES="$(cd "$LOG_DIR" 2>/dev/null && sha256sum [1-4]-*.log 2>/dev/null | LC_ALL=C sort)"
    info "logs do aceite: $LOG_DIR  |  logs do dente: $DENTE_LOG_DIR (caminhos separados)"
    SEGURANCA="security/transformativa_sales_ai_security.xml"

    cabecalho "prova de dente 1: regra de carteira mutada para (1,'=',1) (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    sed -i "s@\[('partner_id.user_id', '=', user.id)\]@[(1, '=', 1)]@" "$DENTE_DIR/m1/$SEGURANCA"
    if grep -q "partner_id.user_id" "$DENTE_DIR/m1/$SEGURANCA"; then
        echo 'FALHOU dente 1: a mutacao nao pegou no arquivo (prova sem valor)'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    else
        D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_BANCO="${BANCO}_d1" TRE_LOG_DIR="$DENTE_LOG_DIR/prova-1" bash "$0" 2>&1)"
        printf '%s\n' "$D1" >"$DENTE_LOG_DIR/dente-1-carteira-aberta.out"
        printf '%s\n' "$D1" | grep -E '^(FALHOU|RESULTADO)' | tail -6
        if printf '%s\n' "$D1" | grep -q 'RESULTADO: ACL_FALHOU'; then
            echo 'OK    dente 1: a regra de carteira aberta reprova o aceite'
        else
            echo 'FALHOU dente 1: a regra de carteira aberta NAO reprovou — o criterio 2 nao tem dente'
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
        fi
    fi

    cabecalho "prova de dente 2: ACL plantada dando escrita em res.users ao vendedor (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    printf '%s\n' 'access_res_users_plantada,"ACL plantada pela prova de dente",base.model_res_users,group_tf_sales_ai_user,1,1,1,1' \
        >>"$DENTE_DIR/m2/security/ir.model.access.csv"
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_d2" TRE_LOG_DIR="$DENTE_LOG_DIR/prova-2" bash "$0" 2>&1)"
    printf '%s\n' "$D2" >"$DENTE_LOG_DIR/dente-2-acl-plantada.out"
    printf '%s\n' "$D2" | grep -E '^(FALHOU|RESULTADO)' | tail -6
    if printf '%s\n' "$D2" | grep -q 'RESULTADO: ACL_FALHOU'; then
        echo 'OK    dente 2: a ACL plantada (superficie/escalacao) reprova o aceite'
    else
        echo 'FALHOU dente 2: a ACL plantada NAO reprovou — o criterio 3 nao tem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    # Dente do item de linha de teste reprovado (aprendizado do TRE-W2-E03-T01-D01: item de
    # verificador precisa de dente proprio): o dente 2 tem teste reprovado de verdade no log do
    # runner — o item do passo 2 TEM de disparar nele. Sem isto, o item pode voltar a ser codigo
    # morto (grep ancorado em formato que o Odoo nao emite) sem a bateria perceber.
    if printf '%s\n' "$D2" | grep -qE '^FALHOU [0-9]+ linha\(s\) de teste reprovado'; then
        echo 'OK    dente 2: o item de linha de teste reprovado disparou (item com dente proprio)'
    else
        echo 'FALHOU dente 2: o item "linha(s) de teste reprovado(a) no log" NAO disparou com um teste reprovado — item sem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
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
    if [ "$DENTE_FALHAS" -eq 0 ] && [ "$GUARDA_FALHAS" -eq 0 ]; then
        echo "RESULTADO: ACL_DENTE_OK (2 provas, 0 falhas) modulo=$MODULO logs_aceite=$LOG_DIR logs_dente=$DENTE_LOG_DIR"
        exit 0
    fi
    echo "RESULTADO: ACL_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente, $GUARDA_FALHAS falha(s) na guarda dos logs do aceite) modulo=$MODULO"
    exit 1
fi

# ---------------------------------------------------------------------------
# helpers de medicao
# ---------------------------------------------------------------------------
limpeza() {
    if [ -n "$DESC_DIR" ]; then
        docker rm -f -v "$PG_TMP" >/dev/null 2>&1
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
if command -v openssl >/dev/null 2>&1; then ok "openssl disponivel (segredo das duplas descartaveis)"; else falhou "openssl ausente"; resumo; fi
if [ -f "$MODULO_DIR/__manifest__.py" ]; then
    ok "modulo em disco: $MODULO_DIR/__manifest__.py"
    info "sha256 dos artefatos sob teste:"
    (cd "$MODULO_DIR" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's/^/      /')
else
    falhou "modulo ausente em $MODULO_DIR (__manifest__.py nao encontrado)"; resumo
fi
if [ -f "$PROVA" ]; then
    ok "prova negativa em disco: $PROVA (sha256 $(sha256sum "$PROVA" | cut -d' ' -f1))"
else
    falhou "prova negativa ausente em $PROVA"; resumo
fi
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e do ambiente — so banco descartavel"; resumo ;;
esac
if printf '%s' "$BANCO" | grep -qE '^tre_[a-z0-9_]+$' && ! printf '%s' "$BANCO" | grep -qEi 'prod|homolog'; then
    ok "banco descartavel com nome seguro: $BANCO"
else
    falhou "nome de banco fora do padrao descartavel (^tre_[a-z0-9_]+$): $BANCO"; resumo
fi
DEV_PG_ANTES="nao_medido"
if [ "$(docker inspect -f '{{.State.Running}}' "$DEV_PG_CT" 2>/dev/null)" = "true" ]; then
    DEV_PG_ANTES="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)"
    info "instancia do dev ($DEV_PG_CT) ANTES: $DEV_PG_ANTES"
else
    info "container $DEV_PG_CT do dev nao esta de pe — conferencia 'nao tocou o dev' fica sem medicao"
fi
HOMOLOG_PROD_ANTES="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
info "arquivos em homolog/prod antes: $HOMOLOG_PROD_ANTES"

# ---------------------------------------------------------------------------
# dupla descartavel propria (ver o cabecalho: por que nao usar pg-odoo-dev)
# ---------------------------------------------------------------------------
cabecalho "dupla descartavel propria (postgres + odoo)"
DESC_DIR="$(mktemp -d /tmp/verificacao-acl-XXXXXX)"
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
    # quando o valor e uma variavel (mesma convencao dos outros scripts do projeto).
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
# passo 1/4 — instalacao em banco limpo
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
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "ir_module_module.state = installed" || falhou "estado no banco: '$ESTADO' (esperado installed)"
PENDENTES="$(psql_bd "$BANCO" "select count(*) from ir_module_module where state in ('to install','to upgrade','to remove')")"
[ "$PENDENTES" = "0" ] && ok "nenhum modulo pendurado em to install/to upgrade/to remove" \
    || falhou "$PENDENTES modulo(s) pendurado(s) depois da instalacao"

# ---------------------------------------------------------------------------
# passo 2/4 — suite de testes do modulo (--test-enable)
# ---------------------------------------------------------------------------
LOG_ATUAL="$LOG_DIR/2-teste.log"
if [ "$MODO" = "artefatos" ]; then
    cabecalho "passo 2/4 — PULADO (--apenas-artefatos)"
    info "a suite do Odoo nao foi executada nesta rodada"
else
    cabecalho "passo 2/4 — testes do Odoo (--test-enable)"
    RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -u "$MODULO" --test-enable \
            --without-demo=all --max-cron-threads=0 --stop-after-init --log-level=test)"
    [ "$RC" = "0" ] && ok "odoo --test-enable exit 0 (log: $LOG_ATUAL)" || falhou "odoo --test-enable exit $RC (log: $LOG_ATUAL)"
    RELATORIO="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests when loading database' "$LOG_ATUAL" | tail -1)"
    if [ -n "$RELATORIO" ]; then
        N_TESTS="$(printf '%s' "$RELATORIO" | sed -nE 's/.*of ([0-9]+) tests.*/\1/p')"
        N_FALHAS="$(printf '%s' "$RELATORIO" | sed -nE 's/^([0-9]+) failed.*/\1/p')"
        N_ERROS="$(printf '%s' "$RELATORIO" | sed -nE 's/^[0-9]+ failed, ([0-9]+) error.*/\1/p')"
        if [ "$N_TESTS" -ge "$MIN_TESTS" ] && [ "$N_FALHAS" = "0" ] && [ "$N_ERROS" = "0" ]; then
            ok "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests (minimo $MIN_TESTS)"
        else
            falhou "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests (minimo $MIN_TESTS) — '$RELATORIO'"
        fi
    else
        falhou "log sem linha de relatorio do runner ('N failed, N error(s) of N tests') — sem medicao nao ha aceite"
    fi
    grep -q 'At least one test failed when loading the modules\.' "$LOG_ATUAL" \
        && falhou "log traz 'At least one test failed when loading the modules.'" \
        || ok "log sem 'At least one test failed when loading the modules.'"
    # Formato real do Odoo 19: a linha vem prefixada por `data pid NIVEL banco logger:` —
    # `<logger>: FAIL: TestClasse.test_metodo`. O padrao antigo (`^(FAIL|ERROR): `, ancorado no
    # inicio da linha) era CODIGO MORTO aqui (defeito TRE-W2-E03-T01-D01): imprimia OK com teste
    # reprovado no log. Padrao usado: o mesmo ja medido em `scripts/odoo/verificar-res-partner.sh`.
    LINHAS_TESTE_REPROVADO="$(grep -E '(^| )(FAIL|ERROR): [A-Za-z_]' "$LOG_ATUAL" || true)"
    FALHAS_TESTE="$(printf '%s' "$LINHAS_TESTE_REPROVADO" | grep -c . || true)"
    [ "$FALHAS_TESTE" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
        || falhou "$FALHAS_TESTE linha(s) de teste reprovado(a) no log: $(printf '%s\n' "$LINHAS_TESTE_REPROVADO" | head -2 | tr '\n' ' ')"
    # a classe do aceite TEM de ter rodado: suite verde sem a classe nao prova nada do card
    N_METODOS_ACL="$(grep -cE 'Starting TestAclSeguranca\.test_' "$LOG_ATUAL" || true)"
    if [ "$N_METODOS_ACL" -ge 11 ]; then
        ok "classe de aceite TestAclSeguranca rodou ($N_METODOS_ACL metodos no log)"
    else
        falhou "classe de aceite TestAclSeguranca nao rodou inteira ($N_METODOS_ACL metodos no log, esperado >= 11)"
    fi
    ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
    [ "$ESTADO" = "installed" ] && ok "modulo segue installed depois do teste" || falhou "estado depois do teste: '$ESTADO'"
fi

# ---------------------------------------------------------------------------
# passo 3/4 — as regras estao aplicadas (medido no banco, nao no XML)
# ---------------------------------------------------------------------------
cabecalho "passo 3/4 — regras aplicadas (lidas no banco)"
xmlid() { # $1 = model, $2 = name  -> res_id (ou vazio)
    psql_bd "$BANCO" "select res_id from ir_model_data where module = '$MODULO' and model = '$1' and name = '$2'"
}
N_GRUPOS="$(psql_bd "$BANCO" "select count(*) from ir_model_data where module = '$MODULO' and model = 'res.groups'")"
[ "$N_GRUPOS" = "2" ] && ok "modulo declara 2 grupos (res.groups via ir_model_data)" \
    || falhou "grupos do modulo no banco: $N_GRUPOS (esperado 2)"

PRIVILEGIO_OK="$(psql_bd "$BANCO" "select count(*) from ir_model_data dp join res_groups_privilege p on p.id = dp.res_id join ir_model_data dc on dc.res_id = p.category_id where dp.module = '$MODULO' and dp.name = 'privilege_tf_sales_ai' and dc.module = '$MODULO' and dc.name = 'ir_module_category_tf_sales_ai'")"
[ "$PRIVILEGIO_OK" = "1" ] && ok "privilegio do modulo existe e esta na categoria do modulo" \
    || falhou "privilegio do modulo/categoria nao conferem (esperado 1, medido '$PRIVILEGIO_OK')"

GRUPOS_NO_PRIVILEGIO="$(psql_bd "$BANCO" "select count(*) from ir_model_data dg join res_groups g on g.id = dg.res_id join ir_model_data dp on dp.res_id = g.privilege_id where dg.module = '$MODULO' and dg.model = 'res.groups' and dp.module = '$MODULO' and dp.name = 'privilege_tf_sales_ai'")"
[ "$GRUPOS_NO_PRIVILEGIO" = "2" ] && ok "os 2 grupos do modulo estao no privilegio do modulo" \
    || falhou "grupos no privilegio do modulo: $GRUPOS_NO_PRIVILEGIO (esperado 2)"

GESTOR_IMPLICA="$(psql_bd "$BANCO" "select count(*) from res_groups_implied_rel ir join ir_model_data dg on dg.res_id = ir.gid join ir_model_data dh on dh.res_id = ir.hid where dg.module = '$MODULO' and dg.name = 'group_tf_sales_ai_manager' and dh.module = '$MODULO' and dh.name = 'group_tf_sales_ai_user'")"
[ "$GESTOR_IMPLICA" = "1" ] && ok "gestor implica o grupo do vendedor" \
    || falhou "gestor nao implica o vendedor (medido $GESTOR_IMPLICA, esperado 1)"

ESCALADA="$(psql_bd "$BANCO" "select count(*) from res_groups_implied_rel ir join ir_model_data dg on dg.res_id = ir.gid where dg.module = '$MODULO' and ir.hid in (select res_id from ir_model_data where module = 'base' and name in ('group_system','group_erp_manager'))")"
[ "$ESCALADA" = "0" ] && ok "nenhum grupo do modulo alcanca administracao do Odoo (group_system/group_erp_manager)" \
    || falhou "$ESCALADA ligacao(oes) de grupo do modulo com administracao do Odoo"

# AC3 — a superficie de ACL do modulo e a ALLOW-LIST explicita: modelo a modelo E xmlid a xmlid.
# ACL fora da lista (superficie nova ou escalacao plantada) reprova; ACL da lista que sumiu
# (regressao de seguranca) tambem reprova. O conjunto e fechado — nao ha "qualquer superficie serve".
SUPERFICIE="$(psql_bd "$BANCO" "select string_agg(distinct m.model, ', ' order by m.model) from ir_model_data d join ir_model_access a on a.id = d.res_id join ir_model m on m.id = a.model_id where d.module = '$MODULO' and d.model = 'ir.model.access'")"
[ "$SUPERFICIE" = "$SUPERFICIE_ACL_ESPERADA" ] && ok "superficie de ACL do modulo = allow-list explicita ($SUPERFICIE)" \
    || falhou "superficie de ACL do modulo: '$SUPERFICIE' (allow-list: '$SUPERFICIE_ACL_ESPERADA')"

ACLS_MEDIDAS="$(psql_bd "$BANCO" "select string_agg(name, ' ' order by name) from ir_model_data where module = '$MODULO' and model = 'ir.model.access'")"
[ "$ACLS_MEDIDAS" = "$ACLS_ESPERADAS" ] && ok "ACLs do modulo = allow-list explicita ($ACLS_MEDIDAS)" \
    || falhou "ACLs do modulo fora da allow-list: '$ACLS_MEDIDAS' (esperado: '$ACLS_ESPERADAS')"

MATRIZ_VENDEDOR="$(psql_bd "$BANCO" "select a.perm_read, a.perm_write, a.perm_create, a.perm_unlink from ir_model_access a join ir_model_data d on d.res_id = a.id where d.module = '$MODULO' and d.name = 'access_tf_process_opportunity_user'")"
[ "$MATRIZ_VENDEDOR" = "t|t|t|f" ] && ok "ACL do vendedor = le/cria/escreve, sem apagar (t|t|t|f)" \
    || falhou "ACL do vendedor: '$MATRIZ_VENDEDOR' (esperado 't|t|t|f')"
MATRIZ_GESTOR="$(psql_bd "$BANCO" "select a.perm_read, a.perm_write, a.perm_create, a.perm_unlink from ir_model_access a join ir_model_data d on d.res_id = a.id where d.module = '$MODULO' and d.name = 'access_tf_process_opportunity_manager'")"
[ "$MATRIZ_GESTOR" = "t|t|t|t" ] && ok "ACL do gestor = le/cria/escreve/apaga (t|t|t|t)" \
    || falhou "ACL do gestor: '$MATRIZ_GESTOR' (esperado 't|t|t|t')"

N_REGRAS="$(psql_bd "$BANCO" "select count(*) from ir_model_data where module = '$MODULO' and model = 'ir.rule'")"
[ "$N_REGRAS" = "3" ] && ok "modulo declara 3 regras de registro" || falhou "regras do modulo: $N_REGRAS (esperado 3)"
REGRAS_FORA="$(psql_bd "$BANCO" "select count(*) from ir_rule r join ir_model m on m.id = r.model_id where r.id in (select res_id from ir_model_data where module = '$MODULO' and model = 'ir.rule') and m.model <> '$MODELO'")"
[ "$REGRAS_FORA" = "0" ] && ok "todas as regras do modulo sao do modelo do modulo" \
    || falhou "$REGRAS_FORA regra(s) do modulo apontando para outro modelo"
REGRAS_INATIVAS="$(psql_bd "$BANCO" "select count(*) from ir_rule where id in (select res_id from ir_model_data where module = '$MODULO' and model = 'ir.rule') and not active")"
[ "$REGRAS_INATIVAS" = "0" ] && ok "nenhuma regra do modulo desativada" || falhou "$REGRAS_INATIVAS regra(s) desativada(s)"
REGRAS_AMPLAS="$(psql_bd "$BANCO" "select count(*) from ir_rule where id in (select res_id from ir_model_data where module = '$MODULO' and model = 'ir.rule') and not (perm_read and perm_write and perm_create and perm_unlink)")"
[ "$REGRAS_AMPLAS" = "0" ] && ok "as 3 regras cobrem leitura/escrita/criacao/remocao" \
    || falhou "$REGRAS_AMPLAS regra(s) com permissao parcial (a regra tem de fechar as 4)"

DOM_TENANT="$(psql_bd "$BANCO" "select domain_force from ir_rule where id = $(xmlid ir.rule rule_tf_oportunidade_tenant)")"
[ "$DOM_TENANT" = "[('company_id', 'in', company_ids)]" ] && ok "dominio da regra de tenant e o declarado" \
    || falhou "dominio da regra de tenant: '$DOM_TENANT'"
DOM_CARTEIRA="$(psql_bd "$BANCO" "select domain_force from ir_rule where id = $(xmlid ir.rule rule_tf_oportunidade_carteira)")"
[ "$DOM_CARTEIRA" = "[('partner_id.user_id', '=', user.id)]" ] && ok "dominio da regra de carteira e o declarado" \
    || falhou "dominio da regra de carteira: '$DOM_CARTEIRA'"
DOM_GESTOR="$(psql_bd "$BANCO" "select domain_force from ir_rule where id = $(xmlid ir.rule rule_tf_oportunidade_gestor)")"
[ "$DOM_GESTOR" = "[(1, '=', 1)]" ] && ok "dominio da regra do gestor e o declarado" \
    || falhou "dominio da regra do gestor: '$DOM_GESTOR'"

GLOBAL_TENANT="$(psql_bd "$BANCO" "select count(*) from ir_rule r where r.id = $(xmlid ir.rule rule_tf_oportunidade_tenant) and r.global")"
[ "$GLOBAL_TENANT" = "1" ] && ok "a regra de tenant e global (vale para todo mundo que alcanca o modelo)" \
    || falhou "a regra de tenant nao e global (o recorte de tenant pode ser contornado por outro grupo)"
REGRA_DO_VENDEDOR="$(psql_bd "$BANCO" "select count(*) from ir_rule r join rule_group_rel rg on rg.rule_group_id = r.id join ir_model_data dg on dg.res_id = rg.group_id where r.id = $(xmlid ir.rule rule_tf_oportunidade_carteira) and dg.module = '$MODULO' and dg.name = 'group_tf_sales_ai_user'")"
[ "$REGRA_DO_VENDEDOR" = "1" ] && ok "a regra de carteira esta presa ao grupo do vendedor" \
    || falhou "a regra de carteira nao esta no grupo do vendedor"
REGRA_DO_GESTOR="$(psql_bd "$BANCO" "select count(*) from ir_rule r join rule_group_rel rg on rg.rule_group_id = r.id join ir_model_data dg on dg.res_id = rg.group_id where r.id = $(xmlid ir.rule rule_tf_oportunidade_gestor) and dg.module = '$MODULO' and dg.name = 'group_tf_sales_ai_manager'")"
[ "$REGRA_DO_GESTOR" = "1" ] && ok "a regra do gestor esta presa ao grupo do gestor" \
    || falhou "a regra do gestor nao esta no grupo do gestor"

# ---------------------------------------------------------------------------
# passo 4/4 — prova negativa independente (dois tenants, tres carteiras)
# ---------------------------------------------------------------------------
cabecalho "passo 4/4 — prova negativa independente (provar_acl_modulo.py)"
LOG_ATUAL="$LOG_DIR/4-prova-negativa.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$PROVA")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
grep -E '^(ACL_ITEM|ACL_ITENS|ACL_RESULTADO)' "$LOG_ATUAL" | sed 's/^/      /'
ITENS_PROVA="$(grep -oE '^ACL_ITENS=[0-9]+' "$LOG_ATUAL" | tail -1 | cut -d= -f2)"
FALHAS_PROVA="$(grep -oE 'ACL_FALHAS=[0-9]+' "$LOG_ATUAL" | tail -1 | cut -d= -f2)"
if [ -n "${ITENS_PROVA:-}" ] && [ -n "${FALHAS_PROVA:-}" ]; then
    if [ "$FALHAS_PROVA" = "0" ] && [ "$ITENS_PROVA" -ge "$MIN_ITENS_PROVA" ]; then
        ok "prova negativa: $ITENS_PROVA itens, 0 falhas (minimo $MIN_ITENS_PROVA)"
    else
        falhou "prova negativa: $ITENS_PROVA itens, $FALHAS_PROVA falha(s) (minimo $MIN_ITENS_PROVA)"
    fi
else
    falhou "prova negativa sem marcadores ACL_ITENS/ACL_FALHAS (nao deu para medir)"
fi
grep -q '^ACL_RESULTADO: OK' "$LOG_ATUAL" && ok "prova negativa com ACL_RESULTADO: OK" \
    || falhou "prova negativa sem ACL_RESULTADO: OK"
grep -qE 'MATERIAL ALHEIO|VAZOU' "$LOG_ATUAL" \
    && falhou "a prova negativa acusou material alheio alcancado" \
    || ok "nenhuma acusacao de material alheio na prova negativa"
grep -qE 'Traceback' "$LOG_ATUAL" && falhou "log da prova negativa com traceback" \
    || ok "log da prova negativa sem traceback"

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
docker rm -f -v "$PG_TMP" >/dev/null 2>&1
if [ -z "$(docker ps -q --filter "name=^$PG_TMP$")" ]; then ok "postgres descartavel $PG_TMP removido"; else falhou "postgres descartavel $PG_TMP continua de pe"; fi
docker network rm "$NET_TMP" >/dev/null 2>&1
if [ -z "$(docker network ls -q --filter "name=^$NET_TMP$")" ]; then ok "rede descartavel $NET_TMP removida"; else falhou "rede descartavel $NET_TMP continua"; fi
if [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR" && [ ! -d "$DESC_DIR" ]; then
    DESC_DIR=""
    ok "diretorio de configuracao descartavel removido (senha gerada na hora, nunca em log)"
else
    falhou "diretorio descartavel $DESC_DIR nao foi removido"
fi
if [ "$DEV_PG_ANTES" != "nao_medido" ]; then
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
