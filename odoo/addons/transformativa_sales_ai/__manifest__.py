# -*- coding: utf-8 -*-
# Módulo do Sales AI da Transformativa sobre o Odoo.
#
# Card de origem: TRE-W2-E03-T01 (`t_c536ce86`). Runbook: docs/runbooks/odoo-modulo-sales-ai.md.
# Conteúdo atual: TRE-W2-E05-T01 (`t_9c91ecce`) — modelo `tf.process.opportunity`
# (runbook docs/runbooks/odoo-oportunidade-canonica.md) —, TRE-W2-E07-T01 (`t_e0b1bcbf`) —
# ACLs/regras de segurança (carteira × tenant; runbook docs/runbooks/odoo-acl-seguranca.md) — e
# TRE-W2-E06-T01 (`t_cf7519c9`) — views do Sales AI (runbook docs/runbooks/odoo-views-sales-ai.md).
#
# A base (manifesto, versão, dependências, empacotamento) é do E03. Os demais conteúdos
# entram nas cards seguintes:
#   * TRE-W2-E04-T01/T02 — customização de `res.partner` (dedup) e `crm.lead` (rastreio);
#   * TRE-W2-E05-T01     — modelo `tf.process.opportunity` (FEITO);
#   * TRE-W2-E06-T01     — views do Sales AI (FEITO);
#   * TRE-W2-E07-T01     — ACLs/security (carteira × tenant) (FEITO);
#   * TRE-W3-E01-T01     — API controlada.
# `depends` já declara `crm` (o alvo das customizações e o dono do funil) e a série da versão
# acompanha a do Odoo em dev (19.0). A versão não sobe neste card (decisão D6 do runbook §1):
# o módulo não está instalado em nenhum ambiente persistente e três cards da mesma onda editam
# este arquivo em paralelo.
{
    'name': 'Transformativa Sales AI',
    'version': '19.0.1.0.0',
    'summary': 'Base do Sales AI da Transformativa sobre o CRM do Odoo',
    'description': """
Transformativa Sales AI
=======================

Modulo do Sales AI da Transformativa sobre o Odoo Community: as customizacoes e
a integracao do Sales AI.

Conteudo de hoje: o modelo `tf.process.opportunity` — a oportunidade canonica
do lado Odoo (card TRE-W2-E05-T01), com vinculo a `res.partner` e a identidade
canonica em UUID —, as ACLs/regras de seguranca do modulo (card
TRE-W2-E07-T01): grupos de vendedor/gestor, isolamento por tenant
(`res.company`) e por carteira (`res.partner.user_id`), e as views do Sales AI
(card TRE-W2-E06-T01): lista/formulario/busca da oportunidade canonica, o menu
do modulo e a secao "Sales AI" nos formularios de `res.partner` e `crm.lead`. A
base do modulo (manifesto, versao, dependencias, testes) e do card
TRE-W2-E03-T01; os campos de `res.partner`/`crm.lead` sao dos cards
TRE-W2-E04-T01/T02 e a API entra no TRE-W3-E01-T01.

Regra de ambiente (ADR-005): nada nasce em producao. O modulo e desenvolvido e
medido em dev; homologacao e producao exigem aprovacao humana registrada.
""",
    'author': 'Transformativa',
    'website': 'https://transformativa.com.br',
    'license': 'LGPL-3',
    'category': 'Sales/Sales',
    'depends': [
        'base',
        # alvo das customizacoes (crm.lead / crm.stage / crm.team) — declarado desde a base
        # para que a dependencia seja explicita e resolvivel no ambiente de dev
        'crm',
        # Card TRE-W3-E01-T05: o modulo agora INHERITA `mail.activity` (ancora da atividade e
        # campos de rastreio). O `crm` ja' traz o `mail`, mas dependencia de modelo herdado se
        # declara: dependencia implicita e' a que quebra quando o de cima mudar.
        'mail',
    ],
    'data': [
        # Grupos, regras de registro (carteira × tenant) e ACLs do modelo do módulo.
        # Ordem: XML (grupos e regras) antes do CSV (ACLs que referenciam os grupos).
        'security/transformativa_sales_ai_security.xml',
        'security/ir.model.access.csv',
        # Views do Sales AI (card TRE-W2-E06-T01). Depois de `security/`: o menu e as seções
        # "Sales AI" referenciam os grupos criados acima. A view do parceiro e a do lead são
        # extensões das views de `base`/`crm` (dependências já carregadas).
        'views/tf_process_opportunity_views.xml',
        'views/res_partner_views.xml',
        'views/crm_lead_views.xml',
    ],
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
