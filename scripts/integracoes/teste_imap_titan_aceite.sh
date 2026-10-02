#!/usr/bin/env bash
# teste_imap_titan_aceite.sh [--prova-de-dente] [--manter] [--raiz <dir>]
#
# ACEITE do card TRE-W6-E01-T02 — Titan IMAP (leitura validada e guardada da caixa).
#
# Roda inteiro OFFLINE, num sink IMAP descartavel que este proprio roteiro sobe em 127.0.0.1
# (scripts/integracoes/sink-imap-dev.py, TLS proprio gerado na hora com openssl). Nenhuma credencial
# Titan e usada, nenhum host externo e contatado e nada toca producao (ADR-005): o papel dev-harness
# nao tem TRE_TITAN_* (hermes/policies/dev-harness.yaml) e nao contata lead/cliente.
#
# O que este aceite mede:
#   1. sinal de vida — um sink em TLS implicito (2993) e um em STARTTLS (2143);
#   2. configuracao contra o sink — planejar (senha mascarada) e conferir;
#   3. conexao de verdade — TLS + LOGIN + CAPACIDADE + EXAMINE + NOOP nos dois modos (sem trazer corpo);
#   4. leitura — --listar traz os envelopes e NAO marca nada como lido;
#   5. ingesta — dry-run nao grava; --confirmo grava UMA copia por mensagem, com conteudo conferido;
#      replay responde JA_INGERIDO e NAO busca o corpo de novo;
#   6. INVARIANTE DE LEITURA medido no sink — todas as selecoes em EXAMINE, zero busca sem PEEK, zero
#      comando de escrita, nenhuma mensagem marcada \\Seen (as flags da caixa saem identicas);
#   7. guardas — host real em dev, prod, login corporativo, porta de outro protocolo, incoerencia
#      porta x TLS: cada uma RECUSA e a caixa do sink fica intacta;
#   8. segredo — o valor da senha nao aparece em saida, relatorio, trilha, mensagem gravada nem na
#      captura do sink (que guarda LOGIN <usuario> <senha-oculta>);
#   9. desfazer — dry-run ate --confirmo, com a auditoria original preservada;
#  10. escopo — o repositorio sai identico, todos os hosts contatados foram 127.0.0.1, e o veredito.
#
# --prova-de-dente: muta COPIA do modulo (scripts/integracoes/mutar_imap_titan.py) e exige que cada
#   mutacao reprove O ITEM ESPERADO — nao basta "o aceite falhou". No fim, o controle: a suite offline
#   roda de novo no modulo INTACTO e continua verde (se o ambiente estivesse quebrado, o dente nao
#   seria prova de nada).
#
# Variaveis: TRE_W6_RAIZ, TRE_W6_IMAP_PY, TRE_W6_TRABALHO, TRE_W6_PORTA_TLS, TRE_W6_PORTA_STARTTLS.
# Exit: 0 = ACEITE_IMAP_TITAN_001_OK · 1 = ACEITE_IMAP_TITAN_001_FALHOU · 2 = uso · 3 = NAO_TESTAVEL.
set -uo pipefail

RAIZ="${TRE_W6_RAIZ:-$(cd "$(dirname "$0")/../.." && pwd)}"
MODULO="${TRE_W6_IMAP_PY:-$RAIZ/hermes/integracoes/titan/imap_titan.py}"
SINK="$RAIZ/scripts/integracoes/sink-imap-dev.py"
SUITE="$RAIZ/scripts/integracoes/verificar_imap_titan.py"
MUTADOR="$RAIZ/scripts/integracoes/mutar_imap_titan.py"
TRABALHO="${TRE_W6_TRABALHO:-/tmp/imap-titan-aceite-$$}"
PORTA_TLS="${TRE_W6_PORTA_TLS:-2993}"
PORTA_STARTTLS="${TRE_W6_PORTA_STARTTLS:-2143}"
SENHA="sentinela-dev-9f3a-nao-e-segredo"
CA_PEM="$TRABALHO/ca/dev.pem"
CA_KEY="$TRABALHO/ca/dev.key"
CAP_TLS="$TRABALHO/tls/comandos.jsonl"
CAP_STARTTLS="$TRABALHO/starttls/comandos.jsonl"
SAIDA="$TRABALHO/saida"
TRILHA="$TRABALHO/trilha.jsonl"
DENTE=0
MANTER=0
ITENS_OK=0
FALHAS=0
EXECUCOES="$TRABALHO/execucoes.txt"
GIT_ANTES=""

