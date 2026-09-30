#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conserto de raiz: a raiz do registro de entregas e DECLARADA, nao literal.

Card: TRE-W0-E04-T11 (`t_b74c2edd`). Entrega: patch versionado + uma linha do
operador. O alvo e `plugin_api.py` do plugin do dashboard kanban, que lia os
artefatos de entrega de um caminho FIXO (`/workspace/financial-dash`) dentro de
cada helper -- ou seja, a projecao de entrega do board de um projeto era decidida
pelo diretorio de OUTRO projeto.

Este editor faz as edicoes ANCORADAS (nunca `patch`: num salto de imagem os
hunks nao entram -- ver skill `kanban-plugin-update`):

  1. insere o bloco de resolucao `_delivery_repo_root(board)` + a constante
     documentada `_LEGACY_DELIVERIES_ROOT`, com o porque e a precedencia;
  2. troca as QUATRO ocorrencias de `repo_root = Path("/workspace/financial-dash")`
     (helpers `_delivery_production_done_task_ids`,
     `_delivery_explicit_done_task_ids`, `_delivery_lifecycle_task_columns`,
     `_delivery_human_approval_task_ids`) pela chamada ao resolvedor;
  3. acrescenta o parametro `board` nas cinco assinaturas da familia;
  4. propaga `board` em `_delivery_terminal_task_ids` e nos dois pontos de
     composicao do board (`get_board`).

Garantias:
  * cada ancora tem de aparecer EXATAMENTE uma vez (as 4 literais: exatamente 4);
    se nao casar, FALHA e nao escreve nada;
  * idempotente: rodar de novo nao duplica nada (o estado canonico e reconhecido);
  * `py_compile` antes e depois de escrever; backup datado ao lado;
  * `--check` relata sem escrever; `--reverter` desfaz as edicoes;
  * `--autoteste` roda o ciclo aplicar/checar/conferir/reverter/conferir numa
    copia em memoria do arquivo, sem tocar em disco.

Fail-closed: o conserto muda APENAS de onde o artefato e lido. A semantica por
item (item DONE => filhos terminais; item VALIDATION/CANDIDATE/HUMAN_APPROVAL =>
filhos projetados) e o fail-closed (ausencia de evidencia => VALIDATION) seguem
intocados.

Uso:
    python3 editar_plugin_api_delivery_root.py --alvo <plugin_api.py>
    python3 editar_plugin_api_delivery_root.py --alvo <plugin_api.py> --check
    python3 editar_plugin_api_delivery_root.py --alvo <plugin_api.py> --reverter
    python3 editar_plugin_api_delivery_root.py --autoteste
    bash aplicar_projecao_entrega.sh [--check|--reverter]     # casca do operador
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

# --- as ancoras ---------------------------------------------------------------

ANCORA_INSERCAO = '_CARD_SUMMARY_PREVIEW_CHARS = 200\n\n# --- PROJECAO DE ENTREGA (ASR/Transformativa) ---'

BLOCO_RESOLUCAO = '''_CARD_SUMMARY_PREVIEW_CHARS = 200

{bloco}

# --- PROJECAO DE ENTREGA (ASR/Transformativa) ---'''

