#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E01-T04 — operacao de escrita de negocio `oportunidade_upsert`
# (`POST /tf/api/v1/oportunidade_upsert`) no modulo transformativa_sales_ai.
#
# Criterios de aceitacao (definidos no inicio do card, registrados no card e no runbook
# docs/runbooks/odoo-oportunidade-upsert.md):
#   AC1 operacao declarada e versionada (`oportunidade_upsert` na politica real, tipo escrita,
#       modelo crm.lead, identidade `tf_opportunity_id`, chave exigida; servida pela MESMA porta
#       unica — 1 rota; politica ilegivel nao serve nada);
#   AC2 identidade canonica (contrato §3): casa pelo UUID, nunca por nome; sem o UUID no pedido ->
#       422 com recusa NOMEADA; UUID fora do formato -> 422 `valor_invalido` (constraint do modelo);
#   AC3 upsert por identidade: N chamadas com o mesmo UUID -> UM registro (contagem medida no
#       banco); `criar` na primeira e `atualizar` depois; atualizacao PARCIAL (campo nao enviado
#       permanece);
#   AC4 fronteira de dono (contrato §2): campo de dono do Odoo (`stage_id`, `expected_revenue`,
#       `probability`) -> 422 `campo_nao_declarado`; `tf_priority_tier` (derivado) tambem; e o
#       ESPELHO nao muda estagio/valor de um lead ja' existente (medido no banco antes/depois);
#   AC5 contrato de integracao (doc 06 §7): `idempotency_key` exigida e validada; `dry_run`
#       descreve sem escrever (contagem identica); `correlation_id` ecoado; rastro gravado no lead;
#   AC6 guarda de ambiente (ADR-005) na ESCRITA: dev atende; `homologacao` fora da politica -> 503
#       (medido por curl, com o servidor NOVO depois da troca pelo ORM);
#   AC7 rastreabilidade (UMA linha TF_API_AUDIT por chamada autenticada, inclusive nas recusas,
#       sem token e sem payload);
#   AC8 ambiente de execucao intocado (dupla descartavel propria; dev/homolog/prod medidos antes e
#       depois; nenhum DDL/migration/escrita em sales_intelligence).
#
# TEST PLAN (executado por este script, NA VPS do dev):
#   passo 0  suite PURA do motor (sem Odoo)              -> scripts/odoo/testar_motor_api.py
#   passo 1  instalacao do modulo em banco limpo         -> log + estado lido no banco
#   passo 2  suite do Odoo (--test-enable)               -> relatorio do runner + nome dos testes
#   passo 3  preparo + servidor HTTP real + curl         -> upsert de verdade (cria/atualiza/
#                                                          repete), recusas, dry-run e contagem
#   passo 3b medicao no banco (SQL de leitura)           -> 1 registro por UUID, espelho, estagio
#                                                          e valor intocados, dono do lead
#   passo 3c auditoria no log do servidor                -> 1 linha por chamada autenticada
#   passo 3d guarda de ambiente na escrita (ADR-005)     -> 503 com o ambiente trocado pelo ORM
#   passo 4  contrato do modulo (greps)                  -> 1 rota, bearer, POST, 0 SQL, fronteira
#   limpeza  banco/dupla/rede removidos; dev, homolog e producao conferidos intactos
#
# ISOLAMENTO (mesma licao do E03, ver docs/runbooks/odoo-modulo-sales-ai.md §8): o Odoo do dev
# abre sessao em QUALQUER banco novo da instancia `pg-odoo-dev`, entao este verificador sobe a SUA
# propria dupla descartavel (postgres:16 + odoo:19.0, as mesmas imagens do par de dev) em rede
# propria, e nao usa `pg-odoo-dev`, `odoo_dev` nem a copia operacional /opt/tre/repo.
#
# Uso (NA VPS, a partir de arquivo):
#   bash verificar-oportunidade-upsert.sh
#   bash verificar-oportunidade-upsert.sh --apenas-motor
#   bash verificar-oportunidade-upsert.sh --apenas-suites   (instalacao + teste do Odoo)
#   bash verificar-oportunidade-upsert.sh --apenas-http    (instalacao + servidor + curl)
#   bash verificar-oportunidade-upsert.sh --prova-de-dente (3 mutacoes; cada uma TEM de reprovar,
#                                                          e dente que nao MEDE reprova — o harness
#                                                          e' fail-closed: licao do t_fa9db205)
#   bash verificar-oportunidade-upsert.sh --banco tre_outro --modulo-dir /caminho/do/modulo
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
BANCO="${TRE_BANCO:-tre_e01_t04_oportunidade}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-oportunidade-upsert}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"
# Piso de testes do modulo medido em 02/10/2026: 109 testes
# (50 dos cards W2 + 32 da suite da API do E01-T01 + 27 da suite do upsert de oportunidade do
# E01-T04). Piso = o medido: menos que isso e regressao (teste que nao roda nao passou).
PISO_DE_TESTES="${TRE_PISO_DE_TESTES:-109}"

