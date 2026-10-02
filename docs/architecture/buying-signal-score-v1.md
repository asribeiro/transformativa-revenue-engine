# Buying Signal Score v1 — contrato do score de força dos sinais (TRE-W5-E03-T01)

**Card:** `TRE-W5-E03-T01` (W5 · E03 · P1) · **Onda:** W5 — Scoring + Next Best Action
**Versão do componente:** `buying_signal/1.0.0` · **Score:** `BUYING_SIGNAL` · **Versão do score:** `buying-signal-v1`
**Artefato legível por máquina:** `hermes/agents/buying_signal/agente-buying-signal-v1.json`
**Implementação:** `hermes/agents/buying_signal/buying_signal_score.py`
**Suíte offline:** `scripts/agentes/verificar_buying_signal_score.py` (91 itens + 12 dentes)
**Aceite E2E:** `scripts/agentes/teste_buying_signal_aceite.sh` (36 itens) · **Runbook:** `docs/runbooks/buying-signal-score.md`

## 1. Por que este componente existe

O Data Contract V1.0 (§8) lista **cinco scores** — `ICP`, `AUTOMATION_FIT`, `BUYING_SIGNAL`,
`DATA_QUALITY`, `PRIORITY` — e fecha a fórmula do `PRIORITY` (0,35·ICP + 0,30·AUTOMATION_FIT +
0,25·BUYING_SIGNAL + 0,10·DATA_QUALITY). O peso de 25% do `BUYING_SIGNAL` existe no contrato desde
o W0, mas **nenhum componente calculava o score**: o Signal Detector (`TRE-W4-E03-T01`) produz os
fatos (`signals`) e recusa, por desenho, escrever qualquer coluna de score (guarda de escrita
declarada na §6 do doc dele).

Este é o componente que transforma **sinais datados em número**, com versão, explicação e
histórico. Sem ele, o Priority Score (W5-E05) e o tiering (W5-E06) não têm de onde partir.

## 2. Entrada e saída

- **Entrada:** os `signals` de **uma** empresa (`organizations.id`) já existentes em
  `sales_intelligence.signals` — nada é recebido por arquivo, a matéria-prima é o banco.
  Colunas lidas: `id`, `signal_type`, `signal_category`, `confidence`, `event_date`, `detected_at`.
- **Saída:** **uma linha** em `sales_intelligence.scores` com:
  - `score_type = 'BUYING_SIGNAL'` e `score_version = 'buying-signal-v1'` (score sem versão não é
    reprodutível e é recusado pelo contrato §8);
  - `score_value` em 0,00–100,00;
  - `inputs` (JSONB): os sinais que entraram (id, tipo, categoria, pontos, confiança,
    decaimento, idade), os que ficaram fora do limite, os descartados com motivo, o
    `entrada_hash` e o `confianca_padrao_usada`;
  - `explanation` (JSONB): resumo em texto, motivo (`SEM_SINAIS` quando não há sinal utilizável) e
    os parâmetros declarados da fórmula;
  - `valid_until = calculated_at + 30 dias`.
- **Auditoria:** uma linha em `agent_runs` por rodada (`agent_name='buying_signal'`,
  `correlation_id` da rodada, `output.score_id`) e a trava em `sync_events`
  (`operation='SCORE'`, `idempotency_key`).

## 3. Fórmula V1 (`buying-signal-v1`, congelada)

```text
pontos   = peso_do_tipo(tipo) * confianca_efetiva * decaimento(idade)
decaimento = 0.5 ** (idade_dias / meia_vida_dias(categoria))
forca    = 1 - PROD(1 - pontos)        # saturação: muitos sinais fracos não inventam 100
score    = 100 * forca                 # arredondado a 2 decimais, teto 100
```

