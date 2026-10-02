-- Estado final do REPLAY por chave — card TRE-W3-E02-T02 (dedup).
-- Contrato: n8n/contracts/outbox-consumer.v1.json -> dedup (registro_do_replay, guarda, preserva).
--
-- O evento que voltou para a fila (retry/replay) e cuja chave JA' foi entregue e' FINALIZADO
-- reaproveitando o registro da trilha: nada e' escrito na trilha e nenhuma entrega nova acontece.
--
-- Parametros (a ordem e' o contrato deste arquivo; o workflow passa uma LISTA, nunca texto):
--   $1  idempotency_key da trilha (UNIQUE no contrato: a chave derivada do evento)
--   $2  id do evento (uuid)
--
-- Regras da casa que aparecem aqui de proposito:
--   * a linha da trilha NAO e' tocada: o replay preserva id, completed_at e response_payload —
--     o primeiro instante de conclusao e a prova do efeito ja' acontecido continuam valendo;
--   * attempts NAO e' incrementado: replay nao e' tentativa de entrega (contrato -> dedup);
--   * processed_at ja' marcado no primeiro sucesso e' preservado (COALESCE);
--   * o evento tem de estar NA FILA (status do contrato -> status.entrada): evento fora dela nao
--     e' finalizado por este caminho — quem ja' esta' PROCESSED nao volta a ser "finalizado";
--   * GUARDA fail-closed: sem trilha da chave com o status de sucesso o UPDATE casa ZERO linhas e
--     o SELECT devolve ZERO linhas — nada e' finalizado e nenhum sucesso e' inventado.
WITH finalizado AS (
    UPDATE sales_intelligence.outbox_events
       SET status = 'PROCESSED',
           last_error = NULL,
           processed_at = COALESCE(processed_at, NOW())
     WHERE id = $2::uuid
       AND status = ANY (ARRAY['PENDING', 'RETRY'])
       AND EXISTS (SELECT 1
                     FROM sales_intelligence.sync_events t
                    WHERE t.idempotency_key = $1
                      AND t.status = 'COMPLETED')
     RETURNING id, status, attempts, processed_at
)
SELECT f.id::text         AS outbox_id,
       f.status           AS status_outbox,
       f.attempts         AS attempts,
       f.processed_at     AS processed_at,
       t.id::text         AS trilha_id,
       t.idempotency_key  AS chave,
       t.status           AS status_trilha,
       t.completed_at     AS trilha_completed_at,
       t.response_payload AS response_payload
  FROM finalizado f
  JOIN sales_intelligence.sync_events t ON t.idempotency_key = $1;
