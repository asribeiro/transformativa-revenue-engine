# Agente ICP Score v1 — contrato do fit estrutural (TRE-W5-E01-T01)

Card **TRE-W5-E01-T01** (W5 · EPIC E01 · P1) · Baseline V1.1.0 · Depende de `TRE-W4-E06-T01`
(cadeia Scout → Research → Signal → Pain Hypothesis → Contact Research medida num banco).

Agente: `hermes/agents/icp_score/icp_score.py` · Contrato: `hermes/agents/icp_score/agente-icp-score-v1.json`
Suíte offline: `scripts/agentes/verificar_agente_icp_score.py` · Aceite no banco:
`scripts/agentes/teste_icp_score_aceite.sh` · Runbook: `docs/runbooks/agente-icp-score.md`.

Este documento declara os quatro campos que a seção 2 do doc 11 exige e que o plano **não**
detalhava para este card: **ACCEPTANCE**, **TEST**, **ROLLBACK** e **RISK**. Eles foram
definidos no início da execução e registrados na thread do card antes de rodar o aceite
(comentário `2026-10-02`, thread `t_e4a90eba`).

## 1. Por que este agente existe

O baseline nomeia o ICP Score e **não** define a fórmula: o doc 03 §3 diz apenas "fit
estrutural com o cliente desejado"; o doc 04 §8 lista `ICP` entre os cinco scores e exige
`score_version` (score sem versão é recusado); o Data Contract V1.0 dá o **contexto de
negócio** (`scores.icp_context`: faixa 70–1.000 colaboradores, sweet spot 150–700, cinco ICPs,
decisores). Faltava a peça que transforma esse contexto em número auditável — é o que este
card entrega.

O padrão da onda W5 é: cada score mede **uma** coisa, e o Priority Score (W5-E05) combina os
quatro com os pesos do contrato de dados. O ICP responde só "esta empresa **é** do tipo certo?",
nunca "temos chance com ela hoje?" — isso é sinal de compra (W5-E03) e fit de automação
(W5-E02).

## 2. O modelo 1.1 (`icp-v1.1.0`) — homologado pelo dono

```
ICP = 0,30 * segmento + 0,25 * porte + 0,10 * geografia + 0,15 * modelo_b2b + 0,20 * intencao
```

| componente | peso | campo lido | escala do sub-score |
| --- | --- | --- | --- |
| segmento | 0,30 | industry_code + industry_name | 100 = casa um dos cinco ICPs do contrato; 0 = não reconhecido ou não informado |
| porte | 0,25 | employee_band (ou employee_count) | 100 = sweet spot 150–700; 60 = 70–149 e 700–1000; 0 = LT_70, GT_1000 ou não informado |
| geografia | 0,10 | state | 100 = UF declarada no corte (SP); 0 = fora ou não informado |
| modelo_b2b | 0,15 | business_model | 100 = B2B; 70 = B2B2C; 0 = B2C ou não informado |
| intencao | 0,20 | sales_intelligence.signals | 100 × (sinais com fonte e data) / (sinais declarados); 0 = nenhum sinal creditado |

Os pesos do `icp-v1.0.0` (segmento 0,45 / porte 0,35 / modelo_b2b 0,20) valiam para três
critérios; a 1.1 acrescenta **geografia** e **intenção** e os três originais foram reduzidos na
mesma proporção (0,70 do total) — a conta fecha em 1,00, e a suíte exige isso.

### 2.1 Cortes (portão, não peso) — a definição do dono

`docs/business/icp-transformativa-v1.md` homologa: porte **≥ 50** usuários/funcionários
declarados e `state = SP` são **corte**. Quem não passa **não entra na campanha**, mesmo com
score alto: `score_value = 0.00`, `elegivel = false` e o motivo do corte na explicação
(`CORTE_PORTE_ABAIXO_DE_50`, `CORTE_PORTE_SEM_DADO`, `CORTE_FORA_DE_SP`,
`CORTE_GEOGRAFIA_SEM_DADO`). O valor efetivo do porte é o `employee_count` ou, na falta dele,
o **limite inferior da faixa** declarada (`LT_70` vale 0, `GT_1000` vale 1001) — não há
crawl do LinkedIn (definição do dono: a lista e o porte vêm dele ou de fonte licenciada).

