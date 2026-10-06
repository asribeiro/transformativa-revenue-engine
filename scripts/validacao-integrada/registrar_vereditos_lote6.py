#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da VALIDACAO INTEGRADA (lote 6) — cards da onda W6 (eixo 2) e o
E2E Foundation #001 (W3), que o lote 5 indicou como o proximo alvo de maior risco.

(A) W3 — TRE-W3-E06-T01 / t_fcbe3d7d ("Executar E2E Foundation #001", trio descartavel
    postgres+odoo+n8n): APPEND do work_item no artefato da onda W3 (que ja existe e nao e'
    regerado). Aceite `scripts/e2e/verificar-e2e-foundation-001.sh` por EXECUCAO REAL em dois
    cenarios completos + prova de dente.

(B) W6 (eixo 2, fila da triagem logo apos o #70): APPEND de 5 work_items no artefato da onda
    W6 (que ja existe com Titan SMTP/IMAP do lote 2) — E02 (gerador de abordagem), E03
    (workflow de aprovacao humana), E04 (envio outbound), E05 (ingestao/classificacao de
    respostas) e E07 (E2E Outbound #002). Cada um executado de verdade contra pontas
    descartaveis locais (PostgreSQL proprio, sink SMTP/IMAP, stub da API controlada).

Rito (skill validacao-de-entregas / precedentes lote1..lote5):
1. LE/ALTERA o artefato existente — nunca o regera.
2. dry-run por padrao; `--aplicar` escreve.
3. backup datado ao lado antes de escrever; escrita atomica.
4. NUNCA autoriza producao (`production_promotion_authorized` segue false).
5. fail-closed: recusa se algum card declarado nao estiver `done` no board.

Higiene (corrigida na raiz neste lote, commit anterior b1cb7f7): os descartaveis dos harnesses
sao removidos com `docker rm -f -v` (removem os volumes anonimos). Medido: 1802 volumes dangling
antes e 1802 depois desta rodada — nada novo ficou para tras.

Contrato do veredito (leitor do dashboard): trabalho em `work_items[].children[]` = {hermes_task_id,
stage, current_gate:'DONE' quando PASS, validation_result, evidence, validated_at, validated_by,
eixo_de_risco, verification:{commit, ambiente, passes_independentes:'2', portoes:[{gate, exit,
resultado}]}}.

Uso:
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote6.py
    /opt/hermes/.venv/bin/python scripts/validacao-integrada/registrar_vereditos_lote6.py --aplicar
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
ARTEFATO_W3 = DELIVERIES / "W3-integracao-odoo-pg.json"
ARTEFATO_W6 = DELIVERIES / "W6-outbound-e-canais.json"
BOARD = pathlib.Path("/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db")
PLUGIN_API = pathlib.Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")

COMMIT = "b1cb7f7051d8bf58bdfaf7fcef878437a70574fa"  # higiene de volumes (-v) + arvore verificada
DATA = "2026-10-03"
VALIDADOR = "Hermes — validacao integrada (lote 6 — E2E Foundation #001 + outbound W6)"

AMB_LOTE6 = (
    "VPS Contabo vmi3619453 (dev, root via ssh): clone isolado /tmp/tre_lote6/repo em b1cb7f7 "
    "(bundle git do develop, 14M). Cada aceite sobe containers DESCARTALVEIS proprios "
    "(postgres:16 e, no E2E Foundation, tambem odoo:19.0 + n8nio/n8n:latest), com nomes/banco/rede "
    "proprios; containers do dev (pg-sales-dev/pg-odoo-dev/odoo-dev/proxy-dev/pg-wa-probe) "
    "INTOCADOS; nada escrito em sales_intelligence do dev/producao; duas passadas por aceite "
    "(saida normalizada por nome de container/rede/pid/porta/uuid/timestamp/hash de fixture "
    "aleatoria). Higiene: os descartaveis sao removidos com 'docker rm -f -v' (commit b1cb7f7) — "
    "volumes dangling medidos em 1802 antes e 1802 depois da rodada (zero crescimento); nenhum "
    "container ficou de pe (docker ps -aq = 5, so' os do dev)."
)

# --------------------------------------------------------------------------- #
# W3 — E2E Foundation #001 (t_fcbe3d7d)
# --------------------------------------------------------------------------- #
_E2E_FOUNDATION_EVID = (
    "Aceite E2E 'Foundation #001' (doc 08 §3, passos 1..3 e 11..19 + o sentido Odoo->PG pela porta "
    "de ingestao) executado por EXECUCAO REAL num UNICO trio DESCARTALVEL proprio (postgres:16 + "
    "odoo:19.0 com o modulo transformativa_sales_ai instalado + n8nio/n8n:latest com os 4 workflows "
    "importados) — o mesmo banco, o mesmo n8n e o mesmo Odoo do primeiro ao ultimo passo. Os passos "
    "4..10 (research/scores/tier/NBA/pain) aparecem como 2 linhas DECLARADAS fora do escopo, nunca "
    "como OK: PASS aqui significa 'a fundacao fecha o caminho ponta a ponta e nao duplica', NAO o doc "
    "08 §3 inteiro. Passo 0 roda os gates do projeto (estrutura/secret/papeis/contrato) e os 4 "
    "aceites de origem em --apenas-codigo; o cenario cobre: organizacao da ACME por UUID canonico "
    "(AC2), evento COMPANY_QUALIFIED -> consumidor n8n -> POST /tf/api/v1/empresa_upsert gravando UM "
    "res.partner com tf_company_id = UUID (porta unica como unico caminho de escrita, AC3), "
    "contato_upsert e atividade_criar pela MESMA porta (AC4), trilha sync_events COMPLETED com o id "
    "do Odoo (AC5), ZERO duplicatas por REPLAY do evento e do envelope Odoo->PG (AC6), fail-closed "
    "DEAD_LETTER para evento sem event_version SEM chamar a porta (AC7), reconciliacao E04 com 0 "
    "divergencia e observabilidade E05 OK na rodada saudavel (AC8), regressao das 4 portas em "
    "--apenas-codigo no MESMO run (AC9) e ambiente descartavel com sha256 reconferido e nenhum "
    "arquivo em homolog/producao (AC10). DUAS passadas identicas (exit 0 em ambas; 139 itens, 0 "
    "falhas, 2 passos DECLARADOS fora do escopo cada; saida normalizada identica, sha256 "
    "25208afb5bdb549d3b0503f1b7ccd472de46f19794a6f2f5e9b7a241da5612d0 nas duas) e prova de dente "
    "3/3 (baseline NAO mutado verde + 3 mutacoes nomeadas do consumidor, cada uma reprovando O ITEM "
    "ESPERADO). Historico do BLOCKED: o card nasceu como alvo de maior risco do lote 5 e e' fechado "
    "aqui sem nenhum registro de BLOCKED anterior."
)

PORTOES_E2E_FOUNDATION = [
    {"gate": "bash scripts/e2e/verificar-e2e-foundation-001.sh --apenas-codigo",
     "exit": 0,
     "resultado": "RESULTADO: E2E_FOUNDATION_001_OK (9 itens, 0 falhas) — gate estrutura OK + "
                  "regressao das 4 portas (OUTBOX_CONSUMER_OK / EVENTOS_ODOO_PG_OK(28) / "
                  "RECONCILIACAO_OK(37) / OBSERVABILIDADE_SYNC_OK(3))"},
    {"gate": "bash scripts/e2e/verificar-e2e-foundation-001.sh (passada 1, cenario completo)",
     "exit": 0,
     "resultado": "RESULTADO: E2E_FOUNDATION_001_OK (139 itens, 0 falhas, 2 passo(s) declarado(s) "
                  "fora do escopo) banco=tre_e06_e2e banco_si=sales_intelligence "
                  "imagens=odoo:19.0+postgres:16+n8nio/n8n:latest"},
    {"gate": "bash scripts/e2e/verificar-e2e-foundation-001.sh (passada 2, identica)",
     "exit": 0,
     "resultado": "saida normalizada BYTE-IDENTICA a passada 1 (sha256 "
                  "25208afb5bdb549d3b0503f1b7ccd472de46f19794a6f2f5e9b7a241da5612d0 == "
                  "25208afb5bdb549d3b0503f1b7ccd472de46f19794a6f2f5e9b7a241da5612d0); RESULTADO: "
                  "E2E_FOUNDATION_001_OK (139 itens, 0 falhas)"},
    {"gate": "bash scripts/e2e/verificar-e2e-foundation-001.sh --prova-de-dente",
     "exit": 0,
     "resultado": "RESULTADO: E2E_FOUNDATION_001_DENTE_OK (3/3 dentes cumpridos; juiz conferido; "
                  "baseline nao mutado verde). DENTE_CUMPRIDO em: sem_validacao_de_envelope "
                  "(reprova 'evento sem event_version vai para DEAD_LETTER sem chamada'), "
                  "mapeamento_trocado (reprova 'o parceiro nasceu com o dominio do evento'), "
                  "sem_consulta_de_trilha (reprova 'repetir o evento NAO duplica')"},
]

# --------------------------------------------------------------------------- #
# W6 (eixo 2) — 5 cards
# --------------------------------------------------------------------------- #
def _item_e06_foundation() -> dict:
    return {
        "id": "TRE-W3-E06-T01",
        "title": "Executar E2E Foundation #001",
        "stage": "DONE",
        "current_gate": "DONE",
        "children": [
            {
                "hermes_task_id": "t_fcbe3d7d",
                "stage": "DONE",
                "current_gate": "DONE",
                "validation_result": "PASS",
                "eixo_de_risco": "INTEGRACAO",
                "evidence": _E2E_FOUNDATION_EVID,
                "validated_at": DATA,
                "validated_by": VALIDADOR,
                "verification": {
                    "commit": COMMIT,
                    "ambiente": AMB_LOTE6,
                    "passes_independentes": "2",
                    "portoes": PORTOES_E2E_FOUNDATION,
                },
            }
        ],
    }


def _item(idx, titulo, tid, evidence, portoes):
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
                    "commit": COMMIT,
                    "ambiente": AMB_LOTE6,
                    "passes_independentes": "2",
                    "portoes": portoes,
                },
            }
        ],
    }


