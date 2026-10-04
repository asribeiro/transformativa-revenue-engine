#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memoria comercial em Qdrant (`memoria-comercial-v1`) — card TRE-W9-E06-T01 (W9 / Inteligencia Avancada).

O que este componente FAZ (e so' isto): MEDE a estabilidade do corpus comercial na base canonica de
`sales_intelligence` em LEITURA PURA e, quando — e so' quando — a PRE-CONDICAO "corpus comercial estavel"
e' atendida, DERIVA a memoria semantica (documentos do corpus com vetor) para uma colecao do Qdrant e
permite CONSULTA-LA por semelhanca. O Qdrant e' MEMORIA DERIVADA, nunca fonte de verdade: a fonte
continua sendo o PostgreSQL, e a memoria inteira e' reconstruivel a partir dele.

Responde tres perguntas:

  1. O CORPUS SUSTENTA A MEMORIA? (pre-condicao "corpus comercial estavel" do doc 11, que nao e' card)
     Quantos documentos indexaveis existem por tipo? Quantos tipos tem base minima? Abaixo do minimo
     declarado o relatorio SAI, mas `memoria_publicada=false` e a lista `faltando` diz o que falta — o
     componente NAO publica memoria com corpus de brinquedo (fail-closed) e NAO escreve nada no Qdrant.
  2. O QUE VIRA MEMORIA? Documentos derivados das receitas declaradas no contrato (mensagem outbound,
     objecao/resposta inbound, dor/hipotese, contexto reutilizavel), cada um com origem canonica
     (tabela + id), texto, canal e data. Documento sem texto, origem desconhecida ou COM PADRAO DE PII
     no texto vai para lacuna nomeada e NAO e' indexado (o componente nunca le `contacts`).
  3. COMO A MEMORIA SE COMPORTA? Idempotencia por id determinístico (UUIDv5 de colecao+tabela+id), entao
     reindexar nao duplica; busca deterministica (score desc, desempate por id) com filtro por tipo e por
     organizacao e piso de score declarado; consulta sem correspondencia retorna vazio, nunca "o menos pior".

Invariantes (cada um com item de suite/aceite):

  1. FONTE DE VERDADE E' O POSTGRESQL: toda consulta roda com `default_transaction_read_only = on` (o
     proprio PostgreSQL recusa escrita) e a auditoria da fonte reprova verbo de escrita ANTES de qualquer
     conexao (`ESCRITA_NO_CODIGO`, exit 3). O componente nao cria tabela, coluna, metrica nem linha —
     a colecao do Qdrant e' descartavel e reconstruivel (`--recriar --confirmo`).
  2. IDEMPOTENCIA: o id do ponto e' UUIDv5 sobre (colecao, tabela de origem, id da origem). Rodar de novo
     sobrescreve o MESMO ponto; a contagem nao cresce e o `hash_do_corpus` nao muda. Conteudo alterado na
     origem atualiza o payload do mesmo ponto (nao cria um segundo).
  3. PAYLOAD FECHADO: so' viajam os campos declarados em `payload_fechado`; nenhum campo de `contacts`
     (e-mail, telefone, WhatsApp, nome, CNPJ) existe no payload — a unidade e' a organizacao (UUID canonico).
  4. PII E' LACUNA, NAO MEMORIA: texto que casa com padrao proibido do contrato vai para lacuna
     `PII_SUSPEITA` com a origem e NAO entra na colecao; o texto indexado nunca carrega esses padroes.
  5. DESEMPATE DECLARADO: busca ordena por score desc e desempate por id asc; resultado abaixo do
     `score_minimo` do contrato e' descartado (sem correspondencia = lista vazia, nao ruido).
  6. DETERMINISMO: o mesmo corpus com o mesmo contrato produz o MESMO `hash_do_relatorio`; `gerado_em` e
     `referencia_temporal` sao a unica diferenca e NAO entram no hash (o estado do Qdrant tambem nao entra:
     ele e' consequencia, nao medicao).
  7. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige Qdrant LOCAL
     (127.0.0.1/localhost/[::1]); host remoto RECUSA (`QDRANT_NAO_E_DEV`); `homolog` exige `--confirmo`;
     `prod` RECUSA por desenho (exit 4). Colecao existente com dimensao diferente da declarada RECUSA
     (`DIMENSAO_DIVERGENTE`) sem escrever.
  8. PROVEDOR DECLARADO: o vetor sai do provedor declarado no contrato (hoje `local-deterministico-v1`,
     hashing de tokens L2-normalizado, sem dependencia e sem rede). Nome de modelo externo/preco NAO mora
     no contrato: trocar de provedor (ou de dimensao) exige versao nova do contrato e reindexacao.

Saida: relatorio JSON + HTML auto-contido. Exit code:
  0 = relatorio gerado (ou plano/conferencia, sem banco) · 2 = uso errado · 3 = recusa
  (contrato/fonte/guarda/colecao/qdrant) · 4 = producao recusada · 5 = segredo vazado.
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
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

VERSAO = "memoria-comercial-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "memoria-comercial-v1.json")
AMBIENTES = ("dev", "homolog", "prod")
HOSTES_LOCAIS = ("127.0.0.1", "localhost", "::1", "[::1]")
NAMESPACE_DO_ID = uuid.UUID("6f1c9c2e-6a4b-4b03-9c1e-9b2f8d7a1c51")
CODIGO_SEGREDO = 5
VERBOS_DE_ESCRITA = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke",
    "comment on", "copy", "vacuum", "reindex", "refresh materialized",
)
SEGREDOS_VIGIADOS = ("TRE_MEMORIA_COMERCIAL_TOKEN", "TRE_MEMORIA_TOKEN", "TRE_QDRANT_API_KEY")


class Recusa(Exception):
    def __init__(self, motivo, detalhe="", codigo=3):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe
        self.codigo = codigo


# ------------------------------------------------------------------------------------------------
# 1. Contrato
# ------------------------------------------------------------------------------------------------
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


CAMPOS_DO_CONTRATO = (
    "versao", "card", "colecao", "provedor_de_embedding", "fonte_de_verdade", "tipos_de_documento",
    "minimos", "busca", "payload_fechado", "privacidade", "guardas_de_ambiente", "pre_condicao", "lacunas",
)


def validar_contrato(contrato):
    faltando = [c for c in CAMPOS_DO_CONTRATO if c not in contrato]
    if faltando:
        raise Recusa("CONTRATO_INCOMPLETO", "campos ausentes: %s" % ", ".join(faltando))
    if contrato["versao"] != VERSAO:
        raise Recusa("CONTRATO_VERSAO_DIVERGENTE", "esperado %s, obtido %s" % (VERSAO, contrato.get("versao")))
    provedor = contrato["provedor_de_embedding"]
    if provedor.get("nome") not in PROVEDORES:
        raise Recusa("PROVEDOR_DESCONHECIDO",
                     "%s (provedor de embedding nao declarado; trocar exige versao nova do contrato)"
                     % provedor.get("nome"))
    if not isinstance(provedor.get("dimensao"), int) or provedor["dimensao"] <= 0:
        raise Recusa("DIMENSAO_INVALIDA", str(provedor.get("dimensao")))
    tipos = [t.get("nome") for t in contrato["tipos_de_documento"]]
    if not tipos or len(set(tipos)) != len(tipos):
        raise Recusa("TIPOS_INVALIDOS", "tipos_de_documento vazio ou com nome repetido")
    for tipo in contrato["tipos_de_documento"]:
        for campo in ("nome", "tabela", "id_coluna", "colunas_de_texto"):
            if not tipo.get(campo):
                raise Recusa("TIPO_INCOMPLETO", "%s sem %s" % (tipo.get("nome"), campo))
        if not tipo["tabela"].startswith("sales_intelligence."):
            raise Recusa("TIPO_FORA_DA_FONTE", "%s: %s" % (tipo["nome"], tipo["tabela"]))
    if not isinstance(contrato["privacidade"].get("padroes_proibidos"), list):
        raise Recusa("PRIVACIDADE_INVALIDA", "padroes_proibidos deve ser lista")
    return contrato


def validar_ambiente(ambiente, qdrant_url, confirmo):
    if ambiente not in AMBIENTES:
        raise Recusa("AMBIENTE_INVALIDO", ambiente, codigo=2)
    if ambiente == "prod":
        raise Recusa("RECUSA_POR_DESENHO", "prod: memoria comercial nao nasce em producao (ADR-005)", codigo=4)
    if not host_local(qdrant_url):
        raise Recusa("QDRANT_NAO_E_DEV", "em %s o Qdrant tem de ser local: %s" % (ambiente, qdrant_url))
    if ambiente == "homolog" and not confirmo:
        raise Recusa("HOMOLOG_SEM_CONFIRMO", "homolog exige --confirmo")


def host_local(url):
    m = re.match(r"^https?://([^/:]+|\[[^\]]+\])(?::\d+)?/?$", url or "")
    if not m:
        return False
    return m.group(1).lower() in HOSTES_LOCAIS


def auditar_fonte(sql):
    """Reprova verbo de escrita ANTES de qualquer conexao: a fonte canonica e' leitura pura."""
    baixo = re.sub(r"\s+", " ", sql.lower())
    for verbo in VERBOS_DE_ESCRITA:
        if re.search(r"(^|[\s(])" + re.escape(verbo) + r"\b", baixo):
            raise Recusa("ESCRITA_NO_CODIGO", "verbo '%s' no SQL da fonte" % verbo)
    if "select" not in baixo:
        raise Recusa("CONSULTA_SEM_SELECT", "SQL da fonte sem SELECT")
    return True


# ------------------------------------------------------------------------------------------------
# 2. Embedding declarado (local, deterministico, sem rede e sem dependencia)
# ------------------------------------------------------------------------------------------------
def normalizar_texto(texto):
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return sem_acento


def tokenizar(texto, palavras_vazias):
    bruto = re.findall(r"[a-z0-9]+", normalizar_texto(texto).lower())
    return [t for t in bruto if len(t) > 1 and t not in palavras_vazias]


def embed(texto, provedor):
    """Vetor do provedor declarado: hashing de tokens com semente fixa + L2. Deterministico por desenho."""
    dimensao = provedor["dimensao"]
    semente = provedor.get("semente", "").encode("utf-8")
    palavras_vazias = set(p.lower() for p in provedor.get("palavras_vazias", []))
    vetor = [0.0] * dimensao
    for token in tokenizar(texto, palavras_vazias):
        dig = hashlib.blake2b(semente + token.encode("utf-8"), digest_size=8).digest()
        inteiro = int.from_bytes(dig, "big")
        indice = inteiro % dimensao
        sinal = 1.0 if (inteiro >> 8) % 2 == 0 else -1.0
        vetor[indice] += sinal
    norma = sum(v * v for v in vetor) ** 0.5
    if norma == 0.0:
        return vetor
    return [round(v / norma, 12) for v in vetor]


PROVEDORES = {"local-deterministico-v1": embed}


# ------------------------------------------------------------------------------------------------
# 3. Corpus (leitura pura na fonte canonica) e pre-condicao
# ------------------------------------------------------------------------------------------------
def montar_consulta(tipo):
    """A receita da consulta vem do CONTRATO; o codigo nao carrega nome de coluna nem filtro literal."""
    textos = " || ' · ' || ".join(
        "coalesce(%s::text,'')" % c for c in tipo["colunas_de_texto"]
    )
    canal = "NULL" if not tipo.get("canal_coluna") else "%s::text" % tipo["canal_coluna"]
    data = "NULL" if not tipo.get("data_coluna") else "to_char(%s,'YYYY-MM-DD')" % tipo["data_coluna"]
    onde = [tipo["filtro"]] if tipo.get("filtro") else []
    onde.append("coalesce(btrim(%s),'') <> ''" % textos)
    sql = (
        "SELECT coalesce(json_agg(t), '[]')::text FROM ("
        " SELECT %(id)s::text AS origem_id, %(org)s::text AS organization_id, %(canal)s AS canal,"
        " %(data)s AS ocorrido_em, nullif(btrim(%(textos)s),'') AS texto"
        " FROM %(tabela)s WHERE %(onde)s"
        ") t;"
    ) % {
        "id": tipo["id_coluna"],
        "org": tipo["organization_id_coluna"],
        "canal": canal,
        "data": data,
        "textos": textos,
        "tabela": tipo["tabela"],
        "onde": " AND ".join(onde),
    }
    auditar_fonte(sql)
    return sql


def extrair_json(saida):
    """Interpreta a resposta da porta de banco.

    ARMADILHA MEDIDA NA VPS (aceite): o `psql` devolve o array JSON em VARIAS linhas (quebra depois
    da virgula, com indentacao) — parsear por linha pega um fragmento e falha com 'Extra data'.
    A leitura correta e' o texto INTEIRO; a varredura por linha fica so' como ultimo recurso.
    """
    texto = (saida or "").strip()
    if not texto:
        raise Recusa("FONTE_SEM_JSON", "resposta vazia da fonte")
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    for linha in reversed([l.strip() for l in texto.splitlines() if l.strip()]):
        if linha[:1] in ("[", "{"):
            try:
                return json.loads(linha)
            except json.JSONDecodeError:
                continue
    raise Recusa("FONTE_JSON_ILEGIVEL", "resposta da fonte nao e' JSON: %r" % texto[:200])


def executar_consulta(porta_banco, sql):
    """Porta de banco = comando base do psql (o mesmo padrao dos irmaos). Sessao READ ONLY."""
    comando = shlex.split(porta_banco)
    if not comando:
        raise Recusa("PORTA_DE_BANCO_AUSENTE", "--porta-banco vazio")
    if not re.search(r"127\.0\.0\.1|localhost|pg-[a-z0-9-]+", " ".join(comando)) and "docker" not in comando[0]:
        raise Recusa("BANCO_NAO_E_DEV", "porta de banco ilegivel como local: %s" % porta_banco)
    args = comando + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A",
                      "-c", "SET default_transaction_read_only = on;",
                      "-c", sql]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Recusa("FONTE_INDISPONIVEL", (proc.stderr or proc.stdout or "").strip()[:400])
    return extrair_json(proc.stdout)


