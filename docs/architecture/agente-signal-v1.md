# Agente Signal Detector v1 — contrato da deteccao de sinais (TRE-W4-E03-T01)

**Card:** `TRE-W4-E03-T01` (W4 · E03 · P1) · **Onda:** W4 — Hermes Sales Intelligence
**Versão do agente:** `signal/1.0.0` · **Papel:** `signal_detection` (Hermes Sales AI, doc 02 §2)
**Artefato legível por máquina:** `hermes/agents/signal/agente-signal-v1.json`
**Implementação:** `hermes/agents/signal/signal.py` · **Suíte:** `scripts/agentes/verificar_agente_signal.py`
**Aceite E2E:** `scripts/agentes/teste_signal_aceite.sh` · **Runbook:** `docs/runbooks/agente-signal.md`

## 1. Por que este agente existe

Depois de *Descoberto* (Scout, `TRE-W4-E01-T01`) e *Pesquisado* (Research, `TRE-W4-E02-T01`), o
funil do doc 03 §2 e a lista do doc 07 §7 pedem o **Signal Detector**. A tabela `signals` está no
Data Contract V1.0 desde o W0 e **nenhum produtor a escrevia**: o Research declara, na sua própria
§12, "sem sinais, sem hipótese de dor e sem contato: `signals`, `pain_hypotheses` e `contacts` são
W4-E03/E04/E05". Sem sinais, a W5 inteira (Buying Signal Score, `TRE-W5-E03-T01`, e o Priority
Score que o consome) não tem de onde partir.

O Signal Detector é esse produtor — e só isso: resolve a empresa **que já existe** pelos
identificadores fortes do contrato, valida o tipo do sinal contra o vocabulário fechado e grava o
**fato datado com evidência** em `signals`. Nada mais: sem score, sem hipótese de dor, sem contato,
sem evento de outbox, sem tocar em `organizations`.

## 2. Entrada — a observação

Arquivo JSONL, uma observação por linha (`hermes/agents/signal/exemplos/observacoes-exemplo.jsonl`):

```json
{"organizacao":{"cnpj":"11.222.333/0001-81"},"tipo":"HIRING",
 "titulo":"40 vagas de logistica abertas em 30 dias",
 "fontes":[{"tipo":"LINKEDIN","url":"https://www.linkedin.com/company/vale-forte/jobs",
            "trecho":"40 vagas abertas no ultimo mes"}],
 "data_do_evento":"2026-09-28","confianca":0.8}
```

- **Identidade (`organizacao`):** um ou mais identificadores **fortes** do Data Contract V1.0
  (`cnpj → domain → linkedin_url`). O detector **não cria** empresa: identidade que não casa é
  `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`).
- **Tipo (`tipo`):** vocabulário **fechado** de 18 valores — `signals.signal_type` é `NOT NULL` no
  DDL e o vocabulário está em `vocabularies.signal_type` do contrato e no doc 03 §7. Tipo fora da
  lista é **recusa**, não aviso.
- **Fontes (`fontes`):** **pelo menos uma**, com tipo do vocabulário de canais (`LINKEDIN`, `WEB`,
  `GOOGLE`, `META`, `WHATSAPP`, `TITAN`, `EVENTOS`, `DADOS_PUBLICOS` — o mesmo vocabulário do Scout
  e do Research, porque é o mesmo canal do doc 02 §1). É a evidência conservada (compliance:
  "conservar evidência") e a **primeira fonte declarada é a primária**: ela vira
  `signals.source_type` / `signals.source_url`, e todas entram em `signals.evidence.fontes`.
- **Título/descrição/dados:** opcionais. O DDL **não** exige título (é `VARCHAR(500)` nullable) e o
  detector **não inventa** um: o que não veio fica `NULL`; título acima de 500 e descrição acima de
  8.000 são **descartados com motivo**.
- **`data_do_evento`:** ISO-8601 (data ou data-hora). Inválida é descartada com motivo, nunca
  escrita torta.
- **`confianca`:** opcional, 0–1 (o `NUMERIC(5,4)` do DDL, arredondado a 4 casas). Ausente fica
  `NULL` — não se inventa confiança.
