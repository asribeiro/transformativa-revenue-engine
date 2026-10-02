#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E01-T02 — operacao de escrita de negocio `empresa_upsert`
# (`POST /tf/api/v1/empresa_upsert`) no modulo transformativa_sales_ai.
#
# Criterios de aceitacao (definidos no inicio do card, registrados no card e no runbook
# docs/runbooks/odoo-empresa-upsert.md):
#   AC1 operacao declarada e versionada (politica real declara `empresa_upsert` como escrita,
#       com `requer_idempotency_key: true`; servida pela MESMA porta unica — 1 rota);
#   AC2 identidade declarada, nao literal (`campos_de_identidade` na ordem canonico -> fortes do
#       contrato §5); sem nenhum identificador com valor -> 422 `identificador_ausente`;
#   AC3 upsert idempotente por identidade (cria uma vez, atualiza depois; 0 duplicata medida no
#       banco; identidade pelo canonico e pelos fortes CNPJ/dominio/LinkedIn);
#   AC4 ambiguidade reportada, nunca resolvida por heuristica (contrato §5): >1 registro casado
#       pelos identificadores do pedido -> 409 `valor_ambiguo` e NADA escrito;
#   AC5 semantica de empresa (`valores_fixos: {is_company: true}` declarado na politica; medido
#       no banco; divergencia do chamador -> 422 `campo_fixo_divergente`);
#   AC6 contrato de integracao (`idempotency_key` exigida/validada; `dry_run` nao escreve;
#       `correlation_id` ecoado; UUID canonico fora de formato -> 422);
#   AC7 guarda de ambiente (ADR-005) e rastro (UMA linha `TF_API_AUDIT` por chamada autenticada,
#       sem token e sem payload);
#   AC8 ambiente de execucao intocado (dupla descartavel propria; dev/homolog/prod medidos antes
#       e depois; nenhum DDL, migration ou escrita em sales_intelligence).
#
# TEST PLAN (executado por este script, NA VPS do dev):
#   passo 0  suite PURA do motor (sem Odoo)              -> scripts/odoo/testar_motor_api.py
#   passo 1  instalacao do modulo em banco limpo         -> log + estado lido no banco
#   passo 2  suite do Odoo (--test-enable)               -> relatorio do runner + nome dos testes
#   passo 3  preparo + servidor HTTP real + curl         -> upsert de verdade pelas 4 identidades,
#                                                          ambiguidade, dry-run, recusas e contagem
#   passo 3b medicao no banco (ORM e SQL de leitura)     -> 1 registro por identidade, is_company
#   passo 3c auditoria no log do servidor                -> 1 linha por chamada autenticada
#   passo 4  contrato do modulo (greps)                  -> 1 rota, bearer, POST, 0 SQL
#   limpeza  banco/dupla/rede removidos; dev, homolog e producao conferidos intactos
#
# ISOLAMENTO (mesma licao do E03/E01-T01): o Odoo do dev abre sessao em QUALQUER banco novo da
# instancia `pg-odoo-dev`, entao este verificador sobe a SUA propria dupla descartavel
# (postgres:16 + odoo:19.0, as mesmas imagens do par de dev) em rede propria, e nao usa
# `pg-odoo-dev`, `odoo_dev` nem a copia operacional /opt/tre/repo.
#
# Uso (NA VPS, a partir de arquivo):
#   bash verificar-empresa-upsert.sh
#   bash verificar-empresa-upsert.sh --apenas-motor
#   bash verificar-empresa-upsert.sh --apenas-suites        (instalacao + teste do Odoo)
#   bash verificar-empresa-upsert.sh --apenas-http         (instalacao + servidor + curl)
#   bash verificar-empresa-upsert.sh --prova-de-dente      (3 mutacoes; cada uma TEM de reprovar)
#   bash verificar-empresa-upsert.sh --banco tre_outro --modulo-dir /caminho/do/modulo
#
# Variaveis: TRE_MODULO, TRE_MODULO_DIR, TRE_BANCO, TRE_IMAGEM, TRE_IMAGEM_PG, TRE_PG_USER,
# TRE_LOG_DIR, TRE_DEV_PG_CT, TRE_MANTER_BANCO=1 (nao limpa no fim).
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo final em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir   2 = uso errado
# ============================================================================
set -u

MODULO="${TRE_MODULO:-transformativa_sales_ai}"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
RAIZ_REPO="$(cd "$AQUI/../.." && pwd)"
MODULO_DIR="${TRE_MODULO_DIR:-$RAIZ_REPO/odoo/addons/$MODULO}"
MOTOR_TESTE="${TRE_MOTOR_TESTE:-$AQUI/testar_motor_api.py}"
PREPARADOR="${TRE_PREPARADOR:-$AQUI/preparar_api_teste.py}"
BANCO="${TRE_BANCO:-tre_e01_t02_empresa}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-empresa-upsert}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"
# Piso de testes do modulo medido em 02/10/2026: 101 testes
# (50 dos cards W2 + 32 da suite da API do E01-T01 + 19 da suite do upsert de empresa do
# E01-T02). Piso = o medido: menos que isso e regressao (teste que nao roda nao passou).
PISO_DE_TESTES="${TRE_PISO_DE_TESTES:-101}"

