#!/usr/bin/env bash
# Aceite do CRM basico no ambiente DEV do TRE (modulo crm + funil comercial configurado).
#
# Card: TRE-W2-E02-T01 (`t_adea8e6b`). Runbook: docs/runbooks/odoo-crm-dev.md.
#
# Verificador INDEPENDENTE do configurador: nao chama o configurador nem o ORM, nao escreve
# nada no ambiente — le o ESTADO VIVO (psql dentro de pg-odoo-dev, containers, HTTP, UFW) e
# compara com a declaracao versionada odoo/crm/funil-transformativa.yaml.
#
# Roda NA VPS. Uso:  bash verificar-crm-dev.sh
# Saida: itens OK/FALHOU, um por verificacao, e a ultima linha
#   RESULTADO: CRM_DEV_OK (N itens, 0 falhas)      -> exit 0
#   RESULTADO: CRM_DEV_FALHOU (N itens, M falhas)  -> exit 1
#
# Variaveis (opcionais): TRE_ODOO_COMPOSE, TRE_ODOO_ENV, TRE_CRM_YAML, TRE_ODOO_BANCO.
set -u

COMPOSE="${TRE_ODOO_COMPOSE:-/opt/tre/dev/compose/odoo.yml}"
ENVFILE="${TRE_ODOO_ENV:-/opt/tre/dev/compose/odoo.env}"
YAML="${TRE_CRM_YAML:-/opt/tre/dev/odoo/crm/funil-transformativa.yaml}"
BANCO="${TRE_ODOO_BANCO:-odoo_dev}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ITENS="$TMP/itens.txt"; : > "$ITENS"

item() { local r="$1"; shift; printf '%s %s\n' "$r" "$*" >> "$ITENS"; printf '%-6s %s\n' "$r" "$*"; }
ok()  { item OK "$@"; }
nao() { item FALHOU "$@"; }
psql_odoo() { docker exec -i pg-odoo-dev psql -U odoo -d "$BANCO" -tA -f - ; }

echo "IDENTIDADE"
if [ -f "$YAML" ]; then
  echo "  declaracao..... $YAML"
  echo "  sha256......... $(sha256sum "$YAML" | awk '{print $1}')"
else
  echo "  declaracao..... AUSENTE ($YAML)"
fi
echo "  banco medido... $BANCO em pg-odoo-dev"
echo "  medido em...... $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo

# ---------------------------------------------------------------------------
# 1. Ambiente (o aceite do CRM nao pode ter regredido o que o card E01 aceitou)
# ---------------------------------------------------------------------------
if [ -f "$COMPOSE" ] && [ -f "$ENVFILE" ] \
   && docker compose --env-file "$ENVFILE" -f "$COMPOSE" config -q 2>/dev/null; then
  ok "par do Odoo do dev presente e \`docker compose config\` valido"
else
  nao "par do Odoo do dev ausente ou compose invalido ($COMPOSE / $ENVFILE)"
fi

# O ambiente dev e' COMPARTILHADO: MEDIDO em 01/10/2026 (card TRE-W2-E01-T02, TLS/proxy) que o
# container odoo-dev e' reiniciado por outro card no meio de uma medicao. Uma espera curta evita
# reprovar ESTE aceite por indisponibilidade que nao e' deste card (o que este card mede e' o CRM
# configurado; se o servico nao voltar, o item abaixo reprova — honesto).
for _ in $(seq 1 18); do
  if docker ps --format '{{.Names}}' | grep -qx 'odoo-dev' \
     && docker ps --format '{{.Names}}' | grep -qx 'pg-odoo-dev'; then
    break
  fi
  sleep 5
done

for c in odoo-dev pg-odoo-dev; do
  if docker ps --format '{{.Names}}' | grep -qx "$c"; then
    ok "container $c de pe"
  else
    nao "container $c NAO esta rodando"
  fi
done

PORT="$(sed -n 's/^ODOO_HTTP_PORT=//p' "$ENVFILE" 2>/dev/null)"
if [ -n "$PORT" ]; then
  # Repete a medida: indisponibilidade de um instante (outro card do dev reiniciando o servico)
  # nao e' o que este aceite mede — o que ele mede e' o servico de pe e respondendo.
  CODIGO="000"
  for _ in 1 2 3 4 5; do
    CODIGO="$(curl -s -o "$TMP/login.html" -m 10 -w '%{http_code}' "http://127.0.0.1:${PORT}/web/login" || true)"
    [ "$CODIGO" = "200" ] && break
    sleep 3
  done
  if [ "$CODIGO" = "200" ] && grep -qi 'odoo' "$TMP/login.html"; then
    ok "Odoo responde HTTP 200 na tela de login (127.0.0.1:$PORT/web/login) e a pagina e' do Odoo"
  else
    nao "Odoo nao respondeu 200 com pagina do Odoo em 127.0.0.1:$PORT/web/login (codigo: $CODIGO)"
  fi