def corpus_de_leitura_pura(contrato, porta_banco):
    """Le cada receita do contrato pela porta de banco (sessao READ ONLY) e deriva os documentos."""
    linhas_por_tipo = {tipo["nome"]: executar_consulta(porta_banco, montar_consulta(tipo))
                       for tipo in contrato["tipos_de_documento"]}
    return derivar_documentos(contrato, linhas_por_tipo)


def derivar_documentos(contrato, linhas_por_tipo):
    """Recebe as linhas cruas por tipo e aplica as regras do contrato (texto, origem, PII) — pura."""
    documentos, lacunas = [], []
    for tipo in contrato["tipos_de_documento"]:
        for linha in linhas_por_tipo.get(tipo["nome"], []):
            texto = (linha.get("texto") or "").strip()
            origem = (linha.get("origem_id") or "").strip()
            if not origem:
                lacunas.append({"lacuna": "ORIGEM_SEM_ID", "tipo": tipo["nome"]})
                continue
            if not texto:
                lacunas.append({"lacuna": "DOCUMENTO_SEM_TEXTO", "tipo": tipo["nome"], "origem_id": origem})
                continue
            achados = pii_em_texto(texto, contrato)
            if achados:
                lacunas.append({"lacuna": "PII_SUSPEITA", "tipo": tipo["nome"], "origem_id": origem,
                                "padroes": achados})
                continue
            documentos.append({
                "tipo": tipo["nome"],
                "origem_tabela": tipo["tabela"],
                "origem_id": origem,
                "organization_id": linha.get("organization_id"),
                "canal": linha.get("canal"),
                "ocorrido_em": linha.get("ocorrido_em"),
                "texto": " ".join(texto.split()),
            })
    documentos.sort(key=lambda d: (d["tipo"], d["origem_id"]))
    return documentos, lacunas


