#!/usr/bin/env python3
"""COMPOE as customizacoes do plugin kanban do dashboard (`plugin_api.py`).

Cadeia (a ordem e' obrigatoria; cada delta exige a forma do anterior):

  1. `deploy/hermes/projecao-entrega/editar_plugin_api_delivery_root.py`
     (card TRE-W0-E04-T11 / `t_b74c2edd`): a raiz do registro de entregas passa a ser
     DECLARADA (`delivery_repo` no board.json > env `HERMES_DELIVERIES_ROOT` > legado
     documentado). Fim do caminho fixo de OUTRO projeto dentro do helper.
  2. DELTA 2 — filtro de snapshots (`_artifact_files`): escrita em RUNTIME em 02/10/2026,
     sem card que a versionasse e ate' 05/10/2026 existente SO na copia em uso. O glob
     tratava `*.bak-*.json` como entrega vigente: um snapshot com `current_gate: VALIDATION`
     estacionava 4 cards na coluna de validacao para sempre.
  3. DELTA 3 — coluna `production` ("Em producao (entrega)", 05/10/2026): card `done` cuja
     evidencia terminal e' a PROMOCAO do release (`release_status: PRODUCTION_PROMOTED` +
     `production_promotion_authorized: true`, ids em `production_promoted_task_ids`).
     Projecao do artefato de entrega, como validation/candidate/human_approval.

Por que existe: medido em 05/10/2026, a copia em USO divergia do canonico versionado
(sha 0ed3674f x cef44123) ANTES da coluna — o delta 2 nunca foi versionado e nenhum card
o cobria. Edicao de runtime nao sobrevive a troca de imagem do Hermes.

Uso:
    compor_plugin_api.py --alvo <plugin_api.py>              # aplica (SECO por padrao)
    compor_plugin_api.py --alvo <plugin_api.py> --aplicar
    compor_plugin_api.py --alvo <plugin_api.py> --reverter [--aplicar]
    compor_plugin_api.py --alvo <plugin_api.py> --check      # so relata o sha
    compor_plugin_api.py --canonico-destino <arquivo>        # baseline -> editor T11 -> deltas
    compor_plugin_api.py --autoteste                         # ida-e-volta em memoria

Canonico composto (baseline + 1 + 2 + 3): 634389875f6f174f8d3aebe33155b0c06aaaa131fec876897867841cc6864d17
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

SHA_CANONICO = "634389875f6f174f8d3aebe33155b0c06aaaa131fec876897867841cc6864d17"
# Frontend: `dist/index.js` e' distribuido SEM fonte no repo (nao ha src/, nao ha build
# reproducible daqui). A forma versionada e' o patch. Medido em 05/10/2026:
#   imagem do Hermes : 5b309b30... (sem pt-BR nas colunas, sem `production`)
#   em uso           : 3b27d66f... (relabel pt-BR anterior + coluna `production`)
SHA_BUNDLE_IMAGEM = "5b309b30e966c151ebf5bfac882610bdf42f1693459b85e8a87995be5279e1f8"
SHA_BUNDLE_CANONICO = "3b27d66fbd263936e7aa5904dd8480d843b38a1a775528cdfda183d32738bddd"
PATCH_BUNDLE = pathlib.Path(__file__).with_name("dist_index.patch")
SHA_BASELINE = "a0d99603463b9188a8cf67adacbfcabad1c8d2d40a02f7c5d81cf4a6674472ef"

# A cadeia por ESTADO (medida em 05/10/2026). Cada passo e' verificado pelo sha do
# resultado: se nao cair exatamente no esperado, nada e' gravado. Estado desconhecido
# => ABORTA. E' isto que torna a reaplicacao segura (idempotente por construcao, nao
# por heuristica): S2 -> S3 e' no-op, S3 -> S3 e' no-op.
SHA_S0 = SHA_BASELINE         # baseline empacotado na imagem (sem delta nenhum)
SHA_S1 = "cef4412392c89bdcf51cf4500bb19dea8c4b2e4930c33a3e89d50b58f25eacba"  # delta 1
SHA_S2 = "0ed3674f206932bb8aaef8161d72103d8ebca09c3c57cad72f3cb6b769c1672c"  # + delta 2
SHA_S3 = SHA_CANONICO         # + delta 3 (coluna production) = a copia em uso

RAIZ_REPO = pathlib.Path(__file__).resolve().parents[3]
EDITOR_T11 = RAIZ_REPO / "deploy" / "hermes" / "projecao-entrega" / "editar_plugin_api_delivery_root.py"
BASELINE = RAIZ_REPO / "deploy" / "hermes" / "projecao-entrega" / "baseline" / "plugin_api.pristina.py"

# ---- DELTA 2: filtro de snapshots (02/10/2026, sem card) -------------------
DELTA2 = [
    # edicao 1 (x4) — for delivery_file in sorted(deliveries_dir.glob("*.json")):
    ("""    for delivery_file in sorted(deliveries_dir.glob("*.json")):
