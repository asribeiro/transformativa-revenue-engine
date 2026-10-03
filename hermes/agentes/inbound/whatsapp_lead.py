#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WhatsApp engaged-lead workflow v1 (`whatsapp-lead-v1`) — card TRE-W7-E05-T01 (W7 / Inbound).

O que este componente FAZ (e so' isto): recebe a MENSAGEM INBOUND de WhatsApp ja' normalizada em
JSON (o webhook do provedor e' do n8n/operador — ver lacuna L2 do contrato) e conduz o lead que JA'
ESTA NA BASE pelo ciclo de engajamento de WhatsApp:

  1. valida o evento contra o contrato (`whatsapp-lead-v1.json`) — tipo de mensagem e vocabulario
     fechados, nunca inventados pelo codigo;
  2. resolve a identidade pelo TELEFONE (nucleo nacional de 11 digitos) contra `contacts.phone` e
     `contacts.whatsapp`; sem par -> `SEM_VINCULO` (organizacao/contato NAO se inventam); com mais de
     um par -> `REVIEW_REQUIRED` (fila humana, nunca heuristica);
  3. classifica a mensagem por REGRA DECLARADA (OPT_OUT vence qualquer sinal de interesse);
  4. calcula a JANELA DE ATENDIMENTO do provedor (24 h desde a ultima entrada do contato) para dizer
     se a resposta e' LIVRE ou se exige TEMPLATE + aprovacao humana;
  5. grava a interacao (`channel=WHATSAPP`, `direction=INBOUND`) e a trilha de idempotencia em
     `sync_events` com a chave `whatsapp:<message_id>`;
  6. mascara o telefone do lead na evidencia (relatorio/trilha).

Invariantes deste componente (cada um com item de suite e de aceite):

  1. NUNCA ENVIA. O outbound de WhatsApp e' acao L1 (primeiro contato/reengajamento) e depende de
     aprovacao humana registrada: aqui a saida e' uma PROPOSTA (`proximo_passo`), jamais uma mensagem.
  2. OPT_OUT VENCE: mensagem com pedido de descadastro e' `OPT_OUT` mesmo que tambem traga sinal de
     interesse — errar para menos aqui custa compliance (o oposto do interesse, onde errar para mais
     custa atencao comercial).
  3. BLOQUEIO NAO E' PREFERENCIA: contato com `do_not_contact` ou `opt_out_whatsapp` na base nao gera
     proximo passo nenhum (`BLOQUEADO_POR_BLOQUEIO`, fila humana); a mensagem continua registrada
     porque o FATO aconteceu — apagar o registro seria perder a evidencia de compliance.
  4. IDENTIDADE NAO SE INVENTA: telefone desconhecido -> `SEM_VINCULO`, telefone ambiguo ->
     `REVIEW_REQUIRED`; nenhum dos dois cria organizacao/contato.
  5. LIMITE DE ESCRITA: so' `interactions` e `sync_events`, e so' por INSERT. A auditoria da propria
     fonte procura comando de escrita proibido e RECUSA (`ESCRITA_NO_CODIGO`, exit 3) ANTES de
     qualquer conexao.
  6. IDEMPOTENCIA: `whatsapp:<message_id>` e' UNIQUE em `sync_events`; reentrega devolve `JA_RECEBIDO`
     e nao grava nada de novo.
  7. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `dev` exige porta de banco LOCAL
     (`docker exec -i pg-<dev|aceite> psql`, via --porta-banco/TRE_WHATSAPP_PORTA_BANCO) — prefixo
     remoto RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige aprovacao humana registrada; `prod` RECUSA por
     desenho (exit 4). Escrever exige `--confirmo`; sem ele a rodada e' DRY_RUN.

O que ele NAO faz, por desenho (declarado em `lacunas` do contrato): nao envia mensagem nem template,
nao escreve no Odoo (o vocabulario de eventos PG -> Odoo do Data Contract V1 §6 nao tem evento de
mensagem de canal), nao altera `contacts` (dono operacional e' o Odoo; a propagacao do descadastro e' do
W6-E06), nao escreve `recommendations`/`human_approvals`/`outbox_events` (donos W5/W6-E03), nao baixa
midia (so' metadados) e nao usa LLM: a classificacao e' a forca da regra declarada.

Saida: um evento JSON por linha (relatorio opcional) + trilha JSONL opcional; exit code:
  0 = recebido (ou dry-run/replay/recusa registrada) · 2 = uso errado · 3 = recusa (contrato/fonte/
  guarda/banco) · 4 = producao recusada · 5 = segredo vazado.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone

VERSAO = "whatsapp-lead-v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "whatsapp-lead-v1.json")
AMBIENTES = ("dev", "homolog", "prod")
CODIGO_SEGREDO = 5

TABELA_CONTATOS = "sales_intelligence.contacts"
TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_TRILHA = "sales_intelligence.sync_events"

# Guarda de ambiente: em dev a porta de banco e' `docker exec -i <container local> psql ...`
# (mesmo padrao medido nos cards irmaos W6-E05 e W7-E01). Prefixo remoto (ssh ... psql) e' recusado.
RE_CONTAINER_LOCAL = re.compile(r"^docker\s+exec\s+-i\s+(pg-[A-Za-z0-9._-]+)\s+psql\b")
CONTAINERS_LOCAIS_DEV = re.compile(r"^pg-(sales|site|captura|inbound|whatsapp)[A-Za-z0-9._-]*$")


class Recusa(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe


# --------------------------------------------------------------------------------------------
# 0. Utilidades (PII mascarada na evidencia; nada de valor cru na saida)
# --------------------------------------------------------------------------------------------
def mascarar(valor):
    if not valor:
        return ""
    return valor[:2] + "*" * max(0, len(valor) - 2) if len(valor) > 6 else "<oculta>"


def mascarar_telefone(numero):
    if not numero:
        return ""
    digitos = so_digitos(numero)
    return ("*" * max(0, len(digitos) - 2)) + digitos[-2:] if digitos else "<oculta>"


def texto(valor) -> str:
    return ("" if valor is None else str(valor)).strip()


def q(valor) -> str:
    """Literal SQL seguro (somente string/NULL) — nao existe interpolacao crua de valor."""
    if valor is None:
        return "NULL"
    return "'" + str(valor).replace("'", "''") + "'"


def so_digitos(valor):
    return re.sub(r"[^0-9]", "", valor or "")


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def nucleo_telefone(valor):
    """Nucleo nacional do telefone: digitos, sem o codigo do pais 55, ultimos 11 digitos.

    Regra DECLARADA (nao heuristica): o mesmo numero chega como `+55 11 98888-7777`, `11988887777`
    ou `5511988887777`; os tres tem o MESMO nucleo `11988887777`. Numero com menos de 10 digitos nao
    tem DDD suficiente e devolve "" — o que nao casa nunca e' resolvido por adivinhacao.
    """
    digitos = so_digitos(valor)
    if len(digitos) >= 12 and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) < 10:
        return ""
    return digitos[-11:]


def ler_instante(valor):
    """Instante ISO-8601 do provedor; devolve None quando ausente/invalido (nunca inventa hora)."""
    bruto = texto(valor)
    if not bruto:
        return None
    try:
        instante = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
    except ValueError:
        return None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc)


def instante_sql(valor):
    instante = ler_instante(valor)
    return "NULL" if instante is None else "'" + instante.isoformat() + "'"


# --------------------------------------------------------------------------------------------
# 1. Fonte: invariantes verificaveis no proprio arquivo (invariante 5)
# --------------------------------------------------------------------------------------------
def padroes_de_escrita_proibida() -> dict:
    """Montados em tempo de execucao (literais escritos direto nao se achariam a si mesmos)."""
    return {
        "sql_de_escrita_crua": ["DE" + "LETE FROM", "UP" + "DATE ", "TRUN" + "CATE", "DR" + "OP ",
                                "AL" + "TER TABLE"],
    }


def auditar_fonte(caminho=None) -> list:
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
        # comeca com aspas (mesmo defeito ja' medido no card irmao W7-E01).
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
    if ("sub" + "process.run") in corpo and ("shell" + "=True") in corpo:
        violacoes.append({"padrao": "shell_aberto", "alvo": "a porta de banco roda sem shell"})
    return violacoes


# --------------------------------------------------------------------------------------------
# 2. Contrato (vocabulario, regras e janela) — declarado, nunca inventado
# --------------------------------------------------------------------------------------------
def carregar_contrato(caminho=None) -> dict:
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
    for campo in ("channel", "direction", "interaction_type", "tipo_mensagem", "response_category",
                  "intent", "sentiment", "status_trilha", "proximo_passo"):
        valores = vocab.get(campo)
        if not isinstance(valores, list) or not valores:
            falhas.append(f"vocabulario.{campo} ausente/vazio")
        elif len(set(valores)) != len(valores):
            falhas.append(f"vocabulario.{campo} com repeticao")
    for obrigatorio in ("RECEBIDO", "JA_RECEBIDO", "SEM_VINCULO", "REVIEW_REQUIRED",
                        "BLOQUEADO_POR_BLOQUEIO"):
        if obrigatorio not in (vocab.get("status_trilha") or []):
            falhas.append(f"status_trilha sem o estado obrigatorio {obrigatorio}")
    for obrigatorio in ("RESPOSTA_LIVRE_SUGERIDA", "REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA",
                        "NENHUM_FILA_HUMANA"):
        if obrigatorio not in (vocab.get("proximo_passo") or []):
            falhas.append(f"proximo_passo sem o estado obrigatorio {obrigatorio}")
    evento = contrato.get("evento") or {}
    obrigatorios = evento.get("campos_obrigatorios") or []
    for exigido in ("message_id", "recebido_em", "remetente.telefone", "mensagem.tipo"):
        if exigido not in obrigatorios:
            falhas.append(f"evento.campos_obrigatorios sem {exigido}")
    regras = contrato.get("regras") or []
    if not isinstance(regras, list) or not regras:
        falhas.append("regras ausentes/vazias (sem regra declarada a classificacao seria inventada)")
    else:
        ordens = []
        categorias = set()
        for regra in regras:
            if not isinstance(regra, dict):
                falhas.append(f"regra nao e' objeto: {regra}")
                continue
            categoria = texto(regra.get("categoria")).upper()
            if categoria not in (vocab.get("response_category") or []):
                falhas.append(f"regra com categoria fora do vocabulario: {categoria}")
            if regra.get("fallback") is not True:
                categorias.add(categoria)
                if not (regra.get("deteccao") or {}).get("padroes"):
                    falhas.append(f"regra {categoria} sem padroes declarados")
            ordens.append(regra.get("ordem"))
            try:
                confianca = float(regra.get("confianca"))
            except (TypeError, ValueError):
                falhas.append(f"regra {categoria} sem confianca numerica")
                continue
            if not 0 <= confianca <= 1:
                falhas.append(f"regra {categoria} com confianca fora de [0,1]: {confianca}")
        if len(ordens) != len(set(ordens)):
            falhas.append("regras com ordem repetida (a precedencia ficaria ambigua)")
        if "OPT_OUT" not in categorias:
            falhas.append("regras sem a categoria OPT_OUT (barreira de descadastro ausente)")
        elif min(r.get("ordem", 99) for r in regras
                 if texto(r.get("categoria")).upper() == "OPT_OUT") != 1:
            falhas.append("OPT_OUT nao e' a regra de ordem 1 (descadastro tem de vencer o interesse)")
    janela = contrato.get("janela_de_atendimento") or {}
    try:
        minutos = int(janela.get("minutos"))
    except (TypeError, ValueError):
        falhas.append("janela_de_atendimento.minutos ausente/nao numerico")
        minutos = None
    if minutos is not None and minutos <= 0:
        falhas.append(f"janela_de_atendimento.minutos fora de (0,inf): {minutos}")
    ident = contrato.get("identidade") or {}
    if "contacts.whatsapp" not in (ident.get("colunas") or []):
        falhas.append("identidade.colunas sem contacts.whatsapp")
    if not ident.get("digitos_minimos"):
        falhas.append("identidade.digitos_minimos ausente")
    escrita = contrato.get("escrita") or {}
    if sorted(escrita.get("tabelas") or []) != ["interactions", "sync_events"]:
        falhas.append(f"escrita.tabelas fora do escopo declarado: {escrita.get('tabelas')}")
    return falhas


# --------------------------------------------------------------------------------------------
# 3. Configuracao e guardas de ambiente (ADR-005)
# --------------------------------------------------------------------------------------------
class Configuracao:
    def __init__(self, env: dict):
        self.ambiente = (env.get("TRE_AMBIENTE") or "dev").strip().lower()
        self.porta_banco = (env.get("TRE_WHATSAPP_PORTA_BANCO") or "").strip()
        self.aprovacao = (env.get("TRE_WHATSAPP_APROVACAO_HUMANA") or "").strip()
        self.token = env.get("TRE_WHATSAPP_TOKEN") or ""

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
                          "porta de banco obrigatoria (--porta-banco / TRE_WHATSAPP_PORTA_BANCO)"))
    elif config.ambiente == "dev":
        m = RE_CONTAINER_LOCAL.match(config.porta_banco.strip())
        if not m or not CONTAINERS_LOCAIS_DEV.match(m.group(1)):
            problemas.append(("BANCO_NAO_E_DEV",
                              "dev escreve apenas em container local de dev/aceite via docker exec; "
                              f"prefixo recusado: {config.porta_banco}"))
    if config.ambiente == "homolog" and not config.aprovacao:
        problemas.append(("HOMOLOG_SEM_APROVACAO", "homolog exige TRE_WHATSAPP_APROVACAO_HUMANA registrada"))
    return problemas


