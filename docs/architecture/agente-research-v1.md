# Agente Research v1 — contrato da pesquisa (TRE-W4-E02-T01)

**Card:** `TRE-W4-E02-T01` (W4 · E02 · P1) · **Onda:** W4 — Hermes Sales Intelligence
**Versão do agente:** `research/1.0.0` · **Papel:** `research` (Hermes Sales AI, doc 02 §2)
**Artefato legível por máquina:** `hermes/agents/research/agente-research-v1.json`
**Implementação:** `hermes/agents/research/research.py` · **Suíte:** `scripts/agentes/verificar_agente_research.py`
**Aceite E2E:** `scripts/agentes/teste_research_aceite.sh` · **Runbook:** `docs/runbooks/agente-research.md`

## 1. Por que este agente existe

O funil do doc 03 §2 tem, depois de **Descoberto**, o estado **Pesquisado** — e o doc 07 §7 lista o
`Research Agent` como o segundo item da W4. O Scout (TRE-W4-E01-T01) cria a empresa candidata com
`status='DISCOVERED'` e **não enriquece**: nada no PostgreSQL escrevia `research_runs`, nem
preenchia indústria, faixa de funcionários, site ou cidade da empresa descoberta. Sem isso, a W4
inteira (sinais, hipótese de dor, contatos) e a W5 (scores de ICP/Data Quality) não teriam de onde
partir.

O Research é esse produtor — e só isso: resolve a empresa **que já existe** pelos identificadores
fortes do contrato, registra a execução da pesquisa em `research_runs` (com evidência, `input_hash`
e `source_count`) e **enriquece as colunas vazias** da organização com o que a fonte mostrou. Nada
mais: sem sinais, sem hipótese, sem contato, sem score, sem evento de outbox.

## 2. Entrada — o pedido de pesquisa

Arquivo JSONL, um pedido por linha (`hermes/agents/research/exemplos/pesquisas-exemplo.jsonl`):

```json
{"organizacao":{"cnpj":"45.723.174/0001-10"},"tipo":"SIZE_AND_STRUCTURE",
 "fontes":[{"tipo":"DADOS_PUBLICOS","url":"https://empresas.example/nova-alpha",
            "trecho":"210 colaboradores; 2 unidades"}],
 "achados":{"employee_count":210,"unit_count":2,"revenue_estimate":18500000},
 "confianca":0.7}
```

- **Identidade (`organizacao`):** um ou mais identificadores **fortes** do Data Contract V1.0
  (`cnpj → domain → linkedin_url`). A pesquisa **não cria** empresa: identidade que não casa é
  `RECUSADA` (`ORGANIZACAO_NAO_ENCONTRADA`).
- **Tipo (`tipo`):** vocabulário fechado do agente — `COMPANY_PROFILE`, `SIZE_AND_STRUCTURE`,
  `INDUSTRY`, `DIGITAL_PRESENCE`. Cada tipo declara **quais colunas** pode enriquecer.
