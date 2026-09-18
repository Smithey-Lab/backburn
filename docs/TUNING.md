# Tuning guide

Every gameplay number lives in `backburn/data/terrain.json` and `backburn/data/units.json`.
The brief (§15) is explicit that the original constants are unknown, so this is a design
space. The loop:

1. Change a number.
2. `pytest -q` — the tests guard *relationships* (wind bias, fuel ordering, breaks hold,
   water works, spotting needs wind), not exact values, so they tell you when a change
   broke the model rather than merely moved it.
3. Look: `backburn run scenarios/prairie_fire.json --ticks 900 --gif out.gif` or play it in the
   viewer. `--overlay heat` shows where the fire is pushing.
4. `python tools/gen_tables.py` so the docs match.

## Fire

| Want | Change |
|---|---|
| everything faster/slower | `exposure_scale` (0.18). Roughly linear in front speed. |
| more/less wind anisotropy | `wind_gain` (0.14) for the downwind boost, `wind_upwind_min` (0.12) for how dead the upwind side is |
| one fuel type faster | its `ignition_rate` × `heat_output` product sets front speed; `burn_rate` sets how long a cell burns (and threatens neighbours); `smolder_time` sets rekindle risk |
| break jumps in less wind | `spot_min_wind` (8) and `spot_rate` (0.002); `spot_dist_base`/`spot_dist_gain` for how far |
| water more/less effective | `water_extinguish_threshold` (0.45), `water_decay` (0.02), unit `spray_water` rates |
| retardant lasts longer | `retardant_decay` (0.001); `retardant_strength` (1.5) sets how much coverage blocks |
| slopes matter more | `slope_gain` (1.5): multiplier is exp(gain × grade) |
| drought | scenario `moisture` (0.08–0.15 is normal); `dryness_floor` for how immune wet cells are |

Front-speed sanity check (flat grass, 6 m/s) — `python tools/front_speed.py`:

```
fuel            downwind   upwind  no wind
GRASS               0.35     0.07     0.21
DENSE_FOREST        0.07     0.05     0.07
```

Target neighbourhood: grass downwind 0.3–0.4 with upwind well under half of that, dense
forest about a fifth of grass.

## Units

| Want | Change |
|---|---|
| engines useful off-road | `ROAD` move cost for grass (2.2) in terrain.json |
| hose teams reach further | `hose_max_length` |
| helicopter more decisive | `capacity`, `drop_strength`, `drop_width`; `refill_rate` for turnaround |
| bombers less spammy | `reload_seconds` |
| dozer line holds in wind | `cut_width` 2 → 3 |
| budget pressure | scenario `budget` and per-unit `cost`; `arrival_seconds` for dispatch pain |

Speed is cells/s; 1.0 ≈ 36 km/h. Crews at 0.8–1.1 walk; engines 2.6 on roads.

## Scenario difficulty

Order of leverage: wind speed (spotting starts at 8 m/s) → moisture → fuel mix
(`forest_level`, `dense_level`) → number of ignitions → budget → objectives. Add a wind
event at the two-thirds mark to punish a plan that only works for one direction.

## Performance

`grid.step()` is O(cells) NumPy; 256×256 costs ~4× a 128×128 map. Unit cost is dominated
by A* replans; if a scenario with many units stutters, raise the replan cooldown in
`Unit._plan_to` or reduce `max_expand` in `pathfinding.find_path`. `backburn bench`
reports ticks/s.
