#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prova que o guardrail `do_not_contact` so decide outbound por ABORDAGEM a terceiro.

Defeito [encaixe] do card `t_fa344342`, medido em 02/10/2026 (commit `0256154`),
detectado pela suite do defeito `TRE-W3-E04-T03-D01` (`verificar_papel_sem_prefixo.py`,
item "lado 1b'").

O QUE ACONTECIA
`_e_acao_outbound` montava a lista de entradas com a prosa de `pode`/`nao_pode` de TODOS
os papeis (`hermes/policies/*.yaml`), alem dos codigos da politica e das entradas
derivadas por termo. Uma entrada de PERMISSAO do dev-harness — "escrever codigo e
migrations no repositorio do TRE" — casava QUALQUER titulo de card que citasse "TRE" e
"codigo": dois tokens casados acionam a regra `casados >= 2` de `_entradas_que_casam`.
Efeito medido, com o sinal `empresa_do_not_contact` ligado: um card de DESENVOLVIMENTO,
que nao aborda ninguem, era BLOQUEADO pelo guardrail `do_not_contact` (`_e_acao_outbound`
= True). Sem o sinal, o mesmo card passa — o falso positivo so aparece com o sinal.

O QUE ESTA SUITE TRAVA (os dois lados exigidos no card)
  1. A ENTRADA DE PERMISSAO fica fora da lista do guardrail: o titulo verbatim do card
     medido nao aciona `_e_acao_outbound` e o sinal nao muda a decisao dele;
  2. O VOCABULARIO DE ABORDAGEM continua acionando (o fail-closed nao foi afrouxado) —
     inclusive a frase do item "lado 2c" da suite do D01;
  3. A LISTA E PARTE DO GUARDRAIL: invariante de que toda entrada ou e codigo da politica
     ou cita um termo de abordagem — e a fonte `human-approval.yaml` fica FORA, porque
     uma acao dela ("expor segredo em log, receipt ou mensagem") cita um termo de canal
     sem falar de abordagem a terceiro (o mesmo falso positivo voltaria por outra entrada).

Uso: /opt/hermes/.venv/bin/python scripts/verificar_outbound_sem_prosa_de_papel.py [--autoteste]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from hermes.jev.routing import router as r  # noqa: E402

ITENS: list[tuple[str, bool]] = []
ITENS_ANTES = 0

IDENTIFICADOR_DO_GUARDRAIL = "do_not_contact"

# Titulo VERBATIM do card medido (`t_c096e9a4`), o mesmo dos defeitos do roteador.
TITULO_DO_CARD_MEDIDO = (
    "DEFEITO [precisao] TRE-W3-E05-T01: CHANGELOG com contagem desatualizada, "
    "typo no registro, contrato x codigo (linha_vazia) e robustez da lente"
)

# A entrada de PERMISSAO do papel de desenvolvimento que casava o titulo acima por
# "tre" + "codigo" (dois tokens de dominio, nenhum de abordagem).
ENTRADA_DE_PERMISSAO = "escrever codigo e migrations no repositorio do TRE"

# Textos de desenvolvimento legitimo: nenhum aborda terceiro.
TEXTOS_DE_DESENVOLVIMENTO = (
    TITULO_DO_CARD_MEDIDO,
    "gravar dados de negocio no PostgreSQL do TRE e ajustar o codigo do repositorio",
    "rodar a bateria de testes do repositorio do TRE apos o conserto do roteador",
)

# Casos do corpus anotado (`hermes/jev/benchmarks/corpus-anotacao.yaml`, v1.5) com rotulo
# HUMANO `sinais.outbound_para_terceiro: false` que a lista antiga acionava — o mesmo
# defeito, medido em rotulo do dono: 'ICP Score V1' e 'Score calibration' casavam a prosa
# "gerar conteudo, resumo, score e recomendacao" por UM token (fracao 0,5) e 'Aplicar
# migration que altera tabela de producao' casava "aplicar DDL/migration em qualquer
# ambiente" por "aplicar" + "migration". Os tres passaram a NAO acionar.
CORPUS_NAO_OUTBOUND = (
    "ICP Score V1",
    "Score calibration",
    "Aplicar migration que altera tabela de producao",
)

