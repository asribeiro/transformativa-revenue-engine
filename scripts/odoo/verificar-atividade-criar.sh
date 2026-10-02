#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E01-T05 — operacao de escrita de negocio `atividade_criar`
# (`POST /tf/api/v1/atividade_criar`) no modulo transformativa_sales_ai.
#
# Criterios de aceitacao (definidos no inicio do card, registrados no card e no runbook
# docs/runbooks/odoo-atividade-criar.md):
#   AC1 operacao declarada e versionada (a politica real declara `atividade_criar` como escrita, com
#       `requer_idempotency_key: true`; servida pela MESMA porta unica — 1 rota);
#   AC2 superficie fechada e ANCORA DECLARADA: `res_model` e' valor FIXO da politica (o chamador NAO
#       escolhe o modelo-alvo; divergencia -> 422 `campo_fixo_divergente`, inclusive a tentativa de
#       ancorar pelo id interno `res_model_id`) e `res_id` e' obrigatorio (sem ele -> 422
#       `campo_obrigatorio_ausente`); campo fora da declaracao -> 422 `campo_nao_declarado`;
#   AC3 a atividade NASCE no modelo declarado, medido no BANCO (`mail_activity.res_model` /
#       `res_id`), com o resumo e o prazo que o chamador mandou — e o id devolvido e' o do registro;
#   AC4 contrato de integracao (`idempotency_key` exigida/validada; `dry_run` descreve e NAO cria;
#       `correlation_id` ecoado; o rastro `tf_idempotency_key`/`tf_correlation_id` REGISTRADO na
#       atividade criada, alem da trilha TF_API_AUDIT);
#   AC5 guarda de ambiente (ADR-005) NA ESCRITA: fora do ambiente permitido -> 503 e nada criado;
#       ambiente nao declarado -> 503 (API inerte);
#   AC6 rastro: UMA linha `TF_API_AUDIT` por chamada autenticada (inclusive as recusas), sem token e
#       SEM PAYLOAD (o resumo da atividade nao entra na trilha);
#   AC7 ACL: a criacao passa pelas ACLs do usuario dono da chave (o `mail.activity._check_access`
#       exige acesso de ESCRITA ao documento ancorado) — sem acesso, recusa nomeada 403, nunca
#       criacao "por baixo" (o controlador nao faz `sudo()`);
#   AC8 ambiente de execucao intocado (dupla descartavel propria; dev/homolog/prod medidos antes e
#       depois; nenhum DDL, migration ou escrita em sales_intelligence);
#   AC9 escopo declarado e LACUNAS MEDIDAS: a operacao de criacao nao declara identidade (quem
#       garante nao duplicar e' a dedup por chave, card E02-T02 — e o REPLAY da mesma chave ainda
#       cria uma segunda atividade, medido aqui, nao silenciado); `crm.lead` como ancora NAO e'
#       servido (ancora fixa em `res.partner`) — recusa nomeada e contagem no banco provando que
#       nada nasceu no modelo nao declarado; `note`/`stage_id`/`user_id` de outro contexto e demais
#       campos fora da declaracao sao recusa nomeada.
#
# TEST PLAN (executado por este script, NA VPS do dev):
#   passo 0  suite PURA do motor (sem Odoo)              -> scripts/odoo/testar_motor_api.py
#   passo 1  instalacao do modulo em banco limpo         -> log + estado lido no banco
#   passo 2  suite do Odoo (--test-enable)               -> relatorio do runner + nome dos testes
#   passo 3  preparo + servidor HTTP real + curl         -> criacao de verdade pela ancora declarada,
#                                                          recusas nomeadas, dry-run, ACL e contagem
#   passo 3b medicao no banco (SQL de leitura)           -> a atividade nasceu em res.partner/res_id
#   passo 3c auditoria no log do servidor                -> 1 linha por chamada autenticada
#   passo 3d guarda de ambiente na escrita               -> 503 fora da politica, sem escrita
#   passo 4  contrato do modulo (greps)                  -> 1 rota, bearer, POST, 0 SQL
#   limpeza  banco/dupla/rede removidos; dev, homolog e producao conferidos intactos
#
# ISOLAMENTO (mesma licao do E03/E01-T01/E01-T02/E01-T03/E01-T04): o Odoo do dev abre sessao em
# QUALQUER banco novo da instancia `pg-odoo-dev`, entao este verificador sobe a SUA propria dupla
# descartavel (postgres:16 + odoo:19.0, as mesmas imagens do par de dev) em rede propria, e nao usa
# `pg-odoo-dev`, `odoo_dev` nem a copia operacional /opt/tre/repo.
#
# Uso (NA VPS, a partir de arquivo, como root — a dupla descartavel exige chown para o uid do
# container):
#   bash verificar-atividade-criar.sh
#   bash verificar-atividade-criar.sh --apenas-motor
#   bash verificar-atividade-criar.sh --apenas-suites        (instalacao + teste do Odoo)
#   bash verificar-atividade-criar.sh --apenas-http         (instalacao + servidor + curl)
#   bash verificar-atividade-criar.sh --prova-de-dente      (4 mutacoes; cada uma TEM de reprovar)
#   bash verificar-atividade-criar.sh --banco tre_outro --modulo-dir /caminho/do/modulo
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
BANCO="${TRE_BANCO:-tre_e01_t05_atividade}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-atividade-criar}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"
# Piso de testes do modulo, medido na rodada do aceite deste card (a ultima linha do log do runner
# e' a medicao; o numero aqui e' o medido, nao uma estimativa). Menos que o piso = regressao (teste
# que nao roda nao passou).
PISO_DE_TESTES="${TRE_PISO_DE_TESTES:-147}"

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
PG_TMP="e01t05-pg-$SUFIXO"
API_TMP="e01t05-api-$SUFIXO"
NET_TMP="e01t05-net-$SUFIXO"
DESC_DIR=""
API_CT=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: ATIVIDADE_CRIAR_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: ATIVIDADE_CRIAR_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? QUATRO mutacoes, cada uma em copia propria do modulo.
#   dente 1: politica SEM a operacao `atividade_criar`      -> o item "declarada como escrita" reprova;
#   dente 2: politica SEM o valor fixo da ANCORA            -> o item "ancora divergente -> 422
#                                                             campo_fixo_divergente" reprova (o
#                                                             chamador passaria a escolher o modelo);
#   dente 3: controlador SEM o ramo de criacao              -> o item "a criacao devolveu o id da
#                                                             atividade" reprova;
#   dente 4: modulo SEM a traducao da ancora                -> o item "a atividade nasceu ancorada no
#                                                             modelo DECLARADO, medido no banco" reprova.
#
# NOTA MEDIDA (o que separa este card do E01-T03/E01-T04): a operacao desta leva tem um pedaco de
# codigo PROPRIO — a traducao da ancora em `models/mail_activity.py` (o Odoo 19 nao escreve
# `res_model` direto: o campo e' related-readonly e o ORM descarta o valor em silencio). Por isso o
# dente 4 muta o MODULO, e nao so' politica/controlador: prova negativa que nao cobre o codigo novo
# mede so' o que os cards anteriores ja' tinham.
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e01t05-XXXXXX)"
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
    # medido nada — defeito medido pelo tester no aceite do E01-T01 (card `t_fa9db205`).
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
        # diagnostico). Por isso a avaliacao aceita VARIOS marcadores e vale pelo primeiro que casar.
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
        "atividade_criar declarada como escrita com idempotency_key exigida" \
        "atividade_criar nao declarada como escrita com chave" \
        "ancora divergente (crm.lead) -> HTTP 422 campo_fixo_divergente" \
        "codigo de recusa errado (ancora divergente)" \
        "a criacao devolveu o id da atividade" \
        "a criacao nao devolveu id de atividade" \
        "a atividade nasceu ancorada no modelo DECLARADO, medido no banco" \
        "ancora gravada diferente"; do
        MARC_USOS="$(grep -cF "$marcador_dente" "$0")"
        [ "$MARC_USOS" -ge 2 ] \
            && ok "marcador de dente e' texto de item do verificador ($MARC_USOS usos): $marcador_dente" \
            || falhou "marcador de dente so aparece na propria lista ($MARC_USOS uso(s)): $marcador_dente"
    done

    cabecalho "prova de dente 1: politica sem a operacao atividade_criar (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    MUT1="$(python3 - "$DENTE_DIR/m1/api/politica_api.json" <<'PY'
import json, sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    dados = json.load(fh)
antes = [op["nome"] for op in dados["operacoes"]]
dados["operacoes"] = [op for op in dados["operacoes"] if op["nome"] != "atividade_criar"]
with open(caminho, "w", encoding="utf-8") as fh:
    json.dump(dados, fh, ensure_ascii=False, indent=2)
# A conferencia da mutacao e' por OPERACAO, nao por texto: a palavra `atividade_criar` continua
# aparecendo na `descricao` da politica, entao um `grep` pela palavra acusaria "mutacao nao
# aplicada" com a mutacao aplicada (defeito medido na rodada 1 dos dentes do E01-T02).
with open(caminho, encoding="utf-8") as fh:
    depois = [op["nome"] for op in json.load(fh)["operacoes"]]
if "atividade_criar" in depois:
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
        "atividade_criar declarada como escrita com idempotency_key exigida" \
        "atividade_criar nao declarada como escrita com chave"

    cabecalho "prova de dente 2: politica SEM o valor fixo da ancora (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    MUT2="$(python3 - "$DENTE_DIR/m2/api/politica_api.json" <<'PY'
import json, sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    dados = json.load(fh)
antes = None
for op in dados["operacoes"]:
    if op["nome"] == "atividade_criar":
        declaracao = op["modelos"]["mail.activity"]
        antes = declaracao.get("valores_fixos")
        declaracao["valores_fixos"] = {}
with open(caminho, "w", encoding="utf-8") as fh:
    json.dump(dados, fh, ensure_ascii=False, indent=2)
with open(caminho, encoding="utf-8") as fh:
    depois = None
    for op in json.load(fh)["operacoes"]:
        if op["nome"] == "atividade_criar":
            depois = op["modelos"]["mail.activity"].get("valores_fixos")
# A conferencia e' sobre o VALOR declarado (nao sobre a palavra): valores_fixos vazio e' a mutacao.
if depois:
    print("MUTACAO_NAO_APLICADA (valores_fixos ainda: %r)" % (depois,))
    raise SystemExit(3)
print("MUTACAO_APLICADA (valores_fixos: %r -> %r)" % (antes, depois))
PY
)"
    if [ "${MUT2#MUTACAO_APLICADA}" != "$MUT2" ]; then
        echo "OK    dente 2: mutacao aplicada — $MUT2"
    else
        echo "FALHOU dente 2: mutacao NAO foi aplicada na copia ($MUT2)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_d2" TRE_LOG_DIR="$LOG_DIR/dente2" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D2" >"$LOG_DIR/dente-2-sem-valor-fixo.out"
    printf '%s\n' "$D2" | tail -3
    avaliar_dente "dente 2 (politica sem o valor fixo da ancora)" "$LOG_DIR/dente-2-sem-valor-fixo.out" \
        "ancora divergente (crm.lead) -> HTTP 422 campo_fixo_divergente" \
        "codigo de recusa errado (ancora divergente)"

    cabecalho "prova de dente 3: controlador SEM o ramo de criacao (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m3"
    MUT3="$(python3 - "$DENTE_DIR/m3/controllers/api_controlada.py" <<'PY'
import sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    texto = fh.read()
antes = texto
# O alvo e' o ramo de CRIACAO de `_executar_escrita` — o pedaco que vem DEPOIS do ramo de upsert
# (o mesmo `criado = modelo.create(valores)` aparece tambem no upsert; mutar "o primeiro" mutaria
# o ramo errado e o dente mediria outra coisa). Por isso a ancora e' o fim do ramo de upsert.
marcador = "\"ids\": existentes.ids}"
if marcador not in texto:
    print("MUTACAO_NAO_APLICADA (ancora do ramo de criacao ausente no controlador)")
    sys.exit(3)
corte = texto.index(marcador)
cabeca, cauda = texto[:corte], texto[corte:]
if "criado = modelo.create(valores)" not in cauda:
    print("MUTACAO_NAO_APLICADA (ramo de criacao nao encontrado depois do upsert)")
    sys.exit(3)
cauda = cauda.replace("criado = modelo.create(valores)",
                      "criado = modelo.browse()  # mutacao: ramo de criacao desligado", 1)
texto = cabeca + cauda
if texto == antes:
    print("MUTACAO_NAO_APLICADA")
    sys.exit(3)
with open(caminho, "w", encoding="utf-8") as fh:
    fh.write(texto)
print("MUTACAO_APLICADA")
PY
)"
    if [ "$MUT3" = "MUTACAO_APLICADA" ]; then
        echo 'OK    dente 3: mutacao aplicada na copia do controlador (ramo de criacao)'
    else
        echo "FALHOU dente 3: mutacao NAO foi aplicada na copia do controlador ($MUT3)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D3="$(TRE_MODULO_DIR="$DENTE_DIR/m3" TRE_BANCO="${BANCO}_d3" TRE_LOG_DIR="$LOG_DIR/dente3" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D3" >"$LOG_DIR/dente-3-sem-ramo-de-criacao.out"
    printf '%s\n' "$D3" | tail -3
    avaliar_dente "dente 3 (controlador sem o ramo de criacao)" "$LOG_DIR/dente-3-sem-ramo-de-criacao.out" \
        "a criacao devolveu o id da atividade" \
        "a criacao nao devolveu id de atividade"

    cabecalho "prova de dente 4: modulo SEM a traducao da ancora (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m4"
    MUT4="$(python3 - "$DENTE_DIR/m4/models/mail_activity.py" <<'PY'
import sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    texto = fh.read()
antes = texto
texto = texto.replace('        vals["res_model_id"] = modelo.id',
                      '        return  # mutacao: ancora por NOME nao traduzida para res_model_id', 1)
if texto == antes:
    print("MUTACAO_NAO_APLICADA")
    sys.exit(3)
with open(caminho, "w", encoding="utf-8") as fh:
    fh.write(texto)
print("MUTACAO_APLICADA")
PY
)"
    if [ "$MUT4" = "MUTACAO_APLICADA" ]; then
        echo 'OK    dente 4: mutacao aplicada na copia do modulo (traducao da ancora)'
    else
        echo "FALHOU dente 4: mutacao NAO foi aplicada na copia do modulo ($MUT4)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D4="$(TRE_MODULO_DIR="$DENTE_DIR/m4" TRE_BANCO="${BANCO}_d4" TRE_LOG_DIR="$LOG_DIR/dente4" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D4" >"$LOG_DIR/dente-4-sem-traducao-de-ancora.out"
    printf '%s\n' "$D4" | tail -3
    avaliar_dente "dente 4 (modulo sem a traducao da ancora)" "$LOG_DIR/dente-4-sem-traducao-de-ancora.out" \
        "a atividade nasceu ancorada no modelo DECLARADO, medido no banco" \
        "ancora gravada diferente"

    # Controle do PROPRIO harness de dente: se ele contar como dente um sub-run que reprovou por
    # ambiente (comando barrado antes de medir) ou que nao reprovou o item alvo, a prova de dente
    # inteira e' decorativa. Os dois controles abaixo sao sinteticos de proposito (o alvo e' a
    # FUNCAO de avaliacao, nao o modulo).
    cabecalho "controle do harness de dente (nao pode ser fail-open)"
    printf 'FALHOU imagem odoo:19.0 ausente (nada a medir)\nRESULTADO: ATIVIDADE_CRIAR_FALHOU (3 itens, 1 falha(s))\n' \
        >"$DENTE_DIR/controle-cego.out"
    ANTES="$DENTE_FALHAS"
    avaliar_dente "controle-cego (ambiente quebrado)" "$DENTE_DIR/controle-cego.out" "item qualquer" >/dev/null
    if [ "$DENTE_FALHAS" -gt "$ANTES" ]; then
        echo 'OK    controle: sub-run que reprova por AMBIENTE nao conta como dente (nao e fail-open)'
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
        echo "RESULTADO: ATIVIDADE_CRIAR_DENTE_OK (4 provas + 2 controles do proprio harness, 0 falhas) modulo=$MODULO"
        exit 0
    fi
    echo "RESULTADO: ATIVIDADE_CRIAR_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente) modulo=$MODULO"
    exit 1
fi

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
               models/mail_activity.py tests/test_atividade_criar.py; do
    if [ -f "$MODULO_DIR/$arquivo" ]; then
        ok "modulo em disco: $arquivo"
    else
        falhou "modulo ausente em $MODULO_DIR/$arquivo"; resumo
    fi
done
info "sha256 dos arquivos sob teste:"
for arquivo in api/politica_api.json api/motor.py controllers/api_controlada.py \
               models/mail_activity.py tests/test_atividade_criar.py; do
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
DESC_DIR="$(mktemp -d /tmp/verificacao-atividade-XXXXXX)"
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
    # do provisionamento do dev e dos aceites do E01-T01/E01-T02).
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
preparar_api() { # $1=log $2=ambiente [$3=arquivo da chave] [$4..=env EXTRA=valor]
    local log="$1"; shift
    local ambiente="${1:-dev}"; shift
    local arquivo="${1:-chave.txt}"; shift || true
    # `TRE_API_GRUPOS`/`TRE_API_USUARIO` extras existem para a prova de ACL do E01-T05 (uma chave de
    # usuario SEM escrita no documento ancorado); sem eles o comportamento e' o dos cards anteriores.
    local extras=()
    local par
    for par in "$@"; do extras+=(-e "$par"); done
    docker run --rm -i --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -v "$DESC_DIR":/preparo \
        -e "TRE_API_AMBIENTE=$ambiente" \
        -e "TRE_API_ARQUIVO_CHAVE=/preparo/$arquivo" \
        "${extras[@]}" \
        --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$PREPARADOR" >"$log" 2>&1
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
# passo 2 — suite do Odoo (--test-enable): a suite da criacao de atividade + as anteriores
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
    if [ -f "$MODULO_DIR/tests/test_atividade_criar.py" ]; then
        while read -r metodo; do
            [ -z "$metodo" ] && continue
            TOTAL_SUITE=$((TOTAL_SUITE + 1))
            grep -q "$metodo" "$LOG_ATUAL" || { FALTANDO=$((FALTANDO + 1)); info "teste ausente no log: $metodo"; }
        done <<<"$(grep -oE 'def (test_[0-9]+_[a-z_0-9]+)' "$MODULO_DIR/tests/test_atividade_criar.py" \
            | sed 's/^def //' | sort -u)"
    fi
    if [ "$TOTAL_SUITE" -ge 15 ] && [ "$FALTANDO" = "0" ]; then
        ok "todos os $TOTAL_SUITE testes da suite da criacao de atividade aparecem no log do runner"
    else
        falhou "suite da criacao de atividade: $TOTAL_SUITE testes encontrados, $FALTANDO ausentes no log"
    fi
    ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
    [ "$ESTADO" = "installed" ] && ok "modulo segue installed depois da suite" \
        || falhou "estado depois da suite: '$ESTADO'"
fi

# ---------------------------------------------------------------------------
# passo 3 — HTTP externo de verdade: preparo + servidor + curl (AC2/AC3/AC4/AC5/AC6/AC7/AC9)
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
    USUARIO_INTEGRACAO="$(sed -nE 's/.*TF_API_PREPARO_OK usuario=([^ ]+).*/\1/p' "$LOG_ATUAL" | tail -1)"
    [ "$USUARIO_INTEGRACAO" = "tf_api_integracao" ] \
        && ok "usuario de integracao do preparo medido no log (login $USUARIO_INTEGRACAO)" \
        || falhou "usuario de integracao inesperado no preparo ('$USUARIO_INTEGRACAO')"
    # AC7: uma SEGUNDA chave, de um usuario com LEITURA em res.partner (`base.group_user`) e SEM os
    # grupos de vendas (que sao quem da' ESCRITA) — sem ela, "a API respeita a ACL" seria afirmacao
    # sem medicao.
    LOG_SEM_ESCRITA="$LOG_DIR/3-preparo-sem-escrita.log"
    preparar_api "$LOG_SEM_ESCRITA" dev chave-sem-escrita.txt \
        "TRE_API_USUARIO=tf_api_sem_escrita" "TRE_API_GRUPOS=base.group_user,$MODULO.group_tf_sales_ai_user"
    grep -q 'TF_API_PREPARO_OK usuario=tf_api_sem_escrita' "$LOG_SEM_ESCRITA" \
        && ok "preparo da chave SEM escrita no documento ancorado (AC7)" \
        || falhou "preparo da chave sem escrita nao confirmou (log: $LOG_SEM_ESCRITA)"

    subir_servidor
    if [ "$FALHAS" -gt 0 ]; then resumo; fi

    # curl por arquivo de configuracao (600): o token NUNCA entra em argumento de comando
    CFG="$DESC_DIR/curl.cfg"
    escrever_cfg_curl "$CFG" "$DESC_DIR/chave.txt"
    # Chave do usuario SEM escrita no documento ancorado (AC7): `base.group_user` da' LEITURA em
    # `res.partner`; quem da' ESCRITA sao os grupos de vendas — que este usuario nao tem.
    CFG_SEM_ESCRITA="$DESC_DIR/curl-sem-escrita.cfg"
    escrever_cfg_curl "$CFG_SEM_ESCRITA" "$DESC_DIR/chave-sem-escrita.txt"
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
    api_post() { # $1=rotulo $2=codigo_esperado $3=operacao $4=arquivo [$5=1|0 audita] [$6=cfg]
        local rotulo="$1" esperado="$2" operacao="$3" arquivo="$4" audita="${5:-1}" cfg="${6:-$CFG}"
        local codigo
        codigo="$(curl --config "$cfg" -X POST -d @"$arquivo" -o "$DESC_DIR/resposta.json" \
            -w '%{http_code}' "$BASE/tf/api/v1/$operacao" || true)"
        CHAMADAS=$((CHAMADAS + 1))
        if [ "$audita" = "1" ]; then
            # So' conta como auditaria a chamada que CHEGA ao controlador: 401 do `auth='bearer'`
            # morre antes da rota e nao gera trilha — somar essa linha faria o item de auditoria
            # medir um numero que nao existe (licao do E01-T01).
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
    # `2>/dev/null`: quando o campo nao existe no envelope, o `eval` estoura com KeyError e o
    # traceback do python entrava no log de evidencia no meio do relatorio de falha; o valor vazio
    # ja' aparece na mensagem do proprio `falhou`.
    campo_json() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))" \
        "$1" "$2" 2>/dev/null; }
    corpo() { # $1=arquivo; o conteudo vem do stdin
        cat >"$DESC_DIR/$1"
    }
    # Semeia o PARCEIRO-ANCORA direto no banco (SQL de ESCRITA aqui e' do fixture, nao da API) e
    # devolve o id. ARMADILHA MEDIDA nos cards anteriores e valida aqui: o `active` PRECISA vir
    # explicito — o default `True` e' do ORM, nao da coluna; sem ele a linha existe para o SQL e
    # fica INVISIVEL para o `search` do Odoo (e o fixture mediria outra coisa).
    # O id sai de um SELECT sobre a CTE: o `insert ... returning id` imprime a etiqueta do comando
    # ("INSERT 0 1") em STDOUT junto com o id, e o `-tA` nao a suprime (medido no E01-T03).
    semear_parceiro() { # $1=name $2=email
        psql_bd "$BANCO" "with novo as (
                              insert into res_partner (name, is_company, active, email, create_date, write_date)
                              values ('$1', false, true, '$2', now(), now())
                              returning id)
                          select id from novo" | tr -d ' \n'
    }
    # Id do primeiro tipo de atividade da base (a `mail` traz os tipos no `data/`, sem depender de
    # demo data). Sai de SELECT sobre subconsulta — mesma licao da etiqueta do `psql`.
    TIPO_ATIVIDADE="$(psql_bd "$BANCO" \
        "select id from (select id from mail_activity_type order by sequence, id limit 1) as t")"
    case "$TIPO_ATIVIDADE" in
        ''|*[!0-9]*) falhou "nao consegui ler um tipo de atividade da base ('$TIPO_ATIVIDADE')" ;;
        *) ok "base preparada com o tipo de atividade $TIPO_ATIVIDADE (fixture lido por SQL)" ;;
    esac
    PARCEIRO_ANCORA="$(semear_parceiro "Parceiro ancora HTTP do E01-T05" "ancora@atividade-t05.example")"
    case "$PARCEIRO_ANCORA" in ''|*[!0-9]*)
        falhou "nao consegui semear o parceiro-ancora (id devolvido: '$PARCEIRO_ANCORA')"; resumo ;;
    *)  ok "base preparada com o parceiro-ancora (id $PARCEIRO_ANCORA)";;
    esac

    # ---- AC1: a operacao esta declarada e servida pela mesma porta unica -------------------
    corpo "corpo-capacidades.json" <<'JSON'
{"correlation_id": "tre-e01-t05-http-capacidades"}
JSON
    api_post "operacao declarada (sistema_capacidades)" "200" "sistema_capacidades" \
        "$DESC_DIR/corpo-capacidades.json"
    OPERACAO_DECLARADA="$(python3 - "$ULTIMA_RESPOSTA" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1]))
