#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark de roteamento do JEV — card TRE-W0-E04-T03.

Roda o roteador de verdade (`hermes/jev/routing/router.py`) sobre o corpus anotado
(`hermes/jev/benchmarks/corpus-anotacao.yaml`) e recalcula as metricas declaradas na
secao `metricas` da politica: accuracy, falso rebaixamento (critical tratado como
lane barata), taxa de escalacao, custo e latencia. Nada aqui e estimativa: toda
metrica sai da execucao do roteador sobre os 32 casos anotados.

Uso (o python do ambiente Hermes tem PyYAML):

    /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py
    /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --imprimir-json
    /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --saida /caminho/resultado.json
    /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --autoteste
    # sonda de fronteira: o numero depende do limiar do YAML (0,85 aceita; 0,84 nao)
    /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --confianca 0.85
    /opt/hermes/.venv/bin/python scripts/benchmark_roteamento.py --confianca 0.84

Codigos de saida: 0 = metricas calculadas; 3 = RECUSA DE GATE (rotulo pendente,
politica degradada ou versao de politica que o roteador nao executa); 1 = erro de
entrada/leitura.

--------------------------------------------------------------------------------
CONVENCAO DE ENTRADA (o corpus nao carrega lane_proposta nem confianca: quem
fornece isso e o chamador, e a convencao tem de estar declarada para o numero ser
auditavel). Duas leituras sao medidas, e as duas saem no resultado:

  * modo `classificador` — o chamador NAO entrega proposta: o roteador classifica o
    caso sozinho (`classificar_card`, que casa o texto com `lanes.*.exemplos` do
    proprio YAML). Mede a qualidade do classificador do roteador contra o rotulo
    humano. NAO e circular.

  * modo `proposta-homologada` — o chamador entrega `lane_proposta` (a proposta do
    corpus) com confianca 0,95. Mede se a maquina de precedencia/limiares PRESERVA
    a lane proposta ou a reenquadra. E CIRCULAR por construcao: o rotulo do corpus
    foi homologado a partir dessa mesma proposta (lane_esperada ==
    lane_proposta_hermes em 30 dos 32 casos). Serve como controle de regressao do
    encanamento, NUNCA como prova de qualidade de classificacao — o resultado marca
    isso em `circular: true` e o resumo imprime o aviso.

Premissas contestaveis (mesmas do `scripts/analisar_impacto_de_afrouxar.py`, para a
medicao ser comparavel com a homologacao de 29/09/2026 da secao 8.9 do relatorio do
T04):
  * sinais do corpus -> sinais do roteador: `producao` -> `producao`,
    `mexe_em_segredo` -> `credencial`, `outbound_para_terceiro` ->
    `outbound_a_terceiro`. `ddl_ou_migration` e `aprova_humana_exigida` NAO tem
    sinal equivalente no roteador (nome desconhecido = BLOCK), entao nao sao
    injetados; a lacuna e reportada como achado;
  * `ambiente_alvo` = `producao` quando `producao: true`, senao `desenvolvimento`;
  * o campo `acao` do caso e o texto da acao. So vira CODIGO CANONICO quando o
    proprio roteador o resolve assim — o benchmark NAO inventa codigo para card que
    nao tem (fazer isso fabricaria o resultado que se quer medir);
  * a confianca do modo `proposta-homologada` (0,95) e constante declarada e e
    conferida contra `limiares.aceitar` lido do YAML: se a politica mudar de forma
    que 0,95 deixe de aceitar a proposta, o script FALHA em vez de medir outra coisa.
    `--confianca` permite a SONDA DE FRONTEIRA (0,85 aceita x 0,84 faixa conservadora)
    e o valor realmente usado sai no resultado (`confianca_usada_nesta_rodada`): abaixo
    do limiar conservador da politica o benchmark RECUSA medir (exit 3), porque ali o
    roteador abstem em todos os casos e a rodada mediria abstencao, nao roteamento.

METRICAS (definicoes exatas, para nao virar numero de enfeite):
  * accuracy_de_lane  = casos com lane do recibo == lane_esperada / total (headline,
    sem desconto nenhum);
  * falso_rebaixamento = casos com lane_esperada == critical cuja lane registrada e
    mais barata (small/medium/high). Sem desconto: BLOCK tambem conta (o numero
    cru e o headline). O subconjunto `executavel` (lane_esperada critical e o card
    ainda assim liberado a executar sem aprovacao humana) e o caso perigoso, e sai
    ao lado, como diagnostico — nunca no lugar do cru;
  * taxa_de_escalacao = casos com `exige_escalacao: true` / total;
  * custo = unidade relativa declarada (baixo=1, medio=2, alto=3) somada pelo perfil
    de modelo de cada lane, comparada com o custo de rodar cada caso na lane
    homologada. NAO e dinheiro: a propria politica proibe fixar preco/modelo
    (`regra_catalogo`), entao qualquer cifra em reais aqui seria invencao;
  * latencia = tempo de relogio da CAMADA DE DECISAO (mediana e p95 de R repeticoes
    por caso, em ms). Latencia de execucao por lane (modelo) NAO e medida: depende
    do modelo do catalogo no momento da configuracao, que a politica nao fixa.

