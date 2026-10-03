#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conversao por segmento — `conversao-segmento-v1` — card TRE-W8-E02-T01 (W8 / Analytics).

O que este componente FAZ (e so' isto): RECORTA o funil ja' derivado (`funil-v1`, card
TRE-W8-E01-T01) por EIXO de segmentacao declarado no contrato, com vocabulario fechado vindo do
Data Contract V1 (faixa de funcionarios — doc 03 §5; faixas de prioridade/tier — doc 03 §4), e
compara a conversao de cada segmento com a conversao da base inteira.

Decisao de arquitetura (deliberada, e' o ponto do card depender do W8-E01-T01):
  A DERIVACAO DO FUNIL E' UMA SO'. Este componente **importa** `funil.py` e usa as MESMAS funcoes
  de contrato, guarda, execucao em leitura pura, resolucao de evidencia, alcance cumulativo,
  terminal Won/Lost e ramo Nurture. Nao existe segunda implementacao de estagio, de ordem, de
  alcance nem de atribuicao: o relatorio por segmento e' o MESMO funil, recortado por organizacao.
  Duas copias da regra de funil divergiriam em silencio — e' exatamente o defeito que este desenho
  evita.

Invariantes deste componente (cada um com item de suite/aceite):

  1. ESTAGIO, ORDEM, ALCANCE E ATRIBUICAO NAO SE REIMPLEMENTAM: vem de `funil.py` (contrato
     `funil-v1` + `funnel_stages` congelado). Aqui so' entra o RECORTE por eixo.
  2. SEGMENTO SO' EXISTE SE DECLARADO: os valores validos de cada eixo sao os do Data Contract V1
     (`vocabularies.employee_band`; `scores.tiers`). Valor fora do vocabulario NAO vira segmento —
     vai para a lacuna `fora_do_vocabulario` do eixo; organizacao sem o dado vai para `SEM_DADO`.
     Nada e' atribuido por suposicao (mesma regra do rotulo de estagio, D04/D06/D07).
  3. TIER E' DERIVADO, NAO INVENTADO: a faixa de prioridade sai da PONTUACAO VIGENTE (score
     PRIORITY com `score_version` preenchida, contrato §8) aplicada as faixas congeladas do
     contrato de dados. Score sem versao nao qualifica; sem pontuacao vigente -> `SEM_DADO`.
  4. COBERTURA E' DECLARADA, NUNCA MAQUIADA: todo relatorio traz, por eixo, quantas organizacoes
     foram classificadas, quantas ficaram em `SEM_DADO` e quantas em `fora_do_vocabulario`; a
     soma tem de fechar com a base.
  5. AMOSTRA PEQUENA E' DITA: segmento com menos que `amostra_minima` organizacoes (derivada do
     contrato) carrega `amostra_pequena: true` — leitura honesta, nao "segmento vencedor".
  6. TODA LACUNA DO FUNIL VALE PARA A BASE (o recorte nao inventa lacuna por segmento).
  7. LEITURA PURA: SO' `SELECT`, em transacao READ ONLY (mecanismo do `funil.executar_consulta`,
     que usa dois `-c`: `SET default_transaction_read_only = on` e depois a consulta).
  8. GUARDAS DE AMBIENTE (ADR-005): `dev` exige porta de banco LOCAL (`docker exec -i pg-<...>
     psql`); `homolog` exige `--confirmo`; `prod` RECUSA por desenho (exit 4).
  9. SEGREDO: se o valor de `TRE_CONVERSAO_TOKEN` aparecer na evidencia, a rodada e' recusada.
 10. PRIVACIDADE: o recorte usa `employee_band` e a PONTUACAO — nunca nome, e-mail, telefone,
     CNPJ ou dominio; a saida carrega contagem e taxa, nunca organizacao nominal.

Saida: relatorio JSON + HTML auto-contido. Exit code:
  0 = relatorio gerado (ou planilha/conferencia, sem banco) · 2 = uso errado · 3 = recusa
  (contrato/fonte/guarda/banco) · 4 = producao recusada · 5 = segredo vazado.
