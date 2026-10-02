-- Le a TRILHA pelas chaves de um lote de eventos — card TRE-W3-E02-T02 (dedup por chave).
-- Contrato: n8n/contracts/outbox-consumer.v1.json -> dedup (criterio_de_replay, consulta_da_chave).
--
-- Somente LEITURA. O status de sucesso NAO e' filtrado aqui DE PROPOSITO: quem decide o replay
-- e' o nucleo, que compara o status devolvido com o declarado no contrato. Filtrar no SQL
-- esconderia do nucleo a diferenca entre "nao ha trilha para a chave" e "ha trilha SEM status de
-- sucesso" — e essa diferenca muda a decisao (a primeira nao diz nada; a segunda manda o evento
-- de volta para a fila: falha transitoria NAO autoriza reaproveitar o registro).
--
-- Parametros:
--   $1  chaves do lote (text[]) — derivadas do PROPRIO evento pelo nucleo (`outbox:<id>:<event_type>`)
SELECT t.idempotency_key,
       t.id::text AS trilha_id,
       t.status,
       t.completed_at,
       t.response_payload
  FROM sales_intelligence.sync_events t
 WHERE t.idempotency_key = ANY ($1::text[]);
