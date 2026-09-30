#!/usr/bin/env bash
# =====================================================================================
# verificar-modos-executaveis.sh — prova que todo script chamado DIRETO por um unit
# systemd tem o bit executavel no git (100755) e na copia operacional.
#
# Motivo (defeito t_22c27625): os scripts de scripts/backup/ estavam 100644 no git; o
# `ExecStart=` do unit chama o arquivo direto, entao qualquer sincronizacao a partir do
# repositorio devolvia 644 na copia operacional e o systemd recusava o exec:
#   tre-backup.service: Failed at step EXEC ... Permission denied   (status=203/EXEC)
# O bit executavel vive no GIT (100755 x 100644), nao no sistema operacional: corrigir
# so a copia operacional nao sobrevive ao proximo deploy.
#
# Uso:
#   scripts/backup/verificar-modos-executaveis.sh
#   RAIZ_REPO=/opt/tre/repo scripts/backup/verificar-modos-executaveis.sh
#
# Variaveis:
#   RAIZ_REPO            raiz do checkout/repositorio (default: deduzida do proprio script)
#   UNITS_DIR            onde estao os .service (default: $RAIZ_REPO/deploy/systemd)
#   PREFIXO_OPERACIONAL  prefixo dos ExecStart que apontam para a copia operacional
#                        (default: /opt/tre/repo; no VPS a copia operacional E o repo)
#
# Item por unit:
#   1. modo no INDICE do git (100755) do caminho relativo do script — NOTA quando
#      $RAIZ_REPO nao e um work tree git (a copia operacional nao tem .git: la a prova do
#      indice e feita no checkout);
#   2. bit executavel NO DISCO do caminho absoluto do ExecStart (o que o systemd
#      resolve) — NOTA quando o arquivo nao existe nesta maquina.
#
# NAO existe PASS vazio: se nenhuma prova efetiva foi possivel (tudo NOTA), o resultado e
# MODOS_FALHOU. Mesma licao do defeito D04 (t_967965f0): verificador que imprime PASS sem
# ter conferido nada e aceite falso.
#
# Saida: OK/FALHOU/NOTA por item; `RESULTADO: MODOS_OK` exit 0 ou `RESULTADO: MODOS_FALHOU`
# exit 1 — o instalar-timers.sh usa este exit code para NAO habilitar um timer quebrado.
# =====================================================================================
set -u

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ_REPO="${RAIZ_REPO:-$(cd "$AQUI/../.." && pwd)}"
UNITS_DIR="${UNITS_DIR:-$RAIZ_REPO/deploy/systemd}"
PREFIXO_OPERACIONAL="${PREFIXO_OPERACIONAL:-/opt/tre/repo}"

FALHAS=0
ITENS=0
PROVAS=0
SEM_GIT=0
chk() { ITENS=$((ITENS + 1)); PROVAS=$((PROVAS + 1)); if [ "$2" = "1" ]; then echo "OK     $1"; else echo "FALHOU $1"; FALHAS=$((FALHAS + 1)); fi; }
nota() { ITENS=$((ITENS + 1)); echo "NOTA   $1"; }
falha() { ITENS=$((ITENS + 1)); PROVAS=$((PROVAS + 1)); echo "FALHOU $1"; FALHAS=$((FALHAS + 1)); }

echo "== raiz: $RAIZ_REPO"
echo "== units: $UNITS_DIR"
if [ ! -d "$UNITS_DIR" ]; then
  falha "diretorio de units inexistente: $UNITS_DIR"
  echo
  echo "RESULTADO: MODOS_FALHOU ($ITENS itens, $FALHAS falhas, $PROVAS provas efetivas)"
  exit 1
fi

# A copia operacional nao tem .git: a prova do modo no indice e feita no checkout do repositorio.
if git -C "$RAIZ_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TEM_GIT=1
else
  TEM_GIT=0
  SEM_GIT=1
fi

for unidade in "$UNITS_DIR"/*.service; do
  [ -f "$unidade" ] || continue
  # primeiro token do ExecStart = o binario/script chamado direto pelo systemd
  exec_start="$(awk -F= '/^ExecStart=/{print $2; exit}' "$unidade" | awk '{print $1}')"
  [ -n "$exec_start" ] || { nota "$(basename "$unidade") sem ExecStart"; continue; }

  rel=""
  case "$exec_start" in
    "$PREFIXO_OPERACIONAL"/*) rel="${exec_start#"$PREFIXO_OPERACIONAL"/}" ;;
    "$RAIZ_REPO"/*)           rel="${exec_start#"$RAIZ_REPO"/}" ;;
  esac

  if [ -n "$rel" ]; then
    if [ "$TEM_GIT" = "1" ]; then
      modo="$(git -C "$RAIZ_REPO" ls-files -s -- "$rel" | awk '{print $1}')"
      if [ -z "$modo" ]; then
        chk "git 100755 $rel (NAO versionado no indice do git)" 0
      else
        chk "git $modo $rel (esperado 100755)" "$([ "$modo" = "100755" ] && echo 1 || echo 0)"
      fi
    else
      nota "git(1) $rel: $RAIZ_REPO nao e um work tree git"
    fi
  else
    nota "ExecStart '$exec_start' nao esta sob $RAIZ_REPO nem $PREFIXO_OPERACIONAL: sem modo no git a provar"
  fi

  if [ -e "$exec_start" ]; then
    chk "disco -x $exec_start" "$([ -x "$exec_start" ] && echo 1 || echo 0)"
  else
    nota "disco -x $exec_start (nao existe nesta maquina)"
  fi
done

if [ "$SEM_GIT" = "1" ]; then
  echo "AVISO  modo no git nao conferido aqui (sem .git): a prova do indice e feita no checkout"
fi

echo
if [ "$PROVAS" = "0" ]; then
  echo "RESULTADO: MODOS_FALHOU ($ITENS itens, $FALHAS falhas, 0 provas efetivas) — nada foi provado"
  exit 1
fi
if [ "$FALHAS" = "0" ]; then
  echo "RESULTADO: MODOS_OK ($ITENS itens, 0 falhas, $PROVAS provas efetivas)"
  exit 0
fi
echo "RESULTADO: MODOS_FALHOU ($ITENS itens, $FALHAS falhas, $PROVAS provas efetivas)"
exit 1
