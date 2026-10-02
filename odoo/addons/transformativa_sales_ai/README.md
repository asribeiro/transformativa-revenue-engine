# `transformativa_sales_ai` — Sales AI da Transformativa no Odoo

Módulo Odoo Community que hospeda as **customizações e a integração** do Sales AI da
Transformativa. Card de origem da base: **`TRE-W2-E03-T01`** (`t_c536ce86`), onda W2, épico E03.
Runbook da base, aceite medido e rollback: **`docs/runbooks/odoo-modulo-sales-ai.md`**.

## Estado do módulo

O que já existe e quem entrega o quê:

| O que | Card | Estado |
|---|---|---|
| Base: manifesto, versão, dependências, empacotamento, teste do módulo | `TRE-W2-E03-T01` | entregue |
| Modelo canônico `tf.process.opportunity` (oportunidade canônica do lado Odoo) | `TRE-W2-E05-T01` | entregue |
| ACLs / segurança (carteira × tenant) | `TRE-W2-E07-T01` | entregue |
| Campos de dedup em `res.partner` (CNPJ, domínio, LinkedIn) | `TRE-W2-E04-T01` | entregue |
| Campos de rastreio em `crm.lead` | `TRE-W2-E04-T02` | entregue |
| Views do Sales AI | `TRE-W2-E06-T01` | **neste card** |
| API controlada | `TRE-W3-E01-T01` | entregue |
| Upsert de empresa pela API (`empresa_upsert`) | `TRE-W3-E01-T02` | entregue |
| Upsert de contato pela API (`contato_upsert`) | `TRE-W3-E01-T03` | **neste card** |
| API controlada | `TRE-W3-E01-T01` | **neste card** |
| Operação de escrita de negócio `oportunidade_upsert` (espelho da oportunidade no CRM) | `TRE-W3-E01-T04` | **neste card** |

## `tf.process.opportunity` — a oportunidade canônica do lado Odoo

Runbook do card: **`docs/runbooks/odoo-oportunidade-canonica.md`**.

O Data Contract V1.0 (`docs/data/DATA_CONTRACT_V1.md`) dá a oportunidade canônica ao Odoo
(`opportunity_owner: Odoo`) e **não** cria tabela de oportunidade no PostgreSQL: o lado
PostgreSQL/n8n referencia a oportunidade por UUID (`recommendations.opportunity_id`, sem FK).
Este modelo é essa entidade no Odoo.

| Campo | Tipo | De onde vem |
|---|---|---|
| `tf_uuid` | Char(36), obrigatório, único, imutável | Data Contract §3 — chave canônica (UUID). Aceita o UUID do produtor do fato; se ausente, gera UUID v4; valor que não é UUID é recusado |
| `name` | Char, obrigatório | identificação da oportunidade (`_rec_name`; é o que a view mostra — E06) |
| `active` | Boolean | convenção do Odoo (arquivar em vez de apagar) |
| `partner_id` | Many2one `res.partner`, obrigatório, `restrict` | Data Contract §2 (`contato comercial: Odoo`) + AC2 (vínculo a `res.partner`) |
| `company_id` / `currency_id` | Many2one `res.company` / `res.currency` | empresa e moeda do valor (convenção do Odoo) |
| `stage_id` | Many2one `crm.stage` | Data Contract §2 (`estágio: Odoo`) e §7.1 (funil); o won/lost do funil vem de `crm.stage.is_won` |
| `expected_revenue` | Monetary | Data Contract §2 (`valor: Odoo`) |
| `lost_reason_id` | Many2one `crm.lost.reason` | Data Contract §2 (`motivo de perda: Odoo`) |

O que o modelo **não** faz (de propósito, para não inventar contrato): não é o `crm.lead` — o
vínculo com o CRM é `crm.lead.tf_opportunity_id` (card E04-T02), do lado do lead; não carrega o
score de prioridade (o contrato o mapeia para `res.partner`/`crm.lead`). As views são do card
E06 e as ACLs/regras de acesso, do card E07 (abaixo).

