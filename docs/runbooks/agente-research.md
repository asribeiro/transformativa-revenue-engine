# Runbook — Agente Research v1 (`research/1.0.0`)

**Card:** `TRE-W4-E02-T01` · **Contrato:** `docs/architecture/agente-research-v1.md` ·
**Artefato legível por máquina:** `hermes/agents/research/agente-research-v1.json`

Quem opera: o Hermes Dev Harness (agente `research`, papel `research`). O que ele faz: resolve a
empresa **que já existe** pelos identificadores fortes, grava a execução da pesquisa em
`research_runs` e **enriquece as colunas vazias** da organização (não sobrescreve o que já estava
preenchido). O que ele **não** faz: criar empresa, escrever identidade/estágio/score, detectar
sinais, criar hipótese de dor ou contato, emitir evento de outbox, falar com a rede ou escrever em
produção.

## 1. Onde o agente roda

O PostgreSQL vive na VPS do ambiente (ADR-0008); o container do Hermes **não** tem socket Docker
nem rota TCP para o banco. Por isso o agente recebe o **prefixo psql** do ambiente e o SQL sai por
`docker exec`:

| Ambiente | Container | Prefixo |
|---|---|---|
| dev | `pg-sales-dev` | `docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence` |
| homolog | `pg-sales-homolog` | idem, trocando o container |

O agente registra no relatório a identidade do alvo **medido** (`current_database()`,
`current_user`) e não afirma nome de ambiente — a convenção de nome divergiu no dev e alinhar isso
é decisão do dono.

## 2. Rodar uma rodada

```bash
# 1) ensaio sem banco (não abre conexão nenhuma)
python3 hermes/agents/research/research.py --planejar --fonte /tmp/pesquisas.jsonl

# 2) rodada no dev (não existe caminho para prod nesta versão)
python3 hermes/agents/research/research.py --ambiente dev \
  --fonte /tmp/pesquisas.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/research-$(date -u +%Y%m%dT%H%M%SZ).json
```

O relatório traz `por_veredito` (contagem por veredito) e, por pedido, `veredito`, `motivos`,
`organization_id`, `research_run_id`, `idempotency_key`, `colunas_enriquecidas`,
`colunas_preservadas` e `descartados`.

Exit codes: `0` OK · `1` houve `ERRO` em pedido ou falha de porta · `2` uso incorreto ·
`4` recusou o ambiente (prod) · `5` fonte ilegível/inexistente.

## 3. Ler o resultado

| Veredito | Significado | O que fazer |
|---|---|---|
| `PESQUISADA` | `research_run` gravada e colunas vazias enriquecidas | nada — é o caminho normal |
| `JA_PESQUISADO` | a mesma entrada já tinha sido pesquisada (`IDEMPOTENCIA_REPLAY`) | nada — retry idempotente |
| `REVISAO_IDENTIDADE` | a identidade casou com **duas ou mais** empresas | humano decide: `human_approvals` (`RESEARCH_IDENTITY_REVIEW`, `PENDING`) |
| `RECUSADA` | pedido inválido ou empresa inexistente | corrigir a fonte (`SEM_FONTE`, `FONTE_DESCONHECIDA`, `TIPO_DE_PESQUISA_DESCONHECIDO`, `SEM_IDENTIFICADOR_FORTE`, `IDENTIFICADOR_FORTE_INVALIDO`, `ORGANIZACAO_NAO_ENCONTRADA`) |
| `ERRO` | falha de banco/porta ou auditoria não registrada | ler `motivos`; nada foi escrito fora de `agent_runs` |

O que a pesquisa **descartou** (achado fora do tipo, identificador forte, faixa vinda da fonte,
campo não declarado, valor inválido) está em `research_runs.structured_output.achados_descartados`,
com motivo por campo — nunca em silêncio.

## 4. Consultas úteis

```sql
-- o que a rodada fez (trocar o correlation_id)
SELECT r.id, r.research_type, r.status, r.source_count, r.confidence,
       r.structured_output->'colunas_enriquecidas' AS enriquecidas,
       r.structured_output->'colunas_preservadas'  AS preservadas
FROM sales_intelligence.research_runs r
JOIN sales_intelligence.agent_runs a ON (a.output->>'research_run_id') = r.id::text
WHERE a.correlation_id = '<correlation_id>' AND a.agent_name = 'research';

-- o que a pesquisa escreveu, coluna por coluna (antes/depois), para auditar
SELECT s.idempotency_key, s.request_payload->'antes' AS antes, s.request_payload->'depois' AS depois
FROM sales_intelligence.sync_events s
WHERE s.operation = 'RESEARCH' AND s.request_payload->>'correlation_id' = '<correlation_id>';

-- fila humana de identidade aberta pela pesquisa
SELECT id, proposed_action->>'motivo', requested_at
FROM sales_intelligence.human_approvals
WHERE action_type = 'RESEARCH_IDENTITY_REVIEW' AND status = 'PENDING';
```

## 5. Desfazer uma rodada

Dry-run por padrão — nunca apaga nem restaura sem `--confirmo`:

```bash
python3 hermes/agents/research/research.py --desfazer <correlation_id> \
  --ambiente dev --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
# confere a lista de research_runs e organizações afetadas; depois:
python3 hermes/agents/research/research.py --desfazer <correlation_id> --confirmo \
  --ambiente dev --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

O desfazer **restaura** as colunas enriquecidas para os valores anteriores (gravados no
`sync_events` da rodada, com o tipo da coluna), **apaga** os `research_runs` e `sync_events` da
rodada e grava um `sync_events` de `ROLLBACK`. Não apaga organização, não toca `agent_runs`
(auditoria) e não mexe na `human_approvals`.

**Limite declarado:** o desfazer restaura o que consegue provar pelas `research_runs` da rodada. Se
um `research_run` for removido por fora, o `sync_event` correspondente fica órfão e a coluna que
ele enriqueceu não é restaurada — o órfão continua visível para investigação. Medido no aceite
(rodada 3, cenário de replay).

## 6. Verificação

```bash
# offline (sem banco e sem rede): 65 itens + 15 mutações
python3 scripts/agentes/verificar_agente_research.py --autoteste

# E2E em container descartável NA VPS (o aceite recusa rodar se o container já existir)
bash scripts/agentes/teste_research_aceite.sh              # espera ACEITE_RESEARCH_001_OK
bash scripts/agentes/teste_research_aceite.sh --prova-de-dente   # + 4 mutações, cada uma pelo item esperado

# estrutura (artefatos versionados)
bash scripts/verificar_estrutura.sh
```

O aceite E2E usa o container `pg-research-acc` (`TRE_RESEARCH_CONTAINER` para trocar), aplica a
migration 0001 num schema limpo e remove o container ao final (`--manter` deixa para inspeção).

## 7. Quando parar e chamar humano

- `ERRO` que se repete na mesma fonte → investigar a porta (`docker exec` funciona? porta aberta?)
  antes de reexecutar; o agente é fail-closed, então nada ficou pela metade.
- `REVISAO_IDENTIDADE` acumulando → a base tem duplicidade de identidade: o caminho é o módulo de
  dedup (`TRE-W1-E04-T01`) e a revisão humana, nunca afrouxar a regra.
- Achado de identificador forte que a empresa não tem → **é decisão humana** escrever identidade
  (o agente não escreve): use a `deduplicacao-strong-identifiers` para casar antes.
- Necessidade de escrever em produção → não é caminho desta versão: promoção é card próprio com
  aprovação humana registrada (ADR-005).
