# Agente Scout v1 — contrato da descoberta (TRE-W4-E01-T01)

**Card:** `TRE-W4-E01-T01` (W4 · E01 · P1) · **Onda:** W4 — Hermes Sales Intelligence
**Versão do agente:** `scout/1.0.0` · **Papel:** `discovery` (Hermes Sales AI, doc 02 §2)
**Artefato legível por máquina:** `hermes/agents/scout/agente-scout-v1.json`
**Implementação:** `hermes/agents/scout/scout.py` · **Suíte:** `scripts/agentes/verificar_agente_scout.py`
**Aceite E2E:** `scripts/agentes/teste_scout_aceite.sh` · **Runbook:** `docs/runbooks/agente-scout.md`

## 1. Por que este agente existe

O funil do doc 03 §2 começa em **Descoberto**. Antes deste card, o PostgreSQL tinha a tabela
`organizations` e a regra de deduplicação, mas **nenhum produtor** de organização: nada criava a
empresa candidata, e a W4 inteira (research, signals, hipótese de dor, contatos) não tinha de onde
partir. O Scout é esse produtor — e só isso: ele **cria a empresa candidata** a partir de uma fonte
declarada, com identidade resolvida por identificador forte e retry idempotente.

O fluxo do doc 06 §2 ("empresa descoberta") tem o Scout como primeiro ator: `Fonte → Hermes Sales AI`.
O `COMPANY_QUALIFIED` que aparece mais adiante nesse fluxo **não** nasce aqui: ele pressupõe score, e
score é W5. Essa é a razão de o Scout não emitir evento de outbox (ver §7).

## 2. Entrada — a empresa candidata

Arquivo JSONL, uma candidata por linha (`hermes/agents/scout/exemplos/candidatas-exemplo.jsonl`):

```json
{"legal_name":"Metalurgica Vale Forte Ltda","trade_name":"Vale Forte","cnpj":"11.222.333/0001-81",
 "domain":"valeforte.com.br","industry_name":"Metalurgia","employee_count":420,
 "city":"Sao Bernardo do Campo","state":"SP","source":"LINKEDIN",
 "evidence":{"url":"https://www.linkedin.com/company/vale-forte"}}
```

- **Nome:** `legal_name` ou `trade_name` (pelo menos um).
- **Fonte (`source`):** vocabulário fechado do agente — `LINKEDIN`, `WEB`, `GOOGLE`, `META`,
  `WHATSAPP`, `TITAN`, `EVENTOS`, `DADOS_PUBLICOS` (derivado das fontes do doc 02 §1). Fonte fora
  dessa lista é **recusa**, não aviso: `organizations.source` não vira texto livre.
- **Evidência (`evidence`):** o que a fonte mostrou (URL, trecho, arquivo de origem). Vai inteira para
  `sync_events.request_payload` — a regra "conservar evidência" do `compliance` do contrato.
- O que o Scout não sabe fica **NULL**: ele não inventa indústria, faixa ou receita. `data_quality_score`
  é da W5.

## 3. Identidade — a regra que decide criar, não criar ou perguntar

Prioridade dos identificadores **fortes** lida do Data Contract V1.0 a cada execução
(`dedup.strong`: `cnpj → domain → linkedin_url`) — não há lista paralela no código.

| Situação | Veredito | Escrita |
|---|---|---|
| Forte válido, nenhuma organização casa | `CRIADA` | `organizations` + `sync_events` + `agent_runs` |
| Forte válido, casa com **uma** organização | `JA_EXISTE` | só `agent_runs` (nenhuma linha nova) |
| Fortes válidos casando com **duas ou mais** organizações distintas | `REVISAO_IDENTIDADE` | `human_approvals` (`PENDING`) + `agent_runs` |
| **Nenhum** identificador forte declarado | `REVISAO_IDENTIDADE` | `human_approvals` (`PENDING`) + `agent_runs` |
| Declarou forte e **nenhum é válido** (ex.: CNPJ com DV errado) | `RECUSADA` | só `agent_runs` |
| Sem nome, sem fonte ou fonte fora do vocabulário | `RECUSADA` | só `agent_runs` |
| Falha de banco/porta | `ERRO` | só `agent_runs` (`status = FAILED`) |

Regras que sustentam a tabela:

- **Normalização antes de comparar:** CNPJ só dígitos (com dígito verificador módulo 11), domínio sem
  esquema/`www`/caminho/porta em minúsculas, LinkedIn canônico
  (`https://www.linkedin.com/company/<slug>`; perfil pessoal `/in/` **não** é identificador de empresa).
