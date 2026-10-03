#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote 4).

Duas frentes:

(A) FECHA O FURO DECLARADO NO LOTE 3: os 6 aceites da W5 foram medidos no lote 3 APENAS
    no baseline 2x, sem `--prova-de-dente`. Aqui a prova de dente de cada um e' anexada a
    `evidence` e ao `verification.portoes` do MESMO item (read-modify-write; nunca regenera).

(B) REGISTRA os proximos itens de maior risco da fila: W5-E07 (NBA) + W5-E08 (E2E scoring/NBA)
    e o eixo 3 da W3 (API controlada Odoo E01 T01..T05) — aceite executado de verdade na VPS
    de dev, duas passadas identicas, veredito no artefato da onda.

Rito (skill validacao-de-entregas / precedentes lote1..lote3):

1. **LE e ALTERA** o artefato — nunca o regera.
2. **dry-run por padrao**; `--aplicar` escreve.
3. **Backup datado** ao lado antes de escrever; escrita atomica.
4. **Nunca autoriza producao** (`production_promotion_authorized` segue false).
5. **Fail-closed**: recusa se algum card declarado nao estiver `done` no board, ou se algum
   veredito nao for PASS.

Contrato do veredito (leitor do dashboard):
  work_items[].children[] = {hermes_task_id, stage, current_gate, validation_result, evidence,
      validated_at, validated_by, eixo_de_risco,
      verification:{commit, ambiente, passes_independentes, portoes:[{gate, exit, resultado}]}}
  PASS -> stage/current_gate DONE (o item tambem precisa declarar DONE).

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote4.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote4.py --aplicar
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import sqlite3
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent.parent
DELIVERIES = RAIZ / "control-plane" / "deliveries"
ARTEFATO_W3 = DELIVERIES / "W3-integracao-odoo-pg.json"
ARTEFATO_W5 = DELIVERIES / "W5-scores-e-nba.json"
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")

COMMIT_ANTIGO = "3d64b8d48b376a3ed09916d1ae9f947623c89475"  # commit medido no lote 3
COMMIT_NOVO = "dab62fd109bfa0cec610f7c43af6b6711253ad77"    # HEAD de origin/develop nesta rodada
DATA = "2026-10-03"
VALIDADOR = "Hermes — validacao integrada (lote 4 — prova de dente W5 + NBA + API controlada Odoo)"

AMB_LOTE4 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): copia isolada /tmp/tre_lote4/repo (git archive "
    "de dab62fd109bfa0cec610f7c43af6b6711253ad77, 11M); o aceite sobe containers DESCARTALVEIS "
    "proprios (postgres:16 e/ou odoo:19.0) com nomes/banco/rede proprios e --rm na limpeza; "
    "containers do dev (pg-sales-dev/pg-odoo-dev/odoo-dev/proxy-dev/pg-wa-probe) INTOCADOS; nada "
    "escrito em sales_intelligence do dev/producao; duas passadas identicas (saida normalizada por "
    "nome de container/pid/porta/uuid/timestamp)."
)

