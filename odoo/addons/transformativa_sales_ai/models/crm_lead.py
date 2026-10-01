# -*- coding: utf-8 -*-
"""Campos de rastreio em `crm.lead` — card TRE-W2-E04-T02.

O QUE ESTE ARQUIVO ENTREGA (nome por nome, com a fonte):

O contrato congelado (`docs/data/DATA_CONTRACT_V1.md` §3 e
`docs/data/data_contract_v1.json` -> `canonical_ids.odoo_map`) **nomeia, em `crm.lead`, dois
campos**:

    | canonico             | Odoo                          |
    |----------------------|-------------------------------|
    | oportunidade canonica| `crm.lead.tf_opportunity_id`  |
    | score de prioridade  | `crm.lead.tf_priority_score`  |

Esses dois nomes sao implementados **exatamente como o contrato os escreve** — quem os le'
(integracao, views, API) encontra o contrato no banco. Os outros **11 campos de rastreio**
deste card nao inventam regra: cada um e' o espelho operacional de um artefato que o proprio
contrato publica (score model §8, vocabulario fechado §7, colunas de correlacao/idempotencia
§3, trilha de sincronizacao e lista de eventos §6). O inventario completo (13 campos) e a
proveniencia item a item estao no runbook `docs/runbooks/odoo-crm-lead-sales-ai.md` §2 e no
registro do card; o conferidor `scripts/odoo/conferir_crm_lead_no_contrato.py` confronta este
arquivo com o `data_contract_v1.json` congelado.

Resumo dos 13 (o mesmo de `CAMPOS_DE_RASTREIO`, medido no aceite):

    1  tf_opportunity_id          contrato §3 (UUID canonico da oportunidade)
    2  tf_priority_score          contrato §3 + §8 (score PRIORITY do funil)
    3  tf_icp_score               contrato §8 (score ICP)
    4  tf_automation_fit_score    contrato §8 (score AUTOMATION_FIT)
    5  tf_buying_signal_score     contrato §8 (score BUYING_SIGNAL)
    6  tf_data_quality_score      contrato §8 (score DATA_QUALITY)
    7  tf_score_version           contrato §8 (score sem versao e' recusado)
    8  tf_priority_tier           contrato §8 (tiering A+/A/B/C/Nurture, derivado do score)
    9  tf_next_best_action        contrato §7 (vocabulario fechado next_best_action)
    10 tf_correlation_id          contrato §3 + §6 (correlacao da integracao)
    11 tf_idempotency_key         contrato §3 + §6 (idempotencia, unica por operacao)
    12 tf_last_sync_at            contrato §6 (trilha de sincronizacao)
    13 tf_last_event_type         contrato §6 (ultimo evento do contrato aplicado)

O QUE ESTE ARQUIVO **NAO** FAZ (lacunas declaradas, para nao inventar regra de negocio):

- **nao** altera nenhum campo padrao de `crm.lead`: os 13 entram aditivos (o AC2 do card e'
  exatamente "o CRM padrao nao quebra");
- **nao** valida faixa de score: o contrato define as FAIXAS (§8, cobrem 0–100), nao uma regra
  de recusa de valor — recusar no Odoo seria regra mais dura que o contrato (mesmo criterio do
  E04-T01 para CNPJ invalido);
- **nao** normaliza nem recalcula score: o calculo, a versao e o historico sao do
  `sales_intelligence` (o contrato da' a `scores` ao PostgreSQL); aqui vive o espelho corrente;
- **nao** cria vocabulario novo: `tf_next_best_action` usa os 9 valores do contrato,
  `tf_priority_tier` usa as faixas do contrato e `tf_last_event_type` recebe os `event_type`
  do contrato (documentados no `help`);
- **nao** cria view, ACL ou regra de registro (`TRE-W2-E06-T01` / `TRE-W2-E07-T01`);
- **nao** finge faixa sem score: `tf_priority_tier` so' existe quando ha' score informado
  (ver `faixa_do_priority_score`).
"""
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Formato do ID canonico do contrato (§3): `id UUID`. Flexivel na versao do UUID de proposito —
# o produtor do fato gera v4, mas o espelho nao recusa um UUID canonico legitimo de outra versao
# (mesmo criterio ja usado em `models/res_partner.py`, card TRE-W2-E04-T01).
FORMATO_UUID = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)

# Tiering do contrato (§8 e `data_contract_v1.json` -> `scores.tiers`): faixas que cobrem 0–100
# sem lacuna e sem sobreposicao. A comparacao e' pelo PISO (min) da primeira faixa que atende.
TIERS_DE_PRIORIDADE = (
    ('A+', 90.0, 100.0),
    ('A', 80.0, 89.99),
    ('B', 65.0, 79.99),
    ('C', 50.0, 64.99),
    ('Nurture', 0.0, 49.99),
)

