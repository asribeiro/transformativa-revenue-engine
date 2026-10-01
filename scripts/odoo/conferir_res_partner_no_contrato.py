#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Confere o modelo `res.partner` do modulo contra o Data Contract V1.0 congelado.

Card: TRE-W2-E04-T01. AC2: "IDs canonicos seguem o Data Contract V1.0".

Este conferidor NAO adivinha nada: ele le' o `docs/data/data_contract_v1.json` (artefato
congelado) e o codigo-fonte do modulo, e confronta os dois item a item:

  * `dedup.strong` (["cnpj", "domain", "linkedin_url"]) -> exige em `res.partner` o campo
    `tf_<nome>` do tipo Char **indexado** (btree), na MESMA ordem de prioridade do contrato
    (CNPJ -> dominio -> LinkedIn);
  * `canonical_ids.odoo_map` restringido as chaves que apontam para `res.partner`
    (`organizations.id` -> `res.partner.tf_company_id`; `priority score` ->
    `res.partner.tf_priority_score`) -> exige o campo com o nome EXATO do contrato;
  * disciplina de nome: todo campo declarado na clase `res.partner` do modulo carrega o
    prefixo `tf_` do contrato (o espelho nao se confunde com os campos do Odoo);
  * deriva (campo exigido pelo contrato ausente; nome com um caractere diferente; campo sem
    indice) e' **FALHOU** — nao existe "aviso que passa".

Uso:

    python3 scripts/odoo/conferir_res_partner_no_contrato.py \
        --contrato docs/data/data_contract_v1.json \
        --modulo-dir odoo/addons/transformativa_sales_ai

