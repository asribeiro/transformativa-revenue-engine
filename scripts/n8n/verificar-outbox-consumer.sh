#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E02-T01 — consumidor de outbox (n8n) do board
# transformativa-revenue-engine (card t_ba84b412).
#
# Criterios de aceitacao (definidos no inicio do card, registrados no card e no runbook
# docs/runbooks/n8n-outbox-consumer.md):
#   AC1 consumidor DECLARADO: o workflow versionado e' o resultado dos artefatos versionados
#       (contrato + nucleo + SQL) — o Code node embute o nucleo byte a byte e o contrato igual,
#       e o SQL dos nos e' o arquivo. Divergencia = reprovacao (lente estrutural);
#   AC2 porta unica: a UNICA saida e' POST /tf/api/v1/<operacao> por $env.TRE_API_BASE com a
#       credencial declarada por id/nome; nenhum caminho paralelo (XML-RPC/JSON-RPC), nenhum
#       host literal, nenhum segredo no versionado;
#   AC3 envelope: evento sem `event_version` (ou versao desconhecida) e' RECUSADO sem chamada
#       (DEAD_LETTER + motivo nomeado) — data contract §6 regra 5;
#   AC4 evento fora do contrato / identidade ausente / campo exigido ausente: recusa nomeada,
#       nunca ignorado em silencio;
#   AC5 entrega ponta a ponta: evento da fila vira escrita de NEGOCIO no CRM pela porta unica
#       (parceiro criado com o `tf_company_id` do evento) + `outbox_events` PROCESSED com
#       `processed_at` + UMA linha de `sync_events` com a chave derivada do evento;
#   AC6 retry limitado e VISIVEL: falha transitoria incrementa `attempts` e deixa o motivo;
#       no teto o evento vira DEAD_LETTER sem nova chamada; o retry nao duplica a trilha
#       (chave UNIQUE) nem o registro no CRM;
#   AC7 recusa definitiva da API (409/422/503 terminal) para o evento como DEAD_LETTER com o
#       codigo do erro na trilha — e o evento NAO volta para a fila;
#   AC8 trilha sem payload de segredo + ambiente intocado: trio DESCARTÁVEL proprio
#       (postgres + odoo + n8n), dev/homolog/producao medidos antes e depois, nenhum DDL ou
#       escrita no banco do dev.
#
# TEST PLAN (executado por este script, na VPS, por execucao real):
#   passo 0  lente estrutural (python puro) e suite do nucleo (node dentro da imagem n8n)
#   guardas  docker, imagens, ferramentas, artefatos em disco, banco descartavel, ambiente antes
#   trio     postgres descartavel + schema do contrato + Odoo com o modulo instalado +
#            n8n descartavel com credenciais e workflow importados
#   ciclo 1  fila com 7 eventos (valido, atualizacao, sem versao, fora do contrato, sem name,
#            sem identidade, identidade ambigua) -> medicoes item a item no banco
#   ciclo 2  Odoo PARADO -> falha de transporte (RETRY + attempts=1, trilha FAILED)
#   ciclo 3  Odoo de volta -> o retry entrega (PROCESSED + attempts=2, trilha unica, sem duplicata)
#   ciclo 4  evento ja no teto de tentativas -> DEAD_LETTER sem chamada e sem escrita
#   final    contagem de chamadas autenticadas, segredo nos logs, ambiente depois, fecho do
#            sha256 (fixado nas guardas, reconferido no fim) e limpeza
#
# O modo --prova-de-dente e' FAIL-CLOSED: roda primeiro um sub-run NAO mutado (baseline) que
# tem de ficar verde, depois os 4 mutantes, e so' fecha com DENTE_OK se TODOS os vereditos
# forem DENTE_CUMPRIDO e os dois juizes (dente e sha256) estiverem conferidos. Qualquer outro
# veredito (NAO_CONTA / MUTACAO_SEM_DENTE / MUTACAO_NAO_APLICADA) fecha com DENTE_FALHOU e exit 1.
#
# Uso (NA VPS, a partir de ARQUIVO — a prova de dente reinvoca o proprio script):
#   bash scripts/n8n/verificar-outbox-consumer.sh
#   bash scripts/n8n/verificar-outbox-consumer.sh --apenas-codigo
#   bash scripts/n8n/verificar-outbox-consumer.sh --apenas-consumo
#   bash scripts/n8n/verificar-outbox-consumer.sh --prova-de-dente
#   bash scripts/n8n/verificar-outbox-consumer.sh --manter         (nao limpa o trio no fim)
#
# Variaveis: TRE_WORKFLOW (workflow sob teste), TRE_MODULO_DIR, TRE_BANCO (banco do Odoo),
# TRE_BANCO_SI (banco do contrato), TRE_IMAGEM/_PG/_N8N, TRE_LOG_DIR, TRE_PG_USER,
# TRE_DEV_PG_CT, TRE_DEV_HOMOLOG_PROD, TRE_MANTER_BANCO.
#
# Saida: um item por linha (OK/FALHOU), resumo final em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir   2 = uso errado
# ============================================================================
set -u

MODULO="transformativa_sales_ai"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
RAIZ_REPO="$(cd "$AQUI/../.." && pwd)"
MODULO_DIR="${TRE_MODULO_DIR:-$RAIZ_REPO/odoo/addons/$MODULO}"
WORKFLOW="${TRE_WORKFLOW:-$RAIZ_REPO/n8n/workflows/TRE-outbox-consumer.json}"
CONTRATO="$RAIZ_REPO/n8n/contracts/outbox-consumer.v1.json"
NUCLEO="$RAIZ_REPO/n8n/codigo/nucleo-outbox-consumer.js"
SQL_LER="$RAIZ_REPO/n8n/sql/ler-pendentes.sql"
SQL_REGISTRAR="$RAIZ_REPO/n8n/sql/registrar-resultado.sql"
MIGRATION="$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql"
MONTADOR="$RAIZ_REPO/scripts/n8n/montar_workflow.py"
ESTRUTURAL="$RAIZ_REPO/scripts/n8n/conferir_contrato_e_workflow.py"
SUITE_NUCLEO="$RAIZ_REPO/scripts/n8n/testar_nucleo_consumidor.js"
MUTADOR="$RAIZ_REPO/scripts/n8n/mutar_workflow.py"
PREPARADOR="$RAIZ_REPO/scripts/odoo/preparar_api_teste.py"
MASSA_AMBIGUA="$AQUI/preparar_massa_ambigua.py"

BANCO="${TRE_BANCO:-tre_e02_outbox}"
BANCO_SI="${TRE_BANCO_SI:-sales_intelligence}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
IMAGEM_N8N="${TRE_IMAGEM_N8N:-n8nio/n8n:latest}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-outbox-consumer}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER="${TRE_MANTER_BANCO:-0}"
ID_WORKFLOW="TREOUTBOXCONSUM1"

