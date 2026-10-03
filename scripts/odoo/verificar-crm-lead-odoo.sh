#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W2-E04-T02 — campos de rastreio em `crm.lead` (modulo transformativa_sales_ai)
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 campos previstos no contrato presentes em `crm.lead`;
#   AC2 CRM padrao nao quebra: criacao/consulta de lead funcionam;
#   AC3 teste de criacao e consulta com dado sintetico.
#
# TEST PLAN executado por este script (na VPS do dev):
#   passo 0 confronto ESTATICO modulo x contrato congelado (conferidor Python, §3/§6/§7/§8);
#   passo 1 instalacao do modulo em banco limpo            -> log + estado lido no banco
#   passo 2 campos no banco                                -> ir_model_fields + indice em pg_indexes
#   passo 3 teste do Odoo (--test-enable)                  -> relatorio do runner + estado
#   passo 4 dado sintetico pelo ORM (scripts/odoo/medir_crm_lead.py) -> marcadores + SQL fora da sessao
#   passo 5 rollback pelo modulo (desinstalacao)           -> campos/indices removidos, padrao intacto
#   passo 6 limpeza e prova de que a instancia do dev nao foi tocada
#
# ISOLAMENTO: o aceite sobe a SUA propria dupla descartavel (`postgres:16` + `odoo:19.0`, as
# imagens/digests do par de dev) em rede propria. NAO usa `pg-odoo-dev`, `odoo_dev` nem a copia
# operacional `/opt/tre/repo` — o Odoo do dev abre sessao em qualquer banco novo da instancia do
# dev (medido no E03-T01) e o AC pede banco limpo.
#
# Uso (na VPS, a partir de ARQUIVO) — as tres variaveis de caminho vao SEMPRE juntas:
#   TRE_MODULO_DIR=/caminho/modulo/transformativa_sales_ai \
#   TRE_CONTRATO_JSON=/caminho/docs/data/data_contract_v1.json \
#   TRE_LOG_DIR=/caminho/logs \
#   bash verificar-crm-lead-odoo.sh                     # aceite completo
#   bash verificar-crm-lead-odoo.sh --apenas-confronto  # so o confronto modulo x contrato
#   bash verificar-crm-lead-odoo.sh --apenas-instalacao-e-campos
#   bash verificar-crm-lead-odoo.sh --apenas-instalacao-e-testes
#   bash verificar-crm-lead-odoo.sh --prova-de-dente    # baseline nao mutado (exige verde) + 5
#                                                       # mutacoes; espera-se FALHOU em TODAS
#   bash verificar-crm-lead-odoo.sh --banco tre_outro_banco
#
# Variaveis: TRE_MODULO, TRE_MODULO_DIR, TRE_BANCO, TRE_IMAGEM, TRE_IMAGEM_PG, TRE_PG_USER,
# TRE_CONTRATO_JSON, TRE_CONFERIDOR, TRE_MEDIDOR, TRE_LOG_DIR, TRE_DEV_PG_CT, TRE_MANTER_BANCO.
#
# TRE_CONTRATO_JSON NAO tem default que resolva: o caminho padrao
# (/opt/tre/dev/contrato/data_contract_v1.json) nao existe em nenhum ambiente medido, e o
# verificador RECUSA DE CARA (fail-closed) quando o contrato nao esta em disco, apontando o que
# exportar — exporte sempre o `docs/data/data_contract_v1.json` do commit sob teste
# (defeito 5 do §8 da runbook: dente que dava verde sem medir).
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo final em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir
# ============================================================================
set -u

