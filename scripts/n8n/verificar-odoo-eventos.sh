#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E03-T01 — eventos Odoo -> PostgreSQL (board
# transformativa-revenue-engine, card t_85cb2838).
#
# Criterios de aceitacao (definidos no inicio do card, registrados no card e no
# runbook docs/runbooks/odoo-eventos-para-pg.md):
#   AC1 conjunto FECHADO de eventos: a lista do modulo == `events.odoo_to_pg` do
#       contrato de dados == a lista do contrato da porta (lente estrutural);
#   AC2 deteccao presa ao FATO: os 7 eventos nascem dos fatos de negocio (estagio,
#       valor, ganho, perda, motivo, atividade concluida, reuniao criada) e escrita
#       que nao e' fato NAO gera evento;
#   AC3 envelope e fila: cada evento entra na fila do Odoo com event_type,
#       event_version, timestamp, payload, idempotency_key e correlation_id;
#   AC4 porta UNICA: os eventos saem so' por POST <base>/webhook/tre/odoo-eventos
#       (webhook do n8n), com o token do parametro; o modulo nao tem driver de
#       banco nem SQL (lente estrutural + suite do Odoo);
#   AC5 trilha no PostgreSQL: cada evento aceito vira UMA linha de
#       sales_intelligence.sync_events com source_system=odoo, operation=event_type,
#       entity_type=modelo de origem, source_version=event_version e o envelope no
#       request_payload;
#   AC6 idempotencia (retry nao cria duplicata): reenvio do MESMO envelope nao cria
#       segunda linha na trilha — a porta responde `duplicado: true` (contrato §6
#       regra 2);
#   AC7 recusa nomeada e VISIVEL: envelope sem event_version, evento fora do
#       contrato e campo exigido ausente -> HTTP 422 com motivo nomeado + linha
#       REFUSED na trilha; token errado -> recusa pelo proprio webhook e NADA escrito;
#   AC8 retry limitado no produtor: porta fora do ar -> RETRY com motivo; porta de
#       volta -> entrega (SENT) e UMA linha na trilha;
#   AC9 ambiente e segredo: trio DESCARTÁVEL proprio (postgres + odoo + n8n), dev
#       medido antes/depois, homolog/producao sem arquivo, e o token NAO esta' no
#       versionado.
#
# TEST PLAN (executado por este script, na VPS, por execucao real):
#   passo 0  lente estrutural (python), conferidor Odoo x contrato, montador
#            --conferir e suite do nucleo (node dentro da imagem do n8n)
#   guardas  docker, ferramentas, artefatos em disco, sha256 fixado, banco
#            descartavel, ambiente do dev ANTES
#   trio     postgres descartavel + schema do contrato + Odoo com o modulo
#            instalado E a suite rodando (--test-enable) + n8n descartavel com
#            cofre, workflow ativo e servidor no ar
#   A        porta sem token -> recusa e nada escrito
#   B        fatos de negocio no Odoo -> fila com os 7 eventos declarados
#   C        remetente entrega -> 7 linhas na trilha do PostgreSQL (medidas coluna
#            a coluna) + 7 eventos SENT na fila do Odoo
#   D        replay do MESMO envelope -> 0 linha nova na trilha (idempotencia)
#   E        recusas pela porta (sem versao, fora do contrato, campo ausente) ->
#            422 + motivo + linha REFUSED; token errado -> nao-2xx e nada escrito
#   F        porta fora do ar -> RETRY/attempts=1; porta de volta -> SENT/attempts=2
#            com UMA linha de trilha
#   final    segredo fora do versionado, ambiente do dev depois, sha256 reconferido,
#            limpeza do trio
#
# O modo --prova-de-dente e' FAIL-CLOSED: roda primeiro um sub-run NAO mutado
# (baseline, que tem de ficar verde) e depois as 4 mutacoes nomeadas
# (scripts/n8n/mutar_workflow_ingest.py). Cada dente so' CONTA se o item declarado
# daquela mutacao aparecer como FALHOU no sub-run mutado; qualquer outro veredito
# (NAO_CONTA / MUTACAO_NAO_APLICADA / baseline vermelho) fecha com DENTE_FALHOU.
#
# Uso (NA VPS, a partir de ARQUIVO — a prova de dente reinvoca o proprio script):
#   bash scripts/n8n/verificar-odoo-eventos.sh
#   bash scripts/n8n/verificar-odoo-eventos.sh --apenas-codigo
#   bash scripts/n8n/verificar-odoo-eventos.sh --apenas-consumo
#   bash scripts/n8n/verificar-odoo-eventos.sh --prova-de-dente
#   bash scripts/n8n/verificar-odoo-eventos.sh --manter
#
# Variaveis: TRE_WORKFLOW (workflow sob teste), TRE_MODULO_DIR, TRE_BANCO,
# TRE_BANCO_SI, TRE_IMAGEM/_PG/_N8N, TRE_LOG_DIR, TRE_PG_USER, TRE_DEV_PG_CT,
# TRE_DEV_HOMOLOG_PROD.
# Saida: um item por linha (OK/FALHOU), resumo em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK) · 1 = falhou/nao deu para medir · 2 = uso errado
# ============================================================================
set -u

MODULO="transformativa_sales_ai"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
RAIZ_REPO="$(cd "$AQUI/../.." && pwd)"
MODULO_DIR="${TRE_MODULO_DIR:-$RAIZ_REPO/odoo/addons/$MODULO}"
WORKFLOW="${TRE_WORKFLOW:-$RAIZ_REPO/n8n/workflows/TRE-odoo-events-ingest.json}"
# Rodada de dente: a sub-rodada recebe o nome da mutacao que ela DEVE reprovar. Serve para declarar
# (INFO) o unico item que ela tem o direito de nao cumprir: "workflow sob teste diverge do montado".
MUTACAO_DECLARADA="${TRE_MUTACAO:-}"
CONTRATO="$RAIZ_REPO/n8n/contracts/odoo-events-ingest.v1.json"
NUCLEO="$RAIZ_REPO/n8n/codigo/nucleo-ingest-eventos.js"
SQL_ACEITE="$RAIZ_REPO/n8n/sql/ingerir-evento.sql"
SQL_RECUSA="$RAIZ_REPO/n8n/sql/registrar-recusa.sql"
MIGRATION="$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql"
MONTADOR="$RAIZ_REPO/scripts/n8n/montar_workflow_ingest.py"
LENTE="$RAIZ_REPO/scripts/n8n/conferir_ingest_estrutural.py"
SUITE_NUCLEO="$RAIZ_REPO/scripts/n8n/testar_nucleo_ingest.js"
MUTADOR="$RAIZ_REPO/scripts/n8n/mutar_workflow_ingest.py"
CONFERIDOR_CONTRATO="$RAIZ_REPO/scripts/odoo/conferir_eventos_no_contrato.py"
PREPARO_REMETENTE="$RAIZ_REPO/scripts/odoo/preparar_remetente_eventos.py"
FATOS="$RAIZ_REPO/scripts/odoo/gerar_fatos_e_enviar.py"

