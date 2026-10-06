#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E05-T01 — OBSERVABILIDADE DE SINCRONIZACAO do board
# transformativa-revenue-engine (card t_0b77a689).
#
# Criterios de aceitacao (definidos no card e registrados nele):
#   AC1 observabilidade DECLARADA: o workflow versionado e' o resultado dos
#       artefatos versionados (contrato + nucleo + as duas consultas SQL) — o
#       Code node embute o nucleo byte a byte, o contrato embutido e' o arquivo,
#       o SQL dos dois nos e' o arquivo, e o workflow em disco e' o montado agora
#       pelo montador versionado. Divergencia = reprovacao (lente estrutural);
#   AC2 medicao em DUAS consultas SOMENTE LEITURA sobre as duas tabelas do
#       contrato (`outbox_events`, `sync_events`), uma linha por metrica declarada,
#       sem payload e sem tocar no que observa (retrato das duas tabelas antes e
#       depois de uma rodada: IDENTICO);
#   AC3 AUSENCIA DE MEDICAO NAO E' SAUDE: metrica declarada sem linha, valor nao
#       numerico, metrica fora do contrato, limiar ausente/invertido, direcao
#       desconhecida e dimensao fora do vocabulario fecham INDETERMINADO (nunca
#       OK) — medido pela suite do nucleo (fail-closed) e pelo ramo real;
#   AC4 dead-letter SEM MOTIVO e' CRITICO e APARECE: o estado C tem de fechar
#       CRITICO com o dead-letter visivel no relatorio como (SEM MOTIVO) — a
#       falha engolida nao pode passar em silencio;
#   AC5 dead-letter COM MOTIVO e' VISIVEL com o `last_error`/`error_message` no
#       relatorio (estado D), e a conferencia cruzada metrica x lista de detalhes
#       fecha INDETERMINADO quando as duas contas divergem;
#   AC6 o veredito ATRIBUIVEL: cada estado produz UM sinal e o veredito e' o
#       pior deles (estado A OK, B ATENCAO pela falha da trilha, C CRITICO pelo
#       dead-letter sem motivo, D ATENCAO pelo dead-letter com motivo, E CRITICO
#       pelo PROCESSED sem trilha, F CRITICO pela fila no teto, G CRITICO pela
#       direcao fora do vocabulario, H CRITICO pela trilha sem conclusao);
#   AC7 a medicao e' REPLICAVEL: o mesmo numero sai da consulta rodada direto no
#       psql e do relatorio produzido pelo workflow no n8n (as duas medicoes tem
#       de bater entre si e com o valor esperado a mao);
#   AC8 ambiente intocado: trio DESCARTAVEL proprio (postgres + n8n), banco com
#       nome ^tre_[a-z0-9_]+$, dev/homolog/producao medidos antes e depois, nenhum
#       segredo em claro no cofre do n8n, sha256 dos artefatos fixado e reconferido.
#
# TEST PLAN (executado por este script, na VPS, por execucao real):
#   passo 0   lente estrutural (python puro), suite do nucleo (node na imagem do n8n)
#             e montador em modo --conferir (o workflow em disco e' o montado agora)
#   guardas   docker, imagens, python3/openssl, artefatos em disco, banco descartavel,
#             sha256 fixado e ambiente (dev/homolog/prod) medido ANTES
#   trio      postgres descartavel + schema do contrato + n8n descartavel com a
#             credencial (id/nome do contrato) e o workflow importados
#   estados   A..H: cada estado TRUNCA as duas tabelas, aplica a massa do bloco,
#             roda o workflow por execucao real no n8n e mede por DOIS caminhos
#   final     rodada extra para provar que a observabilidade nao escreve, ambiente
#             depois, segredo, sha256 reconferido e limpeza do trio
#
# O modo --prova-de-dente e' FAIL-CLOSED: roda primeiro um sub-run NAO mutado
# (baseline) que tem de ficar verde, depois as mutacoes do mutador versionado e so'
# fecha com DENTE_OK se TODOS os vereditos forem DENTE_CUMPRIDO e os dois juizes
# (dente e sha256) estiverem conferidos. Qualquer outro veredito (NAO_CONTA /
# MUTACAO_SEM_DENTE / MUTACAO_NAO_APLICADA) fecha com DENTE_FALHOU e exit 1.
#
# Uso (NA VPS, a partir de ARQUIVO — a prova de dente reinvoca o proprio script):
#   bash scripts/n8n/verificar-observabilidade-sync.sh
#   bash scripts/n8n/verificar-observabilidade-sync.sh --apenas-codigo
#   bash scripts/n8n/verificar-observabilidade-sync.sh --apenas-medicao
#   bash scripts/n8n/verificar-observabilidade-sync.sh --prova-de-dente
#   bash scripts/n8n/verificar-observabilidade-sync.sh --manter    (nao limpa o trio)
#
# Variaveis: TRE_WORKFLOW (workflow sob teste), TRE_LOG_DIR, TRE_BANCO, TRE_IMAGEM_PG,
# TRE_IMAGEM_N8N, TRE_PG_USER, TRE_DEV_PG_CT, TRE_DEV_HOMOLOG_PROD, TRE_MANTER_BANCO.
#
# Saida: um item por linha (OK/FALHOU), resumo final em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir   2 = uso errado
# ============================================================================

RAIZ_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKFLOW="${TRE_WORKFLOW:-$RAIZ_REPO/n8n/workflows/TRE-observabilidade-sync.json}"
CONTRATO="$RAIZ_REPO/n8n/contracts/observabilidade-sync.v1.json"
NUCLEO="$RAIZ_REPO/n8n/codigo/observabilidade-sync.js"
SQL_METRICAS="$RAIZ_REPO/n8n/sql/observabilidade-sync.sql"
SQL_DETALHES="$RAIZ_REPO/n8n/sql/observabilidade-sync-dead-letters.sql"
MASSA="$RAIZ_REPO/scripts/n8n/massa-observabilidade.sql"
MONTADOR="$RAIZ_REPO/scripts/n8n/montar_workflow_observabilidade.py"
MUTADOR="$RAIZ_REPO/scripts/n8n/mutar_workflow_observabilidade.py"
LENTE="$RAIZ_REPO/scripts/n8n/conferir_observabilidade.py"
SUITE="$RAIZ_REPO/scripts/n8n/testar_observabilidade_sync.js"
LEITOR="$RAIZ_REPO/scripts/n8n/ler_resultado_n8n.py"
NORMALIZADOR="$RAIZ_REPO/scripts/n8n/normalizar_medicao.py"
MIGRATION="$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
IMAGEM_N8N="${TRE_IMAGEM_N8N:-n8nio/n8n:latest}"
PG_USER="${TRE_PG_USER:-tre}"
ID_WORKFLOW="${TRE_ID_WORKFLOW:-TREOBSERVSYNC1}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-observabilidade-sync}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER="${TRE_MANTER_BANCO:-0}"
SUFIXO="$$$RANDOM"
BANCO="${TRE_BANCO:-tre_obs_$SUFIXO}"
ESTADOS="A B C D E F G H"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-codigo)   MODO=codigo ;;
        --apenas-medicao)  MODO=medicao ;;
        --prova-de-dente)  MODO=dente ;;
        --manter)          MANTER=1 ;;
        --workflow)        shift; WORKFLOW="${1:-}" ;;
        --log-dir)         shift; LOG_DIR="${1:-}" ;;
        --banco)           shift; BANCO="${1:-}" ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
    shift