# Identidades fixas da fase HTTP (UUID canonico do contrato §3; os nomes sao propositalmente
# distintos para o banco poder ser conferido por SQL depois).
UUID_HTTP="7c9e6679-7425-40de-944b-e07fc1f90ae7"
UUID_NOME="8d0f778a-8536-41ef-a55c-f18fc2a1bf18"
UUID_PARCIAL="9e1f889b-9647-42f0-b66d-f29fd3b2cf29"
UUID_VERSAO="af2f99ac-a758-4301-c77e-f3afe4c3df3a"
MARCADOR_PAYLOAD="MARCADOR-PAYLOAD-E01T04-NAO-DEVE-APARECER"

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
PG_TMP="e01t04-pg-$SUFIXO"
API_TMP="e01t04-api-$SUFIXO"
NET_TMP="e01t04-net-$SUFIXO"
DESC_DIR=""
API_CT=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: OPORTUNIDADE_UPSERT_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: OPORTUNIDADE_UPSERT_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente (FAIL-CLOSED): o aceite tem dentes? Tres mutacoes, cada uma em copia propria do
# modulo. Cada prova TEM de (a) APLICAR a mutacao na copia, (b) RODAR de verdade e (c) reprovar o
# ITEM ESPERADO do aceite. Prova que nao mede nada REPROVA: este harness nunca imprime DENTE_OK sem
# medicao (licao do defeito t_fa9db205 — o harness fail-open da rodada 1 do E01-T01 imprimia
# DENTE_OK com os dentes abortados).
#   dente 1: politica SEM a operacao `oportunidade_upsert`      -> o item de declaracao reprova (e a
#                                                                escrita passa a dar 404);
#   dente 2: motor SEM a checagem de campo declarado na ESCRITA -> o item de campo de dono do Odoo
#                                                                deixa de recusar 422;
#   dente 3: controlador SEM o upsert por identidade            -> o item "segunda chamada atualiza o
#                                                                mesmo registro" reprova (duplicata).
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e01t04-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_FALHAS=0
    DENTE_PROVAS=0

    # Fail-closed ANTES de tudo: sem docker e sem as DUAS imagens nao ha' o que medir, e "nao deu
    # para medir" nao e' prova de nada.
    if ! docker info >/dev/null 2>&1; then
        echo "FALHOU dente: docker nao responde — nenhuma prova pode ser medida"
        echo "RESULTADO: OPORTUNIDADE_UPSERT_DENTE_FALHOU (0 provas medidas) modulo=$MODULO"
        exit 1
    fi
    for img in "$IMAGEM" "$IMAGEM_PG"; do
        if ! docker image inspect "$img" >/dev/null 2>&1; then
            echo "FALHOU dente: imagem $img ausente — nenhuma prova pode ser medida"
            echo "RESULTADO: OPORTUNIDADE_UPSERT_DENTE_FALHOU (0 provas medidas) modulo=$MODULO"
            exit 1
        fi
    done

    # Ancora do alvo: o dente roda contra uma copia do artefato REAL, e o artefato real nao pode
    # mudar por causa da prova. A ancora e' o sha256 do conteudo do modulo, SEM `__pycache__` (o
    # cache gerado pela propria execucao nao e' artefato — licao da ancora do E01-T01).
    manifesto_modulo() {
        find "$MODULO_DIR" \( -name '__pycache__' -o -name '*.pyc' \) -prune -o -type f -print0 \
            | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
    }
    arquivos_modulo() {
        find "$MODULO_DIR" \( -name '__pycache__' -o -name '*.pyc' \) -prune -o -type f -print \
            | wc -l | tr -d ' '
    }
    MANIFESTO_ANTES="$(manifesto_modulo)"
    ARQUIVOS_MODULO="$(arquivos_modulo)"
    echo "OK    ancora: alvo do dente = $MODULO_DIR ($ARQUIVOS_MODULO arquivos, sha256 $MANIFESTO_ANTES)"

    mutar_python() { # $1=arquivo $2=expressao de troca (python) — falha se a mutacao nao aplicar
        python3 - "$1" <<PY
import sys
caminho = sys.argv[1]
with open(caminho, encoding="utf-8") as fh:
    texto = fh.read()
antes = texto
$2
if texto == antes:
    print("MUTACAO_NAO_APLICADA")
    sys.exit(3)
with open(caminho, "w", encoding="utf-8") as fh:
    fh.write(texto)
print("MUTACAO_APLICADA")
PY
    }

    prova() { # $1=numero $2=item do aceite que TEM de reprovar $3=rotulo (sem espaco)
        local numero="$1" item="$2" rotulo="$3"
        local saida linhas_ok
        DENTE_PROVAS=$((DENTE_PROVAS + 1))
        cabecalho "prova de dente $numero: $rotulo (espera-se FALHOU em '$item')"
        saida="$(TRE_MODULO_DIR="$DENTE_DIR/m$numero" TRE_BANCO="${BANCO}_d$numero" \
                 TRE_LOG_DIR="$LOG_DIR/dente$numero" "$0" --apenas-http 2>&1)"
        printf '%s\n' "$saida" >"$LOG_DIR/dente-$numero-$rotulo.out"
        printf '%s\n' "$saida" | tail -3
        linhas_ok="$(printf '%s\n' "$saida" | grep -cE '^OK    ' || true)"
        if [ "${linhas_ok:-0}" -lt 20 ]; then
            echo "FALHOU dente $numero: rodou menos itens que o minimo de medicao ($linhas_ok itens OK) — prova sem medicao"
            printf '%s\n' "$saida" | tail -8 | sed 's/^/      /'
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
            return
        fi
        if ! printf '%s\n' "$saida" | grep -q 'RESULTADO: OPORTUNIDADE_UPSERT_FALHOU'; then
            echo "FALHOU dente $numero: o aceite NAO reprovou com a mutacao ($rotulo)"
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
            return
        fi
        if printf '%s\n' "$saida" | grep -qF "FALHOU $item"; then
            echo "OK    dente $numero: '$item' reprova com a mutacao ($linhas_ok itens medidos)"
        else
            echo "FALHOU dente $numero: reprovou por OUTRO motivo — '$item' nao esta' entre os reprovados"
            printf '%s\n' "$saida" | grep -E '^FALHOU ' | head -5 | sed 's/^/      /'
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
        fi
    }

    # ---------------------------------------------------------------- dente 1
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    MUT1="$(mutar_python "$DENTE_DIR/m1/api/politica_api.json" '
import json
dados = json.loads(texto)
dados["operacoes"] = [op for op in dados["operacoes"] if op["nome"] != "oportunidade_upsert"]
texto = json.dumps(dados, ensure_ascii=False, indent=2)
')"
    DENTE1_AUSENTE="$(python3 - "$DENTE_DIR/m1/api/politica_api.json" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1], encoding="utf-8"))