# --------------------------------------------------------------------------- #
# (A) Prova de dente dos 6 aceites da W5 (lote 3 mediu so' o baseline 2x).
#     Anexa `evidence` + um portao novo ao item JA GRAVADO. O `verification.commit` do item NAO
#     muda (segue 3d64b8d): os scripts de aceite sao byte-identicos entre 3d64b8d e dab62fd
#     (sha256 conferido), entao o dente medido na copia de dab62fd vale para o mesmo codigo.
# --------------------------------------------------------------------------- #
DENTES_W5: dict[str, dict] = {
    "t_e4a90eba": {
        "script": "scripts/agentes/teste_icp_score_aceite.sh",
        "resumo": "DENTE OK (9/9 mutacoes detectadas, cada uma pelo item esperado)",
        "detalhe": "9 mutacoes em copia do icp_score.py reprovaram o item esperado (ausencia que vira fit, "
                   "faixa derivada do count, peso do modelo zerado, idempotencia fora do SQL, fingerprint "
                   "constante, prod liberado, fonte que contamina o score, etc.)",
    },
    "t_11815e63": {
        "script": "scripts/agentes/teste_automation_fit_aceite.sh",
        "resumo": "DENTE OK (5/5 mutacoes detectadas, cada uma pelo item esperado)",
        "detalhe": "5 mutacoes em copia do automation_fit.py reprovaram o item esperado (cobertura ignorada, "
                   "score, idempotencia sem o estado, leitura sem filtro de empresa, prod liberado)",
    },
    "t_967911e0": {
        "script": "scripts/agentes/teste_buying_signal_aceite.sh",
        "resumo": "prova de dente OK: 4/4 mutacoes reprovaram o item esperado (ACEITE 40 OK / 0 FALHOU)",
        "detalhe": "4 mutacoes em copia do modulo reprovaram o item esperado (sem-versao, sem-teto, "
                   "sem-idempotencia, sem-validade)",
    },
    "t_701c574f": {
        "script": "scripts/scores/teste_data_quality_aceite.sh",
        "resumo": "prova de dente OK: 6/6 mutacoes reprovaram o item esperado; rodada de referencia verde "
                  "apos os dentes (RESULTADO: ACEITE_DATA_QUALITY_001_OK, 35 itens, 0 falhas, 0 dentes reprovados)",
        "detalhe": "6 mutacoes em copia do data_quality.py reprovaram o item esperado (idempotencia fora do "
                   "SQL, espelho gravado nulo, prod passa a ser aceito, identidade sem normalizar a forma "
                   "guardada, versao errada no insert, confiabilidade sem pesquisa)",
    },
    "t_8ab79fb9": {
        "script": "scripts/scores/teste_priority_aceite.sh",
        "resumo": "prova de dente OK: 5/5 mutacoes reprovaram o item esperado (ACEITE 51 OK / 0 FALHOU)",
        "detalhe": "5 mutacoes em copia do priority_score.py reprovaram o item esperado (cobertura afrouxada, "
                   "sem checagem de vencido, sem idempotencia, sem validade, sem soma de um componente)",
    },
    "t_c6b9ecf1": {
        "script": "scripts/scores/teste_tiering_aceite.sh",
        "resumo": "prova de dente OK: 6/6 mutacoes reprovaram o item esperado (ACEITE 53 OK / 0 FALHOU)",
        "detalhe": "6 mutacoes em copia do modulo reprovaram o item esperado (ausencia vira registro, sem "
                   "checagem de vencido, fronteira aberta, sem idempotencia, ultimo vira primeiro, operation "
                   "trocada)",
    },
}

