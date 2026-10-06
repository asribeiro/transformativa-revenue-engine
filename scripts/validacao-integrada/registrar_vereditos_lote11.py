#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra a VALIDACAO INTEGRADA (lote 11).

Fecha os PROXIMOS 8 cards de maior risco da fila de triagem (proximas ordens a
partir de 102; a ordem 101 / t_b4b11995 foi fechada no lote 10) — ordens 102..109,
TODOS W0/CONTRATO-API, do nucleo JEV (policy/benchmark/piso/gate/projecao):

  1. TRE-W0-E04-T11    t_b74c2edd  Projecao de entrega: raiz do registro NAO pode ser caminho fixo  -> BLOCKED
  2. TRE-W0-E04-T02-D06 t_d10fda7c DEFEITO canonicalizacao por vocabulario deixou passar 1o contato (D06)
  3. TRE-W0-E04-T08    t_d36c7d0f  Homologar a JEV v1.1 + piso de lane por ambiente no roteador
  4. TRE-W0-E04-T03    t_d8bc83b3  Criar benchmark anotado de routing
  5. TRE-W0-E04-T03-D01 t_dfcfc4d4 LACUNA: 28/32 casos sem codigo canonico de acao (benchmark sem poder)
  6. TRE-W0-E04-T10    t_e09a95bf  Nomear o codigo comum `execucao_de_card` (escopo estreito)
  7. TRE-W0-E04-T06-D01 t_e589867b DEFEITO [retroativo]: numero de card duplicado (dois cards como T07)
  8. TRE-W0-E04-T01    t_e7d9decd  Definir JEV Decision Policy V1

Rito (skill validacao-de-entregas / precedentes lote1..lote10):
1. dry-run por padrao; `--aplicar` escreve. backup datado antes de cada arquivo.
2. NUNCA autoriza producao (`production_promotion_authorized` segue false).
3. fail-closed: recusa se algum card declarado nao estiver `done` no board.

DIFERENAS em relacao ao lote 10:
(A) 5 cards NOVOS entram por APPEND no W0-governanca-e-baseline.json: E04-T11
    (BLOCKED por ambiente/verificador — NAO entra como DONE), E04-T02-D06,
    E04-T03-D01, E04-T10 e E04-T06-D01.
(B) 3 cards JA EXISTEM como itens (E04-T01, E04-T03, E04-T08) — READ-MODIFY-WRITE
    do item existente (preenche o veredito no child e normaliza item+child para
    DONE), preservando os demais campos/children.
(C) AUDITORIA de campos em TODOS os artefatos vigentes: nenhum child com
    `validation_result: PASS` pode ter `stage != 'DONE'`; nenhum filho BLOCKED
    pode ter `stage != 'VALIDATION'` (o contrato do leitor). Se houver misto,
    ABORTA (fail-closed).

Contrato do veredito (leitor do dashboard): `work_items[].children[]` =
{hermes_task_id, stage:'DONE', current_gate:'DONE' quando PASS,
 validation_result, evidence, validated_at, validated_by, eixo_de_risco,
 verification:{commit, ambiente, passes_independentes:'2',
 portoes:[{gate, exit, resultado}]}}.