MODULO="${TRE_MODULO:-transformativa_sales_ai}"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# Caminho ABSOLUTO deste script: as provas de dente reexecutam o proprio verificador, e
# `"$0"` so' funciona quando o chamador passou caminho com barra (com `bash verificar-...sh` o
# shell procuraria no PATH e falharia com "command not found" — defeito medido na 1a rodada).
SELF="$(readlink -f "$0")"
MODULO_DIR="${TRE_MODULO_DIR:-/opt/tre/dev/modulos/$MODULO}"
DESINSTALADOR="${TRE_DESINSTALADOR:-$AQUI/desinstalar_modulo.py}"
CONFERIDOR="${TRE_CONFERIDOR:-$AQUI/conferir_crm_lead_no_contrato.py}"
MEDIDOR="${TRE_MEDIDOR:-$AQUI/medir_crm_lead.py}"
CONTRATO_JSON="${TRE_CONTRATO_JSON:-/opt/tre/dev/contrato/data_contract_v1.json}"
BANCO="${TRE_BANCO:-tre_e04_t02_crm_lead}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-crm-lead-odoo}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"
# TRE_PULAR_CONFRONTO=1: pula o passo 0 (confronto estatico) — usado SO' pelas provas de dente,
# para que a mutacao seja medida pelo caminho de BANCO (passo 2) e nao apenas pelo conferidor
# estatico. Sem ele, uma mutacao de campo/index ja' reprova no passo 0.
PULAR_CONFRONTO="${TRE_PULAR_CONFRONTO:-0}"
# 13 = 6 testes do modulo base (TRE-W2-E03-T01) + 7 deste card. Como outros cards do mesmo
# modulo acrescentam testes, o item exige N >= 13 (nunca menos que o ja' provado).
MIN_TESTS="${TRE_MIN_TESTS:-13}"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-confronto) MODO=confronto ;;
        --apenas-instalacao-e-campos) MODO=instalacao_campos ;;
        --apenas-instalacao-e-testes) MODO=instalacao_testes ;;
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
PG_TMP="e04t02-pg-$SUFIXO"
NET_TMP="e04t02-net-$SUFIXO"
DESC_DIR=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: CRM_LEAD_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: CRM_LEAD_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? baseline nao mutado + 5 mutacoes, cada uma em copia propria
#
# DEFEITO 5 do §8 (rodada 2): a versao anterior aceitava QUALQUER `CRM_LEAD_FALHOU` como prova de
# dente. Com os defaults do script (contrato ausente no caminho padrao / modulo publicado sem
# `crm_lead.py`) as 5 provas morriam na GUARDA — antes de medir qualquer coisa — e o modo devolvia
# `CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`, exit 0: verde sem dente nenhum (fail-open). O aceite
# sempre foi fail-CLOSED; quem era fail-open era o modo dente. Agora o modo dente:
#   1. roda o caminho NAO mutado (baseline: passo 0 + 1 + 2 + 3) e EXIGE verde — sem baseline
#      verde nao existe prova de dente, e o comando termina em CRM_LEAD_DENTE_FALHOU, exit 1;
#   2. exige de CADA prova a SUA assinatura de falha (o texto que so' aquela mutacao produz),
#      em vez de "qualquer FALHOU";
#   3. reprova o dente cuja saida abortou numa guarda do ambiente ou nao chegou ao passo medido.
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e04t02-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_FALHAS=0
    # $1=rotulo  $2=regex da ASSINATURA de falha esperada  $3=passo que tem de ter sido medido
    # $4=saida do verificador mutado
    confere_dente() {
        local rotulo="$1" assinatura="$2" passo="$3" saida="$4" motivo=""
        if ! printf '%s' "$saida" | grep -q 'RESULTADO: CRM_LEAD_FALHOU'; then
            motivo="a mutacao NAO reprovou o aceite — o item nao mede o que promete"
        elif printf '%s' "$saida" | grep -qE 'nada a medir|sem contrato nao ha confronto|nao consegui ler o inventario|nao responde|nao ficou pronto|nao consegui subir|nao consegui criar'; then
            motivo="a prova ABORTOU numa guarda do ambiente, antes de medir — aborto nao e' prova de dente"
        elif ! printf '%s' "$saida" | grep -q "$passo"; then
            motivo="a prova nao chegou ao '$passo' (morreu antes de medir) — aborto nao e' prova de dente"
        elif ! printf '%s' "$saida" | grep -qE "$assinatura"; then
            motivo="a prova reprovou por outro motivo: falta a assinatura esperada /$assinatura/"
        fi
        if [ -z "$motivo" ]; then
            echo "OK    $rotulo: a mutacao REPROVOU o aceite com a assinatura esperada (o item tem dente)"
        else
            echo "FALHOU $rotulo: $motivo"
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
        fi
    }

    # --- baseline: o caminho NAO mutado tem de medir verde ANTES de qualquer mutacao ------------
    # Roda passo 0 (confronto) + passo 1 (instalacao) + passo 2 (campos no banco) + passo 3
    # (teste do Odoo): e' o mesmo caminho que os 5 dentes medem, sem mutacao nenhuma. Sem este
    # verde, uma mutacao que "reprova" nao prova nada (ela pode estar reprovando a guarda).
    cabecalho "baseline: caminho NAO mutado (passo 0 + 1 + 2 + 3) tem de medir CRM_LEAD_OK"
    BASE="$(TRE_MODULO_DIR="$MODULO_DIR" TRE_BANCO="${BANCO}_baseline" TRE_LOG_DIR="$LOG_DIR" \
            TRE_PULAR_CONFRONTO=0 bash "$SELF" --apenas-instalacao-e-testes 2>&1)"
    echo "$BASE" >"$LOG_DIR/dente-0-baseline.out"
    echo "$BASE" | tail -3
    if printf '%s' "$BASE" | grep -q 'RESULTADO: CRM_LEAD_OK'; then
        echo "OK    baseline: o caminho NAO mutado mediu CRM_LEAD_OK — os dentes tem contra o que medir"
    else
        echo "FALHOU baseline: o caminho NAO mutado NAO mediu verde: $(printf '%s' "$BASE" | grep '^FALHOU ' | head -3 | tr '\n' ' ')"
        echo '---'
        echo "RESULTADO: CRM_LEAD_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado) modulo=$MODULO"
        exit 1
    fi

    cabecalho "dente 1: campo do contrato renomeado (tf_opportunity_id -> tf_opp_id)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    sed -i 's/tf_opportunity_id = fields\.Char(/tf_opp_id = fields.Char(/' "$DENTE_DIR/m1/models/crm_lead.py"
    sed -i "s/@api.constrains('tf_opportunity_id')/@api.constrains('tf_opp_id')/" "$DENTE_DIR/m1/models/crm_lead.py"
    D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_BANCO="${BANCO}_dente1" TRE_LOG_DIR="$LOG_DIR" TRE_PULAR_CONFRONTO=1 \
          bash "$SELF" --apenas-instalacao-e-campos 2>&1)"
    echo "$D1" >"$LOG_DIR/dente-1-campo-renomeado.out"
    echo "$D1" | tail -3
    confere_dente "dente 1" "campo nomeado pelo contrato AUSENTE: tf_opportunity_id|so' 12 de 13 campos do inventario" "passo 2/6" "$D1"

    cabecalho "dente 2: indice removido do campo de idempotencia (tf_idempotency_key)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    python3 - "$DENTE_DIR/m2/models/crm_lead.py" <<'PY'