ops = {op["nome"]: op for op in dados["dados"]["capacidades"]["operacoes"]}
op = ops.get("atividade_criar")
print("%s|%s|%s" % (bool(op), op and op["tipo"], op and op["requer_idempotency_key"]))
PY
)"
    [ "$OPERACAO_DECLARADA" = "True|escrita|True" ] \
        && ok "atividade_criar declarada como escrita com idempotency_key exigida ($OPERACAO_DECLARADA)" \
        || falhou "atividade_criar nao declarada como escrita com chave ($OPERACAO_DECLARADA)"
    VERSAO_POLITICA="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['versao'])" \
        "$MODULO_DIR/api/politica_api.json")"
    VERSAO_RESPOSTA="$(campo_json "$ULTIMA_RESPOSTA" "d['politica_versao']")"
    [ "$VERSAO_RESPOSTA" = "$VERSAO_POLITICA" ] \
        && ok "envelope traz a versao da politica em vigor ($VERSAO_RESPOSTA)" \
        || falhou "politica_versao '$VERSAO_RESPOSTA' difere da politica em vigor '$VERSAO_POLITICA'"

    # ---- AC6: sem token e com token invalido ------------------------------------------------
    SAIDA="$(curl -s -X POST -H 'Content-Type: application/json' -d '{}' -o "$DESC_DIR/resposta.json" \
        -w '%{http_code}' "$BASE/tf/api/v1/atividade_criar" || true)"
    [ "$SAIDA" = "401" ] && ok "sem token na rota -> HTTP 401" || falhou "sem token -> HTTP $SAIDA (esperado 401)"
    SAIDA="$(curl --config "$CFG_RUIM" -X POST -d '{}' -o "$DESC_DIR/resposta.json" \
        -w '%{http_code}' "$BASE/tf/api/v1/atividade_criar" || true)"
    [ "$SAIDA" = "401" ] && ok "token invalido -> HTTP 401 (sem trilha, como o sem-token)" \
        || falhou "token invalido -> HTTP $SAIDA (esperado 401)"

    # ---- AC3: a atividade NASCE ancorada no modelo declarado --------------------------------
    ATIVIDADES_ANTES="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    corpo "corpo-1.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0001", "correlation_id": "tre-e01-t05-http-1",
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "activity_type_id": $TIPO_ATIVIDADE,
                            "summary": "Ligar para o decisor da ancora",
                            "date_deadline": "2026-12-31",
                            "tf_idempotency_key": "tre-e01-t05-http-0001",
                            "tf_correlation_id": "tre-e01-t05-http-1"}}}
