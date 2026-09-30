#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Anota o codigo comum `execucao_de_card` nos casos do corpus — card TRE-W0-E04-T10.

O QUE ESTE ARQUIVO RESOLVE (medido, nao suposto)
  A anotacao do card TRE-W0-E04-T03-D01 (corpus v1.4) mediu: 18 dos 32 casos tinham por
  acao `execucao_de_card` — execucao generica de card de desenvolvimento — e ficaram com
  `acao_codigo: null` porque esse codigo NAO EXISTIA no catalogo. Com a postura estrita
  homologada (texto livre sem codigo canonico nao executa, D07), esses 18 casos eram NAO
  EXECUTAVEIS por lacuna de catalogo: o proprio trabalho de execucao de card nao tinha por
  onde ser declarado. O card TRE-W0-E04-T10 existe porque o dono NOMEOU o codigo (30/09/2026,
  escopo estreito — desenvolvimento, sem producao e sem credencial). Nomeado o codigo, os 18
  casos passam a ter codigo canonico declarado, e a medicao passa a medir o roteamento deles
  em vez da lacuna.

CRITERIO DE ATRIBUICAO (o mesmo do T03-D01, declarado, para nao virar fabricacao)
  O codigo so e atribuido quando a PROPRIA DECLARACAO do caso o identifica:

    (a) o rotulo `acao` do caso E o codigo do catalogo. E o caso destes 18: `acao:
        execucao_de_card` e `execucao_de_card` passou a ser codigo comum do roteador.

  NUNCA pelo texto da `justificativa` nem pela `titulo`: inferir o codigo da prosa seria
  inventar o dado que se quer medir. Por isso o script DERIVA a lista de casos do proprio
  corpus (rotulo `acao` == codigo E `acao_codigo` nulo) e exige que ela seja IGUAL ao mapa
  declarado aqui — lista a mao que ficasse menor que a derivacao, ou maior, e FALHA.

O QUE O SCRIPT **NAO** FAZ
  * nao altera `lane_esperada` homologada (30 em bloco + 2 individuais, 29/09/2026) — provado
    por comparacao profunda antes/depois, campo a campo; divergencia de lane e ACHADO para o
    Anderson, nunca correcao de rotulo por aqui;
  * nao altera `lane_proposta_hermes`, `acao`, `titulo`, `sinais`, `justificativa`, `origem`
    nem `homologacao`;
  * nao inventa codigo: todo codigo escrito tem de constar do catalogo vigente lido EM TEMPO
    DE EXECUCAO (CODIGOS_DE_ACAO_COMUNS do roteador + as acoes de `nunca_decidido_por_maquina`
    da politica em vigor). Codigo fora do catalogo = FALHA ANTES DE ESCREVER (exit 1), arquivo
    intocado;
  * nao mexe nos 3 casos que continuam sem codigo: `borda-06`, `borda-07` e `borda-10` tem
    acao que NAO e o codigo nomeado (concessao de credencial, ajuste de interface, resposta a
    cliente). Eles so recebem, no `nota`, a marca de que a v1.5 os reavaliou — o `acao_codigo`
    deles continua `null` explicito;
  * nao escreve se o corpus ja e o de saida (guarda de re-execucao): rodar de novo falha alto.

PROVENIENCIA
  A proveniencia nova e ANEXADA ao campo `nota`, substituindo APENAS o trecho do card anterior
  (TRE-W0-E04-T03-D01) que dizia "nenhum no catalogo vigente"; a proveniencia da lane
  homologada continua intacta, na frente.

USO
    python3 scripts/anotar_execucao_de_card_no_corpus.py
    python3 scripts/anotar_execucao_de_card_no_corpus.py --ensaio
    # provas negativas (escrevem em COPIA, nunca no corpus versionado):
    ... --corpus /tmp/copia.yaml --mapa /tmp/mapa.json

Codigos de saida: 0 = anotado (ou ensaio OK); 1 = FALHOU (nada foi escrito) ou nada a fazer;
3 = RECUSA de gate (catalogo divergente do espelho do encaixe).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CORPUS = RAIZ / "hermes/jev/benchmarks/corpus-anotacao.yaml"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
POLITICA_EM_VIGOR = RAIZ / "hermes/jev/policy_v1_2.yaml"
ACOES_DECLARADAS = RAIZ / "hermes/jev/acoes-declaradas.yaml"
VERSAO_DE_ENTRADA = "corpus-anotacao-v1.4"
VERSAO_DE_SAIDA = "corpus-anotacao-v1.5"
CODIGO = "execucao_de_card"
CARD = "TRE-W0-E04-T10"

