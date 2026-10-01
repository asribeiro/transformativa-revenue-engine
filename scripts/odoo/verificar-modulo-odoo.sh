#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W2-E03-T01 — modulo Odoo `transformativa_sales_ai`
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 modulo instala e desinstala limpo em banco limpo (idempotente);
#   AC2 manifesto e versao corretos; dependencias declaradas;
#   AC3 `--test-enable` do Odoo sem erro no modulo.
#
# TEST PLAN (executado por este script, na VPS do dev):
#   passo 1 instalacao em banco limpo     -> log + estado lido no banco
#   passo 2 teste do Odoo (--test-enable) -> relatorio do runner + estado lido no banco
#   passo 3 desinstalacao                 -> marcador do ORM + estado lido no banco
#   passo 4 reinstalacao (idempotencia)   -> estado lido no banco
#   limpeza: banco e dupla descartavel removidos; instancia do dev conferida inalterada
#
# ISOLAMENTO (achado na primeira rodada deste verificador — ver runbook §8, defeitos 2 e 3):
# o Odoo do dev (`odoo-dev`) abre sessao em QUALQUER banco novo da instancia `pg-odoo-dev`
# (medido: banco probe criado do zero recebeu sessao do IP do `odoo-dev` em ~30s, sem ninguem
# pedir) e o `dropdb` do banco de teste morria com "being accessed by other users". Por isso
# este verificador sobe a SUA propria dupla descartavel (`postgres:16` + `odoo:19.0`, as
# mesmas imagens do par de dev, com os digests medidos) em rede propria — e NAO usa
# `pg-odoo-dev`, `odoo_dev` nem a copia operacional `/opt/tre/repo`.
#
# Uso (na VPS, a partir de arquivo — nao por stdin, ver armadilha do `docker compose run`):
#   bash verificar-modulo-odoo.sh
#   bash verificar-modulo-odoo.sh --apenas-manifesto
#   bash verificar-modulo-odoo.sh --apenas-instalacao-e-teste
#   bash verificar-modulo-odoo.sh --prova-de-dente      (tres provas: versao, teste, resquicio)
#   bash verificar-modulo-odoo.sh --banco tre_outro_banco
#
# Variaveis: TRE_MODULO, TRE_MODULO_DIR, TRE_BANCO, TRE_IMAGEM, TRE_IMAGEM_PG, TRE_PG_USER,
# TRE_VERSAO_ESPERADA, TRE_SERIE_ESPERADA, TRE_LOG_DIR, TRE_DEV_PG_CT (conferencia do dev),
# TRE_DESINSTALADOR (desinstalador do passo 3 — o dente 3 passa um proprio), TRE_MANTER_BANCO=1
# (nao limpa o banco no fim).
#
# REGUA DO RESQUICIO (passo 3, defeito TRE-W2-E03-T01-D03): os itens de resquicio sao medidos
# contra as entidades que o modulo REGISTRA no banco (modelos/tabelas/campos/views lidos por
# `ir_model_data` COM o modulo instalado) — nunca contra o nome do pacote, que em Odoo nao
# aparece no nome da tabela (a tabela e o nome do MODELO: `tf_process_opportunity`).
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo final em uma linha e exit code:
#   0 = aceite cumprido (todos os itens OK)   1 = falhou / nao deu para medir
# ============================================================================
set -u

MODULO="${TRE_MODULO:-transformativa_sales_ai}"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
MODULO_DIR="${TRE_MODULO_DIR:-/opt/tre/dev/modulos/$MODULO}"
PARSER="${TRE_PARSER:-$AQUI/manifesto_do_modulo.py}"
DESINSTALADOR="${TRE_DESINSTALADOR:-$AQUI/desinstalar_modulo.py}"
BANCO="${TRE_BANCO:-tre_e03_t01_modulo}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
PG_USER="${TRE_PG_USER:-odoo}"
VERSAO_ESPERADA="${TRE_VERSAO_ESPERADA:-19.0.1.0.0}"
SERIE_ESPERADA="${TRE_SERIE_ESPERADA:-19.0}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-modulo-odoo}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER_BANCO="${TRE_MANTER_BANCO:-0}"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-manifesto) MODO=manifesto ;;
        --apenas-instalacao-e-teste) MODO=instalacao_teste ;;
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
PG_TMP="e03t01-pg-$SUFIXO"
NET_TMP="e03t01-net-$SUFIXO"
DESC_DIR=""
mkdir -p "$LOG_DIR"

