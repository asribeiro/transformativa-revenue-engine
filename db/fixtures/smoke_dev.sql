-- =====================================================================================
-- Massa minima de smoke test do schema `sales_intelligence`.
-- Uso: exercitar backup/restore com dados em TODAS as 12 tabelas (nao e dado de negocio).
-- IDs deterministicos de proposito: o teste de restore compara contagem por tabela.
-- Idempotente (ON CONFLICT DO NOTHING) para poder rodar duas vezes sem quebrar.
-- =====================================================================================

INSERT INTO sales_intelligence.organizations
  (id, odoo_partner_id, legal_name, trade_name, domain, cnpj, industry_name, employee_count,
   employee_band, city, state, status, data_quality_score)
VALUES
  ('11111111-1111-1111-1111-111111111111', 9001, 'MARCADOR SMOKE LTDA', 'MARCADOR-SMOKE',
   'smoke.teste.local', '12345678000199', 'Industria', 320, 'B_150_700', 'Sao Paulo', 'SP',
   'QUALIFIED', 88.50),
  ('22222222-2222-2222-2222-222222222222', 9002, 'Segunda Empresa S.A.', 'Segunda',
   'segunda.teste.local', '98765432000155', 'Servicos', 120, 'B_50_150', 'Campinas', 'SP',
   'DISCOVERED', 61.00)
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.contacts
  (id, organization_id, full_name, job_title, decision_role, email, legal_basis, contactability_score)
VALUES
  ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111',
   'Contato Smoke', 'Diretor de Operacoes', 'DECISION_MAKER', 'contato@smoke.teste.local',
   'LEGITIMO_INTERESSE', 74.00)
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.research_runs
  (id, organization_id, agent_name, agent_version, workflow_name, status, source_count, summary,
   model_provider, model_name, prompt_version)
VALUES
  ('44444444-4444-4444-4444-444444444444', '11111111-1111-1111-1111-111111111111',
   'research-agent', '1.0.0', 'pesquisa-empresa', 'COMPLETED', 7,
   'Execucao de smoke test do contrato de dados', 'deepseek', 'deepseek-v4-flash', 'v1.0')
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.signals
  (id, organization_id, signal_type, signal_category, title, source_type, confidence,
   relevance_score, research_run_id)
VALUES
  ('55555555-5555-5555-5555-555555555555', '11111111-1111-1111-1111-111111111111',
   'CONTRATACAO_TI', 'PESSOAS', 'Vaga aberta para gerente de TI', 'SITE', 0.9100, 78.00,
   '44444444-4444-4444-4444-444444444444')
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.pain_hypotheses
  (id, organization_id, research_run_id, pain_category, pain_statement, confidence,
   business_impact_score)
VALUES
  ('66666666-6666-6666-6666-666666666666', '11111111-1111-1111-1111-111111111111',
   '44444444-4444-4444-4444-444444444444', 'PROCESSOS_MANUAIS',
   'Indicio de fechamento manual em planilha', 0.7800, 65.00)
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.scores
  (id, organization_id, score_type, score_value, score_version, inputs, explanation)
VALUES
  ('77777777-7777-7777-7777-777777777777', '11111111-1111-1111-1111-111111111111',
   'PRIORITY', 82.50, 'v1.0', '{"icp": 90, "signals": 78}'::jsonb, '{"faixa": "A"}'::jsonb),
  ('88888888-8888-8888-8888-888888888888', '22222222-2222-2222-2222-222222222222',
   'PRIORITY', 41.00, 'v1.0', '{"icp": 55, "signals": 30}'::jsonb, '{"faixa": "NURTURE"}'::jsonb)
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.interactions
  (id, organization_id, contact_id, channel, direction, interaction_type, occurred_at, subject,
   sentiment)
VALUES
  ('99999999-9999-9999-9999-999999999999', '11111111-1111-1111-1111-111111111111',
   '33333333-3333-3333-3333-333333333333', 'EMAIL', 'OUTBOUND', 'PRIMEIRO_CONTATO',
   NOW() - INTERVAL '2 days', 'Primeiro contato (smoke)', 'NEUTRO')
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.recommendations
  (id, organization_id, contact_id, recommendation_type, action, description, confidence, priority)
VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111',
   '33333333-3333-3333-3333-333333333333', 'FIRST_TOUCH', 'LIGAR',
   'Ligar citando a vaga de TI como gancho', 0.8600, 1)
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.agent_runs
  (id, agent_name, agent_role, agent_version, workflow, organization_id, triggered_by, status,
   started_at, finished_at, model)
VALUES
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'research-agent', 'RESEARCH', '1.0.0',
   'pesquisa-empresa', '11111111-1111-1111-1111-111111111111', 'scheduler', 'SUCCESS',
   NOW() - INTERVAL '3 hours', NOW() - INTERVAL '2 hours', 'deepseek-v4-flash')
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.outbox_events
  (id, aggregate_type, aggregate_id, event_type, payload, status, attempts)
VALUES
  ('cccccccc-cccc-cccc-cccc-cccccccccccc', 'organization', '11111111-1111-1111-1111-111111111111',
   'organization.enriched', '{"smoke": true, "score": 82.5}'::jsonb, 'PENDING', 0)
ON CONFLICT DO NOTHING;

-- A linha da trilha e' COMPLETED com o instante de conclusao PREENCHIDO (`completed_at`):
-- e' o que TODAS as quatro portas declaradas gravam (n8n/sql/ingerir-evento.sql,
-- registrar-recusa.sql, registrar-resultado.sql e registrar-replay.sql). Ate' 06/10/2026 este
-- fixture gravava `COMPLETED` SEM `completed_at` e a massa de dev (que fica carregada por
-- criterio do TRE-W1-E02-T01) fechava a observabilidade de sync em CRITICO para sempre
-- (`trilha_sem_conclusao`, limiar 1/1) — linha que a producao nao consegue produzir, medida
-- com a observabilidade real. Card que corrigiu: t_2b93007f.
INSERT INTO sales_intelligence.sync_events
  (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status,
   request_payload, completed_at)
VALUES
  ('dddddddd-dddd-dddd-dddd-dddddddddddd', 'organization', '11111111-1111-1111-1111-111111111111',
   'postgres', 'odoo', 'UPSERT', 'smoke-org-11111111', 'COMPLETED', '{"smoke": true}'::jsonb, NOW())
ON CONFLICT DO NOTHING;

INSERT INTO sales_intelligence.human_approvals
  (id, action_type, entity_type, entity_id, requested_by, proposed_action, status)
VALUES
  ('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', 'FIRST_TOUCH', 'recommendation',
   'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'sales-ai',
   '{"canal": "email", "smoke": true}'::jsonb, 'PENDING')
ON CONFLICT DO NOTHING;
