# Fire model

The wildfire is a probabilistic cellular automaton on an 8-neighbour grid,
implemented in `backburn/fire.py` and fully vectorised with NumPy. It follows the
research brief's suggested model (§8):

> base fuel rate × wind factor × slope factor × dryness × neighbour heat × (1 − suppression)

Everything numeric is in `backburn/data/terrain.json`; the tables below are generated
from that file by `tools/gen_tables.py` and checked in CI.

## Cell state

Each cell carries: `terrain` (type id), `fuel` (remaining, 0..1.6), `moisture`
(0..1, relaxes toward `base_moisture`), `state`, `heat` (emitted this tick),
`exposure` (received this tick, for the heat overlay), `water` (temporary
suppression), `retardant` (long-lived suppression), `smolder_timer`,
`ignited_at`, `elevation`.

```
 UNBURNED ──ignition──▶ BURNING ──fuel exhausted──▶ SMOLDER ──timer──▶ COLD
     ▲                    │
     └────── water ≥ threshold (fuel × 0.85, moisture += 0.5) ──┘
```

A cell put out by water goes **back to UNBURNED** with less fuel and high
moisture. When that moisture dries (`moisture_dry_rate`) the cell can ignite
again. This single rule produces the "apparent rekindling" the brief documents
for dense forest without any special-casing.

## The step

For `dt` = 1 simulated second:

1. **Emitted heat.** Burning cells emit `heat_output[terrain]`. Smoldering cells emit
   `heat_output × smolder_heat_fraction × (timer / smolder_time)`, so a fresh burn scar
   can still ignite a neighbour but a cooling one can't.
2. **Exposure.** For each of the 8 directions, the neighbour's emitted heat is shifted
   onto the target cell and multiplied by
   - a **wind factor** `max(upwind_min, 1 + wind_gain × speed × cos θ) × distance_weight`
     where θ is the angle between the direction of travel and the wind vector and
     diagonals weigh 0.7071;
   - a **slope factor** `exp(slope_gain × grade)` where grade = rise/run from source to
     target (clamped ±1). Fire runs uphill. Flat maps skip this entirely.
3. **Hazard → probability.**
   `hazard = exposure × exposure_scale × ignition_rate[terrain] × dryness × clip(1 − retardant_strength × retardant) × (1 − water)`
   then `P = 1 − exp(−hazard × dt)`. One uniform roll per cell decides ignition.
   `dryness = clip(1 − moisture, dryness_floor, 1)`.
4. **Fuel.** Burning cells lose `burn_rate × dt`; at zero they smolder for `smolder_time` seconds.
5. **Water** at or above `water_extinguish_threshold` extinguishes a burning cell (see the diagram).
6. **Smolder** timers count down; water speeds cooling ×(1 + 6·water).
7. **Ignitions** from step 3 are applied last so a cell never burns on the tick it starts.
8. **Embers** due to land are tested for ignition; burning cells launch new ones if the wind is strong enough.
9. **Decay.** Water evaporates (`water_decay`), moisture relaxes toward base
   (`moisture_dry_rate`), retardant fades slowly (`retardant_decay`).

## Ember spotting

Only above `spot_min_wind` m/s and only from fuels whose `heat_output ≥ spot_min_heat`
(forest, dense forest, structures — not grass). Each such burning cell launches an ember
with probability `spot_rate × (wind − spot_min_wind) × heat × dt`. The ember flies
`spot_dist_base + (wind − spot_min_wind) × spot_dist_gain × U(0,1)` cells downwind with
Gaussian jitter (`spot_jitter`), arriving after `dist / (wind × 0.5)` ticks. On landing an
unburned fuel cell ignites with probability `spot_ignite × dryness × (1 − retardant)`.

This is what lets a narrow handline fail in a gale (`test_spotting_jumps_a_break_only_in_high_wind`)
while holding in a breeze. Embers in flight are drawn as yellow points.

## Suppression

| Field | Applied by | Effect | Decay |
|---|---|---|---|
| `water` | hose teams, engines, brush trucks, fire boat, helicopter and water-bomber drops | multiplies hazard by (1 − water); ≥ threshold extinguishes; adds moisture | fast (`water_decay`) |
| `retardant` | retardant bombers, P-3 | multiplies hazard by clip(1 − 1.5 × retardant); full coverage blocks ignition until it decays below ~0.67 | slow (`retardant_decay`) |
| `FIREBREAK` terrain | cut crews, hotshots, smokejumpers, dozers | fuel 0.02, ignition rate 0.05 — effectively won't carry fire | permanent |

Roads, gravel, sand and water have zero fuel and never burn.

## Buildings

