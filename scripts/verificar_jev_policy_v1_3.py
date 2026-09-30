#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador da JEV Decision Policy v1.3 — RASCUNHO do card TRE-W0-E04-T10.

O que a v1.3 entrega (e o que esta suite prova): UMA entrada nova no mapa de lane por
codigo canonico — `execucao_de_card` (execucao generica de card de DESENVOLVIMENTO,
escopo estreito: dev, sem producao e sem credencial), nomeada pelo dono. Mais nada muda.

Mesmo rito das v1.0, v1.1 e v1.2: documento e arquivo legivel por maquina NAO podem
divergir, e cada item existe por um motivo declarado. Nenhuma parte substitui a anterior.

  PARTE 1 — a bateria da v1.0, inteira, sobre a v1.3 (versoes parametrizadas).
  PARTE 2 — a bateria da v1.1, inteira, RODADA SOBRE A v1.1: prova de que a versao
    anterior nao foi tocada e de que ela continua executavel pelo roteador no ar.
  PARTE 3 — a bateria da v1.2, inteira, RODADA SOBRE A v1.2: mesma prova para a versao
    EM VIGOR (ela e que decide hoje; se ela mudasse em silencio, o rascunho estaria
    mudando a regra que ja vale).
  PARTE 4 — o contrato desta versao, declarado:
    * identidade e estado HONESTO: rascunho nao homologado (e a guarda inversa: marcar-se
      `em-vigor` sem a linha de homologacao do Anderson no registro de aprovacoes REPROVA);
    * `lane_por_codigo_de_acao` com COBERTURA conferida contra `CODIGOS_DE_ACAO_COMUNS` do
      roteador, espelho medido no CODIGO nas duas direcoes;
    * o codigo novo (`execucao_de_card`) declarado, e so ele: o mapa da v1.3 = o mapa da
      v1.2 + EXATAMENTE esse codigo (nem um a mais, nem um a menos);
    * o mapa e o CONSERVADOR (todos na lane conservadora homologada);
    * o escopo estreito declarado em prosa (dev, sem producao, sem credencial);
    * o contrato da v1.1 sobrevive: lane conservadora, os dois ramos da regra por ambiente
      (lane minima E aprovacao humana), precedencia, fallback e metricas;
    * o portao de versao esta FECHADO: a versao em vigor continua sendo a v1.2, e o roteador
      carrega ESSE caminho POR PADRAO — medido contra o codigo, nao por leitura;
    * o executor declarado (`execucao.roteador_que_a_executa`) e o roteador REAL, e ele
      declara SUPORTE a v1.3 sem que ela esteja em vigor.
  PARTE 5 — provas de COMPORTAMENTO contra o roteador. Prosa em YAML nao prova nada:
    C1. `execucao_de_card` com texto limpo e ambiente dev EXECUTA na lane declarada;
    C2. o MESMO codigo com dominio sensivel declarado (producao ou credencial) NAO EXECUTA
        — o escopo estreito e provado, nao prometido;
    C3. o MAPA DECIDE: trocar a lane declarada no YAML troca a decisao;
    C4. codigo comum SEM lane declarada ABSTEM, registrando a lane conservadora;
    C5. o PISO POR AMBIENTE continua elevando sobre a lane declarada (o codigo novo nao
        e excecao: DDL declarado em ambiente vivo chega a `critical` com aprovacao humana);
    C6. o CLASSIFICADOR NAO E CHAMADO pelo caminho de decisao (espiao armado que levanta);
    C7. o RECIBO continua com os 13 campos e a origem da lane declarada HONESTA;
    C8. a falha fechada do D07 (acao sem codigo canonico) continua intacta;
    C9. a politica EM VIGOR (v1.2), com o codigo novo, ABSTEM — declarar SUPORTE a uma
        versao nao e entrar em vigor.

Uso:
    python3 scripts/verificar_jev_policy_v1_3.py                # verifica
    python3 scripts/verificar_jev_policy_v1_3.py --autoteste    # verifica + mutacoes

Saida: OK/FALHOU por item + RESULTADO final. Codigo de saida 0 so quando tudo passa.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

import yaml

RAIZ = pathlib.Path(__file__).resolve().parent.parent
YAML_V13 = RAIZ / "hermes/jev/policy_v1_3.yaml"
DOC_V13 = RAIZ / "docs/architecture/jev-decision-policy-v1.3.md"
YAML_V12 = RAIZ / "hermes/jev/policy_v1_2.yaml"
DOC_V12 = RAIZ / "docs/architecture/jev-decision-policy-v1.2.md"
YAML_V11 = RAIZ / "hermes/jev/policy_v1_1.yaml"
DOC_V11 = RAIZ / "docs/architecture/jev-decision-policy-v1.1.md"
YAML_V10 = RAIZ / "hermes/jev/policy_v1.yaml"
DOC_V10 = RAIZ / "docs/architecture/jev-decision-policy-v1.md"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
REGISTRO = RAIZ / "docs/operations/registro-de-aprovacoes.md"
VERIFICADOR_V12 = RAIZ / "scripts/verificar_jev_policy_v1_2.py"
VERIFICADOR_V11 = RAIZ / "scripts/verificar_jev_policy_v1_1.py"
VERIFICADOR_V10 = RAIZ / "scripts/verificar_jev_policy.py"
ACOES_DECLARADAS = RAIZ / "hermes/jev/acoes-declaradas.yaml"

VERSAO_V13 = "jev-policy-v1.3"
VERSAO_V12 = "jev-policy-v1.2"
VERSAO_V11 = "jev-policy-v1.1"
ROTEADOR_DA_V13 = "jev-router-v1.3"

# O codigo NOMEADO por esta versao. Um so — nomear outros por conveniencia nao foi
# autorizado pelo dono (decisao do card TRE-W0-E04-T10).
CODIGO_NOVO = "execucao_de_card"

# Ramo da regra por ambiente -> (lane minima, aprovacao humana registrada). Herdado da v1.1
# e NAO alterado por esta versao (a regra so ELEVA, e o codigo novo nao cria excecao).
RAMOS_ESPERADOS = (
    ("novo_ou_dev", "high", False),
    ("vivo_ou_producao", "critical", True),
)