## ACLs e segurança — carteira × tenant (card `TRE-W2-E07-T01`)

Runbook do card: **`docs/runbooks/odoo-acl-seguranca.md`**. Artefatos:
`security/transformativa_sales_ai_security.xml` e `security/ir.model.access.csv`.

| Peça | O que é |
|---|---|
| `group_tf_sales_ai_user` — "Sales AI: Vendedor (carteira)" | vê e opera as oportunidades da **própria carteira**: parceiros cujo vendedor (`res.partner.user_id`) é ele |
| `group_tf_sales_ai_manager` — "Sales AI: Gestor (tenant)" | herda o grupo do vendedor e alcança **todas as carteiras do tenant** (nenhuma do outro) |
| Regra global "tenant" | `[('company_id', 'in', company_ids)]` — isola por **companhia** do Odoo (o tenant da V1; o isolamento entre clientes é físico, um banco por cliente) |
| Regra de grupo "carteira" | `[('partner_id.user_id', '=', user.id)]` no grupo do vendedor |
| Regra de grupo "gestor" | `[(1, '=', 1)]` no grupo do gestor (a carteira inteira do tenant) |
| ACL do modelo | vendedor: ler/criar/escrever, **sem apagar**; gestor: ler/criar/escrever/apagar |

Nenhum campo novo é criado: o Data Contract V1.0 não tem dimensão de cliente, e as duas
dimensões usam o que o Odoo já tem (`res.company` para tenant, `res.partner.user_id` para
carteira). O módulo **não** concede `base.group_user` por conta própria e nenhum de seus grupos
alcança `group_system`/`group_erp_manager`: conceder aprovação humana nunca é coisa de máquina —
isso vive fora do Odoo (registro de aprovações + gate JEV).

## Manifesto (contrato do módulo)

| Campo | Valor | Porquê |
|---|---|---|
| `version` | `19.0.1.0.0` | esquema `<série do Odoo>.<major>.<minor>.<patch>`; a série acompanha a do Odoo em dev (`19.0`) e o teste do módulo reprova se divergir. Não sobe no E05 (decisão D6 do runbook do card: nada instalado em ambiente persistente e três cards da onda editam este manifesto em paralelo) |
| `license` | `LGPL-3` | licença padrão de módulo comunitário, coerente com a imagem oficial |
| `depends` | `base`, `crm` | `crm` é o alvo das customizações e o dono do funil (E04/E05/E06/E07) |
| `installable` | `True` | módulo instalável |
| `application` | `False` | é base de customização, não uma aplicação própria |

## Testes do Odoo

- `tests/test_modulo_base.py` (card E03, 6 testes, tag `post_install`/`-at_install`): versão do
  manifesto no banco, série == série do Odoo, dependências declaradas == gravadas e instaladas,
  âncoras `res.partner`/`crm.lead` e instalação assentada.
- `tests/test_oportunidade_canonica.py` (card E05, 9 testes, tag `post_install`): modelo e campos
  do contrato, criação (UUID do produtor preservado e UUID gerado quando ausente), leitura dos
  campos do funil, consulta por UUID e por parceiro, relação com `res.partner` (exclusão do
  parceiro recusada), unicidade, imutabilidade e recusa de UUID inválido.
- `tests/test_acl_seguranca.py` (card E07, 11 testes, tag `post_install`): grupos e privilégio,
  matriz da ACL, as três regras de registro (domínio/alcance/global), e os testes negativos com
  usuários de verdade (`with_user`): fail-closed sem o grupo, carteira alheia (busca e leitura
  por id), carteira vazia, outro tenant, gestor do tenant, criação em carteira alheia (recusada)
  e na própria (aceita), e a prova de que o módulo não promove ninguém (AC3).
- `tests/test_api_controlada.py` (card W3-E01-T01, 32 testes, tag `post_install`): a API controlada
  — os negativos de credencial (401), superfície (404/405), payload (400), campo/limite/modelo fora
  da declaração (422), ambiente e aprovação (503), escrita sem `idempotency_key` (422), `dry_run`
  que não escreve (e o `dry_run` na leitura, que descreve a consulta sem executá-la), a recusa
  `dry_run_nao_suportado` onde a política não aceita, upsert que cria uma vez e atualiza depois, a
  ACL do dono da chave valendo na leitura e a auditoria das duas linhas (`ok` e `recusado`).
