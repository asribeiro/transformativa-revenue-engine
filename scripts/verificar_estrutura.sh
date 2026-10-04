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

# Agente Signal Detector v1 (TRE-W4-E03-T01): terceiro agente da W4 e o produtor do SINAL
# (`signals`) — resolve a empresa que JA existe pelos identificadores fortes do contrato, deriva a
# categoria do tipo e grava o fato datado com evidencia, sem criar organizacao e sem calcular score
# (Buying Signal Score e W5). Se o contrato do agente, o codigo, a suite, o aceite ou o runbook
# sumirem do git, o card entrega fica sem artefato verificavel — o gate reprova por nome.
# Agente Contact Research v1 (TRE-W4-E05-T01): o produtor do CONTATO comercial (`contacts`) da
# empresa ja' pesquisada. Mesmo portao por nome dos irmaos: contrato, codigo, exemplo de fonte,
# suite offline, aceite E2E, arquitetura e runbook, todos versionados. Um arquivo por linha de
# proposito: o aceite da classe mede `grep -c`.
for f in hermes/agents/signal/signal.py hermes/agents/signal/agente-signal-v1.json \
         hermes/agents/signal/exemplos/observacoes-exemplo.jsonl \
         scripts/agentes/verificar_agente_signal.py scripts/agentes/teste_signal_aceite.sh \
         hermes/agents/contact_research/contact_research.py \
         hermes/agents/contact_research/agente-contact-research-v1.json \
         hermes/agents/contact_research/exemplos/contatos-exemplo.jsonl \
         scripts/agentes/verificar_agente_contact_research.py \
         scripts/agentes/teste_contact_research_aceite.sh \
         docs/architecture/agente-signal-v1.md docs/runbooks/agente-signal.md \
         docs/architecture/agente-contact-research-v1.md docs/runbooks/agente-contact-research.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/agentes/teste_signal_aceite.sh \
         scripts/agentes/teste_contact_research_aceite.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Agente Pain Hypothesis v1 (TRE-W4-E04-T01): quarto agente da W4 e o produtor da HIPOTESE DE DOR
# (`pain_hypotheses`) — resolve a empresa que JA existe pelos identificadores fortes do contrato,
# CONFERE que o lastro declarado existe no banco e e da MESMA empresa (SINAL -> signals.id,
# PESQUISA -> research_runs.id) e grava a hipotese marcada como inferencia, sem criar organizacao,
# sem score de impacto e sem validar status (ato humano). Se o contrato do agente, o codigo, a
# suite, o aceite ou o runbook sumirem do git, o card entrega fica sem artefato verificavel — o
# gate reprova por nome.
for f in hermes/agents/pain_hypothesis/pain_hypothesis.py \
         hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json \
         hermes/agents/pain_hypothesis/exemplos/hipoteses-exemplo.jsonl \
         scripts/agentes/verificar_agente_pain_hypothesis.py scripts/agentes/teste_pain_hypothesis_aceite.sh \
         docs/architecture/agente-pain-hypothesis-v1.md docs/runbooks/agente-pain-hypothesis.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/agentes/teste_pain_hypothesis_aceite.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Aceite E2E Sales Intelligence (TRE-W4-E06-T01): o encadeamento dos cinco agentes da onda W4
