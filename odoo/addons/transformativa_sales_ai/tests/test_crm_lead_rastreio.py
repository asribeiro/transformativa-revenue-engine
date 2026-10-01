# -*- coding: utf-8 -*-
"""Testes dos campos de rastreio de `crm.lead` — card TRE-W2-E04-T02.

O que estes testes provam (criterios de aceitacao homologados do card):

1. **AC1** — os campos previstos no contrato estao presentes em `crm.lead` (os 13 do inventario
   `CAMPOS_DE_RASTREIO`, incluindo os dois que o contrato nomeia: `tf_opportunity_id` e
   `tf_priority_score`), com o tipo declarado e os indices onde o contrato os justifica;
2. **AC2** — o CRM padrao nao quebra: criacao e consulta de lead seguem funcionando, os campos
   padrao do `crm.lead` continuam intactos e um lead comum nao ganha valor artificial nos campos
   novos;
3. **AC3** — criacao e consulta com dado sintetico: um lead com os 13 campos e' criado, lido de
   volta e encontrado por busca/filtro nos campos indexados.

Provam tambem as duas derivacoes do contrato que este modulo faz: o tiering (§8) e o
vocabulario fechado `next_best_action` (§7) — ambos conferidos contra as constantes do modelo,
que o conferidor de contrato `scripts/odoo/conferir_crm_lead_no_contrato.py` confronta com o
`data_contract_v1.json`.

Rodam com `odoo -d <banco> -u transformativa_sales_ai --test-enable --stop-after-init`.
"""
from odoo import fields
from odoo.addons.transformativa_sales_ai.models import crm_lead as modelo
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULO = 'transformativa_sales_ai'

# Tipo esperado de cada campo do inventario (o contrato fixa o PAPEL do dado; o tipo Odoo e' a
# traducao declarada no modelo — se um deles mudar de tipo sem o contrato mudar, o teste cai).
TIPOS_ESPERADOS = {
    'tf_opportunity_id': 'char',
    'tf_priority_score': 'float',
    'tf_icp_score': 'float',
    'tf_automation_fit_score': 'float',
    'tf_buying_signal_score': 'float',
    'tf_data_quality_score': 'float',
    'tf_score_version': 'char',
    'tf_priority_tier': 'selection',
    'tf_next_best_action': 'selection',
    'tf_correlation_id': 'char',
    'tf_idempotency_key': 'char',
    'tf_last_sync_at': 'datetime',
    'tf_last_event_type': 'char',
}

UUID_SINTETICO = '3f1a2b4c-5d6e-4f70-8a91-b2c3d4e5f607'


