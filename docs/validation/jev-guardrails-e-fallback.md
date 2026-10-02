# Validação dos guardrails e do fallback do JEV

**Card:** TRE-W0-E04-T04 (depende de TRE-W0-E04-T03) · **Data da validação:** 29/09/2026
**Correção dos achados:** cards TRE-W0-E04-T02-D03, TRE-W0-E04-T02-D04 e TRE-W0-E04-T02-D05 (29/09/2026) — ver seção 0
(estado pós-correção), card TRE-W0-E04-T07 (29/09/2026) — correção de raiz do defeito D06 (código canônico de ação +
falha fechada), ver seção 7, e cards TRE-W0-E04-T02-D07 e TRE-W0-E04-T02-D08 (29/09/2026) — texto livre sem código
canônico deixa de executar (fecha a classe dos 4 escapes) e o guardrail de DDL só aciona com DDL real, com o motivo
dizendo a causa verdadeira, ver seção 8. A memória do que estava errado fica nas seções 5 e nos anexos "histórico
pré-correção".
**Alvo validado:** `hermes/jev/routing/router.py` (jev-router-v1.0) + `hermes/jev/policy_v1.yaml` (jev-policy-v1.0)
**Suíte:** `scripts/validar_jev_guardrails.py` — validação adversarial e **independente** da suíte do card irmão
(`scripts/verificar_jev_router.py`, T02). Nenhum arquivo auditado é alterado pelas suítes: as mutações acontecem em cópia
temporária. `hermes/jev/policy_v1.yaml` (política congelada) e `hermes/policies/human-approval.yaml` **não foram alterados**
na correção.

**Resultado pós-correção (zero achados):**

| Suíte | Comando | Resultado |
|---|---|---|
| Validação adversarial (T04) | `/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --autoteste --estrito` | `PASS (74 itens, 0 falhas)`, **0 achados**, `autoteste 20/20` (exit 0) |
| Roteador (T02) | `/opt/hermes/.venv/bin/python scripts/verificar_jev_router.py --autoteste` | `PASS (59 itens, 0 falhas)`, `autoteste 21/21` (exit 0) |
| Política (T01) | `/opt/hermes/.venv/bin/python scripts/verificar_jev_policy.py --autoteste` | `PASS (42 itens, 0 falhas)`, `autoteste 12/12` (exit 0) |
| Papéis | `bash scripts/verificar_papeis.sh` | `PASS (0 falhas)` |
| Segredo versionado | `bash scripts/secret_scan.sh` | `PASS (nenhum segredo versionado)` |

As contagens acima são as de **depois** dos cards TRE-W0-E04-T02-D07/-D08 (seção 8): o roteador foi de 55 para 59 itens e
a validação adversarial de 72 para 74 itens; o autoteste foi de 18/18 para 21/21 e de 17/17 para 20/20. O histórico das
contagens anteriores ao T07 está nos Anexos A e B (46 e 64 itens), a saída bruta do T07 está na seção 7 e a do D07/D08
está na seção 8.

**Pré-correção (histórico):** `PASS (60 itens, 0 falhas)` com **7 achados** — 3 de casamento em prosa (D04), 2 de
fail-closed de tipo inválido (D05), 1 da camada Human Approval (D03) e 1 da mesma camada declarada no próprio
repositório. Os 7 achados eram a lista de defeitos abertos pelos cards D03/D04/D05; todos estão fechados.

**Comando de tudo o que está neste relatório:**

```
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --autoteste
```

## Como ler os vereditos

| Estado | Significado |
|---|---|
| `OK` | item de critério atendido, com saída bruta no relatório da suíte |
| `FALHOU` | item de critério NÃO atendido (suíte vermelha; nenhum item ficou nesta classe) |
| `ACHADO` | o roteador executa/estoura onde a política manda parar. **Defeito encontrado** (pós-correção nenhum item fica nesta classe) |
| `VALIDADO` | o guardrail/ação tem prova dos dois lados (ou do caminho exigido) **e** mutação que o remove é detectada |
| `NAO VALIDADO` | não existe prova, ou a prova mostrou que o caminho proibido passa |

`--estrito` soma os ACHADOs como falha (exit 1) para uso como gate de defeito. O default (usado acima) mantém o
critério homologado no comando e expõe os defeitos separadamente, sem esconder nenhum: toda linha ACHADO aparece
no stdout e aqui. Com zero achados, `--estrito` e o default dão o mesmo veredito (`exit 0`).

## 0. Correção dos defeitos D03, D04 e D05 — estado pós-correção

Esta seção é posterior à validação original (seções 1 a 6 e anexos "histórico pré-correção"). Os três defeitos
altos/médios que a validação havia encontrado foram **corrigidos no roteador** — nenhuma regra da política foi tocada.

| Defeito | Severidade | O que era o defeito | Correção (arquivo: o quê) |
|---|---|---|---|
| **D03** (`TRE-W0-E04-T02-D03`) | alta | a camada Human Approval **nunca lia** `hermes/policies/human-approval.yaml` (o arquivo não tem `role:`, o índice de papéis só indexa quem tem, e o filtro por nome de arquivo era código morto). `promocao de release para producao` saía `executar/PASS`, `exit=0` | `hermes/jev/routing/router.py`: `_e_arquivo_de_human_approval` + `_acoes_de_human_approval` leem a fonte **por nome de arquivo**, independente de `role:`; `carregar_politica` lê as duas seções (`exige_aprovacao` e `nunca_automatico`) e guarda em `_acoes_de_human_approval`; `acoes_de_decisao_humana` usa essa chave. Fonte ausente/ilegível/sem ação declarada = **recusa na leitura** (fail-closed), nunca camada decorativa |
| **D04** (`TRE-W0-E04-T02-D04`) | alta | o casamento por texto das 8 ações de `nunca_decidido_por_maquina` não pegava o texto da própria política: `promocao de release para producao`, `promover release para producao`, `publicar release em producao` e `exclusao de registro de auditoria` executavam com `PASS` | `hermes/jev/routing/router.py`: `CONCEITOS_DE_ACAO` (tabela de variantes, declarada e comentada) + `REGRAS_DE_ACAO_HUMANA` + `acao_canonica_de_decisao_humana`, aplicados **antes** do casamento de frases em `acao_de_decisao_humana`. A tabela só muda vocabulário: limiar, lane, perfil e lista de proibição continuam vindo do YAML |
| **D05** (`TRE-W0-E04-T02-D05`) | média | entrada de tipo inválido **estourava exceção** em vez de bloquear: payload não-mapa → `ValueError`, `sinais` em lista → `AttributeError` | `hermes/jev/routing/router.py`: os guardrails de código rodam sobre a entrada crua e `_tarefa_segura` normaliza a borda (payload não-mapa/sinais não-mapa → mapa identificado). Resultado: `BLOCK` com recibo de 13 campos e `payload_valido` registrado, nunca exceção; `_guardrails_de_politica` também lê `sinais` defensivamente |

Nota de arquitetura (correção de raiz do D04): casar prosa é **finito** — a tabela de variantes cobre o vocabulário
medido, não qualquer texto equivalente. A correção de raiz é o **dispatch passar o CÓDIGO CANÔNICO da ação** (a mesma
nomenclatura de `nunca_decidido_por_maquina`), nunca texto livre — **card TRE-W0-E04-T05**. Enquanto a entrada for prosa,
esta tabela tem de crescer com o vocabulário real medido; a suite prova o alinhamento entre tabela e política.

### 0.1 As 4 frases que escapavam, pelo CLI real (agora `BLOCK`, `exit=3`)

```
### as 4 frases que escapavam (pre-correcao: PASS/exit 0)
  'promocao de release para producao': exit=3 outcome=BLOCK decidido=bloquear exige_aprovacao_humana=True guardrails=[] motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
  'promover release para producao': exit=3 outcome=BLOCK decidido=bloquear exige_aprovacao_humana=True guardrails=[] motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
  'publicar release em producao': exit=3 outcome=BLOCK decidido=bloquear exige_aprovacao_humana=True guardrails=[] motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
  'exclusao de registro de auditoria': exit=3 outcome=BLOCK decidido=bloquear exige_aprovacao_humana=True guardrails=[] motivo=acao de decisao humana (exclusao_de_dado_de_cliente): nunca decidida por maquina
```

O JSON completo de uma delas (o que o roteador imprime agora, `exit=3`):

```
$ /opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"t_def","acao":"promover release para producao","lane_proposta":"small","confianca":0.9}'
{
  "recibo": {
    "decision_id": "dec-2254428d6c2cc028", "card_id": "t_def",
    "task_hash": "261f9fdfd07e3696fab0864741528bcca91e4e38b396a7acaadcc7b9c4cd689b",
    "lane": "high", "model_profile": null, "selected_model": null, "effort": null,
    "confidence": null, "policy_version": "jev-policy-v1.0", "router_version": "jev-router-v1.0",
    "timestamp": "2026-09-29T21:44:55+00:00", "override": null, "outcome": "BLOCK"
  },
  "decisao": {
    "decidido": "bloquear", "outcome": "BLOCK", "lane": "high",
    "lane_conservadora_da_politica": "high", "degraded_mode": false, "pode_executar": false,
    "exige_revisao": false, "exige_escalacao": true, "exige_aprovacao_humana": true,
    "papel_executor": "dev-harness", "policy_version": "jev-policy-v1.0",
    "politica_lida_de": ".../hermes/jev/policy_v1.yaml",
    "guardrails_acionados": [],
    "motivos": ["acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina",
                "encaminhar para Human Approval do Anderson"],
    "evidencias": []
  }
}
exit=3
```

Observação honesta: nas duas frases que só a canonicalização pega (não estão declaradas verbatim em nenhum arquivo —
ver 0.3), o bloqueio sai pela camada Human Approval com `exige_aprovacao_humana=true` e lane conservadora; nas frases
que casam a fonte `human-approval.yaml`, o mesmo. `deploy em producao` continua bloqueado, agora perdendo o guardrail
de papel (`guardrails=[]`) porque a camada 2 vem antes — a precedência é `Security → Human Approval`, e nenhuma das duas
executa.

### 0.2 Entrada de tipo inválido: `BLOCK` com recibo, sem traceback

```
### entrada de tipo invalido (pre-correcao: ValueError / AttributeError)
  payload nao-mapa (texto): exit=3 outcome=BLOCK pode_executar=False guardrails_acionados=['payload_valido'] campos_do_recibo=13 stderr=''
  payload nao-mapa (lista): exit=3 outcome=BLOCK pode_executar=False guardrails_acionados=['payload_valido'] campos_do_recibo=13 stderr=''
  sinais nao-mapa: exit=3 outcome=BLOCK pode_executar=False guardrails_acionados=['payload_valido'] campos_do_recibo=13 stderr=''
  recibo (13 campos) do caso 'sinais nao-mapa': {"decision_id": "dec-e0bacda86a90d8b0", "card_id": "t_d05", "task_hash": "753eaef2bb9e131749457e42db5c59b6daf36503cc1c9a665c7239f2c17f2b2a", "lane": "high", "model_profile": null, "selected_model": null, "effort": null, "confidence": null, "policy_version": "jev-policy-v1.0", "router_version": "jev-router-v1.0", "timestamp": "2026-09-29T21:44:55+00:00", "override": null, "outcome": "BLOCK"}
```

`stderr=''` nos três casos: não há traceback. `card_id` é `null` no payload não-mapa (não há card a extrair) — o recibo
continua com os 13 campos e `task_hash` determinístico da entrada de bloqueio.

### 0.3 A camada Human Approval lendo a fonte sem `role:` (D03)

```
### camada Human Approval: fonte lida por NOME de arquivo (arquivo sem `role:`)
  grep -c '^role:' hermes/policies/human-approval.yaml -> 0
  'promocao de release para producao': exit=3 outcome=BLOCK ... exige_aprovacao_humana=True guardrails=[] motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
  'expor segredo em log, receipt ou mensagem': exit=3 outcome=BLOCK ... exige_aprovacao_humana=True guardrails=[] motivo=acao de decisao humana (expor segredo em log, receipt ou mensagem): nunca decidida por maquina
  'contatar empresa com do_not_contact / opt_out marcado': exit=3 outcome=BLOCK ... guardrails=['papel_sem_credencial_de_deploy'] motivo=guardrail papel_sem_credencial_de_deploy: Sales AI nao tem credencial de deploy nem altera codigo (papel dev-harness nao pode: contatar lead, cliente ou decisor)
```

A segunda linha é a prova de que a leitura da **fonte** acontece (essa declaração só existe em `nunca_automatico` do
arquivo e não tem regra canônica própria); a terceira é bloqueada pela camada Security (guardrail de papel), o que
mostra a precedência funcionando.

### 0.4 Prova de que os itens de regressão REPROVAM se o defeito voltar

Cada defeito foi **revertido em cópia temporária** do roteador (reversão pontual, sem tocar o repo) e as duas
suítes rodaram contra o mutante. Saída real:

```
T04 | D03 revertido (fonte human-approval.yaml volta a ser ignorada)
       FALHOU: 1 -> ['cobertura D03 — a fonte human-approval.yaml entra na camada Human Approval sem depender de role: e nao executa']
       ACHADO: 1 -> ['acao declarada em human-approval.yaml — BLOQUEIO (9 declaracoes)']
T02 | D03 revertido (fonte human-approval.yaml volta a ser ignorada)
       FALHOU: 4 -> ['CLI: entrada sintetica imprime o recibo (13 campos) e sai 0', 'CLI: bloqueio sai com codigo 3 e recibo em BLOCK', 'CLI: le o card do board e imprime o recibo', 'D03: a fonte human-approval.yaml entra na camada Human Approval sem depender de role: (9 declaracoes nao executam)']
T04 | D04 revertido (canonicalizacao da acao desligada)
       FALHOU: 0 -> []
       ACHADO: 2 -> ["acao proibida aprovacao_de_producao — PROSA 'publicar versao em producao' bloqueia ou escala", "acao proibida exclusao_de_dado_de_cliente — PROSA 'remover cadastro de titular' bloqueia ou escala"]
T02 | D04 revertido (canonicalizacao da acao desligada)
       FALHOU: 3 -> ['CLI: entrada sintetica imprime o recibo (13 campos) e sai 0', 'CLI: bloqueio sai com codigo 3 e recibo em BLOCK', 'CLI: le o card do board e imprime o recibo']
T04 | D05 revertido (borda sem validacao de tipo: dict(tarefa or {}))
       FALHOU: 1 -> ['cobertura D05 — entrada de tipo invalido termina em BLOCK com recibo de 13 campos, sem excecao']
       ACHADO: 1 -> ['fail-closed ponta a ponta — payload nao-mapa nao pode estourar']
T02 | D05 revertido (borda sem validacao de tipo: dict(tarefa or {}))
       FALHOU: 4 -> ['CLI: entrada sintetica imprime o recibo (13 campos) e sai 0', 'CLI: bloqueio sai com codigo 3 e recibo em BLOCK', 'CLI: le o card do board e imprime o recibo', 'D05: entrada de tipo invalido termina em BLOCK com recibo, nunca em excecao']
```

Leitura honesta dessa prova, incluindo o que **não** está provado:

1. **D03 e D05 têm item próprio** que reprova ao reverter (`cobertura D03 —` e `cobertura D05 —` na T04; `D03:` e
   `D05:` na T02). São os itens certos: reprovam pelo comportamento, não por inspeção de código.
