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

> **Adendo 30/09/2026 (card TRE-W0-E04-T03-D01):** o corpus foi anotado com o **código canônico de
> ação** (v1.4, `b66ecd36…`) e o benchmark foi re-medido com instrumento estendido. O estado
> **atual** do corpus/instrumento e as provas estão na **§10** (no fim do documento). O corpo
> principal é a medição da v1.3 e segue valendo como linha de base (números idênticos, ver §10.4).

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

---

## 10. Adendo — código canônico de ação no corpus (card TRE-W0-E04-T03-D01, 30/09/2026)

Fecha a lacuna A1 (§6.1). **Corpus v1.4** (`b66ecd36bd80`), política `jev-policy-v1.1`
(`4fe3357c1a9c`), roteador `jev-router-v1.1` (`6f44335de494`). Ferramenta da anotação:
`scripts/anotar_codigos_de_acao_no_corpus.py` (ensaio + real, com guardas de recusa).
**Nada de rótulo foi tocado** (§10.2, C5/C6). **Commit da entrega:** `e34c9ea`.

### 10.1 O que foi entregue, por critério do card

| Exigência do card | Entrega | Evidência |
|---|---|---|
| (1) código canônico por caso, com proveniência, ou registro de "não executável por desenho" | 11 casos com código (5 comuns, 6 proibidos) + 21 com `acao_codigo: null` explícito e o motivo na `nota` | §10.2 C1–C4; `dump_anotacao.py` |
| (2) re-medição com o MESMO instrumento, resultado versionado, com sha256 dos três insumos | `scripts/benchmark_roteamento.py` (estendido) + `resultado-benchmark-2026-09-30-jev-policy-v1.1-corpus-anotacao-v1.4.json` | §10.4; §10.5 |
| (3) decisão declarada sobre o classificador, com a medição | **manter** — e **não** ajustar o estimador (ganho medido zero) | §10.6 |

**Fechamento dos 28 casos do card:** dos 28 que a medição de 30/09 registrou sem código resolvido,
**7** ganharam código canônico declarado — `real-t_969affa7` e `real-t_d9cb5755` →
`migracao_de_esquema`; `borda-02` → `migracao_de_esquema`; `borda-04` →
`rotacao_ou_revogacao_de_credencial`; `borda-05` → `publicacao_em_nome_da_transformativa`;
`borda-08` → `exclusao_de_dado_de_cliente`; `borda-12` → `mudanca_estrutural_de_arquitetura` — e
**21** ficaram com `acao_codigo: null` explícito + motivo no `nota` ("não executável por desenho",
falha fechada D07). Os 4 casos que **já** resolviam por código em v1.3 (`borda-01`, `borda-11`,
`borda-03`, `borda-09`) passaram a **declarar** o campo: 32/32 anotados = 11 com código + 21 `null`.

### 10.2 Critério → teste → evidência (verificação independente, fora do script que anotou)

`<ws2>` = `/opt/data/kanban/boards/transformativa-revenue-engine/workspaces/t_dfcfc4d4`.

```
$ /opt/hermes/.venv/bin/python <ws2>/verificar_anotacao.py    # exit 0
RESULTADO: PASS (0 falha(s), 6 achado(s))
```

| # | Checagem | Resultado |
|---|---|---|
| C1 | 32/32 casos declaram `acao_codigo` (presente, mesmo que `null`) | PASS (`sem campo: []`) |
| C2 | todo código declarado é do catálogo vigente (4 comuns ∪ 8 proibidos) | PASS (`fora: []`) |
| C3 | proveniência da anotação na `nota` dos 32 casos | PASS (`sem proveniência: []`) |
| C4 | caso sem código registra "NAO EXECUTAVEL por lacuna de catalogo" | PASS (21 casos, 0 sem registro) |
| C5 | nenhum `lane_esperada` homologado alterado (v1.3 → v1.4) | PASS (`alterados: []`) |
| C6 | nenhum campo além de `acao_codigo`/`nota` mudou caso a caso | PASS (`campos mexidos: {}`) |
| C6b/c | nenhuma seção nova/removida; versão subiu para v1.4 | PASS |
| C7 | ação **proibida** nunca executa (decisão humana) | PASS (`violações: []`) |
| C7b | caso **sem código** nunca executa (falha fechada D07) | PASS (`violações: []`) |
| C8a | a anotação **não mudou a lane de nenhum caso** (v1.3 → v1.4) | PASS (`mudanças: {}`) |
| C8b | nenhuma divergência de lane foi introduzida pela anotação | PASS (`novas: []`) |
| C8c | divergências pré-existentes registradas como achado, sem corrigir rótulo | PASS (6 casos, §10.7 A7) |
| C9 | o `acao_codigo` declarado chega ao roteador como `codigo_canonico` | PASS (11/11) |
| C10 | a `nota_da_versao` cita o movimento **medido** (4→11, 2→4), não número de memória | PASS |

