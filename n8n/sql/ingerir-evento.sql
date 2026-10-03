-- Trilha da ingestao dos eventos Odoo -> PostgreSQL — card TRE-W3-E03-T01.
-- Contrato: n8n/contracts/odoo-events-ingest.v1.json (`trilha`).
--
-- O QUE ESTE ARQUIVO FAZ: escreve a linha de TRILHA do evento aceito em
-- `sales_intelligence.sync_events`, de forma IDEMPOTENTE pela chave: `ON CONFLICT (idempotency_key)
-- DO NOTHING`. Reenvio do mesmo fato (retry do produtor, replay, entrega duplicada da porta) NAO
-- cria linha duplicada — a porta responde `duplicado: true` (data contract §6 regra 2).
--
-- Parametros (a ordem e' o contrato deste arquivo; o workflow passa uma LISTA, nunca texto):
--   $1 idempotency_key   (texto, chave derivada do FATO pelo produtor)
--   $2 operation         (o proprio event_type do envelope)
--   $3 entity_type       (o modelo de origem declarado: crm.lead | mail.activity | calendar.event)
--   $4 entity_id         (uuid canonico do contrato §3, ou NULL: nunca se inventa UUID)
--   $5 source_version    (o event_version do envelope)
--   $6 request_payload   (jsonb: o envelope integral)
--   $7 status            (COMPLETED)
--   $8 error_message     (NULL no aceite)
--
-- O que NAO existe aqui de proposito: nenhuma escrita em tabela de negocio (o contrato V1 nao tem
-- tabela de historico de funil — a trilha guarda o payload integral e a materializacao e' derivavel),
-- e nenhuma correcao de dado (reconciliacao e' o card TRE-W3-E04-T01, e corrigir em silencio e'
-- proibido pelo doc 06 §8).
WITH inserido AS (
    INSERT INTO sales_intelligence.sync_events
        (id, entity_type, entity_id, source_system, target_system, operation, source_version,
         idempotency_key, status, request_payload, completed_at, error_message)
    VALUES (gen_random_uuid(), $3, $4::uuid, 'odoo', 'postgres', $2, $5,
            $1, $7, $6::jsonb, NOW(), $8)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id
),
existente AS (
    -- Le a linha PRE-EXISTENTE (o snapshot do comando nao enxerga o INSERT do CTE acima):
    -- e' o que distingue "gravei agora" de "ja' estava na trilha".
    SELECT id FROM sales_intelligence.sync_events WHERE idempotency_key = $1
)
SELECT COALESCE((SELECT id FROM inserido), (SELECT id FROM existente))::text AS sync_event_id,
       (SELECT count(*) FROM inserido) > 0                                   AS inserido,
       200                                                                   AS codigo_http,
       json_build_object(
           'aceito', true,
           'motivo', NULL::text,
           'duplicado', NOT ((SELECT count(*) FROM inserido) > 0),
           'sync_event_id',
               COALESCE((SELECT id FROM inserido), (SELECT id FROM existente))::text,
           'event_type', NULLIF($2, '')
       )                                                                      AS corpo;
