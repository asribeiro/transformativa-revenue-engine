#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lote 7 — ajuste de consistencia no veredito do W6-E05 (t_cbb01999).

O lote 6 registrou o W6-E05 com a evidencia "Este aceite NAO expoe modo --prova-de-dente (o script
so' aceita --manter)". A melhoria pedida no lote 7 deu a esse aceite um --prova-de-dente de verdade
(commit 6db2f06). Para o artefato nao ficar com uma afirmacao hoje FALSA, o lote 7 faz APPEND
rotulado (preserva o texto antigo como historico) e acrescenta o portao novo. Nada e' apagado e a
classificacao do card NAO muda (segue PASS).

dry-run por padrao; --aplicar escreve com backup datado.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import shutil
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent.parent
ARTEFATO = RAIZ / "control-plane" / "deliveries" / "W6-outbound-e-canais.json"
PLUGIN_API = pathlib.Path("/opt/data/plugins/kanban/dashboard/plugin_api.py")
COMMIT_MELHORIA = "6db2f06"
DATA = "2026-10-04"

NOTA = (
    " [LOTE 7 — append de consistencia] O texto acima ('NAO expoe modo --prova-de-dente; o script "
    "so' aceita --manter') descreve o estado verificado no lote 6 e fica preservado como historico. "
    "O lote 7 ACRESCENTOU ao aceite um --prova-de-dente de verdade (commit "
    + COMMIT_MELHORIA + "): muta uma COPIA de hermes/agentes/respostas/ingestao_respostas.py e exige "
    "que o aceite REPROVE o item 12 nomeado. Medido no commit: a rodada normal fica INALTERADA "
    "(ACEITE_INGESTAO_RESPOSTAS_001_OK, 43 itens, 0 falhas; duas passadas byte-identicas) e "
    "--prova-de-dente emite 'DENTE_OK citacao — o aceite REPROVOU o item esperado (exit=1): dente: "
    "descadastro so na citacao NAO vira OPT_OUT' e 'PROVA_DE_DENTE_OK' (exit 0)."
)

PORTAO = {
    "gate": "bash scripts/agentes/teste_ingestao_respostas_aceite.sh --prova-de-dente",
    "exit": 0,
    "resultado": "PROVA_DE_DENTE_OK (1/1): mutacao da limpeza de citacao em COPIA do componente -> "
                 "a linha nomeada 'FALHOU dente: descadastro so na citacao NAO vira OPT_OUT' "
                 "aparece e o aceite sai exit 1 (rota normal inalterada: 43 itens, 0 falhas, duas "
                 "passadas byte-identicas)",
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true")
    args = p.parse_args()

    d = json.loads(ARTEFATO.read_text(encoding="utf-8"))
    alvo = None
    for it in d["work_items"]:
        if it["id"] == "TRE-W6-E05-T01":
            alvo = it["children"][0]
    if alvo is None:
        raise SystemExit("FAIL-CLOSED: TRE-W6-E05-T01 ausente do artefato W6")
    if "LOTE 7 — append de consistencia" in alvo["evidence"]:
        raise SystemExit("FAIL-CLOSED: ajuste ja' aplicado (idempotencia)")
    if alvo["validation_result"] != "PASS":
        raise SystemExit(f"FAIL-CLOSED: W6-E05 nao esta PASS ({alvo['validation_result']})")

    print("=== AJUSTE DE CONSISTENCIA — W6-E05 (--prova-de-dente) —",
          "APLICANDO" if args.aplicar else "DRY-RUN", "===")
    print("  child:", alvo["hermes_task_id"], "| portoes antes:", len(alvo["verification"]["portoes"]))

    novo = json.loads(json.dumps(d))
    alvo2 = None
    for it in novo["work_items"]:
        if it["id"] == "TRE-W6-E05-T01":
            alvo2 = it["children"][0]
    alvo2["evidence"] = alvo2["evidence"] + NOTA
    alvo2["verification"]["portoes"].append(json.loads(json.dumps(PORTAO)))
    novo["updated_at"] = DATA
    novo["production_promotion_authorized"] = False
    novo.setdefault("events", []).append({
        "event": "INTEGRATED_VALIDATION_EVIDENCE_APPENDED",
        "at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "by": "Hermes — validacao integrada (lote 7)",
        "scope": "lote 7 — W6-E05 ganha --prova-de-dente (append de consistencia, sem reclassificar)",
        "cards": [alvo["hermes_task_id"]],
        "production_promotion_authorized": False,
    })
    print("  portoes depois:", len(alvo2["verification"]["portoes"]),
          "| validacao_result inalterado:", alvo2["validation_result"])

    if not args.aplicar:
        print("(dry-run: rode com --aplicar)")
        return 0

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bkp = ARTEFATO.with_name(ARTEFATO.name.replace(".json", f".bak-{ts}.json"))
    shutil.copy2(ARTEFATO, bkp)
    print("  backup:", bkp)
    tmp = ARTEFATO.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(novo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(ARTEFATO)
    print("GRAVADO")

    spec = importlib.util.spec_from_file_location("papi", str(PLUGIN_API))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    col = m._delivery_lifecycle_task_columns(board="transformativa-revenue-engine") or {}
    expl = m._delivery_explicit_done_task_ids(board="transformativa-revenue-engine") or set()
    print("  leitor: t_cbb01999 na coluna de validacao:", alvo["hermes_task_id"] in col,
          "| DONE explicito:", alvo["hermes_task_id"] in expl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
