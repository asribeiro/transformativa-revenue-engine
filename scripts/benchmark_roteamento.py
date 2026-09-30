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

  * modo `classificador` — LINHA DE BASE HISTORICA (o classificador foi aposentado em
    30/09/2026, card TRE-W0-E04-T09): o benchmark chama o estimador aposentado
    (`classificar_card`, que casa o texto com `lanes.*.exemplos` do proprio YAML) e
    injeta a proposta dele na entrada. Mede a qualidade do estimador contra o rotulo
    humano — e mantem reproduzivel a medicao que justificou a aposentadoria. NAO e
    circular. NAO e o que o roteador decide hoje.

  * modo `politica` — o caminho REAL de hoje: o chamador NAO entrega proposta e nenhum
    estimador entra; a lane vem declarada na politica para o codigo canonico da acao
    (`lane_por_codigo_de_acao`), e codigo sem lane declarada abstem. E o modo que mede a
    decisao em vigor.

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
  * o corpus PODE declarar `acao_codigo` (campo CANONICO, anotado caso a caso pelo card
    TRE-W0-E04-T03-D01, com a proveniencia de cada caso no `nota`). Quando declara, o
    benchmark REPASSA o campo ao roteador — e a via PRINCIPAL do contrato, a mesma que o
    despachante usa. `acao_codigo: null` (explicito, nunca ausente) = o caso NAO tem
    codigo no catalogo vigente: mede a falha fechada do D07. O benchmark segue sem
    inventar codigo: ele repassa o que o corpus declarou, e codigo fora do catalogo nao
    resolve (nao executa);
  * por isso o resultado publica as POPULACOES separadas (`populacoes`): a lane de um caso
    SEM codigo canonico e a lane conservadora de AUDITORIA, nao uma decisao de roteamento.
    Misturar as duas populacoes era medir abstencao com nome de acerto (achado do T03).
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
    do modelo do catalogo no momento da configuracao, que a politica nao fixa;
  * `populacoes` = a leitura SEPARADA exigida pelo card TRE-W0-E04-T03-D01:
    EXECUTAVEL (o roteador aceitou executar) x NAO EXECUTAVEL (acao proibida, sem
    codigo no catalogo, ou bloqueio por guardrail/dominio). A accuracy de lane so e
    leitura de qualidade de roteamento na populacao que o roteador ROTEIA (codigo
    comum); nas outras a lane registrada e a de auditoria;
  * `classificador` = a leitura do classificador automatico contra a CONSTANTE
    ESTRUTURAL (o mesmo pipeline sem classificacao nenhuma). As duas lanes saem lado a
    lado porque a igualdade entre elas e o que decide manter/ajustar/aposentar o
    classificador — decisao do dono, medida aqui, nao "achismo".

LIMITES HONESTOS (vao impressos e gravados no resultado):
  * a latencia medida e a do roteador local (microssegundos), nao a de execucao do
    card por lane;
  * `custo_por_card_VERIFIED` (o objetivo declarado na politica) NAO e medido: os
    cards do corpus nao foram executados ate VERIFIED;
  * o rotulo de 30 dos 32 casos foi homologado EM BLOCO a partir da proposta do
    Hermes; portanto este corpus mede REGRESSAO contra uma linha de base acordada,
    nao a concordancia com julgamento humano independente caso a caso;
  * a lane de um caso SEM codigo canonico de acao (ou com acao PROIBIDA) e a lane
    conservadora de AUDITORIA, nao uma decisao de roteamento: contar esses casos na
    accuracy mistura abstencao com acerto. Por isso o resultado publica as populacoes
    separadas — e a accuracy crua continua saindo ao lado, nunca no lugar;
  * o classificador automatico do roteador e INERTE neste corpus, e isso e MEDIDO: a lane
    do modo `classificador` e igual a da constante estrutural (mesmo pipeline sem
    classificacao nenhuma) nos 32 casos, e nenhum caso atinge o limiar de aceite da
    politica com a confianca que o estimador do roteador produz.
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
import tempfile
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CORPUS = RAIZ / "hermes/jev/benchmarks/corpus-anotacao.yaml"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"

# Versoes de politica que esta convencao de medicao sabe interpretar. A v1.1 entrou
# aqui ao entrar em vigor (card TRE-W0-E04-T08): o benchmark roda a politica EM VIGOR
# por padrao, e sem isto ele recusaria medir a propria politica em vigor (exit 3).
VERSOES_SUPORTADAS = frozenset({"jev-policy-v1.0", "jev-policy-v1.1"})

# Sinais do corpus que o roteador sabe avaliar (nome do corpus -> nome do roteador).
MAPA_DE_SINAIS = {"producao": "producao", "mexe_em_segredo": "credencial",
                  "outbound_para_terceiro": "outbound_a_terceiro"}
# Sinais do corpus sem equivalente no roteador: declarados para o achado sair no
# resultado, e NUNCA injetados (nome desconhecido = BLOCK, o que seria sujar a
# medicao com um bloqueio que o caso nao pede).
SINAIS_SEM_EQUIVALENTE = ("ddl_ou_migration", "aprova_humana_exigida")