- **Forte inválido é descartado** — não entra no banco nem na chave de idempotência. Se sobrar algum
  forte válido, a candidata segue com ele; se não sobrar nenhum, é recusa.
- **Ambiguidade é reportada, nunca resolvida por heurística** (`dedup.rule` do contrato). Por isso
  dois casamentos distintos param na fila humana em vez de escolher um.
- **O Scout não mescla.** A decisão de *merge* de duplicidade continua no módulo do `TRE-W1-E04-T01`
  (`entity_match_confidence`, limiar `auto_merge_threshold` = 0,95 lido do contrato). Aqui só se
  resolve **identidade na ingestão**: mesma entidade não entra duas vezes.
- Identificador **fraco** (nome + cidade) não cria e não casa: um nome nunca é prova de identidade.

## 4. Idempotência — retry não cria duplicata (doc 06 §7)

A chave é a **identidade**, não a rodada: `scout:org:<tipo>:<valor>` (ex.: `scout:org:cnpj:11222333000181`),
gravada em `sync_events.idempotency_key` (**UNIQUE** no contrato). Cada candidata é ingerida por **uma
transação com duas instruções**:

```sql
BEGIN;
WITH claim AS (INSERT INTO ...sync_events (...) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id)
INSERT INTO ...organizations (...) SELECT ... FROM claim ON CONFLICT (id) DO NOTHING RETURNING id;
UPDATE ...sync_events SET status='SUCCESS', response_payload=...
 WHERE idempotency_key = '<chave>' AND EXISTS (SELECT 1 FROM ...organizations WHERE id = '<orgid>')
RETURNING 'SCOUT_CRIADA';
COMMIT;
```

- Replay (a chave já existe): `claim` volta vazia, a organização **não é inserida** e o `UPDATE` fecha
  **0 linhas** — sem a marca `SCOUT_CRIADA`. Nada foi duplicado; o veredito vira `JA_EXISTE` com o
  motivo `IDEMPOTENCIA_REPLAY`.
- **Por que duas instruções** (e não uma, com o fechamento dentro da CTE): as CTEs de escrita e a
  instrução principal rodam no **mesmo snapshot**, então a instrução principal **não enxerga** a linha
  que a CTE acabou de inserir. Medido no aceite E2E: com `UPDATE ... WHERE id = (SELECT id FROM claim)`
  o `UPDATE` fecha 0 linhas e o evento fica `PENDING` para sempre. O fechamento tem de ser um comando
  próprio (snapshot novo) — e ele se ancora na existência da organização **desta** rodada, não na chave,
  para que o replay não marque sucesso. A suíte reprova se o fechamento voltar para dentro da CTE.
- A história de **cada tentativa** fica em `agent_runs` (uma linha por candidata, com o
  `correlation_id` do lote) — auditoria não depende da narrativa de quem rodou.
- UUID v4 é gerado **no produtor** (o agente), antes de qualquer escrita (`canonical_ids.generation_rule`).

## 5. Guardrails

- **Ambiente (ADR-005):** `--ambiente dev|homolog`. `prod` é **recusado** nesta versão (exit 4) — a
  promoção é card próprio, com aprovação humana registrada; ambiente não declarado ou desconhecido
  também é recusado (fail-closed). O modo `--planejar` não abre conexão nenhuma e por isso não exige
  ambiente (se declarado, é conferido).
- **Escrita declarada:** apenas `organizations` (INSERT), `agent_runs` (INSERT), `sync_events`
  (INSERT/UPDATE de status) e `human_approvals` (INSERT). A guarda `validar_sql` roda em **toda**
  instrução antes de sair: recusa DDL, recusa tabela não declarada, recusa `UPDATE`/`DELETE` em
  `organizations` — a única remoção possível é o `--desfazer --confirmo`, explícito no chamador.
- **Natureza da ação (doc 02 §4):** descoberta é **L0** (pesquisar/enriquecer/classificar) — não exige
  aprovação humana. As ações **L1** (primeiro e-mail, LinkedIn, WhatsApp, proposta) não são executadas
  por este agente: sem SMTP, sem IMAP, sem cliente HTTP no código (a suíte reprova se aparecerem).
