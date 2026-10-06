#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra a VALIDACAO INTEGRADA (lote 12).

Fecha os PROXIMOS 10 cards da fila de triagem (ordens 110..119, 0-based 109..118;
a ordem 109 / t_e7d9decd foi fechada no lote 11):

  W0 / JEV (verificados OFFLINE no clone limpo de develop 8e1466d):
   1. TRE-W0-E04-T06     t_e9535df3  Politica JEV v1.1: lane conservadora legivel por maquina -> RMW
   2. TRE-W0-E04-T07     t_f8a6e209  Codigo canonico de acao + falha fechada (raiz do D06)    -> RMW
   3. TRE-W0-E04-T05-D01 t_fa85fa52  Suite do gate JEV: expectativa impossivel no S9          -> APPEND

  W2 / Odoo (consertos em branches `fix/*` NAO mergeadas; verificados no topo da branch,
  executando os verificadores reais na VPS de dev em duplas descartaveis proprias):
   4. TRE-W2-E03-T01-D04     t_025f9a2a  --prova-de-dente por caminho resolvido (era "$0")     -> APPEND
   5. TRE-W2-E03-T01-D01     t_578a4e4d  item do log de teste reprovado casa o Odoo 19         -> APPEND
   6. TRE-W2-E03-T01-D02     t_5c4fc7ac  modo dente nao sobrescreve os logs do aceite          -> APPEND
   7. TRE-W2-E03-T01-D04-D01 t_52c74f31  registro cita o head entregue (f773d3c), nao c41822e  -> APPEND
   8. DEFEITO-t_26be11c7     t_26be11c7  errata do ponteiro f1f1cb6b (fora de ref nenhuma)     -> APPEND
   9. TRE-W2-E04-T01-D01     t_5cad1689  artefatos do E04-T01 dentro do verificador de estrutura-> APPEND
  10. TRE-W2-E04-T02-D01     t_945f96f1  assinatura de dente exige o caminho saudavel (O8)      -> APPEND

Rito (skill validacao-de-entregas / precedentes lote1..lote11):
1. dry-run por padrao; `--aplicar` escreve. backup datado antes de cada arquivo.
2. NUNCA autoriza producao (`production_promotion_authorized` segue false).
3. fail-closed: recusa se algum card declarado nao estiver `done` no board.
4. AUDITORIA de campos em TODOS os artefatos vigentes (nenhum PASS fora de DONE).

Contrato do veredito (leitor do dashboard): `work_items[].children[]` =
{hermes_task_id, stage:'DONE', current_gate:'DONE' quando PASS,
 validation_result, evidence, validated_at, validated_by, eixo_de_risco,
 verification:{commit, ambiente, passes_independentes:'2',
 portoes:[{gate, exit, resultado}]}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote12.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote12.py --aplicar
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
ARTEFATO_W2 = DELIVERIES / "W2-odoo-e-seguranca.json"
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")
PLUGIN_API = pathlib.Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")

DATA = "2026-10-04"
VALIDADOR = ("Hermes — validacao integrada (lote 12 — 10 cards da fila: 3 W0/JEV offline + "
             "7 W2/Odoo em dupla descartavel própria na VPS de dev)")

# --- commits sob teste ------------------------------------------------------ #
COMMIT_W0 = "8e1466d50b4d53eecb4093b67c8c1113f910ca7d"   # origin/develop (arvore sob teste dos 3 W0)
COMMIT_D05 = "3f104acfcdc45b7dc7d594604110e4933d6598f5"  # fix/TRE-W2-E03-T01-D05 (consolidado D01+D02+D03+D04)
COMMIT_PTR = "292baf8"                                    # fix/t_37db9564-verificador-ponteiros (verificador da classe)
COMMIT_E04T01D01 = "80b64a8746499b83165727efa7ce5c34aa7c56b2"  # fix/TRE-W2-E04-T01-D01
COMMIT_E04T02D01 = "a96e8af71bd9315f23233762d197666c023e5bf0"  # fix/TRE-W2-E04-T02-D01

# --- ambientes -------------------------------------------------------------- #
AMB_W0 = (
    "clone limpo (scratch) do repo do TRE em origin/develop = commit 8e1466d (arvore sob teste), "
    "python /opt/hermes/.venv. Verificacao OFFLINE/ESTATICA, sem banco de producao, rede, credencial real, "
    "runtime de producao nem promocao. DUAS passadas com exit 0; saida crua BYTE-IDENTICA para "
    "verificar_jev_policy_v1_1.py (sem token volatil) e para verificar_jev_router.py (normalizacao "
    "declarada de decision_id aleatorio dec-<hex> -> dec-HASH); para verificar_gate_jev.py a saida e "
    "byte-identica apos normalizacao declarada do diretorio temporario da suite "
    "(/opt/data/cache/scratch/gate-jev-XXXX -> gate-jev-TMP). Dente por mutacao INTERNA (autoteste embutido): "
    "policy v1.1 64/64, roteador 21/21; no gate a suite traz as mutacoes embutidas S6/S6b/S6c (kernel sem a "
    "edicao) como itens proprios. Nenhum container/volume docker foi criado nesta hosted host (docker ausente)."
)
AMB_W2 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): clone ISOLADO em /tmp/tre_lote12/repo (bundle git do commit "
    "sob teste), NUNCA a copia operacional /opt/tre/repo nem o modulo instalado do dev. Os verificadores "
    "executam em DUPLA DESCARTÁVEL PRÓPRIA (postgres:16 + odoo:19.0, rede/banco/nomes proprios, --rm na "
    "limpeza; nunca pg-odoo-dev/odoo_dev). Modulo lido da copia isolada. DUAS passadas com exit 0; saida "
    "byte-identica apos normalizacao declarada dos tokens volateis (sufixo do container/rede "
    "e03t01-*/e04t02-*, diretorio temporario /tmp/verificacao-*/, timestamps dos logs e caminho do clone). "
    "Higiene: 5 containers / 7 volumes / 0 dangling antes; os conteineres/volumes descartaveis dos runs foram "
    "removidos (docker rm -f -v dos residuos) e o estado medido depois. Zero escrita em sales_intelligence; "
    "zero producao. Havia uma EXECUCAO IRMA concorrente (outro clone /tmp/tre_lote12/repo_e04t02) em voo — "
    "seus conteineres NAO foram tocados."
)

