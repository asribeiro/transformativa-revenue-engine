# Runbook — Agente Pain Hypothesis v1 (`pain_hypothesis/1.0.0`)

**Card:** `TRE-W4-E04-T01` · **Contrato:** `docs/architecture/agente-pain-hypothesis-v1.md` ·
**Artefato legível por máquina:** `hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json`

Quem opera: o Hermes Dev Harness (agente `pain_hypothesis`, papel `pain_hypothesis`). O que ele faz:
resolve a empresa **que já existe** pelos identificadores fortes, confere que as **evidências**
declaradas existem de verdade **e são da mesma empresa**, e **grava a hipótese de dor marcada como
inferência** em `pain_hypotheses`, com o lastro conservado. O que ele **não** faz: criar empresa,
escrever qualquer coluna de `organizations`, `signals` ou `research_runs`, calcular impacto/score
(`business_impact_score`, `estimated_impact_description` — não há fórmula homologada), validar a
hipótese (`status` fica em `HYPOTHESIS`; a transição é ato humano), criar contato, emitir evento de
outbox, falar com a rede ou escrever em produção.

## 1. Onde o agente roda

O PostgreSQL vive na VPS do ambiente (ADR-0008); o container do Hermes **não** tem socket Docker nem
rota para o banco. Por isso o agente recebe o **prefixo psql** do ambiente e o SQL sai por
`docker exec`:

| Ambiente | Container | Prefixo |
|---|---|---|
| dev | `pg-sales-dev` | `docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence` |
| homolog | `pg-sales-homolog` | idem, trocando o container |

O agente registra no relatório a identidade do alvo **medido** (`current_database()`,
`current_user`) e não afirma nome de ambiente — a convenção de nome divergiu no dev e alinhar isso é
decisão do dono.

## 2. Rodar uma rodada

```bash
# 1) ensaio sem banco (não abre conexão nenhuma)
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --planejar --fonte /tmp/hipoteses.jsonl

# 2) rodada no dev (não existe caminho para prod nesta versão)
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --ambiente dev \
  --fonte /tmp/hipoteses.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/pain-$(date -u +%Y%m%dT%H%M%SZ).json
```

O relatório traz `por_veredito` (contagem por veredito) e, por hipótese, `veredito`, `motivos`,
`organization_id`, `pain_hypothesis_id`, `idempotency_key`, `categoria`, `evidencias`,
`research_run_id`, `confianca` e `descartados`.

Exit codes: `0` OK · `1` houve `ERRO` em hipótese ou falha de porta · `2` uso incorreto ·
`4` recusou o ambiente (prod) · `5` fonte ilegível/inexistente.

## 3. Montar a fonte (o que vai em cada linha)

```json
{"organizacao":{"cnpj":"11.222.333/0001-81"},
 "dor":"<enunciado da dor, OBRIGATORIO>",
 "categoria":"OPERACOES",
 "evidencias":[{"tipo":"SINAL","id":"<uuid do signal>"},{"tipo":"PESQUISA","id":"<uuid do research_run>"}],
 "resumo_da_evidencia":"...", "confianca":0.7, "research_run_id":"<uuid>"}
```

- a **dor** é obrigatória (`pain_statement` é `NOT NULL`); `categoria` tem de ser uma das cinco do
  doc 01 §5 (`FINANCEIRO`, `COMERCIAL`, `ATENDIMENTO`, `OPERACOES`, `DOCUMENTOS`);
- cada **evidência** tem de apontar para um `signals.id` (`tipo: SINAL`) ou um `research_runs.id`
  (`tipo: PESQUISA`) que **exista** e seja **da mesma empresa**: pegue os ids da rodada de sinal
  (`docker exec … psql -c "select id, signal_type from sales_intelligence.signals …"`);
- `status`, `validated_at`, `business_impact_score` e `estimated_impact_description` **não** vão na
  fonte: são derivados/ato humano e o agente os descarta.

## 4. Ler o resultado

| Veredito | Significado | O que fazer |
|---|---|---|
| `REGISTRADA` | hipótese gravada em `pain_hypotheses` (com lastro, inferência marcada e status inicial) e chave registrada em `sync_events` | nada — é o caminho normal |
| `JA_REGISTRADA` | a mesma hipótese já tinha sido registrada (`IDEMPOTENCIA_REPLAY`) | nada — retry idempotente |
| `REVISAO_IDENTIDADE` | a identidade casou com **duas ou mais** empresas | humano decide: `human_approvals` (`PAIN_IDENTITY_REVIEW`, `PENDING`) |
| `RECUSADA` | hipótese inválida, empresa inexistente **ou sem lastro que exista** | corrigir a fonte (`SEM_DOR_DECLARADA`, `DOR_ACIMA_DO_LIMITE`, `CATEGORIA_DE_DOR_DESCONHECIDA`, `SEM_EVIDENCIA_DECLARADA`, `SEM_EVIDENCIA_VALIDA`, `TIPO_DE_EVIDENCIA_DESCONHECIDO`, `SEM_IDENTIFICADOR_FORTE`, `IDENTIFICADOR_FORTE_INVALIDO`, `ORGANIZACAO_NAO_ENCONTRADA`) |
| `ERRO` | falha de banco/porta ou auditoria não registrada | ler `motivos`; nada foi escrito fora de `agent_runs` |

