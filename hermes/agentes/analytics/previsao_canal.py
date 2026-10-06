#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Previsao do melhor canal (`previsao-canal-v1`) — card TRE-W9-E03-T01 (W9 / Inteligencia Avancada).

O que este componente FAZ (e so' isto): MEDE a efetividade historica de cada canal declarado na
base canonica de `sales_intelligence` em LEITURA PURA e, quando — e so' quando — a PRE-CONDICAO de
dados multicanal e' atendida, PREVE o melhor canal por organizacao. Responde tres perguntas:

  1. A BASE SUSTENTA A PREVISAO? (pre-condicao "dados multicanal" do doc 11, que nao e' card)
     Quantos canais distintos tem base suficiente? Quantas organizacoes tem interacao registrada?
     Abaixo do minimo declarado o relatorio SAI, mas `previsao_emitida=false` e a lista `faltando`
     diz o que falta — o componente NAO preve' com base de brinquedo (fail-closed).
  2. EFETIVIDADE POR CANAL — por canal: organizacoes abordadas (OUTBOUND), interacoes, respostas
     INBOUND classificadas, taxa de resposta, taxa de avanco no endpoint principal (`Reunião`),
     lift contra a taxa-base, Won/Lost e `base_suficiente`. Canal sem base suficiente fica FORA
     do ranking elegivel (numero medido, previsao nao emitida por ele).
  3. MELHOR CANAL POR ORGANIZACAO — entre os canais elegiveis e NAO bloqueados, o de maior taxa
     de avanco, com desempate DECLARADO (resposta propria da organizacao -> interacao propria ->
     `preferred_channel` -> ordem do vocabulario) e a amostra do canal viajando junto.

Invariantes (cada um com item de suite/aceite):

  1. LEITURA PURA, NAO E' PREFERENCIA: toda consulta roda com `default_transaction_read_only = on`
     (o proprio PostgreSQL recusa escrita) e a auditoria da fonte reprova verbo de escrita ANTES de
     qualquer conexao (`ESCRITA_NO_CODIGO`, exit 3). Nao cria tabela, coluna, metrica nem linha.
  2. OPT-OUT E' BLOQUEIO, NAO PREFERENCIA (contrato §9): `do_not_contact` bloqueia TODOS os canais
     da organizacao; `opt_out_email`/`opt_out_whatsapp` bloqueiam o canal correspondente. Canal
     bloqueado nunca aparece como previsto — aparece como bloqueado, com o motivo. `preferred_channel`
     e' apenas DESEMPATE entre canais ja' elegiveis.
  3. NAO INVENTA CANAL: o vocabulario e' LIDO do contrato (`vocabulario_de_canal`); valor gravado
     fora da lista vai para lacuna nomeada e NAO e' mapeado por semelhanca.
  4. DESFECHO VEM DO PAI, NAO DE COPIA: o alcance por organizacao sai de `alcance_por_organizacao`
     do `funil.py` (card W8-E01-T01), importado. Reimplementar o funil daria duas verdades para o
     mesmo numero; o sha256 do contrato do pai viaja no relatorio.
  5. DETERMINISMO: a mesma base com o mesmo contrato e a mesma referencia temporal produz o MESMO
     relatorio; `gerado_em` e `referencia_temporal` sao a unica diferenca e NAO entram no hash.
  6. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<...> psql`); prefixo remoto RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige
     `--confirmo`; `prod` RECUSA por desenho (exit 4).
  7. PRIVACIDADE: a saida carrega CONTAGEM e o UUID canonico da organizacao. O componente nao le
     (nem seleciona) e-mail, telefone, WhatsApp, CNPJ ou nome — de `contacts` so' entram contagens
     de bloqueio e o canal preferido.

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
from datetime import datetime, timezone

VERSAO = "previsao-canal-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "previsao-canal-v1.json")
FUNIL_PADRAO = os.path.join(AQUI, "funil.py")
CONTRATO_FUNIL_PADRAO = os.path.join(AQUI, "funil-v1.json")
VERSAO_FUNIL_EXIGIDA = "funil-v1"
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5
BLOQUEIOS_VALIDOS = ("opt_out_email", "opt_out_whatsapp", "do_not_contact")
FONTES_PROPRIAS = ("BLOQUEIOS_POR_ORGANIZACAO", "INTERACOES_POR_CANAL")
CAMPOS_DE_COMPLIANCE = ("do_not_contact", "opt_out_email", "opt_out_whatsapp", "preferred_channel")
CASTIGO_VALOR = 40


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
    spec = importlib.util.spec_from_file_location("funil_dependencia_canal", caminho_funil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONTRATO_PADRAO = caminho_contrato_funil
    contrato_funil = carregar_json(caminho_contrato_funil, "CONTRATO_DO_FUNIL_AUSENTE",
                                   "CONTRATO_DO_FUNIL_ILEGIVEL")
    if contrato_funil.get("versao") != VERSAO_FUNIL_EXIGIDA:
        raise Recusa("DEPENDENCIA_VERSAO_INCOMPATIVEL",
                     "esperado %s, obtido %s" % (VERSAO_FUNIL_EXIGIDA, contrato_funil.get("versao")))
    for exigido in ("alcance_por_organizacao", "montar_consultas", "extrair_base",
                    "resolver_evidencia", "executar_consulta", "auditar_fonte", "validar_ambiente",
                    "normalizar_instante"):
        if not hasattr(mod, exigido):
            raise Recusa("DEPENDENCIA_INCOMPATIVEL", "funil.py sem `%s`" % exigido)
    return mod, contrato_funil


def validar_contrato(contrato, dados, contrato_funil):
    """Contrato do componente x Data Contract V1 x contrato do pai. Contrato incoerente RECUSA."""
    if contrato.get("versao") != VERSAO:
        raise Recusa("VERSAO_DO_CONTRATO_DESCONHECIDA", str(contrato.get("versao")))

    vocabulario = contrato.get("vocabulario_de_canal") or {}
    canais = [c.get("nome") for c in (vocabulario.get("canais") or [])]
    if not canais:
        raise Recusa("CONTRATO_SEM_VOCABULARIO_DE_CANAL",
                     "o Data Contract nao congela `interactions.channel`: o vocabulario tem de ser DECLARADO aqui")
    if any(not n for n in canais) or len(set(canais)) != len(canais):
        raise Recusa("CANAL_SEM_NOME_OU_DUPLICADO", str(canais))
    bloqueio_por_canal = {}
    for canal in (vocabulario.get("canais") or []):
        if canal.get("bloqueio") not in BLOQUEIOS_VALIDOS:
            raise Recusa("BLOQUEIO_DE_CANAL_DESCONHECIDO",
                         "%s -> %r" % (canal.get("nome"), canal.get("bloqueio")))
        bloqueio_por_canal[canal["nome"]] = canal["bloqueio"]

    exigidos = list((dados.get("compliance") or {}).get("required_fields") or [])
    for campo in CAMPOS_DE_COMPLIANCE:
        if campo not in exigidos:
            raise Recusa("CONTRATO_DE_DADOS_SEM_CAMPO_DE_COMPLIANCE",
                         "%s ausente de compliance.required_fields" % campo)

    parametros = contrato.get("parametros") or {}
    minimos = {}
    for nome in ("minimo_organizacoes_por_canal", "minimo_canais_com_base",
                 "minimo_organizacoes_com_evidencia"):
        valor = parametros.get(nome)
        if not isinstance(valor, int) or isinstance(valor, bool) or valor < 1:
            raise Recusa("MINIMO_INVALIDO", "%s=%r" % (nome, valor))
        minimos[nome] = valor
    if minimos["minimo_canais_com_base"] > len(canais):
        raise Recusa("MINIMOS_INCOERENTES",
                     "%d canais exigidos com %d declarados" % (minimos["minimo_canais_com_base"], len(canais)))

    estagios = {e["nome"]: e for e in (contrato_funil.get("estagios") or [])}
    if not estagios:
        raise Recusa("CONTRATO_DO_FUNIL_SEM_ESTAGIOS")
    endpoint = parametros.get("endpoint_principal") or {}
    if endpoint.get("nome") not in estagios:
        raise Recusa("ENDPOINT_FORA_DOS_ESTAGIOS_DO_PAI", str(endpoint.get("nome")))
    if estagios[endpoint["nome"]].get("nivel") != endpoint.get("nivel_minimo"):
        raise Recusa("NIVEL_DO_ENDPOINT_DIVERGENTE",
                     "%s: pai=%s contrato=%s" % (endpoint.get("nome"),
                                                 estagios[endpoint["nome"]].get("nivel"),
                                                 endpoint.get("nivel_minimo")))
    for ep in (parametros.get("endpoints") or []):
        if ep.get("rotulo"):
            if ep["rotulo"] not in estagios:
                raise Recusa("ENDPOINT_FORA_DOS_ESTAGIOS_DO_PAI", str(ep["rotulo"]))
        elif ep.get("nome") not in estagios or estagios[ep["nome"]].get("nivel") != ep.get("nivel_minimo"):
            raise Recusa("ENDPOINT_FORA_DOS_ESTAGIOS_DO_PAI", str(ep))

    vigentes = sorted((contrato_funil.get("fontes") or {}).keys())
    declaradas = sorted(contrato.get("fontes_do_funil") or [])
    if declaradas != vigentes:
        raise Recusa("FONTES_DO_FUNIL_DIVERGEM",
                     "declaradas=%s vigentes=%s" % (declaradas, vigentes))
    if sorted(contrato.get("fontes_proprias") or {}) != sorted(FONTES_PROPRIAS):
        raise Recusa("FONTES_PROPRIAS_DIVERGEM_DO_CONTRATO",
                     "contrato=%s esperado=%s" % (sorted(contrato.get("fontes_proprias") or {}),
                                                  sorted(FONTES_PROPRIAS)))
    return {"canais": canais, "bloqueio_por_canal": bloqueio_por_canal, "minimos": minimos,
            "endpoint": endpoint, "endpoints": list(parametros.get("endpoints") or [])}


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


def _janela(coluna, desde, ate):
    partes = ""
    if desde:
        partes += " AND %s >= '%s'" % (coluna, desde)
    if ate:
        partes += " AND %s <= '%s'" % (coluna, ate)
    return partes


def montar_consultas_proprias(contrato, desde=None, ate=None):
    """Fontes PROPRIAS deste componente (trilha de canal e bloqueios). O desfecho e' do funil."""
    declaradas = dict(contrato.get("fontes_proprias") or {})
    q = {}
    q["INTERACOES_POR_CANAL"] = (
        "SELECT organization_id::text || '|' || channel || '|' || direction || '|' || count(*)::text || '|' || "
        "count(*) FILTER (WHERE direction = 'INBOUND' AND response_category IS NOT NULL)::text || '|' || "
        "to_char(min(occurred_at), 'YYYY-MM-DD\"T\"HH24:MI:SSOF') || '|' || "
        "to_char(max(occurred_at), 'YYYY-MM-DD\"T\"HH24:MI:SSOF') "
        "FROM sales_intelligence.interactions WHERE occurred_at IS NOT NULL"
        + _janela("occurred_at", desde, ate)
        + " GROUP BY organization_id, channel, direction"
    )
    q["BLOQUEIOS_POR_ORGANIZACAO"] = (
        "SELECT organization_id::text || '|' || count(*)::text || '|' || "
        "count(*) FILTER (WHERE do_not_contact)::text || '|' || "
        "count(*) FILTER (WHERE opt_out_email)::text || '|' || "
        "count(*) FILTER (WHERE opt_out_whatsapp)::text || '|' || "
        "COALESCE(string_agg(DISTINCT preferred_channel, ','), '') "
        "FROM sales_intelligence.contacts WHERE organization_id IS NOT NULL"
        + _janela("created_at", desde, ate)
        + " GROUP BY organization_id"
    )
    if sorted(q.keys()) != sorted(declaradas.keys()):
        raise Recusa("FONTES_PROPRIAS_DIVERGEM_DO_CONTRATO",
                     "componente=%s contrato=%s" % (sorted(q.keys()), sorted(declaradas.keys())))
    return q


# --------------------------------------------------------------------------------------------
# 3. Leitura (pura) e derivacao
# --------------------------------------------------------------------------------------------
def _inteiro(texto, contexto):
    try:
        return int(str(texto).strip())
    except Exception:  # noqa: BLE001 — valor ilegivel recusa, nunca vira zero
        raise Recusa("VALOR_ILEGIVEL", "%s=%r" % (contexto, texto))


def _instante(texto):
    if not texto:
        return None
    texto = texto.strip().replace(" ", "T")
    if re.match(r"^.*[+-]\d{2}$", texto):  # psql OF: +00
        texto += ":00"
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def _mascarar(texto, tamanho=CASTIGO_VALOR):
    texto = re.sub(r"[^A-Za-z0-9_.@-]", "", str(texto or ""))
    return (texto[:tamanho] + ("..." if len(texto) > tamanho else "")) or "?"


def extrair_interacoes(linhas):
    """Linhas: org|canal|direcao|total|respondidas|primeira|ultima -> por (org, canal)."""
    por_chave = {}
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) < 7 or not campos[0]:
            continue
        org, canal, direcao = campos[0], campos[1].strip(), campos[2].strip().upper()
        total = _inteiro(campos[3], "interactions.total")
        respondidas = _inteiro(campos[4], "interactions.respondidas")
        item = por_chave.setdefault((org, canal), {
            "outbound": 0, "inbound": 0, "respondidas": 0, "outra_direcao": 0,
            "primeira": campos[5].strip(), "ultima": campos[6].strip(),
        })
        if direcao == "OUTBOUND":
            item["outbound"] += total
        elif direcao == "INBOUND":
            item["inbound"] += total
            item["respondidas"] += respondidas
        else:
            item["outra_direcao"] += total
        item["primeira"] = min(item["primeira"] or campos[5].strip(), campos[5].strip())
        item["ultima"] = max(item["ultima"] or campos[6].strip(), campos[6].strip())
    return por_chave


def extrair_bloqueios(linhas):
    """Linhas: org|contatos|do_not_contact|opt_out_email|opt_out_whatsapp|canais_preferidos."""
    por_org = {}
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) < 6 or not campos[0]:
            continue
        org = campos[0]
        preferidos = [p.strip() for p in campos[5].split(",") if p.strip()]
        por_org[org] = {
            "contatos": _inteiro(campos[1], "contacts.contatos"),
            "do_not_contact": _inteiro(campos[2], "contacts.do_not_contact"),
            "opt_out_email": _inteiro(campos[3], "contacts.opt_out_email"),
            "opt_out_whatsapp": _inteiro(campos[4], "contacts.opt_out_whatsapp"),
            "canais_preferidos": sorted(set(preferidos)),
        }
    return por_org


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
    canais = validado["canais"]
    bloqueio_por_canal = validado["bloqueio_por_canal"]
    minimos = validado["minimos"]
    endpoint = validado["endpoint"]
    endpoints = validado["endpoints"]

    organizacoes = funil.extrair_base(brutas_funil.get("BASE_ORGANIZACOES", []))
    evidencia, _lacunas_funil = funil.resolver_evidencia(contrato_funil, brutas_funil, organizacoes)
    alcance = funil.alcance_por_organizacao(contrato_funil, organizacoes, evidencia)
    interacoes = extrair_interacoes(brutas_proprias.get("INTERACOES_POR_CANAL", []))
    bloqueios = extrair_bloqueios(brutas_proprias.get("BLOQUEIOS_POR_ORGANIZACAO", []))

    lacunas = {
        "interacao_de_organizacao_desconhecida": 0, "canal_fora_do_vocabulario": {},
        "organizacao_sem_interacao": 0, "organizacao_sem_contato": 0, "organizacao_sem_canal_elegivel": 0,
        "canal_bloqueado_por_do_not_contact": 0, "canal_bloqueado_por_opt_out": {},
        "inbound_sem_classificacao": 0, "direcao_fora_do_vocabulario": 0,
        "canal_sem_base_suficiente": [], "previsao_nao_emitida": 0, "evidencia_orfa_de_bloqueio": 0,
    }

    def atingiu(org, ep):
        info = alcance.get(org) or {}
        if ep.get("rotulo"):
            return ep["rotulo"] in (info.get("rotulos") or [])
        nivel = info.get("nivel")
        return nivel is not None and nivel >= int(ep["nivel_minimo"])

    # --- inventario da trilha ----------------------------------------------------------------
    orgs_com_interacao = set()
    for (org, canal), item in interacoes.items():
        if org not in organizacoes:
            lacunas["interacao_de_organizacao_desconhecida"] += 1
            continue
        orgs_com_interacao.add(org)
        if canal not in canais:
            forma = _mascarar(canal)
            lacunas["canal_fora_do_vocabulario"][forma] = lacunas["canal_fora_do_vocabulario"].get(forma, 0) + 1
        if item["outra_direcao"]:
            lacunas["direcao_fora_do_vocabulario"] += item["outra_direcao"]
        if item["inbound"] > item["respondidas"]:
            lacunas["inbound_sem_classificacao"] += item["inbound"] - item["respondidas"]
    orgs_com_interacao = sorted(orgs_com_interacao)
    for org in organizacoes:
        if org not in orgs_com_interacao:
            lacunas["organizacao_sem_interacao"] += 1
    for org in bloqueios:
        if org not in organizacoes:
            lacunas["evidencia_orfa_de_bloqueio"] += 1

    # --- taxa-base (uniao dos canais: quem foi abordado por OUTBOUND em algum canal) ----------
    abordadas_total = sorted({org for (org, canal), item in interacoes.items()
                              if canal in canais and org in organizacoes and item["outbound"] > 0})
    atingiram_base = sum(1 for org in abordadas_total if atingiu(org, endpoint))
    taxa_base = _pct(atingiram_base, len(abordadas_total))

    def bloco(ep, orgs):
        atingiram = sum(1 for o in orgs if atingiu(o, ep))
        return {"nome": ep.get("nome") or ep.get("rotulo"), "atingiram": atingiram,
                "taxa_pct": _pct(atingiram, len(orgs))}

    base_endpoints = {ep.get("nome") or ep.get("rotulo"): _pct(sum(1 for o in abordadas_total if atingiu(o, ep)),
                                                              len(abordadas_total))
                      for ep in endpoints}

    # --- efetividade por canal ---------------------------------------------------------------
    por_canal = []
    for canal in canais:
        orgs = sorted(org for (org, c), item in interacoes.items()
                      if c == canal and org in organizacoes and item["outbound"] > 0)
        outbound = sum(item["outbound"] for (org, c), item in interacoes.items()
                       if c == canal and org in organizacoes)
        respondidas = sum(item["respondidas"] for (org, c), item in interacoes.items()
                          if c == canal and org in organizacoes)
        orgs_com_resposta = sum(1 for org in orgs
                                if (interacoes.get((org, canal)) or {}).get("respondidas", 0) > 0)
        ganharam = sum(1 for o in orgs if "Won" in ((alcance.get(o) or {}).get("rotulos") or []))
        perderam = sum(1 for o in orgs if "Lost" in ((alcance.get(o) or {}).get("rotulos") or []))
        blocos = []
        for ep in endpoints:
            b = bloco(ep, orgs)
            b["lift"] = _lift(b["taxa_pct"], base_endpoints[ep.get("nome") or ep.get("rotulo")])
            blocos.append(b)
        principal = next((b for b in blocos if b["nome"] == endpoint["nome"]), None)
        base_suficiente = len(orgs) >= minimos["minimo_organizacoes_por_canal"]
        if not base_suficiente:
            lacunas["canal_sem_base_suficiente"].append(canal)
        por_canal.append({
            "canal": canal,
            "bloqueio": bloqueio_por_canal[canal],
            "organizacoes_abordadas": len(orgs),
            "interacoes_outbound": outbound,
            "respostas_inbound": respondidas,
            "organizacoes_com_resposta": orgs_com_resposta,
            "taxa_de_resposta_pct": _pct(respondidas, outbound),
            "endpoints": blocos,
            "avanco_pct": (principal or {}).get("taxa_pct"),
            "lift_avanco": _lift((principal or {}).get("taxa_pct"), taxa_base),
            "ganharam": ganharam,
            "perderam": perderam,
            "em_aberto": len(orgs) - ganharam - perderam,
            "base_suficiente": base_suficiente,
            "elegivel_para_previsao": base_suficiente,
        })

    elegiveis = [c for c in por_canal if c["elegivel_para_previsao"]]
    ranking = sorted(elegiveis, key=lambda c: (-(c["avanco_pct"] or 0.0), c["canal"]))

    # --- pre-condicao "dados multicanal" -----------------------------------------------------
    faltando = []
    if len(elegiveis) < minimos["minimo_canais_com_base"]:
        faltando.append("canais com base suficiente >= %d (obtido %d)"
                        % (minimos["minimo_canais_com_base"], len(elegiveis)))
    if len(orgs_com_interacao) < minimos["minimo_organizacoes_com_evidencia"]:
        faltando.append("organizacoes com interacao >= %d (obtido %d)"
                        % (minimos["minimo_organizacoes_com_evidencia"], len(orgs_com_interacao)))
    atendida = not faltando
    pre_condicao = {
        "atendida": atendida,
        "canais_com_base": len([c for c in por_canal if c["organizacoes_abordadas"] > 0]),
        "canais_suficientes": len(elegiveis),
        "organizacoes_com_interacao": len(orgs_com_interacao),
        "exigidos": {"minimo_canais_com_base": minimos["minimo_canais_com_base"],
                     "minimo_organizacoes_por_canal": minimos["minimo_organizacoes_por_canal"],
                     "minimo_organizacoes_com_evidencia": minimos["minimo_organizacoes_com_evidencia"]},
        "faltando": faltando,
    }
    if not atendida:
        lacunas["previsao_nao_emitida"] += 1

    # --- previsao por organizacao ------------------------------------------------------------
    previsoes = []
    if atendida:
        for org in orgs_com_interacao:
            info = bloqueios.get(org)
            if info is None:
                lacunas["organizacao_sem_contato"] += 1
            bloqueados = []
            dnc = bool(info and info["do_not_contact"] > 0)
            for canal in canais:
                motivo = None
                if dnc:
                    motivo = "do_not_contact"
                    lacunas["canal_bloqueado_por_do_not_contact"] += 1
                elif info and bloqueio_por_canal[canal] == "opt_out_email" and info["opt_out_email"] > 0:
                    motivo = "opt_out_email"
                elif info and bloqueio_por_canal[canal] == "opt_out_whatsapp" and info["opt_out_whatsapp"] > 0:
                    motivo = "opt_out_whatsapp"
                if motivo:
                    bloqueados.append({"canal": canal, "motivo": motivo})
                    if motivo != "do_not_contact":
                        lacunas["canal_bloqueado_por_opt_out"][motivo] = \
                            lacunas["canal_bloqueado_por_opt_out"].get(motivo, 0) + 1
            set_bloqueados = {b["canal"] for b in bloqueados}
            candidatos = [c for c in elegiveis if c["canal"] not in set_bloqueados]
            if not candidatos:
                lacunas["organizacao_sem_canal_elegivel"] += 1
                continue

            def marca(canal):
                item = interacoes.get((org, canal)) or {}
                return {
                    "resposta_propria": 1 if item.get("respondidas", 0) > 0 else 0,
                    "interacao_propria": 1 if (item.get("outbound", 0) + item.get("inbound", 0)) > 0 else 0,
                    "preferido": 1 if (info and canal in info["canais_preferidos"]) else 0,
                }

            def chave(c):
                m = marca(c["canal"])
                return (-(c["avanco_pct"] or 0.0), -m["resposta_propria"], -m["interacao_propria"],
                        -m["preferido"], canais.index(c["canal"]))

            candidatos.sort(key=chave)
            escolhido = candidatos[0]
            desempate = "taxa_de_avanco"  # candidato unico, ou o de maior taxa
            if len(candidatos) > 1:
                k1, k2 = chave(escolhido), chave(candidatos[1])
                if k1[0] == k2[0]:  # empate REAL de taxa: o desempate declarado decide
                    for indice, nome in ((1, "resposta_propria"), (2, "interacao_propria"),
                                         (3, "preferencia_declarada"), (4, "ordem_do_vocabulario")):
                        if k1[indice] != k2[indice]:
                            desempate = nome
                            break
            m = marca(escolhido["canal"])
            previsoes.append({
                "organization_id": org,
                "canal_previsto": escolhido["canal"],
                "avanco_pct": escolhido["avanco_pct"],
                "lift_avanco": escolhido["lift_avanco"],
                "amostra_do_canal": escolhido["organizacoes_abordadas"],
                "taxa_de_resposta_pct": escolhido["taxa_de_resposta_pct"],
                "resposta_propria": bool(m["resposta_propria"]),
                "canal_com_interacao_propria": bool(m["interacao_propria"]),
                "preferido": bool(m["preferido"]),
                "empate_desfeito_por": desempate,
                "canais_considerados": [c["canal"] for c in candidatos],
                "canais_bloqueados": bloqueados,
            })

    contagem = {}
    for p in previsoes:
        contagem[p["canal_previsto"]] = contagem.get(p["canal_previsto"], 0) + 1
    resumo = {
        "organizacoes_com_interacao": len(orgs_com_interacao),
        "organizacoes_abordadas": len(abordadas_total),
        "organizacoes_com_previsao": len(previsoes),
        "previsao_emitida": bool(atendida and previsoes),
        "taxa_base_avanco_pct": taxa_base,
        "endpoint_principal": endpoint["nome"],
        "canal_mais_efetivo": (ranking[0]["canal"] if ranking else None),
        "avanco_do_canal_mais_efetivo_pct": (ranking[0]["avanco_pct"] if ranking else None),
        "canais_com_base_suficiente": [c["canal"] for c in por_canal if c["base_suficiente"]],
        "canais_sem_base_suficiente": [c["canal"] for c in por_canal if not c["base_suficiente"]],
        "canal_previsto_mais_frequente": (sorted(contagem.items(), key=lambda par: (-par[1], par[0]))[0][0]
                                          if contagem else None),
        "distribuicao_das_previsoes": {k: contagem[k] for k in sorted(contagem)},
    }

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
        "base": {"organizacoes": len(organizacoes), "digest": _digest({"organizacoes": sorted(organizacoes)})},
        "parametros": {"endpoint_principal": endpoint["nome"],
                       "endpoints": [ep.get("nome") or ep.get("rotulo") for ep in endpoints],
                       "vocabulario_de_canal": canais,
                       "bloqueio_por_canal": bloqueio_por_canal,
                       "minimos": minimos},
        "pre_condicao_dados_multicanal": pre_condicao,
        "por_canal": por_canal,
        "ranking_de_canais": [c["canal"] for c in ranking],
        "previsoes": previsoes,
        "resumo": resumo,
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
    def tabela(cabecalho, linhas):
        th = "".join("<th>%s</th>" % _html.escape(c) for c in cabecalho)
        tr = "".join("<tr>%s</tr>" % "".join(
            '<td class="n">%s</td>' % _html.escape(str(c)) if i else "<td>%s</td>" % _html.escape(str(c))
            for i, c in enumerate(linha)) for linha in linhas)
        return '<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (th, tr)

    pre = relatorio["pre_condicao_dados_multicanal"]
    resumo = relatorio["resumo"]
    canais = [[c["canal"], c["bloqueio"], c["organizacoes_abordadas"], c["interacoes_outbound"],
               c["respostas_inbound"],
               "-" if c["taxa_de_resposta_pct"] is None else "%.2f%%" % c["taxa_de_resposta_pct"],
               "-" if c["avanco_pct"] is None else "%.2f%%" % c["avanco_pct"],
               "-" if c["lift_avanco"] is None else "%.2fx" % c["lift_avanco"],
               c["ganharam"], c["perderam"], "sim" if c["base_suficiente"] else "nao"]
              for c in relatorio["por_canal"]]
    previsoes = [[p["organization_id"][:8], p["canal_previsto"],
                  "-" if p["avanco_pct"] is None else "%.2f%%" % p["avanco_pct"], p["amostra_do_canal"],
                  "sim" if p["resposta_propria"] else "nao", "sim" if p["preferido"] else "nao",
                  p["empate_desfeito_por"],
                  ", ".join(b["canal"] + "(" + b["motivo"] + ")" for b in p["canais_bloqueados"]) or "-"]
                 for p in relatorio["previsoes"]]
    lacunas = [[k, json.dumps(v, ensure_ascii=False)] for k, v in sorted(relatorio["lacunas"].items())
               if v not in (0, {}, [], None)]
    return (
        "<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Previsao do melhor canal — TRE-W9-E03-T01</title>\n<style>\n"
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
        "<h1>Previsao do melhor canal — Transformativa Revenue Engine</h1>\n"
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
                  for k, v in [("pre-condicao", "atendida" if pre["atendida"] else "NAO atendida"),
                               ("canais suficientes", pre["canais_suficientes"]),
                               ("organizacoes com interacao", pre["organizacoes_com_interacao"]),
                               ("previsoes", resumo["organizacoes_com_previsao"]),
                               ("canal mais efetivo", resumo["canal_mais_efetivo"] or "-"),
                               ("taxa-base de avanco",
                                "-" if resumo["taxa_base_avanco_pct"] is None
                                else "%.2f%%" % resumo["taxa_base_avanco_pct"])])
        + "</div>\n<h2>Pre-condicao \"dados multicanal\"</h2>\n<p class=\"lacunas\">%s</p>\n"
          % ("atendida" if pre["atendida"] else "NAO atendida — faltando: " + "; ".join(pre["faltando"]))
        + "<h2>Efetividade por canal (endpoint: %s)</h2>\n" % _html.escape(str(resumo["endpoint_principal"]))
        + tabela(["Canal", "Bloqueio", "Abordadas", "Outbound", "Respostas", "Taxa de resposta",
                  "Avanco", "Lift", "Won", "Lost", "Base suficiente"], canais)
        + "<h2>Ranking de canais elegiveis</h2>\n<p>%s</p>\n"
          % _html.escape(", ".join(relatorio["ranking_de_canais"]) or "nenhum canal elegivel")
        + "<h2>Previsao por organizacao</h2>\n"
        + (tabela(["Organizacao", "Canal", "Avanco", "Amostra", "Resposta propria", "Preferido",
                   "Desempate", "Bloqueados"], previsoes)
           if previsoes else "<p class=\"lacunas\">Nenhuma previsao emitida (pre-condicao ou bloqueio).</p>\n")
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
    pre = relatorio["pre_condicao_dados_multicanal"]
    resumo = relatorio["resumo"]
    return " ".join([
        "PREVISAO_CANAL_OK",
        "pre_condicao=%s" % ("atendida" if pre["atendida"] else "NAO_atendida"),
        "canais_suficientes=%d" % pre["canais_suficientes"],
        "orgs_com_interacao=%d" % pre["organizacoes_com_interacao"],
        "previsoes=%d" % resumo["organizacoes_com_previsao"],
        "canal_mais_efetivo=%s" % (resumo["canal_mais_efetivo"] or "-"),
        "taxa_base=%s" % resumo["taxa_base_avanco_pct"],
        "hash=%s" % relatorio["hash_do_relatorio"][:16],
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Previsao do melhor canal v1 — TRE-W9-E03-T01")
    parser.add_argument("--ambiente", required=True, choices=list(AMBIENTES))
    parser.add_argument("--porta-banco", default=os.environ.get("TRE_CANAL_PORTA_BANCO"))
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
        validado = validar_contrato(contrato, dados, contrato_funil)

        if args.conferir:
            print("PREVISAO_CANAL_CONFERIR_OK contrato=%s canais=%d minimos=%s funil=%s" % (
                contrato["versao"], len(validado["canais"]),
                json.dumps(validado["minimos"], ensure_ascii=False), contrato_funil.get("versao")))
            return 0
        if args.planejar:
            print("PREVISAO_CANAL_PLANO versao=%s unidade=organization" % contrato["versao"])
            print("  vocabulario de canal (declarado no contrato): %s" % ", ".join(
                "%s[bloqueio=%s]" % (c["nome"], c["bloqueio"])
                for c in contrato["vocabulario_de_canal"]["canais"]))
            print("  minimos da pre-condicao: %s" % json.dumps(validado["minimos"], ensure_ascii=False))
            print("  endpoint principal: %s (nivel %s)" % (validado["endpoint"]["nome"],
                                                           validado["endpoint"]["nivel_minimo"]))
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
        for token_nome in ("TRE_CANAL_TOKEN", "TRE_PREVISAO_CANAL_TOKEN", "TRE_FUNIL_TOKEN"):
            token = os.environ.get(token_nome)
            if token and (token in saida_json or token in saida_html):
                raise Recusa("SENHA_VAZADA", "valor de %s presente na evidencia" % token_nome,
                             codigo=CODIGO_SEGREDO)

        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "previsao-canal.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "previsao-canal.html"), "w", encoding="utf-8") as fh:
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
