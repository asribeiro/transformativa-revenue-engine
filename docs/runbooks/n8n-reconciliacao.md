# Runbook — job diário de RECONCILIAÇÃO (PostgreSQL × Odoo pela porta única)

Card: **TRE-W3-E04-T01** (`t_2ee17829`) — board `transformativa-revenue-engine`, épico W3 (E04).
Escopo: **o job que MEDE a coerência entre os dois lados** — as organizações que o PostgreSQL
espera ver espelhadas no CRM, o ID cruzado entre eles e a fila de eventos contra a trilha de
sincronização. O que este card NÃO faz: corrigir qualquer dado (doc 06 §8), publicar o workflow
(o `active` nasce `false`), criar tabela de relatório (é DDL — ato de contrato com aprovação
registrada, ADR-005) e alertar (o destino do relatório é decisão de operação).

## 1. Por que um job de reconciliação (e por que somente leitura)

A sincronização saudável não garante que os dois lados contem a mesma história: o espelho pode
**sumir** do CRM, pode ser **arquivado**, pode ter o **ID cruzado** apontando para outra
organização, pode carregar um **identificador forte divergente** (CNPJ/domínio). Nada disso
aparece na trilha — a trilha diz o que foi entregue, não o que sobrou. A fila tem o problema
simétrico: evento que ficou velho `PENDING`, evento cuja trilha diz `COMPLETED` e que continua
na fila, evento no teto de tentativas que não saiu, recusa definitiva que não drenou.

A reconciliação é o único lugar do desenho que olha **os dois lados ao mesmo tempo** e diz, com
nome próprio, qual é o desvio. Ela é **somente leitura por contrato**: mede e nomeia; quem
corrige é operador, com o relatório na mão. Corrigir aqui seria apagar a evidência do defeito.

## 2. Artefatos versionados (a fonte é o repositório, não a UI do n8n)

| Artefato | Papel |
| --- | --- |
| `n8n/contracts/reconciliation-job.v1.json` | **O contrato**: fontes, lote (limite/ordem/janela), leituras do destino (campos, filtro, operador, limite), `vinculo_da_trilha`, `teto_de_tentativas`, `janela_de_pendencia_s`, vocabulário de fila e trilha, `comparacoes` (cada uma com id, tipo e motivo), `observacoes`, `veredito`, `regras_de_fail_closed`, `proibicoes`, `relatorio`, credenciais por id/nome, grafo e saída |
| `n8n/codigo/nucleo-reconciliacao.js` | Núcleo em JS puro: monta os pedidos da porta única e decide (veredito, divergências nomeadas, cobertura, relatório). Roda em node e dentro do Code node do n8n |
| `n8n/sql/reconciliacao-origem.sql` | Lado PostgreSQL: o lote de organizações + a ponta do vínculo + os identificadores fortes + a linha de **cobertura** |
| `n8n/sql/reconciliacao-pendentes.sql` | Lado PostgreSQL: a **fila** × a **trilha** (idade, tentativas, status) + a cobertura da fila |
| `n8n/workflows/TRE-reconciliation.json` | O workflow **gerado** dos artefatos acima |
| `scripts/n8n/montar_workflow_reconciliacao.py` | Monta o workflow (artefato derivado) |
| `scripts/n8n/conferir_reconciliacao.py` | Lente estrutural: contrato × SQL × núcleo × workflow × política da API × contratos vizinhos |
| `scripts/n8n/testar_nucleo_reconciliacao.js` | Suite do núcleo (node puro) + o código **embutido** no workflow |
| `scripts/n8n/mutar_reconciliacao.py` | Mutações nomeadas da prova de dente |
| `scripts/n8n/massa-reconciliacao.sql` | Massa do PostgreSQL por estado (só no banco descartável do aceite) |
| `scripts/odoo/massa_reconciliacao.py` | Massa de parceiros no Odoo por estado (só no trio descartável) |
| `scripts/n8n/ler_resultado_reconciliacao.py` | Lê a saída da rodada (veredito, divergências, linha do relatório) |
| `scripts/n8n/verificar-reconciliacao.sh` | Aceite ponta a ponta no trio descartável + prova de dente |

