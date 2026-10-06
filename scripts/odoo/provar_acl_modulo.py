#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prova NEGATIVA das ACLs/regras de seguranca do modulo (card TRE-W2-E07-T01).

Uso (o script e lido pelo stdin do `odoo shell`, num banco descartavel):

    docker run --rm -i --entrypoint odoo odoo:19.0 shell -d <banco> --no-http \
        < scripts/odoo/provar_acl_modulo.py

Por que existe, se o modulo ja tem `tests/test_acl_seguranca.py`: o aceite deste card nao pode
depender do proprio autor. Este script monta a cena do zero (dois tenants, tres carteiras,
usuarios de verdade), mede o comportamento pelo ORM com `with_user` e imprime item a item —
e o verificador `scripts/odoo/verificar-acl-modulo.sh` roda os DOIS (a suite do modulo e esta
prova) e ainda muta o artefato para provar que a medicao tem dente.

O que ele prova (critérios homologados do card):

    AC1 as regras estao aplicadas: grupos, ACL do modelo e as tres regras de registro;
    AC2 teste negativo: usuario de um tenant NAO alcanca dado do outro — vazio (busca) ou
        erro (leitura por id), NUNCA material alheio; e a carteira alheia do MESMO tenant
        tambem nao e alcancada;
    AC3 aprovacao humana nunca concedida por maquina: o usuario do Sales AI nao administra
        outro usuario, nao cria regra de acesso, nao se promove a administrador, e a
        superficie de ACL do modulo e' a ALLOW-LIST explicita (modelos E xmlids: as 5 ACLs de
        `tf.process.opportunity` + `tf.evento.outbox`).

