# Runbook — Aceite E2E da cadeia W5: Score → Tier → NBA (TRE-W5-E08-T01)

O que este aceite é, como se roda, o que cada item significa e **o que ele NÃO mede** (lacuna
declarada — não se inventa medição que mente). Card: **TRE-W5-E08-T01** ("Test scoring/NBA", W5/E08,
depende de W5-E07-T01). Contrato do card (ACCEPTANCE/TEST/ROLLBACK/RISK):
`docs/kanban/criterios-de-aceitacao.md`, seção TRE-W5-E08-T01.

- Aceite: `scripts/e2e/verificar-e2e-scoring-nba.sh`
- Gate da onda (doc 07 §7): *score/tier/NBA gerados sem intervenção*
- Veredito: `ACEITE_E2E_SCORING_NBA_001_OK` / `ACEITE_E2E_SCORING_NBA_001_FALHOU`

## 1. Onde ele roda

**Na VPS do ambiente** (ADR-0008): o container do Hermes não tem daemon Docker nem rota até o
PostgreSQL, então quem orquestra entra por SSH e o aceite roda **no host**. Ele sobe **um** container
PostgreSQL descartável próprio (`pg-w5-acc`, `postgres:16`, sem porta publicada), aplica a migration
`db/migrations/0001_sales_intelligence_v1.sql` em schema limpo e roda **os sete componentes** no
mesmo banco, em sequência.

Se o container `pg-w5-acc` **já existir**, o aceite **aborta** (exit 2) em vez de mexer no que não é
dele. `pg-sales-dev`, `pg-odoo-dev`, `odoo-dev` e `proxy-dev` nunca são tocados.

## 2. Como se roda

```bash
cd /opt/tre/<dir-com-o-repo>

# o aceite completo (container descartável; é o modo de produção do aceite)
bash scripts/e2e/verificar-e2e-scoring-nba.sh

# o aceite tem dentes? (baseline verde + uma mutação por módulo, cada uma pelo item esperado)
bash scripts/e2e/verificar-e2e-scoring-nba.sh --prova-de-dente

# deixar container e diretório de trabalho de pé para investigar
bash scripts/e2e/verificar-e2e-scoring-nba.sh --manter
```

**Variáveis** (todas com default seguro): `TRE_W5_RAIZ`, `TRE_W5_IMAGEM` (`postgres:16`),
`TRE_W5_CONTAINER` (`pg-w5-acc`), `TRE_W5_TRABALHO` (diretório temporário), `TRE_W5_PULAR_SUITES=1`
(pula as suítes offline, usado na recursão dos dentes) e um caminho de código por componente
(`TRE_W5_ICP_PY`, `TRE_W5_AF_PY`, `TRE_W5_BS_PY`, `TRE_W5_DQ_PY`, `TRE_W5_PR_PY`, `TRE_W5_TIER_PY`,
`TRE_W5_NBA_PY`). São essas variáveis que a prova de dente usa para apontar o aceite para as **cópias
mutadas**.

**Saída**: uma linha por item (`OK` / `FALHOU`), o resumo `<rótulo>: N OK / M FALHOU` e o veredito em
uma linha. **Exit codes**: `0` = aceite OK · `1` = FALHOU · `2` = guarda/uso (container existente,
docker ausente, artefato faltando).

## 3. O que cada passo mede

| passo | o que mede | por que não dá para fingir |
| --- | --- | --- |
| 0 · suítes offline | as **sete suítes** dos componentes no **mesmo commit** do aceite | diz em que commit a cadeia foi medida: contrato de componente quebrado reprova aqui, antes de qualquer banco |
| 1 · massa | 3 empresas com identificadores fortes: **A** com lastro completo (sinal de tecnologia + programa de eficiência + dor validada 85 + decisor com e-mail, sem interação), **B** com lastro completo e uma abordagem de 5 dias atrás sem resposta, **C** sem sinal e sem hipótese | os três casos são o que separa "cadeia viva" de "cadeia que só sabe dizer sim" |
| 2 · ICP → AUTOMATION_FIT → BUYING_SIGNAL → DATA_QUALITY | cada componente grava a **linha** do seu score no banco, com versão; `AUTOMATION_FIT` **RECUSA** a empresa sem lastro (`SEM_LASTRO`); `BUYING_SIGNAL` da empresa sem sinal é **0,00** com motivo; `DATA_QUALITY` espelha o valor na organização | a entrada de cada score é o **estado real das tabelas** (o AUTOMATION_FIT lê `signals` e `pain_hypotheses` do banco — o item confere o `inputs` gravado) |
| 3 · PRIORITY → TIER → NBA | `PRIORITY` só existe com os **quatro** componentes (`SEM_LASTRO_COMPLETO` para C); `TIER` só existe com `PRIORITY` (`SEM_PRIORITY`); `NBA` só existe com registro `TIER` (`SEM_TIER`); a recomendação é `OPEN`, com ação do vocabulário do contrato e `confidence` NULL | são os três portões da cadeia: sem eles a "recomendação" sairia de evidência que ninguém calculou |
| 4 · escopo | as tabelas de **negócio** (organizations, contacts, interactions, signals, pain_hypotheses, research_runs) saem **idênticas** (foto md5 antes/depois), a `outbox` fica **0** e nenhum `agent_runs` tem modelo/token/custo | é a prova de que a cadeia não "resolve" o problema escrevendo onde não pode e de que nenhuma rodada chamou LLM |
| 5 · replay | repetir a cadeia inteira com as **mesmas** entradas: `scores`, registros `TIER` e `recommendations` com a **mesma contagem** | idempotência por entrada (doc 06 §7): retry não é duplicata |
| 6 · guardas | `--ambiente prod` recusado (**exit 4**) nos sete componentes **sem escrita**; `--planejar`/`--regras` exit 0 **sem conexão**; `DATA_QUALITY --planejar` sem porta **recusa** e não escreve | roda os componentes de verdade com a fonte de cada um; a foto de contagens antes/depois é o que prova "sem escrita" (ADR-005) |
| 7 · desfazer | `--desfazer` do NBA é **dry-run** até o `--confirmo`, e o `--confirmo` apaga só as recomendações daquela correlação, preservando a auditoria | rollback executável, não promessa |
| 8 · dentes | cada mutação em **cópia** de um módulo (4 mutações) tem de reprovar **o item esperado** — não basta "o aceite falhou" | é o que separa um aceite que mede de um aceite que só imprime OK |

