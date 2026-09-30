# `entity_match_confidence` — o campo/score da decisão de merge (TRE-W1-E04-T02)

**Card:** `TRE-W1-E04-T02` · **Depende de:** `TRE-W1-E04-T01` (motor de deduplicação por identificadores
fortes) · **Contrato:** Data Contract V1.0 §5 (`docs/data/DATA_CONTRACT_V1.md`) ·
**Motor:** `scripts/dedup/deduplicar_organizacoes.py` · **Ambiente:** dev (o motor recusa `prod` — ADR-005)

## 1. O que a regra manda (não inventado aqui)

O contrato §5 diz duas coisas sobre o score:

| Regra do contrato | Onde está no código |
|---|---|
| "Merge automático somente com `entity_match_confidence >= 0.95`" | `faixas_confianca()` (fronteira lida de `dedup.auto_merge_threshold`) + `decidir_por_confianca()` |
| "Abaixo disso: `REVIEW_REQUIRED` — fila humana, **nunca** merge silencioso" | faixa `REVISAO_HUMANA` → `human_approvals` (PENDING) |

O contrato **não** define faixas nem como o score é calculado — só o limiar e o que acontece abaixo dele.
O que este card acrescenta é declarado abaixo, item por item, e nenhuma regra nova foi inventada: as
fronteiras vêm do contrato e o piso de candidatura é decisão já registrada no E04-T01 (D4).

## 2. Como o score é calculado

`entity_match_confidence(a, b)` é a confiança de **duas organizações serem a mesma entidade**. Ela é
derivada da evidência que o E04-T01 já compara — não é um segundo cálculo que possa divergir do que
decide o merge: `avaliar_par()` usa o mesmo valor, e a decisão do par é a decisão da faixa do score.

| Evidência do par | Score | Por quê |
|---|---|---|
| Identificador forte válido idêntico (CNPJ / domínio / LinkedIn Company URL) | **1,00** | identidade provada por identificador forte |
| Identificador forte presente mas inválido (CNPJ igual que não passa no dígito verificador) | **0,94** (`teto_fraco`) | detecta, mas nunca mergeia automático (D3 do E04-T01) |
| Só evidência fraca qualificada (nome + cidade iguais, similaridade ≥ piso, sem conflito de UF) | **min(similaridade, 0,94)** | evidência fraca nunca alcança a faixa de merge (D2 do E04-T01) |
| Sem evidência de identidade qualificada | **0,00** | o score **não finge** identidade: a similaridade bruta de nome continua na evidência (`evidencias.fracos.similaridade_nome`) |

## 3. As faixas de confiança

Faixas **derivadas do contrato a cada chamada** (`faixas_confianca()`): a fronteira da faixa de merge é o
`dedup.auto_merge_threshold` do contrato e o piso é o de candidatura (0,80). Não há constante de fronteira
ajustável — mudar o limiar no contrato move as faixas junto, e o modelo **se recusa a operar** com um
contrato que não consiga representar (`piso < teto_fraco < limiar`). As faixas cobrem `[0, 1]` sem lacuna e
sem sobreposição, e isso é conferido por validador próprio (mesmo espírito do tiering de scores do contrato).

| Faixa | Intervalo (limiar vigente 0,95) | Decisão | O que cai nela |
|---|---|---|---|
| `MERGE_AUTOMATICO` | `[0.95, 1.00]` (limiar **inclusivo**) | `MERGE` | identificador forte válido e idêntico |
| `REVISAO_HUMANA` | `[0.80, 0.95)` | `REVIEW_REQUIRED` (fila humana) | evidência fraca no teto 0,94; identificador forte inválido; similaridade alta sem identificador |
| `SEM_DUPLICIDADE` | `[0.00, 0.80)` | `SEM_DUPLICIDADE` | sem evidência de identidade que qualifique candidatura |

**Teste de faixa do aceite (0,94 não mescla; 0,95 mescla)** — provado em três lugares:

1. função de decisão: `decidir_por_confianca(0.94) == REVIEW_REQUIRED` e `decidir_por_confianca(0.95) == MERGE`;
2. caminho do merge no ponto exato: `gerar_sql_merge` **recusa** uma avaliação de 0,94 (`ValueError`) e
   **gera** o SQL auditável de 0,95;
