# Changelog

## 0.2.0 — 2026-09-18

Gameplay
- Civilians: flee fire, die in it, are rescued by reaching or being flown to a safe zone.
- Helicopter `PICKUP` / `DROPOFF` for civilians and foot crews (smokejumper deployment).
- Ember spotting above a wind threshold; embers fly, land, and ignite probabilistically.
- Slope: optional elevation grid; fire runs uphill (`exp(slope_gain × grade)`).
- Budget, unit costs, availability lists, dispatch delays with staging/airbase arrival.
- Scenario events: wind change, new ignition, reinforcement, budget change, message.
- Objectives and outcomes (`contained` / `failed` / `timeout`) with a scoring breakdown.
- Buildings counted as connected components, not cells.
- Fire boat, hotshots, smokejumpers, P-3 bomber wired up.

Tooling
- Full-state savegames (`.bbsave`, zip of JSON + npz, RNG state included).
- Strict scenario validation with field-naming errors; JSON Schema.
- CLI: `view run replay resume validate gen bench info`; `backburn` console script.
- Viewer: buy menu, outcome banner, overlays (heat/moisture/elevation), quicksave/load,
  help panel, message colouring, extinguish eraser.
- Docs: architecture, fire model, units, scenario format, scoring, controls, tuning,
  development, roadmap; tables generated from data and checked in CI.
- Packaging: PyInstaller spec for a Windows executable; GitHub Actions CI.
- Four scenarios: Prairie Fire, Stranded Hikers, Refinery Row, Wall of Fire.

## 0.1.0 — 2026-09-18

- Python/NumPy SimulationCore: probabilistic cellular automaton with wind, four-state cell
  lifecycle, water/retardant fields, deterministic seeded RNG.
- Units: hose team, cut team, engine, brush truck, dozer, helicopter, water bomber,
  retardant bomber; A* pathfinding by movement class; auto-refill/reload.
- JSON scenarios (generated or explicit grid), command-log replay, headless PNG/GIF
  renderer, pygame viewer, 21 behavioural tests, Godot 4 shell.
