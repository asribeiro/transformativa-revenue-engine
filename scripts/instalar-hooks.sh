#!/usr/bin/env bash
# Instala os ganchos versionados deste repo no clone atual.
# O git usa os ganchos do worktree PRINCIPAL para todos os worktrees do mesmo repo — entao
# rodar isto uma vez no clone compartilhado cobre todas as execucoes-irmas.
set -euo pipefail

ORIGEM=$(git rev-parse --show-toplevel)
FONTE="$ORIGEM/scripts/hooks/pre-push"
[ -f "$FONTE" ] || { echo "nao achei $FONTE (rode este script de um checkout que tenha o gancho)" >&2; exit 1; }

DESTINO="${1:-$ORIGEM}"
COMMON=$(git -C "$DESTINO" rev-parse --git-common-dir)
case "$COMMON" in /*) DEST_DIR="$COMMON" ;; *) DEST_DIR="$DESTINO/$COMMON" ;; esac

# O projeto usa ganchos VERSIONADOS: `core.hooksPath = scripts/hooks` (relativo a raiz do repo).
# Sem isso o git olha `.git/hooks` e ignora o que esta' versionado. Garante nos dois formatos.
HOOKS_DIR="$DESTINO/scripts/hooks"
mkdir -p "$HOOKS_DIR"
install -m 0755 "$FONTE" "$HOOKS_DIR/pre-push"
git -C "$DESTINO" config core.hooksPath scripts/hooks
# copia tambem para .git/hooks (funciona em clone onde core.hooksPath nao esteja setado)
mkdir -p "$DEST_DIR/hooks"
install -m 0755 "$FONTE" "$DEST_DIR/hooks/pre-push"

echo "gancho instalado em:"
echo "  $HOOKS_DIR/pre-push        (versionado; e' o que core.hooksPath usa)"
echo "  $DEST_DIR/hooks/pre-push   (fallback)"
echo "  fonte: $FONTE"
echo "  vale para o repo $DESTINO (todos os worktrees dele)."
echo "  teste: git -C $DESTINO push --dry-run origin HEAD:main   (deve RECUSAR)"
