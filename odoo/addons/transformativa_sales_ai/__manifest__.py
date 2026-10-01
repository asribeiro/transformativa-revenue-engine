# -*- coding: utf-8 -*-
# Módulo base do Sales AI da Transformativa sobre o Odoo.
#
# Card: TRE-W2-E03-T01 (`t_c536ce86`). Runbook: docs/runbooks/odoo-modulo-sales-ai.md.
#
# Este card entrega o MÓDULO (manifesto, versão, dependências, empacotamento e teste do
# Odoo). Os modelos, campos, views e ACLs entram nas cards seguintes:
#   * TRE-W2-E04-T01/T02 — customização de `res.partner` (dedup) e `crm.lead` (rastreio);
#   * TRE-W2-E05-T01     — modelo `tf.process.opportunity`;
#   * TRE-W2-E06-T01     — views do Sales AI;
#   * TRE-W2-E07-T01     — ACLs/security (carteira × tenant);
#   * TRE-W3-E01-T01     — API controlada.
# Por isso o módulo nasce sem modelo nem data file: `depends` já declara `crm` (o alvo das
# customizações) e a série da versão acompanha a do Odoo em dev (19.0).
{
    'name': 'Transformativa Sales AI',
    'version': '19.0.1.0.0',
    'summary': 'Base do Sales AI da Transformativa sobre o CRM do Odoo',
    'description': """
Transformativa Sales AI
=======================

Modulo base das customizacoes e da integracao do Sales AI da Transformativa
no Odoo Community.

Escopo deste modulo (card TRE-W2-E03-T01): o proprio modulo — manifesto,
versao, dependencias e teste. Os modelos, campos, views e regras de acesso
chegam nas cards TRE-W2-E04-T01/T02, TRE-W2-E05-T01, TRE-W2-E06-T01,
TRE-W2-E07-T01 e TRE-W3-E01-T01, todas penduradas nesta base.

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
    ],
    'data': [],
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