- **peso por tipo** — tabela fechada sobre o vocabulário `signal_type` do contrato (18 valores,
  cada um exatamente uma vez). Tipo fora da lista é **RECUSA** (`ContratoDivergente`), não aviso.
  Pesos: `ERP_CHANGE` 0,95 · `AI_INITIATIVE` 0,90 · `DIGITAL_TRANSFORMATION` 0,85 · `M_AND_A`
  0,85 · `CRM_CHANGE` 0,80 · `FUNDING` 0,80 · `EFFICIENCY_PROGRAM` 0,75 · `NEW_EXECUTIVE` 0,70 ·
  `COST_REDUCTION` 0,70 · `GROWTH` 0,65 · `TECH_ADOPTION` 0,60 · `HIRING` 0,60 ·
  `REGULATORY_CHANGE` 0,55 · `NEW_PRODUCT` 0,55 · `NEW_LOCATION` 0,50 · `SERVICE_VOLUME` 0,50 ·
  `PROCESS_COMPLEXITY` 0,45 · `CUSTOMER_COMPLAINT` 0,40.
- **meia-vida por categoria** (a categoria é derivada do tipo pelo detector): `PRESSAO_OPERACIONAL`
  90 d · `TECNOLOGIA` 120 d · `EXPANSAO` 150 d · `CORPORATIVO` 180 d · `EFICIENCIA` 240 d ·
  `REGULATORIO` 300 d. Sinal de tecnologia esfria mais rápido que mudança regulatória.
- **confiança ausente** não vira fato: usa a confiança padrão **0,5** e o fato é registrado
  (`inputs.confianca_padrao_usada`). O JSONB do banco devolve `''` para coluna `NULL` — vazio é
  **ausente**, não zero (defeito medido no aceite, corrigido e coberto por item + dente).
- **data futura** não decai (idade 0) e é registrada (`data_futura`). **Sem `event_date` e sem
  `detected_at`** o sinal é **descartado** com motivo `DATA_AUSENTE`: idade não se inventa.
- **limite de 10 sinais** (os mais fortes por pontos): o excedente vai para
  `inputs.sinais_ignorados`. O score não é contador de volume.
- **sem sinal algum** o score é **0,00** com `explanation.motivo = 'SEM_SINAIS'`: ausência de sinal
  é informação (o Priority Score precisa de número), não erro.

## 4. Garantias (o que o componente NÃO faz)

1. **Não escreve em `signals` nem em `organizations`.** A guarda de escrita recusa, com a mesma
   força com que o detector recusa escrever score. Escreve apenas em `scores`, `agent_runs` e
   `sync_events`.
2. **Não faz UPDATE em `scores`**: o score é **histórico, não mutável** (contrato §8). Recalcular
   **insere** linha nova; o valor antigo permanece no banco.
3. **Idempotência pela ENTRADA, não pela rodada**: `score:BUYING_SIGNAL:<org>:<entrada_hash>` em
   `sync_events.idempotency_key` (UNIQUE). Mesma entrada (mesmos sinais, mesma fórmula) ⇒ veredito
   `JA_CALCULADO` e nenhuma linha nova. O hash usa a **identidade** dos sinais e os parâmetros da
   fórmula — nunca os pontos derivados (que carregam a idade e mudam a cada segundo; defeito medido
   no aceite, corrigido e coberto por item).
4. **Sem rede e sem LLM**: nenhuma requisição e nenhuma chamada de modelo — a fórmula é
   determinística e o resultado é reprodutível a partir do `inputs`.
5. **Nada nasce em produção** (ADR-005): `--ambiente dev|homolog`; `prod` é recusado com exit 4
   **sem escrever**. `--planejar` calcula e **não abre conexão**.
6. **Desfazer é cirúrgico**: apaga **só** os scores que a rodada criou (âncora em
   `agent_runs.output.score_id`) e **só** com `--confirmo`; dry-run é o padrão. A auditoria da
   rodada é preservada.

## 5. Campos exigidos pelo doc 11 §2