# Chaves do contrato da lane declarada. `escopo` entra na v1.3: nomear um codigo com
# escopo estreito exige DECLARAR o escopo, senao "dev, sem producao e sem credencial"
# fica so no card.
CHAVES_DA_REGRA_DA_LANE = ("fonte", "cobertura", "sem_declaracao", "limiares", "direcao",
                           "escopo")

# O texto que o classificador historico lia como `critical`, e o que ele lia como `small`.
TEXTO_DO_CRITICAL = "deploy em produção com credencial de infraestrutura e decisão de arquitetura"
TEXTO_DO_SMALL = "ajuste de texto no rodapé, formatação e renomeação de campo"

# O dump de YAML perde comentarios, e o roteador EXIGE encontrar no texto da politica o
# vocabulario de resultado (declarado em comentario). As copias de prova recebem o mesmo
# vocabulario como cabecalho — sem isso o mutante nem carrega e a prova mediria outra coisa.
VOCABULARIO_DE_RESULTADO = "# vocabulario de resultado: PASS | RETRY | ESCALATE | BLOCK\n"


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------
def carregar_modulo(caminho: pathlib.Path, nome: str):
    especificacao = importlib.util.spec_from_file_location(nome, str(caminho))
    assert especificacao is not None and especificacao.loader is not None
    modulo = importlib.util.module_from_spec(especificacao)
    sys.modules[nome] = modulo
    especificacao.loader.exec_module(modulo)
    return modulo


class Itens:
    def __init__(self):
        self.lista: list[tuple[str, bool, str]] = []

    def add(self, nome, ok, detalhe=""):
        self.lista.append((str(nome), bool(ok), str(detalhe)))

    def checar(self, nome, funcao):
        try:
            ok, detalhe = funcao()
        except Exception as erro:  # noqa: BLE001 - a suite precisa registrar tudo
            ok, detalhe = False, f"excecao: {type(erro).__name__}: {erro}"
        self.add(nome, ok, detalhe)

    def falhas(self):
        return sum(1 for _, ok, _ in self.lista if not ok)


def texto_de_yaml(dado: dict) -> str:
    return yaml.safe_dump(dado, allow_unicode=True, sort_keys=False)


def normalizar(texto) -> str:
    """minusculo e sem acento — para comparar prosa escrita a mao em documento e YAML."""
    tabela = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                           "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")
    return str(texto).translate(tabela).lower()


def _texto(ok: bool, detalhe: str):
    return bool(ok), detalhe


def _politica_temporaria(raiz: pathlib.Path, dado: dict) -> pathlib.Path:
    destino = raiz / "policy_copia.yaml"
    destino.write_text(VOCABULARIO_DE_RESULTADO + texto_de_yaml(dado), encoding="utf-8")
    return destino


def _arvore_do_roteador_mutado(codigo: str) -> pathlib.Path:
    """Repo temporario com o roteador mutado, para exercitar o MESMO encanamento.

    O roteador resolve a raiz por `__file__` e carrega a politica padrao por
    `CAMINHO_POLITICA_PADRAO`; a arvore temporaria reproduz esses caminhos (politicas
    versionadas e pasta de papeis por symlink) para que a mutacao de codigo seja medida
    no roteador de verdade — sem tocar no arquivo versionado.
    """
    raiz = pathlib.Path(tempfile.mkdtemp(prefix="jev-v13-mut-"))
    (raiz / "hermes/jev/routing").mkdir(parents=True)
    (raiz / "hermes/jev/routing/router.py").write_text(codigo, encoding="utf-8")
    for nome in ("policy_v1.yaml", "policy_v1_1.yaml", "policy_v1_2.yaml", "policy_v1_3.yaml"):
        os.symlink(RAIZ / "hermes/jev" / nome, raiz / "hermes/jev" / nome)
    os.symlink(RAIZ / "hermes/policies", raiz / "hermes/policies")
    return raiz / "hermes/jev/routing/router.py"


# ---------------------------------------------------------------------------
# PARTE 1 — a bateria da v1.0 sobre a v1.3
# ---------------------------------------------------------------------------
def itens_da_bateria_da_v1_0(itens: Itens, yaml_txt: str, doc_txt: str, base) -> None:
    for nome, ok, detalhe in base.verificar(yaml_txt, doc_txt, VERSAO_V13):
        itens.add(f"v1.0-bateria: {nome}", ok, detalhe)


# ---------------------------------------------------------------------------
# PARTE 2 — a bateria da v1.1 sobre a v1.1 (a versao anterior nao foi tocada)
# ---------------------------------------------------------------------------
def itens_da_bateria_da_v1_1(itens: Itens, base, modulo) -> None:
    v11 = carregar_modulo(VERIFICADOR_V11, "verificador_v11_para_v13")
    yaml_v11 = YAML_V11.read_text(encoding="utf-8")
    doc_v11 = DOC_V11.read_text(encoding="utf-8")
    proprios = v11.verificar(yaml_v11, doc_v11, base, modulo)
    for nome, ok, detalhe in proprios.lista:
        itens.add(f"v1.1-bateria: {nome}", ok, detalhe)


# ---------------------------------------------------------------------------
# PARTE 3 — a bateria da v1.2 sobre a v1.2 (a versao EM VIGOR nao foi tocada)
# ---------------------------------------------------------------------------
def itens_da_bateria_da_v1_2(itens: Itens, base, modulo) -> None:
    v12 = carregar_modulo(VERIFICADOR_V12, "verificador_v12_para_v13")
    yaml_v12 = YAML_V12.read_text(encoding="utf-8")
    doc_v12 = DOC_V12.read_text(encoding="utf-8")
    proprios = v12.verificar(yaml_v12, doc_v12, base, modulo)
    for nome, ok, detalhe in proprios.lista:
        itens.add(f"v1.2-bateria: {nome}", ok, detalhe)