Caminho proibido reprovando (transcrições reais; a ferramenta nunca escreve quando recusa):

```
$ .../python scripts/anotar_codigos_de_acao_no_corpus.py --corpus <baseline v1.3> --mapa <mapa> --ensaio
mapa    : 11 caso(s) com codigo + 21 caso(s) sem codigo no catalogo = 32 casos
ENSAIO: 32 casos seriam anotados; texto montado parseia (32 casos, versao corpus-anotacao-v1.4); arquivo NAO escrito (sha256 intacto=c5ced4fc2a67)

$ .../python scripts/anotar_codigos_de_acao_no_corpus.py            # exit 0
  escrito: 32/32 casos com o campo; versao=corpus-anotacao-v1.4 (b66ecd36bd80)

$ .../python scripts/anotar_codigos_de_acao_no_corpus.py            # exit 1
NADA A FAZER: os 32 casos ja declaram `acao_codigo` (versao=corpus-anotacao-v1.4). Arquivo NAO foi tocado.
sha256=b66ecd36bd80...                                              # inalterado

$ .../python scripts/anotar_corpus_benchmark.py                     # exit 1
NADA A FAZER: nenhum caso com lane_esperada vazio — o corpus ja esta anotado (versao=corpus-anotacao-v1.4).

$ .../python <ws2>/provas_negativas_anotacao.py                     # exit 0
baseline pre-anotacao (git HEAD): versao=corpus-anotacao-v1.3 casos=32 com_codigo=0
  - codigo inventado fora do catalogo ....... RECUSOU=sim ARQUIVO_INTOCADO=sim
  - caso sem anotacao declarada ............. RECUSOU=sim ARQUIVO_INTOCADO=sim
  - caso declarado nas duas listas .......... RECUSOU=sim ARQUIVO_INTOCADO=sim
  - codigo sem proveniencia ................. RECUSOU=sim ARQUIVO_INTOCADO=sim
PROVA NEGATIVA: 4/4 testes recusaram sem tocar o arquivo

$ .../python <ws2>/provar_guarda_duplicidade.py                     # exit 0
RECUSADO (anotacao): caso borda-01 ja declara `acao_codigo` — anotacao duplicada recusada
RESULTADO: PASS — a guarda de duplicidade por caso reprova o caminho proibido
```

**Nota de método (defeito do próprio harness, encontrado e corrigido):** a primeira versão das
provas negativas copiava o corpus de **trabalho** para o fixture. Depois da anotação, essa cópia já
vinha anotada, a guarda de arquivo disparava primeiro e as quatro recusas específicas **nunca eram
exercidas** — o harness reportava `0/4` (falha) e a evidência não provava o que dizia provar. Agora o
baseline é regenerado de `git show HEAD:...` (v1.3, `com_codigo=0`) com auto-checagem que **falha se
o baseline já tiver código**. Lição aplicada: prova negativa que depende do estado do arquivo de
trabalho deixa de provar quando o arquivo muda.

**Nota de método 2 (falha real da ferramenta, encontrada pela prova):** a primeira versão de
`scripts/anotar_codigos_de_acao_no_corpus.py` escreveu um YAML **inválido** (indentação do campo
`nota`) e o corpus ficou quebrado até a leitura de prova reprovar. A ferramenta passou a (a) **parsear
o texto montado antes de escrever** — versão, número de casos e presença do campo em 32/32 têm de
conferir, senão `RECUSADO` e nada é escrito — e (b) **restaurar o texto original** se qualquer
verificação posterior à escrita falhar (`caminho.write_text(texto_antes)`). Ou seja: a escrita não
pode deixar o corpus pior do que estava, e o ensaio acima já é essa checagem.