# Identidades fixas da fase HTTP (o valor e' o que o contrato chama de identidade: UUID canonico
# de `organizations.id` e os fortes de dedup CNPJ/dominio/LinkedIn Company URL).
UUID_HTTP="7c9e6679-7425-40de-944b-e07fc1f90ae7"
UUID_CNPJ="7c9e6679-7425-40de-944b-e07fc1f90ae8"
CNPJ_HTTP="12.345.678/0001-95"
DOMINIO_HTTP="upsert-http.example"
LINKEDIN_HTTP="https://www.linkedin.com/company/upsert-http"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-motor) MODO=motor ;;
        --apenas-suites) MODO=suites ;;
        --apenas-http) MODO=http ;;
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
PG_TMP="e01t02-pg-$SUFIXO"
API_TMP="e01t02-api-$SUFIXO"
NET_TMP="e01t02-net-$SUFIXO"
DESC_DIR=""
API_CT=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: EMPRESA_UPSERT_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: EMPRESA_UPSERT_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? tres mutacoes, cada uma em copia propria do modulo.
#   dente 1: politica SEM a operacao `empresa_upsert`   -> o item "operacao declarada atende" reprova;
#   dente 2: controlador SEM o portao de ambiguidade    -> o item "identificadores conflitantes -> 409" reprova;
#   dente 3: motor SEM aplicar o valor fixo declarado   -> o item "a empresa criada e' empresa" reprova.
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e01t02-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_FALHAS=0

    # Ancora do alvo: o dente TEM de rodar contra uma copia do artefato REAL, e o artefato real
    # nao pode mudar por causa da prova (licao do E06/E01-T01: prova negativa que nao ancora o
    # alvo nao prova nada). A ancora e' o sha256 do conteudo do modulo, sem `__pycache__`.
    manifesto_modulo() {
        find "$MODULO_DIR" -type f -name '*.pyc' -prune -o -type d -name '__pycache__' -prune -o \
            -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
    }
    arquivos_modulo() {
        find "$MODULO_DIR" -type f -name '*.pyc' -prune -o -type d -name '__pycache__' -prune -o \
            -type f -print | wc -l | tr -d ' '
    }
    MANIFESTO_ANTES="$(manifesto_modulo)"
    ARQUIVOS_MODULO="$(arquivos_modulo)"
    echo "OK    ancora: alvo do dente = $MODULO_DIR ($ARQUIVOS_MODULO arquivos, sha256 $MANIFESTO_ANTES)"

    # AVALIACAO DO DENTE — nao basta o sub-run terminar em FALHOU: o dente so' vale se a copia
    # mutada (a) PASSOU pelas guardas (imagem/docker/banco) e (b) chegou a fase HTTP, e (c) o item
    # que a mutacao quebra reprovou. Sem isso o dente e' fail-open: imagem ausente ou ambiente
    # quebrado encerra o sub-run em FALHOU e o harness imprimiria "o dente tem dente" sem ter
    # medido nada — defeito medido pelo tester no aceite do E01-T01 (card `t_fa9db205`), que NAO
    # se repete aqui.
    avaliar_dente() { # $1=rotulo $2=saida do sub-run $3...=itens (qualquer um deles) que TEM de reprovar
        local rotulo="$1" arquivo="$2" marcador=""
        shift 2
        if grep -qE 'imagem .* ausente|docker nao responde|nao consegui subir|banco .* nao foi criado|openssl ausente' "$arquivo"; then
            echo "FALHOU $rotulo: o sub-run reprovou ANTES de medir (guarda/ambiente) — dente inconclusivo, nao 'dente que pegou'"
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
            return
        fi
        if ! grep -q '^OK    servidor Odoo no ar' "$arquivo"; then
            echo "FALHOU $rotulo: o sub-run nao chegou a fase HTTP (nenhum servidor no ar) — dente inconclusivo"
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
            return
        fi
        # O `falhou` de um item NAO precisa repetir o texto do `ok` (o do ramo negativo costuma dar o
        # diagnostico). Por isso a avaliacao aceita VARIOS marcadores e vale pelo primeiro que casar:
        # exigir um texto unico tornava o dente dependente da redacao do item, nao do comportamento.
        for marcador in "$@"; do
            if grep -qF "FALHOU $marcador" "$arquivo"; then
                echo "OK    $rotulo: reprovou o item que a mutacao quebra -> $marcador"
                return
            fi
        done
        echo "FALHOU $rotulo: nenhum dos itens esperados reprovou na copia mutada ($*) — mutacao sem dente"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    }

    # Autoteste do harness: marcador so vale se EXISTE como texto de item no proprio verificador.
    # A lista abaixo e' a mesma que o `avaliar_dente` usa; cada marcador tem de aparecer tambem
    # fora dela (senao um erro de digitacao viveria dizendo "mutacao sem dente" sem ninguem notar).
    for marcador_dente in \
        "empresa_upsert declarada como escrita com idempotency_key exigida" \
        "empresa_upsert nao declarada como escrita com chave" \
        "recusa nomeia valor_ambiguo (ambiguidade e' reportada, nao resolvida)" \
        "codigo de recusa errado (ambiguidade)" \
        "o parceiro criado e' EMPRESA (is_company=true, medido no banco)" \
        "o parceiro criado nao e' empresa"; do
        MARC_USOS="$(grep -cF "$marcador_dente" "$0")"
        [ "$MARC_USOS" -ge 2 ] \
            && ok "marcador de dente e' texto de item do verificador ($MARC_USOS usos): $marcador_dente" \
            || falhou "marcador de dente so aparece na propria lista ($MARC_USOS uso(s)): $marcador_dente"
    done

    cabecalho "prova de dente 1: politica sem a operacao empresa_upsert (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    MUT1="$(python3 - "$DENTE_DIR/m1/api/politica_api.json" <<'PY'
import json, sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    dados = json.load(fh)
antes = [op["nome"] for op in dados["operacoes"]]
dados["operacoes"] = [op for op in dados["operacoes"] if op["nome"] != "empresa_upsert"]
with open(caminho, "w", encoding="utf-8") as fh:
    json.dump(dados, fh, ensure_ascii=False, indent=2)
# A conferencia da mutacao e' por OPERACAO, nao por texto: a palavra `empresa_upsert` continua
# aparecendo na `descricao` da politica, entao um `grep` pela palavra acusaria "mutacao nao
# aplicada" com a mutacao aplicada (foi o que aconteceu na rodada 1 dos dentes).
with open(caminho, encoding="utf-8") as fh:
    depois = [op["nome"] for op in json.load(fh)["operacoes"]]
if "empresa_upsert" in depois:
    print("MUTACAO_NAO_APLICADA (ainda na politica: %s)" % ",".join(depois))
    raise SystemExit(3)
print("MUTACAO_APLICADA (antes: %s | depois: %s)" % (",".join(antes), ",".join(depois) or "nenhuma"))
PY
)"
    if [ "${MUT1#MUTACAO_APLICADA}" != "$MUT1" ]; then
        echo "OK    dente 1: mutacao aplicada — $MUT1"
    else
        echo "FALHOU dente 1: mutacao NAO foi aplicada na copia — o dente mediria o artefato intacto ($MUT1)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_BANCO="${BANCO}_d1" TRE_LOG_DIR="$LOG_DIR/dente1" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D1" >"$LOG_DIR/dente-1-sem-operacao.out"
    printf '%s\n' "$D1" | tail -3
    avaliar_dente "dente 1 (politica sem a operacao)" "$LOG_DIR/dente-1-sem-operacao.out" \
        "empresa_upsert declarada como escrita com idempotency_key exigida" \
        "empresa_upsert nao declarada como escrita com chave"

    cabecalho "prova de dente 2: controlador sem o portao de ambiguidade (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    MUT2="$(python3 - "$DENTE_DIR/m2/controllers/api_controlada.py" <<'PY'
import sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    texto = fh.read()
antes = texto
texto = texto.replace("        if len(registros) > 1:", "        if False:  # mutacao: portao desligado", 1)
if texto == antes:
    print("MUTACAO_NAO_APLICADA")
    sys.exit(3)
with open(caminho, "w", encoding="utf-8") as fh:
    fh.write(texto)
print("MUTACAO_APLICADA")
PY
)"
    if [ "$MUT2" = "MUTACAO_APLICADA" ]; then
        echo 'OK    dente 2: mutacao aplicada na copia do controlador'
    else
        echo "FALHOU dente 2: mutacao NAO foi aplicada na copia do controlador ($MUT2)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_d2" TRE_LOG_DIR="$LOG_DIR/dente2" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D2" >"$LOG_DIR/dente-2-sem-portao.out"
    printf '%s\n' "$D2" | tail -3
    avaliar_dente "dente 2 (controlador sem o portao de ambiguidade)" "$LOG_DIR/dente-2-sem-portao.out" \
        "recusa nomeia valor_ambiguo (ambiguidade e' reportada, nao resolvida)" \
        "codigo de recusa errado (ambiguidade)"

    cabecalho "prova de dente 3: motor sem aplicar o valor fixo declarado (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m3"
    MUT3="$(python3 - "$DENTE_DIR/m3/api/motor.py" <<'PY'
import sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    texto = fh.read()
antes = texto
texto = texto.replace("    valores_finais.update(fixos)",
                      "    valores_finais.update({})  # mutacao: valor fixo nao entra", 1)
if texto == antes:
    print("MUTACAO_NAO_APLICADA")
    sys.exit(3)
with open(caminho, "w", encoding="utf-8") as fh:
    fh.write(texto)
