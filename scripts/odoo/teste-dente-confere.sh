#!/usr/bin/env bash
# ============================================================================
# Controle do predicado de dente do aceite da API controlada — TRE-W3-E01-T01-D01.
#
# O QUE ESTE SCRIPT PROVA (e por que existe):
#   O defeito TRE-W3-E01-T01-D01 foi um `--prova-de-dente` FAIL-OPEN: cada dente decidia "mordeu"
#   por `grep -q 'RESULTADO: ..._FALHOU'` no sub-run, entao QUALQUER falha valia como prova —
#   inclusive um aborto da guarda de ambiente (imagem ausente, docker fora do ar). Com
#   `TRE_IMAGEM=odoo:nao-existe-9999` os dentes 1 e 2 abortavam depois de 2 itens, sem medir nada, e
#   o harness imprimia `API_CONTROLADA_DENTE_OK (3 provas, 0 falhas)` com exit 0.
#
#   O conserto troca o predicado por "o sub-run MEDIU (piso de itens do modo + logs de passo) E o
#   item esperado esta' entre os reprovados". Este controle trava esse predicado: ele extrai a
#   funcao REAL (`dente_confere`) de `scripts/odoo/verificar-api-controlada.sh` — nao uma copia — e a
#   exercita com entradas fabricadas, uma por condicao. Nao precisa de Docker nem da VPS: roda em
#   segundos, e e' o controle que o proximo verificador da onda W3 (cards E02/E03/E04, que herdam
#   este script como molde) deve rodar junto com o aceite.
#
# Uso:
#   bash scripts/odoo/teste-dente-confere.sh
#   bash scripts/odoo/teste-dente-confere.sh /caminho/do/verificar-api-controlada.sh
#
# Saida: um item por linha (`OK`/`FALHOU`), resumo em uma linha e exit code:
#   0 = predicado do dente confere   1 = o predicado esta' errado   2 = nao deu para testar
# ============================================================================
set -u

AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ALVO="${1:-$AQUI/verificar-api-controlada.sh}"
ITENS=0
FALHAS=0

ok()     { ITENS=$((ITENS + 1)); printf 'OK    %s\n' "$*"; }
falhou() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); printf 'FALHOU %s\n' "$*"; }
resumo() {
    if [ "$FALHAS" -eq 0 ]; then
        echo "RESULTADO: DENTE_CONFERE_OK ($ITENS itens, 0 falhas) alvo=$ALVO"
        exit 0
    fi
    echo "RESULTADO: DENTE_CONFERE_FALHOU ($ITENS itens, $FALHAS falha(s)) alvo=$ALVO"
    exit 1
}

[ -f "$ALVO" ] || { echo "FALHOU alvo ausente: $ALVO" >&2; echo "RESULTADO: DENTE_CONFERE_NAO_MEDIU (alvo ausente)"; exit 2; }

T="$(mktemp -d /tmp/teste-dente-confere-XXXXXX)"
trap 'rm -rf "$T"' EXIT

# A funcao dente_confere vive dentro do bloco `if [ "$MODO" = "dente" ]` do verificador, indentada
# com 4 espacos. Extrai-la (em vez de reescrever a regra aqui) e' o ponto do controle: o que roda e'
# o codigo que o aceite usa.
sed -n '/^    dente_confere() {$/,/^    }$/p' "$ALVO" | sed 's/^    //' >"$T/fn.sh"
if ! grep -q 'DENTE_FALHAS=$((DENTE_FALHAS + 1))' "$T/fn.sh" \
   || ! grep -q 'ORIGEM_DENTE=' "$T/fn.sh"; then
    echo "FALHOU nao consegui extrair a dente_confere do alvo (o modo --prova-de-dente mudou de forma?)" >&2
    echo "RESULTADO: DENTE_CONFERE_NAO_MEDIU (funcao nao extraida de $ALVO)"
    exit 2
