-- =====================================================================================
-- Rollback da massa de smoke (`smoke_dev.sql`): remove SO a massa, tabela por tabela.
-- Par do fixture; usado pelo runbook docs/runbooks/massa-de-smoke-dev.md (secao 6) e
-- exercitado em container descartavel pelo teste do TRE-W1-E02-T01.
--
-- Ordem: filhas antes das PAI (`organizations` por ultimo) — sem isso as FKs do contrato
-- barram o DELETE. IDs deterministicos do fixture: nada de negocio e tocado (na V1 a unica
-- massa em dev e esta).
-- Uso:
--   docker cp db/fixtures/smoke_dev_rollback.sql pg-sales-dev:/tmp/r.sql
--   docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence -v ON_ERROR_STOP=1 -f /tmp/r.sql
-- =====================================================================================

DELETE FROM sales_intelligence.human_approvals WHERE id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
DELETE FROM sales_intelligence.sync_events     WHERE id = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
DELETE FROM sales_intelligence.outbox_events   WHERE id = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
DELETE FROM sales_intelligence.agent_runs      WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
DELETE FROM sales_intelligence.recommendations WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
DELETE FROM sales_intelligence.interactions    WHERE id = '99999999-9999-9999-9999-999999999999';
DELETE FROM sales_intelligence.scores          WHERE id IN ('77777777-7777-7777-7777-777777777777',
                                                            '88888888-8888-8888-8888-888888888888');
DELETE FROM sales_intelligence.pain_hypotheses WHERE id = '66666666-6666-6666-6666-666666666666';
DELETE FROM sales_intelligence.signals         WHERE id = '55555555-5555-5555-5555-555555555555';
DELETE FROM sales_intelligence.research_runs   WHERE id = '44444444-4444-4444-4444-444444444444';
DELETE FROM sales_intelligence.contacts        WHERE id = '33333333-3333-3333-3333-333333333333';
DELETE FROM sales_intelligence.organizations   WHERE id IN ('11111111-1111-1111-1111-111111111111',
                                                            '22222222-2222-2222-2222-222222222222');
