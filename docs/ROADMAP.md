# Roadmap

Mapped to the research brief's MVP order (§11). Status as of v0.2.0.

| # | Step | Status | Where |
|---|---|---|---|
| 1 | Map + pan/zoom + tile properties | done | scenario.py, viewer.py |
| 2 | Ignition and cell-to-cell spread | done | fire.py |
| 3 | Wind + terrain-dependent spread | done, plus slope and ember spotting | fire.py |
| 4 | Hose team + cut team | done | units.py |
| 5 | Helicopter move / refill / line drop | done, plus pickup/drop-off | units.py |
| 6 | Buildings, objectives, scoring | done | sim.py, docs/SCORING.md |
| 7 | Engine / brush truck / dozer | done | units.py, data/units.json |
| 8 | Water + retardant aircraft | done (water bomber, retardant bomber, P-3) | units.py |
| 9 | Scenario editor | partial: paint terrain, ignite, extinguish, wind, buy, bake with `gen`; no unit placement UI, no save-as-scenario from the viewer | viewer.py, __main__.py |
| 10 | Polish fire/smoke, sound, classic UI feedback | not started (placeholder rectangles by design) | Godot |
| 11 | Save/load + deterministic replay | done (replay + full savegame) | sim.py |
| 12 | Cooperative multiplayer | not started | — |

## Next, in order

1. **Godot port of SimulationCore** (`godot/PORTING.md`). Everything above is the
   spec; the Python stays as the reference implementation and test oracle.
2. **Editor completeness**: place/remove units, edit objectives and events, "save as
   scenario" from the viewer (currently `gen` + hand edit).
3. **Fire visuals**: smoke particles, flame sprites, burn-scar texture; unit sprites.
   Original art only.
4. **Sound**: radio chatter for unit messages, aircraft passes, wind.
5. **Weather**: humidity/time-of-day moisture curve, gusts, wind schedule beyond events.
6. **Unit losses**: overrun vehicles can be destroyed; `unit_lost` score weight.
7. **Multiplayer**: lockstep on the command log (the replay format is already the
   wire format), chat, IC/moderator role. Last, per the brief.

## Non-goals

3D, a general-purpose engine, faithful reproduction of the original's assets.
