# Agente Pain Hypothesis v1 — contrato da hipotese de dor com lastro (TRE-W4-E04-T01)

**Card:** `TRE-W4-E04-T01` (W4 · E04 · P1) · **Onda:** W4 — Hermes Sales Intelligence
**Versão do agente:** `pain_hypothesis/1.0.0` · **Papel:** `pain_hypothesis` (Hermes Sales AI, doc 02 §2)
**Artefato legível por máquina:** `hermes/agents/pain_hypothesis/agente-pain-hypothesis-v1.json`
**Implementação:** `hermes/agents/pain_hypothesis/pain_hypothesis.py` · **Suíte:** `scripts/agentes/verificar_agente_pain_hypothesis.py`
**Aceite E2E:** `scripts/agentes/teste_pain_hypothesis_aceite.sh` · **Runbook:** `docs/runbooks/agente-pain-hypothesis.md`

## 1. Por que este agente existe

Depois de *Descoberto* (Scout, `TRE-W4-E01-T01`), *Pesquisado* (Research, `TRE-W4-E02-T01`) e
*Sinalizado* (Signal Detector, `TRE-W4-E03-T01`), o funil do doc 03 §2 e a lista do doc 07 §7 pedem a
**hipótese de dor**. A tabela `pain_hypotheses` está no Data Contract V1.0 desde o W0 (doc 04 §7) e
**nenhum produtor a escrevia** — o Signal Detector declara, na sua própria §12, "sem hipótese de dor
e sem contato: `pain_hypotheses` e `contacts` são W4-E04/E05". Sem hipótese, o gate da onda
("empresa → research/signals/hypothesis/contacts", doc 07 §7) e o E2E #001 do doc 08 §3 (passo 9,
"cria pain hypothesis") não fecham.

Este agente é esse produtor — e só isso: resolve a empresa **que já existe** pelos identificadores
fortes do contrato, **confere que o lastro declarado existe de verdade e é da mesma empresa** e grava
a hipótese **marcada como inferência** em `pain_hypotheses`. Nada mais: sem score de impacto, sem
validação de status, sem contato, sem evento de outbox, sem tocar em `organizations`, `signals` ou
`research_runs`.

## 2. Entrada — a hipótese

Arquivo JSONL, uma hipótese por linha (`hermes/agents/pain_hypothesis/exemplos/hipoteses-exemplo.jsonl`):

```json
{"organizacao":{"cnpj":"11.222.333/0001-81"},
 "dor":"A conciliacao de recebiveis e manual em quatro sistemas e trava o fechamento mensal",
 "categoria":"FINANCEIRO",
 "evidencias":[{"tipo":"SINAL","id":"9f6b3d1e-6f4a-4b2c-8f1d-2c3a4b5d6e7f"},
               {"tipo":"PESQUISA","id":"eeeeeeee-0000-4000-8000-000000000001"}],
 "resumo_da_evidencia":"Vaga exige conciliacao manual entre quatro sistemas",
 "confianca":0.7,
 "research_run_id":"eeeeeeee-0000-4000-8000-000000000001"}
```

- **Identidade (`organizacao`):** um ou mais identificadores **fortes** do Data Contract V1.0
  (`cnpj → domain → linkedin_url`). O agente **não cria** empresa: identidade que não casa é
  `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`).
- **Dor (`dor`):** o `pain_statement` — **obrigatório**, porque a coluna é `NOT NULL` no DDL.
  Ausente é `RECUSADA` (`SEM_DOR_DECLARADA`); acima de 4.000 caracteres é `RECUSADA`
  (`DOR_ACIMA_DO_LIMITE`). Diferente do título do sinal (coluna nullable, onde o longo era
  **descartado** e o fato seguia), aqui o enunciado **é** a hipótese: truncar seria inventar outra
  frase.
