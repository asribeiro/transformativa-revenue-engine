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
