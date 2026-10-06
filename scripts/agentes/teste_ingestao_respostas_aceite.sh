#!/usr/bin/env bash
# Aceite E2E da ingestao/classificacao de respostas (card TRE-W6-E05-T01).
#
# Mede a cadeia INTEIRA contra pontas reais e descartaveis, sem credencial Titan e sem tocar nada que
# ja exista (ADR-005): sink IMAP local (127.0.0.1, TLS proprio, corpus de 10 respostas) + PostgreSQL
# DESCARTavel (container `pg-resp-acc`, migration 0001 aplicada) + o componente
# `hermes/agentes/respostas/ingestao_respostas.py` gravando em sales_intelligence.
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. config/guardas: --planejar/--conferir sem banco, prod RECUSA (exit 4), prefixo de banco
#      remoto RECUSA em dev (BANCO_NAO_E_DEV), --ingerir sem --confirmo e DRY_RUN;
#   2. leitura: a caixa e lida (uidvalidity/EXAMINE) e o sink atesta EXAMINE, 0 busca sem PEEK,
#      0 comando de escrita e nenhuma mensagem marcada \Seen (invariante do card pai preservado);
#   3. classificacao: cada resposta do corpus vira UMA linha em interactions com a categoria
#      esperada, intent/sentiment coerentes e content_reference = UIDVALIDITY:UID;
#   4. vinculo: remetente fora de contacts NAO inventa organizacao — vira sync_events SEM_VINCULO;
#   5. escopo de escrita: snapshot de contagem das 12 tabelas antes/depois — so interactions e
#      sync_events mudam (nenhuma DDL, nenhum UPDATE/DELETE);
#   6. idempotencia: replay da mesma rodada -> JA_INGERIDO, zero linha nova;
#   7. dente de classificacao: a resposta cujo descadastro existe SO na citacao historica nao pode
#      virar OPT_OUT (limpeza de citacao medida no banco, nao so na unidade).
#
# Pre-requisitos: docker com imagem postgres:16, python3, openssl. Nada de rede externa.
# Uso: bash scripts/agentes/teste_ingestao_respostas_aceite.sh [--manter] [--prova-de-dente] [--sub-run] [--modulo <py>]
# Exit: 0 = OK · 1 = FALHOU · 2 = uso/guarda · 3 = nao testavel.
# --prova-de-dente: muta uma COPIA do componente (ingestao_respostas.py) e exige que o aceite
# REPROVE o ITEM ESPERADO (item 12, o dente de citacao) — sem tocar o componente real.
set -u

OK=0; FALHAS=0
BASE="${TRE_ACEITE_BASE:-/tmp/aceite-e05t01}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PG=pg-resp-acc
SENHA_SINK="senha-do-sink-e05"
USUARIO_SINK="sink-dev@dev.local"
MODULO="hermes/agentes/respostas/ingestao_respostas.py"
PORTA_BANCO="docker exec -i $PG psql -U sales_ai -d sales_intelligence"
MANTER=0
DENTE=0
SUB_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --manter) MANTER=1 ;;
    --prova-de-dente) DENTE=1 ;;
    --sub-run) SUB_RUN=1 ;;
    --modulo) shift; MODULO="${1:?--modulo exige caminho}" ;;
    --modulo=*) MODULO="${1#--modulo=}" ;;
    *) echo "uso: $0 [--manter] [--prova-de-dente] [--sub-run] [--modulo <py>]" >&2; exit 2 ;;
  esac
  shift
done
[ "$SUB_RUN" = "1" ] && DENTE=0   # sub-run do dente: roda a medicao com o mutante, sem re-mutar

item() { # item <nome> <0|1>
  if [ "$2" = "0" ]; then echo "OK    $1"; OK=$((OK+1)); else echo "FALHOU $1"; FALHAS=$((FALHAS+1)); fi
}
psql_q() { docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -t -A -c "$1" 2>/dev/null; }
limpar() {
  docker rm -f -v "$PG" >/dev/null 2>&1
  [ -n "${PID_SINK:-}" ] && kill "$PID_SINK" >/dev/null 2>&1
  [ -n "${PID_SINK:-}" ] && wait "$PID_SINK" 2>/dev/null
  return 0
}
[ "$MANTER" = "1" ] || trap limpar EXIT

rm -rf "$BASE"; mkdir -p "$BASE/ca" "$BASE/fx" "$BASE/out"
cd "$REPO" || exit 1