JSON
    api_post "primeira chamada: cria a atividade na ancora declarada" "200" "atividade_criar" \
        "$DESC_DIR/corpo-1.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "criar" ] \
        && ok "primeira chamada responde acao_efetiva=criar" || falhou "primeira chamada nao criou"
    ID_ATIVIDADE="$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")"
    case "$ID_ATIVIDADE" in
        ''|*[!0-9]*) falhou "a criacao nao devolveu id de atividade ('$ID_ATIVIDADE')" ;;
        *)  ok "a criacao devolveu o id da atividade ($ID_ATIVIDADE)" ;;
    esac
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['correlation_id']")" = "tre-e01-t05-http-1" ] \
        && ok "correlation_id do chamador ecoado" || falhou "correlation_id nao ecoado"
    # A medicao e' no BANCO, e pelo `res_model_id` (a traducao da ancora) — nao pelo eco da resposta.
    MODELO_GRAVADO="$(psql_bd "$BANCO" "select m.model from mail_activity a
        join ir_model m on m.id = a.res_model_id where a.id = ${ID_ATIVIDADE:-0}")"
    [ "$MODELO_GRAVADO" = "res.partner" ] \
        && ok "a atividade nasceu ancorada no modelo DECLARADO, medido no banco (ir_model.model=res.partner)" \
        || falhou "ancora gravada diferente: '$MODELO_GRAVADO' (esperado res.partner)"
    RES_ID_GRAVADO="$(psql_bd "$BANCO" "select res_id from mail_activity where id = ${ID_ATIVIDADE:-0}")"
    [ "$RES_ID_GRAVADO" = "$PARCEIRO_ANCORA" ] \
        && ok "res_id gravado e' o parceiro informado ($RES_ID_GRAVADO)" \
        || falhou "res_id gravado diferente: '$RES_ID_GRAVADO' (esperado $PARCEIRO_ANCORA)"
    RESUMO_GRAVADO="$(psql_bd "$BANCO" "select summary from mail_activity where id = ${ID_ATIVIDADE:-0}")"
    [ "$RESUMO_GRAVADO" = "Ligar para o decisor da ancora" ] \
        && ok "o resumo do chamador foi gravado na atividade" \
        || falhou "resumo gravado diferente: '$RESUMO_GRAVADO'"
    PRAZO_GRAVADO="$(psql_bd "$BANCO" "select to_char(date_deadline,'YYYY-MM-DD') from mail_activity
        where id = ${ID_ATIVIDADE:-0}")"
    [ "$PRAZO_GRAVADO" = "2026-12-31" ] \
        && ok "o prazo do chamador foi gravado na atividade ($PRAZO_GRAVADO)" \
        || falhou "prazo gravado diferente: '$PRAZO_GRAVADO'"
    TIPO_GRAVADO="$(psql_bd "$BANCO" "select activity_type_id from mail_activity where id = ${ID_ATIVIDADE:-0}")"
    [ "$TIPO_GRAVADO" = "$TIPO_ATIVIDADE" ] \
        && ok "o tipo de atividade informado foi gravado ($TIPO_GRAVADO)" \
        || falhou "tipo gravado diferente: '$TIPO_GRAVADO' (esperado $TIPO_ATIVIDADE)"
    RASTRO_GRAVADO="$(psql_bd "$BANCO" "select coalesce(tf_idempotency_key,'-') || '|' ||
        coalesce(tf_correlation_id,'-') from mail_activity where id = ${ID_ATIVIDADE:-0}")"
    [ "$RASTRO_GRAVADO" = "tre-e01-t05-http-0001|tre-e01-t05-http-1" ] \
        && ok "o rastro da chamada foi REGISTRADO na atividade (tf_idempotency_key/tf_correlation_id)" \
        || falhou "rastreio gravado diferente: '$RASTRO_GRAVADO'"
    DONO_GRAVADO="$(psql_bd "$BANCO" "select u.login from mail_activity a
        join res_users u on u.id = a.create_uid where a.id = ${ID_ATIVIDADE:-0}")"
    [ "$DONO_GRAVADO" = "$USUARIO_INTEGRACAO" ] \
        && ok "a criacao foi feita PELO DONO DA CHAVE, medido no banco (create_uid=$DONO_GRAVADO)" \
        || falhou "create_uid gravado diferente: '$DONO_GRAVADO' (esperado $USUARIO_INTEGRACAO)"

    # ---- AC2: a ANCORA nao vem do payload ---------------------------------------------------
    corpo "corpo-2.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0002",
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "res_model": "crm.lead",
                            "summary": "atividade no modelo errado"}}}