# Frases de ABORDAGEM que CONTINUAM acionando o guardrail.
FRASES_DE_ABORDAGEM = (
    "enviar mensagem no WhatsApp para lead novo",       # item "lado 2c" da suite do D01
    "enviar e-mail pelo Titan",                          # caso REPROVA do validador de guardrails
    "contatar lead, cliente ou decisor",
    "contatar quem estiver marcado como opt-out",
    "contatar o decisor por e-mail no primeiro contato",
    "enviar proposta comercial para a empresa nova",
)


def item(descricao: str, ok: bool, detalhe: str = "") -> None:
    ITENS.append((f"{descricao}{(' — ' + detalhe) if detalhe else ''}", bool(ok)))
    print(f"  [{'OK' if ok else 'FALHA'}] {descricao}{(' — ' + detalhe) if detalhe else ''}")


def _politica():
    return r.carregar_politica(r.CAMINHO_POLITICA_PADRAO)


def decisao(acao: str, politica, **extra):
    tarefa = {"card_id": "t_outbound_sem_prosa", "acao": acao, "acao_codigo": "execucao_de_card"}
    tarefa.update(extra)
    return r.decidir(tarefa, politica=politica)


def _acionados(resultado) -> list:
    return resultado["decisao"]["guardrails_acionados"]


def _acao_de_permissao_ainda_na_politica(papeis) -> bool:
    """A prosa de permissao continua declarada no papel: o conserto e na LISTA do
    guardrail, nunca apagando a politica de papel para agradar o guardrail."""
    return ENTRADA_DE_PERMISSAO in (papeis.get("dev-harness", {}).get("pode") or [])


def checar() -> None:
    politica = _politica()
    papeis = r.carregar_politicas_de_papel()
    entradas = r._entradas_de_abordagem(politica, papeis)
    codigos = [str(x) for x in politica.get("nunca_decidido_por_maquina") or []]

    # ------------------------------------------------------------------ causa
    casadas_causa = r._entradas_que_casam(TITULO_DO_CARD_MEDIDO, [ENTRADA_DE_PERMISSAO],
                                          r._token_casa_inteiro)
    comuns = sorted(set(r._tokens(TITULO_DO_CARD_MEDIDO)) & set(r._tokens(ENTRADA_DE_PERMISSAO)))
    item("causa provada: a entrada de PERMISSAO casa o titulo do card por 2 tokens de dominio",
         bool(casadas_causa) and comuns == ["codigo", "tre"], f"tokens comuns={comuns}")
    item("a entrada de PERMISSAO ficou FORA da lista do guardrail",
         ENTRADA_DE_PERMISSAO not in entradas)
    item("a prosa de permissao continua declarada na politica de papel (conserto na lista, nao na politica)",
         _acao_de_permissao_ainda_na_politica(papeis))

    # --------------------------------------------- invariante: lista = abordagem
    fora_do_padrao = [e for e in entradas
                      if e not in codigos
                      and not any(t in r._normalizar(e) for t in r.TERMOS_DE_ABORDAGEM)]
    item("invariante: toda entrada ou e codigo da politica ou NOMEIA abordagem",
         fora_do_padrao == [], f"fora do padrao={fora_do_padrao}")
    item("os codigos da politica continuam na lista",
         all(c in entradas for c in codigos), f"{len(codigos)} codigo(s)")
    acoes_humanas = [str(x) for x in (politica.get("_acoes_de_human_approval") or [])]
    item("fonte human-approval.yaml fica FORA da lista (acao de canal sem abordagem nao decide outbound)",
         bool(acoes_humanas) and not any(a in entradas for a in acoes_humanas),
         f"{len(acoes_humanas)} acao(oes) da fonte, nenhuma na lista")
    item("a entrada de canal da fonte ('expor segredo em log, receipt ou mensagem') NAO esta na lista",
         "expor segredo em log, receipt ou mensagem" not in entradas)

    # ------------------------------------------------- lado 1: NAO dispara
    for texto in TEXTOS_DE_DESENVOLVIMENTO + CORPUS_NAO_OUTBOUND:
        item(f"lado 1a — outbound nao aciona: {texto[:44]!r}…",
             not r._e_acao_outbound(texto, politica, papeis))
        com_sinal = decisao(texto, politica, sinais={"empresa_do_not_contact": True})
        sem_sinal = decisao(texto, politica)
        item(f"lado 1b — com o sinal ligado, o guardrail nao aciona: {texto[:44]!r}…",
             IDENTIFICADOR_DO_GUARDRAIL not in _acionados(com_sinal),
             f"guardrails={_acionados(com_sinal)}")
        item(f"lado 1c — o sinal nao muda a decisao de um card que nao aborda: {texto[:44]!r}…",
             com_sinal["decisao"]["decidido"] == sem_sinal["decisao"]["decidido"],
             f"com={com_sinal['decisao']['decidido']} sem={sem_sinal['decisao']['decidido']}")

    # ------------------------------------------------- lado 2: CONTINUA disparando
    for frase in FRASES_DE_ABORDAGEM:
        item(f"lado 2a — outbound aciona: {frase!r}", r._e_acao_outbound(frase, politica, papeis))
        resultado = decisao(frase, politica, sinais={"empresa_do_not_contact": True})
        item(f"lado 2b — com o sinal ligado, o guardrail aciona: {frase!r}",
             IDENTIFICADOR_DO_GUARDRAIL in _acionados(resultado), f"guardrails={_acionados(resultado)}")

    # ------------------------------------------------- regressao do comparador
    item("plural continua acionando: 'contatar leads do prospect'",
         r._e_acao_outbound("contatar leads do prospect", politica, papeis))
    item("prefixo de 4 caracteres preservado onde ele ajuda: 'produto' ~ 'producao'",
         r._token_casa("produto", "producao"))
    interno = decisao("ajuste de texto simples", politica, acao_codigo="ajuste_de_texto",
                      sinais={"empresa_do_not_contact": True})
    item("acao interna com o sinal ligado continua executando",
         IDENTIFICADOR_DO_GUARDRAIL not in _acionados(interno)
         and interno["decisao"]["decidido"] == "executar",
         f"decidido={interno['decisao']['decidido']} guardrails={_acionados(interno)}")


