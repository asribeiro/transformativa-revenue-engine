#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prova a inferencia de DOMINIO SENSIVEL por vocabulario proprio e casamento exato.

Correcao de 30/09/2026 (achado do card TRE-W0-E04-T11): a inferencia de dominio herdava
`CONCEITOS_DE_ACAO`, cujo casamento tolera PREFIXO de 4 caracteres. Efeito medido: prosa
legitima de card nascia escalada — "implementador" ~ "implantacao", "versionado" ~
"versao", "entrar" ~ "entrega", "entrega" (de projeto) ~ release, "registro" (de
artefatos) ~ dado de cliente.

O que esta suite trava:
  1. FALSO POSITIVO: termo tecnico de card de desenvolvimento nao declara dominio;
  2. VERDADEIRO POSITIVO: vocabulario de producao/release/credencial/dado de cliente
     continua declarando dominio (o fail-closed do D07 nao foi afrouxado);
  3. REGRESSAO: `CONCEITOS_DE_ACAO` e `REGRAS_DE_ACAO_HUMANA` NAO foram tocadas — acao
     exclusiva continua resolvendo para codigo de decisao humana ("exclusao de registro
     de auditoria" segue escalando, mesmo com "registro" fora do dominio inferido).

Uso: python scripts/verificar_dominios_sensiveis.py [--autoteste]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from hermes.jev.routing import router as r  # noqa: E402

ITENS: list[tuple[str, bool]] = []


def item(descricao: str, ok: bool, detalhe: str = "") -> None:
    ITENS.append((f"{descricao}{(' — ' + detalhe) if detalhe else ''}", bool(ok)))
    print(f"  [{'OK' if ok else 'FALHA'}] {descricao}{(' — ' + detalhe) if detalhe else ''}")


def dominios(texto: str) -> set:
    return r._dominios_sensiveis_do_texto(texto)


def checar() -> None:
    # 1. FALSO POSITIVO — termo tecnico de card nao declara dominio nenhum.
    for texto in ("implementador", "versionado", "entrar", "entrega de projeto",
                  "registro de artefatos", "catalogo de artefatos", "mover o card para ready"):
        item(f"sem dominio: {texto!r}", dominios(texto) == set(),
             f"declarou {sorted(dominios(texto))}")

    # 2. VERDADEIRO POSITIVO — o fail-closed segue de pe.
    for texto, esperado in (
        ("publicar em producao", "producao_ou_release"),
        ("deploy do servico", "producao_ou_release"),
        ("implantacao no ambiente", "producao_ou_release"),
        ("rollback da release", "producao_ou_release"),
        ("rotacionar a credencial", "credencial"),
        ("token de API exposto", "credencial"),
        ("dados do cliente", "dado_de_cliente"),
        ("cadastro do titular", "dado_de_cliente"),
        ("rotacionar as credenciais", "credencial"),
        ("dados de clientes do cadastro", "dado_de_cliente"),
        ("publicar nas releases de implantacoes", "producao_ou_release"),
        ("envio de propostas para leads", "outbound_a_terceiro"),
        ("envio de proposta para o lead", "outbound_a_terceiro"),
        # "versao"/"promover" continuam no vocabulario de release (herdados da tabela
        # original): sao termos de release de verdade e a mudanca nao os afrouxou.
        ("a versao do roteador sera publicada", "producao_ou_release"),
        ("promover a release para producao", "producao_ou_release"),
    ):
        ok = esperado in dominios(texto)
        item(f"declara {esperado}: {texto!r}", ok, f"declarou {sorted(dominios(texto))}")

    # 3. SEM ALVO NAO E OUTBOUND (regra preservada).
    d = dominios("envio de e-mail interno para o time")
    item("sem alvo nao e outbound: 'envio de e-mail interno'", "outbound_a_terceiro" not in d,
         f"declarou {sorted(d)}")

    # 4. REGRESSAO — as regras de acao humana continuam resolvendo codigo canonico.
    for texto, esperado in (
        ("exclusao de registro de auditoria", "exclusao_de_dado_de_cliente"),
        ("aprovacao de producao para publicar", "aprovacao_de_producao"),
        ("rollback em producao", "rollback_em_producao"),
        ("rotacionar a credencial do banco", "rotacao_ou_revogacao_de_credencial"),
    ):
        obtido = r.acao_canonica_de_decisao_humana(texto)
        item(f"acao humana intocada: {texto!r} -> {esperado}", obtido == esperado,
             f"obtido {obtido!r}")

    # 5. VOCABULARIO DECLARADO — a tabela e a fonte, e ela tem de ser exata.
    reservados = [t for termos in r.TERMOS_DE_DOMINIO_SENSIVEL.values() for t in termos]
    item("vocabulario: 'producao' declara release sozinho",
         dominios("producao") == {"producao_ou_release"}, f"declarou {sorted(dominios('producao'))}")
    item("vocabulario declarado (>= 20 termos)", len(reservados) >= 20,
         f"{len(reservados)} termos")
    item("vocabulario exato: 'entrega' e 'registro' fora da lista",
         "entrega" not in reservados and "registro" not in reservados)
    item("casamento exato nao aceita prefixo",
         r._termos_presentes("implementador", ("implantacao",)) == set())
    item("causa provada: o casamento por prefixo casaria 'implementador' ~ 'implantacao'",
         r._token_casa("implementador", "implantacao"))


def autoteste() -> int:
    """Muta o modulo e exige que a suite REPROVE (buraco de verificacao vira falha)."""
    import contextlib
    import io

    mutacoes = {
        "tira 'producao' do vocabulario de release":
            lambda: setattr(r, "TERMOS_DE_DOMINIO_SENSIVEL",
                            {**r.TERMOS_DE_DOMINIO_SENSIVEL,
                             "producao_ou_release": tuple(
                                 t for t in r.TERMOS_DE_DOMINIO_SENSIVEL["producao_ou_release"]
                                 if t != "producao")}),
        "outbound sem exigir alvo explicito":
            lambda: setattr(r, "TERMOS_DE_DOMINIO_SENSIVEL",
                            {**r.TERMOS_DE_DOMINIO_SENSIVEL,
                             "outbound_a_terceiro": tuple(
                                 r.TERMOS_DE_DOMINIO_SENSIVEL["outbound_a_terceiro"]) + ("time",)}),
        "tira os PLURAIS do vocabulario (achado da revisao T12)":
            lambda: setattr(r, "TERMOS_DE_DOMINIO_SENSIVEL",
                            {**r.TERMOS_DE_DOMINIO_SENSIVEL,
                             "credencial": tuple(t for t in r.TERMOS_DE_DOMINIO_SENSIVEL["credencial"]
                                                 if not t.endswith("s"))}),
        "mexe nas regras de acao humana (regressao do item 4)":
            lambda: setattr(r, "REGRAS_DE_ACAO_HUMANA",
                            tuple(x for x in r.REGRAS_DE_ACAO_HUMANA
                                  if x[0] != "exclusao_de_dado_de_cliente")),
    }
    reprovadas = 0
    for nome, mutar in mutacoes.items():
        originais = {k: getattr(r, k) for k in ("_termos_presentes", "TERMOS_DE_DOMINIO_SENSIVEL",
                                                "TERMOS_DE_ENVIO", "REGRAS_DE_ACAO_HUMANA")}
        mutar()
        buffer, codigo = io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(buffer):
                checar()
            codigo = 0 if all(ok for _, ok in ITENS[-ITENS_ANTES:]) else 1
            codigo = 1 if "FALHA" in buffer.getvalue() else 0
        except Exception:  # mutacao que quebra o modulo tambem conta como detectada
            codigo = 1
        finally:
            for k, v in originais.items():
                setattr(r, k, v)
            del ITENS[-ITENS_ANTES:]
        detectada = codigo != 0
        reprovadas += 1 if detectada else 0
        print(f"  [{'OK' if detectada else 'BURACO'}] mutacao reprovada: {nome}")
    total = len(mutacoes)
    print(f"AUTOTESTE: {reprovadas}/{total} mutacoes reprovadas")
    return 0 if reprovadas == total else 1


ITENS_ANTES = 0


def main() -> int:
    global ITENS_ANTES
    parser = argparse.ArgumentParser()
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    checar()
    falhas = [d for d, ok in ITENS if not ok]
    print(f"RESULTADO: {'PASS' if not falhas else 'FALHOU'} ({len(ITENS)} itens, "
          f"{len(falhas)} falha(s))")
    for d in falhas:
        print(f"  -> {d}")
    if args.autoteste:
        ITENS_ANTES = len(ITENS)
        codigo = autoteste()
        if codigo:
            print("RESULTADO FINAL: FALHOU (autoteste com buraco)")
            return 1
        print("RESULTADO FINAL: PASS (autoteste OK)")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