- **Fontes (`fontes`):** **pelo menos uma**, com tipo do vocabulário de canais (`LINKEDIN`, `WEB`,
  `GOOGLE`, `META`, `WHATSAPP`, `TITAN`, `EVENTOS`, `DADOS_PUBLICOS` — o mesmo vocabulário do
  Scout, porque é o mesmo canal do doc 02 §1). É a evidência conservada (compliance: "conservar
  evidência") e alimenta `research_runs.source_count`.
- **Achados (`achados`):** `{coluna_de_organizations: valor}`. O que é inválido é **descartado com
  motivo** (`structured_output.achados_descartados`), nunca escrito.
- **Confiança (`confianca`):** opcional, 0–1. Ausente = `NULL` — não se inventa confiança.

## 3. Enriquecimento — a regra que decide o que é escrito

| Situação | O que acontece |
|---|---|
| Coluna **vazia** (`NULL`/`''`) e coluna é do **tipo** do pedido | enriquecida com o achado |
| Coluna **já preenchida** | **preservada** — o valor atual vence o achado da pesquisa |
| Coluna **fora do tipo** do pedido (ex.: `employee_count` num `COMPANY_PROFILE`) | achado descartado (`COLUNA_FORA_DO_TIPO`) |
| Coluna **não declarada** (ex.: campo inventado pela fonte) | achado descartado (`CAMPO_NAO_DECLARADO`) |
| **Identificador forte** nos achados (`cnpj`, `domain`, `linkedin_url`) | descartado (`IDENTIFICADOR_FORTE_NAO_ESCRITO_PELA_PESQUISA`), registrado como evidência |
| `employee_band` vindo da fonte | descartado (`DERIVADO_NAO_ACEITO`); a faixa é **derivada** de `employee_count` |
| Valor inválido (país fora de `AA`, URL não `http(s)`, número fora da faixa, texto acima do limite) | descartado com o motivo específico |

Regras que sustentam a tabela:

- **Não sobrescrever é garantido pelo SQL, não pela prosa:** texto grava
  `COALESCE(NULLIF(coluna, ''), <valor>)` e número grava `COALESCE(coluna, <valor>)`. A guarda de
  escrita **exige** exatamente esse formato — trocar por atribuição direta faz o agente recusar a
  própria instrução (medido por mutação no aceite: `enriquecimento-sem-coalesce`).
- **A pesquisa não escreve identidade nem estágio:** `cnpj`, `domain`, `linkedin_url`, `status`,
  `data_quality_score`, `odoo_partner_id`, `created_at`, `deleted_at` estão na lista de colunas
  proibidas do contrato do agente. Identidade é do produtor (Scout) e do módulo de dedup
  (`TRE-W1-E04-T01`); estágio é do Odoo (doc 12 §1); score é W5. Um forte achado pela pesquisa
  entra em `structured_output` como **evidência para revisão humana**, não como escrita.
- **`employee_band` é derivada**, pelo vocabulário fechado do contrato (`LT_70` … `GT_1000`,
  `UNKNOWN`), com os limites do doc 03 §5 — a suíte compara a tabela de faixas com o vocabulário
  do Data Contract e com os limites do contrato do agente.
- **Um único pedido não escreve coluna de outro tipo:** o `SET` do `UPDATE` carrega apenas as
  colunas do tipo declarado; colunas sem achado **não entram** no `SET` (não há clobber de `NULL`).

## 4. Idempotência — retry não cria duplicata (doc 06 §7)

A chave é a **entrada**, não a rodada: `research:org:<uuid>:<tipo>:<input_hash>`, gravada em
`sync_events.idempotency_key` (**UNIQUE** no contrato). `input_hash` = `sha256` do JSON canônico
(identidades normalizadas + tipo + achados aceitos + fontes), então a mesma entrada apresentada de
novo produz o mesmo hash.

Cada pedido é ingerido por **uma transação com três comandos**:

```sql
BEGIN;
WITH claim AS (INSERT INTO ...sync_events (...) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id)
INSERT INTO ...research_runs (...) SELECT ... FROM claim ON CONFLICT (id) DO NOTHING RETURNING id;
UPDATE ...organizations SET <coluna> = COALESCE(...), updated_at = now()
 WHERE id = '<org>' AND EXISTS (SELECT 1 FROM ...research_runs WHERE id = '<run>') RETURNING id;
UPDATE ...sync_events SET status='SUCCESS', response_payload=...
 WHERE idempotency_key = '<chave>' AND EXISTS (SELECT 1 FROM ...research_runs WHERE id = '<run>')
 RETURNING 'RESEARCH_PESQUISADA';
COMMIT;
```

- **Replay (a chave já existe):** o `claim` volta vazio, o `research_run` **não é inserido**, o
  enriquecimento **não roda** (nada é reaplicado) e o fechamento fecha **0 linhas** — sem a marca
  `RESEARCH_PESQUISADA`. O veredito vira `JA_PESQUISADO` com o motivo `IDEMPOTENCIA_REPLAY`.
- **Por que três comandos** (e não um, com o fechamento dentro da CTE): as CTEs de escrita e a
  instrução principal rodam no **mesmo snapshot**, então a instrução principal **não enxerga** a
  linha que a CTE acabou de inserir — defeito medido e pago no aceite do Scout. Aqui cada passo é
  um comando próprio (snapshot novo) e **todos se ancoram na existência do `research_run` desta
  rodada** (não na chave), para que o replay não marque sucesso.
- **Cenário de concorrência medido:** a chave já reivindicada com o `research_run` **ausente**
  (retentativa depois de uma remoção) não estoura `UNIQUE` nem vira `ERRO`: é replay silencioso.
  É este item que prova o `ON CONFLICT (idempotency_key) DO NOTHING` (medido em 3 rodadas no E2E).
- `research_runs.input_hash` e `sync_events.request_payload` guardam o hashing e os valores
  **ANTES/DEPOIS** de cada coluna — é deles que o `--desfazer` restaura.
- A história de **cada tentativa** fica em `agent_runs` (uma linha por pedido, com o
  `correlation_id` do lote) — auditoria não depende da narrativa de quem rodou.
- UUID v4 é gerado **no produtor** (o agente), antes de qualquer escrita (`canonical_ids`).

## 5. Guardrails

- **Ambiente (ADR-005):** `--ambiente dev|homolog`. `prod` é **recusado** nesta versão (exit 4) —
  promoção é card próprio com aprovação humana registrada; ambiente não declarado ou desconhecido
  também é recusado (fail-closed). O modo `--planejar` não abre conexão nenhuma.
- **Escrita declarada:** `research_runs` (INSERT; DELETE só no desfazer), `organizations`
  (**UPDATE** — enriquecimento com COALESCE ou restauração do `--desfazer --confirmo`; **nenhum**
  `DELETE`, nenhuma criação), `agent_runs` (INSERT), `sync_events` (INSERT/UPDATE; DELETE só no
  desfazer) e `human_approvals` (INSERT). A guarda `validar_sql` roda em **toda** instrução antes
  de sair e recusa: DDL, tabela não declarada, `UPDATE` de organização sem modo, coluna fora das
  11 de enriquecimento, `UPDATE` de organização **sem COALESCE**, `DELETE` de organização e
  `DELETE` fora do desfazer. Ela varre o **código SQL**, não o conteúdo dos literais: nome de
  empresa (`Drop Solucoes Ltda`) não derruba pedido legítimo.
- **Uma regra de identidade, uma implementação:** a normalização e o dígito verificador do CNPJ não
  são reescritos aqui — o agente **importa** o módulo do produtor (`hermes/agents/scout/scout.py`)
  por caminho. A suíte reprova se aparecer uma segunda implementação no arquivo do agente.
- **Natureza da ação (doc 02 §4):** pesquisar/enriquecer/classificar é **L0** — não exige aprovação
  humana. Ações **L1** (primeiro e-mail, LinkedIn, WhatsApp, proposta) não são executadas por este
  agente: sem SMTP, sem IMAP, sem cliente HTTP no código (a suíte reprova se aparecerem).
- **JEV (doc 07 §7, W4):** "antes de chamadas de LLM em tarefas elegíveis, o Hermes consulta o
  JEV". `chamar_llm()` exige recibo com `decision_id`, `lane` do vocabulário e `outcome = PASS` do
  roteador (`hermes/jev/routing/router.py`); sem recibo, ou com `ESCALATE`/`BLOCK`/lane inválida,
  **levanta e o pedido não é escrito** (fail-closed). Na v1 o caminho de LLM não é exercido: a
  pesquisa é determinística (§9).
- **Ambiguidade é reportada, nunca resolvida por heurística** (`dedup.rule`): dois casamentos
  distintos param na fila humana (`human_approvals` `PENDING`, `RESEARCH_IDENTITY_REVIEW`).

## 6. Operação

O banco vive na VPS do ambiente (**ADR-0008**): o SQL sai por `docker exec … psql`. O agente recebe
o **prefixo psql** do ambiente e registra, no relatório, a identidade do alvo medido
(`current_database()`/`current_user`) — sem afirmar nome de ambiente, porque a convenção de nome
divergiu no dev e alinhar isso é decisão do dono, não do agente.

```bash
# planejar (sem banco)
python3 hermes/agents/research/research.py --planejar --fonte pesquisas.jsonl

# pesquisar no dev
python3 hermes/agents/research/research.py --ambiente dev --fonte pesquisas.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/research-rodada.json

# desfazer uma rodada (dry-run por padrão)
python3 hermes/agents/research/research.py --desfazer <correlation_id>
python3 hermes/agents/research/research.py --desfazer <correlation_id> --confirmo
```

## 7. ACCEPTANCE (critérios de aceitação)

- **A1** modo `--planejar` roda sem abrir conexão de banco: 0 escrita, exit 0.
- **A2** pesquisa de empresa existente grava `research_runs` com `agent_name=research`,
  `agent_version=1.0.0`, `research_type` do vocabulário, `source_count` = nº de fontes,
  `input_hash` de 64 hex, `started_at`/`completed_at`/`created_at` e `status='COMPLETED'`.
- **A3** coluna **já preenchida** permanece com o valor anterior (medido antes/depois); coluna
  vazia recebe o achado.
- **A4** a pesquisa **não cria** organização: identidade que não casa é `RECUSADA` com
  `ORGANIZACAO_NAO_ENCONTRADA` e nenhuma linha nova.
- **A5** identidade casando com duas ou mais organizações distintas → `REVISAO_IDENTIDADE` com
  `human_approvals` `PENDING` (`RESEARCH_IDENTITY_REVIEW`) e evidência; se a escrita da fila humana
  falhar, o veredito é `ERRO` (fail-closed).
- **A6** pedido inválido (sem identificador forte, forte declarado e inválido, sem fonte, fonte ou
  tipo fora do vocabulário) é `RECUSADA` com motivo; nada escrito em `research_runs`.
- **A7** reprocessar a **mesma** entrada cria 0 `research_runs` novo (medido por contagem) e o
  veredito é `JA_PESQUISADO` (`IDEMPOTENCIA_REPLAY`); a chave reivindicada com o run ausente não
  vira erro (prova do `ON CONFLICT (idempotency_key)`).
- **A8** `status`, `cnpj`, `domain`, `linkedin_url`, `deleted_at`, `odoo_partner_id` e
  `data_quality_score` **nunca** são escritos; achado fora do tipo declarado não é escrito;
  identificador forte achado fica só como evidência em `structured_output`.
- **A9** auditoria: uma linha de `agent_runs` por pedido (`agent_name=research`,
  `agent_role=research`, `correlation_id` do lote, `output` com veredito e `research_run_id`); se a
  linha de auditoria não for escrita, a execução é `ERRO` (nada de `COMPLETED` sem auditoria).
- **A10** `--ambiente prod` é recusado (exit 4) e nada é escrito; ambiente não declarado idem.
- **A11** nenhuma escrita fora das 5 tabelas declaradas; nenhum `DELETE` em `organizations`;
  nenhuma chamada de LLM sem recibo válido do JEV; nenhum acesso a Odoo/Titan/n8n; nenhum cliente
  HTTP no código.
- **A12** `--desfazer <correlation_id>` é dry-run por padrão; com `--confirmo` **restaura** os
  valores anteriores das colunas que a rodada enriqueceu (com o tipo da coluna), apaga os
  `research_runs` e `sync_events` da rodada e registra um `sync_events` de `ROLLBACK`, sem tocar
  `agent_runs` e `human_approvals`.
- **A13** artefatos versionados e cobertos por `scripts/verificar_estrutura.sh`; a suíte passa com
  autoteste por mutação.

## 8. TEST

- **Offline (contrato e regra):**
  `python3 scripts/agentes/verificar_agente_research.py --autoteste` — **65 itens**: contrato do
  agente espelhado no código, identidade forte lida do Data Contract V1.0, vocabulário de fontes
  idêntico ao do Scout (mesmo canal), colunas de enriquecimento contra o DDL congelado, colunas
  proibidas (identidade/estágio/score) fora da lista, INSERT de `research_runs`/`agent_runs`/
  `human_approvals`/`sync_events` conferido contra o DDL (colunas **e** paridade colunas × valores),
  faixas de empregados contra o vocabulário e contra os limites do contrato, validação/descarte de
  achados, decisão de veredito, guarda de escrita (DDL, tabela não declarada, organização sem modo,
  coluna proibida, sem COALESCE, DELETE de organização, DELETE fora do desfazer, separação do `SET`
  com parênteses), gate do JEV fail-closed, ausência de rede/LLM, a guarda da **própria prova** (item
  esperado inexistente ou mutação sem item declarado **reprova**, em vez de passar muda), e o fluxo
  completo numa **porta de roteiro** (pesquisar, não sobrescrever, replay idempotente, revisão, recusa,
  auditoria que falha, desfazer dry-run/confirmado). O autoteste muta **cópia** do arquivo sob teste
  (`--codigo`; o canônico, por padrão) e exige que o item correspondente **reprove** — hoje 15/15.
- **E2E (banco real, descartável):** `bash scripts/agentes/teste_research_aceite.sh` na VPS, em
  container PostgreSQL descartável (`pg-research-acc`) com a migration 0001 e **3 organizações
  pré-existentes** (uma com `industry_name` já preenchido: é o dado curado) — **55 itens**, em 3
  rodadas mais as guardas, com veredito `ACEITE_RESEARCH_001_OK`: 6 pesquisas legítimas (os 4 tipos),
  contagem antes/depois medindo criação
  de `research_runs`, **não** criação de organização, não sobrescrita da coluna curada, faixa
  derivada do número, coluna fora do tipo não escrita, descartes com motivo medidos por SQL,
  retry sem duplicata, replay com a chave já reivindicada, `prod` recusado sem escrita, `--planejar`
  sem porta e desfazer dry-run/`--confirmo` (restauração + remoção + `ROLLBACK`). O
  `--prova-de-dente` muta a cópia do agente (**4 mutações**) e exige, para **cada uma**, que o
  aceite reprove **o item esperado** (não apenas "falhou"), com baseline verde antes e depois.
- **Evidência no `docs/operations/registro-de-execucoes.md`** com comando e saída reais.

## 9. ROLLBACK

- **Código:** a branch do card é **aditiva** (arquivos novos + bloco novo no gate de estrutura,
  CHANGELOG e registro). Reverter a branch devolve o estado anterior; nenhum comportamento
  existente muda.
- **Dados (dev/homolog):** `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo` aplica)
  **restaura** as colunas enriquecidas para os valores anteriores gravados no próprio
  `sync_events` da rodada, apaga os `research_runs` e `sync_events` da rodada e registra um
  `sync_events` de `ROLLBACK`. A organização pré-existente **não** é apagada, e `agent_runs`
  (auditoria) e `human_approvals` (fila humana) não são tocados.
