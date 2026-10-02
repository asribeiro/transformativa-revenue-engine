# Runbook — consumidor de outbox (n8n) → API controlada do Odoo

Card: **TRE-W3-E02-T01** (`t_ba84b412`) — board `transformativa-revenue-engine`, épico W3 (E02).
Escopo: **o caminho de consumo** da fila `sales_intelligence.outbox_events` até a escrita de negócio
no CRM, passando **só** pela porta única (API controlada). O que este card NÃO faz: produtor de
eventos, deduplicação por chave (E02-T02), publicação automática do workflow (o `active` nasce
`false`) e agenda em produção — a agenda é decisão de operação, com o workflow já validado.

## 1. Por que um consumidor em n8n (e por que assim)

O produtor escreve o fato na fila e vai embora. Quem fala com o Odoo é este consumidor, e ele fala
por **um único caminho**: `POST $TRE_API_BASE/tf/api/v1/<operacao>`. Nada de XML-RPC, JSON-RPC,
`execute_kw` ou escrita direta em tabela do Odoo — a política da API (contrato versionado, doc 12)
é quem decide campo, valor fixo e identidade. O consumidor não interpreta o negócio: ele
**transporta com prova** (quem entregou, quando, com que resposta) e **recusa com nome**
(nunca ignora em silêncio).

Consequência prática: qualquer regra nova de negócio (campo novo, modelo novo, valor fixo novo)
muda na **política da API**, não no n8n. O consumidor só muda quando o *contrato do evento* muda.

## 2. Artefatos versionados (a fonte é o repositório, não a UI do n8n)

| Artefato | Papel |
| --- | --- |
| `n8n/contracts/outbox-consumer.v1.json` | **O contrato** (v1.1.0): envelope, eventos aceitos, mapeamento evento→campos, operação destino, teto de tentativas, classificação HTTP, status da trilha, **dedup por chave** (`dedup`), credenciais por id/nome |
| `n8n/codigo/nucleo-outbox-consumer.js` | Núcleo em JS puro (decisão: replay/enviar/recusar/esgotado + classificação da resposta). Roda em node e dentro do Code node |
| `n8n/sql/ler-pendentes.sql` | Leitura da fila (somente SELECT) |
| `n8n/sql/ler-trilha.sql` | Leitura da **trilha** pelas chaves do lote (somente SELECT) — insumo do dedup |
| `n8n/sql/registrar-resultado.sql` | Estado final do evento **e** linha da trilha, numa transação |
| `n8n/sql/registrar-replay.sql` | Estado final do **replay** (reaproveita o registro da trilha; não toca a trilha) |
| `n8n/workflows/TRE-outbox-consumer.json` | O workflow **gerado** dos artefatos acima |
| `scripts/n8n/montar_workflow.py` | Monta o workflow a partir dos artefatos (o workflow é artefato derivado) |
| `scripts/n8n/conferir_contrato_e_workflow.py` | Lente estrutural: contrato × SQL × nós × política da API |
| `scripts/n8n/testar_nucleo_consumidor.js` | Suite do núcleo (node puro) + **código embutido no workflow** |
| `scripts/n8n/verificar-outbox-consumer.sh` | Aceite ponta a ponta no trio descartável + prova de dente |
| `scripts/n8n/mutar_workflow.py` | Mutações nomeadas da prova de dente |
| `scripts/n8n/preparar_massa_ambigua.py` | Massa da identidade ambígua (dois parceiros, mesmo domínio) |

O workflow **não se edita à mão**. Editar o JSON direto cria divergência com o contrato — e a lente
estrutural reprova (`o workflow sob teste e' o montado a partir dos artefatos`). O fluxo é:
editar contrato/núcleo/SQL → `python3 scripts/n8n/montar_workflow.py` → commitar os dois.

## 3. O que o consumidor faz em cada evento

1. Lê a fila (`status IN ('PENDING','RETRY')`, `ORDER BY created_at, id LIMIT 20`).
2. Deriva a **chave de idempotência** de cada evento da leva (`outbox:<id>:<event_type>`) e lê a
   trilha **numa única consulta** pelas chaves do lote (`n8n/sql/ler-trilha.sql`).