O workflow **não se edita à mão**: o texto do Code node é o arquivo do núcleo + um adaptador
marcado, o contrato vai **embutido** e o SQL dos nós é o arquivo. Editar o JSON direto cria
divergência e a lente estrutural reprova. O fluxo é: editar contrato/núcleo/SQL →
`python3 scripts/n8n/montar_workflow_reconciliacao.py` → commitar os dois.

## 3. O que o job compara

### 3.1 Entidades (PostgreSQL → CRM)

O lote são as organizações da base (ordem `updated_at DESC, id ASC`, limite declarado). Uma
organização é **esperada** no destino quando o PostgreSQL gravou a ponta do vínculo
(`odoo_partner_id`) ou quando há trilha de espelho entregue. Para cada esperada, o job lê o
destino **duas vezes** pela porta única — e as duas perguntas são diferentes:

* `crm_registros_ler` com `filtro: [["id","in",[<odoo_partner_id>...]]]` → *o parceiro que o
  PostgreSQL aponta existe?*;
* `crm_registros_ler` com `filtro: [["tf_company_id","in",[<organizações do lote>...]]]` → *quantos
  parceiros se declaram o espelho desta organização?*

Com isso saem as divergências nomeadas:

| id | tipo | o que mede |
| --- | --- | --- |
| E1 | `entidade_esperada_ausente_no_destino` | esperada no destino e ausente nas duas leituras |
| E2 | `entidade_arquivada_no_destino` | espelho existe e está `active = false` |
| E4 | `identidade_forte_divergente` | CNPJ/domínio com valor nos **dois** lados e diferente |
| I1 | `id_cruzado_divergente` | o parceiro apontado se declara espelho de **outra** organização do lote (ou não tem ID canônico) |
| I2 | `id_cruzado_sem_volta_no_pg` | o parceiro se declara espelho e a ponta do PostgreSQL não volta para ele |
| I3 | `id_cruzado_duplicado_no_destino` | mais de um parceiro com o mesmo `tf_company_id` |
| I4 | `id_cruzado_aponta_para_fora_do_lote` | o espelho aponta organização que não está na janela desta rodada |

### 3.2 Fila × trilha

A fila é lida no recorte declarado (`status IN ('PENDING','RETRY')`, `created_at ASC`, limite) e
cruzada com a trilha pela **chave derivada** (`outbox:<evento>:<event_type>`), que é a derivação
do contrato do consumidor (a tabela da V1 não tem coluna de `event_type`).

| id | tipo | o que mede |
| --- | --- | --- |
| P1 | `evento_pendente_alem_da_janela` | na fila há mais tempo que a janela declarada |
| P2 | `evento_entregue_ainda_na_fila` | trilha com o status de sucesso e o evento continua na fila |
| P3 | `evento_no_teto_ainda_na_fila` | `attempts` no teto e deveria ter saído como `DEAD_LETTER` |
| P4 | `recusa_ainda_na_fila` | trilha com recusa definitiva e o evento continua na fila |

### 3.3 O veredito (e o fail-closed)

`OK < DIVERGENTE < INDETERMINADO` — nessa ordem de gravidade. O job fecha **INDETERMINADO**
(nunca OK) quando falta medição: contrato inválido ou sem os números que decidem, consulta sem
linha de cobertura (base vazia é informação; consulta quebrada não é), leitura do destino não
medida (HTTP fora da faixa, recusa da porta única, corpo sem `dados.registros`), status de
trilha fora do vocabulário, pendente sem idade, cobertura da fila sem os totais. É a regra que
impede o pior defeito de um job de reconciliação: **ler ausência de medição como ausência de
problema**.

A linha-resumo carrega sempre as **duas janelas** (`janela_<completa|parcial|nao_medida>` e
`fila_<...>`): veredito de base parcial não pode ser lido como veredito da base inteira.