2. **As 4 frases do D04 têm cobertura dupla** (a fonte `human-approval.yaml` agora lida + a canonicalização): reverter
   **só** a canonicalização **não** reabre as 4, porque a fonte lida já as pega. Por isso a suite ganhou duas variações
   que **não existem em prosa em nenhum arquivo do repo** (`publicar versao em producao`, `remover cadastro de titular`):
   são elas que reprovam quando a canonicalização é desligada (2 `ACHADO` na T04 acima). Sem esses dois casos, a T02
   de 3 itens de CLI reprovados seria o único sinal — e esse sinal é artefato do modo degradado do mutante, não do D04.
3. As 3 falhas de CLI que aparecem em **toda** reversão são pré-existentes e não têm relação com os defeitos: os itens
   de CLI rodam o roteador mutante a partir de um diretório temporário, onde o caminho padrão da política não existe, e
   acontece o modo degradado (a suíte completa registra isso também nas mutações do autoteste).

## 1. Contagem do que o YAML declara e do que ficou validado

| Bloco do YAML | Declarados | VALIDADOS | NAO VALIDADOS |
|---|---|---|---|
| `guardrails` (regras) | 5 | **5** (cada um com teste de REPROVA + APROVA + mutação que o remove detectada) | 0 como regra; **2 casos de borda do fail-closed** não validados ponta a ponta (achado D3 — **corrigido**, ver 0.2) |
| `nunca_decidido_por_maquina` (ações) | 8 | **8** por nome exato (bloqueio) | **2** com cobertura em prosa incompleta: `aprovacao_de_producao` e `exclusao_de_dado_de_cliente` (achados D1/D2 — **corrigidos**, ver 0.1) |
| `fallback` (gatilhos exercitados) | 4 gatilhos citados | **4 de 4** exercitados de ponta a ponta pela CLI (política ausente, ilegível/corrompida, versão desconhecida, seção faltando) + o caso do caminho padrão | **0**; porém "timeout" e "saída fora do contrato" não têm implementação no roteador (ver seção 6) |
| Mutações (autoteste) | 13 | **13 de 13 detectadas** | 0 |

## 2. Guardrails — um por um

Cada guardrail abaixo tem: o caminho proibido (REPROVA), o permitido (APROVA), a mutação que o remove e o que ficou
sem prova. O item de cobertura da própria suíte recusa marcar OK guardrail declarado no YAML sem os dois lados:

```
OK     cobertura — cada guardrail do YAML tem teste de REPROVA e de APROVA  [5 guardrails declarados no YAML, 5 regras, todos com teste de REPROVA e de APROVA]
```

### 2.1 `segredo nunca entra em prompt, log, recibo ou mensagem` — **VALIDADO**

| Lado | Item da suíte (estado) | Saída bruta |
|---|---|---|
| REPROVA | `guardrail segredo — REPROVA: 6 formas de segredo bloqueiam` (OK) | `6 formas bloqueadas com BLOCK` — sinal `segredo_no_payload`, formato `sk-`, bloco PEM, credencial em URL, `senha:` no corpo e valor de variável de ambiente no payload |
| APROVA | `guardrail segredo — APROVA: payload limpo executa` (OK) | `decidido=executar outcome=PASS lane=small guardrails=[]` |
| APROVA | `guardrail segredo — APROVA: segredo bloqueado nao aparece em decisao/recibo` (OK) | `nem o valor nem o formato aparecem na decisao/recibo` |
| Mutação | `guardrail segredo removido (payload com segredo passa)` | **detectada** — 3 itens reprovam o mutante, incluindo `fallback — APROVA: no degradado os guardrails continuam rodando primeiro` |

Sem prova: segredo em anexo binário/mídia (o scan é sobre o JSON da tarefa, não sobre arquivos).

### 2.2 `empresa com do_not_contact ou opt_out não é contatada` — **VALIDADO**

| Lado | Item (estado) | Saída bruta |
|---|---|---|
| REPROVA | `guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia` (OK) | `decidido=bloquear outcome=BLOCK lane=high guardrails=['do_not_contact']` |
| APROVA | `guardrail do_not_contact — APROVA: sem marcacao ou sem outbound executa` (OK) | outbound (`enviar e-mail pelo Titan`) sem marcação executa; empresa marcada em ação interna **não aciona o guardrail de VOCABULÁRIO** (a execução automática é proibida pelo `registro_marcado` — ver §9) |
| REPROVA | `guardrail registro_marcado — REPROVA: registro anotado nao executa automaticamente (acao interna ou de abordagem)` (OK) | `decidido=bloquear outcome=BLOCK guardrails=['registro_marcado']`, `exige_aprovacao_humana=true`, 13 campos com o motivo em `override.registro_marcado.motivo` |
| APROVA | `guardrail registro_marcado — APROVA: registro limpo segue o fluxo e executa` (OK) | `decidido=executar outcome=PASS guardrails=[]` (o encaixe não virou bloqueio geral) |
| Mutação | `guardrail do_not_contact removido (empresa marcada e contatada)` | **detectada** — 1 item reprova o mutante |
| Mutação | `guardrail de registro marcado removido (registro anotado volta a executar sozinho)` | **detectada** — 2 itens reprovam o mutante (ver §9) |

Sem prova: consulta real à base cadastral (o roteador lê o sinal `empresa_do_not_contact` da tarefa; quem preenche o
sinal a partir da base não faz parte deste card).

### 2.3 `DDL não nasce em produção` — **VALIDADO**

| Lado | Item (estado) | Saída bruta |
|---|---|---|
| REPROVA | `guardrail DDL — REPROVA: DDL em producao ou sem ambiente bloqueia` (OK) | `DDL em producao e DDL sem ambiente declarado bloqueiam` (`guardrails=['ddl_fora_de_producao']`, `lane=high`, `BLOCK`) |
| APROVA | `guardrail DDL — APROVA: DDL em ambiente de desenvolvimento executa` (OK) | DDL com `ambiente_alvo=desenvolvimento` e `migration` com `dev` executam (`PASS`) |
| Mutação | `guardrail DDL removido (DDL nasce em producao)` | **detectada** — 1 item reprova o mutante |

Sem prova: DDL executada por script fora do roteador (o guardrail julga o campo `ambiente_alvo` da tarefa).

### 2.4 `Sales AI não tem credencial de deploy nem altera código` — **VALIDADO**

| Lado | Item (estado) | Saída bruta |
|---|---|---|
| REPROVA | `guardrail Sales AI x credencial de deploy — REPROVA: bloqueia` (OK) | deploy pedido por `sales-ai`, credencial proibida `GITHUB_TOKEN` e papel desconhecido: os três bloqueiam com `papel_sem_credencial_de_deploy` |
| APROVA | `guardrail Sales AI x credencial de deploy — APROVA: acao/credencial permitida executa` (OK) | ação comercial e credencial permitida (`TRE_PG_*`) executam (`lane=medium`) |
| Mutação | `guardrail de papel/credencial removido (Sales AI com deploy)` | **detectada** — 1 item reprova o mutante |

Sem prova: alteração de código feita por fora (o guardrail julga `papel_solicitado`/`credencial_solicitada` da tarefa,
e lê a matriz de `hermes/policies/*.yaml`).

### 2.5 `falha de guardrail bloqueia, não libera` (fail-closed) — **VALIDADO com 2 casos de borda NAO VALIDADOS**

| Lado | Item (estado) | Saída bruta |
|---|---|---|
| REPROVA | `guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam` (OK) | sinal desconhecido → `BLOCK` com `fail_closed_sinal_desconhecido`; `sinais: None` → `BLOCK` com `payload_valido`; o guardrail `payload_valido` do código **aciona** para payload não-mapa e `sinais` não-mapa |
| APROVA | `guardrail fail-closed — APROVA: entrada valida e sem sinal desconhecido executa` (OK) | `decidido=executar outcome=PASS lane=small guardrails=[]` |
| REPROVA | `guardrail fail-closed na leitura — REPROVA: politica sem guardrail declarado e recusada` (OK) | os 5 guardrails exigidos: política que para de declarar qualquer um dos 5 é recusada na leitura |
| Mutação | `fail-closed removido no sinal desconhecido` / `fail-closed removido na entrada de tipo invalido` / `roteador deixa de exigir o guardrail declarado na politica` / `nenhum guardrail bloqueia (a barreira e decorativa)` | **as 4 detectadas** (1, 1, 1 e 7 itens reprovados) |

**NAO VALIDADO ponta a ponta (achado D3) — CORRIGIDO (D05, ver seção 0):** para payload não-mapa e `sinais` não-mapa,
`decidir()` **estourava exceção** em vez de devolver `BLOCK` — o guardrail acionava, mas a avaliação da política rodava
antes do teste de bloqueio e quebrava primeiro. Itens da suíte: `fail-closed ponta a ponta — payload nao-mapa nao pode
estourar` e `fail-closed ponta a ponta — sinais nao-mapa nao pode estourar`, ambos `ACHADO` (hoje `OK`, e a suíte ganhou
o item `cobertura D05 —` que reprova se a exceção voltar).

## 3. Ações de `nunca_decidido_por_maquina` — 8 por nome exato

Nenhuma executa. `BLOCK` por nome exato em todas as 8 (a saída completa está no anexo A):

```
OK     acao proibida aprovacao_de_producao — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida primeiro_contato_outbound — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida envio_de_proposta_comercial — BLOQUEIO por nome exato  [BLOCK por nome exato — ...]
OK     acao proibida mudanca_estrutural_de_arquitetura — BLOQUEIO por nome exato  [BLOCK por nome exato — ...]
OK     acao proibida rollback_em_producao — BLOQUEIO por nome exato  [BLOCK por nome exato — ...]
OK     acao proibida exclusao_de_dado_de_cliente — BLOQUEIO por nome exato  [BLOCK por nome exato — ...]
OK     acao proibida rotacao_ou_revogacao_de_credencial — BLOQUEIO por nome exato  [BLOCK por nome exato — ...]
OK     acao proibida publicacao_em_nome_da_transformativa — BLOQUEIO por nome exato  [BLOCK por nome exato — guardrails=['papel_sem_credencial_de_deploy']]
OK     cobertura — cada acao proibida do YAML tem teste de bloqueio e de prosa  [8 acoes de nunca_decidido_por_maquina com teste de bloqueio e com variacao em prosa]
```

### 3.1 Cobertura em prosa (24 variações: as 22 da validação original + 2 que só a canonicalização pega)

As duas linhas marcadas como `NAO VALIDADO` foram **fechadas** pela correção do D04 (ver 0.1); a foto abaixo é a
pré-correção. A coluna "Variações" inclui as 2 adicionadas na correção para provar a canonicalização
(`publicar versao em producao`, `remover cadastro de titular` — não existem em prosa em nenhum arquivo do repo).

| Ação | Variações testadas | Bloqueiam/escalam | Executam (ACHADO, pré-correção) | Veredito da cobertura em prosa |
|---|---|---|---|---|
| `aprovacao_de_producao` | 6 (5 + 1 pós-correção) | 2 (`aprovar promocao de release para producao`, `deploy em producao`) | **3** — `promocao de release para producao`, `promover release para producao`, `publicar release em producao` | **NAO VALIDADO** pré-correção → **corrigido** (D04) |
| `primeiro_contato_outbound` | 3 | 3 | 0 | VALIDADO |
| `envio_de_proposta_comercial` | 2 | 2 | 0 | VALIDADO |
| `mudanca_estrutural_de_arquitetura` | 2 | 2 | 0 | VALIDADO |
| `rollback_em_producao` | 2 | 2 | 0 | VALIDADO |
| `exclusao_de_dado_de_cliente` | 4 (3 + 1 pós-correção) | 2 | **1** — `exclusao de registro de auditoria` | **NAO VALIDADO** pré-correção → **corrigido** (D04) |
| `rotacao_ou_revogacao_de_credencial` | 2 | 2 | 0 | VALIDADO |
| `publicacao_em_nome_da_transformativa` | 3 | 3 | 0 | VALIDADO |

Linhas ACHADO cruas (as mesmas 4 variações, mais 1 declaração do próprio repositório):

```
ACHADO acao proibida aprovacao_de_producao — PROSA 'promocao de release para producao' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
ACHADO acao proibida aprovacao_de_producao — PROSA 'promover release para producao' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
ACHADO acao proibida aprovacao_de_producao — PROSA 'publicar release em producao' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
ACHADO acao proibida exclusao_de_dado_de_cliente — PROSA 'exclusao de registro de auditoria' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
ACHADO acao declarada em human-approval.yaml — BLOQUEIO (9 declaracoes)  [exige_aprovacao: 'promocao de release para producao' -> decidido=executar outcome=PASS lane=small guardrails=[]; nunca_automatico: 'expor segredo em log, receipt ou mensagem' -> decidido=executar outcome=PASS lane=small guardrails=[]]
```

Prova independente do caso mais grave, pelo CLI do próprio roteador (`exit=0` = execução liberada):

```
$ /opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"t_def","acao":"promocao de release para producao","lane_proposta":"small","confianca":0.9}'
 "outcome": "PASS", "lane": "small", "model_profile": "worker-barato",
 "decidido": "executar", "pode_executar": true, "exige_aprovacao_humana": false,
 "guardrails_acionados": [], "motivos": ["confianca 0.9 >= limiar de aceite 0.85: aceita a lane"]
exit=0
```

## 4. Fallback / modo degradado — exercitado de ponta a ponta

Executado pela CLI real, com cópia temporária do roteador (`--politica` apontando para o arquivo quebrado, `--papeis`
para `hermes/policies` do repo). Em **todos** os casos: lane conservadora (`high`), `degraded_mode: true`,
`confidence: null` (ausência de resposta = abstinência), `outcome: ESCALATE`, `pode_executar: false`, recibo com os 13
campos, `exit=2`.

```
OK     fallback ponta a ponta (CLI) — REPROVA: politica ausente -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ilegivel/corrompida -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: versao de politica desconhecida -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: secao guardrails faltando -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ausente no caminho padrao -> degradado, nunca execucao silenciosa  [exit=2 lane=high degradado=True policy_version=None]
OK     fallback ponta a ponta (CLI) — APROVA: guardrails rodam primeiro nos 4 cenarios quebrados  [nos 4 cenarios quebrados o guardrail de segredo bloqueou (BLOCK) antes do fallback]
OK     fallback — REPROVA: politica corrompida/versao desconhecida/secao faltando sao recusadas na leitura  [...]
OK     fallback — REPROVA: qualquer secao obrigatoria faltando e recusada  [as 8 secoes obrigatorias: removida uma a uma, todas recusadas]
OK     fallback — APROVA: politica indisponivel entra em degradado conservador sem execucao  [lane=high degradado=True outcome=ESCALATE confidence=None]
OK     fallback — APROVA: no degradado os guardrails continuam rodando primeiro  [decidido=bloquear outcome=BLOCK lane=high guardrails=['segredo_sem_payload']]
OK     fallback — APROVA: abstencao (confianca baixa/ausente, lane desconhecida) escala na lane conservadora  [abstencao usa a lane conservadora e escala nos 3 casos]
```

**O caso mais fraco do fallback** (o que eu considero a prova mais fraca deste bloco, declarado com a saída real): o
gatilho "seção faltando" foi exercitado pela remoção da seção `guardrails` — a política é recusada por *duas* causas ao
mesmo tempo (a seção obrigatória some **e** o guardrail deixa de ser declarado), então aquela execução não isola a causa:

