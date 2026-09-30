#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite da APROVACAO HUMANA COM EXECUTOR — o card aprovado executa, e com rastro.

Desenho homologado: ``docs/architecture/aprovacao-humana-com-executor.md`` (Anderson
Ribeiro, 30/09/2026, decisoes 1 a 4). Camada consultada: ``hermes/jev/gate/aprovacoes.py``.
Encaixe: ``hermes/jev/gate/gate_jev.py::decidir_card`` (depois da decisao do roteador e
antes de gravar o recibo).

O que esta suite prova, item por item, SEM tocar em material de producao (board, registro
de aprovacoes, registro-de-aprovacoes.md e recibos vivem em diretorio temporario):

   1  sem arquivo de aprovacoes -> escala IGUAL a hoje, com o recibo de 13 campos exato;
   2  aprovacao valida -> PASS com ``origem: aprovacao_humana_registrada`` no recibo;
   3  aprovacao de OUTRO card -> escala;
   4  validade no passado -> escala;
   5  sem validade explicita -> escala;
   6  hash divergente (card editado depois da aprovacao) -> escala;
   7  escopo parcial (nao cobre todos os dominios da decisao) -> escala;
   8  canal telegram com ambiente alvo nao-desenvolvimento -> escala;
   9  canal telegram com dominio 'credencial' -> escala;
  10  canal telegram com dominio 'dado_de_cliente' -> escala;
  11  canal telegram em desenvolvimento com dominios comuns -> PASS;
  12  canal commit-do-aprovador com tudo coberto -> PASS;
  13  dupla entrada incompleta (card ausente do registro-de-aprovacoes) -> escala;
  14  aprovador nao autorizado (nao 'Anderson Ribeiro') -> escala;
  15  registro de aprovacoes malformado -> escala, sem excecao nao tratada;
  16  canal telegram SEM ambiente alvo declarado -> escala (ambiente nao declarado nao e
      desenvolvimento: e o ramo conservador, a mesma postura do piso de lane do roteador);
  17  o encaixe de PRODUCAO resolve modulo e caminhos pelas variaveis de ambiente
      (``JEV_APROVACOES`` / ``JEV_REGISTRO_DE_APROVACOES``) — sem injecao nenhuma;
  18  validade declarada como data-hora (datetime do YAML) vale pelo DIA dela -> PASS.

AUTOTESTE (``--autoteste``): muta uma COPIA do modulo de aprovacao e exige que a suite
reprove naquele item ESPECIFICO — mutacao que passa em silencio e buraco de verificacao.
As mutacoes cobrem: hash, dupla entrada, telegram em ambiente vivo, telegram sem ambiente
declarado (regressao do bug corrigido em 30/09/2026) e a leitura de data-hora.

Uso:  /opt/hermes/.venv/bin/python scripts/verificar_aprovacao_humana.py [--autoteste] [--manter]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[1]
GATE_PADRAO = RAIZ / "hermes/jev/gate/gate_jev.py"
APROVACOES_PADRAO = RAIZ / "hermes/jev/gate/aprovacoes.py"
APROVADOR = "Anderson Ribeiro"
ORIGEM = "aprovacao_humana_registrada"
HOJE = dt.date.today()

# Texto dos fixtures deliberadamente limpo: sem termo de dominio sensivel
# (TERMOS_DE_DOMINIO_SENSIVEL) e sem termo de DDL (PADROES_DE_DDL) — o dominio de cada
# item entra por `sinais` declarados no acoes-declaradas.yaml, que e a via do contrato.
TITULO = "Execucao generica do card de desenvolvimento"
CORPO = "Ajuste de rotina no ambiente de desenvolvimento, declarado pelo catalogo de acoes."


# ---------------------------------------------------------------------------
# Fixtures (tudo em diretorio temporario)
# ---------------------------------------------------------------------------
def hash_do_texto(titulo: str, corpo: str) -> str:
    """Receita DECLARADA do vinculo (decisao 2): sha256 de titulo+corpo normalizados.

    Reimplementada aqui DE PROPOSITO: o fixture nao pode usar a funcao do modulo sob
    teste, senao uma mutacao do vinculo passaria com o proprio fixture adaptado.
    """
    base = "\n".join(str(x or "") for x in (titulo, corpo))
    return hashlib.sha256(re.sub(r"\s+", " ", base).strip().encode("utf-8")).hexdigest()