JSON
    api_post "ancora divergente (crm.lead) -> HTTP 422 campo_fixo_divergente" "422" "atividade_criar" \
        "$DESC_DIR/corpo-2.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "campo_fixo_divergente" ] \
        && ok "recusa nomeia campo_fixo_divergente (o chamador NAO decide o modelo-alvo)" \
        || falhou "codigo de recusa errado (ancora divergente)"
    ATIVIDADES_NO_LEAD="$(psql_bd "$BANCO" "select count(*) from mail_activity a
        join ir_model m on m.id = a.res_model_id where m.model = 'crm.lead'")"
    [ "$ATIVIDADES_NO_LEAD" = "0" ] \
        && ok "nenhuma atividade nasceu no modelo nao declarado (crm.lead=0, lacuna declarada)" \
        || falhou "nasceu atividade em modelo NAO declarado na politica ($ATIVIDADES_NO_LEAD)"
    corpo "corpo-3.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0003",
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "res_model_id": 1,
                            "summary": "ancora pelo id interno"}}}
JSON
    api_post "ancora pelo id interno (res_model_id) -> HTTP 422 campo_nao_declarado" "422" \
        "atividade_criar" "$DESC_DIR/corpo-3.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "campo_nao_declarado" ] \
        && ok "recusa nomeia campo_nao_declarado (o id interno da ancora nao e' porta)" \
        || falhou "codigo de recusa errado (res_model_id)"
    corpo "corpo-4.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0004",
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "note": "<p>texto livre</p>",
                            "summary": "campo fora da declaracao"}}}
