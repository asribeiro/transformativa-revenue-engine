#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instrumento das metricas de CUSTO e LATENCIA POR LANE — card TRE-W0-E04-T06.

A politica declara, na secao `metricas`, o que e sucesso e o que se acompanha. Ate aqui
isso era declaracao sem medicao: `latencia_por_lane` e `falso_rebaixamento` estavam
escritos no YAML e ninguem os media, e `custo_por_card_verified` (o objetivo) nao e
medivel hoje. Este instrumento fecha essa lacuna SEM mentir: o que ele consegue medir,
ele mede; o que ele nao consegue, sai no resultado como NAO MEDIDO, com o motivo.

CONVENCAO (nada aqui e recalculado por conta propria):
  * a lane de cada decisao vem do ROTEADOR DE VERDADE (`hermes/jev/routing/router.py`),
    executado sobre o corpus anotado com a MESMA convencao de entrada do benchmark do
    TRE-W0-E04-T03 — importada de `scripts/benchmark_roteamento.py`, nao recopiada: se a
    convencao mudar la, muda aqui junto, ou o script quebra em vez de medir outra coisa;
  * a classe de custo de cada lane vem de `perfis_modelo.<perfil>.custo` da POLITICA
    (baixo=1, medio=2, alto=3). Perfil sem classe = metrica impossivel: RECUSA (exit 3),
    porque somar zero seria mentir a metrica;
  * a latencia e a da CAMADA DE DECISAO (relogio de `decidir()`, mediana e p95 de N
    repeticoes por caso). Latencia de execucao por lane NAO e medida: depende do modelo
    do catalogo no momento da configuracao, que a politica proibe fixar;
  * as decisoes REAIS vem de `hermes/jev/receipts/*.json` (recibo de 13 campos). Custo por
    lane e derivavel do recibo (`lane` + `model_profile`); latencia NAO — o contrato do
    recibo nao carrega latencia, e o instrumento diz isso em vez de estimar.

LIMITE QUE NAO PODE SER ESQUECIDO (medido, nao opinado): `high` e `critical` tem a MESMA
classe de custo (`alto`) na politica. Portanto o custo por lane NAO enxerga rebaixamento
`critical -> high`; quem enxerga e `falso_rebaixamento`. O resultado traz esse limite como
DADO (`blindagem_de_custo`), nao como nota de rodape: se um dia as classes deixarem de ser
iguais, o proprio resultado acusa (`high_e_critical_mesma_classe: false`).

Uso:
    /opt/hermes/.venv/bin/python scripts/medir_metricas_por_lane.py
    /opt/hermes/.venv/bin/python scripts/medir_metricas_por_lane.py --imprimir-json
    /opt/hermes/.venv/bin/python scripts/medir_metricas_por_lane.py --saida /caminho.json
    /opt/hermes/.venv/bin/python scripts/medir_metricas_por_lane.py --politica <arquivo>
    /opt/hermes/.venv/bin/python scripts/medir_metricas_por_lane.py --recibos <dir>
    /opt/hermes/.venv/bin/python scripts/medir_metricas_por_lane.py --autoteste

Codigos de saida: 0 = metricas medidas; 3 = RECUSA DE GATE (politica ausente/ilegivel/de
versao desconhecida, ou perfil de lane sem classe de custo declarada); 1 = erro de
entrada/leitura.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import pathlib
import statistics
import sys
import tempfile
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CORPUS = RAIZ / "hermes/jev/benchmarks/corpus-anotacao.yaml"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
BENCHMARK = RAIZ / "scripts/benchmark_roteamento.py"
RECIBOS = RAIZ / "hermes/jev/receipts"

VERSAO_DO_INSTRUMENTO = "metricas-por-lane-v1"

REPETICOES_DE_LATENCIA = 15

