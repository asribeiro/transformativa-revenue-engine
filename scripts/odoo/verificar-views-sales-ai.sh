#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W2-E06-T01 — views do Sales AI no modulo `transformativa_sales_ai`.
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 as views exibem os campos das entidades customizadas, sem erro de renderizacao;
#   AC2 o acesso respeita o perfil de usuario (quem nao pode ver, nao ve);
#   AC3 evidencia de abertura das views (lista/formulario).
#
# TEST PLAN (executado por este script, na VPS do dev, em banco descartavel):
#   passo 1/6 instalacao em banco limpo            -> log + estado lido no banco
#   passo 2/6 testes do modulo (--test-enable)     -> relatorio do runner + classe do aceite
#   passo 3/6 views/menus/acoes lidos NO BANCO     -> arch gravada, grupos, hierarquia do menu
#   passo 4/6 prova independente                   -> scripts/odoo/provar_views_sales_ai.py
#   passo 5/6 rollback (desinstalacao pelo ORM)    -> views/menus/acoes do modulo = 0
#   passo 6/6 limpeza + instancia do dev intacta   -> nada nasce fora do dev (ADR-005)
#
# POR QUE UMA PROVA ALEM DOS TESTES: o passo 2 e' a suite do proprio autor. O passo 4 monta a
# cena de novo (usuario MEMBRO com o grupo do modulo x usuario RESTRITO sem ele, com os MESMOS
# grupos de CRM) e mede pelo ORM renderizando a view como cada um — a mesma chamada (`get_view`)
# que o web client faz para abrir a lista e o formulario. Sem os dois, um erro nos testes do
# modulo seria invisivel.
#
# ISOLAMENTO (mesma razao do verificador do E03): o Odoo do dev abre sessao em QUALQUER banco
# novo da instancia `pg-odoo-dev`; por isso este verificador sobe a SUA propria dupla
# descartavel (`postgres:16` + `odoo:19.0`, as imagens do par de dev) em rede propria, com
# nomes prefixados por `e06t01-`, e NAO usa `pg-odoo-dev`, `odoo_dev` nem `/opt/tre/repo`.
#
# Uso (na VPS, a partir de ARQUIVO — nunca por stdin, ver armadilha do `docker compose run`):
#   bash verificar-views-sales-ai.sh
#   bash verificar-views-sales-ai.sh --apenas-artefatos      (sem a suite de testes do Odoo)
#   bash verificar-views-sales-ai.sh --banco tre_e06t01_outro
#   bash verificar-views-sales-ai.sh --prova-de-dente        (baseline NAO mutado, que tem de
#        medir VIEWS_OK, + duas mutacoes; exige a assinatura de falha de cada uma e escreve os
#        logs em TRE_LOG_DIR/dente/ — nunca no diretorio do aceite)
#
# Variaveis: TRE_MODULO, TRE_MODULO_DIR, TRE_ANCORA_DIR (diretorio de ancora do modo dente; por
# padrao o modulo no checkout ao lado deste script), TRE_PROVA, TRE_DESINSTALADOR, TRE_BANCO,
# TRE_IMAGEM, TRE_IMAGEM_PG, TRE_PG_USER, TRE_MIN_TESTS, TRE_MIN_METODOS_VIEWS, TRE_MIN_ITENS_PROVA,
# TRE_LOG_DIR, TRE_DEV_PG_CT, TRE_MANTER_BANCO=1 (nao limpa no fim).
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir
# ============================================================================
set -u

MODULO="${TRE_MODULO:-transformativa_sales_ai}"
MODELO="${TRE_MODELO:-tf.process.opportunity}"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SELF="$(readlink -f "$0")"   # sub-runs do modo dente chamam ESTE arquivo pelo caminho resolvido
MODULO_DIR="${TRE_MODULO_DIR:-/opt/tre/dev/modulos/$MODULO}"
PROVA="${TRE_PROVA:-$AQUI/provar_views_sales_ai.py}"
DESINSTALADOR="${TRE_DESINSTALADOR:-$AQUI/desinstalar_modulo.py}"
BANCO="${TRE_BANCO:-tre_e06t01_views}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
MIN_TESTS="${TRE_MIN_TESTS:-50}"
MIN_METODOS_VIEWS="${TRE_MIN_METODOS_VIEWS:-10}"
MIN_ITENS_PROVA="${TRE_MIN_ITENS_PROVA:-21}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-views-sales-ai}"
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
PG_TMP="e06t01-pg-$SUFIXO"
NET_TMP="e06t01-net-$SUFIXO"
DESC_DIR=""
mkdir -p "$LOG_DIR"

