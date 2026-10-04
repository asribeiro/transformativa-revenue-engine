#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador da CLASSE do D04-D01: ponteiro de commit citado em doc de REGISTRO.

DEFEITO DA CLASSE (`t_44cbc48c` / D04-D01; reincidiu em `t_26be11c7`): doc de registro cita hash
de commit; o objeto envelhece (amend, rebase, republicacao) e passa a nao ser alcancado por ref
nenhuma. Quem le o doc nao consegue conferir a citacao — e nenhum verificador do repo olhava
para isso: `scripts/verificar_estrutura.sh` confere que o ARQUIVO existe e esta versionado, mas
nao resolve hash contra o repositorio. Os dois defeitos da classe foram achados por varredura
MANUAL do revisor/autor, nao pela esteira do card: e isso que este verificador fecha.

O QUE ELE FAZ
  Varre os docs de registro (`docs/operations/*.md` e `docs/runbooks/*.md` por default), extrai
  cada identificador hexadecimal de 7..40 caracteres e resolve contra o repositorio:
    1. existe?             -> `git rev-parse --disambiguate` + `git cat-file -t`
    2. e commit/tree/blob? -> `git cat-file -t <token>`
    3. esta em alguma ref? -> `git for-each-ref --contains <token>`

REGRA DE REPROVACAO (o dente)
  Identificador que resolve para **commit** e **nao** esta contido em ref nenhuma = PONTEIRO
  MORTO. Ponteiro morto cuja **unidade do doc** (item de lista, linha de tabela ou paragrafo —
  a unidade inteira, incluindo continuacao de linha) **nao** trouxer uma MARCA explicita de que o
  objeto nao se alcanca mais -> **FALHA**, exit 1.
  Citacao historica devidamente qualificada PASSA: o verificador nao pode exigir apagar historia;
  ele exige que a historia nao seja vendida como ponteiro conferivel hoje.

MARCAS aceitas (o contrato; ver `RE_MARCA`):
  `fora de ref` / `fora de qualquer ref`, `nao se alcanca por ref` (com ou sem acento),
  `inalcancavel por ref`, `ERRATA DE PONTEIRO`.

NAO REPROVAR POR FALSO POSITIVO
  * `sha256` de conteudo (64 hex) e identidade de conteudo, **nao** ponteiro de commit;
  * identificador truncado seguido de `...`/`…` (ex. `e4e1f05d…`) e fragmento: classificado;
  * prefixo ambiguo (casa com mais de um objeto) e identificador sem objeto no repositorio (typo,
    fragmento, hash de outro clone, id de card) : classificados — nao se afirma "ponteiro morto"
    sobre o que nao se pode resolver.
  `--verboso` lista cada ocorrencia dessas classes; sem ele, elas saem agregadas por categoria
  (o resumo diz quantos identificadores distintos ficaram sem resolucao, para auditoria).

Uso:
    /opt/hermes/.venv/bin/python scripts/verificar_ponteiros_de_registro.py
    ... --raiz /caminho/da/arvore      # varre os docs de OUTRA arvore (dente antes/depois)
    ... --docs 'docs/operations/*.md'  # troca a janela de varredura (caminho ou glob; repetivel)
    ... --verboso                      # lista todas as ocorrencias classificadas e os vivos
    ... --autoteste                    # prova de MUTACAO: planta ponteiro morto NOVO (com e sem
                                       # marca, em mesma e em outra unidade) e exige cada veredito

Saida: `OK`/`FALHA` por item reprovavel, `INFO` para o que so classifica;
       `RESULTADO: PONTEIROS_OK|PONTEIROS_FALHOU (N itens, F falha(s), ...)`.
Exit: 0 = passou; 1 = reprovou (ou nada foi provado); 2 = FAIL-CLOSED (sem repo git / janela vazia).