JSON
    api_post "campo fora da declaracao (note) -> HTTP 422 campo_nao_declarado" "422" "atividade_criar" \
        "$DESC_DIR/corpo-4.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "campo_nao_declarado" ] \
        && ok "recusa nomeia campo_nao_declarado (fronteira declarada, nada ignorado em silencio)" \
        || falhou "codigo de recusa errado (campo fora)"
    corpo "corpo-5.json" <<'JSON'
{"idempotency_key": "tre-e01-t05-http-0005",
 "parametros": {"valores": {"summary": "atividade sem ancora"}}}
JSON
    api_post "atividade sem res_id -> HTTP 422 campo_obrigatorio_ausente" "422" "atividade_criar" \
        "$DESC_DIR/corpo-5.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "campo_obrigatorio_ausente" ] \
        && ok "recusa nomeia campo_obrigatorio_ausente (res_id e' obrigatorio)" \
        || falhou "codigo de recusa errado (res_id ausente)"
    corpo "corpo-6.json" <<JSON
{"parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "summary": "sem chave"}}}
JSON
    api_post "escrita sem idempotency_key -> HTTP 422 idempotency_key_ausente" "422" "atividade_criar" \
        "$DESC_DIR/corpo-6.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "idempotency_key_ausente" ] \
        && ok "recusa nomeia idempotency_key_ausente" || falhou "codigo de recusa errado (chave)"
    corpo "corpo-7.json" <<JSON
{"idempotency_key": "curta!!", "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA,
                                                          "summary": "chave torta"}}}