print("MUTACAO_APLICADA")
PY
)"
    if [ "$MUT3" = "MUTACAO_APLICADA" ]; then
        echo 'OK    dente 3: mutacao aplicada na copia do motor'
    else
        echo "FALHOU dente 3: mutacao NAO foi aplicada na copia do motor ($MUT3)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D3="$(TRE_MODULO_DIR="$DENTE_DIR/m3" TRE_BANCO="${BANCO}_d3" TRE_LOG_DIR="$LOG_DIR/dente3" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D3" >"$LOG_DIR/dente-3-sem-valor-fixo.out"
    printf '%s\n' "$D3" | tail -3
    avaliar_dente "dente 3 (motor sem aplicar o valor fixo)" "$LOG_DIR/dente-3-sem-valor-fixo.out" \
        "o parceiro criado e' EMPRESA (is_company=true, medido no banco)" \
        "o parceiro criado nao e' empresa"

    # Controle do PROPRIO harness de dente: se ele contar como dente um sub-run que reprovou por
    # ambiente (comando barrado antes de medir) ou que nao reprovou o item alvo, a prova de dente
    # inteira e' decorativa. Os dois controles abaixo sao sinteticos de proposito (o alvo e' a
    # FUNCAO de avaliacao, nao o modulo).
    cabecalho "controle do harness de dente (nao pode ser fail-open)"
    printf 'FALHOU imagem odoo:19.0 ausente (nada a medir)\nRESULTADO: EMPRESA_UPSERT_FALHOU (3 itens, 1 falha(s))\n' \
        >"$DENTE_DIR/controle-cego.out"
    ANTES="$DENTE_FALHAS"
    avaliar_dente "controle-cego (ambiente quebrado)" "$DENTE_DIR/controle-cego.out" "item qualquer" >/dev/null
    if [ "$DENTE_FALHAS" -gt "$ANTES" ]; then
        echo 'OK    controle: sub-run que reprova por AMBIENTE nao conta como dente (nao e fail-open)'
        # O incremento acima e' o SINAL de que o controle pegou o caso; ele volta ao valor anterior
        # para nao virar falha fantasma no veredito final — o contador mede as PROVAS de dente, nao
        # os controles (falso FALHOU medido na rodada 3 dos dentes). Se o controle NAO pegar o caso,
        # o else nao restaura e a falha fica contada.
        DENTE_FALHAS="$ANTES"
    else
        echo 'FALHOU controle: sub-run cego foi aceito como dente — o harness e fail-open'
    fi
    printf 'OK    servidor Odoo no ar em 127.0.0.1:1 (container simulado)\nOK    item qualquer -> HTTP 200\n' \
        >"$DENTE_DIR/controle-sem-dente.out"
    ANTES="$DENTE_FALHAS"
    avaliar_dente "controle-sem-dente (mutacao inocua)" "$DENTE_DIR/controle-sem-dente.out" "item qualquer" >/dev/null
    if [ "$DENTE_FALHAS" -gt "$ANTES" ]; then
        echo 'OK    controle: item alvo que NAO reprova e reportado como mutacao sem dente'
        DENTE_FALHAS="$ANTES"
    else
        echo 'FALHOU controle: mutacao que nao muda nada foi contada como dente'
    fi

    MANIFESTO_DEPOIS="$(manifesto_modulo)"
    if [ "$MANIFESTO_DEPOIS" = "$MANIFESTO_ANTES" ]; then
        echo "OK    guarda externa: o artefato real nao foi tocado pelos dentes ($ARQUIVOS_MODULO arquivos, sha256 $MANIFESTO_DEPOIS)"
    else
        echo "FALHOU guarda externa: o artefato real MUDOU durante as provas ($MANIFESTO_ANTES -> $MANIFESTO_DEPOIS)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi

    echo '---'
    if [ "$DENTE_FALHAS" -eq 0 ]; then
        echo "RESULTADO: EMPRESA_UPSERT_DENTE_OK (3 provas + 2 controles do proprio harness, 0 falhas) modulo=$MODULO"
        exit 0
    fi
    echo "RESULTADO: EMPRESA_UPSERT_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente) modulo=$MODULO"
    exit 1
fi

# ---------------------------------------------------------------------------
# passo 0 — suite PURA do motor (roda na VPS, sem Odoo)
# ---------------------------------------------------------------------------
if [ "$MODO" = "motor" ] || [ "$MODO" = "completo" ]; then
    cabecalho "passo 0 — suite pura do motor (sem Odoo)"
    if ! command -v python3 >/dev/null 2>&1; then
        falhou "python3 ausente na VPS (sem ele nao ha' medicao do motor)"; resumo
    fi
    if [ -f "$MOTOR_TESTE" ] && [ -f "$MODULO_DIR/api/motor.py" ]; then
        SAIDA_MOTOR="$(TRE_MODULO_DIR="$MODULO_DIR" python3 "$MOTOR_TESTE" 2>&1)"
        printf '%s\n' "$SAIDA_MOTOR" >"$LOG_DIR/0-motor-puro.out"
        printf '%s\n' "$SAIDA_MOTOR" | grep -E '^(OK|FALHOU) ' | sed 's/^/      /'
        RESULTADO_MOTOR="$(printf '%s\n' "$SAIDA_MOTOR" | grep -E '^RESULTADO: ' | tail -1)"
        case "$RESULTADO_MOTOR" in
            *MOTOR_API_OK*) ok "suite pura do motor: $RESULTADO_MOTOR" ;;
            *) falhou "suite pura do motor NAO passou: ${RESULTADO_MOTOR:-sem linha de resultado}" ;;
        esac
    else
        falhou "suite pura ou motor ausente (motor=$MODULO_DIR/api/motor.py teste=$MOTOR_TESTE)"
    fi
    if [ "$MODO" = "motor" ]; then resumo; fi
fi

# ---------------------------------------------------------------------------
# guardas do ambiente (fail-closed: sem ambiente medido nao existe aceite)
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
command -v curl >/dev/null 2>&1 && ok "curl disponivel (fase HTTP externa)" \
    || { falhou "curl ausente"; resumo; }
for arquivo in __manifest__.py api/motor.py api/politica_api.json controllers/api_controlada.py \
               tests/test_empresa_upsert.py; do
    if [ -f "$MODULO_DIR/$arquivo" ]; then
        ok "modulo em disco: $arquivo"
    else
        falhou "modulo ausente em $MODULO_DIR/$arquivo"; resumo
    fi
done
info "sha256 dos arquivos sob teste:"
for arquivo in api/politica_api.json api/motor.py controllers/api_controlada.py \
               tests/test_empresa_upsert.py; do
    if [ -f "$MODULO_DIR/$arquivo" ]; then
        printf '      %s  %s\n' "$(sha256sum "$MODULO_DIR/$arquivo" | cut -d' ' -f1)" "$arquivo"
    fi