ok()     { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()   { printf 'INFO  %s\n' "$*"; }
cabecalho() { printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: VIEWS_OK ($ITENS itens, 0 falhas) modulo=$MODULO modelo=$MODELO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: VIEWS_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO modelo=$MODELO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? baseline NAO mutado (tem de medir VERDE) + 2 mutacoes
#   dente 1: o recorte por grupo das views do parceiro/lead removido (pagina e view) -> o
#            usuario RESTRITO passa a ver a secao do Sales AI -> tem de reprovar (AC2)
#   dente 2: a view de formulario do modelo sai do manifesto -> a view nao esta no banco ->
#            tem de reprovar (AC1/AC3: a lista/formulario do modulo nao existe)
#
# FAIL-CLOSED (revisao independente da rodada 1 deste card; a classe ja' tinha sido consertada
# no E04-T01 `a539802` — defeito `t_e1f62fae` — e no E04-T02 `e8bfe71`): a versao anterior
# aceitava QUALQUER `RESULTADO: VIEWS_FALHOU` como "o dente mordeu" (fail-open). Medido pela
# revisao: com `TRE_MODULO_DIR` inexistente, `DOCKER_HOST` invalido ou imagem ausente as provas
# morriam na GUARDA antes de medir e o comando devolvia `VIEWS_DENTE_OK (2 provas, 0 falhas)`,
# exit 0 — verde sem exercitar dente nenhum. Agora o modo dente:
#   1. CONFERE A ANCORA: o `TRE_MODULO_DIR` tem de ser o artefato DESTE card, arquivo a arquivo
#      (sha256 de tudo o que existe no modulo do `TRE_ANCORA_DIR`, por padrao o checkout ao lado
#      deste script) — diretorio que nao e' o artefato do card nao prova nada e o modo RECUSA:
#      o default `/opt/tre/dev/modulos/<modulo>` e' copia compartilhada de outro card (medido,
#      sem o diretorio `views/` do E06) e uma copia com o README de rodada anterior tambem
#      diverge (foi o defeito apontado na revisao);
#   2. roda o caminho NAO mutado (baseline = os 6 passos do aceite, superconjunto do que os
#      dois dentes medem) e EXIGE `VIEWS_OK`; sem baseline verde nao existe prova de dente
#      (`VIEWS_DENTE_FALHOU (baseline nao medido)`, exit 1, nenhuma mutacao sobe);
#   3. exige de CADA prova a SUA assinatura de falha (o texto que so' aquela mutacao produz;
#      mais de uma separadas por `;;`), o aceite inteiro medido (`passo 6/6`) e nenhum
#      marcador de aborto de guarda;
#   4. escreve em LOG PROPRIO (`$TRE_LOG_DIR/dente/prova-N`) e nao escreve NADA em
#      `$TRE_LOG_DIR`: guarda fail-closed compara o sha256 dos arquivos do diretorio do
#      aceite antes/depois (defeito TRE-W2-E03-T01-D02 — conserto `c389223`; terceira
#      incidencia, agora com guarda no proprio harness).
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e06t01-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_LOG_DIR="${LOG_DIR}/dente"
    mkdir -p "$DENTE_LOG_DIR/baseline" "$DENTE_LOG_DIR/prova-1" "$DENTE_LOG_DIR/prova-2"
    DENTE_FALHAS=0
    GUARDA_FALHAS=0
    VIEW_PARCEIRO="views/res_partner_views.xml"
    VIEW_OPORTUNIDADE="views/tf_process_opportunity_views.xml"
    MANIFESTO="__manifest__.py"
    # Artefato do card = todos os arquivos do modulo no checkout do card (o proprio modulo, os
    # modelos dos cards irmaos, README, etc.). A comparacao e' arquivo a arquivo: um `MODULO_DIR`
    # que seja copia de OUTRO card (o default compartilhado) ou copia com um arquivo de rodada
    # antiga (foi o caso do README na revisao) diverge e o modo RECUSA.
    ANCORA_DIR="${TRE_ANCORA_DIR:-$AQUI/../../odoo/addons/$MODULO}"
    foto_ancora() { # $1=diretorio do modulo -> "sha256  caminho" de todo arquivo (sem __pycache__)
        (cd "$1" 2>/dev/null && find . -type f -not -path '*__pycache__*' -printf '%P\n' | LC_ALL=C sort | xargs -r sha256sum)
    }
    # Guarda do defeito D02: fotografia dos arquivos do diretorio do ACEITE (nivel 1 — o modo
    # dente escreve em $DENTE_LOG_DIR, subdiretorio). Qualquer escrita aqui reprova a rodada.
    foto_logs_aceite() {
        (cd "$LOG_DIR" 2>/dev/null && find . -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort | xargs -r sha256sum 2>/dev/null)
    }
    # $1=rotulo  $2=assinatura(s) de falha exigida(s), separadas por `;;`  $3=passo que tem de
    # ter sido medido  $4=saida do aceite mutado
    confere_dente() {
        local rotulo="$1" assinaturas="$2" passo="$3" saida="$4" motivo="" assinatura resto
        if ! printf '%s' "$saida" | grep -q 'RESULTADO: VIEWS_FALHOU'; then
            motivo="a mutacao NAO reprovou o aceite — o item nao mede o que promete"
        elif printf '%s' "$saida" | grep -qE 'nada a medir|modulo ausente|artefato ausente|docker nao responde|nao consegui criar|nao consegui subir|nao ficou pronto|e do ambiente|fora do padrao descartavel|script ausente|imagem .* ausente'; then
            motivo="a prova ABORTOU numa guarda do ambiente, antes de medir — aborto nao e' prova de dente"
        elif ! printf '%s' "$saida" | grep -qF -- "$passo"; then
            motivo="a prova nao chegou ao passo '$passo' (morreu antes de medir) — aborto nao e' prova de dente"
        else
            resto="$assinaturas"
            while [ -n "$resto" ]; do
                assinatura="${resto%%;;*}"
                if [ "$assinatura" = "$resto" ]; then resto=""; else resto="${resto#*;;}"; fi
                if ! printf '%s' "$saida" | grep -qF -- "$assinatura"; then
                    motivo="a prova reprovou por outro motivo: falta a assinatura esperada '$assinatura'"
                    break
                fi
            done
        fi
        if [ -z "$motivo" ]; then
            echo "OK    $rotulo: a mutacao REPROVOU o aceite com a assinatura esperada (o item tem dente)"
        else
            echo "FALHOU $rotulo: $motivo"
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
        fi
    }

    # --- ancora do artefato: sem ela, "reprovar" pode ser so' o ambiente errado ---------------
    cabecalho "ancora do artefato: o TRE_MODULO_DIR tem de ser o modulo DESTE card"
    if [ ! -d "$ANCORA_DIR" ]; then
        echo "FALHOU ancora: diretorio de referencia ausente ($ANCORA_DIR) — sem ancora nao ha como saber se o artefato medido e' o do card (aponte TRE_ANCORA_DIR para o checkout do card)"
        echo '---'
        echo "RESULTADO: VIEWS_DENTE_FALHOU (artefato nao ancorado — nenhum dente exercitado) modulo=$MODULO"
        exit 1
    fi
    ANCORA_REF="$(foto_ancora "$ANCORA_DIR")"
    if [ -z "$ANCORA_REF" ]; then
        echo "FALHOU ancora: $ANCORA_DIR nao tem nenhum arquivo do modulo — nao e' o artefato deste card"
        echo '---'
        echo "RESULTADO: VIEWS_DENTE_FALHOU (artefato nao ancorado — nenhum dente exercitado) modulo=$MODULO"
        exit 1
    fi
    ANCORA_N=0
    ANCORA_DIVERGE=""
    while read -r sha arq; do
        [ -z "${arq:-}" ] && continue
        ANCORA_N=$((ANCORA_N + 1))
        if [ "$(sha256sum "$MODULO_DIR/$arq" 2>/dev/null | cut -d' ' -f1)" != "$sha" ]; then
            ANCORA_DIVERGE="$ANCORA_DIVERGE $arq"
        fi
    done <<<"$ANCORA_REF"
    if [ -z "$ANCORA_DIVERGE" ]; then
        echo "OK    ancora: o artefato em $MODULO_DIR e' o do checkout ($ANCORA_DIR) — sha256 identico em $ANCORA_N arquivos"
    else
        echo "FALHOU ancora: o artefato medido NAO e' o do card — arquivo(s) divergente(s) contra $ANCORA_DIR:"
        for a in $ANCORA_DIVERGE; do
            echo "      $a"
            echo "        referencia: $(printf '%s\n' "$ANCORA_REF" | grep -F -- " $a" | cut -d' ' -f1)"
            echo "        medido:     $(sha256sum "$MODULO_DIR/$a" 2>/dev/null | cut -d' ' -f1)"
        done
        echo '---'
        echo "RESULTADO: VIEWS_DENTE_FALHOU (artefato nao ancorado — nenhum dente exercitado) modulo=$MODULO"
        exit 1
    fi

    ACEITE_ANTES="$(foto_logs_aceite)"
    info "logs do aceite: $LOG_DIR  |  logs do dente: $DENTE_LOG_DIR (caminhos separados)"

    # --- baseline: o caminho NAO mutado (6 passos) tem de medir VERDE antes das mutacoes ------
    cabecalho "baseline: caminho NAO mutado (6 passos do aceite) tem de medir VIEWS_OK"
    BASE="$(TRE_MODULO_DIR="$MODULO_DIR" TRE_BANCO="${BANCO}_baseline" TRE_LOG_DIR="$DENTE_LOG_DIR/baseline" bash "$SELF" 2>&1)"
    printf '%s\n' "$BASE" >"$DENTE_LOG_DIR/dente-0-baseline.out"
    printf '%s\n' "$BASE" | grep -E '^(FALHOU|RESULTADO)' | tail -3
    if printf '%s' "$BASE" | grep -q 'RESULTADO: VIEWS_OK'; then
        echo 'OK    baseline: o caminho NAO mutado mediu VIEWS_OK — os dentes tem contra o que medir'
    else
        echo "FALHOU baseline: o caminho NAO mutado NAO mediu verde: $(printf '%s' "$BASE" | grep '^FALHOU ' | head -3 | tr '\n' ' ')"
        echo '---'
        echo "RESULTADO: VIEWS_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado) modulo=$MODULO"
        exit 1
    fi

    cabecalho "prova de dente 1: recorte por grupo da secao do parceiro removido (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    # O que se mutila e' o atributo `groups` da pagina da secao (no Odoo 19 o registro de view
    # herdada NAO pode carregar `groups` — ver §6 do runbook); sem ele a secao fica aberta a todos.
    sed -i 's/ groups="transformativa_sales_ai.group_tf_sales_ai_user"//g' \
        "$DENTE_DIR/m1/$VIEW_PARCEIRO"
    if grep -q 'group_tf_sales_ai_user' "$DENTE_DIR/m1/$VIEW_PARCEIRO"; then
        echo 'FALHOU dente 1: a mutacao nao pegou no arquivo (prova sem valor)'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    else
        D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_BANCO="${BANCO}_d1" TRE_LOG_DIR="$DENTE_LOG_DIR/prova-1" bash "$SELF" 2>&1)"
        printf '%s\n' "$D1" >"$DENTE_LOG_DIR/dente-1-sem-recorte.out"
        printf '%s\n' "$D1" | grep -E '^(FALHOU|RESULTADO)' | tail -6
        confere_dente "dente 1" \
            'AC2 a view herdada view_partner_form_tf_sales_ai nao recorta a secao pelo grupo do vendedor;;prova independente: 21 itens, 2 falha' \
            'passo 6/6' "$D1"
    fi

    cabecalho "prova de dente 2: formulario do modelo fora do manifesto (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    sed -i "\|'$VIEW_OPORTUNIDADE',|d" "$DENTE_DIR/m2/$MANIFESTO"
    if grep -q "$VIEW_OPORTUNIDADE" "$DENTE_DIR/m2/$MANIFESTO"; then
        echo 'FALHOU dente 2: a mutacao nao pegou no manifesto (prova sem valor)'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    else
        D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_d2" TRE_LOG_DIR="$DENTE_LOG_DIR/prova-2" bash "$SELF" 2>&1)"
        printf '%s\n' "$D2" >"$DENTE_LOG_DIR/dente-2-view-fora-do-manifesto.out"
        printf '%s\n' "$D2" | grep -E '^(FALHOU|RESULTADO)' | tail -6
        confere_dente "dente 2" \
            'views do modulo no banco: 2 (esperado 5);;AC1 a busca do tf.process.opportunity nao traz tf_uuid' \
            'passo 6/6' "$D2"
    fi

    # Guarda D02: o diretorio do aceite tem de sair das provas com o MESMO conteudo.
    ACEITE_DEPOIS="$(foto_logs_aceite)"
    if [ "$ACEITE_ANTES" = "$ACEITE_DEPOIS" ]; then
        if [ -z "$ACEITE_ANTES" ]; then
            echo 'OK    diretorio do aceite sem arquivo no inicio e no fim (o modo dente nao escreveu nele)'
        else
            echo "OK    logs do aceite intactos depois das provas ($(printf '%s\n' "$ACEITE_ANTES" | grep -c . | tr -d ' ') arquivo(s) com sha256 identico)"
        fi
    else
        echo 'FALHOU o modo dente mexeu no diretorio do aceite — evidencia do aceite destruida (defeito TRE-W2-E03-T01-D02 de volta)'
        printf '%s\n' "$ACEITE_ANTES" | sed 's/^/      antes:  /'
        printf '%s\n' "$ACEITE_DEPOIS" | sed 's/^/      depois: /'
        GUARDA_FALHAS=$((GUARDA_FALHAS + 1))
    fi

    echo '---'
    if [ "$DENTE_FALHAS" -eq 0 ] && [ "$GUARDA_FALHAS" -eq 0 ]; then
        echo "RESULTADO: VIEWS_DENTE_OK (2 provas, 0 falhas) modulo=$MODULO logs_aceite=$LOG_DIR logs_dente=$DENTE_LOG_DIR"
        exit 0
    fi
    echo "RESULTADO: VIEWS_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente, $GUARDA_FALHAS falha(s) na guarda do diretorio do aceite) modulo=$MODULO"
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
xmlid() { # $1=model $2=name -> res_id (ou vazio)
    psql_bd "$BANCO" "select res_id from ir_model_data where module = '$MODULO' and model = '$1' and name = '$2'"
}
campo_na_arch() { # $1=xml id da view, $2=nome do campo
    # `arch_db` e' jsonb (campo traduzivel): o valor cru do idioma padrao sai por `->>'en_US'`.
    # Comparar com `arch_db::text` nao serve — o dump do jsonb escapa as aspas do XML (`name=\"x\"`).
    psql_bd "$BANCO" "select count(*) from ir_ui_view v join ir_model_data d on d.res_id = v.id \
        where d.module = '$MODULO' and d.name = '$1' \
        and coalesce(v.arch_db->>'en_US', '') like '%' || 'name=\"$2\"' || '%'"
}

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
    # Identidade do alvo medido (informativo — quem bloqueia e' o modo dente, pela ancora): o
    # aceite mede um diretorio que pode ser copia. Rodada de revisao anterior pegou uma copia com
    # o README de uma rodada antiga; o bloco sha256 acima e' o que permite auditar arquivo a
    # arquivo, e o INFO abaixo diz na hora se a copia casa com o modulo do checkout.
    ANCORA_INFO="${TRE_ANCORA_DIR:-$AQUI/../../odoo/addons/$MODULO}"
    if [ -f "$ANCORA_INFO/__manifest__.py" ]; then
        if [ "$(sha256sum "$MODULO_DIR/__manifest__.py" | cut -d' ' -f1)" = "$(sha256sum "$ANCORA_INFO/__manifest__.py" | cut -d' ' -f1)" ]; then
            info "identidade: o manifesto medido casa com o checkout ($ANCORA_INFO)"
        else
            info "identidade: ATENCAO — o manifesto medido NAO casa com o checkout $ANCORA_INFO (TRE_MODULO_DIR fora do artefato do card)"
        fi
    fi
else
    falhou "modulo ausente em $MODULO_DIR (__manifest__.py nao encontrado)"; resumo
fi
for arquivo in "$PROVA" "$DESINSTALADOR"; do
    if [ -f "$arquivo" ]; then
        ok "script em disco: $arquivo (sha256 $(sha256sum "$arquivo" | cut -d' ' -f1))"
    else
        falhou "script ausente em $arquivo"; resumo
    fi
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
DESC_DIR="$(mktemp -d /tmp/verificacao-views-XXXXXX)"
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
# passo 1/6 — instalacao em banco limpo
# ---------------------------------------------------------------------------
cabecalho "passo 1/6 — instalacao em banco limpo"
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
# O carregamento das views e' o que o criterio 1 chama de "sem erro de renderizacao" na entrada:
# uma arch invalida faz a instalacao gritar ERROR de validacao de view.
VIEWS_ERRO="$(grep -cE '(^| )ERROR .*(ir\.ui\.view|Invalid view|view .*not found|does not exist)' "$LOG_ATUAL" || true)"
[ "$VIEWS_ERRO" = "0" ] && ok "log de instalacao sem erro de view (arch carregada sem erro)" \
    || falhou "$VIEWS_ERRO linha(s) de erro de view no log de instalacao"

# ---------------------------------------------------------------------------
# passo 2/6 — suite de testes do modulo (--test-enable)
# ---------------------------------------------------------------------------
LOG_ATUAL="$LOG_DIR/2-teste.log"
if [ "$MODO" = "artefatos" ]; then
    cabecalho "passo 2/6 — PULADO (--apenas-artefatos)"
    info "a suite do Odoo nao foi executada nesta rodada"
else
    cabecalho "passo 2/6 — testes do Odoo (--test-enable)"
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
    # O Odoo 19 escreve a reprovacao prefixada (`<hora> <pid> ERROR <banco> <modulo>: FAIL: TestX`):
    # o padrao tem de aceitar o prefixo, senao o item imprime OK com teste reprovado (a classe do
    # defeito t_578a4e4d, ja' cardada).
    FALHAS_TESTE="$(grep -cE '(^| )(FAIL|ERROR): [A-Za-z_]' "$LOG_ATUAL" || true)"
    [ "$FALHAS_TESTE" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
        || falhou "$FALHAS_TESTE linha(s) de teste reprovado(a) no log: $(grep -nE '(^| )(FAIL|ERROR): [A-Za-z_]' "$LOG_ATUAL" | head -3 | tr '\n' ' ')"
    # a classe do aceite TEM de ter rodado: suite verde sem a classe nao prova nada do card
    N_METODOS="$(grep -cE 'Starting TestViewsSalesAi\.test_' "$LOG_ATUAL" || true)"
    if [ "$N_METODOS" -ge "$MIN_METODOS_VIEWS" ]; then
        ok "classe de aceite TestViewsSalesAi rodou ($N_METODOS metodos no log)"
    else
        falhou "classe de aceite TestViewsSalesAi nao rodou inteira ($N_METODOS metodos no log, esperado >= $MIN_METODOS_VIEWS)"
    fi
    ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
    [ "$ESTADO" = "installed" ] && ok "modulo segue installed depois do teste" || falhou "estado depois do teste: '$ESTADO'"
fi

# ---------------------------------------------------------------------------
# passo 3/6 — views, menus e acoes do modulo (medido no banco, nao no XML)
# ---------------------------------------------------------------------------
cabecalho "passo 3/6 — views/menus/acoes lidos no banco"
TAB_ACAO="$(psql_bd "$BANCO" "select coalesce(to_regclass('ir_act_window')::text, to_regclass('ir_actions_act_window')::text, '')")"
[ -n "$TAB_ACAO" ] && ok "tabela da acao de janela localizada ($TAB_ACAO)" \
    || falhou "nao localizei a tabela da acao de janela (ir_act_window/ir_actions_act_window)"

N_VIEWS="$(psql_bd "$BANCO" "select count(*) from ir_model_data where module = '$MODULO' and model = 'ir.ui.view'")"
[ "$N_VIEWS" = "5" ] && ok "modulo declara 5 views (3 da oportunidade + 2 herdadas)" \
    || falhou "views do modulo no banco: $N_VIEWS (esperado 5)"
N_MENUS="$(psql_bd "$BANCO" "select count(*) from ir_model_data where module = '$MODULO' and model = 'ir.ui.menu'")"
[ "$N_MENUS" = "2" ] && ok "modulo declara 2 menus (Sales AI + Oportunidades)" \
    || falhou "menus do modulo no banco: $N_MENUS (esperado 2)"
N_ACOES="$(psql_bd "$BANCO" "select count(*) from ir_model_data where module = '$MODULO' and model = 'ir.actions.act_window'")"
[ "$N_ACOES" = "1" ] && ok "modulo declara 1 acao de janela" \
    || falhou "acoes de janela do modulo no banco: $N_ACOES (esperado 1)"

# AC1 — os campos das entidades customizadas estao na ARCH GRAVADA de cada view
for campo in name partner_id stage_id expected_revenue company_id; do
    N="$(campo_na_arch view_tf_process_opportunity_list "$campo")"
    [ "$N" = "1" ] && ok "AC1 lista do $MODELO traz o campo $campo" \
        || falhou "AC1 o campo $campo nao esta na lista do $MODELO (arch gravada)"
done
for campo in name partner_id company_id currency_id stage_id expected_revenue lost_reason_id tf_uuid; do
    N="$(campo_na_arch view_tf_process_opportunity_form "$campo")"
    [ "$N" = "1" ] && ok "AC1 formulario do $MODELO traz o campo $campo" \
        || falhou "AC1 o campo $campo nao esta no formulario do $MODELO (arch gravada)"
done
N="$(campo_na_arch view_tf_process_opportunity_search tf_uuid)"
[ "$N" = "1" ] && ok "AC1 busca do $MODELO traz o UUID canonico (caminho do contrato §3)" \
    || falhou "AC1 a busca do $MODELO nao traz tf_uuid"
for campo in tf_cnpj tf_domain tf_linkedin_url tf_company_id tf_priority_score; do
    N="$(campo_na_arch view_partner_form_tf_sales_ai "$campo")"
    [ "$N" = "1" ] && ok "AC1 view herdada do parceiro traz o campo $campo" \
        || falhou "AC1 o campo $campo nao esta na view herdada do parceiro"
done
for campo in tf_opportunity_id tf_priority_score tf_icp_score tf_automation_fit_score \
             tf_buying_signal_score tf_data_quality_score tf_score_version tf_priority_tier \
             tf_next_best_action tf_correlation_id tf_idempotency_key tf_last_sync_at \
             tf_last_event_type; do
    N="$(campo_na_arch view_crm_lead_form_tf_sales_ai "$campo")"
    [ "$N" = "1" ] && ok "AC1 view herdada do lead traz o campo $campo" \
        || falhou "AC1 o campo $campo nao esta na view herdada do lead"
done

# AC2 — o recorte por perfil esta no BANCO: o menu guarda o grupo em `ir_ui_menu_group_rel` e as
# views herdadas recortam a secao DENTRO da arch (no Odoo 19 o registro de view herdada NAO pode
# carregar `groups`: "Inherited view cannot have 'groups' defined on the record" — medido na
# primeira rodada deste aceite, ver runbook §6). O comportamento (quem ve/quem nao ve a view
# renderizada) e' medido no passo 4, com dois usuarios de verdade.
for view in view_partner_form_tf_sales_ai view_crm_lead_form_tf_sales_ai; do
    N="$(psql_bd "$BANCO" "select count(*) from ir_ui_view v join ir_model_data d on d.res_id = v.id \
        where d.module = '$MODULO' and d.name = '$view' \
        and v.arch_db::text like '%' || 'group_tf_sales_ai_user' || '%'")"
    [ "$N" = "1" ] && ok "AC2 a view herdada $view recorta a secao pelo grupo do vendedor" \
        || falhou "AC2 a view herdada $view nao recorta a secao pelo grupo do vendedor (medido $N)"
done
N="$(psql_bd "$BANCO" "select count(*) from ir_ui_menu_group_rel r join ir_model_data dm on dm.res_id = r.menu_id \
    join ir_model_data dg on dg.res_id = r.gid where dm.module = '$MODULO' and dm.name = 'menu_tf_sales_ai_root' \
    and dg.module = '$MODULO' and dg.name = 'group_tf_sales_ai_user'")"
[ "$N" = "1" ] && ok "AC2 o menu do Sales AI esta preso ao grupo do vendedor" \
    || falhou "AC2 o menu do Sales AI nao esta preso ao grupo do vendedor (medido $N)"

# heranca e hierarquia (a view abre sobre a view certa do Odoo)
N="$(psql_bd "$BANCO" "select count(*) from ir_ui_view v join ir_model_data d on d.res_id = v.id \
    where d.module = '$MODULO' and d.name = 'view_partner_form_tf_sales_ai' \
    and v.inherit_id = $(psql_bd "$BANCO" "select res_id from ir_model_data where module = 'base' and name = 'view_partner_form'")")"
[ "$N" = "1" ] && ok "AC1 a view do parceiro herda o formulario do parceiro do base" \
    || falhou "AC1 a view do parceiro nao herda base.view_partner_form"
N="$(psql_bd "$BANCO" "select count(*) from ir_ui_view v join ir_model_data d on d.res_id = v.id \
    where d.module = '$MODULO' and d.name = 'view_crm_lead_form_tf_sales_ai' \
    and v.inherit_id = $(psql_bd "$BANCO" "select res_id from ir_model_data where module = 'crm' and name = 'crm_lead_view_form'")")"
[ "$N" = "1" ] && ok "AC1 a view do lead herda o formulario do lead do crm" \
    || falhou "AC1 a view do lead nao herda crm.crm_lead_view_form"

# AC3 — o par lista/formulario esta ligado a acao e ao menu (o caminho de abertura)
if [ -n "$TAB_ACAO" ]; then
    ACAO="$(psql_bd "$BANCO" "select res_model || '|' || view_mode from $TAB_ACAO where id = $(xmlid ir.actions.act_window action_tf_process_opportunity)")"
    [ "$ACAO" = "$MODELO|list,form" ] && ok "AC3 a acao abre a lista e o formulario do modelo ($ACAO)" \
        || falhou "AC3 a acao nao abre list,form do modelo: '$ACAO'"
fi
MENU_ACAO="$(psql_bd "$BANCO" "select action from ir_ui_menu where id = $(xmlid ir.ui.menu menu_tf_sales_ai_oportunidades)")"
ACAO_ID="$(xmlid ir.actions.act_window action_tf_process_opportunity)"
[ "$MENU_ACAO" = "ir.actions.act_window,$ACAO_ID" ] && ok "AC3 o menu do item abre a acao do modelo" \
    || falhou "AC3 o menu aponta para '$MENU_ACAO' (esperado ir.actions.act_window,$ACAO_ID)"
PAI_ESPERADO="$(psql_bd "$BANCO" "select res_id from ir_model_data where module = 'crm' and name = 'crm_menu_root'")"
PAI_REAL="$(psql_bd "$BANCO" "select parent_id from ir_ui_menu where id = $(xmlid ir.ui.menu menu_tf_sales_ai_root)")"
if [ -n "$PAI_ESPERADO" ] && [ "$PAI_REAL" = "$PAI_ESPERADO" ]; then
    ok "AC3 o menu raiz do Sales AI pende do menu raiz do CRM"
else
    falhou "AC3 o menu raiz do Sales AI nao pende de crm.crm_menu_root (real='$PAI_REAL' esperado='$PAI_ESPERADO')"
fi

# ---------------------------------------------------------------------------
# passo 4/6 — prova independente (membro x restrito, view renderizada)
# ---------------------------------------------------------------------------
cabecalho "passo 4/6 — prova independente (provar_views_sales_ai.py)"
LOG_ATUAL="$LOG_DIR/4-prova-independente.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$PROVA")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
grep -E '^(VIEW_ITEM|VIEW_ITENS|VIEW_FALHAS|VIEW_RESULTADO)' "$LOG_ATUAL" | sed 's/^/      /'
ITENS_PROVA="$(grep -oE '^VIEW_ITENS=[0-9]+' "$LOG_ATUAL" | tail -1 | cut -d= -f2)"
FALHAS_PROVA="$(grep -oE '^VIEW_FALHAS=[0-9]+' "$LOG_ATUAL" | tail -1 | cut -d= -f2)"
if [ -n "${ITENS_PROVA:-}" ] && [ -n "${FALHAS_PROVA:-}" ]; then
    if [ "$FALHAS_PROVA" = "0" ] && [ "$ITENS_PROVA" -ge "$MIN_ITENS_PROVA" ]; then
        ok "prova independente: $ITENS_PROVA itens, 0 falhas (minimo $MIN_ITENS_PROVA)"
    else
        falhou "prova independente: $ITENS_PROVA itens, $FALHAS_PROVA falha(s) (minimo $MIN_ITENS_PROVA)"
    fi
else
    falhou "prova independente sem marcadores VIEW_ITENS/VIEW_FALHAS (nao deu para medir)"
fi
grep -q '^VIEW_RESULTADO: OK' "$LOG_ATUAL" && ok "prova independente com VIEW_RESULTADO: OK" \
    || falhou "prova independente sem VIEW_RESULTADO: OK"
grep -qE 'Traceback' "$LOG_ATUAL" && falhou "log da prova independente com traceback" \
    || ok "log da prova independente sem traceback"

# ---------------------------------------------------------------------------
# passo 5/6 — rollback: desinstalar pelo ORM e medir o rastro
# ---------------------------------------------------------------------------
cabecalho "passo 5/6 — rollback (desinstalacao pelo ORM)"
LOG_ATUAL="$LOG_DIR/5-desinstalacao.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$DESINSTALADOR")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
grep -q 'DESINSTALACAO_OK' "$LOG_ATUAL" && ok "ORM desinstalou o modulo (marcador DESINSTALACAO_OK)" \
    || falhou "marcador DESINSTALACAO_OK ausente no log do shell (exit $RC)"