Marcadores (lidos pelo verificador, nunca o exit code sozinho — `odoo shell` e' interativo):

    ACL_ITEM OK <descricao>
    ACL_ITEM FALHOU <descricao>
    ACL_ITENS=<n> ACL_FALHAS=<n>
    ACL_RESULTADO: OK | FALHOU
"""
import uuid

from odoo.exceptions import AccessError

MODULO = 'transformativa_sales_ai'
MODELO = 'tf.process.opportunity'
# Superficie de ACL do modulo = ALLOW-LIST EXPLICITA (decisao do dono, 03/10/2026, opcao A do
# defeito de integracao do W2 / card t_e0b1bcbf). O card TRE-W3-E03-T01 (commit d0b8d5a) entregou
# o consumidor de outbox e o modelo `tf.evento.outbox` passou a ter ACL. O guardrail NAO foi
# afrouxado: a expectativa e um CONJUNTO FECHADO de modelos E de xmlids — ACL inesperada reprova
# e ACL da lista que sumiu tambem reprova. Nao e "qualquer superficie serve".
MODELOS_ACL = {'tf.evento.outbox', 'tf.process.opportunity'}
ACLS_ESPERADAS = {
    'access_tf_evento_outbox_manager',
    'access_tf_evento_outbox_system',
    'access_tf_evento_outbox_user',
    'access_tf_process_opportunity_manager',
    'access_tf_process_opportunity_user',
}
PREFIXO = 'e07t01-prova'

ITENS = [0]
FALHAS = [0]


def ok(descricao):
    ITENS[0] += 1
    print('ACL_ITEM OK %s' % descricao)


def falhou(descricao):
    ITENS[0] += 1
    FALHAS[0] += 1
    print('ACL_ITEM FALHOU %s' % descricao)


def checar(condicao, descricao):
    if condicao:
        ok(descricao)
    else:
        falhou(descricao)


def negativa(modelo, usuario, registro, rotulo):
    """AC2: o registro nunca chega ao usuario — vazio na busca OU erro na leitura.

    Devolve o caminho medido ('vazio'/'erro') ou 'VAZOU' (que reprova o item).
    """
    ids = set(modelo.with_user(usuario).search([]).ids)
    if registro.id in ids:
        return 'VAZOU'
    try:
        registro.with_user(usuario).read(['name', 'tf_uuid'])
    except AccessError:
        return 'erro'
    return 'VAZOU'


def main():
    Oportunidade = env['tf.process.opportunity']  # noqa: F821 (env do shell)
    Usuario = env['res.users']  # noqa: F821
    Parceiro = env['res.partner']  # noqa: F821
    Companhia = env['res.company']  # noqa: F821

    # ---- o que o modulo declara (a regra do card, lida no banco) ----------------
    ref = env.ref  # noqa: F821
    try:
        grupo_vendedor = ref('%s.group_tf_sales_ai_user' % MODULO)
        grupo_gestor = ref('%s.group_tf_sales_ai_manager' % MODULO)
        regra_tenant = ref('%s.rule_tf_oportunidade_tenant' % MODULO)
        regra_carteira = ref('%s.rule_tf_oportunidade_carteira' % MODULO)
        regra_gestor = ref('%s.rule_tf_oportunidade_gestor' % MODULO)
        ok('AC1 grupos e regras do modulo carregados do XML de seguranca')
    except ValueError as exc:
        falhou('AC1 grupos/regras do modulo ausentes: %s' % exc)
        return resumo()

    checar(regra_tenant.model_id.model == MODELO and regra_carteira.model_id.model == MODELO
           and regra_gestor.model_id.model == MODELO,
           'AC1 as tres regras de registro apontam para %s' % MODELO)
    checar(not regra_tenant.groups and regra_carteira.groups == grupo_vendedor
           and regra_gestor.groups == grupo_gestor,
           'AC1 alcance das regras: tenant global, carteira no vendedor, gestor no gestor')
    checar(regra_tenant.domain_force == "[('company_id', 'in', company_ids)]"
           and regra_carteira.domain_force == "[('partner_id.user_id', '=', user.id)]"
           and regra_gestor.domain_force == "[(1, '=', 1)]",
           'AC1 dominio das tres regras e o declarado')

    # ---- cena: dois TENANTS (companhias) e tres CARTEIRAS (vendedores) ----------
    grupo_interno = ref('base.group_user')
    base = uuid.uuid4().hex[:8]
    tenant_a = Companhia.create({'name': '%s tenant A' % PREFIXO})
    tenant_b = Companhia.create({'name': '%s tenant B' % PREFIXO})

    def usuario(sufixo, tenant, grupos):
        return Usuario.create({
            'name': '%s %s' % (PREFIXO, sufixo),
            'login': '%s.%s.%s' % (PREFIXO, sufixo, base),
            'company_id': tenant.id,
            'company_ids': [(6, 0, [tenant.id])],
            'group_ids': [(6, 0, [grupo_interno.id] + [g.id for g in grupos])],
        })

    a1 = usuario('vendedor-a1', tenant_a, [grupo_vendedor])
    a2 = usuario('vendedor-a2', tenant_a, [grupo_vendedor])
    a3 = usuario('vendedor-a3-sem-carteira', tenant_a, [grupo_vendedor])
    ga = usuario('gestor-a', tenant_a, [grupo_gestor])
    b1 = usuario('vendedor-b1', tenant_b, [grupo_vendedor])
    sg = usuario('sem-grupo', tenant_a, [])
    estagio = env['crm.stage'].create({'name': '%s estagio' % PREFIXO, 'sequence': 950})  # noqa: F821

    def parceiro(nome, tenant, vendedor):
        return Parceiro.create({'name': '%s %s' % (PREFIXO, nome), 'is_company': True,
                                'company_id': tenant.id, 'user_id': vendedor.id})

    def oportunidade(nome, parceiro_reg, tenant):
        return Oportunidade.create({'name': '%s %s' % (PREFIXO, nome),
                                    'partner_id': parceiro_reg.id, 'company_id': tenant.id,
                                    'stage_id': estagio.id})

    parceiro_a1 = parceiro('carteira A1', tenant_a, a1)
    parceiro_a2 = parceiro('carteira A2', tenant_a, a2)
    parceiro_b1 = parceiro('carteira B1', tenant_b, b1)
    op_a1 = oportunidade('op A1', parceiro_a1, tenant_a)
    op_a2 = oportunidade('op A2', parceiro_a2, tenant_a)
    op_b1 = oportunidade('op B1', parceiro_b1, tenant_b)
    ok('cena montada: 2 tenants, 3 carteiras, 3 oportunidades (ids %s/%s/%s)'
       % (op_a1.id, op_a2.id, op_b1.id))

    # ---- AC2: carteira isola dentro do tenant ----------------------------------
    visiveis_a1 = set(Oportunidade.with_user(a1).search([]).ids)
    checar(visiveis_a1 == {op_a1.id},
           'AC2 vendedor A1 ve so a propria carteira do proprio tenant (%s)' % sorted(visiveis_a1))
    visiveis_a2 = set(Oportunidade.with_user(a2).search([]).ids)
    checar(visiveis_a2 == {op_a2.id},
           'AC2 vendedor A2 ve so a propria carteira do proprio tenant (%s)' % sorted(visiveis_a2))
    checar(set(Oportunidade.with_user(a3).search([]).ids) == set(),
           'AC2 vendedor sem carteira devolve VAZIO (nao erro, nao material alheio)')

    caminho = negativa(Oportunidade, a1, op_a2, 'carteira A2')
    checar(caminho == 'erro', 'AC2 leitura por id de carteira alheia na busca nega (%s)' % caminho)

    # ---- AC2: tenant isola (o osso do criterio) --------------------------------
    visiveis_b1 = set(Oportunidade.with_user(b1).search([]).ids)
    checar(visiveis_b1 == {op_b1.id},
           'AC2 vendedor do tenant B ve so o dado do tenant B (%s)' % sorted(visiveis_b1))
    for registro, rotulo in ((op_a1, 'tenant A'), (op_a2, 'tenant A')):
        caminho = negativa(Oportunidade, b1, registro, rotulo)
        checar(caminho == 'erro', 'AC2 %s negado ao tenant B (%s)' % (rotulo, caminho))
    visiveis_ga = set(Oportunidade.with_user(ga).search([]).ids)
    checar(visiveis_ga == {op_a1.id, op_a2.id},
           'AC2 gestor do tenant A ve a carteira inteira do SEU tenant (%s)' % sorted(visiveis_ga))
    caminho = negativa(Oportunidade, ga, op_b1, 'tenant B')
    checar(caminho == 'erro', 'AC2 gestor do tenant A nao alcanca o tenant B (%s)' % caminho)

    # ---- fail-closed: sem o grupo do modulo, o modelo fecha --------------------
    try:
        Oportunidade.with_user(sg).search([])
        falhou('AC2 usuario sem o grupo do modulo alcancou o modelo (nao e fail-closed)')
    except AccessError:
        ok('AC2 usuario sem o grupo do modulo recebe AccessError (fail-closed)')

    # ---- a regra vale tambem na CRIACAO ----------------------------------------
    try:
        Oportunidade.with_user(a1).create({'name': '%s plantada' % PREFIXO,
                                           'partner_id': parceiro_a2.id,
                                           'company_id': tenant_a.id})
        falhou('AC2 vendedor A1 criou oportunidade para parceiro de carteira alheia')
    except AccessError:
        ok('AC2 criacao em carteira alheia e recusada (AccessError)')
    criada = Oportunidade.with_user(a1).create({'name': '%s propria' % PREFIXO,
                                                'partner_id': parceiro_a1.id,
                                                'company_id': tenant_a.id})
    checar(criada.id and criada.id in set(Oportunidade.with_user(a1).search([]).ids),
           'AC2 contraprova: na propria carteira a criacao funciona')

    # ---- AC3: aprovacao humana nunca concedida por maquina ---------------------
    dados = env['ir.model.data'].sudo().search([('module', '=', MODULO),  # noqa: F821
                                                ('model', '=', 'ir.model.access')])
    acls = env['ir.model.access'].sudo().browse(dados.mapped('res_id'))  # noqa: F821
    modelos = set(acls.mapped('model_id.model'))
    checar(bool(acls) and modelos == MODELOS_ACL,
           'AC3 superficie de ACL do modulo = allow-list de modelos (%s)' % sorted(modelos))
    nomes = set(dados.mapped('name'))
    checar(nomes == ACLS_ESPERADAS,
           'AC3 ACLs do modulo = allow-list de xmlids (%s)' % sorted(nomes))
    proibidos = {ref('base.group_system').id, ref('base.group_erp_manager').id}
    alcance = set(grupo_vendedor.all_implied_ids.ids) | set(grupo_gestor.all_implied_ids.ids)
    checar(not (alcance & proibidos), 'AC3 nenhum grupo do modulo alcanca administracao do Odoo')
    try:
        a2.with_user(ga).write({'name': '%s promovido' % PREFIXO})
        falhou('AC3 usuario do Sales AI administrou OUTRO usuario')
    except AccessError:
        ok('AC3 usuario do Sales AI nao administra outro usuario (AccessError)')
    try:
        env['ir.rule'].with_user(ga).create({'name': '%s regra plantada' % PREFIXO,  # noqa: F821
                                             'model_id': env['ir.model']._get_id(MODELO),  # noqa: F821
                                             'domain_force': "[(1, '=', 1)]"})
        falhou('AC3 usuario do Sales AI criou regra de acesso')
    except AccessError:
        ok('AC3 usuario do Sales AI nao cria regra de acesso (AccessError)')
    try:
        ga.with_user(ga).write({'group_ids': [(4, ref('base.group_system').id)]})
    except AccessError:
        pass
    checar(ref('base.group_system') not in ga.with_user(ga).group_ids,
           'AC3 tentativa de autopromocao a administrador nao promove')

    resumo()


def resumo():
    print('ACL_ITENS=%s ACL_FALHAS=%s' % (ITENS[0], FALHAS[0]))
    print('ACL_RESULTADO: %s' % ('OK' if FALHAS[0] == 0 else 'FALHOU'))


main()