BLOCKED = {stage:'VALIDATION', current_gate:'VALIDATION', validation_result:'BLOCKED',
 motivo_do_bloqueio:{...}, verification:{...}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote11.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote11.py --aplicar
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
VALIDADOR = "Hermes — validacao integrada (lote 11 — 8 cards de W0 da fila: JEV policy/benchmark/piso/gate/renumeracao/projecao)"
COMMIT = "332e67a1b5ad2309482c49e5cd336dc6c7291f16"  # origin/develop (arvore sob teste)

# --- ambiente compartilhado (suites offline/estaticas; sem banco/rede) -------- #
AMB_W0 = (
    "clone limpo (scratch) do repo do TRE em origin/develop = commit 332e67a (arvore sob teste), python "
    "/opt/hermes/.venv. Verificacao OFFLINE/ESTATICA, sem banco de producao, rede, credencial real, "
    "runtime de producao nem promocao. DUAS passadas com exit 0: saida crua BYTE-IDENTICA para "
    "verificar_jev_policy.py e verificar_jev_policy_v1_1.py; para verificar_jev_router.py, "
    "verificar_gate_jev.py e benchmark_roteamento.py a saida so e byte-identica APOS NORMALIZACAO "
    "DECLARADA de tokens volateis: diretorio temporario da suite "
    "(/opt/data/cache/scratch/<suite>-XXXX -> <suite>-TMP), decision_id aleatorio (dec-<hex> -> dec-HASH), "
    "sha256 do resultado do autoteste do benchmark (sha256=<hex> -> sha256=HASH) e a latencia medida em ms "
    "(mediana=.. p95=.. e '— .. ms' -> MS); nenhum outro token muda (diff cru pos-normalizacao = 0). "
    "Dente por mutacao em DUAS frentes: (a) o autoteste EMBUTIDO dos verificadores que tem um (policy v1.0 "
    "12/12, policy v1.1 64/64, roteador 21/21, benchmark 23/23) e (b) mutacoes EXTERNAS aplicadas em CLONE "
    "ISOLADO (git clone --no-hardlinks, em temp) nos 2 itens SEM autoteste proprio: o gate JEV (remover "
    "`execucao_de_card` do espelho `codigos_validos`) e a conferencia de renumeracao do T06-D01 (rotulo "
    "T08->T07 na homologacao) — os arquivos VERSIONADOS nunca foram alterados. O board real e /opt/hermes "
    "NAO foram tocados; nenhum container/volume docker foi criado (VPS dev: 5 containers / 7 volumes / 0 "
    "dangling ANTES e DEPOIS)."
)

# --------------------------------------------------------------------------- #
# Portoes / evidencia por card
# --------------------------------------------------------------------------- #

# --- 1. TRE-W0-E04-T01 (JEV Decision Policy V1) ----------------------------- #
PORT_T01 = [
    {"gate": "python3 scripts/verificar_jev_policy.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (42 itens, 0 falhas) + autoteste OK; itens 'lanes exatamente as 4 esperadas', "
                  "'limiar de aceite = 0.85', 'limiar conservador = 0.65', 'limiar de abstencao = 0.65', "
                  "'precedencia exata e na ordem' [security, human_approval, prioridade_e_dependencias, jev, llm] "
                  "e 'documento cita a cadeia de precedencia na ordem' OK — documento e YAML nao divergem"},
    {"gate": "python3 scripts/verificar_jev_policy.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (42 itens, 0 falhas) + autoteste OK (12/12)"},
    {"gate": "dente INTERNO (autoteste 12/12): mutacoes 'documento sem os limiares', 'documento sem a cadeia de "
             "precedencia', 'precedencia invertida', 'limiar de aceite afrouxado', 'recibo sem a versao da "
             "politica', 'fallback apontando para lane barata'", "exit": 0,
     "resultado": "cada mutacao REPROVA o item nomeado (ex.: 'documento cita a cadeia de precedencia na ordem' "
                  "reprovado pela mutacao correspondente) — o dente morde, nao e' decorativo"},
]
EVID_T01 = (
    "Reexecucao real do verificador da JEV Decision Policy V1 no develop 332e67a: 42 itens, 0 falhas. Prova "
    "que o documento (docs/architecture/jev-decision-policy-v1.md) e o arquivo legivel por maquina "
    "(hermes/jev/policy_v1.yaml) NAO divergem: as 4 lanes (small/medium/high/critical), os limiares "
    "(>=0,85 aceita; 0,65-0,85 lane conservadora; <0,65 abstem), a precedencia exata "
    "(Security -> Human Approval -> prioridade/dependencias -> JEV -> LLM), os perfis de modelo e a ausencia "
    "de modelo/preco hard-coded. Dente: autoteste 12/12 — cada mutacao (afrouxar limiar, inverter precedencia, "
    "remover a cadeia do documento, apontar para perfil inexistente) reprova o item nomeado."
)

# --- 2. TRE-W0-E04-T03 (benchmark anotado de routing) ----------------------- #
PORT_T03 = [
    {"gate": "python3 scripts/benchmark_roteamento.py --autoteste (passada 1)", "exit": 0,
     "resultado": "benchmark: OK — roda o roteador real sobre o corpus anotado (32 casos, corpus-anotacao-v1.5) "
                  "e recalcula as metricas declaradas: accuracy_de_lane 0.4375 (14/32, modo proposta-homologada), "
                  "falso_rebaixamento(critical) 8, taxa_de_escalacao 0.6875, custo relativo delta=11 (13.58%), "
                  "latencia mediana ~4,7 ms; AUTOTESTE 23/23 itens OK, 0 falhas"},
    {"gate": "python3 scripts/benchmark_roteamento.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida byte-identica APOS normalizacao declarada de data/hora volateis (diretorio temp da "
                  "suite, sha256 do resultado do autoteste e a latencia medida em ms); benchmark: OK, autoteste "
                  "23/23 items OK"},
    {"gate": "dente INTERNO (autoteste 23/23): 'metrica sensivel: accuracy cai exatamente 1/N ao desalinhar "
             "rotulo que acertava', 'falso_rebaixamento reage ao mesmo rotulo', 'rotulo uniforme small derruba "
             "a accuracy'", "exit": 0,
     "resultado": "cada mutacao REPROVA a metrica nomeada (ex.: high->critical em caso que acertava derruba a "
                  "accuracy 0.4375 -> 0.4062) — o dente morde o aceite de 'metricas calculadas e versionadas'"},
]
EVID_T03 = (
    "Reexecucao real do benchmark de routing no develop 332e67a (commit be2788a do card): roda o roteador de "
    "verdade sobre o corpus anotado (hermes/jev/benchmarks/corpus-anotacao.yaml, 32 casos, 30 em bloco + 2 "
    "individuais homologados por Anderson em 29/09) e recalcula as metricas de aceitacao — accuracy, falso "
    "rebaixamento (critical tratado como lane barata), taxa de escalacao, custo relativo e latencia — com "
    "resultado versionado em hermes/jev/benchmarks/. Dente: autoteste 23/23 — desalinhar um rotulo que o "
    "roteador acertava derruba a accuracy exatamente 1/N e o falso_rebaixamento reage; rotulo uniforme derruba "
    "a accuracy."
)

# --- 3. TRE-W0-E04-T08 (homologar v1.1 + piso de lane por ambiente) --------- #
PORT_T08 = [
    {"gate": "python3 scripts/verificar_jev_policy_v1_1.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (155 itens, 0 falhas) + autoteste OK (64/64); itens 'v1.1: a regra e piso "
                  "(so eleva, nunca rebaixa)', 'v1.1: ramo vivo_ou_producao: valores enumerados', 'v1.1: ramo "
                  "vivo exige aprovacao humana registrada', 'v1.1: ambiente nao declarado cai no ramo MAIS "
                  "conservador', 'v1.1: a execucao da regra e declarada', 'v1.1: o card citado EXISTE no board' "
                  "[t_d36c7d0f] e B4 comportamento (DDL em ambiente dev NAO aciona guardrail) OK"},
    {"gate": "python3 scripts/verificar_jev_policy_v1_1.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (155 itens, 0 falhas) + autoteste OK (64/64)"},
    {"gate": "dente INTERNO (autoteste 64/64): 'roteador sem o piso: a regra declarada volta a ser ignorada em "
             "silencio', 'piso REBAIXANDO: a lane minima do ramo vira a lane final (sem comparar)', 'ambiente "
             "NAO declarado caindo no PRIMEIRO ramo (dev)', 'ramo vivo sem exigir aprovacao humana'", "exit": 0,
     "resultado": "cada mutacao REPROVA os itens nomeados (5, 4, 4 e 4 itens respectivamente) — o dente morde "
                  "o piso e a recusa de rebaixamento"},
    {"gate": "corroboracao: python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK — o roteador em vigor executa a v1.1 e a "
                  "piso por ambiente (a suite do roteador reconfirma o contrato comportamental)"},
]
EVID_T08 = (
    "Reexecucao real do verificador da JEV Decision Policy v1.1 no develop 332e67a: 155 itens, 0 falhas. Prova, "
    "por COMPORTAMENTO contra o roteador, que a homologacao (Anderson Ribeiro, registro em "
    "docs/operations/registro-de-aprovacoes.md) esta declarada e que o piso de lane por ambiente foi "
    "implementado: a regra SO ELEVA (direcao='piso_so_eleva'), DDL/migration em ambiente dev => lane nao mais "
    "barata que high sem aprovacao humana; ambiente vivo/producao => nao mais barata que critical com aprovacao "
    "humana registrada; ambiente nao declarado => ramo conservador; o portao de versao do roteador esta aberto "
    "para jev-policy-v1.1 e a v1.0 segue preservada para auditoria. Corrige o card 'Homologar a politica JEV "
    "v1.1 e implementar o piso de lane por ambiente no roteador'. Dente: autoteste 64/64 — desligar o piso, "
    "fazer o piso rebaixar, jogar o ambiente nao declarado no ramo dev, ou tirar a aprovacao humana do ramo vivo "
    "reprova os itens nomeados."
)

# --- 4. TRE-W0-E04-T02-D06 (canonicalizacao por vocabulario) ---------------- #
PORT_T02D06 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK (21/21); item nomeado 'T07: as 5 frases "
                  "que ja escaparam nao executam (4 do D04 + 1 do D06)' OK [as 5 frases terminam em BLOCK/ESCALATE "
                  "com recibo de 13 campos]; 'T07: dominio sensivel inferido do texto barra mesmo sem sinal "
                  "declarado' OK; 'D07: codigo canonico comum com texto limpo segue executando' OK"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida byte-identica APOS normalizacao declarada (diretorio temp da suite e decision_id "
                  "aleatorio dec-<hex> -> dec-HASH); PASS (63 itens, 0 falhas) + autoteste OK (21/21)"},
    {"gate": "dente INTERNO (autoteste 21/21): 'T07: falha fechada da acao nao classificada removida', "
             "'D07: texto livre sem codigo volta a executar (falha fechada volta a exigir dominio sensivel)'",
     "exit": 0,
     "resultado": "as mutacoes REPROVAM o item nomeado 'T07: as 5 frases que ja escaparam nao executam' (11 e 6 "
                  "itens reprovados respectivamente) — o dente morde exatamente a classe do D06"},
]
EVID_T02D06 = (
    "Reexecucao real da suite do roteador do JEV no develop 332e67a: 63 itens, 0 falhas. A frase que escapou no "
    "defeito D06 ('enviar mensagem ao primeiro cliente interessado') agora NAO executa: ela nao resolve para "
    "codigo canonico de acao E a tarefa carrega dominio sensivel inferido do texto (dado_de_cliente, "
    "outbound_a_terceiro), entao o roteador FALHA FECHADO — ESCALATE, exit 2, recibo de 13 campos, "
    "origem='nao_classificada'. A causa de raiz entregue e' codigo canonico de acao como via principal + "
    "inferencia de dominio sensivel do proprio texto + falha fechada quando a acao nao classifica em tarefa "
    "sensivel — nao ampliacao de sinonimos. Dente: autoteste 21/21 — remover a falha fechada da acao nao "
    "classificada (ou fazer o texto livre sem codigo voltar a executar) reprova o item nomeado."
)

