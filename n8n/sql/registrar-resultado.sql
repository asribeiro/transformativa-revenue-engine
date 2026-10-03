-- Estado final do evento + trilha da sincronizacao, numa UNICA transacao
-- (o UPDATE do outbox e a linha de sync_events acontecem juntos ou nao acontecem).
-- Card TRE-W3-E02-T01; contrato: n8n/contracts/outbox-consumer.v1.json.
--
-- Parametros (a ordem e' o contrato deste arquivo; o workflow passa uma LISTA, nunca texto):
--   $1  status final do evento  (PROCESSED | RETRY | DEAD_LETTER)
--   $2  incrementa tentativas   (0 ou 1 — recusa de envelope NAO e' tentativa de entrega)
--   $3  last_error do evento    (texto ou NULL)
--   $4  id do evento (uuid)     (NULL = no-op: status fora da fila nao toca nada)
--   $5  entity_type da trilha
--   $6  entity_id da trilha (uuid ou NULL)
--   $7  source_version da trilha (a versao do envelope do evento)
--   $8  idempotency_key da trilha (UNIQUE no contrato)
--   $9  status da trilha        (COMPLETED | FAILED | REFUSED)
--   $10 request_payload         (jsonb)
--   $11 response_payload        (jsonb ou NULL)
--   $12 error_message           (texto ou NULL)
--
-- Regras da casa que aparecem aqui de proposito:
--   * retry nao cria duplicata (data contract §7.2): a trilha e' upsertada pela chave UNICA;
--   * dead-letter e' VISIVEL (data contract §7.3): motivo vai em last_error/error_message;
--   * processed_at so' existe para evento entregue: retry/dead-letter preservam o campo.
WITH atualizado AS (
    UPDATE sales_intelligence.outbox_events
       SET status = $1,
           attempts = attempts + $2::int,
           processed_at = CASE WHEN $1 = 'PROCESSED' THEN NOW() ELSE processed_at END,
           last_error = $3
     WHERE id = $4::uuid
     RETURNING id, status, attempts, processed_at
), trilha AS (
    INSERT INTO sales_intelligence.sync_events
        (id, entity_type, entity_id, source_system, target_system, operation, source_version,
         idempotency_key, status, request_payload, response_payload, completed_at, error_message)
    SELECT gen_random_uuid(), $5, $6::uuid, 'postgres', 'odoo', 'UPSERT', $7,
           $8, $9, $10::jsonb, $11::jsonb, NOW(), $12
      FROM atualizado
    ON CONFLICT (idempotency_key) DO UPDATE
       SET status = EXCLUDED.status,
           request_payload = EXCLUDED.request_payload,
           response_payload = EXCLUDED.response_payload,
           completed_at = EXCLUDED.completed_at,
           error_message = EXCLUDED.error_message
    RETURNING id, idempotency_key, status
)
SELECT a.id::text          AS outbox_id,
       a.status            AS status_outbox,
       a.attempts          AS attempts,
       a.processed_at      AS processed_at,
       t.id::text          AS sync_event_id,
       t.status            AS status_trilha
  FROM atualizado a
 CROSS JOIN trilha t;
