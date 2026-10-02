#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prova o casamento de PALAVRA INTEIRA no vocabulario das politicas de PAPEL.

Defeito [encaixe] TRE-W3-E04-T03-D01, medido em 02/10/2026 no card `t_c096e9a4`.

O QUE ACONTECIA
A leitura da prosa das politicas de papel (`hermes/policies/*.yaml`, incluindo a fonte
`human-approval.yaml`) casava por PREFIXO de 4 caracteres. Com isso, DUAS palavras
legitimas de engenharia do card medido — as que nomeiam contagem de itens e acordo
entre contrato e codigo — casavam UM termo de abordagem externa do vocabulario de papel
(as duas comecam pelo mesmo prefixo "cont" do verbo de abordagem entre pessoas). Efeito
medido: o texto do card foi reescrito SEM nenhuma palavra sensivel literal (contagem de
termos do vocabulario = 0) e o gate CONTINUAVA reprovando, com o recibo dizendo
`papel dev-harness nao pode: <frase do vocabulario>`.

Corrigir so o guardrail de papel nao bastou: com ele corrigido, o MESMO mecanismo
apareceu na camada de decisao humana (o mesmo texto casava a frase da fonte que comeca
pelo verbo de abordagem), o que prova que a causa e o COMPARADOR, nao um call site. Por
isso o casamento de palavra inteira vale nos tres lugares que leem a prosa dos papeis:
guardrail `papel_sem_credencial_de_deploy`, guardrail `do_not_contact` e a ponte de prosa
da camada Human Approval.

O QUE ESTA SUITE TRAVA (os dois lados exigidos no card)
  1. FALSO POSITIVO: as duas palavras de engenharia NAO acionam papel, nem outbound, nem
     decisao humana — inclusive no titulo verbatim do card medido;
  2. VERDADEIRO POSITIVO: o vocabulario declarado CONTINUA acionando (papel, plural,
     outbound e as frases da fonte de Human Approval) — o fail-closed nao foi afrouxado;
  3. REGRESSAO: `CONCEITOS_DE_ACAO`, `REGRAS_DE_ACAO_HUMANA` e o casamento de credencial
     NAO foram tocados — o prefixo continua onde ele ajuda.

ACHADO FORA DO ESCOPO (nao e regressao deste conserto, ja existia antes): a lista de
entradas do guardrail `do_not_contact` inclui a prosa de `pode`/`nao_pode` dos papeis, e
uma entrada de desenvolvimento ("escrever codigo e migrations no repositorio do TRE")
casa qualquer titulo de card que cite "TRE" e "codigo" — com o sinal de `do_not_contact`
ligado, um card de desenvolvimento legitimo seria bloqueado. Medido no item "lado 1b'"
desta suite e registrado em card de defeito proprio; corrigir a SELECAO de entradas do
outbound nao faz parte deste conserto (o defeito medido nao tem esse sinal ligado).

Uso: /opt/hermes/.venv/bin/python scripts/verificar_papel_sem_prefixo.py [--autoteste]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from hermes.jev.routing import router as r  # noqa: E402

ITENS: list[tuple[str, bool]] = []

# Identificador do guardrail na linguagem do roteador (mesmo literal que ele grava no
# recibo e em `guardrails_acionados`).
GUARDRAIL_DE_PAPEL = "papel_sem_credencial_de_deploy"

# Titulo VERBATIM do card medido (`t_c096e9a4`): nenhuma palavra do vocabulario de papel
# aparece nele. As duas palavras de engenharia que disparavam o falso positivo estao aqui
# ("contagem" e "contrato").
TITULO_DO_CARD_MEDIDO = (
    "DEFEITO [precisao] TRE-W3-E05-T01: CHANGELOG com contagem desatualizada, "
    "typo no registro, contrato x codigo (linha_vazia) e robustez da lente"
)

# Textos de engenharia legitima que NAO podem acionar o vocabulario de papel.
TEXTOS_SEM_PALAVRA_SENSIVEL = (
    TITULO_DO_CARD_MEDIDO,
    "contagem de saidas sinteticas do juiz",
    "contrato x codigo: divergencia de texto entre contrato e implementacao",
    "contagem de itens por tabela e contrato entre modulos",
)

# Vocabulario de papel que CONTINUA acionando (lado verdadeiro-positivo).
PAPEL_QUE_ACIONA = (
    "contatar lead, cliente ou decisor",
    "contatar cliente",
    "contatar leads do prospect",
    "conceder aprovacao humana no lugar do Anderson",
)

