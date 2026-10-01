#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Criacao e consulta de parceiro com identificadores fortes, pelo ORM (card TRE-W2-E04-T01).

AC3 do card: "Teste de criacao e consulta com dado sintetico".

Este script e' lido pelo stdin do `odoo shell` dentro do container descartavel do aceite —
e' um instrumento INDEPENDENTE dos testes do modulo (`tests/test_res_partner_dedup.py`):
usa outro caminho (shell + ORM direto) para medir o mesmo criterio. Como o `odoo shell` e'
console interativo (excecao imprime traceback e o processo termina com exit 0), o resultado
sai em MARCADORES que o verificador confere — nunca pelo exit code sozinho.

Uso (pelo verificador; o banco vem do `-d` do shell):

    docker run --rm -i --network <rede> -v <conf>:/etc/odoo/odoo.conf:ro \
        -v <modulo>:/mnt/extra-addons/<modulo>:ro --entrypoint odoo odoo:19.0 \
        shell -d <banco> --no-http < scripts/odoo/medir_res_partner.py

Marcadores: `MEDICAO_PARCEIRO`, `MEDICAO_GRAVADO`, `MEDICAO_BUSCA`, `MEDICAO_BUSCA_NEGATIVA`,
`MEDICAO_PRIORITY`, `MEDICAO_ROLLBACK_OK`, `MEDICAO_OK` / `MEDICAO_FALHOU motivo=...`.

NADA FICA NO BANCO: a transacao e' revertida antes de terminar (`MEDICAO_ROLLBACK_OK`).
"""
import uuid

IDENTIFICADORES = (
    ('tf_cnpj', '12.345.678/0001-95'),
    ('tf_domain', 'sintetica.tre.test'),
    ('tf_linkedin_url', 'https://www.linkedin.com/company/tre-sintetica'),
)

concluido = False
try:
    parceiro_modelo = env['res.partner']  # noqa: F821 (env do `odoo shell`)
    canonico = str(uuid.uuid4())
    valores = dict(IDENTIFICADORES)
    valores['tf_company_id'] = canonico
    parceiro = parceiro_modelo.create(dict(
        valores,
        name='Empresa Sintetica TRE-W2-E04-T01 (medicao do aceite)',
        tf_priority_score=87.5,
    ))
    print('MEDICAO_PARCEIRO id=%s' % parceiro.id, flush=True)
    for campo, valor in valores.items():
        gravado = parceiro[campo]
        print('MEDICAO_GRAVADO %s=%s' % (campo, gravado), flush=True)
        if str(gravado) != str(valor):
            raise AssertionError('%s gravado %r != enviado %r' % (campo, gravado, valor))
        achados = parceiro_modelo.search([(campo, '=', valor)])
        print('MEDICAO_BUSCA %s n=%s' % (campo, len(achados)), flush=True)
        if parceiro not in achados:
            raise AssertionError('consulta por %s=%r nao devolveu o parceiro criado'
                                 % (campo, valor))
    negativos = parceiro_modelo.search([('tf_cnpj', '=', '99.999.999/9999-99')])
    print('MEDICAO_BUSCA_NEGATIVA n=%s' % len(negativos), flush=True)
    if negativos:
        raise AssertionError('identificador diferente casou com parceiro (a consulta nao e por igualdade)')
    print('MEDICAO_PRIORITY valor=%s' % parceiro.tf_priority_score, flush=True)
    if abs(parceiro.tf_priority_score - 87.5) > 1e-9:
        raise AssertionError('tf_priority_score lido %r != 87.5' % parceiro.tf_priority_score)
    concluido = True
except Exception as erro:  # medicao falha vira marcador legivel, nunca traceback mudo
    print('MEDICAO_FALHOU motivo=%s' % erro, flush=True)
finally:
    try:
        env.cr.rollback()  # noqa: F821
        print('MEDICAO_ROLLBACK_OK', flush=True)
    except Exception as erro_rollback:
        print('MEDICAO_ROLLBACK_FALHOU motivo=%s' % erro_rollback, flush=True)
        concluido = False

if concluido:
    print('MEDICAO_OK', flush=True)