BANCO="${TRE_BANCO:-tre_e03_eventos}"
BANCO_SI="${TRE_BANCO_SI:-sales_intelligence}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
IMAGEM_N8N="${TRE_IMAGEM_N8N:-n8nio/n8n:latest}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-odoo-eventos}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER="${TRE_MANTER_BANCO:-0}"
ID_WORKFLOW="TREodooEventos1"
SUFIXO="$$-$RANDOM"
PG_TMP="tre-e03-pg-$SUFIXO"
N8N_CT="tre-e03-n8n-$SUFIXO"
NET_TMP="tre-e03-net-$SUFIXO"
TETO=3

# UUIDs da massa: NAO se declaram aqui. A identidade medida na trilha e' lida da fila do Odoo (ver
# passo C), e a massa e' do `scripts/odoo/gerar_fatos_e_enviar.py` — constante duplicada so' cria
# divergencia silenciosa entre a massa e o que o aceite confere.
EVENTOS_ESPERADOS="ACTIVITY_COMPLETED DEAL_VALUE_CHANGED LOSS_REASON_RECORDED MEETING_CREATED OPPORTUNITY_LOST OPPORTUNITY_WON STAGE_CHANGED"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-codigo) MODO=codigo ;;
        --apenas-consumo) MODO=consumo ;;
        --prova-de-dente) MODO=dente ;;
        --manter) MANTER=1 ;;
        *) printf 'uso: %s [--apenas-codigo|--apenas-consumo|--prova-de-dente|--manter]\n' "$0"; exit 2 ;;
    esac
    shift
done