# ---------------------------------------------------------------------------
# PARTE 4 — o contrato novo
# ---------------------------------------------------------------------------
def itens_do_contrato(itens: Itens, dado: dict, doc_txt: str, modulo) -> None:
    doc = normalizar(doc_txt)
    lanes = list((dado.get("lanes") or {}).keys())
    limiares = dado.get("limiares") or {}
    mapa = dado.get("lane_por_codigo_de_acao")
    comuns = set(getattr(modulo, "CODIGOS_DE_ACAO_COMUNS", ()))

    # ---- 4.1 identidade da versao -----------------------------------------
    itens.add("v1.3: a versao se identifica como jev-policy-v1.3",
              dado.get("versao") == VERSAO_V13, f"versao={dado.get('versao')!r}")
    itens.add("v1.3: declara `substitui: jev-policy-v1.2`",
              dado.get("substitui") == VERSAO_V12, f"substitui={dado.get('substitui')!r}")
    itens.add("v1.3: a v1.2 continua existindo e se declarando v1.2 (nada foi sobrescrito)",
              YAML_V12.is_file()
              and (yaml.safe_load(YAML_V12.read_text(encoding="utf-8")) or {}).get("versao") == VERSAO_V12,
              f"versao do arquivo preservado={(yaml.safe_load(YAML_V12.read_text(encoding='utf-8')) or {}).get('versao')!r}")

    # ---- 4.2 estado: rascunho honesto, e a guarda da homologacao -----------
    def _estado_honesto():
        estado = dado.get("estado")
        if estado == "em-vigor":
            # GUARDA: so pode se declarar em vigor com a homologacao REGISTRADA.
            texto_registro = normalizar(REGISTRO.read_text(encoding="utf-8")) if REGISTRO.is_file() else ""
            numero = VERSAO_V13.rsplit("-", 1)[-1]
            linha = [l for l in texto_registro.splitlines()
                     if (VERSAO_V13 in normalizar(l) or numero in normalizar(l))
                     and "anderson" in normalizar(l)
                     and ("homolog" in normalizar(l) or "aprovad" in normalizar(l))]
            return _texto(bool(linha),
                          "estado=em-vigor exige linha de homologacao no registro "
                          f"(encontradas: {len(linha)})")
        return _texto(estado == "rascunho-nao-homologado", f"estado={estado!r}")

    itens.checar("v1.3: estado honesto — rascunho ate existir a homologacao registrada", _estado_honesto)

    homologacao = dado.get("homologacao") or {}
    itens.add("v1.3: `homologacao.estado` nao se declara aprovada sem registro",
              (homologacao.get("estado") == "pendente"
               and not homologacao.get("registrada_em")
               and not homologacao.get("registrada_por"))
              or (homologacao.get("estado") == "aprovada"
                  and homologacao.get("registrada_por") == "Anderson Ribeiro"
                  and bool(homologacao.get("registrada_em"))),
              f"homologacao={homologacao}")
    itens.add("v1.3: declarar-se EM VIGOR exige `homologacao` aprovada, com data e responsável",
              dado.get("estado") != "em-vigor"
              or (homologacao.get("estado") == "aprovada"
                  and bool(homologacao.get("registrada_em"))
                  and homologacao.get("registrada_por") == "Anderson Ribeiro"),
              f"estado={dado.get('estado')!r} homologacao={homologacao}")
    itens.add("v1.3: `congelada_em` só é preenchido quando a versão está EM VIGOR "
              "(rascunho não se declara congelado)",
              (dado.get("estado") == "em-vigor") == bool(str(dado.get("congelada_em") or "").strip()),
              f"estado={dado.get('estado')!r} congelada_em={dado.get('congelada_em')!r}")

    # ---- 4.3 a lane declarada por codigo canonico --------------------------
    itens.add("v1.3: `lane_por_codigo_de_acao` declarado como mapa nao vazio",
              isinstance(mapa, dict) and bool(mapa), f"mapa={mapa!r}")
    itens.add("v1.3: toda lane declarada no mapa e uma lane declarada da politica",
              isinstance(mapa, dict) and bool(mapa)
              and all(str(v) in lanes for v in mapa.values()),
              f"valores={sorted({str(v) for v in (mapa or {}).values()})} lanes={lanes}")

    def _cobertura_espelhada():
        # Espelho medido no CODIGO, nas duas direcoes. Codigo comum sem lane = card que
        # abstem em silencio; chave que nao e codigo comum = typo que abstem em silencio.
        if not isinstance(mapa, dict) or not comuns:
            return _texto(False, f"mapa={mapa!r} comuns={sorted(comuns)}")
        sem_lane = sorted(c for c in comuns if c not in mapa)
        desconhecidas = sorted(c for c in mapa if c not in comuns)
        return _texto(not sem_lane and not desconhecidas,
                      f"sem lane={sem_lane} fora dos comuns={desconhecidas}")

    itens.checar("v1.3: o mapa cobre TODOS os codigos comuns do roteador (espelho no codigo, "
                 "nas duas direcoes; hoje CINCO com o codigo novo)", _cobertura_espelhada)

    def _mapa_conservador():
        conservadora = limiares.get("lane_conservadora")
        if not isinstance(mapa, dict) or not mapa:
            return _texto(False, "sem mapa")
        fora = sorted({str(v) for v in mapa.values() if str(v) != str(conservadora)})
        return _texto(not fora and str(conservadora) == "high",
                      f"lane_conservadora={conservadora!r} valores fora dela={fora}")

    itens.checar("v1.3: o mapa desta versao e o CONSERVADOR (todos na lane conservadora homologada)",
                 _mapa_conservador)

    itens.add(f"v1.3: o codigo NOMEADO pelo dono (`{CODIGO_NOVO}`) tem lane declarada",
              isinstance(mapa, dict) and CODIGO_NOVO in mapa,
              f"mapa={mapa!r}")

    def _um_codigo_so():
        """O mapa da v1.3 = mapa da v1.2 + EXATAMENTE o codigo nomeado."""
        anterior = (yaml.safe_load(YAML_V12.read_text(encoding="utf-8")) or {}).get(
            "lane_por_codigo_de_acao") or {}
        if not isinstance(mapa, dict) or not isinstance(anterior, dict):
            return _texto(False, f"mapa={mapa!r} v1.2={anterior!r}")
        adicionados = sorted(set(mapa) - set(anterior))
        removidos = sorted(set(anterior) - set(mapa))
        mudados = sorted(c for c in set(mapa) & set(anterior) if str(mapa[c]) != str(anterior[c]))
        return _texto(adicionados == [CODIGO_NOVO] and not removidos and not mudados,
                      f"adicionados={adicionados} removidos={removidos} "
                      f"lane mudada={mudados} (esperado: so `{CODIGO_NOVO}` adicionado)")

    itens.checar("v1.3: o mapa muda UMA coisa — adiciona o codigo nomeado e nao remove nem "
                 "altera lane de nenhum outro (decisao do dono: um codigo so)", _um_codigo_so)

    regra = dado.get("regra_da_lane_declarada") or {}
    itens.add("v1.3: `regra_da_lane_declarada` declara as chaves do contrato",
              all(str(regra.get(c) or "").strip() for c in CHAVES_DA_REGRA_DA_LANE),
              f"faltando={[c for c in CHAVES_DA_REGRA_DA_LANE if not str(regra.get(c) or '').strip()]}")
    itens.add("v1.3: a regra diz, em prosa, que sem declaracao a decisao ABSTEM",
              "abstem" in normalizar(regra.get("sem_declaracao"))
              and "conservadora" in normalizar(regra.get("sem_declaracao")),
              f"sem_declaracao={str(regra.get('sem_declaracao'))[:90]!r}")
    itens.add("v1.3: a regra diz que os limiares NAO se aplicam a lane declarada",
              "nao se aplicam" in normalizar(regra.get("limiares")),
              f"limiares={str(regra.get('limiares'))[:90]!r}")

    def _escopo_estreito_declarado():
        """O escopo do codigo novo NAO fica so no card: a politica declara, em prosa."""
        escopo = normalizar(regra.get("escopo"))
        faltando = [t for t in ("desenvolvimento", "producao", "credencial") if t not in escopo]
        return _texto(not faltando and CODIGO_NOVO in escopo,
                      f"escopo={str(regra.get('escopo'))[:120]!r} faltando={faltando}")

    itens.checar("v1.3: o escopo estreito do codigo nomeado esta declarado na regra "
                 "(desenvolvimento, sem producao, sem credencial)", _escopo_estreito_declarado)

    # ---- 4.4 o contrato da v1.1 sobrevive ----------------------------------
    itens.add("v1.3: a lane conservadora da v1.1 continua declarada (e `high`)",
              limiares.get("lane_conservadora") == "high",
              f"lane_conservadora={limiares.get('lane_conservadora')!r}")

    def _ramos_preservados():
        ambientes = ((dado.get("regra_de_lane_por_ambiente") or {}).get("ambientes") or [])
        encontrados = {str(a.get("ambiente")): (a.get("lane_minima"), bool(a.get("aprovacao_humana_registrada")))
                       for a in ambientes if isinstance(a, dict)}
        esperado = {nome: (lane, aprovacao) for nome, lane, aprovacao in RAMOS_ESPERADOS}
        return _texto(encontrados == esperado, f"ramos={encontrados} esperado={esperado}")

    itens.checar("v1.3: os dois ramos da regra por ambiente sobrevivem, com lane minima e "
                 "aprovacao humana identicas as da v1.1 (a regra SO ELEVA)", _ramos_preservados)
    for chave in ("precedencia", "fallback", "metricas"):
        itens.add(f"v1.3: a secao `{chave}` da v1.1 continua declarada",
                  bool(dado.get(chave)), f"tipo={type(dado.get(chave)).__name__}")
    itens.add("v1.3: `lanes.*.exemplos` continua declarado em todas as lanes (linha de base do benchmark)",
              all(isinstance((dado.get("lanes") or {}).get(l), dict)
                  and bool((dado.get("lanes") or {}).get(l, {}).get("exemplos")) for l in lanes),
              f"lanes={lanes}")

    # ---- 4.5 o portao de versao FECHADO ------------------------------------
    vig = dado.get("versao_em_vigor") or {}

    def _vigencia_coerente_com_o_estado():
        """Rascunho → a versao anterior em vigor (v1.2) e ESTA declarada como proxima.
        Em vigor → esta, com a v1.2 preservada para auditoria. Nunca um estado morno."""
        em_vigor_esta = dado.get("estado") == "em-vigor"
        esperado = VERSAO_V13 if em_vigor_esta else VERSAO_V12
        detalhe = (f"estado={dado.get('estado')!r} versao_em_vigor={vig.get('versao')!r} "
                   f"esperado={esperado!r}")
        if vig.get("versao") != esperado:
            return _texto(False, detalhe)
        if em_vigor_esta:
            preservada = vig.get("preservada_para_auditoria_v1_2") or {}
            return _texto(preservada.get("versao") == VERSAO_V12,
                          detalhe + f" preservada_v1_2={preservada.get('versao')!r}")
        proxima = vig.get("proxima_versao") or (dado.get("proxima_versao") or {})
        caminho = str(proxima.get("caminho") or "")
        return _texto(proxima.get("versao") == VERSAO_V13 and bool(caminho)
                      and os.path.realpath(str(RAIZ / caminho)) == os.path.realpath(str(YAML_V13)),
                      detalhe + f" proxima_versao={proxima.get('versao')!r} caminho={caminho!r}")

    itens.checar("v1.3: vigência coerente com o estado — rascunho: a v1.2 em vigor e a v1.3 "
                 "declarada como próxima; em vigor: a v1.3 em vigor e a v1.2 preservada "
                 "para auditoria", _vigencia_coerente_com_o_estado)

    def _a_versao_em_vigor_declarada_se_declara_em_vigor():
        """O caminho declarado como EM VIGOR tem de ser um arquivo que se diz em vigor."""
        caminho = str(vig.get("caminho") or "")
        if not caminho or not (RAIZ / caminho).is_file():
            return _texto(False, f"caminho da versao em vigor inexistente: {caminho!r}")
        declaracao = yaml.safe_load((RAIZ / caminho).read_text(encoding="utf-8")) or {}
        return _texto(str(declaracao.get("estado")) == "em-vigor"
                      and declaracao.get("versao") == vig.get("versao"),
                      f"{caminho}: versao={declaracao.get('versao')!r} "
                      f"estado={declaracao.get('estado')!r}")

    itens.checar("v1.3: o arquivo declarado em `versao_em_vigor` se declara, ele mesmo, em vigor "
                 "(rascunho nunca e apontado como a versao em vigor)",
                 _a_versao_em_vigor_declarada_se_declara_em_vigor)

    def _portao_fechado_medido_no_codigo():
        """O criterio: o roteador carrega POR PADRAO a versao EM VIGOR — e NAO esta."""
        padrao = os.path.realpath(str(getattr(modulo, "CAMINHO_POLITICA_PADRAO", "")))
        declarado = str(vig.get("caminho") or "")
        return _texto(bool(declarado) and os.path.realpath(str(RAIZ / declarado)) == padrao
                      and padrao != os.path.realpath(str(YAML_V13)),
                      f"padrao do roteador={padrao} declarado={declarado!r} "
                      f"rascunho={os.path.realpath(str(YAML_V13))}")

    itens.checar("v1.3: PORTÃO FECHADO — o roteador carrega POR PADRAO a versao em vigor "
                 "(v1.2) e o arquivo desta versao NAO e o padrao (medido no codigo)",
                 _portao_fechado_medido_no_codigo)

    itens.add("v1.3: `versao_em_vigor` declara desde quando a v1.2 está em vigor e qual roteador a executa",
              bool(str(vig.get("desde") or "").strip()) and bool(vig.get("roteador")),
              f"desde={vig.get('desde')!r} roteador={vig.get('roteador')!r}")

    execucao = dado.get("execucao") or {}
    itens.add("v1.3: o roteador declarado como executor e o roteador REAL (medido no codigo)",
              execucao.get("roteador_que_a_executa") == ROTEADOR_DA_V13
              and getattr(modulo, "ROUTER_VERSION", None) == ROTEADOR_DA_V13,
              f"declarado={execucao.get('roteador_que_a_executa')!r} "
              f"roteador real={getattr(modulo, 'ROUTER_VERSION', None)!r}")
    itens.add("v1.3: o roteador declara SUPORTE a versao (regra declarada tem de ser regra executavel)",
              VERSAO_V13 in set(getattr(modulo, "VERSOES_DE_POLITICA_SUPORTADAS", ())),
              f"suportadas={sorted(getattr(modulo, 'VERSOES_DE_POLITICA_SUPORTADAS', ()))}")
    itens.add("v1.3: o portao de versao se declara FECHADO na propria secao de execucao",
              "fechado" in normalizar(execucao.get("portao_de_versao")),
              f"portao_de_versao={str(execucao.get('portao_de_versao'))[:100]!r}")

    # ---- 4.6 documento e YAML dizem a mesma coisa --------------------------
    def _documento_concorda():
        frase_de_estado = ("em vigor" if dado.get("estado") == "em-vigor"
                           else "rascunho nao homologado")
        exigidos = {
            VERSAO_V13: "versao",
            frase_de_estado: "estado declarado no documento",
            "lane_por_codigo_de_acao": "mapa declarado",
            "codigos_de_acao_comuns": "cobertura contra o codigo",
            ROTEADOR_DA_V13: "executor declarado",
            CODIGO_NOVO: "o codigo nomeado",
            "lane conservadora": "lane conservadora",
            "abstencao": "abstencao declarada",
            "verificar_jev_policy_v1_3.py": "verificador",
            "sem producao": "escopo estreito declarado",
            "classificar_card": "linha de base historica",
        }
        ausentes = [rotulo for chave, rotulo in exigidos.items() if chave not in doc]
        # A tabela do mapa no documento tem de casar CODIGO a CODIGO com o YAML: procurar
        # so as palavras soltas deixaria passar uma tabela que aponta a lane errada.
        linhas_normalizadas = [normalizar(l) for l in doc_txt.splitlines()]
        for codigo, lane in sorted((mapa or {}).items()):
            alvo = f"| `{normalizar(codigo)}` | `{normalizar(lane)}` |"
            if not any(alvo in l for l in linhas_normalizadas):
                ausentes.append(f"linha da tabela '{alvo}' no documento")
        return _texto(not ausentes, f"ausentes no documento: {ausentes}" if ausentes else
                      "documento e YAML concordam nos pontos declarados")

    itens.checar("v1.3: documento e YAML concordam (versao, estado, mapa, codigo novo, escopo, executor)",
                 _documento_concorda)

    motivos = dado.get("motivo_da_versao") or []
    itens.add("v1.3: motivo da versao registrado (>= 5 motivos declarados)",
              isinstance(motivos, list) and len(motivos) >= 5, f"{len(motivos)} motivos")

    # ---- 4.7 o espelho do encaixe (o catalogo canonico tem uma fonte so) ---
    def _espelho_do_encaixe():
        espelho = [str(c) for c in ((yaml.safe_load(ACOES_DECLARADAS.read_text(encoding="utf-8"))
                                     or {}).get("codigos_validos") or [])]
        return _texto(espelho == list(getattr(modulo, "CODIGOS_DE_ACAO_COMUNS", ())),
                      f"espelho={espelho} roteador={list(getattr(modulo, 'CODIGOS_DE_ACAO_COMUNS', ()))}")

    itens.checar("v1.3: o espelho `codigos_validos` do acoes-declaradas.yaml casa com "
                 "`CODIGOS_DE_ACAO_COMUNS` do roteador (uma fonte so de catalogo)",
                 _espelho_do_encaixe)


