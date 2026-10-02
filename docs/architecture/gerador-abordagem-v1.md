# Gerador de abordagem outbound v1 (`gerador-abordagem-v1`) — card TRE-W6-E02-T01

Componente: `hermes/agents/outreach/outreach_generator.py` · Política: `hermes/agents/outreach/politica-outreach-v1.json`
Prompt versionado: `hermes/agents/outreach/prompt-abordagem-v1.md` (`abordagem-v1`) · Contrato do componente:
`hermes/agents/outreach/gerador-abordagem-v1.json` · Onda W6 · Depende de `TRE-W5-E07-T01` (NBA v1).

## 1. O que o card entrega

O passo `Hermes → GPT: gerar abordagem` do fluxo outbound do doc 06 §3: o componente lê a evidência que já
existe no PostgreSQL, monta o **prompt versionado**, pede a abordagem (assunto/corpo/CTA) ao provedor
declarado, **valida deterministicamente** o que voltou e grava o resultado como **pedido de aprovação humana**
(`human_approvals`, status `PENDING`), com auditoria em `agent_runs`. Nada é enviado: o envio é do W6-E04 e a
decisão humana é do W6-E03.

Vereditos: `GERADA` (pedido gravado) · `JA_GERADA` (mesma entrada, nada duplicado) · `RECUSADA`
(empresa inexistente, sem contato, contato bloqueado, sem evidência, provedor incompleto, abordagem inválida,
fato não sustentado — **nada** escrito em `human_approvals`) · `ABSTEVE` (sem recomendação `OPEN`, ou ação do
contrato declarada sem abordagem) · `ERRO` (rede/banco/porta).

## 2. Campos exigidos pelo doc 11 §2

### ACCEPTANCE

| # | Critério | Onde é medido |
|---|---|---|
| AC1 | abordagem válida vira pedido `PENDING` em `human_approvals` com `action_type` = ação recomendada, `entity_type=CONTACT`, `entity_id` = contato, `proposed_action` com canal/tipo/assunto/corpo/cta/citações/hash/recomendação e `decided_by` vazio | aceite `A1` (E2E) |
| AC2 | auditoria da rodada em `agent_runs` com `input` ESTRUTURADO (organization_id, recommendation_id, contact_id, ação, canal, `entrada_hash`, `prompt_version`, `model_provider`, `model_name`, política@versão) e `output` com o id do pedido | aceite `A2` |
| AC3 | nada fora das duas tabelas é tocado (organizations, contacts, recommendations, scores, signals, pain_hypotheses, research_runs, interactions, sync_events, outbox_events) | aceite `A3`, `A9` |
| AC4 | replay da mesma entrada não duplica pedido nem expira PENDING (id determinístico) | aceite `A4`, suíte `F1` |
| AC5 | evidência citável nova gera pedido novo e o PENDING anterior vira `EXPIRED`; mexer só em campo não citável é replay | aceite `A5`, suíte `F2` |
| AC6 | compliance: `do_not_contact`/`opt_out_*` bloqueiam (absoluto e por canal) e a rodada RECUSA **sem gravar**, com recusa auditada | aceite `A6`, suíte `B1..B4` |
| AC7 | fail-closed: sem recomendação ABSTEM; ação sem abordagem ABSTEM; sem contato/sem evidência/empresa inexistente RECUSA sem gravar | aceite `A6`, suíte `A5`, `E1` |
| AC8 | fato não sustentado (número/URL/e-mail fora da evidência), marcador de citação inexistente, ausência de citação, afirmação proibida e excesso de tamanho RECUSAM a abordagem | aceite `A8` (alucinação do provedor), suíte `E2..E9` |
| AC9 | adaptador `chat-completions`: request leva modelo, credencial no cabeçalho, prompt versionado e evidência; resposta vira abordagem; tokens do provedor entram na auditoria | aceite `A8`, suíte `G3..G7` |
| AC10 | assinatura vem do bloco `remetente` declarado (não do modelo); credencial nunca em argv, política, relatório ou registro | aceite `A1`, `A8`, suíte `F5` |
| AC11 | guarda de escrita recusa DDL, escrita fora das duas tabelas e DELETE sem `--confirmo`; `--desfazer` dry-run não apaga e `--confirmo` apaga só os pedidos da rodada, preservando a auditoria | aceite `A7`, `A9`, suíte `D1..D5` |
| AC12 | `prod` recusado (exit 4) sem escrita; `--planejar`/`--regras` sem conexão; política/contrato incoerente RECUSA (exit 3) | aceite `A7`, suíte `H1..H4` |
| AC13 | o vocabulário das ações e os status de aprovação são LIDOS do Data Contract; nenhuma ação, papel, regra do NBA, tipo de score/TIER ou texto de prompt em forma executável no código | suíte `A3..A7`, `C1..C6`, autoteste `D12` |

### TEST

- **Suíte offline** (sem banco, sem rede externa):
  `python3 scripts/agentes/verificar_gerador_abordagem.py` → medido **PASS (100 OK / 0 falhas)**.
  Cobre identidade, política/contrato, guarda de contato, o que não pode estar no código, guarda de escrita,
  validação da abordagem, idempotência, adaptador de provedor contra **stub HTTP local** (incl. retry em 5xx,
  4xx sem retry, resposta ilegível, cerca de código, alucinação barrada) e CLI.
- **Autoteste por mutação**: `… --autoteste` → medido **PASS (12/12 mutações detectadas)**, cada mutação
  reprovando o item esperado (guarda de contato, fato não sustentado, idempotência, citação mínima, afirmação
  proibida, vocabulário do contrato, marcador do prompt, DELETE sem confirmação, limite de tamanho, etc.).
