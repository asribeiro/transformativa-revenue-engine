#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra a VALIDACAO INTEGRADA (lote 8).

(A) CONSERTO DA INCONSISTENCIA DE CAMPO — no artefato da onda W3 os children
    t_2ee17829 / t_85cb2838 / t_ba84b412 / t_3bde06ab ficaram com
    `validation_result: PASS` e `current_gate: DONE` mas `stage: VALIDATION`
    (estado misto). Leitor real: `stage == "VALIDATION"` forca a coluna de
    validacao. A auditoria de TODOS os children de TODOS os artefatos vigentes
    achou exatamente estes 4 (nenhum FAIL/BLOCKED misto nos artefatos vigentes;
    os snapshots historicos nao sao lidos pelo leitor). Normalizacao: filho e
    item -> `stage: DONE` (+ `current_gate: DONE` no item). NAO se toca em
    `evidence`/`verification`. MEDIDO: mexer SO no filho tira o card da
    lifecycle mas NAO o poe em `explicit_done` (o leitor exige `DONE` tambem no
    ITEM); por isso o item tambem e normalizado — e o mesmo padrao dos itens
    irmaos da W3 (todos DONE).

(B) VEREDITOS — os 6 proximos da fila de triagem (ordens 83..88):
    1. W7-E01-T01  t_eb323dd7  (Website lead capture, topo da branch)
    2. W0-E04-T12  t_0a236da5  (revisao: inferencia de dominio com casamento exato)
    3. W0-E04-T02-D03 t_263416c5 (human-approval.yaml ignorado)
    4. W0-E04-T02-D02 t_285e6dfb (matriz de papel inconsistente aceita — fail-open)
    5. W0-E04-T01-D01 t_2d1ea557 (documento sem os oito itens proibidos)
    6. W0-E04-T02-D05 t_35c74a4c (entrada de tipo invalido estourava excecao)
    Cada um: aceite por EXECUCAO REAL (W7 no clone isolado da VPS de dev; os 5
    da W0 em clone limpo offline) + DUAS passadas byte-identicas com exit +
    DENTE que nomeia o item reprovado. W7 nao esta mergeada em develop: os cards
    dela sao verificados no topo de branch (commit declarado na evidence).

Rito (skill validacao-de-entregas / precedentes lote1..lote7):
1. dry-run por padrao; `--aplicar` escreve. backup datado antes de cada arquivo.
2. NUNCA autoriza producao (`production_promotion_authorized` segue false).
3. fail-closed: recusa se algum card declarado nao estiver `done` no board.

Contrato do veredito (leitor do dashboard): `work_items[].children[]` =
{hermes_task_id, stage:'DONE', current_gate:'DONE' quando PASS, validation_result,
evidence, validated_at, validated_by, eixo_de_risco,
verification:{commit, ambiente, passes_independentes:'2',
portoes:[{gate, exit, resultado}]}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote8.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote8.py --aplicar
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
ARTEFATO_W0 = DELIVERIES / "W0-governanca-e-baseline.json"
ARTEFATO_W3 = DELIVERIES / "W3-integracao-odoo-pg.json"
ARTEFATO_W7 = DELIVERIES / "W7-inbound-e-multicanal.json"
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")
PLUGIN_API = pathlib.Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")

DATA = "2026-10-04"
VALIDADOR = "Hermes — validacao integrada (lote 8 — 6 cards da fila + conserto W3)"

# --- (A) conserto da inconsistencia da W3 ---------------------------------- #
W3_FIX = {
    "TRE-W3-E04-T01": "t_2ee17829",
    "TRE-W3-E03-T01": "t_85cb2838",
    "TRE-W3-E02-T01": "t_ba84b412",
    "TRE-W3-E02-T02": "t_3bde06ab",
}

# --- (B) os 6 cards do lote ------------------------------------------------ #
COMMIT_W7E01 = "9ce9cf8920bc3a56dcbc0de7cd19ea2640b3590f"
# develop (arvore verificada pelos 5 cards de W0, suites offline/estaticas)
COMMIT_W0 = "307068b4cb0428d16c08e7636d71d3cc92df8dbf"

AMB_W7E01 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): clone isolado /tmp/tre_lote8/repo "
    "(bundle git somente da branch) NO TOPO da branch feature/TRE-W7-E01-T01 "
    "(commit 9ce9cf89), NAO mergeada em develop (merge-base 20e8ea67) — OVERRIDE "
    "DECLARADO: o card da W7 e' verificado no topo de branch. Aceite sobe UM "
    "container PostgreSQL DESCARTALVEL (pg-site-acc, postgres:16) + migration 0001; "
    "nenhuma credencial real, nenhum destino externo (pontas em 127.0.0.1), nada de "
    "producao. Containers do dev (pg-sales-dev/pg-odoo-dev/odoo-dev/proxy-dev/"
    "pg-wa-probe) INTOCADOS; nada escrito em sales_intelligence do ambiente. DUAS "
    "passadas com saida CRUA BYTE-IDENTICA (diff = 0 linhas). DENTE: o proprio aceite "
    "chama `verificar_captura_site.py --autoteste` (5 dentes D1..D5, cada um reprova o "
    "item declarado) e mede o 'dente de ponta' (base legal inventada RECUSA, medido no "
    "banco). Higiene MEDIDA: volumes 7 -> 7 (dangling 0 -> 0); containers 5 -> 5. Os "
    "volumes anonimos que o aceite deixa (usa 'docker rm -f' sem '-v') foram removidos "
    "por fora apos cada passada; crescimento liquido ZERO."
)

