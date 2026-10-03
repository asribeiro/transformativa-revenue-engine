#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E01-T01 — API controlada do Odoo (`POST /tf/api/v1/<operacao>`)
#
# Criterios de aceitacao (definidos no inicio do card, registrados no card e no runbook
# docs/runbooks/api-controlada-odoo.md):
#   AC1 superficie fechada e declarada (politica versionada; 1 rota; so' POST; politica ilegivel
#       nao serve nada);
#   AC2 nada de escrita direta no banco interno do Odoo (todo acesso por ORM; 0 SQL nos arquivos
#       da API);
#   AC3 filtro por campo declarado (campo/operador/limite fora da declaracao = recusa);
#   AC4 credencial fora do repositorio e fora do log (auth='bearer' com chave de API; 401 sem
#       token; token ausente do log, da resposta e do artefato);
#   AC5 guarda de ambiente (ADR-005: so' atende no ambiente declarado E permitido; homologacao/
#       producao exigem aprovacao humana registrada);
#   AC6 contrato de integracao (idempotency_key exigida na escrita; dry_run nao escreve;
#       correlation_id ecoado);
#   AC7 rastreabilidade (UMA linha TF_API_AUDIT por chamada, inclusive nas recusas, sem token e
#       sem payload);
#   AC8 ambiente de execucao intocado (dupla descartavel propria; dev/homolog/prod medidos antes
#       e depois).
#
# TEST PLAN (executado por este script, NA VPS do dev):
#   passo 0  suite PURA do motor (sem Odoo)              -> scripts/odoo/testar_motor_api.py
#   passo 1  instalacao do modulo em banco limpo         -> log + estado lido no banco
#   passo 2  suite do Odoo (--test-enable)               -> relatorio do runner + nomes dos testes
#   passo 3  preparo + servidor HTTP real + curl         -> codigos HTTP, envelopes e auditoria
#   passo 4  contrato do modulo (greps)                  -> rota unica, bearer, POST, 0 SQL
#   limpeza  banco/dupla/rede removidos; dev, homolog e producao conferidos intactos
#
# ISOLAMENTO (mesma licao do E03, ver docs/runbooks/odoo-modulo-sales-ai.md §8): o Odoo do dev
# abre sessao em QUALQUER banco novo da instancia `pg-odoo-dev`, entao este verificador sobe a SUA
# propria dupla descartavel (postgres:16 + odoo:19.0, as mesmas imagens do par de dev) em rede
# propria, e nao usa `pg-odoo-dev`, `odoo_dev` nem a copia operacional /opt/tre/repo.
#
# Uso (NA VPS, a partir de arquivo):
#   bash verificar-api-controlada.sh
#   bash verificar-api-controlada.sh --apenas-motor
#   bash verificar-api-controlada.sh --apenas-suites        (instalacao + teste do Odoo)
#   bash verificar-api-controlada.sh --apenas-http         (instalacao + servidor + curl)
#   bash verificar-api-controlada.sh --prova-de-dente      (3 mutacoes; cada uma TEM de reprovar)
#   bash verificar-api-controlada.sh --banco tre_outro --modulo-dir /caminho/do/modulo
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
BANCO="${TRE_BANCO:-tre_e01_t01_api}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-api-controlada}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"
# Piso de testes do modulo medido em 01/10/2026: 82 testes
# (50 dos cards W2 + 32 da suite da API deste card). Piso = o medido: menos que isso e regressao.
PISO_DE_TESTES="${TRE_PISO_DE_TESTES:-82}"

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
PG_TMP="e01t01-pg-$SUFIXO"
API_TMP="e01t01-api-$SUFIXO"
NET_TMP="e01t01-net-$SUFIXO"
DESC_DIR=""
API_CT=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: API_CONTROLADA_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: API_CONTROLADA_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? tres mutacoes, cada uma em copia propria do modulo.
#   dente 1: politica SEM a operacao declarada      -> o item "operacao declarada atende" reprova;
#   dente 2: motor SEM a checagem de campo declarado -> o item "campo nao declarado recusa" reprova;
#   dente 3: motor SEM a checagem de aprovacao       -> a suite pura reprova a aprovacao ausente.
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e01t01-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_FALHAS=0

    # Ancora do alvo: o dente TEM de rodar contra uma copia do artefato REAL, e o artefato real
    # nao pode mudar por causa da prova (a licao do E06: prova negativa que nao ancora o alvo nao
    # prova nada, porque pode estar medindo outra coisa).
    manifesto_modulo() {
        find "$MODULO_DIR" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
    }
    arquivos_modulo() { find "$MODULO_DIR" -type f | wc -l | tr -d ' '; }
    MANIFESTO_ANTES="$(manifesto_modulo)"
    ARQUIVOS_MODULO="$(arquivos_modulo)"
    echo "OK    ancora: alvo do dente = $MODULO_DIR ($ARQUIVOS_MODULO arquivos, sha256 $MANIFESTO_ANTES)"

    cabecalho "prova de dente 1: politica sem a operacao declarada (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    MUT1="$(python3 - "$DENTE_DIR/m1/api/politica_api.json" <<'PY'
import json, sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    dados = json.load(fh)
dados["operacoes"] = [op for op in dados["operacoes"] if op["nome"] != "sistema_capacidades"]
with open(caminho, "w", encoding="utf-8") as fh:
    json.dump(dados, fh, ensure_ascii=False, indent=2)
print("MUTACAO_APLICADA")
PY
)"
    if [ "$MUT1" = "MUTACAO_APLICADA" ] && ! grep -q 'sistema_capacidades' "$DENTE_DIR/m1/api/politica_api.json"; then
        echo 'OK    dente 1: mutacao aplicada (a operacao sumiu da copia da politica)'
    else
        echo 'FALHOU dente 1: mutacao NAO foi aplicada na copia — o dente mediria o artefato intacto'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_BANCO="${BANCO}_d1" TRE_LOG_DIR="$LOG_DIR/dente1" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D1" >"$LOG_DIR/dente-1-politica-mutada.out"
    printf '%s\n' "$D1" | tail -3
    if printf '%s\n' "$D1" | grep -q 'RESULTADO: API_CONTROLADA_FALHOU'; then
        echo 'OK    dente 1: politica sem a operacao reprova o aceite (a politica e load-bearing)'
    else
        echo 'FALHOU dente 1: politica mutada NAO reprovou — o item de operacao declarada nao tem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi

    cabecalho "prova de dente 2: motor sem a checagem de campo declarado (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    MUT2="$(python3 - "$DENTE_DIR/m2/api/motor.py" <<'PY'
import re, sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    texto = fh.read()
antes = texto
# Muta a checagem de campo declarado na LEITURA (a que o item de campo nao declarado mede).
texto = texto.replace(
    '                raise ErroApi(\n                    "campo_nao_declarado",\n'
    '                    "campo %r nao esta declarado para este modelo na operacao %s (declarados: %s)"\n'
    '                    % (campo, op["nome"], ", ".join(campos)),\n                )',
    '                pass',
    1,
)
if texto == antes:
    print("MUTACAO_NAO_APLICADA")
    sys.exit(3)
with open(caminho, "w", encoding="utf-8") as fh:
    fh.write(texto)
print("MUTACAO_APLICADA")
PY
)"
    if [ "$MUT2" = "MUTACAO_APLICADA" ]; then
        echo 'OK    dente 2: mutacao aplicada na copia do motor'
    else
        echo "FALHOU dente 2: mutacao NAO foi aplicada na copia do motor ($MUT2) — o dente mediria o motor intacto"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_d2" TRE_LOG_DIR="$LOG_DIR/dente2" \
          "$0" --apenas-http 2>&1)"
    printf '%s\n' "$D2" >"$LOG_DIR/dente-2-motor-mutado.out"
    printf '%s\n' "$D2" | tail -3
    if printf '%s\n' "$D2" | grep -q 'RESULTADO: API_CONTROLADA_FALHOU'; then
        echo 'OK    dente 2: motor sem a checagem de campo reprova o aceite'
    else
        echo 'FALHOU dente 2: motor mutado NAO reprovou — o item de campo nao declarado nao tem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi

    cabecalho "prova de dente 3: motor sem a checagem de aprovacao (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m3"
    MUT3="$(python3 - "$DENTE_DIR/m3/api/motor.py" <<'PY'
import sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    texto = fh.read()
antes = texto
texto = texto.replace(
    '    if ambiente in AMBIENTES_COM_APROVACAO and not aprovacao_valida(aprovacao, hoje=hoje):',
    '    if False and ambiente in AMBIENTES_COM_APROVACAO and not aprovacao_valida(aprovacao, hoje=hoje):',
    1,
)
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
    D3="$(TRE_MODULO_DIR="$DENTE_DIR/m3" "$0" --apenas-motor 2>&1)"
    printf '%s\n' "$D3" >"$LOG_DIR/dente-3-aprovacao-mutada.out"
    printf '%s\n' "$D3" | tail -3
    if printf '%s\n' "$D3" | grep -q 'RESULTADO: MOTOR_API_FALHOU'; then
        echo 'OK    dente 3: motor sem a checagem de aprovacao reprova a suite pura'
    else
        echo 'FALHOU dente 3: motor mutado NAO reprovou — o item de aprovacao nao tem dente'
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
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
        echo "RESULTADO: API_CONTROLADA_DENTE_OK (3 provas, 0 falhas) modulo=$MODULO"
        exit 0
    fi
    echo "RESULTADO: API_CONTROLADA_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente) modulo=$MODULO"
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
for arquivo in __manifest__.py api/motor.py api/politica_api.json controllers/api_controlada.py; do
    if [ -f "$MODULO_DIR/$arquivo" ]; then
        ok "modulo em disco: $arquivo"
    else
        falhou "modulo ausente em $MODULO_DIR/$arquivo"; resumo
    fi