def pii_em_texto(texto, contrato):
    achados = []
    for padrao in contrato["privacidade"]["padroes_proibidos"]:
        if re.search(padrao["regex"], texto or ""):
            achados.append(padrao["nome"])
    return achados


def medir_pre_condicao(contrato, documentos):
    minimos = contrato["minimos"]
    por_tipo = {}
    for tipo in contrato["tipos_de_documento"]:
        por_tipo[tipo["nome"]] = sum(1 for d in documentos if d["tipo"] == tipo["nome"])
    faltando = []
    if len(documentos) < minimos["documentos"]:
        faltando.append("documentos: %d de %d" % (len(documentos), minimos["documentos"]))
    base = [t for t, n in por_tipo.items() if n >= minimos["por_tipo"].get(t, 1)]
    if len(base) < minimos["tipos_com_base"]:
        faltando.append("tipos com base: %d de %d" % (len(base), minimos["tipos_com_base"]))
    for tipo, exigido in minimos["por_tipo"].items():
        if por_tipo.get(tipo, 0) < exigido:
            faltando.append("%s: %d de %d" % (tipo, por_tipo.get(tipo, 0), exigido))
    return {"nome": contrato["pre_condicao"]["nome"], "atendida": not faltando,
            "documentos": len(documentos), "por_tipo": por_tipo, "tipos_com_base": sorted(base),
            "minimos": minimos, "faltando": faltando}