# Identidades da fase de fila (o que o contrato chama de identidade: UUID canonico de
# `organizations.id`, que o consumidor mapeia para `tf_company_id`).
E1="a1111111-1111-4111-8111-111111111111"   # valido            -> PROCESSED
E2="a2222222-2222-4222-8222-222222222222"   # mesma identidade  -> PROCESSED (atualiza)
E3="a3333333-3333-4333-8333-333333333333"   # sem event_version -> DEAD_LETTER sem chamada
E4="a4444444-4444-4444-8444-444444444444"   # fora do contrato  -> DEAD_LETTER
E5="a5555555-5555-4555-8555-555555555555"   # sem `name`        -> DEAD_LETTER
E6="a6666666-6666-4666-8666-666666666666"   # sem identidade    -> DEAD_LETTER
E7="a7777777-7777-4777-8777-777777777777"   # identidade ambigua na API -> DEAD_LETTER, 1 chamada
E8="a8888888-8888-4888-8888-888888888888"   # Odoo parado       -> RETRY, depois PROCESSED
E9="a9999999-9999-4999-8999-999999999999"   # ja' no teto       -> DEAD_LETTER sem chamada
ORG1="11111111-1111-4111-8111-111111111111" # identidade canonica da E1/E2
ORG3="33333333-3333-4333-8333-333333333333"
ORG4="44444444-4444-4444-8444-444444444444"
ORG5="55555555-5555-4555-8555-555555555555"
ORG7="77777777-7777-4777-8777-777777777777"
ORG8="88888888-8888-4888-8888-888888888888"
ORG9="99999999-9999-4999-8999-999999999999"
PARCEIRO_AMB1="b1111111-1111-4111-8111-111111111111"
PARCEIRO_AMB2="b2222222-2222-4222-8222-222222222222"
DOMINIO_AMBIGUO="ambiguo.example"
DOMINIO_E1="alfa.example"
NOME_E1="Alfa Consultoria Ltda"
NOME_E1_ATUALIZADO="Alfa Consultoria Ltda (atualizada)"
TETO=3

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-codigo) MODO=codigo ;;
        --apenas-consumo) MODO=consumo ;;
        --prova-de-dente) MODO=dente ;;
        --manter) MANTER=1 ;;
        --workflow) shift; WORKFLOW="${1:-}" ;;
        --log-dir) shift; LOG_DIR="${1:-}" ;;
        --banco) shift; BANCO="${1:-}" ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
    shift
done

ITENS=0
FALHAS=0
SUFIXO="$$-$RANDOM"
PG_TMP="e02t01-pg-$SUFIXO"
API_TMP="e02t01-api-$SUFIXO"
N8N_TMP="e02t01-n8n-$SUFIXO"
NET_TMP="e02t01-net-$SUFIXO"
DESC_DIR=""
API_CT=""
N8N_HOME=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
limpar()   { printf '%s' "$1" | tr -d '[:space:]'; }
podar()    { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: OUTBOX_CONSUMER_OK ($ITENS itens, 0 falhas) banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG+$IMAGEM_N8N workflow=$WORKFLOW"
        exit 0
    fi
    echo "RESULTADO: OUTBOX_CONSUMER_FALHOU ($ITENS itens, $FALHAS falha(s)) banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG+$IMAGEM_N8N workflow=$WORKFLOW"
    exit 1
}

# ---------------------------------------------------------------------------
# sha256 dos artefatos sob teste: fixado nas guardas e RECONFERIDO no fecho, item a item.
# A reconferencia so' vale com o juiz conferido (`controle_do_juiz_sha`): sem isso o item
# poderia comparar duas medidas do mesmo nada e sair verde (o registro ja' afirmou isso
# antes de o item existir — corrigido na rodada 2).
# ---------------------------------------------------------------------------
sha256_dos_artefatos() { # $1 = arquivo onde gravar "<sha>  <caminho relativo>"
    local destino="$1" arquivo
    : >"$destino"
    for arquivo in "$CONTRATO" "$NUCLEO" "$SQL_LER" "$SQL_REGISTRAR" "$WORKFLOW"; do
        printf '%s  %s\n' "$(sha256sum "$arquivo" | cut -d' ' -f1)" "${arquivo#"$RAIZ_REPO"/}" >>"$destino"
    done
}

veredito_sha256() { # $1=arquivo "antes"  $2=arquivo "depois"  ->  IDENTICO | MUDOU
    if diff -q "$1" "$2" >/dev/null 2>&1; then echo IDENTICO; else echo MUDOU; fi
}

# ---------------------------------------------------------------------------
# --apenas-codigo: lente estrutural (python) + suite do nucleo (node na imagem do n8n)
# ---------------------------------------------------------------------------
passo_codigo() {
    cabecalho "passo 0 — lente estrutural (contrato x workflow)"
    if [ ! -f "$ESTRUTURAL" ]; then falhou "verificador estrutural ausente: $ESTRUTURAL"; resumo; fi
    SAIDA_ESTRUTURAL="$(python3 "$ESTRUTURAL" --raiz "$RAIZ_REPO" 2>&1)"
    printf '%s\n' "$SAIDA_ESTRUTURAL" >"$LOG_DIR/0-estrutural.out"
    printf '%s\n' "$SAIDA_ESTRUTURAL" | grep -E '^(OK|FALHOU) ' | sed 's/^/      /'
    RESULTADO_ESTRUTURAL="$(printf '%s\n' "$SAIDA_ESTRUTURAL" | grep -E '^RESULTADO: ' | tail -1)"
    case "$RESULTADO_ESTRUTURAL" in
        *CONTRATO_WORKFLOW_OK*) ok "lente estrutural: $RESULTADO_ESTRUTURAL" ;;
        *) falhou "lente estrutural NAO passou: ${RESULTADO_ESTRUTURAL:-sem linha de resultado}" ;;
    esac

    cabecalho "passo 0b — suite do nucleo (node, dentro da imagem do n8n)"
    if ! docker image inspect "$IMAGEM_N8N" >/dev/null 2>&1; then
        falhou "imagem $IMAGEM_N8N ausente: sem node nao ha' medicao do nucleo"; resumo
    fi
    MUTADO_DIR="$(dirname "$WORKFLOW")"
    SAIDA_NUCLEO="$(docker run --rm -v "$RAIZ_REPO":/repo:ro -v "$MUTADO_DIR":/mutado:ro \
        --entrypoint node "$IMAGEM_N8N" /repo/scripts/n8n/testar_nucleo_consumidor.js \
        --workflow "/mutado/$(basename "$WORKFLOW")" 2>&1)"
    printf '%s\n' "$SAIDA_NUCLEO" >"$LOG_DIR/0b-nucleo.out"
    printf '%s\n' "$SAIDA_NUCLEO" | grep -E '^(OK|FALHOU) ' | sed 's/^/      /'
    RESULTADO_NUCLEO="$(printf '%s\n' "$SAIDA_NUCLEO" | grep -E '^RESULTADO: ' | tail -1)"
    case "$RESULTADO_NUCLEO" in
        *NUCLEO_CONSUMIDOR_OK*) ok "suite do nucleo: $RESULTADO_NUCLEO" ;;
        *) falhou "suite do nucleo NAO passou: ${RESULTADO_NUCLEO:-sem linha de resultado}" ;;
    esac
}

# ---------------------------------------------------------------------------
# --prova-de-dente: cada mutacao roda o aceite inteiro numa COPIA do workflow.
# Um dente so' conta quando o sub-run (a) passou pelas guardas, (b) chegou a executar
# o ciclo de consumo e (c) reprovou O ITEM que a mutacao quebra. O juiz e' testado com
# saidas sinteticas — sem isso, ambiente quebrado viraria "dente cumprido".
# ---------------------------------------------------------------------------
juizo_do_dente() { # $1=arquivo de saida do sub-run  $2=trecho do item esperado
    local saida="$1" esperado="$2"
    if ! grep -q 'FASE_CONSUMO_OK' "$saida"; then
        echo "NAO_CONTA (o sub-run nao chegou a fase de consumo)"
        return
    fi
    if grep -q "^OK    .*$esperado" "$saida"; then
        echo "MUTACAO_SEM_DENTE (o item esperado continuou OK)"
        return
    fi
    if grep -q "^FALHOU .*$esperado" "$saida"; then
        echo "DENTE_CUMPRIDO"
        return
    fi
    echo "NAO_CONTA (item esperado ausente na saida — ancora quebrada)"
}