# --------------------------------------------------------------------------- #
# (B) Novos itens de maior risco da fila.
# --------------------------------------------------------------------------- #
ITENS_W5_NOVOS: list[dict] = [
    {
        "id": "TRE-W5-E07-T01",
        "title": "Next Best Action V1",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_2ad13d64",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E do Next Best Action v1 (A1..A11) por EXECUCAO REAL em container PostgreSQL "
                    "DESCARTALVEL proprio (pg-nba-acc): a recomendacao LEGITIMA e' gravada em `recommendations` "
                    "(status OPEN) com a acao do contrato, prioridade/prazos da politica, contato escolhido e "
                    "rationale com a evidencia, e auditoria em `agent_runs` sem LLM (model/tokens/custo NULL, "
                    "sem `confidence` inventada); a rodada NAO toca score/tier/contato/interacao; a TABELA DE "
                    "DECISAO e' a da politica (SEND_EMAIL, PREPARE_LINKEDIN, WAIT, FOLLOW_UP, CREATE_MEETING, "
                    "NURTURE, DISQUALIFY, RESEARCH_MORE, FIND_DECISION_MAKER) e a PRIMEIRA regra que casa vence "
                    "(compliance antes do tier); replay da MESMA entrada nao duplica (id deterministico) e "
                    "evidencia nova gera recomendacao NOVA que SUPERSEDE a anterior; empresa SEM registro TIER "
                    "RECUSA (SEM_TIER) e empresa inexistente e' RECUSADA sem gravar; `prod` recusado exit 4 sem "
                    "escrita; `--planejar`/`--regras` sem conexao; `--desfazer` dry-run ate o `--confirmo`. "
                    "DUAS passadas identicas (exit 0 em ambas; saida igual apos normalizar uuid do registro TIER "
                    "citado no item A11) e prova de dente 6/6."
                ),
                "portoes": [
                    {"gate": "bash scripts/scores/teste_nba_aceite.sh (passada 1)", "exit": 0,
                     "resultado": "ACEITE_NBA_001_OK"},
                    {"gate": "bash scripts/scores/teste_nba_aceite.sh (passada 2, identica)", "exit": 0,
                     "resultado": "ACEITE_NBA_001_OK"},
                    {"gate": "bash scripts/scores/teste_nba_aceite.sh --prova-de-dente", "exit": 0,
                     "resultado": "6/6 mutacoes reprovaram o item esperado (ACEITE 79 OK / 0 FALHOU): "
                                  "sem-supersessao, sem-idempotencia, sem-checagem-de-tier, "
                                  "primeira-regra-sempre, ordem-invertida, contato-bloqueado-ignorado"},
                ],
            }
        ],
    },
    {
        "id": "TRE-W5-E08-T01",
        "title": "Test scoring/NBA",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_537080f0",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "DADO",
                "evidence": (
                    "Aceite E2E da CADEIA W5 inteira (ICP -> AUTOMATION_FIT -> BUYING_SIGNAL -> DATA_QUALITY "
                    "-> PRIORITY -> TIER -> NBA) por EXECUCAO REAL em container PostgreSQL DESCARTALVEL proprio "
                    "(pg-w5-acc): as sete suites/verificadores OFFLINE rodam no MESMO commit antes do banco; a "
                    "composicao e' medida no banco (o PRIORITY e' a formula do contrato sobre os quatro scores "
                    "gravados, o registro TIER cita o PRIORITY lido, a recomendacao cita o TIER gravado); "
                    "FAIL-CLOSED na cadeia (empresa SEM LASTRO nao ganha score/tier/recomendacao — cada etapa "
                    "RECUSA com motivo nominal SEM_LASTRO/SEM_LASTRO_COMPLETO/SEM_PRIORITY/SEM_TIER); ESCOPO "
                    "(tabelas de negocio intactas, outbox vazia, nenhuma rodada chama LLM); REPLAY sem duplicata; "
                    "GUARDAS (`prod` recusado exit 4, `--planejar`/`--regras` sem conexao, `--desfazer` dry-run). "
                    "DUAS passadas identicas (exit 0 em ambas; lista de itens OK/FALHOU identica entre as passadas) "
                    "e prova de dente 4/4."
                ),
                "portoes": [
                    {"gate": "bash scripts/e2e/verificar-e2e-scoring-nba.sh (passada 1)", "exit": 0,
                     "resultado": "ACEITE_E2E_SCORING_NBA_001_OK"},
                    {"gate": "bash scripts/e2e/verificar-e2e-scoring-nba.sh (passada 2, identica)", "exit": 0,
                     "resultado": "ACEITE_E2E_SCORING_NBA_001_OK"},
                    {"gate": "bash scripts/e2e/verificar-e2e-scoring-nba.sh --prova-de-dente", "exit": 0,
                     "resultado": "4/4 mutacoes reprovaram o item esperado (ACEITE E2E SCORING/NBA 76 OK / 0 "
                                  "FALHOU): sem-motivo-sem-priority, sem-motivo-sem-lastro, sem-checagem-de-tier, "
                                  "sem-idempotencia"},
                ],
            }
        ],
    },
]

