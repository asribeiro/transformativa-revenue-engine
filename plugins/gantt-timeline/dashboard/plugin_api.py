"""Backend do plugin `gantt-timeline`: entrega o PLANEJADO para a pagina somar ao REAL.

O planejado vive no cronograma versionado do repositorio (`hermes/plan/cronograma.yaml`), nao no board:
o board guarda o que aconteceu (created_at/started_at/completed_at) e o cronograma guarda o que se pretendia.
Manter os dois separados e o que permite ver o desvio — que e o ponto da linha do tempo.

Se o cronograma nao existir ou estiver ilegivel, responde `disponivel: false` e a pagina segue mostrando
apenas o real (nunca inventa data planejada).
"""
from __future__ import annotations

import datetime
import os
import pathlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

router = APIRouter()

CRONOGRAMA_PADRAO = "/opt/data/repos/transformativa-revenue-engine/hermes/plan/cronograma.yaml"


def _caminho() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TRE_CRONOGRAMA", CRONOGRAMA_PADRAO))


def _epoca(data: Any) -> Optional[int]:
    """Aceita date do YAML (datetime.date) ou string ISO e devolve epoch em segundos."""
    if data is None:
        return None
    if isinstance(data, datetime.datetime):
        d = data.date()
    elif isinstance(data, datetime.date):
        d = data
    else:
        texto = str(data).strip()
        try:
            d = datetime.date.fromisoformat(texto)
        except ValueError:
            return None
    # meio-dia UTC: evita a barra escorregar um dia na conversao de fuso do navegador
    return int(datetime.datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp())


def _carregar() -> Dict[str, Any]:
    caminho = _caminho()
    if not caminho.is_file():
        return {"disponivel": False, "motivo": f"cronograma nao encontrado em {caminho}"}
    try:
        import yaml  # noqa: PLC0415 — so no processo do dashboard (tem PyYAML)
    except ImportError:
        return {"disponivel": False, "motivo": "PyYAML indisponivel no processo do dashboard"}
    try:
        bruto = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # YAML invalido nao pode derrubar a pagina
        return {"disponivel": False, "motivo": f"cronograma ilegivel: {exc.__class__.__name__}"}

    ondas: Dict[str, Dict[str, Any]] = {}
    for onda, dados in (bruto.get("ondas") or {}).items():
        if not isinstance(dados, dict):
            continue
        ini, fim = _epoca(dados.get("inicio")), _epoca(dados.get("fim"))
        if ini is None and fim is None:
            continue
        ondas[str(onda)] = {"inicio": ini, "fim": fim, "estado": dados.get("estado")}

    cards: Dict[str, Dict[str, Any]] = {}
    for card_id, dados in (bruto.get("cards") or {}).items():
        if not isinstance(dados, dict):
            continue
        ini, fim = _epoca(dados.get("inicio")), _epoca(dados.get("fim"))
        if ini is None and fim is None:
            continue
        cards[str(card_id)] = {"inicio": ini, "fim": fim, "estado": dados.get("estado")}

    return {
        "disponivel": True,
        "versao": bruto.get("versao"),
        "proposta_em": str(bruto.get("proposta_em") or ""),
        "origem": bruto.get("origem"),
        "caminho": str(caminho),
        "ondas": ondas,
        "cards": cards,
    }


@router.get("/planejado")
def planejado(board: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Janelas planejadas por onda (e, quando houver, por card). `board` fica na assinatura porque um dia
    pode haver cronograma por board; hoje ha um so, o do TRE."""
    dados = _carregar()
    dados["board"] = board
    return dados