done

ITENS=0
FALHAS=0
PG_TMP="e05t01-pg-$SUFIXO"
NET_TMP="e05t01-net-$SUFIXO"
DESC_DIR=""
N8N_HOME=""
mkdir -p "$LOG_DIR"

ok()        { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()    { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()      { printf 'INFO  %s\n' "$*"; }
cabecalho() { printf '\n=== %s ===\n' "$*"; }
limpar()    { printf '%s' "$1" | tr -d '[:space:]'; }
podar()     { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: OBSERVABILIDADE_SYNC_OK ($ITENS itens, 0 falhas) banco=$BANCO imagens=$IMAGEM_PG+$IMAGEM_N8N workflow=$WORKFLOW"
        exit 0
    fi
    echo "RESULTADO: OBSERVABILIDADE_SYNC_FALHOU ($ITENS itens, $FALHAS falha(s)) banco=$BANCO imagens=$IMAGEM_PG+$IMAGEM_N8N workflow=$WORKFLOW"
    exit 1
}

# ---------------------------------------------------------------------------
# sha256 dos artefatos sob teste: fixado nas guardas e RECONFERIDO no fecho.
# A reconferencia so' vale com o juiz conferido (`controle_do_juiz_sha`).
# ---------------------------------------------------------------------------
sha256_dos_artefatos() { # $1 = arquivo onde gravar "<sha>  <caminho relativo>"
    local destino="$1" arquivo
    : >"$destino"
    for arquivo in "$CONTRATO" "$NUCLEO" "$SQL_METRICAS" "$SQL_DETALHES" "$WORKFLOW" \
                   "$MONTADOR" "$MUTADOR" "$LENTE" "$SUITE" "$MASSA"; do
        printf '%s  %s\n' "$(sha256sum "$arquivo" | cut -d' ' -f1)" "${arquivo#"$RAIZ_REPO"/}" >>"$destino"
    done
}

veredito_sha256() { # $1=arquivo "antes"  $2=arquivo "depois"  ->  IDENTICO | MUDOU
    if diff -q "$1" "$2" >/dev/null 2>&1; then echo IDENTICO; else echo MUDOU; fi
}

# ---------------------------------------------------------------------------
# --apenas-codigo: lente estrutural (python) + suite do nucleo (node no n8n)
#                  + montador --conferir (o workflow e' artefato DERIVADO)
# ---------------------------------------------------------------------------
passo_codigo() {
    cabecalho "passo 0 — lente estrutural (contrato x SQL x nucleo x workflow)"
    if [ ! -f "$LENTE" ]; then falhou "lente estrutural ausente: $LENTE"; resumo; fi
    SAIDA_LENTE="$(python3 "$LENTE" --raiz "$RAIZ_REPO" 2>&1)"
    printf '%s\n' "$SAIDA_LENTE" >"$LOG_DIR/0-lente.out"
    printf '%s\n' "$SAIDA_LENTE" | grep -E '^(OK|FALHOU) ' | sed 's/^/      /'
    RESULTADO_LENTE="$(printf '%s\n' "$SAIDA_LENTE" | grep -E '^RESULTADO: ' | tail -1)"
    case "$RESULTADO_LENTE" in
        *OBSERVABILIDADE_LENTE_OK*) ok "lente estrutural: $RESULTADO_LENTE" ;;
        *) falhou "lente estrutural NAO passou: ${RESULTADO_LENTE:-sem linha de resultado}" ;;
    esac

    cabecalho "passo 0b — suite do nucleo (node, dentro da imagem do n8n)"
    if ! docker image inspect "$IMAGEM_N8N" >/dev/null 2>&1; then
        falhou "imagem $IMAGEM_N8N ausente: sem node nao ha' medicao do nucleo"; resumo
    fi
    SAIDA_SUITE="$(docker run --rm -v "$RAIZ_REPO":/repo:ro -v "$(dirname "$WORKFLOW")":/mutado:ro \
        --entrypoint node "$IMAGEM_N8N" /repo/scripts/n8n/testar_observabilidade_sync.js \
        --workflow "/mutado/$(basename "$WORKFLOW")" 2>&1)"
    printf '%s\n' "$SAIDA_SUITE" >"$LOG_DIR/0b-suite.out"
    printf '%s\n' "$SAIDA_SUITE" | grep -E '^(OK|FALHOU) ' | sed 's/^/      /'
    RESULTADO_SUITE="$(printf '%s\n' "$SAIDA_SUITE" | grep -E '^RESULTADO: ' | tail -1)"
    case "$RESULTADO_SUITE" in
        *OBSERVABILIDADE_SYNC_NUCLEO_OK*) ok "suite do nucleo: $RESULTADO_SUITE" ;;
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
# --prova-de-dente: cada mutacao roda o aceite numa COPIA do workflow. Um dente so'
# conta quando o sub-run (a) chegou a' fase certa, (b) a mutacao foi APLICADA e
# (c) reprovou O ITEM que ela quebra. O juiz e' testado com saidas sinteticas —
# sem isso, ambiente quebrado viraria "dente cumprido".
# ---------------------------------------------------------------------------
juizo_do_dente() { # $1=arquivo de saida do sub-run  $2=marcador da fase  $3=trecho do item esperado
    # O item pode vir indentado (a fase de codigo imprime os itens da suite com recuo),
    # por isso a comparacao aceita recuo antes de OK/FALHOU.
    local saida="$1" marcador="$2" esperado="$3"
    if ! grep -q "$marcador" "$saida"; then
        echo "NAO_CONTA (o sub-run nao chegou a' fase $marcador)"
        return
    fi
    # Ambiente quebrado NAO e' "ancora quebrada": se a propria rodada reprovou o ambiente
    # (massa que nao aplica, execucao do workflow que falha, trio que sumiu), o veredito diz
    # isso — quem le o log do dente precisa do erro real, nao de uma pista falsa.
    local quebrado
    quebrado="$(grep -m1 -E '^[[:space:]]*FALHOU[[:space:]]+(estado [A-H]: a massa do bloco nao aplicou|estado [A-H]: a execucao do workflow no n8n falhou|trio descartavel|nao consegui)' "$saida")"
    if [ -n "$quebrado" ]; then
        echo "NAO_CONTA (ambiente quebrado na fase: $(printf '%s' "$quebrado" | sed 's/^[[:space:]]*//' | cut -c1-100))"
        return
    fi
    # A reprovacao e' checada PRIMEIRO: um OUTRO item que por acaso contenha o mesmo
    # trecho de texto nao pode esconder o dente (aconteceu: "combinacao ausente nao vira
    # indeterminado" mascarava "linha VAZIA ... nao vira indeterminado").
    if grep -qE "^[[:space:]]*FALHOU[[:space:]]+.*$esperado" "$saida"; then
        echo "DENTE_CUMPRIDO"
        return
    fi
    if grep -qE "^[[:space:]]*OK[[:space:]]+.*$esperado" "$saida"; then
        echo "MUTACAO_SEM_DENTE (o item esperado continuou OK)"
        return
    fi
    echo "NAO_CONTA (item esperado ausente na saida — ancora quebrada)"
}

