# Agente Contact Research v1 — contrato do contato comercial (TRE-W4-E05-T01)

> Terceiro agente da W4. Roda **depois** do Research: recebe a empresa **já pesquisada** e
> devolve o **contato comercial** dela (o decision maker), gravando em `contacts` e deixando o
> rastro da rodada em `sync_events` + `agent_runs`. Evento de CRM (`DECISION_MAKER_FOUND`) é
> **medido** e **não emitido** nesta versão — o consumidor versionado (`n8n/contracts/`) não o
> cobre (lacuna declarada na §12).

## 1. Por que este agente existe

A empresa descoberta e pesquisada é o **alvo**; sem um nome do outro lado não existe conversa
comercial. O `organizations` diz *para quem* vender; o `contacts` diz *para quem falar*. Este
agente é o produtor do `contacts` na v1: resolve a empresa por identificador **forte** (a mesma
regra do Scout/Research, importada do Data Contract V1.0, sem segunda cópia) e grava o contato
com a base legal declarada — nada mais.

O que ele faz, e **só** isto:

- recebe pedidos de contato (empresa por identificador forte + contato candidato + fontes);
- resolve a empresa em `organizations` (1 casa; 2+ = fila humana; 0 = recusa);
- casa o contato por **e-mail** (identidade), sem diferença de caixa;
- cria o contato que não existe; **enriquece** o que existe, sem sobrescrever o que está
  preenchido e nunca tocando a identidade nem o opt-out do titular;
- registra a rodada (`sync_events` com `antes`/`depois` e `input_hash`), audita cada pedido
  (`agent_runs`) e mede a elegibilidade do evento `DECISION_MAKER_FOUND`.

O que ele **não** faz: não cria empresa, não escreve a identidade (e-mail), não mexe em
estágio/score, não liga para Odoo/Titan/n8n, não chama LLM e não escreve em `prod`.

## 2. Entrada — o pedido de contato

Fonte JSONL (uma linha por pedido) ou stdin. Campos:

| Campo | Obrigatório | Regra |
| --- | --- | --- |
| `organizacao` | sim | pelo menos **um identificador forte** (`cnpj`, `domain` ou `linkedin_url`) |
| `contato.full_name` | sim | nome do contato; sem ele o veredito é `RECUSADA` |
| `contato.email` | sim | identidade: obrigatório, normalizado para minúsculas, ≤ 320 |
| `contato.legal_basis` | sim | base legal (LGPD) declarada; sem ela `RECUSADA` |
| `contato.*` | não | **só** colunas declaradas do `contacts` (fora disso vira descarte) |
| `fontes[]` | sim | ≥ 1 fonte com tipo do vocabulário fechado |
| `confianca` | não | 0..1 |

Vereditos: `IDENTIFICADO` (contato escrito), `JA_IDENTIFICADO` (replay idempotente),
`REVISAO_IDENTIDADE` (empresa ambígua → fila humana), `RECUSADA` (entrada inválida) e `ERRO`
(falha de porta/auditoria, fail-closed).

## 3. Enriquecimento — a regra que decide o que é escrito

O agente é **criador** do contato e **enriquecedor** do que já existe:

1. **Identidade é do e-mail** — o casamento é `lower(email)` dos dois lados. Contato existente
   com `MARINA.ALVES@VALEFOR...,` casa com o pedido `marina.alves@...` e **não** é reescrito.
2. **Coluna preenchida não é sobrescrita.** O `UPDATE` só existe na forma
   `COALESCE(NULLIF(coluna, ''), valor)` (texto) — e a guarda de escrita **recusa** qualquer
   `SET` por atribuição direta (medido por mutação).
3. **Identidade e opt-out nunca são escritos.** `email`, `do_not_contact`, `opt_out_email`,
   `opt_out_whatsapp`, `odoo_partner_id` e os scores **não** entram no `SET` nem no `INSERT`
   (a guarda reprova; o aceite mede `do_not_contact`/`opt_out_email` preservados).
4. **Campo não declarado vira descarte com motivo** (`CAMPO_NAO_DECLARADO`), nunca coluna.
5. **Papel de decisão fora do vocabulário fechado é descartado** (`PAPEL_FORA_DO_VOCABULARIO`),
   nunca gravado.

## 4. Idempotência — retry não cria duplicata (doc 06 §7)

