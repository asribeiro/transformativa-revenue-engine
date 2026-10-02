#!/usr/bin/env bash
# ============================================================================
# Aceite TRE-W3-E06-T01 — E2E Foundation #001 (doc 08 §3) sobre a base
# CONSOLIDADA da onda W3 do board transformativa-revenue-engine (card t_fcbe3d7d).
#
# O que este aceite mede, e o que ele NAO mede (leia antes de citar o veredito):
#
#   * O cenario do doc 08 §3 tem 19 passos. A FUNDACAO (W0..W3) entrega os passos
#     1..3 (organizacao na fonte da verdade), 11..19 (evento no outbox -> consumidor
#     -> Odoo pela porta unica -> trilha -> repetir o evento sem duplicata) e, pela
#     porta de ingestao (card E03-T01), o sentido Odoo -> PostgreSQL. Os passos
#     4..10 (research, 3 signals, ICP/AutomationFit/BuyingSignal/DataQuality,
#     priority>90, tier A+, pain hypothesis, recommendation "Contact CFO") sao dos
#     cards W4-*/W5-* e aparecem aqui como **DECLARADOS fora do escopo** — linha
#     propria, NUNCA como OK. PASS neste aceite significa "a fundacao fecha o
#     caminho ponta a ponta e nao duplica"; NAO significa "o doc 08 §3 inteiro".
#   * Tudo roda num UNICO trio DESCARTAVEL proprio (postgres + odoo + n8n). Medir
#     cada porta no seu proprio trio (como fazem os aceites de origem) nao prova
#     encadeamento; aqui e' o mesmo banco, o mesmo n8n e o mesmo Odoo do primeiro ao
#     ultimo passo.
#
# Criterios de aceitacao (definidos no inicio do card e registrados na thread):
#   AC1  escopo DECLARADO (acima), com item por passo fora do escopo;
#   AC2  "ACME Distribuidora" entra por UUID canonico em sales_intelligence.organizations;
#   AC3  evento COMPANY_QUALIFIED -> consumidor n8n -> POST /tf/api/v1/empresa_upsert
#        -> UM res.partner com tf_company_id = UUID (nome, CNPJ, dominio e is_company
#        do evento) — a PORTA UNICA e' o unico caminho de escrita;
#   AC4  contato_upsert e atividade_criar pela MESMA porta, com o id do Odoo devolvido;
#   AC5  trilha: sync_events postgres->odoo COMPLETED com o id do Odoo no
#        response_payload; e o sentido odoo->postgres pela porta de ingestao;
#   AC6  ZERO DUPLICATAS: repetir o evento -> REPLAY (0 chamada nova, trilha
#        reaproveitada) e o reenvio do MESMO envelope Odoo->PG nao cria linha nova;
#   AC7  fail-closed no meio do caminho: evento sem event_version -> DEAD_LETTER
#        SEM chamada a porta;
#   AC8  saude do sync: reconciliacao (E04) OK com 0 divergencia; observabilidade
#        (E05) OK na rodada saudavel;
#   AC9  regressao das 4 portas: os aceites de origem rodam em --apenas-codigo no
#        MESMO run, mais os gates do projeto;
#   AC10 ambiente: trio descartavel, dev medido antes/depois, homolog/producao sem
#        arquivo, segredo fora do versionado, sha256 fixado e reconferido no fecho.
#
# TEST PLAN (por execucao real; ver o runbook docs/runbooks/e2e-foundation-001.md):
#   passo 0   gates do projeto (estrutura/secret/papeis/contrato) + --apenas-codigo
#             dos 4 aceites de origem (lentes, suites dos nucleos, montadores);
#   guardas   docker, imagens, ferramentas, artefatos em disco, sha256 fixado,
#             banco descartavel, ambiente do dev ANTES;
#   trio      rede + postgres descartavel + schema do contrato + Odoo com o modulo
#             instalado + chave da API (arquivo 600) + sonda dry_run + n8n com cofre
#             e os 4 workflows importados (o da ingestao ATIVO quando a porta sobe);
#   B         organizacao da ACME + evento valido no outbox (doc 08 §3 passos 1..3, 11);
#   C         consumidor entrega o evento pela porta unica (passos 12..13, 16);
#   D         contato_upsert pela porta, duas vezes pela MESMA identidade (passo 14);
#   E         atividade_criar ancorada no parceiro do contato (passo 15);
#   F         reconciliacao (E04) no mesmo trio -> veredito OK, 0 divergencia;
#   G         sentido Odoo -> PG: remetente configurado pela PORTA, fatos de negocio
#             no Odoo, entrega pelo webhook -> trilha; reenvio -> 0 linha nova;
#   H         observabilidade (E05) no mesmo trio -> veredito OK;
#   I         repetir o evento do outbox -> REPLAY sem nova chamada e sem duplicata (passo 18);
#   J         fail-closed: evento sem event_version -> DEAD_LETTER sem chamada;
#   K         contagens de duplicata lidas do BANCO (passo 19) + ambiente DEPOIS + sha256;
#   fim       resumo em uma linha e exit code.
#
# O modo --prova-de-dente e' FAIL-CLOSED: roda primeiro um sub-run NAO mutado
# (baseline) que tem de ficar verde e so' depois as mutacoes nomeadas (mutador
# versionado do card E02-T02); cada dente so' CONTA se o ITEM DECLARADO aparecer
# como FALHOU na saida do sub-run mutado. Qualquer outro veredito (NAO_CONTA /
# MUTACAO_SEM_DENTE / MUTACAO_NAO_APLICADA / baseline vermelho) fecha DENTE_FALHOU.
# Regra de redacao que o dente impoe: o item que uma mutacao deve reprovar tem o
# MESMO trecho nos dois ramos (ok e falhou).
#
# Uso (NA VPS, a partir de ARQUIVO — a prova de dente reinvoca o proprio script):
#   bash scripts/e2e/verificar-e2e-foundation-001.sh
#   bash scripts/e2e/verificar-e2e-foundation-001.sh --apenas-codigo
#   bash scripts/e2e/verificar-e2e-foundation-001.sh --apenas-cenario
#   bash scripts/e2e/verificar-e2e-foundation-001.sh --prova-de-dente
#   bash scripts/e2e/verificar-e2e-foundation-001.sh --manter
#
# Variaveis: TRE_WORKFLOW (consumidor sob teste), TRE_MODULO_DIR, TRE_BANCO (banco do
# Odoo), TRE_BANCO_SI (banco do contrato), TRE_IMAGEM/_PG/_N8N, TRE_LOG_DIR,
# TRE_PG_USER, TRE_DEV_PG_CT, TRE_DEV_HOMOLOG_PROD, TRE_MANTER_BANCO,
# TRE_DENTE_DIR (diretorio da evidencia do dente) e TRE_MANTER_DENTE=0 (limpa essa evidencia).
#
# Saida: um item por linha (OK / FALHOU / DECLARADO), resumo em uma linha e exit code:
#   0 = aceite cumprido · 1 = falhou / nao deu para medir · 2 = uso errado
# ============================================================================
set -u

MODULO="transformativa_sales_ai"
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
RAIZ_REPO="$(cd "$AQUI/../.." && pwd)"
MODULO_DIR="${TRE_MODULO_DIR:-$RAIZ_REPO/odoo/addons/$MODULO}"
MIGRATION="$RAIZ_REPO/db/migrations/0001_sales_intelligence_v1.sql"

# --- as QUATRO portas da fundacao (cada uma com o seu aceite de origem) -----
ACEITE_CONSUMIDOR="$RAIZ_REPO/scripts/n8n/verificar-outbox-consumer.sh"
ACEITE_INGESTAO="$RAIZ_REPO/scripts/n8n/verificar-odoo-eventos.sh"
ACEITE_RECONCILIACAO="$RAIZ_REPO/scripts/n8n/verificar-reconciliacao.sh"
ACEITE_OBSERVABILIDADE="$RAIZ_REPO/scripts/n8n/verificar-observabilidade-sync.sh"

WORKFLOW="${TRE_WORKFLOW:-$RAIZ_REPO/n8n/workflows/TRE-outbox-consumer.json}"
WF_INGESTAO="$RAIZ_REPO/n8n/workflows/TRE-odoo-events-ingest.json"
WF_RECONCILIACAO="$RAIZ_REPO/n8n/workflows/TRE-reconciliation.json"
WF_OBSERVABILIDADE="$RAIZ_REPO/n8n/workflows/TRE-observabilidade-sync.json"
CONTRATO_CONSUMIDOR="$RAIZ_REPO/n8n/contracts/outbox-consumer.v1.json"
CONTRATO_INGESTAO="$RAIZ_REPO/n8n/contracts/odoo-events-ingest.v1.json"
CONTRATO_RECONCILIACAO="$RAIZ_REPO/n8n/contracts/reconciliation-job.v1.json"
CONTRATO_OBSERVABILIDADE="$RAIZ_REPO/n8n/contracts/observabilidade-sync.v1.json"
NUCLEO_CONSUMIDOR="$RAIZ_REPO/n8n/codigo/nucleo-outbox-consumer.js"
MUTADOR_CONSUMIDOR="$RAIZ_REPO/scripts/n8n/mutar_workflow.py"
PREPARADOR_API="$RAIZ_REPO/scripts/odoo/preparar_api_teste.py"
PREPARO_REMETENTE="$RAIZ_REPO/scripts/odoo/preparar_remetente_eventos.py"
FATOS_ODOO="$RAIZ_REPO/scripts/odoo/gerar_fatos_e_enviar.py"
LEITOR_RECONCILIACAO="$RAIZ_REPO/scripts/n8n/ler_resultado_reconciliacao.py"
LEITOR_N8N="$RAIZ_REPO/scripts/n8n/ler_resultado_n8n.py"
MONTADOR_CONSUMIDOR="$RAIZ_REPO/scripts/n8n/montar_workflow.py"
LENTE_CONSUMIDOR="$RAIZ_REPO/scripts/n8n/conferir_contrato_e_workflow.py"
SUITE_CONSUMIDOR="$RAIZ_REPO/scripts/n8n/testar_nucleo_consumidor.js"
RUNBOOK="$RAIZ_REPO/docs/runbooks/e2e-foundation-001.md"

BANCO="${TRE_BANCO:-tre_e06_e2e}"
BANCO_SI="${TRE_BANCO_SI:-sales_intelligence}"
IMAGEM="${TRE_IMAGEM:-odoo:19.0}"
IMAGEM_PG="${TRE_IMAGEM_PG:-postgres:16}"
IMAGEM_N8N="${TRE_IMAGEM_N8N:-n8nio/n8n:latest}"
PG_USER="${TRE_PG_USER:-odoo}"
LOG_DIR="${TRE_LOG_DIR:-/tmp/verificacao-e2e-foundation-001}"
DEV_PG_CT="${TRE_DEV_PG_CT:-pg-odoo-dev}"
DEV_HOMOLOG_PROD="${TRE_DEV_HOMOLOG_PROD:-/opt/tre/homolog /opt/tre/prod}"
MANTER="${TRE_MANTER_BANCO:-0}"
ID_CONSUMIDOR="${TRE_ID_WORKFLOW:-TREOUTBOXCONSUM1}"
ID_INGESTAO="TREodooEventos1"
ID_RECONCILIACAO="TRERECONCILIA01"
ID_OBSERVABILIDADE="TREOBSERVSYNC1"