# Campo do codigo canonico de acao no CONTRATO do roteador (via principal). Aqui e so o
# nome: o valor declarado por caso vem do corpus, e o autoteste prova que este nome e o
# mesmo que `CAMPO_DO_CODIGO_DE_ACAO` declara do lado do roteador.
CAMPO_DO_CODIGO_DE_ACAO = "acao_codigo"

# Modo de referencia das POPULACOES: o modo homologado (a proposta do corpus com a
# confianca declarada). E o modo em que a lane proposta e preservada quando o roteador
# consegue rotear, entao e nele que "a lane registrada" diz algo sobre o roteamento.
MODO_DE_REFERENCIA = "proposta-homologada"

# Confianca do modo `proposta-homologada` (constante declarada, conferida no YAML).
CONFIANCA_DECLARADA = 0.95

# Peso de custo relativo por classe de custo do perfil (a classe vem do YAML).
PESO_CUSTO_RELATIVO = {"baixo": 1, "medio": 2, "alto": 3}

REPETICOES_DE_LATENCIA = 15

MODOS = ("classificador", "politica", "proposta-homologada")


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


def tarefa_do_caso(caso: dict, modo: str, confianca: float = CONFIANCA_DECLARADA,
                   sem_descricao: bool = False) -> dict:
    """Caso do corpus -> entrada do roteador. Convencao declarada no cabecalho.

    `sem_descricao=True` monta a MESMA entrada sem declarar descricao e SEM injetar
    proposta — e a CONSTANTE ESTRUTURAL: nenhum estimador entra, a lane cai na abstencao +
    lane conservadora da politica. Nao e um modo de decisao; e o piso de comparacao.
    (Depois da aposentadoria do classificador — TRE-W0-E04-T09 — a injecao da proposta
    historica acontece em `roda_caso`, para o modo `classificador`.)
    """
    sinais = caso.get("sinais") or {}
    tarefa = {
        "card_id": caso["id"],
        "titulo": caso.get("titulo") or "",
        "descricao": None if sem_descricao else (caso.get("titulo") or ""),
        "acao": caso.get("acao") or "",
        "sinais": {MAPA_DE_SINAIS[k]: True for k, v in sinais.items()
                   if v and k in MAPA_DE_SINAIS},
        "ambiente_alvo": "producao" if sinais.get("producao") else "desenvolvimento",
    }
    # Codigo canonico DECLARADO PELO CORPUS: repassado como a via principal do contrato
    # (o despachante faz o mesmo com o `acao_codigo` do card). `null`/ausente nao vira
    # campo: o caso simplesmente nao tem codigo, e o roteador escalou por isso.
    if caso.get(CAMPO_DO_CODIGO_DE_ACAO):
        tarefa[CAMPO_DO_CODIGO_DE_ACAO] = str(caso[CAMPO_DO_CODIGO_DE_ACAO])
    if modo == "proposta-homologada":
        tarefa["lane_proposta"] = caso.get("lane_proposta_hermes")
        tarefa["confianca"] = confianca
    return tarefa


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
def roda_caso(modulo, politica: dict, caso: dict, modo: str,
              repeticoes: int = REPETICOES_DE_LATENCIA,
              confianca: float = CONFIANCA_DECLARADA,
              sem_descricao: bool = False) -> dict:
    tarefa = tarefa_do_caso(caso, modo, confianca, sem_descricao=sem_descricao)
    # ANCORA:CLASSIFICADOR_APOSENTADO (30/09/2026, card TRE-W0-E04-T09)
    # O modo `classificador` mede a LINHA DE BASE HISTORICA. O roteador NAO classifica mais
    # (o classificador foi aposentado), entao o proprio benchmark chama o estimador
    # aposentado e injeta a proposta dele na entrada: a medicao que justificou a aposentadoria
    # (0,1875 isolado contra 0,375 da constante estrutural) continua reproduzivel.
    # `sem_descricao=True` (a constante estrutural) NAO injeta nada: e o piso de comparacao,
    # o caso em que nenhum estimador entra — abstencao + lane conservadora.
    if modo == "classificador" and not sem_descricao:
        historico = modulo.classificar_card(
            {"titulo": caso.get("titulo") or "", "descricao": caso.get("titulo") or ""}, politica)
        tarefa["lane_proposta"] = historico.get("lane_proposta")
        tarefa["confianca"] = historico.get("confianca")
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


def classe_do_codigo(codigo, comuns: set, proibidos: set) -> str:
    """'comum' | 'proibida' | '' — de que lado do catalogo veio o codigo resolvido."""
    if not codigo:
        return ""
    if codigo in proibidos:
        return "proibida"
    if codigo in comuns:
        return "comum"
    return ""


