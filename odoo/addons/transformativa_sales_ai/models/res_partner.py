# -*- coding: utf-8 -*-
"""Campos de identificacao e deduplicacao em `res.partner` — card TRE-W2-E04-T01.

O QUE ESTE ARQUIVO ENTREGA (e de onde a regra vem — nada e' inventado aqui):

1. **Identificadores fortes de deduplicacao de empresa** (`docs/data/DATA_CONTRACT_V1.md` §5,
   `docs/data/data_contract_v1.json` -> `dedup.strong`): CNPJ, dominio e LinkedIn Company URL.
   O contrato fixa o espelho operacional (`res.partner`) e a ordem de prioridade
   CNPJ -> dominio -> LinkedIn; por isso os tres campos nascem **indexados** (a busca de
   duplicidade e' por igualdade de identificador).

2. **IDs canonicos do contrato** (`DATA_CONTRACT_V1.md` §3 e `canonical_ids.odoo_map`):

   | Canonico            | Odoo                                                |
   |---------------------|-----------------------------------------------------|
   | `organizations.id`  | `res.partner.tf_company_id`                          |
   | score de prioridade | `res.partner.tf_priority_score` (e `crm.lead.…`)     |

   O ID de sistema externo e' **referencia, nunca identidade** (§3): `tf_company_id` guarda o
   UUID canonico (`organizations.id`) e nao substitui nada — o que ele faz e' ligar o parceiro
   do funil a empresa do `sales_intelligence`.

3. **Nome do campo** = prefixo `tf_` do contrato (`tf_company_id`, `tf_priority_score`) seguido
   do nome do identificador como o contrato o chama (`cnpj`, `domain`, `linkedin_url`). O
   conferidor `scripts/odoo/conferir_res_partner_no_contrato.py` compara estes nomes com o
   `data_contract_v1.json` congelado, item a item.

O QUE ESTE ARQUIVO **NAO** FAZ (lacunas declaradas, para nao inventar regra de negocio):

- **nao** normaliza o valor (digitos do CNPJ, dominio em minusculas): o contrato nao define
  regra de normalizacao e o motor de dedup da V1 (`scripts/dedup/deduplicar_organizacoes.py`)
  compara o valor do identificador como ele chega — normalizar aqui decidiria pelo W1 sem
  contrato que o sustente;
- **nao** valida CNPJ: pelo contrato (§5 + decisao D3 do runbook de dedup), CNPJ invalido e'
  **armazenado** e leva o par para a fila humana (`REVIEW_REQUIRED`), nunca merge automatico;
  recusar o dado no Odoo seria regra mais dura que o contrato;
- **nao** cria `UNIQUE` nem merge automatico: o contrato so' permite merge com
  `entity_match_confidence >= 0.95` e, abaixo disso, fila humana — unicidade no banco impediria
  exatamente o caso que o contrato manda *reportar* em vez de resolver (§5);
- **nao** cria view nem ACL: `TRE-W2-E06-T01` e `TRE-W2-E07-T01`.
"""
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Formato do ID canonico do contrato (§3): `id UUID`. Flexivel na versao do UUID de proposito —
# o produtor do fato gera v4, mas o espelho nao recusa um UUID canonico legitimo de outra versao.
FORMATO_UUID = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # --- identificadores fortes de deduplicacao (contrato §5 / dedup.strong) ----------------
    tf_cnpj = fields.Char(
        string='CNPJ',
        index=True,
        copy=False,
        help='Identificador forte de deduplicacao (1o na ordem do contrato). '
             'Espelho do `organizations.cnpj`. Guardado como recebido — a normalizacao nao e'
             ' definida pelo contrato V1.',
    )
    tf_domain = fields.Char(
        string='Dominio',
        index=True,
        copy=False,
        help='Identificador forte de deduplicacao (2o na ordem do contrato). '
             'Espelho do `organizations.domain`.',
    )
    tf_linkedin_url = fields.Char(
        string='LinkedIn (empresa)',
        index=True,
        copy=False,
        help='Identificador forte de deduplicacao (3o na ordem do contrato). '
             'Espelho do `organizations.linkedin_url` (LinkedIn Company URL).',
    )

    # --- IDs canonicos (contrato §3 / canonical_ids.odoo_map) -------------------------------
    tf_company_id = fields.Char(
        string='UUID canonico (organizations.id)',
        index=True,
        copy=False,
        help='ID canonico Transformativa da empresa pesquisada (`organizations.id`, UUID). '
             'ID de sistema externo e referencia, nunca identidade: este campo e que liga o '
             'parceiro do funil a empresa do sales_intelligence.',
    )
    tf_priority_score = fields.Float(
        string='Priority score',
        copy=False,
        help='Score de prioridade (contrato §8). Espelho de `scores` (score_type=PRIORITY, '
             'versionado). O calculo e a versao do score ficam no sales_intelligence; aqui '
             'vive so o valor corrente do funil.',
    )

    @api.constrains('tf_company_id')
    def _check_tf_company_id_uuid(self):
        """`tf_company_id` so' aceita UUID — e' o ID canonico do contrato (§3), nao texto livre."""
        for parceiro in self:
            valor = (parceiro.tf_company_id or '').strip()
            if valor and not FORMATO_UUID.match(valor):
                raise ValidationError(
                    'tf_company_id deve ser o UUID canonico (organizations.id) do Data '
                    'Contract V1.0; recebido: %r' % parceiro.tf_company_id
                )