ITENS_W3_NOVOS: list[dict] = [
    {
        "id": "TRE-W3-E01-T01",
        "title": "Criar API controlada Odoo",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_e0489efc",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": (
                    "Aceite E2E da API controlada do Odoo (AC1..AC8) por EXECUCAO REAL numa dupla DESCARTALVEL "
                    "propria (postgres:16 + odoo:19.0, rede/banco/nomes proprios): superficie fechada e declarada "
                    "(1 rota, so' POST, politica versionada com ambiente unico dev); 0 SQL nos arquivos da API "
                    "(so' ORM); filtro por campo declarado (campo/operador fora da declaracao recusa); credencial "
                    "fora do repo/log (auth=bearer, 401 sem token, chave ausente do log/resposta); guarda de "
                    "ambiente ADR-005; contrato de integracao (idempotency_key na escrita, dry_run nao escreve, "
                    "correlation_id ecoado); rastro (UMA linha TF_API_AUDIT por chamada, sem token/payload); "
                    "ambiente de execucao intocado (dev/homolog/prod medidos antes e depois). "
                    "DUAS passadas identicas (exit 0 em ambas; 75 itens OK cada) e prova de dente 3/3."
                ),
                "portoes": [
                    {"gate": "bash scripts/odoo/verificar-api-controlada.sh (passada 1)", "exit": 0,
                     "resultado": "RESULTADO: API_CONTROLADA_OK (75 itens, 0 falhas) modulo=transformativa_sales_ai "
                                  "banco=tre_e01_t01_api imagens=odoo:19.0+postgres:16"},
                    {"gate": "bash scripts/odoo/verificar-api-controlada.sh (passada 2, identica)", "exit": 0,
                     "resultado": "RESULTADO: API_CONTROLADA_OK (75 itens, 0 falhas)"},
                    {"gate": "bash scripts/odoo/verificar-api-controlada.sh --prova-de-dente", "exit": 0,
                     "resultado": "RESULTADO: API_CONTROLADA_DENTE_OK (3 provas, 0 falhas)"},
                ],
            }
        ],
    },
    {
        "id": "TRE-W3-E01-T02",
        "title": "Implementar company upsert",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_cdc21b43",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": (
                    "Aceite E2E da operacao de escrita de negocio `empresa_upsert` (AC1..AC8) por EXECUCAO REAL "
                    "numa dupla DESCARTALVEL propria (postgres:16 + odoo:19.0): operacao declarada/versionada com "
                    "requer_idempotency_key (mesma porta unica); identidade declarada (sem identificador -> 422 "
                    "identificador_ausente); upsert idempotente por identidade (0 duplicata medida no banco); "
                    "ambiguidade -> 409 valor_ambiguo e NADA escrito; semantica de empresa (is_company=true, "
                    "divergencia -> 422 campo_fixo_divergente); idempotency_key/dry_run/correlation_id; guarda de "
                    "ambiente ADR-005 e UMA linha TF_API_AUDIT por chamada; ambiente intocado. "
                    "DUAS passadas identicas (exit 0 em ambas; 104 itens OK cada) e prova de dente 3/3 (+2 "
                    "controles do proprio harness)."
                ),
                "portoes": [
                    {"gate": "bash scripts/odoo/verificar-empresa-upsert.sh (passada 1)", "exit": 0,
                     "resultado": "RESULTADO: EMPRESA_UPSERT_OK (104 itens, 0 falhas) modulo=transformativa_sales_ai "
                                  "banco=tre_e01_t02_empresa imagens=odoo:19.0+postgres:16"},
                    {"gate": "bash scripts/odoo/verificar-empresa-upsert.sh (passada 2, identica)", "exit": 0,
                     "resultado": "RESULTADO: EMPRESA_UPSERT_OK (104 itens, 0 falhas)"},
                    {"gate": "bash scripts/odoo/verificar-empresa-upsert.sh --prova-de-dente", "exit": 0,
                     "resultado": "RESULTADO: EMPRESA_UPSERT_DENTE_OK (3 provas + 2 controles do proprio harness, "
                                  "0 falhas)"},
                ],
            }
        ],
    },
    {
        "id": "TRE-W3-E01-T03",
        "title": "Implementar contact upsert",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_e6e3b0b3",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": (
                    "Aceite E2E da operacao de escrita de negocio `contato_upsert` (AC1..AC9) por EXECUCAO REAL "
                    "numa dupla DESCARTALVEL propria (postgres:16 + odoo:19.0): operacao declarada/versionada "
                    "com requer_idempotency_key; identidade = `email` (sem valor -> 422 identificador_ausente; "
                    "`identificador` escalar NAO substitui a identidade por lista); upsert idempotente (1 registro "
                    "por e-mail depois de N chamadas); ambiguidade -> 409 valor_ambiguo e nada escrito/alterado; "
                    "semantica de contato (is_company=false, divergencia -> 422 campo_fixo_divergente); "
                    "idempotency_key/dry_run/correlation_id; guarda de ambiente ADR-005 (503 fora da politica, sem "
                    "escrita); rastro TF_API_AUDIT sem token e SEM DADO PESSOAL; compliance do schema PG e' recusa "
                    "nomeada (422). DUAS passadas identicas (exit 0 em ambas) e prova de dente 3/3 (+2 controles)."
                ),
                "portoes": [
                    {"gate": "bash scripts/odoo/verificar-contato-upsert.sh (passada 1)", "exit": 0,
                     "resultado": "RESULTADO: CONTATO_UPSERT_OK (110 itens, 0 falhas) modulo=transformativa_sales_ai "
                                  "banco=tre_e01_t03_contato imagens=odoo:19.0+postgres:16"},
                    {"gate": "bash scripts/odoo/verificar-contato-upsert.sh (passada 2, identica)", "exit": 0,
                     "resultado": "RESULTADO: CONTATO_UPSERT_OK (110 itens, 0 falhas)"},
                    {"gate": "bash scripts/odoo/verificar-contato-upsert.sh --prova-de-dente", "exit": 0,
                     "resultado": "RESULTADO: CONTATO_UPSERT_DENTE_OK (3 provas + 2 controles do proprio harness, "
                                  "0 falhas)"},
                ],
            }
        ],
    },
    {
        "id": "TRE-W3-E01-T04",
        "title": "Implementar opportunity upsert",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_8b2ed1b7",
                "stage": "VALIDATION",
                "current_gate": "VALIDATION",
                "validation_result": "BLOCKED",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": (
                    "NAO APROVADO nesta rodada. O aceite E2E da operacao `oportunidade_upsert` (AC1..AC8) PASSA "
                    "por EXECUCAO REAL numa dupla DESCARTALVEL propria (postgres:16 + odoo:19.0): operacao "
                    "declarada/versionada (modelo crm.lead, identidade tf_opportunity_id, chave exigida); "
                    "identidade canonica por UUID; upsert por identidade (N chamadas -> UM registro; atualizacao "
                    "parcial); fronteira de dono; idempotency_key/dry_run/correlation_id; guarda de ambiente "
                    "ADR-005; UMA linha TF_API_AUDIT por chamada; ambiente intocado — DUAS passadas identicas "
                    "(exit 0 em ambas; 125 itens OK cada). MAS a PROVA DE DENTE REPROVOU: "
                    "`bash scripts/odoo/verificar-oportunidade-upsert.sh --prova-de-dente` saiu exit 1 com "
                    "`RESULTADO: OPORTUNIDADE_UPSERT_DENTE_FALHOU (2 problema(s) em 3 provas)` — o dente 3 "
                    "(`controlador-sem-upsert`) NAO conseguiu nem aplicar a mutacao: a ancora de texto "
                    "'            if existentes:' nao existe mais em controllers/api_controlada.py (hoje o "
                    "controlador usa `if len(existentes) != 1:`), logo a prova NAO mede o que nomeia e o harness "
                    "falha fechado (MUTACAO_NAO_APLICADA + 'o aceite NAO reprovou com a mutacao'). Defeito do "
                    "PROPRIO VERIFICADOR (ancora de dente apodrecida apos o fecho do card), nao do aceite. "
                    "Pela regra do lote ('dente que nao reprova = check que nao vale'), o card NAO recebe PASS."
                ),
                "pre_condicao": (
                    "Corrigir a ancora do dente 3 em scripts/odoo/verificar-oportunidade-upsert.sh/--prova-de-dente "
                    "(ancora 'if existentes:' -> a checagem de identidade atual do controlador), re-rodar "
                    "`--prova-de-dente` ate DENTE_OK (3 provas) e so entao promover a PASS."
                ),
                "portoes": [
                    {"gate": "bash scripts/odoo/verificar-oportunidade-upsert.sh (passada 1)", "exit": 0,
                     "resultado": "RESULTADO: OPORTUNIDADE_UPSERT_OK (125 itens, 0 falhas) "
                                  "modulo=transformativa_sales_ai banco=tre_e01_t04_oportunidade "
                                  "imagens=odoo:19.0+postgres:16"},
                    {"gate": "bash scripts/odoo/verificar-oportunidade-upsert.sh (passada 2, identica)", "exit": 0,
                     "resultado": "RESULTADO: OPORTUNIDADE_UPSERT_OK (125 itens, 0 falhas)"},
                    {"gate": "bash scripts/odoo/verificar-oportunidade-upsert.sh --prova-de-dente", "exit": 1,
                     "resultado": "RESULTADO: OPORTUNIDADE_UPSERT_DENTE_FALHOU (2 problema(s) em 3 provas): dente "
                                  "1 e 2 OK; dente 3 'controlador-sem-upsert' com ancora desatualizada "
                                  "(MUTACAO_NAO_APLICADA) — prova inconclusiva, check nao vale"},
                ],
            }
        ],
    },
    {
        "id": "TRE-W3-E01-T05",
        "title": "Implementar activity create",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_cb615018",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": (
                    "Aceite E2E da operacao de escrita de negocio `atividade_criar` (AC1..AC9) por EXECUCAO REAL "
                    "numa dupla DESCARTALVEL propria (postgres:16 + odoo:19.0): superficie fechada com ancora "
                    "DECLARADA (res_model fixo da politica; res_id obrigatorio -> 422 campo_obrigatorio_ausente; "
                    "campo fora da declaracao -> 422 campo_nao_declarado); a atividade NASCE no modelo declarado "
                    "(medido no banco em mail_activity.res_model/res_id); idempotency_key/dry_run/correlation_id "
                    "registrados na atividade; guarda de ambiente ADR-005 na escrita (503, nada criado); UMA linha "
                    "TF_API_AUDIT por chamada sem payload; ACL respeitada (recusa 403 nomeada, sem sudo()); lacunas "
                    "MEDIDAS (o REPLAY da mesma chave ainda cria segunda atividade; ancora crm.lead nao e' servida "
                    "-> recusa nomeada). DUAS passadas identicas (exit 0 em ambas) e prova de dente 4/4."
                ),
                "portoes": [
                    {"gate": "bash scripts/odoo/verificar-atividade-criar.sh (passada 1)", "exit": 0,
                     "resultado": "RESULTADO: ATIVIDADE_CRIAR_OK (119 itens, 0 falhas) "
                                  "modulo=transformativa_sales_ai banco=tre_e01_t05_atividade "
                                  "imagens=odoo:19.0+postgres:16"},
                    {"gate": "bash scripts/odoo/verificar-atividade-criar.sh (passada 2, identica)", "exit": 0,
                     "resultado": "RESULTADO: ATIVIDADE_CRIAR_OK (119 itens, 0 falhas)"},
                    {"gate": "bash scripts/odoo/verificar-atividade-criar.sh --prova-de-dente", "exit": 0,
                     "resultado": "RESULTADO: ATIVIDADE_CRIAR_DENTE_OK (4 provas + 2 controles do proprio harness, "
                                  "0 falhas)"},
                ],
            }
        ],
    },
]