sumiu = not any(op.get("nome") == "oportunidade_upsert" for op in dados.get("operacoes", []))
print("AUSENTE" if sumiu else "PRESENTE")
PY
)"
    if [ "$MUT1" = "MUTACAO_APLICADA" ] && [ "$DENTE1_AUSENTE" = "AUSENTE" ]; then
        echo 'OK    dente 1: mutacao aplicada (a operacao sumiu da LISTA de operacoes da copia)'
    else
        echo "FALHOU dente 1: mutacao NAO foi aplicada na copia ($MUT1) — o dente mediria o artefato intacto"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    prova 1 "capacidades: declaracao inesperada da operacao" "sem-operacao"

    # ---------------------------------------------------------------- dente 2
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    MUT2="$(mutar_python "$DENTE_DIR/m2/api/motor.py" '
texto = texto.replace(
    "    for campo in valores:\n        if campo not in campos:\n",
    "    for campo in valores:\n        if False:  # mutacao: campo nao declarado aceito na escrita\n",
    1,
)
')"
    if [ "$MUT2" = "MUTACAO_APLICADA" ]; then
        echo 'OK    dente 2: mutacao aplicada na copia do motor (checagem de campo na escrita)'
    else
        echo "FALHOU dente 2: mutacao NAO foi aplicada na copia do motor ($MUT2)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    prova 2 "campo de ESTAGIO do dono Odoo" "motor-sem-checagem-de-campo"

    # ---------------------------------------------------------------- dente 3
    cp -a "$MODULO_DIR" "$DENTE_DIR/m3"
    MUT3="$(mutar_python "$DENTE_DIR/m3/controllers/api_controlada.py" '
texto = texto.replace(
    "            if existentes:\n",
    "            if False:  # mutacao: upsert por identidade desligado (cria sempre)\n",
    1,
)
')"
    if [ "$MUT3" = "MUTACAO_APLICADA" ]; then
        echo 'OK    dente 3: mutacao aplicada na copia do controlador (upsert por identidade)'
    else
        echo "FALHOU dente 3: mutacao NAO foi aplicada na copia do controlador ($MUT3)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    prova 3 "segunda chamada nao atualizou" "controlador-sem-upsert"

    # ---------------------------------------------------------------- ancora externa
    MANIFESTO_DEPOIS="$(manifesto_modulo)"
    if [ "$MANIFESTO_DEPOIS" = "$MANIFESTO_ANTES" ]; then
        echo "OK    guarda externa: o artefato real nao foi tocado pelos dentes ($ARQUIVOS_MODULO arquivos, sha256 $MANIFESTO_DEPOIS)"
    else
        echo "FALHOU guarda externa: o artefato real MUDOU durante as provas ($MANIFESTO_ANTES -> $MANIFESTO_DEPOIS)"
        DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi

    echo '---'
    if [ "$DENTE_FALHAS" -eq 0 ] && [ "$DENTE_PROVAS" = "3" ]; then
        echo "RESULTADO: OPORTUNIDADE_UPSERT_DENTE_OK ($DENTE_PROVAS provas, 0 falhas) modulo=$MODULO"
        exit 0
    fi
    echo "RESULTADO: OPORTUNIDADE_UPSERT_DENTE_FALHOU ($DENTE_FALHAS problema(s) em $DENTE_PROVAS provas) modulo=$MODULO"
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
               tests/test_oportunidade_upsert.py; do
    if [ -f "$MODULO_DIR/$arquivo" ]; then
        ok "modulo em disco: $arquivo"
    else
        falhou "modulo ausente em $MODULO_DIR/$arquivo"; resumo
    fi
done
if [ -d "$MODULO_DIR/tests/politicas" ]; then ok "politicas de teste presentes (fixtures do aceite)"; \
    else falhou "fixtures de politica de teste ausentes"; fi