# --------------------------------------------------------------------------- #
# Portoes / evidencia por card
# --------------------------------------------------------------------------- #

# --- 1. TRE-W0-E04-T06 (JEV policy v1.1) ------------------------------------ #
PORT_T06 = [
    {"gate": "python3 scripts/verificar_jev_policy_v1_1.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (155 itens, 0 falhas) + autoteste OK; itens nomeados pelo card 'v1.1: a "
                  "regra e piso (so eleva, nunca rebaixa)', 'v1.1: ramo vivo_ou_producao: valores enumerados', "
                  "'v1.1: ramo vivo exige aprovacao humana registrada', 'v1.1: ambiente nao declarado cai no "
                  "ramo MAIS conservador', 'documento deixa de citar a chave explicita (limiares."
                  "lane_conservadora)' e 'custo_por_card_verified deixa de declarar por que nao e medivel' OK"},
    {"gate": "python3 scripts/verificar_jev_policy_v1_1.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (155 itens, 0 falhas) + autoteste OK (64/64)"},
    {"gate": "dente INTERNO (autoteste 64/64): 'roteador sem o piso: a regra declarada volta a ser ignorada em "
             "silencio' (5 itens reprovados), 'piso REBAIXANDO: a lane minima do ramo vira a lane final' (4), "
             "'ambiente NAO declarado caindo no PRIMEIRO ramo (dev)' (4), 'documento deixa de declarar a regra "
             "por ambiente' (1), 'custo_por_card_verified deixa de declarar por que nao e medivel' (1)", "exit": 0,
     "resultado": "cada mutacao REPROVA os itens nomeados — a chave explicita `lane_conservadora` e as metricas "
                  "do recibo instrumentadas tem dente, nao sao decorativas"},
]
EVID_T06 = (
    "Reexecucao real do verificador da JEV Decision Policy v1.1 no develop 8e1466d: 155 itens, 0 falhas + "
    "autoteste 64/64. Prova que a lane conservadora virou CONTRATO LEGIVEL POR MAQUINA (chave explicita "
    "`limiares.lane_conservadora`, nao prosa do YAML) e que as metricas declaradas na secao `metricas` estao "
    "instrumentadas no recibo (13 campos exatos, sem 14o). A regra por ambiente (DDL em dev => high; DDL em "
    "ambiente vivo => critical com aprovacao humana; ambiente nao declarado => ramo conservador) e executada "
    "pelo roteador em vigor. Dente: autoteste 64/64 — desligar o piso, faze-lo rebaixar, jogar o ambiente nao "
    "declarado no ramo dev, ou tirar a chave do documento reprova os itens nomeados."
)

# --- 2. TRE-W0-E04-T07 (codigo canonico de acao + falha fechada) ------------ #
PORT_T07 = [
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (63 itens, 0 falhas) + autoteste OK (21/21); itens 'T07: as 5 frases que ja "
                  "escaparam nao executam (4 do D04 + 1 do D06)' OK [terminam em BLOCK/ESCALATE com recibo de 13 "
                  "campos], 'T07: dominio sensivel inferido do texto barra mesmo sem sinal declarado' OK, "
                  "'D07: codigo canonico comum com texto limpo segue executando' OK"},
    {"gate": "python3 scripts/verificar_jev_router.py --autoteste (passada 2, identica)", "exit": 0,
     "resultado": "saida byte-identica APOS normalizacao declarada (decision_id aleatorio dec-<hex> -> dec-HASH); "
                  "PASS (63 itens, 0 falhas) + autoteste OK (21/21)"},
    {"gate": "dente INTERNO (autoteste 21/21): 'T07: acao deixa de resolver para codigo canonico (volta a "
             "depender de prosa)' (18 itens reprovados), 'T07: falha fechada da acao nao classificada removida' "
             "(11), 'T07: inferencia de dominio sensivel do texto desligada' (5), 'D07: texto livre sem codigo "
             "volta a executar' (6)", "exit": 0,
     "resultado": "cada mutacao REPROVA o item nomeado — o codigo canonico de acao como via principal e a falha "
                  "fechada para acao nao classificada tem dente"},
]
EVID_T07 = (
    "Reexecucao real da suite do roteador JEV no develop 8e1466d: 63 itens, 0 falhas + autoteste 21/21. A "
    "correcao de raiz do D06 esta entregue: o roteador resolve a acao para um CODIGO canonico antes de decidir "
    "(texto livre fica como PONTE), infere do proprio texto os dominios sensiveis (producao/release, credencial, "
    "dado de cliente, outbound a terceiro) e FALHA FECHADO (ESCALATE, exit 2, recibo de 13 campos, "
    "origem='nao_classificada') quando a acao nao classifica em tarefa sensivel. A frase que escapou no D06 "
    "('enviar mensagem ao primeiro cliente interessado') nao executa. Dente: autoteste 21/21 — remover a falha "
    "fechada ou a inferencia de dominio, ou fazer o roteador voltar a depender de prosa, reprova os itens nomeados."
)