BLOCO = '''# --- RAIZ DO REGISTRO DE ENTREGAS (DECLARADA) -------------------------------
# Os artefatos de entrega (`<raiz>/control-plane/deliveries/*.json`) vivem no
# REPOSITORIO do projeto, nao no plugin do dashboard. O plugin so precisa saber
# ONDE essa raiz esta -- e a resposta depende do board renderizado, porque cada
# board pertence a um repositorio.
#
# Ate 29/09/2026 a resposta era um caminho fixo, literal dentro de cada helper
# (a raiz do projeto /workspace/financial-dash), o que amarrava a projecao de
# entrega a UM projeto: o board de qualquer outro repositorio nunca alcancava
# evidencia terminal e todo card fechado ficava preso na projecao VALIDATION,
# mesmo com a evidencia ja escrita no repo dele.
#
# A raiz agora e DECLARADA, nesta ordem de precedencia (a primeira que existir vence):
#   1. `delivery_repo` no `board.json` do board renderizado -> a raiz fica
#      declarada junto do board; e isto que substitui o caminho literal;
#   2. env `HERMES_DELIVERIES_ROOT` -> override do processo (board ainda nao
#      declarado, fixtures e testes). `HERMES_DELIVERY_REPO` continua aceito: foi
#      o nome do override que rodou entre 29/09/2026 e a entrada deste conserto;
#   3. `_LEGACY_DELIVERIES_ROOT` -> compatibilidade com o unico consumidor que
#      existia quando a projecao foi escrita.
#
# Fail-closed intacto: nada aqui inventa evidencia. Raiz inexistente, sem
# `control-plane/deliveries/` ou artefato ilegivel => os helpers devolvem vazio e
# o card nativo `done` com pai de dependencia continua projetado em VALIDATION.

_LEGACY_DELIVERIES_ROOT = Path("/workspace/financial-dash")
_DELIVERIES_ENV_VARS = ("HERMES_DELIVERIES_ROOT", "HERMES_DELIVERY_REPO")


def _delivery_repo_root(board: Optional[str] = None) -> Path:
    """Raiz do registro de entregas do board (ver bloco acima).

    Resolve apenas ONDE ler. Quem decide se a entrega acabou continuam sendo os
    campos do artefato, por item, exatamente como antes.
    """
    import os

    if board:
        try:
            meta_path = kanban_db.board_metadata_path(board)
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text()) or {}
                valor = str(meta.get("delivery_repo") or "").strip()
                if valor:
                    return Path(valor).expanduser()
        except Exception as exc:  # metadado ausente/invalido nunca derruba o board
            log.warning("delivery_repo invalido no board %s: %s", board, exc)

    for nome in _DELIVERIES_ENV_VARS:
        env = str(os.environ.get(nome) or "").strip()
        if env:
            return Path(env).expanduser()

    return _LEGACY_DELIVERIES_ROOT'''

LITERAL_ANTIGO = '        repo_root = Path("/workspace/financial-dash")'
LITERAL_NOVO = "        repo_root = _delivery_repo_root(board)"
OCORRENCIAS_DO_LITERAL = 4

# Forma LEGADA do conserto: e a variante que rodou em producao desde 29/09/2026
# (copia `user` do plugin do dashboard), escrita fora do repo. Ela resolve a raiz
# por board, mas o caminho legado e um literal solto dentro do resolvedor e a env
# atende por outro nome. O editor a RECONHECE e a NORMALIZA para a forma
# canonica -- versionar o conserto exige que o que roda seja o que esta aqui.
MARCA_VARIANTE_LEGADA = "# --- OVERRIDE ASR: repositorio de entrega por board"
MARCA_FIM_DA_SECAO = "# --- PROJECAO DE ENTREGA (ASR/Transformativa)"
MARCA_INICIO_DA_SECAO = "_CARD_SUMMARY_PREVIEW_CHARS = 200\n"

BLOCO_VARIANTE_LEGADA = '''# --- OVERRIDE ASR: repositorio de entrega por board -------------------------
# O plugin empacotado lia os artefatos de entrega de um caminho fixo
# (/workspace/financial-dash). Isso fazia o board de outro projeto nunca alcancar
# evidencia terminal -- todo card fechado ficava preso na projecao VALIDATION.
# Agora o repositorio e resolvido POR BOARD, nesta ordem:
#   1. campo `delivery_repo` no board.json do board;
#   2. variavel de ambiente HERMES_DELIVERY_REPO;
#   3. caminho legado /workspace/financial-dash (compatibilidade).
# Ausencia de valor continua sendo fail-closed: sem diretorio de artefatos,
# nenhuma evidencia terminal e inventada.


def _delivery_repo_root(board: Optional[str] = None) -> Path:
    """Resolve o repositorio de artefatos de entrega deste board."""
    import os

    if board:
        try:
            meta_path = kanban_db.board_metadata_path(board)
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text()) or {}
                valor = str(meta.get("delivery_repo") or "").strip()
                if valor:
                    return Path(valor).expanduser()
        except Exception as exc:  # metadado ausente/invalido nunca derruba o board
            log.warning("delivery_repo invalido no board %s: %s", board, exc)

    env = str(os.environ.get("HERMES_DELIVERY_REPO") or "").strip()
    if env:
        return Path(env).expanduser()

    return Path("/workspace/financial-dash")'''