### 3.4 Leitura não medida não vira divergência

Com **qualquer** leitura do destino não medida, as comparações de entidade (`E1/E2/E4/I1..I4`) são
**puladas** e o pulo entra nomeado nas indeterminações (`comparacoes_de_entidade_puladas`), ao lado
da `leitura_do_destino_nao_medida`. É a mesma regra do §3.3 vista do outro lado: se "ausente" saísse
de uma leitura que não aconteceu, o job inventaria divergência a partir de ausência de medição —
defeito **medido** no aceite (estado *G*, porta única parada: o job reportava `E1` para todo espelho
esperado enquanto o veredito já era `INDETERMINADO`). As comparações de **fila × trilha** continuam
rodando: elas não dependem da porta única do Odoo.

## 4. Operação

### 4.1 Variáveis e credenciais

* `TRE_API_BASE` — base da porta única (ex.: `http://odoo-dev:8069` na rede do dev). O workflow
  usa `$env.TRE_API_BASE`, com `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` no container do n8n.
* Credenciais por **id/nome** declarados no contrato: `tre-dev-postgres`
  (`TRE dev — sales_intelligence`) e `tre-dev-api-controlada` (`TRE dev — API controlada Odoo
  (Bearer)`). O versionado não carrega valor de credencial nenhum; quem cria é a operação.
* A única chamada externa é `POST /tf/api/v1/crm_registros_ler` (operação de **leitura** da
  política da API), com o pedido dentro do invólucro `{parametros: {...}}`.
* A leitura pede `incluir_arquivados: true` (declarado no contrato, por leitura). Sem isso o ORM do
  Odoo devolve só registros **ativos**: espelho **arquivado** viraria espelho **ausente** (`E2`
  viraria `E1`, e a operação mandaria criar de novo o que existe). O pedido só é aceito porque a
  operação declara `leitura_de_arquivados` na política (**≥ 1.4.0**) e o motor exige `active` entre
  os campos pedidos; pedir em operação que não declara é recusa nomeada (422
  `parametro_nao_declarado`) — que a rodada lê como leitura não medida (`INDETERMINADO`), nunca como
  espelho ausente.
* O nó HTTP da leitura é declarado no contrato com `onError: continueRegularOutput`
  (`envelope_da_requisicao.no_que_nao_mede`): porta única fora do ar vira **item** que o núcleo lê
  como não medida, em vez de abortar a execução do n8n (e a rodada morrer sem relatório).

### 4.2 Ligar a agenda

O gatilho é declarado no contrato (24h) e o workflow nasce **inativo**. Quem ativa ajusta a hora
ao plantão: `n8n update:workflow --id=TRERECONCILIA01 --active=true`. A execução sob demanda é o
nó `Executar agora` (`n8n execute --id=TRERECONCILIA01`).

### 4.3 Diagnóstico rápido

1. `leitura=ilegivel` na saída → a **execução do n8n** não chegou ao nó do relatório (erro de
   execução, não de negócio: a porta única fora do ar **não** cai aqui, ela vira `INDETERMINADO` com
   a regra nomeada — ver §3.4). Ver o log da execução, não o resultado: ele não existe.
2. `INDETERMINADO` com `leitura_do_destino_nao_medida` → a porta única não respondeu ou recusou:
   checar `TRE_API_BASE`, a credencial e o log do Odoo.
3. `INDETERMINADO` com `consulta_de_origem_sem_cobertura`/`consulta_de_pendentes_sem_cobertura` →
   o SQL foi substituído por outro que não devolve a linha de cobertura: a rodada não sabe o que
   mediu.
4. `DIVERGENTE` com `I2`/`I1` em massa → provável espelho criado sem `tf_company_id` ou com o ID
   canônico de outra organização; comparar `organizations.odoo_partner_id` com
   `res_partner.tf_company_id` antes de qualquer correção.
5. `DIVERGENTE` com `P1`/`P3` em massa → o consumidor não está drenando: ver o runbook
   `n8n-outbox-consumer.md` §4.3.