3. cenário real em dev: o par fraco (nome + cidade) para em **0,94** e vai para `human_approvals` sem
   mesclar; o par com o mesmo CNPJ (score 1,00) mescla de verdade.

**Fonte única na saída do `--faixas` (correção do defeito D02):** a tabela e a linha de detalhe saem do
**modelo** (`faixa_de_confianca()`), nunca de nome de faixa escrito no código. O defeito original tinha os
nomes fixos enquanto as decisões eram calculadas: com o limiar do contrato em 0,90 o comando imprimia
`MERGE_AUTOMATICO [0.90, 1.00]` na tabela e "0,94 cai em REVISAO_HUMANA" na linha de detalhe — **exit 0**,
o próprio artefato do critério 2 se contradizendo. Hoje `linha_detalhe_faixas()` deriva faixa e decisão da
mesma fonte que decide o merge, e a prova exige que tabela e detalhe **andem juntos** quando o limiar se move.

## 4. Onde o campo é persistido

O score é persistido **no registro auditado de cada decisão** — as duas estruturas que o contrato já
governa. Não há coluna nova: criar coluna exige nova versão do contrato (governança §10 + ADR-0004) e é
lacuna declarada (§6), não feita por conta própria.

| Decisão | Onde persiste | Campos gravados |
|---|---|---|
| `MERGE` | `sales_intelligence.sync_events.request_payload` | `entity_match_confidence`, `entity_match_confidence_faixa`, `entity_match_confidence_modelo`, `entity_match_confidence_faixas` (a tabela de faixas vigente), `limiar_vigente`, `politica`, `contrato` |
| `REVIEW_REQUIRED` | `sales_intelligence.human_approvals.proposed_action` | os mesmos campos |

- **Nome canônico:** `entity_match_confidence`. O nome do E04-T01 (`confianca`) permanece **no mesmo
  registro**, com o mesmo valor, como alias de compatibilidade — nenhum registro antigo ou verificador
  existente deixa de valer.
- **`SEM_DUPLICIDADE` não gera registro:** sem decisão não há o que auditar (o par não é candidato). É
  limite declarado, não persistência esquecida.
- **Conferência (o "registro persistido conferido" do plano de teste):**

```sql
-- merge: o score persistido e a faixa que o decidiu
SELECT created_at, operation, idempotency_key,
       request_payload->>'entity_match_confidence'        AS entity_match_confidence,
       request_payload->>'entity_match_confidence_faixa'  AS faixa,
       request_payload->>'entity_match_confidence_modelo' AS modelo,
       request_payload->>'confianca'                      AS alias_e04_t01
FROM sales_intelligence.sync_events
WHERE operation = 'MERGE' AND entity_type = 'organization'
ORDER BY created_at DESC;

-- fila humana: o mesmo campo no que ficou abaixo do limiar
SELECT requested_at, entity_id,
       proposed_action->>'entity_match_confidence'       AS entity_match_confidence,
       proposed_action->>'entity_match_confidence_faixa' AS faixa
FROM sales_intelligence.human_approvals
WHERE action_type = 'ORGANIZATION_MERGE_REVIEW' AND status = 'PENDING';
```

## 5. Como rodar a prova

```bash
# modelo de faixas impresso (fronteira = limiar do contrato) — não toca o banco
python3 scripts/dedup/deduplicar_organizacoes.py --faixas

# suíte do motor: faixa, score, coerência score<->faixa<->decisão e persistência (sem banco)
python3 scripts/dedup/deduplicar_organizacoes.py --autoteste

# prova negativa: sabotar a persistência, a coerência, o limiar e a linha de detalhe TEM de reprovar
python3 scripts/dedup/deduplicar_organizacoes.py --autoteste --sabotar persistencia   # coerencia | limiar | detalhe

# prova completa (faixas + suíte + negativa + cenário real em dev, com read-back do banco)
bash scripts/dedup/teste_entity_match_confidence.sh dev
```

A prova do modelo de faixas não assere string: `teste_entity_match_confidence.sh` calcula a expectativa de
0,94 e 0,95 **pelo próprio `faixa_de_confianca()`** do motor e, numa cópia com o limiar em 0,90, exige que
tabela e linha de detalhe se movam juntas (é o defeito D02 virado teste). O contrato do repositório não é
mutado — a mutação vive numa cópia temporária dentro do teste.

