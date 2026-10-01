# -*- coding: utf-8 -*-
"""Medicao ORM do aceite TRE-W2-E04-T02 — campos de rastreio em `crm.lead`.

Roda dentro de `odoo shell -d <banco>` (ver `verificar-crm-lead-odoo.sh`), DEPOIS do modulo
instalado. Nao e' teste do runner (os testes do modulo estao em
`odoo/addons/transformativa_sales_ai/tests/test_crm_lead_rastreio.py`): aqui o aceite mede o
banco VIVO, pelo ORM, com dado sintetico, e deixa a linha sintetica gravada para o verificador
conferir por SQL fora da sessao do Odoo.

O que mede (criterios de aceitacao homologados):
  AC1 campos previstos no contrato presentes em crm.lead (os 13 + os 5 score types do §8);
  AC2 CRM padrao nao quebra (lead comum cria, le', busca e escreve; campos padrao intactos);
  AC3 criacao e consulta com dado sintetico (lead com os 13 campos, busca por UUID canonico e
      por chave de idempotencia).

Marcadores (o `odoo shell` NAO propaga exit code; quem confere e' o marcador):
  MEDICAO_CRM_LEAD_OK (N itens, 0 falhas)
  MEDICAO_CRM_LEAD_FALHOU (N itens, M falha(s))
"""
import uuid as uuid_mod

from odoo import fields as odoo_fields
from odoo.addons.transformativa_sales_ai.models.crm_lead import (
    CAMPOS_DE_RASTREIO,
    CAMPOS_INDEXADOS,
    EVENTOS_DO_CONTRATO,
    TIERS_DE_PRIORIDADE,
    VOCABULARIO_NEXT_BEST_ACTION,
)
from odoo.exceptions import ValidationError

ITENS = 0
FALHAS = 0
UUID_SINTETICO = '7c2d4e6f-8a90-4b1c-9d2e-3f4a5b6c7d80'
IDEMPOTENCIA = 'idem-tre-w2-e04-t02-medicao'
CORRELACAO = 'corr-tre-w2-e04-t02-medicao'


def ok(msg):
    global ITENS
    ITENS += 1
    print('OK    %s' % msg)


def falhou(msg):
    global ITENS, FALHAS
    ITENS += 1
    FALHAS += 1
    print('FALHOU %s' % msg)


def confere(condicao, msg_ok, msg_falha):
    if condicao:
        ok(msg_ok)
    else:
        falhou(msg_falha)


# ---------------------------------------------------------------------------
# AC1 — campos presentes no banco (ir_model_fields) e indices reais em pg_indexes
# ---------------------------------------------------------------------------
print('=== AC1: campos do contrato presentes em crm.lead ===')
presentes = env['ir.model.fields'].search([
    ('model', '=', 'crm.lead'),
    ('name', 'in', list(CAMPOS_DE_RASTREIO)),
])
nomes_presentes = set(presentes.mapped('name'))
faltando = sorted(set(CAMPOS_DE_RASTREIO) - nomes_presentes)
confere(not faltando, 'os %d campos do inventario estao em ir_model_fields de crm.lead'
        % len(CAMPOS_DE_RASTREIO), 'campos ausentes em crm.lead: %s' % ', '.join(faltando))

todos_tf = env['ir.model.fields'].search([('model', '=', 'crm.lead'), ('name', 'like', 'tf_%')])
so_nossos = set(todos_tf.mapped('name'))
confere(so_nossos == set(CAMPOS_DE_RASTREIO),
        'os campos tf_ de crm.lead sao exatamente o inventario do card (%d)' % len(so_nossos),
        'campos tf_ em crm.lead (%s) != inventario do card (%s)'
        % (sorted(so_nossos), sorted(CAMPOS_DE_RASTREIO)))

manuais = todos_tf.filtered(lambda f: f.state == 'manual')
confere(not manuais, 'nenhum campo tf_ com state=manual (campos do modulo, nao do Studio)',
        'campos tf_ marcados como manuais: %s' % ', '.join(manuais.mapped('name')))

env.cr.execute(
    "select indexdef from pg_indexes where tablename = 'crm_lead'"
)
indices = [linha[0] for linha in env.cr.fetchall()]
for nome in CAMPOS_INDEXADOS:
    confere(any(nome in definicao for definicao in indices),
            'indice real no banco para %s' % nome,
            'sem indice no banco para %s (esperado: busca por identidade/correlacao)' % nome)

# score types do contrato §8 tem espelho (o conferidor de contrato confronta com o JSON)
for tipo in ('PRIORITY', 'ICP', 'AUTOMATION_FIT', 'BUYING_SIGNAL', 'DATA_QUALITY'):
    nome = 'tf_%s_score' % tipo.lower()
    confere(nome in env['crm.lead']._fields, 'score_type %s tem espelho %s' % (tipo, nome),
            'score_type %s sem espelho %s' % (tipo, nome))

