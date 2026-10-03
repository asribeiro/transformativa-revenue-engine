#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Funil — dashboard v1 (`funil-v1`) — card TRE-W8-E01-T01 (W8 / Analytics).

O que este componente FAZ (e so' isto): DERIVA o funil do doc 03 §2 na ordem congelada do
Data Contract V1 (`funnel_stages`, contrato §7.1) a partir da base canonica de `sales_intelligence`
(organizations, research_runs, signals, scores, contacts, interactions, recommendations) e da TRILHA
dos eventos Odoo -> PostgreSQL (`sync_events`), conta por ORGANIZACAO (UUID canonico), aplica alcance
cumulativo e conversoes, e desenha o resultado em JSON e num HTML auto-contido — o dashboard.
Leitura pura: SO' `SELECT`, dentro de transacao READ ONLY.

Invariantes deste componente (cada um com item de suite/aceite):

  1. LEITURA PURA, NAO E' PREFERENCIA: toda consulta roda com `default_transaction_read_only = on`
     (o proprio PostgreSQL recusa escrita) e a auditoria da fonte reprova verbo de escrita ANTES de
     qualquer conexao (`ESCRITA_NO_CODIGO`, exit 3). O componente nao cria tabela, nao materializa
     estagio e nao move funil no CRM (o estagio e' do Odoo — contrato §2).
  2. ESTAGIO SEM EVIDENCIA NAO SE INVENTA: rotulo de estagio (de `organizations.status` ou do payload
     da trilha) e' normalizado e comparado por IGUALDADE EXATA contra os rotulos declarados; rotulo
     fora do vocabulario NAO vira estagio — vai para lacuna, e a organizacao vale so' pelos outros
     fatos dela. Casar prosa por semelhanca ja' foi reprovado tres vezes neste projeto (D04/D06/D07).
  3. ALCANCE E' CUMULATIVO: quem chegou a Reuniao passou por Abordagem iniciada. Won e Lost dividem o
     nivel terminal: perdido NAO conta em Won; no nivel terminal o alcance e' a evidencia PROPRIA.
     Nurture e' RAMO LATERAL (nada implica, nada e' implicado).
  4. EVIDENCIA SEM DONO NAO ENTRA: evento da trilha cujo `entity_id` (e, na falta dele, o
     `payload.organizacao_id`) nao casa organizacao conhecida fica em lacuna — nada e' atribuido por
     suposicao. E' o caso tipico de OPPORTUNITY_WON/LOST, que carregam o UUID da OPORTUNIDADE.
  5. DETERMINISMO: a mesma base com o mesmo contrato produz o MESMO relatorio; `gerado_em` e' a unica
     diferenca e NAO entra no `hash_do_relatorio`.
  6. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<dev|aceite> psql`, via --porta-banco/TRE_FUNIL_PORTA_BANCO) — prefixo remoto
     RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige `--confirmo`; `prod` RECUSA por desenho (exit 4).
  7. SEGREDO: nenhum valor de segredo entra na linha de comando; se o valor de `TRE_FUNIL_TOKEN`
     aparecer na evidencia, a rodada e' recusada (`SENHA_VAZADA`, exit 5).
  8. PRIVACIDADE: a saida carrega CONTAGEM. O componente nao le (nem seleciona) e-mail, telefone,
     WhatsApp, CNPJ ou nome: o filtro de contato e' por PREENCHIMENTO do canal, nunca por valor.

Saida: relatorio JSON + HTML auto-contido. Exit code:
  0 = relatorio gerado (ou planilha/conferencia, sem banco) · 2 = uso errado · 3 = recusa
  (contrato/fonte/guarda/banco) · 4 = producao recusada · 5 = segredo vazado.
"""

import argparse
import hashlib
import html as _html
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone

VERSAO = "funil-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "funil-v1.json")
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5
CASTIGO_PII = 40

# Guarda de ambiente: em dev a porta de banco e' `docker exec -i <container local> psql ...`
# (mesmo padrao medido nos cards irmaos W6-E05 / W7-E01/E06). Prefixo remoto (ssh ... psql) e' recusado.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(sales|funil|analytics|aceite)[A-Za-z0-9._-]*$")
VERBOS_DE_ESCRITA = re.compile(
    r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|COMMENT|CALL|DO)\b",
    re.IGNORECASE,
)
RE_SO_SELECT = re.compile(r"^SELECT\b", re.IGNORECASE)
RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?(Z|[+-]\d{2}:?\d{2})?$")


class Recusa(Exception):
    def __init__(self, motivo, detalhe="", codigo=3):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe
        self.codigo = codigo


# --------------------------------------------------------------------------------------------
# 0. Contrato
# --------------------------------------------------------------------------------------------
def carregar_contrato(caminho=None):
    caminho = caminho or CONTRATO_PADRAO
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise Recusa("CONTRATO_AUSENTE", caminho)
    except json.JSONDecodeError as exc:
        raise Recusa("CONTRATO_ILEGIVEL", str(exc))


def sha256_de_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def caminho_contrato_de_dados(dir_raiz):
    return os.path.join(dir_raiz, "docs", "data", "data_contract_v1.json")


def validar_contrato(contrato, contrato_dados):
    """Contrato do componente x contrato de dados: a lista e a ORDEM dos estagios e' congelada."""
    estagios = contrato.get("estagios") or []
    nomes = [e.get("nome") for e in estagios]
    if not nomes:
        raise Recusa("CONTRATO_SEM_ESTAGIOS")
    if len(set(nomes)) != len(nomes):
        raise Recusa("CONTRATO_ESTAGIO_DUPLICADO")
    congelados = list(contrato_dados.get("funnel_stages") or [])
    if nomes != congelados:
        raise Recusa(
            "ESTAGIOS_DIVERGEM_DO_CONTRATO_DE_DADOS",
            "componente=%s contrato=%s" % (nomes, congelados),
        )
    fontes = contrato.get("fontes") or {}
    for e in estagios:
        declared = e.get("fontes") or []
        if not declared:
            raise Recusa("ESTAGIO_SEM_FONTE_DECLARADA", e.get("nome"))
        for fid in declared:
            if fid not in fontes:
                raise Recusa("FONTE_NAO_DECLARADA", "%s -> %s" % (e.get("nome"), fid))
    lineares = [e for e in estagios if not e.get("lateral")]
    niveis = [e.get("nivel") for e in lineares]
    if any(n is None for n in niveis) or any(not isinstance(n, int) for n in niveis):
        raise Recusa("NIVEL_INVALIDO", str(niveis))
    unicos = sorted(set(niveis))
    if unicos != list(range(len(unicos))):
        raise Recusa("NIVEIS_NAO_CONSECUTIVOS", str(niveis))
    # Won e Lost dividem o nivel terminal (dois rotulos, um nivel); nenhum outro nivel se repete
    if niveis.count(max(unicos)) > 2 or any(niveis.count(n) > 1 for n in unicos if n != max(unicos)):
        raise Recusa("NIVEL_REPETIDO", str(niveis))
    if any(e.get("nivel") is not None for e in estagios if e.get("lateral")):
        raise Recusa("LATERAL_COM_NIVEL")
    if contrato.get("versao") != VERSAO:
        raise Recusa("VERSAO_DO_CONTRATO_DESCONHECIDA", str(contrato.get("versao")))
    return True


def rotulos_declarados(contrato):
    return {normalizar_rotulo(e["nome"]): e["nome"] for e in contrato["estagios"]}


def nivel_terminal(contrato):
    return max(e["nivel"] for e in contrato["estagios"] if not e.get("lateral"))


# --------------------------------------------------------------------------------------------
# 1. Normalizacao de rotulo (igualdade exata depois de normalizar — nunca semelhanca)
# --------------------------------------------------------------------------------------------
def normalizar_rotulo(valor):
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFD", str(valor).strip().lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto)


def resolver_rotulo(contrato, rotulo):
    """Devolve o nome canonico do estagio ou None (rotulo nao declarado / reservado)."""
    chave = normalizar_rotulo(rotulo)
    if not chave or chave == normalizar_rotulo("DISCOVERED"):
        return None
    return rotulos_declarados(contrato).get(chave)


# --------------------------------------------------------------------------------------------
# 2. Guardas
# --------------------------------------------------------------------------------------------
def validar_ambiente(ambiente, porta_banco=None, confirmo=False):
    if ambiente not in AMBIENTES:
        raise Recusa("AMBIENTE_INVALIDO", str(ambiente), codigo=2)
    if ambiente == "prod":
        raise Recusa(
            "PRODUCAO_RECUSADA",
            "nada nasce em producao (ADR-005); homologar e promover e' ato de operador com aprovacao registrada",
            codigo=4,
        )
    if ambiente == "dev":
        if not porta_banco:
            raise Recusa("PORTA_BANCO_AUSENTE", "dev exige --porta-banco/TRE_FUNIL_PORTA_BANCO")
        m = RE_CONTAINER_LOCAL.match(porta_banco.strip())
        if not m:
            raise Recusa("BANCO_NAO_E_DEV", "em dev a porta e' `docker exec -i pg-<...> psql ...`: %s" % porta_banco)
        if not CONTAINERS_LOCAIS_DEV.match(m.group(1)):
            raise Recusa("CONTAINER_NAO_LOCAL_DE_DEV", m.group(1))
    if ambiente == "homolog":
        if not porta_banco:
            raise Recusa("PORTA_BANCO_AUSENTE", "homolog exige --porta-banco")
        if not confirmo:
            raise Recusa("HOMOLOG_SEM_CONFIRMO", "leitura em homolog exige --confirmo explicito")
    return True


def auditar_sql(sql):
    """Auditoria da fonte: o SQL que vai rodar e' SO' SELECT. Devolve a lista de achados."""
    achados = []
    for parte in str(sql).split(";"):
        texto = parte.strip()
        if not texto or texto.lower().startswith("set "):
            continue
        casado = VERBOS_DE_ESCRITA.search(texto)
        if casado:
            achados.append(casado.group(0).upper())
        if not RE_SO_SELECT.match(texto):
            achados.append("NAO_SELECT")
    return achados


def auditar_fonte(consultas):
    problemas = {}
    for fid, sql in consultas.items():
        achados = auditar_sql(sql)
        if achados:
            problemas[fid] = achados
    if problemas:
        raise Recusa("ESCRITA_NO_CODIGO", json.dumps(problemas, ensure_ascii=False))
    return True


def mascarar(valor, tamanho=CASTIGO_PII):
    texto = str(valor or "")
    return texto[:tamanho] + ("…" if len(texto) > tamanho else "")


# --------------------------------------------------------------------------------------------
# 3. Consultas (montadas a partir do contrato; nunca SQL literal fora do contrato)
# --------------------------------------------------------------------------------------------
def _janela(coluna, desde, ate):
    partes = ""
    if desde:
        partes += " AND %s >= '%s'" % (coluna, desde)
    if ate:
        partes += " AND %s <= '%s'" % (coluna, ate)
    return partes


def normalizar_instante(valor, campo):
    if not valor:
        return None
    if not RE_ISO.match(valor.strip()):
        raise Recusa("JANELA_INVALIDA", "%s=%s" % (campo, valor), codigo=2)
    texto = valor.strip().replace(" ", "T")
    try:
        instante = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        raise Recusa("JANELA_INVALIDA", "%s=%s" % (campo, valor), codigo=2)
    return instante.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def montar_consultas(contrato, desde=None, ate=None):
    s = _janela("created_at", desde, ate)
    q = {}
    q["BASE_ORGANIZACOES"] = (
        "SELECT id::text || '|' || COALESCE(source, '') || '|' || COALESCE(status, '') "
        "FROM sales_intelligence.organizations WHERE deleted_at IS NULL" + s
    )
    q["PESQUISA_CONCLUIDA"] = (
        "SELECT DISTINCT organization_id::text FROM sales_intelligence.research_runs "
        "WHERE organization_id IS NOT NULL AND (status = 'COMPLETED' OR completed_at IS NOT NULL)"
        + _janela("created_at", desde, ate)
    )
    q["SINAIS"] = (
        "SELECT DISTINCT organization_id::text FROM sales_intelligence.signals "
        "WHERE organization_id IS NOT NULL" + _janela("detected_at", desde, ate)
    )
    q["SCORE_PRIORITY"] = (
        "SELECT DISTINCT organization_id::text FROM sales_intelligence.scores "
        "WHERE score_type = 'PRIORITY' AND score_version IS NOT NULL AND score_version <> ''"
        + _janela("calculated_at", desde, ate)
    )
    q["CONTATO_COM_CANAL"] = (
        "SELECT DISTINCT organization_id::text FROM sales_intelligence.contacts "
        "WHERE COALESCE(email, '') <> '' OR COALESCE(phone, '') <> '' OR COALESCE(whatsapp, '') <> ''"
        + _janela("created_at", desde, ate)
    )
    q["INTERACAO_OUTBOUND"] = (
        "SELECT DISTINCT organization_id::text FROM sales_intelligence.interactions "
        "WHERE direction = 'OUTBOUND'" + _janela("occurred_at", desde, ate)
    )
    q["INTERACAO_INBOUND_CLASSIFICADA"] = (
        "SELECT DISTINCT organization_id::text FROM sales_intelligence.interactions "
        "WHERE direction = 'INBOUND' AND response_category IS NOT NULL"
        + _janela("occurred_at", desde, ate)
    )
    q["RECOMENDACAO_REUNIAO"] = (
        "SELECT DISTINCT organization_id::text FROM sales_intelligence.recommendations "
        "WHERE action = 'CREATE_MEETING' AND status = 'EXECUTED'" + _janela("created_at", desde, ate)
    )
    q["TRILHA_ESTAGIO"] = (
        "SELECT COALESCE(NULLIF(entity_id::text, ''), COALESCE(request_payload -> 'payload' ->> 'organizacao_id', '')) "
        "|| '|' || CASE operation WHEN 'OPPORTUNITY_WON' THEN 'Won' WHEN 'OPPORTUNITY_LOST' THEN 'Lost' "
        "WHEN 'MEETING_CREATED' THEN 'Reuniao' "
        "ELSE COALESCE(request_payload -> 'payload' ->> 'estagio_novo', '') END "
        "|| '|' || operation FROM sales_intelligence.sync_events "
        "WHERE source_system = 'odoo' AND status = 'COMPLETED' "
        "AND operation IN ('STAGE_CHANGED', 'MEETING_CREATED', 'OPPORTUNITY_WON', 'OPPORTUNITY_LOST')"
        + _janela("created_at", desde, ate)
    )
    declaradas = set((contrato.get("fontes") or {}).keys())
    montadas = set(q.keys())
    if declaradas != montadas:
        raise Recusa(
            "FONTES_DIVERGEM_DO_CONTRATO",
            "so' no contrato=%s so' no componente=%s" % (sorted(declaradas - montadas), sorted(montadas - declaradas)),
        )
    auditar_fonte(q)
    return q


# --------------------------------------------------------------------------------------------
# 4. Execucao (leitura pura)
# --------------------------------------------------------------------------------------------
def executar_consulta(porta_banco, sql, timeout=60):
    # Leitura pura em DOIS passos, de proposito: `SET` e `SELECT` em `-c` separados.
    # Medido: com `SET ...; SELECT ...` num unico `-c` o SET NAO vale (a transacao implicita ja'
    # comecou antes dele e o INSERT de prova PASSOU), enquanto com dois `-c` o PostgreSQL recusa
    # a escrita de verdade ("cannot execute INSERT in a read-only transaction").
    comando = shlex.split(porta_banco) + ["-A", "-t", "-F", "|", "-q", "-v", "ON_ERROR_STOP=1",
                                          "-c", "SET default_transaction_read_only = on",
                                          "-c", sql]
    try:
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise Recusa("PORTA_BANCO_INDISPONIVEL", str(exc))
    except subprocess.TimeoutExpired:
        raise Recusa("CONSULTA_EXCEDEU_TEMPO", sql[:80])
    if proc.returncode != 0 or "ERROR" in (proc.stderr or "").upper():
        raise Recusa("BANCO_RECUSOU_A_CONSULTA", (proc.stderr or "").strip()[:300])
    return [linha for linha in (proc.stdout or "").splitlines() if linha.strip() != ""]


def ler_fontes(porta_banco, consultas):
    return {fid: executar_consulta(porta_banco, sql) for fid, sql in consultas.items()}


# --------------------------------------------------------------------------------------------
# 5. Derivacao (puro — testavel sem banco)
# --------------------------------------------------------------------------------------------
def extrair_base(linhas_base):
    organizacoes = {}
    for linha in linhas_base:
        campos = linha.split("|")
        if len(campos) < 3 or not campos[0]:
            continue
        organizacoes[campos[0]] = {"source": campos[1], "status": campos[2]}
    return organizacoes


def _ids(linhas):
    return {linha.strip() for linha in linhas if linha.strip()}


def resolver_evidencia(contrato, fontes, organizacoes):
    """Cruza as fontes com o contrato e devolve (evidencia_por_estagio, lacunas).

    Tres caminhos, todos declarados no contrato:
      (a) fonte direta por organizacao (pesquisa, sinais, score, contato, interacao, recomendacao);
      (b) BASE_ORGANIZACOES — a propria linha viva da organizacao (estagio Descoberto);
      (c) por ROTULO — `organizations.status` e a trilha Odoo -> PostgreSQL, onde o rotulo tem de
          casar por igualdade exata um estagio declarado e o estagio tem de declarar aquela fonte.
    Rotulo nao declarado NAO vira estagio; evidencia sem organizacao conhecida NAO entra.
    """
    conhecidas = set(organizacoes.keys())
    evidencia = {e["nome"]: set() for e in contrato["estagios"]}
    lacunas = {"estagios_desconhecidos": 0, "status_nao_declarado": 0,
               "eventos_sem_atribuicao": {}, "rotulos_nao_declarados": [],
               "evidencia_orfa": 0, "estagio_fora_da_trilha_declarada": 0}

    # (a) e (b) — fontes diretas por organizacao
    for estagio in contrato["estagios"]:
        for fid in estagio["fontes"]:
            if fid == "TRILHA_ESTAGIO":
                continue
            if fid == "BASE_ORGANIZACOES":
                ids = conhecidas
            else:
                ids = _ids(fontes.get(fid, []))
            evidencia[estagio["nome"]] |= (ids & conhecidas)
            lacunas["evidencia_orfa"] += len(ids - conhecidas)

    # (c1) trilha por rotulo
    estagios_da_trilha = {e["nome"] for e in contrato["estagios"] if "TRILHA_ESTAGIO" in (e["fontes"] or [])}
    for linha in fontes.get("TRILHA_ESTAGIO", []):
        campos = linha.split("|")
        if len(campos) < 3:
            continue
        atribuicao, rotulo, operacao = campos[0], campos[1], campos[2]
        alvo = resolver_rotulo(contrato, rotulo)
        if alvo is None:
            lacunas["estagios_desconhecidos"] += 1
            if rotulo:
                forma = mascarar(normalizar_rotulo(rotulo))
                if forma not in lacunas["rotulos_nao_declarados"]:
                    lacunas["rotulos_nao_declarados"].append(forma)
            continue
        if alvo not in estagios_da_trilha:
            lacunas["estagio_fora_da_trilha_declarada"] += 1
            continue
        if atribuicao not in conhecidas:
            lacunas["eventos_sem_atribuicao"][operacao] = \
                lacunas["eventos_sem_atribuicao"].get(operacao, 0) + 1
            continue
        evidencia[alvo].add(atribuicao)

    # (c2) organizations.status por rotulo
    for org, info in organizacoes.items():
        rotulo = info.get("status") or ""
        alvo = resolver_rotulo(contrato, rotulo)
        if alvo:
            evidencia[alvo].add(org)
        elif normalizar_rotulo(rotulo) not in ("", normalizar_rotulo("DISCOVERED")):
            lacunas["status_nao_declarado"] += 1
            forma = mascarar(normalizar_rotulo(rotulo))
            if forma not in lacunas["rotulos_nao_declarados"]:
                lacunas["rotulos_nao_declarados"].append(forma)

    return evidencia, lacunas


def propria_por_estagio(contrato, organizacoes, evidencia):
    """Evidencia PROPRIA por estagio, restrita as organizacoes conhecidas."""
    conhecidas = set(organizacoes.keys())
    return {e["nome"]: set(evidencia.get(e["nome"], set())) & conhecidas for e in contrato["estagios"]}


def alcance_por_organizacao(contrato, organizacoes, evidencia):
    """Alcance por ORGANIZACAO — exposicao deliberada da regra que ja' vale no relatorio.

    `nivel` = maior nivel LINEAR alcancado por evidencia propria (None quando a organizacao so'
    tem evidencia lateral, hoje Nurture). `rotulos` = os rotulos com evidencia propria, ordenados.
    Quem DEPENDE deste funil (W8-E03, efetividade do score) le' o desfecho por organizacao daqui
    em vez de reimplementar a regra: duplicar o alcance criaria duas verdades para o mesmo numero.
    """
    propria = propria_por_estagio(contrato, organizacoes, evidencia)
    alcance = {}
    for org in sorted(organizacoes):
        niveis = [e["nivel"] for e in contrato["estagios"]
                  if not e.get("lateral") and e.get("nivel") is not None and org in propria[e["nome"]]]
        alcance[org] = {
            "nivel": max(niveis) if niveis else None,
            "rotulos": sorted(nome for nome, ids in propria.items() if org in ids),
        }
    return alcance


def calcular_funil(contrato, organizacoes, evidencia, lacunas):
    estagios = contrato["estagios"]
    terminal = nivel_terminal(contrato)
    conhecidas = set(organizacoes.keys())
    propria = propria_por_estagio(contrato, organizacoes, evidencia)
    nivel_max = {org: info["nivel"]
                 for org, info in alcance_por_organizacao(contrato, organizacoes, evidencia).items()}

    estagios_saida = []
    alcancadas = {}
    for indice, e in enumerate(estagios):
        nome = e["nome"]
        nivel = e.get("nivel")
        if e.get("lateral") or nivel is None:
            contagem = len(propria[nome])
        elif nivel >= terminal:
            contagem = len(propria[nome])
        else:
            contagem = sum(1 for org in conhecidas
                           if org in propria[nome]
                           or (nivel_max.get(org) is not None and nivel_max[org] >= nivel))
        alcancadas[nome] = contagem

    for indice, e in enumerate(estagios):
        nome = e["nome"]
        if e.get("lateral"):
            base = "Qualificado"
        else:
            base = estagios[indice - 1]["nome"] if indice > 0 else None
        anterior = alcancadas.get(base) if base else None
        topo = alcancadas.get(estagios[0]["nome"])
        estagios_saida.append({
            "nome": nome,
            "nivel": e.get("nivel"),
            "lateral": bool(e.get("lateral")),
            "dono": e.get("dono"),
            "alcancadas": alcancadas[nome],
            "evidencia_propria": len(propria[nome]),
            "conversao_de": base,
            "conversao_da_anterior_pct": _pct(alcancadas[nome], anterior),
            "conversao_do_topo_pct": _pct(alcancadas[nome], topo),
        })

    won = len(propria.get("Won", set()))
    lost = len(propria.get("Lost", set()))
    nurture = len(propria.get("Nurture", set()))
    total = len(conhecidas)
    relatorio = {
        "versao": VERSAO,
        "card": contrato.get("card"),
        "base": {"organizacoes": total,
                 "digest": _digest({"organizacoes": sorted(conhecidas)})},
        "estagios": estagios_saida,
        "resumo": {
            "total_organizacoes": total,
            "pesquisado": alcancadas.get("Pesquisado", 0),
            "qualificado": alcancadas.get("Qualificado", 0),
            "engajamento": alcancadas.get("Engajamento", 0),
            "reuniao": alcancadas.get("Reunião", 0),
            "negociacao": alcancadas.get("Negociação", 0),
            "won": won,
            "lost": lost,
            "nurture": nurture,
            "em_aberto": total - won - lost - nurture,
        },
        "fontes": {},
        "lacunas": lacunas,
    }
    _conferir_monotonicidade(relatorio)
    return relatorio


def _pct(numerador, denominador):
    if not denominador:
        return None
    return round(100.0 * numerador / denominador, 2)


def _digest(obj):
    canonico = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]


def _conferir_monotonicidade(relatorio):
    lineares = [e for e in relatorio["estagios"] if not e["lateral"]]
    for atual, seguinte in zip(lineares, lineares[1:]):
        if atual["nivel"] < seguinte["nivel"] and atual["alcancadas"] < seguinte["alcancadas"]:
            raise Recusa("FUNIL_NAO_MONOTONO", "%s=%d < %s=%d" % (
                atual["nome"], atual["alcancadas"], seguinte["nome"], seguinte["alcancadas"]))
    won = relatorio["resumo"]["won"]
    lost = relatorio["resumo"]["lost"]
    negociacao = relatorio["resumo"]["negociacao"]
    if negociacao < won + lost:
        raise Recusa("TERMINAL_NAO_CABE_EM_NEGOCIACAO", "%d < %d + %d" % (negociacao, won, lost))
    return True


def hash_do_relatorio(relatorio):
    limpo = {k: v for k, v in relatorio.items() if k not in ("gerado_em", "hash_do_relatorio")}
    canonico = json.dumps(limpo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------
# 6. Dashboard (HTML auto-contido: uma string, CSS inline, nenhum recurso externo)
# --------------------------------------------------------------------------------------------
def emitir_html(relatorio, lacunas_do_contrato=None):
    linhas = []
    maior = max([e["alcancadas"] for e in relatorio["estagios"]] + [1])
    for e in relatorio["estagios"]:
        largura = int(round(100.0 * e["alcancadas"] / maior))
        anterior = "—" if e["conversao_da_anterior_pct"] is None else "%.2f%%" % e["conversao_da_anterior_pct"]
        topo = "—" if e["conversao_do_topo_pct"] is None else "%.2f%%" % e["conversao_do_topo_pct"]
        classe = "lateral" if e["lateral"] else "linear"
        linhas.append(
            '<tr class="%s"><td>%s</td><td class="n">%d</td><td class="n">%d</td>'
            '<td class="n">%s</td><td class="n">%s</td>'
            '<td><div class="barra"><span style="width:%d%%"></span></div></td></tr>'
            % (classe, _html.escape(e["nome"]), e["alcancadas"], e["evidencia_propria"], anterior, topo, largura)
        )
    gaps = "".join("<li>%s</li>" % _html.escape(g) for g in (lacunas_do_contrato or []))
    resumo = relatorio["resumo"]
    return (
        "<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Funil — TRE-W8-E01-T01</title>\n<style>\n"
        "body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#111}\n"
        "h1{font-size:20px;margin:0 0 4px 0}\n.meta{color:#555;font-size:12px;margin-bottom:16px}\n"
        "table{border-collapse:collapse;width:100%%;font-size:13px}\n"
        "th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}\n"
        "td.n{text-align:right;font-variant-numeric:tabular-nums}\n"
        "tr.lateral td{color:#555;font-style:italic}\n"
        ".barra{background:#eef;height:12px;width:160px}\n.barra span{display:block;height:12px;background:#36c}\n"
        ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 18px 0}\n"
        ".card{border:1px solid #ddd;border-radius:6px;padding:8px 12px;min-width:110px}\n"
        ".card b{display:block;font-size:20px}\n.lacunas{font-size:12px;color:#555}\n"
        "</style>\n</head>\n<body>\n"
        "<h1>Funil comercial — Transformativa Revenue Engine</h1>\n"
        "<div class=\"meta\">card %s · contrato %s (sha256 %s) · ambiente %s · janela %s · gerado em %s · hash do relatorio %s</div>\n"
        % (_html.escape(str(relatorio.get("card"))), _html.escape(relatorio["contrato"]["versao"]),
           _html.escape(relatorio["contrato"]["sha256"][:16]), _html.escape(relatorio["ambiente"]),
           _html.escape(str(relatorio.get("janela"))), _html.escape(relatorio["gerado_em"]),
           _html.escape(relatorio["hash_do_relatorio"][:16]))
        + "<div class=\"cards\">"
        + "".join("<div class=\"card\"><b>%d</b>%s</div>" % (v, _html.escape(k))
                  for k, v in [("organizações", resumo["total_organizacoes"]), ("engajamento", resumo["engajamento"]),
                               ("reunião", resumo["reuniao"]), ("won", resumo["won"]), ("lost", resumo["lost"]),
                               ("nurture", resumo["nurture"])])
        + "</div>\n<table>\n<thead><tr><th>Estágio</th><th>Alcançadas</th><th>Evidência própria</th>"
          "<th>Conversão da anterior</th><th>Conversão do topo</th><th>Proporção</th></tr></thead>\n<tbody>\n"
        + "\n".join(linhas)
        + "\n</tbody>\n</table>\n<h2 style=\"font-size:15px\">Lacunas declaradas</h2>\n<ul class=\"lacunas\">\n"
        + gaps + "\n</ul>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------------------------
# 7. CLI
# --------------------------------------------------------------------------------------------
def _resumo_texto(relatorio):
    partes = ["FUNIL_OK"]
    for e in relatorio["estagios"]:
        partes.append("%s=%d(propria=%d)" % (e["nome"].replace(" ", "_"), e["alcancadas"], e["evidencia_propria"]))
    partes.append("hash=%s" % relatorio["hash_do_relatorio"][:16])
    return " ".join(partes)


def montar_relatorio(contrato, brutas, ambiente, janela, gerado_em=None,
                     sha_contrato=None, caminho_contrato=None):
    """Derivacao PURA (sem banco): recebe as linhas cruas de cada fonte e devolve o relatorio."""
    organizacoes = extrair_base(brutas.get("BASE_ORGANIZACOES", []))
    evidencia, lacunas = resolver_evidencia(contrato, brutas, organizacoes)
    relatorio = calcular_funil(contrato, organizacoes, evidencia, lacunas)
    relatorio["ambiente"] = ambiente
    relatorio["gerado_em"] = gerado_em or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    relatorio["janela"] = dict(janela or {"desde": None, "ate": None})
    relatorio["contrato"] = {
        "versao": contrato["versao"],
        "sha256": sha_contrato or sha256_de_arquivo(caminho_contrato or CONTRATO_PADRAO),
    }
    relatorio["fontes"] = {fid: len(linhas) for fid, linhas in sorted(brutas.items())}
    relatorio["lacunas_declaradas"] = contrato.get("lacunas_declaradas", [])
    relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
    return relatorio


def construir_relatorio(contrato, porta_banco, ambiente, desde, ate, gerado_em=None):
    if porta_banco is None:
        raise Recusa("PORTA_BANCO_AUSENTE", "sem banco nao ha funil: use --planejar/--conferir")
    consultas = montar_consultas(contrato, desde, ate)
    brutas = ler_fontes(porta_banco, consultas)
    return montar_relatorio(contrato, brutas, ambiente, {"desde": desde, "ate": ate},
                            gerado_em=gerado_em)


def exportar_por_organizacao(contrato, porta_banco, desde, ate, caminho):
    """Escreve o alcance por organizacao em JSON (exportacao ADITIVA, fora do relatorio/hash).

    Le a MESMA base pela MESMA derivacao do relatorio — nao ha' segunda verdade: o JSON sai de
    `alcance_por_organizacao`, a funcao que `calcular_funil` usa.
    """
    if porta_banco is None:
        raise Recusa("PORTA_BANCO_AUSENTE", "exportacao por organizacao exige --porta-banco")
    consultas = montar_consultas(contrato, desde, ate)
    brutas = ler_fontes(porta_banco, consultas)
    organizacoes = extrair_base(brutas.get("BASE_ORGANIZACOES", []))
    evidencia, _lacunas = resolver_evidencia(contrato, brutas, organizacoes)
    alcance = alcance_por_organizacao(contrato, organizacoes, evidencia)
    conteudo = {
        "versao": VERSAO,
        "card": contrato.get("card"),
        "janela": {"desde": desde, "ate": ate},
        "organizacoes": alcance,
        "hash_do_alcance": _digest(alcance),
    }
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(conteudo, ensure_ascii=False, indent=2, sort_keys=True))
    return conteudo


def main(argv=None):
    parser = argparse.ArgumentParser(description="Funil (dashboard) v1 — TRE-W8-E01-T01")
    parser.add_argument("--ambiente", required=True, choices=list(AMBIENTES))
    parser.add_argument("--porta-banco", default=os.environ.get("TRE_FUNIL_PORTA_BANCO"))
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--saida", default=None, help="diretorio de saida do relatorio")
    parser.add_argument("--formato", default="json,html", choices=["json", "html", "json,html"])
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--planejar", action="store_true", help="imprime o plano declarado (sem banco)")
    parser.add_argument("--por-organizacao", default=None,
                        help="escreve o alcance por organizacao em JSON (insumo de quem depende deste funil)")
    parser.add_argument("--conferir", action="store_true", help="valida contrato e guardas (sem banco)")
    parser.add_argument("--contrato", default=CONTRATO_PADRAO)
    parser.add_argument("--raiz", default=os.path.join(AQUI, "..", "..", ".."))
    args = parser.parse_args(argv)

    try:
        raiz = os.path.abspath(args.raiz)
        # Guarda de ambiente ANTES de qualquer leitura: producao e' recusa por desenho (exit 4),
        # independentemente do contrato, da base ou do formato de saida.
        if args.ambiente == "prod":
            raise Recusa(
                "PRODUCAO_RECUSADA",
                "nada nasce em producao (ADR-005); ler/promover em producao e' ato de operador com aprovacao registrada",
                codigo=4,
            )
        contrato = carregar_contrato(args.contrato)
        with open(caminho_contrato_de_dados(raiz), "r", encoding="utf-8") as fh:
            contrato_dados = json.load(fh)
        validar_contrato(contrato, contrato_dados)

        if args.conferir:
            print("FUNIL_CONFERIR_OK contrato=%s estagios=%d fontes=%d" % (
                contrato["versao"], len(contrato["estagios"]), len(contrato["fontes"])))
            return 0
        if args.planejar:
            print("FUNIL_PLANO versao=%s unidade=%s" % (contrato["versao"], contrato["unidade"]["contagem"]))
            for e in contrato["estagios"]:
                print("  %-22s nivel=%s lateral=%s dono=%-11s fontes=%s" % (
                    e["nome"], e["nivel"], e["lateral"], e["dono"], ",".join(e["fontes"])))
            print("  guardas: %s" % json.dumps(contrato["guardas"], ensure_ascii=False)[:160])
            return 0

        validar_ambiente(args.ambiente, args.porta_banco, args.confirmo)
        desde = normalizar_instante(args.desde, "--desde")
        ate = normalizar_instante(args.ate, "--ate")
        relatorio = construir_relatorio(contrato, args.porta_banco, args.ambiente, desde, ate)

        token = os.environ.get("TRE_FUNIL_TOKEN")
        saida_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
        saida_html = emitir_html(relatorio, contrato.get("lacunas_declaradas"))
        for texto in (saida_json, saida_html):
            if token and token in texto:
                raise Recusa("SENHA_VAZADA", "valor de TRE_FUNIL_TOKEN presente na evidencia", codigo=CODIGO_SEGREDO)

        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "funil.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "funil.html"), "w", encoding="utf-8") as fh:
                    fh.write(saida_html)
        if args.por_organizacao:
            # Exportacao ADITIVA (nao entra no relatorio nem no hash): o alcance por organizacao que
            # o proprio relatorio ja' usa, em JSON, para quem depende deste funil (W8-E03) ler o
            # desfecho em vez de reimplementar a regra de alcance.
            exportar_por_organizacao(contrato, args.porta_banco, desde, ate, args.por_organizacao)
        print(_resumo_texto(relatorio))
        return 0
    except Recusa as exc:
        print("RECUSA %s %s" % (exc.motivo, exc.detalhe))
        return exc.codigo


if __name__ == "__main__":
    sys.exit(main())
