# Runbook — Buying Signal Score v1 (TRE-W5-E03-T01)

Como rodar, medir e desfazer o cálculo do score `BUYING_SIGNAL` de uma empresa.
Contrato do componente: `docs/architecture/buying-signal-score-v1.md`.

## 0. Pré-requisitos

- A empresa **existe** em `sales_intelligence.organizations` (o score não cria empresa).
- Os sinais dela estão em `sales_intelligence.signals` (produzidos pelo Signal Detector,
  `TRE-W4-E03-T01`). Sem sinal, o score sai `0,00` com motivo `SEM_SINAIS` — isso é resultado, não erro.
- O banco vive na VPS (ADR-0008): quem fala com ele é a **porta psql** informada em `--prefixo`.
  No container do Hermes não há daemon Docker — as medições de aceite rodam **na VPS**.

## 1. Sem tocar no banco (planejamento)

```bash
python3 hermes/agents/buying_signal/buying_signal_score.py --planejar \
  --organizacao <uuid-da-empresa> --raiz <raiz-do-repo>
```

`--planejar` calcula com os sinais que conseguir ler (nenhum, no modo planejar) e **não abre
conexão**. Serve para conferir contrato/fórmula antes de escrever.

## 2. Calcular e gravar em dev

```bash
python3 hermes/agents/buying_signal/buying_signal_score.py \
  --ambiente dev --organizacao <uuid-da-empresa> \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/bss-rodada.json --raiz <raiz-do-repo>
```

Saída (uma linha JSON + uma linha por resultado):

```text
{"agente": "buying_signal", "ambiente": "dev", "gravados": 1, "por_veredito": {"CALCULADO": 1}, ...}
  CALCULADO            score=91.82
```

- `CACULADO` (`gravados: 1`): linha nova em `scores`.
- `JA_CALCULADO` (`gravados: 0`): mesma entrada já estava calculada — **nada foi duplicado**.
- `RECUSADA` (motivo `ORGANIZACAO_NAO_ENCONTRADA`): empresa não existe; nada foi escrito.
- `prod` é recusado com **exit 4** e nada é escrito (ADR-005).

## 3. Ler o resultado

```sql
SELECT calculated_at, score_value, score_version, valid_until,
       jsonb_array_length(inputs->'sinais_utilizados') AS sinais_usados,
       inputs->>'entrada_hash' AS entrada_hash,
       explanation->>'resumo' AS resumo
FROM sales_intelligence.scores
WHERE organization_id = '<uuid>' AND score_type = 'BUYING_SIGNAL'
ORDER BY calculated_at DESC;
```

`inputs` reconstrói o número: pontos, confiança, decaimento e idade sinal a sinal, mais os
descartes com motivo. `explanation.motivo = 'SEM_SINAIS'` quando nada entrou.

## 4. Recalcular (histórico)

Rodar de novo com os **mesmos sinais** não grava nada (`JA_CALCULADO`). Quando entra sinal novo (ou
muda a confiança/data de um sinal), o `entrada_hash` muda e uma **linha nova** é inserida — o valor
anterior permanece. O score é histórico: **não há UPDATE**.

## 5. Desfazer uma rodada

```bash
# dry-run (padrão): mostra o que seria apagado e NÃO apaga
python3 hermes/agents/buying_signal/buying_signal_score.py --desfazer <correlation_id> \
  --ambiente dev --prefixo "<prefixo psql>" --raiz <raiz-do-repo>

# aplica: apaga SOMENTE os scores que aquela rodada criou
python3 hermes/agents/buying_signal/buying_signal_score.py --desfazer <correlation_id> --confirmo \
  --ambiente dev --prefixo "<prefixo psql>" --raiz <raiz-do-repo>
```

Preserva `signals`, `organizations`, `agent_runs` (a auditoria da rodada continua no banco) e a fila
humana. Rodada inexistente não apaga nada.

## 6. Medir (teste e aceite)

```bash
# offline: fórmula, vocabulário, descartes, limite, guarda, DDL, idempotência, CLI/ambiente
python3 scripts/agentes/verificar_buying_signal_score.py [--raiz <raiz>]
python3 scripts/agentes/verificar_buying_signal_score.py --prova-de-dente   # 12 mutações

# E2E: PostgreSQL DESCARTAVEL na VPS (exige daemon Docker). NUNCA toca pg-sales-dev.
bash scripts/agentes/teste_buying_signal_aceite.sh --raiz <raiz>
bash scripts/agentes/teste_buying_signal_aceite.sh --raiz <raiz> --prova-de-dente
```

O aceite sobe `pg-buying-acc` (container **próprio**), aplica a migration 0001, mede e **remove** o
container. Se o container já existir, o aceite **aborta** em vez de mexer no que não é dele.

## 7. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `RECUSADO_AMBIENTE ambiente 'prod'` (exit 4) | ambiente de produção | use `--ambiente dev` ou `--ambiente homolog`; produção exige aprovação humana registrada |
| `JA_CALCULADO` quando se esperava valor novo | mesmos sinais (mesmo `entrada_hash`) | confira `signals` da empresa; sinal novo é que muda o score |
| `score_value = 0.00` com `SEM_SINAIS` | empresa sem sinal utilizável | veja `inputs.descartados` (motivos `DATA_AUSENTE`, `CONFIANCA_FORA_DA_FAIXA`) |
| `FALHOU porta psql falhou` | prefixo errado/permissão | confira o `--prefixo` e o acesso à VPS (ADR-0008) |
| `GUARDA_VIOLADA escrita em tabela não declarada` | SQL tentando escrever fora de `scores`/`agent_runs`/`sync_events` | comportamento esperado da guarda: o score é derivado |
