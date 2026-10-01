#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conferidor: o modulo `transformativa_sales_ai` bate com o Data Contract V1.0? (TRE-W2-E04-T02)

Este script NAO escreve nada: ele LE o contrato congelado
(`docs/data/data_contract_v1.json`) e o modelo do card (`models/crm_lead.py`) e confronta os
dois item a item. E' o que impede o aceite de ser auto-referente quanto ao conteudo do
contrato (o verificador de banco confronta banco x modulo; este confronta modulo x contrato).

O que ele confere (fontes: `docs/data/DATA_CONTRACT_V1.md` §3, §6, §7 e §8):

1. **nomes que o contrato escreve em `crm.lead`** (`canonical_ids.odoo_map`) estao declarados
   no modelo — lidos do JSON, nunca fixos aqui;
2. **os 5 score types do contrato** (§8) tem campo espelho `tf_<tipo>_score` no modelo;
3. **o tiering** (§8 `scores.tiers`) e' o mesmo do modelo (nomes e pisos), e cobre 0–100 sem
   lacuna e sem sobreposicao;
4. **o vocabulario fechado** `next_best_action` (§7) e' o mesmo do modelo, valor a valor;
5. **os 13 eventos do contrato** (§6: 6 PG->Odoo + 7 Odoo->PG) sao os do modelo;
6. **o inventario de campos do card** (`CAMPOS_DE_RASTREIO`) e' exatamente o implementado —
   nenhum `tf_` a mais, nenhum a menos;
7. **campos indexados** = os declarados pelo card (`CAMPOS_INDEXADOS`), todos com `index=True`;
8. **ID canonico e' UUID** (§3): `tf_opportunity_id` tem constraint de formato.

Uso:
    python3 scripts/odoo/conferir_crm_lead_no_contrato.py
    python3 scripts/odoo/conferir_crm_lead_no_contrato.py --contrato <json> --modulo-dir <dir>

