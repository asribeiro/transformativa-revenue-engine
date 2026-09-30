#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Anota o CODIGO CANONICO de acao nos casos do corpus de benchmark — card TRE-W0-E04-T03-D01.

PROBLEMA QUE ESTE ARQUIVO RESOLVE (medido, nao suposto)
  O benchmark de roteamento (`scripts/benchmark_roteamento.py`) roda o roteador real
  sobre `hermes/jev/benchmarks/corpus-anotacao.yaml`. Na v1.3 do corpus, `acao_codigo`
  estava preenchido em 0 dos 32 casos: com a postura estrita homologada (texto livre sem
  codigo canonico NAO executa, D07), 28 dos 32 casos nunca chegavam ao caminho de
  execucao e a accuracy medida era abstencao, nao acerto. Este script declara o codigo
  canonico de acao de cada caso, caso a caso, e a medicao passa a separar as populacoes.

CRITERIO DE ATRIBUICAO (declarado, para a anotacao nao virar fabricacao de resultado)
  O codigo so e atribuido quando a PROPRIA DECLARACAO do caso o identifica:

    (a) o rotulo `acao` do caso E o codigo do catalogo, ou e o nome direto da acao
        daquele codigo (casos sinteticos de borda, cujo rotulo ja e o nome da acao); ou
    (b) os SINAIS do caso, somados a regra DECLARADA pela politica
        (`regra_de_lane_por_ambiente`, que nomeia `real-t_969affa7` pelo id), identificam
        a operacao de DDL/migration -> codigo comum `migracao_de_esquema`.

  NUNCA pelo texto da `justificativa`: a justificativa explica a LANE, nao a acao.
  Inferir o codigo da prosa seria inventar o dado que se quer medir — a mesma proibicao
  que o benchmark declara para o `acao` ("o benchmark NAO inventa codigo para card que
  nao tem"). Caso em que nenhuma das duas vias identifica um codigo do catalogo recebe
  `acao_codigo: null` (explicito, para nao se confundir com "ainda nao anotado") e a
  proveniencia registrada no campo `nota` diz que a acao NAO tem codigo no catalogo
  vigente — caso NAO EXECUTAVEL por lacuna de catalogo, falha fechada do D07.

O QUE O SCRIPT **NAO** FAZ
  * nao altera `lane_esperada` homologada (30 em bloco + 2 individuais, 29/09/2026) —
    provado por comparacao profunda antes/depois, campo a campo; divergencia de lane e
    ACHADO para o Anderson, nunca correcao de rotulo por aqui;
  * nao altera `lane_proposta_hermes`, `acao`, `titulo`, `sinais`, `justificativa`,
    `origem` nem `homologacao`;
  * nao inventa codigo: todo codigo declarado tem de constar do catalogo vigente lido
    EM TEMPO DE EXECUCAO (CODIGOS_DE_ACAO_COMUNS do roteador + as acoes de
    `nunca_decidido_por_maquina` da politica em vigor). Codigo fora do catalogo =
    FALHA ANTES DE ESCREVER (exit 1), arquivo intocado;
  * nao escreve se ja anotou (guarda de re-execucao): rodar de novo e no-op que falha
    alto, como em `scripts/anotar_corpus_benchmark.py`.

PROVENIENCIA
  A proveniencia de cada caso e ANEXADA ao campo `nota` existente (nunca substitui a
  proveniencia da lane homologada, que continua la). O texto do codigo diz, caso a caso,
  qual foi o caminho de atribuicao e o que sustenta o codigo.

USO
    /opt/hermes/.venv/bin/python scripts/anotar_codigos_de_acao_no_corpus.py
    /opt/hermes/.venv/bin/python scripts/anotar_codigos_de_acao_no_corpus.py --ensaio
    # provas negativas (escrevem em COPIA, nunca no corpus versionado):
    ... --corpus /tmp/copia.yaml --mapa /tmp/mapa-com-codigo-inventado.json

Codigos de saida: 0 = anotado (ou ensaio OK); 1 = FALHOU (nada foi escrito) ou nada a
fazer; 3 = RECUSA de gate (catalogo divergente do espelho do encaixe).
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
POLITICA = RAIZ / "hermes/jev/policy_v1_1.yaml"
ACOES_DECLARADAS = RAIZ / "hermes/jev/acoes-declaradas.yaml"
VERSAO_DE_ENTRADA = "corpus-anotacao-v1.3"
VERSAO_DE_SAIDA = "corpus-anotacao-v1.4"

# Campos que a anotacao NAO pode tocar (a prova compara antes/depois um a um).
CAMPOS_PROTEGIDOS = ("id", "origem", "titulo", "acao", "sinais", "lane_proposta_hermes",
                     "lane_esperada", "justificativa", "homologacao")

NOTA_DA_VERSAO = (
    "v1.4 (30/09/2026, card TRE-W0-E04-T03-D01): campo `acao_codigo` anotado NOS 32 CASOS "
    "a partir do catalogo canonico vigente (os 4 codigos comuns do roteador + as 8 acoes de "
    "`nunca_decidido_por_maquina` da jev-policy-v1.1), com proveniencia caso a caso no campo "
    "nota. 11 casos declaram codigo (5 comuns, 6 proibidos); 21 casos declaram `acao_codigo: "
    "null` explicito, porque a acao NAO TEM codigo no catalogo vigente (18 deles sao "
    "`execucao_de_card`, execucao generica de card de desenvolvimento, para a qual nao existe "
    "codigo comum — nomear um codigo novo e decisao do dono, nao deste card). Criterio de "
    "atribuicao declarado no cabecalho de scripts/anotar_codigos_de_acao_no_corpus.py: o codigo "
    "so vem da propria declaracao do caso (o rotulo `acao` E o codigo, ou os sinais do caso + a "
    "regra declarada da politica identificam a operacao), NUNCA da prosa da justificativa. "
    "Nenhum `lane_esperada` foi alterado (30 em bloco + 2 individuais de 29/09/2026 preservados, "
    "provado por comparacao profunda campo a campo) e nenhum campo protegido mudou. Efeito na "
    "medicao (rodada de 30/09/2026, corpus v1.4 x v1.3): `codigo_de_acao` no recibo passa de 4/32 "
    "para 11/32, `pode_executar` de 2/32 para 4/32, escalacao cai e a medicao passa a separar "
    "EXECUTAVEL (com codigo comum) de NAO EXECUTAVEL (acao proibida ou sem codigo) — a lane de "
    "um caso sem codigo e a lane conservadora de AUDITORIA, nao uma decisao de roteamento."
)

# ---------------------------------------------------------------------------
# MAPA DA ANOTACAO (o contrato deste card, caso a caso)
#
# `com_codigo`: caso -> (codigo, motivo/proveniencia). O codigo e conferido contra o
#   catalogo lido em tempo de execucao; nome que nao existir no catalogo = FALHA.
# `sem_codigo`: caso -> motivo pelo qual a acao NAO tem codigo no catalogo vigente.
# Todo caso do corpus tem de estar em UM dos dois (e em um so): caso nao declarado =
#   FALHA (nada de default silencioso).
# ---------------------------------------------------------------------------
MAPA = {
    "com_codigo": {
        "borda-01": (
            "ajuste_de_texto",
            "o rotulo `acao` do proprio caso ja e o codigo comum do catalogo (ajuste de "
            "texto no README; lane small homologada)"),
        "borda-11": (
            "ajuste_de_texto",
            "o rotulo `acao` do proprio caso ja e o codigo comum do catalogo (revisao de "
            "texto de runbook; lane small homologada)"),
        "borda-02": (
            "migracao_de_esquema",
            "a acao do caso e migration e o codigo comum que cobre DDL/migration e "
            "migracao_de_esquema; o guardrail de DDL segue decidindo o ambiente (aqui "
            "producao, bloqueia) e o piso por ambiente da v1.1 mantem a lane critical "
            "homologada"),
        "real-t_969affa7": (
            "migracao_de_esquema",
            "a acao do card e criar database/schema (sinais ddl_ou_migration true e "
            "producao false); a propria jev-policy-v1.1 nomeia este caso, pelo id, como "
            "exemplo do ramo novo_ou_dev da `regra_de_lane_por_ambiente`"),
        "real-t_d9cb5755": (
            "migracao_de_esquema",
            "a acao do card e criar as tabelas core (sinais ddl_ou_migration true e "
            "producao false; DDL em dev homologado como high em 29/09); mesmo ramo "
            "novo_ou_dev da `regra_de_lane_por_ambiente` da v1.1"),
        "borda-03": (
            "primeiro_contato_outbound",
            "o rotulo `acao` do proprio caso ja e o codigo proibido de "
            "`nunca_decidido_por_maquina` (primeiro contato outbound — nenhuma maquina decide)"),
        "borda-04": (
            "rotacao_ou_revogacao_de_credencial",
            "rotacionar a chave de API do provedor e rotacao de credencial, acao da lista "
            "`nunca_decidido_por_maquina`"),
        "borda-05": (
            "publicacao_em_nome_da_transformativa",
            "publicar post institucional em nome da Transformativa e a acao proibida "
            "`publicacao_em_nome_da_transformativa` da lista `nunca_decidido_por_maquina`"),
        "borda-08": (
            "exclusao_de_dado_de_cliente",
            "apagar registros duplicados de um cliente em producao e a acao proibida "
            "`exclusao_de_dado_de_cliente` da lista `nunca_decidido_por_maquina`"),
        "borda-09": (
            "rollback_em_producao",
            "rollback da versao em producao ja resolvia pela ponte de prosa "
            "(`texto_canonicalizado`) e passa a ter o codigo canonico declarado; acao "
            "proibida da lista `nunca_decidido_por_maquina`"),
        "borda-12": (
            "mudanca_estrutural_de_arquitetura",
            "decidir a arquitetura do novo modulo de comissao e a acao proibida "
            "`mudanca_estrutural_de_arquitetura` da lista `nunca_decidido_por_maquina`"),
    },
    "sem_codigo": {
        "real-t_ac8a2130": "criar a estrutura do repositorio e trabalho de engenharia "
                           "comum, nao ajuste de texto, consulta, operacao comercial nem "
                           "migracao",
        "real-t_4be20bcc": "definir a gestao de secrets e desenho de seguranca (nao opera "
                           "credencial); nao ha codigo comum que cubra desenho",
        "real-t_d6dc5a4c": "instalar e configurar o Odoo Community e operacao multi-sistema, "
                           "nao coberta por codigo comum",
        "real-t_1acf11f2": "configurar TLS, reverse proxy e security e operacao de "
                           "infraestrutura, nao coberta por codigo comum",
        "real-t_e0489efc": "criar a API controlada do Odoo e integracao entre servicos, nao "
                           "coberta por codigo comum",
        "real-t_cdc21b43": "implementar o company upsert e funcao nova com teste, nao "
                           "coberta por codigo comum",
        "real-t_fd3e41f0": "implementar o Scout Agent e funcao nova com teste, nao coberta "
                           "por codigo comum",
        "real-t_d9be7d3c": "implementar o Research Agent e funcao nova com teste, nao "
                           "coberta por codigo comum",
        "real-t_e4a90eba": "ICP Score V1 e regra de negocio nova, nao coberta por codigo "
                           "comum",
        "real-t_11815e63": "Automation Fit Score V1 e regra de negocio nova, nao coberta "
                           "por codigo comum",
        "real-t_6267d886": "configurar o SMTP do Titan e configuracao de canal externo, nao "
                           "coberta por codigo comum",
        "real-t_9d38e360": "configurar o IMAP do Titan e configuracao de canal externo, nao "
                           "coberta por codigo comum",
        "real-t_eb323dd7": "captura de lead no website e integracao de canal externo, nao "
                           "coberta por codigo comum",
        "real-t_33bc1765": "ingestao de lead do Meta e integracao de canal externo, nao "
                           "coberta por codigo comum",
        "real-t_6cc75a1d": "funnel dashboard e visualizacao interna, nao coberta por codigo "
                           "comum",
        "real-t_0248a568": "conversao por segmento e entrega de engenharia (consulta "
                           "agregada) — o codigo `consulta_interna` nao e atribuido porque o "
                           "rotulo `acao` do caso e `execucao_de_card` e nada na declaracao "
                           "do caso aponta a acao de consulta interna; atribuir seria inferir "
                           "da prosa da justificativa",
        "real-t_f599bd02": "calibrar o score e trabalho de engenharia com teste, nao "
                           "coberto por codigo comum",
        "real-t_2821c15b": "predictive scoring e modelo novo com dado de cliente, nao "
                           "coberto por codigo comum",
        "borda-06": "conceder credencial de deploy ao papel Sales AI nao e rotacao nem "
                    "revogacao de credencial (o catalogo proibido cobre rotacao/revogacao); "
                    "o bloqueio de hoje vem do guardrail de papel, nao do codigo de acao",
        "borda-07": "ajustar rotulo de coluna no dashboard interno e ajuste de interface, "
                    "nao ajuste de texto, consulta, operacao comercial nem migracao",
        "borda-10": "explicar ao cliente o atraso do relatorio e outbound de resposta a "
                    "cliente existente; o catalogo proibido cobre primeiro contato e "
                    "proposta comercial, nao resposta a cliente",
    },
}

FORMATO_COM_CODIGO = "codigo de acao (TRE-W0-E04-T03-D01) {codigo} — {motivo}"
FORMATO_SEM_CODIGO = ("codigo de acao (TRE-W0-E04-T03-D01) nenhum no catalogo vigente — "
                      "{motivo}; caso NAO EXECUTAVEL por lacuna de catalogo (falha fechada D07)")


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
    """O espelho `codigos_validos` do acoes-declaradas.yaml tem de bater com o roteador.

    Mesma regra que a suite do encaixe ja checa (S10 item 28). Divergencia = o catalogo
    canonico esta sendo lido de duas fontes que discordam: recusa (exit 3).
    """
    dado = yaml.safe_load(acoes_declaradas.read_text(encoding="utf-8"))
    espelho = tuple(str(c) for c in (dado or {}).get("codigos_validos") or [])
    if set(espelho) != set(comuns):
        return [f"espelho `codigos_validos` do {acoes_declaradas.name} = {list(espelho)} "
                f"diverge de CODIGOS_DE_ACAO_COMUNS do roteador = {list(comuns)}"]
    return []


def valida_mapa(mapa: dict, casos: list, comuns, proibidos) -> list:
    """Falhas do mapa ANTES de escrever qualquer coisa: caso faltando/sobrando, codigo inventado."""
    falhas = []
    ids = [str(c["id"]) for c in casos]
    com = dict(mapa.get("com_codigo") or {})
    sem = dict(mapa.get("sem_codigo") or {})
    if len(set(ids)) != len(ids):
        falhas.append("corpus com id repetido")
    repetidos = sorted(set(com) & set(sem))
    if repetidos:
        falhas.append(f"caso declarado em `com_codigo` E em `sem_codigo`: {repetidos}")
    nao_declarados = sorted(set(ids) - set(com) - set(sem))
    if nao_declarados:
        falhas.append(f"caso SEM anotacao declarada: {nao_declarados}")
    inexistentes = sorted((set(com) | set(sem)) - set(ids))
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
    for caso, motivo in sorted(sem.items()):
        if not str(motivo).strip():
            falhas.append(f"{caso}: `sem_codigo` sem motivo declarado")
    return falhas


def _yaml_escalar(chave: str, texto: str, yaml) -> str:
    return yaml.safe_dump({chave: texto}, allow_unicode=True, default_flow_style=False,
                          width=10 ** 6).strip()


def anota_texto(texto: str, mapa: dict, yaml) -> tuple:
    """Anota o texto YAML por linha (cirurgico). Devolve (novo_texto, linhas_anotadas).

    A proveniencia e montada na linha do `acao` (onde o codigo e decidido) e consumida na
    linha do `nota`. Se o `nota` vier ANTES do `acao` no caso, a anotacao RECUSA em vez de
    adivinhar — ordem inesperada e duvida, e duvida nao vira anotacao.
    """
    com = dict(mapa.get("com_codigo") or {})
    sem = dict(mapa.get("sem_codigo") or {})
    linhas = texto.splitlines()
    corte = next((i for i, l in enumerate(linhas) if l.startswith("nota_da_versao:")), None)
    if corte is None:
        raise Recusa("corpus sem `nota_da_versao` no fim: formato inesperado")
    corpo, saida, atual, anotados = linhas[:corte], [], None, []
    notas = {}
    for linha in corpo:
        if linha.startswith("- id:"):
            atual = linha.split(":", 1)[1].strip()
        saida.append(linha)
        if linha.startswith("versao: " + VERSAO_DE_ENTRADA):
            saida[-1] = "versao: " + VERSAO_DE_SAIDA
            continue
        if atual is not None and linha.startswith("  acao_codigo:"):
            raise Recusa(f"caso {atual} ja declara `acao_codigo` — anotacao duplicada "
                         "recusada (o corpus nao duplica o campo)")
        if atual is not None and linha.startswith("  acao:"):
            if atual in com:
                codigo, motivo = com[atual]
                saida.append(f"  acao_codigo: {codigo}")
                notas[atual] = FORMATO_COM_CODIGO.format(codigo=codigo, motivo=motivo)
            elif atual in sem:
                saida.append("  acao_codigo: null")
                notas[atual] = FORMATO_SEM_CODIGO.format(motivo=sem[atual])
            else:
                raise Recusa(f"caso {atual} sem anotacao declarada")
            anotados.append(atual)
            continue
        if atual is not None and linha.startswith("  nota:"):
            if atual not in notas:
                raise Recusa(f"caso {atual}: `nota` apareceu antes de `acao` — ordem "
                             "inesperada no corpus")
            atual_nota = linha.split(":", 1)[1].strip()
            if atual_nota in ("''", '""', ""):
                saida[-1] = _yaml_escalar("nota", notas[atual], yaml).replace("nota:", "  nota:", 1)
            else:
                saida[-1] = _yaml_escalar_append(linha, notas[atual])
            continue
    novo = "\n".join(saida + [_yaml_escalar("nota_da_versao", NOTA_DA_VERSAO, yaml)]) + "\n"
    return novo, anotados


def _yaml_escalar_append(linha: str, nova: str) -> str:
    """Anexa a proveniencia ao `nota` existente sem trocar o estilo do escalar.

    O texto novo e redigido sem `: ` e sem caractere especial de inicio, entao continua
    valido como escalar simples — e a linha antiga (proveniencia da lane homologada)
    permanece legivel inteira, em vez de virar aspas.
    """
    if re.search(r"[#]", nova) or ": " in nova or nova[:1] in "-?*&!|>%@`":
        raise Recusa(f"proveniencia com caractere que exigiria quoting: {nova!r}")
    return f"{linha.rstrip()} ; {nova}"


def prova(casos_antes: list, casos_depois: list, mapa: dict, comuns, proibidos, trocados: list) -> list:
    """Comparacao profunda antes/depois: nada de campo protegido mudou, e tudo foi anotado."""
    falhas = []
    if len(casos_antes) != len(casos_depois):
        falhas.append("numero de casos mudou")
    if len(casos_depois) != 32:
        falhas.append(f"esperava 32 casos, achei {len(casos_depois)}")
    esperados = len(mapa.get("com_codigo") or {}) + len(mapa.get("sem_codigo") or {})
    if len(trocados) != esperados:
        falhas.append(f"esperava {esperados} anotacoes, fiz {len(trocados)}")
    for antes, depois in zip(casos_antes, casos_depois):
        if antes["id"] != depois["id"]:
            falhas.append(f"ordem dos casos mudou: {antes['id']} -> {depois['id']}")
            continue
        caso = antes["id"]
        for campo in CAMPOS_PROTEGIDOS:
            if antes.get(campo) != depois.get(campo):
                falhas.append(f"{caso}: campo protegido {campo!r} mudou")
        if not str(depois.get("lane_esperada") or "").strip():
            falhas.append(f"{caso}: ficou sem lane_esperada")
        declarado = depois.get("acao_codigo", "AUSENTE")
        if declarado == "AUSENTE":
            falhas.append(f"{caso}: sem o campo acao_codigo depois da anotacao")
            continue
        if declarado is None:
            if caso not in (mapa.get("sem_codigo") or {}):
                falhas.append(f"{caso}: acao_codigo null mas o caso nao esta em `sem_codigo`")
            if "nenhum no catalogo vigente" not in str(depois.get("nota") or ""):
                falhas.append(f"{caso}: nota sem a proveniencia de 'nenhum codigo no catalogo'")
        else:
            if declarado not in set(comuns) | set(proibidos):
                falhas.append(f"{caso}: acao_codigo {declarado!r} fora do catalogo")
            if declarado not in str(depois.get("nota") or ""):
                falhas.append(f"{caso}: nota sem o codigo declarado")
        if "TRE-W0-E04-T03-D01" not in str(depois.get("nota") or ""):
            falhas.append(f"{caso}: nota sem a proveniencia do card")
    return falhas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Anota o codigo canonico de acao no corpus de benchmark.")
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--mapa", default=None,
                        help=("JSON com o mapa da anotacao (default: o mapa declarado NESTE arquivo). "
                              "Existe para as provas negativas: mapa com codigo inventado tem de ser "
                              "RECUSADO sem tocar o corpus"))
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

    roteador = carrega_modulo(ROTEADOR, "rot_anotador")
    politica = roteador.carregar_politica(POLITICA)
    comuns, proibidos = catalogo(roteador, politica)

    # --- gates antes de qualquer escrita -----------------------------------
    problemas = confere_espelho_do_encaixe(comuns, yaml, ACOES_DECLARADAS)
    if problemas:
        print("RECUSADO (gate de catalogo): " + "; ".join(problemas))
        return 3

    # guarda de re-execucao: so anota corpus que ainda NAO tem o campo (vale tambem com
    # --mapa: idempotencia e propriedade do ARQUIVO, nao do mapa que chegou)
    ja_anotados = [str(c["id"]) for c in casos_antes if "acao_codigo" in c]
    if len(ja_anotados) == len(casos_antes):
        print(f"NADA A FAZER: os {len(casos_antes)} casos ja declaram `acao_codigo` "
              f"(versao={bruto_antes.get('versao')}). Arquivo NAO foi tocado.")
        print(f"sha256={sha_antes}")
        return 1

    mapa = MAPA if not args.mapa else json.loads(pathlib.Path(args.mapa).read_text(encoding="utf-8"))
    falhas = valida_mapa(mapa, casos_antes, comuns, proibidos)
    if falhas:
        print("RECUSADO (mapa da anotacao): nada foi escrito.")
        for f in falhas:
            print(f"  - {f}")
        return 1

    print(f"corpus  : {caminho}  versao={bruto_antes.get('versao')}  casos={len(casos_antes)}")
    print(f"catalogo: comuns={list(comuns)}")
    print(f"          proibidos={list(proibidos)}")
    print(f"mapa    : {len(mapa['com_codigo'])} caso(s) com codigo + "
          f"{len(mapa['sem_codigo'])} caso(s) sem codigo no catalogo = "
          f"{len(mapa['com_codigo']) + len(mapa['sem_codigo'])} casos")

    try:
        texto_depois, anotados = anota_texto(texto_antes, mapa, yaml)
    except Recusa as erro:
        print(f"RECUSADO (anotacao): {erro}. Nada foi escrito.")
        return 1

    # O texto novo e validado ANTES de escrever: corpus que nao parseia (ou que perdeu
    # caso/campo) nao chega ao arquivo. Escrita que estraga o corpus e pior que recusa.
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
        falhas = prova(casos_antes, casos_depois, mapa, comuns, proibidos, anotados)
    except Exception as erro:
        caminho.write_text(texto_antes, encoding="utf-8")
        print(f"FALHOU na prova ({type(erro).__name__}: {erro}) — corpus RESTAURADO "
              f"(sha256={sha_antes})")
        return 1
    if falhas:
        caminho.write_text(texto_antes, encoding="utf-8")
        print("FALHOU a prova da anotacao — corpus RESTAURADO "
              f"(sha256={sha_antes})")
        for f in falhas:
            print(f"  - {f}")
        return 1
    com = {str(c["id"]): c.get("acao_codigo") for c in casos_depois}
    depois_por_id = {str(d["id"]): d for d in casos_depois}
    lane_mudou = [str(c["id"]) for c in casos_antes
                  if depois_por_id.get(str(c["id"]), {}).get("lane_esperada") != c.get("lane_esperada")]
    relatorio = {
        "corpus": str(caminho.relative_to(RAIZ)) if str(caminho).startswith(str(RAIZ)) else str(caminho),
        "versao_antes": bruto_antes.get("versao"),
        "versao_depois": bruto_depois.get("versao"),
        "sha256_antes": sha_antes,
        "sha256_depois": hashlib.sha256(caminho.read_bytes()).hexdigest(),
        "casos": len(casos_depois),
        "anotados": len(anotados),
        "com_codigo": sum(1 for v in com.values() if v),
        "sem_codigo": sum(1 for v in com.values() if not v),
        "codigos_usados": sorted({v for v in com.values() if v}),
        "comuns_usados": sorted({v for v in com.values() if v in set(comuns)}),
        "proibidos_usados": sorted({v for v in com.values() if v in set(proibidos)}),
        "lane_esperada_alterada_em": lane_mudou,
        "falhas": falhas,
    }
    print()
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    if falhas:
        print("RESULTADO: FALHOU")
        return 1
    print(f"RESULTADO: OK — {len(anotados)}/{len(casos_depois)} casos anotados, "
          "0 campo protegido alterado, lane_esperada intacta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
