#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Desinstala um modulo Odoo pelo ORM — rollback do card TRE-W2-E03-T01.

Uso (o script e lido pelo stdin do `odoo shell`):

    docker run --rm -i --entrypoint odoo odoo:19.0 shell -d <banco> --no-http \
        < scripts/odoo/desinstalar_modulo.py

O nome do modulo vem de `TRE_MODULO` (padrao: `transformativa_sales_ai`); o banco NAO e
adivinhado aqui (vem do `-d` do shell).

Por que o marcador `DESINSTALACAO_OK`: o `odoo shell` e um console interativo — excecao
dentro do script imprime traceback e o processo termina com exit 0. Quem confere o resultado
e o marcador (e o estado lido no banco), nunca o exit code sozinho.
"""
import os

MODULO = os.environ.get('TRE_MODULO', 'transformativa_sales_ai')

modulo = env['ir.module.module'].search([('name', '=', MODULO)])  # noqa: F821 (env do shell)
if len(modulo) != 1:
    print('DESINSTALACAO_FALHOU motivo=registro_do_modulo_ausente quantidade=%s' % len(modulo))
else:
    estado_antes = modulo.state
    if estado_antes != 'installed':
        print('DESINSTALACAO_FALHOU motivo=modulo_nao_instalado estado_antes=%s' % estado_antes)
    else:
        modulo.button_immediate_uninstall()
        env.cr.commit()  # noqa: F821
        estado_depois = env['ir.module.module'].search(  # noqa: F821
            [('name', '=', MODULO)]).state
        print('DESINSTALACAO_OK estado_antes=%s estado_depois=%s' % (estado_antes, estado_depois))