- **Categoria (`categoria`):** opcional, do vocabulário da baseline (doc 01 §5, "Hipóteses de dor
  prioritárias"): `FINANCEIRO`, `COMERCIAL`, `ATENDIMENTO`, `OPERACOES`, `DOCUMENTOS`. Ausente fica
  `NULL` (não se inventa categoria); fora da lista é `RECUSADA`
  (`CATEGORIA_DE_DOR_DESCONHECIDA`). **Este vocabulário não é do Data Contract V1.0** — o contrato
  fecha apenas `pain_hypotheses.status` (doc 12 §6) —, então ele mora no contrato **deste** agente,
  versionado com ele e coberto pela suíte.
- **Lastro (`evidencias`):** **pelo menos uma** evidência `{tipo, id}` com `tipo` do vocabulário
  fechado (`SINAL` → `signals.id`; `PESQUISA` → `research_runs.id`). Cada evidência tem de
  **existir no banco** e ser **da mesma empresa resolvida** (ver §3).
- **Resumo (`resumo_da_evidencia`):** opcional (vira `evidence_summary`); acima de 4.000 caracteres é
  **descartado com motivo** — a hipótese segue, porque o resumo é acessório ao lastro.
- **Confiança (`confianca`):** opcional, 0–1 (o `NUMERIC(5,4)` do DDL, arredondado a 4 casas).
  Ausente fica `NULL`; fora da faixa é descartada com motivo.
- **`research_run_id`:** vínculo de **origem** da hipótese. **Aqui há FK de verdade** no DDL
  (`pain_hypotheses.research_run_id → research_runs(id)`) — ao contrário de
  `signals.research_run_id`, que é vínculo lógico sem FK (está em `logical_links_without_fk` do
  contrato). Por isso o agente **confere a existência antes de escrever**: inexistente
  (`RESEARCH_RUN_NAO_ENCONTRADO`) ou de outra empresa (`RESEARCH_RUN_DE_OUTRA_ORGANIZACAO`) é
  **descartado com motivo** e a hipótese segue sem o vínculo — escrever um id inexistente derrubaria
  o `INSERT` inteiro (é o mesmo desenho de "vínculo quebrado não perde o fato", agora com o DDL
  cobrando).
- **Campos derivados — proibidos na entrada:** `status` (o status é **sempre** o inicial do
  contrato: a transição é ato humano, doc 12 §6), `id` (UUID v4 gerado no produtor), `validated_at`,
  `business_impact_score` e `estimated_impact_description` (impacto de negócio não tem fórmula
  homologada no baseline). Quem declarar um deles tem o campo **descartado** com motivo
  `DERIVADO_NAO_ACEITO` (ou `CAMPO_NAO_DECLARADO`, se nem declarado for).

## 3. A regra que decide o que é escrito

| Situação | Veredito | Escrita |
|---|---|---|
| Forte válido casando com **uma** organização, dor presente e **≥1 evidência com lastro** | `REGISTRADA` | `pain_hypotheses` + `sync_events` + `agent_runs` |
| Mesma hipótese reapresentada (chave já existente) | `JA_REGISTRADA` | só `agent_runs` |
| Fortes válidos casando com **duas ou mais** organizações distintas | `REVISAO_IDENTIDADE` | `human_approvals` (`PENDING`) + `agent_runs` |
| **Nenhuma** evidência declarada, ou nenhuma que exista/seja da empresa | `RECUSADA` (`SEM_EVIDENCIA_DECLARADA` / `SEM_EVIDENCIA_VALIDA`) | só `agent_runs` |
| **Nenhum** identificador forte declarado | `RECUSADA` (`SEM_IDENTIFICADOR_FORTE`) | só `agent_runs` |
| Declarou forte e **nenhum é válido** (ex.: CNPJ com DV errado) | `RECUSADA` (`IDENTIFICADOR_FORTE_INVALIDO`) | só `agent_runs` |
| Forte válido que **não casa** com nenhuma organização | `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`) | só `agent_runs` |
| Sem dor, dor acima do limite, categoria fora do vocabulário, evidência ilegível/sem tipo/tipo fora/sem id/id fora do formato | `RECUSADA` com motivo | só `agent_runs` |
| Falha de banco/porta ou auditoria não registrada | `ERRO` | só `agent_runs` (`status = FAILED`) |

Regras que sustentam a tabela:

- **Hipótese sem lastro não é gravada.** Inferência sem evidência é opinião. O agente lê `signals` e
  `research_runs` pelos ids declarados e só considera lastro o que **existe** e **pertence à empresa
  resolvida**: evidência de outra empresa é o caso que mais engana (o id existe, a leitura passaria) e
  é exatamente o que o dono no `organization_id` barra — `EVIDENCIA_DE_OUTRA_ORGANIZACAO`. Evidência
  inexistente é `EVIDENCIA_NAO_ENCONTRADA`. Se **nenhuma** sobrar, o veredito é `RECUSADA`
  (`SEM_EVIDENCIA_VALIDA`).
