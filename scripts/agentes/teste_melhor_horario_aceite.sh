#!/usr/bin/env bash
# teste_melhor_horario_aceite.sh — ACEITE E2E do melhor horario de contato (card TRE-W9-E04-T01,
# `melhor-horario-v1`).
#
# Mede o componente contra PostgreSQL DE VERDADE, descartavel e em loopback (ADR-005): container
# `pg-timing-acc` (postgres:16) + db/migrations/0001. Nenhuma credencial e nenhum ambiente real.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. a porta de banco responde e a migration aplica no PostgreSQL real;
#   2. as linhas de ENVIO entram na FORMA declarada pelo card irmao (canal/direcao/tipo/referencia lidos
#      de hermes/agents/outreach/politica-envio-v1.json — nao digitados aqui);
#   3. as linhas de RESPOSTA entram no vocabulario do dono (ingestao-respostas-v1.json);
#   4. a JANELA e' derivada do instante UTC do envio no fuso declarado (-03:00), inclusive na virada de
#      dia (sexta 02:00Z = quinta 23:00 local) — conferido A MAO sobre a base semeada;
#   5. a grade fecha: 49 celulas, soma das celulas e das duas marginais igual ao total lido;
#   6. amostra minima governa o ranking: celula com 2 envios existe e NAO e' coroada;
#   7. COERENCIA COM O IRMAO: os totais do melhor-horario sao IGUAIS aos do desempenho-mensagens na
#      MESMA base (prova que a atribuicao resposta->envio nao foi reimplementada);
#   8. a analise e' SOMENTE LEITURA: contagem das tabelas identica antes/depois;
#   9. prod RECUSA (exit 4) e a saida e' reproduzivel (duas rodadas iguais, sem carimbo);
#  10. a saida nao carrega organization_id/contact_id/approval_id (agregada).
#
# LIMITE DECLARADO: as linhas de envio/resposta sao semeadas por fixture SQL NA FORMA dos irmaos (a cadeia
# real SMTP/IMAP -> interactions ja foi medida pelo TRE-W6-E07-T01); o que este aceite mede de ponta a ponta
# e' a ANALISE DE JANELA contra o PostgreSQL real. Os dentes (prova de que a suite REPROVA) vivem no
# autoteste do verificador offline.
# Veredito: ACEITE_MELHOR_HORARIO_001_OK / _FALHOU. Exit 0 = OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
CONTAINER="${TRE_TIMING_ACC_CONTAINER:-pg-timing-acc}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
SENHA="timing-aceite-descartavel"
USUARIO="sales_ai"; BANCO="sales_intelligence"
TRABALHO="${TRE_TIMING_TRABALHO:-/tmp/timing-aceite-trabalho}"
MANTER=0
[ "${1:-}" = "--manter" ] && MANTER=1
OK=0; FALHAS=0
item() { if [ "$2" = "$3" ]; then echo "OK     $1 ($3)"; OK=$((OK+1)); else echo "FALHOU $1 (esperado=$2 obtido=$3)"; FALHAS=$((FALHAS+1)); fi; }

command -v docker  >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
for f in "$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" "$RAIZ/hermes/analytics/melhor_horario.py" \
         "$RAIZ/hermes/analytics/desempenho_mensagens.py" "$RAIZ/hermes/agents/outreach/politica-envio-v1.json" \
         "$RAIZ/hermes/agentes/respostas/ingestao-respostas-v1.json"; do
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
for categoria in ("INTERESSE", "OPT_OUT"):
    assert categoria in irmao, categoria
O1, O2, O3 = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
              "33333333-3333-4333-8333-333333333333")
L = ["SET client_min_messages TO WARNING;"]
for i, org in enumerate((O1, O2, O3), start=1):
    L.append("INSERT INTO sales_intelligence.organizations (id, trade_name, status, source) VALUES "
             f"('{org}', 'Org {i}', 'DISCOVERED', 'ACEITE_TIMING');")

def envios(org, variante, dia, horas):
    linhas = []
    for n, hora in enumerate(horas, start=1):
        ref = f"envio:ped-{variante}-{n}:{variante}"
        linhas.append("INSERT INTO sales_intelligence.interactions "
                      "(id, organization_id, channel, direction, interaction_type, occurred_at, "
                      "subject, content_summary, content_reference) VALUES "
                      f"(gen_random_uuid(), '{org}', '{canal_envio}', '{direcao}', '{tipo}', "
                      f"TIMESTAMPTZ '{dia} {hora}:00+00', 'assunto {variante}', 'resumo', '{ref}');")
    return linhas

