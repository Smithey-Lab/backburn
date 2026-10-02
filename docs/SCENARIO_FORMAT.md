# Scenario file format

Scenarios are JSON. `backburn validate file.json` checks them with exact error messages;
`schema/scenario.schema.json` is the equivalent JSON Schema for editors and CI.
Everything is optional except that a scenario needs some fuel and, to be a game, an
ignition or an `ignite` event.

```jsonc
{
  "name": "Refinery Row",
  "briefing": "Shown to the player at start.",          // string
  "notes": "Author notes, never shown.",

  "seed": 33,                     // int — drives terrain generation AND fire randomness
  "width": 128, "height": 80,     // 8..512; ignored in grid mode (taken from rows)
  "moisture": 0.10,               // 0..1 base fuel moisture (lower = drier = faster)
  "wind": { "speed": 7, "bearing": 100 },   // m/s, compass degrees the wind blows TOWARD
  "duration": 1500,               // seconds until timeout

  "airbase": [3, 3],              // where aircraft spawn / bombers reload
  "staging": [8, 40],             // where purchased ground units arrive (default: airbase)
  "safe_zone": [12, 62, 5],       // [x, y, radius] — civilians inside are rescued (optional)

  "budget": 12000,                // null/absent = unlimited (Sandbox mode)
  "available_units": ["ENGINE", "RETARDANT_BOMBER"],   // absent = all non-civilian types

  "terrain": { "mode": "generate", "params": { ... } }   // or grid mode, see below
  "elevation": { "mode": "generate", "relief": 90, "seed": 3 },   // optional; or grid mode

  "ignitions": [ { "x": 8, "y": 30, "radius": 1 } ],
  "units":     [ { "type": "ENGINE", "x": 8, "y": 40 } ],   // snapped to passable cells

  "objectives": { ... },          // see Objectives
  "scoring":    { ... },          // see docs/SCORING.md
  "events":     [ ... ]           // see Events
}
```

## Terrain

**Generate mode** — seeded procedural map. Params (all optional):

| Param | Default | Meaning |
|---|---|---|
| `water_level` | 0.28 | noise threshold below which cells are water (with a sand fringe) |
| `shrub_level` | 0.45 | vegetation-noise threshold for shrub |
| `forest_level` | 0.52 | … for forest |
| `dense_level` | 0.68 | … for dense forest |
| `roads` | 2 | number of bent roads |
| `structures` | 14 | buildings placed near roads (1–2 cells each) |
| `lake` | true | set false for no water |

**Grid mode** — explicit rows of single-letter codes, one string per row, all the same length:

| Letter | Terrain | Letter | Terrain |
|---|---|---|---|
| `W` | water | `R` | road |
| `G` | grass | `V` | gravel |
| `S` | shrub | `B` | structure |
| `F` | forest | `X` | firebreak |
| `D` | dense forest | `A` | sand |

`backburn gen out.json --seed N` writes a generated map in grid mode so you can hand-edit
it. The viewer's editor (`E`) paints terrain into the running sim; use `F5` to save a
replay whose embedded scenario carries your paint commands, or bake with `gen`.

## Elevation

Optional. `{"mode": "generate", "relief": metres, "seed": n, "octaves": 3}` makes rolling
terrain with that total relief; `{"mode": "grid", "rows": [[...]]}` gives explicit metres
per cell (height × width). Fire runs uphill; see `FIRE_MODEL.md`. Without elevation the
map is flat and the slope term is skipped.

## Objectives

| Key | Type | Effect |
|---|---|---|
| `max_structures_lost` | int | **FAILED** the moment more buildings than this have burned |
| `max_civilians_lost` | int | FAILED when more civilians than this are lost |
| `max_area_burned_pct` | number | FAILED when burned fuel area exceeds this percentage |
| `rescue_all_civilians` | bool | required for CONTAINED; at timeout with civilians still out → FAILED |
| `win_on_contained` | bool (default true) | **CONTAINED** as soon as nothing burns, no embers fly, and no scar is hot enough to re-ignite |

Outcomes: `running`, `contained`, `failed`, `timeout` (duration reached without failing).
`Simulation.step()` returns 0 once an outcome is set.

## Events

Each event has an `at` time in seconds and one or more actions:

```jsonc
{ "at": 400, "ignite": { "x": 70, "y": 12, "radius": 1 }, "message": "Spot fire reported" }
{ "at": 600, "wind": { "bearing": 130 } }          // omit speed or bearing to keep it
{ "at": 900, "spawn": { "type": "P3_BOMBER" } }    // free reinforcement, normal dispatch delay
{ "at": 600, "budget": 3000 }                       // negative to cut funds
```

Events are part of the scenario, so replays reproduce them without logging.

## Units

`{"type": "...", "x": .., "y": ..}` — types are the keys of `data/units.json`. Positions are
snapped to the nearest cell the unit's movement class can stand on (within 12 cells), so
placing an engine roughly near a road is enough. `CIVILIAN` entries are the rescue objective.

## Validation rules

`validate()` raises `ScenarioError` naming the field for: unknown unit types, unknown
objective/scoring keys, out-of-bounds points, ragged or mis-lettered terrain rows,
non-numeric numbers, events without `at` or without an action, elevation rows of the
wrong shape, missing files, invalid JSON (with line number). `tests/test_features.py`
covers these.


### Variable wind

`wind.variable` is an optional boolean (default `false`). Desktop mission layouts enable it.
Gusts transition smoothly between seeded targets every 35 simulated seconds, within
-25%/+35% of the current baseline speed and +/-20 degrees of its bearing. Scripted or
player wind changes establish a new baseline. Weather state is saved and replayed.
