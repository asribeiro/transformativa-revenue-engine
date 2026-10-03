#!/usr/bin/env bash
# teste_desempenho_mensagens_aceite.sh — ACEITE E2E da analise de desempenho de mensagens
# (card TRE-W8-E04-T01, `desempenho-mensagens-v1`).
#
# Mede o componente contra PostgreSQL DE VERDADE, descartavel e em loopback (ADR-005): container
# `pg-desemp-acc` (postgres:16) + db/migrations/0001. Nenhuma credencial e nenhum ambiente real.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. a porta de banco responde e a migration aplica no PostgreSQL real;
#   2. as linhas de ENVIO entram na FORMA declarada pelo card irmao (canal/direcao/tipo/referencia lidos
#      de hermes/agents/outreach/politica-envio-v1.json — nao digitados aqui);
#   3. as linhas de RESPOSTA entram no vocabulario do card irmao (ingestao-respostas-v1.json);
#   4. a analise le e calcula: enviadas, respondidas, positivas, opt-out, descarte de auto-resposta,
#      taxa de interesse e melhor variante (amostra);
#   5. a analise e SOMENTE LEITURA: contagem das 12 tabelas identica antes/depois;
#   6. prod RECUSA (exit 4) e a saida e reproduzivel (duas rodadas iguais, sem carimbo);
#   7. a saida nao carrega organization_id/contact_id/approval_id (agregada).
#
# LIMITE DECLARADO: as linhas de envio/resposta sao semeadas por fixture SQL NA FORMA dos irmaos (a cadeia
# real SMTP/IMAP -> interactions ja foi medida pelo TRE-W6-E07-T01 e pelo aceite do W6-E05); o que este
# aceite mede de ponta a ponta e a ANALISE contra o PostgreSQL real. Os dentes (prova de que a suite
# REPROVA) vivem no autoteste do verificador offline.
# Veredito: ACEITE_DESEMPENHO_MENSAGENS_001_OK / _FALHOU. Exit 0 = OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
CONTAINER="${TRE_DESEMP_ACC_CONTAINER:-pg-desemp-acc}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
SENHA="desemp-aceite-descartavel"
USUARIO="sales_ai"; BANCO="sales_intelligence"
TRABALHO="${TRE_DESEMP_TRABALHO:-/tmp/desemp-aceite-trabalho}"
MANTER=0
[ "${1:-}" = "--manter" ] && MANTER=1
OK=0; FALHAS=0
item() { if [ "$2" = "$3" ]; then echo "OK     $1 ($3)"; OK=$((OK+1)); else echo "FALHOU $1 (esperado=$2 obtido=$3)"; FALHAS=$((FALHAS+1)); fi; }

command -v docker  >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
for f in "$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" "$RAIZ/hermes/analytics/desempenho_mensagens.py" \
         "$RAIZ/hermes/agents/outreach/politica-envio-v1.json" "$RAIZ/hermes/agentes/respostas/ingestao-respostas-v1.json"; do
  [ -f "$f" ] || { echo "FALHOU arquivo ausente: $f"; exit 2; }
done
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
rm -rf "$TRABALHO"; mkdir -p "$TRABALHO"
limpar() { local rc=$?; if [ "$MANTER" -eq 0 ]; then docker rm -f -v "$CONTAINER" >/dev/null 2>&1; else echo "== --manter: $CONTAINER de pe"; fi; exit $rc; }
trap limpar EXIT

docker run -d --name "$CONTAINER" -e POSTGRES_USER="$USUARIO" -e POSTGRES_PASSWORD="$SENHA" \
  -e POSTGRES_DB="$BANCO" "$IMAGEM" >/dev/null || { echo "FALHOU nao subiu o container"; exit 2; }
# pg_isready MENTE no start (servidor temporario): espera SELECT 1 responder duas vezes.
pronto=0
for _ in $(seq 1 60); do
  if docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -tAc "SELECT 1" >/dev/null 2>&1; then
    sleep 2
    if docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -tAc "SELECT 1" >/dev/null 2>&1; then pronto=1; break; fi
  fi
  sleep 1
done
item "A0 (pre-condicao) PostgreSQL descartavel responde SELECT 1 duas vezes" 1 "$pronto"
[ "$pronto" = 1 ] || exit 2
PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
"${PSQL[@]}" -q -f - < "$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" >/dev/null 2>"$TRABALHO/mig.err"
item "A1 migration 0001 no PostgreSQL real (schema exists)" "sales_intelligence" \
  "$("${PSQL[@]}" -c "SELECT schema_name FROM information_schema.schemata WHERE schema_name='sales_intelligence';" </dev/null | tr -d ' ')"