def id_do_ponto(colecao, origem_tabela, origem_id):
    return str(uuid.uuid5(NAMESPACE_DO_ID, "%s|%s|%s" % (colecao, origem_tabela, origem_id)))


def montar_ponto(contrato, documento):
    colecao = contrato["colecao"]["nome"]
    payload = {
        "tipo": documento["tipo"],
        "origem_tabela": documento["origem_tabela"],
        "origem_id": documento["origem_id"],
        "organization_id": documento["organization_id"],
        "canal": documento["canal"],
        "ocorrido_em": documento["ocorrido_em"],
        "conteudo_sha256": hashlib.sha256(documento["texto"].encode("utf-8")).hexdigest(),
        "texto": documento["texto"],
        "colecao_versao": contrato["colecao"]["versao"],
    }
    fora = [c for c in payload if c not in contrato["payload_fechado"]]
    if fora:
        raise Recusa("PAYLOAD_FORA_DO_CONTRATO", ",".join(fora))
    return {"id": id_do_ponto(colecao, documento["origem_tabela"], documento["origem_id"]),
            "vector": embed(documento["texto"], contrato["provedor_de_embedding"]),
            "payload": payload}


# ------------------------------------------------------------------------------------------------
# 4. Qdrant (memoria derivada) via REST
# ------------------------------------------------------------------------------------------------
def q_requisicao(qdrant_url, metodo, caminho, corpo=None, api_key=None):
    url = qdrant_url.rstrip("/") + caminho
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo)
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("api-key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            bruto = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise Recusa("QDRANT_RECUSOU", "%s %s -> %s %s" % (metodo, caminho, exc.code,
                                                           (exc.read() or b"").decode("utf-8", "replace")[:300]))
    except Exception as exc:  # URLError, timeout
        raise Recusa("QDRANT_INDISPONIVEL", "%s %s -> %s" % (metodo, caminho, exc))
    return json.loads(bruto) if bruto else {}


