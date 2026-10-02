-- Leitura da fila do outbox — card TRE-W3-E02-T01.
-- Contrato: n8n/contracts/outbox-consumer.v1.json -> origem (status_lidos, limite_por_ciclo).
-- Somente LEITURA: o consumidor nunca escreve na tabela do produtor; quem muda o estado do
-- evento e' a propria entrega (n8n/sql/registrar-resultado.sql).
--
-- O ENVELOPE (data contract §6): a tabela V1 nao tem coluna `event_version`, entao o envelope
-- viaja dentro do jsonb `payload` como o doc 12 §2 o define:
--     {"event_version": "1.0", "payload": {<campos do fato>}}
-- `event_version` e' lido da RAIZ do jsonb (e' o que se exige: sem ele o evento e' recusado,
-- contrato §6 regra 5). Os campos do fato sao lidos de `payload.payload`; quando o produtor
-- escreve o fato direto na raiz do jsonb, a raiz mesma serve (por isso o COALESCE) — o que NAO
-- se abre mao e' do `event_version`.
SELECT id::text                            AS id,
       aggregate_type                      AS aggregate_type,
       aggregate_id::text                  AS aggregate_id,
       event_type                          AS event_type,
       payload ->> 'event_version'         AS event_version,
       COALESCE(payload -> 'payload', payload) AS payload,
       attempts                            AS attempts,
       status                              AS status
  FROM sales_intelligence.outbox_events
 WHERE status = ANY (ARRAY['PENDING', 'RETRY'])
 ORDER BY created_at ASC, id ASC
 LIMIT 20;