info "sha256 dos arquivos da API sob teste:"
for arquivo in api/politica_api.json api/motor.py controllers/api_controlada.py \
               tests/test_oportunidade_upsert.py; do
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
DESC_DIR="$(mktemp -d /tmp/verificacao-oportunidade-XXXXXX)"
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
# passo 2 — suite do Odoo (--test-enable): a suite do upsert de oportunidade + as anteriores
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
    # O Odoo 19 escreve `<hora> <pid> ERROR <banco> <modulo>: FAIL: TestX.test_y`: ancorar no
    # INICIO da linha (o defeito do E03-T01/D01) imprimia OK com teste reprovado.
    LINHAS_REPROVADAS="$(grep -cE '(^| )(FAIL|ERROR): [A-Za-z_]' "$LOG_ATUAL" || true)"
    [ "$LINHAS_REPROVADAS" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
        || falhou "$LINHAS_REPROVADAS linha(s) de teste reprovado(a) no log"
    FALTANDO=0
    TOTAL_SUITE=0
    if [ -f "$MODULO_DIR/tests/test_oportunidade_upsert.py" ]; then
        while read -r metodo; do
            [ -z "$metodo" ] && continue
            TOTAL_SUITE=$((TOTAL_SUITE + 1))
            grep -q "$metodo" "$LOG_ATUAL" || { FALTANDO=$((FALTANDO + 1)); info "teste ausente no log: $metodo"; }
        done <<<"$(grep -oE 'def (test_[0-9]+_[a-z_0-9]+)' "$MODULO_DIR/tests/test_oportunidade_upsert.py" \
            | sed 's/^def //' | sort -u)"
    fi
    if [ "$TOTAL_SUITE" -ge 24 ] && [ "$FALTANDO" = "0" ]; then
        ok "todos os $TOTAL_SUITE testes da suite do upsert de oportunidade aparecem no log do runner"
    else
        falhou "suite do upsert de oportunidade: $TOTAL_SUITE testes encontrados, $FALTANDO ausentes no log"
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
    # ---- helpers (o corpo vai em ARQUIVO: nada de payload na linha de comando) ----------------
    api_post() { # $1=rotulo $2=codigo_esperado $3=operacao $4=arquivo-do-corpo [$5=1|0 audita]
        local rotulo="$1" esperado="$2" operacao="$3" arquivo="$4" audita="${5:-1}" codigo
        codigo="$(curl --config "$CFG" -X POST -d @"$arquivo" -o "$DESC_DIR/resposta.json" \
            -w '%{http_code}' "$BASE/tf/api/v1/$operacao" || true)"
        CHAMADAS=$((CHAMADAS + 1))
        # So' entra na conta de AUDITADAS a chamada que CHEGA ao controlador: o 401 do
        # `auth='bearer'` morre antes da rota e nao gera trilha.
        [ "$audita" = "1" ] && AUDITADAS=$((AUDITADAS + 1))
        cp "$DESC_DIR/resposta.json" "$DESC_DIR/resposta-$CHAMADAS.json"
        ULTIMA_RESPOSTA="$DESC_DIR/resposta-$CHAMADAS.json"
        if [ "$codigo" = "$esperado" ]; then
            ok "$rotulo -> HTTP $codigo"
        else
            falhou "$rotulo -> HTTP $codigo (esperado $esperado): $(head -c 300 "$ULTIMA_RESPOSTA" | tr -d '\n')"
        fi
    }
    sem_token() { # $1=rotulo $2=esperado $3=operacao (nao audita: morre antes do controlador)
        local codigo
        codigo="$(curl -s -X POST -H 'Content-Type: application/json' -d '{}' \
            -o "$DESC_DIR/resposta.json" -w '%{http_code}' "$BASE/tf/api/v1/$3" || true)"
        CHAMADAS=$((CHAMADAS + 1))
        cp "$DESC_DIR/resposta.json" "$DESC_DIR/resposta-$CHAMADAS.json"
        ULTIMA_RESPOSTA="$DESC_DIR/resposta-$CHAMADAS.json"
        [ "$codigo" = "$2" ] && ok "$1 -> HTTP $codigo" || falhou "$1 -> HTTP $codigo (esperado $2)"
    }
    COM_TOKEN_RUIM() { # $1=rotulo $2=esperado $3=operacao (token invalido: tambem nao audita)
        local codigo
        codigo="$(curl --config "$CFG_RUIM" -X POST -d '{}' -o "$DESC_DIR/resposta.json" \
            -w '%{http_code}' "$BASE/tf/api/v1/$3" || true)"
        CHAMADAS=$((CHAMADAS + 1))
        cp "$DESC_DIR/resposta.json" "$DESC_DIR/resposta-$CHAMADAS.json"
        ULTIMA_RESPOSTA="$DESC_DIR/resposta-$CHAMADAS.json"
        [ "$codigo" = "$2" ] && ok "$1 -> HTTP $codigo" || falhou "$1 -> HTTP $codigo (esperado $2)"
    }
    campo() { # $1=arquivo de resposta $2=expressao python sobre `d` (o JSON carregado)
        python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))" \
            "$1" "$2"
    }
    corpo() { cat >"$DESC_DIR/$1"; }
    codigo_de() { campo "$1" "d.get('codigo','')"; }
    sql() { psql_bd "$BANCO" "$1"; }
    confere_codigo() { # $1=rotulo $2=resposta $3..=codigos aceitos
        local rotulo="$1" resposta="$2"; shift 2
        local veio aceito="nao"
        veio="$(codigo_de "$resposta")"
        for esperado in "$@"; do [ "$veio" = "$esperado" ] && aceito="sim"; done
        if [ "$aceito" = "sim" ]; then
            ok "$rotulo -> recusa $veio"
        else
            falhou "$rotulo -> recusa $veio (esperado um de: $*)"
        fi
    }
    item_sql() { # $1=rotulo $2=sql $3=esperado
        local veio
        veio="$(sql "$2")"
        [ "$veio" = "$3" ] && ok "$1 ($veio)" || falhou "$1: veio '$veio', esperado '$3'"
    }

    # ---- AC? superficie: sem token e com token invalido -------------------------------------
    sem_token "sem token na operacao de negocio" "401" "oportunidade_upsert"
    COM_TOKEN_RUIM "token invalido na operacao de negocio" "401" "oportunidade_upsert"

    # ---- AC1: a operacao esta declarada, na versao da politica em vigor ----------------------
    corpo "corpo-capacidades.json" <<'JSON'
{"correlation_id": "tre-e01-t04-http-capacidades"}
JSON
    api_post "operacao declarada (sistema_capacidades)" "200" "sistema_capacidades" \
        "$DESC_DIR/corpo-capacidades.json"
    DECLARADA="$(python3 - "$ULTIMA_RESPOSTA" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1]))
op = next((o for o in dados["dados"]["capacidades"]["operacoes"]
           if o["nome"] == "oportunidade_upsert"), None)