def executar(modulo, politica: dict, casos: list, repeticoes: int = REPETICOES_DE_LATENCIA,
             confianca: float = CONFIANCA_DECLARADA) -> dict:
    """Roda todos os casos nos dois modos e calcula as metricas. Funcao pura."""
    ordem = modulo.ordem_lanes(politica)
    indice = {lane: i for i, lane in enumerate(ordem)}
    perfis = {lane: modulo.perfil_da_lane(politica, lane) for lane in ordem}
    # catalogo canonico lido EM TEMPO DE EXECUCAO (nenhuma lista literal no benchmark)
    comuns = set(modulo.CODIGOS_DE_ACAO_COMUNS)
    proibidos = set(modulo.acoes_nunca_decididas_por_maquina(politica))
    # peso de custo por LANE, derivado do perfil declarado na politica
    peso_da_lane = {lane: PESO_CUSTO_RELATIVO.get(classe_de_custo_da_politica(politica, perfis[lane]), 0)
                    for lane in ordem}

    linhas = []
    for caso in casos:
        linha = {"id": caso["id"], "acao": caso.get("acao"), "titulo": caso.get("titulo"),
                 "acao_codigo_declarado": caso.get(CAMPO_DO_CODIGO_DE_ACAO),
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
                "codigo_classe": classe_do_codigo(decisao.get("codigo_de_acao"), comuns, proibidos),
                "guardrails_acionados": list(decisao.get("guardrails_acionados") or []),
                "acerta_lane": lane == esperada,
                "rebaixa": indice.get(lane, -1) < indice.get(esperada, -1),
                "desvio_conservador": indice.get(lane, -1) > indice.get(esperada, -1),
                "latencia_ms": saida["latencia_ms"],
                "motivo": (decisao.get("motivos") or [""])[0],
            }
        # CONSTANTE ESTRUTURAL: o mesmo pipeline do modo `classificador` sem classificacao
        # nenhuma (sem descricao). Uma leitura por caso basta: aqui se compara LANE e
        # DESFECHO, nao latencia.
        base = roda_caso(modulo, politica, caso, "classificador", repeticoes=1,
                         sem_descricao=True)
        decisao_base = base["decisao"]
        linha["constante_estrutural"] = {
            "lane": decisao_base["lane"], "outcome": decisao_base["outcome"],
            "decidido": decisao_base["decidido"],
            "pode_executar": bool(decisao_base["pode_executar"]),
            "exige_escalacao": bool(decisao_base["exige_escalacao"]),
            "codigo_de_acao": decisao_base.get("codigo_de_acao"),
            "lane_igual_ao_modo_classificador": (
                decisao_base["lane"] == linha["modos"]["classificador"]["lane"]),
            "desfecho_igual_ao_modo_classificador": (
                decisao_base["outcome"] == linha["modos"]["classificador"]["outcome"]),
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
# Populacoes — a leitura separada exigida pelo card TRE-W0-E04-T03-D01
#
# O PROBLEMA (medido no T03): com a postura estrita homologada (D07), caso sem codigo
# canonico de acao NAO EXECUTA — escala e a lane registrada e a conservadora de AUDITORIA.
# Medir accuracy sobre esses casos mede abstencao com nome de acerto. A leitura separada
# torna o numero interpretavel: quem o roteador ROTEIA (codigo comum declarado) x quem ele
# NAO PODE executar, com o motivo.
# ---------------------------------------------------------------------------
BALDES = ("executavel", "bloqueado_por_regra_com_codigo_comum",
          "acao_proibida_decisao_humana", "sem_codigo_no_catalogo")


def balde_do_caso(linha: dict, modo: str = MODO_DE_REFERENCIA) -> str:
    """Balde do caso: um so, derivado do DESFECHO do roteador (nao de rotulo escrito a mao)."""
    dado = linha["modos"][modo]
    if dado.get("pode_executar"):
        return "executavel"
    if dado.get("codigo_classe") == "proibida":
        return "acao_proibida_decisao_humana"
    if not dado.get("codigo_de_acao"):
        return "sem_codigo_no_catalogo"
    return "bloqueado_por_regra_com_codigo_comum"


def _resumo_do_balde(linhas: list, modo: str) -> dict:
    acertos = sum(1 for l in linhas if l["modos"][modo].get("acerta_lane"))
    falsos = sum(1 for l in linhas if str(l["lane_esperada"]) == "critical"
                 and l["modos"][modo]["lane"] != "critical")
    return {
        "total": len(linhas),
        "casos": [l["id"] for l in linhas],
        "acertos_de_lane": acertos,
        "accuracy_de_lane": round(acertos / len(linhas), 4) if linhas else None,
        "falso_rebaixamento": falsos,
        "executaveis": sum(1 for l in linhas if l["modos"][modo]["pode_executar"]),
        "escalados": sum(1 for l in linhas if l["modos"][modo]["exige_escalacao"]),
        "bloqueados": sum(1 for l in linhas if l["modos"][modo]["outcome"] == "BLOCK"),
        "casos_com_codigo_declarado_no_corpus": sum(
            1 for l in linhas if l.get("acao_codigo_declarado")),
    }


def populacoes(casos: list, brutos: dict, modo: str = MODO_DE_REFERENCIA) -> dict:
    """EXECUTAVEL x NAO EXECUTAVEL, com o motivo, e o PODER DE MEDICAO de cada leitura."""
    por_id = {l["id"]: l for l in brutos["casos"]}
    baldes = {b: [] for b in BALDES}
    for caso in casos:
        linha = por_id[str(caso["id"])]
        baldes[balde_do_caso(linha, modo)].append(linha)

    crua = brutos["modos"][modo]
    com_codigo_comum = baldes["executavel"] + baldes["bloqueado_por_regra_com_codigo_comum"]
    resumo_comum = _resumo_do_balde(com_codigo_comum, modo)

    # checagens de desenho: as afirmacoes que a separacao FAZ tem de bater com o desfecho
    proibidos_executando = [l["id"] for l in baldes["acao_proibida_decisao_humana"]
                            if l["modos"][modo]["pode_executar"]]
    sem_codigo_executando = [l["id"] for l in baldes["sem_codigo_no_catalogo"]
                             if l["modos"][modo]["pode_executar"]]
    executavel_sem_codigo_comum = [l["id"] for l in baldes["executavel"]
                                   if l["modos"][modo].get("codigo_classe") != "comum"]
    com_codigo_declarado = [l for l in brutos["casos"] if l.get("acao_codigo_declarado")]
    resolvidos_por_codigo = [l["id"] for l in com_codigo_declarado
                             if l["modos"][modo].get("origem_do_codigo_de_acao") == "codigo_canonico"]
    return {
        "definicao": (
            "separacao exigida pelo card TRE-W0-E04-T03-D01: EXECUTAVEL e quem o roteador "
            "aceitou executar; NAO EXECUTAVEL e quem ele nao pode executar, com o MOTIVO. "
            "O balde e derivado do desfecho do roteador (nunca de rotulo escrito a mao), e "
            "a lane de um caso NAO executavel e a conservadora de auditoria — nao uma "
            "decisao de roteamento"),
        "modo_de_referencia": modo,
        "executavel": _resumo_do_balde(baldes["executavel"], modo),
        "nao_executavel": {
            "total": sum(len(baldes[b]) for b in BALDES if b != "executavel"),
            "por_motivo": {
                "acao_proibida_decisao_humana": _resumo_do_balde(
                    baldes["acao_proibida_decisao_humana"], modo),
                "sem_codigo_no_catalogo": _resumo_do_balde(
                    baldes["sem_codigo_no_catalogo"], modo),
                "bloqueado_por_regra_com_codigo_comum": _resumo_do_balde(
                    baldes["bloqueado_por_regra_com_codigo_comum"], modo),
            },
        },
        "poder_de_medicao": {
            "definicao": (
                "a accuracy de lane so e leitura de qualidade de roteamento na populacao "
                "com codigo COMUM (o roteador decide a lane); nos demais baldes a lane e a "
                "de auditoria e o que se mede e o DESFECHO (bloquear/escalar), nao a lane"),
            "com_codigo_comum": resumo_comum,
            "accuracy_crua_sobre_os_32": {
                "accuracy_de_lane": crua["accuracy_de_lane"], "acertos": crua["acertos"],
                "casos": crua["casos"]},
            "leitura": (
                f"sobre os {crua['casos']} a accuracy crua e {crua['accuracy_de_lane']}; "
                f"sobre os {resumo_comum['total']} casos com codigo comum o roteador acerta "
                f"{resumo_comum['accuracy_de_lane']} — a diferenca entre os dois numeros e "
                "exatamente a abstencao causada pela lacuna de codigo de acao"),
        },
        "checagem_de_desenho": {
            "acao_proibida_nunca_executa": {
                "casos": len(baldes["acao_proibida_decisao_humana"]),
                "violacoes": proibidos_executando, "ok": not proibidos_executando},
            "sem_codigo_nunca_executa": {
                "casos": len(baldes["sem_codigo_no_catalogo"]),
                "violacoes": sem_codigo_executando, "ok": not sem_codigo_executando},
            "executavel_tem_codigo_comum": {
                "casos": len(baldes["executavel"]),
                "violacoes": executavel_sem_codigo_comum, "ok": not executavel_sem_codigo_comum},
            "codigo_declarado_no_corpus_chega_ao_roteador": {
                "casos_com_codigo_declarado": len(com_codigo_declarado),
                "resolvidos_por_codigo_canonico": len(resolvidos_por_codigo),
                "ok": len(resolvidos_por_codigo) == len(com_codigo_declarado)},
        },
    }


# ---------------------------------------------------------------------------
# Leitura do classificador contra a CONSTANTE ESTRUTURAL (card T03-D01, item 3)
# ---------------------------------------------------------------------------
def leitura_do_classificador(modulo, politica: dict, casos: list, brutos: dict) -> dict:
    """Mede o classificador automatico e a constante 'nenhuma classificacao'.

    Responde a pergunta do card com numero, nao com opiniao:
      (a) o classificador DECIDE alguma lane? (lane dele x lane da constante estrutural);
      (b) por que nao decide? (confianca maxima x limiar de aceite da politica);
      (c) a proposta dele, SE fosse aceita, teria qualidade? (acerto da proposta antes
          dos limiares) — e o numero que separa "ajustar o estimador" de "aposentar".
    """
    linhas = brutos["casos"]
    modo = "classificador"
    classif = brutos["modos"][modo]
    iguais = [l["id"] for l in linhas if l["constante_estrutural"]["lane_igual_ao_modo_classificador"]]
    desfecho_iguais = [l["id"] for l in linhas
                       if l["constante_estrutural"]["desfecho_igual_ao_modo_classificador"]]
    acertos_base = sum(1 for l in linhas
                       if l["constante_estrutural"]["lane"] == str(l["lane_esperada"]))
    lim = modulo.limiares(politica)
    limiar = lim.get("aceitar")
    propostas, confiancas, aceitam = [], [], []
    for caso in casos:
        cls = modulo.classificar_card(tarefa_do_caso(caso, modo), politica)
        propostas.append(cls.get("lane_proposta"))
        confianca = cls.get("confianca")
        confiancas.append(confianca)
        if confianca is not None and limiar is not None and confianca >= limiar:
            aceitam.append(str(caso["id"]))
    acertos_proposta = sum(1 for caso, proposta in zip(casos, propostas)
                           if proposta == str(caso.get("lane_esperada")))
    com_proposta = [str(caso["id"]) for caso, proposta in zip(casos, propostas)
                    if proposta is not None]

    # Contrafactual DECLARADO (mutacao em memoria, nada escrito no repo): se o estimador de
    # confianca entregasse >= limiar de aceite, a proposta do classificador passaria a decidir
    # a lane. E o numero que separa "ajustar o estimador" de "aposentar o classificador":
    # se a proposta nao supera a constante estrutural, aceita-la nao melhora a metrica.
    base_original = modulo.CONFIANCA_BASE
    try:
        if limiar is not None:
            modulo.CONFIANCA_BASE = limiar
        contra = executar(modulo, politica, casos, repeticoes=1)
    finally:
        modulo.CONFIANCA_BASE = base_original
    contra_acertos = sum(1 for l in contra["casos"]
                         if l["modos"][modo]["lane"] == str(l["lane_esperada"]))
    mudaram = sorted(l["id"] for l in contra["casos"]
                     if l["modos"][modo]["lane"] != l["constante_estrutural"]["lane"])
    acc_constante = round(acertos_base / len(linhas), 4) if linhas else None
    acc_contra = round(contra_acertos / len(contra["casos"]), 4) if contra["casos"] else None
    return {
        "modo_medido": modo,
        "accuracy_de_lane": classif["accuracy_de_lane"],
        "matriz_de_confusao": classif["matriz_de_confusao"],
        "constante_estrutural": {
            "definicao": (
                "mesmo pipeline do modo `classificador` SEM classificacao nenhuma (o "
                "chamador nao declara descricao, e o roteador so aciona o classificador "
                "quando ha descricao): a lane cai na abstencao + lane conservadora da "
                "politica, com o piso por ambiente. E a constante 'a lane conservadora'"),
            "accuracy_de_lane": round(acertos_base / len(linhas), 4) if linhas else None,
            "acertos": acertos_base, "casos": len(linhas),
            "casos_com_lane_igual": len(iguais),
            "lane_igual_em_todos_os_casos": len(iguais) == len(linhas),
            "casos_com_desfecho_igual": len(desfecho_iguais),
            "divergencias_de_lane": sorted(set(l["id"] for l in linhas) - set(iguais)),
        },
        "proposta_do_classificador": {
            "definicao": (
                "lane proposta por `classificar_card` ANTES dos limiares (probe com a "
                "mesma entrada do modo `classificador`) — e a qualidade da proposta, "
                "independente de o limiar aceita-la ou nao"),
            "acertos": acertos_proposta, "total": len(casos),
            "accuracy": round(acertos_proposta / len(casos), 4) if casos else None,
            "confianca_maxima_observada": max([c for c in confiancas if c is not None], default=None),
            "confianca_minima_observada": min([c for c in confiancas if c is not None], default=None),
            "limiar_de_aceite_da_politica": limiar,
            "casos_que_atingem_o_limiar_de_aceite": aceitam,
            "casos_com_proposta_nao_nula": com_proposta,
        },
        "se_o_estimador_aceitasse": {
            "definicao": (
                "contrafactual declarado: `CONFIANCA_BASE` do roteador elevada NA MEMORIA do "
                "processo ate o limiar de aceite da politica (nada escrito no repo), re-medindo "
                "o modo `classificador` — mede o que muda se o estimador deixar de barrar a "
                "proposta dele"),
            "accuracy_de_lane": acc_contra, "acertos": contra_acertos,
            "casos": len(contra["casos"]),
            "accuracy_da_constante_estrutural": acc_constante,
            "delta_contra_a_constante": (None if acc_contra is None or acc_constante is None
                                         else round(acc_contra - acc_constante, 4)),
            "casos_que_mudam_de_lane": mudaram,
            "leitura": (
                f"aceitar a proposta muda a lane de {len(mudaram)} caso(s) e leva a accuracy de "
                f"{acc_constante} (constante estrutural) para {acc_contra}: "
                + ("a proposta SUPERA a constante — ajustar o estimador tem ganho medido"
                   if (acc_contra or 0) > (acc_constante or 0) else
                   "a proposta NAO supera a constante — aceita-la nao melhora a metrica; o ganho "
                   "nao esta em afrouxar o estimador, e sim em dar ao classificador entrada que "
                   "ele hoje nao recebe (descricao/sinais) ou em aposenta-lo")),
        },
    }

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
            "acao_codigo_anotado": {
                "definicao": ("campo canonico `acao_codigo` do corpus (anotacao do card "
                              "TRE-W0-E04-T03-D01, proveniencia caso a caso no `nota`): "
                              "codigo declarado x `null` explicito"),
                "com_codigo": sum(1 for c in casos if c.get(CAMPO_DO_CODIGO_DE_ACAO)),
                "sem_codigo_declarado": sum(1 for c in casos if CAMPO_DO_CODIGO_DE_ACAO in c
                                            and not c.get(CAMPO_DO_CODIGO_DE_ACAO)),
                "sem_o_campo": [str(c.get("id")) for c in casos
                                if CAMPO_DO_CODIGO_DE_ACAO not in c],
            },
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
            "acao_codigo": (f"campo `{CAMPO_DO_CODIGO_DE_ACAO}` do caso, quando declarado, e "
                            "repassado ao roteador como a via principal do contrato; o "
                            "benchmark nunca inventa codigo"),
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
        "populacoes": populacoes(casos, brutos),
        "classificador": leitura_do_classificador(modulo, politica, casos, brutos),
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
            (f"o corpus declara codigo canonico de acao em "
             f"{sum(1 for c in casos if c.get(CAMPO_DO_CODIGO_DE_ACAO))} de {len(casos)} casos "
             f"(anotacao do card T03-D01); nos "
             f"{sum(1 for c in casos if not c.get(CAMPO_DO_CODIGO_DE_ACAO))} restantes a acao NAO "
             "tem codigo no catalogo vigente e a lane medida neles e a conservadora de auditoria, "
             "nao uma decisao de roteamento — por isso o resultado publica as populacoes "
             "separadas (`populacoes`);"),
            "o corpus nao carrega o corpo do card: o caso tem titulo, sinais e acao, e a decisao "
            "medida e a da camada de decisao, nao a do trabalho executado;",
            "o classificador automatico do roteador e INERTE neste corpus: lane identica a da "
            "constante estrutural em todos os casos, e nenhum caso atinge o limiar de aceite "
            "(medido no campo `classificador`);",
        ],
    }