def estado_da_colecao(qdrant_url, colecao, api_key=None):
    try:
        info = q_requisicao(qdrant_url, "GET", "/collections/%s" % colecao, api_key=api_key)
    except Recusa as exc:
        if exc.motivo == "QDRANT_RECUSOU" and "404" in (exc.detalhe or ""):
            return {"existe": False}
        raise
    resultado = info.get("result") or {}
    vetores = ((resultado.get("config") or {}).get("params") or {}).get("vectors") or {}
    return {"existe": True, "dimensao": vetores.get("size"), "distancia": vetores.get("distance"),
            "pontos": resultado.get("points_count")}


def garantir_colecao(qdrant_url, contrato, recriar=False, confirmo=False, api_key=None):
    colecao = contrato["colecao"]["nome"]
    dimensao = contrato["provedor_de_embedding"]["dimensao"]
    estado = estado_da_colecao(qdrant_url, colecao, api_key)
    if recriar:
        if not confirmo:
            raise Recusa("RECRIAR_SEM_CONFIRMO", "--recriar exige --confirmo (a memoria e' derivada e reconstruivel)")
        if estado["existe"]:
            q_requisicao(qdrant_url, "DELETE", "/collections/%s" % colecao, api_key=api_key)
        estado = {"existe": False}
    if estado["existe"]:
        if estado["dimensao"] != dimensao:
            raise Recusa("DIMENSAO_DIVERGENTE",
                         "colecao %s tem %s e o contrato declara %s — reindexar exige versao nova"
                         % (colecao, estado["dimensao"], dimensao))
        return estado
    q_requisicao(qdrant_url, "PUT", "/collections/%s" % colecao,
                 {"vectors": {"size": dimensao, "distance": contrato["colecao"]["distancia"]}}, api_key=api_key)
    return estado_da_colecao(qdrant_url, colecao, api_key)


def upsert_pontos(qdrant_url, colecao, pontos, api_key=None):
    enviados = 0
    for inicio in range(0, len(pontos), 128):
        lote = pontos[inicio:inicio + 128]
        q_requisicao(qdrant_url, "PUT", "/collections/%s/points?wait=true" % colecao, {"points": lote},
                     api_key=api_key)
        enviados += len(lote)
    return enviados


def buscar_no_qdrant(qdrant_url, colecao, vetor, limite, filtro=None, api_key=None):
    corpo = {"vector": vetor, "limit": limite, "with_payload": True}
    if filtro:
        corpo["filter"] = {"must": filtro}
    resposta = q_requisicao(qdrant_url, "POST", "/collections/%s/points/search" % colecao, corpo, api_key=api_key)
    achados = []
    for item in resposta.get("result") or []:
        payload = dict(item.get("payload") or {})
        payload.pop("texto", None)
        achados.append({"id": item.get("id"), "score": round(float(item.get("score", 0.0)), 6), "memoria": payload,
                        "texto": (item.get("payload") or {}).get("texto", "")})
    achados.sort(key=lambda a: (-a["score"], str(a["id"])))
    return achados


