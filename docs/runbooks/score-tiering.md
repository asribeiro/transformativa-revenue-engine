# Runbook — Tiering v1 (`tiering-v1`)

**Card:** TRE-W5-E06-T01 · **Componente:** `hermes/scores/tiering/tiering.py` · **Ambiente:** dev / homolog
(o banco vive na VPS do ambiente — ADR-0008; `prod` é recusado com exit 4 pela ADR-005).

## 1. O que faz, em uma linha

Lê o **último score `PRIORITY`** da empresa e grava o **tier** (faixa de `scores.tiers` do Data
Contract) no registro auditado (`sync_events.request_payload` + `agent_runs`). Não escreve em `scores`.

## 2. Comandos

```bash
# o que seria feito (não abre conexão)
python3 hermes/scores/tiering/tiering.py --planejar

# a tabela de faixas vigente, lida do contrato (não abre conexão)
python3 hermes/scores/tiering/tiering.py --faixas

# classificar UMA empresa
python3 hermes/scores/tiering/tiering.py --ambiente dev --organizacao <uuid> \
    --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
    --relatorio /tmp/tiering-rodada.json

# classificar um lote (o arquivo escolhe o SUJEITO, não o dado)
python3 hermes/scores/tiering/tiering.py --ambiente dev --fonte organizacoes.jsonl \
    --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"

# desfazer uma rodada (dry-run por padrão; --confirmo aplica)
python3 hermes/scores/tiering/tiering.py --ambiente dev --desfazer <correlation_id>
python3 hermes/scores/tiering/tiering.py --ambiente dev --desfazer <correlation_id> --confirmo
```

## 3. Vereditos

| Veredito | Significado |
|---|---|
| `CLASSIFICADO` | registro gravado em `sync_events` (`operation='TIER'`, `status='PROCESSED'`) |
| `JA_CLASSIFICADO` | a chave da mesma entrada já existia: nada duplicado (`gravados=0`) |
| `RECUSADA` | `SEM_PRIORITY`, `PRIORITY_VENCIDO`, `PRIORITY_ILEGIVEL`, `PRIORITY_FORA_DA_FAIXA`, `ORGANIZACAO_NAO_ENCONTRADA` ou `CONTRATO_INCOERENTE` — nada escrito em `sync_events`; a recusa fica em `agent_runs` (`REJECTED`) |
| `ERRO` | falha de banco/porta (`agent_runs` `FAILED`) |

## 4. Conferência do que foi gravado

```sql
SELECT entity_id, status, request_payload->>'tier'      AS tier,
       request_payload->'faixa'->>'min'                 AS faixa_min,
       request_payload->'faixa'->>'max'                 AS faixa_max,
       request_payload->'score_lido'->>'score_id'       AS priority_lido,
       request_payload->'score_lido'->>'score_value'    AS valor_lido,
       request_payload->>'entrada_hash'                 AS entrada_hash,
       idempotency_key
FROM sales_intelligence.sync_events
WHERE operation = 'TIER' ORDER BY created_at DESC;
```

- `scores` **não** deve ter nenhuma linha nova por causa desta rodada (nem `score_type='TIER'`).
- A auditoria da rodada: `SELECT status, output FROM sales_intelligence.agent_runs WHERE agent_name='tiering' ORDER BY finished_at DESC;`

## 5. Diagnóstico rápido

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `SEM_PRIORITY` | a empresa não tem score `PRIORITY` | rodar o card E05 (`priority_score.py`) para a empresa |
| `PRIORITY_VENCIDO` | `valid_until` do PRIORITY passou (30 dias) | recalcular o PRIORITY (E05) e rodar de novo |
| `CONTRATO_INCOERENTE` | as faixas do Data Contract não cobrem 0–100 (lacuna/sobreposição) | conferir `docs/data/data_contract_v1.json#scores.tiers` e `scripts/verificar_contrato_dados.py` antes de qualquer mudança de faixa |
| `JA_CLASSIFICADO` com tier diferente do esperado | o PRIORITY não mudou de identidade | é o comportamento correto: PRIORITY novo ⇒ registro novo |
| exit 4 | `--ambiente prod` | promoção é card de release com aprovação humana registrada |

## 6. Evidência (este card)

Suíte `26 itens / 0 falhas` + autoteste `10/10`; aceite E2E em container descartável com dente por
item esperado. Números medidos e o sha256 do código sob teste ficam em
`docs/operations/registro-de-execucoes.md` (entrada `TRE-W5-E06-T01`).