3. Decide, por evento, com o contrato na mão (a ordem é a declarada em `dedup.ordem_da_decisao`):
   * `status` fora da fila → **IGNORAR** (defensivo, vira no-op);
   * chave **já entregue** (linha da trilha com status `COMPLETED`) → **REPLAY**: não chama a API,
     não incrementa `attempts`, não toca a trilha — o registro existente é reaproveitado
     (`n8n/sql/registrar-replay.sql`, que exige a trilha de sucesso para finalizar o evento);
   * envelope (regra 5 do contrato §6): sem `event_version` (ou versão fora de `1.0`) → **RECUSAR**;
   * `event_type` fora do contrato → **RECUSAR**;
   * identidade (`aggregate_id`) vazia → **RECUSAR**;
   * campo exigido do mapeamento ausente (ex.: `name`) → **RECUSAR**;
   * `attempts >= teto` (3) → **ESGOTADO**;
   * caso contrário → **ENVIAR** (com `idempotency_key` e `correlation_id` derivados do evento).
4. Recusa: não chama a API, marca `DEAD_LETTER` e grava o motivo em `last_error` **e** na trilha
   (`sync_events`, status `REFUSED`). Recusa por envelope/contrato **não incrementa** `attempts`
   (não houve tentativa de entrega).
5. Envio: `POST /tf/api/v1/empresa_upsert` com `parametros.valores` **só** com o que o mapeamento
   declara, e classifica a resposta: 2xx → `PROCESSED`/`COMPLETED`; 4xx e códigos terminais
   (ex.: `valor_ambiguo`, `ambiente_nao_permitido`) → `DEAD_LETTER`/`REFUSED`; 5xx e falha de
   transporte → `RETRY`/`FAILED` com o motivo.
6. O estado do evento e a linha da trilha gravam na **mesma instrução SQL** (uma transação):
   não existe evento marcado como entregue sem trilha, nem trilha sem estado.

**Replay (TRE-W3-E02-T02).** A garantia é do **evento**: a chave é determinística, a trilha é o
registro dela (coluna `UNIQUE`) e o replay é reconhecido por **consulta**, nunca por heurística. Só
trilha com status de sucesso autoriza replay — trilha `FAILED` ou `REFUSED` **não** autoriza (a falha
transitória pode não ter escrito nada no destino, e a recusa é do evento, não da chave): nesses casos
o evento volta a ser entregue normalmente. O caminho do replay **não** tem nó de HTTP alcançável (a
lente estrutural mede isso: só o ramo de entrega alimenta a porta única).

## 4. Operação

### 4.0 Por que a entrega é SERIALIZADA (uma por vez, em ordem)

O nó HTTP entrega **um item por vez** (`batching.batchSize = 1`, `batchInterval` declarado no
contrato). Isso não é detalhe de desempenho: a fila pode trazer **dois eventos da mesma identidade**
na mesma leva (uma qualificação e a atualização da mesma empresa) e, em paralelo, os dois pedidos
chegam à API antes de o primeiro ter commitado — a busca de identidade não vê o registro do vizinho e
o resultado é **dois parceiros para a mesma empresa**. Isso não é hipótese: foi medido neste card
(E1 e E2 com **2ms** de intervalo na auditoria do servidor, dois parceiros criados, `acao_efetiva:
"criar"` nas duas respostas). Com a entrega serializada, o intervalo medido ficou **duas ordens de
grandeza acima** e o segundo evento passou a ATUALIZAR o mesmo parceiro.

Ordem do lote = ordem da fila (`ORDER BY created_at, id`). O teto de itens por ciclo é declarado no
contrato (`origem.limite_por_ciclo`) e o intervalo entre chamadas está em
`destino.entrega_serializada` — os dois são lidos pelo montador, não escritos à mão no workflow.

### 4.1 Variáveis e credenciais

* `TRE_API_BASE` — base da porta única (ex.: `http://<host-do-odoo>:8069`). O workflow lê do
  ambiente: **nenhum host fica escrito no artefato**. Se a instância bloquear acesso a env var em
  node (`N8N_BLOCK_ENV_ACCESS_IN_NODE`), ela precisa ser `false` nesta instância.
* Credencial `postgres` **id `tre-dev-postgres`** — banco do schema (`sales_intelligence`).
* Credencial `httpHeaderAuth` **id `tre-dev-api-controlada`** — `Authorization: Bearer <chave da API>`.
  A chave é gerada por `scripts/odoo/preparar_api_teste.py` (arquivo 600, nunca em log/argumento).

Importação (o cofre do n8n é por instância; produção repete o mesmo passo com os ids do contrato):

```bash
n8n import:credentials --input=credenciais.json
n8n import:workflow --input=n8n/workflows/TRE-outbox-consumer.json
n8n execute --id=TREOUTBOXCONSUM1 --rawOutput   # ciclo manual (a prova de ponta a ponta)
```