controle_do_juiz() {
    local dir="$1" ok_controles=0 faltas=0
    # 1) a mutacao nao mexeu no comportamento: o item esperado CONTINUA OK
    printf 'FASE_CONSUMO_OK\nOK    E3 sem event_version -> DEAD_LETTER sem chamada\n' >"$dir/controle_1.out"
    # 2) a mutacao quebrou o item: e' isso que um dente cumprido devolve
    printf 'FASE_CONSUMO_OK\nFALHOU E3 sem event_version -> DEAD_LETTER sem chamada\n' >"$dir/controle_2.out"
    # 3) o sub-run nem chegou a fase de consumo (ambiente quebrado nao e' dente)
    printf 'FALHOU guardas do ambiente\n' >"$dir/controle_3.out"
    # 4) a fase rodou, mas o item esperado nao aparece na saida (ancora quebrada nao e' dente)
    printf 'FASE_CONSUMO_OK\nOK    E1 evento valido -> PROCESSED com attempts=1\n' >"$dir/controle_4.out"
    local c1 c2 c3 c4
    c1="$(juizo_do_dente "$dir/controle_1.out" 'E3 sem event_version')"
    c2="$(juizo_do_dente "$dir/controle_2.out" 'E3 sem event_version')"
    c3="$(juizo_do_dente "$dir/controle_3.out" 'E3 sem event_version')"
    c4="$(juizo_do_dente "$dir/controle_4.out" 'E9 teto de tentativas')"
    case "$c1" in *MUTACAO_SEM_DENTE*) ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c2" in *DENTE_CUMPRIDO*) ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c3" in *NAO_CONTA*) ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    case "$c4" in *NAO_CONTA*) ok_controles=$((ok_controles + 1)) ;; *) faltas=$((faltas + 1)) ;; esac
    if [ "$faltas" -eq 0 ]; then
        ok "controle do juiz do dente (4 saidas sinteticas: sem dente, dente, ambiente, ancora)"
    else
        falhou "controle do juiz do dente ($faltas de 4 saidas sinteticas julgadas errado)"
    fi
}

controle_do_juiz_sha() { # o item de sha256 nao pode ser vacuO: 2 saidas sinteticas
    local dir="$1" certos=0
    printf 'a1  n8n/contracts/outbox-consumer.v1.json\nb2  n8n/codigo/nucleo-outbox-consumer.js\n' >"$dir/sha-antes.txt"
    cp "$dir/sha-antes.txt" "$dir/sha-igual.txt"
    printf 'a1  n8n/contracts/outbox-consumer.v1.json\nzz  n8n/codigo/nucleo-outbox-consumer.js\n' >"$dir/sha-mudado.txt"
    [ "$(veredito_sha256 "$dir/sha-antes.txt" "$dir/sha-igual.txt")" = "IDENTICO" ] && certos=$((certos + 1))
    [ "$(veredito_sha256 "$dir/sha-antes.txt" "$dir/sha-mudado.txt")" = "MUDOU" ] && certos=$((certos + 1))
    if [ "$certos" -eq 2 ]; then
        ok "controle do juiz do sha256 (2 saidas sinteticas: identico e mudado)"
    else
        falhou "controle do juiz do sha256 (so' $certos de 2 saidas sinteticas julgadas certo)"
    fi
}

if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e02t01-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    cabecalho "--prova-de-dente: o aceite tem dentes?"
    controle_do_juiz "$DENTE_DIR"
    controle_do_juiz_sha "$DENTE_DIR"
    FALHAS_JUIZ=$FALHAS   # o que os JUÍZES erraram — separado do resto para o veredito ser legivel

    # Controle do proprio controle: o sub-run NAO mutado tem de estar VERDE antes de contar
    # dente. Sem esta linha, ambiente quebrado devolveria NAO_CONTA em todos os dentes e o
    # veredito final verde seria fail-open — o defeito da rodada 1 deste card.
    cabecalho "--prova-de-dente: baseline NAO mutado (o ambiente mede?)"
    BASELINE_OK=0
    TRE_LOG_DIR="$DENTE_DIR/logs-baseline" TRE_WORKFLOW="$WORKFLOW" TRE_MANTER_BANCO=0 \
        bash "$(readlink -f "$0")" --apenas-consumo >"$DENTE_DIR/baseline.out" 2>&1
    BASELINE_RC=$?
    BASELINE_RES="$(grep -E '^RESULTADO: ' "$DENTE_DIR/baseline.out" | tail -1)"
    if [ "$BASELINE_RC" -eq 0 ] && printf '%s' "$BASELINE_RES" | grep -q 'OUTBOX_CONSUMER_OK'; then
        BASELINE_OK=1
        ok "baseline NAO mutado verde ($BASELINE_RES) — o ambiente mede antes de contar dentes"
    else
        falhou "baseline NAO mutado nao ficou verde (rc=$BASELINE_RC; ${BASELINE_RES:-sem linha de resultado}) — ambiente quebrado nao e' dente"
    fi

    cabecalho "--prova-de-dente: 4 mutacoes nomeadas"
    # mutacao|item esperado que TEM de reprovar|por que a mutacao quebra o item
    MUTACOES="sem_validacao_de_envelope|E3 sem event_version|sem a exigencia de event_version o evento passa e o estado final deixa de ser DEAD_LETTER