# --- 3. TRE-W0-E04-T05-D01 (suite do gate: conta do S9) --------------------- #
PORT_T05D01 = [
    {"gate": "python3 scripts/verificar_gate_jev.py (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (30 itens, 0 falhas); item 25 'S9 recibo: consulta ao gate SEMPRE deixa "
                  "registro (recibo de 13 campos ou registro explicito de falha do encaixe), nos 8 cenarios' OK; "
                  "item 26 'S9 recibo: os 5 cenarios obrigatorios de SUCESSO deixam, cada um, o seu recibo com os "
                  "13 campos exatos do contrato' OK — a conta impossivel (>=6 recibos de 8 cenarios, dos quais 3 "
                  "sao registros de falha) foi corrigida para os 5 cenarios de SUCESSO que de fato emitem recibo"},
    {"gate": "python3 scripts/verificar_gate_jev.py (passada 2, identica)", "exit": 0,
     "resultado": "saida byte-identica APOS normalizacao declarada do diretorio temporario da suite "
                  "(gate-jev-XXXX -> gate-jev-TMP); PASS (30 itens, 0 falhas)"},
    {"gate": "mutacoes embutidas S6/S6b/S6c (o codigo do ponto de estrangulamento e a trava do caminho manual)",
     "exit": 0,
     "resultado": "itens 15-18: 'S6 MUTACAO: encaixe ligado + kernel sem a edicao => card EXECUTA', 'S6b "
                  "MUTACAO: revertendo SO a edicao de claim_task, o claim manual volta a funcionar', 'S6b: com o "
                  "claim_task livre, o DESPACHANTE ainda segura o card', 'S6b: o tick reporta o card no balde "
                  "skipped_jev_gate' — a mutacao do kernel faz o card executar, provando que a edicao e o que segura"},
]
EVID_T05D01 = (
    "Reexecucao real da suite do encaixe do gate JEV no develop 8e1466d: 30 itens, 0 falhas. O defeito "
    "retroativo (S9 exigia `verificados >= 6` recibos de 8 cenarios, dos quais 3 sao registros de falha do "
    "encaixe — 5 >= 6 falso por construcao) esta corrigido: a expectativa passou a contar os 5 cenarios "
    "obrigatorios de SUCESSO, coerente com a enumeracao do proprio item 25. Nao houve afrouxamento: item 25 "
    "continua exigindo registro em TODOS os 8 cenarios. Dente: as mutacoes embutidas S6/S6b/S6c continuam "
    "reprovando o aceite quando o codigo do ponto de estrangulamento e revertido."
)

# --- 4. TRE-W2-E03-T01-D04 (invocacao por caminho resolvido) ---------------- #
PORT_D04 = [
    {"gate": "bash verificar-modulo-odoo.sh (aceite completo, forma documentada por NOME) @ 3f104ac", "exit": 0,
     "resultado": "RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas) modulo=transformativa_sales_ai "
                  "banco=tre_l12_d05_aceite imagens=odoo:19.0+postgres:16"},
    {"gate": "bash verificar-modulo-odoo.sh --prova-de-dente (forma por NOME, cwd = dir do script) @ 3f104ac",
     "exit": 0,
     "resultado": "INFO 'verificador re-invocado por caminho resolvido: /tmp/tre_lote12/repo/scripts/odoo/"
                  "verificar-modulo-odoo.sh'; dente 1 OK (manifesto mutado -> MODULO_ODOO_FALHOU 19 itens); "
                  "RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas) — a re-invocacao NAO morre em "
                  "'command not found' e nenhuma falha e contada como INVOCACAO"},
    {"gate": "bash verificar-modulo-odoo.sh --prova-de-dente (passada 2, banco/LOG_DIR proprios) @ 3f104ac",
     "exit": 0,
     "resultado": "saida byte-identica apos normalizacao declarada (caminho do clone, sufixo de container/rede, "
                  "timestamps); MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)"},
]
EVID_D04 = (
    "Defeito: o modo --prova-de-dente re-invocava o proprio script com \"$0\"; chamado pela forma DOCUMENTADA "
    "(`bash verificar-modulo-odoo.sh`, de dentro do diretorio do script), `$0` e um nome sem diretorio fora do "
    "PATH -> 'command not found' e a prova era acusada de 'item sem dente'. Conserto (b1cb7f7->f773d3c, "
    "consolidado em 3f104ac): `EU=\"$(readlink -f \"$0\")\"` e `bash \"$EU\"` em TODA re-invocacao; sub-run sem "
    "linha RESULTADO: passa a ser reportado como falha de INVOCACAO (contador proprio). Medido por EXECUCAO REAL "
    "na VPS (dupla descartavel propria, modulo do clone isolado), invocando pela forma por NOME: o aceite completo "
    "passa 51/0 e o modo dente fecha MODULO_ODOO_DENTE_OK (2 provas, 0 falhas) sem nenhuma 'command not found'. "
    "GAP DECLARADO: o verificador NAO tem `--autoteste` proprio; o dente e' o modo de dente embutido. "
    "NOTA: o fix esta na branch fix/TRE-W2-E03-T01-D05 (commit 3f104ac, que contem o conserto do card f773d3c); "
    "develop 8e1466d ainda NAO contem o conserto."
)

