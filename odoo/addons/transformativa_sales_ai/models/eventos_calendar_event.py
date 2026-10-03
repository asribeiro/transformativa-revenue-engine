# -*- coding: utf-8 -*-
"""Detector do fato REUNIAO CRIADA em `calendar.event` — card TRE-W3-E03-T01.

O EVENTO: `MEETING_CREATED` (contrato §6 `events.odoo_to_pg`). O gancho e' o `create` do ORM: a
reuniao nasce em Odoo e o evento entra na fila na MESMA transacao.

O QUE ESTE ARQUIVO **NAO** FAZ (declarado):

  * nao emite evento de reuniao CANCELADA, REMARCADA ou concluida: o contrato publica
    `MEETING_CREATED` — e so' (os outros seriam eventos fora da lista fechada);
  * nao altera nem valida o `create` do Odoo (o gancho observa depois do `super()`);
  * nao inventa identidade canonica: a oportunidade vem de `calendar.event.opportunity_id`
    (`crm.lead`, dono do funil) e a organizacao do parceiro da oportunidade; sem elas o evento sai
    com `entidade_canonica_tipo = vazia`.
"""
from odoo import api, fields, models


class CalendarEventEventos(models.Model):
    """`calendar.event` -> fila de eventos Odoo->PostgreSQL (o evento MEETING_CREATED)."""

    _inherit = 'calendar.event'

    @api.model_create_multi
    def create(self, vals_list):
        reunioes = super().create(vals_list)
        for reuniao in reunioes:
            reuniao._tf_emitir_evento_de_reuniao()
        return reunioes

    def _tf_emitir_evento_de_reuniao(self):
        self.ensure_one()
        oportunidade = self.opportunity_id
        oportunidade_id = (oportunidade.tf_opportunity_id or False) if oportunidade else False
        organizacao_id = False
        if oportunidade and oportunidade.partner_id:
            organizacao_id = oportunidade.partner_id.tf_company_id or False
        if oportunidade_id:
            tipo_identidade = 'oportunidade'
        elif organizacao_id:
            tipo_identidade = 'organizacao'
        else:
            tipo_identidade = 'vazia'
        ocorrido = fields.Datetime.now()
        self.env['tf.evento.outbox']._tf_emitir(
            'MEETING_CREATED',
            'calendar.event',
            self.id,
            {
                'reuniao_id': self.id,
                'nome': self.name or '',
                'inicio': fields.Datetime.to_string(self.start) if self.start else False,
                'fim': fields.Datetime.to_string(self.stop) if self.stop else False,
                'dia_inteiro': bool(self.allday),
                'duracao_horas': float(self.duration or 0.0),
                'participantes': len(self.partner_ids),
                'oportunidade_id': oportunidade_id or False,
                'lead_id': oportunidade.id if oportunidade else 0,
                'organizacao_id': organizacao_id or False,
            },
            occurred_at=ocorrido,
            correlation_id='odoo-ui:calendar.event:%s:%s'
            % (self.id, fields.Datetime.to_string(ocorrido)),
            entidade_canonica_id=oportunidade_id or organizacao_id or False,
            entidade_canonica_tipo=tipo_identidade,
        )