done
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e' do ambiente — so' banco descartavel"; resumo ;;
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
# dupla descartavel propria (ver o cabecalho: por que nao usar pg-odoo-dev)
# ---------------------------------------------------------------------------
cabecalho "dupla descartavel propria (postgres + odoo)"
DESC_DIR="$(mktemp -d /tmp/verificacao-empresa-XXXXXX)"
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
    # As duas chaves vao por variavel de proposito: o scanner de segredo do repo reprova a forma
    # literal "<chave> = <valor>" no codigo mesmo quando o valor e' uma variavel (mesma convencao
    # do provisionamento do dev e dos aceites do E01-T01/E03).
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

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
limpeza() {
    [ -n "$API_CT" ] && docker rm -f "$API_CT" >/dev/null 2>&1
    docker rm -f "$PG_TMP" >/dev/null 2>&1
    docker network rm "$NET_TMP" >/dev/null 2>&1
    [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR"
}
trap limpeza EXIT

psql_bd() { # $1=banco $2=sql (o `docker exec` nao le o stdin do chamador)
    docker exec "$PG_TMP" psql -U "$PG_USER" -d "$1" -tAc "$2" 2>/dev/null
}
banco_existe() {
    [ "$(psql_bd postgres "select count(*) from pg_database where datname = '$1'")" = "1" ]
}
banco_limpo() { docker exec "$PG_TMP" dropdb -U "$PG_USER" --if-exists --force "$1" >/dev/null 2>&1; }
odoo_ci() { # $1=log; restantes = args do odoo (container descartavel, --stop-after-init)
    local log="$1"; shift
    docker run --rm --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        --entrypoint odoo "$IMAGEM" "$@" >"$log" 2>&1
    echo $?
}
erros_no_log() { grep -cE '(^| )(ERROR|CRITICAL) ' "$1" 2>/dev/null || true; }
subir_servidor() { # sobe o servidor HTTP descartavel no banco $BANCO; define API_CT, PORTA_API, BASE
    API_CT="$API_TMP"
    docker run -d --name "$API_CT" --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -p 127.0.0.1::8069 \
        --entrypoint odoo "$IMAGEM" -d "$BANCO" --max-cron-threads=0 --db-filter="^$BANCO\$" \
        >/dev/null 2>&1
    PORTA_API="$(docker port "$API_CT" 8069/tcp 2>/dev/null | head -1 | sed 's/.*://')"
    if [ -n "$PORTA_API" ]; then
        ok "servidor Odoo no ar em 127.0.0.1:$PORTA_API (container $API_CT)"
    else
        falhou "nao consegui descobrir a porta publicada do servidor de API"; resumo
    fi
    BASE="http://127.0.0.1:$PORTA_API"
    local vivo=0 codigo=nenhum
    for _ in $(seq 1 30); do
        codigo="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$BASE/web/login" || true)"
        [ "$codigo" = "200" ] && { vivo=1; break; }
        sleep 3
    done
    [ "$vivo" = "1" ] && ok "servidor responde HTTP 200 em /web/login" \
        || falhou "servidor nao respondeu /web/login (ultimo codigo: $codigo)"
}
preparar_api() { # $1=log $2=ambiente [$3=arquivo da chave]
    docker run --rm -i --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -v "$DESC_DIR":/preparo \
        -e "TRE_API_AMBIENTE=${2:-dev}" \
        -e "TRE_API_ARQUIVO_CHAVE=/preparo/${3:-chave.txt}" \
        --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$PREPARADOR" >"$1" 2>&1
}
escrever_cfg_curl() { # $1=arquivo $2=caminho do arquivo da chave
    {
        echo 'silent'
        echo 'show-error'
        echo 'header = "Content-Type: application/json"'
        printf 'header = "Authorization: Bearer %s"\n' "$(cat "$2")"
    } >"$1"
    chmod 600 "$1"
}

# ---------------------------------------------------------------------------
# passo 1 — instalacao em banco limpo (base dos passos 2 e 3)
# ---------------------------------------------------------------------------
if [ "$MODO" = "completo" ] || [ "$MODO" = "suites" ] || [ "$MODO" = "http" ]; then
    cabecalho "passo 1 — instalacao do modulo em banco limpo"
    banco_limpo "$BANCO"
    if banco_existe "$BANCO"; then falhou "banco $BANCO continua existindo depois do drop"; resumo
    else ok "banco $BANCO nao existia (limpo) antes da instalacao"; fi
    LOG_ATUAL="$LOG_DIR/1-instalacao.log"
    RC="$(odoo_ci "$LOG_ATUAL" -d "$BANCO" -i "$MODULO" --without-demo=all --max-cron-threads=0 \
            --stop-after-init --log-level=info)"
    [ "$RC" = "0" ] && ok "odoo --init exit 0 (log: $LOG_ATUAL)" || falhou "odoo --init exit $RC"
    ERROS="$(erros_no_log "$LOG_ATUAL")"
    [ "$ERROS" = "0" ] && ok "log de instalacao sem linha ERROR/CRITICAL" \
        || falhou "log de instalacao com $ERROS linha(s) ERROR/CRITICAL"
    grep -q 'Modules loaded\.' "$LOG_ATUAL" && ok "log de instalacao com 'Modules loaded.'" \
        || falhou "log de instalacao sem 'Modules loaded.'"
    banco_existe "$BANCO" && ok "banco $BANCO criado pelo Odoo" || falhou "banco $BANCO nao foi criado"
    ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
    [ "$ESTADO" = "installed" ] && ok "ir_module_module.state = installed" \
        || falhou "estado no banco: '$ESTADO' (esperado installed)"
fi