- **Limite declarado:** o desfazer restaura o que consegue provar pelas `research_runs` da rodada.
  Se um `research_run` for removido **por fora** (não pelo agente), o `sync_event` correspondente
  fica órfão e a coluna que ele enriqueceu **não** é restaurada — o `sync_event` órfão continua
  visível para quem for investigar. Medido no aceite (cenário de replay da rodada 3).
- **Schema:** **nenhuma migration nova** — rollback não exige restore de banco.

## 10. RISK

- **R1 (alto) — sobrescrever dado curado por humano.** Mitigado **no SQL**: `COALESCE(NULLIF(col,''),
  valor)` para texto e `COALESCE(col, valor)` para número, com a guarda exigindo exatamente esse
  formato. Um `UPDATE` por atribuição direta é recusado — medido pelo dente.
- **R2 (médio) — pesquisa mexendo em identidade ou estágio.** Mitigado por desenho: colunas
  proibidas + `DELETE` de organização bloqueado + nenhuma criação de empresa. Identidade segue no
  Scout e no módulo de dedup; estágio é do Odoo.
- **R3 (médio) — fonte entrega dado sujo ou inventado.** Achado inválido é descartado **com
  motivo** e o descarte fica auditado em `structured_output`; campo não declarado vira descarte,
  nunca coluna. Nada é mesclado em silêncio e nada é inventado (confiança ausente fica `NULL`).