- **`research_run_id`:** vínculo **lógico** com a pesquisa que motivou a detecção (o DDL declara
  `signals.research_run_id -> research_runs(id)` **sem** FK, na lista
  `logical_links_without_fk`). Se o `research_run` declarado não existir, o **vínculo** é
  descartado com motivo (`RESEARCH_RUN_NAO_ENCONTRADO`) e o sinal segue — o fato observado não se
  perde por causa de um vínculo quebrado.
- **`categoria`:** **proibida** na entrada. `signal_category` é **derivada** do tipo (ver §3); a
  categoria que a fonte declarar é descartada (`DERIVADO_NAO_ACEITO`).
- **Campo não declarado** (inclusive `buying_signal_points`, `id`, `detected_at`): descartado com
  motivo `CAMPO_NAO_DECLARADO`.

## 3. A regra que decide o que é escrito

| Situação | Veredito | Escrita |
|---|---|---|
| Forte válido, casa com **uma** organização | `DETECTADO` | `signals` + `sync_events` + `agent_runs` |
| Mesma observação reapresentada (chave já existente) | `JA_DETECTADO` | só `agent_runs` |
| Fortes válidos casando com **duas ou mais** organizações distintas | `REVISAO_IDENTIDADE` | `human_approvals` (`PENDING`) + `agent_runs` |
| **Nenhum** identificador forte declarado | `RECUSADA` (`SEM_IDENTIFICADOR_FORTE`) | só `agent_runs` |
| Declarou forte e **nenhum é válido** (ex.: CNPJ com DV errado) | `RECUSADA` (`IDENTIFICADOR_FORTE_INVALIDO`) | só `agent_runs` |
| Forte válido que **não casa** com nenhuma organização | `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`) | só `agent_runs` |
| Sem tipo, tipo fora do vocabulário, sem fonte, fonte/URL/trecho inválidos | `RECUSADA` com motivo | só `agent_runs` |
| Falha de banco/porta ou auditoria não registrada | `ERRO` | só `agent_runs` (`status = FAILED`) |

Regras que sustentam a tabela:

- **`signal_category` é DERIVADA do tipo**, por tabela declarada no contrato
  (`categorias_por_tipo`, coberta uma vez por tipo pela suíte): `EXPANSAO`, `CORPORATIVO`,
  `TECNOLOGIA`, `PRESSAO_OPERACIONAL`, `EFICIENCIA`, `REGULATORIO`. Dado derivado não se aceita da
  fonte — é a mesma regra do `employee_band` no Research.
- **O detector não escreve nenhuma coluna de `organizations`** — nem `updated_at`. O sinal é
  **aditivo**: quem enriquece a empresa é o Research; identidade é do Scout/dedup; estágio é do
  Odoo (doc 12 §1). A guarda de escrita recusa `INSERT`/`UPDATE`/`DELETE` em `organizations` **pelo
  motivo de desenho** (a mensagem nomeia a regra).
- **Nenhum score é calculado aqui.** `buying_signal_points`, `relevance_score`, `decay_factor` e
  `expires_at` existem no DDL e **não** são escritos: força e atualidade do sinal são o **Buying
  Signal Score (W5)** — `TRE-W5-E03-T01` —, com fórmula e janela homologadas próprias. A guarda
  **recusa** quem tentar escrever qualquer uma delas no `INSERT`.
- **A evidência é conservada:** `signals.evidence` guarda as fontes, a fonte primária, o
  `correlation_id`, o `input_hash` e os **descartes com motivo**; `sync_events.request_payload`
  guarda o mesmo em trilha de sincronização.
- **A ambiguidade é reportada, nunca resolvida por heurística** (`dedup.rule`): dois casamentos
  distintos param na fila humana (`human_approvals` `PENDING`, `SIGNAL_IDENTITY_REVIEW`).
- **Nada é mesclado em silêncio**: todo descarte sai com campo, motivo e valor original.

## 4. Idempotência — retry não cria duplicata (doc 06 §7)

A chave é a **entrada**, não a rodada: `signal:org:<uuid>:<tipo>:<input_hash>`, gravada em
`sync_events.idempotency_key` (**UNIQUE** no contrato). `input_hash` = `sha256` do JSON canônico
(identidades normalizadas + tipo + título + descrição + fontes + data do evento + confiança +
vínculo), então a **mesma** observação apresentada de novo produz o mesmo hash — e uma observação
diferente (outro título, outra data, outra fonte) produz outro.