# Frases da fonte de Human Approval que continuam levando a decisao humana.
HUMANA_QUE_ACIONA = (
    "contatar empresa com do_not_contact / opt_out marcado",
    "publicar conteudo em nome da Transformativa sem aprovacao expressa",
    "expor segredo em log, receipt ou mensagem",
)

# Tokens que nomeiam ABORDAGEM a terceiro: e o vocabulario que o defeito fazia casar por
# prefixo. Comparados ja sem plural, como o proprio comparador faz.
TERMOS_DE_ABORDAGEM = ("contatar", "contato", "lead", "prospect", "proposta")


def item(descricao: str, ok: bool, detalhe: str = "") -> None:
    ITENS.append((f"{descricao}{(' — ' + detalhe) if detalhe else ''}", bool(ok)))
    print(f"  [{'OK' if ok else 'FALHA'}] {descricao}{(' — ' + detalhe) if detalhe else ''}")


def _politica():
    return r.carregar_politica(r.CAMINHO_POLITICA_PADRAO)


def decidir(acao: str, **extra):
    tarefa = {"card_id": "t_papel_sem_prefixo", "acao": acao, "acao_codigo": "execucao_de_card"}
    tarefa.update(extra)
    return r.decidir(tarefa, politica=_politica())


def _entradas_de_outbound(papeis) -> list:
    """As MESMAS entradas que `_e_acao_outbound` monta (papel + codigos + termos)."""
    entradas = list((_politica().get("nunca_decidido_por_maquina") or []))
    for dado in papeis.values():
        entradas += list(dado.get("pode") or []) + list(dado.get("nao_pode") or [])
    for termo in ("contato", "contatar", "proposta", "e-mail", "mensagem", "linkedin", "whatsapp"):
        entradas += r._entradas_com(papeis, termo)
    return entradas


def _casam_abordagem(texto: str, papeis) -> list:
    """Entradas de ABORDAGEM casadas pelo texto (o alvo deste conserto)."""
    casadas = r._entradas_que_casam(texto, _entradas_de_outbound(papeis), r._token_casa_inteiro)
    return [e for e in casadas
            if any(r._singular(t) in TERMOS_DE_ABORDAGEM for t in r._tokens(e))]


