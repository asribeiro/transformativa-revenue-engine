#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atualizacao do Odoo a partir das respostas v1 (`atualizacao-odoo-respostas-v1`) — card TRE-W6-E06-T01.

O que este componente FAZ (e so isto): le as respostas JA classificadas pelo card TRE-W6-E05-T01 em
`sales_intelligence.interactions`, monta o ATO de CRM correspondente por regra DECLARADA no contrato
versionado (`hermes/agentes/respostas/atualizacao-odoo-respostas-v1.json`) e o executa na API
CONTROLADA do modulo `transformativa_sales_ai` (card TRE-W3-E01-T01, `POST /tf/api/v1/<operacao>`),
gravando a trilha de idempotencia em `sync_events`.

Invariantes deste componente (cada um com item de aceite):

  1. NAO CLASSIFICA E NAO LE A CAIXA: a materia-prima e' a linha de `interactions` que o card
     anterior gravou. Nada de IMAP, nada de LLM, nada de heuristica sobre texto.
  2. ATO DECLARADO, NUNCA INVENTADO: qual categoria gera qual evento/proxima acao/atividade, e em
     quais campos, esta' no contrato. Categoria fora do vocabulario fechado RECUSA a rodada
     (`CONTRATO_INVALIDO`, exit 3). Nenhum campo e' escrito sem estar declarado na politica da API
     (v1.3.0): o que a politica nao declara vira LACUNA DECLARADA, nunca escrita por fora.
  3. SO A PORTA CONTROLADA: todo acesso ao Odoo e' `POST /tf/api/v1/<operacao>` com chave bearer,
     `idempotency_key` (escrita), `correlation_id` e `dry_run`. Nao existe XML-RPC/JSON-RPC direto,
     nao existe SQL no banco do Odoo.
  4. LIMITE DE ESCRITA NO BANCO CANONICO: so `sync_events` e' tocada, e so por INSERT (trilha
     append). UPDATE/DELETE/TRUNCATE/DDL nao existem no modulo — a auditoria de fonte procura esses
     comandos e RECUSA antes de qualquer conexao (`DDL_NO_CODIGO`, exit 3).
  5. VINCULO NAO SE INVENTA: sem `odoo_lead_id` (ou, quando a categoria exige contato, sem
     `contato.odoo_partner_id`), a resposta NAO vira escrita: a linha fica registrada em
     `sync_events` com `status = SEM_VINCULO` (auditavel, elegivel a reprocesso).
  6. IDEMPOTENCIA: a chave e' `odoo-resposta:<interaction_id>` (indice unico em `sync_events`).
     Replay devolve `JA_ATUALIZADO`, nao grava de novo e **nao chama a API**.
  7. SEGREDO: a chave da API so por `TRE_ODOO_API_KEY`; ela nunca entra em stdout, relatorio, trilha
     ou payload auditado; a gravacao confere e RECUSA se o valor aparecer (`SENHA_VAZADA`, exit 5).
  8. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige API em LOOPBACK
     (`HOST_NAO_E_DEV`) e porta de banco LOCAL (container de dev/aceite via `docker exec` —
     `BANCO_NAO_E_DEV`); `homolog` exige aprovacao humana registrada
     (`TRE_ODOO_RESPOSTAS_APROVACAO_HUMANA`); `prod` RECUSA por desenho (exit 4).

O que ele NAO faz, por desenho (declarado no contrato -> `lacunas`):
  - **nao marca supressao** (`OPT_OUT`) em campo do Odoo: a politica da API v1.3.0 nao declara campo
    de supressao; o evento e a atividade sao registrados e a lacuna fica nomeada, com encaminhamento;
  - **nao responde nem envia e-mail** (W6-E04) e **nao decide nada humano** (W6-E03);
  - **nao escreve no `sales_intelligence`** alem da trilha, e nao altera `interactions`;
  - **nao usa LLM**.