# ---------------------------------------------------------------------------------------------
# --prova-de-dente: muta uma COPIA do componente e exige que o aceite REPROVE o ITEM ESPERADO.
# O aceite roda de novo (--sub-run --modulo <mutante>) e a prova so' vale se ele sair != 0 E
# a linha 'FALHOU <item nomeado>' aparecer — item que ja' passa no codigo bom nao serve de dente.
# ---------------------------------------------------------------------------------------------
if [ "$DENTE" -eq 1 ]; then
  echo "== prova de dente (mutacao em COPIA do componente; o componente real nao e' tocado)"
  TRABALHO="$BASE/dente"
  rm -rf "$TRABALHO"; mkdir -p "$TRABALHO"
  mutar() { # <rotulo> <ancora exata> <troca>
    MUT_ALVO="$2" MUT_TROCA="$3" MUT_SAIDA="$TRABALHO/mut-$1.py" MUT_ORIGEM="$MODULO" python3 - <<'PY'
import os, pathlib, sys
origem = pathlib.Path(os.environ["MUT_ORIGEM"]).read_text(encoding="utf-8")
alvo, troca = os.environ["MUT_ALVO"], os.environ["MUT_TROCA"]
n = origem.count(alvo)
if n == 0:
    print("MUTACAO_NAO_APLICAVEL: ancora ausente", file=sys.stderr); sys.exit(9)
if n != 1:
    print(f"MUTACAO_AMBIGUA: ancora aparece {n}x", file=sys.stderr); sys.exit(9)
pathlib.Path(os.environ["MUT_SAIDA"]).write_text(origem.replace(alvo, troca, 1), encoding="utf-8")
print("mutante escrito:", os.environ["MUT_SAIDA"])
PY
  }
  dente() { # <rotulo> <item esperado>
    local saida="$TRABALHO/dente-$1.out"
    # BASE proprio do sub-run: ele faz 'rm -rf "$BASE"' no inicio e nao pode apagar este log.
    TRE_ACEITE_BASE="$TRABALHO/sub-$1" bash "$0" --sub-run --modulo "$TRABALHO/mut-$1.py" >"$saida" 2>&1
    local rc=$?
    if [ "$rc" -ne 0 ] && grep -qF "FALHOU $2" "$saida"; then
      echo "DENTE_OK $1 — o aceite REPROVOU o item esperado (exit=$rc): $2"
      DENTES_OK=$((DENTES_OK + 1))
    else
      echo "DENTE_FALHOU $1 — esperava exit!=0 e a linha 'FALHOU $2' (veio exit=$rc)"
      grep -E "^(FALHOU|ACEITE_)" "$saida" | head -5
    fi
  }
  DENTES_OK=0
  # Dente do card (item 12): se a limpeza de citacao cair, a resposta cujo descadastro so' existe
  # no historico citado vira OPT_OUT — e o aceite TEM de reprovar o item 12 nomeado.
  mutar citacao '        if re.match(r"^(em|on)\s.{0,200}(escreveu|wrote):\s*$", normalizar(t)):' \
    '        if False and re.match(r"^(em|on)\s.{0,200}(escreveu|wrote):\s*$", normalizar(t)):'
  dente citacao "dente: descadastro so na citacao NAO vira OPT_OUT"
  echo "-- dentes OK=$DENTES_OK de 1"
  if [ "$DENTES_OK" = "1" ]; then echo "PROVA_DE_DENTE_OK"; exit 0; fi
  echo "PROVA_DE_DENTE_FALHOU"; exit 1
fi

echo "== 0. pre-flight"
docker info >/dev/null 2>&1; item "docker responde (daemon presente)" $?
command -v python3 >/dev/null 2>&1; item "python3 disponivel" $?
command -v openssl >/dev/null 2>&1; item "openssl disponivel" $?

echo "== 1. fixtures e certificado do sink"
python3 scripts/integracoes/gerar-fixtures-respostas.py --saida "$BASE/fx/respostas.jsonl" \
  --expectativa "$BASE/fx/expectativa.json" >"$BASE/fx/gerador.out" 2>&1
item "corpus de respostas gerado (10 casos rotulados)" $?
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$BASE/ca/dev.key" -out "$BASE/ca/dev.pem" \
  -days 2 -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1" >/dev/null 2>&1
item "certificado TLS proprio gerado" $?

echo "== 2. PostgreSQL descartavel + migration 0001"
docker rm -f -v "$PG" >/dev/null 2>&1
docker run -d --name "$PG" -e POSTGRES_PASSWORD=dev -e POSTGRES_USER=postgres postgres:16 >/dev/null 2>&1
item "container descartavel $PG criado" $?
pronto=1
for _ in $(seq 1 60); do
  prontos=$(docker logs "$PG" 2>&1 | grep -c "database system is ready to accept connections")
  if [ "${prontos:-0}" -ge 2 ] \
     && docker exec "$PG" psql -U postgres -d postgres -tAc "select 1" >/dev/null 2>&1; then
    pronto=0; break
  fi
  sleep 1