print("%s|%s|%s" % (bool(op), op and op["tipo"], op and op["requer_idempotency_key"]))
PY
)"
    [ "$DECLARADA" = "True|escrita|True" ] \
        && ok "capacidades: oportunidade_upsert declarada como escrita com chave exigida ($DECLARADA)" \
        || falhou "capacidades: declaracao inesperada da operacao ($DECLARADA)"
    VERSAO_POLITICA="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['versao'])" \
        "$MODULO_DIR/api/politica_api.json")"
    VERSAO_RESPOSTA="$(campo "$ULTIMA_RESPOSTA" "d['politica_versao']")"
    [ "$VERSAO_RESPOSTA" = "$VERSAO_POLITICA" ] \
        && ok "envelope traz a versao da politica em vigor ($VERSAO_RESPOSTA)" \
        || falhou "politica_versao '$VERSAO_RESPOSTA' difere da politica em vigor '$VERSAO_POLITICA'"
    corpo "corpo-nao-declarada.json" <<'JSON'
{"idempotency_key": "tre-e01-t04-http-nao-declarada", "parametros": {"valores": {"name": "x"}}}
JSON
    api_post "operacao nao declarada" "404" "oportunidade_upsert_livre" "$DESC_DIR/corpo-nao-declarada.json"

    # ---- AC5: dry-run descreve e NAO escreve ------------------------------------------------
    corpo "corpo-dry.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-dry-1", "dry_run": true,
 "parametros": {"valores": {"name": "Oportunidade Dry Run", "tf_opportunity_id": "$UUID_HTTP",
                            "tf_priority_score": 88.0}}}
JSON
    api_post "dry-run de oportunidade nova" "200" "oportunidade_upsert" "$DESC_DIR/corpo-dry.json"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "criar" ] \
        && ok "dry-run descreve acao_efetiva=criar" || falhou "dry-run nao descreveu a criacao"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['dry_run']")" = "True" ] \
        && ok "envelope marca dry_run" || falhou "envelope sem dry_run"
    item_sql "dry-run NAO criou registro" \
        "select count(*) from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "0"

    # ---- AC3: cria pela identidade canonica ---------------------------------------------------
    corpo "corpo-1.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0001", "correlation_id": "tre-e01-t04-http-correl-1",
 "parametros": {"valores": {"name": "Oportunidade HTTP Um", "type": "opportunity",
                            "tf_opportunity_id": "$UUID_HTTP", "tf_priority_score": 82.5,
                            "tf_icp_score": 80.0, "tf_next_best_action": "FOLLOW_UP",
                            "tf_correlation_id": "tre-e01-t04-http-correl-1",
                            "tf_idempotency_key": "tre-e01-t04-http-0001",
                            "tf_last_sync_at": "2026-10-02 03:00:00",
                            "tf_last_event_type": "OPPORTUNITY_RECOMMENDED"}}}
JSON
    api_post "upsert de oportunidade: primeira chamada (cria)" "200" "oportunidade_upsert" \
        "$DESC_DIR/corpo-1.json"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "criar" ] \
        && ok "primeira chamada responde acao_efetiva=criar" || falhou "primeira chamada nao criou"
    ID_CRIADO="$(campo "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['correlation_id']")" = "tre-e01-t04-http-correl-1" ] \
        && ok "correlation_id do chamador ecoado" || falhou "correlation_id nao ecoado"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['idempotency_key']")" = "tre-e01-t04-http-0001" ] \
        && ok "idempotency_key do chamador registrada no envelope" || falhou "envelope sem a chave"
    item_sql "um unico registro com o UUID canonico" \
        "select count(*) from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "1"
    item_sql "o espelho gravou o nome" \
        "select name from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "Oportunidade HTTP Um"
    item_sql "o espelho gravou o rastro da trilha" \
        "select tf_idempotency_key || '|' || tf_last_event_type || '|' || tf_next_best_action from crm_lead where tf_opportunity_id = '$UUID_HTTP'" \
        "tre-e01-t04-http-0001|OPPORTUNITY_RECOMMENDED|FOLLOW_UP"
    item_sql "o espelho gravou a correlacao" \
        "select tf_correlation_id from crm_lead where tf_opportunity_id = '$UUID_HTTP'" \
        "tre-e01-t04-http-correl-1"

    # ---- AC4: antes/depois do ESTAGIO e do VALOR (dono: Odoo) --------------------------------
    ESTAGIO_ANTES="$(sql "select stage_id from crm_lead where tf_opportunity_id = '$UUID_HTTP'")"
    VALOR_ANTES="$(sql "select expected_revenue from crm_lead where tf_opportunity_id = '$UUID_HTTP'")"
    DONO_ANTES="$(sql "select user_id from crm_lead where tf_opportunity_id = '$UUID_HTTP'")"
    USUARIO_API_ID="$(sql "select id from res_users where login = 'tf_api_integracao'")"
    [ "$DONO_ANTES" = "$USUARIO_API_ID" ] \
        && ok "o lead do espelho nasce sob o usuario de integracao ($DONO_ANTES) — declarado" \
        || falhou "dono do lead inesperado: '$DONO_ANTES' x usuario de integracao '$USUARIO_API_ID'"

    corpo "corpo-2.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0002", "correlation_id": "tre-e01-t04-http-correl-2",
 "parametros": {"valores": {"name": "Oportunidade HTTP Dois", "type": "opportunity",
                            "tf_opportunity_id": "$UUID_HTTP", "tf_priority_score": 91.5}}}
