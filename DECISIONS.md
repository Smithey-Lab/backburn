# DECISIONS.md — pinned architecture choices

Every item below was chosen once so the codebase has one shape. Change a line here
before changing code that depends on it. Each is tagged **documented** (from the
research brief), **inferred** (from screenshots/descriptions), or **new** (our call).

## Project

- **Working name: Backburn.** *(new)* "FireJumpers" belongs to the original developer,
  who is still active. Keep the repo, package, and window title under a different name
  so nothing has to be renamed later if this ever leaves your machine.
- **Clean-room.** *(documented)* Mechanics and feel are reproduced; no original code,
  art, audio, or extracted assets. Reference screenshots are for comparison only.

## Engine and language

### v0.3 shipping decision

The first downloadable Windows release ships the existing NumPy simulation with a
new pygame-ce desktop interface. This retains the tested v0.2 mechanics, avoids a
second simulation implementation, and supports standalone PyInstaller packaging.
The Godot folder is an archived port experiment, not the current shipping engine.
The earlier engine choices below describe the prototype's original plan.

As of v0.3.1 the desktop runs one fixed simulation tick per real second at normal speed;
the older tuning harness retains ten. Each tick still represents one simulated
second, so replays and balance remain deterministic. Pausing supports planning.

New art is generated from geometric sprites and seeded map coordinates. UI audio
is synthesized locally; no source-game assets are bundled. Saves use per-user
storage outside the installation. Updates stage complete, checksummed versions
from GitHub Releases and switch a small current-version pointer after validation.

Rescue-only missions (rescue_all_civilians true and win_on_contained false) finish
when everyone is safe. Containment missions wait for scheduled future ignitions.

- **Shipping engine: Godot 4.3+.** *(new)* Text-based scenes, headless CLI, one-command
  Windows export, and a UI toolkit we don't have to write. Unity and Unreal rejected
  for agent-workflow reasons (editor-bound, binary Blueprints).
- **SimulationCore is prototyped in Python + NumPy first.** *(new)* It is
  engine-agnostic by design (brief §10). Proving fire behaviour in Python takes days,
  not weeks, and every constant we tune here carries over.
- **When ported into Godot, the sim runs in C# (Godot .NET), not GDScript.** *(new)*
  GDScript per-cell loops over a 128×128 grid at 10 Hz will not hold. Everything else
  (scenes, UI, input, camera) stays GDScript. Alternative if C# is unwanted: shrink the
  grid to 96×96 and accept the ceiling.

## Simulation

- **Grid: 128 × 128 cells default, max 256 × 256.** *(new)* One cell ≈ 10 m. Large
  enough for the original mission scale, small enough to stay fast everywhere.
- **Fixed timestep: 10 sim ticks per second at 1× speed; 1 tick = 1 simulated second.**
  *(new)* Speed controls change ticks-per-real-second, never dt.
- **Fire model: probabilistic cellular automaton, 8-neighbour (Moore).** *(documented,
  brief §8)* Ignition probability = base fuel rate × wind factor × slope factor ×
  dryness × neighbour heat × (1 − suppression). Slope factor exists but elevation is
  flat in the first scenarios.
- **Cell state machine: UNBURNED → BURNING → SMOLDER → COLD.** *(new)* SMOLDER cells
  keep residual heat and can re-ignite neighbours. Extinguishing a BURNING cell returns
  it to UNBURNED with reduced fuel and high moisture, which is what produces apparent
  rekindling in dense forest. *(documented behaviour, new mechanism)*
- **Wind is one global vector.** *(documented: Sandbox wind speed + direction)*
  Changeable at runtime. Ember spotting is a later addition on top of this.
- **Determinism: one seeded `numpy.random.Generator` per simulation, all randomness
  goes through it, and cells are updated in a single vectorised pass so update order
  cannot leak.** *(documented: seeded RNG for replay)* Same seed + same orders ⇒ same
  state hash.
- **Suppression is two fields:** `water` (fast decay, strong effect) and `retardant`
  (slow decay, blocks ignition ahead of fire). *(documented)*

## Units

- **Units are object-based, not grid-based.** *(new)* A few dozen agents with float
  positions on top of the cell grid. Only the fire is a cellular automaton.
- **Movement classes:** `FOOT`, `ROAD`, `OFFROAD`, `AIR`, `WATERCRAFT`. Each terrain has
  a movement cost per class; `inf` means impassable. *(inferred from unit roles)*
- **Pathfinding: Dijkstra on the cost grid, path recomputed when the order changes or
  a path cell becomes impassable.** *(new)* Flow fields deferred until unit counts
  justify them.
- **Orders are queued per unit.** MOVE, SUPPRESS, HOSE, CUT (polyline), DROP (segment),
  REFILL, PICKUP/DROPOFF. Shift-queue is a UI concern, not a sim concern.
- **Hose teams draw a hose line from a water cell to their position; any burning cell
  under the hose burns it, and the team stops suppressing until it reconnects.**
  *(documented)*

## Scenarios and data

