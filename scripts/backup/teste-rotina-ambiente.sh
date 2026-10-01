#!/usr/bin/env bash
# =====================================================================================
# teste-rotina-ambiente.sh — teste da RESOLUCAO DE AMBIENTE da rotina de backup.
#
# Prova (e reprova) o defeito do card t_1b2ab418: `backup-tre.sh todos` cobria ZERO
# ambientes e saia `BACKUP_OK` com exit 0 e nenhum artefato, porque procurava `pg-dev`
# enquanto o dev real e `pg-sales-dev` (e nenhum timer lia deploy/environments/dev.env).
#
# Roda em qualquer maquina, SEM tocar container nenhum: `docker` e um DUBLE no PATH
# (só `inspect`, `exec` e os subcomandos usados pela rotina) e o destino/raiz sao
# temporarios. O teste e o mesmo para o script novo e para o antigo — para o antigo se
# passar o caminho (TRE_F2_SCRIPT_ANTIGO / TRE_F2_VERIFICADOR_ANTIGO) e a rodada fica
# ao contrario, o que prova que o teste REPROVAVA antes da correcao.
#
# Uso:
#   scripts/backup/teste-rotina-ambiente.sh
#   TRE_F2_SCRIPT_ANTIGO=/tmp/backup-tre.antigo.sh \
#   TRE_F2_VERIFICADOR_ANTIGO=/tmp/verificar-ultimo-backup.antigo.sh \
#     scripts/backup/teste-rotina-ambiente.sh
#
# Saida: OK/FALHOU item a item; `RESULTADO: TESTE_OK` exit 0 ou `TESTE_FALHOU` exit 1.
# =====================================================================================
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ_REPO="$(cd "$AQUI/../.." && pwd)"
ROTINA="$RAIZ_REPO/scripts/backup/backup-tre.sh"
VERIFICADOR="$RAIZ_REPO/scripts/backup/verificar-ultimo-backup.sh"
ROTINA_ANTIGA="${TRE_F2_SCRIPT_ANTIGO:-}"
VERIFICADOR_ANTIGO="${TRE_F2_VERIFICADOR_ANTIGO:-}"

ITENS=0
FALHAS=0
ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }
nota() { ITENS=$((ITENS + 1)); echo "NOTA  $*"; }
confere() { # <descricao> <esperado> <obtido>
  if [ "$2" = "$3" ]; then ok "$1 ($3)"; else ko "$1 — esperado '$2', obtido '$3'"; fi
}

TMP="$(mktemp -d "${TMPDIR:-/tmp}/tre-f2.XXXXXXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM HUP

STUB="$TMP/bin"
mkdir -p "$STUB" "$TMP/env-dev" "$TMP/env-vazio" "$TMP/env-misto" "$TMP/env-colisao" "$TMP/env-homolog-so"

# --- duble do docker -----------------------------------------------------------------
cat >"$STUB/docker" <<'DUBLE'
#!/usr/bin/env bash
# Duble de `docker` do teste-rotina-ambiente.sh: nao fala com o Docker de verdade.
set -uo pipefail
cmd="${1:-}"; shift || true
existe() {
  local alvo="$1" n
  for n in ${TRE_TESTE_DOCKER_EXISTENTES:-}; do [ "$n" = "$alvo" ] && return 0; done
  return 1
}
case "$cmd" in
  inspect)
    if existe "${1:-}"; then echo '[{"Id":"duble"}]'; exit 0; fi
    echo "Error: No such object: ${1:-}" >&2; exit 1 ;;
  exec)
    servico="${1:-}"; shift || true
    if ! existe "$servico"; then echo "Error: No such container: $servico" >&2; exit 1; fi
    sub="${1:-}"; shift || true
    case "$sub" in
      pg_isready) exit 0 ;;
      pg_dump)    printf 'PGDMP-falso\n' ;;
      pg_dumpall) printf -- '-- globais falsos do duble\n' ;;
      psql)
        sql="${!#}"
        case "$sql" in
          *"SHOW server_version"*) printf '16.15\n' ;;
          *string_agg*"information_schema.tables"*)
            printf "SELECT 'organizations' AS tabela, count(*)::bigint AS linhas FROM sales_intelligence.organizations UNION ALL SELECT 'signals' AS tabela, count(*)::bigint AS linhas FROM sales_intelligence.signals\n" ;;
          *"FROM information_schema.tables"*) printf '2\n' ;;
          *pg_indexes*)                       printf '3\n' ;;
          "SELECT 'organizations'"*)          printf 'organizations|2\nsignals|1\n' ;;
          *)                                  printf '0\n' ;;
        esac ;;
      *) echo "duble: subcomando nao suportado: $sub" >&2; exit 99 ;;
    esac ;;
  run)
    # duble do `docker run --rm --entrypoint tar -v <volume>:/origem:ro -v <dir>:/destino <imagem> -czf /destino/<arquivo> -C /origem .`
    # (o backup do Odoo empacota o filestore por container efemero — card TRE-W2-E01-T01-F01)
    destino=""; arquivo=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -v) par="${2:-}"; shift 2
            case "$par" in *:/destino) destino="${par%%:/destino*}" ;; esac ;;
        -czf) arquivo="$(basename "${2:-}")"; shift 2 ;;
        *) shift ;;
      esac
    done
    if [ -z "$destino" ] || [ -z "$arquivo" ]; then
      echo "duble: run sem '-v ...:/destino' ou sem '-czf'" >&2; exit 99
    fi
    printf 'TAR-FALSO\n' >"$destino/$arquivo"
    exit 0 ;;
  *) echo "duble: comando nao suportado: $cmd" >&2; exit 99 ;;