- A rodada de cada pedido é ancorada por uma **claim** em `sync_events`:
  `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING` com a mesma
  `idempotency_key = contact:<correlation>:<sha256 do pedido>`; toda instrução da rodada depende
  do `sync_event` **daquela** rodada (`... FROM sync_events WHERE id = <id> AND status='PENDING'`).
- Reenvio da mesma fonte → 0 contato novo, veredito `JA_IDENTIFICADO` (o fechamento não casa,
  porque o `sync_event` já está `SUCCESS`).
- Chave já reivindicada com o **contato ausente** (removido por fora) → replay silencioso: não
  recria e não estoura `UNIQUE` (é o que o `ON CONFLICT` garante; medido no aceite, rodada 3).
- O `input_hash` (64 hex) fica gravado em `request_payload`, junto de `antes`/`depois` da rodada —
  é dele que o `--desfazer` tira a restauração.

## 5. Guardrails

- **Guarda de escrita** (`validar_sql`): só as 5 tabelas declaradas (`contacts`,
  `organizations`, `agent_runs`, `sync_events`, `human_approvals`), `organizations` **sem modo**
  (nenhuma escrita), `contacts` só nas colunas declaradas do DDL congelado, `DELETE` de contato
  só no desfazer, e o `SET` do enriquecimento só na forma que não sobrescreve.
- **DDL congelado**: a guarda lê a migration de verdade (`db/migrations/0001_...sql`); `CREATE`
  /`ALTER`/`DROP` são recusados, e literal de texto (nome de pessoa) não é lido como SQL.
- **Ambiente**: `prod` recusado (ADR-005, exit 4) e ambiente não declarado idem.
- **JEV fail-closed**: qualquer chamada de LLM exige recibo `PASS` do JEV; sem recibo,
  `ReciboJEVInvalido` e nada é executado. A v1 não usa LLM (custo zero por pedido).
- **LGPD**: base legal obrigatória, opt-out do titular é dado de terceiro que o agente **lê e
  preserva** — jamais escreve.

## 6. Operação

```bash
# planejar (sem banco, sem conexão)
python3 hermes/agents/contact_research/contact_research.py --planejar --fonte <arquivo.jsonl>

# rodada no dev
python3 hermes/agents/contact_research/contact_research.py --ambiente dev \
    --correlation-id <uuid> --fonte <arquivo.jsonl> \
    --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"

# desfazer uma rodada (dry-run por padrão)
python3 hermes/agents/contact_research/contact_research.py --desfazer <correlation_id>
python3 hermes/agents/contact_research/contact_research.py --desfazer <correlation_id> --confirmo
```

`--prefixo` é o comando do `psql` já apontado para o banco (o agente não tem credencial
nenhuma): no dev da VPS é o `docker exec` do container. O agente nunca abre conexão própria.

## 7. ACCEPTANCE (critérios de aceitação)

- **A1** modo `--planejar` roda sem abrir conexão de banco: 0 escrita, exit 0.
- **A2** contato novo grava `contacts` com `id` UUID v4, `organization_id` da empresa resolvida,
  colunas declaradas, `created_at`/`updated_at`, e a rodada em `sync_events`
  (`operation='CONTACT_RESEARCH'`, `status='SUCCESS'`, `request_payload` com `idempotency_key`,
  `input_hash` de 64 hex, `contato_id`, `organization_id`, `acao`, `antes`, `depois`).
- **A3** coluna **já preenchida** permanece com o valor anterior (medido antes/depois); coluna
  vazia recebe o achado — e a identidade (`email`) **não** é reescrita.
- **A4** a empresa é resolvida em `organizations`; identidade que não casa é `RECUSADA` com
  `ORGANIZACAO_NAO_ENCONTRADA` e **nenhum** contato é criado (o agente não cria empresa).
- **A5** identidade casando com duas ou mais empresas distintas → `REVISAO_IDENTIDADE` com
  `human_approvals` `PENDING` (`CONTACT_IDENTITY_REVIEW`) e evidência; se a escrita da fila humana
  falhar, o veredito é `ERRO` (fail-closed).
- **A6** pedido inválido (sem identificador forte, forte declarado e inválido, sem e-mail, e-mail
  fora de formato/limite, sem base legal, sem fonte) é `RECUSADA` com motivo; nada de contato.
- **A7** reprocessar a **mesma** entrada cria 0 contato novo (medido por contagem) e o veredito é
  `JA_IDENTIFICADO`; a chave reivindicada com o contato ausente não vira erro.
- **A8** `email` (identidade), `odoo_partner_id`, `do_not_contact`, `opt_out_email`,
  `opt_out_whatsapp` e os scores **nunca** são escritos; `decision_role` fora do vocabulário e
  campo fora do DDL viram descarte auditado.
