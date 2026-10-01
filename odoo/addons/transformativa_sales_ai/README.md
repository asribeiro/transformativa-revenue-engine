# `transformativa_sales_ai` — Sales AI da Transformativa no Odoo

Módulo Odoo Community que hospeda as **customizações e a integração** do Sales AI da
Transformativa. Card de origem da base: **`TRE-W2-E03-T01`** (`t_c536ce86`), onda W2, épico E03.
Runbook da base, aceite medido e rollback: **`docs/runbooks/odoo-modulo-sales-ai.md`**.

## Estado do módulo

O que já existe e quem entrega o quê:

| O que | Card | Estado |
|---|---|---|
| Base: manifesto, versão, dependências, empacotamento, teste do módulo | `TRE-W2-E03-T01` | entregue |
| Modelo canônico `tf.process.opportunity` (oportunidade canônica do lado Odoo) | `TRE-W2-E05-T01` | **neste card** |
| Campos de dedup em `res.partner` (CNPJ, domínio, LinkedIn) | `TRE-W2-E04-T01` | pendente |
| Campos de rastreio em `crm.lead` | `TRE-W2-E04-T02` | pendente |
| Views do Sales AI | `TRE-W2-E06-T01` | pendente |
| ACLs / segurança (carteira × tenant) | `TRE-W2-E07-T01` | pendente |
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
score de prioridade (o contrato o mapeia para `res.partner`/`crm.lead`); e não traz view nem
regra de acesso (cards E06 e E07).

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

O aceite de quatro passos (instalação em banco limpo → teste do Odoo → desinstalação →
reinstalação) roda por `scripts/odoo/verificar-modulo-odoo.sh`, com provas negativas em
`--prova-de-dente`.
