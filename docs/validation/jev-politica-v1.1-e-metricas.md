# Política JEV v1.1 (rascunho) e métricas por lane instrumentadas — evidência

**Card:** `TRE-W0-E04-T06` (t_e9535df3) · **Data:** 30/09/2026 · **Autor:** Hermes (perfil `default`)
**Escopo autorizado pelo Anderson (30/09/2026, Telegram):** (1) chave explícita `limiares.lane_conservadora`
na v1.1, com documento e verificador no mesmo rito da v1.0; (2) instrumentar as métricas de custo e latência
por lane; (3) carregar a convenção de lane de 29/09 (DDL em ambiente novo/dev = `high`; DDL em ambiente vivo
= `critical` com aprovação humana registrada).
**Fora de escopo (declarado em card próprio, não vinculado como pré-requisito):** os 28 de 32 casos do corpus
sem código canônico de ação e o classificador empatado com a constante sempre-`high`.

---

## 1. O que foi entregue

| Artefato | Papel |
|---|---|
| `hermes/jev/policy_v1_1.yaml` | **rascunho** da política v1.1 (não homologado, não em vigor) |
| `docs/architecture/jev-decision-policy-v1.1.md` | documento da v1.1, autossuficiente, com a seção de mudanças |
| `scripts/verificar_jev_policy_v1_1.py` | verificador da v1.1: roda a bateria da v1.0 inteira (42 itens) + 80 itens novos (57 de contrato, 9 de documento, 13 de comportamento, 1 de sanidade do YAML) + autoteste por mutação (33 mutações) |
| `scripts/medir_metricas_por_lane.py` | instrumento das métricas declaradas em `metricas` (custo e latência por lane) |
| `scripts/verificar_jev_policy.py` | **uma linha de contrato a mais**: `verificar()` passou a aceitar a versão esperada como parâmetro (default preserva a v1.0) — é o que permite a v1.1 passar pela MESMA bateria |
| `hermes/jev/benchmarks/metricas-por-lane-2026-09-30-jev-policy-v1.0.json` | resultado medido do instrumento, versionado |

**O roteador (`hermes/jev/routing/router.py`) NÃO foi tocado** — e isso é decisão, não esquecimento: ver §5.

## 2. Estado: rascunho inerte, por construção

A v1.1 **não está em vigor** e o verificador prova isso em dois itens independentes:

- o roteador em vigor (`jev-router-v1.0`) aceita exatamente `['jev-policy-v1.0']` — carregar a v1.1 levanta
  `PoliticaInvalida: versao de politica desconhecida: 'jev-policy-v1.1'` (itens B3 e B3b);
- com o portão de versão **aberto em memória**, a v1.1 carrega e resolve a mesma lane conservadora
  (`high`) — ou seja, o portão é o único impedimento, não um defeito de schema (item B3-sensibilidade).

Consequência declarada no YAML (`regra_de_lane_por_ambiente.execucao.roteador_que_a_executa: null`) e provada
por comportamento: **o piso por ambiente ainda não é aplicado**. Um card com DDL declarada em ambiente vivo
(ficha: `acao_codigo: migracao_de_esquema`, `ambiente_alvo: vivo`, proposta `medium`, confiança 0,95) é
decidido com `lane = high` — a regra declarada manda `critical` (item B5). Quem afirma que a regra está ativa
está afirmando algo falso, e a suíte diz por quê.

## 3. A chave explícita é contrato, não prosa (provas B1/B2)

O que decide se "legível por máquina" é verdade não é a existência do campo: é o roteador conseguir resolver
a lane **sem** a prosa. Medido, em cópia temporária da política (o arquivo versionado nunca é tocado):

| Prova | Entrada | Resultado |
|---|---|---|
| B1a | v1.0 + `limiares.lane_conservadora: high`, **sem** as duas prosas | `_lane_conservadora = 'high'` — a chave sozinha resolve |
| B1b | v1.0 **sem** chave e **sem** prosa | recusa: *"nao foi possivel resolver a lane conservadora declarada (fontes lidas: nenhuma)"* — é exatamente a dependência de prosa que existia até aqui |
| B2 | chave `medium` contra `fallback.acao: high` | recusa: *"politica inconsistente: lane conservadora declarada de formas divergentes ['high', 'medium']"* |