- **A9** auditoria: uma linha de `agent_runs` por pedido (`agent_name=contact_research`,
  `agent_role=contact_research`, `correlation_id` do lote, `output` com veredito, descartes e
  `evento_de_espelho`); sem a linha de auditoria a execução é `ERRO`.
- **A10** `--ambiente prod` é recusado (exit 4) e nada é escrito; ambiente não declarado idem.
- **A11** nenhuma escrita fora das 4 tabelas declaradas; **não existe** `content_json` de
  contato no `sync_events` (o contrato de sync não tem essa coluna); nenhum `INSERT`/`UPDATE`
  no dado do Odoo; nenhuma chamada de LLM sem recibo do JEV; nenhum cliente HTTP.
- **A12** `--desfazer <correlation_id>` é dry-run por padrão; com `--confirmo` **restaura** as
  colunas que a rodada enriqueceu (valor anterior gravado em `antes`), apaga os contatos que a
  rodada **criou** e os `sync_events` da rodada, e registra um `sync_events` de `ROLLBACK` — sem
  tocar `agent_runs`, `human_approvals`, `organizations` nem o contato pré-existente.
  Rodada com contato **já espelhado no CRM** (`odoo_partner_id` preenchido) é **recusada**.
- **A13** artefatos versionados e cobertos por `scripts/verificar_estrutura.sh`; a suíte passa com
  autoteste por mutação.

## 8. TEST

- **Offline (contrato e regra):** `python3 scripts/agentes/verificar_agente_contact_research.py
  --autoteste` — **60 itens**: contrato do agente espelhado no código, fortes de identidade e
  vocabulário de `decision_role` idênticos aos do Data Contract V1.0 (e o evento conferido contra
  `events.pg_to_odoo`), colunas de enriquecimento contra o DDL congelado, colunas proibidas fora
  das listas, `INSERT` de `contacts` conferido contra o DDL, normalização/limite de e-mail,
  validação e descarte de contato, decisão de veredito, guarda de escrita (DDL, tabela não
  declarada, `organizations` sem modo, coluna proibida, sem `COALESCE`, `DELETE` de contato,
  literal que não é código), gate do JEV fail-closed, ambiente, a guarda da **própria prova**
  (item esperado inexistente ou mutação sem item declarado **reprova**) e o fluxo completo numa
  **porta de roteiro** (criar, não sobrescrever, replay, revisão, recusa, auditoria que falha,
  desfazer dry-run/confirmado/recusado). O autoteste muta **cópia** do arquivo sob teste
  (`--codigo`) e exige que o item correspondente **reprove** — hoje 25/25.
- **E2E (banco real, descartável):** `bash scripts/agentes/teste_contact_research_aceite.sh` na
  VPS, em container PostgreSQL descartável (`pg-contact-acc`) com a migration 0001, **3 empresas
  pré-existentes** e **1 contato pré-existente** com e-mail em CAIXA ALTA, cargo curado e
  `do_not_contact`/`opt_out_email` ligados — **65 itens**, em 3 rodadas mais as guardas e o
  desfazer, com veredito `ACEITE_CONTACT_RESEARCH_001_OK` (contagens antes/depois, casamento sem
  diferença de caixa, não sobrescrita do cargo curado, opt-out preservado, descartes medidos por
  SQL, retry sem duplicata, replay com a chave já reivindicada, `prod` recusado sem escrita,
  `--planejar` sem porta e desfazer dry-run/`--confirmo`/recusado-por-espelho). O
  `--prova-de-dente` muta a cópia do agente (**4 mutações**) e exige, para **cada uma**, que o
  aceite reprove **o item esperado**, com baseline verde antes.
- **Evidência no `docs/operations/registro-de-execucoes.md`** com comando e saída reais.

## 9. ROLLBACK

- **Código:** a branch do card é **aditiva** (arquivos novos + bloco novo no gate de estrutura,
  CHANGELOG e registro). Reverter a branch devolve o estado anterior.
- **Dados (dev/homolog):** `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo` aplica)
  restaura as colunas enriquecidas com os valores anteriores gravados no `sync_events` da rodada,
  apaga os contatos **criados** pela rodada e os `sync_events` da rodada, e registra um
  `sync_events` de `ROLLBACK`. O contato pré-existente **não** é apagado (nem o eu cargo curado,
  nem o opt-out), e `agent_runs`/`human_approvals` não são tocados.
