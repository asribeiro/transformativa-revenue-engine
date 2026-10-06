#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra a VALIDACAO INTEGRADA (lote 9).

Fecha os PROXIMOS 6 cards de maior risco da fila de triagem (ordens 89..94 da
fila /opt/data/cache/scratch/triagem_155) — TODOS W0/CONTRATO-API, do nucleo JEV:

  1. TRE-W0-E04-T02-D08  t_4200e054  guardrail de DDL acionou sem DDL (recibo mentia)
  2. TRE-W0-E04-T02      t_4f20bd10  Integrar JEV ao Hermes Dev Harness (card-mae da suite do roteador)
  3. TRE-W0-E04-T02-D01  t_56b622e7  limiar em prosa (0,65) hard-coded no roteador
  4. TRE-W0-E04-T05      t_6d326367  Ligar o roteador JEV ao dispatch do board (encaixe/gate)
  5. TRE-W0-E04-T02-D04  t_77c2e407  casamento por texto das 8 acoes proibidas nao pegava a propria politica
  6. TRE-W0-E04-T02-D07  t_83242193  inferencia de dominio por sinonimo deixou passar 4 acoes sensiveis

Rito (skill validacao-de-entregas / precedentes lote1..lote8):
1. dry-run por padrao; `--aplicar` escreve. backup datado antes de cada arquivo.
2. NUNCA autoriza producao (`production_promotion_authorized` segue false).
3. fail-closed: recusa se algum card declarado nao estiver `done` no board.

DUAS DIFERENCAS em relacao ao lote 8 (aprendizado de campo):
(A) D01/D04/D07/D08 sao cards NOVOS no artefato -> append. Mas T02 (t_4f20bd10) e
    T05 (t_6d326367) JA existem como itens no W0-governanca-e-baseline.json, com
    `stage: DONE` e SEM veredito -> aqui a gravacao e READ-MODIFY-WRITE do item
    existente (preenche o veredito no child e normaliza item+child para DONE). O
    item T05 tem DOIS children (t_6d326367 e t_38cbab9a) — o segundo e' PRESERVADO
    intacto.
(B) ANTES de gravar roda uma AUDITORIA de campos em TODOS os artefatos vigentes:
    nenhum child com `validation_result` pode ter `stage != 'DONE'` (nem item com
    veredito em stage misto). Se houver misto, ABORTA (fail-closed) — no lote 8
    havia 4 na W3, ja normalizados; hoje a auditoria deve fechar ZERO.