B1b e B2 exigem a recusa **pelo motivo certo** (`nao declara o resultado` no motivo = falha), para o item não
passar por acidente.

## 4. Métricas declaradas e finalmente medidas

`python3 scripts/medir_metricas_por_lane.py` sobre o corpus v1.3 (32 casos), política **em vigor** (v1.0,
`sha256=0058db6edf1b…`), roteador `jev-router-v1.0`, 15 repetições por caso:

```
lane       classe   peso  decisoes   custo  lat.mediana(ms)  lat.p95(ms)
small      baixo       1         2       2           1.3839       1.3952
medium     medio       2         0    None             None         None
high       alto        3        30      90            1.484       1.7288
critical   alto        3         0    None             None         None

custo relativo total (corpus) : 92 (unidade declarada, NAO dinheiro)
taxa de abstencao / escalacao : 0.6562 / 0.75
falso rebaixamento (critical) : 9
blindagem de custo            : high=alto critical=alto mesma_classe=True
```

Status de cada métrica que a política declara em `metricas.acompanhar`:

| Métrica | Status | Fonte / motivo |
|---|---|---|
| `custo_por_lane` | **medida** | classe `custo` do perfil da lane, lida do YAML (na v1.0 não estava declarada em `acompanhar`; a v1.1 a declara) |
| `latencia_por_lane` | **medida** | relógio de `decidir()`, camada de decisão |
| `taxa_de_abstencao`, `taxa_de_escalacao`, `falso_rebaixamento` | **medidas** | execução do roteador sobre o corpus |
| `custo_por_card_verified` | **não medida** | nenhum card do corpus foi executado até VERIFIED e o recibo não carrega custo |
| `regressao_ou_retrabalho` | **não medida** | exige histórico de execução de card, que o encanamento ainda não registra |

A honestidade é item de teste, não de estilo: o autoteste reprova se uma métrica declarada não sair no
resultado, se uma métrica não medida sair sem motivo e se **uma lane sem decisão reportar zero em vez de
`null`** (`medium` e `critical` saem `None` — "não medi" ≠ "medi zero").

### 4.1 O limite que o instrumento recusa a esconder

`high` e `critical` têm a **mesma** classe de custo (`alto`) na política. O custo por lane, portanto, **não
enxerga rebaixamento `critical → high`** — quem enxerga é `falso_rebaixamento` (9 casos no corpus). O
resultado carrega isso como **dado medido** (`blindagem_de_custo.high_e_critical_mesma_classe: true`), não
como nota de rodapé: se as classes deixarem de ser iguais, o próprio resultado acusa. Sem isso, a métrica de
custo reportaria "sem problema" num cenário de rebaixamento crítico — foi o insumo nº 2 do benchmark do
`TRE-W0-E04-T03`.

As decisões **reais** também entram: 2 recibos lidos de `hermes/jev/receipts/`, com custo por lane derivado
de `lane → perfil → classe`. Latência a partir do recibo é declarada **não medida** — o contrato do recibo
tem 13 campos e nenhum deles carrega latência (e o contrato não foi esticado para caber: o verificador
reprova um 14º campo).

## 5. Por que o roteador não foi tocado (e o que isso implica)

A tentação era implementar o piso por ambiente no roteador junto com a v1.1. Não foi feito, por duas razões
medidas:

1. **Rito.** A v1.0 nasceu como contrato (T01) e só depois ganhou executor (T02). Mudança de
   comportamento sem a palavra do dono é exatamente o que a seção 10 da política proíbe.
2. **O portão de versão é a guarda, não o obstáculo.** Se a v1.1 entrasse em `VERSOES_DE_POLITICA_SUPORTADAS`
   sem o piso implementado, o roteador **ignoraria em silêncio** uma regra declarada — pior que recusar, e a
   mesma classe de defeito que o D08 já custou a este projeto (regra declarada que não é a regra executada).

Entrar em vigor exige, no mesmo movimento: (a) o registro do Anderson em `docs/operations/registro-de-aprovacoes.md`,
(b) `homologacao.registrada_em` preenchido no YAML, (c) a implementação do piso no roteador pelo card
`TRE-W0-E04-T07` (`t_d36c7d0f`, criado por este card e **blocked** de propósito) — que também abre o portão de
versão. O verificador reprova o estado (b) **sem** o registro (a).