# ---- fixture na FORMA declarada pelos irmaos (valores lidos do repositorio, nunca digitados aqui)
python3 - "$RAIZ" "$TRABALHO/fixture.sql" <<'PY'
import json, sys
from pathlib import Path
raiz, saida = Path(sys.argv[1]), Path(sys.argv[2])
envio = json.loads((raiz / "hermes/agents/outreach/politica-envio-v1.json").read_text(encoding="utf-8"))["interacoes"]
irmao = json.loads((raiz / "hermes/agentes/respostas/ingestao-respostas-v1.json").read_text(encoding="utf-8"))["vocabulario"]["response_category"]
canal_envio, direcao, tipo = envio["channel"], envio["direction"], envio["interaction_type"]
assert canal_envio == "EMAIL" and direcao == "OUTBOUND", envio
for categoria in ("INTERESSE", "AUTO_RESPOSTA", "OPT_OUT"):
    assert categoria in irmao, categoria
O1, O2, O3 = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
              "33333333-3333-4333-8333-333333333333")
K1 = "aaaaaaaa-0000-4000-8000-000000000001"
L = ["SET client_min_messages TO WARNING;"]
for i, org in enumerate((O1, O2, O3), start=1):
    L.append("INSERT INTO sales_intelligence.organizations (id, trade_name, status, source) VALUES "
             f"('{org}', 'Org {i}', 'DISCOVERED', 'ACEITE_DESEMPENHO');")
L.append("INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, source) VALUES "
         f"('{K1}', '{O1}', 'Contato A', 'a@dev.local', 'ACEITE_DESEMPENHO');")

def envios(org, variante, inicio, contato=""):
    linhas = []
    for n, hora in enumerate(("10:00", "10:30", "11:00", "11:30", "12:00"), start=1):
        cid = f"'{contato}'" if (contato and n == 1) else "NULL"
        ref = f"envio:ped-{variante}-{n}:{variante}"
        linhas.append("INSERT INTO sales_intelligence.interactions "
                      "(id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, "
                      "subject, content_summary, content_reference) VALUES "
                      f"(gen_random_uuid(), '{org}', {cid}, '{canal_envio}', '{direcao}', '{tipo}', "
                      f"TIMESTAMPTZ '{inicio} {hora}:00+00', 'assunto {variante}', 'resumo', '{ref}');")
    return linhas

def resposta(org, contato, hora, categoria, tipo_resp="EMAIL_RESPOSTA"):
    cid = f"'{contato}'" if contato else "NULL"
    return ("INSERT INTO sales_intelligence.interactions "
            "(id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, "
            "content_reference, response_category) VALUES "
            f"(gen_random_uuid(), '{org}', {cid}, 'email', 'INBOUND', '{tipo_resp}', "
            f"TIMESTAMPTZ '{hora}+00', 'UIDVALIDITY:1', '{categoria}');")

L += envios(O1, "H1", "2026-09-01", contato=K1)
L.append(resposta(O1, K1, "2026-09-01 13:00:00", "INTERESSE"))
L.append(resposta(O1, "", "2026-09-01 13:30:00", "INTERESSE"))
L += envios(O2, "H2", "2026-09-02")
L.append(resposta(O2, "", "2026-09-02 13:00:00", "INTERESSE"))
L.append(resposta(O2, "", "2026-09-02 14:00:00", "AUTO_RESPOSTA"))
L += envios(O3, "H8", "2026-09-03")
L.append(resposta(O3, "", "2026-09-03 15:00:00", "OPT_OUT"))
L.append("INSERT INTO sales_intelligence.interactions "
         "(id, organization_id, channel, direction, interaction_type, occurred_at, content_summary) VALUES "
         f"(gen_random_uuid(), '{O1}', '{canal_envio}', '{direcao}', '{tipo}', "
         "TIMESTAMPTZ '2026-09-01 09:00:00+00', 'sem referencia de envio');")
saida.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"fixture ok: canal={canal_envio} direcao={direcao} tipo={tipo}")
PY
"${PSQL[@]}" -q -f - < "$TRABALHO/fixture.sql" >/dev/null || { echo "FALHOU nao semeou a fixture"; exit 2; }
item "A2 fixture: 15 envios + 5 respostas gravados" "15|5" \
  "$("${PSQL[@]}" -c "SELECT (SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference LIKE 'envio:%')||'|'||(SELECT count(*) FROM sales_intelligence.interactions WHERE direction='INBOUND');" </dev/null | tr -d ' ')"