SAIDA_CMD=""
RC=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prova-de-dente) DENTE=1 ;;
    --manter) MANTER=1 ;;
    --raiz) shift; RAIZ="${1:?--raiz exige caminho}"; MODULO="$RAIZ/hermes/integracoes/titan/imap_titan.py" ;;
    --raiz=*) RAIZ="${1#--raiz=}"; MODULO="$RAIZ/hermes/integracoes/titan/imap_titan.py" ;;
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

listar() { # diretorio -> numero de arquivos .json (0 se nao existir)
  if [ -d "$1" ]; then
    find "$1" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' '
  else
    echo 0
  fi
}

# contem <texto> <agulha>: sem pipe (com `set -o pipefail`, `printf | grep -q` morre por SIGPIPE e
# devolve 141 — defeito real medido no aceite irmao do SMTP).
contem() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# estado_do_sink <captura>: ultimo ESTADO_FINAL acumulado daquele sink
estado_do_sink() {
  grep '"evento": "ESTADO_FINAL"' "$1" 2>/dev/null | tail -1
}

limpar() {
  for pidfile in "$TRABALHO"/sink-*.pid; do
    [ -f "$pidfile" ] || continue
    kill "$(cat "$pidfile")" 2>/dev/null || true
  done
}

if [ "$MANTER" -eq 0 ]; then trap limpar EXIT; fi

subir_sink() { # nome, porta, modo, captura
  local nome="$1" porta="$2" modo="$3" captura="$4"
  python3 "$SINK" --porta "$porta" --modo "$modo" --cert "$CA_PEM" --chave "$CA_KEY" \
    --senha "$SENHA" --mensagens 3 --captura "$captura" \
    --pronto "$TRABALHO/pronto-$nome" --pidfile "$TRABALHO/sink-$nome.pid" \
    >"$TRABALHO/sink-$nome.log" 2>&1 &
  local i=0
  while [ $i -lt 60 ]; do
    [ -f "$TRABALHO/pronto-$nome" ] && return 0
    sleep 0.2
    i=$((i+1))
  done
  return 1
}

# exec_modulo <args...>: roda o modulo com a configuracao de sink corrente
exec_modulo() {
  SAIDA_CMD="$(env TRE_TITAN_IMAP_HOST="$HOST" TRE_TITAN_IMAP_PORT="$PORTA" \
    TRE_TITAN_IMAP_SEGURANCA="$SEG" TRE_TITAN_IMAP_CAIXA="$CAIXA" \
    TRE_TITAN_USER="$USUARIO" TRE_TITAN_PASSWORD="$SENHA" TRE_TITAN_CA="$CA_PEM" \
    TRE_TITAN_DOMINIO_DEV=dev.local TRE_TITAN_TIMEOUT="$TIMEOUT" \
    TRE_TITAN_CAIXAS_PERMITIDAS="$CAIXAS" TRE_TITAN_APROVACAO_HUMANA="$APROVACAO" \
    python3 "$MODULO" "$@" 2>&1)"
  RC=$?
  printf '%s %s\n' "$HOST" "$RC" >>"$EXECUCOES"
}

HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; CAIXA="INBOX"; CAIXAS=""
USUARIO="sink-dev@dev.local"; TIMEOUT=5; APROVACAO=""

