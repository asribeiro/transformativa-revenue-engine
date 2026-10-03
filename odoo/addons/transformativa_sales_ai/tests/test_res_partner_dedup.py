# -*- coding: utf-8 -*-
"""Campos de dedup e IDs canonicos em `res.partner` (card TRE-W2-E04-T01).

O que estes testes provam — e' o criterio de aceitacao homologado (Anderson, 29/09/2026),
lido aqui item a item:

AC1 "Campos de dedup (CNPJ, dominio, LinkedIn) presentes e indexados em `res.partner`":
    test_01 (presentes no modelo e em `ir.model.fields`), test_02 (declarados como indexados
    pelo ORM) e test_03 (indice **real** no catalogo do PostgreSQL — `pg_indexes`).
AC2 "IDs canonicos seguem o Data Contract V1.0":
    test_04 (nome, tipo e espelho dos IDs do `canonical_ids.odoo_map`) e test_05 (UUID invalido
    e' recusado: o campo e' o ID canonico, nao texto livre). O confronto dos NOMES com o
    `docs/data/data_contract_v1.json` congelado e' feito por
    `scripts/odoo/conferir_res_partner_no_contrato.py`, no aceite — aqui ficam as constantes que
    aquele conferidor tambem usa.
AC3 "Teste de criacao e consulta com dado sintetico":
    test_06 cria o parceiro com os tres identificadores fortes + UUID canonico e o consulta por
    cada um deles (e prova que um identificador diferente nao casa).

Limites declarados (nao sao AC, estao no runbook `docs/runbooks/res-partner-campos-dedup.md`):
    test_07 documenta que CNPJ invalido e' ARMAZENADO (o contrato manda reportar o par para a
    fila humana, nao recusar o dado); nao ha teste de unicidade porque o contrato nao a define.

Rodam com `odoo -d <banco> -u transformativa_sales_ai --test-enable --stop-after-init`.
"""
import re
import uuid

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULO = 'transformativa_sales_ai'

# Identificadores fortes do contrato (`docs/data/data_contract_v1.json` -> `dedup.strong`:
# ["cnpj", "domain", "linkedin_url"]) espelhados no Odoo com o prefixo do contrato.
CAMPOS_DEDUP = ('tf_cnpj', 'tf_domain', 'tf_linkedin_url')

# `canonical_ids.odoo_map` do contrato, restringido as chaves que apontam para `res.partner`:
#   "organizations.id"  -> "res.partner.tf_company_id"
#   "priority score"    -> "res.partner.tf_priority_score / crm.lead.tf_priority_score"
CAMPOS_CANONICOS = {
    'organizations.id': ('tf_company_id', 'char'),
    'priority score': ('tf_priority_score', 'float'),
}