O que a v1.1 traz pronto para esse dia: o vocabulário de ambiente declarado (`desenvolvimento`, `dev` ×
`vivo`, `producao`) foi conferido **valor a valor** contra o guardrail de DDL do roteador em vigor (provas
B4): os valores do ramo dev não acionam `ddl_fora_de_producao`, os do ramo vivo acionam, e ambiente não
declarado aciona — o mesmo fail-closed que o roteador já pratica.

## 6. Evidência bruta (comandos e resultado)

| Comando | Resultado |
|---|---|
| `/opt/hermes/.venv/bin/python scripts/verificar_jev_policy.py --autoteste` | `PASS (42 itens, 0 falhas) + autoteste OK` (12/12 mutações) |
| `/opt/hermes/.venv/bin/python scripts/verificar_jev_router.py --autoteste` | `PASS (59 itens, 0 falhas) + autoteste OK` (21/21 mutações) |
| `/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py` | `itens: 74 … falhas: 0 … achados: 0` → `PASS (74 itens, 0 falhas)` |
| `/opt/hermes/.venv/bin/python scripts/verificar_jev_policy_v1_1.py --autoteste` | `PASS (122 itens, 0 falhas) + autoteste OK` (33/33 mutações) |
| `/opt/hermes/.venv/bin/python scripts/medir_metricas_por_lane.py --autoteste` | `instrumento: OK`, autoteste `10/10 itens OK` |
| `/opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py` | `benchmark: OK` |

As suítes da v1.0 e do roteador continuam verdes **sem alteração de comportamento**: a mudança em
`verificar_jev_policy.py` é um parâmetro com default, e o roteador não foi editado.

Mutações que o verificador da v1.1 precisa reprovar (33, todas detectadas): chave removida; chave divergindo
de cada uma das prosas; chave apontando lane inexistente; prosa apontando lane barata; regra por ambiente
removida; ramo dev virando `critical`; ramo vivo sem aprovação humana; ramo vivo virando `high`; ramo dev
recebendo valor de produção; ambiente não declarado caindo no ramo dev; regra deixando de ser piso; regra se
declarando executada por roteador inexistente; card de implementação removido, com id malformado, fora da
prosa de `entra_em_vigor_com` e apontando card que não existe no board; rascunho se declarando homologado sem
registro; estado trocado para homologado; `substitui` removido; motivo da versão esvaziado; `custo_por_lane` e
`latencia_por_lane` fora de `acompanhar`; instrumento inexistente; motivo de não-medibilidade removido;
limite da cegueira de custo removido; `campos_fora_do_contrato` removido; 14º campo no recibo; e cinco
mutações no documento.

Um item merece nota porque ele não é sobre o YAML: **o card citado existe no board** (lido do SQLite, em modo
somente-leitura). Decisão que não viaja para o card seguinte se perde na primeira pressão — a v1.1 aponta
nominalmente para `TRE-W0-E04-T07` (`t_d36c7d0f`, `status=blocked`, assignee `desenvolvedor`), e o verificador
confere que o id citado está lá.

## 7. Limites honestos desta entrega

- **A v1.1 não roda.** Nada muda em decisão de card até a homologação e o card de implementação.
- **A medição é a da política em vigor (v1.0).** A comparação v1.0 × v1.1 sobre o mesmo corpus (o "efeito
  medido" da mudança) só existe depois que o roteador souber executar a v1.1 — o instrumento já lê as
  definições de qualquer política que lhe derem (`--politica`), mas a versão precisa passar pelo portão.
- **Latência é da camada de decisão**, não da execução do modelo por lane (o modelo do catálogo não é fixado
  pela política, por regra).
- **Custo não é dinheiro** e `custo_por_card_verified` (o objetivo declarado) segue não medido.
- **Achado fora do escopo deste card, registrado:** `scripts/verificar_gate_jev.py` (suíte do
  `TRE-W0-E04-T05`) reprova 1 de 28 itens já em `HEAD` (`b576566`), sem relação com esta entrega: o item 26
  exige `verificados >= 6` recibos quando a própria enumeração da suíte tem 8 cenários, dos quais 3 deixam
  registro de falha do encaixe (`FALHA-DO-GATE.json`) e 5 deixam recibo de 13 campos. Medido: 5 ≠ 6. Vai em
  card de defeito próprio, com a evidência.
