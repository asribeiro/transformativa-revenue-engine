#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prova que REGISTRO ANOTADO como nao-perturbe/opt-out NAO TEM EXECUCAO AUTOMATICA.

Defeito [encaixe] do card `t_60fac84b` (medido em 02/10/2026), filho do conserto
`TRE-W3-E04-T03-D02` (card `t_fa344342`). O texto bruto do defeito esta no anexo
`caso-bruto-original.txt` do card (movido para anexo porque o proprio guardrail rejeita a
prosa que o descreve — bloqueio recursivo, medido).

O QUE FALTAVA
Com o sinal `empresa_do_not_contact` ligado, a unica barreira era o guardrail
`do_not_contact`, que decide por VOCABULARIO (`_e_acao_outbound`) se a acao e abordagem a
terceiro. Vocabulario tem recall finito: acao de abordagem que ele nao reconhece passava (e
o registro anotado seguia executavel), e acao de outro dominio casava entrada de abordagem
(defeito do card `t_fa344342`). Faltava a marca que a POLITICA ja declara — `guardrails`:
"empresa com do_not_contact ou opt_out nao e contatada"; fonte
`hermes/policies/human-approval.yaml`, secao `nunca_automatico`: "contatar empresa com
do_not_contact / opt_out marcado". Com o registro anotado a decisao nao pode depender de
casamento de prosa: a acao NAO EXECUTA SOZINHA (guardrail declarado "falha de guardrail
bloqueia, nao libera": duvida na avaliacao = BLOCK) e o recibo sai BLOCK com
`exige_aprovacao_humana: true`.

O QUE ESTA SUITE TRAVA (os dois lados exigidos no card)
  1. REGISTRO MARCADO -> BLOCK COM RECIBO: qualquer acao sobre registro anotado nao executa
     automaticamente; o recibo sai BLOCK, com o motivo DENTRO do recibo (campo `override`) e
     com os 13 campos do contrato — nenhum campo novo;
  2. REGISTRO LIMPO -> FLUXO NORMAL: a mesma acao sem a anotacao nao e barrada pelo guardrail
     novo (o encaixe nao virou bloqueio geral) e o guardrail de CONTATO (`do_not_contact`)
     segue acionando so por vocabulario de abordagem (o conserto do D02 nao foi desfeito);
  3. ENCAIXE ponta a ponta, pelo caminho real do board (card na tabela `tasks` + declaracao
     em `acoes-declaradas.yaml`): com a anotacao o gate NAO libera o card e grava o recibo de
     13 campos, mesmo com a aprovacao de onda que libera o card limpo; sem a anotacao o gate
     libera (PASS) e grava o recibo.
  4. COBERTURA dos achados D1/D2 da verificacao independente do card `t_2c8c5a22`, fechados no
     conserto do card `t_145eeaef`:
       D1 — com anotacao E DDL/migration declarada no proprio card (o piso por ambiente age),
       o rastro do guardrail segue no TOPO do campo `override` (nunca em
       `override.humano.registro_marcado`), e `humano` nomeia SO o override do chamador;
       D2 — em modo degradado (politica ausente) a anotacao DECIDE: BLOCK/exit 3 com
       aprovacao humana exigida, e a aprovacao de onda NAO libera o card anotado (o card
       limpo em modo degradado segue no fluxo degradado — nao ha bloqueio geral).

Uso: /opt/hermes/.venv/bin/python scripts/verificar_registro_marcado_sem_execucao_automatica.py [--autoteste]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import sqlite3
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from hermes.jev.routing import router as roteador_padrao  # noqa: E402

CAMINHO_DO_ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
CAMINHO_DO_GATE = RAIZ / "hermes/jev/gate/gate_jev.py"
CAMINHO_DA_FONTE_HUMANA = RAIZ / "hermes/policies/human-approval.yaml"
CAMINHO_DA_ANOTACAO_BRUTA = pathlib.Path(
    "/opt/data/kanban/boards/transformativa-revenue-engine/attachments/t_60fac84b/"
    "caso-bruto-original.txt")

GUARDRAIL = "registro_marcado"
SINAL = "empresa_do_not_contact"

# Acoes do caso MARCADO: a acao interna de desenvolvimento (que nao aborda ninguem), duas
# acoes de abordagem e os casos REAIS do board que o vocabulario classificava de abordagem
# (rotulo humano `outbound_para_terceiro: false` no corpus — cards `t_fa344342`/`t_60fac84b`).
ACOES = (
    ("ajuste de texto simples", "ajuste_de_texto"),
    ("enviar e-mail pelo Titan", "execucao_de_card"),
    ("enviar mensagem no WhatsApp para lead novo", "execucao_de_card"),
    ("Criar API controlada Odoo", "execucao_de_card"),
    ("Dar rollback da versao em producao", "execucao_de_card"),
    ("Publicar post em nome da Transformativa no LinkedIn", "execucao_de_card"),
)

ITENS: list[tuple[str, bool]] = []
ITENS_ANTES = 0


def item(descricao: str, ok: bool, detalhe: str = "") -> None:
    linha = f"{descricao}{(' — ' + detalhe) if detalhe else ''}"
    ITENS.append((linha, bool(ok)))
    print(f"  [{'OK' if ok else 'FALHA'}] {linha}")


# ---------------------------------------------------------------------------
# Decisao pelo roteador
# ---------------------------------------------------------------------------
def decisao(acao: str, codigo: str, marcado: bool, roteador=roteador_padrao):
    politica = roteador.carregar_politica(roteador.CAMINHO_POLITICA_PADRAO)
    tarefa = {"card_id": "t_registro_marcado", "acao": acao, "acao_codigo": codigo}
    if marcado:
        tarefa["sinais"] = {SINAL: True}
    return roteador.decidir(tarefa, politica=politica)


def acionados(resultado) -> list:
    return list(resultado["decisao"]["guardrails_acionados"])


# ---------------------------------------------------------------------------
# Encaixe ponta a ponta: board temporario + declaracoes + gate real
# ---------------------------------------------------------------------------
def _board_temporario(area: pathlib.Path, card_id: str, titulo: str) -> pathlib.Path:
    caminho = area / "kanban.db"
    conexao = sqlite3.connect(caminho)
    conexao.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, status TEXT, "
        "assignee TEXT, priority INTEGER, block_kind TEXT, branch_name TEXT, project_id TEXT)")
    conexao.execute(
        "CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    conexao.execute("INSERT INTO tasks (id, title, body, status, assignee) VALUES (?,?,?,?,?)",
                    (card_id, titulo, "corpo do card de teste do encaixe", "ready", "desenvolvedor"))
    conexao.commit()
    conexao.close()
    return caminho