### 2.2 Os três sinais de intenção (critérios 3, 4 e 5)

Cada sinal só dá crédito com **fonte** (`source_type`/`source_url`) **e data** (`event_date`).
Sem fonte o sinal não credita e o motivo nomeia o critério (`INTENCAO_SEM_FONTE`,
`INTENCAO_SEM_DATA`, `INTENCAO_SEM_DADO`) — **nenhum campo de fonte/data é preenchido pelo
agente com valor sintético**, nem no modo real, nem no `--planejar`. Sinal cuja `evidence`
declara inferência vale, mas entra **marcado** como inferência na explicação. As três decisões
que o documento precisa deixar explícitas:

1. **A fórmula deixou de ser proposta.** `modelo.status = HOMOLOGADO`: cortes e cinco critérios
   vêm da definição do dono; peso, corte ou vocabulário novo a partir daqui é versão nova
   (doc 04 §10). A versão anterior (0,45/0,35/0,20) fica registrada no contrato em
   `pesos_da_versao_anterior`.
2. **Ausência de dado não vira fit.** Componente sem dado reconhecido pontua 0 e registra o
   motivo na explicação. Quem mede dado faltante é o Data Quality Score (W5-E04); misturar as
   duas coisas produziria um ICP alto para empresa mal preenchida.
3. **O que é acidente de vocabulário fica visível.** O casamento de segmento é por
   vocabulário declarado (termo casando no **início** de palavra do
   `industry_code + industry_name`). Quando mais de um ICP casa, **todos** vão para
   `segmentos_casados` na explicação e o primeiro na ordem declarada pontua — ambiguidade é
   reportada, nunca escondida (mesma regra do `dedup.rule` do contrato).

Pesos, faixas, vocabulário e motivos vivem em `agente-icp-score-v1.json` — **nenhum peso é
literal no código** (item próprio da suíte, que reprova se um peso aparecer no corpo do
agente). A tabela acima é conferida contra o contrato pelo verificador: mudar peso num lado só
reprova.

## 3. Entrada — o sujeito, não o dado

A fonte é `jsonl`, uma linha por organização, com `organization_id` obrigatório. No modo real
o agente **lê a organização no banco** (`sales_intelligence.organizations`, `deleted_at IS
NULL`) e o dado de score vem **só de lá**: campo equivalente vindo da fonte é ignorado (item
`fonte-nao-contamina-o-score`, que mede o score de uma organização cujo `employee_band` no
banco é `GT_1000` mesmo com a fonte dizendo `150_299`).

No modo `--planejar` — que **não abre conexão nenhuma** — os campos podem vir na própria
linha (inclusive `state` e `sinais`, a lista de sinais com `signal_type`, `source_type`,
`source_url` e `event_date`), para medir o modelo sem banco. É o único modo em que a fonte fornece o dado, e a saída
o declara (`origem_do_dado: "fonte (modo planejar, sem banco)"`).

## 4. Persistência, versão e idempotência

- Score gravado em `sales_intelligence.scores` com `score_type = 'ICP'` e
  `score_version = modelo.nome` (`icp-v1.1.0`) — a versão tem **uma** fonte, o modelo; não há
  literal de versão no código (item próprio).
- Leitura: `sales_intelligence.organizations` (a organização) e `sales_intelligence.signals`
  (os sinais da intenção). `signals` é **somente leitura** — o agente não escreve sinal, não
  preenche fonte nem data.
- `inputs` grava o que foi lido (campos, `faixa_efetiva`, `origem_do_porte`, `fingerprint`);
  `explanation` grava peso, sub-score, contribuição, valor lido e motivo por componente, mais
  `modelo`, `formula`, `pesos_somam` e `motivos`. `valid_until` fica **NULL** — política de
  validade é do tiering/priority (W5-E05/E06).
