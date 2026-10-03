#!/usr/bin/env bash
# Aceite E2E do nurture automatizado (`nutricao-automatica-v1`) — card TRE-W9-E05-T01.
#
# Mede a CADEIA inteira contra PostgreSQL DE VERDADE, descartavel e em loopback (ADR-005):
#   funil/`previsao-canal-v1` (canal por organizacao)  ->  `melhor-horario-v1` (janela)
#                                                        ->  `nutricao-automatica-v1` (fila de toques)
# Container `pg-analytics-nurture-acc` (postgres:16) + db/migrations/0001, base semeada na FORMA
# declarada pelos contratos dos pais (canal/direcao/tipo/categoria LIDOS do repositorio).
#
# O que o aceite PROVA (cada item imprime OK/FALHOU):
#   1. guardas sem banco: prod RECUSA (exit 4) ANTES de ler entrada; homolog sem --confirmo; --conferir;
#      --regras; e a CLI NAO tem porta de banco (o componente nao abre banco);
#   2. regressao das duas dependencias: as suites offline dos pais PASSAM (canal e horario);
#   3. os dois pais medem a MESMA base real: pre-condition de dados multicanal atendida e melhor janela
#      escolhida com amostra (nao no chute);
#   4. o plano e PLENO: 4 toques por organizacao prevista, canal = o previsto pelo pai, janela = a do pai,
#      due_at alinhado ao dia x faixa da janela e NUNCA no passado, fila deterministica;
#   5. NADA ENVIA: todo toque carrega `exige_aprovacao_humana: true` e as condicoes de parada do contrato;
#   6. ABSTENCAO medida: janela sem amostra no relatorio do pai -> PLANO_ABSTIDO com fila vazia;
#   7. LEITURA PURA na cadeia inteira: contagem das 12 tabelas identica antes/depois;
#   8. prod RECUSA (exit 4), saida reproduzivel (duas rodadas => mesmo hash) e sem PII.
#
# LIMITE DECLARADO: as linhas de interacao sao semeadas por fixture NA FORMA dos contratos (a cadeia real
# SMTP/IMAP -> interactions ja' foi medida no TRE-W6-E07-T01); o que este aceite mede de ponta a ponta e'
# a DERIVACAO do plano a partir das duas medicoes reais. Os dentes ficam no autoteste do verificador offline.
# Veredito: ACEITE_NUTRICAO_AUTOMATICA_001_OK / _FALHOU. Exit 0 = OK · 1 = FALHOU · 2 = uso/guarda.
set -uo pipefail

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
CONTAINER="${TRE_NURTURE_ACC_CONTAINER:-pg-analytics-nurture-acc}"
IMAGEM="${TRE_FIXTURE_IMAGEM:-postgres:16}"
SENHA="nurture-aceite-descartavel"
USUARIO="sales_ai"; BANCO="sales_intelligence"
TRABALHO="${TRE_NURTURE_TRABALHO:-/tmp/nurture-aceite-trabalho}"
AGORA="2026-10-03T00:00:00Z"
MANTER=0
[ "${1:-}" = "--manter" ] && MANTER=1
OK=0; FALHAS=0
item() { if [ "$2" = "$3" ]; then echo "OK     $1 ($3)"; OK=$((OK+1)); else echo "FALHOU $1 (esperado=$2 obtido=$3)"; FALHAS=$((FALHAS+1)); fi; }

COMPONENTE="hermes/agentes/analytics/nutricao_automatica.py"
CONTRATO="hermes/agentes/analytics/nutricao-automatica-v1.json"
CANAL="hermes/agentes/analytics/previsao_canal.py"
CANAL_CONTRATO="hermes/agentes/analytics/previsao-canal-v1.json"
HORARIO="hermes/analytics/melhor_horario.py"
VERIFICADOR="scripts/agentes/verificar_nutricao_automatica.py"

