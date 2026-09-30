#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aprovacao humana COM executor — consulta do registro declarado.

Desenho: ``docs/architecture/aprovacao-humana-com-executor.md`` (homologado por Anderson
Ribeiro em 30/09/2026, decisoes 1 a 4).

Regra: o roteador continua decidindo como sempre (fail-closed). Esta camada responde UMA
pergunta: *existe aprovacao registrada, valida, com o hash do texto aprovado e que cobre os
dominios desta decisao?* Se sim, a decisao escalada passa a executar COM RASTRO
(``origem: aprovacao_humana_registrada`` no recibo). Se nao, nada muda.

Decisoes homologadas que esta camada implementa:

1. canal (indecisao 1 + 4): ``canal: telegram`` vale para execucao em desenvolvimento e
   dominios que NAO sejam credencial nem dado de cliente; qualquer outro caso exige
   ``canal: commit-do-aprovador`` (commit do proprio aprovador, fora do alcance do agente).
   Ambiente NAO declarado nao e desenvolvimento: vale o ramo conservador, como no piso de
   lane do roteador (correcao medida em 30/09/2026);
2. vinculo por HASH do titulo+corpo do card: editar o card depois de aprovado invalida;
3. validade explicita: sem ``validade`` o registro nao vale; vencido, nao vale (padrao 7 dias
   na hora de conceder — a consulta so verifica);
4. dupla entrada: a aprovacao so vale se o card constar tambem do
   ``docs/operations/registro-de-aprovacoes.md`` com o mesmo aprovador.

Nunca "aprova por presuncao": qualquer duvida (arquivo malformado, campos faltando, hash
divergente, validade ilegivel, cobertura parcial) devolve NAO APLICAVEL com o motivo.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[3]
CAMINHO_PADRAO = RAIZ / "hermes" / "jev" / "aprovacoes-humanas.yaml"
CAMINHO_DO_REGISTRO = RAIZ / "docs" / "operations" / "registro-de-aprovacoes.md"
APROVADOR_AUTORIZADO = "Anderson Ribeiro"
DOMINIOS_COM_ASSINATURA = ("credencial", "dado_de_cliente")
AMBIENTES_DE_DESENVOLVIMENTO = ("desenvolvimento", "dev", "local", "teste", "test")


def _yaml():
    import yaml  # import tardio: o gate roda em ambiente minimo

    return yaml


def hash_do_card(titulo: str, corpo: str) -> str:
    """Hash do texto APROVADO (titulo+corpo normalizados). Edicao posterior invalida."""
    base = "\n".join(str(x or "") for x in (titulo, corpo))
    normalizado = re.sub(r"\s+", " ", base).strip()
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


def carregar(caminho=None) -> dict:
    """Mapa card_id -> entrada. Arquivo ausente = sem aprovacao (nao erro)."""
    alvo = pathlib.Path(caminho) if caminho else CAMINHO_PADRAO
    if not alvo.is_file():
        return {}
    dados = _yaml().safe_load(alvo.read_text(encoding="utf-8")) or {}
    entradas = dados.get("aprovacoes") or []
    if not isinstance(entradas, list):
        raise ValueError("aprovacoes deve ser lista")
    mapa = {}
    for entrada in entradas:
        if not isinstance(entrada, dict):
            raise ValueError(f"entrada fora do contrato: {entrada!r}")
        cid = str(entrada.get("card_id") or "").strip()
        if not cid:
            raise ValueError(f"entrada sem card_id: {entrada!r}")
        mapa[cid] = dict(entrada)
    return mapa


def _data(valor):
    # `datetime` ANTES de `date`: por heranca, todo datetime E um date, e a ordem
    # invertida devolvia o proprio datetime de uma validade declarada como data-hora
    # (`validade: 2026-10-07 23:59:59` no YAML). A comparacao com o dia corrente entao
    # estourava `TypeError: '<' not supported between instances of 'datetime.datetime'
    # and 'datetime.date'` e a aprovacao caia por FALHA da consulta, nao por regra —
    # medido em 30/09/2026 e corrigido aqui.
    if isinstance(valor, _dt.datetime):
        return valor.date()
    if isinstance(valor, _dt.date):
        return valor
    try:
        return _dt.date.fromisoformat(str(valor).strip())
    except Exception:
        return None


