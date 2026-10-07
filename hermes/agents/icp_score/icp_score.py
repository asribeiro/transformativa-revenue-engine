#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agente ICP Score v1 — fit estrutural da organizacao com o cliente desejado (TRE-W5-E01-T01).

O que este agente FAZ (e so' isto): le a organizacao NO BANCO (fonte da verdade), calcula o
score `ICP` (0-100) segundo o modelo declarado em `agente-icp-score-v1.json` e grava o
resultado em `sales_intelligence.scores` com `score_type = 'ICP'`, `score_version` do modelo,
`inputs` (o que foi lido) e `explanation` (como o numero saiu).

O que ele NAO faz, por desenho (declarado em `agente-icp-score-v1.json` -> lacunas):
  - nao calcula tier, Priority Score nem Next Best Action (W5-E05/E06/E07);
  - nao emite evento de outbox: `COMPANY_QUALIFIED` e decisao de tiering/priority; o caminho
    ate Odoo e W3;
  - nao chama LLM (`model`, `tokens_*` e `estimated_cost` ficam NULL), nao faz HTTP, nao le
    fonte externa: a fonte da entrada escolhe o SUJEITO, nao o dado;
  - nao mescla, nao atualiza organizacao, nao toca Odoo/Titan/n8n/host;
  - nao escreve em producao (ADR-005).

Modelo 1.1 (HOMOLOGADO pelo dono em 07/10/2026 — docs/business/icp-transformativa-v1.md):

    ICP = 0,30 * segmento + 0,25 * porte + 0,10 * geografia + 0,15 * modelo_b2b + 0,20 * intencao
    (pesos com virgula aqui de proposito: o valor EXECUTAVEL mora so' no contrato do agente —
    a suite reprova se um peso aparecer em forma de codigo no corpo deste arquivo)

  - segmento: casamento do industry_code/industry_name com os cinco ICPs do Data Contract
    (`scores.icp_context.icps`), por vocabulario declarado no contrato do agente;
  - porte: `employee_band` (ou derivado de `employee_count`) contra a faixa 70-1000 e o sweet
    spot 150-700 (`scores.icp_context`);
  - geografia: `state` contra os estados declarados no corte (criterio 2 da definicao: SP);
  - modelo_b2b: `business_model` (B2B / B2B2C / B2C);
  - intencao: os tres sinais declarados, lidos de `sales_intelligence.signals`, cada um valendo
    so' com FONTE e DATA (`source_type`/`source_url` e `event_date`) — criterios 3, 4 e 5.
  Criterio sem dado reconhecido pontua 0 **e registra o motivo nomeando o criterio**: ausencia
  nao vira fit — quem mede dado faltante e o Data Quality Score (W5-E04).

Cortes da definicao do dono: o porte minimo de usuarios/funcionarios declarados (o limiar
mora no contrato, em `cortes.porte_minimo.minimo`) e `state = SP` sao
PORTAO, nao peso. Quem nao passa nao entra na campanha: `score_value = 0.00`, `elegivel =
false` e o motivo do corte vai para a explicacao. O agente NUNCA preenche fonte nem data de
sinal com valor sintetico: sinal sem fonte declarada simplesmente nao da credito de intencao.

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"): a chave e
`icp:score:<org>:<modelo>:<fingerprint dos campos que mudam o score>`, gravada em
`sync_events.idempotency_key` (UNIQUE). Mesmos dados => replay (nada novo); dado alterado =>
score novo, com o anterior preservado — score e historico, nao mutavel (Data Contract §8/§10).

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/agents/icp_score/icp_score.py --planejar --fonte organizacoes.jsonl
  python3 hermes/agents/icp_score/icp_score.py --ambiente dev --fonte organizacoes.jsonl \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \\
      --relatorio /tmp/icp-score-rodada.json
  python3 hermes/agents/icp_score/icp_score.py --desfazer <correlation_id>
  python3 hermes/agents/icp_score/icp_score.py --desfazer <correlation_id> --confirmo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do agente (espelha `hermes/agents/icp_score/agente-icp-score-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "icp_score"
PAPEL = "scoring"
VERSAO = "1.1.0"
WORKFLOW = "score-icp"
WORKFLOW_VERSAO = "v1"

SCORE_TYPE = "ICP"
TABELA_SCORES = "sales_intelligence.scores"
TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELA_SYNC_EVENTS = "sales_intelligence.sync_events"
TABELAS_PERMITIDAS = (TABELA_SCORES, TABELA_AGENT_RUNS, TABELA_SYNC_EVENTS)
TABELAS_PROIBIDAS = (
    "sales_intelligence.recommendations", "sales_intelligence.outbox_events",
    "sales_intelligence.interactions", "sales_intelligence.human_approvals",
    "sales_intelligence.contacts", "sales_intelligence.pain_hypotheses",
)

VER_CALCULADO = "CALCULADO"
VER_JA_EXISTE = "JA_EXISTE"
VER_RECUSADA = "RECUSADA"
VER_ERRO = "ERRO"
VEREDITOS = (VER_CALCULADO, VER_JA_EXISTE, VER_RECUSADA, VER_ERRO)

STATUS_AGENT_RUNS = {
    VER_CALCULADO: "COMPLETED",
    VER_JA_EXISTE: "COMPLETED",
    VER_RECUSADA: "REJECTED",
    VER_ERRO: "FAILED",
}

# Vereditos do modo `--planejar` (nenhuma conexao de banco e feita nele)
PLANEJADO_CALCULAR = "PLANEJADO_CALCULAR"
PLANEJADO_RECUSAR = "PLANEJADO_RECUSAR"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

# Vocabulario do recibo do JEV (hermes/jev/routing/router.py: OUTCOME_*)
JEV_EXECUTAR = "PASS"
LANES = ("small", "medium", "high", "critical")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_RECUSOU_AMBIENTE = 4
EXIT_FONTE = 5

MARCA_GRAVADO = "ICP_SCORE_GRAVADO"

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
CONTRATO_AGENTE_PADRAO = "hermes/agents/icp_score/agente-icp-score-v1.json"


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR — nunca pela profundidade do arquivo.

    O agente tem de importar de QUALQUER diretorio (as copias mutadas dos verificadores vivem
    em diretorio raso, as vezes `/tmp`): aqui a raiz e o primeiro ancestral que contem o
    contrato do agente, e o diretorio de trabalho entra como segunda tentativa porque a copia
    sob teste nao esta na arvore do repo.
    """
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / CONTRATO_AGENTE_PADRAO).is_file():
                return pasta
    return Path.cwd()


RAIZ_PADRAO = descobrir_raiz_padrao()


class RecusaDeAmbiente(Exception):
    """Ambiente nao permitido (ADR-005): nada e escrito."""


class ReciboJEVInvalido(Exception):
    """Chamada de LLM sem recibo valido do JEV: fail-closed."""


class GuardaDeEscritaViolada(Exception):
    """SQL tentando escrever fora das tabelas/operacoes declaradas."""


class PortaIndisponivel(Exception):
    """A porta de banco falhou."""


class ModeloInvalido(Exception):
    """O contrato do agente nao descreve um modelo utilizavel (fail-closed)."""


# ---------------------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------------------
def carregar_json(caminho) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_contrato_do_agente(raiz) -> dict:
    return carregar_json(Path(raiz) / CONTRATO_AGENTE_PADRAO)


def faixas_do_contrato_de_dados(raiz) -> tuple:
    """O vocabulario de `employee_band` vem do Data Contract V1.0, nao do codigo."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    return tuple(contrato["vocabularies"]["employee_band"])


def tipos_de_sinal_do_contrato_de_dados(raiz) -> tuple:
    """O vocabulario de `signal_type` (o que um sinal pode ser) vem do Data Contract."""
    contrato = carregar_json(Path(raiz) / CONTRATO_DADOS_PADRAO)
    return tuple(contrato["vocabularies"]["signal_type"])


def validar_modelo(modelo: dict, faixas_do_contrato, tipos_de_sinal=None) -> dict:
    """O modelo e policy: se ele nao esta integro, o agente RECUSA (fail-closed)."""
    if not isinstance(modelo, dict) or not modelo.get("nome"):
        raise ModeloInvalido("modelo sem nome: score sem versao e recusado pelo Data Contract")
    pesos = modelo.get("pesos")
    componentes = (modelo.get("componentes") or {})
    if not isinstance(pesos, dict) or not pesos:
        raise ModeloInvalido("modelo sem pesos")
    if set(pesos) != set(componentes):
        raise ModeloInvalido("pesos e componentes divergem: %s x %s"
                             % (sorted(pesos), sorted(componentes)))
    soma = round(sum(float(p) for p in pesos.values()), 6)
    if soma != 1.0:
        raise ModeloInvalido("pesos nao somam 1.00 (somam %s)" % soma)
    escala = modelo.get("escala") or {}
    if float(escala.get("minimo", -1)) != 0.0 or float(escala.get("maximo", -1)) != 100.0:
        raise ModeloInvalido("escala fora de 0-100: %r" % (escala,))
    bandas = set(componentes["porte"]["sub_scores"])
    if not bandas.issubset(set(faixas_do_contrato)):
        raise ModeloInvalido("faixa de porte fora do vocabulario do Data Contract: %s"
                             % sorted(bandas - set(faixas_do_contrato)))
    # -- cortes (1.1): portao de porte e de geografia, sem peso -------------------------
    cortes = modelo.get("cortes")
    if not isinstance(cortes, dict) or "porte_minimo" not in cortes or "geografia" not in cortes:
        raise ModeloInvalido("modelo sem os cortes declarados (porte_minimo/geografia)")
    corte_porte = cortes["porte_minimo"] or {}
    try:
        minimo_do_corte = float(corte_porte["minimo"])
    except (TypeError, ValueError, KeyError):
        raise ModeloInvalido("corte de porte sem minimo numerico: %r" % (corte_porte,))
    if minimo_do_corte <= 0:
        raise ModeloInvalido("corte de porte tem de ser positivo (veio %s)" % minimo_do_corte)
    if not isinstance(corte_porte.get("motivos"), dict) or not corte_porte["motivos"]:
        raise ModeloInvalido("corte de porte sem os motivos declarados")
    corte_geo = cortes["geografia"] or {}
    estados = corte_geo.get("estados")
    if not isinstance(estados, list) or not estados:
        raise ModeloInvalido("corte de geografia sem estados declarados")
    if not isinstance(corte_geo.get("motivos"), dict) or not corte_geo["motivos"]:
        raise ModeloInvalido("corte de geografia sem os motivos declarados")
    # -- intencao (1.1): os sinais declarados tem de existir e falar o vocabulario -------
    sinais = (componentes["intencao"] or {}).get("sinais")
    if not isinstance(sinais, list) or not sinais:
        raise ModeloInvalido("componente de intencao sem sinais declarados")
    tipos = [t for s in sinais for t in (s.get("signal_types") or [])]
    if not tipos:
        raise ModeloInvalido("componente de intencao sem signal_types declarados")
    if tipos_de_sinal is not None and not set(tipos).issubset(set(tipos_de_sinal)):
        raise ModeloInvalido("signal_type fora do vocabulario do Data Contract: %s"
                             % sorted(set(tipos) - set(tipos_de_sinal)))
    return modelo


# ---------------------------------------------------------------------------------------
# Normalizacao e o modelo puro (testavel sem banco)
# ---------------------------------------------------------------------------------------
def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return str(valor).strip()


def normalizar_texto(valor) -> str:
    """Caixa baixa, sem acento, espaco colapsado — o casamento nao depende de grafia."""
    texto = unicodedata.normalize("NFKD", _texto(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower()).strip()


def casa_termo(texto_normalizado: str, termo: str) -> bool:
    """Termo casando no INICIO de uma palavra do texto ('distribuid' casa 'distribuidora').

    Substring puro casaria no MEIO de palavra ('tech' dentro de 'fintech', 'carga' dentro de
    'descarga'), o que transforma vocabulario em ruido; o `\\b` inicial mantem o prefixo
    (singular/plural/genero) e corta o acidente.
    """
    termo = normalizar_texto(termo)
    if not termo:
        return False
    return re.search(r"\b" + re.escape(termo), texto_normalizado) is not None


def faixa_de_empregados(quantidade, faixas_do_contrato) -> str:
    """Deriva a faixa do `employee_count` — MESMA regra do Scout, vocabulario do contrato."""
    if quantidade in (None, ""):
        return "UNKNOWN"
    try:
        n = int(quantidade)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if n < 0:
        return "UNKNOWN"
    # As faixas do contrato sao do tipo `LT_70`, `70_149`, ..., `GT_1000`, `UNKNOWN`.
    numericas = []
    for faixa in faixas_do_contrato:
        m = re.fullmatch(r"(\d+)_(\d+)", faixa)
        if m:
            numericas.append((int(m.group(1)), int(m.group(2)), faixa))
    numericas.sort()
    for inicio, fim, faixa in numericas:
        if inicio <= n <= fim:
            return faixa
    primeira = numericas[0] if numericas else None
    ultima = numericas[-1] if numericas else None
    if primeira and n < primeira[0]:
        return "LT_%d" % primeira[0]
    if ultima and n > ultima[1]:
        return "GT_%d" % ultima[1]
    return "UNKNOWN"


def porte_efetivo(organizacao: dict, faixa_efetiva: str) -> tuple:
    """Quantos usuarios/funcionarios a fonte DECLAROU — a base do corte de porte.

    `employee_count` quando informado; senao o LIMITE INFERIOR da faixa efetiva, que e' o que
    a fonte declarou (`LT_70` vale 0, `GT_1000` vale 1001). Sem nenhum dos dois a quantidade e'
    `None` e o corte NAO passa: ausencia nao vira fit (fail-closed).
    """
    quantidade = organizacao.get("employee_count")
    if quantidade not in (None, ""):
        try:
            numero = int(quantidade)
        except (TypeError, ValueError):
            numero = None
        if numero is not None and numero >= 0:
            return numero, "employee_count"
    faixa = _texto(faixa_efetiva)
    if faixa:
        casou = re.fullmatch(r"(\d+)_(\d+)", faixa)
        if casou:
            return int(casou.group(1)), "faixa_inferior"
        casou = re.fullmatch(r"LT_(\d+)", faixa)
        if casou:
            return 0, "faixa_inferior"
        casou = re.fullmatch(r"GT_(\d+)", faixa)
        if casou:
            return int(casou.group(1)) + 1, "faixa_inferior"
    return None, "ausente"


def _sub_score(mapa: dict, chave: str, padrao: float) -> float:
    if chave in mapa:
        return float(mapa[chave])
    return float(padrao)


def calcular_icp(organizacao: dict, modelo: dict, faixas_do_contrato) -> dict:
    """Modelo V1 sobre os campos lidos. Devolve score, inputs, explanation e fingerprint.

    Funcao PURA: nao fala com banco, nao le arquivo, nao sabe de onde veio o dado. A regra
    vive no contrato (`modelo`), aqui fica so' a mecanica.
    """
    componentes = modelo["componentes"]

    # -- segmento ----------------------------------------------------------------------
    seg = componentes["segmento"]
    valores_seg = [_texto(organizacao.get(c)) for c in seg["campos"]]
    texto_seg = normalizar_texto(" ".join(v for v in valores_seg if v))
    casados = []
    if texto_seg:
        for grupo in seg["segmentos"]:
            if any(casa_termo(texto_seg, t) for t in grupo["termos"]):
                casados.append(grupo["nome"])
    if not texto_seg:
        sub_seg, motivo_seg = float(seg["sub_score_nao_casou"]), seg["motivos"]["sem_dado"]
        valor_seg = None
    elif casados:
        sub_seg, motivo_seg = float(seg["sub_score_casou"]), None
        valor_seg = casados[0]
    else:
        sub_seg, motivo_seg = float(seg["sub_score_nao_casou"]), seg["motivos"]["nao_reconhecido"]
        valor_seg = None

    # -- porte -------------------------------------------------------------------------
    port = componentes["porte"]
    band = _texto(organizacao.get("employee_band")).upper()
    origem_porte = "employee_band"
    if band not in faixas_do_contrato:
        derivada = faixa_de_empregados(organizacao.get("employee_count"), faixas_do_contrato)
        origem_porte = "employee_count" if derivada != "UNKNOWN" else "ausente"
        band = derivada
    faixa_efetiva = band
    if faixa_efetiva == "UNKNOWN":
        sub_port, motivo_port = float(port["sub_score_ausente"]), port["motivos"]["sem_dado"]
    else:
        sub_port = _sub_score(port["sub_scores"], faixa_efetiva, port["sub_score_ausente"])
        if sub_port >= 100.0:
            motivo_port = None
        elif faixa_efetiva in ("LT_70",):
            motivo_port = port["motivos"]["abaixo_do_icp"]
        elif faixa_efetiva in ("GT_1000",):
            motivo_port = port["motivos"]["acima_do_icp"]
        else:
            motivo_port = port["motivos"]["fora_do_sweet_spot"]

    # -- geografia (criterio 2 da definicao do dono) ------------------------------------
    geo = componentes["geografia"]
    cortes = modelo["cortes"]
    corte_geo = cortes["geografia"]
    estados_do_corte = [normalizar_texto(e).upper() for e in corte_geo["estados"]]
    valor_geo = _texto(organizacao.get(geo["campo"])).upper()
    if not valor_geo:
        sub_geo, motivo_geo, valor_geo = float(geo["sub_score_ausente"]), geo["motivos"]["sem_dado"], None
    elif valor_geo in [normalizar_texto(k).upper() for k in geo["sub_scores"]]:
        chave_geo = [k for k in geo["sub_scores"]
                     if normalizar_texto(k).upper() == valor_geo][0]
        sub_geo, motivo_geo = float(geo["sub_scores"][chave_geo]), None
    else:
        sub_geo, motivo_geo = float(geo["sub_score_fora"]), geo["motivos"]["fora_do_icp"]

    # -- intencao (criterios 3, 4 e 5): sinal so' vale com FONTE e DATA ------------------
    intc = componentes["intencao"]
    sinais_lidos = [s for s in (organizacao.get("sinais") or []) if isinstance(s, dict)]
    por_tipo = {}
    for sinal in sinais_lidos:
        tipo = _texto(sinal.get("signal_type")).upper()
        if tipo:
            por_tipo.setdefault(tipo, []).append(sinal)
    sinais_saida = []
    fontes_creditadas = []
    for declarado in intc["sinais"]:
        candidatos = []
        for tipo in declarado["signal_types"]:
            candidatos.extend(por_tipo.get(_texto(tipo).upper(), []))
        candidatos.sort(key=lambda s: (_texto(s.get("event_date")), _texto(s.get("signal_id"))))
        escolhido = candidatos[-1] if candidatos else None
        item = {"nome": declarado["nome"], "signal_types": list(declarado["signal_types"]),
                "credito": False, "fonte": None, "data": None, "signal_type": None,
                "origem_do_sinal": None, "inferido": False, "motivo": None}
        if escolhido is None:
            item["motivo"] = intc["motivos"]["sem_dado"]
        else:
            # Fonte e data sao LIDAS, nunca preenchidas: sem elas o sinal nao da credito.
            fonte = _texto(escolhido.get("source_type")) or _texto(escolhido.get("source_url"))
            data = _texto(escolhido.get("event_date"))
            item["signal_type"] = _texto(escolhido.get("signal_type"))
            item["origem_do_sinal"] = _texto(escolhido.get("source_type")) or None
            item["inferido"] = bool(escolhido.get("inferido"))
            if not fonte:
                item["motivo"] = intc["motivos"]["sem_fonte"]
            elif not data:
                item["motivo"] = intc["motivos"]["sem_data"]
            else:
                item.update({"credito": True, "fonte": fonte, "data": data})
                fontes_creditadas.append({"criterio": declarado["nome"],
                                          "signal_type": item["signal_type"],
                                          "fonte": fonte, "data": data})
        sinais_saida.append(item)
    creditos_de_intencao = sum(1 for s in sinais_saida if s["credito"])
    total_de_sinais = len(intc["sinais"])
    sub_int = round(float(intc["sub_score_cheio"]) * creditos_de_intencao / total_de_sinais,
                    6) if total_de_sinais else float(intc["sub_score_ausente"])
    motivo_int = next((s["motivo"] for s in sinais_saida if s["motivo"]), None)

    # -- modelo de negocio -------------------------------------------------------------
    mod = componentes["modelo_b2b"]
    valor_mod_bruto = _texto(organizacao.get(mod["campo"])).upper()
    valor_mod = valor_mod_bruto or None
    mapa_mod = {k.upper(): v for k, v in mod["sub_scores"].items()}
    if not valor_mod_bruto:
        sub_mod, motivo_mod = float(mod["sub_score_ausente"]), mod["motivos"]["sem_dado"]
    elif valor_mod_bruto in mapa_mod:
        sub_mod = float(mapa_mod[valor_mod_bruto])
        if sub_mod >= 100.0:
            motivo_mod = None
        elif sub_mod <= float(mod["sub_score_ausente"]):
            motivo_mod = mod["motivos"]["fora_do_icp"]
        else:
            motivo_mod = mod["motivos"]["parcial"]
    else:
        sub_mod, motivo_mod = float(mod["sub_score_ausente"]), mod["motivos"]["sem_dado"]

    componentes_saida = [
        {"nome": "segmento", "peso": float(modelo["pesos"]["segmento"]), "sub_score": sub_seg,
         "campo_lido": "+".join(seg["campos"]),
         "valor_lido": " / ".join(v for v in valores_seg if v) or None,
         "segmento_casado": valor_seg, "segmentos_casados": casados, "motivo": motivo_seg},
        {"nome": "porte", "peso": float(modelo["pesos"]["porte"]), "sub_score": sub_port,
         "campo_lido": "employee_band|employee_count", "valor_lido": faixa_efetiva,
         "origem_do_valor": origem_porte, "employee_count": organizacao.get("employee_count"),
         "motivo": motivo_port},
        {"nome": "geografia", "peso": float(modelo["pesos"]["geografia"]), "sub_score": sub_geo,
         "campo_lido": geo["campo"], "valor_lido": valor_geo, "motivo": motivo_geo},
        {"nome": "modelo_b2b", "peso": float(modelo["pesos"]["modelo_b2b"]), "sub_score": sub_mod,
         "campo_lido": mod["campo"], "valor_lido": valor_mod, "motivo": motivo_mod},
        {"nome": "intencao", "peso": float(modelo["pesos"]["intencao"]), "sub_score": sub_int,
         "campo_lido": "signals.signal_type + source_type/source_url + event_date",
         "valor_lido": "%d/%d sinais com fonte e data" % (creditos_de_intencao, total_de_sinais),
         "sinais_lidos": len(sinais_lidos), "sinais": sinais_saida, "motivo": motivo_int},
    ]
    for c in componentes_saida:
        c["contribuicao"] = round(c["peso"] * c["sub_score"], 6)

    decimais = int((modelo.get("escala") or {}).get("decimais", 2))
    minimo = float((modelo.get("escala") or {}).get("minimo", 0.0))
    maximo = float((modelo.get("escala") or {}).get("maximo", 100.0))
    score_bruto = round(sum(c["contribuicao"] for c in componentes_saida), decimais)
    score_bruto = min(max(score_bruto, minimo), maximo)

    # -- cortes: PORTAO, nao peso — quem nao passa NAO entra na campanha -----------------
    corte_porte = cortes["porte_minimo"]
    valor_porte, origem_do_corte = porte_efetivo(organizacao, faixa_efetiva)
    passou_porte = valor_porte is not None and valor_porte >= float(corte_porte["minimo"])
    motivo_corte_porte = None if passou_porte else (
        corte_porte["motivos"]["abaixo"] if valor_porte is not None
        else corte_porte["motivos"]["sem_dado"])
    passou_geo = bool(valor_geo) and valor_geo in estados_do_corte
    motivo_corte_geo = None if passou_geo else (
        corte_geo["motivos"]["fora"] if valor_geo else corte_geo["motivos"]["sem_dado"])
    cortes_saida = [
        {"nome": "porte_minimo", "minimo": float(corte_porte["minimo"]),
         "valor_efetivo": valor_porte, "origem_do_valor": origem_do_corte,
         "passou": passou_porte, "motivo": motivo_corte_porte},
        {"nome": "geografia", "estados": list(corte_geo["estados"]), "valor_efetivo": valor_geo,
         "passou": passou_geo, "motivo": motivo_corte_geo},
    ]
    motivos_corte = [c["motivo"] for c in cortes_saida if c["motivo"]]
    corte_aplicado = bool(motivos_corte)
    score = 0.0 if corte_aplicado else score_bruto

    # -- os CINCO criterios da definicao, nomeados um a um ------------------------------
    criterios = [
        {"nome": "porte", "resultado": "casou" if sub_port >= 100.0 else (
            "sem_dado" if faixa_efetiva == "UNKNOWN" else "nao_casou"), "motivo": motivo_port},
        {"nome": "geografia", "resultado": "casou" if sub_geo >= 100.0 else (
            "sem_dado" if not valor_geo else "nao_casou"), "motivo": motivo_geo},
    ]
    for sinal in sinais_saida:
        criterios.append({
            "nome": "intencao_%s" % sinal["nome"],
            "resultado": "casou" if sinal["credito"] else (
                "sem_dado" if not sinal["signal_type"] else "nao_casou"),
            "motivo": sinal["motivo"]})

    motivos = motivos_corte + [c["motivo"] for c in componentes_saida if c["motivo"]]
    campos_fingerprint = {
        "industry_code": _texto(organizacao.get("industry_code")),
        "industry_name": _texto(organizacao.get("industry_name")),
        "employee_count": _texto(organizacao.get("employee_count")),
        "employee_band": _texto(organizacao.get("employee_band")),
        "faixa_efetiva": faixa_efetiva,
        "business_model": _texto(organizacao.get("business_model")),
        "state": valor_geo or "",
        "porte_efetivo": _texto(valor_porte),
        "intencao": sorted("%s|%s|%s|%s" % (s["criterio"], s["signal_type"], s["fonte"], s["data"])
                           for s in fontes_creditadas),
    }
    fingerprint = hashlib.sha256(
        json.dumps(campos_fingerprint, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    inputs = {
        "organization_id": organizacao.get("organization_id"),
        "lido_em": organizacao.get("lido_em"),
        "campos": {
            "legal_name": _texto(organizacao.get("legal_name")) or None,
            "trade_name": _texto(organizacao.get("trade_name")) or None,
            "industry_code": _texto(organizacao.get("industry_code")) or None,
            "industry_name": _texto(organizacao.get("industry_name")) or None,
            "employee_count": organizacao.get("employee_count"),
            "employee_band": _texto(organizacao.get("employee_band")) or None,
            "business_model": _texto(organizacao.get("business_model")) or None,
            "state": valor_geo,
            "country_code": _texto(organizacao.get("country_code")) or None,
            "status": _texto(organizacao.get("status")) or None,
            "organizacao_updated_at": organizacao.get("updated_at"),
        },
        "faixa_efetiva": faixa_efetiva,
        "origem_do_porte": origem_porte,
        "porte_efetivo": {"valor": valor_porte, "origem": origem_do_corte},
        "sinais_lidos": len(sinais_lidos),
        "fingerprint": fingerprint,
    }
    explanation = {
        "modelo": modelo["nome"],
        "status": modelo.get("status"),
        "formula": modelo.get("formula"),
        "escala": "%s-%s" % (minimo, maximo),
        "pesos_somam": round(sum(float(p) for p in modelo["pesos"].values()), 6),
        "componentes": componentes_saida,
        "cortes": cortes_saida,
        "corte_aplicado": corte_aplicado,
        "elegivel": not corte_aplicado,
        "score_bruto": score_bruto,
        "criterios": criterios,
        "motivos": motivos,
        "regra_de_ausencia": modelo.get("regra_de_ausencia"),
    }
    return {"score_value": score, "score_bruto": score_bruto, "elegivel": not corte_aplicado,
            "inputs": inputs, "explanation": explanation, "fingerprint": fingerprint,
            "motivos": motivos}


def chave_idempotencia(organization_id: str, modelo_nome: str, fingerprint: str) -> str:
    return "icp:score:%s:%s:%s" % (organization_id, modelo_nome, fingerprint[:16])


# ---------------------------------------------------------------------------------------
# SQL — literal seguro + guarda de escrita (o que o agente pode e nao pode escrever)
# ---------------------------------------------------------------------------------------
def lit(valor) -> str:
    if valor is None:
        return "NULL"
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, float)):
        return repr(valor)
    return "'" + str(valor).replace("'", "''") + "'"


def lit_json(objeto) -> str:
    return lit(json.dumps(objeto, ensure_ascii=False, sort_keys=True)) + "::jsonb"


_ESCRITA = (
    ("insert", re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][\w\.]*)", re.I)),
    ("update", re.compile(r"\bUPDATE\s+([A-Za-z_][\w\.]*)", re.I)),
    ("delete", re.compile(r"\bDELETE\s+FROM\s+([A-Za-z_][\w\.]*)", re.I)),
)
_DDL = re.compile(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT\s+ON)\b", re.I)
_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _sem_literais(sql: str) -> str:
    """A instrucao SEM o conteudo dos literais — so o codigo SQL.

    Nome de empresa nao e instrucao: um literal ('Drop Solucoes Ltda') faria a guarda recusar
    dado legitimo como se fosse DDL. O contrato proibe DDL e escrita fora do declarado; quem
    escreve dado e o `lit()`, que escapa apostrofo dobrando.
    """
    return _LITERAL.sub("''", sql)


def validar_sql(sql: str, permitir_remocao: bool = False) -> None:
    """Fail-closed: recusa DDL, escrita fora do declarado e UPDATE em `scores`."""
    codigo = _sem_literais(sql)
    if _DDL.search(codigo):
        raise GuardaDeEscritaViolada("DDL nao e permitido ao agente ICP Score")
    for operacao, padrao in _ESCRITA:
        for tabela in padrao.findall(codigo):
            if tabela.lower() not in TABELAS_PERMITIDAS:
                raise GuardaDeEscritaViolada("escrita em tabela nao declarada: %s" % tabela)
            if operacao == "update" and tabela.lower() == TABELA_SCORES:
                raise GuardaDeEscritaViolada(
                    "UPDATE em scores nao e permitido: score e imutavel (corrigir e inserir "
                    "versao nova)")
            if operacao == "delete" and not permitir_remocao:
                raise GuardaDeEscritaViolada("DELETE fora do desfazer explicito: %s" % tabela)


# ---------------------------------------------------------------------------------------
# Porta de banco (ADR-0008: o SQL roda na VPS; a porta e o prefixo psql)
# ---------------------------------------------------------------------------------------
class PortaSQL:
    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        raise NotImplementedError


class PortaAusente(PortaSQL):
    """Sem porta configurada: qualquer tentativa de falar com o banco RECUSA."""

    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        raise PortaIndisponivel(
            "nenhuma porta de banco configurada (use --prefixo ou TRE_PSQL_PREFIXO)")


class PortaPsql(PortaSQL):
    """Executa SQL pelo prefixo psql informado (ex.: 'docker exec -i pg-sales-dev psql -U ..')."""

    def __init__(self, prefixo: str, timeout: int = 120):
        if not prefixo:
            raise PortaIndisponivel("prefixo psql vazio (use --prefixo ou TRE_PSQL_PREFIXO)")
        self.prefixo = shlex.split(prefixo)
        self.timeout = timeout

    def executar(self, sql: str, permitir_remocao: bool = False) -> tuple:
        validar_sql(sql, permitir_remocao=permitir_remocao)
        comando = self.prefixo + ["-v", "ON_ERROR_STOP=1", "-q", "-tA", "-F", "|"]
        try:
            p = subprocess.run(comando, input=sql, capture_output=True, text=True,
                               timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PortaIndisponivel("porta psql falhou: %s" % exc)
        return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------------------
# SQL do agente
# ---------------------------------------------------------------------------------------
def sql_ler_organizacao(organization_id: str) -> str:
    return (
        "SELECT id::text, COALESCE(legal_name, ''), COALESCE(trade_name, ''), "
        "COALESCE(industry_code, ''), COALESCE(industry_name, ''), "
        "COALESCE(employee_count::text, ''), COALESCE(employee_band, ''), "
        "COALESCE(business_model, ''), COALESCE(state, ''), COALESCE(country_code, ''), "
        "COALESCE(status, ''), "
        "COALESCE(to_char(updated_at, 'YYYY-MM-DD\"T\"HH24:MI:SS'), '') "
        "FROM %s WHERE id = %s AND deleted_at IS NULL;" % (
            TABELA_ORGANIZACOES, lit(organization_id))
    )


def sql_ler_sinais(organization_id: str) -> str:
    """Os sinais da organizacao — SOMENTE LEITURA (o agente nao escreve sinal).

    Traz o que o criterio de intencao precisa medir: `signal_type`, a FONTE declarada
    (`source_type`/`source_url`), a DATA (`event_date`) e a marca de inferencia da `evidence`.
    """
    return (
        "SELECT COALESCE(id::text, ''), COALESCE(signal_type, ''), "
        "COALESCE(source_type, ''), COALESCE(source_url, ''), "
        "COALESCE(to_char(event_date, 'YYYY-MM-DD\"T\"HH24:MI:SS'), ''), "
        "COALESCE(evidence ->> 'inferencia', '') "
        "FROM sales_intelligence.signals WHERE organization_id = %s "
        "ORDER BY event_date ASC NULLS LAST, id;" % lit(organization_id)
    )


def organizacao_da_linha(linha: str) -> dict:
    campos = linha.split("|")
    while len(campos) < 12:
        campos.append("")
    return {
        "organization_id": campos[0].strip(),
        "legal_name": campos[1],
        "trade_name": campos[2],
        "industry_code": campos[3],
        "industry_name": campos[4],
        "employee_count": campos[5].strip() or None,
        "employee_band": campos[6],
        "business_model": campos[7],
        "state": campos[8],
        "country_code": campos[9],
        "status": campos[10],
        "updated_at": campos[11],
    }


def sinais_da_saida(saida: str) -> list:
    """Linhas do SELECT de sinais -> sinais do calculo. Fonte e data vem do que foi LIDO."""
    sinais = []
    for linha in saida.splitlines():
        if not linha.strip():
            continue
        campos = linha.split("|")
        while len(campos) < 6:
            campos.append("")
        valor_inferencia = campos[5].strip().lower()
        sinais.append({
            "signal_id": campos[0].strip() or None,
            "signal_type": campos[1],
            "source_type": campos[2],
            "source_url": campos[3],
            "event_date": campos[4],
            "inferido": valor_inferencia in ("true", "t", "1", "sim"),
        })
    return sinais


def sql_gravar_score(score_id: str, sync_event_id: str, chave: str, organization_id: str,
                     calculo: dict, modelo_nome: str, evidencia: dict) -> str:
    """Uma transacao, DUAS instrucoes: claim da chave -> insere o score -> fecha o evento.

    Por que duas instrucoes e nao uma: as CTEs de escrita e a instrucao principal rodam com o
    MESMO snapshot, entao a instrucao principal NAO enxerga a linha que a CTE acabou de
    inserir (defeito medido no aceite E2E da W4). Aqui a segunda instrucao e um comando
    proprio — snapshot novo — e so fecha a sincronia quando o score DESTA rodada existe de
    fato, devolvendo a marca `ICP_SCORE_GRAVADO`. Sem a marca (chave ja existia), nada foi
    duplicado: e replay.
    """
    payload = dict(evidencia)
    payload["idempotency_key"] = chave
    return (
        "BEGIN;\n"
        "WITH claim AS (\n"
        "  INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, created_at)\n"
        "  VALUES ({sevid}, 'score', {orgid}, 'icp_score', 'postgresql', 'INSERT', {versao}, "
        "{chave}, 'PENDING', {payload}, now())\n"
        "  ON CONFLICT (idempotency_key) DO NOTHING\n"
        "  RETURNING id\n"
        ")\n"
        "INSERT INTO {scores} (id, organization_id, score_type, score_value, score_version, "
        "inputs, explanation, calculated_at)\n"
        "SELECT {sid}, {orgid}, {tipo}, {valor}, {versao}, {inputs}, {expl}, now() FROM claim\n"
        "ON CONFLICT (id) DO NOTHING\n"
        "RETURNING id;\n"
        "UPDATE {sync} SET status = 'SUCCESS', completed_at = now(),\n"
        "  response_payload = jsonb_build_object('score_id', {sid}, 'score_value', {valor}, "
        "'score_type', {tipo}, 'veredito', 'CALCULADO', 'idempotency_key', {chave})\n"
        "WHERE idempotency_key = {chave} AND EXISTS (SELECT 1 FROM {scores} WHERE id = {sid})\n"
        "RETURNING {marca};\n"
        "COMMIT;\n"
    ).format(sync=TABELA_SYNC_EVENTS, scores=TABELA_SCORES, sevid=lit(sync_event_id),
             sid=lit(score_id), orgid=lit(organization_id), tipo=lit(SCORE_TYPE),
             valor=lit(calculo["score_value"]), versao=lit(modelo_nome),
             inputs=lit_json(calculo["inputs"]), expl=lit_json(calculo["explanation"]),
             chave=lit(chave), payload=lit_json(payload), marca=lit(MARCA_GRAVADO))


def sql_registrar_execucao(run_id: str, correlation_id: str, organization_id, status: str,
                           entrada: dict, saida: dict, iniciado: str, terminado: str,
                           erro=None) -> str:
    return (
        "INSERT INTO {tabela} (id, agent_name, agent_role, agent_version, workflow, "
        "workflow_version, organization_id, triggered_by, correlation_id, input, output, model, "
        "started_at, finished_at, status, error, created_at)\n"
        "VALUES ({rid}, {agente}, {papel}, {versao}, {workflow}, {wfversao}, {orgid}, {gatilho}, "
        "{corr}, {entrada}, {saida}, NULL, {ini}, {fim}, {status}, {erro}, now());\n"
    ).format(tabela=TABELA_AGENT_RUNS, rid=lit(run_id), agente=lit(AGENTE), papel=lit(PAPEL),
             versao=lit(VERSAO), workflow=lit(WORKFLOW), wfversao=lit(WORKFLOW_VERSAO),
             orgid=lit(organization_id), gatilho=lit("cli"), corr=lit(correlation_id),
             entrada=lit_json(entrada), saida=lit_json(saida), ini=lit(iniciado), fim=lit(terminado),
             status=lit(status), erro=lit_json(erro) if erro else "NULL")


def sql_selecionar_calculados(correlation_id: str) -> str:
    return (
        "SELECT DISTINCT (a.output ->> 'score_id') FROM {runs} a\n"
        "JOIN {scores} s ON s.id::text = (a.output ->> 'score_id')\n"
        "WHERE a.correlation_id = {corr} AND a.agent_name = {agente}\n"
        "  AND a.output ->> 'veredito' = 'CALCULADO';\n"
    ).format(runs=TABELA_AGENT_RUNS, scores=TABELA_SCORES, corr=lit(correlation_id),
             agente=lit(AGENTE))


def sql_desfazer(ids: list, correlation_id: str, sync_event_id: str) -> str:
    lista = ", ".join(lit(i) for i in ids)
    payload = {"motivo": "desfazer da rodada", "correlation_id": correlation_id,
               "agente": "%s/%s" % (AGENTE, VERSAO)}
    return (
        "BEGIN;\n"
        "DELETE FROM {sync} WHERE entity_type = 'score' AND entity_id IN ({ids}) "
        "AND operation = 'INSERT';\n"
        "DELETE FROM {scores} WHERE id IN ({ids});\n"
        "INSERT INTO {sync} (id, entity_type, entity_id, source_system, target_system, operation, "
        "source_version, idempotency_key, status, request_payload, created_at)\n"
        "VALUES ({sevid}, 'score', NULL, 'icp_score', 'postgresql', 'ROLLBACK', {versao}, "
        "{chave}, 'SUCCESS', {payload}, now());\n"
        "COMMIT;\n"
    ).format(sync=TABELA_SYNC_EVENTS, scores=TABELA_SCORES, ids=lista, sevid=lit(sync_event_id),
             versao=lit(VERSAO), chave=lit("icp:rollback:%s" % correlation_id),
             payload=lit_json(payload))


# ---------------------------------------------------------------------------------------
# Gate do JEV (doc 07 §7: antes de chamada de LLM em tarefa elegivel, o Hermes consulta o JEV)
# ---------------------------------------------------------------------------------------
def validar_recibo_jev(recibo) -> dict:
    if not recibo:
        raise ReciboJEVInvalido("sem recibo de decisao do JEV: abstencao e recusa, nao permissao")
    if not isinstance(recibo, dict):
        raise ReciboJEVInvalido("recibo do JEV ilegivel")
    faltando = [c for c in ("decision_id", "lane", "outcome") if not recibo.get(c)]
    if faltando:
        raise ReciboJEVInvalido("recibo do JEV incompleto: %s" % ", ".join(faltando))
    if recibo["outcome"] != JEV_EXECUTAR:
        raise ReciboJEVInvalido("recibo do JEV com outcome %r (exige %r)"
                                % (recibo["outcome"], JEV_EXECUTAR))
    if recibo["lane"] not in LANES:
        raise ReciboJEVInvalido("lane fora do vocabulario: %r" % recibo["lane"])
    return recibo


def chamar_llm(prompt: str, recibo=None, provedor=None, modelo=None) -> dict:
    """v1 NAO executa LLM — o gate existe e e fail-closed. Sem recibo valido, nao passa."""
    recibo = validar_recibo_jev(recibo)
    return {
        "autorizado": True,
        "decision_id": recibo["decision_id"],
        "lane": recibo["lane"],
        "provedor": provedor,
        "modelo": modelo,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "executado": False,
        "motivo": "v1 do ICP Score e deterministica: nenhuma chamada de LLM e feita",
    }


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------------------
class IcpScore:
    def __init__(self, porta=None, raiz=None, relogio=agora, ambiente=None, correlation_id=None):
        self.raiz = Path(raiz or RAIZ_PADRAO)
        self.contrato = carregar_contrato_do_agente(self.raiz)
        if self.contrato.get("agente") != AGENTE:
            raise ValueError("contrato do agente nao e do icp_score: %r"
                             % self.contrato.get("agente"))
        self.faixas = faixas_do_contrato_de_dados(self.raiz)
        self.tipos_de_sinal = tipos_de_sinal_do_contrato_de_dados(self.raiz)
        self.modelo = validar_modelo(self.contrato.get("modelo") or {}, self.faixas,
                                     self.tipos_de_sinal)
        # A versao do score tem UMA fonte: o nome do modelo. Se ele nao serve como valor da
        # coluna `score_version` (varchar(30)), o agente RECUSA em vez de gravar prosa.
        self.modelo_nome = self.modelo["nome"]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,30}", self.modelo_nome):
            raise ModeloInvalido(
                "modelo.nome nao serve como `scores.score_version` (varchar(30)): %r"
                % self.modelo_nome)
        if self.contrato.get("persistencia", {}).get("score_type") != SCORE_TYPE:
            raise ValueError("contrato do agente declara score_type %r (codigo usa %r)"
                             % (self.contrato.get("persistencia", {}).get("score_type"),
                                SCORE_TYPE))
        self.porta = porta if porta is not None else PortaAusente()
        self.relogio = relogio
        self.ambiente = ambiente
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.alvo = None

    def porta_sql(self):
        """A porta responde sempre; sem prefixo configurado ela RECUSA (fail-closed)."""
        return self.porta

    # -- ambiente ---------------------------------------------------------------------
    def conferir_ambiente(self) -> str:
        if self.ambiente in (None, ""):
            raise RecusaDeAmbiente("ambiente nao declarado: recusa (fail-closed)")
        if self.ambiente == AMBIENTE_RECUSADO:
            raise RecusaDeAmbiente(
                "ICP Score v1 nao escreve em prod (ADR-005): a promocao exige card proprio com "
                "aprovacao humana registrada")
        if self.ambiente not in AMBIENTES_PERMITIDOS:
            raise RecusaDeAmbiente("ambiente desconhecido: %r" % self.ambiente)
        return self.ambiente

    def identificar_alvo(self) -> dict:
        """Registra a identidade do alvo medido (sem afirmar nome de ambiente)."""
        rc, saida, erro = self.porta.executar(
            "SELECT current_database(), current_user, version();")
        if rc != 0:
            raise PortaIndisponivel("nao consegui ler a identidade do alvo: %s" % (erro or saida))
        campos = saida.strip().split("|")
        self.alvo = {"banco": campos[0] if campos else None,
                     "usuario": campos[1] if len(campos) > 1 else None,
                     "servidor": (campos[2] if len(campos) > 2 else "")[:80]}
        return self.alvo

    # -- leitura ----------------------------------------------------------------------
    def ler_organizacao(self, organization_id: str):
        """Le a organizacao no banco. `None` = inexistente/apagada (RECUSADA, nao erro)."""
        rc, saida, erro = self.porta.executar(sql_ler_organizacao(organization_id))
        if rc != 0:
            raise PortaIndisponivel("leitura da organizacao falhou: %s" % (erro or saida))
        linhas = [l for l in saida.splitlines() if l.strip()]
        if not linhas:
            return None
        organizacao = organizacao_da_linha(linhas[0])
        organizacao["lido_em"] = self.relogio()
        return organizacao

    def ler_sinais(self, organization_id: str) -> list:
        """Le os sinais da organizacao — SOMENTE LEITURA: o agente nao escreve sinal."""
        rc, saida, erro = self.porta.executar(sql_ler_sinais(organization_id))
        if rc != 0:
            raise PortaIndisponivel("leitura dos sinais falhou: %s" % (erro or saida))
        return sinais_da_saida(saida)

    def conferir_fonte(self, organization_id) -> tuple:
        """A fonte escolhe o SUJEITO: id ilegivel e recusa de forma, nao ida ao banco."""
        texto = _texto(organization_id)
        if not texto:
            return None, "SEM_ORGANIZATION_ID"
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", texto):
            return None, "ORGANIZATION_ID_INVALIDO"
        return texto, None

    # -- modos ------------------------------------------------------------------------
    def planejar(self, linha: dict) -> dict:
        """Modo sem banco: calcula com os campos da PROPRIA fonte e diz o que faria."""
        organization_id, problema = self.conferir_fonte(linha.get("organization_id"))
        if problema:
            return {"veredito": PLANEJADO_RECUSAR, "motivos": [problema],
                    "organization_id": None, "score_value": None, "idempotency_key": None,
                    "explanation": None}
        dados = {c: linha.get(c) for c in self.contrato["entrada"]["campos_do_modo_planejar"]}
        dados["organization_id"] = organization_id
        calculo = calcular_icp(dados, self.modelo, self.faixas)
        chave = chave_idempotencia(organization_id, self.modelo_nome, calculo["fingerprint"])
        return {"veredito": PLANEJADO_CALCULAR, "motivos": calculo["motivos"],
                "organization_id": organization_id, "score_value": calculo["score_value"],
                "idempotency_key": chave, "explanation": calculo["explanation"],
                "origem_do_dado": "fonte (modo planejar, sem banco)"}

    def processar(self, linha: dict) -> dict:
        inicio = self.relogio()
        organization_id, problema = self.conferir_fonte(linha.get("organization_id"))
        entrada = {"linha": linha, "ambiente": self.ambiente,
                   "correlation_id": self.correlation_id,
                   "modelo": self.modelo_nome}
        run_id = str(uuid.uuid4())
        resultado = {"veredito": VER_ERRO, "motivos": [], "organization_id": organization_id,
                     "score_id": None, "score_value": None, "idempotency_key": None,
                     "fingerprint": None, "origem_do_dado": "banco (organizations)"}
        try:
            if problema:
                resultado["veredito"] = VER_RECUSADA
                resultado["motivos"] = [problema]
            else:
                organizacao = self.ler_organizacao(organization_id)
                if organizacao is None:
                    resultado["veredito"] = VER_RECUSADA
                    resultado["motivos"] = ["ORGANIZACAO_NAO_ENCONTRADA"]
                else:
                    # A fonte NAO contamina: o dado vem do banco (organizacao E sinais).
                    organizacao["sinais"] = self.ler_sinais(organization_id)
                    calculo = calcular_icp(organizacao, self.modelo, self.faixas)
                    chave = chave_idempotencia(organization_id, self.modelo_nome,
                                               calculo["fingerprint"])
                    resultado["fingerprint"] = calculo["fingerprint"]
                    resultado["idempotency_key"] = chave
                    resultado["score_value"] = calculo["score_value"]
                    resultado["motivos"] = calculo["motivos"]
                    score_id = str(uuid.uuid4())
                    sync_event_id = str(uuid.uuid4())
                    evidencia = {"origem": "icp_score",
                                 "agente": "%s/%s" % (AGENTE, VERSAO),
                                 "modelo": self.modelo_nome,
                                 "correlation_id": self.correlation_id,
                                 "organization_id": organization_id,
                                 "fingerprint": calculo["fingerprint"]}
                    rc, saida, erro = self.porta.executar(sql_gravar_score(
                        score_id, sync_event_id, chave, organization_id, calculo,
                        self.modelo_nome, evidencia))
                    if rc != 0:
                        raise PortaIndisponivel("gravacao do score falhou: %s" % (erro or saida))
                    if MARCA_GRAVADO in [l.strip() for l in saida.splitlines()]:
                        resultado["veredito"] = VER_CALCULADO
                        resultado["score_id"] = score_id
                        resultado["sync_event_id"] = sync_event_id
                    else:
                        # a chave ja existia (mesmos dados): nada foi duplicado
                        resultado["veredito"] = VER_JA_EXISTE
                        resultado["motivos"] = list(resultado["motivos"]) + ["IDEMPOTENCIA_REPLAY"]
        except (PortaIndisponivel, GuardaDeEscritaViolada) as exc:
            resultado["veredito"] = VER_ERRO
            resultado["motivos"] = [str(exc)]
            resultado["erro"] = {"tipo": type(exc).__name__, "mensagem": str(exc)}
        fim = self.relogio()
        status = STATUS_AGENT_RUNS[resultado["veredito"]]
        rc_run, saida_run, erro_run = self.porta_sql().executar(sql_registrar_execucao(
            run_id, self.correlation_id, resultado.get("organization_id"), status,
            entrada, resultado, inicio, fim, resultado.get("erro")))
        if rc_run != 0:
            # Fail-closed: a execucao NAO pode ser reportada como concluida quando a propria
            # auditoria nao foi escrita.
            resultado["veredito"] = VER_ERRO
            resultado["motivos"] = list(resultado.get("motivos") or []) + \
                ["AUDITORIA_NAO_REGISTRADA: %s" % (erro_run or saida_run)]
            resultado["erro"] = {"tipo": "PortaIndisponivel",
                                 "mensagem": "auditoria nao registrada: %s"
                                             % (erro_run or saida_run)}
            resultado["auditoria_registrada"] = False
            resultado["agent_run_id"] = run_id
            resultado["status_agent_runs"] = STATUS_AGENT_RUNS[VER_ERRO]
            return resultado
        resultado["agent_run_id"] = run_id
        resultado["auditoria_registrada"] = True
        resultado["status_agent_runs"] = status
        return resultado

    def rodar(self, linhas: list, planejar: bool = False) -> dict:
        resultados = []
        for linha in linhas:
            if planejar:
                resultados.append(self.planejar(linha))
            else:
                resultados.append(self.processar(linha))
        resumo = {"agente": "%s/%s" % (AGENTE, VERSAO), "modelo": self.modelo_nome,
                  "ambiente": self.ambiente, "correlation_id": self.correlation_id,
                  "alvo": self.alvo, "planejar": planejar, "total": len(resultados),
                  "por_veredito": {}, "resultados": resultados}
        for r in resultados:
            resumo["por_veredito"][r["veredito"]] = resumo["por_veredito"].get(r["veredito"], 0) + 1
        return resumo

    def desfazer(self, correlation_id: str, confirmo: bool = False) -> dict:
        rc, saida, erro = self.porta.executar(sql_selecionar_calculados(correlation_id))
        if rc != 0:
            raise PortaIndisponivel("nao consegui listar os scores da rodada: %s" % (erro or saida))
        ids = [l.strip() for l in saida.splitlines() if l.strip()]
        if not confirmo:
            return {"correlation_id": correlation_id, "confirmo": False, "scores": ids,
                    "apagados": 0, "dry_run": True}
        if ids:
            rc, saida, erro = self.porta.executar(
                sql_desfazer(ids, correlation_id, str(uuid.uuid4())), permitir_remocao=True)
            if rc != 0:
                raise PortaIndisponivel("desfazer falhou: %s" % (erro or saida))
        return {"correlation_id": correlation_id, "confirmo": True, "scores": ids,
                "apagados": len(ids), "dry_run": False}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def ler_linhas(caminho: str) -> list:
    if caminho == "-":
        texto = sys.stdin.read()
    else:
        p = Path(caminho)
        if not p.is_file():
            raise SystemExit(EXIT_FONTE)
        texto = p.read_text(encoding="utf-8")
    linhas = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        try:
            linhas.append(json.loads(linha))
        except json.JSONDecodeError as exc:
            raise SystemExit("FALHOU linha %d da fonte nao e JSON: %s" % (numero, exc))
    return linhas


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agente ICP Score v1 (TRE-W5-E01-T01)")
    p.add_argument("--fonte", help="arquivo jsonl com as organizacoes a pontuar ('-' = stdin)")
    p.add_argument("--ambiente", help="dev | homolog (prod e recusado)")
    p.add_argument("--prefixo", default=None,
                   help="prefixo psql do ambiente (ex.: 'docker exec -i pg-sales-dev psql -U ..')")
    p.add_argument("--planejar", action="store_true", help="calcula da fonte; NAO toca o banco")
    p.add_argument("--relatorio", help="caminho do relatorio JSON da rodada")
    p.add_argument("--correlation-id", default=None)
    p.add_argument("--desfazer", metavar="CORRELATION_ID", default=None)
    p.add_argument("--confirmo", action="store_true", help="aplica o desfazer (padrao e dry-run)")
    p.add_argument("--raiz", default=str(RAIZ_PADRAO), help="raiz do repo (contratos)")
    return p


def main(argv=None) -> int:
    args = montar_parser().parse_args(argv)
    if not args.fonte and not args.desfazer:
        print("uso: --fonte <arquivo.jsonl> [--ambiente <amb>] [--planejar] | --desfazer <cid>")
        return EXIT_USO
    porta = None
    if not args.planejar:
        try:
            porta = PortaPsql(args.prefixo)
        except PortaIndisponivel as exc:
            print("FALHOU %s" % exc)
            return EXIT_USO
    try:
        agente = IcpScore(porta=porta, raiz=args.raiz, ambiente=args.ambiente,
                          correlation_id=args.correlation_id)
    except (ModeloInvalido, ValueError) as exc:
        print("FALHOU contrato do agente invalido: %s" % exc)
        return EXIT_FALHOU
    # O ambiente e exigido em todo modo que ESCREVE. No modo de planejamento (que nao abre
    # conexao nenhuma) ele e opcional: se declarado, e conferido; se ausente, nada e escrito.
    if args.ambiente or not args.planejar:
        try:
            agente.conferir_ambiente()
        except RecusaDeAmbiente as exc:
            print("RECUSADO_AMBIENTE %s" % exc)
            return EXIT_RECUSOU_AMBIENTE
    try:
        if args.desfazer:
            agente.identificar_alvo()
            relatorio = agente.desfazer(args.desfazer, confirmo=args.confirmo)
        else:
            linhas = ler_linhas(args.fonte)
            if args.planejar:
                relatorio = agente.rodar(linhas, planejar=True)
            else:
                agente.identificar_alvo()
                relatorio = agente.rodar(linhas)
    except PortaIndisponivel as exc:
        print("FALHOU %s" % exc)
        return EXIT_FALHOU
    if args.relatorio:
        Path(args.relatorio).write_text(json.dumps(relatorio, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps({"agente": relatorio.get("agente"), "modelo": relatorio.get("modelo"),
                      "ambiente": relatorio.get("ambiente"),
                      "correlation_id": relatorio["correlation_id"],
                      "alvo": relatorio.get("alvo"), "total": relatorio.get("total"),
                      "por_veredito": relatorio.get("por_veredito"),
                      "apagados": relatorio.get("apagados"), "dry_run": relatorio.get("dry_run")},
                     ensure_ascii=False, sort_keys=True))
    for r in relatorio.get("resultados", []):
        print("  %-20s %-38s score=%s %s" % (
            r["veredito"], (r.get("organization_id") or "-")[:38], r.get("score_value"),
            ",".join(r.get("motivos") or [])))
    if any(r["veredito"] == VER_ERRO for r in relatorio.get("resultados", [])):
        return EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