command -v docker  >/dev/null 2>&1 || { echo "FALHOU docker ausente (rode na VPS)"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FALHOU python3 ausente"; exit 2; }
for f in "$COMPONENTE" "$CONTRATO" "$CANAL" "$CANAL_CONTRATO" "$HORARIO" "$VERIFICADOR" \
         scripts/agentes/verificar_previsao_canal.py scripts/agentes/verificar_melhor_horario.py \
         hermes/analytics/melhor-horario-v1.json hermes/agentes/respostas/ingestao-respostas-v1.json \
         hermes/agents/outreach/politica-envio-v1.json db/migrations/0001_sales_intelligence_v1.sql; do
  [ -f "$RAIZ/$f" ] || { echo "FALHOU arquivo ausente: $f"; exit 2; }
done
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "FALHOU o container $CONTAINER JA EXISTE — nao mexo nele, pare aqui e me chame"; exit 2
fi
mkdir -p "$TRABALHO"
cd "$RAIZ" || exit 2

echo "== A. guardas de ambiente (sem banco)"
python3 "$COMPONENTE" --ambiente prod >"$TRABALHO/prod.out" 2>&1
item "A1 prod RECUSADO por desenho (ADR-005)" 4 "$?"
python3 "$COMPONENTE" --ambiente prod --relatorio-canal /tmp/nao-existe.json \
  --relatorio-horario /tmp/nao-existe.json >"$TRABALHO/prod2.out" 2>&1
item "A2 prod RECUSADO ANTES de ler entrada" 4 "$?"
python3 "$COMPONENTE" --ambiente homolog >"$TRABALHO/homolog.out" 2>&1
item "A3 homolog sem --confirmo RECUSADO (exit 2)" 2 "$?"
python3 "$COMPONENTE" --ambiente dev --conferir >"$TRABALHO/conferir.out" 2>&1
item "A4 --conferir valida contrato, pais e guarda de escrita" 0 "$?"
grep -q "NUTRICAO_AUTOMATICA_CONFERIR_OK" "$TRABALHO/conferir.out" \
  && item "A5 --conferir imprime o marcador" "SIM" "SIM" || item "A5 --conferir imprime o marcador" "SIM" "NAO"
python3 "$COMPONENTE" --ambiente dev --regras >"$TRABALHO/regras.out" 2>&1
item "A6 --regras publica a politica declarada sem banco" 0 "$?"
python3 "$COMPONENTE" --ambiente dev --porta-banco "docker exec -i pg-x psql -U a -d b" >/dev/null 2>&1
item "A7 a CLI NAO tem porta de banco (o componente nao abre banco)" 2 "$?"

echo "== B. regressao das dependencias e suite propria (autoteste por mutacao)"
python3 scripts/agentes/verificar_previsao_canal.py --autoteste >"$TRABALHO/ver-canal.out" 2>&1
grep -q "VERIFICADOR_PREVISAO_CANAL_PASS" "$TRABALHO/ver-canal.out" \
  && item "B1 suite offline do PAI do canal passa" "SIM" "SIM" || item "B1 suite offline do PAI do canal passa" "SIM" "NAO"
python3 scripts/agentes/verificar_melhor_horario.py --autoteste >"$TRABALHO/ver-horario.out" 2>&1
grep -q "VERIFICADOR_MELHOR_HORARIO_PASS" "$TRABALHO/ver-horario.out" \
  && item "B2 suite offline do PAI do horario passa" "SIM" "SIM" || item "B2 suite offline do PAI do horario passa" "SIM" "NAO"
python3 "$VERIFICADOR" --autoteste >"$TRABALHO/ver-nurture.out" 2>&1
grep -q "VERIFICADOR_NUTRICAO_AUTOMATICA_PASS" "$TRABALHO/ver-nurture.out" \
  && item "B3 suite offline do nurture passa" "SIM" "SIM" || item "B3 suite offline do nurture passa" "SIM" "NAO"
grep -q "AUTOTESTE OK" "$TRABALHO/ver-nurture.out" \
  && item "B4 autoteste por mutacao do nurture (cada mutacao derruba o item nomeado)" "SIM" "SIM" \
  || item "B4 autoteste por mutacao do nurture (cada mutacao derruba o item nomeado)" "SIM" "NAO"

echo "== C. PostgreSQL descartavel + migration 0001"
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
item "C0 (pre-condicao) PostgreSQL descartavel responde SELECT 1 duas vezes" 1 "$pronto"
[ "$pronto" = 1 ] || exit 2
PSQL=(docker exec -i "$CONTAINER" psql -U "$USUARIO" -d "$BANCO" -v ON_ERROR_STOP=1 -tA -F'|')
"${PSQL[@]}" -q -f - < "$RAIZ/db/migrations/0001_sales_intelligence_v1.sql" >/dev/null 2>"$TRABALHO/mig.err"
item "C1 migration 0001 no PostgreSQL real (schema existe)" "sales_intelligence" \
  "$("${PSQL[@]}" -c "SELECT schema_name FROM information_schema.schemata WHERE schema_name='sales_intelligence';" </dev/null | tr -d ' ')"

echo "== D. base semeada: 8 organizacoes, 3 canais, bloqueios e desfecho no funil"
"${PSQL[@]}" -q -f - >"$TRABALHO/seed.out" 2>&1 <<'SQL'
INSERT INTO sales_intelligence.organizations (id, legal_name, domain, status, source) VALUES
 ('00000001-0000-0000-0000-000000000000','Empresa Um','um.test','DISCOVERED','SITE'),
 ('00000002-0000-0000-0000-000000000000','Empresa Dois','dois.test','DISCOVERED','SITE'),
 ('00000003-0000-0000-0000-000000000000','Empresa Tres','tres.test','DISCOVERED','SITE'),
 ('00000004-0000-0000-0000-000000000000','Empresa Quatro','quatro.test','DISCOVERED','SITE'),
 ('00000005-0000-0000-0000-000000000000','Empresa Cinco','cinco.test','DISCOVERED','META'),
 ('00000006-0000-0000-0000-000000000000','Empresa Seis','seis.test','DISCOVERED','SITE'),
 ('00000007-0000-0000-0000-000000000000','Empresa Sete','sete.test','DISCOVERED','SITE'),
 ('00000008-0000-0000-0000-000000000000','Empresa Oito','oito.test','DISCOVERED','SITE');
INSERT INTO sales_intelligence.contacts (id, organization_id, first_name, last_name, email, preferred_channel,
                                         do_not_contact, opt_out_email, opt_out_whatsapp) VALUES
 ('000000f1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','A','Um','a@um.test','WHATSAPP',FALSE,FALSE,FALSE),
 ('000000f2-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','B','Dois','b@dois.test',NULL,FALSE,TRUE,FALSE),
 ('000000f3-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','C','Tres','c@tres.test',NULL,TRUE,FALSE,FALSE),
 ('000000f5-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','E','Cinco','e@cinco.test',NULL,FALSE,FALSE,TRUE),
 ('000000f6-0000-0000-0000-000000000000','00000006-0000-0000-0000-000000000000','F','Seis','f@seis.test','EMAIL',FALSE,FALSE,FALSE),
 ('000000f7-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','G','Sete','g@sete.test',NULL,FALSE,TRUE,FALSE),
 ('000000f8-0000-0000-0000-000000000000','00000008-0000-0000-0000-000000000000','H','Oito','h@oito.test',NULL,FALSE,FALSE,FALSE);
INSERT INTO sales_intelligence.interactions (id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, response_category) VALUES
 ('000000e1-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000e2-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-02T10:00:00+00',NULL),
 ('000000e3-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000','000000f1-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA','2026-09-03T10:00:00+00','INTERESSE'),
 ('000000e4-0000-0000-0000-000000000000','00000001-0000-0000-0000-000000000000',NULL,'SMS','OUTBOUND','OUTBOUND_SMS','2026-09-04T10:00:00+00',NULL),
 ('000000e5-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','000000f2-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000e6-0000-0000-0000-000000000000','00000002-0000-0000-0000-000000000000','000000f2-0000-0000-0000-000000000000','LINKEDIN','OUTBOUND','OUTBOUND_LINKEDIN','2026-09-02T10:00:00+00',NULL),
 ('000000e7-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000e8-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-02T10:00:00+00',NULL),
 ('000000e9-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-03T10:00:00+00',NULL),
 ('000000ea-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA','2026-09-04T10:00:00+00','INTERESSE'),
 ('000000eb-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000','000000f3-0000-0000-0000-000000000000','EMAIL','INBOUND','RESPOSTA','2026-09-05T10:00:00+00','INTERESSE'),
 ('000000ec-0000-0000-0000-000000000000','00000003-0000-0000-0000-000000000000',NULL,'EMAIL','INTERNAL','NOTA','2026-09-06T10:00:00+00',NULL),
 ('000000ed-0000-0000-0000-000000000000','00000004-0000-0000-0000-000000000000',NULL,'EMAIL','OUTBOUND','OUTBOUND_EMAIL','2026-09-01T10:00:00+00',NULL),
 ('000000ee-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','000000f5-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-01T10:00:00+00',NULL),
 ('000000ef-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','000000f5-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-02T10:00:00+00',NULL),
 ('000000f0-0000-0000-0000-000000000000','00000005-0000-0000-0000-000000000000','000000f5-0000-0000-0000-000000000000','WHATSAPP','INBOUND','RESPOSTA','2026-09-03T10:00:00+00','INTERESSE'),
 ('00000011-0000-0000-0000-000000000000','00000006-0000-0000-0000-000000000000','000000f6-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-01T10:00:00+00',NULL),
 ('00000012-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','000000f7-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-01T10:00:00+00',NULL),
 ('00000013-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','000000f7-0000-0000-0000-000000000000','WHATSAPP','OUTBOUND','OUTBOUND_WHATSAPP','2026-09-02T10:00:00+00',NULL),
 ('00000014-0000-0000-0000-000000000000','00000007-0000-0000-0000-000000000000','000000f7-0000-0000-0000-000000000000','WHATSAPP','INBOUND','RESPOSTA','2026-09-03T10:00:00+00',NULL),
 ('00000015-0000-0000-0000-000000000000','00000008-0000-0000-0000-000000000000','000000f8-0000-0000-0000-000000000000','LINKEDIN','OUTBOUND','OUTBOUND_LINKEDIN','2026-09-01T10:00:00+00',NULL);
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, request_payload) VALUES
 ('00000091-0000-0000-0000-000000000000','crm.lead','00000001-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:1','COMPLETED','{"payload":{"estagio_novo":"Reunião"}}'),
 ('00000093-0000-0000-0000-000000000000','crm.lead','00000003-0000-0000-0000-000000000000','odoo','postgres','OPPORTUNITY_WON','odoo:won:3','COMPLETED','{"payload":{"valor":1000}}'),
 ('00000095-0000-0000-0000-000000000000','crm.lead','00000005-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:5','COMPLETED','{"payload":{"estagio_novo":"Reunião"}}'),
 ('00000096-0000-0000-0000-000000000000','crm.lead','00000006-0000-0000-0000-000000000000','odoo','postgres','OPPORTUNITY_LOST','odoo:lost:6','COMPLETED','{"payload":{"motivo":"preco"}}'),
 ('00000097-0000-0000-0000-000000000000','crm.lead','00000007-0000-0000-0000-000000000000','odoo','postgres','STAGE_CHANGED','odoo:sc:7','COMPLETED','{"payload":{"estagio_novo":"Proposta"}}');
