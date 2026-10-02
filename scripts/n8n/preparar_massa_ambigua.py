#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Massa de teste da AMBIGUIDADE — card TRE-W3-E02-T01.

Dois parceiros com o MESMO dominio de e-mail e identidades canonicas DIFERENTES. Um evento
novo que traz so' o dominio casa com os dois: pela politica da API controlada isso e'
`valor_ambiguo` (409) — o consumidor tem de registrar DEAD_LETTER e NAO tentar escolher um
deles por conta propria (adivinhar identidade e' o que o contrato proibe).

Roda por `odoo shell` (env do ORM), dentro do Odoo DESCARTÁVEL do aceite:
    docker run --rm -i ... --entrypoint odoo odoo:19.0 shell -d <banco> --no-http < este_arquivo
Entradas por ambiente: TRE_MASSA_DOMINIO, TRE_MASSA_IDS (uuids separados por virgula).
"""
import os

DOMINIO = os.environ.get("TRE_MASSA_DOMINIO", "ambiguo.example")
IDS = [i.strip() for i in os.environ.get("TRE_MASSA_IDS", "").split(",") if i.strip()]

if not IDS:
    raise SystemExit("MASSA_AMBIGUA_ERRO: TRE_MASSA_IDS vazio")

criados = 0
for indice, ident in enumerate(IDS):
    existente = env["res.partner"].search([("tf_company_id", "=", ident)], limit=1)  # noqa: F821
    if existente:
        continue
    env["res.partner"].create({  # noqa: F821
        "name": "Ambiguo %d (massa do aceite TRE-W3-E02-T01)" % (indice + 1),
        "is_company": True,
        "tf_company_id": ident,
        "tf_domain": DOMINIO,
    })
    criados += 1

env.cr.commit()  # noqa: F821
total = env["res.partner"].search_count([("tf_domain", "=", DOMINIO)])  # noqa: F821
print("MASSA_AMBIGUA_OK criados=%d total_com_dominio=%d dominio=%s ids=%s"
      % (criados, total, DOMINIO, ",".join(IDS)))