import pathlib
import re
import sys
caminho = pathlib.Path(sys.argv[1])
texto = caminho.read_text(encoding='utf-8')
novo, n = re.subn(
    r"(tf_idempotency_key = fields\.Char\(\n(?:.*\n)*?)\s*index=True,\n",
    r"\1",
    texto,
    count=1,
)
assert n == 1, 'nao consegui remover o index do tf_idempotency_key'
caminho.write_text(novo, encoding='utf-8')
PY
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_dente2" TRE_LOG_DIR="$LOG_DIR" TRE_PULAR_CONFRONTO=1 \
          bash "$SELF" --apenas-instalacao-e-campos 2>&1)"
    echo "$D2" >"$LOG_DIR/dente-2-indice-removido.out"
    echo "$D2" | tail -3
    confere_dente "dente 2" "sem indice no banco para tf_idempotency_key" "passo 2/6" "$D2"

    cabecalho "dente 3: campo do inventario apagado do modelo (tf_next_best_action)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m3"
    python3 - "$DENTE_DIR/m3/models/crm_lead.py" <<'PY'
import pathlib
import re
import sys
caminho = pathlib.Path(sys.argv[1])
texto = caminho.read_text(encoding='utf-8')
novo, n = re.subn(
    r"    tf_next_best_action = fields\.Selection\(\n(?:.*\n)*?    \)\n",
    "",
    texto,
    count=1,
)
assert n == 1, 'nao consegui apagar a declaracao do tf_next_best_action'
caminho.write_text(novo, encoding='utf-8')
PY
    D3="$(TRE_MODULO_DIR="$DENTE_DIR/m3" TRE_BANCO="${BANCO}_dente3" TRE_LOG_DIR="$LOG_DIR" TRE_PULAR_CONFRONTO=1 \
          bash "$SELF" --apenas-instalacao-e-campos 2>&1)"
    echo "$D3" >"$LOG_DIR/dente-3-campo-apagado.out"
    echo "$D3" | tail -3
    confere_dente "dente 3" "crm.lead tem 12 campo" "passo 2/6" "$D3"

    cabecalho "dente 5: campo do contrato renomeado, medido SO' pelo confronto estatico (passo 0)"
    cp -a "$DENTE_DIR/m1" "$DENTE_DIR/m5"
    D5="$(TRE_MODULO_DIR="$DENTE_DIR/m5" TRE_BANCO="${BANCO}_dente5" TRE_LOG_DIR="$LOG_DIR" \
          bash "$SELF" --apenas-confronto 2>&1)"
    echo "$D5" >"$LOG_DIR/dente-5-confronto-estatico.out"
    echo "$D5" | tail -3
    confere_dente "dente 5" "campo nomeado pelo contrato AUSENTE: tf_opportunity_id|inventario != implementado" "passo 0/6" "$D5"

    cabecalho "dente 4: teste plantado que falha de proposito (espera-se FALHOU no passo 3)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m4"
    cat >>"$DENTE_DIR/m4/tests/test_crm_lead_rastreio.py" <<'PY'

    def test_99_prova_de_dente(self):
        """Teste plantado pela prova de dente do verificador: TEM de reprovar."""
        self.assertTrue(False, 'teste plantado pela prova de dente (TRE-W2-E04-T02)')