esac
DUBLE
chmod 755 "$STUB/docker"

# --- configuracoes dos cenarios ------------------------------------------------------
cp "$RAIZ_REPO/deploy/environments/dev.env" "$TMP/env-dev/dev.env"
printf 'TRE_PG_SERVICO=pg-sales-homolog\nTRE_PG_USER=sales_ai\nTRE_PG_DB=sales_intelligence\n' >"$TMP/env-misto/homolog.env"
cp "$RAIZ_REPO/deploy/environments/dev.env" "$TMP/env-misto/dev.env"
printf 'TRE_PG_SERVICO=pg-sales-dev\nTRE_PG_USER=sales_ai\nTRE_PG_DB=sales_intelligence\n' >"$TMP/env-colisao/homolog.env"
cp "$RAIZ_REPO/deploy/environments/dev.env" "$TMP/env-colisao/dev.env"
printf 'TRE_PG_SERVICO=pg-homolog\nTRE_PG_USER=tre\nTRE_PG_DB=sales_intelligence\n' >"$TMP/env-homolog-so/homolog.env"


# rotina <log> <containers> <env_dir> <dest> <script> <args...> [VAR=valor extra]
rodar_rotina() {
  local log="$1" containers="$2" env_dir="$3" dest="$4" script="$5"; shift 5
  env -u TRE_PG_SERVICO -u TRE_PG_USER -u TRE_PG_DB -u TRE_PG_SERVICO_DEV \
      -u TRE_PG_SERVICO_HOMOLOG -u RCLONE_CONFIG \
      PATH="$STUB:$PATH" TRE_TESTE_DOCKER_EXISTENTES="$containers" \
      TRE_ENV_DIR="$env_dir" TRE_BACKUP_DIR="$dest" TRE_BACKUP_RETENCAO_DIAS=0 \
      TRE_BACKUP_EXTERNO= bash "$script" "$@" >"$log" 2>&1
}
rodar_verificador() {
  local log="$1" containers="$2" env_dir="$3" dest="$4" script="$5"; shift 5
  env -u TRE_PG_SERVICO -u TRE_PG_USER -u TRE_PG_DB \
      PATH="$STUB:$PATH" TRE_TESTE_DOCKER_EXISTENTES="$containers" \
      TRE_ENV_DIR="$env_dir" TRE_BACKUP_DIR="$dest" \
      bash "$script" "$@" >"$log" 2>&1
}
conta_artefatos() { find "$1" -maxdepth 1 -type d -name "tre_$2_*" 2>/dev/null | wc -l | tr -d '[:space:]'; }
campo_manifesto() { awk -F': ' -v k="$2" '$1==k{print $2; exit}' "$1/manifest.txt" 2>/dev/null; }

echo "=================================================================="
echo "-- teste da resolucao de ambiente da rotina de backup (t_1b2ab418)"
echo "-- rotina sob teste: $ROTINA"
echo "-- duble do docker:  $STUB/docker (nenhum container real e tocado)"
echo "=================================================================="