- **A inferência é marcada como inferência** (compliance, doc 12 §8): `evidence.inferencia = true` e
  `evidence.marcada_como_inferencia = true` viajam na própria linha, junto do `input_hash` e do
  `correlation_id`.
- **A evidência é conservada:** cada evidência que sobreviveu entra em `evidence.evidencias` com o
  **fato de origem** — para `SINAL`, `signal_type`, `signal_category` e `event_date`; para `PESQUISA`,
  `research_type` e `status`. A primeira declarada que sobreviveu vira `evidence.evidencia_primaria`.
- **Nenhum impacto é calculado e nenhuma validação é feita aqui.** `business_impact_score`,
  `estimated_impact_description` e `validated_at` existem no DDL e **não** são escritos: a W5 cobre
  ICP/Automation Fit/Buying Signal/Data Quality/Priority e **nenhuma** delas é impacto da dor, e a
  validação da hipótese é ato humano (doc 12 §6). A guarda **recusa** quem tentar escrever qualquer
  uma delas no `INSERT`.
- **Nada é mesclado em silêncio:** todo descarte sai com campo, motivo e valor original.
- **A ambiguidade é reportada, nunca resolvida por heurística** (`dedup.rule`): dois casamentos
  distintos param na fila humana (`human_approvals` `PENDING`, `PAIN_IDENTITY_REVIEW`).
- **A hipótese é aditiva:** nenhuma coluna de `organizations`, `signals` ou `research_runs` é
  escrita — o agente **lê** essas três (identidade, lastro e dono) e escreve só nas quatro tabelas
  declaradas.

## 4. Idempotência — retry não cria duplicata (doc 06 §7)

A chave é a **entrada declarada e normalizada**, não a rodada nem o que sobrou dos descartes:
`pain:org:<uuid>:<input_hash>`, gravada em `sync_events.idempotency_key` (**UNIQUE** no contrato).
`input_hash` = `sha256` do JSON canônico (identidades normalizadas + dor + categoria + evidências
declaradas + resumo + confiança + vínculo declarado), então a **mesma** hipótese reapresentada produz
o mesmo hash — e uma hipótese diferente (outra dor, outra categoria, outra evidência, outra
confiança) produz outro.

Cada hipótese é ingerida por **uma transação com dois comandos de escrita**:

```sql
BEGIN;
WITH claim AS (INSERT INTO ...sync_events (...) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id)
INSERT INTO ...pain_hypotheses (...) SELECT ... FROM claim ON CONFLICT (id) DO NOTHING RETURNING id;
UPDATE ...sync_events SET status='SUCCESS', response_payload=...
 WHERE idempotency_key = '<chave>' AND EXISTS (SELECT 1 FROM ...pain_hypotheses WHERE id = '<hipotese>')
 RETURNING 'PAIN_REGISTRADA';
COMMIT;
```

- **Replay (a chave já existe):** o `claim` volta vazio, a hipótese **não é inserida** e o fechamento
  fecha **0 linhas** — sem a marca `PAIN_REGISTRADA`. O veredito vira `JA_REGISTRADA` com o motivo
  `IDEMPOTENCIA_REPLAY`.
- **Por que dois comandos** (e não um, com o fechamento dentro da CTE): as CTEs de escrita e a
  instrução principal rodam no **mesmo snapshot**, então a instrução principal **não enxerga** a
  linha que a CTE acabou de inserir — defeito medido e pago no aceite do Scout. Aqui cada passo é um
  comando próprio (snapshot novo) e o fechamento se **ancora na hipótese desta rodada** (não na
  chave), para que o replay não marque sucesso.
- **Cenário de concorrência medido:** a chave já reivindicada com a hipótese **ausente** (retentativa
  depois de uma remoção) não estoura `UNIQUE` nem vira `ERRO`: é replay silencioso.
- A história de **cada hipótese** fica em `agent_runs` (uma linha por hipótese, com o
  `correlation_id` do lote e o `pain_hypothesis_id` no `output`) — auditoria não depende da narrativa
  de quem rodou, e é por ela que o `--desfazer` alcança as hipóteses da rodada.
- UUID v4 é gerado **no produtor** (o agente), antes de qualquer escrita.

## 5. Guardrails