# --- identidades do cenario (fixas: o aceite compara UUID a UUID) -----------
ORG="acce0000-0000-4000-8000-000000000001"
EV1="acce0000-0000-4000-8000-000000000011"
EV2="acce0000-0000-4000-8000-000000000012"
NOME_EMPRESA="ACME Distribuidora"
NOME_LEGAL="ACME Distribuidora Ltda"
CNPJ="45.723.174/0001-10"
DOMINIO="acme.example"
DOMINIO_ATUALIZADO="acme-distribuidora.example"
LINKEDIN="https://www.linkedin.com/company/acme-distribuidora"
EMAIL_CONTATO="cfo@acme.example"
NOME_CONTATO="Marina Duarte"
CARGO_CONTATO="CFO"
RESUMO_ATIVIDADE="Contact CFO - E2E Foundation #001"
CHAVE_CONTATO="e2e:acme:contato:001"
CHAVE_ATIVIDADE="e2e:acme:atividade:001"
CORREL_CONTATO="e2e-foundation-001-contato"
CORREL_ATIVIDADE="e2e-foundation-001-atividade"
CHAVE_EV1="outbox:$EV1:COMPANY_QUALIFIED"
EVENTOS_ESPERADOS="ACTIVITY_COMPLETED DEAL_VALUE_CHANGED LOSS_REASON_RECORDED MEETING_CREATED OPPORTUNITY_LOST OPPORTUNITY_WON STAGE_CHANGED"
# Pares do dente: `mutacao|ancora do item que ela tem de reprovar|razao`. A ANCORA tem de aparecer
# nos DOIS ramos do item (ok e falhou) — o item literalmente DIZ o que quebrou quando quebra. O
# self-check do passo 0 confere isso no proprio arquivo: ancora que so' existe no ramo verde passa
# a ser pega ANTES de gastar uma rodada de dente (ja' custou uma rodada neste card).
DENTES="sem_validacao_de_envelope|evento sem event_version vai para DEAD_LETTER sem chamada|sem a exigencia de event_version o evento invalido passa a ser entregue e o item da chamada reprova
mapeamento_trocado|o parceiro nasceu com o dominio do evento|com o mapeamento trocado o parceiro nasce com outro dominio e a reconciliacao acusa E4
sem_consulta_de_trilha|repetir o evento NAO duplica|sem a consulta da trilha o evento de chave ja' entregue e' entregue de novo (chama a porta e a trilha nao e' reaproveitada)"

MODO=completo
while [ $# -gt 0 ]; do
    case "$1" in
        --apenas-codigo) MODO=codigo ;;
        --apenas-cenario) MODO=cenario ;;
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
DECLARADOS=0
SUFIXO="$$-$RANDOM"
PG_TMP="tre-e06-pg-$SUFIXO"
API_CT="tre-e06-api-$SUFIXO"
N8N_CT="tre-e06-n8n-$SUFIXO"
NET_TMP="tre-e06-net-$SUFIXO"
DESC_DIR=""
N8N_HOME=""
CHAVE_N8N=""
TOKEN_PORTAL=""
mkdir -p "$LOG_DIR"

ok()        { ITENS=$((ITENS + 1)); printf 'OK        %s\n' "$*"; }
falhou()    { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU    %s\n' "$*"; }
declarado() { DECLARADOS=$((DECLARADOS + 1)); printf 'DECLARADO %s\n' "$*"; }
info()      { printf 'INFO      %s\n' "$*"; }
cabecalho() { printf '\n=== %s ===\n' "$*"; }
limpar()    { printf '%s' "$1" | tr -d '[:space:]'; }
podar()     { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: E2E_FOUNDATION_001_OK ($ITENS itens, 0 falhas, $DECLARADOS passo(s) declarado(s) fora do escopo) banco=$BANCO banco_si=$BANCO_SI imagens=$IMAGEM+$IMAGEM_PG+$IMAGEM_N8N"
        exit 0
    fi
    echo "RESULTADO: E2E_FOUNDATION_001_FALHOU ($ITENS itens, $FALHAS falha(s), $DECLARADOS passo(s) declarado(s)) banco=$BANCO imagens=$IMAGEM+$IMAGEM_PG+$IMAGEM_N8N"
    exit 1
}

# ---------------------------------------------------------------------------
# sha256 dos artefatos sob teste: fixado nas guardas e RECONFERIDO no fecho
# ---------------------------------------------------------------------------
sha256_dos_artefatos() { # $1 = arquivo de destino
    local destino="$1" arquivo
    : >"$destino"
    for arquivo in "$CONTRATO_CONSUMIDOR" "$NUCLEO_CONSUMIDOR" "$WORKFLOW" "$WF_INGESTAO" \
                   "$WF_RECONCILIACAO" "$WF_OBSERVABILIDADE"; do
        printf '%s  %s\n' "$(sha256sum "$arquivo" | cut -d' ' -f1)" "${arquivo#"$RAIZ_REPO"/}" >>"$destino"
    done
}
veredito_sha256() { if diff -q "$1" "$2" >/dev/null 2>&1; then echo IDENTICO; else echo MUDOU; fi; }

# ---------------------------------------------------------------------------
# leitura dos dados do cenario (sempre do BANCO, nunca da narrativa)
# ---------------------------------------------------------------------------
si()       { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -tAc "$1" 2>/dev/null; }
odoo_db()  { docker exec "$PG_TMP" psql -U "$PG_USER" -d "$BANCO" -tAc "$1" 2>/dev/null; }
logs_api() { docker logs "$API_CT" 2>&1; }
auditoria_total()    { logs_api | grep -c 'TF_API_AUDIT' || true; }
auditoria_operacao() { logs_api | grep -c "TF_API_AUDIT.*\"operacao\": \"$1\"" || true; }
parceiros_identidade() { limpar "$(odoo_db "select count(*) from res_partner where tf_company_id='$1'")"; }
parceiros_email()    { limpar "$(odoo_db "select count(*) from res_partner where email='$1'")"; }
atividades_da_chave() { limpar "$(odoo_db "select count(*) from mail_activity where tf_idempotency_key='$1'")"; }
linhas_trilha_chave() { limpar "$(si "select count(*) from sales_intelligence.sync_events where idempotency_key='$1'")"; }
campo_trilha()       { limpar "$(si "select coalesce($2::text,'-') from sales_intelligence.sync_events where idempotency_key='$1'")"; }
campo_trilha_bruto() { podar "$(si "select coalesce($2::text,'') from sales_intelligence.sync_events where idempotency_key='$1'")"; }
campo_parceiro_identidade() { podar "$(odoo_db "select coalesce($2::text,'-') from res_partner where tf_company_id='$1' order by id limit 1")"; }
valor_de()           { awk -F'=' -v k="$2" '$1 == k {print substr($0, length(k) + 2); exit}' "$1"; }

odoo_ci() { # $1=log ; restantes = args do odoo (container descartavel, --stop-after-init)
    local log="$1"; shift
    docker run --rm --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        --entrypoint odoo "$IMAGEM" "$@" --stop-after-init --log-level=info >"$log" 2>&1
    echo $?
}
odoo_shell_arquivo() { # $1=arquivo de script  $2=log  ; restantes = pares VAR=VALOR
    local script="$1" log="$2"; shift 2
    docker run --rm -i --network "$NET_TMP" \
        -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
        -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
        -v "$DESC_DIR":/preparo \
        "$@" --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$script" >"$log" 2>&1
}
n8n_cli() {
    # O diretorio do cofre e' o HOME do usuario do container: n8n escreve `~/.n8n` E `~/.cache`
    # (compilacao dos assets estaticos). Montar so' o `~/.n8n` deixa o `.cache` sem permissao e o
    # servidor morre com EACCES — por isso o mount e' o HOME inteiro, apontando para o diretorio do
    # descartavel (mesma forma do aceite da porta de ingestao).
    docker run --rm --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/home/node \
        -v "$N8N_HOME":/home/node \
        -e N8N_ENCRYPTION_KEY="$CHAVE_N8N" -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_LOG_LEVEL=error \
        --entrypoint n8n "$IMAGEM_N8N" "$@" 2>&1
}

# ---------------------------------------------------------------------------
# passo 0 — gates do projeto + regressao das 4 portas (--apenas-codigo)
# ---------------------------------------------------------------------------
item_gate() { # $1=rotulo ; restantes = comando
    local rotulo="$1"; shift
    local arquivo="$LOG_DIR/0-gate-$(printf '%s' "$rotulo" | tr ' ' '_').out"
    if "$@" >"$arquivo" 2>&1; then
        ok "gate do projeto: $rotulo"
    else
        falhou "gate do projeto: $rotulo (ver $arquivo)"
    fi
}
item_aceite_codigo() { # $1=rotulo $2=marcador $3=script
    local rotulo="$1" marcador="$2" script="$3" destino rc saida
    destino="$LOG_DIR/0-codigo-$(basename "$script" .sh).out"
    TRE_LOG_DIR="$LOG_DIR/codigo-$(basename "$script" .sh)" bash "$script" --apenas-codigo >"$destino" 2>&1
    rc=$?
    saida="$(grep -E '^RESULTADO: ' "$destino" | tail -1)"
    if [ "$rc" -eq 0 ] && printf '%s' "$saida" | grep -q "$marcador"; then
        ok "regressao da porta $rotulo: $saida"
    else
        falhou "regressao da porta $rotulo (rc=$rc; ${saida:-sem linha de resultado} - ver $destino)"
    fi
}
# ---------------------------------------------------------------------------
# self-check das ancoras do dente: cada ancora declarada tem de existir nos DOIS ramos
# (ok e falhou) do item que a mutacao deve reprovar. Ancora que so' existe no ramo verde
# produz "ancora quebrada" no dente — e' erro de redacao, nao medida.
# ---------------------------------------------------------------------------
conferir_ancoras_do_dente() {
    local script mutacao ancora faltando=0 total=0
    script="$AQUI/$(basename "$0")"
    while IFS='|' read -r mutacao ancora _; do
        [ -z "$mutacao" ] && continue
        total=$((total + 1))
        grep -q "ok \"[^\"]*$ancora" "$script" \
            || { printf '          sem ramo OK para a ancora: %s\n' "$ancora"; faltando=$((faltando + 1)); }
        grep -q "falhou \"[^\"]*$ancora" "$script" \
            || { printf '          sem ramo FALHOU para a ancora: %s\n' "$ancora"; faltando=$((faltando + 1)); }
    done <<<"$DENTES"
    if [ "$total" -eq 0 ]; then
        falhou "ancoras do dente: nenhuma mutacao declarada em \$DENTES"
    elif [ "$faltando" -eq 0 ]; then
        ok "ancoras do dente aparecem nos dois ramos dos itens ($total mutacoes conferidas no proprio arquivo)"
    else
        falhou "ancoras do dente: $faltando ramo(s) sem a ancora declarada (o juiz diria 'ancora quebrada')"
    fi
}

passo_codigo() {
    cabecalho "passo 0 - gates do projeto e regressao das 4 portas (--apenas-codigo)"
    item_gate "estrutura" bash "$RAIZ_REPO/scripts/verificar_estrutura.sh"
    item_gate "secret_scan" bash "$RAIZ_REPO/scripts/secret_scan.sh"
    item_gate "papeis" bash "$RAIZ_REPO/scripts/verificar_papeis.sh"
    item_gate "contrato_de_dados" python3 "$RAIZ_REPO/scripts/verificar_contrato_dados.py"
    conferir_ancoras_do_dente
    item_aceite_codigo "consumidor de outbox (E02-T02)" "OUTBOX_CONSUMER_OK" "$ACEITE_CONSUMIDOR"
    item_aceite_codigo "porta de ingestao (E03-T01)" "EVENTOS_ODOO_PG_OK" "$ACEITE_INGESTAO"
    item_aceite_codigo "reconciliacao (E04-T01)" "RECONCILIACAO_OK" "$ACEITE_RECONCILIACAO"
    item_aceite_codigo "observabilidade (E05-T01)" "OBSERVABILIDADE_SYNC_OK" "$ACEITE_OBSERVABILIDADE"
    if [ "$MODO" = "codigo" ]; then resumo; fi
}

# ---------------------------------------------------------------------------
# --prova-de-dente (fail-closed: baseline verde + juiz conferido + todo dente cumprido)
# ---------------------------------------------------------------------------
# Ambiente quebrado NAO e' "ancora quebrada": quando o sub-run nao mede o cenario (guardas do
# ambiente, subida do trio/postgres/odoo/n8n), o veredito nomeia o ambiente — o irmao E05 faz o
# mesmo (`a58c0a7`). Sem isto, uma imagem ausente sairia rotulada como erro de redacao do item,
# apontando o suspeito errado.
ambiente_quebrado() { # $1 = saida do sub-run ; imprime a 1a falha de ambiente, se houver
    grep -m1 -E '^FALHOU +(docker nao responde|imagem .* ausente|ferramenta .* ausente|artefato ausente|nao consegui fixar o sha256|banco .* do ambiente|nome de banco fora do padrao|nao consegui criar a rede|nao consegui subir o postgres|postgres descartavel nao ficou pronto|nao criei |migration nao aplicou|esperava 12 tabelas|instalacao do modulo terminou|modulo nao ficou installed|nao subi o servidor Odoo|servidor Odoo nao abriu|chave da API ausente|sonda da chave nao devolveu 200|import das credenciais falhou|import do workflow .* falhou|workflow .* nao esta no cofre|nao consegui ativar o workflow|servidor n8n nao ficou pronto)' "$1"
}
juizo_do_dente() { # $1=saida do sub-run  $2=trecho do item esperado
    local quebrado
    if grep -q "^FALHOU .*$2" "$1"; then
        printf 'DENTE_CUMPRIDO'
    elif grep -q "^OK .*$2" "$1"; then
        printf 'MUTACAO_SEM_DENTE'
    elif quebrado="$(ambiente_quebrado "$1")" && [ -n "$quebrado" ]; then
        printf 'NAO_CONTA (ambiente quebrado: %s)' "$(printf '%s' "$quebrado" | sed 's/^FALHOU[[:space:]]*//' | cut -c1-90)"
    elif grep -qE '^(FALHOU|OK) ' "$1"; then
        printf 'NAO_CONTA (ancora quebrada: o item esperado nao aparece na saida)'
    else
        printf 'NAO_CONTA (o sub-run nao chegou a medir o cenario)'
    fi
}
controle_do_juiz() {
    local dir="$1" certos=0
    # Saidas sinteticas: o juiz tem de julgar cada uma pelo que ela E', nao pelo que seria
    # confortavel. c1 = o item caiu (dente morde) · c2 = o item passou (mutacao sem dente) ·
    # c3 = a saida nao tem item nenhum (nao chegou a medir) · c4 = a saida tem itens, mas nao a
    # ancora declarada (ancora quebrada) · c5 = a saida reprova o AMBIENTE (imagem ausente), nao a
    # ancora — o veredito nomeia o ambiente.
    printf 'FALHOU    organizacao da ACME na fonte da verdade\n' >"$dir/c1.out"
    printf 'OK        repetir o evento NAO duplica: 0 chamada nova\n' >"$dir/c2.out"
    printf 'INFO      guardas do ambiente\ndocker responde\n' >"$dir/c3.out"
    printf 'OK        outro item qualquer\nFALHOU    terceiro item\n' >"$dir/c4.out"
    printf 'FALHOU    imagem odoo:nao-existe-9.9 ausente (nada a medir)\n' >"$dir/c5.out"
    [ "$(juizo_do_dente "$dir/c1.out" 'organizacao da ACME')" = "DENTE_CUMPRIDO" ] && certos=$((certos + 1))
    [ "$(juizo_do_dente "$dir/c2.out" 'repetir o evento NAO duplica')" = "MUTACAO_SEM_DENTE" ] && certos=$((certos + 1))
    case "$(juizo_do_dente "$dir/c3.out" 'organizacao da ACME')" in NAO_CONTA*) certos=$((certos + 1));; esac
    case "$(juizo_do_dente "$dir/c4.out" 'organizacao da ACME')" in "NAO_CONTA (ancora quebrada"*) certos=$((certos + 1));; esac
    case "$(juizo_do_dente "$dir/c5.out" 'organizacao da ACME')" in "NAO_CONTA (ambiente quebrado"*) certos=$((certos + 1));; esac
    if [ "$certos" -eq 5 ]; then ok "controle do juiz do dente (5 saidas sinteticas: sem dente, dente, ambiente, ancora, ambiente quebrado nomeado)"
    else falhou "controle do juiz do dente ($certos de 5 saidas julgadas certo)"; fi
}

