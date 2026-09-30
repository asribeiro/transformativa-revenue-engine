#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador da JEV Decision Policy v1.2 (card TRE-W0-E04-T09).

Mesmo rito das v1.0 e v1.1: documento e arquivo legivel por maquina NAO podem divergir,
e cada item existe por um motivo declarado. Nenhuma parte substitui a anterior.

  PARTE 1 — a bateria da v1.0, inteira, sobre a v1.2 (versoes parametrizadas).
  PARTE 2 — a bateria da v1.1, inteira, RODADA SOBRE A v1.1: prova de que a versao
    anterior nao foi tocada e de que ela continua executavel pelo roteador no ar.
  PARTE 3 — o contrato novo, declarado:
    * a versao se declara RASCUNHO e nao homologada — e o item de estado vira GUARDA:
      marcar-se `em-vigor` sem a linha de homologacao do Anderson no registro de
      aprovacoes e reprovacao, nao formalidade;
    * `lane_por_codigo_de_acao` declarado, com lane existente em cada valor e COBERTURA
      conferida contra `CODIGOS_DE_ACAO_COMUNS` do roteador — espelho medido no CODIGO,
      nas duas direcoes (codigo comum sem lane = card que abstem em silencio);
    * o mapa desta versao e o CONSERVADOR (todos na lane conservadora homologada), e o
      documento diz o mesmo: o valor declarado e o que a medicao sustenta — o
      classificador empatava com a constante estrutural;
    * o contrato da v1.1 sobrevive: lane conservadora, os dois ramos da regra por
      ambiente (lane minima e aprovacao humana), precedencia, fallback e metricas;
    * o portao de versao esta FECHADO: a versao em vigor continua sendo a v1.1 e o
      roteador carrega esse caminho POR PADRAO — medido contra o codigo, nao por leitura;
    * o executor declarado (`execucao.roteador_que_a_executa`) e o roteador REAL.

  PARTE 4 — provas de COMPORTAMENTO contra o roteador. Prosa em YAML nao prova nada:
    C1. PALAVRAS NAO DECIDEM LANE: um card cujo texto o classificador historico leria
        como `critical` (o caso vivo do proprio card T09: "producao" e "credencial"
        citados PARA ISENTAR) tem de sair na lane declarada — nunca em `critical`;
    C2. O MAPA DECIDE: trocar a lane declarada no YAML troca a decisao (nenhuma lane
        literal no roteador — trocar o YAML tem de mudar a lane);
    C3. SEM LANE DECLARADA A DECISAO ABSTEM, registrando a lane conservadora
        (ausencia de resposta e abstinencia, nunca permissao);
    C4. O CLASSIFICADOR NAO E CHAMADO pelo caminho de decisao (espiao armado que
        levanta se for chamado) — e continua alcancavel pelo benchmark, como linha de
        base historica;
    C5. O PISO POR AMBIENTE CONTINUA ELEVANDO sobre a lane declarada (DDL declarado
        `high` em ambiente vivo chega a `critical` com aprovacao humana exigida);
    C6. O RECIBO continua com os 13 campos e a origem da lane declarada HONESTA;
    C7. a falha fechada do D07 (acao sem codigo canonico) NAO depende do classificador;
    C8. politica SEM mapa nao ganha lane por omissao: todo card abstem.

Uso:
    python3 scripts/verificar_jev_policy_v1_2.py                # verifica
    python3 scripts/verificar_jev_policy_v1_2.py --autoteste    # verifica + mutacoes

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
YAML_V12 = RAIZ / "hermes/jev/policy_v1_2.yaml"
DOC_V12 = RAIZ / "docs/architecture/jev-decision-policy-v1.2.md"
YAML_V11 = RAIZ / "hermes/jev/policy_v1_1.yaml"
DOC_V11 = RAIZ / "docs/architecture/jev-decision-policy-v1.1.md"
YAML_V10 = RAIZ / "hermes/jev/policy_v1.yaml"
DOC_V10 = RAIZ / "docs/architecture/jev-decision-policy-v1.md"
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
REGISTRO = RAIZ / "docs/operations/registro-de-aprovacoes.md"
VERIFICADOR_V11 = RAIZ / "scripts/verificar_jev_policy_v1_1.py"
VERIFICADOR_V10 = RAIZ / "scripts/verificar_jev_policy.py"

VERSAO_V12 = "jev-policy-v1.2"
VERSAO_V11 = "jev-policy-v1.1"
ROTEADOR_DA_V12 = "jev-router-v1.2"

