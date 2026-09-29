# Validação dos guardrails e do fallback do JEV

**Card:** TRE-W0-E04-T04 (depende de TRE-W0-E04-T03) · **Data:** 29/09/2026
**Alvo validado:** `hermes/jev/routing/router.py` (jev-router-v1.0) + `hermes/jev/policy_v1.yaml` (jev-policy-v1.0)
**Suíte:** `scripts/validar_jev_guardrails.py` — validação adversarial e **independente** da suíte do card irmão
(`scripts/verificar_jev_router.py`, T02). Nenhum arquivo auditado foi alterado: as mutações acontecem em cópia temporária.
**Comando de tudo o que está neste relatório:**

```
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --autoteste
```

**Resultado:** `RESULTADO: PASS (60 itens, 0 falhas) + autoteste OK` (exit 0), com **7 achados** (defeitos encontrados e
**não corrigidos**, por regra do card) listados na seção 5.

## Como ler os vereditos

| Estado | Significado |
|---|---|
| `OK` | item de critério atendido, com saída bruta no relatório da suíte |
| `FALHOU` | item de critério NÃO atendido (suíte vermelha; nenhum item ficou nesta classe) |
| `ACHADO` | o roteador executa/estoura onde a política manda parar. **Defeito encontrado**, não corrigido |
| `VALIDADO` | o guardrail/ação tem prova dos dois lados (ou do caminho exigido) **e** mutação que o remove é detectada |
| `NAO VALIDADO` | não existe prova, ou a prova mostrou que o caminho proibido passa |

`--estrito` soma os ACHADOs como falha (exit 1) para uso como gate de defeito. O default (usado acima) mantém o
critério homologado no comando e expõe os defeitos separadamente, sem esconder nenhum: toda linha ACHADO aparece
no stdout e aqui.

## 1. Contagem do que o YAML declara e do que ficou validado

| Bloco do YAML | Declarados | VALIDADOS | NAO VALIDADOS |
|---|---|---|---|
| `guardrails` (regras) | 5 | **5** (cada um com teste de REPROVA + APROVA + mutação que o remove detectada) | 0 como regra; **2 casos de borda do fail-closed** não validados ponta a ponta (achado D3) |
| `nunca_decidido_por_maquina` (ações) | 8 | **8** por nome exato (bloqueio) | **2** com cobertura em prosa incompleta: `aprovacao_de_producao` e `exclusao_de_dado_de_cliente` (achados D1/D2) |
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
| APROVA | `guardrail do_not_contact — APROVA: sem marcacao ou sem outbound executa` (OK) | outbound (`enviar e-mail pelo Titan`) sem marcação executa; empresa marcada em ação interna executa |
| Mutação | `guardrail do_not_contact removido (empresa marcada e contatada)` | **detectada** — 1 item reprova o mutante |

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

**NAO VALIDADO ponta a ponta (achado D3):** para payload não-mapa e `sinais` não-mapa, `decidir()` **estoura exceção**
em vez de devolver `BLOCK` — o guardrail aciona, mas a avaliação da política roda antes do teste de bloqueio e quebra
primeiro. Itens da suíte: `fail-closed ponta a ponta — payload nao-mapa nao pode estourar` e
`fail-closed ponta a ponta — sinais nao-mapa nao pode estourar`, ambos `ACHADO`.

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

### 3.1 Cobertura em prosa (22 variações)

| Ação | Variações testadas | Bloqueiam/escalam | Executam (ACHADO) | Veredito da cobertura em prosa |
|---|---|---|---|---|
| `aprovacao_de_producao` | 5 | 2 (`aprovar promocao de release para producao`, `deploy em producao`) | **3** — `promocao de release para producao`, `promover release para producao`, `publicar release em producao` | **NAO VALIDADO** |
| `primeiro_contato_outbound` | 3 | 3 | 0 | VALIDADO |
| `envio_de_proposta_comercial` | 2 | 2 | 0 | VALIDADO |
| `mudanca_estrutural_de_arquitetura` | 2 | 2 | 0 | VALIDADO |
| `rollback_em_producao` | 2 | 2 | 0 | VALIDADO |
| `exclusao_de_dado_de_cliente` | 3 | 2 | **1** — `exclusao de registro de auditoria` | **NAO VALIDADO** |
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

## 5. Achados (defeitos encontrados, NÃO corrigidos)

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

## Anexo A — saída bruta da suíte (60 itens)

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

## Anexo B — autoteste por mutação (13/13 detectadas)

Cada mutação é aplicada a uma **cópia temporária** do roteador; a suíte roda contra a cópia e tem de reprovar. Mutação
não detectada = guardrail decorativo.

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
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py             # 60 itens, 0 falhas, exit 0
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --autoteste # + 13/13 mutacoes, exit 0
/opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --estrito   # achados contam como falha (exit 1)
```

Nada foi commitado nem enviado. `hermes/jev/policy_v1.yaml`, `hermes/jev/routing/router.py`,
`scripts/verificar_jev_router.py` e `docs/architecture/jev-decision-policy-v1.md` **não foram alterados** (a suíte
roda contra cópias temporárias; `git status` limpo além dos arquivos novos deste card).