- **JEV (doc 07 §7, W4):** "antes de chamadas de LLM em tarefas elegíveis, o Hermes consulta o JEV".
  `chamar_llm()` exige um recibo com `decision_id`, `lane` do vocabulário e `outcome = PASS` do
  roteador (`hermes/jev/routing/router.py`); sem recibo, ou com `ESCALATE`/`BLOCK`/lane inválida,
  **levanta e o candidato não é escrito** (fail-closed). Na v1 o caminho de LLM não é exercido: a
  descoberta é determinística (§9).

## 6. Operação

O banco vive na VPS do ambiente (**ADR-0008**): o SQL sai por `docker exec … psql`. O agente recebe o
**prefixo psql** do ambiente e registra, no relatório, a identidade do alvo medido
(`current_database()`/`current_user`) — sem afirmar nome de ambiente, porque a convenção de nome
divergiu no dev e alinhar isso é decisão do dono, não do agente.

```bash
# planejar (sem banco)
python3 hermes/agents/scout/scout.py --planejar --fonte candidatas.jsonl

# ingerir no dev
python3 hermes/agents/scout/scout.py --ambiente dev --fonte candidatas.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/scout-rodada.json

# desfazer uma rodada (dry-run por padrão)
python3 hermes/agents/scout/scout.py --desfazer <correlation_id>
```

## 7. ACCEPTANCE (critérios de aceitação)

- **A1** modo `--planejar` roda sem abrir conexão de banco: 0 escrita, exit 0.
- **A2** ingestão cria `organizations` com `status='DISCOVERED'`, `source` do vocabulário, UUID v4 do
  produtor e carimbos `created_at`/`updated_at`.
- **A3** rodar a **mesma fonte duas vezes** cria 0 organização nova (medido por contagem antes/depois),
  com `sync_events.idempotency_key` (UNIQUE) como garantia de banco.
- **A4** candidata que casa por forte com organização existente **não** é criada; veredito `JA_EXISTE`
  com o id casado.
- **A5** sem forte válido, ou com fortes conflitantes, **não** cria nem mescla: `human_approvals`
  `PENDING` (`SCOUT_IDENTITY_REVIEW`) com evidência.
- **A6** candidata inválida (sem nome, ou forte declarado e nenhum válido) é `RECUSADA` com motivo.
- **A7** auditoria: uma linha de `agent_runs` por candidata (`agent_name=scout`,
  `agent_role=discovery`, `correlation_id` do lote, `output` com o veredito) e `sync_events` com
  operação, chave, payload de evidência e status.
- **A8** `--ambiente prod` é recusado (exit 4) e nada é escrito; ambiente não declarado idem.
- **A9** nenhuma chamada de LLM sem recibo válido do JEV (fail-closed).
- **A10** nenhuma escrita fora das 4 tabelas declaradas; nenhum UPDATE/DELETE em `organizations` fora
  do `--desfazer --confirmo`; nenhum acesso a Odoo/Titan/n8n.
- **A11** artefatos versionados e cobertos por `scripts/verificar_estrutura.sh`; a suíte passa com
  autoteste por mutação.

## 8. TEST

- **Offline (contrato e regra):**
  `python3 scripts/agentes/verificar_agente_scout.py --autoteste` — **54 itens**: espelho do contrato com
  o Data Contract V1.0, colunas do INSERT contra o DDL congelado (e a **contagem** de colunas × valores),
  normalização/validação, decisão de identidade, guarda de escrita, gate do JEV e o fluxo completo numa
  porta de roteiro. O autoteste muta **cópia** do agente (**8 mutações**) e exige que o item
  correspondente **reprove** — hoje 8/8.
- **E2E (banco real, descartável):** `bash scripts/agentes/teste_scout_aceite.sh` na VPS, em container
  PostgreSQL descartável (`pg-scout-acc`) com a migration 0001 — **35 itens** medidos por contagem
  antes/depois em 3 rodadas mais as guardas: criação (3), retry sem duplicata, **replay com a chave já
  reivindicada e a organização ausente** — é este item que prova o `ON CONFLICT (idempotency_key)`;
  `prod` recusado sem escrita, `--planejar` sem porta, dry-run do desfazer e desfazer com `--confirmo`
  preservando base e auditoria. O `--prova-de-dente` muta a cópia do agente e exige que o aceite
  **REPROVE** (baseline verde antes e depois): **3/3**.