sem_incremento_de_tentativas|E8 falha de transporte|sem o incremento o attempts do evento que falhou nao muda
sem_teto_de_tentativas|E9 teto de tentativas|sem o teto o evento esgotado volta a ser entregue
mapeamento_trocado|E1 o parceiro do CRM tem|com o mapeamento trocado o parceiro nasce com outro dominio"
    # O laco roda em subshell (pipe): os vereditos vao para ARQUIVO e a agregacao vem depois.
    # Antes (rodada 1) o laco so' imprimia e o script fechava com DENTE_OK incondicional.
    VEREDITOS="$DENTE_DIR/vereditos.txt"
    : >"$VEREDITOS"
    printf '%s\n' "$MUTACOES" | while IFS='|' read -r mutacao esperado porque; do
        [ -z "$mutacao" ] && continue
        ALVO="$DENTE_DIR/$mutacao.json"
        if ! python3 "$MUTADOR" --mutacao "$mutacao" --entrada "$WORKFLOW" --saida "$ALVO" >"$DENTE_DIR/$mutacao.mutacao" 2>&1; then
            printf 'DENTE %-32s MUTACAO_NAO_APLICADA (%s)\n' "$mutacao" "$(head -1 "$DENTE_DIR/$mutacao.mutacao")"
            printf '%s|MUTACAO_NAO_APLICADA\n' "$mutacao" >>"$VEREDITOS"
            continue
        fi
        SAIDA_RUN="$DENTE_DIR/$mutacao.out"
        TRE_LOG_DIR="$DENTE_DIR/logs-$mutacao" TRE_WORKFLOW="$ALVO" TRE_MANTER_BANCO=0 \
            bash "$(readlink -f "$0")" --apenas-consumo >"$SAIDA_RUN" 2>&1 || true
        VEREDITO="$(juizo_do_dente "$SAIDA_RUN" "$esperado")"
        printf 'DENTE %-32s %s\n' "$mutacao" "$VEREDITO"
        printf '      item esperado: %s\n' "$esperado"
        printf '      razao: %s\n' "$porque"
        printf '%s|%s\n' "$mutacao" "$VEREDITO" >>"$VEREDITOS"
    done

    # Agregacao fail-closed: qualquer veredito que nao seja DENTE_CUMPRIDO (NAO_CONTA,
    # MUTACAO_SEM_DENTE, MUTACAO_NAO_APLICADA) reprova o modo — e o juiz tambem decide.
    TOTAL_DENTES=0
    DENTES_CUMPRIDOS=0
    SEM_DENTE=0
    while IFS='|' read -r _mutacao _veredito; do
        [ -z "$_mutacao" ] && continue
        TOTAL_DENTES=$((TOTAL_DENTES + 1))
        case "$_veredito" in
            *DENTE_CUMPRIDO*) DENTES_CUMPRIDOS=$((DENTES_CUMPRIDOS + 1)) ;;
            *) SEM_DENTE=$((SEM_DENTE + 1)) ;;
        esac
    done <"$VEREDITOS"

    echo
    info "resumo do dente: $DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos; baseline=$BASELINE_OK; juiz=$([ "$FALHAS_JUIZ" -eq 0 ] && echo conferido || echo COM_FALTA)"
    if [ "$FALHAS_JUIZ" -ne 0 ]; then
        echo "RESULTADO: OUTBOX_CONSUMER_DENTE_FALHOU (controle do juiz com falta; $DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos)"
        exit 1
    fi
    if [ "$TOTAL_DENTES" -eq 0 ]; then
        echo "RESULTADO: OUTBOX_CONSUMER_DENTE_FALHOU (0 dente medido: nenhuma mutacao aplicada)"
        exit 1
    fi
    if [ "$BASELINE_OK" != "1" ]; then
        echo "RESULTADO: OUTBOX_CONSUMER_DENTE_FALHOU (baseline NAO mutado nao ficou verde; $SEM_DENTE sem dente de $TOTAL_DENTES)"
        exit 1
    fi
    if [ "$DENTES_CUMPRIDOS" -ne "$TOTAL_DENTES" ]; then
        echo "RESULTADO: OUTBOX_CONSUMER_DENTE_FALHOU ($SEM_DENTE sem dente de $TOTAL_DENTES) mutacoes=$TOTAL_DENTES dentes=$DENTES_CUMPRIDOS"
        exit 1
    fi
    echo "RESULTADO: OUTBOX_CONSUMER_DENTE_OK ($DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos; juiz conferido; baseline nao mutado verde)"
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
for img in "$IMAGEM" "$IMAGEM_PG" "$IMAGEM_N8N"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
        ok "imagem $img presente ($(docker image inspect -f '{{index .RepoDigests 0}}' "$img" 2>/dev/null))"
    else
        falhou "imagem $img ausente (nada a medir)"; resumo
    fi
done
command -v openssl >/dev/null 2>&1 && ok "openssl disponivel (segredo dos descartaveis)" \
    || { falhou "openssl ausente"; resumo; }
command -v curl >/dev/null 2>&1 && ok "curl disponivel (sonda HTTP do Odoo)" \
    || { falhou "curl ausente"; resumo; }
python3 --version >/dev/null 2>&1 && ok "python3 disponivel (lente estrutural e mutacoes)" \
    || { falhou "python3 ausente"; resumo; }
for arquivo in "$WORKFLOW" "$CONTRATO" "$NUCLEO" "$SQL_LER" "$SQL_REGISTRAR" "$MONTADOR" \
               "$MUTADOR" "$MASSA_AMBIGUA" "$MODULO_DIR/__manifest__.py" \
               "$MODULO_DIR/api/politica_api.json" "$PREPARADOR" "$MIGRATION"; do
    if [ -f "$arquivo" ]; then ok "consumidor em disco: ${arquivo#"$RAIZ_REPO"/}"; else falhou "ausente: $arquivo"; resumo; fi
done
info "sha256 dos artefatos sob teste (fixado agora e reconferido no fecho):"
sha256_dos_artefatos "$LOG_DIR/sha256-antes.txt"
sed 's/^/      /' "$LOG_DIR/sha256-antes.txt"
if [ "$(wc -l <"$LOG_DIR/sha256-antes.txt" | tr -d ' ')" = "5" ]; then
    ok "sha256 dos 5 artefatos sob teste fixado (base da reconferencia do fecho)"
else
    falhou "nao consegui fixar o sha256 dos 5 artefatos (ver $LOG_DIR/sha256-antes.txt)"
fi
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e' do ambiente — so' banco descartavel"; resumo ;;
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

# ---------------------------------------------------------------------------
# trio descartavel proprio (ver o cabecalho: por que nao usar o dev)
# ---------------------------------------------------------------------------
cabecalho "trio descartavel proprio (postgres + odoo + n8n)"
DESC_DIR="$(mktemp -d /tmp/verificacao-outbox-XXXXXX)"
chmod 700 "$DESC_DIR"
N8N_HOME="$DESC_DIR/n8n-home"
mkdir -p "$N8N_HOME"
chmod 700 "$N8N_HOME"
chown 1000:1000 "$N8N_HOME" 2>/dev/null || chown 1000:1000 "$N8N_HOME"
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
chown 100:101 "$DESC_DIR/odoo.conf"
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
        info "--manter: trio preservado (containers $PG_TMP/$API_CT, rede $NET_TMP, diretorio $N8N_HOME)"
        return 0
    fi
    docker rm -f "$API_CT" >/dev/null 2>&1
    docker rm -f "$PG_TMP" >/dev/null 2>&1
    [ -n "$N8N_HOME" ] && rm -rf "$N8N_HOME"
    docker network rm "$NET_TMP" >/dev/null 2>&1
    [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR"
}
trap limpeza EXIT

si()      { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -tAc "$1" 2>/dev/null; }
odoo_db() { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -tAc "$1" 2>/dev/null; }
logs_api(){ docker logs "$API_CT" 2>&1; }
odoo_ci() { # $1=log; restantes = args do odoo (container descartavel, --stop-after-init)
    local log="$1"; shift
    docker run --rm --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        --entrypoint odoo "$IMAGEM" "$@" --stop-after-init --log-level=info >"$log" 2>&1
    echo $?
}

# ---------------------------------------------------------------------------
# passo 1 — schema do contrato no banco descartavel + modulo instalado no Odoo
# ---------------------------------------------------------------------------
cabecalho "passo 1 — schema do contrato e instalacao do modulo"
docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO_SI" >/dev/null 2>&1 \
    && ok "banco do contrato criado no descartavel ($BANCO_SI)" || falhou "nao criei $BANCO_SI"
docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -v ON_ERROR_STOP=1 -q <"$MIGRATION" >"$LOG_DIR/1-migration.log" 2>&1 \
    && ok "migration do contrato aplicada em $BANCO_SI" || falhou "migration nao aplicou (ver $LOG_DIR/1-migration.log)"
TABELAS="$(limpar "$(si 'select count(*) from information_schema.tables where table_schema='"'"'sales_intelligence'"'"'')")"
[ "$TABELAS" = "12" ] && ok "12 tabelas do contrato no banco descartavel" \
    || falhou "esperava 12 tabelas em sales_intelligence, medidas: ${TABELAS:-0}"

