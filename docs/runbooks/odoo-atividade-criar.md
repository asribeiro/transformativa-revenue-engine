# Operação de escrita de negócio `atividade_criar` — a ATIVIDADE comercial na âncora declarada

**Card:** `TRE-W3-E01-T05` (`t_cb615018`) · **branch:** `feature/TRE-W3-E01-T05` (empilhada em
`feature/TRE-W3-E01-T04` + `feature/TRE-W3-E01-T03`) · **módulo:** `transformativa_sales_ai`
(`odoo/addons/transformativa_sales_ai`) · **operação:** `POST /tf/api/v1/atividade_criar` ·
**modelo:** `mail.activity` · **ação:** `criar`

Este runbook é o manual da operação **e** o registro do aceite medido. Ele é escrito para quem
precisa (a) integrar o consumidor externo (n8n), (b) revisar o aceite (estágio 6, perfil `tester`) ou
(c) operar/rollbackar a operação. A §11 é o registro da rodada medida, com os números lidos da VPS.

---

## §1 Desenho — a operação é uma DECLARAÇÃO, com UM pedaço de código novo

A operação entra por **declaração** na política (`api/politica_api.json` sobe de `1.2.0` para
`1.3.0`): nenhuma rota nova, nenhuma linha de motor, nenhum `sudo()`. O que este card **acrescenta de
código** é uma coisa só, e ela foi forçada pelo ambiente — ver §1.2.

```http
POST /tf/api/v1/atividade_criar        Authorization: Bearer <chave de API>
{"idempotency_key": "tre-...", "dry_run": false, "correlation_id": "tre-...",
 "parametros": {"valores": {"res_id": 42, "summary": "Ligar para o decisor",
                            "date_deadline": "2026-10-10",
                            "tf_idempotency_key": "tre-...", "tf_correlation_id": "tre-..."}}}
```

### §1.1 A decisão de fronteira: de quem é a atividade

| grupo | campos | quem manda |
| --- | --- | --- |
| âncora | `res_model` (FIXO), `res_id` (obrigatório) | **política** — o chamador não escolhe o modelo-alvo |
| conteúdo | `summary`, `date_deadline`, `activity_type_id` | produtor do fato |
| destino | `user_id` | produtor do fato (opcional: o Odoo põe o usuário da chave) |
| rastro | `tf_idempotency_key`, `tf_correlation_id` | produtor do fato (registrados na atividade, além da trilha) |

Decisões, com o porquê:

| decisão | porquê |
| --- | --- |
| a âncora é `res.partner` e é **valor fixo** da política | é o documento que **existe** no fluxo de fundação do E2E #001 (doc 08 §3 passos 13..15: empresa → contato → atividade, quando ainda **não** existe lead). Atividade ancorada em `crm.lead` **não** é servida por esta versão: recusa nomeada, nunca atividade no lugar errado (§5, lacuna roteada) |
| `activity_type_id`, `date_deadline` e `user_id` são declarados e **não** obrigatórios | o modelo tem default para os três; exigir do consumidor externo o id interno de `mail.activity.type` seria identidade por suposição (contrato §5) |
| a operação **não** declara identidade | a ação é `criar`: não há registro anterior a casar. Quem garante "não duplicar" é a dedup por `idempotency_key` (card `TRE-W3-E02-T02`) — e a ausência dela é **medida**, não silenciada (§3, item do replay) |
| o rastro vai para o **registro**, não só para a trilha | o motor devolve `tf_*` no plano "para ser registrada"; o mesmo padrão dos campos de rastreio do card `TRE-W2-E04-T02` em `crm.lead` |

### §1.2 O código novo: a tradução da âncora (`models/mail_activity.py`)

**Medido no dev (Odoo 19), com `odoo shell`, antes de escolher o desenho:**

* `create({'res_model': 'res.partner', 'res_id': n, ...})` **não escreve** a âncora: `res_model` é
  campo *related* (`res_model_id.model`), `store=True`, `readonly=True` e **sem inverse** — a coluna
  vai `NULL` e o INSERT morre na CHECK `mail_activity_check_res_id_is_set_if_model`;
* **pior que o erro:** o ORM **descarta em silêncio** o valor que o chamador mandou — exatamente o
  que a regra da casa proíbe ("nunca ignorar o que o chamador disse", a mesma razão de
  `campo_fixo_divergente`);
