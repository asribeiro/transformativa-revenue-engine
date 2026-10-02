#!/usr/bin/env bash
# Aceite TRE-W0-E01-T01: confere a estrutura obrigatória do repositorio.
set -u
FALHAS=0
DIRS="docs/architecture docs/data docs/integrations docs/business docs/testing docs/operations docs/adr \
docs/runbooks docs/releases docs/kanban db/migrations db/tests odoo/addons/transformativa_sales_ai \
n8n/workflows n8n/contracts hermes/agents hermes/prompts hermes/policies hermes/jev/routing \
hermes/jev/benchmarks hermes/jev/receipts scripts tests"
ARQS="README.md BRANCHING.md .gitignore .env.example"
for d in $DIRS; do
  if [ -d "$d" ]; then echo "OK    dir  $d"; else echo "FALHOU dir $d"; FALHAS=$((FALHAS+1)); fi
done
for a in $ARQS; do
  if [ -f "$a" ]; then echo "OK    arq  $a"; else echo "FALHOU arq $a"; FALHAS=$((FALHAS+1)); fi
done
ADR=$(ls docs/adr/ADR-*.md 2>/dev/null | wc -l)
if [ "$ADR" -ge 6 ]; then echo "OK    ADRs iniciais ($ADR)"; else echo "FALHOU ADRs iniciais ($ADR < 6)"; FALHAS=$((FALHAS+1)); fi
# Artefatos que PRECISAM estar versionados. A regra "data/" do .gitignore ja engoliu docs/data/
# e o Data Contract V1 foi commitado sem os proprios documentos — este check teria pego.
for f in docs/data/DATA_CONTRACT_V1.md docs/data/data_contract_v1.json \
         db/migrations/0001_sales_intelligence_v1.sql CHANGELOG.md; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    echo "OK    versionado  $f"
  else
    echo "FALHOU versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"
    FALHAS=$((FALHAS+1))
  fi
done
# DEFEITO CORRIGIDO (TRE-W1-E04-T01, achado por leitura do proprio verificador): aqui existia
# `echo "---" ; if FALHAS==0 -> PASS e exit`. Esse `exit` no MEIO do script matava todo o resto
# do arquivo: os blocos de artefato versionado (backup/T03, JEV policy, dedup, processo de
# defeitos) nunca rodavam e o script imprimia PASS mesmo com artefato fora do git — aceite falso.
# O resumo e o exit passaram para o FIM do script; nenhum check abaixo ficou inalcancavel.

# Artefatos do backup/restore (T03) existem E estao versionados
for f in scripts/backup/backup-tre.sh scripts/backup/verificar-backup.sh \
         scripts/backup/restore-tre.sh scripts/backup/teste-backup-restore.sh \
         docs/runbooks/backup-restore-rollback.md deploy/systemd/tre-backup.timer; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done

# Artefatos da JEV Decision Policy V1 (E04-T01) existem E estao versionados
for f in hermes/jev/policy_v1.yaml docs/architecture/jev-decision-policy-v1.md scripts/verificar_jev_policy.py; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done

# Artefatos da deduplicacao strong identifiers (TRE-W1-E04-T01) existem E estao versionados
for f in scripts/dedup/deduplicar_organizacoes.py scripts/dedup/teste_dedup_sintetico.sh \
         scripts/dedup/teste_dedup_ambiente.sh docs/runbooks/deduplicacao-strong-identifiers.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done

# Artefatos do campo entity_match_confidence (TRE-W1-E04-T02) existem E estao versionados
for f in scripts/dedup/teste_entity_match_confidence.sh docs/data/entity-match-confidence.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/dedup/teste_entity_match_confidence.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Artefatos da suite de teste do banco (TRE-W1-E05-T01) existem E estao versionados
for f in scripts/db/suite_banco.sh scripts/db/teste_isolamento_clientes.sh \
         scripts/db/teste_tenant_rls.sh docs/runbooks/suite-de-teste-do-banco.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/db/suite_banco.sh scripts/db/teste_isolamento_clientes.sh scripts/db/teste_tenant_rls.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Processo de defeitos (card -> defeito -> correcao -> liberacao) versionado