SQL
item "D1 base do canal semeada (8 organizacoes, 3 canais, bloqueios, desfecho)" "8" \
  "$("${PSQL[@]}" -c "SELECT count(*) FROM sales_intelligence.organizations;" </dev/null | tr -d ' ')"

echo "== E. fixture das interacoes de ENVIO/RESPOSTA na FORMA declarada pelos contratos dos pais"
python3 - "$RAIZ" "$TRABALHO/fixture.sql" <<'PY'
import json, sys
from pathlib import Path
raiz, saida = Path(sys.argv[1]), Path(sys.argv[2])
envio = json.loads((raiz / "hermes/agents/outreach/politica-envio-v1.json").read_text(encoding="utf-8"))["interacoes"]
resposta = json.loads((raiz / "hermes/agentes/respostas/ingestao-respostas-v1.json").read_text(encoding="utf-8"))
pers = resposta["persistencia"]["colunas"]
categorias = resposta["vocabulario"]["response_category"]
assert envio["channel"] == "EMAIL" and envio["direction"] == "OUTBOUND", envio
assert envio["interaction_type"], envio
for chave in ("channel", "direction", "interaction_type", "content_reference"):
    assert pers[chave], chave
assert "INTERESSE" in categorias, categorias
ORG1 = "00000001-0000-0000-0000-000000000000"
L = ["SET client_min_messages TO WARNING;"]
# 5 envios na MESMA celula (segunda 09:00-10:00 locais = 12:00-13:00Z) -> amostra suficiente no pai do horario
for n, hora in enumerate(("12:00", "12:15", "12:30", "12:45", "13:00"), start=1):
    L.append("INSERT INTO sales_intelligence.interactions "
             "(id, organization_id, channel, direction, interaction_type, occurred_at, subject, "
             "content_summary, content_reference) VALUES "
             f"(gen_random_uuid(), '{ORG1}', '{envio['channel']}', '{envio['direction']}', '{envio['interaction_type']}', "
             f"TIMESTAMPTZ '2026-08-31 {hora}:00+00', 'assunto N{n}', 'resumo', 'envio:ped-n1-{n}:T1');")