JSON
    api_post "idempotency_key fora do formato -> HTTP 422 idempotency_key_invalida" "422" "atividade_criar" \
        "$DESC_DIR/corpo-7.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "idempotency_key_invalida" ] \
        && ok "recusa nomeia idempotency_key_invalida" || falhou "codigo de recusa errado (formato da chave)"

    # ---- AC4: dry_run descreve e NAO cria --------------------------------------------------
    ANTES_DRY="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    corpo "corpo-8.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0008", "dry_run": true,
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "summary": "dry-run da atividade"}}}
JSON
    api_post "dry-run de atividade nova" "200" "atividade_criar" "$DESC_DIR/corpo-8.json"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "criar" ] \
        && ok "dry-run descreve acao_efetiva=criar" || falhou "dry-run nao descreveu a criacao"
    case "$(campo_json "$ULTIMA_RESPOSTA" "d['dados']['criaria']")" in
        *res_model*) ok "dry-run mostra o valor fixo declarado entre os campos que CRIARIA" ;;
        *) falhou "dry-run nao mostra o valor fixo declarado ($(campo_json "$ULTIMA_RESPOSTA" "d['dados']['criaria']"))" ;;
    esac
    DEPOIS_DRY="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    [ "$ANTES_DRY" = "$DEPOIS_DRY" ] && ok "dry-run NAO escreveu ($DEPOIS_DRY atividades)" \
        || falhou "dry-run escreveu ($ANTES_DRY -> $DEPOIS_DRY)"

    # ---- AC9: a LACUNA da dedup por chave, medida (nao silenciada) -------------------------
    ANTES_REPLAY="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    corpo "corpo-9.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0009", "correlation_id": "tre-e01-t05-replay",
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "summary": "replay da mesma chave"}}}
JSON
    api_post "replay: primeira chamada de uma MESMA chave" "200" "atividade_criar" "$DESC_DIR/corpo-9.json"
    api_post "replay: segunda chamada da MESMA chave" "200" "atividade_criar" "$DESC_DIR/corpo-9.json"
    DEPOIS_REPLAY="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    [ "$((DEPOIS_REPLAY - ANTES_REPLAY))" = "2" ] \
        && ok "o replay da MESMA chave cria uma SEGUNDA atividade (lacuna declarada do E02-T02, medida: $ANTES_REPLAY -> $DEPOIS_REPLAY)" \
        || falhou "o comportamento do replay mudou ($ANTES_REPLAY -> $DEPOIS_REPLAY): a dedup por chave existe agora — atualize a declaracao e este item"

    # ---- AC7: a criacao passa pela ACL do dono da chave ------------------------------------
    ANTES_ACL="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    corpo "corpo-10.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0010",
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "summary": "sem escrita no documento"}}}
JSON
    api_post "chave sem escrita no documento ancorado -> HTTP 403 acesso_negado" "403" "atividade_criar" \
        "$DESC_DIR/corpo-10.json" "1" "$CFG_SEM_ESCRITA"
    [ "$(campo_json "$ULTIMA_RESPOSTA" "d['codigo']")" = "acesso_negado" ] \
        && ok "recusa nomeia acesso_negado (a API nao faz sudo no dado: a ACL do dono da chave vale)" \
        || falhou "codigo de recusa errado (ACL)"
    DEPOIS_ACL="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    [ "$ANTES_ACL" = "$DEPOIS_ACL" ] \
        && ok "a recusa por ACL NAO criou atividade ($DEPOIS_ACL atividades)" \
        || falhou "a recusa por ACL criou atividade ($ANTES_ACL -> $DEPOIS_ACL)"
    # Contagem final: 1 (criacao) + 2 (replay) — as recusas e o dry-run nao deixaram registro.
    TOTAL_ATIVIDADES="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    [ "$TOTAL_ATIVIDADES" = "3" ] \
        && ok "nenhuma recusa deixou registro: 3 atividades no fim (1 criacao + 2 do replay)" \
        || falhou "contagem final inesperada: $TOTAL_ATIVIDADES atividade(s) (esperado 3)"

    cabecalho "passo 3c — auditoria por chamada (AC6) e ausencia de segredo/dado pessoal"
    LOG_API="$LOG_DIR/3b-servidor.log"
    docker logs "$API_CT" >"$LOG_API" 2>&1
    LINHAS_AUDITORIA="$(grep -c 'TF_API_AUDIT' "$LOG_API" || true)"
    if [ "$LINHAS_AUDITORIA" = "$AUDITADAS" ]; then
        ok "uma linha TF_API_AUDIT por chamada autenticada ($LINHAS_AUDITORIA linhas, $AUDITADAS chamadas)"
    else
        falhou "auditoria: $LINHAS_AUDITORIA linhas para $AUDITADAS chamadas autenticadas"
    fi
    grep -q '"operacao": "atividade_criar"' "$LOG_API" && ok "trilha registra a operacao de escrita" \
        || falhou "trilha sem a operacao atividade_criar"
    grep -q '"acao": "criar"' "$LOG_API" && ok "trilha registra a acao criar" \
        || falhou "trilha sem a acao criar"
    grep -q '"modelo": "mail.activity"' "$LOG_API" && ok "trilha registra o modelo da escrita" \
        || falhou "trilha sem o modelo mail.activity"
    grep -q '"codigo": "campo_fixo_divergente"' "$LOG_API" && ok "trilha registra a recusa da ancora divergente" \
        || falhou "trilha sem a recusa campo_fixo_divergente"
    grep -q '"codigo": "acesso_negado"' "$LOG_API" && ok "trilha registra a recusa por ACL" \
        || falhou "trilha sem a recusa acesso_negado"
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
    # O resumo da atividade e' PAYLOAD de negocio: a trilha nao pode carrega-lo.
    if grep -q 'Ligar para o decisor\|replay da mesma chave\|Parceiro ancora HTTP' "$LOG_API" 2>/dev/null; then
        falhou "payload de negocio (resumo/nome) aparece na trilha de auditoria"
    else
        ok "nenhum payload de negocio entra na trilha de auditoria"
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
    corpo "corpo-11.json" <<JSON
{"idempotency_key": "tre-e01-t05-http-0011",
 "parametros": {"valores": {"res_id": $PARCEIRO_ANCORA, "summary": "atividade em homologacao"}}}
