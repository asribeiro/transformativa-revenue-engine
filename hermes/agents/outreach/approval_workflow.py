#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Workflow de APROVACAO HUMANA do outbound v1 (`aprovacao-humana-v1`) — card TRE-W6-E03-T01.

O passo `Hermes -> PG: human_approval PENDING` do fluxo outbound (doc 06 §3) termina no card
TRE-W6-E02-T01, que grava o PEDIDO. Este componente implementa o passo SEGUINTE do mesmo fluxo:
a DECISAO humana sobre o pedido — notificacao, decisao registrada (`decided_by`/`decided_at`/
`decision_notes`), edicao do texto pelo humano e o portao que o envio (W6-E04) tem de consultar.

Regras que este componente NAO negocia (doc 12 §4/§8, ADR-0004 e ADR-005):

* quem decide e HUMANO NOMEADO e autorizado — nao existe decisao sem operador, nem decisao
  atribuida a maquina (os nomes de maquina RECUSAM, fail-closed);
* vocabulario fechado: os estados sao LIDOS do Data Contract (`vocabularies['human_approvals.status']`);
  a politica declara o PAPEL de cada estado e o mapeamento verbo -> estado, nunca inventa estado novo;
* estado terminal nao se reescreve: decidir um pedido ja decidido com o MESMO voto e replay
  (JA_DECIDIDO, nada escrito); com voto DIFERENTE e CONFLITO e RECUSA;
* edicao do humano passa pela MESMA validacao deterministica do gerador (fato sustentado,
  citacao, limites, afirmacoes proibidas): o humano nao pode aprovar um fato que a evidencia
  nao sustenta — a edicao nao e porta dos fundos da alucinacao;
* compliance em vigor na HORA DA DECISAO: contato que virou `do_not_contact`/`opt_out_*` depois
  do pedido, ou recomendacao que deixou de estar aberta, RECUSAM a aprovacao;
* nada e enviado: o envio e o W6-E04, e ele so pode enviar o que o portao `--consultar` liberar.

Vereditos: NOTIFICADO / JA_NOTIFICADO / APROVADO / EDITADO / REJEITADO / EXPIRADO / JA_DECIDIDO /
RECUSADA / ERRO. Escrita: SO `sales_intelligence.human_approvals` (UPDATE de decisao) e
`sales_intelligence.agent_runs` (auditoria de cada rodada). Nenhum DDL, nenhuma outra tabela.

Uso:
  python3 hermes/agents/outreach/approval_workflow.py --planejar
  python3 hermes/agents/outreach/approval_workflow.py --regras
  python3 hermes/agents/outreach/approval_workflow.py --ambiente dev --prefixo '<porta psql>' [--fila] [--notificacoes arq]
  python3 hermes/agents/outreach/approval_workflow.py --ambiente dev --prefixo '<porta>' --decidir <id> --decisao aprovar --por "Anderson Ribeiro" [--nota ...]
  python3 hermes/agents/outreach/approval_workflow.py --ambiente dev --prefixo '<porta>' --decidir <id> --decisao editar --por "..." --edicao texto.json
  python3 hermes/agents/outreach/approval_workflow.py --ambiente dev --prefixo '<porta>' --expirar
  python3 hermes/agents/outreach/approval_workflow.py --ambiente dev --prefixo '<porta>' --consultar <id>
  python3 hermes/agents/outreach/approval_workflow.py --ambiente dev --prefixo '<porta>' --desfazer <correlation_id> [--confirmo --por ... --motivo ...]

Exit: 0 OK/replay · 1 falha ou recusa de decisao · 2 uso · 3 politica/contrato incoerente ou
operador nao autorizado · 4 ambiente recusado (prod).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

AGENTE = "outreach"
PAPEL = "agente_outreach"
VERSAO = "1.0.0"
WORKFLOW = "outbound-aprovacao"
WORKFLOW_VERSAO = "v1"

APROVACAO_VERSION = "aprovacao-humana-v1"

POLITICA_PADRAO = "hermes/agents/outreach/politica-aprovacao-v1.json"
CONTRATO_COMPONENTE_PADRAO = "hermes/agents/outreach/aprovacao-humana-v1.json"
CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"

TABELA_APROVACOES = "sales_intelligence.human_approvals"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"
TABELAS_ESCRITA = (TABELA_APROVACOES, TABELA_AGENT_RUNS)

TABELAS_LEITURA = ("sales_intelligence.organizations", "sales_intelligence.contacts",
                   "sales_intelligence.recommendations", "sales_intelligence.scores",
                   "sales_intelligence.signals", "sales_intelligence.pain_hypotheses",
                   "sales_intelligence.research_runs", "sales_intelligence.interactions",
                   "sales_intelligence.sync_events", "sales_intelligence.outbox_events")

# Papeis de estado (o VALOR de cada um vem da politica, validado contra o contrato)
PAPEL_PENDENTE = "pendente"
PAPEL_APROVADO = "aprovado"
PAPEL_REJEITADO = "rejeitado"
PAPEL_EXPIRADO = "expirado"
PAPEIS_DE_ESTADO = (PAPEL_PENDENTE, PAPEL_APROVADO, PAPEL_REJEITADO, PAPEL_EXPIRADO)

# Atos declarados na politica (de qual estado cada um e permitido)
ATO_DECISAO = "decisao"
ATO_EXPIRACAO = "expiracao"
ATO_REVERSAO = "reversao"
ATOS_DECLARADOS = (ATO_DECISAO, ATO_EXPIRACAO, ATO_REVERSAO)

# Vereditos
NOTIFICADO = "NOTIFICADO"
JA_NOTIFICADO = "JA_NOTIFICADO"
APROVADO = "APROVADO"
EDITADO = "EDITADO"
REJEITADO = "REJEITADO"
EXPIRADO = "EXPIRADO"
JA_DECIDIDO = "JA_DECIDIDO"
RECUSADA = "RECUSADA"
ERRO = "ERRO"
VEREDITOS = (NOTIFICADO, JA_NOTIFICADO, APROVADO, EDITADO, REJEITADO, EXPIRADO, JA_DECIDIDO,
             RECUSADA, ERRO)

STATUS_AGENT_RUNS = {
    NOTIFICADO: "COMPLETED",
    JA_NOTIFICADO: "COMPLETED",
    APROVADO: "COMPLETED",
    EDITADO: "COMPLETED",
    REJEITADO: "COMPLETED",
    EXPIRADO: "COMPLETED",
    JA_DECIDIDO: "COMPLETED",
    RECUSADA: "REJECTED",
    ERRO: "FAILED",
}

# Motivos de recusa (vocabulario de motivo, nao de estado)
MOTIVO_PEDIDO_NAO_ENCONTRADO = "PEDIDO_NAO_ENCONTRADO"
MOTIVO_CONFLITO_DE_VOTO = "CONFLITO_DE_VOTO"
MOTIVO_PEDIDO_EXPIRADO = "PEDIDO_EXPIRADO"
MOTIVO_PEDIDO_JA_REJEITADO = "PEDIDO_JA_REJEITADO"
MOTIVO_ESTADO_INESPERADO = "ESTADO_INESPERADO"
MOTIVO_CONFLITO_DE_ESTADO = "CONFLITO_DE_ESTADO"
MOTIVO_OPERADOR_AUSENTE = "OPERADOR_AUSENTE"
MOTIVO_OPERADOR_NAO_AUTORIZADO = "OPERADOR_NAO_AUTORIZADO"
MOTIVO_OPERADOR_NAO_HUMANO = "OPERADOR_NAO_HUMANO"
MOTIVO_CONTATO_BLOQUEADO = "CONTATO_BLOQUEADO"
MOTIVO_RECOMENDACAO_NAO_ABERTA = "RECOMENDACAO_NAO_ABERTA"
MOTIVO_EDICAO_INVALIDA = "EDICAO_INVALIDA"
MOTIVO_EDICAO_AUSENTE = "EDICAO_AUSENTE"
MOTIVO_MOTIVO_AUSENTE = "MOTIVO_AUSENTE"
MOTIVO_POLITICA_INCOERENTE = "POLITICA_INCOERENTE"
MOTIVO_VERBO_INVALIDO = "VERBO_INVALIDO"
MOTIVO_PORTA_AUSENTE = "PORTA_DE_BANCO_AUSENTE"
MOTIVO_MARCADOR_NAO_SUBSTITUIDO = "MARCADOR_DE_NOTIFICACAO_NAO_SUBSTITUIDO"

