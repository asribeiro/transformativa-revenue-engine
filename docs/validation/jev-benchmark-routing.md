# Benchmark anotado de roteamento do JEV — relatório de evidência

**Card:** TRE-W0-E04-T03 (depende de TRE-W0-E04-T02) · **Execução:** 30/09/2026 (UTC)
**Perfil executor:** `analista-teste` · **Roteador medido:** `jev-router-v1.0` (`2cb72ab4…`)
**Política usada:** `jev-policy-v1.0` (`0058db6e…`) · **Corpus:** `corpus-anotacao-v1.3` (`c5ced4fc…`)
**Commit-base da medição:** `bbb4fd9`

Artefatos versionados por este card:

| Artefato | sha256 (12) |
|---|---|
| `hermes/jev/benchmarks/corpus-anotacao.yaml` (v1.3, rotulado) | `c5ced4fc2a67` |
| `scripts/anotar_corpus_benchmark.py` (ferramenta da anotação + prova) | `fa41a0f3c68d` |
| `scripts/benchmark_roteamento.py` (benchmark reprodutível) | `ce90a065b887` |
| `hermes/jev/benchmarks/resultado-benchmark-2026-09-30-jev-policy-v1.0.json` (resultado) | `d6eaa7801fa5` |

Toda evidência abaixo é saída bruta de comando com `exit code`. Nenhum número foi estimado,
arredondado para melhorar ou copiado de outra rodada.

`<ws>` nas linhas de comando abaixo é o workspace do card:
`/opt/data/kanban/boards/transformativa-revenue-engine/workspaces/t_d8bc83b3` — onde ficam os
utilitários de prova (`montar_fixtures_negativos.py`, `comparar_rodadas.py`, `dump_casos.py`,
`agrupar_motivos.py`, `ver_motivo.py`) e os fixtures dos testes negativos.

---

## 1. Critério de aceitação → teste → evidência

| Critério (homologado por Anderson em 29/09/2026) | Teste que prova / reprova | Evidência | Veredito |
|---|---|---|---|
| Corpus com casos anotados por lane, anotação final homologada pelo Anderson | Conferência campo a campo: 32 casos, `lane_esperada` preenchido, proveniência no campo `nota`, campos protegidos intactos | `anotar_corpus.py` → `32/32 casos com lane_esperada homologada, 0 campo protegido alterado` | **OK** (com o limite do §6.1: 30 em bloco, 2 individuais) |
| Métricas calculadas e versionadas: accuracy, false downgrade, escalation rate, custo e latência | Script roda o roteador real sobre o corpus e recalcula as 5 métricas nos 2 modos de entrada | §4 (saída bruta) + `resultado-benchmark-…json` | **OK** |
| Caso não revisado fica **pendente**, nunca válido | Teste negativo: corpus com 1 rótulo vazio → o benchmark **recusa** medir (exit 3) | §5.1, `EXIT=3` | **OK** (32/32 homologados; nenhum pendente) |
| Resultado gravado em `hermes/jev/benchmarks/` com data e versão da política | Nome do arquivo e campos `gerado_em`/`politica.versao`/`politica.sha256` | §4 | **OK** |
| Sem afrouxar o D07 nem tocar `policy_v1.yaml` para o número melhorar | `git status`/`git diff` da política e do roteador durante a medição; o script **recusa** medir sem política válida (exit 3) | §5.1 e §8 | **OK** |