# Vocabulario fechado do contrato (§7 e `data_contract_v1.json` -> `vocabularies.next_best_action`).
# Fonte unica dos VALORES: o rotulo do Selection e' o proprio valor (e' vocabulario de contrato,
# nao texto de interface).
VOCABULARIO_NEXT_BEST_ACTION = (
    'RESEARCH_MORE',
    'FIND_DECISION_MAKER',
    'SEND_EMAIL',
    'PREPARE_LINKEDIN',
    'WAIT',
    'FOLLOW_UP',
    'CREATE_MEETING',
    'NURTURE',
    'DISQUALIFY',
)

# Eventos do contrato (§6 e `data_contract_v1.json` -> `events.pg_to_odoo` + `events.odoo_to_pg`):
# 6 PostgreSQL -> Odoo + 7 Odoo -> PostgreSQL. `tf_last_event_type` recebe um destes valores.
EVENTOS_DO_CONTRATO = (
    'COMPANY_QUALIFIED',
    'COMPANY_UPDATED',
    'PRIORITY_SCORE_CHANGED',
    'DECISION_MAKER_FOUND',
    'NEXT_BEST_ACTION_CHANGED',
    'OPPORTUNITY_RECOMMENDED',
    'STAGE_CHANGED',
    'ACTIVITY_COMPLETED',
    'MEETING_CREATED',
    'OPPORTUNITY_WON',
    'OPPORTUNITY_LOST',
    'DEAL_VALUE_CHANGED',
    'LOSS_REASON_RECORDED',
)

# Os 13 campos deste card — inventario unico, usado pelo conferidor de contrato, pelos testes e
# pelo aceite. Ordem = ordem do resumo no docstring e do runbook §2.
CAMPOS_DE_RASTREIO = (
    'tf_opportunity_id',
    'tf_priority_score',
    'tf_icp_score',
    'tf_automation_fit_score',
    'tf_buying_signal_score',
    'tf_data_quality_score',
    'tf_score_version',
    'tf_priority_tier',
    'tf_next_best_action',
    'tf_correlation_id',
    'tf_idempotency_key',
    'tf_last_sync_at',
    'tf_last_event_type',
)

# Campos com indice no banco: os tres de BUSCA por identidade/correlacao do contrato (§3 e §6).
# Os scores nao sao indexados: o contrato nao pede busca por score, e o `sales_intelligence` e'
# quem consulta score por score_type (§8).
CAMPOS_INDEXADOS = (
    'tf_opportunity_id',
    'tf_correlation_id',
    'tf_idempotency_key',
)


