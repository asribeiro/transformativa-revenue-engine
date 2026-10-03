# -*- coding: utf-8 -*-
# ============================================================================
# Aceite TRE-W2-E05-T01 — a oportunidade canonica do lado Odoo.
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 modelo `tf.process.opportunity` criado com os campos do contrato;
#   AC2 a oportunidade canonica vive no Odoo (regra do contrato), com vinculo a `res.partner`;
#   AC3 teste de criacao, consulta e relacao com parceiro.
#
# Estes testes sao `post_install`: rodam no `--test-enable` do aceite (o verificador exige
# N >= 1 teste no relatorio do runner, fail-closed) e medem comportamento, nao forma:
# o modelo e' criado, gravado, consultado e relacionado — contra o codigo REAL do modulo e
# contra o `crm` de verdade (nenhum mock do proprio alvo).
# ============================================================================

import uuid

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

# UUID fixo do "produtor do fato" nos testes (contrato §3: o ID nasce no produtor).
UUID_DO_PRODUTOR = '8f14e45f-ceea-4a1f-9b3c-2f7a1d0e5b60'


@tagged('post_install', '-at_install')
class TestOportunidadeCanonica(TransactionCase):
    """Aceite do modelo tf.process.opportunity (card TRE-W2-E05-T01)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Oportunidade = cls.env['tf.process.opportunity']
        cls.parceiro = cls.env['res.partner'].create({
            'name': 'Parceiro sintetico TRE-W2-E05-T01',
            'is_company': True,
            'email': 'aceite-e05t01@exemplo.invalido',
        })
        cls.estagio = cls.env['crm.stage'].create({
            'name': 'Estagio sintetico TRE-W2-E05-T01',
            'sequence': 900,
        })
        cls.motivo_perda = cls.env['crm.lost.reason'].create({
            'name': 'Motivo de perda sintetico TRE-W2-E05-T01',
        })

    # -- AC1: o modelo existe e tem os campos do contrato ----------------------
    def test_01_modelo_criado_com_os_campos_do_contrato(self):
        """O modelo existe no registry (e no ir.model) com os campos do contrato."""
        self.assertIn('tf.process.opportunity', self.env)
        modelo = self.env['ir.model']._get('tf.process.opportunity')
        self.assertTrue(modelo, 'tf.process.opportunity nao esta registrado em ir.model')

        campos = self.Oportunidade._fields
        # identidade canonica (contrato §3)
        self.assertTrue(campos['tf_uuid'].required, 'tf_uuid tem de ser obrigatorio (chave canonica)')
        # vinculo a res.partner (AC2)
        self.assertEqual(campos['partner_id'].comodel_name, 'res.partner')
        self.assertEqual(campos['partner_id'].type, 'many2one')
        self.assertTrue(campos['partner_id'].required, 'a oportunidade canonica tem de ter parceiro')
        # fatos cujo dono e' o Odoo (contrato §2)
        self.assertEqual(campos['stage_id'].comodel_name, 'crm.stage')
        self.assertEqual(campos['expected_revenue'].type, 'monetary')
        self.assertEqual(campos['lost_reason_id'].comodel_name, 'crm.lost.reason')

    # -- AC3: criacao ---------------------------------------------------------
    def test_02_criacao_com_uuid_do_produtor_preserva_o_id(self):
        """O UUID que vem do produtor do fato e' preservado (em forma estavel)."""
        oportunidade = self.Oportunidade.create({
            'name': 'Oportunidade com UUID do produtor',
            'partner_id': self.parceiro.id,
            # de proposito com maiusculas e espaco: a forma estavel e' minuscula com hifens
            'tf_uuid': '  %s  ' % UUID_DO_PRODUTOR.upper(),
        })
        self.assertEqual(oportunidade.tf_uuid, UUID_DO_PRODUTOR)
        self.assertEqual(oportunidade.name, 'Oportunidade com UUID do produtor')
        self.assertEqual(oportunidade.partner_id, self.parceiro)

    def test_03_uuid_gerado_quando_o_produtor_nao_manda(self):
        """Sem UUID no vals, o modelo gera um UUID v4 valido (nao um id inventado pela mao)."""
        oportunidade = self.Oportunidade.create({
            'name': 'Oportunidade sem UUID do produtor',
            'partner_id': self.parceiro.id,
        })
        self.assertTrue(oportunidade.tf_uuid, 'tf_uuid ficou vazio')
        self.assertEqual(uuid.UUID(oportunidade.tf_uuid).version, 4)

    def test_04_campos_do_contrato_gravam_e_leem(self):
        """Estagio, valor e motivo de perda (fatos do Odoo) gravam e voltam na leitura."""
        oportunidade = self.Oportunidade.create({
            'name': 'Oportunidade com funil',
            'partner_id': self.parceiro.id,
            'stage_id': self.estagio.id,
            'expected_revenue': 12345.67,
            'lost_reason_id': self.motivo_perda.id,
        })
        # releitura do banco (nao do cache do ORM)
        oportunidade.invalidate_recordset()
        lida = self.Oportunidade.browse(oportunidade.id)
        self.assertEqual(lida.stage_id, self.estagio)
        self.assertEqual(lida.expected_revenue, 12345.67)
        self.assertEqual(lida.lost_reason_id, self.motivo_perda)
        self.assertEqual(lida.currency_id, lida.company_id.currency_id)

    # -- AC3: consulta --------------------------------------------------------
    def test_05_consulta_por_uuid_e_por_parceiro(self):
        """A oportunidade e' consultavel pelo UUID canonico e pela relacao com o parceiro."""
        oportunidade = self.Oportunidade.create({
            'name': 'Oportunidade consultavel',
            'partner_id': self.parceiro.id,
            'tf_uuid': UUID_DO_PRODUTOR,
        })
        por_uuid = self.Oportunidade.search([('tf_uuid', '=', UUID_DO_PRODUTOR)])
        self.assertEqual(por_uuid, oportunidade)

        por_parceiro = self.Oportunidade.search_read(
            [('partner_id', '=', self.parceiro.id)],
            ['name', 'tf_uuid', 'partner_id'],
        )
        self.assertEqual(len(por_parceiro), 1)
        self.assertEqual(por_parceiro[0]['tf_uuid'], UUID_DO_PRODUTOR)
        self.assertEqual(por_parceiro[0]['partner_id'][0], self.parceiro.id)

    def test_06_relacao_com_parceiro_e_restricao_de_exclusao(self):
        """O vinculo com o parceiro e' real: o parceiro de uma oportunidade nao e' apagavel."""
        oportunidade = self.Oportunidade.create({
            'name': 'Oportunidade que segura o parceiro',
            'partner_id': self.parceiro.id,
        })
        erro = None
        try:
            with mute_logger('odoo.sql_db'):
                with self.env.cr.savepoint():
                    self.parceiro.unlink()
        except Exception as exc:  # a recusa e' o comportamento esperado
            erro = exc
        # A classe exata da recusa e' do core (FK `restrict`): fica no log para auditoria.
        print('test_06: recusa da exclusao do parceiro = %s: %s' % (type(erro).__name__, erro))
        self.assertIsNotNone(
            erro, 'o parceiro foi apagado apesar de ter oportunidade vinculada (o vinculo seria decorativo)')
        # Medido na imagem odoo:19.0: o core nao engole a violacao de FK `restrict` — ela chega
        # como `psycopg2.errors.ForeignKeyViolation` (subclasse de IntegrityError).
        self.assertIsInstance(
            erro, IntegrityError,
            'classe inesperada na recusa da exclusao: %s' % type(erro).__name__)
        self.assertTrue(self.parceiro.exists(), 'o parceiro foi apagado apesar do vinculo')
        self.assertTrue(oportunidade.exists(), 'a tentativa de exclusao levou a oportunidade junto')

    # -- AC1/AC2: a identidade canonica se comporta como chave ----------------
    def test_07_uuid_canonico_e_unico(self):
        """Duas oportunidades com o mesmo UUID sao recusadas (chave canonica unica, §3)."""
        self.Oportunidade.create({
            'name': 'Oportunidade unica (primeira)',
            'partner_id': self.parceiro.id,
            'tf_uuid': UUID_DO_PRODUTOR,
        })
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self.Oportunidade.create({
                    'name': 'Oportunidade unica (duplicada)',
                    'partner_id': self.parceiro.id,
                    'tf_uuid': UUID_DO_PRODUTOR,
                })
        self.assertEqual(self.Oportunidade.search_count([('tf_uuid', '=', UUID_DO_PRODUTOR)]), 1)

    def test_08_uuid_canonico_e_imutavel(self):
        """O UUID canonico nao muda; regravar o mesmo valor e' inofensivo (idempotencia)."""
        oportunidade = self.Oportunidade.create({
            'name': 'Oportunidade de identidade fixa',
            'partner_id': self.parceiro.id,
            'tf_uuid': UUID_DO_PRODUTOR,
        })
        oportunidade.write({'tf_uuid': UUID_DO_PRODUTOR})
        self.assertEqual(oportunidade.tf_uuid, UUID_DO_PRODUTOR)
        with self.assertRaises(ValidationError):
            oportunidade.write({'tf_uuid': str(uuid.uuid4())})
        self.assertEqual(oportunidade.tf_uuid, UUID_DO_PRODUTOR)

    def test_09_uuid_invalido_e_recusado(self):
        """Valor que nao e' UUID e' recusado, nao 'consertado' em silencio (contrato §3)."""
        with self.assertRaises(ValidationError):
            self.Oportunidade.create({
                'name': 'Oportunidade com id invalido',
                'partner_id': self.parceiro.id,
                'tf_uuid': 'nao-e-um-uuid',
            })