Saida: um item por linha (`OK`/`FALHOU`), resumo em uma linha e exit code 0 (tudo OK) / 1.
"""
import argparse
import json
import pathlib
import re
import sys

AQUI = pathlib.Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent

ITENS = 0
FALHAS = 0


def ok(msg):
    global ITENS
    ITENS += 1
    print('OK    %s' % msg)


def falhou(msg):
    global ITENS, FALHAS
    ITENS += 1
    FALHAS += 1
    print('FALHOU %s' % msg)


def le_texto(caminho):
    return pathlib.Path(caminho).read_text(encoding='utf-8')


def campos_declarados(texto):
    """{nome: tipo} das declaracoes `tf_x = fields.Tipo(` no arquivo do modelo."""
    declarados = {}
    for m in re.finditer(r'^\s*(tf_[a-z0-9_]+) = fields\.(\w+)\(', texto, re.M):
        declarados[m.group(1)] = m.group(2)
    return declarados


def tupla_de_strings(texto, nome_constante):
    """Extrai uma tupla de strings simples: NOME = ('a', 'b', ...)."""
    m = re.search(r'^%s = \((.*?)\)\s*$' % re.escape(nome_constante), texto, re.M | re.S)
    if not m:
        return None
    return tuple(re.findall(r"'([^']+)'", m.group(1)))


def tuplas_de_paridade(texto, nome_constante):
    """Extrai uma tupla de tuplas com strings/numeros: (('A+', 90.0, 100.0), ...)."""
    m = re.search(r'^%s = \((.*?)\n\)\s*$' % re.escape(nome_constante), texto, re.M | re.S)
    if not m:
        return None
    itens = []
    for linha in re.finditer(r"\(\s*'([^']+)'\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)", m.group(1)):
        itens.append((linha.group(1), float(linha.group(2)), float(linha.group(3))))
    return tuple(itens)


def linhas_com_index_true(texto):
    """Campos `tf_x` cuja declaracao tem `index=True` (bloco entre `fields.Tipo(` e `)`)."""
    com_indice = []
    for m in re.finditer(r'^\s*(tf_[a-z0-9_]+) = fields\.\w+\(', texto, re.M):
        inicio = m.end()
        fim = texto.find('\n    )', inicio)
        bloco = texto[inicio:fim if fim > 0 else len(texto)]
        if re.search(r'index=True', bloco):
            com_indice.append(m.group(1))
    return com_indice


def main():
    p = argparse.ArgumentParser(description='Confronto modulo crm.lead x Data Contract V1.0')
    p.add_argument('--contrato', default=str(RAIZ / 'docs/data/data_contract_v1.json'))
    p.add_argument('--modulo-dir', default=str(RAIZ / 'odoo/addons/transformativa_sales_ai'))
    args = p.parse_args()

    contrato_txt = le_texto(args.contrato)
    contrato = json.loads(contrato_txt)
    modelo_txt = le_texto(pathlib.Path(args.modulo_dir) / 'models/crm_lead.py')
    declarados = campos_declarados(modelo_txt)
    print('INFO  contrato: %s (versao %s, congelado em %s)'
          % (args.contrato, contrato['contract']['version'], contrato['contract']['frozen_at']))
    print('INFO  modulo:   %s/models/crm_lead.py (%d campos tf_ declarados)'
          % (args.modulo_dir, len(declarados)))

    # 1. nomes que o contrato escreve em crm.lead (lidos do JSON)
    nomes_contrato = set()
    for chave, valor in contrato['canonical_ids']['odoo_map'].items():
        for m in re.finditer(r'crm\.lead\.([a-zA-Z0-9_]+)', str(valor)):
            nomes_contrato.add(m.group(1))
    if nomes_contrato:
        ok('contrato nomeia %d campo(s) em crm.lead: %s'
           % (len(nomes_contrato), ', '.join(sorted(nomes_contrato))))
    else:
        falhou('contrato nao nomeia nenhum campo em crm.lead (canonical_ids.odoo_map)')
    for nome in sorted(nomes_contrato):
        if nome in declarados:
            ok('campo nomeado pelo contrato declarado no modulo: %s' % nome)
        else:
            falhou('campo nomeado pelo contrato AUSENTE no modulo: %s' % nome)

    # 2. os 5 score types do contrato (§8) tem espelho no modelo
    for tipo in contrato['scores']['types']:
        esperado = 'tf_%s_score' % tipo.lower()
        if esperado in declarados:
            ok('score_type %s tem espelho %s (%s)' % (tipo, esperado, declarados[esperado]))
        else:
            falhou('score_type %s sem espelho no modelo (esperado %s)' % (tipo, esperado))

    # 3. tiering (§8): modelo == contrato, e o contrato cobre 0-100 sem lacuna/sobreposicao
    tiers_contrato = tuple((t['name'], float(t['min']), float(t['max']))
                           for t in contrato['scores']['tiers'])
    tiers_modelo = tuplas_de_paridade(modelo_txt, 'TIERS_DE_PRIORIDADE')
    if tiers_modelo is None:
        falhou('nao achei TIERS_DE_PRIORIDADE no modelo')
    elif set(tiers_modelo) == set(tiers_contrato):
        ok('tiering do modelo == tiering do contrato (%d faixas: %s)'
           % (len(tiers_modelo), ', '.join(t[0] for t in tiers_modelo)))
    else:
        falhou('tiering do modelo != contrato: modelo=%s contrato=%s'
               % (sorted(tiers_modelo), sorted(tiers_contrato)))
    faixas = sorted(tiers_contrato, key=lambda t: t[1])
    lacunas = []
    for anterior, seguinte in zip(faixas, faixas[1:]):
        if round(seguinte[1] - anterior[2], 2) != 0.01:
            lacunas.append('%s..%s -> %s..%s' % (anterior[0], anterior[2], seguinte[0], seguinte[1]))
    if lacunas:
        falhou('tiering do contrato com lacuna/sobreposicao: %s' % '; '.join(lacunas))
    else:
        ok('tiering do contrato cobre 0-100 sem lacuna e sem sobreposicao')
    if faixas and faixas[0][1] == 0.0 and faixas[-1][2] == 100.0:
        ok('tiering comeca em 0.0 e termina em 100.0 (contrato §8)')
    else:
        falhou('tiering nao cobre 0-100 (inicio=%s fim=%s)'
               % (faixas[0][1] if faixas else '?', faixas[-1][2] if faixas else '?'))

    # 4. vocabulario next_best_action (§7)
    vocab_contrato = tuple(contrato['vocabularies']['next_best_action'])
    vocab_modelo = tupla_de_strings(modelo_txt, 'VOCABULARIO_NEXT_BEST_ACTION')
    if vocab_modelo is None:
        falhou('nao achei VOCABULARIO_NEXT_BEST_ACTION no modelo')
    elif set(vocab_modelo) == set(vocab_contrato):
        ok('vocabulario next_best_action do modelo == contrato (%d valores)' % len(vocab_modelo))
    else:
        falhou('vocabulario do modelo != contrato: faltando=%s sobrando=%s'
               % (sorted(set(vocab_contrato) - set(vocab_modelo)),
                  sorted(set(vocab_modelo) - set(vocab_contrato))))

    # 5. eventos do contrato (§6)
    eventos_contrato = tuple(contrato['events']['pg_to_odoo'] + contrato['events']['odoo_to_pg'])
    eventos_modelo = tupla_de_strings(modelo_txt, 'EVENTOS_DO_CONTRATO')
    if eventos_modelo is None:
        falhou('nao achei EVENTOS_DO_CONTRATO no modelo')
    elif set(eventos_modelo) == set(eventos_contrato):
        ok('os %d eventos do contrato (§6: %d PG->Odoo + %d Odoo->PG) sao os do modelo'
           % (len(eventos_modelo), len(contrato['events']['pg_to_odoo']),
              len(contrato['events']['odoo_to_pg'])))
    else:
        falhou('eventos do modelo != contrato: faltando=%s sobrando=%s'
               % (sorted(set(eventos_contrato) - set(eventos_modelo)),
                  sorted(set(eventos_modelo) - set(eventos_contrato))))

    # 6. inventario do card == implementado
    inventario = tupla_de_strings(modelo_txt, 'CAMPOS_DE_RASTREIO')
    if inventario is None:
        falhou('nao achei CAMPOS_DE_RASTREIO no modelo')
    else:
        if len(set(inventario)) == len(inventario):
            ok('inventario do card sem repeticao (%d campos)' % len(inventario))
        else:
            falhou('inventario do card com campo repetido: %s' % (inventario,))
        faltando = sorted(set(inventario) - set(declarados))
        sobrando = sorted(set(declarados) - set(inventario))
        if not faltando and not sobrando:
            ok('inventario do card == campos tf_ implementados (%d)' % len(declarados))
        else:
            falhou('inventario != implementado: faltando=%s sobrando=%s' % (faltando, sobrando))
        if not (nomes_contrato - set(inventario)):
            ok('todo campo nomeado pelo contrato esta dentro do inventario do card')

    # 7. campos indexados
    indexados_card = tupla_de_strings(modelo_txt, 'CAMPOS_INDEXADOS')
    com_indice = linhas_com_index_true(modelo_txt)
    if indexados_card is None:
        falhou('nao achei CAMPOS_INDEXADOS no modelo')
    elif set(indexados_card) == set(com_indice):
        ok('campos indexados do modelo == declarados pelo card (%s)' % ', '.join(sorted(com_indice)))
    else:
        falhou('indices divergem: card=%s implementado=%s'
               % (sorted(indexados_card), sorted(com_indice)))

    # 8. ID canonico e' UUID (§3)
    if re.search(r"@api\.constrains\('tf_opportunity_id'\)", modelo_txt):
        ok('tf_opportunity_id tem constraint de formato (UUID canonico — contrato §3)')
    else:
        falhou('tf_opportunity_id sem constraint de UUID (contrato §3 exige UUID como ID canonico)')

    print('---')
    if FALHAS == 0:
        print('RESULTADO: CONFERIDOR_CRM_LEAD_OK (%d itens, 0 falhas)' % ITENS)
        return 0
    print('RESULTADO: CONFERIDOR_CRM_LEAD_FALHOU (%d itens, %d falha(s))' % (ITENS, FALHAS))
    return 1


if __name__ == '__main__':
    sys.exit(main())
