# -*- coding: utf-8 -*-
"""Detector dos fatos de FUNIL em `crm.lead` — card TRE-W3-E03-T01 (`t_85cb2838`).

O QUE ESTE ARQUIVO ENTREGA:

Os cinco eventos de funil do contrato (§6 `events.odoo_to_pg`) nascem de fatos que acontecem em
`crm.lead` — o dono do funil (contrato §2):

    STAGE_CHANGED          estagio mudou                       (estagio anterior -> novo)
    OPPORTUNITY_WON        o lead passou a "ganho"             (`won_status` virou won)
    OPPORTUNITY_LOST       o lead passou a "perdido"           (`won_status` virou lost)
    DEAL_VALUE_CHANGED     `expected_revenue` mudou            (valor anterior -> novo)
    LOSS_REASON_RECORDED   `lost_reason_id` mudou e ficou set  (motivo anterior -> novo)

DETECCAO POR FATO OBSERVADO, NAO POR VARREDURA: o gancho e' o `write` do ORM, no MESMO ciclo do
fato (o evento entra na fila na mesma transacao). Nao ha' cron varrendo o banco atras de mudanca:
o doc 02 §3 reserva o polling para reconciliacao, nunca para a integracao principal.

MULTIPLICIDADE DECLARADA (um `write` pode produzir mais de um evento — e isso e' intencional):

  * `stage_id` mudou            -> STAGE_CHANGED sempre; se o novo estagio e' de ganho, TAMBEM
                                   OPPORTUNITY_WON (sao dois fatos distintos: "mudou de estagio" e
                                   "foi ganho");
  * `active` virou falso        -> OPPORTUNITY_LOST (o `won_status` computado passa a lost);
  * `lost_reason_id` preenchido -> LOSS_REASON_RECORDED (motivo informado e' um fato proprio);
  * `expected_revenue` mudou    -> DEAL_VALUE_CHANGED.

O QUE ESTE ARQUIVO **NAO** FAZ (declarado):

  * nao emite evento em `create`: nenhum dos sete eventos do contrato e' "lead criado" — inventar
    um `LEAD_CREATED` seria evento fora da lista fechada;
  * nao emite em mudanca de campo que nao seja fato do contrato (nome, descricao, tags...): o
    detector nao e' "qualquer coisa mudou" — e' o que o contrato publica;
  * nao decide valor fixo, identidade nem permissao de escrita: quem escreve em `crm.lead` pela
    porta (API controlada) e' o motor/controlador do TRE-W3-E01-T01; aqui o que se observa e' o
    RESULTADO no registro, depois do `super().write()`;
  * nao inventa UUID canonico: sem `tf_opportunity_id`/`tf_company_id` preenchido, o evento sai com
    `entidade_canonica_tipo = vazia` e a identidade Odoo declarada no payload (lacuna visivel).
"""
from odoo import fields, models

# Campos cujo `write` pode produzir fato do contrato. Mudanca em qualquer outro campo nao abre o
# gancho: e' o que mantem a deteccao presa ao contrato em vez de virar espelho de tudo.
CAMPOS_DO_FATO = ('stage_id', 'expected_revenue', 'lost_reason_id', 'active', 'probability')