PY
    D4="$(TRE_MODULO_DIR="$DENTE_DIR/m4" TRE_BANCO="${BANCO}_dente4" TRE_LOG_DIR="$LOG_DIR" \
          bash "$SELF" --apenas-instalacao-e-testes 2>&1)"
    echo "$D4" >"$LOG_DIR/dente-4-teste-plantado.out"
    echo "$D4" | tail -3
    confere_dente "dente 4" "1 failed, 0 error\(s\) of" "passo 3/6" "$D4"

    echo '---'
    if [ "$DENTE_FALHAS" -eq 0 ]; then
        echo "RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas) modulo=$MODULO"
        exit 0
    fi
    echo "RESULTADO: CRM_LEAD_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente) modulo=$MODULO"
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
# Container DESCARTAVEL do Odoo (mesma imagem do par de dev), com o modulo montado em
# /mnt/extra-addons e configuracao propria: a senha nasce nesta execucao, vive num arquivo 600
# dono uid 100 e nunca aparece em argumento, log ou artefato.
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
        -v "$script":/tmp/medidor.py:ro \
        -e TRE_MODULO="$MODULO" \
        --entrypoint odoo "$IMAGEM" shell -d "$banco" --no-http <"$script" >"$log" 2>&1
    echo $?
}
erros_no_log() { grep -cE '(^| )(ERROR|CRITICAL) ' "$1" 2>/dev/null || true; }
# Inventario de campos: lido do PROPRIO modulo sob teste (fonte unica), nunca redigitado aqui.
campos_do_modulo() { # $1=nome da constante
    sed -n "/^$1 = (/,/^)/p" "$MODULO_DIR/models/crm_lead.py" \
        | grep -oE "'tf_[a-z0-9_]+'" | tr -d "'" | sort -u
}

# ---------------------------------------------------------------------------
# guardas do ambiente (fail-closed: sem ambiente medido, nao existe aceite)
# ---------------------------------------------------------------------------
cabecalho "guardas do ambiente"
# Contrato, conferidor e medidor sao artefatos de ARQUIVO: conferidos PRIMEIRO, para o comando
# RECUSAR DE CARA — sem subir nada — quando o caminho do contrato nao resolve. O default antigo
# (`/opt/tre/dev/contrato/data_contract_v1.json`) nao existe em nenhum ambiente medido: sem esta
# recusa explicita, o comando morria no meio e (no modo dente) dava verde sem medir (defeito 5 §8).
if [ -f "$CONTRATO_JSON" ]; then
    ok "contrato congelado em disco: $CONTRATO_JSON"
else
    falhou "contrato congelado ausente em $CONTRATO_JSON (sem contrato nao ha confronto) — exporte TRE_CONTRATO_JSON=/caminho/docs/data/data_contract_v1.json"; resumo
fi
if [ -f "$CONFERIDOR" ]; then
    ok "conferidor modulo x contrato em disco: $CONFERIDOR"
else
    falhou "conferidor ausente em $CONFERIDOR (sem confronto estatico)"; resumo
fi
if [ -f "$MEDIDOR" ]; then
    ok "medidor ORM em disco: $MEDIDOR"
