#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote 7).

(A) W6 — TRE-W6-E06-T01 / t_fa1f31bf ("Atualizar Odoo a partir de respostas"): APPEND do work_item
    no artefato da onda W6 (que ja' existe e nao e' regerado). Aceite
    `scripts/agentes/teste_atualizacao_odoo_aceite.sh` por EXECUCAO REAL (stub local da API
    controlada + PostgreSQL descartavel proprio) + a suite offline
    `scripts/agentes/verificar_atualizacao_odoo.py --prova-de-dente` (12 dentes).

(B) W7 (eixo 2, fila da triagem logo apos o #77): a onda NAO tinha artefato de entrega — o lote
    REGISTRA a onda W7 (mesmo rito do lote 1 com a onda W1) com os 5 cards da fila: TRE-W7-E02-T01
    (Meta), TRE-W7-E06-T01 (eventos), TRE-W7-E04-T01 (LinkedIn), TRE-W7-E03-T01 (Google) e
    TRE-W7-E05-T01 (WhatsApp). Cada aceite executado de verdade contra pontas descartaveis locais.

Rito (skill validacao-de-entregas / precedentes lote1..lote6):
1. O W6 e' LE/ALTERA o artefato existente; a onda W7 e' CRIADA (nao existia) — nunca regerar o W6.
2. dry-run por padrao; `--aplicar` escreve.
3. backup datado ao lado antes de escrever; escrita atomica.
4. NUNCA autoriza producao (`production_promotion_authorized` segue false).
5. fail-closed: recusa se algum card declarado nao estiver `done` no board.

OVERRIDE DE AMBIENTE DECLARADO: o codigo da onda W7 NAO esta em develop (vive nos commits de topo
das branches `feature/TRE-W7-Exx-T01`, base 20e8ea67). O W6-E06 e' verificado em develop (54ee769).
Cada verificacao roda num clone isolado proprio na VPS de dev; nada de producao, nada escrito em
`sales_intelligence` do ambiente (so' em container descartavel), nenhum container do dev tocado.

Higiene (corrigida na raiz no commit b1cb7f7): os descartaveis dos harnesses sao removidos com
`docker rm -f -v` (removem os volumes anonimos). Volumes dangling medidos antes/depois da rodada.

Contrato do veredito (leitor do dashboard): trabalho em `work_items[].children[]` = {hermes_task_id,
stage, current_gate:'DONE' quando PASS, validation_result, evidence, validated_at, validated_by,
eixo_de_risco, verification:{commit, ambiente, passes_independentes:'2', portoes:[{gate, exit,
resultado}]}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote7.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote7.py --aplicar
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import shutil
import sqlite3
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent.parent
DELIVERIES = RAIZ / "control-plane" / "deliveries"
ARTEFATO_W6 = DELIVERIES / "W6-outbound-e-canais.json"
ARTEFATO_W7 = DELIVERIES / "W7-inbound-e-multicanal.json"
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")
PLUGIN_API = pathlib.Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")

# develop no momento da verificacao (arvore do W6-E06) e os topos das branches da onda W7.
COMMIT_W6E06 = "54ee7690422458fa42943bc7eb9ef189ce6626dd"
COMMIT_W7E02 = "c7155bc4e3100d5d5fb30ade05c9def928a7411b"
COMMIT_W7E06 = "618fa57efa54f1d66028a5877d577da8ecc5ee1a"
COMMIT_W7E04 = "96a275ee85006abd1f78eda1536e4a37c143e544"
COMMIT_W7E03 = "9e78655ddae9598f5292a31426d4a24d96d7dd3f"
COMMIT_W7E05 = "cfb45af4f2193090b778c386974735cc7c702036"

DATA = "2026-10-04"
VALIDADOR = "Hermes — validacao integrada (lote 7 — W6-E06 + onda W7)"

AMB_W6E06 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): clone isolado /tmp/tre_lote7/repo (bundle git do "
    "develop, 3.2M) NO COMMIT develop 54ee769. Aceite sobe UM container PostgreSQL DESCARTALVEL "
    "proprio (pg-e06-acc) + stub LOCAL da API controlada do Odoo em 127.0.0.1:8799 (o dev nao tem "
    "chave de API do Odoo); nenhuma credencial Titan, nenhum host externo. Containers do dev "
    "(pg-sales-dev/pg-odoo-dev/odoo-dev/proxy-dev/pg-wa-probe) INTOCADOS; nada escrito em "
    "sales_intelligence do ambiente. DUAS passadas byte-identicas (o aceite nao emite timestamp/"
    "uuid; diff cru = 0 linhas). A prova de dente e' a suite offline do mesmo card "
    "(verificar_atualizacao_odoo.py --prova-de-dente, 12 mutacoes) MAIS uma prova negativa a mao "
    "no aceite E2E: guarda de producao desativada em COPIA do componente -> o aceite REPROVA o item "
    "'prod RECUSA por desenho (exit 4)' com exit 1. Higiene: descartaveis removidos com "
    "'docker rm -f -v'; nenhum container ficou de pe."
)

AMB_W7 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): MESMO clone isolado /tmp/tre_lote7/repo (bundle "
    "git do develop + as branches W7). OVERRIDE DECLARADO: os 5 cards da onda W7 sao verificados "
    "nos respectivos topos de branch — nao em develop — porque o codigo da W7 nao esta mergeado em "
    "develop (merge-base 20e8ea67). Cada aceite sobe um PostgreSQL DESCARTALVEL proprio "
    "(pg-meta-acc / pg-evt-acc / pg-lk-e04 / pg-google-acc / pg-wa-acc) e, quando aplicavel, stubs/"
    "sinks LOCAIS (Graph API, sink IMAP); nenhuma credencial real (Meta/Google/Titan/LinkedIn), "
    "nenhum destino externo, nada de producao. Containers do dev INTOCADOS; nada escrito em "
    "sales_intelligence do ambiente. DUAS passadas por aceite com saida normalizada BYTE-IDENTICA "
    "(diff cru = 0 linhas em todos). HIGIENE MEDIDA (nao presumida): 3 dos 5 aceites da W7 "
    "(E03/E05/E06, nas branches) removem o container com 'docker rm -f' SEM '-v' — o mesmo defeito "
    "de volume anonimo ja' corrigido em develop no commit b1cb7f7 e que SEGUE ABERTO nessas "
    "branches; a rodada deixou 5 volumes dangling (todos criados dentro da janela do lote), "
    "REMOVIDOS apos a medicao — crescimento liquido ZERO do lote. Nenhum container ficou de pe."
)

# --------------------------------------------------------------------------- #
# W6 — E06 (append no artefato da onda W6)
# --------------------------------------------------------------------------- #
PORTOES_W6E06 = [
    {"gate": "bash scripts/agentes/teste_atualizacao_odoo_aceite.sh (passada 1)", "exit": 0,
     "resultado": "ACEITE_ATUALIZACAO_ODOO_RESPOSTAS_001_OK (45 itens, 0 falhas)"},
    {"gate": "bash scripts/agentes/teste_atualizacao_odoo_aceite.sh (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA a passada 1 (diff cru = 0 linhas); "
                  "ACEITE_ATUALIZACAO_ODOO_RESPOSTAS_001_OK (45 itens, 0 falhas)"},
    {"gate": "python3 scripts/agentes/verificar_atualizacao_odoo.py (suite offline)", "exit": 0,
     "resultado": "PASS (67 itens, 0 falhas)"},
    {"gate": "python3 scripts/agentes/verificar_atualizacao_odoo.py --prova-de-dente", "exit": 0,
     "resultado": "12/12 dentes reprovaram O ITEM NOMEADO (prod/loopback/categoria/ato/leitura/"
                  "envelope/auditoria/md5/SQL/chave/ON CONFLICT/escrita crua)"},
    {"gate": "aceite E2E com mutacao a mao (guarda de producao desativada em COPIA do componente)",
     "exit": 1,
     "resultado": "ACEITE_ATUALIZACAO_ODOO_RESPOSTAS_001_FALHOU (36 OK, 9 falhas); item nomeado "
                  "'FALHOU 1. prod RECUSA por desenho (exit 4)' — o aceite REPROVA a mutacao"},
]

EVID_W6E06 = (
    "Aceite E2E do card TRE-W6-E06-T01 por EXECUCAO REAL: PostgreSQL DESCARTALVEL proprio "
    "(pg-e06-acc) com a migration 0001 + fixtures de respostas ja' classificadas (materia-prima do "
    "W6-E05) + STUB local da API controlada do Odoo (127.0.0.1, Bearer). Mede: guardas (prod RECUSA "
    "exit 4; API fora de loopback RECUSA; porta de banco remota RECUSA; nenhuma guarda escreve "
    "trilha), dry-run sem escrita nem chamada, rodada confirmada com 3 respostas propagadas "
    "(RESPOSTA_INTERESSE + RESPOSTA_OPT_OUT + RESPOSTA_SEM_INTERESSE), BOUNCE como SEM_ATO e "
    "INTERESSE sem lead como SEM_VINCULO, 9 chamadas (3 atos x ler+upsert+atividade) com "
    "idempotency_key, evento/res_model/res_id no CRM, idempotencia por REPLAY (JA_ATUALIZADO, sem "
    "linha nova nem chamada nova), --desfazer (dry-run x --confirmo) preservando a trilha, escopo "
    "(as 9 tabelas do sales_intelligence intactas; interactions inalterado) e a chave nunca na "
    "saida. DUAS passadas byte-identicas (exit 0 em ambas; 45 itens, 0 falhas cada) + prova de dente "
    "em duas pontas: a suite offline com 12 mutacoes (12/12) E uma mutacao a mao no aceite E2E "
    "(guarda de producao desligada -> FALHOU o item nomeado, exit 1)."
)

# --------------------------------------------------------------------------- #
# W7 — 5 cards (onda criada)
# --------------------------------------------------------------------------- #

def _item(idx, titulo, tid, commit, evidence, portoes):
    return {
        "id": idx,
        "title": titulo,
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": tid,
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": evidence,
                "validated_at": DATA,
                "validated_by": VALIDADOR,
                "verification": {
                    "commit": commit,
                    "ambiente": AMB_W7,
                    "passes_independentes": "2",
                    "portoes": portoes,
                },
            }
        ],
    }


