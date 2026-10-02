#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gerador de abordagem outbound v1 (`gerador-abordagem-v1`) — card TRE-W6-E02-T01.

O que este componente FAZ (e so isto): le a EVIDENCIA que ja existe no PostgreSQL
(`sales_intelligence`) — a recomendacao OPEN do Next Best Action (card irmao TRE-W5-E07-T01),
a organizacao, o contato indicado, a pesquisa, as hipoteses de dor, os sinais, o PRIORITY e o
registro TIER — monta o PROMPT versionado (`prompt-abordagem-v1.md`) e pede a abordagem
(assunto/corpo/CTA) ao provedor declarado. A abordagem passa por VALIDACAO DETERMINISTICA
(fato sustentado pela evidencia, limites de tamanho, citacao, afirmacoes proibidas) e, passando,
vira PEDIDO DE APROVACAO HUMANA: `human_approvals` (status PENDING) + auditoria em `agent_runs`.

O que ele NAO faz, por desenho (declarado em `gerador-abordagem-v1.json` -> lacunas):
  - **nao envia nada**: nao fala SMTP/IMAP, nao cria atividade no Odoo, nao agenda. Grava o pedido
    PENDING e para — a decisao e do workflow humano (card seguinte TRE-W6-E03-T01), o envio e do
    W6-E04;
  - **nao decide o proximo passo**: a acao vem lida da recomendacao do NBA. Sem recomendacao OPEN com
    abordagem declarada, a rodada ABSTEM — ausencia de resposta e abstencao, nunca abordagem default;
  - **nao contata opt-out**: `do_not_contact`/`opt_out_*` sao BLOQUEIO (Data Contract §9); contato
    bloqueado no canal da acao RECUSA a rodada e nada e gravado;
  - **nao afirma o que a evidencia nao sustenta**: todo numero/URL/e-mail do texto tem de existir na
    evidencia (ou na assinatura declarada). Fato inventado pelo modelo RECUSA a abordagem;
  - **nao inventa assinatura**: a assinatura vem do bloco `remetente` da politica, acrescentada
    DEPOIS da validacao;
  - **nao escreve em producao** (ADR-005): `--ambiente dev|homolog`, `prod` e recusado com exit 4;
  - **nao guarda segredo**: a credencial do provedor vem de variavel de ambiente, nunca de argv,
    politica ou log.

Vocabulario: o codigo NAO carrega a lista de acoes do contrato em forma executavel — ela e LIDA de
`docs/data/data_contract_v1.json#vocabularies.next_best_action` a cada rodada, e a suite reprova se
uma acao do contrato aparecer escrita neste arquivo. O texto do prompt tambem nao mora aqui: mora no
arquivo versionado apontado pela politica.

Provedores (o adaptador tem UM contrato):
  - `offline` (padrao): renderizador DETERMINISTICO que so concatena trechos da evidencia. A
    auditoria marca provider 'offline' e nao conta token nem custo. Serve para medir o caminho de
    validacao/persistencia sem rede — nunca para fingir resposta de modelo;
  - `chat-completions`: HTTP POST `{base_url}/chat/completions` (formato compativel com OpenAI),
    modelo/base_url por `--modelo`/`--base-url` ou pelas variaveis de ambiente declarados na
    politica, credencial SO por `TRE_OUTREACH_API_KEY`. Sem credencial: RECUSA sem abrir conexao.

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"): o `id` do pedido e DETERMINISTICO —
`uuid5(NAMESPACE_URL, 'gerador-abordagem-v1:<organization_id>:<entrada_hash>')`. Mesma entrada =>
mesmo id => replay (JA_GERADA, nada duplicado). Evidencia/recomendacao/modelo/prompt novos => id
novo => pedido NOVO, com os pedidos PENDING anteriores da MESMA empresa/contato/acao em EXPIRED
(unico estado do vocabulario fechado que expressa pedido que perdeu validade sem decisao humana).

