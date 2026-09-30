#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TRE-W1-E04-T01 — deduplicacao de empresas por identificadores fortes.

Fonte da regra: Data Contract V1.0, secao 5 (`docs/data/DATA_CONTRACT_V1.md`) e o
contrato legivel por maquina (`docs/data/data_contract_v1.json`, chave `dedup`):

  - identificadores FORTES: CNPJ -> domain -> LinkedIn Company URL (nessa prioridade);
  - identificadores FRACOS: nome + cidade; nome + telefone; nome + endereco;
  - merge automatico SOMENTE com confianca >= `auto_merge_threshold` (0.95);
  - abaixo do limiar: `REVIEW_REQUIRED` (fila humana) — nunca merge silencioso.

Decisoes de implementacao registradas no card (nao mudam o contrato):

  D1. O limiar de merge e LIDO do contrato a cada chamada. Ninguem passa limiar por
      parametro e nenhuma variavel de ambiente o altera: mudar o limiar exige mudar o
      contrato (governanca da secao 10 do contrato) — e isso e provado por teste.
  D2. Evidencia fraca NUNCA alcanca a faixa de merge: a confianca fraca e limitada a
      `limiar - 0,01` (hoje 0,94) e vai para a fila humana, como o contrato manda.
  D3. Identificador forte presente mas INVALIDO (CNPJ que nao passa no digito
      verificador) DETECTA a duplicidade, porem nunca mergeia automatico: vira revisao.
  D4. `organizations` nao tem coluna de telefone nem de endereco no schema congelado da V1:
      os fracos "nome + telefone" e "nome + endereco" sao lacuna declarada do contrato.
      Nao inventamos coluna; implementavel hoje: "nome + cidade".
  D5. Auditoria de merge: `sync_events` (operation=MERGE, entity_type=organization,
      idempotency_key deterministica, payload com a evidencia completa). Fila humana:
      `human_approvals` (action_type=ORGANIZATION_MERGE_REVIEW, status=PENDING).
      Nenhuma tabela/coluna nova — criar coluna exigiria nova versao do contrato (secao 10).
  D6. Rollback de dado mesclado: a auditoria guarda sobrevivente, duplicado e evidencia;
      `--desfazer-merge <idempotency_key>` reverte os vinculos e registra UNMERGE.

TRE-W1-E04-T02 — `entity_match_confidence` (o campo/score da decisao de merge):

  - o score canonico e `entity_match_confidence` (constante `CAMPO_CONFIANCA`); o nome antigo
    `confianca` continua no MESMO registro como alias do E04-T01 (mesmo valor, mesma origem);
  - o score e calculado por evidencia (forte valido => 1,0; forte invalido => teto fraco;
    fraco qualificado => min(similaridade, teto fraco); sem evidencia qualificada => 0,0);
  - as FAIXAS sao derivadas do contrato a cada chamada (fronteira = `auto_merge_threshold`):
    MERGE_AUTOMATICO [limiar, 1] · REVISAO_HUMANA [piso de candidatura, limiar) ·
    SEM_DUPLICIDADE [0, piso de candidatura). Cobertura de [0,1] sem lacuna/sobreposicao;
  - a decisao do par E a decisao da faixa do score (fonte unica: `faixas_confianca()`), provada
    por teste de coerencia;
  - persistencia: `sync_events.request_payload` (merge) e `human_approvals.proposed_action`
    (revisao) levam `entity_match_confidence`, `..._faixa` e `..._modelo` — nao ha coluna nova
    (criar coluna exige nova versao do contrato; ver `docs/data/entity-match-confidence.md`).

Uso (na VPS do ambiente — ADR-0008, quem fala com o PostgreSQL e a VPS):

  python3 scripts/dedup/deduplicar_organizacoes.py --limiar
  python3 scripts/dedup/deduplicar_organizacoes.py --faixas
  python3 scripts/dedup/deduplicar_organizacoes.py --autoteste
  python3 scripts/dedup/deduplicar_organizacoes.py --autoteste --sabotar persistencia
  python3 scripts/dedup/deduplicar_organizacoes.py --detectar --ambiente dev
  python3 scripts/dedup/deduplicar_organizacoes.py --cenario-ambiente dev
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shlex
import subprocess
import sys
import unicodedata
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CONTRATO_ARQ = RAIZ / "docs/data/data_contract_v1.json"
SCHEMA = "sales_intelligence"
FONTE_AUDITORIA = "dedup_strong_identifiers"
ACAO_REVISAO = "ORGANIZATION_MERGE_REVIEW"
VERSAO_POLITICA = "dedup-strong-identifiers-v1"
PISO_CANDIDATO_FRACO = 0.80  # abaixo disso nem e candidato a duplicidade (D4, contrato omisso)
CAMPO_CONFIANCA = "entity_match_confidence"      # nome canonico do campo (TRE-W1-E04-T02)
CAMPO_CONFIANCA_LEGADO = "confianca"             # alias do E04-T01, mesmo valor, mesmo registro
VERSAO_MODELO_CONFIANCA = "entity-match-confidence-v1"
CENARIO = "tre-w1-e04-t01"
IDENTIFICADORES_FORTES = ("cnpj", "domain", "linkedin_url")
IDENTIFICADORES_REBAIXAVEIS = ("cnpj",)


# --------------------------------------------------------------------------- contrato
def _contrato() -> dict:
    if not CONTRATO_ARQ.is_file():
        raise RuntimeError(f"contrato ausente: {CONTRATO_ARQ}")
    return json.loads(CONTRATO_ARQ.read_text(encoding="utf-8"))


def contrato_versao() -> str:
    return str(_contrato()["contract"]["version"])


def sha_contrato() -> str:
    return hashlib.sha256(CONTRATO_ARQ.read_bytes()).hexdigest()


def limiar_merge() -> float:
    """Limiar de merge automatico — LIDO do contrato a cada chamada (D1).

    Nao existe parametro, constante de modulo ajustavel nem variavel de ambiente que
    mude este valor: a unica forma de mudar o limiar e mudar o contrato/politica.
    """
    dedup = _contrato()["dedup"]
    fortes = tuple(dedup["strong"])
    if fortes != IDENTIFICADORES_FORTES:
        raise RuntimeError(f"contrato: identificadores fortes divergentes: {fortes}")
    return float(dedup["auto_merge_threshold"])


def teto_evidencia_fraca() -> float:
    """Evidencia fraca nunca alcanca a faixa de merge (D2)."""
    return round(limiar_merge() - 0.01, 4)


# ------------------------------------------------------- faixas de confianca (T02)
def _validar_faixas(faixas) -> None:
    """As faixas tem de cobrir [0, 1] sem lacuna e sem sobreposicao.

    Mesmo espirito do tiering de scores do contrato (faixas que cobrem a escala inteira sao
    conferidas por script). Faixa que deixa buraco faria a confianca cair em lugar nenhum.
    """
    ordenadas = sorted(faixas, key=lambda f: f["piso"])
    if ordenadas[0]["piso"] != 0.0 or not ordenadas[0]["piso_inclusivo"]:
        raise RuntimeError("faixas de confianca nao comecam em 0")
    if ordenadas[-1]["teto"] != 1.0 or not ordenadas[-1]["teto_inclusivo"]:
        raise RuntimeError("faixas de confianca nao terminam em 1")
    for atual, seguinte in zip(ordenadas, ordenadas[1:]):
        if atual["teto_inclusivo"] or atual["teto"] != seguinte["piso"] or not seguinte["piso_inclusivo"]:
            raise RuntimeError(f"faixas de confianca com lacuna ou sobreposicao em {atual['teto']}")


def faixas_confianca() -> list:
    """Faixas de confianca do match, DERIVADAS do contrato a cada chamada (D1/T02).

    A fronteira da faixa de merge e o `dedup.auto_merge_threshold` do contrato e o piso das
    faixas inferiores e o piso de candidatura (decisao D4 do E04-T01, o contrato e omisso).
    Nao ha constante de fronteira ajustavel: mudar o limiar no contrato move as faixas junto.
    O modelo SE RECUSA a operar com um contrato que nao consiga representar.
    """
    limiar = limiar_merge()
    piso = PISO_CANDIDATO_FRACO
    teto_fraco = teto_evidencia_fraca()
    if not (0.0 < piso < teto_fraco < limiar <= 1.0):
        raise RuntimeError(
            f"contrato: limiar {limiar} incompativel com as faixas de confianca "
            f"(piso de candidatura {piso}, teto de evidencia fraca {teto_fraco})"
        )
    faixas = [
        {"faixa": "MERGE_AUTOMATICO", "piso": limiar, "teto": 1.0, "piso_inclusivo": True,
         "teto_inclusivo": True, "decisao": "MERGE",
         "significado": "identificador forte valido e identico: merge automatico acontece"},
        {"faixa": "REVISAO_HUMANA", "piso": piso, "teto": limiar, "piso_inclusivo": True,
         "teto_inclusivo": False, "decisao": "REVIEW_REQUIRED",
         "significado": "evidencia suficiente para duvidar e insuficiente para mesclar: fila humana"},
        {"faixa": "SEM_DUPLICIDADE", "piso": 0.0, "teto": piso, "piso_inclusivo": True,
         "teto_inclusivo": False, "decisao": "SEM_DUPLICIDADE",
         "significado": "sem evidencia de identidade que qualifique candidatura"},
    ]
    _validar_faixas(faixas)
    return faixas


def faixa_de_confianca(confianca) -> dict:
    """Faixa documentada em que a confianca cai — e a decisao que ela implica."""
    try:
        valor = float(confianca)
    except (TypeError, ValueError):
        raise ValueError(f"confianca invalida: {confianca!r}")
    if not (0.0 <= valor <= 1.0):
        raise ValueError(f"confianca fora de [0, 1]: {valor}")
    valor = round(valor, 6)
    for faixa in faixas_confianca():
        dentro_piso = valor >= faixa["piso"] if faixa["piso_inclusivo"] else valor > faixa["piso"]
        dentro_teto = valor <= faixa["teto"] if faixa["teto_inclusivo"] else valor < faixa["teto"]
        if dentro_piso and dentro_teto:
            return dict(faixa)
    raise RuntimeError(f"confianca {valor} fora das faixas documentadas")


