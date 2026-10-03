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

echo "---"
if [ "$FALHAS" -eq 0 ]; then echo "RESULTADO: PASS (0 falhas)"; exit 0; else echo "RESULTADO: FALHOU ($FALHAS)"; exit 1; fi