# ---------------------------------------------------------------------------------
echo
echo "== 1. POSITIVO: dev DECLARADO em deploy/environments e container real existe =="
D1="$TMP/d1"; mkdir -p "$D1"
rodar_rotina "$TMP/1.log" "pg-sales-dev pg-odoo-dev" "$TMP/env-dev" "$D1" "$ROTINA" dev
COD1=$?
confere "exit code do backup de dev" "0" "$COD1"
grep -q "RESULTADO: BACKUP_OK" "$TMP/1.log" && ok "imprime RESULTADO: BACKUP_OK" || ko "nao imprimiu BACKUP_OK: $(tail -3 "$TMP/1.log")"
grep -q "PULADO ambiente" "$TMP/1.log" && ko "ambiente dev foi PULADO (o defeito)" || ok "dev nao foi pulado"
confere "artefatos de dev criados" "1" "$(conta_artefatos "$D1" dev)"
ART1="$(find "$D1" -maxdepth 1 -type d -name 'tre_dev_*' | head -1)"
confere "manifesto: servico" "pg-sales-dev" "$(campo_manifesto "$ART1" servico)"
confere "manifesto: usuario" "sales_ai" "$(campo_manifesto "$ART1" usuario)"
confere "manifesto: banco"   "sales_intelligence" "$(campo_manifesto "$ART1" banco)"
confere "manifesto: tabelas" "2" "$(campo_manifesto "$ART1" tabelas)"
[ -s "$ART1/sales_intelligence.dump" ] && ok "dump gravado no artefato" || ko "dump ausente/vazio"
[ -s "$ART1/contagens.txt" ] && [ "$(wc -l <"$ART1/contagens.txt")" = "2" ] && ok "contagens.txt com 2 tabelas" || ko "contagens.txt inesperado"
case "$(campo_manifesto "$ART1" config)" in
  arquivo\ *dev.env) ok "manifesto registra a origem da configuracao ($(campo_manifesto "$ART1" config))" ;;
  *) ko "manifesto nao registra a config usada: '$(campo_manifesto "$ART1" config)'" ;;
esac
# Odoo no MESMO artefato do ambiente (card TRE-W2-E01-T01-F01)
grep -q "OK    odoo: container 'pg-odoo-dev' existe" "$TMP/1.log" && ok "Odoo declarado no par do ambiente foi coberto" || ko "o backup nao cobriu o Odoo: $(grep -i odoo "$TMP/1.log" | tail -2)"
[ -s "$ART1/odoo_dev.dump" ] && ok "dump do Odoo gravado no mesmo artefato" || ko "odoo_dev.dump ausente/vazio"
[ -s "$ART1/odoo-contagens.txt" ] && ok "contagens do Odoo gravadas" || ko "odoo-contagens.txt ausente"
[ -s "$ART1/odoo-filestore.tar.gz" ] && ok "filestore do Odoo empacotado no mesmo artefato" || ko "odoo-filestore.tar.gz ausente"
[ -s "$ART1/odoo-manifest.txt" ] && ok "manifesto proprio do Odoo gravado" || ko "odoo-manifest.txt ausente"
confere "manifesto: odoo_servico" "pg-odoo-dev" "$(campo_manifesto "$ART1" odoo_servico)"
confere "manifesto: odoo_banco"   "odoo_dev"    "$(campo_manifesto "$ART1" odoo_banco)"
confere "manifesto: odoo_filestore_volume" "odoo-data-dev" "$(campo_manifesto "$ART1" odoo_filestore_volume)"

# ---------------------------------------------------------------------------------
echo
echo "== 2. POSITIVO: 'todos' com a config real do timer (so dev provisionado) =="
D2="$TMP/d2"; mkdir -p "$D2"
rodar_rotina "$TMP/2.log" "pg-sales-dev pg-odoo-dev" "$TMP/env-dev" "$D2" "$ROTINA" todos
COD2=$?
confere "exit code de 'todos'" "0" "$COD2"
grep -q "RESULTADO: BACKUP_OK" "$TMP/2.log" && ok "imprime RESULTADO: BACKUP_OK" || ko "nao imprimiu BACKUP_OK"
confere "artefatos de dev"     "1" "$(conta_artefatos "$D2" dev)"
confere "artefatos de homolog" "0" "$(conta_artefatos "$D2" homolog)"
confere "artefatos de prod"    "0" "$(conta_artefatos "$D2" prod)"
grep -q "PULADO ambiente 'homolog' nao provisionado" "$TMP/2.log" && ok "homolog pulado com motivo honesto" || ko "homolog nao foi pulado como esperado"
grep -q "1 ambiente(s) coberto(s), 2 pulado(s)" "$TMP/2.log" && ok "resumo conta cobertos e pulados" || ko "resumo nao conta cobertos/pulados: $(tail -2 "$TMP/2.log")"

