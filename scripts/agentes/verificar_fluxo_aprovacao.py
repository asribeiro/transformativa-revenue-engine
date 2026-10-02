#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do WORKFLOW DE APROVACAO HUMANA (`aprovacao-humana-v1`, card TRE-W6-E03-T01).

Mede, SEM banco e SEM rede, o que da para medir aqui:

  A  contrato/politica/template: vocabulario do contrato lido, cobertura dos papeis de estado,
     verbo -> estado, transicoes declaradas, TTL, template com TODOS os marcadores e a recusa
     fail-closed da politica/contrato incoerente;
  B  guarda de OPERADOR: humano nomeado e autorizado aceito; ausente, nao autorizado e nome de
     maquina RECUSAM (fail-closed);
  C  o que NAO pode estar no codigo: estado do vocabulario como literal, operador autorizado,
     TTL em horas, guarda de contato copiada em vez de lida do irmao;
  D  guarda de escrita: DDL, escrita fora das duas tabelas e DELETE sem confirmacao RECUSAM;
     UPDATE em human_approvals e INSERT em agent_runs passam;
  E  notificacao: marcadores todos substituidos, sem sobra {{ }}, com codigo curto, empresa,
     contato, acao, canal, texto e os TRES comandos de decisao; sem credencial;
  F  idempotencia: hash do texto estavel, hash muda com o texto, codigo curto deterministico;
  G  expiracao: o SQL usa o ESTADO do contrato e o TTL da politica (trocar a politica muda o SQL);
  H  CLI: --planejar/--regras sem porta; prod exit 4; uso incompleto exit 2; politica recusada exit 3;
  I  contrato do componente: tabelas escritas, lacunas declaradas, "nao envia", EDITED nao e estado.

Nao entra aqui o que exige banco (fila real, decisao gravada, conflito de voto, expiracao medida,
portao do envio, desfazer): isso e o ACEITE E2E em PostgreSQL descartavel —
`scripts/agentes/teste_fluxo_aprovacao_aceite.sh`.

Uso:
  python3 scripts/agentes/verificar_fluxo_aprovacao.py [--raiz <dir>] [--modulo <py>]
      [--politica <json>] [--contrato <json>]
  python3 scripts/agentes/verificar_fluxo_aprovacao.py --autoteste

Exit: 0 = PASS (0 falhas) · 1 = FALHOU · 2 = uso/guardrail.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ITENS_OK = 0
ITENS_FALHOU = 0
FALHAS: list[str] = []

MODULO_REL = "hermes/agents/outreach/approval_workflow.py"
POLITICA_REL = "hermes/agents/outreach/politica-aprovacao-v1.json"
CONTRATO_REL = "docs/data/data_contract_v1.json"
COMPONENTE_REL = "hermes/agents/outreach/aprovacao-humana-v1.json"

ARQUIVOS_DO_CARD = (
    "approval_workflow.py", "politica-aprovacao-v1.json", "aprovacao-humana-v1.json",
    "notificacao-aprovacao-v1.md", "outreach_generator.py", "politica-outreach-v1.json",
    "gerador-abordagem-v1.json", "prompt-abordagem-v1.md",
)


def item(nome: str, esperado, obtido) -> None:
    global ITENS_OK, ITENS_FALHOU
    if esperado == obtido:
        print(f"OK     {nome} ({obtido})")
        ITENS_OK += 1
    else:
        print(f"FALHOU {nome} (esperado={esperado} obtido={obtido})")
        ITENS_FALHOU += 1
        FALHAS.append(nome)