### 4.2 Ligar a agenda

O workflow nasce **inativo**. Ligar o gatilho agendado é decisão de operação: ativar na UI
(ou `n8n update:workflow --id=TREOUTBOXCONSUM1 --active=true`) **depois** de rodar um ciclo manual
com a fila real e conferir a trilha. Enquanto não houver scheduler distribuído, uma execução por
minuto num único n8n é o desenho esperado (a fila é idempotente por chave; ver §6).

### 4.3 Diagnóstico rápido

```sql
-- fila: o que está pendente, o que está travado e por quê
SELECT status, count(*), min(created_at), max(attempts) FROM sales_intelligence.outbox_events GROUP BY status;
SELECT id, event_type, attempts, left(last_error, 120) FROM sales_intelligence.outbox_events
 WHERE status IN ('DEAD_LETTER','RETRY') ORDER BY created_at DESC LIMIT 20;

-- trilha: a entrega deixou rastro? a chave duplicou?
SELECT idempotency_key, status, completed_at, left(error_message, 120)
  FROM sales_intelligence.sync_events ORDER BY created_at DESC LIMIT 20;
```

Log de auditoria da API no servidor do Odoo: uma linha `TF_API_AUDIT` por chamada autenticada.
Contagem de linhas de auditoria == número de eventos com tentativa de entrega (recusa local não
gera chamada nenhuma) — é a checagem mais barata de "o consumidor não inventou caminho".

### 4.4 Reenfileirar um DEAD_LETTER

Não se apaga trilha. Para reprocessar depois de corrigir o dado/política:

```sql
UPDATE sales_intelligence.outbox_events
   SET status='RETRY', attempts=0, last_error=NULL
 WHERE id='<uuid do evento>';
```

O teto de tentativas volta a contar do zero — é uma decisão consciente de operação, não automática.

**Chave já entregue volta como REPLAY, não como nova entrega (T02).** Se a chave do evento
reenfileirado já tem linha `COMPLETED` na trilha, o consumidor finaliza o evento **sem chamar a API**
— e isso é o desenho, não um defeito: a chave é a identidade do efeito no CRM e o registro da trilha
já guarda o pedido e a resposta daquela entrega. Para forçar uma **nova** escrita, o caminho é um
evento novo (outro `id`/`event_type`, portanto outra chave) — não existe, e não deve existir, operação
que apague a trilha para burlar a garantia. Antes de reenfileirar, confira o que a trilha diz:

```sql
SELECT idempotency_key, status, completed_at, left(error_message, 120)
  FROM sales_intelligence.sync_events
 WHERE idempotency_key = 'outbox:<uuid do evento>:<event_type>';
```

`COMPLETED` → o reenfileiramento vira REPLAY (no-op no CRM). `FAILED` ou `REFUSED` → o evento volta a
ser entregue de verdade.

## 5. Evidência do aceite

`bash scripts/n8n/verificar-outbox-consumer.sh` mede, num **trio descartável próprio**
(postgres + odoo + n8n criados e destruídos na hora, banco `tre_e02_outbox`):

* lente estrutural (93 itens) e suite do núcleo (124 itens), incluindo o código **embutido** no workflow;
* 7 eventos de fila no ciclo 1 (válido, atualização da mesma identidade, sem versão, fora do
  contrato, sem `name`, sem identidade, identidade ambígua) com o estado final medido item a item;
* Odoo **parado** → falha transitória (`RETRY`, `attempts=1`, trilha `FAILED`); Odoo de volta →
  o retry entrega (`PROCESSED`, `attempts=2`, **uma** linha de trilha, **um** parceiro);
* evento já no teto → `DEAD_LETTER` **sem** chamada e **sem** escrita no CRM;
* **dedup por chave (ciclo 5)**: dois eventos voltam à fila com os IDs originais — o de chave
  `COMPLETED` vira REPLAY (`PROCESSED`, `attempts` inalterado, **uma** chamada a menos na contagem do
  ciclo) e o de chave `REFUSED` é reentregue. Mede-se que o ciclo com 2 eventos chamou a API **uma**
  vez, que a trilha **não cresceu** e que a linha do replay é a **mesma** (mesmo `id`, mesmo
  `completed_at`, mesma resposta da entrega original) — e que o parceiro do CRM segue com o `name` e o
  score da entrega original (o replay não reescreveu nada);