# ---------------------------------------------------------------------------------
echo
echo "== 3. NEGATIVO (item 3 do card): ambiente DECLARADO e container inexistente =="
D3="$TMP/d3"; mkdir -p "$D3"
rodar_rotina "$TMP/3.log" "pg-sales-dev pg-odoo-dev" "$TMP/env-misto" "$D3" "$ROTINA" todos
COD3=$?
[ "$COD3" -ne 0 ] && ok "exit code != 0 ($COD3)" || ko "saiu com exit 0 — nunca pode"
grep -q "RESULTADO: BACKUP_OK" "$TMP/3.log" && ko "IMPRIMIU BACKUP_OK (o defeito)" || ok "nao imprimiu BACKUP_OK"
grep -q "esta DECLARADO" "$TMP/3.log" && ok "aponta a declaracao como causa" || ko "nao aponta a declaracao: $(tail -3 "$TMP/3.log")"
grep -q "RESULTADO: BACKUP_FALHOU" "$TMP/3.log" && ok "declara BACKUP_FALHOU" || ko "nao declarou BACKUP_FALHOU: $(tail -2 "$TMP/3.log")"
confere "dev (provisionado) foi copiado mesmo com a falha de homolog" "1" "$(conta_artefatos "$D3" dev)"
confere "nenhum artefato criado para homolog" "0" "$(conta_artefatos "$D3" homolog)"

# ---------------------------------------------------------------------------------
echo
echo "== 4. NEGATIVO: TRE_PG_SERVICO_DEV apontando para container inexistente =="
D4="$TMP/d4"; mkdir -p "$D4"
env -u TRE_PG_SERVICO -u TRE_PG_USER -u TRE_PG_DB \
    PATH="$STUB:$PATH" TRE_TESTE_DOCKER_EXISTENTES="pg-sales-dev" \
    TRE_ENV_DIR="$TMP/env-vazio" TRE_BACKUP_DIR="$D4" TRE_BACKUP_RETENCAO_DIAS=0 \
    TRE_BACKUP_EXTERNO= TRE_PG_SERVICO_DEV=pg-nao-existe \
    bash "$ROTINA" dev >"$TMP/4.log" 2>&1
COD4=$?
[ "$COD4" -ne 0 ] && ok "exit code != 0 ($COD4)" || ko "saiu com exit 0 — nunca pode"
grep -q "RESULTADO: BACKUP_OK" "$TMP/4.log" && ko "IMPRIMIU BACKUP_OK (o defeito)" || ok "nao imprimiu BACKUP_OK"
grep -q "TRE_PG_SERVICO_DEV" "$TMP/4.log" && ok "aponta a variavel por ambiente como fonte" || ko "nao aponta a fonte: $(tail -3 "$TMP/4.log")"
confere "nenhum artefato criado" "0" "$(conta_artefatos "$D4" dev)"

# ---------------------------------------------------------------------------------
echo
echo "== 5. NEGATIVO: dev e homolog apontando para o MESMO container em 'todos' =="
D5="$TMP/d5"; mkdir -p "$D5"
rodar_rotina "$TMP/5.log" "pg-sales-dev pg-odoo-dev" "$TMP/env-colisao" "$D5" "$ROTINA" todos
COD5=$?
[ "$COD5" -ne 0 ] && ok "exit code != 0 ($COD5)" || ko "saiu com exit 0 — nunca pode"
grep -q "RESULTADO: BACKUP_OK" "$TMP/5.log" && ko "IMPRIMIU BACKUP_OK com colisao de origem" || ok "nao imprimiu BACKUP_OK"
grep -q "ja usado por outro ambiente" "$TMP/5.log" && ok "nomeia a colisao de origem" || ko "nao nomeia a colisao: $(tail -3 "$TMP/5.log")"
confere "so o primeiro ambiente foi copiado" "1" "$(conta_artefatos "$D5" dev)"
confere "homolog nao ganhou artefato"        "0" "$(conta_artefatos "$D5" homolog)"

# ---------------------------------------------------------------------------------
echo
echo "== 6. NEGATIVO (armadilha): TRE_PG_SERVICO global unico + 'todos' =="
D6="$TMP/d6"; mkdir -p "$D6"
env -u TRE_PG_SERVICO_DEV -u TRE_PG_USER -u TRE_PG_DB \
    PATH="$STUB:$PATH" TRE_TESTE_DOCKER_EXISTENTES="pg-sales-dev" \
    TRE_ENV_DIR="$TMP/env-vazio" TRE_BACKUP_DIR="$D6" TRE_BACKUP_RETENCAO_DIAS=0 \
    TRE_BACKUP_EXTERNO= TRE_PG_SERVICO=pg-sales-dev \
    bash "$ROTINA" todos >"$TMP/6.log" 2>&1