controle_do_juiz() {
    local dir="$1" ok_controles=0 faltas=0
    # 1) a mutacao nao mexeu no comportamento: o item esperado CONTINUA OK
    printf 'FASE_MEDICAO_OK\nOK    estado C: veredito CRITICO pelo dead-letter SEM motivo\n' >"$dir/controle_1.out"
    # 2) a mutacao quebrou o item: e' isso que um dente cumprido devolve
    printf 'FASE_MEDICAO_OK\nFALHOU estado C: veredito CRITICO pelo dead-letter SEM motivo\n' >"$dir/controle_2.out"
    # 3) o sub-run nem chegou a' fase de medicao (ambiente quebrado nao e' dente)
    printf 'FALHOU guardas do ambiente\n' >"$dir/controle_3.out"
    # 4) a fase rodou, mas o item esperado nao aparece na saida (ancora quebrada)
    printf 'FASE_MEDICAO_OK\nOK    estado A: veredito OK (rodada saudavel)\n' >"$dir/controle_4.out"
    # 5) a fase de CODIGO tambem tem de ser reconhecida pelo juiz
    printf 'FASE_CODIGO_OK\nFALHOU metrica declarada sem linha fecha INDETERMINADO/exit 3 (OK/0)\n' >"$dir/controle_5.out"
    # 6) item INDENTADO (a suite imprime com recuo dentro da fase de codigo)
    printf 'FASE_CODIGO_OK\n      FALHOU metrica declarada sem linha fecha INDETERMINADO/exit 3 (OK/0)\n' >"$dir/controle_6.out"
    # 7) dois itens com o MESMO trecho (um OK, outro FALHOU): o FALHOU vence — o dente nao
    #    pode ser mascarado por um item vizinho de texto parecido
    printf 'FASE_CODIGO_OK\nOK    combinacao ausente nao vira indeterminado nem critico\nFALHOU linha VAZIA nao vira indeterminado\n' >"$dir/controle_7.out"
    # 8) ambiente quebrado dentro da fase (massa nao aplicou): NAO_CONTA com o motivo do ambiente
    printf 'FASE_MEDICAO_OK\nFALHOU estado E: a massa do bloco nao aplicou (ver /tmp/x/semear-estado-E.sql.log)\nFALHOU estado F: a massa do bloco nao aplicou (ver /tmp/x/semear-estado-F.sql.log)\n' >"$dir/controle_8.out"
    local c1 c2 c3 c4 c5 c6 c7 c8
    c1="$(juizo_do_dente "$dir/controle_1.out" FASE_MEDICAO_OK 'estado C')"
    c2="$(juizo_do_dente "$dir/controle_2.out" FASE_MEDICAO_OK 'estado C')"
    c3="$(juizo_do_dente "$dir/controle_3.out" FASE_MEDICAO_OK 'estado C')"
    c4="$(juizo_do_dente "$dir/controle_4.out" FASE_MEDICAO_OK 'estado C' )"
    c5="$(juizo_do_dente "$dir/controle_5.out" FASE_CODIGO_OK 'metrica declarada sem linha')"
    c6="$(juizo_do_dente "$dir/controle_6.out" FASE_CODIGO_OK 'metrica declarada sem linha')"
    c7="$(juizo_do_dente "$dir/controle_7.out" FASE_CODIGO_OK 'nao vira indeterminado')"
    c8="$(juizo_do_dente "$dir/controle_8.out" FASE_MEDICAO_OK 'estado E: veredito CRITICO pelo PROCESSED sem trilha de sucesso')"
    case "$c1" in *MUTACAO_SEM_DENTE*) ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c2" in *DENTE_CUMPRIDO*)   ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c3" in *NAO_CONTA*)        ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c4" in *NAO_CONTA*)        ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c5" in *DENTE_CUMPRIDO*)   ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c6" in *DENTE_CUMPRIDO*)   ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c7" in *DENTE_CUMPRIDO*)   ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c8" in *"NAO_CONTA (ambiente quebrado"*) ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    if [ "$faltas" -eq 0 ]; then
        ok "controle do juiz do dente (8 saidas sinteticas: sem dente, dente, ambiente, ancora, fase de codigo, item indentado, FALHOU vence OK, ambiente quebrado nomeado)"
    else
        falhou "controle do juiz do dente ($faltas de 8 saidas sinteticas julgadas errado)"
    fi
}

controle_do_juiz_sha() { # o item de sha256 nao pode ser vacuO: 2 saidas sinteticas
    local dir="$1" certos=0
    printf 'a1  n8n/contracts/observabilidade-sync.v1.json\nb2  n8n/codigo/observabilidade-sync.js\n' >"$dir/sha-antes.txt"
    cp "$dir/sha-antes.txt" "$dir/sha-igual.txt"
    printf 'a1  n8n/contracts/observabilidade-sync.v1.json\nzz  n8n/codigo/observabilidade-sync.js\n' >"$dir/sha-mudado.txt"
    [ "$(veredito_sha256 "$dir/sha-antes.txt" "$dir/sha-igual.txt")" = "IDENTICO" ] && certos=$((certos + 1))
    [ "$(veredito_sha256 "$dir/sha-antes.txt" "$dir/sha-mudado.txt")" = "MUDOU" ] && certos=$((certos + 1))
    if [ "$certos" -eq 2 ]; then
        ok "controle do juiz do sha256 (2 saidas sinteticas: identico e mudado)"
    else
        falhou "controle do juiz do sha256 (so' $certos de 2 saidas sinteticas julgadas certo)"
    fi
}

