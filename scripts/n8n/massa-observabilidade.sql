-- ============================================================================
-- Massa da OBSERVABILIDADE DE SINCRONIZACAO — card TRE-W3-E05-T01
-- (board transformativa-revenue-engine). Banco DESCARTÁVEL do aceite
-- (nome ^tre_[a-z0-9_]+$), nunca o banco do dev.
--
-- Por que existe: a observabilidade mede o que a trilha e a fila CONTAM. Para o
-- aceite morder, cada estado precisa de uma massa que produza UM sinal por vez —
-- assim o veredito de cada estado e' ATRIBUIVEL (senao um CRITICO de um estado
-- explicaria o do estado seguinte e nenhum item provaria nada).
--
-- O aceite TRUNCA as duas tabelas antes de aplicar cada bloco (estados
-- INDEPENDENTES, nao cumulativos). Cada bloco e' delimitado por
-- `-- ESTADO <letra>` / `-- FIM ESTADO <letra>` e aplicado por sed.
--
-- A massa imita o que o consumidor de outbox escreve (n8n/sql/registrar-resultado.sql):
-- `outbox_events` com id/event_type/status/attempts/processed_at/last_error e a
-- linha de `sync_events` com a chave derivada 'outbox:<id>:<event_type>' e o
-- status da trilha (COMPLETED/FAILED/REFUSED). A derivacao da chave e' a do
-- contrato do consumidor e e' conferida pela lente estrutural (espelho).
--
-- ESTADOS:
--   A sucesso                    -> OK        (rodada saudavel: fila vazia, zero dead-letter)
--   B falha transitoria da trilha -> ATENCAO  (RETRY + FAILED com motivo)
--   C dead-letter SEM motivo      -> CRITICO  (a falha engolida: por isso existe o card)
--   D dead-letter COM motivo      -> ATENCAO  (dead-letter e' visivel com o MOTIVO)
--   E PROCESSED sem trilha        -> CRITICO  (sucesso sem prova na trilha)
--   F fila no teto                -> CRITICO  (attempts no teto ainda PENDING)
--   G direcao fora do vocabulario -> CRITICO  (alguem escreveu na trilha fora das portas)
--   H COMPLETED sem conclusao     -> CRITICO  (trilha fechada sem completed_at)
-- ============================================================================

-- ESTADO A
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, created_at, processed_at, attempts, status, last_error)
VALUES
    ('a0000001-0000-4000-8000-000000000001', 'organization', '11111111-1111-4111-8111-111111111111', 'organization.updated',
     '{"nome": "Alfa Consultoria Ltda"}'::jsonb, now() - interval '180 seconds', now() - interval '120 seconds', 1, 'PROCESSED', NULL),
    ('a0000001-0000-4000-8000-000000000002', 'organization', '11111111-1111-4111-8111-111111111111', 'organization.updated',
     '{"nome": "Alfa Consultoria Ltda (atualizada)"}'::jsonb, now() - interval '170 seconds', now() - interval '120 seconds', 1, 'PROCESSED', NULL);

INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, source_version, idempotency_key, status, created_at, completed_at, error_message)
VALUES
    ('b0000001-0000-4000-8000-000000000001', 'organization', '11111111-1111-4111-8111-111111111111', 'postgres', 'odoo', 'organization.updated', '1.0.0',
     'outbox:a0000001-0000-4000-8000-000000000001:organization.updated', 'COMPLETED', now() - interval '180 seconds', now() - interval '120 seconds', NULL),
    ('b0000001-0000-4000-8000-000000000002', 'organization', '11111111-1111-4111-8111-111111111111', 'postgres', 'odoo', 'organization.updated', '1.0.0',
     'outbox:a0000001-0000-4000-8000-000000000002:organization.updated', 'COMPLETED', now() - interval '170 seconds', now() - interval '120 seconds', NULL);
-- FIM ESTADO A

-- ESTADO B
-- Uma falha TRANSITORIA nomeada: o evento voltou para a fila (RETRY) e a trilha
-- registrou FAILED COM motivo. Sinal unico de alerta: trilha_falhas = 1.
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, created_at, processed_at, attempts, status, last_error)
VALUES
    ('a0000002-0000-4000-8000-000000000001', 'organization', '22222222-2222-4222-8222-222222222222', 'organization.updated',
     '{"nome": "Beta Servicos ME"}'::jsonb, now() - interval '120 seconds', NULL, 1, 'RETRY',
     'timeout ao entregar na porta unica (tentativa 1)');

INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, source_version, idempotency_key, status, created_at, completed_at, error_message)
VALUES
    ('b0000002-0000-4000-8000-000000000001', 'organization', '22222222-2222-4222-8222-222222222222', 'postgres', 'odoo', 'organization.updated', '1.0.0',
     'outbox:a0000002-0000-4000-8000-000000000001:organization.updated', 'FAILED', now() - interval '120 seconds', NULL,
     'timeout ao entregar na porta unica (tentativa 1)');
-- FIM ESTADO B

-- ESTADO C
-- A FALHA ENGOLIDA: o evento morreu (DEAD_LETTER, attempts no teto) e NAO ha
-- motivo em lugar nenhum — nem `last_error` no evento, nem `error_message` na
-- trilha da chave derivada. E' exatamente o que o card manda tornar impossivel
-- de passar em silencio: sinal critico unico = dead_letter_sem_motivo = 1.
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, created_at, processed_at, attempts, status, last_error)
VALUES
    ('a0000003-0000-4000-8000-000000000001', 'organization', '33333333-3333-4333-8333-333333333333', 'organization.updated',
     '{"nome": "Gama Comercio Ltda"}'::jsonb, now() - interval '90 seconds', now() - interval '30 seconds', 3, 'DEAD_LETTER', NULL);

INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, source_version, idempotency_key, status, created_at, completed_at, error_message)
VALUES
    ('b0000003-0000-4000-8000-000000000001', 'organization', '33333333-3333-4333-8333-333333333333', 'postgres', 'odoo', 'organization.updated', '1.0.0',
     'outbox:a0000003-0000-4000-8000-000000000001:organization.updated', 'REFUSED', now() - interval '90 seconds', now() - interval '30 seconds', NULL);
-- FIM ESTADO C

-- ESTADO D
-- Dead-letter COM motivo: o evento carrega o `last_error` e a trilha da chave
-- derivada carrega o `error_message`. O que o aceite mede aqui e' a VISIBILIDADE
-- (o relatorio tem de mostrar o MOTIVO, nao so' a contagem).
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, created_at, processed_at, attempts, status, last_error)
VALUES
    ('a0000004-0000-4000-8000-000000000001', 'organization', '44444444-4444-4444-8444-444444444444', 'organization.updated',
     '{"nome": "Delta Industria SA"}'::jsonb, now() - interval '90 seconds', now() - interval '30 seconds', 3, 'DEAD_LETTER',
     'Odoo recusou o evento: CNPJ invalido (HTTP 422)');

INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, source_version, idempotency_key, status, created_at, completed_at, error_message)
VALUES
    ('b0000004-0000-4000-8000-000000000001', 'organization', '44444444-4444-4444-8444-444444444444', 'postgres', 'odoo', 'organization.updated', '1.0.0',
     'outbox:a0000004-0000-4000-8000-000000000001:organization.updated', 'REFUSED', now() - interval '90 seconds', now() - interval '30 seconds',
     'recusa definitiva da porta unica: HTTP 422');
-- FIM ESTADO D

-- ESTADO E
-- SUCESSO SEM PROVA: o evento esta PROCESSED e NAO existe linha de trilha com a
-- chave derivada. E' o replay quebrado / a entrega que nao deixou rastro —
-- sinal critico unico = outbox_processado_sem_trilha = 1.
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, created_at, processed_at, attempts, status, last_error)
VALUES
    ('a0000005-0000-4000-8000-000000000001', 'organization', '55555555-5555-4555-8555-555555555555', 'organization.updated',
     '{"nome": "Epsilon Tecnologia Ltda"}'::jsonb, now() - interval '120 seconds', now() - interval '60 seconds', 1, 'PROCESSED', NULL);
-- FIM ESTADO E

-- ESTADO F
-- FILA NO TETO: um evento PENDING cujas tentativas ja' chegaram ao teto do
-- contrato do consumidor (3) nao volta a ser entregue sozinho — alguem tem de
-- olhar. Sinal critico unico = fila_no_teto = 1. A idade da fila (1200s) e' o
-- segundo sinal, de alerta (fila_idade_maxima_s >= 900).
INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, created_at, processed_at, attempts, status, last_error)
SELECT ('c0000006-0000-4000-8000-0000000000' || lpad(n::text, 2, '0'))::uuid, 'organization', '66666666-6666-4666-8666-666666666666',
       'organization.updated', '{"nome": "Zeta Servicos"}'::jsonb, now() - interval '1200 seconds', NULL, 0, 'PENDING', NULL
  FROM generate_series(1, 50) AS n;

INSERT INTO sales_intelligence.outbox_events (id, aggregate_type, aggregate_id, event_type, payload, created_at, processed_at, attempts, status, last_error)
VALUES
    ('c0000006-0000-4000-8000-000000000099', 'organization', '66666666-6666-4666-8666-666666666666', 'organization.updated',
     '{"nome": "Zeta Servicos (no teto)"}'::jsonb, now() - interval '1200 seconds', NULL, 3, 'PENDING', NULL);
-- FIM ESTADO F

-- ESTADO G
-- DIRECAO FORA DO VOCABULARIO: alguem escreveu na trilha uma direcao que nenhum
-- contrato declara (`odoo -> odoo`). Nao se soma ao balde "outras": fecha
-- CRITICO (sinal unico = trilha_direcao_nao_declarada = 1). A linha tambem NAO
-- entra na metrica dimensional (que so' tem as portas declaradas).
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, source_version, idempotency_key, status, created_at, completed_at, error_message)
VALUES
    ('b0000007-0000-4000-8000-000000000001', 'organization', '77777777-7777-4777-8777-777777777777', 'odoo', 'odoo', 'organization.updated', '1.0.0',
     'externo:77777777-7777-4777-8777-777777777777:organization.updated', 'COMPLETED', now() - interval '60 seconds', now() - interval '30 seconds', NULL);
-- FIM ESTADO G

-- ESTADO H
-- TRILHA FECHADA SEM CONCLUSAO: status COMPLETED com `completed_at` NULO — a
-- linha afirma ter terminado e nao registra quando. Sinal critico unico =
-- trilha_sem_conclusao = 1.
INSERT INTO sales_intelligence.sync_events (id, entity_type, entity_id, source_system, target_system, operation, source_version, idempotency_key, status, created_at, completed_at, error_message)
VALUES
    ('b0000008-0000-4000-8000-000000000001', 'organization', '88888888-8888-4888-8888-888888888888', 'postgres', 'odoo', 'organization.updated', '1.0.0',
     'outbox:b0000008-0000-4000-8000-000000000001:organization.updated', 'COMPLETED', now() - interval '60 seconds', NULL, NULL);
-- FIM ESTADO H