W7_ITENS = [
    _item(
        "TRE-W7-E02-T01", "Meta lead ingestion", "t_33bc1765", COMMIT_W7E02,
        (
            "Aceite E2E da INGESTAO DE LEADS META por EXECUCAO REAL contra pontas reais e "
            "descartaveis, sem token do Meta e sem tocar nada existente (ADR-005): STUB LOCAL da "
            "Graph API (127.0.0.1, GET com Bearer, contador de chamadas) + PostgreSQL DESCARTALVEL "
            "proprio (pg-meta-acc, migration 0001) + o componente "
            "hermes/agentes/inbound/ingestao_leads_meta.py gravando em sales_intelligence. WEBHOOKS "
            "ASSINADOS de verdade (HMAC-SHA256 do corpo cru com o app secret, inclusive assinatura "
            "ERRADA). Mede: guardas (prod RECUSA exit 4, banco remoto RECUSA, Graph fora de loopback "
            "RECUSA, --ingerir sem --confirmo = DRY_RUN sem gravar nem chamar a Graph); assinatura "
            "errada nao vira interacao; vinculo por e-mail e por TELEFONE formatado viram UMA linha; "
            "sem vinculo NAO inventa organizacao; dado insuficiente nao vira interacao; lead apagado "
            "= LEAD_INDISPONIVEL e erro persistente respeita o TETO de tentativas do contrato; "
            "idempotencia por leadgen_id (replay = JA_INGERIDO, zero chamada nova); PII fora do "
            "texto livre; escopo (so interactions/sync_events mudam); token/app secret fora da "
            "saida. DUAS passadas byte-identicas (exit 0 em ambas; 53 OK / 0 FALHOU cada) e prova de "
            "dente (--prova-de-dente) que REPROVA o item nomeado 5.8."
        ),
        [
            {"gate": "bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_META_LEADS_001_OK (53 OK / 0 FALHOU)"},
            {"gate": "bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh (passada 2, identica)",
             "exit": 0,
             "resultado": "saida BYTE-IDENTICA a passada 1 (diff cru = 0 linhas); "
                          "ACEITE_META_LEADS_001_OK (53 OK / 0 FALHOU)"},
            {"gate": "bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh --prova-de-dente",
             "exit": 0,
             "resultado": "ACEITE_META_LEADS_001_OK (54 OK / 0 FALHOU) com o dente: 'OK 10.1 dente: a "
                          "mutacao do resumo REPROVA exatamente o item 5.8'; mutante reproduz "
                          "'FALHOU 5.8 content_summary NAO tem e-mail em claro (esperado=0 obtido=1)'"},
        ],
    ),
    _item(
        "TRE-W7-E06-T01", "Event lead capture", "t_578d88a4", COMMIT_W7E06,
        (
            "Aceite E2E da CAPTURA DE LEAD DE EVENTO por EXECUCAO REAL num PostgreSQL DESCARTALVEL "
            "proprio (migration 0001) + o componente hermes/agentes/inbound/captura_evento.py "
            "gravando em sales_intelligence; nenhuma ponta externa. Mede a cadeia do card: assinatura "
            "fail-closed, primitivo de leitura, captura de lead de evento com consentimento "
            "obrigatorio e identificador forte no limiar de merge, barreira do vinculo do evento, "
            "idempotencia por event_id, mascaramento de e-mail, escopo de escrita e os DENTES DE "
            "PONTA medidos no BANCO (sem event_id e com capture_method inventado NAO viram cadastro). "
            "DUAS passadas byte-identicas (exit 0 em ambas; 60 itens, 0 falhas cada) e prova de dente "
            "INTERNA: a suite offline do componente (verificar_captura_evento.py --autoteste) roda "
            "5 mutacoes e todas reprovam o item DECLARADO (5/5)."
        ),
        [
            {"gate": "bash scripts/agentes/teste_captura_evento_aceite.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_CAPTURA_EVENTO_001_OK (60 itens, 0 falhas) — inclui o item "
                          "'suite offline + dentes verde' e os dentes de ponta do canal medidos no banco"},
            {"gate": "bash scripts/agentes/teste_captura_evento_aceite.sh (passada 2, identica)",
             "exit": 0,
             "resultado": "saida BYTE-IDENTICA a passada 1 (diff cru = 0 linhas); "
                          "ACEITE_CAPTURA_EVENTO_001_OK (60 itens, 0 falhas)"},
            {"gate": "dente INTERNO: python3 scripts/agentes/verificar_captura_evento.py --autoteste",
             "exit": 0,
             "resultado": "VERIFICADOR_CAPTURA_EVENTO_PASS (46 itens, 0 falhas, 5 dentes); D1..D5 "
                          "cada um 'item declarado REPROVOU sob mutacao' (o script nao expoe "
                          "--prova-de-dente; o dente e' o autoteste do proprio verificador)"},
        ],
    ),
    _item(
        "TRE-W7-E04-T01", "LinkedIn AI-assisted workflow", "t_6c170b51", COMMIT_W7E04,
        (
            "Aceite E2E do LINKEDIN ASSISTIDO v1 por EXECUCAO REAL num PostgreSQL DESCARTALVEL "
            "proprio (pg-lk-e04, migration 0001); o canal LinkedIn NAO tem rede neste componente (o "
            "aceite mede isso por grep no codigo). Mede o que a maquina PREPARA (rascunho + pedido de "
            "aprovacao) e REGISTRA (engajamento), e o que o HUMANO decide/publica; e prova cada "
            "proibicao (publicar/comentar/reagir/seguir/convidar/enviar DM/mencionar/responder/"
            "agendar/automatizar navegador/usar API do LinkedIn) com exit 5 e zero escrita de efeito. "
            "DUAS passadas byte-identicas (exit 0 em ambas; 70 itens, 0 falhas cada) e prova de dente "
            "3/3 (mutacao em COPIA do modulo: publicar pela maquina, sem aprovacao e sem evidencia "
            "— cada uma reprova o item nomeado)."
        ),
        [
            {"gate": "bash scripts/linkedin/verificar-linkedin-assistido.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_LINKEDIN_ASSISTIDO_OK (70 itens OK, 0 FALHOU)"},
            {"gate": "bash scripts/linkedin/verificar-linkedin-assistido.sh (passada 2, identica)",
             "exit": 0,
             "resultado": "saida BYTE-IDENTICA a passada 1 (diff cru = 0 linhas); "
                          "ACEITE_LINKEDIN_ASSISTIDO_OK (70 itens OK, 0 FALHOU)"},
            {"gate": "bash scripts/linkedin/verificar-linkedin-assistido.sh --prova-de-dente",
             "exit": 0,
             "resultado": "PROVA_DE_DENTE_OK (3/3); DENTE_OK 'publica' -> item 9.1, DENTE_OK "
                          "'sem-aprovacao' -> item 7.2, DENTE_OK 'evidencia' -> item 5.1"},
        ],
    ),
    _item(
        "TRE-W7-E03-T01", "Google lead attribution", "t_ba40919b", COMMIT_W7E03,
        (
            "Aceite E2E da ATRIBUICAO DE LEAD DO GOOGLE por EXECUCAO REAL contra pontas reais e "
            "descartaveis: STUB local do Google Ads (127.0.0.1) + PostgreSQL DESCARTALVEL proprio + o "
            "componente hermes/inbound/google/atribuicao_google.py. Mede: tabela declarada, "
            "fail-closed, trilha idempotente, confianca forte degradada no contrato, guarda de "
            "producao e limite de escrita (auditoria da fonte). DUAS passadas byte-identicas (exit 0 "
            "em ambas; 35 itens OK / 0 FALHOU cada) e prova de dente 3/3 (mutacao em COPIA do "
            "componente/contrato: confianca-forte, prod-liberado e escrita-fora-do-limite — cada uma "
            "REPROVA o item nomeado)."
        ),
        [
            {"gate": "bash scripts/inbound/aceite-atribuicao-google.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_GOOGLE_LEADS_001_OK; 35 itens OK / 0 FALHOU"},
            {"gate": "bash scripts/inbound/aceite-atribuicao-google.sh (passada 2, identica)",
             "exit": 0,
             "resultado": "saida BYTE-IDENTICA a passada 1 (diff cru = 0 linhas); "
                          "ACEITE_GOOGLE_LEADS_001_OK; 35 itens OK / 0 FALHOU"},
            {"gate": "bash scripts/inbound/aceite-atribuicao-google.sh --prova-de-dente", "exit": 0,
             "resultado": "ACEITE_GOOGLE_LEADS_001_OK; 38 itens OK / 0 FALHOU; dentes: 3 ok / 0 falhou "
                          "('confianca-forte'->1.3, 'prod-liberado'->13.1, "
                          "'escrita-fora-do-limite'->11.1, cada um REPROVOU o item esperado)"},
        ],
    ),
    _item(
        "TRE-W7-E05-T01", "WhatsApp engaged-lead workflow", "t_e5e497fd", COMMIT_W7E05,
        (
            "Aceite E2E do WORKFLOW DE LEAD ENGAJADO NO WHATSAPP por EXECUCAO REAL contra pontas "
            "reais e descartaveis: PostgreSQL DESCARTALVEL proprio + o componente "
            "hermes/agentes/inbound/whatsapp_lead.py gravando em sales_intelligence; nenhuma "
            "credencial do WhatsApp, nenhum destino externo. Mede: dados minimos (message_id), "
            "precedencia (OPT_OUT vence), negacao explicita do contrato, normalizacao do codigo do "
            "pais, bloqueio de contato, janela de atendimento, identidade desconhecida que nao vira "
            "interacao, mascaramento do telefone, trilha idempotente e os DENTES DE PONTA medidos no "
            "BANCO (descadastro por WhatsApp vira response_category=OPT_OUT). DUAS passadas "
            "byte-identicas (exit 0 em ambas; 68 itens, 0 falhas cada) e prova de dente INTERNA: a "
            "suite offline (verificar_whatsapp_lead.py --autoteste) roda 10 mutacoes, todas "
            "reprovando o item DECLARADO (10/10)."
        ),
        [
            {"gate": "bash scripts/agentes/teste_whatsapp_lead_aceite.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_WHATSAPP_LEAD_001_OK (68 itens, 0 falhas) — inclui 'suite offline + "
                          "dentes verde' e o dente de ponta do descadastro medido no banco"},
            {"gate": "bash scripts/agentes/teste_whatsapp_lead_aceite.sh (passada 2, identica)",
             "exit": 0,
             "resultado": "saida BYTE-IDENTICA a passada 1 (diff cru = 0 linhas); "
                          "ACEITE_WHATSAPP_LEAD_001_OK (68 itens, 0 falhas)"},
            {"gate": "dente INTERNO: python3 scripts/agentes/verificar_whatsapp_lead.py --autoteste",
             "exit": 0,
             "resultado": "VERIFICADOR_WHATSAPP_LEAD_PASS (70 itens, 0 falhas, 10 dentes); D1..D10 "
                          "cada um 'item declarado REPROVOU sob mutacao' (o script nao expoe "
                          "--prova-de-dente; o dente e' o autoteste do proprio verificador)"},
        ],
    ),
]