```
OK     fallback ponta a ponta (CLI) — REPROVA: secao guardrails faltando -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
```

Foi por isso que a suíte também remove as **8** seções obrigatórias uma a uma (não só `guardrails`): todas as 8 são
recusadas. Ainda assim, "seção faltando" só está provado para seções exigidas por `carregar_politica` — uma seção nova
e desconhecida do roteador passaria.

## 5. Achados originais (pré-correção) — 7 achados, todos fechados

> **Histórico.** Esta seção é o registro de quando a validação do T04 encontrou os defeitos, com a evidência de como
> cada um passava. Os 7 achados estão **fechados** pela correção descrita na seção 0 (o `ACHADO` de
> `human-approval.yaml` era o D03; as 4 linhas de prosa eram o D04; as 2 de tipo inválido eram o D05; o item de
> fail-closed ponta a ponta era o D05). O texto abaixo fala no presente de propósito: é a foto do que estava errado.

**Status de cada um:** D1 → **corrigido** (card D03) · D2 → **corrigido** (card D04) · D3 → **corrigido** (card D05).
Reverter cada correção volta a produzir o achado — prova na seção 0.4.

Severidade conforme `docs/kanban/processo-de-defeitos.md`. Local exato informado para abrir o card de defeito.

| # | Severidade | Sintoma | Local | Evidência | Detecção |
|---|---|---|---|---|---|
| **D1** | **alta** | A camada Human Approval **ignora `hermes/policies/human-approval.yaml` inteiro**: o arquivo não tem chave `role:`, `_papeis_do_diretorio` só indexa políticas com `role` e o filtro `_arquivo.endswith("human-approval.yaml")` nunca é verdadeiro. Com isso `exige_aprovacao`/`nunca_automatico` nunca entram na camada 2. `promocao de release para producao` (verbatim do arquivo) sai `executar/PASS`, `exit=0` | `hermes/policies/human-approval.yaml` (sem `role:`); `hermes/jev/routing/router.py:300-308` (`_papeis_do_diretorio`) e `router.py:715-722` (`acoes_de_decisao_humana`) | `ACHADO acao declarada em human-approval.yaml — BLOQUEIO (9 declaracoes)` e o CLI acima (`exit=0`) | teste/verificador (autoteste do T04) |
| **D2** | **alta** | Casamento por texto de `nunca_decidido_por_maquina` não cobre o texto que a própria política usa: `exclusao de registro de auditoria` (a política diz `exclusao_de_dado_de_cliente # ou de registro de auditoria`), `promover release para producao` e `publicar release em producao` executam com `PASS` (`pode_executar: true`) | `hermes/jev/policy_v1.yaml:92-100` (lista) + `router.py:231-247` (`_entradas_que_casam`/limiar de 0,5) | 4 linhas `ACHADO acao proibida ... PROSA ...` com `outcome=PASS` | teste/verificador |
| **D3** | **média** | `decidir()` **estoura exceção** em vez de bloquear: payload não-mapa → `ValueError: dictionary update sequence element #0 has length 1; 2 is required`; `sinais` não-mapa (lista) → `AttributeError: 'list' object has no attribute 'get'`. O guardrail `payload_valido` **aciona** (provado por chamada direta), mas `_guardrails_de_politica` roda antes do teste de acionados e quebra primeiro — o contrato "dúvida = BLOCK" não se cumpre ponta a ponta | `router.py:920` (`dict(tarefa or {})`), `router.py:923-937` (ordem: `extend` da política antes de `acionados`), `router.py:599` (`sinais.get`) | 2 linhas `ACHADO fail-closed ponta a ponta — ... nao pode estourar` com a exceção crua | teste/verificador |

Observação de precisão (os dois achados são distintos): `promocao de release para producao` executaria mesmo depois de
D1 corrigido? Não necessariamente, mas `promover release para producao` e `publicar release em producao` **continuam**
executando independentemente de D1, porque a política do roteador só declara `aprovacao_de_producao` e o casamento por
token não liga "release"/"producao" a `aprovacao_de_producao` com os limiares atuais. Corrigir um não fecha o outro.

**Pós-correção:** os dois foram corrigidos por caminhos diferentes — D1 pela leitura da fonte `human-approval.yaml`
(D03) e D2 pela canonicalização das ações no roteador (D04). A previsão acima se confirmou e virou teste: reverter
**só** a canonicalização não reabre as 4 frases (a fonte lida as pega), por isso a suíte ganhou 2 variações que só a
canonicalização pega (seção 0.4, item 2). A primeira frase do D2 (`promocao de release para producao`) também está
declarada verbatim na fonte D1, então ela é coberta pelos dois caminhos.

## 6. Limitações honestas — o que NÃO ficou provado

1. **"timeout" e "saída fora do contrato"** são citados no gatilho do `fallback` do YAML, mas **não têm implementação
   no roteador** (não há chamada de rede/LLM). Provei `política ausente`, `ilegível/corrompida`, `versão desconhecida` e
   `seção faltando`. Timeout de verdade e saída inválida de um classificador externo **não estão provados** — não existe
   alvo para provar.
2. **`selected_model` nunca é preenchido** em nenhuma decisão (o catálogo fica fora da política e não foi passado): o
   recibo grava `null`. Fora do escopo do card, mas o campo do contrato não tem prova de preenchimento.
3. A cobertura em prosa é **finita e amostrada** (22 variações). O resultado prova o contrário do que se gostaria: 4
   variações escapam. Não é prova de que qualquer texto equivalente seja pego.
4. **Fail-closed de tipo inválido** ficou `NAO VALIDADO` ponta a ponta (achado D3). O guardrail aciona quando chamado
   direto; a decisão completa estoura antes de bloquear.
5. A matriz Dev × Sales foi provada só pelo que o card cobra (deploy/credencial/código/DDL); não provei
   `conceder aprovacao humana no lugar do Anderson` nem outros itens de `nao_pode` sem teste próprio.
6. Os 6 critérios homologados do **T02 não foram re-verificados** aqui de propósito (trabalho independente). A
   referência para eles continua `scripts/verificar_jev_router.py`.
7. Os guardrails julgam o **payload da tarefa** (`sinais`, `ambiente_alvo`, `papel_solicitado`, `credencial_solicitada`).
   Quem preenche esses campos a partir da realidade (base cadastral, ambiente real, credencial real) não faz parte
   deste card e não está validado.
8. Nenhum segredo real foi usado; os valores são sintéticos e óbvios de teste. Não há prova de que segredos fora dos 7
   formatos reconhecidos (`PADROES_DE_SEGREDO`) sejam detectados.

Limitações **introduzidas/remanescentes com a correção** dos defeitos (seção 0) — declaradas com a mesma honestidade:

9. **O casamento em prosa continua finito.** `CONCEITOS_DE_ACAO`/`REGRAS_DE_ACAO_HUMANA` cobrem o vocabulário medido
   (24 variações na T04 + 4 frases na T02), não qualquer texto equivalente. **Atenuado pelo card TRE-W0-E04-T07 (seção
   7):** a decisão de segurança deixou de depender só dessa peneira — a ação é resolvida para um **código** antes de
   decidir e, sem código conhecido em tarefa com domínio sensível, o roteador **falha fechado** (escala com motivo
   "acao nao classificada com seguranca"). O casamento em prosa continua existindo como ponte de compatibilidade e segue
   finito; o caminho durável é o **dispatch passar sempre o código canônico** (card TRE-W0-E04-T05).
10. **O casamento de frases declaradas mantém a folga antiga** (`casados >= 2`): uma ação que mencione duas palavras de
   uma frase da fonte pode exigir Human Approval sem ser aquela ação. Exemplo medido: `executar deploy, promocao de
   release ou rollback` (sem `producao`) casa `promocao de release para producao` e o motivo registra essa frase. O erro
   é para o lado conservador (bloqueia/escala, nunca executa), mas o motivo pode apontar a frase errada.
11. **A camada Human Approval agora depende da fonte:** sem `hermes/policies/human-approval.yaml` no diretório de
   políticas, `carregar_politica` **recusa** (fail-closed) — um diretório de políticas sem esse arquivo deixa de ser
   utilizável pelo roteador. É intencional (camada sem fonte era o defeito D03), mas é mudança de contrato e quem
   monta um diretório de políticas precisa saber.
12. **Sem prova de falso positivo da canonicalização** fora do par REPROVA/APROVA das suítes: não medi tarefas reais
   (o corpus anotado do T03 não foi reprocessado) para ver se alguma ação legítima passou a exigir aprovação.
13. `selected_model` continua `null` em toda decisão (limitação 2, não mexida por esta correção).

## 7. Correção de raiz do D06 — código canônico de ação + falha fechada (card TRE-W0-E04-T07)

**Card:** TRE-W0-E04-T07 (P0, fecha o defeito TRE-W0-E04-T02-D06) · **Data:** 29/09/2026
**Alvo:** `hermes/jev/routing/router.py` (jev-router-v1.0, política congelada **não** tocada)
**O que estava aberto:** a canonicalização por vocabulário é **finita**. Com as 4 suítes verdes, a frase de primeiro
contato `enviar mensagem ao primeiro cliente interessado` saía `executar/PASS` — a peneira tapa as frases testadas e
deixa passar as não testadas. Acrescentar sinônimos foi descartado no próprio card: **remendo de vocabulário não fecha a
classe do problema**.

### 7.1 O que mudou no roteador (e o que NÃO mudou)

| Peça | O quê |
|---|---|
| Resolução da ação em **código** antes de decidir | `resolver_codigo_de_acao(tarefa, politica)` resolve a ação por **(1)** o campo novo `acao_codigo` (via **principal**), **(2)** o código escrito direto no texto da ação, **(3)** a prosa canonicalizada (ponte de compatibilidade, finita). Devolve `codigo` + `origem` + `classe`. O código **proibido** vem sempre de `nunca_decidido_por_maquina` da política (lido em tempo de execução); o lado **comum** do contrato é `CODIGOS_DE_ACAO_COMUNS` (conjunto fechado, declarado no roteador porque a política congelada nomeia só as ações proibidas). Código desconhecido **não resolve**. `origem`/`codigo` vão para a **decisão** (`codigo_de_acao`, `origem_do_codigo_de_acao`) — o recibo continua com os 13 campos congelados. |
| **Sinais defensivos** (união) | `dominios_sensiveis(tarefa)` = **declarados** (`sinais` do chamador: `producao_ou_release`, `credencial`, `dado_de_cliente`, `outbound_a_terceiro` e apelidos — agora em `SINAIS_CONHECIDOS`, então não caem mais no guardrail de sinal desconhecido) ∪ **inferidos do texto da ação** (`_dominios_sensiveis_do_texto`, por **conceito** — produção/release, credencial, dado de cliente, outbound a terceiro com alvo). Quem esquece de declarar **não** abre buraco. |
| **Falha fechada** | `_falha_fechada_por_acao_nao_classificada(resolucao, dominios)`: ação que **não resolve para código conhecido** E tarefa com domínio sensível **não executa** — escala (`escalar_acao_nao_classificada` / `ESCALATE` / `exit=2`, lane conservadora, recibo de 13 campos) com o motivo obrigatório **`acao nao classificada com seguranca`**. Vale também para código **comum em desacordo** com o texto sensível (código e texto em conflito é dúvida) e para código **declarado desconhecido**. |
| Precedência preservada | Human Approval continua **antes** da falha fechada, e os guardrails determinísticos continuam **primeiro de tudo**. A prosa continua sendo consultada mesmo quando há código comum declarado (código e texto em desacordo não passam). |
| **Não mudou** | `hermes/jev/policy_v1.yaml` (congelada) e `hermes/policies/*.yaml`: **intocados** (verificado por `git status`). Nenhum limiar, lane, perfil ou lista de ação proibida entrou no código. Nenhum sinônimo novo foi acrescentado às tabelas de conceitos. |

### 7.2 Evidência bruta 1 — as 5 frases que já escaparam, pelo CLI real

```
===== 1. as 5 frases que JA escaparam =====
--- D04: promocao de release para producao
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='aprovacao_de_producao' origem='texto_canonicalizado' dominios=['producao_ou_release'] (declarados=[], inferidos=['producao_ou_release'])
    motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
--- D04: promover release para producao
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='aprovacao_de_producao' origem='texto_canonicalizado' dominios=['producao_ou_release'] (declarados=[], inferidos=['producao_ou_release'])
    motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
--- D04: publicar release em producao
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='aprovacao_de_producao' origem='texto_canonicalizado' dominios=['producao_ou_release'] (declarados=[], inferidos=['producao_ou_release'])
    motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
--- D04: exclusao de registro de auditoria
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='exclusao_de_dado_de_cliente' origem='texto_canonicalizado' dominios=['dado_de_cliente'] (declarados=[], inferidos=['dado_de_cliente'])
    motivo=acao de decisao humana (exclusao_de_dado_de_cliente): nunca decidida por maquina
--- D06: enviar mensagem ao primeiro cliente interessado
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    codigo_de_acao=None origem='nao_classificada' dominios=['dado_de_cliente', 'outbound_a_terceiro'] (declarados=[], inferidos=['dado_de_cliente', 'outbound_a_terceiro'])
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo conhecido e a tarefa tem dominio sensivel ['dado_de_cliente', 'outbound_a_terceiro']
```

A frase do D06 sai agora por **falha fechada** (`ESCALATE`, `exit=2`), não por vocabulário: nenhuma tabela de sinônimos
foi ampliada para pegá-la.

### 7.3 Evidência bruta 2 — 5 frases **novas**, inventadas pelo executor deste card

Inventadas aqui (não copiadas do card, do D04, do D06 nem de teste anterior):

1. `enviar mensagem de apresentacao para prospect novo` (primeiro contato)
2. `enviar mensagem para lead recem-chegado` (primeiro contato)
3. `trocar a senha de servico do banco` (credencial)
4. `divulgar comunicado assinado pela Transformativa` (publicação em nome da Transformativa)
5. `apagar linha do cadastro de clientes` (dado de cliente)

```
===== 2. as 5 frases NOVAS inventadas pelo executor do card =====
--- nova (primeiro contato): enviar mensagem de apresentacao para prospect novo
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='primeiro_contato_outbound' origem='texto_canonicalizado' dominios=['outbound_a_terceiro'] (declarados=[], inferidos=['outbound_a_terceiro'])
    motivo=guardrail papel_sem_credencial_de_deploy: Sales AI nao tem credencial de deploy nem altera codigo (papel dev-harness nao pode: enviar e-mail ou mensagem em nome da Transformativa (canal outbound))
--- nova (primeiro contato): enviar mensagem para lead recem-chegado
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao=None origem='nao_classificada' dominios=['outbound_a_terceiro'] (declarados=[], inferidos=['outbound_a_terceiro'])
    motivo=guardrail papel_sem_credencial_de_deploy: Sales AI nao tem credencial de deploy nem altera codigo (papel dev-harness nao pode: enviar e-mail ou mensagem em nome da Transformativa (canal outbound))
--- nova (credencial): trocar a senha de servico do banco
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    codigo_de_acao=None origem='nao_classificada' dominios=['credencial'] (declarados=[], inferidos=['credencial'])
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo conhecido e a tarefa tem dominio sensivel ['credencial']
--- nova (publicacao): divulgar comunicado assinado pela Transformativa
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='publicacao_em_nome_da_transformativa' origem='texto_canonicalizado' dominios=[] (declarados=[], inferidos=[])
    motivo=acao de decisao humana (publicacao_em_nome_da_transformativa): nunca decidida por maquina
--- nova (dado de cliente): apagar linha do cadastro de clientes
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='exclusao_de_dado_de_cliente' origem='texto_canonicalizado' dominios=['dado_de_cliente'] (declarados=[], inferidos=['dado_de_cliente'])
    motivo=acao de decisao humana (exclusao_de_dado_de_cliente): nunca decidida por maquina
```

