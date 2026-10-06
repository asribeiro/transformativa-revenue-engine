#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ENCAIXE ARQUIVO QUENTE — o promotor da onda passa a olhar ARQUIVO, nao so dependencia.

Card de origem: TRE-W3-E01-T03-D01 (defeito de processo, retroativo, medido em 02/10/2026).

O DEFEITO
---------
O promotor da onda decidia por **dependencia e prioridade** e nao tinha **mapa de
arquivo quente**. Dois cards irmaos que editam os MESMOS arquivos subiam no mesmo
tick (`TRE-W3-E01-T02` e `TRE-W3-E01-T03` ficaram em voo juntos; o `git status` do
worktree do T02 listava os 7 arquivos que o T03 obrigatoriamente toca). Rodar em
paralelo nesse caso so tem duas saidas, ambas ruins: duplicar o mecanismo (duas
implementacoes do mesmo conceito no mesmo arquivo) ou divergir (cada card inventa
o seu) — foi o que aconteceu de fato: os dois irmaos divergiram no esquema da
politica da API (`campos_de_identidade` em lista x `campo_de_identidade` singular).

A REGRA QUE ESTE ENCAIXE FAZ VALER
----------------------------------
    Card que declara `HOTSPOT:` no corpo entra em voo SOZinho por arquivo quente:
    no maximo UM card por arquivo declarado em voo por vez. O segundo fica em
    `ready` (parkeado) e e reivindicado no tick seguinte ao fechamento do primeiro.

* **Arquivo quente declarado** — o card escreve no corpo, em linha propria, o
  marcador ``HOTSPOT:`` seguido dos caminhos (a regra do formato esta em
  ``docs/kanban/hotspot-de-arquivo.md``). Exemplo, as duas formas aceitas::

      HOTSPOT: api/motor.py, api/politica_api.json

  e::

      HOTSPOT:
      - api/motor.py
      - api/politica_api.json

  PROSA que apenas menciona a palavra "hotspot" (o que os cards da W2/W3
  escrevem hoje: "HOTSPOT — fazer UMA vez...", "…é hotspot de append…") **nao**
  e declaracao e nao liga o encaixe para aquele card: sem o marcador seguido de
  caminho, o card roda como sempre rodou. E isso que impede falso positivo.

* **Em voo** — ``running`` ou ``review``: trabalho ainda nao integrado. ``blocked``
  NAO conta (card parado nao esta editando; quem serializa um card bloqueado e o
  proprio vinculo de dependencia do board).

