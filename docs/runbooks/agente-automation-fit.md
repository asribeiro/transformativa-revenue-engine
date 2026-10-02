# Runbook — Automation Fit Score v1 (`automation_fit/1.0.0`)

**Card:** `TRE-W5-E02-T01` · **Contrato:** `docs/architecture/agente-automation-fit-v1.md` ·
**Artefato legível por máquina:** `hermes/agents/automation_fit/agente-automation-fit-v1.json`

Quem opera: o Hermes Dev Harness (componente `automation_fit`, papel `automation_scoring`). O que
ele faz: resolve a empresa **que já existe** pelos identificadores fortes, **lê** o estado da onda
W4 (`organizations` + `signals` + `pain_hypotheses`) e **grava a linha do score** `AUTOMATION_FIT`
em `scores`, com fórmula declarada (`automation-fit-v1`), explicação por componente e cobertura. O
que ele **não** faz: criar empresa, escrever qualquer coluna de `organizations` (inclusive
`data_quality_score`), escrever em `signals`/`pain_hypotheses`/`research_runs`, calcular os scores
irmãos (ICP/Buying Signal/Data Quality/Priority), emitir evento de outbox, falar com a rede, chamar
LLM ou escrever em produção.

## 1. Onde o componente roda

O PostgreSQL vive na VPS do ambiente (ADR-0008); o container do Hermes **não** tem socket Docker
nem rota TCP para o banco. Por isso o componente recebe o **prefixo psql** do ambiente e o SQL sai
por `docker exec`:

| Ambiente | Container | Prefixo |
|---|---|---|
| dev | `pg-sales-dev` | `docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence` |
| homolog | `pg-sales-homolog` | idem, trocando o container |

O componente registra no relatório a identidade do alvo **medido** (`current_database()`,
`current_user`) e não afirma nome de ambiente.

## 2. Rodar uma rodada

```bash
# 1) ensaio sem banco (não abre conexão nenhuma)
python3 hermes/agents/automation_fit/automation_fit.py --planejar \
  --fonte hermes/agents/automation_fit/exemplos/perfis-exemplo.jsonl

# 2) rodada no dev (não existe caminho para prod nesta versão)
python3 hermes/agents/automation_fit/automation_fit.py --ambiente dev \
  --fonte /tmp/perfis.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/automation-fit-$(date -u +%Y%m%dT%H%M%SZ).json
```

A fonte é um jsonl com **uma empresa por linha**, só com identificador forte:
`{"organizacao": {"cnpj": "11.222.333/0001-81"}}`. Qualquer outro campo é descartado com motivo
(`CAMPO_NAO_DECLARADO`) — porte, tipos de sinal e impacto **não** se aceitam de fora.

O relatório traz `por_veredito` e, por empresa, `veredito`, `motivos`, `organization_id`, `score_id`,
`score_value`, `cobertura`, `componentes` (presente/valor/peso), `ausentes`, `input_hash`,
`idempotency_key` e `descartados`.

Exit codes: `0` OK · `1` houve `ERRO` em pedido ou falha de porta · `2` uso incorreto ·
`4` recusou o ambiente (prod) · `5` fonte ilegível/inexistente.

## 3. Ler o resultado

| Veredito | Significado | O que fazer |
|---|---|---|
| `CALCULADO` | linha gravada em `scores` (`AUTOMATION_FIT` / `automation-fit-v1`) e chave em `sync_events` | nada — é o caminho normal |
| `JA_CALCULADO` | o **mesmo estado** já tinha sido calculado (`IDEMPOTENCIA_REPLAY`) | nada — retry idempotente |
| `REVISAO_IDENTIDADE` | a identidade casou com duas ou mais empresas | humano decide: `human_approvals` (`AUTOMATION_FIT_IDENTITY_REVIEW`, `PENDING`) |
| `RECUSADA` | pedido inválido, empresa inexistente ou `SEM_LASTRO` | corrigir a fonte (`SEM_IDENTIFICADOR_FORTE`, `IDENTIFICADOR_FORTE_INVALIDO`, `ORGANIZACAO_NAO_ENCONTRADA`) ou **enriquecer o estado** (o `SEM_LASTRO` diz que falta evidência, não que a empresa é ruim) |
| `ERRO` | falha de banco/porta ou auditoria não registrada | ler `motivos`; nada foi escrito fora de `agent_runs` |

`JA_CALCULADO` **não** é falha: é a garantia de que retry não duplica. O que cria linha nova é
**estado novo** (sinal novo, hipótese nova, porte/unidades medidos diferentes) — é assim que o
histórico do score é construído.

## 4. Conferir a linha gravada

```sql
SELECT organization_id, score_type, score_version, score_value,
       explanation->>'cobertura' AS cobertura,
       explanation->'componentes' AS componentes,
       inputs->>'input_hash'     AS input_hash,
       calculated_at
FROM sales_intelligence.scores
WHERE score_type = 'AUTOMATION_FIT'
ORDER BY organization_id, calculated_at DESC;
```

Leitura rápida: `cobertura` baixa (< 0,60) significa score apoiado em pouco lastro — o número existe
e é honesto, mas quem prioriza (W5-E05) deve saber disso. `componentes` mostra **por que** o número
saiu; `ausentes` diz o que não foi medido (não é "zero").

## 5. Desfazer uma rodada

```bash
# dry-run: mostra o que seria apagado (nada é apagado)
python3 hermes/agents/automation_fit/automation_fit.py --desfazer <correlation_id>
# aplica: apaga as linhas de scores da rodada + os sync_events delas (e registra ROLLBACK)
python3 hermes/agents/automation_fit/automation_fit.py --desfazer <correlation_id> --confirmo
```

O desfazer **não** restaura valor anterior (o score é linha nova) e **não** toca `organizations`,
`signals`, `pain_hypotheses`, `agent_runs` nem `human_approvals`. Corrigir a fórmula não é desfazer:
é **versão nova** (`automation-fit-v2`) — a v1 continua no histórico e continua executável.

## 6. Verificação (passo 0 e aceite)

```bash
# passo 0: suíte offline + autoteste por mutação (sem banco, sem rede)
python3 scripts/agentes/verificar_agente_automation_fit.py --autoteste

# aceite E2E com PostgreSQL descartável (RODA NA VPS; o container do Hermes não tem docker)
bash scripts/agentes/teste_automation_fit_aceite.sh
bash scripts/agentes/teste_automation_fit_aceite.sh --prova-de-dente
```

O aceite sobe o container próprio (`pg-automation-fit-acc`), aplica a migration 0001 num schema
limpo e **aborta** se o container já existir (não mexe no que não é dele). No fim, remove container
e diretório de trabalho. `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev` nunca são tocados.

## 7. Limites declarados

- A **fórmula V1** (pesos, faixas, limiar de cobertura) é proposta do worker: o baseline define o
  que o score mede, não como. Homologação é do Anderson — até lá, o número é reprodutível e
  auditável, **não** validado comercialmente.
- `valid_until` fica `NULL` (lacuna declarada do baseline).
- Nada de LLM/modelo na v1; sem promocinação a produção (ADR-005) e sem evento de outbox (o evento
  de score no contrato é `PRIORITY_SCORE_CHANGED`, que nasce depois do Tiering).
