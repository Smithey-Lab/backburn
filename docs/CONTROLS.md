# Viewer controls

The pygame viewer (`backburn view [scenario.json | save.bbsave]`) implements the control
scheme from the research brief §9. It is a tuning harness, not the final UI, but it is
fully playable.

## Mouse

| Input | Action |
|---|---|
| Left click on a unit | select it (white ring) |
| Left click on empty map | deselect |
| Left click on a civilian/crew **with an aircraft selected** | `PICKUP` order (Shift = queue) |
| Right click | order for the selected unit: `MOVE`; `SUPPRESS` for engines/brush trucks/fire boat; `HOSE` for hose teams; `DROPOFF` if the unit is carrying passengers |
| Right **drag** | line order: `CUT` for cut crews, hotshots, smokejumpers, dozers; `DROP` for aircraft (start → end of the run) |
| Shift + any order | queue after current orders instead of replacing |
| Mouse wheel | zoom about the cursor |
| Middle drag | pan |

## Keyboard

| Key | Action |
|---|---|
| `W A S D` | pan |
| `Space` | pause / resume |
| `1` `2` `3` | speed 1× / 3× / 8× |
| `[` `]` | wind bearing −15° / +15° |
| `-` `=` | wind speed −1 / +1 m/s |
| `B` | buy menu; press the letter next to a unit to purchase (budget and dispatch delay apply); `B`/`Esc` closes |
| `Tab` | cycle selection through your units |
| `Esc` | clear selection / mode / editor / help; press twice with nothing selected to quit |
| `O` | cycle map overlay: none → heat (incoming exposure) → moisture → elevation |
| `E` | terrain editor on/off; `0`–`9` picks the brush (terrain.json order: 0 water, 1 grass, 2 shrub, 3 forest, 4 dense, 5 road, 6 gravel, 7 structure, 8 firebreak, 9 sand); left-drag paints |
| `I` (hold) + left click | ignite a cell |
| `X` (hold) + left click | extinguish (editor eraser, not gameplay) |
| `F5` | save `replay.json` in the working directory |
| `F6` / `F7` | quicksave / quickload `quicksave.bbsave` |
| `H` | help panel |

## HUD

Top line: time, speed, burning cells, burned %, buildings lost/total, civilians
rescued/total/lost, wind, budget remaining, incoming units, active overlay/mode.
Second line: the selected unit's state, current order, tank, passengers, queue length.
Bottom-left: last seven messages (orange = objective, blue = scenario event, grey = system,
white = unit radio). Top-right: wind arrow. Green circle: safe zone. Yellow square:
staging. White square: airbase. Yellow dots: embers in flight.

Everything the viewer does goes through `Simulation.cmd_*`, so a session is always
replayable with `F5`.
