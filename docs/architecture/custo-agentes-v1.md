# Custo de agentes v1 (`custo-agentes-v1`) — desenho

Card **TRE-W8-E05-T01** (W8 / Epic E05) · componente `hermes/agentes/analytics/custo_agentes.py` ·
contrato `hermes/agentes/analytics/custo-agentes-v1.json` · runbook `docs/runbooks/custo-de-agentes.md`.

## 1. O que a peça responde

Quanto custa cada agente, quanto custa **cada sucesso** dele, quantos tokens ele queima e como termina a
execução. A pergunta de gestão não é "quanto gastei" e sim **"quanto me custou cada execução que deu
certo"** — por isso a métrica de ponta é `custo_por_execucao_concluida`.

## 2. Fonte da verdade (e por que é só uma)

`sales_intelligence.agent_runs` — a auditoria de execução de agente, dona PostgreSQL, contrato de dados §9
(`docs/data/data_contract_v1.json#tables['agent_runs']`). Cada irmao que executa agente grava ali uma linha
(agent_name, agent_role, agent_version, workflow, organization_id, correlation_id, model, started_at,
finished_at, status, tokens_input, tokens_output, estimated_cost, error).

Este card **não** é dono de nada: ele LÊ a auditoria. Não cria tabela, não materializa métrica, não escreve
lane, não escolhe modelo.

## 3. Invariantes (cada uma com item de suíte/aceite)

| # | Invariante | Como é medido |
|---|---|---|
| 1 | **Nulo ≠ zero** | execução sem `estimated_cost` não entra na soma e não vira `0`; vai para `runs_sem_custo`, a média fica `null` e o grupo sai do ranking |
| 2 | **Não se calcula preço** | o componente não tem tarifa: reporta o `estimated_cost` GRAVADO, como string decimal de 6 casas (a política de lane proíbe fixar preço) |
| 3 | **Status fora do vocabulário não vira desfecho** | `COMPLETED`/`FAILED`/`REJECTED`/`REVIEW_REQUIRED` vêm dos mapas `STATUS_AGENT_RUNS` dos irmaos; qualquer outro valor (ex.: `TIMEOUT`) vai para lacuna |
| 4 | **Recusa declarada não é falha** | `REJECTED` conta em `recusadas`; `REVIEW_REQUIRED` em `revisao`; as duas são medidas separadas e não somam em `falhas` |
| 5 | **A coluna que não existe não se inventa** | o card LÊ a DDL congelada e RECUSA (exit 3) se uma coluna da métrica faltar no `CREATE TABLE` |
| 6 | **A saída é agregada** | `organization_id` é lido só para CONTAR distintos; `input`, `output` e `error` (JSONB) não são selecionados; o SELECT nunca é `*` |
| 7 | **Determinismo** | sem `--com-carimbo` a saída é byte a byte idêntica entre rodadas; `gerado_em` não entra no `hash_do_relatorio` |
| 8 | **Leitura pura** | só `SELECT`; a consulta roda com `default_transaction_read_only = on` em `-c` **separado** (num `-c` só, o `SET` não vale — defeito medido no card irmao W8-E01-T01) |
| 9 | **Guarda de ambiente (ADR-005)** | `prod` RECUSA (exit 4) antes de qualquer leitura; `dev` exige porta local (`docker exec -i pg-... psql`); `homolog` exige `--confirmo` |
| 10 | **Segredo** | se o valor de `TRE_CUSTO_AGENTES_TOKEN` aparecer na evidência, a rodada é recusada (exit 5) |

## 4. As três visões

- **por_agente** (chave `agent_name`): a visão de dono. `agent_version` viaja no detalhe (`versoes`), não na
  chave — uma versão nova do mesmo agente não deve fragmentar o ranking de custo.
- **por_modelo** (chave `model`): onde o dinheiro sai.
- **por_workflow** (chave `workflow`): a visão de processo.

Execução sem chave (`agent_name`/`model`/`workflow` vazio) **não** vira grupo: é contada em lacuna.

## 5. Ranking — quem pode ser coroado