- `tests/test_empresa_upsert.py` (card W3-E01-T02, 19 testes, tag `post_install`): a operação de
  escrita de negócio `empresa_upsert` — operação declarada nas capacidades (lendo a versão da
  política do **próprio artefato**, não de literal), criar/atualizar sem duplicar pela identidade
  canônica e pelos fortes (CNPJ, domínio, LinkedIn), `422 identificador_ausente` sem identificador,
  `409 valor_ambiguo` quando dois identificadores casam registros **diferentes** (e o mesmo registro
  **não** é ambiguidade), `422 campo_fixo_divergente` quando o chamador tenta decidir `is_company`,
  `idempotency_key` ausente/fora do formato, `dry_run` que descreve sem criar **nem** atualizar,
  UUID fora do formato (a recusa **não** deixa registro) e a auditoria da escrita (ids na trilha,
  sem payload e sem token).
- `tests/test_contato_upsert.py` (card W3-E01-T03, 19 testes, tag `post_install`): a operação de
  escrita de negócio `contato_upsert` — operação declarada nas capacidades (lendo a versão da
  política do **próprio artefato**), criar/atualizar sem duplicar pela identidade declarada
  (`email`, o identificador natural dos dois lados), `422 identificador_ausente` sem o valor de
  identidade, `409 valor_ambiguo` quando mais de um parceiro tem o mesmo e-mail (nada escrito nem
  alterado), `422 campo_fixo_divergente` quando o chamador tenta tornar o contato empresa,
  `422 campo_nao_declarado` para campo de empresa (`tf_cnpj`) e para campo de compliance/opt-out
  (escopo do PostgreSQL, contrato §9), `identificador` escalar **recusado** (nunca descartado em
  silêncio), `idempotency_key` ausente/fora do formato, `dry_run` que descreve sem criar **nem**
  atualizar, e a auditoria da escrita (ids na trilha, **sem** nome e **sem** e-mail).
- `tests/test_oportunidade_upsert.py` (card W3-E01-T04, 27 testes, tag `post_install`): a operação
  de escrita de negócio — declarada na política em vigor (lida do artefato), recusas nomeadas de
  credencial/identidade/chave/payload, upsert que cria uma vez e atualiza depois (contagem por
  identidade), atualização parcial, identidade pelo UUID (nunca pelo nome), fronteira de dono do
  Odoo na recusa **e** no registro (`stage_id`/`expected_revenue`/`probability` etc. comparados
  antes/depois), `dry_run` que descreve sem escrever, trilha sem payload, guarda de ambiente do
  ADR-005 na escrita (503/503/200) e o vínculo/dono do lead medidos.

O aceite de quatro passos (instalação em banco limpo → teste do Odoo → desinstalação →
reinstalação) roda por `scripts/odoo/verificar-modulo-odoo.sh`, com provas negativas em
`--prova-de-dente`.

O aceite das ACLs roda por **`scripts/odoo/verificar-acl-modulo.sh`** (51 itens: instalação,
suite do Odoo, as regras lidas no banco e a prova negativa independente
`scripts/odoo/provar_acl_modulo.py`), com as duas provas negativas do próprio verificador em
`--prova-de-dente`.
## Conteúdo das customizações (a partir de `TRE-W2-E04-T01`)

O "estado neste card" acima descreve o **card base** (`TRE-W2-E03-T01`): o módulo nasceu vazio de
propósito. O conteúdo entra por card, cada um no seu arquivo — o que já existe:

