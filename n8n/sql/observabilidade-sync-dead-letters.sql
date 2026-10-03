-- ============================================================================
-- Detalhes da observabilidade da sincronizacao — card TRE-W3-E05-T01.
-- Contrato: n8n/contracts/observabilidade-sync.v1.json (`detalhes`).
--
-- O QUE ESTE ARQUIVO FAZ: lista, COM MOTIVO, o que deu errado — evento em
-- dead-letter, linha de trilha com falha transitoria e linha de trilha com
-- recusa definitiva. Doc 06 §7.3: "falha nao e' engolida: vai para dead-letter
-- com motivo, visivel". Contagem sem motivo nao e' observabilidade.
--
-- SOMENTE LEITURA, igual a consulta de metricas: nenhum INSERT/UPDATE/DELETE/
-- DDL/lock sobre `sales_intelligence.outbox_events` e `sales_intelligence.sync_events`.
--
-- O QUE ESTE ARQUIVO NAO DEVOLVE, DE PROPOSITO (contrato -> detalhes.nao_mostrar):
-- `payload`/`request_payload`/`response_payload` (podem carregar dado pessoal),
-- nenhuma credencial e nenhum corpo de erro longo: o MOTIVO e' nomeado e curto —
-- o nucleo trunca em 200 caracteres com marca (limite declarado no contrato).
--
-- O teto de linhas (200) e' o declarado no contrato (`detalhes.limite_de_linhas`);
-- a lente estrutural confere o espelho. A ordem e' do mais recente para o mais
-- antigo: o que acabou de falhar e' o que se le primeiro.
-- ============================================================================
WITH fila AS (
    SELECT o.id,
           o.event_type,
           o.status,
           o.attempts,
           o.created_at,
           o.processed_at,
           o.last_error,
           'outbox:' || o.id::text || ':' || trim(coalesce(o.event_type, '')) AS chave
      FROM sales_intelligence.outbox_events o
),
detalhes AS (
    -- 1) evento em DEAD_LETTER (motivo: last_error do evento; sem ele, a trilha
    --    da chave derivada — a mesma chave que o consumidor registrou)
    SELECT 'outbox_dead_letter'::text                              AS tipo,
           f.id::text                                              AS id,
           f.event_type                                            AS event_type,
           'postgres->odoo'::text                                  AS direcao,
           f.status                                                AS status,
           f.attempts                                              AS tentativas,
           coalesce(f.processed_at, f.created_at)                  AS quando,
           NULLIF(btrim(coalesce(f.last_error,
                                 (SELECT NULLIF(btrim(t.error_message), '')
                                    FROM sales_intelligence.sync_events t
                                   WHERE t.idempotency_key = f.chave
                                   ORDER BY t.created_at DESC
                                   LIMIT 1), '')), '')             AS motivo
      FROM fila f
     WHERE f.status = 'DEAD_LETTER'
    UNION ALL
    -- 2) linha de trilha com FALHA transitoria registrada
    SELECT 'trilha_falha'::text,
           t.id::text,
           t.operation,
           t.source_system || '->' || t.target_system,
           t.status,
           NULL::integer,
           coalesce(t.completed_at, t.created_at),
           NULLIF(btrim(coalesce(t.error_message, '')), '')
      FROM sales_intelligence.sync_events t
     WHERE t.status = 'FAILED'
    UNION ALL
    -- 3) linha de trilha com RECUSA definitiva (motivo nomeado pelo contrato da porta)
    SELECT 'trilha_recusa'::text,
           t.id::text,
           t.operation,
           t.source_system || '->' || t.target_system,
           t.status,
           NULL::integer,
           coalesce(t.completed_at, t.created_at),
           NULLIF(btrim(coalesce(t.error_message, '')), '')
      FROM sales_intelligence.sync_events t
     WHERE t.status = 'REFUSED'
)
SELECT tipo, id, event_type, direcao, status, tentativas, quando, motivo
  FROM detalhes
 ORDER BY quando DESC, id
 LIMIT 200;