""",
     """    for delivery_file in _artifact_files(deliveries_dir):
""",
     4),
    # edicao 2 (x1) — def _delivery_lifecycle_task_columns(
    ("""def _delivery_lifecycle_task_columns(
    repo_root: Optional[Path] = None,
""",
     '_SNAPSHOT_MARCADORES = (".bak-", ".bak.", ".orig", ".old", "~", ".tmp", ".swp", ".save")\n\n\ndef _artifact_files(deliveries_dir: Path) -> list:\n    """Artefatos de entrega vigentes: um por ``delivery_id``, o mais recente.\n\n    Ignora snapshots/backups que estejam na pasta. Medido em 02/10/2026: um\n    `.bak-*.json` com `current_gate: VALIDATION` estacionava 4 cards na coluna de\n    validação para sempre, porque o glob tratava o snapshot como entrega vigente.\n    """\n    import json as _json\n\n    melhores: dict = {}\n    sem_id: list = []\n    for f in sorted(deliveries_dir.glob("*.json")):\n        if any(m in f.name.lower() for m in _SNAPSHOT_MARCADORES):\n            continue\n        try:\n            d = _json.loads(f.read_text())\n        except Exception:\n            continue\n        did = str(d.get("delivery_id") or "").strip()\n        if not did:\n            sem_id.append(f)\n            continue\n        atual = str(d.get("updated_at") or "")\n        if did not in melhores or atual > melhores[did][0]:\n            melhores[did] = (atual, f)\n    return sorted([p for _, p in melhores.values()] + sem_id)\n\n\ndef _delivery_lifecycle_task_columns(\n    repo_root: Optional[Path] = None,\n',
     1),
    # edicao 3 (x1) — for t in tasks:
    ("""        for t in tasks:
            full = summary_map.get(t.id)
""",
     """        production_ids = _delivery_production_done_task_ids(board=board)
        for t in tasks:
            full = summary_map.get(t.id)
""",
     1),
]

# ---- DELTA 3: coluna production (05/10/2026) ------------------------------
DELTA3 = [
    # edicao 1 (x1) — "done",
    ("""    "done",
]
""",
     """    # Promocao de release: item DONE e release PRODUCTION_PROMOTED no artefato.
    "production",
    "done",
]
""",
     1),
    # edicao 2 (x1) — col = (
    ("""            col = (
                projected
""",
     """            # Promocao a producao: evidencia terminal mais forte que existe.
            # Sai da coluna nativa 'done' e ganha a coluna propria -- visivel.
            if t.status == "done" and t.id in production_ids:
                projected = "production"

            col = (
                projected
""",
     1),
    # edicao 3 (x1) — if projected in {"validation", "candidate", "human_approval"
    ("""                if projected in {"validation", "candidate", "human_approval"}
""",
     """                if projected in {"validation", "candidate", "human_approval", "production"}
""",
     1),
    # edicao 4 (x1) — columns[col].append(d)
    ("""
            columns[col].append(d)
""",
     """            d["delivery_production"] = col == "production"

            columns[col].append(d)
""",
     1),
]


def sha256_texto(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _editar(texto: str, edicoes, *, reverter: bool = False) -> str:
    """Aplica (ou desfaz) edicoes ancoradas: cada ancora exige a CONTAGEM exata.

    NAO ha heuristica de "ja aplicado" aqui de proposito. Medido em 05/10/2026: num
    delta cujo texto de destino CONTEM a ancora (todo sufixo compartilhado), o teste
    "destino presente e ancora ausente" da falso e a edicao se reaplica — foi assim que
    a coluna `production` foi duplicada na primeira versao deste script. Quem decide se
    ja esta aplicado e' o ESTADO do arquivo (sha), em `compor`.
    """
    for i, (de, para, esperado) in enumerate(edicoes, 1):
        alvo, novo = (para, de) if reverter else (de, para)
        n = texto.count(alvo)
        if n != esperado:
            raise AssertionError(
                f"edicao {i}: ancora casou {n}x (esperado {esperado}x) — o plugin mudou de "
                f"forma; NAO editei. Ancora comeca com: {alvo[:70]!r}"
            )
        texto = texto.replace(alvo, novo)
    return texto


def _passo(texto: str, edicoes, sha_antes: str, sha_depois: str,
           *, reverter: bool = False) -> str:
    """Um elo da cadeia: so age se o sha de ORIGEM casar, e exige o sha de DESTINO."""
    atual = sha256_texto(texto)
    destino = sha_antes if reverter else sha_depois
    origem = sha_depois if reverter else sha_antes
    if atual == destino:
        return texto
    if atual != origem:
        raise AssertionError(
            f"estado inesperado: sha {atual[:16]}, esperado {origem[:16]}. Nada gravado — "
            "o arquivo nao e' nenhum dos estados da cadeia; revisar, NAO adivinhar."
        )
    novo = _editar(texto, edicoes, reverter=reverter)
    if sha256_texto(novo) != destino:
        raise AssertionError(
            f"o passo nao caiu no sha esperado: {sha256_texto(novo)[:16]} != {destino[:16]}. "
            "As ancoras casaram mas o resultado difere — NADA gravado."
        )
    return novo


NOMES = {
    SHA_S0: "S0 baseline (imagem, sem delta)",
    SHA_S1: "S1 delta 1 (card T11)",
    SHA_S2: "S2 delta 1+2 (pre-coluna `production`)",
    SHA_S3: "S3 delta 1+2+3 (canonico, = copia em uso)",
}


def estado(texto: str) -> str:
    return NOMES.get(sha256_texto(texto), "DESCONHECIDO")


def compor(texto: str, *, reverter: bool = False) -> str:
    """Aplica (ou desfaz) os deltas 2 e 3 — sempre por ESTADO, nunca por heuristica.

    Sem `reverter`: aceita S1 (aplica 2 e 3), S2 (aplica 3) ou S3 (no-op).
    Com `reverter`: desfaz 3 e depois 2 (S3 -> S2 -> S1).
    Qualquer outro sha => erro, sem gravar nada.
    """
    if reverter:
        texto = _passo(texto, DELTA3, SHA_S2, SHA_S3, reverter=True)
        return _passo(texto, DELTA2, SHA_S1, SHA_S2, reverter=True)
    s = sha256_texto(texto)
    if s == SHA_S3:
        return texto
    if s == SHA_S2:
        return _passo(texto, DELTA3, SHA_S2, SHA_S3)
    if s == SHA_S1:
        return _passo(_passo(texto, DELTA2, SHA_S1, SHA_S2), DELTA3, SHA_S2, SHA_S3)
    raise AssertionError(
        f"estado desconhecido: sha {s[:16]} nao e' nenhum dos 4 da cadeia "
        f"(S0={SHA_S0[:8]} S1={SHA_S1[:8]} S2={SHA_S2[:8]} S3={SHA_S3[:8]}). Nada gravado — "
        "o plugin mudou de forma; revisar, NAO adivinhar."
    )


def canonico() -> str:
    """baseline -> editor do card T11 -> deltas 2 e 3 (com o sha da baseline conferido)."""
    base = BASELINE.read_text(encoding="utf-8")
    if sha256_texto(base) != SHA_BASELINE:
        raise AssertionError(f"baseline mudou: {sha256_texto(base)[:16]} != {SHA_BASELINE[:16]}")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="compor_t11_")) / "api.py"
    tmp.write_text(base, encoding="utf-8")
    r = subprocess.run([sys.executable, str(EDITOR_T11), "--alvo", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"editor do T11 falhou: {(r.stdout + r.stderr).strip()[:300]}")
    return compor(tmp.read_text(encoding="utf-8"))


def _valida(texto: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(texto)
        tmp = pathlib.Path(fh.name)
    try:
        r = subprocess.run([sys.executable, "-m", "py_compile", str(tmp)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"py_compile reprovou: {(r.stderr or r.stdout).strip()[:300]}")
    finally:
        tmp.unlink(missing_ok=True)


def _estamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def autoteste() -> int:
    canon = canonico()
    sha = sha256_texto(canon)
    _valida(canon)
    print(f"canonico composto : {sha}")
    print(f"sha esperado      : {SHA_CANONICO}")
    ok = sha == SHA_CANONICO

    # REAPLICACAO — o defeito medido em 05/10/2026 (a coluna foi duplicada porque o
    # "ja aplicado" era heuristica de texto). Agora e' no-op por construcao.
    reaplicado = compor(canon)
    print("reaplicacao       : " + ("NO-OP (correto)" if reaplicado == canon else "DUPLICOU (defeito)"))
    ok = ok and reaplicado == canon

    # IDA-E-VOLTA por estado: S3 -> S1 (desfaz os dois deltas) e volta; e o elo
    # intermediario S2, que e' o estado em que a copia em uso estava antes da coluna.
    meio = _passo(canon, DELTA3, SHA_S2, SHA_S3, reverter=True)
    print(f"desfazer delta 3  : {sha256_texto(meio)[:16]} "
          + ("OK (S2)" if sha256_texto(meio) == SHA_S2 else "DIVERGENTE do sha S2"))
    ok = ok and sha256_texto(meio) == SHA_S2
    s1 = compor(canon, reverter=True)
    print(f"desfazer 3 e 2    : {sha256_texto(s1)[:16]} "
          + ("OK (S1)" if sha256_texto(s1) == SHA_S1 else "DIVERGENTE do sha S1"))
    ok = ok and sha256_texto(s1) == SHA_S1
    volta = compor(compor(s1))
    print("reaplicar S1->S3  : " + ("IDENTICA" if volta == canon else "DIVERGENTE"))
    ok = ok and volta == canon

    # estado DESCONHECIDO tem de abortar (nao adivinhar)
    try:
        compor(canon + "\n# lixo\n")
        print("estado desconhecido: ACEITOU (errado)")
        ok = False
    except AssertionError:
        print("estado desconhecido: ABORTA (correto)")

    print("AUTOTESTE=" + ("OK" if ok else "FALHOU"))
    return 0 if ok else 1


def bundle(raiz: pathlib.Path, *, check=False, aplicar=False, reverter=False) -> int:
    """Frontend: `dist/index.js` e' distribuido SEM fonte no repo.

    O bundle em uso nao e' o da imagem: ele tem o relabel pt-BR das colunas (drift de
    runtime anterior) e a coluna `production`. A forma versionada dele e' o patch
    (`dist_index.patch`, pristino -> canonico). Recusa agir se o sha nao for nem o da
    imagem nem o canonico -- bundle de terceiro nao se adivinha.
    """
    alvo = raiz / "plugins/kanban/dashboard/dist/index.js"
    if not alvo.is_file():
        print(f"PULADO ausente: {alvo}")
        return 0
    sha = sha256_texto(alvo.read_text(encoding="utf-8"))
    if check:
        estado = ("CANONICO" if sha == SHA_BUNDLE_CANONICO
                  else "IMAGEM (pristino)" if sha == SHA_BUNDLE_IMAGEM else "DIVERGENTE")
        print(f"bundle: {alvo}")
        print(f"sha   : {sha}")
        print(f"imagem: {SHA_BUNDLE_IMAGEM}")
        print(f"canonico: {SHA_BUNDLE_CANONICO}")
        print("VEREDITO=" + estado)
        return 0 if sha == SHA_BUNDLE_CANONICO else 1

    if sha == SHA_BUNDLE_CANONICO and not reverter:
        print(f"OK    bundle ja canonico: {alvo}")
        return 0
    if sha not in {SHA_BUNDLE_IMAGEM, SHA_BUNDLE_CANONICO}:
        print(f"ABORTA: bundle {alvo} sha256={sha[:16]} nao e' nem o da imagem nem o "
              f"canonico — o frontend mudou de forma; revisar o patch, NAO adivinhar")
        return 1

    args = ["patch", "-p1", "-s", "--forward", "-i", str(PATCH_BUNDLE)]
    if reverter:
        args.append("-R")
    if not aplicar:
        r = subprocess.run(args + ["--dry-run"], cwd=raiz, capture_output=True, text=True)
        print(("(seco) aplicaria " if r.returncode == 0 else "(seco) NAO aplicaria ")
              + f"{alvo}" + ("" if r.returncode == 0 else f": {r.stderr.strip()}"))
        return 0 if r.returncode == 0 else 1
    r = subprocess.run(args, cwd=raiz, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FALHOU patch do bundle: {r.stderr.strip()}")
        return 1
    print(f"gravado {alvo} sha256={sha256_texto(alvo.read_text(encoding='utf-8'))}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alvo")
    ap.add_argument("--canonico-destino")
    ap.add_argument("--bundle-raiz", help="raiz do plugin (ex.: /opt/data) para o dist/index.js")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--reverter", action="store_true")
    ap.add_argument("--autoteste", action="store_true")
    a = ap.parse_args(argv)

    if a.autoteste:
        return autoteste()

    if a.bundle_raiz:
        return bundle(pathlib.Path(a.bundle_raiz), check=a.check,
                      aplicar=a.aplicar, reverter=a.reverter)

    if a.canonico_destino:
        destino = pathlib.Path(a.canonico_destino)
        texto = canonico()
        _valida(texto)
        destino.write_text(texto, encoding="utf-8")
        print(f"canonico gravado: {destino} sha256={sha256_texto(texto)}")
        return 0

    if not a.alvo:
        print("nada a fazer: use --alvo, --canonico-destino ou --autoteste")
        return 2

    alvo = pathlib.Path(a.alvo)
    atual = alvo.read_text(encoding="utf-8")
    sha_atual = sha256_texto(atual)

    if a.check:
        print(f"alvo : {alvo}")
        print(f"sha  : {sha_atual}")
        print(f"estado: {estado(atual)}")
        print(f"canonico versionado: {SHA_CANONICO}")
        canonico_ok = sha_atual == SHA_CANONICO
        print("VEREDITO=" + ("CANONICO" if canonico_ok else "DIVERGENTE"))
        return 0 if canonico_ok else 1

    try:
        novo = compor(atual, reverter=a.reverter)
    except AssertionError as exc:
        print(f"ABORTA: {exc}")
        return 1
    if novo == atual:
        print(f"OK    ja no estado desejado: {alvo} sha256={sha_atual}")
        return 0
    _valida(novo)
    if not a.aplicar:
        print(f"(seco) {alvo}: mudaria {sha_atual[:16]} -> {sha256_texto(novo)[:16]}")
        return 0
    backup = alvo.with_name(alvo.name + f".bak-composicao-{_estamp()}")
    if backup.exists():
        print(f"ABORTA: backup {backup.name} ja existe")
        return 1
    shutil.copy2(alvo, backup)
    tmp = alvo.with_name(alvo.name + ".tmp-composicao")
    tmp.write_text(novo, encoding="utf-8")
    tmp.replace(alvo)
    print(f"gravado {alvo} sha256={sha256_texto(novo)} backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