def filtrar_por_score(contrato, achados):
    """Piso de score declarado no contrato: abaixo dele o resultado e' ruido e fica fora."""
    return [a for a in achados if a["score"] >= contrato["busca"]["score_minimo"]]


def montar_filtro(contrato, tipo=None, organization_id=None):
    must = []
    if tipo:
        if tipo not in [t["nome"] for t in contrato["tipos_de_documento"]]:
            raise Recusa("TIPO_DESCONHECIDO", "%s (tipos declarados: %s)"
                         % (tipo, ",".join(t["nome"] for t in contrato["tipos_de_documento"])), codigo=2)
        must.append({"key": "tipo", "match": {"value": tipo}})
    if organization_id:
        must.append({"key": "organization_id", "match": {"value": organization_id}})
    return must


# ------------------------------------------------------------------------------------------------
# 5. Relatorios
# ------------------------------------------------------------------------------------------------
def hash_do_relatorio(relatorio):
    """O hash cobre a MEDICAO (contrato + corpus + pre-condicao) — nao o estado do Qdrant nem o relogio."""
    canonico = {
        "versao": relatorio["versao"], "ambiente": relatorio["ambiente"], "colecao": relatorio["colecao"],
        "dimensao": relatorio["provedor_da_memoria"]["dimensao"],
        "sha256_do_contrato": relatorio["sha256_do_contrato"],
        "pre_condicao": relatorio["pre_condicao"],
        "corpus": {"total_documentos": relatorio["corpus"]["total_documentos"],
                   "por_tipo": relatorio["corpus"]["por_tipo"],
                   "documentos": relatorio["corpus"]["documentos"]},
        "lacunas": relatorio["lacunas"],
    }
    for campo in ("consulta", "resultados", "descartados_por_score"):
        if campo in relatorio:
            canonico[campo] = relatorio[campo]
    return hashlib.sha256(json.dumps(canonico, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def render_html(relatorio):
    linhas = []
    for doc in relatorio["corpus"]["documentos"]:
        linhas.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _html.escape(doc["tipo"]), _html.escape(doc["origem_id"]), _html.escape(str(doc["canal"] or "-")),
            _html.escape(str(doc["ocorrido_em"] or "-")), _html.escape(doc["texto"][:120])))
    pre = relatorio["pre_condicao"]
    lacunas = "".join("<li>%s — %s</li>" % (_html.escape(l["lacuna"]), _html.escape(str(l.get("tipo") or "")))
                      for l in relatorio["lacunas"]) or "<li>nenhuma</li>"
    return """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Memoria comercial %(versao)s</title></head>
<body><h1>Memoria comercial — %(versao)s</h1>
<p>Card %(card)s · ambiente %(ambiente)s · colecao %(colecao)s · provedor %(provedor)s (%(dimensao)s dim)</p>
<h2>Pre-condicao: %(pre_nome)s — %(pre_estado)s</h2>
<ul>%(faltando)s</ul>
<h2>Corpus comercial</h2>
<p>%(total)s documentos · %(por_tipo)s</p>
<h2>Documentos derivados</h2>
<table border="1"><tr><th>tipo</th><th>origem</th><th>canal</th><th>data</th><th>texto</th></tr>%(linhas)s</table>
<h2>Lacunas declaradas</h2><ul>%(lacunas)s</ul>
<p>hash do relatorio: %(hash)s</p></body></html>
""" % {
        "versao": _html.escape(relatorio["versao"]), "card": _html.escape(relatorio["card"]),
        "ambiente": _html.escape(relatorio["ambiente"]), "colecao": _html.escape(relatorio["colecao"]),
        "provedor": _html.escape(relatorio["provedor_da_memoria"]["nome"]),
        "dimensao": relatorio["provedor_da_memoria"]["dimensao"],
        "pre_nome": _html.escape(pre["nome"]),
        "pre_estado": "ATENDIDA" if pre["atendida"] else "NAO ATENDIDA",
        "faltando": "".join("<li>%s</li>" % _html.escape(f) for f in pre["faltando"]) or "<li>nada falta</li>",
        "total": relatorio["corpus"]["total_documentos"], "por_tipo": _html.escape(str(relatorio["corpus"]["por_tipo"])),
        "linhas": "".join(linhas), "lacunas": lacunas, "hash": relatorio["hash_do_relatorio"],
    }