Contrato do veredito (leitor do dashboard): `work_items[].children[]` =
{hermes_task_id, stage:'DONE', current_gate:'DONE' quando PASS, validation_result,
evidence, validated_at, validated_by, eixo_de_risco,
verification:{commit, ambiente, passes_independentes:'2',
portoes:[{gate, exit, resultado}]}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote9.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote9.py --aplicar
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
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")
PLUGIN_API = pathlib.Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")

DATA = "2026-10-04"
VALIDADOR = "Hermes — validacao integrada (lote 9 — 6 cards de W0/JEV da fila)"
COMMIT = "793d7379023c58c3abf927162f486f65d5f37aef"  # origin/develop (arvore sob teste)

# --- ambiente compartilhado (suites offline; gate em overlay temporario) ----- #
AMB_W0 = (
    "clone limpo (scratch) do repo do TRE em origin/develop = commit 793d7379 (arvore "
    "sob teste), python /opt/hermes/.venv. Verificacao OFFLINE/ESTATICA, sem banco de "
    "producao, rede, credencial real, runtime de producao nem promocao: as suites leem o "
    "proprio repo. A suite do gate (T05) monta um overlay TEMPORARIO de hermes_cli/, roda "
    "o kernel REAL com um binario `hermes` FALSO e um board TEMPORARIO (HERMES_KANBAN_HOME "
    "em temp) — nada escrito em /opt/hermes nem no board real; o adaptador instalado e' "
    "apenas LIDO (item S10). DUAS passadas com exit 0 em TODAS as suites. Saida crua "
    "byte-identica nas suites de guardrails (74 itens) e dominios (32 itens); as suites do "
    "roteador e do gate emitem caminhos/ids volateis (jev-suite-<rand> e dec-<hex>; "
    "gate-jev-<rand>) — normalizacao DECLARADA: <rand> -> TMP e dec-<hex> -> ID; apos "
    "normalizar, diff cru = 0 linhas. Dente por mutacao em COPIA do roteador/politica em "
    "diretorio temporario (os arquivos versionados nunca sao alterados). Os executores do "
    "dev e o board real NAO foram tocados; nenhum container/volume foi criado."
)

# --------------------------------------------------------------------------- #
# Portoes / evidencia por card
# --------------------------------------------------------------------------- #

# --- 1. TRE-W0-E04-T02-D08 (guardrail de DDL acionou sem DDL) --------------- #
PORT_D08 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK; item 'D08: acao sem DDL "
                  "nao carrega motivo nem guardrail de DDL (o recibo nao mente)' OK [5 acoes sem "
                  "DDL: guardrail avaliado, nao acionado, nenhum motivo de DDL, mesmo com ambiente "
                  "producao declarado]; item 'D08: DDL/migration real em ambiente errado continua "
                  "bloqueando, com o motivo dizendo a causa' OK"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada de jev-suite-<rand>/dec-<hex> "
                  "(diff cru = 0); PASS (63 itens, 0 falhas) + autoteste 21/21"},
    {"gate": "python3 scripts/validar_jev_guardrails.py --autoteste (passada 1, corroboracao)", "exit": 0,
     "resultado": "RESULTADO: PASS (74 itens, 0 falhas) + autoteste OK (20/20); item 'guardrail DDL "
                  "— D08: so aciona com DDL/migration REAL e o motivo diz a causa real (nenhum "
                  "motivo de DDL em acao sem DDL)' OK"},
    {"gate": "dente INTERNO (router): mutacao 'D08: guardrail de DDL volta a acionar sem DDL real "
             "no texto'", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado: 'D08: acao sem DDL nao carrega motivo nem "
                  "guardrail de DDL (o recibo nao mente)' (22 itens reprovados no total)"},
    {"gate": "dente INTERNO (guardrails): mutacao 'D08: motivo do guardrail de DDL deixa de dizer "
             "a causa real'", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'guardrail DDL — D08: so aciona com DDL/migration "
                  "REAL e o motivo diz a causa real' (1 item); a mutacao 'guardrail DDL removido' "
                  "reprova 2 itens (inclusive o nomeado) — o dente morde"},
]
EVID_D08 = (
    "Reexecucao real das suites do JEV no develop 793d737: o item D08 PASSA — acao SEM DDL nenhum "
    "(inclusive 'publicar o post do LinkedIn', a acao EXATA do defeito) NAO aciona o guardrail "
    "ddl_fora_de_producao, NAO carrega motivo de DDL no recibo e o guardrail e' registrado como "
    "avaliado/nao-acionado mesmo quando o ambiente 'producao' e' declarado; e DDL/migration REAL "
    "(declarada ou comando SQL como 'rodar CREATE TABLE...') em ambiente errado CONTINUA bloqueando, "
    "com o motivo dizendo a causa real (operacao reconhecida + ambiente alvo). Corrige o defeito em "
    "que o guardrail de DDL disparava por casamento de prosa com entradas de PostgreSQL e gravava no "
    "recibo um motivo de DDL falso. Dente: restaurando o gatilho sem DDL real em COPIA do roteador, o "
    "item nomeado reprova."
)

# --- 2. TRE-W0-E04-T02 (Integrar JEV ao Hermes Dev Harness) ----------------- #
PORT_T02 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK (21/21 mutacoes detectadas); "
                  "cobre os 6 criterios homologados (versao de politica, lane no recibo, abstencao por "
                  "limiar, as 8 acoes nunca_decidido_por_maquina, recibo de 13 campos, modo degradado) "
                  "+ a matriz Dev x Sales e a ausencia de limiar literal no codigo"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada (diff cru = 0); PASS (63 itens, "
                  "0 falhas) + autoteste 21/21"},
    {"gate": "dente INTERNO: cada uma das 21 mutacoes da suite (autoteste)", "exit": 0,
     "resultado": "21/21 mutacoes REPROVADAS pela suite (cada mutacao nomeia o item que derruba, ex.: "
                  "'politica de papel dando deploy ao Sales AI' reprova 'T02/harness: politica de papel "
                  "que da deploy ao Sales AI e recusada'; 'limiar hard-coded' reprova 'roteador sem "
                  "limiar literal no codigo') — o autoteste morde, nao e' decorativo"},
]
EVID_T02 = (
    "Reexecucao real da suite do roteador do JEV (card TRE-W0-E04-T02, 'Integrar JEV ao Hermes Dev "
    "Harness') no develop 793d737: 63 itens, 0 falhas. Prova, item por item, os criterios homologados "
    "pelo dono em 29/09/2026: o roteador le hermes/jev/policy_v1.yaml e recusa versao desconhecida; a "
    "lane bate com a politica e vai no recibo; confianca abaixo do limiar abstem e escala (nunca "
    "executa); as 8 acoes nunca_decidido_por_maquina nunca passam; o recibo grava os 13 campos sem "
    "segredo; politica ausente/ilegivel entra em modo degradado (lane conservadora), nunca execucao "
    "silenciosa; a matriz Dev x Sales e' respeitada (Sales AI nunca recebe credencial de deploy). Dente: "
    "autoteste 21/21 — cada mutacao em COPIA do roteador/politica derruba os itens nomeados."
)