Na VPS do ambiente (ADR-0008 — quem fala com o PostgreSQL é a VPS). `--ambiente prod` é recusado com
`ADR-005`: nada nasce em produção.

## 6. Decisões de implementação (registradas no card; não mudam o contrato)

| # | Decisão | Racional |
|---|---|---|
| **D-T02-1** | Nome canônico `entity_match_confidence`, com `confianca` mantido como alias de mesmo valor no mesmo registro | o contrato e os artefatos do E04-T01 falam `entity_match_confidence`; renomear sem alias invalidaria o registro do E04-T01 e os verificadores que o leem |
| **D-T02-2** | A decisão do par passou a ser a decisão da faixa do score (3 estados: `MERGE` / `REVIEW_REQUIRED` / `SEM_DUPLICIDADE`) | o "abaixo do limiar → `REVIEW_REQUIRED`" do contrato vale para **candidato a deduplicação**; um par que nem candidato é (abaixo do piso, 0,80) não vira fila humana. Antes o score de um par não-candidato era a similaridade bruta (podia ser 1,00 num par sem cidade em comum) — score que não decidia nada |
| **D-T02-3** | Campo persistido no registro auditado (`sync_events` / `human_approvals`); **nenhuma coluna nova** | criar coluna é gatilho de nova versão do contrato (governança §10) + aprovação humana (ADR-0004) — não é decisão de um card de execução. O rollback proposto ("coluna fica nula") não se aplica: não existe coluna, e o registro de auditoria é imutável por contrato (§9) |
| **D-T02-4** | Sem evidência qualificada o score é **0,00**, não a similaridade de nome | o score é confiança de identidade; registrar 1,00 para "nomes iguais, cidades diferentes" seria valor que mente. A similaridade bruta continua auditável em `evidencias.fracos` |
| **D-T02-5** | O registro leva a tabela de faixas vigente e a versão do modelo | o registro tem de ser legível sem o código da época: quem auditar vê a régua, não só o número |
| **D-T02-6** | A linha de detalhe do `--faixas` — e a sua verificação — é **derivada do modelo**, nunca texto fixo | defeito D02 (achado na revisão independente): com os nomes das faixas fixos no código, o comando se contradizia quando o limiar do contrato mudava (tabela dizia uma faixa, o detalhe outra, exit 0) e o teste do projeto asseria a string constante como evidência do critério — provando por construção, mesma classe do D04 do E04-T01 |

## 7. Rollback

- **Código:** `git revert <sha>` — não há DDL a desfazer e o contrato não foi tocado. Os registros
  seguintes deixam de trazer o campo; os já gravados permanecem (auditoria é trilha, não rascunho).
- **Um valor errado já persistido:** não se edita a trilha de auditoria (§9). O caminho é
  `--desfazer-merge <idempotency_key>` (registra `UNMERGE` com a evidência) e, se o cálculo estiver errado,
  reverter o commit — o registro antigo fica como prova do que foi decidido sob a régua daquele momento.
- **Massa de teste:** `teste_dedup_ambiente.sh` limpa o que marcou (`cenario=tre-w1-e04-t01`) e confere a
  contagem por tabela contra o estado anterior.

## 8. Limites declarados

- **Sem coluna em `organizations`:** o score é do **par** (decisão de match), não um atributo solto da
  empresa — a casa natural é o registro da decisão. Se o dono quiser coluna, é mudança de contrato (v1.1)
  com aprovação registrada, e não este card.
- **Sem `SEM_DUPLICIDADE` persistido:** os pares sem candidatura não geram registro (ver §4).
- **Faixas acopladas ao limiar:** se o contrato for para um limiar ≤ 0,81 (ou para teto fraco ≤ piso), o
  modelo levanta erro em vez de operar com faixa sobreposta — falha fechada, com motivo, é o comportamento
  desejado.

## 9. Evidência medida

Registrada em `docs/operations/registro-de-execucoes.md` (entradas W1/E04-T02 e W1/E04-T02-D02) e no card
`t_430ba4cc`: suíte do motor com os itens de faixa/score/coerência/persistência, as sabotagens reprovando e o
cenário real em dev com o campo lido **de volta do banco** nos dois registros (merge e fila humana). A
correção do defeito D02 entrou com prova de dois lados: o código anterior (com o limiar em 0,90) **reprova**
a verificação nova e o código corrigido passa, além da sabotagem `detalhe` derrubando a suíte.