def base_do_relatorio(contrato, caminho_contrato, ambiente, qdrant_url, agora):
    return {
        "versao": VERSAO, "card": contrato["card"], "ambiente": ambiente,
        "colecao": contrato["colecao"]["nome"],
        "provedor_da_memoria": {"nome": contrato["provedor_de_embedding"]["nome"],
                                "dimensao": contrato["provedor_de_embedding"]["dimensao"]},
        "fonte_de_verdade": contrato["fonte_de_verdade"],
        "qdrant_local": host_local(qdrant_url),
        "sha256_do_contrato": sha256_de_arquivo(caminho_contrato),
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "referencia_temporal": agora,
        "lacunas_declaradas": list(contrato["lacunas"]),
    }


# ------------------------------------------------------------------------------------------------
# 6. Operacoes
# ------------------------------------------------------------------------------------------------
def operacao_indexar(contrato, caminho_contrato, args, api_key=None):
    base = base_do_relatorio(contrato, caminho_contrato, args.ambiente, args.qdrant_url, args.agora)
    documentos, lacunas = corpus_de_leitura_pura(contrato, args.porta_banco)
    pre = medir_pre_condicao(contrato, documentos)
    base["pre_condicao"] = pre
    base["corpus"] = {
        "total_documentos": len(documentos),
        "por_tipo": {t["nome"]: pre["por_tipo"].get(t["nome"], 0) for t in contrato["tipos_de_documento"]},
        "documentos": [{"tipo": d["tipo"], "origem_id": d["origem_id"], "canal": d["canal"],
                        "ocorrido_em": d["ocorrido_em"],
                        "conteudo_sha256": hashlib.sha256(d["texto"].encode("utf-8")).hexdigest(),
                        "texto": d["texto"]} for d in documentos],
    }
    base["lacunas"] = lacunas
    base["indexacao"] = {"memoria_publicada": False, "pontos_antes": None, "pontos_depois": None,
                         "enviados": 0, "ids_duplicados": 0}
    if not pre["atendida"]:
        base["motivo"] = "pre-condicao nao atendida: nada foi escrito no Qdrant (fail-closed)"
        base["hash_do_relatorio"] = hash_do_relatorio(base)
        return base
    pontos = [montar_ponto(contrato, d) for d in documentos]
    ids = [p["id"] for p in pontos]
    base["indexacao"]["ids_duplicados"] = len(ids) - len(set(ids))
    antes = estado_da_colecao(args.qdrant_url, contrato["colecao"]["nome"], api_key)
    base["indexacao"]["pontos_antes"] = antes.get("pontos") or 0
    garantir_colecao(args.qdrant_url, contrato, recriar=args.recriar, confirmo=args.confirmo, api_key=api_key)
    base["indexacao"]["enviados"] = upsert_pontos(args.qdrant_url, contrato["colecao"]["nome"], pontos, api_key)
    depois = estado_da_colecao(args.qdrant_url, contrato["colecao"]["nome"], api_key)
    base["indexacao"]["pontos_depois"] = depois.get("pontos") or 0
    base["indexacao"]["memoria_publicada"] = True
    base["hash_do_relatorio"] = hash_do_relatorio(base)
    return base


def operacao_buscar(contrato, caminho_contrato, args, api_key=None):
    base = base_do_relatorio(contrato, caminho_contrato, args.ambiente, args.qdrant_url, args.agora)
    base["pre_condicao"] = {"nome": contrato["pre_condicao"]["nome"], "atendida": None, "documentos": None,
                            "por_tipo": {}, "tipos_com_base": [], "minimos": contrato["minimos"], "faltando": []}
    base["corpus"] = {"total_documentos": 0, "por_tipo": {}, "documentos": []}
    base["lacunas"] = []
    estado = estado_da_colecao(args.qdrant_url, contrato["colecao"]["nome"], api_key)
    if not estado["existe"]:
        raise Recusa("COLECAO_AUSENTE", "%s (rode a indexacao antes de consultar)" % contrato["colecao"]["nome"])
    if estado["dimensao"] != contrato["provedor_de_embedding"]["dimensao"]:
        raise Recusa("DIMENSAO_DIVERGENTE", "colecao %s vs contrato" % estado["dimensao"])
    limite = args.limite or contrato["busca"]["limite_padrao"]
    if limite > contrato["busca"]["limite_maximo"]:
        raise Recusa("LIMITE_ACIMA_DO_MAXIMO", "%s > %s" % (limite, contrato["busca"]["limite_maximo"]), codigo=2)
    vetor = embed(args.buscar, contrato["provedor_de_embedding"])
    filtro = montar_filtro(contrato, tipo=args.filtro_tipo, organization_id=args.filtro_organizacao)
    achados = buscar_no_qdrant(args.qdrant_url, contrato["colecao"]["nome"], vetor, limite, filtro, api_key)
    base["consulta"] = {"texto": args.buscar, "limite": limite,
                        "score_minimo": contrato["busca"]["score_minimo"],
                        "filtro": {"tipo": args.filtro_tipo, "organization_id": args.filtro_organizacao},
                        "desempate": contrato["busca"]["desempate"]}
    base["resultados"] = filtrar_por_score(contrato, achados)
    base["descartados_por_score"] = len(achados) - len(base["resultados"])
    base["hash_do_relatorio"] = hash_do_relatorio(base)
    return base


