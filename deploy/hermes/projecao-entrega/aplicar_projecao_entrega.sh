#!/usr/bin/env bash
# APLICA o conserto de raiz da RAIZ DO REGISTRO DE ENTREGAS (card TRE-W0-E04-T11:
# `t_b74c2edd`) no plugin kanban do dashboard do Hermes.
#
#   bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh            # aplica
#   bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh --check    # so relata
#   bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh --reverter # desfaz
#
# POR QUE ESTE SCRIPT EXISTE:
# o plugin lia os artefatos de entrega (`control-plane/deliveries/*.json`) de um
# caminho FIXO -- a raiz de OUTRO projeto. Aqui mora a versao versionada do
# conserto: a raiz passa a ser DECLARADA (`delivery_repo` no board.json > env
# `HERMES_DELIVERIES_ROOT` > legado documentado). O agente do Hermes nao tem
# sudo e /opt/hermes pertence ao root (ver skill `kanban-plugin-update`), entao a
# copia empacotada (bundled) exige UMA LINHA DO OPERADOR; a copia `user`, que e a
# que o dashboard realmente carrega, o agente aplica sozinho.
#
# NAO use patch(1) para isto: num salto de imagem os hunks nao entram. O editor
# (`editar_plugin_api_delivery_root.py`) trabalha por ANCORA, exige cada ancora
# exatamente 1x, faz backup datado e ABORTA SEM ESCREVER se a forma do arquivo
# mudou -- e idempotente (rodar de novo nao duplica nada).
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="${HERMES_PY:-/opt/hermes/.venv/bin/python}"
EDITOR="$RAIZ/deploy/hermes/projecao-entrega/editar_plugin_api_delivery_root.py"

ALVOS=(
  "/opt/data/plugins/kanban/dashboard/plugin_api.py"   # user  (o dashboard USA este)
  "/opt/hermes/plugins/kanban/dashboard/plugin_api.py" # bundled (imagem; exige root)
)

if [ ! -f "$EDITOR" ]; then
  echo "FALHOU editor nao encontrado: $EDITOR"
  exit 1
fi

PENDENTE_ROOT=0
FALHA=0
for ALVO in "${ALVOS[@]}"; do
  if [ ! -f "$ALVO" ]; then
    echo "PULADO ausente: $ALVO"
    continue
  fi
  if [ ! -w "$ALVO" ]; then
    echo "PENDENTE (root) sem permissao de escrita: $ALVO"
    PENDENTE_ROOT=1
    continue
  fi
  if ! "$PY" "$EDITOR" --alvo "$ALVO" "$@"; then
    FALHA=1
  fi
done

echo
if [ "$FALHA" -ne 0 ]; then
  echo "RESULTADO: FALHOU (ancora ausente ou erro de escrita — o plugin mudou de forma; revisar o patch, NAO adivinhar)"
  exit 1
fi
if [ "$PENDENTE_ROOT" -ne 0 ]; then
  echo "RESULTADO: OK nas copias gravaveis; a copia EMPACOTADA ainda depende da linha do operador (root)."
  exit 2
fi
echo "RESULTADO: OK (todas as copias presentes estao canonicas)"
