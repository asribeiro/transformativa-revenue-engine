# Runbook — Agente Signal Detector v1 (`signal/1.0.0`)

**Card:** `TRE-W4-E03-T01` · **Contrato:** `docs/architecture/agente-signal-v1.md` ·
**Artefato legível por máquina:** `hermes/agents/signal/agente-signal-v1.json`

Quem opera: o Hermes Dev Harness (agente `signal`, papel `signal_detection`). O que ele faz: resolve
a empresa **que já existe** pelos identificadores fortes, valida o tipo do sinal contra o vocabulário
fechado e **grava o sinal datado com evidência** em `signals` (com a categoria derivada do tipo e o
vínculo lógico com a pesquisa que o motivou). O que ele **não** faz: criar empresa, escrever
qualquer coluna de `organizations`, calcular score (pontos/relevância/decaimento são
`TRE-W5-E03-T01`), criar hipótese de dor ou contato, emitir evento de outbox, falar com a rede ou
escrever em produção.

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
python3 hermes/agents/signal/signal.py --planejar --fonte /tmp/observacoes.jsonl

# 2) rodada no dev (não existe caminho para prod nesta versão)
python3 hermes/agents/signal/signal.py --ambiente dev \
  --fonte /tmp/observacoes.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/signal-$(date -u +%Y%m%dT%H%M%SZ).json
```

O relatório traz `por_veredito` (contagem por veredito) e, por observação, `veredito`, `motivos`,
`organization_id`, `signal_id`, `idempotency_key`, `tipo`, `categoria`, `fontes`, `confianca`,
`data_do_evento`, `research_run_id` e `descartados`.

Exit codes: `0` OK · `1` houve `ERRO` em observação ou falha de porta · `2` uso incorreto ·
`4` recusou o ambiente (prod) · `5` fonte ilegível/inexistente.

## 3. Ler o resultado

| Veredito | Significado | O que fazer |
|---|---|---|
| `DETECTADO` | sinal gravado em `signals` (com categoria derivada e evidência) e chave registrada em `sync_events` | nada — é o caminho normal |
| `JA_DETECTADO` | a mesma observação já tinha sido detectada (`IDEMPOTENCIA_REPLAY`) | nada — retry idempotente |
| `REVISAO_IDENTIDADE` | a identidade casou com **duas ou mais** empresas | humano decide: `human_approvals` (`SIGNAL_IDENTITY_REVIEW`, `PENDING`) |
| `RECUSADA` | observação inválida ou empresa inexistente | corrigir a fonte (`SEM_TIPO_DE_SINAL`, `TIPO_DE_SINAL_DESCONHECIDO`, `SEM_FONTE`, `FONTE_DESCONHECIDA`, `SEM_IDENTIFICADOR_FORTE`, `IDENTIFICADOR_FORTE_INVALIDO`, `ORGANIZACAO_NAO_ENCONTRADA`) |
| `ERRO` | falha de banco/porta ou auditoria não registrada | ler `motivos`; nada foi escrito fora de `agent_runs` |

O que a detecção **descartou** (título/descrição acima do limite, data inválida, confiança fora de
0–1, vínculo com `research_run` inexistente, categoria declarada pela fonte, campo não declarado)
está em `signals.evidence.descartados`, em `sync_events.request_payload` e no `output` do
`agent_runs` correspondente — com campo, motivo e valor, nunca em silêncio.

## 4. Conferir a rodada por SQL

```sql
-- sinais gravados na rodada (o correlation_id vem do relatório)
SELECT s.signal_type, s.signal_category, s.title, s.event_date, s.confidence,
       s.source_type, s.research_run_id
FROM sales_intelligence.signals s
JOIN sales_intelligence.sync_events e ON e.entity_id = s.id AND e.operation = 'SIGNAL'
WHERE e.request_payload ->> 'correlation_id' = '<correlation_id>'
ORDER BY s.detected_at;

-- nenhum score foi escrito (as quatro colunas ficam vazias / no default do contrato)
SELECT count(*) FROM sales_intelligence.signals
WHERE relevance_score IS NOT NULL OR buying_signal_points IS NOT NULL
   OR expires_at IS NOT NULL;

-- a empresa nao foi tocada pela deteccao
SELECT max(updated_at) FROM sales_intelligence.organizations;   -- nao muda por causa de sinal
```

## 5. Desfazer uma rodada

```bash
# dry-run: diz o que apagaria, e nao apaga
python3 hermes/agents/signal/signal.py --desfazer <correlation_id> --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"

# aplica: apaga os signals da rodada + os sync_events deles e grava um sync_events de ROLLBACK
python3 hermes/agents/signal/signal.py --desfazer <correlation_id> --ambiente dev --confirmo \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

O **rollback** não restaura valor nenhum em `organizations` (o agente não escreve lá) e **não toca**
`agent_runs` (auditoria), `human_approvals` (fila humana) nem `research_runs`. O que ele apaga é
exatamente o que a rodada criou, e o vínculo que prova a posse da linha pela rodada é o
`sync_events` da chave (`entity_id = signals.id`) — a trilha fica visível para quem investigar
depois.

## 6. Guardrails que o operador precisa conhecer

- `--ambiente prod` é **recusado** (exit 4): promoção é card próprio com aprovação humana registrada
  (ADR-005). Ambiente não declarado também é recusado.
- O agente **recusa** qualquer SQL de DDL, qualquer escrita fora de `signals`, `agent_runs`,
  `sync_events` e `human_approvals`, qualquer `INSERT` de sinal sem `organization_id`/`signal_type`
  e qualquer `INSERT` de sinal com coluna de score (`buying_signal_points`, `relevance_score`,
  `decay_factor`, `expires_at`). `DELETE` só no `--desfazer --confirmo`.
- O agente **não** reimplementa a regra de identidade: ele importa o módulo do Scout. Divergência ali
  é defeito, não configuração.
- O gate do JEV é fail-closed: sem recibo válido (`outcome = PASS`) não há chamada de LLM. Na v1 não
  há caminho de LLM.

## 7. Aceite

```bash
# na VPS (o container do Hermes nao tem daemon Docker); container descartavel pg-signal-acc
bash scripts/agentes/teste_signal_aceite.sh
bash scripts/agentes/teste_signal_aceite.sh --prova-de-dente
```

Veredito: `ACEITE_SIGNAL_001_OK` / `ACEITE_SIGNAL_001_FALHOU`. O aceite **nunca** toca
`pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` ou `proxy-dev`: se o container `pg-signal-acc` já existir,
ele **aborta** em vez de mexer no que não é dele.