# ---------------------------------------------------------------------------
# passo 2 — suite do Odoo (--test-enable): a suite do upsert de empresa + as anteriores
# ---------------------------------------------------------------------------
if [ "$MODO" = "completo" ] || [ "$MODO" = "suites" ]; then
    cabecalho "passo 2 — suite do Odoo (--test-enable)"
    LOG_ATUAL="$LOG_DIR/2-teste.log"
    RC="$(odoo_ci "$LOG_ATUAL" -d "$BANCO" -u "$MODULO" --test-enable --without-demo=all \
            --max-cron-threads=0 --stop-after-init --log-level=test)"
    [ "$RC" = "0" ] && ok "odoo --test-enable exit 0 (log: $LOG_ATUAL)" \
        || falhou "odoo --test-enable exit $RC"
    RELATORIO="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests when loading database' \
        "$LOG_ATUAL" | tail -1)"
    if [ -n "$RELATORIO" ]; then
        N_TESTS="$(printf '%s' "$RELATORIO" | sed -nE 's/.*of ([0-9]+) tests.*/\1/p')"
        N_FALHAS="$(printf '%s' "$RELATORIO" | sed -nE 's/^([0-9]+) failed.*/\1/p')"
        N_ERROS="$(printf '%s' "$RELATORIO" | sed -nE 's/^[0-9]+ failed, ([0-9]+) error.*/\1/p')"
        if [ "$N_FALHAS" = "0" ] && [ "$N_ERROS" = "0" ] && [ "$N_TESTS" -ge "$PISO_DE_TESTES" ]; then
            ok "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests (piso $PISO_DE_TESTES)"
        else
            falhou "runner do Odoo: $RELATORIO (esperado 0 failed, 0 errors, >= $PISO_DE_TESTES testes)"
        fi
    else
        falhou "log sem a linha de relatorio do runner — sem medicao nao ha' aceite"
    fi
    # O Odoo 19 escreve `<hora> <pid> ERROR <banco> <modulo>: FAIL: TestX.test_y`: ancorar no
    # inicio da linha (o defeito do E03-T01/D01) imprimia OK com teste reprovado.
    LINHAS_REPROVADAS="$(grep -cE '(^| )(FAIL|ERROR): [A-Za-z_]' "$LOG_ATUAL" || true)"
    [ "$LINHAS_REPROVADAS" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
        || falhou "$LINHAS_REPROVADAS linha(s) de teste reprovado(a) no log"
    FALTANDO=0
    TOTAL_SUITE=0
    if [ -f "$MODULO_DIR/tests/test_empresa_upsert.py" ]; then
        while read -r metodo; do
            [ -z "$metodo" ] && continue
            TOTAL_SUITE=$((TOTAL_SUITE + 1))
            grep -q "$metodo" "$LOG_ATUAL" || { FALTANDO=$((FALTANDO + 1)); info "teste ausente no log: $metodo"; }
        done <<<"$(grep -oE 'def (test_[0-9]+_[a-z_0-9]+)' "$MODULO_DIR/tests/test_empresa_upsert.py" \
            | sed 's/^def //' | sort -u)"
    fi
    if [ "$TOTAL_SUITE" -ge 15 ] && [ "$FALTANDO" = "0" ]; then
        ok "todos os $TOTAL_SUITE testes da suite do upsert de empresa aparecem no log do runner"
    else
        falhou "suite do upsert de empresa: $TOTAL_SUITE testes encontrados, $FALTANDO ausentes no log"
    fi
    ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
    [ "$ESTADO" = "installed" ] && ok "modulo segue installed depois da suite" \
        || falhou "estado depois da suite: '$ESTADO'"
fi

# ---------------------------------------------------------------------------
# passo 3 — HTTP externo de verdade: preparo + servidor + curl (AC2/AC3/AC4/AC5/AC6/AC7)
# ---------------------------------------------------------------------------
if [ "$MODO" = "completo" ] || [ "$MODO" = "http" ]; then
    cabecalho "passo 3 — servidor HTTP real + curl (consumidor externo)"
    LOG_ATUAL="$LOG_DIR/3-preparo.log"
    # O container `odoo:19.0` roda como uid 100/gid 101: o diretorio descartavel tem de ser
    # gravavel por ELE para o preparo escrever a chave.
    chown 100:101 "$DESC_DIR" 2>/dev/null || info "nao consegui chown do diretorio descartavel"
    preparar_api "$LOG_ATUAL" dev chave.txt
    grep -q 'TF_API_PREPARO_OK' "$LOG_ATUAL" && ok "preparo do ambiente descartavel (usuario de integracao + parametros)" \
        || falhou "preparo nao confirmou (log: $LOG_ATUAL)"
    if [ -s "$DESC_DIR/chave.txt" ] && [ "$(stat -c '%a' "$DESC_DIR/chave.txt")" = "600" ]; then
        ok "chave de API gerada em arquivo 600 (fora de stdout, log e argumento)"
    else
        falhou "chave de API ausente ou sem permissao 600 em $DESC_DIR/chave.txt"
    fi

    subir_servidor
    if [ "$FALHAS" -gt 0 ]; then resumo; fi

    # curl por arquivo de configuracao (600): o token NUNCA entra em argumento de comando
    CFG="$DESC_DIR/curl.cfg"
    escrever_cfg_curl "$CFG" "$DESC_DIR/chave.txt"
    CFG_RUIM="$DESC_DIR/curl-ruim.cfg"
    {
        echo 'silent'
        echo 'show-error'
        echo 'header = "Content-Type: application/json"'
        echo 'header = "Authorization: Bearer chave-...0000"'
    } >"$CFG_RUIM"
    chmod 600 "$CFG_RUIM"

    CHAMADAS=0
    AUDITADAS=0
    ULTIMA_RESPOSTA=""
    api_post() { # $1=rotulo $2=codigo_esperado $3=operacao $4=arquivo com o corpo [$5=1|0 audita]
        local rotulo="$1" esperado="$2" operacao="$3" arquivo="$4" audita="${5:-1}"
        local codigo
        codigo="$(curl --config "$CFG" -X POST -d @"$arquivo" -o "$DESC_DIR/resposta.json" \
            -w '%{http_code}' "$BASE/tf/api/v1/$operacao" || true)"
        CHAMADAS=$((CHAMADAS + 1))
        if [ "$audita" = "1" ]; then
            # So' conta como auditaria a chamada que CHEGA ao controlador: 401 do `auth='bearer'`
            # morre antes da rota e nao gera trilha — somar essa linha faria o item de auditoria
            # medir um numero que nao existe (licao desta rodada).
            AUDITADAS=$((AUDITADAS + 1))
        fi
        cp "$DESC_DIR/resposta.json" "$DESC_DIR/resposta-$CHAMADAS.json"
        ULTIMA_RESPOSTA="$DESC_DIR/resposta-$CHAMADAS.json"
        if [ "$codigo" = "$esperado" ]; then
            ok "$rotulo -> HTTP $codigo"
        else
            falhou "$rotulo -> HTTP $codigo (esperado $esperado): $(head -c 300 "$ULTIMA_RESPOSTA" | tr -d '\n')"
        fi
    }
    campo_json() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))" \
        "$1" "$2"; }
    corpo() { # $1=arquivo; o conteudo vem do stdin
        cat >"$DESC_DIR/$1"
    }

    # ---- AC1: a operacao esta declarada e servida pela mesma porta unica -------------------
    corpo "corpo-capacidades.json" <<'JSON'
{"correlation_id": "tre-e01-t02-http-capacidades"}
JSON
    api_post "operacao declarada (sistema_capacidades)" "200" "sistema_capacidades" \
        "$DESC_DIR/corpo-capacidades.json"
    OPERACAO_DECLARADA="$(python3 - "$ULTIMA_RESPOSTA" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1]))
ops = {op["nome"]: op for op in dados["dados"]["capacidades"]["operacoes"]}
op = ops.get("empresa_upsert")
print("%s|%s|%s" % (bool(op), op and op["tipo"], op and op["requer_idempotency_key"]))
PY
)"
    [ "$OPERACAO_DECLARADA" = "True|escrita|True" ] \
        && ok "empresa_upsert declarada como escrita com idempotency_key exigida ($OPERACAO_DECLARADA)" \
        || falhou "empresa_upsert nao declarada como escrita com chave ($OPERACAO_DECLARADA)"
    VERSAO_POLITICA="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['versao'])" \
        "$MODULO_DIR/api/politica_api.json")"
    VERSAO_RESPOSTA="$(campo_json "$ULTIMA_RESPOSTA" "d['politica_versao']")"
    [ "$VERSAO_RESPOSTA" = "$VERSAO_POLITICA" ] \
        && ok "envelope traz a versao da politica em vigor ($VERSAO_RESPOSTA)" \
        || falhou "politica_versao '$VERSAO_RESPOSTA' difere da politica em vigor '$VERSAO_POLITICA'"

    # ---- AC6: sem token e com token invalido ------------------------------------------------
    SAIDA="$(curl -s -X POST -H 'Content-Type: application/json' -d '{}' -o "$DESC_DIR/resposta.json" \
        -w '%{http_code}' "$BASE/tf/api/v1/empresa_upsert" || true)"
    [ "$SAIDA" = "401" ] && ok "sem token na rota -> HTTP 401" || falhou "sem token -> HTTP $SAIDA (esperado 401)"
    # A recusa de autenticacao acontece ANTES da rota: nao gera linha de trilha, entao esta
    # chamada entra na conta de chamadas mas NAO na conta de chamadas auditadas.
    SAIDA="$(curl --config "$CFG_RUIM" -X POST -d '{}' -o "$DESC_DIR/resposta.json" \
        -w '%{http_code}' "$BASE/tf/api/v1/empresa_upsert" || true)"
    [ "$SAIDA" = "401" ] && ok "token invalido -> HTTP 401 (sem trilha, como o sem-token)" \
        || falhou "token invalido -> HTTP $SAIDA (esperado 401)"

    # ---- AC3: cria pela identidade canonica e atualiza depois (sem duplicar) ----------------
    corpo "corpo-1.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0001", "correlation_id": "tre-e01-t02-http-1",
 "parametros": {"valores": {"name": "Empresa HTTP Um", "tf_company_id": "$UUID_HTTP",
                            "tf_cnpj": "$CNPJ_HTTP", "tf_priority_score": 71.5}}}
JSON
    api_post "upsert de empresa: primeira chamada (cria)" "200" "empresa_upsert" "$DESC_DIR/corpo-1.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "criar" ] \
        && ok "primeira chamada responde acao_efetiva=criar" || falhou "primeira chamada nao criou"
    ID_CRIADO="$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['correlation_id']")" = "tre-e01-t02-http-1" ] \
        && ok "correlation_id do chamador ecoado" || falhou "correlation_id nao ecoado"
    corpo "corpo-2.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0002", "correlation_id": "tre-e01-t02-http-2",
 "parametros": {"valores": {"name": "Empresa HTTP Dois", "tf_company_id": "$UUID_HTTP"}}}