# Campos que a anotacao NAO pode tocar (a prova compara antes/depois um a um).
CAMPOS_PROTEGIDOS = ("id", "origem", "titulo", "acao", "sinais", "lane_proposta_hermes",
                     "lane_esperada", "justificativa", "homologacao")

# O trecho do card ANTERIOR que este card substitui nas notas dos 18 casos (era a declaracao
# de "sem codigo no catalogo") e a cauda que morria com ele.
TRECHO_ANTERIOR = " ; codigo de acao (TRE-W0-E04-T03-D01) nenhum no catalogo vigente — "
CAUDA_ANTERIOR = ("; caso NAO EXECUTAVEL por lacuna de catalogo (falha fechada D07)")

NOTA_DA_VERSAO = (
    "v1.5 (30/09/2026, card TRE-W0-E04-T10): o dono NOMEOU o codigo comum `execucao_de_card` "
    "(execucao generica de card de DESENVOLVIMENTO; escopo estreito — sem producao e sem "
    "credencial) e os 18 casos cujo rotulo `acao` E esse codigo passam a declarar "
    "`acao_codigo: execucao_de_card` (criterio (a) do T03-D01: o codigo so vem da propria "
    "declaracao do caso, nunca da prosa). Os 3 casos que seguem sem codigo no catalogo "
    "(borda-06 concessao de credencial, borda-07 ajuste de interface, borda-10 resposta a "
    "cliente) continuam `acao_codigo: null` explicito e recebem so a marca de reavaliacao. "
    "Nenhum `lane_esperada` foi alterado (30 em bloco + 2 individuais de 29/09/2026 "
    "preservados, provado por comparacao profunda campo a campo) e nenhum campo protegido "
    "mudou. O codigo novo NAO afrouxa o escopo: card que declare dominio sensivel (producao, "
    "credencial, dado de cliente) continua NAO EXECUTAVEL por falha fechada do D07, e o piso "
    "por ambiente continua elevando — provado por comportamento em "
    "scripts/verificar_jev_policy_v1_3.py (parte 5)."
)