# Motivo da nao-medicao quando a POLITICA em vigor ainda nao declara `metricas.instrumentacao`
# (caso da v1.0). O instrumento nao pode, sozinho, transformar uma metrica nao medivel em
# silencio: se a politica nao diz por que, ele diz — e diz o mesmo que a v1.1 passa a declarar.
MOTIVOS_PADRAO_DE_NAO_MEDICAO = {
    "custo_por_card_verified": ("nenhum card do corpus foi executado ate VERIFIED e o recibo "
                                "nao carrega custo"),
    "regressao_ou_retrabalho": ("exige historico de execucao de card (retrabalho medido em "
                                "execucao), que o encanamento ainda nao registra"),
}


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
def carrega_modulo(caminho: pathlib.Path, nome: str):
    especificacao = importlib.util.spec_from_file_location(nome, str(caminho))
    assert especificacao is not None and especificacao.loader is not None, f"nao carreguei {caminho}"
    modulo = importlib.util.module_from_spec(especificacao)
    sys.modules[nome] = modulo
    especificacao.loader.exec_module(modulo)
    return modulo


def sha256_de(caminho: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(caminho).read_bytes()).hexdigest()


def caminho_relativo(caminho) -> str:
    try:
        return str(pathlib.Path(caminho).relative_to(RAIZ))
    except ValueError:
        return str(caminho)


# ---------------------------------------------------------------------------
# Medicao
# ---------------------------------------------------------------------------
def _p95(latencias: list) -> float:
    """p95 da amostra, com piso na mediana.

    O indice do p95 e `ceil(0,95 x n) - 1`. Com poucas amostras esse indice cai na cauda
    inferior e um "p95" menor que a mediana seria um numero que mente; por isso o piso na
    mediana. Com n <= 20 o p95 coincide com o maximo — leitura conservadora, nao erro (o
    campo `max` sai ao lado para quem quiser separar as duas coisas).
    """
    ordenado = sorted(latencias)
    indice = min(len(ordenado) - 1, max(0, math.ceil(0.95 * len(ordenado)) - 1))
    return max(ordenado[indice], statistics.median(ordenado))


def peso_da_lane(roteador, benchmark, politica: dict, lane: str):
    """(perfil, classe de custo, peso). Classe lida do YAML; ausente = None (gate)."""
    perfil = roteador.perfil_da_lane(politica, lane)
    classe = benchmark.classe_de_custo_da_politica(politica, perfil)
    peso = benchmark.PESO_CUSTO_RELATIVO.get(classe)
    return perfil, classe, peso


