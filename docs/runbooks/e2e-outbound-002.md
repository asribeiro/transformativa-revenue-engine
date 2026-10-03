# E2E Outbound #002 — o caminho outbound ponta a ponta (Titan)

Runbook do card **TRE-W6-E07-T01**. O que e' este aceite, como se roda, o que cada item significa e
**o que ele NAO mede** (lacuna declarada — nao se inventa medicao que mente).

Aceite: `scripts/e2e/verificar-e2e-outbound-002.sh`.
Cenario de origem: `Comercial Transformativa/08_PLANO_DE_TESTES_E_VALIDACAO.md` §4 (10 passos).

## 1. O que este aceite e' (e o que nao e')

**E'** o encadeamento dos cards da onda W6 **num unico trio descartavel**, com as pecas reais de cada
card (nao dubles): um PostgreSQL, um **sink SMTP** local, um **sink IMAP** local e um **stub da API
controlada do Odoo** — todos em `127.0.0.1`. Ele responde a pergunta que nenhum aceite de card
responde sozinho: *a junta fecha?* Cada aceite individual pode estar verde e a cadeia quebrar entre
um e o proximo (pedido aprovado que o envio nao aceita, resposta que o CRM nao acha, evidencia nova
que nao gera recomendacao nova).

**Os 10 passos medidos** (doc 08 §4):

| # | passo | quem entrega | item |
|---|-------|--------------|------|
| 1 | lead A+ elegivel | massa do aceite + registro TIER (W5) | 1.1, 1.2 |
| 2 | Hermes gera Next Best Action | `hermes/scores/nba/nba.py` (W5-E07) | 2.1..2.5 |
| 3 | GPT cria draft | `outreach_generator.py` (W6-E02) | 3.1..3.4 |
| 4 | Human Approval | `approval_workflow.py` (W6-E03) | 4.1..4.6 |
| 5 | Titan envia | `send_workflow.py` (W6-E04) + sink SMTP | 5.0..5.7 |
| 6 | interaction registrada | `interactions` + `sync_events` (W6-E04) | 6.1..6.3 |
| 7 | reply recebido | `ingestao_respostas.py` (W6-E05) + sink IMAP | 7.1..7.5 |
| 8 | GPT classifica | contrato `ingestao-respostas-v1` (W6-E05) | 8.1..8.4 |
| 9 | Odoo atualizado | `atualizacao_odoo.py` (W6-E06) + stub da API | 9.0..9.7 |
| 10 | nova NBA criada | NBA de novo, com evidencia nova | 10.1..10.4 |

Alem dos passos, o aceite mede: guardas de ambiente (11.1, `prod` recusa nos 4 componentes), escopo de
escrita (11.2, nenhuma tabela nova) e vazamento de segredo (11.3).

**NAO e'** o E2E contra as pontas REAIS de terceiros. O que este aceite nao mede esta declarado no §5
— e o que nao esta medido nao vira "OK".

## 2. Como se roda

Roda **na VPS do ambiente** (ADR-0008: e' la' que vive o Docker do TRE), do repositorio em disco:

```bash
# o aceite (trio descartavel, ~1 min)
bash scripts/e2e/verificar-e2e-outbound-002.sh

# com a prova de dente (3 sub-runs, cada um com um componente mutado)
bash scripts/e2e/verificar-e2e-outbound-002.sh --prova-de-dente

# deixar o trio de pe para investigar (nao faz limpeza no fim)
bash scripts/e2e/verificar-e2e-outbound-002.sh --manter

# injecao de modulo mutado para UM sub-run (usado pelo proprio dente)
bash scripts/e2e/verificar-e2e-outbound-002.sh --sub-run --envio /tmp/mut_send.py
```

**Requer**: docker com a imagem `postgres:16`, `python3` e `openssl`; o checkout do repositorio.
Na VPS ja' existe tudo isso.

**Variaveis** (todas com default seguro): `TRE_RAIZ`, `TRE_FIXTURE_IMAGEM`, `TRE_E2E002_CONTAINER`
(default `pg-resp-e2e002`, ver §4), `TRE_E2E002_TRABALHO`, `TRE_E2E002_DOMINIO_DEV`.

**Guardas de execucao** (medidas, nao presumidas): o aceite ABORTA se o container do trio ja' existir
(nao toca em `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev`, `proxy-dev`) e ABORTA se uma das 3 portas
locais (`2465` SMTP, `2993` IMAP, `8799` API) estiver ocupada — porta ocupada e' sobra de rodada
anterior, e medir contra o stub de outra rodada da' falso negativo (foi um defeito medido aqui).

## 3. O que cada bloco de item garante

- **1.x** — o lead tem pesquisa concluida, dor, sinal e **decisor contactavel** (sem
  `do_not_contact`/`opt_out_email`), alem do registro TIER `A+` do W5. Sem isso o NBA nem escolheria
  `SEND_EMAIL`.
- **2.x** — a recomendacao nasce do componente W5 com a **acao do contrato** (`SEND_EMAIL`), status
  `OPEN`, contato escolhido e `rationale` citando o tier lido; a auditoria da rodada vai para
  `agent_runs` sem LLM.
- **3.x** — o pedido de aprovacao nasce do gerador irmao (nada de pedido fabricado a mao), cita a
  **recomendacao OPEN** (`proposed_action.recommendation_id`) e traz assunto/corpo/CTA.