AMB_W0 = (
    "clone limpo (scratch) do repo do TRE em origin/develop = commit 307068b (arvore "
    "sob teste), python /opt/hermes/.venv. Verificacao OFFLINE/ESTATICA (sem banco, "
    "rede, credencial, runtime vivo ou producao): suites do JEV lidas do proprio repo. "
    "DUAS passadas byte-identicas com exit 0 (a suite do roteador emite tmpdir/"
    "decision_id volateis — normalizacao DECLARADA: 'jev-suite-<rand>' -> TMP e "
    "'dec-<hex>' -> ID; apos a normalizacao o diff cru = 0). Dente por MUTACAO em COPIA "
    "(nunca no arquivo versionado): cada dente REPROVA o ITEM NOMEADO do card."
)

# --- portoes / evidencia por card ------------------------------------------ #
# W7-E01
PORT_W7E01 = [
    {"gate": "bash scripts/agentes/teste_captura_site_aceite.sh (passada 1)", "exit": 0,
     "resultado": "ACEITE_CAPTURA_SITE_001_OK (46 itens, 0 falhas)"},
    {"gate": "bash scripts/agentes/teste_captura_site_aceite.sh (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA a passada 1 (diff cru = 0 linhas); "
                  "ACEITE_CAPTURA_SITE_001_OK (46 itens, 0 falhas)"},
    {"gate": "dente INTERNO: python3 scripts/agentes/verificar_captura_site.py --autoteste", "exit": 0,
     "resultado": "VERIFICADOR_CAPTURA_SITE_PASS (37 itens, 0 falhas, 5 dentes); "
                  "D1..D5 cada 'item declarado REPROVOU sob mutacao' (o aceite nao expoe "
                  "--prova-de-dente; o dente e' o autoteste do verificador) + dente de ponta "
                  "do aceite (item 13): base legal fora do vocabulario RECUSA, nenhum contato novo"},
]
EVID_W7E01 = (
    "Aceite E2E da CAPTURA DE LEAD DO SITE por EXECUCAO REAL contra pontas reais e "
    "descartaveis (ADR-005): container PostgreSQL DESCARTALVEL pg-site-acc + migration 0001 + "
    "o componente hermes/agentes/inbound/captura_site.py gravando em sales_intelligence; "
    "nenhuma ponta externa. Mede: guardas (prod RECUSA exit 4, --submissao sem porta local "
    "RECUSA, prefixo de banco remoto RECUSA em dev, --planejar/--conferir sem banco); sem "
    "--confirmo e' DRY_RUN (snapshot das 12 tabelas antes/depois igual); submissao valida com "
    "consentimento cria 1 organization (source WEBSITE_FORMULARIO, status DISCOVERED) + 1 "
    "contact + 1 interaction (WEBSITE/INBOUND/FORMULARIO_SITE) com UUID canonico e trilha "
    "CAPTURADO; identificador FORTE (CNPJ) casa a organizacao existente (REUSO, zero nova); "
    "identificador FRACO nao faz merge (REVIEW_REQUIRED, zero cadastro); consentimento e' "
    "barreira (sem opt-in nada e' cadastrado); idempotencia por submissao (JA_CAPTURADO, zero "
    "linha nova); escopo de escrita (so organizations/contacts/interactions/sync_events mudam); "
    "privacidade (e-mail/telefone do lead ausentes da evidencia); e o DENTE DE PONTA medido no "
    "BANCO (base legal fora do vocabulario nao vira cadastro). DUAS passadas byte-identicas "
    "(exit 0 em ambas; 46 itens, 0 falhas cada) e prova de dente pela suite offline com 5 "
    "mutacoes (5/5, cada uma reprova o item declarado)."
)