| Card | Arquivo | O que entra |
|---|---|---|
| `TRE-W2-E04-T01` | `models/res_partner.py` | `tf_cnpj`, `tf_domain`, `tf_linkedin_url` (identificadores fortes do contrato §5, os três **indexados**), `tf_company_id` (UUID canônico de `organizations.id`, com a forma do UUID conferida) e `tf_priority_score` (contrato §8) — runbook `docs/runbooks/res-partner-campos-dedup.md` |
| `TRE-W2-E04-T02` | `models/crm_lead.py` | campos de rastreio de `crm.lead` — runbook `docs/runbooks/odoo-crm-lead-sales-ai.md` |
| `TRE-W2-E05-T01` | `models/tf_process_opportunity.py` | modelo canônico `tf.process.opportunity` — runbook `docs/runbooks/odoo-oportunidade-canonica.md` |
| `TRE-W2-E06-T01` | `views/*.xml` | as views do Sales AI — runbook `docs/runbooks/odoo-views-sales-ai.md` |
| `TRE-W3-E01-T01` | `api/` e `controllers/api_controlada.py` | a API controlada (`POST /tf/api/v1/<operacao>`) — runbook `docs/runbooks/odoo-api-controlada.md` |
| `TRE-W3-E01-T02` | `api/politica_api.json` (operação `empresa_upsert`) + `tests/test_empresa_upsert.py` | a primeira **escrita de negócio** da porta única: upsert de empresa em `res.partner` por identidade declarada — runbook `docs/runbooks/odoo-empresa-upsert.md` |

**Ponto de contato entre cards paralelos (hotspot declarado):** `__init__.py` (uma vez),
`models/__init__.py`, `tests/__init__.py` e este README. Cada card acrescenta **uma linha** nesses
arquivos e o seu próprio módulo de modelo/teste; a integração junta as linhas, não reescreve os
arquivos.

Testes deste conteúdo: `tests/test_res_partner_dedup.py` (7 testes, tag `post_install`), com o
aceite item a item em `scripts/odoo/verificar-res-partner.sh` e a conferência de não divergência com
o Data Contract V1.0 em `scripts/odoo/conferir_res_partner_no_contrato.py`.
## Campos de rastreio em `crm.lead` (`TRE-W2-E04-T02`)

`models/crm_lead.py` acrescenta a `crm.lead` os **13 campos de rastreio** do Sales AI: os dois
que o Data Contract V1.0 §3 nomeia (`tf_opportunity_id`, `tf_priority_score`) e 11 espelhos de
artefatos do contrato (score model §8, vocabulário `next_best_action` §7, correlação/
idempotência §3 e trilha de sincronização/eventos §6). Os campos entram **aditivos** — nenhum
campo padrão do `crm.lead` é alterado —, com `tracking=True` e índice só nos três campos de
busca por identidade/correlação. Inventário, proveniência item a item e lacunas declaradas:
`docs/runbooks/odoo-crm-lead-sales-ai.md` §1–§2.

- **Testes do Odoo:** `tests/test_crm_lead_rastreio.py` (7 testes, tag `post_install`).
- **Confronto módulo × contrato congelado:** `python3 scripts/odoo/conferir_crm_lead_no_contrato.py`.
- **Aceite (6 passos, com rollback medido):** `bash scripts/odoo/verificar-crm-lead-odoo.sh`
  (na VPS, com `TRE_MODULO_DIR` do card); provas negativas em `--prova-de-dente`.

## Views do Sales AI (`TRE-W2-E06-T01`)

Runbook do card: **`docs/runbooks/odoo-views-sales-ai.md`**. Arquivos: `views/` (três deles) e a
lista `data` do manifesto.

| View | Modelo | O que mostra |
|---|---|---|
| `view_tf_process_opportunity_list` | `tf.process.opportunity` | lista da oportunidade canônica (`name`, `partner_id`, `stage_id`, `expected_revenue`, `company_id`) |
| `view_tf_process_opportunity_form` | `tf.process.opportunity` | formulário da oportunidade (os da lista + `currency_id`, `lost_reason_id`, `tf_uuid` somente leitura) |
| `view_tf_process_opportunity_search` | `tf.process.opportunity` | busca — o `tf_uuid` é caminho de busca declarado (contrato §3) |
| `view_partner_form_tf_sales_ai` | `res.partner` | herda `base.view_partner_form` e acrescenta a seção **"Sales AI"** com os 5 campos `tf_*` do parceiro |
| `view_crm_lead_form_tf_sales_ai` | `crm.lead` | herda `crm.crm_lead_view_form` e acrescenta a seção **"Sales AI"** com os 13 campos `tf_*` do lead |