done
if [ -d "$MODULO_DIR/tests/politicas" ]; then ok "politicas de teste presentes (fixtures do aceite)"; \
    else falhou "fixtures de politica de teste ausentes"; fi
info "sha256 dos arquivos da API sob teste:"
for arquivo in api/politica_api.json api/motor.py controllers/api_controlada.py tests/test_api_controlada.py; do
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
DESC_DIR="$(mktemp -d /tmp/verificacao-api-XXXXXX)"
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
    # ja usada em scripts/provision/instalar-odoo-dev.sh e no verificador do E03).
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

# ---------------------------------------------------------------------------
# passo 1 — instalacao em banco limpo (AC1 no ambiente; base dos passos 2 e 3)
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
    ROTAS="$(psql_bd "$BANCO" "select count(*) from ir_http_route where path like '/tf/api/v1/%'")"
    case "$ROTAS" in
        ""|"0") ok "a rota do modulo nao depende de tabela de rota do Odoo (controle por decorador)" ;;
        *) ok "rotas /tf/api/v1/* registradas no banco: $ROTAS" ;;
    esac
fi

# ---------------------------------------------------------------------------
# passo 2 — suite do Odoo (--test-enable): a suite da API + as suites dos cards W2
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
    grep -q 'At least one test failed when loading the modules\.' "$LOG_ATUAL" \
        && falhou "log traz 'At least one test failed when loading the modules.'" \
        || ok "log sem 'At least one test failed when loading the modules.'"
    LINHAS_REPROVADAS="$(grep -cE '^(FAIL|ERROR): ' "$LOG_ATUAL" || true)"
    [ "$LINHAS_REPROVADAS" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
        || falhou "$LINHAS_REPROVADAS linha(s) de teste reprovado(a) no log"
    FALTANDO=0
    TOTAL_API=0
    if [ -f "$MODULO_DIR/tests/test_api_controlada.py" ]; then
        while read -r metodo; do
            [ -z "$metodo" ] && continue
            TOTAL_API=$((TOTAL_API + 1))
            grep -q "$metodo" "$LOG_ATUAL" || { FALTANDO=$((FALTANDO + 1)); info "teste ausente no log: $metodo"; }
        done <<<"$(grep -oE 'def (test_[0-9]+_[a-z_0-9]+)' "$MODULO_DIR/tests/test_api_controlada.py" \
            | sed 's/^def //' | sort -u)"
    fi
    if [ "$TOTAL_API" -ge 20 ] && [ "$FALTANDO" = "0" ]; then
        ok "todos os $TOTAL_API testes da suite da API aparecem no log do runner"
    else
        falhou "suite da API: $TOTAL_API testes encontrados, $FALTANDO ausentes no log"
    fi
    ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
    [ "$ESTADO" = "installed" ] && ok "modulo segue installed depois da suite" \
        || falhou "estado depois da suite: '$ESTADO'"
fi

# ---------------------------------------------------------------------------
# passo 3 — HTTP externo de verdade: preparo + servidor + curl (AC1/AC3/AC4/AC6/AC7)
# ---------------------------------------------------------------------------
if [ "$MODO" = "completo" ] || [ "$MODO" = "http" ]; then
    cabecalho "passo 3 — servidor HTTP real + curl (consumidor externo)"
    LOG_ATUAL="$LOG_DIR/3-preparo.log"
    # O container `odoo:19.0` roda como uid 100 (`odoo`) / gid 101: o diretorio descartavel tem de
    # ser gravavel por ELE para o preparo conseguir escrever a chave (sem isso o `open()` do
    # preparador falha com Permission denied). O diretorio continua 700 — quem manda nele e' o uid
    # do container, e o verificador (root) continua lendo tudo.
    chown 100:101 "$DESC_DIR" 2>/dev/null || info "nao consegui chown do diretorio descartavel (uid do container pode diferir)"
    RC="$(docker run --rm -i --network "$NET_TMP" \
            -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
            -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
            -v "$DESC_DIR":/preparo \
            -e TRE_API_ARQUIVO_CHAVE=/preparo/chave.txt \
            --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$PREPARADOR" >"$LOG_ATUAL" 2>&1)"
    grep -q 'TF_API_PREPARO_OK' "$LOG_ATUAL" && ok "preparo do ambiente descartavel (usuario de integracao + parametros)" \
        || falhou "preparo nao confirmou (log: $LOG_ATUAL)"
    if [ -s "$DESC_DIR/chave.txt" ] && [ "$(stat -c '%a' "$DESC_DIR/chave.txt")" = "600" ]; then
        ok "chave de API gerada em arquivo 600 (fora de stdout, log e argumento)"
    else
        falhou "chave de API ausente ou sem permissao 600 em $DESC_DIR/chave.txt"
    fi
    USUARIO_API="$(psql_bd "$BANCO" "select login from res_users where login = 'tf_api_integracao'")"
    [ "$USUARIO_API" = "tf_api_integracao" ] && ok "usuario de integracao existe no banco" \
        || falhou "usuario de integracao ausente"
    CHAVES="$(psql_bd "$BANCO" "select count(*) from res_users_apikeys")"
    [ "$CHAVES" -ge 1 ] && ok "chave de API gravada em res_users_apikeys ($CHAVES)" \
        || falhou "nenhuma chave de API no banco"

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
    VIVO=0
    for _ in $(seq 1 30); do
        CODIGO="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$BASE/web/login" || true)"
        [ "$CODIGO" = "200" ] && { VIVO=1; break; }
        sleep 3
    done
    [ "$VIVO" = "1" ] && ok "servidor responde HTTP 200 em /web/login" \
        || falhou "servidor nao respondeu /web/login (ultimo codigo: ${CODIGO:-nenhum})"
    if [ "$FALHAS" -gt 0 ]; then resumo; fi

    # curl por arquivo de configuracao (600): o token NUNCA entra em argumento de comando
    CFG="$DESC_DIR/curl.cfg"
    {
        echo 'silent'
        echo 'show-error'
        echo 'header = "Content-Type: application/json"'
        printf 'header = "Authorization: Bearer %s"\n' "$(cat "$DESC_DIR/chave.txt")"
    } >"$CFG"
    chmod 600 "$CFG"
    CFG_RUIM="$DESC_DIR/curl-ruim.cfg"
    {
        echo 'silent'
        echo 'show-error'
        echo 'header = "Content-Type: application/json"'
        echo 'header = "Authorization: Bearer chave-invalida-000000000000"'
    } >"$CFG_RUIM"
    chmod 600 "$CFG_RUIM"

    CHAMADAS=0
    AUDITADAS=0
    ULTIMA_RESPOSTA=""
    api_post() { # $1=rotulo $2=codigo_esperado $3=operacao $4=corpo $5=config (padrao: com token)
        local rotulo="$1" esperado="$2" operacao="$3" corpo="$4" config="${5:-$CFG}"
        local saida codigo
        saida="$(curl --config "$config" -X POST -d "$corpo" -o "$DESC_DIR/resposta.json" \
            -w '%{http_code}' "$BASE/tf/api/v1/$operacao" || true)"
        codigo="$saida"
        CHAMADAS=$((CHAMADAS + 1))
        [ "$config" = "$CFG" ] && AUDITADAS=$((AUDITADAS + 1))
        if [ "$codigo" = "$esperado" ]; then
            ok "$rotulo -> HTTP $codigo"
        else
            falhou "$rotulo -> HTTP $codigo (esperado $esperado): $(head -c 300 "$DESC_DIR/resposta.json" | tr -d '\n')"
        fi
        cp "$DESC_DIR/resposta.json" "$DESC_DIR/resposta-$CHAMADAS.json"
        ULTIMA_RESPOSTA="$DESC_DIR/resposta-$CHAMADAS.json"
    }
    api_get() { # $1=rotulo $2=codigo_esperado $3=operacao
        # NAO conta como chamada auditada: o 405 e' levantado pelo roteamento do Odoo (o verbo nao
        # combina com a rota), ANTES do controlador — entao nao ha' linha de auditoria, por desenho.
        local saida
        saida="$(curl --config "$CFG" -o "$DESC_DIR/resposta.json" -w '%{http_code}' \
            "$BASE/tf/api/v1/$3" || true)"
        CHAMADAS=$((CHAMADAS + 1))
        cp "$DESC_DIR/resposta.json" "$DESC_DIR/resposta-$CHAMADAS.json"
        ULTIMA_RESPOSTA="$DESC_DIR/resposta-$CHAMADAS.json"
        if [ "$saida" = "$2" ]; then ok "$1 -> HTTP $saida"; else falhou "$1 -> HTTP $saida (esperado $2)"; fi
    }
    sem_token() { # $1=rotulo $2=codigo_esperado $3=operacao
        local saida
        saida="$(curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
            -o "$DESC_DIR/resposta.json" -w '%{http_code}' "$BASE/tf/api/v1/$3" || true)"
        CHAMADAS=$((CHAMADAS + 1))
        cp "$DESC_DIR/resposta.json" "$DESC_DIR/resposta-$CHAMADAS.json"
        ULTIMA_RESPOSTA="$DESC_DIR/resposta-$CHAMADAS.json"
        if [ "$saida" = "$2" ]; then ok "$1 -> HTTP $saida"; else falhou "$1 -> HTTP $saida (esperado $2)"; fi
    }
    campo_json() { python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ''))" \
        "$1" "$2"; }

    sem_token "sem token na rota" "401" "sistema_capacidades"
    api_post "token invalido" "401" "sistema_capacidades" '{}' "$CFG_RUIM"
    api_post "operacao declarada (sistema_capacidades)" "200" "sistema_capacidades" \
        '{"correlation_id":"tre-e01-t01-http-1"}'
    VALOR_OK="$(campo_json "$ULTIMA_RESPOSTA" ok)"
    VERSAO_OK="$(campo_json "$ULTIMA_RESPOSTA" politica_versao)"
    CORREL_OK="$(campo_json "$ULTIMA_RESPOSTA" correlation_id)"
    [ "$VALOR_OK" = "True" ] && ok "envelope de sucesso com ok=true" || falhou "resposta 200 sem ok=true"
    # ANCORA:POLITICA_EM_VIGOR — a versao esperada sai do PROPRIO arquivo da politica sob teste
    # (era literal "1.0.0"): assim o item nao expira a cada versao nova da politica (a chegada da
    # primeira operacao de negocio, TRE-W3-E01-T02, mudou a versao para 1.1.0 e o literal viraria
    # falso vermelho sem nenhuma regressao por tras).
    VERSAO_POLITICA="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['versao'])" \
        "$MODULO_DIR/api/politica_api.json")"
    [ "$VERSAO_OK" = "$VERSAO_POLITICA" ] && ok "envelope traz a versao da politica em vigor ($VERSAO_OK)" \
        || falhou "politica_versao da resposta '$VERSAO_OK' difere da politica em vigor '$VERSAO_POLITICA'"
    [ "$CORREL_OK" = "tre-e01-t01-http-1" ] && ok "correlation_id do chamador ecoado na resposta" \
        || falhou "correlation_id nao ecoado (veio '$CORREL_OK')"
    OPERACOES_DECLARADAS="$(python3 -c "