- **Ambiente (ADR-005):** `--ambiente dev|homolog`. `prod` é **recusado** nesta versão (exit 4) —
  promoção é card próprio com aprovação humana registrada; ambiente não declarado ou desconhecido
  também é recusado (fail-closed). O modo `--planejar` não abre conexão nenhuma.
- **Escrita declarada:** `pain_hypotheses` (INSERT; DELETE só no desfazer), `agent_runs` (INSERT),
  `sync_events` (INSERT/UPDATE; DELETE só no desfazer) e `human_approvals` (INSERT). `organizations`,
  `research_runs` e `signals` são **leitura** — escrita nelas é recusa.
- A guarda `validar_sql` roda em **toda** instrução antes de sair e recusa: DDL; tabela não
  declarada; escrita em `organizations`; `INSERT` de hipótese sem `organization_id`/`pain_statement`
  (NOT NULL do DDL); `INSERT` de hipótese com coluna de impacto/validação; `DELETE` fora do desfazer.
  Ela varre o **código SQL**, não o conteúdo dos literais: uma dor com "queda de receita" ou um
  trecho de evidência com "drop table" não derruba hipótese legítima.
- **Uma regra de identidade, uma implementação:** a normalização e o dígito verificador do CNPJ, o
  domínio canônico e o LinkedIn canônico não são reescritos aqui — o agente **importa** o módulo do
  produtor (`hermes/agents/scout/scout.py`) por caminho, inclusive o escape de literal. A suíte
  reprova se aparecer uma segunda implementação.
- **Natureza da ação (doc 02 §4):** criar hipótese é **L0** — não exige aprovação humana. Ações
  **L1** (primeiro e-mail, LinkedIn, WhatsApp, proposta) não são executadas por este agente: sem
  SMTP, sem IMAP, sem cliente HTTP no código (a suíte reprova se aparecerem).
- **JEV (doc 07 §7, W4):** "antes de chamadas de LLM em tarefas elegíveis, o Hermes consulta o JEV".
  `chamar_llm()` exige recibo com `decision_id`, `lane` do vocabulário e `outcome = PASS` do roteador
  (`hermes/jev/routing/router.py`); sem recibo, ou com `ESCALATE`/`BLOCK`/lane inválida, **levanta** e
  nada é escrito (fail-closed). Na v1 o caminho de LLM não é exercido: o registro é determinístico.

## 6. Operação

O banco vive na VPS do ambiente (**ADR-0008**): o SQL sai por `docker exec … psql`. O agente recebe
o **prefixo psql** do ambiente e registra, no relatório, a identidade do alvo medido
(`current_database()`/`current_user`) — sem afirmar nome de ambiente.

```bash
# planejar (sem banco)
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --planejar --fonte hipoteses.jsonl

# registrar no dev
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --ambiente dev --fonte hipoteses.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/pain-rodada.json

# desfazer uma rodada (dry-run por padrão)
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --desfazer <correlation_id>
python3 hermes/agents/pain_hypothesis/pain_hypothesis.py --desfazer <correlation_id> --confirmo
```

## 7. ACCEPTANCE (critérios de aceitação)

- **A1** modo `--planejar` roda sem abrir conexão de banco: 0 escrita, exit 0.
- **A2** hipótese legítima grava **uma** linha em `pain_hypotheses` com `organization_id` da empresa
  casada, `pain_statement` do enunciado, `pain_category` do vocabulário (ou `NULL`), `status =
  HYPOTHESIS`, `evidence` com `inferencia`/`marcada_como_inferencia`/`evidencias`/`evidencia_primaria`/
  `input_hash`, `confidence` (NULL quando ausente) e `id` UUID v4.
- **A3** o registro **não cria** organização e não escreve nenhuma coluna de `organizations`,
  `signals` ou `research_runs`.
- **A4** organização inexistente → `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`), nada em
  `pain_hypotheses`.
- **A5** identidade casando com duas ou mais organizações distintas → `REVISAO_IDENTIDADE` com
  `human_approvals` `PENDING` (`PAIN_IDENTITY_REVIEW`); se a escrita da fila humana falhar, o veredito
  é `ERRO` (fail-closed).
- **A6** hipótese inválida (sem dor, dor acima do limite, categoria fora do vocabulário, sem
  evidência declarada, evidência ilegível/sem tipo/tipo fora/sem id/id fora do formato, sem
  identificador forte, forte declarado e inválido) é `RECUSADA` com motivo; nada escrito em
  `pain_hypotheses`.