# --- 5. TRE-W2-E03-T01-D01 (item do log de teste reprovado) ----------------- #
PORT_D01 = [
    {"gate": "bash verificar-modulo-odoo.sh --prova-de-dente (dente 2: teste que falha de proposito) @ 3f104ac",
     "exit": 0,
     "resultado": "FALHOU 1 linha(s) de teste reprovado(a) no log: '2026-10-04 01:48:15,057 1 ERROR "
                  "tre_l12_d05_aceite_dente odoo.addons.transformativa_sales_ai.tests.test_modulo_base: FAIL: "
                  "TestModuloBase.test_99_prova_de_dente'; 'OK dente 2: o item do log de teste reprovado disparou "
                  "(item com dente proprio)'"},
    {"gate": "bash verificar-modulo-odoo.sh --prova-de-dente (passada 2, identica) @ 3f104ac", "exit": 0,
     "resultado": "mesmo item dispara (byte-identico apos normalizacao de timestamp/banco/caminho); o item "
                  "'nenhuma linha de teste FAIL:/ERROR:' deixa de ser codigo morto e casa o formato do Odoo 19"},
]
EVID_D01 = (
    "Defeito: o item do aceite 'nenhuma linha de teste FAIL:/ERROR: no log' imprimia OK mesmo com um teste "
    "reprovado, porque usava `grep -cE '^(FAIL|ERROR): '` (ancorado no inicio da linha) e no Odoo 19 a linha de "
    "reprovacao e' prefixada por 'data pid NIVEL banco logger:'. Conserto (ebd90fd, consolidado em 3f104ac): o "
    "item casa o formato real do Odoo 19. Medido por EXECUCAO REAL na VPS: o dente 2 planta um teste que falha "
    "de proposito e o item agora FALHOU nomeando a linha do log (1 linha de teste reprovada), com dente proprio "
    "(sem ele, o item poderia voltar ao padrao morto e o dente seguiria OK). NOTA: fix na branch "
    "fix/TRE-W2-E03-T01-D05 (contem ebd90fd); develop 8e1466d ainda NAO contem o conserto."
)

# --- 6. TRE-W2-E03-T01-D02 (logs do aceite intactos no modo dente) ---------- #
PORT_D02 = [
    {"gate": "bash verificar-modulo-odoo.sh (aceite) seguido de --prova-de-dente, MESMO TRE_LOG_DIR @ 3f104ac",
     "exit": 0,
     "resultado": "OK 'logs de passo do aceite intactos depois das provas (4 arquivo(s) com sha256 identico)' — "
                  "aceite.out + 1-instalacao/2-teste/3-desinstalacao/4-reinstalacao.log do ACEITE (banco "
                  "tre_l12_d05_aceite) preservados; o dente escreveu em '$TRE_LOG_DIR/dente/prova-N'"},
    {"gate": "bash verificar-modulo-odoo.sh (aceite) seguido de --prova-de-dente, MESMO TRE_LOG_DIR (passada 2)",
     "exit": 0,
     "resultado": "guarda D02 OK nas duas passadas; SO os logs do aceite (nao os do dente) entram no teste de "
                  "integridade; o modo de dente nao escreve um arquivo sequer no diretorio do aceite"},
]
EVID_D02 = (
    "Defeito: rodar --prova-de-dente depois de um aceite com o mesmo TRE_LOG_DIR herdao por ambiente sobrescrevia "
    "os 4 logs de passo do aceite (1-instalacao..4-reinstalacao) com a execucao mutada, apagando a evidencia bruta "
    "do aceite. Conserto (c389223, consolidado em 3f104ac): cada prova usa '$TRE_LOG_DIR/dente/prova-N' e o modo "
    "fotografa o sha256 dos [1-4]-*.log do aceite antes/depois, reprovando (fail-closed) se qualquer um mudar. "
    "Medido por EXECUCAO REAL na VPS (aceite verde 51/0 seguido do modo dente no MESMO diretorio): os 4 logs do "
    "aceite saem com sha256 identico e o item 'logs de passo do aceite intactos depois das provas' fica OK. "
    "NOTA: fix na branch fix/TRE-W2-E03-T01-D05 (contem c389223); develop 8e1466d ainda NAO contem o conserto."
)