# --- 3. TRE-W0-E04-T02-D01 (limiar em prosa 0,65) --------------------------- #
PORT_D01 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK; item 'criterio 2: o limiar "
                  "usado vem do YAML (prova comportamental)' OK e item 'roteador sem limiar literal no "
                  "codigo' OK [prova comportamental: trocar o limiar no YAML muda a decisao]"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada (diff cru = 0); PASS (63 itens, "
                  "0 falhas) + autoteste 21/21"},
    {"gate": "dente INTERNO: mutacao 'limiar hard-coded no roteador (ignora o YAML)'", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'criterio 2: o limiar usado vem do YAML (prova "
                  "comportamental)' e tambem 'roteador sem limiar literal no codigo' (5 itens "
                  "reprovados no total) — o dente morde exatamente o limiar"},
]
EVID_D01 = (
    "Reexecucao real da suite do roteador no develop 793d737: o item 'o limiar usado vem do YAML "
    "(prova comportamental)' PASSA — o roteador NAO carrega limiar literal (0,65) em prosa no codigo; "
    "o limiar e' lido de hermes/jev/policy_v1.yaml e trocar o limiar no YAML muda a decisao. Corrige o "
    "defeito em que o limiar 0,65 aparecia escrito em prosa dentro do roteador e reprovava o criterio "
    "de 'limiar nao-hardcoded'. Dente: com 'aceitar = 0.85' mutado em COPIA do roteador, o item nomeado "
    "reprova — a suite morde a regressao do limiar literal."
)

# --- 4. TRE-W0-E04-T05 (Ligar o roteador JEV ao dispatch do board) ---------- #
PORT_T05 = [
    {"gate": "python3 scripts/verificar_gate_jev.py (passada 1)", "exit": 0,
     "resultado": "PASS (30 itens, 0 falhas): S1..S9 (dispatch/claim com encaixe, recibo de 13 campos "
                  "em toda consulta, codigo proibido bloqueia+exige aprovacao, fail-closed por comando "
                  "ausente/saida fora do contrato/timeout, escopo por board, adaptador instalado == "
                  "versionado)"},
    {"gate": "python3 scripts/verificar_gate_jev.py (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada de gate-jev-<rand> (diff cru = 0); "
                  "PASS (30 itens, 0 falhas)"},
    {"gate": "dente INTERNO (prova negativa S5/S6 da propria suite)", "exit": 0,
     "resultado": "S5: sem o encaixe (HERMES_JEV_GATE=off) o MESMO card EXECUTA e nenhum evento do gate "
                  "e' gravado; S6: encaixe ligado + kernel SEM a edicao => o card EXECUTA (o codigo do "
                  "ponto de estrangulamento e' o que segura); S6b/S6c: revertendo SO a edicao de "
                  "claim_task OU SO a do despachante, o caminho respectivo reabre/segura — cada metade "
                  "vale sozinha"},
]
EVID_T05 = (
    "Reexecucao real da suite do ENCAIXE do gate JEV no dispatch (card TRE-W0-E04-T05) no develop "
    "793d737: 30 itens, 0 falhas. O encaixe roda ANTES de reivindicar/despachar e o card retido pelo "
    "roteador NAO executa por caminho nenhum (nem o tick do despachante com spawn — usa binario "
    "`hermes` FALSO, sem nascer worker de verdade — nem o `hermes kanban claim` manual); card com "
    "codigo canonico declarado executa; codigo proibido bloqueia e marca exige_aprovacao_humana; "
    "recibo de 13 campos gravado em toda consulta; gate quebrado (comando ausente/saida fora do "
    "contrato/timeout) NAO executa (fail-closed). Roda em overlay TEMPORARIO com board TEMPORARIO: "
    "nada escrito em /opt/hermes nem no board real. Dente: provas negativas S5/S6 e mutacoes S6b/S6c "
    "embutidas — removendo o encaixe ou a edicao do kernel, o card volta a executar, provando que o "
    "codigo do ponto de estrangulamento (nao a configuracao) e' o que segura."
)