### 4.4 O que fazer com o relatório

O relatório é o **produto** da rodada (o job não grava em tabela). Para desviar de `E1`, a ação é
do operador: criar/atualizar o espelho pela porta única (operação de escrita própria) e reexecutar
o job para medir de novo. Para desviar de `I1`/`I4`, a correção é decidir **qual** das duas pontas
está errada — o job não decide isso por ninguém.

## 5. Evidência do aceite

`bash scripts/n8n/verificar-reconciliacao.sh` mede, num **trio descartável próprio** (postgres +
odoo + n8n criados e destruídos na hora):

* lente estrutural e suite do núcleo (incluindo o código **embutido** no workflow) e o montador
  `--conferir`: o workflow em disco é o montado agora;
* **prova de dente** (`--prova-de-dente`): baseline verde e cada mutação nomeada reprovando **o
  item que ela quebra** — a mutação declara o alvo (`nucleo` = a suite tem de reprovar;
  `lente:<item>` = aquela linha da lente tem de reprovar);
* 7 estados em **execução real** (PostgreSQL + Odoo + n8n descartáveis): espelho saudável → `OK`
  **sem divergência**; espelho ausente → `E1`; ID cruzado → `I1`; identidade forte → `E4`;
  espelho arquivado → `E2`; fila × trilha → `P1,P2,P3,P4`; porta única **fora do ar** →
  `INDETERMINADO` com a regra nomeada;
* **somente-leitura** medido em cada rodada: digest das três tabelas do PostgreSQL e dos parceiros
  do Odoo, antes e depois;
* `sha256` dos artefatos sob teste fixado nas guardas e **reconferido no fecho**; instância do dev
  medida antes e depois; nenhum segredo em claro no cofre do descartável nem no versionado.

## 6. Limites conhecidos (o que este card não resolve)

* **Sem persistência do relatório**: a rodada termina no nó `Relatorio` (no-op). Guardar histórico
  de vereditos exige tabela nova — DDL é ato de contrato com aprovação registrada (ADR-005).
* **Sem alerta**: o destino do relatório (log, e-mail, chat) é decisão de operação; o job só o
  produz.
* **Janela de entidades = lote limitado**: base maior que o limite da porta única (200) faz a
  rodada declarar `janela_parcial`; reconciliar a base inteira exige paginação, que é trabalho de
  outro card.
* **Janela da fila**: a leitura da fila é limitada pelo mesmo teto; com a fila maior, a rodada
  declara `fila_parcial` (os totais por status vêm da tabela, não do recorte).
* **Leitura por `write_date` não entra**: a reconciliação compara **existência, identidade e
  estado**, não o conteúdo campo a campo do último `write_date` — sincronização de conteúdo é do
  caminho de escrita.
* **Dependência da política ≥ 1.4.0**: com a política anterior (sem `leitura_de_arquivados`), o
  pedido `incluir_arquivados` é recusado (422) e a rodada fica `INDETERMINADO` — declarado e
  nomeado, nunca silencioso. O job não "cai para ativos": ler menos do que o contrato declara seria
  um veredito mentiroso.
* **`E4` só compara campos fortes declarados** (`tf_cnpj`, `tf_domain`, `tf_linkedin_url`) e só
  quando os dois lados têm valor: ausência de um lado não é divergência.

## 7. Rollback

1. Desativar a agenda (`--active=false`) — nada é escrito pelo job, então não há dado a desfazer.
2. Reimportar a versão anterior do workflow (`n8n import:workflow`) para voltar o comportamento.
3. Se o defeito for de **contrato** (limite, janela, vocabulário, comparação), o rollback é do
   arquivo do contrato + `montar_workflow_reconciliacao.py` + reimportação — o núcleo não carrega
   limiar próprio, então o comportamento muda com o contrato.
4. O aceite é o juiz do rollback: rodar `verificar-reconciliacao.sh` depois de reimportar.

## 8. Registro

Execuções e resultados ficam em `docs/operations/registro-de-execucoes.md`.
