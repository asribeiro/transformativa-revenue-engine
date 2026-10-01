# -*- coding: utf-8 -*-
# ============================================================================
# Aceite TRE-W2-E06-T01 — views do Sales AI no modulo `transformativa_sales_ai`.
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 as views exibem os campos das entidades customizadas, sem erro de renderizacao;
#   AC2 o acesso respeita o perfil de usuario (quem nao pode ver, nao ve);
#   AC3 evidencia de abertura das views (lista/formulario).
#
# Estes testes sao `post_install` (rodam no `--test-enable` do aceite) e medem COM
# COMPORTAMENTO, nao pela forma do XML:
#   * a view e' lida pelo ORM para um usuario de verdade (`get_view`, o MESMO caminho que o web
#     client usa para abrir a lista e o formulario) — "sem erro de renderizacao" e' nao levantar
#     e trazer os campos;
#   * o perfil e' medido com DOIS usuarios que diferem SO' pelo grupo do modulo (mesmos grupos
#     de CRM): o que aparecer num e nao no outro e' efeito deste modulo, nao do CRM;
#   * o recorte e' medido no BANCO quando o criterio fala de configuracao (grupo na view e no
#     menu) e no ORM quando o criterio fala de comportamento (o que o usuario ve/abre).
#
# Nenhum mock do proprio alvo: o que esta sob teste e' o registry do Odoo carregando as views
# do modulo e o ORM aplicando-as ao usuario.
# ============================================================================

from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULO = 'transformativa_sales_ai'
MODELO = 'tf.process.opportunity'
CAMPOS_PARCEIRO = (
    'tf_cnpj', 'tf_domain', 'tf_linkedin_url', 'tf_company_id', 'tf_priority_score')
CAMPOS_LEAD = (
    'tf_opportunity_id', 'tf_priority_score', 'tf_icp_score', 'tf_automation_fit_score',
    'tf_buying_signal_score', 'tf_data_quality_score', 'tf_score_version', 'tf_priority_tier',
    'tf_next_best_action', 'tf_correlation_id', 'tf_idempotency_key', 'tf_last_sync_at',
    'tf_last_event_type',
)
CAMPOS_LISTA = ('name', 'partner_id', 'stage_id', 'expected_revenue')
# O que a view RENDERIZADA mostra para o membro sem depender de grupo padrao do Odoo.
CAMPOS_FORMULARIO = ('name', 'partner_id', 'stage_id', 'expected_revenue', 'lost_reason_id', 'tf_uuid')
# `company_id` e `currency_id` seguem o convencional do Odoo (grupos padrao `base.group_multi_company`
# e `base.group_multi_currency`, como no proprio crm.lead): na arch GRAVADA estao sempre; na view
# renderizada, para quem tem esses grupos padrao — por isso sao medidos com o membro multi.
CAMPOS_FORMULARIO_MULTI = ('company_id', 'currency_id')
CAMPOS_FORMULARIO_TODOS = CAMPOS_FORMULARIO + CAMPOS_FORMULARIO_MULTI