### 10.3 As duas populações (o que o card exigia: medir abstenção **em separado**)

Publicado em `populacoes` no resultado, derivado do **desfecho** do roteador (nunca de rótulo
escrito à mão), partição exata dos 32 (C-item "os 32 casos aparecem exatamente uma vez"):

| População | Casos | Leitura |
|---|---|---|
| **EXECUTÁVEL** (código comum aceito) | **4** — `real-t_969affa7`, `real-t_d9cb5755`, `borda-01`, `borda-11` | accuracy de lane **1,0** |
| NÃO EXECUTÁVEL — ação proibida (decisão humana) | 6 (`borda-03/04/05/08/09/12`) | 6 BLOCK, 3 escalam |
| NÃO EXECUTÁVEL — sem código no catálogo | 21 (18 `execucao_de_card` + `borda-06/07/10`) | 19 escalam, 2 BLOCK |
| NÃO EXECUTÁVEL — bloqueio por regra, **com** código comum | 1 (`borda-02`, DDL em produção) | 1 BLOCK |

**Poder de medição:** com código comum (executável + bloqueio por regra = 5 casos) a accuracy de lane
é **1,0 (5/5)**; o número cru sobre os 32 é **0,4375**. A diferença entre os dois **é** a abstenção
causada pela lacuna de código — agora visível em campo próprio, em vez de diluída no número cru.

### 10.4 Movimento (mesmo instrumento, corpus v1.3 → v1.4)

R1 = linha de base versionada (instrumento anterior) · R2 = corpus v1.3 **regenerado do git** e rodado
com o instrumento novo (controle do instrumento) · R3 = corpus v1.4 anotado.

| Métrica (modo `proposta-homologada`) | R1 (v1.3) | R2 (v1.3, instrumento novo) | R3 (v1.4) |
|---|---|---|---|
| accuracy de lane | 0,4375 (14/32) | 0,4375 | 0,4375 |
| accuracy do modo `classificador` | 0,375 (12/32) | 0,375 | 0,375 |
| `falso_rebaixamento` | 8 | 8 | 8 |
| `taxa_de_escalacao` | 0,75 | 0,75 | **0,6875** |
| `taxa_de_bloqueio` | 0,2812 | 0,2812 | 0,2812 |
| `taxa_de_execucao` | 0,0625 | 0,0625 | **0,125** |
| casos com `codigo_de_acao` resolvido | 4/32 | 4/32 | **11/32** |
| casos que **executam** | 2/32 | 2/32 | **4/32** |
| sha256 corpus / política / roteador | `c5ced4fc…`/`4fe3357c…`/`6f44335d…` | idem | **`b66ecd36…`**/`4fe3357c…`/`6f44335d…` |

Leitura honesta: a **accuracy crua não se move** (0,4375). O que se move é o poder de medição — os
casos que o roteador **roteia** passam de 2 para 4 (e 5 com código comum, todos corretos), a escalação
cai de 0,75 para 0,6875 e o `falso_rebaixamento` fica **igual** (8, todos não executáveis) porque a
anotação não afrouxou nada: nenhum caso crítico passou a executar. R2 = R1 prova que **nada disso veio
da mudança do instrumento** (`[OK] R2 == R1 nas métricas`).

Diff por caso (controle v1.3 → v1.4, `diff_rodadas.py`), com o número exato de cada movimento:

| Campo | Mudanças | Casos |
|---|---|---|
| `lane` | **0** | — (nenhuma lane mudou: §10.2 C8a) |
| `exige_escalacao` / `outcome` | **2** | `real-t_969affa7`, `real-t_d9cb5755`: `ESCALATE` → `PASS` (24/32 → 22/32) |
| `pode_executar` | **2** | os mesmos dois passam a executar (2/32 → 4/32) |
| `codigo_de_acao` | **7** | `real-t_969affa7`, `real-t_d9cb5755` → `migracao_de_esquema`; `borda-02` → `migracao_de_esquema`; `borda-04/05/08/12` → código **proibido** (BLOQUEIAM igual a antes) |