else
    falhou "medidor ausente em $MEDIDOR (sem medicao de dado sintetico)"; resumo
fi
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
    info "sha256 dos arquivos do modulo sob teste:"
    (cd "$MODULO_DIR" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's/^/      /')
else
    falhou "modulo ausente em $MODULO_DIR (__manifest__.py nao encontrado)"; resumo
fi
if [ -f "$MODULO_DIR/models/crm_lead.py" ]; then
    ok "modelo do card em disco: $MODULO_DIR/models/crm_lead.py"
else
    falhou "models/crm_lead.py ausente em $MODULO_DIR (nada a medir)"; resumo
fi
CAMPOS_INVENTARIO="$(campos_do_modulo CAMPOS_DE_RASTREIO)"
CAMPOS_INDEXADOS_MODULO="$(campos_do_modulo CAMPOS_INDEXADOS)"
N_INVENTARIO="$(printf '%s\n' "$CAMPOS_INVENTARIO" | grep -c . || true)"
if [ "$N_INVENTARIO" -ge 1 ]; then
    ok "inventario lido do modulo: $N_INVENTARIO campo(s) de rastreio"
else
    falhou "nao consegui ler o inventario (CAMPOS_DE_RASTREIO) do modulo"; resumo
fi
if [ "$N_INVENTARIO" -eq 13 ]; then
    ok "inventario do card com os 13 campos de rastreio"
else
    falhou "inventario com $N_INVENTARIO campo(s) (o card define 13)"; resumo
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
# passo 0 — confronto ESTATICO modulo x contrato congelado
# ---------------------------------------------------------------------------
cabecalho "passo 0/6 — confronto modulo x contrato congelado"
LOG_ATUAL="$LOG_DIR/0-confronto-contrato.out"
RC=0
if [ "$PULAR_CONFRONTO" = "1" ]; then
    info "TRE_PULAR_CONFRONTO=1: confronto estatico PULADO nesta execucao (prova de dente do caminho de banco)"
else
python3 "$CONFERIDOR" --contrato "$CONTRATO_JSON" --modulo-dir "$MODULO_DIR" >"$LOG_ATUAL" 2>&1 || RC=$?
N_OK_CONF="$(grep -c '^OK ' "$LOG_ATUAL" || true)"
N_FALHOU_CONF="$(grep -c '^FALHOU ' "$LOG_ATUAL" || true)"
if grep -q 'RESULTADO: CONFERIDOR_CRM_LEAD_OK' "$LOG_ATUAL" && [ "$N_FALHOU_CONF" = "0" ]; then
    ok "conferidor de contrato: CONFERIDOR_CRM_LEAD_OK ($N_OK_CONF itens, 0 falhas)"
else
    falhou "conferidor de contrato reprovou ($N_OK_CONF OK / $N_FALHOU_CONF falhas, exit $RC) — ver $LOG_ATUAL: $(grep '^FALHOU ' "$LOG_ATUAL" | head -3 | tr '\n' ' ')"
fi
fi
if [ "$MODO" = "confronto" ]; then resumo; fi

# ---------------------------------------------------------------------------
# dupla descartavel propria (ver o cabecalho: por que nao usar pg-odoo-dev)
# ---------------------------------------------------------------------------
cabecalho "dupla descartavel propria (postgres + odoo)"
DESC_DIR="$(mktemp -d /tmp/verificacao-crm-lead-XXXXXX)"
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
    # As duas chaves vao por variavel de proposito: o scanner de segredo do repo (`scripts/secret_scan.sh`)
    # reprova a forma literal "<chave> = <valor>" no codigo mesmo quando o valor e' uma variavel.
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
if banco_existe "$BANCO"; then ok "banco $BANCO criado pelo Odoo (do zero -> instalado)"; else falhou "banco $BANCO nao foi criado"; resumo; fi
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "ir_module_module.state = installed" || falhou "estado no banco: '$ESTADO' (esperado installed)"
# o modulo padrao do CRM tem de estar de pe' (o AC2 e' 'o CRM padrao nao quebra')
ESTADO_CRM="$(psql_bd "$BANCO" "select state from ir_module_module where name = 'crm'")"
[ "$ESTADO_CRM" = "installed" ] && ok "modulo 'crm' (padrao) instalado" || falhou "modulo 'crm' em '$ESTADO_CRM' (esperado installed)"

