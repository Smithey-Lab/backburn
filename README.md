# Backburn

Clean-room, personal desktop recreation of a classic top-down wildfire tactics RTS.
This repo is the **SimulationCore** prototype in Python + NumPy, plus a pygame
tuning harness and Godot 4 scaffolding for the shipping game.

Read `DECISIONS.md` first. Every architecture choice is pinned there with its reason.

## Status — 2026-09-18

Against the brief's MVP order (§11):

| # | Step | State |
|---|------|-------|
| 1 | Map + pan/zoom + tile properties | **done** (procedural + explicit-grid maps, 10 terrain types, viewer pans/zooms) |
| 2 | Ignition and cell-to-cell spread | **done** (probabilistic CA, UNBURNED→BURNING→SMOLDER→COLD) |
| 3 | Wind + terrain-dependent spread | **done** (global wind vector, per-fuel rates, tuned: grass ≈5× dense forest) |
| 4 | One hose team + one cut team | **done** (hose draws a line to water, burns, reconnects; cut crews lay handline) |
| 5 | Helicopter move / refill / line drop | **done** |
| 6 | Buildings / objectives / scoring | **partial** (structures burn and are counted; no score formula yet) |
| 7 | Engine / brush truck / dozer | **done** (movement classes, auto-refill, dozer cuts 2-wide through dense fuel) |
| 8 | Water + retardant aircraft | **done** (line drops, return-to-base reload timer) |
| 9 | Scenario editor | **partial** (paint terrain, ignite, change wind in the viewer; JSON round-trips) |
| 10 | Polish, sound, UI feedback | not started (placeholder rectangles by design) |
| 11 | Save/load + deterministic replay | **done** (command log replays to an identical state hash) |
| 12 | Multiplayer | deferred |

21 behavioural tests pass (`pytest -q`). The tick runs at ~450 ticks/s on a 128×96
map with 8 units, i.e. ~45× real time headroom.

Not implemented yet: ember spotting, slope, civilians/rescue, hotshots/smokejumpers/
fire boat/P-3 beyond their data entries, scoring, audio.

## Run it

```
pip install -r requirements.txt
python -m backburn view                          # play the Prairie Fire scenario
python -m backburn run scenarios/prairie_fire.json --ticks 600 --gif out.gif
python -m backburn gen new.json --seed 12        # bake a generated map to grid mode
pytest -q
```

Viewer controls are listed at the top of `backburn/viewer.py` and follow the brief §9.

## Layout

```
backburn/
  data/terrain.json     fire + movement numbers per terrain  ← tune here
  data/units.json       unit balance (speed, capacity, radius) ← tune here
  config.py             JSON → NumPy lookup tables
  fire.py               the cellular automaton (FireGrid)
  pathfinding.py        A* on the move-cost grid
  units.py              unit state machines, orders, World
  scenario.py           JSON scenarios + procedural map generator
  sim.py                Simulation facade, command log, replay
  render.py             headless PNG/GIF renderer
  viewer.py             pygame harness (not the shipping UI)
scenarios/              JSON scenarios
tests/                  pytest suite
godot/                  Godot 4 project shell + PORTING.md
DECISIONS.md            pinned choices, tagged documented / inferred / new
```

## Tuning workflow

1. Change a number in `data/terrain.json` or `data/units.json`.
2. `pytest -q` — the behavioural tests guard the relationships that matter
   (wind bias, fuel ordering, breaks hold, water works), not exact values.
3. `python -m backburn run … --gif` and look at it, or play it in the viewer.

The fire constants were set by sweeping `exposure_scale`, `wind_gain` and the
per-fuel `ignition_rate` until a grass front moved ~0.35 cells/s downwind in a
6 m/s wind, ~0.03 upwind, and dense forest ~0.07 downwind. Those are starting
points, not facts (brief §15).

## Working name

"Backburn" is a placeholder chosen so nothing here carries the original title.
Rename freely; it appears only in the package name, README, and window title.
