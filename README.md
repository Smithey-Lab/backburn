# Backburn

Clean-room, personal desktop recreation of a classic top-down wildfire tactics RTS.
The wildfire is the opponent: you manage terrain, wind, time, a budget, and a small
roster of crews, engines, and aircraft to keep it off the houses and the hikers.

This repository is the **SimulationCore** in Python + NumPy, a fully playable pygame
harness, four scenarios, the tooling around them, and a Godot 4 shell for the shipping
game. Read `DECISIONS.md` first: every architecture choice is pinned there with its reason.

> Working name. "Backburn" appears in the package name, README, and window title only,
> so nothing here carries the original title.

## Quick start

```
pip install -r requirements.txt
python -m backburn view                     # Prairie Fire, the tutorial scenario
python -m backburn view scenarios/stranded_hikers.json
```

Left-click a unit, right-click to order it, right-drag for lines (cut lines, air drops),
`B` to buy units, `Space` to pause, `H` for help. Full reference: `docs/CONTROLS.md`.

Headless:

```
python -m backburn run scenarios/wall_of_fire.json --ticks 900 --gif out.gif
python -m backburn validate scenarios/*.json
python -m backburn bench
pytest -q
```

`pip install -e ".[dev]"` gives you a `backburn` console command and the test/build tools.

## What's in it (v0.2.0)

**Fire.** Probabilistic cellular automaton on an 8-neighbour grid with wind, slope,
four-state cell lifecycle (UNBURNED → BURNING → SMOLDER → COLD), water and retardant
fields, rekindling from wet fuel, ember spotting in high wind, and a seeded RNG so every
run is reproducible. `docs/FIRE_MODEL.md`.

**Units.** Hose team, cut team, hotshots, smokejumpers, engine, brush truck, bulldozer,
helicopter, water bomber, retardant bomber, P-3, fire boat, civilians. Movement classes,
A* pathfinding that avoids fire, hose lines that burn, cut lines that become firebreaks,
line drops, auto-refill and base reload, helicopter pickup/drop-off, civilians that flee.
`docs/UNITS.md`.

**Scenarios.** JSON with generated or explicit maps, elevation, weather, budget and
unit availability, objectives, timed events, briefings. Strictly validated with
field-naming errors; JSON Schema in `schema/`. `docs/SCENARIO_FORMAT.md`.

**Outcomes and score.** Contained / failed / timeout against structure, civilian, and
burned-area limits; weighted score breakdown. `docs/SCORING.md`.

**Persistence.** Command-log replays (small, human-readable, hash-verified) and
full-state savegames (`.bbsave`, RNG included). `docs/ARCHITECTURE.md`.

**Tooling.** CLI (`view run replay resume validate gen bench info`), pygame viewer with
buy menu, overlays, editor, quicksave; headless PNG/GIF renderer; docs tables generated
from the balance data; PyInstaller spec for a Windows executable; GitHub Actions CI.

## Scenarios

| File | Premise | Objective |
|---|---|---|
| `prairie_fire.json` | Grass fire west of a settlement, westerly wind, everything already on scene | lose ≤ 6 buildings |
| `stranded_hikers.json` | Four hikers cut off on a forested ridge, fire running uphill | fly everyone to the trailhead |
| `refinery_row.json` | Industrial strip on the highway, grass upwind, second fire later | lose ≤ 3 buildings on a budget |
| `wall_of_fire.json` | 12 m/s wind in heavy timber with embers, frontal wind shift | keep burned area under 45% |

## Status against the brief's MVP order

| # | Step | State |
|---|------|-------|
| 1 | Map + pan/zoom + tile properties | done |
| 2 | Ignition and cell-to-cell spread | done |
| 3 | Wind + terrain-dependent spread | done, plus slope and spotting |
| 4 | Hose team + cut team | done |
| 5 | Helicopter move / refill / drop | done, plus pickup/drop-off |
| 6 | Buildings / objectives / scoring | done |
| 7 | Engine / brush truck / dozer | done |
| 8 | Water + retardant aircraft | done |
| 9 | Scenario editor | partial (paint, ignite, wind, buy, bake; no unit placement UI) |
| 10 | Polish, sound, UI feedback | not started; placeholder art by design |
| 11 | Save/load + deterministic replay | done |
| 12 | Multiplayer | deferred |

Details and what comes next: `docs/ROADMAP.md`.

## Layout

```
backburn/           package: fire.py units.py sim.py scenario.py pathfinding.py render.py viewer.py
backburn/data/      terrain.json units.json  ← every gameplay number lives here
scenarios/          four shipped scenarios
schema/             scenario.schema.json
tests/              41 behavioural tests (~4 s)
tools/              gen_tables.py, front_speed.py
docs/               ARCHITECTURE FIRE_MODEL UNITS SCENARIO_FORMAT SCORING CONTROLS TUNING DEVELOPMENT ROADMAP
packaging/          PyInstaller spec + Windows build script
godot/              Godot 4 shell + PORTING.md
```

## Tuning

Change a number in `backburn/data/`, run `pytest -q` (tests guard relationships, not
constants), look at a GIF or play it, then `python tools/gen_tables.py` so the docs match.
`docs/TUNING.md` maps "I want the fire to…" onto the right constant.

## Performance

Reference scenario (128×96, 8 units): ~1,400 ticks/s idle, ~450 with every unit working.
The fire step is ~0.5 ms; the rest is pathfinding. The Godot port keeps the sim in C#
for this reason (`godot/PORTING.md`).

## Clean-room statement

Mechanics and feel are reproduced from public descriptions and screenshots listed in the
research brief. No original code, art, audio, or extracted assets are used or included.
Every uncertain behaviour is tagged documented / inferred / new in the docs.