def _declaracoes(area: pathlib.Path, card_id: str, marcado: bool) -> pathlib.Path:
    caminho = area / "acoes-declaradas.yaml"
    sinais = "producao: false, credencial: false"
    if marcado:
        sinais += f", {SINAL}: true"
    caminho.write_text(
        "versao: acoes-declaradas-v1\n"
        "declaracoes:\n"
        f"  - card_id: {card_id}\n"
        "    acao_codigo: execucao_de_card\n"
        "    declarado_por: suite do registro marcado\n"
        "    declarado_em: '2026-10-02'\n"
        "    motivo: caso do defeito [encaixe] do card t_60fac84b\n"
        f"    sinais: {{{sinais}}}\n"
        "    ambiente_alvo: desenvolvimento\n",
        encoding="utf-8")
    return caminho


def _gate_para(card_id: str, area: pathlib.Path, marcado: bool, gate, roteador,
               politica_path=None):
    """(resposta do gate, recibo lido do disco, caminho do recibo).

    O gate roda com o registro de aprovacoes DO REPO (variavel de ambiente nao usada aqui):
    e a aprovacao de ONDA em vigor que libera o card limpo em producao. Se ela nao puder
    liberar o card marcado, e porque a acao e proibida para execucao automatica.

    `politica_path` explicito e o caminho do MODO DEGRADADO (defeito de cobertura D2, card
    `t_145eeaef`): ausente/corrompido, o gate cai no degradado e a anotacao tem de decidir
    mesmo sem YAML.
    """
    area.mkdir(parents=True, exist_ok=True)
    banco = _board_temporario(area, card_id, "Ajustar o encaixe do roteador JEV")
    declaracoes = _declaracoes(area, card_id, marcado)
    recibos = area / "recibos"
    resposta = gate.decidir_card(
        card_id, board=None, kanban_db=str(banco), declaracoes_path=str(declaracoes),
        recibos_dir=str(recibos), roteador=roteador, politica_path=politica_path)
    recibo = json.loads(pathlib.Path(resposta["receipt_path"]).read_text(encoding="utf-8"))
    return resposta, recibo, resposta["receipt_path"]


