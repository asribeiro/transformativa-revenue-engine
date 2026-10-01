# -*- coding: utf-8 -*-
# Módulo do Sales AI da Transformativa sobre o Odoo.
#
# Card de origem: TRE-W2-E03-T01 (`t_c536ce86`). Runbook: docs/runbooks/odoo-modulo-sales-ai.md.
# Conteúdo atual: TRE-W2-E05-T01 (`t_9c91ecce`) — modelo `tf.process.opportunity`
# (runbook docs/runbooks/odoo-oportunidade-canonica.md).
#
# A base (manifesto, versão, dependências, empacotamento) é do E03. Os demais conteúdos
# entram nas cards seguintes:
#   * TRE-W2-E04-T01/T02 — customização de `res.partner` (dedup) e `crm.lead` (rastreio);
#   * TRE-W2-E05-T01     — modelo `tf.process.opportunity` (FEITO);
#   * TRE-W2-E06-T01     — views do Sales AI;
#   * TRE-W2-E07-T01     — ACLs/security (carteira × tenant);
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
canonica em UUID. A base do modulo (manifesto, versao, dependencias, testes) e
do card TRE-W2-E03-T01; os campos de `res.partner`/`crm.lead`, as views, as
regras de acesso e a API entram nas cards TRE-W2-E04-T01/T02, TRE-W2-E06-T01,
TRE-W2-E07-T01 e TRE-W3-E01-T01.

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