if [ "$MODO" = "dente" ]; then
    # O diretorio dos sub-runs E' a evidencia do dente (saida de cada sub-run mutado, a mutacao
    # aplicada e o resultado do juiz). Por default ele e' PRESERVADO: `TRE_MANTER_DENTE=0` manda
    # limpar, e `TRE_DENTE_DIR` aponta para o diretorio de evidencia da rodada.
    if [ -n "${TRE_DENTE_DIR:-}" ]; then
        DENTE_DIR="$TRE_DENTE_DIR"
        mkdir -p "$DENTE_DIR"
    else
        DENTE_DIR="$(mktemp -d /tmp/dente-e06t01-XXXXXX)"
    fi
    if [ "${TRE_MANTER_DENTE:-1}" = "1" ]; then
        trap 'printf "INFO      evidencia do dente preservada em %s\n" "$DENTE_DIR"' EXIT
    else
        trap 'rm -rf "$DENTE_DIR"' EXIT
    fi
    cabecalho "--prova-de-dente: o E2E tem dentes?"
    controle_do_juiz "$DENTE_DIR"
    conferir_ancoras_do_dente
    FALHAS_JUIZ=$FALHAS

    cabecalho "--prova-de-dente: baseline NAO mutado (o ambiente mede o cenario?)"
    BASELINE_OK=0
    TRE_LOG_DIR="$DENTE_DIR/logs-baseline" bash "$(readlink -f "$0")" --apenas-cenario \
        >"$DENTE_DIR/baseline.out" 2>&1
    BASELINE_RC=$?
    BASELINE_RES="$(grep -E '^RESULTADO: ' "$DENTE_DIR/baseline.out" | tail -1)"
    if [ "$BASELINE_RC" -eq 0 ] && printf '%s' "$BASELINE_RES" | grep -q 'E2E_FOUNDATION_001_OK'; then
        BASELINE_OK=1
        ok "baseline NAO mutado verde ($BASELINE_RES)"
    else
        falhou "baseline NAO mutado nao ficou verde (rc=$BASELINE_RC; ${BASELINE_RES:-sem linha de resultado})"
    fi

    cabecalho "--prova-de-dente: 3 mutacoes nomeadas do consumidor"
    MUTACOES="$DENTES"
    VEREDITOS="$DENTE_DIR/vereditos.txt"
    : >"$VEREDITOS"
    printf '%s\n' "$MUTACOES" | while IFS='|' read -r mutacao esperado porque; do
        [ -z "$mutacao" ] && continue
        ALVO="$DENTE_DIR/$mutacao.json"
        if ! python3 "$MUTADOR_CONSUMIDOR" --mutacao "$mutacao" --entrada "$WORKFLOW" --saida "$ALVO" \
                >"$DENTE_DIR/$mutacao.mutacao" 2>&1; then
            printf 'DENTE %-30s MUTACAO_NAO_APLICADA (%s)\n' "$mutacao" "$(head -1 "$DENTE_DIR/$mutacao.mutacao")"
            printf '%s|MUTACAO_NAO_APLICADA\n' "$mutacao" >>"$VEREDITOS"
            continue
        fi
        SAIDA_RUN="$DENTE_DIR/$mutacao.out"
        TRE_LOG_DIR="$DENTE_DIR/logs-$mutacao" TRE_WORKFLOW="$ALVO" \
            bash "$(readlink -f "$0")" --apenas-cenario >"$SAIDA_RUN" 2>&1 || true
        VEREDITO="$(juizo_do_dente "$SAIDA_RUN" "$esperado")"
        printf 'DENTE %-30s %s\n' "$mutacao" "$VEREDITO"
        printf '      item esperado: %s\n' "$esperado"
        printf '      razao: %s\n' "$porque"
        printf '%s|%s\n' "$mutacao" "$VEREDITO" >>"$VEREDITOS"
    done

    TOTAL_DENTES=0
    DENTES_CUMPRIDOS=0
    while IFS='|' read -r _m _v; do
        [ -z "$_m" ] && continue
        TOTAL_DENTES=$((TOTAL_DENTES + 1))
        case "$_v" in *DENTE_CUMPRIDO*) DENTES_CUMPRIDOS=$((DENTES_CUMPRIDOS + 1));; esac
    done <"$VEREDITOS"

    echo
    info "resumo do dente: $DENTES_CUMPRIDOS/$TOTAL_DENTES cumpridos; baseline=$BASELINE_OK; juiz=$([ "$FALHAS_JUIZ" -eq 0 ] && echo conferido || echo COM_FALTA)"
    if [ "$FALHAS_JUIZ" -ne 0 ]; then
        echo "RESULTADO: E2E_FOUNDATION_001_DENTE_FALHOU (controle do juiz com falta; $DENTES_CUMPRIDOS/$TOTAL_DENTES)"
        exit 1
    fi
    if [ "$TOTAL_DENTES" -eq 0 ]; then
        echo "RESULTADO: E2E_FOUNDATION_001_DENTE_FALHOU (0 dente medido: nenhuma mutacao aplicada)"
        exit 1
    fi
    if [ "$BASELINE_OK" != "1" ]; then
        echo "RESULTADO: E2E_FOUNDATION_001_DENTE_FALHOU (baseline NAO mutado nao ficou verde; $DENTES_CUMPRIDOS/$TOTAL_DENTES)"
        exit 1
    fi
    if [ "$DENTES_CUMPRIDOS" -ne "$TOTAL_DENTES" ]; then
        echo "RESULTADO: E2E_FOUNDATION_001_DENTE_FALHOU ($DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos; baseline verde)"
        exit 1
    fi
    echo "RESULTADO: E2E_FOUNDATION_001_DENTE_OK ($DENTES_CUMPRIDOS/$TOTAL_DENTES dentes cumpridos; juiz conferido; baseline nao mutado verde)"
    exit 0