- **R4 (baixo) — custo/volume.** Sem LLM na v1: custo zero por pedido; a rodada é limitada ao que a
  fonte entrega (sem paginação/crawler).
- **R5 (baixo) — duas pesquisas distintas para a mesma empresa.** São legítimas e geram duas
  `research_runs` (o baseline não define janela de validade); o enriquecimento continua sendo
  idempotente por coluna, porque a coluna já preenchida nunca é sobrescrita.

## 11. Decisões de implementação

- **D1** base da branch no head aprovado do card pai (`feature/TRE-W4-E01-T01` @ `cb2c53a`).
- **D2** a pesquisa **não cria** organização nem escreve identificador forte: resolve a empresa pelo
  forte e enriquece só o que o tipo declarado permite.
- **D3** uma linha de `agent_runs` por pedido, com `correlation_id` compartilhado do lote.
- **D4** v1 não escreve em produção: `prod` recusado por desenho, promoção é card próprio.
- **D5** a identidade forte vem do contrato a cada execução; a suíte reprova se a lista do agente
  divergir de `dedup.strong`.
- **D6** a regra de identidade é **importada** do produtor (Scout) — uma implementação só.
- **D7** o enriquecimento é ancorado no `research_run` desta rodada (`EXISTS`), para que o replay
  não reaplique nada.

