-- ============================================================================
-- Metricas da observabilidade da sincronizacao — card TRE-W3-E05-T01.
-- Contrato: n8n/contracts/observabilidade-sync.v1.json (`fontes`, `metricas`,
-- `vocabulario_da_trilha`, `observado`).
--
-- O QUE ESTE ARQUIVO FAZ: devolve a MEDICAO da sincronizacao — uma linha por
-- metrica declarada (colunas `metrica`, `valor`, `dimensao`). Quem decide o
-- veredito e' o nucleo (n8n/codigo/observabilidade-sync.js) contra os limiares
-- DECLARADOS; aqui nao existe limiar, nem "OK", nem alerta.
--
-- SOMENTE LEITURA (contrato -> fontes.somente_leitura): so' SELECT/WITH sobre
-- `sales_intelligence.outbox_events` e `sales_intelligence.sync_events`.
-- Nenhum INSERT/UPDATE/DELETE/DDL/lock. A observabilidade que escreve no que
-- observa nao serve para julgar o que ela propria mexeu.
--
-- REGRAS DECLARADAS QUE APARECEM AQUI (nada implicito):
--   * todo agregado devolve UMA linha — nao devolver significa que a
--     superficie quebrou, e o nucleo fecha em INDETERMINADO (o aceite mede
--     isso: metrica declarada sem linha);
--   * `ausencia: zero` (declarado por metrica no contrato) e' materializado
--     pelo COALESCE: fila vazia e trilha vazia sao INFORMACAO (nada esperando,
--     nada atrasado), nao ausencia de medicao;
--   * `fila_no_teto` usa o teto DECLARADO no contrato do consumidor
--     (n8n/contracts/outbox-consumer.v1.json -> retry.teto_de_tentativas = 3).
--     O espelho entre os dois contratos e' conferido pela lente estrutural:
--     este numero e' declarado, nao escolhido aqui;
--   * `trilha_direcao_nao_declarada` conta o que NAO esta' no vocabulario
--     declarado (as duas portas): linha de outra direcao e' divergencia, nao
--     "outras";
--   * a metrica dimensional `trilha_por_status` traz as combinacoes DECLARADAS
--     que existem na base; a combinacao ausente vale 0 no nucleo (declarado).
--
-- O QUE NAO EXISTE AQUI DE PROPOSITO: contagem de replay/dedup. Os dois lados
-- da trilha da V1 nao guardam marca de que a entrega foi reaproveitada (o
-- replay preserva a linha de trilha, por contrato) — e' LACUNA DECLARADA no
-- contrato de observabilidade (`lacunas_declaradas`). O que se mede, e pega o
-- replay quebrado, e' `outbox_processado_sem_trilha`: sucesso sem prova.
-- ============================================================================
WITH fila AS (
    SELECT o.id,
           o.event_type,
           o.status,
           o.attempts,
           o.created_at,
           o.last_error,
           -- chave derivada do PROPRIO evento, igual a do consumidor
           -- (contrato do consumidor -> trilha.chave_de_idempotencia.derivacao):
           -- 'outbox:<outbox_events.id>:<event_type>'. E' o que liga um evento
           -- da fila a sua linha de trilha na conferencia de sucesso.
           'outbox:' || o.id::text || ':' || trim(coalesce(o.event_type, '')) AS chave
      FROM sales_intelligence.outbox_events o
),
trilha AS (
    SELECT t.id,
           t.idempotency_key,
           t.source_system,
           t.target_system,
           t.status,
           t.completed_at,
           t.created_at,
           t.error_message
      FROM sales_intelligence.sync_events t
),
direcao_declarada AS (
    -- vocabulario declarado (contrato -> vocabulario_da_trilha.direcoes_declaradas)
    SELECT d.source_system, d.target_system
      FROM (VALUES ('postgres', 'odoo'),
                   ('odoo', 'postgres')) AS d(source_system, target_system)
),
medidas AS (
    SELECT 'fila_pendentes'::text AS metrica,
           (SELECT count(*) FROM fila WHERE status = 'PENDING')::double precision AS valor,
           '{}'::jsonb AS dimensao
    UNION ALL
    SELECT 'fila_retry',
           (SELECT count(*) FROM fila WHERE status = 'RETRY')::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'fila_idade_maxima_s',
           COALESCE((SELECT extract(epoch FROM (now() - min(created_at)))
                       FROM fila
                      WHERE status = ANY (ARRAY['PENDING', 'RETRY'])), 0)::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'fila_no_teto',
           (SELECT count(*) FROM fila
             WHERE status = ANY (ARRAY['PENDING', 'RETRY'])
               AND coalesce(attempts, 0) >= 3)::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'dead_letter_total',
           (SELECT count(*) FROM fila WHERE status = 'DEAD_LETTER')::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'dead_letter_sem_motivo',
           (SELECT count(*)
              FROM fila f
             WHERE f.status = 'DEAD_LETTER'
               AND coalesce(btrim(f.last_error), '') = ''
               AND NOT EXISTS (SELECT 1
                                 FROM trilha t
                                WHERE t.idempotency_key = f.chave
                                  AND coalesce(btrim(t.error_message), '') <> ''))::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'outbox_processado_total',
           (SELECT count(*) FROM fila WHERE status = 'PROCESSED')::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'outbox_processado_sem_trilha',
           (SELECT count(*)
              FROM fila f
             WHERE f.status = 'PROCESSED'
               AND NOT EXISTS (SELECT 1
                                 FROM trilha t
                                WHERE t.idempotency_key = f.chave
                                  AND t.status = 'COMPLETED'))::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'trilha_falhas',
           (SELECT count(*) FROM trilha WHERE status = 'FAILED')::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'trilha_recusas',
           (SELECT count(*) FROM trilha WHERE status = 'REFUSED')::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'trilha_direcao_nao_declarada',
           (SELECT count(*)
              FROM trilha t
             WHERE NOT EXISTS (SELECT 1
                                 FROM direcao_declarada d
                                WHERE d.source_system = t.source_system
                                  AND d.target_system = t.target_system))::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'trilha_sem_conclusao',
           (SELECT count(*) FROM trilha
             WHERE status = 'COMPLETED' AND completed_at IS NULL)::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'trilha_ultima_atividade_s',
           COALESCE((SELECT extract(epoch FROM (now() - max(coalesce(completed_at, created_at))))
                       FROM trilha), 0)::double precision,
           '{}'::jsonb
    UNION ALL
    SELECT 'trilha_total',
           (SELECT count(*) FROM trilha)::double precision,
           '{}'::jsonb
    UNION ALL
    -- metrica DIMENSIONAL: uma linha por DIRECAO DECLARADA x STATUS DECLARADO — a grade
    -- inteira, mesmo sem atividade. E' aqui que "ausencia de ATIVIDADE" (zero sincronizacao
    -- naquela porta naquela rodada = informacao) se separa de "ausencia de MEDICAO" (a
    -- consulta nao devolver o que o contrato declara = INDETERMINADO): a grade vem de
    -- CROSS JOIN do vocabulario declarado, com LEFT JOIN da trilha (contagem, nao presenca).
    -- Direcao fora do vocabulario NAO entra aqui — quem a conta e' `trilha_direcao_nao_declarada`.
    SELECT 'trilha_por_status'::text,
           count(t.id)::double precision,
           jsonb_build_object('source_system', g.source_system,
                              'target_system', g.target_system,
                              'status', g.status)
      FROM (SELECT d.source_system, d.target_system, s.status
              FROM direcao_declarada d
             CROSS JOIN (VALUES ('COMPLETED'), ('FAILED'), ('REFUSED')) AS s(status)) AS g
      LEFT JOIN trilha t
        ON t.source_system = g.source_system
       AND t.target_system = g.target_system
       AND t.status = g.status
     GROUP BY g.source_system, g.target_system, g.status
)
SELECT metrica, valor, dimensao
  FROM medidas
 ORDER BY metrica, dimensao::text;
