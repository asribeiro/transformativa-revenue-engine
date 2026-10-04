#!/usr/bin/env bash
# =============================================================================================
# ACEITE E2E DA ATRIBUICAO DE LEAD DO GOOGLE — card TRE-W7-E03-T01
#
# Mede o caminho inteiro do card num PostgreSQL DESCARTAVEL (`pg-google-acc`) + um STUB LOCAL do
# resolvedor de `gclid` da Ads API em 127.0.0.1 (porta 8899):
#   1. formulario de Lead Ads do Google Ads  -> ATRIBUIDO pela evidencia forte (campanha no payload)
#   2. formulario do site com gclid          -> ATRIBUIDO pela resolucao da porta (campanha da Ads API)
#   3. gclid que a porta nao conhece         -> canal google, campanha NAO (fail-closed)
#   4. lead sem evidencia nomeada            -> NAO_ATRIBUIDO/SEM_IDENTIFICADOR, zero interactions
#   5. formulario de Lead Ads sem campanha   -> NAO_ATRIBUIDO/FORMULARIO_SEM_CAMPANHA
#   6. lead sem e-mail e sem telefone        -> DADOS_INSUFICIENTES
#   7. contato desconhecido                  -> SEM_VINCULO (nao se inventa organizacao)
#   8. replay do mesmo lead                  -> JA_INGERIDO, sem linha nova
#   9. porta do gclid fora do ar             -> a resolucao NAO e' usada (cai na evidencia fraca)
#  10. trilha com a evidencia/confianca e com os campos desconhecidos
#  11. escrita restrita: so interactions + sync_events mudam (snapshot das 12 tabelas)
#  12. resumo/subject sem PII em claro; segredo (token) nunca na saida
#  13. guardas: prod recusa (exit 4), banco nao-local recusado (exit 3), --desfazer grava DESFEITO
#
# ADR-005: nada nasce em producao e nenhuma credencial real entra. O container do aceite e' proprio
# (`pg-google-acc`); se ele ja' existir, o aceite ABORTA em vez de mexer no que nao e' dele. Os
# containers do ambiente (pg-sales-dev, pg-odoo-dev, odoo-dev, proxy-dev) NAO sao tocados.
#
# Veredito: ACEITE_GOOGLE_LEADS_001_OK / ACEITE_GOOGLE_LEADS_001_FALHOU.
# --prova-de-dente: muta uma COPIA do componente/contrato e exige que o item esperado REPROVE.
# Exit: 0 = OK · 1 = FALHOU · 2 = uso/guarda · 3 = nao testavel · 4 = recusa de ambiente.
# =============================================================================================
set -uo pipefail

RAIZ="${TRE_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
CONTAINER="${TRE_GOOGLE_CONTAINER:-pg-google-acc}"
TRABALHO="${TRE_GOOGLE_TRABALHO:-/tmp/aceite-google-leads}"
USUARIO=sales_ai
BANCO=sales_intelligence
SENHA=dev
PORTA_STUB=8899
COMPONENTE="${TRE_GOOGLE_COMPONENTE:-$RAIZ/hermes/inbound/google/atribuicao_google.py}"
CONTRATO="$RAIZ/hermes/inbound/google/atribuicao-google-v1.json"
EXEMPLOS="$RAIZ/hermes/inbound/google/exemplos"
MIGRATION="$RAIZ/db/migrations/0001_sales_intelligence_v1.sql"
STUB="$RAIZ/scripts/inbound/stub-google-ads-dev.py"
PORTA_BANCO="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
ORG_A="aaaa1111-1111-4111-8111-aaaaaaaaaaaa"
CT_A="bbbb1111-1111-4111-8111-bbbbbbbbbbbb"
CT_B="cccc1111-1111-4111-8111-cccccccccccc"
CT_C="dddd1111-1111-4111-8111-dddddddddddd"
EMAIL_A="ana.souza@cliente-demo.test"
EMAIL_C="carlos.lima@cliente-demo.test"
FONE_B="+5511988887777"
# GCLID_BOM tem de ser o MESMO do exemplo `lead-site-gclid.json`: exemplo e aceite que discordassem
# mediriam outro caso (defeito real da rodada 1 deste aceite).
GCLID_BOM="Cj0KCQjw2cWgBhCYARIsAKZkXfS0"
GCLID_RUIM="Cj0KCQjw2cWgBhCYARIsAKZkXfZZ"
DENTE=0
MANTER=0
DENTE_ROTULO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --manter) MANTER=1 ;;
    --prova-de-dente) DENTE=1 ;;
    --dente) shift; DENTE=1; DENTE_ROTULO="${1:?--dente exige rotulo}" ;;
    --dente=*) DENTE=1; DENTE_ROTULO="${1#--dente=}" ;;
    --componente) shift; COMPONENTE="${1:?--componente exige caminho}" ;;
    *) echo "uso: $0 [--manter] [--prova-de-dente] [--dente <rotulo>] [--componente <py>]"; exit 2 ;;
  esac
  shift