O teste deste verificador (`scripts/teste_ponteiros_de_registro.sh`) roda o dente antes/depois
contra as arvores congeladas da classe; os dois estao cobertos por `scripts/verificar_estrutura.sh`
(licao do defeito `t_5cad1689`: artefato fora do verificador de estrutura envelhece sem ninguem ver).
"""

from __future__ import annotations

import argparse
import glob as globmod
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

RAIZ_PADRAO = pathlib.Path(__file__).resolve().parent.parent
DOCS_PADRAO = ("docs/operations/*.md", "docs/runbooks/*.md")

# Unidade de citacao: item de lista, linha de tabela, titulo, citacao ou paragrafo. A marca de
# qualificacao tem de estar na MESMA unidade da citacao.
RE_NOVA_UNIDADE = re.compile(r"^\s*(-\s|\*\s|\+\s|\d+\.\s|\||>\s?|#{1,6}\s)")

# Identificador hex: 7+ caracteres, cercado por nao-alfanumerico dos dois lados (nao casa pedaco de
# palavra, ex. `vmi3619453` ou `20260929T1937`). O corte por tamanho e feito na classificacao.
RE_HEX = re.compile(r"(?<![0-9A-Za-z])([0-9a-f]{7,})(?![0-9A-Za-z])")
RE_TRUNCADO = re.compile(r"^\s*(…|\.\.\.)")
RE_MARCA = re.compile(
    r"fora de ref|fora de qualquer ref|n[aã]o se alcan[cç]a por ref|"
    r"inalcan[cç][aá]vel por ref|errata de ponteiro",
    re.IGNORECASE,
)

ITENS: list[tuple[str, bool, str]] = []
OCORRENCIAS: dict[str, list[tuple[str, str]]] = {"vivo": [], "classificado": []}
CLASSES: Counter = Counter()
MORTOS: list[tuple[str, bool, str]] = []  # (rotulo, marcado, token)


def item(nome: str, ok: bool, detalhe: str = "") -> None:
    ITENS.append((nome, ok, detalhe))
    print(f"{'OK   ' if ok else 'FALHA'} {nome}" + (f"  [{detalhe}]" if detalhe else ""))


def info(texto: str) -> None:
    print(f"INFO  {texto}")


def _run(cmd: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


class Repo:
    """Resolucao de identificadores contra o repositorio (com cache por token)."""

    def __init__(self, raiz: pathlib.Path):
        self.raiz = raiz
        self._cache: dict[str, tuple[str, list[str]]] = {}

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return _run(["git", *args], self.raiz)

    def esta_em_alguma_ref(self, tok: str) -> list[str]:
        p = self.git("for-each-ref", "--contains", tok, "--count=10000", "--format=%(refname)")
        if p.returncode != 0:
            return []
        return [ln for ln in p.stdout.splitlines() if ln.strip()]

    def resolver(self, tok: str) -> tuple[str, list[str]]:
        """-> (classe, refs). classe: commit|tree|blob|tag|ambiguo|ausente."""
        if tok in self._cache:
            return self._cache[tok]
        cls, refs = "ausente", []
        dis = self.git("rev-parse", "--disambiguate=" + tok)
        candidatos = [ln for ln in dis.stdout.splitlines() if ln.strip()]
        if len(candidatos) > 1:
            cls = "ambiguo"
        else:
            t = self.git("cat-file", "-t", tok)
            if t.returncode == 0:
                cls = t.stdout.strip()
                if cls == "commit":
                    refs = self.esta_em_alguma_ref(tok)
            elif "ambiguous" in (t.stderr + t.stdout).lower():
                cls = "ambiguo"
        self._cache[tok] = (cls, refs)
        return cls, refs


def unidades(caminho: pathlib.Path):
    """Agrupa o texto em unidades verificaveis, com a linha inicial de cada uma."""
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    buf: list[str] = []
    inicio = 1
    for i, linha in enumerate(linhas, 1):
        if not linha.strip():
            if buf:
                yield inicio, "\n".join(buf)
            buf, inicio = [], 0
            continue
        if buf and RE_NOVA_UNIDADE.match(linha):
            yield inicio, "\n".join(buf)
            buf, inicio = [], 0
        if not buf:
            inicio = i
        buf.append(linha)
    if buf:
        yield inicio, "\n".join(buf)


def fragmento(unidade: str, tok: str, limite: int = 100) -> str:
    """Trecho da unidade em volta da citacao, para o humano achar a linha sem abrir o arquivo."""
    pos = unidade.find(tok)
    if pos < 0:
        return ""
    ini = max(0, pos - limite // 3)
    trecho = unidade[ini:ini + limite].replace("\n", " ⏎ ")
    return ("…" if ini else "") + trecho + "…"


def classe_do_nao_resolvido(cls: str, tok: str, truncado: bool) -> str:
    if cls == "ambiguo":
        return "prefixo ambiguo (casa com mais de um objeto)"
    if truncado:
        return "fragmento truncado (seguido de …/...) sem objeto no repositorio"
    return "sem objeto no repositorio (typo, fragmento, id de card ou hash de outro clone)"


def varrer(repo: Repo, docs: list[pathlib.Path], verboso: bool) -> int:
    examinados: set[str] = set()
    for doc in docs:
        rel = os.path.relpath(doc, repo.raiz)
        mortos_doc = vivos_doc = 0
        for linha_ini, unidade in unidades(doc):
            # uma ocorrencia por (unidade, token): a mesma citacao repetida no item/paragrafo nao
            # vira N itens (o que importa e se a UNIDADE qualifica a citacao).
            for tok in sorted(set(RE_HEX.findall(unidade))):
                examinados.add(tok)
                pos = unidade.find(tok)
                truncado = bool(RE_TRUNCADO.match(unidade[pos + len(tok):]))
                rotulo = f"{rel}:{linha_ini}"
                if len(tok) > 40:
                    classe = (f"sha256 de conteudo ({len(tok)} hex) — identidade, nao ponteiro"
                              if len(tok) == 64 else
                              f"hex de {len(tok)} caracteres — nem ponteiro de commit nem sha256")
                    CLASSES[classe.split(" (")[0].split(" — ")[0]] += 1
                    OCORRENCIAS["classificado"].append((f"{rotulo} {tok}", classe))
                    continue
                cls, refs = repo.resolver(tok)
                if cls == "commit" and not refs:
                    marcado = bool(RE_MARCA.search(unidade))
                    MORTOS.append((rotulo, marcado, tok))
                    mortos_doc += 1
                    base = f"{rotulo} {tok} ponteiro morto (commit fora de ref nenhuma)"
                    if marcado:
                        item(f"{base} MARCADO", True, fragmento(unidade, tok, 80))
                    else:
                        item(f"{base} SEM MARCA", False,
                             "a unidade cita commit que nao se alcanca por ref nenhuma e nao traz "
                             "marca ('fora de ref' / 'nao se alcanca por ref' / "
                             "'ERRATA DE PONTEIRO')")
                elif cls == "commit":
                    vivos_doc += 1
                    OCORRENCIAS["vivo"].append((f"{rotulo} {tok}", f"{len(refs)} ref(s) contem"))
                elif cls in ("tree", "blob", "tag"):
                    OCORRENCIAS["classificado"].append(
                        (f"{rotulo} {tok}", f"objeto {cls} (nao e ponteiro de commit)"))
                    CLASSES[f"objeto {cls} (nao e ponteiro de commit)"] += 1
                else:
                    classe = classe_do_nao_resolvido(cls, tok, truncado)
                    OCORRENCIAS["classificado"].append((f"{rotulo} {tok}", classe))
                    CLASSES[classe.split(" (")[0]] += 1
        if mortos_doc or vivos_doc:
            info(f"{rel}: {mortos_doc} ponteiro(s) morto(s), {vivos_doc} vivo(s)")
    return len(examinados)


def analisar(raiz: pathlib.Path, padroes: list[str], verboso: bool) -> int:
    print(f"raiz: {raiz}")
    topo = _run(["git", "rev-parse", "--show-toplevel"], raiz)
    if topo.returncode != 0:
        print(f"FAIL-CLOSED: {raiz} nao esta dentro de um repositorio git — nao ha objeto contra o "
              f"qual resolver os identificadores")
        return 2
    print(f"repo: {topo.stdout.strip()}")
    print(f"docs: {' '.join(padroes)}")
    print()

    docs: list[pathlib.Path] = []
    for padrao in padroes:
        alvo = raiz / padrao
        achados = sorted(pathlib.Path(p) for p in globmod.glob(str(alvo)))
        if not achados and alvo.is_file():
            achados = [alvo]
        docs.extend(a for a in achados if a.is_file())
    docs = sorted(set(docs))

    if not docs:
        print(f"FAIL-CLOSED: janela de varredura vazia ({' '.join(padroes)}) — glob errado nao vira "
              f"PASS vazio")
        return 2

    repo = Repo(raiz)
    item(f"docs varridos: {len(docs)} arquivo(s)", True, " ".join(padroes))

    encontrados = varrer(repo, docs, verboso)

    examinados = len(repo._cache)
    item(f"identificadores examinados nos docs: {encontrados} distinto(s) "
         f"({examinados} resolvido(s) contra o repositorio)",
         encontrados > 0, "" if encontrados else "nada foi provado com 0 identificador")

    print()
    if verboso:
        for chave in ("vivo", "classificado"):
            for rotulo, classe in OCORRENCIAS[chave]:
                info(f"{chave:<12} {rotulo} -> {classe}")
    info(f"ponteiros vivos (commit contido em ref, nao reprovam): {len(OCORRENCIAS['vivo'])} "
         f"ocorrencia(s), {len({r.split(' ')[1] for r, _ in OCORRENCIAS['vivo']})} token(s) "
         f"distinto(s)")
    info(f"classificados (nao reprovam): {len(OCORRENCIAS['classificado'])} ocorrencia(s), "
         f"{len({r.split(' ')[1] for r, _ in OCORRENCIAS['classificado']})} token(s) distinto(s)")
    for classe, n in CLASSES.most_common():
        info(f"    {n:>4}x {classe}")

    mortos_marcados = sum(1 for _, m, _ in MORTOS if m)
    falhas = [i for i in ITENS if not i[1]]
    print()
    veredito = "PONTEIROS_OK" if not falhas else "PONTEIROS_FALHOU"
    print(f"RESULTADO: {veredito} ({len(ITENS)} itens, {len(falhas)} falha(s), "
          f"{len(MORTOS)} ponteiro(s) morto(s) citado(s) — {mortos_marcados} marcado(s), "
          f"{len(MORTOS) - mortos_marcados} sem marca)")
    return 1 if falhas else 0


# ---------------------------------------------------------------------------------------------
# Prova de MUTACAO (--autoteste): planta ponteiro morto NOVO no doc e exige o veredito certo.
# Nao copia ponteiro morto historico (o objeto pode sair do object DB): o commit e CRIADO aqui,
# dangling, por `git commit-tree` — existe no repositorio e nao esta em ref nenhuma, por construcao.
# ---------------------------------------------------------------------------------------------
def commit_dangling(raiz: pathlib.Path) -> str | None:
    """Cria um commit sem ref nenhuma (dangling) para a mutacao. O objeto fica no object DB do
    repositorio e e alcancado so por hash — como qualquer ponteiro morto real achado em doc; o
    `gc` normal poda (nao ha ref, nem reflog, guardando o objeto)."""
    arvore = _run(["git", "rev-parse", "HEAD^{tree}"], raiz)
    if arvore.returncode != 0:
        return None
    env = dict(os.environ, GIT_AUTHOR_NAME="mutacao", GIT_AUTHOR_EMAIL="mutacao@teste.invalid",
               GIT_COMMITTER_NAME="mutacao", GIT_COMMITTER_EMAIL="mutacao@teste.invalid")
    p = subprocess.run(["git", "commit-tree", arvore.stdout.strip(), "-m",
                        "mutacao do verificador de ponteiros (dangling)"],
                       cwd=str(raiz), capture_output=True, text=True, env=env)
    return p.stdout.strip() if p.returncode == 0 else None


def commit_em_ref(raiz: pathlib.Path) -> str | None:
    p = _run(["git", "for-each-ref", "--count=1", "--format=%(objectname)", "refs/heads"], raiz)
    return p.stdout.strip() or None


def autoteste(raiz: pathlib.Path, doc_base: pathlib.Path) -> int:
    morto = commit_dangling(raiz)
    vivo = commit_em_ref(raiz)
    if not morto or not vivo:
        print("FAIL-CLOSED: nao consegui preparar as mutacoes (commit dangling / commit em ref)")
        return 1
    sha256_falso = "9d" * 32
    base = doc_base.read_text(encoding="utf-8")
    mutacoes: list[tuple[str, str, int]] = [
        ("ponteiro morto novo, SEM marca -> tem de REPROVAR",
         f"\n- Publicacao de teste medida no commit `{morto}` (306 arquivos).\n", 1),
        ("ponteiro morto novo, sem crase e SEM marca -> tem de REPROVAR",
         f"\n- Publicacao de teste medida no commit {morto} (306 arquivos).\n", 1),
        ("ponteiro morto novo COM marca na mesma unidade -> tem de PASSAR",
         f"\n- Publicacao de teste medida no commit `{morto}` (**hoje fora de ref nenhuma**).\n", 0),
        ("marca em OUTRA unidade (paragrafo seguinte) -> tem de REPROVAR",
         f"\n- Publicacao de teste medida no commit `{morto}` (306 arquivos).\n"
         f"\nERRATA DE PONTEIRO: a citacao acima e historica.\n", 1),
        ("sha256 de conteudo (64 hex) sem marca -> tem de PASSAR (falso positivo)",
         f"\n- Conteudo conferido por sha256 `{sha256_falso}` identico ao repositorio.\n", 0),
        ("ponteiro VIVO (commit contido em ref) sem marca -> tem de PASSAR (falso positivo)",
         f"\n- Head entregue desta arvore: commit `{vivo}`.\n", 0),
    ]

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ponteiros_mut_"))
    buracos = 0
    try:
        print()
        print("=== AUTOTESTE: mutacoes do doc de registro ===")
        print(f"doc base: {doc_base}")
        print(f"commit dangling criado para a mutacao: {morto} (fora de ref nenhuma, por construcao)")
        for n, (nome, sufixo, esperado) in enumerate(mutacoes, start=1):
            copia = tmp / f"doc_m{n}.md"
            copia.write_text(base + sufixo, encoding="utf-8")
            p = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()),
                                "--raiz", str(raiz), "--docs", str(copia)],
                               capture_output=True, text=True)
            ok = p.returncode == esperado
            print(f"{'OK   ' if ok else 'FALHOU'} mutacao {n}: {nome} (exit {p.returncode}, "
                  f"esperado {esperado})")
            if not ok:
                buracos += 1
                for ln in (p.stdout + p.stderr).strip().splitlines()[-3:]:
                    print(f"        (saida: {ln})")
        print()
        print(f"AUTOTESTE: {len(mutacoes) - buracos}/{len(mutacoes)} mutacoes com o veredito esperado")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return buracos


def main() -> int:
    ap = argparse.ArgumentParser(description="ponteiro de commit em doc de registro: existe? e "
                                             "commit? esta em alguma ref?")
    ap.add_argument("--raiz", default=str(RAIZ_PADRAO), help="arvore cujos docs sao varridos")
    ap.add_argument("--docs", action="append", default=None,
                    help=f"caminho ou glob do doc (repetivel; default: {' '.join(DOCS_PADRAO)})")
    ap.add_argument("--verboso", action="store_true",
                    help="lista cada ocorrencia viva/classificada (default: agregado por classe)")
    ap.add_argument("--autoteste", action="store_true",
                    help="planta ponteiro morto novo e exige o veredito de cada mutacao")
    args = ap.parse_args()

    raiz = pathlib.Path(args.raiz).resolve()
    padroes: list[str] = list(args.docs) if args.docs else list(DOCS_PADRAO)

    codigo = analisar(raiz, padroes, args.verboso)
    if args.autoteste:
        base = [raiz / "docs/operations/registro-de-execucoes.md"]
        if not base[0].is_file():
            base = sorted(pathlib.Path(p) for p in globmod.glob(str(raiz / DOCS_PADRAO[0])))
        if not base:
            print("FAIL-CLOSED: nenhum doc base para o autoteste")
            return 2
        if autoteste(raiz, base[0]):
            return 1
    return codigo


if __name__ == "__main__":
    sys.exit(main())