import json, sys
dados = json.load(open(sys.argv[1]))
print(','.join(sorted(op['nome'] for op in dados['dados']['capacidades']['operacoes'])))
" "$ULTIMA_RESPOSTA")"
    # ANCORA:POLITICA_EM_VIGOR — a lista esperada vem da POLITICA (era literal
    # 'crm_registros_ler,sistema_capacidades'): o que se garante e' que a API serve exatamente as
    # operacoes declaradas na politica em vigor, sem literal que expira a cada operacao nova.
    OPERACOES_POLITICA="$(python3 -c "
import json, sys
dados = json.load(open(sys.argv[1], encoding='utf-8'))
print(','.join(sorted(op['nome'] for op in dados['operacoes'])))
" "$MODULO_DIR/api/politica_api.json")"
    [ "$OPERACOES_DECLARADAS" = "$OPERACOES_POLITICA" ] \
        && ok "a API declara exatamente as operacoes da politica ($OPERACOES_DECLARADAS)" \
        || falhou "operacoes servidas ($OPERACOES_DECLARADAS) diferentes da politica ($OPERACOES_POLITICA)"
    api_post "operacao nao declarada" "404" "parceiro_criar" '{}'
    [ "$(campo_json "$ULTIMA_RESPOSTA" codigo)" = "operacao_nao_declarada" ] \
        && ok "recusa de operacao nao declarada nomeia o codigo" || falhou "codigo de recusa errado"
    api_get "verbo GET na mesma rota" "405" "sistema_capacidades"
    api_post "leitura declarada (res.partner)" "200" "crm_registros_ler" \
        '{"parametros":{"modelo":"res.partner","campos":["id","name"],"limite":3}}'
    TOTAL_LIDO="$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1]))['dados']['total'])" "$ULTIMA_RESPOSTA")"
    [ "$TOTAL_LIDO" -ge 1 ] && ok "leitura declarada devolveu registros (total=$TOTAL_LIDO)" \
        || falhou "leitura declarada nao devolveu registro (total=$TOTAL_LIDO)"
    api_post "campo nao declarado" "422" "crm_registros_ler" \
        '{"parametros":{"modelo":"res.partner","campos":["id","email"]}}'
    [ "$(campo_json "$ULTIMA_RESPOSTA" codigo)" = "campo_nao_declarado" ] \
        && ok "recusa de campo nao declarado nomeia o codigo" || falhou "codigo de recusa errado (campo)"
    api_post "operador nao declarado no filtro" "422" "crm_registros_ler" \
        '{"parametros":{"modelo":"crm.lead","filtro":[["tf_opportunity_id","like","x"]]}}'
    api_post "limite acima do teto" "422" "crm_registros_ler" \
        '{"parametros":{"modelo":"res.partner","limite":5000}}'
    api_post "chave desconhecida no corpo" "400" "sistema_capacidades" \
        '{"marcador_que_nao_pode_vazar":"MARCADOR-NAO-DEVE-APARECER"}'
    api_post "escrita nao declarada na politica real" "404" "parceiro_upsert" \
        '{"idempotency_key":"tre-e01-t01-http-escrita","parametros":{"valores":{"name":"x"}}}'
    # `grep -c` com VÁRIOS arquivos imprime "arquivo:contagem" por arquivo (e vira multi-linha) — o
    # item media sempre FALHOU por isso. Aqui o total e' contado de verdade: uma ocorrencia a mais
    # ja' reprova.
    MARCADOR_VAZOU="$(grep -h -o 'MARCADOR-NAO-DEVE-APARECER' "$DESC_DIR"/resposta-*.json 2>/dev/null | wc -l | tr -d ' ')"
    [ "$MARCADOR_VAZOU" = "0" ] && ok "payload recusado nao ecoa de volta na resposta" \
        || falhou "payload recusado voltou na resposta"

    cabecalho "passo 3b — auditoria por chamada (AC7) e ausencia de segredo"
    LOG_API="$LOG_DIR/3b-servidor.log"
    docker logs "$API_CT" >"$LOG_API" 2>&1
    LINHAS_AUDITORIA="$(grep -c 'TF_API_AUDIT' "$LOG_API" || true)"
    if [ "$LINHAS_AUDITORIA" = "$AUDITADAS" ]; then
        ok "uma linha TF_API_AUDIT por chamada autenticada ($LINHAS_AUDITORIA linhas, $AUDITADAS chamadas; as outras 3 param no Odoo antes do controlador: 2 sem token valido e 1 verbo errado)"
    else
        falhou "auditoria: $LINHAS_AUDITORIA linhas para $AUDITADAS chamadas autenticadas"
    fi
    grep -q '"resultado": "ok"' "$LOG_API" && ok "trilha tem chamadas de sucesso" \
        || falhou "trilha sem chamada de sucesso"
    grep -q '"codigo": "operacao_nao_declarada"' "$LOG_API" && ok "trilha registra a recusa com o codigo" \
        || falhou "trilha sem a recusa de operacao nao declarada"
    grep -q '"codigo": "campo_nao_declarado"' "$LOG_API" && ok "trilha registra a recusa de campo" \
        || falhou "trilha sem a recusa de campo nao declarado"
    grep -q '"correlation_id": "tre-e01-t01-http-1"' "$LOG_API" && ok "trilha carrega o correlation_id do chamador" \
        || falhou "trilha sem o correlation_id do chamador"
    if grep -q -F -f "$DESC_DIR/chave.txt" "$LOG_API" 2>/dev/null; then
        falhou "a chave de API aparece no log do servidor"
    else
        ok "a chave de API NAO aparece no log do servidor"
    fi
    if grep -q 'MARCADOR-NAO-DEVE-APARECER' "$LOG_API" 2>/dev/null; then
        falhou "payload recusado aparece na trilha de auditoria"
    else
        ok "payload nao entra na trilha de auditoria (nem o recusado)"
    fi
    if grep -q 'Bearer' "$LOG_API" 2>/dev/null; then
        falhou "a palavra Bearer aparece no log (indicio de token logado)"
    else
        ok "nenhum token/Bearer no log do servidor"
    fi
    docker rm -f "$API_CT" >/dev/null 2>&1
    API_CT=""
    ok "servidor de API encerrado (container descartavel removido)"