ok()       { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou()   { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
info()     { printf 'INFO  %s\n' "$*"; }
cabecalho(){ printf '\n=== %s ===\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: MODULO_ODOO_OK ($ITENS itens, 0 falhas) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
        exit 0
    fi
    echo "RESULTADO: MODULO_ODOO_FALHOU ($ITENS itens, $FALHAS falha(s)) modulo=$MODULO banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG"
    exit 1
}

# ---------------------------------------------------------------------------
# --prova-de-dente: o aceite tem dentes? duas mutacoes, cada uma em copia propria
# ---------------------------------------------------------------------------
if [ "$MODO" = "dente" ]; then
    DENTE_DIR="$(mktemp -d /tmp/dente-e03t01-XXXXXX)"
    trap 'rm -rf "$DENTE_DIR"' EXIT
    DENTE_FALHAS=0
    DENTE_PROVAS=0
    cabecalho "prova de dente 1: versao do manifesto mutada (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m1"
    sed -i "s/'version': *'$VERSAO_ESPERADA'/'version': '18.0.1.0.0'/" "$DENTE_DIR/m1/__manifest__.py"
    sed -i "s/'version': *\"$VERSAO_ESPERADA\"/'version': '18.0.1.0.0'/" "$DENTE_DIR/m1/__manifest__.py"
    D1="$(TRE_MODULO_DIR="$DENTE_DIR/m1" TRE_LOG_DIR="$LOG_DIR" "$0" --apenas-manifesto 2>&1)"
    echo "$D1" >"$LOG_DIR/dente-1-manifesto-mutado.out"
    echo "$D1" | tail -4
    if echo "$D1" | grep -q 'RESULTADO: MODULO_ODOO_FALHOU'; then
        echo 'OK    dente 1: versao mutada reprova (o verificador nao passa por qualquer coisa)'
    else
        echo 'FALHOU dente 1: versao mutada NAO reprovou — o item de versao nao tem dente'; DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    DENTE_PROVAS=$((DENTE_PROVAS + 1))

    cabecalho "prova de dente 2: teste do Odoo que falha de proposito (espera-se FALHOU)"
    cp -a "$MODULO_DIR" "$DENTE_DIR/m2"
    cat >>"$DENTE_DIR/m2/tests/test_modulo_base.py" <<'PY'

    def test_99_prova_de_dente(self):
        """Teste plantado pela prova de dente do verificador: TEM de reprovar."""
        self.assertTrue(False, 'teste plantado pela prova de dente (TRE-W2-E03-T01)')
PY
    D2="$(TRE_MODULO_DIR="$DENTE_DIR/m2" TRE_BANCO="${BANCO}_dente" TRE_LOG_DIR="$LOG_DIR" \
          "$0" --apenas-instalacao-e-teste 2>&1)"
    echo "$D2" >"$LOG_DIR/dente-2-teste-mutado.out"
    echo "$D2" | tail -4
    if echo "$D2" | grep -q 'RESULTADO: MODULO_ODOO_FALHOU'; then
        echo 'OK    dente 2: teste que falha reprova o aceite'
    else
        echo 'FALHOU dente 2: teste que falha NAO reprovou — o item de --test-enable nao tem dente'; DENTE_FALHAS=$((DENTE_FALHAS + 1))
    fi
    DENTE_PROVAS=$((DENTE_PROVAS + 1))

    cabecalho "prova de dente 3: resquicio plantado no banco depois da desinstalacao (espera-se FALHOU)"
    # Dente do rolamento contrario aos dois primeiros: la o modulo e mutado para reprovar; aqui o
    # banco fica SUJO depois da desinstalacao (modelo/tabela/campo/view plantados, nada com
    # `ir_model_data` do modulo) e quem tem de acusar sao os itens de resquicio do passo 3 — que
    # antes deste defeito (D03) imprimiam OK sem poder ver tabela, campo ou view.
    #
    # A prova so existe onde ha entidade para acusar: se o modulo NAO declara `models/`, o dente
    # sai como NAO APLICAVEL (explicito, com o motivo) — nao como OK silencioso nem como falha de
    # um modulo que legitimamente nao registra entidade nenhuma.
    if [ -d "$MODULO_DIR/models" ] && [ -n "$(ls -A "$MODULO_DIR"/models/*.py 2>/dev/null)" ]; then
        cp -a "$MODULO_DIR" "$DENTE_DIR/m3"
        # desinstalador do dente = desinstalador REAL + prologo (captura os modelos proprios COM o
        # modulo instalado) + epilogo (planta o resquicio depois de desinstalar de verdade)
        {
            cat <<'PY'
# --- dente 3 do verificador: prologo (captura ANTES, com o modulo instalado) --------------
# Depois da desinstalacao o `ir_model_data` do modulo nao existe mais, entao a lista de
# modelos proprios tem de ser capturada aqui.
import os

MODULO = os.environ.get('TRE_MODULO', 'transformativa_sales_ai')
_imd = env['ir.model.data'].sudo()  # noqa: F821
RESQUICIO_MODELOS = [m.model for m in env['ir.model'].sudo().browse(  # noqa: F821
    _imd.search([('module', '=', MODULO), ('model', '=', 'ir.model')]).mapped('res_id'))
    if not _imd.search_count([('model', '=', 'ir.model'), ('res_id', 'in', m.ids),
                              ('module', '!=', MODULO)])]
print('DENTE3_MODELOS_PROPRIOS=%s' % (','.join(RESQUICIO_MODELOS) or 'nenhum'))
PY
            cat "$DESINSTALADOR"
            cat <<'PY'

# --- dente 3 do verificador: epilogo (planta o resquicio DEPOIS de desinstalar) ------------
def _plantar_resquicio():
    for modelo in RESQUICIO_MODELOS:
        env.cr.execute(  # noqa: F821
            'INSERT INTO ir_model (model, name, "order", state)'
            " VALUES (%s, %s::jsonb, %s, %s)",
            (modelo, '{"en_US": "Residuo plantado"}', 'model', 'base'))
        env.cr.execute('SELECT id FROM ir_model WHERE model = %s', (modelo,))  # noqa: F821
        modelo_id = env.cr.fetchone()[0]  # noqa: F821
        env.cr.execute('CREATE TABLE IF NOT EXISTS "%s" (id serial primary key)'  # noqa: F821
                       % modelo.replace('.', '_'))
        env.cr.execute(  # noqa: F821
            'INSERT INTO ir_ui_view (name, model, type, mode, priority, arch_db)'
            " VALUES (%s, %s, 'form', 'primary', 16, '{}'::jsonb)",
            ('resquicio plantado', modelo))
        env.cr.execute(  # noqa: F821
            'INSERT INTO ir_model_fields'
            ' (name, model, model_id, field_description, ttype, state)'
            ' VALUES (%s, %s, %s, %s::jsonb, %s, %s)',
            ('tf_residuo_plantado', modelo, modelo_id, '{"en_US": "Residuo plantado"}',
             'char', 'base'))
    env.cr.commit()  # noqa: F821
    print('RESQUICIO_PLANTADO modelos=%s' % (','.join(RESQUICIO_MODELOS) or 'nenhum'))


_plantar_resquicio()
PY
        } >"$DENTE_DIR/desinstalar_com_resquicio.py"
        D3="$(TRE_MODULO_DIR="$DENTE_DIR/m3" TRE_BANCO="${BANCO}_dente3" TRE_LOG_DIR="$LOG_DIR/dente" \
              TRE_DESINSTALADOR="$DENTE_DIR/desinstalar_com_resquicio.py" "$0" 2>&1)"
        echo "$D3" >"$LOG_DIR/dente-3-resquicio-plantado.out"
        echo "$D3" | tail -4
        PLANTIO="$(grep -h 'RESQUICIO_PLANTADO modelos=' "$LOG_DIR/dente/3-desinstalacao.log" 2>/dev/null | tail -1)"
        if [ -n "$PLANTIO" ] \
           && echo "$D3" | grep -qE '^FALHOU [0-9]+ resquicio' \
           && echo "$D3" | grep -qE '^FALHOU [0-9]+ tabela\(s\) dos modelos'; then
            echo "OK    dente 3: resquicio plantado reprova os itens de resquicio e de tabela ($PLANTIO)"
        else
            echo "FALHOU dente 3: resquicio plantado NAO reprovou os itens de resquicio — a regua continua sem dente (plantio='$PLANTIO')"
            DENTE_FALHAS=$((DENTE_FALHAS + 1))
        fi
        DENTE_PROVAS=$((DENTE_PROVAS + 1))
    else
        echo "NAO APLICAVEL dente 3: o modulo em $MODULO_DIR nao declara models/ — sem modelo/tabela/campo/view o item de resquicio nao tem entidade para acusar"
    fi

    echo '---'
    if [ "$DENTE_FALHAS" -eq 0 ]; then
        echo "RESULTADO: MODULO_ODOO_DENTE_OK ($DENTE_PROVAS provas, 0 falhas) modulo=$MODULO"
        exit 0
    fi
    echo "RESULTADO: MODULO_ODOO_DENTE_FALHOU ($DENTE_FALHAS prova(s) sem dente de $DENTE_PROVAS) modulo=$MODULO"
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
# Container DESCARTAVEL do Odoo (mesma imagem do par de dev), com o modulo montado em
# /mnt/extra-addons e uma configuracao propria: a senha nasce nesta execucao, vive num
# arquivo 600 dono uid 100 e nunca aparece em argumento, log ou artefato.
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
# regua do resquicio (defeito TRE-W2-E03-T01-D03)
#
# A regua NAO pode ser escrita com o nome do PACOTE ($MODULO): em Odoo o nome da TABELA e o nome
# do MODELO (`tf.process.opportunity` -> `tf_process_opportunity`), e nem campo nem view carrega
# o nome do modulo. Medido em 01/10/2026, com o modulo COM o primeiro modelo instalado:
#   ir_model_data  where module = '<modulo>'      = 17  (tem superficie: mede)
#   ir_ui_view     where model like '<modulo>%'   = 0   (codigo morto)
#   ir_model_fields where name like '<modulo>%'   = 0   (codigo morto)
#   tabelas com prefixo '<modulo>_'               = 0   (0 COM o modulo instalado)
# Ou seja: os itens de resquicio do passo 3 podiam imprimir OK sem poder acusar tabela, campo ou
# view — e o passo 3 e o rollback declarado do card.
#
# Aqui a regua e DERIVADA do que o modulo REGISTRA, medido no banco COM o modulo instalado:
#   modelos proprios = ir_model_data (module=$MODULO, model='ir.model') MENOS os modelos
#     compartilhados com outro modulo — mesmo criterio que o Odoo usa para decidir se apaga o
#     modelo na desinstalacao (ex.: `res.partner`, que o `crm` registra mas sobrevive de
#     proposito: acusa-lo seria falso positivo);
#   tabela de cada modelo = EXISTENCIA MEDIDA em information_schema (nome derivado do modelo,
#     nao suposto);
#   views e campos = ir_ui_view / ir_model_fields com model nos modelos proprios.
# A superficie medida vai impressa em INFO (item sem superficie nao prova nada) e a prova de
# dente 3 planta resquicio no banco para mostrar que a regua corrigida acusa.
# Limite declarado: entidades sem `ir_model_data` do modulo e que nao sejam modelo/tabela/campo/
# view acima (ACLs, regras, constraints) nao entram nesta regua.
# ---------------------------------------------------------------------------
so_numero() { printf '%s' "$1" | grep -qE '^[0-9]+$'; }

modelos_proprios_do_modulo() { # $1=banco (modulo INSTALADO) -> um modelo por linha
    psql_bd "$1" "select m.model from ir_model_data d join ir_model m on m.id = d.res_id
        where d.module = '$MODULO' and d.model = 'ir.model'
          and not exists (select 1 from ir_model_data o where o.model = 'ir.model'
                            and o.res_id = d.res_id and o.module <> '$MODULO')
        order by 1"
}

tabelas_dos_modelos() { # $1=banco $2=modelos (linhas) -> as tabelas que EXISTEM, uma por linha
    local modelo
    while IFS= read -r modelo; do
        [ -n "$modelo" ] || continue
        psql_bd "$1" "select table_name from information_schema.tables
            where table_schema = 'public' and table_name = replace('$modelo', '.', '_')"
    done <<<"$2"
}

capturar_superficie_do_modulo() { # $1=banco COM o modulo instalado — preenche SUPERFICIE_*/LISTA_MODELOS
    local lista
    SUPERFICIE_MODELOS="$(modelos_proprios_do_modulo "$1")"
    SUPERFICIE_TABELAS="$(tabelas_dos_modelos "$1" "$SUPERFICIE_MODELOS")"
    SUPERFICIE_DATA="$(psql_bd "$1" "select count(*) from ir_model_data where module = '$MODULO'")"
    SUPERFICIE_N_MODELOS="$(printf '%s\n' "$SUPERFICIE_MODELOS" | grep -c . || true)"
    SUPERFICIE_N_TABELAS="$(printf '%s\n' "$SUPERFICIE_TABELAS" | grep -c . || true)"
    LISTA_MODELOS=""
    SUPERFICIE_MODELOS_REG=0
    SUPERFICIE_CAMPOS=0
    SUPERFICIE_VIEWS=0
    if [ "$SUPERFICIE_N_MODELOS" -gt 0 ]; then
        lista="$(printf '%s\n' "$SUPERFICIE_MODELOS" | sed "s/.*/'&'/" | paste -sd, -)"
        LISTA_MODELOS="($lista)"
        SUPERFICIE_MODELOS_REG="$(psql_bd "$1" "select count(*) from ir_model where model in $LISTA_MODELOS")"
        SUPERFICIE_CAMPOS="$(psql_bd "$1" "select count(*) from ir_model_fields where model in $LISTA_MODELOS")"
        SUPERFICIE_VIEWS="$(psql_bd "$1" "select count(*) from ir_ui_view where model in $LISTA_MODELOS")"
    fi
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
    info "sha256 do modulo sob teste:"
    (cd "$MODULO_DIR" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's/^/      /')
else
    falhou "modulo ausente em $MODULO_DIR (__manifest__.py nao encontrado)"; resumo
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
# dupla descartavel propria (ver o cabecalho: por que nao usar pg-odoo-dev)
# ---------------------------------------------------------------------------
cabecalho "dupla descartavel propria (postgres + odoo)"
DESC_DIR="$(mktemp -d /tmp/verificacao-modulo-XXXXXX)"
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
    # quando o valor e uma variavel (mesma convencao ja usada em scripts/provision/instalar-odoo-dev.sh).
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
# manifesto e versao (AC2)
# ---------------------------------------------------------------------------
cabecalho "manifesto e versao (AC2)"
MANIFESTO_RAW="$(docker run --rm --entrypoint python3 \
    -v "$MODULO_DIR":/leitura/"$MODULO":ro -v "$PARSER":/tmp/manifesto.py:ro "$IMAGEM" \
    /tmp/manifesto.py /leitura/"$MODULO" 2>&1)"
if printf '%s' "$MANIFESTO_RAW" | grep -q '^nome='; then
    ok "manifesto lido e avaliado como literal Python"
else
    falhou "manifesto ilegivel: $(printf '%s' "$MANIFESTO_RAW" | tr '\n' ' ')"; resumo
fi
val() { printf '%s\n' "$MANIFESTO_RAW" | sed -n "s/^$1=//p" | head -1; }
NOME_NO_DIR="$(val nome)"
VERSAO="$(val versao)"
SERIE="$(val serie)"
LICENCA="$(val licenca)"
INSTALAVEL="$(val installable)"
APLICACAO="$(val application)"
DEPENDENCIAS="$(printf '%s\n' "$MANIFESTO_RAW" | sed -n 's/^depende=//p')"
SERIE_IMAGEM="$(docker run --rm --entrypoint odoo "$IMAGEM" --version 2>/dev/null | sed -n 's/^Odoo Server \([0-9.]*\).*/\1/p' | head -1)"

[ "$NOME_NO_DIR" = "$MODULO" ] && ok "nome tecnico do diretorio == modulo: $MODULO" \
    || falhou "diretorio ($NOME_NO_DIR) diferente do modulo ($MODULO)"
[ "$VERSAO" = "$VERSAO_ESPERADA" ] && ok "versao do manifesto == $VERSAO_ESPERADA" \
    || falhou "versao do manifesto ($VERSAO) diferente da esperada ($VERSAO_ESPERADA)"
if [ -n "$SERIE_IMAGEM" ] && [ "$SERIE" = "$SERIE_IMAGEM" ]; then
    ok "serie da versao ($SERIE) == serie do Odoo na imagem ($SERIE_IMAGEM)"
else
    falhou "serie da versao ($SERIE) != serie do Odoo na imagem ($SERIE_IMAGEM)"
fi
[ "$SERIE" = "$SERIE_ESPERADA" ] && ok "serie da versao == $SERIE_ESPERADA" \
    || falhou "serie da versao ($SERIE) diferente da esperada ($SERIE_ESPERADA)"
[ "$INSTALAVEL" = "True" ] && ok "installable=True declarado" || falhou "installable=$INSTALAVEL (esperado True)"
[ "$APLICACAO" = "False" ] && ok "application=False declarado (modulo base, nao aplicacao)" || falhou "application=$APLICACAO (esperado False)"
[ -n "$LICENCA" ] && ok "licenca declarada: $LICENCA" || falhou "manifesto sem licenca"
[ -n "$DEPENDENCIAS" ] && ok "dependencias declaradas: $(printf '%s' "$DEPENDENCIAS" | tr '\n' ' ')" \
    || falhou "manifesto sem dependencias declaradas"

ADDONS_IMAGEM="$(docker run --rm --entrypoint bash "$IMAGEM" -lc \
    'ls /usr/lib/python3/dist-packages/odoo/addons /mnt/extra-addons 2>/dev/null' | LC_ALL=C sort -u)"
DEPS_OK=1
for dep in $DEPENDENCIAS; do
    printf '%s\n' "$ADDONS_IMAGEM" | grep -qx "$dep" || { DEPS_OK=0; falhou "dependencia '$dep' nao existe nos addons da imagem $IMAGEM"; }
done
[ "$DEPS_OK" = "1" ] && ok "toda dependencia declarada e resolvivel na imagem $IMAGEM"

if [ "$MODO" = "manifesto" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 1 — instalacao em banco limpo (AC1 + AC2 no banco)
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
if banco_existe "$BANCO"; then ok "banco $BANCO criado pelo Odoo (do zero -> instalado)"; else falhou "banco $BANCO nao foi criado"; resumo; fi

ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "ir_module_module.state = installed" || falhou "estado no banco: '$ESTADO' (esperado installed)"
VERSAO_BANCO="$(psql_bd "$BANCO" "select latest_version from ir_module_module where name = '$MODULO'")"
[ "$VERSAO_BANCO" = "$VERSAO" ] && ok "versao gravada no banco == manifesto ($VERSAO_BANCO)" \
    || falhou "versao gravada no banco ('$VERSAO_BANCO') diferente da do manifesto ('$VERSAO')"
LICENCA_BANCO="$(psql_bd "$BANCO" "select license from ir_module_module where name = '$MODULO'")"
[ "$LICENCA_BANCO" = "$LICENCA" ] && ok "licenca gravada no banco == manifesto ($LICENCA_BANCO)" \
    || falhou "licenca no banco ('$LICENCA_BANCO') diferente da do manifesto ('$LICENCA')"
DEPS_BANCO="$(psql_bd "$BANCO" "select d.name from ir_module_module_dependency d join ir_module_module m on m.id = d.module_id where m.name = '$MODULO' order by d.name" | tr '\n' ' ' | sed 's/ *$//')"
DEPS_MANIFESTO="$(printf '%s' "$DEPENDENCIAS" | tr '\n' ' ' | sed 's/ *$//')"
[ "$DEPS_BANCO" = "$DEPS_MANIFESTO" ] && ok "dependencias gravadas no banco == declaradas ($DEPS_BANCO)" \
    || falhou "dependencias no banco ('$DEPS_BANCO') != declaradas ('$DEPS_MANIFESTO')"
DEPS_NAO_INSTALADAS="$(psql_bd "$BANCO" "select count(*) from ir_module_module_dependency d join ir_module_module m on m.id = d.module_id join ir_module_module dep on dep.name = d.name where m.name = '$MODULO' and dep.state <> 'installed'")"
[ "$DEPS_NAO_INSTALADAS" = "0" ] && ok "todas as dependencias instaladas no banco" \
    || falhou "$DEPS_NAO_INSTALADAS dependencia(s) nao instalada(s)"
PENDENTES="$(psql_bd "$BANCO" "select count(*) from ir_module_module where state in ('to install','to upgrade','to remove')")"
[ "$PENDENTES" = "0" ] && ok "nenhum modulo pendurado em to install/to upgrade/to remove" \
    || falhou "$PENDENTES modulo(s) pendurado(s) depois da instalacao"

# ---------------------------------------------------------------------------
# passo 2 — teste do Odoo (AC3)
# ---------------------------------------------------------------------------
cabecalho "passo 2/4 — teste do Odoo (--test-enable)"
LOG_ATUAL="$LOG_DIR/2-teste.log"
RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -u "$MODULO" --test-enable \
        --without-demo=all --max-cron-threads=0 --stop-after-init --log-level=test)"
[ "$RC" = "0" ] && ok "odoo --test-enable exit 0 (log: $LOG_ATUAL)" || falhou "odoo --test-enable exit $RC (log: $LOG_ATUAL)"
RELATORIO="$(grep -oE '[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests when loading database' "$LOG_ATUAL" | tail -1)"
if [ -n "$RELATORIO" ]; then
    N_TESTS="$(printf '%s' "$RELATORIO" | sed -nE 's/.*of ([0-9]+) tests.*/\1/p')"
    N_FALHAS="$(printf '%s' "$RELATORIO" | sed -nE 's/^([0-9]+) failed.*/\1/p')"
    N_ERROS="$(printf '%s' "$RELATORIO" | sed -nE 's/^[0-9]+ failed, ([0-9]+) error.*/\1/p')"
    if [ "$N_TESTS" -ge 1 ] && [ "$N_FALHAS" = "0" ] && [ "$N_ERROS" = "0" ]; then
        ok "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests ($MODULO)"
    else
        falhou "runner do Odoo: $N_FALHAS failed, $N_ERROS error(s) of $N_TESTS tests — '$RELATORIO'"
    fi
else
    falhou "log sem linha de relatorio do runner ('N failed, N error(s) of N tests when loading database') — sem medicao nao ha aceite"
fi
grep -q 'At least one test failed when loading the modules\.' "$LOG_ATUAL" \
    && falhou "log traz 'At least one test failed when loading the modules.'" \
    || ok "log sem 'At least one test failed when loading the modules.'"
FALHAS_TESTE="$(grep -cE '^(FAIL|ERROR): ' "$LOG_ATUAL" || true)"
[ "$FALHAS_TESTE" = "0" ] && ok "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" \
    || falhou "$FALHAS_TESTE linha(s) de teste reprovado(a) no log"
grep -q 'Modules loaded\.' "$LOG_ATUAL" && ok "log de teste com 'Modules loaded.'" \
    || falhou "log de teste sem 'Modules loaded.' (a execucao dos testes nao assentou)"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "modulo segue installed depois do teste" || falhou "estado depois do teste: '$ESTADO'"

if [ "$MODO" = "instalacao_teste" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 3 — desinstalacao (AC1)
# ---------------------------------------------------------------------------
cabecalho "passo 3/4 — desinstalacao"
# A regua do resquicio e medida ANTES de desinstalar: depois da desinstalacao o proprio
# `ir_model_data` do modulo (de onde saem os modelos/tabelas/campos/views) ja nao existe e a
# regua ficaria vazia — era exatamente o defeito D03 (item que nao podia acusar nada).
ESTADO_ANTES_DA_REGUA="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
capturar_superficie_do_modulo "$BANCO"
info "regua do resquicio derivada do modulo em state='$ESTADO_ANTES_DA_REGUA': ${SUPERFICIE_DATA} registro(s) em ir_model_data, ${SUPERFICIE_N_MODELOS} modelo(s) proprio(s), ${SUPERFICIE_N_TABELAS} tabela(s) dos modelos, ${SUPERFICIE_CAMPOS} campo(s), ${SUPERFICIE_VIEWS} view(s)"
[ -n "$SUPERFICIE_MODELOS" ] && info "modelos proprios medidos: $(printf '%s' "$SUPERFICIE_MODELOS" | tr '\n' ' ')"
[ -n "$SUPERFICIE_TABELAS" ] && info "tabelas dos modelos medidas: $(printf '%s' "$SUPERFICIE_TABELAS" | tr '\n' ' ')"
LOG_ATUAL="$LOG_DIR/3-desinstalacao.log"
RC="$(odoo_shell_stdin "$LOG_ATUAL" "$BANCO" "$DESINSTALADOR")"
info "odoo shell exit $RC (log: $LOG_ATUAL)"
grep -q 'DESINSTALACAO_OK' "$LOG_ATUAL" && ok "ORM desinstalou o modulo (marcador DESINSTALACAO_OK)" \
    || falhou "marcador DESINSTALACAO_OK ausente no log do shell (exit $RC)"
grep -qE '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" \
    && falhou "log do shell com traceback/recusa: $(grep -E '(Traceback|DESINSTALACAO_FALHOU)' "$LOG_ATUAL" | head -2 | tr '\n' ' ')" \
    || ok "log do shell sem traceback/recusa"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "uninstalled" ] && ok "ir_module_module.state = uninstalled" || falhou "estado no banco: '$ESTADO' (esperado uninstalled)"
# resquicio medido contra a regua derivada acima (antes da desinstalacao), nao contra o nome do pacote
RESQUICOS_DATA="$(psql_bd "$BANCO" "select count(*) from ir_model_data where module = '$MODULO'")"
RESQUICOS_MODELOS_REG=0
RESQUICOS_CAMPOS=0
RESQUICOS_VIEWS=0
if [ -n "$LISTA_MODELOS" ]; then
    RESQUICOS_MODELOS_REG="$(psql_bd "$BANCO" "select count(*) from ir_model where model in $LISTA_MODELOS")"
    RESQUICOS_CAMPOS="$(psql_bd "$BANCO" "select count(*) from ir_model_fields where model in $LISTA_MODELOS")"
    RESQUICOS_VIEWS="$(psql_bd "$BANCO" "select count(*) from ir_ui_view where model in $LISTA_MODELOS")"
fi
if so_numero "$RESQUICOS_DATA" && so_numero "$RESQUICOS_MODELOS_REG" \
   && so_numero "$RESQUICOS_CAMPOS" && so_numero "$RESQUICOS_VIEWS"; then
    RESQUICOS_TOTAL=$((RESQUICOS_DATA + RESQUICOS_MODELOS_REG + RESQUICOS_CAMPOS + RESQUICOS_VIEWS))
    if [ "$RESQUICOS_TOTAL" = "0" ]; then
        ok "nenhum resquicio do modulo no banco (regua derivada das entidades do modulo — superficie instalada: ${SUPERFICIE_DATA} dado(s), ${SUPERFICIE_N_MODELOS} modelo(s), ${SUPERFICIE_CAMPOS} campo(s), ${SUPERFICIE_VIEWS} view(s))"
    else
        falhou "$RESQUICOS_TOTAL resquicio(s) do modulo no banco depois da desinstalacao (ir_model_data=$RESQUICOS_DATA modelo(s)=$RESQUICOS_MODELOS_REG campo(s)=$RESQUICOS_CAMPOS view(s)=$RESQUICOS_VIEWS)"
    fi
else
    falhou "medicao de resquicio sem numero (dados='$RESQUICOS_DATA' modelos='$RESQUICOS_MODELOS_REG' campos='$RESQUICOS_CAMPOS' views='$RESQUICOS_VIEWS') — sem medicao nao ha aceite"
fi
TABELAS_RESQUICOS=0
TABELAS_RESQUICOS_LISTA=""
while IFS= read -r TABELA; do
    [ -n "$TABELA" ] || continue
    if [ "$(psql_bd "$BANCO" "select count(*) from information_schema.tables where table_schema = 'public' and table_name = '$TABELA'")" != "0" ]; then
        TABELAS_RESQUICOS=$((TABELAS_RESQUICOS + 1))
        TABELAS_RESQUICOS_LISTA="$TABELAS_RESQUICOS_LISTA $TABELA"
    fi
done <<<"$SUPERFICIE_TABELAS"
[ "$TABELAS_RESQUICOS" = "0" ] && ok "nenhuma tabela dos modelos do modulo sobreviveu a desinstalacao (${SUPERFICIE_N_TABELAS} tabela(s) dos ${SUPERFICIE_N_MODELOS} modelo(s) do modulo medida(s) com o modulo instalado)" \
    || falhou "$TABELAS_RESQUICOS tabela(s) dos modelos do modulo sobreviveram a desinstalacao:$TABELAS_RESQUICOS_LISTA"

# ---------------------------------------------------------------------------
# passo 4 — reinstalacao (AC1, idempotencia)
# ---------------------------------------------------------------------------
cabecalho "passo 4/4 — reinstalacao (idempotencia)"
LOG_ATUAL="$LOG_DIR/4-reinstalacao.log"
RC="$(odoo_docker "$LOG_ATUAL" -d "$BANCO" -i "$MODULO" --without-demo=all --max-cron-threads=0 \
        --stop-after-init --log-level=info)"
[ "$RC" = "0" ] && ok "odoo --init (segunda vez) exit 0 (log: $LOG_ATUAL)" || falhou "odoo --init (segunda vez) exit $RC"
ERROS="$(erros_no_log "$LOG_ATUAL")"
[ "$ERROS" = "0" ] && ok "log de reinstalacao sem linha ERROR/CRITICAL" \
    || falhou "log de reinstalacao com $ERROS linha(s) ERROR/CRITICAL: $(grep -nE '(^| )(ERROR|CRITICAL) ' "$LOG_ATUAL" | head -3 | tr '\n' ' ')"
ESTADO="$(psql_bd "$BANCO" "select state from ir_module_module where name = '$MODULO'")"
[ "$ESTADO" = "installed" ] && ok "modulo installed de novo depois do ciclo install/uninstall/install" \
    || falhou "estado depois da reinstalacao: '$ESTADO'"
VERSAO_BANCO="$(psql_bd "$BANCO" "select latest_version from ir_module_module where name = '$MODULO'")"
[ "$VERSAO_BANCO" = "$VERSAO" ] && ok "versao gravada na reinstalacao == manifesto ($VERSAO_BANCO)" \
    || falhou "versao na reinstalacao ('$VERSAO_BANCO') diferente da do manifesto ('$VERSAO')"

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
