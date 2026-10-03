# -*- coding: utf-8 -*-
# ============================================================================
# tf.process.opportunity — a oportunidade canonica do lado Odoo.
#
# Card: TRE-W2-E05-T01 (`t_9c91ecce`). Runbook: docs/runbooks/odoo-oportunidade-canonica.md.
#
# O que este modelo E':
#   O lado Odoo do contrato de dados (docs/data/DATA_CONTRACT_V1.md). O contrato diz que a
#   oportunidade canonica vive no Odoo (`canonical_ids.opportunity_owner: Odoo`) e da ao Odoo
#   os fatos: contato comercial, estagio, valor, won/lost e motivo de perda (secao 2). Este
#   modelo e' essa entidade — nao ha tabela de oportunidade no PostgreSQL (secao 3).
#
# O que este modelo NAO E' (decisoes registradas no runbook §2, nenhuma delas inventada aqui):
#   * nao e' o `crm.lead`: o vinculo com o CRM e' `crm.lead.tf_opportunity_id` (card
#     TRE-W2-E04-T02), do lado do lead — o contrato mapeia a oportunidade canonica para esse
#     campo, e o campo aponta para a identidade canonica daqui (`tf_uuid`).
#   * nao carrega o score de prioridade: o contrato mapeia `priority score` para
#     `res.partner.tf_priority_score` / `crm.lead.tf_priority_score`, nao para a oportunidade.
#   * nao traz view nem regra de acesso: views sao TRE-W2-E06-T01 e ACLs sao TRE-W2-E07-T01.
# ============================================================================

import uuid

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class TfProcessOpportunity(models.Model):
    """Oportunidade canonica do Transformativa Revenue Engine (lado Odoo)."""

    _name = 'tf.process.opportunity'
    _description = 'Oportunidade canonica (Sales AI)'
    _order = 'id desc'

    # Identidade canonica (Data Contract V1.0 §3): `id UUID` e' a chave canonica em todo o
    # contrato e IDs de sistema externo nunca substituem o UUID canonico. No Odoo a PK e' o
    # `id` inteiro, entao o UUID canonico vive nesta coluna — e' por ele que o lado
    # PostgreSQL/n8n referencia a oportunidade (`recommendations.opportunity_id`, UUID sem FK).
    tf_uuid = fields.Char(
        string='UUID canonico',
        required=True,
        index=True,
        copy=False,
        default=lambda self: str(uuid.uuid4()),
        help='Chave canonica (UUID) da oportunidade. Chega do produtor do fato quando ele nasce '
             'fora do Odoo (ex.: OPPORTUNITY_RECOMMENDED) e e gerada aqui quando o fato nasce no '
             'proprio Odoo. Depois de gravada nao muda: ID canonico nao e editavel.',
    )

    # Identificacao da oportunidade. `name` e' o `_rec_name` do modelo (o que a lista e o
    # formulario mostram — TRE-W2-E06-T01) e o que o usuario humano reconhece no funil.
    name = fields.Char(string='Oportunidade', required=True, index=True)

    # Convencao do Odoo para arquivar em vez de apagar (mesma semantica de `crm.lead`).
    active = fields.Boolean(string='Ativo', default=True)

    # 'contato comercial: Odoo' (contrato §2) e o vinculo exigido pelo criterio de aceitacao:
    # toda oportunidade canonica pertence a um `res.partner`. `restrict` porque uma oportunidade
    # orfa nao e' oportunidade — o vinculo e' real, nao decorativo.
    partner_id = fields.Many2one(
        'res.partner',
        string='Parceiro',
        required=True,
        ondelete='restrict',
        index=True,
    )

    # Empresa/moeda de operacao (convencao do Odoo; o valor em dinheiro precisa de moeda).
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moeda',
        related='company_id.currency_id',
        store=True,
        readonly=True,
    )

    # 'estagio: Odoo' + funil do contrato §7.1. O estagio carrega o won/lost do funil: e'
    # `crm.stage.is_won` que define se a oportunidade esta ganha (mesma regra que o
    # `won_status` calculado de `crm.lead` usa no core).
    stage_id = fields.Many2one('crm.stage', string='Estagio', ondelete='restrict', index=True)

    # 'valor: Odoo' (contrato §2).
    expected_revenue = fields.Monetary(
        string='Valor esperado',
        currency_field='currency_id',
    )

    # 'motivo de perda: Odoo' (contrato §2).
    lost_reason_id = fields.Many2one('crm.lost.reason', string='Motivo de perda', ondelete='restrict')

    # O contrato exige a chave canonica unica por entidade (§3): duas oportunidades com o mesmo
    # UUID seriam a mesma oportunidade. API de constraint do Odoo 19 (`models.Constraint`).
    _tf_uuid_uniq = models.Constraint(
        'unique (tf_uuid)',
        'O UUID canonico da oportunidade e unico (Data Contract V1.0, secao 3).',
    )

    @api.model
    def _tf_normalizar_uuid(self, valor):
        """Devolve o UUID canonico em forma estavel (minusculo, com hifens) ou recusa.

        O contrato nao admite identidade por suposicao: valor ausente vira UUID v4 novo; valor
        presente que nao e' UUID e' RECUSADO (nunca 'consertado' em silencio).
        """
        if valor is None or str(valor).strip() == '':
            return str(uuid.uuid4())
        try:
            return str(uuid.UUID(str(valor).strip()))
        except (ValueError, AttributeError, TypeError):
            raise ValidationError(
                'O UUID canonico da oportunidade tem de ser um UUID valido '
                '(Data Contract V1.0, secao 3). Recebido: %r' % (valor,)
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['tf_uuid'] = self._tf_normalizar_uuid(vals.get('tf_uuid'))
        return super().create(vals_list)

    def write(self, vals):
        # ID canonico e' imutavel: sobrescrever a identidade da oportunidade trocaria o dono do
        # fato sem evento nenhum. Gravar o mesmo valor de novo e' inofensivo (idempotencia).
        if 'tf_uuid' in vals:
            novo = self._tf_normalizar_uuid(vals['tf_uuid'])
            for registro in self:
                if registro.tf_uuid != novo:
                    raise ValidationError(
                        'O UUID canonico da oportunidade nao pode ser alterado '
                        '(%s -> %s). O ID canonico nasce no produtor do fato e nao muda '
                        '(Data Contract V1.0, secao 3).' % (registro.tf_uuid, novo)
                    )
            vals = dict(vals, tf_uuid=novo)
        return super().write(vals)