- `explanation.pesos_somam` e a soma das contribuições são conferidos contra `score_bruto`
  pela suíte, e `score_value` contra o corte aplicado (`corte_aplicado ⇒ score_value = 0`):
  explicação que não fecha a conta é falha, não detalhe. `explanation.cortes` registra cada
  corte com valor efetivo e motivo; `explanation.criterios` nomeia os **cinco critérios** da
  definição (porte, geografia e as três intenções) com o resultado de cada um
  (`casou` / `nao_casou` / `sem_dado`).
- **Idempotência** (doc 06 §7): chave
  `icp:score:<organization_id>:<modelo>:<fingerprint[:16]>`, reivindicada em
  `sync_events.idempotency_key` (UNIQUE). O fingerprint é o sha256 canônico **dos campos que
  mudam o score** (`industry_code`, `industry_name`, `employee_count`, `employee_band`,
  `faixa_efetiva`, `business_model`, `state` e o conjunto de sinais **creditados** com fonte e
  data) — `status`, `updated_at`, nome, país e sinais sem fonte ficam de fora.
  Consequências medidas no aceite:
  - mesmos dados ⇒ mesma chave ⇒ **replay**: nada é inserido, veredito `JA_EXISTE`;
  - dado alterado ⇒ chave nova ⇒ **score novo**, e o anterior permanece (score é histórico,
    não mutável — doc 04 §8/§10).
- Escrita em duas instruções numa transação (o claim da chave, o INSERT do score e o
  fechamento do evento em snapshot novo): a segunda instrução só fecha a sincronia quando o
  score **daquela** rodada existe (`EXISTS ... WHERE id = '<sid>'`), devolvendo a marca
  `ICP_SCORE_GRAVADO`. Sem a marca, nada foi duplicado. (Defeito medido no aceite E2E da W4:
  CTE de escrita e instrução principal compartilham o snapshot, e um `UPDATE` que dependa da
  linha recém-inserida fecha 0 linhas.)

## 5. Vereditos e auditoria

| veredito | quando | `agent_runs.status` |
| --- | --- | --- |
| `CALCULADO` | score novo gravado | `COMPLETED` |
| `JA_EXISTE` | mesmos dados: replay idempotente | `COMPLETED` |
| `RECUSADA` | `organization_id` ilegível, organização inexistente ou apagada | `REJECTED` |
| `ERRO` | porta de banco falhou ou guarda de escrita recusou | `FAILED` |

Uma linha de `agent_runs` por organização, com `correlation_id` do lote. Se a **própria**
auditoria não for escrita, o veredito vira `ERRO` (`AUDITORIA_NAO_REGISTRADA`) — conclusão
sem trilha não é conclusão. `model`, `tokens_input`, `tokens_output` e `estimated_cost` ficam
**NULL**: a v1 é determinística e não chama LLM (o gate de recibo do JEV existe e é
fail-closed, mantido para paridade com os agentes da W4 — não há caminho que o exercite aqui).

## 6. Guardrails

- Guarda de escrita fail-closed: recusa DDL, escrita fora de `scores`/`agent_runs`/`sync_events`
  e **UPDATE em `scores`** (corrigir score é inserir versão nova, nunca reescrever).
- A varredura de SQL ignora o conteúdo dos literais: nome de empresa com "Drop"/"Create" não
  pode ser confundido com DDL (defeito medido na revisão da W4).
- `--ambiente prod` recusado (exit 4) **sem escrever** e sem registrar execução (ADR-005);
  ambiente ausente também recusa, e `--planejar` não abre conexão.
- Nenhum evento de outbox nesta v1: `COMPANY_QUALIFIED` é decisão de tiering/priority
  (W5-E05/E06) e o caminho até Odoo é W3. O ICP Score escreve **só** o próprio score.

## 7. Operação

```bash
# planejar (sem banco): mede o modelo com os campos da fonte
python3 hermes/agents/icp_score/icp_score.py --planejar --fonte hermes/agents/icp_score/exemplos/organizacoes-exemplo.jsonl

# pontuar no dev (o banco vive na VPS do ambiente — ADR-0008)
python3 hermes/agents/icp_score/icp_score.py --ambiente dev --fonte organizacoes.jsonl \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --relatorio /tmp/icp-score-rodada.json

# desfazer uma rodada (dry-run por padrão)
python3 hermes/agents/icp_score/icp_score.py --desfazer <correlation_id>
python3 hermes/agents/icp_score/icp_score.py --desfazer <correlation_id> --confirmo
```