# O contrato DESTA versao e o de QUATRO codigos canonicos — o catalogo com que ela foi
# homologada. DATADO em 30/09/2026 pelo card TRE-W0-E04-T10: o dono nomeou um codigo comum
# novo (`execucao_de_card`), que e do contrato da versao SEGUINTE (v1.3). Manter aqui a
# expectativa de "todos os codigos comuns do roteador" faria a suite desta versao reprovar
# no dia em que o catalogo crescesse por decisao legitima — e o item que existe para pegar
# buraco do CONTRATO DA v1.2 passaria a medir outra coisa.
CODIGOS_CONTRATADOS_DA_V12 = ("ajuste_de_texto", "consulta_interna", "operacao_comercial",
                              "migracao_de_esquema")
CODIGO_NOMEADO_DEPOIS = "execucao_de_card"

# Ramo da regra por ambiente -> (lane minima, aprovacao humana registrada). Herdado da v1.1.
RAMOS_ESPERADOS = (
    ("novo_ou_dev", "high", False),
    ("vivo_ou_producao", "critical", True),
)

CHAVES_DA_REGRA_DA_LANE = ("fonte", "cobertura", "sem_declaracao", "limiares", "direcao")

# O texto que o classificador historico lia como `critical`, e o que ele lia como `small`.
# Os dois vem dos `exemplos`/`escopo` declarados nas lanes do proprio YAML.
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
    raiz = pathlib.Path(tempfile.mkdtemp(prefix="jev-v12-mut-"))
    (raiz / "hermes/jev/routing").mkdir(parents=True)
    (raiz / "hermes/jev/routing/router.py").write_text(codigo, encoding="utf-8")
    for nome in ("policy_v1.yaml", "policy_v1_1.yaml", "policy_v1_2.yaml"):
        os.symlink(RAIZ / "hermes/jev" / nome, raiz / "hermes/jev" / nome)
    os.symlink(RAIZ / "hermes/policies", raiz / "hermes/policies")
    return raiz / "hermes/jev/routing/router.py"


# ---------------------------------------------------------------------------
# PARTE 1 — a bateria da v1.0 sobre a v1.2
# ---------------------------------------------------------------------------
def itens_da_bateria_da_v1_0(itens: Itens, yaml_txt: str, doc_txt: str, base) -> None:
    for nome, ok, detalhe in base.verificar(yaml_txt, doc_txt, VERSAO_V12):
        itens.add(f"v1.0-bateria: {nome}", ok, detalhe)


# ---------------------------------------------------------------------------
# PARTE 2 — a bateria da v1.1 sobre a v1.1 (a versao anterior nao foi tocada)
# ---------------------------------------------------------------------------
def itens_da_bateria_da_v1_1(itens: Itens, base, modulo) -> None:
    v11 = carregar_modulo(VERIFICADOR_V11, "verificador_v11_para_v12")
    yaml_v11 = YAML_V11.read_text(encoding="utf-8")
    doc_v11 = DOC_V11.read_text(encoding="utf-8")
    proprios = v11.verificar(yaml_v11, doc_v11, base, modulo)
    for nome, ok, detalhe in proprios.lista:
        itens.add(f"v1.1-bateria: {nome}", ok, detalhe)


