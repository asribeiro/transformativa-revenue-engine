"""GATE JEV (ASR/Transformativa) — consulta o roteador JEV ANTES de reivindicar/despachar.

Card de origem: TRE-W0-E04-T05. Este adaptador e o UNICO ponto que o kernel do board
conhece do encaixe: os dois chamadores (``kanban_db.claim_task`` e
``kanban_db_dispatch._dispatch_lane_task``) so perguntam "pode executar?".

Sem gate INSTALADO nada muda: ``kanban.jev_gate`` ausente/vazio => esta camada e
inerte (``None``) e o board se comporta exatamente como antes. E isso que torna o
encaixe reversivel em um comando — e e a prova negativa da suite do TRE
(``scripts/verificar_gate_jev.py``): removido o encaixe, o card volta a executar.

Configuracao (``/opt/data/config.yaml``, secao ``kanban``):

    kanban:
      jev_gate: /opt/data/repos/transformativa-revenue-engine/hermes/jev/gate/gate_jev.py
      jev_gate_boards: [transformativa-revenue-engine]   # ausente = todos os boards
      jev_gate_timeout: 60                               # segundos (default 60)

Contrato do comando do gate (versionado, ver ``hermes/jev/gate/gate_jev.py``):
  * recebe ``--board``, ``--card``, ``--kanban-db`` e ``--resposta <arquivo json>``;
  * saida 0 = pode executar; 2 = escala; 3 = bloqueia; 1 = falha do gate;
  * o recibo (13 campos) e gravado pelo proprio gate, sempre.

Ausencia de resposta do JEV e abstencao, nunca permissao: timeout, saida fora do
contrato e falha de execucao terminam em NAO EXECUTA (fail-closed), com o evento
``jev_gate_rejected`` registrado no card.

ESTE ARQUIVO E A VERSAO VERSIONADA (repo TRE). A copia ativa vive em
``/opt/hermes/hermes_cli/kanban_jev_gate.py`` e e instalada/atualizada por
``deploy/hermes/aplicar_gate_jev.sh``. A suite do encaixe reprova se as duas
divergirem (sha256).
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional

_log = logging.getLogger(__name__)

CONFIG_SECTION = "kanban"
CONFIG_KEY = "jev_gate"
CONFIG_KEY_BOARDS = "jev_gate_boards"
CONFIG_KEY_TIMEOUT = "jev_gate_timeout"
DEFAULT_TIMEOUT_SECONDS = 60
# Chaves de ambiente (tem precedencia sobre a config, para ensaio e para o
# interruptor de emergencia do operador).
ENV_GATE = "HERMES_JEV_GATE"
ENV_BOARDS = "HERMES_JEV_GATE_BOARDS"
ENV_TIMEOUT = "HERMES_JEV_GATE_TIMEOUT"
# Valor que DESLIGA o encaixe mesmo com `kanban.jev_gate` configurado.
VALORES_DE_DESLIGAR = {"off", "0", "none", "false", "nao", "não", "desligado"}
EVENT_REJECTED = "jev_gate_rejected"
EVENT_ALLOWED = "jev_gate_allowed"


def _env(nome: str) -> str:
    return str(os.environ.get(nome, "") or "").strip()


def _desligado(valor: str) -> bool:
    return valor.casefold() in VALORES_DE_DESLIGAR


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
def _kanban_config() -> dict:
    try:
        from hermes_cli.config import load_config
        return dict((load_config() or {}).get(CONFIG_SECTION) or {})
    except Exception as exc:  # pragma: no cover - defensivo
        _log.debug("gate JEV: config indisponivel (%s)", exc)
        return {}


def comandos_do_gate() -> Optional[list]:
    """Comando do gate, ou ``None`` quando o encaixe nao esta instalado.

    Precedencia: ``HERMES_JEV_GATE`` (env) > ``kanban.jev_gate`` (config).
    Os valores de ``VALORES_DE_DESLIGAR`` desligam o encaixe mesmo com a config
    preenchida — e o interruptor de emergencia do operador e, na suite, o que
    produz a PROVA NEGATIVA: sem encaixe, o card volta a executar.
    """
    valor = _env(ENV_GATE)
    if not valor:
        valor = str(_kanban_config().get(CONFIG_KEY) or "").strip()
    if not valor or _desligado(valor):
        return None
    partes = shlex.split(valor) if " " in valor else [valor]
    if not partes:
        return None
    if len(partes) == 1 and partes[0].endswith(".py"):
        partes = [sys.executable, partes[0]]
    return partes


def boards_do_gate() -> Optional[list]:
    """Boards em que o gate vale; ``None`` = todos (fail-closed por omissao)."""
    bruto = _env(ENV_BOARDS)
    if not bruto:
        cfg = _kanban_config()
        bruto = cfg.get(CONFIG_KEY_BOARDS)
    if bruto is None:
        return None
    if isinstance(bruto, str):
        itens = [p.strip() for p in bruto.split(",")]
    elif isinstance(bruto, (list, tuple)):
        itens = [str(p).strip() for p in bruto]
    else:
        return None
    itens = [p for p in itens if p]
    if not itens:
        return None
    if itens in (["*"], ["all"], ["todos"]):
        return None
    return itens


def timeout_do_gate() -> int:
    bruto = _env(ENV_TIMEOUT)
    if not bruto:
        bruto = _kanban_config().get(CONFIG_KEY_TIMEOUT)
    try:
        valor = int(bruto or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return max(1, valor)


def aplica_ao_board(board: Optional[str]) -> bool:
    escopo = boards_do_gate()
    if escopo is None:
        return True
    return (board or "") in escopo


def _executavel(caminho: str) -> bool:
    return bool(shutil.which(caminho) or os.path.isfile(caminho))


def _dir_de_recibos(comando) -> Optional[str]:
    """Diretorio dos recibos que o gate usa (mesma resolucao do proprio gate).

    Existe para que a regra "recibo gravado sempre" valha tambem quando o gate
    NAO conseguiu rodar: nesse caso o adaptador grava ali um registro explicito de
    falha, em vez de deixar a consulta silenciosa.
    """
    explicito = _env("JEV_RECIBOS_DIR")
    if explicito:
        return explicito
    script = next((p for p in (comando or []) if str(p).endswith(".py")), None)
    if not script:
        return None
    try:
        raiz = pathlib.Path(script).resolve().parents[2]   # <repo>/hermes/jev/gate/x.py
    except Exception:
        return None
    alvo = raiz / "hermes" / "jev" / "receipts"
    return str(alvo) if alvo.parent.is_dir() else None


def _registrar_falha(diretorio: Optional[str], task_id: str, motivo: str, resposta: dict) -> None:
    """Registro de falha do encaixe. NAO e um recibo de decisao (e diz isso)."""
    if not diretorio:
        return
    try:
        destino = pathlib.Path(diretorio)
        destino.mkdir(parents=True, exist_ok=True)
        (destino / f"{task_id}--FALHA-DO-GATE.json").write_text(
            json.dumps({
                "gate": "gate-jev-adaptador",
                "card_id": task_id,
                "decisao": None,
                "nao_e_recibo_de_decisao": True,
                "outcome": resposta.get("outcome"),
                "motivo": motivo,
                "observacao": ("o gate nao produziu decisao: o board trata como NAO EXECUTA "
                               "(fail-closed) e registra o evento jev_gate_rejected no card"),
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        resposta.setdefault("receipt_path", str(destino / f"{task_id}--FALHA-DO-GATE.json"))
    except Exception:  # pragma: no cover - defensivo
        pass


def consultar_gate(task_id: str, *, board: Optional[str] = None) -> Optional[dict]:
    """Decide se o card pode executar. ``None`` = encaixe ausente/inativo.

    Nunca levanta: falha do gate e resposta (NAO EXECUTA), nao excecao — um erro
    aqui nao pode derrubar o ciclo do board nem liberar o card por acidente.
    """
    comando = comandos_do_gate()
    if not comando:
        return None
    slug, caminho_db = _resolver_board_e_db(board)
    if not aplica_ao_board(slug):
        return None
    diretorio_recibos = _dir_de_recibos(comando)
    if not _executavel(comando[0]):
        resposta = {"allow": False, "outcome": "GATE_INDISPONIVEL", "decidido": "falha_do_gate",
                    "motivo": "comando do gate JEV nao encontrado: %r" % (comando[0],),
                    "decision_id": None, "receipt_path": None}
        _registrar_falha(diretorio_recibos, task_id, resposta["motivo"], resposta)
        return resposta

    arquivo_resposta = None
    try:
        fd, arquivo_resposta = tempfile.mkstemp(prefix="jev-gate-", suffix=".json")
        os.close(fd)
        linha = [*comando, "--card", str(task_id), "--resposta", arquivo_resposta]
        if slug:
            linha += ["--board", str(slug)]
        if caminho_db:
            linha += ["--kanban-db", caminho_db]
        if diretorio_recibos:
            linha += ["--recibos-dir", diretorio_recibos]
        proc = subprocess.run(  # comando vem da config do operador
            linha, capture_output=True, text=True, timeout=timeout_do_gate(), check=False,
        )
        resposta = _ler_resposta(arquivo_resposta)
        if resposta is None:
            resposta = {"allow": False, "outcome": "GATE_INDISPONIVEL",
                        "decidido": "saida_fora_do_contrato",
                        "motivo": ("gate JEV nao escreveu resposta legivel (exit=%s): %s"
                                   % (proc.returncode, (proc.stderr or "").strip()[:400])),
                        "decision_id": None, "receipt_path": None}
            _registrar_falha(diretorio_recibos, task_id, resposta["motivo"], resposta)
            return resposta
        resposta.setdefault("exit_code", proc.returncode)
        if proc.stderr:
            resposta["gate_stderr"] = proc.stderr.strip()[:400]
        # Falha do gate NUNCA vira permissao, mesmo que a resposta diga allow.
        if proc.returncode == 1:
            resposta["allow"] = False
            resposta.setdefault("outcome", "GATE_INDISPONIVEL")
        return resposta
    except subprocess.TimeoutExpired:
        resposta = {"allow": False, "outcome": "GATE_TIMEOUT", "decidido": "timeout_do_gate",
                    "motivo": "gate JEV nao respondeu em %ss (abstencao, nao permissao)"
                              % timeout_do_gate(),
                    "decision_id": None, "receipt_path": None}
        _registrar_falha(diretorio_recibos, task_id, resposta["motivo"], resposta)
        return resposta
    except Exception as exc:  # pragma: no cover - defensivo
        resposta = {"allow": False, "outcome": "GATE_INDISPONIVEL", "decidido": "falha_do_gate",
                    "motivo": "falha ao consultar o gate JEV: %s: %s" % (type(exc).__name__, exc),
                    "decision_id": None, "receipt_path": None}
        _registrar_falha(diretorio_recibos, task_id, resposta["motivo"], resposta)
        return resposta
    finally:
        if arquivo_resposta:
            try:
                os.unlink(arquivo_resposta)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
def _resolver_board_e_db(board: Optional[str]):
    """Board ativo e caminho do ``kanban.db`` que o gate vai ler."""
    from hermes_cli import kanban_db as _kb

    slug = board
    if slug is None:
        try:
            slug = _kb.get_current_board()
        except Exception:
            slug = None
    caminho = ""
    try:
        caminho = str(_kb.kanban_db_path(slug))
    except Exception as exc:  # pragma: no cover - defensivo
        _log.debug("gate JEV: nao resolvi o caminho do board (%s)", exc)
    return slug, (caminho or None)


def _ler_resposta(caminho: str) -> Optional[dict]:
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
    except Exception:
        return None
    return dados if isinstance(dados, dict) else None


# ---------------------------------------------------------------------------
# Registro no card (evento, sem segredo)
# ---------------------------------------------------------------------------
def _ultimo_decision_id(conn, task_id: str, evento: str) -> Optional[str]:
    linha = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = ? "
        "ORDER BY id DESC LIMIT 1", (task_id, evento),
    ).fetchone()
    if linha is None:
        return None
    try:
        payload = json.loads(linha["payload"] or "{}")
    except Exception:
        return None
    return payload.get("decision_id") if isinstance(payload, dict) else None


def _registrar(conn, task_id: str, evento: str, resposta: dict) -> None:
    """Grava o evento do gate UMA vez por decisao distinta (nao uma por tick)."""
    decision_id = resposta.get("decision_id")
    if decision_id is not None and _ultimo_decision_id(conn, task_id, evento) == decision_id:
        return
    from hermes_cli import kanban_db as _kb

    payload = {
        "gate": resposta.get("gate") or resposta.get("gate_versao"),
        "outcome": resposta.get("outcome"),
        "decidido": resposta.get("decidido"),
        "lane": resposta.get("lane"),
        "motivo": (resposta.get("motivos") or [resposta.get("motivo") or ""])[0],
        "codigo_de_acao": resposta.get("codigo_de_acao"),
        "origem_do_codigo_de_acao": resposta.get("origem_do_codigo_de_acao"),
        "decision_id": decision_id,
        "receipt_path": resposta.get("receipt_path"),
        "exige_aprovacao_humana": bool(resposta.get("exige_aprovacao_humana")),
    }
    try:
        with _kb.write_txn(conn):
            _kb._append_event(conn, task_id, evento, payload)
    except Exception as exc:  # pragma: no cover - defensivo
        _log.debug("gate JEV: nao registrei o evento %s de %s (%s)", evento, task_id, exc)


def _status_do_card(conn, task_id: str) -> Optional[str]:
    try:
        linha = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    except Exception:  # pragma: no cover - defensivo
        return None
    return linha["status"] if linha is not None else None


# ---------------------------------------------------------------------------
# API para os chamadores do kernel
# ---------------------------------------------------------------------------
def rejeicao(conn, task_id: str, *, board: Optional[str] = None,
             origem: str = "claim") -> Optional[dict]:
    """Consulta o gate e devolve a resposta QUANDO o card NAO pode executar.

    ``None`` significa "sem impedimento" — encaixe ausente, card fora do escopo do
    gate, ou o roteador liberou. O evento do card e gravado aqui (uma vez por
    decisao distinta), para que ``hermes kanban tail`` mostre o motivo.
    """
    if _status_do_card(conn, task_id) != "ready":
        return None
    resposta = consultar_gate(task_id, board=board)
    if resposta is None:
        return None
    resposta.setdefault("origem_da_consulta", origem)
    _registrar(conn, task_id, EVENT_REJECTED if not resposta.get("allow") else EVENT_ALLOWED, resposta)
    if resposta.get("allow"):
        return None
    return resposta


def resumo(resposta: dict) -> str:
    """Motivo curto para CLI/log, sem segredo."""
    motivo = resposta.get("motivo") or ", ".join(resposta.get("motivos") or []) or "sem motivo"
    return "%s: %s" % (resposta.get("outcome"), motivo)


def gate_instalado() -> bool:
    """True quando o encaixe esta instalado e alcancavel (usado por diagnostico)."""
    comando = comandos_do_gate()
    return bool(comando) and _executavel(comando[0])


def diagnostico() -> dict:
    """Estado do encaixe — usado por ``scripts/verificar_gate_jev.py`` e pelo operador."""
    comando = comandos_do_gate()
    return {
        "instalado": gate_instalado(),
        "comando": comando,
        "boards": boards_do_gate(),
        "timeout_s": timeout_do_gate(),
        "config": "%s.%s" % (CONFIG_SECTION, CONFIG_KEY),
        "verificado_em": int(time.time()),
    }


__all__ = [
    "EVENT_ALLOWED", "EVENT_REJECTED", "aplica_ao_board", "boards_do_gate",
    "comandos_do_gate", "consultar_gate", "diagnostico", "gate_instalado",
    "rejeicao", "resumo", "timeout_do_gate",
]
