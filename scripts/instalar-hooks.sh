#!/usr/bin/env bash
# Instala os ganchos versionados deste repo no clone atual.
# O git usa os ganchos do worktree PRINCIPAL para todos os worktrees do mesmo repo — entao
# rodar isto uma vez no clone compartilhado cobre todas as execucoes-irmas.
set -euo pipefail

TOPO=$(git rev-parse --show-toplevel)
COMMON=$(git rev-parse --git-common-dir)
case "$COMMON" in /*) DEST="$COMMON/hooks" ;; *) DEST="$TOPO/$COMMON/hooks" ;; esac

mkdir -p "$DEST"
install -m 0755 "$TOPO/scripts/git-hooks/pre-push" "$DEST/pre-push"
echo "instalado: $DEST/pre-push"
echo "  vale para o repo $TOPO (todos os worktrees). Teste: git push --dry-run origin HEAD:main  (deve recusar)"