# ---------------------------------------------------------------------------
# PARTE 5 — provas de comportamento contra o roteador
# ---------------------------------------------------------------------------
def itens_de_comportamento(itens: Itens, dado: dict, modulo) -> None:
    politica = modulo.carregar_politica(YAML_V13)
    mapa = dado.get("lane_por_codigo_de_acao") or {}
    conservadora = dado["limiares"]["lane_conservadora"]
    raiz_temporaria = pathlib.Path(tempfile.mkdtemp(prefix="jev-v13-comportamento-"))

    def card(texto, status="ready"):
        return {"id": "t_prova", "card_id": "t_prova", "titulo": texto,
                "descricao": texto, "status": status}

    def tarefa(texto, codigo=CODIGO_NOVO, ambiente=None, sinais=None):
        t = modulo.tarefa_a_partir_do_card(card(texto), politica)
        if codigo is not None:
            t["acao_codigo"] = str(codigo)
        if ambiente is not None:
            t["ambiente_alvo"] = ambiente
        if sinais:
            t["sinais"] = dict(sinais)
        return t

    # ---- C1 o codigo novo executa com texto limpo --------------------------
    def _codigo_novo_executa():
        resultado = modulo.decidir(
            tarefa("execucao generica de card de desenvolvimento, sem efeito externo"),
            politica=politica)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        origem = str(decisao.get("origem_da_classificacao") or "")
        return _texto(decisao.get("decidido") == "executar"
                      and decisao.get("pode_executar") is True
                      and recibo["lane"] == mapa.get(CODIGO_NOVO)
                      and recibo["lane"] == conservadora
                      and recibo["confidence"] is None
                      and str(decisao.get("codigo_de_acao")) == CODIGO_NOVO
                      and origem.startswith("politica:"),
                      f"decidido={decisao.get('decidido')!r} lane={recibo['lane']!r} "
                      f"codigo={decisao.get('codigo_de_acao')!r} origem={origem!r}")

    itens.checar(f"C1: `{CODIGO_NOVO}` com texto limpo e ambiente dev EXECUTA na lane declarada",
                 _codigo_novo_executa)

    # ---- C2 o escopo estreito: dominio sensivel barra ----------------------
    def _escopo_estreito_barra():
        problemas = []
        casos = (("sinal de producao", {"producao": True}, "producao_ou_release"),
                 ("sinal de credencial", {"credencial": True}, "credencial"),
                 ("sinal de dado de cliente", {"dado_de_cliente": True}, "dado_de_cliente"),
                 ("sinal de outbound a terceiro", {"outbound_a_terceiro": True}, "outbound_a_terceiro"))
        for rotulo, sinais, dominio in casos:
            resultado = modulo.decidir(
                tarefa("execucao generica de card de desenvolvimento", sinais=sinais),
                politica=politica)
            decisao, recibo = resultado["decisao"], resultado["recibo"]
            motivos = normalizar(json.dumps(decisao.get("motivos") or [], ensure_ascii=False))
            if decisao.get("pode_executar") or recibo["outcome"] != modulo.OUTCOME_ESCALAR:
                problemas.append(f"{rotulo}: {decisao.get('decidido')}/{recibo['outcome']}")
            elif dominio not in (decisao.get("dominios_sensiveis") or []):
                problemas.append(f"{rotulo}: dominios={decisao.get('dominios_sensiveis')}")
            elif "nao cobre o dominio sensivel" not in motivos:
                problemas.append(f"{rotulo}: motivo sem o dominio nao coberto: {motivos[:120]}")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"o codigo {CODIGO_NOVO} NAO executa com dominio sensivel declarado "
                      "(producao, credencial, dado de cliente, outbound): o escopo estreito "
                      "e medido, nao prometido")

    itens.checar(f"C2: o MESMO codigo `{CODIGO_NOVO}` com dominio sensivel declarado NAO executa "
                 "(escopo estreito: dev, sem producao e sem credencial)", _escopo_estreito_barra)

    # ---- C3 o mapa decide --------------------------------------------------
    def _mapa_decide():
        vistas = []
        for lane in ("small", "medium", "high"):
            copia = copy.deepcopy(dado)
            copia["lane_por_codigo_de_acao"] = {c: lane for c in mapa}
            caminho = _politica_temporaria(raiz_temporaria, copia)
            politica_copia = modulo.carregar_politica(caminho)
            resultado = modulo.decidir(
                tarefa("execucao generica de card de desenvolvimento"), politica=politica_copia)
            vistas.append((lane, resultado["recibo"]["lane"], resultado["decisao"]["decidido"]))
        return _texto(all(d == r and x == "executar" for d, r, x in vistas),
                      f"declarada -> registrada/decidido: {vistas}")

    itens.checar("C3: trocar a lane declarada no YAML troca a decisao (nenhuma lane literal no roteador)",
                 _mapa_decide)

    # ---- C4 sem lane declarada abstem -------------------------------------
    def _sem_lane_declarada_abstem():
        copia = copy.deepcopy(dado)
        copia["lane_por_codigo_de_acao"] = {c: v for c, v in mapa.items() if c != CODIGO_NOVO}
        caminho = _politica_temporaria(raiz_temporaria, copia)
        politica_copia = modulo.carregar_politica(caminho)
        resultado = modulo.decidir(
            tarefa("execucao generica de card de desenvolvimento"), politica=politica_copia)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        return _texto(decisao["decidido"] == "abster_e_escalar"
                      and decisao.get("pode_executar") is False
                      and recibo["lane"] == conservadora,
                      f"decidido={decisao['decidido']} lane={recibo['lane']} esperada={conservadora}")

    itens.checar(f"C4: codigo comum (`{CODIGO_NOVO}`) SEM lane declarada ABSTEM, registrando a "
                 "lane conservadora (ausencia de resposta e abstinencia)", _sem_lane_declarada_abstem)

    # ---- C5 o piso por ambiente continua elevando -------------------------
    def _piso_sobre_a_lane_declarada():
        resultado = modulo.decidir(
            tarefa("aplicar DDL/migration em qualquer ambiente", ambiente="vivo"),
            politica=politica)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        serializado = normalizar(json.dumps(resultado, ensure_ascii=False))
        return _texto(recibo["lane"] == "critical"
                      and decisao.get("pode_executar") is False
                      and "aprovacao humana" in serializado,
                      f"lane={recibo['lane']!r} decidido={decisao.get('decidido')!r} "
                      f"aprovacao_humana_exigida={'aprovacao humana' in serializado}")

    itens.checar(f"C5: o piso por ambiente continua ELEVANDO sobre a lane declarada do codigo "
                 f"novo (DDL em ambiente vivo chega a `critical` com aprovacao humana)",
                 _piso_sobre_a_lane_declarada)

    # ---- C6 o classificador nao e chamado ---------------------------------
    def _classificador_fora_do_caminho():
        original = modulo.classificar_card
        chamadas = []

        def espiao(*args, **kwargs):
            chamadas.append(1)
            raise AssertionError("o classificador de card foi chamado no caminho de decisao")

        modulo.classificar_card = espiao
        try:
            primeira = modulo.decidir(
                tarefa("execucao generica de card de desenvolvimento"),
                politica=politica)["recibo"]["lane"]
            segunda = modulo.decidir(
                modulo.tarefa_a_partir_do_card(card(TEXTO_DO_CRITICAL), politica),
                politica=politica)["recibo"]["lane"]
        finally:
            modulo.classificar_card = original
        base = modulo.classificar_card(card(TEXTO_DO_CRITICAL), politica)
        return _texto(not chamadas and primeira == mapa.get(CODIGO_NOVO)
                      and segunda == conservadora
                      and base.get("lane_proposta") == "critical",
                      f"chamadas no caminho de decisao={len(chamadas)} lanes={primeira!r}/{segunda!r} "
                      f"linha de base={base.get('lane_proposta')!r}")

    itens.checar("C6: o caminho de decisao NAO chama o classificador — e ele segue alcancavel "
                 "como linha de base do benchmark", _classificador_fora_do_caminho)

    # ---- C7 o recibo -------------------------------------------------------
    def _recibo_honesto():
        campos = list(modulo.campos_do_recibo(politica))
        recibo = modulo.decidir(
            tarefa("execucao generica de card de desenvolvimento"), politica=politica)["recibo"]
        return _texto(list(recibo.keys()) == campos
                      and recibo["confidence"] is None
                      and str(recibo.get("policy_version")) == VERSAO_V13
                      and str(recibo.get("router_version")) == ROTEADOR_DA_V13,
                      f"campos={len(recibo)} esperados={len(campos)} "
                      f"policy_version={recibo.get('policy_version')!r} "
                      f"router_version={recibo.get('router_version')!r}")

    itens.checar("C7: o recibo mantem os 13 campos e registra a versao da politica e do roteador "
                 "que decidiram", _recibo_honesto)

    # ---- C8 o D07 intacto --------------------------------------------------
    def _d07_intacto():
        resultado = modulo.decidir(tarefa(TEXTO_DO_SMALL, codigo=None), politica=politica)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        serializado = normalizar(json.dumps(resultado, ensure_ascii=False))
        return _texto(decisao.get("pode_executar") is False
                      and "codigo canonico" in serializado
                      and recibo["lane"] == conservadora,
                      f"decidido={decisao.get('decidido')!r} lane={recibo['lane']!r}")

    itens.checar("C8: acao SEM codigo canonico continua abstendo mesmo com o mapa declarado "
                 "(fail-closed do D07 intacto)", _d07_intacto)

    # ---- C9 declarar SUPORTE nao e entrar em vigor -------------------------
    def _em_vigor_abstem_com_o_codigo_novo():
        politica_em_vigor = modulo.carregar_politica()
        if politica_em_vigor.get("versao") != VERSAO_V12:
            return _texto(False, f"a versao em vigor medida nao e a v1.2: "
                                 f"{politica_em_vigor.get('versao')!r}")
        t = modulo.tarefa_a_partir_do_card(card("execucao generica de card de desenvolvimento"),
                                           politica_em_vigor)
        t["acao_codigo"] = CODIGO_NOVO
        resultado = modulo.decidir(t, politica=politica_em_vigor)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        return _texto(decisao.get("pode_executar") is False
                      and decisao.get("decidido") == "abster_e_escalar"
                      and recibo["outcome"] == modulo.OUTCOME_ESCALAR
                      and recibo["lane"] == conservadora
                      and str(decisao.get("codigo_de_acao")) == CODIGO_NOVO,
                      f"em vigor={politica_em_vigor.get('versao')!r} "
                      f"decidido={decisao.get('decidido')!r} outcome={recibo['outcome']!r} "
                      f"lane={recibo['lane']!r} codigo={decisao.get('codigo_de_acao')!r}")

    itens.checar(f"C9: a politica EM VIGOR (v1.2), com o codigo novo `{CODIGO_NOVO}`, ABSTEM — "
                 "declarar suporte a uma versao nao e entrar em vigor",
                 _em_vigor_abstem_com_o_codigo_novo)


