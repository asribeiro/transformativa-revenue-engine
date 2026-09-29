#!/usr/bin/env bash
# Procura segredos versionados. Uso: bash scripts/secret_scan.sh
PADROES='ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|password\s*=\s*[^\s#]|api[_-]?key\s*=\s*[^\s#]'
if git grep -nIE "$PADROES" -- . 2>/dev/null | grep -v '.env.example' | grep -v 'secret_scan.sh'; then
  echo "RESULTADO: FALHOU (segredo encontrado)"; exit 1
fi
echo "RESULTADO: PASS (nenhum segredo versionado)"; exit 0