def tabelas_do_contrato() -> list:
    return [t["name"] for t in _contrato()["tables"]]


# ------------------------------------------------------------------------ normalizacao
def apenas_digitos(valor) -> str:
    return re.sub(r"\D", "", valor or "")


def normalizar_cnpj(valor):
    """CNPJ canonico = 14 digitos. Formato diferente disso nao e identificador forte."""
    digitos = apenas_digitos(valor)
    return digitos if len(digitos) == 14 else None


def cnpj_valido(cnpj) -> bool:
    """Confere os dois digitos verificadores do CNPJ."""
    cnpj = normalizar_cnpj(cnpj)
    if not cnpj or cnpj == cnpj[0] * 14:
        return False

    def dv(base: str) -> str:
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):]
        soma = sum(int(d) * p for d, p in zip(base, pesos))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    return cnpj[12] == dv(cnpj[:12]) and cnpj[13] == dv(cnpj[:13])


def cnpj_com_dv(base12: str) -> str:
    """Gera um CNPJ valido a partir de 12 digitos (usado nos casos sinteticos)."""
    base12 = apenas_digitos(base12)[:12]
    if len(base12) != 12:
        raise ValueError("base de CNPJ precisa de 12 digitos")
    peso1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    peso2 = [6] + peso1
    for peso in (peso1, peso2):
        soma = sum(int(d) * p for d, p in zip(base12, peso))
        resto = soma % 11
        base12 += "0" if resto < 2 else str(11 - resto)
    return base12