Os 4 casos que já resolviam por código em v1.3 (`borda-01`, `borda-11` pelo código comum do próprio
rótulo `acao`; `borda-03`, `borda-09` pelo texto canonicalizado) não mudaram de valor: a anotação
apenas **declara** o que já era resolvido — por isso `codigo_de_acao` passa de 4/32 para **11/32**.


Autoteste do instrumento: `23/23 itens OK, 0 falhas` (exit 0) na rodada real. Na rodada do corpus
v1.3 **não anotado** o autoteste reprova **exatamente 1 item**, o novo: `FALHOU corpus: os 32 casos
declaram acao_codigo` — a prova de que o item novo tem dentes (detecta a lacuna que este card fecha).

### 10.5 O que impede a anotação de "afrouxar o D07"

1. A ferramenta só **declara** o código; quem decide continua sendo o roteador (C7/C7b: proibida e
   sem-código nunca executam, 0 violações).
2. O critério de atribuição é **fechado e declarado** no cabeçalho da ferramenta: o código vem da
   própria declaração do caso (o rótulo `acao` **é** o código, ou os sinais + a regra declarada da
   política identificam a operação) — **nunca** da prosa da justificativa. Onde não há código no
   catálogo, o campo é `null` explícito com o motivo; nomear código novo é decisão do dono.
3. `lane_esperada` intocada (C5/C6) e a suíte do gate segue **30/30** (inclui o item que confere o
   espelho `codigos_validos` × `CODIGOS_DE_ACAO_COMUNS`).

### 10.6 O classificador empatado com a constante — decisão declarada (exigência 3)

Medido no campo `classificador` do resultado:

| Medida | Valor |
|---|---|
| lane do modo `classificador` × constante estrutural (mesmo pipeline **sem** classificação) | **32/32 idênticas** (divergências: `[]`) |
| confiança máxima observada × limiar de aceite da política | **0,74 < 0,85** → **0 caso** atinge o limiar |
| acerto da **proposta** (antes dos limiares) | 6/32 = **0,1875** — pior que a constante (12/32 = 0,375) |
| contrafactual declarado (estimador aceitando, mutação **em memória**) | accuracy **0,375**, delta **0,0**; muda a lane de 2 casos (`borda-01`, `borda-11`) |

**Decisão: MANTER — e explicitamente NÃO ajustar o estimador.** O motivo é medido, não opinativo:
aceitar a proposta do classificador **não move a accuracy** (delta 0,0) e, hoje, o estimador
**não pode** atingir o limiar por construção (`0,5 + 0,12·k`, máximo 0,74 com 2 sinais casados). Pior:
a proposta crua (0,1875) é **pior** que a constante (0,375). Aposentar seria decidir sobre um
componente que este corpus **não alimenta** — o caso do corpus não traz o corpo do card nem os sinais
que o classificador usa; portanto a avaliação de aposentadoria exige um corpus com entrada real, e
isso é card novo, não este. **Não fazer:** baixar o limiar de aceite ou subir `CONFIANCA_BASE` — não há
ganho medido e o efeito é fazer a máquina decidir sobre a proposta mais fraca que a própria constante.

### 10.7 Achados (registrados; nenhum rótulo corrigido aqui)

- **A7 (pré-existente, agora atribuído):** os 6 casos com código **proibido** têm `lane_esperada`
  = `critical` e o roteador registra `high` (desfecho BLOCK) — a divergência **já existia em v1.3**
  (C8a: nenhuma lane mudou). Ela não é um erro de rótulo que este card possa corrigir: é a lane
  conservadora registrada para um caso que a máquina **não decide**. Leitura correta = desfecho
  (BLOCK/decisão humana), não a lane — por isso a população separada (§10.3). **Fica para o dono**
  decidir se o rótulo homologado de um caso proibido deve ser `critical` (lane) ou expressar
  "não decidível por máquina".
