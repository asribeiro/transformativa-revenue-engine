#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Impacto medido de AFROUXAR o criterio do D07 (texto livre volta a executar).

Base da homologacao de 29/09/2026 ("manter estrito agora, revisitar depois do
T05"): mede, sobre o corpus anotado do benchmark, quantos cards mudam de veredito
se o criterio for afrouxado — e quais deles tem sinal sensivel ligado.

  estrito   = o que esta no repo: texto livre sem codigo canonico conhecido NAO
              executa (escala sempre; card TRE-W0-E04-T02-D07).
  afrouxado = reversao do D07 em COPIA TEMPORARIA do roteador: volta a escalar
              somente quando o texto declara dominio sensivel (criterio por
              vocabulario, o que produziu os 4 escapes).

Read-only no repo: nenhum arquivo versionado e alterado; a mutacao vive num
diretorio temporario. Uso: `/opt/hermes/.venv/bin/python scripts/analisar_impacto_de_afrouxar.py`

Premissas (contestaveis, declaradas):
  * corpus.sinais -> sinais do roteador: producao -> producao_ou_release,
    mexe_em_segredo -> credencial, outbound_para_terceiro -> outbound_a_terceiro.
    `ddl_ou_migration` e `aprova_humana_exigida` NAO tem sinal equivalente no
    roteador (nome desconhecido = BLOCK), entao nao sao injetados;
  * ambiente_alvo = producao quando `producao: true`; senao desenvolvimento
    (leitura da justificativa do corpus: "DDL novo, ambiente novo");
  * confianca 0,95 e lane_proposta small em todos (o corpus nao carrega confianca);
  * o campo `acao` do corpus e usado como texto da acao. Ele NAO e codigo canonico
    do roteador na maioria dos casos — isso e medido e reportado abaixo.
"""
import collections
import importlib.util
import pathlib
import shutil
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CORPUS = RAIZ / "hermes/jev/benchmarks/corpus-anotacao.yaml"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"


def carrega(caminho, nome):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec is not None and spec.loader is not None, f"nao carreguei {caminho}"
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def roteador_afrouxado(tmp):
    """Reversao do D07, em copia temporaria, com a politica ao lado."""
    fonte = ROTEADOR.read_text()
    alvo = ('    if resolucao["classe"] == "proibida":\n        return None\n'
            '    if not resolucao["codigo"]:')
    troca = ('    if not dominios["uniao"]:\n        return None\n'
             '    if resolucao["classe"] == "proibida":\n        return None\n'
             '    if not resolucao["codigo"]:')
    mutante = fonte.replace(alvo, troca)
    if mutante == fonte:
        raise SystemExit("FALHOU: a reversao do D07 nao aplicou (ancora do codigo mudou)")
    (tmp / "hermes/jev/routing").mkdir(parents=True)
    (tmp / "hermes/policies").mkdir(parents=True)
    shutil.copy(RAIZ / "hermes/jev/policy_v1.yaml", tmp / "hermes/jev/policy_v1.yaml")
    for politica in (RAIZ / "hermes/policies").glob("*.yaml"):
        shutil.copy(politica, tmp / "hermes/policies" / politica.name)
    (tmp / "hermes/jev/routing/router.py").write_text(mutante)
    return carrega(tmp / "hermes/jev/routing/router.py", "rot_afrouxado")


MAPA_DE_SINAIS = {"producao": "producao", "mexe_em_segredo": "credencial",
                  "outbound_para_terceiro": "outbound_a_terceiro"}


def tarefa_do_caso(caso):
    sinais = caso.get("sinais") or {}
    return {"card_id": caso["id"], "acao": caso["acao"], "titulo": caso.get("titulo", ""),
            "sinais": {MAPA_DE_SINAIS[k]: True for k, v in sinais.items()
                       if v and k in MAPA_DE_SINAIS},
            "ambiente_alvo": "producao" if sinais.get("producao") else "desenvolvimento",
            "lane_proposta": "small", "confianca": 0.95}


def main():
    import yaml

    estrito = carrega(ROTEADOR, "rot_estrito")
    politica = estrito.carregar_politica(estrito.CAMINHO_POLITICA_PADRAO)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="d07-afrouxado-"))
    try:
        afrouxado = roteador_afrouxado(tmp)
        casos = yaml.safe_load(CORPUS.read_text())["casos"]

        def roda(modulo, caso):
            resultado = modulo.decidir(tarefa_do_caso(caso), politica=politica)
            return resultado["decisao"], resultado["recibo"]

        estrito_cnt = collections.Counter()
        afrouxado_cnt = collections.Counter()
        flips = []
        for caso in casos:
            d1, r1 = roda(estrito, caso)
            d2, r2 = roda(afrouxado, caso)
            estrito_cnt[r1["outcome"]] += 1
            afrouxado_cnt[r2["outcome"]] += 1
            if not d1["pode_executar"] and d2["pode_executar"]:
                flips.append((caso["id"], caso["acao"],
                              [k for k, v in (caso.get("sinais") or {}).items() if v],
                              caso.get("lane_proposta_hermes"),
                              caso.get("lane_esperada") or "-"))

        print(f"casos no corpus: {len(casos)}")
        print(f"veredito ESTRITO  : {dict(estrito_cnt)}")
        print(f"veredito AFROUXADO: {dict(afrouxado_cnt)}")
        print(f"passam a EXECUTAR se afrouxar: {len(flips)} de {len(casos)} "
              f"({100.0 * len(flips) / len(casos):.1f}%)")
        for ident, acao, sinais, proposta, esperada in flips:
            print(f"    {ident:<12} acao={acao:<24} lane_proposta={proposta:<7} "
                  f"lane_esperada={esperada:<8} sinais={sinais}")

        proibidos = {str(x) for x in (politica.get("nunca_decidido_por_maquina") or [])}
        conhecidos = proibidos | set(estrito.CODIGOS_DE_ACAO_COMUNS)
        do_corpus = {c["acao"] for c in casos}
        print("codigos comuns do roteador:", sorted(estrito.CODIGOS_DE_ACAO_COMUNS))
        print("codigos proibidos (politica):", sorted(proibidos))
        print("acoes do corpus que o roteador NAO conhece:", sorted(do_corpus - conhecidos))
        print("acoes do corpus que ele conhece:", sorted(do_corpus & conhecidos))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