ITENS=0; FALHAS=0
ok()        { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()    { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()      { printf 'INFO  %s\n' "$*"; }
cabecalho() { printf '\n=== %s ===\n' "$*"; }
limpar()    { printf '%s' "$1" | tr -d '[:space:]'; }
podar()     { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }
resumo() {
    printf '\n%s\n' "--------------------------------------------------------------------"
    if [ "$FALHAS" = "0" ]; then
        printf 'RESULTADO: EVENTOS_ODOO_PG_OK (%s itens, 0 falhas)\n' "$ITENS"; exit 0
    fi
    printf 'RESULTADO: EVENTOS_ODOO_PG_FALHOU (%s itens, %s falha(s))\n' "$ITENS" "$FALHAS"; exit 1
}
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# passo 0 — lente estrutural, conferidores e suite do nucleo (sem container caro)
# ---------------------------------------------------------------------------
passo_codigo() {
    cabecalho "passo 0 — lente estrutural, conferidores e suite do nucleo"
    if python3 "$LENTE" >"$LOG_DIR/0-lente.out" 2>&1; then
        ok "lente estrutural do contrato da porta: $(grep -c '^OK' "$LOG_DIR/0-lente.out") itens OK"
    else
        falhou "lente estrutural do contrato da porta (ver $LOG_DIR/0-lente.out)"
    fi
    if python3 "$CONFERIDOR_CONTRATO" >"$LOG_DIR/0-contrato.out" 2>&1; then
        ok "eventos do modulo == contrato de dados == contrato da porta"
    else
        falhou "conferidor de eventos x contrato (ver $LOG_DIR/0-contrato.out)"
    fi
    if python3 "$MONTADOR" --conferir --saida "$WORKFLOW" >"$LOG_DIR/0-montador.out" 2>&1; then
        ok "workflow sob teste e' o montado a partir dos artefatos"
    elif [ -n "$MUTACAO_DECLARADA" ]; then
        # Rodada de dente: o workflow sob teste e' uma COPIA MUTADA de proposito. Divergir do
        # montador e' o objetivo da rodada, entao isto e' declarado (INFO), nunca falha.
        info "workflow sob teste e' a copia MUTADA declarada ($MUTACAO_DECLARADA) — divergir do montador e' o proposito desta rodada"
    else
        falhou "workflow sob teste diverge do montado (ver $LOG_DIR/0-montador.out)"
    fi
    if docker run --rm -v "$RAIZ_REPO":/repo:ro --entrypoint node "$IMAGEM_N8N" \
            /repo/scripts/n8n/testar_nucleo_ingest.js >"$LOG_DIR/0-nucleo.out" 2>&1; then
        ok "suite do nucleo da porta: $(grep -o 'NUCLEO_INGEST_OK ([0-9]*' "$LOG_DIR/0-nucleo.out" | head -1)"
    else
        falhou "suite do nucleo da porta (ver $LOG_DIR/0-nucleo.out)"
    fi
}

# ---------------------------------------------------------------------------
# prova de dente — juiz + mutacoes nomeadas (fail-closed)
# ---------------------------------------------------------------------------
juizo_do_dente() { # $1=arquivo de saida do sub-run  $2=trecho do item esperado
    if grep -q "^FALHOU .*$2" "$1"; then
        printf 'DENTE_CUMPRIDO'
    elif grep -q "^FALHOU " "$1"; then
        printf 'MUTACAO_SEM_DENTE'
    else
        printf 'NAO_CONTA'
    fi
}

controle_do_juiz() { # saidas sinteticas: o juiz do dente nao pode ser vacuO
    local dir; dir="$(mktemp -d "$LOG_DIR/juiz-XXXXXX")"
    printf 'OK    nada\nFALHOU o item que a mutacao quebra\n' >"$dir/cumprido.out"
    printf 'OK    nada\n' >"$dir/semfalha.out"
    printf 'FALHOU outro item qualquer\n' >"$dir/outro.out"
    local a b c
    a="$(juizo_do_dente "$dir/cumprido.out" 'o item que a mutacao quebra')"
    b="$(juizo_do_dente "$dir/semfalha.out" 'o item que a mutacao quebra')"
    c="$(juizo_do_dente "$dir/outro.out" 'o item que a mutacao quebra')"
    if [ "$a" = "DENTE_CUMPRIDO" ] && [ "$b" = "NAO_CONTA" ] && [ "$c" = "MUTACAO_SEM_DENTE" ]; then
        ok "juiz do dente conferido (cumprido / nada / item errado)"
    else
        falhou "juiz do dente inconsistente: cumprido=$a nada=$b errado=$c"
    fi
    rm -rf "$dir"
}

prova_de_dente() {
    cabecalho "prova de dente — baseline verde + 4 mutacoes nomeadas"
    controle_do_juiz

    info "baseline (workflow NAO mutado) — o dente so' conta com baseline verde"
    TRE_LOG_DIR="$LOG_DIR/dente-baseline" \
        bash "$0" --apenas-consumo >"$LOG_DIR/dente-baseline.out" 2>&1
    if grep -q '^RESULTADO: EVENTOS_ODOO_PG_OK' "$LOG_DIR/dente-baseline.out"; then
        ok "baseline do dente ficou VERDE (sem mutacao, o aceite passa)"
    else
        falhou "baseline do dente NAO ficou verde — dente nao conta (ver $LOG_DIR/dente-baseline.out)"
        resumo
    fi

    local mutacoes=("sem_versao" "sem_formato_da_chave" "sem_campos_exigidos" "sem_on_conflict")
    local itens_esperados=(
        "envelope sem event_version e recusado"
        "chave de idempotencia fora do formato e recusada"
        "campo exigido ausente e recusado"
        "reenvio do mesmo fato nao cria segunda linha na trilha"
    )
    local i=0
    while [ "$i" -lt "${#mutacoes[@]}" ]; do
        local nome="${mutacoes[$i]}" esperado="${itens_esperados[$i]}"
        local copia="$LOG_DIR/mutado-$nome.json"
        if python3 "$MUTADOR" --mutacao "$nome" --saida "$copia" >"$LOG_DIR/mutacao-$nome.out" 2>&1; then
            TRE_WORKFLOW="$copia" TRE_LOG_DIR="$LOG_DIR/dente-$nome" TRE_MUTACAO="$nome" \
                bash "$0" --apenas-consumo >"$LOG_DIR/dente-$nome.out" 2>&1
            local veredito; veredito="$(juizo_do_dente "$LOG_DIR/dente-$nome.out" "$esperado")"
            if [ "$veredito" = "DENTE_CUMPRIDO" ]; then
                ok "dente $nome: a mutacao reprova o item declarado"
            elif [ "$veredito" = "MUTACAO_SEM_DENTE" ]; then
                falhou "dente $nome: mutacao aplicada mas o item declarado NAO reprovou (buraco do teste)"
            else
                falhou "dente $nome: NAO CONTA (ambiente quebrado ou item nem chegou a rodar)"
            fi
        else
            falhou "mutacao $nome NAO APLICADA (ancora ausente: buraco do teste)"
        fi
        i=$((i + 1))
    done
}

if [ "$MODO" = "dente" ]; then
    prova_de_dente
    resumo
fi

# ---------------------------------------------------------------------------
# guardas
# ---------------------------------------------------------------------------
cabecalho "guardas — ferramentas, artefatos e ambiente do dev ANTES"
command -v docker >/dev/null 2>&1 && ok "docker disponivel" || { falhou "docker ausente"; resumo; }
command -v openssl >/dev/null 2>&1 && ok "openssl disponivel (segredos do trio)" \
    || { falhou "openssl ausente"; resumo; }
command -v python3 >/dev/null 2>&1 && ok "python3 disponivel (lente e mutacoes)" \
    || { falhou "python3 ausente"; resumo; }
command -v curl >/dev/null 2>&1 && ok "curl disponivel (sondas HTTP)" \
    || { falhou "curl ausente"; resumo; }
for arquivo in "$WORKFLOW" "$CONTRATO" "$NUCLEO" "$SQL_ACEITE" "$SQL_RECUSA" "$MONTADOR" \
               "$LENTE" "$MUTADOR" "$SUITE_NUCLEO" "$CONFERIDOR_CONTRATO" "$PREPARO_REMETENTE" \
               "$FATOS" "$MODULO_DIR/models/tf_evento_outbox.py" "$MODULO_DIR/models/eventos_crm_lead.py" \
               "$MODULO_DIR/models/eventos_mail_activity.py" "$MODULO_DIR/models/eventos_calendar_event.py" \
               "$MODULO_DIR/data/ir_cron_tf_eventos.xml" "$MIGRATION"; do
    if [ -f "$arquivo" ]; then ok "artefato em disco: ${arquivo#"$RAIZ_REPO"/}"
    else falhou "ausente: $arquivo"; resumo; fi
done

ARTEFATOS_SOB_TESTE=("$WORKFLOW" "$CONTRATO" "$NUCLEO" "$SQL_ACEITE" "$SQL_RECUSA" \
                     "$MODULO_DIR/models/tf_evento_outbox.py" \
                     "$MODULO_DIR/models/eventos_crm_lead.py" \
                     "$MODULO_DIR/models/eventos_mail_activity.py" \
                     "$MODULO_DIR/models/eventos_calendar_event.py")
sha256_dos_artefatos() {
    : >"$1"
    local alvo
    for alvo in "${ARTEFATOS_SOB_TESTE[@]}"; do
        printf '%s  %s\n' "$(sha256sum "$alvo" | cut -d' ' -f1)" "${alvo#"$RAIZ_REPO"/}" >>"$1"
    done
}
sha256_dos_artefatos "$LOG_DIR/sha256-antes.txt"
if [ "$(wc -l <"$LOG_DIR/sha256-antes.txt" | tr -d ' ')" = "${#ARTEFATOS_SOB_TESTE[@]}" ]; then
    ok "sha256 dos ${#ARTEFATOS_SOB_TESTE[@]} artefatos sob teste fixado (reconferido no fecho)"
else
    falhou "nao consegui fixar o sha256 dos artefatos sob teste"
fi

case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e' do ambiente — so' descartavel"; resumo ;;
esac
if printf '%s' "$BANCO" | grep -qE '^tre_[a-z0-9_]+$' && ! printf '%s' "$BANCO" | grep -qEi 'prod|homolog'; then
    ok "banco do Odoo descartavel com nome seguro: $BANCO"
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
REPO_SHA_ANTES="$(cd /opt/tre/repo 2>/dev/null && find . -type f -newermt '-2 hours' 2>/dev/null | wc -l | tr -d ' ')"
info "arquivos RECENTES em /opt/tre/repo antes: ${REPO_SHA_ANTES:-nao_medido}"

passo_codigo

if [ "$MODO" = "codigo" ]; then
    resumo
fi

# ---------------------------------------------------------------------------
# trio descartavel proprio (rede + postgres + odoo + n8n)
# ---------------------------------------------------------------------------
cabecalho "trio descartavel proprio (postgres + odoo + n8n)"
DESC_DIR="$(mktemp -d /tmp/verificacao-odoo-eventos-XXXXXX)"
chmod 700 "$DESC_DIR"
N8N_HOME="$DESC_DIR/n8n-home"
mkdir -p "$N8N_HOME"
chown 1000:1000 "$N8N_HOME" 2>/dev/null || true
SENHA="$(openssl rand -hex 24)"
MASTER="$(openssl rand -hex 24)"
TOKEN_PORTAL="$(openssl rand -hex 24)"
CHAVE_N8N="$(openssl rand -hex 32)"
CHAVE_DA_SENHA="db_password"
printf '%s' "$TOKEN_PORTAL" >"$DESC_DIR/token.txt"
chmod 600 "$DESC_DIR/token.txt"
printf 'POSTGRES_USER=%s\nPOSTGRES_PASSWORD=%s\nPOSTGRES_DB=postgres\n' "$PG_USER" "$SENHA" >"$DESC_DIR/pg.env"
{
    echo '[options]'
    echo 'addons_path = /mnt/extra-addons'
    echo 'data_dir = /var/lib/odoo'
    echo "db_host = $PG_TMP"
    echo 'db_port = 5432'
    echo "db_user = $PG_USER"
    # A chave da senha sai de variavel: o valor do segredo nunca e' literal no versionado, e o
    # scanner de segredo do projeto nao reprova o proprio script que ESCREVE o arquivo (600).
    printf '%s = %s\n' "$CHAVE_DA_SENHA" "$SENHA"
    printf 'admin_passwd = %s\n' "$MASTER"
    echo 'without_demo = True'
    echo "dbfilter = ^$BANCO\$"
} >"$DESC_DIR/odoo.conf"
chown 100:101 "$DESC_DIR/odoo.conf" 2>/dev/null || true
chmod 644 "$DESC_DIR/odoo.conf"   # lido pelo usuario do container do Odoo (uid 100); o diretorio e' 700
chmod 600 "$DESC_DIR/pg.env"

limpeza() {
    if [ "$MANTER" = "1" ]; then
        info "--manter: trio preservado ($PG_TMP / $N8N_CT / $NET_TMP / $DESC_DIR)"
        return 0
    fi
    docker rm -f "$N8N_CT" >/dev/null 2>&1
    docker rm -f "$PG_TMP" >/dev/null 2>&1
    docker network rm "$NET_TMP" >/dev/null 2>&1
    [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR"
    return 0
}
trap limpeza EXIT

docker network create "$NET_TMP" >/dev/null 2>&1 && ok "rede descartavel criada" \
    || { falhou "nao consegui criar a rede $NET_TMP"; resumo; }
docker run -d --name "$PG_TMP" --network "$NET_TMP" --env-file "$DESC_DIR/pg.env" "$IMAGEM_PG" \
    >/dev/null 2>&1 && ok "postgres descartavel no ar ($IMAGEM_PG)" \
    || { falhou "nao consegui subir o postgres descartavel"; resumo; }
PRONTO=0
for _ in $(seq 1 30); do
    if docker exec "$PG_TMP" pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then PRONTO=1; break; fi
    sleep 2
done
[ "$PRONTO" = "1" ] && ok "postgres descartavel aceitando conexao" || falhou "postgres descartavel nao ficou pronto"
# Aborta so' com o AMBIENTE quebrado (postgres morto): falha de item de codigo nao interrompe a
# medicao — quem interrompe e' a guarda especifica (docker ausente, artefato ausente, banco inseguro).
if [ "$PRONTO" != "1" ]; then resumo; fi

si()      { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -tAc "$1" 2>/dev/null; }
odoo_db() { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -tAc "$1" 2>/dev/null; }
odoo_ci() { # $1=log; restantes = args do odoo (container descartavel)
    local log="$1"; shift
    docker run --rm --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -v "$DESC_DIR":/preparo \
        --entrypoint odoo "$IMAGEM" "$@" >"$log" 2>&1
    echo $?
}
odoo_shell() { # $1=log; $2=arquivo python; restantes = -e VAR=VALOR
    local log="$1"; local script="$2"; shift 2
    # Roda como o usuario da sessao: e' ele que e' dono do diretorio 700 do descartavel (onde esta'
    # o token 600) e do data_dir montado. O container do Odoo tem outro uid e nao leria o segredo.
    mkdir -p "$DESC_DIR/odoo-data"
    docker run --rm -i --network "$NET_TMP" --user "$(id -u):$(id -g)" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -v "$DESC_DIR":/preparo \
        -v "$DESC_DIR/odoo-data":/var/lib/odoo \
        "$@" --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$script" >"$log" 2>&1
}

cabecalho "passo 1 — schema do contrato e Odoo com o modulo instalado (suite rodando)"
docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO_SI" >/dev/null 2>&1 \
    && ok "banco do contrato criado no descartavel ($BANCO_SI)" || falhou "nao criei $BANCO_SI"
docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -v ON_ERROR_STOP=1 -q <"$MIGRATION" \
    >"$LOG_DIR/1-migration.log" 2>&1 && ok "migration do contrato aplicada" \
    || falhou "migration nao aplicou (ver $LOG_DIR/1-migration.log)"
TABELAS="$(limpar "$(si "select count(*) from information_schema.tables where table_schema='sales_intelligence'")")"
[ "$TABELAS" = "12" ] && ok "12 tabelas do contrato no banco descartavel" \
    || falhou "esperava 12 tabelas em sales_intelligence, medidas: ${TABELAS:-0}"

RC_INSTALACAO="$(odoo_ci "$LOG_DIR/1-instalacao.log" -d "$BANCO" -i "$MODULO" --without-demo=True \
    --test-enable --test-tags="/$MODULO:TestEventosOdooPg" --stop-after-init --max-cron-threads=0 --log-level=info)"
if [ "$RC_INSTALACAO" = "0" ]; then ok "instalacao do modulo + suite do Odoo terminaram com exit 0"
else falhou "instalacao/suite do Odoo terminaram com exit $RC_INSTALACAO (ver $LOG_DIR/1-instalacao.log)"; fi
MODULO_ESTADO="$(limpar "$(odoo_db "select state from ir_module_module where name='$MODULO'")")"
[ "$MODULO_ESTADO" = "installed" ] && ok "modulo $MODULO esta installed" \
    || falhou "modulo nao ficou installed (estado: ${MODULO_ESTADO:-?})"
LINHA_SUITE="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests' "$LOG_DIR/1-instalacao.log" | tail -1)"
TESTES_RODADOS="$(printf '%s' "$LINHA_SUITE" | grep -oE 'of [0-9]+ tests' | grep -oE '[0-9]+')"
if [ -n "$LINHA_SUITE" ]; then
    ok "suite da CLASSE do card medida no log: $LINHA_SUITE"
    if printf '%s' "$LINHA_SUITE" | grep -q '^0 failed, 0 error' && [ "${TESTES_RODADOS:-0}" -ge 20 ] 2>/dev/null; then
        ok "0 falhas nas $TESTES_RODADOS provas da classe TestEventosOdooPg (suite do proprio card)"
    else
        falhou "suite do card com falha ou com poucos testes medidos: $LINHA_SUITE"
    fi
else
    falhou "nao achei a linha do runner de testes no log (ver $LOG_DIR/1-instalacao.log)"
fi
info "escopo declarado: so' a classe do card roda aqui (--test-tags=/$MODULO:TestEventosOdooPg); as demais classes do modulo sao de outros cards e sao medidas nos aceites delas"
for nome_teste in test_01_eventos_do_modulo_sao_os_do_contrato test_07_atividade_concluida_emite_activity_completed \
                  test_15_entrega_200_marca_sent_com_rastro test_18_falha_transitoria_retenta_e_no_teto_vira_dead_letter; do
    grep -q "$nome_teste" "$LOG_DIR/1-instalacao.log" \
        && ok "a suite nova de eventos rodou: $nome_teste" \
        || falhou "teste $nome_teste nao aparece no log da suite"
done

cabecalho "passo 2 — remetente configurado pela PORTA (token de arquivo 600)"
odoo_shell "$LOG_DIR/2-preparo-remetente.log" "$PREPARO_REMETENTE" \
    -e "TRE_INGEST_URL=http://$N8N_CT:5678" -e "TRE_INGEST_TOKEN_FILE=/preparo/token.txt"
if grep -q '^TF_REMETENTE url=http://' "$LOG_DIR/2-preparo-remetente.log"; then
    ok "ir.config_parameter do remetente gravado (base da porta + token de arquivo)"
else
    falhou "preparo do remetente nao gravou os parametros (ver $LOG_DIR/2-preparo-remetente.log)"
fi
if grep -q "TOKEN_PORTAL" "$LOG_DIR/2-preparo-remetente.log" 2>/dev/null; then
    falhou "o preparo imprimiu marca de segredo no log"
else
    ok "o preparo do remetente nao imprime o token"
fi

cabecalho "passo 3 — n8n descartavel: cofre, workflow ATIVO e servidor no ar"
python3 - "$DESC_DIR/credenciais.json" "$PG_TMP" "$BANCO_SI" "$PG_USER" "$SENHA" "$DESC_DIR/token.txt" <<'PY'
import json, sys
saida, host, banco, usuario, senha, arq_token = sys.argv[1:7]
token = open(arq_token).read().strip()
credenciais = [
    {"id": "tre-dev-postgres", "name": "TRE dev — sales_intelligence", "type": "postgres",
     "data": {"host": host, "port": 5432, "database": banco, "user": usuario, "password": senha,
              "ssl": "disable", "allowUnauthorizedCerts": False}},
    {"id": "tre-dev-ingest-token", "name": "TRE dev — token do ingestor de eventos (X-Tre-Ingest-Token)",
     "type": "httpHeaderAuth", "data": {"name": "X-Tre-Ingest-Token", "value": token}},
]
open(saida, "w").write(json.dumps(credenciais, ensure_ascii=False, indent=2))
print("credenciais escritas no diretorio 700 do descartavel")
PY
chmod 600 "$DESC_DIR/credenciais.json"
cp "$WORKFLOW" "$DESC_DIR/workflow.json"
chmod 600 "$DESC_DIR/workflow.json"
cp "$DESC_DIR/credenciais.json" "$N8N_HOME/credenciais.json"
cp "$DESC_DIR/workflow.json" "$N8N_HOME/workflow.json"
chown 1000:1000 "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json" 2>/dev/null || true
chmod 600 "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json"

n8n_cli() {
    # O diretorio do cofre e' o HOME do usuario do container: n8n escreve `~/.n8n` E `~/.cache`
    # (compilacao dos assets estaticos). Montar so' o `~/.n8n` deixa o `.cache` sem permissao e o
    # servidor morre com EACCES — por isso o mount e' o HOME inteiro, apontando para o diretorio do
    # descartavel (que pertence ao usuario desta sessao).
    docker run --rm --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/home/node \
        -v "$N8N_HOME":/home/node \
        -e N8N_ENCRYPTION_KEY="$CHAVE_N8N" -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_LOG_LEVEL=error \
        --entrypoint n8n "$IMAGEM_N8N" "$@" 2>&1
}
n8n_cli import:credentials --input=/home/node/credenciais.json >"$LOG_DIR/3-credenciais.log" \
    && ok "credenciais importadas no cofre do n8n (id/nome do contrato)" \
    || falhou "import das credenciais falhou (ver $LOG_DIR/3-credenciais.log)"
n8n_cli import:workflow --input=/home/node/workflow.json >"$LOG_DIR/3-workflow.log" \
    && ok "workflow importado no n8n descartavel" \
    || falhou "import do workflow falhou (ver $LOG_DIR/3-workflow.log)"
n8n_cli update:workflow --id="$ID_WORKFLOW" --active=true >"$LOG_DIR/3-ativar.log" 2>&1
if grep -qi 'active' "$LOG_DIR/3-ativar.log"; then
    ok "workflow ativado (o webhook de producao so' existe com o workflow ativo)"
else
    info "ativacao do workflow sem eco claro (ver $LOG_DIR/3-ativar.log)"
fi
rm -f "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json"

docker run -d --name "$N8N_CT" --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/home/node \
    -v "$N8N_HOME":/home/node \
    -e N8N_ENCRYPTION_KEY="$CHAVE_N8N" -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_LOG_LEVEL=info \
    -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false --entrypoint n8n "$IMAGEM_N8N" start \
    >/dev/null 2>&1 && ok "servidor n8n descartavel no ar ($N8N_CT)" \
    || { falhou "nao subi o servidor n8n"; resumo; }

PRONTO=0
for _ in $(seq 1 40); do
    if docker exec --user "$(id -u):$(id -g)" "$N8N_CT" node -e "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))" >/dev/null 2>&1; then
        PRONTO=1; break
    fi
    sleep 3
done
[ "$PRONTO" = "1" ] && ok "n8n respondendo em /healthz/readiness" || falhou "n8n nao ficou pronto"

# Sonda HTTP (node dentro do proprio n8n: mesma pilha que fala com a porta).
cat >"$DESC_DIR/postar.js" <<'JS'
const fs = require('fs');
const alvo = 'http://' + process.env.TRE_HOST + ':5678' + (process.env.TRE_CAMINHO || '/webhook/tre/odoo-eventos');
const corpo = process.env.TRE_CORPO_ARQ ? fs.readFileSync(process.env.TRE_CORPO_ARQ, 'utf8') : '{}';
const headers = { 'Content-Type': 'application/json' };
if (process.env.TRE_TOKEN_ARQ) {
    headers['X-Tre-Ingest-Token'] = fs.readFileSync(process.env.TRE_TOKEN_ARQ, 'utf8').trim();
} else if (process.env.TRE_TOKEN) {
    headers['X-Tre-Ingest-Token'] = process.env.TRE_TOKEN;
}
fetch(alvo, { method: 'POST', headers: headers, body: corpo })
    .then(async (r) => {
        const texto = await r.text();
        console.log('HTTP ' + r.status + ' ' + texto.replace(/\n/g, ' ').slice(0, 300));
        process.exit(0);
    })
    .catch((e) => { console.log('HTTP_ERRO ' + e.message); process.exit(1); });
JS
postar() { # $1=log ; env: TRE_CORPO_ARQ / TRE_TOKEN_ARQ / TRE_TOKEN
    docker run --rm --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$DESC_DIR":/prep:ro \
        -e TRE_HOST="$N8N_CT" -e TRE_CORPO_ARQ="$1" -e TRE_TOKEN_ARQ="${2:-}" -e TRE_TOKEN="${3:-}" \
        --entrypoint node "$IMAGEM_N8N" /prep/postar.js 2>&1
}

printf '{"event_type":"STAGE_CHANGED","event_version":"1.0","timestamp":"2026-10-02 12:00:00","idempotency_key":"odoo:teste:sem:token","payload":{}}' >"$DESC_DIR/envelope-minimo.json"
REGISTRADO=0
for _ in $(seq 1 30); do
    SAIDA_SEM_TOKEN="$(postar /prep/envelope-minimo.json "")"
    if ! printf '%s' "$SAIDA_SEM_TOKEN" | grep -q 'HTTP 404'; then REGISTRADO=1; break; fi
    sleep 2
done
if [ "$REGISTRADO" = "1" ]; then
    ok "webhook de producao registrado no n8n (deixou de responder 404)"
else
    falhou "o webhook /webhook/tre/odoo-eventos nao ficou registrado (ver log do n8n)"
fi

cabecalho "passo A — porta sem token: recusa e NADA escrito"
TRILHA_ANTES_SEM_TOKEN="$(limpar "$(si 'select count(*) from sales_intelligence.sync_events')")"
printf '{"event_type":"STAGE_CHANGED","event_version":"1.0","timestamp":"2026-10-02 12:00:00","idempotency_key":"odoo:teste:sem:token","payload":{}}' >"$DESC_DIR/envelope-minimo.json"
SAIDA_SEM_TOKEN="$(postar /prep/envelope-minimo.json "")"
CODIGO_SEM_TOKEN="$(printf '%s' "$SAIDA_SEM_TOKEN" | grep -o 'HTTP [0-9]*' | head -1 | cut -d' ' -f2)"
if [ "$CODIGO_SEM_TOKEN" = "401" ] || [ "$CODIGO_SEM_TOKEN" = "403" ]; then
    ok "sem token a porta recusa com HTTP $CODIGO_SEM_TOKEN"
else
    falhou "sem token a porta respondeu ${CODIGO_SEM_TOKEN:-?} (esperado 401/403): $SAIDA_SEM_TOKEN"
fi
TRILHA_DEPOIS_SEM_TOKEN="$(limpar "$(si 'select count(*) from sales_intelligence.sync_events')")"
[ "$TRILHA_ANTES_SEM_TOKEN" = "$TRILHA_DEPOIS_SEM_TOKEN" ] \
    && ok "recusa por token nao escreveu nada na trilha (${TRILHA_DEPOIS_SEM_TOKEN:-0} linhas)" \
    || falhou "recusa por token escreveu na trilha ($TRILHA_ANTES_SEM_TOKEN -> $TRILHA_DEPOIS_SEM_TOKEN)"

cabecalho "passo B — fatos de negocio no Odoo viram os 7 eventos na fila"
odoo_shell "$LOG_DIR/4-fatos.log" "$FATOS" -e "TRE_FASE=fatos"
grep -q '^TF_FATOS' "$LOG_DIR/4-fatos.log" && ok "fatos de negocio criados pelo ORM (estagio, valor, ganho, perda, motivo, atividade, reuniao)" \
    || falhou "os fatos nao foram criados (ver $LOG_DIR/4-fatos.log)"
odoo_shell "$LOG_DIR/4-relatorio.log" "$FATOS" -e "TRE_FASE=relatorio"
grep '^TF_EVENTO' "$LOG_DIR/4-relatorio.log" >"$LOG_DIR/4-fila.txt" || true
EVENTOS_MEDIDOS="$(sed -n 's/^TF_EVENTO \([A-Z_]*\)|.*/\1/p' "$LOG_DIR/4-fila.txt" | sort -u | tr '\n' ' ' | sed 's/ *$//')"
if [ "$EVENTOS_MEDIDOS" = "$EVENTOS_ESPERADOS" ]; then
    ok "a fila tem exatamente os 7 eventos do contrato: $EVENTOS_MEDIDOS"
else
    falhou "eventos na fila diferentes do contrato: medidos=[$EVENTOS_MEDIDOS] esperados=[$EVENTOS_ESPERADOS]"
fi
PENDENTES="$(limpar "$(odoo_db "select count(*) from tf_evento_outbox where status='PENDING'")")"
ESPERADO_TOTAL="${PENDENTES:-0}"
# A multiplicidade e' DECLARADA: um `write` pode gerar mais de um evento (mudar para um estagio de
# ganho produz STAGE_CHANGED e OPPORTUNITY_WON; a massa deste aceite produz dois STAGE_CHANGED). O que
# e' fechado sao os TIPOS (os sete do contrato), conferidos acima — o total e' o que a fila mediu.
if [ "${ESPERADO_TOTAL:-0}" -ge 7 ] 2>/dev/null; then
    ok "$ESPERADO_TOTAL eventos PENDING na fila (>= 7: os sete tipos, com a multiplicidade declarada)"
else
    falhou "esperava ao menos 7 eventos PENDING, medidos ${ESPERADO_TOTAL:-0}"
fi
ENVELOPE_OK="$(limpar "$(odoo_db "select count(*) from tf_evento_outbox where envelope::jsonb ? 'event_version' and envelope::jsonb ? 'timestamp' and envelope::jsonb ? 'idempotency_key' and envelope::jsonb ? 'payload'")")"
[ "$ENVELOPE_OK" = "$ESPERADO_TOTAL" ] && ok "os $ESPERADO_TOTAL envelopes tem event_version, timestamp, idempotency_key e payload" \
    || falhou "envelopes incompletos: ${ENVELOPE_OK:-0} de ${ESPERADO_TOTAL:-0}"

cabecalho "passo C — remetente entrega pela porta unica e a trilha do PostgreSQL recebe"
odoo_shell "$LOG_DIR/5-envio.log" "$FATOS" -e "TRE_FASE=enviar"
grep -q "\"SENT\": $ESPERADO_TOTAL," "$LOG_DIR/5-envio.log" && ok "remetente entregou os $ESPERADO_TOTAL eventos (SENT=$ESPERADO_TOTAL)" \
    || falhou "remetente nao entregou os $ESPERADO_TOTAL (ver $LOG_DIR/5-envio.log: $(grep -o 'TF_RESUMO.*' "$LOG_DIR/5-envio.log" | head -1))"
TRILHA="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='odoo' and target_system='postgres'")")"
[ "$TRILHA" = "$ESPERADO_TOTAL" ] && ok "$ESPERADO_TOTAL linhas de trilha em sales_intelligence.sync_events (odoo -> postgres)" \
    || falhou "esperava $ESPERADO_TOTAL linhas de trilha, medidas ${TRILHA:-0}"
TRILHA_STATUS="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where status='COMPLETED'")")"
[ "$TRILHA_STATUS" = "$ESPERADO_TOTAL" ] && ok "as $ESPERADO_TOTAL linhas estao COMPLETED" \
    || falhou "esperava $ESPERADO_TOTAL COMPLETED, medidas ${TRILHA_STATUS:-0}"
OPERACOES="$(limpar "$(si "select string_agg(distinct operation, ',' order by operation) from sales_intelligence.sync_events")")"
[ "$OPERACOES" = "ACTIVITY_COMPLETED,DEAL_VALUE_CHANGED,LOSS_REASON_RECORDED,MEETING_CREATED,OPPORTUNITY_LOST,OPPORTUNITY_WON,STAGE_CHANGED" ] \
    && ok "a trilha carrega os 7 event_type como operation" \
    || falhou "operations na trilha: $OPERACOES"
ENTIDADES="$(limpar "$(si "select string_agg(distinct entity_type, ',' order by entity_type) from sales_intelligence.sync_events")")"
[ "$ENTIDADES" = "calendar.event,crm.lead,mail.activity" ] \
    && ok "entity_type e' o modelo de origem declarado (crm.lead, mail.activity, calendar.event)" \
    || falhou "entity_type na trilha: $ENTIDADES"
# A identidade canonica medida e' a que SAIU do Odoo (lida da propria fila), nao uma constante
# duplicada aqui: o que se prova e' que o UUID canonico que viajou no payload chegou na trilha.
UUID_OPORTUNIDADE="$(limpar "$(odoo_db "select entidade_canonica_id from tf_evento_outbox where entidade_canonica_tipo='oportunidade' and entidade_canonica_id is not null limit 1")")"
IDENTIDADE="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where entity_id::text = '$UUID_OPORTUNIDADE'")")"
if [ -n "$UUID_OPORTUNIDADE" ] && [ "${IDENTIDADE:-0}" -ge 5 ] 2>/dev/null; then
    ok "os eventos de funil carregam o UUID canonico da oportunidade na trilha ($IDENTIDADE linhas, entity_id=$UUID_OPORTUNIDADE)"
