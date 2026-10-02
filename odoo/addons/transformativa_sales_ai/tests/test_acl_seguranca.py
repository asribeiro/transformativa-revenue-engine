# -*- coding: utf-8 -*-
# ============================================================================
# Aceite TRE-W2-E07-T01 — ACLs e regras de seguranca do modulo (carteira x tenant).
#
# Criterios de aceitacao homologados (Anderson, 29/09/2026):
#   AC1 regras de acesso por carteira/tenant aplicadas;
#   AC2 teste negativo: usuario de um tenant NAO ve dado de outro — vazio ou erro,
#       nunca material alheio;
#   AC3 aprovacao humana jamais concedida por maquina.
#
# Estes testes sao `post_install` (rodam no `--test-enable` do aceite) e medem
# COMPORTAMENTO com usuarios de verdade (`with_user`), nao a forma do XML:
# os dois tenants sao duas companhias reais, as carteiras sao vendedores reais em
# `res.partner.user_id`, e o material alheio e atacado por busca E por leitura direta
# do id (o ataque que a busca filtrada sozinha nao cobriria).
#
# Nenhum mock do proprio alvo: o que esta sob teste e' o registry do Odoo carregando as
# regras do modulo e o ORM aplicando-as.
# ============================================================================

from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULO = 'transformativa_sales_ai'
MODELO = 'tf.process.opportunity'