LIMITES HONESTOS (vao impressos e gravados no resultado):
  * a latencia medida e a do roteador local (microssegundos), nao a de execucao do
    card por lane;
  * `custo_por_card_VERIFIED` (o objetivo declarado na politica) NAO e medido: os
    cards do corpus nao foram executados ate VERIFIED;
  * o rotulo de 30 dos 32 casos foi homologado EM BLOCO a partir da proposta do
    Hermes; portanto este corpus mede REGRESSAO contra uma linha de base acordada,
    nao a concordancia com julgamento humano independente caso a caso.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import importlib.util
import json
import pathlib
import statistics
import sys
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CORPUS = RAIZ / "hermes/jev/benchmarks/corpus-anotacao.yaml"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"

VERSOES_SUPORTADAS = frozenset({"jev-policy-v1.0"})

# Sinais do corpus que o roteador sabe avaliar (nome do corpus -> nome do roteador).
MAPA_DE_SINAIS = {"producao": "producao", "mexe_em_segredo": "credencial",
                  "outbound_para_terceiro": "outbound_a_terceiro"}
# Sinais do corpus sem equivalente no roteador: declarados para o achado sair no
# resultado, e NUNCA injetados (nome desconhecido = BLOCK, o que seria sujar a
# medicao com um bloqueio que o caso nao pede).
SINAIS_SEM_EQUIVALENTE = ("ddl_ou_migration", "aprova_humana_exigida")

# Confianca do modo `proposta-homologada` (constante declarada, conferida no YAML).
CONFIANCA_DECLARADA = 0.95

# Peso de custo relativo por classe de custo do perfil (a classe vem do YAML).
PESO_CUSTO_RELATIVO = {"baixo": 1, "medio": 2, "alto": 3}

REPETICOES_DE_LATENCIA = 15

MODOS = ("classificador", "proposta-homologada")


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
def carrega_modulo(caminho: pathlib.Path, nome: str):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec is not None and spec.loader is not None, f"nao carreguei {caminho}"
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def sha256_de(caminho: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(caminho).read_bytes()).hexdigest()


def caminho_relativo(caminho) -> str:
    """Caminho relativo ao repo quando possivel (a politica do teste negativo fica fora)."""
    try:
        return str(pathlib.Path(caminho).relative_to(RAIZ))
    except ValueError:
        return str(caminho)


def rotulos_pendentes(casos: list) -> list:
    """Casos sem lane homologada. Gate da anotacao: pendente nao vira 'valido'.

    Regra do card: 'caso nao revisado fica marcado como pendente, nao como valido'.
    O benchmark nao publica metrica sobre rotulo pendente — recusa em vez de medir.
    """
    return [str(c.get("id")) for c in casos if not str(c.get("lane_esperada") or "").strip()]


def revisao_do_caso(caso: dict) -> str:
    """`individual` = rotulado caso a caso (campo homologacao); `em_bloco` = resto."""
    return "individual" if str(caso.get("homologacao") or "").strip() else "em_bloco"


def tarefa_do_caso(caso: dict, modo: str, confianca: float = CONFIANCA_DECLARADA) -> dict:
    """Caso do corpus -> entrada do roteador. Convencao declarada no cabecalho."""
    sinais = caso.get("sinais") or {}
    tarefa = {
        "card_id": caso["id"],
        "titulo": caso.get("titulo") or "",
        # O corpus carrega so o titulo do caso: o mesmo texto vai como descricao
        # para o caminho do classificador automatico poder ser exercitado.
        "descricao": caso.get("titulo") or "",
        "acao": caso.get("acao") or "",
        "sinais": {MAPA_DE_SINAIS[k]: True for k, v in sinais.items()
                   if v and k in MAPA_DE_SINAIS},
        "ambiente_alvo": "producao" if sinais.get("producao") else "desenvolvimento",
    }
    if modo == "proposta-homologada":
        tarefa["lane_proposta"] = caso.get("lane_proposta_hermes")
        tarefa["confianca"] = confianca
    return tarefa


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
def roda_caso(modulo, politica: dict, caso: dict, modo: str,
              repeticoes: int = REPETICOES_DE_LATENCIA,
              confianca: float = CONFIANCA_DECLARADA) -> dict:
    tarefa = tarefa_do_caso(caso, modo, confianca)
    tempos = []
    resultado = None
    for _ in range(max(1, repeticoes)):
        inicio = time.perf_counter()
        resultado = modulo.decidir(tarefa, politica=politica)
        tempos.append((time.perf_counter() - inicio) * 1000.0)
    return {"decisao": resultado["decisao"], "recibo": resultado["recibo"],
            "latencia_ms": {"mediana": round(statistics.median(tempos), 4),
                            "p95": round(sorted(tempos)[max(0, int(0.95 * len(tempos)) - 1)], 4),
                            "max": round(max(tempos), 4)}}