grep -qE '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" \
    && falhou "log do shell com traceback/recusa: $(grep -E '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" | head -2 | tr '\n' ' ')" \
    || ok "log da desinstalacao sem traceback e sem recusa"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "uninstalled" ] && ok "ir_module_module.state = uninstalled" || falhou "estado depois da desinstalacao: '$ESTADO'"
RESTOS_VIEW="$(psql_bd "$BANCO" "select count(*) from ir_ui_view where id in (select res_id from ir_model_data where module = '$MODULO' and model = 'ir.ui.view')")"
[ "$RESTOS_VIEW" = "0" ] && ok "rollback das views: 0 view do modulo sobrou" \
    || falhou "$RESTOS_VIEW view(s) do modulo sobraram depois da desinstalacao"
RESTOS_MENU="$(psql_bd "$BANCO" "select count(*) from ir_ui_menu where id in (select res_id from ir_model_data where module = '$MODULO' and model = 'ir.ui.menu')")"
[ "$RESTOS_MENU" = "0" ] && ok "rollback dos menus: 0 menu do modulo sobrou" \
    || falhou "$RESTOS_MENU menu(s) do modulo sobraram depois da desinstalacao"
RESTOS_MODULO="$(psql_bd "$BANCO" "select count(*) from ir_model_data where module = '$MODULO'")"
[ "$RESTOS_MODULO" = "0" ] && ok "rollback: 0 registro do modulo em ir_model_data" \
    || falhou "$RESTOS_MODULO registro(s) do modulo sobraram em ir_model_data"
# a secao "Sales AI" nao pode continuar no formulario do parceiro depois da desinstalacao
SECAO=$(psql_bd "$BANCO" "select count(*) from ir_ui_view v where v.model = 'res.partner' and v.arch_db::text like '%tf_sales_ai%'")
[ "$SECAO" = "0" ] && ok "rollback: nenhuma view de parceiro carrega a secao Sales AI" \
    || falhou "$SECAO view(s) de parceiro ainda carregam a secao Sales AI"

# ---------------------------------------------------------------------------
# passo 6/6 — limpeza e prova de que a instancia do dev nao foi tocada
# ---------------------------------------------------------------------------
cabecalho "passo 6/6 — limpeza e instancia do dev"
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
