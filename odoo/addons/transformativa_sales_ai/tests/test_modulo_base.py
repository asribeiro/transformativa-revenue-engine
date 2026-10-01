# -*- coding: utf-8 -*-
"""Testes do modulo base `transformativa_sales_ai` (card TRE-W2-E03-T01).

O que estes testes provam (criterios de aceitacao homologados):

1. o modulo esta instalado e a versao que o Odoo gravou e a do manifesto em disco;
2. a serie da versao acompanha a serie do Odoo em execucao (19.0);
3. as dependencias declaradas no manifesto estao instaladas e batem com o que o Odoo
   gravou em `ir.module.module.dependency`;
4. as ancoras das customizacoes que vem nas proximas cards (`res.partner` e `crm.lead`)
   existem e aceitam dado sintetico — a base sobre a qual o modulo sera customizado;
5. a instalacao terminou assentada (nenhum modulo em 'to install'/'to upgrade'/'to remove').

Rodam com `odoo -d <banco> -u transformativa_sales_ai --test-enable --stop-after-init`.
"""
from odoo import release
from odoo.modules.module import get_manifest
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULO = 'transformativa_sales_ai'


@tagged('post_install', '-at_install', MODULO)
class TestModuloBase(TransactionCase):

    def _modulo(self):
        modulo = self.env['ir.module.module'].search([('name', '=', MODULO)])
        self.assertEqual(
            len(modulo), 1,
            'esperava exatamente 1 registro de ir.module.module para %s, achei %s'
            % (MODULO, len(modulo)),
        )
        return modulo

    def test_01_instalado_com_a_versao_do_manifesto(self):
        """AC 'manifesto e versao corretos': a versao gravada e a do manifesto em disco."""
        modulo = self._modulo()
        manifesto = get_manifest(MODULO)
        self.assertEqual(modulo.state, 'installed',
                         'modulo %s nao esta instalado (estado: %s)' % (MODULO, modulo.state))
        self.assertEqual(
            modulo.latest_version, manifesto['version'],
            'versao gravada (%s) diferente da do manifesto em disco (%s)'
            % (modulo.latest_version, manifesto['version']),
        )

    def test_02_serie_da_versao_acompanha_o_odoo(self):
        """A serie da versao do modulo tem de acompanhar a serie do Odoo em execucao."""
        manifesto = get_manifest(MODULO)
        serie_odoo = release.version.split('-')[0]
        serie_modulo = '.'.join(manifesto['version'].split('.')[:2])
        self.assertEqual(
            serie_modulo, serie_odoo,
            'serie do modulo (%s) != serie do Odoo em execucao (%s)' % (serie_modulo, serie_odoo),
        )

    def test_03_manifesto_declarado(self):
        """Licenca, instalabilidade e nao-aplicacao declaradas e conferidas no banco."""
        modulo = self._modulo()
        manifesto = get_manifest(MODULO)
        self.assertTrue(manifesto.get('installable', True), 'modulo nao declarado instalavel')
        self.assertFalse(manifesto.get('application', False), 'modulo base nao e aplicacao')
        self.assertTrue(manifesto.get('license'), 'manifesto sem licenca declarada')
        self.assertEqual(modulo.license, manifesto['license'])
        self.assertIn('base', manifesto['depends'], 'manifesto nao declara a dependencia base')

    def test_04_dependencias_declaradas_e_instaladas(self):
        """AC 'dependencias declaradas': manifesto == banco, e todas instaladas."""
        modulo = self._modulo()
        manifesto = get_manifest(MODULO)
        declaradas = set(manifesto['depends'])
        gravadas = set(
            self.env['ir.module.module.dependency']
            .search([('module_id', '=', modulo.id)])
            .mapped('name')
        )
        self.assertEqual(
            gravadas, declaradas,
            'dependencias gravadas (%s) != declaradas no manifesto (%s)'
            % (sorted(gravadas), sorted(declaradas)),
        )
        for nome in sorted(declaradas):
            dep = self.env['ir.module.module'].search([('name', '=', nome)])
            self.assertEqual(dep.state, 'installed',
                             'dependencia %s nao esta instalada (estado: %s)' % (nome, dep.state))

    def test_05_ancoras_das_customizacoes_respondem(self):
        """As ancoras das proximas cards existem e aceitam dado sintetico."""
        for modelo in ('res.partner', 'crm.lead'):
            self.assertIn(modelo, self.env, 'modelo ancora %s ausente' % modelo)
        parceiro = self.env['res.partner'].create({'name': 'Parceiro sintetico TRE-W2-E03-T01'})
        self.assertTrue(parceiro.id)
        lead = self.env['crm.lead'].create({
            'name': 'Lead sintetico TRE-W2-E03-T01',
            'partner_id': parceiro.id,
            'type': 'lead',
        })
        self.assertTrue(lead.id)
        self.assertEqual(lead.partner_id, parceiro)
        self.assertEqual(lead.partner_id.name, 'Parceiro sintetico TRE-W2-E03-T01')

    def test_06_instalacao_assentada(self):
        """Nenhum modulo ficou pendurado em to install/to upgrade/to remove."""
        pendentes = self.env['ir.module.module'].search(
            [('state', 'in', ('to install', 'to upgrade', 'to remove'))]
        )
        self.assertFalse(
            pendentes,
            'instalacao nao assentou: %s' % ', '.join(pendentes.mapped('name')),
        )