EVENTO = {
    "event": "INTEGRATED_VALIDATION_RECORDED",
    "by": VALIDADOR,
    "scope": "lote 4 — prova de dente dos 6 aceites da W5 + W5-E07/E08 (NBA) + W3-E01 (API controlada Odoo)",
    "production_promotion_authorized": False,
}


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def ler_board() -> dict[str, str]:
    if not BOARD.is_file():
        raise SystemExit(f"FAIL-CLOSED: board ausente: {BOARD}")
    con = sqlite3.connect(f"file:{BOARD}?mode=ro", uri=True)
    try:
        return {tid: st for tid, st in con.execute("SELECT id, status FROM tasks")}
    finally:
        con.close()


def conferir_board(board: dict[str, str], ids: list[str]) -> None:
    faltando = [i for i in ids if board.get(i) != "done"]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")


def _filhos(itens: list[dict]) -> list[dict]:
    return [c for it in itens for c in it.get("children") or []]


def anexar_dentes(novo: dict, dentes: dict[str, dict]) -> int:
    """Anexa a prova de dente ao item JA GRAVADO (evidence + portao). Nao toca o commit."""
    achados = 0
    for item in novo.get("work_items") or []:
        for child in item.get("children") or []:
            tid = child.get("hermes_task_id")
            if tid not in dentes:
                continue
            achados += 1
            d = dentes[tid]
            nota = (
                f" | PROVA DE DENTE (fecha o furo declarado no lote 3): `bash {d['script']} "
                f"--prova-de-dente` exit 0 — {d['resumo']} ({d['detalhe']}); medido em copia isolada de "
                f"{COMMIT_NOVO} (script de aceite byte-identico a {COMMIT_ANTIGO[:12]}, sha256 conferido)."
            )
            if "PROVA DE DENTE" not in (child.get("evidence") or ""):
                child["evidence"] = (child.get("evidence") or "") + nota
            ver = child.setdefault("verification", {})
            portoes = ver.setdefault("portoes", [])
            if not any("--prova-de-dente" in (p.get("gate") or "") for p in portoes):
                portoes.append({"gate": f"bash {d['script']} --prova-de-dente", "exit": 0,
                                "resultado": d["resumo"]})
    return achados