A ação `action_tf_process_opportunity` (`view_mode = list,form`) e o menu **Sales AI →
Oportunidades**, pendurado no menu raiz do CRM, fecham o AC3 (abertura da lista e do formulário).

Recorte por perfil (AC2): o menu e as duas seções "Sales AI" estão presos ao grupo
`group_tf_sales_ai_user`. O recorte vai no **nó da arch** (a página da seção) e no menu, não no
registro da view herdada — no Odoo 19 uma view herdada **não pode** carregar `groups` no registro
(`ParseError: Inherited view cannot have 'groups' defined on the record`); o detalhe e as demais
armadilhas medidas estão no runbook §6. `company_id` e `currency_id` seguem o convencional do
Odoo (grupos padrão `group_multi_company`/`group_multi_currency`), como no próprio `crm.lead`.

- **Testes do Odoo:** `tests/test_views_sales_ai.py` (10 testes, tag `post_install`) — declaração
  das views/ação/menus, campos do contrato na lista/formulário/busca, views herdadas, abertura pelo
  membro, seção visível ao membro, e os negativos do restrito (menu, seção do parceiro/lead e a
  entidade canônica com `AccessError`).
- **Aceite (6 passos, com rollback medido):** `bash scripts/odoo/verificar-views-sales-ai.sh`
  (na VPS, com `TRE_MODULO_DIR` do card): 83 itens — instalação em banco limpo, suite do Odoo
  (`0 failed of 50 tests`), leitura das views/grupos/menu **no banco**, a prova independente
  `scripts/odoo/provar_views_sales_ai.py` (21 itens, com três usuários que diferem só pelo grupo
  do módulo) e a desinstalação pelo ORM. Provas negativas em `--prova-de-dente`.

## API controlada — a porta única do Odoo para a integração (`TRE-W3-E01-T01`)

Runbook do card: **`docs/runbooks/odoo-api-controlada.md`**. Arquivos: `api/politica_api.json`
(a declaração), `api/motor.py` (a decisão) e `controllers/api_controlada.py` (a rota).

```
POST /tf/api/v1/<operacao>     Authorization: Bearer <chave de API do Odoo>
```

| Peça | O que é |
|---|---|
| `api/politica_api.json` | A **fonte da verdade**: operação a operação, o modelo alvo, os campos permitidos, o teto de registros e a exigência de `idempotency_key`. Mora dentro do módulo, logo vai versionada no repo **e** no artefato publicado |
| `api/motor.py` | Valida a chamada contra a política e monta o plano — **sem importar `odoo`**, para a decisão ser exercitável sem subir Odoo (`scripts/odoo/testar_motor_api.py`) |
| `controllers/api_controlada.py` | UMA rota, UM verbo: não há rota genérica de "execute qualquer modelo/método/campo" (doc 02 §3). Executa o plano **pelo ORM** (as ACLs do dono da chave valem — não é `sudo`) e grava uma linha `TF_API_AUDIT` por chamada |

Operações declaradas nesta versão da política (**a leitura do E01-T01 e as escritas de negócio dos
cards `TRE-W3-E01-T02..T05`, que entram uma a uma na MESMA política, com a `versao` subindo**):