# --- 5. TRE-W0-E04-T02-D04 (casamento por texto das 8 acoes) ---------------- #
PORT_D04 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK; item 'D04: as 4 frases em prosa "
                  "que escapavam do bloqueio terminam em BLOCK' OK [as 4 frases terminam em BLOCK]; "
                  "item 'D04: o vocabulario canonico do roteador nao inventa nem omite acao da "
                  "politica' OK [8 regras canonicas alinhadas com 8 acoes de "
                  "nunca_decidido_por_maquina]"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada (diff cru = 0); PASS (63 itens, "
                  "0 falhas) + autoteste 21/21"},
    {"gate": "dente DIRECIONADO: renomeia a regra canonica 'publicacao_em_nome_da_transformativa' em "
             "COPIA do roteador (desalinha o vocabulario x politica)", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'D04: o vocabulario canonico do roteador nao inventa "
                  "nem omite acao da politica' [codigo do roteador fora da politica; acao da politica "
                  "sem regra canonica no roteador] (4 itens reprovados no total) — o dente morde o "
                  "vocabulario que o D04 introduziu"},
    {"gate": "dente INTERNO (corroboracao): mutacao 'inverter a precedencia: sem Human Approval'", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'D04: as 4 frases em prosa que escapavam do bloqueio "
                  "terminam em BLOCK' (11 itens reprovados no total) — o 2o item do D04 tambem morde"},
]
EVID_D04 = (
    "Reexecucao real da suite do roteador no develop 793d737: os dois itens do D04 PASSAM — as 4 frases "
    "em prosa que escapavam do bloqueio ('promocao de release para producao', 'promover release para "
    "producao', 'publicar release em producao', 'exclusao de registro de auditoria') agora terminam em "
    "BLOCK; e o vocabulario canonico do roteador casa 1:1 com as 8 acoes de nunca_decidido_por_maquina "
    "da politica (nao inventa nem omite). Corrige o defeito em que o casamento por texto das 8 acoes "
    "proibidas nao pegava o texto da propria politica. Dente: renomeando UMA regra canonica em COPIA do "
    "roteador, o item de alinhamento do vocabulario reprova; e removendo a precedencia humana, o item "
    "das 4 frases reprova."
)

# --- 6. TRE-W0-E04-T02-D07 (inferencia de dominio por sinonimo) ------------- #
PORT_D07 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK; itens 'D07: as 4 acoes sensiveis "
                  "que escapavam nao executam — escalam por falta de codigo canonico, nao por "
                  "vocabulario' OK, 'D07: texto livre comum, sem dominio sensivel, tambem nao executa' "
                  "OK e 'D07: codigo canonico comum com texto limpo segue executando' OK"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA apos normalizacao declarada (diff cru = 0); PASS (63 itens, "
                  "0 falhas) + autoteste 21/21"},
    {"gate": "python3 scripts/verificar_dominios_sensiveis.py --autoteste (passada 1, corroboracao)", "exit": 0,
     "resultado": "RESULTADO: PASS (32 itens, 0 falha(s)) + AUTOTESTE 4/4 mutacoes reprovadas; item "
                  "'regressao: casamento exato nao aceita prefixo' e 'as acoes humanas continuam "
                  "resolvendo' OK"},
    {"gate": "dente INTERNO (router): mutacao 'D07: texto livre sem codigo volta a executar (falha "
             "fechada volta a exigir dominio sensivel)'", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'D07: as 4 acoes sensiveis que escapavam nao "
                  "executam — escalam por falta de codigo canonico, nao por vocabulario' (6 itens "
                  "reprovados no total); a mutacao 'T07: falha fechada da acao nao classificada "
                  "removida' tambem o reprova"},
    {"gate": "dente INTERNO (guardrails/dominios): mutacao 'D07: texto livre sem codigo volta a "
             "executar' e 'tira os PLURAIS do vocabulario'", "exit": 0,
     "resultado": "guardrails: reprova 'D07 — texto livre sem codigo canonico NAO executa (as 4 que "
                  "escapavam + texto comum), escalando com o motivo do codigo faltante' (1 item); "
                  "dominios: reprova os 4/4 mutacoes, incl. 'tira os PLURAIS do vocabulario (achado da "
                  "revisao T12)'"},
]
EVID_D07 = (
    "Reexecucao real das suites do JEV no develop 793d737: o D07 PASSA — as 4 acoes SENSIVEIS escritas "
    "com sinonimo nao previsto ('conceder permissao de administrador', 'restaurar um backup por cima "
    "da base', 'soltar nota no site da Transformativa', 'limpar a base de contatos antigos') NAO "
    "EXECUTAM: escalam (ESCALATE, lane conservadora, recibo de 13 campos) com o motivo dizendo que "
    "FALTOU O CODIGO CANONICO da acao — nao por vocabulario; texto livre comum sem dominio sensivel "
    "tambem nao executa (a regra nao depende de parecer sensivel); e codigo canonico comum com texto "
    "limpo segue executando (a regra nao virou bloqueio geral). Corrige o defeito em que a inferencia "
    "de dominio por sinonimo deixava passar 4 acoes sensiveis (PASS/exit 0 com as suites verdes). "
    "Dente: restaurando a peneira por vocabulario em COPIA do roteador, o item nomeado reprova; a suite "
    "de dominios reprova as 4/4 mutacoes."
)