if [ "$MODO" = "dente" ]; then
    # O diretorio do dente fica no LOG_DIR da rodada: e' evidencia reproduzivel
    # (mutante aplicado, saida de cada sub-run), nao lixo de /tmp.
    DENTE_DIR="${LOG_DIR}/dente"
    mkdir -p "$DENTE_DIR"
    cabecalho "--prova-de-dente: o aceite tem dentes?"
    controle_do_juiz "$DENTE_DIR"
    controle_do_juiz_sha "$DENTE_DIR"
    FALHAS_JUIZ=$FALHAS

    info "baseline (workflow NAO mutado) precisa ficar verde antes de qualquer dente"
    TRE_LOG_DIR="$DENTE_DIR/logs-baseline" TRE_WORKFLOW="$WORKFLOW" TRE_MANTER_BANCO=0 \
        TRE_BANCO="tre_obs_base_$$" bash "$(readlink -f "$0")" --apenas-medicao >"$DENTE_DIR/baseline.out" 2>&1
    BASELINE_RES="$(grep -E '^RESULTADO: ' "$DENTE_DIR/baseline.out" | tail -1)"
    case "$BASELINE_RES" in
        *OBSERVABILIDADE_SYNC_OK*) BASELINE_OK=1; ok "baseline nao mutado: $BASELINE_RES" ;;
        *) BASELINE_OK=0; falhou "baseline nao mutado NAO ficou verde: ${BASELINE_RES:-sem resultado} (ver $DENTE_DIR/baseline.out)" ;;
    esac

    if [ ! -f "$MUTADOR" ]; then falhou "mutador ausente: $MUTADOR"; else
    VEREDITOS="$DENTE_DIR/vereditos.txt"
    : >"$VEREDITOS"
    while IFS=$'\t' read -r mutacao fase esperado porque; do
        [ -n "$mutacao" ] || continue
        ALVO="$DENTE_DIR/$mutacao.json"
        if ! python3 "$MUTADOR" --mutacao "$mutacao" --entrada "$WORKFLOW" --saida "$ALVO" >"$DENTE_DIR/$mutacao.mutacao" 2>&1; then
            VEREDITO="MUTACAO_NAO_APLICADA ($(head -1 "$DENTE_DIR/$mutacao.mutacao"))"
        else
            if [ "$fase" = "codigo" ]; then
                TRE_LOG_DIR="$DENTE_DIR/logs-$mutacao" TRE_WORKFLOW="$ALVO" TRE_MANTER_BANCO=0 \
                    TRE_BANCO="tre_obs_$mutacao" bash "$(readlink -f "$0")" --apenas-codigo >"$DENTE_DIR/$mutacao.out" 2>&1
                VEREDITO="$(juizo_do_dente "$DENTE_DIR/$mutacao.out" FASE_CODIGO_OK "$esperado")"
            else
                TRE_LOG_DIR="$DENTE_DIR/logs-$mutacao" TRE_WORKFLOW="$ALVO" TRE_MANTER_BANCO=0 \
                    TRE_BANCO="tre_obs_$mutacao" bash "$(readlink -f "$0")" --apenas-medicao >"$DENTE_DIR/$mutacao.out" 2>&1
                VEREDITO="$(juizo_do_dente "$DENTE_DIR/$mutacao.out" FASE_MEDICAO_OK "$esperado")"
            fi
        fi
        printf 'DENTE %-40s %s\n' "$mutacao" "$VEREDITO"
        printf '%s|%s|%s|%s\n' "$mutacao" "$fase" "$esperado" "$VEREDITO" >>"$VEREDITOS"
    done < <(python3 "$MUTADOR" --listar)
    fi

    TOTAL_DENTES=0
    DENTES_CUMPRIDOS=0
    SEM_DENTE=0
    if [ -f "$VEREDITOS" ]; then
        while IFS='|' read -r nome fase esperado veredito; do
            [ -n "$nome" ] || continue
            TOTAL_DENTES=$((TOTAL_DENTES + 1))
            case "$veredito" in
                *DENTE_CUMPRIDO*) DENTES_CUMPRIDOS=$((DENTES_CUMPRIDOS + 1)) ;;
                *) SEM_DENTE=$((SEM_DENTE + 1)) ;;
            esac
        done <"$VEREDITOS"
    fi

    info "resumo do dente: $DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos; baseline=$([ "$BASELINE_OK" = "1" ] && echo verde || echo NAO_VERDE); juiz=$([ "$FALHAS" -eq "$FALHAS_JUIZ" ] && echo conferido || echo COM_FALTA)"
    if [ "$FALHAS" -ne "$FALHAS_JUIZ" ]; then
        echo "RESULTADO: OBSERVABILIDADE_SYNC_DENTE_FALHOU (controle do juiz com falta; $DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos)"
        exit 1
    fi
    if [ "$TOTAL_DENTES" -eq 0 ]; then
        echo "RESULTADO: OBSERVABILIDADE_SYNC_DENTE_FALHOU (0 dente medido: nenhuma mutacao aplicada)"
        exit 1
    fi
    if [ "$BASELINE_OK" != "1" ]; then
        echo "RESULTADO: OBSERVABILIDADE_SYNC_DENTE_FALHOU (baseline NAO mutado nao ficou verde; $SEM_DENTE sem dente de $TOTAL_DENTES)"
        exit 1
    fi
    if [ "$DENTES_CUMPRIDOS" -ne "$TOTAL_DENTES" ]; then
        echo "RESULTADO: OBSERVABILIDADE_SYNC_DENTE_FALHOU ($SEM_DENTE sem dente de $TOTAL_DENTES) mutacoes=$TOTAL_DENTES dentes=$DENTES_CUMPRIDOS"
        exit 1
    fi
    echo "RESULTADO: OBSERVABILIDADE_SYNC_DENTE_OK ($DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos; juiz conferido; baseline nao mutado verde)"
    exit 0
fi

if [ "$MODO" = "codigo" ] || [ "$MODO" = "completo" ]; then
    passo_codigo
    if [ "$MODO" = "codigo" ]; then resumo; fi
fi

# ---------------------------------------------------------------------------
# guardas do ambiente (fail-closed: sem ambiente medido nao existe aceite)
# ---------------------------------------------------------------------------
cabecalho "guardas do ambiente"
if docker info >/dev/null 2>&1; then ok "docker responde"; else falhou "docker nao responde"; resumo; fi
for img in "$IMAGEM_PG" "$IMAGEM_N8N"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
        ok "imagem $img presente ($(docker image inspect -f '{{index .RepoDigests 0}}' "$img" 2>/dev/null))"
    else
        falhou "imagem $img ausente (nada a medir)"; resumo
    fi
done
command -v openssl >/dev/null 2>&1 && ok "openssl disponivel (segredo do descartavel)" \
    || { falhou "openssl ausente"; resumo; }
python3 --version >/dev/null 2>&1 && ok "python3 disponivel (lente, leitores e mutacoes)" \
    || { falhou "python3 ausente"; resumo; }
for arquivo in "$WORKFLOW" "$CONTRATO" "$NUCLEO" "$SQL_METRICAS" "$SQL_DETALHES" "$MASSA" \
               "$MONTADOR" "$MUTADOR" "$LENTE" "$SUITE" "$LEITOR" "$NORMALIZADOR" "$MIGRATION"; do
    if [ -f "$arquivo" ]; then ok "artefato em disco: ${arquivo#"$RAIZ_REPO"/}"; else falhou "ausente: $arquivo"; resumo; fi
done
info "sha256 dos artefatos sob teste (fixado agora e reconferido no fecho):"
sha256_dos_artefatos "$LOG_DIR/sha256-antes.txt"
sed 's/^/      /' "$LOG_DIR/sha256-antes.txt"
if [ "$(wc -l <"$LOG_DIR/sha256-antes.txt" | tr -d ' ')" = "10" ]; then
    ok "sha256 dos 10 artefatos sob teste fixado (base da reconferencia do fecho)"
else
    falhou "nao consegui fixar o sha256 dos 10 artefatos (ver $LOG_DIR/sha256-antes.txt)"
fi
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres|template0|template1) falhou "banco $BANCO e' do ambiente — so' banco descartavel"; resumo ;;
esac
if printf '%s' "$BANCO" | grep -qE '^tre_[a-z0-9_]+$' && ! printf '%s' "$BANCO" | grep -qEi 'prod|homolog'; then
    ok "banco descartavel com nome seguro: $BANCO"
else
    falhou "nome de banco fora do padrao descartavel (^tre_[a-z0-9_]+$): $BANCO"; resumo
fi
ID_CONTRATO="$(python3 - "$CONTRATO" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["workflow"]["id_estavel"])
PY
)"
if [ -n "$ID_CONTRATO" ]; then
    ID_WORKFLOW="$ID_CONTRATO"
    ok "id estavel do workflow lido do contrato: $ID_WORKFLOW"
else
    falhou "nao consegui ler o id estavel do contrato"
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
if [ "$FALHAS" -gt 0 ]; then resumo; fi
printf 'FASE_MEDICAO_OK\n'

