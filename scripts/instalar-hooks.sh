#!/usr/bin/env bash
# Instala os ganchos versionados deste repo no clone atual.
# O git usa os ganchos do worktree PRINCIPAL para todos os worktrees do mesmo repo — entao
# rodar isto uma vez no clone compartilhado cobre todas as execucoes-irmas.
set -euo pipefail

TOPO=$(git rev-parse --show-toplevel)
FONTE="$TOPO/scripts/git-hooks/pre-push"
[ -f "$FONTE" ] || { echo "nao achei $FONTE (rode este script de um checkout que tenha o gancho)" >&2; exit 1; }

DESTINO="${1:-$TOPO}"
COMMON=$(git -C "$DESTINO" rev-parse --git-common-dir)
case "$COMMON" in /*) DEST="$COMMON/hooks" ;; *) DEST="$DESTINO/$COMMON/hooks" ;; esac

mkdir -p "$DEST"
install -m 0755 "$FONTE" "$DEST/pre-push"
echo "gancho instalado: $DEST/pre-push"
echo "  fonte: $FONTE"
echo "  vale para o repo $DESTINO (todos os worktrees dele)."
echo "  teste: git -C $DESTINO push --dry-run origin HEAD:main   (deve RECUSAR)"