# --------------------------------------------------------------------------- #
# Itens NOVOS (append) — D01, D04, D07, D08
# --------------------------------------------------------------------------- #


def _child(tid, evidence, portoes, eixo="CONTRATO/API"):
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
            "commit": COMMIT,
            "ambiente": AMB_W0,
            "passes_independentes": "2",
            "portoes": portoes,
        },
    }


def _item(idx, titulo, tid, evidence, portoes):
    return {
        "id": idx, "title": titulo, "stage": "DONE", "current_gate": "DONE",
        "children": [_child(tid, evidence, portoes)],
    }


NOVOS = [
    _item("TRE-W0-E04-T02-D08",
          "DEFEITO guardrail de DDL acionou sem DDL e gravou motivo falso no recibo",
          "t_4200e054", EVID_D08, PORT_D08),
    _item("TRE-W0-E04-T02-D01",
          "DEFEITO [retroativo] limiar em prosa (0,65) dentro do roteador",
          "t_56b622e7", EVID_D01, PORT_D01),
    _item("TRE-W0-E04-T02-D04",
          "DEFEITO casamento por texto das 8 acoes proibidas nao pegava o texto da propria politica",
          "t_77c2e407", EVID_D04, PORT_D04),
    _item("TRE-W0-E04-T02-D07",
          "DEFEITO inferencia de dominio por sinonimo deixou passar 4 acoes sensiveis (PASS/exit 0)",
          "t_83242193", EVID_D07, PORT_D07),
]

# Itens JA EXISTENTES (read-modify-write) — T02 e T05
EXISTENTES = {
    "TRE-W0-E04-T02": {
        "child": "t_4f20bd10",
        "evidence": EVID_T02,
        "portoes": PORT_T02,
    },
    "TRE-W0-E04-T05": {
        "child": "t_6d326367",
        "evidence": EVID_T05,
        "portoes": PORT_T05,
    },
}

IDS = [c["hermes_task_id"] for it in NOVOS for c in it["children"]] + \
      [v["child"] for v in EXISTENTES.values()]


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


def _evento(cards: list[str]) -> dict:
    return {
        "event": "INTEGRATED_VALIDATION_RECORDED",
        "by": VALIDADOR,
        "scope": "lote 9 — 6 cards de W0/JEV (T02 e D05-mae + defeitos D01/D04/D07/D08)",
        "production_promotion_authorized": False,
        "at": agora(),
        "cards": cards,
    }


def _artefatos_vigentes() -> list[pathlib.Path]:
    marcadores = (".bak-", ".bak.", ".orig", ".old", "~", ".tmp", ".swp", ".save")
    por_id: dict[str, tuple[str, pathlib.Path]] = {}
    sem_id: list[pathlib.Path] = []
    for f in sorted(DELIVERIES.glob("*.json")):
        if any(m in f.name.lower() for m in marcadores):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        did = str(d.get("delivery_id") or "").strip()
        if not did:
            sem_id.append(f)
            continue
        atual = str(d.get("updated_at") or "")
        if did not in por_id or atual > por_id[did][0]:
            por_id[did] = (atual, f)
    return sorted([p for _, p in por_id.values()] + sem_id)


