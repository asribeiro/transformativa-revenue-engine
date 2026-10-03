#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Imprime os campos do `__manifest__.py` de um modulo Odoo, um por linha (`chave=valor`).

Usado pelo verificador `scripts/odoo/verificar-modulo-odoo.sh` (card TRE-W2-E03-T01) para
ler o manifesto REAL em disco — dentro da imagem do Odoo, onde o python3 existe sempre:

    docker run --rm --entrypoint python3 -v <dir>:/modulo:ro -v <este script>:/tmp/m.py:ro \
        odoo:19.0 /tmp/m.py /modulo

Saida (uma linha por par, `depende=` repetido por dependencia):

    nome=transformativa_sales_ai
    versao=19.0.1.0.0
    licenca=LGPL-3
    installable=True
    application=False
    serie=19.0
    depende=base
    depende=crm

Exit 0 quando o manifesto foi lido; 2 quando o diretorio/manifesto nao existe; 3 quando o
manifesto nao e avaliado como literal Python.
"""
import ast
import pathlib
import sys


def main(argv):
    if len(argv) != 2:
        print('uso: manifesto_do_modulo.py <diretorio-do-modulo>', file=sys.stderr)
        return 1
    raiz = pathlib.Path(argv[1])
    if not raiz.is_dir():
        print('ERRO diretorio do modulo nao existe: %s' % raiz, file=sys.stderr)
        return 2
    arquivo = raiz / '__manifest__.py'
    if not arquivo.is_file():
        print('ERRO __manifest__.py ausente em: %s' % raiz, file=sys.stderr)
        return 2
    try:
        manifesto = ast.literal_eval(arquivo.read_text(encoding='utf-8'))
    except Exception as erro:  # manifesto que nao e um literal Python
        print('ERRO manifesto nao e literal Python: %s' % erro, file=sys.stderr)
        return 3
    if not isinstance(manifesto, dict):
        print('ERRO manifesto nao e um dicionario', file=sys.stderr)
        return 3

    versao = str(manifesto.get('version', ''))
    print('nome=%s' % raiz.name)
    print('dir=%s' % raiz.resolve())
    print('versao=%s' % versao)
    print('serie=%s' % '.'.join(versao.split('.')[:2]) if versao else 'serie=')
    print('licenca=%s' % manifesto.get('license', ''))
    print('installable=%s' % manifesto.get('installable', True))
    print('application=%s' % manifesto.get('application', False))
    print('categoria=%s' % manifesto.get('category', ''))
    for dependencia in manifesto.get('depends', []) or []:
        print('depende=%s' % dependencia)
    for arquivo_dado in (manifesto.get('data', []) or []) + (manifesto.get('demo', []) or []):
        print('data=%s' % arquivo_dado)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
