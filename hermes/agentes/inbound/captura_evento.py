#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Captura de lead COLETADO EM EVENTO v1 (`captura-evento-v1`) — card TRE-W7-E06-T01 (W7 / Inbound).

O que este componente FAZ (e so' isto): recebe a COLETA do evento ja' normalizada em JSON (o leitor de
QR/cracha ou a planilha de presenca sao operacao de n8n/operador — ver lacuna L2 do contrato), exige o
VINCULO DO EVENTO e o consentimento, resolve a identidade da empresa por identificador DECLARADO (forte
antes de fraco), grava `organizations` + `contacts` + `interactions` na base canonica de
`sales_intelligence` e a trilha de idempotencia em `sync_events`.

Invariantes deste componente (cada um com item de aceite):

  1. EVENTO DECLARADO E' BARREIRA, NAO PREFERENCIA: sem `origem_evento.event_id`, sem `capture_method`
     no vocabulario ou sem `origem_evento.capturado_em` a coleta RECUSA (`EVENTO_NAO_DECLARADO`) — zero
     linha em organizations/contacts/interactions. Lead coletado em evento sem o vinculo do evento e'
     lead sem atribuicao: entra na base como se fosse outro canal (doc 03, Motor 3 — Relationship).
  2. CONSENTIMENTO E' BARREIRA, NAO PREFERENCIA: sem `consentimento.aceito = true`, sem `legal_basis` no
     vocabulario e sem `forma` no vocabulario de captura de evento a coleta RECUSA
     (`RECUSADO_CONSENTIMENTO`) — a forma e' a evidencia de COMO o opt-in foi dado no evento (Data
     Contract V1 §9). Errar para mais aqui custa compliance.
  3. IDENTIDADE NAO SE INVENTA: identificador FORTE (CNPJ/dominio/LinkedIn) que casa reusa a
     organizacao; identificador FRACO que casa vai para `REVIEW_REQUIRED` (fila humana) e NAO faz
     merge — ambiguidade nao se resolve por heuristica (§5). Nada casando, o UUID canonico nasce aqui.
  4. LIMITE DE ESCRITA: so' `organizations`, `contacts`, `interactions` e `sync_events`, e so' por
     INSERT (a trilha e' append). A auditoria da propria fonte procura comandos de escrita proibidos e
     RECUSA (`ESCRITA_NO_CODIGO`, exit 3) ANTES de qualquer conexao.
  5. IDEMPOTENCIA: a chave e' `evento:<event_id>:<captura_id>` (UNIQUE em `sync_events`). Reentrega
     devolve `JA_CAPTURADO`, nao grava de novo e nao cria linha nova de trilha. O `event_id` entra na
     chave de proposito: o mesmo lead em dois eventos sao duas coletas legitimas e ficam distintas.
  6. PRIVACIDADE: a evidencia (relatorio/trilha) mascara e-mail, telefone e CNPJ. `do_not_contact`,
     `opt_out_email` e `opt_out_whatsapp` da ficha do evento sao gravados como BLOQUEIO.
  7. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<dev|aceite> psql`, via --porta-banco/TRE_EVENTO_PORTA_BANCO) — prefixo remoto
     RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige aprovacao humana registrada; `prod` RECUSA por desenho
     (exit 4). Escrever exige `--confirmo`; sem ele a rodada e' DRY_RUN.
  8. SEGREDO: nenhum valor de segredo entra na linha de comando; se o token configurado
     (`TRE_EVENTO_TOKEN`) aparecer na evidencia, a gravacao e' recusada (`SENHA_VAZADA`, exit 5).

O que ele NAO faz, por desenho (declarado em `lacunas` do contrato): nao le QR/cracha, nao importa
planilha em lote, nao cria tabela de eventos (o vinculo do evento vive no resumo/referencia da interacao
e no payload da trilha — lacuna L3), nao escreve no Odoo (o evento de lead inbound nao existe no
vocabulario congelado do Data Contract V1 — falha fechada do consumidor de outbox), nao abre endpoint
HTTP, nao manda mensagem ao lead, nao calcula score/NBA, nao atribui campanha por anuncio (W7-E02/E03).

Saida: um evento JSON por linha (relatorio opcional) + trilha JSONL opcional; exit code:
  0 = capturado (ou dry-run/replay/recusa registrada) · 2 = uso errado · 3 = recusa (contrato/fonte/
  guarda/banco) · 4 = producao recusada · 5 = segredo vazado.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

VERSAO = "captura-evento-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "captura-evento-v1.json")
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5

TABELA_ORGANIZACOES = "sales_intelligence.organizations"
TABELA_CONTATOS = "sales_intelligence.contacts"
TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_TRILHA = "sales_intelligence.sync_events"

# Guarda de ambiente: em dev a porta de banco e' `docker exec -i <container local> psql ...`
# (mesmo padrao medido nos cards irmaos W6-E05 / W7-E01). Prefixo remoto (ssh ... psql) e' recusado.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(sales|evento|event|evt|captura|inbound)[A-Za-z0-9._-]*$")


class Recusa(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe


# --------------------------------------------------------------------------------------------
# 0. Utilidades (PII mascarada na evidencia; nada de valor cru na saida)
# --------------------------------------------------------------------------------------------
def mascarar(valor: str | None) -> str:
    if not valor:
        return ""
    return valor[:2] + "*" * max(0, len(valor) - 2) if len(valor) > 6 else "<oculta>"


def mascarar_email(endereco: str | None) -> str:
    if not endereco or "@" not in endereco:
        return "<oculta>" if endereco else ""
    local, _, dominio = endereco.partition("@")
    return (local[:2] + "*" * max(0, len(local) - 2)) + "@" + dominio


def mascarar_telefone(numero: str | None) -> str:
    if not numero:
        return ""
    digitos = re.sub(r"\D", "", numero)
    return ("*" * max(0, len(digitos) - 2)) + digitos[-2:] if digitos else "<oculta>"


def mascarar_cnpj(cnpj: str | None) -> str:
    digitos = re.sub(r"\D", "", cnpj or "")
    if not digitos:
        return ""
    return "**.***.***/" + digitos[-4:] if len(digitos) == 14 else ("*" * len(digitos))


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def so_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def texto(valor) -> str:
    return ("" if valor is None else str(valor)).strip()


def q(valor) -> str:
    """Literal SQL seguro (somente string/NULL) — nao existe interpolacao crua de valor."""
    if valor is None:
        return "NULL"
    return "'" + str(valor).replace("'", "''") + "'"


def normalizar_dominio(valor: str) -> str:
    limpo = (valor or "").strip().lower()
    limpo = re.sub(r"^https?://", "", limpo)
    limpo = re.sub(r"^www\.", "", limpo)
    return limpo.split("/")[0]


def normalizar_instante(valor: str | None, padrao: str = "NOW()") -> str:
    bruto = texto(valor)
    if not bruto:
        return padrao
    try:
        limpo = bruto.replace("Z", "+00:00")
        return "'" + datetime.fromisoformat(limpo).astimezone(timezone.utc).isoformat() + "'"
    except ValueError:
        return padrao


def instante_valido(valor: str | None) -> bool:
    bruto = texto(valor)
    if not bruto:
        return False
    try:
        datetime.fromisoformat(bruto.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------------------------
# 1. Fonte: invariantes verificaveis no proprio arquivo (invariante 4)
# --------------------------------------------------------------------------------------------
def padroes_de_escrita_proibida() -> dict:
    """Montados em tempo de execucao (literais escritos direto nao se achariam a si mesmos)."""
    return {
        "sql_de_escrita_crua": ["DE" + "LETE FROM", "UP" + "DATE ", "TRUN" + "CATE", "DR" + "OP ",
                                "AL" + "TER TABLE"],
    }


def auditar_fonte(caminho: str | None = None) -> list:
    """Auditoria da propria fonte. Devolve a lista de violacoes (vazia = ok)."""
    alvo = caminho or os.path.abspath(__file__)
    with open(alvo, "r", encoding="utf-8") as fh:
        linhas = fh.readlines()
    util = []
    dentro_doc = False
    for linha in linhas:
        t = linha.strip()
        # docstrings e comentarios DECLARAM a regra; nao podem ser confundidos com a regra quebrada.
        # A contagem de delimitadores por linha e' o que distingue docstring de uma linha de codigo que
        # comeca com aspas (defeito medido no card irmao W7-E01: o toggle simples desalinhava a paridade).
        ocorrencias = t.count('"""') + t.count("'''")
        if ocorrencias >= 2:
            continue
        if ocorrencias == 1:
            dentro_doc = not dentro_doc
            continue
        if dentro_doc or t.startswith("#"):
            continue
        util.append(linha)
    corpo = "".join(util)
    violacoes = []
    for nome, padroes in padroes_de_escrita_proibida().items():
        for padrao in padroes:
            if padrao in corpo:
                violacoes.append({"padrao": nome, "alvo": padrao})
    if "INSERT INTO" not in corpo:
        violacoes.append({"padrao": "sem_insert", "alvo": "o modulo nao grava por INSERT declarado"})
    return violacoes


# --------------------------------------------------------------------------------------------
# 2. Contrato (vocabulario e regras) — declarado, nunca inventado
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
    vocab = contrato.get("vocabulario") or {}
    for campo in ("channel", "direction", "interaction_type", "organization_source", "organization_status",
                  "legal_basis", "forma_de_consentimento", "capture_method", "contact_source",
                  "status_trilha"):
        valores = vocab.get(campo)
        if not isinstance(valores, list) or not valores:
            falhas.append(f"vocabulario.{campo} ausente/vazio")
        elif len(set(valores)) != len(valores):
            falhas.append(f"vocabulario.{campo} com repeticao")
    for obrigatorio in ("CAPTURADO", "JA_CAPTURADO", "REVIEW_REQUIRED", "RECUSADO_CONSENTIMENTO",
                        "EVENTO_NAO_DECLARADO"):
        if obrigatorio not in (vocab.get("status_trilha") or []):
            falhas.append(f"status_trilha sem o estado obrigatorio {obrigatorio}")
    coleta = contrato.get("coleta") or {}
    obrigatorios = coleta.get("campos_obrigatorios") or []
    for exigido in ("captura_id", "origem_evento.event_id", "origem_evento.capture_method",
                    "origem_evento.capturado_em", "consentimento.aceito", "consentimento.legal_basis",
                    "consentimento.forma", "empresa.nome", "contato.nome"):
        if exigido not in obrigatorios:
            falhas.append(f"coleta.campos_obrigatorios sem {exigido}")
    if "contato.email ou contato.telefone" not in obrigatorios:
        falhas.append("coleta.campos_obrigatorios sem o canal de resposta (email ou telefone)")
    idents = contrato.get("identificadores") or {}
    try:
        limiar = float(idents.get("limiar_de_merge_automatico"))
    except (TypeError, ValueError):
        falhas.append("identificadores.limiar_de_merge_automatico ausente/nao numerico")
        limiar = None
    if limiar is not None and not 0 < limiar <= 1:
        falhas.append(f"limiar_de_merge_automatico fora de (0,1]: {limiar}")
    for grupo in ("fortes", "fracos"):
        itens = idents.get(grupo)
        if not isinstance(itens, list) or not itens:
            falhas.append(f"identificadores.{grupo} ausente/vazio")
            continue
        for item in itens:
            try:
                conf = float(item.get("confianca"))
            except (TypeError, ValueError):
                falhas.append(f"identificadores.{grupo} item sem confianca numerica: {item}")
                continue
            if limiar is not None and grupo == "fortes" and conf < limiar:
                falhas.append(f"identificador forte {item.get('campo')} abaixo do limiar de merge ({conf})")
            if limiar is not None and grupo == "fracos" and conf >= limiar:
                falhas.append(f"identificador fraco {item.get('campo')} no limiar de merge ({conf}) — "
                              "fraco nunca faz merge automatico")
    return falhas


# --------------------------------------------------------------------------------------------
# 3. Configuracao e guardas de ambiente (ADR-005)
# --------------------------------------------------------------------------------------------
class Configuracao:
    def __init__(self, env: dict):
        self.ambiente = (env.get("TRE_AMBIENTE") or "dev").strip().lower()
        self.porta_banco = (env.get("TRE_EVENTO_PORTA_BANCO") or "").strip()
        self.aprovacao = (env.get("TRE_EVENTO_APROVACAO_HUMANA") or "").strip()
        self.token = env.get("TRE_EVENTO_TOKEN") or ""

    def resumo(self) -> dict:
        return {
            "versao": VERSAO,
            "ambiente": self.ambiente,
            "porta_banco": self.porta_banco or "(ausente)",
            "aprovacao_humana": self.aprovacao or "(ausente)",
            "token": mascarar(self.token),
        }


def validar(config: Configuracao) -> list:
    problemas = []
    if config.ambiente not in AMBIENTES:
        problemas.append(("AMBIENTE_INVALIDO", f"ambiente '{config.ambiente}' fora de {AMBIENTES}"))
        return problemas
    if config.ambiente == "prod":
        problemas.append(("PRODUCAO_RECUSADA",
                          "prod recusa por desenho (ADR-005): nada nasce em producao, a promocao e' ato humano"))
        return problemas
    if not config.porta_banco:
        problemas.append(("BANCO_NAO_DECLARADO",
                          "porta de banco obrigatoria (--porta-banco / TRE_EVENTO_PORTA_BANCO)"))
    elif config.ambiente == "dev":
        m = RE_CONTAINER_LOCAL.match(config.porta_banco.strip())
        if not m or not CONTAINERS_LOCAIS_DEV.match(m.group(1)):
            problemas.append(("BANCO_NAO_E_DEV",
                              "dev escreve apenas em container local de dev/aceite via docker exec; "
                              f"prefixo recusado: {config.porta_banco}"))
    if config.ambiente == "homolog" and not config.aprovacao:
        problemas.append(("HOMOLOG_SEM_APROVACAO", "homolog exige TRE_EVENTO_APROVACAO_HUMANA registrada"))
    return problemas


# --------------------------------------------------------------------------------------------
# 4. Porta de banco (comando declarado; SQL por stdin; resposta JSON)
# --------------------------------------------------------------------------------------------
class PortaBanco:
    """Acesso ao PostgreSQL pelo comando de porta declarado (`docker exec -i <container> psql ...`).

    Sem driver embutido: o SQL vai por stdin e a resposta volta em JSON (`json_agg`) — a mesma porta
    serve ao aceite em container descartavel e ao host da VPS. Sem DDL, sem UPDATE e sem DELETE.
    """

    def __init__(self, prefixo: str, ambiente: str):
        self.prefixo = prefixo
        self.ambiente = ambiente

    def _argumentos(self) -> list:
        try:
            return shlex.split(self.prefixo)
        except ValueError as e:
            raise Recusa("BANCO_NAO_DECLARADO", f"prefixo de banco invalido: {e}") from e

    def consultar(self, sql: str) -> list:
        comando = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c",
                                        f"SELECT coalesce(json_agg(t), '[]'::json)::text FROM ({sql}) t;"]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        linhas = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        if not linhas:
            raise Recusa("BANCO_RESPOSTA_VAZIA", "a porta de banco nao devolveu JSON")
        try:
            return json.loads(linhas[-1])
        except json.JSONDecodeError as e:
            raise Recusa("BANCO_RESPOSTA_INVALIDA",
                         f"a porta de banco nao devolveu JSON: {linhas[-1][:200]}") from e

    def executar(self, sql: str) -> list:
        """Escreve (INSERT ... RETURNING) e devolve as linhas afetadas.

        INSERT sem RETURNING nao pode ser lido por CTE ("WITH query does not have a RETURNING clause");
        so' o INSERT com RETURNING e' envolvido pelo envelope de leitura.
        """
        base = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c"]
        if "RETURNING" in sql.upper():
            sql_exec = (f"WITH afetados AS ({sql}) "
                        "SELECT coalesce(json_agg(afetados), '[]'::json)::text FROM afetados;")
        else:
            sql_exec = sql
        proc = subprocess.run(base + [sql_exec], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        linhas = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        return json.loads(linhas[-1]) if linhas else []


# --------------------------------------------------------------------------------------------
# 5. Leitura da coleta e validacao de conteudo
# --------------------------------------------------------------------------------------------
def ler_coleta(origem: str) -> dict:
    if origem == "-":
        bruto = sys.stdin.read()
    else:
        if not os.path.isfile(origem):
            raise Recusa("COLETA_AUSENTE", f"arquivo de coleta nao encontrado: {origem}")
        with open(origem, "r", encoding="utf-8") as fh:
            bruto = fh.read()
    try:
        coleta = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise Recusa("COLETA_INVALIDA", f"JSON invalido: {e}") from e
    if not isinstance(coleta, dict):
        raise Recusa("COLETA_INVALIDA", "a coleta precisa ser um objeto JSON")
    return coleta


def _bloco(coleta: dict, nome: str) -> dict:
    valor = coleta.get(nome)
    return valor if isinstance(valor, dict) else {}


def validar_coleta(coleta: dict, contrato: dict) -> tuple:
    """Devolve (status, motivo, payload_auditoria). status CAPTURAVEL significa 'pode seguir'."""
    vocab = contrato.get("vocabulario") or {}
    captura_id = texto(coleta.get("captura_id"))
    evento = _bloco(coleta, "origem_evento")
    empresa = _bloco(coleta, "empresa")
    contato = _bloco(coleta, "contato")
    consentimento = _bloco(coleta, "consentimento")
    capture_method = texto(evento.get("capture_method")).upper()
    auditoria = {
        "captura_id": captura_id,
        "event_id": texto(evento.get("event_id")),
        "evento_nome": texto(evento.get("evento_nome")),
        "capture_method": capture_method,
        "empresa": mascarar(texto(empresa.get("nome"))),
        "cnpj": mascarar_cnpj(texto(empresa.get("cnpj"))),
        "dominio": normalizar_dominio(texto(empresa.get("dominio"))),
        "contato_nome": mascarar(texto(contato.get("nome"))),
        "contato_email": mascarar_email(texto(contato.get("email"))),
        "contato_telefone": mascarar_telefone(texto(contato.get("telefone"))),
        "forma": texto(consentimento.get("forma")).upper(),
    }
    if not captura_id:
        return ("COLETA_INVALIDA", "captura_id ausente (sem chave nao ha idempotencia)", auditoria)
    aceito = consentimento.get("aceito")
    legal_basis = texto(consentimento.get("legal_basis")).upper()
    forma = texto(consentimento.get("forma")).upper()
    if aceito is not True or legal_basis not in (vocab.get("legal_basis") or []) \
            or forma not in (vocab.get("forma_de_consentimento") or []):
        return ("RECUSADO_CONSENTIMENTO",
                f"consentimento.aceito={aceito!r}, legal_basis={legal_basis or '(ausente)'} e "
                f"forma={forma or '(ausente)'} — captura em evento exige opt-in explicito, base legal "
                "declarada e a FORMA como evidencia", auditoria)
    event_id = texto(evento.get("event_id"))
    capturado_em = texto(evento.get("capturado_em"))
    if not event_id or not capturado_em:
        return ("EVENTO_NAO_DECLARADO",
                f"origem_evento.event_id={event_id or '(ausente)'} e capturado_em="
                f"{capturado_em or '(ausente)'} — sem o vinculo do evento a coleta vira lead sem "
                "atribuicao", auditoria)
    if not instante_valido(capturado_em):
        return ("EVENTO_NAO_DECLARADO",
                f"origem_evento.capturado_em='{capturado_em}' nao e' instante ISO-8601 valido", auditoria)
    if capture_method not in (vocab.get("capture_method") or []):
        return ("EVENTO_NAO_DECLARADO",
                f"origem_evento.capture_method='{capture_method or '(ausente)'}' fora do vocabulario "
                f"{vocab.get('capture_method')} — metodo de captura nao se inventa", auditoria)
    faltando = []
    if not texto(empresa.get("nome")):
        faltando.append("empresa.nome")
    if not texto(contato.get("nome")):
        faltando.append("contato.nome")
    if not (texto(contato.get("email")) or texto(contato.get("telefone"))):
        faltando.append("contato.email|contato.telefone")
    if faltando:
        return ("SEM_DADOS_MINIMOS", "campos minimos ausentes: " + ", ".join(faltando), auditoria)
    return ("CAPTURAVEL", "", auditoria)


def chave_de(coleta: dict) -> str:
    evento = _bloco(coleta, "origem_evento")
    event_id = texto(evento.get("event_id"))
    captura_id = texto(coleta.get("captura_id"))
    if event_id and captura_id:
        return f"evento:{event_id}:{captura_id}"
    digest = hashlib.sha256(json.dumps(coleta, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return "evento:" + digest[:24]


def trilha_existente(porta, chave: str) -> dict | None:
    linhas = porta.consultar(
        f"SELECT id::text, status, entity_id::text FROM {TABELA_TRILHA} "
        f"WHERE idempotency_key = {q(chave)} LIMIT 1")
    return linhas[0] if linhas else None


# --------------------------------------------------------------------------------------------
# 6. Resolucao de identidade (forte antes de fraco; ambiguidade vai para a fila humana)
# --------------------------------------------------------------------------------------------
def resolver_organizacao(porta, coleta: dict, contrato: dict) -> dict:
    """Devolve {'status': 'NOVA'|'REUSO'|'REVIEW_REQUIRED', 'organization_id', 'confianca', 'motivo'}."""
    idents = contrato.get("identificadores") or {}
    limiar = float(idents.get("limiar_de_merge_automatico"))
    empresa = _bloco(coleta, "empresa")
    contato = _bloco(coleta, "contato")

    for item in sorted(idents.get("fortes") or [], key=lambda i: i.get("ordem", 99)):
        campo = item.get("campo") or ""
        coluna = item.get("coluna")
        valor_cru = texto(empresa.get(campo.split(".")[-1]))
        if campo.endswith("cnpj"):
            valor = so_digitos(valor_cru)
        elif campo.endswith("dominio"):
            valor = normalizar_dominio(valor_cru)
        else:
            valor = valor_cru.lower().rstrip("/")
        if not valor:
            continue
        linhas = porta.consultar(
            f"SELECT id::text AS id FROM {TABELA_ORGANIZACOES} WHERE {coluna} = {q(valor)} LIMIT 1")
        if linhas:
            return {"status": "REUSO", "organization_id": linhas[0]["id"],
                    "confianca": float(item.get("confianca", 1.0)),
                    "motivo": f"identificador forte {campo}"}
        # identificador forte presente que NAO casa: nada mais a procurar (o forte manda)
        if campo.endswith("cnpj"):
            return {"status": "NOVA", "organization_id": None, "confianca": 1.0,
                    "motivo": "CNPJ declarado sem par na base: organizacao nova"}

    nome = texto(empresa.get("nome")).lower()
    cidade = texto(empresa.get("cidade")).lower()
    telefone = so_digitos(texto(contato.get("telefone")))
    for item in sorted(idents.get("fracos") or [], key=lambda i: i.get("ordem", 99)):
        campo = item.get("campo") or ""
        confianca = float(item.get("confianca", 0.0))
        if "cidade" in campo and nome and cidade:
            linhas = porta.consultar(
                f"SELECT id::text AS id, trade_name, city FROM {TABELA_ORGANIZACOES} "
                f"WHERE lower(coalesce(trade_name,'')) = {q(nome)} AND lower(coalesce(city,'')) = {q(cidade)} LIMIT 1")
        elif "telefone" in campo and nome and telefone:
            linhas = porta.consultar(
                f"SELECT o.id::text AS id, o.trade_name, o.city FROM {TABELA_ORGANIZACOES} o "
                f"JOIN {TABELA_CONTATOS} c ON c.organization_id = o.id "
                f"WHERE lower(coalesce(o.trade_name,'')) = {q(nome)} "
                f"AND regexp_replace(coalesce(c.phone,''), '[^0-9]', '', 'g') = {q(telefone)} LIMIT 1")
        else:
            continue
        if linhas:
            return {"status": "REVIEW_REQUIRED", "organization_id": linhas[0]["id"], "confianca": confianca,
                    "motivo": f"identificador fraco {campo} abaixo do limiar {limiar} "
                              "— fila humana, nunca merge silencioso"}
    return {"status": "NOVA", "organization_id": None, "confianca": 1.0,
            "motivo": "nenhum identificador casou: organizacao nova com UUID canonico proprio"}


# --------------------------------------------------------------------------------------------
# 7. Gravacao (INSERT apenas)
# --------------------------------------------------------------------------------------------
def gravar_organizacao(porta, coleta: dict, contrato: dict) -> str:
    vocab = contrato.get("vocabulario") or {}
    empresa = _bloco(coleta, "empresa")
    colunas = ("id", "legal_name", "trade_name", "domain", "website_url", "linkedin_url", "cnpj", "city",
               "state", "status", "source")
    valores = ("gen_random_uuid()", q(texto(empresa.get("nome"))), q(texto(empresa.get("nome"))),
               q(normalizar_dominio(texto(empresa.get("dominio"))) or None),
               q(texto(empresa.get("website_url")) or None),
               q(texto(empresa.get("linkedin_url")).lower().rstrip("/") or None),
               q(so_digitos(texto(empresa.get("cnpj"))) or None),
               q(texto(empresa.get("cidade")) or None), q(texto(empresa.get("estado")) or None),
               q(vocab["organization_status"][0]), q(vocab["organization_source"][0]))
    linhas = porta.executar(
        f"INSERT INTO {TABELA_ORGANIZACOES} ({', '.join(colunas)}) VALUES ({', '.join(valores)}) "
        "RETURNING id::text AS id")
    if not linhas:
        raise Recusa("GRAVACAO_SEM_RETORNO", "INSERT em organizations nao devolveu id")
    return linhas[0]["id"]


def gravar_contato(porta, coleta: dict, contrato: dict, organization_id: str) -> str:
    vocab = contrato.get("vocabulario") or {}
    contato = _bloco(coleta, "contato")
    nome = texto(contato.get("nome"))
    partes = nome.split(" ")
    preferido = texto(contato.get("preferred_channel")).lower()
    if preferido and preferido not in (vocab.get("preferred_channel") or []):
        raise Recusa("CONTRATO_INVALIDO",
                     f"contato.preferred_channel '{preferido}' fora do vocabulario do contrato")
    colunas = ("id", "organization_id", "first_name", "last_name", "full_name", "job_title", "email",
               "phone", "whatsapp", "preferred_channel", "legal_basis", "do_not_contact", "opt_out_email",
               "opt_out_whatsapp", "source")
    valores = ("gen_random_uuid()", q(organization_id), q(partes[0] if partes else None),
               q(" ".join(partes[1:]) or None), q(nome), q(texto(contato.get("cargo")) or None),
               q(texto(contato.get("email")) or None), q(texto(contato.get("telefone")) or None),
               q(texto(contato.get("telefone")) or None), q(preferido or None),
               q(texto((_bloco(coleta, "consentimento")).get("legal_basis")).upper() or None),
               "true" if contato.get("do_not_contact") is True else "false",
               "true" if contato.get("opt_out_email") is True else "false",
               "true" if contato.get("opt_out_whatsapp") is True else "false",
               q(vocab["contact_source"][0]))
    linhas = porta.executar(
        f"INSERT INTO {TABELA_CONTATOS} ({', '.join(colunas)}) VALUES ({', '.join(valores)}) "
        "RETURNING id::text AS id")
    if not linhas:
        raise Recusa("GRAVACAO_SEM_RETORNO", "INSERT em contacts nao devolveu id")
    return linhas[0]["id"]


def resumo_do_evento(evento: dict) -> str:
    partes = [f"Coleta em evento: {texto(evento.get('evento_nome')) or texto(evento.get('event_id'))}",
              f"evento={texto(evento.get('event_id'))}",
              f"metodo={texto(evento.get('capture_method')).upper()}"]
    if texto(evento.get("evento_inicio")):
        partes.append(f"inicio={texto(evento.get('evento_inicio'))}")
    if texto(evento.get("local")):
        partes.append(f"local={texto(evento.get('local'))}")
    if texto(evento.get("stand")):
        partes.append(f"stand={texto(evento.get('stand'))}")
    return " | ".join(partes)


def gravar_interacao(porta, coleta: dict, contrato: dict, organization_id: str, contact_id: str) -> str:
    vocab = contrato.get("vocabulario") or {}
    evento = _bloco(coleta, "origem_evento")
    colunas = ("id", "organization_id", "contact_id", "channel", "direction", "interaction_type",
               "occurred_at", "subject", "content_summary", "content_reference")
    valores = ("gen_random_uuid()", q(organization_id), q(contact_id), q(vocab["channel"][0]),
               q(vocab["direction"][0]), q(vocab["interaction_type"][0]),
               normalizar_instante(evento.get("capturado_em")), q("Lead coletado em evento"),
               q(resumo_do_evento(evento)[:400]), q(texto(coleta.get("captura_id"))))
    linhas = porta.executar(
        f"INSERT INTO {TABELA_INTERACOES} ({', '.join(colunas)}) VALUES ({', '.join(valores)}) "
        "RETURNING id::text AS id")
    if not linhas:
        raise Recusa("GRAVACAO_SEM_RETORNO", "INSERT em interactions nao devolveu id")
    return linhas[0]["id"]


def gravar_trilha(porta, chave: str, status: str, payload: dict, entity_id: str | None = None) -> None:
    sql = (f"INSERT INTO {TABELA_TRILHA} (id, entity_type, entity_id, source_system, target_system, "
           f"operation, source_version, idempotency_key, status, request_payload, completed_at) VALUES "
           f"(gen_random_uuid(), {q('organization')}, {q(entity_id)}, {q('evento')}, "
           f"{q('sales_intelligence')}, {q('CAPTURA_LEAD_EVENTO')}, {q(VERSAO)}, {q(chave)}, {q(status)}, "
           f"{q(json.dumps(payload, ensure_ascii=False, sort_keys=True))}, NOW())")
    porta.executar(sql)


# --------------------------------------------------------------------------------------------
# 8. Saida com checagem fail-closed de segredo
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

    def _conferir(self, texto_evidencia: str) -> None:
        if self.segredo and len(self.segredo) >= 4 and self.segredo in texto_evidencia:
            print(json.dumps({"evento": "SENHA_VAZADA", "motivo": "SENHA_VAZADA",
                              "detalhe": "o valor de TRE_EVENTO_TOKEN apareceu na evidencia; "
                                         "gravacao recusada (exit 5)"}, ensure_ascii=False))
            raise SystemExit(CODIGO_SEGREDO)

    def _gravar(self, caminho: str, dados: dict) -> None:
        texto_evidencia = json.dumps(dados, ensure_ascii=False, sort_keys=True)
        self._conferir(texto_evidencia)
        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write(texto_evidencia + "\n")

    def fechar(self, veredito: str) -> None:
        self.relatorio["veredito"] = veredito
        conteudo = json.dumps(self.relatorio, ensure_ascii=False, sort_keys=True, indent=2)
        self._conferir(conteudo)
        if self.caminho_relatorio:
            os.makedirs(os.path.dirname(os.path.abspath(self.caminho_relatorio)), exist_ok=True)
            with open(self.caminho_relatorio, "w", encoding="utf-8") as fh:
                fh.write(conteudo + "\n")


# --------------------------------------------------------------------------------------------
# 9. Captura (nucleo, testavel in-process com uma porta injetada)
# --------------------------------------------------------------------------------------------
def capturar(coleta: dict, contrato: dict, porta, saida: Saida, escrever: bool) -> dict:
    chave = chave_de(coleta)
    status, motivo, auditoria = validar_coleta(coleta, contrato)
    if status != "CAPTURAVEL":
        payload = {"captura_id": auditoria.get("captura_id"), "event_id": auditoria.get("event_id"),
                   "motivo": motivo}
        if escrever:
            gravar_trilha(porta, chave, status, payload)
        saida.evento(evento="CAPTURA_RECUSADA", status=status, motivo=motivo,
                     captura_id=auditoria.get("captura_id"), event_id=auditoria.get("event_id"),
                     dados=auditoria)
        return {"status": status, "motivo": motivo, "escrito": False}

    existente = trilha_existente(porta, chave)
    if existente:
        saida.evento(evento="CAPTURA_REPLAY", status="JA_CAPTURADO",
                     captura_id=auditoria.get("captura_id"), event_id=auditoria.get("event_id"),
                     organization_id=existente.get("entity_id"), dados=auditoria)
        return {"status": "JA_CAPTURADO", "motivo": "coleta ja' capturada", "escrito": False}

    resolucao = resolver_organizacao(porta, coleta, contrato)
    if resolucao["status"] == "REVIEW_REQUIRED":
        payload = {"captura_id": auditoria.get("captura_id"), "event_id": auditoria.get("event_id"),
                   "capture_method": auditoria.get("capture_method"),
                   "candidato": resolucao.get("organization_id"), "confianca": resolucao.get("confianca"),
                   "motivo": resolucao.get("motivo")}
        if escrever:
            gravar_trilha(porta, chave, "REVIEW_REQUIRED", payload,
                          entity_id=resolucao.get("organization_id"))
        saida.evento(evento="CAPTURA_EM_REVISAO", status="REVIEW_REQUIRED", motivo=resolucao.get("motivo"),
                     captura_id=auditoria.get("captura_id"), event_id=auditoria.get("event_id"),
                     organization_id=resolucao.get("organization_id"),
                     confianca=resolucao.get("confianca"), dados=auditoria)
        return {"status": "REVIEW_REQUIRED", "motivo": resolucao.get("motivo"), "escrito": False}

    if not escrever:
        saida.evento(evento="DRY_RUN", status="DRY_RUN", captura_id=auditoria.get("captura_id"),
                     event_id=auditoria.get("event_id"), identidade=resolucao.get("status"),
                     motivo=resolucao.get("motivo"), dados=auditoria)
        return {"status": "DRY_RUN", "motivo": "sem --confirmo: nada foi gravado", "escrito": False}

    organization_id = resolucao.get("organization_id")
    novo = resolucao["status"] == "NOVA"
    if novo:
        organization_id = gravar_organizacao(porta, coleta, contrato)
    contact_id = gravar_contato(porta, coleta, contrato, organization_id)
    interaction_id = gravar_interacao(porta, coleta, contrato, organization_id, contact_id)
    payload = {"captura_id": auditoria.get("captura_id"), "event_id": auditoria.get("event_id"),
               "evento_nome": auditoria.get("evento_nome"),
               "capture_method": auditoria.get("capture_method"),
               "organization_id": organization_id, "contact_id": contact_id,
               "interaction_id": interaction_id, "confianca": resolucao.get("confianca"),
               "motivo": resolucao.get("motivo")}
    gravar_trilha(porta, chave, "CAPTURADO", payload, entity_id=organization_id)
    saida.evento(evento="CAPTURA_OK", status="CAPTURADO", captura_id=auditoria.get("captura_id"),
                 event_id=auditoria.get("event_id"), organization_nova=novo,
                 organization_id=organization_id, contact_id=contact_id, interaction_id=interaction_id,
                 identidade=resolucao.get("status"), confianca=resolucao.get("confianca"),
                 dados=auditoria)
    return {"status": "CAPTURADO", "escrito": True, "organization_id": organization_id,
            "contact_id": contact_id, "interaction_id": interaction_id}


# --------------------------------------------------------------------------------------------
# 10. CLI
# --------------------------------------------------------------------------------------------
def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="captura_evento", description="Captura de lead coletado em evento v1")
    p.add_argument("--coleta", help="arquivo JSON da coleta do evento ('-' = stdin)")
    p.add_argument("--porta-banco", help="comando de porta do PostgreSQL (docker exec -i <ct> psql ...)")
    p.add_argument("--ambiente", help="dev | homolog | prod (default: TRE_AMBIENTE ou dev)")
    p.add_argument("--relatorio", help="arquivo do relatorio JSON")
    p.add_argument("--trilha", help="arquivo JSONL da trilha")
    p.add_argument("--confirmo", action="store_true", help="autoriza a escrita (sem isso e' DRY_RUN)")
    p.add_argument("--planejar", action="store_true", help="mostra config/contrato sem tocar o banco")
    p.add_argument("--conferir", action="store_true", help="valida config + contrato + fonte")
    p.add_argument("--autoteste", action="store_true", help="roda a auditoria da propria fonte")
    return p


def main(argv=None) -> int:
    args = montar_parser().parse_args(argv)
    env = dict(os.environ)
    if args.ambiente:
        env["TRE_AMBIENTE"] = args.ambiente
    if args.porta_banco:
        env["TRE_EVENTO_PORTA_BANCO"] = args.porta_banco
    config = Configuracao(env)

    if args.autoteste:
        violacoes = auditar_fonte()
        print(json.dumps({"auditoria": "fonte", "violacoes": violacoes}, ensure_ascii=False))
        return 0 if not violacoes else 3

    contrato = carregar_contrato(os.environ.get("CAPTURA_EVENTO_CONTRATO"))

    if args.planejar or args.conferir:
        violacoes = auditar_fonte()
        problemas = validar(config) if args.conferir else []
        print(json.dumps({"versao": VERSAO, "config": config.resumo(),
                          "campos_obrigatorios": (contrato.get("coleta") or {}).get("campos_obrigatorios"),
                          "violacoes_da_fonte": violacoes,
                          "problemas": [{"motivo": m, "detalhe": d} for m, d in problemas]},
                         ensure_ascii=False, indent=2))
        return 0

    if not args.coleta:
        print(json.dumps({"motivo": "USO_INCORRETO", "detalhe": "--coleta e' obrigatorio"},
                         ensure_ascii=False))
        return 2

    problemas = validar(config)
    if problemas:
        motivo, detalhe = problemas[0]
        codigo = 4 if motivo == "PRODUCAO_RECUSADA" else 3
        print(json.dumps({"motivo": motivo, "detalhe": detalhe, "ambiente": config.ambiente},
                         ensure_ascii=False))
        return codigo

    violacoes = auditar_fonte()
    if violacoes:
        print(json.dumps({"motivo": "ESCRITA_NO_CODIGO", "detalhe": violacoes}, ensure_ascii=False))
        return 3

    coleta = ler_coleta(args.coleta)
    saida = Saida(args.relatorio, args.trilha, config.token)
    porta = PortaBanco(config.porta_banco, config.ambiente)
    try:
        resultado = capturar(coleta, contrato, porta, saida, escrever=args.confirmo)
    except Recusa as e:
        saida.evento(evento="RECUSA", motivo=e.motivo, detalhe=e.detalhe)
        saida.fechar(e.motivo)
        return 3
    saida.fechar(resultado["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