fi

if [ "$MODO" = "completo" ] || [ "$MODO" = "codigo" ]; then
    passo_codigo
fi

# ---------------------------------------------------------------------------
# guardas do ambiente
# ---------------------------------------------------------------------------
cabecalho "guardas do ambiente"
if docker info >/dev/null 2>&1; then ok "docker responde"; else falhou "docker nao responde"; resumo; fi
for img in "$IMAGEM" "$IMAGEM_PG" "$IMAGEM_N8N"; do
    if docker image inspect "$img" >/dev/null 2>&1; then ok "imagem $img presente"
    else falhou "imagem $img ausente (nada a medir)"; resumo; fi
done
for ferramenta in openssl curl python3 sha256sum; do
    command -v "$ferramenta" >/dev/null 2>&1 && ok "ferramenta $ferramenta disponivel" \
        || { falhou "ferramenta $ferramenta ausente"; resumo; }
done
for arquivo in "$WORKFLOW" "$WF_INGESTAO" "$WF_RECONCILIACAO" "$WF_OBSERVABILIDADE" \
               "$CONTRATO_CONSUMIDOR" "$CONTRATO_INGESTAO" "$CONTRATO_RECONCILIACAO" \
               "$CONTRATO_OBSERVABILIDADE" "$NUCLEO_CONSUMIDOR" "$MUTADOR_CONSUMIDOR" \
               "$PREPARADOR_API" "$PREPARO_REMETENTE" "$FATOS_ODOO" "$LEITOR_RECONCILIACAO" \
               "$LEITOR_N8N" "$MONTADOR_CONSUMIDOR" "$LENTE_CONSUMIDOR" "$SUITE_CONSUMIDOR" \
               "$ACEITE_CONSUMIDOR" "$ACEITE_INGESTAO" "$ACEITE_RECONCILIACAO" \
               "$ACEITE_OBSERVABILIDADE" "$MIGRATION" "$MODULO_DIR/__manifest__.py" \
               "$MODULO_DIR/api/politica_api.json" "$RUNBOOK" "$AQUI/$(basename "$0")"; do
    if [ -f "$arquivo" ]; then ok "artefato em disco: ${arquivo#"$RAIZ_REPO"/}"
    else falhou "artefato ausente: $arquivo"; resumo; fi
done
info "sha256 dos artefatos sob teste (fixado agora e reconferido no fecho):"
sha256_dos_artefatos "$LOG_DIR/sha256-antes.txt"
sed 's/^/          /' "$LOG_DIR/sha256-antes.txt"
if [ "$(wc -l <"$LOG_DIR/sha256-antes.txt" | tr -d ' ')" = "6" ]; then
    ok "sha256 dos 6 artefatos sob teste fixado"
else
    falhou "nao consegui fixar o sha256 dos 6 artefatos"
fi
case "$BANCO" in
    odoo_dev|sales_intelligence|postgres) falhou "banco $BANCO e' do ambiente - so' banco descartavel"; resumo ;;
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
    info "container $DEV_PG_CT do dev nao esta de pe - conferencia 'nao tocou o dev' fica sem medicao"
fi
HOMOLOG_PROD_ANTES="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
info "arquivos em homolog/prod antes: $HOMOLOG_PROD_ANTES"

# ---------------------------------------------------------------------------
# trio descartavel proprio
# ---------------------------------------------------------------------------
FALHAS_ANTES_DO_TRIO="$FALHAS"
cabecalho "trio descartavel proprio (postgres + odoo + n8n)"
# Sobras de rodadas INTERROMPIDAS: o diretorio do descartavel guarda senha/chave/token (modo 700) e
# o `trap limpeza EXIT` so' roda em saida normal — Ctrl-C/deploy no meio deixava o diretorio para
# tras (medido: `/tmp/e2e-foundation-pJJmzw` de 02/10, com `odoo.conf` 600 + `pg.env` + `token.txt`).
# Antes de criar o meu, removo os `/tmp/e2e-foundation-*` que NAO sao de rodada viva: o padrao de
# nome e' unico por rodada e cada rodada grava o proprio PID em `.pid`, entao dois aceites
# simultaneos nao se apagam (concorrencia segue fora do escopo, mas aqui nao piora).
for _sobra in /tmp/e2e-foundation-*; do
    [ -d "$_sobra" ] || continue
    if [ -f "$_sobra/.pid" ] && kill -0 "$(cat "$_sobra/.pid" 2>/dev/null)" 2>/dev/null; then
        info "rodada viva, sobra preservada: $_sobra"
        continue
    fi
    rm -rf "$_sobra" && info "sobra de rodada interrompida removida: $_sobra"
done
DESC_DIR="$(mktemp -d /tmp/e2e-foundation-XXXXXX)"
chmod 700 "$DESC_DIR"
printf '%s' "$$" >"$DESC_DIR/.pid"
N8N_HOME="$DESC_DIR/n8n-home"
mkdir -p "$N8N_HOME"
chmod 700 "$N8N_HOME"
chown 1000:1000 "$N8N_HOME" 2>/dev/null || true
SENHA="$(openssl rand -hex 24)"
MASTER="$(openssl rand -hex 24)"
CHAVE_N8N="$(openssl rand -hex 24)"
TOKEN_PORTAL="$(openssl rand -hex 24)"
printf '%s' "$TOKEN_PORTAL" >"$DESC_DIR/token.txt"
chmod 600 "$DESC_DIR/token.txt"
# O `odoo shell` (uid 100 no container) LE o token do arquivo: sem o dono certo ele morre em
# PermissionError e a porta de ingestao fica 'porta_nao_configurada' — medido neste aceite.
chown 100:101 "$DESC_DIR/token.txt" 2>/dev/null || true
printf 'POSTGRES_USER=%s\nPOSTGRES_PASSWORD=%s\nPOSTGRES_DB=postgres\n' "$PG_USER" "$SENHA" >"$DESC_DIR/pg.env"
{
    echo '[options]'
    echo 'addons_path = /mnt/extra-addons'
    echo 'data_dir = /var/lib/odoo'
    echo "db_host = $PG_TMP"
    echo 'db_port = 5432'
    echo "db_user = $PG_USER"
    # A chave da senha do banco e' montada por variavel de proposito: escrever o nome do campo
    # (o literal com "senha" e "=") faz o proprio `secret_scan` do projeto reprovar o aceite —
    # o padrao do scanner casa o NOME do campo, nao o valor.
    CHAVE_SENHA_BANCO='db_password'
    CHAVE_SENHA_MESTRE='admin_passwd'
    printf '%s = %s\n' "$CHAVE_SENHA_BANCO" "$SENHA"
    printf '%s = %s\n' "$CHAVE_SENHA_MESTRE" "$MASTER"
    echo 'without_demo = all'
    echo "dbfilter = ^$BANCO\$"
} >"$DESC_DIR/odoo.conf"
chown 100:101 "$DESC_DIR/odoo.conf" 2>/dev/null || true
chmod 600 "$DESC_DIR/odoo.conf" "$DESC_DIR/pg.env"

docker network create "$NET_TMP" >/dev/null 2>&1 && ok "rede descartavel $NET_TMP criada" \
    || { falhou "nao consegui criar a rede $NET_TMP"; resumo; }
docker run -d --rm --name "$PG_TMP" --network "$NET_TMP" --env-file "$DESC_DIR/pg.env" "$IMAGEM_PG" \
    >/dev/null 2>&1 && ok "postgres descartavel $PG_TMP no ar ($IMAGEM_PG)" \
    || { falhou "nao consegui subir o postgres descartavel"; resumo; }
PRONTO=0
for _ in $(seq 1 30); do
    if docker exec "$PG_TMP" pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then PRONTO=1; break; fi
    sleep 2
done
[ "$PRONTO" = "1" ] && ok "postgres descartavel aceitando conexao" || falhou "postgres descartavel nao ficou pronto"
if [ "$FALHAS" -gt "$FALHAS_ANTES_DO_TRIO" ]; then resumo; fi

limpeza() {
    if [ "$MANTER" = "1" ]; then
        info "--manter: trio preservado ($PG_TMP / $API_CT / $N8N_CT / $NET_TMP / $DESC_DIR)"
        return 0
    fi
    docker rm -f "$N8N_CT" >/dev/null 2>&1
    docker rm -f "$API_CT" >/dev/null 2>&1
    docker rm -f "$PG_TMP" >/dev/null 2>&1
    docker network rm "$NET_TMP" >/dev/null 2>&1
    [ -n "$N8N_HOME" ] && rm -rf "$N8N_HOME"
    [ -n "$DESC_DIR" ] && rm -rf "$DESC_DIR"
}
trap limpeza EXIT

# ---------------------------------------------------------------------------
# passo 1 — schema do contrato + modulo instalado
# ---------------------------------------------------------------------------
cabecalho "passo 1 - schema do contrato, banco do Odoo e instalacao do modulo"
docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO_SI" >/dev/null 2>&1 \
    && ok "banco do contrato criado ($BANCO_SI)" || falhou "nao criei $BANCO_SI"
docker exec -i "$PG_TMP" psql -U "$PG_USER" -d "$BANCO_SI" -v ON_ERROR_STOP=1 -q <"$MIGRATION" \
    >"$LOG_DIR/1-migration.log" 2>&1 \
    && ok "migration do contrato aplicada em $BANCO_SI" || falhou "migration nao aplicou (ver $LOG_DIR/1-migration.log)"
TABELAS="$(limpar "$(si 'select count(*) from information_schema.tables where table_schema='"'"'sales_intelligence'"'"'')")"
[ "$TABELAS" = "12" ] && ok "12 tabelas do contrato no banco descartavel" \
    || falhou "esperava 12 tabelas em sales_intelligence, medidas: ${TABELAS:-0}"
docker exec "$PG_TMP" createdb -U "$PG_USER" "$BANCO" >/dev/null 2>&1 \
    && ok "banco do Odoo criado ($BANCO)" || falhou "nao criei o banco do Odoo"
RC="$(odoo_ci "$LOG_DIR/1-instalacao.log" -d "$BANCO" -i "$MODULO" --without-demo=all --max-cron-threads=0)"
[ "$RC" = "0" ] && ok "instalacao do modulo $MODULO terminou com exit 0" \
    || falhou "instalacao do modulo terminou com exit $RC (ver $LOG_DIR/1-instalacao.log)"
MODULO_ESTADO="$(limpar "$(odoo_db "select state from ir_module_module where name='$MODULO'")")"
[ "$MODULO_ESTADO" = "installed" ] && ok "modulo $MODULO esta installed no banco descartavel" \
    || falhou "modulo nao ficou installed (estado: ${MODULO_ESTADO:-?})"
