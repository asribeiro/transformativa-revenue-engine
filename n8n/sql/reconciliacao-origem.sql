-- Lado PostgreSQL da RECONCILIACAO — card TRE-W3-E04-T01.
-- Contrato: n8n/contracts/reconciliation-job.v1.json -> fontes.origem, lote, operacoes_de_espelho,
--           vinculo_da_trilha, campos_do_vinculo.
--
-- SOMENTE LEITURA (contrato -> fontes.origem.somente_leitura): so' SELECT/WITH sobre
-- `sales_intelligence.organizations` e `sales_intelligence.sync_events`. Nenhum INSERT/UPDATE/DELETE/
-- DDL/lock. O job nao conserta NADA (doc 06 §8: 'nunca corrigir silenciosamente dados ambiguos') —
-- ele mede e nomeia; quem corrige e' operador, com o relatorio na mao.
--
-- O QUE ESTE ARQUIVO DEVOLVE (duas familias de linha, distinguidas por `tipo`):
--   * `entidade`  — uma linha por organizacao do LOTE, com o que a rodada precisa para reconciliar:
--                   a ponta do vinculo no PostgreSQL (`odoo_partner_id`), os identificadores fortes,
--                   se o espelho era ESPERADO e qual foi a operacao do espelho;
--   * `cobertura` — UMA linha com o TOTAL de organizacoes da base e o LIMITE do lote. E' ela que
--                   distingue BASE VAZIA (informacao: nada a reconciliar) de CONSULTA QUEBRADA
--                   (o nucleo fecha INDETERMINADO quando a linha de cobertura nao vem).
--
-- VALORES ESPELHADOS (a lente estrutural confere; este arquivo nao os escolhe):
--   * operacoes de espelho `COMPANY_QUALIFIED`/`COMPANY_UPDATED` — contrato deste job
--     (`operacoes_de_espelho`) e, na origem, n8n/contracts/outbox-consumer.v1.json -> eventos;
--   * direcao `postgres`->`odoo`, operacao `UPSERT` e status de sucesso `COMPLETED` —
--     contrato deste job (`vinculo_da_trilha`) espelhando n8n/contracts/outbox-consumer.v1.json
--     -> trilha (source_system, target_system, operation, status.sucesso);
--   * o event_type e' lido da CHAVE DERIVADA do evento (`outbox:<evento>:<event_type>`), que e' a
--     derivacao declarada no contrato do consumidor -> trilha.chave_de_idempotencia.derivacao: a
--     tabela de trilha da V1 nao tem coluna de event_type e inventar uma leitura paralela seria
--     ler o que nao foi gravado;
--   * o LIMITE do lote (200) e a ordem (`updated_at DESC, id ASC`) — contrato deste job -> lote.
--
-- DE PROPOSITO NAO ENTRA AQUI: contagem de eventos, dead-letter ou metrica qualquer. Observar a
-- sincronizacao e' o card TRE-W3-E05-T01; aqui a fila entra so' como DIVERGENCIA entre a fila e a
-- trilha (n8n/sql/reconciliacao-pendentes.sql).
WITH lote AS (
    SELECT o.id,
           o.odoo_partner_id,
           o.legal_name,
           o.trade_name,
           o.cnpj,
           o.domain,
           o.linkedin_url,
           o.status,
           o.updated_at
      FROM sales_intelligence.organizations o
     ORDER BY o.updated_at DESC, o.id ASC
     LIMIT 200
),
espelho AS (
    -- A trilha do espelho: a linha da direcao declarada, operacao declarada, status de sucesso
    -- declarado, ligada pela coluna do vinculo (`entity_id`) e cujo event_type (extraido da chave
    -- derivada) esta' entre as operacoes de espelho declaradas.
    SELECT t.entity_id                                                       AS organizacao_id,
           min(substring(t.idempotency_key from '^outbox:[^:]+:(.*)$'))      AS operacao_do_espelho,
           count(*)                                                          AS linhas
      FROM sales_intelligence.sync_events t
     WHERE t.source_system = 'postgres'
       AND t.target_system = 'odoo'
       AND t.operation = 'UPSERT'
       AND t.status = 'COMPLETED'
       AND substring(t.idempotency_key from '^outbox:[^:]+:(.*)$')
           = ANY (ARRAY['COMPANY_QUALIFIED', 'COMPANY_UPDATED'])
     GROUP BY t.entity_id
),
linhas AS (
    SELECT 'entidade'::text                                            AS tipo,
           l.id::text                                                  AS entidade_id,
           COALESCE(NULLIF(btrim(l.trade_name), ''), btrim(l.legal_name)) AS nome,
           l.odoo_partner_id                                           AS odoo_partner_id,
           NULLIF(btrim(l.cnpj), '')                                   AS cnpj,
           NULLIF(btrim(l.domain), '')                                 AS domain,
           NULLIF(btrim(l.linkedin_url), '')                           AS linkedin_url,
           l.status                                                    AS status,
           (l.odoo_partner_id IS NOT NULL OR e.organizacao_id IS NOT NULL) AS esperada,
           e.operacao_do_espelho                                       AS operacao_do_espelho,
           (e.organizacao_id IS NOT NULL)                              AS espelho_entregue,
           NULL::bigint                                                AS total_de_organizacoes,
           NULL::integer                                               AS limite
      FROM lote l
      LEFT JOIN espelho e ON e.organizacao_id = l.id
)
SELECT tipo, entidade_id, nome, odoo_partner_id, cnpj, domain, linkedin_url, status,
       esperada, operacao_do_espelho, espelho_entregue, total_de_organizacoes, limite
  FROM linhas
UNION ALL
SELECT 'cobertura'::text,
       NULL, NULL, NULL, NULL, NULL, NULL, NULL,
       NULL, NULL, NULL,
       (SELECT count(*) FROM sales_intelligence.organizations),
       200
 ORDER BY tipo, entidade_id;