else
  nao "ODOO_HTTP_PORT ausente em $ENVFILE (nao da' para medir o servico)"
fi

if docker ps -a --format '{{.Names}}' | grep -qE '^(odoo|pg-odoo)-(homolog|prod)$'; then
  nao "existe container de homologacao/producao — o card so opera o dev (ADR-005)"
else
  ok "nenhum container de homologacao/producao (so dev)"
fi

PORTA_CHK="${PORT:-8069}"
# O invariante DESTE card e' que o Odoo NAO ganha exposicao publica: o bind tem de continuar
# loopback. Exposicao publica (80/443 do proxy) e' do card TRE-W2-E01-T02, que roda em
# paralelo — por isso o item nao exige "UFW so com 22/tcp" (fato que ja' mudou por outro card),
# e sim que nao exista bind publico nem regra de UFW para a porta do proprio Odoo.
# A 4a coluna do `ss` e' o endereco LOCAL; a 5a e' o peer (que sempre traz 0.0.0.0:* e nao diz
# nada sobre exposicao) — comparar com a linha toda dava falso positivo.
BINDS="$(ss -lntH "sport = :${PORTA_CHK}" 2>/dev/null | awk '{print $4}' | sort -u | tr '\n' ' ')"
NAO_LOCAL=0
[ -n "${BINDS// /}" ] || NAO_LOCAL=1
for b in $BINDS; do
  case "$b" in
    127.0.0.1:*|\[::1\]:*) : ;;
    *) NAO_LOCAL=$((NAO_LOCAL+1)) ;;
  esac
done
if [ "$NAO_LOCAL" = "0" ]; then
  ok "porta do Odoo ($PORTA_CHK) so em loopback (binds medidos: $BINDS)"
else
  nao "porta do Odoo ($PORTA_CHK) em endereco nao-loopback ou ausente (binds: ${BINDS:-nenhum})"
fi

if ufw status 2>/dev/null | grep -qE "^${PORTA_CHK}(/tcp)?[[:space:]]+ALLOW"; then
  nao "UFW tem regra liberando a porta do Odoo ($PORTA_CHK) — exposicao publica e' decisao do card TRE-W2-E01-T02"
else
  ok "UFW sem regra para a porta do Odoo ($PORTA_CHK); regras ALLOW atuais: $(ufw status 2>/dev/null | awk '$2 == "ALLOW" {gsub(/ \(v6\)/, "", $1); if (!visto[$1]++) printf "%s ", $1}')"
fi

# ---------------------------------------------------------------------------
# 2. Modulo `crm` e funil comercial (dado vivo)
# ---------------------------------------------------------------------------
ESTADO_CRM="$(psql_odoo <<'SQL' | tr -d '[:space:]'
select coalesce((select state from ir_module_module where name = 'crm'), 'ausente');
SQL
)"
if [ "$ESTADO_CRM" = "installed" ]; then
  ok "modulo crm instalado no banco $BANCO"
else
  nao "modulo crm nao esta instalado no banco $BANCO (estado: $ESTADO_CRM)"
fi

# `crm_stage.name` e `crm_team.name` sao traduziveis (JSONB no banco) e `is_won` pode estar
# NULO quando a etapa e' criada sem esse campo (medido: etapa criada por fora fica is_won NULL).
# Duas armadilhas vencidas aqui, as duas MEDIDAS nesta execucao:
#   * `rpad(jsonb, int)` nao existe -> sem `->>` a consulta reprova;
#   * `NULL || '|' || ...` == NULL: a linha imprimia VAZIA e o comparador a descartava em
#     silencio (uma etapa intrusa no banco passava como OK). Por isso TODO campo vai em
#     `coalesce(..., '<marcador>')` e o nome sai de `jsonb_each_text`, que pega qualquer idioma.
psql_odoo <<'SQL' > "$TMP/etapas.txt" 2>/dev/null
select coalesce((select string_agg(v, ' / ' order by k) from jsonb_each_text(s.name) as x(k, v)),
                '<SEM NOME>') || '|' ||
       coalesce(s.sequence::text, '<SEM SEQ>') || '|' ||
       coalesce(s.is_won::text, 'false') || '|' ||
       coalesce((select string_agg(coalesce(t.name->>'pt_BR', t.name->>'en_US'), ',' order by t.id)
                 from crm_team t
                 join crm_stage_crm_team_rel r on r.crm_team_id = t.id
                 where r.crm_stage_id = s.id), '-')