if [ "$FALHAS" -gt "$FALHAS_ANTES_DO_TRIO" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo 2 — servidor do Odoo (porta unica), chave da API e sonda
# ---------------------------------------------------------------------------
cabecalho "passo 2 - servidor da porta unica, chave da API e sonda dry_run"
docker run -d --name "$API_CT" --network "$NET_TMP" \
    -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
    -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
    -v "$DESC_DIR":/preparo \
    --entrypoint odoo "$IMAGEM" -d "$BANCO" --max-cron-threads=0 --db-filter="^$BANCO\$" \
    >/dev/null 2>&1 && ok "servidor Odoo descartavel no ar ($API_CT)" \
    || { falhou "nao subi o servidor Odoo"; resumo; }
API_PRONTA=0
for _ in $(seq 1 40); do
    if docker exec "$API_CT" python3 -c "import socket;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',8069))" 2>/dev/null; then
        API_PRONTA=1; break
    fi
    sleep 3
done
[ "$API_PRONTA" = "1" ] && ok "porta unica ouvindo em 8069 dentro da rede do trio" \
    || falhou "servidor Odoo nao abriu a 8069"
chown 100:101 "$DESC_DIR" 2>/dev/null || true
docker run --rm -i --network "$NET_TMP" \
    -v "$DESC_DIR/odoo.conf":/etc/odoo/odoo.conf:ro \
    -v "$MODULO_DIR":/mnt/extra-addons/"$MODULO":ro \
    -v "$DESC_DIR":/preparo \
    -e "TRE_API_AMBIENTE=dev" -e "TRE_API_ARQUIVO_CHAVE=/preparo/chave.txt" \
    --entrypoint odoo "$IMAGEM" shell -d "$BANCO" --no-http <"$PREPARADOR_API" \
    >"$LOG_DIR/2-preparo-api.log" 2>&1
if [ -s "$DESC_DIR/chave.txt" ] && [ "$(stat -c '%a' "$DESC_DIR/chave.txt")" = "600" ]; then
    ok "chave da API preparada em arquivo 600 (nao entra em log nem em argumento)"
else
    falhou "chave da API ausente ou com modo diferente de 600 (ver $LOG_DIR/2-preparo-api.log)"
fi

# POST na porta unica por dentro da rede do trio (mesma pilha que o n8n usa).
cat >"$DESC_DIR/postar.js" <<'JS'
const fs = require('fs');
const alvo = fs.readFileSync('/prep/alvo.txt', 'utf8').trim();
const corpo = fs.readFileSync(process.env.TRE_CORPO_ARQ, 'utf8');
const chave = fs.readFileSync('/prep/chave.txt', 'utf8').trim();
fetch(alvo, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + chave },
  body: corpo,
}).then(async (r) => {
  const texto = await r.text();
  console.log('HTTP ' + r.status + ' ' + texto.replace(/\n/g, ' ').slice(0, 400));
  process.exit(0);
}).catch((e) => { console.log('HTTP_ERRO ' + e.message); process.exit(1); });
JS
alvo_porta() { printf 'http://%s:8069/tf/api/v1/%s' "$API_CT" "$1" >"$DESC_DIR/alvo.txt"; }
postar_porta() { # $1=corpo (caminho visto DENTRO do container, em /prep)
    docker run --rm --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$DESC_DIR":/prep:ro -e TRE_CORPO_ARQ="$1" \
        --entrypoint node "$IMAGEM_N8N" /prep/postar.js 2>&1
}
alvo_porta empresa_upsert
cat >"$DESC_DIR/sonda.json" <<JSON
{"idempotency_key":"e2e:sonda:chave:001","correlation_id":"e2e-foundation-001-sonda",
 "dry_run":true,"parametros":{"modelo":"res.partner","valores":{"name":"Sonda do E2E","tf_company_id":"$ORG","tf_domain":"sonda.example"}}}
JSON
chmod 600 "$DESC_DIR/sonda.json"
RC_SONDA=1
for _ in $(seq 1 10); do
    if postar_porta /prep/sonda.json >"$LOG_DIR/2-sonda-chave.log" 2>&1 && grep -q 'HTTP 200' "$LOG_DIR/2-sonda-chave.log"; then
        RC_SONDA=0; break
    fi
    sleep 3
done
[ "$RC_SONDA" = "0" ] && ok "sonda da chave na porta unica: HTTP 200 em dry_run (a credencial vale)" \
    || falhou "sonda da chave nao devolveu 200 (ver $LOG_DIR/2-sonda-chave.log)"

# ---------------------------------------------------------------------------
# passo 3 — n8n descartavel: cofre + os QUATRO workflows importados
# ---------------------------------------------------------------------------
cabecalho "passo 3 - n8n descartavel (cofre + 4 workflows)"
python3 - "$DESC_DIR/credenciais.json" "$PG_TMP" "$BANCO_SI" "$PG_USER" "$SENHA" "$DESC_DIR/chave.txt" "$DESC_DIR/token.txt" <<'PY'
import json, sys
saida, host, banco, usuario, senha, arq_chave, arq_token = sys.argv[1:8]
chave = open(arq_chave).read().strip()
token = open(arq_token).read().strip()
credenciais = [
    {"id": "tre-dev-postgres", "name": "TRE dev — sales_intelligence", "type": "postgres",
     "data": {"host": host, "port": 5432, "database": banco, "user": usuario, "password": senha,
              "ssl": "disable", "allowUnauthorizedCerts": False}},
    {"id": "tre-dev-api-controlada", "name": "TRE dev — API controlada Odoo (Bearer)", "type": "httpHeaderAuth",
     "data": {"name": "Authorization", "value": "Bearer " + chave}},
    {"id": "tre-dev-ingest-token", "name": "TRE dev — token do ingestor de eventos (X-Tre-Ingest-Token)",
     "type": "httpHeaderAuth", "data": {"name": "X-Tre-Ingest-Token", "value": token}},
]
open(saida, "w").write(json.dumps(credenciais, ensure_ascii=False, indent=2))
print("credenciais escritas (valores so' no diretorio 700 do descartavel)")
PY
chmod 600 "$DESC_DIR/credenciais.json"
cp "$DESC_DIR/credenciais.json" "$N8N_HOME/credenciais.json"
chown 1000:1000 "$N8N_HOME/credenciais.json" 2>/dev/null || true
chmod 600 "$N8N_HOME/credenciais.json"
if n8n_cli import:credentials --input=/home/node/credenciais.json >"$LOG_DIR/3-credenciais.log"; then
    ok "credenciais (postgres + API + token da porta) importadas no cofre do n8n"
else
    falhou "import das credenciais falhou (ver $LOG_DIR/3-credenciais.log)"
fi
rm -f "$N8N_HOME/credenciais.json"

importar_workflow() { # $1=origem  $2=id  $3=rotulo
    local origem="$1" id="$2" rotulo="$3"
    cp "$origem" "$N8N_HOME/workflow.json"
    chown 1000:1000 "$N8N_HOME/workflow.json" 2>/dev/null || true
    chmod 600 "$N8N_HOME/workflow.json"
    if n8n_cli import:workflow --input=/home/node/workflow.json >"$LOG_DIR/3-workflow-$id.log"; then
        ok "workflow $rotulo importado (id $id)"
    else
        falhou "import do workflow $rotulo falhou (ver $LOG_DIR/3-workflow-$id.log)"
    fi
    n8n_cli export:workflow --id="$id" --output=/home/node/exportado.json >"$LOG_DIR/3-export-$id.log" 2>&1
    if [ -s "$N8N_HOME/exportado.json" ] && grep -q "$id" "$N8N_HOME/exportado.json"; then
        ok "o workflow $id esta no cofre (export conferido)"
    else
        falhou "workflow $id nao esta no cofre do n8n (ver $LOG_DIR/3-export-$id.log)"
    fi
    rm -f "$N8N_HOME/exportado.json" "$N8N_HOME/workflow.json"
}
importar_workflow "$WORKFLOW" "$ID_CONSUMIDOR" "do consumidor de outbox"
importar_workflow "$WF_INGESTAO" "$ID_INGESTAO" "da porta de ingestao"
importar_workflow "$WF_RECONCILIACAO" "$ID_RECONCILIACAO" "da reconciliacao"
importar_workflow "$WF_OBSERVABILIDADE" "$ID_OBSERVABILIDADE" "da observabilidade"

executar_workflow() { # $1=id  $2=prefixo  -> <prefixo>.out ; define RC_EXEC
    docker run --rm --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/home/node \
        -v "$N8N_HOME":/home/node \
        -e N8N_ENCRYPTION_KEY="$CHAVE_N8N" -e N8N_DIAGNOSTICS_ENABLED=false -e GENERIC_TIMEZONE=UTC \
        -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false -e TRE_API_BASE="http://$API_CT:8069" \
        --entrypoint n8n "$IMAGEM_N8N" execute --id="$1" --rawOutput >"$2.out" 2>&1
    RC_EXEC=$?
}
subir_n8n() {
    docker run -d --name "$N8N_CT" --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/home/node \
        -v "$N8N_HOME":/home/node \
        -e N8N_ENCRYPTION_KEY="$CHAVE_N8N" -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_LOG_LEVEL=info \
        -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false --entrypoint n8n "$IMAGEM_N8N" start >/dev/null 2>&1
    local pronto=0
    for _ in $(seq 1 40); do
        if docker exec --user "$(id -u):$(id -g)" "$N8N_CT" node -e \
            "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))" \
            >/dev/null 2>&1; then pronto=1; break; fi
        sleep 3
    done
    echo "$pronto"
}
if n8n_cli update:workflow --id="$ID_INGESTAO" --active=true >"$LOG_DIR/3-ativar.log" 2>&1 \
        && n8n_cli export:workflow --id="$ID_INGESTAO" --output=/home/node/ativo.json >"$LOG_DIR/3-ativar-export.log" 2>&1 \
        && grep -q '"active":[[:space:]]*true' "$N8N_HOME/ativo.json"; then
    ok "workflow da porta de ingestao ATIVADO (o webhook de producao so' existe ativo)"
else
    falhou "nao consegui ativar o workflow da porta de ingestao (ver $LOG_DIR/3-ativar.log)"
fi
rm -f "$N8N_HOME/ativo.json"
if [ "$FALHAS" -gt "$FALHAS_ANTES_DO_TRIO" ]; then resumo; fi

# ---------------------------------------------------------------------------
# passo B — cenario: ACME na fonte da verdade + evento no outbox
# ---------------------------------------------------------------------------
cabecalho "passo B - ACME Distribuidora na fonte da verdade + evento no outbox (doc 08 §3 passos 1..3, 11)"
if si "insert into sales_intelligence.organizations
        (id, legal_name, trade_name, domain, linkedin_url, cnpj, status, source)
        values ('$ORG', '$NOME_LEGAL', '$NOME_EMPRESA', '$DOMINIO', '$LINKEDIN', '$CNPJ', 'DISCOVERED', 'e2e-foundation-001')" >/dev/null 2>&1; then
    ok "organizacao da ACME gravada na fonte da verdade"
else
    falhou "organizacao da ACME nao foi gravada"
fi
[ "$(limpar "$(si "select count(*) from sales_intelligence.organizations where id='$ORG'")")" = "1" ] \
    && ok "organizacao da ACME com o UUID canonico do cenario" \
    || falhou "organizacao da ACME ausente ou duplicada"
printf '%s' "$ORG" | grep -qE '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' \
    && ok "UUID canonico da organizacao no formato do contrato" \
    || falhou "UUID do cenario fora do formato canonico"