- **Aceite E2E** (PostgreSQL descartável na VPS + stub local do provedor):
  `bash scripts/agentes/teste_gerador_abordagem_aceite.sh` → medido **ACEITE_OUTREACH_001_OK (69 OK / 0 FALHOU)**.
  Com `--prova-de-dente`: **4/4 dentes** — guarda de contato (reprova `A6 contato com opt_out recusa`), fato não
  sustentado (`A8 alucinação RECUSA a rodada`), id aleatório (`A4 replay não duplica pedido`) e supersessão
  desligada (`A5 pedido antigo preservado e EXPIRED`).

### ROLLBACK

- **Componente**: nada é aplicado em produção por este card (ADR-005). O rollback é `--desfazer <correlation_id>`
  (dry-run informa; `--confirmo` apaga **só** os pedidos de aprovação daquela rodada, em `human_approvals`),
  com a auditoria de `agent_runs` **preservada** — a rodada continua explicável depois de desfeita.
- **Código**: reverter o merge do branch `feature/TRE-W6-E02-T01` devolve o repo ao estado do W5. Não há
  migration, coluna, índice nem artefato de infraestrutura: o schema V1 do contrato é usado como está.
- **Promoção a produção**: exige card próprio com aprovação humana registrada (`docs/operations/registro-de-aprovacoes.md`).
- Fora de produção, o par `human_approvals.status` (`PENDING`→`EXPIRED`) é reversível por SQL descartável; não
  há estado externo criado (nenhum e-mail, nenhuma atividade no Odoo, nenhum evento de outbox).

### RISK

| Risco | Mitigação medida |
|---|---|
| modelo inventa fato (número, URL, cliente, resultado) | validação determinística de fato sustentado + citação obrigatória: `E3`, `E4`, `E5`, `G8`, dente "fato" |
| pressão comercial vira promessa | lista declarada de afirmações proibidas na política: `E6` |
| abordagem a contato que pediu opt-out | guarda de contato antes de qualquer geração: `B2`, `B3`, aceite `A6`, dente "guarda" |
| rascunho sem base (genérico) | `SEM_EVIDENCIA` sem pesquisa/sinal/dor/score/TIER: aceite `A6` |
| duplicação de pedidos com retry | id `uuid5` do conteúdo: `F1`, aceite `A4`, dente "id" |
| pilha de PENDINGs vencidos | supersessão para `EXPIRED` com histórico preservado: aceite `A5`, dente "supersessão" |
| credencial vazando | só variável de ambiente; ausente = RECUSA **sem abrir conexão**; sem chave em argv/relatório/registro: `G1`, aceite `A1`, `A8` |
| custo de modelo fora de controle | provedor offline é o padrão e é declarado como offline (sem token/custo); provedor de rede só com `--provedor chat-completions` explícito: aceite `A2` |
| escrita indevida no schema | guarda de escrita + `BEGIN/COMMIT` + tabelas declaradas: `D1..D5`, aceite `A3`/`A9` |
| publicar/enviar sem aval humano | o componente não envia e não decide: grava `PENDING` e para (doc 12 §4 / ADR-0004) |

## 3. Decisões declaradas deste card

1. **Onde o rascunho vive**: em `human_approvals.proposed_action` (JSONB, `status=PENDING`). O `interactions`
   só registra o que foi de fato enviado (W6-E04) — nenhuma coluna nova, nenhum DDL (governança §10).
2. **Supersessão usa `EXPIRED`**: `human_approvals.status` é vocabulário fechado; `EXPIRED` é o único estado
   que expressa "pedido que perdeu validade sem decisão humana". Inventar `SUPERSEDED` seria mudar contrato.
3. **Provedor (o padrão é offline)**: o adaptador tem UM contrato (chat-completions compatível com OpenAI). O
   modo `offline` é um renderizador determinístico declarado, marcado como offline na auditoria — existe para
   medir o caminho de validação/persistência sem rede, **jamais** para fingir resposta de modelo. Modelo e
   base URL não moram na política (regra herdada do JEV).
4. **Assinatura**: acrescentada pelo gerador a partir do bloco `remetente` declarado, depois da validação — o
   modelo não escreve assinatura e não inventa remetente.
5. **`entrada_hash` usa a evidência citável**: campo que não entra no texto citável (ex.: `relevance_score`)
   não força rascunho novo — mudar só ele é replay, com prova no aceite `A5(a)`.

## 4. Lacunas declaradas (não são implementadas aqui)

- `agent_runs` **não tem** colunas `model_provider`/`model_name`/`prompt_version`/`input_hash`; os quatro vão
  estruturados dentro de `agent_runs.input` (JSONB). Criar coluna exige versão nova do contrato — registrado
  para o V1.1.
- O **texto do prompt** e a **rubrica** (limites, citação, afirmações proibidas) são proposta deste card: o
  baseline define o fluxo, não o conteúdo.
- Não há medida de **resposta** (interesse real) nesta v1: a prova de qualidade é validação determinística.
  Calibração por taxa de resposta é W9.
- Um **único** contato por empresa (o indicado pela recomendação do NBA); abordagem paralela a vários decisores
  exige decisão de cadência — card futuro.
- Rota/comparação entre modelos e custo por abordagem: W9.

## 5. Fluxo (encadeamento com os cards vizinhos)

```
TRE-W5-E05/T06/T07   PRIORITY → tier registrado → recomendação OPEN
        │
        ▼
TRE-W6-E02-T01 (este)  lê recomendação + evidência → monta prompt → provedor → valida → human_approvals PENDING
        │
        ▼
TRE-W6-E03-T01  workflow de aprovação humana (notificação, decisão, APPROVED/EDITED/REJECTED)
        │
        ▼
TRE-W6-E04-T01  envio por SMTP Titan + interação SENT (único ponto que envia)
```
