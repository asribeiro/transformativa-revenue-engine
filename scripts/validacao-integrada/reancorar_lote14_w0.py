#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reancora os 4 cards de defeito do backup (lote 14) como FILHOS do epic TRE-W0-E01-T03.

ANTES (forma que eu tinha gravado): cada defeito virou um work_item independente
(`TRE-W0-E01-T03-D0N`), com um filho so' — o que um leitor de "epic -> filhos" le' como
4 epics novos.

DEPOIS (forma do artefato): os 4 filhos ficam SOB o work_item `TRE-W0-E01-T03`, e os 4
work_items avulsos sao removidos.

Uso: python3 scripts/validacao-integrada/reancorar_lote14_w0.py [--dry-run]
"""
import json
import pathlib
import sys

ARQ = pathlib.Path("control-plane/deliveries/W0-governanca-e-baseline.json")
EPIC = "TRE-W0-E01-T03"
DEFEITOS = [
    "TRE-W0-E01-T03-D01",
    "TRE-W0-E01-T03-D02",
    "TRE-W0-E01-T03-D03",
    "TRE-W0-E01-T03-D04",
]


def main() -> int:
    dry = "--dry-run" in sys.argv
    j = json.loads(ARQ.read_text(encoding="utf-8"))
    items = j["work_items"]
    ids_antes = [w.get("id") for w in items]
    filhos_antes = sum(len(w.get("children") or []) for w in items)

    epic = next((w for w in items if w.get("id") == EPIC), None)
    if epic is None:
        print(f"!! epic {EPIC} nao existe no artefato — nada feito")
        return 1
    print(f"epic {EPIC}: filhos antes = {len(epic.get('children') or [])}")

    movidos = []
    for did in DEFEITOS:
        avulso = next((w for w in items if w.get("id") == did), None)
        if avulso is None:
            print(f"  {did}: ja' nao e' work_item avulso (nada a mover)")
            continue
        filhos = avulso.get("children") or []
        # nao duplica: so' entra filho cujo hermes_task_id ainda nao esteja no epic
        ja = {c.get("hermes_task_id") for c in (epic.get("children") or [])}
        novos = [c for c in filhos if c.get("hermes_task_id") not in ja]
        epic.setdefault("children", []).extend(novos)
        items.remove(avulso)
        movidos.append((did, [c.get("hermes_task_id") for c in novos]))
        print(f"  {did}: {len(novos)} filho(s) movido(s) -> {[c.get('hermes_task_id') for c in novos]}")

    for did, _ in movidos:
        j.setdefault("events", []).append(
            {
                "event": "WORK_ITEM_REANCHORED",
                "by": "Hermes — validacao integrada do lote 14",
                "scope": f"{did} deixa de ser work_item avulso e passa a ser filho de {EPIC} (decisao do dono, 05/10/2026)",
                "at": "2026-10-05T16:30:00+00:00",
                "production_promotion_authorized": False,
            }
        )
    if movidos:
        j["updated_at"] = "2026-10-05"

    print(
        f"itens: {len(ids_antes)} -> {len(items)} | filhos: {filhos_antes} -> "
        f"{sum(len(w.get('children') or []) for w in items)} | "
        f"epic {EPIC} agora com {len(epic.get('children') or [])} filho(s)"
    )
    print("  ids do epic:", [c.get("hermes_task_id") for c in (epic.get("children") or [])])
    if dry:
        print("(dry-run: nada gravado)")
        return 0
    ARQ.write_text(json.dumps(j, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("gravado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