def _gate_seguro():
    """Carrega o gate real por caminho (o roteador mutante entra por parametro)."""
    spec = importlib.util.spec_from_file_location("gate_do_registro_marcado", CAMINHO_DO_GATE)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------------------
# Itens
# ---------------------------------------------------------------------------
def checar(roteador=roteador_padrao, gate=None) -> None:
    gate = gate or _gate_seguro()
    politica = roteador.carregar_politica(roteador.CAMINHO_POLITICA_PADRAO)
    campos = list(roteador.campos_do_recibo(politica))

    # ---------------------------------------------------------- fonte da regra
    texto_guardrails = json.dumps(politica.get("guardrails") or [], ensure_ascii=False).lower()
    fonte_humana = CAMINHO_DA_FONTE_HUMANA.read_text(encoding="utf-8")
    item("a regra esta DECLARADA na politica (nada de guardrail sem fonte)",
         "do_not_contact" in texto_guardrails and "opt_out" in texto_guardrails
         and "nunca_automatico" in fonte_humana and "do_not_contact / opt_out marcado" in fonte_humana,
         "guardrails da politica + `nunca_automatico` de human-approval.yaml")
    item("o identificador do guardrail e DECLARADO no roteador (nao e string solta)",
         roteador.IDENTIFICADOR_DO_GUARDRAIL_DE_REGISTRO_MARCADO == GUARDRAIL
         and roteador.SINAL_DE_REGISTRO_MARCADO == SINAL
         and GUARDRAIL in roteador.GUARDRAILS_PROIBIDOS_PARA_EXECUCAO_AUTOMATICA)
    item("o anexo com o texto bruto do defeito esta citado (aqui e no doc §9)",
         "attachments/t_60fac84b/" in CAMINHO_DA_ANOTACAO_BRUTA.as_posix()
         and str(CAMINHO_DA_ANOTACAO_BRUTA) in (RAIZ / "docs/validation/"
                                                "jev-guardrails-e-fallback.md").read_text(
                                                    encoding="utf-8")
         and (not CAMINHO_DA_ANOTACAO_BRUTA.exists()
              or "card t_60fac84b" in CAMINHO_DA_ANOTACAO_BRUTA.read_text(encoding="utf-8")),
         str(CAMINHO_DA_ANOTACAO_BRUTA))

    # ------------------------------------------------ lado 1: registro MARCADO
    for acao, codigo in ACOES:
        resultado = decisao(acao, codigo, True, roteador)
        decisao_ = resultado["decisao"]
        recibo = resultado["recibo"]
        override = recibo.get("override") or {}
        rastro = override.get("registro_marcado") if isinstance(override, dict) else None
        item(f"marcado — BLOCK com recibo: {acao[:46]!r}…",
             decisao_["outcome"] == "BLOCK" and decisao_["decidido"] == "bloquear"
             and GUARDRAIL in acionados(resultado),
             f"outcome={decisao_['outcome']} guardrails={acionados(resultado)}")
        item(f"marcado — aprovacao humana exigida (nao executa sozinho): {acao[:46]!r}…",
             bool(decisao_["exige_aprovacao_humana"]) and not decisao_["pode_executar"],
             f"exige_aprovacao_humana={decisao_['exige_aprovacao_humana']} "
             f"pode_executar={decisao_['pode_executar']}")
        item(f"marcado — recibo de 13 campos, sem campo novo, com o motivo dentro: {acao[:46]!r}…",
             len(recibo) == 13 and list(recibo) == campos
             and recibo["outcome"] == "BLOCK"
             and isinstance(rastro, dict) and rastro.get("sinal") == SINAL
             and "proibida para execucao automatica" in str(rastro.get("motivo") or ""),
             f"campos={len(recibo)} motivo_no_recibo={bool(rastro)}")
        item(f"marcado — o motivo chega tambem em `decisao.motivos` (evento/board): {acao[:46]!r}…",
             any("registro anotado como do_not_contact/opt_out" in str(m)
                 for m in decisao_.get("motivos") or []),
             f"motivos={len(decisao_.get('motivos') or [])}")

    # ------------------------------------------------ lado 2: registro LIMPO
    for acao, codigo in ACOES:
        resultado = decisao(acao, codigo, False, roteador)
        item(f"limpo — o guardrail novo NAO aciona (nao virou bloqueio geral): {acao[:46]!r}…",
             GUARDRAIL not in acionados(resultado),
             f"guardrails={acionados(resultado)}")
        item(f"limpo — o guardrail de CONTATO tambem nao aciona sem anotacao: {acao[:46]!r}…",
             "do_not_contact" not in acionados(resultado),
             f"guardrails={acionados(resultado)}")
    interno = decisao("ajuste de texto simples", "ajuste_de_texto", False, roteador)
    item("limpo — acao interna segue o fluxo normal e EXECUTA",
         interno["decisao"]["decidido"] == "executar"
         and interno["decisao"]["outcome"] == "PASS"
         and not interno["recibo"].get("override"),
         f"decidido={interno['decisao']['decidido']} outcome={interno['decisao']['outcome']} "
         f"override={interno['recibo'].get('override')}")

    # ------------------------------- o conserto do D02 nao foi desfeito (vocabulario)
    titulo_d02 = ("DEFEITO [precisao] TRE-W3-E05-T01: CHANGELOG com contagem desatualizada, "
                  "typo no registro, contrato x codigo (linha_vazia) e robustez da lente")
    marcado_d02 = decisao(titulo_d02, "execucao_de_card", True, roteador)
    item("o guardrail de contato segue decidindo por VOCABULARIO (o sinal sozinho nao o liga)",
         "do_not_contact" not in acionados(marcado_d02) and GUARDRAIL in acionados(marcado_d02),
         f"guardrails={acionados(marcado_d02)}")
    abordagem = decisao("enviar e-mail pelo Titan", "execucao_de_card", True, roteador)
    item("a acao de abordagem aciona OS DOIS guardrails (contato + registro marcado)",
         "do_not_contact" in acionados(abordagem) and GUARDRAIL in acionados(abordagem),
         f"guardrails={acionados(abordagem)}")

    # ------------------------------------------------------- encaixe ponta a ponta
    with tempfile.TemporaryDirectory(prefix="jev-registro-marcado-") as temporario:
        area = pathlib.Path(temporario)
        limpa, recibo_limpo, _ = _gate_para("t_limpo", area / "limpo", False, gate, roteador)
        marcada, recibo_marcado, caminho_marcado = _gate_para(
            "t_marcado", area / "marcado", True, gate, roteador)
        saida_limpa = gate.codigo_de_saida(limpa)
        saida_marcada = gate.codigo_de_saida(marcada)
        item("encaixe — card LIMPO: o gate libera (allow) e grava recibo de 13 campos",
             bool(limpa["allow"]) and saida_limpa == 0 and len(recibo_limpo) == 13,
             f"allow={limpa['allow']} exit={saida_limpa} outcome={limpa['outcome']}")
        item("encaixe — card LIMPO liberado pela aprovacao de ONDA em vigor "
             "(o caminho automatico de liberacao estava ativo na medicao)",
             recibo_limpo["outcome"] == "PASS"
             and bool((recibo_limpo.get("override") or {}).get("aprovacao_humana")
                      or (recibo_limpo.get("override") or {}).get("humano"))
             and not (recibo_limpo.get("override") or {}).get("registro_marcado"),
             f"override={list((recibo_limpo.get('override') or {}))}")
        item("encaixe — a MESMA aprovacao de onda NAO libera o card MARCADO "
             "(a acao e proibida para execucao automatica)",
             not marcada["allow"] and marcada["outcome"] == "BLOCK",
             f"allow={marcada['allow']} outcome={marcada['outcome']}")
        item("encaixe — card MARCADO: o gate NAO libera (BLOCK, exit 3) com recibo de 13 campos",
             not marcada["allow"] and saida_marcada == 3
             and marcada["outcome"] == "BLOCK" and len(recibo_marcado) == 13,
             f"allow={marcada['allow']} exit={saida_marcada} outcome={marcada['outcome']}")
        item("encaixe — card MARCADO: aprovacao humana exigida na resposta do gate",
             bool(marcada["exige_aprovacao_humana"])
             and GUARDRAIL in list(marcada["guardrails_acionados"] or []),
             f"exige_aprovacao_humana={marcada['exige_aprovacao_humana']} "
             f"guardrails={marcada['guardrails_acionados']}")
        rastro_marcado = (recibo_marcado.get("override") or {}).get("registro_marcado") or {}
        item("encaixe — o motivo sai DENTRO do recibo gravado (campo `override`)",
             "proibida para execucao automatica" in str(rastro_marcado.get("motivo") or "")
             and list(recibo_marcado) == campos,
             f"receipt_path={caminho_marcado}")
        item("encaixe — o recibo do card marcado NAO carrega segredo",
             not any(t in json.dumps(recibo_marcado, ensure_ascii=False)
                     for t in ("sk-", "senha:", "SENHA=")))

    # ------------------------- D1: anotacao + piso por ambiente no MESMO card (t_145eeaef)
    # Card anotado E card que DECLARA DDL/migration no proprio texto: o piso por ambiente
    # tambem age. O rastro do guardrail tem de continuar no TOPO do campo `override` — o
    # piso embrulha SO o override do CHAMADOR sob `humano` (defeito de cobertura D1 do
    # card `t_145eeaef`: o rastro saia em `override.humano.registro_marcado`, caminho que
    # o card, o doc §9 e esta suite leem).
    def _d1(override_do_chamador=None):
        tarefa = {"card_id": "t_d1_marcado", "acao": "ajuste de texto simples",
                  "acao_codigo": "ajuste_de_texto", "ambiente_alvo": "desenvolvimento",
                  "descricao": "aplicar migracao de esquema no CRM",
                  "sinais": {SINAL: True}}
        if override_do_chamador is not None:
            tarefa["override"] = override_do_chamador
        return roteador.decidir(tarefa, politica=politica)

    d1 = _d1()
    recibo_d1 = d1["recibo"]
    override_d1 = recibo_d1.get("override") or {}
    rastro_d1 = override_d1.get("registro_marcado") if isinstance(override_d1, dict) else None
    item("D1 — anotacao + card que DECLARA DDL/migration (o piso por ambiente age): o rastro "
         "do guardrail segue no TOPO do campo `override`, com o piso ao lado",
         d1["decisao"]["outcome"] == "BLOCK"
         and (d1["decisao"].get("piso_de_lane") or {}).get("ramo") == "novo_ou_dev"
         and isinstance(rastro_d1, dict) and bool(rastro_d1.get("motivo"))
         and isinstance(override_d1.get("piso_por_ambiente"), dict)
         and "humano" not in override_d1
         and len(recibo_d1) == 13 and list(recibo_d1) == campos,
         f"chaves de override={list(override_d1)}")
    declarado_d1 = {"por": "anderson", "motivo": "revisao manual"}
    d1b = _d1(declarado_d1)
    override_d1b = d1b["recibo"].get("override") or {}
    item("D1 — `humano` nomeia SO o override do chamador; o rastro de guardrail e IRMAO "
         "dele no topo (nunca embrulhado sob `humano`)",
         override_d1b.get("humano") == declarado_d1
         and isinstance(override_d1b.get("registro_marcado"), dict)
         and isinstance(override_d1b.get("piso_por_ambiente"), dict),
         f"override={json.dumps(override_d1b, ensure_ascii=False)[:150]}")

    # -------------------- D2: modo degradado (politica ausente) com card ANOTADO (t_145eeaef)
    # O guardrail de registro marcado e CODIGO: sem politica carregavel ele tem de decidir
    # mesmo assim. Antes ele vivia na camada da politica, nao existia no degradado e a
    # aprovacao de onda liberava o card ANOTADO — identico a um card limpo.
    with tempfile.TemporaryDirectory(prefix="jev-registro-degradado-") as temporario:
        area = pathlib.Path(temporario)
        politica_ausente = str(area / "politica-que-nao-existe.yaml")
        degradado_marcada, recibo_degradado_marcado, _ = _gate_para(
            "t_degradado_marcado", area / "marcado", True, gate, roteador,
            politica_path=politica_ausente)
        degradado_limpa, _recibo_degradado_limpo, _ = _gate_para(
            "t_degradado_limpo", area / "limpo", False, gate, roteador,
            politica_path=politica_ausente)
        saida_degradada = gate.codigo_de_saida(degradado_marcada)
        item("D2 — politica AUSENTE (modo degradado) + card ANOTADO: a anotacao decide "
             "(BLOCK, exit 3, aprovacao humana exigida) e a aprovacao de onda NAO libera",
             not degradado_marcada["allow"] and degradado_marcada["outcome"] == "BLOCK"
             and saida_degradada == 3
             and bool(degradado_marcada["exige_aprovacao_humana"])
             and GUARDRAIL in list(degradado_marcada["guardrails_acionados"] or [])
             and len(recibo_degradado_marcado) == 13
             and list(recibo_degradado_marcado) == campos,
             f"allow={degradado_marcada['allow']} exit={saida_degradada} "
             f"outcome={degradado_marcada['outcome']} "
             f"guardrails={degradado_marcada['guardrails_acionados']}")
        item("D2 — politica AUSENTE + card LIMPO: segue o fluxo degradado e a aprovacao de "
             "onda libera (a correcao do D2 nao virou bloqueio geral)",
             bool(degradado_limpa["allow"]) and degradado_limpa["outcome"] == "PASS"
             and gate.codigo_de_saida(degradado_limpa) == 0
             and GUARDRAIL not in list(degradado_limpa["guardrails_acionados"] or []),
             f"allow={degradado_limpa['allow']} exit={gate.codigo_de_saida(degradado_limpa)} "
             f"guardrails={degradado_limpa['guardrails_acionados']}")