## 8. ACCEPTANCE

| # | Critério | Itens que o medem |
| --- | --- | --- |
| AC1 | lê a organização no banco e grava em `scores` com `score_type='ICP'`, `score_version` do modelo, `inputs` e `explanation` preenchidos | `rodada1-scores-gravados`, `rodada1-tipo-e-versao`, `rodada1-inputs-e-explicacao` |
| AC2 | modelo determinístico: pesos 0,30/0,25/0,10/0,15/0,20 somando 1,00 lidos do contrato (sem peso literal no código) | suíte: `pesos-somam-um`, `peso-nao-esta-literal-no-codigo`, `documento-e-modelo-nao-diverge` |
| AC3 | `explanation` diz como o número saiu (peso, sub-score, valor lido, motivo) e ausência de dado não vira fit | suíte: `explicacao-tem-contribuicao-e-pesos-que-somam-um`, `sem-dado-pontua-0-e-nao-vira-fit`; aceite: `rodada1-explicacao-fecha-a-conta`,`rodada1-dado-ausente-com-motivos` |
| AC4 | idempotência: mesmos dados ⇒ replay sem linha nova; dado alterado ⇒ score novo com o anterior preservado | `rodada2-nao-duplica`, `rodada2-replay`, `rodada3-dado-novo-grava-de-novo`, `rodada3-historico-preservado` |
| AC5 | organização inexistente/apagada ⇒ `RECUSADA` sem escrita | `rodada1-recusadas`, `rodada1-inexistente-sem-score` |
| AC6 | auditoria por organização e `sync_events` por score; sem LLM (model/tokens/custo NULL) | `rodada1-auditoria-por-organizacao`, `rodada1-sem-llm`, `rodada1-sync-events-success` |
| AC7 | fail-closed: `--ambiente prod` recusado (exit 4) sem escrever; `--planejar` não conecta | `prod-recusado-exit-4`, `prod-nao-escreveu`, `planejar-exit-0-sem-conectar` |
| AC8 | desfazer: dry-run não apaga; `--confirmo` apaga só os scores da rodada, preserva auditoria e outros scores, registra `ROLLBACK` | `desfazer-dry-run-*`, `desfazer-apagou-so-a-rodada`, `desfazer-preservou-*`, `desfazer-registrou-rollback` |
| AC9 | ambiente: container descartável próprio, containers persistentes intactos, nada em produção, zero escrita em `recommendations`/`outbox_events`/`interactions`/`signals` | `rodada1-nenhuma-outra-tabela-escrita`, `ambiente-containers-intactos` |
| AC10 | **corte de porte ≥ 50**: 49 funcionários **não** passa o corte (score 0, `elegivel=false`); 50 passa | suíte: `corte-de-porte-49-nao-passa`, `corte-de-porte-50-passa`; aceite: `rodada1-valor-49-no-corte-de-porte`, `rodada1-corte-de-porte-49-nao-passa` |
| AC11 | **geografia SP**: fora de SP **não** entra; `state` ausente também não | suíte: `corte-fora-de-sp-nao-entra`, `corte-geografia-sem-dado-nao-entra`; aceite: `rodada1-valor-fora-de-sp`, `rodada1-corte-de-geografia-fora-de-sp` |
| AC12 | **intenção com fonte e data**: sinal sem fonte declarada **não** recebe crédito; sem data também não; os três sinais creditados pontuam cheio | suíte: `intencao-sem-fonte-nao-da-credito`, `intencao-sem-data-nao-da-credito`, `intencao-tres-sinais-pontua-cheio`; aceite: `rodada1-valor-intencao-sem-fonte`, `rodada1-intencao-sem-fonte-motivo` |
| AC13 | **cinco critérios nomeados** na explicação (`explanation.criterios`), cada pontuação dizendo o que casou e o que faltou | suíte: `explicacao-nomeia-os-cinco-criterios`, `explicacao-tem-contribuicao-e-pesos-que-somam-um` |
| AC14 | **caminho oposto**: organização 50+, em SP, com os três sinais pontua 100,00 e explica por quê | suíte: `caminho-oposto-50-mais-em-sp-com-tres-sinais-pontua-100`; aceite: `rodada1-valor-sweet-spot`, `rodada1-cinco-criterios-casados` |
| AC15 | **nada inventado**: nenhuma fonte/data sintética em caminho de produção (`signals` não é escrito; sinal sem fonte não ganha fonte) | suíte: `nenhuma-fonte-ou-data-sintetica-no-codigo`, `intencao-sem-fonte-nao-da-credito` |