* **Caminho** — comparado por caminho normalizado (``./`` fora, ``//`` colapsado).
  Declaracao terminada em ``/`` e diretorio e casa qualquer arquivo abaixo dele.

O ENCAIXE
---------
E um ponto de estrangulamento so — ``kanban_db.claim_task`` (por onde passam o
claim manual E o despachante) — mais o balde nomeado em
``kanban_db_dispatch._dispatch_lane_task`` para o tick reportar o card retido em
``skipped_hotspot`` em vez de parecer ocioso. Este adaptador e a unica coisa que o
kernel conhece do encaixe.

INERTE POR OMISSAO: sem ``kanban.hotspot_gate`` configurado (ou desligado), TODAS
as funcoes abaixo devolvem "sem impedimento" e o board se comporta exatamente como
antes — e o que permite a prova negativa (defeito reproduzido com o encaixe
desligado) e o desligamento de emergencia em um comando.

Configuracao (``/opt/data/config.yaml``, secao ``kanban``)::

    kanban:
      hotspot_gate: true                                   # ausente/off => inerte
      hotspot_gate_boards: [transformativa-revenue-engine] # ausente = todos

Variaveis de ambiente equivalentes (tem precedencia, para ensaio e emergencia):
``HERMES_HOTSPOT_GATE`` e ``HERMES_HOTSPOT_GATE_BOARDS``.

ESTE ARQUIVO E A VERSAO VERSIONADA (repo TRE). A copia ativa vive em
``/opt/hermes/hermes_cli/kanban_hotspot_gate.py`` e e instalada por
``deploy/hermes/aplicar_hotspot.sh`` (operador, root). A suite
``scripts/verificar_hotspot_gate.py`` reprova se as duas divergirem.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Optional

_log = logging.getLogger(__name__)

GATE = "hotspot-gate-v1"
CONFIG_SECTION = "kanban"
CONFIG_KEY = "hotspot_gate"
CONFIG_KEY_BOARDS = "hotspot_gate_boards"
ENV_GATE = "HERMES_HOTSPOT_GATE"
ENV_BOARDS = "HERMES_HOTSPOT_GATE_BOARDS"
# Valor que DESLIGA o encaixe mesmo com `kanban.hotspot_gate` ligado.
VALORES_DE_DESLIGAR = {"off", "0", "none", "false", "nao", "não", "desligado", ""}
VALORES_DE_LIGAR = {"on", "1", "true", "sim", "ligado", "yes"}
# "Em voo" = trabalho ainda nao integrado. `blocked` NAO conta (card parado nao
# esta editando; para isso o board ja tem o vinculo de dependencia).
ESTADOS_EM_VOO = ("running", "review")
EVENTO_ESPERA = "hotspot_wait"
MARCADOR = "hotspot:"
MARCADORES_DE_LISTA = ("-", "*", "+", "•")
# Extensoes aceitas para um token SEM barra virar caminho (evita ler palavra de
# prosa como arquivo). Token com barra e aceito sempre.
EXTENSOES = (
    "py", "sh", "bash", "json", "yaml", "yml", "toml", "ini", "cfg", "env", "md", "rst",
    "txt", "csv", "tsv", "sql", "js", "ts", "tsx", "jsx", "html", "css", "scss", "xml",
    "svg", "png", "jpg", "jpeg", "gif", "pdf", "lock", "mod", "conf", "service", "timer",
)
_RE_TOKEN_COM_EXTENSAO = re.compile(r"^[\w.\-]+\.[A-Za-z0-9]{1,8}$")


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
def _env(nome: str) -> str:
    return str(os.environ.get(nome, "") or "").strip()


def _desligado(valor: str) -> bool:
    return valor.casefold() in VALORES_DE_DESLIGAR


def _kanban_config() -> dict:
    try:
        from hermes_cli.config import load_config
        return dict((load_config() or {}).get(CONFIG_SECTION) or {})
    except Exception as exc:  # pragma: no cover - defensivo
        _log.debug("arquivo quente: config indisponivel (%s)", exc)
        return {}


def _valor_do_gate() -> str:
    valor = _env(ENV_GATE)
    if not valor:
        cfg = _kanban_config().get(CONFIG_KEY)
        if isinstance(cfg, bool):
            return "on" if cfg else "off"
        valor = str(cfg or "").strip()
    return valor


def encaixe_ligado() -> bool:
    """True quando o encaixe esta LIGADO para este processo.

    Precedencia: ``HERMES_HOTSPOT_GATE`` (env) > ``kanban.hotspot_gate`` (config).
    Ausente/vazio/desligado => False (inerte); valor explicito de ligar => True.
    """
    valor = _valor_do_gate()
    if not valor or _desligado(valor):
        return False
    return valor.casefold() in VALORES_DE_LIGAR


def boards_do_encaixe() -> Optional[list]:
    """Boards em que o encaixe vale; ``None`` = todos."""
    bruto = _env(ENV_BOARDS)
    if not bruto:
        bruto = _kanban_config().get(CONFIG_KEY_BOARDS)
    if bruto is None:
        return None
    if isinstance(bruto, str):
        itens = [p.strip() for p in bruto.split(",")]
    elif isinstance(bruto, (list, tuple)):
        itens = [str(p).strip() for p in bruto]
    else:
        return None
    itens = [p for p in itens if p]
    if not itens or itens in (["*"], ["all"], ["todos"]):
        return None
    return itens


def _board_ativo() -> str:
    try:
        from hermes_cli import kanban_db as _kb
        return str(_kb.get_current_board() or "")
    except Exception:  # pragma: no cover - defensivo
        return ""


def aplica_ao_board(board: Optional[str] = None) -> bool:
    escopo = boards_do_encaixe()
    if escopo is None:
        return True
    return (board or _board_ativo() or "") in escopo


def diagnostico() -> dict:
    """Estado do encaixe — usado pela suite e pelo operador (read-only)."""
    return {
        "gate": GATE,
        "encaixe_ligado": encaixe_ligado(),
        "boards": boards_do_encaixe(),
        "estados_em_voo": list(ESTADOS_EM_VOO),
        "config": "%s.%s" % (CONFIG_SECTION, CONFIG_KEY),
        "marcador": MARCADOR,
        "regra": "no maximo UM card por arquivo quente em voo por vez",
    }


# ---------------------------------------------------------------------------
# Declaracao de arquivo quente no corpo do card
# ---------------------------------------------------------------------------
def _limpa_marcacao(linha: str) -> str:
    """Tira a marcacao de markdown do COMECO da linha (``>``, ``*``, ``#``, ``_``)."""
    return linha.strip().lstrip(">*#_ \t")


def _limpa_token(token: str) -> str:
    """Tira crase/aspas/lista em volta do token e o pontuacao final."""
    t = token.strip().strip("`*_'\"()[]<>|")
    return t.strip().rstrip(".,;:").strip()


def _e_caminho(token: str) -> bool:
    if "/" in token:
        return True
    if _RE_TOKEN_COM_EXTENSAO.match(token) is None:
        return False
    return token.rsplit(".", 1)[-1].casefold() in EXTENSOES


def _normalizar(token: str) -> str:
    t = re.sub(r"/{2,}", "/", token.strip())
    while t.startswith("./"):
        t = t[2:]
    diretorio = t.endswith("/")
    t = t.rstrip("/")
    return t + "/" if diretorio else t


def _tokens_de(texto: str) -> list:
    return [p for p in re.split(r"[,\s;]+", texto or "") if p.strip()]


def declarar_hotspot(corpo: Optional[str]) -> tuple:
    """Extrai a declaracao ``HOTSPOT:`` do corpo. Devolve ``(arquivos, ignorados)``.

    ``arquivos`` vem normalizado e sem repeticao, na ordem de declaracao.
    ``ignorados`` guarda os tokens que o card escreveu no marcador mas que nao tem
    forma de caminho (fica no evento/diagnostico, para o operador ver o que o card
    quis dizer em vez de o encaixe adivinhar).

    So o MARCADOR conta. Prosa que menciona "hotspot" nao e declaracao.
    """
    linhas = (corpo or "").splitlines()
    arquivos: list = []
    ignorados: list = []
    for indice, linha in enumerate(linhas):
        visivel = _limpa_marcacao(linha)
        if not visivel.casefold().startswith(MARCADOR):
            continue
        blocos = [visivel.split(":", 1)[1]]
        for seguinte in linhas[indice + 1:]:
            limpa = seguinte.strip()
            if not limpa:
                break
            if limpa[:1] not in MARCADORES_DE_LISTA:
                break
            blocos.append(_limpa_marcacao(limpa))
            # `- arquivo` -> o marcador de lista sai junto com a marcacao
            blocos[-1] = blocos[-1][1:] if blocos[-1][:1] in MARCADORES_DE_LISTA else blocos[-1]
        for bloco in blocos:
            for token in _tokens_de(bloco):
                limpo = _limpa_token(token)
                if not limpo:
                    continue
                if not _e_caminho(limpo):
                    ignorados.append(limpo)
                    continue
                caminho = _normalizar(limpo)
                if caminho and caminho not in arquivos:
                    arquivos.append(caminho)
        break
    return arquivos, ignorados


def casa(declarado_a: str, declarado_b: str) -> bool:
    """Dois caminhos declarados colidem? Diretorio (``.../``) casa o que ha abaixo."""
    if declarado_a.endswith("/") or declarado_b.endswith("/"):
        a = declarado_a.rstrip("/")
        b = declarado_b.rstrip("/")
        return a == b or a.startswith(b + "/") or b.startswith(a + "/")
    return declarado_a == declarado_b


# ---------------------------------------------------------------------------
# Consulta ao board
# ---------------------------------------------------------------------------
def _campo(linha, nome: str, indice: int):
    try:
        return linha[nome]
    except Exception:
        return linha[indice]


def _linhas(conn, sql: str, parametros: tuple) -> list:
    try:
        return conn.execute(sql, parametros).fetchall()
    except Exception as exc:  # pragma: no cover - defensivo
        _log.debug("arquivo quente: consulta ao board falhou (%s)", exc)
        return []


def card_do_board(conn, task_id: str):
    """``(status, corpo, arquivos_declarados)`` do card; ``(None, None, [])`` se sumiu."""
    linhas = _linhas(conn, "SELECT status, body FROM tasks WHERE id = ?", (task_id,))
    if not linhas:
        return None, None, []
    status = _campo(linhas[0], "status", 0)
    corpo = _campo(linhas[0], "body", 1)
    arquivos, _ = declarar_hotspot(corpo)
    return status, corpo, arquivos


def em_voo(conn, excluir: Optional[str] = None) -> list:
    """Cards EM VOO que declaram arquivo quente: ``[{task_id,status,arquivos}]``.

    Ordem de entrada no voo (``started_at``, depois id) — a ordem e o que a tela
    do operador mostra como fila.
    """
    linhas = _linhas(
        conn,
        "SELECT id, status, body FROM tasks "
        "WHERE status IN ('running', 'review') AND id IS NOT ? "
        "ORDER BY started_at, created_at, id",
        (excluir,),
    )
    saida = []
    for linha in linhas:
        arquivos, _ = declarar_hotspot(_campo(linha, "body", 2))
        if not arquivos:
            continue
        saida.append({
            "task_id": _campo(linha, "id", 0),
            "status": _campo(linha, "status", 1),
            "arquivos": arquivos,
        })
    return saida


def impedimento(conn, task_id: str, *, board: Optional[str] = None) -> Optional[dict]:
    """Devolve a decisao QUANDO o card nao pode entrar em voo agora; ``None`` = pode.

    Qualquer erro aqui e tratado como "sem impedimento": o encaixe serializa
    trabalho, nao e um portao de seguranca — travar o board por falha de leitura
    seria pior do que o defeito que ele corrige.
    """
    try:
        if not encaixe_ligado():
            return None
        if not aplica_ao_board(board):
            return None
        status, _corpo, arquivos = card_do_board(conn, task_id)
        if status != "ready" or not arquivos:
            return None
        detentores = []
        for dono in em_voo(conn, excluir=task_id):
            comuns = [a for a in arquivos if any(casa(a, b) for b in dono["arquivos"])]
            if comuns:
                detentores.append({
                    "task_id": dono["task_id"],
                    "status": dono["status"],
                    "arquivos": comuns,
                })
        if not detentores:
            return None
        comuns = sorted({a for d in detentores for a in d["arquivos"]})
        nomes = ", ".join("%s (%s)" % (d["task_id"], d["status"]) for d in detentores)
        return {
            "gate": GATE,
            "impedido": True,
            "motivo": "arquivo quente em voo em %s: %s" % (nomes, ", ".join(comuns)),
            "arquivos": comuns,
            "detentores": detentores,
            "regra": "no maximo UM card por arquivo quente em voo por vez",
        }
    except Exception as exc:  # pragma: no cover - defensivo
        _log.debug("arquivo quente: impedimento de %s falhou (%s)", task_id, exc)
        return None


# ---------------------------------------------------------------------------
# Registro no card (evento, sem segredo)
# ---------------------------------------------------------------------------
def _impressao(decisao: dict) -> str:
    base = json.dumps({
        "arquivos": sorted(decisao.get("arquivos") or []),
        "detentores": sorted(d.get("task_id") for d in (decisao.get("detentores") or [])),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _ultima_impressao(conn, task_id: str) -> Optional[str]:
    linhas = _linhas(
        conn,
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
        (task_id, EVENTO_ESPERA),
    )
    if not linhas:
        return None
    try:
        payload = json.loads(_campo(linhas[0], "payload", 0) or "{}")
    except Exception:
        return None
    return payload.get("impressao") if isinstance(payload, dict) else None


def registrar(conn, task_id: str, decisao: dict, *, origem: str = "claim") -> None:
    """Grava ``hotspot_wait`` UMA vez por configuracao distinta de espera.

    Sem isso o evento viraria um por tick. Quando o conjunto de detentores/arquivos
    muda (o primeiro card fechou e outro assumiu), a espera e outra e o evento novo
    aparece. Chamavel de dentro de uma transacao do kernel (nao abre txn aninhada).
    """
    try:
        from hermes_cli import kanban_db as _kb
        impressao = _impressao(decisao)
        if _ultima_impressao(conn, task_id) == impressao:
            return
        payload = {
            "gate": GATE,
            "motivo": decisao.get("motivo"),
            "arquivos": decisao.get("arquivos"),
            "detentores": [
                {"task_id": d.get("task_id"), "status": d.get("status")}
                for d in (decisao.get("detentores") or [])
            ],
            "impressao": impressao,
            "origem_da_consulta": origem,
            "regra": decisao.get("regra"),
        }
        if getattr(conn, "in_transaction", False):
            _kb._append_event(conn, task_id, EVENTO_ESPERA, payload)
        else:
            with _kb.write_txn(conn):
                _kb._append_event(conn, task_id, EVENTO_ESPERA, payload)
    except Exception as exc:  # pragma: no cover - defensivo
        _log.debug("arquivo quente: nao registrei o evento de %s (%s)", task_id, exc)


def resumo(decisao: dict) -> str:
    """Motivo curto para CLI/log."""
    return str(decisao.get("motivo") or "arquivo quente em voo")


# ---------------------------------------------------------------------------
# Ferramenta read-only do coordenador/operador
# ---------------------------------------------------------------------------
def _cli(argv=None) -> int:
    """``--corpo`` confere a declaracao antes de o card entrar no board; ``--card``
    mostra o que o card declara e quem esta segurando (leitura read-only)."""
    import argparse
    import sqlite3

    parser = argparse.ArgumentParser(
        prog="kanban_hotspot_gate",
        description="Confere a declaracao `HOTSPOT:` de um corpo de card (regra: no maximo "
                    "UM card por arquivo quente EM VOO por vez). Nao escreve nada.")
    parser.add_argument("--corpo", help="arquivo com o corpo do card")
    parser.add_argument("--card", help="id de um card existente no board")
    parser.add_argument("--board", help="slug do board (default: board ativo)")
    args = parser.parse_args(argv)

    if not args.corpo and not args.card:
        parser.error("informe --corpo <arquivo.md> ou --card <id>")

    if args.corpo:
        texto = open(args.corpo, "r", encoding="utf-8").read()
        arquivos, ignorados = declarar_hotspot(texto)
        print("arquivos quentes declarados: %d" % len(arquivos))
        for caminho in arquivos:
            print("  - %s" % caminho)
        if ignorados:
            print("nao viraram caminho (revise): %s" % ", ".join(ignorados))
        if not arquivos:
            print("  (nenhum: sem o marcador `HOTSPOT:` o card NAO e serializado por arquivo)")
        return 0

    from hermes_cli import kanban_db as _kb
    slug = args.board or _kb.get_current_board()
    con = sqlite3.connect("file:%s?mode=ro" % _kb.kanban_db_path(slug), uri=True)
    con.row_factory = sqlite3.Row
    try:
        status, _corpo, arquivos = card_do_board(con, args.card)
        if status is None:
            print("card %s nao encontrado no board %s" % (args.card, slug))
            return 2
        print("card %s (%s) no board %s" % (args.card, status, slug))
        for caminho in arquivos:
            print("  declara: %s" % caminho)
        if not arquivos:
            print("  (sem declaracao `HOTSPOT:` — nao e serializado por arquivo)")
        for dono in em_voo(con, excluir=args.card):
            comuns = [a for a in arquivos if any(casa(a, b) for b in dono["arquivos"])]
            if comuns:
                print("  segurado por %s (%s): %s"
                      % (dono["task_id"], dono["status"], ", ".join(comuns)))
        print("encaixe: %s" % ("LIGADO" if encaixe_ligado() else "desligado (inerte)"))
    finally:
        con.close()
    return 0


__all__ = [
    "GATE", "EVENTO_ESPERA", "ESTADOS_EM_VOO", "aplica_ao_board", "boards_do_encaixe",
    "card_do_board", "casa", "declarar_hotspot", "diagnostico", "em_voo", "encaixe_ligado",
    "impedimento", "registrar", "resumo",
]


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli())

