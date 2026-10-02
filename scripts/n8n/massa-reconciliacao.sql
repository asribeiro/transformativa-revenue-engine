-- Massa do aceite da RECONCILIACAO (card TRE-W3-E04-T01) — lado PostgreSQL.
--
-- Um bloco por ESTADO, extraido pelo aceite (`scripts/n8n/verificar-reconciliacao.sh`) e
-- aplicado no banco DESCARTÁVEL com `-v ON_ERROR_STOP=1`. Cada bloco comeca por um
-- `truncate ... cascade` das tres tabelas (CASCADE por causa de `contacts`, que referencia
-- `organizations`): estado que nao limpa o anterior nao mede nada.
--
-- Variaveis do psql (todas passadas pelo aceite):
--   :org_a  :org_b  :org_c        organizacoes do teste (UUID; ids FIXOS do aceite)
--   :parceiro_a                   id REAL do parceiro criado no Odoo pelo estado
--   :parceiro_inexistente         id que NAO existe no Odoo (prova do espelho ausente)
--   :trilha_a                     UUID da linha de trilha do espelho de :org_a
--   :linha_p1 :linha_p2 :linha_p3 :linha_p4   UUIDs das linhas de trilha da fila
--   :evento_p1 :evento_p2 :evento_p3 :evento_p4  UUIDs dos eventos da fila
--
-- Nenhuma escrita fora das tres tabelas do contrato; nenhum DDL.

-- ESTADO A
truncate table sales_intelligence.organizations, sales_intelligence.outbox_events, sales_intelligence.sync_events cascade;
insert into sales_intelligence.organizations
    (id, legal_name, trade_name, odoo_partner_id, cnpj, domain, status, updated_at)
values
    (:'org_a', 'Alfa Consultoria Ltda', 'Alfa', :parceiro_a, '11.222.333/0001-81', 'alfa.example', 'QUALIFIED', now() - interval '1 hour');
insert into sales_intelligence.sync_events
    (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, created_at, completed_at)
values
    (:'trilha_a', 'organization', :'org_a', 'postgres', 'odoo', 'UPSERT',
     'outbox:' || :'trilha_a' || ':COMPANY_QUALIFIED', 'COMPLETED', now() - interval '2 hour', now() - interval '2 hour');
-- FIM ESTADO A

-- ESTADO B
truncate table sales_intelligence.organizations, sales_intelligence.outbox_events, sales_intelligence.sync_events cascade;
insert into sales_intelligence.organizations
    (id, legal_name, trade_name, odoo_partner_id, cnpj, domain, status, updated_at)
values
    (:'org_a', 'Alfa (espelho ausente)', 'Alfa', :parceiro_inexistente, '11.222.333/0001-81', 'alfa.example', 'QUALIFIED', now() - interval '1 hour');
-- FIM ESTADO B

-- ESTADO C
truncate table sales_intelligence.organizations, sales_intelligence.outbox_events, sales_intelligence.sync_events cascade;
insert into sales_intelligence.organizations
    (id, legal_name, trade_name, odoo_partner_id, cnpj, domain, status, updated_at)
values
    (:'org_a', 'Alfa (id cruzado)', 'Alfa', :parceiro_a, '11.222.333/0001-81', 'alfa.example', 'QUALIFIED', now() - interval '1 hour'),
    (:'org_c', 'Gama (dona do id cruzado)', 'Gama', NULL, NULL, 'gama.example', 'QUALIFIED', now() - interval '2 hour');
-- FIM ESTADO C

-- ESTADO D
truncate table sales_intelligence.organizations, sales_intelligence.outbox_events, sales_intelligence.sync_events cascade;
insert into sales_intelligence.organizations
    (id, legal_name, trade_name, odoo_partner_id, cnpj, domain, status, updated_at)
values
    (:'org_a', 'Alfa (identidade forte)', 'Alfa', :parceiro_a, '11.222.333/0001-81', 'alfa.example', 'QUALIFIED', now() - interval '1 hour');
-- FIM ESTADO D

-- ESTADO E
truncate table sales_intelligence.organizations, sales_intelligence.outbox_events, sales_intelligence.sync_events cascade;
insert into sales_intelligence.organizations
    (id, legal_name, trade_name, odoo_partner_id, cnpj, domain, status, updated_at)
values
    (:'org_a', 'Alfa (espelho arquivado)', 'Alfa', :parceiro_a, '11.222.333/0001-81', 'alfa.example', 'QUALIFIED', now() - interval '1 hour');
-- FIM ESTADO E

-- ESTADO F
truncate table sales_intelligence.organizations, sales_intelligence.outbox_events, sales_intelligence.sync_events cascade;
insert into sales_intelligence.organizations
    (id, legal_name, trade_name, odoo_partner_id, cnpj, domain, status, updated_at)
values
    (:'org_a', 'Alfa Consultoria Ltda', 'Alfa', :parceiro_a, '11.222.333/0001-81', 'alfa.example', 'QUALIFIED', now() - interval '1 hour');
insert into sales_intelligence.sync_events
    (id, entity_type, entity_id, source_system, target_system, operation, idempotency_key, status, created_at, completed_at)
values
    (:'trilha_a', 'organization', :'org_a', 'postgres', 'odoo', 'UPSERT',
     'outbox:' || :'trilha_a' || ':COMPANY_QUALIFIED', 'COMPLETED', now() - interval '2 hour', now() - interval '2 hour'),
    (:'linha_p2', 'organization', :'org_a', 'postgres', 'odoo', 'UPSERT',
     'outbox:' || :'evento_p2' || ':COMPANY_QUALIFIED', 'COMPLETED', now() - interval '30 second', now() - interval '30 second'),
    (:'linha_p4', 'organization', :'org_a', 'postgres', 'odoo', 'UPSERT',
     'outbox:' || :'evento_p4' || ':COMPANY_QUALIFIED', 'REFUSED', now() - interval '20 second', now() - interval '20 second');
insert into sales_intelligence.outbox_events
    (id, aggregate_type, aggregate_id, event_type, payload, status, attempts, created_at, last_error)
values
    (:'evento_p1', 'organization', :'org_a', 'COMPANY_QUALIFIED', '{"event_version":"1.0"}'::jsonb,
     'PENDING', 0, now() - interval '1 hour', NULL),
    (:'evento_p2', 'organization', :'org_a', 'COMPANY_QUALIFIED', '{"event_version":"1.0"}'::jsonb,
     'PENDING', 0, now() - interval '30 second', NULL),
    (:'evento_p3', 'organization', :'org_a', 'COMPANY_QUALIFIED', '{"event_version":"1.0"}'::jsonb,
     'PENDING', 3, now() - interval '5 second', 'transporte falhou'),
    (:'evento_p4', 'organization', :'org_a', 'COMPANY_QUALIFIED', '{"event_version":"1.0"}'::jsonb,
     'PENDING', 1, now() - interval '20 second', NULL);
-- FIM ESTADO F