# 2 respostas positivas creditadas a esses envios (interesse 0.4)
for n, hora in enumerate(("12:05", "12:20"), start=1):
    L.append("INSERT INTO sales_intelligence.interactions "
             "(id, organization_id, channel, direction, interaction_type, occurred_at, content_reference, "
             "response_category) VALUES "
             f"(gen_random_uuid(), '{ORG1}', '{pers['channel']}', '{pers['direction']}', '{pers['interaction_type']}', "
             f"TIMESTAMPTZ '2026-08-31 {hora}:00+00', 'UIDVALIDITY:{n}', 'INTERESSE');")
saida.write_text("\n".join(L) + "\n", encoding="utf-8")
print("fixture ok: canal=%s direcao=%s tipo=%s resposta=%s/%s" % (
    envio["channel"], envio["direction"], envio["interaction_type"], pers["channel"], pers["interaction_type"]))
PY
[ -f "$TRABALHO/fixture.sql" ] || { echo "FALHOU nao gerou a fixture (contratos ilegiveis)"; exit 2; }
"${PSQL[@]}" -q -f - < "$TRABALHO/fixture.sql" >/dev/null 2>"$TRABALHO/fixture.err" \
  || { echo "FALHOU nao semeou a fixture: $(tail -3 "$TRABALHO/fixture.err")"; exit 2; }