| Operação | O que faz |
|---|---|
| `sistema_capacidades` | Sonda de saúde do consumidor: devolve a versão da política e as operações declaradas, sem tocar modelo de negócio |
| `crm_registros_ler` | Leitura controlada de `res.partner` (campos `tf_*` do E04-T01) e `crm.lead` (rastreio do E04-T02): só campos declarados, só filtros declarados, com teto |
| `empresa_upsert` | **Escrita de negócio** (`TRE-W3-E01-T02`): upsert do parceiro-**empresa** em `res.partner` (`name`, `tf_company_id`, `tf_cnpj`, `tf_domain`, `tf_linkedin_url`, `tf_priority_score`), identidade declarada em `campos_de_identidade` (canônico → CNPJ → domínio → LinkedIn), `is_company` como **valor fixo** declarado e recusa `409 valor_ambiguo` quando os identificadores do pedido casam mais de um registro — runbook `docs/runbooks/odoo-empresa-upsert.md` |
| `contato_upsert` | **Escrita de negócio** (`TRE-W3-E01-T03`): upsert do parceiro-**pessoa** (contato comercial) em `res.partner` (`name`, `email`, `is_company`, `function`, `phone`), identidade declarada em `campos_de_identidade` (**`email`** — o identificador natural que existe nos dois lados e é indexado pelo contrato §4), `is_company` como **valor fixo** declarado (`false`) e `409 valor_ambiguo` quando mais de um parceiro tem o mesmo e-mail. Os campos de opt-out/`legal_basis`/`preferred_channel` são do PostgreSQL (contrato §9) e **não** existem neste espelho: enviá-los é recusa nomeada — runbook `docs/runbooks/odoo-contato-upsert.md` |
| `oportunidade_upsert` | Escrita de negócio: espelha a oportunidade canônica em `crm.lead` — cria na primeira chamada de um UUID e **atualiza** nas seguintes, sem duplicar; identidade por `tf_opportunity_id`; **não** escreve campo de dono do Odoo (`TRE-W3-E01-T04`) |

Guarda de ambiente (ADR-005): sem `ir.config_parameter` `tf.api.ambiente` **declarado** e presente
em `ambientes_permitidos`, a API recusa tudo (503); `homologacao`/`producao` exigem aprovação humana
registrada (`tf.api.aprovacao`, `card=...,aprovador=...,validade=AAAA-MM-DD`). A política desta
versão permite `dev` e só.

- **Testes do Odoo:** `tests/test_api_controlada.py` (32 testes, tag `post_install`) — inclui os
  negativos de credencial (401), superfície (404/405), campo/limite/modelo fora da declaração (422),
  ambiente e aprovação (503), escrita sem `idempotency_key` (422), `dry_run` que não escreve,
  upsert que cria uma vez e atualiza depois, e a auditoria das duas linhas.
- **Aceite:** `bash scripts/odoo/verificar-api-controlada.sh` (na VPS) — suíte pura do motor,
  instalação em banco limpo, suíte do Odoo, **servidor HTTP real com `curl` de fora do processo**
  (chave gerada na hora, arquivo 600, nunca em `ps`), auditoria lida do log do servidor, greps de
  contrato e limpeza com dev/homolog/prod medidos. Provas negativas em `--prova-de-dente` (3
  mutações, cada uma **tem** de reprovar, com guarda externa do artefato por sha256).
- **Aceite da operação de escrita (`TRE-W3-E01-T02`):** `bash scripts/odoo/verificar-empresa-upsert.sh`
  (na VPS, script próprio — o aceite do E01-T01 **não** foi ampliado) — 104 itens: suíte pura do
  motor, instalação em banco limpo, `101 testes` do Odoo sem falha, HTTP real por `curl` (criar,
  atualizar, identidade pelos fortes, `409` ambíguo com o banco conferido antes/depois, dry-run que
  não escreve, `503` fora do ambiente medido com servidor novo, `422` do valor fixo), auditoria lida
  do log (`15 linhas para 15 chamadas autenticadas`) e greps de contrato. `--prova-de-dente` com 3
  mutações **mais 2 controles do próprio harness** (sub-run que reprova por ambiente não conta como
  dente; mutação inócua é reportada como `mutacao sem dente`).
