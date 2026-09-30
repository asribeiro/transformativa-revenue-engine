#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador da projecao de entrega por raiz DECLARADA (card TRE-W0-E04-T11).

Mede o codigo REAL do plugin do dashboard contra o board REAL, e prova:

  A. o conserto — a raiz do registro de entregas e declarada (board.json > env >
     legado documentado), sem caminho literal no caminho de decisao, e o delta
     versionado reproduz byte a byte o arquivo canonico;
  B. a evidencia de aceitacao do card — com a raiz apontada para o repo do TRE os
     14 itens do artefato `W0-governanca-e-baseline.json` produzem os 16 ids
     terminais e NENHUM card `done` do board projeta `validation` (era 12); com a
     raiz padrao (/workspace/financial-dash) o resultado e o de hoje (44 ids);
  C. os guardrails homologados — fail-closed intacto, semantica por item inalterada,
     projecao e APRESENTACAO (o status nativo do card nao e tocado);
  D. o autoteste por mutacao — o proprio verificador REPROVA o codigo quando o
     conserto e desfeito de quatro maneiras (raiz ignorada, env ignorado,
     fail-open, literal de volta no helper).

Uso:
    /opt/hermes/.venv/bin/python scripts/verificar_projecao_entrega.py
    /opt/hermes/.venv/bin/python scripts/verificar_projecao_entrega.py --manter

Nao escreve em /opt/hermes nem no board (abre o SQLite em modo leitura).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DEPLOY = RAIZ / "deploy/hermes/projecao-entrega"
BASELINE = DEPLOY / "baseline/plugin_api.pristina.py"
EDITOR = DEPLOY / "editar_plugin_api_delivery_root.py"
PATCH = DEPLOY / "plugin_api.patch"
ARTEFATO_W0 = RAIZ / "control-plane/deliveries/W0-governanca-e-baseline.json"

BOARD = "transformativa-revenue-engine"
BOARD_DIR = Path("/opt/data/kanban/boards") / BOARD
BOARD_DB = BOARD_DIR / "kanban.db"
BOARD_JSON = BOARD_DIR / "board.json"

# A copia que o dashboard REALMENTE carrega e a `user` (o plugin empacotado com o
# mesmo nome e sombreado por ela — ver `_dashboard_plugin_search_dirs`).
LIVE_PLUGIN = Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")
BUNDLED_PLUGIN = Path("/opt/hermes/plugins/kanban/dashboard/plugin_api.py")

RAIZ_LEGADA = Path("/workspace/financial-dash")
SHA_BASELINE = "a0d99603463b9188a8cf67adacbfcabad1c8d2d40a02f7c5d81cf4a6674472ef"
SHA_CANONICO = "cef4412392c89bdcf51cf4500bb19dea8c4b2e4930c33a3e89d50b58f25eacba"

# Os 12 cards `done` que ficavam presos em `validation` lendo o caminho fixo
# (medido em 30/09/2026, o achado que abriu este card).
CARDS_PRESOS_ANTES = {
    "t_25ca689e", "t_38cbab9a", "t_4be20bcc", "t_4f20bd10", "t_6d326367",
    "t_722b6cbd", "t_8c332df7", "t_b3387f25", "t_c8e69f74", "t_d8bc83b3",
    "t_e7d9decd", "t_e9535df3",
}

ITENS: list[tuple[str, bool]] = []


def checa(nome: str, cond: bool, detalhe: str = "") -> bool:
    ITENS.append((nome, bool(cond)))
    sufixo = f"  [{detalhe}]" if detalhe else ""
    print(("OK    " if cond else "FALHOU ") + nome + sufixo)
    return bool(cond)


# --- infraestrutura -----------------------------------------------------------


def carregar(caminho: Path, nome: str):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


def carregar_editor():
    return carregar(EDITOR, "editor_projecao")