done
item "postgres pronto (init concluido: 2x 'ready to accept connections')" $pronto
criado=1
for _ in $(seq 1 15); do
  if docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE sales_ai LOGIN PASSWORD 'dev';" \
     >"$BASE/pg-role.out" 2>&1; then criado=0; break; fi
  sleep 2
done
item "role sales_ai criado" $criado
docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE sales_intelligence OWNER sales_ai;" \
  >"$BASE/pg-db.out" 2>&1
item "database sales_intelligence criada" $?
docker exec -i "$PG" psql -U sales_ai -d sales_intelligence -q -v ON_ERROR_STOP=1 \
  < db/migrations/0001_sales_intelligence_v1.sql >"$BASE/migration.out" 2>&1
item "migration 0001 aplicada (12 tabelas)" $?
TABELAS=$(psql_q "select count(*) from information_schema.tables where table_schema='sales_intelligence';")
[ "$TABELAS" = "12" ] && item "schema com 12 tabelas" 0 || item "schema com 12 tabelas (obtido '$TABELAS')" 1

echo "== 3. evidencia de vinculo (organizacao + contatos reais)"
psql_q "INSERT INTO sales_intelligence.organizations (id, legal_name, trade_name, domain, status)
  VALUES ('11111111-1111-1111-1111-111111111111','Cliente Demo LTDA','Cliente Demo','cliente-demo.test','QUALIFIED');
  INSERT INTO sales_intelligence.contacts (id, organization_id, full_name, email, email_status)
  VALUES ('22222222-2222-2222-2222-222222222222','11111111-1111-1111-1111-111111111111','Joao Demo','joao@cliente-demo.test','VALID'),
         ('33333333-3333-3333-3333-333333333333','11111111-1111-1111-1111-111111111111','Ana Demo','ana@cliente-demo.test','VALID'),
         ('44444444-4444-4444-4444-444444444444','11111111-1111-1111-1111-111111111111','Maria Demo','maria@cliente-demo.test','VALID'),
         ('55555555-5555-5555-5555-555555555555','11111111-1111-1111-1111-111111111111','Carlos Demo','carlos@cliente-demo.test','VALID'),
         ('66666666-6666-6666-6666-666666666666','11111111-1111-1111-1111-111111111111','Lead Dois','lead2@cliente-demo.test','VALID'),
         ('77777777-7777-7777-7777-777777777777','11111111-1111-1111-1111-111111111111','Alguem Demo','alguem@cliente-demo.test','VALID'),
         ('88888888-8888-8888-8888-888888888888','11111111-1111-1111-1111-111111111111','Contato Auto','contato@cliente-demo.test','VALID');" >/dev/null 2>&1
item "organizacao + 7 contatos inseridos" $?
CONTATOS=$(psql_q "select count(*) from sales_intelligence.contacts;")
[ "$CONTATOS" = "7" ] && item "7 contatos no vinculo (obtido $CONTATOS)" 0 || item "7 contatos no vinculo (obtido '$CONTATOS')" 1

echo "== 4. sink IMAP com o corpus de respostas"
python3 scripts/integracoes/sink-imap-dev.py --porta 2993 --modo implicit_tls --cert "$BASE/ca/dev.pem" \
  --chave "$BASE/ca/dev.key" --senha "$SENHA_SINK" --fixtures "$BASE/fx/respostas.jsonl" \
  --captura "$BASE/sink.jsonl" --pronto "$BASE/sink.pronto" --pidfile "$BASE/sink.pid" \
  >"$BASE/sink.log" 2>&1 &
PID_SINK=$!
for _ in $(seq 1 30); do [ -s "$BASE/sink.pronto" ] && break; sleep 0.5; done
[ -s "$BASE/sink.pronto" ] && item "sink escutando em 127.0.0.1:2993" 0 || item "sink escutando em 127.0.0.1:2993" 1

export TRE_AMBIENTE=dev TRE_TITAN_IMAP_HOST=127.0.0.1 TRE_TITAN_IMAP_PORT=2993 \
  TRE_TITAN_IMAP_SEGURANCA=implicit_tls TRE_TITAN_IMAP_CAIXA=INBOX TRE_TITAN_USER="$USUARIO_SINK" \
  TRE_TITAN_PASSWORD="$SENHA_SINK" TRE_TITAN_CA="$BASE/ca/dev.pem" TRE_TITAN_DOMINIO_DEV=dev.local \
  TRE_RESPOSTAS_PORTA_BANCO="$PORTA_BANCO"
