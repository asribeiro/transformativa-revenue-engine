#!/usr/bin/env bash
# Instala os hooks do repositorio (idempotente).
set -e
cd "$(dirname "$0")/.."
git config core.hooksPath scripts/hooks
chmod +x scripts/hooks/* 2>/dev/null || true
echo "hooks instalados: core.hooksPath=$(git config --get core.hooksPath)"
