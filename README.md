# Backburn

An original top-down wildfire tactics game for Windows. Read the wind, send crews to
cut firebreaks, coordinate air drops, and bring stranded hikers home.

![Backburn tactical view](docs/images/desktop-demo.png)

## Download and play

Download **[Backburn-Setup.exe](https://github.com/Smithey-Lab/backburn/releases/latest/download/Backburn-Setup.exe)**,
open it, and click **Install Backburn**. No Python, Git, or administrator account is needed.
The Desktop shortcut opens a launcher with **Play** and **Install update** buttons.
Saves and scores survive updates. Offline play is supported after installation.

Prefer portable? Extract the [Windows zip](https://github.com/Smithey-Lab/backburn/releases/latest/download/Backburn-Windows-x64.zip)
and run Backburn.exe. Windows binaries are currently unsigned.
See [installation details](docs/INSTALLING.md) and [release notes](docs/RELEASE_NOTES.md).

## The demo

- Four incidents: Prairie Fire, Stranded Hikers, Refinery Row, Wall of Fire.
- Wind, slope, fuel, moisture, water, retardant, smoldering and airborne embers.
- Engines, brush trucks, hose teams, cut teams, dozers, hotshots, helicopters and bombers.
- Helicopter rescues, resource dispatch, weather events, objectives and scores.
- Mission briefings, field guide, unit roster, minimap, tactical overlays and sandbox tools.
- Original procedural sprites, animated fire and synthesized interface sounds.
- Quicksaves, autosaves, replay export, personal bests and GitHub updates.

Single player. Multiplayer, voice and a full scenario authoring interface are future work.
This is a game, not an operational wildfire model. Balance is experimental.

## Controls

| Action | Control |
|---|---|
| Select unit | Left-click map or roster |
| Context order | Right-click |
| Cut line / aircraft drop | Right-drag from start to end |
| Queue orders | Hold Shift |
| Rescue | Select helicopter, click hiker; right-click safe zone to unload |
| Force move / restore context orders | M / Q |
| Pause and plan | Space |
| Speed | 1 / 2 / 3 |
| Pan / zoom / fit | WASD or middle-drag / wheel / Home |
| Dispatch / help / overlay | B / H / O |
| Save / load / export replay | F6 / F7 / F5 |

Incidents open paused for planning. Normal speed is one simulation second per real second.
Maps extend beyond the initial camera view; use WASD, the wheel or the minimap to explore,
and Home for an overview.

Start with Stranded Hikers for a short rescue mission, or Prairie Fire to use the full fleet.

## Development

Python 3.14 is used for CI and Windows builds; the source requires Python 3.12 or newer.

```sh
python -m venv .venv
# Activate the virtual environment for your shell, then:
pip install -r requirements-dev.txt
python -m backburn.game
python -m pytest
ruff check .
ruff format --check .
python tools/gen_tables.py --check
python tools/build_release.py  # Windows
```

The original tuning harness remains available as `python -m backburn view` and the
headless tools as `python -m backburn --help`. `godot/` is an archived port experiment;
the shipping desktop game uses pygame-ce and the existing NumPy simulation.

`main` is the release branch, `develop` the integration branch, and `feature/*` / `fix/*`
branches carry changes through pull requests. See [repository workflow](docs/REPOSITORY.md),
[architecture decisions](DECISIONS.md), [simulation docs](docs/ARCHITECTURE.md), and
[security policy](SECURITY.md).

## Project origins

Built from the user-supplied Backburn v0.2.0 prototype and research brief, inspired by
classic FireJumpers gameplay. Code and visuals are original; no extracted game assets
or original-game code are distributed. Not affiliated with the original developer.
The repository is public for development and distribution. No open-source license is
currently granted; the prototype's all-rights-reserved status is retained.
