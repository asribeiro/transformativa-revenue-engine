# Automation Fit Score v1 — contrato, fórmula e critérios de aceitação (TRE-W5-E02-T01)

WAVE: W5 · EPIC: E02 · PRIORITY: P1 · DEPENDS ON: W4-E06-T01
CARD: `TRE-W5-E02-T01` · componente: `hermes/agents/automation_fit/automation_fit.py`
CONTRATO LEGÍVEL POR MÁQUINA: `hermes/agents/automation_fit/agente-automation-fit-v1.json`

Este documento é o **contrato do card**: o que o Automation Fit Score v1 mede, com que pesos, o que
ele recusa fazer, e — exigência da seção 2 do doc 11 — **ACCEPTANCE, TEST, ROLLBACK e RISK**. O
baseline (doc 03 §3) define *o que* o score mede ("probabilidade de existir oportunidade relevante
de automação/IA") e **não** define a fórmula: pesos, faixas e limiar ficam declarados aqui, como
proposta V1 do worker. A validação comercial da fórmula é decisão do Anderson (estágio 7) — o que
esta entrega prova é reprodutibilidade, idempotência, escopo de escrita e poder de discriminação.

---

## 1. O que o componente mede

`AUTOMATION_FIT` = probabilidade de existir **oportunidade relevante de automação/IA** na empresa,
calculada a partir do estado que a onda W4 já produziu no PostgreSQL:

| Fonte | O que entra | Quem produz |
|---|---|---|
| `organizations` | `employee_band` / `employee_count`, `unit_count` | Scout (TRE-W4-E01-T01) / Research |
| `signals` | `signal_type` dos sinais **da empresa** | Signal Detector (TRE-W4-E03-T01) |
| `pain_hypotheses` | `business_impact_score` **da empresa** | Pain Hypothesis (TRE-W4-E04-T01) |
| `scores` | **escrita** da linha do score (nada lido) | este componente |

A entrada do componente (jsonl) traz **só a identidade forte** da empresa (`cnpj` / `domain` /
`linkedin_url`). Porte, tipos de sinal e impacto **não vêm da fonte**: são lidos do banco. Campo
extra no pedido é descartado com motivo `CAMPO_NAO_DECLARADO` — inclusive `employee_band`, para que
ninguém "melhore" o score por fora.

## 2. Fórmula V1 (`automation-fit-v1`)

```
valor = 100 * SUM( peso_i * componente_i ) / SUM( peso_i )     para i em componentes PRESENTES
```

| Componente | Peso | De onde sai | Como pontua |
|---|---|---|---|
| `porte` | 0,25 | `employee_band` (ou derivada de `employee_count`) | LT_70 0,25 · 70_149 0,60 · 150_299/300_499/500_699 1,00 · 700_1000 0,80 · GT_1000 0,50 · `UNKNOWN` sem medição = **ausente** |
| `pressao_operacional` | 0,30 | sinais de pressão/eficiência | PROCESS_COMPLEXITY 0,40 · EFFICIENCY_PROGRAM 0,35 · SERVICE_VOLUME 0,30 · COST_REDUCTION 0,30 · CUSTOMER_COMPLAINT 0,20 · REGULATORY_CHANGE 0,15 (teto 1,00) |
| `prontidao_tecnologica` | 0,20 | sinais de tecnologia/IA | DIGITAL_TRANSFORMATION 0,40 · AI_INITIATIVE 0,40 · ERP_CHANGE 0,30 · TECH_ADOPTION 0,25 · CRM_CHANGE 0,25 (teto 1,00) |
| `dispersao_de_processos` | 0,10 | `unit_count` | 1 → 0,30 · 2–3 → 0,60 · 4–10 → 0,85 · >10 → 1,00 · NULL/0 = **ausente** |
| `dor_quantificada` | 0,15 | `pain_hypotheses.business_impact_score` | `max(impacto)/100`, capado em [0, 100]; sem impacto medido = **ausente** |

Pesos somam **exatamente 1,00** (`conferir_pesos()`, chamada no `__init__` e coberta pela suíte).
Aritmética em `Decimal` com `ROUND_HALF_UP` em 2 casas (o DDL pede `NUMERIC(5,2)`): mesma entrada ⇒
mesmo valor, **bit a bit**. Determinístico por desenho: **sem LLM, sem rede**.

### 2.1 Componente ausente não é voto — e é isso que separa este score do Data Quality

Componente sem evidência sai do **numerador e do denominador**; a `cobertura` (soma dos pesos
presentes) vai para `explanation.cobertura` e `inputs.cobertura`. Motivo declarado: um score que
trata ausência como zero vira **um segundo Data Quality Score** (W5-E04) e passa a medir completude
de dado, não oportunidade. Quem quiser punir ausência usa o `DATA_QUALITY` e o `PRIORITY` (W5-E05),
que misturam os dois de propósito.

### 2.2 Sem lastro não há score (fail-closed)

`cobertura < 0,40` ⇒ **RECUSADA** com motivo `SEM_LASTRO`; **nada é escrito** em `scores`, e o
`agent_runs` registra `REJECTED`. Um score calculado majoritariamente sobre ausência seria ruído
com cara de número.

### 2.3 Poder de discriminação (não é opcional)

A lição que custou uma camada aposentada no roteador JEV vale aqui: **estimador que empata com a
constante não é fonte de decisão, é ruído calibrado.** Por isso o aceite mede, no corpus declarado
de dez perfis: ≥ 3 faixas de 20 pontos distintas, margem ≥ 30 pontos entre mínimo e máximo, desvio
médio em relação à constante 50 ≥ 10 e desvio padrão ≥ 8. Medido: **4 faixas, margem 51,7,
desvio 18,1, desvio padrão 17,5**.

## 3. Histórico, idempotência e desfazer

- **Score é histórico** (doc 12 §8): `UPDATE` em `scores` é recusa dura na guarda de escrita. Estado
  **novo** ⇒ **linha nova**; replay do **mesmo** estado ⇒ `JA_CALCULADO`, zero duplicata.
- A chave é a **entrada**, não a rodada: `score:AUTOMATION_FIT:org:<uuid>:<input_hash>`, gravada em
  `sync_events.idempotency_key` (UNIQUE). O `input_hash` é o SHA-256 do **snapshot canônico** que
  entra na fórmula (porte medido + `unit_count` + `{id, tipo}` dos sinais válidos + `{id, impacto}`
  das hipóteses medidas). Sinal novo ⇒ hash novo ⇒ linha nova; ordem do banco não muda o hash.
- Ingestão = **uma transação com dois comandos**: `claim` da chave + `INSERT` do score ancorado no
  claim; depois o fechamento do `sync_event` **ancorado na linha do score desta rodada**
  (`EXISTS (SELECT 1 FROM scores WHERE id = <id desta rodada>)`). Sem a âncora, um replay marcaria
  sucesso por uma linha que não é dele.
- **Desfazer** `--desfazer <correlation_id> [--confirmo]` (dry-run por padrão): apaga as linhas de
  `scores` da rodada + os `sync_events` delas e registra um `sync_events` de `ROLLBACK`. Auditoria
  preservada. Não há valor anterior a restaurar (o score é linha nova).

## 4. Escrita permitida (e o que é recusa dura)

Permitido: `scores` (INSERT; DELETE só no `--desfazer --confirmo`), `agent_runs` (INSERT),
`sync_events` (INSERT/UPDATE/DELETE só no desfazer), `human_approvals` (INSERT).

Recusa: DDL; `UPDATE` em `scores`; **qualquer** escrita em `organizations` (inclusive
`data_quality_score`, que é o card W5-E04), `signals`, `pain_hypotheses`, `research_runs`, `contacts`,
`interactions`, `recommendations`, `outbox_events`; INSERT de score sem
`id`/`organization_id`/`score_type`/`score_value`/`score_version`; INSERT com coluna não declarada;
`DELETE` fora do desfazer e fora de `scores`/`sync_events`; escrita em Odoo/Titan/n8n/host.

Ambiente: `dev` e `homolog`; **`prod` recusado** (exit 4, sem escrever) — promoção exige card
próprio com aprovação humana registrada (ADR-005). `--planejar` não abre conexão.

---

## 5. ACCEPTANCE (AC1..AC10)

- **AC1** — `AUTOMATION_FIT` calculado do estado real do banco por fórmula versionada
  (`automation-fit-v1`) e gravado em `scores` com `score_type`, `score_version`, `inputs`,
  `explanation`.
- **AC2** — Determinístico e reprodutível: mesma entrada ⇒ mesmo valor, bit a bit; sem LLM e sem rede.
- **AC3** — Explicação auditável: `explanation.componentes` com valor/peso/presença/detalhe; `inputs`
  com o snapshot medido + `input_hash` + cobertura.
- **AC4** — Histórico imutável: estado novo ⇒ linha nova; replay ⇒ `JA_CALCULADO` (zero duplicata);
  `UPDATE` em `scores` recusado.
- **AC5** — Fail-closed sem lastro: cobertura < 0,40 ⇒ `RECUSADA/SEM_LASTRO`, nada escrito.
- **AC6** — Guarda de escrita conforme a seção 4, medida por item e por mutação.
- **AC7** — Ambiente: `dev`/`homolog`; `prod` recusado (exit 4); `--planejar` sem conexão.
- **AC8** — Poder de discriminação medido no corpus (seção 2.3) atendido.
- **AC9** — Desfazer: dry-run não apaga; `--confirmo` apaga só o que a rodada criou, preserva
  `organizations`/`signals`/`pain_hypotheses`/`agent_runs` e registra `ROLLBACK`.
- **AC10** — Ambiente de prova: container PostgreSQL descartável próprio na VPS; containers
  persistentes intactos; nada em produção; sha256 do código sob teste fixado na evidência.

## 6. TEST

1. **Passo 0 — suíte offline** (sem banco, sem rede):
   `python3 scripts/agentes/verificar_agente_automation_fit.py --autoteste`
   → `AUTOMATION_FIT_SUITE_OK (N itens, 0 falhas)` + autoteste com todas as mutações detectadas.
2. **Aceite E2E** em container descartável na VPS:
   `bash scripts/agentes/teste_automation_fit_aceite.sh` → `ACEITE_AUTOMATION_FIT_001_OK`.
   Mede por execução real: gravação da linha com a versão certa; determinismo no banco; replay sem
   duplicata; estado novo ⇒ linha nova; `SEM_LASTRO`; organização inexistente e sem identificador
   forte; ambiguidade na fila humana sem escrever score; nenhum score de OUTRA empresa lido; nenhuma
   tabela de negócio tocada; `prod` recusado; `--planejar` sem porta; desfazer dry-run/`--confirmo`;
   discriminação medida nas linhas gravadas.
3. **Prova de dente** (`--prova-de-dente`): cada mutação aplicada em cópia do código tem de reprovar
   **o item esperado**, não apenas "o aceite falhou".

## 7. ROLLBACK

`--desfazer <correlation_id>` (dry-run) e `--desfazer <correlation_id> --confirmo` removem as linhas
de `scores` criadas pela rodada e os `sync_events` delas, registrando `sync_events` de `ROLLBACK`.
Nenhuma tabela de negócio é restaurada porque nenhuma foi escrita; `agent_runs` e `human_approvals`
são preservados. Rollback de fórmula (mudar peso/faixa) **não** é desfazer: é **versão nova**
(`automation-fit-v2`) — a v1 continua legível no histórico.

## 8. RISK

1. **Fórmula V1 é proposta do worker** (o baseline não a define) → pesos declarados e versionados,
   cobertura explícita, discriminação medida. Homologação é do Anderson; nenhum número sai como
   "validado por cliente".
2. **Score virar proxy de qualidade de dado** → normalização pelos componentes presentes + cobertura
   separada + item que mede os dois lados (componente ausente não vota e não é voto neutro).
3. **Divergência com os scores irmãos** (W5-E01/E03/E04) → `score_type` e fórmula saem do Data
   Contract V1; o `E05` consome a **tabela** `scores`, não o módulo.
4. **Inflar o histórico** a cada micro-mudança → `input_hash` sobre o snapshot canônico do que entra
   na fórmula; o que não entra (nome, cidade, tipo inventado) não cria linha.
5. **Escrever na coluna errada** (`organizations.data_quality_score`, colunas de `signals`) → guarda
   por lista de tabelas/colunas + item e mutação dedicados.

## 9. Lacunas declaradas

- O doc 08 §3 cita "Automation Fit 91" no E2E #001: é número **ilustrativo do documento**, não meta
  de calibração cumprida aqui.
- `valid_until` fica **NULL** (o baseline não define janela de validade do `AUTOMATION_FIT`).
- Não lê `research_runs`/`contacts`/`interactions`: completude é o card W5-E04.
- Nada de modelo de propensão na v1: um estimador treinado entra como **versão nova**.
- O score **não decide nada**: priorização é W5-E05, corte é W5-E06, ação é W5-E07.