# ---------------------------------------------------------------------------
# Autoteste por mutacao: cada protecao removida tem de reprovar a suite
# ---------------------------------------------------------------------------
def _carregar_modulo(caminho: pathlib.Path, nome: str):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _copia_mutada(origem: pathlib.Path, destino: pathlib.Path, antigo: str, novo: str) -> None:
    texto = origem.read_text(encoding="utf-8")
    if antigo not in texto:
        raise RuntimeError(f"ancora ausente em {origem.name}: {antigo!r}")
    destino.write_text(texto.replace(antigo, novo, 1), encoding="utf-8")


def mutacoes() -> list:
    """(nome, arquivo, antigo, novo). A mutacao REMOVE a protecao."""
    return [
        ("guardrail de registro marcado neutralizado (registro anotado executa)",
         CAMINHO_DO_ROTEADOR,
         '    registro_marcado = bool(sinais.get(SINAL_DE_REGISTRO_MARCADO))',
         '    registro_marcado = False'),
        ("guardrail de registro marcado sempre aciona (bloqueia registro limpo)",
         CAMINHO_DO_ROTEADOR,
         '    registro_marcado = bool(sinais.get(SINAL_DE_REGISTRO_MARCADO))',
         '    registro_marcado = True'),
        ("a exigencia de aprovacao humana sai do bloqueio (bloqueia, mas a maquina decide)",
         CAMINHO_DO_ROTEADOR,
         '    plano["exige_aprovacao_humana"] = True\n'
         '    plano["exige_escalacao"] = True\n'
         '    registro = {"guardrails"',
         '    plano["exige_aprovacao_humana"] = False\n'
         '    plano["exige_escalacao"] = True\n'
         '    registro = {"guardrails"'),
        ("o motivo sai do recibo (bloqueio sem causa registrada no recibo)",
         CAMINHO_DO_ROTEADOR,
         '        plano["override"] = {"registro_marcado": registro}\n',
         '        plano["override"] = None\n'),
        ("o encaixe ignora a anotacao do registro (gate nao passa o sinal)",
         CAMINHO_DO_GATE,
         '        sinais = declaracao.get("sinais")\n'
         '        if isinstance(sinais, dict) and sinais:\n'
         '            tarefa["sinais"] = dict(sinais)\n',
         '        sinais = None\n'
         '        if False:\n'
         '            tarefa["sinais"] = dict(sinais)\n'),
        ("D1: o piso por ambiente volta a embrulhar o rastro de guardrail sob `humano` "
         "(o rastro sai do topo quando anotacao e piso agem no mesmo card)",
         CAMINHO_DO_ROTEADOR,
         '    plano["override"] = _compor_override_do_recibo('
         'plano, {"piso_por_ambiente": registro})',
         '    humano = plano.get("override")\n'
         '    if isinstance(humano, dict) and humano:\n'
         '        plano["override"] = {"humano": humano, "piso_por_ambiente": registro}\n'
         '    else:\n'
         '        plano["override"] = {"piso_por_ambiente": registro}'),
        ("D2: guardrail de codigo deixa de rodar em modo degradado (a anotacao volta a nao "
         "decidir sem politica e a aprovacao de onda libera o card anotado)",
         CAMINHO_DO_ROTEADOR,
         '    guardrails = list(_guardrails_de_codigo(tarefa))',
         '    guardrails = [] if politica is None else list(_guardrails_de_codigo(tarefa))'),
    ]