Cada observação é ingerida por **uma transação com dois comandos de escrita**:

```sql
BEGIN;
WITH claim AS (INSERT INTO ...sync_events (...) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id)
INSERT INTO ...signals (...) SELECT ... FROM claim ON CONFLICT (id) DO NOTHING RETURNING id;
UPDATE ...sync_events SET status='SUCCESS', response_payload=...
 WHERE idempotency_key = '<chave>' AND EXISTS (SELECT 1 FROM ...signals WHERE id = '<sinal>')
 RETURNING 'SIGNAL_DETECTADO';
COMMIT;
```

- **Replay (a chave já existe):** o `claim` volta vazio, o sinal **não é inserido** e o fechamento
  fecha **0 linhas** — sem a marca `SIGNAL_DETECTADO`. O veredito vira `JA_DETECTADO` com o motivo
  `IDEMPOTENCIA_REPLAY`.
- **Por que dois comandos** (e não um, com o fechamento dentro da CTE): as CTEs de escrita e a
  instrução principal rodam no **mesmo snapshot**, então a instrução principal **não enxerga** a
  linha que a CTE acabou de inserir — defeito medido e pago no aceite do Scout. Aqui cada passo é um
  comando próprio (snapshot novo) e o fechamento se **ancora no sinal desta rodada** (não na chave),
  para que o replay não marque sucesso.
- **Cenário de concorrência medido:** a chave já reivindicada com o sinal **ausente** (retentativa
  depois de uma remoção) não estoura `UNIQUE` nem vira `ERRO`: é replay silencioso. É este item que
  prova o `ON CONFLICT (idempotency_key) DO NOTHING`.
- A história de **cada observação** fica em `agent_runs` (uma linha por observação, com o
  `correlation_id` do lote e o `signal_id` no `output`) — auditoria não depende da narrativa de quem
  rodou, e é por ela que o `--desfazer` alcança os sinais da rodada.
- UUID v4 é gerado **no produtor** (o agente), antes de qualquer escrita.

## 5. Guardrails

- **Ambiente (ADR-005):** `--ambiente dev|homolog`. `prod` é **recusado** nesta versão (exit 4) —
  promoção é card próprio com aprovação humana registrada; ambiente não declarado ou desconhecido
  também é recusado (fail-closed). O modo `--planejar` não abre conexão nenhuma.
- **Escrita declarada:** `signals` (INSERT; DELETE só no desfazer), `agent_runs` (INSERT),
  `sync_events` (INSERT/UPDATE; DELETE só no desfazer) e `human_approvals` (INSERT). `organizations`
  e `research_runs` são **leitura** (identidade e vínculo lógico) — escrita nelas é recusa.
- A guarda `validar_sql` roda em **toda** instrução antes de sair e recusa: DDL; tabela não
  declarada; escrita em `organizations`; `INSERT` de sinal sem `organization_id`/`signal_type`
  (NOT NULL do DDL); `INSERT` de sinal com coluna de score/decaimento/expiracao; `DELETE` fora do
  desfazer. Ela varre o **código SQL**, não o conteúdo dos literais: título com "Drop Soluções Ltda"
  ou trecho de evidência com "drop table" não derruba sinal legítimo.
- **Uma regra de identidade, uma implementação:** a normalização e o dígito verificador do CNPJ, o
  domínio canônico e o LinkedIn canônico não são reescritos aqui — o agente **importa** o módulo do
  produtor (`hermes/agents/scout/scout.py`) por caminho, inclusive o escape de literal. A suíte
  reprova se aparecer uma segunda implementação.
- **Natureza da ação (doc 02 §4):** detectar/classificar é **L0** — não exige aprovação humana.
  Ações **L1** (primeiro e-mail, LinkedIn, WhatsApp, proposta) não são executadas por este agente:
  sem SMTP, sem IMAP, sem cliente HTTP no código (a suíte reprova se aparecerem).
- **JEV (doc 07 §7, W4):** "antes de chamadas de LLM em tarefas elegíveis, o Hermes consulta o
  JEV". `chamar_llm()` exige recibo com `decision_id`, `lane` do vocabulário e `outcome = PASS` do
  roteador (`hermes/jev/routing/router.py`); sem recibo, ou com `ESCALATE`/`BLOCK`/lane inválida,
  **levanta** e nada é escrito (fail-closed). Na v1 o caminho de LLM não é exercido: a detecção é
  determinística (§9).