Camada que segurou cada uma: 1 e 2 pelo guardrail de papel (outbound no papel inferido), 3 pela **falha fechada**
(domínio credencial inferido, sem código), 4 e 5 pelo **código canônico** resolvido antes de decidir. Nenhuma executa.

### 7.4 Evidência bruta 3 — código canônico é a via principal; a falha fechada não é bloqueio geral

```
===== 3. codigo canonico como via principal (a falha fechada nao e bloqueio geral) =====
--- codigo COMUM 'ajuste_de_texto' + texto limpo (executa)
    exit=0
    decidido=executar outcome=PASS pode_executar=True lane=small campos_recibo=13
    codigo_de_acao='ajuste_de_texto' origem='codigo_canonico' dominios=[] (declarados=[], inferidos=[])
    motivo=confianca 0.95 >= limiar de aceite 0.85: aceita a lane
--- codigo PROIBIDO 'primeiro_contato_outbound' + texto limpo (bloqueia pelo codigo)
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='primeiro_contato_outbound' origem='codigo_canonico' dominios=[] (declarados=[], inferidos=[])
    motivo=acao de decisao humana (primeiro_contato_outbound): nunca decidida por maquina
--- codigo DESCONHECIDO + dominio sensivel declarado (escala)
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    codigo_de_acao=None origem='codigo_desconhecido' dominios=['credencial'] (declarados=['credencial'], inferidos=[])
    motivo=acao nao classificada com seguranca: o codigo declarado 'codigo-que-nao-existe' nao e conhecido e a tarefa tem dominio sensivel ['credencial']
--- codigo COMUM em desacordo com o texto sensivel (escala)
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='ajuste_de_texto' origem='codigo_canonico' dominios=['dado_de_cliente', 'outbound_a_terceiro'] (declarados=[], inferidos=['dado_de_cliente', 'outbound_a_terceiro'])
    motivo=acao nao classificada com seguranca: o codigo comum 'ajuste_de_texto' nao cobre o dominio sensivel que o texto declara e a tarefa tem dominio sensivel ['dado_de_cliente', 'outbound_a_terceiro']
```

O **comando do card** (`acao_codigo` = `primeiro_contato_outbound`) bloqueia com **texto da ação inocente**: quem
decide é o código. E o código **comum** segue executando (`exit=0`) quando não há domínio sensível — a falha fechada
**não** virou bloqueio geral.

### 7.5 Evidência bruta 4 — chamador que não declara sinal nenhum

```
===== 4. chamador NAO declara sinal nenhum (a inferencia do texto barra) =====
--- sem sinal declarado: enviar mensagem ao primeiro cliente interessado
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    codigo_de_acao=None origem='nao_classificada' dominios=['dado_de_cliente', 'outbound_a_terceiro'] (declarados=[], inferidos=['dado_de_cliente', 'outbound_a_terceiro'])
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo conhecido e a tarefa tem dominio sensivel ['dado_de_cliente', 'outbound_a_terceiro']
--- sem sinal declarado: trocar a senha de servico do banco
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    codigo_de_acao=None origem='nao_classificada' dominios=['credencial'] (declarados=[], inferidos=['credencial'])
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo conhecido e a tarefa tem dominio sensivel ['credencial']
--- sem sinal declarado, sem dominio sensivel: ajuste de texto simples (executa)
    exit=0
    decidido=executar outcome=PASS pode_executar=True lane=small campos_recibo=13
    codigo_de_acao=None origem='nao_classificada' dominios=[] (declarados=[], inferidos=[])
    motivo=confianca 0.95 >= limiar de aceite 0.85: aceita a lane
```

`declarados=[]` nos dois primeiros: **nenhum** sinal foi declarado pelo chamador, e a inferência do texto barrou do
mesmo jeito. O terceiro caso mostra o outro lado: sem domínio sensível, nada bloqueia.

### 7.6 Prova de que os itens REPROVAM se o mecanismo for removido

Reversão numa **cópia temporária** do roteador (arquivo versionado intocado), rodando só os itens do T07
(`/opt/data/cache/scratch/t07/prova_reversao.py`, que importa a suíte real por caminho):

```
=== mecanismo removido: codigo canonico neutralizado
    itens reprovados: 6  | do T07: 3
    REPROVOU T07: o codigo canonico e a via principal (acao_codigo bloqueia sem prosa)
    REPROVOU T07: acao resolvida por codigo canonico comum executa (a falha fechada nao virou bloqueio geral)
    REPROVOU T07: codigo desconhecido e codigo em desacordo com o texto nao abrem buraco
=== mecanismo removido: inferencia de dominio desligada
    itens reprovados: 7  | do T07: 4
    REPROVOU T07: as 5 frases que ja escaparam nao executam (4 do D04 + 1 do D06)
    REPROVOU T07: frases novas inventadas pelo executor (primeiro contato, credencial, publicacao, dado de cliente) nao executam
    REPROVOU T07: dominio sensivel inferido do texto barra mesmo sem sinal declarado
    REPROVOU T07: codigo desconhecido e codigo em desacordo com o texto nao abrem buraco
=== mecanismo removido: sinais declarados ignorados
    itens reprovados: 5  | do T07: 2
    REPROVOU T07: dominio sensivel declarado pelo chamador tambem barra a acao nao classificada
    REPROVOU T07: codigo desconhecido e codigo em desacordo com o texto nao abrem buraco
=== mecanismo removido: falha fechada removida
    itens reprovados: 8  | do T07: 5
    REPROVOU T07: as 5 frases que ja escaparam nao executam (4 do D04 + 1 do D06)
    REPROVOU T07: frases novas inventadas pelo executor (primeiro contato, credencial, publicacao, dado de cliente) nao executam
    REPROVOU T07: dominio sensivel inferido do texto barra mesmo sem sinal declarado
    REPROVOU T07: dominio sensivel declarado pelo chamador tambem barra a acao nao classificada
    REPROVOU T07: codigo desconhecido e codigo em desacordo com o texto nao abrem buraco
```

As 4 reversões também entraram no **autoteste por mutação** das duas suítes (roteador: 18/18 detectadas; validação
adversarial: 17/17 detectadas), então a prova roda sozinha no comando padrão.

### 7.7 Itens novos nas suítes (nenhum item existente foi relaxado)

Roteador (`scripts/verificar_jev_router.py`), 9 itens: código canônico como via principal (as 8 ações proibidas
bloqueadas pelo campo `acao_codigo`, com o código registrado); código comum executa; ação comum sem domínio sensível
segue executando; as 5 frases que escaparam; as 5 frases novas inventadas; domínio sensível **inferido** barra sem sinal
declarado (com o motivo obrigatório); domínio sensível **declarado** barra; código desconhecido / código em desacordo
com o texto; contrato de código (lados disjuntos e domínios declaráveis).

Validação adversarial (`scripts/validar_jev_guardrails.py`), 8 itens: os mesmos eixos, do lado adversarial, mais a
checagem de que a falha fechada não virou bloqueio geral. Total: roteador **46 → 55** itens, validação **64 → 72** itens,
**0 falhas e 0 achados** nas duas.

### 7.8 Limitações honestas do T07

1. **A inferência de texto continua sendo heurística e finita.** Ela infere quatro **domínios** (produção/release,
   credencial, dado de cliente, outbound a terceiro), não a **intenção** da ação. A defesa real é a falha fechada: se a
   ação não casar um domínio sensível **mesmo sendo sensível**, a dúvida escapa — o caminho durável é quem despacha
   passar **sempre** o código canônico (responsabilidade do card TRE-W0-E04-T05, ainda aberto). O T07 **reduz** a
   superfície (não depende mais de a prosa estar na tabela para barrar), mas não prova que qualquer frase equivalente
   pare.
2. **O lado "comum" do contrato de código é do roteador**, não da política congelada (`ajuste_de_texto`,
   `consulta_interna`, `operacao_comercial`). A política v1.0 nomeia só as ações proibidas, então o conjunto comum
   precisa existir em algum lugar; ele é fechado, disjunto do proibido (provado por item de suíte) e o T05 é quem deve
   passar a usá-lo. Se a política for revisada, os dois lados precisam ser reconciliados numa versão nova.
3. **A falha fechada é conservadora por desenho — e gera falso positivo.** Toda ação que (a) não resolva para código
   conhecido **e** (b) carregue domínio sensível passa a **escalar**, mesmo que seja legítima (ex.: uma consulta a
   `cadastro` que não altera nada). Isso é o custo aceito da falha fechada, mas **não** medi a taxa de falso positivo em
   cards reais: o corpus anotado do T03 não foi reprocessado.
4. **A extração da inferência é só o texto da ação** (`acao`/`titulo`), **não** o corpo do card. Se a sensibilidade
   estiver só na descrição (ex.: ação `ajuste de texto` num board cujo corpo fala de produção), a inferência não vê —
   inclusão do corpo do card aumentaria falso positivo e ficou fora do escopo; o sinal declarado (`sinais`) é o caminho
   para esse caso.
5. **`sinais` de domínio são aceitos por nome** (`producao_ou_release`, `credencial`, `dado_de_cliente`,
   `outbound_a_terceiro` + apelidos). Quem despacha precisa usar esses nomes; nome fora da lista continua caindo no
   guardrail de **sinal desconhecido** (fail-closed, BLOCK) — seguro, mas não é a falha fechada com o motivo do card.
6. **O roteador ainda não está ligado a nenhum dispatch** (card TRE-W0-E04-T05). Nada aqui muda o fato de que um card
   pode ser executado sem passar pelo JEV enquanto o encaixe não existir.
7. **O motivo "acao nao classificada com seguranca" não foi homologado pelo Anderson** como texto de recibo: entrou o
   texto que o D06/T07 pedem, literal. O critério de aceitação do card ainda é esboço ("a homologar").
8. **A tabela de conceitos continua lá** (não foi removida, e não devia ser: é a ponte de compatibilidade). Ela segue
   finita; o que mudou é que a decisão de segurança **não depende mais dela sozinha** — e há item de suíte provando isso.

## 8. Defeitos D07 e D08 — texto livre deixa de executar e o guardrail de DDL passa a exigir DDL real (cards TRE-W0-E04-T02-D07 e -D08)

**Cards:** TRE-W0-E04-T02-D07 (severidade alta) e TRE-W0-E04-T02-D08 (severidade média) · **Data:** 29/09/2026
**Alvo:** `hermes/jev/routing/router.py` (jev-router-v1.0). `hermes/jev/policy_v1.yaml` e `hermes/policies/*.yaml`
**não** foram tocados (congelados).
**O que estava aberto:**

- **D07** — a detecção de sensibilidade por vocabulário deixou escapar 4 ações sensíveis escritas com sinônimo não
  previsto: `conceder permissao de administrador ao usuario de testes` (concessão de credencial/acesso),
  `restaurar um backup por cima da base` (restauração/rollback), `soltar nota no site da Transformativa` (publicação em
  nome da empresa) e `limpar a base de contatos antigos` (exclusão de dado de cliente) saíam `executar/PASS`, `exit=0`.
  Terceira demonstração do mesmo lugar: a decisão dependia da **palavra**, não da **ação**.
- **D08** — `escrever e publicar o post do LinkedIn` (publicação, **nenhuma DDL**) era bloqueada com o motivo
  `guardrail ddl_fora_de_producao: DDL nao nasce em producao`. Causa medida: o gatilho do guardrail era o **casamento de
  prosa** da ação com entradas das políticas de papel que mencionam DDL — `post` casava `postgresql` e `escrever` casava
  `escrever`, duas entradas de PostgreSQL/DDL. Bloquear pelo motivo errado é pior que não bloquear: o recibo mentia sobre
  a causa e o humano ia pelo caminho errado.

### 8.1 Postura adotada (conservadora e reversível) e o que mudou

A decisão de postura foi tomada **em nome do dono** (reversível, conservadora de propósito), porque o defeito travava o
card TRE-W0-E04-T05 (ligar o roteador ao dispatch) e a alternativa de ampliar vocabulário já havia falhado três vezes.
**Homologada pelo dono em 29/09/2026 — ver 8.9:**

> O texto livre deixa de ser via executável. Uma ação **só executa** quando (a) resolve para um código canônico conhecido
> e não proibido, **E** (b) não há domínio sensível (declarado ou inferido), **E** (c) a confiança atende ao limiar.
> Texto que **não** resolve para código conhecido **não executa em nenhuma hipótese**: escala (`ESCALATE`, `exit=2`, lane
> conservadora) com motivo explícito de que faltou o código canônico. Enquanto o dispatch (card TRE-W0-E04-T05) não
> passar o código, escalar é o comportamento correto.

| Peça | O quê |
|---|---|
| **D07 — falha fechada por classe** | `_falha_fechada_por_acao_nao_classificada(resolucao, dominios)` deixou de exigir domínio sensível para barrar texto livre. Sem código canônico conhecido: **sempre** escala, com o motivo `acao nao classificada com seguranca: ... faltou o codigo canonico da acao ...`. Continua valendo o outro ramo: código **comum** cujo texto declara domínio sensível que ele não cobre também não executa (código e texto em desacordo é dúvida) |
| **D07 — vocabulário não foi ampliado** | Nenhum sinônimo novo entrou em `CONCEITOS_DE_ACAO`/`REGRAS_DE_ACAO_HUMANA`. As 4 frases do D07 não foram "pegas por vocabulário": elas escalam porque **nenhuma** ação sem código executa. O código proibido continua vindo da política congelada |
| **D07 — contrato de código** | `CODIGOS_DE_ACAO_COMUNS` ganhou **um** código, `migracao_de_esquema`: DDL/migration em desenvolvimento é operação legítima (o guardrail de DDL tem lado permitido) e, sem um código canônico para ela, a regra nova tornaria DDL impossível até em dev. O código **não** afrouxa o guardrail: quem decide o ambiente continua sendo o guardrail de DDL |
| **D08 — gatilho do guardrail de DDL** | `_e_acao_ddl` (casamento de prosa contra as políticas de papel) foi substituído por `_operacao_de_ddl(tarefa)`, que reconhece **DDL/migration REAL**: termo `DDL`, `migration`/`migracao`, comando SQL de definição (`CREATE/ALTER/DROP/TRUNCATE` + objeto), `GRANT/REVOKE` e operação de esquema declarada (adicionar/remover/criar coluna, índice, tabela). A busca é só nos **campos de declaração da própria tarefa** (`acao`, `titulo`, `descricao`, `comando`, `operacao`) — nunca em prosa de terceiros |
| **D08 — motivo verdadeiro** | O motivo do recibo diz a causa real: a operação reconhecida (com o trecho que casou) e o ambiente alvo. Numa ação sem DDL o guardrail é **avaliado e não acionado**, com detalhe vazio: nenhum motivo de DDL aparece no recibo |
| **Não mudou** | `hermes/jev/policy_v1.yaml` e `hermes/policies/*.yaml` intocados; nenhum limiar, lane, perfil ou lista de ação proibida entrou no código; as 8 ações de `nunca_decidido_por_maquina` continuam saindo pela camada Human Approval; a precedência (security → human approval → dependências → JEV) foi preservada |