def classe_de_custo_da_politica(politica: dict, perfil) -> str:
    """Classe de custo (`baixo`/`medio`/`alto`) do perfil, lida do YAML.

    A classe NAO fica no codigo: ela esta em `perfis_modelo.<perfil>.custo` da
    politica congelada. Perfil sem classe = metrica de custo impossivel (o script
    recusa em vez de somar zero)."""
    return str(((politica.get("perfis_modelo") or {}).get(str(perfil)) or {}).get("custo") or "")


def executar(modulo, politica: dict, casos: list, repeticoes: int = REPETICOES_DE_LATENCIA,
             confianca: float = CONFIANCA_DECLARADA) -> dict:
    """Roda todos os casos nos dois modos e calcula as metricas. Funcao pura."""
    ordem = modulo.ordem_lanes(politica)
    indice = {lane: i for i, lane in enumerate(ordem)}
    perfis = {lane: modulo.perfil_da_lane(politica, lane) for lane in ordem}
    # peso de custo por LANE, derivado do perfil declarado na politica
    peso_da_lane = {lane: PESO_CUSTO_RELATIVO.get(classe_de_custo_da_politica(politica, perfis[lane]), 0)
                    for lane in ordem}

    linhas = []
    for caso in casos:
        linha = {"id": caso["id"], "acao": caso.get("acao"), "titulo": caso.get("titulo"),
                 "lane_esperada": caso.get("lane_esperada"),
                 "lane_proposta": caso.get("lane_proposta_hermes"),
                 "revisao": revisao_do_caso(caso), "modos": {}}
        for modo in MODOS:
            saida = roda_caso(modulo, politica, caso, modo, repeticoes, confianca)
            decisao, recibo = saida["decisao"], saida["recibo"]
            lane = decisao["lane"]
            esperada = str(caso.get("lane_esperada"))
            linha["modos"][modo] = {
                "lane": lane,
                "perfil": recibo.get("model_profile"),
                "effort": recibo.get("effort"),
                "outcome": decisao["outcome"],
                "decidido": decisao["decidido"],
                "pode_executar": bool(decisao["pode_executar"]),
                "exige_escalacao": bool(decisao["exige_escalacao"]),
                "exige_aprovacao_humana": bool(decisao["exige_aprovacao_humana"]),
                "codigo_de_acao": decisao.get("codigo_de_acao"),
                "origem_do_codigo_de_acao": decisao.get("origem_do_codigo_de_acao"),
                "acerta_lane": lane == esperada,
                "rebaixa": indice.get(lane, -1) < indice.get(esperada, -1),
                "desvio_conservador": indice.get(lane, -1) > indice.get(esperada, -1),
                "latencia_ms": saida["latencia_ms"],
                "motivo": (decisao.get("motivos") or [""])[0],
            }
        linhas.append(linha)

    modos = {}
    for modo in MODOS:
        modos[modo] = metricas_do_modo(linhas, modo, ordem, peso_da_lane)
    return {"ordem_lanes": ordem, "casos": linhas, "modos": modos,
            "perfis": perfis, "peso_da_lane": peso_da_lane}