* contagem de chamadas autenticadas, segredo fora do versionado, ambiente do dev intocado;
* `sha256` dos 7 artefatos sob teste **fixado nas guardas** e **reconferido no fecho** (dois itens,
  com juiz próprio): artefato que mude no meio da medição reprova o aceite.

`--prova-de-dente` roda o aceite em cópias mutadas do workflow (sem exigir `event_version`, sem
incrementar `attempts`, sem teto, com o mapeamento trocado, **sem a consulta da trilha** e **com a
guarda de status da trilha afrouxada**) e exige que **o item que aquela
mutação quebra** reprove. O modo é **fail-closed** (rodada 2): antes de contar dente ele roda um
sub-run **não mutado** (baseline) que tem de ficar verde — sem isso, ambiente quebrado devolveria
`NAO_CONTA` em todos os dentes e um "verde" não significaria nada —, os vereditos vão para arquivo
(o laço roda em subshell) e a agregação só fecha com `OUTBOX_CONSUMER_DENTE_OK` quando **todos** os
vereditos forem `DENTE_CUMPRIDO`; qualquer outro veredito (`NAO_CONTA`, `MUTACAO_SEM_DENTE`,
`MUTACAO_NAO_APLICADA`), baseline vermelho ou juiz com falta fecha com
`OUTBOX_CONSUMER_DENTE_FALHOU` e **exit 1**. Dois juízes são conferidos por saídas sintéticas: o do
dente (mutação sem efeito / mutação cumprida / ambiente quebrado / âncora quebrada) e o da
reconferência de `sha256` (idêntico / mudado) — senão ambiente quebrado viraria "dente cumprido" e o
item de integridade dos artefatos poderia comparar duas medidas do mesmo nada.

Modos: `--apenas-codigo` (estático, sem containers), `--apenas-consumo` (o trio + os ciclos) e
`--manter`. **`--manter` preserva de propósito o trio e o diretório do preparo — e esse diretório
contém a chave da API em claro** (`chave.txt`) enquanto existir: é modo de depuração, e o diretório
tem de ser removido no fecho (`ls -d /tmp/verificacao-outbox-*`). Sem `--manter`, containers, rede e
diretório do preparo saem no fim (medido: 0 resíduo). Os **logs** ficam em `TRE_LOG_DIR` (default
`/tmp/verificacao-outbox-consumer`) de propósito, para leitura posterior.

## 6. Limites conhecidos (o que este card não resolve)

* **Deduplicação de entrega** — implementada em T02 (AC9): ver §3 (Replay) e §4.4. A proteção é a
  chave derivada do evento (`outbox:<id>:<event_type>`), `UNIQUE` na trilha: a chave já `COMPLETED`
  vira REPLAY (sem chamada, sem nova linha, sem reescrever o CRM) e retry/reenfileiramento da mesma
  chave não duplica trilha. A idempotência da **política da API** (`idempotency_key`) continua sendo a
  segunda linha de defesa, para o caso de duas instâncias concorrentes (ver o item abaixo).
* **Concorrência entre dois consumidores**: a leitura da fila não usa `FOR UPDATE SKIP LOCKED`, então
  dois n8n processando a mesma fila podem pegar o mesmo evento — e, pior, dois eventos da mesma
  identidade em paralelo reproduzem a duplicação descrita em §4.0 (a serialização protege **dentro**
  de uma instância, não entre instâncias). Em dev (um n8n) não há efeito; antes de escalar, este
  ponto entra como card próprio (lock por agregado, ou `SKIP LOCKED` + trava na identidade).
* **Eventos com payload de segredo**: o `payload` do evento é gravado na trilha
  (`request_payload`). Evento que carregue segredo não pode entrar na fila (contrato §9) — regra do
  produtor, não deste consumidor.
* **Agenda em produção** e **importação do cofre real**: ficam para a operação (o aceite usa cofre
  descartável com as chaves de um banco descartável).

## 7. Rollback

1. Desligar a agenda (`--active=false`) — a fila para de crescer para o CRM; nada é perdido.
2. `outbox_events` e `sync_events` guardam o estado: nenhum evento é apagado pelo rollback.
3. Reimportar a versão anterior do workflow (`n8n import:workflow`) para voltar o comportamento.
4. Se a falha for na **política da API**, o rollback é dela (versão do `politica_api.json`), não
   do consumidor — o consumidor só transporta.

## 8. Registro

Execuções e resultados ficam em `docs/operations/registro-de-execucoes.md`.