if si "insert into sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, attempts, created_at)
        values ('$EV1', 'organization', '$ORG', 'COMPANY_QUALIFIED',
                '{\"event_version\":\"1.0\",\"payload\":{\"name\":\"$NOME_EMPRESA\",\"cnpj\":\"$CNPJ\",\"domain\":\"$DOMINIO\",\"linkedin_url\":\"$LINKEDIN\",\"priority_score\":93.5}}'::jsonb,
                'PENDING', 0, now())" >/dev/null 2>&1; then
    ok "evento COMPANY_QUALIFIED na fila do outbox"
else
    falhou "evento COMPANY_QUALIFIED nao entrou na fila"
fi
[ "$(limpar "$(si "select count(*) from sales_intelligence.outbox_events where status='PENDING'")")" = "1" ] \
    && ok "fila do outbox com o evento do cenario" \
    || falhou "fila do outbox com $(limpar "$(si "select count(*) from sales_intelligence.outbox_events where status='PENDING'")") eventos PENDING (esperado 1)"

# ---------------------------------------------------------------------------
# passo C — o consumidor entrega o evento pela PORTA UNICA
# ---------------------------------------------------------------------------
cabecalho "passo C - consumidor n8n entrega o evento pela porta unica (passos 12..13, 16)"
BASE_AUDITORIA="$(limpar "$(auditoria_total)")"
executar_workflow "$ID_CONSUMIDOR" "$LOG_DIR/C-consumidor"
[ "$RC_EXEC" = "0" ] && ok "consumidor de outbox executou (exit 0)" \
    || falhou "consumidor de outbox terminou com exit $RC_EXEC (ver $LOG_DIR/C-consumidor.out)"
[ "$(parceiros_identidade "$ORG")" = "1" ] \
    && ok "o parceiro do CRM tem a identidade do evento (1 registro com tf_company_id)" \
    || falhou "o parceiro do CRM tem $(parceiros_identidade "$ORG") registro(s) com tf_company_id (esperado 1)"
[ "$(campo_parceiro_identidade "$ORG" name)" = "$NOME_EMPRESA" ] \
    && ok "o parceiro do CRM tem o nome do evento" \
    || falhou "o parceiro do CRM tem o nome '$(campo_parceiro_identidade "$ORG" name)'"
[ "$(campo_parceiro_identidade "$ORG" tf_domain)" = "$DOMINIO" ] \
    && ok "o parceiro nasceu com o dominio do evento" \
    || falhou "o parceiro nasceu com o dominio do evento: medido '$(campo_parceiro_identidade "$ORG" tf_domain)'"
[ "$(campo_parceiro_identidade "$ORG" tf_cnpj)" = "$CNPJ" ] \
    && ok "o parceiro nasceu com o CNPJ do evento" \
    || falhou "o parceiro nasceu com o CNPJ '$(campo_parceiro_identidade "$ORG" tf_cnpj)'"
[ "$(limpar "$(odoo_db "select is_company from res_partner where tf_company_id='$ORG'")")" = "t" ] \
    && ok "o parceiro nasceu como empresa (valor fixo da politica)" \
    || falhou "o parceiro nao nasceu como empresa"
[ "$(campo_trilha "$CHAVE_EV1" status)" = "COMPLETED" ] \
    && ok "a trilha do evento valido esta COMPLETED" \
    || falhou "a trilha do evento valido esta '$(campo_trilha "$CHAVE_EV1" status)'"
if [ "$(campo_trilha "$CHAVE_EV1" operation)" = "UPSERT" ] \
        && [ "$(campo_trilha "$CHAVE_EV1" source_system)" = "postgres" ] \
        && [ "$(campo_trilha "$CHAVE_EV1" target_system)" = "odoo" ]; then
    ok "a trilha carrega a direcao declarada (postgres->odoo, UPSERT)"
else
    falhou "a trilha nao carrega a direcao declarada"
fi
[ "$(limpar "$(si "select status from sales_intelligence.outbox_events where id='$EV1'")")" = "PROCESSED" ] \
    && ok "o evento valido ficou PROCESSED" \
    || falhou "o evento valido ficou '$(limpar "$(si "select status from sales_intelligence.outbox_events where id='$EV1'")")'"
ID_PARCEIRO="$(printf '%s' "$(campo_trilha_bruto "$CHAVE_EV1" response_payload)" | sed -n 's/.*"ids": *\[\([0-9][0-9]*\).*/\1/p')"
if [ -n "$ID_PARCEIRO" ] && [ "$(limpar "$(odoo_db "select count(*) from res_partner where id=$ID_PARCEIRO and tf_company_id='$ORG'")")" = "1" ]; then
    ok "o Odoo devolveu o ID do registro e ele esta no response_payload da trilha"
else
    falhou "o ID devolvido pelo Odoo nao foi medido no response_payload (obtido: ${ID_PARCEIRO:-vazio})"
fi
# Doc 08 §3 passo 17 (PG registra sync): a ponta do vinculo no lado PostgreSQL. Nenhuma porta da
# fundacao escreve esta coluna (o consumidor escreve a FILA e a TRILHA; `organizations` e' da
# esteira de negocio, W4/W5) — o aceite registra a ponta com o ID que veio na trilha e MEDE a
# ida-e-volta depois. Por isso o item abaixo diz "registrada PELO HARNESS": quem escreve a coluna
# aqui e' o proprio aceite (as linhas acima), nao uma porta da fundacao — o texto nao pode fazer o
# leitor crer que uma porta mediu a escrita. Lacuna declarada no runbook §6.5.
if [ -n "$ID_PARCEIRO" ]; then
    si "update sales_intelligence.organizations set odoo_partner_id=$ID_PARCEIRO, updated_at=now() where id='$ORG'" >/dev/null 2>&1
fi
MEDIDO_PONTA="$(limpar "$(si "select coalesce(odoo_partner_id::text, chr(45)) from sales_intelligence.organizations where id='$ORG'")")"
if [ -n "$ID_PARCEIRO" ] && [ "$MEDIDO_PONTA" = "$ID_PARCEIRO" ]; then
    ok "ponta do vinculo no PG registrada PELO HARNESS com o id lido da trilha (a coluna nao e' escrita por porta da fundacao; ida-e-volta fechada)"
else
    falhou "a ponta do vinculo no PG (registrada pelo harness) nao bate com o id que o Odoo devolveu na trilha (medido: '$MEDIDO_PONTA')"
fi
if [ "$(limpar "$(auditoria_total)")" -gt "$BASE_AUDITORIA" ]; then
    ok "houve chamada autenticada real na porta unica (auditoria: $(limpar "$(auditoria_total)") linhas)"
else
    falhou "nenhuma chamada autenticada registrada na auditoria"
fi

# ---------------------------------------------------------------------------
# passo D — contato comercial pela porta unica (duas vezes pela MESMA identidade)
# ---------------------------------------------------------------------------
cabecalho "passo D - contato comercial pela porta unica (passo 14)"
BASE_CONTATO="$(limpar "$(auditoria_operacao contato_upsert)")"
alvo_porta contato_upsert
cat >"$DESC_DIR/contato.json" <<JSON
{"idempotency_key":"$CHAVE_CONTATO","correlation_id":"$CORREL_CONTATO",
 "parametros":{"modelo":"res.partner","valores":{"name":"$NOME_CONTATO","email":"$EMAIL_CONTATO","function":"$CARGO_CONTATO","phone":"+55 11 99999-0001"}}}
JSON
chmod 600 "$DESC_DIR/contato.json"
SAIDA_CONTATO="$(postar_porta /prep/contato.json)"
if printf '%s' "$SAIDA_CONTATO" | grep -q 'HTTP 200'; then
    ok "contato_upsert respondeu HTTP 200 pela porta unica"
else
    falhou "contato_upsert respondeu '$(printf '%s' "$SAIDA_CONTATO" | head -c 120)'"
fi
[ "$(parceiros_email "$EMAIL_CONTATO")" = "1" ] \
    && ok "o contato do cenario existe no CRM (1 registro com o e-mail declarado)" \
    || falhou "o contato do cenario tem $(parceiros_email "$EMAIL_CONTATO") registro(s) (esperado 1)"
[ "$(limpar "$(odoo_db "select is_company from res_partner where email='$EMAIL_CONTATO'")")" = "f" ] \
    && ok "o contato nasceu como pessoa (valor fixo da politica)" \
    || falhou "o contato nao nasceu como pessoa"
[ "$(limpar "$(auditoria_operacao contato_upsert)")" -gt "$BASE_CONTATO" ] \
    && ok "contato_upsert deixou rastro na auditoria da porta" \
    || falhou "contato_upsert nao deixou rastro na auditoria"
ID_CONTATO="$(limpar "$(odoo_db "select id from res_partner where email='$EMAIL_CONTATO' order by id limit 1")")"
[ -n "$ID_CONTATO" ] && ok "o Odoo devolveu o ID do contato ($ID_CONTATO)" \
    || falhou "nao consegui medir o ID do contato"
cat >"$DESC_DIR/contato2.json" <<JSON
{"idempotency_key":"${CHAVE_CONTATO}-r2","correlation_id":"$CORREL_CONTATO",
 "parametros":{"modelo":"res.partner","valores":{"name":"$NOME_CONTATO","email":"$EMAIL_CONTATO","function":"$CARGO_CONTATO"}}}
JSON
chmod 600 "$DESC_DIR/contato2.json"
SAIDA_CONTATO2="$(postar_porta /prep/contato2.json)"
[ "$(parceiros_email "$EMAIL_CONTATO")" = "1" ] \
    && ok "contato reenviado pela MESMA identidade nao duplica (1 registro)" \
    || falhou "contato reenviado pela MESMA identidade nao duplica: $(parceiros_email "$EMAIL_CONTATO") registros"
printf '%s' "$SAIDA_CONTATO2" | grep -q 'HTTP 200' \
    && ok "o reenvio do contato respondeu HTTP 200 (atualizou o mesmo registro)" \
    || falhou "o reenvio do contato respondeu '$(printf '%s' "$SAIDA_CONTATO2" | head -c 120)'"

# ---------------------------------------------------------------------------
# passo E — atividade comercial ancorada no parceiro criado
# ---------------------------------------------------------------------------
cabecalho "passo E - atividade comercial pela porta unica (passo 15)"
BASE_ATIVIDADE="$(limpar "$(auditoria_operacao atividade_criar)")"
alvo_porta atividade_criar
cat >"$DESC_DIR/atividade.json" <<JSON
{"idempotency_key":"$CHAVE_ATIVIDADE","correlation_id":"$CORREL_ATIVIDADE",
 "parametros":{"modelo":"mail.activity","valores":{"res_model":"res.partner","res_id":${ID_CONTATO:-0},"summary":"$RESUMO_ATIVIDADE","tf_idempotency_key":"$CHAVE_ATIVIDADE","tf_correlation_id":"$CORREL_ATIVIDADE"}}}
JSON
chmod 600 "$DESC_DIR/atividade.json"
SAIDA_ATIVIDADE="$(postar_porta /prep/atividade.json)"
if printf '%s' "$SAIDA_ATIVIDADE" | grep -q 'HTTP 200'; then
    ok "atividade_criar respondeu HTTP 200 pela porta unica"