"""

import argparse
import hashlib
import html as _html
import json
import os
import sys
from datetime import datetime, timezone

VERSAO = "conversao-segmento-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "conversao-segmento-v1.json")

# Reuso deliberado do funil (W8-E01-T01): contrato, guardas, leitura pura, derivacao. Ver invariante 1.
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)
import funil  # noqa: E402

CODIGO_SEGREDO = 5
SENTINELA = {"SEM_DADO", "FORA_DO_VOCABULARIO"}


class Recusa(funil.Recusa):
    """Mesma recusa do funil (motivo, detalhe, codigo) — o tratamento de erro nao se duplica."""


# --------------------------------------------------------------------------------------------
# 0. Contratos (o do componente + o do funil + o de dados)
# --------------------------------------------------------------------------------------------
def caminho_contrato_funil(raiz):
    return os.path.join(raiz, "hermes", "agentes", "analytics", "funil-v1.json")


def carregar_json(caminho, motivo_ausente):
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise Recusa(motivo_ausente, caminho)
    except json.JSONDecodeError as exc:
        raise Recusa("CONTRATO_ILEGIVEL", str(exc))


def validar_contrato(contrato, contrato_dados, contrato_funil, raiz):
    """Contrato do recorte x contrato do funil x contrato de dados. Nada de vocabulario proprio."""
    if contrato.get("versao") != VERSAO:
        raise Recusa("VERSAO_DO_CONTRATO_DESCONHECIDA", str(contrato.get("versao")))
    eixos = contrato.get("eixos") or []
    if not eixos:
        raise Recusa("CONTRATO_SEM_EIXOS")
    nomes = [e.get("nome") for e in eixos]
    if len(set(nomes)) != len(nomes) or any(not n for n in nomes):
        raise Recusa("EIXO_DUPLICADO_OU_SEM_NOME", str(nomes))
    derivacoes = {"igualdade", "faixa_de_pontuacao"}
    for eixo in eixos:
        for chave in ("nome", "titulo", "fonte", "valores", "regra", "derivacao"):
            if not eixo.get(chave):
                raise Recusa("EIXO_INCOMPLETO", "%s sem %s" % (eixo.get("nome"), chave))
        if eixo["derivacao"] not in derivacoes:
            raise Recusa("DERIVACAO_DE_EIXO_DESCONHECIDA", str(eixo["derivacao"]))
        if eixo["fonte"] not in (contrato.get("fontes") or {}):
            raise Recusa("EIXO_COM_FONTE_NAO_DECLARADA", "%s -> %s" % (eixo["nome"], eixo["fonte"]))
        esperado = valores_oficiais_do_eixo(eixo, contrato_dados)
        if list(eixo["valores"]) != esperado:
            raise Recusa(
                "EIXO_DIVERGE_DO_CONTRATO_DE_DADOS",
                "%s: componente=%s contrato_de_dados=%s" % (eixo["nome"], eixo["valores"], esperado),
            )

    # O funil recortado tem de ser O MESMO funil: estagios, ordem, niveis, lateral e fontes.
    funil.validar_contrato(contrato_funil, contrato_dados)
    if contrato.get("estagios_esperados") != [e["nome"] for e in contrato_funil["estagios"]]:
        raise Recusa("ESTAGIOS_DIVERGEM_DO_FUNIL", str(contrato.get("estagios_esperados")))
    if contrato.get("contrato_de_funil") != contrato_funil.get("versao"):
        raise Recusa("CONTRATO_DE_FUNIL_NAO_DECLARADO", str(contrato.get("contrato_de_funil")))

    fontes = contrato.get("fontes") or {}
    tabelas = {t.get("name") for t in (contrato_dados.get("tables") or [])}
    for fid, fonte in fontes.items():
        if fonte.get("tabela") not in tabelas:
            raise Recusa("FONTE_FORA_DO_CONTRATO_DE_DADOS", "%s -> %s" % (fid, fonte.get("tabela")))
    if contrato.get("estagios_esperados") != list(contrato_dados.get("funnel_stages") or []):
        raise Recusa("ESTAGIOS_DIVERGEM_DO_CONTRATO_DE_DADOS")
    minima = contrato.get("amostra_minima")
    if not isinstance(minima, int) or minima < 1:
        raise Recusa("AMOSTRA_MINIMA_INVALIDA", str(minima))
    return True


def valores_oficiais_do_eixo(eixo, contrato_dados):
    """Vocabulario do eixo vem do contrato de dados — este componente nao inventa valor."""
    origem = eixo.get("vocabulario")
    if origem == "employee_band":
        return list((contrato_dados.get("vocabularies") or {}).get("employee_band") or [])
    if origem == "tiers":
        return [t.get("name") for t in ((contrato_dados.get("scores") or {}).get("tiers") or [])]
    raise Recusa("VOCABULARIO_DESCONHECIDO", str(origem))


def faixas_de_tier(contrato_dados):
    return [dict(t) for t in ((contrato_dados.get("scores") or {}).get("tiers") or [])]


# --------------------------------------------------------------------------------------------
# 1. Consultas do recorte (montadas a partir do contrato; nunca SQL literal fora do contrato)
# --------------------------------------------------------------------------------------------
def montar_consultas(contrato, desde=None, ate=None):
    s = funil._janela("created_at", desde, ate)
    q = {
        "FAIXA_FUNCIONARIOS": (
            "SELECT id::text || '|' || COALESCE(employee_band, '') "
            "FROM sales_intelligence.organizations WHERE deleted_at IS NULL" + s
        ),
        "PONTUACAO_PRIORITY": (
            "SELECT organization_id::text || '|' || score_value::text || '|' || "
            "to_char(calculated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS') "
            "FROM sales_intelligence.scores "
            "WHERE score_type = 'PRIORITY' AND organization_id IS NOT NULL "
            "AND COALESCE(score_version, '') <> ''" + funil._janela("calculated_at", desde, ate)
        ),
    }
    declaradas = set((contrato.get("fontes") or {}).keys())
    montadas = set(q.keys())
    if declaradas != montadas:
        raise Recusa(
            "FONTES_DIVERGEM_DO_CONTRATO",
            "so' no contrato=%s so' no componente=%s" % (sorted(declaradas - montadas), sorted(montadas - declaradas)),
        )
    funil.auditar_fonte(q)
    return q


# --------------------------------------------------------------------------------------------
# 2. Leitura dos atributos de segmento (puro — testavel sem banco)
# --------------------------------------------------------------------------------------------
def extrair_eixo(linhas):
    """Linhas `id|valor` -> {id: valor}. Valor vazio vira '' (e o recorte decide o sentinela)."""
    valores = {}
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) < 2 or not campos[0]:
            continue
        valor = campos[1].strip()
        if valores.get(campos[0], "") == "":
            valores[campos[0]] = valor
    return valores


def pontuacao_vigente(linhas):
    """{id: valor} com a pontuacao VIGENTE: maior `calculated_at`; empate pelo maior valor.

    Deterministico de proposito: a regra fica no codigo E no contrato (eixo.regra).
    """
    vigente = {}
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) < 3 or not campos[0]:
            continue
        try:
            valor = float(campos[1])
        except ValueError:
            continue
        chave = (campos[2], valor)
        if campos[0] not in vigente or chave > vigente[campos[0]][0]:
            vigente[campos[0]] = (chave, valor)
    return {org: par[1] for org, par in vigente.items()}


def tier_da_pontuacao(valor, faixas):
    """Faixa congelada do contrato de dados; fora de qualquer faixa devolve None (lacuna)."""
    for faixa in faixas:
        if faixa["min"] <= valor <= faixa["max"]:
            return faixa["name"]
    return None


def classificar_eixo(contrato, eixo, organizacoes, valores_por_org, faixas=None):
    """{org conhecida: valor declarado | sentinela}. A forma de derivar vem DECLARADA no eixo."""
    por_org = {}
    for org in organizacoes:
        bruto = valores_por_org.get(org, "")
        if eixo["derivacao"] == "faixa_de_pontuacao":
            pontos = pontuacao_bruta(bruto) if bruto != "" else None
            valor = tier_da_pontuacao(pontos, faixas or []) if pontos is not None else None
        else:
            valor = eixo_valor_normalizado(eixo, bruto)
        if valor is None:
            por_org[org] = "SEM_DADO" if bruto == "" else "FORA_DO_VOCABULARIO"
        else:
            por_org[org] = valor
    return por_org


def pontuacao_bruta(bruto):
    """O bruto do eixo de faixa chega como o VALOR da pontuacao vigente (str, do Python puro)."""
    try:
        return float(bruto)
    except (TypeError, ValueError):
        return None


def eixo_valor_normalizado(eixo, bruto):
    """Igualdade EXATA contra o vocabulario declarado (com a normalizacao do funil): nada de semelhanca."""
    chave = funil.normalizar_rotulo(bruto)
    if not chave:
        return None
    for valor in eixo["valores"]:
        if funil.normalizar_rotulo(valor) == chave:
            return valor
    return None


def valores_do_eixo(contrato, eixo, brutas, organizacoes, contrato_dados):
    """Le as fontes do eixo e devolve {org: bruto} (a leitura antes de qualquer classificacao)."""
    if eixo["fonte"] == "FAIXA_FUNCIONARIOS":
        return extrair_eixo(brutas.get("FAIXA_FUNCIONARIOS", []))
    if eixo["fonte"] == "PONTUACAO_PRIORITY":
        return {org: str(valor) for org, valor in pontuacao_vigente(brutas.get("PONTUACAO_PRIORITY", [])).items()}
    raise Recusa("EIXO_COM_FONTE_NAO_DECLARADA", eixo["fonte"])


# --------------------------------------------------------------------------------------------
# 3. Derivacao (puro): o funil recortado por segmento
# --------------------------------------------------------------------------------------------
def relatorio_do_recorte(contrato_funil, organizacoes, evidencia, lacunas, selecionadas,
                         ambiente="dev", janela=None, gerado_em=None):
    """Roda a derivacao DO FUNIL sobre o subconjunto de organizacoes — uma unica regra de funil."""
    organizacoes = {org: info for org, info in organizacoes.items() if org in selecionadas}
    evidencia = {nome: set(orgs) & selecionadas for nome, orgs in evidencia.items()}
    return funil.calcular_funil(contrato_funil, organizacoes, evidencia, dict(lacunas or {}))


def quantos_no_top(rel):
    return rel["resumo"]["total_organizacoes"]


def taxa(rel, estagio):
    for e in rel["estagios"]:
        if e["nome"] == estagio:
            return e
    return None


def alcancadas(rel, nome):
    for e in rel["estagios"]:
        if e["nome"] == nome:
            return e["alcancadas"]
    raise Recusa("ESTAGIO_AUSENTE_NO_RECORTE", nome)


def montar_recorte(contrato_funil, organizacoes, evidencia, lacunas, segmento, selecionadas,
                   base_rel, amostra_minima):
    """Relatorio do segmento + comparacao com a base inteira (indice de conversao)."""
    rel = relatorio_do_recorte(contrato_funil, organizacoes, evidencia, lacunas, selecionadas)
    total = quantos_no_top(rel)
    won = alcancadas(rel, "Won")
    perdido = alcancadas(rel, "Lost")
    topo_taxa = taxa(rel, "Descoberto")["conversao_do_topo_pct"]
    base_total = quantos_no_top(base_rel)
    base_pct = None if not base_total else round(100.0 * alcancadas(base_rel, "Won") / base_total, 2)
    pct_won = None if not total else round(100.0 * won / total, 2)
    return {
        "segmento": segmento,
        "organizacoes": total,
        "amostra_pequena": total < amostra_minima,
        "won": won,
        "lost": perdido,
        "nurture": rel["resumo"]["nurture"],
        "em_aberto": rel["resumo"]["em_aberto"],
        "taxa_conversao_pct": pct_won,
        "taxa_conversao_da_base_pct": base_pct,
        "indice_vs_base_pct": (None if (pct_won is None or not base_pct) else round(100.0 * pct_won / base_pct, 2)),
        "conversao_do_topo_pct": topo_taxa,
        "estagios": rel["estagios"],
        "resumo": rel["resumo"],
    }


def calcular_relatorio(contrato, contrato_funil, contrato_dados, brutas, organizacoes, evidencia,
                       lacunas, ambiente, janela, gerado_em=None):
    if gerado_em is None:
        gerado_em = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_rel = funil.calcular_funil(contrato_funil, organizacoes, evidencia, dict(lacunas))
    total_base = len(organizacoes)

    eixos_saida = []
    for eixo in contrato["eixos"]:
        valores_por_org = valores_do_eixo(contrato, eixo, brutas, organizacoes, contrato_dados)
        por_org = classificar_eixo(contrato, eixo, organizacoes, valores_por_org,
                                   faixas_de_tier(contrato_dados))
        buckets = {valor: set() for valor in eixo["valores"]}
        buckets["SEM_DADO"] = set()
        buckets["FORA_DO_VOCABULARIO"] = set()
        for org, valor in por_org.items():
            buckets[valor].add(org)
        segmentos = []
        for valor in list(eixo["valores"]) + ["SEM_DADO", "FORA_DO_VOCABULARIO"]:
            if valor in SENTINELA and not buckets[valor]:
                continue
            segmentos.append(montar_recorte(contrato_funil, organizacoes, evidencia, lacunas,
                                            valor, buckets[valor], base_rel, contrato["amostra_minima"]))
        classificadas = sum(1 for v in por_org.values() if v not in SENTINELA)
        soma = sum(s["organizacoes"] for s in segmentos)
        if soma != total_base:
            raise Recusa("RECORTE_NAO_FECHA_COM_A_BASE", "%s: %d != %d" % (eixo["nome"], soma, total_base))
        eixos_saida.append({
            "nome": eixo["nome"],
            "titulo": eixo["titulo"],
            "fonte": eixo["fonte"],
            "regra": eixo["regra"],
            "valores": list(eixo["valores"]),
            "classificadas": classificadas,
            "cobertura_pct": None if not total_base else round(100.0 * classificadas / total_base, 2),
            "sem_dado": len(buckets["SEM_DADO"]),
            "fora_do_vocabulario": len(buckets["FORA_DO_VOCABULARIO"]),
            "segmentos": segmentos,
        })

    relatorio = {
        "versao": VERSAO,
        "card": contrato.get("card"),
        "ambiente": ambiente,
        "janela": dict(janela or {"desde": None, "ate": None}),
        "base": {
            "organizacoes": total_base,
            "digest": funil._digest({"organizacoes": sorted(organizacoes)}),
            "amostra_minima": contrato["amostra_minima"],
        },
        "global": {
            "total_organizacoes": base_rel["resumo"]["total_organizacoes"],
            "won": base_rel["resumo"]["won"],
            "lost": base_rel["resumo"]["lost"],
            "nurture": base_rel["resumo"]["nurture"],
            "em_aberto": base_rel["resumo"]["em_aberto"],
            "taxa_conversao_pct": None if not total_base
            else round(100.0 * base_rel["resumo"]["won"] / total_base, 2),
            "estagios": base_rel["estagios"],
        },
        "eixos": eixos_saida,
        "lacunas": dict(lacunas),
        "fontes": {fid: len(linhas) for fid, linhas in sorted(brutas.items())},
        "lacunas_declaradas": contrato.get("lacunas_declaradas", []),
        "gerado_em": gerado_em,
        "contrato": {
            "versao": contrato["versao"],
            "sha256": funil.sha256_de_arquivo(CONTRATO_PADRAO),
            "contrato_de_funil": contrato_funil["versao"],
            "sha256_do_funil": None,
        },
    }
    relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
    return relatorio


def hash_do_relatorio(relatorio):
    limpo = {k: v for k, v in relatorio.items() if k not in ("gerado_em", "hash_do_relatorio")}
    canonico = json.dumps(limpo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------
# 4. HTML auto-contido (uma string, CSS inline, nenhum recurso externo)
# --------------------------------------------------------------------------------------------
def emitir_html(relatorio):
    blocos = []
    for eixo in relatorio["eixos"]:
        linhas = []
        for seg in eixo["segmentos"]:
            if seg["taxa_conversao_pct"] is None:
                taxa_txt = "—"
            else:
                taxa_txt = "%.2f%%" % seg["taxa_conversao_pct"]
            indice = "—" if seg["indice_vs_base_pct"] is None else "%.2f%%" % seg["indice_vs_base_pct"]
            marca = " <span class=\"aviso\">amostra pequena</span>" if seg["amostra_pequena"] else ""
            classe = "sentinela" if seg["segmento"] in SENTINELA else "valor"
            linhas.append(
                '<tr class="%s"><td>%s%s</td><td class="n">%d</td><td class="n">%d</td>'
                '<td class="n">%d</td><td class="n">%s</td><td class="n">%s</td></tr>'
                % (classe, _html.escape(seg["segmento"]), marca, seg["organizacoes"], seg["won"],
                   seg["lost"], taxa_txt, indice)
            )
        blocos.append(
            "<h2>%s <span class=\"meta\">(%s · cobertura %s%% · sem dado %d · fora do vocabulario %d)</span></h2>\n"
            "<table><thead><tr><th>%s</th><th>Organizacoes</th><th>Won</th><th>Lost</th>"
            "<th>Taxa de conversao</th><th>Indice vs base</th></tr></thead><tbody>\n%s\n</tbody></table>\n"
            % (_html.escape(eixo["titulo"]), _html.escape(eixo["regra"]),
               ("" if eixo["cobertura_pct"] is None else "%.2f" % eixo["cobertura_pct"]),
               eixo["sem_dado"], eixo["fora_do_vocabulario"], _html.escape(eixo["nome"]),
               "\n".join(linhas))
        )
    gaps = "".join("<li>%s</li>" % _html.escape(g) for g in relatorio.get("lacunas_declaradas", []))
    return (
        "<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Conversao por segmento — TRE-W8-E02-T01</title>\n<style>\n"
        "body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#111}\n"
        "h1{font-size:20px;margin:0 0 4px 0}\nh2{font-size:15px;margin:20px 0 6px 0}\n"
        ".meta{color:#555;font-size:12px;font-weight:400}\n"
        "table{border-collapse:collapse;width:100%%;font-size:13px;margin-bottom:8px}\n"
        "th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}\n"
        "td.n{text-align:right;font-variant-numeric:tabular-nums}\n"
        "tr.sentinela td{color:#666;font-style:italic}\n.aviso{color:#b45309;font-size:11px}\n"
        ".lacunas{font-size:12px;color:#555}\n</style>\n</head>\n<body>\n"
        "<h1>Conversao por segmento — Transformativa Revenue Engine</h1>\n"
        "<div class=\"meta\">card %s · contrato %s (sha256 %s) · funil %s · ambiente %s · janela %s · "
        "gerado em %s · hash do relatorio %s · base %d organizacoes · taxa global %s%%</div>\n"
        "<p class=\"meta\">O funil e' UMA derivacao so' (funil-v1, card TRE-W8-E01-T01): este relatorio "
        "recorta a mesma base por eixo declarado, sem reimplementar estagio, ordem, alcance ou atribuicao.</p>\n"
        % (_html.escape(str(relatorio.get("card"))), _html.escape(relatorio["contrato"]["versao"]),
           _html.escape(relatorio["contrato"]["sha256"][:16]),
           _html.escape(str(relatorio["contrato"].get("contrato_de_funil"))),
           _html.escape(relatorio["ambiente"]), _html.escape(str(relatorio.get("janela"))),
           _html.escape(relatorio["gerado_em"]), _html.escape(relatorio["hash_do_relatorio"][:16]),
           relatorio["base"]["organizacoes"],
           ("" if relatorio["global"]["taxa_conversao_pct"] is None
            else "%.2f" % relatorio["global"]["taxa_conversao_pct"]))
        + "\n".join(blocos)
        + "<h2>Lacunas declaradas</h2>\n<ul class=\"lacunas\">\n" + gaps + "\n</ul>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------------------------
# 5. CLI
# --------------------------------------------------------------------------------------------
def _resumo_texto(relatorio):
    partes = ["CONVERSAO_SEGMENTO_OK", "base=%d" % relatorio["base"]["organizacoes"]]
    for eixo in relatorio["eixos"]:
        melhores = [s for s in eixo["segmentos"] if not s["amostra_pequena"]
                    and s["indice_vs_base_pct"] is not None]
        if melhores:
            melhor = max(melhores, key=lambda s: s["indice_vs_base_pct"])
            partes.append("%s:melhor=%s(%s%%)" % (eixo["nome"], melhor["segmento"].replace(" ", "_"),
                                                  melhor["indice_vs_base_pct"]))
        partes.append("%s:cobertura=%s%%" % (eixo["nome"], eixo["cobertura_pct"]))
    partes.append("hash=%s" % relatorio["hash_do_relatorio"][:16])
    return " ".join(partes)


def construir_relatorio(contrato, contrato_funil, contrato_dados, porta_banco, ambiente, desde, ate,
                        gerado_em=None):
    if porta_banco is None:
        raise Recusa("PORTA_BANCO_AUSENTE", "sem banco nao ha conversao: use --planejar/--conferir")
    consultas = dict(funil.montar_consultas(contrato_funil, desde, ate))
    consultas.update(montar_consultas(contrato, desde, ate))
    brutas = funil.ler_fontes(porta_banco, consultas)
    organizacoes = funil.extrair_base(brutas.get("BASE_ORGANIZACOES", []))
    evidencia, lacunas = funil.resolver_evidencia(contrato_funil, brutas, organizacoes)
    return calcular_relatorio(contrato, contrato_funil, contrato_dados, brutas, organizacoes,
                              evidencia, lacunas, ambiente, {"desde": desde, "ate": ate},
                              gerado_em=gerado_em)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Conversao por segmento v1 — TRE-W8-E02-T01")
    parser.add_argument("--ambiente", required=True, choices=list(funil.AMBIENTES))
    parser.add_argument("--porta-banco", default=os.environ.get("TRE_CONVERSAO_PORTA_BANCO")
                        or os.environ.get("TRE_FUNIL_PORTA_BANCO"))
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--saida", default=None, help="diretorio de saida do relatorio")
    parser.add_argument("--formato", default="json,html", choices=["json", "html", "json,html"])
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--planejar", action="store_true", help="imprime o plano declarado (sem banco)")
    parser.add_argument("--conferir", action="store_true", help="valida contratos e guardas (sem banco)")
    parser.add_argument("--contrato", default=CONTRATO_PADRAO)
    parser.add_argument("--raiz", default=os.path.join(AQUI, "..", "..", ".."))
    args = parser.parse_args(argv)

    try:
        raiz = os.path.abspath(args.raiz)
        if args.ambiente == "prod":
            raise Recusa(
                "PRODUCAO_RECUSADA",
                "nada nasce em producao (ADR-005); ler/promover em producao e' ato de operador com aprovacao registrada",
                codigo=4,
            )
        contrato = carregar_json(args.contrato, "CONTRATO_AUSENTE")
        contrato_funil = carregar_json(caminho_contrato_funil(raiz), "CONTRATO_DO_FUNIL_AUSENTE")
        contrato_dados = carregar_json(funil.caminho_contrato_de_dados(raiz), "CONTRATO_DE_DADOS_AUSENTE")
        validar_contrato(contrato, contrato_dados, contrato_funil, raiz)

        if args.conferir:
            print("CONVERSAO_SEGMENTO_CONFERIR_OK contrato=%s eixos=%d funil=%s estagios=%d" % (
                contrato["versao"], len(contrato["eixos"]), contrato_funil["versao"],
                len(contrato.get("estagios_esperados") or [])))
            return 0
        if args.planejar:
            print("CONVERSAO_SEGMENTO_PLANO versao=%s unidade=%s amostra_minima=%d" % (
                contrato["versao"], contrato["unidade"]["contagem"], contrato["amostra_minima"]))
            for eixo in contrato["eixos"]:
                print("  %-20s fonte=%-20s regra=%s" % (eixo["nome"], eixo["fonte"], eixo["regra"]))
                print("      valores: %s" % ", ".join(eixo["valores"]))
            print("      sentinelas: SEM_DADO, FORA_DO_VOCABULARIO")
            return 0

        funil.validar_ambiente(args.ambiente, args.porta_banco, args.confirmo)
        desde = funil.normalizar_instante(args.desde, "--desde")
        ate = funil.normalizar_instante(args.ate, "--ate")
        relatorio = construir_relatorio(contrato, contrato_funil, contrato_dados, args.porta_banco,
                                        args.ambiente, desde, ate)
        relatorio["contrato"]["sha256_do_funil"] = funil.sha256_de_arquivo(caminho_contrato_funil(raiz))
        relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)

        token = os.environ.get("TRE_CONVERSAO_TOKEN")
        saida_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
        saida_html = emitir_html(relatorio)
        for texto in (saida_json, saida_html):
            if token and token in texto:
                raise Recusa("SENHA_VAZADA", "valor de TRE_CONVERSAO_TOKEN presente na evidencia",
                             codigo=CODIGO_SEGREDO)

        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "conversao-segmento.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "conversao-segmento.html"), "w", encoding="utf-8") as fh:
                    fh.write(saida_html)
        print(_resumo_texto(relatorio))
        return 0
    except funil.Recusa as exc:   # cobre a recusa daqui E a do funil reusado (classe-mae)
        print("RECUSA %s %s" % (exc.motivo, exc.detalhe))
        return exc.codigo


if __name__ == "__main__":
    sys.exit(main())
