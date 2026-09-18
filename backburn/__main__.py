"""Command line entry point (`backburn` after `pip install -e .`, or `python -m backburn`).

backburn view     [scenario.json | save.bbsave]          interactive viewer
backburn run      scenario.json [--ticks N] [--gif out.gif] [--png out.png] [--overlay heat]
backburn replay   replay.json [--png out.png] [--until TICK]
backburn resume   save.bbsave [--ticks N] [--png out.png]
backburn validate scenario.json [...]                     check files, exit 1 on any error
backburn gen      out.json [--seed S --w W --h H --relief M]  bake a generated map to grid mode
backburn bench    [scenario.json] [--ticks N]             ticks/second on this machine
backburn info     scenario.json                           print a summary
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .scenario import Scenario, ScenarioError, load_scenario, save_scenario
from .sim import Simulation

DEFAULT = Path(__file__).resolve().parents[1] / "scenarios" / "prairie_fire.json"


def _load_any(path: str) -> Simulation:
    if path.endswith(".bbsave"):
        return Simulation.load_state(path)
    return Simulation(load_scenario(path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="backburn", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--version", action="version", version=f"backburn {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("view", help="interactive viewer")
    v.add_argument("scenario", nargs="?", default=str(DEFAULT))

    r = sub.add_parser("run", help="headless run")
    r.add_argument("scenario")
    r.add_argument("--ticks", type=int, default=600)
    r.add_argument("--gif")
    r.add_argument("--png")
    r.add_argument("--every", type=int, default=10)
    r.add_argument("--scale", type=int, default=4)
    r.add_argument("--overlay", choices=["heat", "moisture", "elevation"])
    r.add_argument("--save", help="write a .bbsave at the end")

    rp = sub.add_parser("replay", help="re-run a replay file")
    rp.add_argument("replay")
    rp.add_argument("--png")
    rp.add_argument("--until", type=int)

    rs = sub.add_parser("resume", help="continue from a savegame headlessly")
    rs.add_argument("save")
    rs.add_argument("--ticks", type=int, default=600)
    rs.add_argument("--png")

    va = sub.add_parser("validate", help="validate scenario files")
    va.add_argument("files", nargs="+")

    g = sub.add_parser("gen", help="generate a scenario in grid mode")
    g.add_argument("out")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--w", type=int, default=128)
    g.add_argument("--h", type=int, default=96)
    g.add_argument("--relief", type=float, default=0.0, help="elevation relief in metres (0 = flat)")

    b = sub.add_parser("bench", help="measure ticks per second")
    b.add_argument("scenario", nargs="?", default=str(DEFAULT))
    b.add_argument("--ticks", type=int, default=300)

    inf = sub.add_parser("info", help="print a scenario summary")
    inf.add_argument("scenario")

    a = ap.parse_args(argv)
    try:
        return _dispatch(a)
    except ScenarioError as e:
        print(f"scenario error: {e}", file=sys.stderr)
        return 2


def _dispatch(a) -> int:
    if a.cmd == "view":
        from .viewer import Viewer

        Viewer(_load_any(a.scenario)).run()
        return 0

    if a.cmd == "run":
        from .render import frame, record_gif

        sim = Simulation(load_scenario(a.scenario))
        if a.gif:
            record_gif(sim, a.gif, a.ticks, every=a.every, scale=a.scale, overlay=a.overlay)
        else:
            sim.step(a.ticks)
        _report(sim)
        if a.png:
            frame(sim, a.scale, overlay=a.overlay).save(a.png)
        if a.save:
            sim.save_state(a.save)
        return 0

    if a.cmd == "replay":
        from .render import frame

        sim = Simulation.replay(a.replay, until_tick=a.until)
        _report(sim)
        if a.png:
            frame(sim, 4).save(a.png)
        return 0

    if a.cmd == "resume":
        from .render import frame

        sim = Simulation.load_state(a.save)
        sim.step(a.ticks)
        _report(sim)
        if a.png:
            frame(sim, 4).save(a.png)
        return 0

    if a.cmd == "validate":
        bad = 0
        for f in a.files:
            try:
                s = load_scenario(f)
                print(
                    f"OK    {f}  ({s.name}, {s.width}×{s.height}, {len(s.units)} units, {len(s.ignitions)} ignitions)"
                )
            except ScenarioError as e:
                bad += 1
                print(f"ERROR {e}")
        return 1 if bad else 0

    if a.cmd == "gen":
        s = Scenario(name=f"generated-{a.seed}", seed=a.seed, width=a.w, height=a.h)
        s.terrain_grid = s.build_terrain()
        s.terrain_mode = "grid"
        if a.relief > 0:
            s.elevation = {"mode": "generate", "relief": a.relief}
        save_scenario(s, a.out)
        print("wrote", a.out)
        return 0

    if a.cmd == "bench":
        sim = Simulation(load_scenario(a.scenario))
        t0 = time.perf_counter()
        n = sim.step(a.ticks)
        dt = time.perf_counter() - t0
        print(
            f"{n} ticks in {dt:.2f}s  →  {n / dt:.0f} ticks/s  ({n / dt / 10:.0f}× real time at 10 ticks/s)"
        )
        return 0

    if a.cmd == "info":
        s = load_scenario(a.scenario)
        print(
            f"{s.name}\n  size {s.width}×{s.height}  seed {s.seed}  moisture {s.moisture}  duration {s.duration:.0f}s"
        )
        print(
            f"  wind {s.wind_speed} m/s toward {s.wind_bearing}°  terrain {s.terrain_mode}  elevation {'yes' if s.elevation else 'no'}"
        )
        print(
            f"  ignitions {len(s.ignitions)}  units {len(s.units)}  events {len(s.events)}  budget {s.budget}"
        )
        print(f"  objectives {s.objectives}")
        if s.briefing:
            print(f"  briefing: {s.briefing}")
        return 0
    return 1


def _report(sim: Simulation) -> None:
    st = sim.stats()
    print(
        f"t={st['time']:.0f}s outcome={st['outcome']} burning={st['burning']} burned={st['area_burned_pct']}% "
        f"buildings lost {st['structures_lost']}/{st['structures_total']} civilians {st['civilians']} "
        f"spot_fires={st['spot_fires']}"
    )
    if sim.outcome_reason:
        print("reason:", sim.outcome_reason)
    print("score:", sim.score())
    print("hash:", sim.state_hash())


if __name__ == "__main__":
    sys.exit(main())
