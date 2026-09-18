"""Regenerate the data tables embedded in docs/UNITS.md and docs/FIRE_MODEL.md from the
JSON balance files, so the documentation can never drift from the numbers the sim uses.

    python tools/gen_tables.py          # rewrites the tables in place
    python tools/gen_tables.py --check  # exit 1 if the docs are stale (used by CI)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "backburn" / "data"
BEGIN, END = "<!-- BEGIN GENERATED: {} -->", "<!-- END GENERATED: {} -->"


def units_table() -> str:
    d = json.loads((DATA / "units.json").read_text())["types"]
    rows = [
        "| Type | Label | Confidence | Move | Speed | Action | Cost | ETA (s) | Capacity | Spray r | Cut w | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k, v in d.items():
        notes = []
        if v.get("passenger_slots"):
            notes.append(f"carries {v['passenger_slots']}")
        if v.get("reload_at_base"):
            notes.append(f"reloads at base {v.get('reload_seconds', 0):.0f}s")
        if v.get("hose_max_length"):
            notes.append(f"hose ≤ {v['hose_max_length']} cells")
        if v.get("can_cut_dense"):
            notes.append("cuts dense forest")
        if v.get("drop_agent"):
            notes.append(f"drops {v['drop_agent']} ×{v.get('drop_width')} wide")
        if v.get("is_civilian"):
            notes.append("rescue objective")
        if v.get("air_deployable"):
            notes.append("air-deployable")
        rows.append(
            f"| `{k}` | {v.get('label', k)} | {v.get('confidence', '')} | {v['movement']} | {v['speed']} | "
            f"{v['action']} | {v.get('cost', 0)} | {v.get('arrival_seconds', 0)} | {v.get('capacity', 0)} | "
            f"{v.get('spray_radius', 0)} | {v.get('cut_width', 0)} | {'; '.join(notes)} |"
        )
    return "\n".join(rows)


def terrain_table() -> str:
    d = json.loads((DATA / "terrain.json").read_text())["types"]
    rows = [
        "| Terrain | Letter | Fuel | Ignition rate | Burn rate | Heat | Smolder (s) | FOOT | ROAD | OFFROAD | AIR | WATERCRAFT |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    letters = {
        "WATER": "W",
        "GRASS": "G",
        "SHRUB": "S",
        "FOREST": "F",
        "DENSE_FOREST": "D",
        "ROAD": "R",
        "GRAVEL": "V",
        "STRUCTURE": "B",
        "FIREBREAK": "X",
        "SAND": "A",
    }
    for k, v in sorted(d.items(), key=lambda kv: kv[1]["id"]):
        m = v["move"]

        def mv(c):
            return "—" if m.get(c) is None else str(m[c])

        rows.append(
            f"| `{k}` | {letters.get(k, '?')} | {v['fuel']} | {v['ignition_rate']} | {v['burn_rate']} | "
            f"{v['heat_output']} | {v['smolder_time']} | {mv('FOOT')} | {mv('ROAD')} | {mv('OFFROAD')} | {mv('AIR')} | {mv('WATERCRAFT')} |"
        )
    return "\n".join(rows)


def fire_constants_table() -> str:
    d = json.loads((DATA / "terrain.json").read_text())["fire"]
    rows = ["| Constant | Value |", "|---|---|"]
    for k, v in d.items():
        if k.startswith("_"):
            continue
        rows.append(f"| `{k}` | {v} |")
    return "\n".join(rows)


GENERATORS = {"units": units_table, "terrain": terrain_table, "fire_constants": fire_constants_table}


def splice(text: str, name: str, body: str) -> str:
    b, e = BEGIN.format(name), END.format(name)
    pat = re.compile(re.escape(b) + r".*?" + re.escape(e), re.S)
    if not pat.search(text):
        raise SystemExit(f"marker {name} not found")
    return pat.sub(f"{b}\n{body}\n{e}", text)


def main(check: bool) -> int:
    stale = 0
    for path in (ROOT / "docs" / "UNITS.md", ROOT / "docs" / "FIRE_MODEL.md"):
        text = path.read_text(encoding="utf-8")
        new = text
        for name, gen in GENERATORS.items():
            if BEGIN.format(name) in new:
                new = splice(new, name, gen())
        if new != text:
            stale += 1
            if not check:
                path.write_text(new, encoding="utf-8")
                print("updated", path.relative_to(ROOT))
            else:
                print("STALE", path.relative_to(ROOT))
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