# ---------------------------------------------------------------------------
# Composicao
# ---------------------------------------------------------------------------
def verificar(yaml_txt: str, doc_txt: str, base, modulo) -> Itens:
    itens = Itens()
    try:
        dado = yaml.safe_load(yaml_txt)
    except Exception as erro:  # noqa: BLE001
        itens.add("v1.3 YAML valido", False, f"nao parseia: {erro}")
        return itens
    itens.add("v1.3 YAML valido", isinstance(dado, dict),
              f"{len(dado)} chaves de topo" if isinstance(dado, dict) else "raiz nao e mapa")
    if not isinstance(dado, dict):
        return itens
    partes = (
        ("PARTE 1 (bateria da v1.0)", lambda: itens_da_bateria_da_v1_0(itens, yaml_txt, doc_txt, base)),
        ("PARTE 2 (bateria da v1.1)", lambda: itens_da_bateria_da_v1_1(itens, base, modulo)),
        ("PARTE 3 (bateria da v1.2)", lambda: itens_da_bateria_da_v1_2(itens, base, modulo)),
        ("PARTE 4 (contrato da v1.3)", lambda: itens_do_contrato(itens, dado, doc_txt, modulo)),
        ("PARTE 5 (comportamento do roteador)", lambda: itens_de_comportamento(itens, dado, modulo)),
    )
    for rotulo, parte in partes:
        try:
            parte()
        except Exception as erro:  # noqa: BLE001
            itens.add(f"{rotulo}: roda ate o fim", False,
                      f"excecao: {type(erro).__name__}: {erro}")
    return itens


