"""Command line.

  python -m backburn view  [scenario.json]                 interactive viewer
  python -m backburn run   scenario.json --ticks N [--gif out.gif] [--png out.png]
  python -m backburn replay replay.json [--png out.png]     re-run a replay, print hash
  python -m backburn gen   out.json --seed S [--w W --h H]  write a generated scenario in grid mode
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .scenario import Scenario, load_scenario, save_scenario
from .sim import Simulation

DEFAULT = Path(__file__).resolve().parents[1] / "scenarios" / "prairie_fire.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backburn")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("view"); v.add_argument("scenario", nargs="?", default=str(DEFAULT))

    r = sub.add_parser("run"); r.add_argument("scenario"); r.add_argument("--ticks", type=int, default=600)
    r.add_argument("--gif"); r.add_argument("--png"); r.add_argument("--every", type=int, default=10)
    r.add_argument("--scale", type=int, default=4)

    rp = sub.add_parser("replay"); rp.add_argument("replay"); rp.add_argument("--png")

    g = sub.add_parser("gen"); g.add_argument("out"); g.add_argument("--seed", type=int, default=1)
    g.add_argument("--w", type=int, default=128); g.add_argument("--h", type=int, default=96)

    a = ap.parse_args(argv)

    if a.cmd == "view":
        from .viewer import Viewer
        Viewer(Simulation(load_scenario(a.scenario))).run()
        return 0

    if a.cmd == "run":
        from .render import frame, record_gif
        sim = Simulation(load_scenario(a.scenario))
        if a.gif:
            record_gif(sim, a.gif, a.ticks, every=a.every, scale=a.scale)
        else:
            sim.step(a.ticks)
        print(sim.stats())
        print("hash", sim.state_hash())
        if a.png:
            frame(sim, a.scale).save(a.png)
        return 0

    if a.cmd == "replay":
        from .render import frame
        sim = Simulation.replay(a.replay)
        print(sim.stats()); print("hash", sim.state_hash())
        if a.png:
            frame(sim, 4).save(a.png)
        return 0

    if a.cmd == "gen":
        s = Scenario(name=f"generated-{a.seed}", seed=a.seed, width=a.w, height=a.h)
        s.terrain_grid = s.build_terrain(); s.terrain_mode = "grid"
        save_scenario(s, a.out)
        print("wrote", a.out)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