EVENTO = {
    "event": "INTEGRATED_VALIDATION_RECORDED",
    "by": VALIDADOR,
    "scope": "lote 7 — W6-E06 (Odoo a partir de respostas) + onda W7 (captura de leads inbound)",
    "production_promotion_authorized": False,
}

W7_IDS = [c["hermes_task_id"] for it in W7_ITENS for c in it["children"]]
W6E06_ID = "t_fa1f31bf"
TODOS_IDS = [W6E06_ID] + W7_IDS


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


def _append_itens(caminho: pathlib.Path, itens: list[dict]) -> tuple[dict, int]:
    base = json.loads(caminho.read_text(encoding="utf-8"))
    novo = json.loads(json.dumps(base))
    existentes = {it.get("id") for it in novo.get("work_items") or []}
    if any(it["id"] in existentes for it in itens):
        raise SystemExit(f"FAIL-CLOSED: item ja' presente em {caminho.name}: "
                         f"{sorted(it['id'] for it in itens if it['id'] in existentes)}")
    for it in itens:
        novo.setdefault("work_items", []).append(it)
    novo["updated_at"] = DATA
    novo["production_promotion_authorized"] = False
    novo["production_promoted_task_ids"] = []
    ev = dict(EVENTO)
    ev["at"] = agora()
    ev["cards"] = [c["hermes_task_id"] for it in itens for c in it["children"]]
    novo.setdefault("events", []).append(ev)
    return novo, len(itens)