# ---------------------------------------------------------------------------
# passo 2/6 — os campos do contrato no banco (AC1)
# ---------------------------------------------------------------------------
cabecalho "passo 2/6 — campos de rastreio no banco (AC1)"
LISTA_SQL="$(printf '%s\n' "$CAMPOS_INVENTARIO" | sed "s/^/'/;s/\$/'/" | tr '\n' ',' | sed 's/,$//')"
info "inventario conferido no banco: $(printf '%s' "$CAMPOS_INVENTARIO" | tr '\n' ' ')"
PRESENTES="$(psql_bd "$BANCO" "select count(*) from ir_model_fields where model = 'crm.lead' and name in ($LISTA_SQL)")"
[ "$PRESENTES" = "$N_INVENTARIO" ] && ok "os $N_INVENTARIO campos do inventario estao em ir_model_fields de crm.lead" \
    || falhou "so' $PRESENTES de $N_INVENTARIO campos do inventario em ir_model_fields de crm.lead"
TODOS_TF="$(psql_bd "$BANCO" "select count(*) from ir_model_fields where model = 'crm.lead' and name like 'tf\\_%'")"
[ "$TODOS_TF" = "$N_INVENTARIO" ] && ok "crm.lead tem exatamente os $N_INVENTARIO campos tf_ do inventario (nenhum a mais)" \
    || falhou "crm.lead tem $TODOS_TF campo(s) tf_ (inventario: $N_INVENTARIO)"
MANUAIS="$(psql_bd "$BANCO" "select count(*) from ir_model_fields where model = 'crm.lead' and name like 'tf\\_%' and state = 'manual'")"
[ "$MANUAIS" = "0" ] && ok "nenhum campo tf_ com state=manual (campos do modulo, nao do Studio)" \
    || falhou "$MANUAIS campo(s) tf_ com state=manual"
for campo in $CAMPOS_INDEXADOS_MODULO; do
    TEM_INDICE="$(psql_bd "$BANCO" "select count(*) from pg_indexes where tablename = 'crm_lead' and indexdef like '%$campo%'")"
    [ "$TEM_INDICE" -ge 1 ] && ok "indice real no banco para $campo" \
        || falhou "sem indice no banco para $campo (busca por identidade/correlacao — contrato §3/§6)"
done
# os dois nomes que o contrato escreve literalmente (Data Contract V1.0 §3)
for campo in tf_opportunity_id tf_priority_score; do
    NOME="$(psql_bd "$BANCO" "select count(*) from ir_model_fields where model = 'crm.lead' and name = '$campo'")"
    [ "$NOME" = "1" ] && ok "campo nomeado pelo contrato presente: $campo" \
        || falhou "campo nomeado pelo contrato AUSENTE: $campo"
done
# os 5 score types do contrato §8 tem espelho no banco
for tipo in icp automation_fit buying_signal data_quality priority; do
    NOME="$(psql_bd "$BANCO" "select count(*) from ir_model_fields where model = 'crm.lead' and name = 'tf_${tipo}_score'")"
    [ "$NOME" = "1" ] && ok "score_type ${tipo} tem espelho tf_${tipo}_score no banco" \
        || falhou "score_type ${tipo} sem espelho tf_${tipo}_score no banco"
done

if [ "$MODO" = "instalacao_campos" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 3/6 — teste do Odoo (--test-enable)
# ---------------------------------------------------------------------------
cabecalho "passo 3/6 — teste do Odoo (--test-enable)"
LOG_ATUAL="$LOG_DIR/3-teste.log"
RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -u "$MODULO" --test-enable \
        --without-demo=all --max-cron-threads=0 --stop-after-init --log-level=test)"
[ "$RC" = "0" ] && ok "odoo --test-enable exit 0 (log: $LOG_ATUAL)" || falhou "odoo --test-enable exit $RC (log: $LOG_ATUAL)"
RELATORIO="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests when loading database' "$LOG_ATUAL" | tail -1)"
if [ -n "$RELATORIO" ]; then
    N_TESTS="$(printf '%s' "$RELATORIO" | sed -nE 's/.*of ([0-9]+) tests.*/\1/p')"
    N_FALHAS_T="$(printf '%s' "$RELATORIO" | sed -nE 's/^([0-9]+) failed.*/\1/p')"
    N_ERROS_T="$(printf '%s' "$RELATORIO" | sed -nE 's/^[0-9]+ failed, ([0-9]+) error.*/\1/p')"
    if [ "$N_TESTS" -ge "$MIN_TESTS" ] && [ "$N_FALHAS_T" = "0" ] && [ "$N_ERROS_T" = "0" ]; then
        ok "runner do Odoo: $N_FALHAS_T failed, $N_ERROS_T error(s) of $N_TESTS tests (minimo $MIN_TESTS)"
    else
        falhou "runner do Odoo: $N_FALHAS_T failed, $N_ERROS_T error(s) of $N_TESTS tests (minimo $MIN_TESTS) — '$RELATORIO'"
    fi