def entrada(card_id: str, *, escopo, canal="telegram", validade=None, validade_bruta=None,
            hash_=None, aprovador=APROVADOR) -> dict:
    return {"card_id": card_id, "escopo": list(escopo), "canal": canal,
            "validade": validade, "validade_bruta": validade_bruta,
            "hash": hash_ if hash_ is not None else None, "aprovador": aprovador}


def _criar_banco(caminho: pathlib.Path, card_id: str, titulo: str, corpo: str) -> None:
    con = sqlite3.connect(caminho)
    try:
        con.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, status TEXT, "
            "assignee TEXT, priority INTEGER, block_kind TEXT, branch_name TEXT, project_id TEXT);"
            "CREATE TABLE task_links (parent_id TEXT, child_id TEXT);")
        con.execute("INSERT INTO tasks (id,title,body,status,assignee,priority) VALUES (?,?,?,?,?,?)",
                    (card_id, titulo, corpo, "ready", "default", 2))
        con.commit()
    finally:
        con.close()


def _escrever_declaracoes(caminho: pathlib.Path, card_id: str, *, acao_codigo: str,
                          sinais: dict, ambiente_alvo) -> None:
    linhas = ["versao: acoes-declaradas-v1", "declaracoes:", f"  - card_id: {card_id}",
              f"    acao_codigo: {acao_codigo}"]
    if sinais:
        linhas.append("    sinais:")
        for nome, ligado in sinais.items():
            linhas.append(f"      {nome}: {str(bool(ligado)).lower()}")
    if ambiente_alvo:
        linhas.append(f"    ambiente_alvo: {ambiente_alvo}")
    linhas += ["    declarado_por: suite da aprovacao humana",
               "    declarado_em: '2026-09-30'",
               "    motivo: fixture temporario da suite (nada de producao)"]
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def _escrever_aprovacoes(caminho: pathlib.Path, entradas: list) -> None:
    linhas = ["aprovacoes:"]
    for e in entradas:
        linhas.append(f"  - card_id: {e['card_id']}")
        linhas.append(f"    aprovador: \"{e['aprovador']}\"")
        linhas.append(f"    aprovado_em: {HOJE.isoformat()}")
        linhas.append(f"    canal: {e['canal']}")
        if e.get("validade_bruta"):
            linhas.append(f"    validade: {e['validade_bruta']}")
        elif e.get("validade"):
            linhas.append(f"    validade: {e['validade'].isoformat()}")
        linhas.append("    escopo: [" + ", ".join(e["escopo"]) + "]")
        if e.get("hash"):
            linhas.append(f"    hash: {e['hash']}")
        linhas.append("    evidencia: \"fixture temporario da suite (nenhuma aprovacao real)\"")
    if not entradas:
        linhas[0] = "aprovacoes: []"
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def texto_do_registro(cards, aprovador=APROVADOR) -> str:
    linhas = ["# Registro de aprovacoes humanas (fixture temporario da suite)", "",
              "| Data | Aprovado por | O que | Evidencia |", "|---|---|---|---|"]
    for cid in cards:
        linhas.append(f"| {HOJE.isoformat()} | {aprovador} | aprovacao do card `{cid}` | "
                      "suite da aprovacao humana, diretorio temporario |")
    return "\n".join(linhas) + "\n"