for f in docs/kanban/processo-de-defeitos.md scripts/kanban/abrir-defeito.sh \
         scripts/kanban/fechar-defeito.sh scripts/kanban/listar-defeitos.sh; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/kanban/abrir-defeito.sh scripts/kanban/fechar-defeito.sh scripts/kanban/listar-defeitos.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Artefatos do Titan SMTP (TRE-W6-E01-T01) existem E estao versionados
for f in hermes/integracoes/titan/smtp_titan.py hermes/integracoes/titan/titan-smtp-v1.json \
         scripts/integracoes/sink-smtp-dev.py scripts/integracoes/verificar_smtp_titan.py \
         scripts/integracoes/teste_smtp_titan_aceite.sh scripts/integracoes/mutar_smtp_titan.py \
         deploy/environments/dev-smtp.env docs/integrations/titan-smtp-v1.md docs/runbooks/titan-smtp.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done
# O dev-harness NAO tem credencial Titan: o ambiente de dev do aceite nao pode carregar senha nem
# apontar para host que nao seja loopback.
if grep -qE '^TRE_TITAN_PASSWORD=.+$' deploy/environments/dev-smtp.env; then
  echo "FALHOU dev-smtp.env carrega valor em TRE_TITAN_PASSWORD (dev-harness nao tem TRE_TITAN_*)"
  FALHAS=$((FALHAS+1))
elif grep -qE '^TRE_TITAN_SMTP_HOST=(127\.0\.0\.1|localhost)$' deploy/environments/dev-smtp.env; then
  echo "OK    dev-smtp.env sem senha e com host de sink local (loopback)"
else
  echo "FALHOU dev-smtp.env sem host loopback (a prova de dev e contra sink local, ADR-005)"
  FALHAS=$((FALHAS+1))
fi

# Artefatos do Titan IMAP (TRE-W6-E01-T02) existem E estao versionados
for f in hermes/integracoes/titan/imap_titan.py hermes/integracoes/titan/titan-imap-v1.json \
         scripts/integracoes/sink-imap-dev.py scripts/integracoes/verificar_imap_titan.py \
         scripts/integracoes/teste_imap_titan_aceite.sh scripts/integracoes/mutar_imap_titan.py \
         deploy/environments/dev-imap.env docs/integrations/titan-imap-v1.md docs/runbooks/titan-imap.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done
# Mesma regra do SMTP para o ambiente de dev do IMAP: sem senha versionada e com host de sink local.
if grep -qE '^TRE_TITAN_PASSWORD=.+$' deploy/environments/dev-imap.env; then
  echo "FALHOU dev-imap.env carrega valor em TRE_TITAN_PASSWORD (dev-harness nao tem TRE_TITAN_*)"
  FALHAS=$((FALHAS+1))
elif grep -qE '^TRE_TITAN_IMAP_HOST=(127\.0\.0\.1|localhost)$' deploy/environments/dev-imap.env; then
  echo "OK    dev-imap.env sem senha e com host de sink local (loopback)"
else
  echo "FALHOU dev-imap.env sem host loopback (a prova de dev e contra sink local, ADR-005)"
  FALHAS=$((FALHAS+1))
fi
# O IMAP le a caixa: o invariante de leitura tem de estar no componente (EXAMINE + PEEK) — se alguem
# trocar por SELECT de escrita ou por busca sem PEEK, a estrutura acusa antes do aceite.
if grep -q 'readonly=True' hermes/integracoes/titan/imap_titan.py \
   && grep -q 'BODY.PEEK' hermes/integracoes/titan/imap_titan.py \
   && ! grep -qE 'readonly[[:space:]]*=[[:space:]]*False' hermes/integracoes/titan/imap_titan.py; then
  echo "OK    imap_titan.py abre a caixa em EXAMINE e busca o conteudo por BODY.PEEK"
else
  echo "FALHOU imap_titan.py sem o invariante de leitura (EXAMINE + BODY.PEEK)"
  FALHAS=$((FALHAS+1))
fi

echo "---"
if [ "$FALHAS" -eq 0 ]; then echo "RESULTADO: PASS (0 falhas)"; exit 0; else echo "RESULTADO: FALHOU ($FALHAS)"; exit 1; fi