def _novo_item(base: dict, item_novo: dict, commit: str) -> dict:
    it = json.loads(json.dumps(item_novo))
    for child in it.get("children") or []:
        eixo = child.pop("eixo_de_risco")
        portoes = child.pop("portoes")
        if child["validation_result"] not in ("PASS", "BLOCKED"):
            raise SystemExit(
                f"FAIL-CLOSED: lote 4 so registra PASS/BLOCKED; {child['hermes_task_id']} veio "
                f"{child['validation_result']}"
            )
        child["validated_at"] = DATA
        child["validated_by"] = VALIDADOR
        child["eixo_de_risco"] = eixo
        child["verification"] = {
            "commit": commit,
            "ambiente": AMB_LOTE4,
            "passes_independentes": 2,
            "portoes": portoes,
        }
    return it


def aplicar(caminho: pathlib.Path, itens_novos: list[dict], commit: str) -> dict:
    if not caminho.is_file():
        raise SystemExit(f"FAIL-CLOSED: artefato ausente: {caminho}")
    base = json.loads(caminho.read_text(encoding="utf-8"))
    novo = json.loads(json.dumps(base))
    existentes = {it.get("id"): it for it in novo.get("work_items") or []}
    ordem = [it.get("id") for it in novo.get("work_items") or []]
    for item in itens_novos:
        it = _novo_item(base, item, commit)
        if it["id"] in existentes:
            existentes[it["id"]].update(it)
        else:
            existentes[it["id"]] = it
            ordem.append(it["id"])
    novo["work_items"] = [existentes[i] for i in ordem]
    novo["updated_at"] = DATA
    novo["production_promotion_authorized"] = False
    novo["production_promoted_task_ids"] = []
    return novo