@tagged('post_install', '-at_install', MODULO)
class TestCrmLeadRastreio(TransactionCase):

    def _lead(self, **valores):
        base = {'name': 'Lead sintetico TRE-W2-E04-T02', 'type': 'opportunity'}
        base.update(valores)
        return self.env['crm.lead'].create(base)

    # --- AC1: campos presentes ---------------------------------------------------------------
    def test_01_campos_do_contrato_presentes_no_modelo(self):
        """AC1: os 13 campos de rastreio estao declarados em `crm.lead`."""
        campos = self.env['crm.lead']._fields
        faltando = [nome for nome in modelo.CAMPOS_DE_RASTREIO if nome not in campos]
        self.assertFalse(
            faltando,
            'campos de rastreio ausentes em crm.lead: %s' % ', '.join(faltando),
        )
        # os dois nomes que o contrato escreve literalmente (Data Contract V1.0 §3)
        for nome in ('tf_opportunity_id', 'tf_priority_score'):
            self.assertIn(nome, campos, 'campo nomeado pelo contrato ausente: %s' % nome)

    def test_02_tipos_e_indices_dos_campos(self):
        """AC1: tipo declarado de cada campo e indice nos 3 campos de busca do contrato."""
        campos = self.env['crm.lead']._fields
        for nome, tipo in sorted(TIPOS_ESPERADOS.items()):
            campo = campos[nome]
            self.assertEqual(
                campo.type, tipo,
                'campo %s: tipo %s (esperado %s)' % (nome, campo.type, tipo),
            )
        for nome in modelo.CAMPOS_INDEXADOS:
            self.assertTrue(
                campos[nome].index,
                'campo %s deveria estar indexado (busca por identidade/correlacao — contrato §3/§6)'
                % nome,
            )
        # inventario fechado: nenhum tf_ a mais que os 13 declarados
        declarados = {nome for nome in campos if nome.startswith('tf_')}
        self.assertEqual(
            declarados, set(modelo.CAMPOS_DE_RASTREIO),
            'campos tf_ em crm.lead (%s) != inventario do card (%s)'
            % (sorted(declarados), sorted(modelo.CAMPOS_DE_RASTREIO)),
        )

    # --- AC3: dado sintetico ----------------------------------------------------------------
    def test_03_criacao_e_consulta_com_dado_sintetico(self):
        """AC3: lead com os 13 campos e' criado, lido de volta e encontrado por busca."""
        parceiro = self.env['res.partner'].create({'name': 'Parceiro sintetico TRE-W2-E04-T02'})
        lead = self._lead(
            partner_id=parceiro.id,
            tf_opportunity_id=UUID_SINTETICO,
            tf_priority_score=87.5,
            tf_icp_score=91.0,
            tf_automation_fit_score=72.0,
            tf_buying_signal_score=63.5,
            tf_data_quality_score=80.0,
            tf_score_version='2026-10-01.1',
            tf_next_best_action='CREATE_MEETING',
            tf_correlation_id='corr-sintetica-tre-w2-e04-t02',
            tf_idempotency_key='idem-sintetica-tre-w2-e04-t02',
            tf_last_sync_at=fields.Datetime.now(),
            tf_last_event_type='OPPORTUNITY_RECOMMENDED',
        )
        self.assertTrue(lead.id, 'lead sintetico nao foi criado')
        self.assertEqual(lead.tf_opportunity_id, UUID_SINTETICO)
        self.assertEqual(lead.tf_priority_score, 87.5)
        self.assertEqual(lead.tf_icp_score, 91.0)
        self.assertEqual(lead.tf_automation_fit_score, 72.0)
        self.assertEqual(lead.tf_buying_signal_score, 63.5)
        self.assertEqual(lead.tf_data_quality_score, 80.0)
        self.assertEqual(lead.tf_score_version, '2026-10-01.1')
        self.assertEqual(lead.tf_priority_tier, 'A', 'tiering do contrato §8: 87,5 -> A')
        self.assertEqual(lead.tf_next_best_action, 'CREATE_MEETING')
        self.assertEqual(lead.tf_correlation_id, 'corr-sintetica-tre-w2-e04-t02')
        self.assertEqual(lead.tf_idempotency_key, 'idem-sintetica-tre-w2-e04-t02')
        self.assertTrue(lead.tf_last_sync_at)
        self.assertEqual(lead.tf_last_event_type, 'OPPORTUNITY_RECOMMENDED')

        # consulta de volta por ORM: busca pelo UUID canonico e filtro pelo vocabulario do contrato
        achado = self.env['crm.lead'].search([('tf_opportunity_id', '=', UUID_SINTETICO)])
        self.assertEqual(achado, lead, 'busca por tf_opportunity_id nao devolveu o lead')
        por_acao = self.env['crm.lead'].search([
            ('tf_next_best_action', '=', 'CREATE_MEETING'),
            ('tf_idempotency_key', '=', 'idem-sintetica-tre-w2-e04-t02'),
        ])
        self.assertIn(lead, por_acao, 'filtro por campos de rastreio nao encontrou o lead')

    # --- AC2: CRM padrao nao quebra ---------------------------------------------------------
    def test_04_crm_padrao_nao_quebra(self):
        """AC2: criacao/consulta de lead comum funcionam e os campos padrao seguem intactos."""
        campos = self.env['crm.lead']._fields
        for padrao in ('name', 'type', 'partner_id', 'stage_id', 'expected_revenue', 'user_id'):
            self.assertIn(padrao, campos, 'campo padrao %s desapareceu de crm.lead' % padrao)

        parceiro = self.env['res.partner'].create({'name': 'Parceiro padrao TRE-W2-E04-T02'})
        lead = self._lead(
            partner_id=parceiro.id,
            expected_revenue=15000.0,
            type='opportunity',
        )
        self.assertTrue(lead.id)
        self.assertEqual(lead.partner_id, parceiro)
        self.assertEqual(lead.expected_revenue, 15000.0)
        self.assertTrue(lead.stage_id, 'oportunidade sem estagio padrao do CRM')

        # um lead comum NAO ganha valor artificial nos campos novos
        for nome in ('tf_opportunity_id', 'tf_score_version', 'tf_correlation_id',
                     'tf_idempotency_key', 'tf_last_event_type', 'tf_last_sync_at',
                     'tf_next_best_action', 'tf_priority_tier'):
            self.assertFalse(getattr(lead, nome), '%s veio preenchido num lead comum' % nome)
        for nome in ('tf_priority_score', 'tf_icp_score', 'tf_automation_fit_score',
                     'tf_buying_signal_score', 'tf_data_quality_score'):
            self.assertEqual(getattr(lead, nome), 0.0, '%s veio diferente de 0.0' % nome)

        # consulta padrao do CRM continua funcionando
        achados = self.env['crm.lead'].search([('name', '=', lead.name)])
        self.assertIn(lead, achados, 'busca padrao por nome nao encontrou o lead')
        lead.write({'name': 'Lead padrao renomeado TRE-W2-E04-T02'})
        self.assertEqual(lead.name, 'Lead padrao renomeado TRE-W2-E04-T02')

    # --- derivacoes do contrato --------------------------------------------------------------
    def test_05_faixa_de_prioridade_segue_o_tiering_do_contrato(self):
        """O tiering do contrato §8 e' respeitado pelo campo derivado `tf_priority_tier`."""
        casos = ((95.0, 'A+'), (90.0, 'A+'), (89.99, 'A'), (84.5, 'A'), (80.0, 'A'),
                 (79.99, 'B'), (65.0, 'B'), (64.99, 'C'), (50.0, 'C'), (49.99, 'Nurture'),
                 (0.01, 'Nurture'))
        for score, esperado in casos:
            lead = self._lead(tf_priority_score=score)
            self.assertEqual(
                lead.tf_priority_tier, esperado,
                'score %s -> faixa %s (esperado %s)' % (score, lead.tf_priority_tier, esperado),
            )
        sem_score = self._lead()
        self.assertFalse(sem_score.tf_priority_tier,
                         'lead sem score nao deveria ter faixa (o contrato define faixas de SCORE)')

    def test_06_uuid_canonico_da_oportunidade(self):
        """`tf_opportunity_id` e' o UUID canonico do contrato §3 — texto livre e' recusado."""
        with self.assertRaises(ValidationError):
            lead = self._lead(tf_opportunity_id='nao-e-uuid')
            lead.flush_recordset()
        lead = self._lead(tf_opportunity_id=UUID_SINTETICO)
        self.assertEqual(lead.tf_opportunity_id, UUID_SINTETICO)

    def test_07_vocabulario_e_eventos_do_contrato(self):
        """Vocabulario (§7), faixas (§8) e lista de eventos (§6) batem com o declarado no modelo."""
        campos = self.env['crm.lead']._fields
        acoes = dict(campos['tf_next_best_action']._description_selection(self.env))
        self.assertEqual(
            set(acoes), set(modelo.VOCABULARIO_NEXT_BEST_ACTION),
            'vocabulario de tf_next_best_action != vocabulario do contrato §7',
        )
        self.assertEqual(len(acoes), 9)
        faixas = dict(campos['tf_priority_tier']._description_selection(self.env))
        self.assertEqual(
            set(faixas), {nome for nome, _min, _max in modelo.TIERS_DE_PRIORIDADE},
            'faixas de tf_priority_tier != tiering do contrato §8',
        )
        self.assertEqual(len(modelo.EVENTOS_DO_CONTRATO), 13,
                         'a lista de eventos do contrato §6 tem 13 nomes (6 PG->Odoo + 7 Odoo->PG)')
        ajuda = campos['tf_last_event_type'].help or ''
        for evento in modelo.EVENTOS_DO_CONTRATO:
            self.assertIn(evento, ajuda, 'evento %s nao documentado no help do campo' % evento)