from crm_stage s
order by s.sequence, coalesce(s.name->>'en_US', s.name::text);
SQL

psql_odoo <<'SQL' > "$TMP/campos.txt" 2>/dev/null
select column_name from information_schema.columns
where table_name = 'crm_lead' order by column_name;
SQL

cat > "$TMP/confere.py" <<'PY'
import sys

import yaml

caminho_yaml, caminho_etapas, caminho_campos = sys.argv[1], sys.argv[2], sys.argv[3]
falhas = 0


def item(condicao, texto):
    global falhas
    print("%-6s %s" % ("OK" if condicao else "FALHOU", texto))
    if not condicao:
        falhas += 1


with open(caminho_yaml, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)

etapas = []
with open(caminho_etapas, encoding="utf-8") as fh:
    for linha in fh:
        linha = linha.rstrip("\n")
        if not linha.strip():
            print("FALHOU linha de medicao VAZIA (registro de crm.stage que a leitura perdeu)")
            falhas += 1
            continue
        if len(linha.split("|")) != 4:
            print("FALHOU linha de medicao malformada (%d campos, esperado 4): %r"
                  % (len(linha.split("|")), linha))
            falhas += 1
            continue
        nome, seq, won, times = linha.split("|")
        etapas.append({"nome": nome, "seq": int(seq), "won": won in ("t", "true", "True"),
                       "times": [t for t in times.split(",") if t and t != "-"]})

declaradas = {e["nome"]: e for e in cfg["pipeline_padrao"]["etapas"]}
lateral = cfg.get("ramo_lateral") or {}
nome_lateral = lateral["nome"] if lateral.get("representacao") == "pipeline_proprio" else None
esperadas = set(declaradas) | ({nome_lateral} if nome_lateral else set())

medidas = [e["nome"] for e in etapas]
faltando = sorted(esperadas - set(medidas))
a_mais = sorted(set(medidas) - esperadas)
item(not faltando and not a_mais,
     "conjunto de etapas == declaracao (faltando: %s; a mais: %s)"
     % (", ".join(faltando) or "nenhuma", ", ".join(a_mais) or "nenhuma"))

for nome, declarada in declaradas.items():
    achadas = [e for e in etapas if e["nome"] == nome]
    if len(achadas) != 1:
        item(False, "etapa '%s': esperava 1 registro, medidos %d" % (nome, len(achadas)))
        continue
    medida = achadas[0]
    item(medida["seq"] == declarada["sequencia"]
         and medida["won"] == bool(declarada.get("is_won", False)) and medida["times"],
         "etapa '%-20s' seq=%s won=%s times=%s (declarado: seq=%s won=%s)"
         % (nome, medida["seq"], medida["won"], ",".join(medida["times"]) or "-",
            declarada["sequencia"], bool(declarada.get("is_won", False))))

presentes = {e["nome"]: e for e in etapas}

times_funil = sorted({t for nome in declaradas if nome in presentes for t in presentes[nome]["times"]})
item(times_funil == [cfg["pipeline_padrao"]["nome"]],
     "as %d etapas do funil estao em um unico pipeline '%s' (medido: %s)"
     % (len(declaradas), cfg["pipeline_padrao"]["nome"], ",".join(times_funil) or "-"))

ganho = [e for e in etapas if e["won"]]
ultimas = [e for e in etapas if e["nome"] in declaradas]
ultima = max(ultimas, key=lambda e: e["seq"]) if ultimas else None
item(len(ganho) == 1 and ganho[0]["nome"] == "Won" and ultima is not None and ultima["nome"] == "Won",
     "exatamente uma etapa de ganho, '%s', e ela e' a ultima do funil (ganho medido: %s; "
     "ultima etapa do funil: %s)"
     % ("Won", ",".join(e["nome"] for e in ganho) or "nenhuma",
        ultima["nome"] if ultima else "nenhuma"))