# ---------------------------------------------------------------------------
# MAPA DA ANOTACAO (o contrato deste card, caso a caso)
#
# `com_codigo`: caso -> (codigo, motivo/proveniencia). O codigo e conferido contra o catalogo
#   lido em tempo de execucao; nome que nao existir no catalogo = FALHA.
# `sem_codigo`: caso -> motivo pelo qual a acao CONTINUA sem codigo no catalogo vigente.
# `ja_com_codigo`: caso -> motivo pelo qual o codigo declarado na v1.4 e PRESERVADO. Este card
#   nao sobrescreve codigo ja declarado — nem para "melhorar" o codigo, porque trocar codigo
#   de caso ja anotado e decisao de politica, nao de anotacao.
# Todo caso do corpus tem de estar em EXATAMENTE UMA das tres listas: caso nao declarado =
# FALHA (nada de default silencioso).
# ---------------------------------------------------------------------------
MAPA = {
    "com_codigo": {
        "real-t_ac8a2130": (
            CODIGO,
            "criar a estrutura do repositorio e execucao de card de desenvolvimento — o rotulo "
            "`acao` do caso E o codigo nomeado; nada aqui encosta em ambiente vivo nem opera "
            "credencial"),
        "real-t_4be20bcc": (
            CODIGO,
            "definir a gestao de secrets e execucao de card de desenvolvimento (desenho de "
            "seguranca, que NAO opera credencial) — o rotulo `acao` do caso E o codigo nomeado; "
            "se o card passasse a operar credencial, a falha fechada o barraria pelo dominio"),
        "real-t_d6dc5a4c": (
            CODIGO,
            "instalar e configurar o Odoo Community e execucao de card de desenvolvimento "
            "(multi-sistema, em desenvolvimento) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_1acf11f2": (
            CODIGO,
            "configurar TLS, reverse proxy e security e execucao de card de desenvolvimento em "
            "ambiente de dev — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_e0489efc": (
            CODIGO,
            "criar a API controlada do Odoo e execucao de card de desenvolvimento (integracao "
            "entre servicos, em dev) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_cdc21b43": (
            CODIGO,
            "implementar o company upsert e execucao de card de desenvolvimento (funcao nova "
            "com teste) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_fd3e41f0": (
            CODIGO,
            "implementar o Scout Agent e execucao de card de desenvolvimento (funcao nova com "
            "teste) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_d9be7d3c": (
            CODIGO,
            "implementar o Research Agent e execucao de card de desenvolvimento (funcao nova "
            "com teste) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_e4a90eba": (
            CODIGO,
            "ICP Score V1 e execucao de card de desenvolvimento (regra de negocio nova) — o "
            "rotulo `acao` do caso E o codigo nomeado"),
        "real-t_11815e63": (
            CODIGO,
            "Automation Fit Score V1 e execucao de card de desenvolvimento (regra de negocio "
            "nova) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_6267d886": (
            CODIGO,
            "configurar o SMTP do Titan e execucao de card de desenvolvimento (canal externo, "
            "sem outbound a terceiro) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_9d38e360": (
            CODIGO,
            "configurar o IMAP do Titan e execucao de card de desenvolvimento (canal externo, "
            "sem outbound a terceiro) — o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_eb323dd7": (
            CODIGO,
            "captura de lead no website e execucao de card de desenvolvimento (canal externo) "
            "— o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_33bc1765": (
            CODIGO,
            "ingestao de lead do Meta e execucao de card de desenvolvimento (canal externo) — "
            "o rotulo `acao` do caso E o codigo nomeado"),
        "real-t_6cc75a1d": (
            CODIGO,
            "funnel dashboard e execucao de card de desenvolvimento (visualizacao interna) — o "
            "rotulo `acao` do caso E o codigo nomeado"),
        "real-t_0248a568": (
            CODIGO,
            "conversao por segmento e execucao de card de desenvolvimento (entrega de "
            "engenharia) — o rotulo `acao` do caso E o codigo nomeado; o codigo "
            "`consulta_interna` NAO e atribuido, porque nada na declaracao do caso aponta a "
            "acao de consulta"),
        "real-t_f599bd02": (
            CODIGO,
            "calibrar o score e execucao de card de desenvolvimento (engenharia com teste) — o "
            "rotulo `acao` do caso E o codigo nomeado"),
        "real-t_2821c15b": (
            CODIGO,
            "predictive scoring e execucao de card de desenvolvimento (modelo novo) — o rotulo "
            "`acao` do caso E o codigo nomeado; o card trata dado de cliente e a falha fechada "
            "segue decidindo por dominio, nao por codigo"),
    },
    "sem_codigo": {
        "borda-06": "conceder credencial de deploy ao papel Sales AI nao e rotacao nem "
                    "revogacao de credencial (o catalogo proibido cobre rotacao/revogacao) e "
                    "nao e execucao de card de desenvolvimento; o bloqueio de hoje vem do "
                    "guardrail de papel, nao do codigo de acao",
        "borda-07": "ajustar rotulo de coluna no dashboard interno e ajuste de interface, nao "
                    "ajuste de texto, consulta, operacao comercial, migracao nem execucao de "
                    "card de desenvolvimento",
        "borda-10": "explicar ao cliente o atraso do relatorio e resposta a cliente existente "
                    "(outbound de resposta); o catalogo proibido cobre primeiro contato e "
                    "proposta comercial, e o codigo nomeado cobre execucao de card de "
                    "desenvolvimento — nenhum dos dois e esta acao",
    },
    "ja_com_codigo": {
        "borda-01": "ja declara `ajuste_de_texto` desde a v1.4 (o rotulo `acao` do caso ja era "
                    "o codigo comum) — nao ha o que re-anotar",
        "borda-11": "ja declara `ajuste_de_texto` desde a v1.4 (o rotulo `acao` do caso ja era "
                    "o codigo comum) — nao ha o que re-anotar",
        "borda-02": "ja declara `migracao_de_esquema` desde a v1.4 (migration em producao — o "
                    "guardrail de DDL segue bloqueando e o piso por ambiente mantem critical)",
        "real-t_969affa7": "ja declara `migracao_de_esquema` desde a v1.4 (sinais do caso + "
                           "regra declarada da politica, criterio (b) do T03-D01) — o rotulo "
                           "`acao` e `execucao_de_card`, mas o codigo comum que cobre a "
                           "operacao declarada (DDL/migration em dev) e `migracao_de_esquema`, "
                           "e codigo ja declarado NAO e sobrescrito por este card",
        "real-t_d9cb5755": "ja declara `migracao_de_esquema` desde a v1.4 (sinais do caso + "
                           "regra declarada da politica, criterio (b) do T03-D01) — mesma "
                           "razao de real-t_969affa7; sobrescrever seria trocar codigo de caso "
                           "ja anotado",
        "borda-03": "ja declara `primeiro_contato_outbound` desde a v1.4 (acao de "
                    "`nunca_decidido_por_maquina`)",
        "borda-04": "ja declara `rotacao_ou_revogacao_de_credencial` desde a v1.4 (acao de "
                    "`nunca_decidido_por_maquina`)",
        "borda-05": "ja declara `publicacao_em_nome_da_transformativa` desde a v1.4 (acao de "
                    "`nunca_decidido_por_maquina`)",
        "borda-08": "ja declara `exclusao_de_dado_de_cliente` desde a v1.4 (acao de "
                    "`nunca_decidido_por_maquina`)",
        "borda-09": "ja declara `rollback_em_producao` desde a v1.4 (acao de "
                    "`nunca_decidido_por_maquina`)",
        "borda-12": "ja declara `mudanca_estrutural_de_arquitetura` desde a v1.4 (acao de "
                    "`nunca_decidido_por_maquina`)",
    },
}

FORMATO_COM_CODIGO = "codigo de acao ({card}) {codigo} — {motivo}"
FORMATO_REAVALIADO = ("reavaliado na v1.5 ({card}) e CONTINUA sem codigo no catalogo vigente "
                      "— {motivo}; segue NAO EXECUTAVEL por lacuna de catalogo (falha fechada "
                      "D07)")


class Recusa(Exception):
    """A anotacao seria invalida: nada e escrito."""


def carrega_modulo(caminho: pathlib.Path, nome: str):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec is not None and spec.loader is not None, f"nao carreguei {caminho}"
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def sha256_de(caminho) -> str:
    return hashlib.sha256(pathlib.Path(caminho).read_bytes()).hexdigest()


def catalogo(roteador, politica) -> tuple:
    """(comuns, proibidos) lidos EM TEMPO DE EXECUCAO — nenhuma lista literal aqui."""
    comuns = tuple(roteador.CODIGOS_DE_ACAO_COMUNS)
    proibidos = tuple(roteador.acoes_nunca_decididas_por_maquina(politica))
    return comuns, proibidos


def confere_espelho_do_encaixe(comuns, yaml, acoes_declaradas: pathlib.Path) -> list:
    """O espelho `codigos_validos` do acoes-declaradas.yaml tem de bater com o roteador."""
    dado = yaml.safe_load(acoes_declaradas.read_text(encoding="utf-8"))
    espelho = tuple(str(c) for c in (dado or {}).get("codigos_validos") or [])
    if set(espelho) != set(comuns):
        return [f"espelho `codigos_validos` do {acoes_declaradas.name} = {list(espelho)} "
                f"diverge de CODIGOS_DE_ACAO_COMUNS do roteador = {list(comuns)}"]
    return []


def deriva_do_corpus(casos: list, codigo: str = CODIGO) -> list:
    """Os casos que a PROPRIA declaracao identifica: rotulo `acao` == codigo E codigo nulo.

    E a derivacao do criterio (a) do T03-D01 — medida no corpus, nao escrita a mao. O mapa
    declarado tem de bater com ela nos dois sentidos.
    """
    return sorted(str(c["id"]) for c in casos
                  if str(c.get("acao")) == codigo and c.get("acao_codigo") in (None, ""))


def valida_mapa(mapa: dict, casos: list, comuns, proibidos, derivados: list) -> list:
    """Falhas do mapa ANTES de escrever qualquer coisa."""
    falhas = []
    ids = [str(c["id"]) for c in casos]
    com = dict(mapa.get("com_codigo") or {})
    sem = dict(mapa.get("sem_codigo") or {})
    ja = dict(mapa.get("ja_com_codigo") or {})
    if len(set(ids)) != len(ids):
        falhas.append("corpus com id repetido")
    grupos = {"com_codigo": com, "sem_codigo": sem, "ja_com_codigo": ja}
    for a, b in (("com_codigo", "sem_codigo"), ("com_codigo", "ja_com_codigo"),
                 ("sem_codigo", "ja_com_codigo")):
        repetidos = sorted(set(grupos[a]) & set(grupos[b]))
        if repetidos:
            falhas.append(f"caso declarado em `{a}` E em `{b}`: {repetidos}")
    nao_declarados = sorted(set(ids) - set(com) - set(sem) - set(ja))
    if nao_declarados:
        falhas.append(f"caso SEM anotacao declarada: {nao_declarados}")
    inexistentes = sorted((set(com) | set(sem) | set(ja)) - set(ids))
    if inexistentes:
        falhas.append(f"caso declarado no mapa mas ausente do corpus: {inexistentes}")
    catalogo = set(comuns) | set(proibidos)
    for caso, valor in sorted(com.items()):
        codigo = valor[0] if isinstance(valor, (tuple, list)) else valor
        if codigo not in catalogo:
            falhas.append(f"{caso}: codigo {codigo!r} NAO esta no catalogo vigente "
                          f"(comuns={list(comuns)}, proibidos={list(proibidos)}) — "
                          "codigo inventado nao entra no corpus")
        if not isinstance(valor, (tuple, list)) or not str(valor[1] if len(valor) > 1 else "").strip():
            falhas.append(f"{caso}: codigo sem proveniencia declarada")
    for caso, motivo in sorted(list(sem.items()) + list(ja.items())):
        if not str(motivo).strip():
            falhas.append(f"{caso}: sem motivo declarado")
    # A DERIVACAO e o mapa declarado tem de ser a mesma coisa: nem um caso a menos (mapa
    # desatualizado deixaria caso executavel sem codigo em silencio) nem um a mais (mapa
    # escrito a mao atribuiria codigo que o caso nao declara).
    declarados = sorted(com)
    if declarados != derivados:
        falhas.append(f"mapa declarado != derivacao do corpus: so no mapa="
                      f"{sorted(set(declarados) - set(derivados))}, so na derivacao="
                      f"{sorted(set(derivados) - set(declarados))}")
    # Todo caso cujo ROTULO `acao` e o codigo nomeado tem de estar revisto: ou ganha o codigo
    # agora (`com_codigo`), ou ja tem codigo declarado e e preservado (`ja_com_codigo`). Caso
    # nenhum dos dois seria caso com rotulo do codigo nomeado e sem codigo, sem ninguem
    # respondendo por ele.
    rotulo_do_codigo = sorted(str(c["id"]) for c in casos if str(c.get("acao")) == CODIGO)
    orfaos = sorted(set(rotulo_do_codigo) - set(com) - set(ja))
    if orfaos:
        falhas.append(f"caso com rotulo `acao` == {CODIGO!r} e sem revisao declarada "
                      f"(nem `com_codigo` nem `ja_com_codigo`): {orfaos}")
    return falhas


def _yaml_escalar(chave: str, texto: str, yaml) -> str:
    return yaml.safe_dump({chave: texto}, allow_unicode=True, default_flow_style=False,
                          width=10 ** 6).strip()


def _anexa(linha: str, nova: str) -> str:
    """Anexa a proveniencia ao `nota` existente sem trocar o estilo do escalar."""
    if re.search(r"[#]", nova) or ": " in nova or nova[:1] in "-?*&!|>%@`":
        raise Recusa(f"proveniencia com caractere que exigiria quoting: {nova!r}")
    return f"{linha.rstrip()} ; {nova}"


def anota_texto(texto: str, mapa: dict, yaml) -> tuple:
    """Anota o texto YAML por linha (cirurgico). Devolve (novo_texto, casos_anotados)."""
    com = dict(mapa.get("com_codigo") or {})
    sem = dict(mapa.get("sem_codigo") or {})
    ja = dict(mapa.get("ja_com_codigo") or {})
    linhas = texto.splitlines()
    corte = next((i for i, l in enumerate(linhas) if l.startswith("nota_da_versao:")), None)
    if corte is None:
        raise Recusa("corpus sem `nota_da_versao` no fim: formato inesperado")
    corpo, saida, atual, anotados = linhas[:corte], [], None, []
    for linha in corpo:
        if linha.startswith("- id:"):
            atual = linha.split(":", 1)[1].strip()
        if linha.startswith("versao: " + VERSAO_DE_ENTRADA):
            saida.append("versao: " + VERSAO_DE_SAIDA)
            continue
        if atual is not None and linha.startswith("  acao_codigo:"):
            valor = linha.split(":", 1)[1].strip()
            if atual in com:
                codigo, _ = com[atual]
                if valor not in ("null", "~", ""):
                    raise Recusa(f"caso {atual} ja declara `acao_codigo: {valor}` — este card "
                                 "so preenche o que a v1.4 deixou nulo, nunca sobrescreve "
                                 "codigo declarado")
                saida.append(f"  acao_codigo: {codigo}")
                continue
            if atual in sem:
                if valor not in ("null", "~", ""):
                    raise Recusa(f"caso {atual} declarado `sem_codigo` mas ja tem "
                                 f"`acao_codigo: {valor}` — mapa e corpus divergem")
                saida.append(linha)
                continue
        if atual is not None and linha.startswith("  nota:"):
            if atual in com:
                if TRECHO_ANTERIOR not in linha:
                    raise Recusa(f"caso {atual}: `nota` sem o trecho do card anterior "
                                 f"({TRECHO_ANTERIOR!r}) — o texto do corpus mudou de forma; "
                                 "anotacao cirurgica recusada em vez de reescrita as cegas")
                if CAUDA_ANTERIOR not in linha:
                    raise Recusa(f"caso {atual}: `nota` sem a cauda do card anterior "
                                 f"({CAUDA_ANTERIOR!r}) — ordem inesperada")
                prefixo = linha.split(TRECHO_ANTERIOR, 1)[0].rstrip()
                _, motivo = com[atual]
                saida.append(prefixo + " ; " + FORMATO_COM_CODIGO.format(
                    card=CARD, codigo=com[atual][0], motivo=motivo))
                anotados.append(atual)
                continue
            if atual in sem:
                if TRECHO_ANTERIOR not in linha:
                    raise Recusa(f"caso {atual}: `nota` sem o trecho do card anterior — "
                                 "nao sei onde anexar a reavaliacao")
                prefixo = linha.split(TRECHO_ANTERIOR, 1)[0].rstrip()
                saida.append(_anexa(prefixo, FORMATO_REAVALIADO.format(card=CARD,
                                                                      motivo=sem[atual])))
                anotados.append(atual)
                continue
            if atual in ja:
                saida.append(linha)
                continue
            raise Recusa(f"caso {atual} sem anotacao declarada no mapa")
        saida.append(linha)
    novo = "\n".join(saida + [_yaml_escalar("nota_da_versao", NOTA_DA_VERSAO, yaml)]) + "\n"
    return novo, anotados


def prova(casos_antes: list, casos_depois: list, mapa: dict, comuns, proibidos, trocados: list,
          derivados: list) -> list:
    """Comparacao profunda antes/depois: nada de campo protegido mudou, e tudo foi anotado."""
    falhas = []
    if len(casos_antes) != len(casos_depois):
        falhas.append("numero de casos mudou")
    if len(casos_depois) != 32:
        falhas.append(f"esperava 32 casos, achei {len(casos_depois)}")
    esperados = len(mapa.get("com_codigo") or {}) + len(mapa.get("sem_codigo") or {})
    if len(trocados) != esperados:
        falhas.append(f"esperava {esperados} anotacoes, fiz {len(trocados)}")
    antes_por_id = {str(c["id"]): c for c in casos_antes}
    for depois in casos_depois:
        caso = str(depois["id"])
        antes = antes_por_id.get(caso)
        if antes is None:
            falhas.append(f"caso novo no corpus: {caso}")
            continue
        for campo in CAMPOS_PROTEGIDOS:
            if antes.get(campo) != depois.get(campo):
                falhas.append(f"{caso}: campo protegido {campo!r} mudou")
        if not str(depois.get("lane_esperada") or "").strip():
            falhas.append(f"{caso}: ficou sem lane_esperada")
        nota = str(depois.get("nota") or "")
        declarado = depois.get("acao_codigo", "AUSENTE")
        if declarado == "AUSENTE":
            falhas.append(f"{caso}: sem o campo acao_codigo")
            continue
        if caso in (mapa.get("com_codigo") or {}):
            if declarado != CODIGO:
                falhas.append(f"{caso}: acao_codigo {declarado!r} != {CODIGO!r}")
            if declarado not in set(comuns) | set(proibidos):
                falhas.append(f"{caso}: acao_codigo {declarado!r} fora do catalogo")
            if antes.get("acao_codigo") not in (None, ""):
                falhas.append(f"{caso}: ganhou codigo mas ja tinha {antes.get('acao_codigo')!r}")
            if "nenhum no catalogo vigente" in nota:
                falhas.append(f"{caso}: nota ainda diz 'nenhum no catalogo vigente' — "
                              "proveniencia do card anterior nao foi substituida")
            if CODIGO not in nota or CARD not in nota:
                falhas.append(f"{caso}: nota sem a proveniencia deste card ({CARD}/{CODIGO})")
        elif caso in (mapa.get("sem_codigo") or {}):
            if declarado is not None:
                falhas.append(f"{caso}: devia CONTINUAR sem codigo, achei {declarado!r}")
            if CARD not in nota:
                falhas.append(f"{caso}: nota sem a marca de reavaliacao deste card")
        elif caso in (mapa.get("ja_com_codigo") or {}):
            if declarado != antes.get("acao_codigo"):
                falhas.append(f"{caso}: codigo JA declarado mudou "
                              f"({antes.get('acao_codigo')!r} -> {declarado!r}) — este card nao "
                              "sobrescreve codigo declarado")
            if nota != str(antes.get("nota") or ""):
                falhas.append(f"{caso}: `nota` mudou em caso de codigo ja declarado")
        else:
            falhas.append(f"{caso}: caso nao declarado no mapa")
    rotulo_do_codigo = sorted(str(c["id"]) for c in casos_depois if str(c.get("acao")) == CODIGO)
    ja_do_rotulo = sorted(k for k in (mapa.get("ja_com_codigo") or {})
                          if k in rotulo_do_codigo)
    if sorted(set(derivados) | set(ja_do_rotulo)) != rotulo_do_codigo:
        falhas.append("casos com o rotulo do codigo nomeado nao batem com a derivacao do corpus "
                      f"(`com_codigo`) + os preservados: rotulo={rotulo_do_codigo} "
                      f"derivados={derivados} preservados={ja_do_rotulo}")
    return falhas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Anota o codigo comum `execucao_de_card` no corpus de benchmark.")
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--mapa", default=None,
                        help="JSON com o mapa da anotacao (default: o mapa declarado NESTE "
                             "arquivo). Existe para as provas negativas: mapa com codigo "
                             "inventado tem de ser RECUSADO sem tocar o corpus")
    parser.add_argument("--ensaio", action="store_true",
                        help="monta e prova a anotacao sem escrever no arquivo")
    args = parser.parse_args(argv)

    import yaml

    caminho = pathlib.Path(args.corpus)
    if not caminho.is_file():
        print(f"FALHOU: corpus nao encontrado em {caminho}")
        return 1
    texto_antes = caminho.read_text(encoding="utf-8")
    sha_antes = hashlib.sha256(texto_antes.encode("utf-8")).hexdigest()
    bruto_antes = yaml.safe_load(texto_antes)
    casos_antes = bruto_antes.get("casos") or []

    roteador = carrega_modulo(ROTEADOR, "rot_anotador_t10")
    politica = roteador.carregar_politica(POLITICA_EM_VIGOR)
    comuns, proibidos = catalogo(roteador, politica)

    # --- gates antes de qualquer escrita -----------------------------------
    problemas = confere_espelho_do_encaixe(comuns, yaml, ACOES_DECLARADAS)
    if problemas:
        print("RECUSADO (gate de catalogo): " + "; ".join(problemas))
        return 3

    if bruto_antes.get("versao") != VERSAO_DE_ENTRADA:
        print(f"NADA A FAZER: o corpus esta em {bruto_antes.get('versao')!r}; este card anota "
              f"{VERSAO_DE_ENTRADA!r} -> {VERSAO_DE_SAIDA!r}. Arquivo NAO foi tocado.")
        print(f"sha256={sha_antes}")
        return 1

    derivados = deriva_do_corpus(casos_antes)
    mapa = MAPA if not args.mapa else json.loads(pathlib.Path(args.mapa).read_text(encoding="utf-8"))
    falhas = valida_mapa(mapa, casos_antes, comuns, proibidos, derivados)
    if falhas:
        print("RECUSADO (mapa da anotacao): nada foi escrito.")
        for f in falhas:
            print(f"  - {f}")
        return 1

    print(f"corpus  : {caminho}  versao={bruto_antes.get('versao')}  casos={len(casos_antes)}")
    print(f"catalogo: comuns={list(comuns)}")
    print(f"          proibidos={list(proibidos)}")
    print(f"derivado: {len(derivados)} caso(s) com rotulo `acao` == {CODIGO!r} e sem codigo "
          f"(criterio (a) do T03-D01)")
    print(f"mapa    : {len(mapa['com_codigo'])} caso(s) ganham o codigo + "
          f"{len(mapa['sem_codigo'])} caso(s) seguem sem codigo + "
          f"{len(mapa.get('ja_com_codigo') or {})} caso(s) preservam o codigo ja declarado")

    try:
        texto_depois, anotados = anota_texto(texto_antes, mapa, yaml)
    except Recusa as erro:
        print(f"RECUSADO (anotacao): {erro}. Nada foi escrito.")
        return 1

    validacao, casos_validados, problemas = None, [], []
    try:
        validacao = yaml.safe_load(texto_depois)
        casos_validados = (validacao or {}).get("casos") or []
        problemas = []
        if len(casos_validados) != len(casos_antes):
            problemas.append(f"casos mudou: {len(casos_antes)} -> {len(casos_validados)}")
        sem_campo = [str(c.get("id")) for c in casos_validados if "acao_codigo" not in c]
        if sem_campo:
            problemas.append(f"caso sem `acao_codigo` no texto montado: {sem_campo}")
        if (validacao or {}).get("versao") != VERSAO_DE_SAIDA:
            problemas.append(f"versao nao subiu para {VERSAO_DE_SAIDA}: "
                             f"{(validacao or {}).get('versao')!r}")
    except Exception as erro:
        problemas = [f"texto montado NAO parseia como YAML: {erro}"]
    if problemas:
        print("RECUSADO (texto montado): nada foi escrito.")
        for p in problemas:
            print(f"  - {p}")
        return 1

    if args.ensaio:
        print(f"\nENSAIO: {len(anotados)} casos seriam anotados; texto montado parseia "
              f"({len(casos_validados)} casos, versao {(validacao or {}).get('versao')}); "
              f"arquivo NAO escrito (sha256 intacto={sha_antes[:12]})")
        return 0

    caminho.write_text(texto_depois, encoding="utf-8")

    # --- prova --------------------------------------------------------------
    # Se QUALQUER coisa falhar aqui, o corpus volta ao conteudo de antes: o arquivo
    # versionado nunca fica num estado que nao passou na prova.
    try:
        bruto_depois = yaml.safe_load(caminho.read_text(encoding="utf-8"))
        casos_depois = bruto_depois.get("casos") or []
        falhas = prova(casos_antes, casos_depois, mapa, comuns, proibidos, anotados, derivados)
    except Exception as erro:
        caminho.write_text(texto_antes, encoding="utf-8")
        print(f"FALHOU na prova ({type(erro).__name__}: {erro}) — corpus RESTAURADO "
              f"(sha256={sha_antes})")
        return 1
    if falhas:
        caminho.write_text(texto_antes, encoding="utf-8")
        print("FALHOU a prova da anotacao — corpus RESTAURADO " f"(sha256={sha_antes})")
        for f in falhas:
            print(f"  - {f}")
        return 1

    com = {str(c["id"]): c.get("acao_codigo") for c in casos_depois}
    depois_por_id = {str(d["id"]): d for d in casos_depois}
    lane_mudou = [str(c["id"]) for c in casos_antes
                  if depois_por_id.get(str(c["id"]), {}).get("lane_esperada") != c.get("lane_esperada")]
    relatorio = {
        "card": CARD,
        "corpus": str(caminho.relative_to(RAIZ)) if str(caminho).startswith(str(RAIZ)) else str(caminho),
        "versao_antes": bruto_antes.get("versao"),
        "versao_depois": bruto_depois.get("versao"),
        "sha256_corpus_antes": sha_antes,
        "sha256_corpus_depois": hashlib.sha256(caminho.read_bytes()).hexdigest(),
        "sha256_politica_em_vigor": sha256_de(POLITICA_EM_VIGOR),
        "sha256_roteador": sha256_de(ROTEADOR),
        "casos": len(casos_depois),
        "anotados": len(anotados),
        "com_o_codigo_nomeado": sum(1 for v in com.values() if v == CODIGO),
        "com_codigo": sum(1 for v in com.values() if v),
        "sem_codigo": sum(1 for v in com.values() if not v),
        "seguem_sem_codigo": sorted(k for k, v in com.items() if not v),
        "codigos_usados": sorted({v for v in com.values() if v}),
        "lane_esperada_alterada_em": lane_mudou,
        "falhas": falhas,
    }
    print()
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    print(f"RESULTADO: OK — {len(anotados)}/{len(casos_depois)} casos re-avaliados, "
          f"{relatorio['com_o_codigo_nomeado']} declaram {CODIGO!r}, "
          f"{relatorio['sem_codigo']} seguem sem codigo no catalogo, "
          "0 campo protegido alterado, lane_esperada intacta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