- **A7** **hipótese sem lastro não é gravada**: evidência inexistente e evidência de outra empresa são
  `RECUSADA` (`SEM_EVIDENCIA_VALIDA`) e descarte com motivo, respectivamente; com pelo menos uma
  evidência válida a hipótese é gravada e o lastro fica amarrado à origem real.
- **A8** reprocessar a **mesma** hipótese cria 0 linhas novas (contagem antes/depois) e o veredito é
  `JA_REGISTRADA` (`IDEMPOTENCIA_REPLAY`); a chave reivindicada com a hipótese ausente não vira erro.
- **A9** **nenhum impacto e nenhuma validação**: `business_impact_score`,
  `estimated_impact_description` e `validated_at` ficam `NULL` e a guarda recusa quem tentar;
  `status` declarado pela fonte não é escrito.
- **A10** auditoria: uma linha de `agent_runs` por hipótese (`agent_name=pain_hypothesis`,
  `agent_role=pain_hypothesis`, `correlation_id` do lote, `output` com veredito e
  `pain_hypothesis_id`); se a linha de auditoria não for escrita, a execução é `ERRO`.
- **A11** `--ambiente prod` é recusado (exit 4) e nada é escrito; ambiente não declarado idem.
- **A12** nenhuma escrita fora das tabelas declaradas; nenhum `DELETE` fora do `--desfazer --confirmo`;
  nenhuma chamada de LLM sem recibo válido do JEV; nenhum acesso a Odoo/Titan/n8n; nenhum cliente HTTP
  no código.
- **A13** `--desfazer <correlation_id>` é dry-run por padrão; com `--confirmo` **remove as hipóteses da
  rodada** e os `sync_events` delas e registra um `sync_events` de `ROLLBACK`, sem tocar
  `organizations`, `research_runs`, `signals`, `agent_runs` nem `human_approvals`.
- **A14** artefatos versionados e cobertos por `scripts/verificar_estrutura.sh`; a suíte passa com
  autoteste por mutação.

## 8. TEST

- **Offline (contrato e regra):**
  `python3 scripts/agentes/verificar_agente_pain_hypothesis.py --autoteste` — **85 itens**: contrato do
  agente espelhado no código, vocabulário de categorias contra o doc 01 §5, `status` escrito contra o
  vocabulário do Data Contract e contra o DEFAULT do DDL, colunas do `INSERT` de `pain_hypotheses`
  conferidas contra o DDL congelado (inclusive as duas `NOT NULL`), colunas de impacto/validação fora
  da escrita, identidade forte importada do produtor (sem segunda cópia), validação/descarte da
  hipótese (dor, categoria, evidências, resumo, confiança, vínculo, campos derivados, campo não
  declarado), decisão de veredito pura (inclusive `SEM_EVIDENCIA_VALIDA`), hash de idempotência
  estável e sensível ao conteúdo, guarda de escrita (DDL, tabela fora da lista, `organizations`,
  coluna de impacto, hipótese sem coluna `NOT NULL`, `DELETE` fora do desfazer, prosa com "drop" não é
  DDL), gate do JEV fail-closed, ausência de rede/LLM, a guarda da **própria prova** (item esperado
  inexistente ou mutação sem item declarado **reprova**) e o **fluxo completo** numa **porta de
  roteiro** (registrar, replay idempotente, revisão, recusa sem lastro, recusa por identidade,
  evidência de outra empresa, evidência inexistente, vínculo quebrado, auditoria que falha, porta
  indisponível, dry-run falha, desfazer dry-run/confirmado). O autoteste muta **cópia** do arquivo sob
  teste (`--codigo`; o canônico, por padrão) e exige que o item correspondente **reprove** — hoje
  27/27.
