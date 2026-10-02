#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Muta uma COPIA do modulo SMTP do Titan para a prova de dente do aceite (TRE-W6-E01-T01).

A prova de dente existe porque "o aceite reprovou" nao prova nada sozinho: o que se exige e que cada
mutacao faca reprovar O ITEM ESPERADO. Este script faz a mutacao textual na copia e confere que ela
de fato aconteceu (se o texto alvo nao existir, ele falha em vez de gerar uma copia intacta e um dente
verde falso).

Uso: python3 scripts/integracoes/mutar_smtp_titan.py --modulo <origem> --destino <copia> --mutacao <nome>
Exit: 0 = copia mutada · 2 = uso/mutacao desconhecida/alvo ausente.
"""

from __future__ import annotations

import argparse
import sys

# nome -> (descricao, item do aceite que DEVE reprovar, [(de, para), ...])
MUTACOES = {
    "sem-guarda-de-host": (
        "remove a guarda HOST_NAO_E_DEV (dev passa a aceitar host real)",
        "item 5 'dev com host real RECUSA'",
        [('        if not loopback(config.host or ""):', "        if False:")],
    ),
    "sem-matriz-porta-tls": (
        "remove a conferencia de coerencia porta x TLS (465/starttls passa)",
        "item 6 'porta 465 com starttls RECUSA'",
        [("        if config.seguranca != MATRIZ_PORTA_TLS[config.porta]:", "        if False:")],
    ),
    "ignora-confirmo": (
        "remove a exigencia de --confirmo (envio sai em dry-run)",
        "item 4 'sem --confirmo nao entrega'",
        [("    if args.enviar and not args.confirmo:", "    if False:")],
    ),
    "senha-sem-mascara": (
        "publica a senha no relatorio e desliga a checagem fail-closed de vazamento",
        "item 7 'a senha nunca aparece'",
        [
            ('            "senha": "<oculta>" if self._senha else "<vazio>",',
             '            "senha": self._senha or "<vazio>",'),
            ("        if self.segredo and len(self.segredo) >= 4 and self.segredo in texto:",
             "        if False:"),
        ],
    ),
    "sem-idempotencia": (
        "desliga a leitura da chave ja enviada (replay duplica)",
        "item 9 'replay nao duplica'",
        [('        if evento.get("chave_idempotencia") == chave and evento.get("resultado") == "ENVIADO" \\',
          '        if False and evento.get("chave_idempotencia") == chave and evento.get("resultado") == "ENVIADO" \\')],
    ),
}


def main() -> int:
    p = argparse.ArgumentParser(description="Mutacao para prova de dente (TRE-W6-E01-T01)")
    p.add_argument("--modulo", required=True)
    p.add_argument("--destino", required=True)
    p.add_argument("--mutacao", required=True)
    p.add_argument("--listar", action="store_true")
    args = p.parse_args()

    if args.listar:
        for nome, (desc, item, _) in MUTACOES.items():
            print(f"{nome}: {desc} -> deve reprovar {item}")
        return 0

    if args.mutacao not in MUTACOES:
        print(f"mutacao desconhecida: {args.mutacao}", file=sys.stderr)
        return 2

    descricao, item_esperado, trocas = MUTACOES[args.mutacao]
    with open(args.modulo, "r", encoding="utf-8") as fh:
        texto = fh.read()

    for de, para in trocas:
        if texto.count(de) != 1:
            print(f"alvo ausente ou ambiguo ({texto.count(de)} ocorrencias): {de[:60]}", file=sys.stderr)
            return 2
        texto = texto.replace(de, para)

    with open(args.destino, "w", encoding="utf-8") as fh:
        fh.write(texto)
    print(f"mutacao={args.mutacao} descricao='{descricao}' item_esperado='{item_esperado}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
