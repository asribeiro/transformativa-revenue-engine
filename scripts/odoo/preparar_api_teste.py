# -*- coding: utf-8 -*-
"""Preparo do ambiente descartavel para a fase HTTP do aceite da API controlada.

Card TRE-W3-E01-T01 (`t_e0489efc`). Roda DENTRO do container descartavel, via `odoo shell`:

    docker run --rm -i -v "$DESC_DIR":/preparo --entrypoint odoo "$IMAGEM" \
        shell -d "$BANCO" --no-http < preparar_api_teste.py

O QUE ELE FAZ (e por que cada coisa):
  1. declara o ambiente (`tf.api.ambiente=dev`) e o caminho da politica (`tf.api.politica`) —
     sem isso a API recusa tudo por desenho (guarda do ADR-005, fail-closed);
  2. cria (ou reaproveita) o usuario de INTEGRACAO, com os grupos do modulo (`E07`) e os grupos
     de vendas do CRM — e' a ACL DELE que o `auth='bearer'` aplica em cada chamada;
  3. gera uma chave de API (`res.users.apikeys`) para esse usuario e grava no arquivo
     `/preparo/chave.txt` (600) — a chave NAO passa por stdout, log, argumento ou evidencia;
  4. commita.

Variaveis de ambiente lidas (com padrao): TRE_API_AMBIENTE (dev), TRE_API_USUARIO (tf_api_integracao),
TRE_API_ARQUIVO_CHAVE (/preparo/chave.txt).
"""

import os

AMBIENTE = os.environ.get("TRE_API_AMBIENTE", "dev")
LOGIN = os.environ.get("TRE_API_USUARIO", "tf_api_integracao")
ARQUIVO_CHAVE = os.environ.get("TRE_API_ARQUIVO_CHAVE", "/preparo/chave.txt")

GRUPOS = ",".join([
    "base.group_user",
    "transformativa_sales_ai.group_tf_sales_ai_user",
    "sales_team.group_sale_salesman",
])

ICP = env["ir.config_parameter"].sudo()  # noqa: F821 - `env` vem do `odoo shell`
ICP.set_param("tf.api.ambiente", AMBIENTE)
ICP.set_param("tf.api.politica", "")  # vazio = politica do proprio modulo
ICP.set_param("tf.api.aprovacao", "")

usuarios = env["res.users"].sudo().with_context(active_test=False)  # noqa: F821
usuario = usuarios.search([("login", "=", LOGIN)], limit=1)
if not usuario:
    usuario = usuarios.create({
        "name": "API Controlada (integracao)",
        "login": LOGIN,
        "email": "%s@tre.local" % LOGIN,
        # Odoo 19 renomeou `groups_id` para `group_ids` em `res.users` (o verificador pegou o
        # `ValueError: Invalid field 'groups_id'` na primeira rodada).
        "group_ids": [(6, 0, [env.ref(xmlid).id for xmlid in GRUPOS.split(",")])],  # noqa: F821
    })

chaves = env["res.users.apikeys"].sudo().with_user(usuario)  # noqa: F821
chave = chaves._generate(scope="rpc", name="api-controlada-tre", expiration_date=None)

with open(ARQUIVO_CHAVE, "w", encoding="utf-8") as fh:
    fh.write(chave)
os.chmod(ARQUIVO_CHAVE, 0o600)

env.cr.commit()  # noqa: F821 - o `odoo shell` roda em transacao propria
print("TF_API_PREPARO_OK usuario=%s ambiente=%s chave_em=%s" % (usuario.login, AMBIENTE, ARQUIVO_CHAVE))