### 8.2 Evidência bruta 1 — as 4 frases do D07 e as 5 que já bloqueavam, uma a uma pela CLI

Script: `bash /opt/data/cache/scratch/d07d08/evidencia_d07_d08.sh` (rodado da raiz do repo). Saída:

```
===== 1. D07: as 4 frases que escaparam (pre-correcao: PASS/exit 0) =====
--- conceder permissao de administrador ao usuario de testes
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo canonico conhecido: faltou o codigo canonico da acao e o texto livre nao e via executavel
--- restaurar um backup por cima da base
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo canonico conhecido: faltou o codigo canonico da acao e o texto livre nao e via executavel
--- soltar nota no site da Transformativa
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo canonico conhecido: faltou o codigo canonico da acao e o texto livre nao e via executavel
--- limpar a base de contatos antigos
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo canonico conhecido: faltou o codigo canonico da acao e o texto livre nao e via executavel
===== 2. D07: as 5 frases que ja bloqueavam continuam bloqueando =====
--- D04: promocao de release para producao
    exit=3
    decidido=bloquear outcome=BLOCK pode_executar=False lane=high campos_recibo=13
    codigo_de_acao='aprovacao_de_producao' origem='texto_canonicalizado' dominios=['producao_ou_release']
    motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
--- D04: promover release para producao
    exit=3  decidido=bloquear outcome=BLOCK codigo_de_acao='aprovacao_de_producao'
    motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
--- D04: publicar release em producao
    exit=3  decidido=bloquear outcome=BLOCK codigo_de_acao='aprovacao_de_producao'
    motivo=acao de decisao humana (aprovacao_de_producao): nunca decidida por maquina
--- D04: exclusao de registro de auditoria
    exit=3  decidido=bloquear outcome=BLOCK codigo_de_acao='exclusao_de_dado_de_cliente'
    motivo=acao de decisao humana (exclusao_de_dado_de_cliente): nunca decidida por maquina
--- D06: enviar mensagem ao primeiro cliente interessado
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    codigo_de_acao=None origem='nao_classificada' dominios=['dado_de_cliente', 'outbound_a_terceiro']
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo canonico conhecido: faltou o codigo canonico da acao e o texto livre nao e via executavel; o texto declara o dominio sensivel ['dado_de_cliente', 'outbound_a_terceiro']
```

As 4 do D07 saem `ESCALATE`, `exit=2`, lane conservadora (`high`) e recibo de 13 campos — pelo motivo do **código
canônico faltante**, não por vocabulário. As 5 conhecidas continuam parando (4 em `BLOCK/exit=3` pela camada Human
Approval; a do D06 em `ESCALATE/exit=2`).

### 8.3 Evidência bruta 2 — controle: a regra NÃO virou bloqueio geral

```
===== 3. Controle: codigo canonico comum + texto banal (NAO virou bloqueio geral) =====
--- codigo COMUM 'ajuste_de_texto' + texto banal (executa)
    exit=0
    decidido=executar outcome=PASS pode_executar=True lane=small campos_recibo=13
    codigo_de_acao='ajuste_de_texto' origem='codigo_canonico' dominios=[]
    motivo=confianca 0.95 >= limiar de aceite 0.85: aceita a lane
--- codigo COMUM 'operacao_comercial' + texto banal (executa)
    exit=0
    decidido=executar outcome=PASS pode_executar=True lane=medium campos_recibo=13
    codigo_de_acao='operacao_comercial' origem='codigo_canonico' dominios=[]
--- codigo COMUM 'migracao_de_esquema' + DDL em dev (executa)
    exit=0
    decidido=executar outcome=PASS pode_executar=True lane=small campos_recibo=13
    codigo_de_acao='migracao_de_esquema' origem='codigo_canonico' dominios=[]
```

### 8.4 Evidência bruta 3 — o motivo falso do D08 e o guardrail de DDL continuando funcional

```
===== 4. D08: motivo CORRIGIDO (acao sem DDL nao cita DDL) =====
--- D08: escrever e publicar o post do LinkedIn
    exit=2
    decidido=escalar_acao_nao_classificada outcome=ESCALATE pode_executar=False lane=high campos_recibo=13
    guardrails_acionados=[]
    motivo=acao nao classificada com seguranca: a acao nao resolve para nenhum codigo canonico conhecido: faltou o codigo canonico da acao e o texto livre nao e via executavel; o texto declara o dominio sensivel ['producao_ou_release']
===== 5. D08: o guardrail de DDL continua funcionando (DDL real em ambiente errado) =====
--- DDL declarada, ambiente producao (BLOCK pelo DDL)
    exit=3  decidido=bloquear outcome=BLOCK guardrails_acionados=['ddl_fora_de_producao']
    motivo=guardrail ddl_fora_de_producao: DDL nao nasce em producao (DDL declarada no texto da tarefa (termo 'DDL'): 'DDL'; ambiente alvo producao)
--- DDL declarada, ambiente NAO declarado (BLOCK pelo DDL)
    exit=3  decidido=bloquear outcome=BLOCK guardrails_acionados=['ddl_fora_de_producao']
    motivo=guardrail ddl_fora_de_producao: DDL nao nasce em producao (DDL declarada no texto da tarefa (termo 'DDL'): 'DDL'; ambiente alvo nao declarado)
--- comando SQL de DDL real (CREATE TABLE), ambiente producao
    exit=3  decidido=bloquear outcome=BLOCK guardrails_acionados=['ddl_fora_de_producao']
    motivo=guardrail ddl_fora_de_producao: DDL nao nasce em producao (comando de definicao de esquema (CREATE/ALTER/DROP/TRUNCATE + objeto): 'CREATE TABLE'; ambiente alvo producao)
--- DDL declarada, ambiente dev (executa: lado permitido do guardrail)
    exit=0  decidido=executar outcome=PASS pode_executar=True guardrails_acionados=[]
```

A ação do D08 agora **não** carrega guardrail de DDL nem motivo de DDL: ela escala pelo contrato novo (texto livre sem
código canônico, com domínio `producao_ou_release` inferido do próprio texto). E o guardrail de DDL, quando há DDL real,
continua bloqueando em produção/ambiente não declarado e liberando em desenvolvimento — com o motivo dizendo **qual**
operação foi reconhecida e **qual** era o ambiente.

O item de prova também confere o lado silencioso do guardrail (ação sem DDL, com `ambiente_alvo=producao` declarado —
o cenário exato do defeito):

```
===== 6. D08: o guardrail de DDL e AVALIADO e nao ACIONADO em acao sem DDL =====
    'escrever e publicar o post do LinkedIn'
      decidido=escalar_acao_nao_classificada guardrails_acionados=[] citou_ddl_no_motivo=False
      guardrail ddl_fora_de_producao: avaliado=True acionado=False detalhe='' (ambiente_alvo=producao declarado)
    ... (exportar a planilha do time comercial / revisar o texto do rodape do site / as 2 frases do D07) ...
    ACHADOS de motivo/guardrail de DDL sem DDL: 0 (esperado 0)
```

### 8.5 Itens de suíte que precisaram mudar (e por quê)

A regra nova invalida itens que **esperavam que texto livre comum executasse**. O ajuste foi sempre no mesmo sentido:
declarar o **código canônico** da ação (`acao_codigo`) para que o item continue medindo o que media. **Nenhum item foi
relaxado** — os itens de execução passaram a exigir código, e itens novos provam que o texto livre escalou.

| Arquivo | Item | Mudança | Por quê |
|---|---|---|---|
| `scripts/verificar_jev_router.py` | criterio 2 (lane/perfil/confianca), criterio 2 (limiar do YAML), criterio 2 (lane fora da política), criterio 3 (abstenção), criterio 3 (confiança ausente), criterio 3 (borda), criterio 3 (faixa conservadora), recibo/override | passaram a declarar `acao_codigo="ajuste_de_texto"` | o texto livre desses itens não executa mais; sem o código o item mediria a falha fechada em vez do critério |
| `scripts/verificar_jev_router.py` | T02/harness (Sales AI: credencial proibida, operação comercial, papel desconhecido) | `acao_codigo="operacao_comercial"`; o item da credencial proibida passou a exigir também o guardrail `papel_sem_credencial_de_deploy` | isola a causa: o bloqueio tem de vir da credencial/papel, não de a ação ser inclassificável (mais estrito) |
| `scripts/verificar_jev_router.py` | board (card, dependência, recibo) | a prova da dependência passou a usar uma tarefa **com** código; somou-se a prova de que o card lido do board (texto livre) **escala** | a camada de dependência continua provada; a leitura pura do card agora escala por falta de código (fato novo, provado no mesmo item) |
| `scripts/verificar_jev_router.py` | "T07: ação sem domínio sensível segue executando" | **substituído** por três itens: (a) as 4 ações do D07 escalam; (b) texto livre comum (sem domínio sensível) também não executa; (c) os códigos comuns seguem executando | o item antigo media um contrato revogado; os três novos provam a classe (não depende de parecer sensível) e o controle (não virou bloqueio geral) |
| `scripts/validar_jev_guardrails.py` | guardrail segredo (APROVA), do_not_contact (APROVA), DDL (REPROVA e APROVA), papel/credencial (APROVA), fail-closed (APROVA), abstenção, T07 "não é bloqueio geral" | passaram a declarar `acao_codigo` (DDL: `migracao_de_esquema`) | mesma razão: o caminho APROVA tem de chegar ao critério, e não parar na falha fechada |
| `scripts/validar_jev_guardrails.py` | T07 "ação comum sem domínio sensível segue executando" | virou "segue executando **quando tem** código canônico" (com os 4 códigos comuns) e ganhou o item D07 de que texto livre sem código não executa | idem |
| ambos | — | **itens novos**: D07 (4 escapes + texto comum escalam, com o motivo obrigatório), D07 (códigos comuns continuam executando), D08 (nenhum motivo/guardrail de DDL em ação sem DDL), D08 (DDL real bloqueia com o motivo dizendo a causa) | é o teste que reprova se o defeito voltar |
| ambos | autoteste por mutação | **3 mutações novas**: D07 (falha fechada volta a exigir domínio sensível), D08 (guardrail de DDL volta a acionar sem DDL real), D08 (motivo deixa de dizer a causa) | prova que os itens novos reprovam o mutante — a prova roda sozinha no comando padrão |

Nota de execução: a suíte do roteador também **mantém** o item de CLI com `acao_codigo` explícito (`ajuste_de_texto`)
saindo `exit=0`; ele é a prova de que o caminho de execução continua existindo pelo campo de código.

### 8.6 Prova de reversão — o item REPROVA se a regra nova for removida

Reversão em **cópia temporária** do roteador (arquivos versionados intocados), rodando as duas suítes reais por caminho
(`/opt/data/cache/scratch/d07d08/prova_reversao.py`):

```
=== D07 reverso: texto livre sem codigo volta a executar quando o texto nao declara dominio
    suite do roteador: 6 itens reprovados | D07/D08: 2
      REPROVOU D07: as 4 acoes sensiveis que escapavam nao executam — escalam por falta de codigo canonico, nao por vocabulario
      REPROVOU D07: texto livre comum, sem dominio sensivel, tambem nao executa — a regra nao depende de parecer sensivel
    validacao adversarial: 1 FALHOU / 0 ACHADO | D07/D08: 1
      REPROVOU D07 — texto livre sem codigo canonico NAO executa (as 4 que escapavam + texto comum), escalando com o motivo do codigo faltante
=== D08 reverso: guardrail de DDL volta a acionar sem DDL real no texto
    suite do roteador: 20 itens reprovados | D07/D08: 5
      REPROVOU D07: as 4 acoes sensiveis que escapavam nao executam — escalam por falta de codigo canonico, nao por vocabulario
      REPROVOU D07: texto livre comum, sem dominio sensivel, tambem nao executa — a regra nao depende de parecer sensivel
      REPROVOU D07: codigo canonico comum com texto limpo segue executando (a regra nao virou bloqueio geral)
      REPROVOU D08: acao sem DDL nao carrega motivo nem guardrail de DDL (o recibo nao mente)
      REPROVOU D08: DDL/migration real em ambiente errado continua bloqueando, com o motivo dizendo a causa
    validacao adversarial: 11 FALHOU / 0 ACHADO | D07/D08: 3
      REPROVOU guardrail DDL — D08: so aciona com DDL/migration REAL e o motivo diz a causa real (nenhum motivo de DDL em acao sem DDL)
      REPROVOU T07/D07 — acao comum sem dominio sensivel segue executando QUANDO tem codigo canonico (nao ha bloqueio geral)
      REPROVOU D07 — texto livre sem codigo canonico NAO executa (as 4 que escapavam + texto comum), escalando com o motivo do codigo faltante
=== D08 reverso: motivo do guardrail de DDL deixa de dizer a causa real
    suite do roteador: 4 itens reprovados | D07/D08: 1
      REPROVOU D08: DDL/migration real em ambiente errado continua bloqueando, com o motivo dizendo a causa
    validacao adversarial: 1 FALHOU / 0 ACHADO | D07/D08: 1
      REPROVOU guardrail DDL — D08: so aciona com DDL/migration REAL e o motivo diz a causa real (nenhum motivo de DDL em acao sem DDL)
```

As três reversões também entraram no **autoteste por mutação** das duas suítes (roteador: **21/21** detectadas; validação
adversarial: **20/20**), então a prova roda sozinha no comando padrão.

### 8.7 Contagens finais

| Suíte | Resultado |
|---|---|
| `scripts/verificar_jev_router.py --autoteste` | `PASS (59 itens, 0 falhas)`, **`autoteste 21/21`** (55 itens / 18 mutações antes) |
| `scripts/validar_jev_guardrails.py --autoteste --estrito` | `PASS (74 itens, 0 falhas)`, **0 achados**, **`autoteste 20/20`** (72 itens / 17 mutações antes) |
| `scripts/verificar_jev_policy.py --autoteste` | `PASS (42 itens, 0 falhas)` + `autoteste 12/12` |
| `bash scripts/verificar_papeis.sh` / `bash scripts/secret_scan.sh` | `PASS (0 falhas)` / `PASS (nenhum segredo versionado)` |

### 8.8 Limitações honestas do D07/D08

1. **A postura é conservadora e foi tomada em nome do dono, depois homologada.** O custo é explícito: **todo card que
   chega como texto livre** — inclusive os que já existem no board e são legítimos — passa a **escalar** até o dispatch
   (card TRE-W0-E04-T05) passar o código canônico. Anderson decidiu em 29/09/2026 manter o critério estrito agora e
   revisitar só depois do T05 (ver 8.9, com o impacto medido em 4 de 32 casos do corpus); continua reversível numa linha.
2. **O código canônico é do roteador, não da política congelada.** O lado comum virou quatro códigos
   (`ajuste_de_texto`, `consulta_interna`, `operacao_comercial`, `migracao_de_esquema`). O conjunto é fechado e disjunto
   do proibido (provado por item), mas a política v1.0 nomeia só as ações proibidas — se a política for revisada, os
   dois lados precisam ser reconciliados numa versão nova.
3. **Não medi a taxa de falso positivo** em cards reais: o corpus anotado do T03 não foi reprocessado. A diferença é que
   antes o falso positivo era *executar o que não devia*; agora é *escalar o que podia executar* (fail-closed).