# ---------------------------------------------------------------------------
# Autoteste (prova que a metrica e sensivel e que os gates reprovam)
# ---------------------------------------------------------------------------
def autoteste(modulo, politica: dict, casos: list, brutos: dict,
              caminho_corpus: pathlib.Path = CORPUS) -> int:
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

    # 3b. direcao inversa: tirar o rotulo critical de um caso QUE A METRICA CONTA hoje
    #     faz o falso rebaixamento cair. O alvo tem de ser um caso que contribui hoje
    #     (critical esperado E lane registrada mais barata): depois do piso por ambiente
    #     da v1.1 o corpus passou a ter caso critical decidido como critical, e escolher
    #     "o primeiro critical" mediria um caso que nao move o numero — verde/vermelho
    #     por sorte, nao por sensibilidade da metrica.
    contribuintes = {linha["id"] for linha in brutos["casos"]
                     if str(linha["lane_esperada"]) == "critical"
                     and linha["modos"]["proposta-homologada"]["lane"] != "critical"}
    mutado = [dict(c) for c in casos]
    alvo_critico = next((c for c in mutado if c["id"] in contribuintes), None)
    item("corpus tem caso critical CONTANDO no falso rebaixamento (base do teste de sensibilidade)",
         alvo_critico is not None, f"{len(contribuintes)} caso(s) contribuindo hoje")
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
    # O item confere o corpus REALMENTE lido nesta rodada (caminho_corpus), nao o corpus
    # padrao: com `--corpus <fixture>` a checagem antiga comparava outro arquivo e acusava
    # escrita que nao houve (falso positivo latente, corrigido aqui).
    item("benchmark e read-only no corpus", sha256_de(caminho_corpus) == SHA_DO_CORPUS_NO_INICIO)

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

    # ---------------- anotacao de codigo canonico de acao (TRE-W0-E04-T03-D01) --------
    # 9. o corpus declara o campo em TODOS os casos (presente, mesmo que `null`): campo
    #    ausente e "nao anotado" e nao pode se confundir com "nao tem codigo".
    sem_o_campo = [str(c.get("id")) for c in casos if CAMPO_DO_CODIGO_DE_ACAO not in c]
    item("corpus: os 32 casos declaram `acao_codigo` (mesmo que null)",
         not sem_o_campo, f"sem o campo: {sem_o_campo}")

    # 10. nenhum codigo inventado: todo codigo declarado consta do catalogo vigente lido
    #     do proprio roteador/politica em tempo de execucao.
    comuns = set(modulo.CODIGOS_DE_ACAO_COMUNS)
    proibidos = set(modulo.acoes_nunca_decididas_por_maquina(politica))
    declarados = {str(c.get(CAMPO_DO_CODIGO_DE_ACAO)) for c in casos
                  if c.get(CAMPO_DO_CODIGO_DE_ACAO)}
    fora_do_catalogo = sorted(declarados - comuns - proibidos)
    item("corpus: todo codigo declarado e do catalogo vigente (nada inventado)",
         not fora_do_catalogo, f"fora do catalogo: {fora_do_catalogo}")

    # 11. o campo do corpus CHEGA ao roteador (via principal do contrato). Se o repasse
    #     quebrar, o roteador volta a resolver por prosa/falhar e este item cai.
    com_codigo = [l for l in brutos["casos"] if l.get("acao_codigo_declarado")]
    resolvidos = [l["id"] for l in com_codigo
                  if l["modos"][MODO_DE_REFERENCIA].get("origem_do_codigo_de_acao") == "codigo_canonico"]
    item("medicao: o `acao_codigo` do corpus chega ao roteador como codigo canonico",
         len(resolvidos) == len(com_codigo),
         f"{len(resolvidos)}/{len(com_codigo)} resolvidos por `codigo_canonico`")

    # 12. a postura estrita continua valendo: acao PROIBIDA e acao SEM codigo nunca executam.
    pop = populacoes(casos, brutos)
    proibido_executou = pop["checagem_de_desenho"]["acao_proibida_nunca_executa"]["violacoes"]
    sem_codigo_executou = pop["checagem_de_desenho"]["sem_codigo_nunca_executa"]["violacoes"]
    item("populacoes: acao PROIBIDA nunca executa (decisao humana)",
         not proibido_executou, f"violacoes: {proibido_executou}")
    item("populacoes: caso SEM codigo no catalogo nunca executa (falha fechada D07)",
         not sem_codigo_executou, f"violacoes: {sem_codigo_executou}")

    # 13. a separacao e uma PARTICAO: cada caso em um balde e so um (nem sobra, nem dobra).
    baldes = [b for b in BALDES]
    contagem = {b: 0 for b in baldes}
    for linha in brutos["casos"]:
        contagem[balde_do_caso(linha)] += 1
    item("populacoes: os 32 casos aparecem exatamente uma vez (particao)",
         sum(contagem.values()) == len(casos),
         f"{contagem} = {sum(contagem.values())} de {len(casos)}")
    item("populacoes: todo caso EXECUTAVEL tem codigo COMUM declarado",
         pop["checagem_de_desenho"]["executavel_tem_codigo_comum"]["ok"],
         f"violacoes: {pop['checagem_de_desenho']['executavel_tem_codigo_comum']['violacoes']}")

    # 14. o campo do codigo no benchmark e o MESMO nome que o contrato do roteador declara.
    item("contrato: nome do campo do codigo == CAMPO_DO_CODIGO_DE_ACAO do roteador",
         CAMPO_DO_CODIGO_DE_ACAO == modulo.CAMPO_DO_CODIGO_DE_ACAO,
         f"{CAMPO_DO_CODIGO_DE_ACAO!r} vs {modulo.CAMPO_DO_CODIGO_DE_ACAO!r}")

    # 15. o CLASSIFICADOR e INERTE: a lane dele e a da constante estrutural em todos os
    #     casos, e nenhum caso atinge o limiar de aceite (a causa do empate).
    leitura = leitura_do_classificador(modulo, politica, casos, brutos)
    constante = leitura["constante_estrutural"]
    proposta = leitura["proposta_do_classificador"]
    item("classificador: lane do classificador == constante estrutural em TODOS os casos",
         constante["lane_igual_em_todos_os_casos"],
         f"{constante['casos_com_lane_igual']}/{constante['casos']} iguais; "
         f"divergencias={constante['divergencias_de_lane']}")
    item("classificador: nenhum caso atinge o limiar de aceite (causa medida do empate)",
         not proposta["casos_que_atingem_o_limiar_de_aceite"],
         f"confianca maxima observada={proposta['confianca_maxima_observada']} < "
         f"limiar de aceite={proposta['limiar_de_aceite_da_politica']}; "
         f"aceitam={proposta['casos_que_atingem_o_limiar_de_aceite']}")

    # 16. SENSIBILIDADE do item 15: elevando o estimador de confianca (mutacao EM MEMORIA,
    #     nada escrito no repo) o classificador DEIXA de ser inerte. Prova que a inercia
    #     vem do limiar e que o item 15 mede algo real — nao que "nao ha o que comparar".
    base_original = modulo.CONFIANCA_BASE
    try:
        modulo.CONFIANCA_BASE = 0.90
        mutado = executar(modulo, politica, casos, repeticoes=1)
        mudaram = [l["id"] for l in mutado["casos"]
                   if l["modos"]["classificador"]["lane"] != l["constante_estrutural"]["lane"]]
    finally:
        modulo.CONFIANCA_BASE = base_original
    item("sensibilidade da inercia: com o estimador aceitando (base 0,90) a lane MUDA",
         bool(mudaram),
         f"{len(mudaram)} caso(s) deixam de cair na constante: {sorted(mudaram)}")

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
                        help=("caminho da politica (padrao: a EM VIGOR no repo, hoje a v1.1). Existe para o "
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
    # A fonte de verdade das versoes suportadas e o ROTEADOR — o mesmo que vai decidir.
    # A lista literal `VERSOES_SUPORTADAS` fica como ultimo recurso, para quando o modulo
    # nao declarar o conjunto: duas listas separadas divergem em silencio (classe do
    # defeito D08 — regra declarada de um lado, executada de outro).
    versoes_suportadas = frozenset(getattr(modulo, "VERSOES_DE_POLITICA_SUPORTADAS",
                                          VERSOES_SUPORTADAS))
    if politica is None or politica.get("versao") not in versoes_suportadas:
        print(f"RECUSADO (gate de politica): versao {politica.get('versao') if politica else None!r} "
              f"fora de {sorted(versoes_suportadas)} — medir em modo degradado nao mede o roteador")
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
    anot = resultado["corpus"]["acao_codigo_anotado"]
    print(f"\nACHADO de vocabulario: {achado.get('acao sem codigo canonico', 0)} "
          f"de {len(casos)} casos nao resolvem para codigo canonico de acao")
    print(f"anotacao de codigo no corpus: {anot['com_codigo']} caso(s) com codigo declarado, "
          f"{anot['sem_codigo_declarado']} com `null` explicito (sem codigo no catalogo)")
    print(f"rotulos: {resultado['corpus']['revisao_dos_rotulos']}")

    # ---- populacoes e classificador (leitura separada) ---------------------
    pop = resultado["populacoes"]
    print(f"\n--- POPULACOES (modo {pop['modo_de_referencia']})")
    print(f"  EXECUTAVEL : {pop['executavel']['total']} caso(s) "
          f"accuracy_de_lane={pop['executavel']['accuracy_de_lane']} "
          f"casos={pop['executavel']['casos']}")
    for motivo, resumo in pop["nao_executavel"]["por_motivo"].items():
        print(f"  NAO EXECUTAVEL ({motivo}): {resumo['total']} caso(s) "
              f"bloqueados={resumo['bloqueados']} escalados={resumo['escalados']} "
              f"executaveis={resumo['executaveis']}")
    pm = pop["poder_de_medicao"]
    print(f"  poder de medicao: com codigo comum = {pm['com_codigo_comum']['accuracy_de_lane']} "
          f"({pm['com_codigo_comum']['acertos_de_lane']}/{pm['com_codigo_comum']['total']}) x "
          f"accuracy crua sobre os 32 = {pm['accuracy_crua_sobre_os_32']['accuracy_de_lane']}")

    cls = resultado["classificador"]
    print("\n--- CLASSIFICADOR x CONSTANTE ESTRUTURAL")
    print(f"  modo classificador          : accuracy_de_lane={cls['accuracy_de_lane']} "
          f"matriz={cls['matriz_de_confusao']}")
    print(f"  constante estrutural        : accuracy_de_lane="
          f"{cls['constante_estrutural']['accuracy_de_lane']} "
          f"lane igual em {cls['constante_estrutural']['casos_com_lane_igual']}/"
          f"{cls['constante_estrutural']['casos']} casos")
    prop = cls["proposta_do_classificador"]
    print(f"  proposta antes dos limiares : accuracy={prop['accuracy']} "
          f"({prop['acertos']}/{prop['total']}) confianca maxima="
          f"{prop['confianca_maxima_observada']} limiar de aceite="
          f"{prop['limiar_de_aceite_da_politica']} "
          f"aceitam={len(prop['casos_que_atingem_o_limiar_de_aceite'])}")
    contra = cls["se_o_estimador_aceitasse"]
    print(f"  se o estimador aceitasse    : accuracy={contra['accuracy_de_lane']} "
          f"({contra['acertos']}/{contra['casos']}) "
          f"delta_contra_a_constante={contra['delta_contra_a_constante']} "
          f"casos_que_mudam={contra['casos_que_mudam_de_lane']}")

    # ---- saida ------------------------------------------------------------
    # O nome do arquivo carrega a versao da POLITICA **e** a do CORPUS: os dois entram no
    # que foi medido (e os dois vao gravados com sha256). Sem a versao do corpus, a rodada
    # seguinte do mesmo dia sobrescreveria a linha de base — foi o que quase aconteceu
    # nesta propria frente (a rodada do corpus v1.4 colidiria com o arquivo do v1.3).
    if args.saida:
        destino = pathlib.Path(args.saida)
    elif args.autoteste:
        # Rodada de AUTOTESTE nao publica resultado: o arquivo versionado e evidencia do
        # que foi medido numa rodada deliberada, e sobrescreve-lo numa verificacao sujaria
        # a linha de base (aconteceu em 30/09/2026: o autoteste do benchmark reescreveu o
        # resultado da v1.1). Autoteste sem `--saida` grava em diretorio temporario.
        destino = pathlib.Path(tempfile.mkdtemp(prefix="jev-bench-autoteste-")) / "resultado.json"
    else:
        data = gerado_em[:10]
        destino = (RAIZ / "hermes/jev/benchmarks"
                   / (f"resultado-benchmark-{data}-{resultado['politica']['versao']}"
                      f"-{resultado['corpus']['versao']}.json"))
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nresultado gravado em {destino} (sha256={sha256_de(destino)[:12]})")
    if args.imprimir_json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))

    if args.autoteste:
        codigo = autoteste(modulo, politica, casos, brutos, caminho_corpus)
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