- **E2E (banco real, descartável):** `bash scripts/agentes/teste_pain_hypothesis_aceite.sh` na VPS, em
  container PostgreSQL descartável (`pg-pain-acc`) com a migration 0001, **3 organizações
  pré-existentes** (`updated_at` fixo em 2000-01-01, de propósito, para acusar qualquer toque em coluna
  de empresa), **3 `research_runs`** e **4 `signals`** semeados por SQL — o lastro real das hipóteses.
  Os 14 casos da fonte cobrem: hipótese registrada com lastro misto (SINAL + PESQUISA), casamento por
  CNPJ com pontuação e por LinkedIn "sujo", os descartes com motivo (evidência de outra empresa,
  evidência inexistente, `research_run` inexistente, confiança fora da faixa, derivado, campo não
  declarado, resumo acima do limite), a recusa por falta de lastro, o replay (mesma linha no lote e
  rodada 2 inteira) e a retentativa com a hipótese ausente. O `--prova-de-dente` muta a cópia do agente
  e exige, para **cada** mutação, que o aceite reprove **o item esperado** (não apenas "falhou"), com
  baseline verde antes e depois — hoje 5/5 (idempotência, fechamento sem âncora na hipótese, lastro
  não conferido, lastro de outra empresa aceito, status chumbado como validado).
- **Evidência no `docs/operations/registro-de-execucoes.md`** com comando e saída reais.

## 9. ROLLBACK

- **Código:** a branch do card é **aditiva** (arquivos novos + bloco novo no gate de estrutura,
  CHANGELOG e registro). Reverter a branch devolve o estado anterior; nenhum comportamento existente
  muda.
- **Dados (dev/homolog):** `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo` aplica)
  **apaga as hipóteses da rodada** e os `sync_events` delas e grava um `sync_events` de `ROLLBACK`. Não
  há valor anterior a restaurar — a hipótese é uma **linha nova** (aditivo), e a prova de que a linha
  pertence à rodada é o `sync_events` da chave, que é como o `DELETE` chega na hipótese certa.
  `organizations`, `research_runs`, `signals`, `agent_runs` (auditoria) e `human_approvals` (fila
  humana) não são tocados.
- **Limite declarado:** o desfazer alcança o que a rodada **criou e registrou**. Hipótese removida por
  fora (não pelo agente) deixa o `sync_event` órfão visível, e o que a rodada **não** criou (revisão,
  recusa) não tem nada a desfazer.
- **Schema:** **nenhuma migration nova** — rollback não exige restore de banco.

## 10. RISK

- **R1 (alto) — score inventado por conta própria.** `business_impact_score` e
  `estimated_impact_description` existem no DDL e **nenhuma** fórmula de impacto está homologada no
  baseline (a W5 cobre outros cinco scores). Preencher esses campos "porque a coluna existe" daria ao
  produto um número sem fórmula. Mitigado **por desenho**: colunas na lista proibida do contrato **e**
  recusa na guarda de escrita — medido por mutação na suíte e pelo dente do aceite.
- **R2 (alto) — hipótese sem lastro (opinião virando dado).** Uma inferência que não se ancora em fato
  observado contamina ICP, Priority e a próxima ação. Mitigado por desenho: evidência obrigatória,
  conferida no banco e **da mesma empresa**; sem lastro o veredito é `RECUSADA`
  (`SEM_EVIDENCIA_VALIDA`).
- **R3 (médio) — hipótese presa à empresa errada.** Identidade casando com duas ou mais organizações
  não é resolvida por heurística: vai para a fila humana (`dedup.rule` do contrato). E o lastro de
  outra empresa é recusado mesmo quando o id existe.
- **R4 (médio) — hipótese duplicada em retry.** A idempotência vive no **SQL**
  (`sync_events.idempotency_key` UNIQUE + fechamento ancorado na hipótese da rodada), não na prosa;
  replay não insere e não marca sucesso.
- **R5 (médio) — inferência passando por fato.** Compliance exige "inferência marcada como
  inferência": `evidence.inferencia` e a regra viajam na linha, e o `status` fica no inicial
  (`HYPOTHESIS`) até um humano validar — o agente **não** escreve `VALIDATED`.
- **R6 (médio) — evidência perdida.** Cada evidência que sustentou a hipótese fica em
  `evidence.evidencias` com o fato de origem (tipo/categoria/data do sinal, tipo/estado da pesquisa) e
  todo descarte fica registrado com campo, motivo e valor.
- **R7 (baixo) — duas hipóteses distintas da mesma empresa.** São legítimas e geram duas linhas (o
  baseline não define janela nem limiar de quantidade); o que a idempotência barra é a **mesma**
  hipótese reapresentada.
- **R8 (baixo) — custo/volume.** Sem LLM e sem rede na v1: custo zero por hipótese; a rodada é
  limitada ao que a fonte entrega (sem paginação/crawler).

## 11. Decisões de implementação