class CrmLeadEventos(models.Model):
    """`crm.lead` -> fila de eventos Odoo->PostgreSQL (cinco dos sete eventos do contrato)."""

    _inherit = 'crm.lead'

    def _tf_retrato_do_fato(self):
        """Retrato dos campos que decidem a emissao (lido ANTES e DEPOIS do `write`)."""
        self.ensure_one()
        parceiro = self.partner_id
        return {
            'stage_id': self.stage_id.id or 0,
            'stage_nome': self.stage_id.name or '',
            'stage_ganho': bool(self.stage_id.is_won),
            'expected_revenue': float(self.expected_revenue or 0.0),
            'lost_reason_id': self.lost_reason_id.id or 0,
            'lost_reason_nome': self.lost_reason_id.name or '',
            'active': bool(self.active),
            'won_status': self.won_status or 'pending',
            'probability': float(self.probability or 0.0),
            'write_date': fields.Datetime.to_string(self.write_date) if self.write_date else '',
            'oportunidade_id': self.tf_opportunity_id or False,
            'correlacao': self.tf_correlation_id or False,
            'organizacao_id': (parceiro.tf_company_id or False) if parceiro else False,
            'parceiro_id': parceiro.id or 0,
            'moeda': self.company_currency.name or '',
        }

    def write(self, vals):
        """Observa o fato no mesmo ciclo: retrato antes, `super()`, emissao depois."""
        interessam = [campo for campo in CAMPOS_DO_FATO if campo in vals]
        antes = {}
        if interessam:
            for lead in self:
                antes[lead.id] = lead._tf_retrato_do_fato()
        resultado = super().write(vals)
        if interessam:
            for lead in self:
                retrato_anterior = antes.get(lead.id)
                if retrato_anterior is None:
                    continue
                lead._tf_emitir_eventos_do_fato(retrato_anterior, lead._tf_retrato_do_fato())
        return resultado

    def _tf_emitir_eventos_do_fato(self, antes, depois):
        self.ensure_one()
        fila = self.env['tf.evento.outbox']
        ocorrido = fields.Datetime.now()
        identidade = depois['oportunidade_id'] or depois['organizacao_id'] or False
        if depois['oportunidade_id']:
            tipo_identidade = 'oportunidade'
        elif depois['organizacao_id']:
            tipo_identidade = 'organizacao'
        else:
            tipo_identidade = 'vazia'
        corr = depois['correlacao'] or 'odoo-ui:crm.lead:%s:%s' % (self.id, depois['write_date'])
        comum = {
            'modelo_origem': 'crm.lead',
            'lead_id': self.id,
            'oportunidade_id': depois['oportunidade_id'] or False,
            'organizacao_id': depois['organizacao_id'] or False,
            'parceiro_id': depois['parceiro_id'] or 0,
            'estagio': depois['stage_nome'],
        }
        argumentos = {
            'occurred_at': ocorrido,
            'correlation_id': corr,
            'entidade_canonica_id': identidade,
            'entidade_canonica_tipo': tipo_identidade,
        }

        if antes['stage_id'] != depois['stage_id']:
            fila._tf_emitir(
                'STAGE_CHANGED',
                'crm.lead',
                self.id,
                dict(
                    comum,
                    estagio_anterior={'id': antes['stage_id'], 'nome': antes['stage_nome']},
                    estagio_novo={'id': depois['stage_id'], 'nome': depois['stage_nome']},
                    probabilidade=depois['probability'],
                ),
                **argumentos
            )

        if depois['won_status'] == 'won' and antes['won_status'] != 'won':
            fila._tf_emitir(
                'OPPORTUNITY_WON',
                'crm.lead',
                self.id,
                dict(
                    comum,
                    valor=depois['expected_revenue'],
                    moeda=depois['moeda'],
                    probabilidade=depois['probability'],
                ),
                **argumentos
            )

        if depois['won_status'] == 'lost' and antes['won_status'] != 'lost':
            fila._tf_emitir(
                'OPPORTUNITY_LOST',
                'crm.lead',
                self.id,
                dict(
                    comum,
                    valor=depois['expected_revenue'],
                    moeda=depois['moeda'],
                    motivo=depois['lost_reason_nome'] or False,
                ),
                **argumentos
            )

        if depois['lost_reason_id'] and antes['lost_reason_id'] != depois['lost_reason_id']:
            fila._tf_emitir(
                'LOSS_REASON_RECORDED',
                'crm.lead',
                self.id,
                dict(
                    comum,
                    motivo={
                        'id': depois['lost_reason_id'],
                        'nome': depois['lost_reason_nome'],
                    },
                    motivo_anterior={
                        'id': antes['lost_reason_id'],
                        'nome': antes['lost_reason_nome'],
                    },
                ),
                **argumentos
            )

        if abs(antes['expected_revenue'] - depois['expected_revenue']) > 1e-9:
            fila._tf_emitir(
                'DEAL_VALUE_CHANGED',
                'crm.lead',
                self.id,
                dict(
                    comum,
                    valor_anterior=antes['expected_revenue'],
                    valor_novo=depois['expected_revenue'],
                    moeda=depois['moeda'],
                ),
                **argumentos
            )