`agente_mais_caro_por_execucao_concluida`. Candidato tem de ter **amostra suficiente**
(`concluidas >= limite_amostra`, default 3) **e** declarar custo em **todas** as execuções
(`runs_sem_custo == 0`). Motivo: quem declara custo em parte das execuções tem total **subdeclarado** —
compará-lo coroaria como "barato" exatamente quem menos declara. Esses ficam FORA, nomeados em
`agentes_fora_do_ranking`, com o motivo (`SEM_EXECUCOES`, `SEM_CUSTO_DECLARADO`,
`CUSTO_NAO_DECLARADO_EM_PARTE`, `AMOSTRA_INSUFICIENTE`).

## 6. Métricas por grupo

`runs`, `concluidas`, `falhas`, `recusadas`, `revisao`, `sem_status`, `classificadas`, `taxa_de_falha`,
`taxa_de_recusa`, `tokens_input/output/totais`, `runs_sem_tokens`, `custo_total`, `runs_com_custo`,
`runs_sem_custo`, `custo_medio_por_execucao`, `custo_por_execucao_concluida`, `latencia_media_s`,
`latencia_mediana_s`, `latencia_p95_s`, `runs_sem_latencia`, `organizacoes`, `amostra_suficiente`.

Custo sai como **string decimal de 6 casas** (float perderia o valor gravado). Latência usa o método do
**posto mais próximo** (declarado) — média, mediana e p95. Latência com `finished_at < started_at` é
invertida e fica FORA (vai para lacuna); execução sem os dois carimbos também.

## 7. Lacunas declaradas (medidas, não escondidas)

1. **L1 — nem todo irmão declara custo.** `research` grava `NULL` em tokens/custo; `outreach` grava a partir
   do provedor (`provedor_meta`). Enquanto for assim, o custo medido é o das execuções que **declaram** —
   nunca o custo do sistema inteiro. A lacuna é do produtor.
2. **L2 — não existe tabela de preços** no contrato V1, e a política de lane PROÍBE fixar preço. O
   `estimated_cost` é multiplicado pelo produtor, com a tarifa dele: duas execuções do mesmo modelo podem
   trazer tarifas diferentes e o card não concilia.
3. **L3 — a unidade (moeda/base) do `estimated_cost` não está declarada** no contrato de dados. O relatório
   reporta o número gravado, sem rotular moeda nem converter.
4. **L4 — `agent_runs.status` não tem CHECK na DDL**: o vocabulário vive nos mapas dos irmãos. Status novo
   gravado sem atualizar este contrato cai em lacuna (visível), mas a suíte só cobre o vocabulário medido em
   2026-10-03 (`COMPLETED`, `FAILED`, `REJECTED`, `REVIEW_REQUIRED`).
5. **L5 — a janela filtra por `started_at`** (execução sem `started_at` fica fora dela) e o recorte é por
   execução de agente: não há atribuição de custo a card do board nem a receita — o "custo por card
   VERIFIED" da política de lane não é medível aqui (o recibo do JEV não carrega custo).

## 8. Saída

`custo-agentes.json` (relatório), `custo-agentes.csv` (uma linha por agente) e `custo-agentes.html`
(dashboard auto-contido, sem recurso externo e sem servidor). A saída carrega contagem e os nomes de
agente/modelo/workflow (vocabulário do próprio sistema) — nunca UUID de organização, id de execução,
e-mail, nome de contato ou payload de entrada/saída.

## 9. Decisões de forma registradas

- **Diretório**: `hermes/agentes/analytics/` — o mesmo do card pai (W8-E01-T01, funil). O irmão W8-E04
  (desempenho de mensagens) ficou em `hermes/analytics/`; a divergência de caminho entre irmãos da mesma
  onda é registrada aqui, não escondida. Unificar exige decisão do dono da estrutura (custo de mover é
  baixo, mas mexe em dois cards já fechados).
- **Uma única fonte**: a análise inteira sai de UMA consulta sobre uma tabela. Fontes declaradas no contrato
  têm de ser exatamente as fontes montadas no código (`FONTES_DIVERGEM_DO_CONTRATO`, exit 3).
- **Porta de banco**: mesmo contrato dos irmãos — o comando que fala `psql`, respondendo em `-tA -F'|'`. A
  linha tem de ter **14 campos**; fora disso a rodada RECUSA (`LINHA_FORA_DO_FORMATO`, exit 3) em vez de
  analisar dado torto.