def normalizar_dominio(valor):
    """Dominio canonico: minusculo, sem esquema, sem www., sem porta/caminho/consulta."""
    s = (valor or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = s.split("#")[0].split("?")[0].split("/")[0]
    s = s.split("@")[-1]
    s = s.split(":")[0]
    s = s.rstrip(".")
    if s.startswith("www."):
        s = s[4:]
    if "." not in s or s.startswith(".") or s.endswith("."):
        return None
    return s


def normalizar_linkedin(valor):
    """Slug canonico de LinkedIn COMPANY URL. Perfil pessoal (/in/) nao e identificador de empresa."""
    s = (valor or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = s.split("#")[0].split("?")[0]
    partes = [p for p in s.split("/") if p]
    if len(partes) < 3:
        return None
    host = partes[0]
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return None
    if partes[1] not in ("company",):
        return None
    return partes[2]


_SUFIXOS = re.compile(
    r"\b(ltda|limitada|me|epp|eireli|sa|s a|s/a|cia|companhia|holding|grupo|group|"
    r"inc|corp|corporation|company|co)\b"
)


def normalizar_nome(valor) -> str:
    s = unicodedata.normalize("NFKD", valor or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _SUFIXOS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalizar_cidade(valor) -> str:
    return normalizar_nome(valor)


def similaridade_nome(a, b) -> float:
    na, nb = normalizar_nome(a), normalizar_nome(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# --------------------------------------------------------------------------- decisao
def decidir_por_confianca(confianca: float) -> str:
    """Decisao do par = decisao da faixa em que a confianca cai (fonte unica).

    MERGE            -> [limiar, 1] (limiar inclusivo)
    REVIEW_REQUIRED  -> [piso de candidatura, limiar) — fila humana, nunca merge
    SEM_DUPLICIDADE  -> [0, piso de candidatura) — nem candidato a duplicidade

    O limiar vem do contrato e nao e parametro (D1); o piso de candidatura e a decisao D4
    do E04-T01 (o contrato e omisso nele).
    """
    return faixa_de_confianca(confianca)["decisao"]


def _campo_forte(org: dict, normalizador, alternativas=()):
    """Devolve (valor_normalizado, campo_de_origem) do primeiro campo preenchido."""
    for campo in alternativas:
        valor = normalizador(org.get(campo))
        if valor:
            return valor, campo
    return None, None


def avaliar_par(a: dict, b: dict) -> dict:
    """Compara duas organizacoes e decide: MERGE, REVIEW_REQUIRED ou SEM_DUPLICIDADE."""
    limiar = limiar_merge()
    teto_fraco = teto_evidencia_fraca()
    iguais: list = []
    decisivos: list = []
    rebaixamentos: list = []
    evidencias: dict = {}

    # --- identificadores fortes, na prioridade do contrato: cnpj -> domain -> linkedin_url
    cnpj_a, cnpj_b = normalizar_cnpj(a.get("cnpj")), normalizar_cnpj(b.get("cnpj"))
    if cnpj_a and cnpj_b and cnpj_a == cnpj_b:
        iguais.append("cnpj")
        evidencias["cnpj"] = {"valor": cnpj_a, "campo": "cnpj", "valido": cnpj_valido(cnpj_a)}
        if cnpj_valido(cnpj_a):
            decisivos.append("cnpj")
        else:
            rebaixamentos.append("cnpj_invalido")

    for nome, campos in (("domain", ("domain", "website_url")), ("linkedin_url", ("linkedin_url",))):
        normalizador = normalizar_dominio if nome == "domain" else normalizar_linkedin
        va, campo_a = _campo_forte(a, normalizador, campos)
        vb, campo_b = _campo_forte(b, normalizador, campos)
        if va and vb and va == vb:
            iguais.append(nome)
            evidencias[nome] = {"valor": va, "campo": campo_a if campo_a == campo_b else f"{campo_a}/{campo_b}"}
            decisivos.append(nome)

    if decisivos:
        confianca, candidato, motivo = 1.0, True, "identificador_forte"
    elif iguais:
        # so identificador forte invalido (D3): detecta, mas nunca mergeia automatico
        confianca, candidato, motivo = teto_fraco, True, "identificador_forte_invalido"
    else:
        sim = round(similaridade_nome(a.get("legal_name") or a.get("trade_name"),
                                      b.get("legal_name") or b.get("trade_name")), 4)
        cidade_a, cidade_b = normalizar_cidade(a.get("city")), normalizar_cidade(b.get("city"))
        estado_a, estado_b = normalizar_cidade(a.get("state")), normalizar_cidade(b.get("state"))
        evid_fraca = {
            "similaridade_nome": sim,
            "cidade": cidade_a or None,
            "cidade_igual": bool(cidade_a and cidade_a == cidade_b),
            "estado_igual": bool(estado_a and estado_a == estado_b),
        }
        evidencias["fracos"] = evid_fraca
        estado_conflita = bool(estado_a and estado_b and estado_a != estado_b)
        if evid_fraca["cidade_igual"] and sim >= PISO_CANDIDATO_FRACO and not estado_conflita:
            confianca, candidato, motivo = min(sim, teto_fraco), True, "identificador_fraco"
        else:
            # Sem evidencia de identidade que qualifique candidatura: o score NAO finge
            # identidade (vale 0,0) — a similaridade bruta de nome segue em `evidencias.fracos`.
            confianca, candidato, motivo = 0.0, False, "sem_candidatura"

    faixa = faixa_de_confianca(confianca)
    decisao = faixa["decisao"]

    return {
        "candidato": candidato,
        "confianca": round(float(confianca), 6),
        "entity_match_confidence": round(float(confianca), 6),
        "entity_match_confidence_faixa": faixa["faixa"],
        "entity_match_confidence_decisao": faixa["decisao"],
        "entity_match_confidence_modelo": VERSAO_MODELO_CONFIANCA,
        "decisao": decisao,
        "motivo": motivo,
        "identificadores_iguais": iguais,
        "identificadores_decisivos": decisivos,
        "rebaixamentos": rebaixamentos,
        "evidencias": evidencias,
        "limiar_vigente": limiar,
        "teto_evidencia_fraca": teto_fraco,
        "piso_candidato_fraco": PISO_CANDIDATO_FRACO,
    }


def entity_match_confidence(a: dict, b: dict) -> float:
    """O campo/score canonico da decisao de merge: confianca de `a` e `b` serem a MESMA entidade.

    Ponto de entrada publico do T02 — mesma fonte que decide o merge (`avaliar_par`), para nao
    existir um segundo calculo que possa divergir do que e persistido.
    """
    return avaliar_par(a, b)["entity_match_confidence"]


# ----------------------------------------------------------------------- SQL / banco
def _ident(nome: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", nome or ""):
        raise SystemExit(f"FALHOU identificador SQL invalido: {nome!r}")
    return nome


def _sql_txt(valor) -> str:
    return "'" + str(valor).replace("'", "''") + "'"


def _uuid_ou_erro(valor, campo: str) -> str:
    try:
        return str(uuid.UUID(str(valor)))
    except Exception:
        raise SystemExit(f"FALHOU {campo} nao e UUID: {valor!r}")


def versao_auditoria() -> str:
    return f"{VERSAO_POLITICA}/contrato={contrato_versao()}/limiar={limiar_merge():.4f}"


def chave_idempotencia(sobrevivente: str, duplicado: str) -> str:
    return f"org-merge:{_uuid_ou_erro(sobrevivente, 'sobrevivente')}:{_uuid_ou_erro(duplicado, 'duplicado')}"


def rodar_sql(prefixo, sql: str, timeout: int = 120):
    """Executa SQL no ambiente pelo prefixo psql informado (stdin fechado — ADR-0008)."""
    cmd = list(prefixo) + ["-X", "-q", "-v", "ON_ERROR_STOP=1", "-At", "-c", sql]
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _json_da_saida(saida: str):
    for linha in reversed([l for l in saida.splitlines() if l.strip()]):
        try:
            return json.loads(linha)
        except json.JSONDecodeError:
            continue
    return None


def ler_organizacoes(prefixo) -> list:
    sql = (
        "SELECT COALESCE(json_agg(json_build_object("
        "'id', id, 'legal_name', legal_name, 'trade_name', trade_name, 'domain', domain, "
        "'website_url', website_url, 'linkedin_url', linkedin_url, 'cnpj', cnpj, "
        "'city', city, 'state', state)), '[]'::json)::text "
        f"FROM {SCHEMA}.organizations WHERE deleted_at IS NULL"
    )
    rc, out, err = rodar_sql(prefixo, sql)
    if rc != 0:
        raise SystemExit(f"FALHOU leitura de organizations: {err or out}")
    return _json_da_saida(out) or []


def colunas_filhas(prefixo) -> list:
    """Colunas que apontam para organizations (FK declarada + organization_id sem FK)."""
    sql = (
        "WITH fk AS ("
        "  SELECT kcu.table_name AS t, kcu.column_name AS c "
        "  FROM information_schema.table_constraints tc "
        "  JOIN information_schema.key_column_usage kcu "
        "    ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema "
        "  JOIN information_schema.constraint_column_usage ccu "
        "    ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema "
        f"  WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = '{SCHEMA}' "
        "    AND ccu.table_name = 'organizations'), "
        "col AS ("
        "  SELECT table_name AS t, column_name AS c FROM information_schema.columns "
        f"  WHERE table_schema = '{SCHEMA}' AND column_name = 'organization_id') "
        "SELECT t || '|' || c FROM (SELECT * FROM fk UNION SELECT * FROM col) u "
        "WHERE t <> 'organizations' ORDER BY 1"
    )
    rc, out, err = rodar_sql(prefixo, sql)
    if rc != 0:
        raise SystemExit(f"FALHOU descoberta de colunas filhas: {err or out}")
    return [tuple(l.split("|")) for l in out.splitlines() if "|" in l]


def contar_tabelas(prefixo, tabelas) -> dict:
    linhas = ", ".join(
        f"({_sql_txt(t)}, (SELECT count(*) FROM {SCHEMA}.{_ident(t)}))" for t in tabelas
    )
    sql = (
        "SELECT json_object_agg(x.t, x.n)::text FROM "
        f"(SELECT * FROM (VALUES {linhas}) AS v(t, n)) x"
    )
    rc, out, err = rodar_sql(prefixo, sql)
    if rc != 0:
        raise SystemExit(f"FALHOU contagem de tabelas: {err or out}")
    return _json_da_saida(out) or {}


def montar_evidencia(avaliacao: dict, sobrevivente: str, duplicado: str, executado_por: str, extra=None) -> dict:
    """Evidencia persistida do registro auditado (merge em `sync_events`,
    revisao em `human_approvals`).

    TRE-W1-E04-T02: o score canonico vai PERSISTIDO com o nome do contrato
    (`entity_match_confidence`), junto da faixa em que caiu e da versao do modelo que o
    calculou — o registro nao depende de quem le para saber sob que regua decidiu. O nome
    antigo (`confianca`, E04-T01) permanece no MESMO registro como alias de mesmo valor.
    """
    faixa = avaliacao.get("entity_match_confidence_faixa") or faixa_de_confianca(avaliacao["confianca"])["faixa"]
    evidencia = {
        "schema": SCHEMA,
        "sobrevivente_id": sobrevivente,
        "duplicado_id": duplicado,
        CAMPO_CONFIANCA: avaliacao["confianca"],
        f"{CAMPO_CONFIANCA}_faixa": faixa,
        f"{CAMPO_CONFIANCA}_modelo": VERSAO_MODELO_CONFIANCA,
        f"{CAMPO_CONFIANCA}_faixas": faixas_confianca(),
        CAMPO_CONFIANCA_LEGADO: avaliacao["confianca"],  # alias do E04-T01 (mesmo valor) — mesma chave de `confianca`
        "confianca": avaliacao["confianca"],
        "decisao": avaliacao["decisao"],
        "motivo": avaliacao["motivo"],
        "limiar_vigente": avaliacao["limiar_vigente"],
        "identificadores_iguais": avaliacao["identificadores_iguais"],
        "identificadores_decisivos": avaliacao["identificadores_decisivos"],
        "rebaixamentos": avaliacao["rebaixamentos"],
        "evidencias": avaliacao["evidencias"],
        "executado_por": executado_por,
        "politica": versao_auditoria(),
        "contrato": {"arquivo": str(CONTRATO_ARQ.relative_to(RAIZ)), "sha256": sha_contrato()[:16]},
    }
    if extra:
        evidencia.update(extra)
    return evidencia


def gerar_sql_merge(sobrevivente, duplicado, avaliacao, colunas, idempotency_key=None,
                    executado_por="dedup", contexto=None) -> str:
    """SQL (transacional) do merge: repoe filhos no sobrevivente, soft-delete no duplicado
    e grava a auditoria em `sync_events`. Recusa merge abaixo do limiar (nao ha caminho
    para merge silencioso nem para merge fora do contrato)."""
    if avaliacao["decisao"] != "MERGE":
        raise ValueError(
            f"merge recusado: decisao={avaliacao['decisao']} confianca={avaliacao['confianca']} "
            f"limiar={avaliacao['limiar_vigente']} (abaixo do limiar vai para REVIEW_REQUIRED)"
        )
    sobre = _uuid_ou_erro(sobrevivente, "sobrevivente")
    dup = _uuid_ou_erro(duplicado, "duplicado")
    chave = idempotency_key or chave_idempotencia(sobre, dup)
    evidencia = montar_evidencia(avaliacao, sobre, dup, executado_por, contexto)
    resposta = {"operacao": "MERGE", "filhas_reapontadas": [f"{t}.{c}" for t, c in colunas]}
    linhas = ["BEGIN;"]
    for tabela, coluna in colunas:
        linhas.append(
            f"UPDATE {SCHEMA}.{_ident(tabela)} SET {_ident(coluna)} = '{sobre}' WHERE {_ident(coluna)} = '{dup}';"
        )
    linhas.append(
        f"UPDATE {SCHEMA}.organizations SET deleted_at = NOW(), updated_at = NOW() "
        f"WHERE id = '{dup}' AND deleted_at IS NULL;"
    )
    linhas.append(
        f"INSERT INTO {SCHEMA}.sync_events (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, response_payload, completed_at) "
        f"VALUES ('{uuid.uuid4()}', 'organization', '{sobre}', '{FONTE_AUDITORIA}', "
        f"'{SCHEMA}.organizations', 'MERGE', '{versao_auditoria()}', '{chave}', 'DONE', "
        f"{_sql_txt(json.dumps(evidencia, ensure_ascii=False, sort_keys=True))}::jsonb, "
        f"{_sql_txt(json.dumps(resposta, ensure_ascii=False, sort_keys=True))}::jsonb, NOW());"
    )
    linhas.append("COMMIT;")
    return "\n".join(linhas)


def gerar_sql_revisao(candidato, outro, avaliacao, solicitado_por="dedup", contexto=None) -> str:
    """SQL da fila humana: REVIEW_REQUIRED nunca mergeia, so registra a pendencia."""
    cand = _uuid_ou_erro(candidato, "candidato")
    out = _uuid_ou_erro(outro, "outro")
    proposta = montar_evidencia(avaliacao, cand, out, solicitado_por, contexto)
    proposta["operacao"] = "REVIEW_REQUIRED"
    return (
        f"INSERT INTO {SCHEMA}.human_approvals (id, action_type, entity_type, entity_id, requested_by, "
        "proposed_action, status) VALUES ("
        f"'{uuid.uuid4()}', '{ACAO_REVISAO}', 'organization', '{cand}', '{FONTE_AUDITORIA}', "
        f"{_sql_txt(json.dumps(proposta, ensure_ascii=False, sort_keys=True))}::jsonb, 'PENDING');"
    )


def executar_merge(prefixo, sobrevivente, duplicado, avaliacao, colunas, executado_por="dedup", contexto=None) -> dict:
    chave = chave_idempotencia(sobrevivente, duplicado)
    rc, out, err = rodar_sql(
        prefixo, f"SELECT count(*) FROM {SCHEMA}.sync_events WHERE idempotency_key = {_sql_txt(chave)}"
    )
    if rc != 0:
        raise SystemExit(f"FALHOU consulta de idempotencia: {err or out}")
    if int(out.splitlines()[-1] or 0) > 0:
        return {"status": "JA_REGISTRADO", "idempotency_key": chave}
    sql = gerar_sql_merge(sobrevivente, duplicado, avaliacao, colunas, chave, executado_por, contexto)
    rc, out, err = rodar_sql(prefixo, sql)
    if rc != 0:
        raise SystemExit(f"FALHOU merge: {err or out}")
    return {"status": "MESCLADO", "idempotency_key": chave, "saida": out.splitlines()[-1] if out else ""}


def executar_revisao(prefixo, candidato, outro, avaliacao, solicitado_por="dedup", contexto=None) -> dict:
    if avaliacao["decisao"] == "MERGE":
        raise ValueError("executar_revisao chamado para par com decisao MERGE")
    chave = f"org-review:{_uuid_ou_erro(candidato, 'candidato')}:{_uuid_ou_erro(outro, 'outro')}"
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT count(*) FROM {SCHEMA}.human_approvals WHERE action_type = '{ACAO_REVISAO}' AND status = 'PENDING' "
        f"AND proposed_action->>'par' = {_sql_txt(chave)}",
    )
    if rc != 0:
        raise SystemExit(f"FALHOU consulta de revisao: {err or out}")
    if int(out.splitlines()[-1] or 0) > 0:
        return {"status": "JA_REGISTRADO", "par": chave}
    proposto = dict(contexto or {})
    sql = gerar_sql_revisao(candidato, outro, avaliacao, solicitado_por, {**proposto, "par": chave})
    rc, out, err = rodar_sql(prefixo, sql)
    if rc != 0:
        raise SystemExit(f"FALHOU revisao: {err or out}")
    return {"status": "REVISAO_REGISTRADA", "par": chave}


def desfazer_merge(prefixo, chave: str, executado_por="dedup", contexto=None) -> dict:
    """Rollback de merge: le a auditoria, devolve os filhos ao duplicado e registra UNMERGE."""
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT json_agg(json_build_object('entity_id', entity_id, 'request_payload', request_payload))::text "
        f"FROM {SCHEMA}.sync_events WHERE idempotency_key = {_sql_txt(chave)} AND operation = 'MERGE'",
    )
    if rc != 0:
        raise SystemExit(f"FALHOU leitura da auditoria: {err or out}")
    registros = _json_da_saida(out) or []
    if not registros:
        raise SystemExit(f"FALHOU nao existe merge auditado com idempotency_key={chave}")
    sobre = _uuid_ou_erro(registros[0]["entity_id"], "sobrevivente")
    dup = _uuid_ou_erro(registros[0]["request_payload"]["duplicado_id"], "duplicado")
    colunas = colunas_filhas(prefixo)
    reverso = {"operacao": "UNMERGE", "merge_desfeito": chave, "sobrevivente_id": sobre,
               "duplicado_id": dup, "executado_por": executado_por}
    if contexto:
        reverso.update(contexto)
    linhas = ["BEGIN;"]
    for tabela, coluna in colunas:
        linhas.append(
            f"UPDATE {SCHEMA}.{_ident(tabela)} SET {_ident(coluna)} = '{dup}' WHERE {_ident(coluna)} = '{sobre}';"
        )
    linhas.append(
        f"UPDATE {SCHEMA}.organizations SET deleted_at = NULL, updated_at = NOW() WHERE id = '{dup}';"
    )
    linhas.append(
        f"INSERT INTO {SCHEMA}.sync_events (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, response_payload, completed_at) "
        f"VALUES ('{uuid.uuid4()}', 'organization', '{dup}', '{FONTE_AUDITORIA}', "
        f"'{SCHEMA}.organizations', 'UNMERGE', '{versao_auditoria()}', '{chave}:desfeito', 'DONE', "
        f"{_sql_txt(json.dumps(reverso, ensure_ascii=False, sort_keys=True))}::jsonb, "
        f"'{{\"operacao\": \"UNMERGE\"}}'::jsonb, NOW());"
    )
    linhas.append("COMMIT;")
    rc, out, err = rodar_sql(prefixo, "\n".join(linhas))
    if rc != 0:
        raise SystemExit(f"FALHOU desfazer merge: {err or out}")
    return {"status": "DESFEITO", "idempotency_key": f"{chave}:desfeito", "sobrevivente_id": sobre, "duplicado_id": dup}


# ------------------------------------------------------------------------- ambiente
def prefixo_do_ambiente(ambiente: str, prefixo: str | None = None):
    if ambiente in ("prod", "producao", "production"):
        raise SystemExit(
            "FALHOU ADR-005: deduplicacao nao nasce em producao — a sequencia e dev -> homolog -> producao, "
            "com aprovacao humana registrada"
        )
    if prefixo:
        return shlex.split(prefixo)
    cfg = {}
    arq = RAIZ / f"deploy/environments/{ambiente}.env"
    if arq.is_file():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            cfg[k.strip()] = v.strip()
    servico = cfg.get("TRE_PG_SERVICO", f"pg-{ambiente}")
    usuario = cfg.get("TRE_PG_USER", "tre")
    banco = cfg.get("TRE_PG_DB", "sales_intelligence")
    return ["docker", "exec", servico, "psql", "-U", usuario, "-d", banco]


def detectar(prefixo) -> list:
    organizacoes = ler_organizacoes(prefixo)
    achados = []
    for i in range(len(organizacoes)):
        for j in range(i + 1, len(organizacoes)):
            a, b = organizacoes[i], organizacoes[j]
            avaliacao = avaliar_par(a, b)
            if avaliacao["candidato"]:
                achados.append({"a": a["id"], "b": b["id"], "avaliacao": avaliacao})
    return achados


# ------------------------------------------------------------------ suite sintetica
class Suite:
    def __init__(self, titulo: str):
        self.titulo = titulo
        self.itens = 0
        self.falhas = 0

    def chk(self, nome: str, ok: bool, detalhe: str = ""):
        self.itens += 1
        if ok:
            print(f"OK     {nome}")
        else:
            self.falhas += 1
            print(f"FALHOU {nome}" + (f" — {detalhe}" if detalhe else ""))

    def resultado(self, sufixo: str = "TESTE_OK"):
        if self.falhas == 0:
            print(f"RESULTADO: {sufixo} ({self.itens} itens, 0 falhas)")
            return 0
        print(f"RESULTADO: {sufixo.replace('_OK', '_FALHOU')} ({self.itens} itens, {self.falhas} falhas)")
        return 1


def _org(**campos) -> dict:
    base = {"id": str(uuid.uuid4()), "legal_name": None, "trade_name": None, "domain": None,
            "website_url": None, "linkedin_url": None, "cnpj": None, "city": None, "state": None}
    base.update(campos)
    return base


SABOTAGENS = {
    # cada sabotagem quebra um pedaco real do alvo e a suite TEM de reprovar
    "limiar": "decidir_por_confianca e faixa_de_confianca passam a aceitar tudo como MERGE",
    "auditoria": "merge deixa de gravar o registro auditavel em sync_events",
    "identificadores": "normalizacao de CNPJ deixa de reconhecer o identificador",
    "fraco": "evidencia fraca passa a alcancar a faixa de merge",
    "persistencia": "o registro auditado (merge/revisao) perde o entity_match_confidence",
    "coerencia": "a decisao do par deixa de ser a decisao da faixa do score",
}


def aplicar_sabotagem(nome: str):
    modulo = sys.modules[__name__]
    if nome == "limiar":
        setattr(modulo, "decidir_por_confianca", lambda confianca: "MERGE")
        setattr(modulo, "faixa_de_confianca", lambda confianca: {
            "faixa": "MERGE_AUTOMATICO", "piso": 0.0, "teto": 1.0, "piso_inclusivo": True,
            "teto_inclusivo": True, "decisao": "MERGE", "significado": "sabotagem",
        })
    elif nome == "auditoria":
        def _sql_sem_auditoria(sobrevivente, duplicado, avaliacao, colunas, idempotency_key=None,
                               executado_por="dedup", contexto=None):
            return "BEGIN;\nCOMMIT;"
        setattr(modulo, "gerar_sql_merge", _sql_sem_auditoria)
    elif nome == "identificadores":
        setattr(modulo, "normalizar_cnpj", lambda valor: None)
    elif nome == "fraco":
        setattr(modulo, "teto_evidencia_fraca", lambda: 1.0)
    elif nome == "persistencia":
        _original = montar_evidencia

        def _evidencia_sem_campo(*args, **kwargs):
            evidencia = _original(*args, **kwargs)
            for chave in (CAMPO_CONFIANCA, f"{CAMPO_CONFIANCA}_faixa", f"{CAMPO_CONFIANCA}_modelo"):
                evidencia.pop(chave, None)
            return evidencia

        setattr(modulo, "montar_evidencia", _evidencia_sem_campo)
    elif nome == "coerencia":
        _original_avaliar = avaliar_par

        def _avaliar_com_decisao_solta(a, b):
            avaliacao = _original_avaliar(a, b)
            avaliacao["decisao"] = "MERGE"  # score e faixa intactos: so a decisao mente
            return avaliacao

        setattr(modulo, "avaliar_par", _avaliar_com_decisao_solta)
    else:
        raise SystemExit(f"FALHOU sabotagem desconhecida: {nome}")


def rodar_sintetico() -> int:
    """Suite sintetica do motor (sem banco).

    Toda excecao vira ITEM REPROVADO: o wrapper `teste_dedup_sintetico.sh` exige
    `RESULTADO: TESTE_FALHOU` com exit != 0 — um traceback cru cumpriria o exit code sem
    dizer que a suite reprovou, e o teste de sabotagem passaria a medir a coisa errada.
    """
    suite = Suite("deduplicacao — casos sinteticos")
    try:
        _itens_sinteticos(suite)
    except Exception as erro:  # noqa: BLE001 — qualquer excecao e falha medida, nao crash
        suite.chk(f"a suite roda ate o fim sem estourar ({type(erro).__name__})", False,
                  f"{type(erro).__name__}: {erro}")
    return suite.resultado()


def _itens_sinteticos(suite: Suite) -> None:
    limiar = limiar_merge()
    teto = teto_evidencia_fraca()
    cnpj_a = cnpj_com_dv("112223330001")
    cnpj_b = cnpj_com_dv("445556660001")
    cnpj_c = cnpj_com_dv("778889990001")

    # ---- normalizacao
    suite.chk("normalizacao: CNPJ formatado == CNPJ em digitos",
              normalizar_cnpj("11.222.333/0001-81") == normalizar_cnpj("11222333000181")
              and normalizar_cnpj("11.222.333/0001-81") is not None)
    suite.chk("normalizacao: dominio com esquema/www./caminho == dominio nu",
              normalizar_dominio("HTTPS://WWW.Alfa.Teste.Local/contato?x=1") == "alfa.teste.local")
    suite.chk("normalizacao: LinkedIn company URL com consulta/maiuscula == slug nu",
              normalizar_linkedin("https://www.linkedin.com/company/Alfa-Teste/?trk=x") == "alfa-teste")
    suite.chk("normalizacao: LinkedIn de perfil pessoal nao e identificador de empresa",
              normalizar_linkedin("https://www.linkedin.com/in/fulano-de-tal") is None)
    suite.chk("normalizacao: valor sem cara de dominio nao vira dominio",
              normalizar_dominio("MARCADOR-SMOKE") is None)
    suite.chk("validacao: CNPJ do fixture de smoke (12345678000199) e invalido",
              cnpj_valido("12345678000199") is False)
    suite.chk(f"validacao: CNPJ sintetico {cnpj_a} passa no digito verificador",
              cnpj_valido(cnpj_a) is True and cnpj_a != cnpj_b)

    # ---- identificadores fortes, isoladamente
    a = _org(legal_name="Alfa Teste Ltda", cnpj=cnpj_a, domain="alfa.teste.local",
             city="Sao Paulo", state="SP")
    b = _org(legal_name="Empresa Completamente Diferente S.A.", cnpj=cnpj_a,
             domain="outro-dominio.teste.local", linkedin_url="https://www.linkedin.com/company/outra-marca",
             city="Recife", state="PE")
    av = avaliar_par(a, b)
    suite.chk("forte isolado (CNPJ): mesmo CNPJ detecta duplicidade mesmo com nome/cidade/dominio diferentes",
              av["candidato"] and av["decisao"] == "MERGE" and av["confianca"] == 1.0
              and av["identificadores_decisivos"] == ["cnpj"],
              f"decisao={av['decisao']} confianca={av['confianca']} decisivos={av['identificadores_decisivos']}")

    c = _org(legal_name="Beta Servicos", cnpj=cnpj_b, domain="https://www.Beta.Teste.Local/pagina", city="Curitiba")
    d = _org(legal_name="Gamma Comercio", cnpj=cnpj_c, domain="beta.teste.local", city="Curitiba")
    av = avaliar_par(c, d)
    suite.chk("forte isolado (domain): mesmo dominio normalizado detecta duplicidade (merge)",
              av["candidato"] and av["decisao"] == "MERGE" and av["identificadores_decisivos"] == ["domain"],
              f"decisao={av['decisao']} decisivos={av['identificadores_decisivos']} evidencias={av['evidencias']}")

    e = _org(legal_name="Delta Industria", domain="delta.teste.local",
             linkedin_url="https://www.linkedin.com/company/Delta-Industria/?trk=abc", city="Osasco")
    f = _org(legal_name="Delta Industria Unidade Dois", domain="delta-unidade-dois.teste.local",
             linkedin_url="linkedin.com/company/delta-industria", city="Santos")
    av = avaliar_par(e, f)
    suite.chk("forte isolado (LinkedIn): mesmo slug de empresa detecta duplicidade (merge)",
              av["candidato"] and av["decisao"] == "MERGE" and av["identificadores_decisivos"] == ["linkedin_url"],
              f"decisao={av['decisao']} decisivos={av['identificadores_decisivos']}")

    # ---- combinado: todos os fortes juntos, prioridade do contrato e evidencia completa
    g = _org(legal_name="Epsilon Logistica Ltda", cnpj=cnpj_a, domain="epsilon.teste.local",
             linkedin_url="https://www.linkedin.com/company/epsilon-logistica/", city="Sorocaba", state="SP")
    h = _org(legal_name="Epsilon Logistica", cnpj=cnpj_a, domain="EPSILON.TESTE.LOCAL/x",
             linkedin_url="linkedin.com/company/epsilon-logistica?trk=y", city="Sorocaba", state="SP")
    av = avaliar_par(g, h)
    suite.chk("forte em conjunto: CNPJ+dominio+LinkedIn juntos => merge com os tres na evidencia",
              av["candidato"] and av["decisao"] == "MERGE"
              and sorted(av["identificadores_iguais"]) == ["cnpj", "domain", "linkedin_url"]
              and av["identificadores_decisivos"][0] == "cnpj",
              f"iguais={av['identificadores_iguais']} decisivos={av['identificadores_decisivos']}")

    # ---- CNPJ igual mas invalido: detecta, nunca mergeia automatico (D3)
    i = _org(legal_name="Zeta Comercio", cnpj="12345678000199", city="Bauru")
    j = _org(legal_name="Zeta Comercio ME", cnpj="12.345.678/0001-99", city="Bauru")
    av = avaliar_par(i, j)
    suite.chk("CNPJ igual porem invalido: detecta duplicidade e vai para revisao (nunca merge automatico)",
              av["candidato"] and av["decisao"] == "REVIEW_REQUIRED"
              and "cnpj_invalido" in av["rebaixamentos"],
              f"decisao={av['decisao']} rebaixamentos={av['rebaixamentos']}")

    # ---- fracos: nome + cidade
    k = _org(legal_name="Beta Logistica Integrada LTDA", cnpj=cnpj_b, domain="beta-log.teste.local", city="Sorocaba", state="SP")
    l = _org(legal_name="Beta Logistica Integrada", cnpj=cnpj_c, domain="beta-int.teste.local", city="Sorocaba", state="SP")
    av = avaliar_par(k, l)
    suite.chk(f"limite 0,94: nome+cidade com alta similaridade para em {teto} (mantem separado e sinaliza)",
              av["candidato"] and av["decisao"] == "REVIEW_REQUIRED" and abs(av["confianca"] - 0.94) < 1e-9,
              f"confianca={av['confianca']} decisao={av['decisao']} evidencias={av['evidencias'].get('fracos')}")
    suite.chk("limite 0,94: a similaridade bruta de nome e registrada na evidencia (nao e caixa-preta)",
              av["evidencias"]["fracos"]["similaridade_nome"] >= 0.94,
              str(av["evidencias"].get("fracos")))

    # ---- fronteira do limiar (inclusivo)
    suite.chk(f"limite exato: confianca {limiar} => MERGE (limiar inclusivo)",
              decidir_por_confianca(limiar) == "MERGE" and abs(limiar - 0.95) < 1e-9, f"limiar={limiar}")
    suite.chk("limite exato: confianca 0,94 => REVIEW_REQUIRED (mantem separado)",
              decidir_por_confianca(0.94) == "REVIEW_REQUIRED")
    suite.chk("limite exato: confianca 0,949999 => REVIEW_REQUIRED",
              decidir_por_confianca(0.949999) == "REVIEW_REQUIRED")
    suite.chk("limite exato: confianca 1,0 => MERGE",
              decidir_por_confianca(1.0) == "MERGE")

    # ---- negativos: nao e duplicidade
    m = _org(legal_name="Marca Um Comercio", cnpj=cnpj_a, domain="marca-um.teste.local", city="Niteroi")
    n = _org(legal_name="Outra Coisa Totalmente Distinta", cnpj=cnpj_b, domain="marca-dois.teste.local", city="Niteroi")
    av = avaliar_par(m, n)
    suite.chk("negativo: sem identificador comum e nome distinto => SEM_DUPLICIDADE (nenhum merge, nenhuma fila)",
              (not av["candidato"]) and av["decisao"] == "SEM_DUPLICIDADE", f"decisao={av['decisao']}")
    o = _org(legal_name="Delta Servicos Industriais", cnpj=cnpj_a, domain="d1.teste.local", city="Santos")
    p = _org(legal_name="Delta Servicos Industriais", cnpj=cnpj_b, domain="d2.teste.local", city="Bauru")
    av = avaliar_par(o, p)
    suite.chk("negativo: nome igual mas cidade diferente => SEM_DUPLICIDADE (fraco e nome+cidade)",
              (not av["candidato"]) and av["decisao"] == "SEM_DUPLICIDADE", f"decisao={av['decisao']}")

    # ---- entity_match_confidence: score calculado, com faixa (TRE-W1-E04-T02)
    faixas = faixas_confianca()
    suite.chk("faixas: modelo tem as 3 faixas documentadas, da maior para a menor",
              [f["faixa"] for f in faixas] == ["MERGE_AUTOMATICO", "REVISAO_HUMANA", "SEM_DUPLICIDADE"],
              str([f["faixa"] for f in faixas]))
    suite.chk("faixas: cobertura de [0,1] sem lacuna e sem sobreposicao (validador recusa faixa invalida)",
              _faixas_cobrem_a_escala())
    suite.chk("faixas: a fronteira da faixa de merge e o limiar do CONTRATO (nao constante do codigo)",
              faixas[0]["piso"] == limiar and faixas[1]["teto"] == limiar
              and faixas[2]["teto"] == PISO_CANDIDATO_FRACO,
              f"faixas={[(f['piso'], f['teto']) for f in faixas]} limiar={limiar}")
    suite.chk(f"faixas: {teto} (teto da evidencia fraca) cai em REVISAO_HUMANA, com REVIEW_REQUIRED",
              faixa_de_confianca(teto)["faixa"] == "REVISAO_HUMANA"
              and decidir_por_confianca(teto) == "REVIEW_REQUIRED")
    suite.chk("faixas: 0,94 NAO mescla e 1,0 mescla, caindo na faixa MERGE_AUTOMATICO",
              decidir_por_confianca(0.94) == "REVIEW_REQUIRED" and decidir_por_confianca(1.0) == "MERGE"
              and faixa_de_confianca(1.0)["faixa"] == "MERGE_AUTOMATICO")
    suite.chk("faixas: 0,7999 nao e duplicidade e 0,80 ja e candidato (piso inclusivo)",
              faixa_de_confianca(0.7999)["faixa"] == "SEM_DUPLICIDADE"
              and faixa_de_confianca(0.80)["faixa"] == "REVISAO_HUMANA")
    suite.chk("faixas: confianca fora de [0,1] e recusada (nao existe valor silencioso)",
              _recusa_confianca_fora_da_escala())
    suite.chk("score: par forte devolve entity_match_confidence 1,0 na faixa de merge",
              _score_do_par((g, h), 1.0, "MERGE_AUTOMATICO", "MERGE"))
    suite.chk(f"score: par fraco devolve {teto} e nunca alcanca a faixa de merge",
              _score_do_par((k, l), teto, "REVISAO_HUMANA", "REVIEW_REQUIRED"))
    suite.chk("score: par sem evidencia de identidade devolve 0,0 (nao finge identidade)",
              _score_do_par((m, n), 0.0, "SEM_DUPLICIDADE", "SEM_DUPLICIDADE"))
    suite.chk("score: entity_match_confidence(a, b) e a MESMA fonte que decide o merge",
              entity_match_confidence(g, h) == avaliar_par(g, h)["entity_match_confidence"] == 1.0)
    suite.chk("coerencia: em todo par avaliado, a decisao E a decisao da faixa do score",
              _decisoes_coerentes_com_faixas())
    suite.chk("aceite (faixa): no ponto exato, 0,94 nao mescla (merge recusado) e 0,95 mescla (SQL gerado)",
              _limite_094_nao_mescla_095_mescla())

    # ---- limiar nao e ajustavel por codigo de producao (D1)
    suite.chk("governanca: o limiar vigente e o do contrato (0,95) e o teto fraco e 0,94",
              abs(limiar - 0.95) < 1e-9 and abs(teto - 0.94) < 1e-9, f"limiar={limiar} teto={teto}")
    suite.chk("governanca: decidir_por_confianca nao aceita limiar por parametro",
              _recusa_kwarg_limiar())
    suite.chk("governanca: variavel de ambiente nao altera o limiar",
              _limiar_imune_a_ambiente())
    suite.chk("governanca: atributo de modulo nao altera o limiar lido do contrato",
              _limiar_imune_a_atributo())
    suite.chk("governanca: gerar_sql_merge recusa merge com decisao abaixo do limiar",
              _merge_recusa_abaixo_do_limiar())

    # ---- auditoria
    avaliacao = avaliar_par(g, h)
    sql = gerar_sql_merge(g["id"], h["id"], avaliacao, [("signals", "organization_id")], executado_por="teste")
    suite.chk("auditoria: o merge grava sync_events com operacao MERGE e a evidencia",
              "sync_events" in sql and "'MERGE'" in sql and "identificadores_decisivos" in sql
              and versao_auditoria() in sql)
    k1 = chave_idempotencia(g["id"], h["id"])
    sql2 = gerar_sql_merge(g["id"], h["id"], avaliacao, [("signals", "organization_id")])
    suite.chk("auditoria: chave de idempotencia deterministica (merge repetido nao duplica registro)",
              k1 == chave_idempotencia(g["id"], h["id"]) and k1 in sql and k1 in sql2)
    suite.chk("auditoria: a versao auditada carrega o limiar em vigor no momento do merge",
              f"limiar={limiar:.4f}" in versao_auditoria())
    av_fraco = avaliar_par(k, l)
    sql_rev = gerar_sql_revisao(k["id"], l["id"], av_fraco, solicitado_por="teste")
    suite.chk("fila humana: REVIEW_REQUIRED registra human_approvals PENDING",
              "human_approvals" in sql_rev and "PENDING" in sql_rev and ACAO_REVISAO in sql_rev)
    suite.chk("fila humana: o registro de revisao nao mescla nem apaga organizacao (so uma pendencia)",
              "UPDATE" not in sql_rev.upper() and "organizations" not in sql_rev)

    # ---- persistencia do campo canonico no registro auditado (TRE-W1-E04-T02)
    suite.chk("persistencia: o registro de MERGE carrega entity_match_confidence, a faixa e o modelo",
              f'"{CAMPO_CONFIANCA}": 1.0' in sql
              and f'"{CAMPO_CONFIANCA}_faixa": "MERGE_AUTOMATICO"' in sql
              and f'"{CAMPO_CONFIANCA}_modelo": "{VERSAO_MODELO_CONFIANCA}"' in sql,
              "o registro auditado do merge nao leva o campo canonico")
    suite.chk("persistencia: o registro de REVISAO carrega entity_match_confidence e a faixa REVISAO_HUMANA",
              f'"{CAMPO_CONFIANCA}": 0.94' in sql_rev
              and f'"{CAMPO_CONFIANCA}_faixa": "REVISAO_HUMANA"' in sql_rev,
              "o registro da fila humana nao leva o campo canonico")
    suite.chk("persistencia: campo canonico e alias `confianca` (E04-T01) com o MESMO valor no registro",
              f'"{CAMPO_CONFIANCA_LEGADO}": 1.0' in sql and f'"{CAMPO_CONFIANCA}": 1.0' in sql)
    suite.chk("persistencia: o registro leva as faixas vigentes (nao depende de quem le para saber a regua)",
              f'"{CAMPO_CONFIANCA}_faixas"' in sql and '"piso": 0.95' in sql and '"piso": 0.8' in sql)


def _faixas_cobrem_a_escala() -> bool:
    """O validador aceita as faixas do contrato e RECUSA faixa com buraco ou sobreposicao."""
    try:
        _validar_faixas(faixas_confianca())
    except RuntimeError:
        return False
    com_buraco = [dict(f) for f in faixas_confianca()]
    com_buraco[1]["teto"] = 0.90            # deixa buraco entre 0,90 e a faixa de merge
    sobreposta = [dict(f) for f in faixas_confianca()]
    sobreposta[1]["teto"] = 0.99            # invade a faixa de merge
    for invalida in (com_buraco, sobreposta):
        try:
            _validar_faixas(invalida)
            return False
        except RuntimeError:
            continue
    return True


def _recusa_confianca_fora_da_escala() -> bool:
    for valor in (1.5, -0.1, "alta", None):
        try:
            faixa_de_confianca(valor)
            return False
        except (ValueError, TypeError):
            continue
    return True


def _score_do_par(par, esperado: float, faixa: str, decisao: str) -> bool:
    a, b = par
    avaliacao = avaliar_par(a, b)
    return (abs(avaliacao["entity_match_confidence"] - esperado) < 1e-9
            and avaliacao["entity_match_confidence"] == avaliacao["confianca"]
            and avaliacao["entity_match_confidence_faixa"] == faixa
            and avaliacao["entity_match_confidence_decisao"] == decisao
            and avaliacao["decisao"] == decisao)


def _decisoes_coerentes_com_faixas() -> bool:
    """Score, faixa e decisao contam a MESMA historia em pares de todos os tipos de evidencia."""
    cnpj_a = cnpj_com_dv("112223330001")
    cnpj_b = cnpj_com_dv("445556660001")
    pares = [
        (_org(legal_name="Alfa Teste Ltda", cnpj=cnpj_a), _org(legal_name="Nome Bem Diferente", cnpj=cnpj_a)),
        (_org(legal_name="Beta Logistica Integrada LTDA", city="Sorocaba", state="SP"),
         _org(legal_name="Beta Logistica Integrada", city="Sorocaba", state="SP")),
        (_org(legal_name="Marca Um Comercio", cnpj=cnpj_a, city="Niteroi"),
         _org(legal_name="Outra Coisa Distinta", cnpj=cnpj_b, city="Niteroi")),
        (_org(legal_name="Zeta Comercio", cnpj="12345678000199", city="Bauru"),
         _org(legal_name="Zeta Comercio ME", cnpj="12345678000199", city="Bauru")),
    ]
    for a, b in pares:
        avaliacao = avaliar_par(a, b)
        confianca = avaliacao["entity_match_confidence"]
        faixa = faixa_de_confianca(confianca)
        dentro = confianca >= faixa["piso"] and (
            confianca <= faixa["teto"] if faixa["teto_inclusivo"] else confianca < faixa["teto"])
        if (avaliacao["decisao"] != faixa["decisao"]
                or avaliacao["entity_match_confidence_faixa"] != faixa["faixa"]
                or not dentro):
            return False
    return True


def _avaliacao_no_limite(confianca: float) -> dict:
    """Avaliacao sintetica no ponto exato do limiar, para exercitar o caminho do merge."""
    faixa = faixa_de_confianca(confianca)
    return {
        "candidato": True, "confianca": confianca, "decisao": faixa["decisao"], "motivo": "teste_do_limite",
        "identificadores_iguais": ["cnpj"], "identificadores_decisivos": ["cnpj"], "rebaixamentos": [],
        "evidencias": {}, "limiar_vigente": limiar_merge(), "teto_evidencia_fraca": teto_evidencia_fraca(),
        "piso_candidato_fraco": PISO_CANDIDATO_FRACO,
        "entity_match_confidence": confianca, "entity_match_confidence_faixa": faixa["faixa"],
        "entity_match_confidence_decisao": faixa["decisao"], "entity_match_confidence_modelo": VERSAO_MODELO_CONFIANCA,
    }


def _limite_094_nao_mescla_095_mescla() -> bool:
    """O teste de faixa do aceite, no ponto exato: 0,94 o merge RECUSA; 0,95 o merge ACONTECE."""
    sobrevivente, duplicado = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        gerar_sql_merge(sobrevivente, duplicado, _avaliacao_no_limite(0.94), [])
        return False                      # 0,94 gerou SQL de merge: aceite falso
    except ValueError:
        pass
    try:
        sql = gerar_sql_merge(sobrevivente, duplicado, _avaliacao_no_limite(0.95), [])
    except ValueError:
        return False                      # 0,95 recusado: o limiar deixou de ser inclusivo
    return "'MERGE'" in sql and CAMPO_CONFIANCA in sql


def _recusa_kwarg_limiar() -> bool:
    try:
        decidir_por_confianca(0.94, limiar=0.5)  # type: ignore[call-arg]
        return False
    except TypeError:
        return True


def _limiar_imune_a_ambiente() -> bool:
    import os as _os
    antes = limiar_merge()
    _os.environ["TRE_DEDUP_LIMIAR"] = "0.5"
    _os.environ["DEDUP_AUTO_MERGE_THRESHOLD"] = "0.5"
    depois = limiar_merge()
    del _os.environ["TRE_DEDUP_LIMIAR"]
    del _os.environ["DEDUP_AUTO_MERGE_THRESHOLD"]
    return antes == depois == 0.95


def _limiar_imune_a_atributo() -> bool:
    modulo = sys.modules[__name__]
    antes = limiar_merge()
    original = getattr(modulo, "limiar_merge")
    setattr(modulo, "LIMIAR_MERGE", 0.5)
    setattr(modulo, "limiar_merge", lambda: 0.5)  # tenta burlar o contrato
    try:
        depois = modulo.limiar_merge()
    finally:
        setattr(modulo, "limiar_merge", original)
        delattr(modulo, "LIMIAR_MERGE")
    restaurado = limiar_merge()
    # antes e restaurado tem de ser o valor do contrato; so trocando o contrato o valor muda
    return antes == 0.95 and depois == 0.5 and restaurado == 0.95


def _merge_recusa_abaixo_do_limiar() -> bool:
    a = _org(legal_name="Nome A", city="Sao Paulo")
    b = _org(legal_name="Nome A", city="Sao Paulo")
    av = avaliar_par(a, b)
    try:
        gerar_sql_merge(a["id"], b["id"], av, [])
        return False
    except ValueError:
        return True


# ------------------------------------------------------------- cenario no ambiente
SINTETICOS = {
    "A": "dd000001-0000-4000-8000-000000000001",  # sobrevivente (CNPJ valido)
    "B": "dd000002-0000-4000-8000-000000000002",  # duplicado por CNPJ
    "C": "dd000003-0000-4000-8000-000000000003",  # par fraco (nome+cidade) — 0,94
    "D": "dd000004-0000-4000-8000-000000000004",  # par fraco (nome+cidade) — 0,94
    "E": "dd000005-0000-4000-8000-000000000005",  # controle sem relacao
    "S": "dd00000a-0000-4000-8000-00000000000a",  # sinal filho de B
}
CNPJ_SINTETICO_A = cnpj_com_dv("112223330001")
CNPJ_SINTETICO_C = cnpj_com_dv("445556660001")
CNPJ_SINTETICO_D = cnpj_com_dv("778889990001")


def sql_semear_cenario() -> str:
    return f"""
INSERT INTO {SCHEMA}.organizations (id, legal_name, trade_name, domain, website_url, linkedin_url, cnpj, city, state, status, source)
VALUES
 ('{SINTETICOS["A"]}', 'Alfa Teste Dedup Ltda', 'Alfa-Teste', 'alfa-dedup.teste.local', 'https://alfa-dedup.teste.local', 'https://www.linkedin.com/company/alfa-dedup', '{CNPJ_SINTETICO_A}', 'Sorocaba', 'SP', 'DISCOVERED', 'cenario-dedup'),
 ('{SINTETICOS["B"]}', 'Empresa Totalmente Diferente S.A.', 'Diferente', 'beta-dedup.teste.local', 'https://beta-dedup.teste.local', 'https://www.linkedin.com/company/beta-dedup', '{CNPJ_SINTETICO_A}', 'Recife', 'PE', 'DISCOVERED', 'cenario-dedup'),
 ('{SINTETICOS["C"]}', 'Beta Logistica Integrada LTDA', 'Beta-Int', 'beta-int.teste.local', 'https://beta-int.teste.local', 'https://www.linkedin.com/company/beta-int', '{CNPJ_SINTETICO_C}', 'Sorocaba', 'SP', 'DISCOVERED', 'cenario-dedup'),
 ('{SINTETICOS["D"]}', 'Beta Logistica Integrada', 'Beta-Log', 'beta-log.teste.local', 'https://beta-log.teste.local', 'https://www.linkedin.com/company/beta-log', '{CNPJ_SINTETICO_D}', 'Sorocaba', 'SP', 'DISCOVERED', 'cenario-dedup'),
 ('{SINTETICOS["E"]}', 'Omicron Sistemas', 'Omicron', 'omicron.teste.local', 'https://omicron.teste.local', 'https://www.linkedin.com/company/omicron', '{cnpj_com_dv("998887770001")}', 'Niteroi', 'RJ', 'DISCOVERED', 'cenario-dedup')
ON CONFLICT (id) DO NOTHING;
INSERT INTO {SCHEMA}.signals (id, organization_id, signal_type, title)
VALUES ('{SINTETICOS["S"]}', '{SINTETICOS["B"]}', 'GROWTH', 'Sinal sintetico do cenario de dedup')
ON CONFLICT (id) DO NOTHING;
"""


def sql_limpar_cenario() -> str:
    """Limpeza da massa sintetica do cenario. Filtra pelo marcador do cenario no payload:
    NAO usa LIKE generico, para nunca apagar auditoria de merge real do sistema."""
    ids = "', '".join(SINTETICOS.values())
    return f"""
DELETE FROM {SCHEMA}.sync_events WHERE request_payload->>'cenario' = '{CENARIO}';
DELETE FROM {SCHEMA}.human_approvals WHERE proposed_action->>'cenario' = '{CENARIO}';
DELETE FROM {SCHEMA}.signals WHERE id IN ('{ids}');
DELETE FROM {SCHEMA}.organizations WHERE id IN ('{ids}');
"""


def rodar_cenario_ambiente(ambiente: str, prefixo_txt: str | None = None) -> int:
    prefixo = prefixo_do_ambiente(ambiente, prefixo_txt)
    suite = Suite(f"deduplicacao — cenario no ambiente '{ambiente}'")
    tabelas = tabelas_do_contrato()
    print(f"-- alvo: {' '.join(prefixo)}")
    antes = contar_tabelas(prefixo, tabelas)
    print(f"-- estado ANTES: {json.dumps(antes, ensure_ascii=False, sort_keys=True)}")

    rc, out, err = rodar_sql(prefixo, "SELECT 1")
    if rc != 0:
        raise SystemExit(f"FALHOU psql do ambiente: {err or out}")
    suite.chk("ambiente responde e o schema do contrato esta aplicado",
              set(antes.keys()) == set(tabelas), f"tabelas={sorted(antes.keys())}")

    rc, out, err = rodar_sql(prefixo, sql_semear_cenario())
    suite.chk("massa sintetica do cenario aplicada no ambiente", rc == 0, err or out)

    achados = detectar(prefixo)
    por_par = {tuple(sorted((f["a"], f["b"]))): f["avaliacao"] for f in achados}
    par_ab = por_par.get(tuple(sorted((SINTETICOS["A"], SINTETICOS["B"]))))
    par_cd = por_par.get(tuple(sorted((SINTETICOS["C"], SINTETICOS["D"]))))
    par_ae = por_par.get(tuple(sorted((SINTETICOS["A"], SINTETICOS["E"]))))
    suite.chk("deteccao no ambiente: par com mesmo CNPJ => candidato com confianca 1,0 e decisao MERGE",
              bool(par_ab) and par_ab["confianca"] == 1.0 and par_ab["decisao"] == "MERGE",
              str(par_ab))
    suite.chk("deteccao no ambiente: par fraco (nome+cidade) => candidato com confianca 0,94 e decisao REVIEW_REQUIRED",
              bool(par_cd) and abs(par_cd["confianca"] - 0.94) < 1e-9 and par_cd["decisao"] == "REVIEW_REQUIRED",
              str(par_cd))
    suite.chk("deteccao no ambiente: par sem relacao => nao e candidato (SEM_DUPLICIDADE)",
              par_ae is None, str(par_ae))

    colunas = colunas_filhas(prefixo)
    suite.chk("descoberta de vinculos: colunas que apontam para organizations lidas do proprio schema",
              any(t == "signals" and c == "organization_id" for t, c in colunas), str(colunas))

    # ---- merge real do par forte
    res = executar_merge(prefixo, SINTETICOS["A"], SINTETICOS["B"], par_ab, colunas,
                         executado_por="cenario-autoteste", contexto={"cenario": CENARIO})
    suite.chk("merge executado com registro auditavel (sync_events, operation MERGE)",
              res["status"] == "MESCLADO", str(res))
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT json_build_object('auditoria', count(*), 'operacao', max(operation), "
        f"'confianca', max((request_payload->>'confianca')::numeric), "
        f"'entity_match_confidence', max((request_payload->>'{CAMPO_CONFIANCA}')::numeric), "
        f"'faixa', max(request_payload->>'{CAMPO_CONFIANCA}_faixa'), "
        f"'modelo', max(request_payload->>'{CAMPO_CONFIANCA}_modelo'), "
        f"'faixas_no_registro', (bool_and(request_payload ? '{CAMPO_CONFIANCA}_faixas')), "
        f"'limiar', max(request_payload->>'limiar_vigente'))::text "
        f"FROM {SCHEMA}.sync_events WHERE idempotency_key = {_sql_txt(res['idempotency_key'])}",
    )
    registro = _json_da_saida(out) or {}
    suite.chk("auditoria lida de volta do banco: 1 registro MERGE com confianca 1,0 e o limiar vigente",
              rc == 0 and registro.get("auditoria") == 1 and registro.get("operacao") == "MERGE"
              and float(registro.get("confianca") or 0) == 1.0 and float(registro.get("limiar") or 0) == 0.95,
              str(registro))
    suite.chk("campo persistido e lido DE VOLTA do banco: entity_match_confidence 1,0 na faixa MERGE_AUTOMATICO",
              float(registro.get("entity_match_confidence") or 0) == 1.0
              and registro.get("faixa") == "MERGE_AUTOMATICO"
              and registro.get("modelo") == VERSAO_MODELO_CONFIANCA,
              str(registro))
    suite.chk("campo canonico e alias `confianca` do E04-T01 com o MESMO valor no registro persistido",
              str(registro.get("confianca")) == str(registro.get("entity_match_confidence")) != "None",
              str(registro))
    suite.chk("o registro persistido leva a tabela de faixas vigente (registro autossuficiente)",
              registro.get("faixas_no_registro") is True, str(registro))
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT json_build_object('duplicado_soft_deleted', (SELECT deleted_at IS NOT NULL FROM {SCHEMA}.organizations WHERE id='{SINTETICOS['B']}'), "
        f"'filho_reapontado', (SELECT organization_id = '{SINTETICOS['A']}' FROM {SCHEMA}.signals WHERE id='{SINTETICOS['S']}'), "
        f"'sobrevivente_intacto', (SELECT deleted_at IS NULL FROM {SCHEMA}.organizations WHERE id='{SINTETICOS['A']}'))::text",
    )
    estado = _json_da_saida(out) or {}
    suite.chk("merge medido no banco: duplicado soft-deleted, filho reapontado, sobrevivente intacto",
              estado.get("duplicado_soft_deleted") and estado.get("filho_reapontado") and estado.get("sobrevivente_intacto"),
              str(estado))

    # ---- idempotencia: repetir o merge nao cria segundo registro
    res2 = executar_merge(prefixo, SINTETICOS["A"], SINTETICOS["B"], par_ab, colunas,
                          executado_por="cenario-autoteste", contexto={"cenario": CENARIO})
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT count(*) FROM {SCHEMA}.sync_events WHERE idempotency_key = {_sql_txt(res['idempotency_key'])}",
    )
    suite.chk("merge repetido e idempotente (recusado, sem segundo registro)",
              res2["status"] == "JA_REGISTRADO" and int(out.splitlines()[-1] or 0) == 1, f"{res2} count={out}")

    # ---- rollback do dado mesclado, com registro
    res3 = desfazer_merge(prefixo, res["idempotency_key"], executado_por="cenario-autoteste",
                          contexto={"cenario": CENARIO})
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT json_build_object("
        f"'filho_de_volta', (SELECT organization_id = '{SINTETICOS['B']}' FROM {SCHEMA}.signals WHERE id='{SINTETICOS['S']}'), "
        f"'duplicado_visivel', (SELECT deleted_at IS NULL FROM {SCHEMA}.organizations WHERE id='{SINTETICOS['B']}'), "
        f"'unmerge_auditado', (SELECT count(*) FROM {SCHEMA}.sync_events WHERE operation='UNMERGE' AND idempotency_key = '{res['idempotency_key']}:desfeito'))::text",
    )
    desfeito = _json_da_saida(out) or {}
    suite.chk("rollback do merge: vinculos devolvidos, duplicado visivel de novo e UNMERGE auditado",
              res3["status"] == "DESFEITO" and desfeito.get("filho_de_volta") and desfeito.get("duplicado_visivel")
              and desfeito.get("unmerge_auditado") == 1, str(desfeito))

    # ---- par abaixo do limiar: so fila humana
    res4 = executar_revisao(prefixo, SINTETICOS["C"], SINTETICOS["D"], par_cd, solicitado_por="cenario-autoteste",
                            contexto={"cenario": CENARIO})
    suite.chk("par com 0,94 nao mescla: registra pendencia humana (REVIEW_REQUIRED em human_approvals)",
              res4["status"] == "REVISAO_REGISTRADA", str(res4))
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT json_build_object("
        f"'revisoes_pendentes', (SELECT count(*) FROM {SCHEMA}.human_approvals WHERE action_type='{ACAO_REVISAO}' AND status='PENDING' AND proposed_action->>'cenario'='{CENARIO}'), "
        f"'confianca', (SELECT max((proposed_action->>'confianca')::numeric) FROM {SCHEMA}.human_approvals WHERE action_type='{ACAO_REVISAO}' AND proposed_action->>'cenario'='{CENARIO}'), "
        f"'entity_match_confidence', (SELECT max((proposed_action->>'{CAMPO_CONFIANCA}')::numeric) FROM {SCHEMA}.human_approvals WHERE action_type='{ACAO_REVISAO}' AND proposed_action->>'cenario'='{CENARIO}'), "
        f"'faixa', (SELECT max(proposed_action->>'{CAMPO_CONFIANCA}_faixa') FROM {SCHEMA}.human_approvals WHERE action_type='{ACAO_REVISAO}' AND proposed_action->>'cenario'='{CENARIO}'), "
        f"'nada_mesclado', (SELECT count(*) = 2 FROM {SCHEMA}.organizations WHERE id IN ('{SINTETICOS['C']}','{SINTETICOS['D']}') AND deleted_at IS NULL))::text",
    )
    fila = _json_da_saida(out) or {}
    suite.chk("fila humana medida no banco: 1 pendencia com confianca 0,94 e nenhuma organizacao mesclada",
              fila.get("revisoes_pendentes") == 1 and abs(float(fila.get("confianca") or 0) - 0.94) < 1e-9
              and fila.get("nada_mesclado"), str(fila))
    suite.chk("fila humana: o campo canonico persistido na pendencia e 0,94 na faixa REVISAO_HUMANA",
              abs(float(fila.get("entity_match_confidence") or 0) - 0.94) < 1e-9
              and fila.get("faixa") == "REVISAO_HUMANA", str(fila))

    # ---- guardrail de ambiente + limpeza + estado final
    suite.chk("nenhum merge silencioso: o merge do cenario deixou registro auditavel no banco",
              _tem_auditoria_de_merge(prefixo), "")

    rc, out, err = rodar_sql(prefixo, sql_limpar_cenario())
    suite.chk("limpeza da massa sintetica aplicada", rc == 0, err or out)
    depois = contar_tabelas(prefixo, tabelas)
    print(f"-- estado DEPOIS: {json.dumps(depois, ensure_ascii=False, sort_keys=True)}")
    suite.chk("ambiente volta ao estado anterior (contagem por tabela identica ao ANTES)",
              antes == depois, f"antes={antes} depois={depois}")
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT count(*) FROM {SCHEMA}.organizations WHERE id IN ('" + "', '".join(SINTETICOS.values()) + "')",
    )
    suite.chk("nenhuma linha sintetica sobrou no ambiente",
              rc == 0 and int(out.splitlines()[-1] or 0) == 0, out)
    return suite.resultado("CENARIO_OK")


def _tem_auditoria_de_merge(prefixo) -> bool:
    """Prova que o merge do cenario (e o rollback dele) ficaram na trilha de auditoria."""
    rc, out, err = rodar_sql(
        prefixo,
        f"SELECT json_build_object("
        f"'merges', (SELECT count(*) FROM {SCHEMA}.sync_events WHERE operation='MERGE' AND request_payload->>'cenario'='{CENARIO}'), "
        f"'unmerges', (SELECT count(*) FROM {SCHEMA}.sync_events WHERE operation='UNMERGE' AND request_payload->>'cenario'='{CENARIO}'), "
        f"'evidencia_completa', (SELECT bool_and(request_payload ? 'identificadores_decisivos' AND request_payload ? 'limiar_vigente' AND request_payload ? 'contrato') FROM {SCHEMA}.sync_events WHERE operation='MERGE' AND request_payload->>'cenario'='{CENARIO}'))::text",
    )
    registro = _json_da_saida(out) or {}
    return bool(rc == 0 and registro.get("merges") == 1 and registro.get("unmerges") == 1
                and registro.get("evidencia_completa"))


# ------------------------------------------------------------------------- CLI
def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deduplicacao strong identifiers (TRE-W1-E04-T01).")
    p.add_argument("--ambiente", default="dev", help="ambiente alvo (default dev; prod e recusado)")
    p.add_argument("--prefixo", default=None, help="prefixo psql explicito (ex.: 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence')")
    p.add_argument("--executado-por", default="hermes-dev-harness", help="quem executa (vai para a auditoria)")
    p.add_argument("--limiar", action="store_true", help="imprime o limiar em vigor e a origem (contrato)")
    p.add_argument("--faixas", action="store_true", help="imprime as faixas de confianca do match (fronteiras vindas do contrato)")
    p.add_argument("--autoteste", action="store_true", help="roda a suite sintetica (sem banco)")
    p.add_argument("--sabotar", default=None, choices=sorted(SABOTAGENS), help="quebra o alvo de proposito: a suite TEM de reprovar")
    p.add_argument("--detectar", action="store_true", help="varre organizacoes do ambiente e lista candidatos (somente leitura)")
    p.add_argument("--cenario-ambiente", action="store_true", help="ciclo completo do cenario no ambiente (semeia, mergeia, audita, desfaz, revisa, limpa)")
    p.add_argument("--mesclar", nargs=2, metavar=("SOBREVIVENTE", "DUPLICADO"), help="mergeia um par ja avaliado no ambiente")
    p.add_argument("--desfazer-merge", metavar="IDEMPOTENCY_KEY", help="desfaz um merge auditado (rollback com registro)")
    return p


def main(argv=None) -> int:
    args = construir_parser().parse_args(argv)

    if args.limiar:
        print(f"limiar de merge em vigor: {limiar_merge():.4f}")
        print(f"origem: {CONTRATO_ARQ.relative_to(RAIZ)} (chave dedup.auto_merge_threshold)")
        print(f"contrato versao {contrato_versao()} · sha256 {sha_contrato()[:16]}")
        print(f"teto de evidencia fraca (nunca mergeia): {teto_evidencia_fraca():.4f}")
        return 0

    if args.faixas:
        limiar = limiar_merge()
        print(f"faixas de {CAMPO_CONFIANCA} (modelo {VERSAO_MODELO_CONFIANCA})")
        print(f"origem das fronteiras: {CONTRATO_ARQ.relative_to(RAIZ)} (dedup.auto_merge_threshold={limiar:.4f}) "
              f"+ piso de candidatura {PISO_CANDIDATO_FRACO:.2f} (decisao D4 do E04-T01)")
        for faixa in faixas_confianca():
            print(f"  {faixa['faixa']:<17} [{faixa['piso']:.2f}, {faixa['teto']:.2f}{']' if faixa['teto_inclusivo'] else ')'}"
                  f"  -> {faixa['decisao']:<16} {faixa['significado']}")
        print(f"detalhe: 0,94 cai em REVISAO_HUMANA ({decidir_por_confianca(0.94)}); "
              f"0,95 cai em MERGE_AUTOMATICO ({decidir_por_confianca(0.95)}); limiar inclusivo")
        return 0

    if args.autoteste:
        if args.sabotar:
            aplicar_sabotagem(args.sabotar)
            print(f"-- sabotagem aplicada: {args.sabotar} ({SABOTAGENS[args.sabotar]})")
            print("-- esperado: a suite REPROVA (exit != 0)")
        return rodar_sintetico()

    if args.cenario_ambiente:
        return rodar_cenario_ambiente(args.ambiente, args.prefixo)

    if args.detectar:
        prefixo = prefixo_do_ambiente(args.ambiente, args.prefixo)
        achados = detectar(prefixo)
        print(f"-- ambiente '{args.ambiente}' · candidatos a duplicidade: {len(achados)}")
        for f in achados:
            av = f["avaliacao"]
            print(f"   {f['a']} x {f['b']} -> {av['decisao']} {CAMPO_CONFIANCA}={av['entity_match_confidence']} "
                  f"faixa={av['entity_match_confidence_faixa']} "
                  f"motivo={av['motivo']} iguais={av['identificadores_iguais']}")
        return 0

    if args.mesclar:
        prefixo = prefixo_do_ambiente(args.ambiente, args.prefixo)
        sobre, dup = args.mesclar
        organizacoes = {o["id"]: o for o in ler_organizacoes(prefixo)}
        if sobre not in organizacoes or dup not in organizacoes:
            raise SystemExit("FALHOU par nao encontrado entre as organizacoes ativas do ambiente")
        avaliacao = avaliar_par(organizacoes[sobre], organizacoes[dup])
        if avaliacao["decisao"] != "MERGE":
            print(f"FALHOU merge recusado: decisao={avaliacao['decisao']} confianca={avaliacao['confianca']} "
                  f"limiar={avaliacao['limiar_vigente']} — vai para REVIEW_REQUIRED")
            return 1
        res = executar_merge(prefixo, sobre, dup, avaliacao, colunas_filhas(prefixo), args.executado_por)
        print(f"{res['status']} {res['idempotency_key']}")
        return 0 if res["status"] in ("MESCLADO", "JA_REGISTRADO") else 1

    if args.desfazer_merge:
        prefixo = prefixo_do_ambiente(args.ambiente, args.prefixo)
        res = desfazer_merge(prefixo, args.desfazer_merge, args.executado_por)
        print(f"{res['status']} {res['idempotency_key']}")
        return 0

    construir_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