@tagged('post_install', '-at_install', MODULO)
class TestAclSeguranca(TransactionCase):
    """Aceite das ACLs/regras de seguranca do modulo (card TRE-W2-E07-T01)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Oportunidade = cls.env[MODELO]
        cls.grupo_vendedor = cls.env.ref('%s.group_tf_sales_ai_user' % MODULO)
        cls.grupo_gestor = cls.env.ref('%s.group_tf_sales_ai_manager' % MODULO)
        cls.grupo_interno = cls.env.ref('base.group_user')

        # Dois TENANTS (companhias do Odoo) e quatro usuarios: dois vendedores e um gestor no
        # tenant A, um vendedor no tenant B, mais um usuario interno SEM o grupo do modulo.
        cls.tenant_a = cls.env['res.company'].create(
            {'name': 'Tenant A (sintetico) TRE-W2-E07-T01'})
        cls.tenant_b = cls.env['res.company'].create(
            {'name': 'Tenant B (sintetico) TRE-W2-E07-T01'})

        cls.vendedor_a1 = cls._usuario('e07t01.vendedor.a1', cls.tenant_a, cls.grupo_vendedor)
        cls.vendedor_a2 = cls._usuario('e07t01.vendedor.a2', cls.tenant_a, cls.grupo_vendedor)
        cls.vendedor_a3 = cls._usuario('e07t01.vendedor.a3', cls.tenant_a, cls.grupo_vendedor)
        cls.gestor_a = cls._usuario('e07t01.gestor.a', cls.tenant_a, cls.grupo_gestor)
        cls.vendedor_b = cls._usuario('e07t01.vendedor.b', cls.tenant_b, cls.grupo_vendedor)
        cls.sem_grupo = cls._usuario('e07t01.sem.grupo', cls.tenant_a, None)

        cls.estagio = cls.env['crm.stage'].create(
            {'name': 'Estagio sintetico TRE-W2-E07-T01', 'sequence': 901})

        # Carteiras: o vendedor do parceiro e' o dono da carteira (`res.partner.user_id`).
        cls.parceiro_a1 = cls._parceiro('Parceiro carteira A1', cls.tenant_a, cls.vendedor_a1)
        cls.parceiro_a2 = cls._parceiro('Parceiro carteira A2', cls.tenant_a, cls.vendedor_a2)
        cls.parceiro_b = cls._parceiro('Parceiro carteira B', cls.tenant_b, cls.vendedor_b)

        cls.op_a1 = cls._oportunidade('Oportunidade tenant A / carteira A1',
                                      cls.parceiro_a1, cls.tenant_a)
        cls.op_a2 = cls._oportunidade('Oportunidade tenant A / carteira A2',
                                      cls.parceiro_a2, cls.tenant_a)
        cls.op_b = cls._oportunidade('Oportunidade tenant B / carteira B',
                                     cls.parceiro_b, cls.tenant_b)

    # -- ajudantes -------------------------------------------------------------
    @classmethod
    def _usuario(cls, login, tenant, grupo):
        vals = {
            'name': 'Usuario %s TRE-W2-E07-T01' % login,
            'login': login,
            'company_id': tenant.id,
            'company_ids': [(6, 0, [tenant.id])],
            'group_ids': [(6, 0, [cls.grupo_interno.id] + ([grupo.id] if grupo else []))],
        }
        return cls.env['res.users'].create(vals)

    @classmethod
    def _parceiro(cls, nome, tenant, vendedor):
        return cls.env['res.partner'].create({
            'name': '%s TRE-W2-E07-T01' % nome,
            'is_company': True,
            'company_id': tenant.id,
            'user_id': vendedor.id,
        })

    @classmethod
    def _oportunidade(cls, nome, parceiro, tenant):
        return cls.env[MODELO].create({
            'name': nome,
            'partner_id': parceiro.id,
            'company_id': tenant.id,
            'stage_id': cls.estagio.id,
        })

    def _prova_negativa(self, usuario, registro, rotulo):
        """AC2: `registro` NUNCA chega a `usuario` — vazio na busca OU erro na leitura.

        Devolve 'vazio' ou 'erro' (o caminho medido) e reprova se o material alheio passar.
        """
        modelo_do_usuario = self.Oportunidade.with_user(usuario)
        ids = modelo_do_usuario.search([]).ids
        self.assertNotIn(
            registro.id, ids,
            'MATERIAL ALHEIO: a busca de %s devolveu %r (%s)' % (usuario.login, registro.id, rotulo),
        )
        try:
            lido = registro.with_user(usuario).read(['name', 'tf_uuid'])
        except AccessError:
            return 'erro'
        self.fail(
            'MATERIAL ALHEIO: %s leu por id o registro %r (%s): %r'
            % (usuario.login, registro.id, rotulo, lido)
        )

    def _ids_visiveis(self, usuario):
        return set(self.Oportunidade.with_user(usuario).search([]).ids)

    # -- AC1: as regras estao aplicadas (e sao as do modulo) -------------------
    def test_01_grupos_do_modulo_declarados_com_hierarquia(self):
        """Os dois grupos existem, no privilegio/categoria do modulo, e o gestor e' vendedor com mais."""
        self.assertTrue(self.grupo_vendedor, 'grupo do vendedor ausente')
        self.assertTrue(self.grupo_gestor, 'grupo do gestor ausente')
        categoria = self.env.ref('%s.ir_module_category_tf_sales_ai' % MODULO)
        privilegio = self.env.ref('%s.privilege_tf_sales_ai' % MODULO)
        self.assertEqual(privilegio.category_id, categoria)
        self.assertEqual(self.grupo_vendedor.privilege_id, privilegio)
        self.assertEqual(self.grupo_gestor.privilege_id, privilegio)
        self.assertIn(
            self.grupo_vendedor, self.grupo_gestor.all_implied_ids,
            'o gestor tem de herdar o grupo do vendedor',
        )
        self.assertNotIn(
            self.grupo_interno, self.grupo_gestor.all_implied_ids,
            'o modulo nao concede acesso interno (base.group_user) por conta propria',
        )

    def test_02_acl_do_modelo_com_a_matriz_declarada(self):
        """A ACL do modelo e' a declarada: vendedor nao apaga; gestor apaga."""
        vendedor = self.env['ir.model.access'].sudo().search([
            ('model_id.model', '=', MODELO), ('group_id', '=', self.grupo_vendedor.id),
        ])
        gestor = self.env['ir.model.access'].sudo().search([
            ('model_id.model', '=', MODELO), ('group_id', '=', self.grupo_gestor.id),
        ])
        self.assertEqual(len(vendedor), 1, 'esperava 1 ACL do vendedor, achei %s' % len(vendedor))
        self.assertEqual(len(gestor), 1, 'esperava 1 ACL do gestor, achei %s' % len(gestor))
        self.assertEqual(
            (vendedor.perm_read, vendedor.perm_write, vendedor.perm_create, vendedor.perm_unlink),
            (True, True, True, False),
            'matriz do vendedor diferente da declarada',
        )
        self.assertEqual(
            (gestor.perm_read, gestor.perm_write, gestor.perm_create, gestor.perm_unlink),
            (True, True, True, True),
            'matriz do gestor diferente da declarada',
        )

    def test_03_regras_de_registro_com_o_dominio_declarado_e_ativas(self):
        """As tres regras existem, ativas, com o dominio e o alcance declarados."""
        tenant = self.env.ref('%s.rule_tf_oportunidade_tenant' % MODULO)
        carteira = self.env.ref('%s.rule_tf_oportunidade_carteira' % MODULO)
        gestor = self.env.ref('%s.rule_tf_oportunidade_gestor' % MODULO)
        for regra in (tenant, carteira, gestor):
            self.assertTrue(regra.active, 'regra %s inativa' % regra.name)
            self.assertEqual(regra.model_id.model, MODELO, 'regra %s em modelo errado' % regra.name)
            self.assertTrue(regra.perm_read and regra.perm_write and regra.perm_create,
                            'regra %s nao cobre leitura/escrita/criacao' % regra.name)
        # TENANT: global (sem grupo) -> vale para todo mundo que alcanca o modelo.
        self.assertFalse(tenant.groups, 'a regra de tenant nao pode ser presa a um grupo')
        self.assertTrue(self._e_global(tenant), 'a regra de tenant tem de ser global')
        self.assertEqual(tenant.domain_force, "[('company_id', 'in', company_ids)]")
        # CARTEIRA: presa ao grupo do vendedor, pelo vendedor NATIVO do parceiro.
        self.assertFalse(self._e_global(carteira))
        self.assertEqual(carteira.groups, self.grupo_vendedor)
        self.assertEqual(carteira.domain_force, "[('partner_id.user_id', '=', user.id)]")
        # GESTOR: presa ao grupo do gestor, sem recorte de carteira (o recorte de tenant e' global).
        self.assertFalse(self._e_global(gestor))
        self.assertEqual(gestor.groups, self.grupo_gestor)
        self.assertEqual(gestor.domain_force, "[(1, '=', 1)]")

    def _e_global(self, regra):
        """Le o campo `global` de uma regra (nome reservado em Python: vai por dominio)."""
        return bool(self.env['ir.rule'].sudo().search([('id', '=', regra.id), ('global', '=', True)]))

    # -- AC2: os testes negativos ---------------------------------------------
    def test_04_usuario_sem_grupo_do_modulo_nao_alcanca_o_modelo(self):
        """Fail-closed: sem o grupo do modulo o modelo nem e' pesquisavel."""
        with self.assertRaises(AccessError):
            self.Oportunidade.with_user(self.sem_grupo).search([])

    def test_05_carteira_isola_dentro_do_tenant(self):
        """O vendedor ve a propria carteira; a carteira do colega e' vazio/erro, nunca dado."""
        self.assertEqual(self._ids_visiveis(self.vendedor_a1), {self.op_a1.id})
        self.assertEqual(self._ids_visiveis(self.vendedor_a2), {self.op_a2.id})
        self.assertEqual(self._prova_negativa(self.vendedor_a1, self.op_a2, 'carteira A2'), 'erro')
        self.assertEqual(self._prova_negativa(self.vendedor_a2, self.op_a1, 'carteira A1'), 'erro')

    def test_06_carteira_vazia_devolve_vazio_sem_erro(self):
        """O vendedor sem carteira devolve VAZIO (nao erro, nao material alheio)."""
        self.assertEqual(self._ids_visiveis(self.vendedor_a3), set())

    def test_07_tenant_isola_o_dado_do_outro_tenant(self):
        """AC2 no osso: o dado do outro TENANT nao chega por busca nem por leitura de id."""
        self.assertEqual(self._ids_visiveis(self.vendedor_b), {self.op_b.id})
        self.assertEqual(self._prova_negativa(self.vendedor_b, self.op_a1, 'outro tenant'), 'erro')
        self.assertEqual(self._prova_negativa(self.vendedor_b, self.op_a2, 'outro tenant'), 'erro')

    def test_08_gestor_alcanca_o_tenant_inteiro_e_so_ele(self):
        """O gestor ve todas as carteiras do SEU tenant e nenhuma do outro."""
        self.assertEqual(self._ids_visiveis(self.gestor_a), {self.op_a1.id, self.op_a2.id})
        self.assertEqual(self._prova_negativa(self.gestor_a, self.op_b, 'outro tenant'), 'erro')

    def test_09_carteira_alheia_recusa_criacao(self):
        """A regra vale na criacao: o vendedor nao cria para parceiro de carteira alheia."""
        with self.assertRaises(AccessError):
            self.Oportunidade.with_user(self.vendedor_a1).create({
                'name': 'Oportunidade plantada pelo vendedor A1 em carteira alheia',
                'partner_id': self.parceiro_a2.id,
                'company_id': self.tenant_a.id,
            })

    def test_10_vendedor_cria_na_propria_carteira(self):
        """Contraprova do 09: na propria carteira a criacao funciona (a regra nao e' um bloqueio cego)."""
        criada = self.Oportunidade.with_user(self.vendedor_a1).create({
            'name': 'Oportunidade do vendedor A1 na propria carteira',
            'partner_id': self.parceiro_a1.id,
            'company_id': self.tenant_a.id,
        })
        self.assertTrue(criada.id)
        self.assertIn(criada.id, self._ids_visiveis(self.vendedor_a1))

    # -- AC3: aprovacao humana nunca concedida por maquina ---------------------
    def test_11_o_modulo_nao_promove_usuario_nem_abre_capacidade_de_aprovacao(self):
        """AC3: o modulo nao da ao usuario poder de administrador nem alcance fora do seu modelo.

        A aprovacao humana do TRE vive FORA do Odoo (registro de aprovacoes + gate JEV) e e'
        decisao do Anderson. O que se mede aqui e' que este modulo nao cria caminho nenhum para
        uma maquina conceder aprovacao: (a) a superficie de ACL do modulo fica dentro dos modelos
        do proprio modulo (derivada de `ir.model`, para nao envelhecer a cada modelo novo),
        (b) os grupos do modulo nao alcancam os grupos de administracao do Odoo,
        (c) o usuario do Sales AI nao administra OUTRO usuario, (d) nao cria regra de acesso, e
        (e) a tentativa de se dar o grupo de administrador NAO promove (medida, nao suposta).
        """
        dados = self.env['ir.model.data'].sudo().search([
            ('module', '=', MODULO), ('model', '=', 'ir.model.access'),
        ])
        self.assertTrue(dados, 'o modulo nao declara ACL nenhuma')
        acls = self.env['ir.model.access'].sudo().browse(dados.mapped('res_id'))
        # A superficie de ACL do modulo tem de ficar DENTRO dos modelos do proprio modulo.
        # Nao e' a lista dos modelos que "hoje existem": a expectativa e' DERIVADA de `ir.model`
        # (quais modelos este modulo registra), entao ela nao envelhece quando um card novo cria
        # modelo. O defeito que este conserto mede: o `tf.evento.outbox` entrou no card
        # TRE-W3-E03-T01 (commit d0b8d5a) COM ACL propria e esta expectativa ficou presa em
        # `{MODELO}` — a suite do modulo passou a reprovar por defeito HERDADO, nao por violacao.
        # (`git log -1 -- tests/test_acl_seguranca.py` = 060c369, anterior ao d0b8d5a.)
        # DERIVADO dos dados do proprio modulo (`ir.model.data` com `model = ir.model`): sao os
        # modelos que ESTE modulo registra. `ir.model.modules` nao serve para filtrar em `search`
        # (campo nao armazenado — medido: o `search` estoura no SQL), entao a derivacao vem do
        # registro do modulo, nao da coluna.
        modelos_do_modulo = set(
            self.env['ir.model'].sudo().browse(
                self.env['ir.model.data'].sudo().search([
                    ('module', '=', MODULO), ('model', '=', 'ir.model'),
                ]).mapped('res_id')
            ).mapped('model')
        )
        self.assertTrue(modelos_do_modulo, 'o modulo nao registra modelo nenhum')
        self.assertTrue(modelos_do_modulo >= {MODELO}, 'o modelo do card nao esta em ir.model')
        self.assertEqual(
            set(acls.mapped('model_id.model')) - modelos_do_modulo, set(),
            'a superficie de ACL do modulo tem de ficar dentro dos modelos do proprio modulo',
        )
        grupo_system = self.env.ref('base.group_system')
        proibidos = {grupo_system.id, self.env.ref('base.group_erp_manager').id}
        for grupo in (self.grupo_vendedor, self.grupo_gestor):
            alcance = set(grupo.all_implied_ids.ids)
            self.assertFalse(
                alcance & proibidos,
                'grupo %s alcanca grupo de administracao do Odoo' % grupo.name,
            )
        with self.assertRaises(AccessError):
            self.vendedor_a1.with_user(self.gestor_a).write({'name': 'promovido por outro usuario'})
        with self.assertRaises(AccessError):
            self.env['ir.rule'].with_user(self.gestor_a).create({
                'name': 'regra plantada pelo usuario do Sales AI',
                'model_id': self.env['ir.model']._get_id(MODELO),
                'domain_force': "[(1, '=', 1)]",
            })
        # (e) a tentativa de autopromocao: recusada (AccessError) OU, se passar, o grupo de
        # administrador NAO entra no usuario — as duas formas fecham; dado alcancado, nao.
        try:
            self.gestor_a.with_user(self.gestor_a).write(
                {'group_ids': [(4, grupo_system.id)]})
        except AccessError:
            pass
        self.assertNotIn(
            grupo_system, self.gestor_a.with_user(self.gestor_a).group_ids,
            'MATERIAL ALHEIO: o usuario do Sales AI se promoveu a administrador do Odoo',
        )