# --- 7. TRE-W2-E03-T01-D04-D01 (ponteiro do head entregue) ------------------ #
PORT_D04D01 = [
    {"gate": "python3 scripts/verificar_ponteiros_de_registro.py @ 292baf8 (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PONTEIROS_OK (13 itens, 0 falha(s), 11 ponteiro(s) morto(s) citado(s) — 11 "
                  "marcado(s), 0 sem marca)"},
    {"gate": "python3 scripts/verificar_ponteiros_de_registro.py (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PONTEIROS_OK (13 itens, 0 falhas, 0 sem marca)"},
    {"gate": "DENTE EXTERNO (clone isolado): reverter o conserto 8827c37 (o registro volta a citar o head "
             "pre-amend c41822e)", "exit": 1,
     "resultado": "FALHA docs/operations/registro-de-execucoes.md:321/325 'c41822e ponteiro morto (commit fora "
                  "de ref nenhuma) SEM MARCA'; RESULTADO: PONTEIROS_FALHOU (14 itens, 2 falha(s)) — o dente morde "
                  "o ponteiro nomeado pelo card (c41822e -> head entregue f773d3c)"},
]
EVID_D04D01 = (
    "Defeito: o registro de execucoes citava o head `c41822e` (commit pre-amend, nao ancestral do entregue e fora "
    "de qualquer branch) em vez do head entregue `f773d3c`. Conserto (8827c37): o registro cita o head que existe "
    "de fato na branch entregue (f773d3c, blob do script byte-identico). Verificacao por EXECUCAO REAL do "
    "verificador da classe (scripts/verificar_ponteiros_de_registro.py), que resolve cada hex 7..40 contra o "
    "repositorio: PONTEIROS_OK (11 ponteiros mortos citados, TODOS marcados, 0 sem marca). Dente: reverter o "
    "conserto em clone isolado faz o verificador FALHAR nomeando c41822e (2 falhas, exit 1). NOTA: o verificador "
    "vive na branch fix/t_37db9564-verificador-ponteiros (292baf8), que CONTEM o conserto deste card (8827c37); "
    "develop 8e1466d ainda NAO contem o conserto."
)

# --- 8. DEFEITO-t_26be11c7 (errata do ponteiro f1f1cb6b) -------------------- #
PORT_T26BE = [
    {"gate": "python3 scripts/verificar_ponteiros_de_registro.py @ 292baf8 (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PONTEIROS_OK (13 itens, 0 falha(s), 11 ponteiro(s) morto(s) — 11 marcado(s), 0 "
                  "sem marca); a errata do f1f1cb6b (docs/operations/registro-de-execucoes.md:193 e a nota do "
                  "§7d do runbook de backup) marca as 5 citacoes como 'fora de ref nenhuma'"},
    {"gate": "python3 scripts/verificar_ponteiros_de_registro.py (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PONTEIROS_OK (13 itens, 0 falhas)"},
    {"gate": "DENTE EXTERNO (clone isolado): reverter a errata (4ea3d36) — as citacoes de f1f1cb6b voltam sem "
             "marca", "exit": 1,
     "resultado": "FALHA docs/operations/registro-de-execucoes.md:190/192 e backup-restore-rollback.md:192/203/"
                  "212/220/248 'f1f1cb6b ponteiro morto (commit fora de ref nenhuma) SEM MARCA'; RESULTADO: "
                  "PONTEIROS_FALHOU (10 itens, 7 falha(s)) — o dente morde exatamente o ponteiro nomeado"},
]
EVID_T26BE = (
    "Defeito (classe do D04-D01): o registro de execucoes e o runbook de backup citavam o commit de publicacao "
    "f1f1cb6b, hoje inalcancavel por ref nenhuma (amend/publicacao). Conserto (4ea3d36): errata de ponteiro que "
    "MANTEM o valor historico (registro do que foi publicado) e o MARCA explicitamente como 'fora de ref nenhuma', "
    "com ancora de digest publicado (e4e1f05d) reconferivel pelo proprio objeto. Verificacao por EXECUCAO REAL do "
    "verificador da classe: PONTEIROS_OK (0 ponteiro morto sem marca). Dente: reverter a errata em clone isolado "
    "faz o verificador FALHAR nomeando f1f1cb6b nas 7 citacoes (exit 1). NOTA: verificado na branch "
    "fix/t_37db9564-verificador-ponteiros (292baf8), que contem o conserto (4ea3d36); develop 8e1466d ainda NAO "
    "contem a errata."
)

