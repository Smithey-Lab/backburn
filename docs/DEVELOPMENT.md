# Development

## Setup

```
git clone <your repo> backburn && cd backburn
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                                          # ~4 s, 41 tests
backburn view                                      # play
```

Python 3.10+. Runtime dependencies are NumPy and Pillow; pygame only for the viewer;
pytest/jsonschema/pyinstaller for development.

## Layout

```
backburn/            the package (SimulationCore + tooling)
  data/              balance JSON — the only place numbers live
scenarios/           shipped scenarios (validated by tests)
schema/              JSON Schema for scenario files
tests/               pytest suite: test_sim.py (model), test_features.py (gameplay, persistence, CLI)
tools/               gen_tables.py (docs from data), front_speed.py (tuning readout)
docs/                this folder
godot/               Godot 4 shell + PORTING.md
packaging/           PyInstaller spec and build scripts for a Windows executable
.github/workflows/   CI
```

## Commands

| Task | Command |
|---|---|
| run tests | `pytest -q` |
| one test | `pytest -q -k spotting` |
| check docs match data | `python tools/gen_tables.py --check` |
| regenerate docs tables | `python tools/gen_tables.py` |
| validate scenarios | `backburn validate scenarios/*.json` |
| measure speed | `backburn bench` |
| headless run with a GIF | `backburn run scenarios/wall_of_fire.json --ticks 900 --gif out.gif` |
| reproduce a bug | play, `F5`, then `backburn replay replay.json --png end.png` |
| Windows build | see `packaging/README.md` |

## Godot shell

`godot/` opens in Godot 4.3+. Validate it headlessly (CI does not, since it needs the binary):

```
godot --headless --path godot --quit-after 10      # prints "[backburn] loaded Prairie Fire — 128x96 …"
```

The shell reads grid-mode scenarios from `godot/data/scenarios/`. After editing a scenario in
`scenarios/`, re-bake:

```
python - <<'PY'
from pathlib import Path
from backburn import load_scenario, save_scenario
for f in Path("scenarios").glob("*.json"):
    s = load_scenario(f); s.terrain_grid = s.build_terrain(); s.terrain_mode = "grid"
    e = s.build_elevation()
    if e is not None: s.elevation = {"mode": "grid", "rows": [[round(float(v), 1) for v in r] for r in e]}
    save_scenario(s, Path("godot/data/scenarios") / f.name)
PY
cp backburn/data/*.json godot/data/
```

## Testing philosophy

Tests assert **relationships**, not magic numbers: wind biases downwind, grass beats
dense forest, a break holds without spotting and fails with it, water reduces burning
cells, replays and savegames hash-match. That way tuning doesn't break the suite but
model regressions do. Every shipped scenario is loaded and stepped in
`test_all_shipped_scenarios_validate_and_run`.

When adding a behaviour: write the test first as the smallest grid that exhibits it
(most tests use 32–80 cell maps and finish in milliseconds).

## Determinism rules (don't break these)

- Never call `random`/`np.random` directly; use `grid.rng`.
- Never iterate a `set` or `dict` of cells to make gameplay decisions; use array order.
- Unit code must be randomness-free.
- If you add state to `FireGrid`, add it to `to_arrays()/meta()/from_arrays()` and the
  savegame test will catch you if you forget.
- If you add a player command, add it to `Simulation._apply()` for replays.

## Style

Type hints everywhere, docstrings on public functions, `from __future__ import
annotations`. Keep the sim free of pygame and Pillow imports. Line length 110.

## Release checklist

1. `pytest -q` green, `python tools/gen_tables.py --check` green, `backburn validate scenarios/*.json`.
2. Bump `__version__` in `backburn/__init__.py` and `pyproject.toml`; add a `CHANGELOG.md` entry.
3. Build the Windows executable (`packaging/`), smoke-test `backburn.exe view`.
4. Tag.