- **Defeitos que só o E2E pegou** (cada um virou item offline): colunas × valores fora de sincronia no
  INSERT; veredito de criação ignorando os problemas de validação (nome/fonte); carimbos
  `BEGIN`/`COMMIT` da porta lidos como "não ingerido"; e o fechamento do `sync_events` dentro da CTE —
  mesmo snapshot, o evento ficava `PENDING` para sempre.
- Evidência no `docs/operations/registro-de-execucoes.md` com comando e saída reais.

## 9. ROLLBACK

- **Código:** a branch do card é **aditiva** (arquivos novos + blocos novos no gate de estrutura,
  CHANGELOG e registro). Reverter a branch devolve o estado anterior; nenhum comportamento existente
  muda. O caminho é `git revert` dos commits do card (ou descartar `feature/TRE-W4-E01-T01`).
- **Dados (dev/homolog):** o agente **não faz UPDATE nem DELETE** em `organizations`. Desfazer uma
  rodada é apagar o que **ela** criou: `--desfazer <correlation_id>` (dry-run por padrão; `--confirmo`
  aplica), escopado por `agent_runs.correlation_id` + veredito `CRIADA` + `status='DISCOVERED'`,
  removendo também os `sync_events` dessas identidades e registrando um `sync_events` de `ROLLBACK`.
  Organização pré-existente, `agent_runs` e `human_approvals` **não** são tocados.
- **Schema:** **nenhuma migration nova** — rollback não exige restore de banco.

## 10. RISK

- **R1 (alto) — duplicidade por identificador fraco.** Mitigado por desenho: só cria com forte válido;
  nome sozinho nunca cria; `sync_events` UNIQUE fecha o retry; o merge automático continua com o
  módulo do E04-T01.
- **R2 (médio) — fonte entrega dado sujo.** Vira `RECUSADA`/`REVISAO_IDENTIDADE` com motivo — nunca
  merge silencioso, nunca campo inventado.
- **R3 (médio) — forte válido pertencente a outra entidade.** Forte casado = mesma entidade; **conflito
  de dois fortes distintos** para a fila humana. Um CNPJ errado *válido* (de terceiro) ainda pode
  casar: é o limite conhecido de identidade por forte, e o corretivo é a revisão humana, não um chute.
- **R4 (baixo) — custo/volume.** Sem LLM na v1: custo zero por candidata; a rodada é limitada ao que a
  fonte entrega (sem paginação/crawler).

## 11. Decisões de implementação

- **D1** base da branch no head aprovado do card pai (`feature/TRE-W3-E06-T01` @ `bf31cb1`).
- **D2** o Scout não mescla (merge é do E04-T01); só resolve identidade na ingestão.
- **D3** uma linha de `agent_runs` por candidata, com `correlation_id` compartilhado do lote.
- **D4** v1 não escreve em produção: `prod` recusado por desenho, promoção é card próprio.
- **D5** a identidade forte vem do contrato a cada execução; a suíte reprova se a lista do agente
  divergir de `dedup.strong`.

## 12. Lacunas declaradas

1. **Sem LLM e sem crawler:** a fonte entrega as candidatas; busca ativa (web/LinkedIn) não está
   implementada. O gate do JEV existe e é fail-closed, mas não há caminho de LLM na v1.
2. **Sem enriquecimento e sem score:** `data_quality_score` fica NULL (Data Quality Score é W5);
   nenhum evento de outbox nasce na descoberta (`COMPANY_QUALIFIED` pressupõe score).
3. **Sem contato:** `contacts`, sinais e hipóteses de dor são W4-E03/E04/E05.
4. **`retention_until`/`collected_at` não existem** no contrato V1 (lacuna já declarada no
   `compliance` do contrato; não inventamos coluna).
5. **Candidatas que só compartilham identificador fraco** entre si não são resolvidas aqui: vão para
   revisão humana (é a regra do contrato, não uma simplificação).
6. **Sem paginação/limite de volume:** o baseline não define limite por rodada.

## 13. Referências

- Baseline V1.1.0: docs 02 §2/§4, 03 §2, 06 §2/§7, 07 §7, 11 §2, 12 (contratos e governança).
- `docs/data/DATA_CONTRACT_V1.md` e `docs/data/data_contract_v1.json` (dedup, vocabulários, eventos,
  IDs canônicos).
- `ADR-0005` (nada nasce em produção), `ADR-0007` (ambientes), `ADR-0008` (acesso ao PostgreSQL).
- `docs/runbooks/agente-scout.md` · `docs/architecture/aprovacao-humana-com-executor.md`.