@tagged('post_install', '-at_install', MODULO)
class TestResPartnerDedup(TransactionCase):

    # --- AC1: presentes -------------------------------------------------------------------
    def test_01_campos_de_dedup_presentes_no_modelo(self):
        """Os tres identificadores fortes existem em `res.partner` e em `ir.model.fields`."""
        parceiro = self.env['res.partner']
        for campo in CAMPOS_DEDUP:
            self.assertIn(campo, parceiro._fields,
                          'campo de dedup %s ausente do modelo res.partner' % campo)
            registro = self.env['ir.model.fields'].search([
                ('model', '=', 'res.partner'), ('name', '=', campo),
            ])
            self.assertEqual(len(registro), 1,
                             'esperava 1 ir.model.fields para res.partner.%s, achei %s'
                             % (campo, len(registro)))
            self.assertEqual(registro.ttype, 'char',
                             'res.partner.%s deveria ser Char (identificador textual), e %s'
                             % (campo, registro.ttype))
            self.assertTrue(registro.field_description,
                            'res.partner.%s sem rotulo (field_description vazio)' % campo)

    def test_02_campos_de_dedup_declarados_como_indexados(self):
        """O ORM declara indice nos tres campos (`ir.model.fields.index`).

        No Odoo 19 `ir.model.fields.index` e' **booleano** (`True`/`False`) — medido no aceite
        deste card (log 2-teste.log, primeira rodada: campo indexado grava `True`). O *tipo*
        do indice (btree) e' provado no catalogo do PostgreSQL em `test_03`.
        """
        for campo in CAMPOS_DEDUP:
            registro = self.env['ir.model.fields'].search([
                ('model', '=', 'res.partner'), ('name', '=', campo),
            ])
            self.assertTrue(
                registro.index is True,
                'res.partner.%s deveria ter indice declarado no ORM, e ir.model.fields.index=%r'
                % (campo, registro.index),
            )

    def test_03_indice_real_no_catalogo_do_postgres(self):
        """Prova o indice no BANCO (`pg_indexes`), nao so' a declaracao do modelo."""
        self.env.cr.execute(
            "select indexdef from pg_indexes where schemaname = 'public' "
            "and tablename = 'res_partner'"
        )
        definicoes = [linha[0] for linha in self.env.cr.fetchall()]
        self.assertTrue(definicoes, 'nenhum indice encontrado em res_partner (medicao impossivel)')
        for campo in CAMPOS_DEDUP:
            alvo = re.compile(r'\(\s*%s\s*\)' % re.escape(campo))
            candidatos = [d for d in definicoes if alvo.search(d)]
            self.assertTrue(
                candidatos,
                'nenhum indice do PostgreSQL cobre res_partner.%s; indices medidos: %s'
                % (campo, definicoes),
            )
            self.assertTrue(
                any('btree' in d for d in candidatos),
                'o indice de res_partner.%s nao e btree: %s' % (campo, candidatos),
            )

    # --- AC2: IDs canonicos do contrato ----------------------------------------------------
    def test_04_ids_canonicos_do_contrato(self):
        """`tf_company_id` e `tf_priority_score` existem, com o tipo do mapeamento do contrato."""
        parceiro = self.env['res.partner']
        for chave, (campo, tipo) in CAMPOS_CANONICOS.items():
            self.assertIn(campo, parceiro._fields,
                          'campo canonico %s (contrato: %s) ausente de res.partner'
                          % (campo, chave))
            self.assertEqual(
                parceiro._fields[campo].type, tipo,
                'res.partner.%s deveria ser %s (contrato: %s), e %s'
                % (campo, tipo, chave, parceiro._fields[campo].type),
            )
        self.assertTrue(self.env['res.partner']._fields['tf_company_id'].index,
                        'tf_company_id e chave de ligacao com organizations (UUID): tem de ser indexado')

    def test_05_uuid_canonico_invalido_e_recusado(self):
        """O campo do ID canonico aceita UUID; texto que nao e' UUID e' recusado."""
        canonico = str(uuid.uuid4())
        parceiro = self.env['res.partner'].create({
            'name': 'Parceiro UUID canonico TRE-W2-E04-T01',
            'tf_company_id': canonico,
        })
        self.assertEqual(parceiro.tf_company_id, canonico)
        with self.assertRaises(ValidationError):
            self.env['res.partner'].create({
                'name': 'Parceiro UUID invalido TRE-W2-E04-T01',
                'tf_company_id': 'organizacao-1',
            })

    # --- AC3: criacao e consulta com dado sintetico -----------------------------------------
    def test_06_criacao_e_consulta_com_dado_sintetico(self):
        """Cria parceiro com os identificadores fortes e o consulta por cada um deles."""
        canonico = str(uuid.uuid4())
        parceiro = self.env['res.partner'].create({
            'name': 'Empresa Sintetica TRE-W2-E04-T01',
            'tf_cnpj': '12.345.678/0001-95',
            'tf_domain': 'sintetica.tre.test',
            'tf_linkedin_url': 'https://www.linkedin.com/company/tre-sintetica',
            'tf_company_id': canonico,
            'tf_priority_score': 87.5,
        })
        self.assertTrue(parceiro.id, 'parceiro sintetico nao foi criado')

        consultas = [
            ('tf_cnpj', '12.345.678/0001-95'),
            ('tf_domain', 'sintetica.tre.test'),
            ('tf_linkedin_url', 'https://www.linkedin.com/company/tre-sintetica'),
            ('tf_company_id', canonico),
        ]
        for campo, valor in consultas:
            achados = self.env['res.partner'].search([(campo, '=', valor)])
            self.assertIn(parceiro, achados,
                          'consulta por %s=%r nao devolveu o parceiro sintetico' % (campo, valor))
        self.assertEqual(parceiro.tf_priority_score, 87.5)

        # negativo: identificador diferente nao casa (a consulta e' por igualdade de identificador)
        for campo, valor in (('tf_cnpj', '99.999.999/9999-99'),
                             ('tf_domain', 'outra.tre.test'),
                             ('tf_company_id', str(uuid.uuid4()))):
            self.assertFalse(self.env['res.partner'].search([(campo, '=', valor)]),
                             'consulta por %s=%r casou com o parceiro errado' % (campo, valor))

        # o CRM padrao segue funcionando com o parceiro customizado
        lead = self.env['crm.lead'].create({
            'name': 'Lead sintetico TRE-W2-E04-T01',
            'partner_id': parceiro.id,
            'type': 'lead',
        })
        self.assertEqual(lead.partner_id, parceiro)

    def test_07_cnpj_invalido_e_armazenado_nao_recusado(self):
        """Decisao declarada: CNPJ invalido e' ARMAZENADO — o contrato manda reportar, nao recusar.

        (§5 + D3 do runbook de dedup: CNPJ sem digito verificador detecta a duplicidade e vai
        para a fila humana; recusar o dado aqui seria regra mais dura que o contrato.)
        """
        parceiro = self.env['res.partner'].create({
            'name': 'Empresa CNPJ invalido TRE-W2-E04-T01',
            'tf_cnpj': '11.111.111/1111-11',
        })
        self.assertEqual(parceiro.tf_cnpj, '11.111.111/1111-11')
