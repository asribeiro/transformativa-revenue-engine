-- Trilha da RECUSA de um evento Odoo -> PostgreSQL — card TRE-W3-E03-T01.
-- Contrato: n8n/contracts/odoo-events-ingest.v1.json (`trilha` e `envelope.ordem_da_validacao`).
--
-- O QUE ESTE ARQUIVO FAZ: grava a linha de trilha da recusa com status `REFUSED` e o motivo
-- NOMEADO em `error_message`. A recusa existe para ser VISIVEL (data contract §6 regra 3: "falha
-- nao e' engolida: vai para dead-letter com motivo"): o produtor que receber 422 marca o evento
-- dele como DEAD_LETTER com o mesmo motivo, e a trilha do PostgreSQL guarda o lado de ca'.
--
-- Parametros (a MESMA ordem do arquivo de aceite — o workflow passa uma LISTA, nunca texto):
--   $1 idempotency_key   (a chave do envelope; sem envelope valido, a chave nomeada da recusa)
--   $2 operation         (event_type quando declarado; texto vazio quando nem isso veio)
--   $3 entity_type       (modelo de origem quando o evento e' declarado; NULL fora do contrato)
--   $4 entity_id         (uuid canonico quando valido; NULL caso contrario)
--   $5 source_version    (event_version quando veio; texto vazio caso contrario)
--   $6 request_payload   (jsonb: o corpo recebido, integral — a recusa tambem tem prova)
--   $7 status            (REFUSED)
--   $8 error_message     (o motivo nomeado da recusa)
--
-- Idempotencia: reenvio do MESMO corpo recusado nao cria segunda linha de recusa (ON CONFLICT DO
-- NOTHING pela chave) — a porta responde `duplicado: true` e o mesmo motivo.
WITH recusa AS (
    INSERT INTO sales_intelligence.sync_events
        (id, entity_type, entity_id, source_system, target_system, operation, source_version,
         idempotency_key, status, request_payload, completed_at, error_message)
    VALUES (gen_random_uuid(), $3, $4::uuid, 'odoo', 'postgres', $2, $5,
            $1, $7, $6::jsonb, NOW(), $8)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id
),
existente AS (
    SELECT id FROM sales_intelligence.sync_events WHERE idempotency_key = $1
)
SELECT COALESCE((SELECT id FROM recusa), (SELECT id FROM existente))::text AS sync_event_id,
       (SELECT count(*) FROM recusa) > 0                                    AS inserido,
       422                                                                  AS codigo_http,
       json_build_object(
           'aceito', false,
           'motivo', $8,
           'duplicado', NOT ((SELECT count(*) FROM recusa) > 0),
           'sync_event_id',
               COALESCE((SELECT id FROM recusa), (SELECT id FROM existente))::text,
           'event_type', NULLIF($2, '')
       )                                                                    AS corpo;
