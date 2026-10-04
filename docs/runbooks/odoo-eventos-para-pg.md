# Runbook — eventos Odoo → PostgreSQL (fila de saída + porta única de ingestão)

Card: **TRE-W3-E03-T01** (`t_85cb2838`) — board `transformativa-revenue-engine`, épico W3 (E03).
Escopo: **o caminho de produção** dos eventos do funil, do fato de negócio no Odoo até a linha de
trilha em `sales_intelligence.sync_events`, passando **só** pela porta única (webhook do n8n).

O que este card **NÃO** faz (declarado, para não virar promessa implícita):

* **não liga a agenda**: o cron `ir_cron_tf_eventos` nasce **inativo** (`active = False`); ativar é
  decisão de operação (§4.2);
* **não é o consumidor do caminho de volta** (`sales_intelligence.outbox_events` → API controlada do
  Odoo): isso é o TRE-W3-E02-T01, que tem runbook próprio (`n8n-outbox-consumer.md`);
* **não faz reconciliação** nem corrige divergência: quem faz isso é o TRE-W3-E04-T01;
* **não escreve em tabela de negócio**: a porta escreve **só** a trilha (`sync_events`) — quem
  materializa o funil no PostgreSQL é o E04.

## 1. Por que uma porta única (e por que n8n)

O módulo do Odoo **não tem driver de banco**: ele não conhece `psycopg`, não tem DSN, não executa
SQL. O fato de negócio vira linha na **fila local** (`tf.evento.outbox`, no próprio PostgreSQL do
Odoo) e um único caminho tira dali: `POST <base>/webhook/tre/odoo-eventos` no n8n, com o token da
porta. Isso é a regra do contrato de dados (§6) e é a razão de o E03 existir nesta ordem: primeiro a
trilha, depois a reconciliação.

Consequências práticas:

* o Odoo pode ficar sem a porta no ar: os eventos **acumulam na fila** (nada se perde) e o ciclo
  seguinte retenta;
* mudar destino (banco, schema, n8n de produção) é configuração — `ir.config_parameter` —, não
  código;
* qualquer caminho paralelo (SQL direto, XML-RPC, psycopg) é **reprovado por teste**, não por
  revisão (`test_22_modulo_nao_tem_caminho_paralelo_para_o_postgres`).

## 2. Artefatos versionados (a fonte é o repositório, não a UI do n8n)

| Artefato | Papel |
| --- | --- |
| `n8n/contracts/odoo-events-ingest.v1.json` | **O contrato da porta**: rota, método, autenticação, os 7 eventos com campos exigidos, ordem da validação, motivos de recusa, trilha, credenciais por id/nome |
| `n8n/codigo/nucleo-ingest-eventos.js` | Núcleo em JS puro (decisão aceitar/recusar + parâmetros da trilha). Roda em node e dentro do Code node |
| `n8n/sql/ingerir-evento.sql` | Escreve a linha de trilha do evento **aceito** e devolve a resposta da porta |
| `n8n/sql/registrar-recusa.sql` | Escreve a linha de trilha do evento **recusado** (status `REFUSED` + motivo) |
| `n8n/workflows/TRE-odoo-events-ingest.json` | O workflow **gerado** dos quatro acima |
| `scripts/n8n/montar_workflow_ingest.py` | Monta o workflow (artefato derivado; `--conferir` reprova divergência) |
| `scripts/n8n/conferir_ingest_estrutural.py` | Lente estrutural: contrato × núcleo × SQL × nós × módulo Odoo |
| `scripts/n8n/testar_nucleo_ingest.js` | Suite do núcleo (node puro) + o código **embutido** no workflow |
| `scripts/n8n/mutar_workflow_ingest.py` | Mutações nomeadas da prova de dente |
| `scripts/n8n/verificar-odoo-eventos.sh` | Aceite ponta a ponta no trio descartável + prova de dente |
| `scripts/odoo/conferir_eventos_no_contrato.py` | Confronta a lista do módulo com o Data Contract V1.0 |
| `odoo/addons/transformativa_sales_ai/models/tf_evento_outbox.py` | A fila (`tf.evento.outbox`), a lista fechada de eventos e o remetente |
| `odoo/addons/.../models/eventos_crm_lead.py` | Detecção dos 5 eventos de funil (gancho no `write` do ORM) |
| `odoo/addons/.../models/eventos_mail_activity.py` | Detecção de `ACTIVITY_COMPLETED` |
| `odoo/addons/.../models/eventos_calendar_event.py` | Detecção de `MEETING_CREATED` |
| `odoo/addons/.../data/ir_cron_tf_eventos.xml` | A agenda — **inativa** de propósito |