- **Limite declarado:** rodada cujo contato criado **já saiu daqui** (espelhado no CRM) é
  **recusada** — apagar no PostgreSQL um contato que existe no CRM deixaria as duas pontas
  discordando, e quem resolve isso é o operador, não o agente.
- **Limite declarado 2:** coluna enriquecida cujo contato foi removido por fora não é restaurada
  (não há onde escrever); o `sync_event` da rodada continua visível para investigação.
- **Schema:** **nenhuma migration nova** — rollback não exige restore de banco.

## 10. RISK

- **R1 (alto) — sobrescrever dado curado por humano.** Mitigado **no SQL**:
  `COALESCE(NULLIF(col,''), valor)`, com a guarda exigindo esse formato; `SET` por atribuição
  direta é recusado — medido pelo dente.
- **R2 (alto) — escrever identidade ou opt-out do titular.** Mitigado por desenho: identidade é
  chave de casamento (nunca `SET`/`INSERT` de update), e as quatro colunas de opt-out/estado de
  e-mail estão na lista proibida da guarda. Medido no aceite (flags do titular intactas) e por
  mutação (dente).
- **R3 (médio) — contato duplicado por diferença de caixa/espaço.** Mitigado pelo casamento em
  `lower(email)` dos dois lados e pela normalização na entrada; medido no aceite (1 linha para o
  e-mail semeado em CAIXA ALTA).
- **R4 (médio) — fonte entrega dado sujo ou inventado.** Achado inválido é descartado **com
  motivo** e o descarte fica auditado em `output.descartados`; campo não declarado vira descarte,
  nunca coluna.
- **R5 (médio) — LGPD.** Base legal obrigatória por pedido; o agente não apaga nem altera opt-out
  (só lê e preserva) e não inventa dado de contato: confiança ausente fica `NULL`.
- **R6 (baixo) — custo/volume.** Sem LLM na v1: custo zero por pedido; a rodada é limitada ao que
  a fonte entrega.

## 11. Decisões de implementação

- **D1** base da branch no head aprovado do card irmão (`feature/TRE-W4-E02-T01`), o Research.
- **D2** a **identidade do contato é o e-mail**: o casamento normaliza os dois lados e o e-mail
  nunca é reescrito.
- **D3** uma linha de `agent_runs` por pedido, com `correlation_id` compartilhado do lote.
- **D4** v1 não escreve em produção: `prod` recusado por desenho.
- **D5** a lista de identidade forte e o vocabulário de `decision_role` vêm do contrato a cada
  execução; a suíte reprova se divergirem do Data Contract V1.0.
- **D6** o evento `DECISION_MAKER_FOUND` **não** é emitido na v1: a elegibilidade é medida e
  registrada como evidência; a emissão é card próprio (lacuna §12).

## 12. Lacunas declaradas

- **Elegibilidade medida, evento não emitido.** O evento `DECISION_MAKER_FOUND` existe no
  contrato de dados (`events.pg_to_odoo`), mas o consumidor versionado
  (`n8n/contracts/outbox-consumer.v1.json`) só cobre `RESEARCH_COMPLETED`/`LEAD_QUALIFIED`.
  Emitir sem consumidor reprovaria o `scripts/verificar_estrutura.sh` (gate de contrato) — então
  a v1 grava `evento_de_espelho {elegivel, emitido: false, o_que_faltaria}` em `output` e o gate
  do outbox fica para o card do consumidor.
- **Sem verificação paga/Exa na v1.** A fonte é o JSONL (o operador/integração entrega), como no
  Research.
- **Sem dedupe contra o CRM (Odoo).** O `odoo_partner_id` do contato existente é lido e nunca
  escrito; casar contato com `res.partner` é do card de espelho.
- **Uma empresa por pedido.** O lote é a fonte; não há agrupamento nem priorização na v1.

## 13. Referências

- Data Contract V1.0: `docs/data/data_contract_v1.json` (`vocabularies.decision_role`,
  `vocabularies.legal_basis`, `dedup.strong`, `events.pg_to_odoo`).
- DDL congelado: `db/migrations/0001_sales_intelligence_v1.sql` (`contacts`, `sync_events`,
  `agent_runs`, `human_approvals`).
- Posição no pipeline: `docs/architecture/agente-research-v1.md` (empresa) e
  `docs/runbooks/agente-contact-research.md` (operação).
- Contrato de saída/consumidor: `n8n/contracts/outbox-consumer.v1.json` (§12).