Saida: um item por linha (`OK`/`FALHOU`/`INFO`), resumo em uma linha e exit code
(0 = contrato cumprido; 1 = divergencia ou medicao impossivel).
"""
import argparse
import ast
import hashlib
import json
import pathlib
import sys

MODELO = 'res.partner'
PREFIXO = 'tf_'

# Tipo do campo no Odoo -> tipo declarado no contrato, para o que o contrato nomeia.
#   `organizations.id` e' UUID: o Odoo nao tem coluna UUID nativa, entao o espelho guarda o UUID
#   canonico em Char (decisao D2 do runbook do card, provada por teste do modulo);
#   "score de prioridade" e' numerico (Float).
TIPOS_ESPERADOS = {
    'organizations.id': ('tf_company_id', 'char'),
    'priority score': ('tf_priority_score', 'float'),
}
# Identificadores fortes do contrato sao textuais (Char) e indexados.
TIPO_DEDUP = 'char'

ITENS = 0
FALHAS = 0


def ok(mensagem):
    global ITENS
    ITENS += 1
    print('OK    %s' % mensagem)


def falhou(mensagem):
    global ITENS, FALHAS
    ITENS += 1
    FALHAS += 1
    print('FALHOU %s' % mensagem)


def info(mensagem):
    print('INFO  %s' % mensagem)


def campos_do_modelo(diretorio, modelo):
    """Le' os .py do modulo e devolve os campos declarados na clase `_inherit = <modelo>`.

    Retorna {nome: {'tipo': 'char', 'indexado': bool, 'arquivo': ..., 'linha': n}} na ORDEM
    de declaracao no arquivo.
    """
    campos = {}
    arquivos = sorted(pathlib.Path(diretorio).rglob('*.py'))
    if not arquivos:
        return campos, 'nenhum .py encontrado em %s' % diretorio
    erro = None
    for arquivo in arquivos:
        try:
            arvore = ast.parse(arquivo.read_text(encoding='utf-8'))
        except SyntaxError as exc:
            erro = '%s nao e Python valido: %s' % (arquivo, exc)
            continue
        for no in ast.walk(arvore):
            if not isinstance(no, ast.ClassDef):
                continue
            herda = None
            for item in no.body:
                if isinstance(item, ast.Assign) and any(
                    isinstance(alvo, ast.Name) and alvo.id == '_inherit' for alvo in item.targets
                ):
                    try:
                        herda = ast.literal_eval(item.value)
                    except Exception:
                        herda = None
            herdar = herda if isinstance(herda, list) else [herda]
            if modelo not in herdar:
                continue
            for item in no.body:
                if not isinstance(item, ast.Assign) or len(item.targets) != 1:
                    continue
                alvo = item.targets[0]
                if not isinstance(alvo, ast.Name) or not isinstance(item.value, ast.Call):
                    continue
                chamada = item.value.func
                if not (isinstance(chamada, ast.Attribute) and isinstance(chamada.value, ast.Name)
                        and chamada.value.id == 'fields'):
                    continue
                tipo = chamada.attr.lower()
                indexado = any(
                    palavra.arg == 'index' and isinstance(palavra.value, ast.Constant)
                    and palavra.value.value is True
                    for palavra in item.value.keywords
                )
                campos[alvo.id] = {
                    'tipo': tipo,
                    'indexado': indexado,
                    'arquivo': str(arquivo),
                    'linha': item.lineno,
                }
    return campos, erro


def main(argv=None):
    parser = argparse.ArgumentParser(description='Confere res.partner contra o Data Contract V1.0')
    parser.add_argument('--contrato', required=True, help='caminho do data_contract_v1.json')
    parser.add_argument('--modulo-dir', required=True, help='diretorio do modulo Odoo')
    args = parser.parse_args(argv)

    contrato_caminho = pathlib.Path(args.contrato)
    if not contrato_caminho.is_file():
        falhou('contrato ausente: %s' % contrato_caminho)
        return resumo()
    bruto = contrato_caminho.read_bytes()
    try:
        contrato = json.loads(bruto.decode('utf-8'))
    except Exception as exc:
        falhou('contrato ilegivel como JSON: %s' % exc)
        return resumo()
    sha = hashlib.sha256(bruto).hexdigest()
    versao = contrato.get('contract', {}).get('version', '?')
    info('contrato: %s versao=%s sha256=%s' % (contrato_caminho, versao, sha))
    if versao != '1.0':
        falhou('contrato na versao %s (este conferidor implementa a V1.0)' % versao)
    else:
        ok('contrato congelado na versao 1.0 (o conferido e este artefato: sha256 acima)')

    modulo_dir = pathlib.Path(args.modulo_dir)
    if not (modulo_dir / '__manifest__.py').is_file():
        falhou('modulo ausente em %s (__manifest__.py nao encontrado)' % modulo_dir)
        return resumo()

    campos, erro = campos_do_modelo(modulo_dir, MODELO)
    if erro:
        falhou(erro)
    if not campos:
        falhou('nenhum campo declarado na clase _inherit = %s do modulo' % MODELO)
        return resumo()
    info('campos declarados na clase %s: %s' % (MODELO, ', '.join(campos)))

    # --- 1. identificadores fortes de deduplicacao (contrato §5 / dedup.strong) -------------
    fortes = contrato.get('dedup', {}).get('strong', [])
    if not fortes:
        falhou('contrato sem dedup.strong — nada a conferir (medicao impossivel)')
    exigidos = []
    for identificador in fortes:
        nome = PREFIXO + identificador
        exigidos.append(nome)
        if nome not in campos:
            falhou('dedup.strong "%s" -> campo %s AUSENTE em %s' % (identificador, nome, MODELO))
            continue
        campo = campos[nome]
        if campo['tipo'] != TIPO_DEDUP:
            falhou('dedup.strong "%s" -> %s e %s (esperado %s)'
                   % (identificador, nome, campo['tipo'], TIPO_DEDUP))
        elif not campo['indexado']:
            falhou('dedup.strong "%s" -> %s NAO esta indexado (%s:%s)'
                   % (identificador, nome, campo['arquivo'], campo['linha']))
        else:
            ok('dedup.strong "%s" -> %s presente, %s, indexado (%s:%s)'
               % (identificador, nome, campo['tipo'], campo['arquivo'], campo['linha']))
    ordem_contrato = [PREFIXO + i for i in fortes]
    ordem_codigo = [c for c in campos if c in ordem_contrato]
    if ordem_codigo == ordem_contrato:
        ok('ordem de prioridade do contrato preservada na declaracao: %s'
           % ' -> '.join(ordem_contrato))
    else:
        falhou('ordem dos fortes no codigo (%s) != ordem do contrato (%s)'
               % (' -> '.join(ordem_codigo), ' -> '.join(ordem_contrato)))

    # --- 2. IDs canonicos do contrato (canonical_ids.odoo_map) ------------------------------
    odoo_map = contrato.get('canonical_ids', {}).get('odoo_map', {})
    if not odoo_map:
        falhou('contrato sem canonical_ids.odoo_map — nada a conferir (medicao impossivel)')
    nomes_do_contrato = set()
    for chave, valor in sorted(odoo_map.items()):
        marcados = [pedaco.strip().split('.')[-1] for pedaco in str(valor).split('/')
                    if pedaco.strip().startswith(MODELO + '.')]
        if not marcados:
            continue
        nome_esperado, tipo_esperado = TIPOS_ESPERADOS.get(chave, (None, None))
        for nome in marcados:
            nomes_do_contrato.add(nome)
            if nome not in campos:
                falhou('odoo_map "%s" -> %s.%s AUSENTE no modulo' % (chave, MODELO, nome))
                continue
            campo = campos[nome]
            if tipo_esperado and campo['tipo'] != tipo_esperado:
                falhou('odoo_map "%s" -> %s e %s (esperado %s)'
                       % (chave, nome, campo['tipo'], tipo_esperado))
            else:
                ok('odoo_map "%s" -> %s presente e %s (%s:%s)'
                   % (chave, nome, campo['tipo'], campo['arquivo'], campo['linha']))
        if nome_esperado and nome_esperado not in marcados:
            falhou('odoo_map "%s" aponta para %s, mas o esperado deste card e %s'
                   % (chave, valor, nome_esperado))
    if nomes_do_contrato:
        ok('todo nome de campo que o contrato mapeia para %s foi conferido (%d nomes)'
           % (MODELO, len(nomes_do_contrato)))
    else:
        falhou('nenhum nome do odoo_map aponta para %s (medicao impossivel)' % MODELO)

    # --- 3. disciplina de nome e deriva -----------------------------------------------------
    fora_do_prefixo = sorted(n for n in campos if not n.startswith(PREFIXO))
    if fora_do_prefixo:
        falhou('campo(s) em %s sem o prefixo %s do contrato: %s'
               % (MODELO, PREFIXO, ', '.join(fora_do_prefixo)))
    else:
        ok('todo campo declarado na clase %s carrega o prefixo %s (%d campos)'
           % (MODELO, PREFIXO, len(campos)))
    exigidos_todos = set(exigidos) | set(nomes_do_contrato)
    extras = sorted(set(campos) - exigidos_todos)
    if extras:
        info('campos do modulo alem dos exigidos neste card (permitidos, declarados no runbook): %s'
             % ', '.join(extras))
    return resumo()


def resumo():
    if FALHAS == 0:
        print('RESULTADO: CONTRATO_RES_PARTNER_OK (%d itens, 0 falhas)' % ITENS)
        return 0
    print('RESULTADO: CONTRATO_RES_PARTNER_FALHOU (%d itens, %d falha(s))' % (ITENS, FALHAS))
    return 1


if __name__ == '__main__':
    sys.exit(main())
