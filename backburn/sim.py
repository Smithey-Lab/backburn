"""Simulation facade.

Owns one FireGrid and one World, advances them with a fixed timestep, and keeps
an event log of every player command so a run can be replayed exactly:
same scenario + same seed + same command log ⇒ same state hash.

The renderer and UI only ever talk to this class.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import TERRAIN, TerrainType
from .fire import FireGrid
from .scenario import Scenario
from .units import CUT, DROP, HOSE, MOVE, REFILL, SUPPRESS, Order, Unit, World

TICK_DT = 1.0            # simulated seconds per tick
TICKS_PER_SECOND = 10    # at 1× speed (DECISIONS.md)


@dataclass
class Command:
    """One player action, recorded for replay."""
    tick: int
    kind: str
    args: dict = field(default_factory=dict)


class Simulation:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        terrain = scenario.build_terrain()
        self.grid = FireGrid(terrain, seed=scenario.seed, base_moisture=scenario.moisture)
        self.grid.set_wind(scenario.wind_speed, scenario.wind_bearing)
        self.world = World(airbase=tuple(scenario.airbase))
        self.tick = 0
        self.log: list[Command] = []
        self.paused = False
        self.speed = 1.0
        self.messages: list[tuple[float, str]] = []

        for ig in scenario.ignitions:
            self.grid.ignite(int(ig["x"]), int(ig["y"]), int(ig.get("radius", 0)))
        for u in scenario.units:
            self.world.add(u["type"], float(u["x"]), float(u["y"]), self.grid)

    # ---- player commands (all logged) ---------------------------------------

    def _record(self, _kind: str, **args) -> None:
        self.log.append(Command(self.tick, _kind, args))

    def cmd_ignite(self, x: int, y: int, radius: int = 0) -> None:
        self._record("ignite", x=x, y=y, radius=radius)
        self.grid.ignite(x, y, radius)

    def cmd_wind(self, speed: float, bearing: float) -> None:
        self._record("wind", speed=speed, bearing=bearing)
        self.grid.set_wind(speed, bearing)

    def cmd_spawn(self, utype: str, x: float, y: float) -> Unit:
        self._record("spawn", utype=utype, x=x, y=y)
        return self.world.add(utype, x, y, self.grid)

    def cmd_order(self, uid: int, kind: str, target=None, points=None, queue: bool = False) -> None:
        self._record("order", uid=uid, kind=kind, target=target, points=points, queue=queue)
        u = self.world.by_id(uid)
        if u is None:
            return
        o = Order(kind, tuple(target) if target else None, [tuple(p) for p in (points or [])])
        u.give(o, queue=queue)

    def cmd_paint(self, x: int, y: int, ttype: int, radius: int = 0) -> None:
        """Scenario editor: paint terrain."""
        self._record("paint", x=x, y=y, ttype=int(ttype), radius=radius)
        ys, xs = self.grid._disc(x, y, radius)
        for yy, xx in zip(ys, xs):
            self.grid.set_terrain(int(xx), int(yy), int(ttype))

    # ---- stepping -----------------------------------------------------------

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.grid.step(TICK_DT)
            self.world.update(self.grid, TICK_DT)
            self.tick += 1
            self._collect_messages()

    def _collect_messages(self) -> None:
        for u in self.world.units:
            while u.log:
                self.messages.append((self.grid.time, u.log.pop(0)))

    # ---- state / replay -----------------------------------------------------

    def state_hash(self) -> str:
        return self.grid.state_hash()

    def stats(self) -> dict:
        s = self.grid.stats()
        return {
            "time": self.grid.time, "tick": self.tick,
            "burning": s.burning, "smoldering": s.smoldering, "burned_cells": s.burned_cells,
            "area_burned_pct": round(100 * s.area_burned_frac, 2),
            "structures_total": s.structures_total, "structures_lost": s.structures_lost,
            "wind": (self.grid.wind_speed, self.grid.wind_bearing),
            "units": len(self.world.units),
        }

    def save_replay(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"scenario": self.scenario.to_dict(),
                       "commands": [c.__dict__ for c in self.log],
                       "final_tick": self.tick, "final_hash": self.state_hash()}, f, indent=1)

    @classmethod
    def replay(cls, path: str | Path) -> "Simulation":
        """Rebuild a run from its replay file and return the finished simulation."""
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        sim = cls(Scenario.from_dict(d["scenario"]))
        cmds = [Command(**c) for c in d["commands"]]
        i = 0
        while sim.tick < d["final_tick"]:
            while i < len(cmds) and cmds[i].tick == sim.tick:
                sim._apply(cmds[i]); i += 1
            sim.step()
        return sim

    def _apply(self, c: Command) -> None:
        a = c.args
        if c.kind == "ignite":
            self.grid.ignite(a["x"], a["y"], a.get("radius", 0))
        elif c.kind == "wind":
            self.grid.set_wind(a["speed"], a["bearing"])
        elif c.kind == "spawn":
            self.world.add(a["utype"], a["x"], a["y"], self.grid)
        elif c.kind == "order":
            u = self.world.by_id(a["uid"])
            if u is not None:
                u.give(Order(a["kind"], tuple(a["target"]) if a.get("target") else None,
                             [tuple(p) for p in (a.get("points") or [])]), queue=a.get("queue", False))
        elif c.kind == "paint":
            ys, xs = self.grid._disc(a["x"], a["y"], a.get("radius", 0))
            for yy, xx in zip(ys, xs):
                self.grid.set_terrain(int(xx), int(yy), int(a["ttype"]))
        self.log.append(c)