# ---------------------------------------------------------------------------
# trio descartavel proprio: e' o que autoriza medir sem tocar no ambiente
# ---------------------------------------------------------------------------
cabecalho "trio descartavel proprio (postgres + n8n)"
DESC_DIR="$(mktemp -d /tmp/verificacao-observabilidade-XXXXXX)"
chmod 700 "$DESC_DIR"
N8N_HOME="$DESC_DIR/n8n-home"
mkdir -p "$N8N_HOME"
chmod 700 "$N8N_HOME"
chown 1000:1000 "$N8N_HOME" 2>/dev/null || chown 1000:1000 "$N8N_HOME"
SENHA="$(openssl rand -hex 24)"
printf 'POSTGRES_USER=%s\nPOSTGRES_PASSWORD=%s\nPOSTGRES_DB=postgres\n' "$PG_USER" "$SENHA" >"$DESC_DIR/pg.env"
chmod 600 "$DESC_DIR/pg.env"

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
        info "--manter: trio preservado (container $PG_TMP, rede $NET_TMP, diretorio $DESC_DIR)"
        return 0
    fi
    docker rm -f -v "$PG_TMP" >/dev/null 2>&1
    [ -n "$N8N_HOME" ] && rm -rf "$N8N_HOME"
    docker network rm "$NET_TMP" >/dev/null 2>&1
    [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR"
}
trap limpeza EXIT

si() { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -tAc "$1" 2>/dev/null; }

# ---------------------------------------------------------------------------
# passo 1 — schema do contrato no banco descartavel
# ---------------------------------------------------------------------------
cabecalho "passo 1 — schema do contrato no banco descartavel"
docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO" >/dev/null 2>&1 \
    && ok "banco $BANCO criado no postgres descartavel" \
    || { falhou "nao consegui criar o banco $BANCO"; resumo; }
docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -v ON_ERROR_STOP=1 -q <"$MIGRATION" >"$LOG_DIR/1-migration.log" 2>&1 \
    && ok "schema do contrato aplicado (db/migrations/0001_sales_intelligence_v1.sql)" \
    || { falhou "schema do contrato NAO aplicou (ver $LOG_DIR/1-migration.log)"; resumo; }
TABELAS_ESPERADAS="$(grep -c 'CREATE TABLE' "$MIGRATION" | tr -d ' ')"
TABELAS_MEDIDAS="$(limpar "$(si "select count(*) from information_schema.tables where table_schema='sales_intelligence'")")"
[ "$TABELAS_MEDIDAS" = "$TABELAS_ESPERADAS" ] \
    && ok "as $TABELAS_ESPERADAS tabelas do contrato existem no banco descartavel" \
    || falhou "esperava $TABELAS_ESPERADAS tabelas do contrato, medidas ${TABELAS_MEDIDAS:-0}"

# ---------------------------------------------------------------------------
# passo 2 — n8n descartavel: credencial (id/nome do contrato) e workflow
# ---------------------------------------------------------------------------
cabecalho "passo 2 — n8n descartavel (cofre + workflow importado)"
python3 - "$DESC_DIR/credenciais.json" "$CONTRATO" "$PG_TMP" "$BANCO" "$PG_USER" "$SENHA" <<'PY'
import json, sys
saida, arq_contrato, host, banco, usuario, senha = sys.argv[1:7]
contrato = json.load(open(arq_contrato))
postgres = contrato["credenciais"]["postgres"]
credenciais = [{"id": postgres["id"], "name": postgres["nome"], "type": "postgres",
                "data": {"host": host, "port": 5432, "database": banco, "user": usuario,
                         "password": senha, "ssl": "disable", "allowUnauthorizedCerts": False}}]
open(saida, "w").write(json.dumps(credenciais, ensure_ascii=False, indent=2))
print("credencial escrita com o id/nome do contrato (valor so' no diretorio 700)")
PY
chmod 600 "$DESC_DIR/credenciais.json"
cp "$WORKFLOW" "$DESC_DIR/workflow.json"
chmod 600 "$DESC_DIR/workflow.json"

n8n_cli() { # restantes = args do n8n (usa o cofre do descartavel)
    docker run --rm --network "$NET_TMP" -v "$N8N_HOME":/home/node/.n8n \
        -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_LOG_LEVEL=error \
        --entrypoint n8n "$IMAGEM_N8N" "$@" 2>&1
}
cp "$DESC_DIR/credenciais.json" "$N8N_HOME/credenciais.json"
cp "$DESC_DIR/workflow.json" "$N8N_HOME/workflow.json"
chown 1000:1000 "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json" 2>/dev/null \
    || { falhou "nao consegui dar o cofre ao uid 1000 do n8n"; resumo; }
chmod 600 "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json"
if n8n_cli import:credentials --input=/home/node/.n8n/credenciais.json >"$LOG_DIR/2-credenciais.log"; then
    ok "credencial postgres importada no cofre do n8n descartavel (id/nome do contrato)"
else
    falhou "import da credencial falhou (ver $LOG_DIR/2-credenciais.log)"
fi
if n8n_cli import:workflow --input=/home/node/.n8n/workflow.json >"$LOG_DIR/2-workflow.log"; then
    ok "workflow da observabilidade importado no n8n descartavel"
else
    falhou "import do workflow falhou (ver $LOG_DIR/2-workflow.log)"
fi
# O id estavel e' o contrato com o publicador e com os recibos: confere EXPORTANDO o
# workflow pelo id (a listagem da CLI muda de formato entre versoes; o export nao).
n8n_cli export:workflow --id="$ID_WORKFLOW" --output=/home/node/.n8n/exportado.json >"$LOG_DIR/2-export.log" 2>&1
if [ -s "$N8N_HOME/exportado.json" ] && grep -q "$ID_WORKFLOW" "$N8N_HOME/exportado.json"; then
    ok "o workflow importado e' exportavel pelo id estavel $ID_WORKFLOW"
else
    falhou "workflow $ID_WORKFLOW nao esta no cofre do n8n (ver $LOG_DIR/2-export.log)"
fi
rm -f "$N8N_HOME/exportado.json"
# Os arquivos de importacao cumpriram o papel: saem do cofre (o cofre nao guarda copia
# em claro da credencial — o item de segredo no fim do aceite mede exatamente isso).
rm -f "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json"

executar_rodada() { # $1=prefixo ; define RC_RODADA
    docker run --rm --network "$NET_TMP" -v "$N8N_HOME":/home/node/.n8n \
        -e N8N_DIAGNOSTICS_ENABLED=false -e GENERIC_TIMEZONE=UTC \
        --entrypoint n8n "$IMAGEM_N8N" execute --id="$ID_WORKFLOW" --rawOutput >"$1.out" 2>&1
    RC_RODADA=$?
}
semear() { # $1 = arquivo sql (devolve o rc do psql: massa que nao aplica nao vira estado)
    docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -v ON_ERROR_STOP=1 -q <"$1" \
        >"$LOG_DIR/semear-$(basename "$1").log" 2>&1
}
medir_direto() { # $1=prefixo -> escreve <prefixo>.valores no MESMO formato do workflow
    docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -tA -F'|' -f - <"$SQL_METRICAS" \
        | python3 "$NORMALIZADOR" >"$1.valores" 2>"$1.normalizacao"
}
retrato_tabelas() { # digest das DUAS tabelas: prova de somente-leitura em execucao real
    limpar "$(si "select coalesce(md5(
        coalesce((select string_agg(x::text, '|' order by x.id) from sales_intelligence.outbox_events x), '')
        || '||' ||
        coalesce((select string_agg(y::text, '|' order by y.id) from sales_intelligence.sync_events y), '')
      ), '-')
      || '|' || (select count(*) from sales_intelligence.outbox_events)
      || '|' || (select count(*) from sales_intelligence.sync_events)")"
}