# ---------------------------------------------------------------------------
# AC3 — dado sintetico: criacao, leitura de volta e busca
# ---------------------------------------------------------------------------
print('=== AC3: dado sintetico (criacao + consulta) ===')
Lead = env['crm.lead']
Partner = env['res.partner']
# limpa o residuo de uma rodada anterior (a medicao pode rodar duas vezes no mesmo banco)
Lead.search([('tf_opportunity_id', '=', UUID_SINTETICO)]).unlink()
Partner.search([('name', '=', 'Parceiro sintetico TRE-W2-E04-T02')]).unlink()

parceiro = Partner.create({'name': 'Parceiro sintetico TRE-W2-E04-T02'})
lead = Lead.create({
    'name': 'Lead sintetico TRE-W2-E04-T02',
    'type': 'opportunity',
    'partner_id': parceiro.id,
    'expected_revenue': 42000.0,
    'tf_opportunity_id': UUID_SINTETICO,
    'tf_priority_score': 87.5,
    'tf_icp_score': 91.0,
    'tf_automation_fit_score': 72.0,
    'tf_buying_signal_score': 63.5,
    'tf_data_quality_score': 80.0,
    'tf_score_version': '2026-10-01.1',
    'tf_next_best_action': 'CREATE_MEETING',
    'tf_correlation_id': CORRELACAO,
    'tf_idempotency_key': IDEMPOTENCIA,
    'tf_last_sync_at': odoo_fields.Datetime.now(),
    'tf_last_event_type': 'OPPORTUNITY_RECOMMENDED',
})
env.cr.commit()
confere(bool(lead.id), 'lead sintetico criado (id=%s)' % lead.id, 'lead sintetico NAO foi criado')

lido = Lead.browse(lead.id)
tabela_valores = (
    ('tf_opportunity_id', UUID_SINTETICO),
    ('tf_priority_score', 87.5),
    ('tf_icp_score', 91.0),
    ('tf_automation_fit_score', 72.0),
    ('tf_buying_signal_score', 63.5),
    ('tf_data_quality_score', 80.0),
    ('tf_score_version', '2026-10-01.1'),
    ('tf_next_best_action', 'CREATE_MEETING'),
    ('tf_correlation_id', CORRELACAO),
    ('tf_idempotency_key', IDEMPOTENCIA),
    ('tf_last_event_type', 'OPPORTUNITY_RECOMMENDED'),
)
for campo, esperado in tabela_valores:
    valor = getattr(lido, campo)
    confere(valor == esperado, 'leitura de %s == %r' % (campo, esperado),
            'leitura de %s = %r (esperado %r)' % (campo, valor, esperado))
confere(bool(lido.tf_last_sync_at), 'tf_last_sync_at gravado (%s)' % lido.tf_last_sync_at,
        'tf_last_sync_at nao gravado')
confere(lido.tf_priority_tier == 'A', 'tiering do contrato: 87,5 -> A (%s)' % lido.tf_priority_tier,
        'tiering: 87,5 -> %s (esperado A)' % lido.tf_priority_tier)
confere(lido.expected_revenue == 42000.0, 'campo padrao expected_revenue intacto (42000.0)',
        'expected_revenue = %s (esperado 42000.0)' % lido.expected_revenue)

por_uuid = Lead.search([('tf_opportunity_id', '=', UUID_SINTETICO)])
confere(por_uuid == lido, 'busca por tf_opportunity_id devolve o lead (indice usado)',
        'busca por tf_opportunity_id devolveu %s' % por_uuid.ids)

por_idem = Lead.search([('tf_idempotency_key', '=', IDEMPOTENCIA),
                        ('tf_next_best_action', '=', 'CREATE_MEETING')])
confere(por_idem == lido, 'busca por idempotencia + proxima acao devolve o lead',
        'busca por idempotencia + proxima acao devolveu %s' % por_idem.ids)

# grava de novo por ORM (o espelho aceita atualizacao vinda da integracao) e le'
lido.write({'tf_priority_score': 40.0, 'tf_next_best_action': 'NURTURE',
            'tf_last_event_type': 'PRIORITY_SCORE_CHANGED'})
env.cr.commit()
relido = Lead.browse(lead.id)
confere(relido.tf_priority_tier == 'Nurture',
        'tier recalculado apos write (40,0 -> Nurture)',
        'tier apos write = %s (esperado Nurture)' % relido.tf_priority_tier)
confere(relido.tf_last_event_type == 'PRIORITY_SCORE_CHANGED',
        'ultimo evento do contrato atualizado (PRIORITY_SCORE_CHANGED)',
        'ultimo evento = %s' % relido.tf_last_event_type)

