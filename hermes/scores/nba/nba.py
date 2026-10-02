#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Next Best Action v1 (`nba-v1`) — proximo passo recomendado de UMA empresa (card TRE-W5-E07-T01).

O que este componente FAZ (e so isto): le a EVIDENCIA que ja existe no PostgreSQL
(`sales_intelligence`) — o tier REGISTRADO pelo card irmao TRE-W5-E06-T01 em `sync_events`, a
pesquisa, as hipoteses de dor, os sinais, os contatos, as interacoes e os scores — e decide, por uma
TABELA DE DECISAO DECLARADA (`politica-nba-v1.json`), qual e o proximo passo da empresa entre as
acoes do Data Contract V1.0 (`vocabularies.next_best_action`). A recomendacao e gravada em
`sales_intelligence.recommendations` (a tabela cujo proposito no contrato E "proximo passo
recomendado") e a rodada fica auditada em `sales_intelligence.agent_runs`.

O vocabulario das acoes (RESEARCH_MORE, FIND_DECISION_MAKER, SEND_EMAIL, PREPARE_LINKEDIN, WAIT,
FOLLOW_UP, CREATE_MEETING, NURTURE, DISQUALIFY) NAO existe em forma executavel neste arquivo: ele e
LIDO do Data Contract a cada rodada e a suite reprova se uma acao do contrato aparecer escrita aqui.
A tabela de decisao tambem nao: ela e o arquivo de politica — o codigo so sabe LER regras
(fato/operador/valor), nao quais regras existem. Trocar o limiar de espera, a ordem ou o motivo e
editar a politica, nunca este arquivo.

O que ele NAO faz, por desenho (declarado em `next-best-action-v1.json` -> lacunas):
  - **nao calcula score nenhum e nao recalcula o tier**: score e tier sao ENTRADA, lidos do banco.
    Sem registro TIER (`operation='TIER'` em `sync_events`) NAO existe NBA: a rodada RECUSA
    (`SEM_TIER`) e nada e gravado — a acao depende da prioridade ja decidida, nao de palpite;
  - **nao manda e-mail, nao cria atividade no Odoo, nao chama LLM** e nao abre rede: a v1 e
    deterministica e so escreve a recomendacao. Executar a acao e do caminho de integracao
    (W3/W6) e do aval humano (human_approvals, doc 12 §4);
  - **nao emite evento de outbox**: `NEXT_BEST_ACTION_CHANGED` (doc 12 §2) e do caminho de
    integracao; aqui a recomendacao fica registrada e pronta para o evento;
  - **nao inventa acao**: nenhuma regra casando, o veredito e ABSTEM (`SEM_REGRA`) e nada e gravado.
    A ausencia de resposta e abstencao, nunca acao default;
  - nao escreve em producao (ADR-005): `--ambiente dev|homolog`, `prod` e recusado com exit 4.

Onde a recomendacao e persistida (decisao DESTE card, declarada): em `recommendations`, com
`recommendation_type='NEXT_BEST_ACTION'`, `action` = o codigo da acao do contrato, `status='OPEN'`
(estado inicial de `recommendations.status`), `priority` e prazos vindos da POLITICA e `rationale`
com o motivo. A recomendacao e IMUTAVEL no conteudo: quando a evidencia muda, a nova recomendacao
SUPERSEDE a anterior (`status='SUPERSEDED'`, estado previsto no doc 12 §5) em vez de reescrever
historico. `confidence` fica NULL nesta v1: a regra e determinista e um numero ali fingiria uma
calibracao que nao existe (lacuna declarada).

Idempotencia (doc 06 §7: "retry nao pode criar duplicata"): o `id` da recomendacao e DETERMINISTICO
— `uuid5(nba-v1, <organization_id>:<entrada_hash>)`. A `entrada_hash` carrega a IDENTIDADE da
entrada desta decisao (tier registrado lido, contato escolhido, politica/regra, e os fatos NAO
numericos citados pela regra vencedora; contadores entram como o RESULTADO da comparacao, senao o
mesmo estado geraria uma recomendacao nova a cada dia). Mesma entrada => mesmo id => replay
(`JA_RECOMENDADA`, nada novo). Entrada nova => id novo => recomendacao NOVA com a anterior
preservada e SUPERSEDED.

Uso (o banco vive na VPS do ambiente — ADR-0008; quem fala com ele e a VPS):

  python3 hermes/scores/nba/nba.py --planejar
  python3 hermes/scores/nba/nba.py --regras
  python3 hermes/scores/nba/nba.py --ambiente dev --organizacao <uuid> \\
      --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \\
      --relatorio /tmp/nba-rodada.json
  python3 hermes/scores/nba/nba.py --desfazer <correlation_id> [--confirmo]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------------------
# Identidade do componente (espelha `hermes/scores/nba/next-best-action-v1.json`)
# ---------------------------------------------------------------------------------------
AGENTE = "nba"
PAPEL = "score_nba"
VERSAO = "1.0.0"
WORKFLOW = "scoring-nba"
WORKFLOW_VERSAO = "v1"

NBA_VERSION = "nba-v1"

TABELA_RECOMENDACOES = "sales_intelligence.recommendations"
TABELA_AGENT_RUNS = "sales_intelligence.agent_runs"

# O NBA ESCREVE apenas nestas duas: a recomendacao e a auditoria da rodada.
TABELAS_ESCRITA = (TABELA_RECOMENDACOES, TABELA_AGENT_RUNS)
# Tudo o que a rodada LE (declarado; a leitura e o que sustenta a decisao).
TABELAS_LEITURA = ("sales_intelligence.organizations", "sales_intelligence.sync_events",
                   "sales_intelligence.research_runs", "sales_intelligence.pain_hypotheses",
                   "sales_intelligence.signals", "sales_intelligence.contacts",
                   "sales_intelligence.interactions", "sales_intelligence.scores")

TIPO_DA_RECOMENDACAO = "NEXT_BEST_ACTION"
STATUS_INICIAL = "OPEN"
STATUS_SUPERSEDIDA = "SUPERSEDED"

OPERACAO_TIER = "TIER"
VERSAO_TIER = "tiering-v1"

RECOMENDADA = "RECOMENDADA"
JA_RECOMENDADA = "JA_RECOMENDADA"
RECUSADA = "RECUSADA"
ABSTEVE = "ABSTEVE"
ERRO = "ERRO"
VEREDITOS = (RECOMENDADA, JA_RECOMENDADA, RECUSADA, ABSTEVE, ERRO)

STATUS_AGENT_RUNS = {
    RECOMENDADA: "COMPLETED",
    JA_RECOMENDADA: "COMPLETED",
    RECUSADA: "REJECTED",
    ABSTEVE: "REJECTED",
    ERRO: "FAILED",
}

MOTIVO_SEM_TIER = "SEM_TIER"
MOTIVO_NAO_ENCONTRADA = "ORGANIZACAO_NAO_ENCONTRADA"
MOTIVO_SEM_REGRA = "SEM_REGRA"
MOTIVO_POLITICA_INCOERENTE = "POLITICA_INCOERENTE"
MOTIVO_PORTA_AUSENTE = "PORTA_DE_BANCO_AUSENTE"

AMBIENTES_PERMITIDOS = ("dev", "homolog")
AMBIENTE_RECUSADO = "prod"

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_USO = 2
EXIT_POLITICA_RECUSADA = 3
EXIT_RECUSOU_AMBIENTE = 4

CONTRATO_DADOS_PADRAO = "docs/data/data_contract_v1.json"
POLITICA_PADRAO = "hermes/scores/nba/politica-nba-v1.json"
CONTRATO_DO_COMPONENTE_PADRAO = "hermes/scores/nba/next-best-action-v1.json"

OPERADORES = ("igual", "diferente", "em", "nao_em", "maior_que", "maior_ou_igual", "menor_que",
              "menor_ou_igual", "nulo", "nao_nulo")

# Guarda de escrita (reprovada por suite): DDL e qualquer escrita fora das duas tabelas.
PADRAO_DDL = re.compile(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT\s+ON)\b", re.IGNORECASE)
PADRAO_INSERT = re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
PADRAO_UPDATE = re.compile(r"\bUPDATE\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
PADRAO_DELETE = re.compile(r"\bDELETE\s+FROM\s+([A-Za-z_][\w.]*)", re.IGNORECASE)


class RecusaDeEscrita(Exception):
    """Escrita que a guarda recusa (DDL, tabela fora do escopo, DELETE sem --confirmo)."""


class RecusaDePolitica(Exception):
    """Politica/contrato incoerente: o componente se recusa a decidir (fail-closed)."""


def descobrir_raiz_padrao() -> Path:
    """Raiz do repo por MARCADOR (a politica DESTE componente), nunca pela profundidade do arquivo."""
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for pasta in (base,) + tuple(base.parents):
            if (pasta / POLITICA_PADRAO).is_file():
                return pasta
    return Path.cwd()


RAIZ_PADRAO = descobrir_raiz_padrao()


# ---------------------------------------------------------------------------------------
# Contrato e politica (tudo o que decide vem de fora do codigo)
# ---------------------------------------------------------------------------------------
def ler_json(caminho: Path) -> dict:
    with caminho.open(encoding="utf-8") as fh:
        return json.load(fh)


def carregar_politica(caminho_politica: Path, caminho_contrato: Path) -> tuple[dict, dict, list]:
    """Le a politica e o Data Contract e confere a COERENCIA entre os dois (fail-closed).

    Devolve (politica, contrato, vocabulario_das_acoes). Recusa:
      - politica/contrato ilegiveis;
      - acao da politica que nao existe no vocabulario do contrato (acao inventada);
      - acao do vocabulario do contrato sem NENHUMA regra e sem declaracao em `nao_alcancadas`
        (buraco de cobertura: a acao existe e o componente nao sabe quando recomendar);
      - operador/fato/regra fora do contrato da politica (campo desconhecido, id repetido).
    """
    politica = ler_json(caminho_politica)
    contrato = ler_json(caminho_contrato)
    vocabulario = list((contrato.get("vocabularies") or {}).get("next_best_action") or [])
    if not vocabulario:
        raise RecusaDePolitica(f"{MOTIVO_POLITICA_INCOERENTE}: contrato sem vocabularies.next_best_action")
    regras = politica.get("regras") or []
    if not regras:
        raise RecusaDePolitica(f"{MOTIVO_POLITICA_INCOERENTE}: politica sem regras")

    fatos_declarados = set((politica.get("fatos") or {}).keys())
    fatos_declarados.discard("origem")
    ids = set()
    nao_alcancadas = set(politica.get("nao_alcancadas") or [])
    for regra in regras:
        rid = regra.get("id")
        if not rid or rid in ids:
            raise RecusaDePolitica(f"{MOTIVO_POLITICA_INCOERENTE}: id de regra ausente/repetido ({rid!r})")
        ids.add(rid)
        acao = regra.get("acao")
        if acao not in vocabulario:
            raise RecusaDePolitica(
                f"{MOTIVO_POLITICA_INCOERENTE}: acao {acao!r} da regra {rid} nao esta no vocabulario "
                f"do Data Contract ({', '.join(vocabulario)})")
        for campo in ("motivo", "descricao", "prioridade", "due_dias", "validade_dias", "quando"):
            if regra.get(campo) in (None, "", []):
                raise RecusaDePolitica(f"{MOTIVO_POLITICA_INCOERENTE}: regra {rid} sem {campo}")
        for condicao in regra["quando"]:
            fato = condicao.get("fato")
            if fato not in fatos_declarados:
                raise RecusaDePolitica(
                    f"{MOTIVO_POLITICA_INCOERENTE}: regra {rid} usa o fato {fato!r}, que nao esta em fatos")
            if condicao.get("op") not in OPERADORES:
                raise RecusaDePolitica(
                    f"{MOTIVO_POLITICA_INCOERENTE}: regra {rid} usa o operador {condicao.get('op')!r}")
    cobertas = {r["acao"] for r in regras}
    buracos = [a for a in vocabulario if a not in cobertas and a not in nao_alcancadas]
    if buracos:
        raise RecusaDePolitica(
            f"{MOTIVO_POLITICA_INCOERENTE}: acoes do contrato sem regra e sem declaracao de "
            f"nao_alcancadas: {', '.join(buracos)}")
    for papel in (politica.get("papeis_de_decisao") or []):
        papeis_do_contrato = list((contrato.get("vocabularies") or {}).get("decision_role") or [])
        if papeis_do_contrato and papel not in papeis_do_contrato:
            raise RecusaDePolitica(
                f"{MOTIVO_POLITICA_INCOERENTE}: papel {papel!r} nao esta no vocabulario decision_role")
    return politica, contrato, vocabulario


# ---------------------------------------------------------------------------------------
# Avaliacao das regras (pura: sem banco, sem relogio, sem rede)
# ---------------------------------------------------------------------------------------
def avaliar_condicao(fatos: dict, condicao: dict) -> bool:
    valor = fatos.get(condicao["fato"])
    esperado = condicao.get("valor")
    op = condicao["op"]
    if op == "igual":
        resultado = valor == esperado
    elif op == "diferente":
        resultado = valor != esperado
    elif op == "em":
        resultado = valor in (esperado or [])
    elif op == "nao_em":
        resultado = valor not in (esperado or [])
    elif op == "maior_que":
        resultado = valor is not None and esperado is not None and valor > esperado
    elif op == "maior_ou_igual":
        resultado = valor is not None and esperado is not None and valor >= esperado
    elif op == "menor_que":
        resultado = valor is not None and esperado is not None and valor < esperado
    elif op == "menor_ou_igual":
        resultado = valor is not None and esperado is not None and valor <= esperado
    elif op == "nulo":
        resultado = valor is None
    elif op == "nao_nulo":
        resultado = valor is not None
    else:  # pragma: no cover - barrado por carregar_politica
        raise RecusaDePolitica(f"{MOTIVO_POLITICA_INCOERENTE}: operador {op!r}")
    return not resultado if condicao.get("negado") else resultado


def decidir(fatos: dict, politica: dict) -> tuple[dict | None, dict | None]:
    """PRIMEIRA regra que casa vence. Nenhuma casando: (None, None) -> ABSTEM."""
    if not fatos.get("tier"):
        return None, None
    for regra in politica["regras"]:
        if all(avaliar_condicao(fatos, c) for c in regra["quando"]):
            return regra, fatos
    return None, None


def contato_escolhido(fatos: dict) -> str | None:
    return fatos.get("contato_escolhido_id")


# ---------------------------------------------------------------------------------------
# Identidade da entrada (idempotencia por conteudo, nunca pelo relogio da rodada)
# ---------------------------------------------------------------------------------------
def identidade_da_entrada(fatos: dict, politica: dict, regra: dict, acao: str) -> str:
    """sha256 canonico da ENTRADA desta decisao.

    Entram: organizacao, versao do componente, politica, regra vencedora, acao, a IDENTIDADE do
    registro TIER lido e o contato escolhido. Dos fatos citados pela regra vencedora entram os
    valores NAO numericos; fatos numericos (contadores como `dias_desde_a_abordagem`) entram como o
    RESULTADO da comparacao que a regra faz — senao o mesmo estado geraria id novo a cada dia e a
    idempotencia viraria enfeite. NAO entram: o relogio da rodada, o correlation_id e o texto
    renderizado (motivo/descricao mudam de forma sem mudar a decisao).
    """
    itens: dict = {}
    for condicao in regra["quando"]:
        fato = condicao["fato"]
        valor = fatos.get(fato)
        numerico = isinstance(valor, (int, float)) and not isinstance(valor, bool)
        itens[fato] = True if numerico else valor
    entrada = {
        "organizacao": fatos.get("organizacao"),
        "nba_version": NBA_VERSION,
        "politica": f"{politica['nome']}@{politica['versao']}",
        "regra": regra["id"],
        "acao": acao,
        "tier": {
            "registro_id": fatos.get("tier_registro_id"),
            "tier": fatos.get("tier"),
            "tier_version": fatos.get("tier_version"),
        },
        "contato_escolhido_id": contato_escolhido(fatos),
        "fatos_da_regra": itens,
    }
    canonico = json.dumps(entrada, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def id_da_recomendacao(organization_id: str, entrada_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NBA_VERSION}:{organization_id}:{entrada_hash}"))


# ---------------------------------------------------------------------------------------
# SQL — leitura dos fatos e escrita da recomendacao
# ---------------------------------------------------------------------------------------
def lit(texto: str | None) -> str:
    if texto is None:
        return "NULL"
    return "'" + str(texto).replace("'", "''") + "'"


def lit_json(objeto) -> str:
    return lit(json.dumps(objeto, ensure_ascii=False, sort_keys=True)) + "::jsonb"


def sql_dos_fatos(organization_id: str, politica: dict) -> str:
    """Uma linha (json) por empresa: o estado que sustenta a decisao, lido do banco."""
    papeis = list(politica["ordem_dos_papeis"])
    array_papeis = "ARRAY[" + ", ".join(lit(p) for p in papeis) + "]::text[]"
    canal_linkedin = lit(politica["canal_de_linkedin"])
    return f"""
SELECT json_build_object(
  'organizacao', o.id,
  'tier', t.tier,
  'tier_registro_id', t.id,
  'tier_em', t.created_at,
  'tier_version', t.source_version,
  'pesquisada', EXISTS (SELECT 1 FROM sales_intelligence.research_runs r
                        WHERE r.organization_id = o.id AND r.status = 'COMPLETED'),
  'dor_validada', EXISTS (SELECT 1 FROM sales_intelligence.pain_hypotheses p
                          WHERE p.organization_id = o.id
                          AND p.status IN ('VALIDATED','PARTIALLY_VALIDATED')),
  'dor_rejeitada', COALESCE((SELECT (p.status = 'REJECTED')
                             FROM sales_intelligence.pain_hypotheses p
                             WHERE p.organization_id = o.id
                             ORDER BY p.created_at DESC, p.id DESC LIMIT 1), false),
  'sinais_ativos', (SELECT COUNT(*) FROM sales_intelligence.signals s
                    WHERE s.organization_id = o.id
                    AND (s.expires_at IS NULL OR s.expires_at > NOW())),
  'contatos', (SELECT COUNT(*) FROM sales_intelligence.contacts c
               WHERE c.organization_id = o.id),
  'contatos_bloqueados', (SELECT COUNT(*) FROM sales_intelligence.contacts c
                          WHERE c.organization_id = o.id
                          AND (c.do_not_contact IS TRUE OR c.opt_out_email IS TRUE)),
  'contatos_contactaveis', (SELECT COUNT(*) FROM sales_intelligence.contacts c
                            WHERE c.organization_id = o.id
                            AND c.email IS NOT NULL
                            AND c.do_not_contact IS NOT TRUE AND c.opt_out_email IS NOT TRUE),
  'decisor_contactavel', EXISTS (SELECT 1 FROM sales_intelligence.contacts c
                                 WHERE c.organization_id = o.id
                                 AND c.decision_role = ANY({array_papeis})
                                 AND c.do_not_contact IS NOT TRUE AND c.opt_out_email IS NOT TRUE),
  'contato_escolhido_id', (SELECT c.id FROM sales_intelligence.contacts c
                           WHERE c.organization_id = o.id
                           AND c.decision_role = ANY({array_papeis})
                           AND c.do_not_contact IS NOT TRUE AND c.opt_out_email IS NOT TRUE
                           ORDER BY array_position({array_papeis}, c.decision_role), c.created_at, c.id
                           LIMIT 1),
  'contato_escolhido_papel', (SELECT c.decision_role FROM sales_intelligence.contacts c
                              WHERE c.organization_id = o.id
                              AND c.decision_role = ANY({array_papeis})
                              AND c.do_not_contact IS NOT TRUE AND c.opt_out_email IS NOT TRUE
                              ORDER BY array_position({array_papeis}, c.decision_role), c.created_at, c.id
                              LIMIT 1),
  'contato_tem_email_escolhido', COALESCE((SELECT (c.email IS NOT NULL) FROM sales_intelligence.contacts c
                                           WHERE c.organization_id = o.id
                                           AND c.decision_role = ANY({array_papeis})
                                           AND c.do_not_contact IS NOT TRUE AND c.opt_out_email IS NOT TRUE
                                           ORDER BY array_position({array_papeis}, c.decision_role),
                                                   c.created_at, c.id
                                           LIMIT 1), false),
  'canal_preferido', (SELECT c.preferred_channel FROM sales_intelligence.contacts c
                      WHERE c.organization_id = o.id
                      AND c.decision_role = ANY({array_papeis})
                      AND c.do_not_contact IS NOT TRUE AND c.opt_out_email IS NOT TRUE
                      ORDER BY array_position({array_papeis}, c.decision_role), c.created_at, c.id
                      LIMIT 1),
  'tem_contato_linkedin', EXISTS (SELECT 1 FROM sales_intelligence.contacts c
                                  WHERE c.organization_id = o.id AND c.linkedin_url IS NOT NULL
                                  AND c.do_not_contact IS NOT TRUE AND c.opt_out_email IS NOT TRUE),
  'abordada', EXISTS (SELECT 1 FROM sales_intelligence.interactions i
                      WHERE i.organization_id = o.id AND i.direction = 'OUTBOUND' AND i.channel = 'EMAIL'),
  'dias_desde_a_abordagem', (SELECT FLOOR(EXTRACT(EPOCH FROM (NOW() - MAX(i.occurred_at))) / 86400)::int
                             FROM sales_intelligence.interactions i
                             WHERE i.organization_id = o.id AND i.direction = 'OUTBOUND'
                             AND i.channel = 'EMAIL'),
  'respondeu', EXISTS (SELECT 1 FROM sales_intelligence.interactions i
                       WHERE i.organization_id = o.id AND i.direction = 'INBOUND'
                       AND i.occurred_at > COALESCE((SELECT MAX(j.occurred_at)
                                                     FROM sales_intelligence.interactions j
                                                     WHERE j.organization_id = o.id
                                                     AND j.direction = 'OUTBOUND' AND j.channel = 'EMAIL'),
                                                    'epoch'::timestamptz)),
  'sentimento_da_resposta', (SELECT i.sentiment FROM sales_intelligence.interactions i
                             WHERE i.organization_id = o.id AND i.direction = 'INBOUND'
                             ORDER BY i.occurred_at DESC, i.id DESC LIMIT 1),
  'canal_linkedin_declarado', {canal_linkedin}
)::text
FROM sales_intelligence.organizations o
LEFT JOIN LATERAL (
  SELECT s.id, s.source_version, s.created_at, s.request_payload->>'tier' AS tier
  FROM sales_intelligence.sync_events s
  WHERE s.entity_id = o.id AND s.operation = {lit(OPERACAO_TIER)}
  ORDER BY s.created_at DESC, s.id DESC LIMIT 1
) t ON TRUE
WHERE o.id = {lit(organization_id)} AND o.deleted_at IS NULL
  AND EXISTS (SELECT 1 FROM sales_intelligence.organizations x WHERE x.id = o.id);
""".replace("\n", " ")


def sql_da_escrita(organization_id: str, recomendacao: dict, auditoria: dict) -> str:
    """A rodada inteira numa transacao: supera a anterior, grava a recomendacao e audita."""
    rid = recomendacao["id"]
    return f"""
BEGIN;
UPDATE {TABELA_RECOMENDACOES}
   SET status = {lit(STATUS_SUPERSEDIDA)}
 WHERE organization_id = {lit(organization_id)}
   AND status = {lit(STATUS_INICIAL)}
   AND recommendation_type = {lit(TIPO_DA_RECOMENDACAO)}
   AND id <> {lit(rid)};
INSERT INTO {TABELA_RECOMENDACOES}
  (id, organization_id, contact_id, recommendation_type, action, description, rationale,
   confidence, priority, status, created_at, due_at, expires_at)
VALUES ({lit(rid)}, {lit(organization_id)}, {lit(recomendacao.get('contact_id'))},
        {lit(TIPO_DA_RECOMENDACAO)}, {lit(recomendacao['action'])}, {lit(recomendacao['descricao'])},
        {lit(recomendacao['rationale'])}, NULL, {int(recomendacao['prioridade'])},
        {lit(STATUS_INICIAL)}, NOW(), NOW() + INTERVAL '{int(recomendacao['due_dias'])} days',
        NOW() + INTERVAL '{int(recomendacao['validade_dias'])} days')
ON CONFLICT (id) DO NOTHING;
INSERT INTO {TABELA_AGENT_RUNS}
  (id, agent_name, agent_role, agent_version, workflow, workflow_version, organization_id,
   triggered_by, correlation_id, input, output, model, started_at, finished_at, status,
   tokens_input, tokens_output, estimated_cost)
VALUES ({lit(auditoria['id'])}, {lit(AGENTE)}, {lit(PAPEL)}, {lit(VERSAO)}, {lit(WORKFLOW)},
        {lit(WORKFLOW_VERSAO)}, {lit(organization_id)}, {lit(auditoria['triggered_by'])},
        {lit(auditoria['correlation_id'])}, {lit_json(auditoria['input'])},
        {lit_json(auditoria['output'])}, NULL, NOW(), NOW(), {lit(auditoria['status'])},
        NULL, NULL, NULL);
SELECT json_build_object(
  'gravados', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES} WHERE id = {lit(rid)}),
  'abertas', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES}
              WHERE organization_id = {lit(organization_id)} AND status = {lit(STATUS_INICIAL)}
              AND recommendation_type = {lit(TIPO_DA_RECOMENDACAO)}),
  'supersedidas', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES}
                   WHERE organization_id = {lit(organization_id)} AND status = {lit(STATUS_SUPERSEDIDA)}
                   AND recommendation_type = {lit(TIPO_DA_RECOMENDACAO)}),
  'auditoria', (SELECT COUNT(*) FROM {TABELA_AGENT_RUNS}
                WHERE correlation_id = {lit(auditoria['correlation_id'])}
                AND organization_id = {lit(organization_id)})
)::text;
COMMIT;
"""  # noqa: E501


def sql_do_estado_anterior(organization_id: str, recomendacao_id: str) -> str:
    return f"""
SELECT json_build_object(
  'ja_existia', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES} WHERE id = {lit(recomendacao_id)}),
  'supersedidas_antes', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES}
                         WHERE organization_id = {lit(organization_id)}
                         AND status = {lit(STATUS_SUPERSEDIDA)}
                         AND recommendation_type = {lit(TIPO_DA_RECOMENDACAO)}),
  'abertas_antes', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES}
                    WHERE organization_id = {lit(organization_id)}
                    AND status = {lit(STATUS_INICIAL)}
                    AND recommendation_type = {lit(TIPO_DA_RECOMENDACAO)})
)::text;
""".replace("\n", " ")


# ---------------------------------------------------------------------------------------
# Guarda de escrita (fail-closed)
# ---------------------------------------------------------------------------------------
def validar_sql(sql: str, permitir_delete: bool = False) -> None:
    """Recusa DDL e escrita em qualquer tabela que nao seja a recomendacao e a auditoria."""
    if PADRAO_DDL.search(sql):
        raise RecusaDeEscrita(f"SQL com DDL e recusado pela guarda: {sql[:120]}")
    for padrao, rotulo in ((PADRAO_INSERT, "INSERT"), (PADRAO_UPDATE, "UPDATE"),
                           (PADRAO_DELETE, "DELETE")):
        for alvo in padrao.findall(sql):
            tabela = alvo.lower()
            if tabela not in TABELAS_ESCRITA:
                raise RecusaDeEscrita(f"{rotulo} em {alvo} e recusado: nao esta nas tabelas do componente")
            if rotulo == "DELETE" and not permitir_delete:
                raise RecusaDeEscrita("DELETE e recusado fora do --desfazer --confirmo")


def executar_sql(sql: str, prefixo: str, permitir_delete: bool = False) -> tuple[int, str, str]:
    validar_sql(sql, permitir_delete=permitir_delete)
    comando = shlex.split(prefixo) + ["-v", "ON_ERROR_STOP=1", "-q", "-tA", "-F", "|", "-f", "-"]
    processo = subprocess.run(comando, input=sql, capture_output=True, text=True)
    return processo.returncode, processo.stdout, processo.stderr


def linhas_de_json(saida: str) -> list[dict]:
    objetos = []
    for linha in saida.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            valor = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if isinstance(valor, dict):
            objetos.append(valor)
    return objetos


# ---------------------------------------------------------------------------------------
# Rodada
# ---------------------------------------------------------------------------------------
def montar_recomendacao(fatos: dict, politica: dict, regra: dict, organization_id: str) -> dict:
    acao = regra["acao"]
    entrada_hash = identidade_da_entrada(fatos, politica, regra, acao)
    contato = contato_escolhido(fatos)
    rationale = (
        f"regra={regra['id']} motivo={regra['motivo']} | tier={fatos.get('tier')} "
        f"(registro {fatos.get('tier_registro_id')}) | politica={politica['nome']}@{politica['versao']} "
        f"| evidencia: pesquisa={fatos.get('pesquisada')} dor_validada={fatos.get('dor_validada')} "
        f"dor_rejeitada={fatos.get('dor_rejeitada')} sinais_ativos={fatos.get('sinais_ativos')} "
        f"contatos={fatos.get('contatos')} contatos_contactaveis={fatos.get('contatos_contactaveis')} "
        f"decisor_contactavel={fatos.get('decisor_contactavel')} "
        f"contato_escolhido={contato} abordada={fatos.get('abordada')} "
        f"dias_desde_a_abordagem={fatos.get('dias_desde_a_abordagem')} "
        f"respondeu={fatos.get('respondeu')} sentimento={fatos.get('sentimento_da_resposta')} "
        f"| entrada_hash={entrada_hash}"
    )
    return {
        "id": id_da_recomendacao(organization_id, entrada_hash),
        "action": acao,
        "descricao": regra["descricao"],
        "rationale": rationale,
        "prioridade": regra["prioridade"],
        "due_dias": regra["due_dias"],
        "validade_dias": regra["validade_dias"],
        "regra": regra["id"],
        "motivo": regra["motivo"],
        "contact_id": contato,
        "entrada_hash": entrada_hash,
    }


def rodar_organizacao(organization_id: str, politica: dict, prefixo: str, correlation_id: str,
                      triggered_by: str, started_at: str) -> dict:
    """Uma rodada para UMA empresa: le os fatos, decide, grava. Nada de rede, nada de LLM."""
    rc, saida, erro = executar_sql(sql_dos_fatos(organization_id, politica), prefixo)
    if rc != 0:
        return {"organizacao": organization_id, "veredito": ERRO, "motivo": MOTIVO_PORTA_AUSENTE,
                "detalhe": (erro or saida).strip()[:500]}
    linhas = linhas_de_json(saida)
    if not linhas:
        return {"organizacao": organization_id, "veredito": RECUSADA,
                "motivo": MOTIVO_NAO_ENCONTRADA, "detalhe": "empresa inexistente ou removida"}
    fatos = linhas[0]
    if not fatos.get("tier"):
        return {"organizacao": organization_id, "veredito": RECUSADA, "motivo": MOTIVO_SEM_TIER,
                "detalhe": "nenhum registro TIER em sync_events para a empresa",
                "fatos": fatos}
    regra, _ = decidir(fatos, politica)
    if regra is None:
        return {"organizacao": organization_id, "veredito": ABSTEVE, "motivo": MOTIVO_SEM_REGRA,
                "detalhe": "nenhuma regra da politica casou com a evidencia (abstencao)", "fatos": fatos}
    recomendacao = montar_recomendacao(fatos, politica, regra, organization_id)
    auditoria = {
        "id": str(uuid.uuid4()),
        "correlation_id": correlation_id,
        "triggered_by": triggered_by,
        "status": STATUS_AGENT_RUNS[RECOMENDADA],
        "input": {"organizacao": organization_id, "nba_version": NBA_VERSION,
                  "politica": f"{politica['nome']}@{politica['versao']}", "fatos": fatos,
                  "entrada_hash": recomendacao["entrada_hash"]},
        "output": {"veredito": RECOMENDADA, "regra": recomendacao["regra"],
                   "motivo": recomendacao["motivo"], "acao": recomendacao["action"],
                   "recomendacao": recomendacao["id"],
                   "recomendacoes": [recomendacao["id"]],
                   "contato_escolhido_id": recomendacao["contact_id"],
                   "prioridade": recomendacao["prioridade"], "gerado_em": started_at,
                   "llm": {"executado": False, "model": None, "tokens": None, "custo": None}},
    }
    rc, saida, erro = executar_sql(sql_do_estado_anterior(organization_id, recomendacao["id"]), prefixo)
    if rc != 0:
        return {"organizacao": organization_id, "veredito": ERRO, "motivo": MOTIVO_PORTA_AUSENTE,
                "detalhe": (erro or saida).strip()[:500]}
    anterior = (linhas_de_json(saida) or [{}])[0]
    rc, saida, erro = executar_sql(sql_da_escrita(organization_id, recomendacao, auditoria), prefixo)
    if rc != 0:
        return {"organizacao": organization_id, "veredito": ERRO, "motivo": MOTIVO_PORTA_AUSENTE,
                "detalhe": (erro or saida).strip()[:500]}
    resultado = (linhas_de_json(saida) or [{}])[-1]
    gravados = int(resultado.get("gravados", 0))
    veredito = RECOMENDADA if gravados == 1 and int(anterior.get("ja_existia", 0)) == 0 else JA_RECOMENDADA
    return {
        "organizacao": organization_id,
        "veredito": veredito,
        "motivo": recomendacao["motivo"],
        "regra": recomendacao["regra"],
        "acao": recomendacao["action"],
        "tier": fatos.get("tier"),
        "tier_registro_id": fatos.get("tier_registro_id"),
        "recomendacao_id": recomendacao["id"],
        "contato_escolhido_id": recomendacao["contact_id"],
        "prioridade": recomendacao["prioridade"],
        "entrada_hash": recomendacao["entrada_hash"],
        "gravados": gravados,
        "abertas": int(resultado.get("abertas", 0)),
        "supersedidas": int(resultado.get("supersedidas", 0)),
        "supersedidas_antes": int(anterior.get("supersedidas_antes", 0)),
        "ja_existia": int(anterior.get("ja_existia", 0)),
        "fatos": fatos,
    }


# ---------------------------------------------------------------------------------------
# Desfazer
# ---------------------------------------------------------------------------------------
def sql_do_desfazer(correlation_id: str, confirmar: bool) -> str:
    alvo = (f"(SELECT jsonb_array_elements_text(output->'recomendacoes') "
            f"FROM {TABELA_AGENT_RUNS} WHERE correlation_id = {lit(correlation_id)})")
    if not confirmar:
        return f"SELECT json_build_object('seriam_apagadas', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES} WHERE id::text IN {alvo}))::text;"
    return f"""
BEGIN;
DELETE FROM {TABELA_RECOMENDACOES} WHERE id::text IN {alvo};
SELECT json_build_object('apagadas', (SELECT COUNT(*) FROM {TABELA_RECOMENDACOES} WHERE id::text IN {alvo}))::text;
COMMIT;
""".replace("\n", " ")


def desfazer(correlation_id: str, prefixo: str, confirmar: bool) -> dict:
    rc, saida, erro = executar_sql(sql_do_desfazer(correlation_id, confirmar), prefixo,
                                   permitir_delete=confirmar)
    if rc != 0:
        return {"veredito": ERRO, "detalhe": (erro or saida).strip()[:500]}
    resultado = (linhas_de_json(saida) or [{}])[-1]
    return {"correlation_id": correlation_id, "confirmado": bool(confirmar), **resultado}


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def achar_arquivo(raiz: Path, caminho: str) -> Path:
    arquivo = Path(caminho)
    return arquivo if arquivo.is_absolute() else raiz / arquivo


def comando_planejar(politica: dict, contrato: dict, vocabulario: list) -> int:
    print(json.dumps({
        "componente": NBA_VERSION,
        "card": "TRE-W5-E07-T01",
        "politica": f"{politica['nome']}@{politica['versao']}",
        "contrato": "docs/data/data_contract_v1.json",
        "vocabulario_das_acoes": vocabulario,
        "regras": len(politica["regras"]),
        "papeis_de_decisao": politica["papeis_de_decisao"],
        "fatos": [f for f in politica["fatos"] if f != "origem"],
        "nao_alcancadas": politica.get("nao_alcancadas") or [],
        "persistencia": [TABELA_RECOMENDACOES, TABELA_AGENT_RUNS],
        "leitura": list(TABELAS_LEITURA),
        "llm": {"executado": False},
        "conexao": "nenhuma: --planejar nao abre porta de banco",
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def comando_regras(politica: dict, contrato: dict, vocabulario: list) -> int:
    print(json.dumps({
        "politica": f"{politica['nome']}@{politica['versao']}",
        "vocabulario_das_acoes": vocabulario,
        "regras": [{"id": r["id"], "acao": r["acao"], "motivo": r["motivo"],
                    "quando": r["quando"], "prioridade": r["prioridade"]}
                   for r in politica["regras"]],
        "acoes_cobertas": sorted({r["acao"] for r in politica["regras"]}),
        "acoes_do_contrato_sem_regra": [a for a in vocabulario
                                        if a not in {r["acao"] for r in politica["regras"]}],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Next Best Action v1 (TRE-W5-E07-T01)")
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
        politica, contrato, vocabulario = carregar_politica(caminho_politica, caminho_contrato)
    except RecusaDePolitica as recusa:
        print(f"RECUSADO {recusa}", file=sys.stderr)
        return EXIT_POLITICA_RECUSADA

    if args.planejar:
        return comando_planejar(politica, contrato, vocabulario)
    if args.regras:
        return comando_regras(politica, contrato, vocabulario)

    ambiente = args.ambiente
    if ambiente is None:
        print("uso: --ambiente dev|homolog (ou --planejar/--regras/--desfazer)", file=sys.stderr)
        return EXIT_USO
    if ambiente == AMBIENTE_RECUSADO:
        print("RECUSADO ambiente prod: ADR-005 (nada nasce em producao)", file=sys.stderr)
        return EXIT_RECUSOU_AMBIENTE
    if ambiente not in AMBIENTES_PERMITIDOS:
        print(f"uso: ambiente {ambiente!r} invalido", file=sys.stderr)
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

    relatorio = {"nba_version": NBA_VERSION, "politica": f"{politica['nome']}@{politica['versao']}",
                 "ambiente": ambiente, "correlation_id": correlation_id, "iniciado_em": started_at,
                 "organizacoes": []}
    for organization_id in organizacoes:
        resultado = rodar_organizacao(organization_id, politica, args.prefixo, correlation_id,
                                      args.triggered_by, started_at)
        relatorio["organizacoes"].append(resultado)
        if resultado["veredito"] == ERRO:
            print(f"ERRO {organization_id}: {resultado.get('detalhe')}", file=sys.stderr)
        elif resultado["veredito"] == RECUSADA:
            print(f"tier=- acao=- regra=- veredito={RECUSADA} motivo={resultado['motivo']} "
                  f"org={organization_id}")
        elif resultado["veredito"] == ABSTEVE:
            print(f"tier={resultado['fatos'].get('tier')} acao=- regra=- veredito={ABSTEVE} "
                  f"motivo={resultado['motivo']} org={organization_id}")
        else:
            print(f"tier={resultado['tier']} acao={resultado['acao']} regra={resultado['regra']} "
                  f"veredito={resultado['veredito']} gravados={resultado['gravados']} "
                  f"org={organization_id}")
    relatorio["resumo"] = {
        "organizacoes": len(relatorio["organizacoes"]),
        "recomendadas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == RECOMENDADA),
        "ja_recomendadas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == JA_RECOMENDADA),
        "recusadas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == RECUSADA),
        "abstidas": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == ABSTEVE),
        "erros": sum(1 for o in relatorio["organizacoes"] if o["veredito"] == ERRO),
    }
    texto = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
    if args.relatorio:
        caminho_relatorio = Path(args.relatorio)
        caminho_relatorio.parent.mkdir(parents=True, exist_ok=True)
        caminho_relatorio.write_text(texto, encoding="utf-8")
    print(texto)
    return EXIT_OK if relatorio["resumo"]["erros"] == 0 else EXIT_FALHOU


if __name__ == "__main__":
    sys.exit(main())