COMPONENTE="python3 $MODULO"

echo "== 5. guardas antes de escrever"
$COMPONENTE --planejar >"$BASE/c5-planejar.out" 2>&1; item "--planejar sem banco (exit 0)" $?
$COMPONENTE --conferir >"$BASE/c5-conferir.out" 2>&1; item "--conferir (exit 0)" $?
$COMPONENTE --ingerir --ambiente prod --chave-idempotencia x >"$BASE/c5-prod.out" 2>&1
[ $? -eq 4 ] && item "prod RECUSA a ingesta (exit 4)" 0 || item "prod RECUSA a ingesta (exit 4)" 1
TRE_RESPOSTAS_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence" \
  $COMPONENTE --ingerir --chave-idempotencia x --confirmo >"$BASE/c5-remoto.out" 2>&1
[ $? -eq 3 ] && grep -q BANCO_NAO_E_DEV "$BASE/c5-remoto.out" && item "dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV, exit 3)" 0 \
  || item "dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV, exit 3)" 1
$COMPONENTE --ingerir --chave-idempotencia rodada-1 >"$BASE/c5-dry.out" 2>&1
grep -q DRY_RUN "$BASE/c5-dry.out" && ! grep -q CAIXA_LIDA "$BASE/c5-dry.out" \
  && item "--ingerir sem --confirmo e DRY_RUN (nao le a caixa)" 0 || item "--ingerir sem --confirmo e DRY_RUN (nao le a caixa)" 1

echo "== 6. snapshot das 12 tabelas ANTES"
ANTES="$BASE/antes.txt"
: > "$ANTES"
for t in organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >> "$ANTES"
done
cat "$ANTES"

echo "== 7. ingesta real"
$COMPONENTE --ingerir --confirmo --chave-idempotencia rodada-1 --saida "$BASE/out" \
  --porta-banco "$PORTA_BANCO" --relatorio "$BASE/relatorio.json" --registro "$BASE/trilha.jsonl" \
  >"$BASE/aceite-rodada1.out" 2>&1
item "ingesta concluida (exit 0)" $?
grep -q INGESTAO_RESPOSTAS_001_OK "$BASE/aceite-rodada1.out" \
  && item "veredito INGESTAO_RESPOSTAS_001_OK" 0 || item "veredito INGESTAO_RESPOSTAS_001_OK" 1

echo "== 8. classificacao medida NO BANCO"
python3 - "$BASE/fx/expectativa.json" <<'PY' >"$BASE/checagens.txt"
import json, subprocess, sys
esperado = json.load(open(sys.argv[1], encoding="utf-8"))
def q(sql):
    out = subprocess.run(["docker", "exec", "-i", "pg-resp-acc", "psql", "-U", "sales_ai",
                          "-d", "sales_intelligence", "-t", "-A", "-c", sql],
                         capture_output=True, text=True)
    return out.stdout.strip()
for uid, info in sorted(esperado.items(), key=lambda kv: int(kv[0])):
    if info["esperado"] == "NAO_RESPOSTA":
        n = q("select count(*) from sales_intelligence.interactions where content_reference like '%:" + uid + "';")
        print(("OK    " if n == "0" else "FALHOU ") + f"uid {uid} ({info['rotulo']}): NAO_RESPOSTA nao vira interacao")
    elif not info["remetente_vinculado"]:
        st = q("select status from sales_intelligence.sync_events where idempotency_key like 'resposta:%:" + uid + "' limit 1;")
        print(("OK    " if st == "SEM_VINCULO" else "FALHOU ") + f"uid {uid} ({info['rotulo']}): SEM_VINCULO sem inventar organizacao (obtido '{st}')")
    else:
        linha = q("select response_category||'|'||coalesce(intent,'-')||'|'||coalesce(sentiment,'-')||'|'||"
                  "content_reference||'|'||organization_id::text||'|'||ai_confidence::text "
                  "from sales_intelligence.interactions where content_reference like '%:" + uid + "' limit 1;")
        cat = linha.split("|")[0] if linha else ""
        print(("OK    " if cat == info["esperado"] else "FALHOU ") +
              f"uid {uid} ({info['rotulo']}): categoria {info['esperado']} (obtido '{cat}') {linha}")
PY
while read -r linha; do
  case "$linha" in
    OK*) item "${linha#OK    }" 0 ;;
    *) item "${linha#FALHOU }" 1 ;;
  esac
