#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifica a NAO-DERIVA do registro de aprovacoes humanas.

Regra (docs/operations/registro-de-aprovacoes.md, secao "Como citar evidencia"): linha do registro que cita
verificador/suite COM contagem de itens tem de citar **identidade imutavel** — o caminho, o **commit** e o
**sha256** do arquivo NAQUELE commit. Contagem solta ("233 itens PASS") envelhece no commit seguinte e vira
afirmacao falsa: foi assim que as linhas da v1.1 e da v1.2 mentiram.

Checks:
  1. FORMATO   — toda linha que cita `scripts/*.py` E uma contagem de itens cita commit + sha256;
  2. PAR       — todo sha256 citado tem commit anterior na mesma linha (sha256 orfao = FAIL);
  3. IDENTIDADE— cada caminho citado tem de ser reproduzido por pelo menos um par da linha:
                 sha256(git show <commit>:<caminho>) == sha256 citado;
  4. CONTAGEM  — com `--contagem`: mede a contagem de itens no commit citado (worktree temporario) e
                 compara com o numero declarado na linha. Sem o flag, o item reporta PULADO (explicito).
  5. AUTOTESTE — com `--autoteste`: muta uma COPIA do registro e exige que o verificador REPROVE cada
                 mutacao. Mutacao que passa em silencio = buraco no verificador.

Uso:
    /opt/hermes/.venv/bin/python scripts/verificar_registro_de_aprovacoes.py
    ... --autoteste          # prova que o verificador pega as mutacoes que ele promete pegar
    ... --contagem           # tambem mede a contagem no commit citado (mais lento: usa worktree)
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
REGISTRO_PADRAO = RAIZ / "docs/operations/registro-de-aprovacoes.md"

RE_CAMINHO = re.compile(r"`(scripts/[A-Za-z0-9_./-]+\.py)[^`]*`")  # tolera flags depois do caminho, dentro das crases
RE_CONTAGEM = re.compile(r"(\d+)\s+itens")
RE_COMMIT = re.compile(r"commit\s+`([0-9a-f]{7,40})`")
RE_SHA = re.compile(r"sha256\s+`?([0-9a-f]{64})`?")

ITENS: list[tuple[str, bool, str]] = []


def item(nome: str, ok: bool, detalhe: str = "") -> None:
    ITENS.append((nome, ok, detalhe))
    print(f"{'OK   ' if ok else 'FALHA'} {nome}" + (f"  [{detalhe}]" if detalhe else ""))


def _git(*args: str, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd or RAIZ), capture_output=True, text=True)


def sha_do_blob(commit: str, caminho: str) -> str | None:
    p = _git("show", f"{commit}:{caminho}")
    if p.returncode != 0:
        return None
    return hashlib.sha256(p.stdout.encode("utf-8")).hexdigest()


def linhas_de_dados(texto: str) -> list[str]:
    out = []
    for linha in texto.splitlines():
        if not linha.startswith("|"):
            continue
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(celulas) < 3 or set("".join(celulas)) <= set("-: "):
            continue
        if celulas[0].lower().startswith("data"):
            continue
        out.append(linha)
    return out


def analisar(texto: str, com_contagem: bool) -> None:
    linhas = linhas_de_dados(texto)
    item("registro: linhas de aprovacao encontradas", len(linhas) > 0, f"{len(linhas)} linha(s)")

    for n, linha in enumerate(linhas, start=1):
        rotulo = f"linha {n}"
        caminhos = RE_CAMINHO.findall(linha)
        contagens = RE_CONTAGEM.findall(linha)
        commits = RE_COMMIT.findall(linha)
        shas = RE_SHA.findall(linha)

        if not caminhos:
            # linha sem verificador: nao ha o que ancorar
            item(f"{rotulo}: sem verificador citado (nada a verificar)", True)
            continue

        item(f"{rotulo}: cita verificador ({len(caminhos)} caminho(s), {len(contagens)} contagem(ns))", True)
        item(f"{rotulo}: cita commit + sha256", bool(commits) and bool(shas),
             f"{len(commits)} commit(s), {len(shas)} sha256")

        # pareamento: cada sha256 se liga ao commit imediatamente anterior NA LINHA
        eventos = []
        for m in re.finditer(r"commit\s+`([0-9a-f]{7,40})`|sha256\s+`?([0-9a-f]{64})`?", linha):
            eventos.append(("commit", m.group(1)) if m.group(1) else ("sha", m.group(2)))
        pares: list[tuple[str, str]] = []
        commit_corrente = None
        for tipo, valor in eventos:
            if tipo == "commit":
                commit_corrente = valor
            else:
                if commit_corrente is None:
                    item(f"{rotulo}: sha256 sem commit na mesma linha", False, valor[:12])
                    continue
                pares.append((commit_corrente, valor))
        if len(pares) != len(shas):
            item(f"{rotulo}: todo sha256 tem commit", False,
                 f"{len(shas) - len(pares)} orfao(s)")

        for caminho in sorted(set(caminhos)):
            reproduzido = False
            for commit, sha in pares:
                real = sha_do_blob(commit, caminho)
                if real is None:
                    continue
                if real == sha:
                    reproduzido = True
                    break
            item(f"{rotulo}: {caminho} reproduzido por (commit, sha256) citado", reproduzido,
                 f"{len(pares)} par(es) testados")

        # Cada par (commit, sha256) citado tem de reproduzir ALGUM caminho citado na linha: par errado
        # nao pode passar so porque outro par da mesma linha casa.
        for commit, sha in pares:
            ok_par = any(sha == sha_do_blob(commit, c) for c in caminhos)
            item(f"{rotulo}: par (commit {commit[:7]}, sha256 {sha[:12]}…) reproduz um caminho citado", ok_par)

        if com_contagem:
            if not contagens:
                item(f"{rotulo}: contagem medida no commit citado", True, "PULADO (linha sem contagem declarada)")
                continue
            for caminho in sorted(set(caminhos)):
                for commit, _sha in pares:
                    if sha_do_blob(commit, caminho) is None:
                        continue
                    medido = medir_contagem(commit, caminho)
                    ok = medido is not None and str(medido) in contagens
                    item(f"{rotulo}: contagem medida no commit {commit[:7]} consta da linha ({medido} medido; "
                         f"declarados: {','.join(contagens)})", ok, caminho)
        else:
            item(f"{rotulo}: contagem medida no commit citado", True, "PULADO (--contagem)")

    falhas = [i for i in ITENS if not i[1]]
    print()
    print(f"RESULTADO: {'PASS' if not falhas else 'FALHOU'} ({len(ITENS)} itens, {len(falhas)} falha(s))")


