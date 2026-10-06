#!/bin/bash
# Sonda DIRETA do portao de aprovacao da API controlada (roda NA VPS, com a stack no ar).
# Por que existe: mede o portao em UM request, sem depender da agenda do consumidor.
#
# Regras de segredo do projeto: a chave e lida do arquivo 600 e entregue ao curl por ARQUIVO DE
# CONFIGURACAO (--config) — nunca por argv (nao aparece no `ps`) — e o script imprime apenas
# status HTTP, `codigo` e `ambiente`. Nenhum valor de segredo sai na saida.
#
# Uso:  bash sonda-portao-producao.sh <rotulo> [diretorio-de-segredos]
#   <rotulo>  sufixo curto do idempotency_key/correlation_id (ex.: vencida, valida)
#   dir       default /etc/tre/odoo-prod
#
# ATENCAO (armadilha medida): o parametro `tf.api.aprovacao` e CACHEADO no registro do Odoo.
# Mudar a aprovacao e NAO reiniciar o Odoo faz esta sonda responder com a validade antiga
# (falso passe do portao). Reinicie antes de sondar:  docker compose restart <odoo>
#
# O UUID do payload usa sufixo de 2 digitos hex, calculado FORA do $() que monta o payload.
# Defeito que isto evita (medido, nao teorico): com o calculo aninhado dentro do printf, um
# rotulo sem digito (ex.: "vencida") faz `$((10#${SUF//[!0-9]/} % 100))` morrer com
# "10#: invalid integer constant" ANTES de enviar o request; e um rotulo como "probe-3c"
# descarta o 'c' em silencio, fazendo dois rotulos colidirem no mesmo UUID.
# TRE_UUID_SUFIXO sobrepoe o sufixo (util para fixar um UUID conhecido num teste de dedup).
#
# Interpretacao:
#   HTTP 503 + codigo=aprovacao_ausente  => portao FECHADO (sem aprovacao valida) — o que o dente exige
#   HTTP 422 + codigo=valor_invalido     => passou do portao e recusou o payload (a aprovacao vale)
#   HTTP 200 + codigo=aceito             => escrita aceita sob aprovacao valida
#   HTTP 403                             => token/credencial recusada (nao e o portao de aprovacao)
set -euo pipefail

ROTULO="${1:?informe um rotulo curto (ex.: vencida, valida)}"
DIR="${2:-/etc/tre/odoo-prod}"
HOST="${TRE_HOST:-https://tre.transformativa.com.br}"
OPERACAO="${TRE_OPERACAO:-empresa_upsert}"

# Sufixo do UUID: 2 digitos hex, montado numa atribuicao propria e validado fail-closed.
# O padrao e ALEATORIO por execucao, de proposito: dois rotulos sondados no mesmo segundo nao podem
# cair no mesmo UUID — foi medido que derivar de `date +%s` colide, e UUID repetido faz o teste de
# portao passar como dedup (falso passe) em vez de exercitar uma chamada nova.
if [ -n "${TRE_UUID_SUFIXO:-}" ]; then
  SUFIXO="${TRE_UUID_SUFIXO}"
else
  SUFIXO="$(printf '%02x' $(( RANDOM % 256 )))"
fi
case "$SUFIXO" in
  [0-9a-f][0-9a-f]) ;;
  *) echo "SONDA_ERRO sufixo invalido: '$SUFIXO' (use 2 digitos hex, 00-ff)" >&2; exit 2 ;;
esac
UUID="22222222-3333-4444-8555-9001000000${SUFIXO}"

CHAVE="$(cat "$DIR/chave-api.txt")"
CFG="$(mktemp)"; CORPO="$(mktemp)"
chmod 600 "$CFG" "$CORPO"
trap 'rm -f "$CFG" "$CORPO"' EXIT
printf 'header = "Authorization: Bearer ***"\n' "$CHAVE" > "$CFG"
printf 'header = "Content-Type: application/json"\nsilent\nshow-error\nmax-time = 20\n' >> "$CFG"

PAYLOAD="$(printf '{"idempotency_key":"sonda-portao-%s","correlation_id":"sonda-portao-%s","parametros":{"modelo":"res.partner","valores":{"name":"Sonda portao %s","tf_company_id":"%s"}}}' "$ROTULO" "$ROTULO" "$ROTULO" "$UUID")"

# `-w` ja imprime 000 quando o curl falha: nao duplicar com `|| echo 000`
# (fazia SONDA_HTTP=000000, ambiguo para quem le o veredito).
HTTP=000
HTTP="$(curl --config "$CFG" -o "$CORPO" -w '%{http_code}' -X POST --data "$PAYLOAD" "$HOST/tf/api/v1/$OPERACAO" 2>/dev/null)" || HTTP=000
echo "SONDA_HTTP=$HTTP"
echo "SONDA_uuid=$UUID"
python3 - "$CORPO" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("SONDA_CORPO_NAO_JSON"); raise SystemExit(0)
for campo in ("codigo", "aceito", "ambiente"):
    print("SONDA_%s=%s" % (campo, d.get(campo)))
PY