LOG_ATUAL="$LOG_DIR/1-instalacao.log"
RC="$(odoo_ci "$LOG_ATUAL" -d "$BANCO" -i "$MODULO" --without-demo=all --max-cron-threads=0)"
if [ "$RC" = "0" ]; then ok "instalacao do modulo $MODULO terminou com exit 0"; else falhou "instalacao do modulo terminou com exit $RC (ver $LOG_ATUAL)"; fi
MODULO_ESTADO="$(odoo_db "select state from ir_module_module where name='$MODULO'")"
[ "$(limpar "$MODULO_ESTADO")" = "installed" ] && ok "modulo $MODULO esta installed no banco descartavel" \
    || falhou "modulo nao ficou installed (estado: ${MODULO_ESTADO:-?})"

# ---------------------------------------------------------------------------
# passo 2 — servidor Odoo (porta unica), chave de API e massa da ambiguidade
# ---------------------------------------------------------------------------
cabecalho "passo 2 — servidor da porta unica, chave de API e massa"
API_CT="$API_TMP"
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

# chave de API: gerada por `odoo shell`, gravada em arquivo 600 do diretorio descartavel.
# O diretorio tem de ser gravavel pelo uid do Odoo do container (100): sem isso o `open()`
# da chave morre em PermissionError e o aceite seguiria medindo um ambiente sem credencial.
chown 100:101 "$DESC_DIR" 2>/dev/null || info "nao consegui chown do diretorio descartavel (uid do container pode diferir)"
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

# Sonda independente do n8n: a chave do cofre e' aceita pela porta unica? (dry_run: nao escreve)
# Sem esta sonda, um 401 no ciclo nao distingue "a chave nao vale" de "o n8n nao mandou o cabecalho".
# A sonda usa a MESMA imagem do n8n (node + fetch): mesma pilha que vai entregar, sem o workflow.
cat >"$DESC_DIR/sonda.json" <<JSON
{"idempotency_key":"outbox:$E9:SONDA","correlation_id":"sonda-aceite-e02t01",
 "dry_run":true,"parametros":{"modelo":"res.partner","valores":{"name":"Sonda do aceite","tf_company_id":"$E9","tf_domain":"sonda.example"}}}