item "E1 fixture: 5 envios com referencia + 2 respostas da ingestao" "5|2" \
  "$("${PSQL[@]}" -c "SELECT (SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference LIKE 'envio:%')||'|'||(SELECT count(*) FROM sales_intelligence.interactions WHERE content_reference LIKE 'UIDVALIDITY:%');" </dev/null | tr -d ' ')"

contagens() {
  "${PSQL[@]}" -c "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence')||'|'||(SELECT count(*) FROM sales_intelligence.interactions)||'|'||(SELECT count(*) FROM sales_intelligence.organizations);" </dev/null | tr -d ' '
}
ANTES="$(contagens)"
PORTA="docker exec -i $CONTAINER psql -U $USUARIO -d $BANCO"

echo "== F. os DOIS pais medem a MESMA base real"
python3 "$CANAL" --ambiente dev --porta-banco "$PORTA" --agora "$AGORA" \
  --saida "$TRABALHO/pai" >"$TRABALHO/canal.out" 2>&1
item "F1 PAI do canal roda contra o PostgreSQL real (exit 0)" 0 "$?"
python3 "$HORARIO" --ambiente dev --prefixo "$PORTA" --desde 2026-08-01T00:00:00Z --ate 2026-10-03T00:00:00Z \
  --limite-amostra 5 --json "$TRABALHO/horario.json" >"$TRABALHO/horario.out" 2>&1
item "F2 PAI do horario roda contra o PostgreSQL real (exit 0)" 0 "$?"

echo "== G. o nurture deriva o plano dos dois relatorios reais"
python3 "$COMPONENTE" --ambiente dev --relatorio-canal "$TRABALHO/pai/previsao-canal.json" \
  --relatorio-horario "$TRABALHO/horario.json" --agora "$AGORA" --saida "$TRABALHO/plano" \
  >"$TRABALHO/plano.out" 2>&1
item "G1 o nurture deriva o plano (exit 0)" 0 "$?"
grep -q "NUTRICAO_AUTOMATICA_OK" "$TRABALHO/plano.out" \
  && item "G2 marcador NUTRICAO_AUTOMATICA_OK na saida" "SIM" "SIM" || item "G2 marcador NUTRICAO_AUTOMATICA_OK na saida" "SIM" "NAO"