done

ITENS_OK=0
ITENS_FALHOU=0
FALHAS=""
item() { # <nome> <esperado> <obtido>
  if [ "$2" = "$3" ]; then
    echo "OK     $1 ($3)"
    ITENS_OK=$((ITENS_OK + 1))
  else
    echo "FALHOU $1 (esperado='$2' obtido='$3')"
    ITENS_FALHOU=$((ITENS_FALHOU + 1))
    FALHAS="$FALHAS|$1"
  fi
}

limpar() {
  if [ "$MANTER" = "1" ]; then
    echo "# --manter: trio de pe para investigacao"
    return 0
  fi
  if [ -f "$TRABALHO/stub.pid" ]; then kill "$(cat "$TRABALHO/stub.pid")" 2>/dev/null; fi
  docker rm -f "$CONTAINER" >/dev/null 2>&1
  return 0
}
trap limpar EXIT

comando_pg() { docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -q -t -A "$@"; }
consulta() { printf 'SELECT coalesce(json_agg(t), %s)::text FROM (%s) t;\n' "'[]'::json" "$1" | comando_pg; }
contagem() { consulta "$1" | tr -d '[]{}"a-zA-Z: '; }
sonda_stub() {
  python3 - "$1" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as r:
        print(r.read().decode())
except Exception as e:  # noqa: BLE001
    print("ERRO", e)
PY
}

rodar_comp() { # <json_path> [args extras...]
  local entrada="$1"; shift
  TRE_AMBIENTE=dev python3 "$COMPONENTE" --ingerir --confirmo --porta-banco "$PORTA_BANCO" \
      --porta-ads "http://127.0.0.1:$PORTA_STUB" --entrada "$entrada" "$@"
}

# ---------------------------------------------------------------------------------------------
# 0. Guardas de ambiente e preparacao
# ---------------------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then echo "# nao testavel: docker ausente"; exit 3; fi
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "# recusa de ambiente: container $CONTAINER ja' existe (o aceite nao mexe no que nao e' dele)"; exit 4
fi
if (exec 3<>/dev/tcp/127.0.0.1/$PORTA_STUB) 2>/dev/null; then
  echo "# recusa de ambiente: porta $PORTA_STUB ocupada (sobra de rodada anterior)"; exit 4
fi
for arquivo in "$COMPONENTE" "$CONTRATO" "$MIGRATION" "$STUB"; do
  [ -f "$arquivo" ] || { echo "# nao testavel: ausente $arquivo"; exit 3; }