# W0-E04-T02-D02 (matriz inconsistente)
PORT_D02 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK (21/21 mutacoes detectadas)"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada de tmpdir/decision_id "
                  "(diff cru = 0); PASS (63 itens, 0 falhas) + autoteste 21/21"},
    {"gate": "dente MANUAL: recusa da matriz removida em COPIA do roteador "
             "('problemas_papeis = _inconsistencias_dos_papeis(papeis)' -> '[]')", "exit": 0,
     "resultado": "o item nomeado REPROVA: 'T02/harness: politica de papel que da deploy ao "
                  "Sales AI e recusada' (4 itens reprovados no total) — a recusa fail-closed "
                  "do PoliticaInvalida e' o que o item mede"},
]
EVID_D02 = (
    "Reexecucao real da suite do roteador do JEV (TRE-W0-E04-T02) no develop 307068b: item "
    "T02/harness 'politica de papel que da deploy ao Sales AI e recusada' PASSA — a matriz "
    "Dev x Sales inconsistente (mesma credencial permitida e proibida) passa a REPROVAR o "
    "carregamento da politica (PoliticaInvalida), e nao mais ser aceita em silencio. Dente: "
    "removendo a recusa em COPIA do roteador o item nomeado falha ('ACEITOU politica com "
    "Sales AI recebendo GITHUB_TOKEN'), provando que o item morde a causa."
)

# W0-E04-T02-D03 (human-approval.yaml)
PORT_D03 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK (21/21); item "
                  "'D03: a fonte human-approval.yaml entra na camada Human Approval sem depender "
                  "de role: (9 declaracoes nao executam)' OK [9 declaracoes da fonte bloqueiam "
                  "com BLOCK, sem chave role:]"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada (diff cru = 0); PASS + autoteste 21/21"},
    {"gate": "dente MANUAL: mutacao 'inverter a precedencia: sem Human Approval' "
             "(if humano: -> if False and humano:) em COPIA do roteador", "exit": 0,
     "resultado": "o item nomeado REPROVA: 'D03: a fonte human-approval.yaml entra na camada "
                  "Human Approval sem depender de role:' (11 itens reprovados no total) — "
                  "removida a camada, as 9 declaracoes da fonte deixam de bloquear"},
]
EVID_D03 = (
    "Reexecucao real da suite do roteador do JEV no develop 307068b: o item D03 PASSA — a "
    "fonte hermes/policies/human-approval.yaml (que NAO declara chave role:) entra na camada "
    "Human Approval por NOME de arquivo, e as 9 declaracoes dela terminam em BLOCK. Corrige o "
    "defeito em que a camada nunca lia o arquivo (filtro por 'role:' nunca satisfeito) e uma "
    "acao exigente de aprovacao humana decidia EXECUTAR. Dente: com a precedencia humana "
    "ignorada em COPIA do roteador, o item nomeado falha."
)