grep -q "RESULTADO: BACKUP_OK" "$TMP/6.log" && ko "IMPRIMIU BACKUP_OK cobrindo zero ambientes" || ok "nao imprimiu BACKUP_OK"
grep -q "RESULTADO: BACKUP_SEM_AMBIENTE" "$TMP/6.log" && ok "declara BACKUP_SEM_AMBIENTE (nada foi copiado)" || ko "nao declarou BACKUP_SEM_AMBIENTE: $(tail -3 "$TMP/6.log")"
grep -q "TRE_PG_SERVICO global ignorado" "$TMP/6.log" && ok "avisa que o global unico foi ignorado" || ko "nao avisa sobre o global ignorado"
confere "nenhum artefato com o banco do dev rotulado de homolog/prod" "0" "$(conta_artefatos "$D6" homolog)"
confere "nenhum artefato com o banco do dev rotulado de prod"         "0" "$(conta_artefatos "$D6" prod)"

# ---------------------------------------------------------------------------------
echo
echo "== 7. VERIFICADOR: provisionado sem backup = FALHA (nao 'pulado') =="
D7="$TMP/d7"; mkdir -p "$D7"
rodar_verificador "$TMP/7.log" "pg-sales-dev pg-odoo-dev" "$TMP/env-dev" "$D7" "$VERIFICADOR" todos
COD7=$?
confere "exit code do verificador" "1" "$COD7"
grep -q "RESULTADO: VERIFICACAO_FALHOU" "$TMP/7.log" && ok "declara VERIFICACAO_FALHOU" || ko "nao falhou: $(tail -3 "$TMP/7.log")"
grep -q "nao esta produzindo artefato" "$TMP/7.log" && ok "aponta a rotina diaria como causa" || ko "nao aponta a causa: $(tail -5 "$TMP/7.log")"

echo
echo "== 8. VERIFICADOR: nada provisionado = PULADO honesto (exit 0) =="
D8="$TMP/d8"; mkdir -p "$D8"
rodar_verificador "$TMP/8.log" "" "$TMP/env-vazio" "$D8" "$VERIFICADOR" todos
COD8=$?
confere "exit code do verificador" "0" "$COD8"
grep -q "RESULTADO: VERIFICACAO_OK" "$TMP/8.log" && ok "declara VERIFICACAO_OK (nada a verificar)" || ko "nao aprovou: $(tail -3 "$TMP/8.log")"

# ---------------------------------------------------------------------------------
echo
echo "== 9. FRONTEIRA: container pela convencao pg-<amb>, sem arquivo de configuracao =="
D9F="$TMP/d9f"; mkdir -p "$D9F"
rodar_rotina "$TMP/9f.log" "pg-homolog" "$TMP/env-vazio" "$D9F" "$ROTINA" homolog
COD9F=$?
confere "exit code de homolog por convencao" "0" "$COD9F"
confere "artefatos de homolog" "1" "$(conta_artefatos "$D9F" homolog)"
ART9F="$(find "$D9F" -maxdepth 1 -type d -name 'tre_homolog_*' | head -1)"
confere "manifesto: servico" "pg-homolog" "$(campo_manifesto "$ART9F" servico)"
confere "manifesto: usuario (padrao da convencao)" "tre" "$(campo_manifesto "$ART9F" usuario)"

# ---------------------------------------------------------------------------------
echo
echo "== 9b. NEGATIVO: ambiente DECLARA Odoo e o container do Odoo NAO existe =="
D9B="$(mktemp -d "$TMP/d9b.XXXXXX")"
rodar_rotina "$TMP/9b.log" "pg-sales-dev" "$TMP/env-dev" "$D9B" "$ROTINA" dev
COD9B=$?
[ "$COD9B" -ne 0 ] && ok "exit code != 0 ($COD9B)" || ko "saiu com exit 0 — Odoo declarado e ausente nao pode passar"
grep -q "RESULTADO: BACKUP_OK" "$TMP/9b.log" && ko "IMPRIMIU BACKUP_OK com Odoo declarado e container ausente" || ok "nao imprimiu BACKUP_OK"
grep -q "DECLARA Odoo" "$TMP/9b.log" && ok "aponta o Odoo declarado como causa" || ko "nao aponta a causa: $(grep -i odoo "$TMP/9b.log" | tail -2)"
confere "artefato do trio criado apesar da falha do Odoo" "1" "$(conta_artefatos "$D9B" dev)"
ART9B="$(find "$D9B" -maxdepth 1 -type d -name 'tre_dev_*' | head -1)"
grep -q '^odoo: ausente neste ambiente' "$ART9B/manifest.txt" && ok "manifesto declara a ausencia do Odoo (sem omissao silenciosa)" || ko "manifesto nao declara a ausencia do Odoo"
[ -s "$ART9B/odoo-manifest.txt" ] && ko "artefato diz ter Odoo sem ter copiado" || ok "artefato nao finge ter Odoo"