# ---------------------------------------------------------------------------
# PARTE 3 — o contrato novo
# ---------------------------------------------------------------------------
def itens_do_contrato(itens: Itens, dado: dict, yaml_txt: str, doc_txt: str, modulo) -> None:
    doc = normalizar(doc_txt)
    lanes = list((dado.get("lanes") or {}).keys())
    limiares = dado.get("limiares") or {}
    mapa = dado.get("lane_por_codigo_de_acao")
    comuns = set(getattr(modulo, "CODIGOS_DE_ACAO_COMUNS", ()))

    # ---- 3.1 identidade da versao -----------------------------------------
    itens.add("v1.2: a versao se identifica como jev-policy-v1.2",
              dado.get("versao") == VERSAO_V12, f"versao={dado.get('versao')!r}")
    itens.add("v1.2: declara `substitui: jev-policy-v1.1`",
              dado.get("substitui") == VERSAO_V11, f"substitui={dado.get('substitui')!r}")
    itens.add("v1.2: a v1.1 continua existindo e se declarando v1.1 (nada foi sobrescrito)",
              YAML_V11.is_file()
              and (yaml.safe_load(YAML_V11.read_text(encoding="utf-8")) or {}).get("versao") == VERSAO_V11,
              f"versao do arquivo preservado={(yaml.safe_load(YAML_V11.read_text(encoding='utf-8')) or {}).get('versao')!r}")

    # ---- 3.2 estado: rascunho honesto, e a guarda da homologacao -----------
    def _estado_honesto():
        estado = dado.get("estado")
        if estado == "em-vigor":
            # GUARDA: so pode se declarar em vigor com a homologacao REGISTRADA.
            texto_registro = normalizar(REGISTRO.read_text(encoding="utf-8")) if REGISTRO.is_file() else ""
            # A versao pode estar escrita por extenso (`jev-policy-v1.2`) ou como marcador
            # curto (`JEV v1.2`) — o registro e escrito por gente, mas tem de NOMEAR a versao.
            numero = VERSAO_V12.rsplit("-", 1)[-1]
            linha = [l for l in texto_registro.splitlines()
                     if (VERSAO_V12 in normalizar(l) or numero in normalizar(l))
                     and "anderson" in normalizar(l)
                     and ("homolog" in normalizar(l) or "aprovad" in normalizar(l))]
            return _texto(bool(linha),
                          "estado=em-vigor exige linha de homologacao no registro "
                          f"(encontradas: {len(linha)})")
        return _texto(estado == "rascunho-nao-homologado", f"estado={estado!r}")

    itens.checar("v1.2: estado honesto — rascunho ate existir a homologacao registrada", _estado_honesto)

    homologacao = dado.get("homologacao") or {}
    itens.add("v1.2: `homologacao.estado` nao se declara aprovada sem registro",
              (homologacao.get("estado") == "pendente"
               and not homologacao.get("registrada_em")
               and not homologacao.get("registrada_por"))
              or (homologacao.get("estado") == "aprovada"
                  and homologacao.get("registrada_por") == "Anderson Ribeiro"
                  and bool(homologacao.get("registrada_em"))),
              f"homologacao={homologacao}")
    itens.add("v1.2: declarar-se EM VIGOR exige `homologacao` aprovada, com data e responsável",
              dado.get("estado") != "em-vigor"
              or (homologacao.get("estado") == "aprovada"
                  and bool(homologacao.get("registrada_em"))
                  and homologacao.get("registrada_por") == "Anderson Ribeiro"),
              f"estado={dado.get('estado')!r} homologacao={homologacao}")
    itens.add("v1.2: `congelada_em` só é preenchido quando a versão está EM VIGOR "
              "(rascunho não se declara congelado)",
              (dado.get("estado") == "em-vigor") == bool(str(dado.get("congelada_em") or "").strip()),
              f"estado={dado.get('estado')!r} congelada_em={dado.get('congelada_em')!r}")

    # ---- 3.3 a lane declarada por codigo canonico --------------------------
    itens.add("v1.2: `lane_por_codigo_de_acao` declarado como mapa nao vazio",
              isinstance(mapa, dict) and bool(mapa), f"mapa={mapa!r}")
    itens.add("v1.2: toda lane declarada no mapa e uma lane declarada da politica",
              isinstance(mapa, dict) and bool(mapa)
              and all(str(v) in lanes for v in mapa.values()),
              f"valores={sorted({str(v) for v in (mapa or {}).values()})} lanes={lanes}")

    def _cobertura_espelhada():
        # DATADO em 30/09/2026 pelo card TRE-W0-E04-T10: o dono NOMEOU um codigo comum novo
        # (`execucao_de_card`) e a versao SEGUINTE (v1.3, rascunho) e que responde pelo
        # catalogo inteiro. O contrato DESTA versao e o de QUATRO codigos, e continua sendo
        # conferido contra o codigo do roteador: os quatro seguem comuns, e chave que nao e
        # codigo comum (typo/renomeacao silenciosa) continua reprovando. Nao se apaga o item
        # nem se afrouxa o outro lado — o que muda e QUEM responde pelo codigo nomeado depois.
        if not isinstance(mapa, dict) or not comuns:
            return _texto(False, f"mapa={mapa!r} comuns={sorted(comuns)}")
        sem_lane = sorted(c for c in CODIGOS_CONTRATADOS_DA_V12 if c not in mapa)
        desconhecidas = sorted(c for c in mapa if c not in comuns)
        return _texto(not sem_lane and not desconhecidas,
                      f"sem lane no contrato da v1.2={sem_lane} "
                      f"chaves estranhas ao catalogo={desconhecidas}")

    itens.checar("v1.2 (datado em 30/09/2026): o mapa cobre os QUATRO codigos do contrato desta "
                 "versao e nenhuma chave e estranha ao catalogo do roteador",
                 _cobertura_espelhada)

    itens.add("v1.2 (datado em 30/09/2026): esta versao NAO declara lane para o codigo nomeado "
              "depois (`execucao_de_card`) — e por isso ele abstem sob ela; quem responde por "
              "ele e a versao seguinte",
              isinstance(mapa, dict) and CODIGO_NOMEADO_DEPOIS not in mapa,
              f"mapa={sorted((mapa or {}))!r}")

    def _mapa_conservador():
        conservadora = limiares.get("lane_conservadora")
        if not isinstance(mapa, dict) or not mapa:
            return _texto(False, "sem mapa")
        fora = sorted({str(v) for v in mapa.values() if str(v) != str(conservadora)})
        return _texto(not fora and str(conservadora) == "high",
                      f"lane_conservadora={conservadora!r} valores fora dela={fora}")

    itens.checar("v1.2: o mapa desta versao e o CONSERVADOR (todos na lane conservadora homologada)",
                 _mapa_conservador)

    regra = dado.get("regra_da_lane_declarada") or {}
    itens.add("v1.2: `regra_da_lane_declarada` declara as chaves do contrato",
              all(str(regra.get(c) or "").strip() for c in CHAVES_DA_REGRA_DA_LANE),
              f"faltando={[c for c in CHAVES_DA_REGRA_DA_LANE if not str(regra.get(c) or '').strip()]}")
    itens.add("v1.2: a regra diz, em prosa, que sem declaracao a decisao ABSTEM",
              "abstem" in normalizar(regra.get("sem_declaracao"))
              and "conservadora" in normalizar(regra.get("sem_declaracao")),
              f"sem_declaracao={str(regra.get('sem_declaracao'))[:90]!r}")
    itens.add("v1.2: a regra diz que os limiares NAO se aplicam a lane declarada",
              "nao se aplicam" in normalizar(regra.get("limiares")),
              f"limiares={str(regra.get('limiares'))[:90]!r}")

    # ---- 3.4 o contrato da v1.1 sobrevive ----------------------------------
    itens.add("v1.2: a lane conservadora da v1.1 continua declarada (e `high`)",
              limiares.get("lane_conservadora") == "high",
              f"lane_conservadora={limiares.get('lane_conservadora')!r}")

    def _ramos_preservados():
        ambientes = ((dado.get("regra_de_lane_por_ambiente") or {}).get("ambientes") or [])
        encontrados = {str(a.get("ambiente")): (a.get("lane_minima"), bool(a.get("aprovacao_humana_registrada")))
                       for a in ambientes if isinstance(a, dict)}
        esperado = {nome: (lane, aprovacao) for nome, lane, aprovacao in RAMOS_ESPERADOS}
        return _texto(encontrados == esperado, f"ramos={encontrados} esperado={esperado}")

    itens.checar("v1.2: os dois ramos da regra por ambiente sobrevivem, com lane minima e "
                 "aprovacao humana identicas as da v1.1", _ramos_preservados)
    for chave in ("precedencia", "fallback", "metricas"):
        itens.add(f"v1.2: a secao `{chave}` da v1.1 continua declarada",
                  bool(dado.get(chave)), f"tipo={type(dado.get(chave)).__name__}")
    itens.add("v1.2: `lanes.*.exemplos` continua declarado em todas as lanes (linha de base do benchmark)",
              all(isinstance((dado.get("lanes") or {}).get(l), dict)
                  and bool((dado.get("lanes") or {}).get(l, {}).get("exemplos")) for l in lanes),
              f"lanes={lanes}")

    # ---- 3.5 o portao de versao -------------------------------------------
    vig = dado.get("versao_em_vigor") or {}

    def _vigencia_coerente_com_o_estado():
        """Rascunho → a v1.1 em vigor e a v1.2 declarada como próxima. Em vigor → a v1.2,
        com a v1.1 declarada como PRESERVADA para auditoria. Nunca um estado morno."""
        em_vigor_esta = dado.get("estado") == "em-vigor"
        esperado = VERSAO_V12 if em_vigor_esta else VERSAO_V11
        detalhe = (f"estado={dado.get('estado')!r} versao_em_vigor={vig.get('versao')!r} "
                   f"esperado={esperado!r}")
        if vig.get("versao") != esperado:
            return _texto(False, detalhe)
        if em_vigor_esta:
            preservada = vig.get("preservada_para_auditoria_v1_1") or {}
            return _texto(preservada.get("versao") == VERSAO_V11,
                          detalhe + f" preservada_v1_1={preservada.get('versao')!r}")
        proxima = vig.get("proxima_versao") or {}
        return _texto(proxima.get("versao") == VERSAO_V12
                      and os.path.realpath(str(RAIZ / str(proxima.get("caminho") or ""))) == os.path.realpath(str(YAML_V12)),
                      detalhe + f" proxima_versao={proxima.get('versao')!r}")

    itens.checar("v1.2: vigência coerente com o estado — rascunho: v1.1 em vigor e a v1.2 declarada como "
                 "próxima; em vigor: v1.2 em vigor e a v1.1 preservada para auditoria",
                 _vigencia_coerente_com_o_estado)

    def _vigor_e_o_padrao_do_codigo():
        caminho = str(vig.get("caminho") or "")
        padrao = os.path.realpath(str(getattr(modulo, "CAMINHO_POLITICA_PADRAO", "")))
        return _texto(bool(caminho) and os.path.realpath(str(RAIZ / caminho)) == padrao,
                      f"declarado={caminho!r} padrao do roteador={padrao}")

    itens.checar("v1.2: o roteador carrega POR PADRAO o arquivo declarado em `versao_em_vigor` "
                 "(medido no codigo)", _vigor_e_o_padrao_do_codigo)

    proxima = vig.get("proxima_versao") or {}
    itens.add("v1.2: `versao_em_vigor` declara desde quando está em vigor e qual roteador a executa",
              bool(str(vig.get("desde") or "").strip())
              and vig.get("roteador") == ROTEADOR_DA_V12,
              f"desde={vig.get('desde')!r} roteador={vig.get('roteador')!r}")

    execucao = dado.get("execucao") or {}
    itens.add("v1.2 (datado em 30/09/2026): o executor declarado e o roteador DESTA versao "
              "(`jev-router-v1.2`) e o roteador vivo ainda a executa (suporte declarado) — "
              "a igualdade com `ROUTER_VERSION` vivo deixou de ser exigivel quando a geracao "
              "do roteador avancou por decisao legitima; o que se exige de versao preservada e "
              "continuar EXECUTAVEL",
              execucao.get("roteador_que_a_executa") == ROTEADOR_DA_V12
              and VERSAO_V12 in set(getattr(modulo, "VERSOES_DE_POLITICA_SUPORTADAS", ())),
              f"declarado={execucao.get('roteador_que_a_executa')!r} "
              f"roteador vivo={getattr(modulo, 'ROUTER_VERSION', None)!r} "
              f"suportadas={sorted(getattr(modulo, 'VERSOES_DE_POLITICA_SUPORTADAS', ()))}")
    itens.add("v1.2: o roteador declara SUPORTE a versao v1.2 (regra declarada = regra executavel)",
              VERSAO_V12 in set(getattr(modulo, "VERSOES_DE_POLITICA_SUPORTADAS", ())),
              f"suportadas={sorted(getattr(modulo, 'VERSOES_DE_POLITICA_SUPORTADAS', ()))}")

    # ---- 3.6 documento e YAML dizem a mesma coisa --------------------------
    def _documento_concorda():
        # A frase de estado acompanha o YAML: o documento nao pode dizer "rascunho" de uma
        # versao em vigor, nem "em vigor" de um rascunho.
        frase_de_estado = ("em vigor" if dado.get("estado") == "em-vigor"
                           else "rascunho nao homologado")
        exigidos = {
            VERSAO_V12: "versao",
            frase_de_estado: "estado declarado no documento",
            "lane_por_codigo_de_acao": "mapa declarado",
            "codigos_de_acao_comuns": "cobertura contra o codigo",
            "jev-router-v1.2": "executor declarado",
            "lane conservadora": "lane conservadora",
            "abstencao": "abstencao declarada",
            "0,1875": "medicao do classificador isolado",
            "0,375": "medicao da constante estrutural",
            "verificar_jev_policy_v1_2.py": "verificador",
            "classificar_card": "linha de base historica",
        }
        ausentes = [rotulo for chave, rotulo in exigidos.items() if chave not in doc]
        # A tabela do mapa no documento tem de casar CODIGO a CODIGO com o YAML: procurar
        # so as palavras soltas deixaria passar uma tabela que aponta a lane errada (foi o
        # buraco que o autoteste pegou em 30/09/2026).
        linhas_normalizadas = [normalizar(l) for l in doc_txt.splitlines()]
        for codigo, lane in sorted((mapa or {}).items()):
            alvo = f"| `{normalizar(codigo)}` | `{normalizar(lane)}` |"
            if not any(alvo in l for l in linhas_normalizadas):
                ausentes.append(f"linha da tabela '{alvo}' no documento")
        return _texto(not ausentes, f"ausentes no documento: {ausentes}" if ausentes else
                      "documento e YAML concordam nos pontos declarados")

    itens.checar("v1.2: documento e YAML concordam (versao, estado, mapa, medicao, executor)",
                 _documento_concorda)

    motivos = dado.get("motivo_da_versao") or []
    itens.add("v1.2: motivo da versao registrado (>= 5 motivos declarados)",
              isinstance(motivos, list) and len(motivos) >= 5, f"{len(motivos)} motivos")