Test plan do card ("script de benchmark reprodutível; evidência = saída do script + arquivo de
resultado"): cumprido — `scripts/benchmark_roteamento.py` + o JSON de resultado versionados.

---

## 2. Contrato da anotação: o que mudou e o que **não** mudou

Executado exatamente o contrato do card (comentário de liberação de 29/09/2026):

- `lane_esperada` recebeu a proposta **homologada**: `lane_proposta_hermes` copiado para os **30
  casos vazios**; os 2 já rotulados individualmente (`real-t_969affa7`, `real-t_d9cb5755` = `high`,
  commit `842d62e`) **não foram tocados**;
- proveniência em cada um dos 30: `nota: lane homologada por Anderson em 29/09/2026 a partir da
  proposta do Hermes (corpus v1.2)`;
- `lane_proposta_hermes`, `sinais`, `acao` e `justificativa` **não** foram alterados (provado por
  comparação profunda campo a campo antes/depois);
- versão do arquivo subiu para `corpus-anotacao-v1.3` e `nota_da_versao` foi reescrita (a anterior
  dizia que os 30 seguiam pendentes — deixou de ser verdade).

```
$ /opt/hermes/.venv/bin/python <ws>/anotar_corpus.py
{
  "corpus": "hermes/jev/benchmarks/corpus-anotacao.yaml",
  "sha256_antes": "e99221a466caea985c52ca234d24801c2a863b708e822b541dd154e5edef80bc",
  "sha256_depois": "c5ced4fc2a678018cbebd3cc56b4ab627a2b15f515ccc145cd7d3d7a92155b8c",
  "casos": 32, "rotulados_agora": 30,
  "intocados": ["real-t_969affa7", "real-t_d9cb5755"],
  "por_lane": {"medium": 9, "high": 11, "small": 3, "critical": 9},
  "falhas": []
}
RESULTADO: OK — 32/32 casos com lane_esperada homologada, 0 campo protegido alterado
$ echo $?
0
```

(A ferramenta rodou do workspace e está versionada como `scripts/anotar_corpus_benchmark.py`; os
dois são o mesmo arquivo.)

Re-execução é segura e **para antes de escrever** — nenhum rótulo homologado é reescrito:

```
$ /opt/hermes/.venv/bin/python scripts/anotar_corpus_benchmark.py
NADA A FAZER: nenhum caso com lane_esperada vazio — o corpus ja esta anotado (versao=corpus-anotacao-v1.3). Arquivo NAO foi tocado.
sha256=c5ced4fc2a678018cbebd3cc56b4ab627a2b15f515ccc145cd7d3d7a92155b8c
EXIT=1
$ sha256sum hermes/jev/benchmarks/corpus-anotacao.yaml
c5ced4fc2a678018cbebd3cc56b4ab627a2b15f515ccc145cd7d3d7a92155b8c   # inalterado
```

As quatro lanes têm caso: 3 `small`, 9 `medium`, 11 `high`, 9 `critical`. A régua tem poder de
separação (se tudo fosse `critical`, a lane perderia função).

---

## 3. Convenção de entrada do benchmark (premissas declaradas, contestáveis)

O corpus **não** carrega `lane_proposta` nem `confianca`: quem fornece isso é o chamador. Para o
número ser auditável, a convenção está no cabeçalho do script e gravada no resultado. São medidos
**dois modos**, e os dois saem no arquivo:

| Modo | O que é a entrada | O que mede | Circular? |
|---|---|---|---|
| `classificador` | sem proposta: o roteador classifica o caso sozinho (`classificar_card`, casando o texto com `lanes.*.exemplos` do próprio YAML) | qualidade do classificador **do roteador** contra o rótulo humano | **não** |
| `proposta-homologada` | `lane_proposta` = proposta do corpus, `confianca` = 0,95 | se a precedência/limiares **preserva** a lane proposta ou a reenquadra | **sim** — o rótulo de 30 dos 32 casos foi homologado a partir dessa mesma proposta. Vale como controle de regressão do encanamento, **nunca** como prova de qualidade de classificação |

Demais premissas (iguais às de `scripts/analisar_impacto_de_afrouxar.py`, para comparabilidade com a
seção 8.9 do relatório do T04):

- sinais do corpus → sinais do roteador: `producao`→`producao`, `mexe_em_segredo`→`credencial`,
  `outbound_para_terceiro`→`outbound_a_terceiro`;
- `ddl_ou_migration` e `aprova_humana_exigida` **não** têm equivalente no roteador e **não** foram
  injetados (nome desconhecido = BLOCK; injetar sujaria a medição com um bloqueio que o caso não pede);
- `ambiente_alvo` = `producao` quando `sinais.producao`, senão `desenvolvimento`;
- o campo `acao` do caso é o texto da ação e **só** vira código canônico quando o próprio roteador o
  resolve — o benchmark **não** inventa código para card que não tem (inventar fabricaria o resultado).

---

## 4. Resultado

```
$ cd /opt/data/repos/transformativa-revenue-engine
$ /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --autoteste
corpus : hermes/jev/benchmarks/corpus-anotacao.yaml  versao=corpus-anotacao-v1.3  casos=32  sha256=c5ced4fc2a67
roteador: jev-router-v1.0  sha256=2cb72ab41172
politica: jev-policy-v1.0  sha256=0058db6edf1b  lane_conservadora=high  caminho=hermes/jev/policy_v1.yaml
politicas de papel carregadas: ['dev-harness', 'sales-ai']
peso de custo relativo por lane (classe vem do YAML): {'small': 1, 'medium': 2, 'high': 3, 'critical': 3}

--- modo classificador (nao circular: classificador do roteador vs rotulo humano)
  accuracy_de_lane            : 0.3438 (11/32)
  accuracy por lane esperada  : {'small': 0.0, 'medium': 0.0, 'high': 1.0, 'critical': 0.0}
  falso_rebaixamento (critical): 9 (dos quais executaveis: 0)
  taxa_de_escalacao           : 0.8125
  taxa_de_bloqueio / execucao : 0.2812 / 0.0
  custo relativo              : roteado=96 homologado=81 delta=15 (18.52%)
  latencia da decisao (ms)    : mediana=1.9521 p95=3.2811
  matriz de confusao          : {'small': {'high': 3}, 'medium': {'high': 9}, 'high': {'high': 11}, 'critical': {'high': 9}}
--- modo proposta-homologada (CIRCULAR contra o rotulo homologado: controle de regressao)
  accuracy_de_lane            : 0.4062 (13/32)
  accuracy por lane esperada  : {'small': 0.6667, 'medium': 0.0, 'high': 1.0, 'critical': 0.0}
  falso_rebaixamento (critical): 9 (dos quais executaveis: 0)
  taxa_de_escalacao           : 0.75
  taxa_de_bloqueio / execucao : 0.2812 / 0.0625
  custo relativo              : roteado=92 homologado=81 delta=11 (13.58%)
  latencia da decisao (ms)    : mediana=1.9395 p95=3.6488
  matriz de confusao          : {'small': {'small': 2, 'high': 1}, 'medium': {'high': 9}, 'high': {'high': 11}, 'critical': {'high': 9}}

ACHADO de vocabulario: 28 de 32 casos nao resolvem para codigo canonico de acao
rotulos: {'em_bloco': 30, 'individual': 2}
resultado gravado em hermes/jev/benchmarks/resultado-benchmark-2026-09-30-jev-policy-v1.0.json
```

### 4.1 Por caso (modo `proposta-homologada` — a lane registrada e o desfecho de cada um)

```
caso             rotulo    lane      outcome   decidido                           codigo                       exec
borda-01         small     small     PASS      executar                           ajuste_de_texto              True
borda-02         critical  high      BLOCK     bloquear                           nao_classificada             False
borda-03         critical  high      BLOCK     bloquear                           primeiro_contato_outbound    False
borda-04         critical  high      BLOCK     bloquear                           nao_classificada             False
borda-05         critical  high      BLOCK     bloquear                           nao_classificada             False
borda-06         critical  high      BLOCK     bloquear                           nao_classificada             False
borda-07         small     high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
borda-08         critical  high      BLOCK     bloquear                           nao_classificada             False
borda-09         critical  high      BLOCK     bloquear                           rollback_em_producao         False
borda-10         critical  high      BLOCK     bloquear                           nao_classificada             False
borda-11         small     small     PASS      executar                           ajuste_de_texto              True
borda-12         critical  high      BLOCK     bloquear                           nao_classificada             False
real-t_0248a568  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_11815e63  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_1acf11f2  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_2821c15b  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_33bc1765  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_4be20bcc  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_6267d886  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_6cc75a1d  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_9d38e360  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_ac8a2130  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_cdc21b43  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_d6dc5a4c  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_d9be7d3c  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_d9cb5755  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_e0489efc  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_e4a90eba  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_eb323dd7  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_f599bd02  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_fd3e41f0  medium    high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
real-t_969affa7  high      high      ESCALATE  escalar_acao_nao_classificada      nao_classificada             False
```

Leitura direta: **2 casos executam** (borda-01 e borda-11, ambos `ajuste_de_texto` em `small`), **9
bloqueiam** e **21 escalam**. Nenhum caso `critical` executa. A causa dominante da escalação (21 de
32) é o portão de código canônico de ação — não a lane proposta.

Reconciliação das taxas com os desfechos (para os dois números fecharem):

| Métrica | Modo `proposta-homologada` | Modo `classificador` | O que conta |
|---|---|---|---|
| `taxa_de_escalacao` | 0,75 (24/32) | 0,8125 (26/32) | casos com `exige_escalacao: true` = desfechos `ESCALATE` **mais** os `BLOCK` da camada de aprovação humana (borda-03, 05, 08) |
| desfechos `ESCALATE` | 21 | 23 | inclui, no modo `classificador`, borda-01/11 (abstenção por confiança 0,62) |
| `taxa_de_bloqueio` | 0,2812 (9/32) | 0,2812 (9/32) | desfechos `BLOCK` |
| `taxa_de_execucao` | 0,0625 (2/32) | 0,0 | `pode_executar: true` |

---

## 5. Testes negativos, fronteiras e provas de reprodutibilidade

### 5.1 Testes negativos do próprio benchmark (o caminho proibido tem de reprovar)

```
$ /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --corpus <ws>/fixtures/corpus-com-pendente.yaml
RECUSADO (gate de anotacao): 1 caso(s) sem lane homologada: ['real-t_ac8a2130']
Regra do card: caso nao revisado fica PENDENTE, nunca valido. O benchmark nao publica metrica sobre rotulo pendente.
EXIT=3

$ /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --politica /tmp/nao-existe.yaml
RECUSADO (gate de politica): politica ausente: /tmp/nao-existe.yaml
EXIT=3

$ /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --politica <ws>/fixtures/policy-versao-desconhecida.yaml
RECUSADO (gate de politica): versao de politica desconhecida: 'jev-policy-v9.9' (suportadas: ['jev-policy-v1.0'])
EXIT=3
```

Os três **reprovam** com exit 3: nenhum publica métrica sobre rótulo pendente nem sobre política que
o roteador não executa (modo degradado). O `exit code` é a evidência — não a narrativa.

### 5.2 Fronteira: o número depende do limiar do **YAML** (0,84 × 0,85)

```
$ ... --confianca 0.85   (== limiar de aceite)     -> accuracy proposta-homologada = 0.4062 (13/32)  EXIT=0
$ ... --confianca 0.84   (< limiar de aceite)      -> accuracy proposta-homologada = 0.3438 (11/32)  EXIT=0
   AVISO — sonda de fronteira: confianca 0.84 esta na faixa conservadora [0.65, 0.85) ...
$ ... --confianca 0.64   (< limiar conservador)    -> RECUSADO ... exit 3
```

Prova que (a) o limiar vem do YAML e não de literal no código, (b) a métrica é sensível na fronteira
(nos 0,84 os dois `small` caem para a lane conservadora) e (c) abaixo do limiar conservador o
benchmark recusa medir em vez de medir abstinência disfarçada de roteamento.

### 5.3 Autoteste do benchmark (a métrica tem de reagir, e os gates têm de reprovar)

```
$ /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --autoteste
  [OK] corpus: 32 casos, nenhum rotulo pendente — 32 casos, pendentes=[]
  [OK] gate: rotulo vazio e detectado como pendente — pendentes=['real-t_ac8a2130']
  [OK] corpus tem caso que o roteador acerta hoje (base do teste) — 13 casos
  [OK] metrica sensivel: accuracy cai exatamente 1/N ao desalinhar rotulo que acertava — real-t_4be20bcc: high -> critical: 0.4062 -> 0.375 (esperado 0.375)
  [OK] metrica sensivel: falso_rebaixamento reage ao mesmo rotulo — 9 -> 10
  [OK] corpus tem caso critical para o teste de sensibilidade
  [OK] metrica sensivel: falso_rebaixamento cai quando o caso deixa de ser critical — 9 -> 8
  [OK] metrica sensivel: rotulo uniforme 'small' derruba a accuracy — 0.0625 < 0.4062
  [OK] benchmark e read-only no corpus
  [OK] decisao local e barata (mediana < 50 ms) — 1.9395 ms
  [OK] confianca declarada >= limiar de aceite do YAML — 0.95 >= 0.85
  [OK] gate: politica ausente e recusada
AUTOTESTE: 12/12 itens OK, 0 falhas
$ echo $?
0
```

### 5.4 Reprodutibilidade (duas rodadas independentes)

```
$ ... --saida r1.json ; ... --saida r2.json ; /opt/hermes/.venv/bin/python <ws>/comparar_rodadas.py r1.json r2.json
REPRODUZIVEL: as duas rodadas batem em tudo menos latencia e carimbo de tempo
  classificador: accuracy=0.3438 falso_rebaixamento=9 escalacao=0.8125 custo=96/81
  proposta-homologada: accuracy=0.4062 falso_rebaixamento=9 escalacao=0.75 custo=92/81
EXIT_REPRO=0
```

### 5.5 Contraprova com o CLI do próprio roteador (fora do meu script)

Para a evidência não depender do meu harness, dois casos foram rodados pelo CLI do artefato:

```
$ /opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"borda-01","acao":"ajuste_de_texto","lane_proposta":"small","confianca":0.95}'
  "lane": "small", "outcome": "PASS", "pode_executar": true, "codigo_de_acao": "ajuste_de_texto",
  "origem_do_codigo_de_acao": "codigo_canonico", "politica_lida_de": ".../hermes/jev/policy_v1.yaml"
EXIT_borda01=0

$ /opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"borda-02","acao":"migration","ambiente_alvo":"producao","lane_proposta":"critical","confianca":0.95}'
  "lane": "high", "outcome": "BLOCK", "guardrails_acionados": ["ddl_fora_de_producao"],
  "motivos": ["guardrail ddl_fora_de_producao: DDL nao nasce em producao (migration declarada no texto da tarefa (termo 'migration'/'migracao'): 'migration'; ambiente alvo producao)"]
EXIT_borda02=3
```

Confere com o resultado do benchmark para os dois casos.

---

## 6. Achados (o que a medição expôs)

### 6.1 A1 — 28 dos 32 casos escalam por **desencontro de vocabulário**, não pela lane

`acao: execucao_de_card` (20 cards reais) não é código do catálogo do roteador
(`ajuste_de_texto`, `consulta_interna`, `operacao_comercial`, `migracao_de_esquema`); 8 dos 12 casos
de borda também não resolvem. Resultado: 21 dos 32 casos escalam com o motivo
`"acao nao classificada com seguranca"`. Isso é a postura estrita homologada (D07) operando como
desenhada — **não** é efeito da lane. Quem destrava o corpus é a ponte de códigos (T05/política
v1.1), e a medição do efeito dela é a **próxima rodada deste mesmo benchmark** (mesmo corpus, mesmos
rótulos). O número de hoje é a linha de base honesta.

### 6.2 A2 — o classificador do roteador devolve `high` nos 32 casos

Matriz de confusão do modo `classificador`: **todos os 32 casos → `high`**. A accuracy de 0,3438 é
exatamente a do palpite constante "sempre `high`" (11/32). Neste corpus, o classificador automático
**não agrega** sobre a constante: ou não acha evidência (confiança 0,50 → abstém e escala) ou acha
pouca (0,62 < 0,65 → abstém). O caminho de classificação que funciona hoje é o **chamador passar a
lane**; é o que o T05 vai fazer com o código canônico.

### 6.3 A3 — falso rebaixamento: 9 no número cru, **0 executáveis**

Os 9 casos `critical` registram lane `high` no recibo — mas em **todos** o card foi bloqueado ou
escalado (`pode_executar: false`): nenhum `critical` rodou em lane barata. O `lane` de um recibo
BLOCK/ESCALATE é a **lane conservadora registrada para auditoria** (`_lane_segura` → `high`), não uma
decisão de roteamento. Por isso o resultado publica **os dois** números, com o cru na frente:
`falso_rebaixamento.total = 9` e `falso_rebaixamento.executaveis = 0`. O segundo explica; o primeiro
é o headline e não foi maquiado.

### 6.4 A4 — o custo **não** enxerga rebaixamento `critical → high`

As classes de custo do YAML dão `alto` tanto ao perfil de `high` (`reasoning-forte`) quanto ao de
`critical` (`critical-frontier-reviewer`): peso 3 para os dois. Consequência: um rebaixamento
`critical→high` não move o custo — quem enxerga é `falso_rebaixamento`. O custo relativo medido
(`roteado=92` × `homologado=81`, +13,58%) mostra apenas o desvio de lanes e **não é dinheiro**: a
própria política proíbe fixar preço/modelo (`regra_catalogo`), então qualquer cifra em reais seria
invenção.

### 6.5 A5 — 5 dos 32 bloqueios citam uma regra que não descreve a causa

| Caso | Regra no recibo | Causa real (campo `detalhe`) |
|---|---|---|
| borda-04 | Sales AI não tem credencial de deploy… | papel `sales-ai` não pode: acessar credencial de infraestrutura ou do host |
| borda-06 | idem | papel `dev-harness` não pode: conceder aprovação humana no lugar do Anderson |
| borda-09 | idem | papel `sales-ai` não pode: executar deploy, promoção de release ou rollback |
| borda-10 | idem | papel `dev-harness` não pode: contatar lead, cliente ou decisor |
| borda-12 | idem | papel `dev-harness` não pode: contatar lead, cliente ou decisor |

O guardrail `papel_sem_credencial_de_deploy` declara a regra "Sales AI não tem credencial de deploy
nem altera código", mas **verifica a matriz Dev × Sales inteira** — inclusive contato outbound. O
`detalhe` diz a causa verdadeira; a **regra** é mais estreita do que o que o guardrail executa. Não é
defeito do roteador (ele é fiel à declaração da política congelada): é a prosa da política sendo mais
estreita que o contrato — insumo do card **TRE-W0-E04-T06** (política v1.1). Registrado como
comentário naquele card, sem abrir card de defeito (`severidade média`, "artefato registra informação
imprecisa"), para não duplicar escopo.

### 6.6 A6 — dois sinais do corpus não existem no roteador

`ddl_ou_migration` e `aprova_humana_exigida` não têm equivalente em `SINAIS_CONHECIDOS`: são
injetados como sinal desconhecido = BLOCK, então **não foram injetados**. O `ddl_ou_migration` acaba
entrando por outra via (o guardrail de DDL lê o **texto** declarado — foi assim que borda-02
bloqueou); `aprova_humana_exigida` não entra por via nenhuma. Enquanto o corpus e o roteador falarem
vocabulários de sinal diferentes, parte do caso anotado não é exercitada. Insumo do T05/T06.

---

## 7. O que **não** ficou provado (limites, com todas as letras)

1. **A latência medida é a da camada de decisão** (`decidir()`, mediana 1,94 ms), não a de execução
   por lane. Latência de execução depende do modelo do catálogo no momento da configuração, que a
   política proíbe fixar — medir isso aqui seria medir a máquina de hoje e chamar de política.
2. **`custo_por_card_VERIFIED` não é medido**: é o objetivo declarado na política, mas nenhum card do
   corpus foi executado até VERIFIED. Medido é o custo **relativo declarado** por perfil de lane.
3. **Os rótulos não são julgamento humano independente caso a caso**: 30 dos 32 foram homologados
   **em bloco** a partir da proposta do Hermes (2 individualmente, commit `842d62e`). O corpus mede
   **regressão contra uma linha de base acordada**, não concordância com régua humana independente.
   A revisão individual dos 30 segue pendente do Anderson — está escrita no `nota_da_versao` do
   corpus e o resultado marca `revisao_dos_rotulos: {em_bloco: 30, individual: 2}`.
4. **A accuracy de 0,4062 no modo `proposta-homologada` é circular** por construção: o rótulo veio da
   mesma proposta. O número não circular é o do modo `classificador` (0,3438).
5. **Os 20 cards reais do corpus não foram executados**: o benchmark mede a **decisão**, não o
   resultado do trabalho. Nada de "regressão/retrabalho por card" foi medido.
6. **Casos com `lane` registrada em BLOCK/ESCALATE não são roteamento**: contá-los como erro de lane
   infla o denominador da accuracy; contá-los como acerto esconderia o "não roteado". Publicado dos
   dois jeitos (`accuracy_de_lane` e o diagnóstico `entre roteados`), com o cru na frente.

---

## 8. Concorrência no worktree e hotspots

- A medição rodou em `/opt/data/repos/transformativa-revenue-engine` (branch `develop`), com o
  roteador e a política **no estado do commit `bbb4fd9`** (`router.py` `2cb72ab4…`, `policy_v1.yaml`
  `0058db6e…`). O benchmark é read-only no corpus, na política e no roteador; o autoteste prova isso
  (`[OK] benchmark e read-only no corpus`) e o resultado embute os três `sha256`.
- **Hotspot declarado:** no momento do commit havia trabalho de outro card no mesmo worktree
  (`TRE-W0-E04-T05`: `hermes/jev/gate/`, `hermes/jev/acoes-declaradas.yaml`, `scripts/*gate*`,
  `docs/runbooks/gate-jev-do-dispatch.md`, `docs/validation/jev-gate-no-dispatch.md` e
  `docs/operations/registro-de-aprovacoes.md` **modificado**). O commit deste card foi feito por
  caminho explícito, arquivo por arquivo — **nunca `git add -A`**: em 29/09/2026 um `git add -A` de
  outra frente arrastou arquivos alheios para dentro de um commit (registrado no relatório do T04,
  seção 8). `hermes/jev/routing/router.py` e `hermes/jev/policy_v1.yaml` **não** foram tocados por
  este card.
- Reexecução: `/opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py` (o `--autoteste`
  acrescenta as 12 provas do §5.3).

---

## 9. Parecer do analista de teste

**O que está provado, com comando e exit code:** corpus anotado nas quatro lanes com proveniência
registrada caso a caso; as cinco métricas do critério calculadas por um script reprodutível (duas
rodadas batem em tudo menos relógio); resultado versionado com data, versão e hash da política e do
roteador; o benchmark **recusa** medir rótulo pendente e política que o roteador não executa (3
testes negativos, exit 3); a métrica reage na fronteira 0,84 × 0,85 do limiar do YAML; a decisão de
dois casos confere pelo CLI do próprio roteador.

**O que não aceito como "passou":**

- a leitura de que a accuracy baixa é falha do roteador — **não é**: é o portão de código canônico
  (T05) e o vocabulário do corpus v1.2. O roteador, no que é dele, é conservador e não deixa passar
  nada grave: **0 rebaixamento executável** em 32 casos;
- a leitura de que a accuracy de 0,4062 mede qualidade de classificação — **não mede**: é circular
  (§7.4). A leitura honesta é "o encanamento preserva a lane proposta quando pode rotear (2/2), e
  recusa rotear em 30/32 por falta de código de ação";
- o número cru de `falso_rebaixamento = 9` como se 9 casos críticos tivessem caído em lane barata
  (§6.3) — publicado assim mesmo, com o subconjunto executável ao lado, para não maquiar.

**Pendência que não é minha para fechar (é decisão do dono):** a revisão **individual** dos 30
rótulos homologados em bloco. Não abre card: é julgamento humano do Anderson sobre uma régua de
negócio, e o contrato de 29/09/2026 já homologou o conjunto — mas fica escrito no corpus, no
resultado e aqui, para não passar como se cada caso tivesse sido lido um a um.

**Recomendação de sequência:** rodar este mesmo benchmark **depois** do T05 (códigos declarados) e
**depois** da política v1.1 (T06), mantendo corpus e rótulos: a diferença entre as duas rodadas é a
medida do efeito do encaixe, com a mesma régua.