# ---------------------------------------------------------------------------
# Resiliencia do trio descartavel: a VPS e' COMPARTILHADA com outras rodadas e a
# higiene de outra sessao pode remover container alheio no MEIO da medicao (medido:
# o postgres sumiu entre dois estados e o n8n passou a devolver "DNS server returned
# an error"; com `--rm`, container que sai e' removido). O aceite mede a
# OBSERVABILIDADE, nao a higiene da VPS: se o trio sumiu, ele e' restabelecido com o
# MESMO nome e o esquema e' reaplicado (o cofre do n8n vive em bind mount e
# sobrevive). Cada restabelecimento vira ITEM — ambiente remexido nao pode ser
# invisivel na leitura do log.
# ---------------------------------------------------------------------------
garantir_trio() {
    if ! docker network inspect "$NET_TMP" >/dev/null 2>&1; then
        docker network create "$NET_TMP" >/dev/null 2>&1
        docker network connect "$NET_TMP" "$PG_TMP" >/dev/null 2>&1
        ok "rede descartavel $NET_TMP restabelecida e o postgres reconectado"
    fi
    if docker inspect -f '{{.State.Running}}' "$PG_TMP" 2>/dev/null | grep -q true; then
        return 0
    fi
    if [ ! -f "$DESC_DIR/pg.env" ]; then
        falhou "trio descartavel sumiu por fora e o diretorio 700 tambem (sem senha nao da' para restabelecer)"
        return 1
    fi
    docker rm -f -v "$PG_TMP" >/dev/null 2>&1
    docker run -d --rm --name "$PG_TMP" --network "$NET_TMP" --env-file "$DESC_DIR/pg.env" "$IMAGEM_PG" \
        >/dev/null 2>&1
    local pronto=0
    for _ in $(seq 1 30); do
        if docker exec "$PG_TMP" pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then pronto=1; break; fi
        sleep 2
    done
    if [ "$pronto" != "1" ]; then
        falhou "trio descartavel restabelecido nao ficou pronto (a medicao nao continua)"
        return 1
    fi
    docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO" >/dev/null 2>&1
    docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -v ON_ERROR_STOP=1 -q <"$MIGRATION" \
        >"$LOG_DIR/0-restabelecimento-migration.log" 2>&1
    ok "trio descartavel restabelecido com o MESMO nome e esquema reaplicado: o container tinha sumido por fora"
}
limpar_estado() { si "truncate table sales_intelligence.outbox_events, sales_intelligence.sync_events" >/dev/null; }
semear_estado() { # $1 = letra do estado (bloco da massa)
    local bloco="$LOG_DIR/estado-$1.sql"
    sed -n "/^-- ESTADO $1\$/,/^-- FIM ESTADO $1\$/p" "$MASSA" | grep -v '^-- \(ESTADO\|FIM ESTADO\)' >"$bloco"
    semear "$bloco"
}
valor_de() { # $1=arquivo de valores  $2=metrica  $3=observacao -> valor
    awk -F'|' -v m="$2" -v o="$3" '$1 == m && $2 == o {print $3; exit}' "$1"
}
veredito_da_rodada() { sed -n 's/^veredito=//p' "$1.resumo" | head -1; }
confere_valor() { # $1=metrica $2=observacao $3=esperado $4=rotulo
    local direto wf
    direto="$(valor_de "$PREFIXO.valores" "$1" "$2")"
    wf="$(valor_de "$PREFIXO.wf.valores" "$1" "$2")"
    local nome="$1"; [ -n "$2" ] && nome="$1[$2]"
    if [ -z "$direto" ] || [ -z "$wf" ]; then
        falhou "$4 (metrica $nome ausente: direto='${direto:-}' workflow='${wf:-}')"
        return
    fi
    if ! awk -v a="$direto" -v b="$3" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<0.001)}'; then
        falhou "$4 (medicao direta $direto, esperado $3)"
        return
    fi
    if ! awk -v a="$wf" -v b="$3" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<0.001)}'; then
        falhou "$4 (medicao pelo workflow $wf, esperado $3)"
        return
    fi
    ok "$4"
}
confere_ausencia() { # $1=metrica $2=observacao $3=rotulo : a linha NAO pode existir na medicao
    if grep -q -- "^$1|$2|" "$PREFIXO.valores" || grep -q -- "^$1|$2|" "$PREFIXO.wf.valores"; then
        falhou "$3 (a linha $1[$2] apareceu na medicao, e o contrato nao a declara)"
    else
        ok "$3"
    fi
}
confere_valor_tolerancia() { # $1=metrica $2=esperado $3=tolerancia_s $4=rotulo (metricas de TEMPO: lidas em instantes diferentes)
    local direto wf
    direto="$(valor_de "$PREFIXO.valores" "$1" "")"
    wf="$(valor_de "$PREFIXO.wf.valores" "$1" "")"
    if [ -z "$direto" ] || [ -z "$wf" ]; then
        falhou "$4 (metrica $1 ausente: direto='${direto:-}' workflow='${wf:-}')"
        return
    fi
    if awk -v a="$direto" -v b="$2" -v t="$3" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<=t)}' \
       && awk -v a="$wf" -v b="$2" -v t="$3" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<=t)}'; then
        ok "$4"
    else
        falhou "$4 (direto=$direto workflow=$wf, esperado ≈$2 ±$3)"
    fi
}
confere_veredito() { # $1=esperado $2=rotulo (usa o veredito LIDO DA SAIDA DO WORKFLOW)
    local obtido; obtido="$(veredito_da_rodada "$PREFIXO.wf")"
    if [ "$obtido" = "$1" ]; then ok "$2"; else falhou "$2 (veredito medido: ${obtido:-ausente}, esperado $1)"; fi
}
confere_relatorio() { # $1=trecho (regex) $2=rotulo
    if grep -q "$1" "$PREFIXO.wf.relatorio"; then ok "$2"; else falhou "$2 (o relatorio nao traz '$1')"; fi
}
confere_duas_medicoes() { # $1=rotulo : as duas medicoes coincidem EXATAMENTE nas contagens
    local filtro='^(fila_idade_maxima_s|trilha_ultima_atividade_s)\|'
    grep -v -E "$filtro" "$PREFIXO.valores" | sort >"$PREFIXO.direto-contagens"
    grep -v -E "$filtro" "$PREFIXO.wf.valores" | sort >"$PREFIXO.wf-contagens"
    if diff -q "$PREFIXO.direto-contagens" "$PREFIXO.wf-contagens" >/dev/null 2>&1; then
        ok "$1"
    else
        falhou "$1 (divergencia: $(diff "$PREFIXO.direto-contagens" "$PREFIXO.wf-contagens" | head -4 | tr '\n' ' '))"
    fi
}