AMBIENTE_RECUSADO = "prod"
AMBIENTES_PERMITIDOS = ("dev", "homolog")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_POLITICA_RECUSADA = 3
EXIT_RECUSOU_AMBIENTE = 4


class RecusaDeEscrita(Exception):
    pass


class RecusaDePolitica(Exception):
    pass


class RecusaDeDecisao(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        super().__init__(f"{motivo}: {detalhe}" if detalhe else motivo)
        self.motivo = motivo
        self.detalhe = detalhe


def descobrir_raiz_padrao() -> Path:
    atual = Path(__file__).resolve()
    for pai in atual.parents:
        if (pai / "docs" / "data" / "data_contract_v1.json").is_file():
            return pai
    return atual.parents[3]


RAIZ_PADRAO = descobrir_raiz_padrao()


# ---------------------------------------------------------------------------------------
# Politica, contrato e o modulo IRMAO do gerador (o dono da guarda de contato e da validacao)
# ---------------------------------------------------------------------------------------
def ler_json(caminho: Path) -> dict:
    with caminho.open(encoding="utf-8") as fh:
        return json.load(fh)


def achar_arquivo(raiz: Path, caminho: str) -> Path:
    p = Path(caminho)
    return p if p.is_absolute() else (raiz / caminho)


def carregar_modulo_gerador(raiz: Path, politica: dict):
    """Carrega o modulo IRMAO (TRE-W6-E02-T01) por caminho declarado. Ausente = RECUSA (fail-closed)."""
    declarado = ((politica.get("gerador") or {}).get("modulo"))
    if not declarado:
        raise RecusaDePolitica("politica sem gerador.modulo (a validacao da edicao e do irmao)")
    caminho = achar_arquivo(raiz, declarado)
    if not caminho.is_file():
        raise RecusaDePolitica(f"modulo irmao ausente: {caminho}")
    spec = importlib.util.spec_from_file_location("gerador_abordagem_irmao", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def carregar_politicas(raiz: Path, caminho_politica: Path, caminho_contrato: Path) -> dict:
    """Le e VALIDA a politica do card contra o Data Contract e contra a politica IRMA do gerador.

    Qualquer incoerencia RECUSA (fail-closed): politica nao e sugestao, e contrato do componente.
    """
    if not caminho_politica.is_file():
        raise RecusaDePolitica(f"politica ausente: {caminho_politica}")
    if not caminho_contrato.is_file():
        raise RecusaDePolitica(f"contrato de dados ausente: {caminho_contrato}")
    politica = ler_json(caminho_politica)
    contrato = ler_json(caminho_contrato)
    vocab = contrato.get("vocabularies") or {}
    status_do_contrato = vocab.get("human_approvals.status")
    if not isinstance(status_do_contrato, list) or not status_do_contrato:
        raise RecusaDePolitica("contrato sem vocabularies['human_approvals.status']")

    for campo in ("nome", "versao", "status", "verbos", "operadores_autorizados",
                  "padroes_nao_humanos", "transicoes", "notificacao", "gerador", "ttl",
                  "guarda_de_escrita"):
        if campo not in politica:
            raise RecusaDePolitica(f"politica sem campo obrigatorio: {campo}")

    # (1) os papeis de estado cobrem TODOS os estados do contrato e nao inventam estado
    mapa_status = politica["status"]
    for papel in PAPEIS_DE_ESTADO:
        valor = mapa_status.get(papel)
        if not valor:
            raise RecusaDePolitica(f"politica.status sem o papel {papel!r}")
        if valor not in status_do_contrato:
            raise RecusaDePolitica(f"status da politica fora do vocabulario do contrato: {valor!r}")
    estados_declarados = set(mapa_status.values())
    buracos = sorted(set(status_do_contrato) - estados_declarados)
    if buracos:
        raise RecusaDePolitica(f"estado do contrato sem papel declarado na politica: {buracos}")
    if len(estados_declarados) != len(status_do_contrato):
        raise RecusaDePolitica("politica.status tem papel repetido (dois papeis no mesmo estado)")

    # (2) verbos: cada verbo aponta para um PA-PEL do mapa (nunca para um estado solto)
    for verbo, declaracao in politica["verbos"].items():
        if not isinstance(declaracao, dict) or not declaracao.get("status"):
            raise RecusaDePolitica(f"verbo {verbo!r} sem status declarado")
        if declaracao["status"] not in estados_declarados:
            raise RecusaDePolitica(f"verbo {verbo!r} aponta para estado fora do mapa: {declaracao['status']!r}")
        if declaracao["status"] == mapa_status[PAPEL_PENDENTE]:
            raise RecusaDePolitica(f"verbo {verbo!r} nao pode devolver o pedido a {PAPEL_PENDENTE}")

    # (3) transicoes: de qual estado cada ATO e permitido (a lista e declarada, nao adivinhada)
    for ato, permitidos in politica["transicoes"].items():
        if ato not in ATOS_DECLARADOS:
            raise RecusaDePolitica(f"transicao declara ato desconhecido: {ato!r}")
        if not isinstance(permitidos, list) or not permitidos:
            raise RecusaDePolitica(f"transicao de {ato!r} sem estados permitidos")
        for permitido in permitidos:
            if permitido not in estados_declarados:
                raise RecusaDePolitica(f"transicao de {ato!r} cita estado fora do mapa: {permitido!r}")
    for ato in ATOS_DECLARADOS:
        if ato not in politica["transicoes"]:
            raise RecusaDePolitica(f"politica.transicoes sem o ato {ato!r}")

    # (4) operadores: lista nao vazia de nomes; padroes de maquina compilam
    if not politica["operadores_autorizados"]:
        raise RecusaDePolitica("politica sem operador autorizado (nao ha decisao sem humano nomeado)")
    for operador in politica["operadores_autorizados"]:
        if not isinstance(operador, str) or len(operador.strip()) < 3:
            raise RecusaDePolitica(f"operador autorizado invalido: {operador!r}")
    for padrao in politica["padroes_nao_humanos"]:
        try:
            re.compile(padrao)
        except re.error as exc:
            raise RecusaDePolitica(f"padrao de nao-humano invalido {padrao!r}: {exc}") from exc

    # (5) TTL
    ttl = politica["ttl"]
    if not isinstance(ttl.get("horas"), (int, float)) or float(ttl["horas"]) <= 0:
        raise RecusaDePolitica(f"politica.ttl.horas invalido: {ttl.get('horas')!r}")

    # (6) notificacao: arquivo existe e traz TODOS os marcadores declarados
    notificacao = politica["notificacao"]
    if not notificacao.get("marcadores"):
        raise RecusaDePolitica("politica.notificacao sem marcadores declarados")
    caminho_template = achar_arquivo(raiz, notificacao.get("arquivo") or "")
    if not caminho_template.is_file():
        raise RecusaDePolitica(f"template de notificacao ausente: {caminho_template}")
    texto_template = caminho_template.read_text(encoding="utf-8")
    for marcador in notificacao["marcadores"]:
        if f"{{{{{marcador}}}}}" not in texto_template:
            raise RecusaDePolitica(f"template de notificacao sem o marcador {marcador!r}")
    if not notificacao.get("canal"):
        raise RecusaDePolitica("politica.notificacao sem canal declarado")

    # (7) o modulo irmao e a politica irma carregam e VALIDAM juntos (dono da guarda e da validacao)
    gerador = carregar_modulo_gerador(raiz, politica)
    caminho_politica_irma = achar_arquivo(raiz, (politica["gerador"].get("politica") or ""))
    if not caminho_politica_irma.is_file():
        raise RecusaDePolitica(f"politica irma ausente: {caminho_politica_irma}")
    try:
        politica_irma, _, acoes_do_contrato, _ = gerador.carregar_politica(
            raiz, caminho_politica_irma, caminho_contrato)
    except gerador.RecusaDePolitica as recusa:
        raise RecusaDePolitica(f"politica irma recusada: {recusa}") from recusa

    tabelas = set((politica["guarda_de_escrita"] or {}).get("tabelas") or [])
    if tabelas != set(TABELAS_ESCRITA):
        raise RecusaDePolitica(f"politica.guarda_de_escrita.tabelas divergente de {list(TABELAS_ESCRITA)}")

    return {"politica": politica, "contrato": contrato, "status_do_contrato": status_do_contrato,
            "gerador": gerador, "politica_irma": politica_irma, "acoes_do_contrato": acoes_do_contrato}


def status_do_papel(carregado: dict, papel: str) -> str:
    return carregado["politica"]["status"][papel]


def acao_do_contrato(carregado: dict, action_type: str) -> dict | None:
    for acao in carregado["politica_irma"]["acoes_de_abordagem"]:
        if acao["acao"] == action_type:
            return acao
    return None


# ---------------------------------------------------------------------------------------
# Operador humano (fail-closed: sem humano nomeado e autorizado nao ha decisao)
# ---------------------------------------------------------------------------------------
def validar_operador(carregado: dict, operador: str | None) -> str:
    politica = carregado["politica"]
    nome = (operador or "").strip()
    if not nome:
        raise RecusaDePolitica(f"{MOTIVO_OPERADOR_AUSENTE}: --por e obrigatorio (quem decide e humano "
                               f"nomeado, doc 12 §8)")
    for padrao in politica["padroes_nao_humanos"]:
        if re.search(padrao, nome, re.IGNORECASE):
            raise RecusaDePolitica(f"{MOTIVO_OPERADOR_NAO_HUMANO}: {nome!r} casa o padrao de maquina "
                                   f"{padrao!r}")
    autorizados = {a.casefold(): a for a in politica["operadores_autorizados"]}
    if nome.casefold() not in autorizados:
        raise RecusaDePolitica(f"{MOTIVO_OPERADOR_NAO_AUTORIZADO}: {nome!r} nao esta em "
                               f"operadores_autorizados")
    # registra o nome CANONICO da politica (a mesma pessoa nao vira dois operadores por caixa alta)
    return autorizados[nome.casefold()]


# ---------------------------------------------------------------------------------------
# Leitura da fila e do pedido
# ---------------------------------------------------------------------------------------
def lit(texto) -> str:
    return "'" + str(texto).replace("'", "''") + "'"


def lit_json(objeto) -> str:
    return lit(json.dumps(objeto, ensure_ascii=False, sort_keys=True))


CAMPOS_DO_PEDIDO = (
    "'id', a.id, 'action_type', a.action_type, 'entity_type', a.entity_type, 'entity_id', a.entity_id, "
    "'requested_by', a.requested_by, 'status', a.status, "
    "'requested_at', to_char(a.requested_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
    "'decidido_em', to_char(a.decided_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
    "'idade_horas', round(extract(epoch FROM (NOW() - a.requested_at)) / 3600.0, 2), "
    "'proposed_action', a.proposed_action, 'decided_by', a.decided_by, 'decision_notes', a.decision_notes, "
    "'organizacao', COALESCE(o.trade_name, o.legal_name), 'organizacao_id', o.id, "
    "'contato', c.full_name, 'contato_email', c.email, 'contato_canal', c.preferred_channel, "
    "'do_not_contact', COALESCE(c.do_not_contact, false), "
    "'opt_out_email', COALESCE(c.opt_out_email, false), "
    "'opt_out_whatsapp', COALESCE(c.opt_out_whatsapp, false), "
    "'recomendacao_status', r.status"
)

JUNCOES_DO_PEDIDO = (
    f"FROM {TABELA_APROVACOES} a "
    f"LEFT JOIN sales_intelligence.organizations o ON o.id = (a.proposed_action->>'organization_id')::uuid "
    f"LEFT JOIN sales_intelligence.contacts c ON c.id = a.entity_id "
    f"LEFT JOIN sales_intelligence.recommendations r ON r.id = (a.proposed_action->>'recommendation_id')::uuid"
)


def sql_da_fila(carregado: dict, limite: int | None = None) -> str:
    pendente = status_do_papel(carregado, PAPEL_PENDENTE)
    sql = (f"SELECT json_build_object({CAMPOS_DO_PEDIDO}) {JUNCOES_DO_PEDIDO} "
           f"WHERE a.status = {lit(pendente)} ORDER BY a.requested_at ASC")
    if limite:
        sql += f" LIMIT {int(limite)}"
    return sql + ";"


def sql_do_pedido(carregado: dict, pedido_id: str) -> str:
    return f"SELECT json_build_object({CAMPOS_DO_PEDIDO}) {JUNCOES_DO_PEDIDO} WHERE a.id = {lit(pedido_id)};"


def sql_das_notificacoes() -> str:
    """Notificacoes ja feitas: achata os arrays de `output->'notificados'` numa lista unica.

    `json_agg(output->'notificados')` produziria uma lista de LISTAS (uma por rodada) e o mapa de
    idempotencia ficaria vazio — foi o que fez a fila renotificar todo mundo na segunda rodada.
    """
    return (f"SELECT json_build_object('notificados', COALESCE(json_agg(elemento), '[]'::json)) "
            f"FROM {TABELA_AGENT_RUNS} r, jsonb_array_elements(r.output->'notificados') AS elemento "
            f"WHERE r.workflow = {lit(WORKFLOW)} AND r.output->>'evento' = {lit('NOTIFICACAO')};")


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


def validar_sql(sql: str, permitir_delete: bool = False) -> None:
    """Guarda de escrita: so as duas tabelas; DDL e DELETE fora do --desfazer RECUSAM."""
    if re.search(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b", sql, re.IGNORECASE):
        raise RecusaDeEscrita("DDL recusado: nada nasce em producao e nenhum schema muda por aqui (ADR-005)")
    for m in re.finditer(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-zA-Z_\.]+)", sql, re.IGNORECASE):
        verbo, tabela = m.group(1).upper(), m.group(2)
        if tabela.lower() not in TABELAS_ESCRITA:
            raise RecusaDeEscrita(f"{verbo} em {tabela}: escrita permitida so em {list(TABELAS_ESCRITA)}")
        if verbo.startswith("DELETE") and not permitir_delete:
            raise RecusaDeEscrita("DELETE recusado: exige --desfazer <correlation_id> --confirmo")


def executar_sql(sql: str, prefixo: str, permitir_delete: bool = False) -> tuple[int, str, str]:
    validar_sql(sql, permitir_delete=permitir_delete)
    comando = shlex.split(prefixo) + ["-v", "ON_ERROR_STOP=1", "-tA", "-F|", "-f", "-"]
    proc = subprocess.run(comando, input=sql, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def ler_pedido(carregado: dict, prefixo: str, pedido_id: str) -> dict:
    rc, saida, erro = executar_sql(sql_do_pedido(carregado, pedido_id), prefixo)
    if rc != 0:
        raise RecusaDeDecisao(MOTIVO_PORTA_AUSENTE, erro.strip()[:400])
    linhas = linhas_de_json(saida)
    if not linhas:
        raise RecusaDeDecisao(MOTIVO_PORTA_AUSENTE,
                              "a porta de banco nao devolveu JSON (o prefixo aponta para psql?)")
    pedido = linhas[0]
    if not pedido.get("id"):
        raise RecusaDeDecisao(MOTIVO_PEDIDO_NAO_ENCONTRADO, f"pedido {pedido_id} inexistente")
    return pedido


def ler_fila(carregado: dict, prefixo: str, limite: int | None = None) -> list[dict]:
    rc, saida, erro = executar_sql(sql_da_fila(carregado, limite), prefixo)
    if rc != 0:
        raise RecusaDeDecisao(MOTIVO_PORTA_AUSENTE, erro.strip()[:400])
    return linhas_de_json(saida)


# ---------------------------------------------------------------------------------------
# Compliance em vigor NA HORA DA DECISAO (a guarda e a do gerador: um dono so)
# ---------------------------------------------------------------------------------------
def contato_bloqueado(carregado: dict, action_type: str, pedido: dict) -> list[str]:
    guarda = carregado["politica_irma"]["guarda_de_contato"]
    bloqueios = list(guarda.get("bloqueios_absolutos") or [])
    acao = acao_do_contrato(carregado, action_type)
    if acao and acao.get("bloqueio_do_canal"):
        bloqueios.append(acao["bloqueio_do_canal"])
    return sorted({b for b in bloqueios if pedido.get(b)})


# ---------------------------------------------------------------------------------------
# Notificacao: a mensagem que o humano le para decidir (sem segredo, com o texto inteiro)
# ---------------------------------------------------------------------------------------
def codigo_curto(pedido_id: str) -> str:
    return "APR-" + str(pedido_id).replace("-", "")[:8]


def texto_do_pedido(proposed_action: dict) -> dict:
    return {"assunto": proposed_action.get("assunto") or "",
            "corpo": proposed_action.get("corpo") or "",
            "cta": proposed_action.get("cta") or ""}


def hash_do_texto(texto: dict) -> str:
    bruto = json.dumps({k: (texto.get(k) or "") for k in ("assunto", "corpo", "cta")},
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def comando_para(carregado: dict, ambiente: str, pedido_id: str, verbo: str) -> str:
    operador = carregado["politica"]["operadores_autorizados"][0]
    return (f"python3 hermes/agents/outreach/approval_workflow.py --ambiente {ambiente} "
            f"--prefixo '<porta>' --decidir {pedido_id} --decisao {verbo} --por \"{operador}\"")


def renderizar_notificacao(carregado: dict, pedido: dict, ambiente: str) -> str:
    politica = carregado["politica"]
    caminho = achar_arquivo(RAIZ_PADRAO, politica["notificacao"]["arquivo"])
    texto = caminho.read_text(encoding="utf-8")
    pa = pedido.get("proposed_action") or {}
    valores = {
        "CODIGO": codigo_curto(pedido["id"]),
        "EMPRESA": pedido.get("organizacao") or "(empresa nao resolvida)",
        "CONTATO": pedido.get("contato") or "(sem contato)",
        "ACAO": pedido.get("action_type") or "",
        "CANAL": pa.get("canal") or "",
        "PEDIDO_ID": pedido["id"],
        "IDADE_HORAS": str(pedido.get("idade_horas") if pedido.get("idade_horas") is not None else "-"),
        "ASSUNTO": pa.get("assunto") or "",
        "CORPO": pa.get("corpo") or "",
        "CTA": pa.get("cta") or "",
        "CITACOES": ", ".join(pa.get("fatos_citados") or []),
        "COMANDO_APROVAR": comando_para(carregado, ambiente, pedido["id"], "aprovar"),
        "COMANDO_REJEITAR": comando_para(carregado, ambiente, pedido["id"], "rejeitar"),
        "COMANDO_EDITAR": comando_para(carregado, ambiente, pedido["id"], "editar"),
        "TTL_HORAS": str(politica["ttl"]["horas"]),
    }
    for marcador in politica["notificacao"]["marcadores"]:
        if marcador not in valores:
            raise RecusaDeDecisao(MOTIVO_MARCADOR_NAO_SUBSTITUIDO,
                                  f"marcador declarado sem valor: {marcador!r} — pedido incompleto "
                                  f"nao vira notificacao")
        texto = texto.replace("{{" + marcador + "}}", str(valores.get(marcador, "")))
    restantes = sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", texto)))
    if restantes:
        raise RecusaDeDecisao(MOTIVO_MARCADOR_NAO_SUBSTITUIDO, f"marcadores sem valor: {restantes}")
    return texto


# ---------------------------------------------------------------------------------------
# Auditoria (agent_runs) — uma linha por rodada
# ---------------------------------------------------------------------------------------
def montar_auditoria(veredito: str, motivo: str, detalhe: str, entrada: dict, saida: dict,
                     organization_id: str | None, correlation_id: str, triggered_by: str,
                     iniciado_em: str, terminado_em: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "agent_name": AGENTE,
        "agent_role": PAPEL,
        "agent_version": VERSAO,
        "workflow": WORKFLOW,
        "workflow_version": WORKFLOW_VERSAO,
        "organization_id": organization_id,
        "triggered_by": triggered_by,
        "correlation_id": correlation_id,
        "input": entrada,
        "output": saida,
        "model": APROVACAO_VERSION,
        "started_at": iniciado_em,
        "finished_at": terminado_em,
        "status": STATUS_AGENT_RUNS[veredito],
        "error": None if veredito not in (RECUSADA, ERRO) else {"motivo": motivo, "detalhe": detalhe},
    }


def sql_da_auditoria(auditoria: dict) -> str:
    a = auditoria
    return (
        f"INSERT INTO {TABELA_AGENT_RUNS} (id, agent_name, agent_role, agent_version, workflow, "
        f"workflow_version, organization_id, triggered_by, correlation_id, input, output, model, "
        f"started_at, finished_at, status, error) VALUES ("
        f"{lit(a['id'])}, {lit(a['agent_name'])}, {lit(a['agent_role'])}, {lit(a['agent_version'])}, "
        f"{lit(a['workflow'])}, {lit(a['workflow_version'])}, "
        f"{lit(a['organization_id']) if a['organization_id'] else 'NULL'}, {lit(a['triggered_by'])}, "
        f"{lit(a['correlation_id'])}::uuid, {lit_json(a['input'])}::jsonb, {lit_json(a['output'])}::jsonb, "
        f"{lit(a['model'])}, {lit(a['started_at'])}::timestamptz, {lit(a['finished_at'])}::timestamptz, "
        f"{lit(a['status'])}, {lit_json(a['error']) if a['error'] else 'NULL'}::jsonb);")


def auditar(auditoria: dict, prefixo: str) -> int:
    rc, _, erro = executar_sql(sql_da_auditoria(auditoria), prefixo)
    if rc != 0:
        print(f"ERRO auditoria nao gravada: {erro.strip()[:300]}", file=sys.stderr)
    return rc


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def entrada_da_rodada(carregado: dict, ambiente: str, extras: dict | None = None) -> dict:
    politica = carregado["politica"]
    base = {"aprovacao_version": APROVACAO_VERSION,
            "politica": f"{politica['nome']}@{politica['versao']}",
            "contrato": (carregado["contrato"].get("contract") or {}).get("version"),
            "vocabulario_de_status": sorted(carregado["status_do_contrato"]),
            "ambiente": ambiente,
            "ttl_horas": politica["ttl"]["horas"],
            "canal_da_notificacao": politica["notificacao"]["canal"],
            "gerador_irmao": (politica["gerador"] or {}).get("modulo")}
    base.update(extras or {})
    return base


# ---------------------------------------------------------------------------------------
# FILA / notificacao
# ---------------------------------------------------------------------------------------
def notificacoes_anteriores(prefixo: str) -> dict[str, str]:
    """Mapa approval_id -> texto_hash ja notificado (idempotencia da notificacao).

    Fail-closed: se a consulta falhar, RECUSA a rodada. Renotificar o mesmo pedido a cada ciclo e
    pior do que nao notificar — o operador receberia a mesma cobranca em loop.
    """
    rc, saida, erro = executar_sql(sql_das_notificacoes(), prefixo)
    if rc != 0:
        raise RecusaDeDecisao(MOTIVO_PORTA_AUSENTE,
                              f"nao consegui ler as notificacoes anteriores: {erro.strip()[:300]}")
    mapa: dict[str, str] = {}
    for linha in linhas_de_json(saida):
        for item in _achatar(linha.get("notificados")):
            if isinstance(item, dict) and item.get("approval_id"):
                mapa[str(item["approval_id"])] = str(item.get("texto_hash") or "")
    return mapa


def _achatar(itens) -> list:
    """Aceita lista de dicts ou lista de listas de dicts (json_agg aninhado nao passa calado)."""
    plano: list = []
    for item in itens or []:
        if isinstance(item, list):
            plano.extend(item)
        else:
            plano.append(item)
    return plano


def rodar_fila(carregado: dict, prefixo: str, ambiente: str, correlation_id: str,
               triggered_by: str, arquivo_notificacoes: str | None) -> dict:
    iniciado = agora()
    pendentes = ler_fila(carregado, prefixo)
    anteriores = notificacoes_anteriores(prefixo)
    mensagens: list[dict] = []
    novos: list[dict] = []
    for pedido in pendentes:
        texto = texto_do_pedido(pedido.get("proposed_action") or {})
        digest = hash_do_texto(texto)
        mensagem = renderizar_notificacao(carregado, pedido, ambiente)
        registro = {"approval_id": pedido["id"], "codigo": codigo_curto(pedido["id"]),
                    "texto_hash": digest, "acao": pedido.get("action_type"),
                    "canal": (pedido.get("proposed_action") or {}).get("canal"),
                    "organizacao": pedido.get("organizacao"), "idade_horas": pedido.get("idade_horas")}
        mensagens.append({**registro, "mensagem": mensagem})
        if anteriores.get(str(pedido["id"])) != digest:
            novos.append(registro)
    veredito = NOTIFICADO if novos else JA_NOTIFICADO
    if not pendentes:
        veredito = JA_NOTIFICADO
    saida = {"evento": "NOTIFICACAO", "canal": carregado["politica"]["notificacao"]["canal"],
             "entrega_externa": bool(carregado["politica"]["notificacao"].get("entrega_externa")),
             "pendentes": len(pendentes), "notificados": novos,
             "ja_notificados": len(pendentes) - len(novos)}
    auditoria = montar_auditoria(veredito, "", "", entrada_da_rodada(carregado, ambiente),
                                 saida, None, correlation_id, triggered_by, iniciado, agora())
    auditar(auditoria, prefixo)
    if arquivo_notificacoes:
        caminho = Path(arquivo_notificacoes)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with caminho.open("w", encoding="utf-8") as fh:
            for mensagem in mensagens:
                fh.write(mensagem["mensagem"])
                fh.write("\n---\n")
    return {"veredito": veredito, "pendentes": len(pendentes), "novos": len(novos),
            "ja_notificados": len(pendentes) - len(novos), "mensagens": mensagens,
            "auditoria_id": auditoria["id"]}


# ---------------------------------------------------------------------------------------
# Decisao
# ---------------------------------------------------------------------------------------
def validar_edicao(carregado: dict, pedido: dict, edicao: dict, prefixo: str) -> dict:
    """A edicao do humano passa pela MESMA validacao deterministica do gerador (irmao)."""
    if not isinstance(edicao, dict):
        raise RecusaDeDecisao(MOTIVO_EDICAO_AUSENTE, "--edicao exige um JSON com assunto/corpo/cta")
    faltando = [c for c in ("assunto", "corpo", "cta") if not isinstance(edicao.get(c), str) or not edicao[c].strip()]
    if faltando:
        raise RecusaDeDecisao(MOTIVO_EDICAO_INVALIDA, f"campos ausentes/vazios: {faltando}")
    pa = pedido.get("proposed_action") or {}
    organization_id = pa.get("organization_id")
    if not organization_id:
        raise RecusaDeDecisao(MOTIVO_EDICAO_INVALIDA, "pedido sem organization_id no proposed_action")
    gerador = carregado["gerador"]
    politica_irma = carregado["politica_irma"]
    try:
        fatos = gerador.ler_fatos(organization_id, politica_irma, prefixo)
    except gerador.RecusaDeAbordagem as recusa:
        raise RecusaDeDecisao(MOTIVO_EDICAO_INVALIDA,
                              f"evidencia nao pode ser reconferida ({recusa.motivo})") from recusa
    evidencia = gerador.montar_evidencia(fatos, politica_irma)
    problemas = gerador.validar_abordagem({"assunto": edicao["assunto"], "corpo": edicao["corpo"],
                                           "cta": edicao["cta"]}, politica_irma, evidencia)
    if problemas:
        raise RecusaDeDecisao(MOTIVO_EDICAO_INVALIDA, "; ".join(problemas[:5]))
    return {"assunto": edicao["assunto"].strip(), "corpo": edicao["corpo"].strip(),
            "cta": edicao["cta"].strip(), "problemas": [],
            "evidencia_ids": [i["id"] for i in evidencia],
            "politica_irma": f"{politica_irma['nome']}@{politica_irma['versao']}"}


def decidir(carregado: dict, prefixo: str, ambiente: str, pedido_id: str, verbo: str,
            operador: str, nota: str | None, edicao: dict | None, correlation_id: str,
            triggered_by: str, confirmar: bool = True) -> dict:
    iniciado = agora()
    politica = carregado["politica"]
    status_pendente = status_do_papel(carregado, PAPEL_PENDENTE)
    declaracao = politica["verbos"].get(verbo)
    organization_id = None
    entrada = entrada_da_rodada(carregado, ambiente, {"pedido_id": pedido_id, "verbo": verbo})
    try:
        if declaracao is None:
            raise RecusaDeDecisao(MOTIVO_VERBO_INVALIDO,
                                  f"{verbo!r} nao e verbo do vocabulario {sorted(politica['verbos'])}")
        novo_status = declaracao["status"]
        revisao = bool(declaracao.get("revisao"))
        pedido = ler_pedido(carregado, prefixo, pedido_id)
        organization_id = pedido.get("organizacao_id") or \
            (pedido.get("proposed_action") or {}).get("organization_id")
        entrada["organization_id"] = organization_id
        entrada["action_type"] = pedido.get("action_type")
        entrada["texto_hash_original"] = hash_do_texto(texto_do_pedido(pedido.get("proposed_action") or {}))

        # (1) o pedido esta no estado de onde a decisao sai?
        permitidos = politica["transicoes"].get("decisao") or []
        if pedido.get("status") != status_pendente:
            if pedido.get("status") == novo_status and (pedido.get("decided_by") or "").casefold() == operador.casefold():
                resultado = {"veredito": JA_DECIDIDO, "pedido_id": pedido_id,
                             "status": pedido.get("status"), "decidido_por": pedido.get("decided_by"),
                             "detalhe": "mesmo voto do mesmo operador: nada reescrito"}
                auditar(montar_auditoria(JA_DECIDIDO, "", resultado["detalhe"], entrada,
                                         {"evento": "DECISAO", "resultado": resultado}, organization_id,
                                         correlation_id, triggered_by, iniciado, agora()), prefixo)
                return resultado
            if pedido.get("status") == status_do_papel(carregado, PAPEL_EXPIRADO):
                raise RecusaDeDecisao(MOTIVO_PEDIDO_EXPIRADO,
                                      "pedido expirado nao volta atras: evidencia nova gera pedido novo")
            if pedido.get("status") == status_do_papel(carregado, PAPEL_REJEITADO):
                raise RecusaDeDecisao(MOTIVO_PEDIDO_JA_REJEITADO,
                                      "pedido ja rejeitado nao se reabre por aqui")
            if pedido.get("status") in permitidos:
                raise RecusaDeDecisao(MOTIVO_ESTADO_INESPERADO, f"estado atual: {pedido.get('status')!r}")
            raise RecusaDeDecisao(MOTIVO_CONFLITO_DE_VOTO,
                                  f"pedido ja esta {pedido.get('status')!r} (operador "
                                  f"{(pedido.get('decided_by') or '?')!r})")

        # (2) compliance em vigor na hora da decisao (guarda do gerador)
        if novo_status != status_do_papel(carregado, PAPEL_REJEITADO):
            bloqueios = contato_bloqueado(carregado, pedido.get("action_type") or "", pedido)
            if bloqueios:
                raise RecusaDeDecisao(MOTIVO_CONTATO_BLOQUEADO,
                                      f"contato bloqueado por {bloqueios} (compliance nao expira)")
            if pedido.get("recomendacao_status") != "OPEN":
                raise RecusaDeDecisao(MOTIVO_RECOMENDACAO_NAO_ABERTA,
                                      f"recomendacao {pedido.get('recomendacao_status')!r}: a base do "
                                      f"pedido nao esta mais aberta")

        # (3) texto decidido: aprovacao simples carimba o hash; edicao revalida pelo gerador
        pa = dict(pedido.get("proposed_action") or {})
        texto_original = texto_do_pedido(pa)
        revisado = False
        validada: dict | None = None
        if revisao:
            validada = validar_edicao(carregado, pedido, edicao or {}, prefixo)
            pa.update({"assunto": validada["assunto"], "corpo": validada["corpo"], "cta": validada["cta"]})
            revisado = True
        texto_final = texto_do_pedido(pa)
        pa["decisao"] = {
            "por": operador, "em": iniciado, "nota": nota, "revisado": revisado,
            "texto_hash": hash_do_texto(texto_final),
            "texto_hash_original": hash_do_texto(texto_original),
            "texto_original": texto_original if revisado else None,
            "validacao": ({"problemas": [], "politica_irma": validada["politica_irma"]} if revisado else None),
        }
        resultado = {"veredito": EDITADO if revisado else (APROVADO if novo_status
                                                           == status_do_papel(carregado, PAPEL_APROVADO)
                                                           else REJEITADO),
                     "pedido_id": pedido_id, "status": novo_status, "decidido_por": operador,
                     "nota": nota, "revisado": revisado, "texto_hash": pa["decisao"]["texto_hash"],
                     "organization_id": organization_id}
        if not confirmar:
            resultado["veredito"] = "PLANEJADO"
            return resultado

        # (4) escrita: UPDATE condicionado ao estado PENDING + auditoria, na MESMA transacao
        sql = "\n".join([
            "BEGIN;",
            (f"UPDATE {TABELA_APROVACOES} SET status = {lit(novo_status)}, decided_at = NOW(), "
             f"decided_by = {lit(operador)}, decision_notes = "
             f"{lit(nota) if nota else 'NULL'}, proposed_action = {lit_json(pa)}::jsonb "
             f"WHERE id = {lit(pedido_id)} AND status = {lit(status_pendente)};"),
            (f"SELECT json_build_object('atualizados', (SELECT count(*) FROM {TABELA_APROVACOES} "
             f"WHERE id = {lit(pedido_id)} AND status = {lit(novo_status)} AND decided_by = {lit(operador)}), "
             f"'pendentes_do_contato', (SELECT count(*) FROM {TABELA_APROVACOES} WHERE status = "
             f"{lit(status_pendente)} AND entity_id = {lit(pedido.get('entity_id'))}));"),
            sql_da_auditoria(montar_auditoria(resultado["veredito"], "", "", entrada,
                                              {"evento": "DECISAO", "resultado": resultado},
                                              organization_id, correlation_id, triggered_by,
                                              iniciado, agora())),
            "COMMIT;",
        ])
        rc, saida, erro = executar_sql(sql, prefixo)
        if rc != 0:
            raise RecusaDeDecisao(MOTIVO_PORTA_AUSENTE, erro.strip()[:400])
        contagens = (linhas_de_json(saida) or [{}])[0]
        if int(contagens.get("atualizados") or 0) != 1:
            raise RecusaDeDecisao(MOTIVO_CONFLITO_DE_ESTADO,
                                  "o pedido mudou de estado entre a leitura e a escrita: nada aplicado")
        resultado["contagens"] = {"atualizados": int(contagens.get("atualizados") or 0),
                                  "pendentes_do_contato": int(contagens.get("pendentes_do_contato") or 0)}
        return resultado
    except RecusaDeDecisao as recusa:
        resultado = {"veredito": RECUSADA, "pedido_id": pedido_id, "motivo": recusa.motivo,
                     "detalhe": recusa.detalhe, "verbo": verbo, "operador": operador}
        if confirmar:
            auditar(montar_auditoria(RECUSADA, recusa.motivo, recusa.detalhe, entrada,
                                     {"evento": "DECISAO", "resultado": resultado}, organization_id,
                                     correlation_id, triggered_by, iniciado, agora()), prefixo)
        return resultado


# ---------------------------------------------------------------------------------------
# Expiracao por TTL (transicao automatica, sem operador — declarada como tal)
# ---------------------------------------------------------------------------------------
def sql_da_expiracao(carregado: dict, auditoria: dict | None = None) -> str:
    pendente = status_do_papel(carregado, PAPEL_PENDENTE)
    expirado = status_do_papel(carregado, PAPEL_EXPIRADO)
    horas = float(carregado["politica"]["ttl"]["horas"])
    partes = [
        "BEGIN;",
        (f"UPDATE {TABELA_APROVACOES} SET status = {lit(expirado)}, decided_at = NOW(), "
         f"decision_notes = 'EXPIRADO_POR_TTL:' || {lit(f'{horas:g}h')} "
         f"WHERE status = {lit(pendente)} AND requested_at < NOW() - INTERVAL '{horas:g} hours';"),
        (f"SELECT json_build_object('expirados', (SELECT count(*) FROM {TABELA_APROVACOES} WHERE status = "
         f"{lit(expirado)} AND decision_notes = 'EXPIRADO_POR_TTL:' || {lit(f'{horas:g}h')}), "
         f"'pendentes', (SELECT count(*) FROM {TABELA_APROVACOES} WHERE status = {lit(pendente)}));"),
    ]
    if auditoria:
        partes.append(sql_da_auditoria(auditoria))
    partes.append("COMMIT;")
    return "\n".join(partes)


def expirar(carregado: dict, prefixo: str, ambiente: str, correlation_id: str,
            triggered_by: str) -> dict:
    iniciado = agora()
    pendente = status_do_papel(carregado, PAPEL_PENDENTE)
    expirado = status_do_papel(carregado, PAPEL_EXPIRADO)
    horas = float(carregado["politica"]["ttl"]["horas"])
    entrada = entrada_da_rodada(carregado, ambiente,
                                {"regra": f"{status_do_papel(carregado, PAPEL_PENDENTE)} mais velho "
                                          f"que {horas:g}h"})
    antes = [p for p in ler_fila(carregado, prefixo)
             if float(p.get("idade_horas") or 0) > horas]
    saida = {"evento": "EXPIRACAO", "ttl_horas": horas, "pedidos": [p["id"] for p in antes],
             "sem_operador": True,
             "nota": "expiracao e transicao automatica declarada: decided_by fica vazio"}
    auditoria = montar_auditoria(EXPIRADO if antes else JA_NOTIFICADO, "", "", entrada, saida,
                                 None, correlation_id, triggered_by, iniciado, agora())
    rc, saida_sql, erro = executar_sql(sql_da_expiracao(carregado, auditoria), prefixo)
    if rc != 0:
        raise RecusaDeDecisao(MOTIVO_PORTA_AUSENTE, erro.strip()[:400])
    contagens = (linhas_de_json(saida_sql) or [{}])[0]
    resultado = {"veredito": EXPIRADO if antes else JA_NOTIFICADO,
                 "expirados": int(contagens.get("expirados") or 0),
                 "pendentes_restantes": int(contagens.get("pendentes") or 0),
                 "status_pendente": pendente, "status_expirado": expirado,
                 "pedidos": saida["pedidos"], "auditoria_id": auditoria["id"]}
    return resultado


# ---------------------------------------------------------------------------------------
# Portao de consulta (W6-E04): o envio so manda o que este portao libera
# ---------------------------------------------------------------------------------------
def consultar(carregado: dict, prefixo: str, pedido_id: str) -> dict:
    pedido = ler_pedido(carregado, prefixo, pedido_id)
    pa = pedido.get("proposed_action") or {}
    decisao = pa.get("decisao") or {}
    status_aprovado = status_do_papel(carregado, PAPEL_APROVADO)
    texto = texto_do_pedido(pa)
    pode_enviar = pedido.get("status") == status_aprovado
    motivo = "" if pode_enviar else f"status {pedido.get('status')!r} nao libera envio"
    if pode_enviar and hash_do_texto(texto) != (decisao.get("texto_hash") or ""):
        pode_enviar, motivo = False, "o texto do pedido nao bate com o hash aprovado (nao envie)"
    bloqueios = contato_bloqueado(carregado, pedido.get("action_type") or "", pedido)
    if pode_enviar and bloqueios:
        pode_enviar, motivo = False, f"contato bloqueado desde a aprovacao: {bloqueios}"
    return {"pedido_id": pedido_id, "status": pedido.get("status"), "pode_enviar": pode_enviar,
            "motivo": motivo, "action_type": pedido.get("action_type"),
            "canal": pa.get("canal"), "destinatario": {"contato": pedido.get("contato"),
                                                       "email": pa.get("contato_email") or pedido.get("contato_email")},
            "texto": texto, "texto_hash": hash_do_texto(texto), "revisado": bool(decisao.get("revisado")),
            "decidido_por": pedido.get("decided_by"), "decidido_em": pedido.get("decidido_em"),
            "nota": pedido.get("decision_notes"), "recommendation_id": pa.get("recommendation_id"),
            "organization_id": pedido.get("organizacao_id"), "bloqueios_de_contato": bloqueios}


# ---------------------------------------------------------------------------------------
# Desfazer (rollback): reverte DECISOES de uma rodada; auditoria preservada
# ---------------------------------------------------------------------------------------
def sql_do_desfazer(carregado: dict, correlation_id: str, confirmar: bool, operador: str | None,
                    motivo: str | None) -> str:
    pendente = status_do_papel(carregado, PAPEL_PENDENTE)
    reversiveis = carregado["politica"]["transicoes"][ATO_REVERSAO]
    lista = ", ".join(lit(s) for s in reversiveis)
    alvo = (f"SELECT (output->'resultado'->>'pedido_id')::uuid FROM {TABELA_AGENT_RUNS} "
            f"WHERE correlation_id = {lit(correlation_id)}::uuid "
            f"AND output->>'evento' = {lit('DECISAO')} "
            f"AND output->'resultado'->>'pedido_id' IS NOT NULL")
    if not confirmar:
        return (f"SELECT json_build_object('seriam_revertidos', (SELECT count(*) FROM {TABELA_APROVACOES} "
                f"WHERE id IN ({alvo}) AND status IN ({lista})));")
    nota = f"REVERTIDO_POR:{operador}:{motivo}"
    return "\n".join([
        "BEGIN;",
        (f"UPDATE {TABELA_APROVACOES} SET status = {lit(pendente)}, decided_at = NULL, decided_by = NULL, "
         f"decision_notes = {lit(nota)}, "
         f"proposed_action = (proposed_action - 'decisao') "
         f"|| jsonb_build_object('assunto', COALESCE(proposed_action->'decisao'->'texto_original'->>'assunto', "
         f"proposed_action->>'assunto'), 'corpo', COALESCE(proposed_action->'decisao'->'texto_original'->>'corpo', "
         f"proposed_action->>'corpo'), 'cta', COALESCE(proposed_action->'decisao'->'texto_original'->>'cta', "
         f"proposed_action->>'cta')) "
         f"WHERE id IN ({alvo}) AND status IN ({lista});"),
        (f"SELECT json_build_object('revertidos', (SELECT count(*) FROM {TABELA_APROVACOES} WHERE "
         f"decision_notes = {lit(nota)}));"),
        "COMMIT;",
    ])


def desfazer(carregado: dict, prefixo: str, correlation_id: str, confirmar: bool,
             operador: str | None, motivo: str | None) -> dict:
    if confirmar:
        validar_operador(carregado, operador)
        if not (motivo or "").strip():
            raise RecusaDeDecisao(MOTIVO_MOTIVO_AUSENTE, "--desfazer --confirmo exige --motivo")
    try:
        sql = sql_do_desfazer(carregado, correlation_id, confirmar, operador, motivo)
    except RecusaDeDecisao:
        raise
    rc, saida, erro = executar_sql(sql, prefixo)
    if rc != 0:
        return {"veredito": ERRO, "detalhe": erro.strip()[:300]}
    linha = (linhas_de_json(saida) or [{}])[0]
    if not confirmar:
        return {"veredito": "DRY_RUN",
                "seriam_revertidos": int(linha.get("seriam_revertidos") or 0)}
    return {"veredito": "DESFEITO", "revertidos": int(linha.get("revertidos") or 0)}


# ---------------------------------------------------------------------------------------
# Lotes de decisao (--decisoes arquivo.jsonl): dry-run ate --confirmo
# ---------------------------------------------------------------------------------------
def ler_decisoes(caminho: Path) -> list[dict]:
    decisoes = []
    with caminho.open(encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            decisoes.append(json.loads(linha))
    return decisoes


def rodar_lote(carregado: dict, prefixo: str, ambiente: str, correlation_id: str, triggered_by: str,
               decisoes: list[dict], confirmar: bool) -> dict:
    aplicadas = []
    for item in decisoes:
        verbo = item.get("decisao") or ""
        operador = item.get("por")
        resultado = decidir(carregado, prefixo, ambiente, item.get("approval_id"), verbo, operador,
                            item.get("nota"), item.get("edicao"), correlation_id, triggered_by,
                            confirmar=confirmar)
        aplicadas.append(resultado)
    return {"veredito": "LOTE_APLICADO" if confirmar else "LOTE_PLANEJADO",
            "decisoes": aplicadas,
            "recusadas": sum(1 for d in aplicadas if d.get("veredito") == RECUSADA),
            "aplicadas": sum(1 for d in aplicadas if d.get("veredito") in (APROVADO, EDITADO, REJEITADO))}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def comando_planejar(carregado: dict, caminho_politica: Path) -> int:
    politica = carregado["politica"]
    relatorio = {
        "workflow": APROVACAO_VERSION, "card": politica["card"], "politica": f"{politica['nome']}@{politica['versao']}",
        "contrato": (carregado["contrato"].get("contract") or {}).get("version"),
        "status_do_contrato": sorted(carregado["status_do_contrato"]),
        "papeis_de_estado": politica["status"], "verbos": politica["verbos"],
        "transicoes": politica["transicoes"], "ttl_horas": politica["ttl"]["horas"],
        "operadores_autorizados": politica["operadores_autorizados"],
        "notificacao": {"canal": politica["notificacao"]["canal"],
                        "arquivo": politica["notificacao"]["arquivo"],
                        "entrega_externa": bool(politica["notificacao"].get("entrega_externa")),
                        "marcadores": politica["notificacao"]["marcadores"]},
        "escrita": {"tabelas": list(TABELAS_ESCRITA),
                    "guarda": "DDL, escrita fora das duas tabelas e DELETE sem --confirmo RECUSAM"},
        "leitura": list(TABELAS_LEITURA),
        "irmao": {"modulo": politica["gerador"]["modulo"], "politica": politica["gerador"]["politica"],
                  "uso": "guarda de contato e validacao deterministica da edicao (dono unico: W6-E02)"},
        "envio": {"executado": False, "quem_envia": "TRE-W6-E04-T01 — este componente nao envia nada"},
        "decisao": {"humana": True, "sem_operador_nomeado": "RECUSA (operador e obrigatorio)"},
        "politica_arquivo": str(caminho_politica),
    }
    print(json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def comando_regras(carregado: dict) -> int:
    politica = carregado["politica"]
    relatorio = {
        "politica": f"{politica['nome']}@{politica['versao']}",
        "verbos": {v: d for v, d in sorted(politica["verbos"].items())},
        "status_do_contrato": sorted(carregado["status_do_contrato"]),
        "papeis_de_estado_sem_verbo": sorted(
            set(politica["status"].values())
            - {d["status"] for d in politica["verbos"].values()}),
        "transicoes": politica["transicoes"],
        "operadores_autorizados": politica["operadores_autorizados"],
        "padroes_nao_humanos": politica["padroes_nao_humanos"],
        "ttl": politica["ttl"],
        "guarda_de_contato": carregado["politica_irma"]["guarda_de_contato"],
        "validacao_da_edicao": politica.get("validacao_da_edicao"),
        "marcadores_obrigatorios": politica["notificacao"]["marcadores"],
        "canal_da_notificacao": politica["notificacao"]["canal"],
    }
    print(json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workflow de aprovacao humana do outbound v1 (TRE-W6-E03-T01)")
    parser.add_argument("--ambiente", default=None, choices=["dev", "homolog", AMBIENTE_RECUSADO])
    parser.add_argument("--prefixo", default=None, help="porta de banco (ex.: docker exec -i pg-... psql ...)")
    parser.add_argument("--fila", action="store_true", help="le os pedidos aguardando decisao e monta a notificacao")
    parser.add_argument("--notificacoes", default=None, help="arquivo onde gravar as mensagens da fila")
    parser.add_argument("--decidir", default=None, help="id do pedido de aprovacao")
    parser.add_argument("--decisao", default=None, help="verbo declarado na politica (aprovar/rejeitar/editar)")
    parser.add_argument("--por", default=None, help="operador humano que decide (obrigatorio)")
    parser.add_argument("--nota", default=None)
    parser.add_argument("--edicao", default=None, help="JSON com assunto/corpo/cta revisados")
    parser.add_argument("--decisoes", default=None, help="arquivo JSONL com uma decisao por linha")
    parser.add_argument("--expirar", action="store_true")
    parser.add_argument("--consultar", default=None, help="portao de consulta para o envio (W6-E04)")
    parser.add_argument("--desfazer", default=None, help="correlation_id das decisoes a reverter")
    parser.add_argument("--motivo", default=None)
    parser.add_argument("--confirmo", action="store_true")
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
    parser = montar_parser()
    args = parser.parse_args(argv)
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
    if args.desfazer and not (args.ambiente and args.prefixo):
        print("uso: --desfazer exige --ambiente e --prefixo", file=sys.stderr)
        return EXIT_USO

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

    correlation_id = args.correlation_id or str(uuid.uuid4())
    relatorio: dict = {"workflow": APROVACAO_VERSION, "card": carregado["politica"]["card"],
                       "ambiente": args.ambiente, "correlation_id": correlation_id,
                       "iniciado_em": agora()}
    try:
        if args.desfazer:
            relatorio["desfazer"] = desfazer(carregado, args.prefixo, args.desfazer, args.confirmo,
                                             args.por, args.motivo)
            rc = EXIT_OK if relatorio["desfazer"].get("veredito") != ERRO else EXIT_FALHOU
        elif args.consultar:
            relatorio["consulta"] = consultar(carregado, args.prefixo, args.consultar)
            relatorio["veredito"] = "CONSULTADO"
            rc = EXIT_OK
        elif args.expirar:
            relatorio["expiracao"] = expirar(carregado, args.prefixo, args.ambiente, correlation_id,
                                            args.triggered_by)
            relatorio["veredito"] = relatorio["expiracao"]["veredito"]
            rc = EXIT_OK
        elif args.decisoes:
            caminho = achar_arquivo(raiz, args.decisoes)
            if not caminho.is_file():
                print(f"uso: arquivo de decisoes ausente: {caminho}", file=sys.stderr)
                return EXIT_USO
            relatorio["lote"] = rodar_lote(carregado, args.prefixo, args.ambiente, correlation_id,
                                           args.triggered_by, ler_decisoes(caminho), args.confirmo)
            relatorio["veredito"] = relatorio["lote"]["veredito"]
            rc = EXIT_OK if relatorio["lote"]["recusadas"] == 0 else EXIT_FALHOU
        elif args.decidir:
            if not args.decisao:
                print("uso: --decidir exige --decisao <verbo>", file=sys.stderr)
                return EXIT_USO
            operador = validar_operador(carregado, args.por)
            edicao = None
            if args.edicao:
                caminho_edicao = achar_arquivo(raiz, args.edicao)
                if not caminho_edicao.is_file():
                    print(f"uso: arquivo de edicao ausente: {caminho_edicao}", file=sys.stderr)
                    return EXIT_USO
                edicao = ler_json(caminho_edicao)
            relatorio["decisao"] = decidir(carregado, args.prefixo, args.ambiente, args.decidir,
                                           args.decisao, operador, args.nota, edicao, correlation_id,
                                           args.triggered_by)
            relatorio["veredito"] = relatorio["decisao"]["veredito"]
            rc = EXIT_OK if relatorio["decisao"]["veredito"] in (APROVADO, EDITADO, REJEITADO,
                                                                 JA_DECIDIDO) else EXIT_FALHOU
        else:
            relatorio["fila"] = rodar_fila(carregado, args.prefixo, args.ambiente, correlation_id,
                                           args.triggered_by, args.notificacoes)
            relatorio["veredito"] = relatorio["fila"]["veredito"]
            rc = EXIT_OK
    except RecusaDePolitica as recusa:
        print(f"RECUSADO {recusa}", file=sys.stderr)
        return EXIT_POLITICA_RECUSADA
    except RecusaDeDecisao as recusa:
        print(f"RECUSADO {recusa}", file=sys.stderr)
        return EXIT_FALHOU

    if args.relatorio:
        caminho = Path(args.relatorio)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True),
                           encoding="utf-8")
    print(json.dumps({k: v for k, v in relatorio.items() if k != "fila"}, ensure_ascii=False,
                     sort_keys=True, default=str))
    return rc


if __name__ == "__main__":
    sys.exit(main())
