# Next Best Action v1 (`nba-v1`) — card TRE-W5-E07-T01

Documento do card: o que o componente faz, o que ele **nao** faz, os critérios de aceitação, o plano
de teste, o rollback e o risco. O verificador é `scripts/scores/verificar_nba.py` (suíte offline,
com autoteste por mutação) e o aceite E2E é `scripts/scores/teste_nba_aceite.sh` (PostgreSQL
descartável, com prova de dente).

## 1. O que entra e o que sai

O componente **le a evidência que já existe** no schema `sales_intelligence` e **grava a
recomendação** — nada mais:

| Papel | Objeto |
|---|---|
| Entrada (leitura) | o **registro TIER** do card irmão `TRE-W5-E06-T01` (`sync_events`, `operation='TIER'`), `research_runs`, `pain_hypotheses`, `signals`, `contacts`, `interactions`, `scores`, `organizations` |
| Decisão | a **tabela de decisão declarada** em `hermes/scores/nba/politica-nba-v1.json` (12 regras, ordem = decisão) |
| Vocabulário | `docs/data/data_contract_v1.json#vocabularies.next_best_action` — as nove ações. **Lido do contrato a cada rodada**; nenhuma ação existe escrita no código (item C1 por AST) |
| Saída (escrita) | `sales_intelligence.recommendations` (`recommendation_type='NEXT_BEST_ACTION'`, `status='OPEN'`) + `sales_intelligence.agent_runs` (auditoria) |
| Proibido | DDL, `scores`, `organizations`, `contacts`, `signals`, `pain_hypotheses`, `research_runs`, `interactions`, `sync_events`, `outbox_events`, `human_approvals`, UPDATE em `agent_runs` |

A decisão é **determinística e sem LLM**: nenhuma chamada de modelo, nenhuma rede além do banco,
`model`/`tokens_*`/`estimated_cost` NULL na auditoria. `confidence` da recomendação fica **NULL**
declarado: a regra é determinística e um número ali fingiria calibração que não existe.

## 2. A tabela de decisão (12 regras, primeira que casa vence)

| # | Quando | Ação | Motivo |
|---|---|---|---|
| R01 | contatos bloqueados e nenhum decisor alcançável | NURTURE | `COMPLIANCE_SEM_CANAL` |
| R02 | dor rejeitada e nenhum sinal ativo | DISQUALIFY | `DOR_REJEITADA_SEM_SINAL` |
| R03 | tier `Nurture` | NURTURE | `TIER_NURTURE` |
| R04 | sem pesquisa concluída | RESEARCH_MORE | `SEM_PESQUISA` |
| R05 | respondeu com sentimento positivo | CREATE_MEETING | `RESPOSTA_POSITIVA` |
| R06 | respondeu com sentimento negativo | NURTURE | `RESPOSTA_NEGATIVA` |
| R07 | respondeu sem sinal claro | FOLLOW_UP | `RESPOSTA_SEM_SINAL_CLARO` |
| R08 | abordada há menos de 3 dias, sem resposta | WAIT | `ABORDAGEM_RECENTE` |
| R09 | abordada há 3 dias ou mais, sem resposta | FOLLOW_UP | `SEM_RESPOSTA` |
| R10 | nenhum decisor alcançável | FIND_DECISION_MAKER | `SEM_DECISOR_CONTACTAVEL` |
| R11 | decisor alcançável, sem e-mail | PREPARE_LINKEDIN | `DECISOR_SEM_EMAIL` |
| R12 | decisor alcançável com e-mail | SEND_EMAIL | `DECISOR_COM_EMAIL` |

- **Ordem é decisão**: compliance (R01) e qualificação (R02) vêm **antes** do tier — barreira de
  contato e falta de fit não são questão de prioridade. Cobertura conferida a cada rodada: toda ação
  do vocabulário do contrato tem regra (ou é declarada em `nao_alcancadas`).
- **Ausência de resposta é abstinência**: nenhuma regra casando, o veredito é `ABSTEVE` (`SEM_REGRA`)
  e **nada** é gravado — não existe ação default.
- **Sem registro TIER não existe NBA**: veredito `RECUSADA` (`SEM_TIER`), nada gravado. O próximo
  passo depende da prioridade já decidida; o componente não adivinha tier.
- `priority`, `due_at` (`due_dias`) e `expires_at` (`validade_dias`) vêm **da regra**, não do código.

## 3. Idempotência e supersessão

- O `id` da recomendação é **determinístico**: `uuid5(nba-v1, <organization_id>:<entrada_hash>)`,
  onde `entrada_hash` = sha256 canônico de organização, versão do componente, `política@versão`, regra
  vencedora, ação, identidade do registro TIER lido e o contato escolhido, mais os fatos **não
  numéricos** citados pela regra. Contadores (ex. `dias_desde_a_abordagem`) entram como o
  **resultado da comparação** — senão o mesmo estado geraria recomendação nova a cada dia e a
  idempotência seria enfeite (medido em F3).
- Relógio da rodada, `correlation_id` e o texto renderizado **não** entram no hash (medido em F4).
- Mesma entrada ⇒ **replay** (`JA_RECOMENDADA`, nada novo). Entrada nova ⇒ recomendação **nova**, e a
  anterior volta para `SUPERSEDED` (estado do doc 12 §5) — histórico preservado, nunca reescrito.
- Prova da gravação: `ja_existia` (antes) e `gravados` (na mesma transação) — `gravados=1` com
  `ja_existia=0` é gravação; `ja_existia=1` é replay.

## 4. Critérios de aceitação