O workflow **não se edita à mão**. Editar o JSON direto cria divergência com o contrato, e a lente
reprova (`o workflow sob teste e' o montado a partir dos artefatos`). O fluxo é: editar
contrato/núcleo/SQL → `python3 scripts/n8n/montar_workflow_ingest.py --saida n8n/workflows/TRE-odoo-events-ingest.json`
→ commitar os dois.

## 3. O que a porta faz e o que o módulo faz

### 3.1 No Odoo (produtor)

1. O fato acontece pelo ORM (`write`/`action_set_won`/`action_set_lost`/`_action_done`/`create` de
   `calendar.event`) — a detecção é **no mesmo ciclo**, não há varredura de banco atrás de mudança.
2. Cada fato do contrato vira **uma linha** em `tf.evento.outbox`, com `event_type`, `event_version`
   (`1.0`), `timestamp`, `payload`, `idempotency_key` (derivada do conteúdo do fato) e
   `correlation_id`. Um `write` pode gerar **dois** eventos de propósito: mudar para um estágio de
   ganho produz `STAGE_CHANGED` **e** `OPPORTUNITY_WON` (são dois fatos distintos).
3. `_tf_enviar_pendentes()` entrega a fila: `2xx` → `SENT`; recusa nomeada (HTTP 422) →
   `DEAD_LETTER` **sem retry**; resto (transporte, 5xx, 408, 429) → `RETRY` até o teto (3), depois
   `DEAD_LETTER` com o motivo visível. Nenhum evento é apagado: o estado da fila **é** o registro de
   erro.

### 3.2 Na porta (n8n)

4. Webhook `POST /webhook/tre/odoo-eventos` autenticado por header (`X-Tre-Ingest-Token`,
   credencial `httpHeaderAuth` do cofre). Sem token → recusa do próprio webhook e **nada escrito**.
5. O Code node decide com o contrato na mão, na ordem declarada (`envelope.ordem_da_validacao`):
   corpo → `event_type` presente e declarado → `event_version` presente e suportada → `timestamp` →
   `payload` → `idempotency_key` presente e no formato → campos exigidos do evento → identidade
   canônica (ausente **não** recusa: vira `entity_id` nulo, lacuna visível).
6. Aceito → `ingerir-evento.sql` grava a trilha e responde `{aceito: true, duplicado, sync_event_id}`;
   recusado → `registrar-recusa.sql` grava a trilha com `status = REFUSED` e responde
   `{aceito: false, motivo: '<nomeado>', codigo_http: 422}`.
7. **Idempotência em dois níveis**: a `idempotency_key` é `UNIQUE` na trilha e o `INSERT` é
   `ON CONFLICT (idempotency_key) DO NOTHING` — reenvio do mesmo fato responde `duplicado: true` e
   **não** cria segunda linha (regra 2 do contrato: retry não pode criar duplicata).

## 4. Operação

### 4.1 Variáveis e credenciais

* `ir.config_parameter` **`transformativa_sales_ai.ingest_url`** — base da porta (ex.:
  `http://n8n:5678`). Sem ela, a fila fica **intacta** e o resumo do ciclo diz
  `erro: porta_nao_configurada` (fail-closed: sem destino declarado nada sai).
* `ir.config_parameter` **`transformativa_sales_ai.ingest_token`** — o token da porta. Gravar com
  `scripts/odoo/preparar_remetente_eventos.py` (lê de **arquivo 600**; o segredo nunca entra em
  argumento de linha de comando nem no log).
* Credencial `postgres` **id `tre-dev-postgres`** — banco do schema (`sales_intelligence`).
* Credencial `httpHeaderAuth` **id `tre-dev-ingest-token`** — header `X-Tre-Ingest-Token`.