def autoteste() -> int:
    detectadas = 0
    entradas = mutacoes()
    for indice, (nome, arquivo, antigo, novo) in enumerate(entradas, start=1):
        with tempfile.TemporaryDirectory(prefix=f"jev-registro-mut-{indice}-") as temporario:
            area = pathlib.Path(temporario)
            destino = area / arquivo.name
            try:
                _copia_mutada(arquivo, destino, antigo, novo)
            except RuntimeError as erro:
                print(f"  [BURACO] mutacao nao aplicavel: {nome} — {erro}")
                continue
            roteador_mutado, gate_mutado = roteador_padrao, None
            if arquivo == CAMINHO_DO_ROTEADOR:
                roteador_mutado = _carregar_modulo(destino, f"router_mut_{indice}")
            else:
                gate_mutado = _carregar_modulo(destino, f"gate_mut_{indice}")
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    checar(roteador=roteador_mutado, gate=gate_mutado)
                reprovou = "FALHA" in buffer.getvalue()
            except Exception:
                reprovou = True  # mutacao que quebra a suite tambem conta como detectada
            finally:
                del ITENS[-ITENS_ANTES:]
            detectadas += 1 if reprovou else 0
            print(f"  [{'OK' if reprovou else 'BURACO'}] mutacao reprovada: {nome}")
    total = len(entradas)
    print(f"AUTOTESTE: {detectadas}/{total} mutacoes reprovadas")
    return 0 if detectadas == total else 1


def main() -> int:
    global ITENS_ANTES
    parser = argparse.ArgumentParser()
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    checar()
    falhas = [d for d, ok in ITENS if not ok]
    print(f"RESULTADO: {'PASS' if not falhas else 'FALHOU'} ({len(ITENS)} itens, "
          f"{len(falhas)} falha(s))")
    for d in falhas:
        print(f"  -> {d}")
    if args.autoteste:
        ITENS_ANTES = len(ITENS)
        if autoteste():
            print("RESULTADO FINAL: FALHOU (autoteste com buraco)")
            return 1
        print("RESULTADO FINAL: PASS (autoteste OK)")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