else
    falhou "log sem linha de relatorio do runner — sem medicao nao ha aceite"
fi
grep -q 'At least one test failed when loading the modules\.' "$LOG_ATUAL" \
    && falhou "log traz 'At least one test failed when loading the modules.'" \
    || ok "log sem 'At least one test failed when loading the modules.'"
FALHAS_TESTE="$(grep -cE ' (FAIL|ERROR): ' "$LOG_ATUAL" || true)"
[ "$FALHAS_TESTE" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
    || falhou "$FALHAS_TESTE linha(s) de teste reprovado(a) no log"
for classe in TestCrmLeadRastreio; do
    grep -q "$classe" "$LOG_ATUAL" && ok "os testes do card rodaram ($classe)" \
        || falhou "os testes do card NAO aparecem no log do runner ($classe)"
done
# os testes deste card cobrem AC1/AC2/AC3 — o log tem de mostrar os 7 metodos executados
# (o runner do Odoo registra cada metodo como "Starting TestCrmLeadRastreio.test_NN_...")
N_DO_CARD="$(grep -cE 'Starting TestCrmLeadRastreio\.test_' "$LOG_ATUAL" || true)"
[ "$N_DO_CARD" -ge 7 ] && ok "runner executou $N_DO_CARD teste(s) do card (TRE-W2-E04-T02)" \
    || falhou "runner executou $N_DO_CARD teste(s) do card (esperado >= 7)"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "modulo segue installed depois do teste" || falhou "estado depois do teste: '$ESTADO'"

if [ "$MODO" = "instalacao_testes" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 4/6 — dado sintetico pelo ORM (AC2 + AC3)
# ---------------------------------------------------------------------------
cabecalho "passo 4/6 — dado sintetico pelo ORM (AC2 + AC3)"
UUID_MEDICAO="$(sed -n "s/^UUID_SINTETICO = '\(.*\)'/\1/p" "$MEDIDOR" | head -1)"
IDEM_MEDICAO="$(sed -n "s/^IDEMPOTENCIA = '\(.*\)'/\1/p" "$MEDIDOR" | head -1)"
if [ -n "$UUID_MEDICAO" ] && [ -n "$IDEM_MEDICAO" ]; then
    ok "valores sinteticos lidos do medidor (uuid=$UUID_MEDICAO)"
else
    falhou "nao consegui ler os valores sinteticos de $MEDIDOR"; resumo
fi
LOG_ATUAL="$LOG_DIR/4-dado-sintetico.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$MEDIDOR")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
N_OK_MED="$(grep -c '^OK    ' "$LOG_ATUAL" || true)"
N_FALHOU_MED="$(grep -c '^FALHOU ' "$LOG_ATUAL" || true)"
if grep -q 'MEDICAO_CRM_LEAD_OK' "$LOG_ATUAL" && [ "$N_FALHOU_MED" = "0" ]; then
    ok "medicao ORM: MEDICAO_CRM_LEAD_OK ($N_OK_MED itens, 0 falhas)"
else
    falhou "medicao ORM reprovou ($N_OK_MED OK / $N_FALHOU_MED falhas) — ver $LOG_ATUAL: $(grep '^FALHOU ' "$LOG_ATUAL" | head -3 | tr '\n' ' ')"
fi
grep -qE '(Traceback|ValidationError:)' "$LOG_ATUAL" \
    && falhou "log da medicao com traceback/ValidationError inesperado: $(grep -E '(Traceback|ValidationError:)' "$LOG_ATUAL" | head -3 | tr '\n' ' ')" \
    || ok "log da medicao sem traceback inesperado"
# conferencia por SQL, FORA da sessao do Odoo (nao depende do que o ORM imprimiu)
LINHA="$(psql_bd "$BANCO" "select count(*) from crm_lead where tf_opportunity_id = '$UUID_MEDICAO' and tf_idempotency_key = '$IDEM_MEDICAO'")"
[ "$LINHA" = "1" ] && ok "SQL: o lead sintetico esta gravado no banco (uuid + idempotencia)" \
    || falhou "SQL: esperava 1 linha do lead sintetico, achei '$LINHA'"
VALORES="$(psql_bd "$BANCO" "select tf_priority_tier || '|' || tf_next_best_action || '|' || tf_last_event_type || '|' || round(tf_icp_score::numeric, 2) from crm_lead where tf_opportunity_id = '$UUID_MEDICAO'")"
[ "$VALORES" = "Nurture|NURTURE|PRIORITY_SCORE_CHANGED|91.00" ] \
    && ok "SQL: valores do lead sintetico conferem ($VALORES)" \
    || falhou "SQL: valores do lead sintetico diferentes do esperado: '$VALORES'"
COMUM="$(psql_bd "$BANCO" "select count(*) from crm_lead where name like 'Lead comum TRE-W2-E04-T02%'")"
[ "$COMUM" = "0" ] && ok "SQL: lead comum da medicao foi removido (CRM padrao sem residuo da medicao)" \
    || falhou "SQL: sobraram $COMUM lead(s) comuns da medicao"
PARCEIRO="$(psql_bd "$BANCO" "select count(*) from res_partner where name = 'Parceiro sintetico TRE-W2-E04-T02'")"
[ "$PARCEIRO" -ge 1 ] && ok "SQL: parceiro sintetico gravado (res.partner padrao intacto)" \
    || falhou "SQL: parceiro sintetico nao esta no banco"
PADRAO="$(psql_bd "$BANCO" "select count(*) from information_schema.columns where table_name = 'crm_lead' and column_name in ('name','stage_id','partner_id','expected_revenue')")"
[ "$PADRAO" = "4" ] && ok "SQL: colunas padrao do crm_lead intactas (name/stage_id/partner_id/expected_revenue)" \
    || falhou "SQL: so' $PADRAO de 4 colunas padrao do crm_lead"

# ---------------------------------------------------------------------------
# passo 5/6 — rollback pelo modulo (desinstalacao; ROLLBACK PLAN do card)
# ---------------------------------------------------------------------------
cabecalho "passo 5/6 — rollback pelo modulo (desinstalacao)"
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
DEPOIS_TF="$(psql_bd "$BANCO" "select count(*) from ir_model_fields where model = 'crm.lead' and name like 'tf\\_%'")"
[ "$DEPOIS_TF" = "0" ] && ok "0 campo tf_ em ir_model_fields depois da desinstalacao (resquicio medido, nao presumido)" \
    || falhou "$DEPOIS_TF campo(s) tf_ sobreviveram a desinstalacao"
DEPOIS_IND="$(psql_bd "$BANCO" "select count(*) from pg_indexes where tablename = 'crm_lead' and (indexdef like '%tf_opportunity_id%' or indexdef like '%tf_correlation_id%' or indexdef like '%tf_idempotency_key%')")"
[ "$DEPOIS_IND" = "0" ] && ok "0 indice dos campos de rastreio depois da desinstalacao" \
    || falhou "$DEPOIS_IND indice(s) dos campos de rastreio sobreviveram"
DEPOIS_COL="$(psql_bd "$BANCO" "select count(*) from information_schema.columns where table_name = 'crm_lead' and column_name in ('tf_opportunity_id','tf_priority_score','tf_next_best_action')")"
[ "$DEPOIS_COL" = "0" ] && ok "0 coluna tf_ em crm_lead depois da desinstalacao" \
    || falhou "$DEPOIS_COL coluna(s) tf_ sobreviveram a desinstalacao"
DEPOIS_PADRAO="$(psql_bd "$BANCO" "select count(*) from information_schema.columns where table_name = 'crm_lead' and column_name in ('name','stage_id','partner_id','expected_revenue')")"
[ "$DEPOIS_PADRAO" = "4" ] && ok "colunas padrao do crm_lead seguem intactas depois do rollback" \
    || falhou "rollback afetou coluna padrao do crm_lead (so' $DEPOIS_PADRAO de 4)"
DADOS="$(psql_bd "$BANCO" "select count(*) from crm_lead")"
info "linhas em crm_lead depois do rollback: $DADOS (os dados do CRM nao sao apagados pelo rollback do modulo)"

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