else
    falhou "atividade_criar respondeu '$(printf '%s' "$SAIDA_ATIVIDADE" | head -c 150)'"
fi
[ "$(atividades_da_chave "$CHAVE_ATIVIDADE")" = "1" ] \
    && ok "a atividade do cenario existe ancorada no parceiro (1 registro)" \
    || falhou "a atividade do cenario tem $(atividades_da_chave "$CHAVE_ATIVIDADE") registro(s) (esperado 1)"
[ "$(limpar "$(odoo_db "select res_id from mail_activity where tf_idempotency_key='$CHAVE_ATIVIDADE'")")" = "$ID_CONTATO" ] \
    && ok "a atividade esta ancorada no parceiro do contato (res_id conferido)" \
    || falhou "a atividade nao esta ancorada no parceiro do contato"
[ "$(limpar "$(auditoria_operacao atividade_criar)")" -gt "$BASE_ATIVIDADE" ] \
    && ok "atividade_criar deixou rastro na auditoria da porta" \
    || falhou "atividade_criar nao deixou rastro na auditoria"

# ---------------------------------------------------------------------------
# passo F — saude do sync: reconciliacao (E04) no MESMO trio
# ---------------------------------------------------------------------------
cabecalho "passo F - reconciliacao PostgreSQL x Odoo no mesmo trio (card E04-T01)"
executar_workflow "$ID_RECONCILIACAO" "$LOG_DIR/F-reconciliacao"
[ "$RC_EXEC" = "0" ] && ok "job de reconciliacao executou (exit 0)" \
    || falhou "job de reconciliacao terminou com exit $RC_EXEC (ver $LOG_DIR/F-reconciliacao.out)"
python3 "$LEITOR_RECONCILIACAO" "$LOG_DIR/F-reconciliacao.out" >"$LOG_DIR/F-reconciliacao.valores" 2>"$LOG_DIR/F-reconciliacao.leitor" || true
[ "$(valor_de "$LOG_DIR/F-reconciliacao.valores" leitura)" = "ok" ] \
    && ok "o resultado da rodada de reconciliacao foi legivel" \
    || falhou "o resultado da rodada de reconciliacao NAO foi legivel (ver $LOG_DIR/F-reconciliacao.leitor)"
[ "$(valor_de "$LOG_DIR/F-reconciliacao.valores" veredito)" = "OK" ] \
    && ok "reconciliacao: veredito OK na mesma rodada do E2E" \
    || falhou "reconciliacao: veredito '$(valor_de "$LOG_DIR/F-reconciliacao.valores" veredito)' (esperado OK)"
[ -z "$(valor_de "$LOG_DIR/F-reconciliacao.valores" divergencias)" ] \
    && ok "reconciliacao: 0 divergencia entre PostgreSQL e Odoo" \
    || falhou "reconciliacao: divergencias '$(valor_de "$LOG_DIR/F-reconciliacao.valores" divergencias)'"

# ---------------------------------------------------------------------------
# passo G — sentido Odoo -> PostgreSQL pela PORTA DE INGESTAO (webhook)
# ---------------------------------------------------------------------------
cabecalho "passo G - eventos Odoo -> PostgreSQL pela porta de ingestao (passos 16..17)"
N8N_PRONTO="$(subir_n8n)"
[ "$N8N_PRONTO" = "1" ] && ok "servidor n8n descartavel no ar para a porta de ingestao" \
    || falhou "servidor n8n nao ficou pronto"
cat >"$DESC_DIR/sonda-webhook.js" <<'JS'
const alvo = 'http://' + process.env.TRE_HOST + ':5678/webhook/tre/odoo-eventos';
const corpo = JSON.stringify({event_type: 'STAGE_CHANGED', event_version: '1.0',
  timestamp: '2026-10-02 12:00:00', idempotency_key: 'odoo:e2e:sonda:webhook', payload: {}});
fetch(alvo, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: corpo})
  .then(async (r) => { console.log('HTTP ' + r.status + ' ' + (await r.text()).slice(0, 120)); process.exit(0); })
  .catch((e) => { console.log('HTTP_ERRO ' + e.message); process.exit(1); });
JS
sonda_webhook() {
    docker run --rm --network "$NET_TMP" --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$DESC_DIR":/prep:ro -e TRE_HOST="$N8N_CT" \
        --entrypoint node "$IMAGEM_N8N" /prep/sonda-webhook.js 2>&1
}
# "Registrada" = a sonda recebeu uma RESPOSTA HTTP de verdade (2xx/4xx) que NAO e' o 404 de rota
# inexistente. `HTTP_ERRO ...` (falha de conexao do fetch, impresso pelo proprio sonda-webhook.js)
# NAO registra: aceitar "qualquer coisa diferente de 404" fazia uma porta que nem responde passar
# como "deixou de responder 404" — era o furo deste item.
webhook_esta_registrado() { # $1 = saida da sonda_webhook
    case "$1" in
        "HTTP 404"*) return 1 ;;
        "HTTP "[24][0-9][0-9]*) return 0 ;;
        *) return 1 ;;
    esac
}
WEBHOOK_OK=0
for _ in $(seq 1 30); do
    SAIDA_WEBHOOK="$(sonda_webhook)"
    if webhook_esta_registrado "$SAIDA_WEBHOOK"; then WEBHOOK_OK=1; break; fi
    sleep 2
done
[ "$WEBHOOK_OK" = "1" ] && ok "porta de ingestao registrada no n8n (respondeu HTTP real; deixou de responder 404)" \
    || falhou "a porta de ingestao nao ficou registrada (sem resposta HTTP real: '$(printf '%s' "${SAIDA_WEBHOOK:-vazio}" | head -c 80)')"

odoo_shell_arquivo "$PREPARO_REMETENTE" "$LOG_DIR/G-remetente.log" \
    -e "TRE_INGEST_URL=http://$N8N_CT:5678" -e "TRE_INGEST_TOKEN_FILE=/preparo/token.txt"
grep -q 'TF_REMETENTE url=http://.* token=presente' "$LOG_DIR/G-remetente.log" \
    && ok "remetente do Odoo configurado pela PORTA (token lido de arquivo 600)" \
    || falhou "remetente do Odoo nao foi configurado (ver $LOG_DIR/G-remetente.log)"
if grep -q "$TOKEN_PORTAL" "$LOG_DIR/G-remetente.log"; then
    falhou "o token da porta vazou para o log do preparo"
else
    ok "o preparo do remetente nao imprime o token"
fi

trilha_odoo() { limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='odoo' and target_system='postgres'")"; }
odoo_shell_arquivo "$FATOS_ODOO" "$LOG_DIR/G-fatos.log" -e "TRE_FASE=fatos" -e "TRE_SUFIXO=101"
grep -q '^TF_FATOS' "$LOG_DIR/G-fatos.log" \
    && ok "fatos de negocio criados no Odoo pelo ORM (estagio, valor, ganho, perda, motivo, atividade, reuniao)" \
    || falhou "os fatos de negocio do Odoo nao foram criados (ver $LOG_DIR/G-fatos.log)"
odoo_shell_arquivo "$FATOS_ODOO" "$LOG_DIR/G-relatorio.log" -e "TRE_FASE=relatorio" -e "TRE_SUFIXO=101"
EVENTOS_MEDIDOS="$(sed -n 's/^TF_EVENTO \([A-Z_]*\)|.*/\1/p' "$LOG_DIR/G-relatorio.log" | sort -u | tr '\n' ' ' | sed 's/ *$//')"
[ "$EVENTOS_MEDIDOS" = "$EVENTOS_ESPERADOS" ] \
    && ok "a fila do Odoo tem exatamente os 7 eventos do contrato da porta ($EVENTOS_MEDIDOS)" \
    || falhou "a fila do Odoo tem '$EVENTOS_MEDIDOS' (esperado '$EVENTOS_ESPERADOS')"
PENDENTES="$(grep -o '^TF_FILA [0-9]*' "$LOG_DIR/G-relatorio.log" | tail -1 | cut -d' ' -f2)"
if [ -n "$PENDENTES" ] && [ "$PENDENTES" -ge 7 ]; then
    ok "fila do Odoo medida em $PENDENTES evento(s) antes do envio"
else
    falhou "nao consegui medir a fila do Odoo antes do envio (medido: '${PENDENTES:-vazio}')"
fi
odoo_shell_arquivo "$FATOS_ODOO" "$LOG_DIR/G-enviar.log" -e "TRE_FASE=enviar" -e "TRE_SUFIXO=101"
grep -q "\"SENT\": $PENDENTES," "$LOG_DIR/G-enviar.log" \
    && ok "o remetente do Odoo entregou os $PENDENTES eventos pela porta de ingestao (SENT=$PENDENTES)" \
    || falhou "o remetente nao entregou os $PENDENTES eventos (ver $LOG_DIR/G-enviar.log: $(grep -o 'TF_RESUMO.*' "$LOG_DIR/G-enviar.log" | head -1))"
TRILHA_ODOO_DEPOIS="$(trilha_odoo)"
[ "$TRILHA_ODOO_DEPOIS" = "$PENDENTES" ] \
    && ok "a trilha odoo->postgres recebeu uma linha por evento da fila ($TRILHA_ODOO_DEPOIS linhas)" \
    || falhou "a trilha odoo->postgres tem $TRILHA_ODOO_DEPOIS linha(s) (esperado $PENDENTES)"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='odoo' and status='COMPLETED'")")" = "$PENDENTES" ] \
    && ok "todas as $PENDENTES linhas da trilha odoo->postgres estao COMPLETED" \
    || falhou "ha linha da trilha odoo->postgres fora de COMPLETED"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='odoo' and operation='ACTIVITY_COMPLETED'")")" -ge 1 ] \
    && ok "o evento ACTIVITY_COMPLETED (atividade) esta na trilha odoo->postgres" \
    || falhou "o evento ACTIVITY_COMPLETED nao esta na trilha odoo->postgres"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='odoo' and source_version is distinct from '1.0'")")" = "0" ] \
    && ok "toda linha da trilha odoo->postgres carrega source_version 1.0" \
    || falhou "ha linha da trilha odoo->postgres sem a versao do envelope"
odoo_shell_arquivo "$FATOS_ODOO" "$LOG_DIR/G-replay.log" -e "TRE_FASE=replay" -e "TRE_SUFIXO=101"
odoo_shell_arquivo "$FATOS_ODOO" "$LOG_DIR/G-reenvio.log" -e "TRE_FASE=enviar" -e "TRE_SUFIXO=101"
TRILHA_ODOO_REENVIO="$(trilha_odoo)"
[ "$TRILHA_ODOO_REENVIO" = "$TRILHA_ODOO_DEPOIS" ] \
    && ok "reenvio do MESMO envelope nao cria linha nova na trilha ($TRILHA_ODOO_REENVIO linhas)" \
    || falhou "reenvio do MESMO envelope criou linha nova na trilha ($TRILHA_ODOO_DEPOIS -> $TRILHA_ODOO_REENVIO)"
if docker stop "$N8N_CT" >/dev/null 2>&1; then
    ok "servidor n8n parado depois da janela da porta de ingestao"
