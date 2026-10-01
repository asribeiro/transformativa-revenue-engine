# `transformativa_sales_ai` — base do Sales AI da Transformativa no Odoo

Módulo Odoo Community que é a **base das customizações e da integração** do Sales AI da
Transformativa. Card de origem: **`TRE-W2-E03-T01`** (`t_c536ce86`), onda W2, épico E03.
Runbook de execução, aceite medido e rollback: **`docs/runbooks/odoo-modulo-sales-ai.md`**.

## Estado neste card

O módulo nasce **de propósito sem modelo, view ou ACL**: este card entrega o empacotamento
(manifesto, versão, dependências) e o teste do Odoo. As customizações chegam penduradas
nesta base:

| O que entra | Card |
|---|---|
| Campos de dedup em `res.partner` (CNPJ, domínio, LinkedIn) | `TRE-W2-E04-T01` |
| Campos de rastreio em `crm.lead` (13 previstos no contrato) | `TRE-W2-E04-T02` |
| Modelo canônico `tf.process.opportunity` | `TRE-W2-E05-T01` |
| Views do Sales AI | `TRE-W2-E06-T01` |
| ACLs / segurança (carteira × tenant) | `TRE-W2-E07-T01` |
| API controlada | `TRE-W3-E01-T01` |

Quando a primeira card de customização entrar, ela acrescenta `from . import models` em
`__init__.py` e cria o pacote `models/`.

## Manifesto (contrato do módulo)

| Campo | Valor | Porquê |
|---|---|---|
| `version` | `19.0.1.0.0` | esquema `<série do Odoo>.<major>.<minor>.<patch>`; a série acompanha a do Odoo em dev (`19.0`) e o teste do módulo reprova se divergir |
| `license` | `LGPL-3` | licença padrão de módulo comunitário, coerente com a imagem oficial |
| `depends` | `base`, `crm` | `crm` é o alvo das customizações (E04/E05/E06/E07) — declarado desde a base para a dependência ser explícita e resolvível no dev |
| `installable` | `True` | módulo instalável |
| `application` | `False` | é base de customização, não uma aplicação própria |

## Teste do Odoo

`tests/test_modulo_base.py` (tag `post_install`, `-at_install`) prova: módulo instalado com a
versão do manifesto; série da versão == série do Odoo; dependências declaradas == gravadas e
todas instaladas; âncoras `res.partner`/`crm.lead` aceitando dado sintético; e instalação
assentada (nenhum módulo em `to install`/`to upgrade`/`to remove`).

O aceite de quatro passos (instalação em banco limpo → teste do Odoo → desinstalação →
reinstalação) roda por `scripts/odoo/verificar-modulo-odoo.sh`, com provas negativas em
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