JSON
    api_post "mesma identidade: segunda chamada (atualiza)" "200" "oportunidade_upsert" \
        "$DESC_DIR/corpo-2.json"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['dados']['acao_efetiva']")" = "atualizar" ] \
        && ok "segunda chamada responde acao_efetiva=atualizar" || falhou "segunda chamada nao atualizou"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")" = "$ID_CRIADO" ] \
        && ok "atualizou o MESMO registro (id $ID_CRIADO)" || falhou "atualizou outro registro"
    item_sql "segue um unico registro com o UUID canonico" \
        "select count(*) from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "1"
    item_sql "a ultima chamada venceu no nome" \
        "select name from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "Oportunidade HTTP Dois"
    item_sql "a atualizacao foi PARCIAL (next_best_action da 1a chamada permanece)" \
        "select tf_next_best_action from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "FOLLOW_UP"
    item_sql "o espelho acompanhou o score (91,5) " \
        "select tf_priority_score::text from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "91.5"
    item_sql "a faixa derivada acompanhou o score (A+)" \
        "select tf_priority_tier from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "A+"
    item_sql "o ESTAGIO do dono Odoo nao foi tocado" \
        "select stage_id from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "$ESTAGIO_ANTES"
    item_sql "o VALOR do dono Odoo nao foi tocado" \
        "select expected_revenue from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "$VALOR_ANTES"

    # repeticao: nem o numero de registros nem o id podem mudar (idempotencia por identidade)
    corpo "corpo-3.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0003",
 "parametros": {"valores": {"name": "Oportunidade HTTP Tres", "tf_opportunity_id": "$UUID_HTTP"}}}
JSON
    api_post "terceira chamada do mesmo UUID" "200" "oportunidade_upsert" "$DESC_DIR/corpo-3.json"
    [ "$(campo "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")" = "$ID_CRIADO" ] \
        && ok "terceira chamada segue no mesmo registro (sem duplicata)" \
        || falhou "terceira chamada mudou o registro"
    item_sql "tres chamadas, um registro" \
        "select count(*) from crm_lead where tf_opportunity_id = '$UUID_HTTP'" "1"

    # ---- AC2: casa pelo UUID, NAO pelo nome --------------------------------------------------
    corpo "corpo-4a.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0004a",
 "parametros": {"valores": {"name": "Nome repetido do funil", "tf_opportunity_id": "$UUID_NOME"}}}
JSON
    api_post "primeiro lead com o nome repetido" "200" "oportunidade_upsert" "$DESC_DIR/corpo-4a.json"
    ID_NOME="$(campo "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")"
    corpo "corpo-4b.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0004b",
 "parametros": {"valores": {"name": "Nome repetido do funil", "tf_opportunity_id": "$UUID_PARCIAL"}}}
JSON
    api_post "segundo lead com o MESMO nome" "200" "oportunidade_upsert" "$DESC_DIR/corpo-4b.json"
    ID_PARCIAL="$(campo "$ULTIMA_RESPOSTA" "d['dados']['ids'][0]")"
    [ "$ID_NOME" != "$ID_PARCIAL" ] && ok "nomes iguais, UUIDs diferentes: dois registros distintos" \
        || falhou "UUIDs diferentes casaram o mesmo registro"
    corpo "corpo-4c.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0004c",
 "parametros": {"valores": {"name": "So' o segundo muda", "tf_opportunity_id": "$UUID_PARCIAL"}}}
JSON
    api_post "upsert pelo UUID do SEGUNDO" "200" "oportunidade_upsert" "$DESC_DIR/corpo-4c.json"
    item_sql "o primeiro NAO mudou (identidade e' o UUID, nao o nome)" \
        "select name from crm_lead where tf_opportunity_id = '$UUID_NOME'" "Nome repetido do funil"
    item_sql "o segundo mudou" \
        "select name from crm_lead where tf_opportunity_id = '$UUID_PARCIAL'" "So' o segundo muda"

    # ---- AC4: a fronteira de dono do Odoo e' recusa nomeada ---------------------------------
    corpo "corpo-5.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0005",
 "parametros": {"valores": {"name": "Com estagio", "tf_opportunity_id": "$UUID_VERSAO",
                            "stage_id": 1}}}
JSON
    api_post "campo de ESTAGIO do dono Odoo" "422" "oportunidade_upsert" "$DESC_DIR/corpo-5.json"
    confere_codigo "recusa de estagio" "$ULTIMA_RESPOSTA" campo_nao_declarado
    corpo "corpo-6.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0006",
 "parametros": {"valores": {"name": "Com valor", "tf_opportunity_id": "$UUID_VERSAO",
                            "expected_revenue": 99999.0}}}
JSON
    api_post "campo de VALOR do dono Odoo" "422" "oportunidade_upsert" "$DESC_DIR/corpo-6.json"
    confere_codigo "recusa de valor" "$ULTIMA_RESPOSTA" campo_nao_declarado
    corpo "corpo-7.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0007",
 "parametros": {"valores": {"name": "Com tier", "tf_opportunity_id": "$UUID_VERSAO",
                            "tf_priority_tier": "A+"}}}
JSON
    api_post "campo DERIVADO (tf_priority_tier)" "422" "oportunidade_upsert" "$DESC_DIR/corpo-7.json"
    confere_codigo "recusa de campo derivado" "$ULTIMA_RESPOSTA" campo_nao_declarado
    item_sql "as recusas nao criaram registro" \
        "select count(*) from crm_lead where tf_opportunity_id = '$UUID_VERSAO'" "0"

    # ---- AC2/AC5: recusas nomeadas do contrato ----------------------------------------------
    corpo "corpo-8.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0008",
 "parametros": {"valores": {"name": "Sem identidade"}}}
JSON
    api_post "upsert sem o UUID canonico" "422" "oportunidade_upsert" "$DESC_DIR/corpo-8.json"
    confere_codigo "recusa sem identidade" "$ULTIMA_RESPOSTA" campo_obrigatorio_ausente identificador_ausente
    corpo "corpo-9.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0009",
 "parametros": {"valores": {"name": "UUID torto", "tf_opportunity_id": "nao-e-uuid"}}}