def resposta(org, hora, categoria):
    return ("INSERT INTO sales_intelligence.interactions "
            "(id, organization_id, channel, direction, interaction_type, occurred_at, "
            "content_reference, response_category) VALUES "
            f"(gen_random_uuid(), '{org}', 'email', 'INBOUND', 'EMAIL_RESPOSTA', "
            f"TIMESTAMPTZ '{hora}+00', 'UIDVALIDITY:1', '{categoria}');")

# ORG1: SEGUNDA 2026-08-31 09:00-11:00 locais (12:00-14:00Z) -> celula segunda/manha; 2 INTERESSE -> 0.4
L += envios(O1, "T1", "2026-08-31", ("12:00", "12:30", "13:00", "13:30", "14:00"))
L.append(resposta(O1, "2026-08-31 12:10:00", "INTERESSE"))
L.append(resposta(O1, "2026-08-31 12:40:00", "INTERESSE"))
# ORG2: TERCA 2026-09-01 14:00-16:00 locais (17:00-19:00Z) -> celula terca/tarde; 1 INTERESSE -> 0.2
L += envios(O2, "T2", "2026-09-01", ("17:00", "17:30", "18:00", "18:30", "19:00"))
L.append(resposta(O2, "2026-09-01 19:10:00", "INTERESSE"))
# ORG3: SEXTA 2026-09-04 02:00/02:30Z = QUINTA 23:00/23:30 locais -> quinta/noite (virada de dia);
#       2 envios < limite de amostra: a celula existe e NAO pode ser coroada.
L += envios(O3, "T3", "2026-09-04", ("02:00", "02:30"))
L.append(resposta(O3, "2026-09-04 03:00:00", "INTERESSE"))
# Excluida: OUTBOUND sem referencia `envio:` nunca entra na grade.
L.append("INSERT INTO sales_intelligence.interactions "
         "(id, organization_id, channel, direction, interaction_type, occurred_at, content_summary) VALUES "
         f"(gen_random_uuid(), '{O1}', '{canal_envio}', '{direcao}', '{tipo}', "
         "TIMESTAMPTZ '2026-08-31 09:00:00+00', 'sem referencia de envio');")
saida.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"fixture ok: canal={canal_envio} direcao={direcao} tipo={tipo}")
PY
"${PSQL[@]}" -q -f - < "$TRABALHO/fixture.sql" >/dev/null || { echo "FALHOU nao semeou a fixture"; exit 2; }
item "A2 fixture: 12 envios + 4 respostas gravados" "12|4" \
  "$("${PSQL[@]}" -c "SELECT (SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference LIKE 'envio:%')||'|'||(SELECT count(*) FROM sales_intelligence.interactions WHERE direction='INBOUND');" </dev/null | tr -d ' ')"

# ---- contagem das tabelas ANTES (prova de somente-leitura)
contagens() {
  "${PSQL[@]}" -c "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence')||'|'||(SELECT count(*) FROM sales_intelligence.interactions)||'|'||(SELECT count(*) FROM sales_intelligence.organizations);" </dev/null | tr -d ' '
}
ANTES="$(contagens)"

PORTA="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"
REL="$TRABALHO/relatorio.json"
python3 "$RAIZ/hermes/analytics/melhor_horario.py" --ambiente dev --prefixo "$PORTA" \
  --desde 2026-08-31T00:00:00Z --ate 2026-09-10T00:00:00Z --janela-dias 14 --limite-amostra 5 \
  --json "$REL" >/dev/null 2>"$TRABALHO/erro.txt"
item "B1 a analise roda contra o PostgreSQL real (exit 0)" 0 "$?"
python3 - "$REL" <<'PY' > "$TRABALHO/itens.txt"
import json, sys
r = json.loads(open(sys.argv[1], encoding="utf-8").read())
c = {(x["dia"], x["faixa"]): x for x in r["por_janela"]}
def linha(nome, esperado, obtido): print(f"{nome}\t{esperado}\t{obtido}")
linha("B2 veredito", "ANALISADO", r["veredito"])
linha("B3 grade completa (7 dias x 7 faixas)", 49, len(r["por_janela"]))
linha("B4 soma das celulas fecha com o total lido", r["totais"]["enviadas"], sum(x["enviadas"] for x in r["por_janela"]))
linha("B5 marginais fecham com o total", [r["totais"]["enviadas"]] * 2,
      [sum(x["enviadas"] for x in r["por_dia"]), sum(x["enviadas"] for x in r["por_faixa"])])
