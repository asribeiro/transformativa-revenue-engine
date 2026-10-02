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

# Artefatos do Odoo Community em dev (TRE-W2-E01-T01) existem E estao versionados
for f in deploy/compose/dev/odoo.yml deploy/environments/dev-odoo.env \
         scripts/provision/instalar-odoo-dev.sh scripts/provision/verificar-odoo-dev.sh \
         scripts/provision/remover-odoo-dev.sh docs/runbooks/odoo-dev.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/provision/instalar-odoo-dev.sh scripts/provision/verificar-odoo-dev.sh \
         scripts/provision/remover-odoo-dev.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done
# O par do Odoo nao carrega segredo: a senha vive so na VPS (politica de secrets V1). Este
# teste barato pega copia/cola de credencial para dentro do artefato.
if grep -qiE '(passwd|password|senha)[[:space:]]*=[[:space:]]*[^[:space:]#]' deploy/environments/dev-odoo.env 2>/dev/null; then
  echo "FALHOU deploy/environments/dev-odoo.env carrega valor de senha (segredo nao vai para o artefato)"
  FALHAS=$((FALHAS+1))
else
  echo "OK    par do Odoo sem valor de senha"
fi
# Artefatos do modulo Odoo (TRE-W2-E03-T01, TRE-W2-E05-T01, TRE-W2-E07-T01 e TRE-W2-E06-T01)
# existem E estao versionados: o modulo (`transformativa_sales_ai`) e as ferramentas de aceite.
# Sem isto o aceite do card pode passar na VPS por arquivo que nunca entrou no repo.
for f in odoo/addons/transformativa_sales_ai/__manifest__.py \
         odoo/addons/transformativa_sales_ai/__init__.py \
         odoo/addons/transformativa_sales_ai/README.md \
         odoo/addons/transformativa_sales_ai/models/__init__.py \
         odoo/addons/transformativa_sales_ai/models/res_partner.py \
         odoo/addons/transformativa_sales_ai/models/crm_lead.py \
         odoo/addons/transformativa_sales_ai/models/tf_process_opportunity.py \
         odoo/addons/transformativa_sales_ai/security/transformativa_sales_ai_security.xml \
         odoo/addons/transformativa_sales_ai/security/ir.model.access.csv \
         odoo/addons/transformativa_sales_ai/views/tf_process_opportunity_views.xml \
         odoo/addons/transformativa_sales_ai/views/res_partner_views.xml \
         odoo/addons/transformativa_sales_ai/views/crm_lead_views.xml \
         odoo/addons/transformativa_sales_ai/tests/__init__.py \
         odoo/addons/transformativa_sales_ai/tests/test_modulo_base.py \
         odoo/addons/transformativa_sales_ai/tests/test_res_partner_dedup.py \
         odoo/addons/transformativa_sales_ai/tests/test_crm_lead_rastreio.py \
         odoo/addons/transformativa_sales_ai/tests/test_oportunidade_canonica.py \
         odoo/addons/transformativa_sales_ai/tests/test_acl_seguranca.py \
         odoo/addons/transformativa_sales_ai/tests/test_views_sales_ai.py \
         scripts/odoo/verificar-modulo-odoo.sh scripts/odoo/manifesto_do_modulo.py \
         scripts/odoo/desinstalar_modulo.py scripts/odoo/verificar-acl-modulo.sh \
         scripts/odoo/provar_acl_modulo.py scripts/odoo/conferir_res_partner_no_contrato.py \
         scripts/odoo/medir_res_partner.py scripts/odoo/verificar-res-partner.sh \
         scripts/odoo/conferir_crm_lead_no_contrato.py scripts/odoo/medir_crm_lead.py \
         scripts/odoo/verificar-crm-lead-odoo.sh \
         scripts/odoo/verificar-views-sales-ai.sh scripts/odoo/provar_views_sales_ai.py \
         odoo/addons/transformativa_sales_ai/api/__init__.py \
         odoo/addons/transformativa_sales_ai/api/motor.py \
         odoo/addons/transformativa_sales_ai/api/politica_api.json \
         odoo/addons/transformativa_sales_ai/controllers/__init__.py \
         odoo/addons/transformativa_sales_ai/controllers/api_controlada.py \
         odoo/addons/transformativa_sales_ai/tests/test_api_controlada.py \
         odoo/addons/transformativa_sales_ai/tests/test_empresa_upsert.py \
         odoo/addons/transformativa_sales_ai/tests/test_contato_upsert.py \
         odoo/addons/transformativa_sales_ai/tests/test_oportunidade_upsert.py \
         odoo/addons/transformativa_sales_ai/tests/test_atividade_criar.py \
         odoo/addons/transformativa_sales_ai/tests/politicas/politica_de_teste.json \
         odoo/addons/transformativa_sales_ai/tests/politicas/politica_invalida.json \
         scripts/odoo/verificar-api-controlada.sh scripts/odoo/testar_motor_api.py \
         scripts/odoo/verificar-empresa-upsert.sh docs/runbooks/odoo-empresa-upsert.md \
         scripts/odoo/verificar-contato-upsert.sh docs/runbooks/odoo-contato-upsert.md \
         scripts/odoo/verificar-oportunidade-upsert.sh \
         scripts/odoo/verificar-atividade-criar.sh \
         scripts/odoo/preparar_api_teste.py docs/runbooks/odoo-api-controlada.md \
         docs/runbooks/odoo-oportunidade-upsert.md \
         docs/runbooks/odoo-atividade-criar.md \
         docs/runbooks/odoo-modulo-sales-ai.md docs/runbooks/odoo-oportunidade-canonica.md \
         docs/runbooks/odoo-acl-seguranca.md docs/runbooks/res-partner-campos-dedup.md \
         docs/runbooks/odoo-crm-lead-sales-ai.md docs/runbooks/odoo-views-sales-ai.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