def checar() -> None:
    papeis = r.carregar_politicas_de_papel()

    # ------------------------------------------------------------------ causa
    item("causa provada: o prefixo de 4 caracteres casava 'contagem' ~ 'contatar'",
         r._token_casa("contagem", "contatar"),
         f"prefixo comum = {r._prefixo_comum('contagem', 'contatar')}")
    item("causa provada: o prefixo de 4 caracteres casava 'contrato' ~ 'contatar'",
         r._token_casa("contrato", "contatar"),
         f"prefixo comum = {r._prefixo_comum('contrato', 'contatar')}")
    item("o vocabulario de papel nao aceita mais esses dois casamentos",
         not r._token_casa_inteiro("contagem", "contatar")
         and not r._token_casa_inteiro("contrato", "contatar"))

    # ------------------------------------------------- lado 1: NAO dispara
    for texto in TEXTOS_SEM_PALAVRA_SENSIVEL:
        _papel, motivo = r._papel_para_acao({"acao": texto}, _politica(), papeis)
        item(f"lado 1a — papel nao aciona: {texto[:44]!r}…", motivo == "", f"motivo {motivo!r}")
        casadas = _casam_abordagem(texto, papeis)
        item(f"lado 1b — outbound nao casa abordagem: {texto[:44]!r}…", casadas == [],
             f"casou {casadas}")
        humana = r.acao_de_decisao_humana(texto, _politica(), papeis)
        item(f"lado 1c — decisao humana nao aciona: {texto[:44]!r}…", humana == [],
             f"acionou {humana}")
        resultado = decidir(texto)
        item(f"lado 1d — nenhum guardrail acionado no card: {texto[:44]!r}…",
             not resultado["decisao"]["guardrails_acionados"],
             f"guardrails={resultado['decisao']['guardrails_acionados']}")

    # Sem a colisao conhecida de desenvolvimento ('tre'/'codigo'), o caminho de decisao do
    # outbound tambem nao aciona — prova estrita, no nivel do roteador.
    for texto in TEXTOS_SEM_PALAVRA_SENSIVEL[1:]:
        item(f"lado 1b' — outbound nao aciona (estrito): {texto[:40]!r}…",
             not r._e_acao_outbound(texto, _politica(), papeis))

    # ------------------------------------------------- lado 2: CONTINUA disparando
    for frase in PAPEL_QUE_ACIONA:
        _papel, motivo = r._papel_para_acao({"acao": frase}, _politica(), papeis)
        item(f"lado 2a — papel aciona: {frase!r}", bool(motivo), f"motivo {motivo!r}")
    for frase in HUMANA_QUE_ACIONA:
        achadas = r.acao_de_decisao_humana(frase, _politica(), papeis)
        item(f"lado 2b — decisao humana aciona: {frase!r}", bool(achadas), f"{achadas}")
    item("lado 2c — outbound aciona: 'enviar mensagem no WhatsApp para lead novo'",
         r._e_acao_outbound("enviar mensagem no WhatsApp para lead novo", _politica(), papeis))
    resultado = decidir("contatar lead, cliente ou decisor")
    item("lado 2d — guardrail de papel bloqueia no caminho de decisao",
         GUARDRAIL_DE_PAPEL in resultado["decisao"]["guardrails_acionados"],
         f"guardrails={resultado['decisao']['guardrails_acionados']}")

    # ------------------------------------------------- plural declarado
    for a, b in (("leads", "lead"), ("clientes", "cliente"), ("credenciais", "credencial"),
                 ("contatos", "contato")):
        item(f"plural tolerado: {a!r} ~ {b!r}", r._token_casa_inteiro(a, b))
    item("plural: 'contatar leads do prospect' continua acionando o papel",
         bool(r._papel_para_acao({"acao": "contatar leads do prospect"}, _politica(), papeis)[1]))
    item("palavra curta nao e cortada: _singular('cpf') == 'cpf'", r._singular("cpf") == "cpf")
    item("palavra de 4 letras nao e cortada: _singular('dois') == 'dois'",
         r._singular("dois") == "dois")
    item("token inteiro e igual a si mesmo: _token_casa_inteiro('deploy','deploy')",
         r._token_casa_inteiro("deploy", "deploy"))

    # ------------------------- regressao: o prefixo continua onde ele ajuda
    for a, b in (("produto", "producao"), ("implementador", "implantacao"),
                 ("versionado", "versao"), ("promocao", "promover")):
        item(f"prefixo preservado (prosa de ACAO/credencial): {a!r} ~ {b!r}",
             r._token_casa(a, b))
    esperado = "exclusao_de_dado_de_cliente"
    obtido = r.acao_canonica_de_decisao_humana("exclusao de registro de auditoria")
    item(f"regra de acao humana intocada: 'exclusao de registro de auditoria' -> {esperado}",
         obtido == esperado, f"obtido {obtido!r}")
    item("regra de acao humana intocada: 'promocao de release para producao' -> aprovacao_de_producao",
         r.acao_canonica_de_decisao_humana("promocao de release para producao")
         == "aprovacao_de_producao")
    item("vocabulario de dominio sensivel intocado (T11/T12): 'implementador' nao declara dominio",
         r._dominios_sensiveis_do_texto("implementador de deployment") == set(),
         f"{sorted(r._dominios_sensiveis_do_texto('implementador de deployment'))}")


def autoteste() -> int:
    """Muta o roteador e exige que a suite REPROVE (buraco de verificacao vira falha)."""
    import contextlib
    import io

    original_entradas = r._entradas_que_casam

    def sempre_prefixo(acao, entradas, comparador=None):
        return original_entradas(acao, entradas)  # ignora o comparador de palavra inteira

    mutacoes = {
        "volta o prefixo de 4 caracteres no vocabulario de papel":
            lambda: setattr(r, "_token_casa_inteiro", r._token_casa),
        "exato sem o plural declarado":
            lambda: setattr(r, "_token_casa_inteiro", lambda a, b: a == b),
        "call site ignora o comparador (sempre prefixo)":
            lambda: setattr(r, "_entradas_que_casam", sempre_prefixo),
        "guardrail de papel neutralizado":
            lambda: setattr(r, "_papel_para_acao", lambda *a, **k: (None, "")),
        "ponte de prosa da decisao humana neutralizada":
            lambda: setattr(r, "acao_de_decisao_humana", lambda *a, **k: []),
        "guardrail de outbound neutralizado":
            lambda: setattr(r, "_e_acao_outbound", lambda *a, **k: False),
    }
    reprovadas = 0
    for nome, mutar in mutacoes.items():
        originais = {k: getattr(r, k) for k in ("_token_casa_inteiro", "_entradas_que_casam",
                                                "_papel_para_acao", "acao_de_decisao_humana",
                                                "_e_acao_outbound")}
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