O que o registro **descartou** (evidência inexistente, evidência **de outra empresa**, evidência
repetida, `research_run` inexistente ou de outra empresa, confiança fora de 0–1, resumo acima do
limite, campo derivado declarado, campo não declarado) está em `pain_hypotheses.evidence.descartados`,
em `sync_events.request_payload` e no `output` do `agent_runs` correspondente — com campo, motivo e
valor, nunca em silêncio.

## 5. Conferir a rodada por SQL

```sql
-- hipoteses gravadas na rodada (o correlation_id vem do relatorio)
SELECT h.pain_category, h.status, h.confidence, h.research_run_id,
       h.evidence -> 'evidencias' AS lastro, left(h.pain_statement, 60) AS dor
FROM sales_intelligence.pain_hypotheses h
JOIN sales_intelligence.sync_events e ON e.entity_id = h.id AND e.operation = 'PAIN_HYPOTHESIS'
WHERE e.request_payload ->> 'correlation_id' = '<correlation_id>'
ORDER BY h.created_at;

-- nenhum impacto/validacao foi escrito (as tres colunas ficam NULL)
SELECT count(*) FROM sales_intelligence.pain_hypotheses
WHERE business_impact_score IS NOT NULL OR estimated_impact_description IS NOT NULL
   OR validated_at IS NOT NULL;

-- toda hipotese nasce no status inicial (validacao e ato humano)
SELECT count(*) FROM sales_intelligence.pain_hypotheses WHERE status <> 'HYPOTHESIS';

-- o lastro aponta para o que existe de verdade, na mesma empresa
SELECT count(*) FROM sales_intelligence.pain_hypotheses h
JOIN sales_intelligence.signals s
  ON s.id = (h.evidence -> 'evidencia_primaria' ->> 'id')::uuid
WHERE h.evidence -> 'evidencia_primaria' ->> 'tipo' = 'SINAL'
  AND s.organization_id <> h.organization_id;   -- tem de dar 0

-- a empresa nao foi tocada pelo registro
SELECT max(updated_at) FROM sales_intelligence.organizations;   -- nao muda por causa de hipotese
```

## 6. Desfazer uma rodada

```bash
# dry-run: diz o que apagaria, e nao apaga
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --desfazer <correlation_id> --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"

# aplica: apaga as hipoteses da rodada + os sync_events delas e grava um sync_events de ROLLBACK
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --desfazer <correlation_id> --ambiente dev --confirmo \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

O **rollback** não restaura valor nenhum em `organizations` (o agente não escreve lá) e **não toca**
`agent_runs` (auditoria), `human_approvals` (fila humana), `research_runs` nem `signals`. O que ele
apaga é exatamente o que a rodada criou, e o vínculo que prova a posse da linha pela rodada é o
`sync_events` da chave (`entity_id = pain_hypotheses.id`) — a trilha fica visível para quem
investigar depois.

## 7. Guardrails que o operador precisa conhecer

- `--ambiente prod` é **recusado** (exit 4): promoção é card próprio com aprovação humana registrada
  (ADR-005). Ambiente não declarado também é recusado.
- O agente **recusa** qualquer SQL de DDL, qualquer escrita fora de `pain_hypotheses`, `agent_runs`,
  `sync_events` e `human_approvals`, qualquer `INSERT` de hipótese sem `organization_id`/
  `pain_statement` e qualquer `INSERT` com coluna de impacto/validação (`business_impact_score`,
  `estimated_impact_description`, `validated_at`). `DELETE` só no `--desfazer --confirmo`.
- **Hipótese sem lastro não é gravada**: se todas as evidências declaradas forem inexistentes ou de
  outra empresa, o veredito é `RECUSADA` (`SEM_EVIDENCIA_VALIDA`) e nada é escrito.
- O agente **não** reimplementa a regra de identidade: ele importa o módulo do Scout. Divergência ali
  é defeito, não configuração.
- O gate do JEV é fail-closed: sem recibo válido (`outcome = PASS`) não há chamada de LLM. Na v1 não
  há caminho de LLM.

## 8. Aceite

```bash
# na VPS (o container do Hermes nao tem daemon Docker); container descartavel pg-pain-acc
bash scripts/agentes/teste_pain_hypothesis_aceite.sh
bash scripts/agentes/teste_pain_hypothesis_aceite.sh --prova-de-dente
```

Veredito: `ACEITE_PAIN_001_OK` / `ACEITE_PAIN_001_FALHOU`. O aceite **nunca** toca `pg-sales-dev`,
`pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: se o container `pg-pain-acc` já existir, ele **aborta** em
vez de mexer no que não é dele.
