#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Roteador do JEV (System-1 Decision Layer) integrado ao Hermes Dev Harness.

Card TRE-W0-E04-T02. Este modulo aplica a politica `hermes/jev/policy_v1.yaml`
na precedencia declarada no proprio arquivo:

    Security -> Human Approval -> prioridade/dependencias -> JEV -> LLM

Nenhuma camada abaixo contraria a de cima. As regras abaixo sao criterio de
aceitacao e estao provadas em `scripts/verificar_jev_router.py`:

  * nenhum limiar, lane, perfil ou lista de acao proibida e literal no codigo:
    tudo e lido do YAML em tempo de execucao (prova: trocar o limiar no YAML muda
    a decisao do roteador);
  * guardrails deterministicos rodam ANTES de qualquer classificador e sao
    fail-closed (duvida na avaliacao = BLOCK, nunca prosseguir);
  * confianca abaixo do limiar de abstencao gera abstencao e escalacao, nunca
    execucao;
  * as acoes de `nunca_decidido_por_maquina` nunca passam por classificador;
  * politica ausente, ilegivel, invalida ou de versao desconhecida entra em modo
    degradado (lane conservadora + `degraded_mode`) e NUNCA executa em silencio;
  * o recibo tem exatamente os campos de `recibo.campos` e nunca carrega segredo;
  * Sales AI (o papel que a politica proibe de deploy) nunca recebe credencial de
    deploy nem tarefa que exija alterar codigo/DDL.

Uso pela linha de comando (le um card do board ou uma entrada sintetica):

    python3 hermes/jev/routing/router.py --card t_4f20bd10
    python3 hermes/jev/routing/router.py --json '{"card_id":"t_x","acao":"ajuste_de_texto","lane_proposta":"small","confianca":0.9}'
    python3 hermes/jev/routing/router.py --json-file entrada.json --saida recibo.json

Codigos de saida da CLI: 0 = execucao liberada (PASS), 2 = escalacao (inclui modo
degradado), 3 = bloqueio, 1 = erro de entrada.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import site
import sqlite3
import sys
import unicodedata

# ---------------------------------------------------------------------------
# PyYAML: mesma descoberta de ambiente usada em scripts/verificar_jev_policy.py.
# Se o interpretador em uso nao tiver PyYAML, procura nos ambientes conhecidos em
# vez de obrigar quem chama a lembrar qual python usar.
# ---------------------------------------------------------------------------
def _descobrir_raiz(inicio) -> pathlib.Path:
    """Raiz do repo: o primeiro ancestral que tenha `hermes/jev` (ou o diretorio do arquivo)."""
    atual = pathlib.Path(inicio).resolve()
    for ancestral in [atual.parent if atual.is_file() else atual] + list(atual.parents):
        if (ancestral / "hermes" / "jev").is_dir():
            return ancestral
    return atual.parent


_RAIZ_DO_REPO = _descobrir_raiz(__file__)
try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - depende do ambiente
    for _candidato in ("/opt/hermes/.venv/lib/python3.13/site-packages",
                       "/opt/data/.venv/lib/python3.13/site-packages",
                       str(_RAIZ_DO_REPO / ".venv/lib/python3.13/site-packages")):
        if pathlib.Path(_candidato).is_dir():
            site.addsitedir(_candidato)
    try:
        import yaml
    except ModuleNotFoundError as _erro:  # pragma: no cover
        raise RuntimeError(
            "PyYAML nao encontrado. Rode com o python do ambiente Hermes:\n"
            "  /opt/hermes/.venv/bin/python hermes/jev/routing/router.py ...") from _erro


# ---------------------------------------------------------------------------
# Constantes do ROTEADOR (nao sao parametros de politica).
#
# ATENCAO: limiar, lane, perfil e lista de acao proibida NAO ficam aqui. Vem
# sempre do YAML. O que existe abaixo e: identidade do roteador, a declaracao de
# compatibilidade de schema (quais versoes de politica este codigo sabe
# executar), o nome do campo que identifica a politica e a rede de seguranca para
# o caso em que NENHUMA politica pode ser lida.
# ---------------------------------------------------------------------------
ROUTER_VERSION = "jev-router-v1.0"

# Declaracao de compatibilidade: o roteador so executa politicas cujo schema ele
# conhece. Versao fora deste conjunto = recusa (nao ha "tentar mesmo assim").
VERSOES_DE_POLITICA_SUPORTADAS = frozenset({"jev-policy-v1.0"})

# Ultimo recurso, usado SOMENTE quando o arquivo de politica nao pode ser lido
# (ausente/ilegivel): sem politica nao ha de onde ler a lane conservadora. A
# politica, quando legivel, sempre manda (secao `fallback`).
LANE_DEGRADADA_PADRAO = "high"

# Vocabulario de resultado (decisoes_depois_do_ciclo). O roteador so roda
# politica cujo texto declare esse vocabulario.
OUTCOME_EXECUTAR = "PASS"
OUTCOME_ESCALAR = "ESCALATE"
OUTCOME_BLOQUEAR = "BLOCK"

# Espelho do contrato de recibo do roteador. E o que o roteador SABE preencher, e
# ele so e usado quando nenhuma politica pode ser lida (modo degradado). Com
# politica carregada, os campos vem SEMPRE de `recibo.campos` do YAML, e o
# roteador RECUSA gravar recibo fora desse contrato. A suite prova que este
# espelho e igual ao declarado no YAML (divergencia = falha).
CAMPOS_DO_RECIBO_PADRAO = (
    "decision_id",
    "card_id",
    "task_hash",
    "lane",
    "model_profile",
    "selected_model",
    "effort",
    "confidence",
    "policy_version",
    "router_version",
    "timestamp",
    "override",
    "outcome",
)

# Parametros do ESTIMADOR DE CONFIANCA do classificador de card. Nao sao limiares
# de politica (esses vem do YAML e sao comparados em `aplicar_limiares`).
CONFIANCA_BASE = 0.50
CONFIANCA_POR_EVIDENCIA = 0.12
CONFIANCA_MAXIMA = 0.99

# Similaridade minima de texto para casar uma acao com uma entrada das politicas
# de papel (isso e casamento de texto, nao limiar de decisao).
LIMIAR_CAPACIDADE = 0.5

# Sinais de guardrail que o roteador sabe avaliar. Sinal desconhecido = duvida =
# BLOCK (fail-closed), nunca "ignora o que nao entende".
SINAIS_CONHECIDOS = frozenset({"segredo_no_payload", "empresa_do_not_contact"})

# Cada guardrail implementado exige que a politica DECLARE a regra correspondente.
# Se a politica deixar de declarar uma dessas regras, ela nao e a politica que
# este roteador implementa: recusa (modo degradado), nao execucao.
GUARDRAILS_EXIGIDOS = {
    "segredo": ("segredo",),
    "do_not_contact": ("do_not_contact", "opt_out"),
    "ddl": ("DDL",),
    "credencial_de_deploy": ("credencial de deploy",),
    "fail_closed": ("fail-closed",),
}

