#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pontuacao preditiva (`pontuacao-preditiva-v1`) — card TRE-W9-E02-T01 (W9 / Inteligencia Avancada).

O que este componente FAZ (e so' isto): transforma o score ORDINAL de prioridade em uma
PROBABILIDADE DE GANHO por organizacao — a pontuacao preditiva — derivada da coorte com desfecho
resolvido, ajustada em um lado e MEDIDA fora da amostra no outro, e aplicada como PREVISAO sobre as
organizacoes que ainda NAO tem desfecho. Em LEITURA PURA. Ele NAO aplica nada: probabilidade
persistida (coluna, tabela, score_type novo) e' decisao estrutural (§10 do Data Contract +
ADR-0004).

Três perguntas, cada uma com numero proprio:

  1. O VOLUME SUSTENTA A PREVISAO? A pre-condicao declarada do card ("volume real suficiente")
     virou gate medido (mesmos minimos da calibracao): sem coorte com desfecho e as duas classes
     nos dois lados, o componente ABSTEVE (exit 6) e nomeia o motivo — base pequena nao vira
     probabilidade.
  2. O SCORE ORDINAL VIRA PROBABILIDADE HONESTA? A curva sai de BINOS do score ponderado
     (0..100 em `bins_da_curva` faixas), com a taxa de vitoria observada no lado do AJUSTE e
     monotonicidade imposta por PAVA determinista (pool adjacent violators). Bino com base aparece
     com numero proprio; bino sem base NAO inventa valor (herda o bloco anterior, regra declarada,
     e o que nao tem nem anterior fica `sem_base`).
  3. A PREVISAO ACERTA FORA DA AMOSTRA? O lado de VALIDACAO mede AUC, Brier, Brier skill contra a
     taxa-base do ajuste, log-loss e a tabela de confiabilidade (previsto x observado por bino).
     Ganho so' no ajuste nao e' evidencia: o numero que vale e' o de validacao.

