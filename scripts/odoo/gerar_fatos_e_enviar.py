# -*- coding: utf-8 -*-
"""Gera FATOS de negocio e opera a fila de eventos Odoo->PostgreSQL — card TRE-W3-E03-T01.

Rodado por `odoo shell` (passo do aceite; nao e' modulo):

    TRE_FASE=fatos   odoo shell -d <banco> --no-http < scripts/odoo/gerar_fatos_e_enviar.py
    TRE_FASE=enviar  ...   # entrega a fila pela porta unica e imprime o resumo
    TRE_FASE=replay  ...   # devolve os eventos SENT para a fila (reenvio do MESMO envelope)
    TRE_FASE=relatorio ... # imprime o estado da fila (uma linha por evento)

Por que por aqui (e nao por SQL): o que se mede e' o caminho de NEGOCIO do Odoo — os fatos nascem
pelo ORM (create/write/action), como nasceriam na interface, e e' isso que dispara os detectores.
Marcadores de saida (lidos pelo aceite): `TF_FATOS ...`, `TF_RESUMO {...}`, `TF_EVENTO <linha>`,
`TF_FILA <contagem>`.

UUIDs FIXOS de proposito: o aceite confere na trilha do PostgreSQL o UUID canonico que viajou no
payload, entao ele nao pode variar entre rodadas.
"""
import json
import os
from datetime import datetime, timedelta

# Sufixo da massa: o aceite roda os fatos DUAS vezes (a leva do caminho feliz e a leva do retry) e os
# UUIDs canonicos sao UNICOS por contrato — a segunda leva precisa de identidade propria, senao morre
# na restricao de unicidade e o retry fica sem fato para medir.
SUFIXO = int((os.environ.get('TRE_SUFIXO') or '1').strip())

EMPRESA = 'e0e0e0e0-0000-4000-8000-%012d' % SUFIXO
OPORTUNIDADE = '0a0a0a0a-0000-4000-8000-%012d' % (SUFIXO * 2)
OPORTUNIDADE_PERDIDA = '0a0a0a0a-0000-4000-8000-%012d' % (SUFIXO * 2 + 1)
FASE = (os.environ.get('TRE_FASE') or 'fatos').strip()


def fatos():
    parceiro = env['res.partner'].create({
        'name': 'Empresa do aceite E03-T01 #%s' % SUFIXO,
        'tf_company_id': EMPRESA,
        'is_company': True,
    })
    lead = env['crm.lead'].create({
        'name': 'Oportunidade do aceite E03-T01 #%s' % SUFIXO,
        'type': 'opportunity',
        'partner_id': parceiro.id,
        'tf_opportunity_id': OPORTUNIDADE,
        'expected_revenue': 1000.0,
    })
    estagio = env['crm.stage'].search([('is_won', '=', False), ('id', '!=', lead.stage_id.id)],
                                      limit=1)
    lead.write({'stage_id': estagio.id})                 # STAGE_CHANGED
    lead.write({'expected_revenue': 2500.0})             # DEAL_VALUE_CHANGED
    lead.action_set_won()                                # STAGE_CHANGED + OPPORTUNITY_WON

    perdido = env['crm.lead'].create({
        'name': 'Oportunidade perdida do aceite E03-T01 #%s' % SUFIXO,
        'type': 'opportunity',
        'partner_id': parceiro.id,
        'tf_opportunity_id': OPORTUNIDADE_PERDIDA,
        'expected_revenue': 500.0,
    })
    motivo = env['crm.lost.reason'].create({'name': 'Preco'})
    perdido.action_set_lost(lost_reason_id=motivo.id)    # OPPORTUNITY_LOST + LOSS_REASON_RECORDED

    modelo = env['ir.model']._get('crm.lead')
    atividade = env['mail.activity'].create({
        'activity_type_id': env['mail.activity.type'].search([], limit=1).id,
        'res_model_id': modelo.id,
        'res_id': lead.id,
        'summary': 'Ligar para o diretor',
        'date_deadline': (datetime.now() + timedelta(days=2)).date(),
        'user_id': env.user.id,
    })
    atividade._action_done(feedback='Cliente pediu proposta')   # ACTIVITY_COMPLETED

    agora = datetime.now()
    env['calendar.event'].create({                              # MEETING_CREATED
        'name': 'Diagnostico com o cliente (aceite E03-T01 #%s)' % SUFIXO,
        'start': agora + timedelta(days=1),
        'stop': agora + timedelta(days=1, hours=1),
        'opportunity_id': lead.id,
    })
    env.cr.commit()
    print('TF_FATOS empresa=%s oportunidade=%s perdida=%s' % (EMPRESA, OPORTUNIDADE,
                                                              OPORTUNIDADE_PERDIDA))


def enviar():
    resumo = env['tf.evento.outbox']._tf_enviar_pendentes(limite=200)
    env.cr.commit()
    print('TF_RESUMO %s' % json.dumps(resumo, sort_keys=True))


def replay():
    fila = env['tf.evento.outbox'].sudo().search([('status', 'in', ('SENT', 'DEAD_LETTER'))])
    total = len(fila)
    fila.write({'status': 'PENDING', 'last_error': False})
    env.cr.commit()
    print('TF_REPLAY %s evento(s) de volta para a fila (mesmo envelope, mesma chave)' % total)


def relatorio():
    fila = env['tf.evento.outbox'].sudo().search([], order='id')
    for evento in fila:
        print('TF_EVENTO %s|%s|%s|%s|%s' % (
            evento.event_type or '-',
            evento.status or '-',
            evento.attempts,
            evento.idempotency_key or '-',
            (evento.last_error or '-').replace('\n', ' ')[:80],
        ))
    print('TF_FILA %s' % len(fila))


FASES = {'fatos': fatos, 'enviar': enviar, 'replay': replay, 'relatorio': relatorio}

if FASE not in FASES:
    print('FALHOU fase desconhecida: %s (use %s)' % (FASE, ', '.join(sorted(FASES))))
    raise SystemExit(2)
FASES[FASE]()