def _novo_artefato_w7() -> dict:
    novo = {
        "delivery_id": "TRE-W7",
        "project": "Transformativa Revenue Engine",
        "release": "R1 — Foundation",
        "wave": ("W7 — Inbound e multicanal (captura de leads: site, Meta, Google, LinkedIn, "
                 "WhatsApp e eventos)"),
        "release_status": "IN_PROGRESS",
        "current_gate": "VALIDATION",
        "production_promotion_authorized": False,
        "updated_at": DATA,
        "human_approval": {
            "required": True,
            "status": "PENDING",
            "decision": "PENDING",
            "approved_by": None,
            "approved_at": None,
            "scope": ["validacao integrada do lote 7 (W6-E06 + onda W7)"],
            "production_promotion_authorized": False,
            "round": 1,
        },
        "work_items": [],
        "production_promoted_task_ids": [],
        "events": [],
    }
    return novo


def _montar_w7() -> dict:
    """A onda W7 ainda nao tem artefato: cria (mesmo rito do lote 1 com a onda W1)."""
    if ARTEFATO_W7.is_file():
        raise SystemExit(f"FAIL-CLOSED: {ARTEFATO_W7.name} ja' existe — este lote o CRIA; "
                         f"rever a decisao antes de sobrescrever")
    base = _novo_artefato_w7()
    for it in W7_ITENS:
        base["work_items"].append(json.loads(json.dumps(it)))
    ev = dict(EVENTO)
    ev["at"] = agora()
    ev["cards"] = list(W7_IDS)
    base["events"].append(ev)
    return base


