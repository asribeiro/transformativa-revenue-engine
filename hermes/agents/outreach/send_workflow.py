#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Envio outbound v1 (`envio-outbound-v1`) — card TRE-W6-E04-T01.

O que este componente E: o ORQUESTRADOR do envio. Ele pega o pedido que o humano aprovou (gravado
pelo gerador do card irmao TRE-W6-E02-T01 e decidido pelo workflow do card TRE-W6-E03-T01), pergunta
ao PORTAO do dono (`approval_workflow.consultar`) se aquele pedido pode ser enviado, monta a mensagem
com o MESMO texto que o hash aprovado carimba, chama o PRIMITIVO de envio de UMA mensagem
(`hermes/integracoes/titan/smtp_titan.py`, titan-smtp-v1) e grava o fato.

O que ele NAO E:
  - nao gera texto (dono: TRE-W6-E02-T01) e nao valida texto de novo (o dono da validacao e o gerador,
    chamado no caminho de edicao do card irmao);
  - nao decide aprovacao nem reimplementa a regra do portao (dono: TRE-W6-E03-T01): status aprovado +
    hash do texto conferido + contato limpo sao lidos de la, nao recalculados aqui;
  - nao fala SMTP: quem fala e o primitivo, com a matriz porta x TLS, a guarda de ambiente, o
    mascaramento de segredo e a idempotencia por chave dele;
  - nao muda estado de pedido de aprovacao: o vocabulario de `human_approvals.status` e fechado.

Desenho do envio (claim exatamente-uma-vez, declarado em politica-envio-v1.json):
  1. o portao do card irmao libera (ou recusa) o pedido;
  2. a chave `envio:<approval_id>:<texto_hash>` e RECLAMADA em `sync_events` com status ENVIANDO, ANTES
     de falar com o SMTP. Chave ja ENVIADA = JA_ENVIADO (nada reenviado); chave ENVIANDO = RECUSADA
     (envio possivelmente entregue nao se repete por cima); chave FALHOU pode ser retentada;
  3. o primitivo envia a mensagem com a MESMA chave (ele tambem e idempotente por chave);
  4. no sucesso: fato em `interactions` (channel/direction/interaction_type declarados) e a chave vira
     ENVIADO com o payload de request/response; na falha: a chave vira FALHOU e NADA entra em
     `interactions`.

Guarda de escrita: so `interactions` e `sync_events`; DDL e DELETE RECUSAM. prod RECUSA (exit 4).

Uso:
  python3 hermes/agents/outreach/send_workflow.py --planejar | --regras
  python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "docker exec -i pg-... psql -U sales_ai -d sales_intelligence" --fila
  python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "..." --enviar <approval_id> [--confirmo] [--env-file deploy/environments/dev-smtp.env] [--trilha /tmp/envio.jsonl]
  python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "..." --desfazer <correlation_id> [--por <operador> --motivo <motivo>] --confirmo
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

VERSAO = "envio-outbound-v1"
AGENTE = "outreach"
PAPEL = "agente_outreach"
WORKFLOW = "outbound-envio"
WORKFLOW_VERSAO = "v1"
CARD = "TRE-W6-E04-T01"

POLITICA_PADRAO = "hermes/agents/outreach/politica-envio-v1.json"
CONTRATO_COMPONENTE_PADRAO = "hermes/agents/outreach/envio-outbound-v1.json"
CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
APROVACAO_MODULO = "hermes/agents/outreach/approval_workflow.py"
SMTP_PRIMITIVO = "hermes/integracoes/titan/smtp_titan.py"

TABELA_INTERACOES = "sales_intelligence.interactions"
TABELA_SYNC = "sales_intelligence.sync_events"
TABELA_APROVACOES = "sales_intelligence.human_approvals"
TABELAS_ESCRITA = (TABELA_INTERACOES, TABELA_SYNC)
TABELAS_LEITURA = (TABELA_APROVACOES, "sales_intelligence.organizations", "sales_intelligence.contacts")

# Vereditos do componente (o vocabulario fechado do CONTRATO e o de human_approvals.status; estes sao
# os vereditos da RODADA de envio, declarados na politica deste card).
PLANO = "PLANO"
ENVIADO = "ENVIADO"
JA_ENVIADO = "JA_ENVIADO"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
DESFEITO = "DESFEITO"
DRY_RUN = "DRY_RUN"
VEREDITOS = (PLANO, ENVIADO, JA_ENVIADO, RECUSADA, ERRO, DESFEITO, DRY_RUN)