ASSINATURAS = [
    (
        'def _delivery_production_done_task_ids(\n    repo_root: Optional[Path] = None,\n) -> set[str]:',
        'def _delivery_production_done_task_ids(\n    repo_root: Optional[Path] = None,\n    board: Optional[str] = None,\n) -> set[str]:',
    ),
    (
        'def _delivery_explicit_done_task_ids(\n    repo_root: Optional[Path] = None,\n) -> set[str]:',
        'def _delivery_explicit_done_task_ids(\n    repo_root: Optional[Path] = None,\n    board: Optional[str] = None,\n) -> set[str]:',
    ),
    (
        'def _delivery_terminal_task_ids(\n    repo_root: Optional[Path] = None,\n) -> set[str]:',
        'def _delivery_terminal_task_ids(\n    repo_root: Optional[Path] = None,\n    board: Optional[str] = None,\n) -> set[str]:',
    ),
    (
        'def _delivery_lifecycle_task_columns(\n    repo_root: Optional[Path] = None,\n) -> dict[str, str]:',
        'def _delivery_lifecycle_task_columns(\n    repo_root: Optional[Path] = None,\n    board: Optional[str] = None,\n) -> dict[str, str]:',
    ),
    (
        'def _delivery_human_approval_task_ids(repo_root: Optional[Path] = None) -> set[str]:',
        'def _delivery_human_approval_task_ids(repo_root: Optional[Path] = None, board: Optional[str] = None) -> set[str]:',
    ),
]

TERMINAL_ANTES = (
    "    return _delivery_production_done_task_ids(\n"
    "        repo_root\n"
    "    ) | _delivery_explicit_done_task_ids(repo_root)"
)
TERMINAL_DEPOIS = (
    "    return _delivery_production_done_task_ids(\n"
    "        repo_root, board\n"
    "    ) | _delivery_explicit_done_task_ids(repo_root, board)"
)

COMPOSICAO_ANTES = (
    "        lifecycle_columns = _delivery_lifecycle_task_columns()\n"
    "        terminal_ids = _delivery_terminal_task_ids()"
)
COMPOSICAO_DEPOIS = (
    "        lifecycle_columns = _delivery_lifecycle_task_columns(board=board)\n"
    "        terminal_ids = _delivery_terminal_task_ids(board=board)"
)

# O que o conserto promete: nenhum caminho literal no CAMINHO DE DECISAO. O unico
# literal permitido e a constante legada documentada.
LITERAL_ESPERADO_NO_CANONICO = 1
MARCA_CANONICO = "_delivery_repo_root(board)"


class FalhaAncora(Exception):
    pass


def contar(texto: str, agulha: str) -> int:
    return texto.count(agulha)


def aplicar(texto: str) -> str:
    """Aplica as edicoes. Levanta FalhaAncora se alguma ancora nao casar."""
    if contagem_canonica(texto):
        raise FalhaAncora("ja canonico: nada a aplicar")

    # variante legada (runtime de 29/09 ate a entrada deste conserto): normaliza a
    # secao da raiz para a forma canonica. Nao ha o que "aplicar" no resto --
    # assinaturas, propagacao e composicao ja estao na forma canonica.
    if contar(texto, MARCA_VARIANTE_LEGADA) == 1:
        novo = _normalizar_secao(texto)
        if not contagem_canonica(novo):
            raise FalhaAncora("normalizacao da variante legada nao chegou a forma canonica")
        return novo

    n = contar(texto, ANCORA_INSERCAO)
    if n != 1:
        raise FalhaAncora(f"ancora de insercao aparece {n}x (esperado 1x)")

    n = contar(texto, LITERAL_ANTIGO)
    if n != OCORRENCIAS_DO_LITERAL:
        raise FalhaAncora(
            f"literal de caminho fixo aparece {n}x (esperado {OCORRENCIAS_DO_LITERAL}x) — o kernel mudou de forma; NAO editei"
        )

    for antes, _depois in ASSINATURAS:
        n = contar(texto, antes)
        if n != 1:
            raise FalhaAncora(f"assinatura aparece {n}x (esperado 1x): {antes.splitlines()[0]}")

    if contar(texto, TERMINAL_ANTES) != 1:
        raise FalhaAncora("corpo de _delivery_terminal_task_ids nao encontrado 1x")
    if contar(texto, COMPOSICAO_ANTES) != 1:
        raise FalhaAncora("composicao do board (get_board) nao encontrada 1x")

    novo = texto.replace(ANCORA_INSERCAO, BLOCO_RESOLUCAO.format(bloco=BLOCO), 1)
    novo = novo.replace(LITERAL_ANTIGO, LITERAL_NOVO)
    for antes, depois in ASSINATURAS:
        novo = novo.replace(antes, depois, 1)
    novo = novo.replace(TERMINAL_ANTES, TERMINAL_DEPOIS, 1)
    novo = novo.replace(COMPOSICAO_ANTES, COMPOSICAO_DEPOIS, 1)

    if contar(novo, 'Path("/workspace/financial-dash")') != LITERAL_ESPERADO_NO_CANONICO:
        raise FalhaAncora("caminho literal fora da constante legada no resultado")
    return novo