echo "== ACEITE IMAP TITAN (TRE-W6-E01-T02) =="
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

mkdir -p "$TRABALHO/ca" "$TRABALHO/tls" "$TRABALHO/starttls" "$TRABALHO/mutado" "$TRABALHO/out" "$SAIDA"
GIT_ANTES="$(git -C "$RAIZ" status --porcelain 2>/dev/null | sha256sum | cut -d' ' -f1)"
: >"$EXECUCOES"

openssl req -x509 -newkey rsa:2048 -nodes -keyout "$CA_KEY" -out "$CA_PEM" -days 2 \
  -subj "/CN=127.0.0.1" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost,DNS:sink-imap-dev" >/dev/null 2>&1 \
  || { echo "NAO_TESTAVEL: nao consegui gerar o certificado do sink"; exit 3; }

echo "--- 1. sinal de vida dos sinks ---"
subir_sink tls "$PORTA_TLS" implicit_tls "$CAP_TLS" && RC_SINK1=0 || RC_SINK1=1
subir_sink starttls "$PORTA_STARTTLS" starttls "$CAP_STARTTLS" && RC_SINK2=0 || RC_SINK2=1
chk "1.1 sink TLS implicito escutando em 127.0.0.1:$PORTA_TLS e sink STARTTLS em $PORTA_STARTTLS" \
  "$([ "$RC_SINK1" -eq 0 ] && [ "$RC_SINK2" -eq 0 ] && echo 0 || echo 1)" \
  "$(cat "$TRABALHO/pronto-tls" 2>/dev/null)"

echo "--- 2. configuracao contra o sink ---"
exec_modulo --planejar --relatorio "$TRABALHO/out/plano.json"
chk "2.1 --planejar mostra a configuracao com a senha mascarada e exit 0" \
  "$([ "$RC" -eq 0 ] && grep -q '"senha": "<oculta>"' "$TRABALHO/out/plano.json" \
     && ! grep -q "$SENHA" "$TRABALHO/out/plano.json" && echo 0 || echo 1)" "exit=$RC"
exec_modulo --conferir --relatorio "$TRABALHO/out/conferir.json"
chk "2.2 --conferir aprova completude, matriz, guardas e invariante (exit 0)" \
  "$([ "$RC" -eq 0 ] && grep -q 'CONFIGURACAO_OK' "$TRABALHO/out/conferir.json" \
     && grep -q 'sem comando de escrita' "$TRABALHO/out/conferir.json" && echo 0 || echo 1)" "exit=$RC"

echo "--- 3. conexao real: TLS + LOGIN + EXAMINE + NOOP (sem trazer corpo) ---"
exec_modulo --provar --relatorio "$TRABALHO/out/prova-tls.json"
chk "3.1 --provar em TLS implicito: exit 0, saudacao, TLS negociado, LOGIN OK e EXAMINE (3 mensagens)" \
  "$([ "$RC" -eq 0 ] && grep -q '"versao_tls": "TLSv1' "$TRABALHO/out/prova-tls.json" \
     && grep -q '"autenticacao": "OK"' "$TRABALHO/out/prova-tls.json" \
     && grep -q '"modo_de_abertura": "EXAMINE' "$TRABALHO/out/prova-tls.json" \
     && grep -q '"mensagens_na_caixa": 3' "$TRABALHO/out/prova-tls.json" && echo 0 || echo 1)" "exit=$RC"
PORTA="$PORTA_STARTTLS"; SEG="starttls"
exec_modulo --provar --relatorio "$TRABALHO/out/prova-starttls.json"
chk "3.2 --provar em STARTTLS: exit 0, TLS negociado depois do STARTTLS e EXAMINE" \
  "$([ "$RC" -eq 0 ] && grep -q '"versao_tls": "TLSv1' "$TRABALHO/out/prova-starttls.json" \
     && grep -q '"tls": "starttls"' "$TRABALHO/out/prova-starttls.json" && echo 0 || echo 1)" "exit=$RC"