Veredito de sucesso: `ACEITE_ICP_SCORE_001_OK` (uma linha `OK`/`FALHOU` por item).

## 9. TEST

Dois níveis, ambos por **execução real**:

1. **Suíte offline** (`python3 scripts/agentes/verificar_agente_icp_score.py [--autoteste]`, sem
   banco e sem rede): contrato e artefatos, modelo puro caso a caso (sweet spot, fora do ICP,
   B2C, ausência, faixa derivada, precedência da faixa do banco, casamento por início de
   palavra, ambiguidade registrada, decimais, fingerprint), coerência da tabela ACCEPTANCE com
   os itens que realmente existem nos dois verificadores, guardas de escrita, SQL declarado,
   fluxo completo em **porta de roteiro** (que roda a mesma guarda do alvo — a suíte não usa
   dublê de biblioteca) e recusas de contrato inválido. `--autoteste` aplica **uma mutação por
   regra** numa cópia do agente e exige que a suíte reprove **o item esperado**; mutação que
   não se aplica na âncora também reprova.
2. **Aceite no banco** (`bash scripts/agentes/teste_icp_score_aceite.sh [--prova-de-dente]`,
   roda **na VPS** do ambiente): container PostgreSQL descartável `pg-icp-acc` com a migration
   0001 e organizações sintéticas de perfis diferentes (sweet spot com os três sinais, fora de
   SP, **49 funcionários** no corte, sem dado, B2B2C, porte derivado do count e **sinais sem
   fonte declarada**) + uma organização apagada; rodadas 1/2/3 medem valores, replay, histórico e a
   não contaminação da fonte; guardas de ambiente; desfazer na ordem inversa. `--prova-de-dente`
   roda baseline verde e uma mutação por regra, exigindo o **item esperado** em `FALHOU` — entre
   elas as três do card v1.1: corte de porte desligado (49 passa), corte de geografia desligado
   (fora de SP entra) e crédito de intenção sem fonte declarada.

## 10. ROLLBACK

- **Do agente**: `--desfazer <correlation_id> [--confirmo]` (dry-run é o padrão). Com
  `--confirmo` apaga os `scores` da rodada e os `sync_events` de `INSERT` deles, e grava um
  `sync_events` de `ROLLBACK`. `agent_runs` **não** se apaga (auditoria preservada) e scores de
  outras rodadas não são tocados.
- **Do código**: reverter os commits da branch `feature/TRE-W5-E01-T01`. Nada consome o ICP
  Score na v1 — o aceite E2E da W4 (`scripts/e2e/verificar-e2e-sales-intelligence.sh`) continua
  medindo `scores` **vazio**, porque roda no banco descartável dele e os cinco agentes da W4 não
  escrevem score.
- **Da fórmula**: o modelo é versionado e a versão entra na chave de idempotência — fórmula
  nova é versão nova (`icp-v1.1.0`; a anterior é `icp-v1.0.0`), logo linhas novas; score antigo
  nunca é reescrito.

## 11. RISK