Structure cells are grouped into 4-connected components at load time. A **building is
lost when any of its cells is no longer UNBURNED**. Objectives, scoring, and the HUD
count buildings, not cells.

## Tuned behaviour (reference values)

Measured with `pytest tests/test_sim.py` on a flat map after 120 s, single ignition:

| Fuel | 6 m/s wind, downwind front | upwind | no wind |
|---|---|---|---|
| grass | 0.35 cells/s | 0.07 cells/s | 0.21 cells/s |
| shrub | 0.28 | 0.08 | 0.19 |
| forest | 0.16 | 0.07 | 0.12 |
| dense forest | 0.07 | 0.05 | 0.07 |

(`python tools/front_speed.py` reprints this after any change.)

A 1-cell firebreak holds at 12 m/s without spotting; a 3-cell break in forest fails to
embers above ~15 m/s. One pass of water at strength 1.0 puts out ~80% of burning cells
in its radius. These are **starting points** (brief §15 calls them a design space);
`docs/TUNING.md` describes how to move them.

## Terrain table

<!-- BEGIN GENERATED: terrain -->
| Terrain | Letter | Fuel | Ignition rate | Burn rate | Heat | Smolder (s) | FOOT | ROAD | OFFROAD | AIR | WATERCRAFT |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `WATER` | W | 0.0 | 0.0 | 0.0 | 0.0 | 0 | — | — | — | 1.0 | 1.0 |
| `GRASS` | G | 0.35 | 0.55 | 0.045 | 0.6 | 15 | 1.0 | 2.2 | 1.3 | 1.0 | — |
| `SHRUB` | S | 0.6 | 0.32 | 0.05 | 0.85 | 40 | 1.4 | — | 1.8 | 1.0 | — |
| `FOREST` | F | 1.0 | 0.14 | 0.03 | 1.0 | 90 | 1.8 | — | 2.6 | 1.0 | — |
| `DENSE_FOREST` | D | 1.6 | 0.06 | 0.018 | 1.35 | 180 | 2.4 | — | — | 1.0 | — |
| `ROAD` | R | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0.8 | 0.6 | 0.7 | 1.0 | — |
| `GRAVEL` | V | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0.9 | 1.0 | 0.9 | 1.0 | — |
| `STRUCTURE` | B | 1.2 | 0.12 | 0.02 | 1.2 | 120 | — | — | — | 1.0 | — |
| `FIREBREAK` | X | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0.9 | — | 1.0 | 1.0 | — |
| `SAND` | A | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 1.2 | — | 1.4 | 1.0 | — |
<!-- END GENERATED: terrain -->

Movement columns are cost multipliers per movement class (1.0 = full speed, — = impassable).

## Fire constants

<!-- BEGIN GENERATED: fire_constants -->
| Constant | Value |
|---|---|
| `exposure_scale` | 0.18 |
| `wind_gain` | 0.2 |
| `wind_upwind_min` | 0.12 |
| `slope_gain` | 1.5 |
| `dryness_floor` | 0.08 |
| `water_decay` | 0.02 |
| `water_extinguish_threshold` | 0.45 |
| `water_moisture_gain` | 0.5 |
| `retardant_decay` | 0.001 |
| `smolder_heat_fraction` | 0.35 |
| `smolder_ignites_neighbours` | True |
| `moisture_dry_rate` | 0.004 |
| `heat_decay_cold` | 0.05 |
| `retardant_strength` | 1.5 |
| `cell_size_m` | 10.0 |
| `spot_min_wind` | 8.0 |
| `spot_rate` | 0.002 |
| `spot_min_heat` | 0.6 |
| `spot_ignite` | 0.5 |
| `spot_dist_base` | 4.0 |
| `spot_dist_gain` | 1.2 |
| `spot_jitter` | 1.5 |
<!-- END GENERATED: fire_constants -->

## What is documented vs. invented

- **Documented** (brief §3, §8): terrain- and wind-dependent spread, grass fast/easy,
  dense forest slow/hot/rekindles, hoses burn, cut lines depend on wind, water is
  temporary, retardant is long-lived, burned cells stay hot, seeded RNG.
- **Inferred**: the 8-neighbour lattice and the exact shape of the wind term (the
  original's constants are unknown).
- **New**: the UNBURNED-with-moisture rekindle mechanism, exp() slope factor, the ember
  flight model, building labelling.

## Known limitations

- No fuel moisture from weather/humidity; `moisture` is a scenario constant plus water.
- No crown-fire vs. surface-fire distinction; forest is one fuel.
- Embers ignore intervening terrain (they fly over water and roads, as real ones do,
  but also over anything else).
- Cell size is fixed at 10 m for slope purposes; grid sizes above 256 are untested for feel.
