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

COMPOSITOR="$RAIZ/deploy/hermes/plugin-composicao/compor_plugin_api.py"

# A cadeia INTEIRA, na ordem (medido em 05/10/2026; a ordem importa porque cada delta
# exige a forma do anterior):
#   1. delta deste card  — raiz do registro de entregas DECLARADA (editor ancorado)
#   2. filtro de snapshots (`_artifact_files`, escrito em runtime em 02/10 sem card)
#   3. coluna `production` (`Em producao (entrega)`, 05/10)
#   4. frontend: `dist/index.js` (relabel pt-BR + a coluna; nao ha fonte no repo, so
#      bundle — por isso a forma versionada e' o patch `dist_index.patch`)
# Sem os passos 2-4 uma troca de imagem devolve o plugin a um estado em que o conserto
# deste card nao aparece.
RAIZES=(
  "/opt/data"   # copia `user` — o dashboard USA esta (o agente grava sozinho)
  "/opt/hermes" # copia empacotada (imagem) — exige a linha do operador (root)
)

for FERRAMENTA in "$EDITOR" "$COMPOSITOR"; do
  if [ ! -f "$FERRAMENTA" ]; then
    echo "FALHOU ferramenta nao encontrada: $FERRAMENTA"
    exit 1
  fi
done

PENDENTE_ROOT=0
FALHA=0
for R in "${RAIZES[@]}"; do
  echo "== $R"
  if [ ! -d "$R/plugins/kanban/dashboard" ]; then
    echo "PULADO ausente: $R/plugins/kanban/dashboard"
    continue
  fi
  if [ ! -w "$R/plugins/kanban/dashboard" ]; then
    echo "PENDENTE (root) sem permissao de escrita: $R/plugins/kanban/dashboard"
    PENDENTE_ROOT=1
    continue
  fi
  ALVO="$R/plugins/kanban/dashboard/plugin_api.py"
  BUNDLE="$R/plugins/kanban/dashboard/dist/index.js"
  # Em REVERSAO a ordem e' inversa: desfaz a composicao e so' depois o delta do card.
  if [ "${1:-}" = "--reverter" ]; then
    [ -f "$ALVO" ] && { "$PY" "$COMPOSITOR" --alvo "$ALVO" "$@" || FALHA=1; }
    [ -f "$ALVO" ] && { "$PY" "$EDITOR" --alvo "$ALVO" "$@" || FALHA=1; }
  else
    [ -f "$ALVO" ] && { "$PY" "$EDITOR" --alvo "$ALVO" "$@" || FALHA=1; }
    [ -f "$ALVO" ] && { "$PY" "$COMPOSITOR" --alvo "$ALVO" "$@" || FALHA=1; }
  fi
  if [ -f "$BUNDLE" ]; then
    "$PY" "$COMPOSITOR" --bundle-raiz "$R" "$@" || FALHA=1
  else
    echo "PULADO ausente: $BUNDLE"
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