JSON
    ANTES_3D="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    CODIGO_3D="$(curl --config "$CFG_HOMOLOG" -X POST -d @"$DESC_DIR/corpo-11.json" \
        -o "$DESC_DIR/resposta-3d.json" -w '%{http_code}' "$BASE/tf/api/v1/atividade_criar" || true)"
    [ "$CODIGO_3D" = "503" ] && ok "escrita fora do ambiente permitido -> HTTP 503" \
        || falhou "escrita em homologacao -> HTTP $CODIGO_3D (esperado 503)"
    [ "$(campo_json "$DESC_DIR/resposta-3d.json" "d['codigo']")" = "ambiente_nao_permitido" ] \
        && ok "recusa nomeia ambiente_nao_permitido (fail-closed do ADR-005)" \
        || falhou "codigo de recusa errado (ambiente)"
    DEPOIS_3D="$(psql_bd "$BANCO" "select count(*) from mail_activity")"
    [ "$ANTES_3D" = "$DEPOIS_3D" ] && ok "ambiente fora da politica NAO escreveu ($DEPOIS_3D atividades)" \
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
    # O codigo NOVO deste card (a traducao da ancora) tem de estar no artefato E registrado no
    # `models/__init__.py`: arquivo de modelo fora do __init__ nao carrega, e o aceite HTTP passaria
    # a medir o Odoo sem a traducao (foi assim que a sonda mediu o defeito do Odoo 19).
    MODELO_ATIVIDADE="$MODULO_DIR/models/mail_activity.py"
    if [ -f "$MODELO_ATIVIDADE" ]; then
        ok "modelo do override presente: models/mail_activity.py"
    else
        falhou "modelo do override AUSENTE em models/mail_activity.py"
    fi
    for marca in '_inherit = "mail.activity"' 'tf_idempotency_key' 'tf_correlation_id' \
                 'res_model_id' 'def create('; do
        grep -qF "$marca" "$MODELO_ATIVIDADE" 2>/dev/null \
            && ok "modelo do override traz '$marca'" \
            || falhou "modelo do override sem '$marca'"
    done
    grep -qE '^from \. import mail_activity$' "$MODULO_DIR/models/__init__.py" \
        && ok "models/__init__.py importa o modelo do override (registro explicito)" \
        || falhou "models/__init__.py NAO importa mail_activity (modelo nao carregaria)"
    grep -qE "^[[:space:]]*'mail'," "$MODULO_DIR/__manifest__.py" \
        && ok "manifesto declara a dependencia 'mail' (o modulo herda mail.activity)" \
        || falhou "manifesto sem a dependencia 'mail' (dependencia implicita de crm)"
    RESUMO_OPERACAO="$(python3 - "$MODULO_DIR/api/politica_api.json" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1], encoding="utf-8"))