done
mkdir -p "$TRABALHO"
rm -f "$TRABALHO"/*.json "$TRABALHO"/*.out 2>/dev/null

echo "# subida do PostgreSQL descartavel ($CONTAINER)"
docker run -d --name "$CONTAINER" -e POSTGRES_USER=$USUARIO -e POSTGRES_PASSWORD=$SENHA \
  -e POSTGRES_DB=$BANCO "$IMAGEM" >/dev/null || { echo "# nao testavel: docker run falhou"; exit 3; }

# `pg_isready` mente no inicio (servidor temporario da imagem): a espera e' `SELECT 1` DUAS vezes.
prontas=0
for _ in $(seq 1 60); do
  if comando_pg -c 'SELECT 1' >/dev/null 2>&1; then prontas=$((prontas + 1)); else prontas=0; fi
  [ "$prontas" -ge 2 ] && break
  sleep 1
done
if [ "$prontas" -lt 2 ]; then echo "# nao testavel: banco nao ficou pronto"; exit 3; fi

docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -q < "$MIGRATION" >/dev/null \
  || { echo "# nao testavel: migration recusada"; exit 3; }

printf "INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain) VALUES ('%s', 'Cliente Demo LTDA', 'Cliente Demo', 'cliente-demo.test');
INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, phone, source) VALUES
  ('%s', '%s', 'Ana Souza', '%s', NULL, 'google-lead-aceite'),
  ('%s', '%s', 'Bruno Costa', NULL, '%s', 'google-lead-aceite'),
  ('%s', '%s', 'Carlos Lima', '%s', NULL, 'google-lead-aceite');\n" \
  "$ORG_A" "$CT_A" "$ORG_A" "$EMAIL_A" "$CT_B" "$ORG_A" "$FONE_B" "$CT_C" "$ORG_A" "$EMAIL_C" | comando_pg >/dev/null \
  || { echo "# nao testavel: massa inicial recusada"; exit 3; }

python3 "$STUB" --porta "$PORTA_STUB" --gclid-bom "$GCLID_BOM" --dir "$TRABALHO" >"$TRABALHO/stub.log" 2>&1 &
sleep 1
sonda=$(sonda_stub "http://127.0.0.1:$PORTA_STUB/gclid/$GCLID_BOM")
case "$sonda" in
  *RESOLVIDO*) : ;;
  *) echo "# nao testavel: stub do gclid nao respondeu ($sonda)"; exit 3 ;;
esac

snapshot() {
  consulta "SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text::int AS linhas FROM information_schema.tables WHERE table_schema='sales_intelligence' ORDER BY table_name" | tr -d ' \n'
}
ANTES="$(snapshot)"

# ---------------------------------------------------------------------------------------------
# Cenario 1 — formulario de Lead Ads do Google Ads (evidencia forte)
# ---------------------------------------------------------------------------------------------
saida=$(rodar_comp "$EXEMPLOS/lead-google-ads.json" 2>&1); rc=$?
echo "$saida" > "$TRABALHO/c1.out"
item "1.1 formulario de Lead Ads e' ATRIBUIDO" "0" "$rc"
item "1.2 evidencia forte registrada na trilha" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:google_ads_lead_form:gads-88231' AND status='ATRIBUIDO' AND (request_payload->>'evidencia')='FORMULARIO_GOOGLE_ADS'")"
item "1.3 campanha do payload e confianca 0.95 no envelope de atribuicao" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE (request_payload->>'campanha_id')='cmp-7781' AND (request_payload->>'confianca')::numeric = 0.95")"
item "1.4 interaction no canal google, inbound, tipo LEAD_FORM" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.interactions WHERE channel='google' AND direction='INBOUND' AND interaction_type='LEAD_FORM' AND organization_id='$ORG_A'")"
item "1.5 interaction ligada ao contato certo, com a chave de atribuicao" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.interactions WHERE contact_id='$CT_A' AND content_reference='google:google-lead:google_ads_lead_form:gads-88231'")"
item "1.6 resumo e assunto gravados NAO expoem e-mail/telefone em claro" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.interactions WHERE content_summary LIKE '%ana.souza%' OR content_summary LIKE '%5511999990%' OR subject LIKE '%ana.souza%'")"
item "1.7 ai_confidence nunca preenchida (decisao por regra nao e' classificacao de IA)" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.interactions WHERE ai_confidence IS NOT NULL")"

# ---------------------------------------------------------------------------------------------
# Cenario 2 — site com gclid resolvido pela porta
# ---------------------------------------------------------------------------------------------
saida=$(rodar_comp "$EXEMPLOS/lead-site-gclid.json" 2>&1); rc=$?
echo "$saida" > "$TRABALHO/c2.out"
item "2.1 gclid resolvido pela Ads API e' ATRIBUIDO" "0" "$rc"
item "2.2 evidencia GCLID_RESOLVIDO com a campanha da porta" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_gclid:site-4410' AND (request_payload->>'evidencia')='GCLID_RESOLVIDO' AND (request_payload->>'campanha_id')='cmp-900'")"
item "2.3 interaction de site gravada como LEAD_WEB e ligada ao contato do lead" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference='google:google-lead:site_gclid:site-4410' AND interaction_type='LEAD_WEB' AND contact_id='$CT_C'")"

# ---------------------------------------------------------------------------------------------
# Cenario 3 — gclid que a porta nao conhece + campo fora do mapa
# ---------------------------------------------------------------------------------------------
printf '{"lead_id":"site-5500","fonte":"site_gclid","contato_email":"%s","gclid":"%s","utm_source":"google","form_id":"form-77","pagina":"https://transformativa.com.br/diagnostico"}\n' "$EMAIL_A" "$GCLID_RUIM" > "$TRABALHO/lead-gclid-ruim.json"
saida=$(rodar_comp "$TRABALHO/lead-gclid-ruim.json" 2>&1); rc=$?
echo "$saida" > "$TRABALHO/c3.out"
item "3.1 gclid nao resolvido ainda e' ATRIBUIDO ao canal" "0" "$rc"
item "3.2 campanha NAO foi inventada" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_gclid:site-5500' AND (request_payload->>'evidencia')='GCLID_NAO_RESOLVIDO' AND (request_payload->>'campanha_id') IS NULL")"

# ---------------------------------------------------------------------------------------------
# Cenario 4 — lead sem evidencia nomeada: nao se atribui canal por conveniencia
# ---------------------------------------------------------------------------------------------
antes_sem_evid=$(contagem "SELECT count(*) AS n FROM sales_intelligence.interactions")
rodar_comp "$EXEMPLOS/lead-sem-evidencia.json" > "$TRABALHO/c4.out" 2>&1
item "4.1 lead sem evidencia e' registrado como NAO_ATRIBUIDO/SEM_IDENTIFICADOR" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_utm:site-4411' AND status='NAO_ATRIBUIDO' AND error_message='SEM_IDENTIFICADOR'")"
item "4.2 nenhuma interaction inventada para o lead sem evidencia" "$antes_sem_evid" "$(contagem "SELECT count(*) AS n FROM sales_intelligence.interactions")"

# ---------------------------------------------------------------------------------------------
# Cenario 5 — formulario de Lead Ads sem campanha (incoerencia declarada)
# ---------------------------------------------------------------------------------------------
python3 - "$EXEMPLOS/lead-google-ads.json" "$TRABALHO/lead-sem-campanha.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
d["lead_id"] = "gads-99001"
d.pop("campanha_id")
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"))
PY
rodar_comp "$TRABALHO/lead-sem-campanha.json" > "$TRABALHO/c5.out" 2>&1
item "5.1 formulario sem campanha: NAO_ATRIBUIDO/FORMULARIO_SEM_CAMPANHA" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:google_ads_lead_form:gads-99001' AND status='NAO_ATRIBUIDO' AND error_message='FORMULARIO_SEM_CAMPANHA'")"

# ---------------------------------------------------------------------------------------------
# Cenario 6/7 — sem dado de contato e contato desconhecido
# ---------------------------------------------------------------------------------------------
printf '{"lead_id":"gads-99002","fonte":"google_ads_lead_form","campanha_id":"cmp-7781","contato_nome":"Sem Contato"}\n' > "$TRABALHO/lead-sem-contato.json"
rodar_comp "$TRABALHO/lead-sem-contato.json" > "$TRABALHO/c6.out" 2>&1; rc=$?
item "6.1 lead sem e-mail e sem telefone e' RECUSADO (exit 3)" "3" "$rc"
item "6.2 lead recusado nao deixou linha em interactions" "0" "$(contagem "SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference LIKE '%gads-99002%'")"
printf '{"lead_id":"gads-99003","fonte":"google_ads_lead_form","campanha_id":"cmp-7781","contato_email":"ninguem@cliente-demo.test"}\n' > "$TRABALHO/lead-desconhecido.json"
antes_desc=$(contagem "SELECT count(*) AS n FROM sales_intelligence.interactions")
rodar_comp "$TRABALHO/lead-desconhecido.json" > "$TRABALHO/c7.out" 2>&1
item "7.1 contato desconhecido: SEM_VINCULO na trilha, sem entity_id" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:google_ads_lead_form:gads-99003' AND status='SEM_VINCULO' AND entity_id IS NULL")"
item "7.2 nenhuma organizacao/contato inventado e nenhuma interaction" "$antes_desc" "$(contagem "SELECT count(*) AS n FROM sales_intelligence.interactions")"

# ---------------------------------------------------------------------------------------------
# Cenario 8 — replay e cenario 9 — porta do gclid fora do ar
# ---------------------------------------------------------------------------------------------
rodar_comp "$EXEMPLOS/lead-google-ads.json" > "$TRABALHO/c8.out" 2>&1; rc=$?
item "8.1 replay do mesmo lead devolve JA_INGERIDO" "1" "$(grep -c '"evento": "JA_INGERIDO"' "$TRABALHO/c8.out" | tr -d ' ')"
item "8.2 replay nao duplicou a interaction" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference='google:google-lead:google_ads_lead_form:gads-88231'")"
kill "$(cat "$TRABALHO/stub.pid")" 2>/dev/null; sleep 1
printf '{"lead_id":"site-6600","fonte":"site_gclid","contato_email":"%s","gclid":"%s","utm_source":"google"}\n' "$EMAIL_A" "$GCLID_BOM" > "$TRABALHO/lead-porta-fora.json"
rodar_comp "$TRABALHO/lead-porta-fora.json" > "$TRABALHO/c9.out" 2>&1; rc=$?
item "9.1 porta fora do ar: a resolucao NAO e' usada (cai na evidencia fraca)" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_gclid:site-6600' AND (request_payload->>'evidencia')='UTM_SOURCE_GOOGLE'")"
item "9.2 a rodada com a porta fora do ar nao quebrou" "0" "$rc"
python3 "$STUB" --porta "$PORTA_STUB" --gclid-bom "$GCLID_BOM" --dir "$TRABALHO" >"$TRABALHO/stub.log" 2>&1 &
sleep 1

# ---------------------------------------------------------------------------------------------
# Cenario 10/11/12 — trilha, limite de escrita e segredo
# ---------------------------------------------------------------------------------------------
item "10.1 campo fora do mapa vai para a trilha (campos_desconhecidos), nao para coluna" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_gclid:site-5500' AND (request_payload->>'campos_desconhecidos') LIKE '%form_id%'")"
item "10.2 gclid e identidade de origem ficam na trilha para auditoria" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_gclid:site-4410' AND (request_payload->>'gclid')='$GCLID_BOM'")"
DEPOIS="$(snapshot)"
item "11.1 so' interactions e sync_events mudaram (snapshot das 12 tabelas)" "sim" \
  "$(python3 - "$ANTES" "$DEPOIS" <<'PY'
import json, sys
# `/snapshot` devolve a lista de {table_name, linhas} das 12 tabelas do canonico — a comparacao e'
# por tabela, nao por lista (o formato da rodada 1 foi lido errado e o item ficou cego).
antes = {r["table_name"]: int(r["linhas"]) for r in json.loads(sys.argv[1])}
depois = {r["table_name"]: int(r["linhas"]) for r in json.loads(sys.argv[2])}
mudaram = sorted(k for k in depois if depois[k] != antes.get(k))
print("sim" if mudaram and set(mudaram) <= {"interactions", "sync_events"} else "nao:" + ",".join(mudaram))
PY
)"
item "11.2 organizations e contacts intactos" "$(python3 - "$ANTES" <<'PY'
import json, sys
d = {r["table_name"]: int(r["linhas"]) for r in json.loads(sys.argv[1])}
print(d["organizations"], d["contacts"])
PY
)" \
  "$(python3 - "$DEPOIS" <<'PY'
import json, sys
d = {r["table_name"]: int(r["linhas"]) for r in json.loads(sys.argv[1])}
print(d["organizations"], d["contacts"])
PY
)"
TOKEN_FAKE="tok-fake-do-aceite-9f2b"
saida=$(TRE_GOOGLE_ADS_TOKEN="$TOKEN_FAKE" python3 "$COMPONENTE" --atribuir --payload \
  "{\"lead_id\":\"gads-99004\",\"fonte\":\"google_ads_lead_form\",\"campanha_id\":\"$TOKEN_FAKE\",\"contato_email\":\"$EMAIL_A\"}" 2>&1); rc=$?
echo "$saida" > "$TRABALHO/c12.out"
item "12.1 segredo na saida e' recusado (exit 5)" "5" "$rc"
item "12.2 o valor do segredo nao aparece na saida" "0" "$(grep -c "$TOKEN_FAKE" "$TRABALHO/c12.out" | tr -d ' ')"

# ---------------------------------------------------------------------------------------------
# Cenario 13 — guardas de CLI e desfazer
# ---------------------------------------------------------------------------------------------
saida=$(TRE_AMBIENTE=prod python3 "$COMPONENTE" --planejar 2>&1); rc=$?
item "13.1 prod RECUSA por desenho (exit 4)" "4" "$rc"
saida=$(TRE_AMBIENTE=dev python3 "$COMPONENTE" --ingerir --confirmo --porta-banco "ssh host psql -U x" \
  --entrada "$EXEMPLOS/lead-google-ads.json" 2>&1); rc=$?
item "13.2 banco nao-local e' recusado (exit 3)" "3" "$rc"
saida=$(TRE_AMBIENTE=homolog python3 "$COMPONENTE" --planejar 2>&1); rc=$?
item "13.3 homolog sem aprovacao registrada e' recusado (exit 3)" "3" "$rc"
saida=$(TRE_AMBIENTE=dev python3 "$COMPONENTE" --desfazer \
  "google-lead:site_utm:site-4411" --confirmo --porta-banco "$PORTA_BANCO" 2>&1); rc=$?
echo "$saida" > "$TRABALHO/c13.out"
item "13.4 --desfazer grava trilha DESFEITO" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_utm:site-4411#desfeito' AND status='DESFEITO'")"
item "13.5 a trilha original continua no banco (auditoria nao se apaga)" "1" "$(contagem "SELECT count(*) FROM sales_intelligence.sync_events WHERE idempotency_key='google-lead:site_utm:site-4411' AND status='NAO_ATRIBUIDO'")"
item "13.6 nenhuma credencial real de Google Ads no ambiente do aceite" "0" "$(env | grep -cE '^(GOOGLE_ADS_DEVELOPER_TOKEN|GOOGLE_ADS_REFRESH_TOKEN)=' | tr -d ' ')"

# ---------------------------------------------------------------------------------------------
# Prova de dente: muta copia e exige que o item esperado REPROVE
# ---------------------------------------------------------------------------------------------
if [ "$DENTE" = "1" ]; then
  echo "# prova de dente (mutando COPIA do componente/contrato)"
  DENTES_OK=0
  DENTES_FALHOU=0
  dente() { # <rotulo> <arquivo: componente|contrato> <de> <para> <item esperado> <verificacao>
    local rotulo="$1" qual="$2" de="$3" para="$4" esperado="$5" verificacao="$6"
    local dir="$TRABALHO/dente-$rotulo"
    mkdir -p "$dir"
    cp "$CONTRATO" "$dir/atribuicao-google-v1.json"
    if ! python3 - "$COMPONENTE" "$CONTRATO" "$dir" "$qual" "$de" "$para" <<'PY'
import shutil, sys
componente, contrato, destino, qual, de, para = sys.argv[1:7]
origem = componente if qual == "componente" else contrato
alvo = destino + "/atribuicao_google.py" if qual == "componente" else destino + "/atribuicao-google-v1.json"
texto = open(origem, encoding="utf-8").read()
novo = texto.replace(de, para)
if novo == texto:
    sys.exit(3)
open(alvo, "w", encoding="utf-8").write(novo)
if qual == "contrato":
    shutil.copy(componente, destino + "/atribuicao_google.py")
PY
    then
      echo "FALHOU dente '$rotulo': ancora '$de' ausente"
      DENTES_FALHOU=$((DENTES_FALHOU + 1)); ITENS_FALHOU=$((ITENS_FALHOU + 1)); FALHAS="$FALHAS|dente:$rotulo"
      return
    fi
    local veredito
    veredito=$($verificacao "$dir/atribuicao_google.py")
    if [ "$veredito" = "reprovou" ]; then
      echo "OK     dente '$rotulo': o item esperado REPROVOU ($esperado)"
      DENTES_OK=$((DENTES_OK + 1)); ITENS_OK=$((ITENS_OK + 1))
    else
      echo "FALHOU dente '$rotulo': o item esperado NAO reprovou ($esperado)"
      DENTES_FALHOU=$((DENTES_FALHOU + 1)); ITENS_FALHOU=$((ITENS_FALHOU + 1)); FALHAS="$FALHAS|dente:$rotulo"
    fi
  }
  verificar_confianca() { # confianca forte degradada no contrato -> item 1.3 nao vale mais
    local py="$1" dir
    dir="$(dirname "$py")"
    local saida
    saida=$(TRE_AMBIENTE=dev python3 "$py" --atribuir --entrada "$EXEMPLOS/lead-google-ads.json" 2>&1)
    if echo "$saida" | grep -q '"confianca": 0.45'; then echo reprovou; else echo segurou; fi
  }
  verificar_prod() { # guarda de producao desligada -> item 13.1 nao vale mais
    local py="$1"
    TRE_AMBIENTE=prod python3 "$py" --planejar >/dev/null 2>&1
    if [ "$?" = "4" ]; then echo segurou; else echo reprovou; fi
  }
  verificar_limite() { # INSERT fora do limite -> a auditoria da fonte reprova (item 11.1 protegido)
    local py="$1"
    local saida
    saida=$(TRE_AMBIENTE=dev python3 "$py" --conferir 2>&1)
    if echo "$saida" | grep -q 'ESCRITA_NO_CODIGO'; then echo reprovou; else echo segurou; fi
  }
  dente "confianca-forte" contrato '"confianca": 0.95' '"confianca": 0.45' "1.3 confianca 0.95" verificar_confianca
  dente "prod-liberado" componente 'return [("PRODUCAO_RECUSADA"' 'return [("NUNCA_RECUSA"' "13.1 prod exit 4" verificar_prod
  dente "escrita-fora-do-limite" componente 'INSERT INTO {TABELA_SYNC}' 'INSERT INTO sales_intelligence.organizations' "11.1 limite de escrita" verificar_limite
  echo "# dentes: $DENTES_OK ok / $DENTES_FALHOU falhou"
fi

# ---------------------------------------------------------------------------------------------
# Veredito
# ---------------------------------------------------------------------------------------------
if [ "$ITENS_FALHOU" = "0" ]; then
  echo "ACEITE_GOOGLE_LEADS_001_OK"
  echo "# veredito: $ITENS_OK itens OK / 0 FALHOU"
  exit 0
fi
echo "ACEITE_GOOGLE_LEADS_001_FALHOU"
echo "# veredito: $ITENS_OK itens OK / $ITENS_FALHOU FALHOU"
echo "# falhas:$FALHAS"
exit 1