* `res_model_id` (Many2one para `ir.model`) resolve — mas é **id de banco**, que muda de ambiente
  para ambiente: não serve de valor declarado numa política versionada;
* o próprio `mail.activity` já resolve **nome → `res_model_id`** no seu `default_get` — é o idioma da
  casa.

Então: a política declara o **nome** do modelo (`valores_fixos.res_model = "res.partner"`) e o módulo
resolve o nome no `ir.model` **do banco em uso**, no `create` (override de `create`/
`@api.model_create_multi`). A tradução só age quando `res_model` chega em `vals` — o caminho padrão do
Odoo manda `res_model_id`. Divergência entre `res_model` e `res_model_id` no **mesmo** pedido é recusa
(nomeada), nunca escolha silenciosa.

---

## §2 ACCEPTANCE — o que tem de valer (AC1..AC9)

| AC | critério | onde é medido |
| --- | --- | --- |
| AC1 | operação **declarada** na política em vigor (tipo escrita, `mail.activity`, `acao: criar`, chave exigida) e servida pela mesma porta única (**1 rota**) | suíte `test_atividade_criar` 01 + passo 3 do verificador + passo 4 (greps) |
| AC2 | **superfície fechada e âncora declarada**: `res_model` divergente → 422 `campo_fixo_divergente`; `res_model_id` (id interno) → 422 `campo_nao_declarado`; `note`/`stage_id` → 422 `campo_nao_declarado`; sem `res_id` → 422 `campo_obrigatorio_ausente`; **nada criado** em cada recusa | suíte 02/04/05/06 + passo 3 do verificador (com contagem no banco) |
| AC3 | a atividade **nasce no modelo declarado**, medido no **banco** (`ir_model.model` via `res_model_id` e `res_id`), com resumo/prazo/tipo do chamador e o id devolvido sendo o do registro | suíte 03/07 + passo 3 do verificador (SQL) |
| AC4 | **contrato de integração**: `idempotency_key` exigida/validada; `dry_run` descreve e **não** cria; `correlation_id` ecoado; rastro `tf_*` **registrado na atividade** | suíte 08/09/10/11 + passo 3 do verificador |
| AC5 | **guarda de ambiente** (ADR-005) na escrita: `homologacao` → 503 `ambiente_nao_permitido` e **nada** criado; sem ambiente declarado → 503 `ambiente_nao_declarado` | suíte 14/15 + passo 3d do verificador (servidor NOVO depois da troca pelo ORM) |
| AC6 | **trilha**: uma linha `TF_API_AUDIT` por chamada autenticada (inclusive recusas), com operação/ação/modelo/ids, **sem token e sem payload** | suíte 12/13 + passo 3c do verificador (log bruto do servidor) |
| AC7 | **ACL do dono da chave**: a criação passa pelas ACLs do usuário da chave (o `mail.activity.create` exige acesso de **escrita** ao documento ancorado); sem esse acesso → 403 `acesso_negado` e **nada** criado; a criação bem-sucedida é do usuário da chave (`create_uid` medido) | suíte 16 + passo 3 do verificador (segunda chave, sem escrita) |
| AC8 | **ambiente intocado**: dupla descartável própria; `dev`/`homolog`/`prod` medidos antes e depois; nenhum DDL fora do banco descartável | limpeza do verificador |
| AC9 | **lacunas medidas, não silenciadas**: (a) a operação não declara identidade e o `identificador` escalar é recusado; (b) o **replay da mesma chave** ainda cria uma segunda atividade (dedup é o `E02-T02`) — medido; (c) `crm.lead` como âncora é recusa nomeada e a contagem no modelo não declarado **não** muda | suíte 17/18/19 + passo 3 do verificador |

---

## §3 TEST — como cada AC é medido (na VPS, por execução real)

```bash
# na VPS, a partir de ARQUIVO (nunca por stdin), como ROOT (a dupla descartável exige chown para o
# uid do container), no checkout publicado do commit:
sudo -n env TRE_LOG_DIR=/opt/tre/evid-t_cb615018/logs-aceite \
  bash scripts/odoo/verificar-atividade-criar.sh
sudo -n env TRE_LOG_DIR=/opt/tre/evid-t_cb615018/logs-dente \
  bash scripts/odoo/verificar-atividade-criar.sh --prova-de-dente
```

