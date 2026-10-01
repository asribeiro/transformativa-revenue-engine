#!/usr/bin/env bash
# =====================================================================================
# verificar-ultimo-backup.sh [dev|homolog|prod|todos]
#
# Verifica o artefato de backup MAIS RECENTE de cada ambiente, fazendo um RESTORE REAL
# num container descartavel. E o que transforma "backup existe" em "backup presta":
# backup nunca restaurado nao e backup, e esperanca.
#
# Provisionamento e resolucao do ambiente saem da MESMA regra do backup
# (lib-ambiente.sh) — antes da correcao do defeito t_1b2ab418 este script procurava
# `pg-<amb>` e nao via o `pg-sales-dev`, entao aprovava um artefato de teste manual
# enquanto a rotina diaria nao produzia nada:
#   ambiente provisionado (container existe) sem backup = FALHA (nao e "pulado");
#   ambiente nao provisionado e sem backup            = PULADO (honesto);
#   ambiente DECLARADO cujo container nao existe      = FALHA de configuracao.
# =====================================================================================
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-ambiente.sh
. "$AQUI/lib-ambiente.sh"

ALVO="${1:-todos}"
DEST="${TRE_BACKUP_DIR:-/opt/tre/backup}"
FALHAS=0
ITENS=0
ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

verificar_ambiente() {
  local amb="$1" modo="${2:-um}"
  echo "------------------------------------------------------------------"
  if ! tre_resolver_ambiente "$amb" "$modo"; then
    echo "-- ambiente: $amb"
    ko "$TRE_AMB_ERRO"
    return 0
  fi
  tre_estado_ambiente
  echo "-- ambiente: $amb   servico: $TRE_AMB_SERVICO   config: $TRE_AMB_FONTE"
  [ -n "${TRE_AMB_AVISO:-}" ] && echo "NOTA   $TRE_AMB_AVISO"

  local ultimo
  ultimo="$(ls -d "$DEST/tre_${amb}_"* 2>/dev/null | sort | tail -1)"

  if [ -z "$ultimo" ]; then
    case "$TRE_AMB_ESTADO" in
      COBRIR) ko "ambiente '$amb' esta provisionado (container $TRE_AMB_SERVICO, $TRE_AMB_FONTE) e NAO tem backup nenhum — a rotina diaria nao esta produzindo artefato para este ambiente" ;;
      FALHAR) ko "$TRE_AMB_MOTIVO" ;;
      *)      echo "PULADO $TRE_AMB_MOTIVO" ;;
    esac
    return 0
  fi

  # existe artefato: configuracao quebrada continua sendo falha, e o artefato e conferido
  [ "$TRE_AMB_ESTADO" = "FALHAR" ] && ko "$TRE_AMB_MOTIVO"

  local idade_h
  idade_h="$(python3 - "$ultimo" <<'PY' 2>/dev/null || echo "?"
import os, sys, time
print(int((time.time() - os.path.getmtime(sys.argv[1])) // 3600))
PY
)"
  echo "   artefato mais recente: $(basename "$ultimo") (${idade_h}h de idade)"

  # O artefato e mesmo DESTE ambiente? (manifesto diz de qual container ele veio)
  # Um artefato de 'dev' copiado do container de outro ambiente passaria no restore —
  # por isso a origem e conferida aqui, contra a resolucao atual.
  local servico_manifesto
  servico_manifesto="$(awk -F': ' '/^servico:/{print $2; exit}' "$ultimo/manifest.txt" 2>/dev/null)"
  if [ -z "$servico_manifesto" ]; then
    echo "NOTA   manifesto sem a linha 'servico:' — nao da para conferir de qual container veio"
  elif [ "$TRE_AMB_ESTADO" = "COBRIR" ] && [ "$servico_manifesto" != "$TRE_AMB_SERVICO" ]; then
    ko "artefato de '$amb' veio do container '$servico_manifesto' e a configuracao atual aponta para '$TRE_AMB_SERVICO' — backup de outro ambiente com o rotulo deste"
  else
    ok "origem do artefato confere (container '$servico_manifesto')"
  fi

  if bash "$AQUI/verificar-backup.sh" "$ultimo"; then
    ok "restore do ultimo backup de '$amb' aprovado"
  else
    ko "restore do ultimo backup de '$amb' REPROVADO"
  fi

  # Odoo do ambiente (card TRE-W2-E01-T01-F01): o artefato que declara Odoo tem de passar
  # pelo restore proprio — subir o Odoo contra o banco restaurado. Ambiente que declara
  # Odoo e cujo artefato NAO tem o bloco do Odoo tambem e falha (backup pela metade).
  if [ -s "$ultimo/odoo-manifest.txt" ]; then
    if bash "$AQUI/verificar-odoo.sh" "$ultimo"; then
      ok "restore do Odoo do ultimo backup de '$amb' aprovado"
    else
      ko "restore do Odoo do ultimo backup de '$amb' REPROVADO"
    fi
  else
    tre_resolver_odoo "$amb" >/dev/null 2>&1 || true
    tre_estado_odoo
    if [ "$TRE_ODOO_ESTADO" = "COBRIR" ]; then
      ko "ambiente '$amb' tem Odoo ($TRE_ODOO_MOTIVO) e o artefato mais recente NAO tem o bloco do Odoo — backup do ambiente esta pela metade"
    else
      echo "NOTA   artefato sem bloco do Odoo e o ambiente nao declara Odoo (nada a restaurar la)"
    fi
  fi

  # backup velho tambem e falha: se a rotina diaria parou, quero saber aqui
  if [ "${idade_h:-0}" != "?" ] && [ "${idade_h:-0}" -gt 48 ] 2>/dev/null; then
    ko "ultimo backup tem ${idade_h}h (> 48h) — a rotina de backup parou?"
  fi
}

if [ "$ALVO" = "todos" ]; then
  for amb in dev homolog prod; do verificar_ambiente "$amb" todos; done
else
  verificar_ambiente "$ALVO" um
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: VERIFICACAO_OK ($ITENS itens)"
  exit 0
else
  echo "RESULTADO: VERIFICACAO_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
