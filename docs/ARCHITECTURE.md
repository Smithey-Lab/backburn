# Architecture

Backburn is split into a **SimulationCore** (pure Python + NumPy, no rendering
dependencies) and thin presentation layers on top of it. The core is the thing
that will be ported into Godot; everything else is disposable tooling.

```
                    ┌──────────────────────────────────────────────┐
  scenarios/*.json ─┤  scenario.py   Scenario, validate(), generate │
                    └───────────────┬──────────────────────────────┘
                                    │ build_terrain(), build_elevation()
                    ┌───────────────▼──────────────────────────────┐
                    │  sim.py        Simulation (facade)            │
                    │   • fixed timestep, events, outcome, score    │
                    │   • command log → replay                     │
                    │   • full-state savegame                      │
                    ├───────────────┬──────────────┬───────────────┤
                    │  fire.py      │  units.py    │ pathfinding.py│
                    │  FireGrid     │  World, Unit │ A* on costs   │
                    │  (cellular    │  (agents,    │               │
                    │   automaton)  │   orders)    │               │
                    ├───────────────┴──────────────┴───────────────┤
                    │  config.py   data/terrain.json data/units.json│
                    └──────────────────────────────────────────────┘
                                    │ read-only
              ┌─────────────────────┼─────────────────────┐
     ┌────────▼───────┐    ┌────────▼───────┐    ┌────────▼───────┐
     │ render.py      │    │ viewer.py      │    │ __main__.py    │
     │ PNG / GIF      │    │ pygame harness │    │ CLI            │
     └────────────────┘    └────────────────┘    └────────────────┘
```

## Modules

| Module | Responsibility | Brief §10 system |
|---|---|---|
| `config.py` | Loads `data/*.json` into NumPy lookup tables indexed by terrain id. Nothing numeric lives in code. | — |
| `fire.py` | `FireGrid`: all per-cell arrays and the spread step. Wind, slope, suppression fields, ember spotting, building labels, array (de)serialisation. | SimulationCore |
| `pathfinding.py` | A* over the per-movement-class cost grid; refuses burning cells for ground units. | UnitSystem |
| `units.py` | `Unit` state machines, `Order`, `World` (spawning, dispatch queue, safe zone, water lookup). Civilians, pickup/drop-off, hose lines, cut lines, drops, refill/reload. | UnitSystem, OrderSystem |
| `scenario.py` | `Scenario` dataclass, JSON (de)serialisation, strict validation, prototype-scale procedural terrain and elevation. | ScenarioSystem, MapSystem, Save/Load |
| `worldgen.py` | Desktop-scale world generator (constant feature size, lakes, rivers, bridges, highways, towns, elevation, canyons) and placement helpers. | MapSystem |
| `incidents.py` | Random Incident mode: a complete scenario rolled from one seed. | ScenarioSystem |
| `campaign.py` | Desktop mission preparation (fleet rules; upsampling for prototype-scale files). | — |
| `art.py` / `game.py` | Desktop renderer (patched map surface, sprite batching) and front end. | Rendering, UI |
| `sim.py` | `Simulation` facade: tick loop, scenario events, budget, objectives → outcome, scoring, replay log, savegames. | ScenarioSystem, Replay/Debug |
| `render.py` | Headless frame renderer (Pillow). | Rendering |
| `viewer.py` | pygame tuning harness implementing the brief's control scheme. | UI |
| `__main__.py` | CLI: view, run, replay, resume, validate, gen, bench, info. | — |

## Tick order

One call to `Simulation.step()` advances one simulated second:

1. `_apply_events()` — scenario events whose `at` time has been reached (wind change, ignition, spawn, budget, message).
2. `grid.step(dt)` — the fire model (see `FIRE_MODEL.md`), run inside each active region in this order: emitted heat → exposure → ignition roll → fuel consumption → water extinguish → smolder cooling → apply new ignitions → launch embers → field decay; then embers land and the regions are re-derived. Regions are bounding boxes around burning, smoldering and wet cells (one-cell margin, merged when within two cells); on maps up to 256×256 the whole grid is one region.
3. `world.update(dt)` — deliver pending arrivals, then every unit's state machine. Units read the grid state produced in step 2 and write suppression/terrain changes that the *next* fire step sees.
4. `tick += 1`, collect unit messages, `_check_outcome()`.

Player commands (`cmd_*`) are applied immediately when called and recorded with the current tick. Replays re-apply commands *before* the step of the tick they were recorded on, so `cmd → step` ordering is preserved exactly.

## Determinism

- One `numpy.random.Generator` (PCG64) per `FireGrid`, seeded from the scenario. All randomness — ignition rolls, ember launch/landing — goes through it, in array order.
- Cells are updated in a single vectorised pass, so update order can't leak into results.
- Unit ids are assigned from a per-`World` counter, so a replay assigns the same ids.
- Units use no randomness.
- The savegame stores the generator's bit-generator state; a loaded game continues on exactly the same random stream (`tests/test_features.py::test_savegame_roundtrip_is_exact`).

Consequence: `state_hash()` after N ticks is a function of (scenario, seed, command log). This is what makes fire-spread bugs reproducible.

## Coordinate conventions

- Arrays are `[y, x]`, row 0 at the top. Units keep float `(x, y)` in cell units; `Unit.cell` rounds.
- Wind bearing is the compass direction the wind blows **toward** (0 = north/up, 90 = east/right).
- Elevation is metres; a cell is `fire.cell_size_m` (10 m) across.

## Persistence formats

| Format | File | Contents | Use |
|---|---|---|---|
| Scenario | `*.json` | map, weather, units, objectives, events | authoring, editor output |
| Replay | `*.json` (`format: backburn-replay-1`) | scenario + command log + final hash | bug reports, regression, "watch again" |
| Savegame | `*.bbsave` (zip: `meta.json` + `grid.npz`) | full state incl. RNG | resume a game |

Replays are small and human-readable; savegames are exact. No pickling anywhere.

## Pathfinding

Ground units plan on two levels. A `BlockGraph` (per movement class, cached until the class's passability pattern changes) has one node per connected component of passable cells inside each 8×8 block and edges where components touch across block borders, so a river through a block is a real barrier. Long orders run A* over that graph with an optimistic block cost (roads make a block cheap, which routes dozers and engines along highways), then refine only the next six blocks onto cells with a windowed, flat-list A*. The unit carries the unrefined `Route`; when its leg is used up it refines the next one. A corridor that turns out to be cut marks the block and replans. Short orders, hose laying and fleeing civilians use the windowed fine search directly. Nothing here is random, so replays stay exact.

## Performance envelope

Measured on the largest shipped map (Wall of Fire, 1440×990, ~1.4 M cells): `grid.step()` is 1–4 ms with a normal fire and ~17 ms once a fifth of the map has burned; a cross-map order costs 20–120 ms once (graph build plus first leg) and 2–3 ms per later leg; the desktop draws a frame in 4–10 ms at any zoom. The prototype reference (128×96, full-grid pass) still runs at ~1,400 ticks/s. See `docs/TUNING.md` for the knobs and `PORTING.md` for why the Godot port keeps the sim in C#.
