# -*- coding: utf-8 -*-
"""Detector do fato ATIVIDADE CONCLUIDA em `mail.activity` — card TRE-W3-E03-T01.

O EVENTO: `ACTIVITY_COMPLETED` (contrato §6 `events.odoo_to_pg`).

ONDE O FATO ACONTECE: em `mail.activity._action_done()` — o metodo privado que o Odoo chama quando
a atividade e' marcada como concluida (o `action_feedback`/`action_done` da interface passa por
ele). Medido no Odoo 19 antes de escrever este gancho (`odoo shell` sobre o modulo instalado): o
`_action_done` POSTA a mensagem e depois **arquiva/remove** a atividade. Consequencia de desenho:
o retrato do fato tem de ser capturado ANTES do `super()` — depois dele o registro pode nao existir
mais e o evento sairia sem os dados do fato.

O QUE ESTE ARQUIVO **NAO** FAZ (declarado):

  * nao emite evento de atividade CRIADA, REAGENDADA ou de prazo alterado: o contrato publica
    `ACTIVITY_COMPLETED` — e so';
  * nao conclui atividade por conta propria nem altera o comportamento do `_action_done` do Odoo
    (o gancho observa; a conclusao e' do Odoo);
  * nao inventa identidade canonica: a organizacao/oportunidade vem do documento ANCORADO
    (`res.partner.tf_company_id` / `crm.lead.tf_opportunity_id`); documento sem UUID canonico gera
    evento com `entidade_canonica_tipo = vazia`, declarado no payload.
"""
from odoo import fields, models


class MailActivityEventos(models.Model):
    """`mail.activity` -> fila de eventos Odoo->PostgreSQL (o evento ACTIVITY_COMPLETED)."""

    _inherit = 'mail.activity'

    def _tf_retrato_do_fato(self):
        """Retrato da atividade e do documento ancorado — lido ANTES do `_action_done`."""
        self.ensure_one()
        organizacao = False
        oportunidade = False
        documento_existe = False
        if self.res_model and self.res_id:
            documento = self.env[self.res_model].browse(self.res_id).exists()
            documento_existe = bool(documento)
            if documento and self.res_model == 'crm.lead':
                oportunidade = documento.tf_opportunity_id or False
                if documento.partner_id:
                    organizacao = documento.partner_id.tf_company_id or False
            elif documento and self.res_model == 'res.partner':
                organizacao = documento.tf_company_id or False
                oportunidade = False
        return {
            'atividade_id': self.id,
            'tipo': self.activity_type_id.name or '',
            'tipo_id': self.activity_type_id.id or 0,
            'resumo': self.summary or '',
            'prazo': fields.Date.to_string(self.date_deadline) if self.date_deadline else '',
            'documento_modelo': self.res_model or '',
            'documento_id': self.res_id or 0,
            'documento_existe': documento_existe,
            'usuario': self.user_id.name or '',
            'correlacao': self.tf_correlation_id or False,
            'organizacao_id': organizacao,
            'oportunidade_id': oportunidade,
        }

    def _action_done(self, feedback=False, attachment_ids=None):
        retratos = {atividade.id: atividade._tf_retrato_do_fato() for atividade in self}
        resultado = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)
        fila = self.env['tf.evento.outbox']
        ocorrido = fields.Datetime.now()
        for atividade_id, retrato in retratos.items():
            identidade = retrato['oportunidade_id'] or retrato['organizacao_id'] or False
            if retrato['oportunidade_id']:
                tipo_identidade = 'oportunidade'
            elif retrato['organizacao_id']:
                tipo_identidade = 'organizacao'
            else:
                tipo_identidade = 'vazia'
            fila._tf_emitir(
                'ACTIVITY_COMPLETED',
                'mail.activity',
                atividade_id,
                {
                    'atividade_id': atividade_id,
                    'tipo': retrato['tipo'],
                    'tipo_id': retrato['tipo_id'],
                    'resumo': retrato['resumo'],
                    'prazo': retrato['prazo'],
                    'documento': {
                        'modelo': retrato['documento_modelo'],
                        'id': retrato['documento_id'],
                    },
                    'usuario_responsavel': retrato['usuario'],
                    'com_feedback': bool(feedback),
                    'organizacao_id': retrato['organizacao_id'] or False,
                    'oportunidade_id': retrato['oportunidade_id'] or False,
                },
                occurred_at=ocorrido,
                correlation_id=retrato['correlacao']
                or 'odoo-ui:mail.activity:%s:%s' % (atividade_id, fields.Datetime.to_string(ocorrido)),
                entidade_canonica_id=identidade,
                entidade_canonica_tipo=tipo_identidade,
            )
        return resultado