def medir_contagem(commit: str, caminho: str) -> int | None:
    """Roda o verificador no commit citado, em worktree temporario, e le a contagem de itens."""
    destino = pathlib.Path(tempfile.mkdtemp(prefix="regcheck_")) / "wt"
    add = _git("worktree", "add", "--detach", str(destino), commit)
    if add.returncode != 0:
        return None
    try:
        alvo = destino / caminho
        if not alvo.is_file():
            return None
        # nem toda suite aceita --autoteste: tenta a rodada simples primeiro e, se nao houver contagem
        # na saida, tenta com --autoteste.
        for extra in ([], ["--autoteste"]):
            p = subprocess.run([sys.executable, str(alvo), *extra], cwd=str(destino),
                               capture_output=True, text=True)
            m = re.search(r"\((\d+)\s+itens", p.stdout + p.stderr)
            if m:
                return int(m.group(1))
        return None
    finally:
        _git("worktree", "remove", "--force", str(destino))
        shutil.rmtree(destino.parent, ignore_errors=True)


def autoteste(registro: pathlib.Path) -> int:
    base = registro.read_text(encoding="utf-8")
    sha_v12 = "cbd00dd455c540c6f69af8f93e478c67c13941b0c3d2dd2afdbabb39b9b08e8a"
    mutacoes = {
        "sha256 da linha da v1.2 trocado por outro (nao reproduz no commit citado)":
            lambda t: t.replace(sha_v12, "0" * 64),
        "commit da linha da v1.2 removido (identidade vira contagem solta)":
            lambda t: t.replace("detectadas **no commit `1227310`**", "detectadas **em data incerta**", 1),
        "commit trocado por outro em que esse arquivo tem outro conteudo":
            lambda t: t.replace("**no commit `1227310`**", "**no commit `9a2b64c`**", 1),
        "verificador novo citado com contagem e sem ancora":
            lambda t: t + ("\n| 30/09/2026 | Anderson Ribeiro | MUTACAO DE TESTE | verificador "
                           "`scripts/verificar_jev_policy_v1_2.py` = 999 itens PASS |\n"),
    }
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="regmut_"))
    buracos = 0
    for n, (nome, mutar) in enumerate(mutacoes.items(), start=1):
        copia = tmp / f"registro_m{n}.md"
        copia.write_text(mutar(base), encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--registro", str(copia)],
            capture_output=True, text=True)
        reprovou = "FALHOU" in p.stdout
        print(f"{'OK   ' if reprovou else 'FALHOU'} detectada: {nome}"
              + ("" if reprovou else "  <-- buraco no verificador"))
        if not reprovou:
            buracos += 1
            cauda = (p.stdout + p.stderr).strip().splitlines()[-3:]
            for linha in cauda:
                print(f"        (saida da mutacao: {linha})")
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"AUTOTESTE: {len(mutacoes) - buracos}/{len(mutacoes)} mutacoes reprovadas")
    return buracos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registro", default=str(REGISTRO_PADRAO))
    ap.add_argument("--contagem", action="store_true", help="mede a contagem de itens no commit citado")
    ap.add_argument("--autoteste", action="store_true")
    args = ap.parse_args()

    registro = pathlib.Path(args.registro)
    if not registro.is_file():
        print(f"FAIL-CLOSED: registro ausente: {registro}")
        return 2
    print(f"registro: {registro}")
    print()
    analisar(registro.read_text(encoding="utf-8"), args.contagem)
    if args.autoteste:
        print()
        print("=== AUTOTESTE: mutacoes que o verificador precisa reprovar ===")
        return 1 if autoteste(registro) else 0
    return 0 if not [i for i in ITENS if not i[1]] else 1


if __name__ == "__main__":
    sys.exit(main())