# O bit executavel vive no GIT (100755 x 100644): script chamado direto (pelos dentes do
# proprio verificador, por um unit ou por outro script) tem de ser executavel, e corrigir so a
# copia operacional nao sobrevive ao proximo deploy (mesma licao do defeito t_22c27625).
for f in scripts/odoo/verificar-modulo-odoo.sh scripts/odoo/verificar-acl-modulo.sh \
         scripts/odoo/provar_acl_modulo.py scripts/odoo/verificar-crm-lead-odoo.sh \
         scripts/odoo/verificar-res-partner.sh scripts/odoo/verificar-views-sales-ai.sh \
         scripts/odoo/verificar-api-controlada.sh scripts/odoo/testar_motor_api.py \
         scripts/odoo/verificar-empresa-upsert.sh scripts/odoo/verificar-contato-upsert.sh \
         scripts/odoo/verificar-oportunidade-upsert.sh scripts/odoo/verificar-atividade-criar.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done
# O manifesto do modulo nao pode carregar credencial nem nome de banco (o aceite roda em
# banco descartavel criado na hora; nome de banco com valor fixo no manifesto e' vazamento de ambiente).
if grep -qiE '(passwd|password|senha|db_password|api_key|token)[[:space:]]*=[[:space:]]*[^[:space:]#]' \
     odoo/addons/transformativa_sales_ai/__manifest__.py 2>/dev/null; then
  echo "FALHOU manifesto do modulo carrega valor de senha (segredo nao vai para o artefato)"
  FALHAS=$((FALHAS+1))
else
  echo "OK    manifesto do modulo sem valor de senha"
fi

# Artefatos do consumidor de outbox em n8n (TRE-W3-E02-T01 e TRE-W3-E02-T02) existem E estao
# versionados: o workflow e' artefato DERIVADO (contrato + nucleo + SQL) e sem estes arquivos o aceite
# do card poderia passar na VPS por arquivo que nunca entrou no repo.
# DEFEITO CORRIGIDO (card t_a1bed5fa, revisao independente do TRE-W3-E02-T02): o T02 acrescentou
# dois SQL (dedup por chave) e nao estendeu esta lista — o gate imprimia PASS com eles fora da arvore
# versionada. Um arquivo por linha de proposito: o aceite da classe mede `grep -c` (linhas, nao
# ocorrencias) e os dois na mesma linha contariam 1.
for f in n8n/contracts/outbox-consumer.v1.json n8n/codigo/nucleo-outbox-consumer.js \
         n8n/sql/ler-pendentes.sql n8n/sql/registrar-resultado.sql \
         n8n/sql/ler-trilha.sql \
         n8n/sql/registrar-replay.sql \
         n8n/workflows/TRE-outbox-consumer.json scripts/n8n/montar_workflow.py \
         scripts/n8n/conferir_contrato_e_workflow.py scripts/n8n/testar_nucleo_consumidor.js \
         scripts/n8n/mutar_workflow.py scripts/n8n/preparar_massa_ambigua.py \
         scripts/n8n/verificar-outbox-consumer.sh docs/runbooks/n8n-outbox-consumer.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/n8n/verificar-outbox-consumer.sh scripts/n8n/montar_workflow.py \
         scripts/n8n/conferir_contrato_e_workflow.py scripts/n8n/mutar_workflow.py; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Artefatos do job diario de reconciliacao (TRE-W3-E04-T01) existem E estao versionados. Mesma