## 4. Os três itens que só a CADEIA prova (composição)

1. **PRIORITY = fórmula do contrato sobre os quatro scores gravados** — comparação feita em SQL, com
   os valores lidos da tabela `scores` (não de fixture, não de narrativa);
2. **registro `TIER` cita o `score_id` do PRIORITY lido** — o payload carrega a identidade do score:
   o tier é consequência de um número que existe no banco;
3. **a recomendação cita o `tier` gravado pelo tiering** (`rationale LIKE 'tier=<tier>'` casando o
   `sync_events` `operation='TIER'` da mesma empresa) — o próximo passo é consequência do tier, não
   de um palpite do NBA.

Cada componente já tem suíte e aceite próprios (`scripts/agentes/`, `scripts/scores/`); o que este
card acrescenta é **a costura**: que o artefato de um seja de fato o insumo do seguinte, no mesmo
banco, com a empresa sem lastro morrendo em cada portão pelo motivo certo.

## 5. Limites declarados (medidos, não escondidos)

- **CNPJ da coluna normalizado**: a resolução por CNPJ casa a coluna com **dígitos** — fonte
  `"11.222.333/0001-81"` encontra a coluna `11222333000181`, não a pontuada (medido em 02/10/2026).
  Por isso a massa grava o CNPJ em dígitos; `domain` é o identificador forte das outras empresas.
- **`DATA_QUALITY --planejar` exige a porta do banco**: sem porta ele **recusa** ("nenhuma porta de
  banco configurada", exit 1) — o item mede a recusa e a ausência de escrita, não um exit 0 que o
  componente não dá.
- **Tier não é score**: o tier vive em `sync_events` `operation='TIER'`; o contrato lista cinco
  `score_type` e criar o sexto é decisão do dono (lacuna declarada do W5-E06).
- **A massa é sintética**: o aceite mede o **encadeamento e o fail-closed**, não a *acurácia* dos
  scores nem a conversão das recomendações (isso é W8, com dado de produção).
- **Fora do card**: executar a ação recomendada e o Human Approval (W6), o evento de outbox
  `NEXT_BEST_ACTION_CHANGED` e o espelho no Odoo (W3/W6) e a calibração de `confidence` (W6+).

## 6. Evidência medida (02/10/2026)

| execução | comando | resultado | exit |
| --- | --- | --- | --- |
| baseline | `bash scripts/e2e/verificar-e2e-scoring-nba.sh` | `ACEITE E2E SCORING/NBA 72 OK / 0 FALHOU` → `ACEITE_E2E_SCORING_NBA_001_OK` | 0 |
| dentes | `... --prova-de-dente` | `ACEITE E2E SCORING/NBA 76 OK / 0 FALHOU`, com os 4 dentes reprovando o item esperado | 0 |

Os quatro dentes medidos (mutação → item reprovado):

| dente | mutação | item esperado reprovado |
| --- | --- | --- |
| `sem-motivo-sem-priority` | `MOTIVO_SEM_PRIORITY = "SEM_PRIORITY"` → `"MOTIVO_PROVADO"` (tiering) | `3.2 TIER RECUSOU a empresa SEM PRIORITY (SEM_PRIORITY)` |
| `sem-motivo-sem-lastro` | `MOTIVO_SEM_LASTRO = "SEM_LASTRO_COMPLETO"` → `"MOTIVO_PROVADO"` (priority) | `3.1 PRIORITY RECUSOU a empresa sem os quatro (SEM_LASTRO_COMPLETO)` |
| `sem-checagem-de-tier` | `if not fatos.get("tier"):` → `if False:` (nba) | `3.3 NBA RECUSOU a empresa SEM tier (SEM_TIER)` |
| `sem-idempotencia` | id determinístico (`uuid5`) → `uuid.uuid4()` (nba) | `5.1 replay nao duplica recomendacao` |

`sha256` do aceite medido: `76b8997006dc358c6a0eedf3f9cd1268aef8c5bc3370da4129605a13cf5858d1`.
Logs brutos anexados ao card (`aceite-e2e-scoring-nba-72ok.out`,
`aceite-e2e-scoring-nba-dente-76ok.out`) e `sha256-artefatos.out` com o hash de cada módulo e suíte.

## 7. Rollback

Reverter o commit deste card (nenhuma DDL, nenhuma coluna, nenhum schema novo). O aceite **não**
altera ambiente nenhum: o banco de trabalho é um container descartável removido por ele mesmo. O
rollback **operacional** dos dados gravados continua sendo o `--desfazer` de cada componente (o item
7 exercita o do NBA).

## 8. Risco

**Baixo-médio.** É medição: não muda código de produção, schema nem contrato, e não fala com Odoo,
Titan ou n8n. O risco real é o instrumento — um aceite fraco daria verde falso sobre uma cadeia
quebrada — e é exatamente ele que os itens de composição (§4), a foto das tabelas de negócio e os
quatro dentes endereçam. A verificação independente (estágio 6, perfil `tester`) e a homologação
(estágio 7, Anderson) **não** são feitas aqui.