# Detectores de credencial. Nao ha valor de segredo aqui: sao formatos.
PADROES_DE_SEGREDO = (
    ("chave de API (prefixo sk-)", re.compile(r"sk-[A-Za-z0-9]{8,}")),
    ("token de app GitHub (prefixo ghp_)", re.compile(r"ghp_[A-Za-z0-9]{8,}")),
    ("chave AWS (prefixo AKIA)", re.compile(r"AKIA[0-9A-Z]{12,}")),
    ("token de bot Slack (prefixo xox)", re.compile(r"xox[baprs]-[A-Za-z0-9-]{6,}")),
    ("chave privada em bloco PEM", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credencial embutida em URL", re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]{6,}@")),
    ("atribuicao de variavel de credencial", re.compile(
        r"(?i)\b(pass(word)?|senha|token|api[_-]?key|secret|credencial)\b\s*[:=]\s*\S{6,}")),
)
NOMES_DE_VARIAVEL_DE_SEGREDO = re.compile(r"(?i)(TOKEN|SECRET|PASSWORD|SENHA|CREDENTIAL|API_?KEY|_KEY$)")

# ---------------------------------------------------------------------------
# Caminhos padrao (identidade de arquivo, nao parametro de politica)
# ---------------------------------------------------------------------------
CAMINHO_POLITICA_PADRAO = _RAIZ_DO_REPO / "hermes/jev/policy_v1.yaml"
DIRETORIO_POLITICAS_DE_PAPEL = _RAIZ_DO_REPO / "hermes/policies"
DIRETORIO_BOARDS_PADRAO = pathlib.Path(os.environ.get("JEV_BOARDS_DIR", "/opt/data/kanban/boards"))
BOARD_PADRAO = os.environ.get("JEV_BOARD", "transformativa-revenue-engine")


class PoliticaInvalida(Exception):
    """Politica ausente, ilegivel, invalida ou de versao desconhecida."""

    def __init__(self, motivos):
        if isinstance(motivos, str):
            motivos = [motivos]
        self.motivos = list(motivos)
        super().__init__("; ".join(self.motivos))


class ReciboInvalido(Exception):
    """O recibo nao fechou com os campos da politica ou carrega segredo."""


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------
_VAZIAS = frozenset({"para", "com", "sem", "que", "uma", "dos", "das", "por",
                     "nos", "nas", "aos", "como", "mais", "sao"})


def _normalizar(texto) -> str:
    """Maiuscula/minuscula, acentos e pontuacao nao contam para casamento."""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9]+", " ", t.lower())
    return " ".join(t.split())


def _tokens(texto) -> tuple:
    return tuple(p for p in _normalizar(texto).split() if len(p) >= 3 and p not in _VAZIAS)