def _card_no_registro(card_id: str, aprovador: str, caminho=None) -> bool:
    """Dupla entrada: o card (e o aprovador) tambem constam do registro de aprovacoes."""
    alvo = pathlib.Path(caminho) if caminho else CAMINHO_DO_REGISTRO
    if not alvo.is_file():
        return False
    texto = alvo.read_text(encoding="utf-8")
    return card_id in texto and aprovador in texto


def avaliar(*, card_id: str, titulo: str, corpo: str, dominios, ambiente_alvo=None,
            agora=None, caminho=None, caminho_registro=None, entradas=None) -> dict:
    """Decide se existe aprovacao aplicavel. Sempre devolve {aplicavel, motivo, ...}."""
    agora = agora or _dt.date.today()
    dominios = {str(d) for d in (dominios or [])}
    faltando = {"aplicavel": False, "motivo": "sem aprovacao registrada para o card"}
    try:
        mapa = carregar(caminho) if entradas is None else entradas
    except Exception as erro:
        return {"aplicavel": False, "motivo": f"registro de aprovacoes invalido: {erro}"}
    entrada = mapa.get(card_id)
    if not entrada:
        return faltando

    aprovador = str(entrada.get("aprovador") or "").strip()
    if aprovador != APROVADOR_AUTORIZADO:
        return {"aplicavel": False, "motivo": f"aprovador nao autorizado: {aprovador!r}"}

    hash_esperado = str(entrada.get("hash") or "").strip()
    if not hash_esperado:
        return {"aplicavel": False, "motivo": "entrada sem hash do texto aprovado"}
    hash_atual = hash_do_card(titulo, corpo)
    if hash_esperado != hash_atual:
        return {"aplicavel": False,
                "motivo": "o texto do card mudou depois da aprovacao (hash divergente)",
                "hash_aprovado": hash_esperado, "hash_atual": hash_atual}

    validade = _data(entrada.get("validade"))
    if validade is None:
        return {"aplicavel": False, "motivo": "entrada sem validade explicita (nao vale)"}
    if validade < agora:
        return {"aplicavel": False, "motivo": f"aprovacao vencida em {validade.isoformat()}"}

    escopo = {str(d) for d in (entrada.get("escopo") or [])}
    descobertos = sorted(dominios - escopo)
    if descobertos:
        return {"aplicavel": False,
                "motivo": f"aprovacao nao cobre os dominios {descobertos}",
                "escopo": sorted(escopo), "dominios": sorted(dominios)}

    canal = str(entrada.get("canal") or "").strip()
    if canal not in ("telegram", "commit-do-aprovador"):
        return {"aplicavel": False, "motivo": f"canal invalido ou ausente: {canal!r}"}
    ambiente = str(ambiente_alvo or "").strip().lower()
    # Ausencia de ambiente declarado NAO e desenvolvimento — e o ramo conservador: o
    # proprio roteador manda ambiente nao declarado para o ramo vivo/producao do piso de
    # lane ("ausencia de declaracao e abstinencia, nunca permissao"). Sem isto, um card
    # que o roteador qualificou de risco (ambiente nao declarado) era liberado por
    # `canal: telegram`; medido em 30/09/2026 e corrigido aqui.
    exige_assinatura = (ambiente not in AMBIENTES_DE_DESENVOLVIMENTO) or bool(
        dominios & set(DOMINIOS_COM_ASSINATURA))
    if exige_assinatura and canal != "commit-do-aprovador":
        motivo = ("escopo exige commit do aprovador (ambiente nao-desenvolvimento ou dominio "
                  "de credencial/dado de cliente) e o canal registrado e 'telegram'")
        return {"aplicavel": False, "motivo": motivo, "ambiente_alvo": ambiente or None,
                "dominios": sorted(dominios)}

    if not _card_no_registro(card_id, aprovador, caminho_registro):
        return {"aplicavel": False,
                "motivo": "dupla entrada incompleta: card/aprovador ausente do registro-de-aprovacoes"}

    return {"aplicavel": True, "motivo": "aprovacao registrada, valida, com hash e cobertura",
            "aprovador": aprovador, "canal": canal, "validade": validade.isoformat(),
            "escopo": sorted(escopo), "hash": hash_atual}