| # | Critério | Como é medido |
|---|---|---|
| AC1 | a recomendação legítima é gravada em `recommendations` com a **ação do contrato**, `status='OPEN'`, `prioridade`/`due_at`/`expires_at` da regra e o contato escolhido | A1 (9 itens) |
| AC2 | a auditoria da rodada entra em `agent_runs` **sem LLM** (`model`/`tokens`/`custo` NULL) e com o id da recomendação | A1 |
| AC3 | `confidence` fica NULL (não inventa calibração) | A1 |
| AC4 | a rodada **não escreve** fora de `recommendations`/`agent_runs`: score, tier, contatos, interações, `organizations`, `outbox_events` intactos; **0** linha em `sync_events` nova | A11 |
| AC5 | replay da mesma entrada **não duplica** (id determinístico) | A2 + F1/F2/F4 |
| AC6 | evidência nova gera recomendação **nova** e **SUPERSEDE** a anterior, com o histórico preservado | A10 + dente `sem-supersessao` |
| AC7 | cada estado de evidência produz a ação esperada (R01..R12) e a **primeira regra que casa vence** | A3 (18 itens) + E1/E3 |
| AC8 | empresa **sem registro TIER** RECUSA (`SEM_TIER`) sem gravar, com a recusa **auditada** (`REJECTED`) | A6 + dente `sem-checagem-de-tier` |
| AC9 | empresa inexistente RECUSA (`ORGANIZACAO_NAO_ENCONTRADA`) sem gravar | A7 |
| AC10 | `prod` recusado com exit 4 **sem escrita**; `--planejar`/`--regras` exit 0 **sem conexão**; `--regras` cobre as 9 ações | A3 (7 itens) + G1..G4 |
| AC11 | abstenção (nenhuma regra casando) não grava e é auditada | E2 + suite (política sem regra casando) |
| AC12 | a guarda de escrita recusa DDL, escrita em outra tabela e DELETE sem `--confirmo` | D1..D5 |
| AC13 | desfazer: dry-run não apaga, `--confirmo` apaga **só** as recomendações da rodada e a auditoria fica | A9 (5 itens) |
| AC14 | política/contrato incoerente RECUSA (ação fora do vocabulário, ação do contrato sem regra, operador/fato desconhecido, regra sem campo) com exit 3 | A9/B1..B5 + dentes |
| AC15 | nenhuma ação do contrato, papel de decisão ou id de regra existe **em forma executável** no código | C1..C3 por AST |

## 5. Plano de teste

1. **Suíte offline** (sem banco): `python3 scripts/scores/verificar_nba.py --raiz "$PWD"` →
   `VERIFICACAO_NBA_OK (69 itens, 0 falhas)`; autoteste `--autoteste` → `AUTOTESTE OK (7/7 mutações,
   cada uma pelo item esperado)`.
2. **Aceite E2E** (PostgreSQL descartável, na VPS onde há Docker):
   `bash scripts/scores/teste_nba_aceite.sh --raiz "$PWD" --prova-de-dente` →
   `ACEITE_NBA_001_OK`, com os 6 dentes reprovando o item esperado.
3. **Dentes** (mutação em cópia, cada uma tem de reprovar **o item esperado**, não "o aceite"):
   `sem-supersessao`, `sem-idempotencia`, `sem-checagem-de-tier`, `primeira-regra-sempre`,
   `ordem-invertida`, `contato-bloqueado-ignorado`.

## 6. Rollback

- **Desfazer a rodada**: `--desfazer <correlation_id>` (dry-run) e `--desfazer <correlation_id>
  --confirmo` — apaga **só** as recomendações gravadas por aquela correlação. A auditoria em
  `agent_runs` **não** é apagada.
- **Desfazer o componente**: reverter o commit (nenhuma DDL, nenhuma coluna, nenhum schema novo —
  `recommendations` e `agent_runs` são do Data Contract V1.0). Recomendações já gravadas podem ser
  desfeitas por `--desfazer` ou ficam `SUPERSEDED` na próxima rodada.
- Nada é promovido a produção (ADR-005): `prod` é recusado com exit 4.

## 7. Risco

**Médio.** Não muda schema nem contrato, não fala com o Odoo, não envia nada e não executa ação
nenhuma — escreve uma recomendação auditável e reversível. O risco real está na **política**: é ela
que decide o próximo passo, e por isso a tabela é declarada, versionada, conferida a cada rodada
contra o vocabulário do contrato (fail-closed), coberta por 12 casos de decisão na suíte e por 6
dentes no aceite. Ampliar o escopo (executar a ação, calibrar confiança, escrever no Odoo) exige
card próprio e, para produção, aprovação humana registrada.

## 8. Fora do card (declarado)

Execução da ação e Human Approval (W6) · evento de outbox `NEXT_BEST_ACTION_CHANGED` e espelhamento
no Odoo (W3/W6) · aceite E2E da cadeia W5 e o teste scoring/NBA (W5-E08-T01) · varredura de
recomendações vencidas (`EXPIRED`) e o ciclo `APPROVED`/`EXECUTED` · calibração de `confidence`.

## 9. Lacunas (propostas ao dono)

1. A **tabela de decisão** (quais regras, em que ordem, com que prazos) é proposta deste card; o
   contrato define o **vocabulário** das ações, não a política de escolha.
2. `confidence` NULL — a calibração depende do feedback de resposta (W6+).
3. O vínculo resposta↔contato que respondeu depende da classificação de resposta do W6: aqui o
   sentimento vem de `interactions.sentiment` e o contato é o melhor decisor alcançável.
4. `EXPIRED`/`APPROVED`/`EXECUTED` são do workflow humano (doc 12 §4/§5); aqui gravam-se `due_at` e
   `expires_at`.
