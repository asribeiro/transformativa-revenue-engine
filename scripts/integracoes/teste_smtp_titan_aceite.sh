#!/usr/bin/env bash
# teste_smtp_titan_aceite.sh [--prova-de-dente] [--manter] [--raiz <dir>]
#
# ACEITE do card TRE-W6-E01-T01 — Titan SMTP (configuracao validada + primitivo de envio guardado).
#
# Roda inteiro OFFLINE, num sink SMTP descartavel que este proprio roteiro sobe em 127.0.0.1
# (scripts/integracoes/sink-smtp-dev.py, TLS proprio gerado na hora com openssl). Nenhuma credencial
# Titan e usada, nenhum host externo e contatado e nada toca producao (ADR-005): o papel dev-harness
# nao tem TRE_TITAN_* (hermes/policies/dev-harness.yaml) e nao envia e-mail em nome da Transformativa.
#
# O que este aceite mede:
#   1. sinal de vida — um sink em TLS implicito (2465) e um em STARTTLS (2587);
#   2. configuracao contra o sink — planejar (senha mascarada) e conferir;
#   3. conexao de verdade — EHLO + TLS + AUTH + NOOP nos dois modos de TLS (sem enviar mensagem);
#   4. entrega — dry-run NAO entrega; --confirmo entrega UMA mensagem, conferida na captura do sink;
#   5. guardas — host real em dev, prod, destino fora do dominio de dev, incoerencia porta x TLS,
#      porta 25: cada uma RECUSA e a caixa do sink fica intacta;
#   6. idempotencia — replay da mesma chave nao duplica; chave nova entrega;
#   7. segredo — o valor da senha nao aparece em saida, relatorio, trilha nem na captura;
#   8. desfazer — dry-run ate --confirmo, com a auditoria original preservada;
#   9. escopo — o repositorio sai identico, todos os hosts usados foram 127.0.0.1, e o veredito.
#
# --prova-de-dente: muta COPIA do modulo (scripts/integracoes/mutar_smtp_titan.py) e exige que cada
#   mutacao reprove O ITEM ESPERADO — nao basta "o aceite falhou". No fim, o controle: a suite offline
#   roda de novo no modulo INTACTO e continua verde (se o ambiente estivesse quebrado, o dente nao
#   seria prova de nada).
#
# Variaveis: TRE_W6_RAIZ, TRE_W6_SMTP_PY, TRE_W6_TRABALHO, TRE_W6_PORTA_TLS, TRE_W6_PORTA_STARTTLS.
# Exit: 0 = ACEITE_SMTP_TITAN_001_OK · 1 = ACEITE_SMTP_TITAN_001_FALHOU · 2 = uso · 3 = NAO_TESTAVEL.
set -uo pipefail

RAIZ="${TRE_W6_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
MODULO="${TRE_W6_SMTP_PY:-$RAIZ/hermes/integracoes/titan/smtp_titan.py}"
SINK="$RAIZ/scripts/integracoes/sink-smtp-dev.py"
SUITE="$RAIZ/scripts/integracoes/verificar_smtp_titan.py"
MUTADOR="$RAIZ/scripts/integracoes/mutar_smtp_titan.py"
TRABALHO="${TRE_W6_TRABALHO:-/tmp/smtp-titan-aceite-$$}"
PORTA_TLS="${TRE_W6_PORTA_TLS:-2465}"
PORTA_STARTTLS="${TRE_W6_PORTA_STARTTLS:-2587}"
SENHA="sentinela-dev-9f3a-nao-e-segredo"
CA_PEM="$TRABALHO/ca/dev.pem"
CA_KEY="$TRABALHO/ca/dev.key"
CAIXA="$TRABALHO/caixa/mensagens.jsonl"
DENTE=0
MANTER=0
ITENS_OK=0
FALHAS=0
EXECUCOES="$TRABALHO/execucoes.txt"
GIT_ANTES=""

SAIDA=""
RC=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MODULO="$RAIZ/hermes/integracoes/titan/smtp_titan.py" ;;
    --raiz=*) RAIZ="${1#--raiz=}"; MODULO="$RAIZ/hermes/integracoes/titan/smtp_titan.py" ;;
    *) echo "uso: $0 [--prova-de-dente] [--manter] [--raiz <dir>]"; exit 2 ;;
  esac
  shift
done