if nome_lateral:
    achadas = [e for e in etapas if e["nome"] == nome_lateral]
    if len(achadas) == 1:
        times_lat = sorted(achadas[0]["times"])
        item(len(times_lat) == 1 and times_lat[0] not in times_funil,
             "ramo lateral '%s' em pipeline proprio (times: %s; funil: %s)"
             % (nome_lateral, ",".join(times_lat) or "-", ",".join(times_funil) or "-"))
    else:
        item(False, "ramo lateral '%s': esperava 1 etapa, medidos %d" % (nome_lateral, len(achadas)))

com_time = [e["nome"] for e in etapas if not e["times"]]
item(not com_time, "nenhuma etapa sem pipeline (etapa global apareceria em todo pipeline): %s"
     % (", ".join(com_time) or "nenhuma"))

with open(caminho_campos, encoding="utf-8") as fh:
    colunas = {linha.strip() for linha in fh if linha.strip()}
campos = cfg.get("campos_minimos", {})
ausentes = [c for c in campos.get("campos", []) if c not in colunas]
item(not ausentes, "campos minimos presentes em %s (%d/%d; ausentes: %s)"
     % (campos.get("modelo"), len(campos.get("campos", [])) - len(ausentes),
        len(campos.get("campos", [])), ", ".join(ausentes) or "nenhum"))

print("CONFRONTO_FIM falhas=%d" % falhas)
sys.exit(1 if falhas else 0)
PY

if [ -s "$TMP/etapas.txt" ]; then
  python3 "$TMP/confere.py" "$YAML" "$TMP/etapas.txt" "$TMP/campos.txt" > "$TMP/confere.out" 2>&1 || true
  cat "$TMP/confere.out"
  grep -E '^(OK|FALHOU) ' "$TMP/confere.out" >> "$ITENS"
  # Fail-closed: crash do confronto nao pode virar PASS (item faltando nao e' item verde).
  grep -q '^CONFRONTO_FIM' "$TMP/confere.out" \
    || nao "o confronto declaracao x estado vivo nao rodou por inteiro (sem linha CONFRONTO_FIM)"
else
  nao "nao consegui ler crm_stage do banco $BANCO (modulo crm ausente?)"
fi

# Oportunidade sem etapa ou em pipeline nao declarado nao pode passar (funil incompleto).
SEM_ETAPA="$(psql_odoo <<'SQL' | tr -d '[:space:]'
select count(*) from crm_lead where stage_id is null;
SQL
)"
if [ "$SEM_ETAPA" = "0" ]; then
  ok "nenhuma oportunidade sem etapa no funil (crm_lead.stage_id nulo)"
else
  nao "$SEM_ETAPA oportunidade(s) sem etapa no funil"
fi

# ---------------------------------------------------------------------------
# 3. Separacao de ambientes e do banco (o CRM do dev nao pode ter mexido nisso)
# ---------------------------------------------------------------------------
if docker exec -i pg-odoo-dev psql -U odoo -d postgres -tAc "select 1 from pg_database where datname='sales_intelligence'" 2>/dev/null | grep -q 1; then
  nao "o Postgres do Odoo tem o banco sales_intelligence (separacao dos ambientes quebrada)"
else
  ok "banco do Odoo separado do sales_intelligence (nao existe la')"
fi

if docker ps -a --format '{{.Names}}' | grep -qx 'pg-sales-dev'; then
  ok "pg-sales-dev (Sales Intelligence) segue de pe, intocado por este card"
else
  nao "pg-sales-dev nao esta de pe — o card do CRM nao pode ter derrubado o vizinho"
fi

if [ -f "$YAML" ] && grep -qiE '(passwd|password|senha|token|secret)[[:space:]]*[:=][[:space:]]*[^[:space:]#]' "$YAML"; then
  nao "a declaracao do funil carrega valor de credencial (segredo nao vai para o artefato)"
else
  ok "declaracao do funil sem valor de credencial"
fi

echo "---"
TOTAL="$(wc -l < "$ITENS" | tr -d ' ')"
FALHAS="$(grep -c '^FALHOU' "$ITENS" || true)"
if [ "$FALHAS" = "0" ]; then
  echo "RESULTADO: CRM_DEV_OK ($TOTAL itens, 0 falhas)"
  exit 0
else
  echo "RESULTADO: CRM_DEV_FALHOU ($TOTAL itens, $FALHAS falhas)"
  exit 1
fi