def imprimir(itens: Itens) -> int:
    for nome, ok, detalhe in itens.lista:
        marca = "OK   " if ok else "FALHOU"
        print(f"{marca} {nome}" + (f"  [{detalhe}]" if detalhe else ""))
    return itens.falhas()


# ---------------------------------------------------------------------------
# Autoteste por mutacao (em copia temporaria; o arquivo versionado nunca e tocado)
# ---------------------------------------------------------------------------
def autoteste(base, modulo, yaml_txt: str, doc_txt: str) -> bool:
    print("\n=== AUTOTESTE: mutacoes que o verificador precisa reprovar ===")
    base_dado = yaml.safe_load(yaml_txt)
    mutacoes: list[tuple[str, str, str, object]] = []

    def mut_yaml(nome, mutador):
        dado = mutador(copy.deepcopy(base_dado))
        mutacoes.append((nome, texto_de_yaml(dado), doc_txt, None))

    def mut_doc(nome, mutador):
        mutacoes.append((nome, yaml_txt, mutador(doc_txt), None))

    def mut_roteador(nome, mutador):
        original = ROTEADOR.read_text(encoding="utf-8")
        mutado = mutador(original)
        if mutado == original:
            mutacoes.append((f"{nome} [MUTACAO NAO APLICADA]", "", "", None))
            return
        caminho = _arvore_do_roteador_mutado(mutado)
        mutacoes.append((nome, yaml_txt, doc_txt, carregar_modulo(caminho, f"roteador_mut_{abs(hash(nome))}")))

    def sem_o_codigo_novo(d):
        d["lane_por_codigo_de_acao"].pop(CODIGO_NOVO, None)
        return d

    def lane_inexistente(d):
        d["lane_por_codigo_de_acao"][CODIGO_NOVO] = "turbo"
        return d

    def remover_outro_codigo(d):
        d["lane_por_codigo_de_acao"].pop("ajuste_de_texto", None)
        return d

    def mapa_barato(d):
        d["lane_por_codigo_de_acao"] = {c: "small" for c in d["lane_por_codigo_de_acao"]}
        return d

    def mapa_muda_lane_de_outro(d):
        d["lane_por_codigo_de_acao"]["ajuste_de_texto"] = "medium"
        return d

    def estado_falso(d):
        d["estado"] = "em-vigor"
        d["homologacao"] = {"estado": "pendente", "registrada_em": None,
                            "registrada_por": None,
                            "registro": "docs/operations/registro-de-aprovacoes.md"}
        d["congelada_em"] = "2026-09-30"
        return d

    def executor_trocado(d):
        d["execucao"] = dict(d["execucao"], roteador_que_a_executa="jev-router-v1.2")
        return d

    def piso_sem_aprovacao(d):
        for ramo in d["regra_de_lane_por_ambiente"]["ambientes"]:
            if ramo.get("ambiente") == "vivo_ou_producao":
                ramo["aprovacao_humana_registrada"] = False
        return d

    def escopo_removido(d):
        d["regra_da_lane_declarada"]["escopo"] = "execucao de card"
        return d

    mut_yaml(f"YAML: o codigo nomeado (`{CODIGO_NOVO}`) sai do mapa (cobre a lacuna)", sem_o_codigo_novo)
    mut_yaml("YAML: mapa aponta lane inexistente", lane_inexistente)
    mut_yaml("YAML: OUTRO codigo comum sai do mapa (cobre a lacuna em outro ponto)", remover_outro_codigo)
    mut_yaml("YAML: mapa barato (small para todos)", mapa_barato)
    mut_yaml("YAML: a versao mexe na lane de um codigo que a v1.2 ja declarava", mapa_muda_lane_de_outro)
    mut_yaml("YAML: estado `em-vigor` com homologacao ainda PENDENTE (par incoerente)", estado_falso)
    mut_yaml("YAML: executor declarado trocado (regra declarada != roteador real)", executor_trocado)
    mut_yaml("YAML: ramo vivo perde a exigencia de aprovacao humana (piso rebaixado)", piso_sem_aprovacao)
    mut_yaml("YAML: escopo estreito do codigo novo removido da regra", escopo_removido)
    mut_doc("DOC: tabela do mapa discorda do YAML",
            lambda t: t.replace("| `execucao_de_card` | `high` |", "| `execucao_de_card` | `medium` |"))
    mut_doc("DOC: documento se declara EM VIGOR (rascunho mentindo sobre o proprio estado)",
            lambda t: t.replace("RASCUNHO NÃO HOMOLOGADO", "EM VIGOR"))
    mut_roteador("ROTEADOR: codigo comum sem lane declarada passa a executar "
                 "(a ausencia de resposta deixa de ser abstinencia)",
                 lambda c: c.replace(
                     '    return str(mapa.get(codigo) or "").strip()',
                     '    return str(mapa.get(codigo) or politica["_lane_conservadora"]).strip()'))
    mut_roteador("ROTEADOR: o codigo novo deixa de ser um codigo comum conhecido",
                 lambda c: c.replace('    "execucao_de_card",\n)', ')'))
    mut_roteador("ROTEADOR: o piso por ambiente deixa de elevar a lane declarada",
                 lambda c: c.replace(
                     '    plano["lane"] = _lane_com_piso(politica, tarefa, lane_final)[0]',
                     '    plano["lane"] = lane_final'))

    buracos = 0
    for nome, yaml_mutado, doc_mutado, modulo_mutado in mutacoes:
        if not yaml_mutado:
            print(f"  BURACO {nome}: a mutacao nao foi aplicada ao codigo")
            buracos += 1
            continue
        itens = verificar(yaml_mutado, doc_mutado, base, modulo_mutado or modulo)
        falhas = itens.falhas()
        if falhas:
            reprovados = [n for n, ok, _ in itens.lista if not ok][:3]
            print(f"  OK   {nome} -> {falhas} item(ns) reprovado(s) (ex.: {reprovados})")
        else:
            print(f"  FALHOU {nome} -> a mutacao passou: o verificador tem buraco")
            buracos += 1
    print(f"  autoteste: {len(mutacoes) - buracos}/{len(mutacoes)} mutacoes reprovadas")
    return buracos == 0