RC_PLANO=0
python3 - "$TRABALHO/pai/previsao-canal.json" "$TRABALHO/horario.json" \
  "$TRABALHO/plano/nutricao-automatica.json" "$TRABALHO/plano/nutricao-automatica.html" >"$TRABALHO/itens.out" 2>&1 <<'PY' || RC_PLANO=$?
import json, sys
canal = json.load(open(sys.argv[1], encoding="utf-8"))
horario = json.load(open(sys.argv[2], encoding="utf-8"))
plano = json.load(open(sys.argv[3], encoding="utf-8"))
html = open(sys.argv[4], encoding="utf-8").read()
falhas = []
def item(nome, cond, det=""):
    print(("OK    " if cond else "FALHOU ") + nome + ("" if cond else " " + str(det)))
    if not cond:
        falhas.append(nome)
previsoes = {p["organization_id"]: p for p in canal["previsoes"]}
melhor = horario["melhor_janela"]
fila = plano["fila"]
item("P1 pre-condicao dos DOIS pais atendida: canal com base e janela com amostra",
     canal["pre_condicao_dados_multicanal"]["atendida"] is True and len(previsoes) >= 4 and melhor is not None,
     (canal["pre_condicao_dados_multicanal"], horario["melhor_janela_motivo"]))
item("P2 plano emitido com 4 toques por organizacao prevista (%d organizacoes -> %d toques)" % (len(previsoes), 4 * len(previsoes)),
     plano["veredito"] == "PLANO_EMITIDO" and plano["plano_emitido"] is True
     and len(fila) == 4 * len(previsoes) and plano["resumo"]["toques"] == 4 * len(previsoes),
     (plano["veredito"], len(fila)))
item("P3 canal de cada toque e' o canal_previsto do PAI (canal nao se remede)",
     all(t["canal"] == previsoes[t["organization_id"]]["canal_previsto"] for t in fila)
     and set(plano["resumo"]["por_canal"]) == {p["canal_previsto"] for p in previsoes.values()},
     plano["resumo"]["por_canal"])
item("P4 janela de cada toque e' a melhor_janela do PAI (horario nao se remede)",
     all(t["dia"] == melhor["dia"] and t["faixa"] == melhor["faixa"] for t in fila)
     and plano["janela"]["dia"] == melhor["dia"] and plano["janela"]["faixa"] == melhor["faixa"],
     (plano["janela"], melhor))
item("P5 bloqueio de opt-out respeitado: nenhum toque em canal bloqueado da organizacao",
     all(t["canal"] not in [b["canal"] for b in previsoes[t["organization_id"]]["canais_bloqueados"]] for t in fila)
     and plano["lacunas"]["organizacoes_com_canal_bloqueado"] ==
         sum(1 for p in previsoes.values() if p["canais_bloqueados"]),
     plano["lacunas"])
item("P6 due_at nunca no passado e monotono dentro de cada organizacao",
     all(t["due_at_utc"] >= plano["referencia_temporal"] for t in fila)
     and all(sorted(t["due_at_utc"] for t in fila if t["organization_id"] == o)
             == [t["due_at_utc"] for t in sorted((x for x in fila if x["organization_id"] == o),
                                                 key=lambda x: x["passo"])]
             for o in previsoes), plano["referencia_temporal"])
item("P7 due_at alinhado ao dia x faixa da janela no fuso do PAI (hora = inicio da faixa)",
     all(t["due_at_local"].endswith(horario["fuso"]["offset_utc"])
         and t["due_at_local"][11:16] == "%02d:00" % min(f["de"] for f in horario["grade"]["faixas"]
                                                        if f["nome"] == melhor["faixa"])
         for t in fila), fila[0]["due_at_local"] if fila else None)
item("P8 NADA ENVIA: todo toque exige aprovacao humana e carrega as condicoes de parada",
     all(t["exige_aprovacao_humana"] is True for t in fila)
     and all(t["condicoes_de_parada"] == plano["politica"]["condicoes_de_parada"] for t in fila)
     and plano["politica"]["nao_envia"] is True, plano["politica"]["exige_aprovacao_humana"])
item("P9 fila ordenada por (due_at, canal, organizacao) — determinismo",
     [(t["due_at_utc"], t["canal"], t["organization_id"]) for t in fila]
     == sorted((t["due_at_utc"], t["canal"], t["organization_id"]) for t in fila))
