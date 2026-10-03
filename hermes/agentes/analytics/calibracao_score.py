#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calibracao do score (`calibracao-score-v1`) — card TRE-W9-E01-T01 (W9 / Inteligencia Avancada).

O que este componente FAZ (e so' isto): MEDE o desfecho observado na base canonica de
`sales_intelligence` e devolve, como PROPOSTA, pesos e faixas novos para o score — em LEITURA PURA.
Ele NAO aplica nada: mudar formula/peso de score e' decisao estrutural (§10 do Data Contract +
ADR-0004) e quem aplica e' operador com aprovacao humana registrada.

Tres perguntas, cada uma com numero proprio:

  1. O VOLUME SUSTENTA A CALIBRACAO? A pre-condicao declarada do card ("volume real suficiente")
     virou gate medido: coorte com desfecho resolvido, classes Won/Lost por lado do ajuste e os
     dois minimos do contrato. Sem base, o componente ABSTEVE (exit 6) e nomeia o motivo —
     base pequena NAO vira peso novo.
  2. OS PESOS DA FORMULA V1 SEPARAM O DESFECHO REGISTRADO? Busca DETERMINISTICA na grade do
     simplex (passo do contrato), metrica AUC contra Won=1/Lost=0, corte ajuste/validacao por
     sha256(organization_id) e desempate pela MENOR distancia L1 ao peso em vigor. A proposta so'
     nasce se a AUC de VALIDACAO ganhar a margem declarada — ganho so' no ajuste e' overfitting.
  3. AS FAIXAS DO DATA CONTRACT SAO AS QUE SEPARAM? Cortes de Youden aplicados recursivamente no
     grupo com mais organizacoes; as faixas saem contiguas, cobrindo 0..100, na ordem ascendente
     dos `tiers` do contrato.