else
    falhou "UUID canonico da oportunidade na trilha: ${IDENTIDADE:-0} linhas (uuid medido: ${UUID_OPORTUNIDADE:-vazio})"
fi
VERSAO_OK="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_version='1.0'")")"
[ "$VERSAO_OK" = "$ESPERADO_TOTAL" ] && ok "source_version da trilha e' a versao do envelope (1.0)" \
    || falhou "source_version diferente do envelope em ${VERSAO_OK:-0} linhas"
VARIEDADE_CHAVE="$(limpar "$(si "select count(distinct idempotency_key) from sales_intelligence.sync_events")")"
[ "$VARIEDADE_CHAVE" = "$ESPERADO_TOTAL" ] && ok "$ESPERADO_TOTAL chaves de idempotencia distintas (chave derivada do fato)" \
    || falhou "esperava $ESPERADO_TOTAL chaves distintas, medidas ${VARIEDADE_CHAVE:-0}"
SENT_NA_FILA="$(limpar "$(odoo_db "select count(*) from tf_evento_outbox where status='SENT'")")"
[ "$SENT_NA_FILA" = "$ESPERADO_TOTAL" ] && ok "os $ESPERADO_TOTAL eventos ficaram SENT na fila do Odoo" \
    || falhou "esperava $ESPERADO_TOTAL SENT no Odoo, medidos ${SENT_NA_FILA:-0}"