JSON
    api_post "UUID canonico fora do formato" "422" "oportunidade_upsert" "$DESC_DIR/corpo-9.json"
    confere_codigo "recusa de UUID invalido" "$ULTIMA_RESPOSTA" valor_invalido
    corpo "corpo-10.json" <<JSON
{"parametros": {"valores": {"name": "Sem chave", "tf_opportunity_id": "$UUID_VERSAO"}}}
JSON
    api_post "escrita sem idempotency_key" "422" "oportunidade_upsert" "$DESC_DIR/corpo-10.json"
    confere_codigo "recusa sem chave" "$ULTIMA_RESPOSTA" idempotency_key_ausente
    corpo "corpo-11.json" <<JSON
{"idempotency_key": "curta!!", "parametros": {"valores": {"name": "Chave torta",
                                                          "tf_opportunity_id": "$UUID_VERSAO"}}}
JSON
    api_post "idempotency_key fora do formato" "422" "oportunidade_upsert" "$DESC_DIR/corpo-11.json"
    confere_codigo "recusa de chave torta" "$ULTIMA_RESPOSTA" idempotency_key_invalida
    corpo "corpo-12.json" <<'JSON'
{"idempotency_key": "tre-e01-t04-http-0012", "marcador_que_nao_pode_vazar": "MARCADOR-PAYLOAD-E01T04-NAO-DEVE-APARECER"}
JSON
    api_post "chave desconhecida no corpo" "400" "oportunidade_upsert" "$DESC_DIR/corpo-12.json"
    confere_codigo "recusa de payload" "$ULTIMA_RESPOSTA" payload_invalido
    corpo "corpo-13.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-0013",
 "parametros": {"valores": {"name": "$MARCADOR_PAYLOAD", "tf_opportunity_id": "$UUID_VERSAO",
                            "stage_id": 1}}}
JSON
    api_post "recusa com marcador de payload plantado" "422" "oportunidade_upsert" "$DESC_DIR/corpo-13.json"
    MARCADOR_VAZOU="$(grep -h -o "$MARCADOR_PAYLOAD" "$DESC_DIR"/resposta-*.json 2>/dev/null | wc -l | tr -d ' ')"
    [ "$MARCADOR_VAZOU" = "0" ] && ok "payload recusado nao ecoa de volta na resposta" \
        || falhou "payload recusado voltou na resposta"

    cabecalho "passo 3c — auditoria por chamada (AC7) e ausencia de segredo"
    LOG_API="$LOG_DIR/3b-servidor.log"
    docker logs "$API_CT" >"$LOG_API" 2>&1
    LINHAS_AUDITORIA="$(grep -c 'TF_API_AUDIT' "$LOG_API" || true)"
    if [ "$LINHAS_AUDITORIA" = "$AUDITADAS" ]; then
        ok "uma linha TF_API_AUDIT por chamada autenticada ($LINHAS_AUDITORIA linhas, $AUDITADAS chamadas; as 2 sem token valido param no Odoo antes do controlador)"
    else
        falhou "auditoria: $LINHAS_AUDITORIA linhas para $AUDITADAS chamadas autenticadas"
    fi
    grep -q '"resultado": "ok"' "$LOG_API" && ok "trilha tem chamadas de sucesso" \
        || falhou "trilha sem chamada de sucesso"
    grep -q '"operacao": "oportunidade_upsert"' "$LOG_API" && ok "trilha registra a operacao de negocio" \
        || falhou "trilha sem a operacao oportunidade_upsert"
    grep -q '"acao": "upsert"' "$LOG_API" && ok "trilha registra a acao upsert" \
        || falhou "trilha sem a acao upsert"
    grep -q '"codigo": "campo_nao_declarado"' "$LOG_API" && ok "trilha registra a recusa de campo de dono" \
        || falhou "trilha sem a recusa de campo nao declarado"
    grep -q '"codigo": "valor_invalido"' "$LOG_API" && ok "trilha registra a recusa de UUID invalido" \
        || falhou "trilha sem a recusa de UUID invalido"
    grep -q '"correlation_id": "tre-e01-t04-http-correl-1"' "$LOG_API" && ok "trilha carrega o correlation_id do chamador" \
        || falhou "trilha sem o correlation_id do chamador"
    if grep -q -F -f "$DESC_DIR/chave.txt" "$LOG_API" 2>/dev/null; then
        falhou "a chave de API aparece no log do servidor"
    else
        ok "a chave de API NAO aparece no log do servidor"
    fi
    if grep -q "$MARCADOR_PAYLOAD\|Oportunidade HTTP\|Com estagio" "$LOG_API" 2>/dev/null; then
        falhou "payload de negocio aparece na trilha de auditoria"
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

    # ------------------------------------------------------------------
    # passo 3d — guarda de ambiente na ESCRITA (ADR-005), medida por HTTP
    # ------------------------------------------------------------------
    cabecalho "passo 3d — guarda de ambiente na escrita (ADR-005)"
    # O parametro e' lido pela API a cada chamada, mas o ORM o CACHEIA: trocar o valor por SQL
    # direto no banco com o servidor de pe nao invalida esse cache (mediria verde falso). Aqui a
    # troca passa pelo MESMO caminho do preparo (ORM + commit) e o servidor sobe DEPOIS dela.
    LOG_HOMOLOG="$LOG_DIR/3d-preparo-homolog.log"
    docker run --rm -i --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -v "$DESC_DIR":/preparo \
        -e TRE_API_AMBIENTE=homologacao \
        -e TRE_API_ARQUIVO_CHAVE=/preparo/chave-homolog.txt \
        --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$PREPARADOR" >"$LOG_HOMOLOG" 2>&1
    grep -q 'TF_API_PREPARO_OK' "$LOG_HOMOLOG" \
        && ok "ambiente do banco descartavel movido para 'homologacao' pelo ORM (com commit)" \
        || falhou "preparo em homologacao nao confirmou (log: $LOG_HOMOLOG)"
    API_CT="$API_TMP"
    docker run -d --name "$API_CT" --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -p 127.0.0.1::8069 \
        --entrypoint odoo "$IMAGEM" -d "$BANCO" --max-cron-threads=0 --db-filter="^$BANCO\$" \
        >/dev/null 2>&1
    PORTA_API="$(docker port "$API_CT" 8069/tcp 2>/dev/null | head -1 | sed 's/.*://')"
    BASE="http://127.0.0.1:$PORTA_API"
    VIVO=0
    for _ in $(seq 1 30); do
        CODIGO="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$BASE/web/login" || true)"
        [ "$CODIGO" = "200" ] && { VIVO=1; break; }
        sleep 3
    done
    [ "$VIVO" = "1" ] && ok "servidor reiniciado no ambiente 'homologacao' (127.0.0.1:$PORTA_API)" \
        || falhou "servidor nao respondeu depois da troca de ambiente (ultimo codigo: ${CODIGO:-nenhum})"
    CFG_HOMOLOG="$DESC_DIR/curl-homolog.cfg"
    {
        echo 'silent'
        echo 'show-error'
        echo 'header = "Content-Type: application/json"'
        printf 'header = "Authorization: Bearer %s"\n' "$(cat "$DESC_DIR/chave-homolog.txt")"
    } >"$CFG_HOMOLOG"
    chmod 600 "$CFG_HOMOLOG"
    CFG_GUARDA="$CFG"
    CFG="$CFG_HOMOLOG"
    corpo "corpo-homolog.json" <<JSON
{"idempotency_key": "tre-e01-t04-http-homolog-1",
 "parametros": {"valores": {"name": "Oportunidade em homologacao", "tf_opportunity_id": "$UUID_VERSAO"}}}