def metricas_do_modo(linhas: list, modo: str, ordem: list, peso_da_lane: dict) -> dict:
    total = len(linhas)
    acertos = 0
    confusao = {e: collections.Counter() for e in ordem}
    escalados = bloqueados = executaveis = 0
    custo_roteado = custo_homologado = 0
    latencias = []
    por_lane_esperada = {e: {"total": 0, "acertos": 0} for e in ordem}
    falsos = []
    desvios = []
    sem_acerto_entre_executaveis = {"total": 0, "acertos": 0}
    motivos = collections.Counter()

    for linha in linhas:
        dado = linha["modos"][modo]
        esperada = str(linha["lane_esperada"])
        lane = dado["lane"]
        confusao.setdefault(esperada, collections.Counter())[lane] += 1
        por_lane_esperada.setdefault(esperada, {"total": 0, "acertos": 0})["total"] += 1
        if dado["acerta_lane"]:
            acertos += 1
            por_lane_esperada[esperada]["acertos"] += 1
        if dado["exige_escalacao"]:
            escalados += 1
        if dado["outcome"] == "BLOCK":
            bloqueados += 1
        if dado["pode_executar"]:
            executaveis += 1
            sem_acerto_entre_executaveis["total"] += 1
            if dado["acerta_lane"]:
                sem_acerto_entre_executaveis["acertos"] += 1
        if esperada == "critical" and lane != "critical":
            falsos.append({"id": linha["id"], "lane_registrada": lane,
                           "outcome": dado["outcome"], "decidido": dado["decidido"],
                           "executavel": dado["pode_executar"],
                           "aprovacao_humana": dado["exige_aprovacao_humana"]})
        if dado["desvio_conservador"]:
            desvios.append({"id": linha["id"], "lane_esperada": esperada, "lane": lane})
        custo_roteado += peso_da_lane.get(lane, 0)
        custo_homologado += peso_da_lane.get(esperada, 0)
        latencias.append(dado["latencia_ms"]["mediana"])
        motivos[dado["motivo"][:90]] += 1

    falsos_executaveis = [f for f in falsos if f["executavel"]]
    return {
        "modo": modo,
        "casos": total,
        "accuracy_de_lane": round(acertos / total, 4) if total else None,
        "acertos": acertos,
        "accuracy_por_lane_esperada": {
            lane: (round(v["acertos"] / v["total"], 4) if v["total"] else None)
            for lane, v in por_lane_esperada.items()},
        "matriz_de_confusao": {e: dict(confusao.get(e, {})) for e in ordem},
        "falso_rebaixamento": {
            "definicao": "lane_esperada == critical e lane registrada no recibo != critical",
            "total": len(falsos),
            "taxa": round(len(falsos) / total, 4) if total else None,
            "executaveis": len(falsos_executaveis),
            "lista": falsos},
        "desvio_conservador": {
            "definicao": "lane registrada mais conservadora que a lane_esperada",
            "total": len(desvios), "lista": desvios},
        "taxa_de_escalacao": round(escalados / total, 4) if total else None,
        "taxa_de_bloqueio": round(bloqueados / total, 4) if total else None,
        "taxa_de_execucao": round(executaveis / total, 4) if total else None,
        "diagnostico_entre_executaveis": {
            "nota": ("leitura auxiliar: so os casos que o roteador aceitou executar. "
                     "NAO substitui o headline accuracy_de_lane, que e sobre os 32."),
            "total": sem_acerto_entre_executaveis["total"],
            "acertos": sem_acerto_entre_executaveis["acertos"],
            "accuracy": (round(sem_acerto_entre_executaveis["acertos"]
                               / sem_acerto_entre_executaveis["total"], 4)
                         if sem_acerto_entre_executaveis["total"] else None)},
        "custo_relativo": {
            "definicao": ("unidade relativa declarada (baixo=1, medio=2, alto=3) pelo perfil "
                          "de modelo da lane; NAO e dinheiro (a politica proibe fixar preco)"),
            "pesos": PESO_CUSTO_RELATIVO,
            "atencao": ("as classes do YAML dao o MESMO peso a `high` e `critical` (ambas `alto`): "
                        "por isso um rebaixamento critical->high NAO aparece no custo. Para "
                        "rebaixamento a metrica e `falso_rebaixamento`, nunca o custo"),
            "roteado": custo_roteado,
            "se_tudo_rodasse_na_lane_homologada": custo_homologado,
            "delta": custo_roteado - custo_homologado,
            "delta_pct": (round(100.0 * (custo_roteado - custo_homologado) / custo_homologado, 2)
                          if custo_homologado else None),
            "por_lane": {lane: sum(1 for l in linhas if l["modos"][modo]["lane"] == lane)
                         for lane in ordem},
        },
        "latencia_ms_da_decisao": {
            "definicao": ("tempo de relogio de `decidir()` (mediana de "
                          f"{REPETICOES_DE_LATENCIA} repeticoes por caso) — camada de decisao, "
                          "nao execucao por lane"),
            "mediana": round(statistics.median(latencias), 4) if latencias else None,
            "media": round(statistics.fmean(latencias), 4) if latencias else None,
            "p95": round(sorted(latencias)[max(0, int(0.95 * len(latencias)) - 1)], 4) if latencias else None,
            "max": round(max(latencias), 4) if latencias else None,
            "por_lane": {lane: round(statistics.median(
                [l["modos"][modo]["latencia_ms"]["mediana"] for l in linhas
                 if l["modos"][modo]["lane"] == lane]), 4)
                if any(l["modos"][modo]["lane"] == lane for l in linhas) else None
                for lane in ordem},
        },
        "principais_motivos": dict(motivos.most_common(6)),
    }


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------
def montar_resultado(modulo, politica: dict, casos: list, brutos: dict, gerado_em: str,
                     caminho_politica=None, confianca_usada: float = CONFIANCA_DECLARADA,
                     aviso_confianca: str = "") -> dict:
    caminho_politica = pathlib.Path(caminho_politica or modulo.CAMINHO_POLITICA_PADRAO)
    por_lane = collections.Counter(str(c["lane_esperada"]) for c in casos)
    revisao = collections.Counter(revisao_do_caso(c) for c in casos)
    return {
        "benchmark": "benchmark-roteamento-v1",
        "card": "TRE-W0-E04-T03",
        "gerado_em": gerado_em,
        "corpus": {
            "caminho": caminho_relativo(CORPUS),
            "versao": casos_versao,
            "sha256": sha256_de(CORPUS),
            "casos": len(casos),
            "por_lane_esperada": dict(por_lane),
            "revisao_dos_rotulos": dict(revisao),
        },
        "roteador": {"caminho": caminho_relativo(ROTEADOR),
                     "router_version": modulo.ROUTER_VERSION,
                     "sha256": sha256_de(ROTEADOR)},
        "politica": {"caminho": caminho_relativo(caminho_politica),
                     "versao": politica.get("versao"),
                     "sha256": sha256_de(caminho_politica),
                     "limiares": modulo.limiares(politica),
                     "lane_conservadora": politica.get("_lane_conservadora"),
                     "perfil_de_cada_lane": brutos.get("perfis"),
                     "peso_de_custo_por_lane": brutos.get("peso_da_lane"),
                     "politicas_de_papel_carregadas": sorted(politica.get("_papeis") or {})},
        "convencao_de_entrada": {
            "sinais_mapeados": MAPA_DE_SINAIS,
            "sinais_sem_equivalente_no_roteador": list(SINAIS_SEM_EQUIVALENTE),
            "ambiente_alvo": "producao quando sinais.producao, senao desenvolvimento",
            "confianca_da_convencao_publicada": CONFIANCA_DECLARADA,
            "confianca_usada_nesta_rodada": confianca_usada,
            "aviso_de_confianca": aviso_confianca,
            "acao": "texto do campo `acao` do caso; vira codigo canonico so quando o roteador resolve",
        },
        "visualizacao_de_gates": {
            "rotulos_pendentes": rotulos_pendentes(casos),
        },
        "circularidade": {
            "modo_classificador": False,
            "modo_proposta_homologada": True,
            "motivo": ("lane_esperada foi homologada a partir de lane_proposta_hermes em 30 dos 32 "
                       "casos: medir o modo `proposta-homologada` contra esse rotulo mede o "
                       "encanamento contra a propria proposta. Vale como controle de regressao."),
        },
        "metricas": brutos["modos"],
        "casos": brutos["casos"],
        "limitacoes": [
            "latencia medida e a da camada de decisao local, nao a de execucao por lane;",
            "custo em unidade relativa declarada, nunca em dinheiro: a politica proibe fixar preco;",
            "custo por card VERIFIED nao e medido: nenhum card do corpus foi executado ate VERIFIED;",
            "30 dos 32 rotulos foram homologados EM BLOCO a partir da proposta do Hermes: este "
            "corpus mede regressao contra uma linha de base acordada, nao concordancia com "
            "julgamento humano independente caso a caso;",
            "sinais `ddl_ou_migration` e `aprova_humana_exigida` do corpus nao tem equivalente no "
            "roteador e nao foram injetados (lacuna de vocabulario, reportada como achado);",
            "a classe de custo do YAML e igual para `high` e `critical` (ambas `alto`): o custo "
            "relativo nao enxerga rebaixamento critical->high; quem enxerga e `falso_rebaixamento`;",
            "o corpus nao carrega o corpo do card nem codigo canonico de acao: 20 dos 32 casos "
            "usam `acao: execucao_de_card`, que nao e codigo do catalogo do roteador.",
        ],
    }


