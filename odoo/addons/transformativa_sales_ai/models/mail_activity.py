# -*- coding: utf-8 -*-
"""Ancoragem e rastreio da ATIVIDADE (`mail.activity`) — card TRE-W3-E01-T05 (`t_cb615018`).

O QUE ESTE ARQUIVO ACRESCENTA AO `mail.activity` DO ODOO, E POR QUE:

  1. ANCORAGEM — o Odoo 19 tornou `res_model` um campo RELATED (`res_model_id.model`, `store=True`,
     `readonly=True`) e NAO deu inverse a ele. Medido no dev, com `odoo shell`:
       * `create({'res_model': 'res.partner', 'res_id': n, ...})` NAO escreve a ancora — a coluna
         `res_model` vai NULL e o INSERT morre na CHECK
         `mail_activity_check_res_id_is_set_if_model` ("Activities have to be linked to records");
       * pior que o erro: o ORM DESCARTA EM SILENCIO o valor que o chamador mandou. E' exatamente o
         que a regra da casa proibe ("nunca ignorar o que o chamador disse", a mesma razao de
         `campo_fixo_divergente`).
     O `res_model_id` (Many2one para `ir.model`) resolve — mas com um ID DE BANCO, que muda de
     ambiente para ambiente: nao serve de valor declarado na politica, que e' artefato versionado.
     O proprio `mail.activity` ja' resolve o NOME do modelo para `res_model_id` no seu
     `default_get` (idioma da casa). Aqui a MESMA traducao vale no `create`: o valor declarado na
     politica e' o NOME do modelo (`res_model`), resolvido no `ir.model` do banco EM USO.
     Divergencia entre `res_model` e `res_model_id` no MESMO vals e' recusa nomeada (fail-closed),
     nunca escolha silenciosa.
  2. RASTREIO — os dois campos de correlacao do contrato (§§3/6 do doc 13 e §7 do doc 06): a chave
     de idempotencia e a correlacao da chamada ficam REGISTRADAS na atividade criada, e nao so' na
     trilha `TF_API_AUDIT` (o motor devolve as duas no plano "para ser registrada"). Mesmo padrao dos
     campos de rastreio que o card `TRE-W2-E04-T02` pos em `crm.lead`.

O QUE ESTE ARQUIVO NAO FAZ (declarado):
  * nao autentica, nao valida politica e nao monta plano: quem DECIDE e' o motor (`api/motor.py`) e
    quem EXECUTA e' o controlador (`controllers/api_controlada.py`). Aqui so' vive a traducao da
    ancora e os dois campos de rastreio;
  * nao autoriza ancorar em OUTRO modelo: quem fixa `res_model` e' a politica (`valores_fixos`) e o
    valor divergente do chamador e' recusado ANTES, no motor (`campo_fixo_divergente`, 422) — medido
    no aceite deste card (item da superficie fechada e dente 2);
  * nao muda o comportamento de quem ja' usa `mail.activity` pelo caminho padrao: o override so'
    age quando `res_model` chega em `vals` (o caminho padrao do Odoo manda `res_model_id`, resolvido
    no proprio `activity_schedule`).

LACUNA DECLARADA (roteada, nao silenciada): a operacao declara UMA ancora (`res.partner`, o
documento que EXISTE no fluxo de fundacao do E2E #001 — doc 08 §3 passos 13..15: empresa, contato e
*depois* atividade). Atividade ancorada em `crm.lead` NAO e' servida por esta versao: `res_model`
divergente e' recusa nomeada (`campo_fixo_divergente`), nunca atividade no lugar errado. Aceitar um
CONJUNTO declarado de ancoras exige vocabulario novo no mecanismo de valor fixo — dono: card
`TRE-W3-E02-T02` / quem e' dono do vocabulario da escrita, nao este card (mesma fronteira registrada
no plano do card TRE-W3-E01-T05 §1 e no defeito `t_6c8ad8bb`).
"""

from odoo import api, fields, models
from odoo.exceptions import UserError


class MailActivity(models.Model):
    """`mail.activity` + a ancora por nome de modelo e os dois campos de rastreio do contrato."""

    _inherit = "mail.activity"

    tf_idempotency_key = fields.Char(
        string="Chave de idempotencia (Sales AI)",
        index=True,
        help="Chave de idempotencia da chamada da API controlada que criou a atividade "
             "(`idempotency_key` do envelope). O que garante 'nao duplicar' e' o motor de "
             "deduplicacao por chave (card TRE-W3-E02-T02); aqui a chave fica REGISTRADA no "
             "registro criado, alem da trilha TF_API_AUDIT.",
    )
    tf_correlation_id = fields.Char(
        string="Correlacao (Sales AI)",
        index=True,
        help="Identificador de correlacao da chamada da API controlada (`correlation_id` do "
             "envelope), ecoado na resposta e registrado na atividade para a reconciliacao "
             "(contrato §3/§6: preservar audit trail).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Traduz a ancora declarada (`res_model`, NOME do modelo) para `res_model_id` do banco.

        Só age quando `res_model` chega em `vals` — o caminho padrao do Odoo manda `res_model_id`.
        """
        for vals in vals_list:
            self._tf_resolver_ancora(vals)
        return super().create(vals_list)

    @api.model
    def _tf_resolver_ancora(self, vals):
        nome = vals.pop("res_model", None)
        if not nome:
            return
        modelo = self.env["ir.model"]._get(nome)
        if not modelo:
            raise UserError(
                "ancora desconhecida: modelo %r nao existe neste banco (a politica declara o "
                "modelo-alvo da atividade)" % (nome,)
            )
        atual = self._tf_id_de(vals.get("res_model_id"))
        if atual and atual != modelo.id:
            raise UserError(
                "ancora divergente no mesmo pedido: res_model %r (ir.model %s) x res_model_id %s "
                "— a API nao escolhe modelo-alvo por conta propria" % (nome, modelo.id, atual)
            )
        vals["res_model_id"] = modelo.id

    @api.model
    def _tf_id_de(self, valor):
        if not valor:
            return 0
        if isinstance(valor, models.BaseModel):
            return valor.id
        try:
            return int(valor)
        except (TypeError, ValueError):
            return 0
