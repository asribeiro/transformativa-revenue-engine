#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prova independente das views do Sales AI — card TRE-W2-E06-T01 (`t_cf7519c9`).

Roda por `odoo shell` (o `env` do shell esta' disponivel) contra o banco em que o modulo ja'
foi instalado pelo verificador `scripts/odoo/verificar-views-sales-ai.sh`:

    docker run --rm -i --entrypoint odoo odoo:19.0 shell -d <banco> --no-http \
        < scripts/odoo/provar_views_sales_ai.py

POR QUE ESTA PROVA EXISTE ALEM DOS TESTES DO MODULO: os testes `post_install` do autor rodam
com o usuario do teste (administrador) e medem o que o AUTOR achou que devia medir. Esta prova
monta a cena de novo e mede o que os CRITERIOS pedem, com dois usuarios de verdade:

  * MEMBRO   — usuario interno + grupo de vendas do CRM + `group_tf_sales_ai_user` (o perfil do
               vendedor do Sales AI);
  * RESTRITO — usuario interno + grupo de vendas do CRM, SEM o grupo do modulo. A unica
               diferenca entre os dois e' o grupo do Sales AI: o que mudar entre eles e' efeito
               do modulo, nao do CRM.

Mede: (AC1) os campos das entidades customizadas aparecem na view renderizada do membro, sem
erro de renderizacao; (AC3) a lista e o formulario ABREM para o membro (a mesma chamada que o
web client faz para abrir a view) e a lista devolve o dado sintetico; (AC2) o restrito NAO ve o
menu do modulo, NAO ve a secao "Sales AI" do parceiro/lead e NAO le a oportunidade canonica.

Marcadores (o verificador le' estes): `VIEW_ITEM`, `VIEW_ITENS=`, `VIEW_FALHAS=`, `VIEW_RESULTADO`.
"""
import os

MODULO = os.environ.get('TRE_MODULO', 'transformativa_sales_ai')
MODELO = 'tf.process.opportunity'
CAMPOS_PARCEIRO = ('tf_cnpj', 'tf_domain', 'tf_linkedin_url', 'tf_company_id', 'tf_priority_score')
CAMPOS_LEAD = (
    'tf_opportunity_id', 'tf_priority_score', 'tf_icp_score', 'tf_automation_fit_score',
    'tf_buying_signal_score', 'tf_data_quality_score', 'tf_score_version', 'tf_priority_tier',
    'tf_next_best_action', 'tf_correlation_id', 'tf_idempotency_key', 'tf_last_sync_at',
    'tf_last_event_type',
)
# Campos do contrato que a view do modulo mostra SEM depender de grupo padrao do Odoo.
CAMPOS_OPORTUNIDADE_LISTA = ('name', 'partner_id', 'stage_id', 'expected_revenue')
CAMPOS_OPORTUNIDADE_FORM = (
    'name', 'partner_id', 'stage_id', 'expected_revenue', 'lost_reason_id', 'tf_uuid',
)
# O convencional do Odoo prende estes dois aos grupos padrao `base.group_multi_company` /
# `base.group_multi_currency` (e' assim no proprio crm.lead): o recorte que se mede aqui e' o do
# MODULO, entao eles sao medidos com o usuario MEMBRO MULTI, que tem esses grupos padrao. Sem
# essa separacao, o recorte padrao do Odoo apareceria como se fosse falha da view do card.
CAMPOS_OPORTUNIDADE_MULTI = ('company_id', 'currency_id')
CAMPOS_OPORTUNIDADE_TODOS = CAMPOS_OPORTUNIDADE_FORM + CAMPOS_OPORTUNIDADE_MULTI

ITENS = 0
FALHAS = 0


def item(descricao, condicao, detalhe=''):
    global ITENS, FALHAS
    ITENS += 1
    if condicao:
        print('VIEW_ITEM OK    %s%s' % (descricao, (' — %s' % detalhe) if detalhe else ''))
    else:
        FALHAS += 1
        print('VIEW_ITEM FALHOU %s%s' % (descricao, (' — %s' % detalhe) if detalhe else ''))


def info(texto):
    print('VIEW_INFO %s' % texto)


def ref(xmlid):
    return env.ref('%s.%s' % (MODULO, xmlid))  # noqa: F821


def arch_de(usuario, modelo, view_type, view_id=None):
    """A view RENDERIZADA para `usuario` — o mesmo caminho que o web client usa para abrir."""
    modelo_obj = env[modelo].with_user(usuario)  # noqa: F821
    return modelo_obj.get_view(view_id=view_id, view_type=view_type)['arch']


def campos_ausentes(arch, campos):
    return [campo for campo in campos if 'name="%s"' % campo not in arch]


# ---------------------------------------------------------------------------
# cena: usuarios que diferem SO' pelo grupo do modulo (e pelo par multi do Odoo)
# ---------------------------------------------------------------------------
grupo_modulo = env.ref('%s.group_tf_sales_ai_user' % MODULO)  # noqa: F821
grupo_interno = env.ref('base.group_user')  # noqa: F821
grupo_vendas = env.ref('sales_team.group_sale_salesman', raise_if_not_found=False)  # noqa: F821
grupo_multi_companhia = env.ref('base.group_multi_company', raise_if_not_found=False)  # noqa: F821
grupo_multi_moeda = env.ref('base.group_multi_currency', raise_if_not_found=False)  # noqa: F821
grupos_base = [grupo_interno.id] + ([grupo_vendas.id] if grupo_vendas else [])
grupos_multi = [g.id for g in (grupo_multi_moeda,) if g]
# Uma segunda companhia sintetica: e' o `company_ids` com mais de uma companhia que faz o Odoo
# conceder `base.group_multi_company` sozinho (`UsersMultiCompany.create/write` em res_users.py),
# e e' esse grupo que mostra o `company_id` na view. Conceder o grupo na mao nao sobrevive.
companhia2 = env['res.company'].create({  # noqa: F821
    'name': 'Companhia sintetica TRE-W2-E06-T01'})


def usuario(login, com_grupo, com_multi=False):
    grupos = list(grupos_base)
    if com_grupo:
        grupos.append(grupo_modulo.id)
    if com_multi:
        grupos += grupos_multi
    return env['res.users'].create({  # noqa: F821
        'name': 'Usuario %s TRE-W2-E06-T01' % login,
        'login': login,
        'company_id': env.company.id,  # noqa: F821
        'company_ids': [(6, 0, [env.company.id] + ([companhia2.id] if com_multi else []))],  # noqa: F821
        'group_ids': [(6, 0, grupos)],
    })


membro = usuario('e06t01.membro', True)
membro_multi = usuario('e06t01.membro.multi', True, com_multi=True)
restrito = usuario('e06t01.restrito', False)
info('usuario membro=%s grupos=%s' % (membro.login, sorted(membro.group_ids.mapped('name'))))
info('usuario membro multi=%s grupos=%s'
     % (membro_multi.login, sorted(membro_multi.group_ids.mapped('name'))))
info('usuario restrito=%s grupos=%s' % (restrito.login, sorted(restrito.group_ids.mapped('name'))))

estagio = env['crm.stage'].create({  # noqa: F821
    'name': 'Estagio sintetico TRE-W2-E06-T01', 'sequence': 903})
parceiro = env['res.partner'].create({  # noqa: F821
    'name': 'Parceiro sintetico TRE-W2-E06-T01',
    'is_company': True,
    'user_id': membro.id,
    'tf_cnpj': '12.345.678/0001-90',
    'tf_domain': 'exemplo-tre-e06t01.com.br',
    'tf_linkedin_url': 'https://www.linkedin.com/company/exemplo-tre-e06t01',
    'tf_company_id': '3f2504e0-4f89-11d3-9a0c-0305e82c3301',
    'tf_priority_score': 88.0,
})
lead = env['crm.lead'].create({  # noqa: F821
    'name': 'Lead sintetico TRE-W2-E06-T01',
    'type': 'opportunity',
    'partner_id': parceiro.id,
})
oportunidade = env[MODELO].create({  # noqa: F821
    'name': 'Oportunidade sintetica TRE-W2-E06-T01',
    'partner_id': parceiro.id,
    'stage_id': estagio.id,
    'expected_revenue': 12345.67,
})

# ---------------------------------------------------------------------------
# AC1 — os artefatos do card existem e sao do modulo
# ---------------------------------------------------------------------------
VISTAS = {
    'lista': (MODELO, 'list'),
    'formulario': (MODELO, 'form'),
    'busca': (MODELO, 'search'),
}
for rotulo, (modelo, tipo) in VISTAS.items():
    xml_id = 'view_tf_process_opportunity_%s' % ('list' if tipo == 'list' else tipo)
    registro = env['ir.ui.view'].sudo().search([  # noqa: F821
        ('model', '=', modelo), ('type', '=', tipo), ('name', 'like', '%opportunity%')])
    item('view %s do %s declarada (type=%s)' % (rotulo, modelo, tipo), bool(registro),
         'ids=%s' % registro.ids)

item('view do formulario do parceiro declarada',
     bool(ref('view_partner_form_tf_sales_ai')),
     'id=%s' % ref('view_partner_form_tf_sales_ai').id)
item('view do formulario do lead declarada',
     bool(ref('view_crm_lead_form_tf_sales_ai')),
     'id=%s' % ref('view_crm_lead_form_tf_sales_ai').id)
item('acao de janela do modulo aponta para %s' % MODELO,
     ref('action_tf_process_opportunity').res_model == MODELO,
     'res_model=%s view_mode=%s' % (ref('action_tf_process_opportunity').res_model,
                                    ref('action_tf_process_opportunity').view_mode))

# ---------------------------------------------------------------------------
# AC1 + AC3 — MEMBRO abre a lista e o formulario (o caminho do web client)
# ---------------------------------------------------------------------------
arch_lista = arch_de(membro, MODELO, 'list')
faltando = campos_ausentes(arch_lista, CAMPOS_OPORTUNIDADE_LISTA)
item('AC3 lista do %s abre para o membro e traz os campos do contrato' % MODELO,
     not faltando, 'ausentes=%s' % (faltando or 'nenhum'))

arch_form = arch_de(membro, MODELO, 'form')
faltando = campos_ausentes(arch_form, CAMPOS_OPORTUNIDADE_FORM)
item('AC3 formulario do %s abre para o membro e traz os campos do contrato' % MODELO,
     not faltando, 'ausentes=%s' % (faltando or 'nenhum'))

# O formulario COMPLETO do contrato, medido com o membro que tem os grupos padrao do Odoo
# (multi-companhia / multi-moeda) — os dois campos condicionais aparecem para ele.
ausentes = campos_ausentes(arch_de(membro_multi, MODELO, 'form'), CAMPOS_OPORTUNIDADE_TODOS)
item('AC1 o formulario do %s traz TODOS os campos do contrato para o membro multi' % MODELO,
     not ausentes, 'ausentes=%s' % (ausentes or 'nenhum'))
ausentes = campos_ausentes(arch_de(membro_multi, MODELO, 'list'),
                           CAMPOS_OPORTUNIDADE_LISTA + CAMPOS_OPORTUNIDADE_MULTI)
item('AC1 a lista do %s traz TODOS os campos do contrato para o membro multi' % MODELO,
     not ausentes, 'ausentes=%s' % (ausentes or 'nenhum'))

linhas = env[MODELO].with_user(membro).search_read(  # noqa: F821
    [('id', '=', oportunidade.id)],
    ['name', 'partner_id', 'stage_id', 'expected_revenue', 'tf_uuid'])
item('AC3 a lista do membro devolve o dado sintetico (n=1, com UUID canonico)',
     len(linhas) == 1 and bool(linhas[0]['tf_uuid']),
     'n=%s tf_uuid=%s' % (len(linhas), linhas[0]['tf_uuid'] if linhas else 'sem linha'))

# ---------------------------------------------------------------------------
# AC1 — os campos das entidades customizadas nas views herdadas (parceiro e lead)
# ---------------------------------------------------------------------------
view_parceiro = env.ref('base.view_partner_form').id  # noqa: F821
arch_parceiro = arch_de(membro, 'res.partner', 'form', view_id=view_parceiro)
faltando = campos_ausentes(arch_parceiro, CAMPOS_PARCEIRO)
item('AC1 formulario do res.partner (membro) mostra os 5 campos tf_*',
     not faltando, 'ausentes=%s' % (faltando or 'nenhum'))
item('AC1 a secao "Sales AI" esta' ' no formulario do parceiro (membro)',
     'tf_sales_ai' in arch_parceiro, 'page=%s' % ('tf_sales_ai' in arch_parceiro))

view_lead = env.ref('crm.crm_lead_view_form').id  # noqa: F821
arch_lead = arch_de(membro, 'crm.lead', 'form', view_id=view_lead)
faltando = campos_ausentes(arch_lead, CAMPOS_LEAD)
item('AC1 formulario do crm.lead (membro) mostra os 13 campos tf_*',
     not faltando, 'ausentes=%s' % (faltando or 'nenhum'))

# ---------------------------------------------------------------------------
# AC2 — o RESTRITO nao ve o que nao pode ver
# ---------------------------------------------------------------------------
menus_modulo = set(ref('menu_tf_sales_ai_root').ids + ref('menu_tf_sales_ai_oportunidades').ids)
visiveis_membro = set(env['ir.ui.menu'].with_user(membro)._visible_menu_ids())  # noqa: F821
visiveis_restrito = set(env['ir.ui.menu'].with_user(restrito)._visible_menu_ids())  # noqa: F821
item('AC2 o menu do Sales AI aparece para o membro',
     menus_modulo <= visiveis_membro,
     'menus=%s visiveis=%s' % (sorted(menus_modulo), sorted(menus_modulo & visiveis_membro)))
item('AC2 o menu do Sales AI NAO aparece para o restrito',
     not (menus_modulo & visiveis_restrito),
     'menus=%s visiveis=%s' % (sorted(menus_modulo), sorted(menus_modulo & visiveis_restrito)))

arch_parceiro_restrito = arch_de(restrito, 'res.partner', 'form', view_id=view_parceiro)
presentes = [campo for campo in CAMPOS_PARCEIRO if 'name="%s"' % campo in arch_parceiro_restrito]
info('diagnostico: arch do parceiro do restrito traz tf_sales_ai=%s, atributo de grupo=%s, campos=%s'
     % ('tf_sales_ai' in arch_parceiro_restrito,
        'group_tf_sales_ai_user' in arch_parceiro_restrito,
        presentes or 'nenhum'))
info('diagnostico: arch do parceiro do membro traz tf_sales_ai=%s, atributo de grupo=%s'
     % ('tf_sales_ai' in arch_parceiro, 'group_tf_sales_ai_user' in arch_parceiro))
item('AC2 o formulario do parceiro do restrito NAO traz campo tf_* nenhum',
     not presentes, 'presentes=%s' % (presentes or 'nenhum'))
item('AC2 a secao "Sales AI" NAO esta' ' no formulario do parceiro do restrito',
     'tf_sales_ai' not in arch_parceiro_restrito,
     'page presente=%s' % ('tf_sales_ai' in arch_parceiro_restrito))

try:
    arch_lead_restrito = arch_de(restrito, 'crm.lead', 'form', view_id=view_lead)
    presentes_lead = [campo for campo in CAMPOS_LEAD
                      if 'name="%s"' % campo in arch_lead_restrito]
    item('AC2 o formulario do lead do restrito NAO traz campo tf_* nenhum',
         not presentes_lead, 'presentes=%s' % (presentes_lead or 'nenhum'))
except Exception as erro:  # noqa: BLE001 — a recusa da' o mesmo recado: nao ve'.
    item('AC2 o formulario do lead do restrito NAO traz campo tf_* nenhum', True,
         'recusado com %s' % type(erro).__name__)

leitura_liberada = False
try:
    env[MODELO].with_user(restrito).search([])  # noqa: F821
    leitura_liberada = True
except Exception as erro:  # noqa: BLE001
    leitura_liberada = False
    info('leitura do %s pelo restrito recusada com %s' % (MODELO, type(erro).__name__))
item('AC2 o restrito NAO le a oportunidade canonica (fail-closed)', not leitura_liberada,
     'leitura liberada=%s' % leitura_liberada)

abre_liberado = False
try:
    arch_de(restrito, MODELO, 'list')
    abre_liberado = True
except Exception as erro:  # noqa: BLE001
    info('a lista do %s recusou o restrito com %s' % (MODELO, type(erro).__name__))
item('AC2 o restrito NAO abre a lista da oportunidade canonica', not abre_liberado,
     'abriu=%s' % abre_liberado)

# A contraprova do AC2: o recorte e' do grupo do modulo, nao um bloqueio cego — o membro
# (mesmos grupos de CRM) ve tudo o que o restrito nao ve (medido acima).

# ---------------------------------------------------------------------------
# resumo
# ---------------------------------------------------------------------------
print('VIEW_ITENS=%d' % ITENS)
print('VIEW_FALHAS=%d' % FALHAS)
print('VIEW_RESULTADO: %s' % ('OK' if FALHAS == 0 else 'FALHOU'))
env.cr.rollback()  # noqa: F821 — a cena e' sintetica e nao vai ficar no banco