Invariantes (cada um com item de suite/aceite):

  1. PROPOSTA NAO E' APLICACAO: `aplicado: false`, `exige_versao_nova: true`,
     `aprovacao_humana: pendente` viajam no relatorio; nada e' escrito no Data Contract nem no banco.
  2. LEITURA PURA NAO E' PREFERENCIA: toda consulta roda com `default_transaction_read_only = on`
     (o proprio PostgreSQL recusa escrita) e a auditoria da fonte reprova verbo de escrita ANTES de
     qualquer conexao (`ESCRITA_NO_CODIGO`, exit 3). Nao cria tabela, coluna, score nem tier.
  3. NAO REIMPLEMENTA A MEDICAO NEM O DESFECHO: a leitura, a validacao de contrato e o alcance por
     organizacao vem de `efetividade_score.py` (card W8-E03-T01) e, por ele, do `funil.py`
     (W8-E01-T01), importados e conferidos por versao e sha256. Duplicar qualquer um dos dois
     criaria duas verdades para o mesmo numero.
  4. NAO INVENTA NUMERO: organizacao sem PRIORITY valido, sem os QUATRO componentes na mesma
     versao, ou sem desfecho resolvido, fica FORA do ajuste em lacuna nomeada — nunca em zero.
  5. DETERMINISMO: a mesma base com o mesmo contrato e a mesma referencia temporal produz o MESMO
     relatorio (grade, desempate e corte ajuste/validacao sao deterministicos); `gerado_em` e
     `referencia_temporal` sao a unica diferenca e NAO entram no hash.
  6. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<...> psql`); prefixo remoto RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige
     `--confirmo`; `prod` RECUSA por desenho (exit 4).
  7. PRIVACIDADE: a saida carrega CONTAGEM e o UUID canonico da organizacao. O componente nao le
     (nem seleciona) e-mail, telefone, WhatsApp, CNPJ ou nome.

Saida: relatorio JSON + HTML auto-contido. Exit code:
  0 = relatorio gerado (com ou sem proposta) · 2 = uso errado · 3 = recusa
  (contrato/dependencia/fonte/guarda/banco) · 4 = producao recusada · 5 = segredo vazado
  6 = VOLUME INSUFICIENTE (absteve: a pre-condicao do card ganhou codigo proprio).
"""

import argparse
import hashlib
import html as _html
import importlib.util
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

VERSAO = "calibracao-score-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "calibracao-score-v1.json")
INSTRUMENTO_PADRAO = os.path.join(AQUI, "efetividade_score.py")
CONTRATO_INSTRUMENTO_PADRAO = os.path.join(AQUI, "efetividade-score-v1.json")
FUNIL_PADRAO = os.path.join(AQUI, "funil.py")
CONTRATO_FUNIL_PADRAO = os.path.join(AQUI, "funil-v1.json")
VERSAO_INSTRUMENTO_EXIGIDA = "efetividade-score-v1"
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5
CODIGO_VOLUME = 6
COMPONENTES_ESPERADOS = ("ICP", "AUTOMATION_FIT", "BUYING_SIGNAL", "DATA_QUALITY")
CENTESIMO = Decimal("0.01")  # escala do Data Contract: NUMERIC(5,2)
CENTESIMOS_DA_ESCALA = 10000  # 0..100 em centesimos (aritmetica inteira: sem erro de float)


class Recusa(Exception):
    def __init__(self, motivo, detalhe="", codigo=3):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe
        self.codigo = codigo


# -------------------------------------------------------------------------------------------- #
# 1. Contratos e dependencias
# -------------------------------------------------------------------------------------------- #
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


def _carregar_modulo(caminho, rotulo):
    if not os.path.exists(caminho):
        raise Recusa("DEPENDENCIA_%s_AUSENTE" % rotulo, caminho)
    spec = importlib.util.spec_from_file_location("dep_%s" % rotulo.lower(), caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def carregar_instrumento(caminho, caminho_contrato):
    """A dependencia e' DECLARADA e CONFERIDA: sem a medicao do W8-E03 nao ha' calibracao."""
    mod = _carregar_modulo(caminho, "INSTRUMENTO")
    contrato = carregar_json(caminho_contrato, "CONTRATO_DO_INSTRUMENTO_AUSENTE",
                             "CONTRATO_DO_INSTRUMENTO_ILEGIVEL")
    if contrato.get("versao") != VERSAO_INSTRUMENTO_EXIGIDA:
        raise Recusa("DEPENDENCIA_VERSAO_INCOMPATIVEL",
                     "instrumento: esperado %s, obtido %s" % (VERSAO_INSTRUMENTO_EXIGIDA, contrato.get("versao")))
    for exigido in ("validar_contrato", "validar_ambiente", "extrair_prioridade", "extrair_componentes",
                    "faixa_do_valor", "ler_tudo", "carregar_dependencia_funil", "sha256_de_arquivo",
                    "_vencido"):
        if not hasattr(mod, exigido):
            raise Recusa("DEPENDENCIA_INCOMPATIVEL", "efetividade_score.py sem `%s`" % exigido)
    return mod, contrato


def validar_contrato(contrato, dados, contrato_instrumento, contrato_funil, instrumento):
    """Contrato do componente x instrumento x Data Contract x contrato do funil. Incoerente RECUSA."""
    if contrato.get("versao") != VERSAO:
        raise Recusa("VERSAO_DO_CONTRATO_DESCONHECIDA", str(contrato.get("versao")))

    # A validacao pesada (faixas que cobrem a escala, pesos que somam 1, componentes no contrato)
    # e' a do INSTRUMENTO — reusar evita duas verdades sobre o mesmo contrato de dados.
    validado = instrumento.validar_contrato(contrato_instrumento, dados, contrato_funil)

    declaradas = list(contrato.get("fontes_reusadas_do_instrumento") or [])
    vigentes = sorted((contrato_instrumento.get("fontes_proprias") or {}).keys())
    if sorted(declaradas) != vigentes:
        raise Recusa("FONTES_DO_INSTRUMENTO_DIVERGEM",
                     "declaradas=%s vigentes=%s" % (sorted(declaradas), vigentes))
    if sorted(contrato.get("fontes_do_funil") or []) != sorted((contrato_funil.get("fontes") or {}).keys()):
        raise Recusa("FONTES_DO_FUNIL_DIVERGEM",
                     "declaradas=%s vigentes=%s" % (sorted(contrato.get("fontes_do_funil") or []),
                                                    sorted((contrato_funil.get("fontes") or {}).keys())))

    p = contrato.get("parametros") or {}
    minimo_coorte = p.get("minimo_de_coorte")
    minimo_classe = p.get("minimo_por_classe")
    if not isinstance(minimo_coorte, int) or minimo_coorte < 1:
        raise Recusa("MINIMO_DE_COORTE_INVALIDO", str(minimo_coorte))
    if not isinstance(minimo_classe, int) or minimo_classe < 1:
        raise Recusa("MINIMO_POR_CLASSE_INVALIDO", str(minimo_classe))
    if minimo_coorte < 2 * minimo_classe:
        raise Recusa("MINIMOS_INCOERENTES", "coorte=%s classe=%s" % (minimo_coorte, minimo_classe))
    passo = Decimal(str(p.get("passo_da_grade")))
    if not (Decimal("0.01") <= passo <= Decimal("0.25")):
        raise Recusa("PASSO_DA_GRADE_INVALIDO", str(passo))
    if int(Decimal("1") / passo) * passo != Decimal("1"):
        raise Recusa("PASSO_DA_GRADE_NAO_DIVIDE_A_ESCALA", str(passo))
    margem = Decimal(str(p.get("margem_de_ganho_na_validacao")))
    if not (Decimal("0") <= margem <= Decimal("0.5")):
        raise Recusa("MARGEM_INVALIDA", str(margem))
    fracao = p.get("fracao_de_ajuste") or {}
    numerador = fracao.get("numerador")
    denominador = fracao.get("denominador")
    if not (isinstance(numerador, int) and isinstance(denominador, int)
            and 0 < numerador < denominador <= 10):
        raise Recusa("FRACAO_DE_AJUSTE_INVALIDA", json.dumps(fracao, ensure_ascii=False))
    faixas = list(validado["faixas"])
    cortes = p.get("cortes_de_faixa")
    if cortes != len(faixas) - 1:
        raise Recusa("CORTES_DIVERGEM_DAS_FAIXAS", "cortes=%s faixas=%s" % (cortes, len(faixas)))
    return {
        "validado_do_instrumento": validado,
        "faixas_melhor_primeiro": faixas,
        "faixas_ascendentes": list(reversed(faixas)),
        "limites": validado["limites"],
        "pesos": validado["pesos"],
        "escala": validado["escala"],
        "minimo_de_coorte": minimo_coorte,
        "minimo_por_classe": minimo_classe,
        "passo_da_grade": passo,
        "margem": margem,
        "numerador": numerador,
        "denominador": denominador,
        "cortes_de_faixa": cortes,
    }


# -------------------------------------------------------------------------------------------- #
# 2. Guardas de ambiente, auditoria da fonte e segredo (mesmo mecanismo dos irmaos — reusado)
# -------------------------------------------------------------------------------------------- #
def validar_ambiente(funil, ambiente, porta_banco=None, confirmo=False):
    # A guarda vive no funil (dependencia conferida); o instrumento a reexporta com o MESMO contrato.
    return funil.validar_ambiente(ambiente, porta_banco, confirmo)


def recusar_producao():
    raise Recusa(
        "PRODUCAO_RECUSADA",
        "nada nasce em producao (ADR-005); propor em producao e' ato de operador com aprovacao registrada",
        codigo=4,
    )


# -------------------------------------------------------------------------------------------- #
# 3. Leitura (pura) — a leitura e' a do INSTRUMENTO: nada de SQL novo aqui
# -------------------------------------------------------------------------------------------- #
def ler_tudo(instrumento, funil, contrato_instrumento, contrato_funil, porta_banco, desde, ate):
    return instrumento.ler_tudo(funil, contrato_instrumento, contrato_funil, porta_banco, desde, ate)


# -------------------------------------------------------------------------------------------- #
# 4. Derivacao (pura — testavel sem banco)
# -------------------------------------------------------------------------------------------- #
def _decimal(texto, contexto):
    try:
        return Decimal(str(texto).strip())
    except Exception:  # noqa: BLE001 — valor ilegivel recusa, nunca vira zero
        raise Recusa("VALOR_ILEGIVEL", "%s=%r" % (contexto, texto))


def _centesimos(valor):
    return int((valor * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _formatar_cents(cents):
    return str((Decimal(cents) / 100).quantize(CENTESIMO, rounding=ROUND_HALF_UP))


def _pct(numerador, denominador):
    if not denominador:
        return None
    return round(100.0 * numerador / denominador, 2)


def _digest(obj):
    canonico = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]


def lado_do_ajuste(org, numerador, denominador):
    """Corte DETERMINISTICO por sha256 do UUID canonico — reproduzivel, nao sorteio."""
    h = int(hashlib.sha256(org.encode("utf-8")).hexdigest()[:8], 16)
    return (h % denominador) < numerador


def montar_coorte(contrato_funil, funil, instrumento, brutas_funil, brutas_proprias, referencia):
    """Coorte valida (lastro completo) + desfecho resolvido. Nada e' estimado: o que falta, falta."""
    organizacoes = funil.extrair_base(brutas_funil.get("BASE_ORGANIZACOES", []))
    evidencia, _lacunas_funil = funil.resolver_evidencia(contrato_funil, brutas_funil, organizacoes)
    alcance = funil.alcance_por_organizacao(contrato_funil, organizacoes, evidencia)
    prioridade = instrumento.extrair_prioridade(brutas_proprias.get("PRIORITY_ULTIMO", []))
    componentes = instrumento.extrair_componentes(brutas_proprias.get("COMPONENTES_ULTIMOS", []))

    lacunas = {
        "prioridade_ausente": 0, "prioridade_sem_versao": 0, "prioridade_vencida": 0,
        "prioridade_fora_da_escala": 0, "componente_ausente": {}, "componente_sem_versao": {},
        "componente_vencido": {}, "componente_fora_da_escala": {}, "versoes_divergentes": 0,
        "sem_desfecho": 0, "sem_classe": 0, "evidencia_orfa": 0,
    }
    escala_min = Decimal("0")
    escala_max = Decimal("100")

    validos = {}
    for org in sorted(organizacoes):
        linha = prioridade.get(org)
        if linha is None:
            lacunas["prioridade_ausente"] += 1
            continue
        if not linha["versao"]:
            lacunas["prioridade_sem_versao"] += 1
            continue
        if instrumento._vencido(linha["vence_em"], referencia):
            lacunas["prioridade_vencida"] += 1
            continue
        if not (escala_min <= linha["valor"] <= escala_max):
            lacunas["prioridade_fora_da_escala"] += 1
            continue
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
            elif instrumento._vencido(item["vence_em"], referencia):
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
        validos[org] = {"valor": linha["valor"],
                        "componentes": {c: dobra[c]["valor"] for c in COMPONENTES_ESPERADOS}}
    for org in prioridade:
        if org not in organizacoes:
            lacunas["evidencia_orfa"] += 1

    com_desfecho = {}
    for org in sorted(validos):
        rotulos = (alcance.get(org) or {}).get("rotulos") or []
        if "Won" in rotulos:
            com_desfecho[org] = 1
        elif "Lost" in rotulos:
            com_desfecho[org] = 0
        else:
            lacunas["sem_desfecho"] += 1
    return {"organizacoes": organizacoes, "validos": validos, "desfecho": com_desfecho,
            "alcance": alcance, "lacunas": lacunas}


def score_cents(componentes, pesos_cents):
    """Aritmetica INTEIRA: peso em pontos percentuais inteiros x componente em centesimos."""
    total = 0
    for nome, peso in zip(COMPONENTES_ESPERADOS, pesos_cents):
        if peso:
            total += peso * _centesimos(componentes[nome])
    return total // 100  # 0..10000 (centesimos de 0..100), exato


def auc(pontos):
    """AUC por POSTO (Mann-Whitney); empate vale 0,5. None quando falta uma das classes."""
    positivos = sorted(s for s, y in pontos if y == 1)
    negativos = sorted(s for s, y in pontos if y == 0)
    if not positivos or not negativos:
        return None
    vitorias = 0.0
    for p in positivos:
        for n in negativos:
            if p > n:
                vitorias += 1.0
            elif p == n:
                vitorias += 0.5
    return round(vitorias / (len(positivos) * len(negativos)), 6)


def grade_do_simplexo(passo_cents):
    vetores = []
    for a in range(0, 100 + 1, passo_cents):
        for b in range(0, 100 + 1 - a, passo_cents):
            for c in range(0, 100 + 1 - a - b, passo_cents):
                vetores.append((a, b, c, 100 - a - b - c))
    return vetores


def distancia_l1(vetor, incumbente):
    return sum(abs(v - i) for v, i in zip(vetor, incumbente))


def separar_lados(coorte, parametros):
    """Devolve (ajuste, validacao) como listas de (org, componentes, y)."""
    ajuste, validacao = [], []
    for org, y in sorted(coorte["desfecho"].items()):
        item = (org, coorte["validos"][org]["componentes"], y)
        (ajuste if lado_do_ajuste(org, parametros["numerador"], parametros["denominador"]) else validacao).append(item)
    return ajuste, validacao


def resumo_do_lado(itens, pesos_cents):
    pontos = sorted((score_cents(c, pesos_cents), y) for _o, c, y in itens)
    return {"organizacoes": len(itens), "won": sum(1 for _o, _c, y in itens if y == 1),
            "lost": sum(1 for _o, _c, y in itens if y == 0), "auc": auc(pontos)}


def top_candidatos(itens_ajuste, pesos_incumbente, parametros, quantos=5):
    """Top-N da grade por AUC no ajuste (desempate: distancia L1 ao peso em vigor, depois vetor)."""
    passo_cents = int(parametros["passo_da_grade"] * 100)
    avaliados = []
    for vetor in grade_do_simplexo(passo_cents):
        pontos = sorted((score_cents(c, vetor), y) for _o, c, y in itens_ajuste)
        valor = auc(pontos)
        if valor is None:
            continue
        avaliados.append((valor, distancia_l1(vetor, pesos_incumbente), vetor))
    avaliados.sort(key=lambda t: (-t[0], t[1], t[2]))
    return avaliados[:quantos], len(avaliados)


def melhor_corte(pontos):
    """Corte por Youden J (tp_rate - fp_rate) entre valores distintos; None se nao ha' corte."""
    positivos = sum(1 for _s, y in pontos if y == 1)
    negativos = len(pontos) - positivos
    if not positivos or not negativos:
        return None
    valores = sorted({s for s, _y in pontos})
    candidatos = []
    for anterior, seguinte in zip(valores, valores[1:]):
        if seguinte - anterior < 1:
            continue
        corte = anterior + 1
        tp = sum(1 for s, y in pontos if s >= corte and y == 1)
        fp = sum(1 for s, y in pontos if s >= corte and y == 0)
        j = (tp / positivos) - (fp / negativos)
        candidatos.append((j, -corte, corte))
    if not candidatos:
        return None
    candidatos.sort(reverse=True)
    return candidatos[0][2]


def cortes_youden(pontos, quantos_cortes):
    """Cortes recursivos no grupo com MAIS organizacoes (empate: grupo de menor score)."""
    grupos = [sorted(pontos, key=lambda par: (par[0], par[1]))]
    cortes = []
    while len(grupos) < quantos_cortes + 1:
        ordem = sorted(range(len(grupos)), key=lambda i: (-len(grupos[i]), i))
        escolhido = None
        for i in ordem:
            corte = melhor_corte(grupos[i])
            if corte is not None:
                escolhido = (i, corte)
                break
        if escolhido is None:
            return None
        i, corte = escolhido
        esquerda = [p for p in grupos[i] if p[0] < corte]
        direita = [p for p in grupos[i] if p[0] >= corte]
        grupos = grupos[:i] + [esquerda, direita] + grupos[i + 1:]
        cortes.append(corte)
    return sorted(cortes), grupos


def faixas_dos_cortes(cortes, grupos, nomes_ascendentes):
    """Faixas CONTIGUAS cobrindo 0..100: os limites saem dos CORTES, nao dos valores observados.

    `corte_k` e' o menor centesimo do grupo k+1 (v+1 do ultimo valor do grupo k), entao
    `maximo(k) + 1 == minimo(k+1)` por construcao — a mesma regra que o Data Contract exige das
    faixas vigentes (0..100 sem lacuna e sem sobreposicao). Amarrar os limites ao valor observado
    deixaria buraco entre faixas justamente onde a base e' esparsa.
    """
    total = len(grupos)
    faixas = []
    for indice, grupo in enumerate(grupos):
        minimo = 0 if indice == 0 else cortes[indice - 1]
        maximo = CENTESIMOS_DA_ESCALA if indice == total - 1 else cortes[indice] - 1
        faixas.append({"faixa": nomes_ascendentes[indice], "min_cents": minimo, "max_cents": maximo,
                       "organizacoes": len(grupo),
                       "ganharam": sum(1 for _s, y in grupo if y == 1),
                       "perderam": sum(1 for _s, y in grupo if y == 0)})
    return faixas


def avaliar_faixas(coorte, pesos_cents, faixas, rotulo_origem):
    linhas = []
    for faixa in faixas:
        orgs = [org for org, y in sorted(coorte["desfecho"].items())
                if faixa["min_cents"] <= score_cents(coorte["validos"][org]["componentes"], pesos_cents)
                <= faixa["max_cents"]]
        ganharam = sum(1 for o in orgs if coorte["desfecho"][o] == 1)
        perderam = sum(1 for o in orgs if coorte["desfecho"][o] == 0)
        linhas.append({"faixa": faixa["faixa"], "min": _formatar_cents(faixa["min_cents"]),
                       "max": _formatar_cents(faixa["max_cents"]), "organizacoes": len(orgs),
                       "ganharam": ganharam, "perderam": perderam,
                       "taxa_de_vitoria_pct": _pct(ganharam, ganharam + perderam),
                       "origem": rotulo_origem})
    return linhas


def faixas_do_contrato_avaliadas(coorte, pesos_cents, limites, faixas_ascendentes, instrumento):
    linhas = []
    for nome in faixas_ascendentes:
        minimo, maximo = limites[nome]
        orgs = [org for org, y in sorted(coorte["desfecho"].items())
                if minimo <= Decimal(score_cents(coorte["validos"][org]["componentes"], pesos_cents)) / 100
                <= maximo]
        ganharam = sum(1 for o in orgs if coorte["desfecho"][o] == 1)
        perderam = sum(1 for o in orgs if coorte["desfecho"][o] == 0)
        linhas.append({"faixa": nome, "min": str(Decimal(minimo).quantize(CENTESIMO)),
                       "max": str(Decimal(maximo).quantize(CENTESIMO)), "organizacoes": len(orgs),
                       "ganharam": ganharam, "perderam": perderam,
                       "taxa_de_vitoria_pct": _pct(ganharam, ganharam + perderam),
                       "origem": "tiers do Data Contract (vigentes)"})
    return linhas


def violacoes_de_monotonicidade(linhas):
    """Ordem ASCENDENTE: a taxa de vitoria nao deve CAIR ao subir de faixa."""
    com_base = [l for l in linhas if l["organizacoes"] > 0 and l["taxa_de_vitoria_pct"] is not None]
    violacoes = []
    for atual, seguinte in zip(com_base, com_base[1:]):
        if seguinte["taxa_de_vitoria_pct"] < atual["taxa_de_vitoria_pct"]:
            violacoes.append({"esperado": "%s (%s%%) <= %s (%s%%)" % (
                atual["faixa"], atual["taxa_de_vitoria_pct"], seguinte["faixa"], seguinte["taxa_de_vitoria_pct"]),
                "obtido": "%s < %s" % (seguinte["taxa_de_vitoria_pct"], atual["taxa_de_vitoria_pct"])})
    return violacoes


def derivar(contrato, dados, contrato_instrumento, contrato_funil, funil, instrumento,
            brutas_funil, brutas_proprias, referencia, ambiente, janela,
            gerado_em=None, sha_contrato=None, sha_instrumento=None, sha_contrato_instrumento=None,
            sha_contrato_funil=None):
    """Derivacao PURA (sem banco): recebe as linhas cruas e devolve o relatorio."""
    validado = validar_contrato(contrato, dados, contrato_instrumento, contrato_funil, instrumento)
    parametros = {
        "minimo_de_coorte": validado["minimo_de_coorte"],
        "minimo_por_classe": validado["minimo_por_classe"],
        "passo_da_grade": str(validado["passo_da_grade"]),
        "margem_de_ganho_na_validacao": str(validado["margem"]),
        "fracao_de_ajuste": {"numerador": validado["numerador"], "denominador": validado["denominador"]},
        "cortes_de_faixa": validado["cortes_de_faixa"],
        "escala": validado["escala"],
        "faixas_ascendentes": validado["faixas_ascendentes"],
        "pesos_em_vigor": {c: str(validado["pesos"][c]) for c in COMPONENTES_ESPERADOS},
    }
    coorte = montar_coorte(contrato_funil, funil, instrumento, brutas_funil, brutas_proprias, referencia)
    lacunas = coorte["lacunas"]

    # --- gate de volume (a pre-condicao do card virou numero) --------------------------------
    won = sum(1 for y in coorte["desfecho"].values() if y == 1)
    lost = sum(1 for y in coorte["desfecho"].values() if y == 0)
    motivos = []
    if len(coorte["desfecho"]) < parametros["minimo_de_coorte"]:
        motivos.append("COORTE_COM_DESFECHO_ABAIXO_DO_MINIMO")
    if won < parametros["minimo_por_classe"]:
        motivos.append("CLASSE_WON_ABAIXO_DO_MINIMO")
    if lost < parametros["minimo_por_classe"]:
        motivos.append("CLASSE_LOST_ABAIXO_DO_MINIMO")
    if not motivos:
        ajuste_itens, validacao_itens = separar_lados(coorte, validado)
        if any(len({y for _o, _c, y in itens}) < 2 for itens in (ajuste_itens, validacao_itens)):
            motivos.append("CLASSE_AUSENTE_EM_UM_LADO_DO_AJUSTE")
    gate = {
        "organizacoes": len(coorte["organizacoes"]),
        "coorte_valida": len(coorte["validos"]),
        "com_desfecho": len(coorte["desfecho"]),
        "won": won, "lost": lost, "sem_desfecho": lacunas["sem_desfecho"],
        "minimo_de_coorte": parametros["minimo_de_coorte"],
        "minimo_por_classe": parametros["minimo_por_classe"],
        "base_suficiente": not motivos,
        "motivos": motivos,
        "nota": "base_suficiente=false => ABSTEVE (exit %d): o relatorio sai, a proposta NAO" % CODIGO_VOLUME,
    }

    pesos_incumbente = tuple(int(validado["pesos"][c] * 100) for c in COMPONENTES_ESPERADOS)

    relatorio = {
        "versao": VERSAO,
        "card": contrato.get("card"),
        "ambiente": ambiente,
        "janela": dict(janela or {"desde": None, "ate": None}),
        "referencia_temporal": referencia.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gerado_em": gerado_em or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contrato": {"versao": contrato["versao"],
                     "sha256": sha_contrato or instrumento.sha256_de_arquivo(CONTRATO_PADRAO)},
        "dependencia": {
            "instrumento": "efetividade_score.py (card W8-E03-T01)",
            "contrato_do_instrumento": contrato_instrumento.get("versao"),
            "sha256_do_instrumento": sha_instrumento or instrumento.sha256_de_arquivo(INSTRUMENTO_PADRAO),
            "sha256_do_contrato_do_instrumento": sha_contrato_instrumento or
            instrumento.sha256_de_arquivo(CONTRATO_INSTRUMENTO_PADRAO),
            "contrato_do_funil": contrato_funil.get("versao"),
            "sha256_do_contrato_do_funil": sha_contrato_funil or instrumento.sha256_de_arquivo(CONTRATO_FUNIL_PADRAO),
            "funcao_do_desfecho": "alcance_por_organizacao (via instrumento)",
        },
        "base": {"organizacoes": len(coorte["organizacoes"]),
                 "digest": _digest({"organizacoes": sorted(coorte["organizacoes"])})},
        "parametros": parametros,
        "gate_de_volume": gate,
        "separacao": None,
        "incumbente": None,
        "proposta": {"aplicado": False, "exige_versao_nova": True, "aprovacao_humana": "pendente",
                     "alvo": "docs/data/data_contract_v1.json#scores (priority_weights e tiers)",
                     "status": "ABSTEVE", "pesos": None, "faixas": None},
        "faixas_propostas": [],
        "lacunas": lacunas,
        "lacunas_declaradas": contrato.get("lacunas_declaradas", []),
        "fontes": {},
    }
    relatorio["fontes"] = {fid: len(linhas) for fid, linhas in sorted(
        dict(brutas_funil, **brutas_proprias).items())}

    # Sem volume: o relatorio e' o GATE. Nada de ajuste (nem faixa) sobre base pequena.
    if motivos:
        relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
        return relatorio

    ajuste_itens, validacao_itens = separar_lados(coorte, validado)
    incumbente_ajuste = resumo_do_lado(ajuste_itens, pesos_incumbente)
    incumbente_validacao = resumo_do_lado(validacao_itens, pesos_incumbente)
    incumbente_coorte = resumo_do_lado([(o, c, y) for (o, c, y) in ajuste_itens + validacao_itens],
                                       pesos_incumbente)
    faixas_vigentes = faixas_do_contrato_avaliadas(coorte, pesos_incumbente, validado["limites"],
                                                   validado["faixas_ascendentes"], instrumento)
    relatorio["incumbente"] = {
        "pesos": {c: str(validado["pesos"][c]) for c in COMPONENTES_ESPERADOS},
        "ajuste": incumbente_ajuste, "validacao": incumbente_validacao, "coorte": incumbente_coorte,
        "faixas_vigentes": faixas_vigentes,
        "violacoes_de_monotonicidade": violacoes_de_monotonicidade(faixas_vigentes),
    }
    relatorio["separacao"] = {"ajuste": incumbente_ajuste, "validacao": incumbente_validacao}

    # --- pesos: grade do simplexo, AUC, margem na VALIDACAO -------------------------------
    ranking, avaliados = top_candidatos(ajuste_itens, pesos_incumbente, validado, quantos=5)
    melhor = ranking[0][2]
    proposto_ajuste = resumo_do_lado(ajuste_itens, melhor)
    proposto_validacao = resumo_do_lado(validacao_itens, melhor)
    ganho = None
    if proposto_validacao["auc"] is not None and incumbente_validacao["auc"] is not None:
        ganho = round(proposto_validacao["auc"] - incumbente_validacao["auc"], 6)
    pesos_propostos = {c: str(Decimal(v) / 100) for c, v in zip(COMPONENTES_ESPERADOS, melhor)}
    proposta_de_pesos = {
        "vetor": list(melhor),
        "pesos": pesos_propostos,
        "distancia_l1_ao_peso_em_vigor": distancia_l1(melhor, pesos_incumbente),
        "vetores_avaliados": avaliados,
        "ajuste": proposto_ajuste, "validacao": proposto_validacao,
        "ganho_na_validacao": ganho,
        "margem_exigida": str(validado["margem"]),
        "aprovada": bool(ganho is not None and ganho >= float(validado["margem"])),
        "candidatos": [{"vetor": list(v), "pesos": {c: str(Decimal(x) / 100) for c, x in
                                                    zip(COMPONENTES_ESPERADOS, v)},
                        "auc_ajuste": a, "distancia_l1": d} for a, d, v in ranking],
        "nota": "ganho so' no ajuste NAO propoe: a margem e' conferida na VALIDACAO (anti-overfitting)",
    }
    if not proposta_de_pesos["aprovada"]:
        proposta_de_pesos["pesos"] = None

    # --- faixas: Youden recursivo sobre o score do vetor com melhor AUC --------------------
    vetor_das_faixas = melhor if proposta_de_pesos["aprovada"] else pesos_incumbente
    origem_das_faixas = "pesos propostos" if proposta_de_pesos["aprovada"] else "pesos em vigor"
    pontos = sorted((score_cents(coorte["validos"][org]["componentes"], vetor_das_faixas), y)
                    for org, y in coorte["desfecho"].items())
    resultado = cortes_youden(pontos, validado["cortes_de_faixa"])
    proposta_de_faixas = None
    if resultado is None:
        lacunas["faixas_nao_propostas"] = "grupo sem valor distinto para separar (corte de Youden indisponivel)"
    else:
        cortes, grupos = resultado
        faixas = faixas_dos_cortes(cortes, grupos, validado["faixas_ascendentes"])
        linhas = avaliar_faixas(coorte, vetor_das_faixas, faixas, "Youden J (%s)" % origem_das_faixas)
        nas_faixas = sum(l["organizacoes"] for l in linhas)
        if nas_faixas != len(coorte["desfecho"]):
            raise Recusa("FAIXAS_PROPOSTAS_NAO_COBREM_A_COORTE",
                         "faixas=%d coorte=%d" % (nas_faixas, len(coorte["desfecho"])))
        proposta_de_faixas = {
            "sobre": origem_das_faixas,
            "cortes": [_formatar_cents(c) for c in cortes],
            "faixas": linhas,
            "organizacoes_nas_faixas": nas_faixas,
            "cobertura_provada": "a soma das faixas propostas e' igual a coorte com desfecho (%d)" % nas_faixas,
            "violacoes_de_monotonicidade": violacoes_de_monotonicidade(linhas),
        }
        relatorio["faixas_propostas"] = linhas

    status = "PROPOSTA_GERADA" if (proposta_de_pesos["aprovada"] or proposta_de_faixas) else "SEM_GANHO"
    relatorio["proposta"].update({
        "status": status,
        "pesos": proposta_de_pesos,
        "faixas": proposta_de_faixas,
        "resumo": ("pesos propostos ganham %s de AUC na validacao (margem %s)" % (ganho, validado["margem"])
                   if proposta_de_pesos["aprovada"] else
                   "nenhum vetor da grade ganhou a margem na validacao: pesos em vigor mantidos"),
    })
    relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
    return relatorio


def hash_do_relatorio(relatorio):
    limpo = {k: v for k, v in relatorio.items()
             if k not in ("gerado_em", "referencia_temporal", "hash_do_relatorio")}
    canonico = json.dumps(limpo, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# -------------------------------------------------------------------------------------------- #
# 5. Dashboard (HTML auto-contido: uma string, CSS inline, nenhum recurso externo)
# -------------------------------------------------------------------------------------------- #
def emitir_html(relatorio):
    def tabela(cabecalho, linhas):
        th = "".join("<th>%s</th>" % _html.escape(c) for c in cabecalho)
        tr = "".join("<tr>%s</tr>" % "".join(
            '<td class="n">%s</td>' % _html.escape(str(c)) if i else "<td>%s</td>" % _html.escape(str(c))
            for i, c in enumerate(linha)) for linha in linhas)
        return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (th, tr)

    gate = relatorio["gate_de_volume"]
    proposta = relatorio["proposta"]
    incumbente = relatorio.get("incumbente") or {}
    linhas_pesos = []
    if proposta.get("pesos") and (proposta["pesos"] or {}).get("pesos"):
        pp = proposta["pesos"]
        for c in COMPONENTES_ESPERADOS:
            linhas_pesos.append([c, relatorio["parametros"]["pesos_em_vigor"][c], pp["pesos"][c]])
    faixas_propostas = [[f["faixa"], f["min"], f["max"], f["organizacoes"], f["ganharam"], f["perderam"],
                         "-" if f["taxa_de_vitoria_pct"] is None else "%.2f%%" % f["taxa_de_vitoria_pct"]]
                        for f in relatorio.get("faixas_propostas") or []]
    faixas_vigentes = [[f["faixa"], f["min"], f["max"], f["organizacoes"], f["ganharam"], f["perderam"],
                        "-" if f["taxa_de_vitoria_pct"] is None else "%.2f%%" % f["taxa_de_vitoria_pct"]]
                       for f in (incumbente.get("faixas_vigentes") or [])]
    lacunas = [[k, json.dumps(v, ensure_ascii=False)] for k, v in sorted(relatorio["lacunas"].items())
               if v not in (0, {}, None)]
    motivos = "".join("<li>%s</li>" % _html.escape(m) for m in gate["motivos"])
    return (
        "<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Calibracao do score — TRE-W9-E01-T01</title>\n<style>\n"
        "body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#111}\n"
        "h1{font-size:20px;margin:0 0 4px 0}\nh2{font-size:15px;margin:22px 0 6px 0}\n"
        ".meta{color:#555;font-size:12px;margin-bottom:16px}\n"
        "table{border-collapse:collapse;width:100%%;font-size:13px}\n"
        "th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}\n"
        "td.n{text-align:right;font-variant-numeric:tabular-nums}\n"
        ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 6px 0}\n"
        ".card{border:1px solid #ddd;border-radius:6px;padding:8px 12px;min-width:110px}\n"
        ".card b{display:block;font-size:19px}\n.lacunas{font-size:12px;color:#555}\n"
        ".aviso{border-left:4px solid #b00;padding:6px 10px;background:#fff4f4;font-size:13px}\n"
        "</style>\n</head>\n<body>\n"
        "<h1>Calibracao do score — Transformativa Revenue Engine</h1>\n"
        "<div class=\"meta\">card %s · contrato %s (sha256 %s) · instrumento %s · funil %s · "
        "ambiente %s · referencia %s · gerado em %s · hash do relatorio %s</div>\n"
        % (_html.escape(str(relatorio.get("card"))), _html.escape(relatorio["contrato"]["versao"]),
           _html.escape(relatorio["contrato"]["sha256"][:16]),
           _html.escape(str(relatorio["dependencia"]["contrato_do_instrumento"])),
           _html.escape(str(relatorio["dependencia"]["contrato_do_funil"])),
           _html.escape(relatorio["ambiente"]), _html.escape(relatorio["referencia_temporal"]),
           _html.escape(relatorio["gerado_em"]), _html.escape(relatorio["hash_do_relatorio"][:16]))
        + "<div class=\"cards\">"
        + "".join("<div class=\"card\"><b>%s</b>%s</div>" % (_html.escape(str(v)), _html.escape(k))
                  for k, v in [("organizações", gate["organizacoes"]),
                               ("coorte válida", gate["coorte_valida"]),
                               ("com desfecho", gate["com_desfecho"]),
                               ("Won / Lost", "%s / %s" % (gate["won"], gate["lost"])),
                               ("base suficiente", "sim" if gate["base_suficiente"] else "NÃO"),
                               ("status da proposta", proposta["status"])])
        + "</div>\n"
        + ("<div class=\"aviso\"><b>ABSTEVE</b> — a pré-condição do card (volume real suficiente) não está "
           "satisfeita; nenhum peso e nenhuma faixa foram propostos.<ul>%s</ul></div>\n" % motivos
           if not gate["base_suficiente"] else "")
        + "<h2>Pesos: em vigor x propostos</h2>\n"
        + (tabela(["Componente", "Em vigor", "Proposto"], linhas_pesos) if linhas_pesos else
           "<p class=\"lacunas\">Sem proposta de pesos (margem de validação não atingida ou abstenção).</p>\n")
        + "<h2>Faixas propostas (%s)</h2>\n" % _html.escape(str((proposta.get("faixas") or {}).get("sobre", "-")))
        + (tabela(["Faixa", "Min", "Max", "Organizações", "Won", "Lost", "Taxa de vitória"], faixas_propostas)
           if faixas_propostas else "<p class=\"lacunas\">Nenhuma faixa proposta.</p>\n")
        + "<h2>Faixas vigentes (tiers do Data Contract)</h2>\n"
        + (tabela(["Faixa", "Min", "Max", "Organizações", "Won", "Lost", "Taxa de vitória"], faixas_vigentes)
           if faixas_vigentes else "<p class=\"lacunas\">Não avaliadas (abstenção).</p>\n")
        + "<h2>Lacunas medidas</h2>\n<ul class=\"lacunas\">"
        + "".join("<li>%s: %s</li>" % (_html.escape(k), _html.escape(v)) for k, v in lacunas)
        + "</ul>\n</body>\n</html>\n"
    )


# -------------------------------------------------------------------------------------------- #
# 6. Montagem e CLI
# -------------------------------------------------------------------------------------------- #
def construir_relatorio(instrumento, funil, contrato, dados, contrato_instrumento, contrato_funil,
                        porta_banco, ambiente, desde, ate, referencia, gerado_em=None,
                        sha_contrato=None, sha_instrumento=None, sha_contrato_instrumento=None,
                        sha_contrato_funil=None):
    if porta_banco is None:
        raise Recusa("PORTA_BANCO_AUSENTE", "sem banco nao ha' medicao: use --planejar/--conferir")
    brutas_funil, brutas_proprias = ler_tudo(instrumento, funil, contrato_instrumento, contrato_funil,
                                             porta_banco, desde, ate)
    return derivar(contrato, dados, contrato_instrumento, contrato_funil, funil, instrumento,
                   brutas_funil, brutas_proprias, referencia, ambiente, {"desde": desde, "ate": ate},
                   gerado_em=gerado_em, sha_contrato=sha_contrato, sha_instrumento=sha_instrumento,
                   sha_contrato_instrumento=sha_contrato_instrumento,
                   sha_contrato_funil=sha_contrato_funil)


def _resumo_texto(relatorio):
    gate = relatorio["gate_de_volume"]
    proposta = relatorio["proposta"]
    if not gate["base_suficiente"]:
        return "CALIBRACAO_ABSTEVE_VOLUME coorte=%d won=%d lost=%d motivos=%s hash=%s" % (
            gate["com_desfecho"], gate["won"], gate["lost"], ",".join(gate["motivos"]),
            relatorio["hash_do_relatorio"][:16])
    incumbente = relatorio["incumbente"]["validacao"]["auc"]
    pesos = proposta.get("pesos") or {}
    aprovados = bool(pesos.get("pesos"))
    return " ".join([
        "CALIBRACAO_SCORE_OK",
        "status=%s" % proposta["status"],
        "coorte=%d" % gate["com_desfecho"],
        "auc_validacao_incumbente=%s" % incumbente,
        "pesos_propostos=%s" % ("sim" if aprovados else "nao"),
        "auc_validacao_candidato=%s" % (pesos.get("validacao", {}).get("auc") if pesos else "-"),
        "ganho_na_validacao=%s" % (pesos.get("ganho_na_validacao") if pesos else "-"),
        "faixas=%d" % len(relatorio.get("faixas_propostas") or []),
        "hash=%s" % relatorio["hash_do_relatorio"][:16],
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Calibracao do score v1 — TRE-W9-E01-T01")
    parser.add_argument("--ambiente", required=True, choices=list(AMBIENTES))
    parser.add_argument("--porta-banco", default=os.environ.get("TRE_CALIBRACAO_PORTA_BANCO"))
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--agora", default=None, help="referencia temporal (ISO); padrao: relogio da rodada")
    parser.add_argument("--saida", default=None, help="diretorio de saida do relatorio")
    parser.add_argument("--formato", default="json,html", choices=["json", "html", "json,html"])
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--planejar", action="store_true", help="imprime o plano declarado (sem banco)")
    parser.add_argument("--conferir", action="store_true", help="valida contratos e dependencias (sem banco)")
    parser.add_argument("--contrato", default=CONTRATO_PADRAO)
    parser.add_argument("--instrumento", default=INSTRUMENTO_PADRAO)
    parser.add_argument("--contrato-instrumento", default=CONTRATO_INSTRUMENTO_PADRAO)
    parser.add_argument("--funil", default=FUNIL_PADRAO)
    parser.add_argument("--contrato-funil", default=CONTRATO_FUNIL_PADRAO)
    parser.add_argument("--raiz", default=os.path.join(AQUI, "..", "..", ".."))
    args = parser.parse_args(argv)

    try:
        raiz = os.path.abspath(args.raiz)
        if args.ambiente == "prod":
            recusar_producao()  # antes de qualquer leitura, independentemente de contrato/base
        contrato = carregar_contrato(args.contrato)
        dados = carregar_json(os.path.join(raiz, "docs", "data", "data_contract_v1.json"),
                              "CONTRATO_DE_DADOS_AUSENTE", "CONTRATO_DE_DADOS_ILEGIVEL")
        instrumento, contrato_instrumento = carregar_instrumento(args.instrumento,
                                                                  args.contrato_instrumento)
        funil, contrato_funil = instrumento.carregar_dependencia_funil(args.funil, args.contrato_funil)
        validado = validar_contrato(contrato, dados, contrato_instrumento, contrato_funil, instrumento)

        if args.conferir:
            print("CALIBRACAO_CONFERIR_OK contrato=%s instrumento=%s funil=%s coorte_minima=%d "
                  "passo=%s margem=%s faixas=%d" % (
                      contrato["versao"], contrato_instrumento.get("versao"), contrato_funil.get("versao"),
                      validado["minimo_de_coorte"], validado["passo_da_grade"], validado["margem"],
                      len(validado["faixas_ascendentes"])))
            return 0
        if args.planejar:
            print("CALIBRACAO_PLANO versao=%s unidade=organization" % contrato["versao"])
            print("  gate de volume: minimo_de_coorte=%d minimo_por_classe=%d" % (
                validado["minimo_de_coorte"], validado["minimo_por_classe"]))
            print("  grade: passo=%s (simplexo de %d componentes)" % (validado["passo_da_grade"],
                                                                      len(COMPONENTES_ESPERADOS)))
            print("  pesos em vigor (do Data Contract): %s" % json.dumps(
                {c: str(validado["pesos"][c]) for c in COMPONENTES_ESPERADOS}, ensure_ascii=False))
            print("  faixas: %s" % ", ".join("%s[%s..%s]" % (f, validado["limites"][f][0],
                                                             validado["limites"][f][1])
                                             for f in validado["faixas_ascendentes"]))
            print("  desfecho: funil.py -> alcance_por_organizacao, via instrumento efetividade_score.py")
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
            instrumento, funil, contrato, dados, contrato_instrumento, contrato_funil, args.porta_banco,
            args.ambiente, desde, ate, referencia, sha_contrato=instrumento.sha256_de_arquivo(args.contrato),
            sha_instrumento=instrumento.sha256_de_arquivo(args.instrumento),
            sha_contrato_instrumento=instrumento.sha256_de_arquivo(args.contrato_instrumento),
            sha_contrato_funil=instrumento.sha256_de_arquivo(args.contrato_funil))

        saida_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
        saida_html = emitir_html(relatorio)
        for token_nome in ("TRE_CALIBRACAO_TOKEN", "TRE_CALIBRACAO_SCORE_TOKEN",
                           "TRE_EFETIVIDADE_TOKEN", "TRE_EFETIVIDADE_SCORE_TOKEN", "TRE_FUNIL_TOKEN"):
            token = os.environ.get(token_nome)
            if token and (token in saida_json or token in saida_html):
                raise Recusa("SENHA_VAZADA", "valor de %s presente na evidencia" % token_nome,
                             codigo=CODIGO_SEGREDO)

        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "calibracao-score.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "calibracao-score.html"), "w", encoding="utf-8") as fh:
                    fh.write(saida_html)
        print(_resumo_texto(relatorio))
        return 0 if relatorio["gate_de_volume"]["base_suficiente"] else CODIGO_VOLUME
    except Recusa as exc:
        print("RECUSA %s %s" % (exc.motivo, exc.detalhe))
        return exc.codigo
    except Exception as exc:  # noqa: BLE001 — instrumento/funil tem a PROPRIA Recusa: recusa de
        motivo = getattr(exc, "motivo", None)  # qualquer origem sai pelo codigo dela, nunca como
        if motivo is None:                     # traceback (exit 1) confundido com quebra
            raise
        print("RECUSA %s %s" % (motivo, getattr(exc, "detalhe", "")))
        return getattr(exc, "codigo", 3)


if __name__ == "__main__":
    sys.exit(main())