- **A1 (fechada em parte):** os 21 casos restantes **não têm** código no catálogo vigente — 18 são
  `execucao_de_card` (execução genérica de card de desenvolvimento). A lacuna de vocabulário
  permanece aberta **por desenho**: nomear códigos novos (ex.: `execucao_de_card_dev`) é decisão do
  dono sobre o catálogo, não deste card. Consequência medida: 19 deles escalam (falha fechada).
- **A2 (confirmada com o instrumento novo):** o classificador do roteador devolve a constante nos 32
  casos; agora com a **causa** medida e o contrafactual (§10.6).
- **A8 (achado de honestidade do caso `real-t_0248a568`):** o caso é entrega de engenharia com consulta
  agregada, mas o código `consulta_interna` **não foi atribuído**: o rótulo `acao` do caso é
  `execucao_de_card` e nada na declaração do caso aponta a consulta interna — atribuir seria inferir
  da prosa da justificativa, o que o critério de atribuição proíbe. Fica `null` com o motivo escrito.

### 10.8 O que este adendo **não** prova

- não prova que os 11 códigos atribuídos são a **única** leitura possível caso a caso: prova que a
  atribuição segue o critério declarado e que nenhuma delas veio da prosa da justificativa;
- não prova qualidade do classificador em geral (o corpus não lhe dá a entrada que ele usa):
  prova-o **inerte neste corpus**, com a causa;
- não move `custo_por_card_VERIFIED` nem latência de execução por lane (limites §7, inalterados);
- não fecha a revisão **individual** dos 30 rótulos em bloco (§9, pendência do dono).

---

## 11. Adendo — o código comum `execucao_de_card` e a re-anotação do corpus (v1.5), card `TRE-W0-E04-T10`

Este adendo fecha a **lacuna A1 do §10.7** pelo caminho que o card anterior declarou ser o único
legítimo: **o dono nomeou o código** (Anderson Ribeiro, 30/09/2026 — execução genérica de card de
**desenvolvimento**, escopo estreito: sem produção e sem credencial). O que mudou aqui: o catálogo do
roteador e o corpus. **O rascunho da política v1.3 não está em vigor** — quem decide hoje continua sendo
a v1.2.

### 11.1 Hashes das rodadas (o resultado versionado carrega os três)

| Rodada (arquivo em `hermes/jev/benchmarks/`) | política (sha256) | roteador (sha256) | corpus (sha256) |
|---|---|---|---|
| `resultado-benchmark-2026-09-30-jev-policy-v1.2-corpus-anotacao-v1.5.json` | `jev-policy-v1.2` `100702c6…` | `jev-router-v1.3` `95666e7c…` | `corpus-anotacao-v1.5` `fc35c600…` |
| `resultado-benchmark-2026-09-30-jev-policy-v1.3-rascunho-corpus-anotacao-v1.5.json` | `jev-policy-v1.3` (rascunho) `5138f572…` | idem | idem |

O `sha256` do roteador mudou em relação ao §10 (`6f44335d…`) porque este card **nomeou o código** nele:
`ROUTER_VERSION` = `jev-router-v1.3`, `execucao_de_card` em `CODIGOS_DE_ACAO_COMUNS`, `jev-policy-v1.3`
em `VERSOES_DE_POLITICA_SUPORTADAS`. **A lógica de decisão não mudou** — e o caminho padrão
(`CAMINHO_POLITICA_PADRAO`) continua apontando para a **v1.2**, medido no código pela suíte da v1.3.

### 11.2 A re-anotação (corpus v1.4 → v1.5)

Ferramenta: `scripts/anotar_execucao_de_card_no_corpus.py` (mesmo rito do T03-D01: mapa declarado que
tem de bater com a **derivação do próprio corpus**, prova profunda antes/depois, restauração em falha,
espelho de catálogo conferido antes de escrever, provas negativas em cópia).

| Medida | v1.4 | v1.5 |
|---|---|---|
| casos com `acao_codigo` declarado | 11/32 | **29/32** |
| casos com `acao_codigo: null` | 21 | **3** (`borda-06`, `borda-07`, `borda-10`) |
| casos com o código `execucao_de_card` | 0 | **18** |
| `lane_esperada` alterado | — | **0** (C5/C6 do mapa: campo protegido) |
| sha256 do corpus | `b66ecd36…` | **`fc35c600…`** |