rodada_do_estado() { # $1 = letra ; mede o estado por DOIS caminhos (psql e workflow)
    local letra="$1"
    garantir_trio || return 1
    PREFIXO="$LOG_DIR/estado-$letra"
    limpar_estado
    if ! semear_estado "$letra"; then
        falhou "estado $letra: a massa do bloco nao aplicou (ver $LOG_DIR/semear-estado-$letra.sql.log)"
        return 1
    fi
    executar_rodada "$PREFIXO"
    if [ "$RC_RODADA" -ne 0 ]; then
        falhou "estado $letra: a execucao do workflow no n8n falhou (rc=$RC_RODADA; ver $PREFIXO.out)"
        return 1
    fi
    if grep -q '"status": "success"' "$PREFIXO.out"; then
        ok "estado $letra: o workflow rodou no n8n por execucao real (status success)"
    else
        falhou "estado $letra: execucao sem status success (ver $PREFIXO.out)"
        return 1
    fi
    if ! python3 "$LEITOR" "$PREFIXO.out" "$PREFIXO.wf" >"$PREFIXO.wf.resumo" 2>"$PREFIXO.wf.leitura"; then
        falhou "estado $letra: nao consegui ler o resultado produzido pelo workflow ($(head -1 "$PREFIXO.wf.leitura"))"
        return 1
    fi
    medir_direto "$PREFIXO"
    if [ ! -s "$PREFIXO.valores" ]; then
        falhou "estado $letra: a medicao direta (psql) nao devolveu linhas"
        return 1
    fi
    ok "estado $letra: medicao replicada pelos dois caminhos ($(grep -c . "$PREFIXO.valores") linhas na direta, $(grep -c . "$PREFIXO.wf.valores") no workflow)"
    confere_duas_medicoes "estado $letra: as duas medicoes coincidem nas contagens (a mesma consulta por dois caminhos)"
    return 0
}

# ---------------------------------------------------------------------------
# passo 3 — estados A..H, cada um com UM sinal (o veredito tem de ser atribuivel)
# ---------------------------------------------------------------------------
cabecalho "passo 3 — estados A..H (massa pela trilha, rodada real no n8n)"

# --- estado A: sucesso -> OK
rodada_do_estado A && {
    confere_veredito OK "estado A: veredito OK (rodada saudavel)"
    confere_valor fila_pendentes "" 0 "estado A: fila sem evento esperando (fila_pendentes=0)"
    confere_valor fila_retry "" 0 "estado A: nenhum evento em retry (fila_retry=0)"
    confere_valor fila_no_teto "" 0 "estado A: nenhum evento no teto de tentativas (fila_no_teto=0)"
    confere_valor dead_letter_total "" 0 "estado A: nenhum dead-letter (dead_letter_total=0)"
    confere_valor dead_letter_sem_motivo "" 0 "estado A: nenhum dead-letter silencioso (dead_letter_sem_motivo=0)"
    confere_valor outbox_processado_total "" 2 "estado A: dois eventos entregues (outbox_processado_total=2)"
    confere_valor outbox_processado_sem_trilha "" 0 "estado A: todo sucesso tem prova na trilha (outbox_processado_sem_trilha=0)"
    confere_valor trilha_falhas "" 0 "estado A: nenhuma falha na trilha (trilha_falhas=0)"
    confere_valor trilha_recusas "" 0 "estado A: nenhuma recusa na trilha (trilha_recusas=0)"
    confere_valor trilha_direcao_nao_declarada "" 0 "estado A: nenhuma linha de trilha fora das portas (trilha_direcao_nao_declarada=0)"
    confere_valor trilha_sem_conclusao "" 0 "estado A: nenhuma trilha fechada sem conclusao (trilha_sem_conclusao=0)"
    confere_valor trilha_por_status "postgres->odoo/COMPLETED" 2 "estado A: as duas entregas estao na porta PG -> Odoo (trilha_por_status=2)"
    confere_valor trilha_por_status "odoo->postgres/COMPLETED" 0 "estado A: a porta de ingestao nao teve atividade (grade declarada = 0, nao linha faltando)"
    confere_valor_tolerancia trilha_ultima_atividade_s 120 30 "estado A: a ultima atividade da trilha tem ~120s (medida na unidade do contrato)"
    confere_valor fila_idade_maxima_s "" 0 "estado A: fila sem espera (fila_idade_maxima_s=0)"
    confere_relatorio '^VEREDITO: OK ' "estado A: a linha do veredito e' a declarada no contrato"
}

# --- estado B: falha transitoria da trilha -> ATENCAO
rodada_do_estado B && {
    confere_veredito ATENCAO "estado B: veredito ATENCAO pela falha transitoria da trilha"
    confere_valor trilha_falhas "" 1 "estado B: a falha transitoria esta' contada (trilha_falhas=1)"
    confere_valor fila_retry "" 1 "estado B: o evento voltou para a fila (fila_retry=1)"
    confere_valor dead_letter_total "" 0 "estado B: falha transitoria NAO e' dead-letter (dead_letter_total=0)"
    confere_valor trilha_recusas "" 0 "estado B: nenhuma recusa definitiva (trilha_recusas=0)"
    confere_relatorio '\[ATENCAO\] trilha_falhas' "estado B: o relatorio marca a metrica que decidiu (trilha_falhas)"
}

# --- estado C: dead-letter SEM motivo -> CRITICO (a falha engolida)
rodada_do_estado C && {
    confere_veredito CRITICO "estado C: veredito CRITICO pelo dead-letter SEM motivo"
    confere_valor dead_letter_total "" 1 "estado C: o dead-letter esta' contado (dead_letter_total=1)"
    confere_valor dead_letter_sem_motivo "" 1 "estado C: dead-letter SEM motivo medido (dead_letter_sem_motivo=1)"
    confere_valor trilha_recusas "" 1 "estado C: a recusa esta' na trilha (trilha_recusas=1)"
    confere_valor outbox_processado_sem_trilha "" 0 "estado C: o sucesso sem prova e' outro sinal (aqui zero)"
    confere_relatorio 'motivo=(SEM MOTIVO)' "estado C: o dead-letter sem motivo aparece como (SEM MOTIVO) no relatorio"
    confere_relatorio '\[CRITICO\] dead_letter_sem_motivo' "estado C: o relatorio marca a metrica critica (dead_letter_sem_motivo)"
}

# --- estado D: dead-letter COM motivo -> ATENCAO e motivo VISIVEL
rodada_do_estado D && {
    confere_veredito ATENCAO "estado D: veredito ATENCAO pelo dead-letter com motivo"
    confere_valor dead_letter_total "" 1 "estado D: o dead-letter esta' contado (dead_letter_total=1)"
    confere_valor dead_letter_sem_motivo "" 0 "estado D: o dead-letter TEM motivo (dead_letter_sem_motivo=0)"
    confere_valor trilha_recusas "" 1 "estado D: a recusa nomeada esta' na trilha (trilha_recusas=1)"
    # padrao BRE: parentese e' LITERAL sem barra; `\(` seria agrupamento (o grep antigo nao mordia aqui)
    confere_relatorio 'CNPJ invalido (HTTP 422)' "estado D: o relatorio mostra o MOTIVO do dead-letter (contagem sem motivo nao e observabilidade)"
    confere_relatorio 'outbox_dead_letter' "estado D: o detalhe do dead-letter esta' na secao declarada do relatorio"
    confere_relatorio 'outbox_dead_letter (1)' "estado D: o dead-letter aparece UMA vez na lista (a consulta de detalhes roda uma vez por rodada, nao uma por linha de metrica)"
}