# (Scout -> Research -> Signal -> Pain Hypothesis -> Contact Research) medido NUM UNICO banco
# descartavel, com o id que um agente devolve entrando como entrada do proximo. O aceite sozinho
# nao basta como artefato: sem o contrato (ACCEPTANCE/TEST/ROLLBACK/RISK) e o runbook versionados,
# quem citar o veredito nao tem onde conferir o escopo — o gate reprova por nome.
for f in scripts/e2e/verificar-e2e-sales-intelligence.sh \
         docs/architecture/e2e-sales-intelligence.md docs/runbooks/e2e-sales-intelligence.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/e2e/verificar-e2e-sales-intelligence.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Onda W5 (Scoring). Cada score entrega o MESMO conjunto de artefatos: o MODELO (contrato legivel
# por maquina, onde vivem pesos/faixas/vocabulario), o codigo, a suite offline, o aceite E2E no
# banco, o contrato do card (ACCEPTANCE/TEST/ROLLBACK/RISK no documento) e o runbook. Sem qualquer
# um destes, o veredito do score nao e' conferivel por terceiro.
for f in \
  hermes/agents/icp_score/icp_score.py \
  hermes/agents/icp_score/agente-icp-score-v1.json \
  hermes/agents/icp_score/exemplos/organizacoes-exemplo.jsonl \
  scripts/agentes/verificar_agente_icp_score.py \
  scripts/agentes/teste_icp_score_aceite.sh \
  docs/architecture/agente-icp-score-v1.md docs/runbooks/agente-icp-score.md \
  hermes/agents/automation_fit/automation_fit.py \
  hermes/agents/automation_fit/agente-automation-fit-v1.json \
  hermes/agents/automation_fit/exemplos/perfis-exemplo.jsonl \
  scripts/agentes/verificar_agente_automation_fit.py \
  scripts/agentes/teste_automation_fit_aceite.sh \
  docs/architecture/agente-automation-fit-v1.md docs/runbooks/agente-automation-fit.md \
  hermes/agents/buying_signal/buying_signal_score.py \
  hermes/agents/buying_signal/agente-buying-signal-v1.json \
  scripts/agentes/verificar_buying_signal_score.py \
  scripts/agentes/teste_buying_signal_aceite.sh \
  docs/architecture/buying-signal-score-v1.md docs/runbooks/buying-signal-score.md \
  hermes/scores/data_quality/data_quality.py \
  hermes/scores/data_quality/score-data-quality-v1.json \
  hermes/scores/data_quality/exemplos/organizacoes-exemplo.jsonl \
  scripts/scores/verificar_score_data_quality.py \
  scripts/scores/teste_data_quality_aceite.sh \
  docs/architecture/score-data-quality-v1.md docs/runbooks/score-data-quality.md \
  hermes/scores/priority/priority_score.py \
  hermes/scores/priority/score-priority-v1.json \
  hermes/scores/priority/exemplos/organizacoes-exemplo.jsonl \
  scripts/scores/verificar_score_priority.py \
  scripts/scores/teste_priority_aceite.sh \
  docs/architecture/score-priority-v1.md docs/runbooks/score-priority.md \
  hermes/scores/tiering/tiering.py \
  hermes/scores/tiering/score-tiering-v1.json \
  scripts/scores/verificar_score_tiering.py \
  scripts/scores/teste_tiering_aceite.sh \
  docs/architecture/score-tiering-v1.md docs/runbooks/score-tiering.md \
  hermes/scores/nba/nba.py \
  hermes/scores/nba/next-best-action-v1.json \
  hermes/scores/nba/politica-nba-v1.json \
  hermes/scores/nba/exemplos/empresas-exemplo.jsonl \
  scripts/scores/verificar_nba.py \
  scripts/scores/teste_nba_aceite.sh \
  docs/architecture/next-best-action-v1.md docs/runbooks/next-best-action.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/agentes/teste_icp_score_aceite.sh scripts/agentes/teste_automation_fit_aceite.sh \
         scripts/agentes/teste_buying_signal_aceite.sh scripts/scores/teste_data_quality_aceite.sh \
         scripts/scores/teste_priority_aceite.sh scripts/scores/teste_tiering_aceite.sh \
         scripts/scores/teste_nba_aceite.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Aceite E2E da cadeia W5 (TRE-W5-E08-T01): o encadeamento ICP -> AUTOMATION_FIT -> BUYING_SIGNAL ->
# DATA_QUALITY -> PRIORITY -> TIER -> NBA medido NUM UNICO banco descartavel, em que o artefato de
# cada etapa e' o insumo da seguinte (PRIORITY = formula sobre os quatro scores gravados; o registro
# TIER cita o PRIORITY lido; a recomendacao cita o tier gravado). Sem o aceite, o runbook e o
# contrato do card versionados, o veredito da onda W5 nao e' conferivel por terceiro.
for f in scripts/e2e/verificar-e2e-scoring-nba.sh docs/runbooks/e2e-scoring-nba.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/e2e/verificar-e2e-scoring-nba.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Gerador de abordagem outbound (TRE-W6-E02-T01): sem o aceite E2E, a suite offline, o prompt versionado, a
# politica e os docs do card versionados, o pedido de aprovacao PENDING e o fail-closed de compliance nao
# sao conferiveis por terceiro.
for f in hermes/agents/outreach/outreach_generator.py \
         hermes/agents/outreach/gerador-abordagem-v1.json \
         hermes/agents/outreach/politica-outreach-v1.json \
         hermes/agents/outreach/prompt-abordagem-v1.md \
         scripts/agentes/verificar_gerador_abordagem.py \
         scripts/agentes/teste_gerador_abordagem_aceite.sh \
         docs/architecture/gerador-abordagem-v1.md docs/runbooks/gerador-abordagem.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/agentes/teste_gerador_abordagem_aceite.sh; do
  if [ -x "$f" ]; then echo "OK    executavel $f"
  else echo "FALHOU sem permissao de execucao $f"; FALHAS=$((FALHAS+1)); fi
done

# Workflow de aprovacao humana do outbound (TRE-W6-E03-T01): sem o aceite E2E, a suite offline, a politica,
# o template, o contrato do componente e os docs do card versionados, o portao que autoriza abordagem a
# pessoa real nao e conferivel por terceiro.
for f in hermes/agents/outreach/approval_workflow.py \
         hermes/agents/outreach/politica-aprovacao-v1.json \
         hermes/agents/outreach/notificacao-aprovacao-v1.md \
         hermes/agents/outreach/aprovacao-humana-v1.json \
         scripts/agentes/verificar_fluxo_aprovacao.py \
         scripts/agentes/teste_fluxo_aprovacao_aceite.sh \
         docs/architecture/aprovacao-humana-v1.md docs/runbooks/aprovacao-humana.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f (arquivo existe mas nao esta no git — ignorado pelo .gitignore?)"; FALHAS=$((FALHAS+1)); fi
done
for f in scripts/agentes/teste_fluxo_aprovacao_aceite.sh; do
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

# Artefatos da ingestao de respostas (TRE-W6-E05-T01) existem E estao versionados
for f in hermes/agentes/respostas/ingestao_respostas.py hermes/agentes/respostas/ingestao-respostas-v1.json \
         scripts/agentes/verificar_ingestao_respostas.py scripts/agentes/teste_ingestao_respostas_aceite.sh \
         scripts/integracoes/gerar-fixtures-respostas.py \
         deploy/environments/dev-respostas.env docs/integrations/respostas-titan-v1.md \
         docs/runbooks/respostas-ingestao.md docs/validation/registro-de-execucoes-e05-t01.md; do
  if [ ! -f "$f" ]; then echo "FALHOU ausente $f"; FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then echo "OK    versionado $f"
  else echo "FALHOU nao versionado $f"; FALHAS=$((FALHAS+1)); fi
done
# Mesma regra dos outros ambientes de dev: sem senha versionada e com host de sink local (loopback).
if grep -qE '^TRE_TITAN_PASSWORD=.+$' deploy/environments/dev-respostas.env; then
  echo "FALHOU dev-respostas.env carrega valor em TRE_TITAN_PASSWORD (dev-harness nao tem TRE_TITAN_*)"
  FALHAS=$((FALHAS+1))
elif grep -qE '^TRE_TITAN_IMAP_HOST=(127\.0\.0\.1|localhost)$' deploy/environments/dev-respostas.env; then
  echo "OK    dev-respostas.env sem senha e com host de sink local (loopback)"
else
  echo "FALHOU dev-respostas.env sem host loopback (a prova de dev e contra sink local, ADR-005)"
  FALHAS=$((FALHAS+1))
fi
# A ingesta escreve no banco: nada de DDL no componente e o caminho de ingesta tem de EXECUTAR a
# auditoria da fonte (guard que existe e nao e chamado nao protege nada) e nao pode marcar lido.
if grep -q 'auditar_fonte()' hermes/agentes/respostas/ingestao_respostas.py \
   && ! grep -qE '^[[:space:]]*(CREATE|ALTER|DROP|TRUNCATE)[[:space:]]' hermes/agentes/respostas/ingestao_respostas.py \
   && ! grep -qE 'add_flag|[.]store[(]|Seen' hermes/agentes/respostas/ingestao_respostas.py; then
  echo "OK    ingestao_respostas.py: auditoria executada, sem DDL e sem escrita na fonte IMAP"
else
  echo "FALHOU ingestao_respostas.py sem auditoria executada / com DDL / com escrita na fonte"
  FALHAS=$((FALHAS+1))
fi

# --- card TRE-W6-E04-T01 (envio outbound v1) ---------------------------------------------------------
ENVIO="hermes/agents/outreach/send_workflow.py"
for arquivo in "$ENVIO" hermes/agents/outreach/politica-envio-v1.json \
               hermes/agents/outreach/envio-outbound-v1.json \
               scripts/agentes/verificar_envio_outbound.py scripts/agentes/duble_psql_envio.py \
               scripts/agentes/teste_envio_outbound_aceite.sh \
               docs/architecture/envio-outbound-v1.md docs/runbooks/envio-outbound.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W6-E04-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ENVIO" ]; then
  if ! python3 -m py_compile "$ENVIO" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W6-E04-T01: $ENVIO nao compila"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'sales_intelligence.interactions' "$ENVIO" || ! grep -q 'sales_intelligence.sync_events' "$ENVIO"; then
    echo "FALHOU card TRE-W6-E04-T01: escrita do envio fora das duas tabelas declaradas"
    FALHAS=$((FALHAS+1))
  fi
fi
if [ -f hermes/agents/outreach/politica-envio-v1.json ]; then
  if ! grep -q '"ddl": "recusado"' hermes/agents/outreach/politica-envio-v1.json \
     || ! grep -q '"delete": "recusado' hermes/agents/outreach/politica-envio-v1.json; then
    echo "FALHOU card TRE-W6-E04-T01: politica de envio sem ddl/delete declarados recusado"
    FALHAS=$((FALHAS+1))
  fi
fi

# --- card TRE-W6-E07-T01 (E2E Outbound #002) ---------------------------------------------------------
# O aceite da cadeia inteira: existe E esta versionado, com os 10 passos e as guardas de loopback.
ACEITE_E2E="scripts/e2e/verificar-e2e-outbound-002.sh"
for arquivo in "$ACEITE_E2E" docs/runbooks/e2e-outbound-002.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W6-E07-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W6-E07-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_E2E" ]; then
  if ! bash -n "$ACEITE_E2E" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W6-E07-T01: $ACEITE_E2E nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # Os 10 passos do doc 08 §4 tem de estar no aceite, e as pontas externas so' em loopback.
  for passo in nba.py outreach_generator.py approval_workflow.py send_workflow.py \
               ingestao_respostas.py atualizacao_odoo.py; do
    if ! grep -q "$passo" "$ACEITE_E2E"; then
      echo "FALHOU card TRE-W6-E07-T01: aceite nao encadeia $passo"
      FALHAS=$((FALHAS+1))
    fi
  done
  if ! grep -q '127.0.0.1' "$ACEITE_E2E" || ! grep -q 'TRE_AMBIENTE=prod' "$ACEITE_E2E"; then
    echo "FALHOU card TRE-W6-E07-T01: aceite sem as pontas de loopback/guarda de prod"
    FALHAS=$((FALHAS+1))
  fi
fi

# --- card TRE-W8-E01-T01 (Funil — dashboard derivado) -------------------------------------------------
CONTRATO_FUNIL="hermes/agentes/analytics/funil-v1.json"
COMPONENTE_FUNIL="hermes/agentes/analytics/funil.py"
for arquivo in "$COMPONENTE_FUNIL" "$CONTRATO_FUNIL" scripts/agentes/verificar_funil.py \
               scripts/agentes/teste_funil_aceite.sh docs/architecture/funil-v1.md docs/runbooks/funil.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W8-E01-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W8-E01-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_FUNIL" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_FUNIL" scripts/agentes/verificar_funil.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E01-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # Os estagios do componente tem de bater com a lista CONGELADA do Data Contract V1 (ordem e rotulos):
  # quem decide e' o proprio componente (`--conferir`), nao um grep deste portao.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_FUNIL" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E01-T01: --conferir recusou (estagios divergem do contrato de dados)"
    FALHAS=$((FALHAS+1))
  fi
  # Leitura pura declarada: o contrato do componente tem de declarar as guardas de ambiente e de escrita.
  for marca in 'RECUSA por desenho (exit 4)' 'READ ONLY' 'lacunas_declaradas'; do
    if ! grep -q -F "$marca" "$CONTRATO_FUNIL"; then
      echo "FALHOU card TRE-W8-E01-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f scripts/agentes/teste_funil_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_funil_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E01-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ACEITE_FUNIL_OK' scripts/agentes/teste_funil_aceite.sh; then
    echo "FALHOU card TRE-W8-E01-T01: aceite sem o marcador ACEITE_FUNIL_OK"
    FALHAS=$((FALHAS+1))
  fi
fi

# --- card TRE-W8-E03-T01 (Efetividade do score — medicao sobre o funil) ------------------------------
CONTRATO_EFETIVIDADE="hermes/agentes/analytics/efetividade-score-v1.json"
COMPONENTE_EFETIVIDADE="hermes/agentes/analytics/efetividade_score.py"
for arquivo in "$COMPONENTE_EFETIVIDADE" "$CONTRATO_EFETIVIDADE" scripts/agentes/verificar_efetividade_score.py \
               scripts/agentes/teste_efetividade_score_aceite.sh docs/architecture/efetividade-score-v1.md \
               docs/runbooks/efetividade-do-score.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W8-E03-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W8-E03-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_EFETIVIDADE" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_EFETIVIDADE" \
       scripts/agentes/verificar_efetividade_score.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E03-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # Faixas/pesos sao LIDOS do Data Contract e a dependencia (funil) e' conferida: quem decide e' o
  # proprio componente (`--conferir`), nao um grep deste portao.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_EFETIVIDADE" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E03-T01: --conferir recusou (contrato de dados ou dependencia do funil divergem)"
    FALHAS=$((FALHAS+1))
  fi
  # O desfecho NAO pode ser reimplementado: o componente tem de consumir o alcance do funil (W8-E01-T01).
  if ! grep -q "alcance_por_organizacao" "$COMPONENTE_EFETIVIDADE"; then
    echo "FALHOU card TRE-W8-E03-T01: componente nao consome o alcance por organizacao do funil"
    FALHAS=$((FALHAS+1))
  fi
  for marca in 'RECUSA por desenho (exit 4)' 'READ ONLY' 'lacunas_declaradas' 'base_suficiente'; do
    if ! grep -q -F "$marca" "$CONTRATO_EFETIVIDADE"; then
      echo "FALHOU card TRE-W8-E03-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
# A extensao do pai (exportacao aditiva) tem de estar declarada no contrato do funil.
if [ -f "$CONTRATO_FUNIL" ]; then
  if ! grep -q "por_organizacao" "$CONTRATO_FUNIL"; then
    echo "FALHOU card TRE-W8-E03-T01: contrato do funil sem a exportacao por organizacao declarada"
    FALHAS=$((FALHAS+1))
  fi
fi
if [ -f scripts/agentes/teste_efetividade_score_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_efetividade_score_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E03-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ACEITE_EFETIVIDADE_SCORE_OK' scripts/agentes/teste_efetividade_score_aceite.sh; then
    echo "FALHOU card TRE-W8-E03-T01: aceite sem o marcador ACEITE_EFETIVIDADE_SCORE_OK"
    FALHAS=$((FALHAS+1))
  fi
  # O aceite deste card exige o aceite do pai (prova de que a extensao nao mudou o funil-v1).
  if ! grep -q 'ACEITE_FUNIL_OK' scripts/agentes/teste_efetividade_score_aceite.sh; then
    echo "FALHOU card TRE-W8-E03-T01: aceite sem a regressao do pai (ACEITE_FUNIL_OK)"
    FALHAS=$((FALHAS+1))
  fi
fi

# --- card TRE-W9-E01-T01 (Calibracao do score — proposta, nunca aplicacao) -----------------------------
CONTRATO_CALIBRACAO="hermes/agentes/analytics/calibracao-score-v1.json"
COMPONENTE_CALIBRACAO="hermes/agentes/analytics/calibracao_score.py"
for arquivo in "$COMPONENTE_CALIBRACAO" "$CONTRATO_CALIBRACAO" scripts/agentes/verificar_calibracao_score.py \
               scripts/agentes/teste_calibracao_score_aceite.sh docs/architecture/calibracao-score-v1.md \
               docs/runbooks/calibracao-do-score.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W9-E01-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W9-E01-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_CALIBRACAO" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_CALIBRACAO" \
       scripts/agentes/verificar_calibracao_score.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E01-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # Gate, grade e dependencias sao LIDOS do contrato e conferidos pelo proprio componente.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_CALIBRACAO" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E01-T01: --conferir recusou (contrato de dados, instrumento ou funil divergem)"
    FALHAS=$((FALHAS+1))
  fi
  # A medicao e o desfecho NAO podem ser reimplementados: tem de consumir o instrumento (W8-E03) e o funil.
  for marca in "carregar_instrumento" "ler_tudo" "alcance_por_organizacao" "CODIGO_VOLUME"; do
    if ! grep -q "$marca" "$COMPONENTE_CALIBRACAO"; then
      echo "FALHOU card TRE-W9-E01-T01: componente sem $marca (dependencia do instrumento/funil)"
      FALHAS=$((FALHAS+1))
    fi
  done
  for marca in '"aplicado": False' '"exige_versao_nova": True' 'pendente'; do
    if ! grep -q -F "$marca" "$COMPONENTE_CALIBRACAO"; then
      echo "FALHOU card TRE-W9-E01-T01: componente sem a marca de proposta NAO aplicada ($marca)"
      FALHAS=$((FALHAS+1))
    fi
  done
  for marca in 'RECUSA por desenho (exit 4' 'READ ONLY' 'base_suficiente' 'minimo_de_coorte' \
               'margem_de_ganho_na_validacao' 'lacunas_declaradas'; do
    if ! grep -q -F "$marca" "$CONTRATO_CALIBRACAO"; then
      echo "FALHOU card TRE-W9-E01-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f scripts/agentes/teste_calibracao_score_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_calibracao_score_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E01-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # O aceite tem de declarar o marcador proprio E a regressao da dependencia (instrumento W8-E03).
  for marca in 'ACEITE_CALIBRACAO_SCORE_OK' 'VERIFICADOR_EFETIVIDADE_PASS' 'exit 6' 'sha256'; do
    if ! grep -q -F "$marca" scripts/agentes/teste_calibracao_score_aceite.sh; then
      echo "FALHOU card TRE-W9-E01-T01: aceite sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi

# --- card TRE-W9-E02-T01 (Pontuacao preditiva — probabilidade derivada, nunca aplicada) ---------------
CONTRATO_PONTUACAO="hermes/agentes/analytics/pontuacao-preditiva-v1.json"
COMPONENTE_PONTUACAO="hermes/agentes/analytics/pontuacao_preditiva.py"
for arquivo in "$COMPONENTE_PONTUACAO" "$CONTRATO_PONTUACAO" scripts/agentes/verificar_pontuacao_preditiva.py \
               scripts/agentes/teste_pontuacao_preditiva_aceite.sh docs/architecture/pontuacao-preditiva-v1.md \
               docs/runbooks/pontuacao-preditiva.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W9-E02-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W9-E02-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_PONTUACAO" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_PONTUACAO" \
       scripts/agentes/verificar_pontuacao_preditiva.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E02-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # O corte ajuste/validacao e os pesos vem do CONTRATO e do RELATORIO da calibracao (dependencia medida).
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_PONTUACAO" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E02-T01: --conferir recusou (contrato de dados, instrumento ou funil divergem)"
    FALHAS=$((FALHAS+1))
  fi
  # A medicao, o desfecho e a coorte NAO podem ser reimplementados: tem de consumir calibracao -> instrumento -> funil.
  for marca in "carregar_calibracao" "ler_tudo" "montar_coorte" "separar_lados" "CODIGO_VOLUME" \
               "DEPENDENCIA_CALIBRACAO" "CORTE_DIVERGENTE"; do
    if ! grep -q "$marca" "$COMPONENTE_PONTUACAO"; then
      echo "FALHOU card TRE-W9-E02-T01: componente sem $marca (dependencia calibracao/instrumento)"
      FALHAS=$((FALHAS+1))
    fi
  done
  for marca in '"aplicado": False' '"exige_versao_nova": True' 'pendente'; do
    if ! grep -q -F "$marca" "$COMPONENTE_PONTUACAO"; then
      echo "FALHOU card TRE-W9-E02-T01: componente sem a marca de previsao NAO aplicada ($marca)"
      FALHAS=$((FALHAS+1))
    fi
  done
  # A leitura pura nao pode virar SQL proprio (seria uma segunda verdade sobre o mesmo numero).
  if grep -qE "SELECT |INSERT |UPDATE |DELETE " "$COMPONENTE_PONTUACAO"; then
    echo "FALHOU card TRE-W9-E02-T01: componente com SQL proprio (leitura e' do instrumento)"
    FALHAS=$((FALHAS+1))
  fi
  for marca in 'RECUSA por desenho (exit 4' 'READ ONLY' 'base_suficiente' 'minimo_de_coorte' \
               'bins_da_curva' 'fracao_de_ajuste' 'lacunas_declaradas'; do
    if ! grep -q -F "$marca" "$CONTRATO_PONTUACAO"; then
      echo "FALHOU card TRE-W9-E02-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f scripts/agentes/teste_pontuacao_preditiva_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_pontuacao_preditiva_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E02-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # O aceite tem de declarar o marcador proprio E a regressao da dependencia (calibracao W9-E01-T01).
  for marca in 'ACEITE_PONTUACAO_PREDITIVA' 'VERIFICADOR_PONTUACAO_PASS' \
               'VERIFICADOR_CALIBRACAO_PASS' 'exit 6' 'sha256'; do
    if ! grep -q -F "$marca" scripts/agentes/teste_pontuacao_preditiva_aceite.sh; then
      echo "FALHOU card TRE-W9-E02-T01: aceite sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi


# --- card TRE-W7-E01-T01 (captura de lead do site) ---------------------------------------------------
# O componente existe E esta' versionado, com o contrato fechado, a barreira de consentimento declarada,
# a guarda de producao e o portao do entregavel (suite + aceite).
for arquivo in hermes/agentes/inbound/captura_site.py hermes/agentes/inbound/captura-site-v1.json \
               scripts/agentes/verificar_captura_site.py scripts/agentes/teste_captura_site_aceite.sh \
               docs/runbooks/captura-de-lead-do-site.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W7-E01-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W7-E01-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f hermes/agentes/inbound/captura_site.py ]; then
  if grep -q 'auditar_fonte()' hermes/agentes/inbound/captura_site.py \
     && ! grep -qE '^[[:space:]]*(CREATE|ALTER|DROP|TRUNCATE)[[:space:]]' hermes/agentes/inbound/captura_site.py \
     && grep -q 'PRODUCAO_RECUSADA' hermes/agentes/inbound/captura_site.py \
     && grep -q 'BANCO_NAO_E_DEV' hermes/agentes/inbound/captura_site.py; then
    echo "OK    captura_site.py: auditoria de fonte, sem DDL e com guardas de prod/dev"
  else
    echo "FALHOU card TRE-W7-E01-T01: componente sem auditoria de fonte / com DDL / sem guardas"
    FALHAS=$((FALHAS+1))
  fi
fi
if [ -f hermes/agentes/inbound/captura-site-v1.json ]; then
  python3 - <<'PY' || { echo "FALHOU card TRE-W7-E01-T01: contrato sem a barreira de consentimento"; FALHAS=$((FALHAS+1)); }
import json, sys
c = json.load(open("hermes/agentes/inbound/captura-site-v1.json", encoding="utf-8"))
obrig = c["submissao"]["campos_obrigatorios"]
vocab = c["vocabulario"]
ok = ("consentimento.aceito" in obrig and "consentimento.legal_basis" in obrig
      and "RECUSADO_CONSENTIMENTO" in vocab["status_trilha"]
      and float(c["identificadores"]["limiar_de_merge_automatico"]) == 0.95
      and c["lacunas"])
sys.exit(0 if ok else 1)
PY
fi
if [ -f scripts/agentes/teste_captura_site_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_captura_site_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W7-E01-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q '127.0.0.1\|loopback' scripts/agentes/teste_captura_site_aceite.sh \
     || ! grep -q 'TRE_AMBIENTE=dev' scripts/agentes/teste_captura_site_aceite.sh \
     || ! grep -q 'RECUSADO_CONSENTIMENTO' scripts/agentes/teste_captura_site_aceite.sh; then
    echo "FALHOU card TRE-W7-E01-T01: aceite sem as pontas locais/guarda de ambiente/barreira de consentimento"
    FALHAS=$((FALHAS+1))
  fi
fi
python3 scripts/agentes/verificar_captura_site.py >/dev/null 2>&1 \
  && echo "OK    suite do componente verde (verificar_captura_site.py)" \
  || { echo "FALHOU card TRE-W7-E01-T01: suite do componente nao passa"; FALHAS=$((FALHAS+1)); }


# --- card TRE-W7-E02-T01 (ingestao de leads Meta) ----------------------------------------------------
for arquivo in hermes/agentes/inbound/ingestao_leads_meta.py \
               hermes/agentes/inbound/meta-lead-ingestion-v1.json \
               scripts/agentes/verificar_ingestao_leads_meta.py \
               scripts/agentes/stub-meta-graph-dev.py \
               scripts/agentes/teste_ingestao_leads_meta_aceite.sh \
               deploy/environments/dev-meta.env \
               docs/integrations/meta-leads-v1.md \
               docs/runbooks/ingestao-leads-meta.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W7-E02-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W7-E02-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
ACEITE_META=scripts/agentes/teste_ingestao_leads_meta_aceite.sh
if [ -f "$ACEITE_META" ]; then
  if ! bash -n "$ACEITE_META" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W7-E02-T01: $ACEITE_META nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # As pontas externas so' em loopback e as guardas de ambiente medidas no aceite.
  for marca in 127.0.0.1 GRAPH_NAO_E_DEV BANCO_NAO_E_DEV "--ambiente prod" assinatura; do
    if ! grep -q -e "$marca" "$ACEITE_META"; then
      echo "FALHOU card TRE-W7-E02-T01: aceite sem a marca obrigatoria '$marca'"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
# O ambiente de dev nao carrega segredo: token/app secret vem do host (ADR-005).
if grep -qE '^TRE_META_(ACCESS_TOKEN|APP_SECRET)=.+$' deploy/environments/dev-meta.env; then
  echo "FALHOU dev-meta.env carrega valor em TRE_META_ACCESS_TOKEN/TRE_META_APP_SECRET"
  FALHAS=$((FALHAS+1))
else
  echo "OK    dev-meta.env sem token/app secret versionado"
fi



# --- card TRE-W7-E03-T01 (atribuicao de lead do Google) ----------------------------------------------
# Componente + contrato versionados, aceite com as pontas em loopback e suíte offline com dentes.
ACEITE_GOOGLE="scripts/inbound/aceite-atribuicao-google.sh"
for arquivo in hermes/inbound/google/atribuicao_google.py hermes/inbound/google/atribuicao-google-v1.json \
               "$ACEITE_GOOGLE" scripts/inbound/verificar_atribuicao_google.py \
               scripts/inbound/stub-google-ads-dev.py docs/runbooks/atribuicao-google-lead.md \
               docs/architecture/atribuicao-google-v1.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W7-E03-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W7-E03-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_GOOGLE" ]; then
  if ! bash -n "$ACEITE_GOOGLE" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W7-E03-T01: $ACEITE_GOOGLE nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # Sem resolvedor em loopback e sem guarda de producao o aceite nao mede o que promete.
  for exigencia in '127.0.0.1' 'TRE_AMBIENTE=prod' 'pg-google-acc' 'GCLID_NAO_RESOLVIDO'; do
    if ! grep -q "$exigencia" "$ACEITE_GOOGLE"; then
      echo "FALHOU card TRE-W7-E03-T01: aceite sem '$exigencia'"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
# O contrato tem de declarar a tabela de atribuicao e as lacunas (o que nao e' medido fica escrito).
for exigencia in '"tabela_de_atribuicao"' '"lacunas"' '"SEM_IDENTIFICADOR"' '"FORMULARIO_SEM_CAMPANHA"'; do
  if ! grep -q "$exigencia" hermes/inbound/google/atribuicao-google-v1.json; then
    echo "FALHOU card TRE-W7-E03-T01: contrato sem $exigencia"
    FALHAS=$((FALHAS+1))
  fi
done
# Auditoria de fonte com o espaco declarado como pedaco proprio: o padrao antigo ("DE" + "LETE FROM")
# avaliava para DELETEFROM e nunca casaria DELETE FROM (buraco medido nesta onda).
if ! grep -q '" FROM"' hermes/inbound/google/atribuicao_google.py; then
  echo "FALHOU card TRE-W7-E03-T01: auditoria de fonte sem o padrao com espaco separado"
  FALHAS=$((FALHAS+1))
fi



# --- card TRE-W7-E04-T01 (LinkedIn AI-assisted workflow) ---------------------------------------------
# O aceite do canal LinkedIn assistido: versionado, bash valido, banco descartavel e as guardas que
# fazem o card ter sentido (a maquina NAO publica, NAO comenta, NAO reage, NAO manda DM).
ACEITE_LK="scripts/linkedin/verificar-linkedin-assistido.sh"
CONTRATO_LK="hermes/agents/linkedin/linkedin-assistido-v1.json"
MODULO_LK="hermes/agents/linkedin/linkedin_assistido.py"
for arquivo in "$ACEITE_LK" "$CONTRATO_LK" "$MODULO_LK" docs/runbooks/linkedin-assistido.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W7-E04-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W7-E04-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_LK" ]; then
  if ! bash -n "$ACEITE_LK" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W7-E04-T01: $ACEITE_LK nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # O aceite tem de medir as acoes humanas exclusivas e a guarda de prod, e nao pode ter ponta de rede.
  for marca in "tentar-publicar" "tentar-acao" "--ambiente prod" "pg-lk-e04" "SEM_EVIDENCIA" "CONTATO_BLOQUEADO"; do
    if ! grep -q -- "$marca" "$ACEITE_LK"; then
      echo "FALHOU card TRE-W7-E04-T01: aceite sem a marca obrigatoria ($marca)"
      FALHAS=$((FALHAS+1))
    fi
  done
  if grep -qE "linkedin\.com|[[:space:]]curl[[:space:]]" "$MODULO_LK"; then
    echo "FALHOU card TRE-W7-E04-T01: modulo com caminho de rede para o LinkedIn"
    FALHAS=$((FALHAS+1))
  fi
fi
if [ -f "$CONTRATO_LK" ] && ! grep -q '"maquina_proibidas"' "$CONTRATO_LK"; then
  echo "FALHOU card TRE-W7-E04-T01: contrato sem as acoes humanas exclusivas declaradas"
  FALHAS=$((FALHAS+1))
fi


# --- card TRE-W7-E05-T01 (WhatsApp engaged-lead workflow) -------------------------------------------
# O componente existe E esta' versionado, com o contrato fechado (vocabulario, regras e janela), a
# barreira de descadastro declarada, a guarda de producao e o portao do entregavel (suite + aceite).
for arquivo in hermes/agentes/inbound/whatsapp_lead.py hermes/agentes/inbound/whatsapp-lead-v1.json \
               scripts/agentes/verificar_whatsapp_lead.py scripts/agentes/teste_whatsapp_lead_aceite.sh \
               docs/runbooks/whatsapp-engaged-lead.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W7-E05-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W7-E05-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f hermes/agentes/inbound/whatsapp_lead.py ]; then
  if grep -q 'auditar_fonte()' hermes/agentes/inbound/whatsapp_lead.py \
     && ! grep -qE '^[[:space:]]*(CREATE|ALTER|DROP|TRUNCATE)[[:space:]]' hermes/agentes/inbound/whatsapp_lead.py \
     && grep -q 'PRODUCAO_RECUSADA' hermes/agentes/inbound/whatsapp_lead.py \
     && grep -q 'BANCO_NAO_E_DEV' hermes/agentes/inbound/whatsapp_lead.py \
     && ! grep -qE 'INSERT INTO[^"]*contacts|INSERT INTO[^"]*outbox_events' hermes/agentes/inbound/whatsapp_lead.py; then
    echo "OK    whatsapp_lead.py: auditoria de fonte, sem DDL, guardas de prod/dev e escrita so' nas 2 tabelas"
  else
    echo "FALHOU card TRE-W7-E05-T01: componente sem auditoria de fonte / com DDL / sem guardas / fora do escopo"
    FALHAS=$((FALHAS+1))
  fi
fi
if [ -f hermes/agentes/inbound/whatsapp-lead-v1.json ]; then
  python3 - <<'PY' || { echo "FALHOU card TRE-W7-E05-T01: contrato sem a barreira de descadastro/janela"; FALHAS=$((FALHAS+1)); }
import json, sys
c = json.load(open("hermes/agentes/inbound/whatsapp-lead-v1.json", encoding="utf-8"))
vocab = c["vocabulario"]
regras = c["regras"]
opt_out = [r for r in regras if str(r.get("categoria")).upper() == "OPT_OUT"]
ok = (
    "recebido_em" in c["evento"]["campos_obrigatorios"]
    and sorted(c["escrita"]["tabelas"]) == ["interactions", "sync_events"]
    and int(c["janela_de_atendimento"]["minutos"]) > 0
    and len(opt_out) == 1 and int(opt_out[0]["ordem"]) == 1
    and "BLOQUEADO_POR_BLOQUEIO" in vocab["status_trilha"]
    and "REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA" in vocab["proximo_passo"]
    and c["lacunas"]
)
sys.exit(0 if ok else 1)
PY
fi
if [ -f scripts/agentes/teste_whatsapp_lead_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_whatsapp_lead_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W7-E05-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q '127.0.0.1\|loopback' scripts/agentes/teste_whatsapp_lead_aceite.sh \
     || ! grep -q 'TRE_AMBIENTE=dev' scripts/agentes/teste_whatsapp_lead_aceite.sh \
     || ! grep -q 'PARAR' scripts/agentes/teste_whatsapp_lead_aceite.sh; then
    echo "FALHOU card TRE-W7-E05-T01: aceite sem as pontas locais/guarda de ambiente/dente de descadastro"
    FALHAS=$((FALHAS+1))
  fi
fi
python3 scripts/agentes/verificar_whatsapp_lead.py >/dev/null 2>&1 \
  && echo "OK    suite do componente verde (verificar_whatsapp_lead.py)" \
  || { echo "FALHOU card TRE-W7-E05-T01: suite do componente nao passa"; FALHAS=$((FALHAS+1)); }


# --- card TRE-W7-E06-T01 (captura de lead coletado em evento) ----------------------------------------
# O componente existe E esta' versionado, com o contrato fechado (vinculo do evento + forma do consentimento),
# a guarda de producao e o portao do entregavel (suite + aceite).
for arquivo in hermes/agentes/inbound/captura_evento.py hermes/agentes/inbound/captura-evento-v1.json \
               scripts/agentes/verificar_captura_evento.py scripts/agentes/teste_captura_evento_aceite.sh \
               docs/runbooks/captura-de-lead-de-evento.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W7-E06-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W7-E06-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f hermes/agentes/inbound/captura_evento.py ]; then
  if grep -q 'auditar_fonte()' hermes/agentes/inbound/captura_evento.py \
     && ! grep -qE '^[[:space:]]*(CREATE|ALTER|DROP|TRUNCATE)[[:space:]]' hermes/agentes/inbound/captura_evento.py \
     && grep -q 'PRODUCAO_RECUSADA' hermes/agentes/inbound/captura_evento.py \
     && grep -q 'BANCO_NAO_E_DEV' hermes/agentes/inbound/captura_evento.py \
     && grep -q 'EVENTO_NAO_DECLARADO' hermes/agentes/inbound/captura_evento.py; then
    echo "OK    captura_evento.py: auditoria de fonte, sem DDL, guardas de prod/dev e barreira do evento"
  else
    echo "FALHOU card TRE-W7-E06-T01: componente sem auditoria de fonte / com DDL / sem guardas"
    FALHAS=$((FALHAS+1))
  fi
fi
if [ -f hermes/agentes/inbound/captura-evento-v1.json ]; then
  python3 - <<'PY' || { echo "FALHOU card TRE-W7-E06-T01: contrato sem a barreira do evento/consentimento"; FALHAS=$((FALHAS+1)); }
import json, sys
c = json.load(open("hermes/agentes/inbound/captura-evento-v1.json", encoding="utf-8"))
obrig = c["coleta"]["campos_obrigatorios"]
vocab = c["vocabulario"]
ok = ("origem_evento.event_id" in obrig and "origem_evento.capture_method" in obrig
      and "origem_evento.capturado_em" in obrig
      and "consentimento.aceito" in obrig and "consentimento.legal_basis" in obrig
      and "consentimento.forma" in obrig
      and "EVENTO_NAO_DECLARADO" in vocab["status_trilha"]
      and "RECUSADO_CONSENTIMENTO" in vocab["status_trilha"]
      and vocab["channel"] == ["EVENTO"]
      and float(c["identificadores"]["limiar_de_merge_automatico"]) == 0.95
      and c["lacunas"])
sys.exit(0 if ok else 1)
PY
fi
if [ -f scripts/agentes/teste_captura_evento_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_captura_evento_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W7-E06-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q '127.0.0.1\|loopback' scripts/agentes/teste_captura_evento_aceite.sh \
     || ! grep -q 'TRE_AMBIENTE=dev' scripts/agentes/teste_captura_evento_aceite.sh \
     || ! grep -q 'RECUSADO_CONSENTIMENTO' scripts/agentes/teste_captura_evento_aceite.sh \
     || ! grep -q 'EVENTO_NAO_DECLARADO' scripts/agentes/teste_captura_evento_aceite.sh; then
    echo "FALHOU card TRE-W7-E06-T01: aceite sem as pontas locais/guarda de ambiente/barreiras do canal"
    FALHAS=$((FALHAS+1))
  fi
fi
python3 scripts/agentes/verificar_captura_evento.py >/dev/null 2>&1 \
  && echo "OK    suite do componente verde (verificar_captura_evento.py)" \
  || { echo "FALHOU card TRE-W7-E06-T01: suite do componente nao passa"; FALHAS=$((FALHAS+1)); }


# --- card TRE-W8-E02-T01 (Conversao por segmento) -----------------------------------------------------
CONTRATO_SEG="hermes/agentes/analytics/conversao-segmento-v1.json"
COMPONENTE_SEG="hermes/agentes/analytics/conversao_segmento.py"
for arquivo in "$COMPONENTE_SEG" "$CONTRATO_SEG" scripts/agentes/verificar_conversao_segmento.py \
               scripts/agentes/teste_conversao_segmento_aceite.sh \
               docs/architecture/conversao-por-segmento-v1.md docs/runbooks/conversao-por-segmento.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W8-E02-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W8-E02-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_SEG" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_SEG" \
       scripts/agentes/verificar_conversao_segmento.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E02-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # Os eixos tem de bater com o vocabulario do contrato de dados E os estagios com os do funil: quem
  # decide e' o proprio componente (`--conferir`), nao um grep deste portao.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_SEG" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E02-T01: --conferir recusou (eixo x contrato de dados x funil)"
    FALHAS=$((FALHAS+1))
  fi
  # O recorte NAO pode reimplementar o funil: a derivacao e' importada.
  if ! grep -q '^import funil' "$COMPONENTE_SEG"; then
    echo "FALHOU card TRE-W8-E02-T01: componente nao importa o funil (segunda regra de funil?)"
    FALHAS=$((FALHAS+1))
  fi
  for marca in 'RECUSA por desenho (exit 4)' 'READ ONLY' 'lacunas_declaradas' 'SEM_DADO' 'FORA_DO_VOCABULARIO'; do
    if ! grep -q -F "$marca" "$CONTRATO_SEG"; then
      echo "FALHOU card TRE-W8-E02-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f scripts/agentes/teste_conversao_segmento_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_conversao_segmento_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E02-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ACEITE_CONVERSAO_SEGMENTO_OK' scripts/agentes/teste_conversao_segmento_aceite.sh; then
    echo "FALHOU card TRE-W8-E02-T01: aceite sem o marcador ACEITE_CONVERSAO_SEGMENTO_OK"
    FALHAS=$((FALHAS+1))
  fi
fi


# --- card TRE-W8-E04-T01 (analise de desempenho de mensagens) ---------------------------------------
# Contrato + componente + duble + verificador + aceite existem E estao versionados; o aceite e' bash
# valido, usa container DESCARTavel e cobre as guardas de ambiente (prod recusado) e de leitura.
ACEITE_DESEMP="scripts/agentes/teste_desempenho_mensagens_aceite.sh"
for arquivo in \
  hermes/analytics/desempenho-mensagens-v1.json \
  hermes/analytics/desempenho_mensagens.py \
  scripts/agentes/duble_psql_desempenho.py \
  scripts/agentes/verificar_desempenho_mensagens.py \
  "$ACEITE_DESEMP" \
  docs/runbooks/desempenho-de-mensagens.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W8-E04-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W8-E04-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_DESEMP" ]; then
  if ! bash -n "$ACEITE_DESEMP" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E04-T01: $ACEITE_DESEMP nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # Container descartavel do aceite (nada nasce em ambiente existente) e guarda de prod medida.
  if ! grep -q 'pg-desemp-acc' "$ACEITE_DESEMP" || ! grep -q 'ambiente prod' "$ACEITE_DESEMP"; then
    echo "FALHOU card TRE-W8-E04-T01: aceite sem o container descartavel ou sem a guarda de prod"
    FALHAS=$((FALHAS+1))
  fi
  # Somente leitura e' medida: contagem das tabelas antes/depois tem de estar no aceite.
  if ! grep -q 'ANTES' "$ACEITE_DESEMP" || ! grep -q 'DEPOIS' "$ACEITE_DESEMP"; then
    echo "FALHOU card TRE-W8-E04-T01: aceite sem a prova de somente-leitura (contagens antes/depois)"
    FALHAS=$((FALHAS+1))
  fi
fi
# O componente NAO pode carregar verbo de escrita no caminho de leitura.
if [ -f "hermes/analytics/desempenho_mensagens.py" ]; then
  if ! grep -q 'afirmar_somente_leitura' hermes/analytics/desempenho_mensagens.py; then
    echo "FALHOU card TRE-W8-E04-T01: componente sem a guarda de somente-leitura"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'PROD_RECUSADO' hermes/analytics/desempenho_mensagens.py; then
    echo "FALHOU card TRE-W8-E04-T01: componente sem a guarda de ambiente (prod recusado)"
    FALHAS=$((FALHAS+1))
  fi
fi


# --- card TRE-W8-E05-T01 (Custo de agentes) ------------------------------------------------------------
CONTRATO_CUSTO="hermes/agentes/analytics/custo-agentes-v1.json"
COMPONENTE_CUSTO="hermes/agentes/analytics/custo_agentes.py"
for arquivo in "$COMPONENTE_CUSTO" "$CONTRATO_CUSTO" scripts/agentes/verificar_custo_agentes.py \
               scripts/agentes/teste_custo_agentes_aceite.sh docs/architecture/custo-agentes-v1.md \
               docs/runbooks/custo-de-agentes.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W8-E05-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W8-E05-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_CUSTO" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_CUSTO" scripts/agentes/verificar_custo_agentes.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E05-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # As colunas lidas tem de existir na DDL congelada e o vocabulario de status tem de estar coerente:
  # quem decide e' o proprio componente (`--conferir`), nao um grep deste portao.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_CUSTO" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E05-T01: --conferir recusou (colunas divergem da DDL ou vocabulario incoerente)"
    FALHAS=$((FALHAS+1))
  fi
  # Leitura pura e nulo-nao-e-zero declarados: o contrato do componente tem de carregar as marcas.
  for marca in 'RECUSA por desenho (exit 4)' 'READ ONLY' 'lacunas_declaradas' \
               'runs_sem_custo' 'tokens_input' 'estimated_cost'; do
    if ! grep -q -F "$marca" "$CONTRATO_CUSTO"; then
      echo "FALHOU card TRE-W8-E05-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f scripts/agentes/teste_custo_agentes_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_custo_agentes_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E05-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ACEITE_CUSTO_AGENTES_OK' scripts/agentes/teste_custo_agentes_aceite.sh; then
    echo "FALHOU card TRE-W8-E05-T01: aceite sem o marcador ACEITE_CUSTO_AGENTES_OK"
    FALHAS=$((FALHAS+1))
  fi
fi


# --- card TRE-W9-E03-T01 (Previsao do melhor canal — efetividade por canal + opt-out como bloqueio) --------
CONTRATO_CANAL="hermes/agentes/analytics/previsao-canal-v1.json"
COMPONENTE_CANAL="hermes/agentes/analytics/previsao_canal.py"
for arquivo in "$COMPONENTE_CANAL" "$CONTRATO_CANAL" scripts/agentes/verificar_previsao_canal.py \
               scripts/agentes/teste_previsao_canal_aceite.sh docs/architecture/previsao-de-canal-v1.md \
               docs/runbooks/previsao-de-canal.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W9-E03-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W9-E03-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_CANAL" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_CANAL" \
       scripts/agentes/verificar_previsao_canal.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E03-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # Vocabulario de canal e minimos da pre-condicao sao LIDOS do contrato e a dependencia (funil) e' conferida:
  # quem decide e' o proprio componente (`--conferir`), nao um grep deste portao.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_CANAL" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E03-T01: --conferir recusou (contrato de dados ou dependencia do funil divergem)"
    FALHAS=$((FALHAS+1))
  fi
  # O desfecho NAO pode ser reimplementado: o componente tem de consumir o alcance do funil (W8-E01-T01).
  if ! grep -q "alcance_por_organizacao" "$COMPONENTE_CANAL"; then
    echo "FALHOU card TRE-W9-E03-T01: componente nao consome o alcance por organizacao do funil"
    FALHAS=$((FALHAS+1))
  fi
  for marca in 'RECUSA por desenho (exit 4)' 'READ ONLY' 'lacunas_declaradas' 'base_suficiente' 'dados_multicanal' 'opt_out'; do
    if ! grep -q -F "$marca" "$CONTRATO_CANAL"; then
      echo "FALHOU card TRE-W9-E03-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f scripts/agentes/teste_previsao_canal_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_previsao_canal_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E03-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ACEITE_PREVISAO_CANAL_OK' scripts/agentes/teste_previsao_canal_aceite.sh; then
    echo "FALHOU card TRE-W9-E03-T01: aceite sem o marcador ACEITE_PREVISAO_CANAL_OK"
    FALHAS=$((FALHAS+1))
  fi
  # A prova do aceite nao pode fechar sem rodar: todo bloco Python tem de ter o item de exit code.
  for bloco in RC_ASSERT RC_INTEG RC_PRIV; do
    if ! grep -q "$bloco" scripts/agentes/teste_previsao_canal_aceite.sh; then
      echo "FALHOU card TRE-W9-E03-T01: aceite sem a guarda de bloco $bloco (prova que morre em silencio)"
      FALHAS=$((FALHAS+1))
    fi
  done
fi


# --- card TRE-W8-E04-T01 (analise de desempenho de mensagens) ---------------------------------------
# Contrato + componente + duble + verificador + aceite existem E estao versionados; o aceite e' bash
# valido, usa container DESCARTavel e cobre as guardas de ambiente (prod recusado) e de leitura.
ACEITE_DESEMP="scripts/agentes/teste_desempenho_mensagens_aceite.sh"
for arquivo in \
  hermes/analytics/desempenho-mensagens-v1.json \
  hermes/analytics/desempenho_mensagens.py \
  scripts/agentes/duble_psql_desempenho.py \
  scripts/agentes/verificar_desempenho_mensagens.py \
  "$ACEITE_DESEMP" \
  docs/runbooks/desempenho-de-mensagens.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W8-E04-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W8-E04-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_DESEMP" ]; then
  if ! bash -n "$ACEITE_DESEMP" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E04-T01: $ACEITE_DESEMP nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # Container descartavel do aceite (nada nasce em ambiente existente) e guarda de prod medida.
  if ! grep -q 'pg-desemp-acc' "$ACEITE_DESEMP" || ! grep -q 'ambiente prod' "$ACEITE_DESEMP"; then
    echo "FALHOU card TRE-W8-E04-T01: aceite sem o container descartavel ou sem a guarda de prod"
    FALHAS=$((FALHAS+1))
  fi
  # Somente leitura e' medida: contagem das tabelas antes/depois tem de estar no aceite.
  if ! grep -q 'ANTES' "$ACEITE_DESEMP" || ! grep -q 'DEPOIS' "$ACEITE_DESEMP"; then
    echo "FALHOU card TRE-W8-E04-T01: aceite sem a prova de somente-leitura (contagens antes/depois)"
    FALHAS=$((FALHAS+1))
  fi
fi
# O componente NAO pode carregar verbo de escrita no caminho de leitura.
if [ -f "hermes/analytics/desempenho_mensagens.py" ]; then
  if ! grep -q 'afirmar_somente_leitura' hermes/analytics/desempenho_mensagens.py; then
    echo "FALHOU card TRE-W8-E04-T01: componente sem a guarda de somente-leitura"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'PROD_RECUSADO' hermes/analytics/desempenho_mensagens.py; then
    echo "FALHOU card TRE-W8-E04-T01: componente sem a guarda de ambiente (prod recusado)"
    FALHAS=$((FALHAS+1))
  fi
fi

# --- card TRE-W9-E04-T01 (melhor horario de contato) --------------------------------------------------
# Contrato + componente + verificador + aceite existem E estao versionados; o aceite e' bash valido, usa
# container DESCARTavel, cobre as guardas de ambiente (prod recusado) e de leitura, e o componente tem de
# MANTER os guardrails herdados do irmao (somente leitura + prod recusado) em vez de reimplementa-los.
ACEITE_TIMING="scripts/agentes/teste_melhor_horario_aceite.sh"
for arquivo in \
  hermes/analytics/melhor-horario-v1.json \
  hermes/analytics/melhor_horario.py \
  scripts/agentes/verificar_melhor_horario.py \
  "$ACEITE_TIMING" \
  docs/runbooks/melhor-horario.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W9-E04-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W9-E04-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_TIMING" ]; then
  if ! bash -n "$ACEITE_TIMING" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E04-T01: $ACEITE_TIMING nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'pg-timing-acc' "$ACEITE_TIMING" || ! grep -q 'ambiente prod' "$ACEITE_TIMING"; then
    echo "FALHOU card TRE-W9-E04-T01: aceite sem o container descartavel ou sem a guarda de prod"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ANTES' "$ACEITE_TIMING" || ! grep -q 'DEPOIS' "$ACEITE_TIMING"; then
    echo "FALHOU card TRE-W9-E04-T01: aceite sem a prova de somente-leitura (contagens antes/depois)"
    FALHAS=$((FALHAS+1))
  fi
  # O aceite tem de medir a coerencia com o irmao de desempenho (atribuicao unica), nao apenas roda-lo.
  if ! grep -q 'desempenho_mensagens.py' "$ACEITE_TIMING"; then
    echo "FALHOU card TRE-W9-E04-T01: aceite sem a coerencia medida com o irmao de desempenho"
    FALHAS=$((FALHAS+1))
  fi
fi
# O componente NAO pode reimplementar a regra do irmao: a atribuicao e a guarda de leitura vem dele.
if [ -f "hermes/analytics/melhor_horario.py" ]; then
  if ! grep -q 'import desempenho_mensagens as irmao' hermes/analytics/melhor_horario.py; then
    echo "FALHOU card TRE-W9-E04-T01: componente nao reusa o irmao (atribuicao seria uma segunda regra)"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'PROD_RECUSADO' hermes/analytics/melhor_horario.py; then
    echo "FALHOU card TRE-W9-E04-T01: componente sem a guarda de ambiente (prod recusado)"
    FALHAS=$((FALHAS+1))
  fi
fi


# --- card TRE-W9-E03-T01 (Previsao do melhor canal — efetividade por canal + opt-out como bloqueio) --------
CONTRATO_CANAL="hermes/agentes/analytics/previsao-canal-v1.json"
COMPONENTE_CANAL="hermes/agentes/analytics/previsao_canal.py"
for arquivo in "$COMPONENTE_CANAL" "$CONTRATO_CANAL" scripts/agentes/verificar_previsao_canal.py \
               scripts/agentes/teste_previsao_canal_aceite.sh docs/architecture/previsao-de-canal-v1.md \
               docs/runbooks/previsao-de-canal.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W9-E03-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W9-E03-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_CANAL" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_CANAL" \
       scripts/agentes/verificar_previsao_canal.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E03-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # Vocabulario de canal e minimos da pre-condicao sao LIDOS do contrato e a dependencia (funil) e' conferida:
  # quem decide e' o proprio componente (`--conferir`), nao um grep deste portao.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_CANAL" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E03-T01: --conferir recusou (contrato de dados ou dependencia do funil divergem)"
    FALHAS=$((FALHAS+1))
  fi
  # O desfecho NAO pode ser reimplementado: o componente tem de consumir o alcance do funil (W8-E01-T01).
  if ! grep -q "alcance_por_organizacao" "$COMPONENTE_CANAL"; then
    echo "FALHOU card TRE-W9-E03-T01: componente nao consome o alcance por organizacao do funil"
    FALHAS=$((FALHAS+1))
  fi
  for marca in 'RECUSA por desenho (exit 4)' 'READ ONLY' 'lacunas_declaradas' 'base_suficiente' 'dados_multicanal' 'opt_out'; do
    if ! grep -q -F "$marca" "$CONTRATO_CANAL"; then
      echo "FALHOU card TRE-W9-E03-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f scripts/agentes/teste_previsao_canal_aceite.sh ]; then
  if ! bash -n scripts/agentes/teste_previsao_canal_aceite.sh >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E03-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ACEITE_PREVISAO_CANAL_OK' scripts/agentes/teste_previsao_canal_aceite.sh; then
    echo "FALHOU card TRE-W9-E03-T01: aceite sem o marcador ACEITE_PREVISAO_CANAL_OK"
    FALHAS=$((FALHAS+1))
  fi
  # A prova do aceite nao pode fechar sem rodar: todo bloco Python tem de ter o item de exit code.
  for bloco in RC_ASSERT RC_INTEG RC_PRIV; do
    if ! grep -q "$bloco" scripts/agentes/teste_previsao_canal_aceite.sh; then
      echo "FALHOU card TRE-W9-E03-T01: aceite sem a guarda de bloco $bloco (prova que morre em silencio)"
      FALHAS=$((FALHAS+1))
    fi
  done
fi

# --- card TRE-W8-E04-T01 (analise de desempenho de mensagens) ---------------------------------------
# Contrato + componente + duble + verificador + aceite existem E estao versionados; o aceite e' bash
# valido, usa container DESCARTavel e cobre as guardas de ambiente (prod recusado) e de leitura.
ACEITE_DESEMP="scripts/agentes/teste_desempenho_mensagens_aceite.sh"
for arquivo in \
  hermes/analytics/desempenho-mensagens-v1.json \
  hermes/analytics/desempenho_mensagens.py \
  scripts/agentes/duble_psql_desempenho.py \
  scripts/agentes/verificar_desempenho_mensagens.py \
  "$ACEITE_DESEMP" \
  docs/runbooks/desempenho-de-mensagens.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W8-E04-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W8-E04-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_DESEMP" ]; then
  if ! bash -n "$ACEITE_DESEMP" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W8-E04-T01: $ACEITE_DESEMP nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # Container descartavel do aceite (nada nasce em ambiente existente) e guarda de prod medida.
  if ! grep -q 'pg-desemp-acc' "$ACEITE_DESEMP" || ! grep -q 'ambiente prod' "$ACEITE_DESEMP"; then
    echo "FALHOU card TRE-W8-E04-T01: aceite sem o container descartavel ou sem a guarda de prod"
    FALHAS=$((FALHAS+1))
  fi
  # Somente leitura e' medida: contagem das tabelas antes/depois tem de estar no aceite.
  if ! grep -q 'ANTES' "$ACEITE_DESEMP" || ! grep -q 'DEPOIS' "$ACEITE_DESEMP"; then
    echo "FALHOU card TRE-W8-E04-T01: aceite sem a prova de somente-leitura (contagens antes/depois)"
    FALHAS=$((FALHAS+1))
  fi
fi
# O componente NAO pode carregar verbo de escrita no caminho de leitura.
if [ -f "hermes/analytics/desempenho_mensagens.py" ]; then
  if ! grep -q 'afirmar_somente_leitura' hermes/analytics/desempenho_mensagens.py; then
    echo "FALHOU card TRE-W8-E04-T01: componente sem a guarda de somente-leitura"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'PROD_RECUSADO' hermes/analytics/desempenho_mensagens.py; then
    echo "FALHOU card TRE-W8-E04-T01: componente sem a guarda de ambiente (prod recusado)"
    FALHAS=$((FALHAS+1))
  fi
fi

# --- card TRE-W9-E04-T01 (melhor horario de contato) --------------------------------------------------
# Contrato + componente + verificador + aceite existem E estao versionados; o aceite e' bash valido, usa
# container DESCARTavel, cobre as guardas de ambiente (prod recusado) e de leitura, e o componente tem de
# MANTER os guardrails herdados do irmao (somente leitura + prod recusado) em vez de reimplementa-los.
ACEITE_TIMING="scripts/agentes/teste_melhor_horario_aceite.sh"
for arquivo in \
  hermes/analytics/melhor-horario-v1.json \
  hermes/analytics/melhor_horario.py \
  scripts/agentes/verificar_melhor_horario.py \
  "$ACEITE_TIMING" \
  docs/runbooks/melhor-horario.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W9-E04-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W9-E04-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$ACEITE_TIMING" ]; then
  if ! bash -n "$ACEITE_TIMING" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E04-T01: $ACEITE_TIMING nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'pg-timing-acc' "$ACEITE_TIMING" || ! grep -q 'ambiente prod' "$ACEITE_TIMING"; then
    echo "FALHOU card TRE-W9-E04-T01: aceite sem o container descartavel ou sem a guarda de prod"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'ANTES' "$ACEITE_TIMING" || ! grep -q 'DEPOIS' "$ACEITE_TIMING"; then
    echo "FALHOU card TRE-W9-E04-T01: aceite sem a prova de somente-leitura (contagens antes/depois)"
    FALHAS=$((FALHAS+1))
  fi
  # O aceite tem de medir a coerencia com o irmao de desempenho (atribuicao unica), nao apenas roda-lo.
  if ! grep -q 'desempenho_mensagens.py' "$ACEITE_TIMING"; then
    echo "FALHOU card TRE-W9-E04-T01: aceite sem a coerencia medida com o irmao de desempenho"
    FALHAS=$((FALHAS+1))
  fi
fi
# O componente NAO pode reimplementar a regra do irmao: a atribuicao e a guarda de leitura vem dele.
if [ -f "hermes/analytics/melhor_horario.py" ]; then
  if ! grep -q 'import desempenho_mensagens as irmao' hermes/analytics/melhor_horario.py; then
    echo "FALHOU card TRE-W9-E04-T01: componente nao reusa o irmao (atribuicao seria uma segunda regra)"
    FALHAS=$((FALHAS+1))
  fi
  if ! grep -q 'PROD_RECUSADO' hermes/analytics/melhor_horario.py; then
    echo "FALHOU card TRE-W9-E04-T01: componente sem a guarda de ambiente (prod recusado)"
    FALHAS=$((FALHAS+1))
  fi
fi

# --- card TRE-W9-E05-T01 (Nurture automatizado — plano de toques, nunca envio) -------------------------
# Contrato + componente + verificador + aceite existem E estao versionados; o componente compila, o
# contrato e' conferido pelo PROPRIO componente (`--conferir`) e o aceite e' bash valido com container
# DESCARTavel. O nurture NAO tem porta de banco: a guarda de escrita (auditoria de codigo) tem de existir.
CONTRATO_NURTURE="hermes/agentes/analytics/nutricao-automatica-v1.json"
COMPONENTE_NURTURE="hermes/agentes/analytics/nutricao_automatica.py"
ACEITE_NURTURE="scripts/agentes/teste_nutricao_automatica_aceite.sh"
for arquivo in "$COMPONENTE_NURTURE" "$CONTRATO_NURTURE" scripts/agentes/verificar_nutricao_automatica.py \
               "$ACEITE_NURTURE" docs/architecture/nutricao-automatica-v1.md docs/runbooks/nutricao-automatica.md; do
  if [ ! -f "$arquivo" ]; then
    echo "FALHOU card TRE-W9-E05-T01: arquivo ausente ($arquivo)"
    FALHAS=$((FALHAS+1))
  elif git ls-files --error-unmatch "$arquivo" >/dev/null 2>&1; then
    echo "OK    versionado $arquivo"
  else
    echo "FALHOU card TRE-W9-E05-T01: nao versionado $arquivo"
    FALHAS=$((FALHAS+1))
  fi
done
if [ -f "$COMPONENTE_NURTURE" ]; then
  if ! PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$COMPONENTE_NURTURE" \
       scripts/agentes/verificar_nutricao_automatica.py >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E05-T01: componente ou verificador nao compila"
    FALHAS=$((FALHAS+1))
  fi
  # Quem decide se contrato e dependencia estao coerentes e' o proprio componente, nao um grep deste portao.
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$COMPONENTE_NURTURE" --ambiente dev --conferir >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E05-T01: --conferir recusou (contrato, pais ou guarda de escrita)"
    FALHAS=$((FALHAS+1))
  fi
  # O plano CONSOME os dois pais (canal e janela) em vez de remedir; e' pedido, nunca envio; e recusa prod.
  for marca in 'previsao-canal-v1' 'melhor-horario-v1' 'exige_aprovacao_humana' 'NAO_ENVIA' \
               'PROD_RECUSADO' 'auditar_proprio_codigo' 'porta de banco'; do
    if ! grep -q -F "$marca" "$COMPONENTE_NURTURE"; then
      echo "FALHOU card TRE-W9-E05-T01: componente sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
  for marca in 'RECUSA por desenho (exit 4' 'NUNCA envia' 'exige_aprovacao_humana' 'lacunas_declaradas' \
               'pre_condicao' 'porta de banco' 'condicoes_de_parada'; do
    if ! grep -q -F "$marca" "$CONTRATO_NURTURE"; then
      echo "FALHOU card TRE-W9-E05-T01: contrato sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi
if [ -f "$ACEITE_NURTURE" ]; then
  if ! bash -n "$ACEITE_NURTURE" >/dev/null 2>&1; then
    echo "FALHOU card TRE-W9-E05-T01: aceite nao e' bash valido"
    FALHAS=$((FALHAS+1))
  fi
  # O aceite mede a CADEIA: regressao dos dois pais, container descartavel e leitura pura antes/depois.
  for marca in 'ACEITE_NUTRICAO_AUTOMATICA_001_OK' 'VERIFICADOR_PREVISAO_CANAL_PASS' 'VERIFICADOR_MELHOR_HORARIO_PASS' \
               'pg-analytics-nurture-acc' 'PLANO_ABSTIDO' 'ANTES' 'DEPOIS'; do
    if ! grep -q -F "$marca" "$ACEITE_NURTURE"; then
      echo "FALHOU card TRE-W9-E05-T01: aceite sem a marca $marca"
      FALHAS=$((FALHAS+1))
    fi
  done
fi

echo "---"
if [ "$FALHAS" -eq 0 ]; then echo "RESULTADO: PASS (0 falhas)"; exit 0; else echo "RESULTADO: FALHOU ($FALHAS)"; exit 1; fi