chk() { # nome, ok(0/1), detalhe
  if [ "$2" -eq 0 ]; then
    echo "OK    $1"
    ITENS_OK=$((ITENS_OK+1))
  else
    echo "FALHOU $1${3:+  -> $3}"
    FALHAS=$((FALHAS+1))
  fi
}

contar() { # arquivo -> numero de linhas nao vazias (0 se nao existir)
  if [ -f "$1" ]; then
    local n
    n=$(grep -c . "$1" 2>/dev/null)
    echo "${n:-0}"
  else
    echo 0
  fi
}

# contem <texto> <agulha>: sem pipe (com `set -o pipefail`, `printf | grep -q` morre por SIGPIPE e
# devolve 141 — foi um defeito real deste roteiro, medido na primeira rodada: o item 3.3 reprovou
# com a saida CERTA na mao).
contem() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

limpar() {
  for pidfile in "$TRABALHO"/sink-*.pid; do
    [ -f "$pidfile" ] || continue
    kill "$(cat "$pidfile")" 2>/dev/null || true
  done
}

if [ "$MANTER" -eq 0 ]; then trap limpar EXIT; fi

subir_sink() { # nome, porta, modo
  local nome="$1" porta="$2" modo="$3"
  python3 "$SINK" --porta "$porta" --modo "$modo" --cert "$CA_PEM" --chave "$CA_KEY" \
    --captura "$CAIXA" --pronto "$TRABALHO/pronto-$nome" --pidfile "$TRABALHO/sink-$nome.pid" \
    >"$TRABALHO/sink-$nome.log" 2>&1 &
  local i=0
  while [ $i -lt 60 ]; do
    [ -f "$TRABALHO/pronto-$nome" ] && return 0
    sleep 0.2
    i=$((i+1))
  done
  return 1
}

# exec_modulo <args...>: roda o modulo com a configuracao de sink corrente (HOST/PORTA/SEG/TIMEOUT)
exec_modulo() {
  SAIDA="$(env TRE_TITAN_SMTP_HOST="$HOST" TRE_TITAN_SMTP_PORT="$PORTA" \
    TRE_TITAN_SMTP_SEGURANCA="$SEG" TRE_TITAN_USER=sink-dev TRE_TITAN_PASSWORD="$SENHA" \
    TRE_TITAN_FROM="$REMETENTE" TRE_TITAN_CA="$CA_PEM" TRE_TITAN_DOMINIO_DEV=dev.local \
    TRE_TITAN_TIMEOUT="$TIMEOUT" TRE_TITAN_APROVACAO_HUMANA="$APROVACAO" \
    TRE_TITAN_DESTINOS_PERMITIDOS="$DESTINOS" "${EXTRA[@]}" \
    python3 "$MODULO" "$@" 2>&1)"
  RC=$?
  printf '%s %s\n' "$HOST" "$RC" >>"$EXECUCOES"
}

HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; REMETENTE="no-reply@dev.local"
TIMEOUT=5; APROVACAO=""; DESTINOS=""; EXTRA=()

echo "== ACEITE SMTP TITAN (TRE-W6-E01-T01) =="
echo "modulo:   $MODULO"
echo "trabalho: $TRABALHO"
echo "sink:     $PORTA_TLS (implicit_tls) e $PORTA_STARTTLS (starttls)"

if ! command -v openssl >/dev/null 2>&1; then
  echo "NAO_TESTAVEL: openssl ausente — sem certificado proprio nao ha prova de TLS no sink"
  exit 3
fi
if [ ! -f "$MODULO" ] || [ ! -f "$SINK" ]; then
  echo "NAO_TESTAVEL: modulo ou sink ausente"
  exit 3
fi

mkdir -p "$TRABALHO/ca" "$TRABALHO/caixa" "$TRABALHO/mutado" "$TRABALHO/out"
GIT_ANTES="$(git -C "$RAIZ" status --porcelain 2>/dev/null | sha256sum | cut -d' ' -f1)"
: >"$EXECUCOES"
: >"$CAIXA"

openssl req -x509 -newkey rsa:2048 -nodes -keyout "$CA_KEY" -out "$CA_PEM" -days 2 \
  -subj "/CN=127.0.0.1" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost,DNS:sink-smtp-dev" >/dev/null 2>&1 \
  || { echo "NAO_TESTAVEL: nao consegui gerar o certificado do sink"; exit 3; }