W6_ITENS = [
    _item(
        "TRE-W6-E02-T01", "Criar GPT outreach generator", "t_297f82a1",
        (
            "Aceite E2E do GERADOR DE ABORDAGEM v1 por EXECUCAO REAL em container PostgreSQL "
            "DESCARTALVEL proprio (pg-outreach-acc) + STUB HTTP local do provedor (formato OpenAI): a "
            "abordagem legitima vira PEDIDO DE APROVACAO (human_approvals PENDING) com "
            "acao/canal/assunto/corpo/cta/citacoes/entrada_hash e a rodada auditada em agent_runs; a "
            "rodada NAO toca em nenhuma outra tabela; compliance (opt_out RECUSA, sem contato RECUSA, "
            "sem evidencia RECUSA, sem recomendacao ABSTEM, empresa inexistente RECUSA); replay JA_GERADA "
            "sem duplicar e evidencia nova gera pedido NOVO com o PENDING anterior EXPIRED; provedor "
            "chat-completions medido contra STUB (modelo/prompt versionado/evidencia no request, tokens "
            "na auditoria, numero INVENTADO RECUSA e nao grava); prod recusado (exit 4) sem escrita; "
            "--planejar sem conexao; guarda de escrita recusa DDL. DUAS passadas identicas (exit 0 em "
            "ambas; 69 OK / 0 FALHOU cada; saida normalizada identica, sha256 7d62cb12... nas duas) e "
            "prova de dente 4/4."
        ),
        [
            {"gate": "bash scripts/agentes/teste_gerador_abordagem_aceite.sh (passada 1)", "exit": 0,
             "resultado": "RESULTADO: ACEITE_OUTREACH_001_OK (69 OK / 0 FALHOU)"},
            {"gate": "bash scripts/agentes/teste_gerador_abordagem_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida normalizada identica a passada 1 (uuid/timestamp/hash de fixture aleatoria "
                          "normalizados; sha256 7d62cb12c624b5c6 == 7d62cb12c624b5c6); RESULTADO: "
                          "ACEITE_OUTREACH_001_OK (69 OK / 0 FALHOU)"},
            {"gate": "bash scripts/agentes/teste_gerador_abordagem_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (4/4 mutacoes detectadas, cada uma reprovando O ITEM ESPERADO): guarda "
                          "-> 'A6 contato com opt_out recusa'; fato -> 'A8 alucinacao RECUSA a rodada'; id "
                          "-> 'A4 replay nao duplica pedido'; supersessao -> 'A5 pedido antigo preservado e "
                          "EXPIRED'"},
        ],
    ),
    _item(
        "TRE-W6-E03-T01", "Implementar Human Approval workflow", "t_fa1c8af0",
        (
            "Aceite E2E do WORKFLOW DE APROVACAO HUMANA por EXECUCAO REAL em container PostgreSQL "
            "DESCARTALVEL proprio (pg-aprovacao-acc), com a CADEIA REAL: o gerador irmao (W6-E02) cria "
            "os pedidos PENDING e este componente os decide. Mede a fila (codigo curto/empresa/contato/"
            "acao/canal/texto/3 comandos, sem marcador pendurado), notificacao idempotente, aprovar "
            "(APPROVED + decided_by/at/notes + hash carimbado), replay = JA_DECIDIDO e voto diferente = "
            "conflito, rejeitar (nao reabre), editar (texto validado pelo gerador irmao, original "
            "preservado; fato NAO sustentado RECUSA), operador ausente/nao autorizado/maquina RECUSAM, "
            "opt-out posterior aprovacao RECUSA, EXPIRED nao aceita decisao, recomendacao fora de OPEN "
            "RECUSA, --expirar (PENDING antigo vira EXPIRED com decided_by vazio), --consultar como "
            "PORTAO, prod recusado (exit 4) e guarda de escrita (DDL/fora das duas tabelas recusada), "
            "--desfazer (dry-run x --confirmo) preservando auditoria. DUAS passadas identicas (exit 0 em "
            "ambas; 104 OK / 0 FALHOU cada; saida normalizada identica, sha256 29d67b46...) e prova de "
            "dente 4/4."
        ),
        [
            {"gate": "bash scripts/agentes/teste_fluxo_aprovacao_aceite.sh (passada 1)", "exit": 0,
             "resultado": "RESULTADO: ACEITE_APROVACAO_001_OK (104 OK / 0 FALHOU)"},
            {"gate": "bash scripts/agentes/teste_fluxo_aprovacao_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida normalizada identica a passada 1 (sha256 29d67b4652b83ae3 == "
                          "29d67b4652b83ae3); RESULTADO: ACEITE_APROVACAO_001_OK (104 OK / 0 FALHOU)"},
            {"gate": "bash scripts/agentes/teste_fluxo_aprovacao_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (4/4 mutacoes detectadas, cada uma reprovando O ITEM ESPERADO): guarda "
                          "-> 'A6 aprovar contato que virou opt-out RECUSA'; validacao -> 'A8 edicao com "
                          "fato inventado RECUSA'; transicao -> 'A11 pedido EXPIRED nao aceita aprovacao'; "
                          "replay -> 'A3 replay e JA_DECIDIDO (nada reescrito)'"},
        ],
    ),
    _item(
        "TRE-W6-E04-T01", "Implementar send workflow", "t_d24b2861",
        (
            "Aceite E2E do ENVIO OUTBOUND v1 por EXECUCAO REAL: container PostgreSQL DESCARTALVEL "
            "proprio (pg-envio-acc) + SINK SMTP local descartavel (127.0.0.1, TLS proprio, ADR-005, "
            "nenhuma credencial Titan nem destino real). O pedido nasce do gerador W6-E02 e e aprovado "
            "pelo approval_workflow W6-E03 (o portao aceita o que o irmao gravou); mede o que o envio "
            "deixou em interactions (fato canal/direcao/referencia), sync_events (claim ENVIANDO -> "
            "ENVIADO ligado a interaction, ou FALHOU), contagens das outras tabelas intocadas, "
            "idempotencia (replay = JA_ENVIADO), portao (pedido REJEITADO nao envia), prod (exit 4) e "
            "--desfazer (dry-run x --confirmo preservando o fato). DUAS passadas identicas (exit 0 em "
            "ambas; 44 itens, 0 falhas cada; saida normalizada BYTE-IDENTICA) e prova de dente 3/3."
        ),
        [
            {"gate": "bash scripts/agentes/teste_envio_outbound_aceite.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_ENVIO_OUTBOUND_001_OK (44 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_envio_outbound_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida normalizada BYTE-IDENTICA a passada 1 (sha256 6db540e56aff1130 == "
                          "6db540e56aff1130); ACEITE_ENVIO_OUTBOUND_001_OK (44 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_envio_outbound_aceite.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (3/3 mutacoes, cada uma reprovando O ITEM ESPERADO): "
                          "sem-claim-antes-do-smtp -> '4.4 UMA linha em sync_events, ENVIADO'; "
                          "sem-cta-na-mensagem -> '3.7 corpo entregue = texto APROVADO + CTA'; "
                          "ignora-o-portao -> '6.1 pedido REJEITADO nao passa no portao'"},
        ],
    ),
    _item(
        "TRE-W6-E05-T01", "Implementar reply ingestion/classification", "t_cbb01999",
        (
            "Aceite E2E da INGESTAO/CLASSIFICACAO DE RESPOSTAS por EXECUCAO REAL contra pontas reais "
            "descartaveis (ADR-005): SINK IMAP local (127.0.0.1, TLS proprio, corpus de 10 respostas) + "
            "PostgreSQL DESCARTALVEL proprio (pg-resp-acc) + o componente ingestao_respostas.py gravando "
            "em sales_intelligence. Mede: config/guardas (prod RECUSA exit 4, prefixo remoto RECUSA em "
            "dev, --ingerir sem --confirmo = DRY_RUN); leitura (uidvalidity/EXAMINE, 0 busca sem PEEK, 0 "
            "escrita, nenhuma \\Seen); classificacao de cada resposta em UMA interaction com a categoria "
            "esperada; remetente fora de contacts NAO inventa organizacao (sync_events SEM_VINCULO); "
            "escopo de escrita (so' interactions+sync_events mudam); idempotencia (replay JA_INGERIDO); "
            "e o DENTE INTERNO de citacao (descadastro que existe SO na citacao historica NAO vira "
            "OPT_OUT). Este aceite NAO expoe modo --prova-de-dente (o script so' aceita --manter); a "
            "prova de dente e' o item 12 interno, executado de verdade nas duas passadas. DUAS passadas "
            "identicas (exit 0 em ambas; 43 itens, 0 falhas cada; saida normalizada BYTE-IDENTICA)."
        ),
        [
            {"gate": "bash scripts/agentes/teste_ingestao_respostas_aceite.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE_INGESTAO_RESPOSTAS_001_OK (43 itens, 0 falhas)"},
            {"gate": "bash scripts/agentes/teste_ingestao_respostas_aceite.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida normalizada BYTE-IDENTICA a passada 1 (sha256 0e4e5070d9245142 == "
                          "0e4e5070d9245142); ACEITE_INGESTAO_RESPOSTAS_001_OK (43 itens, 0 falhas)"},
            {"gate": "dente INTERNO do aceite (script nao expoe --prova-de-dente; item 12)", "exit": 0,
             "resultado": "OK 'dente: descadastro so na citacao NAO vira OPT_OUT (obtido INDEFINIDO)' — "
                          "o remetente cujo descadastro so' aparece na citacao historica classifica "
                          "INDEFINIDO, nao OPT_OUT"},
        ],
    ),
    _item(
        "TRE-W6-E07-T01", "Executar E2E Outbound #002", "t_74517731",
        (
            "Aceite E2E OUTBOUND #002 (doc 08 §4, 10 passos) por EXECUCAO REAL num UNICO container "
            "PostgreSQL DESCARTALVEL proprio (pg-resp-e2e002) + sink SMTP + sink IMAP + stub da API "
            "controlada do Odoo, todos locais (ADR-005): lead A+ elegivel (tier W5) -> NBA (W5-E07) -> "
            "GPT cria draft (W6-E02) -> Human Approval (W6-E03) -> Titan envia pelo SINK SMTP (W6-E04) -> "
            "interaction registrada -> reply recebido pelo SINK IMAP (W6-E05) -> GPT classifica -> Odoo "
            "atualizado pelo STUB (W6-E06) -> nova NBA SUPERSEDE a anterior. `prod` e' medido nos 4 "
            "componentes e RECUSA (exit 4); nenhuma credencial Titan, nenhum destino real. DUAS passadas "
            "identicas (exit 0 em ambas; 56 OK / 0 FALHOU cada; saida normalizada identica, sha256 "
            "711694f6835fa3cc...) e prova de dente 3/3 (um dente por componente mutado)."
        ),
        [
            {"gate": "bash scripts/e2e/verificar-e2e-outbound-002.sh (passada 1)", "exit": 0,
             "resultado": "ACEITE 56 OK / 0 FALHOU; ACEITE_E2E_OUTBOUND_002_OK"},
            {"gate": "bash scripts/e2e/verificar-e2e-outbound-002.sh (passada 2, identica)", "exit": 0,
             "resultado": "saida normalizada identica a passada 1 (uuid de pedido/timestamp normalizados; "
                          "sha256 711694f6835fa3cc199e7c8edef9c6e79aa9a05421bb5e37a61b182a8cfe056f == mesma); "
                          "ACEITE 56 OK / 0 FALHOU; ACEITE_E2E_OUTBOUND_002_OK"},
            {"gate": "bash scripts/e2e/verificar-e2e-outbound-002.sh --prova-de-dente", "exit": 0,
             "resultado": "DENTE OK (3/3 mutacoes em copia de componente, cada uma reprovando O ITEM "
                          "ESPERADO): sem-cta-na-mensagem -> '5.7 o corpo entregue e o texto APROVADO + "
                          "CTA'; sem-supersessao -> '10.3 a recomendacao anterior virou SUPERSEDED e o "
                          "historico foi preservado'; primeira-regra-sempre -> '2.2 recomendacao OPEN com "
                          "acao SEND_EMAIL'"},
        ],
    ),
]