## 6. Operação

O banco vive na VPS do ambiente (**ADR-0008**): o SQL sai por `docker exec … psql`. O agente recebe
o **prefixo psql** do ambiente e registra, no relatório, a identidade do alvo medido
(`current_database()`/`current_user`) — sem afirmar nome de ambiente.

```bash
# planejar (sem banco)
python3 hermes/agents/signal/signal.py --planejar --fonte observacoes.jsonl

# detectar no dev
python3 hermes/agents/signal/signal.py --ambiente dev --fonte observacoes.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/signal-rodada.json

# desfazer uma rodada (dry-run por padrão)
python3 hermes/agents/signal/signal.py --desfazer <correlation_id>
python3 hermes/agents/signal/signal.py --desfazer <correlation_id> --confirmo
```

## 7. ACCEPTANCE (critérios de aceitação)

- **A1** modo `--planejar` roda sem abrir conexão de banco: 0 escrita, exit 0.
- **A2** observação legítima grava **uma** linha em `signals` com `organization_id` da empresa
  casada, `signal_type` do vocabulário, `signal_category` derivada, `source_type`/`source_url` da
  fonte primária, `event_date` da fonte, `detected_at`, `confidence` (NULL quando ausente),
  `evidence` com as fontes e `id` UUID v4.
- **A3** a detecção **não cria** organização e não escreve nenhuma coluna de `organizations`.
- **A4** organização inexistente → `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`), nada em `signals`.
- **A5** identidade casando com duas ou mais organizações distintas → `REVISAO_IDENTIDADE` com
  `human_approvals` `PENDING` (`SIGNAL_IDENTITY_REVIEW`); se a escrita da fila humana falhar, o
  veredito é `ERRO` (fail-closed).
- **A6** observação inválida (sem tipo, tipo fora do vocabulário, sem fonte, fonte/URL/trecho
  inválidos, sem identificador forte, forte declarado e inválido) é `RECUSADA` com motivo; nada
  escrito em `signals`.
- **A7** reprocessar a **mesma** observação cria 0 linhas novas (contagem antes/depois) e o veredito
  é `JA_DETECTADO` (`IDEMPOTENCIA_REPLAY`); a chave reivindicada com o sinal ausente não vira erro.
- **A8** **nenhum score** é escrito: `buying_signal_points`, `relevance_score`, `decay_factor` e
  `expires_at` ficam como o contrato os deixou (o `decay_factor` no default 1) e a guarda recusa
  quem tentar; categoria declarada pela fonte não é escrita; campo não declarado não é escrito.
- **A9** auditoria: uma linha de `agent_runs` por observação (`agent_name=signal`,
  `agent_role=signal_detection`, `correlation_id` do lote, `output` com veredito e `signal_id`); se a
  linha de auditoria não for escrita, a execução é `ERRO`.
- **A10** `--ambiente prod` é recusado (exit 4) e nada é escrito; ambiente não declarado idem.
- **A11** nenhuma escrita fora das tabelas declaradas; nenhum `DELETE` fora do
  `--desfazer --confirmo`; nenhuma chamada de LLM sem recibo válido do JEV; nenhum acesso a
  Odoo/Titan/n8n; nenhum cliente HTTP no código.
- **A12** `--desfazer <correlation_id>` é dry-run por padrão; com `--confirmo` **remove os sinais da
  rodada** e os `sync_events` deles e registra um `sync_events` de `ROLLBACK`, sem tocar
  `organizations`, `agent_runs` nem `human_approvals`.
- **A13** artefatos versionados e cobertos por `scripts/verificar_estrutura.sh`; a suíte passa com
  autoteste por mutação.

## 8. TEST