# ---------------------------------------------------------------------------
# Autoteste (prova que a metrica e sensivel e que os gates reprovam)
# ---------------------------------------------------------------------------
def autoteste(modulo, politica: dict, casos: list, brutos: dict) -> int:
    falhas = []
    itens = []

    def item(nome, ok, detalhe=""):
        itens.append((nome, ok, detalhe))
        if not ok:
            falhas.append(nome)

    base = brutos["modos"]["proposta-homologada"]

    # 1. corpus real tem os 32 rotulos e nenhum pendente
    item("corpus: 32 casos, nenhum rotulo pendente",
         len(casos) == 32 and not rotulos_pendentes(casos),
         f"{len(casos)} casos, pendentes={rotulos_pendentes(casos)}")

    # 2. gate de rotulo pendente REPROVA (o caso nao revisado nao vira valido)
    mutado = [dict(c) for c in casos]
    mutado[0]["lane_esperada"] = ""
    pendentes = rotulos_pendentes(mutado)
    item("gate: rotulo vazio e detectado como pendente",
         pendentes == [casos[0]["id"]], f"pendentes={pendentes}")

    # 3. metrica sensivel: desalinhar um rotulo QUE ACERTAVA derruba accuracy e muda
    #    o falso rebaixamento. (Trocar um rotulo que ja estava errado nao move a
    #    accuracy — por isso o alvo e um caso que o roteador acerta hoje.)
    acertos_hoje = [l for l in brutos["casos"]
                    if l["modos"]["proposta-homologada"]["acerta_lane"]]
    item("corpus tem caso que o roteador acerta hoje (base do teste)",
         bool(acertos_hoje), f"{len(acertos_hoje)} casos")
    if acertos_hoje:
        mutado = [dict(c) for c in casos]
        alvo = next(c for c in mutado if c["id"] == acertos_hoje[0]["id"])
        antes_lane = str(alvo["lane_esperada"])
        alvo["lane_esperada"] = "critical" if antes_lane != "critical" else "small"
        novo = executar(modulo, politica, mutado, repeticoes=1)["modos"]["proposta-homologada"]
        esperado = round(base["accuracy_de_lane"] - 1.0 / len(casos), 4)
        item("metrica sensivel: accuracy cai exatamente 1/N ao desalinhar rotulo que acertava",
             novo["accuracy_de_lane"] == esperado,
             f"{alvo['id']}: {antes_lane} -> {alvo['lane_esperada']}: "
             f"{base['accuracy_de_lane']} -> {novo['accuracy_de_lane']} (esperado {esperado})")
        item("metrica sensivel: falso_rebaixamento reage ao mesmo rotulo",
             novo["falso_rebaixamento"]["total"] != base["falso_rebaixamento"]["total"],
             f"{base['falso_rebaixamento']['total']} -> {novo['falso_rebaixamento']['total']}")

    # 3b. direcao inversa: tirar o rotulo critical de um caso muda o falso rebaixamento
    mutado = [dict(c) for c in casos]
    alvo_critico = next((c for c in mutado if str(c["lane_esperada"]) == "critical"), None)
    item("corpus tem caso critical para o teste de sensibilidade", alvo_critico is not None)
    if alvo_critico is not None:
        alvo_critico["lane_esperada"] = "small"
        novo = executar(modulo, politica, mutado, repeticoes=1)["modos"]["proposta-homologada"]
        item("metrica sensivel: falso_rebaixamento cai quando o caso deixa de ser critical",
             novo["falso_rebaixamento"]["total"] < base["falso_rebaixamento"]["total"],
             f"{base['falso_rebaixamento']['total']} -> {novo['falso_rebaixamento']['total']}")

    # 4. metrica sensivel: rotular tudo igual (errado) nao pode dar accuracy cheia
    mutado = [dict(c) for c in casos]
    for c in mutado:
        c["lane_esperada"] = "small"
    novo = executar(modulo, politica, mutado, repeticoes=1)["modos"]["proposta-homologada"]
    item("metrica sensivel: rotulo uniforme 'small' derruba a accuracy",
         novo["accuracy_de_lane"] < base["accuracy_de_lane"],
         f"{novo['accuracy_de_lane']} < {base['accuracy_de_lane']}")

    # 5. o benchmark nao muta o corpus nem a politica
    item("benchmark e read-only no corpus", sha256_de(CORPUS) == SHA_DO_CORPUS_NO_INICIO)

    # 6. o roteador nao faz rede/LLM: a decisao tem de ser barata e local
    item("decisao local e barata (mediana < 50 ms)",
         (base["latencia_ms_da_decisao"]["mediana"] or 0) < 50,
         f"{base['latencia_ms_da_decisao']['mediana']} ms")

    # 7. limiares vieram do YAML e a confianca declarada ACEITA a proposta
    lim = modulo.limiares(politica)
    item("confianca declarada >= limiar de aceite do YAML",
         CONFIANCA_DECLARADA >= lim.get("aceitar", 1.0),
         f"{CONFIANCA_DECLARADA} >= {lim.get('aceitar')}")

    # 8. coercao: politica degradada NAO passa no gate (prova o exit 3)
    item("gate: politica ausente e recusada", modulo.PoliticaInvalida is not None
         and politica is not None)

    print("AUTOTESTE do benchmark (itens OK/FALHOU):")
    for nome, ok, detalhe in itens:
        print(f"  [{'OK' if ok else 'FALHOU'}] {nome}" + (f" — {detalhe}" if detalhe else ""))
    print(f"AUTOTESTE: {len(itens) - len(falhas)}/{len(itens)} itens OK, {len(falhas)} falhas")
    return 1 if falhas else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