def sha256(caminho: Path) -> str:
    import hashlib

    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def conexao_leitura() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{BOARD_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def pais_de_dependencia(con: sqlite3.Connection, task_id: str) -> list[str]:
    return [
        r["parent_id"]
        for r in con.execute(
            "SELECT parent_id FROM task_links WHERE child_id=? "
            "AND COALESCE(link_type,'dependency')='dependency'",
            (task_id,),
        )
    ]


def ids_do_artefato_W0() -> list[str]:
    artefato = json.loads(ARTEFATO_W0.read_text(encoding="utf-8"))
    ids: list[str] = []
    for item in artefato.get("work_items") or []:
        for filho in item.get("children") or []:
            if filho.get("hermes_task_id"):
                ids.append(filho["hermes_task_id"])
    return ids


def medir(mod, **kwargs) -> dict:
    """Aplica a regra de composicao do board (`get_board`) e devolve o que importa.

    A logica de coluna e a MESMA de `plugin_api.get_board` (linhas ~636-660):
    evidencia terminal vence a projecao; card `done` sem evidencia e com pai de
    DEPENDENCIA cai em `validation`.
    """
    terminal = mod._delivery_terminal_task_ids(**kwargs)
    lifecycle = mod._delivery_lifecycle_task_columns(**kwargs)
    con = conexao_leitura()
    try:
        done = [r["id"] for r in con.execute("SELECT id FROM tasks WHERE status='done'")]
        em_validation: list[str] = []
        colunas: dict[str, str] = {}
        for tid in done:
            projected = lifecycle.get(tid)
            if tid in terminal:
                projected = None
            if projected is None and tid not in terminal and pais_de_dependencia(con, tid):
                projected = "validation"
            coluna = projected if projected in {"validation", "candidate", "human_approval"} else "done"
            colunas[tid] = coluna
            if coluna == "validation":
                em_validation.append(tid)
    finally:
        con.close()
    return {
        "terminal": set(terminal),
        "lifecycle": dict(lifecycle),
        "validation": set(em_validation),
        "colunas": colunas,
        "done": set(done),
    }


@contextlib.contextmanager
def env(**valores):
    antes = {k: os.environ.get(k) for k in valores}
    for k, v in valores.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def raiz_sintetica(destino: Path, entregas: dict[str, dict]) -> Path:
    """Monta uma raiz de entregas sintetica (control-plane/deliveries/*.json)."""
    pasta = destino / "control-plane" / "deliveries"
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, conteudo in entregas.items():
        (pasta / nome).write_text(json.dumps(conteudo), encoding="utf-8")
    return destino


def artefato_sintetico() -> dict:
    """Um artefato com um item em cada estagio do ciclo + um item sem DONE."""
    return {
        "delivery_id": "SYNTH",
        "release_status": "IN_PROGRESS",
        "current_gate": "VALIDATION",
        "human_approval": {"required": True, "status": "PENDING", "decision": "PENDING"},
        "work_items": [
            {"id": "S-DONE", "stage": "DONE",
             "children": [{"hermes_task_id": "t_synth_done", "stage": "DONE"}]},
            {"id": "S-DONE-FILHO-PENDENTE", "stage": "DONE", "current_gate": "DONE",
             "children": [{"hermes_task_id": "t_synth_pendente", "stage": "IN_DEVELOPMENT",
                           "status": "IN_PROGRESS"}]},
            {"id": "S-NO-DONE", "stage": "IN_DEVELOPMENT",
             "children": [{"hermes_task_id": "t_synth_nodev", "stage": "IN_DEVELOPMENT",
                           "status": "IN_PROGRESS"}]},
            {"id": "S-VALIDATION", "stage": "VALIDATION",
             "children": [{"hermes_task_id": "t_synth_val", "stage": "VALIDATION"}]},
            {"id": "S-CANDIDATE", "stage": "CANDIDATE",
             "children": [{"hermes_task_id": "t_synth_cand", "stage": "CANDIDATE"}]},
            {"id": "S-HUMAN", "stage": "HUMAN_APPROVAL", "current_gate": "HUMAN_APPROVAL",
             "children": [{"hermes_task_id": "t_synth_hum", "stage": "HUMAN_APPROVAL",
                           "current_gate": "HUMAN_APPROVAL"}]},
        ],
    }


# --- o verificador ------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manter", action="store_true", help="mantem o tmp para inspecao")
    args = p.parse_args()

    if not BASELINE.is_file() or not EDITOR.is_file() or not PATCH.is_file():
        print("FALHOU: artefatos do deploy ausentes em", DEPLOY)
        return 1
    if not BOARD_DB.is_file():
        print("FAIL-CLOSED: board ausente:", BOARD_DB)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="verif_projecao_"))
    try:
        return rodar(tmp)
    finally:
        if args.manter:
            print("tmp mantido:", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def rodar(tmp: Path) -> int:
    editor = carregar_editor()
    pristina = BASELINE.read_text(encoding="utf-8")

    # ----- A. o conserto -----------------------------------------------------
    print("=== A. raiz DECLARADA (conserto) ===")
    checa("baseline pristina e a medicao original (sha256)", sha256(BASELINE) == SHA_BASELINE,
          sha256(BASELINE)[:16])
    checa("pristina tem o caminho fixo em 4 helpers",
          pristina.count('repo_root = Path("/workspace/financial-dash")') == 4)

    canonico_texto = editor.aplicar(pristina)
    api_canonico = tmp / "plugin_api.canonico.py"
    api_canonico.write_text(canonico_texto, encoding="utf-8")
    checa("canonico: sha256 esperado", sha256(api_canonico) == SHA_CANONICO,
          sha256(api_canonico)[:16])
    checa("canonico: nenhum caminho literal fora da constante legada",
          invariante_literal_unico(canonico_texto))
    checa("canonico: a constante legada e DOCUMENTADA (o unico literal)",
          '_LEGACY_DELIVERIES_ROOT = Path("/workspace/financial-dash")' in canonico_texto
          and canonico_texto.count("_LEGACY_DELIVERIES_ROOT") >= 2)
    checa("canonico: resolvedor de raiz por board presente",
          "def _delivery_repo_root(board: Optional[str] = None) -> Path:" in canonico_texto)
    checa("canonico: board propagado na composicao do board",
          "_delivery_lifecycle_task_columns(board=board)" in canonico_texto
          and "_delivery_terminal_task_ids(board=board)" in canonico_texto)

    subprocess.run([sys.executable, "-m", "py_compile", str(api_canonico)], check=True)
    checa("canonico compila (py_compile, exit 0)", True)

    # o .patch versionado reproduz o canonico byte a byte
    alvo_patch = tmp / "patchtest"
    (alvo_patch / "plugins/kanban/dashboard").mkdir(parents=True)
    copia = alvo_patch / "plugins/kanban/dashboard/plugin_api.py"
    shutil.copy2(BASELINE, copia)
    r = subprocess.run(["patch", "-p1", "-s", "-i", str(PATCH)], cwd=alvo_patch,
                       capture_output=True, text=True)
    checa("patch versionado aplica na baseline (patch(1), exit 0)", r.returncode == 0, r.stderr.strip())
    checa("patch versionado reproduz o canonico byte a byte", sha256(copia) == SHA_CANONICO)

    r_editor = subprocess.run([sys.executable, str(EDITOR), "--autoteste"], capture_output=True, text=True)
    checa("editor ancorado: autoteste PASS", r_editor.returncode == 0,
          (r_editor.stdout.strip().splitlines() or [""])[-1])

    mod = carregar(api_canonico, "api_canonico")
    mod_pristina = carregar(BASELINE, "api_pristina")

    # o que roda tem de ser o que esta versionado (fim do conserto que so existia
    # em runtime, sem rastro no repo)
    if LIVE_PLUGIN.is_file():
        live_texto = LIVE_PLUGIN.read_text(encoding="utf-8")
        if live_texto == canonico_texto:
            checa("copia em uso (user) ja e a canonica versionada", True)
        else:
            try:
                migrado = editor.aplicar(live_texto)
            except Exception as exc:
                migrado = None
                checa("applier reconhece a copia em uso", False, str(exc))
            checa("applier normaliza a copia em uso para a canonica (byte a byte)",
                  migrado == canonico_texto)
        checa("plugin em uso (copia `user` do dashboard) == canonico versionado",
              sha256(LIVE_PLUGIN) == SHA_CANONICO, sha256(LIVE_PLUGIN)[:16])
        checa("plugin em uso resolve a raiz por board (nao ha caminho fixo no helper)",
              invariante_literal_unico(live_texto))

    # ----- B. evidencia de aceitacao do card ---------------------------------
    print()
    print("=== B. evidencia de aceitacao (board real) ===")
    m_board = medir(mod, board=BOARD)          # raiz DECLARADA no board.json
    m_legado = medir(mod, repo_root=RAIZ_LEGADA)  # raiz padrao (hoje)

    ids_w0 = ids_do_artefato_W0()
    checa("artefato W0: 14 itens => 16 ids de filhos", len(ids_w0) == 16, f"{len(ids_w0)} ids")
    checa("raiz do board (TRE): os 16 ids do artefato W0 sao terminais",
          set(ids_w0) <= m_board["terminal"],
          f"faltando: {sorted(set(ids_w0) - m_board['terminal'])}")
    checa("raiz do board (TRE): 16 ids terminais hoje (o registro do TRE e o W0)",
          len(m_board["terminal"]) == 16, f"{len(m_board['terminal'])}")
    checa("raiz do board (TRE): os 12 cards do achado estao terminais",
          CARDS_PRESOS_ANTES <= m_board["terminal"],
          f"faltando: {sorted(CARDS_PRESOS_ANTES - m_board['terminal'])}")
    checa("raiz do board (TRE): NENHUM dos 12 cards do achado projeta validation",
          not (CARDS_PRESOS_ANTES & m_board["validation"]),
          f"{len(CARDS_PRESOS_ANTES & m_board['validation'])} de 12")
    checa("raiz do board (TRE): NENHUM dos 16 filhos do W0 projeta validation",
          not (set(ids_w0) & m_board["validation"]),
          f"{len(set(ids_w0) & m_board['validation'])} de 16")
    # Estado do board inteiro no momento do aceite (o que o card pede: "0 de 12
    # anteriores"). Se um card NOVO for fechado com pai de dependencia e sem
    # evidencia de entrega, o fail-closed o mantem em validation e este item
    # acusa -- e o comportamento correto, e a linha abaixo diz qual card foi.
    checa("raiz do board (TRE): 0 cards done projetando validation no aceite",
          len(m_board["validation"]) == 0,
          f"{len(m_board['validation'])} de {len(m_board['done'])} done: {sorted(m_board['validation'])}")

    checa("raiz padrao: 44 ids terminais (identico a hoje)", len(m_legado["terminal"]) == 44,
          f"{len(m_legado['terminal'])}")
    checa("raiz padrao: 0 cards done projetando validation", len(m_legado["validation"]) == 0,
          f"{len(m_legado['validation'])}")
    checa("raiz padrao: os 16 do W0 continuam terminais", set(ids_w0) <= m_legado["terminal"])

    producao = mod._delivery_production_done_task_ids(repo_root=RAIZ_LEGADA)
    por_item = mod._delivery_explicit_done_task_ids(repo_root=RAIZ_LEGADA)
    checa("raiz padrao: decomposicao 19 (producao) + 9 (item DONE do DTV1) + 16 (W0) = 44",
          len(producao) == 19 and len(por_item) == 37
          and len(producao | por_item) == 44,
          f"producao={len(producao)} item_done={len(por_item)} uniao={len(producao | por_item)}")

    # ----- B'. o achado e causal: sem o conserto, os 12 voltam --------------
    print()
    print("=== B'. prova causal (o achado se reproduz sem o conserto) ===")
    achado = medir(mod_pristina, repo_root=LEGACY_FIXTURE(tmp))
    checa("codigo com caminho fixo (sem o W0 no caminho): 28 ids terminais",
          len(achado["terminal"]) == 28, f"{len(achado['terminal'])}")
    checa("codigo com caminho fixo (sem o W0 no caminho): 12 cards done em validation",
          achado["validation"] == CARDS_PRESOS_ANTES,
          f"{len(achado['validation'])}")

    # ----- C. guardrails -----------------------------------------------------
    print()
    print("=== C. guardrails homologados ===")
    sint_root = raiz_sintetica(tmp / "raiz_synth", {"SYNTH.json": artefato_sintetico()})
    syn_baseline = {
        "terminal": mod_pristina._delivery_terminal_task_ids(repo_root=sint_root),
        "lifecycle": mod_pristina._delivery_lifecycle_task_columns(repo_root=sint_root),
    }
    syn_canonico = {
        "terminal": mod._delivery_terminal_task_ids(repo_root=sint_root),
        "lifecycle": mod._delivery_lifecycle_task_columns(repo_root=sint_root),
    }
    checa("semantica por item inalterada: identico a pristina no mesmo artefato",
          syn_baseline == syn_canonico)
    checa("item DONE => filho terminal", "t_synth_done" in syn_canonico["terminal"])
    checa("item sem DONE => filho NAO terminal (fail-closed)",
          "t_synth_nodev" not in syn_canonico["terminal"])
    checa("item DONE com filho explicitamente pendente => filho NAO terminal (fail-closed por filho)",
          "t_synth_pendente" not in syn_canonico["terminal"])
    checa("item VALIDATION => filho projetado em validation",
          syn_canonico["lifecycle"].get("t_synth_val") == "validation")
    checa("item CANDIDATE => filho projetado em candidate",
          syn_canonico["lifecycle"].get("t_synth_cand") == "candidate")
    checa("item HUMAN_APPROVAL (aprovacao pendente) => filho projetado em human_approval",
          syn_canonico["lifecycle"].get("t_synth_hum") == "human_approval")
    vazia = tmp / "raiz_vazia"
    vazia.mkdir()
    m_vazia = medir(mod, repo_root=vazia)
    checa("raiz sem control-plane/deliveries => nenhuma evidencia terminal",
          m_vazia["terminal"] == set() and m_vazia["lifecycle"] == {})

    # precedencia: board.json > env > legado
    with env(HERMES_DELIVERIES_ROOT=str(sint_root), HERMES_DELIVERY_REPO=None):
        checa("precedencia: board.json declarado vence a env",
              medir(mod, board=BOARD)["terminal"] == set(ids_w0))
        checa("precedencia: env vence o legado (board nao informado)",
              mod._delivery_terminal_task_ids() == {"t_synth_done"})
    with env(HERMES_DELIVERIES_ROOT=None, HERMES_DELIVERY_REPO=str(sint_root)):
        checa("alias HERMES_DELIVERY_REPO (o nome que rodou em producao) honrado",
              mod._delivery_terminal_task_ids() == {"t_synth_done"})
    with env(HERMES_DELIVERIES_ROOT=None, HERMES_DELIVERY_REPO=None):
        checa("sem configuracao => raiz legada (/workspace/financial-dash)",
              len(mod._delivery_terminal_task_ids()) == 44)

    # projecao e APRESENTACAO: o status nativo nao e tocado
    con = conexao_leitura()
    try:
        antes = {r["id"]: r["status"] for r in con.execute("SELECT id, status FROM tasks")}
        tentativa_escrita = None
        try:
            con.execute("UPDATE tasks SET status='done' WHERE id='__inexistente__'")
        except sqlite3.OperationalError as exc:
            tentativa_escrita = str(exc)
    finally:
        con.close()
    medir(mod, board=BOARD)
    con = conexao_leitura()
    try:
        depois = {r["id"]: r["status"] for r in con.execute("SELECT id, status FROM tasks")}
    finally:
        con.close()
    checa("projecao e apresentacao: status nativo identico antes/depois", antes == depois)
    checa("verificador abre o board em modo leitura (escrita recusada)",
          tentativa_escrita is not None and "readonly" in tentativa_escrita.lower(),
          tentativa_escrita or "")

    # ----- D. autoteste por mutacao -----------------------------------------
    print()
    print("=== D. autoteste por mutacao (o verificador tem de REPROVAR) ===")

    mutacoes = [
        ("raiz nao resolvida por board (o outro projeto decide)",
         canonico_texto.replace(
             "    if board:\n        try:\n            meta_path = kanban_db.board_metadata_path(board)",
             "    if False:\n        try:\n            meta_path = kanban_db.board_metadata_path(board)", 1),
         None),
        ("literal de volta dentro do helper",
         canonico_texto.replace(LITERAL_DE_HELPER_NOVO, LITERAL_DE_HELPER_ANTIGO, 1),
         None),
        ("fail-open: filho sem evidencia vira terminal",
         canonico_texto.replace(
             '                if not child_stages or "DONE" in child_stages:',
             '                if True:', 1),
         None),
        ("env ignorado (override de processo deixa de existir)",
         canonico_texto.replace(
             '    for nome in _DELIVERIES_ENV_VARS:\n'
             '        env = str(os.environ.get(nome) or "").strip()\n'
             '        if env:\n'
             '            return Path(env).expanduser()',
             '    if False:\n        pass', 1),
         None),
    ]
    sint_env = {"HERMES_DELIVERIES_ROOT": str(sint_root), "HERMES_DELIVERY_REPO": None}
    for nome, texto_mutado, _ in mutacoes:
        checa("mutacao muda o arquivo: " + nome, texto_mutado != canonico_texto)
        caminho = tmp / ("mut_" + str(abs(hash(nome))) + ".py")
        caminho.write_text(texto_mutado, encoding="utf-8")
        if nome.startswith("raiz nao resolvida"):
            # o board declara o repo do TRE, mas a mutacao cai na env (outro projeto):
            # e o achado de volta -- 12 cards done presos em validation.
            with env(HERMES_DELIVERIES_ROOT=str(LEGACY_FIXTURE(tmp)), HERMES_DELIVERY_REPO=None):
                m = medir(carregar(caminho, "mut_sem_board"), board=BOARD)
            reprovou = CARDS_PRESOS_ANTES <= m["validation"]
            detalhe = (f"{len(m['validation'])} em validation; "
                       f"{len(CARDS_PRESOS_ANTES & m['validation'])} dos 12 do achado")
        elif nome.startswith("literal"):
            reprovou = not invariante_literal_unico(texto_mutado)
            detalhe = (f"{contar_literais(texto_mutado)} literais, "
                       f"literal no helper={'sim' if LITERAL_DE_HELPER_ANTIGO in texto_mutado else 'nao'}")
        elif nome.startswith("fail-open"):
            with env(**sint_env):
                mm = carregar(caminho, "mut_fail_open")
                reprovou = "t_synth_pendente" in mm._delivery_explicit_done_task_ids(repo_root=sint_root)
            detalhe = "filho explicitamente pendente virou terminal" if reprovou else "seguiu fail-closed"
        else:
            with env(**sint_env):
                mm = carregar(caminho, "mut_env")
                reprovou = mm._delivery_terminal_task_ids() != {"t_synth_done"}
            detalhe = "raiz da env deixou de ser lida" if reprovou else "env ainda honrada"
        checa("verificador REPROVA a mutacao: " + nome, reprovou, detalhe)

    # ----- resultado ---------------------------------------------------------
    falhas = [nome for nome, ok in ITENS if not ok]
    print()
    print("---")
    print(f"RESULTADO: {len(ITENS) - len(falhas)}/{len(ITENS)} itens PASS, {len(falhas)} falhas")
    for nome in falhas:
        print("  FALHOU", nome)
    return 0 if not falhas else 1


def contar_literais(texto: str) -> int:
    return texto.count('Path("/workspace/financial-dash")')


LITERAL_DE_HELPER_ANTIGO = '        repo_root = Path("/workspace/financial-dash")'
LITERAL_DE_HELPER_NOVO = "        repo_root = _delivery_repo_root(board)"


def invariante_literal_unico(texto: str) -> bool:
    """Nenhum caminho literal no CAMINHO DE DECISAO: o unico literal permitido e a
    constante legada documentada (`_LEGACY_DELIVERIES_ROOT`)."""
    return (
        texto.count('Path("/workspace/financial-dash")') == 1
        and '_LEGACY_DELIVERIES_ROOT = Path("/workspace/financial-dash")' in texto
        and LITERAL_DE_HELPER_ANTIGO not in texto
    )


def LEGACY_FIXTURE(tmp: Path) -> Path:
    """Fixture que reproduz o estado ANTES do paliativo: a raiz fixa do outro
    projeto, sem o artefato do TRE alcancavel por symlink."""
    destino = tmp / "legacy_fixture"
    pasta = destino / "control-plane" / "deliveries"
    pasta.mkdir(parents=True, exist_ok=True)
    copiados = 0
    for f in sorted(RAIZ_LEGADA.glob("control-plane/deliveries/*.json")):
        if f.is_symlink():
            continue  # o symlink do W0 e o paliativo: o achado e medido SEM ele
        shutil.copy2(f, pasta / f.name)
        copiados += 1
    if copiados == 0:
        raise SystemExit("FAIL-CLOSED: nao consegui montar a fixture legada (sem acesso a "
                         f"{RAIZ_LEGADA}/control-plane/deliveries)")
    return destino


if __name__ == "__main__":
    sys.exit(main())