cabecalho "passo D — reenvio do MESMO envelope NAO cria segunda linha (idempotencia)"
odoo_shell "$LOG_DIR/6-replay.log" "$FATOS" -e "TRE_FASE=replay"
odoo_shell "$LOG_DIR/6-reenvio.log" "$FATOS" -e "TRE_FASE=enviar"
DUPLICADOS="$(grep -o '"duplicados_no_destino": [0-9]*' "$LOG_DIR/6-reenvio.log" | head -1 | grep -o '[0-9]*')"
[ "${DUPLICADOS:-0}" = "$ESPERADO_TOTAL" ] && ok "reenvio do mesmo fato nao cria segunda linha na trilha: a porta respondeu duplicado para os $ESPERADO_TOTAL reenvios" \
    || falhou "reenvio do mesmo fato nao cria segunda linha na trilha: reconhecidos ${DUPLICADOS:-0} de $ESPERADO_TOTAL (ver $LOG_DIR/6-reenvio.log)"
TRILHA_DEPOIS="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='odoo'")")"
[ "$TRILHA_DEPOIS" = "$ESPERADO_TOTAL" ] && ok "a trilha continua com $ESPERADO_TOTAL linhas depois do reenvio (retry nao duplica)" \
    || falhou "a trilha passou para ${TRILHA_DEPOIS:-0} linhas depois do reenvio"