# ---------------------------------------------------------------------------
# AC2 — o CRM padrao nao quebra
# ---------------------------------------------------------------------------
print('=== AC2: CRM padrao nao quebra ===')
comum = Lead.create({'name': 'Lead comum TRE-W2-E04-T02', 'type': 'lead'})
env.cr.commit()
confere(bool(comum.id), 'lead comum (sem campos novos) criado (id=%s)' % comum.id,
        'lead comum NAO foi criado')
for campo in ('tf_opportunity_id', 'tf_score_version', 'tf_next_best_action', 'tf_priority_tier',
              'tf_last_sync_at', 'tf_last_event_type', 'tf_correlation_id', 'tf_idempotency_key'):
    valor = getattr(comum, campo)
    confere(not valor, 'lead comum sem valor artificial em %s' % campo,
            'lead comum com %s = %r' % (campo, valor))
for campo in ('tf_priority_score', 'tf_icp_score', 'tf_automation_fit_score',
              'tf_buying_signal_score', 'tf_data_quality_score'):
    confere(getattr(comum, campo) == 0.0, 'lead comum com %s = 0.0' % campo,
            'lead comum com %s = %r (esperado 0.0)' % (campo, getattr(comum, campo)))
achado_comum = Lead.search([('name', '=', 'Lead comum TRE-W2-E04-T02')])
confere(comum in achado_comum, 'busca padrao por nome encontra o lead comum',
        'busca padrao por nome nao encontrou o lead comum')
comum.write({'name': 'Lead comum TRE-W2-E04-T02 (renomeado)'})
confere(comum.name.endswith('(renomeado)'), 'write padrao de crm.lead continua funcionando',
        'write padrao de crm.lead falhou')
campos_padrao = ('name', 'type', 'partner_id', 'stage_id', 'expected_revenue', 'user_id', 'email_from')
faltam_padrao = [c for c in campos_padrao if c not in Lead._fields]
confere(not faltam_padrao, 'campos padrao do crm.lead intactos (%s)' % ', '.join(campos_padrao),
        'campos padrao ausentes no crm.lead: %s' % ', '.join(faltam_padrao))
comum.unlink()
env.cr.commit()
ok('lead comum removido depois da medicao')

# ---------------------------------------------------------------------------
# tiering do contrato e constraint de UUID
# ---------------------------------------------------------------------------
print('=== tiering do contrato e UUID canonico ===')
for score, esperado in ((95.0, 'A+'), (89.99, 'A'), (84.5, 'A'), (65.0, 'B'), (50.0, 'C'),
                        (49.99, 'Nurture'), (0.01, 'Nurture')):
    probe = Lead.create({'name': 'probe tier', 'tf_priority_score': score})
    confere(probe.tf_priority_tier == esperado,
            'score %s -> faixa %s' % (score, esperado),
            'score %s -> faixa %s (esperado %s)' % (score, probe.tf_priority_tier, esperado))
    probe.unlink()
env.cr.commit()
probe = Lead.create({'name': 'probe sem score'})
confere(not probe.tf_priority_tier, 'lead sem score nao tem faixa (o contrato define faixas de score)',
        'lead sem score ganhou faixa %s' % probe.tf_priority_tier)
probe.unlink()
env.cr.commit()

try:
    invalido = Lead.create({'name': 'probe uuid invalido', 'tf_opportunity_id': 'nao-e-uuid'})
    invalido.flush_recordset()
    invalido.unlink()
    env.cr.commit()
    falhou('UUID invalido foi ACEITO em tf_opportunity_id (contrato §3 exige UUID)')
except ValidationError:
    env.cr.rollback()
    ok('UUID invalido recusado em tf_opportunity_id (contrato §3)')

valido = uuid_mod.uuid4()
probe = Lead.create({'name': 'probe uuid valido', 'tf_opportunity_id': str(valido)})
confere(probe.tf_opportunity_id == str(valido), 'UUID v4 valido aceito em tf_opportunity_id',
        'UUID v4 valido recusado em tf_opportunity_id')
probe.unlink()
env.cr.commit()

print('=== inventario do modulo (identidade do que foi medido) ===')
print('INFO  campos=%d indexados=%s' % (len(CAMPOS_DE_RASTREIO), ','.join(CAMPOS_INDEXADOS)))
print('INFO  score_types=%d tiers=%d vocabulario=%d eventos=%d'
      % (5, len(TIERS_DE_PRIORIDADE), len(VOCABULARIO_NEXT_BEST_ACTION), len(EVENTOS_DO_CONTRATO)))
print('INFO  lead sintetico: id=%s tf_opportunity_id=%s' % (lead.id, UUID_SINTETICO))
print('---')
if FALHAS == 0:
    print('MEDICAO_CRM_LEAD_OK (%d itens, 0 falhas)' % ITENS)
else:
    print('MEDICAO_CRM_LEAD_FALHOU (%d itens, %d falha(s))' % (ITENS, FALHAS))