# --- 9. TRE-W2-E04-T01-D01 (artefatos no verificador de estrutura) ---------- #
PORT_E04T01D01 = [
    {"gate": "bash scripts/verificar_estrutura.sh @ 80b64a8 (passada 1)", "exit": 0,
     "resultado": "RESULTADO: PASS (0 falhas); bloco novo cobre por DIRETORIO 'odoo/addons/transformativa_sales_ai' "
                  "+ 'scripts/odoo' (todo *.py/*.sh/*.md/*.xml/*.csv versionado), bit de execucao NO DISCO e 100755 "
                  "no INDICE, e docs/runbooks/*.md; guarda do proprio gate (lista vazia/curta reprova)"},
    {"gate": "bash scripts/verificar_estrutura.sh (passada 2, identica)", "exit": 0,
     "resultado": "saida BYTE-IDENTICA (diff cru = 0); PASS (0 falhas)"},
    {"gate": "DENTE EXTERNO (clone isolado): git rm --cached odoo/addons/transformativa_sales_ai/models/"
             "res_partner.py", "exit": 1,
     "resultado": "FALHOU nao versionado odoo/addons/transformativa_sales_ai/models/res_partner.py (arquivo existe "
                  "mas nao esta no git); RESULTADO: FALHOU (1) — o dente morde o artefato nomeado pelo card; "
                  "restaurado -> PASS (0 falhas), exit 0"},
]
EVID_E04T01D01 = (
    "Defeito: o card TRE-W2-E04-T01 registrou os 6 artefatos novos no runbook, mas o verificador de estrutura "
    "nao conhecia nenhum deles (grep = 0 no commit 3b0eac3) — o gate que reprova 'arquivo existe na VPS mas nunca "
    "entrou no repo' / 'perdeu o bit de execucao' ficava cego. Conserto (80b64a8): bloco por DIRETORIO (o que "
    "existe na arvore tem de estar no `git ls-files`), com conferencia de bit de execucao no disco e no indice do "
    "git, cobertura de docs/runbooks e guarda do proprio gate. Verificacao por EXECUCAO REAL do verificador no "
    "clone isolado: PASS (0 falhas). Dente: `git rm --cached` do models/res_partner.py faz o verificador FALHAR "
    "nomeando o arquivo (exit 1); restaurado, volta a PASS. NOTA: fix na branch fix/TRE-W2-E04-T01-D01 (80b64a8); "
    "develop 8e1466d ainda NAO contem o bloco novo."
)

# --- 10. TRE-W2-E04-T02-D01 (assinatura de dente exige o caminho saudavel) --- #
PORT_E04T02D01 = [
    {"gate": "bash verificar-crm-lead-odoo.sh --prova-de-dente @ a96e8af (passada 1)", "exit": 0,
     "resultado": "baseline CRM_LEAD_OK (43 itens, 0 falhas); dentes 1-5 OK cada um com a SUA assinatura; "
                  "RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas)"},
    {"gate": "bash verificar-crm-lead-odoo.sh --prova-de-dente (passada 2, identica)", "exit": 0,
     "resultado": "saida byte-identica apos normalizacao declarada (banco/caminho de log/sufixo de container/"
                  "timestamps); CRM_LEAD_DENTE_OK (5 provas, 0 falhas)"},
    {"gate": "CONTROLE NEGATIVO O8 (offline, funcao confere_dente do proprio artefato entregue, alimentada com a "
             "saida REAL do dente 1)", "exit": 0,
     "resultado": "(a) saida real COM 'OK odoo --init exit 0' + 'OK ir_module_module.state = installed', "
                  "exige_saude=1 -> OK; (b) MESMA saida SEM a linha saudavel, exige_saude=1 -> FALHOU 'a prova "
                  "nao instalou o modulo — queda de ambiente nao e prova de dente'; (c) mesma saida mutilada com o "
                  "codigo ANTIGO (exige_saude=0) -> OK (o falso-positivo do O8); DENTE_FALHAS=1 (so o caso-b)"},
]
EVID_E04T02D01 = (
    "Defeito (O8 da revisao independente do t_d3bd6660): a assinatura de falha do dente 1 nao era exclusiva da "
    "mutacao do proprio dente — um defeito de OUTRA classe (erro de sintaxe -> odoo --init exit 255, nenhum "
    "rename) tambem faz o campo sumir do banco e satisfazia 'campo nomeado pelo contrato AUSENTE', entao o "
    "julgador aprovava o dente sobre um modulo que nunca instalou. Conserto (a96e8af): confere_dente ganha o 5o "
    "parametro (exige o caminho SAUDAVEL) e os dentes 1-3 passam 1 — alem da assinatura, a saida tem de trazer "
    "'OK odoo --init exit 0' e 'OK ir_module_module.state = installed'; guarda fail-closed de medicao no passo 2. "
    "Verificacao por EXECUCAO REAL na VPS (dupla descartavel propria): CRM_LEAD_DENTE_OK (5 provas, 0 falhas). "
    "Dente do proprio O8 (offline, sobre a saida REAL do dente 1): a mesma saida sem a linha saudavel e' REJEITADA "
    "(a prova nao instalou o modulo), enquanto o codigo ANTIGO a aceitava — o dente morde exatamente a classe do "
    "defeito. NOTA: fix na branch fix/TRE-W2-E04-T02-D01 (a96e8af); develop 8e1466d ainda NAO contem o conserto."
)