# ---------------------------------------------------------------------------------
echo
echo "== 9c. VERIFICADOR: ambiente com Odoo e artefato SEM o bloco do Odoo = FALHA =="
D9C="$(mktemp -d "$TMP/d9c.XXXXXX")"
cp -r "$D9B/tre_dev_"* "$D9C/" 2>/dev/null
rodar_verificador "$TMP/9c.log" "pg-sales-dev pg-odoo-dev" "$TMP/env-dev" "$D9C" "$VERIFICADOR" dev
COD9C=$?
[ "$COD9C" -ne 0 ] && ok "exit code != 0 ($COD9C)" || ko "aprovou artefato pela metade"
grep -q "backup do ambiente esta pela metade" "$TMP/9c.log" && ok "nomeia o backup pela metade" || ko "nao nomeia o motivo: $(tail -3 "$TMP/9c.log")"

# ---------------------------------------------------------------------------------
echo
echo "== 10. REGRESSAO (antes x depois), contra o script ANTERIOR =="
if [ -z "$ROTINA_ANTIGA" ] || [ ! -f "$ROTINA_ANTIGA" ]; then
  nota "TRE_F2_SCRIPT_ANTIGO nao informado: regressao do script anterior nao medida"
else
  D9="$TMP/d9"; mkdir -p "$D9"
  confere "o script antigo realmente nao e o atual" "diferentes" \
    "$([ "$(sha256sum <"$ROTINA_ANTIGA" | cut -d' ' -f1)" != "$(sha256sum <"$ROTINA" | cut -d' ' -f1)" ] && echo diferentes || echo iguais)"
  rodar_rotina "$TMP/9.log" "pg-sales-dev" "$TMP/env-dev" "$D9" "$ROTINA_ANTIGA" todos
  COD9=$?
  confere "ANTES: exit code do script antigo" "0" "$COD9"
  grep -q "RESULTADO: BACKUP_OK" "$TMP/9.log" && ok "ANTES: imprimia BACKUP_OK (o defeito)" || ko "ANTES: nao imprimia BACKUP_OK — cenario nao reproduz o defeito"
  confere "ANTES: artefatos criados" "0" "$(conta_artefatos "$D9" dev)"
  grep -q "PULADO ambiente dev" "$TMP/9.log" && ok "ANTES: dev era PULADO mesmo com container existindo" || ko "ANTES: nao pulou dev: $(tail -3 "$TMP/9.log")"
  # o mesmo cenario com o script atual
  D9B="$TMP/d9b"; mkdir -p "$D9B"
  rodar_rotina "$TMP/9b.log" "pg-sales-dev" "$TMP/env-dev" "$D9B" "$ROTINA" todos
  confere "DEPOIS: artefatos criados" "1" "$(conta_artefatos "$D9B" dev)"
fi

if [ -n "$VERIFICADOR_ANTIGO" ] && [ -f "$VERIFICADOR_ANTIGO" ]; then
  D10="$TMP/d10"; mkdir -p "$D10"
  rodar_verificador "$TMP/10.log" "pg-sales-dev" "$TMP/env-dev" "$D10" "$VERIFICADOR_ANTIGO" todos
  COD10=$?
  confere "ANTES: verificador aprovava sem backup nenhum" "0" "$COD10"
  grep -q "RESULTADO: VERIFICACAO_OK" "$TMP/10.log" && ok "ANTES: verificador dizia VERIFICACAO_OK com zero backup" || ko "ANTES: verificador nao aprovou o vazio (o cenario nao reproduz)"
else
  nota "TRE_F2_VERIFICADOR_ANTIGO nao informado: regressao do verificador anterior nao medida"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: TESTE_OK ($ITENS itens, 0 falhas)"
  exit 0
else
  echo "RESULTADO: TESTE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