| modo | o que roda |
| --- | --- |
| (padrão) | tudo: suíte pura do motor, instalação, suíte do Odoo, fase HTTP por `curl`, contrato |
| `--apenas-motor` | só a suíte pura do motor (sem Odoo) |
| `--apenas-suites` | instalação em banco limpo + `--test-enable` |
| `--apenas-http` | instalação + servidor descartável + `curl` de fora do processo |
| `--prova-de-dente` | 4 mutações, cada uma **tem** de reprovar (harness fail-closed) |

O verificador **não** usa `pg-odoo-dev`/`odoo_dev`/`/opt/tre/repo`: sobe a própria dupla descartável
(`postgres:16` + `odoo:19.0`) em rede própria e a remove no fim.

---

## §4 ROLLBACK

1. **Desligar a operação sem tocar em código:** remover a operação da política (ou apontar
   `tf.api.politica` para uma política sem ela) e reiniciar o servidor — a resposta passa a ser
   `404 operacao_nao_declarada`. A política é lida a cada chamada.
2. **Reverter o código:** `git revert <commit do card>` — a única superfície de código é
   `models/mail_activity.py` (+ a linha de import em `models/__init__.py` e a dependência `mail` no
   manifesto). Os dois campos `tf_*` continuam no banco e são inertes.
3. **Dados:** as atividades criadas são registros de `mail.activity` (não há espelho em
   `sales_intelligence`); apagar uma atividade é operação de UI/Odoo, **não** da API.

---

## §5 RISK

| risco | mitigação medida neste card |
| --- | --- |
| a âncora ser escolhida pelo chamador (atividade no lugar errado) | `res_model` é **valor fixo** da política: divergência → 422 e **nada** criado; o caminho pelo id interno (`res_model_id`) também é fechado (suíte 04/06, passo 3) |
| o ORM descartar em silêncio a âncora (defeito do Odoo 19) | tradução no `create` + medição **no banco** (`ir_model.model` via `res_model_id`) — e dente 4 muta exatamente essa tradução |
| a API "ignorar" campo que o consumidor mandou | fora da declaração → 422 nomeada (nunca descarte); medido para `note`, `stage_id`, `res_model_id` |
| duplicar a atividade a cada retry | **não** resolvido aqui: a dedup por chave é o `E02-T02`; a lacuna está **medida** (§3, replay → 2 registros) e citada no doc 08 §3 passos 18-19 como cobrança do E2E #001 |
| payload de negócio vazar para a trilha | `AC6`: o resumo/nome não entram na trilha, medido no log bruto |
| criar atividade em documento que a chave não pode escrever | `AC7`: segunda chave, sem escrita → 403 e nada criado (o controlador não faz `sudo()` no dado) |
| política é artefato compartilhado da onda | declaração aditiva + versão nova (`1.3.0`) + testes que leem o artefato (âncora `ANCORA:ITEM_DATADO`) e a forma de identidade reconciliada em uma só (`campos_de_identidade`) |
| item de aceite "verde" sem medir nada | harness de dentes **fail-closed**: prova que não mede (ambiente) ou que não reprova o item alvo reprova o próprio harness |

---

## §6 O contrato HTTP da operação

| campo do envelope | obrigatório | observação |
| --- | --- | --- |
| `idempotency_key` | sim | formato validado (`a-z0-9-`, 8..80 — ver motor) |
| `dry_run` | não | `true` descreve e não escreve |
| `correlation_id` | não | ecoado na resposta e gravado na atividade (se enviado no rastro) |
| `parametros.valores` | sim | campos declarados (§1.1) |
| `parametros.identificador` | **não aceito** | a operação não declara identidade → 400 `payload_invalido` |