# --- estado E: PROCESSED sem trilha de sucesso -> CRITICO
rodada_do_estado E && {
    confere_veredito CRITICO "estado E: veredito CRITICO pelo PROCESSED sem trilha de sucesso"
    confere_valor outbox_processado_total "" 1 "estado E: um evento consta como entregue (outbox_processado_total=1)"
    confere_valor outbox_processado_sem_trilha "" 1 "estado E: o sucesso sem prova na trilha foi medido (outbox_processado_sem_trilha=1)"
    confere_valor trilha_por_status "postgres->odoo/COMPLETED" 0 "estado E: a grade declarada da porta fica em zero (sem linha faltando)"
    confere_relatorio '\[CRITICO\] outbox_processado_sem_trilha' "estado E: o relatorio marca o sucesso sem prova como critico"
}

# --- estado F: fila no teto -> CRITICO
rodada_do_estado F && {
    confere_veredito CRITICO "estado F: veredito CRITICO pela fila no teto"
    confere_valor fila_pendentes "" 51 "estado F: a fila tem 51 eventos esperando (fila_pendentes=51)"
    confere_valor fila_no_teto "" 1 "estado F: ha' evento PENDING no teto de tentativas (fila_no_teto=1)"
    confere_valor_tolerancia fila_idade_maxima_s 1200 60 "estado F: a espera mais longa da fila tem ~1200s"
    confere_relatorio '\[CRITICO\] fila_no_teto' "estado F: o relatorio marca a fila no teto como critico"
}

# --- estado G: direcao fora do vocabulario -> CRITICO
rodada_do_estado G && {
    confere_veredito CRITICO "estado G: veredito CRITICO pela direcao fora do vocabulario"
    confere_valor trilha_direcao_nao_declarada "" 1 "estado G: a linha de trilha com direcao fora do vocabulario foi medida (trilha_direcao_nao_declarada=1)"
    confere_ausencia trilha_por_status "odoo->odoo/COMPLETED" "estado G: a direcao nao declarada NAO entra na grade da metrica dimensional"
    confere_valor trilha_total "" 1 "estado G: a linha existe na trilha (trilha_total=1) — ela e' contada onde o contrato manda"
    confere_relatorio '\[CRITICO\] trilha_direcao_nao_declarada' "estado G: o relatorio nomeia a direcao fora do vocabulario"
}

# --- estado H: trilha COMPLETED sem conclusao -> CRITICO
rodada_do_estado H && {
    confere_veredito CRITICO "estado H: veredito CRITICO pela trilha COMPLETED sem conclusao"
    confere_valor trilha_sem_conclusao "" 1 "estado H: a trilha fechada sem completed_at foi medida (trilha_sem_conclusao=1)"
    confere_valor trilha_por_status "postgres->odoo/COMPLETED" 1 "estado H: a linha entra na grade da porta declarada"
    confere_relatorio '\[CRITICO\] trilha_sem_conclusao' "estado H: o relatorio marca a trilha sem conclusao como critica"
}

# ---------------------------------------------------------------------------
# passo 4 — somente leitura em execucao real: uma rodada a mais nao muda nada
# ---------------------------------------------------------------------------
cabecalho "passo 4 — a observabilidade nao escreve no que observa"
garantir_trio || true
RETRATO_ANTES="$(retrato_tabelas)"
executar_rodada "$LOG_DIR/estado-H-extra"
RC_EXTRA="$RC_RODADA"
python3 "$LEITOR" "$LOG_DIR/estado-H-extra.out" "$LOG_DIR/estado-H-extra.wf" >"$LOG_DIR/estado-H-extra.wf.resumo" 2>&1
RETRATO_DEPOIS="$(retrato_tabelas)"
if [ "$RC_EXTRA" -eq 0 ] && [ -n "$RETRATO_ANTES" ] && [ "$RETRATO_ANTES" = "$RETRATO_DEPOIS" ]; then
    ok "retrato das duas tabelas IDENTICO depois de uma rodada completa (somente leitura em execucao real)"
else
    falhou "a rodada extra mexeu no que observa (antes=$RETRATO_ANTES depois=$RETRATO_DEPOIS)"
fi
VALORES_EXTRA="$(valor_de "$LOG_DIR/estado-H-extra.wf.valores" trilha_sem_conclusao "")"
[ -n "$VALORES_EXTRA" ] && awk -v v="$VALORES_EXTRA" 'BEGIN { exit !(v > 0.999 && v < 1.001) }' \
    && ok "a rodada extra mediu o mesmo estado (medicao estavel, nao efeito colateral)" \
    || falhou "a rodada extra mediu outro estado (trilha_sem_conclusao=$VALORES_EXTRA)"

# ---------------------------------------------------------------------------
# fecho — ambiente, segredo, sha256 e limpeza
# ---------------------------------------------------------------------------
cabecalho "fecho — ambiente, segredo e sha256"
if [ "$(docker inspect -f '{{.State.Running}}' "$DEV_PG_CT" 2>/dev/null)" = "true" ]; then
    DEV_PG_DEPOIS="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)"
    [ "$DEV_PG_ANTES" = "$DEV_PG_DEPOIS" ] \
        && ok "o banco do dev nao foi tocado (lista de bancos identica antes/depois)" \
        || falhou "a lista de bancos do dev MUDOU (antes=$DEV_PG_ANTES depois=$DEV_PG_DEPOIS)"
else
    ok "instancia do dev nao esta de pe: nada do aceite tocou nela (trio proprio)"
fi
HOMOLOG_PROD_DEPOIS="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
[ "$HOMOLOG_PROD_ANTES" = "$HOMOLOG_PROD_DEPOIS" ] \
    && ok "homolog/prod sem arquivo novo (antes=$HOMOLOG_PROD_ANTES depois=$HOMOLOG_PROD_DEPOIS)" \
    || falhou "homolog/prod MUDOU (antes=$HOMOLOG_PROD_ANTES depois=$HOMOLOG_PROD_DEPOIS)"
if grep -rl "$SENHA" "$N8N_HOME" >/dev/null 2>&1; then
    falhou "a senha do banco descartavel esta' EM CLARO no cofre do n8n"
else
    ok "nenhum segredo em claro no cofre do n8n descartavel (credencial criptografada pelo n8n)"
fi
sha256_dos_artefatos "$LOG_DIR/sha256-depois.txt"
VEREDITO_SHA="$(veredito_sha256 "$LOG_DIR/sha256-antes.txt" "$LOG_DIR/sha256-depois.txt")"
[ "$VEREDITO_SHA" = "IDENTICO" ] \
    && ok "sha256 dos 10 artefatos sob teste IDENTICO ao das guardas (nada mudou durante o aceite)" \
    || falhou "sha256 dos artefatos MUDOU durante o aceite (ver $LOG_DIR/sha256-depois.txt)"
info "logs desta verificacao: $LOG_DIR"

resumo