# --- 5. TRE-W0-E04-T03-D01 (lacuna do corpus) ------------------------------- #
PORT_T03D01 = [
    {"gate": "python3 scripts/benchmark_roteamento.py --autoteste (passada 1)", "exit": 0,
     "resultado": "benchmark: OK; itens 'corpus: os 32 casos declaram acao_codigo (mesmo que null) — sem o "
                  "campo: []', 'corpus: todo codigo declarado e do catalogo vigente — fora do catalogo: []', "
                  "'populacoes: os 32 casos aparecem exatamente uma vez (particao) — {executavel:5, "
                  "bloqueado_por_regra_com_codigo_comum:18, acao_proibida_decisao_humana:6, "
                  "sem_codigo_no_catalogo:3} = 32 de 32', 'populacoes: caso SEM codigo no catalogo nunca executa "
                  "(falha fechada D07) — violacoes: []' e 'medicao: o acao_codigo do corpus chega ao roteador "
                  "como codigo canonico — 29/29 resolvidos'; AUTOTESTE 23/23"},
    {"gate": "python3 scripts/benchmark_roteamento.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "byte-identica APOS normalizacao declarada (diretorio temp, sha256 do autoteste, latencia em "
                  "ms); benchmark: OK, autoteste 23/23"},
    {"gate": "dente INTERNO (autoteste 23/23): 'metrica sensivel: accuracy cai exatamente 1/N', "
             "'populacoes: os 32 casos aparecem exatamente uma vez (particao)'", "exit": 0,
     "resultado": "as mutacoes REPROVAM os itens nomeados da particao e da sensibilidade da metrica — o dente "
                  "morde o aceite de separar executavel de nao-executavel"},
]
EVID_T03D01 = (
    "Reexecucao real do MESMO instrumento (scripts/benchmark_roteamento.py) no develop 332e67a: a lacuna "
    "medida (28 de 32 casos sem codigo canonico de acao, benchmark sem poder de medicao) foi fechada — hoje os "
    "32 casos declaram `acao_codigo` (29 resolvem para codigo do catalogo; 3 declaram null explicito e sao "
    " 'sem_codigo_no_catalogo') e a medicao SEPARA as populacoes: executavel=5, bloqueado_por_regra_com_codigo_"
    "comum=18, acao_proibida_decisao_humana=6, sem_codigo_no_catalogo=3 (32 de 32, particao exata). O poder de "
    "medicao sobe de accuracy crua 0.4375 para 0.6087 (14/23) sobre os executaveis. Nenhum `lane_esperada` "
    "homologado foi alterado; o D07/D08 nao foi afrouxado. Dente: autoteste 23/23 — a particao e a sensibilidade "
    "da accuracy reprovam as mutacoes correspondentes."
)

# --- 6. TRE-W0-E04-T10 (nomear execucao_de_card) ---------------------------- #
PORT_T10 = [
    {"gate": "python3 scripts/verificar_gate_jev.py (passada 1)", "exit": 0,
     "resultado": "PASS (30 itens, 0 falhas); item 30 'S10 codigos_validos do acoes-declaradas.yaml == "
                  "CODIGOS_DE_ACAO_COMUNS do roteador' OK [yaml=roteador=['ajuste_de_texto','consulta_interna',"
                  "'operacao_comercial','migracao_de_esquema','execucao_de_card']] — os dois catalogos CASAM, "
                  "`execucao_de_card` nomeado nos dois"},
    {"gate": "python3 scripts/verificar_gate_jev.py (passada 2, identica)", "exit": 0,
     "resultado": "saida byte-identica APOS normalizacao declarada (diretorio temp da suite gate-jev-XXXX -> "
                  "gate-jev-TMP); PASS (30 itens, 0 falhas)"},
    {"gate": "dente EXTERNO (clone isolado, --no-hardlinks): remove `execucao_de_card` do espelho "
             "`codigos_validos` em hermes/jev/acoes-declaradas.yaml", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 30: 'FALHOU 30 S10 codigos_validos... == CODIGOS_DE_ACAO_"
                  "COMUNS' [yaml sem execucao_de_card] e RESULTADO: FALHOU (30 itens, 1 falhas), exit 1 — o "
                  "dente morde o espelho nomeado pelo card"},
]
EVID_T10 = (
    "Reexecucao real da suite do encaixe do gate JEV no develop 332e67a: 30 itens, 0 falhas. Prova que o codigo "
    "comum `execucao_de_card` (decisao do dono, 30/09/2026; escopo estreito: dev, sem producao, sem credencial) "
    "foi nomeado nos DOIS lados que TEM de casar — CODIGOS_DE_ACAO_COMUNS em hermes/jev/routing/router.py e o "
    "espelho `codigos_validos` de hermes/jev/acoes-declaradas.yaml. Dente: mutacao EXTERNA em clone isolado "
    "removendo `execucao_de_card` do espelho — o item 30 reprova e a suite sai com exit 1. GAP DECLARADO: "
    "verificar_gate_jev.py NAO tem `--autoteste` proprio (tem mutacoes embutidas S6/S6b/S6c que medem o "
    "acoplamento do encaixe ao kernel, nao o espelho do catalogo); por isso o dente deste card e' a mutacao "
    "externa."
)

# --- 7. TRE-W0-E04-T06-D01 (numero de card duplicado) ----------------------- #
PORT_T06D01 = [
    {"gate": "python3 conferencia_t06d01.py <clone> (passada 1) — conferencia read-only board+repo",
     "exit": 0,
     "resultado": "RESULTADO: PASS; ITEM 1 'board: nenhum numero TRE-W0-E04-T<n> repete entre ids' OK "
                  "[repetidos=nenhum; T07=[t_f8a6e209]; T08=[t_d36c7d0f]]; ITEM 2 'homologacao (t_d36c7d0f) "
                  "rotulada TRE-W0-E04-T08, sem rotulo T07 orfao' OK [T07-para-homolog=nenhum; ocorrencias "
                  "T08=10]; ITEM 3 'T07 legitimo = t_f8a6e209' OK"},
    {"gate": "python3 conferencia_t06d01.py <clone> (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); RESULTADO: PASS (0 falhas)"},
    {"gate": "dente EXTERNO (clone isolado, --no-hardlinks): reverte o rotulo TRE-W0-E04-T08 -> TRE-W0-E04-T07 "
             "na homologacao (hermes/jev/policy_v1_1.yaml)", "exit": 0,
     "resultado": "a mutacao REPROVA o item nomeado 2: 'FALHOU ITEM 2 repo: homologacao (t_d36c7d0f) rotulada "
                  "TRE-W0-E04-T08, sem rotulo T07 orfao [T07-para-homolog=hermes/jev/policy_v1_1.yaml:37,167]' "
                  "e RESULTADO: FALHOU (ITEM 2), exit 1 — o dente morde o rotulo nomeado pelo card"},
    {"gate": "corroboracao: python3 scripts/verificar_jev_policy_v1_1.py --autoteste (passada 1)", "exit": 0,
     "resultado": "PASS (155 itens, 0 falhas); item 'v1.1: o card citado EXISTE no board (a decisao viaja para "
                  "quem executa)' OK [t_d36c7d0f: status='done' ... titulo='TRE-W0-E04-T08 — Homologar a "
                  "politica JEV v1.1'] — a referencia no repo aponta o id do card RENOMEADO"},
]
EVID_T06D01 = (
    "Conferencia read-only (board em modo leitura + repo) no develop 332e67a: o numero de card duplicado "
    "(dois cards como TRE-W0-E04-T07) esta corrigido. ITEM 1: nenhum numero principal TRE-W0-E04-T<n> repete "
    "entre ids (T07=[t_f8a6e209], T08=[t_d36c7d0f]); ITEM 2: as referencias versionadas (policy_v1_1.yaml e os "
    "dois documentos da v1.1) rotulam a homologacao como TRE-W0-E04-T08 com o id t_d36c7d0f, sem rotulo T07 "
    "orfao; ITEM 3: o T07 legitimo (codigo canonico de acao) segue t_f8a6e209. Dente: mutacao EXTERNA em clone "
    "isolado revertendo o rotulo T08->T07 na homologacao — o ITEM 2 reprova e a conferencia sai com exit 1. "
    "GAP DECLARADO: a conferencia NAO tem autoteste proprio (e' o proprio criterio de aceite do card: "
    "'conferir depois, com saida bruta'); a corroboracao pela suite da v1.1 (item 'o card citado EXISTE no "
    "board') fecha a linhagem."
)

# --- 8. TRE-W0-E04-T11 (projecao de entrega: raiz fixa) — BLOCKED ----------- #
PORT_T11 = [
    {"gate": "python3 scripts/verificar_projecao_entrega.py (passada 1)", "exit": 1,
     "resultado": "RESULTADO: 38/50 itens PASS, 12 falhas; entre elas 'plugin em uso (copia user do dashboard) == "
                  "canonico versionado' [live sha256=050b8a50 != canonico cef4412], 'precedencia: board.json "
                  "declarado vence a env' e 'sem configuracao => raiz legada'"},
    {"gate": "python3 scripts/verificar_projecao_entrega.py (passada 2, identica)", "exit": 1,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); RESULTADO: 38/50 itens PASS, 12 falhas (mesmas 12)"},
    {"gate": "medicao da razao do bloqueio (contagens fixas do verificador x estado evoluido)", "exit": 1,
     "resultado": "o verificador pina expectativas de 30/09/2026 que o board+artefato JA' ultrapassaram: "
                  "'artefato W0: 14 itens => 16 ids' [hoje 26 itens / 28 ids], 'raiz padrao: 44 ids terminais' "
                  "[hoje 56], '0 cards done projetando validation' [hoje 62 de 168]; o conserto versionado "
                  "(patch byte-a-byte reproduz o canonico + editor autoteste 16/16) esta OK, mas a copia `user` "
                  "do plugin nao esta byte-identica ao canonico (passo do operador) e o proprio verificador "
                  "precisa das contagens refrescadas pelo dono do card"},
]
EVID_T11 = (
    "Verificacao real, DUAS passadas identicas no develop 332e67a: verificar_projecao_entrega.py devolve "
    "RESULTADO: 38/50 PASS, 12 falhas (exit 1). O conserto versionado esta correto e provado no proprio suite "
    "(baseline pristina, patch aplica e reproduz o canonico byte a byte, editor ancorado autoteste 16/16, "
    "autoteste por mutacao 4/4 REPROVA) — o que FALTA e' ambiente/pre-condicao alheia ao agente: (1) a copia "
    "`user` do plugin do dashboard (/opt/data/plugins, sha256=050b8a50) NAO esta byte-identica ao canonico "
    "versionado (cef4412) — passo do operador (troca de imagem / aplicar_projecao_entrega.sh); (2) o proprio "
    "verificador pina contagens de 30/09/2026 (14 itens/16 ids/44 ids/12 cards) que o board e o W0 ja' "
    "ultrapassaram (26 itens/28 ids/56/62), entao mesmo com o plugin normalizado o aceite numerico nao fecha. "
    "BLOCKED (nao PASS, nao FAIL): o artefato nao foi reprovado, a prova nao pode ser concluida neste ambiente. "
    "PRE-CONDICAO PARA DESBLOQUEAR: operador aplica o conserto (aplicar_projecao_entrega.sh) e o dono do card "
    "refresca as contagens fixas do verificador (14/16/44/12) para o estado atual — em card proprio, com o "
    "texto do BLOCKED preservado."
)

# --------------------------------------------------------------------------- #
# Itens NOVOS (append) — bloco com PASS e o BLOCKED (T11)
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


def _child_blocked(tid, evidence, portoes, preview):
    return {
        "hermes_task_id": tid,
        "stage": "VALIDATION",
        "current_gate": "VALIDATION",
        "validation_result": "BLOCKED",
        "eixo_de_risco": "CONTRATO/API",
        "evidence": evidence,
        "validated_at": DATA,
        "validated_by": VALIDADOR,
        "motivo_do_bloqueio": preview,
        "verification": {
            "commit": COMMIT,
            "ambiente": AMB_W0,
            "passes_independentes": "2",
            "portoes": portoes,
        },
    }


def _item(idx, titulo, child):
    if child.get("validation_result") == "BLOCKED":
        return {"id": idx, "title": titulo, "stage": "VALIDATION",
                "current_gate": "VALIDATION", "children": [child]}
    return {"id": idx, "title": titulo, "stage": "DONE", "current_gate": "DONE",
            "children": [child]}


BLOCKED_PREVIEW = {
    "pre_condicao": [
        "operador aplica o conserto versionado no plugin do dashboard (deploy/hermes/projecao-entrega/"
        "aplicar_projecao_entrega.sh) para a copia `user` ficar byte-identica ao canonico versionado;",
        "dono do card refresca as contagens fixas do verificador (14 itens/16 ids/44 ids/12 cards) para o "
        "estado atual (26 itens/28 ids/56/62) em card proprio.",
    ],
    "por_que_bloqueado": "o artefato (patch versionado) nao foi reprovado, mas a prova de aceitacao depende de "
                         "passo do operador (copia do plugin) e de expectativas do verificador pinadas em "
                         "30/09/2026, hoje ultrapassadas pelo board/artefato.",
}

NOVOS = [
    _item("TRE-W0-E04-T11",
          "Projecao de entrega: raiz do registro de artefatos nao pode ser caminho fixo de outro projeto",
          _child_blocked("t_b74c2edd", EVID_T11, PORT_T11, BLOCKED_PREVIEW)),
    _item("TRE-W0-E04-T02-D06",
          "DEFEITO canonicalizacao por vocabulario deixou passar primeiro contato em prosa nova",
          _child("t_d10fda7c", EVID_T02D06, PORT_T02D06)),
    _item("TRE-W0-E04-T03-D01",
          "LACUNA: 28 de 32 casos do corpus sem codigo canonico de acao (benchmark sem poder de medicao)",
          _child("t_dfcfc4d4", EVID_T03D01, PORT_T03D01)),
    _item("TRE-W0-E04-T10",
          "Nomear o codigo comum execucao_de_card (escopo estreito, decisao do dono)",
          _child("t_e09a95bf", EVID_T10, PORT_T10)),
    _item("TRE-W0-E04-T06-D01",
          "DEFEITO [retroativo]: numero de card duplicado (dois cards como TRE-W0-E04-T07)",
          _child("t_e589867b", EVID_T06D01, PORT_T06D01)),
]

# Itens JA EXISTENTES (read-modify-write) — E04-T01, E04-T03, E04-T08
EXISTENTES = {
    "TRE-W0-E04-T01": {"child": "t_e7d9decd", "evidence": EVID_T01, "portoes": PORT_T01},
    "TRE-W0-E04-T03": {"child": "t_d8bc83b3", "evidence": EVID_T03, "portoes": PORT_T03},
    "TRE-W0-E04-T08": {"child": "t_d36c7d0f", "evidence": EVID_T08, "portoes": PORT_T08},
}

IDS_PASS = [c["hermes_task_id"] for it in NOVOS for c in it["children"]
            if c["validation_result"] == "PASS"] + [v["child"] for v in EXISTENTES.values()]
IDS_BLOCKED = [c["hermes_task_id"] for it in NOVOS for c in it["children"]
               if c["validation_result"] == "BLOCKED"]
IDS = IDS_PASS + IDS_BLOCKED


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
        "scope": "lote 11 — 8 cards de W0 da fila (T11 BLOCKED + D06 do T02 + T08 + T03 + D01 do T03 + T10 + "
                 "D01 do T06 + T01)",
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


def auditar_campos() -> tuple[list[tuple], int]:
    """PASS exige stage DONE (filho+item); BLOCKED exige stage VALIDATION (filho+item).

    Devolve (mistos, n_blocked). Qualquer combinacao fora do contrato e' misto.
    """
    mistos = []
    n_blocked = 0
    for p in _artefatos_vigentes():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in d.get("work_items") or []:
            for ch in it.get("children") or []:
                vr = str(ch.get("validation_result") or "").upper()
                st = str(ch.get("stage") or "").upper()
                if vr == "PASS" and st != "DONE":
                    mistos.append((p.name, it.get("id"), ch.get("hermes_task_id"),
                                   ch.get("stage"), ch.get("current_gate"), vr))
                elif vr == "BLOCKED":
                    n_blocked += 1
                    if st != "VALIDATION":
                        mistos.append((p.name, it.get("id"), ch.get("hermes_task_id"),
                                       ch.get("stage"), ch.get("current_gate"), vr))
            ivr = str(it.get("validation_result") or "").upper()
            if ivr and str(it.get("stage") or "").upper() != "DONE":
                mistos.append((p.name, it.get("id"), "<item>", it.get("stage"),
                               it.get("current_gate"), ivr))
    return mistos, n_blocked


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


def _validar_leitor() -> None:
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
    # PASS: tem de sair da coluna e entrar em explicit_done.
    retidos = [i for i in IDS_PASS if i in (col or {})]
    faltam = [i for i in IDS_PASS if i not in (exp or set())]
    print(f"  leitor: {len(IDS_PASS)} cards PASS; ainda na coluna de validacao: {retidos or 'nenhum'}")
    if faltam:
        raise SystemExit(f"FAIL-CLOSED: leitor NAO marcou como DONE explicito: {faltam}")
    if retidos:
        raise SystemExit(f"FAIL-CLOSED: leitor ainda retem na coluna de validacao: {retidos}")
    # BLOCKED: tem de ESTAR na coluna (validation) e NAO entrar em explicit_done.
    for i in IDS_BLOCKED:
        st = (col or {}).get(i)
        if st != "validation":
            raise SystemExit(f"FAIL-CLOSED: BLOCKED {i} deveria projetar 'validation', projetou {st!r}")
        if i in (exp or set()):
            raise SystemExit(f"FAIL-CLOSED: BLOCKED {i} nao pode entrar em explicit_done")
    print(f"  leitor: cards PASS saem da coluna / entram em DONE explicito; "
          f"BLOCKED ({IDS_BLOCKED}) projetam 'validation' e NAO entram em DONE explicito")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve (com backup datado)")
    args = p.parse_args()

    board = ler_board()
    faltando = [i for i in IDS if board.get(i) != "done"]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")

    print("=== REGISTRO DOS VEREDITOS — LOTE 11 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")
    print(f"commit sob teste: {COMMIT}")

    # AUDITORIA DE CAMPOS (fail-closed): nenhum misto pode sobrar.
    mistos, n_blocked = auditar_campos()
    print(f"(AUDITORIA) artefatos vigentes com campo misto (PASS fora de DONE / BLOCKED fora de VALIDATION): "
          f"{len(mistos)}")
    for m in mistos:
        print(f"    MISTO: {m}")
    print(f"(AUDITORIA) children BLOCKED existentes (esperado: 1 no fim do lote): {n_blocked}")
    if mistos and args.aplicar:
        raise SystemExit("FAIL-CLOSED: ha campos mistos; normalizar antes de gravar")

    base = json.loads(ARTEFATO_W0.read_text(encoding="utf-8"))
    w0, n = _append_itens(base, NOVOS)
    ajustes = []
    for item_id, spec in EXISTENTES.items():
        w0, ajuste = _atualizar_item(w0, item_id, spec)
        ajustes.append(ajuste)

    print(f"(A) W0: +{n} itens novos (1 BLOCKED + 4 PASS) | {len(ajustes)} itens pre-existentes por RMW")
    for it in NOVOS:
        c = it["children"][0]
        print(f"    novo {it['id']:<22} {c['hermes_task_id']} -> {c['validation_result']} "
              f"(item {it['stage']})")
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
    _validar_leitor()

    # Reaudita depois de gravar.
    pos, nb = auditar_campos()
    print(f"(AUDITORIA pos-gravacao) mistos: {len(pos)} (BLOCKED: {nb})")
    if pos:
        raise SystemExit(f"FAIL-CLOSED: mistos apos gravar: {pos}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