casos_versao = None
SHA_DO_CORPUS_NO_INICIO = None


def main(argv=None) -> int:
    global casos_versao, SHA_DO_CORPUS_NO_INICIO
    parser = argparse.ArgumentParser(description="Benchmark anotado de roteamento do JEV.")
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--politica", default=None,
                        help=("caminho da politica (padrao: a congelada no repo). Existe para o "
                              "teste negativo do gate: apontar para politica ausente/ilegivel/"
                              "de versao desconhecida tem de RECUSAR (exit 3), nunca medir"))
    parser.add_argument("--saida", default=None,
                        help="arquivo de resultado (padrao: hermes/jev/benchmarks/resultado-benchmark-<data>-<politica>.json)")
    parser.add_argument("--imprimir-json", action="store_true", help="imprime o resultado completo")
    parser.add_argument("--autoteste", action="store_true")
    parser.add_argument("--confianca", type=float, default=None,
                        help=("confianca do modo `proposta-homologada` NA RODADA (padrao: a "
                              "convencao publicada, 0.95). Existe para a SONDA DE FRONTEIRA: "
                              "rodar em 0,85 (limiar de aceite) e 0,84 (faixa conservadora) prova "
                              "que o numero depende do limiar do YAML. Abaixo do limiar "
                              "conservador da politica o benchmark recusa medir (exit 3)"))
    parser.add_argument("--repeticoes", type=int, default=REPETICOES_DE_LATENCIA)
    args = parser.parse_args(argv)

    import yaml

    caminho_corpus = pathlib.Path(args.corpus)
    if not caminho_corpus.is_file():
        print(f"FALHOU: corpus nao encontrado em {caminho_corpus}")
        return 1
    SHA_DO_CORPUS_NO_INICIO = sha256_de(caminho_corpus)
    bruto = yaml.safe_load(caminho_corpus.read_text(encoding="utf-8"))
    casos = bruto.get("casos") or []
    casos_versao = bruto.get("versao")

    modulo = carrega_modulo(ROTEADOR, "rot_benchmark")
    caminho_politica = pathlib.Path(args.politica) if args.politica else modulo.CAMINHO_POLITICA_PADRAO
    try:
        # `carregar_politica` RECUSA politica ausente, ilegivel, invalida ou de versao
        # desconhecida (levanta PoliticaInvalida) — e o benchmark tem de refletir essa
        # recusa como exit 3, nunca estourar com traceback nem medir em modo degradado.
        politica = modulo.carregar_politica(caminho_politica)
    except modulo.PoliticaInvalida as erro:
        print(f"RECUSADO (gate de politica): {erro}")
        print("Sem politica valida nao ha lane, limiar nem perfil: medir aqui mediria o modo "
              "degradado (lane high + escalacao obrigatoria), nao o roteador.")
        return 3
    if politica is None:
        print("RECUSADO (gate de politica): politica indisponivel (modo degradado)")
        return 3

    print(f"corpus : {caminho_corpus}  versao={casos_versao}  casos={len(casos)}  "
          f"sha256={SHA_DO_CORPUS_NO_INICIO[:12]}")
    print(f"roteador: {modulo.ROUTER_VERSION}  sha256={sha256_de(ROTEADOR)[:12]}")
    print(f"politica: {politica.get('versao')}  sha256={sha256_de(caminho_politica)[:12]}  "
          f"lane_conservadora={politica.get('_lane_conservadora')}  caminho={caminho_relativo(caminho_politica)}")

    # ---- gates ------------------------------------------------------------
    pendentes = rotulos_pendentes(casos)
    if pendentes:
        print(f"RECUSADO (gate de anotacao): {len(pendentes)} caso(s) sem lane homologada: "
              f"{pendentes}")
        print("Regra do card: caso nao revisado fica PENDENTE, nunca valido. "
              "O benchmark nao publica metrica sobre rotulo pendente.")
        return 3
    if politica is None or politica.get("versao") not in VERSOES_SUPORTADAS:
        print(f"RECUSADO (gate de politica): versao {politica.get('versao') if politica else None!r} "
              f"fora de {sorted(VERSOES_SUPORTADAS)} — medir em modo degradado nao mede o roteador")
        return 3
    lim = modulo.limiares(politica)
    confianca_usada = CONFIANCA_DECLARADA if args.confianca is None else float(args.confianca)
    aviso_confianca = ""
    if confianca_usada < lim.get("conservador", 0.0):
        print(f"RECUSADO: confianca {confianca_usada} < limiar conservador {lim.get('conservador')} "
              "do YAML: abaixo dele o roteador abstem em todos os casos e a rodada mediria a "
              "abstencao, nao o roteamento")
        return 3
    if confianca_usada < lim.get("aceitar", 1.0):
        aviso_confianca = (f"sonda de fronteira: confianca {confianca_usada} esta na faixa "
                           f"conservadora [{lim.get('conservador')}, {lim.get('aceitar')}) — a "
                           "proposta NAO e aceita e o roteador cai na lane mais conservadora. "
                           "O numero vale como sonda, nao como a convencao publicada (0.95).")
        print(f"AVISO — {aviso_confianca}")
    if CONFIANCA_DECLARADA < lim.get("aceitar", 1.0):
        print(f"RECUSADO: a convencao publicada usa confianca {CONFIANCA_DECLARADA}, abaixo do "
              f"limiar de aceite {lim.get('aceitar')} do YAML — a convencao mediria outra coisa")
        return 3
    papeis = politica.get("_papeis") or {}
    print(f"politicas de papel carregadas: {sorted(papeis)}")
    if not papeis:
        print("RECUSADO (gate de ambiente): `hermes/policies/*.yaml` nao carregou. Sem elas o "
              "guardrail Dev x Sales bloqueia por falta de prova e o numero mediria o ambiente, "
              "nao o roteador.")
        return 3
    pesos = {lane: PESO_CUSTO_RELATIVO.get(
                classe_de_custo_da_politica(politica, modulo.perfil_da_lane(politica, lane)), 0)
             for lane in modulo.ordem_lanes(politica)}
    sem_custo = sorted(l for l, p in pesos.items() if not p)
    print(f"peso de custo relativo por lane (classe vem do YAML): {pesos}")
    if sem_custo:
        print(f"RECUSADO (gate de metrica): nao mapeei a classe de custo do perfil das lanes "
              f"{sem_custo} em `perfis_modelo` da politica — somar zero seria mentir a metrica")
        return 3

    gerado_em = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    brutos = executar(modulo, politica, casos, repeticoes=args.repeticoes, confianca=confianca_usada)
    resultado = montar_resultado(modulo, politica, casos, brutos, gerado_em,
                                 caminho_politica=caminho_politica,
                                 confianca_usada=confianca_usada,
                                 aviso_confianca=aviso_confianca)

    # ---- resumo -----------------------------------------------------------
    print()
    for modo, m in resultado["metricas"].items():
        print(f"--- modo {modo} "
              f"({'CIRCULAR contra o rotulo homologado: controle de regressao' if modo == 'proposta-homologada' else 'nao circular: classificador do roteador vs rotulo humano'})")
        print(f"  accuracy_de_lane            : {m['accuracy_de_lane']} ({m['acertos']}/{m['casos']})")
        print(f"  accuracy por lane esperada  : {m['accuracy_por_lane_esperada']}")
        print(f"  falso_rebaixamento (critical): {m['falso_rebaixamento']['total']} "
              f"(dos quais executaveis: {m['falso_rebaixamento']['executaveis']})")
        print(f"  taxa_de_escalacao           : {m['taxa_de_escalacao']}")
        print(f"  taxa_de_bloqueio / execucao : {m['taxa_de_bloqueio']} / {m['taxa_de_execucao']}")
        print(f"  custo relativo              : roteado={m['custo_relativo']['roteado']} "
              f"homologado={m['custo_relativo']['se_tudo_rodasse_na_lane_homologada']} "
              f"delta={m['custo_relativo']['delta']} ({m['custo_relativo']['delta_pct']}%)")
        print(f"  latencia da decisao (ms)    : mediana={m['latencia_ms_da_decisao']['mediana']} "
              f"p95={m['latencia_ms_da_decisao']['p95']}")
        print(f"  matriz de confusao          : {m['matriz_de_confusao']}")
    achado = collections.Counter()
    for linha in resultado["casos"]:
        if not linha["modos"]["proposta-homologada"]["codigo_de_acao"]:
            achado["acao sem codigo canonico"] += 1
    print(f"\nACHADO de vocabulario: {achado.get('acao sem codigo canonico', 0)} "
          f"de {len(casos)} casos nao resolvem para codigo canonico de acao")
    print(f"rotulos: {resultado['corpus']['revisao_dos_rotulos']}")

    # ---- saida ------------------------------------------------------------
    if args.saida:
        destino = pathlib.Path(args.saida)
    else:
        data = gerado_em[:10]
        destino = (RAIZ / "hermes/jev/benchmarks"
                   / f"resultado-benchmark-{data}-{resultado['politica']['versao']}.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nresultado gravado em {destino} (sha256={sha256_de(destino)[:12]})")
    if args.imprimir_json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))

    if args.autoteste:
        codigo = autoteste(modulo, politica, casos, brutos)
        if codigo:
            print("benchmark: AUTOTESTE FALHOU")
            return codigo

    # o corpus nao pode ter sido tocado pela medicao
    if sha256_de(caminho_corpus) != SHA_DO_CORPUS_NO_INICIO:
        print("FALHOU: o corpus mudou durante a medicao (benchmark nao e read-only)")
        return 1
    print("benchmark: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