- **Scenario files are JSON.** *(documented)* Terrain is either procedurally generated
  from a seed + parameters, or an explicit grid. Both round-trip.
- **Numeric balance (speeds, capacities, rates) lives in `data/units.json` and
  `data/terrain.json`, not in code.** *(new)* The brief says these numbers are a
  design space, so they must be tunable without touching the sim.

## Art

- **Placeholder rectangles until the game is playable.** *(new)* The target style is
  blocky top-down with hard colour separation, so flat colour cells are already close.
  Original sprites come after step 8 of the MVP order.

## Added in v0.2 (2026-09-18)

- **Ember spotting is a separate process from adjacency spread.** *(documented mechanism,
  new model)* Only above `spot_min_wind`, only from hot fuels, embers fly for a few ticks
  and ignite on landing. This is what lets narrow lines fail in a gale.
- **Slope uses `exp(slope_gain × grade)`.** *(new)* Rothermel-flavoured, cheap, cached per
  direction. Flat maps skip it.
- **Buildings are 4-connected components of STRUCTURE cells; a building is lost when any
  cell burns.** *(new)* Objectives, HUD and score count buildings, never cells.
- **Budget is per scenario, costs per unit, arrivals are delayed.** *(documented: Sandbox
  budget/resource setup)* Ground units arrive at `staging`, aircraft at `airbase`. Free
  reinforcements come from events, not from a second code path.
- **Civilians have no orders.** *(documented: rescue objective)* They flee on their own and
  are rescued by entering the safe zone, on foot or by helicopter.
- **Scenario events are declarative and part of the scenario.** *(new)* So replays never
  log them and the file stays the single source of truth.
- **Outcome precedence: structure limit → civilian limit → burn limit → contained →
  timeout.** *(new)* `Simulation.step()` returns 0 once decided; the UI doesn't need a
  separate game-over flag.
- **Two persistence formats, no pickling.** *(documented: save/load + replay)* Replay =
  scenario + command log; savegame = zip of JSON + npz with the RNG state.
- **Strict validation with field-naming errors, plus JSON Schema.** *(new)* Loading a bad
  file fails fast and says which field.
- **Docs tables are generated from the data files and checked in CI.** *(new)* The
  numbers in `docs/` cannot drift from the numbers the sim uses.
- **One-folder PyInstaller build, not one-file.** *(new)* One-file unpacks ~100 MB on
  every launch and trips antivirus heuristics.

## Added in v0.7 (2026-09-19)

- **Desktop maps are generated natively at desktop scale, about 3 m per cell.** *(new)* The
  v0.3–v0.6 maps were 3× nearest-neighbour upscales of 128-wide prototype files, so a bigger
  map meant bigger blobs. `worldgen.py` keeps feature size constant (`feature_cells`) and
  fills the extra area with more lakes, roads, towns and ranches. The prototype generator and
  the small scenario files stay as test fixtures and for the tuning harness.
- **Map size: about 9× the v0.6 area (up to 1440×990 cells).** *(new)* Chosen with the
  player; 81× was rejected because a pure-Python simulation cannot hold it.
- **The fire step processes a list of active regions, not the grid.** *(new)* Regions are
  bounding boxes around burning/smoldering/wet cells with a one-cell margin, re-derived each
  tick from 32×32 activity tiles and merged when within two cells. The maths is identical
  to a full pass; the cost follows the fires. Grids up to 256×256 still run the full pass.
- **Long orders plan on a block graph whose nodes are (block, connected component).**
  *(new)* Rivers and lakes inside a block are honest barriers. Only the next few blocks are
  refined onto cells, and a corridor that still turns out to be cut marks the block and
  replans. The fine A* runs on flat Python lists inside a window; NumPy never sits in the
  hot loop. Flow fields remain deferred.
- **Bulldozers have a `road_speed`.** *(documented feel, new mechanism)* On road and gravel
  a travelling dozer moves at 4 cells/s regardless of terrain cost (its lowboy), 1 cell/s
  otherwise, and cuts at the unchanged `cut_rate`. Its path cost row treats roads as
  `speed / road_speed` so routes prefer them. Other classes keep their terrain costs.
- **`win_on_timeout` is an objective.** *(new)* Survival and hold-the-line incidents end
  CONTAINED when the clock runs out with every limit intact.
- **Random incidents are scenarios rolled from a seed, not a separate game mode.** *(new)*
  Save, replay and score paths are unchanged; the seed is the whole definition.
- **Shipped missions are authored by rules, baked to JSON.** *(new)* `tools/author_missions.py`
  places staging, fires and civilians from the generated map; the JSON files it writes stay
  the single source of truth the game loads and the tests check that they match the rules.
- **Renderer patches, never rebuilds.** *(new)* One map-sized surface is updated only where
  the regions changed; decorations are sprite blits limited to the visible window; the
  minimap and scaled view are cached per change; autosaves compress on a worker thread from
  a snapshot taken in milliseconds.

## Explicitly deferred

Multiplayer, voice, weather-driven moisture, unit destruction, audio, sprites. All are in
the brief or the roadmap and none block the fire model.