SEG="nenhuma"; PORTA="$PORTA_TLS"
exec_modulo --provar --relatorio "$TRABALHO/out/prova-plain.json"
chk "3.3 contra o sink TLS, o caminho sem TLS FALHA (prova que 3.1/3.2 negociaram TLS de verdade)" \
  "$([ "$RC" -ne 0 ] && contem "$SAIDA_CMD" FALHOU && echo 0 || echo 1)" "exit=$RC"
PORTA="$PORTA_TLS"; SEG="implicit_tls"

echo "--- 4. leitura (envelopes) sem marcar lido ---"
exec_modulo --listar --relatorio "$TRABALHO/out/ler.json"
LER="$(cat "$TRABALHO/out/ler.json" 2>/dev/null)"
chk "4.1 --listar traz os 3 envelopes com Message-ID, remetente e assunto" \
  "$([ "$RC" -eq 0 ] && contem "$LER" '<fixture-1@cliente-dev.local>' \
     && contem "$LER" 'lead1@cliente-dev.local' \
     && contem "$LER" 'Resposta do prospect #3' && echo 0 || echo 1)" "exit=$RC"
chk "4.2 --listar nao marca nada como lido (nenhum envelope com marcada_como_lida=true)" \
  "$([ "$RC" -eq 0 ] && ! contem "$LER" '"marcada_como_lida": true' && echo 0 || echo 1)" "exit=$RC"

echo "--- 5. ingesta (uma copia por mensagem, idempotente) ---"
exec_modulo --ingerir --saida "$SAIDA" --chave-idempotencia "aceite:1" --registro "$TRILHA" \
  --relatorio "$TRABALHO/out/dry.json"
chk "5.1 --ingerir sem --confirmo e DRY_RUN (exit 0) e nao grava mensagem nenhuma (0 arquivos)" \
  "$([ "$RC" -eq 0 ] && [ "$(listar "$SAIDA")" -eq 0 ] && echo 0 || echo 1)" \
  "exit=$RC arquivos=$(listar "$SAIDA")"
exec_modulo --ingerir --saida "$SAIDA" --chave-idempotencia "aceite:1" --confirmo \
  --registro "$TRILHA" --relatorio "$TRABALHO/out/ingesta.json"
CORPOS_APOS_1="$(estado_do_sink "$CAP_TLS" | grep -o '"total_corpos_buscados": [0-9]*' | tail -1)"
chk "5.2 --ingerir com --confirmo grava UMA copia por mensagem (3 arquivos) e 3 INGERIDO na trilha" \
  "$([ "$RC" -eq 0 ] && [ "$(listar "$SAIDA")" -eq 3 ] \
     && [ "$(grep -c '"resultado": "INGERIDO"' "$TRILHA" 2>/dev/null)" -eq 3 ] && echo 0 || echo 1)" \
  "exit=$RC arquivos=$(listar "$SAIDA")"
GRAVADA="$(cat "$SAIDA"/999-2.json 2>/dev/null)"
chk "5.3 a mensagem gravada tem Message-ID, remetente, assunto e corpo da fixture" \
  "$(contem "$GRAVADA" '<fixture-2@cliente-dev.local>' && contem "$GRAVADA" 'lead2@cliente-dev.local' \
     && contem "$GRAVADA" 'Resposta do prospect #2' && contem "$GRAVADA" 'FIXTURE-2' && echo 0 || echo 1)"
exec_modulo --ingerir --saida "$SAIDA" --chave-idempotencia "aceite:1" --confirmo \
  --registro "$TRILHA" --relatorio "$TRABALHO/out/replay.json"