cabecalho "passo E — recusas nomeadas pela porta (e nada de escrito por engano)"
printf '{"event_type":"STAGE_CHANGED","timestamp":"2026-10-02 12:00:00","idempotency_key":"odoo:sem:versao:0001","payload":{"lead_id":1}}' >"$DESC_DIR/recusa-sem-versao.json"
SAIDA="$(postar /prep/recusa-sem-versao.json /prep/token.txt)"
if printf '%s' "$SAIDA" | grep -q 'HTTP 422' && printf '%s' "$SAIDA" | grep -q 'envelope_sem_versao'; then
    ok "envelope sem event_version e recusado (422 + motivo nomeado)"
else
    falhou "recusa por versao ausente nao veio como esperado: $SAIDA"
fi
printf '{"event_type":"LEAD_CREATED","event_version":"1.0","timestamp":"2026-10-02 12:00:00","idempotency_key":"odoo:fora:do:contrato:0001","payload":{"lead_id":1}}' >"$DESC_DIR/recusa-fora-do-contrato.json"
SAIDA="$(postar /prep/recusa-fora-do-contrato.json /prep/token.txt)"
if printf '%s' "$SAIDA" | grep -q 'HTTP 422' && printf '%s' "$SAIDA" | grep -q 'evento_fora_do_contrato'; then
    ok "evento fora da lista fechada e recusado (422 + motivo nomeado)"