class Fixture:
    """Card + board + declaracoes + aprovacoes + registro de aprovacoes temporarios."""

    def __init__(self, raiz: pathlib.Path, nome: str, *, titulo=TITULO, corpo=CORPO,
                 card_id="t_aprov_01", sinais=None, ambiente_alvo=None,
                 acao_codigo="execucao_de_card", entradas=None, aprovacoes_bruto=None,
                 registro_cards=None, registro_texto=None, sem_aprovacoes=False,
                 sem_registro=False):
        self.dir = raiz / nome
        self.recibos = self.dir / "recibos"
        self.recibos.mkdir(parents=True, exist_ok=True)
        self.card_id = card_id
        self.titulo, self.corpo = titulo, corpo
        self.banco = self.dir / "kanban.db"
        _criar_banco(self.banco, card_id, titulo, corpo)
        self.declaracoes = self.dir / "acoes-declaradas.yaml"
        _escrever_declaracoes(self.declaracoes, card_id, acao_codigo=acao_codigo,
                              sinais=sinais or {}, ambiente_alvo=ambiente_alvo)
        self.aprovacoes = self.dir / "aprovacoes-humanas.yaml"
        self.aprovacoes_ausente = self.dir / "aprovacoes-que-nao-existe.yaml"
        if not sem_aprovacoes:
            if aprovacoes_bruto is not None:
                self.aprovacoes.write_text(aprovacoes_bruto, encoding="utf-8")
            else:
                _escrever_aprovacoes(self.aprovacoes, entradas or [])
        self.registro = self.dir / "registro-de-aprovacoes.md"
        if not sem_registro:
            self.registro.write_text(
                registro_texto if registro_texto is not None
                else texto_do_registro(registro_cards or []), encoding="utf-8")

    def card(self) -> dict:
        return {"card_id": self.card_id, "titulo": self.titulo, "corpo": self.corpo}

    def recebeu(self) -> dict:
        arquivos = sorted(self.recibos.glob("*.json"))
        if len(arquivos) != 1:
            return {"_erro": f"esperava 1 recibo, achei {len(arquivos)}: "
                             f"{[a.name for a in arquivos]}"}
        try:
            return json.loads(arquivos[0].read_text(encoding="utf-8"))
        except Exception as erro:
            return {"_erro": f"recibo ilegivel: {erro}"}


def carregar_modulo(caminho, apelido="aprovacoes") -> object:
    spec = importlib.util.spec_from_file_location(f"{apelido}_{pathlib.Path(caminho).stem}_"
                                                 f"{abs(hash(str(caminho))) % 100000}", caminho)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"nao consegui carregar {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------------------
# Contador de itens (numerado, no formato das outras suites do repo)
# ---------------------------------------------------------------------------
class Itens:
    def __init__(self, silencioso: bool = False):
        self.total = 0
        self.falhas: list = []
        self.descricoes: dict = {}
        self.silencioso = silencioso

    def checar(self, descricao: str, ok: bool, detalhe: str = "") -> bool:
        self.total += 1
        numero = self.total
        self.descricoes[numero] = descricao
        if not ok:
            self.falhas.append((numero, descricao, detalhe))
        if not self.silencioso:
            marca = "PASS  " if ok else "FALHOU"
            print(f"{marca} {numero:2d}  {descricao}" + (f"  [{detalhe}]" if detalhe else ""))
        return ok