Invariantes (cada um com item de suite/aceite):

  1. PREVISAO NAO E' APLICACAO: `aplicacao.aplicado: false`, `exige_versao_nova: true`,
     `aprovacao_humana: pendente` viajam no relatorio; nada e' escrito no Data Contract nem no banco.
  2. LEITURA PURA NAO E' PREFERENCIA: toda consulta roda pelo INSTRUMENTO com
     `default_transaction_read_only = on` (o proprio PostgreSQL recusa escrita) e a auditoria da
     fonte reprova verbo de escrita ANTES de qualquer conexao (`ESCRITA_NO_CODIGO`, exit 3).
  3. NAO REIMPLEMENTA A MEDICAO NEM O DESFECHO: leitura, validacao e alcance por organizacao vem de
     `efetividade_score.py` (W8-E03-T01) e, por ele, de `funil.py` (W8-E01-T01); a coorte, o corte
     ajuste/validacao, a aritmetica do score ponderado e a AUC vem de `calibracao_score.py`
     (W9-E01-T01), importados e conferidos por versao e sha256.
  4. DEPENDENCIA MEDIDA, NAO PRESUMIDA: sem o relatorio da calibracao o componente RECUSA
     (`DEPENDENCIA_CALIBRACAO`); com corte ajuste/validacao diferente do relatorio, RECUSA
     (`CORTE_DIVERGENTE`) — os dois numeros so' sao comparaveis sobre a MESMA particao.
  5. NAO INVENTA NUMERO: organizacao sem PRIORITY valido, sem os QUATRO componentes na mesma versao
     ou sem desfecho resolvido continua fora do ajuste em lacuna nomeada — nunca em zero, nunca em
     media.
  6. DETERMINISMO: a mesma base, o mesmo relatorio de calibracao e a mesma referencia temporal
     produzem o MESMO relatorio (binos, PAVA e ordem sao deterministicos); `gerado_em` e
     `referencia_temporal` sao a unica diferenca e NAO entram no hash.
  7. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<...> psql`); prefixo remoto RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige
     `--confirmo`; `prod` RECUSA por desenho (exit 4, antes de ler contrato).
  8. PRIVACIDADE: a saida carrega CONTAGEM, o UUID canonico da organizacao e o numero previsto. O
     componente nao le (nem seleciona) e-mail, telefone, WhatsApp, CNPJ ou nome.

Saida: relatorio JSON + HTML auto-contido. Exit code:
  0 = relatorio gerado (com ou sem previsao) · 2 = uso errado · 3 = recusa
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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone

VERSAO = "pontuacao-preditiva-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "pontuacao-preditiva-v1.json")
CALIBRACAO_PADRAO = os.path.join(AQUI, "calibracao_score.py")
CONTRATO_CALIBRACAO_PADRAO = os.path.join(AQUI, "calibracao-score-v1.json")
INSTRUMENTO_PADRAO = os.path.join(AQUI, "efetividade_score.py")
CONTRATO_INSTRUMENTO_PADRAO = os.path.join(AQUI, "efetividade-score-v1.json")
FUNIL_PADRAO = os.path.join(AQUI, "funil.py")
CONTRATO_FUNIL_PADRAO = os.path.join(AQUI, "funil-v1.json")
VERSAO_CALIBRACAO_EXIGIDA = "calibracao-score-v1"
VERSAO_INSTRUMENTO_EXIGIDA = "efetividade-score-v1"
VERSAO_FUNIL_EXIGIDA = "funil-v1"
CARD_DEPENDENTE = "TRE-W9-E01-T01"
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5
CODIGO_VOLUME = 6
COMPONENTES_ESPERADOS = ("ICP", "AUTOMATION_FIT", "BUYING_SIGNAL", "DATA_QUALITY")
CENTESIMO = Decimal("0.01")
ESCALA_CENTESIMOS = 10000  # 0..100 em centesimos (aritmetica inteira: sem erro de float)
TOKENS_DE_SEGREDO = ("TRE_PONTUACAO_TOKEN", "TRE_PONTUACAO_PREDITIVA_TOKEN", "TRE_CALIBRACAO_TOKEN",
                     "TRE_EFETIVIDADE_TOKEN", "TRE_EFETIVIDADE_SCORE_TOKEN", "TRE_FUNIL_TOKEN")


class Recusa(Exception):
    def __init__(self, motivo, detalhe="", codigo=3):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe
        self.codigo = codigo


# -------------------------------------------------------------------------------------------- #
# 1. Carga e validacao de contrato (leitura pura, sem banco)
# -------------------------------------------------------------------------------------------- #
def carregar_json(caminho, motivo_ausente, motivo_ilegivel):
    if not os.path.isfile(caminho):
        raise Recusa(motivo_ausente, "arquivo ausente: %s" % caminho)
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise Recusa(motivo_ilegivel, "%s: %s" % (caminho, exc))


def carregar_contrato(caminho=None):
    return carregar_json(caminho or CONTRATO_PADRAO, "CONTRATO_AUSENTE", "CONTRATO_ILEGIVEL")


def _carregar_modulo(caminho, rotulo):
    if not os.path.isfile(caminho):
        raise Recusa("DEPENDENCIA_AUSENTE", "%s ausente: %s" % (rotulo, caminho))
    spec = importlib.util.spec_from_file_location("tre_dep_%s" % rotulo, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def carregar_calibracao(caminho, caminho_contrato):
    """O instrumento da previsao e' a CALIBRACAO vigente: sem ela nao ha' peso a usar."""
    modulo = _carregar_modulo(caminho, "calibracao")
    contrato = carregar_json(caminho_contrato, "CONTRATO_CALIBRACAO_AUSENTE",
                             "CONTRATO_CALIBRACAO_ILEGIVEL")
    if getattr(modulo, "VERSAO", None) != VERSAO_CALIBRACAO_EXIGIDA:
        raise Recusa("VENDA_DIVERGENTE", "%s declara versao %r" % (caminho, getattr(modulo, "VERSAO", None)))
    if contrato.get("versao") != VERSAO_CALIBRACAO_EXIGIDA:
        raise Recusa("VENDA_DIVERGENTE", "contrato da calibracao declara %r" % contrato.get("versao"))
    return modulo, contrato


def carregar_instrumento(caminho, caminho_contrato):
    modulo = _carregar_modulo(caminho, "instrumento")
    contrato = carregar_json(caminho_contrato, "CONTRATO_INSTRUMENTO_AUSENTE",
                             "CONTRATO_INSTRUMENTO_ILEGIVEL")
    if getattr(modulo, "VERSAO", None) != VERSAO_INSTRUMENTO_EXIGIDA:
        raise Recusa("VENDA_DIVERGENTE", "%s declara versao %r" % (caminho, getattr(modulo, "VERSAO", None)))
    if contrato.get("versao") != VERSAO_INSTRUMENTO_EXIGIDA:
        raise Recusa("VENDA_DIVERGENTE", "contrato do instrumento declara %r" % contrato.get("versao"))
    return modulo, contrato


def _decimal(texto, contexto):
    try:
        return Decimal(str(texto))
    except (InvalidOperation, ValueError):
        raise Recusa("CONTRATO_INVALIDO", "%s nao e' numero: %r" % (contexto, texto))


def _centesimos(valor):
    return int((_decimal(valor, "componente") / CENTESIMO).to_integral_value(rounding=ROUND_HALF_UP))


def pesos_em_centesimos(mapa):
    """Peso em pontos percentuais inteiros (o peso do Data Contract soma 1.00 = 100)."""
    if not isinstance(mapa, dict):
        raise Recusa("CONTRATO_INVALIDO", "pesos ausentes ou nao sao objeto")
    faltando = [c for c in COMPONENTES_ESPERADOS if c not in mapa]
    if faltando:
        raise Recusa("CONTRATO_INVALIDO", "pesos sem componente: %s" % ", ".join(faltando))
    vetor = tuple(int(_decimal(mapa[c], c) * 100) for c in COMPONENTES_ESPERADOS)
    if sum(vetor) != 100:
        raise Recusa("CONTRATO_INVALIDO", "pesos somam %d e nao 100: %s" % (sum(vetor), vetor))
    if any(v < 0 for v in vetor):
        raise Recusa("CONTRATO_INVALIDO", "peso negativo em %s" % (vetor,))
    return vetor


def _limites_do_contrato(dados):
    """Faixas do Data Contract: ascendentes, contiguas e cobrindo 0..100."""
    scores = (dados.get("scores") or {})
    tiers = scores.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise Recusa("CONTRATO_INVALIDO", "docs/data/data_contract_v1.json sem `scores.tiers`")
    tipos = scores.get("types") or []
    faltando = [c for c in COMPONENTES_ESPERADOS if c not in tipos]
    if faltando:
        raise Recusa("CONTRATO_INVALIDO", "componente fora de `scores.types`: %s" % ", ".join(faltando))
    pares = []
    for tier in tiers:
        nome = tier.get("name")
        if not nome:
            raise Recusa("CONTRATO_INVALIDO", "faixa sem nome no Data Contract")
        pares.append((nome, int(_decimal(tier.get("min"), "min") * 100),
                      int(_decimal(tier.get("max"), "max") * 100)))
    ascendentes = [p for p in sorted(pares, key=lambda t: t[1])]
    if len({p[0] for p in ascendentes}) != len(ascendentes):
        raise Recusa("CONTRATO_INVALIDO", "faixa duplicada no Data Contract")
    for anterior, seguinte in zip(ascendentes, ascendentes[1:]):
        if seguinte[1] != anterior[2] + 1:
            raise Recusa("CONTRATO_INVALIDO", "faixa nao contigua: %s->%s" % (anterior[0], seguinte[0]))
    if ascendentes[0][1] != 0 or ascendentes[-1][2] not in (ESCALA_CENTESIMOS - 1, ESCALA_CENTESIMOS):
        raise Recusa("CONTRATO_INVALIDO", "faixas nao cobrem 0..100")
    return ascendentes


def validar_contrato(contrato, dados, calib_relatorio):
    """Valida o contrato DESTE componente e a dependencia da calibracao (sem banco)."""
    if contrato.get("versao") != VERSAO:
        raise Recusa("CONTRATO_INVALIDO", "versao %r != %r" % (contrato.get("versao"), VERSAO))
    params = contrato.get("parametros") or {}
    minimo_de_coorte = int(params.get("minimo_de_coorte", 0))
    minimo_por_classe = int(params.get("minimo_por_classe", 0))
    bins = int(params.get("bins_da_curva", 0))
    fracao = params.get("fracao_de_ajuste") or {}
    numerador, denominador = int(fracao.get("numerador", 0)), int(fracao.get("denominador", 0))
    if minimo_de_coorte <= 0 or minimo_por_classe <= 0:
        raise Recusa("CONTRATO_INVALIDO", "minimos de coorte/classe ausentes")
    if not 2 <= bins <= 100:
        raise Recusa("CONTRATO_INVALIDO", "bins_da_curva fora de 2..100: %r" % params.get("bins_da_curva"))
    if not (0 < numerador < denominador):
        raise Recusa("CONTRATO_INVALIDO", "fracao_de_ajuste invalida: %s/%s" % (numerador, denominador))
    limite = int(params.get("limite_de_previsoes", 0))
    if limite <= 0:
        raise Recusa("CONTRATO_INVALIDO", "limite_de_previsoes ausente")

    ascendentes = _limites_do_contrato(dados)

    if calib_relatorio.get("versao") != VERSAO_CALIBRACAO_EXIGIDA:
        raise Recusa("DEPENDENCIA_CALIBRACAO",
                     "relatorio de calibracao declara %r" % calib_relatorio.get("versao"))
    if calib_relatorio.get("card") != CARD_DEPENDENTE:
        raise Recusa("DEPENDENCIA_CALIBRACAO",
                     "relatorio de calibracao e' do card %r" % calib_relatorio.get("card"))
    digest = calib_relatorio.get("hash_do_relatorio") or ""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise Recusa("DEPENDENCIA_CALIBRACAO", "relatorio de calibracao sem hash_do_relatorio valido")
    gate = calib_relatorio.get("gate_de_volume")
    if not isinstance(gate, dict) or not isinstance(gate.get("base_suficiente"), bool):
        raise Recusa("DEPENDENCIA_CALIBRACAO", "relatorio de calibracao sem gate_de_volume medido")
    params_calib = calib_relatorio.get("parametros") or {}
    fracao_calib = params_calib.get("fracao_de_ajuste") or {}
    if (int(fracao_calib.get("numerador", -1)), int(fracao_calib.get("denominador", -1))) != (numerador, denominador):
        raise Recusa("CORTE_DIVERGENTE",
                     "corte ajuste/validacao do relatorio (%s) != do contrato (%s/%s)"
                     % (fracao_calib, numerador, denominador))
    faixas_calib = params_calib.get("faixas_ascendentes")
    if faixas_calib and [f for f in faixas_calib] != [p[0] for p in ascendentes]:
        raise Recusa("FAIXA_DIVERGENTE", "faixas ascendentes divergem do Data Contract")

    proposta = (calib_relatorio.get("proposta") or {}).get("pesos") or {}
    pesos_propostos = proposta.get("pesos") if proposta.get("aprovada") else None
    if pesos_propostos:
        origem, pesos, base_do_peso = "proposta_de_calibracao", pesos_propostos, proposta
    else:
        origem, pesos, base_do_peso = ("contrato_em_vigor", params_calib.get("pesos_em_vigor") or {},
                                       params_calib.get("pesos_em_vigor") or {})
    vetor = pesos_em_centesimos(pesos)
    return {"minimo_de_coorte": minimo_de_coorte, "minimo_por_classe": minimo_por_classe,
            "bins": bins, "numerador": numerador, "denominador": denominador, "limite": limite,
            "limites": {p[0]: (p[1], p[2]) for p in ascendentes},
            "faixas_ascendentes": [p[0] for p in ascendentes],
            "pesos": {c: str(_decimal(pesos[c], c)) for c in COMPONENTES_ESPERADOS},
            "pesos_cents": vetor, "origem_dos_pesos": origem, "proposta_disponivel": bool(pesos_propostos),
            "base_do_peso": base_do_peso,
            "calibracao": {"versao": calib_relatorio.get("versao"),
                           "card": calib_relatorio.get("card"),
                           "hash_do_relatorio": digest,
                           "base_suficiente": gate.get("base_suficiente"),
                           "com_desfecho": gate.get("com_desfecho"),
                           "won": gate.get("won"), "lost": gate.get("lost")}}


# -------------------------------------------------------------------------------------------- #
# 2. Guardas de ambiente (mesmo mecanismo dos irmaos — reusado, nao reescrito)
# -------------------------------------------------------------------------------------------- #
def validar_ambiente(funil, ambiente, porta_banco=None, confirmo=False):
    exigida = getattr(funil, "VERSAO", None)
    if exigida != VERSAO_FUNIL_EXIGIDA:
        raise Recusa("VENDA_DIVERGENTE", "funil declara versao %r" % exigida)
    return funil.validar_ambiente(ambiente, porta_banco, confirmo)


def recusar_producao():
    raise Recusa("PRODUCAO_RECUSADA",
                 "nada nasce em producao (ADR-005); prever em producao e' ato de operador com aprovacao registrada",
                 codigo=4)


# -------------------------------------------------------------------------------------------- #
# 3. Derivacao (pura — testavel sem banco)
# -------------------------------------------------------------------------------------------- #
def bin_do_score(score, quantidade_bins):
    """Bino do score ponderado (0..10000 centesimos) em `quantidade_bins` faixas de igual largura."""
    if score < 0:
        score = 0
    if score > ESCALA_CENTESIMOS:
        score = ESCALA_CENTESIMOS
    indice = (score * quantidade_bins) // (ESCALA_CENTESIMOS + 1)
    return min(indice, quantidade_bins - 1)


def blocos_da_curva(itens, pesos_cents, quantidade_bins, calib):
    """Taxa de vitoria por bino no lado do AJUSTE + PAVA (pool adjacent violators) determinista.

    Devolve (blocos, contagens) — blocos monotonicos nao-decrescentes no score; contagens por bino.
    """
    contagens = [[0, 0] for _ in range(quantidade_bins)]  # [n, won]
    for _org, componentes, y in itens:
        b = bin_do_score(calib.score_cents(componentes, pesos_cents), quantidade_bins)
        contagens[b][0] += 1
        contagens[b][1] += y
    blocos = []
    for b, (n, won) in enumerate(contagens):
        if n == 0:
            continue
        bloco = {"de": b, "ate": b, "n": n, "won": won, "probabilidade": won / n}
        while blocos and blocos[-1]["probabilidade"] > bloco["probabilidade"]:
            anterior = blocos.pop()
            bloco = {"de": anterior["de"], "ate": bloco["ate"], "n": anterior["n"] + bloco["n"],
                     "won": anterior["won"] + bloco["won"],
                     "probabilidade": (anterior["won"] + bloco["won"]) / (anterior["n"] + bloco["n"])}
        blocos.append(bloco)
    return blocos, contagens


def probabilidade_do_bin(blocos, b):
    """Probabilidade do bino: o bloco que o cobre; bino sem base herda o bloco ANTERIOR (regra
    declarada); sem bloco anterior nao ha' previsao (None) — nunca zero, nunca media."""
    anterior = None
    for bloco in blocos:
        if bloco["de"] <= b <= bloco["ate"]:
            return bloco["probabilidade"]
        if bloco["ate"] < b:
            anterior = bloco
        else:
            break
    return anterior["probabilidade"] if anterior else None


def faixa_do_valor(limites, faixas_ascendentes, score_cents_valor):
    for nome in faixas_ascendentes:
        minimo, maximo = limites[nome]
        if minimo <= score_cents_valor <= maximo:
            return nome
    return None


def brier(pontos):
    """Erro quadratico medio da probabilidade prevista (menor e' melhor). None sem pontos."""
    if not pontos:
        return None
    return round(sum((p - y) ** 2 for p, y in pontos) / len(pontos), 6)


def log_loss(pontos, epsilon=1e-6):
    if not pontos:
        return None
    total = 0.0
    for p, y in pontos:
        q = min(max(p, epsilon), 1 - epsilon)
        total += -(y * __import__("math").log(q) + (1 - y) * __import__("math").log(1 - q))
    return round(total / len(pontos), 6)


def avaliar_com_pesos(itens, blocos, quantidade_bins, calib, pesos_cents, base_rate):
    pontos, sem_base = [], 0
    for _org, componentes, y in itens:
        b = bin_do_score(calib.score_cents(componentes, pesos_cents), quantidade_bins)
        p = probabilidade_do_bin(blocos, b)
        if p is None:
            sem_base += 1
            continue
        pontos.append((p, y))
    won = sum(1 for _o, _c, y in itens if y == 1)
    saida = {"organizacoes": len(itens), "won": won, "lost": len(itens) - won,
             "com_previsao": len(pontos), "sem_base": sem_base,
             "cobertura_pct": round(100.0 * len(pontos) / len(itens), 2) if itens else None}
    if not pontos:
        saida.update({"auc": None, "brier": None, "brier_de_base": None, "brier_skill": None,
                      "log_loss": None})
        return saida
    saida["auc"] = calib.auc(sorted((p, y) for p, y in pontos))
    saida["brier"] = brier(pontos)
    saida["brier_de_base"] = brier([(base_rate, y) for _p, y in pontos])
    if saida["brier_de_base"]:
        saida["brier_skill"] = round(1 - saida["brier"] / saida["brier_de_base"], 6)
    else:
        saida["brier_skill"] = None
    saida["log_loss"] = log_loss(pontos)
    return saida


def confiabilidade(itens, blocos, quantidade_bins, calib, pesos_cents):
    """Tabela previsto x observado por bino (a leitura honesta da curva)."""
    linhas = []
    for b in range(quantidade_bins):
        n = won = 0
        for _org, componentes, y in itens:
            if bin_do_score(calib.score_cents(componentes, pesos_cents), quantidade_bins) == b:
                n += 1
                won += y
        p = probabilidade_do_bin(blocos, b)
        observado = round(won / n, 6) if n else None
        linhas.append({
            "bino": b,
            "min": round(b * 100.0 / quantidade_bins, 2),
            "max": round((b + 1) * 100.0 / quantidade_bins, 2),
            "n": n, "won": won,
            "previsto": None if p is None else round(p, 6),
            "observado": observado,
            "desvio": None if (p is None or observado is None) else round(p - observado, 6),
            "com_base": n > 0,
        })
    return linhas


def _digest(obj):
    canonico = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def derivar(contrato, dados, calib, contrato_calib, instrumento, contrato_instrumento, funil,
            contrato_funil, brutas_funil, brutas_proprias, referencia, ambiente, janela,
            calib_relatorio, calib_caminho, gerado_em=None, sha_contrato=None, sha_calib=None,
            sha_contrato_calib=None, sha_instrumento=None, sha_contrato_instrumento=None,
            sha_contrato_funil=None):
    validado = validar_contrato(contrato, dados, calib_relatorio)
    coorte = calib.montar_coorte(contrato_funil, funil, instrumento, brutas_funil, brutas_proprias,
                                 referencia)
    ajuste, validacao = calib.separar_lados(
        coorte, {"numerador": validado["numerador"], "denominador": validado["denominador"]})

    gate = {
        "organizacoes": len(coorte["organizacoes"]),
        "coorte_valida": len(coorte["validos"]),
        "com_desfecho": len(coorte["desfecho"]),
        "won": sum(1 for y in coorte["desfecho"].values() if y == 1),
        "lost": sum(1 for y in coorte["desfecho"].values() if y == 0),
        "sem_desfecho": coorte["lacunas"]["sem_desfecho"],
        "minimo_de_coorte": validado["minimo_de_coorte"],
        "minimo_por_classe": validado["minimo_por_classe"],
    }
    motivos = []
    if gate["com_desfecho"] < validado["minimo_de_coorte"]:
        motivos.append("COORTE_COM_DESFECHO_ABAIXO_DO_MINIMO")
    for rotulo, lado in (("ajuste", ajuste), ("validacao", validacao)):
        n_won = sum(1 for _o, _c, y in lado if y == 1)
        n_lost = len(lado) - n_won
        if n_won < validado["minimo_por_classe"] or n_lost < validado["minimo_por_classe"]:
            motivos.append("CLASSE_ABAIXO_DO_MINIMO_NA_%s" % rotulo.upper())
    gate["base_suficiente"] = not motivos
    gate["motivos"] = motivos
    gate["nota"] = ("base_suficiente=false => ABSTEVE (exit 6): o relatorio sai, a previsao NAO"
                    if motivos else "base suficiente: a curva e' ajustada no lado de ajuste e medida na validacao")

    relatorio = {
        "versao": VERSAO, "card": "TRE-W9-E02-T01", "ambiente": ambiente, "janela": janela,
        "referencia_temporal": referencia.isoformat(),
        "contrato": {"versao": contrato.get("versao"), "sha256": sha_contrato},
        "calibracao": {"arquivo": calib_caminho, "sha256_do_arquivo": sha_calib,
                       "contrato": {"versao": contrato_calib.get("versao"), "sha256": sha_contrato_calib},
                       "hash_do_relatorio": calib_relatorio.get("hash_do_relatorio"),
                       "card": calib_relatorio.get("card"),
                       "origem_dos_pesos": validado["origem_dos_pesos"],
                       "proposta_disponivel": validado["proposta_disponivel"],
                       "pesos": validado["pesos"]},
        "dependencia": {
            "instrumento": {"componente": "efetividade_score.py", "versao": contrato_instrumento.get("versao"),
                            "sha256": sha_instrumento},
            "contrato_do_instrumento": {"sha256": sha_contrato_instrumento},
            "funil": {"componente": "funil.py", "versao": contrato_funil.get("versao"),
                      "sha256": sha_contrato_funil, "funcao_do_desfecho": "alcance_por_organizacao"},
        },
        "base": {"organizacoes": len(coorte["organizacoes"]),
                 "digest": _digest({"organizacoes": sorted(coorte["organizacoes"]),
                                    "validos": sorted(coorte["validos"]),
                                    "desfecho": {k: coorte["desfecho"][k] for k in sorted(coorte["desfecho"])}})},
        "parametros": {"minimo_de_coorte": validado["minimo_de_coorte"],
                       "minimo_por_classe": validado["minimo_por_classe"],
                       "bins_da_curva": validado["bins"],
                       "fracao_de_ajuste": {"numerador": validado["numerador"],
                                            "denominador": validado["denominador"]},
                       "limite_de_previsoes": validado["limite"],
                       "metrica": "probabilidade de ganho (Won=1) por bino do score ponderado; PAVA",
                       "pesos_em_uso": validado["pesos"],
                       "faixas_ascendentes": validado["faixas_ascendentes"]},
        "gate_de_volume": gate,
        "modelo": None, "avaliacao": None, "confiabilidade": None, "previsao_por_organizacao": None,
        "aplicacao": {"aplicado": False, "exige_versao_nova": True, "aprovacao_humana": "pendente",
                      "o_que_falta": ["persistir a probabilidade (coluna/tabela/score_type) exige versao nova "
                                      "do Data Contract (§10) + aprovacao humana registrada (ADR-0004)",
                                      "servir a previsao por HTTP e' passo de operador/homolog (ADR-005)"]},
        "lacunas": coorte["lacunas"],
        "lacunas_declaradas": contrato.get("lacunas_declaradas") or [],
        "fontes": (contrato.get("fontes_do_funil") or []) + (contrato.get("fontes_reusadas_do_instrumento") or []),
    }
    relatorio["gerado_em"] = gerado_em or datetime.now(timezone.utc).isoformat()

    if not gate["base_suficiente"]:
        relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
        return relatorio

    pesos_cents = validado["pesos_cents"]
    blocos, contagens = blocos_da_curva(ajuste, pesos_cents, validado["bins"], calib)
    base_rate = (sum(y for _o, _c, y in ajuste) / len(ajuste)) if ajuste else None
    relatorio["modelo"] = {
        "bins": validado["bins"],
        "contagens_do_ajuste": [{"bino": b, "min": round(b * 100.0 / validado["bins"], 2),
                                 "max": round((b + 1) * 100.0 / validado["bins"], 2),
                                 "n": c[0], "won": c[1],
                                 "taxa_de_vitoria": (round(c[1] / c[0], 6) if c[0] else None)}
                                for b, c in enumerate(contagens)],
        "blocos": [{"de": bl["de"], "ate": bl["ate"], "n": bl["n"], "won": bl["won"],
                    "probabilidade": round(bl["probabilidade"], 6)} for bl in blocos],
        "bins_sem_base": sum(1 for c in contagens if c[0] == 0),
        "taxa_de_base_do_ajuste": None if base_rate is None else round(base_rate, 6),
    }
    relatorio["avaliacao"] = {
        "ajuste": avaliar_com_pesos(ajuste, blocos, validado["bins"], calib, pesos_cents, base_rate),
        "validacao": avaliar_com_pesos(validacao, blocos, validado["bins"], calib, pesos_cents, base_rate),
        "baseline": {"nome": "taxa-base do ajuste (probabilidade constante)",
                     "brier_no_ajuste": brier([(base_rate, y) for _o, _c, y in ajuste])},
    }
    relatorio["confiabilidade"] = confiabilidade(validacao, blocos, validado["bins"], calib, pesos_cents)

    abertas = sorted(set(coorte["validos"]) - set(coorte["desfecho"]))
    itens, sem_base = [], 0
    for org in abertas:
        componentes = coorte["validos"][org]["componentes"]
        score = calib.score_cents(componentes, pesos_cents)
        b = bin_do_score(score, validado["bins"])
        p = probabilidade_do_bin(blocos, b)
        if p is None:
            sem_base += 1
            continue
        itens.append({"organizacao": org, "score": round(score / 100.0, 2), "bino": b,
                      "faixa": faixa_do_valor(validado["limites"], validado["faixas_ascendentes"], score),
                      "probabilidade_de_ganho": round(p, 6)})
    relatorio["previsao_por_organizacao"] = {
        "total": len(itens), "sem_base": sem_base, "organizacoes_em_aberto": len(abertas),
        "limite": validado["limite"], "itens": itens[:validado["limite"]]}
    relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
    return relatorio


def hash_do_relatorio(relatorio):
    limpo = {k: v for k, v in relatorio.items()
             if k not in ("gerado_em", "referencia_temporal", "hash_do_relatorio")}
    return _digest(limpo)


# -------------------------------------------------------------------------------------------- #
# 4. Dashboard (HTML auto-contido: uma string, CSS inline, nenhum recurso externo)
# -------------------------------------------------------------------------------------------- #
def emitir_html(relatorio):
    def esc(v):
        return _html.escape("" if v is None else str(v))

    def tabela(cabecalho, linhas):
        th = "".join("<th>%s</th>" % esc(c) for c in cabecalho)
        tr = "".join("<tr>%s</tr>" % "".join('<td class="n">%s</td>' % esc(c) for c in linha)
                     for linha in linhas)
        return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (th, tr)

    gate = relatorio["gate_de_volume"]
    linhas_gate = [[k, gate.get(k)] for k in ("organizacoes", "coorte_valida", "com_desfecho", "won",
                                              "lost", "sem_desfecho", "minimo_de_coorte",
                                              "minimo_por_classe", "base_suficiente")]
    modelo = relatorio.get("modelo")
    linhas_binos = [[l["bino"], l["min"], l["max"], l["n"], l["won"], l["taxa_de_vitoria"]]
                    for l in (modelo or {}).get("contagens_do_ajuste", [])]
    linhas_blocos = [[b["de"], b["ate"], b["n"], b["won"], b["probabilidade"]]
                     for b in (modelo or {}).get("blocos", [])]
    avaliacao = relatorio.get("avaliacao") or {}
    campos = ("organizacoes", "won", "lost", "com_previsao", "sem_base", "cobertura_pct", "auc",
              "brier", "brier_de_base", "brier_skill", "log_loss")
    linhas_aval = [[c] + [((avaliacao.get(lado) or {}).get(c)) for lado in ("ajuste", "validacao")]
                   for c in campos]
    linhas_conf = [[l["bino"], l["n"], l["previsto"], l["observado"], l["desvio"]]
                   for l in (relatorio.get("confiabilidade") or [])]
    prev = relatorio.get("previsao_por_organizacao") or {}
    linhas_prev = [[i["organizacao"], i["score"], i["faixa"], i["probabilidade_de_ganho"]]
                   for i in prev.get("itens", [])]
    return """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>%s — %s</title><style>
body{font-family:system-ui,sans-serif;margin:24px;color:#1b1b1b}
h1{font-size:20px}h2{font-size:15px;margin-top:24px}
table{border-collapse:collapse;margin-top:8px;font-size:13px}
th,td{border:1px solid #ccc;padding:4px 8px}td.n{text-align:right}
.aviso{background:#fff4e5;border-left:4px solid #e08a00;padding:8px 12px;margin:12px 0}
code{background:#f2f2f2;padding:1px 4px}
</style></head><body>
<h1>Pontuacao preditiva — %s</h1>
<p>Ambiente <code>%s</code> · referencia <code>%s</code> · card <code>%s</code></p>
<div class="aviso"><b>Previsao NAO e' aplicacao.</b> <code>aplicado: false</code>,
<code>exige_versao_nova: true</code>, <code>aprovacao_humana: pendente</code>.</div>
<p>Pesos em uso: <code>%s</code> (origem: <code>%s</code>) · calibracao
<code>%s</code> hash <code>%s</code></p>
<h2>Gate de volume</h2>%s
<h2>Curva no lado do ajuste (bino x taxa observada)</h2>%s
<h2>Blocos monotonicos (PAVA)</h2>%s
<h2>Avaliacao fora da amostra</h2>%s
<h2>Confiabilidade na validacao</h2>%s
<h2>Previsao por organizacao (em aberto) — %s itens</h2>%s
<p>hash_do_relatorio <code>%s</code></p></body></html>""" % (
        esc(relatorio["versao"]), esc(relatorio["card"]), esc(relatorio["card"]),
        esc(relatorio["ambiente"]), esc(relatorio["referencia_temporal"])[:19], esc(relatorio["card"]),
        esc(json.dumps(relatorio["calibracao"]["pesos"], ensure_ascii=False)),
        esc(relatorio["calibracao"]["origem_dos_pesos"]), esc(relatorio["calibracao"]["hash_do_relatorio"])[:16],
        esc(relatorio["calibracao"]["hash_do_relatorio"])[:16],
        tabela(["campo", "valor"], linhas_gate),
        tabela(["bino", "min", "max", "n", "won", "taxa"], linhas_binos),
        tabela(["de", "ate", "n", "won", "probabilidade"], linhas_blocos),
        tabela(["metrica", "ajuste", "validacao"], linhas_aval),
        tabela(["bino", "n", "previsto", "observado", "desvio"], linhas_conf),
        esc(len(prev.get("itens", []))),
        tabela(["organizacao", "score", "faixa", "probabilidade_de_ganho"], linhas_prev),
        esc(relatorio["hash_do_relatorio"]))


# -------------------------------------------------------------------------------------------- #
# 5. Execucao
# -------------------------------------------------------------------------------------------- #
def _resumo_texto(relatorio):
    gate = relatorio["gate_de_volume"]
    if not gate["base_suficiente"]:
        return "PONTUACAO_ABSTEVE_VOLUME coorte=%d won=%d lost=%d motivos=%s hash=%s" % (
            gate["com_desfecho"], gate["won"], gate["lost"], ",".join(gate["motivos"]),
            relatorio["hash_do_relatorio"][:16])
    aval = relatorio["avaliacao"]
    prev = relatorio["previsao_por_organizacao"]
    return " ".join([
        "PONTUACAO_PREDITIVA_OK",
        "coorte=%d" % gate["com_desfecho"],
        "origem_dos_pesos=%s" % relatorio["calibracao"]["origem_dos_pesos"],
        "auc_validacao=%s" % (aval["validacao"] or {}).get("auc"),
        "brier_validacao=%s" % (aval["validacao"] or {}).get("brier"),
        "brier_skill=%s" % (aval["validacao"] or {}).get("brier_skill"),
        "blocos=%d" % len(relatorio["modelo"]["blocos"]),
        "previsoes=%d" % prev["total"],
        "hash=%s" % relatorio["hash_do_relatorio"][:16],
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pontuacao preditiva v1 — TRE-W9-E02-T01")
    parser.add_argument("--ambiente", required=True, choices=list(AMBIENTES))
    parser.add_argument("--porta-banco", default=os.environ.get("TRE_PONTUACAO_PORTA_BANCO"))
    parser.add_argument("--calibracao", default=None, help="relatorio JSON da calibracao (W9-E01-T01)")
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--agora", default=None)
    parser.add_argument("--saida", default=None)
    parser.add_argument("--formato", default="json,html", choices=["json", "html", "json,html"])
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--planejar", action="store_true")
    parser.add_argument("--conferir", action="store_true")
    parser.add_argument("--contrato", default=CONTRATO_PADRAO)
    parser.add_argument("--modulo-calibracao", default=CALIBRACAO_PADRAO)
    parser.add_argument("--contrato-calibracao", default=CONTRATO_CALIBRACAO_PADRAO)
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
        calib, contrato_calib = carregar_calibracao(args.modulo_calibracao, args.contrato_calibracao)

        if args.conferir:
            calib_relatorio = carregar_json(args.calibracao, "DEPENDENCIA_CALIBRACAO",
                                            "DEPENDENCIA_CALIBRACAO") if args.calibracao else None
            if calib_relatorio is None:
                validado = validar_contrato(contrato, dados, {
                    "versao": VERSAO_CALIBRACAO_EXIGIDA, "card": CARD_DEPENDENTE,
                    "hash_do_relatorio": "0" * 64, "gate_de_volume": {"base_suficiente": True},
                    "parametros": {"fracao_de_ajuste": (contrato.get("parametros") or {}).get("fracao_de_ajuste"),
                                   "pesos_em_vigor": (dados.get("scores") or {}).get("priority_weights") or {}}})
                print("PONTUACAO_CONFERIR_OK contrato=%s instrumento=%s calibracao=%s funil=%s "
                      "bins=%d minimo_de_coorte=%d (sem --calibracao: o corte do relatorio nao foi conferido)"
                      % (contrato["versao"], contrato_instrumento.get("versao"),
                         contrato_calib.get("versao"), contrato_funil.get("versao"), validado["bins"],
                         validado["minimo_de_coorte"]))
                return 0
            validado = validar_contrato(contrato, dados, calib_relatorio)
            print("PONTUACAO_CONFERIR_OK contrato=%s calibracao=%s hash=%s origem_dos_pesos=%s bins=%d"
                  % (contrato["versao"], calib_relatorio.get("versao"),
                     calib_relatorio["hash_do_relatorio"][:16], validado["origem_dos_pesos"],
                     validado["bins"]))
            return 0
        if args.planejar:
            params = contrato.get("parametros") or {}
            print("PONTUACAO_PLANO versao=%s unidade=organization" % contrato["versao"])
            print("  gate: minimo_de_coorte=%s minimo_por_classe=%s"
                  % (params.get("minimo_de_coorte"), params.get("minimo_por_classe")))
            print("  curva: %s binos do score ponderado + PAVA (monotona nao-decrescente)"
                  % params.get("bins_da_curva"))
            print("  corte ajuste/validacao: sha256(organization_id) %% %s < %s (o MESMO da calibracao)"
                  % ((params.get("fracao_de_ajuste") or {}).get("denominador"),
                     (params.get("fracao_de_ajuste") or {}).get("numerador")))
            print("  dependencia: relatorio de %s (card %s) -> pesos e desfecho; funil.py -> alcance"
                  % (VERSAO_CALIBRACAO_EXIGIDA, CARD_DEPENDENTE))
            return 0

        validar_ambiente(funil, args.ambiente, args.porta_banco, args.confirmo)
        if not args.calibracao:
            raise Recusa("DEPENDENCIA_CALIBRACAO",
                         "sem o relatorio da calibracao (--calibracao) nao ha' previsao: "
                         "os pesos e a particao vem do card W9-E01-T01")
        calib_relatorio = carregar_json(args.calibracao, "DEPENDENCIA_CALIBRACAO", "DEPENDENCIA_CALIBRACAO")
        if args.porta_banco is None:
            raise Recusa("PORTA_BANCO_AUSENTE", "sem banco nao ha' medicao: use --planejar/--conferir")

        desde = funil.normalizar_instante(args.desde, "--desde")
        ate = funil.normalizar_instante(args.ate, "--ate")
        if args.agora:
            referencia = datetime.fromisoformat(funil.normalizar_instante(args.agora, "--agora").replace("Z", "+00:00"))
        else:
            referencia = datetime.now(timezone.utc)

        brutas_funil, brutas_proprias = calib.ler_tudo(instrumento, funil, contrato_instrumento,
                                                       contrato_funil, args.porta_banco, desde, ate)
        relatorio = derivar(contrato, dados, calib, contrato_calib, instrumento, contrato_instrumento,
                            funil, contrato_funil, brutas_funil, brutas_proprias, referencia,
                            args.ambiente, {"desde": desde, "ate": ate}, calib_relatorio, args.calibracao,
                            sha_contrato=instrumento.sha256_de_arquivo(args.contrato),
                            sha_calib=instrumento.sha256_de_arquivo(args.calibracao),
                            sha_contrato_calib=instrumento.sha256_de_arquivo(args.contrato_calibracao),
                            sha_instrumento=instrumento.sha256_de_arquivo(args.instrumento),
                            sha_contrato_instrumento=instrumento.sha256_de_arquivo(args.contrato_instrumento),
                            sha_contrato_funil=instrumento.sha256_de_arquivo(args.contrato_funil))

        saida_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
        saida_html = emitir_html(relatorio)
        for token_nome in TOKENS_DE_SEGREDO:
            token = os.environ.get(token_nome)
            if token and (token in saida_json or token in saida_html):
                raise Recusa("SENHA_VAZADA", "valor de %s presente na evidencia" % token_nome,
                             codigo=CODIGO_SEGREDO)
        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "pontuacao-preditiva.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "pontuacao-preditiva.html"), "w", encoding="utf-8") as fh:
                    fh.write(saida_html)
        print(_resumo_texto(relatorio))
        return 0 if relatorio["gate_de_volume"]["base_suficiente"] else CODIGO_VOLUME
    except Recusa as exc:
        print("RECUSA %s %s" % (exc.motivo, exc.detalhe))
        return exc.codigo
    except Exception as exc:  # noqa: BLE001 — instrumento/funil/calibracao tem a PROPRIA Recusa
        motivo = getattr(exc, "motivo", None)
        if motivo is None:
            raise
        print("RECUSA %s %s" % (motivo, getattr(exc, "detalhe", "")))
        return getattr(exc, "codigo", 3)


if __name__ == "__main__":
    sys.exit(main())