| Risco | Como o card se protege |
| --- | --- |
| **Score que não explica** (número sem lastro) | `inputs` grava valor lido + origem do porte; `explanation` grava peso/sub-score/contribuição/motivo; itens conferem os dois no banco e a soma das contribuições contra `score_value` |
| **Fonte contaminando o cálculo** | no modo real o dado vem só do banco; item mede o score de organização cujo dado no banco contradiz a fonte |
| **Peso alterado em silêncio** | pesos/faixas/vocabulário/cortes no contrato, não no código; item proíbe peso literal, item compara contrato × tabela do documento, item exige soma 1,00 |
| **Corte/portão que não morde** (49 passando, fora de SP entrando, intenção sem fonte creditando) | cada regra tem mutação própria na prova de dente da suíte e do aceite, exigida pelo **item esperado** |
| **Fonte ou data inventada** | o agente só grava o que leu; `signals` é somente leitura, sinal sem fonte não credita e o motivo nomeia o critério |
| **Retry criando duplicata / histórico perdido** | chave de idempotência com fingerprint dos campos de score — replay não duplica, dado novo vira score novo |
| **Escrever onde não deve** | guarda fail-closed + item que soma as tabelas proibidas = 0 no aceite + item que compara as tabelas escritas com as declaradas |
| **Confundir prosa com DDL** | a varredura olha o código SQL, não o conteúdo dos literais (defeito medido na W4) |
| **Teste que mede o próprio alvo / dente inerte** | banco descartável com nome próprio e `DROP SCHEMA` antes de cada rodada; a mutação é exigida pelo **item esperado**, não por "o aceite falhou" |
| **Explicação que mente sobre a própria conta** | `pesos_somam` e a soma das contribuições são conferidos contra `score_value` pela suíte |

## 12. Decisões de implementação

- **Porta de banco** = prefixo `psql` (mesmo padrão da W4): o PostgreSQL vive na VPS do
  ambiente (ADR-0008), e o container do Hermes não tem socket Docker nem `psql`.
- **Modelo no contrato, mecânica no código**: mudar peso/faixa/vocabulário é editar o
  contrato (versionado), não caçar literal no meio do código.
- **Uma organização por execução de auditoria**: quatro organizações ⇒ quatro linhas em
  `agent_runs`, o que deixa o replay e a recusa visíveis por organização, não só no total.
- **`--planejar` calcula de verdade** (com os campos da fonte) em vez de só dizer "faria":
  é o único caminho que mede o modelo sem banco, e é o que a suíte usa.

## 13. Lacunas declaradas

- **Fórmula homologada em 1.1** — cortes e cinco critérios são a definição do dono
  (`docs/business/icp-transformativa-v1.md`); a expansão para outros estados e a ida do motor
  para produção são itens próprios (ADR-0009).
- **`business_model` não tem vocabulário fechado no Data Contract** — o casamento é deste
  modelo, e valor desconhecido cai em `sem_dado` com motivo.
- **Casamento de segmento é vocabulário, não semântica**: termo curto pode casar por acidente;
  por isso a lista é curada, o casamento é por início de palavra e o resultado inteiro
  (inclusive os múltiplos casamentos) vai para a explicação.
- **Sem LLM, sem HTTP, sem crawler**: o agente lê o banco e calcula.
- **Fora do card**: `valid_until`, tier, Priority Score, Next Best Action, evento
  `COMPANY_QUALIFIED`, espelhamento no Odoo e o aceite E2E da cadeia W5 (W5-E05, E06, E07,
  W3 e W5-E08).
- **Homologação não é feita aqui**: quem entrega não homologa (estágio 6 é o perfil `tester`,
  estágio 7 é o Anderson).

## 14. Referências

- `03_ARQUITETURA_DE_NEGOCIO.md` §3 e §4 · `04_PROJETO_FISICO_DE_DADOS.md` §8 e §10 ·
  `06_INTEGRACOES_E_FLUXOS_SISTEMICOS.md` §5 e §7 · `07_ROADMAP_DE_IMPLEMENTACAO.md` §7 (W5)
- `docs/data/DATA_CONTRACT_V1.md` §8 · `docs/data/data_contract_v1.json#scores`
- `hermes/agents/icp_score/agente-icp-score-v1.json` (modelo e vocabulários)
- `docs/architecture/agente-scout-v1.md` e `docs/architecture/e2e-sales-intelligence.md`
  (padrão de contrato de agente e de aceite da W4)