def _normalizar_secao(texto: str) -> str:
    """Substitui a SECAO da raiz (do marcador de inicio ao comentario seguinte
    `# --- PROJECAO DE ENTREGA`) pelo bloco canonico.

    Ancorar na secao inteira, e nao no texto exato da variante legada, e o que
    torna a normalizacao estavel a variacoes de linha em branco.
    """
    i = texto.find(MARCA_INICIO_DA_SECAO)
    if i < 0:
        raise FalhaAncora("secao da raiz nao delimitada (marcador de inicio ausente)")
    if contagem_canonica(texto):
        raise FalhaAncora("ja canonico: nada a normalizar")
    # o marcador de fim so conta na COLUNA 0 (a mesma string aparece indentada
    # dentro de get_board; essa nao delimita secao nenhuma)
    if contar(texto, "\n" + MARCA_FIM_DA_SECAO) != 1:
        raise FalhaAncora("marcador de fim da secao nao aparece 1x na coluna 0")
    j = texto.find("\n" + MARCA_FIM_DA_SECAO) + 1
    inicio = i + len(MARCA_INICIO_DA_SECAO)
    return texto[:inicio] + "\n" + BLOCO + "\n\n" + texto[j:]


def reverter(texto: str) -> str:
    """Desfaz as edicoes (inverso exato de `aplicar`)."""
    if not contagem_canonica(texto):
        raise FalhaAncora("arquivo nao esta no estado canonico; nada a reverter")
    novo = texto.replace(LITERAL_NOVO, LITERAL_ANTIGO)
    for antes, depois in ASSINATURAS:
        novo = novo.replace(depois, antes)
    novo = novo.replace(TERMINAL_DEPOIS, TERMINAL_ANTES)
    novo = novo.replace(COMPOSICAO_DEPOIS, COMPOSICAO_ANTES)
    novo = novo.replace(BLOCO_RESOLUCAO.format(bloco=BLOCO), ANCORA_INSERCAO, 1)
    return novo


def contagem_canonica(texto: str) -> bool:
    """True quando o texto tem a forma canonica completa."""
    if contar(texto, LITERAL_NOVO) != OCORRENCIAS_DO_LITERAL:
        return False
    if contar(texto, "_delivery_repo_root(board: Optional[str] = None)") != 1:
        return False
    # a constante legada DOCUMENTADA e a unica forma aceita de caminho fixo
    if contar(texto, '_LEGACY_DELIVERIES_ROOT = Path("/workspace/financial-dash")') != 1:
        return False
    if contar(texto, '_DELIVERIES_ENV_VARS = ("HERMES_DELIVERIES_ROOT", "HERMES_DELIVERY_REPO")') != 1:
        return False
    if MARCA_VARIANTE_LEGADA in texto:
        return False
    for _antes, depois in ASSINATURAS:
        if contar(texto, depois) != 1:
            return False
    if contar(texto, TERMINAL_DEPOIS) != 1 or contar(texto, COMPOSICAO_DEPOIS) != 1:
        return False
    if contar(texto, 'Path("/workspace/financial-dash")') != LITERAL_ESPERADO_NO_CANONICO:
        return False
    return True