@tagged('post_install', '-at_install', MODULO)
class TestViewsSalesAi(TransactionCase):
    """Aceite das views do Sales AI (card TRE-W2-E06-T01)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.grupo_modulo = cls.env.ref('%s.group_tf_sales_ai_user' % MODULO)
        cls.grupo_gestor = cls.env.ref('%s.group_tf_sales_ai_manager' % MODULO)
        cls.grupo_interno = cls.env.ref('base.group_user')
        grupo_vendas = cls.env.ref('sales_team.group_sale_salesman', raise_if_not_found=False)
        cls.grupos_de_crm = [cls.grupo_interno.id] + ([grupo_vendas.id] if grupo_vendas else [])
        cls.grupos_multi = [
            g.id for g in (
                cls.env.ref('base.group_multi_currency', raise_if_not_found=False),
            ) if g
        ]
        # `base.group_multi_company` NAO se concede na mao: o Odoo o concede (e o retira) sozinho
        # conforme o numero de companhias do usuario (`UsersMultiCompany` em res_users.py). Por
        # isso o membro multi tem DUAS companhias sinteticas — e e' esse estado real que mostra o
        # `company_id` na view.
        cls.companhia2 = cls.env['res.company'].create(
            {'name': 'Companhia sintetica TRE-W2-E06-T01'})

        # Os dois usuarios diferem SO' pelo grupo do modulo (AC2 isolado do CRM). O terceiro tem,
        # alem disso, os grupos padrao do Odoo de multi-companhia/multi-moeda — e' ele que mede os
        # campos condicionais (`company_id`, `currency_id`), para nao confundir o recorte do
        # modulo com o recorte padrao do Odoo.
        cls.membro = cls._usuario('e06t01.membro', com_grupo=True)
        cls.membro_multi = cls._usuario('e06t01.membro.multi', com_grupo=True, com_multi=True)
        cls.restrito = cls._usuario('e06t01.restrito', com_grupo=False)

        cls.estagio = cls.env['crm.stage'].create(
            {'name': 'Estagio sintetico TRE-W2-E06-T01', 'sequence': 903})
        cls.parceiro = cls.env['res.partner'].create({
            'name': 'Parceiro sintetico TRE-W2-E06-T01',
            'is_company': True,
            'user_id': cls.membro.id,
            'tf_cnpj': '12.345.678/0001-90',
            'tf_domain': 'exemplo-tre-e06t01.com.br',
            'tf_linkedin_url': 'https://www.linkedin.com/company/exemplo-tre-e06t01',
            'tf_company_id': '3f2504e0-4f89-11d3-9a0c-0305e82c3301',
            'tf_priority_score': 88.0,
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Lead sintetico TRE-W2-E06-T01',
            'type': 'opportunity',
            'partner_id': cls.parceiro.id,
        })
        cls.oportunidade = cls.env[MODELO].create({
            'name': 'Oportunidade sintetica TRE-W2-E06-T01',
            'partner_id': cls.parceiro.id,
            'stage_id': cls.estagio.id,
            'expected_revenue': 12345.67,
        })

    # -- ajudantes -------------------------------------------------------------
    @classmethod
    def _usuario(cls, login, com_grupo, com_multi=False):
        grupos = list(cls.grupos_de_crm)
        if com_grupo:
            grupos.append(cls.grupo_modulo.id)
        if com_multi:
            grupos += cls.grupos_multi
        return cls.env['res.users'].create({
            'name': 'Usuario %s TRE-W2-E06-T01' % login,
            'login': login,
            'company_id': cls.env.company.id,
            'company_ids': [
                (6, 0, [cls.env.company.id] + ([cls.companhia2.id] if com_multi else []))],
            'group_ids': [(6, 0, grupos)],
        })

    def _arch(self, usuario, modelo, view_type, view_id=None):
        """A view RENDERIZADA para `usuario` — o caminho que o web client usa para abrir."""
        return self.env[modelo].with_user(usuario).get_view(
            view_id=view_id, view_type=view_type)['arch']

    def _ausentes(self, arch, campos):
        return [campo for campo in campos if 'name="%s"' % campo not in arch]

    # -- AC1: as views existem e exibem os campos das entidades customizadas ---
    def test_01_views_do_modulo_declaradas(self):
        """As 5 views, a acao e os 2 menus do modulo existem, apontando para os alvos certos."""
        lista = self.env.ref('%s.view_tf_process_opportunity_list' % MODULO)
        formulario = self.env.ref('%s.view_tf_process_opportunity_form' % MODULO)
        busca = self.env.ref('%s.view_tf_process_opportunity_search' % MODULO)
        self.assertEqual((lista.model, lista.type), (MODELO, 'list'))
        self.assertEqual((formulario.model, formulario.type), (MODELO, 'form'))
        self.assertEqual((busca.model, busca.type), (MODELO, 'search'))
        self.assertEqual(
            self.env.ref('%s.view_partner_form_tf_sales_ai' % MODULO).inherit_id,
            self.env.ref('base.view_partner_form'))
        self.assertEqual(
            self.env.ref('%s.view_crm_lead_form_tf_sales_ai' % MODULO).inherit_id,
            self.env.ref('crm.crm_lead_view_form'))
        acao = self.env.ref('%s.action_tf_process_opportunity' % MODULO)
        self.assertEqual(acao.res_model, MODELO)

    def test_02_lista_e_formulario_do_modelo_trazem_os_campos_do_contrato(self):
        """AC1 na lista e no formulario da entidade propria do modulo."""
        lista = self.env.ref('%s.view_tf_process_opportunity_list' % MODULO)
        formulario = self.env.ref('%s.view_tf_process_opportunity_form' % MODULO)
        self.assertEqual(self._ausentes(lista.arch_db, CAMPOS_LISTA), [],
                         'campo do contrato ausente na lista')
        self.assertEqual(self._ausentes(formulario.arch_db, CAMPOS_FORMULARIO_TODOS), [],
                         'campo do contrato ausente no formulario')

    def test_03_busca_traz_o_uuid_canonico_e_o_parceiro(self):
        """AC1 na busca: o UUID canonico (contrato §3) e' caminho de busca declarado."""
        busca = self.env.ref('%s.view_tf_process_opportunity_search' % MODULO)
        self.assertEqual(self._ausentes(busca.arch_db, ('tf_uuid', 'partner_id')), [])

    def test_04_views_herdadas_trazem_os_campos_das_customizacoes(self):
        """AC1 nas views herdadas: os 5 campos do parceiro e os 13 do lead aparecem."""
        parceiro = self.env.ref('%s.view_partner_form_tf_sales_ai' % MODULO)
        lead = self.env.ref('%s.view_crm_lead_form_tf_sales_ai' % MODULO)
        self.assertEqual(self._ausentes(parceiro.arch_db, CAMPOS_PARCEIRO), [])
        self.assertEqual(self._ausentes(lead.arch_db, CAMPOS_LEAD), [])

    # -- AC3: a view ABRE (lista/formulario) e a lista devolve o dado ----------
    def test_05_ac3_lista_e_formulario_abrem_para_o_membro(self):
        """AC3: o membro abre lista e formulario (sem erro de renderizacao) e ve o dado."""
        arch_lista = self._arch(self.membro, MODELO, 'list')
        arch_form = self._arch(self.membro, MODELO, 'form')
        self.assertEqual(self._ausentes(arch_lista, CAMPOS_LISTA), [],
                         'a lista do membro nao trouxe os campos do contrato')
        self.assertEqual(self._ausentes(arch_form, CAMPOS_FORMULARIO), [],
                         'o formulario do membro nao trouxe os campos do contrato')
        # O membro com os grupos padrao do Odoo ve TAMBEM os campos condicionais do contrato:
        # assim se mede o contrato completo sem confundir o recorte padrao do Odoo com o do modulo.
        self.assertEqual(
            self._ausentes(self._arch(self.membro_multi, MODELO, 'form'), CAMPOS_FORMULARIO_TODOS),
            [], 'o formulario do membro multi nao trouxe o contrato completo')
        linhas = self.env[MODELO].with_user(self.membro).search_read(
            [('id', '=', self.oportunidade.id)], ['name', 'tf_uuid'])
        self.assertEqual(len(linhas), 1, 'a lista do membro nao devolveu o dado sintetico')
        self.assertTrue(linhas[0]['tf_uuid'], 'a linha do membro veio sem o UUID canonico')

    def test_06_ac3_o_menu_abre_a_lista_e_o_formulario(self):
        """AC3: o par lista/formulario esta ligado a acao, e o menu do item abre a acao."""
        acao = self.env.ref('%s.action_tf_process_opportunity' % MODULO)
        self.assertEqual(acao.view_mode, 'list,form')
        raiz = self.env.ref('%s.menu_tf_sales_ai_root' % MODULO)
        item = self.env.ref('%s.menu_tf_sales_ai_oportunidades' % MODULO)
        self.assertEqual(raiz.parent_id, self.env.ref('crm.crm_menu_root'))
        self.assertEqual(item.parent_id, raiz)
        self.assertEqual(item.action, acao)

    # -- AC1/AC3: a secao "Sales AI" aparece para quem tem o perfil -----------
    def test_07_a_secao_sales_ai_aparece_para_o_membro(self):
        """AC1/AC3: o membro ve a secao Sales AI no parceiro e no lead, com os campos."""
        parceiro = self._arch(self.membro, 'res.partner', 'form',
                              view_id=self.env.ref('base.view_partner_form').id)
        lead = self._arch(self.membro, 'crm.lead', 'form',
                          view_id=self.env.ref('crm.crm_lead_view_form').id)
        self.assertIn('tf_sales_ai', parceiro, 'a secao Sales AI nao esta no parceiro do membro')
        self.assertEqual(self._ausentes(parceiro, CAMPOS_PARCEIRO), [])
        self.assertEqual(self._ausentes(lead, CAMPOS_LEAD), [])

    # -- AC2: quem nao pode ver, nao ve ---------------------------------------
    def test_08_ac2_o_menu_do_sales_ai_nao_aparece_para_o_restrito(self):
        """AC2: o menu do modulo entra nos menus visiveis do membro e nao nos do restrito."""
        menus = set(
            self.env.ref('%s.menu_tf_sales_ai_root' % MODULO).ids
            + self.env.ref('%s.menu_tf_sales_ai_oportunidades' % MODULO).ids)
        visiveis_membro = set(self.env['ir.ui.menu'].with_user(self.membro)._visible_menu_ids())
        visiveis_restrito = set(self.env['ir.ui.menu'].with_user(self.restrito)._visible_menu_ids())
        self.assertTrue(menus <= visiveis_membro,
                        'o membro nao ve o menu do Sales AI: %s' % sorted(menus - visiveis_membro))
        self.assertFalse(menus & visiveis_restrito,
                         'MATERIAL ALHEIO: o restrito ve o menu do Sales AI: %s'
                         % sorted(menus & visiveis_restrito))

    def test_09_ac2_o_restrito_nao_ve_a_secao_sales_ai_nem_a_oportunidade(self):
        """AC2: o restrito nao ve a secao no parceiro/lead e nao le a oportunidade canonica."""
        parceiro = self._arch(self.restrito, 'res.partner', 'form',
                              view_id=self.env.ref('base.view_partner_form').id)
        self.assertNotIn('tf_sales_ai', parceiro,
                         'MATERIAL ALHEIO: o restrito ve a secao Sales AI no parceiro')
        self.assertEqual(
            [campo for campo in CAMPOS_PARCEIRO if 'name="%s"' % campo in parceiro], [],
            'MATERIAL ALHEIO: o restrito ve campo tf_* no parceiro')
        lead = self._arch(self.restrito, 'crm.lead', 'form',
                          view_id=self.env.ref('crm.crm_lead_view_form').id)
        self.assertNotIn('tf_sales_ai', lead,
                         'MATERIAL ALHEIO: o restrito ve a secao Sales AI no lead')
        self.assertEqual(
            [campo for campo in CAMPOS_LEAD if 'name="%s"' % campo in lead], [],
            'MATERIAL ALHEIO: o restrito ve campo tf_* no lead')
        with self.assertRaises(AccessError):
            self.env[MODELO].with_user(self.restrito).search([])

    # -- rollback: o modulo e' dono do que criou ------------------------------
    def test_10_o_modulo_e_dono_das_views_menus_e_acao_que_cria(self):
        """AC de rollback: views/menus/acao do card estao no modulo (desinstalar os remove)."""
        dados = self.env['ir.model.data'].sudo().search([('module', '=', MODULO)])
        criados = dados.filtered(
            lambda d: d.model in ('ir.ui.view', 'ir.ui.menu', 'ir.actions.act_window'))
        nomes = criados.mapped('name')
        for nome in ('view_tf_process_opportunity_list', 'view_tf_process_opportunity_form',
                     'view_tf_process_opportunity_search', 'view_partner_form_tf_sales_ai',
                     'view_crm_lead_form_tf_sales_ai', 'menu_tf_sales_ai_root',
                     'menu_tf_sales_ai_oportunidades', 'action_tf_process_opportunity'):
            self.assertIn(nome, nomes, 'o card nao registrou %s no modulo (rollback furado)' % nome)
        self.assertEqual(len(criados), 8, 'superficie de views/menus/acao do card mudou: %s' % nomes)