MOTIVO_PORTA_DE_BANCO_AUSENTE = "PORTA_DE_BANCO_AUSENTE"
MOTIVO_PEDIDO_NAO_ENCONTRADO = "PEDIDO_NAO_ENCONTRADO"
MOTIVO_PORTAO_NAO_LIBEROU = "PORTAO_NAO_LIBEROU"
MOTIVO_DESTINO_AUSENTE = "DESTINO_AUSENTE"
MOTIVO_DESTINO_NAO_DEV = "DESTINO_NAO_DEV"
MOTIVO_ENVIO_EM_VOO = "ENVIO_EM_VOO"
MOTIVO_HOMOLOG_SEM_APROVACAO = "HOMOLOG_SEM_APROVACAO"
MOTIVO_PRIMITIVO_AUSENTE = "PRIMITIVO_AUSENTE"
MOTIVO_ENVIO_FALHOU = "ENVIO_FALHOU"
MOTIVO_POLITICA_INCOERENTE = "POLITICA_INCOERENTE"
MOTIVO_OPERADOR_AUSENTE = "OPERADOR_AUSENTE"
MOTIVO_MOTIVO_AUSENTE = "MOTIVO_AUSENTE"
MOTIVO_CONFIRMACAO_AUSENTE = "CONFIRMACAO_AUSENTE"
MOTIVO_ORGANIZACAO_AUSENTE = "ORGANIZACAO_AUSENTE"

AMBIENTE_RECUSADO = "prod"
AMBIENTES_PERMITIDOS = ("dev", "homolog")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_POLITICA_RECUSADA = 3
EXIT_RECUSOU_AMBIENTE = 4


class RecusaDePolitica(Exception):
    """Contrato/politica incoerente: RECUSA (fail-closed), nunca 'corrige sozinho'."""


class RecusaDeEscrita(Exception):
    """Escrita fora das duas tabelas, DDL ou DELETE: RECUSA."""


class RecusaDeEnvio(Exception):
    """Pedido que nao pode ser enviado: motivo nomeado, nada enviado e nada gravado."""

    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(f"{motivo}: {detalhe}" if detalhe else motivo)
        self.motivo = motivo
        self.detalhe = detalhe


def agora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def descobrir_raiz_padrao() -> Path:
    atual = Path(__file__).resolve()
    for pai in [atual.parent, *atual.parents]:
        if (pai / "hermes").is_dir() and (pai / "docs").is_dir():
            return pai
    return atual.parents[2]


RAIZ_PADRAO = descobrir_raiz_padrao()


def ler_json(caminho: Path) -> dict:
    with caminho.open(encoding="utf-8") as fh:
        return json.load(fh)


def achar_arquivo(raiz: Path, caminho: str) -> Path:
    p = Path(caminho)
    return p if p.is_absolute() else raiz / p