Uso (o banco vive na VPS do ambiente — ADR-0008):

  python3 hermes/agents/outreach/outreach_generator.py --planejar
  python3 hermes/agents/outreach/outreach_generator.py --regras
  python3 hermes/agents/outreach/outreach_generator.py --ambiente dev --organizacao <uuid> \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \\
      --relatorio /tmp/outreach-rodada.json
  python3 hermes/agents/outreach/outreach_generator.py --ambiente dev --organizacao <uuid> \\
      --prefixo "..." --provedor chat-completions --base-url https://api.exemplo/v1 --modelo <m>
  python3 hermes/agents/outreach/outreach_generator.py --desfazer <correlation_id> [--confirmo]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do componente (espelha `hermes/agents/outreach/gerador-abordagem-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "outreach"
PAPEL = "agente_outreach"
VERSAO = "1.0.0"
WORKFLOW = "outbound-abordagem"
WORKFLOW_VERSAO = "v1"

GERADOR_VERSION = "gerador-abordagem-v1"

POLITICA_PADRAO = "hermes/agents/outreach/politica-outreach-v1.json"
CONTRATO_COMPONENTE_PADRAO = "hermes/agents/outreach/gerador-abordagem-v1.json"
CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"

TABELA_APROVACOES = "sales_intelligence.human_approvals"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"

# O gerador ESCREVE apenas nestas duas: o pedido de aprovacao e a auditoria da rodada.
TABELAS_ESCRITA = (TABELA_APROVACOES, TABELA_AGENT_RUNS)
# Tudo o que a rodada LE (declarado; a leitura e o que sustenta a abordagem).
TABELAS_LEITURA = ("sales_intelligence.organizations", "sales_intelligence.contacts",
                   "sales_intelligence.recommendations", "sales_intelligence.research_runs",
                   "sales_intelligence.pain_hypotheses", "sales_intelligence.signals",
                   "sales_intelligence.scores", "sales_intelligence.sync_events",
                   "sales_intelligence.interactions")

STATUS_PENDENTE = "PENDING"
STATUS_EXPIRADO = "EXPIRED"

ENTIDADE_CONTATO = "CONTACT"

GERADA = "GERADA"
JA_GERADA = "JA_GERADA"
RECUSADA = "RECUSADA"
ABSTEVE = "ABSTEVE"
ERRO = "ERRO"
VEREDITOS = (GERADA, JA_GERADA, RECUSADA, ABSTEVE, ERRO)

STATUS_AGENT_RUNS = {
    GERADA: "COMPLETED",
    JA_GERADA: "COMPLETED",
    RECUSADA: "REJECTED",
    ABSTEVE: "REJECTED",
    ERRO: "FAILED",
}

MOTIVO_SEM_RECOMENDACAO = "SEM_ACAO_RECOMENDADA"
MOTIVO_ACAO_SEM_ABORDAGEM = "ACAO_SEM_ABORDAGEM_DECLARADA"
MOTIVO_NAO_ENCONTRADA = "ORGANIZACAO_NAO_ENCONTRADA"
MOTIVO_SEM_CONTATO = "SEM_CONTATO"
MOTIVO_CONTATO_BLOQUEADO = "CONTATO_BLOQUEADO"
MOTIVO_SEM_EVIDENCIA = "SEM_EVIDENCIA"
MOTIVO_SEM_EVIDENCIA_RENDERIZAVEL = "SEM_EVIDENCIA_RENDERIZAVEL"
MOTIVO_PROVEDOR_INCOMPLETO = "PROVEDOR_INCOMPLETO"
MOTIVO_PROVEDOR_RECUSOU = "PROVEDOR_RECUSOU"
MOTIVO_PROVEDOR_FALHOU = "PROVEDOR_FALHOU"
MOTIVO_RESPOSTA_ILEGIVEL = "RESPOSTA_DO_PROVEDOR_ILEGIVEL"
MOTIVO_ABORDAGEM_INVALIDA = "ABORDAGEM_INVALIDA"
MOTIVO_FATO_NAO_SUSTENTADO = "FATO_NAO_SUSTENTADO"
MOTIVO_POLITICA_INCOERENTE = "POLITICA_INCOERENTE"
MOTIVO_PORTA_AUSENTE = "PORTA_DE_BANCO_AUSENTE"

AMBIENTE_RECUSADO = "prod"
AMBIENTES_PERMITIDOS = ("dev", "homolog")

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_POLITICA_RECUSADA = 3
EXIT_RECUSOU_AMBIENTE = 4


class RecusaDeEscrita(Exception):
    """A guarda de escrita recusou o SQL (DDL, tabela fora das duas, DELETE sem --confirmo)."""


class RecusaDePolitica(Exception):
    """Politica/contrato incoerente: fail-closed, nada e lido nem gravado."""


class RecusaDeAbordagem(Exception):
    """A rodada nao produz abordagem (provedor ausente/ilegivel/invalida): fail-closed."""

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
# Politica, contrato e prompt (tudo declarado em arquivo; nada de regra no codigo)
# ---------------------------------------------------------------------------------------
def ler_json(caminho: Path) -> dict:
    with caminho.open(encoding="utf-8") as fh:
        return json.load(fh)


def achar_arquivo(raiz: Path, caminho: str) -> Path:
    p = Path(caminho)
    return p if p.is_absolute() else (raiz / caminho)


CAMPOS_DA_ACAO = ("acao", "canal", "tipo", "bloqueio_do_canal")
CAMPOS_DO_PROVEDOR = ("padrao", "permitidos", "contrato_do_provedor", "temperatura", "timeout_s",
                      "tentativas", "credencial_variavel", "modelo_variavel", "base_url_variavel")


def carregar_politica(raiz: Path, caminho_politica: Path, caminho_contrato: Path) -> tuple[dict, dict, list, list]:
    """Le e VALIDA a politica contra o Data Contract. Qualquer incoerencia RECUSA (fail-closed)."""
    if not caminho_politica.is_file():
        raise RecusaDePolitica(f"politica ausente: {caminho_politica}")
    if not caminho_contrato.is_file():
        raise RecusaDePolitica(f"contrato de dados ausente: {caminho_contrato}")
    politica = ler_json(caminho_politica)
    contrato = ler_json(caminho_contrato)
    vocab = contrato.get("vocabularies") or {}
    acoes_do_contrato = vocab.get("next_best_action")
    if not isinstance(acoes_do_contrato, list) or not acoes_do_contrato:
        raise RecusaDePolitica("contrato sem vocabularies.next_best_action")
    status_de_aprovacao = vocab.get("human_approvals.status")
    if not isinstance(status_de_aprovacao, list) or not status_de_aprovacao:
        raise RecusaDePolitica("contrato sem vocabularies['human_approvals.status']")

    for campo in ("nome", "versao", "acoes_de_abordagem", "nao_alcancadas", "limites",
                  "exigencia_de_citacao", "afirmacoes_proibidas", "remetente", "idioma",
                  "prompt", "provedor", "modelo_offline", "evidencia", "tipos_e_operacoes",
                  "guarda_de_contato"):
        if campo not in politica:
            raise RecusaDePolitica(f"politica sem campo obrigatorio: {campo}")

    for acao in politica["acoes_de_abordagem"]:
        for campo in CAMPOS_DA_ACAO:
            if not acao.get(campo):
                raise RecusaDePolitica(f"acao de abordagem sem campo {campo}: {acao!r}")
        if acao["acao"] not in acoes_do_contrato:
            raise RecusaDePolitica(f"acao da politica fora do vocabulario do contrato: {acao['acao']!r}")
        if acao["bloqueio_do_canal"] not in politica["guarda_de_contato"].get("bloqueios_por_canal", {}).values() \
                and acao["bloqueio_do_canal"] not in politica["guarda_de_contato"].get("bloqueios_absolutos", []):
            raise RecusaDePolitica(f"bloqueio de canal nao declarado na guarda: {acao['bloqueio_do_canal']!r}")

    codigos_com_abordagem = [a["acao"] for a in politica["acoes_de_abordagem"]]
    if len(codigos_com_abordagem) != len(set(codigos_com_abordagem)):
        raise RecusaDePolitica("acao repetida em acoes_de_abordagem")
    declaradas = set(codigos_com_abordagem) | set(politica["nao_alcancadas"])
    buracos = sorted(set(acoes_do_contrato) - declaradas)
    if buracos:
        raise RecusaDePolitica(f"acao do contrato sem declaracao na politica: {buracos}")
    estranhas = sorted(set(politica["nao_alcancadas"]) - set(acoes_do_contrato))
    if estranhas:
        raise RecusaDePolitica(f"acao em nao_alcancadas fora do contrato: {estranhas}")

    for status in (STATUS_PENDENTE, STATUS_EXPIRADO):
        if status not in status_de_aprovacao:
            raise RecusaDePolitica(f"status {status!r} fora do vocabulario do contrato")

    provedor = politica["provedor"]
    for campo in CAMPOS_DO_PROVEDOR:
        if campo not in provedor:
            raise RecusaDePolitica(f"politica.provedor sem campo {campo}")
    if provedor["padrao"] not in provedor["permitidos"]:
        raise RecusaDePolitica("provedor.padrao fora de provedor.permitidos")

    prompt = politica["prompt"]
    caminho_prompt = achar_arquivo(raiz, prompt["arquivo"])
    if not caminho_prompt.is_file():
        alt = RAIZ_PADRAO / prompt["arquivo"]
        if alt.is_file():
            caminho_prompt = alt
    if not caminho_prompt.is_file():
        raise RecusaDePolitica(f"prompt declarado nao existe: {prompt['arquivo']}")
    texto_prompt = caminho_prompt.read_text(encoding="utf-8")
    faltando = [m for m in prompt.get("marcadores", []) if m not in texto_prompt]
    if faltando:
        raise RecusaDePolitica(f"prompt sem marcador declarado: {faltando}")

    politica = dict(politica)
    politica["_prompt_texto"] = texto_prompt
    politica["_prompt_caminho"] = str(caminho_prompt)
    return politica, contrato, list(acoes_do_contrato), list(status_de_aprovacao)


def acao_de_abordagem(politica: dict, acao: str) -> dict | None:
    for item in politica["acoes_de_abordagem"]:
        if item["acao"] == acao:
            return item
    return None


# ---------------------------------------------------------------------------------------
# Leitura dos fatos (somente leitura; uma linha JSON por empresa)
# ---------------------------------------------------------------------------------------
def lit(texto) -> str:
    if texto is None:
        return "NULL"
    return "'" + str(texto).replace("'", "''") + "'"


def lit_json(objeto) -> str:
    return lit(json.dumps(objeto, ensure_ascii=False, sort_keys=True))


def sql_dos_fatos(organization_id: str, politica: dict) -> str:
    todo = politica["tipos_e_operacoes"]
    rec = todo["recomendacao"]
    ev = politica["evidencia"]
    status_dores = ", ".join(lit(s) for s in todo["dores_status"])
    sub_recomendacao = (f"SELECT r.contact_id FROM sales_intelligence.recommendations r "
                        f"WHERE r.organization_id = {lit(organization_id)} "
                        f"AND r.recommendation_type = {lit(rec['recommendation_type'])} "
                        f"AND r.status = {lit(rec['status'])} ORDER BY r.created_at DESC LIMIT 1")
    return f"""
SELECT json_build_object(
  'organizacao', (SELECT json_build_object(
        'id', o.id, 'nome', COALESCE(NULLIF(o.trade_name, ''), o.legal_name), 'razao_social', o.legal_name,
        'industria', o.industry_name, 'porte', o.employee_band, 'colaboradores', o.employee_count,
        'cidade', o.city, 'uf', o.state, 'status', o.status, 'dominio', o.domain)
     FROM sales_intelligence.organizations o WHERE o.id = {lit(organization_id)}),
  'recomendacao', (SELECT json_build_object(
        'id', r.id, 'acao', r.action, 'status', r.status, 'contato_id', r.contact_id,
        'rationale', r.rationale, 'criada_em', r.created_at)
     FROM sales_intelligence.recommendations r
     WHERE r.organization_id = {lit(organization_id)} AND r.recommendation_type = {lit(rec['recommendation_type'])}
       AND r.status = {lit(rec['status'])} ORDER BY r.created_at DESC LIMIT 1),
  'contato', (SELECT json_build_object(
        'id', c.id, 'nome', COALESCE(NULLIF(c.full_name, ''), c.first_name), 'primeiro_nome', c.first_name,
        'cargo', c.job_title, 'papel', c.decision_role, 'email', c.email, 'linkedin', c.linkedin_url,
        'canal_preferido', c.preferred_channel,
        'do_not_contact', COALESCE(c.do_not_contact, false),
        'opt_out_email', COALESCE(c.opt_out_email, false))
     FROM sales_intelligence.contacts c WHERE c.id = ({sub_recomendacao})),
  'pesquisa', (SELECT json_build_object('id', rr.id, 'resumo', rr.summary, 'concluida_em', rr.completed_at)
     FROM sales_intelligence.research_runs rr
     WHERE rr.organization_id = {lit(organization_id)} AND rr.status = {lit(todo['pesquisa_status'])}
     ORDER BY rr.completed_at DESC NULLS LAST LIMIT 1),
  'dores', (SELECT COALESCE(json_agg(json_build_object('id', p.id, 'declaracao', p.pain_statement,
        'categoria', p.pain_category, 'status', p.status, 'em', p.created_at)), '[]'::json)
     FROM (SELECT * FROM sales_intelligence.pain_hypotheses
           WHERE organization_id = {lit(organization_id)} AND status IN ({status_dores})
           ORDER BY created_at DESC LIMIT {int(ev['max_dores'])}) p),
  'sinais', (SELECT COALESCE(json_agg(json_build_object('id', s.id, 'tipo', s.signal_type,
        'titulo', s.title, 'descricao', s.description, 'em', s.event_date, 'relevancia', s.relevance_score)), '[]'::json)
     FROM (SELECT * FROM sales_intelligence.signals
           WHERE organization_id = {lit(organization_id)} AND (expires_at IS NULL OR expires_at > NOW())
           ORDER BY relevance_score DESC NULLS LAST, detected_at DESC LIMIT {int(ev['max_sinais'])}) s),
  'priority', (SELECT json_build_object('valor', sc.score_value, 'versao', sc.score_version, 'em', sc.calculated_at)
     FROM sales_intelligence.scores sc
     WHERE sc.organization_id = {lit(organization_id)} AND sc.score_type = {lit(todo['score_priority'])}
     ORDER BY sc.calculated_at DESC LIMIT 1),
  'tier', (SELECT json_build_object('tier', se.request_payload->>'tier', 'registro_id', se.id,
        'versao', se.source_version, 'em', se.created_at)
     FROM sales_intelligence.sync_events se
     WHERE se.entity_id = {lit(organization_id)} AND se.operation = {lit(todo['tier_operation'])}
       AND se.source_version = {lit(todo['tier_source_version'])}
     ORDER BY se.created_at DESC LIMIT 1),
  'interacoes_outbound', (SELECT COALESCE(json_agg(json_build_object('id', i.id, 'canal', i.channel,
        'tipo', i.interaction_type, 'assunto', i.subject, 'em', i.occurred_at)), '[]'::json)
     FROM (SELECT * FROM sales_intelligence.interactions
           WHERE organization_id = {lit(organization_id)} AND direction = {lit(todo['interacao_outbound_direction'])}
           ORDER BY occurred_at DESC LIMIT {int(ev['max_interacoes'])}) i)
)::text;
"""


# ---------------------------------------------------------------------------------------
# Evidencia -> ledger citavel (ids E1..En) ; e a idade do rascunho
# ---------------------------------------------------------------------------------------
def _texto(*partes) -> str:
    return " | ".join(str(p).strip() for p in partes if p not in (None, "", []))


def montar_evidencia(fatos: dict, politica: dict) -> list[dict]:
    """Ledger deterministico: cada item tem id (E1..), fonte, o texto CITAVEL e os campos usados."""
    itens: list[dict] = []
    org = fatos.get("organizacao") or {}
    contato = fatos.get("contato") or {}
    rec = fatos.get("recomendacao") or {}
    prompt = politica["prompt"]

    def add(fonte: str, texto: str, campos: dict | None = None) -> dict:
        item = {"id": f"E{len(itens) + 1}", "fonte": fonte, "texto": texto, "campos": campos or {}}
        itens.append(item)
        return item

    if org.get("nome"):
        setor = org.get("industria") or ""
        porte = org.get("porte") or ""
        local = ", ".join(x for x in ((org.get("cidade") or ""), (org.get("uf") or "")) if x)
        colaboradores = org.get("colaboradores")
        gatilho = _texto(f"atua em {setor}" if setor else None,
                         f"com {colaboradores} colaboradores" if colaboradores else None,
                         f"em {local}" if local else None)
        campos = {"empresa": org["nome"],
                  "tema": setor or (org.get("razao_social") or org["nome"]),
                  "gatilho": gatilho or "e uma empresa B2B brasileira",
                  "tier": (fatos.get("tier") or {}).get("tier")}
        add("organizations", _texto(org["nome"], org.get("razao_social"), setor, porte, local,
                                    f"{colaboradores} colaboradores" if colaboradores else None), campos)
    if contato.get("nome"):
        add("contacts", _texto(contato["nome"], contato.get("cargo"), contato.get("papel")),
            {"contato": contato["nome"], "cargo": contato.get("cargo"), "papel": contato.get("papel")})
    pesquisa = fatos.get("pesquisa") or {}
    if pesquisa.get("resumo"):
        resumo = str(pesquisa["resumo"]).strip()
        add("research_runs", resumo, {"resumo": resumo})
    for dor in (fatos.get("dores") or []):
        if dor.get("declaracao"):
            add("pain_hypotheses", str(dor["declaracao"]).strip(),
                {"hipotese": str(dor["declaracao"]).strip(), "categoria": dor.get("categoria"),
                 "status_da_dor": dor.get("status")})
    for sinal in (fatos.get("sinais") or []):
        add("signals", _texto(sinal.get("titulo"), sinal.get("descricao")),
            {"sinal": sinal.get("titulo"), "tipo": sinal.get("tipo")})
    priority = fatos.get("priority") or {}
    if priority.get("valor") is not None:
        add("scores", _texto(f"PRIORITY {priority['valor']}", f"versao {priority.get('versao')}"),
            {"priority": str(priority["valor"])})
    tier = fatos.get("tier") or {}
    if tier.get("tier"):
        add("sync_events", _texto(f"tier {tier['tier']}", f"registro {tier.get('registro_id')}",
                                  f"em {tier.get('em')}"), {"tier": tier["tier"]})
    for inter in (fatos.get("interacoes_outbound") or []):
        add("interactions", _texto(f"abordagem {inter.get('canal')}", inter.get("assunto"),
                                   f"em {inter.get('em')}"), {"ultima_abordagem_em": str(inter.get("em"))})
    if rec.get("rationale"):
        add("recommendations", str(rec["rationale"]).strip(), {"acao_recomendada": rec.get("acao")})
    for item in itens:
        item["campos"].setdefault("marcador", f"[{item['id']}]")
    politica.setdefault("_prompt_texto", "")
    return itens


def texto_citavel(politica: dict, evidencia: list[dict]) -> str:
    partes = [i["texto"] for i in evidencia]
    rem = politica["remetente"]
    partes += [str(rem.get(k) or "") for k in ("nome", "cargo", "empresa", "email")]
    return "\n".join(partes)


def sem_evidencia(evidencia: list[dict], politica: dict) -> bool:
    fontes_de_fato = set(politica["evidencia"]["fontes_de_fato"])
    fontes_com_fato = {i["fonte"] for i in evidencia if i["fonte"] in fontes_de_fato}
    return len(fontes_com_fato) < int(politica["evidencia"]["minimo_itens"])


# ---------------------------------------------------------------------------------------
# Prompt e provedores
# ---------------------------------------------------------------------------------------
def renderizar_prompt(politica: dict, fatos: dict, evidencia: list[dict], acao: dict) -> tuple[str, str]:
    org = fatos.get("organizacao") or {}
    contato = fatos.get("contato") or {}
    rem = politica["remetente"]
    blocos = [f"{i['id']} [{i['fonte']}] {i['texto']}" for i in evidencia]
    valores = {
        "{{IDIOMA}}": politica["idioma"],
        "{{MARCADOR}}": "[E1]",
        "{{MINIMO_CITACOES}}": str(politica["exigencia_de_citacao"]["minimo"]),
        "{{AFIRMACOES_PROIBIDAS}}": ", ".join(politica["afirmacoes_proibidas"]),
        "{{LIMITE_ASSUNTO}}": str(politica["limites"]["assunto_chars"]),
        "{{LIMITE_CORPO}}": str(politica["limites"]["corpo_chars"]),
        "{{LIMITE_CTA}}": str(politica["limites"]["cta_chars"]),
        "{{ACAO}}": acao["acao"],
        "{{CANAL}}": acao["canal"],
        "{{EMPRESA}}": org.get("nome") or "",
        "{{CONTATO}}": f"{contato.get('nome') or ''} ({contato.get('cargo') or ''})".strip(),
        "{{REMETENTE}}": f"{rem['nome']}, {rem['cargo']} — {rem['empresa']} <{rem['email']}>",
        "{{EVIDENCIA}}": "\n".join(f"- {b}" for b in blocos),
    }
    texto = politica["_prompt_texto"]
    for chave, valor in valores.items():
        texto = texto.replace(chave, valor)
    sistema = texto.split("## Contexto")[0].strip()
    usuario = "## Contexto" + texto.split("## Contexto", 1)[1] if "## Contexto" in texto else texto
    return sistema, usuario


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def _digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def renderizar_offline(politica: dict, fatos: dict, evidencia: list[dict], acao: dict) -> dict:
    """Renderizador DETERMINISTICO: so concatena trechos da evidencia. Nada de fato novo."""
    modelo = politica["modelo_offline"]
    org = fatos.get("organizacao") or {}
    por_marcador = {i["id"]: i for i in evidencia}
    base = next((i for i in evidencia if i["fonte"] == "organizations"), None)
    if base is None:
        raise RecusaDeAbordagem(MOTIVO_SEM_EVIDENCIA_RENDERIZAVEL, "sem item de organizacao na evidencia")
    dor = next((i for i in evidencia if i["fonte"] == "pain_hypotheses"), None)

    def marcar(item: dict | None) -> str:
        return item["campos"]["marcador"] if item else ""

    tema = base["campos"].get("tema") or ""
    assunto = (modelo["assunto"].replace("{{EMPRESA}}", org.get("nome") or "")
               .replace("{{TIPO}}", acao["tipo"]).replace("{{TEMA}}", tema))
    linhas = []
    for linha in modelo["corpo"]:
        if (dor is None) and ("{{HIPOTESE}}" in linha or "{{CITACAO_HIPOTESE}}" in linha):
            continue
        texto = (linha.replace("{{PRIMEIRO_NOME}}", (fatos.get("contato") or {}).get("primeiro_nome") or "")
                 .replace("{{EMPRESA}}", org.get("nome") or "")
                 .replace("{{GATILHO}}", base["campos"].get("gatilho") or "")
                 .replace("{{CITACAO}}", marcar(base))
                 .replace("{{HIPOTESE}}", (dor["campos"].get("hipotese") if dor else "") or "")
                 .replace("{{CITACAO_HIPOTESE}}", marcar(dor)))
        linhas.append(re.sub(r"\s+", " ", texto).strip())
    cta = modelo["cta"]
    return {"assunto": _normalizar(assunto), "corpo": "\n\n".join(l for l in linhas if l),
            "cta": _normalizar(cta)}


def _http_post_json(url: str, corpo: dict, cabecalhos: dict, timeout: int) -> dict:
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers=cabecalhos, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def chamar_chat_completions(politica: dict, sistema: str, usuario: str, base_url: str, modelo: str,
                            chave: str) -> tuple[dict, dict]:
    cfg = politica["provedor"]
    if not base_url or not modelo or not chave:
        raise RecusaDeAbordagem(MOTIVO_PROVEDOR_INCOMPLETO,
                                f"base_url={'ok' if base_url else 'ausente'} modelo={'ok' if modelo else 'ausente'} "
                                f"credencial={'ok' if chave else 'ausente'} (fonte: {cfg['credencial_variavel']})")
    local = re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", base_url)
    if not base_url.startswith("https://") and not local:
        raise RecusaDeAbordagem(MOTIVO_PROVEDOR_INCOMPLETO,
                                "base_url tem de ser https (http so em 127.0.0.1/localhost, stub de teste)")
    corpo = {
        "model": modelo,
        "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
        "temperature": cfg["temperatura"],
        "response_format": {"type": "json_object"},
    }
    cabecalhos = {"Content-Type": "application/json", "Authorization": f"Bearer {chave}"}
    url = base_url.rstrip("/") + "/chat/completions"
    ultimo = ""
    for tentativa in range(1, int(cfg["tentativas"]) + 1):
        try:
            resposta = _http_post_json(url, corpo, cabecalhos, int(cfg["timeout_s"]))
            break
        except urllib.error.HTTPError as exc:
            ultimo = f"HTTP {exc.code}"
            if exc.code < 500:
                raise RecusaDeAbordagem(MOTIVO_PROVEDOR_RECUSOU, f"{ultimo} na tentativa {tentativa}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ultimo = f"falha de rede: {exc}"
        except json.JSONDecodeError as exc:
            raise RecusaDeAbordagem(MOTIVO_RESPOSTA_ILEGIVEL, f"resposta nao e JSON: {exc}")
        if tentativa < int(cfg["tentativas"]):
            time.sleep(1)
    else:
        raise RecusaDeAbordagem(MOTIVO_PROVEDOR_FALHOU, f"{ultimo} apos {cfg['tentativas']} tentativa(s)")

    try:
        conteudo = resposta["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RecusaDeAbordagem(MOTIVO_RESPOSTA_ILEGIVEL, f"resposta sem choices[0].message.content: {exc}")
    uso = resposta.get("usage") or {}
    meta = {
        "provider": "chat-completions",
        "model": resposta.get("model") or modelo,
        "tokens_input": uso.get("prompt_tokens"),
        "tokens_output": uso.get("completion_tokens"),
        "custo": None,
    }
    return extrair_abordagem(conteudo), meta


def extrair_abordagem(conteudo: str) -> dict:
    """A resposta do provedor tem de ser SOMENTE o JSON da abordagem (o prompt exige)."""
    texto = (conteudo or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()
    try:
        dado = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise RecusaDeAbordagem(MOTIVO_RESPOSTA_ILEGIVEL, f"conteudo nao e JSON: {exc}")
    if not isinstance(dado, dict):
        raise RecusaDeAbordagem(MOTIVO_RESPOSTA_ILEGIVEL, "conteudo nao e um objeto JSON")
    return dado


# ---------------------------------------------------------------------------------------
# Validacao deterministica da abordagem
# ---------------------------------------------------------------------------------------
def validar_abordagem(abordagem: dict, politica: dict, evidencia: list[dict]) -> list[str]:
    problemas: list[str] = []
    limites = politica["limites"]
    for campo in ("assunto", "corpo", "cta"):
        valor = abordagem.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            problemas.append(f"campo {campo} ausente ou vazio")
    if problemas:
        return problemas
    assunto = _normalizar(abordagem["assunto"])
    corpo = abordagem["corpo"].strip()
    cta = _normalizar(abordagem["cta"])
    if len(assunto) > int(limites["assunto_chars"]):
        problemas.append(f"assunto com {len(assunto)} caracteres (limite {limites['assunto_chars']})")
    if len(corpo) > int(limites["corpo_chars"]):
        problemas.append(f"corpo com {len(corpo)} caracteres (limite {limites['corpo_chars']})")
    if len(cta) > int(limites["cta_chars"]):
        problemas.append(f"cta com {len(cta)} caracteres (limite {limites['cta_chars']})")

    juntos = f"{assunto}\n{corpo}\n{cta}"
    marcadores = set(re.findall(r"\[E(\d+)\]", juntos))
    existentes = {i["id"][1:] for i in evidencia}
    inventados = sorted(marcadores - existentes, key=int)
    if inventados:
        problemas.append(f"marcador de evidencia inexistente: {['[E' + i + ']' for i in inventados]}")
    if len(marcadores) < int(politica["exigencia_de_citacao"]["minimo"]):
        problemas.append("abordagem sem o minimo de citacao da politica")

    sustentavel = _normalizar(texto_citavel(politica, evidencia))
    digitos = _digitos(sustentavel)
    sem_marcadores = re.sub(r"\[E\d+\]", " ", juntos)
    for numero in re.findall(r"\d[\d.,]*", sem_marcadores):
        if _digitos(numero) and _digitos(numero) not in digitos:
            problemas.append(f"numero sem sustentacao na evidencia: {numero}")
    for url in re.findall(r"https?://[^\s)\]]+", sem_marcadores):
        if _normalizar(url) not in sustentavel:
            problemas.append(f"URL sem sustentacao na evidencia: {url}")
    for email in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", sem_marcadores):
        if _normalizar(email).lower() not in sustentavel.lower():
            problemas.append(f"e-mail sem sustentacao na evidencia: {email}")

    minusculo = juntos.lower()
    for proibida in politica["afirmacoes_proibidas"]:
        if str(proibida).lower() in minusculo:
            problemas.append(f"afirmacao proibida pela politica: {proibida!r}")
    return problemas


def assinatura(politica: dict) -> str:
    rem = politica["remetente"]
    return f"{rem['nome']}\n{rem['cargo']} — {rem['empresa']}\n{rem['email']}"


# ---------------------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------------------
def identidade_da_entrada(politica: dict, fatos: dict, evidencia: list[dict], acao: dict,
                          sistema: str, usuario: str, provedor_meta: dict) -> str:
    org = fatos.get("organizacao") or {}
    rec = fatos.get("recomendacao") or {}
    contato = fatos.get("contato") or {}
    canonico = {
        "gerador": GERADOR_VERSION,
        "prompt_version": politica["prompt"]["versao"],
        "politica": f"{politica['nome']}@{politica['versao']}",
        "organization_id": org.get("id"),
        "recomendacao": {"id": rec.get("id"), "acao": rec.get("acao"), "status": rec.get("status")},
        "contato": {"id": contato.get("id"), "email": contato.get("email")},
        "canal": acao["canal"],
        "provedor": {"provider": provedor_meta.get("provider"), "model": provedor_meta.get("model")},
        "prompt": {"sistema": sistema, "usuario": usuario},
        "evidencia": [{"id": i["id"], "fonte": i["fonte"], "texto": i["texto"]} for i in evidencia],
    }
    bruto = json.dumps(canonico, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def id_da_aprovacao(organization_id: str, entrada_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{GERADOR_VERSION}:{organization_id}:{entrada_hash}"))


# ---------------------------------------------------------------------------------------
# Escrita: pedido de aprovacao + auditoria (nada mais)
# ---------------------------------------------------------------------------------------
def montar_pedido(politica: dict, fatos: dict, acao: dict, abordagem: dict, evidencia: list[dict],
                  sistema: str, usuario: str, entrada_hash: str, provedor_meta: dict,
                  pedido_id: str, requested_by: str) -> dict:
    org = fatos.get("organizacao") or {}
    contato = fatos.get("contato") or {}
    rec = fatos.get("recomendacao") or {}
    citados = sorted({f"[E{m}]" for m in re.findall(r"\[E(\d+)\]",
                     f"{abordagem['assunto']}\n{abordagem['corpo']}\n{abordagem['cta']}")}, key=lambda s: int(s[2:-1]))
    return {
        "id": pedido_id,
        "action_type": rec.get("acao"),
        "entity_type": ENTIDADE_CONTATO,
        "entity_id": contato.get("id"),
        "requested_by": requested_by,
        "proposed_action": {
            "canal": acao["canal"],
            "tipo": acao["tipo"],
            "assunto": abordagem["assunto"],
            "corpo": abordagem["corpo"],
            "cta": abordagem["cta"],
            "fatos_citados": citados,
            "evidencia_ids": [i["id"] for i in evidencia],
            "recommendation_id": rec.get("id"),
            "organization_id": org.get("id"),
            "contact_id": contato.get("id"),
            "gerador_version": GERADOR_VERSION,
            "prompt_version": politica["prompt"]["versao"],
            "politica": f"{politica['nome']}@{politica['versao']}",
            "provedor": {"provider": provedor_meta.get("provider"), "model": provedor_meta.get("model")},
            "entrada_hash": entrada_hash,
            "remetente": politica["remetente"]["email"],
        },
        "status": STATUS_PENDENTE,
    }


def montar_auditoria(politica: dict, fatos: dict, acao: dict | None, veredito: str, motivo: str,
                     detalhe: str, organization_id: str, entrada_hash: str, provedor_meta: dict,
                     saida: dict | None, correlation_id: str, triggered_by: str, started_at: str,
                     finished_at: str) -> dict:
    org = fatos.get("organizacao") or {}
    contato = fatos.get("contato") or {}
    rec = fatos.get("recomendacao") or {}
    entrada = {
        "organization_id": organization_id,
        "organization_name": org.get("nome"),
        "recommendation_id": rec.get("id"),
        "action": rec.get("acao"),
        "contact_id": contato.get("id"),
        "canal": (acao or {}).get("canal"),
        "entrada_hash": entrada_hash,
        "prompt_version": politica["prompt"]["versao"],
        "model_provider": provedor_meta.get("provider"),
        "model_name": provedor_meta.get("model"),
        "politica": f"{politica['nome']}@{politica['versao']}",
        "gerador_version": GERADOR_VERSION,
        "tabelas_lidas": list(TABELAS_LEITURA),
    }
    saida_final = dict(saida or {})
    saida_final.update({"veredito": veredito, "motivo": motivo, "detalhe": detalhe})
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
        "output": saida_final,
        "model": provedor_meta.get("model"),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": STATUS_AGENT_RUNS[veredito],
        "tokens_input": provedor_meta.get("tokens_input"),
        "tokens_output": provedor_meta.get("tokens_output"),
        "estimated_cost": provedor_meta.get("custo"),
        "error": None if veredito in (GERADA, JA_GERADA) else {"motivo": motivo, "detalhe": detalhe},
    }


def sql_do_ja_existia(pedido_id: str) -> str:
    return (f"SELECT json_build_object('ja_existia', "
            f"(SELECT count(*) FROM {TABELA_APROVACOES} WHERE id = {lit(pedido_id)}));")


def sql_da_supersessao(pedido_id: str, acao: str, contato_id: str) -> str:
    return (f"UPDATE {TABELA_APROVACOES} SET status = {lit(STATUS_EXPIRADO)} "
            f"WHERE status = {lit(STATUS_PENDENTE)} AND action_type = {lit(acao)} "
            f"AND entity_type = {lit(ENTIDADE_CONTATO)} AND entity_id = {lit(contato_id)} "
            f"AND id <> {lit(pedido_id)};")


def sql_da_escrita(pedido: dict, auditoria: dict, supersede: bool) -> str:
    pa = pedido["proposed_action"]
    partes = ["BEGIN;"]
    if supersede:
        partes.append(sql_da_supersessao(pedido["id"], pedido["action_type"], pedido["entity_id"]))
    partes.append(
        f"INSERT INTO {TABELA_APROVACOES} (id, action_type, entity_type, entity_id, requested_by, "
        f"proposed_action, status, requested_at) VALUES ("
        f"{lit(pedido['id'])}, {lit(pedido['action_type'])}, {lit(pedido['entity_type'])}, "
        f"{lit(pedido['entity_id'])}, {lit(pedido['requested_by'])}, "
        f"{lit_json(pa)}::jsonb, {lit(pedido['status'])}, NOW()) "
        f"ON CONFLICT (id) DO NOTHING;")
    a = auditoria
    partes.append(
        f"INSERT INTO {TABELA_AGENT_RUNS} (id, agent_name, agent_role, agent_version, workflow, "
        f"workflow_version, organization_id, triggered_by, correlation_id, input, output, model, "
        f"started_at, finished_at, status, tokens_input, tokens_output, estimated_cost, error) VALUES ("
        f"{lit(a['id'])}, {lit(a['agent_name'])}, {lit(a['agent_role'])}, {lit(a['agent_version'])}, "
        f"{lit(a['workflow'])}, {lit(a['workflow_version'])}, {lit(a['organization_id'])}, "
        f"{lit(a['triggered_by'])}, {lit(a['correlation_id'])}, {lit_json(a['input'])}::jsonb, "
        f"{lit_json(a['output'])}::jsonb, {lit(a['model'])}, {lit(a['started_at'])}::timestamptz, "
        f"{lit(a['finished_at'])}::timestamptz, {lit(a['status'])}, {a['tokens_input'] if a['tokens_input'] is not None else 'NULL'}, "
        f"{a['tokens_output'] if a['tokens_output'] is not None else 'NULL'}, "
        f"{a['estimated_cost'] if a['estimated_cost'] is not None else 'NULL'}, "
        f"{lit_json(a['error']) if a['error'] else 'NULL'}::jsonb);")
    partes.append(
        "SELECT json_build_object("
        f"'aprovacoes', (SELECT count(*) FROM {TABELA_APROVACOES} WHERE id = {lit(pedido['id'])}), "
        f"'pendentes_do_contato', (SELECT count(*) FROM {TABELA_APROVACOES} WHERE status = {lit(STATUS_PENDENTE)} "
        f"AND action_type = {lit(pedido['action_type'])} AND entity_id = {lit(pedido['entity_id'])}), "
        f"'auditorias', (SELECT count(*) FROM {TABELA_AGENT_RUNS} WHERE correlation_id = {lit(auditoria['correlation_id'])}));")
    partes.append("COMMIT;")
    return "\n".join(partes)


def sql_da_auditoria_simples(auditoria: dict) -> str:
    a = auditoria
    return (
        f"INSERT INTO {TABELA_AGENT_RUNS} (id, agent_name, agent_role, agent_version, workflow, "
        f"workflow_version, organization_id, triggered_by, correlation_id, input, output, model, "
        f"started_at, finished_at, status, error) VALUES ("
        f"{lit(a['id'])}, {lit(a['agent_name'])}, {lit(a['agent_role'])}, {lit(a['agent_version'])}, "
        f"{lit(a['workflow'])}, {lit(a['workflow_version'])}, {lit(a['organization_id'])}, "
        f"{lit(a['triggered_by'])}, {lit(a['correlation_id'])}, {lit_json(a['input'])}::jsonb, "
        f"{lit_json(a['output'])}::jsonb, {lit(a['model'])}, {lit(a['started_at'])}::timestamptz, "
        f"{lit(a['finished_at'])}::timestamptz, {lit(a['status'])}, "
        f"{lit_json(a['error']) if a['error'] else 'NULL'}::jsonb);")


def sql_do_desfazer(correlation_id: str, confirmar: bool) -> str:
    alvo = (f"SELECT (output->>'human_approval_id')::uuid FROM {TABELA_AGENT_RUNS} "
            f"WHERE correlation_id = {lit(correlation_id)} AND output->>'human_approval_id' IS NOT NULL")
    if not confirmar:
        return f"SELECT json_build_object('seriam_apagados', (SELECT count(*) FROM {TABELA_APROVACOES} WHERE id IN ({alvo})));"
    return (f"WITH alvo AS ({alvo}) DELETE FROM {TABELA_APROVACOES} WHERE id IN (SELECT * FROM alvo) "
            f"RETURNING id;")


def validar_sql(sql: str, permitir_delete: bool = False) -> None:
    """Guarda de escrita: so as duas tabelas, so INSERT/UPDATE de status, DELETE so com --confirmo."""
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


# ---------------------------------------------------------------------------------------
# Rodada por empresa
# ---------------------------------------------------------------------------------------
def ler_fatos(organization_id: str, politica: dict, prefixo: str) -> dict:
    sql = sql_dos_fatos(organization_id, politica)
    rc, saida, erro = executar_sql(sql, prefixo)
    if rc != 0:
        raise RecusaDeAbordagem(MOTIVO_PORTA_AUSENTE, f"leitura falhou: {erro.strip()[:400]}")
    linhas = linhas_de_json(saida)
    if not linhas:
        raise RecusaDeAbordagem(MOTIVO_PORTA_AUSENTE,
                                "a porta de banco nao devolveu JSON (prefixo aponta para psql?)")
    return linhas[0]


def contato_bloqueado(politica: dict, acao: dict, contato: dict) -> list[str]:
    """Compliance: devolve os bloqueios LIGADOS do contato no canal da acao (vazio = pode abordar)."""
    bloqueios = list(politica["guarda_de_contato"]["bloqueios_absolutos"])
    if acao["bloqueio_do_canal"] not in bloqueios:
        bloqueios.append(acao["bloqueio_do_canal"])
    return [b for b in bloqueios if contato.get(b)]


def rodar_organizacao(organization_id: str, politica: dict, prefixo: str, correlation_id: str,
                      triggered_by: str, started_at: str, provedor: str, base_url: str | None,
                      modelo: str | None, chave: str | None, injetar_abordagem: str | None = None) -> dict:
    finished_at = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731
    base = {"organization_id": organization_id, "veredito": ERRO, "motivo": "", "detalhe": "",
            "fatos": {}, "acao": None, "canal": None, "gravados": 0, "ja_existia": 0}

    def auditar(veredito: str, motivo: str, detalhe: str, fatos: dict, acao: dict | None,
                entrada_hash: str, provedor_meta: dict, saida: dict | None) -> int:
        auditoria = montar_auditoria(politica, fatos, acao, veredito, motivo, detalhe, organization_id,
                                     entrada_hash, provedor_meta, saida, correlation_id, triggered_by,
                                     started_at, finished_at())
        rc, _, erro = executar_sql(sql_da_auditoria_simples(auditoria), prefixo)
        if rc != 0:
            print(f"ERRO auditoria nao gravada: {erro.strip()[:300]}", file=sys.stderr)
        return rc

    try:
        fatos = ler_fatos(organization_id, politica, prefixo)
    except RecusaDeAbordagem as recusa:
        return {**base, "veredito": ERRO, "motivo": recusa.motivo, "detalhe": recusa.detalhe}
    base["fatos"] = {"organizacao": (fatos.get("organizacao") or {}).get("nome"),
                     "tier": (fatos.get("tier") or {}).get("tier"),
                     "acao_recomendada": (fatos.get("recomendacao") or {}).get("acao")}

    if not fatos.get("organizacao"):
        auditar(RECUSADA, MOTIVO_NAO_ENCONTRADA, "organizacao inexistente no schema sales_intelligence",
                fatos, None, "", {}, None)
        return {**base, "veredito": RECUSADA, "motivo": MOTIVO_NAO_ENCONTRADA,
                "detalhe": "organizacao inexistente"}

    rec = fatos.get("recomendacao") or {}
    if not rec.get("id"):
        motivo = MOTIVO_SEM_RECOMENDACAO
        auditar(ABSTEVE, motivo, "nenhuma recomendacao NEXT_BEST_ACTION com status OPEN para a empresa",
                fatos, None, "", {}, None)
        return {**base, "veredito": ABSTEVE, "motivo": motivo,
                "detalhe": "sem recomendacao OPEN (o gerador nao decide o proximo passo)"}

    acao = acao_de_abordagem(politica, rec.get("acao"))
    if acao is None:
        motivo = MOTIVO_ACAO_SEM_ABORDAGEM
        auditar(ABSTEVE, motivo, f"acao {rec.get('acao')!r} declarada em nao_alcancadas: nao gera abordagem",
                fatos, None, "", {}, None)
        return {**base, "veredito": ABSTEVE, "motivo": motivo,
                "detalhe": f"acao {rec.get('acao')!r} nao gera abordagem nesta politica"}

    contato = fatos.get("contato") or {}
    base["acao"], base["canal"] = rec.get("acao"), acao["canal"]
    if not contato.get("id"):
        auditar(RECUSADA, MOTIVO_SEM_CONTATO, "a recomendacao nao indica contato existente", fatos, acao, "", {}, None)
        return {**base, "veredito": RECUSADA, "motivo": MOTIVO_SEM_CONTATO,
                "detalhe": "contato ausente ou inexistente"}

    ligados = contato_bloqueado(politica, acao, contato)
    if ligados:
        detalhe = f"contato bloqueado por {ligados} (canal {acao['canal']})"
        auditar(RECUSADA, MOTIVO_CONTATO_BLOQUEADO, detalhe, fatos, acao, "", {}, None)
        return {**base, "veredito": RECUSADA, "motivo": MOTIVO_CONTATO_BLOQUEADO, "detalhe": detalhe}

    evidencia = montar_evidencia(fatos, politica)
    if sem_evidencia(evidencia, politica):
        auditar(RECUSADA, MOTIVO_SEM_EVIDENCIA, "nenhuma pesquisa, sinal, dor, score ou tier lido",
                fatos, acao, "", {}, None)
        return {**base, "veredito": RECUSADA, "motivo": MOTIVO_SEM_EVIDENCIA,
                "detalhe": "abordagem sem fato e abordagem generica: fail-closed"}

    sistema, usuario = renderizar_prompt(politica, fatos, evidencia, acao)
    if injetar_abordagem:
        abordagem = extrair_abordagem(Path(injetar_abordagem).read_text(encoding="utf-8"))
        provedor_meta = {"provider": "injecao-de-teste", "model": None, "tokens_input": None,
                         "tokens_output": None, "custo": None}
    elif provedor == "offline":
        try:
            abordagem = renderizar_offline(politica, fatos, evidencia, acao)
        except RecusaDeAbordagem as recusa:
            auditar(RECUSADA, recusa.motivo, recusa.detalhe, fatos, acao, "", {}, None)
            return {**base, "veredito": RECUSADA, "motivo": recusa.motivo, "detalhe": recusa.detalhe}
        provedor_meta = {"provider": "offline", "model": politica["modelo_offline"]["rotulo"],
                         "tokens_input": None, "tokens_output": None, "custo": None}
    else:
        try:
            abordagem, provedor_meta = chamar_chat_completions(politica, sistema, usuario, base_url or "",
                                                               modelo or "", chave or "")
        except RecusaDeAbordagem as recusa:
            veredito = RECUSADA if recusa.motivo in (MOTIVO_PROVEDOR_INCOMPLETO, MOTIVO_PROVEDOR_RECUSOU,
                                                     MOTIVO_RESPOSTA_ILEGIVEL) else ERRO
            auditar(veredito, recusa.motivo, recusa.detalhe, fatos, acao, "", {}, None)
            return {**base, "veredito": veredito, "motivo": recusa.motivo, "detalhe": recusa.detalhe}

    entrada_hash = identidade_da_entrada(politica, fatos, evidencia, acao, sistema, usuario, provedor_meta)
    problemas = validar_abordagem(abordagem, politica, evidencia)
    if problemas:
        auditar(RECUSADA, MOTIVO_ABORDAGEM_INVALIDA if any("sem sustentacao" not in p for p in problemas)
                else MOTIVO_FATO_NAO_SUSTENTADO, "; ".join(problemas), fatos, acao, entrada_hash,
                provedor_meta, {"problemas": problemas})
        return {**base, "veredito": RECUSADA, "motivo": MOTIVO_ABORDAGEM_INVALIDA,
                "detalhe": "; ".join(problemas), "entrada_hash": entrada_hash}

    corpo_final = abordagem["corpo"].rstrip() + "\n\n" + assinatura(politica)
    abordagem = {"assunto": abordagem["assunto"], "corpo": corpo_final, "cta": abordagem["cta"]}

    pedido_id = id_da_aprovacao(organization_id, entrada_hash)
    pedido = montar_pedido(politica, fatos, acao, abordagem, evidencia, sistema, usuario, entrada_hash,
                           provedor_meta, pedido_id, triggered_by)
    rc, saida, erro = executar_sql(sql_do_ja_existia(pedido_id), prefixo)
    if rc != 0:
        return {**base, "veredito": ERRO, "motivo": MOTIVO_PORTA_AUSENTE, "detalhe": erro.strip()[:300]}
    ja_existia = int((linhas_de_json(saida) or [{"ja_existia": 0}])[0].get("ja_existia") or 0)

    auditoria = montar_auditoria(
        politica, fatos, acao, JA_GERADA if ja_existia else GERADA, "", "", organization_id, entrada_hash,
        provedor_meta,
        {"assunto": abordagem["assunto"], "corpo": abordagem["corpo"], "cta": abordagem["cta"],
         "human_approval_id": pedido_id, "action_type": pedido["action_type"],
         "fatos_citados": pedido["proposed_action"]["fatos_citados"],
         "evidencia_ids": pedido["proposed_action"]["evidencia_ids"],
         "status_do_pedido": STATUS_PENDENTE},
        correlation_id, triggered_by, started_at, finished_at())
    rc, saida, erro = executar_sql(sql_da_escrita(pedido, auditoria, supersede=not ja_existia), prefixo)
    if rc != 0:
        return {**base, "veredito": ERRO, "motivo": MOTIVO_PORTA_AUSENTE,
                "detalhe": f"escrita falhou: {erro.strip()[:300]}"}
    contagens = (linhas_de_json(saida) or [{}])[0]
    return {**base, "veredito": JA_GERADA if ja_existia else GERADA, "motivo": "",
            "detalhe": "", "entrada_hash": entrada_hash, "pedido_id": pedido_id,
            "assunto": abordagem["assunto"], "cta": abordagem["cta"],
            "fatos_citados": pedido["proposed_action"]["fatos_citados"],
            "evidencia_ids": pedido["proposed_action"]["evidencia_ids"],
            "provedor": provedor_meta.get("provider"), "modelo": provedor_meta.get("model"),
            "gravados": int(contagens.get("aprovacoes") or 0),
            "ja_existia": int(contagens.get("aprovacoes") or 0) if ja_existia else 0,
            "pendentes_do_contato": int(contagens.get("pendentes_do_contato") or 0),
            "auditorias": int(contagens.get("auditorias") or 0)}


def desfazer(correlation_id: str, prefixo: str, confirmar: bool) -> dict:
    sql = sql_do_desfazer(correlation_id, confirmar)
    rc, saida, erro = executar_sql(sql, prefixo, permitir_delete=confirmar)
    if rc != 0:
        return {"veredito": ERRO, "detalhe": erro.strip()[:300]}
    if not confirmar:
        return {"veredito": "DRY_RUN", "seriam_apagados": int((linhas_de_json(saida) or [{"seriam_apagados": 0}])[0]
                                                             .get("seriam_apagados") or 0)}
    return {"veredito": "DESFEITO", "apagados": len(saida.split())}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def comando_planejar(politica: dict, contrato: dict, provedor: str) -> int:
    relatorio = {
        "gerador": GERADOR_VERSION, "card": politica["card"], "politica": f"{politica['nome']}@{politica['versao']}",
        "contrato": contrato.get("contract", {}).get("version"), "provedor": provedor,
        "pedido_de_aprovacao": {"tabela": TABELA_APROVACOES, "status": STATUS_PENDENTE,
                                "chave": "id = uuid5(NAMESPACE_URL, gerador-abordagem-v1:<org>:<entrada_hash>)"},
        "auditoria": {"tabela": TABELA_AGENT_RUNS},
        "escrita": {"tabelas": list(TABELAS_ESCRITA), "guarda": "DDL/escrita fora das duas tabelas/DELETE sem --confirmo RECUSAM"},
        "leitura": list(TABELAS_LEITURA),
        "prompt": {"arquivo": politica["prompt"]["arquivo"], "versao": politica["prompt"]["versao"]},
        "llm": {"executado": False, "provedor": provedor,
                "offline": "renderizador deterministico (sem modelo, sem token, sem custo)"},
        "credencial": {"fonte": politica["provedor"]["credencial_variavel"],
                       "no_argv": False, "no_relatorio": False},
        "revisao_do_provedor": "o provedor padrao e recusado se nao estiver em provedor.permitidos",
    }
    print(json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def comando_regras(politica: dict, acoes_do_contrato: list, status_do_contrato: list) -> int:
    relatorio = {
        "politica": f"{politica['nome']}@{politica['versao']}",
        "regra_de_avaliacao": politica["regra_de_avaliacao"],
        "acoes_do_contrato": sorted(acoes_do_contrato),
        "geram_abordagem": [{"acao": a["acao"], "canal": a["canal"], "tipo": a["tipo"],
                             "bloqueio_do_canal": a["bloqueio_do_canal"]}
                            for a in politica["acoes_de_abordagem"]],
        "nao_alcancadas": sorted(politica["nao_alcancadas"]),
        "acoes_do_contrato_sem_declaracao": sorted(set(acoes_do_contrato)
                                                   - {a["acao"] for a in politica["acoes_de_abordagem"]}
                                                   - set(politica["nao_alcancadas"])),
        "status_de_aprovacao_do_contrato": sorted(status_do_contrato),
        "limites": politica["limites"],
        "exigencia_de_citacao": politica["exigencia_de_citacao"],
        "afirmacoes_proibidas": politica["afirmacoes_proibidas"],
        "provedores_permitidos": politica["provedor"]["permitidos"],
        "prompt": f"{politica['prompt']['arquivo']}@{politica['prompt']['versao']}",
    }
    print(json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gerador de abordagem outbound v1 (TRE-W6-E02-T01)")
    parser.add_argument("--ambiente", default=None, choices=["dev", "homolog", AMBIENTE_RECUSADO])
    parser.add_argument("--organizacao", action="append", default=[])
    parser.add_argument("--jsonl", default=None, help="arquivo com uma organizacao por linha")
    parser.add_argument("--prefixo", default=None, help="porta de banco (ex.: docker exec -i pg-... psql ...)")
    parser.add_argument("--relatorio", default=None)
    parser.add_argument("--correlation-id", default=None)
    parser.add_argument("--triggered-by", default="hermes-dev-harness")
    parser.add_argument("--raiz", default=None)
    parser.add_argument("--politica", default=None)
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--provedor", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--injecao-de-teste", default=None,
                        help="le a abordagem de um arquivo JSON (teste do caminho pos-provedor)")
    parser.add_argument("--planejar", action="store_true")
    parser.add_argument("--regras", action="store_true")
    parser.add_argument("--desfazer", default=None)
    parser.add_argument("--confirmo", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = montar_parser()
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve() if args.raiz else RAIZ_PADRAO
    caminho_politica = achar_arquivo(raiz, args.politica or POLITICA_PADRAO)
    caminho_contrato = achar_arquivo(raiz, args.contrato or CONTRATO_DADOS_PADRAO)
    try:
        politica, contrato, acoes_do_contrato, status_do_contrato = carregar_politica(
            raiz, caminho_politica, caminho_contrato)
    except RecusaDePolitica as recusa:
        print(f"RECUSADO {recusa}", file=sys.stderr)
        return EXIT_POLITICA_RECUSADA

    provedor = args.provedor or politica["provedor"]["padrao"]
    if provedor not in politica["provedor"]["permitidos"]:
        print(f"RECUSADO provedor {provedor!r} nao esta em permitidos {politica['provedor']['permitidos']}",
              file=sys.stderr)
        return EXIT_POLITICA_RECUSADA

    if args.planejar:
        return comando_planejar(politica, contrato, provedor)
    if args.regras:
        return comando_regras(politica, acoes_do_contrato, status_do_contrato)

    if args.ambiente is None:
        print("uso: --ambiente dev|homolog (ou --planejar/--regras/--desfazer)", file=sys.stderr)
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
    started_at = datetime.now(timezone.utc).isoformat()

    if args.desfazer:
        resultado = desfazer(args.desfazer, args.prefixo, args.confirmo)
        print(json.dumps(resultado, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK if resultado.get("veredito") != ERRO else EXIT_FALHOU

    organizacoes = list(args.organizacao)
    if args.jsonl:
        caminho = achar_arquivo(raiz, args.jsonl)
        if not caminho.is_file():
            print(f"uso: jsonl ausente: {caminho}", file=sys.stderr)
            return EXIT_USO
        with caminho.open(encoding="utf-8") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha or linha.startswith("#"):
                    continue
                dado = json.loads(linha)
                if dado.get("organization_id"):
                    organizacoes.append(dado["organization_id"])
    if not organizacoes:
        print("uso: nenhuma empresa pedida (--organizacao ou --jsonl)", file=sys.stderr)
        return EXIT_USO

    chave = os.environ.get(politica["provedor"]["credencial_variavel"])
    base_url = args.base_url or os.environ.get(politica["provedor"]["base_url_variavel"])
    modelo = args.modelo or os.environ.get(politica["provedor"]["modelo_variavel"])

    relatorio = {"gerador": GERADOR_VERSION, "politica": f"{politica['nome']}@{politica['versao']}",
                 "prompt_version": politica["prompt"]["versao"], "ambiente": args.ambiente,
                 "correlation_id": correlation_id, "iniciado_em": started_at, "provedor": provedor,
                 "organizacoes": []}
    for organization_id in organizacoes:
        resultado = rodar_organizacao(organization_id, politica, args.prefixo, correlation_id,
                                      args.triggered_by, started_at, provedor, base_url, modelo, chave,
                                      args.injecao_de_teste)
        relatorio["organizacoes"].append(resultado)
        if resultado["veredito"] == GERADA:
            print(f"acao={resultado['acao']} canal={resultado['canal']} veredito={GERADA} "
                  f"provedor={resultado['provedor']} pedido={resultado['pedido_id']} "
                  f"citacoes={'/'.join(resultado['fatos_citados'])} org={organization_id}")
        elif resultado["veredito"] == JA_GERADA:
            print(f"acao={resultado['acao']} veredito={JA_GERADA} pedido={resultado['pedido_id']} "
                  f"(mesma entrada: nada duplicado) org={organization_id}")
        elif resultado["veredito"] == ABSTEVE:
            print(f"acao=- veredito={ABSTEVE} motivo={resultado['motivo']} org={organization_id}")
        else:
            print(f"veredito={resultado['veredito']} motivo={resultado['motivo']} "
                  f"detalhe={resultado['detalhe']} org={organization_id}")
    relatorio["resumo"] = {
        "organizacoes": len(relatorio["organizacoes"]),
        "geradas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == GERADA),
        "ja_geradas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == JA_GERADA),
        "recusadas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == RECUSADA),
        "abstidas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == ABSTEVE),
        "erros": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == ERRO),
    }
    if args.relatorio:
        caminho = Path(args.relatorio)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True),
                           encoding="utf-8")
    return EXIT_OK if relatorio["resumo"]["erros"] == 0 else EXIT_FALHOU


if __name__ == "__main__":
    sys.exit(main())
