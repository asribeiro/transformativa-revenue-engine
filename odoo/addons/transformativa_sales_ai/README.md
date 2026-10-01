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
| ACLs / segurança (carteira × tenant) | `TRE-W2-E07-T01` | **neste card** |
| Campos de dedup em `res.partner` (CNPJ, domínio, LinkedIn) | `TRE-W2-E04-T01` | pendente |
| Campos de rastreio em `crm.lead` | `TRE-W2-E04-T02` | pendente |
| Views do Sales AI | `TRE-W2-E06-T01` | pendente |
| API controlada | `TRE-W3-E01-T01` | pendente |

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
| `TRE-W2-E04-T02` | `models/crm_lead.py` (previsto) | campos de rastreio de `crm.lead` |
| `TRE-W2-E05-T01` | `models/tf_process_opportunity.py` (previsto) | modelo canônico `tf.process.opportunity` |

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