def executar(raiz: pathlib.Path, gate, modulo, itens: Itens) -> None:
    """Roda os itens da suite. `modulo` e a camada de aprovacao SOB TESTE."""
    roteador = gate.carregar_roteador()
    contrato_do_recibo = list(roteador.campos_do_recibo(roteador.carregar_politica()))
    valida = HOJE + dt.timedelta(days=7)
    vencida = HOJE - dt.timedelta(days=1)
    h = hash_do_texto(TITULO, CORPO)

    def decidir(fx: Fixture, *, card_id=None, caminho_aprovacoes=None,
                caminho_registro=None, com_modulo=True, usar_env=False):
        """Chama o gate como o board chama; devolve o recibo junto da resposta."""
        kwargs = {"kanban_db": str(fx.banco), "declaracoes_path": str(fx.declaracoes),
                  "recibos_dir": str(fx.recibos), "roteador": roteador}
        if not usar_env:
            kwargs["aprovacoes_path"] = str(caminho_aprovacoes or fx.aprovacoes)
            kwargs["registro_aprovacoes_path"] = str(caminho_registro or fx.registro)
            if com_modulo:
                kwargs["aprovacoes_modulo"] = modulo
        resposta, recibo = {}, {}
        try:
            resposta = gate.decidir_card(card_id or fx.card_id, **kwargs)
            recibo = fx.recebeu()
        except Exception as erro:  # excecao nao tratada = falha do item
            resposta = {"_erro": f"{type(erro).__name__}: {erro}"}
            recibo = {"_erro": resposta["_erro"]}
        return resposta, recibo

    def negativo(rotulo: str, resposta: dict, recibo: dict, motivo_esperado: str,
                 dominios_esperados=None) -> tuple:
        """Escala de verdade: desfecho ESCALATE, sem execucao, com o motivo da consulta."""
        motivo = str(recibo.get("aprovacao_motivo") or "")
        partes = [
            resposta.get("outcome") == "ESCALATE",
            resposta.get("allow") is False,
            resposta.get("decidido") != "executar",
            "origem" not in recibo,
            motivo_esperado in motivo,
            gate.codigo_de_saida(resposta) == 2,
        ]
        if dominios_esperados is not None:
            partes.append(sorted(resposta.get("dominios_sensiveis") or []) == sorted(dominios_esperados))
        detalhe = (f"{rotulo}: outcome={resposta.get('outcome')} allow={resposta.get('allow')} "
                   f"motivo_da_aprovacao={motivo!r} dominios={resposta.get('dominios_sensiveis')} "
                   f"erro={resposta.get('_erro')}")
        return all(partes), detalhe

    # ---- 1. sem arquivo de aprovacoes -> escala IGUAL a hoje ----------------
    fx = Fixture(raiz, "i01", sinais={"credencial": True},
                 registro_cards=["t_aprov_01"], sem_aprovacoes=True)
    resposta, recibo = decidir(fx, caminho_aprovacoes=fx.aprovacoes_ausente)
    itens.checar(
        "sem arquivo de aprovacoes -> ESCALATE igual a hoje, recibo com os 13 campos exatos "
        "do contrato da politica (o encaixe nao muda nada)",
        (resposta.get("outcome") == "ESCALATE" and resposta.get("allow") is False
         and gate.codigo_de_saida(resposta) == 2
         and resposta.get("origem") is None
         and sorted(recibo) == sorted(contrato_do_recibo)),
        f"outcome={resposta.get('outcome')} campos={sorted(recibo)} "
        f"contrato={sorted(contrato_do_recibo)} erro={resposta.get('_erro')}")

    # ---- 2. aprovacao valida -> PASS com origem no RECIBO -------------------
    fx = Fixture(raiz, "i02", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    itens.checar(
        "aprovacao valida -> PASS com origem='aprovacao_humana_registrada' e os campos da "
        "aprovacao (aprovador, canal, validade, hash) no recibo e na resposta",
        (resposta.get("outcome") == "PASS" and resposta.get("allow") is True
         and resposta.get("decidido") == "executar" and gate.codigo_de_saida(resposta) == 0
         and recibo.get("outcome") == "PASS" and recibo.get("origem") == ORIGEM
         and recibo.get("aprovador") == APROVADOR and recibo.get("canal") == "telegram"
         and recibo.get("validade") == valida.isoformat() and recibo.get("hash") == h
         and resposta.get("origem") == ORIGEM and resposta.get("aprovador") == APROVADOR
         and ORIGEM in str(resposta.get("motivo"))),
        f"outcome={resposta.get('outcome')} allow={resposta.get('allow')} origem={recibo.get('origem')} "
        f"campos_do_recibo={sorted(recibo)} erro={resposta.get('_erro')}")

    # ---- 3. aprovacao de OUTRO card -> escala -------------------------------
    fx = Fixture(raiz, "i03", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_outro_card", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_outro_card"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("outro card", resposta, recibo, "sem aprovacao registrada para o card",
                           dominios_esperados=["producao_ou_release"])
    itens.checar("aprovacao de OUTRO card -> escala (vinculo e por card_id, nao por proximidade)",
                 ok, detalhe)

    # ---- 4. validade no passado -> escala -----------------------------------
    fx = Fixture(raiz, "i04", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"],
                                   canal="commit-do-aprovador", validade=vencida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("validade vencida", resposta, recibo, "vencida")
    itens.checar("validade no passado -> escala (aprovacao vencida volta a escalar)", ok, detalhe)

    # ---- 5. sem validade explicita -> escala --------------------------------
    fx = Fixture(raiz, "i05", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"],
                                   canal="commit-do-aprovador", validade=None, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("sem validade", resposta, recibo, "sem validade explicita")
    itens.checar("sem validade explicita -> escala (aprovacao sem prazo nao vale)", ok, detalhe)

    # ---- 6. hash divergente -> escala ---------------------------------------
    fx = Fixture(raiz, "i06", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=hash_do_texto(TITULO, CORPO + " EDITADO"))],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("hash divergente", resposta, recibo, "hash divergente")
    itens.checar("hash divergente (card editado depois da aprovacao) -> escala", ok, detalhe)

    # ---- 7. escopo parcial -> escala ---------------------------------------
    fx = Fixture(raiz, "i07", sinais={"producao": True, "credencial": True},
                 ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"],
                                   canal="commit-do-aprovador", validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("escopo parcial", resposta, recibo, "nao cobre os dominios",
                           dominios_esperados=["producao_ou_release", "credencial"])
    itens.checar("escopo parcial (aprovacao nao cobre todos os dominios da decisao) -> escala",
                 ok, detalhe)

    # ---- 8. telegram + ambiente nao-desenvolvimento -> escala ---------------
    fx = Fixture(raiz, "i08", sinais={"producao": True}, ambiente_alvo="producao",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("telegram em ambiente vivo", resposta, recibo, "commit do aprovador",
                           dominios_esperados=["producao_ou_release"])
    itens.checar("canal telegram com ambiente alvo nao-desenvolvimento -> escala "
                 "(decisao 1: telegram vale SO em desenvolvimento)", ok, detalhe)

    # ---- 9. telegram + credencial -> escala --------------------------------
    fx = Fixture(raiz, "i09", sinais={"credencial": True},
                 entradas=[entrada("t_aprov_01", escopo=["credencial"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("telegram com credencial", resposta, recibo, "commit do aprovador",
                           dominios_esperados=["credencial"])
    itens.checar("canal telegram com dominio 'credencial' -> escala", ok, detalhe)

    # ---- 10. telegram + dado_de_cliente -> escala --------------------------
    fx = Fixture(raiz, "i10", sinais={"dado_de_cliente": True},
                 entradas=[entrada("t_aprov_01", escopo=["dado_de_cliente"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("telegram com dado de cliente", resposta, recibo, "commit do aprovador",
                           dominios_esperados=["dado_de_cliente"])
    itens.checar("canal telegram com dominio 'dado_de_cliente' -> escala", ok, detalhe)

    # ---- 11. telegram + desenvolvimento + dominios comuns -> PASS ----------
    fx = Fixture(raiz, "i11", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    itens.checar("canal telegram com ambiente desenvolvimento e dominios comuns -> PASS",
                 (resposta.get("outcome") == "PASS" and resposta.get("allow") is True
                  and recibo.get("origem") == ORIGEM and recibo.get("canal") == "telegram"),
                 f"outcome={resposta.get('outcome')} origem={recibo.get('origem')} "
                 f"erro={resposta.get('_erro')}")

    # ---- 12. canal commit-do-aprovador com tudo coberto -> PASS ------------
    fx = Fixture(raiz, "i12", sinais={"producao": True, "credencial": True, "dado_de_cliente": True},
                 ambiente_alvo="producao",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release", "credencial",
                                                         "dado_de_cliente"],
                                   canal="commit-do-aprovador", validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    itens.checar("canal commit-do-aprovador com ambiente vivo e todos os dominios cobertos -> PASS",
                 (resposta.get("outcome") == "PASS" and resposta.get("allow") is True
                  and recibo.get("origem") == ORIGEM
                  and recibo.get("canal") == "commit-do-aprovador"
                  and sorted(resposta.get("dominios_sensiveis") or [])
                  == ["credencial", "dado_de_cliente", "producao_ou_release"]),
                 f"outcome={resposta.get('outcome')} origem={recibo.get('origem')} "
                 f"dominios={resposta.get('dominios_sensiveis')} erro={resposta.get('_erro')}")

    # ---- 13. dupla entrada incompleta -> escala ----------------------------
    fx = Fixture(raiz, "i13", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_outro_card"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("dupla entrada incompleta", resposta, recibo, "dupla entrada incompleta")
    itens.checar("dupla entrada incompleta (card ausente do registro-de-aprovacoes) -> escala",
                 ok, detalhe)

    # ---- 14. aprovador nao autorizado -> escala ----------------------------
    fx = Fixture(raiz, "i14", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h, aprovador="Outro Aprovador")],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("aprovador nao autorizado", resposta, recibo, "aprovador nao autorizado")
    itens.checar("aprovador nao autorizado (nao 'Anderson Ribeiro') -> escala", ok, detalhe)

    # ---- 15. registro malformado -> escala sem excecao ---------------------
    fx = Fixture(raiz, "i15", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 aprovacoes_bruto="aprovacoes:\n  - card_id: t_aprov_01\n"
                                  "    aprovador: \"Anderson Ribeiro\"\n"
                                  "    escopo: [producao_ou_release\n"
                                  "   canal: telegram\n",
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("registro malformado", resposta, recibo, "registro de aprovacoes invalido")
    itens.checar("registro de aprovacoes malformado (YAML quebrado) -> escala, sem excecao nao tratada",
                 ok and resposta.get("outcome") == "ESCALATE", detalhe)

    # ---- 16. telegram SEM ambiente declarado -> escala --------------------
    fx = Fixture(raiz, "i16", sinais={"producao": True}, ambiente_alvo=None,
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    ok, detalhe = negativo("telegram sem ambiente declarado", resposta, recibo, "commit do aprovador")
    itens.checar("canal telegram SEM ambiente alvo declarado -> escala (ausencia de declaracao e o "
                 "ramo conservador, nunca permissao)", ok, detalhe)

    # ---- 17. encaixe de producao: modulo e caminhos por variavel de ambiente
    fx = Fixture(raiz, "i17", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"], canal="telegram",
                                   validade=valida, hash_=h)],
                 registro_cards=["t_aprov_01"])
    antes = {nome: os.environ.get(nome) for nome in ("JEV_APROVACOES", "JEV_REGISTRO_DE_APROVACOES")}
    os.environ["JEV_APROVACOES"] = str(fx.aprovacoes)
    os.environ["JEV_REGISTRO_DE_APROVACOES"] = str(fx.registro)
    try:
        # Sem `aprovacoes_modulo` e sem caminhos explicitos: o gate carrega o modulo do
        # proprio diretorio e resolve os dois caminhos pelas variaveis.
        resposta, recibo = decidir(fx, com_modulo=False, usar_env=True)
    finally:
        for nome, valor in antes.items():
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = valor
    itens.checar("encaixe de producao: modulo e caminhos resolvidos por JEV_APROVACOES/"
                 "JEV_REGISTRO_DE_APROVACOES (nenhuma injecao) -> PASS com origem no recibo",
                 (resposta.get("outcome") == "PASS" and resposta.get("allow") is True
                  and recibo.get("origem") == ORIGEM),
                 f"outcome={resposta.get('outcome')} origem={recibo.get('origem')} "
                 f"erro={resposta.get('_erro')}")

    # ---- 18. validade como data-hora (datetime do YAML) -> vale pelo dia ---
    fx = Fixture(raiz, "i18", sinais={"producao": True}, ambiente_alvo="desenvolvimento",
                 entradas=[entrada("t_aprov_01", escopo=["producao_ou_release"],
                                   canal="commit-do-aprovador", validade=None,
                                   validade_bruta=f"{valida.isoformat()} 23:59:59", hash_=h)],
                 registro_cards=["t_aprov_01"])
    resposta, recibo = decidir(fx)
    itens.checar("validade declarada como data-hora (datetime do YAML) vale pelo DIA dela -> PASS "
                 "sem estourar TypeError",
                 (resposta.get("outcome") == "PASS" and resposta.get("allow") is True
                  and recibo.get("origem") == ORIGEM
                  and recibo.get("validade") == valida.isoformat()),
                 f"outcome={resposta.get('outcome')} origem={recibo.get('origem')} "
                 f"validade={recibo.get('validade')} erro={resposta.get('_erro')} "
                 f"motivo={resposta.get('aprovacao_motivo')!r}")


# ---------------------------------------------------------------------------
# AUTOTESTE: mutacoes do modulo de aprovacao, cada uma com a falha ESPECIFICA
# ---------------------------------------------------------------------------
MUTACOES = (
    {
        "nome": "tira a checagem de HASH do texto aprovado",
        "falhas_esperadas": (6,),
        "de": "    if hash_esperado != hash_atual:",
        "para": "    if False:  # mutacao: sem checagem de hash",
    },
    {
        "nome": "desliga a checagem de DUPLA ENTRADA",
        "falhas_esperadas": (13,),
        "de": "    if not _card_no_registro(card_id, aprovador, caminho_registro):",
        "para": "    if False:  # mutacao: sem dupla entrada",
    },
    {
        "nome": "permite TELEGRAM EM AMBIENTE VIVO (tira a exigencia de assinatura por ambiente)",
        "falhas_esperadas": (8, 16),
        "de": "exige_assinatura = (ambiente not in AMBIENTES_DE_DESENVOLVIMENTO) or bool(",
        "para": "exige_assinatura = bool(",
    },
    {
        "nome": "restaura o BUG do ambiente NAO declarado (telegram no ramo conservador)",
        "falhas_esperadas": (16,),
        "de": "exige_assinatura = (ambiente not in AMBIENTES_DE_DESENVOLVIMENTO) or bool(",
        "para": "exige_assinatura = (ambiente and "
                "ambiente not in AMBIENTES_DE_DESENVOLVIMENTO) or bool(",
    },
    {
        "nome": "volta a comparar DATA-HORA com data (bug do `_data`)",
        "falhas_esperadas": (18,),
        "de": ("    if isinstance(valor, _dt.datetime):\n        return valor.date()\n"
               "    if isinstance(valor, _dt.date):\n        return valor\n"),
        "para": ("    if isinstance(valor, _dt.date):\n        return valor\n"
                 "    if isinstance(valor, _dt.datetime):\n        return valor.date()\n"),
    },
)


def autoteste(raiz: pathlib.Path, gate) -> int:
    """Muta uma COPIA do modulo e exige a falha ESPECIFICA. Buraco = suite reprovada."""
    original = APROVACOES_PADRAO.read_text(encoding="utf-8")
    buracos = 0
    for numero, mutacao in enumerate(MUTACOES, start=1):
        if mutacao["de"] not in original:
            print(f"FALHOU mutacao {numero}: ancora ausente no modulo "
                  f"({mutacao['nome']})\n        ancora: {mutacao['de']!r}")
            buracos += 1
            continue
        cp = raiz / "mutacoes" / f"aprovacoes_m{numero}.py"
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(original.replace(mutacao["de"], mutacao["para"], 1), encoding="utf-8")
        modulo = carregar_modulo(cp, apelido=f"aprovacoes_m{numero}")
        itens = Itens(silencioso=True)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                executar(raiz / f"mut{numero}", gate, modulo, itens)
            except Exception as erro:
                print(f"        (a suite estourou com a mutacao: {type(erro).__name__}: {erro})")
        obtidas = tuple(sorted(numero_do_item for numero_do_item, _, _ in itens.falhas))
        detectada = obtidas == tuple(sorted(mutacao["falhas_esperadas"]))
        buracos += 0 if detectada else 1
        print(f"{'OK    ' if detectada else 'BURACO'} mutacao {numero}: {mutacao['nome']}")
        print(f"        falha esperada: itens {list(mutacao['falhas_esperadas'])}; "
              f"falha obtida: itens {list(obtidas)}")
        if not detectada:
            for numero_do_item, descricao, detalhe in itens.falhas:
                print(f"        item {numero_do_item}: {descricao} [{detalhe[:160]}]")
            cauda = buffer.getvalue().strip().splitlines()[-2:]
            for linha in cauda:
                print(f"        (saida: {linha[:160]})")
    print()
    print(f"AUTOTESTE: {len(MUTACOES) - buracos}/{len(MUTACOES)} mutacoes reprovadas")
    return buracos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Suite da aprovacao humana COM executor (desenho homologado de 30/09/2026).")
    parser.add_argument("--autoteste", action="store_true",
                        help="muta uma copia do modulo e exige a falha especifica de cada mutacao")
    parser.add_argument("--manter", action="store_true", help="nao apaga o diretorio temporario")
    args = parser.parse_args(argv)

    raiz = pathlib.Path(tempfile.mkdtemp(prefix="aprovacao-humana-"))
    print(f"diretorio da suite: {raiz}")
    print(f"gate: {GATE_PADRAO}")
    print(f"camada de aprovacao: {APROVACOES_PADRAO}")
    print()
    gate = carregar_modulo(GATE_PADRAO, apelido="gate")
    modulo = carregar_modulo(APROVACOES_PADRAO, apelido="aprovacoes")

    itens = Itens()
    codigo = 0
    try:
        executar(raiz, gate, modulo, itens)
        if itens.falhas:
            print(f"\nFALHOU ({itens.total} itens, {len(itens.falhas)} falhas)")
            for numero, descricao, detalhe in itens.falhas:
                print(f"  - item {numero}: {descricao}")
                if detalhe:
                    print(f"    {detalhe}")
            codigo = 1
        else:
            print(f"\nPASS ({itens.total} itens, 0 falhas)")
        if args.autoteste:
            print()
            print("=== AUTOTESTE: mutacoes do modulo de aprovacao, falha esperada item a item ===")
            if autoteste(raiz, gate) or codigo:
                print("RESULTADO FINAL: FALHOU (autoteste com buraco ou suite com falha)")
                codigo = 1
            else:
                print("RESULTADO FINAL: PASS (suite e autoteste OK)")
    finally:
        if args.manter:
            print(f"(mantido: {raiz})")
        else:
            shutil.rmtree(raiz, ignore_errors=True)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
