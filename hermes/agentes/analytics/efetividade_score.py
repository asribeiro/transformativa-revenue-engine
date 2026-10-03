#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score effectiveness (`efetividade-score-v1`) — card TRE-W8-E03-T01 (W8 / Analytics).

O que este componente FAZ (e so' isto): MEDE a efetividade do score — o score esta' funcionando? —
lendo a base canonica de `sales_intelligence` em LEITURA PURA e cruzando o PRIORITY de cada
organizacao com o DESFECHO dela no funil. Responde quatro perguntas, cada uma com numero proprio:

  1. COBERTURA — quantas organizacoes vivas tem PRIORITY valido; o que ficou sem score, sem versao
     e vencido (lacuna nomeada, nunca preenchida por suposicao).
  2. EFETIVIDADE POR FAIXA — por faixa do Data Contract (`A+ A B C Nurture`), taxa de avanco no
     funil (endpoint principal: alcancou `Reunião`), Won/Lost, taxa de vitoria e LIFT contra a
     taxa-base; mais a monotonicidade (a ordem das faixas SEPARA os melhores?) e a suficiencia de
     base (faixa com amostra pequena nao sustenta conclusao — o numero viaja com o aviso).
  3. ADESAO A FORMULA — o PRIORITY ARMAZENADO bate com `SUM(peso * componente)` da formula V1,
     com os pesos LIDOS do Data Contract? Divergencia e' medida, nao silenciada.
  4. EFETIVIDADE POR COMPONENTE — ICP, AUTOMATION_FIT, BUYING_SIGNAL e DATA_QUALITY separam
     desfecho? Quartis por posto, Q1 (maiores valores) contra Q4, com lift.

Invariantes (cada um com item de suite/aceite):

  1. LEITURA PURA, NAO E' PREFERENCIA: toda consulta roda com `default_transaction_read_only = on`
     (o proprio PostgreSQL recusa escrita) e a auditoria da fonte reprova verbo de escrita ANTES de
     qualquer conexao (`ESCRITA_NO_CODIGO`, exit 3). Nao cria tabela, coluna, score nem tier.
  2. NAO RECALIBRA: medir efetividade NAO e' mudar peso. Recalibracao e' W9-E01-T01 e exige versao
     nova do contrato + aprovacao humana (§10 / ADR-0004). Aqui nenhum peso e' proposto.
  3. NAO INVENTA NUMERO: organizacao sem PRIORITY nao recebe faixa (senao ela entraria na fila de
     baixa prioridade por falta de dado); faixa, pesos e escala sao LIDOS de
     `docs/data/data_contract_v1.json#scores` — contrato incoerente (faixa com lacuna/sobreposicao,
     peso que nao soma 1, score_type desconhecido) RECUSA antes de ler o banco.
  4. DESFECHO VEM DO PAI, NAO DE COPIA: o alcance por organizacao sai de `alcance_por_organizacao`
     do `funil.py` (card W8-E01-T01), importado. Reimplementar o funil daria duas verdades para o
     mesmo numero; o sha256 do contrato do pai viaja no relatorio.
  5. SCORE VENCIDO NAO CLASSIFICA: `valid_until` no passado (politica de 30 dias nascida no
     W5-E05) nao sustenta faixa — vai para lacuna, como no tiering.
  6. DETERMINISMO: a mesma base com o mesmo contrato e a mesma referencia temporal produz o MESMO
     relatorio; `gerado_em` e `referencia_temporal` sao a unica diferenca e NAO entram no hash.
  7. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<...> psql`); prefixo remoto RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige
     `--confirmo`; `prod` RECUSA por desenho (exit 4).
  8. PRIVACIDADE: a saida carrega CONTAGEM e o UUID canonico da organizacao. O componente nao le
     (nem seleciona) e-mail, telefone, WhatsApp, CNPJ ou nome.

Saida: relatorio JSON + HTML auto-contido. Exit code:
  0 = relatorio gerado (ou plano/conferencia, sem banco) · 2 = uso errado · 3 = recusa
  (contrato/dependencia/fonte/guarda/banco) · 4 = producao recusada · 5 = segredo vazado.
"""

import argparse
import hashlib
import html as _html
import importlib.util
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

VERSAO = "efetividade-score-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "efetividade-score-v1.json")
FUNIL_PADRAO = os.path.join(AQUI, "funil.py")
CONTRATO_FUNIL_PADRAO = os.path.join(AQUI, "funil-v1.json")
VERSAO_FUNIL_EXIGIDA = "funil-v1"
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5
COMPONENTES_ESPERADOS = ("ICP", "AUTOMATION_FIT", "BUYING_SIGNAL", "DATA_QUALITY")
DECIMAL_DO_ZERO = Decimal("0.01")  # escala do Data Contract: NUMERIC(5,2)


class Recusa(Exception):
    def __init__(self, motivo, detalhe="", codigo=3):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe
        self.codigo = codigo


# --------------------------------------------------------------------------------------------
# 1. Contratos e dependencia
# --------------------------------------------------------------------------------------------
def carregar_json(caminho, motivo_ausente, motivo_ilegivel):
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise Recusa(motivo_ausente, caminho)
    except json.JSONDecodeError as exc:
        raise Recusa(motivo_ilegivel, str(exc))


def carregar_contrato(caminho=None):
    return carregar_json(caminho or CONTRATO_PADRAO, "CONTRATO_AUSENTE", "CONTRATO_ILEGIVEL")


def sha256_de_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def carregar_dependencia_funil(caminho_funil, caminho_contrato_funil):
    """A dependencia e' DECLARADA e CONFERIDA: sem `alcance_por_organizacao` nao ha' desfecho."""
    if not os.path.exists(caminho_funil):
        raise Recusa("DEPENDENCIA_AUSENTE", "funil.py do card W8-E01-T01 nao encontrado em %s" % caminho_funil)
    spec = importlib.util.spec_from_file_location("funil_dependencia", caminho_funil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONTRATO_PADRAO = caminho_contrato_funil
    contrato_funil = carregar_json(caminho_contrato_funil, "CONTRATO_DO_FUNIL_AUSENTE", "CONTRATO_DO_FUNIL_ILEGIVEL")
    if contrato_funil.get("versao") != VERSAO_FUNIL_EXIGIDA:
        raise Recusa("DEPENDENCIA_VERSAO_INCOMPATIVEL",
                     "esperado %s, obtido %s" % (VERSAO_FUNIL_EXIGIDA, contrato_funil.get("versao")))
    for exigido in ("alcance_por_organizacao", "montar_consultas", "extrair_base",
                    "resolver_evidencia", "executar_consulta", "auditar_fonte"):
        if not hasattr(mod, exigido):
            raise Recusa("DEPENDENCIA_INCOMPATIVEL", "funil.py sem `%s`" % exigido)
    return mod, contrato_funil


def validar_contrato(contrato, dados, contrato_funil):
    """Contrato do componente x Data Contract V1 x contrato do funil. Contrato incoerente RECUSA."""
    if contrato.get("versao") != VERSAO:
        raise Recusa("VERSAO_DO_CONTRATO_DESCONHECIDA", str(contrato.get("versao")))
    scores = (dados.get("scores") or {})
    tipos = list(scores.get("types") or [])
    if "PRIORITY" not in tipos:
        raise Recusa("CONTRATO_DE_DADOS_SEM_PRIORITY", str(tipos))
    for comp in COMPONENTES_ESPERADOS:
        if comp not in tipos:
            raise Recusa("COMPONENTE_FORA_DO_CONTRATO_DE_DADOS", comp)

    pesos = {k: Decimal(str(v)) for k, v in (scores.get("priority_weights") or {}).items()}
    if set(pesos) != set(COMPONENTES_ESPERADOS):
        raise Recusa("PESOS_DIVERGEM_DO_CONTRATO",
                     "componentes=%s pesos=%s" % (sorted(COMPONENTES_ESPERADOS), sorted(pesos)))
    if sum(pesos.values()) != Decimal("1.00"):
        raise Recusa("PESOS_NAO_SOMAM_1", str(sum(pesos.values())))

    faixas = list(scores.get("tiers") or [])
    if len(faixas) < 2:
        raise Recusa("FAIXAS_INSUFICIENTES", str(faixas))
    nomes = [f.get("name") for f in faixas]
    if any(not n for n in nomes) or len(set(nomes)) != len(nomes):
        raise Recusa("FAIXA_SEM_NOME_OU_DUPLICADA", str(nomes))
    limites = {}
    for f in faixas:
        if f.get("min") is None or f.get("max") is None:
            raise Recusa("FAIXA_SEM_LIMITE", json.dumps(f, ensure_ascii=False))
        limites[f["name"]] = (Decimal(str(f["min"])), Decimal(str(f["max"])))
    ordenadas = sorted(faixas, key=lambda f: limites[f["name"]][0])
    escala_min = limites[ordenadas[0]["name"]][0]
    escala_max = limites[ordenadas[-1]["name"]][1]
    for atual, seguinte in zip(ordenadas, ordenadas[1:]):
        fim_atual = limites[atual["name"]][1]
        inicio_seguinte = limites[seguinte["name"]][0]
        if inicio_seguinte - fim_atual != DECIMAL_DO_ZERO:
            raise Recusa(
                "FAIXAS_NAO_COBREM_ESCALA",
                "%s max=%s + %s != %s min=%s" % (atual["name"], fim_atual, DECIMAL_DO_ZERO,
                                                 seguinte["name"], inicio_seguinte),
            )
    if any(limites[n][0] > limites[n][1] for n in limites):
        raise Recusa("FAIXA_INVERTIDA", json.dumps(limites, default=str))

    if (contrato_funil.get("estagios") or []) == []:
        raise Recusa("CONTRATO_DO_FUNIL_SEM_ESTAGIOS")
    declaradas = list((contrato.get("fontes_do_funil") or []))
    vigentes = sorted((contrato_funil.get("fontes") or {}).keys())
    if sorted(declaradas) != vigentes:
        raise Recusa("FONTES_DO_FUNIL_DIVERGEM",
                     "declaradas=%s vigentes=%s" % (sorted(declaradas), vigentes))
    return {"faixas": [f["name"] for f in reversed(ordenadas)],  # MELHOR FAIXA PRIMEIRO (A+ ... Nurture):
            "limites": limites, "pesos": pesos,                  # a ordem e' a da leitura comercial, e
            "escala": {"minimo": str(escala_min),                # a monotonicidade se confere nela
                       "maximo": str(escala_max)}}


# --------------------------------------------------------------------------------------------
# 2. Guardas de ambiente e auditoria da fonte (mesmo mecanismo do funil — reusado)
# --------------------------------------------------------------------------------------------
def validar_ambiente(funil, ambiente, porta_banco=None, confirmo=False):
    return funil.validar_ambiente(ambiente, porta_banco, confirmo)


def recusar_producao():
    raise Recusa(
        "PRODUCAO_RECUSADA",
        "nada nasce em producao (ADR-005); medir/ler em producao e' ato de operador com aprovacao registrada",
        codigo=4,
    )


def montar_consultas_proprias(contrato, desde=None, ate=None):
    """Fontes PROPRIAS deste componente (PRIORITY e componentes). O resto do desfecho e' do funil."""
    declaradas = dict(contrato.get("fontes_proprias") or {})
    q = {}
    q["PRIORITY_ULTIMO"] = (
        "SELECT DISTINCT ON (organization_id) organization_id::text || '|' || id::text || '|' || "
        "score_value::text || '|' || COALESCE(score_version, '') || '|' || "
        "to_char(calculated_at, 'YYYY-MM-DD\"T\"HH24:MI:SSOF') || '|' || "
        "COALESCE(to_char(valid_until, 'YYYY-MM-DD\"T\"HH24:MI:SSOF'), '') || '|' || "
        "(SELECT count(*)::text FROM sales_intelligence.scores h "
        "WHERE h.organization_id = s.organization_id AND h.score_type = 'PRIORITY') "
        "FROM sales_intelligence.scores s WHERE score_type = 'PRIORITY'"
        + _janela("calculated_at", desde, ate)
        + " ORDER BY organization_id, calculated_at DESC, id DESC"
    )
    q["COMPONENTES_ULTIMOS"] = (
        "SELECT DISTINCT ON (organization_id, score_type) organization_id::text || '|' || score_type "
        "|| '|' || score_value::text || '|' || COALESCE(score_version, '') || '|' || "
        "COALESCE(to_char(valid_until, 'YYYY-MM-DD\"T\"HH24:MI:SSOF'), '') "
        "FROM sales_intelligence.scores WHERE score_type IN ('ICP', 'AUTOMATION_FIT', 'BUYING_SIGNAL', "
        "'DATA_QUALITY')"
        + _janela("calculated_at", desde, ate)
        + " ORDER BY organization_id, score_type, calculated_at DESC, id DESC"
    )
    if sorted(q.keys()) != sorted(declaradas.keys()):
        raise Recusa("FONTES_PROPRIAS_DIVERGEM_DO_CONTRATO",
                     "componente=%s contrato=%s" % (sorted(q.keys()), sorted(declaradas.keys())))
    return q


def _janela(coluna, desde, ate):
    partes = ""
    if desde:
        partes += " AND %s >= '%s'" % (coluna, desde)
    if ate:
        partes += " AND %s <= '%s'" % (coluna, ate)
    return partes


# --------------------------------------------------------------------------------------------
# 3. Leitura (pura) e derivacao
# --------------------------------------------------------------------------------------------
def _decimal(texto, contexto):
    try:
        return Decimal(str(texto).strip())
    except Exception:  # noqa: BLE001 — valor ilegivel recusa, nunca vira zero
        raise Recusa("VALOR_ILEGIVEL", "%s=%r" % (contexto, texto))


def _instante(texto):
    if not texto:
        return None
    texto = texto.strip().replace(" ", "T")
    if re.match(r"^.*[+-]\d{2}$", texto):  # psql OF: +00
        texto += ":00"
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def extrair_prioridade(linhas):
    """PRIORITY mais recente por organizacao (o mesmo recorte que o tiering usa)."""
    por_org = {}
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) < 7 or not campos[0]:
            continue
        org, score_id, valor, versao, calculado, vence, historico = campos[:7]
        por_org[org] = {
            "score_id": score_id,
            "valor": _decimal(valor, "PRIORITY.score_value"),
            "versao": (versao or "").strip(),
            "calculado_em": calculado.strip(),
            "vence_em": (vence or "").strip(),
            "historico": int(historico or 0),
        }
    return por_org


def extrair_componentes(linhas):
    por_org = {}
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) < 5 or not campos[0]:
            continue
        org, tipo, valor, versao, vence = campos[:5]
        por_org.setdefault(org, {})[tipo.strip()] = {
            "valor": _decimal(valor, "%s.score_value" % tipo),
            "versao": (versao or "").strip(),
            "vence_em": (vence or "").strip(),
        }
    return por_org


def _vencido(vence_em, referencia):
    if not vence_em:
        return False
    instante = _instante(vence_em)
    return instante is not None and instante <= referencia


def faixa_do_valor(limites, faixas_ordenadas, valor):
    for nome in faixas_ordenadas:
        minimo, maximo = limites[nome]
        if minimo <= valor <= maximo:
            return nome
    return None


def _pct(numerador, denominador):
    if not denominador:
        return None
    return round(100.0 * numerador / denominador, 2)


def _lift(taxa, base):
    if taxa is None or base in (None, 0):
        return None
    return round(float(taxa) / float(base), 2)


def _digest(obj):
    canonico = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]


def derivar(contrato, dados, contrato_funil, funil, brutas_funil, brutas_proprias, referencia,
            ambiente, janela, gerado_em=None, sha_contrato=None, sha_contrato_funil=None):
    """Derivacao PURA (sem banco): recebe as linhas cruas e devolve o relatorio."""
    validado = validar_contrato(contrato, dados, contrato_funil)
    faixas = validado["faixas"]
    limites = validado["limites"]
    pesos = validado["pesos"]
    escala = validado["escala"]
    minimo_por_faixa = int((contrato.get("parametros") or {}).get("minimo_por_faixa", 5))
    tolerancia = Decimal(str((contrato.get("parametros") or {}).get("tolerancia_de_adesao", "0.01")))
    endpoint = contrato.get("parametros", {}).get("endpoint_principal") or {}
    endpoints = contrato.get("parametros", {}).get("endpoints") or []

    organizacoes = funil.extrair_base(brutas_funil.get("BASE_ORGANIZACOES", []))
    evidencia, _lacunas_funil = funil.resolver_evidencia(contrato_funil, brutas_funil, organizacoes)
    alcance = funil.alcance_por_organizacao(contrato_funil, organizacoes, evidencia)
    prioridade = extrair_prioridade(brutas_proprias.get("PRIORITY_ULTIMO", []))
    componentes = extrair_componentes(brutas_proprias.get("COMPONENTES_ULTIMOS", []))

    lacunas = {
        "prioridade_ausente": 0, "prioridade_sem_versao": 0, "prioridade_vencida": 0,
        "prioridade_fora_da_escala": 0, "faixa_nao_atribuida": 0,
        "historico_ignorado": {"organizacoes": 0, "linhas": 0},
        "componente_ausente": {}, "componente_sem_versao": {}, "componente_vencido": {},
        "componente_fora_da_escala": {}, "versoes_divergentes": 0, "evidencia_orfa": 0,
    }
    escala_min = Decimal(escala["minimo"])
    escala_max = Decimal(escala["maximo"])

    validos = {}
    for org in organizacoes:
        linha = prioridade.get(org)
        if linha is None:
            lacunas["prioridade_ausente"] += 1
            continue
        if not linha["versao"]:
            lacunas["prioridade_sem_versao"] += 1
            continue
        if _vencido(linha["vence_em"], referencia):
            lacunas["prioridade_vencida"] += 1
            continue
        if not (escala_min <= linha["valor"] <= escala_max):
            lacunas["prioridade_fora_da_escala"] += 1
            continue
        faixa = faixa_do_valor(limites, faixas, linha["valor"])
        if faixa is None:
            lacunas["faixa_nao_atribuida"] += 1
            continue
        linha["faixa"] = faixa
        validos[org] = linha
        if linha["historico"] > 1:
            lacunas["historico_ignorado"]["organizacoes"] += 1
            lacunas["historico_ignorado"]["linhas"] += linha["historico"] - 1
    for org in prioridade:
        if org not in organizacoes:
            lacunas["evidencia_orfa"] += 1

    # --- cobertura -------------------------------------------------------------------------
    total = len(organizacoes)
    cobertura = {
        "organizacoes": total,
        "com_priority_valido": len(validos),
        "taxa_pct": _pct(len(validos), total),
    }

    # --- endpoint: nivel do alcance que conta como avanco -----------------------------------
    def atingiu(org, ep):
        info = alcance.get(org) or {}
        if ep.get("rotulo"):
            return ep["rotulo"] in (info.get("rotulos") or [])
        nivel = info.get("nivel")
        return nivel is not None and nivel >= int(ep["nivel_minimo"])

    def bloco(endpoint_ep, orgs):
        atingiram = sum(1 for o in orgs if atingiu(o, endpoint_ep))
        return {"nome": endpoint_ep["nome"], "atingiram": atingiram,
                "taxa_pct": _pct(atingiram, len(orgs))}

    coorte = sorted(validos)
    base_endpoints = {ep["nome"]: _pct(sum(1 for o in coorte if atingiu(o, ep)), len(coorte))
                      for ep in endpoints}

    por_faixa = []
    for nome in faixas:
        orgs = [o for o in coorte if validos[o]["faixa"] == nome]
        ganharam = sum(1 for o in orgs if "Won" in ((alcance.get(o) or {}).get("rotulos") or []))
        perderam = sum(1 for o in orgs if "Lost" in ((alcance.get(o) or {}).get("rotulos") or []))
        blocos = []
        for ep in endpoints:
            b = bloco(ep, orgs)
            b["lift"] = _lift(b["taxa_pct"], base_endpoints[ep["nome"]])
            blocos.append(b)
        principal = next((b for b in blocos if b["nome"] == endpoint["nome"]), None)
        por_faixa.append({
            "faixa": nome,
            "min": str(limites[nome][0]),
            "max": str(limites[nome][1]),
            "organizacoes": len(orgs),
            "endpoints": blocos,
            "avanco_pct": (principal or {}).get("taxa_pct"),
            "lift_avanco": (principal or {}).get("lift"),
            "ganharam": ganharam,
            "perderam": perderam,
            "em_aberto": len(orgs) - ganharam - perderam,
            "taxa_de_vitoria_pct": _pct(ganharam, ganharam + perderam),
            "base_suficiente": len(orgs) >= minimo_por_faixa,
        })

    violacoes = []
    com_base = [f for f in por_faixa if f["organizacoes"] > 0 and f["avanco_pct"] is not None]
    for atual, seguinte in zip(com_base, com_base[1:]):
        if atual["avanco_pct"] < seguinte["avanco_pct"]:
            violacoes.append({
                "esperado": "%s >= %s" % (atual["faixa"], seguinte["faixa"]),
                "obtido": "%s=%s < %s=%s" % (atual["faixa"], atual["avanco_pct"],
                                             seguinte["faixa"], seguinte["avanco_pct"]),
            })
    ranqueadas = [f for f in com_base]
    melhor = max(ranqueadas, key=lambda f: f["avanco_pct"], default=None)
    pior = min(ranqueadas, key=lambda f: f["avanco_pct"], default=None)
    resumo = {
        "coorte": len(coorte),
        "endpoint_principal": endpoint.get("nome"),
        "taxa_base_avanco_pct": base_endpoints.get(endpoint.get("nome")),
        "monotonico": not violacoes,
        "violacoes": violacoes,
        "melhor_faixa": (melhor or {}).get("faixa"),
        "melhor_faixa_avanco_pct": (melhor or {}).get("avanco_pct"),
        "pior_faixa": (pior or {}).get("faixa"),
        "pior_faixa_avanco_pct": (pior or {}).get("avanco_pct"),
        "amplitude_pct": (round(melhor["avanco_pct"] - pior["avanco_pct"], 2)
                          if melhor and pior and melhor["avanco_pct"] is not None
                          and pior["avanco_pct"] is not None else None),
        "faixas_com_base_suficiente": [f["faixa"] for f in por_faixa if f["base_suficiente"]],
        "faixas_sem_base_suficiente": [f["faixa"] for f in por_faixa if not f["base_suficiente"]],
        "base_suficiente_para_conclusao": bool(por_faixa) and all(f["base_suficiente"] for f in por_faixa),
    }

    # --- adesao a formula -------------------------------------------------------------------
    comparaveis, conformes, divergentes = 0, 0, 0
    desvios = []
    exemplos = []
    for org in coorte:
        linha = validos[org]
        dobra = componentes.get(org) or {}
        faltando = [c for c in COMPONENTES_ESPERADOS if c not in dobra]
        if faltando:
            for c in faltando:
                lacunas["componente_ausente"][c] = lacunas["componente_ausente"].get(c, 0) + 1
            continue
        recusa = None
        for c in COMPONENTES_ESPERADOS:
            item = dobra[c]
            if not item["versao"]:
                lacunas["componente_sem_versao"][c] = lacunas["componente_sem_versao"].get(c, 0) + 1
                recusa = "sem versao"
            elif _vencido(item["vence_em"], referencia):
                lacunas["componente_vencido"][c] = lacunas["componente_vencido"].get(c, 0) + 1
                recusa = "vencido"
            elif not (escala_min <= item["valor"] <= escala_max):
                lacunas["componente_fora_da_escala"][c] = lacunas["componente_fora_da_escala"].get(c, 0) + 1
                recusa = "fora da escala"
            elif item["versao"] != linha["versao"]:
                recusa = "versao"
        if recusa == "versao":
            lacunas["versoes_divergentes"] += 1
            continue
        if recusa:
            continue
        esperado = sum(pesos[c] * dobra[c]["valor"] for c in COMPONENTES_ESPERADOS)
        esperado = esperado.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        delta = abs(esperado - linha["valor"])
        comparaveis += 1
        desvios.append(delta)
        if delta <= tolerancia:
            conformes += 1
        else:
            divergentes += 1
            if len(exemplos) < 5:
                exemplos.append({"organization_id": org, "faixa": linha["faixa"],
                                 "armazenado": str(linha["valor"]), "recalculado": str(esperado),
                                 "delta": str(delta), "score_version": linha["versao"]})
    desvio_medio = (sum(desvios) / len(desvios)) if desvios else None
    adesao = {
        "comparaveis": comparaveis,
        "conformes": conformes,
        "divergentes": divergentes,
        "taxa_de_conformidade_pct": _pct(conformes, comparaveis),
        "tolerancia": str(tolerancia),
        "desvio_maximo": str(max(desvios)) if desvios else None,
        "desvio_medio": (str(desvio_medio.quantize(Decimal("0.0001"))) if desvio_medio is not None else None),
        "pesos_usados": {c: str(pesos[c]) for c in COMPONENTES_ESPERADOS},
        "exemplos_de_divergencia": exemplos,
        "fonte": "docs/data/data_contract_v1.json#scores.priority_weights (peso no codigo e' reprovado)",
    }

    # --- efetividade por componente (quartis por posto) -----------------------------------
    quartis_n = int((contrato.get("parametros") or {}).get("quartis", 4))
    por_componente = []
    for comp in COMPONENTES_ESPERADOS:
        elegiveis = []
        for org in sorted(organizacoes):
            item = (componentes.get(org) or {}).get(comp)
            if item is None or not item["versao"] or _vencido(item["vence_em"], referencia):
                continue
            if not (escala_min <= item["valor"] <= escala_max):
                continue
            elegiveis.append((item["valor"], org))
        elegiveis.sort(key=lambda par: (-par[0], par[1]))
        grupos = [list() for _ in range(quartis_n)]
        n = len(elegiveis)
        for indice, (valor, org) in enumerate(elegiveis):
            grupos[min(quartis_n - 1, indice * quartis_n // n) if n else 0].append(org)
        linhas_q = []
        for q, orgs in enumerate(grupos, start=1):
            b = bloco(endpoint, orgs)
            linhas_q.append({"quartil": "Q%d" % q, "organizacoes": len(orgs),
                             "atingiram": b["atingiram"], "taxa_pct": b["taxa_pct"]})
        q1 = linhas_q[0]["taxa_pct"] if linhas_q else None
        q4 = linhas_q[-1]["taxa_pct"] if linhas_q else None
        por_componente.append({
            "score_type": comp,
            "comparaveis": n,
            "endpoint": endpoint.get("nome"),
            "quartis": linhas_q,
            "lift_q1_vs_q4": _lift(q1, q4),
            "base_suficiente": n >= quartis_n,
        })

    relatorio = {
        "versao": VERSAO,
        "card": contrato.get("card"),
        "ambiente": ambiente,
        "janela": dict(janela or {"desde": None, "ate": None}),
        "referencia_temporal": referencia.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gerado_em": gerado_em or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contrato": {"versao": contrato["versao"],
                     "sha256": sha_contrato or sha256_de_arquivo(CONTRATO_PADRAO)},
        "dependencia": {"componente": "funil.py (card W8-E01-T01)",
                        "contrato": contrato_funil.get("versao"),
                        "sha256_do_contrato": sha_contrato_funil or "",
                        "funcao_do_desfecho": "alcance_por_organizacao"},
        "base": {"organizacoes": total, "digest": _digest({"organizacoes": sorted(organizacoes)})},
        "parametros": {"endpoint_principal": endpoint.get("nome"),
                       "endpoints": [ep["nome"] for ep in endpoints],
                       "minimo_por_faixa": minimo_por_faixa,
                       "tolerancia_de_adesao": str(tolerancia),
                       "quartis": quartis_n,
                       "escala": escala,
                       "faixas": faixas},
        "cobertura": cobertura,
        "efetividade_por_faixa": por_faixa,
        "resumo": resumo,
        "adesao_a_formula": adesao,
        "por_componente": por_componente,
        "lacunas": lacunas,
        "lacunas_declaradas": contrato.get("lacunas_declaradas", []),
        "fontes": {},
    }
    relatorio["fontes"] = {fid: len(linhas) for fid, linhas in sorted(
        dict(brutas_funil, **brutas_proprias).items())}
    relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
    return relatorio


def hash_do_relatorio(relatorio):
    limpo = {k: v for k, v in relatorio.items()
             if k not in ("gerado_em", "referencia_temporal", "hash_do_relatorio")}
    canonico = json.dumps(limpo, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------
# 4. Dashboard (HTML auto-contido: uma string, CSS inline, nenhum recurso externo)
# --------------------------------------------------------------------------------------------
def emitir_html(relatorio):
    def tabela(cabecalho, linhas, classe_extra=""):
        th = "".join("<th>%s</th>" % _html.escape(c) for c in cabecalho)
        tr = "".join("<tr>%s</tr>" % "".join(
            '<td class="n">%s</td>' % _html.escape(str(c)) if i else "<td>%s</td>" % _html.escape(str(c))
            for i, c in enumerate(linha)) for linha in linhas)
        return ('<table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
                % (classe_extra, th, tr))

    resumo = relatorio["resumo"]
    adesao = relatorio["adesao_a_formula"]
    faixas = [[f["faixa"], f["organizacoes"], "-" if f["avanco_pct"] is None else "%.2f%%" % f["avanco_pct"],
               "-" if f["lift_avanco"] is None else "%.2fx" % f["lift_avanco"],
               f["ganharam"], f["perderam"],
               "-" if f["taxa_de_vitoria_pct"] is None else "%.2f%%" % f["taxa_de_vitoria_pct"],
               "sim" if f["base_suficiente"] else "nao"] for f in relatorio["efetividade_por_faixa"]]
    comps = [[c["score_type"], c["comparaveis"]]
             + ["-" if q["taxa_pct"] is None else "%.2f%%" % q["taxa_pct"] for q in c["quartis"]]
             + ["-" if c["lift_q1_vs_q4"] is None else "%.2fx" % c["lift_q1_vs_q4"]] for c in relatorio["por_componente"]]
    lacunas = [[k, json.dumps(v, ensure_ascii=False)] for k, v in sorted(relatorio["lacunas"].items())
               if v not in (0, {}, None, {"organizacoes": 0, "linhas": 0})]
    viol = "".join("<li>%s</li>" % _html.escape(v["obtido"]) for v in resumo["violacoes"])
    return (
        "<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Efetividade do score — TRE-W8-E03-T01</title>\n<style>\n"
        "body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#111}\n"
        "h1{font-size:20px;margin:0 0 4px 0}\nh2{font-size:15px;margin:22px 0 6px 0}\n"
        ".meta{color:#555;font-size:12px;margin-bottom:16px}\n"
        "table{border-collapse:collapse;width:100%%;font-size:13px}\n"
        "th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}\n"
        "td.n{text-align:right;font-variant-numeric:tabular-nums}\n"
        ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 6px 0}\n"
        ".card{border:1px solid #ddd;border-radius:6px;padding:8px 12px;min-width:120px}\n"
        ".card b{display:block;font-size:20px}\n.lacunas{font-size:12px;color:#555}\n"
        "</style>\n</head>\n<body>\n"
        "<h1>Efetividade do score — Transformativa Revenue Engine</h1>\n"
        "<div class=\"meta\">card %s · contrato %s (sha256 %s) · desfecho por %s (contrato %s) · "
        "ambiente %s · janela %s · referencia %s · gerado em %s · hash do relatorio %s</div>\n"
        % (_html.escape(str(relatorio.get("card"))), _html.escape(relatorio["contrato"]["versao"]),
           _html.escape(relatorio["contrato"]["sha256"][:16]),
           _html.escape(relatorio["dependencia"]["funcao_do_desfecho"]),
           _html.escape(str(relatorio["dependencia"]["contrato"])),
           _html.escape(relatorio["ambiente"]), _html.escape(str(relatorio.get("janela"))),
           _html.escape(relatorio["referencia_temporal"]), _html.escape(relatorio["gerado_em"]),
           _html.escape(relatorio["hash_do_relatorio"][:16]))
        + "<div class=\"cards\">"
        + "".join("<div class=\"card\"><b>%s</b>%s</div>" % (_html.escape(str(v)), _html.escape(k))
                  for k, v in [("organizações", relatorio["cobertura"]["organizacoes"]),
                               ("com PRIORITY válido", relatorio["cobertura"]["com_priority_valido"]),
                               ("cobertura", "%s%%" % relatorio["cobertura"]["taxa_pct"]),
                               ("taxa-base de avanço", "%s%%" % resumo["taxa_base_avanco_pct"]),
                               ("adesão à fórmula", "%s%%" % adesao["taxa_de_conformidade_pct"]),
                               ("faixas monotônicas", "sim" if resumo["monotonico"] else "não")])
        + "</div>\n<h2>Efetividade por faixa (endpoint: %s)</h2>\n" % _html.escape(str(resumo["endpoint_principal"]))
        + tabela(["Faixa", "Organizações", "Avanço", "Lift", "Won", "Lost", "Taxa de vitória", "Base suficiente"], faixas)
        + ("<h2>Violações de monotonicidade</h2>\n<ul class=\"lacunas\">%s</ul>\n" % viol if viol else
           "<p class=\"lacunas\">Ordem das faixas separa o desfecho (sem violação de monotonicidade).</p>\n")
        + "<h2>Adesão à fórmula (PRIORITY armazenado x recalculado)</h2>\n"
        + tabela(["Comparáveis", "Conformes", "Divergentes", "Conformidade", "Desvio máximo", "Desvio médio"],
                 [[adesao["comparaveis"], adesao["conformes"], adesao["divergentes"],
                   "-" if adesao["taxa_de_conformidade_pct"] is None else "%.2f%%" % adesao["taxa_de_conformidade_pct"],
                   adesao["desvio_maximo"] or "-", adesao["desvio_medio"] or "-"]])
        + "<h2>Efetividade por componente (quartis; Q1 = maiores valores)</h2>\n"
        + tabela(["Score", "Comparáveis", "Q1", "Q2", "Q3", "Q4", "Lift Q1/Q4"], comps)
        + "<h2>Lacunas medidas</h2>\n<ul class=\"lacunas\">"
        + "".join("<li>%s: %s</li>" % (_html.escape(k), _html.escape(v)) for k, v in lacunas)
        + "</ul>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------------------------
# 5. Leitura do banco (pura) e montagem
# --------------------------------------------------------------------------------------------
def ler_tudo(funil, contrato, contrato_funil, porta_banco, desde, ate):
    consultas_funil = funil.montar_consultas(contrato_funil, desde, ate)
    consultas_proprias = montar_consultas_proprias(contrato, desde, ate)
    # Auditoria da fonte ANTES de qualquer conexao: so' SELECT (o funil ja' audita as dele).
    funil.auditar_fonte(consultas_funil)
    funil.auditar_fonte(consultas_proprias)
    brutas_funil = {fid: funil.executar_consulta(porta_banco, sql) for fid, sql in consultas_funil.items()}
    brutas_proprias = {fid: funil.executar_consulta(porta_banco, sql) for fid, sql in consultas_proprias.items()}
    return brutas_funil, brutas_proprias


def construir_relatorio(funil, contrato, dados, contrato_funil, porta_banco, ambiente, desde, ate,
                        referencia, gerado_em=None, sha_contrato=None, sha_contrato_funil=None):
    if porta_banco is None:
        raise Recusa("PORTA_BANCO_AUSENTE", "sem banco nao ha' medicao: use --planejar/--conferir")
    brutas_funil, brutas_proprias = ler_tudo(funil, contrato, contrato_funil, porta_banco, desde, ate)
    return derivar(contrato, dados, contrato_funil, funil, brutas_funil, brutas_proprias, referencia,
                   ambiente, {"desde": desde, "ate": ate}, gerado_em=gerado_em,
                   sha_contrato=sha_contrato, sha_contrato_funil=sha_contrato_funil)


def _resumo_texto(relatorio):
    resumo = relatorio["resumo"]
    return " ".join([
        "EFETIVIDADE_SCORE_OK",
        "coorte=%d" % resumo["coorte"],
        "cobertura=%s%%" % relatorio["cobertura"]["taxa_pct"],
        "base_avanco=%s%%" % resumo["taxa_base_avanco_pct"],
        "melhor=%s(%s%%)" % (resumo["melhor_faixa"], resumo["melhor_faixa_avanco_pct"]),
        "monotonico=%s" % ("sim" if resumo["monotonico"] else "nao"),
        "adesao=%s%%" % relatorio["adesao_a_formula"]["taxa_de_conformidade_pct"],
        "hash=%s" % relatorio["hash_do_relatorio"][:16],
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Efetividade do score v1 — TRE-W8-E03-T01")
    parser.add_argument("--ambiente", required=True, choices=list(AMBIENTES))
    parser.add_argument("--porta-banco", default=os.environ.get("TRE_EFETIVIDADE_PORTA_BANCO"))
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--agora", default=None, help="referencia temporal (ISO); padrao: relogio da rodada")
    parser.add_argument("--saida", default=None, help="diretorio de saida do relatorio")
    parser.add_argument("--formato", default="json,html", choices=["json", "html", "json,html"])
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--planejar", action="store_true", help="imprime o plano declarado (sem banco)")
    parser.add_argument("--conferir", action="store_true", help="valida contratos e dependencia (sem banco)")
    parser.add_argument("--contrato", default=CONTRATO_PADRAO)
    parser.add_argument("--contrato-funil", default=CONTRATO_FUNIL_PADRAO)
    parser.add_argument("--funil", default=FUNIL_PADRAO)
    parser.add_argument("--raiz", default=os.path.join(AQUI, "..", "..", ".."))
    args = parser.parse_args(argv)

    try:
        raiz = os.path.abspath(args.raiz)
        if args.ambiente == "prod":
            recusar_producao()  # antes de qualquer leitura, independentemente de contrato/base
        contrato = carregar_contrato(args.contrato)
        dados = carregar_json(os.path.join(raiz, "docs", "data", "data_contract_v1.json"),
                              "CONTRATO_DE_DADOS_AUSENTE", "CONTRATO_DE_DADOS_ILEGIVEL")
        funil, contrato_funil = carregar_dependencia_funil(args.funil, args.contrato_funil)
        validar_contrato(contrato, dados, contrato_funil)

        if args.conferir:
            print("EFETIVIDADE_CONFERIR_OK contrato=%s faixas=%d componentes=%d funil=%s" % (
                contrato["versao"], len(dados["scores"]["tiers"]), len(COMPONENTES_ESPERADOS),
                contrato_funil.get("versao")))
            return 0
        if args.planejar:
            print("EFETIVIDADE_PLANO versao=%s unidade=organization" % contrato["versao"])
            print("  faixas (do Data Contract): %s" % ", ".join(
                "%s[%s..%s]" % (f["name"], f["min"], f["max"]) for f in dados["scores"]["tiers"]))
            print("  pesos (do Data Contract): %s" % json.dumps(
                dados["scores"]["priority_weights"], ensure_ascii=False))
            print("  desfecho: funil.py -> alcance_por_organizacao (%s)" % contrato_funil.get("versao"))
            return 0

        validar_ambiente(funil, args.ambiente, args.porta_banco, args.confirmo)
        desde = funil.normalizar_instante(args.desde, "--desde")
        ate = funil.normalizar_instante(args.ate, "--ate")
        if args.agora:
            referencia = funil.normalizar_instante(args.agora, "--agora")
            referencia = datetime.fromisoformat(referencia.replace("Z", "+00:00"))
        else:
            referencia = datetime.now(timezone.utc)
        relatorio = construir_relatorio(
            funil, contrato, dados, contrato_funil, args.porta_banco, args.ambiente, desde, ate,
            referencia, sha_contrato=sha256_de_arquivo(args.contrato),
            sha_contrato_funil=sha256_de_arquivo(args.contrato_funil))

        saida_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
        saida_html = emitir_html(relatorio)
        for token_nome in ("TRE_EFETIVIDADE_TOKEN", "TRE_EFETIVIDADE_SCORE_TOKEN", "TRE_FUNIL_TOKEN"):
            token = os.environ.get(token_nome)
            if token and (token in saida_json or token in saida_html):
                raise Recusa("SENHA_VAZADA", "valor de %s presente na evidencia" % token_nome,
                             codigo=CODIGO_SEGREDO)

        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "efetividade-score.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "efetividade-score.html"), "w", encoding="utf-8") as fh:
                    fh.write(saida_html)
        print(_resumo_texto(relatorio))
        return 0
    except Recusa as exc:
        print("RECUSA %s %s" % (exc.motivo, exc.detalhe))
        return exc.codigo
    except Exception as exc:  # noqa: BLE001 — a dependencia (funil.py) tem a PROPRIA Recusa:
        motivo = getattr(exc, "motivo", None)  # ela nao pode virar traceback (exit 1) nem ser tratada
        if motivo is None:                      # como sucesso; recusa de qualquer origem sai pelo
            raise                               # codigo dela (mesma semantica de exit code)
        print("RECUSA %s %s" % (motivo, getattr(exc, "detalhe", "")))
        return getattr(exc, "codigo", 3)


if __name__ == "__main__":
    sys.exit(main())