# ---- contagem das 12 tabelas ANTES (prova de somente-leitura)
contagens() {
  "${PSQL[@]}" -c "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence')||'|'||(SELECT count(*) FROM sales_intelligence.interactions)||'|'||(SELECT count(*) FROM sales_intelligence.organizations)||'|'||(SELECT count(*) FROM sales_intelligence.contacts);" </dev/null | tr -d ' '
}
ANTES="$(contagens)"

PORTa="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
REL="$TRABALHO/relatorio.json"
python3 "$RAIZ/hermes/analytics/desempenho_mensagens.py" --ambiente dev --prefixo "$PORTa" \
  --desde 2026-09-01T00:00:00Z --ate 2026-09-10T00:00:00Z --janela-dias 14 --limite-amostra 5 \
  --json "$REL" >/dev/null 2>"$TRABALHO/erro.txt"
item "B1 a analise roda contra o PostgreSQL real (exit 0)" 0 "$?"
python3 - "$REL" <<'PY' > "$TRABALHO/itens.txt"
import json, sys
r = json.loads(open(sys.argv[1], encoding="utf-8").read())
v = {x["texto_hash"]: x for x in r["por_variante"]}
def linha(nome, esperado, obtido): print(f"{nome}\t{esperado}\t{obtido}")
linha("B2 veredito", "ANALISADO", r["veredito"])
linha("B3 total de envios lidos", 15, r["totais"]["enviadas"])
linha("B4 o filtro do recorte de envio e do SQL: a linha sem referencia fica FORA da leitura "
      "(a defesa do componente para porta que ignora o WHERE e medida na unidade, no verificador)",
      0, r["excluidas_do_recorte"]["outbound_sem_referencia_de_envio"])
linha("B5 total respondidas (5 respostas, 1 auto-resposta descartada)", 4, r["totais"]["respondidas"])
linha("B6 total positivas (interesse)", 3, r["totais"]["positivas"])
linha("B7 total opt-out", 1, r["totais"]["opt_outs"])
linha("B8 auto-resposta medida como descartada", 1, r["totais"]["respostas_descartadas"])
linha("B9 taxa de interesse da variante H1", 0.4, v["H1"]["taxa_de_interesse"])
linha("B10 tempo medio da H1 em horas (3h e 1.5h)", 2.25, v["H1"]["tempo_medio_de_resposta_horas"])
linha("B11 taxa de opt-out da variante H8", 0.2, v["H8"]["taxa_de_opt_out"])
linha("B12 melhor variante (maior taxa de interesse com amostra)", "H1", (r["melhor_variante"] or {}).get("texto_hash"))
texto = json.dumps(r, ensure_ascii=False)
linha("B13 saida agregada (sem organization_id/contact_id/approval_id)",
      None, next((t for t in ("11111111-1111-4111-8111", "aaaaaaaa-0000", "ped-H1-1") if t in texto), None))
PY
while IFS=$'\t' read -r nome esperado obtido; do
  [ -z "$nome" ] && continue
  if [ "$esperado" = "None" ] && [ -z "$obtido" ]; then obtido=""; fi
  item "$nome" "$esperado" "$obtido"
done < "$TRABALHO/itens.txt"

DEPOIS="$(contagens)"
item "C1 somente leitura: contagem das tabelas identica antes/depois" "$ANTES" "$DEPOIS"

python3 "$RAIZ/hermes/analytics/desempenho_mensagens.py" --ambiente dev --prefixo "$PORTa" \
  --desde 2026-09-01T00:00:00Z --ate 2026-09-10T00:00:00Z --janela-dias 14 --limite-amostra 5 \
  > "$TRABALHO/rel2.json" 2>/dev/null
item "C2 saida reproduzivel (duas rodadas iguais, sem carimbo)" "IGUAL" \
  "$(python3 - "$REL" "$TRABALHO/rel2.json" <<'PY'
import json, sys
a = json.loads(open(sys.argv[1], encoding="utf-8").read()); b = json.loads(open(sys.argv[2], encoding="utf-8").read())
print("IGUAL" if a == b else "DIFERENTE")
PY
)"

python3 "$RAIZ/hermes/analytics/desempenho_mensagens.py" --ambiente prod --prefixo "$PORTa" >/dev/null 2>&1
item "C3 prod RECUSADO (ADR-005)" 4 "$?"

echo
if [ "$FALHAS" -eq 0 ]; then echo "ACEITE_DESEMPENHO_MENSAGENS_001_OK ($OK itens, 0 falhas)"; exit 0; fi
echo "ACEITE_DESEMPENHO_MENSAGENS_001_FALHOU ($OK OK, $FALHAS falhas)"; exit 1