def _prefixo_comum(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def _token_casa(a: str, b: str) -> bool:
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and _prefixo_comum(a, b) >= 4


def _sobreposicao(alvo, fonte):
    """(quantos tokens de `alvo` aparecem em `fonte`, fracao de `alvo` casada)."""
    fonte = tuple(fonte)
    casados = sum(1 for t in alvo if any(_token_casa(t, f) for f in fonte))
    fracao = casados / len(alvo) if alvo else 0.0
    return casados, fracao


def _entradas_que_casam(acao: str, entradas) -> list:
    """Entradas (frases) da politica que casam com a acao, da mais parecida a menos.

    Casar = pelo menos um token em comum E (metade dos tokens da acao casados OU
    dois tokens casados). Entrada de politica e prosa, entao o casamento e por
    tokens com tolerancia de prefixo, nunca por igualdade de frase.
    """
    alvo = _tokens(acao)
    if not alvo:
        return []
    pontuadas = []
    for entrada in entradas or ():
        casados, fracao = _sobreposicao(alvo, _tokens(entrada))
        if casados >= 1 and (fracao >= LIMIAR_CAPACIDADE or casados >= 2):
            pontuadas.append((fracao, casados, str(entrada)))
    pontuadas.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [e for _, _, e in pontuadas]


# ---------------------------------------------------------------------------
# Leitura da politica
# ---------------------------------------------------------------------------
def _exigir(condicao: bool, motivos: list, mensagem: str) -> None:
    if not condicao:
        motivos.append(mensagem)


def _lane_conservadora(politica: dict, motivos: list) -> str:
    """Descobre a lane conservadora da politica, sem literal no codigo.

    Fontes, nesta ordem: `fallback.acao` ("seguir pela lane conservadora
    configurada: <lane>"), `limiares.lane_conservadora` e o parentetico de
    `limiares.empate` ("lane mais conservadora (high) + ..."). As duas fontes
    disponiveis na v1 tem de concordar; discordancia = politica inconsistente.
    """
    lanes = list((politica.get("lanes") or {}).keys())
    candidatos = []

    for item in (politica.get("fallback") or {}).get("acao") or []:
        m = re.search(r"lane conservadora configurada:\s*([A-Za-z0-9_-]+)", str(item))
        if m:
            candidatos.append(("fallback.acao", m.group(1)))

    limiares = politica.get("limiares") or {}
    if limiares.get("lane_conservadora"):
        candidatos.append(("limiares.lane_conservadora", str(limiares["lane_conservadora"])))
    m = re.search(r"\(\s*([A-Za-z0-9_-]+)\s*\)", str(limiares.get("empate", "")))
    if m:
        candidatos.append(("limiares.empate", m.group(1)))

    validos = [(origem, lane) for origem, lane in candidatos if lane in lanes]
    if not validos:
        motivos.append("nao foi possivel resolver a lane conservadora declarada "
                       f"(fontes lidas: {candidatos or 'nenhuma'}; lanes={lanes})")
        return ""
    declaradas = {lane for _, lane in validos}
    if len(declaradas) > 1:
        motivos.append(f"politica inconsistente: lane conservadora declarada de formas "
                       f"divergentes {sorted(declaradas)} ({validos})")
        return ""
    return validos[0][1]


def _papeis_do_diretorio(diretorio=None) -> dict:
    """Le as politicas de papel (`hermes/policies/*.yaml`) e indexa por `role:`."""
    diretorio = pathlib.Path(diretorio or DIRETORIO_POLITICAS_DE_PAPEL)
    papeis = {}
    if not diretorio.is_dir():
        return papeis
    for arquivo in sorted(diretorio.glob("*.yaml")):
        try:
            dado = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(dado, dict) and dado.get("role"):
            dado["_arquivo"] = str(arquivo)
            papeis[str(dado["role"])] = dado
    return papeis


def _familia_da_credencial(entrada) -> str:
    """Familia de credencial: 'TRE_PG_* (dev/homolog)' -> 'TRE_PG'. Prosa -> ''."""
    texto = re.sub(r"\(.*?\)", " ", str(entrada)).strip()
    if not texto:
        return ""
    primeiro = texto.split()[0].rstrip("*").strip("_")
    return _normalizar(primeiro).replace(" ", "_").upper()


def _inconsistencias_dos_papeis(papeis: dict) -> list:
    """Invariantes da matriz Dev x Sales que o roteador exige antes de rotear.

    Sao lidos das proprias politicas de papel: nenhuma lista de credencial e
    literal aqui. Regra: uma familia de credencial nao pode estar, ao mesmo tempo,
    entre as permitidas e as proibidas do MESMO papel.
    """
    problemas = []
    if not papeis:
        problemas.append("politicas de papel indisponiveis (hermes/policies/*.yaml)")
        return problemas
    for papel, dado in papeis.items():
        permitidas = {_familia_da_credencial(x) for x in dado.get("credenciais_permitidas") or []}
        proibidas = {_familia_da_credencial(x) for x in dado.get("credenciais_proibidas") or []}
        for familia in sorted((permitidas & proibidas) - {""}):
            problemas.append(
                f"papel {papel}: credencial {familia} declarada nas permitidas e nas proibidas")
        if not (dado.get("nao_pode") or []) and not (dado.get("pode") or []):
            problemas.append(f"papel {papel} nao declara pode/nao_pode")
    return problemas


def carregar_politica(caminho=None, diretorio_de_papeis=None) -> dict:
    """Le e valida a politica do JEV. Levanta PoliticaInvalida se nao servir.

    O dicionario devolvido e o proprio YAML mais as chaves derivadas (prefixo
    `_`): caminho, texto bruto, ordem das lanes, lane conservadora e politicas de
    papel. Quem consome usa essas derivadas; nada e recalculado com literal.
    """
    caminho = pathlib.Path(caminho or CAMINHO_POLITICA_PADRAO)
    if not caminho.is_file():
        raise PoliticaInvalida(f"politica ausente: {caminho}")
    try:
        texto = caminho.read_text(encoding="utf-8")
    except OSError as erro:
        raise PoliticaInvalida(f"politica ilegivel ({caminho}): {erro}")

    motivos = []
    try:
        dado = yaml.safe_load(texto)
    except Exception as erro:
        raise PoliticaInvalida(f"politica invalida (nao parseia): {erro}")
    if not isinstance(dado, dict):
        raise PoliticaInvalida("politica invalida: raiz nao e um mapa YAML")

    # --- versao conhecida: sem isso o roteador nao inicia -------------------
    versao = dado.get("versao")
    # ANCORA:VERSAO_DE_POLITICA
    if not versao or str(versao) not in VERSOES_DE_POLITICA_SUPORTADAS:
        motivos.append(
            f"versao de politica desconhecida: {versao!r} "
            f"(suportadas: {sorted(VERSOES_DE_POLITICA_SUPORTADAS)})")
        raise PoliticaInvalida(motivos)

    # --- estrutura minima que o roteador precisa ----------------------------
    for chave in ("limiares", "lanes", "perfis_modelo", "precedencia",
                  "nunca_decidido_por_maquina", "guardrails", "fallback", "recibo"):
        _exigir(isinstance(dado.get(chave), (dict, list)) and bool(dado.get(chave)),
                motivos, f"politica sem a secao obrigatoria {chave!r}")

    lanes = dado.get("lanes") or {}
    perfis = dado.get("perfis_modelo") or {}
    for nome, cfg in (lanes.items() if isinstance(lanes, dict) else ()):
        if not isinstance(cfg, dict) or not cfg.get("perfil"):
            motivos.append(f"lane {nome!r} sem perfil declarado")
            continue
        if cfg.get("perfil") not in perfis:
            motivos.append(f"lane {nome!r} aponta para perfil inexistente {cfg.get('perfil')!r}")

    campos_recibo = (dado.get("recibo") or {}).get("campos") or []
    if len(campos_recibo) != len(set(campos_recibo)):
        motivos.append("recibo.campos tem repeticao")
    # O recibo tem de fechar com o contrato que o roteador sabe preencher: campo
    # declarado a mais (que o roteador nao preenche) ou a menos (que ele preencheria
    # em branco) torna o recibo inauditavel.
    faltam_no_contrato = [c for c in CAMPOS_DO_RECIBO_PADRAO if c not in campos_recibo]
    fora_do_contrato = [c for c in campos_recibo if c not in CAMPOS_DO_RECIBO_PADRAO]
    if faltam_no_contrato or fora_do_contrato:
        motivos.append(
            f"recibo.campos fora do contrato do roteador: faltando={faltam_no_contrato} "
            f"declarados a mais={fora_do_contrato}")

    for guardrail in dado.get("guardrails") or []:
        if not isinstance(guardrail, dict) or not guardrail.get("regra") or not guardrail.get("verifica"):
            motivos.append(f"guardrail mal declarado: {guardrail!r}")

    texto_guardrails = _normalizar(json.dumps(dado.get("guardrails") or [], ensure_ascii=False))
    for nome_guardrail, termos in GUARDRAILS_EXIGIDOS.items():
        declarado = any(_normalizar(t) in texto_guardrails for t in termos)
        if not declarado:
            motivos.append(f"politica nao declara o guardrail {nome_guardrail!r} "
                           f"(esperado um dos termos {list(termos)})")

    # Limiares coerentes: faixa invertida nao pode virar decisao. (Os valores vem
    # do YAML; aqui so se confere que a politica nao esta se contradizendo.)
    limiares_declarados = dado.get("limiares") or {}
    try:
        coerente = (float(limiares_declarados["aceitar"]) > float(limiares_declarados["conservador"])
                    >= float(limiares_declarados["abster"]))
    except Exception:
        coerente = False
    _exigir(coerente, motivos,
            f"limiares incoerentes ou ausentes (aceitar > conservador >= abster): {limiares_declarados}")

    prec = dado.get("precedencia") or []
    if "llm" not in [str(x) for x in prec] or "security" not in [str(x) for x in prec]:
        motivos.append(f"precedencia sem security/llm declarados: {prec}")

    # O roteador so emite o vocabulario de resultado que a politica declara.
    for valor in (OUTCOME_EXECUTAR, OUTCOME_ESCALAR, OUTCOME_BLOQUEAR):
        if valor not in texto:
            motivos.append(f"politica nao declara o resultado {valor!r}")

    lane_conservadora = _lane_conservadora(dado, motivos)

    papeis = _papeis_do_diretorio(diretorio_de_papeis)
    # A matriz Dev x Sales (docs/architecture/hermes-dev-x-sales.md) faz parte da
    # politica: se ela esta ausente ou se contradiz, o roteador nao executa nada
    # (fail-closed), porque nao consegue provar que nenhum papel ganhou credencial
    # de deploy.
    problemas_papeis = _inconsistencias_dos_papeis(papeis)
    motivos.extend(problemas_papeis)

    if motivos:
        raise PoliticaInvalida(motivos)

    dado["_caminho"] = str(caminho)
    dado["_texto"] = texto
    dado["_ordem_lanes"] = list(lanes.keys())   # declaradas da menos para a mais conservadora
    dado["_lane_conservadora"] = lane_conservadora
    dado["_papeis"] = papeis
    dado["_avisos_papeis"] = problemas_papeis
    return dado


def carregar_politicas_de_papel(diretorio=None) -> dict:
    """Le as politicas de papel e valida os invariantes da matriz Dev x Sales."""
    papeis = _papeis_do_diretorio(diretorio)
    problemas = _inconsistencias_dos_papeis(papeis)
    if problemas:
        raise PoliticaInvalida(problemas)
    return papeis


# ---------------------------------------------------------------------------
# Derivados da politica (tudo lido do YAML)
# ---------------------------------------------------------------------------
def limiares(politica: dict) -> dict:
    return dict(politica.get("limiares") or {})


def ordem_lanes(politica: dict) -> list:
    return list(politica.get("_ordem_lanes") or (politica.get("lanes") or {}).keys())


def indice_da_lane(politica: dict, lane) -> int:
    ordem = ordem_lanes(politica)
    return ordem.index(lane) if lane in ordem else -1


def lane_mais_conservadora(politica: dict, *lanes) -> str:
    """Dentre as lanes informadas, a mais conservadora pela ordem declarada no YAML."""
    ordem = ordem_lanes(politica)
    validas = [l for l in lanes if l in ordem]
    if not validas:
        return ""
    return max(validas, key=ordem.index)


def config_da_lane(politica: dict, lane) -> dict:
    return dict((politica.get("lanes") or {}).get(lane) or {})


def perfil_da_lane(politica: dict, lane) -> str:
    return config_da_lane(politica, lane).get("perfil") or ""


def esforco_do_perfil(politica: dict, perfil) -> str:
    return ((politica.get("perfis_modelo") or {}).get(perfil) or {}).get("esforco") or ""


def _lane_exige_aprovacao_humana(politica: dict, lane) -> bool:
    revisao = config_da_lane(politica, lane).get("revisao") or ""
    return "aprovacao humana" in _normalizar(revisao)


def acoes_nunca_decididas_por_maquina(politica: dict) -> list:
    return [str(x) for x in politica.get("nunca_decidido_por_maquina") or []]


# ---------------------------------------------------------------------------
# Camada 1 — Security: guardrails deterministicos, ANTES de qualquer classificador
# ---------------------------------------------------------------------------
def _valores_de_segredo_do_ambiente() -> dict:
    """Valores de variavel de ambiente com cara de credencial (nunca impressos)."""
    achados = {}
    for nome, valor in os.environ.items():
        if NOMES_DE_VARIAVEL_DE_SEGREDO.search(nome) and valor and len(valor) >= 8:
            achados[nome] = valor
    return achados


def _segredo_no_payload(tarefa: dict) -> str:
    """Devolve o motivo (sem o valor do segredo) ou string vazia."""
    texto = json.dumps(tarefa, ensure_ascii=False, default=str)
    for nome, padrao in PADROES_DE_SEGREDO:
        if padrao.search(texto):
            return f"payload casa o formato de {nome}"
    for nome, valor in _valores_de_segredo_do_ambiente().items():
        if valor in texto:
            return f"payload carrega o valor da variavel de ambiente {nome}"
    return ""


def _guardrail(id_, regra, verifica, acionado, detalhe="") -> dict:
    return {"id": id_, "regra": regra, "verifica": verifica,
            "acionado": bool(acionado), "detalhe": detalhe}


def _guardrails_de_codigo(tarefa: dict) -> list:
    """Guardrails que nao dependem da politica: sempre rodam, sempre fail-closed."""
    guardrails = []
    if not isinstance(tarefa, dict):
        return [_guardrail("payload_valido", "tarefa precisa ser um mapa",
                           "validacao de contrato de entrada", True, "entrada nao e mapa")]
    if not isinstance(tarefa.get("sinais", {}), dict):
        return [_guardrail("payload_valido", "sinais da tarefa precisam ser um mapa",
                           "validacao de contrato de entrada", True, "sinais nao e mapa")]

    # ANCORA:GUARDRAIL_SEGREDO
    motivo_segredo = _segredo_no_payload(tarefa)
    guardrails.append(_guardrail(
        "segredo_sem_payload", "segredo nunca entra em prompt, log, recibo ou mensagem",
        "scan de segredo antes de montar o contexto",
        bool(motivo_segredo) or bool(tarefa.get("sinais", {}).get("segredo_no_payload")),
        motivo_segredo or ("sinal segredo_no_payload ligado"
                           if tarefa.get("sinais", {}).get("segredo_no_payload") else "")))

    desconhecidos = sorted(set((tarefa.get("sinais") or {}).keys()) - SINAIS_CONHECIDOS)
    guardrails.append(_guardrail(
        "fail_closed_sinal_desconhecido", "falha de guardrail bloqueia, nao libera",
        "fail-closed: duvida na avaliacao = BLOCK, nunca prosseguir",
        bool(desconhecidos),
        f"sinal nao reconhecido: {desconhecidos}" if desconhecidos else ""))
    return guardrails


def _entradas_com(papeis: dict, termo: str) -> list:
    termo = _normalizar(termo)
    achadas = []
    for dado in papeis.values():
        for secao in ("pode", "nao_pode"):
            for entrada in dado.get(secao) or []:
                if termo in _normalizar(entrada):
                    achadas.append(str(entrada))
    return achadas


def _e_acao_outbound(acao: str, politica: dict, papeis: dict) -> bool:
    entradas = list((politica.get("nunca_decidido_por_maquina") or []))
    for dado in papeis.values():
        entradas += list(dado.get("pode") or []) + list(dado.get("nao_pode") or [])
    for termo in ("contato", "contatar", "proposta", "e-mail", "mensagem", "linkedin", "whatsapp"):
        entradas += _entradas_com(papeis, termo)
    return bool(_entradas_que_casam(acao, entradas))


def _e_acao_ddl(acao: str, politica: dict, papeis: dict) -> bool:
    return bool(_entradas_que_casam(acao, _entradas_com(papeis, "DDL")
                                    + _entradas_com(papeis, "migration")))


def _guardrails_de_politica(tarefa: dict, politica: dict, papeis: dict) -> list:
    """Guardrails ligados a regra declarada na politica (leem o YAML)."""
    guardrails = []
    sinais = tarefa.get("sinais") or {}
    acao = str(tarefa.get("acao") or tarefa.get("titulo") or "")
    ambiente = _normalizar(tarefa.get("ambiente_alvo") or tarefa.get("ambiente") or "")

    do_not_contact = bool(sinais.get("empresa_do_not_contact"))
    guardrails.append(_guardrail(
        "do_not_contact", "empresa com do_not_contact ou opt_out nao e contatada",
        "consulta a base antes de qualquer outbound",
        do_not_contact and _e_acao_outbound(acao, politica, papeis),
        "empresa marcada como do_not_contact/opt_out em acao outbound"
        if do_not_contact else ""))

    if _e_acao_ddl(acao, politica, papeis):
        ddl_acionado = ambiente != "desenvolvimento" and ambiente != "dev"
        guardrails.append(_guardrail(
            "ddl_fora_de_producao", "DDL nao nasce em producao",
            "ambiente alvo declarado no card/migracao",
            ddl_acionado,
            f"acao de DDL com ambiente alvo {ambiente or 'nao declarado'}"))
    else:
        guardrails.append(_guardrail(
            "ddl_fora_de_producao", "DDL nao nasce em producao",
            "ambiente alvo declarado no card/migracao", False, ""))

    papel, motivo_papel = _papel_para_acao(tarefa, politica, papeis)
    guardrails.append(_guardrail(
        "papel_sem_credencial_de_deploy", "Sales AI nao tem credencial de deploy nem altera codigo",
        "matriz Dev x Sales (docs/architecture/hermes-dev-x-sales.md)",
        bool(motivo_papel), motivo_papel))
    return guardrails


def _credenciais_do_papel(papeis: dict, papel: str) -> list:
    return [str(x) for x in ((papeis.get(papel) or {}).get("credenciais_permitidas") or [])]


def _credencial_proibida_para(papeis: dict, papel: str, credencial: str) -> bool:
    proibidas = [str(x) for x in ((papeis.get(papel) or {}).get("credenciais_proibidas") or [])]
    alvo = _tokens(credencial)
    for proibida in proibidas:
        casados, fracao = _sobreposicao(alvo, _tokens(proibida))
        if casados and fracao >= LIMIAR_CAPACIDADE:
            return True
    return False


def _credencial_permitida_para(papeis: dict, papel: str, credencial: str) -> bool:
    alvo = _tokens(credencial)
    for permitida in _credenciais_do_papel(papeis, papel):
        casados, fracao = _sobreposicao(alvo, _tokens(permitida))
        if casados and fracao >= LIMIAR_CAPACIDADE:
            return True
    return False


def _papel_para_acao(tarefa: dict, politica: dict, papeis: dict):
    """(papel, motivo_de_bloqueio). Motivo vazio = papel liberado.

    Regra do guardrail: o papel nao pode executar acao que a propria politica dele
    proibe, nem receber credencial que a politica dele proibe. Sem papel declarado
    na tarefa, o roteador INFERE o papel executor e nao bloqueia por isso (sem
    papel nem credencial declarados nao ha o que vazar); com papel ou credencial
    declarados, a duvida bloqueia. Se nenhum papel pode fazer a acao e algum
    proibe, o roteador nao escolhe o proibido: bloqueia (fail-closed).
    """
    if not papeis:
        return None, "politicas de papel indisponiveis: nao da para provar a matriz Dev x Sales"
    acao = str(tarefa.get("acao") or tarefa.get("titulo") or "")
    pedido = tarefa.get("papel_solicitado")
    credencial = tarefa.get("credencial_solicitada")

    podem, proibem = [], []
    if acao:
        for papel, dado in papeis.items():
            permitido = _entradas_que_casam(acao, dado.get("pode") or [])
            proibido = _entradas_que_casam(acao, dado.get("nao_pode") or [])
            if proibido:
                proibem.append((papel, proibido))
            if permitido and not proibido:
                podem.append((papel, permitido))

    inferido = None
    if pedido:
        inferido = str(pedido)
    elif podem:
        podem.sort(key=lambda x: (-len(x[1]), x[0]))
        inferido = podem[0][0]
    elif proibem:
        proibem.sort(key=lambda x: (-len(x[1]), x[0]))
        inferido = proibem[0][0]

    papel = inferido
    if pedido and str(pedido) not in papeis:
        return papel, f"papel solicitado desconhecido: {pedido!r}"

    if not papel:
        return None, ""

    proibidas = _entradas_que_casam(acao, (papeis.get(papel) or {}).get("nao_pode") or [])
    if proibidas:
        return papel, f"papel {papel} nao pode: {proibidas[0]}"

    if pedido:
        permitidas = _entradas_que_casam(acao, (papeis.get(papel) or {}).get("pode") or [])
        if not permitidas:
            return papel, f"acao {acao!r} nao esta nas permissoes declaradas do papel {papel}"

    if credencial:
        if _credencial_proibida_para(papeis, papel, str(credencial)):
            return papel, (f"papel {papel} nao pode receber a credencial pedida "
                           f"(consta nas credenciais proibidas do proprio papel)")
        if not _credencial_permitida_para(papeis, papel, str(credencial)):
            return papel, (f"credencial pedida nao esta nas credenciais permitidas do papel {papel}")

    return papel, ""


# ---------------------------------------------------------------------------
# Camada 2 — Human Approval
# ---------------------------------------------------------------------------
def acoes_de_decisao_humana(politica: dict, papeis: dict) -> list:
    """Acoes que nunca passam por classificador: as do YAML + as de human-approval.yaml."""
    entradas = list(acoes_nunca_decididas_por_maquina(politica))
    for dado in papeis.values():
        if dado.get("_arquivo", "").endswith("human-approval.yaml"):
            entradas += [str(x) for x in dado.get("exige_aprovacao") or []]
            entradas += [str(x) for x in dado.get("nunca_automatico") or []]
    return entradas


def acao_de_decisao_humana(acao: str, politica: dict, papeis: dict) -> list:
    return _entradas_que_casam(str(acao or ""), acoes_de_decisao_humana(politica, papeis))


# ---------------------------------------------------------------------------
# Camada 3 — Prioridade e dependencias
# ---------------------------------------------------------------------------
def pendencia_de_dependencia(tarefa: dict) -> str:
    status = _normalizar(tarefa.get("status") or "").strip()
    pendentes = [str(x) for x in tarefa.get("dependencias_pendentes") or []]
    if status in ("blocked", "bloqueado"):
        return "card bloqueado no board: nao inicia"
    if status in ("triage", "triagem"):
        return "card em triage: nao inicia sem classificacao humana"
    if pendentes:
        return f"dependencias pendentes de conclusao: {pendentes}"
    return ""


# ---------------------------------------------------------------------------
# Camada 4 — JEV: classificacao e limiares
# ---------------------------------------------------------------------------
def classificar_tarefa(tarefa: dict) -> dict:
    """Proposta de lane/confianca do JEV. Validacao contra o YAML vem depois."""
    return {"lane_proposta": tarefa.get("lane_proposta") or tarefa.get("lane"),
            "confianca": tarefa.get("confianca"),
            "origem": "entrada explicita"}


def classificar_card(card: dict, politica: dict) -> dict:
    """Classificador deterministico de card: casa o texto com `lanes.*.exemplos`.

    Sem nenhuma evidencia o JEV nao chuta: devolve a confianca base, que fica
    abaixo do limiar de abstencao da politica e leva a abstencao/escalacao.
    """
    texto = " ".join(str(card.get(chave) or "") for chave in ("titulo", "title", "descricao", "body"))
    evidencias = []
    pontuacao = {}
    for lane in ordem_lanes(politica):
        cfg = config_da_lane(politica, lane)
        termos = []
        for campo in ("exemplos", "escopo"):
            termos += _tokens(cfg.get(campo) or "")
        casados = sorted({t for t in _tokens(texto) if any(_token_casa(t, x) for x in termos)})
        pontuacao[lane] = len(casados)
        if casados:
            evidencias.append(f"lane {lane}: {len(casados)} termo(s) do proprio YAML no card")

    melhor = max(pontuacao.values()) if pontuacao else 0
    candidatas = [l for l, p in pontuacao.items() if p == melhor and melhor > 0]
    if not candidatas:
        return {"lane_proposta": None, "confianca": CONFIANCA_BASE,
                "origem": "classificador de card", "evidencias": ["nenhuma evidencia no card"]}
    lane = lane_mais_conservadora(politica, *candidatas)
    confianca = min(CONFIANCA_MAXIMA, CONFIANCA_BASE + CONFIANCA_POR_EVIDENCIA * melhor)
    return {"lane_proposta": lane, "confianca": confianca,
            "origem": "classificador de card", "evidencias": evidencias}


def aplicar_limiares(politica: dict, lane_proposta, confianca):
    """(lane_final, motivo). lane_final None = abstencao (nunca executa).

    Os tres numeros vem do YAML e a faixa e fechada em baixo: a confianca igual ao
    limiar de abstencao ja entra na faixa conservadora; so abaixo dele abstem.
    """
    lim = limiares(politica)
    # ANCORA:LIMIARES_DO_YAML
    aceitar = lim.get("aceitar")
    conservador = lim.get("conservador")
    abster = lim.get("abster")
    if aceitar is None or conservador is None or abster is None:
        raise PoliticaInvalida("politica sem os tres limiares (aceitar/conservador/abster)")

    if confianca is None:
        return None, "confianca ausente: ausencia de resposta e abstinencia, nao permissao"

    # ANCORA:ABSTENCAO
    if confianca < abster:
        return None, (f"confianca {confianca} abaixo do limiar de abstencao {abster}: "
                      "abstem e escala, nunca chuta")

    ordem = ordem_lanes(politica)
    if lane_proposta not in ordem:
        return None, (f"lane proposta desconhecida ({lane_proposta!r}) para as lanes da politica "
                      f"{ordem}: abstem e escala")

    if confianca >= aceitar:
        return lane_proposta, f"confianca {confianca} >= limiar de aceite {aceitar}: aceita a lane"

    conservadora = lane_mais_conservadora(politica, lane_proposta, politica["_lane_conservadora"])
    return conservadora, (f"confianca {confianca} na faixa conservadora "
                          f"[{conservador}, {aceitar}): lane {conservadora}")


# ---------------------------------------------------------------------------
# Recibo
# ---------------------------------------------------------------------------
def campos_do_recibo(politica) -> list:
    """Campos do recibo, na ordem da politica.

    Ordem de preferencia: (1) a politica carregada; (2) o arquivo de politica
    padrao, apenas para ler o contrato do recibo (caso a politica esteja invalida
    mas legivel); (3) o espelho `CAMPOS_DO_RECIBO_PADRAO`, usado SOMENTE quando
    nenhuma politica pode ser lida (modo degradado). O espelho existe para que o
    modo degradado ainda grave o recibo completo: a suite prova que ele e igual ao
    declarado no YAML.
    """
    campos = [str(c) for c in ((politica or {}).get("recibo") or {}).get("campos") or []]
    if campos:
        return campos
    if CAMINHO_POLITICA_PADRAO.is_file():
        try:
            bruto = yaml.safe_load(CAMINHO_POLITICA_PADRAO.read_text(encoding="utf-8"))
            campos = [str(c) for c in ((bruto or {}).get("recibo") or {}).get("campos") or []]
        except Exception:
            campos = []
    if campos:
        return campos
    return list(CAMPOS_DO_RECIBO_PADRAO)


def _hash_da_tarefa(tarefa: dict) -> str:
    canonico = json.dumps(tarefa, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _id_da_decisao(card_id, task_hash, policy_version, timestamp, lane) -> str:
    bruto = f"{card_id}|{task_hash}|{policy_version}|{timestamp}|{lane}"
    return "dec-" + hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def _contem_segredo(valor) -> bool:
    texto = json.dumps(valor, ensure_ascii=False, default=str)
    if any(padrao.search(texto) for _, padrao in PADROES_DE_SEGREDO):
        return True
    return any(v in texto for v in _valores_de_segredo_do_ambiente().values())


def montar_recibo(politica, tarefa: dict, plano: dict, agora=None) -> dict:
    """Monta o recibo com EXATAMENTE os campos de `recibo.campos`. Sem segredo."""
    campos = campos_do_recibo(politica)
    if not campos:
        raise ReciboInvalido("politica nao declara recibo.campos: sem contrato de recibo, sem decisao")

    timestamp = (agora or _dt.datetime.now(_dt.timezone.utc)).replace(microsecond=0).isoformat()
    versao = politica.get("versao") if politica else None
    card_id = tarefa.get("card_id")
    task_hash = _hash_da_tarefa(tarefa)
    perfil = plano.get("perfil")
    valores = {
        "decision_id": _id_da_decisao(card_id, task_hash, versao, timestamp, plano.get("lane")),
        "card_id": card_id,
        "task_hash": task_hash,
        # ANCORA:RECIBO_CAMPOS
        "lane": plano.get("lane"),
        "model_profile": perfil or None,
        "selected_model": (plano.get("catalogo") or {}).get(perfil) if perfil else None,
        "effort": esforco_do_perfil(politica, perfil) if (politica and perfil) else None,
        "confidence": plano.get("confidence"),
        "policy_version": versao,
        "router_version": ROUTER_VERSION,
        "timestamp": timestamp,
        "override": tarefa.get("override") or None,
        "outcome": plano.get("outcome"),
    }
    faltando = [c for c in campos if c not in valores]
    sobrando = [c for c in valores if c not in campos]
    if faltando or sobrando:
        raise ReciboInvalido(
            f"recibo fora do contrato da politica: faltando={faltando} sobrando={sobrando}")
    recibo = {c: valores[c] for c in campos}

    # ANCORA:SEGREDO_NO_RECIBO
    if _contem_segredo(recibo):
        raise ReciboInvalido("recibo carregaria segredo: recusado (a regra e nunca)")
    return recibo


def escrever_recibo(recibo: dict, caminho) -> pathlib.Path:
    caminho = pathlib.Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(recibo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return caminho


# ---------------------------------------------------------------------------
# Decisao
# ---------------------------------------------------------------------------
def decidir(tarefa: dict, politica=None, motivo_politica=None, politicas_papel=None,
            catalogo=None, agora=None) -> dict:
    """Decide e devolve {'recibo': {...13 campos...}, 'decisao': {...}}.

    `politica=None` significa politica indisponivel: modo degradado, jamais
    execucao silenciosa. Usa apenas o que foi passado (nada e lido de novo aqui).
    """
    tarefa = dict(tarefa or {})
    papeis = politicas_papel if politicas_papel is not None else (
        politica.get("_papeis") if politica else {}) or {}
    guardrails = list(_guardrails_de_codigo(tarefa))

    plano = {
        "lane": None, "confidence": None, "outcome": OUTCOME_ESCALAR,
        "decidido": "abster_e_escalar", "perfil": None, "catalogo": dict(catalogo or {}),
        "motivos": [], "guardrails": guardrails, "degraded_mode": politica is None,
        "exige_revisao": False, "exige_escalacao": False, "exige_aprovacao_humana": False,
        "papel_executor": None, "evidencias": [], "demais": {},
    }

    # ---- Camada 1: Security -------------------------------------------------
    if politica is not None:
        guardrails.extend(_guardrails_de_politica(tarefa, politica, papeis))
    acionados = [g for g in guardrails if g["acionado"]]
    if acionados:
        plano["decidido"] = "bloquear"
        plano["outcome"] = OUTCOME_BLOQUEAR
        plano["motivos"] = [f"guardrail {g['id']}: {g['regra']}"
                            + (f" ({g['detalhe']})" if g["detalhe"] else "") for g in acionados]
        plano["lane"] = _lane_segura(politica, tarefa)
        return _fechar(politica, tarefa, plano, agora)

    # ---- Politica indisponivel: modo degradado ------------------------------
    # ANCORA:DEGRADADO
    if politica is None:
        plano["lane"] = _lane_degradada(politica)
        plano["confidence"] = None          # nunca inferir confianca
        plano["degraded_mode"] = True
        plano["exige_escalacao"] = True
        plano["decidido"] = "abster_e_escalar"
        plano["outcome"] = OUTCOME_ESCALAR
        plano["motivos"] = [motivo_politica or "politica indisponivel",
                            "modo degradado registrado no recibo (degraded_mode: true)",
                            "escalacao obrigatoria: sem politica nao ha execucao silenciosa"]
        return _fechar(politica, tarefa, plano, agora)

    if politica.get("_avisos_papeis"):
        plano["motivos"] += list(politica["_avisos_papeis"])

    # ---- Camada 2: Human Approval ------------------------------------------
    acao = str(tarefa.get("acao") or tarefa.get("titulo") or "")
    humano = acao_de_decisao_humana(acao, politica, papeis)
    # ANCORA:PRECEDENCIA_HUMANA
    if humano:
        plano["decidido"] = "bloquear"
        plano["outcome"] = OUTCOME_BLOQUEAR
        plano["exige_aprovacao_humana"] = True
        plano["exige_escalacao"] = True
        plano["lane"] = _lane_segura(politica, tarefa)
        plano["motivos"] = [f"acao de decisao humana ({humano[0]}): nunca decidida por maquina",
                            "encaminhar para Human Approval do Anderson"]
        return _fechar(politica, tarefa, plano, agora)

    # ---- Camada 3: prioridade e dependencias --------------------------------
    pendencia = pendencia_de_dependencia(tarefa)
    # ANCORA:DEPENDENCIAS
    if pendencia:
        plano["decidido"] = "aguardar_dependencia"
        plano["outcome"] = OUTCOME_BLOQUEAR
        plano["lane"] = _lane_segura(politica, tarefa)
        plano["motivos"] = [pendencia, "o JEV nao otimiza a fila por conta propria"]
        return _fechar(politica, tarefa, plano, agora)

    # ---- Camada 4: JEV ------------------------------------------------------
    classificacao = classificar_tarefa(tarefa)
    if classificacao.get("lane_proposta") in (None, "") and tarefa.get("descricao") is not None:
        classificacao = classificar_card(tarefa, politica)
    plano["evidencias"] = list(classificacao.get("evidencias") or [])
    plano["confidence"] = classificacao.get("confianca")
    plano["demais"] = {"origem_da_classificacao": classificacao.get("origem"),
                       "lane_proposta": classificacao.get("lane_proposta")}

    lane_final, motivo = aplicar_limiares(
        politica, classificacao.get("lane_proposta"), classificacao.get("confianca"))

    if lane_final is None:
        plano["decidido"] = "abster_e_escalar"
        plano["outcome"] = OUTCOME_ESCALAR
        plano["lane"] = _lane_degradada(politica)
        plano["exige_escalacao"] = True
        plano["motivos"] = [motivo, f"lane conservadora {plano['lane']} registrada no recibo"]
        return _fechar(politica, tarefa, plano, agora)

    # override humano: nunca rebaixa a lane decidida pela politica
    plano["lane"] = lane_final
    sobrescrita = tarefa.get("override")
    if isinstance(sobrescrita, dict) and sobrescrita.get("lane"):
        pretendida = sobrescrita.get("lane")
        ordem = ordem_lanes(politica)
        if pretendida not in ordem:
            plano["decidido"] = "bloquear"
            plano["outcome"] = OUTCOME_BLOQUEAR
            plano["motivos"] = [f"override aponta lane inexistente: {pretendida!r}"]
            return _fechar(politica, tarefa, plano, agora)
        if indice_da_lane(politica, pretendida) < indice_da_lane(politica, lane_final):
            plano["decidido"] = "bloquear"
            plano["outcome"] = OUTCOME_BLOQUEAR
            plano["motivos"] = [f"override tentou rebaixar a lane de {lane_final} para {pretendida}: "
                                "override so aumenta a conservacao"]
            return _fechar(politica, tarefa, plano, agora)
        plano["lane"] = pretendida
        plano["motivos"].append(f"override humano eleva a lane para {pretendida}"
                                f" (por {sobrescrita.get('por', 'nao declarado')})")

    perfil = perfil_da_lane(politica, plano["lane"])
    plano["perfil"] = perfil
    plano["exige_revisao"] = bool(config_da_lane(politica, plano["lane"]).get("revisao"))
    plano["exige_aprovacao_humana"] = _lane_exige_aprovacao_humana(politica, plano["lane"])

    if plano["exige_aprovacao_humana"]:
        plano["decidido"] = "escalar_para_aprovacao_humana"
        plano["outcome"] = OUTCOME_ESCALAR
        plano["exige_escalacao"] = True
        plano["motivos"].append(f"lane {plano['lane']} exige aprovacao humana registrada "
                                "antes de executar")
    else:
        plano["decidido"] = "executar"
        plano["outcome"] = OUTCOME_EXECUTAR
        plano["motivos"].append(motivo)
    return _fechar(politica, tarefa, plano, agora)


def _lane_degradada(politica) -> str:
    """Lane do modo degradado: a declarada na politica; se ilegivel, a de ultimo recurso."""
    if politica:
        lane = politica.get("_lane_conservadora")
        if lane:
            return lane
    return LANE_DEGRADADA_PADRAO


def _lane_segura(politica, tarefa):
    """Lane para registrar quando a decisao e bloqueio/abstencao."""
    if politica:
        return politica.get("_lane_conservadora") or LANE_DEGRADADA_PADRAO
    return LANE_DEGRADADA_PADRAO


def _fechar(politica, tarefa, plano, agora=None) -> dict:
    """Fecha a decisao: resolve o papel executor e monta o recibo."""
    papeis = (politica.get("_papeis") if politica else {}) or {}
    if papeis:
        papel, _motivo = _papel_para_acao(tarefa, politica or {}, papeis)
        plano["papel_executor"] = papel
        if papel:
            plano["demais"]["credenciais_permitidas_ao_papel"] = _credenciais_do_papel(papeis, papel)
    recibo = montar_recibo(politica, tarefa, plano, agora=agora)
    decisao = {
        "decidido": plano["decidido"],
        "outcome": plano["outcome"],
        "lane": plano["lane"],
        "lane_conservadora_da_politica": (politica or {}).get("_lane_conservadora"),
        "degraded_mode": bool(plano["degraded_mode"]),
        "pode_executar": plano["decidido"] == "executar",
        "exige_revisao": plano["exige_revisao"],
        "exige_escalacao": plano["exige_escalacao"],
        "exige_aprovacao_humana": plano["exige_aprovacao_humana"],
        "papel_executor": plano["papel_executor"],
        "policy_version": (politica or {}).get("versao"),
        "politica_lida_de": (politica or {}).get("_caminho") or motivo_ausente(politica),
        "guardrails_avaliados": plano["guardrails"],
        "guardrails_acionados": [g["id"] for g in plano["guardrails"] if g["acionado"]],
        "motivos": plano["motivos"],
        "evidencias": plano["evidencias"],
    }
    decisao.update(plano["demais"])
    return {"recibo": recibo, "decisao": decisao}


def motivo_ausente(politica) -> str:
    return "politica nao carregada" if politica is None else "nao declarado"


rotear = decidir  # nome alternativo, mesmo comportamento


# ---------------------------------------------------------------------------
# Integracao com o board do kanban
# ---------------------------------------------------------------------------
def caminho_do_board(slug=None, diretorio=None) -> pathlib.Path:
    base = pathlib.Path(diretorio or DIRETORIO_BOARDS_PADRAO)
    return base / str(slug or BOARD_PADRAO) / "kanban.db"


def _colunas(conexao, tabela: str) -> set:
    try:
        linhas = conexao.execute(f"PRAGMA table_info({tabela})").fetchall()
    except sqlite3.Error:
        return set()
    return {linha[1] for linha in linhas}


def carregar_card_do_board(card_id: str, slug=None, caminho_do_banco=None) -> dict:
    """Le o card (tabela `tasks`) e as dependencias pendentes (tabela `task_links`)."""
    caminho = pathlib.Path(caminho_do_banco or caminho_do_board(slug))
    if not caminho.is_file():
        raise PoliticaInvalida(f"banco do board nao encontrado: {caminho}")
    conexao = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    try:
        colunas = _colunas(conexao, "tasks")
        if not colunas:
            raise PoliticaInvalida(f"banco do board sem a tabela tasks: {caminho}")
        alvo = [c for c in ("id", "title", "body", "status", "assignee", "priority",
                            "block_kind", "branch_name", "project_id") if c in colunas]
        linha = conexao.execute(
            f"SELECT {', '.join(alvo)} FROM tasks WHERE id = ?", (card_id,)).fetchone()
        if linha is None:
            raise PoliticaInvalida(f"card {card_id!r} nao esta no board {caminho}")
        card = dict(zip(alvo, linha))

        pendentes = []
        if _colunas(conexao, "task_links"):
            pais = conexao.execute(
                "SELECT parent_id FROM task_links WHERE child_id = ?", (card_id,)).fetchall()
            for (parent_id,) in pais:
                status = conexao.execute(
                    "SELECT status FROM tasks WHERE id = ?", (parent_id,)).fetchone()
                if status and str(status[0]) != "done":
                    pendentes.append(parent_id)
    finally:
        conexao.close()

    return {
        "card_id": card.get("id"),
        "titulo": card.get("title") or "",
        "descricao": card.get("body") or "",
        "status": card.get("status") or "",
        "assignee": card.get("assignee"),
        "prioridade": card.get("priority"),
        "dependencias_pendentes": pendentes,
    }


def tarefa_a_partir_do_card(card: dict, politica: dict) -> dict:
    """Tarefa sintetica a partir do card: lane/confianca pelo classificador do JEV."""
    classificacao = classificar_card(card, politica)
    tarefa = dict(card)
    tarefa["acao"] = card.get("card_id") and card.get("titulo") or card.get("titulo")
    tarefa["lane_proposta"] = classificacao.get("lane_proposta")
    tarefa["confianca"] = classificacao.get("confianca")
    return tarefa


def tarefa_sintetica(card: dict, politica: dict) -> dict:
    return tarefa_a_partir_do_card(card, politica)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _carregar_entrada(args) -> dict:
    if args.json:
        return json.loads(args.json)
    if args.json_file:
        return json.loads(pathlib.Path(args.json_file).read_text(encoding="utf-8"))
    if args.stdin:
        return json.loads(sys.stdin.read())
    if args.card:
        card = carregar_card_do_board(args.card, slug=args.board, caminho_do_banco=args.board_db)
        politica = None
        try:
            politica = carregar_politica(args.politica)
        except PoliticaInvalida:
            politica = None
        if politica is not None:
            return tarefa_a_partir_do_card(card, politica)
        tarefa = dict(card)
        tarefa["acao"] = card.get("titulo")
        return tarefa
    raise PoliticaInvalida("informe --card, --json, --json-file ou --stdin")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Roteador do JEV (card TRE-W0-E04-T02): classifica antes da LLM e grava o recibo.")
    parser.add_argument("--card", help="id do card no board (tabela tasks)")
    parser.add_argument("--board", default=BOARD_PADRAO, help="slug do board")
    parser.add_argument("--board-db", help="caminho direto do kanban.db")
    parser.add_argument("--json", help="tarefa sintetica em JSON")
    parser.add_argument("--json-file", help="arquivo com a tarefa sintetica em JSON")
    parser.add_argument("--stdin", action="store_true", help="le a tarefa sintetica da entrada padrao")
    parser.add_argument("--politica", help="caminho da politica (padrao: hermes/jev/policy_v1.yaml)")
    parser.add_argument("--papeis", help="diretorio das politicas de papel (padrao: hermes/policies)")
    parser.add_argument("--catalogo", help="JSON com perfil->modelo (catalogo fica FORA da politica)")
    parser.add_argument("--saida", help="grava o recibo neste arquivo")
    args = parser.parse_args(argv)

    catalogo = json.loads(args.catalogo) if args.catalogo else None

    politica = None
    motivo = None
    try:
        politica = carregar_politica(args.politica, diretorio_de_papeis=args.papeis)
        if politica.get("_avisos_papeis"):
            print("AVISO politicas de papel: " + "; ".join(politica["_avisos_papeis"]),
                  file=sys.stderr)
    except PoliticaInvalida as erro:
        motivo = f"politica indisponivel/invalida: {erro}"

    try:
        tarefa = _carregar_entrada(args)
    except (PoliticaInvalida, json.JSONDecodeError, OSError) as erro:
        print(f"FALHOU entrada invalida: {erro}", file=sys.stderr)
        return 1

    papeis = None
    if politica is not None:
        papeis = politica.get("_papeis") or {}
    resultado = decidir(tarefa, politica=politica, motivo_politica=motivo,
                        politicas_papel=papeis, catalogo=catalogo)

    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    if args.saida:
        escrever_recibo(resultado["recibo"], args.saida)
        print(f"recibo gravado em {args.saida}", file=sys.stderr)

    decidido = resultado["decisao"]["decidido"]
    if decidido == "executar":
        return 0
    if resultado["decisao"]["outcome"] == OUTCOME_BLOQUEAR:
        return 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