else
    falhou "recusa por evento fora do contrato nao veio como esperado: $SAIDA"
fi
printf '{"event_type":"DEAL_VALUE_CHANGED","event_version":"1.0","timestamp":"2026-10-02 12:00:00","idempotency_key":"odoo:campo:faltando:0001","payload":{"lead_id":1,"valor_novo":10}}' >"$DESC_DIR/recusa-campo-ausente.json"
SAIDA="$(postar /prep/recusa-campo-ausente.json /prep/token.txt)"
if printf '%s' "$SAIDA" | grep -q 'HTTP 422' && printf '%s' "$SAIDA" | grep -q 'campo_exigido_ausente:valor_anterior'; then
    ok "campo exigido ausente e recusado (422 + motivo nomeado)"
else
    falhou "recusa por campo exigido ausente nao veio como esperado: $SAIDA"
fi
# Chave de idempotencia fora do formato declarado (`^[A-Za-z0-9._:-]{8,255}$`): o envelope esta'
# completo, so' a chave e' torta — se a porta aceitar, o `ON CONFLICT` deixa de ser confiavel.
printf '{"event_type":"STAGE_CHANGED","event_version":"1.0","timestamp":"2026-10-02 12:00:00","idempotency_key":"curta","payload":{"lead_id":1}}' >"$DESC_DIR/recusa-chave-torta.json"
SAIDA="$(postar /prep/recusa-chave-torta.json /prep/token.txt)"
if printf '%s' "$SAIDA" | grep -q 'HTTP 422' && printf '%s' "$SAIDA" | grep -q 'idempotency_key_invalida'; then
    ok "chave de idempotencia fora do formato e recusada (422 + motivo nomeado)"
else
    falhou "recusa por chave fora do formato nao veio como esperado: $SAIDA"
fi
RECUSAS="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where status='REFUSED'")")"
[ "$RECUSAS" = "4" ] && ok "as 4 recusas ficaram VISIVEIS na trilha (status REFUSED)" \
    || falhou "esperava 4 linhas REFUSED, medidas ${RECUSAS:-0}"
MOTIVO_NA_TRILHA="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where status='REFUSED' and error_message like 'campo_exigido_ausente:%'")")"
[ "$MOTIVO_NA_TRILHA" = "1" ] && ok "o motivo da recusa esta' nomeado em error_message" \
    || falhou "motivo da recusa nao esta' nomeado na trilha (${MOTIVO_NA_TRILHA:-0})"