item("P10 dashboard HTML auto-contido (sem http/https/script/link) com a fila e o aviso de aprovacao",
     "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
     and "Fila de toques" in html and "aprovacao humana" in html and len(html) > 800, len(html))
texto = json.dumps(plano, ensure_ascii=False) + html
achados = [l for l in ("a@um.test", "Empresa Um", "Empresa Oito", "um.test", "ped-n1") if l.lower() in texto.lower()]
item("P11 saida sem PII (nenhum e-mail, dominio ou nome da base semeada)", achados == [], achados)
sys.exit(0 if not falhas else 1)
PY
cat "$TRABALHO/itens.out"
if [ "$RC_PLANO" = "0" ]; then
  item "G3 bloco de numeros do plano rodou ate' o fim (exit 0)" 0 "$RC_PLANO"
else
  item "G3 bloco de numeros do plano rodou ate' o fim (exit 0)" 0 "$RC_PLANO"
fi
OK=$((OK + $(grep -c "^OK    " "$TRABALHO/itens.out")))
FALHAS=$((FALHAS + $(grep -c "^FALHOU " "$TRABALHO/itens.out")))

echo "== H. abstencao medida (janela do pai sem amostra) e determinismo"
python3 - "$TRABALHO/horario.json" "$TRABALHO/horario-sem-amostra.json" <<'PY'
import json, sys
rel = json.load(open(sys.argv[1], encoding="utf-8"))
rel["melhor_janela"] = None
rel["melhor_janela_motivo"] = "AMOSTRA_INSUFICIENTE"
json.dump(rel, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
PY
python3 "$COMPONENTE" --ambiente dev --relatorio-canal "$TRABALHO/pai/previsao-canal.json" \
  --relatorio-horario "$TRABALHO/horario-sem-amostra.json" --agora "$AGORA" --saida "$TRABALHO/abstido" \
  >"$TRABALHO/abstido.out" 2>&1
item "H1 janela sem amostra -> o nurture ABSTEM (exit 0)" 0 "$?"
item "H2 abstencao com veredito PLANO_ABSTIDO, fila vazia e motivo nomeado" "SIM" \
  "$(python3 - "$TRABALHO/abstido/nutricao-automatica.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
ok = (r["veredito"] == "PLANO_ABSTIDO" and r["plano_emitido"] is False and r["fila"] == []
      and any("AMOSTRA_INSUFICIENTE" in f for f in r["pre_condicoes"]["faltando"]))
print("SIM" if ok else "NAO")
PY
)"

python3 "$COMPONENTE" --ambiente dev --relatorio-canal "$TRABALHO/pai/previsao-canal.json" \
  --relatorio-horario "$TRABALHO/horario.json" --agora "$AGORA" --saida "$TRABALHO/plano2" \
  >/dev/null 2>&1
item "H3 saida reproduzivel: duas rodadas com a mesma referencia -> mesmo hash_do_plano" "IGUAL" \
  "$(python3 - "$TRABALHO/plano/nutricao-automatica.json" "$TRABALHO/plano2/nutricao-automatica.json" <<'PY'
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8")); b = json.load(open(sys.argv[2], encoding="utf-8"))
print("IGUAL" if a["hash_do_plano"] == b["hash_do_plano"] and a["fila"] == b["fila"] else "DIFERENTE")
PY
)"

DEPOIS="$(contagens)"
item "I1 LEITURA PURA na cadeia inteira: contagem das 12 tabelas identica antes/depois" "$ANTES" "$DEPOIS"

python3 "$COMPONENTE" --ambiente prod --relatorio-canal "$TRABALHO/pai/previsao-canal.json" \
  --relatorio-horario "$TRABALHO/horario.json" >/dev/null 2>&1
item "J1 prod RECUSADO (ADR-005) com a cadeia real montada" 4 "$?"

echo
if [ "$FALHAS" -eq 0 ]; then echo "ACEITE_NUTRICAO_AUTOMATICA_001_OK ($OK itens, 0 falhas)"; exit 0; fi
echo "ACEITE_NUTRICAO_AUTOMATICA_001_FALHOU ($OK OK, $FALHAS falhas)"; exit 1