# ---------------------------------------------------------------------------
# PARTE 4 — provas de comportamento contra o roteador
# ---------------------------------------------------------------------------
def itens_de_comportamento(itens: Itens, dado: dict, modulo) -> None:
    politica = modulo.carregar_politica(YAML_V12)
    mapa = dado.get("lane_por_codigo_de_acao") or {}
    conservadora = dado["limiares"]["lane_conservadora"]
    raiz_temporaria = pathlib.Path(tempfile.mkdtemp(prefix="jev-v12-comportamento-"))

    def card(texto, status="ready"):
        return {"id": "t_prova", "card_id": "t_prova", "titulo": texto,
                "descricao": texto, "status": status}

    def tarefa(texto, codigo="ajuste_de_texto", ambiente=None):
        t = modulo.tarefa_a_partir_do_card(card(texto), politica)
        if codigo is not None:
            t["acao_codigo"] = codigo
        if ambiente is not None:
            t["ambiente_alvo"] = ambiente
        return t

    # ---- C1 palavras nao decidem lane -------------------------------------
    def _palavras_nao_decidem():
        historico = modulo.classificar_card(card(TEXTO_DO_CRITICAL), politica)
        resultado = modulo.decidir(tarefa(TEXTO_DO_CRITICAL), politica=politica)
        recibo, decisao = resultado["recibo"], resultado["decisao"]
        # O mesmo codigo com texto benigno: aqui a decisao executa e a origem da lane
        # aparece no rastro — prova de que a lane vem do MAPA, nao do texto.
        benigno = modulo.decidir(tarefa(TEXTO_DO_SMALL), politica=politica)
        origem = str(benigno["decisao"].get("origem_da_classificacao") or "")
        esperada = mapa.get("ajuste_de_texto")
        return _texto(historico.get("lane_proposta") == "critical"
                      and recibo["lane"] == esperada
                      and recibo["lane"] != "critical"
                      and recibo["confidence"] is None
                      and benigno["recibo"]["lane"] == esperada
                      and benigno["decisao"]["decidido"] == "executar"
                      and origem.startswith("politica:"),
                      f"linha de base historica={historico.get('lane_proposta')!r} "
                      f"lane(texto de risco)={recibo['lane']!r} decidido={decisao.get('decidido')!r} "
                      f"lane(texto benigno)={benigno['recibo']['lane']!r} origem={origem!r}")

    itens.checar("C1: texto que a linha de base historica lia como `critical` NAO decide lane "
                 "(o caso vivo do card T09: 'producao'/'credencial' citados para isentar)",
                 _palavras_nao_decidem)

    # ---- C2 o mapa decide --------------------------------------------------
    def _mapa_decide():
        vistas = []
        for lane in ("small", "medium", "high"):
            copia = copy.deepcopy(dado)
            copia["lane_por_codigo_de_acao"] = {c: lane for c in mapa}
            caminho = _politica_temporaria(raiz_temporaria, copia)
            politica_copia = modulo.carregar_politica(caminho)
            resultado = modulo.decidir(tarefa(TEXTO_DO_SMALL), politica=politica_copia)
            vistas.append((lane, resultado["recibo"]["lane"], resultado["decisao"]["decidido"]))
        return _texto(all(d == r and x == "executar" for d, r, x in vistas),
                      f"declarada -> registrada/decidido: {vistas}")

    itens.checar("C2: trocar a lane declarada no YAML troca a decisao (nenhuma lane literal no roteador)",
                 _mapa_decide)

    # ---- C3 sem lane declarada abstem -------------------------------------
    def _sem_lane_declarada_abstem():
        copia = copy.deepcopy(dado)
        copia["lane_por_codigo_de_acao"] = {c: v for c, v in mapa.items() if c != "ajuste_de_texto"}
        caminho = _politica_temporaria(raiz_temporaria, copia)
        politica_copia = modulo.carregar_politica(caminho)
        resultado = modulo.decidir(tarefa(TEXTO_DO_SMALL), politica=politica_copia)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        return _texto(decisao["decidido"] == "abster_e_escalar"
                      and decisao.get("pode_executar") is False
                      and recibo["lane"] == conservadora,
                      f"decidido={decisao['decidido']} lane={recibo['lane']} esperada={conservadora}")

    itens.checar("C3: codigo sem lane declarada ABSTEM, registrando a lane conservadora "
                 "(ausencia de resposta e abstinencia)", _sem_lane_declarada_abstem)

    # ---- C4 o classificador nao e chamado ---------------------------------
    def _classificador_fora_do_caminho():
        original = modulo.classificar_card
        chamadas = []

        def espiao(*args, **kwargs):
            chamadas.append(1)
            raise AssertionError("o classificador de card foi chamado no caminho de decisao")

        modulo.classificar_card = espiao
        try:
            primeira = modulo.decidir(tarefa(TEXTO_DO_SMALL), politica=politica)["recibo"]["lane"]
            segunda = modulo.decidir(modulo.tarefa_a_partir_do_card(card(TEXTO_DO_CRITICAL), politica),
                                     politica=politica)["recibo"]["lane"]
        finally:
            modulo.classificar_card = original
        # E continua alcancavel como LINHA DE BASE (o benchmark depende disso).
        base = modulo.classificar_card(card(TEXTO_DO_CRITICAL), politica)
        return _texto(not chamadas and primeira == mapa.get("ajuste_de_texto")
                      and segunda == mapa.get("ajuste_de_texto")
                      and base.get("lane_proposta") == "critical",
                      f"chamadas no caminho de decisao={len(chamadas)} lanes={primeira!r}/{segunda!r} "
                      f"linha de base={base.get('lane_proposta')!r}")

    itens.checar("C4: o caminho de decisao NAO chama o classificador — e ele segue alcancavel "
                 "como linha de base do benchmark", _classificador_fora_do_caminho)

    # ---- C5 o piso por ambiente continua elevando -------------------------
    def _piso_sobre_a_lane_declarada():
        texto = "aplicar DDL/migration em qualquer ambiente"
        resultado = modulo.decidir(tarefa(texto, codigo="migracao_de_esquema", ambiente="vivo"),
                                   politica=politica)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        serializado = normalizar(json.dumps(resultado, ensure_ascii=False))
        return _texto(recibo["lane"] == "critical"
                      and decisao.get("pode_executar") is False
                      and "aprovacao humana" in serializado,
                      f"lane={recibo['lane']!r} decidido={decisao.get('decidido')!r} "
                      f"aprovacao_humana_exigida={'aprovacao humana' in serializado}")

    itens.checar("C5: o piso por ambiente continua ELEVANDO sobre a lane declarada "
                 "(DDL `high` em ambiente vivo chega a `critical` com aprovacao humana)",
                 _piso_sobre_a_lane_declarada)

    # ---- C6 o recibo -------------------------------------------------------
    def _recibo_honesto():
        # DATADO em 30/09/2026 (card TRE-W0-E04-T10): o recibo tem de registrar a politica que
        # decidiu (v1.2, datado) e o roteador que decidiu de FATO — que e o roteador VIVO, nao
        # a geracao com que esta versao nasceu. Exigir aqui `router_version == jev-router-v1.2`
        # faria o recibo ter de mentir sobre quem decidiu.
        campos = list(modulo.campos_do_recibo(politica))
        recibo = modulo.decidir(tarefa(TEXTO_DO_SMALL), politica=politica)["recibo"]
        return _texto(list(recibo.keys()) == campos
                      and recibo["confidence"] is None
                      and str(recibo.get("policy_version")) == VERSAO_V12
                      and str(recibo.get("router_version")) == getattr(modulo, "ROUTER_VERSION", None),
                      f"campos={len(recibo)} esperados={len(campos)} "
                      f"policy_version={recibo.get('policy_version')!r} "
                      f"router_version={recibo.get('router_version')!r}")

    itens.checar("C6: o recibo mantem os 13 campos e registra a versao da politica e do roteador "
                 "que decidiram", _recibo_honesto)

    # ---- C7 a falha fechada do D07 nao depende do classificador ------------
    def _d07_independente():
        resultado = modulo.decidir(tarefa(TEXTO_DO_SMALL, codigo=None), politica=politica)
        decisao, recibo = resultado["decisao"], resultado["recibo"]
        serializado = normalizar(json.dumps(resultado, ensure_ascii=False))
        return _texto(decisao.get("pode_executar") is False
                      and "codigo canonico" in serializado
                      and recibo["lane"] == conservadora,
                      f"decidido={decisao.get('decidido')!r} lane={recibo['lane']!r}")

    itens.checar("C7: acao SEM codigo canonico continua abstendo mesmo com o mapa declarado "
                 "(fail-closed do D07 intacto)", _d07_independente)

    # ---- C8 politica sem mapa nao ganha lane por omissao -------------------
    def _sem_mapa_nada_executa():
        copia = copy.deepcopy(dado)
        copia.pop("lane_por_codigo_de_acao", None)
        caminho = _politica_temporaria(raiz_temporaria, copia)
        politica_copia = modulo.carregar_politica(caminho)
        # Textos BENIGNOS de proposito: a prova e sobre a lane por omissao. Texto com
        # palavra de risco aciona guardrail (Security) e mediria a camada de cima.
        benignos = ("ajuste de texto no rodapé do relatório",
                    "consulta interna de leitura no board, apenas leitura")
        resultados = [modulo.decidir(tarefa(texto), politica=politica_copia) for texto in benignos]
        return _texto(all(r["decisao"]["decidido"] == "abster_e_escalar"
                          and r["recibo"]["lane"] == conservadora for r in resultados),
                      f"decisoes={[r['decisao']['decidido'] for r in resultados]}")

    itens.checar("C8: politica sem `lane_por_codigo_de_acao` nao ganha lane por omissao "
                 "(toda decisao abstem na conservadora)", _sem_mapa_nada_executa)