def faixa_do_priority_score(valor):
    """Devolve a faixa do tiering do contrato (§8) para um `tf_priority_score`.

    Sem score informado (None/0.0) devolve `False`: o contrato define faixas para um SCORE, e o
    espelho nao finge faixa para um lead que nunca recebeu prioridade (`Float` do Odoo nao
    distingue "nulo" de 0.0 — a distincao esta declarada como lacuna no runbook §2).
    Valor fora das faixas cobertas (negativo) tambem devolve `False`: o contrato cobre 0–100 e o
    espelho nao inventa faixa fora disso.
    """
    if not valor:
        return False
    for nome, minimo, _maximo in TIERS_DE_PRIORIDADE:
        if valor >= minimo:
            return nome
    return False


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # --- nomes fixados pelo contrato (§3 -> canonical_ids.odoo_map) -------------------------
    tf_opportunity_id = fields.Char(
        string='UUID canonico (oportunidade)',
        index=True,
        copy=False,
        tracking=True,
        help='ID canonico Transformativa da oportunidade (`crm.lead.tf_opportunity_id` no '
             'Data Contract V1.0 §3, UUID). O Odoo e o DONO da oportunidade canonica; este '
             'campo e o que a liga ao que o sales_intelligence registra sobre ela. Nao '
             'substitui o id interno do Odoo nem e substituido por ele.',
    )
    tf_priority_score = fields.Float(
        string='Priority score',
        copy=False,
        tracking=True,
        help='Score de prioridade do funil (contrato §8: PRIORITY = 0,35*ICP + 0,30*'
             'AUTOMATION_FIT + 0,25*BUYING_SIGNAL + 0,10*DATA_QUALITY, faixa 0–100). Espelho '
             'do valor corrente de `scores` (score_type=PRIORITY); o calculo e o historico '
             'ficam no sales_intelligence.',
    )

    # --- score model do contrato (§8) -------------------------------------------------------
    tf_icp_score = fields.Float(
        string='Score ICP',
        copy=False,
        tracking=True,
        help='Score estrutural de fit com o cliente desejado (contrato §8, score_type=ICP). '
             'Espelho corrente — o calculo e a versao ficam no sales_intelligence.',
    )
    tf_automation_fit_score = fields.Float(
        string='Score Automacao/IA',
        copy=False,
        tracking=True,
        help='Probabilidade de existir oportunidade relevante de automacao/IA (contrato §8, '
             'score_type=AUTOMATION_FIT).',
    )
    tf_buying_signal_score = fields.Float(
        string='Score Sinal de Compra',
        copy=False,
        tracking=True,
        help='Forca e atualidade dos sinais de mudanca/demanda (contrato §8, '
             'score_type=BUYING_SIGNAL).',
    )
    tf_data_quality_score = fields.Float(
        string='Score Qualidade de Dados',
        copy=False,
        tracking=True,
        help='Confiabilidade e completude dos dados (contrato §8, score_type=DATA_QUALITY).',
    )
    tf_score_version = fields.Char(
        string='Versao do score',
        copy=False,
        tracking=True,
        help='Versao do score que produziu os valores deste lead (contrato §8: "score sem '
             'score_version nao e reprodutivel e e recusado"). O espelho guarda a versao raiz '
             'do conjunto de scores aqui refletido.',
    )
    tf_priority_tier = fields.Selection(
        selection=tuple((nome, nome) for nome, _min, _max in TIERS_DE_PRIORIDADE),
        string='Faixa de prioridade',
        compute='_compute_tf_priority_tier',
        store=True,
        help='Faixa do tiering do contrato §8 derivada de `tf_priority_score` (A+ 90–100, '
             'A 80–89,99, B 65–79,99, C 50–64,99, Nurture < 50). Campo derivado: nao ha fonte '
             'propria, so o score. Sem score informado, sem faixa.',
    )

    # --- vocabulario fechado do contrato (§7) ----------------------------------------------
    tf_next_best_action = fields.Selection(
        selection=tuple((valor, valor) for valor in VOCABULARIO_NEXT_BEST_ACTION),
        string='Proxima melhor acao',
        copy=False,
        tracking=True,
        help='Proximo passo recomendado, do vocabulario fechado do contrato §7: '
             'RESEARCH_MORE, FIND_DECISION_MAKER, SEND_EMAIL, PREPARE_LINKEDIN, WAIT, '
             'FOLLOW_UP, CREATE_MEETING, NURTURE, DISQUALIFY. Vem de '
             'NEXT_BEST_ACTION_CHANGED (contrato §6).',
    )

    # --- rastreio da integracao (§3 correlation_columns + §6) ------------------------------
    tf_correlation_id = fields.Char(
        string='Correlacao (agent_runs)',
        index=True,
        copy=False,
        tracking=True,
        help='Correlacao da cadeia de execucao que produziu o ultimo fato espelhado neste '
             'lead (contrato §3: `agent_runs.correlation_id` liga agente -> workflow -> '
             'evento). E' + ' o que permite rastrear o lead ate a execucao que o escreveu.',
    )
    tf_idempotency_key = fields.Char(
        string='Chave de idempotencia',
        index=True,
        copy=False,
        tracking=True,
        help='Chave de idempotencia da ultima operacao de sincronizacao aplicada a este lead '
             '(contrato §3/§6: `sync_events.idempotency_key` e unica por operacao; retry nao '
             'pode criar duplicata).',
    )
    tf_last_sync_at = fields.Datetime(
        string='Ultima sincronizacao',
        copy=False,
        tracking=True,
        help='Instante da ultima sincronizacao aplicada a este lead (contrato §6: trilha de '
             'sincronizacao em `sync_events`). Rastreio temporal do espelho.',
    )
    tf_last_event_type = fields.Char(
        string='Ultimo evento aplicado',
        copy=False,
        tracking=True,
        help='Ultimo evento do contrato aplicado a este lead (contrato §6), um de: '
             + ', '.join(EVENTOS_DO_CONTRATO) + '.',
    )

    @api.depends('tf_priority_score')
    def _compute_tf_priority_tier(self):
        for lead in self:
            lead.tf_priority_tier = faixa_do_priority_score(lead.tf_priority_score)

    @api.constrains('tf_opportunity_id')
    def _check_tf_opportunity_id_uuid(self):
        """`tf_opportunity_id` so' aceita UUID — e' o ID canonico do contrato (§3), nao texto livre."""
        for lead in self:
            valor = (lead.tf_opportunity_id or '').strip()
            if valor and not FORMATO_UUID.match(valor):
                raise ValidationError(
                    'tf_opportunity_id deve ser o UUID canonico da oportunidade (Data Contract '
                    'V1.0 §3); recebido: %r' % lead.tf_opportunity_id
                )