echo "--- 1. sinal de vida dos sinks ---"
subir_sink tls "$PORTA_TLS" implicit_tls && RC_SINK1=0 || RC_SINK1=1
subir_sink starttls "$PORTA_STARTTLS" starttls && RC_SINK2=0 || RC_SINK2=1
chk "1.1 sink TLS implicito escutando em 127.0.0.1:$PORTA_TLS e sink STARTTLS em $PORTA_STARTTLS" \
  "$([ "$RC_SINK1" -eq 0 ] && [ "$RC_SINK2" -eq 0 ] && echo 0 || echo 1)" \
  "$(cat "$TRABALHO/pronto-tls" 2>/dev/null)"

echo "--- 2. configuracao contra o sink ---"
exec_modulo --planejar --relatorio "$TRABALHO/out/plano.json"
chk "2.1 --planejar mostra a configuracao com a senha mascarada e exit 0" \
  "$([ "$RC" -eq 0 ] && grep -q '"senha": "<oculta>"' "$TRABALHO/out/plano.json" \
     && ! grep -q "$SENHA" "$TRABALHO/out/plano.json" && echo 0 || echo 1)" "exit=$RC"
exec_modulo --conferir --relatorio "$TRABALHO/out/conferir.json"
chk "2.2 --conferir aprova completude, matriz e guardas (exit 0)" \
  "$([ "$RC" -eq 0 ] && grep -q 'CONFIGURACAO_OK' "$TRABALHO/out/conferir.json" && echo 0 || echo 1)" \
  "exit=$RC"

echo "--- 3. conexao real: EHLO + TLS + AUTH + NOOP (sem enviar) ---"
exec_modulo --provar --relatorio "$TRABALHO/out/prova-tls.json"
chk "3.1 --provar em TLS implicito: exit 0, EHLO 250, TLS negociado e AUTH 235" \
  "$([ "$RC" -eq 0 ] && grep -q '"ehlo": 250' "$TRABALHO/out/prova-tls.json" \
     && grep -q '"versao_tls": "TLSv1' "$TRABALHO/out/prova-tls.json" \
     && grep -q '"autenticacao": "235' "$TRABALHO/out/prova-tls.json" && echo 0 || echo 1)" "exit=$RC"
PORTA="$PORTA_STARTTLS"; SEG="starttls"
exec_modulo --provar --relatorio "$TRABALHO/out/prova-starttls.json"
chk "3.2 --provar em STARTTLS: exit 0 e TLS negociado depois do STARTTLS" \
  "$([ "$RC" -eq 0 ] && grep -q '"versao_tls": "TLSv1' "$TRABALHO/out/prova-starttls.json" \
     && grep -q '"tls": "starttls"' "$TRABALHO/out/prova-starttls.json" && echo 0 || echo 1)" "exit=$RC"
SEG="nenhuma"; PORTA="$PORTA_TLS"
exec_modulo --provar --relatorio "$TRABALHO/out/prova-plain.json"
chk "3.3 contra o sink TLS, o caminho sem TLS FALHA (prova que os itens 3.1/3.2 negociaram TLS de verdade)" \
  "$([ "$RC" -ne 0 ] && contem "$SAIDA" FALHOU && echo 0 || echo 1)" "exit=$RC"
PORTA="$PORTA_TLS"; SEG="implicit_tls"

echo "--- 4. entrega ---"
ANTES=$(contar "$CAIXA")
exec_modulo --enviar --para caixa@dev.local --assunto "prova-aceite" --corpo "linha um do aceite" \
  --chave-idempotencia "aceite:1" --registro "$TRABALHO/out/trilha.jsonl" \
  --relatorio "$TRABALHO/out/dry.json"
DEPOIS=$(contar "$CAIXA")
chk "4.1 --enviar sem --confirmo e DRY_RUN (exit 0) e NAO entrega (caixa $ANTES -> $DEPOIS)" \
  "$([ "$RC" -eq 0 ] && [ "$ANTES" -eq "$DEPOIS" ] && echo 0 || echo 1)" "exit=$RC"
exec_modulo --enviar --para caixa@dev.local --assunto "prova-aceite" --corpo "linha um do aceite" \
  --chave-idempotencia "aceite:1" --confirmo --registro "$TRABALHO/out/trilha.jsonl" \
  --relatorio "$TRABALHO/out/envio.json"