# --------------------------------------------------------------------------------------------
# 4. Porta de banco (comando declarado; SQL por stdin; resposta JSON)
# --------------------------------------------------------------------------------------------
class PortaBanco:
    """Acesso ao PostgreSQL pelo comando de porta declarado (`docker exec -i <container> psql ...`).

    Sem driver embutido: o SQL vai por argumento e a resposta volta em JSON (`json_agg`) — a mesma
    porta serve ao aceite em container descartavel e ao host da VPS. Sem DDL, sem UPDATE e sem DELETE.
    """

    def __init__(self, prefixo: str, ambiente: str):
        self.prefixo = prefixo
        self.ambiente = ambiente

    def _argumentos(self) -> list:
        try:
            return shlex.split(self.prefixo)
        except ValueError as e:
            raise Recusa("BANCO_NAO_DECLARADO", f"prefixo de banco invalido: {e}") from e

    def _analisar(self, saida: str) -> list:
        """Le a resposta JSON da porta.

        Defeito medido na rodada 1 do aceite: o psql quebra o valor AGREGADO em varias linhas
        (`json_agg` com mais de uma linha sai como `[...}, \\n {...}]`), e ler so' a ultima linha
        derrubava qualquer leitura com 2+ resultados (REVIEW_REQUIRED de telefone ambiguo). O
        contrato da porta e' o DOCUMENTO JSON: aqui as linhas sao reunidas antes de interpretar.
        """
        bruto = (saida or "").strip()
        if not bruto:
            return []
        try:
            return json.loads(bruto)
        except json.JSONDecodeError:
            pass
        reunido = "".join(linha.strip() for linha in bruto.splitlines())
        try:
            return json.loads(reunido)
        except json.JSONDecodeError as e:
            raise Recusa("BANCO_RESPOSTA_INVALIDA",
                         f"a porta de banco nao devolveu JSON: {bruto[:200]}") from e

    def consultar(self, sql: str) -> list:
        comando = self._argumentos() + ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c",
                                        f"SELECT coalesce(json_agg(t), '[]'::json)::text FROM ({sql}) t;"]
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Recusa("BANCO_RECUSOU", f"porta de banco falhou ({proc.returncode}): "
                                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        if not (proc.stdout or "").strip():
            raise Recusa("BANCO_RESPOSTA_VAZIA", "a porta de banco nao devolveu JSON")
        return self._analisar(proc.stdout)

    def executar(self, sql: str) -> list:
        """Escreve (INSERT ... RETURNING) e devolve as linhas afetadas."""
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
        return self._analisar(proc.stdout)


# --------------------------------------------------------------------------------------------
# 5. Evento: leitura e validacao de conteudo
# --------------------------------------------------------------------------------------------
def ler_evento(origem: str) -> dict:
    if origem == "-":
        bruto = sys.stdin.read()
    else:
        if not os.path.isfile(origem):
            raise Recusa("EVENTO_AUSENTE", f"arquivo de evento nao encontrado: {origem}")
        with open(origem, "r", encoding="utf-8") as fh:
            bruto = fh.read()
    try:
        evento = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise Recusa("EVENTO_INVALIDO", f"JSON invalido: {e}") from e
    if not isinstance(evento, dict):
        raise Recusa("EVENTO_INVALIDO", "o evento precisa ser um objeto JSON")
    return evento


def chave_de(evento: dict) -> str:
    return "whatsapp:" + texto(evento.get("message_id"))


def validar_evento(evento: dict, contrato: dict) -> tuple:
    """Devolve (status, motivo, auditoria). status RECEBIVEL significa 'pode seguir'."""
    vocab = contrato.get("vocabulario") or {}
    ident = contrato.get("identidade") or {}
    remetente = evento.get("remetente") if isinstance(evento.get("remetente"), dict) else {}
    mensagem = evento.get("mensagem") if isinstance(evento.get("mensagem"), dict) else {}
    canal = evento.get("canal") if isinstance(evento.get("canal"), dict) else {}
    message_id = texto(evento.get("message_id"))
    tipo = texto(mensagem.get("tipo")).upper()
    telefone = texto(remetente.get("telefone"))
    nucleo = nucleo_telefone(telefone)
    auditoria = {
        "message_id": message_id,
        "tipo_mensagem": tipo,
        "remetente_telefone": mascarar_telefone(telefone),
        "remetente_nome": mascarar(texto(remetente.get("nome_perfil"))),
        "numero_destino": mascarar_telefone(texto(canal.get("numero_destino"))),
        "origem_do_canal": texto(canal.get("origem")),
        "recebido_em": texto(evento.get("recebido_em")),
    }
    if not message_id:
        return ("SEM_DADOS_MINIMOS", "message_id ausente (sem chave nao ha idempotencia)", auditoria)
    if not telefone or len(nucleo) < int(ident.get("digitos_minimos") or 10):
        return ("SEM_DADOS_MINIMOS",
                "remetente.telefone ausente ou sem DDD suficiente (nucleo de "
                f"{ident.get('digitos_minimos')} digitos) — a identidade nao se adivinha", auditoria)
    if ler_instante(evento.get("recebido_em")) is None:
        return ("EVENTO_INVALIDO",
                "recebido_em ausente/invalida: a mensagem nao e' datada por heuristica", auditoria)
    if tipo not in (vocab.get("tipo_mensagem") or []):
        return ("EVENTO_INVALIDO",
                f"mensagem.tipo '{tipo}' fora do vocabulario declarado", auditoria)
    return ("RECEBIVEL", "", auditoria)


# --------------------------------------------------------------------------------------------
# 6. Identidade pelo telefone (nucleo nacional; ambiguidade vai para a fila humana)
# --------------------------------------------------------------------------------------------
def resolver_contato(porta, evento: dict, contrato: dict) -> dict:
    """Devolve {'status': 'VINCULADO'|'SEM_VINCULO'|'REVIEW_REQUIRED', ...}."""
    ident = contrato.get("identidade") or {}
    remetente = evento.get("remetente") if isinstance(evento.get("remetente"), dict) else {}
    nucleo = nucleo_telefone(texto(remetente.get("telefone")))
    minimo = int(ident.get("digitos_minimos") or 10)
    if len(nucleo) < minimo:
        return {"status": "SEM_VINCULO", "contact_id": None, "organization_id": None,
                "bloqueado": False,
                "motivo": f"telefone sem nucleo de {minimo} digitos: nada a resolver na base"}
    colunas = [c for c in (ident.get("colunas") or []) if re.match(r"^contacts\.[a-z_]+$", str(c))]
    if not colunas:
        raise Recusa("CONTRATO_INVALIDO", "identidade.colunas sem coluna de contacts utilizavel")
    condicoes = " OR ".join(
        f"right(regexp_replace(coalesce(c.{c.split('.')[-1]}, ''), '[^0-9]', '', 'g'), 11) = {q(nucleo)}"
        for c in colunas)
    linhas = porta.consultar(
        f"SELECT c.id::text AS id, c.organization_id::text AS organization_id, "
        f"coalesce(c.do_not_contact, false) AS do_not_contact, "
        f"coalesce(c.opt_out_whatsapp, false) AS opt_out_whatsapp "
        f"FROM {TABELA_CONTATOS} c WHERE {condicoes} LIMIT 3")
    if len(linhas) > 1:
        return {"status": "REVIEW_REQUIRED", "contact_id": None, "organization_id": None,
                "bloqueado": False, "nucleo": nucleo,
                "motivo": f"{len(linhas)} contatos casam o mesmo nucleo de telefone — fila humana, "
                          "nunca heuristica"}
    if not linhas:
        return {"status": "SEM_VINCULO", "contact_id": None, "organization_id": None, "bloqueado": False,
                "nucleo": nucleo,
                "motivo": "telefone fora de contacts.phone/contacts.whatsapp: organizacao nao e' inventada"}
    contato = linhas[0]
    bloqueado = bool(contato.get("do_not_contact")) or bool(contato.get("opt_out_whatsapp"))
    motivo = "contato vinculado"
    if contato.get("do_not_contact"):
        motivo = "contato com do_not_contact na base: nenhum proximo passo"
    elif contato.get("opt_out_whatsapp"):
        motivo = "contato com opt_out_whatsapp na base: nenhum proximo passo"
    return {"status": "VINCULADO", "contact_id": contato.get("id"),
            "organization_id": contato.get("organization_id"), "bloqueado": bloqueado,
            "nucleo": nucleo, "motivo": motivo}


# --------------------------------------------------------------------------------------------
# 7. Classificacao por regra declarada (OPT_OUT de ordem 1 vence o interesse)
# --------------------------------------------------------------------------------------------
def classificar(evento: dict, contrato: dict) -> dict:
    vocab = contrato.get("vocabulario") or {}
    mensagem = evento.get("mensagem") if isinstance(evento.get("mensagem"), dict) else {}
    corpo = texto(mensagem.get("texto"))
    tipo = texto(mensagem.get("tipo")).upper()
    regras = sorted((r for r in (contrato.get("regras") or []) if isinstance(r, dict)),
                    key=lambda r: r.get("ordem", 99))
    if not corpo:
        return {"categoria": "INDEFINIDO", "intent": "INDEFINIDO", "sentiment": "NEUTRO",
                "confianca": 0.0,
                "motivo": f"mensagem do tipo {tipo} sem texto: a midia nao e' baixada nem interpretada",
                "evidencia": []}
    for regra in regras:
        if regra.get("fallback") is True:
            continue
        deteccao = regra.get("deteccao") or {}
        casados = []
        for padrao in deteccao.get("padroes") or []:
            if re.search(padrao, corpo, re.IGNORECASE):
                casados.append(padrao)
        if len(casados) >= int(deteccao.get("minimo") or 1):
            return {"categoria": texto(regra.get("categoria")).upper(),
                    "intent": texto(regra.get("intent")).upper(),
                    "sentiment": texto(regra.get("sentiment")).upper(),
                    "confianca": float(regra.get("confianca")),
                    "motivo": f"regra de ordem {regra.get('ordem')}: "
                              f"{texto(regra.get('descricao'))[:120]}",
                    "evidencia": casados}
    padrao = next((r for r in regras if r.get("fallback") is True), None)
    if padrao is None:
        raise Recusa("CONTRATO_INVALIDO", "nenhuma regra casou e o contrato nao declara fallback")
    return {"categoria": texto(padrao.get("categoria") or "INDEFINIDO").upper(),
            "intent": texto(padrao.get("intent") or "INDEFINIDO").upper(),
            "sentiment": texto(padrao.get("sentiment") or "NEUTRO").upper(),
            "confianca": float(padrao.get("confianca") or 0.0),
            "motivo": "nenhuma regra declarada casou (fail-closed em INDEFINIDO)",
            "evidencia": []}


# --------------------------------------------------------------------------------------------
# 8. Janela de atendimento do provedor (24 h desde a ultima entrada do contato)
# --------------------------------------------------------------------------------------------
def ultima_entrada(porta, contact_id, contrato) -> dict:
    if not contact_id:
        return {}
    vocab = contrato.get("vocabulario") or {}
    canal = q(vocab["channel"][0])
    direcao = q(vocab["direction"][0])
    linhas = porta.consultar(
        f"SELECT to_char(max(i.occurred_at), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS ultima "
        f"FROM {TABELA_INTERACOES} i WHERE i.contact_id = {q(contact_id)} "
        f"AND i.channel = {canal} AND i.direction = {direcao}")
    if not linhas or not linhas[0].get("ultima"):
        return {}
    return linhas[0]


def janela_de(porta, evento: dict, contrato: dict, contact_id: str | None) -> dict:
    """Janela de atendimento: aberta no primeiro contato do contato; senao conta da ultima entrada."""
    limite = int((contrato.get("janela_de_atendimento") or {}).get("minutos") or 0)
    recebido = ler_instante(evento.get("recebido_em"))
    anterior = ultima_entrada(porta, contact_id, contrato)
    if not contact_id or not anterior:
        return {"aberta": True, "limite_minutos": limite, "ultima_entrada": None,
                "minutos_desde_ultima": None, "minutos_restantes": limite,
                "motivo": "primeira entrada do contato neste canal: a janela abre com esta mensagem"}
    ultima = ler_instante(anterior.get("ultima"))
    if recebido is None or ultima is None:
        return {"aberta": True, "limite_minutos": limite,
                "ultima_entrada": anterior.get("ultima"), "minutos_desde_ultima": None,
                "minutos_restantes": limite,
                "motivo": "janela nao mensuravel com o que veio: fail-open apenas para leitura humana"}
    decorridos = (recebido - ultima).total_seconds() / 60.0
    restantes = limite - decorridos
    return {"aberta": restantes > 0, "limite_minutos": limite, "ultima_entrada": anterior.get("ultima"),
            "minutos_desde_ultima": round(decorridos, 1), "minutos_restantes": round(restantes, 1),
            "motivo": "janela aberta (resposta livre)" if restantes > 0
                      else "janela fechada: reengajamento exige template + aprovacao humana"}


def proximo_passo_de(identidade: dict, classificacao: dict, janela: dict, contrato: dict) -> str:
    vocab = contrato.get("vocabulario") or {}
    passos = vocab.get("proximo_passo") or []
    nome = {p.upper(): p for p in passos}
    if identidade.get("bloqueado") or classificacao.get("categoria") == "OPT_OUT":
        return nome.get("NENHUM_FILA_HUMANA", "NENHUM_FILA_HUMANA")
    if janela.get("aberta"):
        return nome.get("RESPOSTA_LIVRE_SUGERIDA", "RESPOSTA_LIVRE_SUGERIDA")
    return nome.get("REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA",
                    "REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA")


# --------------------------------------------------------------------------------------------
# 9. Gravacao (INSERT apenas)
# --------------------------------------------------------------------------------------------
def trilha_existente(porta, chave: str) -> dict:
    linhas = porta.consultar(
        f"SELECT entity_id::text AS entity_id, status FROM {TABELA_TRILHA} "
        f"WHERE idempotency_key = {q(chave)} LIMIT 1")
    return linhas[0] if linhas else {}


def gravar_interacao(porta, evento: dict, contrato: dict, identidade: dict,
                     classificacao: dict) -> str:
    """Grava APENAS a interacao.

    Defeito medido na rodada 1 do aceite: esta funcao tambem gravava a trilha em `sync_events` e o
    nucleo gravava a trilha de novo — a chave `whatsapp:<message_id>` e' UNIQUE, entao o segundo
    INSERT estourava e a mensagem terminava em RECUSA com a interacao ja' gravada. Uma mensagem
    tem UMA trilha: quem grava a trilha e' o nucleo (`gravar_trilha`), com o status do veredito.
    """
    vocab = contrato.get("vocabulario") or {}
    mensagem = evento.get("mensagem") if isinstance(evento.get("mensagem"), dict) else {}
    resumo = texto(mensagem.get("texto"))[:400] or f"<{texto(mensagem.get('tipo')).upper()} sem texto>"
    colunas = ("id", "organization_id", "contact_id", "channel", "direction", "interaction_type",
               "occurred_at", "subject", "content_summary", "content_reference", "response_category",
               "intent", "sentiment", "ai_confidence")
    valores = ("gen_random_uuid()", q(identidade.get("organization_id")), q(identidade.get("contact_id")),
               q(vocab["channel"][0]), q(vocab["direction"][0]), q(vocab["interaction_type"][0]),
               instante_sql(evento.get("recebido_em")), q(f"WhatsApp inbound: {classificacao['categoria']}"),
               q(resumo), q(texto(evento.get("message_id"))), q(classificacao["categoria"]),
               q(classificacao["intent"]), q(classificacao["sentiment"]),
               str(float(classificacao["confianca"])))
    linhas = porta.executar(
        f"INSERT INTO {TABELA_INTERACOES} ({', '.join(colunas)}) VALUES ({', '.join(valores)}) "
        "RETURNING id::text AS id")
    if not linhas:
        raise Recusa("GRAVACAO_SEM_RETORNO", "INSERT em interactions nao devolveu id")
    return linhas[0]["id"]


def gravar_trilha(porta, chave: str, status: str, payload: dict, entity_id=None,
                  operation: str = "ENGAJAMENTO_WHATSAPP") -> None:
    porta.executar(
        f"INSERT INTO {TABELA_TRILHA} (id, entity_type, entity_id, source_system, target_system, "
        f"operation, source_version, idempotency_key, status, request_payload, completed_at) VALUES "
        f"(gen_random_uuid(), {q('contact')}, {q(entity_id)}, {q('whatsapp')}, "
        f"{q('sales_intelligence')}, {q(operation)}, {q(VERSAO)}, {q(chave)}, {q(status)}, "
        f"{q(json.dumps(payload, ensure_ascii=False, sort_keys=True))}, NOW())")


# --------------------------------------------------------------------------------------------
# 10. Saida com checagem fail-closed de segredo
# --------------------------------------------------------------------------------------------
class Saida:
    def __init__(self, relatorio, trilha, segredo):
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
                              "detalhe": "o valor de TRE_WHATSAPP_TOKEN apareceu na evidencia; "
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
# 11. Fluxo do lead engajado (nucleo, testavel in-process com uma porta injetada)
# --------------------------------------------------------------------------------------------
def processar(evento: dict, contrato: dict, porta, saida: Saida, escrever: bool) -> dict:
    chave = chave_de(evento)
    status, motivo, auditoria = validar_evento(evento, contrato)
    if status != "RECEBIVEL":
        if escrever:
            gravar_trilha(porta, chave, status, {"motivo": motivo})
        saida.evento(evento="WHATSAPP_RECUSADO", status=status, motivo=motivo,
                     message_id=auditoria.get("message_id"), dados=auditoria)
        return {"status": status, "motivo": motivo, "escrito": False}

    existente = trilha_existente(porta, chave)
    if existente:
        saida.evento(evento="WHATSAPP_REPLAY", status="JA_RECEBIDO",
                     message_id=auditoria.get("message_id"),
                     entity_id=existente.get("entity_id"), dados=auditoria)
        return {"status": "JA_RECEBIDO", "motivo": "mensagem ja' recebida", "escrito": False}

    identidade = resolver_contato(porta, evento, contrato)
    if identidade["status"] == "SEM_VINCULO":
        payload = {"message_id": auditoria.get("message_id"), "nucleo": identidade.get("nucleo"),
                   "motivo": identidade.get("motivo")}
        if escrever:
            gravar_trilha(porta, chave, "SEM_VINCULO", payload)
        saida.evento(evento="WHATSAPP_SEM_VINCULO", status="SEM_VINCULO", motivo=identidade.get("motivo"),
                     message_id=auditoria.get("message_id"), dados=auditoria)
        return {"status": "SEM_VINCULO", "motivo": identidade.get("motivo"), "escrito": False}

    if identidade["status"] == "REVIEW_REQUIRED":
        payload = {"message_id": auditoria.get("message_id"), "nucleo": identidade.get("nucleo"),
                   "motivo": identidade.get("motivo")}
        if escrever:
            gravar_trilha(porta, chave, "REVIEW_REQUIRED", payload)
        saida.evento(evento="WHATSAPP_EM_REVISAO", status="REVIEW_REQUIRED",
                     motivo=identidade.get("motivo"), message_id=auditoria.get("message_id"),
                     dados=auditoria)
        return {"status": "REVIEW_REQUIRED", "motivo": identidade.get("motivo"), "escrito": False}

    classificacao = classificar(evento, contrato)
    janela = janela_de(porta, evento, contrato, identidade.get("contact_id"))
    proximo = proximo_passo_de(identidade, classificacao, janela, contrato)
    bloqueio = identidade.get("bloqueado") or classificacao["categoria"] == "OPT_OUT"
    veredito = "BLOQUEADO_POR_BLOQUEIO" if bloqueio else "RECEBIDO"

    if not escrever:
        saida.evento(evento="DRY_RUN", status="DRY_RUN", message_id=auditoria.get("message_id"),
                     categoria=classificacao["categoria"], proximo_passo=proximo,
                     janela_de_atendimento=janela, dados=auditoria)
        return {"status": "DRY_RUN", "motivo": "sem --confirmo: nada foi gravado", "escrito": False}

    interaction_id = gravar_interacao(porta, evento, contrato, identidade, classificacao)
    if bloqueio:
        payload = {"message_id": auditoria.get("message_id"), "interaction_id": interaction_id,
                   "categoria": classificacao["categoria"], "proximo_passo": proximo,
                   "motivo": "bloqueio de contato/descadastro na base: nenhum proximo passo"}
        gravar_trilha(porta, chave, "BLOQUEADO_POR_BLOQUEIO", payload,
                      entity_id=identidade.get("contact_id"), operation="BLOQUEIO_WHATSAPP")
        saida.evento(evento="WHATSAPP_BLOQUEADO", status="BLOQUEADO_POR_BLOQUEIO",
                     motivo="bloqueio de contato/descadastro: fila humana, nenhum proximo passo",
                     message_id=auditoria.get("message_id"), contact_id=identidade.get("contact_id"),
                     organization_id=identidade.get("organization_id"), interaction_id=interaction_id,
                     categoria=classificacao["categoria"], proximo_passo=proximo,
                     janela_de_atendimento=janela, dados=auditoria)
        return {"status": "BLOQUEADO_POR_BLOQUEIO", "escrito": True, "interaction_id": interaction_id,
                "proximo_passo": proximo, "categoria": classificacao["categoria"]}

    payload = {"message_id": auditoria.get("message_id"), "interaction_id": interaction_id,
               "categoria": classificacao["categoria"], "intent": classificacao["intent"],
               "confianca": classificacao["confianca"], "evidencia": classificacao["evidencia"],
               "proximo_passo": proximo,
               "janela": {"aberta": janela.get("aberta"), "minutos_restantes": janela.get("minutos_restantes")}}
    gravar_trilha(porta, chave, "RECEBIDO", payload, entity_id=identidade.get("contact_id"))
    saida.evento(evento="WHATSAPP_RECEBIDO", status="RECEBIDO", message_id=auditoria.get("message_id"),
                 contact_id=identidade.get("contact_id"),
                 organization_id=identidade.get("organization_id"), interaction_id=interaction_id,
                 categoria=classificacao["categoria"], intent=classificacao["intent"],
                 sentiment=classificacao["sentiment"], confianca=classificacao["confianca"],
                 proximo_passo=proximo, janela_de_atendimento=janela,
                 evidencia=classificacao["evidencia"], dados=auditoria)
    return {"status": "RECEBIDO", "escrito": True, "interaction_id": interaction_id,
            "proximo_passo": proximo, "categoria": classificacao["categoria"]}


# --------------------------------------------------------------------------------------------
# 12. CLI
# --------------------------------------------------------------------------------------------
def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="whatsapp_lead",
                                description="WhatsApp engaged-lead workflow v1 (inbound)")
    p.add_argument("--evento", help="arquivo JSON da mensagem inbound ('-' = stdin)")
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
        env["TRE_WHATSAPP_PORTA_BANCO"] = args.porta_banco
    config = Configuracao(env)

    if args.autoteste:
        violacoes = auditar_fonte()
        print(json.dumps({"auditoria": "fonte", "violacoes": violacoes}, ensure_ascii=False))
        return 0 if not violacoes else 3

    contrato = carregar_contrato(os.environ.get("WHATSAPP_LEAD_CONTRATO"))

    if args.planejar or args.conferir:
        violacoes = auditar_fonte()
        problemas = validar(config) if args.conferir else []
        print(json.dumps({"versao": VERSAO, "config": config.resumo(),
                          "campos_obrigatorios": (contrato.get("evento") or {}).get("campos_obrigatorios"),
                          "limite_da_janela_minutos": (contrato.get("janela_de_atendimento") or {}).get("minutos"),
                          "violacoes_da_fonte": violacoes,
                          "problemas": [{"motivo": m, "detalhe": d} for m, d in problemas]},
                         ensure_ascii=False, indent=2))
        return 0

    if not args.evento:
        print(json.dumps({"motivo": "USO_INCORRETO", "detalhe": "--evento e' obrigatorio"},
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

    evento = ler_evento(args.evento)
    saida = Saida(args.relatorio, args.trilha, config.token)
    porta = PortaBanco(config.porta_banco, config.ambiente)
    try:
        resultado = processar(evento, contrato, porta, saida, escrever=args.confirmo)
    except Recusa as e:
        saida.evento(evento="RECUSA", motivo=e.motivo, detalhe=e.detalhe)
        saida.fechar(e.motivo)
        return 3
    saida.fechar(resultado["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