JSON
cat >"$DESC_DIR/sonda.js" <<'JS'
const fs = require('fs');
const chave = fs.readFileSync('/prep/chave.txt', 'utf8').trim();
const corpo = fs.readFileSync('/prep/sonda.json', 'utf8');
const alvo = 'http://' + process.env.API_HOST + ':8069/tf/api/v1/empresa_upsert';
fetch(alvo, {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + chave},
  body: corpo,
}).then(async (r) => {
  console.log('SONDA_HTTP', r.status, (await r.text()).slice(0, 300));
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
[ "$RC_SONDA" = "0" ] && ok "sonda da chave na porta unica: HTTP 200 em dry_run (a credencial do cofre vale)" \
    || falhou "sonda da chave nao devolveu 200 (ver $LOG_DIR/2-sonda-chave.log)"

# massa da ambiguidade: dois parceiros com o MESMO dominio, cada um com sua identidade canonica
docker run --rm -i --network "$NET_TMP" \
    -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
    -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
    -e "TRE_MASSA_DOMINIO=$DOMINIO_AMBIGUO" -e "TRE_MASSA_IDS=$PARCEIRO_AMB1,$PARCEIRO_AMB2" \
    --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http \
    <"$MASSA_AMBIGUA" >"$LOG_DIR/2-massa-ambigua.log" 2>&1
AMBIGUOS="$(limpar "$(odoo_db "select count(*) from res_partner where tf_domain='$DOMINIO_AMBIGUO'")")"
[ "$AMBIGUOS" = "2" ] && ok "massa da ambiguidade pronta (2 parceiros com $DOMINIO_AMBIGUO)" \
    || falhou "esperava 2 parceiros com $DOMINIO_AMBIGUO, medidos: ${AMBIGUOS:-0} (ver $LOG_DIR/2-massa-ambigua.log)"

# ---------------------------------------------------------------------------
# passo 3 — n8n descartavel: credenciais (cofre) e workflow importados
# ---------------------------------------------------------------------------
cabecalho "passo 3 — n8n descartavel (cofre + workflow importado)"
python3 - "$DESC_DIR/credenciais.json" "$PG_TMP" "$BANCO_SI" "$PG_USER" "$SENHA" "$DESC_DIR/chave.txt" <<'PY'
import json, sys
saida, host, banco, usuario, senha, arq_chave = sys.argv[1:7]
chave = open(arq_chave).read().strip()
credenciais = [
    {"id": "tre-dev-postgres", "name": "TRE dev — sales_intelligence", "type": "postgres",
     "data": {"host": host, "port": 5432, "database": banco, "user": usuario, "password": senha,
              "ssl": "disable", "allowUnauthorizedCerts": False}},
    {"id": "tre-dev-api-controlada", "name": "TRE dev — API controlada Odoo (Bearer)", "type": "httpHeaderAuth",
     "data": {"name": "Authorization", "value": "Bearer " + chave}},
]
open(saida, "w").write(json.dumps(credenciais, ensure_ascii=False, indent=2))
print("credenciais escritas (valores so' no diretorio 700 do descartavel)")
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
if n8n_cli import:credentials --input=/home/node/.n8n/credenciais.json >"$LOG_DIR/3-credenciais.log"; then
    ok "credenciais importadas no cofre do n8n descartavel (id/nome do contrato)"
else
    falhou "import das credenciais falhou (ver $LOG_DIR/3-credenciais.log)"
fi
if n8n_cli import:workflow --input=/home/node/.n8n/workflow.json >"$LOG_DIR/3-workflow.log"; then
    ok "workflow do consumidor importado no n8n descartavel"
else
    falhou "import do workflow falhou (ver $LOG_DIR/3-workflow.log)"
fi
# O id estavel e' o contrato com o publicador e com os recibos: confere EXPORTANDO o workflow
# pelo id (a listagem da CLI muda de formato entre versoes; o export nao).
n8n_cli export:workflow --id="$ID_WORKFLOW" --output=/home/node/.n8n/exportado.json >"$LOG_DIR/3-export.log" 2>&1
if [ -s "$N8N_HOME/exportado.json" ] && grep -q "$ID_WORKFLOW" "$N8N_HOME/exportado.json"; then
    ok "o workflow importado e' exportavel pelo id estavel $ID_WORKFLOW"
else
    falhou "workflow $ID_WORKFLOW nao esta no cofre do n8n (ver $LOG_DIR/3-export.log)"
fi
rm -f "$N8N_HOME/exportado.json"
# Os arquivos de importacao cumpriram o papel: saem do cofre (o cofre nao guarda copia em claro
# da credencial — o item de token em claro no fim do aceite mede exatamente isso).
rm -f "$N8N_HOME/credenciais.json" "$N8N_HOME/workflow.json"

executar_ciclo() { # $1=log ; define RC_CICLO
    docker run --rm --network "$NET_TMP" -v "$N8N_HOME":/home/node/.n8n \
        -e TRE_API_BASE="http://$API_CT:8069" -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false \
        -e N8N_DIAGNOSTICS_ENABLED=false -e GENERIC_TIMEZONE=UTC \
        --entrypoint n8n "$IMAGEM_N8N" execute --id="$ID_WORKFLOW" --rawOutput >"$1" 2>&1
    RC_CICLO=$?
}
semear() { # $1 = arquivo sql
    docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -v ON_ERROR_STOP=1 -q <"$1" >/dev/null 2>&1
}
estado_evento() { limpar "$(si "select status || '/' || attempts || '/' || coalesce(last_error,'-') from sales_intelligence.outbox_events where id='$1'")"; }
estado_trilha() { limpar "$(si "select count(*) || '/' || coalesce(string_agg(status, ',' order by status),'-') from sales_intelligence.sync_events where idempotency_key='$1'")"; }
parceiros_por_identidade() { limpar "$(odoo_db "select count(*) from res_partner where tf_company_id='$1'")"; }
campo_parceiro() { podar "$(odoo_db "select coalesce($2::text,'-') from res_partner where tf_company_id='$1' order by id limit 1")"; }
retrato_parceiros() {
    info "retrato dos parceiros com identidade canonica (id | name | tf_company_id | tf_domain | is_company | score):"
    odoo_db "select id || ' | ' || name || ' | ' || coalesce(tf_company_id,'-') || ' | ' || coalesce(tf_domain,'-') || ' | ' || coalesce(is_company::text,'-') || ' | ' || coalesce(tf_priority_score::text,'-') from res_partner where tf_company_id is not null order by id" 2>/dev/null | sed 's/^/      /'
}
chamadas_api() { logs_api | grep -c 'TF_API_AUDIT' || true; }
# Base da contagem de auditoria: medida ANTES do primeiro ciclo (a sonda da chave tambem audita).
BASE_AUDITORIA="$(limpar "$(chamadas_api)")"

# ---------------------------------------------------------------------------
# ciclo 1 — fila com 7 eventos (E1..E7)
# ---------------------------------------------------------------------------
cabecalho "ciclo 1 — 7 eventos (valido, atualizacao, sem versao, fora do contrato, sem name, sem identidade, ambiguo)"
cat >"$DESC_DIR/semear1.sql" <<SQL
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, attempts, created_at) VALUES
 ('$E1','organization','$ORG1','COMPANY_QUALIFIED',
  '{"event_version":"1.0","payload":{"name":"$NOME_E1","domain":"$DOMINIO_E1","cnpj":"11.222.333/0001-81","priority_score":72.5,"icp_score":91}}'::jsonb,
  'PENDING',0, now() + interval '1 second'),
 ('$E2','organization','$ORG1','COMPANY_UPDATED',
  '{"event_version":"1.0","payload":{"name":"$NOME_E1_ATUALIZADO","domain":"$DOMINIO_E1","priority_score":80}}'::jsonb,
  'PENDING',0, now() + interval '2 second'),
 ('$E3','organization','$ORG3','COMPANY_QUALIFIED',
  '{"payload":{"name":"Sem Versao Ltda","domain":"semversao.example"}}'::jsonb,
  'PENDING',0, now() + interval '3 second'),
 ('$E4','organization','$ORG4','organization.enriched',
  '{"event_version":"1.0","payload":{"name":"Fora do Contrato Ltda","domain":"foracontrato.example"}}'::jsonb,
  'PENDING',0, now() + interval '4 second'),
 ('$E5','organization','$ORG5','COMPANY_QUALIFIED',
  '{"event_version":"1.0","payload":{"domain":"semname.example"}}'::jsonb,
  'PENDING',0, now() + interval '5 second'),
 ('$E6','organization',NULL,'COMPANY_QUALIFIED',
  '{"event_version":"1.0","payload":{"name":"Sem Identidade Ltda","domain":"semidentidade.example"}}'::jsonb,
  'PENDING',0, now() + interval '6 second'),
 ('$E7','organization','$ORG7','COMPANY_QUALIFIED',
  '{"event_version":"1.0","payload":{"name":"Ambiguo Novo Ltda","domain":"$DOMINIO_AMBIGUO"}}'::jsonb,
  'PENDING',0, now() + interval '7 second')
ON CONFLICT (id) DO NOTHING;
SQL
semear "$DESC_DIR/semear1.sql" && ok "fila semeada com 7 eventos (PENDING)" || falhou "nao semeei a fila"
PENDENTES="$(limpar "$(si "select count(*) from sales_intelligence.outbox_events where status='PENDING'")")"
[ "$PENDENTES" = "7" ] && ok "7 eventos PENDING na fila antes do ciclo" || falhou "esperava 7 PENDING, medidos ${PENDENTES:-0}"

executar_ciclo "$LOG_DIR/4-ciclo1.out"
if grep -Eq '"status" *: *"success"' "$LOG_DIR/4-ciclo1.out"; then
    ok "ciclo 1 executado pelo n8n (execution status success, exit $RC_CICLO)"
else
    falhou "ciclo 1 nao terminou em success (exit $RC_CICLO; ver $LOG_DIR/4-ciclo1.out)"
fi

# ---------------------------------------------------------------------------
# medicoes do ciclo 1 (item a item)
# ---------------------------------------------------------------------------
campo_estado() { printf '%s' "$1" | cut -d'/' -f"$2"; }
e1="$(estado_evento "$E1")"; e2="$(estado_evento "$E2")"; e3="$(estado_evento "$E3")"
e4="$(estado_evento "$E4")"; e5="$(estado_evento "$E5")"; e6="$(estado_evento "$E6")"; e7="$(estado_evento "$E7")"
[ "$(campo_estado "$e1" 1)" = "PROCESSED" ] && [ "$(campo_estado "$e1" 2)" = "1" ] \
    && ok "E1 evento valido -> PROCESSED com attempts=1" \
    || falhou "E1 esperava PROCESSED/1, medido $e1"
[ "$(campo_estado "$e2" 1)" = "PROCESSED" ] \
    && ok "E2 atualizacao da mesma identidade -> PROCESSED" \
    || falhou "E2 esperava PROCESSED, medido $e2"
[ "$(campo_estado "$e3" 1)" = "DEAD_LETTER" ] && [ "$(campo_estado "$e3" 2)" = "0" ] \
    && case "$e3" in *envelope_sem_event_version*) ok "E3 sem event_version -> DEAD_LETTER sem chamada" ;; \
       *) falhou "E3 sem event_version -> DEAD_LETTER sem chamada (motivo inesperado: $e3)" ;; esac \
    || falhou "E3 sem event_version -> DEAD_LETTER sem chamada (medido $e3)"
