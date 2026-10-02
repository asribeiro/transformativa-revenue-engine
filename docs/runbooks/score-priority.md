# Runbook — Priority Score v1 (`priority-v1`)

Card TRE-W5-E05-T01. O componente vive em `hermes/scores/priority/priority_score.py` e o contrato
legível por máquina em `hermes/scores/priority/score-priority-v1.json`.

## 0. Pré-requisitos

- Os quatro componentes já calculados em `sales_intelligence.scores` para a empresa:
  `ICP` (W5-E01), `AUTOMATION_FIT` (W5-E02), `BUYING_SIGNAL` (W5-E03), `DATA_QUALITY` (W5-E04).
  **Sem os quatro, não há PRIORITY** (fail-closed, motivo `SEM_LASTRO_COMPLETO`).
- Nenhum deles com `valid_until` no passado (vencido conta como ausente, motivo `COMPONENTE_VENCIDO`).
- Porta de banco: o SQL roda na VPS (ADR-0008); o Hermes não alcança o PostgreSQL.

## 1. Ver o plano (não toca o banco)

```bash
python3 hermes/scores/priority/priority_score.py --planejar
```

Imprime pesos, cobertura mínima, regra de ausência e a política de validade. Nenhuma conexão é aberta.

## 2. Calcular uma empresa

```bash
python3 hermes/scores/priority/priority_score.py --ambiente dev \
  --organizacao <uuid-da-empresa> \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/priority-rodada.json
```

Vereditos: `CALCULADO` (linha nova), `JA_CALCULADO` (replay do mesmo estado, nada escrito),
`RECUSADA` (empresa inexistente ou lastro incompleto — a recusa **não** escreve score) e `ERRO`.
A linha impressa mostra o motivo (`SEM_LASTRO_COMPLETO`, `COMPONENTE_AUSENTE:<tipo>`,
`COMPONENTE_VENCIDO:<tipo>`) seguido dos motivos nominais.

## 3. Calcular um lote

```bash
python3 hermes/scores/priority/priority_score.py --ambiente dev --fonte organizacoes.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

`organizacoes.jsonl`: uma linha por empresa, `{"organization_id": "<uuid>"}` (comentários com `#` e
linhas vazias são ignoradas; duplicatas são deduplicadas). A fonte escolhe **quem** é medido; o dado
(o score de cada componente) é sempre lido do banco. Um lote tem **um** `correlation_id`.

## 4. Conferir o que foi gravado

```bash
docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence -c \
"SELECT organization_id, score_value, score_version, calculated_at, valid_until,
        inputs->>'cobertura' AS cobertura, inputs->'componentes'->'ICP'->>'score_value' AS icp
   FROM sales_intelligence.scores WHERE score_type='PRIORITY' ORDER BY calculated_at DESC LIMIT 5;"
```

Cada linha é reconstruível: `inputs` traz a identidade dos quatro componentes usados e `explanation`
traz peso, valor e parcela de cada um.

## 5. Desfazer uma rodada

```bash
python3 hermes/scores/priority/priority_score.py --ambiente dev --desfazer <correlation_id> \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"   # dry-run
python3 hermes/scores/priority/priority_score.py --ambiente dev --desfazer <correlation_id> --confirmo
```

O dry-run informa quantos scores seriam apagados e **não apaga**. Com `--confirmo`, apaga só as
linhas da rodada (ancoradas em `agent_runs.output`), preservando os scores dos componentes e a
auditoria.

## 6. Ambientes

`--ambiente dev|homolog`. `prod` é recusado com **exit 4** e nada é escrito (ADR-005: nada nasce em
produção; promoção exige card com aprovação humana registrada).

## 7. Testes

```bash
python3 scripts/scores/verificar_score_priority.py --autoteste   # 35 itens + 12/12 mutacoes
bash scripts/scores/teste_priority_aceite.sh --prova-de-dente    # aceite E2E em container descartavel
```

O aceite roda **na VPS** (onde existe daemon Docker) e cria/remove o container `pg-priority-acc`.

## 8. Leitura do resultado

- `86,45` com os quatro componentes do exemplo = fórmula do contrato (0,35*94 + 0,30*76 + 0,25*83 + 0,10*100).
- `SEM_LASTRO_COMPLETO` = falta componente (ou vencido): **não** é erro do banco — é evidência
  incompleta. Rode o componente que falta e repita.
- `JA_CALCULADO` = o estado não mudou desde a última rodada; é o comportamento esperado do retry.