Uso (dev nao tem chave de API do Odoo real: a prova em dev e' contra stub loopback; contra o Odoo do
dev e' no E2E W6-E07, com chave do usuario de integracao):

  python3 hermes/agentes/respostas/atualizacao_odoo.py --planejar
  python3 hermes/agentes/respostas/atualizacao_odoo.py --conferir
  python3 hermes/agentes/respostas/atualizacao_odoo.py --regras
  python3 hermes/agentes/respostas/atualizacao_odoo.py --propagar --porta-banco "docker exec -i pg-e06-acc psql -U sales_ai -d sales_intelligence" [--confirmo]
  python3 hermes/agentes/respostas/atualizacao_odoo.py --desfazer "odoo-resposta:<uuid>" [--confirmo]

Exit: 0 = OK/DRY_RUN/replay · 1 = falha de execucao (HTTP/banco) · 2 = uso · 3 = recusa de
guarda/contrato/fonte · 4 = recusa de producao · 5 = segredo vazado (recusa de gravacao).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", "..", ".."))

VERSAO = "atualizacao-odoo-respostas-v1"
CONTRATO_PADRAO = os.path.join(AQUI, "atualizacao-odoo-respostas-v1.json")

CODIGO_OK = 0
CODIGO_FALHA = 1
CODIGO_USO = 2
CODIGO_RECUSA = 3
CODIGO_PRODUCAO = 4
CODIGO_SEGREDO = 5

AMBIENTES = ("dev", "homolog", "prod")
TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_SYNC = "sales_intelligence.sync_events"
PREFIXO_CHAVE = "odoo-resposta:"

# Prefixos ACEITOS para escrever no banco canonico (invariante 8): em dev a escrita so pode cair em
# container local de dev/aceite. Prefixo remoto (ssh, host externo) e RECUSADO por medicao.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(sales|odoo|resp|respostas|e06|aceite)[A-Za-z0-9._-]*$")


def mascarar(valor: str | None) -> str:
    if not valor:
        return ""
    return valor[:2] + "*" * max(0, len(valor) - 2) if len(valor) > 6 else "<oculta>"


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def loopback(host: str) -> bool:
    return (host or "").strip().lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


class Recusa(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe


# --------------------------------------------------------------------------------------------
# 1. Fonte: invariantes verificaveis no proprio arquivo
# --------------------------------------------------------------------------------------------
def padroes_de_escrita_proibida() -> dict:
    """Padroes montados em tempo de execucao (literal nao se encontra a si mesmo)."""
    return {
        "sql_de_escrita_crua": ["DE" + "LETE FROM", "UP" + "DATE ", "TRUN" + "CATE", "DR" + "OP ",
                                "AL" + "TER TABLE"],
        "acesso_direto_ao_odoo": ["xmlrp" + "c", "json" + "rpc", "/web/" + "dataset"],
    }


def auditar_fonte(caminho: str | None = None) -> list:
    """Auditoria da propria fonte (invariantes 3 e 4). Devolve a lista de violacoes."""
    alvo = caminho or os.path.abspath(__file__)
    with open(alvo, "r", encoding="utf-8") as fh:
        linhas = fh.readlines()
    texto_util = []
    dentro_doc = False
    for linha in linhas:
        t = linha.strip()
        # Contagem (nao "startswith") dos delimitadores de docstring: docstring de UMA linha tem DOIS
        # delimitadores na MESMA linha e a versao anterior (`startswith`) tratava esse caso como
        # abertura, deixando `dentro_doc` ligado e ESCONDENDO o codigo seguinte — o proprio item 9 da
        # suite mede isso: com o padrao `startswith`, um `DELETE FROM` injetado logo depois de uma
        # docstring de uma linha passava sem acusar (check morto).
        aspas = t.count('"""') + t.count("'''")
        if aspas % 2 == 1:
            dentro_doc = not dentro_doc
            continue
        if dentro_doc or aspas >= 2 or t.startswith("#"):
            continue
        texto_util.append(linha)
    corpo = "".join(texto_util)
    violacoes = []
    for nome, padroes in padroes_de_escrita_proibida().items():
        for padrao in padroes:
            if padrao in corpo:
                violacoes.append({"padrao": nome, "alvo": padrao})
    if "/tf/api/v1/" not in corpo:
        violacoes.append({"padrao": "porta_controlada",
                          "alvo": "o modulo nao aponta para a rota declarada POST /tf/api/v1/<operacao>"})
    if "INSERT INTO" not in corpo:
        violacoes.append({"padrao": "trilha_por_insert",
                          "alvo": "a trilha de idempotencia nao e' gravada por INSERT"})
    return violacoes


# --------------------------------------------------------------------------------------------
# 2. Configuracao e guardas de ambiente
# --------------------------------------------------------------------------------------------
class Configuracao:
    def __init__(self, env: dict):
        self.ambiente = (env.get("TRE_AMBIENTE") or "dev").strip().lower()
        self.url = (env.get("TRE_ODOO_API_URL") or "").strip()
        self.chave = env.get("TRE_ODOO_API_KEY") or ""
        self.porta_banco = (env.get("TRE_ODOO_RESPOSTAS_PORTA_BANCO") or "").strip()
        self.aprovacao = (env.get("TRE_ODOO_RESPOSTAS_APROVACAO_HUMANA") or "").strip()
        self.tipo_atividade = (env.get("TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA") or "").strip()
        self.usuario_atividade = (env.get("TRE_ODOO_USUARIO_ATIVIDADE") or "").strip()
        self.correlacao = (env.get("TRE_ODOO_RESPOSTAS_CORRELACAO") or "").strip()
        self.timeout = (env.get("TRE_ODOO_RESPOSTAS_TIMEOUT") or "15").strip()
        self.limite = (env.get("TRE_ODOO_RESPOSTAS_LIMITE") or "50").strip()
        self.rotulo_de_rodada = (env.get("TRE_ODOO_RESPOSTAS_RODADA") or "rodada").strip()

    def resumo(self) -> dict:
        return {
            "versao": VERSAO,
            "ambiente": self.ambiente,
            "api": {"url": self.url or "(ausente)", "chave": mascarar(self.chave),
                    "tipo_atividade": self.tipo_atividade or "(ausente)",
                    "usuario_atividade": self.usuario_atividade or "(vazio: atividade sem responsavel)",
                    "timeout": self.timeout},
            "porta_banco": self.porta_banco or "(ausente)",
            "aprovacao_humana": self.aprovacao or "(ausente)",
            "correlacao": self.correlacao or "(gerada por resposta)",
            "limite": self.limite,
        }


def validar(config: Configuracao) -> list:
    """Completude + guardas de ambiente (ADR-005). Devolve a lista de problemas."""
    problemas = []
    if config.ambiente not in AMBIENTES:
        problemas.append(("AMBIENTE_INVALIDO", f"ambiente '{config.ambiente}' fora de {AMBIENTES}"))
        return problemas
    if config.ambiente == "prod":
        problemas.append(("PRODUCAO_RECUSADA",
                          "prod recusa por desenho (ADR-005): a promocao e' ato humano registrado"))
        return problemas
    faltantes = [nome for nome, valor in (("TRE_ODOO_API_URL", config.url),
                                          ("TRE_ODOO_API_KEY", config.chave)) if not valor]
    if faltantes:
        problemas.append(("CONFIG_INCOMPLETA", "faltam: " + ", ".join(faltantes)))
    if config.url:
        partes = urllib.parse.urlsplit(config.url)
        if partes.scheme not in ("http", "https") or not partes.netloc:
            problemas.append(("CONFIG_INCOERENTE", f"TRE_ODOO_API_URL nao e' http(s) valida: {config.url}"))
    if config.timeout and not config.timeout.isdigit():
        problemas.append(("CONFIG_INCOERENTE", f"timeout nao numerico: {config.timeout}"))
    if config.limite and not config.limite.isdigit():
        problemas.append(("CONFIG_INCOERENTE", f"limite nao numerico: {config.limite}"))
    if config.tipo_atividade and not config.tipo_atividade.isdigit():
        problemas.append(("CONFIG_INCOERENTE",
                          f"TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA nao e' id numerico: {config.tipo_atividade}"))
    if config.ambiente == "dev":
        if config.url and not loopback(urllib.parse.urlsplit(config.url).hostname or ""):
            problemas.append(("HOST_NAO_E_DEV",
                              "dev so fala com API em loopback (dev nao tem chave de API do Odoo); "
                              f"host={urllib.parse.urlsplit(config.url).hostname}"))
        if not config.porta_banco:
            problemas.append(("BANCO_NAO_DECLARADO",
                              "dev exige --porta-banco/TRE_ODOO_RESPOSTAS_PORTA_BANCO com container local"))
        else:
            m = RE_CONTAINER_LOCAL.match(config.porta_banco.strip())
            if not m or not CONTAINERS_LOCAIS_DEV.match(m.group(1)):
                problemas.append(("BANCO_NAO_E_DEV",
                                  "dev escreve apenas em container local de dev/aceite via docker exec; "
                                  f"prefixo recusado: {config.porta_banco}"))
    if config.ambiente == "homolog":
        if not config.aprovacao:
            problemas.append(("HOMOLOG_SEM_APROVACAO",
                              "homolog exige TRE_ODOO_RESPOSTAS_APROVACAO_HUMANA registrada"))
    return problemas


# --------------------------------------------------------------------------------------------
# 3. Contrato — ato declarado, nunca inventado
# --------------------------------------------------------------------------------------------
def carregar_contrato(caminho: str | None = None) -> dict:
    alvo = caminho or CONTRATO_PADRAO
    if not os.path.isfile(alvo):
        raise Recusa("CONTRATO_AUSENTE", f"contrato nao encontrado: {alvo}")
    try:
        with open(alvo, "r", encoding="utf-8") as fh:
            contrato = json.load(fh)
    except json.JSONDecodeError as e:
        raise Recusa("CONTRATO_INVALIDO", f"JSON invalido: {e}") from e
    falhas = validar_contrato(contrato)
    if falhas:
        raise Recusa("CONTRATO_INVALIDO", "; ".join(falhas))
    return contrato


def validar_contrato(contrato: dict) -> list:
    falhas = []
    vocab = contrato.get("vocabulario_fechado")
    if not isinstance(vocab, list) or not vocab:
        falhas.append("vocabulario_fechado ausente/vazio")
        vocab = []
    elif len(set(vocab)) != len(vocab):
        falhas.append("vocabulario_fechado com repeticao")
    atos = contrato.get("atos_por_categoria")
    if not isinstance(atos, list) or not atos:
        falhas.append("atos_por_categoria ausente/vazio")
        return falhas
    operacoes = (contrato.get("api_controlada") or {}).get("operacoes") or {}
    if not operacoes:
        falhas.append("api_controlada.operacoes ausente")
    campos = contrato.get("campos_por_operacao") or {}
    vistas = set()
    ordens = set()
    for i, ato in enumerate(atos):
        if not isinstance(ato, dict):
            falhas.append(f"ato {i} nao e' objeto")
            continue
        cat = ato.get("categoria")
        if cat not in vocab:
            falhas.append(f"ato {i} categoria fora do vocabulario: {cat}")
        if not isinstance(ato.get("ordem"), int):
            falhas.append(f"ato {i} sem ordem inteira")
        elif ato["ordem"] in ordens:
            falhas.append(f"ato {i} ordem repetida: {ato['ordem']}")
        else:
            ordens.add(ato["ordem"])
        for chave in ("evento", "proxima_acao", "atividade"):
            if not str(ato.get(chave) or "").strip():
                falhas.append(f"ato {i} ({cat}) sem {chave} declarado")
        if not isinstance(ato.get("prazo_dias"), int) or ato["prazo_dias"] < 0:
            falhas.append(f"ato {i} ({cat}) prazo_dias invalido")
        vistas.add(cat)
    for j, cat in enumerate(contrato.get("categorias_sem_ato") or []):
        if cat not in vocab:
            falhas.append(f"categorias_sem_ato[{j}] fora do vocabulario: {cat}")
        elif cat in vistas:
            falhas.append(f"categoria {cat} tem ato E esta' em categorias_sem_ato (ambiguidade)")
    # Toda categoria do vocabulario tem de estar classificada (ato OU sem ato): categoria sem lugar
    # e' a que cairia num ato generico no codigo — exatamente o que este contrato proibe.
    sem_lugar = [c for c in vocab if c not in vistas and c not in set(contrato.get("categorias_sem_ato") or [])]
    if sem_lugar:
        falhas.append(f"categorias do vocabulario sem ato nem exclusao: {sem_lugar}")
    # Os campos que o codigo escreve tem de estar declarados para a operacao que os escreve.
    for operacao in ("oportunidade_upsert", "atividade_criar", "crm_registros_ler"):
        declaracao = campos.get(operacao)
        if not isinstance(declaracao, dict):
            falhas.append(f"campos_por_operacao.{operacao} ausente")
            continue
        if operacao not in operacoes:
            falhas.append(f"{operacao} usada pelo componente e ausente de api_controlada.operacoes")
        for campo in declaracao.get("campos_escritos") or []:
            if campo not in (declaracao.get("campos") or []):
                falhas.append(f"{operacao}: campo escrito '{campo}' fora dos campos declarados")
    return falhas


def ato_da_categoria(categoria: str, contrato: dict) -> dict | None:
    """Devolve o ato declarado da categoria, ou None quando a categoria nao gera ato."""
    for ato in sorted(contrato["atos_por_categoria"], key=lambda a: a["ordem"]):
        if ato["categoria"] == categoria:
            return ato
    return None


# --------------------------------------------------------------------------------------------
# 4. Porta de banco (comando declarado, SQL por stdin, resposta JSON) — so SELECT e INSERT
# --------------------------------------------------------------------------------------------
class PortaBanco:
    def __init__(self, prefixo: str, ambiente: str):
        self.prefixo = prefixo
        self.ambiente = ambiente

    def _argumentos(self) -> list:
        try:
            return shlex.split(self.prefixo)
        except ValueError as e:
            raise Recusa("BANCO_NAO_DECLARADO", f"prefixo de banco invalido: {e}") from e

    def _envelope(self, expressao: str) -> str:
        """Envelope de leitura: md5 + base64 do payload.

        O psql QUEBRA a linha quando o valor e' longo (medido no aceite: `json_agg` de 5 linhas voltou
        em 5 linhas de saida), entao ler "a ultima linha" ja' quebrou uma rodada de verdade. O envelope
        resolve em duas partes: base64 nao tem espaco nem quebra significativa (entao juntar as linhas
        reconstroi o valor) e o md5 PROVA que a juncao foi exata — divergencia RECUSA, nunca segue com
        dado pela metade.
        """
        return ("WITH dados AS (SELECT " + expressao + "::text AS payload) "
                "SELECT md5(payload) || ' ' || encode(convert_to(payload, 'UTF8'), 'base64') FROM dados;")

    def _decodificar(self, brutos: list) -> list:
        linhas = [l for l in brutos if l.strip()]
        if not linhas:
            raise Recusa("BANCO_RESPOSTA_VAZIA", "a porta de banco nao devolveu payload")
        texto = "".join(l.strip() for l in linhas).strip()
        partes = texto.split(" ", 1)
        if len(partes) != 2:
            raise Recusa("BANCO_RESPOSTA_INVALIDA",
                         f"a porta de banco nao devolveu o envelope esperado: {texto[:200]}")
        digest, b64 = partes
        try:
            dados = base64.b64decode(b64, validate=True)
        except Exception as e:  # noqa: BLE001 — qualquer falha de base64 e' envelope invalido
            raise Recusa("BANCO_RESPOSTA_INVALIDA", f"payload base64 invalido: {e}") from e
        if hashlib.md5(dados).hexdigest() != digest:
            raise Recusa("BANCO_RESPOSTA_CORROMPIDA",
                         "o md5 do payload nao confere (resposta truncada ou costurada)")
        try:
            return json.loads(dados.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise Recusa("BANCO_RESPOSTA_INVALIDA", f"payload nao e' JSON: {e}") from e

    def consultar(self, sql: str) -> list:
        expressao = f"coalesce(json_agg(t), '[]'::json) FROM ({sql}) t"
        comando = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c",
                                        self._envelope(f"SELECT {expressao}")]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        return self._decodificar((proc.stdout or "").splitlines())

    def executar(self, sql: str) -> list:
        """Escreve (INSERT ... RETURNING) e devolve as linhas afetadas, pelo mesmo envelope."""
        comando_base = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c"]
        # INSERT sem RETURNING nao pode ser lido por CTE ("WITH query does not have a RETURNING clause",
        # defeito medido na rodada 2 do aceite do card irmao): sem RETURNING nao ha linhas a devolver.
        if "RETURNING" in sql.upper():
            expressao = f"coalesce(json_agg(afetados), '[]'::json) FROM (WITH afetados AS ({sql}) SELECT * FROM afetados) afetados"
            comando = comando_base + [self._envelope(f"SELECT {expressao}")]
        else:
            comando = comando_base + [sql]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        if "RETURNING" not in sql.upper():
            return []
        return self._decodificar((proc.stdout or "").splitlines())


def chave_de(interacao: dict) -> str:
    return PREFIXO_CHAVE + str(interacao.get("interaction_id") or interacao.get("id"))


def ja_propagado(porta: PortaBanco, chave: str) -> dict | None:
    linhas = porta.consultar(
        f"SELECT id::text, status, entity_id::text FROM {TABELA_SYNC} "
        f"WHERE idempotency_key = '{chave.replace(chr(39), chr(39) * 2)}' LIMIT 1")
    return linhas[0] if linhas else None


def ler_interacoes(porta: PortaBanco, limite: int) -> list:
    """Le as respostas classificadas com o vinculo de CRM que elas ja' tem. Nao cria nada."""
    sql = (
        f"SELECT i.id::text AS interaction_id, i.response_category, i.intent, i.sentiment, "
        f"i.subject, i.odoo_lead_id, c.id::text AS contact_id, c.odoo_partner_id AS contato_odoo_id, "
        f"c.email, c.full_name, o.odoo_partner_id AS empresa_odoo_id, "
        f"coalesce(o.trade_name, o.legal_name) AS empresa "
        f"FROM {TABELA_INTERACOES} i "
        f"LEFT JOIN sales_intelligence.contacts c ON c.id = i.contact_id "
        f"LEFT JOIN sales_intelligence.organizations o ON o.id = i.organization_id "
        f"WHERE i.response_category IS NOT NULL AND i.direction = 'INBOUND' "
        f"ORDER BY i.occurred_at LIMIT {int(limite)}")
    return porta.consultar(sql)


def gravar_trilha(porta: PortaBanco, chave: str, interacao: dict, status: str, operacao: str,
                  payload: dict, erro: str | None = None) -> None:
    def q(valor):
        if valor is None:
            return "NULL"
        return "'" + str(valor).replace("'", "''") + "'"

    sql = (f"INSERT INTO {TABELA_SYNC} (id, entity_type, entity_id, source_system, target_system, "
           f"operation, source_version, idempotency_key, status, request_payload, error_message, "
           f"completed_at) VALUES (gen_random_uuid(), 'interaction', {q(interacao.get('interaction_id'))}, "
           f"'sales_intelligence', 'odoo', {q(operacao)}, {q(VERSAO)}, {q(chave)}, {q(status)}, "
           f"{q(json.dumps(payload, ensure_ascii=False, sort_keys=True))}, {q(erro)}, NOW())")
    porta.executar(sql)


# --------------------------------------------------------------------------------------------
# 5. Porta controlada do Odoo (POST /tf/api/v1/<operacao>)
# --------------------------------------------------------------------------------------------
class ClienteApi:
    def __init__(self, config: Configuracao):
        self.config = config
        self.chamadas = []
        self.correlation_id = config.correlacao

    def capacidade(self) -> dict:
        return self.pedir("sistema_capacidades", {}, chave=None, dry_run=False)

    def pedir(self, operacao: str, parametros: dict, chave: str | None, dry_run: bool,
              correlation_id: str | None = None) -> dict:
        corpo = {"parametros": parametros, "dry_run": bool(dry_run)}
        if chave:
            corpo["idempotency_key"] = chave
        corr = correlation_id or self.correlation_id
        if corr:
            corpo["correlation_id"] = corr
        url = (self.config.url.rstrip("/") + "/tf/api/v1/" + urllib.parse.quote(operacao)
               + "?tf.api.ambiente=" + urllib.parse.quote(self.config.ambiente))
        if self.config.aprovacao:
            url += "&tf.api.aprovacao=" + urllib.parse.quote(self.config.aprovacao)
        bruto = json.dumps(corpo).encode("utf-8")
        requisicao = urllib.request.Request(url, data=bruto, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.config.chave,
        })
        registro = {"operacao": operacao, "idempotency_key": chave or "",
                    "dry_run": bool(dry_run), "url": urllib.parse.urlsplit(url).path}
        try:
            with urllib.request.urlopen(requisicao, timeout=int(self.config.timeout or 15)) as resp:
                texto = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            corpo_erro = e.read().decode("utf-8", "replace")
            registro["http"] = e.code
            registro["erro"] = "HTTP " + str(e.code)
            self.chamadas.append(registro)
            raise Recusa("API_RECUSOU", f"{operacao}: HTTP {e.code} — {corpo_erro[:300]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            registro["erro"] = f"sem resposta ({e.__class__.__name__})"
            self.chamadas.append(registro)
            raise Recusa("API_INDISPONIVEL", f"{operacao}: sem resposta — {e}") from e
        try:
            resposta = json.loads(texto)
        except json.JSONDecodeError as e:
            registro["erro"] = "resposta nao e' JSON"
            self.chamadas.append(registro)
            raise Recusa("RESPOSTA_FORA_DO_ENVELOPE",
                         f"{operacao}: resposta nao e' JSON ({texto[:200]})") from e
        for campo in ("ok", "operacao", "correlation_id", "dados"):
            if campo not in resposta:
                registro["erro"] = f"envelope sem {campo}"
                self.chamadas.append(registro)
                raise Recusa("RESPOSTA_FORA_DO_ENVELOPE",
                             f"{operacao}: envelope sem o campo '{campo}' ({sorted(resposta)[:8]})")
        if resposta.get("ok") is not True:
            registro["http"] = 200
            registro["erro"] = str(resposta.get("codigo") or "recusado pela API")
            self.chamadas.append(registro)
            raise Recusa("API_RECUSOU", f"{operacao}: {resposta.get('codigo')} — "
                                        f"{str(resposta.get('mensagem'))[:200]}")
        registro["http"] = 200
        registro["acao_efetiva"] = (resposta.get("dados") or {}).get("acao_efetiva")
        self.chamadas.append(registro)
        return resposta


# --------------------------------------------------------------------------------------------
# 6. Plano: puro, sem rede e sem banco (exercitavel nos dentes)
# --------------------------------------------------------------------------------------------
def resumir_assunto(assunto: str | None, limite: int = 120) -> str:
    texto = re.sub(r"\s+", " ", (assunto or "")).strip()
    return texto[:limite] if texto else "(sem assunto)"


def montar_plano(interacao: dict, contrato: dict, tipo_atividade: str = "",
                 usuario_atividade: str = "", correlacao: str = "", quando: str | None = None) -> dict:
    """Monta o plano de UM interaction a partir do contrato. Puro: nao chama API nem banco.

    Devolve {"status": ..., "atos": [ {"operacao","parametros","escrita"} ... ], "motivo": ...}.
    """
    categoria = interacao.get("response_category")
    ato = ato_da_categoria(categoria, contrato)
    limite = int((contrato.get("limites") or {}).get("tamanho_maximo_assunto") or 120)
    if ato is None:
        return {"status": "SEM_ATO", "atos": [], "motivo": f"categoria {categoria} declarada sem ato de CRM",
                "categoria": categoria}
    if not interacao.get("odoo_lead_id"):
        return {"status": "SEM_VINCULO", "atos": [],
                "motivo": "a resposta nao tem lead no CRM (interactions.odoo_lead_id vazio)",
                "categoria": categoria}
    if ato.get("exige_contato") and not (interacao.get("contato_odoo_id") or interacao.get("email")):
        return {"status": "SEM_VINCULO", "atos": [],
                "motivo": "a categoria exige contato e nem odoo_partner_id nem email estao presentes",
                "categoria": categoria}
    campos = contrato["campos_por_operacao"]
    evento = ato["evento"]
    base_chave = f"w6-e06:{interacao['interaction_id']}"
    chave_lead = f"{base_chave}:lead"
    dados = {"interaction_id": interacao["interaction_id"], "categoria": categoria,
             "evento": evento, "proxima_acao": ato["proxima_acao"]}
    atos = [{
        "operacao": "crm_registros_ler",
        "escrita": False,
        "parametros": {
            "modelo": campos["crm_registros_ler"]["modelo"],
            "filtro": [["id", "=", int(interacao["odoo_lead_id"])]],
            "campos": campos["crm_registros_ler"]["campos"],
            "limite": 1,
        },
        "_papel": "resolver o lead e a identidade canonica (tf_opportunity_id) antes de escrever",
    }, {
        "operacao": "oportunidade_upsert",
        "escrita": True,
        "idempotency_key": chave_lead,
        "parametros": {"modelo": campos["oportunidade_upsert"]["modelo"], "valores": {
            "tf_last_event_type": evento,
            "tf_next_best_action": ato["proxima_acao"],
            "tf_last_sync_at": quando or agora(),
            "tf_correlation_id": correlacao or chave_lead,
            "tf_idempotency_key": chave_lead,
        }},
        "_papel": "registrar o evento da resposta e a proxima acao no lead espelhado",
        "_depende_de": "crm_registros_ler",
    }]
    if ato["atividade"]:
        if not tipo_atividade:
            return {"status": "SEM_CONFIG_DE_ATIVIDADE", "atos": [],
                    "motivo": "o ato exige atividade e TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA nao esta' declarado "
                              "(dado de instancia, por ambiente; fail-closed)",
                    "categoria": categoria, "atos_descartados": atos}
        res_id = interacao.get("contato_odoo_id") or interacao.get("empresa_odoo_id")
        if not res_id:
            return {"status": "SEM_VINCULO", "atos": [],
                    "motivo": "o ato exige atividade em contato e nao ha odoo_partner_id; "
                              "(res_model da atividade e' fixo em res.partner na politica v1.3.0)",
                    "categoria": categoria}
        resumo = (contrato["textos"]["resumo_atividade"]
                  .replace("{categoria}", str(categoria))
                  .replace("{assunto}", resumir_assunto(interacao.get("subject"), limite)))
        valores_atividade = {
            "res_id": int(res_id),
            "activity_type_id": int(tipo_atividade),
            "summary": resumo,
            "date_deadline": (datetime.now(timezone.utc)
                              + timedelta(days=int(ato["prazo_dias"]))).strftime("%Y-%m-%d"),
            "tf_correlation_id": correlacao or chave_lead,
            "tf_idempotency_key": f"{base_chave}:atividade",
        }
        if usuario_atividade:
            valores_atividade["user_id"] = int(usuario_atividade)
        atos.append({
            "operacao": "atividade_criar",
            "escrita": True,
            "idempotency_key": f"{base_chave}:atividade",
            "parametros": {"modelo": campos["atividade_criar"]["modelo"], "valores": valores_atividade},
            "_papel": "abrir a atividade de resposta no contato",
        })
    return {"status": "PRONTO", "atos": atos, "categoria": categoria, "dados": dados,
            "motivo": ato.get("descricao", "")}


def executar_plano(cliente: ClienteApi, plano: dict, dry_run: bool) -> dict:
    """Executa os atos do plano NA ORDEM. O lead resolvido alimenta a escrita seguinte."""
    resultados = []
    lead = None
    for ato in plano["atos"]:
        parametros = json.loads(json.dumps(ato["parametros"]))
        if ato["operacao"] == "oportunidade_upsert":
            if not lead:
                return {"status": "LEAD_NAO_ENCONTRADO", "resultados": resultados,
                        "motivo": "a leitura do lead nao devolveu registros"}
            identidade = lead.get("tf_opportunity_id")
            if not identidade:
                return {"status": "SEM_IDENTIDADE_CANONICA", "resultados": resultados,
                        "motivo": "o lead nao tem tf_opportunity_id: sem identidade declarada "
                                  "a API nao tem como fazer upsert (nada foi escrito)"}
            parametros["valores"]["tf_opportunity_id"] = identidade
            parametros["valores"]["name"] = lead.get("name") or "Oportunidade sem nome"
        resposta = cliente.pedir(ato["operacao"], parametros,
                                 chave=ato.get("idempotency_key"), dry_run=dry_run)
        dados = resposta.get("dados") or {}
        if ato["operacao"] == "crm_registros_ler":
            registros = dados.get("registros") or []
            lead = registros[0] if registros else None
        resultados.append({"operacao": ato["operacao"], "escrita": ato["escrita"],
                           "acao_efetiva": dados.get("acao_efetiva"),
                           "ids": dados.get("ids") or ([dados.get("id")] if dados.get("id") else []),
                           "dry_run": bool(resposta.get("dry_run"))})
    return {"status": "OK", "resultados": resultados}


# --------------------------------------------------------------------------------------------
# 7. Relatorio/trilha local com checagem fail-closed de segredo
# --------------------------------------------------------------------------------------------
class Saida:
    def __init__(self, relatorio: str | None, trilha: str | None, segredo: str | None):
        self.relatorio = {"versao": VERSAO, "quando": agora(), "eventos": []}
        self.caminho_relatorio = relatorio
        self.caminho_trilha = trilha
        self.segredo = segredo

    def evento(self, **campos) -> None:
        campos.setdefault("quando", agora())
        self.relatorio["eventos"].append(campos)
        print(json.dumps(campos, ensure_ascii=False, sort_keys=True))
        if self.caminho_trilha:
            self._gravar(self.caminho_trilha, campos)

    def _conferir(self, texto: str) -> None:
        if self.segredo and len(self.segredo) >= 4 and self.segredo in texto:
            print(json.dumps({"evento": "SENHA_VAZADA", "motivo": "SENHA_VAZADA",
                              "detalhe": "o valor de TRE_ODOO_API_KEY apareceu na gravacao; "
                                         "gravacao recusada (exit 5)"}, ensure_ascii=False))
            raise SystemExit(CODIGO_SEGREDO)

    def _gravar(self, caminho: str, dados: dict) -> None:
        texto = json.dumps(dados, ensure_ascii=False, sort_keys=True)
        self._conferir(texto)
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write(texto + "\n")

    def fechar(self, veredito: str) -> None:
        self.relatorio["veredito"] = veredito
        texto = json.dumps(self.relatorio, ensure_ascii=False, sort_keys=True, indent=2)
        self._conferir(texto)
        print(f"# veredito: {veredito}")
        if self.caminho_relatorio:
            os.makedirs(os.path.dirname(os.path.abspath(self.caminho_relatorio)), exist_ok=True)
            with open(self.caminho_relatorio, "w", encoding="utf-8") as fh:
                fh.write(texto + "\n")
            print(f"# relatorio: {self.caminho_relatorio}")


# --------------------------------------------------------------------------------------------
# 8. CLI
# --------------------------------------------------------------------------------------------
def ler_env_file(caminho: str | None) -> dict:
    env = {}
    if not caminho:
        return env
    if not os.path.isfile(caminho):
        raise Recusa("ENV_AUSENTE", f"env-file nao encontrado: {caminho}")
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            env[chave.strip()] = valor.strip().strip('"').strip("'")
    return env


def montar_config(args) -> Configuracao:
    env = {}
    if args.env_file:
        env.update(ler_env_file(args.env_file))
    env.update({k: v for k, v in os.environ.items() if k.startswith("TRE_")})
    if args.ambiente:
        env["TRE_AMBIENTE"] = args.ambiente
    if args.porta_banco:
        env["TRE_ODOO_RESPOSTAS_PORTA_BANCO"] = args.porta_banco
    if args.url:
        env["TRE_ODOO_API_URL"] = args.url
    if args.limite:
        env["TRE_ODOO_RESPOSTAS_LIMITE"] = str(args.limite)
    return Configuracao(env)


def propagar(config: Configuracao, contrato: dict, saida: Saida, confirmo: bool,
             porta_banco: str | None) -> int:
    problemas = validar(config)
    if problemas:
        for motivo, detalhe in problemas:
            saida.evento(evento="recusa", motivo=motivo, detalhe=detalhe)
        saida.fechar("RECUSADO")
        return CODIGO_PRODUCAO if any(m == "PRODUCAO_RECUSADA" for m, _ in problemas) else CODIGO_RECUSA
    violacoes = auditar_fonte()
    if violacoes:
        saida.evento(evento="recusa", motivo="DDL_NO_CODIGO", detalhe=str(violacoes))
        saida.fechar("RECUSADO")
        return CODIGO_RECUSA
    porta = PortaBanco(config.porta_banco, config.ambiente)
    cliente = ClienteApi(config)
    limite = int(config.limite or 50)
    if not confirmo:
        saida.evento(evento="DRY_RUN", detalhe="sem --confirmo nada e' escrito na API nem na trilha")
    interacoes = ler_interacoes(porta, limite)
    contagem = {"lidas": len(interacoes), "propagadas": 0, "replay": 0, "sem_ato": 0,
                "sem_vinculo": 0, "sem_config": 0, "falhas": 0, "dry_run": 0}
    for interacao in interacoes:
        chave = chave_de(interacao)
        anterior = ja_propagado(porta, chave)
        if anterior and anterior.get("status") in ("ATUALIZADO", "ATUALIZADO_PARCIAL", "DRY_RUN"):
            contagem["replay"] += 1
            saida.evento(evento="JA_ATUALIZADO", interaction_id=interacao["interaction_id"],
                         chave=chave, status=anterior.get("status"),
                         detalhe="replay: nenhuma chamada a API")
            continue
        plano = montar_plano(interacao, contrato, config.tipo_atividade, config.usuario_atividade,
                             config.correlacao)
        if plano["status"] == "SEM_ATO":
            contagem["sem_ato"] += 1
            saida.evento(evento="SEM_ATO", interaction_id=interacao["interaction_id"],
                         categoria=plano.get("categoria"), motivo=plano["motivo"])
            if confirmo:
                gravar_trilha(porta, chave, interacao, "SEM_ATO", "NENHUMA", {"motivo": plano["motivo"]})
            continue
        if plano["status"] in ("SEM_VINCULO", "SEM_CONFIG_DE_ATIVIDADE"):
            contagem["sem_vinculo" if plano["status"] == "SEM_VINCULO" else "sem_config"] += 1
            saida.evento(evento=plano["status"], interaction_id=interacao["interaction_id"],
                         categoria=plano.get("categoria"), motivo=plano["motivo"])
            if confirmo:
                gravar_trilha(porta, chave, interacao, plano["status"], "NENHUMA",
                              {"motivo": plano["motivo"]})
            continue
        if not confirmo:
            contagem["dry_run"] += 1
            saida.evento(evento="PLANO", interaction_id=interacao["interaction_id"],
                         categoria=plano["categoria"],
                         atos=[{"operacao": a["operacao"], "escrita": a["escrita"]} for a in plano["atos"]])
            continue
        try:
            resultado = executar_plano(cliente, plano, dry_run=False)
        except Recusa as e:
            contagem["falhas"] += 1
            saida.evento(evento="FALHA", interaction_id=interacao["interaction_id"],
                         motivo=e.motivo, detalhe=e.detalhe)
            gravar_trilha(porta, chave, interacao, "FALHA", "API_CONTROLADA",
                          {"erro": e.motivo, "detalhe": e.detalhe[:300]}, erro=e.detalhe[:300])
            continue
        if resultado["status"] != "OK":
            contagem["falhas"] += 1
            saida.evento(evento="FALHA", interaction_id=interacao["interaction_id"],
                         motivo=resultado["status"], detalhe=resultado.get("motivo", ""))
            gravar_trilha(porta, chave, interacao, "FALHA", "API_CONTROLADA",
                          {"erro": resultado["status"], "detalhe": resultado.get("motivo", "")},
                          erro=resultado.get("motivo", "")[:300])
            continue
        contagem["propagadas"] += 1
        payload = {"categoria": plano["categoria"], "atos": resultado["resultados"],
                   "correlacao": config.correlacao or chave}
        gravar_trilha(porta, chave, interacao, "ATUALIZADO", "ATUALIZACAO_CRM", payload)
        saida.evento(evento="ATUALIZADO", interaction_id=interacao["interaction_id"], chave=chave,
                     categoria=plano["categoria"], atos=resultado["resultados"])
    saida.evento(evento="resumo", **contagem)
    if not confirmo:
        saida.fechar("DRY_RUN")
        return CODIGO_OK
    saida.fechar("OK" if contagem["falhas"] == 0 else "PARCIAL")
    return CODIGO_OK if contagem["falhas"] == 0 else CODIGO_FALHA


def desfazer(config: Configuracao, saida: Saida, chave: str, confirmo: bool) -> int:
    porta = PortaBanco(config.porta_banco, config.ambiente)
    anterior = ja_propagado(porta, chave)
    if not anterior or anterior.get("status") not in ("ATUALIZADO", "ATUALIZADO_PARCIAL"):
        saida.evento(evento="recusa", motivo="DESFAZER_SEM_ALVO",
                     detalhe=f"a chave {chave} nao tem atualizacao registrada (nem o DRY_RUN serve de alvo)")
        saida.fechar("RECUSADO")
        return CODIGO_RECUSA
    if not confirmo:
        saida.evento(evento="DRY_RUN", chave=chave, detalhe="marcaria DESFEITO; use --confirmo")
        saida.fechar("DRY_RUN")
        return CODIGO_OK
    porta.executar(
        "INSERT INTO " + TABELA_SYNC + " (id, entity_type, entity_id, source_system, target_system, "
        "operation, source_version, idempotency_key, status, request_payload, completed_at) VALUES "
        f"(gen_random_uuid(), 'interaction', {pq(anterior.get('entity_id'))}, 'odoo', "
        f"'sales_intelligence', 'DESFAZER', '{VERSAO}', "
        f"'{chave.replace(chr(39), chr(39) * 2)}#desfeito', 'DESFEITO', "
        f"'{{\"marca\":\"DESFEITO\",\"preserva\":\"ATUALIZADO\"}}', NOW())")
    saida.evento(evento="DESFEITO", chave=chave,
                 detalhe="a linha ATUALIZADO e' preservada; o desfazer e' uma linha nova de trilha")
    saida.fechar("OK")
    return CODIGO_OK


def pq(valor) -> str:
    if valor is None:
        return "NULL"
    return "'" + str(valor).replace("'", "''") + "'"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Atualizacao do Odoo a partir das respostas v1 (TRE-W6-E06-T01)")
    parser.add_argument("--ambiente", choices=AMBIENTES, default=None)
    acao = parser.add_mutually_exclusive_group(required=True)
    acao.add_argument("--planejar", action="store_true",
                      help="mostra a configuracao efetiva (chave mascarada) e o que falta; nunca conecta")
    acao.add_argument("--conferir", action="store_true",
                      help="valida contrato, config, guardas e os invariantes da propria fonte")
    acao.add_argument("--regras", action="store_true", help="imprime os atos declarados por categoria")
    acao.add_argument("--propagar", action="store_true",
                      help="monta e executa os atos de CRM (dry-run sem --confirmo)")
    acao.add_argument("--desfazer", metavar="CHAVE",
                      help="marca a trilha da chave como DESFEITO (dry-run ate --confirmo)")
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--porta-banco", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--limite", type=int, default=None)
    parser.add_argument("--saida", default=None, help="diretorio do relatorio e da trilha")
    parser.add_argument("--confirmo", action="store_true")
    args = parser.parse_args(argv)

    try:
        contrato = carregar_contrato(args.contrato)
    except Recusa as e:
        print(json.dumps({"evento": "recusa", "motivo": e.motivo, "detalhe": e.detalhe},
                         ensure_ascii=False))
        return CODIGO_RECUSA
    config = montar_config(args)
    saida = Saida(os.path.join(args.saida, "relatorio.json") if args.saida else None,
                  os.path.join(args.saida, "trilha.jsonl") if args.saida else None, config.chave)

    if args.regras:
        for ato in sorted(contrato["atos_por_categoria"], key=lambda a: a["ordem"]):
            print(json.dumps({"categoria": ato["categoria"], "evento": ato["evento"],
                              "proxima_acao": ato["proxima_acao"], "atividade": ato["atividade"],
                              "prazo_dias": ato["prazo_dias"]}, ensure_ascii=False))
        print(json.dumps({"sem_ato": contrato["categorias_sem_ato"]}, ensure_ascii=False))
        return CODIGO_OK

    if args.planejar or args.conferir:
        violacoes = auditar_fonte()
        problemas = validar(config)
        print(json.dumps({"configuracao": config.resumo(),
                          "contrato": contrato["versao"],
                          "problemas": [{"motivo": m, "detalhe": d} for m, d in problemas],
                          "violacoes_da_fonte": violacoes}, ensure_ascii=False, indent=2))
        return CODIGO_OK if args.planejar and not violacoes else (CODIGO_RECUSA if problemas or violacoes else CODIGO_OK)

    if args.propagar:
        return propagar(config, contrato, saida, args.confirmo, args.porta_banco)
    return desfazer(config, saida, args.desfazer, args.confirmo)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Recusa as erro:
        print(json.dumps({"evento": "recusa", "motivo": erro.motivo, "detalhe": erro.detalhe},
                         ensure_ascii=False))
        sys.exit(CODIGO_RECUSA)
    except SystemExit:
        raise
