#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra a VALIDACAO INTEGRADA (lote 10).

Fecha os PROXIMOS 6 cards de maior risco da fila de triagem (proximas ordens a
partir de 95; a ordem 95 / t_83242193 ja' foi fechada no lote 9) — ordens 96..101,
TODOS W0/CONTRATO-API, do nucleo JEV + contrato de dados + papeis:

  1. TRE-W0-E04-T01-D02  t_aac6215b  verificador checava a precedencia por palavras soltas (nao pela cadeia)
  2. TRE-W0-E04-T04-D01  t_ae66f348  fixture de segredo em literal bloqueava o commit pelo scanner
  3. TRE-W0-E03-D01      t_b1e3bdf9  .gitignore engolia docs/data/ e o contrato nao estava versionado
  4. TRE-W0-E02-T01      t_8c332df7  Separar Hermes Dev Harness e Sales AI
  5. TRE-W0-E04-T04      t_b3387f25  Validar JEV guardrails e fallback
  6. TRE-W0-E04-T09      t_b4b11995  Aposentar o classificador de lane (medicao que sustenta)

Rito (skill validacao-de-entregas / precedentes lote1..lote9):
1. dry-run por padrao; `--aplicar` escreve. backup datado antes de cada arquivo.
2. NUNCA autoriza producao (`production_promotion_authorized` segue false).
3. fail-closed: recusa se algum card declarado nao estiver `done` no board.

DIFERENCAS em relacao ao lote 9:
(A) 3 cards NOVOS (D02 do T01, D01 do T04, D01 do E03) entram por APPEND no
    W0-governanca-e-baseline.json. 3 cards JA existem como itens, com `stage: DONE`
    e SEM veredito (E02-T01, E04-T04, E04-T09) — aqui a gravacao e READ-MODIFY-WRITE
    do item existente (preenche o veredito no child e normaliza item+child para DONE),
    preservando os demais campos/children.
(B) AUDITORIA de campos em TODOS os artefatos vigentes antes de gravar: nenhum child
    com `validation_result` pode ter `stage != 'DONE'` (nem item com veredito em stage
    misto). Se houver misto, ABORTA (fail-closed). Hoje a auditoria deve fechar ZERO.

Contrato do veredito (leitor do dashboard): `work_items[].children[]` =
{hermes_task_id, stage:'DONE', current_gate:'DONE' quando PASS, validation_result,
evidence, validated_at, validated_by, eixo_de_risco,
verification:{commit, ambiente, passes_independentes:'2',
portoes:[{gate, exit, resultado}]}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote10.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote10.py --aplicar
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
VALIDADOR = "Hermes — validacao integrada (lote 10 — 6 cards de W0 da fila: JEV + contrato + papeis)"
COMMIT = "4aa00c2f0a42b106658a23a0aec9df4eab1d5eab"  # origin/develop (arvore sob teste)

# --- ambiente compartilhado (suites offline/estaticas; sem banco/rede) -------- #
AMB_W0 = (
    "clone limpo (scratch) do repo do TRE em origin/develop = commit 4aa00c2 (arvore sob teste), "
    "python /opt/hermes/.venv. Verificacao OFFLINE/ESTATICA, sem banco de producao, rede, credencial "
    "real, runtime de producao nem promocao: as suites leem o proprio repo (o secret_scan le o indice "
    "do git; a estrutura le o git ls-files). DUAS passadas com exit 0 em TODAS as suites e saida crua "
    "BYTE-IDENTICA (diff cru = 0) — NENHUMA normalizacao foi necessaria: estas suites nao emitem "
    "caminhos/ids volateis. Dente por mutacao em DUAS frentes: (a) o autoteste EMBUTIDO das suites que "
    "tem um (politica 12/12, guardrails 20/20, policy v1.2 8/8, policy v1.3 8/8) e (b) mutacoes EXTERNAS "
    "aplicadas em CLONE ISOLADO do repo (git clone --local, HARD-LINK, em temp) para as 3 suites SEM "
    "autoteste — papeis (concede GITHUB_TOKEN ao sales-ai), secret_scan (planta literal que casa o "
    "padrao) e estrutura (desversiona docs/data/DATA_CONTRACT_V1.md): os arquivos VERSIONADOS nunca foram "
    "alterados. O board real e /opt/hermes NAO foram tocados; nenhum container/volume foi criado "
    "(VPS: 5 containers / 7 volumes / 0 dangling ANTES e DEPOIS)."
)

# --------------------------------------------------------------------------- #
# Portoes / evidencia por card
# --------------------------------------------------------------------------- #

# --- 1. TRE-W0-E04-T01-D02 (precedencia por palavras soltas) ---------------- #
PORT_D02 = [
    {"gate": "python3 scripts/verificar_jev_policy.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (42 itens, 0 falhas) + autoteste OK; item 'documento cita a cadeia de "
                  "precedencia na ordem' OK [a cadeia completa COM AS SETAS existe no documento]"},
    {"gate": "python3 scripts/verificar_jev_policy.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (42 itens, 0 falhas) + autoteste OK (12/12)"},
    {"gate": "dente EXTERNO (clone isolado): remove a cadeia com setas do documento da v1 (deixa so' as "
             "palavras soltas, o defeito D02)", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'documento cita a cadeia de precedencia na ordem' "
                  "[cadeia completa (com as setas) nao encontrada no documento] + RESULTADO: FALHOU "
                  "(42 itens, 1 falha), exit 1 — o dente morde exatamente a classe do defeito"},
    {"gate": "dente INTERNO (autoteste): mutacao 'documento sem a cadeia de precedencia'", "exit": 0,
     "resultado": "detectada — reprova 1 item (o item nomeado); o autoteste roda sozinho no comando padrao"},
]
EVID_D02 = (
    "Reexecucao real do verificador da JEV Decision Policy V1 no develop 4aa00c2: o item 'documento cita a "
    "cadeia de precedencia na ordem' PASSA — o documento da politica declara a cadeia completa com as setas "
    "('Security → Human Approval → prioridade/dependencias → JEV → LLM'), nao apenas as palavras soltas em "
    "qualquer ordem. Corrige o defeito [retroativo] em que o verificador checava a precedencia por palavras "
    "soltas (achava 'Security', 'Human Approval', ... em qualquer lugar do texto) e dava verde a um documento "
    "com a ordem trocada: agora exige a CADEIA na ordem. Dente: removida a cadeia com setas em COPIA do "
    "documento (clone isolado), o item nomeado reprova; o autoteste embutido (12/12) ja' cobre essa mutacao."
)

# --- 2. TRE-W0-E04-T04-D01 (fixture de segredo em literal) ------------------ #
PORT_D01_T04 = [
    {"gate": "bash scripts/secret_scan.sh (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (nenhum segredo versionado) — nenhum literal que case os PADROES do "
                  "scanner esta versionado; o commit nao e' mais bloqueado"},
    {"gate": "bash scripts/secret_scan.sh (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (nenhum segredo versionado)"},
    {"gate": "dente EXTERNO (clone isolado): planta um literal que casa o scanner (sk-<28 chars>) em "
             "arquivo rastreado", "exit": 0,
     "resultado": "a mutacao REPROVA: 'RESULTADO: FALHOU (segredo encontrado)', exit 1 — o dente morde "
                  "(o scanner distingue fixture inocua de segredo literal)"},
    {"gate": "corroboracao: python3 scripts/validar_jev_guardrails.py --autoteste --estrito (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (74 itens, 0 falhas) + autoteste OK; item 'guardrail segredo — REPROVA: "
                  "6 formas de segredo bloqueiam' OK [a fixture sintetica continua exercitando o guardrail "
                  "sem derrubar o scanner do projeto]"},
]
EVID_D01_T04 = (
    "Reexecucao real do scanner do projeto no develop 4aa00c2: `bash scripts/secret_scan.sh` PASSA (nenhum "
    "segredo versionado) — a fixture do teste do guardrail de segredo deixou de ficar como literal que casa "
    "o PADROES do scanner, entao o commit nao e' mais bloqueado por ela; e a mesma fixture continua "
    "exercitando o guardrail (o item 'guardrail segredo — REPROVA: 6 formas de segredo bloqueiam' segue OK "
    "na suite adversarial). Corrige o defeito [retroativo] em que um literal de segredo no fixture "
    "bloqueava o commit pelo scanner do projeto. Dente: plantado um literal `sk-...` em arquivo rastreado "
    "num CLONE isolado, o scanner reprova ('segredo encontrado', exit 1)."
)

# --- 3. TRE-W0-E03-D01 (.gitignore engolia docs/data/) ---------------------- #
PORT_D01_E03 = [
    {"gate": "bash scripts/verificar_estrutura.sh (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (0 falhas); item 'versionado  docs/data/DATA_CONTRACT_V1.md' OK (o "
                  "contrato existe E esta' no git) — a regra errada do .gitignore nao engole mais docs/data/"},
    {"gate": "bash scripts/verificar_estrutura.sh (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (0 falhas)"},
    {"gate": "python3 scripts/verificar_contrato_dados.py (passada 1, corroboracao do conteudo)", "exit": 0,
     "resultado": "RESULTADO: PASS (26 itens, 0 falhas) — o SQL, o documento e o JSON do contrato nao "
                  "divergem; os tres artefatos estao' versionados e coerentes"},
    {"gate": "dente EXTERNO (clone isolado): desversiona docs/data/DATA_CONTRACT_V1.md (git rm --cached, "
             "arquivo segue no disco)", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'versionado docs/data/DATA_CONTRACT_V1.md (arquivo "
                  "existe mas nao esta no git — ignorado pelo .gitignore?)' + RESULTADO: FALHOU (1), exit 1 "
                  "— o dente morde (prova negativa do bloco versionado)"},
]
EVID_D01_E03 = (
    "Reexecucao real do verificador de estrutura e do verificador do contrato de dados no develop 4aa00c2: "
    "verificar_estrutura.sh PASSA (0 falhas) com 'versionado docs/data/DATA_CONTRACT_V1.md' OK — o Data "
    "Contract V1.0 esta' versionado (git ls-files) e `git check-ignore docs/data/DATA_CONTRACT_V1.md` "
    "retorna exit 1 (nao ignorado); o .gitignore usa `/data/` (com barra inicial), nao `data/`. E "
    "verificar_contrato_dados.py PASSA (26 itens): SQL + doc + JSON coerentes. Corrige o defeito "
    "[retroativo] em que a regra `data/` (sem barra) do .gitignore engolia `docs/data/` e o contrato "
    "ficou sem versionamento. Dente: desversionado o arquivo em CLONE isolado, o verificador reprova o "
    "item nomeado."
)

# --- 4. TRE-W0-E02-T01 (Separar Hermes Dev Harness e Sales AI) -------------- #
PORT_E02 = [
    {"gate": "bash scripts/verificar_papeis.sh (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (0 falhas); 18 itens OK — papeis (dev-harness/sales-ai/human-approval) "
                  "existem e declaram role; sales-ai proibido de deploy/codigo/DDL/workflow n8n; sales-ai "
                  "NAO recebe GITHUB_TOKEN nem chave do n8n; dev-harness recebe GITHUB_TOKEN e NAO recebe "
                  "Titan; Human Approval nos itens de contato/publicacao"},
    {"gate": "bash scripts/verificar_papeis.sh (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (0 falhas)"},
    {"gate": "dente EXTERNO (clone isolado): concede GITHUB_TOKEN ao sales-ai em credenciais_permitidas",
     "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'sales-ai NAO recebe GITHUB_TOKEN' + RESULTADO: FALHOU "
                  "(1), exit 1 — o dente morde a separacao de credenciais que o card garante"},
]
EVID_E02 = (
    "Reexecucao real do verificador de papeis no develop 4aa00c2: PASSA (0 falhas, 18 itens). Prova os "
    "invariantes da separacao Dev Harness x Sales AI: politicas versionadas (dev-harness.yaml, sales-ai.yaml, "
    "human-approval.yaml) com role declarado; Sales AI nao pode deploy/codigo/DDL/workflow n8n e nao recebe "
    "GITHUB_TOKEN nem a chave de publicacao do n8n; Dev Harness recebe GITHUB_TOKEN e nao recebe Titan; os "
    "itens de contato/publicacao exigem Human Approval. Dente: concedendo GITHUB_TOKEN ao sales-ai em CLONE "
    "isolado, o item nomeado 'sales-ai NAO recebe GITHUB_TOKEN' reprova."
)

# --- 5. TRE-W0-E04-T04 (Validar JEV guardrails e fallback) ------------------ #
PORT_E04 = [
    {"gate": "python3 scripts/validar_jev_guardrails.py --autoteste --estrito (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (74 itens, 0 falhas) + autoteste OK (20/20); 0 achados em --estrito — "
                  "os 5 guardrails (segredo, do_not_contact, DDL, papel/credencial, fail-closed) com REPROVA "
                  "+ APROVA, as 8 acoes nunca_decidido_por_maquina, e o fallback/degradado exercitado ponta "
                  "a ponta"},
    {"gate": "python3 scripts/validar_jev_guardrails.py --autoteste --estrito (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (74 itens, 0 falhas) + autoteste OK (20/20)"},
    {"gate": "dente INTERNO (autoteste 20/20): ex. 'guardrail DDL removido', 'D07: texto livre sem codigo "
             "volta a executar', 'D08: guardrail de DDL volta a acionar sem DDL real', 'D08: motivo do "
             "guardrail de DDL deixa de dizer a causa real'", "exit": 0,
     "resultado": "cada mutacao REPROVA o item nomeado: 'guardrail DDL — D08: so aciona com DDL/migration "
                  "REAL e o motivo diz a causa real', 'D07 — texto livre sem codigo canonico NAO executa', "
                  "'guardrail segredo — REPROVA: 6 formas de segredo bloqueiam' etc. — o autoteste morde, "
                  "nao e' decorativo"},
]
EVID_E04 = (
    "Reexecucao real da validacao ADVERSARIAL E INDEPENDENTE dos guardrails e do fallback do JEV no develop "
    "4aa00c2: 74 itens, 0 falhas, 0 achados (--estrito). Prova item por item que cada guardrail do YAML tem "
    "lado REPROVA e lado APROVA, que as 8 acoes de nunca_decidido_por_maquina nunca executam, que o fail-closed "
    "bloqueia (nao libera) em entrada invalida/sinal desconhecido, e que o fallback entra em modo degradado "
    "conservador (lane high, sem execucao) nos 4 gatilhos. Dente: autoteste 20/20 — cada mutacao (remover o "
    "guardrail, reabrir o texto livre do D07, reacionar a DDL sem DDL real, apagar o motivo verdadeiro do D08) "
    "reprova o item nomeado."
)

# --- 6. TRE-W0-E04-T09 (Aposentar o classificador de lane) ------------------ #
PORT_E04_T09 = [
    {"gate": "python3 scripts/verificar_jev_policy_v1_2.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (234 itens, 0 falhas) + autoteste OK (8/8); item 'C4: o caminho de "
                  "decisao NAO chama o classificador — e ele segue alcancavel como linha de base do "
                  "benchmark' OK [chamadas no caminho de decisao=0]"},
    {"gate": "python3 scripts/verificar_jev_policy_v1_2.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (234 itens, 0 falhas) + autoteste OK (8/8)"},
    {"gate": "dente INTERNO (autoteste): 'ROTEADOR: classificador volta a ser chamado no caminho de decisao'",
     "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 'C4: o caminho de decisao NAO chama o classificador' "
                  "(1 item); a mutacao 'o TEXTO do card decide a lane' reprova 3 itens (C1/C2/C4) — o dente "
                  "morde a aposentadoria do classificador"},
    {"gate": "corroboracao: python3 scripts/verificar_jev_policy_v1_3.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (474 itens, 0 falhas) + autoteste OK — a PARTE 3 re-roda a bateria da "
                  "v1.2 inteira sobre a v1.2, reconfirmando C1..C8 (incl. C4) na arvore sob teste"},
]
EVID_E04_T09 = (
    "Reexecucao real do verificador da JEV Decision Policy v1.2 no develop 4aa00c2: 234 itens, 0 falhas. "
    "Prova, por COMPORTAMENTO contra o roteador, que o classificador de card foi aposentado do caminho de "
    "decisao ('C4', chamadas=0) e permanece apenas como linha de base historica do benchmark; que a lane e' "
    "DECLARADA por codigo canonico ('C2', trocar a lane no YAML troca a decisao; nenhuma lane literal no "
    "roteador) e que o TEXTO do card nao decide lane ('C1', inclusive o caso vivo do T09 onde 'producao'/"
    "'credencial' apareciam para isentar); que codigo sem lane declarada ABSTEM ('C3'/'C8'); que o piso por "
    "ambiente segue elevando ('C5'); e que o recibo mantem os 13 campos ('C6'). Corrige o card 'Aposentar o "
    "classificador de lane (decisao do dono, com a medicao que sustenta)'. Dente: autoteste 8/8 — fazendo o "
    "roteador voltar a chamar o classificador OU voltar a decidir a lane pelo texto, os itens C1/C2/C4 reprovam."
)

# --------------------------------------------------------------------------- #
# Itens NOVOS (append) — D02 do T01, D01 do T04, D01 do E03
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
    _item("TRE-W0-E04-T01-D02",
          "DEFEITO [retroativo] verificador checava a precedencia por palavras soltas, nao pela cadeia",
          "t_aac6215b", EVID_D02, PORT_D02),
    _item("TRE-W0-E04-T04-D01",
          "DEFEITO [retroativo] fixture de segredo em literal bloqueava o commit pelo scanner do projeto",
          "t_ae66f348", EVID_D01_T04, PORT_D01_T04),
    _item("TRE-W0-E03-D01",
          "DEFEITO [retroativo] .gitignore engolia docs/data/ e o contrato nao estava versionado",
          "t_b1e3bdf9", EVID_D01_E03, PORT_D01_E03),
]

# Itens JA EXISTENTES (read-modify-write) — E02-T01, E04-T04, E04-T09
EXISTENTES = {
    "TRE-W0-E02-T01": {
        "child": "t_8c332df7",
        "evidence": EVID_E02,
        "portoes": PORT_E02,
    },
    "TRE-W0-E04-T04": {
        "child": "t_b3387f25",
        "evidence": EVID_E04,
        "portoes": PORT_E04,
    },
    "TRE-W0-E04-T09": {
        "child": "t_b4b11995",
        "evidence": EVID_E04_T09,
        "portoes": PORT_E04_T09,
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
        "scope": "lote 10 — 6 cards de W0 da fila (D02 do T01 + D01 do T04 + D01 do E03 + E02-T01 + E04-T04 + E04-T09)",
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
    para DONE, PRESERVANDO os demais children/campos. Fail-closed se faltar."""
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

    print("=== REGISTRO DOS VEREDITOS — LOTE 10 —",
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

    print(f"(A) W0: +{n} itens novos | {len(ajustes)} itens pre-existentes por RMW")
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