## 12. Lacunas declaradas

1. **Sem LLM e sem crawler:** a fonte entrega as fontes e os achados; busca ativa (web/LinkedIn)
   não está implementada. O gate do JEV existe e é fail-closed, mas não há caminho de LLM na v1.
2. **Sem score:** `data_quality_score` continua `NULL` (Data Quality Score é W5) e nenhum score é
   calculado aqui.
3. **Sem sinais, sem hipótese de dor e sem contato:** `signals`, `pain_hypotheses` e `contacts` são
   W4-E03/E04/E05.
4. **Sem outbox:** nenhum evento PG→Odoo nasce na pesquisa (o contrato não lista evento de
   pesquisa); a passagem de *Descoberto* para *Pesquisado* no funil é do Odoo, dono do estágio.
5. **Identificador forte achado pela pesquisa não é escrito** na organização (decisão D2 —
   evita colisão de identidade fora do módulo de dedup): fica em `structured_output` como
   evidência para revisão.
6. **`retention_until`/`collected_at` não existem** no contrato V1 (lacuna já declarada no
   `compliance` do contrato; não inventamos coluna).
7. **Sem paginação/limite de volume:** o baseline não define limite por rodada.

## 13. Referências

- Baseline V1.1.0: docs 02 §2/§4, 03 §2/§5, 04 §6, 06 §2/§7, 07 §7-§8, 11 §2, 12 §1/§7/§8.
- `docs/data/DATA_CONTRACT_V1.md` e `docs/data/data_contract_v1.json` (dedup, vocabulários,
  eventos, IDs canônicos, governance).
- `docs/architecture/agente-scout-v1.md` (o produtor da empresa e a regra de identidade importada).
- `ADR-0005` (nada nasce em produção), `ADR-0007` (ambientes), `ADR-0008` (acesso ao PostgreSQL).
- `docs/runbooks/agente-research.md` · `docs/runbooks/agente-scout.md`.