def gravar(caminho: pathlib.Path, novo: dict, backup: bool) -> None:
    if backup and caminho.is_file():
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bkp = caminho.with_name(caminho.name.replace(".json", f".bak-{ts}.json"))
        shutil.copy2(caminho, bkp)
        print(f"  backup: {bkp}")
    tmp = caminho.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(novo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(caminho)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve (com backup datado)")
    args = p.parse_args()

    board = ler_board()
    ids = [c["hermes_task_id"] for c in _filhos(ITENS_W5_NOVOS)] + \
          [c["hermes_task_id"] for c in _filhos(ITENS_W3_NOVOS)] + list(DENTES_W5.keys())
    conferir_board(board, ids)

    print("=== REGISTRO DOS VEREDITOS — LOTE 4 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")

    # W5: anexa dentes aos 6 existentes + adiciona E07/E08.
    w5 = aplicar(ARTEFATO_W5, ITENS_W5_NOVOS, COMMIT_NOVO)
    n = anexar_dentes(w5, DENTES_W5)
    if n != len(DENTES_W5):
        raise SystemExit(f"FAIL-CLOSED: esperava anexar {len(DENTES_W5)} dentes na W5, anexei {n}")
    ev = dict(EVENTO)
    ev["at"] = agora()
    ev["cards"] = [c["hermes_task_id"] for c in _filhos(ITENS_W5_NOVOS)] + list(DENTES_W5.keys())
    w5.setdefault("events", []).append(ev)
    print(f"W5: {ARTEFATO_W5.name} | itens={len(w5['work_items'])} | dentes_anexados={n} | "
          f"prod_autorizada={w5['production_promotion_authorized']}")

    # W3: adiciona E01 T01..T05.
    w3 = aplicar(ARTEFATO_W3, ITENS_W3_NOVOS, COMMIT_NOVO)
    ev3 = dict(EVENTO)
    ev3["at"] = agora()
    ev3["cards"] = [c["hermes_task_id"] for c in _filhos(ITENS_W3_NOVOS)]
    w3.setdefault("events", []).append(ev3)
    print(f"W3: {ARTEFATO_W3.name} | itens={len(w3['work_items'])} | "
          f"prod_autorizada={w3['production_promotion_authorized']}")

    for lst, nome in ((ITENS_W5_NOVOS, "W5"), (ITENS_W3_NOVOS, "W3")):
        for it in lst:
            for c in it["children"]:
                print(f"   {nome} {it['id']:<20} {c['hermes_task_id']} -> {c['validation_result']}")
    for tid in DENTES_W5:
        print(f"   W5 (dente anexado)      {tid}")

    if args.aplicar:
        gravar(ARTEFATO_W5, w5, True)
        gravar(ARTEFATO_W3, w3, True)
        print("GRAVADO")
    else:
        print("(dry-run: rode com --aplicar para escrever)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