DEPOIS=$(contar "$CAIXA")
chk "4.2 --enviar com --confirmo entrega UMA mensagem (caixa $ANTES -> $DEPOIS)" \
  "$([ "$RC" -eq 0 ] && [ "$DEPOIS" -eq $((ANTES+1)) ] && echo 0 || echo 1)" "exit=$RC"
ULTIMA="$(tail -1 "$CAIXA")"
chk "4.3 a mensagem entregue tem remetente, destino, assunto, corpo, marcador do card e usuario autenticado" \
  "$(contem "$ULTIMA" '"mail_from": "no-reply@dev.local"' \
     && contem "$ULTIMA" '"rcpt_to": ["caixa@dev.local"]' \
     && contem "$ULTIMA" '"assunto": "prova-aceite"' \
     && contem "$ULTIMA" 'linha um do aceite' \
     && contem "$ULTIMA" 'X-TRE-Card: TRE-W6-E01-T01' \
     && contem "$ULTIMA" '"autenticado_como": "sink-dev"' && echo 0 || echo 1)"

echo "--- 5. guardas ---"
ANTES=$(contar "$CAIXA")
HOST="192.0.2.1"; PORTA="465"; SEG="implicit_tls"; TIMEOUT=2
exec_modulo --conferir --relatorio "$TRABALHO/out/host-real.json"
chk "5.1 dev com host real RECUSA (HOST_NAO_E_DEV, exit 3) e nada foi entregue" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA" HOST_NAO_E_DEV \
     && [ "$(contar "$CAIXA")" -eq "$ANTES" ] && echo 0 || echo 1)" "exit=$RC"
exec_modulo --conferir --ambiente prod --relatorio "$TRABALHO/out/prod.json"
chk "5.2 --ambiente prod RECUSA por desenho (exit 4, ADR-005)" \
  "$([ "$RC" -eq 4 ] && contem "$SAIDA" PRODUCAO_NAO_E_DESTE_CARD && echo 0 || echo 1)" \
  "exit=$RC"
HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; REMETENTE="no-reply@dev.local"
exec_modulo --enviar --para alguem@exemplo-corporativo.com.br --assunto x --corpo y \
  --chave-idempotencia "aceite:destino" --confirmo --relatorio "$TRABALHO/out/destino.json"
chk "5.3 destino fora do dominio de dev RECUSA (DESTINO_NAO_PERMITIDO) e caixa intacta" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA" DESTINO_NAO_PERMITIDO \
     && [ "$(contar "$CAIXA")" -eq "$ANTES" ] && echo 0 || echo 1)" "exit=$RC"
HOST="192.0.2.1"; PORTA="465"; SEG="starttls"; APROVACAO="APROV-TESTE-001"; TIMEOUT=2
exec_modulo --conferir --ambiente homolog --relatorio "$TRABALHO/out/incoerente.json"
chk "5.4 porta 465 com starttls RECUSA (CONFIG_INCOERENTE, exit 3)" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA" CONFIG_INCOERENTE && echo 0 || echo 1)" "exit=$RC"
PORTA="25"; SEG="starttls"
exec_modulo --conferir --ambiente homolog --relatorio "$TRABALHO/out/porta25.json"
chk "5.5 porta 25 RECUSA (PORTA_NAO_AUTORIZADA, exit 3)" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA" PORTA_NAO_AUTORIZADA && echo 0 || echo 1)" \
  "exit=$RC"

echo "--- 6. idempotencia ---"
HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; APROVACAO=""; TIMEOUT=5
ANTES=$(contar "$CAIXA")
exec_modulo --enviar --para caixa@dev.local --assunto "prova-aceite" --corpo "linha um do aceite" \
  --chave-idempotencia "aceite:1" --confirmo --registro "$TRABALHO/out/trilha.jsonl" \
  --relatorio "$TRABALHO/out/replay.json"
chk "6.1 replay da MESMA chave responde JA_ENVIADO e nao duplica (caixa $ANTES -> $(contar "$CAIXA"))" \
  "$([ "$RC" -eq 0 ] && contem "$SAIDA" JA_ENVIADO \
     && [ "$(contar "$CAIXA")" -eq "$ANTES" ] && echo 0 || echo 1)" "exit=$RC"
