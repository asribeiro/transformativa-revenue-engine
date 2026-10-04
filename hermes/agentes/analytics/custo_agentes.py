#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Custo de agentes — analytics v1 (`custo-agentes-v1`) — card TRE-W8-E05-T01 (W8 / Analytics).

O que este componente FAZ (e so' isto): LE a auditoria de execucao de agente
(`sales_intelligence.agent_runs`, dono: PostgreSQL — contrato de dados §9) e DERIVA, por AGENTE (e
tambem por MODELO e por WORKFLOW), o custo, o consumo de tokens, a latencia e o desfecho de cada
execucao — o quanto cada agente custa e quanto custa cada sucesso dele. Sai em JSON, CSV e num HTML
auto-contido (o painel).
Leitura pura: SO' `SELECT`, dentro de transacao READ ONLY.

Invariantes deste componente (cada um com item de suite/aceite):

  1. LEITURA PURA, NAO E' PREFERENCIA: toda consulta roda com `default_transaction_read_only = on`
     em `-c` SEPARADO (medido no card irmao W8-E01-T01: num unico `-c` o SET nao vale e a escrita de
     prova passa) e a auditoria da fonte reprova verbo de escrita ANTES de qualquer conexao
     (`ESCRITA_NO_CODIGO`, exit 3). O componente nao cria tabela, nao materializa metrica, nao mexe
     em lane nem em modelo.
  2. NULO NAO E' ZERO — e' o coracao desta analise: execucao sem `estimated_cost` NAO entra na soma e
     NAO vira 0; ela vai para lacuna (`runs_sem_custo`) e o grupo dela fica FORA do ranking. Sem essa
     regra, quem menos declara custo seria coroado o mais barato — a metrica mediria a ausencia de
     dado, nao o custo.
  3. NAO SE CALCULA PRECO: o componente NAO multiplica token por tarifa e NAO tem tabela de precos
     (a politica de lane PROIBE fixar preco). Ele reporta o `estimated_cost` que o executor GRAVOU,
     como string decimal de 6 casas — sem float, sem converter moeda.
  4. STATUS FORA DO VOCABULARIO NAO VIRA DESFECHO: o vocabulario (`COMPLETED`, `FAILED`, `REJECTED`,
     `REVIEW_REQUIRED`) e' o dos irmaos que escrevem a auditoria; status ausente ou desconhecido vai
     para lacuna e NAO entra em concluidas/falhas/recusadas/revisao. Recusa declarada NAO e' falha:
     as duas sao medidas separadas, porque recusar de proposito nao e' defeito.
  5. A COLUNA QUE NAO EXISTE NAO SE INVENTA: o card LE a DDL congelada
     (`db/migrations/0001_sales_intelligence_v1.sql`) e RECUSA (exit 3) se qualquer coluna que a
     metrica precisa nao estiver no CREATE TABLE de `agent_runs` — fail-closed, nunca silencio.
  6. A SAIDA E' AGREGADA: `organization_id` e' lido APENAS para contar distintas (o UUID nunca sai);
     `input`, `output` e `error` (JSONB, que podem carregar texto de empresa e e-mail) NAO sao
     selecionados; e o SELECT nunca e' `*`.
  7. DETERMINISMO: a mesma base com o mesmo contrato produz o MESMO relatorio; `gerado_em` e' a unica
     diferenca e NAO entra no `hash_do_relatorio`.
  8. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<dev|aceite> psql`, via --porta-banco/TRE_CUSTO_AGENTES_PORTA_BANCO) —
     prefixo remoto RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige `--confirmo`; `prod` RECUSA por
     desenho (exit 4).
  9. SEGREDO: nenhum valor de segredo entra na linha de comando; se o valor de
     `TRE_CUSTO_AGENTES_TOKEN` aparecer na evidencia, a rodada e' recusada (`SENHA_VAZADA`, exit 5).

Saida: relatorio JSON + CSV + HTML auto-contido. Exit code:
  0 = relatorio gerado (ou plano/conferencia, sem banco) · 2 = uso errado · 3 = recusa
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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

VERSAO = "custo-agentes-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "custo-agentes-v1.json")
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5
LIMITE_AMOSTRA_PADRAO = 3
TABELA = "sales_intelligence.agent_runs"
CAMPOS_POR_LINHA = 14
CASAS_CUSTO = 6
CASAS_TAXA = 4
CASAS_SEGUNDOS = 2

# Guarda de ambiente: em dev a porta de banco e' `docker exec -i <container local> psql ...`
# (mesmo padrao medido nos cards irmaos W6-E05 / W7-E01/E06 / W8-E01/E04). Prefixo remoto e' recusado.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(sales|custo|analytics|aceite)[A-Za-z0-9._-]*$")
VERBOS_DE_ESCRITA = re.compile(
    r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|COMMENT|CALL|DO)\b",
    re.IGNORECASE,
)
RE_SO_SELECT = re.compile(r"^(SELECT|WITH)\b", re.IGNORECASE)
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


def caminho_ddl(dir_raiz):
    """A DDL e' declarada pelo proprio contrato de dados (artifacts.schema_sql) — nao se chuta caminho."""
    with open(caminho_contrato_de_dados(dir_raiz), "r", encoding="utf-8") as fh:
        dados = json.load(fh)
    rel = (dados.get("artifacts") or {}).get("schema_sql")
    if not rel:
        raise Recusa("DDL_NAO_DECLARADA", "docs/data/data_contract_v1.json#artifacts.schema_sql")
    return os.path.join(dir_raiz, rel)


def colunas_da_ddl(texto_ddl, tabela=TABELA):
    """Colunas do CREATE TABLE de `tabela` — lido da DDL congelada, sem presumir nada."""
    alvo = tabela.split(".")[-1]
    casado = re.search(r"CREATE\s+TABLE\s+%s\s*\((.*?)\n\);" % re.escape(tabela), texto_ddl, re.S | re.I)
    if not casado:
        raise Recusa("TABELA_AUSENTE_NA_DDL", tabela)
    colunas = []
    for linha in casado.group(1).splitlines():
        linha = linha.split("--")[0].strip().rstrip(",")
        if not linha or linha.upper().startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK", "CONSTRAINT")):
            continue
        nome = linha.split()[0]
        if re.fullmatch(r"[a-z_][a-z0-9_]*", nome):
            colunas.append(nome)
    if not colunas:
        raise Recusa("DDL_SEM_COLUNAS", alvo)
    return colunas


def validar_contrato(contrato, contrato_dados, texto_ddl):
    """Componente x contrato de dados x DDL congelada: fail-closed, exit 3."""
    if contrato.get("versao") != VERSAO:
        raise Recusa("VERSAO_DO_CONTRATO_DESCONHECIDA", str(contrato.get("versao")))
    if not contrato.get("card"):
        raise Recusa("CONTRATO_SEM_CARD")
    declaradas = list((contrato.get("colunas_de_leitura") or {}).get("selecionadas") or [])
    if not declaradas:
        raise Recusa("CONTRATO_SEM_COLUNAS")
    if len(set(declaradas)) != len(declaradas):
        raise Recusa("COLUNA_DUPLICADA", str(declaradas))
    na_ddl = colunas_da_ddl(texto_ddl)
    faltando = [c for c in declaradas if c not in na_ddl]
    if faltando:
        raise Recusa("CONTRATO_DIVERGE_DA_DDL", "colunas ausentes no CREATE TABLE: %s" % faltando)
    # O coracao da analise nao pode ser promessa: as tres colunas de custo/token TEM de existir.
    for obrigatoria in ("estimated_cost", "tokens_input", "tokens_output", "status", "started_at"):
        if obrigatoria not in na_ddl:
            raise Recusa("DDL_SEM_COLUNA_DE_MEDICAO", obrigatoria)
    proibidas = contrato["colunas_de_leitura"].get("proibidas_no_select") or {}
    if not proibidas:
        raise Recusa("CONTRATO_SEM_COLUNAS_PROIBIDAS")
    interseccao = sorted(set(proibidas) & set(declaradas))
    if interseccao:
        raise Recusa("CONTRATO_INCOERENTE", "coluna proibida E selecionada: %s" % interseccao)
    # Vocabulario: as classes tem de PARTICIONAR exatamente o vocabulario do irmao.
    classes = (contrato.get("vocabulario_status") or {}).get("classes") or {}
    if not classes:
        raise Recusa("CONTRATO_SEM_VOCABULARIO_DE_STATUS")
    vistas = []
    for nome, valores in classes.items():
        if not valores:
            raise Recusa("CLASSE_DE_STATUS_VAZIA", nome)
        vistas.extend(valores)
    if len(set(vistas)) != len(vistas):
        raise Recusa("STATUS_EM_DUAS_CLASSES", str(sorted(vistas)))
    if set(classes) != {"CONCLUIDA", "FALHA", "RECUSADA", "REVISAO"}:
        raise Recusa("CLASSES_DIVERGEM_DO_MEDIDO", str(sorted(classes)))
    # `agent_runs` tem de estar declarada como tabela do contrato de dados (dono do fato).
    tabelas = [t.get("name") for t in (contrato_dados.get("tables") or [])]
    if "agent_runs" not in tabelas:
        raise Recusa("TABELA_NAO_DECLARADA_NO_CONTRATO_DE_DADOS", "agent_runs")
    return True


# --------------------------------------------------------------------------------------------
# 1. Guardas
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
            raise Recusa("PORTA_BANCO_AUSENTE", "dev exige --porta-banco/TRE_CUSTO_AGENTES_PORTA_BANCO")
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


# --------------------------------------------------------------------------------------------
# 2. Consultas (montadas a partir do contrato; nunca SQL literal fora do contrato)
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
    """UMA fonte: a auditoria de execucao. As colunas vem do contrato — nunca `*`."""
    selecionadas = list(contrato["colunas_de_leitura"]["selecionadas"])
    if "agent_name" not in selecionadas or "organization_id" not in selecionadas:
        raise Recusa("CONTRATO_INCOERENTE", "faltam colunas estruturais (agent_name/organization_id)")
    expressao = []
    for coluna in selecionadas:
        if coluna == "organization_id":
            # Lido SO' para contagem; normalizado a texto vazio quando nulo.
            expressao.append("COALESCE(%s::text, '')" % coluna)
        elif coluna in ("started_at", "finished_at"):
            # Carimbo em ISO UTC (o mesmo formato que o componente aceita em --desde/--ate).
            expressao.append(
                "COALESCE(to_char(%s AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS'), '')" % coluna
            )
        elif coluna in ("tokens_input", "tokens_output", "estimated_cost"):
            expressao.append("COALESCE(%s::text, '')" % coluna)
        else:
            expressao.append("COALESCE(NULLIF(%s, ''), '')" % coluna)
    sql = "SELECT " + " || '|' || ".join(expressao) + " FROM %s WHERE TRUE%s ORDER BY agent_name, started_at, id" % (
        TABELA,
        _janela("started_at", desde, ate),
    )
    q = {"EXECUCOES_DE_AGENTE": sql}
    declaradas = set((contrato.get("fontes") or {}).keys()) if contrato.get("fontes") else set(q)
    montadas = set(q.keys())
    if contrato.get("fontes") and declaradas != montadas:
        raise Recusa(
            "FONTES_DIVERGEM_DO_CONTRATO",
            "so' no contrato=%s so' no componente=%s" % (sorted(declaradas - montadas), sorted(montadas - declaradas)),
        )
    auditar_fonte(q)
    return q


# --------------------------------------------------------------------------------------------
# 3. Execucao (leitura pura)
# --------------------------------------------------------------------------------------------
def executar_consulta(porta_banco, sql, timeout=60):
    # Leitura pura em DOIS passos, de proposito: `SET` e `SELECT` em `-c` separados.
    # Medido no card irmao W8-E01-T01: com `SET ...; SELECT ...` num unico `-c` o SET NAO vale (a
    # transacao implicita ja' comecou antes dele e o INSERT de prova PASSOU), enquanto com dois `-c`
    # o PostgreSQL recusa a escrita de verdade ("cannot execute INSERT in a read-only transaction").
    comando = shlex.split(porta_banco) + [
        "-A", "-t", "-F", "|", "-q", "-v", "ON_ERROR_STOP=1",
        "-c", "SET default_transaction_read_only = on",
        "-c", sql,
    ]
    try:
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise Recusa("PORTA_BANCO_INDISPONIVEL", str(exc), codigo=2)
    except subprocess.TimeoutExpired:
        raise Recusa("CONSULTA_EXCEDEU_TEMPO", sql[:80])
    if proc.returncode != 0 or "ERROR" in (proc.stderr or "").upper():
        raise Recusa("BANCO_RECUSOU_A_CONSULTA", (proc.stderr or "").strip()[:300])
    return [linha for linha in (proc.stdout or "").splitlines() if linha.strip() != ""]


def ler_fontes(porta_banco, consultas):
    return {fid: executar_consulta(porta_banco, sql) for fid, sql in consultas.items()}


# --------------------------------------------------------------------------------------------
# 4. Derivacao (puro — testavel sem banco)
# --------------------------------------------------------------------------------------------
def _decimal(texto):
    """Numero declarado ou None. Nulo e' AUSENTE, nao zero: quem chamar decide o que fazer."""
    if texto is None:
        return None
    texto = str(texto).strip()
    if texto == "":
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise Recusa("VALOR_NAO_NUMERICO_NA_PORTA", texto[:40])


def _inteiro(texto):
    valor = _decimal(texto)
    if valor is None:
        return None
    if valor != valor.to_integral_value():
        raise Recusa("TOKEN_NAO_INTEIRO_NA_PORTA", str(valor))
    return int(valor)


def _instante(texto):
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Recusa("CARIMBO_FORA_DO_FORMATO", texto)


def _custo_str(valor):
    """Custo sai como STRING decimal de 6 casas: float perderia o valor que o produtor gravou."""
    return "0.000000" if valor is None else str(Decimal(valor).quantize(Decimal("1." + "0" * CASAS_CUSTO)))


def _custo_razao(total, denominador):
    """Razao de dinheiro: STRING decimal de 6 casas (nunca float). Nulo quando nao ha denominador."""
    if not denominador:
        return None
    return _custo_str(Decimal(total) / Decimal(denominador))


def _taxa(numerador, denominador):
    if not denominador:
        return None
    return round(float(numerador) / float(denominador), CASAS_TAXA)


def _segundos(valores):
    if not valores:
        return None
    return round(sum(valores) / len(valores), CASAS_SEGUNDOS)


def _percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    posto = int(-(-len(ordenados) * p // 1))  # ceil(n*p) pelo metodo do posto mais proximo (declarado)
    posto = min(max(posto, 1), len(ordenados))
    return round(ordenados[posto - 1], CASAS_SEGUNDOS)


def classificar_status(contrato, status):
    if not status:
        return None
    for classe, valores in contrato["vocabulario_status"]["classes"].items():
        if status in valores:
            return classe
    return None


def extrair_execucoes(linhas, contrato):
    """Linhas cruas da porta -> execucoes normalizadas + lacunas de forma. Fail-closed no formato."""
    execucoes = []
    lacunas = {"linhas_malformadas": 0, "runs_sem_status": 0, "status_fora_do_vocabulario": {},
               "runs_sem_agente": 0, "runs_sem_modelo": 0, "runs_sem_workflow": 0,
               "runs_sem_custo": 0, "runs_custo_negativo": 0, "runs_custo_zero": 0,
               "runs_sem_tokens": 0, "tokens_negativos": 0, "latencia_incompleta": 0, "latencia_invertida": 0}
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) != CAMPOS_POR_LINHA:
            lacunas["linhas_malformadas"] += 1
            raise Recusa(
                "LINHA_FORA_DO_FORMATO",
                "esperado %d campos, obtido %d (%s)" % (CAMPOS_POR_LINHA, len(campos), linha[:60]),
            )
        (agente, papel, versao, fluxo, fluxo_versao, modelo, disparado,
         organizacao, inicio, fim, status, t_in, t_out, custo) = campos
        classe = classificar_status(contrato, status)
        if not status:
            lacunas["runs_sem_status"] += 1
        elif classe is None:
            lacunas["status_fora_do_vocabulario"][status] = lacunas["status_fora_do_vocabulario"].get(status, 0) + 1
        if not agente:
            lacunas["runs_sem_agente"] += 1
        if not modelo:
            lacunas["runs_sem_modelo"] += 1
        if not fluxo:
            lacunas["runs_sem_workflow"] += 1
        valor_custo = _decimal(custo)
        if custo.strip() == "":
            lacunas["runs_sem_custo"] += 1
        elif valor_custo is not None and valor_custo < 0:
            lacunas["runs_custo_negativo"] += 1
            valor_custo = None
        elif valor_custo is not None and valor_custo == 0:
            lacunas["runs_custo_zero"] += 1
        valor_in = _inteiro(t_in)
        valor_out = _inteiro(t_out)
        if valor_in is None or valor_out is None:
            lacunas["runs_sem_tokens"] += 1
        if (valor_in is not None and valor_in < 0) or (valor_out is not None and valor_out < 0):
            lacunas["tokens_negativos"] += 1
            valor_in = None if (valor_in is not None and valor_in < 0) else valor_in
            valor_out = None if (valor_out is not None and valor_out < 0) else valor_out
        instante_inicio = _instante(inicio)
        instante_fim = _instante(fim)
        latencia = None
        if instante_inicio is None or instante_fim is None:
            lacunas["latencia_incompleta"] += 1
        elif instante_fim < instante_inicio:
            lacunas["latencia_invertida"] += 1
        else:
            latencia = (instante_fim - instante_inicio).total_seconds()
        execucoes.append({
            "agente": agente, "papel": papel, "versao": versao, "workflow": fluxo,
            "workflow_versao": fluxo_versao, "modelo": modelo, "disparado_por": disparado,
            "organizacao": organizacao, "status": status, "classe": classe,
            "custo": valor_custo, "tokens_input": valor_in, "tokens_output": valor_out,
            "latencia_s": latencia,
        })
    return execucoes, lacunas


def _metricas_vazias():
    return {
        "runs": 0, "concluidas": 0, "falhas": 0, "recusadas": 0, "revisao": 0, "sem_status": 0,
        "classificadas": 0, "taxa_de_falha": None, "taxa_de_recusa": None,
        "tokens_input": 0, "tokens_output": 0, "tokens_totais": 0, "runs_sem_tokens": 0,
        "custo_total": _custo_str(None), "runs_com_custo": 0, "runs_sem_custo": 0,
        "custo_medio_por_execucao": None, "custo_por_execucao_concluida": None,
        "latencia_media_s": None, "latencia_mediana_s": None, "latencia_p95_s": None,
        "runs_sem_latencia": 0, "organizacoes": 0,
    }


def agregar(execucoes, limite_amostra):
    m = _metricas_vazias()
    latencias = []
    custo_total = Decimal("0")
    organizacoes = set()
    for e in execucoes:
        m["runs"] += 1
        classe = e["classe"]
        if classe == "CONCLUIDA":
            m["concluidas"] += 1
        elif classe == "FALHA":
            m["falhas"] += 1
        elif classe == "RECUSADA":
            m["recusadas"] += 1
        elif classe == "REVISAO":
            m["revisao"] += 1
        else:
            m["sem_status"] += 1
        if e["custo"] is None:
            m["runs_sem_custo"] += 1
        else:
            custo_total += e["custo"]
            m["runs_com_custo"] += 1
        if e["tokens_input"] is None or e["tokens_output"] is None:
            m["runs_sem_tokens"] += 1
        else:
            m["tokens_input"] += e["tokens_input"]
            m["tokens_output"] += e["tokens_output"]
        if e["latencia_s"] is None:
            m["runs_sem_latencia"] += 1
        else:
            latencias.append(e["latencia_s"])
        if e["organizacao"]:
            organizacoes.add(e["organizacao"])
    m["classificadas"] = m["concluidas"] + m["falhas"] + m["recusadas"] + m["revisao"]
    m["taxa_de_falha"] = _taxa(m["falhas"], m["classificadas"])
    m["taxa_de_recusa"] = _taxa(m["recusadas"], m["classificadas"])
    m["tokens_totais"] = m["tokens_input"] + m["tokens_output"]
    m["custo_total"] = _custo_str(custo_total if m["runs_com_custo"] else None)
    if m["runs_com_custo"]:
        m["custo_medio_por_execucao"] = _custo_razao(custo_total, m["runs_com_custo"])
    if m["concluidas"] and m["runs_com_custo"] == m["runs"] and m["runs"]:
        m["custo_por_execucao_concluida"] = _custo_razao(custo_total, m["concluidas"])
    m["latencia_media_s"] = _segundos(latencias)
    m["latencia_mediana_s"] = _percentil(latencias, 0.5)
    m["latencia_p95_s"] = _percentil(latencias, 0.95)
    m["organizacoes"] = len(organizacoes)
    m["amostra_suficiente"] = m["concluidas"] >= limite_amostra
    m["limite_amostra"] = limite_amostra
    return m


def agrupar(execucoes, chave, limite_amostra):
    grupos = {}
    for e in execucoes:
        k = e[chave]
        if not k:
            continue  # sem chave nao vira grupo: a lacuna ja' foi contada em extrair_execucoes
        grupos.setdefault(k, []).append(e)
    return {k: agregar(v, limite_amostra) for k, v in sorted(grupos.items())}


def versoes_por_agente(execucoes):
    mapa = {}
    for e in execucoes:
        if not e["agente"]:
            continue
        mapa.setdefault(e["agente"], set()).add(e["versao"] or "(sem versao)")
    return {k: sorted(v) for k, v in mapa.items()}


def calcular_ranking(por_agente):
    """Quem custa mais POR SUCESSO. Grupo que nao declara custo fica FORA: total subdeclarado nao compara."""
    motivos = {
        "SEM_EXECUCOES": "o recorte nao tem execucao alguma",
        "SEM_CUSTO_DECLARADO": "nenhum agente declara custo: nao ha o que comparar",
        "CUSTO_NAO_DECLARADO_EM_PARTE": "todo agente com amostra declara custo so' em parte das execucoes",
        "AMOSTRA_INSUFICIENTE": "os agentes com custo completo ficam abaixo do limite de amostra",
    }
    if not por_agente:
        return {"alvo": "agente_mais_caro_por_execucao_concluida", "vencedor": None, "motivo": "SEM_EXECUCOES",
                "motivo_texto": motivos["SEM_EXECUCOES"], "ordem_declarada":
                "custo_por_execucao_concluida desc, concluidas desc, agente asc"}
    completos = {k: v for k, v in por_agente.items() if v["runs_sem_custo"] == 0 and v["runs_com_custo"] > 0}
    com_amostra = {k: v for k, v in completos.items() if v["concluidas"] >= v["limite_amostra"]}
    if not completos:
        return {"alvo": "agente_mais_caro_por_execucao_concluida", "vencedor": None, "motivo": "SEM_CUSTO_DECLARADO",
                "motivo_texto": motivos["SEM_CUSTO_DECLARADO"], "ordem_declarada":
                "custo_por_execucao_concluida desc, concluidas desc, agente asc"}
    if not com_amostra:
        tem_amostra_sem_custo = any(v["concluidas"] >= v["limite_amostra"] for v in por_agente.values())
        motivo = "AMOSTRA_INSUFICIENTE" if not tem_amostra_sem_custo else "CUSTO_NAO_DECLARADO_EM_PARTE"
        return {"alvo": "agente_mais_caro_por_execucao_concluida", "vencedor": None, "motivo": motivo,
                "motivo_texto": motivos[motivo], "ordem_declarada":
                "custo_por_execucao_concluida desc, concluidas desc, agente asc"}
    vencedor = sorted(
        com_amostra.items(),
        key=lambda kv: (-Decimal(kv[1]["custo_por_execucao_concluida"] or "0"), -kv[1]["concluidas"], kv[0]),
    )[0]
    return {"alvo": "agente_mais_caro_por_execucao_concluida", "vencedor": vencedor[0],
            "custo_por_execucao_concluida": vencedor[1]["custo_por_execucao_concluida"],
            "concluidas": vencedor[1]["concluidas"], "custo_total": vencedor[1]["custo_total"],
            "motivo": None, "motivo_texto": None, "agentes_fora_do_ranking": sorted(set(por_agente) - set(com_amostra)),
            "ordem_declarada": "custo_por_execucao_concluida desc, concluidas desc, agente asc"}


def hash_do_relatorio(relatorio):
    copia = json.loads(json.dumps(relatorio, ensure_ascii=False, sort_keys=True))
    copia.pop("gerado_em", None)
    copia.pop("hash_do_relatorio", None)
    return hashlib.sha256(json.dumps(copia, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def montar_relatorio(contrato, brutas, ambiente, janela, limite_amostra=LIMITE_AMOSTRA_PADRAO,
                     gerado_em=None, sha_contrato=None, caminho_contrato=None):
    """Derivacao PURA (sem banco): recebe as linhas cruas da fonte e devolve o relatorio."""
    linhas = brutas.get("EXECUCOES_DE_AGENTE", [])
    execucoes, lacunas = extrair_execucoes(linhas, contrato)
    por_agente = agrupar(execucoes, "agente", limite_amostra)
    por_modelo = agrupar(execucoes, "modelo", limite_amostra)
    por_workflow = agrupar(execucoes, "workflow", limite_amostra)
    versoes = versoes_por_agente(execucoes)
    for nome, m in por_agente.items():
        m["versoes"] = versoes.get(nome, [])
    resumo = agregar(execucoes, limite_amostra)
    resumo["agentes"] = len(por_agente)
    resumo["modelos"] = len(por_modelo)
    resumo["workflows"] = len(por_workflow)
    relatorio = {
        "card": contrato.get("card"),
        "contrato": {"versao": contrato["versao"],
                     "sha256": sha_contrato or sha256_de_arquivo(caminho_contrato or CONTRATO_PADRAO)},
        "ambiente": ambiente,
        "janela": dict(janela or {"desde": None, "ate": None}),
        "limite_amostra": limite_amostra,
        "veredito": "ANALISADO" if execucoes else "SEM_EXECUCOES",
        "resumo": resumo,
        "por_agente": [dict(m, agente=k) for k, m in sorted(por_agente.items())],
        "por_modelo": [dict(m, modelo=k) for k, m in sorted(por_modelo.items())],
        "por_workflow": [dict(m, workflow=k) for k, m in sorted(por_workflow.items())],
        "ranking": calcular_ranking(por_agente),
        "lacunas": lacunas,
        "fontes": {fid: len(v) for fid, v in sorted(brutas.items())},
        "lacunas_declaradas": contrato.get("lacunas_declaradas", []),
        "gerado_em": gerado_em or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    relatorio["hash_do_relatorio"] = hash_do_relatorio(relatorio)
    return relatorio


def construir_relatorio(contrato, porta_banco, ambiente, desde, ate, limite_amostra,
                        gerado_em=None, sha_contrato=None, caminho_contrato=None):
    if porta_banco is None:
        raise Recusa("PORTA_BANCO_AUSENTE", "sem banco nao ha custo medido: use --planejar/--conferir")
    consultas = montar_consultas(contrato, desde, ate)
    brutas = ler_fontes(porta_banco, consultas)
    return montar_relatorio(contrato, brutas, ambiente, {"desde": desde, "ate": ate},
                            limite_amostra=limite_amostra, gerado_em=gerado_em,
                            sha_contrato=sha_contrato, caminho_contrato=caminho_contrato)


# --------------------------------------------------------------------------------------------
# 5. Saidas (CSV e HTML auto-contido)
# --------------------------------------------------------------------------------------------
COLUNAS_CSV = ("agente", "runs", "concluidas", "falhas", "recusadas", "revisao", "sem_status",
               "taxa_de_falha", "tokens_input", "tokens_output", "tokens_totais", "runs_sem_tokens",
               "custo_total", "runs_sem_custo", "custo_medio_por_execucao", "custo_por_execucao_concluida",
               "latencia_media_s", "latencia_mediana_s", "latencia_p95_s", "organizacoes")


def emitir_csv(relatorio):
    linhas = [";".join(COLUNAS_CSV)]
    for grupo in relatorio["por_agente"]:
        linhas.append(";".join("" if grupo.get(c) is None else str(grupo.get(c)) for c in COLUNAS_CSV))
    return "\n".join(linhas) + "\n"


def emitir_html(relatorio, lacunas_declaradas):
    resumo = relatorio["resumo"]
    linhas = []
    for grupo in relatorio["por_agente"]:
        linhas.append(
            "<tr><td>%s</td><td class=\"n\">%d</td><td class=\"n\">%d</td><td class=\"n\">%d</td>"
            "<td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td>"
            "<td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%d</td></tr>"
            % (_html.escape(str(grupo["agente"])), grupo["runs"], grupo["concluidas"], grupo["falhas"],
               "—" if grupo["taxa_de_falha"] is None else ("%.4f" % grupo["taxa_de_falha"]),
               _html.escape(str(grupo["custo_total"])),
               _html.escape(str(grupo["custo_medio_por_execucao"] or "—")),
               _html.escape(str(grupo["custo_por_execucao_concluida"] or "—")),
               "—" if grupo["latencia_mediana_s"] is None else ("%.2f" % grupo["latencia_mediana_s"]),
               grupo["organizacoes"]))
    ranking = relatorio["ranking"]
    vencedor = ranking["vencedor"] if ranking["vencedor"] else ("— (%s)" % ranking["motivo"])
    gaps = "\n".join("<li>%s</li>" % _html.escape(str(g)) for g in (lacunas_declaradas or []))
    return (
        "<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Custo de agentes — Transformativa Revenue Engine</title>\n<style>\n"
        "body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#111}\n"
        "h1{font-size:20px;margin:0 0 4px 0}\n.meta{color:#555;font-size:12px;margin-bottom:16px}\n"
        "table{border-collapse:collapse;width:100%%;font-size:13px}\n"
        "th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}\n"
        "td.n{text-align:right;font-variant-numeric:tabular-nums}\n"
        ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 18px 0}\n"
        ".card{border:1px solid #ddd;border-radius:6px;padding:8px 12px;min-width:110px}\n"
        ".card b{display:block;font-size:20px}\n.lacunas{font-size:12px;color:#555}\n"
        "h2{font-size:15px}\n</style>\n</head>\n<body>\n"
        "<h1>Custo de agentes — Transformativa Revenue Engine</h1>\n"
        "<div class=\"meta\">card %s · contrato %s (sha256 %s) · ambiente %s · janela %s · "
        "gerado em %s · hash do relatorio %s</div>\n"
        % (_html.escape(str(relatorio.get("card"))), _html.escape(relatorio["contrato"]["versao"]),
           _html.escape(relatorio["contrato"]["sha256"][:16]), _html.escape(relatorio["ambiente"]),
           _html.escape(str(relatorio.get("janela"))),
           _html.escape(str(relatorio.get("gerado_em") or "(sem carimbo)")),
           _html.escape(relatorio["hash_do_relatorio"][:16]))
        + "<div class=\"cards\">"
        + "".join("<div class=\"card\"><b>%s</b>%s</div>" % (_html.escape(str(v)), _html.escape(k))
                  for k, v in [("execuções", resumo["runs"]), ("concluídas", resumo["concluidas"]),
                               ("falhas", resumo["falhas"]), ("recusadas", resumo["recusadas"]),
                               ("custo total", resumo["custo_total"]),
                               ("agentes", resumo["agentes"]), ("organizações", resumo["organizacoes"])])
        + "</div>\n<p><b>Agente mais caro por execução concluída:</b> %s</p>\n"
        % _html.escape(str(vencedor))
        + "<table>\n<thead><tr><th>Agente</th><th>Execuções</th><th>Concluídas</th><th>Falhas</th>"
          "<th>Taxa de falha</th><th>Custo total</th><th>Custo médio</th><th>Custo/concluída</th>"
          "<th>Latência mediana (s)</th><th>Organizações</th></tr></thead>\n<tbody>\n"
        + "\n".join(linhas)
        + "\n</tbody>\n</table>\n<h2>Lacunas declaradas</h2>\n<ul class=\"lacunas\">\n"
        + gaps + "\n</ul>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------------------------
# 6. CLI
# --------------------------------------------------------------------------------------------
def _resumo_texto(relatorio):
    r = relatorio["resumo"]
    partes = ["CUSTO_AGENTES_OK", "veredito=%s" % relatorio["veredito"], "runs=%d" % r["runs"],
              "concluidas=%d" % r["concluidas"], "falhas=%d" % r["falhas"], "recusadas=%d" % r["recusadas"],
              "custo_total=%s" % r["custo_total"], "runs_sem_custo=%d" % r["runs_sem_custo"],
              "agentes=%d" % r["agentes"]]
    if relatorio["ranking"]["vencedor"]:
        partes.append("mais_caro=%s(%s)" % (relatorio["ranking"]["vencedor"],
                                            r["custo_por_execucao_concluida"]))
    else:
        partes.append("mais_caro=nenhum(%s)" % relatorio["ranking"]["motivo"])
    partes.append("hash=%s" % relatorio["hash_do_relatorio"][:16])
    return " ".join(partes)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Custo de agentes v1 — TRE-W8-E05-T01")
    parser.add_argument("--ambiente", required=True, choices=list(AMBIENTES))
    parser.add_argument("--porta-banco", default=os.environ.get("TRE_CUSTO_AGENTES_PORTA_BANCO"))
    parser.add_argument("--desde", default=None)
    parser.add_argument("--ate", default=None)
    parser.add_argument("--limite-amostra", type=int, default=LIMITE_AMOSTRA_PADRAO)
    parser.add_argument("--saida", default=None, help="diretorio de saida do relatorio")
    parser.add_argument("--formato", default="json,csv,html", choices=["json", "csv", "html", "json,csv", "json,html", "csv,html", "json,csv,html"])
    parser.add_argument("--com-carimbo", action="store_true", help="inclui gerado_em no texto do relatorio")
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--planejar", action="store_true", help="imprime o plano declarado (sem banco)")
    parser.add_argument("--conferir", action="store_true", help="valida contrato, DDL e guardas (sem banco)")
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
        if args.limite_amostra < 1:
            raise Recusa("LIMITE_DE_AMOSTRA_INVALIDO", str(args.limite_amostra), codigo=2)
        contrato = carregar_contrato(args.contrato)
        with open(caminho_contrato_de_dados(raiz), "r", encoding="utf-8") as fh:
            contrato_dados = json.load(fh)
        with open(caminho_ddl(raiz), "r", encoding="utf-8") as fh:
            texto_ddl = fh.read()
        validar_contrato(contrato, contrato_dados, texto_ddl)

        if args.conferir:
            colunas = contrato["colunas_de_leitura"]["selecionadas"]
            print("CUSTO_AGENTES_CONFERIR_OK contrato=%s colunas=%d classes=%d ddl=%s" % (
                contrato["versao"], len(colunas),
                len(contrato["vocabulario_status"]["classes"]), TABELA))
            return 0
        if args.planejar:
            print("CUSTO_AGENTES_PLANO versao=%s tabela=%s unidade=%s" % (
                contrato["versao"], TABELA, contrato["unidade"]["execucao"]))
            print("  colunas: %s" % ", ".join(contrato["colunas_de_leitura"]["selecionadas"]))
            print("  proibidas no SELECT: %s" % ", ".join(sorted(contrato["colunas_de_leitura"]["proibidas_no_select"])))
            for classe, valores in contrato["vocabulario_status"]["classes"].items():
                print("  classe %-10s <- %s" % (classe, ", ".join(valores)))
            print("  ranking: %s" % json.dumps(contrato["ranking"], ensure_ascii=False)[:200])
            print("  guardas: %s" % json.dumps(contrato.get("guardrails") or contrato.get("guardas") or {}, ensure_ascii=False)[:200])
            return 0

        validar_ambiente(args.ambiente, args.porta_banco, args.confirmo)
        desde = normalizar_instante(args.desde, "--desde")
        ate = normalizar_instante(args.ate, "--ate")
        relatorio = construir_relatorio(contrato, args.porta_banco, args.ambiente, desde, ate,
                                        args.limite_amostra, caminho_contrato=args.contrato)
        if not args.com_carimbo:
            relatorio.pop("gerado_em", None)  # determinismo: sem carimbo, a saida e' reproduzivel

        token = os.environ.get("TRE_CUSTO_AGENTES_TOKEN")
        saida_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
        saida_csv = emitir_csv(relatorio)
        saida_html = emitir_html(relatorio, contrato.get("lacunas_declaradas"))
        for texto in (saida_json, saida_csv, saida_html):
            if token and token in texto:
                raise Recusa("SENHA_VAZADA", "valor de TRE_CUSTO_AGENTES_TOKEN presente na evidencia", codigo=CODIGO_SEGREDO)

        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "custo-agentes.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "csv" in args.formato:
                with open(os.path.join(args.saida, "custo-agentes.csv"), "w", encoding="utf-8") as fh:
                    fh.write(saida_csv)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "custo-agentes.html"), "w", encoding="utf-8") as fh:
                    fh.write(saida_html)
        print(_resumo_texto(relatorio))
        return 0
    except Recusa as exc:
        print("RECUSA %s %s" % (exc.motivo, exc.detalhe))
        return exc.codigo


if __name__ == "__main__":
    sys.exit(main())