def _compila(texto: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        alvo = Path(tmp) / "plugin_api.py"
        alvo.write_text(texto, encoding="utf-8")
        py_compile.compile(str(alvo), doraise=True, cfile=str(Path(tmp) / "x.pyc"))


def sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--alvo", help="caminho do plugin_api.py a editar")
    p.add_argument("--check", action="store_true", help="so relata (nao escreve)")
    p.add_argument("--reverter", action="store_true", help="desfaz as edicoes")
    p.add_argument("--autoteste", action="store_true", help="ciclo completo numa copia em memoria")
    args = p.parse_args(argv)

    if args.autoteste:
        return autoteste()

    if not args.alvo:
        print("FALHOU: informe --alvo <plugin_api.py> (ou use --autoteste)")
        return 1

    alvo = Path(args.alvo)
    if not alvo.is_file():
        print(f"FALHOU: alvo inexistente: {alvo}")
        return 1

    texto = alvo.read_text(encoding="utf-8")
    canonico = contagem_canonica(texto)

    if args.check:
        if canonico:
            print(f"OK    canonico: {alvo}")
            print(f"      sha256={sha256(alvo)}")
            return 0
        print(f"FALHOU nao canonico: {alvo}")
        return 1

    try:
        if args.reverter:
            novo = reverter(texto)
        else:
            novo = aplicar(texto)
    except FalhaAncora as exc:
        motivo = str(exc)
        if args.reverter or "ja canonico" not in motivo:
            print(f"FALHOU {alvo}: {motivo}")
            return 1
        print(f"OK    ja canonico (idempotente): {alvo}")
        print(f"      sha256={sha256(alvo)}")
        return 0

    _compila(novo)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = alvo.with_name(alvo.name + f".bak-entrega-root-{ts}")
    shutil.copy2(alvo, backup)
    tmp = alvo.with_suffix(alvo.suffix + ".tmp")
    tmp.write_text(novo, encoding="utf-8")
    tmp.replace(alvo)
    print(f"OK    {'revertido' if args.reverter else 'aplicado'}: {alvo}")
    print(f"      backup: {backup}")
    print(f"      sha256: {sha256(alvo)}")
    return 0


def autoteste() -> int:
    """Ciclo aplicar -> checar -> compilar -> reverter -> conferir, em memoria."""
    base = Path(__file__).resolve().parent / "baseline" / "plugin_api.pristina.py"
    if not base.is_file():
        print("FALHOU autoteste: baseline pristina ausente:", base)
        return 1
    original = base.read_text(encoding="utf-8")
    itens = 0
    falhas = 0

    def checa(nome: str, cond: bool) -> None:
        nonlocal itens, falhas
        itens += 1
        print(("OK    " if cond else "FALHOU ") + nome)
        if not cond:
            falhas += 1

    checa("pristina nao e canonica", not contagem_canonica(original))
    checa(
        "pristina tem 4 literais de caminho fixo",
        contar(original, LITERAL_ANTIGO) == 4,
    )
    novo = aplicar(original)
    checa("aplicada e canonica", contagem_canonica(novo))
    checa("um unico literal no canonico (constante legada)", contar(novo, 'Path("/workspace/financial-dash")') == 1)
    checa("resolvedor presente 1x", contar(novo, "def _delivery_repo_root(board: Optional[str] = None) -> Path:") == 1)
    checa("5 assinaturas com board", all(contar(novo, d) == 1 for _a, d in ASSINATURAS))
    checa("board propagado na composicao", contar(novo, COMPOSICAO_DEPOIS) == 1)
    try:
        _compila(novo)
        checa("canonica compila", True)
    except Exception as exc:  # pragma: no cover
        print("      erro:", exc)
        checa("canonica compila", False)
    checa("idempotente (2a aplicacao falha como 'ja canonico')", _ja_canonico(aplicar, novo))
    voltou = reverter(novo)
    checa("reverter volta a pristina (byte a byte)", voltou == original)
    checa("revertida nao e canonica", not contagem_canonica(voltou))

    # variante legada (a que rodou em producao desde 29/09) -> normalizada
    cabeca = novo[:novo.index(MARCA_INICIO_DA_SECAO) + len(MARCA_INICIO_DA_SECAO)]
    v1 = cabeca + "\n" + BLOCO_VARIANTE_LEGADA + "\n\n\n" + \
        novo[novo.index(MARCA_FIM_DA_SECAO):]
    checa("variante legada sintetizada nao e canonica", not contagem_canonica(v1))
    checa("variante legada sintetizada preserva o resto do arquivo",
          v1[v1.index(MARCA_FIM_DA_SECAO):] == novo[novo.index(MARCA_FIM_DA_SECAO):])
    checa("normalizacao da variante legada == forma canonica (byte a byte)",
          aplicar(v1) == novo)
    checa("normalizar idempotente (2a vez falha como 'ja canonico')",
          _ja_canonico(aplicar, aplicar(v1)))
    checa("variante legada contem o literal solto no resolvedor",
          v1.count('Path("/workspace/financial-dash")') == 1
          and '    return Path("/workspace/financial-dash")' in v1)

    print("---")
    print(f"autoteste: {itens - falhas}/{itens} itens OK, {falhas} falhas")
    return 0 if falhas == 0 else 1


def _ja_canonico(fn, texto: str) -> bool:
    try:
        fn(texto)
    except FalhaAncora as exc:
        return "ja canonico" in str(exc)
    return False


if __name__ == "__main__":
    sys.exit(main())