case "$e4" in DEAD_LETTER/*event_type_fora_do_contrato*) ok "E4 event_type fora do contrato -> DEAD_LETTER nomeado" ;; \
    *) falhou "E4 esperava DEAD_LETTER por event_type_fora_do_contrato, medido $e4" ;; esac
case "$e5" in DEAD_LETTER/*campo_exigido_ausente:name*) ok "E5 payload sem campo exigido -> DEAD_LETTER nomeado" ;; \
    *) falhou "E5 esperava DEAD_LETTER por campo_exigido_ausente:name, medido $e5" ;; esac
case "$e6" in DEAD_LETTER/*identidade_ausente*) ok "E6 sem identidade -> DEAD_LETTER nomeado" ;; \
    *) falhou "E6 esperava DEAD_LETTER por identidade_ausente, medido $e6" ;; esac
case "$e7" in DEAD_LETTER/1/*valor_ambiguo*) ok "E7 identidade ambigua na API -> DEAD_LETTER com o codigo do erro" ;; \
    *) falhou "E7 esperava DEAD_LETTER/1 com valor_ambiguo, medido $e7" ;; esac

[ "$(parceiros_por_identidade "$ORG1")" = "1" ] && ok "o parceiro do CRM existe uma vez para a identidade do evento" \
    || falhou "esperava 1 parceiro para $ORG1, medidos $(parceiros_por_identidade "$ORG1")"
[ "$(campo_parceiro "$ORG1" name)" = "$NOME_E1_ATUALIZADO" ] \
    && ok "E2 ATUALIZOU o mesmo parceiro (name atualizado no CRM)" \
    || falhou "esperava name '$NOME_E1_ATUALIZADO', medido '$(campo_parceiro "$ORG1" name)'"
[ "$(campo_parceiro "$ORG1" tf_domain)" = "$DOMINIO_E1" ] \
    && ok "E1 o parceiro do CRM tem o tf_domain mapeado do evento" \
    || falhou "E1 o parceiro do CRM tem o tf_domain mapeado do evento (esperava '$DOMINIO_E1', medido '$(campo_parceiro "$ORG1" tf_domain)')"
[ "$(campo_parceiro "$ORG1" is_company)" = "true" ] && ok "o parceiro criado e' EMPRESA (valor fixo da politica da API)" \
    || falhou "esperava is_company=true, medido '$(campo_parceiro "$ORG1" is_company)'"
[ "$(campo_parceiro "$ORG1" tf_priority_score)" = "80" ] && ok "E2 atualizou o score do parceiro (80)" \
    || falhou "esperava score 80, medido '$(campo_parceiro "$ORG1" tf_priority_score)'"
retrato_parceiros

SOMA_RECUSADOS=0
for ident in "$ORG3" "$ORG4" "$ORG5"; do SOMA_RECUSADOS=$((SOMA_RECUSADOS + $(parceiros_por_identidade "$ident"))); done
[ "$SOMA_RECUSADOS" = "0" ] && ok "evento recusado NAO escreve no CRM (E3/E4/E5 sem parceiro)" \
    || falhou "recusa escreveu no CRM: $SOMA_RECUSADOS parceiro(s)"

CHAMADAS1="$(limpar "$(( $(chamadas_api) - BASE_AUDITORIA ))")"
[ "$CHAMADAS1" = "3" ] && ok "a API foi chamada exatamente pelas 3 entregas do ciclo 1 (E1, E2, E7)" \
    || falhou "esperava 3 linhas TF_API_AUDIT no ciclo 1, medidas ${CHAMADAS1:-0} (base $BASE_AUDITORIA)"

# Serializacao MEDIDA (nao presumida): o intervalo entre as duas entregas do mesmo lote sai da
# auditoria do servidor. Dois pedidos da mesma identidade em paralelo duplicam o parceiro.
cat >"$DESC_DIR/separacao.py" <<'PY'
import datetime as dt
import re
import sys

marcas = {}
for linha in sys.stdin:
    if 'TF_API_AUDIT' not in linha:
        continue
    achado = re.match(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d+)', linha)
    if not achado:
        continue
    if 'outbox:a1111111' in linha:
        marcas['e1'] = (achado.group(1), int(achado.group(2)))
    if 'outbox:a2222222' in linha:
        marcas['e2'] = (achado.group(1), int(achado.group(2)))
if len(marcas) < 2:
    print('SEM_MARCAS')
    raise SystemExit(0)


def em_ms(marca):
    base = dt.datetime.strptime(marca[0], '%Y-%m-%d %H:%M:%S')
    return int(base.timestamp()) * 1000 + marca[1]


print(em_ms(marcas['e2']) - em_ms(marcas['e1']))
PY
SEPARACAO_MS="$(logs_api | python3 "$DESC_DIR/separacao.py")"
case "$SEPARACAO_MS" in
    ''|*[!0-9-]*) falhou "nao consegui medir o intervalo entre as entregas (${SEPARACAO_MS:-vazio})" ;;
    *) if [ "$SEPARACAO_MS" -ge 50 ]; then
           # Piso de comparacao MEDIDO neste mesmo harness antes da entrega serializada: duas
           # entregas do mesmo lote sairam com 2ms de intervalo (paralelo). Serie fica duas ordens
           # de grandeza acima disso — e o item de identidade (1 parceiro, atualizado) e' a prova
           # de negocio de que a segunda entrega VIU o registro da primeira.
           ok "as entregas do lote saem em SERIE (E1 -> E2 com ${SEPARACAO_MS}ms; piso paralelo medido: 2ms)"
       else
           falhou "as entregas sairam em paralelo (${SEPARACAO_MS}ms entre E1 e E2; o piso medido em paralelo e' ~2ms)"
       fi ;;
esac

TRILHAS1="$(limpar "$(si "select count(*) from sales_intelligence.sync_events")")"
[ "$TRILHAS1" = "7" ] && ok "a trilha tem uma linha por evento tratado (7)" \
    || falhou "esperava 7 linhas em sync_events, medidas ${TRILHAS1:-0}"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where idempotency_key='outbox:$E1:COMPANY_QUALIFIED'")")" = "1" ] \
    && ok "a trilha usa a chave derivada do evento (outbox:<id>:<event_type>)" \
    || falhou "a chave derivada do evento nao esta na trilha"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='postgres' and target_system='odoo' and operation='UPSERT'")")" = "7" ] \
    && ok "a trilha registra source/target/operation do contrato" \
    || falhou "source/target/operation da trilha divergem do contrato"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where idempotency_key='outbox:$E1:COMPANY_QUALIFIED' and status='COMPLETED' and request_payload is not null and response_payload->'dados'->>'acao_efetiva'='criar'")")" = "1" ] \
    && ok "a trilha do evento entregue guarda pedido e resposta da API (acao_efetiva=criar)" \
    || falhou "a trilha do evento entregue nao guarda pedido/resposta"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where idempotency_key='outbox:$E7:COMPANY_QUALIFIED' and status='REFUSED' and error_message like '%valor_ambiguo%'")")" = "1" ] \
    && ok "a recusa definitiva da API fica na trilha como REFUSED com o motivo" \
    || falhou "a recusa da API nao ficou na trilha como REFUSED"

# ---------------------------------------------------------------------------
# ciclo 2 — Odoo PARADO: falha de transporte (retry limitado e visivel)
# ---------------------------------------------------------------------------
cabecalho "ciclo 2 — Odoo parado: falha transitória"
docker stop "$API_CT" >/dev/null 2>&1 \
    && ok "servidor Odoo parado de proposito (falha de transporte)" || falhou "nao consegui parar o servidor Odoo"
cat >"$DESC_DIR/semear2.sql" <<SQL
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, attempts, created_at) VALUES
 ('$E8','organization','$ORG8','COMPANY_QUALIFIED',
  '{"event_version":"1.0","payload":{"name":"Echo Ltda","domain":"echo.example","priority_score":55}}'::jsonb,
  'PENDING',0, now() + interval '8 second')
ON CONFLICT (id) DO NOTHING;
SQL
semear "$DESC_DIR/semear2.sql" && ok "evento E8 na fila para o ciclo com o Odoo fora do ar" || falhou "nao semeei E8"
executar_ciclo "$LOG_DIR/5-ciclo2.out"
e8="$(estado_evento "$E8")"
case "$e8" in RETRY/1/*falha_de_transporte*) ok "E8 falha de transporte -> RETRY e attempts=1" ;; \
    *) falhou "E8 falha de transporte -> RETRY e attempts=1 (medido $e8)" ;; esac
[ "$(estado_trilha "outbox:$E8:COMPANY_QUALIFIED")" = "1/FAILED" ] && ok "a falha transitoria fica na trilha como FAILED" \
    || falhou "trilha do E8 esperava 1/FAILED, medida $(estado_trilha "outbox:$E8:COMPANY_QUALIFIED")"
[ "$(parceiros_por_identidade "$ORG8")" = "0" ] && ok "evento que falhou nao escreveu no CRM" \
    || falhou "o evento que falhou escreveu no CRM"

# ---------------------------------------------------------------------------
# ciclo 3 — Odoo de volta: o retry entrega e nao duplica
# ---------------------------------------------------------------------------
cabecalho "ciclo 3 — Odoo de volta: retry entrega o mesmo evento"
docker start "$API_CT" >/dev/null 2>&1 && ok "servidor Odoo religado" || falhou "nao consegui religar o servidor Odoo"
API_PRONTA=0
for _ in $(seq 1 40); do
    if docker exec "$API_CT" python3 -c "import socket;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',8069))" 2>/dev/null; then API_PRONTA=1; break; fi
    sleep 3
done
[ "$API_PRONTA" = "1" ] && ok "porta unica de volta no ar" || falhou "servidor Odoo nao voltou"
executar_ciclo "$LOG_DIR/6-ciclo3.out"
e8="$(estado_evento "$E8")"
case "$e8" in PROCESSED/2/*) ok "E8 retry entregou -> PROCESSED com attempts=2" ;; \
    *) falhou "E8 esperava PROCESSED/2 depois do retry, medido $e8" ;; esac
[ "$(estado_trilha "outbox:$E8:COMPANY_QUALIFIED")" = "1/COMPLETED" ] \
    && ok "retry NAO duplica trilha (1 linha por chave, agora COMPLETED)" \
    || falhou "trilha do E8 esperava 1/COMPLETED, medida $(estado_trilha "outbox:$E8:COMPANY_QUALIFIED")"
[ "$(parceiros_por_identidade "$ORG8")" = "1" ] && ok "retry NAO duplica o registro no CRM (1 por identidade)" \
    || falhou "esperava 1 parceiro para $ORG8, medidos $(parceiros_por_identidade "$ORG8")"

# ---------------------------------------------------------------------------
# ciclo 4 — teto de tentativas: a fila desiste sem nova chamada
# ---------------------------------------------------------------------------
cabecalho "ciclo 4 — evento no teto de tentativas"
CHAMADAS_ANTES="$(limpar "$(chamadas_api)")"
cat >"$DESC_DIR/semear3.sql" <<SQL
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, attempts, created_at) VALUES
 ('$E9','organization','$ORG9','COMPANY_QUALIFIED',
  '{"event_version":"1.0","payload":{"name":"Foxtrot Ltda","domain":"foxtrot.example"}}'::jsonb,
  'RETRY',$TETO, now() + interval '9 second')
ON CONFLICT (id) DO NOTHING;
SQL
semear "$DESC_DIR/semear3.sql" && ok "evento E9 semeado no teto ($TETO tentativas)" || falhou "nao semeei E9"
executar_ciclo "$LOG_DIR/7-ciclo4.out"
e9="$(estado_evento "$E9")"
case "$e9" in DEAD_LETTER/3/*teto_de_tentativas_atingido*) ok "E9 teto de tentativas -> DEAD_LETTER sem chamada" ;; \
    *) falhou "E9 teto de tentativas -> DEAD_LETTER sem chamada (medido $e9)" ;; esac
[ "$(parceiros_por_identidade "$ORG9")" = "0" ] && ok "evento esgotado NAO escreve no CRM" \
    || falhou "evento esgotado escreveu no CRM"

# ---------------------------------------------------------------------------
# fecho — chamadas, segredo, ambiente e limpeza
# ---------------------------------------------------------------------------
cabecalho "fecho — chamadas, segredo e ambiente"
CHAMADAS_FINAIS="$(limpar "$(( $(chamadas_api) - BASE_AUDITORIA ))")"
[ "$CHAMADAS_FINAIS" = "4" ] && ok "total de chamadas autenticadas == entregas tentadas (E1, E2, E7, E8 retry)" \
    || falhou "esperava 4 linhas TF_API_AUDIT (delta da base $BASE_AUDITORIA), medidas ${CHAMADAS_FINAIS:-0}"
if logs_api | grep -q "$(cat "$DESC_DIR/chave.txt")"; then
    falhou "a chave da API aparece no log do servidor"
else
    ok "a chave da API nao aparece no log do servidor"
fi
if docker run --rm -v "$N8N_HOME":/home/node/.n8n --entrypoint sh "$IMAGEM_N8N" -c \
        'grep -ril "Bearer [A-Za-z0-9]\{20,\}" /home/node/.n8n | grep -v ".sqlite" | head -3' | grep -q .; then
    falhou "ha token em claro em arquivo do cofre do n8n fora do banco"
else
    ok "nenhum token em claro em arquivo do cofre do n8n (o valor vive no banco do cofre)"
fi
if [ -x "$RAIZ_REPO/scripts/secret_scan.sh" ] && (cd "$RAIZ_REPO" && bash scripts/secret_scan.sh) >"$LOG_DIR/8-secret-scan.log" 2>&1; then
    ok "secret scan do repositorio passou (nenhum segredo no versionado)"
else
    falhou "secret scan reprovou ou nao rodou (ver $LOG_DIR/8-secret-scan.log)"
fi
DEV_PG_DEPOIS="nao_medido"
if [ "$(docker inspect -f '{{.State.Running}}' "$DEV_PG_CT" 2>/dev/null)" = "true" ]; then
    DEV_PG_DEPOIS="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)"
fi
[ "$DEV_PG_ANTES" = "$DEV_PG_DEPOIS" ] && ok "a instancia do dev tem os MESMOS bancos antes e depois" \
    || falhou "o dev mudou: antes [$DEV_PG_ANTES] depois [$DEV_PG_DEPOIS]"
HOMOLOG_PROD_DEPOIS="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
[ "$HOMOLOG_PROD_ANTES" = "$HOMOLOG_PROD_DEPOIS" ] && ok "homologacao/producao com o mesmo numero de arquivos (nada nasceu la')" \
    || falhou "homolog/prod mudaram: antes $HOMOLOG_PROD_ANTES depois $HOMOLOG_PROD_DEPOIS"
NAO_TOCADOS=0
for banco in $DEV_PG_DEPOIS; do
    case "$banco" in *"$BANCO_SI"*) NAO_TOCADOS=1 ;; esac
done
[ "$NAO_TOCADOS" = "0" ] && ok "o banco do contrato nao existe no dev (o schema so' vive no descartavel)" \
    || falhou "o banco $BANCO_SI apareceu na instancia do dev"
SHA_DEPOIS="$LOG_DIR/sha256-depois.txt"
sha256_dos_artefatos "$SHA_DEPOIS"
if [ "$(veredito_sha256 "$LOG_DIR/sha256-antes.txt" "$SHA_DEPOIS")" = "IDENTICO" ]; then
    ok "sha256 dos 5 artefatos sob teste reconferido no fecho: identico ao fixado nas guardas"
else
    falhou "sha256 dos 5 artefatos MUDOU durante a medicao: $(diff "$LOG_DIR/sha256-antes.txt" "$SHA_DEPOIS" 2>&1 | head -4 | tr '\n' ' ')"
fi

echo "FASE_CONSUMO_OK"
resumo