# ---------------------------------------------------------------------------
# Composicao
# ---------------------------------------------------------------------------
def verificar(yaml_txt: str, doc_txt: str, base, modulo) -> Itens:
    itens = Itens()
    try:
        dado = yaml.safe_load(yaml_txt)
    except Exception as erro:  # noqa: BLE001
        itens.add("v1.2 YAML valido", False, f"nao parseia: {erro}")
        return itens
    itens.add("v1.2 YAML valido", isinstance(dado, dict),
              f"{len(dado)} chaves de topo" if isinstance(dado, dict) else "raiz nao e mapa")
    if not isinstance(dado, dict):
        return itens
    partes = (
        ("PARTE 1 (bateria da v1.0)", lambda: itens_da_bateria_da_v1_0(itens, yaml_txt, doc_txt, base)),
        ("PARTE 2 (bateria da v1.1)", lambda: itens_da_bateria_da_v1_1(itens, base, modulo)),
        ("PARTE 3 (contrato da v1.2)", lambda: itens_do_contrato(itens, dado, yaml_txt, doc_txt, modulo)),
        ("PARTE 4 (comportamento do roteador)", lambda: itens_de_comportamento(itens, dado, modulo)),
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

    def sem_codigo(d):
        d["lane_por_codigo_de_acao"].pop("ajuste_de_texto", None)
        return d

    def lane_inexistente(d):
        d["lane_por_codigo_de_acao"]["ajuste_de_texto"] = "turbo"
        return d

    def sem_mapa(d):
        d.pop("lane_por_codigo_de_acao", None)
        return d

    def mapa_barato(d):
        d["lane_por_codigo_de_acao"] = {c: "small" for c in d["lane_por_codigo_de_acao"]}
        return d

    def estado_falso(d):
        d["estado"] = "em-vigor"
        d["homologacao"] = {"estado": "pendente", "registrada_em": None,
                            "registrada_por": None,
                            "registro": "docs/operations/registro-de-aprovacoes.md"}
        d["congelada_em"] = "2026-09-30"
        return d

    mut_yaml("YAML: um codigo comum sai do mapa (cobre a lacuna)", sem_codigo)
    mut_yaml("YAML: mapa aponta lane inexistente", lane_inexistente)
    mut_yaml("YAML: mapa removido", sem_mapa)
    mut_yaml("YAML: mapa barato (small para todos)", mapa_barato)
    mut_yaml("YAML: estado `em-vigor` com homologacao ainda PENDENTE (par incoerente)", estado_falso)
    mut_doc("DOC: tabela do mapa discorda do YAML",
            lambda t: t.replace("| `ajuste_de_texto` | `high` |", "| `ajuste_de_texto` | `medium` |"))
    mut_roteador("ROTEADOR: classificador volta a ser chamado no caminho de decisao",
                 lambda c: c.replace(
                     '    if classificacao.get("lane_proposta") in (None, ""):',
                     '    classificar_card({"titulo": str(tarefa.get("acao") or ""),'
                     ' "descricao": str(tarefa.get("descricao") or "")}, politica)\n'
                     '    if classificacao.get("lane_proposta") in (None, ""):', 1))
    mut_roteador("ROTEADOR: o TEXTO do card decide a lane (volta o casamento de palavras)",
                 lambda c: c.replace(
                     '        lane_final = classificacao["lane_proposta"]',
                     '        lane_final = ("critical" if "produ" in str(tarefa.get("descricao") or "").lower()'
                     ' or "ajuste" in str(tarefa.get("descricao") or "").lower()'
                     ' else classificacao["lane_proposta"])', 1))

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
    faltando = [p for p in (YAML_V12, DOC_V12, YAML_V11, DOC_V11, YAML_V10, DOC_V10, ROTEADOR)
                if not p.is_file()]
    if faltando:
        print("FALHOU arquivo ausente: " + ", ".join(str(p) for p in faltando))
        return 1

    base = carregar_modulo(VERIFICADOR_V10, "verificador_v10_para_v12")
    modulo = carregar_modulo(ROTEADOR, "roteador_da_suite_v12")

    print("=" * 72)
    print("VERIFICADOR DA JEV DECISION POLICY v1.2 (rascunho nao homologado)")
    print(f"  politica:  {YAML_V12.relative_to(RAIZ)}")
    print(f"  documento: {DOC_V12.relative_to(RAIZ)}")
    print(f"  roteador:  {modulo.ROUTER_VERSION} "
          f"(suporta {sorted(modulo.VERSOES_DE_POLITICA_SUPORTADAS)})")
    print(f"  em vigor:  {YAML_V11.relative_to(RAIZ)} (portao fechado ate a homologacao)")
    print("=" * 72)

    yaml_txt = YAML_V12.read_text(encoding="utf-8")
    doc_txt = DOC_V12.read_text(encoding="utf-8")
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