fi

# ---------------------------------------------------------------------------
# passo 4 — contrato do modulo (AC1/AC2 por leitura do artefato)
# ---------------------------------------------------------------------------
if [ "$MODO" = "completo" ]; then
    cabecalho "passo 4 — contrato do modulo (greps)"
    CONTROLLER="$MODULO_DIR/controllers/api_controlada.py"
    N_ROTAS="$(grep -c '@http.route(' "$CONTROLLER" 2>/dev/null || true)"
    [ "$N_ROTAS" = "1" ] && ok "o controlador tem UMA unica rota (sem despachante generico)" \
        || falhou "o controlador tem $N_ROTAS rotas (esperado 1)"
    grep -q 'auth="bearer"' "$CONTROLLER" && ok "rota com auth='bearer' (chave de API do Odoo)" \
        || falhou "rota sem auth='bearer'"
    grep -q 'methods=\["POST"\]' "$CONTROLLER" && ok "rota declarada so' para POST" \
        || falhou "rota sem methods=['POST']"
    grep -q 'csrf=False' "$CONTROLLER" && ok "CSRF desligado por desenho (a chave e' a protecao)" \
        || falhou "rota sem csrf=False explicito"
    SQL_API="$(grep -rnE '(^|[^a-zA-Z_])cr\.execute|sql\.SQL|_cr\.execute' \
        "$MODULO_DIR/api" "$MODULO_DIR/controllers" 2>/dev/null | wc -l | tr -d ' ')"
    [ "$SQL_API" = "0" ] && ok "nenhum SQL nos arquivos da API (AC2: so' ORM)" \
        || falhou "$SQL_API ocorrencia(s) de SQL nos arquivos da API"
    if grep -q 'import odoo\|from odoo' "$MODULO_DIR/api/motor.py"; then
        falhou "o motor importa odoo (deveria ser puro)"
    else
        ok "o motor e' puro (sem import de odoo) — decisao exercitavel fora do Odoo"
    fi
    POLITICA_RESUMO="$(python3 - "$MODULO_DIR/api/politica_api.json" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1], encoding="utf-8"))
print("esquema=%s versao=%s ambientes=%s operacoes=%s" % (
    dados.get("esquema"), dados.get("versao"),
    ",".join(dados.get("ambientes_permitidos", [])),
    ",".join(op.get("nome") for op in dados.get("operacoes", []))))
PY
)"
    case "$POLITICA_RESUMO" in
        *"ambientes=dev"*) ok "politica versionada com ambiente unico dev ($POLITICA_RESUMO)" ;;
        *) falhou "politica com ambientes inesperados: $POLITICA_RESUMO" ;;
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
if [ -z "$(docker ps -q --filter "name=^$PG_TMP$")" ]; then ok "postgres descartavel $PG_TMP removido"
else falhou "postgres descartavel $PG_TMP continua de pe"; fi
docker network rm "$NET_TMP" >/dev/null 2>&1
if [ -z "$(docker network ls -q --filter "name=^$NET_TMP$")" ]; then ok "rede descartavel $NET_TMP removida"
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