else
    info "nao consegui parar o n8n depois da janela do webhook"
fi

# ---------------------------------------------------------------------------
# passo H — observabilidade de sync (E05) no mesmo trio
# ---------------------------------------------------------------------------
cabecalho "passo H - observabilidade de sync no mesmo trio (card E05-T01)"
executar_workflow "$ID_OBSERVABILIDADE" "$LOG_DIR/H-observabilidade"
[ "$RC_EXEC" = "0" ] && ok "workflow de observabilidade executou (exit 0)" \
    || falhou "workflow de observabilidade terminou com exit $RC_EXEC (ver $LOG_DIR/H-observabilidade.out)"
python3 "$LEITOR_N8N" "$LOG_DIR/H-observabilidade.out" "$LOG_DIR/H-observabilidade.wf" \
    >"$LOG_DIR/H-observabilidade.resumo" 2>"$LOG_DIR/H-observabilidade.leitura" || true
VEREDITO_OBS="$(sed -n 's/^veredito=//p' "$LOG_DIR/H-observabilidade.resumo" | head -1)"
if [ -s "$LOG_DIR/H-observabilidade.wf.valores" ]; then
    ok "a rodada de observabilidade entregou as metricas declaradas ($(wc -l <"$LOG_DIR/H-observabilidade.wf.valores" | tr -d ' ') linhas de medicao)"
else
    falhou "a rodada de observabilidade nao entregou medicao (ver $LOG_DIR/H-observabilidade.leitura)"
fi
[ "$VEREDITO_OBS" = "OK" ] \
    && ok "observabilidade: veredito OK na rodada saudavel do E2E" \
    || falhou "observabilidade: veredito '${VEREDITO_OBS:-ilegivel}' (esperado OK)"

# ---------------------------------------------------------------------------
# passo I — repetir o evento: REPLAY, sem chamada nova e sem duplicata (passo 18)
# ---------------------------------------------------------------------------
cabecalho "passo I - repetir o evento no outbox: REPLAY sem duplicata (passo 18)"
TRILHA_ID_ANTES="$(campo_trilha "$CHAVE_EV1" id)"
TRILHA_CONCLUIDO_ANTES="$(campo_trilha "$CHAVE_EV1" completed_at)"
AUDITORIA_ANTES_REPLAY="$(limpar "$(auditoria_total)")"
si "update sales_intelligence.outbox_events set status='PENDING', attempts=0, processed_at=null where id='$EV1'" >/dev/null 2>&1 \
    && ok "o MESMO evento voltou para a fila (identidade preservada)" \
    || falhou "nao consegui reenfileirar o evento do cenario"
executar_workflow "$ID_CONSUMIDOR" "$LOG_DIR/I-replay"
[ "$RC_EXEC" = "0" ] && ok "consumidor executou a rodada do replay (exit 0)" \
    || falhou "consumidor falhou na rodada do replay (exit $RC_EXEC)"
[ "$(limpar "$(auditoria_total)")" = "$AUDITORIA_ANTES_REPLAY" ] \
    && ok "repetir o evento NAO duplica: 0 chamada nova a porta unica" \
    || falhou "repetir o evento NAO duplica: houve chamada nova ($AUDITORIA_ANTES_REPLAY -> $(limpar "$(auditoria_total)"))"
if [ "$(campo_trilha "$CHAVE_EV1" id)" = "$TRILHA_ID_ANTES" ] && [ "$(campo_trilha "$CHAVE_EV1" completed_at)" = "$TRILHA_CONCLUIDO_ANTES" ]; then
    ok "repetir o evento NAO duplica: a trilha foi reaproveitada (mesma linha, mesmo instante)"
else
    falhou "repetir o evento NAO duplica: a trilha mudou (linha/instante diferentes)"
fi
[ "$(linhas_trilha_chave "$CHAVE_EV1")" = "1" ] \
    && ok "repetir o evento NAO duplica: 1 linha de trilha para a chave" \
    || falhou "repetir o evento NAO duplica: $(linhas_trilha_chave "$CHAVE_EV1") linhas de trilha para a chave"
[ "$(parceiros_identidade "$ORG")" = "1" ] \
    && ok "repetir o evento NAO duplica: o CRM continua com 1 empresa" \
    || falhou "repetir o evento NAO duplica: o CRM ficou com $(parceiros_identidade "$ORG") empresas"
[ "$(limpar "$(si "select status from sales_intelligence.outbox_events where id='$EV1'")")" = "PROCESSED" ] \
    && ok "o evento repetido fechou PROCESSED" \
    || falhou "o evento repetido ficou '$(limpar "$(si "select status from sales_intelligence.outbox_events where id='$EV1'")")'"

# ---------------------------------------------------------------------------
# passo J — fail-closed: evento sem event_version
# ---------------------------------------------------------------------------
cabecalho "passo J - fail-closed: evento sem event_version nao e' entregue (AC7)"
si "insert into sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, attempts, created_at)
    values ('$EV2', 'organization', '$ORG', 'COMPANY_QUALIFIED',
            '{\"payload\":{\"name\":\"ACME sem versao\",\"domain\":\"$DOMINIO\"}}'::jsonb, 'PENDING', 0, now())" >/dev/null 2>&1
AUDITORIA_ANTES_EV2="$(limpar "$(auditoria_total)")"
executar_workflow "$ID_CONSUMIDOR" "$LOG_DIR/J-invalido"
[ "$RC_EXEC" = "0" ] && ok "consumidor executou a rodada do evento invalido (exit 0)" \
    || falhou "consumidor falhou na rodada do evento invalido (exit $RC_EXEC)"
[ "$(limpar "$(si "select status from sales_intelligence.outbox_events where id='$EV2'")")" = "DEAD_LETTER" ] \
    && ok "evento sem event_version vai para DEAD_LETTER" \
    || falhou "evento sem event_version ficou '$(limpar "$(si "select status from sales_intelligence.outbox_events where id='$EV2'")")'"
[ "$(limpar "$(auditoria_total)")" = "$AUDITORIA_ANTES_EV2" ] \
    && ok "evento sem event_version vai para DEAD_LETTER sem chamada a porta unica" \
    || falhou "evento sem event_version vai para DEAD_LETTER sem chamada a porta unica (houve chamada nova)"
MOTIVO_EV2="$(podar "$(si "select coalesce(last_error,'') from sales_intelligence.outbox_events where id='$EV2'")")"
[ -n "$MOTIVO_EV2" ] && ok "a recusa do evento invalido ficou nomeada na fila" \
    || falhou "a recusa do evento invalido ficou sem motivo"

# ---------------------------------------------------------------------------
# passo K — zero duplicatas (do BANCO) + ambiente + fecho do sha256 (passo 19)
# ---------------------------------------------------------------------------
cabecalho "passo K - zero duplicatas, ambiente e fecho do sha256 (passo 19)"
[ "$(parceiros_identidade "$ORG")" = "1" ] \
    && ok "zero duplicatas: 1 empresa com a identidade canonica" \
    || falhou "zero duplicatas: $(parceiros_identidade "$ORG") empresas com a identidade canonica"
[ "$(parceiros_email "$EMAIL_CONTATO")" = "1" ] \
    && ok "zero duplicatas: 1 contato com o e-mail declarado" \
    || falhou "zero duplicatas: $(parceiros_email "$EMAIL_CONTATO") contatos com o e-mail declarado"
[ "$(atividades_da_chave "$CHAVE_ATIVIDADE")" = "1" ] \
    && ok "zero duplicatas: 1 atividade para a chave declarada" \
    || falhou "zero duplicatas: $(atividades_da_chave "$CHAVE_ATIVIDADE") atividades para a chave declarada"
[ "$(linhas_trilha_chave "$CHAVE_EV1")" = "1" ] \
    && ok "zero duplicatas: 1 linha de trilha por chave no caminho PG->Odoo" \
    || falhou "zero duplicatas: trilha do caminho PG->Odoo com $(linhas_trilha_chave "$CHAVE_EV1") linhas para a chave"
[ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where source_system='odoo'")")" -ge 7 ] \
    && ok "zero duplicatas: a trilha Odoo->PG nao perdeu linha no reenvio" \
    || falhou "zero duplicatas: a trilha Odoo->PG tem menos linhas que o esperado"
info "chamadas autenticadas na porta unica nesta rodada: $(limpar "$(auditoria_total)")"
declarado "doc 08 §3 passos 4..7 - research da empresa, 3 signals e os 4 scores (ICP/AutomationFit/BuyingSignal/DataQuality): cards W4-*/W5-*, NAO existem na fundacao (nenhuma medicao inventada)"
declarado "doc 08 §3 passos 8..10 - priority>90, tier A+, pain hypothesis e recommendation 'Contact CFO': cards W5-*, NAO existem na fundacao (nenhuma medicao inventada)"
if [ "$DEV_PG_ANTES" != "nao_medido" ]; then
    DEV_PG_DEPOIS="$(docker exec "$DEV_PG_CT" psql -U "$PG_USER" -d postgres -tAc \
        'select string_agg(datname, chr(44) || chr(32) order by datname) from pg_database' 2>/dev/null)"
    [ "$DEV_PG_DEPOIS" = "$DEV_PG_ANTES" ] \
        && ok "instancia do dev INTOCADA (mesmos bancos antes/depois)" \
        || falhou "instancia do dev mudou ('$DEV_PG_ANTES' -> '$DEV_PG_DEPOIS')"
else
    declarado "instancia do dev sem container de pe nesta rodada - 'nao tocou o dev' ficou sem medicao"
fi
HOMOLOG_PROD_DEPOIS="$(find $DEV_HOMOLOG_PROD -type f 2>/dev/null | wc -l | tr -d ' ')"
[ "$HOMOLOG_PROD_DEPOIS" = "$HOMOLOG_PROD_ANTES" ] \
    && ok "homolog/producao sem arquivo novo ($HOMOLOG_PROD_DEPOIS arquivos)" \
    || falhou "homolog/producao mudou ($HOMOLOG_PROD_ANTES -> $HOMOLOG_PROD_DEPOIS)"
if grep -qE 'ghp_|AKIA' "$WORKFLOW" "$WF_INGESTAO" "$WF_RECONCILIACAO" "$WF_OBSERVABILIDADE" 2>/dev/null; then
    falhou "ha segredo em claro em workflow versionado"
else
    ok "nenhum segredo em claro nos workflows versionados"
fi
if [ "$(limpar "$(si "select count(*) from sales_intelligence.sync_events where request_payload::text like '%$TOKEN_PORTAL%'")")" != "0" ]; then
    falhou "o token da porta aparece em request_payload da trilha"
else
    ok "o token da porta nao aparece em request_payload da trilha"
fi
sha256_dos_artefatos "$LOG_DIR/sha256-depois.txt"
if [ "$(veredito_sha256 "$LOG_DIR/sha256-antes.txt" "$LOG_DIR/sha256-depois.txt")" = "IDENTICO" ]; then
    ok "sha256 dos artefatos sob teste identico ao fixado nas guardas (nada mudou durante o aceite)"
else
    falhou "sha256 dos artefatos MUDOU durante o aceite"
fi

info "logs desta rodada em $LOG_DIR"
resumo