def carregar_modulo(caminho: Path, nome: str):
    """Importa o modulo DONO por caminho (nao por sys.path: o repo tem varios modulos homonimos)."""
    spec = importlib.util.spec_from_file_location(nome, caminho)
    if spec is None or spec.loader is None:
        raise RecusaDePolitica(f"nao foi possivel carregar {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def carregar_politicas(raiz: Path, caminho_politica: Path, caminho_contrato: Path) -> dict:
    """Le e VALIDA a politica deste card contra o Data Contract. Incoerencia RECUSA."""
    if not caminho_politica.is_file():
        raise RecusaDePolitica(f"politica ausente: {caminho_politica}")
    if not caminho_contrato.is_file():
        raise RecusaDePolitica(f"contrato de dados ausente: {caminho_contrato}")
    politica = ler_json(caminho_politica)
    contrato = ler_json(caminho_contrato)

    for campo in ("nome", "versao", "status", "card", "ambiente", "portao", "primitivo",
                  "interacoes", "idempotencia", "vereditos", "guarda_de_escrita", "desfazer",
                  "composicao"):
        if campo not in politica:
            raise RecusaDePolitica(f"politica sem campo obrigatorio: {campo}")

    vocab = (contrato.get("vocabularies") or {}).get("human_approvals.status")
    if not isinstance(vocab, list) or not vocab:
        raise RecusaDePolitica("contrato sem vocabularies['human_approvals.status']")

    ambiente = politica["ambiente"]
    if ambiente.get("recusado") != AMBIENTE_RECUSADO:
        raise RecusaDePolitica("politica.ambiente.recusado tem de ser 'prod' (ADR-005)")
    if set(ambiente.get("permitidos") or []) != set(AMBIENTES_PERMITIDOS):
        raise RecusaDePolitica(f"politica.ambiente.permitidos divergente de {list(AMBIENTES_PERMITIDOS)}")

    # Os valores gravados em `interactions` sao DECLARADOS: sem eles o fato nasce sem canal/direcao.
    for campo in ("channel", "direction", "interaction_type", "content_reference"):
        if not (politica["interacoes"].get(campo) or "").strip():
            raise RecusaDePolitica(f"politica.interacoes sem {campo}")

    tabelas = set((politica["guarda_de_escrita"] or {}).get("tabelas") or [])
    if tabelas != set(TABELAS_ESCRITA):
        raise RecusaDePolitica(f"politica.guarda_de_escrita.tabelas divergente de {list(TABELAS_ESCRITA)}")
    if (politica["guarda_de_escrita"].get("ddl") or "").split()[0:1] != ["recusado"]:
        raise RecusaDePolitica("politica.guarda_de_escrita.ddl tem de declarar 'recusado'")
    if (politica["guarda_de_escrita"].get("delete") or "").split()[0:1] != ["recusado"]:
        raise RecusaDePolitica("politica.guarda_de_escrita.delete tem de declarar 'recusado'")

    for veredito in (PLANO, ENVIADO, JA_ENVIADO, RECUSADA, ERRO, DESFEITO):
        if veredito not in (politica["vereditos"] or []):
            raise RecusaDePolitica(f"politica.vereditos sem {veredito}")

    idem = politica["idempotencia"]
    if "{approval_id}" not in idem.get("chave", "") and "approval_id" not in idem.get("chave", ""):
        raise RecusaDePolitica("politica.idempotencia.chave sem approval_id")
    for campo in ("status_ok", "status_em_voo", "status_falhou", "status_desfeito"):
        if not idem.get(campo):
            raise RecusaDePolitica(f"politica.idempotencia sem {campo}")

    # O modulo DONO do portao tem de existir e carregar: este componente nao reimplementa a regra.
    modulo_aprovacao = carregar_modulo(achar_arquivo(raiz, APROVACAO_MODULO), "tre_approval_workflow")
    caminho_politica_aprovacao = achar_arquivo(raiz, politica["portao"]["politica"])
    # O portao valida contra o DATA CONTRACT (o vocabulario de status e de la); o contrato do
    # componente do portao e documentacao declarada na politica e nao entra no carregamento.
    try:
        portao = modulo_aprovacao.carregar_politicas(raiz, caminho_politica_aprovacao,
                                                    caminho_contrato)
    except modulo_aprovacao.RecusaDePolitica as recusa:  # pragma: no cover - fail-closed
        raise RecusaDePolitica(f"politica do portao recusada: {recusa}") from recusa

    return {"politica": politica, "contrato": contrato, "modulo_aprovacao": modulo_aprovacao,
            "portao": portao}


# ---------------------------------------------------------------------------------------
# Porta de banco (a mesma forma dos cards irmaos: prefixo de comando que fala psql)
# ---------------------------------------------------------------------------------------
def lit(texto) -> str:
    if texto is None:
        return "NULL"
    return "'" + str(texto).replace("'", "''") + "'"


def lit_json(objeto) -> str:
    return lit(json.dumps(objeto, ensure_ascii=False, sort_keys=True)) + "::jsonb"


def validar_sql(sql: str, permitir_update: bool = False) -> None:
    if sql is None:
        raise RecusaDeEscrita("SQL vazio")
    import re as _re
    if _re.search(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b", sql, _re.IGNORECASE):
        raise RecusaDeEscrita("DDL recusado: nenhum schema muda por aqui (ADR-005)")
    for m in _re.finditer(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-zA-Z_\.]+)", sql, _re.IGNORECASE):
        verbo, tabela = m.group(1).upper(), m.group(2)
        if tabela.lower() not in TABELAS_ESCRITA:
            raise RecusaDeEscrita(f"{verbo} em {tabela}: escrita permitida so em {list(TABELAS_ESCRITA)}")
        if verbo.startswith("DELETE"):
            raise RecusaDeEscrita("DELETE recusado: o desfazer MARCA (sync_events), nao apaga")
        if verbo.startswith("UPDATE") and not permitir_update:
            raise RecusaDeEscrita("UPDATE recusado fora das rodadas de envio/desfazer")


def executar_sql(sql: str, prefixo: str, permitir_update: bool = False) -> tuple[int, str, str]:
    validar_sql(sql, permitir_update=permitir_update)
    comando = shlex.split(prefixo) + ["-v", "ON_ERROR_STOP=1", "-tA", "-F|", "-f", "-"]
    proc = subprocess.run(comando, input=sql, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def linhas_de_json(saida: str) -> list[dict]:
    itens = []
    for linha in saida.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            itens.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return itens


def chave_do_envio(pedido_id: str, texto_hash: str) -> str:
    return f"envio:{pedido_id}:{texto_hash}"


# ---------------------------------------------------------------------------------------
# Leitura: os pedidos APROVADOS e o que ja saiu
# ---------------------------------------------------------------------------------------
def sql_dos_aprovados(status_aprovado: str, pedido_id: str | None = None, limite: int | None = None) -> str:
    campos = ("'id', a.id, 'action_type', a.action_type, 'entity_type', a.entity_type, "
              "'entity_id', a.entity_id, 'decidido_por', a.decided_by, "
              "'decidido_em', to_char(a.decided_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
              "'texto_hash', a.proposed_action->'decisao'->>'texto_hash', "
              "'organization_id', a.proposed_action->>'organization_id', "
              "'sincronizacao_status', s.status, "
              "'enviado_em', to_char(s.completed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
              "'interaction_id', s.response_payload->>'interaction_id'")
    juntas = (f"FROM {TABELA_APROVACOES} a LEFT JOIN {TABELA_SYNC} s ON s.idempotency_key = "
              f"'envio:' || a.id::text || ':' || COALESCE(a.proposed_action->'decisao'->>'texto_hash', '')")
    sql = f"SELECT json_build_object({campos}) {juntas} WHERE a.status = {lit(status_aprovado)}"
    if pedido_id:
        sql += f" AND a.id = {lit(pedido_id)}"
    sql += " ORDER BY a.decided_at ASC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return sql + ";"


def ler_aprovados(carregado: dict, prefixo: str, pedido_id: str | None = None,
                  limite: int | None = None) -> list[dict]:
    status_aprovado = carregado["modulo_aprovacao"].status_do_papel(carregado["portao"],
                                                                   carregado["modulo_aprovacao"].PAPEL_APROVADO)
    rc, saida, erro = executar_sql(sql_dos_aprovados(status_aprovado, pedido_id, limite), prefixo)
    if rc != 0:
        raise RecusaDeEnvio(MOTIVO_PORTA_DE_BANCO_AUSENTE, erro.strip()[:400])
    # Lista VAZIA e resposta legitima (nao ha pedido aprovado); porta muda (rc != 0) ja RECUSOU acima.
    return linhas_de_json(saida)


def ler_claim(prefixo: str, chave: str) -> dict | None:
    sql = (f"SELECT json_build_object('status', s.status, 'tentativas', "
           f"COALESCE((s.request_payload->>'tentativas')::int, 0), "
           f"'interaction_id', s.response_payload->>'interaction_id') "
           f"FROM {TABELA_SYNC} s WHERE s.idempotency_key = {lit(chave)};")
    rc, saida, erro = executar_sql(sql, prefixo)
    if rc != 0:
        raise RecusaDeEnvio(MOTIVO_PORTA_DE_BANCO_AUSENTE, erro.strip()[:400])
    linhas = linhas_de_json(saida)
    return linhas[0] if linhas else None


# ---------------------------------------------------------------------------------------
# Composicao da mensagem: o texto aprovado, literal (nada e inventado aqui)
# ---------------------------------------------------------------------------------------
def montar_mensagem(texto: dict | str) -> tuple[str, str]:
    if isinstance(texto, str):
        return "(sem assunto)", texto
    assunto = (texto.get("assunto") or "").strip() or "(sem assunto)"
    corpo = (texto.get("corpo") or "").strip()
    cta = (texto.get("cta") or "").strip()
    if cta:
        corpo = f"{corpo}\n\n{cta}" if corpo else cta
    return assunto, corpo


def resumo(corpo: str, limite: int = 280) -> str:
    return " ".join(corpo.split())[:limite]


# ---------------------------------------------------------------------------------------
# Ambiente: dev so sai para o sink de dev; homolog exige aprovacao registrada; prod RECUSA
# ---------------------------------------------------------------------------------------
def dominio_de_dev(politica: dict) -> str:
    nome = politica["ambiente"].get("dominio_dev_variavel") or "TRE_TITAN_DOMINIO_DEV"
    return os.environ.get(nome) or politica["ambiente"].get("dominio_dev_padrao") or "dev.local"


def conferir_ambiente(politica: dict, ambiente: str, destino: str) -> None:
    if ambiente == AMBIENTE_RECUSADO:
        raise RecusaDeEnvio("AMBIENTE_RECUSADO", "ADR-005: nada nasce em producao")
    if ambiente not in AMBIENTES_PERMITIDOS:
        raise RecusaDeEnvio("AMBIENTE_INVALIDO", f"ambiente {ambiente!r}")
    if not destino:
        raise RecusaDeEnvio(MOTIVO_DESTINO_AUSENTE, "o pedido nao traz o e-mail do contato")
    if ambiente == "dev":
        dev = dominio_de_dev(politica).lower()
        if not destino.lower().endswith("@" + dev):
            raise RecusaDeEnvio(MOTIVO_DESTINO_NAO_DEV,
                                f"em dev o destino tem de ser do dominio {dev} (sink loopback, ADR-005)")
    else:
        variavel = politica["ambiente"].get("aprovacao_homolog_variavel") or "TRE_ENVIO_APROVACAO_HUMANA"
        if not (os.environ.get(variavel) or "").strip():
            raise RecusaDeEnvio(MOTIVO_HOMOLOG_SEM_APROVACAO,
                                f"homolog exige aprovacao humana registrada em {variavel}")


# ---------------------------------------------------------------------------------------
# O envio
# ---------------------------------------------------------------------------------------
def comando_do_primitivo(caminho: Path, ambiente: str, destino: str, assunto: str, corpo: str,
                         chave: str, env_file: str | None, trilha: str | None) -> list[str]:
    comando = [sys.executable, str(caminho), "--ambiente", ambiente, "--enviar",
               "--para", destino, "--assunto", assunto, "--corpo", corpo,
               "--chave-idempotencia", chave, "--confirmo"]
    if env_file:
        comando += ["--env-file", env_file]
    if trilha:
        comando += ["--registro", trilha]
    return comando


def registrar_fato(carregado: dict, prefixo: str, organizacao: str, contato: str | None, assunto: str,
                   corpo: str, referencia: str, pedido_id: str, chave: str, ambiente: str,
                   correlation_id: str, triggered_by: str, tentativas: int, primitivo: str,
                   trilha: str | None, resposta_primitivo: str) -> dict:
    politica = carregado["politica"]
    inter = politica["interacoes"]
    payload = {"workflow": WORKFLOW, "workflow_versao": WORKFLOW_VERSAO, "componente": VERSAO,
               "ambiente": ambiente, "correlation_id": correlation_id, "triggered_by": triggered_by,
               "approval_id": pedido_id, "texto_hash": chave.rsplit(":", 1)[-1], "tentativas": tentativas,
               "primitivo": primitivo, "primitivo_versao": politica["primitivo"].get("versao")}
    resposta = {"trilha": trilha, "primitivo_saida": resposta_primitivo[-400:]}
    sql = "\n".join([
        "BEGIN;",
        (f"INSERT INTO {TABELA_INTERACOES} (id, organization_id, contact_id, channel, direction, "
         f"interaction_type, occurred_at, subject, content_summary, content_reference) VALUES "
         f"(gen_random_uuid(), {lit(organizacao)}::uuid, {lit(contato)}::uuid, "
         f"{lit(inter['channel'])}, {lit(inter['direction'])}, {lit(inter['interaction_type'])}, NOW(), "
         f"{lit(assunto)}, {lit(resumo(corpo))}, {lit(referencia)}) "
         f"RETURNING json_build_object('interaction_id', id);"),
        (f"UPDATE {TABELA_SYNC} SET status = {lit(politica['idempotencia']['status_ok'])}, "
         f"completed_at = NOW(), response_payload = {lit_json(resposta)} || "
         f"jsonb_build_object('interaction_id', (SELECT id::text FROM {TABELA_INTERACOES} "
         f"WHERE content_reference = {lit(referencia)} ORDER BY created_at DESC LIMIT 1)) "
         f"WHERE idempotency_key = {lit(chave)} AND status = "
         f"{lit(politica['idempotencia']['status_em_voo'])} "
         f"RETURNING json_build_object('sync_event_id', id);"),
        "COMMIT;",
    ])
    rc, saida, erro = executar_sql(sql, prefixo, permitir_update=True)
    if rc != 0:
        raise RecusaDeEnvio("GRAVACAO_FALHOU", erro.strip()[:400])
    itens = linhas_de_json(saida)
    gravados = [i for i in itens if i.get("interaction_id") or i.get("sync_event_id")]
    if not gravados:
        raise RecusaDeEnvio("GRAVACAO_FALHOU", "a transacao nao devolveu o fato gravado")
    fato = {"interaction_id": next((i["interaction_id"] for i in gravados if i.get("interaction_id")), None),
            "sync_event_id": next((i["sync_event_id"] for i in gravados if i.get("sync_event_id")), None),
            "request_payload": payload}
    return fato


def reclamar_chave(politica: dict, prefixo: str, chave: str, pedido_id: str, tentativas: int,
                   correlation_id: str, triggered_by: str) -> str:
    """Claim exatamente-uma-vez: ENVIANDO antes do SMTP. Devolve 'NOVA', 'RETENTATIVA' ou RECUSA."""
    idem = politica["idempotencia"]
    payload = {"workflow": WORKFLOW, "workflow_versao": WORKFLOW_VERSAO, "componente": VERSAO,
               "approval_id": pedido_id, "tentativas": tentativas, "correlation_id": correlation_id,
               "triggered_by": triggered_by, "estado": idem["status_em_voo"]}
    sql = "\n".join([
        "BEGIN;",
        (f"INSERT INTO {TABELA_SYNC} (id, entity_type, entity_id, source_system, target_system, "
         f"operation, source_version, idempotency_key, status, request_payload, created_at) VALUES "
         f"(gen_random_uuid(), {lit(idem['entity_type'])}, {lit(pedido_id)}::uuid, "
         f"{lit(idem['source_system'])}, {lit(idem['target_system'])}, {lit(idem['operation'])}, "
         f"{lit(VERSAO)}, {lit(chave)}, {lit(idem['status_em_voo'])}, {lit_json(payload)}, NOW()) "
         f"ON CONFLICT (idempotency_key) DO NOTHING RETURNING json_build_object('claim', 'NOVA');"),
        (f"UPDATE {TABELA_SYNC} SET status = {lit(idem['status_em_voo'])}, "
         f"request_payload = {lit_json(payload)}, created_at = NOW() "
         f"WHERE idempotency_key = {lit(chave)} AND status = {lit(idem['status_falhou'])} "
         f"RETURNING json_build_object('claim', 'RETENTATIVA');"),
        "COMMIT;",
    ])
    rc, saida, erro = executar_sql(sql, prefixo, permitir_update=True)
    if rc != 0:
        raise RecusaDeEnvio(MOTIVO_PORTA_DE_BANCO_AUSENTE, erro.strip()[:400])
    itens = linhas_de_json(saida)
    if not itens:
        # A chave ja existia e nao estava FALHOU: quem decide e o estado lido.
        atual = ler_claim(prefixo, chave) or {}
        estado = atual.get("status")
        if estado == idem["status_ok"]:
            raise RecusaDeEnvio(JA_ENVIADO, f"chave {chave} ja constava {estado}")
        raise RecusaDeEnvio(MOTIVO_ENVIO_EM_VOO,
                            f"chave {chave} esta {estado}: envio possivelmente entregue nao se repete")
    return itens[0]["claim"]


def enviar(carregado: dict, prefixo: str, ambiente: str, pedido_id: str, correlation_id: str,
           triggered_by: str, confirmar: bool, primitivo: Path, env_file: str | None,
           trilha: str | None) -> dict:
    politica = carregado["politica"]
    modulo_aprovacao = carregado["modulo_aprovacao"]
    try:
        consulta = modulo_aprovacao.consultar(carregado["portao"], prefixo, pedido_id)
    except modulo_aprovacao.RecusaDeDecisao as recusa:
        # pedido inexistente ou ilegivel na porta: o PORTAO nao liberou (nunca "envia assim mesmo")
        raise RecusaDeEnvio(MOTIVO_PORTAO_NAO_LIBEROU, str(recusa)) from recusa
    if not consulta.get("pode_enviar"):
        raise RecusaDeEnvio(MOTIVO_PORTAO_NAO_LIBEROU,
                            consulta.get("motivo") or f"status {consulta.get('status')!r}")
    destino = ((consulta.get("destinatario") or {}).get("email") or "").strip()
    conferir_ambiente(politica, ambiente, destino)
    organizacao = consulta.get("organization_id")
    if not organizacao:
        raise RecusaDeEnvio(MOTIVO_ORGANIZACAO_AUSENTE, "o pedido nao traz organizacao (FK de interactions)")
    contato = (consulta.get("destinatario") or {}).get("contato")
    assunto, corpo = montar_mensagem(consulta.get("texto"))
    texto_hash = consulta.get("texto_hash") or ""
    chave = chave_do_envio(pedido_id, texto_hash)
    if not confirmar:
        return {"veredito": PLANO, "pedido_id": pedido_id, "destino": destino, "assunto": assunto,
                "chave": chave, "texto_hash": texto_hash, "destinatario": consulta.get("destinatario"),
                "confirmar": False,
                "comando": " ".join(comando_do_primitivo(primitivo, ambiente, destino, assunto, corpo,
                                                         chave, env_file, trilha))}

    if not primitivo.is_file():
        raise RecusaDeEnvio(MOTIVO_PRIMITIVO_AUSENTE, f"primitivo ausente: {primitivo}")

    atual = ler_claim(prefixo, chave)
    tentativas = int((atual or {}).get("tentativas") or 0) + 1
    reclamar_chave(politica, prefixo, chave, pedido_id, tentativas, correlation_id, triggered_by)

    comando = comando_do_primitivo(primitivo, ambiente, destino, assunto, corpo, chave, env_file, trilha)
    proc = subprocess.run(comando, capture_output=True, text=True)
    saida = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        marcar_falha(politica, prefixo, chave, f"exit {proc.returncode}: {saida.strip()[-400:]}")
        raise RecusaDeEnvio(MOTIVO_ENVIO_FALHOU, f"o primitivo recusou (exit {proc.returncode})")

    fato = registrar_fato(carregado, prefixo, organizacao, contato, assunto, corpo,
                          politica["interacoes"]["content_reference"].replace("<approval_id>", pedido_id)
                          .replace("<texto_hash>", texto_hash),
                          pedido_id, chave, ambiente, correlation_id, triggered_by, tentativas,
                          str(primitivo), trilha, saida)
    return {"veredito": ENVIADO, "pedido_id": pedido_id, "destino": destino, "assunto": assunto,
            "chave": chave, "texto_hash": texto_hash, "corpo_enviado": corpo, "tentativas": tentativas,
            "interaction_id": fato["interaction_id"], "sync_event_id": fato["sync_event_id"],
            "trilha": trilha}


def marcar_falha(politica: dict, prefixo: str, chave: str, detalhe: str) -> None:
    sql = (f"UPDATE {TABELA_SYNC} SET status = {lit(politica['idempotencia']['status_falhou'])}, "
           f"error_message = {lit(detalhe)}, completed_at = NOW() "
           f"WHERE idempotency_key = {lit(chave)};")
    try:
        executar_sql(sql, prefixo, permitir_update=True)
    except RecusaDeEnvio:  # pragma: no cover - a falha ja e o caminho de erro
        pass


# ---------------------------------------------------------------------------------------
# Fila (o que esta pronto para sair) e desfazer
# ---------------------------------------------------------------------------------------
def fila(carregado: dict, prefixo: str, ambiente: str, limite: int | None = None) -> dict:
    politica = carregado["politica"]
    itens = []
    for pedido in ler_aprovados(carregado, prefixo, limite=limite):
        entrada = {"pedido_id": pedido["id"], "organization_id": pedido.get("organization_id"),
                   "decidido_por": pedido.get("decidido_por"), "sincronizacao": pedido.get("sincronizacao_status")}
        if pedido.get("sincronizacao_status") == politica["idempotencia"]["status_ok"]:
            entrada["elegivel"] = False
            entrada["veredito"] = JA_ENVIADO
        else:
            entrada["elegivel"] = True
            entrada["veredito"] = "ELEGIVEL"
        itens.append(entrada)
    return {"veredito": "FILA", "ambiente": ambiente, "pedidos": itens,
            "elegiveis": sum(1 for i in itens if i["elegivel"])}


def desfazer(carregado: dict, prefixo: str, correlation_id: str, confirmar: bool, operador: str | None,
             motivo: str | None) -> dict:
    politica = carregado["politica"]
    idem = politica["idempotencia"]
    if confirmar:
        if not (operador or "").strip():
            raise RecusaDeEnvio(MOTIVO_OPERADOR_AUSENTE, "--desfazer --confirmo exige --por")
        if not (motivo or "").strip():
            raise RecusaDeEnvio(MOTIVO_MOTIVO_AUSENTE, "--desfazer --confirmo exige --motivo")
    alvo = (f"SELECT id FROM {TABELA_SYNC} WHERE status = {lit(idem['status_ok'])} "
            f"AND request_payload->>'correlation_id' = {lit(correlation_id)}")
    if not confirmar:
        sql = f"SELECT json_build_object('seriam_desfeitos', (SELECT count(*) FROM {TABELA_SYNC} s WHERE s.status = {lit(idem['status_ok'])} AND s.request_payload->>'correlation_id' = {lit(correlation_id)}));"
        rc, saida, erro = executar_sql(sql, prefixo)
        if rc != 0:
            raise RecusaDeEnvio(MOTIVO_PORTA_DE_BANCO_AUSENTE, erro.strip()[:400])
        linha = (linhas_de_json(saida) or [{}])[0]
        return {"veredito": DRY_RUN, "seriam_desfeitos": int(linha.get("seriam_desfeitos") or 0)}
    marca = f"DESFEITO_POR:{operador}:{motivo}"
    sql = "\n".join([
        "BEGIN;",
        (f"UPDATE {TABELA_SYNC} SET status = {lit(idem['status_desfeito'])}, error_message = {lit(marca)}, "
         f"completed_at = NOW() WHERE id IN ({alvo}) "
         f"RETURNING json_build_object('desfeito', id);"),
        (f"SELECT json_build_object('desfeitos', (SELECT count(*) FROM {TABELA_SYNC} WHERE "
         f"error_message = {lit(marca)}));"),
        "COMMIT;",
    ])
    rc, saida, erro = executar_sql(sql, prefixo, permitir_update=True)
    if rc != 0:
        return {"veredito": ERRO, "detalhe": erro.strip()[:300]}
    itens = linhas_de_json(saida)
    return {"veredito": DESFEITO,
            "desfeitos": next((int(i["desfeitos"]) for i in itens if "desfeitos" in i), len(itens) - 1),
            "marcados": [i["desfeito"] for i in itens if i.get("desfeito")],
            "marca": marca, "correlation_id": correlation_id}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def comando_planejar(carregado: dict, caminho_politica: Path) -> int:
    politica = carregado["politica"]
    print(f"PLANO {VERSAO} card={CARD} politica={caminho_politica.name}@{politica['versao']}")
    print(f"  ambiente: permitidos={politica['ambiente']['permitidos']} recusado={politica['ambiente']['recusado']}")
    print(f"  portao (dono): {politica['portao']['comando']} em {politica['portao']['dono']}")
    print(f"  primitivo (dono): {politica['primitivo']['versao']} em {politica['primitivo']['dono']}")
    print(f"  escrita: {politica['guarda_de_escrita']['tabelas']} (DDL={politica['guarda_de_escrita']['ddl']}, "
          f"delete={politica['guarda_de_escrita']['delete']})")
    print(f"  idempotencia: chave={politica['idempotencia']['chave']} "
          f"em_voo={politica['idempotencia']['status_em_voo']} ok={politica['idempotencia']['status_ok']} "
          f"falhou={politica['idempotencia']['status_falhou']}")
    print(f"  interacoes: channel={politica['interacoes']['channel']} direction={politica['interacoes']['direction']} "
          f"tipo={politica['interacoes']['interaction_type']}")
    print(f"  vereditos: {politica['vereditos']}")
    print("PLANO_OK: nenhuma conexao foi aberta (nem SMTP, nem banco)")
    return EXIT_OK


def comando_regras(carregado: dict) -> int:
    politica = carregado["politica"]
    print(f"REGRAS {VERSAO} (politica {politica['nome']}@{politica['versao']})")
    for chave in ("composicao", "guardrails" if "guardrails" in politica else "guarda_de_escrita", "desfazer"):
        if chave in politica:
            print(f"  {chave}: {json.dumps(politica[chave], ensure_ascii=False, sort_keys=True)}")
    return EXIT_OK


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSAO} — envio outbound (card {CARD})")
    parser.add_argument("--ambiente", default=None, choices=["dev", "homolog", AMBIENTE_RECUSADO])
    parser.add_argument("--prefixo", default=None, help="porta de banco (ex.: docker exec -i pg-... psql ...)")
    parser.add_argument("--fila", action="store_true", help="o que esta aprovado e pronto para sair")
    parser.add_argument("--enviar", action="append", default=[], help="approval_id a enviar (repetivel)")
    parser.add_argument("--limite", type=int, default=None)
    parser.add_argument("--desfazer", default=None, help="correlation_id das rodadas de envio a desmarcar")
    parser.add_argument("--por", default=None)
    parser.add_argument("--motivo", default=None)
    parser.add_argument("--confirmo", action="store_true")
    parser.add_argument("--env-file", default=None, help="arquivo TRE_TITAN_* (default: ambiente)")
    parser.add_argument("--trilha", default=None, help="trilha append-only do primitivo (JSONL)")
    parser.add_argument("--primitivo", default=None, help="caminho do primitivo SMTP (injecao de teste)")
    parser.add_argument("--relatorio", default=None)
    parser.add_argument("--correlation-id", default=None)
    parser.add_argument("--triggered-by", default="hermes-dev-harness")
    parser.add_argument("--raiz", default=None)
    parser.add_argument("--politica", default=None)
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--planejar", action="store_true")
    parser.add_argument("--regras", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = montar_parser().parse_args(argv)
    raiz = Path(args.raiz).resolve() if args.raiz else RAIZ_PADRAO
    caminho_politica = achar_arquivo(raiz, args.politica or POLITICA_PADRAO)
    caminho_contrato = achar_arquivo(raiz, args.contrato or CONTRATO_DADOS_PADRAO)
    try:
        carregado = carregar_politicas(raiz, caminho_politica, caminho_contrato)
    except RecusaDePolitica as recusa:
        print(f"RECUSADO {MOTIVO_POLITICA_INCOERENTE}: {recusa}", file=sys.stderr)
        return EXIT_POLITICA_RECUSADA
    if args.planejar:
        return comando_planejar(carregado, caminho_politica)
    if args.regras:
        return comando_regras(carregado)
    if args.ambiente is None:
        print("uso: --ambiente dev|homolog (ou --planejar/--regras)", file=sys.stderr)
        return EXIT_USO
    if args.ambiente == AMBIENTE_RECUSADO:
        print("RECUSADO ambiente prod: ADR-005 (nada nasce em producao)", file=sys.stderr)
        return EXIT_RECUSOU_AMBIENTE
    if args.ambiente not in AMBIENTES_PERMITIDOS:
        print(f"uso: ambiente {args.ambiente!r} invalido", file=sys.stderr)
        return EXIT_USO
    if not args.prefixo:
        print("uso: --prefixo exige a porta de banco", file=sys.stderr)
        return EXIT_USO
    if not args.fila and not args.enviar and not args.desfazer:
        print("uso: escolha --fila, --enviar <approval_id> ou --desfazer <correlation_id>", file=sys.stderr)
        return EXIT_USO
    if args.desfazer and not args.confirmo and not (args.por or args.motivo):
        pass  # dry-run puro

    correlation_id = args.correlation_id or str(uuid.uuid4())
    primitivo = Path(args.primitivo).resolve() if args.primitivo else achar_arquivo(raiz, SMTP_PRIMITIVO)
    relatorio: dict = {"workflow": VERSAO, "card": CARD, "ambiente": args.ambiente,
                       "correlation_id": correlation_id, "iniciado_em": agora(),
                       "triggered_by": args.triggered_by}
    if args.fila:
        try:
            relatorio["fila"] = fila(carregado, args.prefixo, args.ambiente, args.limite)
        except RecusaDeEnvio as recusa:
            relatorio["fila"] = {"veredito": RECUSADA, "motivo": recusa.motivo, "detalhe": recusa.detalhe}
        relatorio["veredito"] = relatorio["fila"]["veredito"]
    if args.enviar:
        resultados = []
        for pedido_id in args.enviar:
            try:
                resultados.append(enviar(carregado, args.prefixo, args.ambiente, pedido_id, correlation_id,
                                        args.triggered_by, args.confirmo, primitivo, args.env_file,
                                        args.trilha))
            except RecusaDeEnvio as recusa:
                resultados.append({"veredito": RECUSADA, "pedido_id": pedido_id,
                                   "motivo": recusa.motivo, "detalhe": recusa.detalhe})
            except Exception as erro:  # pragma: no cover - fail-closed, mas auditado
                resultados.append({"veredito": ERRO, "pedido_id": pedido_id, "detalhe": str(erro)[:300]})
        relatorio["envios"] = resultados
        relatorio["veredito"] = ("ENVIADO" if any(r["veredito"] == ENVIADO for r in resultados)
                                 else (PLANO if any(r["veredito"] == PLANO for r in resultados)
                                       else RECUSADA))
        relatorio["enviados"] = sum(1 for r in resultados if r["veredito"] == ENVIADO)
        relatorio["recusados"] = sum(1 for r in resultados if r["veredito"] == RECUSADA)
    if args.desfazer:
        try:
            relatorio["desfazer"] = desfazer(carregado, args.prefixo, args.desfazer, args.confirmo,
                                             args.por, args.motivo)
        except RecusaDeEnvio as recusa:
            relatorio["desfazer"] = {"veredito": RECUSADA, "motivo": recusa.motivo, "detalhe": recusa.detalhe}
        relatorio["veredito"] = relatorio["desfazer"]["veredito"]
    relatorio["terminado_em"] = agora()
    saida = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
    if args.relatorio:
        Path(args.relatorio).write_text(saida + "\n", encoding="utf-8")
    print(saida)
    if relatorio["veredito"] in (ERRO,):
        return EXIT_FALHOU
    if relatorio.get("recusados"):
        return EXIT_OK if any(r["veredito"] == ENVIADO for r in relatorio.get("envios", [])) else EXIT_FALHOU
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