fi
ok "predicado extraido do proprio verificador ($ALVO)"

mkdir -p "$T/prova" "$T/prova-sem-log"
printf 'log de instalacao\n' >"$T/prova/1-instalacao.log"
printf 'log de preparo\n' >"$T/prova/3-preparo.log"
printf 'log do servidor\n' >"$T/prova/3b-servidor.log"

# Entradas fabricadas no formato que o sub-run do dente imprime.
transcricao() { # $1=arquivo $2=itens $3=com_item_esperado(sim|nao)
    {
        printf 'INFO  sub-run sob prova\n'
        printf 'ORIGEM_DENTE=7 modo=http itens=%s falhas=1\n' "$2"
        if [ "${3:-sim}" = "sim" ]; then
            printf 'FALHOU operacao declarada (sistema_capacidades) -> HTTP 404 (esperado 200): {"ok": false}\n'
        else
            printf 'FALHOU item nenhum a ver -> HTTP 500 (esperado 200): {"ok": false}\n'
        fi
        printf 'RESULTADO: API_CONTROLADA_FALHOU (%s itens, 1 falha(s)) modulo=m banco=b imagens=i\n' "$2"
    } >"$1"
}
transcricao_sem_rotulo() {
    { printf 'FALHOU operacao declarada (sistema_capacidades) -> HTTP 404 (esperado 200)\n'
      printf 'RESULTADO: API_CONTROLADA_FALHOU (61 itens, 1 falha(s))\n'; } >"$1"
}
transcricao_que_passou() {
    printf 'RESULTADO: API_CONTROLADA_OK (61 itens, 0 falhas)\n' >"$1"
}

conferir() { # $1=transcricao $2=dir de logs $3=piso -> imprime a ultima linha (DENTE_FALHAS=n)
    ( DENTE_FALHAS=0
      . "$T/fn.sh"
      dente_confere 7 'dente teste' "$1" "$2" "$1" 'RESULTADO: API_CONTROLADA_FALHOU' "$3" \
          'operacao declarada \(sistema_capacidades\) -> HTTP 404' \
          1-instalacao.log 3-preparo.log 3b-servidor.log
      echo "DENTE_FALHAS=$DENTE_FALHAS" )
}
espera() { # $1=rotulo $2=0|1 (falhas esperadas) $3=saida
    local ultima
    ultima="$(printf '%s\n' "$3" | tail -1)"
    if [ "$ultima" = "DENTE_FALHAS=$2" ]; then
        ok "$1 (DENTE_FALHAS=$2, como esperado)"
    else
        falhou "$1 — esperado DENTE_FALHAS=$2, veio '$ultima'"
        printf '%s\n' "$3" | sed 's/^/      /'
    fi
}

transcricao "$T/ok.out" 61 sim
espera "medicao completa, com o item certo, passa" 0 "$(conferir "$T/ok.out" "$T/prova" 61)"

transcricao "$T/aborto.out" 2 sim
espera "aborto da guarda de ambiente (2 itens, piso 61) reprova" 1 "$(conferir "$T/aborto.out" "$T/prova" 61)"

transcricao "$T/semlog.out" 61 sim
espera "log de passo ausente reprova" 1 "$(conferir "$T/semlog.out" "$T/prova-sem-log" 61)"

transcricao "$T/outro.out" 61 nao
espera "falha por OUTRO item (nao o que o dente prova) reprova" 1 "$(conferir "$T/outro.out" "$T/prova" 61)"

transcricao_sem_rotulo "$T/semrotulo.out"
espera "sub-run sem a rotulacao TRE_ORIGEM_DENTE reprova" 1 "$(conferir "$T/semrotulo.out" "$T/prova" 61)"

transcricao_que_passou "$T/passou.out"
espera "sub-run que nao reprovou nada reprova o dente" 1 "$(conferir "$T/passou.out" "$T/prova" 61)"

resumo