4. **O roteador continua não ligado a nenhum dispatch** (card TRE-W0-E04-T05). Nada aqui muda o fato de que um card pode
   ser executado sem passar pelo JEV enquanto o encaixe não existir.
5. **A detecção de DDL é por formato declarado**, não por análise semântica: uma DDL escrita em prosa que não use
   nenhum dos termos reconhecidos (por exemplo "acrescentar um campo naquela tabela de clientes") não aciona o guardrail
   de DDL. Ela continua barrada pelo outro lado — sem código canônico, a ação **escala** —, mas o motivo seria o do
   código faltante, não o do ambiente.
6. **DDL em desenvolvimento passa a exigir código** (`migracao_de_esquema`). Quem despachar DDL/dump/restore precisa
   passar esse código; texto livre de migration, mesmo em dev, escala.
7. **O motivo "acao nao classificada com seguranca" continua não homologado** pelo Anderson como texto de recibo (mesma
   limitação 7 do T07); o D07 apenas acrescentou o fato faltante ("faltou o codigo canonico da acao").

### 8.9 Homologação da postura (29/09/2026) — manter estrito até o T05

O dono decidiu em **29/09/2026 (19:36 BRT, Telegram)**: **manter o critério estrito agora** e **revisitar depois do
T05**. Palavra dele: *"seguirei Recomendação: manter estrito agora e revisitar só depois do T05. Quando o dispatch
passar `acao_codigo`, a postura estrita deixa de custar qualquer coisa (não chega texto livre) e a discussão de
afrouxar vira irrelevante."*

**Impacto medido que sustentou a decisão** (`scripts/analisar_impacto_de_afrouxar.py`, sobre os 32 casos do corpus
anotado; estrito = o que está no repo, afrouxado = reversão do D07 em cópia temporária):

```
casos no corpus: 32
veredito ESTRITO  : {'ESCALATE': 21, 'PASS': 2, 'BLOCK': 9}
veredito AFROUXADO: {'ESCALATE': 17, 'PASS': 6, 'BLOCK': 9}
passam a EXECUTAR se afrouxar: 4 de 32 (12.5%)
    real-t_969affa7 acao=execucao_de_card    lane_proposta=high  lane_esperada=high  sinais=['ddl_ou_migration']
    real-t_d9cb5755 acao=execucao_de_card    lane_proposta=high  lane_esperada=high  sinais=['ddl_ou_migration']
    real-t_1acf11f2 acao=execucao_de_card    lane_proposta=high  lane_esperada=-     sinais=[]
    borda-07        acao=ajuste_de_interface lane_proposta=small lane_esperada=-     sinais=[]
codigos comuns do roteador: ['ajuste_de_texto', 'consulta_interna', 'migracao_de_esquema', 'operacao_comercial']
codigos proibidos (politica): 8 (nunca_decidido_por_maquina)
acoes do corpus que o roteador NAO conhece: ['ajuste_de_interface', 'concessao_de_credencial',
    'decisao_de_arquitetura', 'exclusao_de_dado', 'execucao_de_card', 'migration',
    'publicacao_institucional', 'resposta_a_cliente', 'rollback_producao', 'troca_de_credencial']
acoes do corpus que ele conhece: ['ajuste_de_texto', 'primeiro_contato_outbound']
```

Leitura do número: **afrouxar compra 4 cards de 32 (12,5%)** — dois deles DDL em ambiente novo (o falso positivo real
do critério) e dois sem sinal nenhum declarado (exatamente o perfil por onde os 4 escapes do D07 entraram) — e **não
muda nada** nos 9 `BLOCK` nem em 17 das 21 escaladas. Em troca, volta o critério por vocabulário, sem cerca para o que
ninguém previu.

**Achado de integração que a medição expôs:** das 12 ações distintas do corpus, o roteador conhece **2**. Ou seja, hoje
os 20 cards reais do corpus escalam por **desencontro de vocabulário**, não pela postura — afrouxar o D07 não destrava
o corpus (compraria 4 cards). Quem destrava é a ponte de códigos (T05 / política v1.1).

Premissas da medição (contestáveis; o cabeçalho do script as declara): sinais do corpus mapeados para os do roteador
(`producao`→`producao_ou_release`, `mexe_em_segredo`→`credencial`, `outbound_para_terceiro`→`outbound_a_terceiro`);
`ddl_ou_migration` e `aprova_humana_exigida` não têm sinal equivalente e não foram injetados; `ambiente_alvo` =
produção quando `producao: true`, senão desenvolvimento; confiança 0,95 e lane `small` em todos (o corpus não carrega
confiança).

Formas de afrouxar — descartadas e aceitas (registrado para a decisão não virar oral):

| Forma | Veredito |
|---|---|
| Vocabulário (voltar ao pré-D07) | **descartada** — reabre a classe; falhou 3 vezes (D04, D06, D07) |
| Allowlist de códigos (nomear mais um código comum, com revisão na política) | **aceita** como a única forma de afrouxar, se e quando for preciso — é auditável e reversível |
| Só o sinal do chamador, sem código | **descartada por ora** — transfere a confiança para o dispatch que ainda não existe (T05); omissão do chamador vira execução silenciosa (foi assim que os 4 escapes passaram) |

Registro no board: comentário nos cards `t_83242193` (D07) e `t_4200e054` (D08). Registro de aprovação humana:
`docs/operations/registro-de-aprovacoes.md`.

## Anexo A — saída bruta da suíte, pós-correção (64 itens, 0 achados) — estado anterior ao T07 (seção 7)

```
==============================================================================
VALIDACAO DOS GUARDRAILS E DO FALLBACK DO JEV (card TRE-W0-E04-T04)
  roteador: hermes/jev/routing/router.py
  politica: hermes/jev/policy_v1.yaml
  papeis:   hermes/policies
==============================================================================
OK     guardrail segredo — REPROVA: 6 formas de segredo bloqueiam  [6 formas bloqueadas com BLOCK]
OK     guardrail segredo — APROVA: payload limpo executa  [decidido=executar outcome=PASS lane=small guardrails=[]]
OK     guardrail segredo — APROVA: segredo bloqueado nao aparece em decisao/recibo  [nem o valor nem o formato aparecem na decisao/recibo]
OK     guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia  [decidido=bloquear outcome=BLOCK lane=high guardrails=['do_not_contact']]
OK     guardrail do_not_contact — APROVA: sem marcacao ou sem outbound executa  [outbound sem marcacao executa; empresa marcada em acao interna executa]
OK     guardrail DDL — REPROVA: DDL em producao ou sem ambiente bloqueia  [DDL em producao e DDL sem ambiente declarado bloqueiam]
OK     guardrail DDL — APROVA: DDL em ambiente de desenvolvimento executa  [DDL em desenvolvimento/dev executa]
OK     guardrail Sales AI x credencial de deploy — REPROVA: bloqueia  [deploy, credencial proibida e papel desconhecido bloqueiam]
OK     guardrail Sales AI x credencial de deploy — APROVA: acao/credencial permitida executa  [acao comercial do sales-ai e credencial permitida executam]
OK     guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam  [sinal desconhecido e sinais None bloqueiam; payload_valido aciona para tipo invalido]
OK     guardrail fail-closed — APROVA: entrada valida e sem sinal desconhecido executa  [decidido=executar outcome=PASS lane=small guardrails=[]]
OK     guardrail fail-closed na leitura — REPROVA: politica sem guardrail declarado e recusada  [os 5 guardrails exigidos: politica que para de declara-los e recusada]
OK     acao proibida aprovacao_de_producao — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida primeiro_contato_outbound — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida envio_de_proposta_comercial — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida mudanca_estrutural_de_arquitetura — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rollback_em_producao — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida exclusao_de_dado_de_cliente — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rotacao_ou_revogacao_de_credencial — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida publicacao_em_nome_da_transformativa — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida aprovacao_de_producao — PROSA 'aprovar promocao de release para producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida aprovacao_de_producao — PROSA 'promocao de release para producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida aprovacao_de_producao — PROSA 'promover release para producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida aprovacao_de_producao — PROSA 'publicar release em producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida aprovacao_de_producao — PROSA 'deploy em producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida aprovacao_de_producao — PROSA 'publicar versao em producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida primeiro_contato_outbound — PROSA 'primeiro contato outbound por e-mail para empresa nova' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida primeiro_contato_outbound — PROSA 'primeiro contato com empresa nova via LinkedIn' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida primeiro_contato_outbound — PROSA 'enviar mensagem no WhatsApp para lead novo' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida envio_de_proposta_comercial — PROSA 'enviar proposta comercial ao cliente' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida envio_de_proposta_comercial — PROSA 'envio de proposta comercial' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida mudanca_estrutural_de_arquitetura — PROSA 'mudanca estrutural de arquitetura exige ADR' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida mudanca_estrutural_de_arquitetura — PROSA 'alteracao estrutural de arquitetura' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rollback_em_producao — PROSA 'rollback em producao apos incidente' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rollback_em_producao — PROSA 'rollback em producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida exclusao_de_dado_de_cliente — PROSA 'excluir dado de cliente' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida exclusao_de_dado_de_cliente — PROSA 'exclusao de registro de auditoria' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida exclusao_de_dado_de_cliente — PROSA 'apagar dado de cliente do banco' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida exclusao_de_dado_de_cliente — PROSA 'remover cadastro de titular' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rotacao_ou_revogacao_de_credencial — PROSA 'rotacao de credencial de producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rotacao_ou_revogacao_de_credencial — PROSA 'revogar credencial de deploy' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida publicacao_em_nome_da_transformativa — PROSA 'publicar conteudo em nome da Transformativa' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida publicacao_em_nome_da_transformativa — PROSA 'publicacao em nome da Transformativa sem aprovacao expressa' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida publicacao_em_nome_da_transformativa — PROSA 'postar em nome da Transformativa' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     fallback — REPROVA: politica corrompida/versao desconhecida/secao faltando sao recusadas na leitura  [corrompida, versao desconhecida e secao faltando sao recusadas na leitura]
OK     fallback — REPROVA: qualquer secao obrigatoria faltando e recusada  [as 8 secoes obrigatorias: removida uma a uma, todas recusadas]
OK     fallback — APROVA: politica indisponivel entra em degradado conservador sem execucao  [lane=high degradado=True outcome=ESCALATE confidence=None]
OK     fallback — APROVA: no degradado os guardrails continuam rodando primeiro  [decidido=bloquear outcome=BLOCK lane=high guardrails=['segredo_sem_payload']]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ausente -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ilegivel/corrompida -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: versao de politica desconhecida -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: secao guardrails faltando -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ausente no caminho padrao -> degradado, nunca execucao silenciosa  [exit=2 lane=high degradado=True policy_version=None]
OK     fallback ponta a ponta (CLI) — APROVA: guardrails rodam primeiro nos 4 cenarios quebrados  [nos 4 cenarios quebrados o guardrail de segredo bloqueou (BLOCK) antes do fallback]
OK     fallback — APROVA: abstencao (confianca baixa/ausente, lane desconhecida) escala na lane conservadora  [abstencao usa a lane conservadora e escala nos 3 casos]
OK     fail-closed ponta a ponta — payload nao-mapa bloqueia  [decidido=bloquear outcome=BLOCK lane=high guardrails=['payload_valido']]
OK     fail-closed ponta a ponta — sinais nao-mapa bloqueia  [decidido=bloquear outcome=BLOCK lane=high guardrails=['payload_valido']]
OK     acao declarada em human-approval.yaml — BLOQUEIO (9 declaracoes)  [todas bloqueiam ou escalam]
OK     cobertura D03 — a fonte human-approval.yaml entra na camada Human Approval sem depender de role: e nao executa  [9 declaracoes da fonte entram na camada e bloqueiam com BLOCK]
OK     cobertura D05 — entrada de tipo invalido termina em BLOCK com recibo de 13 campos, sem excecao  [3 entradas de tipo invalido: BLOCK, recibo de 13 campos, payload_valido registrado]
OK     cobertura — cada guardrail do YAML tem teste de REPROVA e de APROVA  [5 guardrails declarados no YAML, 5 regras, todos com teste de REPROVA e de APROVA]
OK     cobertura — cada acao proibida do YAML tem teste de bloqueio e de prosa  [8 acoes de nunca_decidido_por_maquina com teste de bloqueio e com variacao em prosa]
OK     sanity — o roteador carrega a politica e os papeis do repo  [versao=jev-policy-v1.0 lanes=['small', 'medium', 'high', 'critical'] papeis=['dev-harness', 'sales-ai']]
OK     sanity — o roteador nao foi alterado por esta suite (arquivos do repo intactos)  [hermes/jev/routing/router.py hermes/jev/policy_v1.yaml docs/architecture/jev-decision-policy-v1.md]

=== AUTOTESTE: mutacoes que a suite precisa reprovar ===
OK    detectada: guardrail segredo removido (payload com segredo passa)  (3 item(ns) reprovado(s): guardrail segredo — REPROVA: 6 formas de segredo bloqueiam, fallback — APROVA: no degradado os guardrails continuam rodando primeiro...)
OK    detectada: guardrail do_not_contact removido (empresa marcada e contatada)  (1 item(ns) reprovado(s): guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia)
OK    detectada: guardrail DDL removido (DDL nasce em producao)  (1 item(ns) reprovado(s): guardrail DDL — REPROVA: DDL em producao ou sem ambiente bloqueia)
OK    detectada: guardrail de papel/credencial removido (Sales AI com deploy)  (1 item(ns) reprovado(s): guardrail Sales AI x credencial de deploy — REPROVA: bloqueia)
OK    detectada: fail-closed removido no sinal desconhecido  (1 item(ns) reprovado(s): guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam)
OK    detectada: fail-closed removido na entrada de tipo invalido  (2 item(ns) reprovado(s): guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam, cobertura D05 — entrada de tipo invalido termina em BLOCK com recibo de 13 campos, sem excecao)
OK    detectada: guardrails deterministas deixam de rodar  (5 item(ns) reprovado(s): guardrail segredo — REPROVA: 6 formas de segredo bloqueiam, guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam...)
OK    detectada: nenhum guardrail bloqueia (a barreira e decorativa)  (8 item(ns) reprovado(s): guardrail segredo — REPROVA: 6 formas de segredo bloqueiam, guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia...)
OK    detectada: fallback executa em modo degradado  (6 item(ns) reprovado(s): fallback — APROVA: politica indisponivel entra em degradado conservador sem execucao, fallback ponta a ponta (CLI) — REPROVA: politica ausente -> lane conservadora + degradado, sem execucao...)
OK    detectada: fallback usa lane barata em vez da conservadora  (7 item(ns) reprovado(s): fallback — APROVA: politica indisponivel entra em degradado conservador sem execucao, fallback ponta a ponta (CLI) — REPROVA: politica ausente -> lane conservadora + degradado, sem execucao...)
OK    detectada: roteador deixa de exigir o guardrail declarado na politica  (1 item(ns) reprovado(s): guardrail fail-closed na leitura — REPROVA: politica sem guardrail declarado e recusada)
OK    detectada: versao de politica desconhecida aceita  (2 item(ns) reprovado(s): fallback — REPROVA: politica corrompida/versao desconhecida/secao faltando sao recusadas na leitura, fallback ponta a ponta (CLI) — REPROVA: versao de politica desconhecida -> lane conservadora + degradado, sem execucao)
OK    detectada: precedencia humana ignorada (Human Approval contornado)  (6 item(ns) reprovado(s): acao proibida primeiro_contato_outbound — BLOQUEIO por nome exato, acao proibida envio_de_proposta_comercial — BLOQUEIO por nome exato...)

autoteste: 13/13 mutacoes detectadas

itens: 64 (61 de criterio, 3 adversariais) | falhas: 0 | achados: 0
RESULTADO: PASS (64 itens, 0 falhas) + autoteste OK
```