def medir(roteador, benchmark, politica: dict, casos: list,
          repeticoes: int = REPETICOES_DE_LATENCIA,
          confianca=None) -> dict:
    """Roda o corpus pela convencao do benchmark e agrega por LANE. Funcao pura no disco."""
    confianca = benchmark.CONFIANCA_DECLARADA if confianca is None else confianca
    ordem = roteador.ordem_lanes(politica)
    perfis, classes, pesos = {}, {}, {}
    for lane in ordem:
        perfis[lane], classes[lane], pesos[lane] = peso_da_lane(roteador, benchmark, politica, lane)

    por_lane = {lane: {"decisoes": 0, "latencias_ms": [], "outcomes": collections.Counter(),
                       "perfis_registrados": collections.Counter()}
                for lane in ordem}
    falsos_rebaixamento = 0
    abstencao = escalacao = 0

    for caso in casos:
        saida = benchmark.roda_caso(roteador, politica, caso, "proposta-homologada",
                                    repeticoes, confianca)
        decisao = saida["decisao"]
        lane = decisao["lane"]
        balde = por_lane.setdefault(lane, {"decisoes": 0, "latencias_ms": [],
                                           "outcomes": collections.Counter(),
                                           "perfis_registrados": collections.Counter()})
        balde["decisoes"] += 1
        balde["latencias_ms"].append(saida["latencia_ms"]["mediana"])
        balde["outcomes"][str(decisao["outcome"])] += 1
        balde["perfis_registrados"][str(saida["recibo"].get("model_profile"))] += 1
        if str(caso.get("lane_esperada")) == "critical" and lane != "critical":
            falsos_rebaixamento += 1
        if not decisao.get("pode_executar") and decisao.get("outcome") == "ESCALATE":
            abstencao += 1
        if decisao.get("exige_escalacao"):
            escalacao += 1

    total = len(casos)
    tabela = {}
    for lane in ordem:
        balde = por_lane.get(lane) or {}
        decisoes = int(balde.get("decisoes") or 0)
        latencias = list(balde.get("latencias_ms") or [])
        peso = pesos.get(lane)
        # Lane sem decisao reporta null, nunca 0: "nao medi" e "medi zero" sao coisas
        # diferentes, e o custo/latencia de uma lane que nao rodou nao e zero — e ausente.
        tabela[lane] = {
            "perfil_de_modelo": perfis.get(lane),
            "classe_de_custo": classes.get(lane),
            "decisoes": decisoes,
            "custo_relativo_da_lane": (peso * decisoes) if (decisoes and peso) else None,
            "peso_unitario_de_custo": peso,
            "latencia_ms_da_decisao": {
                "definicao": ("relogio de `decidir()` (mediana de "
                              f"{repeticoes} repeticoes por caso) — camada de decisao, "
                              "nao execucao por lane"),
                "mediana": round(statistics.median(latencias), 4) if latencias else None,
                "p95": round(_p95(latencias), 4) if latencias else None,
                "max": round(max(latencias), 4) if latencias else None,
                "amostras": len(latencias),
            },
            "outcomes": dict(balde.get("outcomes") or {}),
            "perfis_registrados_no_recibo": {str(k): v for k, v in
                                             (balde.get("perfis_registrados") or {}).items()},
        }

    custo_total = sum(v["custo_relativo_da_lane"] or 0 for v in tabela.values())
    return {
        "ordem_das_lanes": ordem,
        "total_de_casos": total,
        "por_lane": tabela,
        "custo_relativo_total": custo_total,
        "taxa_de_abstencao": round(abstencao / total, 4) if total else None,
        "taxa_de_escalacao": round(escalacao / total, 4) if total else None,
        "falso_rebaixamento": {"total": falsos_rebaixamento,
                               "taxa": round(falsos_rebaixamento / total, 4) if total else None},
        "blindagem_de_custo": {
            "definicao": ("`high` e `critical` compartilham a mesma classe de custo, entao o "
                          "custo por lane NAO enxerga rebaixamento critical->high"),
            "classes_medidas": {lane: classes.get(lane) for lane in ordem},
            "high_e_critical_mesma_classe": (classes.get("high") is not None
                                            and classes.get("high") == classes.get("critical")),
            "quem_enxerga_rebaixamento": "falso_rebaixamento",
        },
    }