done < "$BASE/checagens.txt"

TOTAL=$(psql_q "select count(*) from sales_intelligence.interactions;")
[ "$TOTAL" = "7" ] && item "7 interacoes gravadas (uma por resposta comercial)" 0 \
  || item "7 interacoes gravadas (obtido '$TOTAL')" 1

echo "== 9. snapshot depois: so interactions e sync_events mudam"
DEPOIS="$BASE/depois.txt"
: > "$DEPOIS"
for t in organizations contacts signals research_runs pain_hypotheses scores interactions recommendations agent_runs outbox_events sync_events human_approvals; do
  echo "$t=$(psql_q "select count(*) from sales_intelligence.$t;")" >> "$DEPOIS"
done
MUDOU=$(diff "$ANTES" "$DEPOIS" | grep '^>' | sed 's/^> //' | cut -d= -f1 | tr '\n' ' ')
case "$(echo $MUDOU)" in
  "interactions sync_events") item "somente interactions e sync_events mudaram ($MUDOU)" 0 ;;
  *) item "somente interactions e sync_events mudaram (mudou: '$MUDOU')" 1 ;;
esac

echo "== 10. invariante de leitura medido no sink"
python3 - "$BASE/sink.jsonl" <<'PY' >"$BASE/sink-resumo.txt"
import json, sys
eventos = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
final = [e for e in eventos if e.get("evento") == "ESTADO_FINAL"]
if not final:
    print("FALHOU sink sem ESTADO_FINAL (nenhuma conexao medida)")
    raise SystemExit(0)
e = final[-1]
selecoes = e.get("selecoes") or []
checks = [
    ("todas as selecoes em EXAMINE", all("EXAMINE" in s for s in selecoes) and len(selecoes) > 0),
    ("nenhuma busca sem PEEK (total_buscas_sem_peek=0)", e.get("total_buscas_sem_peek") == 0),
    ("nenhum comando de escrita IMAP (total_comandos_de_escrita=[])", e.get("total_comandos_de_escrita") == []),
    ("nenhuma mensagem marcada como lida (\\Seen)", e.get("mensagens_marcadas_lidas") == []),
    ("corpos buscados = 10 mensagens do corpus", e.get("total_corpos_buscados") == 10),
]
for nome, ok in checks:
    print(("OK    " if ok else "FALHOU ") + "sink: " + nome)
PY
while read -r linha; do
  case "$linha" in
    OK*) item "${linha#OK    }" 0 ;;
    *) item "${linha#FALHOU }" 1 ;;
  esac
done < "$BASE/sink-resumo.txt"

echo "== 11. idempotencia (replay da mesma rodada)"
$COMPONENTE --ingerir --confirmo --chave-idempotencia rodada-1 --porta-banco "$PORTA_BANCO" \
  >"$BASE/aceite-replay.out" 2>&1
item "replay concluido (exit 0)" $?
REPLAYS=$(grep -c JA_INGERIDO "$BASE/aceite-replay.out")
[ "$REPLAYS" = "10" ] && item "todas as 10 mensagens em JA_INGERIDO" 0 \
  || item "todas as 10 mensagens em JA_INGERIDO (obtido $REPLAYS)" 1
TOTAL2=$(psql_q "select count(*) from sales_intelligence.interactions;")
[ "$TOTAL2" = "$TOTAL" ] && item "replay nao criou linha nova ($TOTAL2 = $TOTAL)" 0 \
  || item "replay nao criou linha nova ($TOTAL2 != $TOTAL)" 1

echo "== 12. segredo e dente de citacao"
if grep -rq "$SENHA_SINK" "$BASE/relatorio.json" "$BASE/trilha.jsonl" "$BASE/out" "$BASE/aceite-rodada1.out" 2>/dev/null; then
  item "senha do sink ausente de relatorio/trilha/saida" 1
else
  item "senha do sink ausente de relatorio/trilha/saida" 0
fi
CIT=$(psql_q "select response_category from sales_intelligence.interactions where content_reference like '%:8' limit 1;")
[ "$CIT" = "INDEFINIDO" ] && item "dente: descadastro so na citacao NAO vira OPT_OUT (obtido INDEFINIDO)" 0 \
  || item "dente: descadastro so na citacao NAO vira OPT_OUT (obtido '$CIT')" 1

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "ACEITE_INGESTAO_RESPOSTAS_001_OK ($OK itens, 0 falhas)"
  exit 0
fi
echo "FALHOU ($OK itens, $FALHAS falhas)"
exit 1