# --------------------------------------------------------------------------- #

def _child(tid, evidence, portoes, eixo="CONTRATO/API", commit=COMMIT_W0, amb=AMB_W0):
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


def _item(idx, titulo, child):
    return {"id": idx, "title": titulo, "stage": "DONE", "current_gate": "DONE", "children": [child]}


# --- W0: 1 item NOVO (append) ---------------------------------------------- #
NOVOS_W0 = [
    _item("TRE-W0-E04-T05-D01",
          "Suite do gate JEV: expectativa impossivel no S9 (exige 6 recibos de 8 cenarios, dos quais 3 sao "
          "registros de falha)",
          _child("t_fa85fa52", EVID_T05D01, PORT_T05D01)),
]

# --- W0: 2 itens pre-existentes (read-modify-write) ------------------------ #
RMW_W0 = {
    "TRE-W0-E04-T06": {"child": "t_e9535df3", "evidence": EVID_T06, "portoes": PORT_T06},
    "TRE-W0-E04-T07": {"child": "t_f8a6e209", "evidence": EVID_T07, "portoes": PORT_T07},
}

# --- W2: 7 itens NOVOS (append), todos em branch fix/* (commit declarado) --- #
_NOVOS_W2_SPEC = [
    ("TRE-W2-E03-T01-D04", "t_025f9a2a",
     "--prova-de-dente na forma documentada falha ('$0' sem caminho) e o diagnostico dizia 'prova sem dente'",
     EVID_D04, PORT_D04, COMMIT_D05),
    ("TRE-W2-E03-T01-D01", "t_578a4e4d",
     "DEFEITO: item do aceite 'nenhuma linha de teste FAIL:/ERROR:' e codigo morto no Odoo 19",
     EVID_D01, PORT_D01, COMMIT_D05),
    ("TRE-W2-E03-T01-D02", "t_5c4fc7ac",
     "DEFEITO: --prova-de-dente herda o TRE_LOG_DIR do chamador e sobrescreve os logs do aceite",
     EVID_D02, PORT_D02, COMMIT_D05),
    ("TRE-W2-E03-T01-D04-D01", "t_52c74f31",
     "DEFEITO [retroativo]: registro de execucoes cita head c41822e (pre-amend) e nao o head entregue f773d3c",
     EVID_D04D01, PORT_D04D01, COMMIT_PTR),
    ("DEFEITO-t_26be11c7", "t_26be11c7",
     "DEFEITO [retroativo]: registro de execucoes e runbook de backup citam commit f1f1cb6b, inalcancavel por "
     "ref nenhuma (classe do D04-D01)",
     EVID_T26BE, PORT_T26BE, COMMIT_PTR),
    ("TRE-W2-E04-T01-D01", "t_5cad1689",
     "DEFEITO: artefatos do TRE-W2-E04-T01 fora do verificador de estrutura",
     EVID_E04T01D01, PORT_E04T01D01, COMMIT_E04T01D01),
    ("TRE-W2-E04-T02-D01", "t_945f96f1",
     "DEFEITO [revisao tester t_d3bd6660]: assinatura do dente 1 do verificador de crm.lead prova o SINTOMA, nao "
     "a CAUSA (O8)",
     EVID_E04T02D01, PORT_E04T02D01, COMMIT_E04T02D01),
]

NOVOS_W2 = [
    _item(idx, titulo, _child(tid, ev, pt, commit=cm, amb=AMB_W2))
    for idx, tid, titulo, ev, pt, cm in _NOVOS_W2_SPEC
]

# --- ids declarados (fail-closed de board) --------------------------------- #
IDS_W0 = [c["hermes_task_id"] for it in NOVOS_W0 for c in it["children"]] + \
         [v["child"] for v in RMW_W0.values()]
IDS_W2 = [c["hermes_task_id"] for it in NOVOS_W2 for c in it["children"]]
IDS = IDS_W0 + IDS_W2


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def ler_board() -> dict:
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