def medir_dos_recibos(roteador, benchmark, politica: dict, diretorio: pathlib.Path) -> dict:
    """Custo por lane a partir das decisoes REAIS (recibos). Latencia: nao ha no recibo."""
    if not diretorio.is_dir():
        return {"disponivel": False, "motivo": f"diretorio de recibos ausente: {diretorio}",
                "caminho": caminho_relativo(diretorio)}
    arquivos = sorted(a for a in diretorio.glob("*.json") if a.is_file())
    por_lane = collections.Counter()
    custo_por_lane = collections.Counter()
    sem_classe = []
    lidos = recusados = 0
    for arquivo in arquivos:
        try:
            recibo = json.loads(arquivo.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - arquivo ilegivel nao derruba a medicao
            recusados += 1
            continue
        lane = str(recibo.get("lane") or "")
        if lane not in roteador.ordem_lanes(politica):
            recusados += 1
            continue
        lidos += 1
        por_lane[lane] += 1
        # O custo vem de lane -> perfil -> classe da POLITICA. O perfil registrado no
        # recibo serve de conferencia: divergir da politica e achado, nao silencio.
        perfil_do_recibo = str(recibo.get("model_profile") or "")
        _, _, peso = peso_da_lane(roteador, benchmark, politica, lane)
        if peso is None:
            sem_classe.append(lane)
            continue
        custo_por_lane[lane] += peso
        if perfil_do_recibo and perfil_do_recibo != str(roteador.perfil_da_lane(politica, lane)):
            sem_classe.append(f"{lane} (recibo diz {perfil_do_recibo})")
    return {
        "disponivel": True,
        "caminho": caminho_relativo(diretorio),
        "arquivos_no_diretorio": len(arquivos),
        "recibos_lidos": lidos,
        "arquivos_ignorados": recusados,
        "decisoes_por_lane": dict(por_lane),
        "custo_relativo_por_lane": dict(custo_por_lane),
        "latencia": ("NAO MEDIDA: o contrato do recibo tem 13 campos e nenhum deles carrega "
                     "latencia; medir latencia exige executar a camada de decisao (fonte: corpus)"),
        "achados": sorted(set(sem_classe)),
    }


def status_das_metricas_declaradas(politica: dict, agregado: dict, recibos: dict) -> dict:
    """Toda metrica declarada em `metricas.acompanhar` sai daqui com valor OU motivo.

    Metrica declarada e silenciosamente nao medida e pior que metrica ausente.
    """
    instrumentacao = (politica.get("metricas") or {}).get("instrumentacao") or {}
    nao_medivel = instrumentacao.get("nao_medivel") or {}
    valores = {
        "custo_por_lane": {lane: v["custo_relativo_da_lane"] for lane, v in agregado["por_lane"].items()},
        "latencia_por_lane": {lane: v["latencia_ms_da_decisao"]["mediana"]
                              for lane, v in agregado["por_lane"].items()},
        "taxa_de_abstencao": agregado["taxa_de_abstencao"],
        "taxa_de_escalacao": agregado["taxa_de_escalacao"],
        "falso_rebaixamento": agregado["falso_rebaixamento"],
        "custo_por_card_verified": None,
        "regressao_ou_retrabalho": None,
    }
    status = {}
    for nome in (politica.get("metricas") or {}).get("acompanhar") or []:
        nome = str(nome)
        valor = valores.get(nome)
        if valor is None:
            motivo = (str(nao_medivel.get(nome) or "").strip()
                      or MOTIVOS_PADRAO_DE_NAO_MEDICAO.get(nome)
                      or "sem instrumentacao declarada na politica")
            status[nome] = {"medida": False, "motivo": motivo,
                            "motivo_declarado_na_politica": nome in nao_medivel}
        else:
            status[nome] = {"medida": True, "valor": valor}
    # metricas que o instrumento mede e que esta politica ainda nao declara (ex.: a v1.0
    # nao lista `custo_por_lane`): aparecem marcadas, nunca escondidas no resultado.
    for nome, valor in valores.items():
        if nome not in status and valor is not None:
            status[nome] = {"medida": True, "valor": valor,
                            "declarada_na_politica": False,
                            "nota": "medida e nao declarada em metricas.acompanhar desta versao"}
    return status


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------
def montar_resultado(roteador, benchmark, politica: dict, casos: list, agregado: dict,
                     recibos: dict, gerado_em: str, caminho_politica,
                     caminho_corpus, confianca_usada: float) -> dict:
    instrumentacao = (politica.get("metricas") or {}).get("instrumentacao") or {}
    return {
        "instrumento": VERSAO_DO_INSTRUMENTO,
        "card": "TRE-W0-E04-T06",
        "gerado_em": gerado_em,
        "politica": {
            "caminho": caminho_relativo(caminho_politica),
            "versao": politica.get("versao"),
            "sha256": sha256_de(caminho_politica),
            "limiares": roteador.limiares(politica),
            "lane_conservadora": politica.get("_lane_conservadora"),
            "classe_de_custo_de_cada_lane": {lane: v["classe_de_custo"]
                                             for lane, v in agregado["por_lane"].items()},
            "peso_de_custo_de_cada_lane": {lane: v["peso_unitario_de_custo"]
                                           for lane, v in agregado["por_lane"].items()},
            "metricas_declaradas": list((politica.get("metricas") or {}).get("acompanhar") or []),
            "limites_declarados_na_politica": list(instrumentacao.get("limites") or []),
        },
        "roteador": {"caminho": caminho_relativo(ROTEADOR), "versao": roteador.ROUTER_VERSION,
                     "sha256": sha256_de(ROTEADOR)},
        "corpus": {"caminho": caminho_relativo(caminho_corpus),
                   "sha256": sha256_de(caminho_corpus), "casos": len(casos)},
        "convencao_de_entrada": {
            "fonte_da_convencao": caminho_relativo(BENCHMARK),
            "modo": "proposta-homologada",
            "confianca_usada": confianca_usada,
            "repeticoes_de_latencia_por_caso": REPETICOES_DE_LATENCIA,
        },
        "metricas": agregado,
        "metricas_declaradas": status_das_metricas_declaradas(politica, agregado, recibos),
        "decisoes_reais": recibos,
        "limitacoes": [
            "latencia medida e a da camada de decisao (roteador local), NAO a de execucao do card por lane;",
            "custo em unidade relativa declarada, NUNCA em dinheiro: a politica proibe fixar preco;",
            "custo por card VERIFIED (o objetivo declarado) NAO e medido: nenhum card do corpus foi executado ate VERIFIED;",
            "as decisoes reais vem dos recibos, que nao carregam latencia nem custo — custo por lane e derivado de lane -> perfil -> classe;",
            "lane sem decisao no periodo reporta null, nunca zero: 'nao medi' e 'medi zero' sao coisas diferentes;",
            "o corpus nao carrega codigo canonico de acao em 28 dos 32 casos (achado do TRE-W0-E04-T03): a lane medida e a do encanamento de bloqueio/escalacao, nao a de execucao;",
        ],
    }


# ---------------------------------------------------------------------------
# Autoteste
# ---------------------------------------------------------------------------
def _escrever_copia(dado: dict, prefixo: str, yaml) -> pathlib.Path:
    """Politica mutada em diretorio TEMPORARIO — o arquivo versionado nunca e tocado.

    O dump do YAML perde os comentarios, e a politica declara o vocabulario de resultado
    (`PASS | RETRY | ESCALATE | BLOCK`) num comentario — vocabulario que o roteador EXIGE
    encontrar no texto. A copia recebe o mesmo vocabulario como cabecalho: sem isso o
    mutante nem carrega e o teste mediria a coisa errada.
    """
    destino = pathlib.Path(tempfile.mkdtemp(prefix=prefixo + "-")) / "politica_copia.yaml"
    destino.write_text(
        "# copia de prova (mutacao em diretorio temporario)\n"
        "# vocabulario de resultado: PASS | RETRY | ESCALATE | BLOCK\n"
        + yaml.safe_dump(dado, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return destino


def autoteste(roteador, benchmark, yaml, politica: dict, casos: list, agregado: dict,
              caminho_politica, caminho_corpus, sha_corpus_inicial, sha_politica_inicial) -> int:
    falhas, itens = [], []

    def item(nome, ok, detalhe=""):
        itens.append((nome, ok, detalhe))
        if not ok:
            falhas.append(nome)

    # 1. sensibilidade: a classe de custo vem do YAML, nao do codigo.
    dado = yaml.safe_load(pathlib.Path(caminho_politica).read_text(encoding="utf-8"))
    alvo = None
    for lane, v in agregado["por_lane"].items():
        if v["decisoes"] and v["classe_de_custo"] in benchmark.PESO_CUSTO_RELATIVO:
            alvo = lane
            break
    item("corpus tem lane com decisao (base do teste de sensibilidade)", alvo is not None,
         f"{alvo!r}")
    if alvo:
        perfil = agregado["por_lane"][alvo]["perfil_de_modelo"]
        classe_atual = agregado["por_lane"][alvo]["classe_de_custo"]
        nova = "baixo" if classe_atual != "baixo" else "alto"
        mutado = json.loads(json.dumps(dado))
        mutado["perfis_modelo"][perfil]["custo"] = nova
        arquivo = _escrever_copia(mutado, "metrica-mut", yaml)
        try:
            politica_mutada = roteador.carregar_politica(arquivo)
            novo = medir(roteador, benchmark, politica_mutada, casos, repeticoes=1)
            antes = agregado["por_lane"][alvo]["custo_relativo_da_lane"]
            depois = novo["por_lane"][alvo]["custo_relativo_da_lane"]
            item("metrica sensivel: trocar a classe de custo no YAML muda o custo por lane",
                 antes != depois,
                 f"lane {alvo} ({perfil}): classe {classe_atual}->{nova}, custo {antes}->{depois}")
        except Exception as erro:  # noqa: BLE001
            item("metrica sensivel: trocar a classe de custo no YAML muda o custo por lane",
                 False, f"excecao ao medir o mutante: {type(erro).__name__}: {erro}")

    # 2. honestidade: lane sem decisao reporta null, nao zero
    sem_decisao = [l for l, v in agregado["por_lane"].items() if not v["decisoes"]]
    if sem_decisao:
        lane = sem_decisao[0]
        v = agregado["por_lane"][lane]
        item("honestidade: lane sem decisao reporta null (nunca zero)",
             v["custo_relativo_da_lane"] is None and v["latencia_ms_da_decisao"]["mediana"] is None,
             f"lane {lane}: custo={v['custo_relativo_da_lane']!r} "
             f"latencia={v['latencia_ms_da_decisao']['mediana']!r}")
    else:
        item("honestidade: o corpus exercita menos lanes que o total (base do teste de null)",
             False, "todas as lanes tiveram decisao — teste de null sem base")

    # 3. latencia medida e coerente
    incoerentes = []
    for lane, v in agregado["por_lane"].items():
        lat = v["latencia_ms_da_decisao"]
        if lat["mediana"] is None:
            continue
        if not (lat["mediana"] > 0 and lat["p95"] >= lat["mediana"] and lat["max"] >= lat["p95"]):
            incoerentes.append((lane, lat))
    item("latencia por lane: mediana > 0, p95 >= mediana e max >= p95",
         not incoerentes, f"incoerentes={incoerentes}")

    # 4. gate: perfil de lane SEM classe de custo e RECUSADO (somar zero seria mentir)
    dado2 = json.loads(json.dumps(dado))
    perfil_alvo = next(iter(dado2["perfis_modelo"]))
    dado2["perfis_modelo"][perfil_alvo].pop("custo", None)
    arquivo2 = _escrever_copia(dado2, "metrica-sem-custo", yaml)
    pesos = {}
    try:
        politica2 = roteador.carregar_politica(arquivo2)
        pesos = {lane: peso_da_lane(roteador, benchmark, politica2, lane)[2]
                 for lane in roteador.ordem_lanes(politica2)}
    except Exception:  # noqa: BLE001 - politica que nao carrega tambem entra pela recusa
        pesos = {}
    sem_custo = sorted(l for l, p in pesos.items() if not p)
    item("gate: perfil de lane sem classe de custo e detectado (a medicao recusa, nao soma zero)",
         bool(sem_custo), f"perfil sem `custo`: {perfil_alvo}; lanes sem peso={sem_custo}")

    # 5. read-only
    item("instrumento e read-only no corpus", sha256_de(caminho_corpus) == sha_corpus_inicial)
    item("instrumento e read-only na politica", sha256_de(caminho_politica) == sha_politica_inicial)

    # 6. completude: toda metrica declarada sai com valor OU motivo
    status = status_das_metricas_declaradas(politica, agregado, {"disponivel": False})
    declaradas = [str(x) for x in (politica.get("metricas") or {}).get("acompanhar") or []]
    faltando = [m for m in declaradas if m not in status]
    item("completude: toda metrica declarada sai no resultado (valor ou motivo)",
         not faltando, f"faltando={faltando}")
    sem_motivo = [m for m, v in status.items()
                  if not v.get("medida") and not str(v.get("motivo") or "").strip()]
    item("completude: metrica nao medida sempre traz o motivo", not sem_motivo,
         f"sem motivo={sem_motivo}")

    # 7. a blindagem de custo e DADO, e o instrumento acusa se ela mudar
    blindagem = agregado["blindagem_de_custo"]
    item("blindagem de custo medida e declarada como dado (nao como nota de rodape)",
         "high_e_critical_mesma_classe" in blindagem
         and blindagem["quem_enxerga_rebaixamento"] == "falso_rebaixamento",
         f"high={blindagem['classes_medidas'].get('high')} "
         f"critical={blindagem['classes_medidas'].get('critical')} "
         f"mesma_classe={blindagem['high_e_critical_mesma_classe']}")

    print("AUTOTESTE do instrumento de metricas por lane:")
    for nome, ok, detalhe in itens:
        print(f"  [{'OK' if ok else 'FALHOU'}] {nome}" + (f" — {detalhe}" if detalhe else ""))
    print(f"AUTOTESTE: {len(itens) - len(falhas)}/{len(itens)} itens OK, {len(falhas)} falhas")
    return 1 if falhas else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Metricas de custo e latencia por lane do JEV.")
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--politica", default=None,
                        help=("caminho da politica (padrao: a EM VIGOR no repo). Apontar para "
                              "politica ausente/ilegivel/de versao desconhecida tem de RECUSAR "
                              "(exit 3), nunca medir em modo degradado"))
    parser.add_argument("--recibos", default=str(RECIBOS),
                        help="diretorio com os recibos das decisoes reais do encanamento")
    parser.add_argument("--saida", default=None,
                        help="arquivo de resultado (padrao: hermes/jev/benchmarks/metricas-por-lane-<data>-<politica>.json)")
    parser.add_argument("--imprimir-json", action="store_true", help="imprime o resultado completo")
    parser.add_argument("--repeticoes", type=int, default=REPETICOES_DE_LATENCIA)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)

    import yaml
    benchmark = carrega_modulo(BENCHMARK, "benchmark_da_metrica")
    roteador = carrega_modulo(ROTEADOR, "roteador_da_metrica")

    caminho_corpus = pathlib.Path(args.corpus)
    if not caminho_corpus.is_file():
        print(f"FALHOU: corpus nao encontrado em {caminho_corpus}")
        return 1
    sha_corpus_inicial = sha256_de(caminho_corpus)
    bruto = yaml.safe_load(caminho_corpus.read_text(encoding="utf-8"))
    casos = bruto.get("casos") or []

    caminho_politica = pathlib.Path(args.politica) if args.politica else roteador.CAMINHO_POLITICA_PADRAO
    if not caminho_politica.is_file():
        print(f"RECUSADO (gate de politica): politica ausente em {caminho_politica}")
        return 3
    sha_politica_inicial = sha256_de(caminho_politica)
    try:
        politica = roteador.carregar_politica(caminho_politica)
    except roteador.PoliticaInvalida as erro:
        print(f"RECUSADO (gate de politica): {erro}")
        print("Sem politica valida nao ha lane, limiar nem perfil: medir aqui mediria o modo "
              "degradado (lane high + escalacao obrigatoria), nao o roteador.")
        return 3

    if not casos:
        print(f"FALHOU: corpus sem casos ({caminho_corpus})")
        return 1

    # ---- gates --------------------------------------------------------------
    pesos = {lane: peso_da_lane(roteador, benchmark, politica, lane)[2]
             for lane in roteador.ordem_lanes(politica)}
    sem_custo = sorted(l for l, p in pesos.items() if not p)
    if sem_custo:
        print(f"RECUSADO (gate de metrica): as lanes {sem_custo} apontam para perfil sem "
              f"classe de custo em `perfis_modelo` — somar zero seria mentir a metrica")
        return 3

    gerado_em = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    inicio = time.perf_counter()
    agregado = medir(roteador, benchmark, politica, casos, repeticoes=args.repeticoes)
    duracao_s = round(time.perf_counter() - inicio, 3)
    recibos = medir_dos_recibos(roteador, benchmark, politica, pathlib.Path(args.recibos))
    resultado = montar_resultado(roteador, benchmark, politica, casos, agregado, recibos,
                                 gerado_em, caminho_politica, caminho_corpus,
                                 benchmark.CONFIANCA_DECLARADA)
    resultado["duracao_da_rodada_s"] = duracao_s

    # ---- resumo -------------------------------------------------------------
    print(f"politica : {politica.get('versao')}  sha256={sha_politica_inicial[:12]}  "
          f"lane_conservadora={politica.get('_lane_conservadora')}  "
          f"caminho={caminho_relativo(caminho_politica)}")
    print(f"roteador : {roteador.ROUTER_VERSION}  corpus: {len(casos)} casos "
          f"(sha256={sha_corpus_inicial[:12]})  duracao={duracao_s}s")
    print()
    print(f"{'lane':10} {'classe':7} {'peso':>5} {'decisoes':>9} {'custo':>7} "
          f"{'lat.mediana(ms)':>16} {'lat.p95(ms)':>12}")
    for lane, v in agregado["por_lane"].items():
        lat = v["latencia_ms_da_decisao"]
        print(f"{lane:10} {str(v['classe_de_custo']):7} {str(v['peso_unitario_de_custo']):>5} "
              f"{v['decisoes']:>9} {str(v['custo_relativo_da_lane']):>7} "
              f"{str(lat['mediana']):>16} {str(lat['p95']):>12}")
    print()
    print(f"custo relativo total (corpus) : {agregado['custo_relativo_total']} "
          f"(unidade declarada, NAO dinheiro)")
    print(f"taxa de abstencao / escalacao : {agregado['taxa_de_abstencao']} / {agregado['taxa_de_escalacao']}")
    print(f"falso rebaixamento (critical) : {agregado['falso_rebaixamento']['total']}")
    b = agregado["blindagem_de_custo"]
    print(f"blindagem de custo            : high={b['classes_medidas'].get('high')} "
          f"critical={b['classes_medidas'].get('critical')} "
          f"mesma_classe={b['high_e_critical_mesma_classe']} "
          f"(rebaixamento so aparece em `falso_rebaixamento`)")
    print()
    print("metricas declaradas na politica:")
    for nome, s in resultado["metricas_declaradas"].items():
        if s.get("medida"):
            extra = "" if s.get("declarada_na_politica", True) else "  (medida e nao declarada nesta versao)"
            print(f"  MEDIDA     {nome}{extra}")
        else:
            print(f"  NAO MEDIDA {nome} — {s.get('motivo')}")
    print()
    print(f"decisoes reais (recibos): {recibos.get('recibos_lidos')} lidos em "
          f"{recibos.get('caminho')} | latencia: {str(recibos.get('latencia'))[:60]}...")

    # ---- saida --------------------------------------------------------------
    if args.saida:
        destino = pathlib.Path(args.saida)
    else:
        data = gerado_em[:10]
        destino = (RAIZ / "hermes/jev/benchmarks"
                   / f"metricas-por-lane-{data}-{resultado['politica']['versao']}.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nresultado gravado em {destino} (sha256={sha256_de(destino)[:12]})")
    if args.imprimir_json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))

    if args.autoteste:
        codigo = autoteste(roteador, benchmark, yaml, politica, casos, agregado, caminho_politica,
                           caminho_corpus, sha_corpus_inicial, sha_politica_inicial)
        if codigo:
            print("instrumento: AUTOTESTE FALHOU")
            return codigo

    if sha256_de(caminho_corpus) != sha_corpus_inicial or sha256_de(caminho_politica) != sha_politica_inicial:
        print("FALHOU: corpus ou politica mudaram durante a medicao (instrumento nao e read-only)")
        return 1
    print("instrumento: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