E a suíte do roteador (T02), pós-correção — os itens novos são os 4 últimos antes de "arquivos do roteador":

```
OK    D03: a fonte human-approval.yaml entra na camada Human Approval sem depender de role: (9 declaracoes nao executam)  [9 declaracoes da fonte bloqueiam com BLOCK, sem chave role:]
OK    D04: as 4 frases em prosa que escapavam do bloqueio terminam em BLOCK  [as 4 frases em prosa que escapavam do bloqueio terminam em BLOCK]
OK    D04: o vocabulario canonico do roteador nao inventa nem omite acao da politica  [8 regras canonicas alinhadas com 8 acoes de nunca_decidido_por_maquina]
OK    D05: entrada de tipo invalido termina em BLOCK com recibo, nunca em excecao  [3 entradas de tipo invalido: BLOCK com recibo de 13 campos, sem excecao]
...
autoteste: 14/14 mutacoes detectadas

RESULTADO: PASS (46 itens, 0 falhas) + autoteste OK
```

## Anexo A.1 — histórico pré-correção (60 itens, 7 achados)

```
==============================================================================
VALIDACAO DOS GUARDRAILS E DO FALLBACK DO JEV (card TRE-W0-E04-T04)
  roteador: hermes/jev/routing/router.py
  politica: hermes/jev/policy_v1.yaml
  papeis:   hermes/policies
==============================================================================
OK     guardrail segredo — REPROVA: 6 formas de segredo bloqueiam  [6 formas bloqueadas com BLOCK]
OK     guardrail segredo — APROVA: payload limpo executa  [decidido=executar outcome=PASS lane=small guardrails=[]]
OK     guardrail segredo — APROVA: segredo bloqueado nao aparece em decisao/recibo  [nem o valor nem o formato aparecem na decisao/recibo]
OK     guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia  [decidido=bloquear outcome=BLOCK lane=high guardrails=['do_not_contact']]
OK     guardrail do_not_contact — APROVA: sem marcacao ou sem outbound executa  [outbound sem marcacao executa; empresa marcada em acao interna executa]
OK     guardrail DDL — REPROVA: DDL em producao ou sem ambiente bloqueia  [DDL em producao e DDL sem ambiente declarado bloqueiam]
OK     guardrail DDL — APROVA: DDL em ambiente de desenvolvimento executa  [DDL em desenvolvimento/dev executa]
OK     guardrail Sales AI x credencial de deploy — REPROVA: bloqueia  [deploy, credencial proibida e papel desconhecido bloqueiam]
OK     guardrail Sales AI x credencial de deploy — APROVA: acao/credencial permitida executa  [acao comercial do sales-ai e credencial permitida executam]
OK     guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam  [sinal desconhecido e sinais None bloqueiam; payload_valido aciona para tipo invalido]
OK     guardrail fail-closed — APROVA: entrada valida e sem sinal desconhecido executa  [decidido=executar outcome=PASS lane=small guardrails=[]]
OK     guardrail fail-closed na leitura — REPROVA: politica sem guardrail declarado e recusada  [os 5 guardrails exigidos: politica que para de declara-los e recusada]
OK     acao proibida aprovacao_de_producao — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida primeiro_contato_outbound — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida envio_de_proposta_comercial — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida mudanca_estrutural_de_arquitetura — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rollback_em_producao — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida exclusao_de_dado_de_cliente — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rotacao_ou_revogacao_de_credencial — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida publicacao_em_nome_da_transformativa — BLOQUEIO por nome exato  [BLOCK por nome exato — decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida aprovacao_de_producao — PROSA 'aprovar promocao de release para producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
ACHADO acao proibida aprovacao_de_producao — PROSA 'promocao de release para producao' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
ACHADO acao proibida aprovacao_de_producao — PROSA 'promover release para producao' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
ACHADO acao proibida aprovacao_de_producao — PROSA 'publicar release em producao' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
OK     acao proibida aprovacao_de_producao — PROSA 'deploy em producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida primeiro_contato_outbound — PROSA 'primeiro contato outbound por e-mail para empresa nova' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida primeiro_contato_outbound — PROSA 'primeiro contato com empresa nova via LinkedIn' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida primeiro_contato_outbound — PROSA 'enviar mensagem no WhatsApp para lead novo' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida envio_de_proposta_comercial — PROSA 'enviar proposta comercial ao cliente' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida envio_de_proposta_comercial — PROSA 'envio de proposta comercial' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida mudanca_estrutural_de_arquitetura — PROSA 'mudanca estrutural de arquitetura exige ADR' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida mudanca_estrutural_de_arquitetura — PROSA 'alteracao estrutural de arquitetura' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rollback_em_producao — PROSA 'rollback em producao apos incidente' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rollback_em_producao — PROSA 'rollback em producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida exclusao_de_dado_de_cliente — PROSA 'excluir dado de cliente' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
ACHADO acao proibida exclusao_de_dado_de_cliente — PROSA 'exclusao de registro de auditoria' bloqueia ou escala  [decidido=executar outcome=PASS lane=small guardrails=[]]
OK     acao proibida exclusao_de_dado_de_cliente — PROSA 'apagar dado de cliente do banco' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rotacao_ou_revogacao_de_credencial — PROSA 'rotacao de credencial de producao' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida rotacao_ou_revogacao_de_credencial — PROSA 'revogar credencial de deploy' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=[]]
OK     acao proibida publicacao_em_nome_da_transformativa — PROSA 'publicar conteudo em nome da Transformativa' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida publicacao_em_nome_da_transformativa — PROSA 'publicacao em nome da Transformativa sem aprovacao expressa' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     acao proibida publicacao_em_nome_da_transformativa — PROSA 'postar em nome da Transformativa' bloqueia ou escala  [decidido=bloquear outcome=BLOCK lane=high guardrails=['papel_sem_credencial_de_deploy']]
OK     fallback — REPROVA: politica corrompida/versao desconhecida/secao faltando sao recusadas na leitura  [corrompida, versao desconhecida e secao faltando sao recusadas na leitura]
OK     fallback — REPROVA: qualquer secao obrigatoria faltando e recusada  [as 8 secoes obrigatorias: removida uma a uma, todas recusadas]
OK     fallback — APROVA: politica indisponivel entra em degradado conservador sem execucao  [lane=high degradado=True outcome=ESCALATE confidence=None]
OK     fallback — APROVA: no degradado os guardrails continuam rodando primeiro  [decidido=bloquear outcome=BLOCK lane=high guardrails=['segredo_sem_payload']]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ausente -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ilegivel/corrompida -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: versao de politica desconhecida -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: secao guardrails faltando -> lane conservadora + degradado, sem execucao  [exit=2 lane=high degradado=True outcome=ESCALATE confidence=None recibo=13 campos]
OK     fallback ponta a ponta (CLI) — REPROVA: politica ausente no caminho padrao -> degradado, nunca execucao silenciosa  [exit=2 lane=high degradado=True policy_version=None]
OK     fallback ponta a ponta (CLI) — APROVA: guardrails rodam primeiro nos 4 cenarios quebrados  [nos 4 cenarios quebrados o guardrail de segredo bloqueou (BLOCK) antes do fallback]
OK     fallback — APROVA: abstencao (confianca baixa/ausente, lane desconhecida) escala na lane conservadora  [abstencao usa a lane conservadora e escala nos 3 casos]
ACHADO fail-closed ponta a ponta — payload nao-mapa nao pode estourar  [excecao ValueError: dictionary update sequence element #0 has length 1; 2 is required]
ACHADO fail-closed ponta a ponta — sinais nao-mapa nao pode estourar  [excecao AttributeError: 'list' object has no attribute 'get']
ACHADO acao declarada em human-approval.yaml — BLOQUEIO (9 declaracoes)  [exige_aprovacao: 'promocao de release para producao' -> decidido=executar outcome=PASS lane=small guardrails=[]; nunca_automatico: 'expor segredo em log, receipt ou mensagem' -> decidido=executar outcome=PASS lane=small guardrails=[]]
OK     cobertura — cada guardrail do YAML tem teste de REPROVA e de APROVA  [5 guardrails declarados no YAML, 5 regras, todos com teste de REPROVA e de APROVA]
OK     cobertura — cada acao proibida do YAML tem teste de bloqueio e de prosa  [8 acoes de nunca_decidido_por_maquina com teste de bloqueio e com variacao em prosa]
OK     sanity — o roteador carrega a politica e os papeis do repo  [versao=jev-policy-v1.0 lanes=['small', 'medium', 'high', 'critical'] papeis=['dev-harness', 'sales-ai']]
OK     sanity — o roteador nao foi alterado por esta suite (arquivos do repo intactos)  [hermes/jev/routing/router.py hermes/jev/policy_v1.yaml docs/architecture/jev-decision-policy-v1.md]

itens: 60 (57 de criterio, 3 adversariais) | falhas: 0 | achados: 7
RESULTADO: PASS (60 itens, 0 falhas)
```

## Anexo B — autoteste por mutação (histórico pré-correção: 13/13 detectadas) — estado anterior ao T07 (seção 7)

Cada mutação é aplicada a uma **cópia temporária** do roteador; a suíte roda contra a cópia e tem de reprovar. Mutação
não detectada = guardrail decorativo. Pós-correção o autoteste continua `13/13` (saída completa no Anexo A); duas
mutações passaram a reprovar mais itens porque os itens de regressão do D03/D05 entraram na conta:

- `fail-closed removido na entrada de tipo invalido`: 2 itens reprovados (era 1) — `cobertura D05 —` entrou na conta;
- `guardrails deterministas deixam de rodar`: 5 itens (eram 4); `nenhum guardrail bloqueia`: 8 (eram 7).

O autoteste **não** cobre a reversão do D03/D04 por mutação própria (não há mutação que desligue a leitura da fonte nem
a canonicalização). Essa prova é feita por reversão pontual e está na seção 0.4 — recomendo transformá-la em duas
mutações do autoteste quando o card do roteador for reaberto.

```
=== AUTOTESTE: mutacoes que a suite precisa reprovar ===
OK    detectada: guardrail segredo removido (payload com segredo passa)  (3 item(ns) reprovado(s): guardrail segredo — REPROVA: 6 formas de segredo bloqueiam, fallback — APROVA: no degradado os guardrails continuam rodando primeiro...)
OK    detectada: guardrail do_not_contact removido (empresa marcada e contatada)  (1 item(ns) reprovado(s): guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia)
OK    detectada: guardrail DDL removido (DDL nasce em producao)  (1 item(ns) reprovado(s): guardrail DDL — REPROVA: DDL em producao ou sem ambiente bloqueia)
OK    detectada: guardrail de papel/credencial removido (Sales AI com deploy)  (1 item(ns) reprovado(s): guardrail Sales AI x credencial de deploy — REPROVA: bloqueia)
OK    detectada: fail-closed removido no sinal desconhecido  (1 item(ns) reprovado(s): guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam)
OK    detectada: fail-closed removido na entrada de tipo invalido  (1 item(ns) reprovado(s): guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam)
OK    detectada: guardrails deterministas deixam de rodar  (4 item(ns) reprovado(s): guardrail segredo — REPROVA: 6 formas de segredo bloqueiam, guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida nao liberam...)
OK    detectada: nenhum guardrail bloqueia (a barreira e decorativa)  (7 item(ns) reprovado(s): guardrail segredo — REPROVA: 6 formas de segredo bloqueiam, guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia...)
OK    detectada: fallback executa em modo degradado  (6 item(ns) reprovado(s): fallback — APROVA: politica indisponivel entra em degradado conservador sem execucao, fallback ponta a ponta (CLI) — REPROVA: politica ausente -> lane conservadora + degradado, sem execucao...)
OK    detectada: fallback usa lane barata em vez da conservadora  (7 item(ns) reprovado(s): fallback — APROVA: politica indisponivel entra em degradado conservador sem execucao, fallback ponta a ponta (CLI) — REPROVA: politica ausente -> lane conservadora + degradado, sem execucao...)
OK    detectada: roteador deixa de exigir o guardrail declarado na politica  (1 item(ns) reprovado(s): guardrail fail-closed na leitura — REPROVA: politica sem guardrail declarado e recusada)
OK    detectada: versao de politica desconhecida aceita  (2 item(ns) reprovado(s): fallback — REPROVA: politica corrompida/versao desconhecida/secao faltando sao recusadas na leitura, fallback ponta a ponta (CLI) — REPROVA: versao de politica desconhecida -> lane conservadora + degradado, sem execucao)
OK    detectada: precedencia humana ignorada (Human Approval contornado)  (5 item(ns) reprovado(s): acao proibida primeiro_contato_outbound — BLOQUEIO por nome exato, acao proibida envio_de_proposta_comercial — BLOQUEIO por nome exato...)

autoteste: 13/13 mutacoes detectadas

itens: 60 (57 de criterio, 3 adversariais) | falhas: 0 | achados: 7
RESULTADO: PASS (60 itens, 0 falhas) + autoteste OK
```

## Anexo C — como reproduzir

```bash
cd /opt/data/repos/transformativa-revenue-engine
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py             # 74 itens, 0 falhas, 0 achados, exit 0
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --autoteste # + 20/20 mutacoes, exit 0
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --estrito   # achados contam como falha (exit 1; hoje exit 0)
/opt/hermes/.venv/bin/python scripts/verificar_jev_router.py --autoteste   # 59 itens, 0 falhas, 21/21 mutacoes, exit 0
/opt/hermes/.venv/bin/python scripts/verificar_jev_policy.py --autoteste   # politica x documento: 42 itens, 12/12
bash scripts/verificar_papeis.sh                                           # PASS
bash scripts/secret_scan.sh                                                # PASS
```

Evidência do card TRE-W0-E04-T07 (seção 7), com o CLI real do roteador — cada caso e o `exit` code:

```bash
cd /opt/data/repos/transformativa-revenue-engine
# as 5 frases que ja escaparam (4 do D04 + 1 do D06): BLOCK (exit 3) x4, ESCALATE (exit 2) x1
/opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"t_5","acao":"enviar mensagem ao primeiro cliente interessado","lane_proposta":"small","confianca":0.95}'
# codigo canonico como via principal (texto da acao inocente): exit 3
/opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"t_c2","acao":"ajuste de texto no runbook","acao_codigo":"primeiro_contato_outbound","lane_proposta":"small","confianca":0.95}'
# codigo comum sem dominio sensivel: exit 0 (a falha fechada nao e bloqueio geral)
/opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"t_c1","acao":"ajuste de texto no runbook","acao_codigo":"ajuste_de_texto","lane_proposta":"small","confianca":0.95}'
# chamador sem sinal nenhum + texto sensivel: exit 2 (inferencia)
/opt/hermes/.venv/bin/python hermes/jev/routing/router.py --json '{"card_id":"t_i2","acao":"trocar a senha de servico do banco","lane_proposta":"small","confianca":0.95}'
# reversao do mecanismo em copia temporaria: os itens do T07 tem de REPROVAR
/opt/hermes/.venv/bin/python /opt/data/cache/scratch/t07/prova_reversao.py
```