exec_modulo --enviar --para caixa@dev.local --assunto "prova-aceite 2" --corpo "linha dois do aceite" \
  --chave-idempotencia "aceite:2" --confirmo --registro "$TRABALHO/out/trilha.jsonl" \
  --relatorio "$TRABALHO/out/envio2.json"
chk "6.2 chave NOVA entrega (caixa $ANTES -> $(contar "$CAIXA"))" \
  "$([ "$RC" -eq 0 ] && [ "$(contar "$CAIXA")" -eq $((ANTES+1)) ] && echo 0 || echo 1)" "exit=$RC"

echo "--- 7. segredo ---"
OCORRENCIAS=$(grep -rl "$SENHA" "$TRABALHO" 2>/dev/null | grep -v "hosts-usados" | wc -l)
chk "7.1 o valor da senha nao aparece em nenhum arquivo do trabalho (0 = $OCORRENCIAS)" \
  "$([ "$OCORRENCIAS" -eq 0 ] && echo 0 || echo 1)"
chk "7.2 a captura do sink guarda o USUARIO autenticado e nao a senha" \
  "$(grep -q '"autenticado_como": "sink-dev"' "$CAIXA" && ! grep -q "$SENHA" "$CAIXA" && echo 0 || echo 1)"

echo "--- 8. desfazer ---"
exec_modulo --desfazer "aceite:1" --registro "$TRABALHO/out/trilha.jsonl" \
  --relatorio "$TRABALHO/out/desfazer-dry.json"
chk "8.1 --desfazer sem --confirmo e dry-run (exit 0, trilha sem DESFEITO ainda)" \
  "$([ "$RC" -eq 0 ] && contem "$SAIDA" DRY_RUN \
     && ! grep -q '"DESFEITO"' "$TRABALHO/out/trilha.jsonl" && echo 0 || echo 1)" "exit=$RC"
exec_modulo --desfazer "aceite:1" --confirmo --registro "$TRABALHO/out/trilha.jsonl" \
  --relatorio "$TRABALHO/out/desfazer.json"
chk "8.2 --desfazer com --confirmo marca DESFEITO e preserva o ENVIADO original na trilha" \
  "$([ "$RC" -eq 0 ] && grep -q '"DESFEITO"' "$TRABALHO/out/trilha.jsonl" \
     && grep -q '"resultado": "ENVIADO"' "$TRABALHO/out/trilha.jsonl" && echo 0 || echo 1)" "exit=$RC"

echo "--- 9. escopo ---"
GIT_DEPOIS="$(git -C "$RAIZ" status --porcelain 2>/dev/null | sha256sum | cut -d' ' -f1)"
chk "9.1 o repositorio sai identico ao que entrou (nenhuma escrita do aceite no repo)" \
  "$([ "$GIT_ANTES" = "$GIT_DEPOIS" ] && echo 0 || echo 1)"
# Fora do loopback, a bateria usa apenas hosts TEST-NET (192.0.2.1, nao roteavel) e SEMPRE em caso de
# recusa medida: se o modulo tivesse tentado conectar, o exit seria 1 (FALHOU), nunca 3/4.
FORA=$(grep -v '^127.0.0.1 ' "$EXECUCOES" | grep -v ' [34]$' | grep -c . 2>/dev/null)
chk "9.2 todo caso com host nao-loopback saiu em RECUSA medida, exit 3/4 (nada de conexao para fora)" \
  "$([ "${FORA:-0}" -eq 0 ] && echo 0 || echo 1)" "${FORA:-0} caso(s) suspeito(s)"
chk "9.3 a entrega foi capturada sob TLS (modo e versao registrados na captura do sink)" \
  "$(contem "$ULTIMA" '"modo": "implicit_tls"' \
     && contem "$ULTIMA" '"versao_tls": "TLSv1' && echo 0 || echo 1)"

