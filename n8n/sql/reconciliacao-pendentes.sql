-- Lado PostgreSQL da reconciliacao: FILA x TRILHA — card TRE-W3-E04-T01.
-- Contrato: n8n/contracts/reconciliation-job.v1.json -> fontes.pendentes, janela_de_pendencia_s,
--           vinculo_da_trilha. Espelhos: n8n/contracts/outbox-consumer.v1.json -> status.entrada
--           (PENDING/RETRY), retry.teto_de_tentativas (3), status.recusa (DEAD_LETTER),
--           trilha.status.sucesso (COMPLETED) e trilha.chave_de_idempotencia.derivacao.
--
-- SOMENTE LEITURA (contrato -> fontes.pendentes.somente_leitura): so' SELECT/WITH sobre
-- `sales_intelligence.outbox_events` e `sales_intelligence.sync_events`. Nenhum INSERT/UPDATE/DELETE/
-- DDL/lock: quem muda estado de evento e' quem entrega (o consumidor), nunca o reconciliador.
--
-- O QUE ESTE ARQUIVO DEVOLVE (duas familias de linha, distinguidas por `tipo`):
--   * `pendente` — uma linha por evento NA FILA (status de entrada declarado), com a idade em
--                  segundos, as tentativas e o status da TRILHA da chave derivada daquele evento.
--                  E' o insumo das comparacoes P1..P4: o que a FILA diz e o que a TRILHA diz tem de
--                  contar a mesma historia;
--   * `cobertura` — UMA linha com o total da fila por status (PENDING/RETRY). Fila vazia e'
--                  INFORMACAO (nada esperando) e chega como linha com zero; consulta quebrada nao
--                  devolve a linha e o nucleo fecha INDETERMINADO.
--
-- LIMITE DE PROPOSITO: nada de metrica, dead-letter ou idade agregada — isso e' a observabilidade
-- (card TRE-W3-E05-T01). Aqui a fila so' entra onde ela DISCORDA da trilha do mesmo fato.
--
-- A chave derivada (`outbox:<outbox_events.id>:<event_type>`) e' a MESMA do consumidor
-- (contrato -> trilha.chave_de_idempotencia.derivacao): sem ela nao haveria como casar o evento da
-- fila com a linha da trilha sem heuristica.
WITH fila AS (
    SELECT o.id,
           o.event_type,
           o.aggregate_id,
           o.status,
           o.attempts,
           o.created_at,
           o.last_error,
           'outbox:' || o.id::text || ':' || trim(coalesce(o.event_type, '')) AS chave
      FROM sales_intelligence.outbox_events o
      WHERE o.status = ANY (ARRAY['PENDING', 'RETRY'])
      ORDER BY o.created_at ASC, o.id ASC
      LIMIT 200
      ),
trilha AS (
    SELECT t.idempotency_key,
           t.status,
           t.operation,
           t.source_system,
           t.target_system
      FROM sales_intelligence.sync_events t
),
linhas AS (
    SELECT 'pendente'::text                                              AS tipo,
           f.id::text                                                    AS evento_id,
           f.chave                                                       AS chave,
           f.event_type                                                  AS event_type,
           f.aggregate_id::text                                          AS aggregate_id,
           f.status                                                      AS status,
           f.attempts                                                    AS attempts,
           EXTRACT(EPOCH FROM (now() - f.created_at))                    AS idade_s,
           NULLIF(btrim(coalesce(f.last_error, '')), '')                 AS motivo,
           t.status                                                      AS status_da_trilha,
           (t.idempotency_key IS NOT NULL)                               AS tem_trilha,
           NULL::bigint                                                  AS total_pending,
           NULL::bigint                                                  AS total_retry
      FROM fila f
      LEFT JOIN trilha t ON t.idempotency_key = f.chave
)
SELECT tipo, evento_id, chave, event_type, aggregate_id, status, attempts, idade_s, motivo,
       status_da_trilha, tem_trilha, total_pending, total_retry
  FROM linhas
UNION ALL
SELECT 'cobertura'::text,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       (SELECT count(*) FROM sales_intelligence.outbox_events WHERE status = 'PENDING'),
       (SELECT count(*) FROM sales_intelligence.outbox_events WHERE status = 'RETRY')
 ORDER BY tipo, evento_id;