def operacao_planejar(contrato, caminho_contrato, args):
    return {
        "versao": VERSAO, "card": contrato["card"], "modo": "planejar", "ambiente": args.ambiente,
        "colecao": contrato["colecao"]["nome"],
        "provedor_de_embedding": contrato["provedor_de_embedding"]["nome"],
        "dimensao": contrato["provedor_de_embedding"]["dimensao"],
        "fonte_de_verdade": contrato["fonte_de_verdade"],
        "tipos_de_documento": [{"nome": t["nome"], "tabela": t["tabela"]} for t in contrato["tipos_de_documento"]],
        "payload_fechado": list(contrato["payload_fechado"]),
        "guardas": list(contrato["guardas_de_ambiente"].get("regras", [])),
        "pre_condicao": contrato["pre_condicao"]["nome"],
        "minimos": contrato["minimos"],
        "sha256_do_contrato": sha256_de_arquivo(caminho_contrato),
    }


def conferir_segredos(payload):
    texto = json.dumps(payload, ensure_ascii=False)
    for nome in SEGREDOS_VIGIADOS:
        valor = os.environ.get(nome)
        if valor and len(valor) >= 8 and valor in texto:
            raise Recusa("SEGREDO_NA_EVIDENCIA", nome, codigo=CODIGO_SEGREDO)


def gravar_saida(base, diretorio, nome):
    os.makedirs(diretorio, exist_ok=True)
    with open(os.path.join(diretorio, nome + ".json"), "w", encoding="utf-8") as fh:
        json.dump(base, fh, ensure_ascii=False, indent=2, sort_keys=True)
    if "corpus" in base:
        with open(os.path.join(diretorio, nome + ".html"), "w", encoding="utf-8") as fh:
            fh.write(render_html(base))


def montar_parser():
    p = argparse.ArgumentParser(description="Memoria comercial (Qdrant) — card TRE-W9-E06-T01")
    p.add_argument("--ambiente", default="dev", choices=list(AMBIENTES))
    p.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    p.add_argument("--contrato", default=CONTRATO_PADRAO)
    p.add_argument("--porta-banco", default="")
    p.add_argument("--saida", default="")
    p.add_argument("--agora", default="")
    p.add_argument("--confirmo", action="store_true")
    p.add_argument("--recriar", action="store_true")
    p.add_argument("--planejar", action="store_true")
    p.add_argument("--buscar", default="")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--filtro-tipo", default="")
    p.add_argument("--filtro-organizacao", default="")
    return p


def main(argv=None):
    args = montar_parser().parse_args(argv)
    try:
        contrato = validar_contrato(carregar_contrato(args.contrato))
        validar_ambiente(args.ambiente, args.qdrant_url, args.confirmo)
        api_key = os.environ.get("TRE_QDRANT_API_KEY") or None
        if args.planejar:
            base = operacao_planejar(contrato, args.contrato, args)
        elif args.buscar:
            base = operacao_buscar(contrato, args.contrato, args, api_key)
        else:
            if not args.porta_banco:
                raise Recusa("PORTA_DE_BANCO_AUSENTE", "indexacao exige --porta-banco (a fonte e' canonica)", codigo=2)
            base = operacao_indexar(contrato, args.contrato, args, api_key)
        base["agora"] = args.agora or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conferir_segredos(base)
        if args.saida:
            gravar_saida(base, args.saida, "memoria-comercial")
        print(json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Recusa as exc:
        print(json.dumps({"recusa": exc.motivo, "detalhe": exc.detalhe}, ensure_ascii=False))
        return exc.codigo


if __name__ == "__main__":
    sys.exit(main())