# W0-E04-T02-D05 (entrada de tipo invalido)
PORT_D05 = [
    {"gate": "python3 scripts/validar_jev_guardrails.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (74 itens, 0 falhas) + autoteste OK (20/20 mutacoes "
                  "detectadas); item 'cobertura D05 — entrada de tipo invalido termina em BLOCK "
                  "com recibo de 13 campos, sem excecao' OK"},
    {"gate": "python3 scripts/validar_jev_guardrails.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (74 itens, 0 falhas) + autoteste 20/20"},
    {"gate": "dente INTERNO: mutacao 'fail-closed removido na entrada de tipo invalido'", "exit": 0,
     "resultado": "a mutacao REPROVA os itens 'cobertura D05 — entrada de tipo invalido termina "
                  "em BLOCK com recibo de 13 campos, sem excecao' e 'guardrail fail-closed — "
                  "REPROVA: sinal desconhecido e entrada invalida nao liberam'"},
]
EVID_D05 = (
    "Reexecucao real da suite de guardrails do JEV no develop 307068b: o item de cobertura D05 "
    "PASSA — payload que nao e' mapa ('texto solto', ['lista']) e 'sinais' em lista terminam em "
    "BLOCK com recibo de 13 campos e o guardrail payload_valido registrado, NUNCA em excecao nao "
    "tratada. Corrige o defeito em que entrada de tipo invalido estourava ValueError/"
    "AttributeError (sem recibo, sem lane, sem rastro). Dente: a mutacao que remove a checagem "
    "de tipo da borda faz o item nomeado falhar."
)

# W0-E04-T01-D01 (documento sem os oito itens)
PORT_D01 = [
    {"gate": "python3 scripts/verificar_jev_policy.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (42 itens, 0 falhas) + autoteste OK (12/12 mutacoes "
                  "detectadas); item 'documento cobre os oito temas proibidos' OK"},
    {"gate": "python3 scripts/verificar_jev_policy.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (42 itens, 0 falhas) + autoteste 12/12"},
    {"gate": "dente MANUAL: documento sem o tema 'rollback' (doc.replace('rollback','retrocesso') "
             "em memoria, mesmo verificador)", "exit": 0,
     "resultado": "o item nomeado REPROVA: 'documento cobre os oito temas proibidos' "
                  "FALHOU [faltando=['rollback']]; no documento real o item PASSA"},
]
EVID_D01 = (
    "Reexecucao real do verificador da politica JEV no develop 307068b: o item 'documento cobre "
    "os oito temas proibidos' PASSA — o documento docs/architecture/jev-decision-policy-v1.md "
    "lista os OITO itens proibidos (producao, primeiro contato, proposta, arquitetura, rollback, "
    "exclusao de dado, credencial, publicacao), sem resumir a lista em prosa. Corrige o defeito "
    "em que a prosa do documento dizia seis dos oito (divergencia documento x maquina invisivel a "
    "olho nu). Dente: retirando 'rollback' de uma COPIA do documento, o item nomeado falha."
)

# W0-E04-T12 (revisao: casamento exato de dominio)
PORT_T12 = [
    {"gate": "python3 scripts/verificar_dominios_sensiveis.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (32 itens, 0 falhas) + AUTOTESTE 4/4 mutacoes reprovadas"},
    {"gate": "python3 scripts/verificar_dominios_sensiveis.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (32 itens, 0 falhas) + autoteste 4/4"},
    {"gate": "dente INTERNO: mutacao 'tira os PLURAIS do vocabulario (achado da revisao T12)'", "exit": 0,
     "resultado": "a mutacao REPROVA a suite: 'mutacao reprovada: tira os PLURAIS do vocabulario "
                  "(achado da revisao T12)' — o item que a revisao T12 exige morde a regressao"},
]
EVID_T12 = (
    "REVISAO INDEPENDENTE (perfil tester) do commit e085ffc, reproduzida por execucao real no "
    "develop 307068b: a suite scripts/verificar_dominios_sensiveis.py PASSA (32 itens, 0 falhas) "
    "e o autoteste reprova 4/4 mutacoes, incluindo 'tira os PLURAIS do vocabulario (achado da "
    "revisao T12)'. A causa original (casamento por PREFIXO de 4 caracteres em CONCEITOS_DE_ACAO: "
    "implementador~implantacao, versionado~versao, entrar~entrega, registro~dado de cliente) esta "
    "travada: nao houve afrouxamento (CONCEITOS_DE_ACAO e REGRAS_DE_ACAO_HUMANA intocadas; "
    "'exclusao de registro de auditoria' segue resolvendo para exclusao_de_dado_de_cliente). "
    "NOTA DATADA: no commit e085ffc a suite tinha 28 itens e autoteste 3/3; no develop 307068b "
    "cresceu para 32 itens e 4/4 (o item de plurais e' o 4o dente) — a contagem citada no card e' "
    "leitura daquele commit, nao a de hoje."
)


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