| `codigo` | HTTP | quando |
| --- | --- | --- |
| `campo_fixo_divergente` | 422 | `res_model` diferente da âncora declarada |
| `campo_nao_declarado` | 422 | campo fora da declaração (inclusive `res_model_id`) |
| `campo_obrigatorio_ausente` | 422 | `res_id` fora do payload |
| `idempotency_key_ausente` / `idempotency_key_invalida` | 422 | chave ausente / fora do formato |
| `payload_invalido` | 400 | `identificador` escalar, ou payload que o motor não lê |
| `acesso_negado` | 403 | ACL do usuário da chave (o documento ancorado é de escrita alheia) |
| `ambiente_nao_permitido` / `ambiente_nao_declarado` | 503 | ADR-005 (fail-closed) |
| `operacao_nao_declarada` | 404 | política sem a operação (rollback do §4) |

---

## §7 O que este card NÃO entrega (declarado)

* **dedup por `idempotency_key`** (retry/replay de verdade): card `TRE-W3-E02-T02`. Aqui a chave é
  exigida, validada e registrada — e a falta da dedup é **medida** (§3, replay);
* **atividade ancorada em `crm.lead`** (reunião/proposta/lead): a âncora é fixa em `res.partner`
  nesta versão; aceitar um **conjunto** declarado de âncoras exige vocabulário novo no mecanismo de
  valor fixo (dono: `E02-T02`), e o documento do E2E #001 não precisa dele nas passos 13..15;
* **rate limit, cache de política, observabilidade durável**: card `TRE-W3-E05-T01`;
* **views do Odoo exibindo `tf_idempotency_key`/`tf_correlation_id` na atividade**: os campos existem
  e são gravados (medidos), mas não foram colocados em nenhuma view — o rastro é para reconciliação,
  não para a UI do vendedor.

---

## §8 Operação (quem chama, com o quê)

* consumidor: n8n (doc 06), pela porta única `POST /tf/api/v1/...` com a chave de API do usuário de
  integração (`tf_api_integracao`), cujos **grupos de venda** dão a escrita necessária em
  `res.partner` (sem eles, a resposta é 403 `acesso_negado` — medido);
* a chave nasce na VPS, em arquivo `600`, e é lida pelo `curl` por arquivo de configuração (nunca em
  `ps`, argumento ou log);
* ambiente: só `dev` (ADR-005). Escrita em `homologacao`/`producao` responde 503.

## §9 Receita para a próxima operação de escrita (o que aprendemos aqui)

1. Escreva a **declaração** antes do código; se o campo tem que ser decidido por você (a âncora, um
   valor fixo), ele pertence à política — não ao payload.
2. **Meça o ORM antes de desenhar**: campo *related* sem inverse e `readonly` **descarta em silêncio**
   o valor do chamador. Se for esse o caso, o nome declarado precisa de tradução explícita no
   `create`, com fail-closed em divergência.
3. A identidade só existe para quem **casa registro**; ação de criação não declara identidade, e a
   dedup por chave é um mecanismo separado (diga isso no texto da operação).
4. Nada de "id de banco" em artefato versionado: `ir.model` muda de ambiente para ambiente.
5. Todo item de aceite tem de ter um **dente** que o derrube; dente que mede o artefato errado é
   dente decorativo.

---

## §10 Arquivos da entrega

| arquivo | papel |
| --- | --- |
| `odoo/addons/transformativa_sales_ai/api/politica_api.json` | política `1.3.0` com a operação declarada (e a reconciliação das identidades numa forma só) |
| `odoo/addons/transformativa_sales_ai/models/mail_activity.py` | **código novo**: tradução da âncora + os dois campos de rastreio |
| `odoo/addons/transformativa_sales_ai/models/__init__.py` | registro do modelo (sem ele o arquivo não carrega) |
| `odoo/addons/transformativa_sales_ai/__manifest__.py` | dependência `mail` explícita (o módulo herda `mail.activity`) |
| `odoo/addons/transformativa_sales_ai/tests/test_atividade_criar.py` | suíte do Odoo (19 testes) |
| `scripts/odoo/verificar-atividade-criar.sh` | aceite próprio (suíte pura + Odoo + HTTP + dentes) |
| `scripts/odoo/testar_motor_api.py` | suíte pura do motor (bloco novo da operação) |
| `scripts/odoo/preparar_api_teste.py` | `TRE_API_GRUPOS` (a segunda chave da prova de ACL) |
| `docs/runbooks/odoo-atividade-criar.md` | este runbook |

---

## §11 Aceite medido (rodada desta entrega)

> Preenchido com os números lidos da VPS na rodada final.