if [ "$DENTE" -eq 1 ]; then
  echo "--- 10. prova de dente (cada mutacao tem de reprovar O ITEM ESPERADO) ---"
  ANTES=$(contar "$CAIXA")
  for mutacao in sem-guarda-de-host sem-matriz-porta-tls ignora-confirmo senha-sem-mascara sem-idempotencia; do
    ALVO="$TRABALHO/mutado/$mutacao.py"
    if ! python3 "$MUTADOR" --modulo "$MODULO" --destino "$ALVO" --mutacao "$mutacao" \
        >"$TRABALHO/out/mut-$mutacao.txt" 2>&1; then
      chk "10 dente $mutacao: mutacao aplicada" 1 "$(cat "$TRABALHO/out/mut-$mutacao.txt")"
      continue
    fi
    MUT_MODULO="$ALVO"
    caso=""
    HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; TIMEOUT=5; APROVACAO=""
    case "$mutacao" in
      sem-guarda-de-host)
        HOST="192.0.2.1"; PORTA="465"; TIMEOUT=2
        MODULO="$MUT_MODULO"; exec_modulo --conferir --relatorio "$TRABALHO/out/m-h.json"
        caso="$([ "$RC" -ne 3 ] && echo 0 || echo 1)"; DETALHE="exit mutado=$RC (esperado != 3)"
        ;;
      sem-matriz-porta-tls)
        HOST="192.0.2.1"; PORTA="465"; SEG="starttls"; TIMEOUT=2; APROVACAO="APROV-TESTE-001"
        MODULO="$MUT_MODULO"
        exec_modulo --conferir --ambiente homolog --relatorio "$TRABALHO/out/m-m.json"
        caso="$([ "$RC" -ne 3 ] && echo 0 || echo 1)"; DETALHE="exit mutado=$RC (esperado != 3)"
        ;;
      ignora-confirmo)
        M_ANTES=$(contar "$CAIXA")
        MODULO="$MUT_MODULO"
        exec_modulo --enviar --para caixa@dev.local --assunto "dente-confirmo" --corpo "d" \
          --chave-idempotencia "dente:confirmo" --registro "$TRABALHO/out/trilha-dente.jsonl" \
          --relatorio "$TRABALHO/out/m-c.json"
        caso="$([ "$(contar "$CAIXA")" -gt "$M_ANTES" ] && echo 0 || echo 1)"
        DETALHE="caixa $M_ANTES -> $(contar "$CAIXA") (esperado > antes)"
        ;;
      senha-sem-mascara)
        MODULO="$MUT_MODULO"; exec_modulo --planejar --relatorio "$TRABALHO/out/m-s.json"
        caso="$(printf '%s' "$SAIDA" | grep -q "$SENHA" && echo 0 || echo 1)"
        DETALHE="senha visivel na saida mutada?"
        ;;
      sem-idempotencia)
        echo '{"evento": "ENVIAR", "resultado": "ENVIADO", "chave_idempotencia": "dente:idem", "quando": "2026-10-01T00:00:00Z"}' \
          >"$TRABALHO/out/trilha-idem.jsonl"
        M_ANTES=$(contar "$CAIXA")
        MODULO="$MUT_MODULO"
        exec_modulo --enviar --para caixa@dev.local --assunto "dente-idem" --corpo "d" \
          --chave-idempotencia "dente:idem" --confirmo --registro "$TRABALHO/out/trilha-idem.jsonl" \
          --relatorio "$TRABALHO/out/m-i.json"
        caso="$([ "$(contar "$CAIXA")" -gt "$M_ANTES" ] && echo 0 || echo 1)"
        DETALHE="caixa $M_ANTES -> $(contar "$CAIXA") (esperado > antes)"
        ;;
    esac
    chk "10 dente $mutacao: $(cat "$TRABALHO/out/mut-$mutacao.txt")" "$caso" "$DETALHE"
    MODULO="${TRE_W6_SMTP_PY:-$RAIZ/hermes/integracoes/titan/smtp_titan.py}"
  done

  echo "--- 11. controle: o modulo INTACTO continua verde (o dente nao e verde falso) ---"
  python3 "$SUITE" --raiz "$RAIZ" >"$TRABALHO/out/suite-controle.out" 2>&1
  RC_SUITE=$?
  chk "11.1 a suite offline roda verde no modulo intacto apos as mutacoes" \
    "$([ "$RC_SUITE" -eq 0 ] && echo 0 || echo 1)" "$(tail -1 "$TRABALHO/out/suite-controle.out")"
fi

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: ACEITE_SMTP_TITAN_001_OK ($ITENS_OK itens, 0 falhas)"
  [ "$MANTER" -eq 1 ] && echo "(trabalho mantido em $TRABALHO)"
  exit 0
fi
echo "RESULTADO: ACEITE_SMTP_TITAN_001_FALHOU ($ITENS_OK itens OK, $FALHAS falha(s))"
echo "(trabalho mantido em $TRABALHO)"
exit 1