def auditar_campos() -> list[tuple]:
    """Nenhum child com validation_result pode ter stage != 'DONE'; idem item misto."""
    mistos = []
    for p in _artefatos_vigentes():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in d.get("work_items") or []:
            for ch in it.get("children") or []:
                if ch.get("validation_result") and str(ch.get("stage") or "").upper() != "DONE":
                    mistos.append((p.name, it.get("id"), ch.get("hermes_task_id"),
                                   ch.get("stage"), ch.get("current_gate"),
                                   ch.get("validation_result")))
            if it.get("validation_result") and str(it.get("stage") or "").upper() != "DONE":
                mistos.append((p.name, it.get("id"), "<item>", it.get("stage"),
                               it.get("current_gate"), it.get("validation_result")))
    return mistos


def _append_itens(base: dict, itens: list[dict]) -> tuple[dict, int]:
    novo = json.loads(json.dumps(base))
    existentes = {it.get("id") for it in novo.get("work_items") or []}
    ja = [it["id"] for it in itens if it["id"] in existentes]
    if ja:
        raise SystemExit(f"FAIL-CLOSED: item ja' presente: {ja}")
    for it in itens:
        novo.setdefault("work_items", []).append(it)
    return novo, len(itens)


def _atualizar_item(base: dict, item_id: str, spec: dict) -> tuple[dict, tuple]:
    """READ-MODIFY-WRITE: preenche o veredito no child alvo e normaliza item+child
    para DONE, PRESERVANDO os demais children. Fail-closed se o item/child faltar."""
    novo = json.loads(json.dumps(base))
    alvo = None
    for it in novo.get("work_items") or []:
        if it.get("id") == item_id:
            alvo = it
            break
    if alvo is None:
        raise SystemExit(f"FAIL-CLOSED: item ausente para RMW: {item_id}")
    child = None
    for ch in alvo.get("children") or []:
        if ch.get("hermes_task_id") == spec["child"]:
            child = ch
            break
    if child is None:
        raise SystemExit(f"FAIL-CLOSED: child ausente em {item_id}: {spec['child']}")
    antes = (alvo.get("stage"), alvo.get("current_gate"), child.get("stage"),
             child.get("current_gate"), child.get("validation_result"))
    veredito = _child(spec["child"], spec["evidence"], spec["portoes"])
    # preserva qualquer campo pre-existente do child que o veredito nao define
    for k, v in child.items():
        veredito.setdefault(k, v)
    child.clear()
    child.update(veredito)
    alvo["stage"] = "DONE"
    alvo["current_gate"] = "DONE"
    return novo, (item_id, spec["child"], antes, ("DONE", "DONE", "DONE", "DONE", "PASS"))


def _validar_leitor(ids: list[str]) -> None:
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

    print("=== REGISTRO DOS VEREDITOS — LOTE 9 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")
    print(f"commit sob teste: {COMMIT}")

    # AUDITORIA DE CAMPOS (fail-closed): nenhum misto pode sobrar.
    mistos = auditar_campos()
    print(f"(AUDITORIA) artefatos vigentes com child/item veredito em stage != DONE: {len(mistos)}")
    for m in mistos:
        print(f"    MISTO: {m}")
    if mistos and args.aplicar:
        raise SystemExit("FAIL-CLOSED: ha campos mistos; normalizar antes de gravar")

    base = json.loads(ARTEFATO_W0.read_text(encoding="utf-8"))
    w0, n = _append_itens(base, NOVOS)
    ajustes = []
    for item_id, spec in EXISTENTES.items():
        w0, ajuste = _atualizar_item(w0, item_id, spec)
        ajustes.append(ajuste)

    print(f"(B) W0: +{n} itens novos | {len(ajustes)} itens pre-existentes por RMW")
    for it in NOVOS:
        print(f"    novo {it['id']:<22} {it['children'][0]['hermes_task_id']} -> PASS")
    for item_id, tid, antes, depois in ajustes:
        print(f"    rmw  {item_id:<22} {tid}: "
              f"item=({antes[0]},{antes[1]}) child=({antes[2]},{antes[3]},vr={antes[4]}) -> {depois}")

    w0["updated_at"] = DATA
    w0["production_promotion_authorized"] = False
    w0.setdefault("events", []).append(_evento(IDS))

    if not args.aplicar:
        print("(dry-run: rode com --aplicar para escrever)")
        return 0

    gravar(ARTEFATO_W0, w0, True)
    print("GRAVADO")
    _validar_leitor(IDS)

    # Reaudita depois de gravar.
    pos = auditar_campos()
    print(f"(AUDITORIA pos-gravacao) mistos: {len(pos)}")
    if pos:
        raise SystemExit(f"FAIL-CLOSED: mistos apos gravar: {pos}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