def _evento(cards, artefato: str) -> dict:
    return {
        "event": "INTEGRATED_VALIDATION_RECORDED",
        "by": VALIDADOR,
        "scope": f"lote 12 — {len(cards)} cards da fila (ordens 110..119) no artefato {artefato}",
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
    """PASS exige stage DONE (filho+item); BLOCKED exigiria VALIDATION. Qualquer misto e' erro."""
    mistos = []
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
                elif vr == "BLOCKED" and st != "VALIDATION":
                    mistos.append((p.name, it.get("id"), ch.get("hermes_task_id"),
                                   ch.get("stage"), ch.get("current_gate"), vr))
            ivr = str(it.get("validation_result") or "").upper()
            if ivr and str(it.get("stage") or "").upper() != "DONE":
                mistos.append((p.name, it.get("id"), "<item>", it.get("stage"),
                               it.get("current_gate"), ivr))
    return mistos


def _append_itens(base: dict, itens: list[dict]) -> tuple[dict, list[str]]:
    novo = json.loads(json.dumps(base))
    existentes = {it.get("id") for it in novo.get("work_items") or []}
    ja = [it["id"] for it in itens if it["id"] in existentes]
    if ja:
        raise SystemExit(f"FAIL-CLOSED: item ja' presente: {ja}")
    for it in itens:
        novo.setdefault("work_items", []).append(it)
    return novo, [it["id"] for it in itens]


def _atualizar_item(base: dict, item_id: str, spec: dict) -> tuple[dict, tuple]:
    novo = json.loads(json.dumps(base))
    alvo = next((it for it in novo.get("work_items") or [] if it.get("id") == item_id), None)
    if alvo is None:
        raise SystemExit(f"FAIL-CLOSED: item ausente para RMW: {item_id}")
    child = next((ch for ch in alvo.get("children") or []
                  if ch.get("hermes_task_id") == spec["child"]), None)
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
    return novo, (item_id, spec["child"], antes,
                  ("DONE", "DONE", "DONE", "DONE", "PASS"))


def _validar_leitor() -> None:
    if not PLUGIN_API.is_file():
        print(f"  AVISO: leitor ausente ({PLUGIN_API}) — aceite nao verificado")
        return
    spec = importlib.util.spec_from_file_location("papi", str(PLUGIN_API))
    if spec is None or spec.loader is None:
        print(f"  AVISO: leitor nao carregou ({PLUGIN_API}) — aceite nao verificado")
        return
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    b = "transformativa-revenue-engine"
    col = m._delivery_lifecycle_task_columns(board=b) or {}
    exp = m._delivery_explicit_done_task_ids(board=b) or set()
    retidos = [i for i in IDS if i in col]
    faltam = [i for i in IDS if i not in exp]
    print(f"  leitor: {len(IDS)} cards; ainda na coluna de validacao: {retidos or 'nenhum'}")
    if retidos:
        raise SystemExit(f"FAIL-CLOSED: leitor ainda retem na coluna de validacao: {retidos}")
    if faltam:
        raise SystemExit(f"FAIL-CLOSED: leitor NAO marcou como DONE explicito: {faltam}")
    print("  leitor: todos os 10 cards saem da coluna de validacao e entram em DONE explicito")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true", help="escreve (com backup datado)")
    args = p.parse_args()

    board = ler_board()
    faltando = [i for i in IDS if board.get(i) != "done"]
    if faltando:
        raise SystemExit(f"FAIL-CLOSED: card declarado nao esta `done` no board: {faltando}")

    print("=== REGISTRO DOS VEREDITOS — LOTE 12 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")

    mistos = auditar_campos()
    print(f"(AUDITORIA) artefatos vigentes com campo misto (PASS fora de DONE / BLOCKED fora de VALIDATION): "
          f"{len(mistos)}")
    for mm in mistos:
        print(f"    MISTO: {mm}")
    if mistos and args.aplicar:
        raise SystemExit("FAIL-CLOSED: ha campos mistos; normalizar antes de gravar")

    # --- W0 --- #
    base_w0 = json.loads(ARTEFATO_W0.read_text(encoding="utf-8"))
    w0, novos_w0 = _append_itens(base_w0, NOVOS_W0)
    ajustes = []
    for item_id, spec in RMW_W0.items():
        w0, aj = _atualizar_item(w0, item_id, spec)
        ajustes.append(aj)
    w0["updated_at"] = DATA
    w0["production_promotion_authorized"] = False
    w0.setdefault("events", []).append(_evento(IDS_W0, "TRE-W0-GOV"))

    # --- W2 --- #
    base_w2 = json.loads(ARTEFATO_W2.read_text(encoding="utf-8"))
    w2, novos_w2 = _append_itens(base_w2, NOVOS_W2)
    w2["updated_at"] = DATA
    w2["production_promotion_authorized"] = False
    w2.setdefault("events", []).append(_evento(IDS_W2, "TRE-W2"))

    print(f"(W0) +{len(novos_w0)} item(ns) novo(s) {novos_w0} | {len(ajustes)} por RMW")
    for it in NOVOS_W0:
        c = it["children"][0]
        print(f"     novo {it['id']:<22} {c['hermes_task_id']} -> {c['validation_result']} (item {it['stage']})")
    for item_id, tid, antes, depois in ajustes:
        print(f"     rmw  {item_id:<22} {tid}: "
              f"item=({antes[0]},{antes[1]}) child=({antes[2]},{antes[3]},vr={antes[4]}) -> {depois}")
    print(f"(W2) +{len(novos_w2)} item(ns) novo(s) {novos_w2}")
    for it in NOVOS_W2:
        c = it["children"][0]
        print(f"     novo {it['id']:<24} {c['hermes_task_id']} -> {c['validation_result']} "
              f"(commit {c['verification']['commit'][:7]})")

    if not args.aplicar:
        print("(dry-run: rode com --aplicar para escrever)")
        return 0

    gravar(ARTEFATO_W0, w0, True)
    gravar(ARTEFATO_W2, w2, True)
    print("GRAVADO")
    _validar_leitor()

    pos = auditar_campos()
    print(f"(AUDITORIA pos-gravacao) mistos: {len(pos)}")
    if pos:
        raise SystemExit(f"FAIL-CLOSED: mistos apos gravar: {pos}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