# --------------------------------------------------------------------------- #
def consertar_w3(base: dict) -> tuple[dict, list[tuple]]:
    """Normaliza os 4 itens/children mistos da W3 (PASS com stage VALIDATION)."""
    novo = json.loads(json.dumps(base))
    ajustes = []
    for item in novo.get("work_items") or []:
        iid = item.get("id")
        if iid not in W3_FIX:
            continue
        child = (item.get("children") or [{}])[0]
        if child.get("hermes_task_id") != W3_FIX[iid]:
            continue
        ajustes.append((
            iid, child.get("hermes_task_id"),
            (item.get("stage"), item.get("current_gate"), child.get("stage")),
            ("DONE", "DONE", "DONE"),
        ))
        item["stage"] = "DONE"
        item["current_gate"] = "DONE"
        child["stage"] = "DONE"
    return novo, ajustes


def _evento(escopo: str, cards: list[str]) -> dict:
    return {
        "event": "INTEGRATED_VALIDATION_RECORDED",
        "by": VALIDADOR,
        "scope": escopo,
        "production_promotion_authorized": False,
        "at": agora(),
        "cards": cards,
    }


def _child(tid, commit, evidence, portoes, amb, eixo):
    return {
        "hermes_task_id": tid,
        "stage": "DONE",
        "current_gate": "DONE",
        "validation_result": "PASS",
        "eixo_de_risco": eixo,
        "evidence": evidence,
        "validated_at": DATA,
        "validated_by": VALIDADOR,
        "verification": {
            "commit": commit,
            "ambiente": amb,
            "passes_independentes": "2",
            "portoes": portoes,
        },
    }


def _item(idx, titulo, tid, commit, evidence, portoes, amb, eixo="INTEGRACAO"):
    return {
        "id": idx, "title": titulo, "stage": "DONE", "current_gate": "DONE",
        "children": [_child(tid, commit, evidence, portoes, amb, eixo)],
    }


W0_NOVOS = [
    _item("TRE-W0-E04-T12", "Revisao independente: inferencia de dominio sensivel com casamento exato",
          "t_0a236da5", COMMIT_W0, EVID_T12, PORT_T12, AMB_W0, "CONTRATO/API"),
    _item("TRE-W0-E04-T02-D03", "DEFEITO: camada de Human Approval ignorava human-approval.yaml",
          "t_263416c5", COMMIT_W0, EVID_D03, PORT_D03, AMB_W0, "CONTRATO/API"),
    _item("TRE-W0-E04-T02-D02", "DEFEITO: matriz de papel inconsistente era aceita em silencio (fail-open)",
          "t_285e6dfb", COMMIT_W0, EVID_D02, PORT_D02, AMB_W0, "CONTRATO/API"),
    _item("TRE-W0-E04-T01-D01", "DEFEITO: documento da politica nao listava os oito itens proibidos",
          "t_2d1ea557", COMMIT_W0, EVID_D01, PORT_D01, AMB_W0, "CONTRATO/API"),
    _item("TRE-W0-E04-T02-D05", "DEFEITO fail-closed quebrado: entrada de tipo invalido estourava excecao",
          "t_35c74a4c", COMMIT_W0, EVID_D05, PORT_D05, AMB_W0, "CONTRATO/API"),
]

W7_NOVO = _item(
    "TRE-W7-E01-T01", "Website lead capture", "t_eb323dd7", COMMIT_W7E01,
    EVID_W7E01, PORT_W7E01, AMB_W7E01, "INTEGRACAO",
)