def main() -> int:
    faltando = [p for p in (YAML_V13, DOC_V13, YAML_V12, DOC_V12, YAML_V11, DOC_V11,
                            YAML_V10, DOC_V10, ROTEADOR) if not p.is_file()]
    if faltando:
        print("FALHOU arquivo ausente: " + ", ".join(str(p) for p in faltando))
        return 1

    base = carregar_modulo(VERIFICADOR_V10, "verificador_v10_para_v13")
    modulo = carregar_modulo(ROTEADOR, "roteador_da_suite_v13")

    print("=" * 72)
    print("VERIFICADOR DA JEV DECISION POLICY v1.3 (rascunho nao homologado)")
    print(f"  politica:  {YAML_V13.relative_to(RAIZ)}")
    print(f"  documento: {DOC_V13.relative_to(RAIZ)}")
    print(f"  roteador:  {modulo.ROUTER_VERSION} "
          f"(suporta {sorted(modulo.VERSOES_DE_POLITICA_SUPORTADAS)})")
    print(f"  em vigor:  {YAML_V12.relative_to(RAIZ)} (portao fechado ate a homologacao)")
    print("=" * 72)

    yaml_txt = YAML_V13.read_text(encoding="utf-8")
    doc_txt = DOC_V13.read_text(encoding="utf-8")
    itens = verificar(yaml_txt, doc_txt, base, modulo)
    falhas = imprimir(itens)

    pediu_autoteste = "--autoteste" in sys.argv
    teste_ok = True
    if pediu_autoteste:
        teste_ok = autoteste(base, modulo, yaml_txt, doc_txt)

    print()
    if falhas == 0 and teste_ok:
        extra = " + autoteste OK" if pediu_autoteste else " (autoteste nao pedido: rode com --autoteste)"
        print(f"RESULTADO: PASS ({len(itens.lista)} itens, 0 falhas){extra}")
        return 0
    print(f"RESULTADO: FALHOU ({len(itens.lista)} itens, {falhas} falha(s))"
          + ("" if teste_ok else " + autoteste com buraco"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