- **D1** base da branch no head aprovado do card pai mais novo (`feature/TRE-W4-E03-T01` @ `c52167b`,
  que já contém o Research `82ff096`).
- **D2** o agente **não cria** organização e **não escreve** nenhuma coluna de `organizations`,
  `signals` ou `research_runs`: a hipótese é aditiva.
- **D3** nenhum impacto e nenhuma validação são calculados aqui: as três colunas ficam fora da escrita
  e a guarda recusa quem tentar.
- **D4** o `status` é **sempre** o inicial do contrato (`HYPOTHESIS`), lido do vocabulário do Data
  Contract; status vindo da fonte é descartado.
- **D5** a categoria da dor é o vocabulário da baseline (doc 01 §5), declarado no contrato **deste**
  agente — o Data Contract V1.0 fecha apenas `pain_hypotheses.status`, e não se inventa vocabulário
  dentro de um contrato congelado.
- **D6** o lastro tem de existir **e** ser da mesma empresa; evidência de outra empresa é recusada
  mesmo existindo (o dono é lido junto, na mesma consulta).
- **D7** o vínculo `research_run_id`, que aqui é **FK de verdade**, é conferido por existência e dono
  antes de ser escrito; vínculo quebrado é descartado com motivo e a hipótese segue.
- **D8** uma linha de `agent_runs` por hipótese, com o `correlation_id` do lote e o
  `pain_hypothesis_id` no `output` (é o vínculo que o `--desfazer` usa).
- **D9** v1 não escreve em produção: `prod` recusado por desenho, promoção é card próprio.
- **D10** a identidade forte vem do contrato a cada execução; a suíte reprova se a lista do agente
  divergir de `dedup.strong`.
- **D11** a regra de identidade e o escape de literal são **importados** do produtor (Scout) — uma
  implementação só.
- **D12** o `input_hash` é calculado sobre a **entrada declarada** (normalizada), não sobre o que
  sobrou dos descartes: a mesma hipótese reapresentada continua sendo o mesmo pedido.

## 12. Lacunas declaradas

1. **Sem LLM e sem crawler:** a fonte entrega a hipótese e o lastro; o agente **confere** o lastro, não
   o produz. A inferência assistida por modelo não está implementada (o gate do JEV existe e é
   fail-closed).
2. **Sem score de impacto:** `business_impact_score` e `estimated_impact_description` não são escritos
   — não há fórmula homologada no baseline. Nenhum limiar de confiança ou de quantidade de evidências
   é aplicado (uma evidência já sustenta; confiança ausente fica `NULL`); julgar relevância é W5.
3. **Sem validação:** `status` fica no inicial (`HYPOTHESIS`) e `validated_at` nunca é escrito.
   `VALIDATED`/`PARTIALLY_VALIDATED`/`REJECTED`/`STALE` são transição humana (doc 12 §6) e **não têm
   fluxo implementado** nesta versão.
4. **Sem contato:** `contacts` é W4-E05.
5. **Sem outbox:** nenhum evento PG→Odoo nasce do registro (o contrato não lista evento de hipótese); a
   passagem de estado no funil é do Odoo, dono do estágio.
6. **Sem janela de validade:** o baseline não define expiração e a coluna não existe no DDL (o `STALE`
   do vocabulário não tem gatilho implementado).
7. **`retention_until`/`collected_at` não existem** no contrato V1 (lacuna já declarada no `compliance`
   do contrato; não inventamos coluna).

## 13. Referências

- Baseline V1.1.0: docs 01 §5, 02 §2/§4, 04 §7, 06 §2/§7, 07 §7, 08 §3, 11 §2, 12 §6/§8.
- `docs/data/DATA_CONTRACT_V1.md` e `docs/data/data_contract_v1.json`
  (`vocabularies.pain_hypotheses.status`, `source_of_truth.hipoteses_ia = PostgreSQL`, `dedup`,
  `logical_links_without_fk`, `governance`).
- `docs/architecture/agente-signal-v1.md` (o produtor do fato datado e o desenho da idempotência) ·
  `docs/architecture/agente-research-v1.md` · `docs/architecture/agente-scout-v1.md`
  (o produtor da empresa e a regra de identidade importada).
- `ADR-0005` (nada nasce em produção), `ADR-0007` (ambientes), `ADR-0008` (acesso ao PostgreSQL).
- `docs/runbooks/agente-pain-hypothesis.md` · `docs/runbooks/agente-signal.md`.