IDS = ["t_2ee17829", "t_85cb2838", "t_ba84b412", "t_3bde06ab"] + \
      [c["hermes_task_id"] for it in W0_NOVOS for c in it["children"]] + \
      [c["hermes_task_id"] for c in W7_NOVO["children"]]


def _append_itens(caminho: pathlib.Path, itens: list[dict], escopo: str) -> tuple[dict, int]:
    base = json.loads(caminho.read_text(encoding="utf-8"))
    novo = json.loads(json.dumps(base))
    existentes = {it.get("id") for it in novo.get("work_items") or []}
    ja = [it["id"] for it in itens if it["id"] in existentes]
    if ja:
        raise SystemExit(f"FAIL-CLOSED: item ja' presente em {caminho.name}: {ja}")
    for it in itens:
        novo.setdefault("work_items", []).append(it)
    novo["updated_at"] = DATA
    novo["production_promotion_authorized"] = False
    ev = _evento(escopo, [c["hermes_task_id"] for it in itens for c in it["children"]])
    novo.setdefault("events", []).append(ev)
    return novo, len(itens)


def _validar_leitor(ids: list[str]) -> None:
    """Aceite da gravacao: o leitor do dashboard classifica os cards como evidenciados."""
    if not PLUGIN_API.is_file():
        print(f"  AVISO: leitor ausente ({PLUGIN_API}) — aceite da gravacao nao verificado")
        return
    spec = importlib.util.spec_from_file_location("papi", str(PLUGIN_API))
    if spec is None or spec.loader is None:
        print(f"  AVISO: leitor nao carregou ({PLUGIN_API}) — aceite da gravacao nao verificado")
        return
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    b = "transformativa-revenue-engine"
    col = m._delivery_lifecycle_task_columns(board=b)
    exp = m._delivery_explicit_done_task_ids(board=b)
    retidos = [i for i in ids if i in (col or {})]
    print(f"  leitor: {len(ids)} cards; ainda na coluna de validacao: {retidos or 'nenhum'}")
    faltam = [i for i in ids if i not in (exp or set())]
    if faltam:
        raise SystemExit(f"FAIL-CLOSED: leitor NAO marcou como DONE explicito: {faltam}")
    if retidos:
        raise SystemExit(f"FAIL-CLOSED: leitor ainda retem na coluna de validacao: {retidos}")
    print("  leitor: todos classificados como DONE explicito (saem da coluna de validacao)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve (com backup datado)")
    args = p.parse_args()

    board = ler_board()
    faltando = [i for i in IDS if board.get(i) != "done"]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")

    print("=== REGISTRO DOS VEREDITOS — LOTE 8 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")

    w3, ajustes = consertar_w3(json.loads(ARTEFATO_W3.read_text(encoding="utf-8")))
    w0, n0 = _append_itens(ARTEFATO_W0, W0_NOVOS, "lote 8 — 5 cards de W0 (JEV: T12/defeitos D01-D05)")
    w7, n7 = _append_itens(ARTEFATO_W7, [W7_NOVO], "lote 8 — W7-E01 (Website lead capture)")

    print(f"(A) W3: {len(ajustes)} itens normalizados:")
    for iid, tid, antes, depois in ajustes:
        print(f"    {iid} {tid}: item=({antes[0]},{antes[1]}) child.stage={antes[2]} -> {depois}")
    print(f"(B) W0: +{n0} itens | W7: +{n7} item | prod_autorizada=False")
    for it in W0_NOVOS:
        print(f"    W0 {it['id']:<20} {it['children'][0]['hermes_task_id']} -> PASS")
    print(f"    W7 {W7_NOVO['id']:<20} {W7_NOVO['children'][0]['hermes_task_id']} -> PASS")

    if not args.aplicar:
        print("(dry-run: rode com --aplicar para escrever)")
        return 0

    gravar(ARTEFATO_W3, w3, True)
    gravar(ARTEFATO_W0, w0, True)
    gravar(ARTEFATO_W7, w7, True)
    print("GRAVADO")
    _validar_leitor(IDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