Importação (o cofre é por instância; produção repete com os mesmos ids do contrato):

```bash
n8n import:credentials --input=credenciais.json
n8n import:workflow --input=n8n/workflows/TRE-odoo-events-ingest.json
n8n update:workflow --id=TREodooEventos1 --active=true   # o webhook de produção só existe com o workflow ATIVO
```

### 4.2 Ligar a agenda

O workflow nasce **inativo** e o cron do Odoo nasce **inativo**: nada dispara sozinho até a operação
decidir. Ordem recomendada, com o workflow já ativo e a trilha conferida:

```python
# no Odoo (shell/UI): ligar a agenda depois de conferir a trilha do primeiro ciclo manual
env.ref('transformativa_sales_ai.ir_cron_tf_eventos').write({'active': True})
```

Ciclo manual (prova de ponta a ponta, sem agenda):

```python
resumo = env['tf.evento.outbox']._tf_enviar_pendentes(limite=200)
# {'na_fila': 7, 'SENT': 7, 'RETRY': 0, 'DEAD_LETTER': 0, 'duplicados_no_destino': 0, 'erro': False}
```

### 4.3 Diagnóstico rápido

```sql
-- fila do Odoo: o que está pendente, o que travou e por quê
SELECT event_type, status, count(*), max(attempts) FROM tf_evento_outbox GROUP BY 1, 2 ORDER BY 1;
SELECT id, event_type, attempts, http_status, left(last_error, 120)
  FROM tf_evento_outbox WHERE status IN ('RETRY','DEAD_LETTER') ORDER BY id DESC LIMIT 20;

-- trilha do PostgreSQL: o evento chegou? duplicou? foi recusado?
SELECT operation, status, count(*), max(created_at) FROM sales_intelligence.sync_events
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT idempotency_key, status, completed_at, left(error_message, 120)
  FROM sales_intelligence.sync_events WHERE status = 'REFUSED' ORDER BY created_at DESC LIMIT 20;
```

Reenfileirar o que foi para `DEAD_LETTER` **depois** de corrigir a causa (o evento não volta
sozinho de propósito):

```python
env['tf.evento.outbox'].search([('status','=','DEAD_LETTER')]).write({'status':'RETRY','attempts':0,'last_error':False})
```

## 5. Aceite (o que roda e o que ele mede)

```bash
bash scripts/n8n/verificar-odoo-eventos.sh              # aceite completo (trio descartável próprio)
bash scripts/n8n/verificar-odoo-eventos.sh --apenas-codigo
bash scripts/n8n/verificar-odoo-eventos.sh --prova-de-dente
bash scripts/n8n/verificar-odoo-eventos.sh --manter      # preserva o trio para inspeção
```

O aceite sobe um **trio descartável próprio** (PostgreSQL + Odoo + n8n, nomes com sufixo da rodada),
cria os fatos de negócio pelo ORM, entrega pela porta e mede **coluna a coluna** na trilha. Ele
reprova se: a lista de eventos divergir do contrato, o workflow divergir do montado, o módulo ganhar
caminho paralelo para o PostgreSQL, o reenvio criar segunda linha, a recusa não vier nomeada, o
retry estourar o teto, o token aparecer no versionado/na trilha, ou o ambiente de dev/homolog/produção
mudar durante a medição. Resultado da rodada publicada (`597f2dd`): `EVENTOS_ODOO_PG_OK (84 itens,
0 falhas)` com a suíte do card em `0 failed, 0 error(s) of 22 tests`, e a prova de dente em
`EVENTOS_ODOO_PG_OK (6 itens, 0 falhas)`.

As **quatro recusas** medidas no passo E são: envelope sem `event_version`, evento fora da lista
fechada, campo exigido ausente e chave de idempotência fora do formato — todas com HTTP 422, motivo
nomeado e rastro `REFUSED` na trilha.

A **prova de dente** roda primeiro um baseline **não mutado** (que tem de ficar verde) e depois 4
mutações nomeadas; cada dente só conta se o item declarado daquela mutação **reprovar**:

| Mutação | Trecho do item que tem de reprovar |
| --- | --- |
| `sem_versao` | `envelope sem event_version` |
| `sem_formato_da_chave` | `chave de idempotencia fora do formato` |
| `sem_campos_exigidos` | `campo exigido ausente` |
| `sem_on_conflict` | `reenvio do mesmo fato nao cria segunda linha na trilha` |

O juiz casa `^FALHOU .*<trecho>` na saída da sub-rodada, então o item que uma mutação deve reprovar
tem de ter **a mesma identidade nos dois ramos** (`ok` e `falhou`) — item cujo ramo de falha tem
outra redação é lido como "mutação sem dente" e reprova o harness. A sub-rodada recebe o nome da
mutação em `TRE_MUTACAO` e, por isso, a divergência em relação ao montador é declarada como INFO
naquela rodada (é o propósito dela), nunca silenciada.

Escopo declarado do que o aceite **não** mede: a suíte do Odoo roda **só a classe do card**
(`--test-tags=/transformativa_sales_ai:TestEventosOdooPg`); as demais classes do módulo são de outros
cards e são medidas nos aceites deles.

## 6. Armadilhas aprendidas neste card

* **`crm.lead` não tem `company_currency_id`** — o campo de moeda é `company_currency`
  (`fields.Many2one` calculado). Ler o campo errado só aparece quando o teste roda, não na revisão.
* **`mail.activity._action_done()` ARQUIVA, não apaga**: o que prova a conclusão é
  `active = False` + `date_done`, não o registro ter desaparecido.
* **`action_set_lost()` escreve em dois tempos** (`action_archive()` e depois
  `probability = 0` + `lost_reason_id`): a perda pode ser emitida **antes** de o motivo existir — por
  isso `LOSS_REASON_RECORDED` é evento próprio e o `motivo` do `OPPORTUNITY_LOST` pode sair vazio.
  Não "conserte" isso: são dois fatos.
* **Regra de registro (carteira × tenant) se aplica ao usuário do teste**: lead sem `user_id` não é
  visível para o vendedor — teste de ACL com lead "de ninguém" mede a regra, não o detector.
* **Teste dentro do container só enxerga o diretório do módulo**: o `docs/data/data_contract_v1.json`
  do repositório **não** está montado; a conferência módulo × contrato JSON é do conferidor que roda
  no repo, e o teste congela a lista (de propósito, para o crescimento exigir decisão).
* **Literais de conexão x prosa**: texto de `help=` que cita o schema (`sales_intelligence`) não é
  caminho para o banco. Lente que casa palavra solta reprova documentação — case gramática de DSN e
  `.execute()`, não substring.
* **Conf do Odoo e cofre do n8n são lidos por OUTRO usuário do container**: o `odoo.conf` precisa
  ser legível pelo uid do Odoo e o diretório do n8n precisa pertencer a quem o container executa
  (`docker run --user $(id -u):$(id -g)`), senão o import do cofre morre em `EACCES` e o Odoo cai
  para os defaults sem dizer por quê.
* **A chave derivada de CONTEÚDO colapsa dois fatos distintos de payload idêntico** (lacuna
  declarada, preço do desenho) — a `idempotency_key` sai do conteúdo do fato
  (`odoo:<event_type>:<modelo>:<res_id>:<sha1 do envelope SEM o instante>`, §3.1 item 2), então dois
  fatos **diferentes** do mesmo tipo com payload idêntico (ex.: o mesmo lead saindo do estágio A para
  o B duas vezes no mesmo segundo, ou um valor que muda e volta ao anterior) geram a **mesma** chave:
  o segundo **não vira linha** na fila do Odoo nem na trilha, e o remetente o trata como já entregue
  (`SENT` no ciclo, ou `duplicado: true` na porta — §3.2 item 7). Ao ler a trilha, **não** trate essa
  ausência como perda de dado: é o colapso declarado, o que faz o reenvio do MESMO fato não duplicar
  (`ON CONFLICT (idempotency_key) DO NOTHING`, contrato §6 regra 2). O que **não** fazer para
  "consertar": emitir chave aleatória por envio — isso quebra a idempotência do retry e passa a criar
  duplicata a cada reentrega do mesmo fato.