CORPOS_APOS_2="$(estado_do_sink "$CAP_TLS" | grep -o '"total_corpos_buscados": [0-9]*' | tail -1)"
chk "5.4 replay da MESMA identidade responde JA_INGERIDO, nao grava de novo e NAO busca o corpo outra vez" \
  "$([ "$RC" -eq 0 ] && contem "$SAIDA_CMD" JA_INGERIDO && [ "$(listar "$SAIDA")" -eq 3 ] \
     && [ "$CORPOS_APOS_1" = "$CORPOS_APOS_2" ] && echo 0 || echo 1)" \
  "exit=$RC corpos: $CORPOS_APOS_1 -> $CORPOS_APOS_2"

echo "--- 6. invariante de leitura (medido no sink) ---"
ESTADO="$(estado_do_sink "$CAP_TLS")"
chk "6.1 TODAS as selecoes do sink foram EXAMINE (nenhum SELECT de escrita)" \
  "$(contem "$ESTADO" '"selecoes": ["EXAMINE INBOX"]' && echo 0 || echo 1)" \
  "$(contem "$ESTADO" 'EXAMINE INBOX' && echo "" || echo "sem selecao EXAMINE na captura")"
chk "6.2 zero busca sem PEEK no registro do sink (total_buscas_sem_peek = 0)" \
  "$(contem "$ESTADO" '"total_buscas_sem_peek": 0' && echo 0 || echo 1)"
chk "6.3 zero comando de escrita (total_comandos_de_escrita = [])" \
  "$(contem "$ESTADO" '"total_comandos_de_escrita": []' && echo 0 || echo 1)"
chk "6.4 nenhuma mensagem marcada como lida (flags da caixa identicas antes/depois)" \
  "$(contem "$ESTADO" '"mensagens_marcadas_lidas": []' && echo 0 || echo 1)"
chk "6.5 o sink recebeu os 3 corpos por BODY.PEEK (total_corpos_buscados = 3)" \
  "$(contem "$ESTADO" '"total_corpos_buscados": 3' && echo 0 || echo 1)"
chk "6.6 o relatorio de ingesta declara modo de abertura EXAMINE e marcada_como_lida=false" \
  "$(contem "$(cat "$TRABALHO/out/ingesta.json")" '"marcada_como_lida": false' \
     && contem "$(cat "$TRABALHO/out/ingesta.json")" '"modo_de_abertura": "EXAMINE' && echo 0 || echo 1)"

echo "--- 7. guardas ---"
COMANDOS_ANTES="$(contar "$CAP_TLS")"
HOST="192.0.2.1"; PORTA="993"; SEG="implicit_tls"; TIMEOUT=2
exec_modulo --conferir --relatorio "$TRABALHO/out/host-real.json"
chk "7.1 dev com host real RECUSA (HOST_NAO_E_DEV, exit 3)" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA_CMD" HOST_NAO_E_DEV && echo 0 || echo 1)" "exit=$RC"
exec_modulo --listar --relatorio "$TRABALHO/out/host-real-ler.json"
chk "7.2 --listar com host real em dev tambem RECUSA (nao le a caixa real de producao)" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA_CMD" HOST_NAO_E_DEV && echo 0 || echo 1)" "exit=$RC"
exec_modulo --conferir --ambiente prod --relatorio "$TRABALHO/out/prod.json"
chk "7.3 --ambiente prod RECUSA por desenho (exit 4, ADR-005)" \
  "$([ "$RC" -eq 4 ] && contem "$SAIDA_CMD" PRODUCAO_NAO_E_DESTE_CARD && echo 0 || echo 1)" "exit=$RC"
HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; USUARIO="anderson.ribeiro@transformativa.com.br"
exec_modulo --conferir --relatorio "$TRABALHO/out/login-real.json"
chk "7.4 dev com login corporativo RECUSA (USUARIO_NAO_DEV)" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA_CMD" USUARIO_NAO_DEV && echo 0 || echo 1)" "exit=$RC"
USUARIO="sink-dev@dev.local"; PORTA="110"; SEG="implicit_tls"
exec_modulo --conferir --ambiente homolog --relatorio "$TRABALHO/out/pop3.json"
chk "7.5 porta 110 (POP3) RECUSA (PORTA_DE_OUTRO_PROTOCOLO)" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA_CMD" PORTA_DE_OUTRO_PROTOCOLO && echo 0 || echo 1)" "exit=$RC"
PORTA="993"; SEG="starttls"; APROVACAO="APROV-TESTE-001"; CAIXAS="INBOX"
exec_modulo --conferir --ambiente homolog --relatorio "$TRABALHO/out/incoerente.json"
chk "7.6 porta 993 com starttls RECUSA (CONFIG_INCOERENTE)" \
  "$([ "$RC" -eq 3 ] && contem "$SAIDA_CMD" CONFIG_INCOERENTE && echo 0 || echo 1)" "exit=$RC"
HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; APROVACAO=""; CAIXAS=""; TIMEOUT=5
exec_modulo --ingerir --saida "$TRABALHO/out/saida-dry" --chave-idempotencia "aceite:dry" \
  --relatorio "$TRABALHO/out/dry2.json"
COMANDOS_DEPOIS="$(contar "$CAP_TLS")"
chk "7.7 --ingerir sem --confirmo NAO abre conexao (a captura do sink nao cresceu: $COMANDOS_ANTES -> $COMANDOS_DEPOIS)" \
  "$([ "$RC" -eq 0 ] && [ "$COMANDOS_ANTES" = "$COMANDOS_DEPOIS" ] && echo 0 || echo 1)" "exit=$RC"

echo "--- 8. segredo ---"
OCORRENCIAS=$(grep -rl "$SENHA" "$TRABALHO" 2>/dev/null | wc -l | tr -d ' ')
chk "8.1 o valor da senha nao aparece em nenhum arquivo do trabalho (0 = $OCORRENCIAS)" \
  "$([ "$OCORRENCIAS" -eq 0 ] && echo 0 || echo 1)"
chk "8.2 a captura do sink guarda o USUARIO autenticado e a marca <senha-oculta>, nunca a senha" \
  "$(contem "$(estado_do_sink "$CAP_TLS")" '"logins": ["sink-dev@dev.local"]' \
     && contem "$(cat "$CAP_TLS")" 'LOGIN sink-dev@dev.local <senha-oculta>' && echo 0 || echo 1)"

echo "--- 9. desfazer ---"
exec_modulo --desfazer "999:1" --registro "$TRILHA" --relatorio "$TRABALHO/out/desfazer-dry.json"
chk "9.1 --desfazer sem --confirmo e dry-run (exit 0, trilha sem DESFEITO ainda)" \
  "$([ "$RC" -eq 0 ] && contem "$SAIDA_CMD" DRY_RUN \
     && ! grep -q '"DESFEITO"' "$TRILHA" && echo 0 || echo 1)" "exit=$RC"
exec_modulo --desfazer "999:1" --confirmo --registro "$TRILHA" --relatorio "$TRABALHO/out/desfazer.json"
chk "9.2 --desfazer com --confirmo marca DESFEITO e preserva o INGERIDO original na trilha" \
  "$([ "$RC" -eq 0 ] && grep -q '"DESFEITO"' "$TRILHA" \
     && grep -q '"resultado": "INGERIDO"' "$TRILHA" && echo 0 || echo 1)" "exit=$RC"

echo "--- 10. escopo ---"
GIT_DEPOIS="$(git -C "$RAIZ" status --porcelain 2>/dev/null | sha256sum | cut -d' ' -f1)"
chk "10.1 o repositorio sai identico ao que entrou (nenhuma escrita do aceite no repo)" \
  "$([ "$GIT_ANTES" = "$GIT_DEPOIS" ] && echo 0 || echo 1)"