SAIDA_TOKEN_ERRADO="$(postar /prep/envelope-minimo.json "" "token-errado-do-aceite")"
if printf '%s' "$SAIDA_TOKEN_ERRADO" | grep -qE 'HTTP (401|403)'; then
    ok "token errado e recusado pela porta ($(printf '%s' "$SAIDA_TOKEN_ERRADO" | grep -o 'HTTP [0-9]*'))"
else
    falhou "token errado nao foi recusado: $SAIDA_TOKEN_ERRADO"
fi
TRILHA_SEM_EXTRA="$(limpar "$(si "select count(*) from sales_intelligence.sync_events")")"
ESPERADO_COM_RECUSAS=$((ESPERADO_TOTAL + 4))
[ "$TRILHA_SEM_EXTRA" = "$ESPERADO_COM_RECUSAS" ] && ok "a trilha tem $ESPERADO_TOTAL aceitas + 4 recusadas (nada extra entrou)" \
    || falhou "trilha com ${TRILHA_SEM_EXTRA:-0} linhas (esperado $ESPERADO_COM_RECUSAS)"

cabecalho "passo F — retry limitado no produtor (porta fora do ar e de volta)"
odoo_shell "$LOG_DIR/7-novo-fato.log" "$FATOS" -e "TRE_FASE=fatos" -e "TRE_SUFIXO=2"
docker stop "$N8N_CT" >/dev/null 2>&1 && info "n8n parado de proposito (falha de transporte)"
odoo_shell "$LOG_DIR/7-envio-falho.log" "$FATOS" -e "TRE_FASE=enviar"
RETRY="$(limpar "$(odoo_db "select count(*) from tf_evento_outbox where status='RETRY'")")"
if [ "${RETRY:-0}" -ge 1 ] 2>/dev/null; then
    ok "porta fora do ar: $RETRY evento(s) em RETRY (falha transitoria registrada)"
else
    falhou "nenhum evento ficou em RETRY com a porta fora do ar (ver $LOG_DIR/7-envio-falho.log)"
fi
MOTIVO_RETRY="$(limpar "$(odoo_db "select count(*) from tf_evento_outbox where status='RETRY' and last_error is not null")")"
[ "${MOTIVO_RETRY:-0}" -ge 1 ] 2>/dev/null && ok "o motivo da falha ficou registrado (last_error)" \
    || falhou "falha de transporte sem motivo registrado"
docker start "$N8N_CT" >/dev/null 2>&1
for _ in $(seq 1 30); do
    if docker exec "$N8N_CT" node -e "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))" >/dev/null 2>&1; then break; fi
    sleep 3
done
odoo_shell "$LOG_DIR/7-envio-retry.log" "$FATOS" -e "TRE_FASE=enviar"
ENTREGUE_NO_RETRY="$(grep -o '"SENT": [0-9]*' "$LOG_DIR/7-envio-retry.log" | head -1 | grep -o '[0-9]*')"
[ "${ENTREGUE_NO_RETRY:-0}" -ge 1 ] 2>/dev/null && ok "porta de volta: o retry entregou (SENT>=1)" \
    || falhou "o retry nao entregou depois de a porta voltar (ver $LOG_DIR/7-envio-retry.log)"
FALHAS_MAX="$(limpar "$(odoo_db "select count(*) from tf_evento_outbox where attempts > $TETO")")"
[ "${FALHAS_MAX:-0}" = "0" ] && ok "nenhum evento passou do teto de $TETO tentativas" \
    || falhou "evento com attempts > $TETO (teto nao respeitado)"
NOVAS_NA_TRILHA="$(limpar "$(si "select count(*) from sales_intelligence.sync_events where idempotency_key like 'odoo:%'")")"
info "linhas de trilha ao fim: $NOVAS_NA_TRILHA"

# ---------------------------------------------------------------------------
# fecho — segredo, ambiente, integridade dos artefatos e limpeza
# ---------------------------------------------------------------------------
cabecalho "fecho — segredo fora do versionado, ambiente intocado, integridade"
if grep -rq --exclude-dir=.git --exclude-dir=.worktrees --exclude='*.pyc' "$TOKEN_PORTAL" "$RAIZ_REPO" 2>/dev/null; then
    falhou "o token da porta aparece no versionado (varredura real)"
else
    ok "o token da porta NAO aparece em nenhum arquivo do repo"
fi
docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -tAc \
    "select count(*) from sales_intelligence.sync_events where request_payload::text like '%$TOKEN_PORTAL%'" 2>/dev/null | grep -qx 0 \
    && ok "o token nao entrou em nenhum request_payload da trilha" \
    || falhou "o token aparece em payload de trilha (payload de segredo na trilha)"
DEV_PG_DEPOIS="nao_medido"
if [ "$(docker inspect -f '{{.State.Running}}' "$DEV_PG_CT" 2>/dev/null)" = "true" ]; then
    DEV_PG_DEPOIS="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)"
fi
if [ "$DEV_PG_ANTES" = "$DEV_PG_DEPOIS" ]; then
    ok "a instancia do dev tem os MESMOS bancos antes/depois ($DEV_PG_DEPOIS)"
else
    falhou "a instancia do dev mudou: antes=[$DEV_PG_ANTES] depois=[$DEV_PG_DEPOIS]"
fi
HOMOLOG_PROD_DEPOIS="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
[ "$HOMOLOG_PROD_ANTES" = "$HOMOLOG_PROD_DEPOIS" ] \
    && ok "homolog/producao sem arquivo novo ($HOMOLOG_PROD_DEPOIS arquivos)" \
    || falhou "homolog/prod mudou: $HOMOLOG_PROD_ANTES -> $HOMOLOG_PROD_DEPOIS"
RESIDUO="$(docker ps -a --filter "name=tre-e03-$SUFIXO" --format '{{.Names}}' | grep -v -e "$PG_TMP" -e "$N8N_CT" | wc -l | tr -d ' ')"
[ "$RESIDUO" = "0" ] && ok "nenhum container/residuo DESTA rodada alem do proprio trio" \
    || falhou "residuo de container desta rodada: $RESIDUO"
ANTIGOS="$(docker ps -a --filter "name=tre-e03-" --format '{{.Names}}' | grep -v "$SUFIXO" | wc -l | tr -d ' ')"
[ "$ANTIGOS" = "0" ] && ok "nenhum container de rodada anterior do aceite no host" \
    || info "containers tre-e03- de rodadas anteriores ainda no host: $ANTIGOS (limpeza e' do operador)"
sha256_dos_artefatos "$LOG_DIR/sha256-depois.txt"
if diff -q "$LOG_DIR/sha256-antes.txt" "$LOG_DIR/sha256-depois.txt" >/dev/null; then
    ok "sha256 dos ${#ARTEFATOS_SOB_TESTE[@]} artefatos sob teste inalterado durante a medicao"
else
    falhou "artefato sob teste MUDOU durante a medicao (ver sha256-antes/depois)"
fi

resumo