- **Aceite da operação de escrita de contato (`TRE-W3-E01-T03`):**
  `bash scripts/odoo/verificar-contato-upsert.sh` (na VPS, script próprio — o aceite do E01-T01
  **não** foi ampliado) — 110 itens: suíte pura do motor (`90 itens`), instalação em banco limpo,
  `120 testes` do Odoo sem falha (os 19 deste card + 101 dos anteriores, sem regressão), HTTP real
  por `curl` de fora do processo (criar → atualizar no mesmo registro com `1` contato após 3
  chamadas, `409 valor_ambiguo` sem escrever **nem** alterar com os dois registros do fixture lidos
  de volta pelo caminho declarado, `422` de identidade ausente / valor fixo divergente / campo de
  empresa / campo de compliance / chave, **`400` do `identificador` escalar** — o parâmetro que antes
  era descartado em silêncio, agora recusado por nome, dry-run que não escreve, `503` fora do
  ambiente), auditoria lida do log do servidor (`15 linhas para 15 chamadas autenticadas`, sem token
  e **sem nenhum e-mail do payload**) e greps de contrato. `--prova-de-dente` com 3 mutações (política
  sem a operação, controlador sem o portão de ambiguidade, motor sem aplicar o valor fixo) **mais 2
  controles do próprio harness**.
## Operação de escrita de negócio `oportunidade_upsert` (`TRE-W3-E01-T04`)

Runbook do card: **`docs/runbooks/odoo-oportunidade-upsert.md`**. A operação entra por
**declaração** na política (`api/politica_api.json` sobe de `1.0.0` para `1.1.0`) — nenhuma linha de
controlador foi necessária: a receita do §9 do runbook da API controlada se confirmou.

```http
POST /tf/api/v1/oportunidade_upsert        Authorization: Bearer <chave de API>
{"idempotency_key": "tre-...", "dry_run": false,
 "parametros": {"valores": {"name": "...", "tf_opportunity_id": "<uuid>", "tf_priority_score": 82.5}}}
```

| Decisão | Porquê |
|---|---|
| identidade é o **UUID canônico** `tf_opportunity_id` (contrato §3) | nome não é identidade: dois leads homônimos continuam dois registros (medido) |
| **fronteira de dono** (contrato §2): `stage_id`, `expected_revenue`, `probability`, `date_deadline`, `date_closed` **não** são escrevíveis | o funil é do time comercial; a API move o conhecimento sobre a oportunidade, nunca a posição dela nem o valor negociado. Campo de dono enviado = 422 `campo_nao_declarado`, nunca silêncio |
| `tf_priority_tier` também não é escrevível | é `compute` de `tf_priority_score` (E04-T02) e acompanha o score sozinho |
| `name` é obrigatório e `partner_id` é declarado | `crm.lead.name` é `compute` no Odoo 19 (só preenche quando vazio): sem `name` no payload o lead nasceria com nome computado pelo Odoo — fora do contrato do espelho |
| o rastro (`tf_idempotency_key`, `tf_correlation_id`, `tf_last_sync_at`, `tf_last_event_type`) é **campo declarado**, escrito com o que o produtor manda | a chave do envelope vai para a trilha `TF_API_AUDIT`; quem garante não-duplicar é a identidade canônica (dedup por chave é o E02-T02) |

- **Testes do Odoo:** `tests/test_oportunidade_upsert.py` (27 testes, tag `post_install`).
- **Aceite:** `bash scripts/odoo/verificar-oportunidade-upsert.sh` (na VPS, **como root** — a dupla
  descartável exige `chown` para o uid do container): suíte pura do motor, instalação em banco
  limpo, suíte do Odoo com piso de testes, **servidor HTTP real com `curl` de fora do processo**
  (cria/atualiza/repete, recusas, `dry_run`), **leitura por SQL** no banco (um registro por UUID,
  espelho gravado, estágio/valor de dono intactos, dono do lead), auditoria lida do log do servidor,
  guarda de ambiente do ADR-005 na escrita (com o servidor reiniciado **depois** da troca feita pelo
  ORM) e limpeza com dev/homolog/prod medidos. Provas negativas em `--prova-de-dente` (3 mutações,
  cada uma **tem** de reprovar, com o item esperado conferido e harness **fail-closed**: prova que
  não mede nada reprova).