JSON
    api_post "mesma identidade: segunda chamada (atualiza)" "200" "empresa_upsert" "$DESC_DIR/corpo-2.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "atualizar" ] \
        && ok "segunda chamada responde acao_efetiva=atualizar" || falhou "segunda chamada nao atualizou"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")" = "$ID_CRIADO" ] \
        && ok "atualizou o MESMO registro (id $ID_CRIADO)" || falhou "atualizou outro registro"
    # repeticao: nem o numero de registros nem o id podem mudar (idempotencia por identidade)
    corpo "corpo-3.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0003",
 "parametros": {"valores": {"name": "Empresa HTTP Tres", "tf_company_id": "$UUID_HTTP"}}}
JSON
    api_post "terceira chamada da mesma empresa" "200" "empresa_upsert" "$DESC_DIR/corpo-3.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")" = "$ID_CRIADO" ] \
        && ok "terceira chamada segue no mesmo registro (sem duplicata)" || falhou "terceira chamada mudou o registro"

    # ---- AC3: identidade pelo forte quando o canonico nao vem -------------------------------
    corpo "corpo-4.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0004",
 "parametros": {"valores": {"name": "Empresa por CNPJ", "tf_cnpj": "$CNPJ_HTTP"}}}
JSON
    api_post "identidade por CNPJ (sem o canonico)" "200" "empresa_upsert" "$DESC_DIR/corpo-4.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "atualizar" ] \
        && ok "o CNPJ ja' cadastrado casa o MESMO registro (atualiza, nao duplica)" \
        || falhou "CNPJ repetido nao casou o registro existente"

    # ---- AC2: sem identificador nenhum ------------------------------------------------------
    corpo "corpo-5.json" <<'JSON'
{"idempotency_key": "tre-e01-t02-http-0005",
 "parametros": {"valores": {"name": "Empresa sem identificador"}}}
JSON
    api_post "upsert sem nenhum identificador" "422" "empresa_upsert" "$DESC_DIR/corpo-5.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "identificador_ausente" ] \
        && ok "recusa nomeia identificador_ausente" || falhou "codigo de recusa errado (identidade)"

    # ---- AC4: ambiguidade (dois identificadores apontando para registros diferentes) --------
    # ARMADILHA MEDIDA: as duas linhas nascem por SQL cru e o `active` PRECISA vir explicito — o
    # default `True` de `active` e' do ORM, nao da coluna: sem ele a linha existe para o SQL e fica
    # INVISIVEL para o `search` do Odoo (que filtra `active = true`), a API nao casa nada e CRIA um
    # terceiro registro em vez de recusar (medido: HTTP 200 `criar`, 9 -> 10 registros). Por isso o
    # fixture so' e' aceito depois de lido DE VOLTA pelo ORM, pelo proprio caminho declarado.
    psql_bd "$BANCO" "insert into res_partner (name, is_company, active, tf_cnpj, create_date, write_date)
                      values ('Ambiguo CNPJ', true, true, '99.888.777/0001-66', now(), now())" >/dev/null 2>&1
    psql_bd "$BANCO" "insert into res_partner (name, is_company, active, tf_domain, create_date, write_date)
                      values ('Ambiguo Dominio', true, true, '$DOMINIO_HTTP', now(), now())" >/dev/null 2>&1
    NAO_CASADOS="$(psql_bd "$BANCO" "select count(*) from res_partner
        where tf_cnpj = '99.888.777/0001-66' or tf_domain = '$DOMINIO_HTTP'")"
    [ "$NAO_CASADOS" = "2" ] && ok "base preparada com dois registros para o caso ambiguo" \
        || falhou "nao consegui preparar os dois registros do caso ambiguo (achei $NAO_CASADOS)"
    corpo "corpo-6b.json" <<'JSON'
{"correlation_id": "tre-e01-t02-http-ambig-fixture",
 "parametros": {"modelo": "res.partner", "campos": ["id", "name"],
                "filtro": [["tf_cnpj", "=", "99.888.777/0001-66"]]}}
JSON
    api_post "fixture do caso ambiguo lido de volta pelo caminho declarado" "200" "crm_registros_ler" \
        "$DESC_DIR/corpo-6b.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['total']")" = "1" ] \
        && ok "o fixture e' visivel para o ORM (1 registro lido pela propria API)" \
        || falhou "fixture invisivel para o ORM — a medicao de ambiguidade mediria outra coisa"
    ANTES_AMBIGUO="$(psql_bd "$BANCO" "select count(*) from res_partner")"
    corpo "corpo-6.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0006",
 "parametros": {"valores": {"name": "Empresa Ambigua", "tf_cnpj": "99.888.777/0001-66",
                            "tf_domain": "$DOMINIO_HTTP"}}}
JSON
    api_post "identificadores casando registros DIFERENTES" "409" "empresa_upsert" "$DESC_DIR/corpo-6.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "valor_ambiguo" ] \
        && ok "recusa nomeia valor_ambiguo (ambiguidade e' reportada, nao resolvida)" \
        || falhou "codigo de recusa errado (ambiguidade)"
    DEPOIS_AMBIGUO="$(psql_bd "$BANCO" "select count(*) from res_partner")"
    [ "$ANTES_AMBIGUO" = "$DEPOIS_AMBIGUO" ] \
        && ok "nada foi criado na recusa por ambiguidade ($DEPOIS_AMBIGUO registros)" \
        || falhou "a recusa por ambiguidade criou registro ($ANTES_AMBIGUO -> $DEPOIS_AMBIGUO)"
    ALTERADOS="$(psql_bd "$BANCO" "select count(*) from res_partner
        where (name = 'Ambiguo CNPJ' and tf_domain is not null)
           or (name = 'Ambiguo Dominio' and tf_cnpj is not null)")"
    [ "$ALTERADOS" = "0" ] && ok "nenhum dos dois registros foi alterado pela recusa" \
        || falhou "a recusa por ambiguidade alterou $ALTERADOS registro(s)"

    # ---- AC5: valor fixo (a empresa nasce empresa) e divergencia recusada -------------------
    corpo "corpo-7.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0007",
 "parametros": {"valores": {"name": "Empresa Pessoa", "is_company": false,
                            "tf_company_id": "$UUID_CNPJ"}}}
JSON
    api_post "chamador tentando decidir o valor fixo is_company" "422" "empresa_upsert" "$DESC_DIR/corpo-7.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "campo_fixo_divergente" ] \
        && ok "recusa nomeia campo_fixo_divergente" || falhou "codigo de recusa errado (valor fixo)"
    corpo "corpo-8.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0008",
 "parametros": {"valores": {"name": "Empresa HTTP Oito", "tf_company_id": "$UUID_CNPJ"}}}
JSON
    api_post "cria empresa com o valor fixo declarado" "200" "empresa_upsert" "$DESC_DIR/corpo-8.json"
    IS_COMPANY="$(psql_bd "$BANCO" "select is_company from res_partner where tf_company_id = '$UUID_CNPJ'")"
    [ "$IS_COMPANY" = "t" ] && ok "o parceiro criado e' EMPRESA (is_company=true, medido no banco)" \
        || falhou "o parceiro criado nao e' empresa (is_company='$IS_COMPANY')"

    # ---- AC6: dry_run nao escreve; UUID invalido recusado; chave exigida --------------------
    ANTES_DRY="$(psql_bd "$BANCO" "select count(*) from res_partner")"
    corpo "corpo-9.json" <<'JSON'
{"idempotency_key": "tre-e01-t02-http-0009", "dry_run": true,
 "parametros": {"valores": {"name": "Empresa Dry Run", "tf_domain": "dry-run.example"}}}