Os 18 são exatamente os que o §10.7 A1 mediu (rótulo `acao` = `execucao_de_card`, sem código no catálogo
vigente) — o mapa declarado da ferramenta e a derivação do corpus batem nos dois sentidos, e o script
**recusa** se não baterem. Os **11 que já declaravam código não foram tocados** (nem o `nota`), inclusive
os 2 casos de DDL cujo rótulo `acao` é `execucao_de_card` mas que declaram `migracao_de_esquema` por sinais:
**sobrescrever código declarado é decisão de política, não de anotação**. Os 3 que seguem `null` receberam
só a marca de reavaliação e o motivo de continuarem fora do catálogo.

### 11.3 Rodada sob a política EM VIGOR (v1.2) × corpus v1.5 — o código novo abstém

Com o corpus re-anotado e a política **em vigor** no roteador, os **18 casos com `execucao_de_card`
continuam NÃO executáveis**: a v1.2 não declara lane para esse código, e ausência de lane declarada é
**abstinência**. É a versão medida do que a política declara em prosa — *declarar suporte a uma versão não
é entrar em vigor* (item C9 da suíte da v1.3).

### 11.4 Rodada sob o rascunho (v1.3) × corpus v1.5 — o que o código muda, e o que ele **não** afrouxa

Modo `politica` (não circular), 32 casos:

| Medida | v1.2 | v1.3 (rascunho) |
|---|---|---|
| casos que **executam** | 4/32 = 0,125 | **5/32 = 0,1562** |
| taxa de escalação | 0,6875 | **0,6562** |
| taxa de bloqueio | 0,2812 | 0,2812 |
| accuracy de lane | 0,375 (12/32) | 0,375 (12/32) |
| `falso_rebaixamento` (critical) | 8 | 8 (todos não executáveis) |

O **único** caso dos 18 que passa a executar é **`real-t_1acf11f2`** (configurar TLS/reverse proxy, sem
sinal sensível): sai de `ESCALATE` para `PASS` na lane `high`, que é a `lane_esperada` homologada. Os
**outros 17 continuam escalando** — e o `motivo` gravado no resultado é o mesmo nas duas rodadas:
*o código comum `execucao_de_card` não cobre o domínio sensível declarado* (produção e/ou credencial,
sinais `producao`/`mexe_em_segredo` do próprio corpus). **O escopo estreito está medido em escala, caso a
caso: 17 dos 18 não são barrados pela lane — são barrados pelo domínio.**

### 11.5 Onde a leitura NÃO se move (e por quê)

- a **accuracy crua não se move** (0,375 no modo `politica`; 0,4375 no `proposta-homologada`): dos 18, 17
  já eram escalados por domínio (a lane registrada era `high` e continua `high`) e o caso que muda de
  desfecho já acertava a lane pela conservadora. Accuracy parada **não** é ausência de efeito: o efeito
  está nas taxas de execução/escalação e, principalmente, no **poder de medição**;
- os **3 casos sem código** seguem `null` explícito: os rótulos deles (concessão de credencial, ajuste de
  interface, resposta a cliente) **não** são o código nomeado, e atribuí-los seria inventar dado. Sequer
  escala — é a lacuna remanescente, declarada;
- o **poder de medição** da população com código comum segue 14/23 = **0,6087** — o número que o
  T03-D01 criou para separar "abstenção com nome de acerto" de roteamento.

### 11.6 O que este adendo **não** prova

- **não** prova que a lane `high` é ótima para o código novo: é o **piso** que a medição do T03-D01
  sustenta (o classificador empatava com a constante "sempre `high`"). O custo é declarado: o código novo
  executável ganha lane mais cara que `small`/`medium`, e o que pode baixar o valor é **dado** de custo e
  latência por lane — versão seguinte, nunca edição silenciosa;
- **não** libera produção nem credencial por execução de card — §11.4 mede o contrário;
- **não** está em vigor: enquanto o dono não homologar, quem decide é a v1.2 e o código novo abstém (§11.3);
- **não** fecha a lacuna dos 3 casos restantes nem a revisão individual dos 30 rótulos em bloco (§9).