Reversão dos defeitos (prova da seção 0.4), em cópia temporária do roteador — cada substituição desliga um defeito
corrigido e a suíte tem de reprovar:

| Defeito | Substituição que reverte |
|---|---|
| D03 | `    entradas += [str(x) for x in (politica or {}).get("_acoes_de_human_approval") or []]` → `    entradas += []` |
| D04 | `    presentes = _conceitos_presentes(acao)` → `    presentes = set()` |
| D05 | `    tarefa = _tarefa_segura(tarefa)` → `    tarefa = dict(tarefa or {})` |

Evidência dos cards TRE-W0-E04-T02-D07 e -D08 (seção 8), com o CLI real do roteador, e a reversão do contrato novo:

```bash
cd /opt/data/repos/transformativa-revenue-engine
# as 4 frases do D07 + as 5 conhecidas + o controle + o D08 + o guardrail de DDL (saida bruta na secao 8)
bash /opt/data/cache/scratch/d07d08/evidencia_d07_d08.sh
# reversao do contrato novo em copia temporaria: os itens D07/D08 tem de REPROVAR
/opt/hermes/.venv/bin/python /opt/data/cache/scratch/d07d08/prova_reversao.py
# impacto de afrouxar o criterio, sobre os 32 casos do corpus anotado (base da homologacao da secao 8.9)
/opt/hermes/.venv/bin/python scripts/analisar_impacto_de_afrouxar.py
```

Nada foi commitado nem enviado **pela correção dos D07/D08**. Registro factual de estado: em 29/09/2026 22:14:48 UTC
outra frente de trabalho no mesmo worktree (card `TRE-W0-E04-T03`, "homologa lanes dos cards 3 e 4") rodou `git add -A`
e commitou a árvore inteira em `842d62e` — arrastando junto `hermes/jev/routing/router.py` e
`scripts/verificar_jev_router.py` (D07/D08 completos) e `scripts/validar_jev_guardrails.py` — e fez `push` para
`origin/develop`. Não foi ação desta correção; ficaram pendentes (não commitados) apenas o acréscimo de docstring em
`scripts/validar_jev_guardrails.py` e este documento. Hashes de conteúdo no estado entregue: `router.py` `1503c7fe`,
`verificar_jev_router.py` `2c43a3f8`, `validar_jev_guardrails.py` `7709ef36`, este documento `3fb19208` (antes dos
últimos parágrafos). Os arquivos alterados por esta correção são `hermes/jev/routing/router.py`,
`scripts/validar_jev_guardrails.py`, `scripts/verificar_jev_router.py` e este documento; `hermes/jev/policy_v1.yaml`
(política congelada) e `hermes/policies/*.yaml` **não foram alterados**. As suítes continuam rodando contra cópias
temporárias e não mutam o repo.

## 9. Registro anotado como não-perturbe/opt-out não tem execução automática (card `t_60fac84b`, 02/10/2026)

**O que o dono decidiu.** O defeito [encaixe] residual do conserto `TRE-W3-E04-T03-D02` (card `t_fa344342`) — as
3 linhas do corpus anotado do dono que continuavam acionando o guardrail `do_not_contact` **por casamento de
tokens** (`api`+`odoo`, `nome`+`transformativa`, `producao`+`rollback`) — não se resolve afinando vocabulário:
resolve-se implementando o que a política **já decidia**: **ação sobre registro anotado como não-perturbe/opt-out
é PROIBIDA para execução automática**. A máquina nunca executa; o recibo sai `BLOCK` com aprovação humana
exigida. O texto bruto original daquele card está no anexo
`/opt/data/kanban/boards/transformativa-revenue-engine/attachments/t_60fac84b/caso-bruto-original.txt`
(o corpo do card foi reescrito pelo operador para esta decisão).

### 9.1 O que mudou no roteador (e o que NÃO mudou)

- guardrail novo `registro_marcado` (`IDENTIFICADOR_DO_GUARDRAIL_DE_REGISTRO_MARCADO`) que aciona pela
  **anotação** (`sinais.empresa_do_not_contact`), nunca pelo casamento de prosa — ele vive na camada de
  **guardrails de CÓDIGO** (`_guardrails_de_codigo`), junto de `segredo_sem_payload` e
  `fail_closed_sinal_desconhecido`, porque o sinal é do chamador e não depende do YAML (ver §9.6, D2);
- quando ele está entre os guardrails acionados, a decisão é marcada como **proibida para execução
  automática**: `decisao.exige_aprovacao_humana = true` e o rastro no campo `override` do recibo
  (`registro_marcado`: `sinal`, `motivo`, `guardrails`) — **os 13 campos do contrato não crescem**;
- o guardrail `do_not_contact` (vocabulário) continua existindo, com o mesmo gatilho: os dois convivem, e uma
  ação de abordagem sobre registro anotado aciona os dois;
- **nenhuma política foi alterada** (`hermes/jev/policy_v1_3.yaml` e `hermes/policies/*.yaml` intactos): a regra
  já estava declarada em `guardrails` ("empresa com do_not_contact ou opt_out não é contatada") e na fonte da
  camada Human Approval (`hermes/policies/human-approval.yaml`, `nunca_automatico`: "contatar empresa com
  do_not_contact / opt_out marcado");
- **o que NÃO mudou:** registro LIMPO segue o fluxo normal (nenhum bloqueio geral) e o conserto do D02 (a entrada
  de PERMISSÃO do dev-harness fora da lista do guardrail de vocabulário) continua valendo.

### 9.2 Suíte própria (nova)

`scripts/verificar_registro_marcado_sem_execucao_automatica.py` — **54 itens, 8 mutações**, autoteste por mutação
(49 itens + 5 mutações no commit `461a257`; os 4 itens e as 2 mutações da cobertura dos achados D1/D2 entraram no
conserto do card `t_145eeaef`, ver §9.6; o item e a mutação do caminho anotado **sem piso** entraram no conserto do
card `t_c21fc474`, ver §9.7).
Ela prova os dois lados: marcado → `BLOCK` com recibo de 13 campos, motivo **dentro** do recibo e aprovação
humana exigida; limpo → fluxo normal (ação interna executa). E o encaixe ponta a ponta: card na tabela `tasks` +
declaração em `acoes-declaradas.yaml`, pelo gate real — o card limpo é liberado (PASS, exit 0) **com a aprovação
de onda em vigor**, e o card marcado **não** é liberado (BLOCK, exit 3) nem com essa mesma aprovação. Desde o
§9.6 ela também trava: (a) o rastro no **topo** do campo `override` quando o piso por ambiente age no mesmo card
(anotação + DDL declarada) e (b) a anotação decidindo em **modo degradado** (política ausente), onde a aprovação
de onda não libera o card anotado. Desde o §9.7 ela trava também (c) o caminho anotado **sem** piso: `humano` é só
o override do chamador e nenhuma chave dele sai espelhada no topo.

### 9.3 Saída bruta (evidência)

```bash
cd <worktree>
/opt/hermes/.venv/bin/python scripts/verificar_registro_marcado_sem_execucao_automatica.py --autoteste
# RESULTADO: PASS (54 itens, 0 falha(s))
# AUTOTESTE: 8/8 mutacoes reprovadas
# RESULTADO FINAL: PASS (autoteste OK)   [exit=0]
```

### 9.4 Itens de suíte HOMOLOGADA que precisaram mudar (e por quê)

| Suíte | Item | Antes | Depois |
|---|---|---|---|
| `scripts/verificar_outbound_sem_prosa_de_papel.py` (D02) | `lado 1c` (6 itens) | "o sinal não muda a decisão de um card que não aborda" | "o guardrail de VOCABULÁRIO não aciona; quem aciona é o de registro marcado" — o falso positivo do D02 segue provado morto |
| `scripts/verificar_outbound_sem_prosa_de_papel.py` (D02) | `acao interna com o sinal ligado continua executando` | executava | "não executa automaticamente (registro marcado), com aprovação humana exigida" + item novo do lado limpo |
| `scripts/validar_jev_guardrails.py` | `guardrail do_not_contact — APROVA` | "empresa marcada em ação interna executa" | "não aciona o guardrail de VOCABULÁRIO (a execução automática é proibida pelo `registro_marcado`)" |
| `scripts/validar_jev_guardrails.py` | — | — | +2 itens (`REPROVA`/`APROVA` do `registro_marcado`) e +1 mutação (`registro_marcado removido`) |
| `scripts/verificar_jev_policy_v1_1.py` (§9.6) | mutação `piso fora do recibo` | âncora `plano["override"] = {"piso_por_ambiente": registro}` | âncora na chamada nova (`_compor_override_do_recibo`) — a mutação continua com o mesmo dente: tira o piso do recibo e a suíte reprova |

### 9.5 Limitações honestas

- a anotação é um **sinal declarado pelo chamador** (`sinais.empresa_do_not_contact`): quem consulta a base e
  preenche o sinal (encaixe/board) não faz parte deste card — o roteador julga o que recebe;
- pelo mesmo motivo, o "não executa automaticamente" vale para a decisão do roteador e para o gate: **não há
  prova de que um executor externo** (cron, ETL, pipeline de mídia) consulte o roteador antes de agir;
- a **aprovação humana registrada** continua podendo liberar o card (é o canal previsto na política) — o que a
  marca nova garante é que a liberação **não é automática**: exige decisão humana registrada;
- a precisão do **casamento de vocabulário** (`_e_acao_outbound`, resíduo do D02) não foi melhorada: para
  registro anotado ela deixou de decidir sozinha (o `registro_marcado` bloqueia antes e independentemente), que é
  o que o dono pediu. Precisão do vocabulário por si é card próprio.
- as duas limitações de **cobertura/rastro** medidas na verificação independente do card `t_2c8c5a22` (D2: em
  modo degradado a anotação não decidia, e o piso por ambiente engolia o caminho `override.registro_marcado`)
  estão **fechadas** no conserto do card `t_145eeaef` — ver §9.6. O que segue fora da garantia: política
  carregável mas **incompleta** para outro guardrail de política segue sendo coberta por eles — a anotação não
  depende do YAML, os demais guardrails de política continuam dependendo.

### 9.6 Achados de cobertura D1/D2 da verificação independente (card `t_145eeaef`, 02/10/2026)

A verificação independente do conserto (card `t_2c8c5a22`, perfil `tester`) aprovou o caminho normal e abriu dois
achados **não bloqueantes** de cobertura/rastro — nenhum dos dois invalidava o guardrail, mas a garantia declarada
não valia nesses caminhos. Os dois foram fechados na raiz:

- **D1 [rastro] — o piso por ambiente engolia o caminho `override.registro_marcado`.** Com card anotado **e**
  DDL/migration declarada no próprio texto, o piso por ambiente também age; ele embrulhava *tudo* o que já estava
  em `override` sob a chave `humano`, e o rastro do guardrail saía em `override.humano.registro_marcado` — o
  caminho que o item 1 do card, este §9 e a suíte leem. **Correção:** a composição do campo `override` passou a ter
  uma regra única (`_compor_override_do_recibo`): `humano` nomeia **só** o override que a própria tarefa declarou
  (`plano["override_do_chamador"]`) e é recomposto desse slot; os rastros de regra (`piso_por_ambiente`,
  `registro_marcado`) são **irmãos** de `humano`, no topo — nenhuma chave já gravada é descartada. Comportamento
  intacto (BLOCK, exit 3, `exige_aprovacao_humana`, 13 campos) e o piso continua registrado em
  `override.piso_por_ambiente`.
- **D2 [fail-open] — em modo degradado a anotação não decidia.** O guardrail `registro_marcado` vivia em
  `_guardrails_de_politica`, que só roda com política carregada (`if politica is not None`): sem política
  (ausente/corrompida/versão desconhecida) a decisão virava `ESCALATE` e o gate consulta a aprovação humana
  exatamente nesse desfecho — o card ANOTADO era liberado (`allow=True`, exit 0, `guardrails=[]`), **idêntico a um
  card limpo**. **Correção:** o guardrail passou para `_guardrails_de_codigo` (sempre roda, inclusive no
  degradado). Em modo degradado o card anotado agora sai `BLOCK`/exit 3 com `exige_aprovacao_humana: true` e o
  motivo no recibo; a aprovação de onda não o libera. O card **limpo** em modo degradado segue no fluxo
  degradado (escada conservadora + aprovação registrada) — não virou bloqueio geral.

### 9.7 Cobertura do caminho ANOTADO **sem** piso (card `t_c21fc474`, 02/10/2026)

A verificação independente do conserto D1/D2 (card `t_831d01f0`, perfil `tester`) aprovou o conserto e mediu um
**buraco de cobertura**: o invariante declarado — "`humano` nomeia **só** o override do CHAMADOR; os rastros de
regra (`registro_marcado`, `piso_por_ambiente`) são **irmãos** dele, no topo do campo `override`" — só estava
travado no caminho em que o **piso por ambiente também age** (o item monta o card com DDL/migration declarada).
Naquele caminho é a própria `_compor_override_do_recibo` que reconstrói `humano` a partir de
`plano["override_do_chamador"]` — e ela **mascarava** uma regressão em `_registrar_proibicao_automatica_no_plano`.

O mutante medido (cópia descartável do bundle do branch do conserto, head `c5867fc`), em
`hermes/jev/routing/router.py`, `_registrar_proibicao_automatica_no_plano`:

```
-        plano["override"] = {"humano": humano, "registro_marcado": registro}
+        plano["override"] = {**humano, "registro_marcado": registro}
```

Com ele, um card anotado com override do chamador declarado e **sem** DDL/migration grava
`override = {"por": "anderson", "motivo": "revisao manual", "registro_marcado": {...}}` — as chaves do chamador ficam
espelhadas no topo e `humano` **desaparece** (o override do chamador deixa de ser nomeado). A suíte anterior
(53 itens + 7 mutações) **passava** por ele: `EXIT 0` (o buraco, medido).

**Correção (cobertura — sem alterar política, sem campo novo no recibo):**
- item novo **D1 sem piso** — anotação + override do chamador **sem** DDL/migration (o piso não age): `override.humano`
  é só o override do chamador, `override.registro_marcado` fica no **topo** e **nenhuma** chave do chamador sai
  espelhada fora de `humano`;
- o item **D1 com piso** passou a exigir o mesmo invariante de "nenhuma chave espelhada" (antes aceitava o
  espelhamento — visível quando o mutante age nos dois caminhos);
- mutação nova na lista da própria suíte (o mutante acima) e, para o dente ser **real**, o autoteste passou a montar
  a árvore temporária na posição relativa do repo (`hermes/jev/routing/router.py` + política por symlink, mesma
  convenção da suíte v1.1, `_arvore_do_roteador_mutado`): antes, com o copy plano no diretório temporário, o roteador
  mutado não achava a política e a suíte reprovava por **exceção** — dente falso, medido. Depois do conserto as 8
  mutações reprovam por **item** ([FALHA]), não por exceção;
- medido na raiz: suíte **54 itens PASS + autoteste 8/8**, `exit 0`; o mutante acima, na mesma árvore, reprova
  **2 itens** (`D1 com piso` e `D1 sem piso`) e a suíte sai `exit 1`; a suíte anterior (53 itens) sobre o mesmo
  mutante saía `exit 0`;
- **nenhuma política alterada** (`hermes/jev/policy_v1*.yaml` e `hermes/policies/*` intactos), contrato de 13 campos
  intacto, nada de credencial.
