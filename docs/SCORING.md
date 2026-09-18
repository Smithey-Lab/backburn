# Scoring and outcomes

The original RTS scored saved terrain and buildings; Sandbox dropped mandatory scoring
(brief §3, §6). Backburn keeps scoring available but every weight is a scenario field, so
a Sandbox-style scenario can zero them.

## Outcome

Checked after every tick, in this order:

1. `max_structures_lost` exceeded → **FAILED**
2. `max_civilians_lost` exceeded → **FAILED**
3. `max_area_burned_pct` exceeded → **FAILED**
4. fire is out (no burning cells, no embers in flight, no scar hot enough to re-ignite)
   and, if `rescue_all_civilians`, every civilian is accounted for → **CONTAINED**
5. `duration` reached → **TIMEOUT**, or FAILED if `rescue_all_civilians` and someone is still out

"Fire is out" means `FireGrid.is_out()`. A smoldering scar with residual heat keeps the
scenario running; that's deliberate — mop-up is part of the job.

## Score

`Simulation.score()` returns a breakdown using the scenario's `scoring` weights
(defaults in `scenario.py::DEFAULT_SCORING`):

| Part | Formula | Default weight |
|---|---|---|
| `structures_saved` | buildings not lost × `structure_saved` | 500 |
| `civilians_rescued` | rescued × `civilian_rescued` | 1000 |
| `civilians_lost` | lost × `civilian_lost` | −1500 |
| `area_saved` | unburned fuel cells × `acre_saved` | 1 |
| `budget_remaining` | (budget − spent) × `budget_remaining` | 0.1 |
| `contain_bonus` | `contain_bonus` if CONTAINED | 2000 |
| `time_bonus` | (duration − contained_at) × `time_bonus_per_second` if CONTAINED | 1 |

`total` is the sum. Weights are floats; set any to 0 to ignore it. The score is shown in
the outcome banner and printed by `backburn run`.

## Design notes

- Saved-area scoring uses fuel cells only, so water and roads don't pad the score.
- Buildings, not cells: a 2×2 house is one structure (see `FIRE_MODEL.md`).
- There is no penalty for units lost because ground units retreat rather than die; if a
  future version adds unit losses, add a `unit_lost` weight rather than reusing another.