def autoteste() -> int:
    """Muta o roteador e exige que a suite REPROVE (buraco de verificacao vira falha)."""
    import contextlib
    import io

    original_entradas_que_casam = r._entradas_que_casam

    def sempre_prefixo(acao, entradas, comparador=None):
        return original_entradas_que_casam(acao, entradas)  # ignora o comparador de palavra inteira

    def lista_antiga(politica, papeis):
        """A formula do defeito: prosa inteira de `pode`/`nao_pode` de todos os papeis."""
        achadas = list(politica.get("nunca_decidido_por_maquina") or [])
        for dado in papeis.values():
            achadas += list(dado.get("pode") or []) + list(dado.get("nao_pode") or [])
        for termo in r.TERMOS_DE_ABORDAGEM:
            achadas += r._entradas_com(papeis, termo)
        return list(dict.fromkeys(achadas))

    def so_os_codigos(politica, papeis):
        return list(politica.get("nunca_decidido_por_maquina") or [])

    def com_a_fonte_humana(politica, papeis):
        return list(r._entradas_de_abordagem(politica, papeis)) + \
            [str(x) for x in (politica.get("_acoes_de_human_approval") or [])]

    mutacoes = {
        "volta a lista antiga (prosa de PERMISSAO de todos os papeis)":
            lambda: setattr(r, "_entradas_de_abordagem", lista_antiga),
        "lista so com os codigos da politica (tira as secoes de abordagem)":
            lambda: setattr(r, "_entradas_de_abordagem", so_os_codigos),
        "inclui a fonte human-approval na lista":
            lambda: setattr(r, "_entradas_de_abordagem", com_a_fonte_humana),
        "guardrail de outbound neutralizado (nunca aciona)":
            lambda: setattr(r, "_e_acao_outbound", lambda *a, **k: False),
        "guardrail de outbound sempre aciona (bloqueia card interno)":
            lambda: setattr(r, "_e_acao_outbound", lambda *a, **k: True),
        "comparador volta ao prefixo de 4 caracteres":
            lambda: setattr(r, "_token_casa_inteiro", r._token_casa),
        "call site ignora o comparador (sempre prefixo)":
            lambda: setattr(r, "_entradas_que_casam", sempre_prefixo),
    }
    atributos = ("_entradas_de_abordagem", "_e_acao_outbound", "_token_casa_inteiro",
                 "_entradas_que_casam")
    reprovadas = 0
    for nome, mutar in mutacoes.items():
        originais = {k: getattr(r, k) for k in atributos}
        mutar()
        buffer, codigo = io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(buffer):
                checar()
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
        if autoteste():
            print("RESULTADO FINAL: FALHOU (autoteste com buraco)")
            return 1
        print("RESULTADO FINAL: PASS (autoteste OK)")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