- **Offline (contrato e regra):**
  `python3 scripts/agentes/verificar_agente_signal.py --autoteste` — **75 itens**: contrato do agente
  espelhado no código, vocabulário de 18 tipos de sinal contra o Data Contract V1.0 e contra o doc 03
  §7, tabela de categorias derivadas cobrindo o vocabulário **exatamente uma vez**, colunas do
  `INSERT` de `signals` conferidas contra o DDL congelado (inclusive as duas `NOT NULL`), colunas de
  score fora da escrita, canais iguais aos dos outros dois agentes da W4, identidade forte importada
  do produtor (sem segunda cópia), validação/descarte da observação (tipo, fontes, título, descrição,
  data, confiança, vínculo, campo não declarado, categoria derivada), decisão de veredito pura, hash
  de idempotência estável e sensível ao conteúdo, guarda de escrita (DDL, tabela fora da lista,
  `organizations`, coluna de score, sinal sem coluna `NOT NULL`, `DELETE` fora do desfazer, prosa com
  "drop" não é DDL), gate do JEV fail-closed, ausência de rede/LLM, a guarda da **própria prova**
  (item esperado inexistente ou mutação sem item declarado **reprova**) e o **fluxo completo** numa
  **porta de roteiro** (detectar, não escrever em empresa, replay idempotente, revisão, recusa,
  organização inexistente, vínculo com e sem `research_run`, auditoria que falha, porta indisponível,
  dry-run falha, desfazer dry-run/confirmado). O autoteste muta **cópia** do arquivo sob teste
  (`--codigo`; o canônico, por padrão) e exige que o item correspondente **reprove** — hoje 20/20.
- **E2E (banco real, descartável):** `bash scripts/agentes/teste_signal_aceite.sh` na VPS, em
  container PostgreSQL descartável (`pg-signal-acc`) com a migration 0001, **3 organizações
  pré-existentes** e **1 `research_run`** (o vínculo lógico real) — com veredito
  `ACEITE_SIGNAL_001_OK`: detecções legítimas dos vários tipos, categoria derivada e conferida por
  SQL, contagem antes/depois medindo a criação de `signals`, **não** criação de organização, nenhuma
  coluna de score preenchida, descartes com motivo medidos por SQL, retry sem duplicata, replay com a
  chave já reivindicada, `prod` recusado sem escrita, `--planejar` sem porta e desfazer
  dry-run/`--confirmo`. O `--prova-de-dente` muta a cópia do agente e exige, para **cada** mutação,
  que o aceite reprove **o item esperado** (não apenas "falhou"), com baseline verde antes e depois —
  hoje 4/4 (idempotência, fechamento sem âncora no sinal, categoria chumbada, vínculo quebrado aceito).
- **Evidência no `docs/operations/registro-de-execucoes.md`** com comando e saída reais.

## 9. ROLLBACK

- **Código:** a branch do card é **aditiva** (arquivos novos + bloco novo no gate de estrutura,
  CHANGELOG e registro). Reverter a branch devolve o estado anterior; nenhum comportamento existente
  muda.
- **Dados (dev/homolog):** `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo` aplica)
  **apaga os sinais da rodada** e os `sync_events` deles e grava um `sync_events` de `ROLLBACK`. Não
  há valor anterior a restaurar — o sinal é uma **linha nova** (aditivo), e a prova de que a linha
  pertence à rodada é o `sync_events` da chave, que é como o `DELETE` chega no sinal certo.
  `organizations`, `research_runs`, `agent_runs` (auditoria) e `human_approvals` (fila humana) não
  são tocados.
- **Limite declarado:** o desfazer alcança o que a rodada **criou e registrou**. Sinal removido por
  fora (não pelo agente) deixa o `sync_event` órfão visível, e o que a rodada **não** criou (revisão,
  recusa) não tem nada a desfazer.
- **Schema:** **nenhuma migration nova** — rollback não exige restore de banco.

## 10. RISK

- **R1 (alto) — inflar score por conta própria.** O card irmão de score é o W5; se o detector
  preenchesse `buying_signal_points`/`relevance_score`/`decay_factor`, o produto teria score sem
  fórmula homologada. Mitigado **por desenho**: colunas na lista proibida do contrato **e** recusa na
  guarda de escrita — medido por mutação na suíte e pelo dente do aceite.
- **R2 (médio) — sinal preso à empresa errada.** Identidade casando com duas ou mais organizações
  não é resolvida por heurística: vai para a fila humana (`dedup.rule` do contrato).
- **R3 (médio) — sinal duplicado em retry.** A idempotência vive no **SQL**
  (`sync_events.idempotency_key` UNIQUE + fechamento ancorado no sinal da rodada), não na prosa;
  replay não insere e não marca sucesso.