def _validar_leitor(ids: list[str]) -> None:
    """Aceite da gravacao: o leitor do dashboard tem de classificar os cards como evidenciados."""
    if not PLUGIN_API.is_file():
        print(f"  AVISO: leitor ausente ({PLUGIN_API}) — aceite da gravacao nao verificado")
        return
    spec = importlib.util.spec_from_file_location("papi", str(PLUGIN_API))
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    colunas = m._delivery_lifecycle_task_columns(board="transformativa-revenue-engine")
    explicitos = m._delivery_explicit_done_task_ids(board="transformativa-revenue-engine")
    reter = [i for i in ids if i in (colunas or {})]
    print(f"  leitor: {len(ids)} cards; ainda retidos na coluna de validacao: {reter or 'nenhum'}")
    faltam = [i for i in ids if i not in (explicitos or set())]
    if faltam:
        raise SystemExit(f"FAIL-CLOSED: leitor NAO marcou como DONE explicito: {faltam}")
    print("  leitor: todos os cards classificados como DONE explicito (saem da coluna de validacao)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve (com backup datado)")
    args = p.parse_args()

    board = ler_board()
    faltando = [i for i in TODOS_IDS if board.get(i) != "done"]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")

    print("=== REGISTRO DOS VEREDITOS — LOTE 7 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")

    w6, n6 = _append_itens(ARTEFATO_W6, [{
        "id": "TRE-W6-E06-T01", "title": "Atualizar Odoo a partir de respostas",
        "stage": "DONE", "current_gate": "DONE",
        "children": [{
            "hermes_task_id": W6E06_ID, "stage": "DONE", "current_gate": "DONE",
            "validation_result": "PASS", "eixo_de_risco": "INTEGRACAO",
            "evidence": EVID_W6E06, "validated_at": DATA, "validated_by": VALIDADOR,
            "verification": {"commit": COMMIT_W6E06, "ambiente": AMB_W6E06,
                             "passes_independentes": "2", "portoes": PORTOES_W6E06},
        }],
    }])
    w7 = _montar_w7()

    print(f"W6: {ARTEFATO_W6.name} | +{n6} item | {W6E06_ID} -> PASS (Atualizar Odoo a partir de respostas)")
    print(f"W7: {ARTEFATO_W7.name} | +{len(w7['work_items'])} itens (ONDA CRIADA) | "
          f"prod_autorizada={w7['production_promotion_authorized']}")
    for it in W7_ITENS:
        for c in it["children"]:
            print(f"   W7 {it['id']:<18} {c['hermes_task_id']} -> {c['validation_result']}")

    if not args.aplicar:
        print("(dry-run: rode com --aplicar para escrever)")
        return 0

    gravar(ARTEFATO_W6, w6, True)
    gravar(ARTEFATO_W7, w7, True)
    print("GRAVADO")
    _validar_leitor(TODOS_IDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