# classe do bloco do consumidor de outbox: o workflow e' DERIVADO do contrato + nucleo + SQL, e sem
# a lista o gate imprimiria PASS com o card fora da arvore versionada. Um arquivo por linha.
# Artefatos da OBSERVABILIDADE de sync (TRE-W3-E05-T01): o workflow de observabilidade tambem e'
# DERIVADO (contrato + SQL + nucleo) e a medicao so' vale se o que foi medido na VPS estiver
# versionado — inclusive o aceite, o montador, o mutador e o RUNBOOK (quem opera precisa do
# procedimento versionado, nao de conhecimento de sessao).
# Consolidacao da onda W3 (TRE-W3-E06-T01): as duas listas dos cards-irmaos entram JUNTAS aqui —
# o merge da onda nao pode deixar nenhum artefato fora do gate.
for f in n8n/contracts/reconciliation-job.v1.json n8n/codigo/nucleo-reconciliacao.js \
         n8n/sql/reconciliacao-origem.sql \
         n8n/sql/reconciliacao-pendentes.sql \
         n8n/workflows/TRE-reconciliation.json scripts/n8n/montar_workflow_reconciliacao.py \
         scripts/n8n/conferir_reconciliacao.py scripts/n8n/testar_nucleo_reconciliacao.js \
         scripts/n8n/mutar_reconciliacao.py scripts/n8n/ler_resultado_reconciliacao.py \
         scripts/n8n/massa-reconciliacao.sql scripts/odoo/massa_reconciliacao.py \
         scripts/n8n/verificar-reconciliacao.sh docs/runbooks/n8n-reconciliacao.md \
         n8n/contracts/observabilidade-sync.v1.json n8n/codigo/observabilidade-sync.js \
         n8n/sql/observabilidade-sync.sql n8n/sql/observabilidade-sync-dead-letters.sql \
         n8n/workflows/TRE-observabilidade-sync.json \
         scripts/n8n/montar_workflow_observabilidade.py scripts/n8n/mutar_workflow_observabilidade.py \
         scripts/n8n/conferir_observabilidade.py scripts/n8n/testar_observabilidade_sync.js \
         scripts/n8n/massa-observabilidade.sql scripts/n8n/ler_resultado_n8n.py \
         scripts/n8n/normalizar_medicao.py scripts/n8n/verificar-observabilidade-sync.sh \
         docs/runbooks/observabilidade-sync.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/n8n/verificar-reconciliacao.sh scripts/n8n/montar_workflow_reconciliacao.py \
         scripts/n8n/conferir_reconciliacao.py scripts/n8n/mutar_reconciliacao.py \
         scripts/n8n/ler_resultado_reconciliacao.py scripts/odoo/massa_reconciliacao.py \
         scripts/n8n/verificar-observabilidade-sync.sh scripts/n8n/montar_workflow_observabilidade.py \
         scripts/n8n/mutar_workflow_observabilidade.py scripts/n8n/conferir_observabilidade.py \
         scripts/n8n/ler_resultado_n8n.py scripts/n8n/normalizar_medicao.py; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# E2E Foundation #001 (TRE-W3-E06-T01): o aceite que encadeia as quatro portas da onda num unico
# trio descartavel — se este artefato sumir, some a unica medicao ponta a ponta da fundacao.
for f in scripts/e2e/verificar-e2e-foundation-001.sh docs/runbooks/e2e-foundation-001.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/e2e/verificar-e2e-foundation-001.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Agente Scout v1 (TRE-W4-E01-T01): primeiro agente da W4 e primeiro produtor de organizacao.
# Se o contrato do agente, o codigo, a suite, o aceite ou o runbook sumirem do git, o card
# entrega fica sem artefato verificavel — o gate reprova por nome.
for f in hermes/agents/scout/scout.py hermes/agents/scout/agente-scout-v1.json \
         hermes/agents/scout/exemplos/candidatas-exemplo.jsonl \
         scripts/agentes/verificar_agente_scout.py scripts/agentes/teste_scout_aceite.sh \
         docs/architecture/agente-scout-v1.md docs/runbooks/agente-scout.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/agentes/teste_scout_aceite.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Agente Research v1 (TRE-W4-E02-T01): segundo agente da W4 e o produtor da PESQUISA
# (`research_runs`) e do enriquecimento da empresa descoberta. Se o contrato do agente, o codigo,
# a suite, o aceite ou o runbook sumirem do git, o card entrega fica sem artefato verificavel —
# o gate reprova por nome. Um arquivo por linha de proposito: o aceite da classe mede `grep -c`.
for f in hermes/agents/research/research.py hermes/agents/research/agente-research-v1.json \
         hermes/agents/research/exemplos/pesquisas-exemplo.jsonl \
         scripts/agentes/verificar_agente_research.py scripts/agentes/teste_research_aceite.sh \
         docs/architecture/agente-research-v1.md docs/runbooks/agente-research.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/agentes/teste_research_aceite.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

echo "---"
if [ "$FALHAS" -eq 0 ]; then echo "RESULTADO: PASS (0 falhas)"; exit 0; else echo "RESULTADO: FALHOU ($FALHAS)"; exit 1; fi