def carregar_modulo(caminho: Path):
    spec = importlib.util.spec_from_file_location("aprovacao_sob_teste", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def descobrir_raiz_padrao() -> Path:
    atual = Path(__file__).resolve()
    for pai in atual.parents:
        if (pai / CONTRATO_REL).is_file():
            return pai
    return atual.parents[2]


# ---------------------------------------------------------------------------------------
# Pedido sintetico (mesma forma que a fila devolve; NAO e dado de producao)
# ---------------------------------------------------------------------------------------
def pedido_de_teste() -> dict:
    return {
        "id": "aaaaaaaa-1111-4111-8111-111111111111",
        "action_type": "SEND_EMAIL",
        "entity_type": "CONTACT",
        "entity_id": "bbbbbbbb-1111-4111-8111-111111111111",
        "requested_by": "hermes-dev-harness",
        "status": "PENDING",
        "requested_at": "2026-10-02T10:00:00Z",
        "decidido_em": None,
        "idade_horas": 4.5,
        "proposed_action": {
            "canal": "EMAIL", "tipo": "ABORDAGEM_INICIAL",
            "assunto": "Eficiencia operacional na Distribuidora Teste [E5]",
            "corpo": "Ola, Maria. Vi que a Distribuidora Teste tem o sinal \"Programa de eficiencia\" [E5].",
            "cta": "Conversa curta de diagnostico?",
            "fatos_citados": ["[E5]"], "evidencia_ids": ["E5"],
            "recommendation_id": "cccccccc-1111-4111-8111-111111111111",
            "organization_id": "dddddddd-1111-4111-8111-111111111111",
            "contact_id": "bbbbbbbb-1111-4111-8111-111111111111",
            "gerador_version": "gerador-abordagem-v1", "entrada_hash": "0" * 64,
            "remetente": "anderson.ribeiro@transformativa.com.br",
        },
        "decided_by": None,
        "decision_notes": None,
        "organizacao": "Distribuidora Teste",
        "organizacao_id": "dddddddd-1111-4111-8111-111111111111",
        "contato": "Maria Souza",
        "contato_email": "maria.souza@distribuidorateste.com.br",
        "contato_canal": "EMAIL",
        "do_not_contact": False,
        "opt_out_email": False,
        "opt_out_whatsapp": False,
        "recomendacao_status": "OPEN",
    }


def literais_de_texto(caminho: Path) -> set[str]:
    """Literais do codigo EXECUTAVEL (docstring fora), como TEXTO: string e numero contam."""
    fonte = caminho.read_text(encoding="utf-8")
    if fonte.count('"""') >= 2:
        fonte = fonte.split('"""', 2)[-1]
    arvore = ast.parse(fonte)
    itens = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and no.value is not None and not isinstance(no.value, bool):
            itens.add(str(no.value))
    return itens


# ---------------------------------------------------------------------------------------
# Verificacao
# ---------------------------------------------------------------------------------------
def verificar(raiz: Path, caminho_modulo: Path, caminho_politica: Path, caminho_contrato: Path) -> int:
    modulo = carregar_modulo(caminho_modulo)
    componente = json.loads((raiz / COMPONENTE_REL).read_text(encoding="utf-8"))
    contrato_bruto = json.loads(caminho_contrato.read_text(encoding="utf-8"))
    politica_bruta = json.loads(caminho_politica.read_text(encoding="utf-8"))
    literais = literais_de_texto(caminho_modulo)

    # ----------------------------------------------------------------------- A politica
    try:
        carregado = modulo.carregar_politicas(raiz, caminho_politica, caminho_contrato)
        recusa = ""
    except modulo.RecusaDePolitica as exc:
        carregado, recusa = None, str(exc)
    item("A8 politica do card carrega e valida (fail-closed)", "carregavel",
         "carregavel" if carregado else f"RECUSADO: {recusa}")
    if carregado is None:
        print("---")
        print(f"RESULTADO: FALHOU ({ITENS_OK} OK / {ITENS_FALHOU} falhas) — sem politica nao ha o que medir")
        return 1

    status_do_contrato = carregado["status_do_contrato"]
    mapa = carregado["politica"]["status"]
    item("A1 contrato declara o vocabulario de aprovacao", 4, len(status_do_contrato))
    item("A2 politica declara os 4 papeis de estado", 4, len(mapa))
    item("A3 politica nao inventa estado",
         [], sorted(set(mapa.values()) - set(status_do_contrato)))
    item("A4 politica cobre todos os estados do contrato",
         [], sorted(set(status_do_contrato) - set(mapa.values())))
    verbos = carregado["politica"]["verbos"]
    fora_do_mapa = [v for v, d in verbos.items() if d["status"] not in mapa.values()]
    item("A5 todo verbo aponta para estado do mapa", [], sorted(fora_do_mapa))
    item("A5 nenhum verbo devolve o pedido a pendente",
         [], sorted(v for v, d in verbos.items() if d["status"] == mapa["pendente"]))
    item("A6 TTL declarado e positivo", True, float(carregado["politica"]["ttl"]["horas"]) > 0)
    template = (raiz / carregado["politica"]["notificacao"]["arquivo"]).read_text(encoding="utf-8")
    faltando = [m for m in carregado["politica"]["notificacao"]["marcadores"]
                if "{{" + m + "}}" not in template]
    item("A7 template traz todos os marcadores declarados", [], faltando)
    item("A9 politica declara os tres atos (decisao/expiracao/reversao)", 3,
         len([a for a in carregado["politica"]["transicoes"] if a in modulo.ATOS_DECLARADOS]))
    item("A10 status vem do contrato, nao do codigo",
         sorted(status_do_contrato), sorted(carregado["status_do_contrato"]))

    # ----------------------------------------------------------------------- B operador
    autorizado = carregado["politica"]["operadores_autorizados"][0]

    def recusa_do_operador(nome):
        try:
            modulo.validar_operador(carregado, nome)
            return "ACEITOU"
        except modulo.RecusaDePolitica as exc:
            return str(exc).split(":")[0]

    item("B1 operador autorizado aceito", autorizado, modulo.validar_operador(carregado, autorizado))
    item("B1 operador autorizado aceito com caixa diferente", autorizado,
         modulo.validar_operador(carregado, autorizado.upper()))
    item("B2 operador ausente RECUSA", "OPERADOR_AUSENTE", recusa_do_operador(None))
    item("B2 operador vazio RECUSA", "OPERADOR_AUSENTE", recusa_do_operador("   "))
    item("B3 operador nao autorizado RECUSA", "OPERADOR_NAO_AUTORIZADO", recusa_do_operador("Ze da Silva"))
    item("B4 nome de maquina RECUSA", "OPERADOR_NAO_HUMANO", recusa_do_operador("agente-hermes"))

    # ----------------------------------------------------------------------- C o codigo
    proibidos = sorted(set(status_do_contrato) - set(modulo.STATUS_AGENT_RUNS.values()))
    item("C1 o conjunto proibido nao ficou vazio (a checagem nao e vacua)", True, len(proibidos) >= 3)
    item("C1 nenhum estado de aprovacao aparece como literal no codigo", [],
         sorted(p for p in proibidos if p in literais))
    item("C2 operador autorizado nao esta no codigo", [], sorted(
        a for a in carregado["politica"]["operadores_autorizados"] if a in literais))
    ttl = carregado["politica"]["ttl"]["horas"]
    item("C3 TTL nao esta no codigo", False, str(ttl) in literais)
    item("C4 estado e sempre lido do mapa da politica", True,
         "status_do_papel(" in caminho_modulo.read_text(encoding="utf-8"))
    item("C5 guarda de contato NAO e copiada da politica irma", False,
         bool((carregado["politica"].get("guarda_de_contato") or {}).get("copia_local")))
    item("C5 a guarda de contato e lida da politica irma (um dono so)", True,
         carregado["politica_irma"]["guarda_de_contato"]["bloqueios_absolutos"] ==
         ["do_not_contact"])

    # ----------------------------------------------------------------------- D escrita
    def guarda(sql, permitir_delete=False):
        try:
            modulo.validar_sql(sql, permitir_delete=permitir_delete)
            return "ACEITOU"
        except modulo.RecusaDeEscrita:
            return "RECUSOU"

    item("D1 DDL recusado", "RECUSOU", guarda("ALTER TABLE sales_intelligence.scores ADD COLUMN x int;"))
    item("D2 escrita fora das duas tabelas recusada", "RECUSOU",
         guarda("UPDATE sales_intelligence.recommendations SET status = 'X';"))
    item("D2 INSERT fora das duas tabelas recusado", "RECUSOU",
         guarda("INSERT INTO sales_intelligence.interactions (id) VALUES (1);"))
    item("D3 DELETE sem confirmacao recusado", "RECUSOU",
         guarda("DELETE FROM sales_intelligence.human_approvals WHERE id = 'x';"))
    item("D3 DELETE com --confirmo passa pela guarda", "ACEITOU",
         guarda("DELETE FROM sales_intelligence.human_approvals WHERE id = 'x';", True))
    item("D4 UPDATE em human_approvals aceito", "ACEITOU",
         guarda("UPDATE sales_intelligence.human_approvals SET status = 'X' WHERE id = 'y';"))
    item("D5 INSERT em agent_runs aceito", "ACEITOU",
         guarda("INSERT INTO sales_intelligence.agent_runs (id) VALUES ('z');"))
    item("D5 escrita declarada sao exatamente as duas tabelas",
         sorted([modulo.TABELA_APROVACOES, modulo.TABELA_AGENT_RUNS]),
         sorted(carregado["politica"]["guarda_de_escrita"]["tabelas"]))

    # ----------------------------------------------------------------------- E notificacao
    pedido = pedido_de_teste()
    mensagem = modulo.renderizar_notificacao(carregado, pedido, "dev")
    item("E1 notificacao sem marcador pendente", [], sorted(set(
        __import__("re").findall(r"\{\{([A-Z_]+)\}\}", mensagem))))
    item("E2 notificacao traz o codigo curto", True, modulo.codigo_curto(pedido["id"]) in mensagem)
    for rotulo, agulha in (("empresa", "Distribuidora Teste"), ("contato", "Maria Souza"),
                           ("acao", "SEND_EMAIL"), ("canal", "EMAIL"),
                           ("assunto", pedido["proposed_action"]["assunto"]),
                           ("cta", pedido["proposed_action"]["cta"])):
        item(f"E3 notificacao traz {rotulo}", True, agulha in mensagem)
    for verbo in carregado["politica"]["verbos"]:
        item(f"E4 notificacao traz o comando de {verbo}", True,
             f"--decisao {verbo}" in mensagem and pedido["id"] in mensagem)
    minuscula = mensagem.lower()
    item("E5 notificacao nao carrega credencial", False,
         ("api_key" in minuscula or "bearer" in minuscula or "senha" in minuscula))
    # E6: marcador no template SEM valor nao vira texto com {{ }} pendurado — RECUSA
    with tempfile.TemporaryDirectory(prefix="aprovacao-marcador-") as tmp:
        raiz_falsa = Path(tmp)
        (raiz_falsa / "notificacao-temp.md").write_text(
            template.replace("{{CTA}}", "{{CTA}}\n{{INEXISTENTE}}"), encoding="utf-8")
        politica_marcada = json.loads(json.dumps(carregado["politica"]))
        politica_marcada["notificacao"]["arquivo"] = "notificacao-temp.md"
        politica_marcada["notificacao"]["marcadores"] = \
            politica_marcada["notificacao"]["marcadores"] + ["INEXISTENTE"]
        propagado = dict(carregado)
        propagado["politica"] = politica_marcada
        raiz_original = modulo.RAIZ_PADRAO
        modulo.RAIZ_PADRAO = raiz_falsa
        try:
            modulo.renderizar_notificacao(propagado, pedido, "dev")
            e6 = "ACEITOU"
        except modulo.RecusaDeDecisao as exc:
            e6 = exc.motivo
        except Exception as exc:  # noqa: BLE001 — excecao nao tratada tambem e falha do item
            e6 = f"EXCECAO:{type(exc).__name__}"
        finally:
            modulo.RAIZ_PADRAO = raiz_original
        item("E6 notificacao recusa marcador declarado sem valor",
             "MARCADOR_DE_NOTIFICACAO_NAO_SUBSTITUIDO", e6)

    # ----------------------------------------------------------------------- F idempotencia
    texto = modulo.texto_do_pedido(pedido["proposed_action"])
    item("F1 hash do texto e estavel", modulo.hash_do_texto(texto), modulo.hash_do_texto(dict(texto)))
    item("F2 hash muda quando o texto muda", True,
         modulo.hash_do_texto(texto) != modulo.hash_do_texto({**texto, "assunto": texto["assunto"] + "!"}))
    item("F2 hash nao depende da ordem das chaves", modulo.hash_do_texto(texto),
         modulo.hash_do_texto({"cta": texto["cta"], "corpo": texto["corpo"], "assunto": texto["assunto"]}))
    item("F3 codigo curto e deterministico", "APR-aaaaaaaa", modulo.codigo_curto(pedido["id"]))
    auditoria = modulo.montar_auditoria(modulo.NOTIFICADO, "", "", {"a": 1}, {"b": 2}, None,
                                        "corr", "teste", "2026-10-02T10:00:00Z", "2026-10-02T10:00:01Z")
    item("F4 auditoria carrega workflow e politica do componente", modulo.WORKFLOW, auditoria["workflow"])
    item("F4 auditoria usa o vocabulario de status de agente", "COMPLETED", auditoria["status"])

    # ----------------------------------------------------------------------- G expiracao
    sql = modulo.sql_da_expiracao(carregado)
    item("G1 SQL da expiracao usa o estado expirado do contrato", True,
         f"'{mapa['expirado']}'" in sql)
    item("G2 SQL da expiracao usa o estado pendente do contrato", True, f"'{mapa['pendente']}'" in sql)
    item("G2 SQL da expiracao usa o TTL da politica", True, f"{float(ttl):g} hours" in sql)
    with tempfile.TemporaryDirectory(prefix="aprovacao-ttl-") as tmp:
        alterada = json.loads(json.dumps(politica_bruta))
        alterada["ttl"]["horas"] = 6
        caminho = Path(tmp) / "politica-aprovacao-v1.json"
        caminho.write_text(json.dumps(alterada), encoding="utf-8")
        outro = modulo.carregar_politicas(raiz, caminho, caminho_contrato)
        sql6 = modulo.sql_da_expiracao(outro)
        item("G2 trocar o TTL na politica muda o SQL (valor nao esta no codigo)", True,
             "6 hours" in sql6 and f"{float(ttl):g} hours" not in sql6)

    # ----------------------------------------------------------------------- H CLI
    def cli(*args):
        proc = subprocess.run([sys.executable, str(caminho_modulo), *args], capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    rc, saida, _ = cli("--planejar")
    item("H1 --planejar exit 0", 0, rc)
    plano = json.loads(saida)
    item("H1 --planejar declara que nao envia", False, plano["envio"]["executado"])
    item("H1 --planejar declara o operador obrigatorio", True, plano["decisao"]["humana"])
    rc, saida, _ = cli("--regras")
    item("H2 --regras exit 0", 0, rc)
    regras = json.loads(saida)
    item("H2 --regras lista os verbos da politica", sorted(verbos), sorted(regras["verbos"]))
    item("H2 --regras mostra a guarda de contato do irmao", ["do_not_contact"],
         regras["guarda_de_contato"]["bloqueios_absolutos"])
    item("H3 prod recusado (exit 4)", 4, cli("--ambiente", "prod")[0])
    item("H4 sem --ambiente e uso invalido (exit 2)", 2, cli("--decidir", "x", "--decisao", "aprovar")[0])
    item("H4 sem --prefixo e uso invalido (exit 2)", 2,
         cli("--ambiente", "dev", "--decidir", "x", "--decisao", "aprovar")[0])
    item("H4 --desfazer sem --ambiente e --prefixo e uso invalido (exit 2)", 2, cli("--desfazer", "c")[0])
    with tempfile.TemporaryDirectory(prefix="aprovacao-politica-") as tmp:
        quebrada = json.loads(json.dumps(politica_bruta))
        quebrada["operadores_autorizados"] = []
        caminho = Path(tmp) / "politica-aprovacao-v1.json"
        caminho.write_text(json.dumps(quebrada), encoding="utf-8")
        rc, _, erro = cli("--planejar", "--politica", str(caminho))
        item("H7 politica sem operador RECUSA (exit 3)", 3, rc)
        item("H7 a recusa nomeia o motivo", True, "POLITICA_INCOERENTE" in erro)

    # ----------------------------------------------------------------------- I contrato do componente
    item("I1 contrato do componente declara as tabelas escritas",
         sorted([modulo.TABELA_APROVACOES, modulo.TABELA_AGENT_RUNS]),
         sorted(componente.get("persistencia", {}).get("tabelas_escritas", [])))
    item("I2 contrato declara a lacuna do consumo exatamente-uma-vez", True,
         any("exatamente-uma-vez" in l for l in componente.get("lacunas", [])))
    item("I2 contrato declara a lacuna de coluna de auditoria", True,
         any("agent_runs" in l and "V1.1" in l for l in componente.get("lacunas", [])))
    item("I3 contrato declara que nada e enviado aqui", True,
         "TRE-W6-E04-T01" in json.dumps(componente.get("fora_do_card", [])))
    item("I3 contrato cita o fluxo do baseline (doc 06 §3)", True,
         any("06_INTEGRACOES" in f for f in componente.get("fonte_da_verdade", [])))
    item("I3 contrato cita o DDL de human_approvals (doc 04 §14)", True,
         any("04_PROJETO_FISICO" in f for f in componente.get("fonte_da_verdade", [])))
    item("I4 contrato declara que EDITED nao e estado", True,
         any("EDITED nao e estado" in d for d in componente.get("decisoes_declaradas", [])))
    item("I4 contrato aponta o vocabulario do contrato como dono", True,
         any("human_approvals.status" in f for f in componente.get("fonte_da_verdade", [])))
    item("I5 politica cita o irmao como dono unico da guarda", True,
         "DONO UNICO" in carregado["politica"]["gerador"]["papel"])

    print("---")
    print(f"RESULTADO: {'PASS' if ITENS_FALHOU == 0 else 'FALHOU'} ({ITENS_OK} OK / {ITENS_FALHOU} falhas)")
    if FALHAS:
        print("FALHAS: " + " | ".join(FALHAS))
    return 0 if ITENS_FALHOU == 0 else 1


# ---------------------------------------------------------------------------------------
# Autoteste por mutacao: a suite tem de REPROVAR o item esperado de cada mutacao
# ---------------------------------------------------------------------------------------
def preparar_arvore(destino: Path, raiz: Path) -> None:
    (destino / "hermes/agents/outreach").mkdir(parents=True, exist_ok=True)
    (destino / "docs/data").mkdir(parents=True, exist_ok=True)
    for nome in ARQUIVOS_DO_CARD:
        origem = raiz / "hermes/agents/outreach" / nome
        if origem.is_file():
            shutil.copy2(origem, destino / "hermes/agents/outreach" / nome)
    shutil.copy2(raiz / CONTRATO_REL, destino / CONTRATO_REL)


def mutar(caminho: Path, de: str, para: str) -> None:
    texto = caminho.read_text(encoding="utf-8")
    if de not in texto:
        raise SystemExit(f"autoteste: ancora nao encontrada em {caminho.name}: {de!r}")
    caminho.write_text(texto.replace(de, para, 1), encoding="utf-8")


MUTACOES = [
    # (nome, arquivo relativo, de, para, item que TEM de reprovar)
    ("D02 tira um estado do vocabulario do contrato", "docs/data/data_contract_v1.json",
     '"APPROVED", "REJECTED", "EXPIRED"]', '"REJECTED", "EXPIRED"]',
     "A8 politica do card carrega e valida (fail-closed)"),
    ("D03 renomeia a chave do vocabulario no contrato", "docs/data/data_contract_v1.json",
     '"human_approvals.status"', '"human_approvals.estado"',
     "A8 politica do card carrega e valida (fail-closed)"),
    ("D04 apaga o operador autorizado da politica", "hermes/agents/outreach/politica-aprovacao-v1.json",
     '"operadores_autorizados": ["Anderson Ribeiro"]', '"operadores_autorizados": []',
     "A8 politica do card carrega e valida (fail-closed)"),
    ("D05 verbo devolve o pedido a pendente", "hermes/agents/outreach/politica-aprovacao-v1.json",
     '"aprovar": {\n      "status": "APPROVED",', '"aprovar": {\n      "status": "PENDING",',
     "A8 politica do card carrega e valida (fail-closed)"),
    ("D06 TTL zerado na politica", "hermes/agents/outreach/politica-aprovacao-v1.json",
     '"horas": 72,', '"horas": 0,',
     "A8 politica do card carrega e valida (fail-closed)"),
    ("D07 marcador a mais na politica (template sem ele)", "hermes/agents/outreach/politica-aprovacao-v1.json",
     '"marcadores": ["CODIGO"', '"marcadores": ["INEXISTENTE", "CODIGO"',
     "A8 politica do card carrega e valida (fail-closed)"),
    ("D08 template perde um marcador obrigatorio", "hermes/agents/outreach/notificacao-aprovacao-v1.md",
     "{{CODIGO}}", "codigo",
     "A8 politica do card carrega e valida (fail-closed)"),
    ("D09 guarda de nome de maquina desligada no codigo", "hermes/agents/outreach/approval_workflow.py",
     "        if re.search(padrao, nome, re.IGNORECASE):", "        if False:",
     "B4 nome de maquina RECUSA"),
    ("D10 estado de aprovacao fixado no codigo", "hermes/agents/outreach/approval_workflow.py",
     "AMBIENTE_RECUSADO = \"prod\"", "AMBIENTE_RECUSADO = \"prod\"\nESTADO_FIXO = \"APPROVED\"",
     "C1 nenhum estado de aprovacao aparece como literal no codigo"),
    ("D11 operador autorizado fixado no codigo", "hermes/agents/outreach/approval_workflow.py",
     "AMBIENTE_RECUSADO = \"prod\"", "AMBIENTE_RECUSADO = \"prod\"\nOPERADOR_FIXO = \"Anderson Ribeiro\"",
     "C2 operador autorizado nao esta no codigo"),
    ("D12 TTL fixado no codigo", "hermes/agents/outreach/approval_workflow.py",
     "    horas = float(carregado[\"politica\"][\"ttl\"][\"horas\"])",
     "    horas = float(carregado[\"politica\"][\"ttl\"][\"horas\"])\n    horas = 72 if horas else horas",
     "C3 TTL nao esta no codigo"),
    ("D13 guarda de escrita deixa o DDL passar", "hermes/agents/outreach/approval_workflow.py",
     'if re.search(r"\\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\\b", sql, re.IGNORECASE):',
     'if False:',
     "D1 DDL recusado"),
    ("D14 guarda de escrita aceita outra tabela", "hermes/agents/outreach/approval_workflow.py",
     "        if tabela.lower() not in TABELAS_ESCRITA:", "        if False:",
     "D2 escrita fora das duas tabelas recusada"),
    ("D15 DELETE passa sem confirmacao", "hermes/agents/outreach/approval_workflow.py",
     'if verbo.startswith("DELETE") and not permitir_delete:', "if False:",
     "D3 DELETE sem confirmacao recusado"),
    ("D16 intervalo do TTL fixo no SQL", "hermes/agents/outreach/approval_workflow.py",
     "f\"WHERE status = {lit(pendente)} AND requested_at < NOW() - INTERVAL '{horas:g} hours';\"",
     "f\"WHERE status = {lit(pendente)} AND requested_at < NOW() - INTERVAL '72 hours';\"",
     "G2 trocar o TTL na politica muda o SQL (valor nao esta no codigo)"),
    ("D17 hash do texto vira nao-deterministico", "hermes/agents/outreach/approval_workflow.py",
     'return hashlib.sha256(bruto.encode("utf-8")).hexdigest()',
     'return hashlib.sha256((bruto + str(uuid.uuid4())).encode("utf-8")).hexdigest()',
     "F1 hash do texto e estavel"),
    ("D18 marcador declarado sem valor deixa de ser conferido", "hermes/agents/outreach/approval_workflow.py",
     "        if marcador not in valores:", "        if False:",
     "E6 notificacao recusa marcador declarado sem valor"),
    ("D19 contrato do componente perde as tabelas escritas",
     "hermes/agents/outreach/aprovacao-humana-v1.json",
     '"tabelas_escritas": ["sales_intelligence.human_approvals", "sales_intelligence.agent_runs"]',
     '"tabelas_escritas": ["sales_intelligence.human_approvals"]',
     "I1 contrato do componente declara as tabelas escritas"),
    ("D20 guarda de contato passa a ser copiada na politica do card",
     "hermes/agents/outreach/politica-aprovacao-v1.json",
     '"copia_local": false', '"copia_local": true',
     "C5 guarda de contato NAO e copiada da politica irma"),
]


def autoteste(raiz: Path, caminho_modulo: Path, caminho_politica: Path, caminho_contrato: Path) -> int:
    print(f"== autoteste por mutacao ({len(MUTACOES)} mutacoes)")
    com_falha = 0
    for nome, relativo, de, para, item_esperado in MUTACOES:
        with tempfile.TemporaryDirectory(prefix="aprovacao-autoteste-") as tmp:
            destino = Path(tmp)
            preparar_arvore(destino, raiz)
            mutar(destino / relativo, de, para)
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--raiz", str(destino)],
                capture_output=True, text=True)
            reprovou = f"FALHOU {item_esperado}" in proc.stdout
            if reprovou:
                print(f"OK     {nome} -> reprovou {item_esperado!r}")
            else:
                print(f"FALHOU {nome} -> NAO reprovou {item_esperado!r} (exit {proc.returncode})")
                com_falha += 1
    print("---")
    print(f"AUTOTESTE: {'PASS' if com_falha == 0 else 'FALHOU'} "
          f"({len(MUTACOES) - com_falha}/{len(MUTACOES)} mutacoes detectadas)")
    return 0 if com_falha == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suite offline do workflow de aprovacao humana (TRE-W6-E03-T01)")
    raiz_padrao = descobrir_raiz_padrao()
    parser.add_argument("--raiz", default=str(raiz_padrao))
    parser.add_argument("--modulo", default=None)
    parser.add_argument("--politica", default=None)
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve()
    caminho_modulo = Path(args.modulo).resolve() if args.modulo else raiz / MODULO_REL
    caminho_politica = Path(args.politica).resolve() if args.politica else raiz / POLITICA_REL
    caminho_contrato = Path(args.contrato).resolve() if args.contrato else raiz / CONTRATO_REL
    for caminho in (caminho_modulo, caminho_politica, caminho_contrato):
        if not caminho.is_file():
            print(f"FALHOU ausente: {caminho}", file=sys.stderr)
            return 2
    if args.autoteste:
        return autoteste(raiz, caminho_modulo, caminho_politica, caminho_contrato)
    return verificar(raiz, caminho_modulo, caminho_politica, caminho_contrato)


if __name__ == "__main__":
    sys.exit(main())