linha("B6 segunda/manha: 5 enviadas, 2 positivas, taxa 0.4", [5, 2, 0.4],
      [c[("segunda", "manha")]["enviadas"], c[("segunda", "manha")]["positivas"], c[("segunda", "manha")]["taxa_de_interesse"]])
linha("B7 terca/tarde: 5 enviadas, 1 positiva, taxa 0.2", [5, 1, 0.2],
      [c[("terca", "tarde")]["enviadas"], c[("terca", "tarde")]["positivas"], c[("terca", "tarde")]["taxa_de_interesse"]])
linha("B8 fuso -03:00: sexta 02:00Z cai em quinta/noite (virada de dia)", 2, c[("quinta", "noite")]["enviadas"])
linha("B9 celula com 2 envios NAO tem amostra suficiente", [2, False],
      [c[("quinta", "noite")]["enviadas"], c[("quinta", "noite")]["amostra_suficiente"]])
linha("B10 melhor janela pela taxa de interesse (segunda/manha 0.4)", ["segunda", "manha"],
      [r["melhor_janela"]["dia"], r["melhor_janela"]["faixa"]] if r["melhor_janela"] else None)
linha("B11 linha OUTBOUND sem referencia `envio:` fica FORA da grade",
      r["totais"]["enviadas"], c[("segunda", "manha")]["enviadas"] + c[("terca", "tarde")]["enviadas"] + c[("quinta", "noite")]["enviadas"])
texto = json.dumps(r, ensure_ascii=False)
linha("B12 saida agregada (sem organization_id/contact_id/approval_id)",
      None, next((t for t in ("11111111-1111-4111-8111", "ped-T1-1", "envio:") if t in texto), None))
PY
while IFS=$'\t' read -r nome esperado obtido; do
  [ -z "$nome" ] && continue
  if [ "$esperado" = "None" ] && [ -z "$obtido" ]; then obtido=""; fi
  item "$nome" "$esperado" "$obtido"
done < "$TRABALHO/itens.txt"

# ---- coerencia com o irmao de desempenho na MESMA base (atribuicao unica)
python3 "$RAIZ/hermes/analytics/desempenho_mensagens.py" --ambiente dev --prefixo "$PORTA" \
  --desde 2026-08-31T00:00:00Z --ate 2026-09-10T00:00:00Z --janela-dias 14 --limite-amostra 5 \
  > "$TRABALHO/irmao.json" 2>/dev/null
item "B13 atribuicao unica: totais do melhor-horario == totais do irmao de desempenho" "IGUAL" \
  "$(python3 - "$REL" "$TRABALHO/irmao.json" <<'PY'
import json, sys
a = json.loads(open(sys.argv[1], encoding="utf-8").read()); b = json.loads(open(sys.argv[2], encoding="utf-8").read())
chaves = ("enviadas", "respondidas", "positivas", "negativas", "opt_outs", "respostas_descartadas")
print("IGUAL" if {k: a["totais"][k] for k in chaves} == {k: b["totais"][k] for k in chaves} else "DIFERENTE")
PY
)"

DEPOIS="$(contagens)"
item "C1 somente leitura: contagem das tabelas identica antes/depois" "$ANTES" "$DEPOIS"

python3 "$RAIZ/hermes/analytics/melhor_horario.py" --ambiente dev --prefixo "$PORTA" \
  --desde 2026-08-31T00:00:00Z --ate 2026-09-10T00:00:00Z --janela-dias 14 --limite-amostra 5 \
  > "$TRABALHO/rel2.json" 2>/dev/null
item "C2 saida reproduzivel (duas rodadas iguais, sem carimbo)" "IGUAL" \
  "$(python3 - "$REL" "$TRABALHO/rel2.json" <<'PY'
import json, sys
a = json.loads(open(sys.argv[1], encoding="utf-8").read()); b = json.loads(open(sys.argv[2], encoding="utf-8").read())
print("IGUAL" if a == b else "DIFERENTE")
PY
)"

python3 "$RAIZ/hermes/analytics/melhor_horario.py" --ambiente prod --prefixo "$PORTA" >/dev/null 2>&1
item "C3 prod RECUSADO (ADR-005)" 4 "$?"

echo
if [ "$FALHAS" -eq 0 ]; then echo "ACEITE_MELHOR_HORARIO_001_OK ($OK itens, 0 falhas)"; exit 0; fi
echo "ACEITE_MELHOR_HORARIO_001_FALHOU ($OK OK, $FALHAS falhas)"; exit 1
