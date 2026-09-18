# Porting the Python SimulationCore into Godot

Status: scaffolding only. `project.godot` opens, the camera works, nothing simulates.

## What ports where

| Python module            | Godot home                          | Language | Notes |
|--------------------------|-------------------------------------|----------|-------|
| `backburn/fire.py`       | `Sim/SimulationCore.cs`             | C#       | The one performance-sensitive piece. Port `step()` loop-for-loop; keep the 8-offset table and wind-factor cache. |
| `backburn/config.py`     | `Sim/Balance.cs`                    | C#       | Read the same `data/terrain.json` / `data/units.json` files at startup. Do not duplicate the numbers. |
| `backburn/pathfinding.py`| `Sim/Pathfinding.cs` or Godot `AStarGrid2D` | C# | `AStarGrid2D` is fine for ground units if you set per-cell weights from the move-cost table each time terrain changes. |
| `backburn/units.py`      | `Sim/Units.cs`                      | C#       | State machines are plain code; nothing Godot-specific. |
| `backburn/scenario.py`   | `Sim/Scenario.cs`                   | C#       | JSON format is already defined; keep both modes. |
| `backburn/render.py`     | `scripts/MapView.gd`, `FireLayer`   | GDScript | Terrain → TileMap once; fire/water/retardant → one `Image` updated per tick → `ImageTexture`. |
| `backburn/viewer.py`     | `scripts/Main.gd`, `scripts/Orders.gd`, `UI/` | GDScript | Same control scheme. UI uses Control nodes. |

## Why C# for the sim

GDScript evaluates the per-cell loop at roughly interpreter speed. A 128×96 grid at
10 ticks/s is ~120k cell-updates/s *per pass*, and `step()` makes several passes.
C# with `Span<float>` over flat arrays runs this in well under a millisecond per
tick, which is the same headroom the NumPy prototype has (~2 ms/tick measured).

If C# is unwanted, the fallback is `RenderingDevice` compute shaders for the
spread pass, which is more work than the C# port.

## Order of work

1. `Balance.cs` — load JSON, verify every value matches `python -c "from backburn.config import *"`.
2. `SimulationCore.cs` — port `FireGrid.step()`. Verify with the **replay files**:
   run `python -m backburn run scenarios/prairie_fire.json --ticks 300` and make the
   C# version produce the same `burned_cells` within a few percent for the same seed.
   Exact hash parity is not required (different RNG), behavioural parity is.
3. `MapView.gd` — draw it. You now have MVP steps 1–3 in-engine.
4. `Units.cs` + `Orders.gd` — MVP steps 4–8.
5. Scenario editor UI — step 9.

## Determinism note

The Python prototype uses NumPy's PCG64. Use `System.Random` with a seed in C#
(or port PCG64 if replay files must cross languages — they don't need to).