JSON
    api_post "dry-run de empresa nova" "200" "empresa_upsert" "$DESC_DIR/corpo-9.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "criar" ] \
        && ok "dry-run descreve acao_efetiva=criar" || falhou "dry-run nao descreveu a criacao"
    DEPOIS_DRY="$(psql_bd "$BANCO" "select count(*) from res_partner")"
    [ "$ANTES_DRY" = "$DEPOIS_DRY" ] && ok "dry-run NAO escreveu ($DEPOIS_DRY registros)" \
        || falhou "dry-run escreveu ($ANTES_DRY -> $DEPOIS_DRY)"
    corpo "corpo-10.json" <<'JSON'
{"idempotency_key": "tre-e01-t02-http-0010",
 "parametros": {"valores": {"name": "UUID invalido", "tf_company_id": "nao-e-uuid"}}}
JSON
    api_post "UUID canonico fora do formato" "422" "empresa_upsert" "$DESC_DIR/corpo-10.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "valor_invalido" ] \
        && ok "recusa nomeia valor_invalido (constraint do modelo, via ORM)" \
        || falhou "codigo de recusa errado (UUID)"
    # A recusa tem de ser LIMPA: o ORM levanta no meio do `create`, entao sem rollback cirurgico
    # (savepoint) o envelope diria "recusado" com o registro gravado — medido no aceite do card.
    CRIADOS_INVALIDOS="$(psql_bd "$BANCO" "select count(*) from res_partner where name = 'UUID invalido'")"
    [ "$CRIADOS_INVALIDOS" = "0" ] && ok "a recusa por UUID invalido NAO gravou registro" \
        || falhou "a recusa por UUID invalido gravou $CRIADOS_INVALIDOS registro(s)"
    corpo "corpo-11.json" <<'JSON'
{"parametros": {"valores": {"name": "Sem chave", "tf_domain": "sem-chave.example"}}}
JSON
    api_post "escrita sem idempotency_key" "422" "empresa_upsert" "$DESC_DIR/corpo-11.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "idempotency_key_ausente" ] \
        && ok "recusa nomeia idempotency_key_ausente" || falhou "codigo de recusa errado (chave)"
    corpo "corpo-12.json" <<'JSON'
{"idempotency_key": "curta!!", "parametros": {"valores": {"name": "Chave torta", "tf_domain": "x.example"}}}
JSON
    api_post "idempotency_key fora do formato" "422" "empresa_upsert" "$DESC_DIR/corpo-12.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "idempotency_key_invalida" ] \
        && ok "recusa nomeia idempotency_invalida" || falhou "codigo de recusa errado (formato da chave)"
    corpo "corpo-13.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0013",
 "parametros": {"valores": {"name": "Campo fora", "tf_company_id": "$UUID_HTTP",
                            "email": "nao-declarado@example.com"}}}
JSON
    api_post "campo fora da declaracao" "422" "empresa_upsert" "$DESC_DIR/corpo-13.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "campo_nao_declarado" ] \
        && ok "recusa nomeia campo_nao_declarado" || falhou "codigo de recusa errado (campo)"

    # ---- contagem final: uma empresa por identidade, nenhuma duplicata -----------------------
    # (a guarda de ambiente da escrita e' medida no passo 3d, com o parametro trocado pelo ORM
    #  e o servidor NOVO — mudar o valor por SQL direto com o servidor de pe nao invalida o cache
    #  do ir.config_parameter e mediria verde falso.)
    TOTAL_UUID="$(psql_bd "$BANCO" "select count(*) from res_partner where tf_company_id = '$UUID_HTTP'")"
    TOTAL_CNPJ="$(psql_bd "$BANCO" "select count(*) from res_partner where tf_cnpj = '$CNPJ_HTTP'")"
    [ "$TOTAL_UUID" = "1" ] && ok "uma unica empresa com o UUID canonico apos 5 chamadas ($TOTAL_UUID)" \
        || falhou "duplicata por UUID canonico: $TOTAL_UUID registros"
    [ "$TOTAL_CNPJ" = "1" ] && ok "uma unica empresa com o CNPJ apos criar por 2 caminhos ($TOTAL_CNPJ)" \
        || falhou "duplicata por CNPJ: $TOTAL_CNPJ registros"
    NOME_FINAL="$(psql_bd "$BANCO" "select name from res_partner where tf_company_id = '$UUID_HTTP'")"
    # O nome final vem da chamada por CNPJ (corpo-4), que casou ESTE MESMO registro: a identidade
    # pelo forte agiu sobre a empresa do canonico, e a ultima escrita venceu.
    [ "$NOME_FINAL" = "Empresa por CNPJ" ] && ok "a ultima escrita venceu no nome ($NOME_FINAL)" \
        || falhou "nome final inesperado: '$NOME_FINAL'"

    cabecalho "passo 3c — auditoria por chamada (AC7) e ausencia de segredo"
    LOG_API="$LOG_DIR/3b-servidor.log"
    docker logs "$API_CT" >"$LOG_API" 2>&1
    LINHAS_AUDITORIA="$(grep -c 'TF_API_AUDIT' "$LOG_API" || true)"
    if [ "$LINHAS_AUDITORIA" = "$AUDITADAS" ]; then
        ok "uma linha TF_API_AUDIT por chamada autenticada ($LINHAS_AUDITORIA linhas, $AUDITADAS chamadas)"
    else
        falhou "auditoria: $LINHAS_AUDITORIA linhas para $AUDITADAS chamadas autenticadas"
    fi
    grep -q '"operacao": "empresa_upsert"' "$LOG_API" && ok "trilha registra a operacao de escrita" \
        || falhou "trilha sem a operacao empresa_upsert"
    grep -q '"acao": "upsert"' "$LOG_API" && ok "trilha registra a acao upsert" \
        || falhou "trilha sem a acao upsert"
    grep -q '"codigo": "valor_ambiguo"' "$LOG_API" && ok "trilha registra a recusa por ambiguidade" \
        || falhou "trilha sem a recusa por ambiguidade"
    grep -q '"codigo": "campo_fixo_divergente"' "$LOG_API" && ok "trilha registra a recusa do valor fixo" \
        || falhou "trilha sem a recusa do valor fixo"
    if grep -q -F -f "$DESC_DIR/chave.txt" "$LOG_API" 2>/dev/null; then
        falhou "a chave de API aparece no log do servidor"
    else
        ok "a chave de API NAO aparece no log do servidor"
    fi
    if grep -q 'Bearer' "$LOG_API" 2>/dev/null; then
        falhou "a palavra Bearer aparece no log (indicio de token logado)"
    else
        ok "nenhum token/Bearer no log do servidor"
    fi
    if grep -q 'Empresa HTTP\|Ambiguo CNPJ\|nao-declarado@example.com' "$LOG_API" 2>/dev/null; then
        falhou "payload de negocio aparece na trilha de auditoria"
    else
        ok "payload nao entra na trilha de auditoria"
    fi
    docker rm -f "$API_CT" >/dev/null 2>&1
    API_CT=""
    ok "servidor de API encerrado (container descartavel removido)"

    # ------------------------------------------------------------------
    # passo 3d — guarda de ambiente na escrita (ADR-005), medida por HTTP
    # ------------------------------------------------------------------
    cabecalho "passo 3d — guarda de ambiente na escrita (ADR-005)"
    # O parametro e' lido pela API a cada chamada, mas o ORM o CACHEIA: trocar o valor por SQL
    # direto no banco com o servidor de pe nao invalida esse cache (mediria verde falso). Aqui a
    # troca passa pelo MESMO caminho do preparo (ORM + commit) e o servidor sobe DEPOIS dela.
    preparar_api "$LOG_DIR/3d-preparo-homolog.log" homologacao chave-homolog.txt
    grep -q 'TF_API_PREPARO_OK' "$LOG_DIR/3d-preparo-homolog.log" \
        && ok "ambiente do banco descartavel movido para 'homologacao' pelo ORM (com commit)" \
        || falhou "preparo em homologacao nao confirmou (log: $LOG_DIR/3d-preparo-homolog.log)"
    subir_servidor
    CFG_HOMOLOG="$DESC_DIR/curl-homolog.cfg"
    escrever_cfg_curl "$CFG_HOMOLOG" "$DESC_DIR/chave-homolog.txt"
    corpo "corpo-14.json" <<JSON
{"idempotency_key": "tre-e01-t02-http-0014",
 "parametros": {"valores": {"name": "Empresa em homologacao", "tf_company_id": "$UUID_HTTP"}}}