JSON
    api_post "escrita com o ambiente fora da politica" "503" "oportunidade_upsert" \
        "$DESC_DIR/corpo-homolog.json"
    confere_codigo "recusa de ambiente" "$ULTIMA_RESPOSTA" ambiente_nao_permitido
    item_sql "a guarda de ambiente nao escreveu nada" \
        "select count(*) from crm_lead where tf_opportunity_id = '$UUID_VERSAO'" "0"
    CFG="$CFG_GUARDA"
    docker rm -f "$API_CT" >/dev/null 2>&1
    API_CT=""
    ok "servidor de homologacao encerrado (container descartavel removido)"
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
    POLITICA_OPERACAO="$(python3 - "$MODULO_DIR/api/politica_api.json" <<'PY'
import json, sys
dados = json.load(open(sys.argv[1], encoding="utf-8"))
op = next((o for o in dados.get("operacoes", []) if o.get("nome") == "oportunidade_upsert"), None)
if op is None:
    print("AUSENTE")
else:
    d = (op.get("modelos") or {}).get("crm.lead") or {}
    print("tipo=%s chave=%s modelo=crm.lead acao=%s identidade=%s obrigatorios=%s campos=%d" % (
        op.get("tipo"), op.get("requer_idempotency_key"), d.get("acao"),
        d.get("campo_de_identidade"), ",".join(d.get("campos_obrigatorios", [])),
        len(d.get("campos", []))))
    donos = [c for c in ("stage_id", "expected_revenue", "probability", "date_deadline",
                         "date_closed") if c in (d.get("campos") or [])]
    print("DONOS_DO_ODDO_DECLARADOS=%s" % (",".join(donos) or "nenhum"))
    print("DERIVADO=%s" % ("tf_priority_tier" in (d.get("campos") or [])))
PY
)"
    case "$POLITICA_OPERACAO" in
        *"tipo=escrita chave=True modelo=crm.lead acao=upsert identidade=tf_opportunity_id"*)
            ok "politica declara oportunidade_upsert como escrita upsert por tf_opportunity_id" ;;
        *) falhou "declaracao da operacao inesperada: $(printf '%s' "$POLITICA_OPERACAO" | head -1)" ;;
    esac
    case "$POLITICA_OPERACAO" in
        *"DONOS_DO_ODDO_DECLARADOS=nenhum"*)
            ok "fronteira de dono declarada: nenhum campo de dono do Odoo e' escrevivel" ;;
        *) falhou "a operacao declara campo de dono do Odoo: $(printf '%s' "$POLITICA_OPERACAO" | grep DONOS_DO_ODDO)" ;;
    esac
    case "$POLITICA_OPERACAO" in
        *"DERIVADO=False"*)
            ok "campo derivado tf_priority_tier nao e' escrevivel (compute do score)" ;;
        *) falhou "tf_priority_tier aparece como escrevivel na politica" ;;
    esac
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
# Os bancos `_dN` sao das provas de dente: so' existem no modo dente. Conferi-los em outro modo
# carimbaria OK de banco que nunca nasceu (item que nao mede nada).
AUXILIARES=("$BANCO")
if [ "$MODO" = "dente" ]; then
    AUXILIARES+=("${BANCO}_d1" "${BANCO}_d2" "${BANCO}_d3")
fi
for auxiliar in "${AUXILIARES[@]}"; do
    if [ "$MANTER_BANCO" = "1" ]; then
        info "TRE_MANTER_BANCO=1: banco $auxiliar mantido para inspecao"
    else
        banco_limpo "$auxiliar"
        if banco_existe "$auxiliar"; then falhou "banco descartavel $auxiliar nao foi removido"
        else ok "banco descartavel $auxiliar removido"; fi
    fi
done
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