- **R4 (médio) — evidência perdida.** Fonte/trecho vão para `signals.evidence` e
  `sync_events.request_payload` (regra "conservar evidência" do compliance), e todo descarte fica
  registrado com campo, motivo e valor.
- **R5 (baixo) — duas observações distintas do mesmo tipo.** São legítimas e geram dois sinais (o
  baseline não define janela de validade de sinal); o que a idempotência barra é a **mesma**
  observação reapresentada.
- **R6 (baixo) — custo/volume.** Sem LLM e sem rede na v1: custo zero por observação; a rodada é
  limitada ao que a fonte entrega (sem paginação/crawler).

## 11. Decisões de implementação

- **D1** base da branch no head aprovado do card pai (`feature/TRE-W4-E02-T01` @ `82ff096`).
- **D2** o detector **não cria** organização e **não escreve** nenhuma coluna de `organizations`: o
  sinal é aditivo.
- **D3** nenhum score é calculado aqui (W5 é de outro card): as quatro colunas de força/atualidade
  ficam fora da escrita e a guarda recusa quem tentar.
- **D4** a categoria é **derivada** do tipo por tabela declarada; a da fonte é descartada.
- **D5** uma linha de `agent_runs` por observação, com o `correlation_id` do lote e o `signal_id` no
  `output` (é o vínculo que o `--desfazer` usa).
- **D6** v1 não escreve em produção: `prod` recusado por desenho, promoção é card próprio.
- **D7** a identidade forte vem do contrato a cada execução; a suíte reprova se a lista do agente
  divergir de `dedup.strong`.
- **D8** a regra de identidade é **importada** do produtor (Scout) — uma implementação só.
- **D9** o vínculo com `research_run_id` (lógico, sem FK) é **conferido por existência** antes de ser
  escrito; vínculo quebrado é descartado com motivo e o sinal segue.

## 12. Lacunas declaradas

1. **Sem LLM e sem crawler:** a fonte entrega a observação e a evidência; busca ativa (web/LinkedIn)
   não está implementada. O gate do JEV existe e é fail-closed, mas não há caminho de LLM na v1.
2. **Sem score:** `buying_signal_points`, `relevance_score`, `decay_factor` e `expires_at` não são
   escritos — o Buying Signal Score é `TRE-W5-E03-T01`. Nenhum limiar de relevância é aplicado (o
   detector entrega o fato, não o julgamento).
3. **Sem hipótese de dor e sem contato:** `pain_hypotheses` e `contacts` são W4-E04/E05.
4. **Sem outbox:** nenhum evento PG→Odoo nasce na detecção (o contrato não lista evento de sinal); a
   passagem de estado no funil é do Odoo, dono do estágio.
5. **Sem janela de validade de sinal:** o baseline não define expiração; a coluna `expires_at` fica
   `NULL` e duas observações legitimamente distintas geram dois sinais.
6. **Confiança não filtra nada:** confiança ausente fica `NULL` e é registrada como veio; o detector
   não descarta observação por confiança baixa (isso seria julgamento de score).
7. **`retention_until`/`collected_at` não existem** no contrato V1 (lacuna já declarada no
   `compliance` do contrato; não inventamos coluna).

## 13. Referências

- Baseline V1.1.0: docs 02 §1/§2/§4, 03 §2/§7, 04 §3/§5, 06 §2/§5/§7, 07 §7-§8, 11 §2, 12 §1/§7/§8.
- `docs/data/DATA_CONTRACT_V1.md` e `docs/data/data_contract_v1.json` (`vocabularies.signal_type`,
  `source_of_truth.sinais = PostgreSQL`, `dedup`, `logical_links_without_fk`, `governance`).
- `docs/architecture/agente-scout-v1.md` (o produtor da empresa e a regra de identidade importada) e
  `docs/architecture/agente-research-v1.md` (o enriquecimento e a decisão de não invadir a W5).
- `ADR-0005` (nada nasce em produção), `ADR-0007` (ambientes), `ADR-0008` (acesso ao PostgreSQL).
- `docs/runbooks/agente-signal.md` · `docs/runbooks/agente-scout.md` · `docs/runbooks/agente-research.md`.