| Campo | Definição deste card |
|---|---|
| **ACCEPTANCE** | A1 score gravado em `scores` com `score_type`/`score_version`/`inputs`/`explanation`/`valid_until`; A2 `signals` e `organizations` intactos (nenhuma coluna de score do sinal preenchida); A3 `prod` recusado (exit 4) sem escrita e `--planejar` sem conexão; A4 replay da mesma entrada não duplica e sinal novo gera linha nova; A5 empresa sem sinal ⇒ 0,00 com motivo `SEM_SINAIS`; A6 empresa inexistente ⇒ `RECUSADA` sem escrita; A7 desfazer dry-run não apaga e `--confirmo` apaga só a rodada; A8 `agent_runs` e `sync_events` registrados; A9 rodada inexistente não apaga nada; A10 suíte offline (91 itens) e dente 12/12 verdes. |
| **TEST** | `scripts/agentes/verificar_buying_signal_score.py` (suíte offline: fórmula, vocabulário, descartes, limite, guarda de escrita, paridade com o DDL, idempotência, ambiente/CLI — 91 itens) + `--prova-de-dente` (12 mutações, cada uma reprovando o **item esperado**, com controle negativo de mutação inerte); `scripts/agentes/teste_buying_signal_aceite.sh` (E2E em PostgreSQL descartável na VPS, 36 itens, com `--prova-de-dente` próprio). |
| **ROLLBACK** | `--desfazer <correlation_id>` (dry-run por padrão) e `--desfazer <correlation_id> --confirmo` apagam **só** os scores da rodada, deixando `signals`, `organizations`, `agent_runs` e a fila humana intactos. Nenhuma DDL é aplicada por este card: não há migration a reverter. |
| **RISK** | **Médio** — não toca dado de negócio alheio (só insere score) e não altera esquema; o risco real é **número errado** virar priorização errada. Mitigação: fórmula declarada e versionada, pesos e meia-vidas no contrato do componente (divergência reprova a suíte), `inputs`/`explanation` persistidos para reconstruir qualquer score, `score_version` obrigatória e plano B explícito (`--desfazer` da rodada). Peso/preço de negócio: mudar peso ⇒ **nova `score_version`** (`buying-signal-v2`), nunca edição silenciosa. |

## 6. Limites declarados deste card

- **A fórmula do `BUYING_SIGNAL` ainda não está escrita no Data Contract V1.0** (§8 fecha apenas a
  fórmula do `PRIORITY`). Este card congela a `buying-signal-v1` e **registra a fórmula para o
  contrato**: a §8 do contrato precisa recebê-la na homologação. É mudança **documental** (nenhuma
  tabela, coluna, vocabulário ou peso do `PRIORITY` muda), mas é mudança de contrato e por isso
  fica declarada aqui em vez de silenciosa.
- A v1 **não usa** `signals.relevance_score` nem `signals.decay_factor` (colunas do DDL que outro
  componente pode preencher): o score é derivado **só** de tipo, confiança e data.
- Os pesos por tipo são **decisão de negócio do baseline**, não medida empírica. Se o uso real
  mostrar peso errado, a correção é uma versão nova do score — o histórico fica comparável.
- Medido em `dev`, num banco **descartável**; **nada** foi escrito em `pg-sales-dev`, homologação ou
  produção. Homologação é do Anderson (estágio 7); este card entrega o estágio 6.

## 7. Evidências da rodada

- Suíte offline: `91 itens / 0 falhas`; dente `12/12 OK` (inclui controle negativo).
- Aceite E2E: `36 OK / 0 FALHOU` → `ACEITE_BSS_001_OK`, em container descartável `pg-buying-acc`
  na VPS do TRE, com a migration 0001 aplicada e a massa de 3 sinais; containers do TRE intactos.
- Ambiente: `pg-buying-acc` **removido** ao fim do aceite; `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev`
  e `proxy-dev` (e o container de outro card, `pg-icp-acc`) intocados; nada em produção.
