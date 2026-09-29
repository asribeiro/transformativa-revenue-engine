#!/usr/bin/env bash
# Lista os cards de DEFEITO para a revisao de fim de onda: severidade, deteccao e pai.
# Uso: scripts/kanban/listar-defeitos.sh [onda|tudo]
set -euo pipefail

BOARD="${KANBAN_BOARD:-transformativa-revenue-engine}"
FILTRO="${1:-tudo}"

hermes kanban --board "$BOARD" ls --json 2>/dev/null > /opt/data/cache/scratch/_defeitos_ls.json || true
/opt/hermes/.venv/bin/python - "$FILTRO" <<'PY'
import json, pathlib, re, sys

filtro = sys.argv[1]
d = json.loads(pathlib.Path("/opt/data/cache/scratch/_defeitos_ls.json").read_text())
cards = d if isinstance(d, list) else (d.get("tasks") or d.get("cards") or [])
if isinstance(cards, dict):
    cards = cards.get("tasks") or []

def campo(corpo, nome):
    m = re.search(rf"^{nome}:\s*(.+)$", corpo or "", re.M)
    return m.group(1).strip() if m else "-"

defeitos = [c for c in cards if "DEFEITO" in (c.get("title") or "").upper()]
if filtro != "tudo":
    defeitos = [c for c in defeitos if filtro.upper() in (c.get("title") or "").upper()]

if not defeitos:
    print(f"nenhum card de defeito encontrado (filtro: {filtro})")
    raise SystemExit(0)

print(f"{len(defeitos)} card(s) de defeito\n")
por_sev = {}
for c in sorted(defeitos, key=lambda x: x.get("created_at") or ""):
    corpo = c.get("body") or ""
    sev = campo(corpo, "SEVERIDADE")
    det = campo(corpo, "DETECTADO POR")
    por_sev[sev] = por_sev.get(sev, 0) + 1
    status = c.get("status") or "?"
    cid = c.get("id") or "?"
    print(f"{status:8s} {cid}  sev={sev:6s} detectado_por={det}")
    print(f"         {c.get('title','')}")
print("\nresumo por severidade: " + ", ".join(f"{k}={v}" for k, v in sorted(por_sev.items())))
PY
rm -f /opt/data/cache/scratch/_defeitos_ls.json