EVENTO = {
    "event": "INTEGRATED_VALIDATION_RECORDED",
    "by": VALIDADOR,
    "scope": "lote 6 — E2E Foundation #001 (W3) + outbound/canais W6 (eixo 2)",
    "production_promotion_authorized": False,
}

W6_IDS = [c["hermes_task_id"] for it in W6_ITENS for c in it["children"]]
TODOS_IDS = ["t_fcbe3d7d"] + W6_IDS


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

    print("=== REGISTRO DOS VEREDITOS — LOTE 6 —",
          "APLICANDO" if args.aplicar else "DRY-RUN (nada escrito)", "===")

    w3, n3 = _append_itens(ARTEFATO_W3, [_item_e06_foundation()])
    w6, n6 = _append_itens(ARTEFATO_W6, W6_ITENS)

    print(f"W3: {ARTEFATO_W3.name} | +{n3} item | t_fcbe3d7d -> PASS (E2E Foundation #001)")
    print(f"W6: {ARTEFATO_W6.name} | +{n6} itens | prod_autorizada={w6['production_promotion_authorized']}")
    for it in W6_ITENS:
        for c in it["children"]:
            print(f"   W6 {it['id']:<18} {c['hermes_task_id']} -> {c['validation_result']}")

    if not args.aplicar:
        print("(dry-run: rode com --aplicar para escrever)")
        return 0

    gravar(ARTEFATO_W3, w3, True)
    gravar(ARTEFATO_W6, w6, True)
    print("GRAVADO")
    _validar_leitor(TODOS_IDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