op = next((o for o in dados["operacoes"] if o["nome"] == "atividade_criar"), None)
if not op:
    print("AUSENTE")
    raise SystemExit(0)
decl = op["modelos"]["mail.activity"]
identidade = decl.get("campo_de_identidade") or ",".join(decl.get("campos_de_identidade") or [])
print("tipo=%s chave=%s modelo=mail.activity acao=%s identidade=%s fixos=%s obrigatorios=%s campos=%s id_interno=%s ambientes=%s" % (
    op.get("tipo"), op.get("requer_idempotency_key"), decl.get("acao"), identidade or "(nenhuma)",
    json.dumps(decl.get("valores_fixos"), sort_keys=True),
    ",".join(decl.get("campos_obrigatorios", [])),
    ",".join(decl.get("campos", [])),
    "res_model_id" in (decl.get("campos") or []),
    ",".join(dados.get("ambientes_permitidos", []))))
PY
)"
    case "$RESUMO_OPERACAO" in
        "tipo=escrita chave=True modelo=mail.activity acao=criar identidade=(nenhuma) fixos={\"res_model\": \"res.partner\"}"*)
            ok "politica declara a operacao de criacao com a ANCORA como valor FIXO e sem identidade ($RESUMO_OPERACAO)" ;;
        *) falhou "declaracao da operacao inesperada: $RESUMO_OPERACAO" ;;
    esac
    case "$RESUMO_OPERACAO" in
        *"obrigatorios=res_id"*) ok "res_id e' o campo obrigatorio declarado (a ancora e' obrigatoria)" ;;
        *) falhou "res_id nao e' o obrigatorio da operacao: $RESUMO_OPERACAO" ;;
    esac
    case "$RESUMO_OPERACAO" in
        *"res_model"*"id_interno=False"*) ok "o id interno da ancora (res_model_id) NAO e' porta declarada" ;;
        *) falhou "a politica declara res_model_id entre os campos (porta pelo id interno): $RESUMO_OPERACAO" ;;
    esac
    case "$RESUMO_OPERACAO" in
        *"idempotency=True"*) ok "a escrita exige idempotency_key (contrato §7 do doc 06)" ;;
        *) falhou "a escrita nao exige idempotency_key: $RESUMO_OPERACAO" ;;
    esac
    case "$RESUMO_OPERACAO" in
        *"ambientes=dev") ok "ambiente declarado e' so' o dev (ADR-005)" ;;
        *) falhou "ambientes declarados fora do previsto: $RESUMO_OPERACAO" ;;
    esac
    case "$RESUMO_OPERACAO" in
        *"do_not_contact"*|*"note"*|*"stage_id"*)
            falhou "a politica declara campo fora do escopo do espelho da atividade: $RESUMO_OPERACAO" ;;
        *) ok "nenhum campo de outro escopo declarado no espelho da atividade" ;;
    esac
fi

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