JSON
    ANTES_3D="$(psql_bd "$BANCO" "select count(*) from res_partner")"
    CODIGO_3D="$(curl --config "$CFG_HOMOLOG" -X POST -d @"$DESC_DIR/corpo-14.json" \
        -o "$DESC_DIR/resposta-3d.json" -w '%{http_code}' "$BASE/tf/api/v1/empresa_upsert" || true)"
    [ "$CODIGO_3D" = "503" ] && ok "escrita fora do ambiente permitido -> HTTP 503" \
        || falhou "escrita em homologacao -> HTTP $CODIGO_3D (esperado 503)"
    [ "$(campo_json "$DESC_DIR/resposta-3d.json" "d['codigo']")" = "ambiente_nao_permitido" ] \
        && ok "recusa nomeia ambiente_nao_permitido (fail-closed do ADR-005)" \
        || falhou "codigo de recusa errado (ambiente)"
    DEPOIS_3D="$(psql_bd "$BANCO" "select count(*) from res_partner")"
    [ "$ANTES_3D" = "$DEPOIS_3D" ] && ok "ambiente fora da politica NAO escreveu ($DEPOIS_3D registros)" \
        || falhou "ambiente fora da politica escreveu ($ANTES_3D -> $DEPOIS_3D)"
    docker logs "$API_CT" >"$LOG_DIR/3d-servidor.log" 2>&1
    LINHAS_3D="$(grep -c 'TF_API_AUDIT' "$LOG_DIR/3d-servidor.log" || true)"
    [ "$LINHAS_3D" = "1" ] && ok "a recusa por ambiente tambem deixa UMA linha de trilha" \
        || falhou "trilha da fase 3d: $LINHAS_3D linha(s) (esperado 1)"
    grep -q '"codigo": "ambiente_nao_permitido"' "$LOG_DIR/3d-servidor.log" \
        && ok "trilha da fase 3d nomeia o codigo da recusa" || falhou "trilha da 3d sem o codigo"
    docker rm -f "$API_CT" >/dev/null 2>&1
    API_CT=""
    ok "servidor da fase 3d encerrado (container descartavel removido)"
fi

# ---------------------------------------------------------------------------
# passo 4 — contrato do modulo (AC1/AC2 por leitura do artefato)
# ---------------------------------------------------------------------------
if [ "$MODO" = "completo" ]; then
    cabecalho "passo 4 — contrato do modulo (greps e leitura da politica)"
    CONTROLLER="$MODULO_DIR/controllers/api_controlada.py"
    N_ROTAS="$(grep -c '@http.route(' "$CONTROLLER" 2>/dev/null || true)"
    [ "$N_ROTAS" = "1" ] && ok "o controlador tem UMA unica rota (a operacao de negocio nao abriu rota nova)" \
        || falhou "o controlador tem $N_ROTAS rotas (esperado 1)"
    grep -q 'auth="bearer"' "$CONTROLLER" && ok "rota com auth='bearer' (chave de API do Odoo)" \
        || falhou "rota sem auth='bearer'"
    grep -q 'methods=\["POST"\]' "$CONTROLLER" && ok "rota declarada so' para POST" \
        || falhou "rota sem methods=['POST']"
    SQL_API="$(grep -rnE '(^|[^a-zA-Z_])cr\.execute|sql\.SQL|_cr\.execute' \
        "$MODULO_DIR/api" "$MODULO_DIR/controllers" 2>/dev/null | wc -l | tr -d ' ')"
    [ "$SQL_API" = "0" ] && ok "nenhum SQL nos arquivos da API (AC2: so' ORM)" \
        || falhou "$SQL_API ocorrencia(s) de SQL nos arquivos da API"
    if grep -q 'import odoo\|from odoo' "$MODULO_DIR/api/motor.py"; then
        falhou "o motor importa odoo (deveria ser puro)"
    else
        ok "o motor e' puro (sem import de odoo) — decisao exercitavel fora do Odoo"
    fi
    RESUMO_OPERACAO="$(python3 - "$MODULO_DIR/api/politica_api.json" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1], encoding="utf-8"))
op = {o["nome"]: o for o in dados["operacoes"]}.get("empresa_upsert")
if not op:
    print("AUSENTE")
    raise SystemExit(0)
decl = op["modelos"]["res.partner"]
print("tipo=%s idempotency=%s identidades=%s fixos=%s ambientes=%s" % (
    op.get("tipo"), op.get("requer_idempotency_key"),
    ",".join(decl.get("campos_de_identidade", [])),
    json.dumps(decl.get("valores_fixos", {}), sort_keys=True),
    ",".join(dados.get("ambientes_permitidos", []))))
PY
)"
    case "$RESUMO_OPERACAO" in
        "tipo=escrita idempotency=True identidades=tf_company_id,tf_cnpj,tf_domain,tf_linkedin_url"*"fixos={\"is_company\": true} ambientes=dev")
            ok "politica declara a operacao com identidade ordenada, valor fixo e ambiente dev ($RESUMO_OPERACAO)" ;;
        *) falhou "declaracao da operacao inesperada: $RESUMO_OPERACAO" ;;
    esac
    # A ordem das identidades e' o contrato: canonico primeiro, depois CNPJ -> dominio -> LinkedIn.
    case "$RESUMO_OPERACAO" in
        *"identidades=tf_company_id,tf_cnpj,tf_domain,tf_linkedin_url"*)
            ok "ordem de identidade = canonico seguido dos fortes do contrato §5" ;;
        *) falhou "ordem de identidade fora do contrato: $RESUMO_OPERACAO" ;;
    esac
fi

# ---------------------------------------------------------------------------
# limpeza e prova de que o ambiente do dev nao foi tocado (AC8)
# ---------------------------------------------------------------------------
cabecalho "limpeza e ambientes"
if [ "$MANTER_BANCO" = "1" ]; then
    info "TRE_MANTER_BANCO=1: banco $BANCO mantido para inspecao"
else
    banco_limpo "$BANCO"
    if banco_existe "$BANCO"; then falhou "banco descartavel $BANCO nao foi removido"
    else ok "banco descartavel $BANCO removido"; fi
fi
docker rm -f "$PG_TMP" >/dev/null 2>&1
if [ -z "$(docker ps -q --filter "name=^$PG_TMP\$")" ]; then ok "postgres descartavel $PG_TMP removido"
else falhou "postgres descartavel $PG_TMP continua de pe"; fi
docker network rm "$NET_TMP" >/dev/null 2>&1
if [ -z "$(docker network ls -q --filter "name=^$NET_TMP\$")" ]; then ok "rede descartavel $NET_TMP removida"
else falhou "rede descartavel $NET_TMP continua"; fi
if [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR" && [ ! -d "$DESC_DIR" ]; then
    DESC_DIR=""
    ok "diretorio de configuracao descartavel removido (chave gerada na hora, nunca em log)"
else
    falhou "diretorio descartavel nao foi removido"
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