- **4.x** — a fila **notifica uma vez** (segunda rodada = `JA_NOTIFICADO`, zero novos), a decisao
  grava `APPROVED` com **operador humano canonico**, o texto aprovado carrega hash e o portao
  `--consultar` libera (`pode_enviar: true`).
- **5.x** — dry-run e' `PLANO` e **nao entrega**; o envio `--confirmo` entrega **uma** mensagem, sob
  TLS, ao **contato do pedido** (nunca destino de linha de comando), com AUTH sem senha, assunto e
  corpo iguais ao **texto aprovado + CTA** lido do banco.
- **6.x** — a `interactions` registra o fato com `content_reference = envio:<pedido>:<texto_hash>`, e
  `sync_events` fica `ENVIADO` ligado a interaction, com o primitivo na trilha.
- **7.x** — a resposta do lead entra por um sink IMAP e o invariante de leitura do card pai e'
  re-medido de ponta (todas as selecoes `EXAMINE`, `total_buscas_sem_peek=0`,
  `total_comandos_de_escrita=[]`, nenhuma mensagem marcada `\Seen`).
- **8.x** — a classificacao mede **categoria/intent/sentiment** no banco, o vinculo ao lead vem do
  remetente (nao se inventa organizacao) e o replay da mesma chave e' `JA_INGERIDO` sem linha nova.
- **9.x** — o CRM so' e' tocado pela **API controlada** (stub com o mesmo envelope): dev + bearer +
  `idempotency_key` em toda escrita, `RESPOSTA_INTERESSE` + `RESPONDER_AGORA`, atividade no contato do
  lead e trilha `ATUALIZADO` no banco canonico. A chave da API nao aparece na saida.
- **10.x** — a resposta positiva e' **evidencia nova**: o NBA gera `CREATE_MEETING`, a anterior vira
  `SUPERSEDED` e o historico e' preservado (ids distintos, nao reescrita).

## 4. Decisoes do aceite (e por que)

- **Trio unico.** Um container por proposta mediria cada porta isolada — que e' o que os aceites de
  card ja' fizeram. Aqui a prova e' a junta.
- **Nome `pg-resp-e2e002`.** Nao e' capricho: as guardas de dev de W6-E05/W6-E06 so' aceitam porta de
  banco em container `pg-(sales|odoo|resp|respostas|e06|aceite)...`; `pg-resp-...` e' a forma que casa
  nas duas. A regra e' dos modulos e **nao** foi afrouxada por este aceite.
- **`TRE_E2E002_DOMINIO_DEV=cliente-demo.test`.** O dominio de dev e' o unico endereco aceito no
  envio em dev; usar o MESMO dominio no remetente da resposta faz o reply casar com o contato pelo
  e-mail, sem caso especial.
- **`correlation_id` e' UUID.** O banco canonico guarda `correlation_id` como `uuid`: id de mentira
  (string) derruba a auditoria de QUALQUER componente e o aceite mede isso (defeito real da rodada 1).
- **A ponte do lead e' do harness, declarada e medida.** Ver §5.

## 5. Lacunas declaradas (o que este aceite NAO mede)

1. **Ponta real do Titan (SMTP/IMAP)**: mede-se contra sinks locais com certificado proprio. A prova
   contra `smtp.titan.email` / `imap.titan.email` e' de **homolog**, com credencial do Sales AI e
   aprovacao registrada do dono — fora deste card.
2. **Odoo real**: mede-se contra o **stub** da API controlada em loopback (o dev nao tem chave de API
   do Odoo). A prova contra `odoo-dev` exige a chave do usuario de integracao.
3. **Draft por LLM**: em dev o gerador usa o **renderizador offline deterministico** (rotulado como
   `provedor=offline`): o aceite mede o CAMINHO pos-provedor (validacao, citacao, pedido de aprovacao),
   nao a qualidade de um modelo real.
4. **`interactions.odoo_lead_id` — LACUNA MEDIDA, nao escondida.** Nenhum componente da onda W6 grava
   esse campo (o envio nao grava; a ingestao de resposta tambem nao). O aceite **mede o efeito** disso
   (item 9.1: sem o vinculo, o CRM responde `SEM_VINCULO`) e so' depois aplica uma **ponte declarada**
   (item 9.2), que representa o papel do E2E #001 / fundacao W3-W4: o lead nasce no CRM e o id volta
   para a interacao. Fechar essa lacuna **nao e' deste card** — e' do caminho de fundacao/sync
   (W3/W4), que precisa gravar o `odoo_lead_id` na `interactions`.
5. **Passo 10 "nova NBA criada"** e' a rodada do NBA do W5 sobre a evidencia nova do reply. O que este
   aceite prova e' a **supersessao com acao nova**; o agendamento/entrega dessa nova acao nao existe
   na onda W6.
6. **Volume**: corpus de 1 resposta (o cenario do doc 08 §4 e' um lead). Nada aqui mede volume,
   paralelismo ou taxa de resposta real.

## 6. Evidencia

Saida completa com exit code, anexada ao card `TRE-W6-E07-T01`:

- `aceite-e2e-outbound-002-56ok.out` — **ACEITE_E2E_OUTBOUND_002_OK (56 itens, 0 falhas)**, exit 0.
- `aceite-e2e-outbound-002-dentes-3de3.out` — `--prova-de-dente` **3/3** (cada mutacao reprovou o item
  que nomeia) com o controle verde.
- `sha256-artefatos.out` — hash dos dois artefatos.

Registro de execucoes: `docs/operations/registro-de-execucoes.md` (entrada de 03/10/2026).