# Fora do loopback, a bateria usa apenas hosts TEST-NET (192.0.2.1, nao roteavel) e SEMPRE em caso de
# recusa medida: se o modulo tivesse tentado conectar, o exit seria 1 (FALHOU), nunca 3/4.
FORA=$(grep -v '^127.0.0.1 ' "$EXECUCOES" | grep -v ' [34]$' | grep -c . 2>/dev/null)
chk "10.2 todo caso com host nao-loopback saiu em RECUSA medida, exit 3/4 (nada de conexao para fora)" \
  "$([ "${FORA:-0}" -eq 0 ] && echo 0 || echo 1)" "${FORA:-0} caso(s) suspeito(s)"
chk "10.3 a leitura e a ingesta aconteceram sob TLS (a captura registra versao TLSv1)" \
  "$(contem "$ESTADO" '"versao_tls": "TLSv1' && echo 0 || echo 1)"
chk "10.4 a captura do sink STARTTLS prova que o STARTTLS foi negociado" \
  "$(contem "$(estado_do_sink "$CAP_STARTTLS")" '"versao_tls": "TLSv1' \
     && contem "$(estado_do_sink "$CAP_STARTTLS")" '"modo_tls": "starttls"' && echo 0 || echo 1)"

if [ "$DENTE" -eq 1 ]; then
  echo "--- 11. prova de dente (cada mutacao tem de reprovar O ITEM ESPERADO) ---"
  for mutacao in sem-guarda-de-host sem-matriz-porta-tls ignora-confirmo senha-sem-mascara \
                 busca-sem-peek sem-idempotencia; do
    ALVO="$TRABALHO/mutado/$mutacao.py"
    if ! python3 "$MUTADOR" --modulo "$MODULO" --destino "$ALVO" --mutacao "$mutacao" \
        >"$TRABALHO/out/mut-$mutacao.txt" 2>&1; then
      chk "11 dente $mutacao: mutacao aplicada" 1 "$(cat "$TRABALHO/out/mut-$mutacao.txt")"
      continue
    fi
    MUT_MODULO="$ALVO"
    caso=""
    HOST="127.0.0.1"; PORTA="$PORTA_TLS"; SEG="implicit_tls"; USUARIO="sink-dev@dev.local"
    TIMEOUT=5; APROVACAO=""; CAIXAS=""
    case "$mutacao" in
      sem-guarda-de-host)
        HOST="192.0.2.1"; PORTA="993"; TIMEOUT=2
        MODULO="$MUT_MODULO"; exec_modulo --conferir --relatorio "$TRABALHO/out/m-h.json"
        caso="$([ "$RC" -ne 3 ] && echo 0 || echo 1)"; DETALHE="exit mutado=$RC (esperado != 3)"
        ;;
      sem-matriz-porta-tls)
        HOST="192.0.2.1"; PORTA="993"; SEG="starttls"; TIMEOUT=2; APROVACAO="APROV-TESTE-001"
        CAIXAS="INBOX"; MODULO="$MUT_MODULO"
        exec_modulo --conferir --ambiente homolog --relatorio "$TRABALHO/out/m-m.json"
        caso="$([ "$RC" -ne 3 ] && echo 0 || echo 1)"; DETALHE="exit mutado=$RC (esperado != 3)"
        ;;
      ignora-confirmo)
        M_SAIDA="$TRABALHO/out/mut-saida"; rm -rf "$M_SAIDA"; mkdir -p "$M_SAIDA"
        MODULO="$MUT_MODULO"
        exec_modulo --ingerir --saida "$M_SAIDA" --chave-idempotencia "dente:confirmo" \
          --registro "$TRABALHO/out/trilha-dente.jsonl" --relatorio "$TRABALHO/out/m-c.json"
        caso="$([ "$(listar "$M_SAIDA")" -gt 0 ] && echo 0 || echo 1)"
        DETALHE="arquivos sem --confirmo = $(listar "$M_SAIDA") (esperado > 0)"
        ;;
      senha-sem-mascara)
        MODULO="$MUT_MODULO"; exec_modulo --planejar --relatorio "$TRABALHO/out/m-s.json"
        caso="$(contem "$SAIDA_CMD" "$SENHA" && echo 0 || echo 1)"
        DETALHE="senha visivel na saida mutada?"
        ;;
      busca-sem-peek)
        M_SAIDA="$TRABALHO/out/mut-peek"; rm -rf "$M_SAIDA"; mkdir -p "$M_SAIDA"
        M_TRILHA="$TRABALHO/out/trilha-peek.jsonl"; : >"$M_TRILHA"
        MODULO="$MUT_MODULO"
        exec_modulo --ingerir --saida "$M_SAIDA" --chave-idempotencia "dente:peek" --confirmo \
          --registro "$M_TRILHA" --relatorio "$TRABALHO/out/m-p.json"
        M_ESTADO="$(estado_do_sink "$CAP_TLS")"
        caso="$(contem "$M_ESTADO" '"mensagens_marcadas_lidas": []' && echo 1 || echo 0)"
        DETALHE="marcadas_lidas mutado=$(contem "$M_ESTADO" '"mensagens_marcadas_lidas": []' && echo '[] (nao detectou)' || echo 'com \\Seen')"
        ;;
      sem-idempotencia)
        M_SAIDA="$TRABALHO/out/mut-idem"; rm -rf "$M_SAIDA"; mkdir -p "$M_SAIDA"
        MODULO="$MUT_MODULO"
        exec_modulo --ingerir --saida "$M_SAIDA" --chave-idempotencia "dente:idem" --confirmo \
          --registro "$TRABALHO/out/trilha-idem.jsonl" --relatorio "$TRABALHO/out/m-i.json"
        # Com a idempotencia desligada, o replay da mensagem ja ingerida e buscado DE NOVO: o
        # contador de corpos do sink cresce (o item 5.4 e a prova de que o modulo intacto nao repete).
        M_SAIDA2="$TRABALHO/out/mut-idem2"; rm -rf "$M_SAIDA2"; mkdir -p "$M_SAIDA2"
        exec_modulo --ingerir --saida "$M_SAIDA2" --chave-idempotencia "dente:idem" --confirmo \
          --registro "$TRABALHO/out/trilha-idem.jsonl" --relatorio "$TRABALHO/out/m-i2.json"
        caso="$(contem "$SAIDA_CMD" JA_INGERIDO && echo 1 || echo 0)"
        DETALHE="replay mutado deveria NAO responder JA_INGERIDO (respondeu $(contem "$SAIDA_CMD" JA_INGERIDO && echo sim || echo nao))"
        ;;
    esac
    chk "11 dente $mutacao: $(cat "$TRABALHO/out/mut-$mutacao.txt")" "$caso" "$DETALHE"
    MODULO="${TRE_W6_IMAP_PY:-$RAIZ/hermes/integracoes/titan/imap_titan.py}"
  done

  echo "--- 12. controle: o modulo INTACTO continua verde (o dente nao e verde falso) ---"
  python3 "$SUITE" --raiz "$RAIZ" >"$TRABALHO/out/suite-controle.out" 2>&1
  RC_SUITE=$?
  chk "12.1 a suite offline roda verde no modulo intacto apos as mutacoes" \
    "$([ "$RC_SUITE" -eq 0 ] && echo 0 || echo 1)" "$(tail -1 "$TRABALHO/out/suite-controle.out")"
fi

echo "---"
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: ACEITE_IMAP_TITAN_001_OK ($ITENS_OK itens, 0 falhas)"
  [ "$MANTER" -eq 1 ] && echo "(trabalho mantido em $TRABALHO)"
  exit 0
fi
echo "RESULTADO: ACEITE_IMAP_TITAN_001_FALHOU ($ITENS_OK itens OK, $FALHAS falha(s))"
echo "(trabalho mantido em $TRABALHO)"
exit 1
