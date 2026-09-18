"""Simulation facade.

Owns one FireGrid and one World, advances them with a fixed timestep, applies
scenario events, tracks budget and objectives, scores the run, and keeps a log of
every player command so a run can be replayed exactly:

    same scenario + same seed + same command log  ⇒  same state hash

Two persistence formats:
  * replay   (.json)      scenario + command log; small, deterministic, human-readable
  * savegame (.bbsave)    full state (zip of meta.json + grid.npz); resume instantly

The renderer and UI only ever talk to this class.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import UNITS
from .fire import FireGrid
from .scenario import Scenario
from .units import ORDER_KINDS, Order, Unit, World

TICK_DT = 1.0  # simulated seconds per tick
TICKS_PER_SECOND = 10  # at 1× speed (DECISIONS.md)

RUNNING, CONTAINED, FAILED, TIMEOUT = "running", "contained", "failed", "timeout"
REPLAY_FORMAT = "backburn-replay-1"
SAVE_FORMAT = "backburn-save-1"


@dataclass
class Command:
    """One player action, recorded for replay."""

    tick: int
    kind: str
    args: dict = field(default_factory=dict)


@dataclass
class Message:
    time: float
    text: str
    kind: str = "unit"  # unit | system | objective | event


class Simulation:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        terrain = scenario.build_terrain()
        self.grid = FireGrid(
            terrain, seed=scenario.seed, base_moisture=scenario.moisture, elevation=scenario.build_elevation()
        )
        self.grid.set_wind(scenario.wind_speed, scenario.wind_bearing)
        self.world = World(
            airbase=tuple(scenario.airbase), staging=scenario.staging, safe_zone=scenario.safe_zone
        )
        self.tick = 0
        self.log: list[Command] = []
        self.messages: list[Message] = []
        self.budget: float | None = scenario.budget
        self.spent = 0.0
        self.outcome = RUNNING
        self.outcome_reason = ""
        self.contained_at: float | None = None
        self._fire_started = False
        self._events_done: set[int] = set()
        self._structures_lost_seen = 0

        for ig in scenario.ignitions:
            self.grid.ignite(int(ig["x"]), int(ig["y"]), int(ig.get("radius", 0)))
        for u in scenario.units:
            self.world.add(u["type"], float(u["x"]), float(u["y"]), self.grid)
        self._fire_started = self.grid.stats().burning > 0
        if scenario.briefing:
            self._msg(scenario.briefing, "system")

    # ---- player commands (all logged) ---------------------------------------

    def _record(self, _kind: str, **args) -> None:
        self.log.append(Command(self.tick, _kind, args))

    def _msg(self, text: str, kind: str = "system") -> None:
        self.messages.append(Message(self.grid.time, text, kind))

    def cmd_ignite(self, x: int, y: int, radius: int = 0) -> int:
        self._record("ignite", x=int(x), y=int(y), radius=int(radius))
        n = self.grid.ignite(int(x), int(y), int(radius))
        self._fire_started |= n > 0
        return n

    def cmd_wind(self, speed: float, bearing: float) -> None:
        self._record("wind", speed=float(speed), bearing=float(bearing))
        self.grid.set_wind(speed, bearing)

    def available_units(self) -> list[str]:
        avail = self.scenario.available_units
        if avail is None:
            return [k for k in UNITS if not UNITS[k].get("is_civilian")]
        return list(avail)

    def can_afford(self, utype: str) -> bool:
        cost = float(UNITS[utype].get("cost", 0))
        return self.budget is None or self.budget - self.spent >= cost

    def cmd_spawn(self, utype: str, x: float | None = None, y: float | None = None) -> bool:
        """Purchase a unit. It arrives after its dispatch delay at the staging point
        (ground) or airbase (air) unless coordinates are given. Returns False when the
        unit is unavailable in this scenario or the budget can't cover it."""
        if utype not in UNITS or utype not in self.available_units():
            self._msg(f"{utype} is not available in this scenario", "system")
            return False
        if not self.can_afford(utype):
            self._msg(
                f"Not enough budget for {UNITS[utype].get('label', utype)} "
                f"({UNITS[utype].get('cost', 0):.0f})",
                "system",
            )
            return False
        self._record("spawn", utype=utype, x=x, y=y)
        self._spawn(utype, x, y)
        return True

    def _spawn(self, utype: str, x: float | None, y: float | None) -> None:
        self.spent += float(UNITS[utype].get("cost", 0))
        a = self.world.request(utype, self.grid.time, x, y)
        eta = a.at - self.grid.time
        self._msg(f"{UNITS[utype].get('label', utype)} requested, arriving in {eta:.0f}s", "system")

    def cmd_place(self, utype: str, x: float, y: float) -> Unit:
        """Editor: place a unit immediately, no cost, no delay (also used for civilians)."""
        self._record("place", utype=utype, x=float(x), y=float(y))
        return self.world.add(utype, x, y, self.grid)

    def cmd_order(
        self, uid: int, kind: str, target=None, points=None, unit_id: int | None = None, queue: bool = False
    ) -> bool:
        if kind not in ORDER_KINDS:
            raise ValueError(f"unknown order kind {kind!r}; valid: {sorted(ORDER_KINDS)}")
        u = self.world.by_id(uid)
        if u is None or u.is_civilian or not u.alive:
            return False
        self._record(
            "order",
            uid=int(uid),
            kind=kind,
            target=list(target) if target else None,
            points=[list(p) for p in (points or [])],
            unit_id=unit_id,
            queue=bool(queue),
        )
        u.give(
            Order(kind, tuple(target) if target else None, [tuple(p) for p in (points or [])], unit_id),
            queue=queue,
        )
        return True

    def cmd_paint(self, x: int, y: int, ttype: int, radius: int = 0) -> None:
        """Scenario editor: paint terrain."""
        self._record("paint", x=int(x), y=int(y), ttype=int(ttype), radius=int(radius))
        self._paint(int(x), int(y), int(ttype), int(radius))

    def _paint(self, x: int, y: int, ttype: int, radius: int) -> None:
        ys, xs = self.grid._disc(x, y, radius)
        for yy, xx in zip(ys, xs):
            self.grid.set_terrain(int(xx), int(yy), ttype)
        self.grid._labels = None  # buildings are relabelled lazily

    def cmd_extinguish(self, x: int, y: int, radius: int = 1) -> None:
        """Editor: erase fire (not a gameplay action)."""
        self._record("extinguish", x=int(x), y=int(y), radius=int(radius))
        self._extinguish(int(x), int(y), int(radius))

    def _extinguish(self, x: int, y: int, radius: int) -> None:
        ys, xs = self.grid._disc(x, y, radius)
        self.grid.state[ys, xs] = 0
        self.grid.smolder_timer[ys, xs] = 0.0

    # ---- stepping -----------------------------------------------------------

    def step(self, n: int = 1) -> int:
        """Advance up to n ticks. Stops early once the scenario has an outcome.
        Returns the number of ticks actually advanced."""
        done = 0
        for _ in range(n):
            if self.outcome != RUNNING:
                break
            self._apply_events()
            self.grid.step(TICK_DT)
            arrived = self.world.update(self.grid, TICK_DT)
            self.tick += 1
            done += 1
            self._collect_messages(arrived)
            self._check_outcome()
        return done

    def _apply_events(self) -> None:
        for i, ev in enumerate(self.scenario.events):
            if i in self._events_done or self.grid.time < float(ev["at"]):
                continue
            self._events_done.add(i)
            if "wind" in ev:
                w = ev["wind"]
                self.grid.set_wind(
                    float(w.get("speed", self.grid.wind_speed)),
                    float(w.get("bearing", self.grid.wind_bearing)),
                )
                self._msg(
                    f"Wind now {self.grid.wind_speed:.0f} m/s toward {self.grid.wind_bearing:.0f}°", "event"
                )
            if "ignite" in ev:
                ig = ev["ignite"]
                if self.grid.ignite(int(ig["x"]), int(ig["y"]), int(ig.get("radius", 0))):
                    self._fire_started = True
                    self._msg(f"New fire reported at ({ig['x']}, {ig['y']})", "event")
            if "spawn" in ev:
                r = ev["spawn"]
                self.world.request(r["type"], self.grid.time, r.get("x"), r.get("y"))
                self._msg(f"Reinforcement: {UNITS[r['type']].get('label', r['type'])} dispatched", "event")
            if "budget" in ev and self.budget is not None:
                self.budget += float(ev["budget"])
                self._msg(
                    f"Budget {'increased' if ev['budget'] >= 0 else 'cut'} by {abs(float(ev['budget'])):.0f}",
                    "event",
                )
            if "message" in ev:
                self._msg(str(ev["message"]), "event")

    def _collect_messages(self, arrived: list[Unit]) -> None:
        for u in self.world.units:
            while u.log:
                t, text = u.log.pop(0)
                self.messages.append(Message(float(t), text, "unit"))
        lost = self.grid.stats().structures_lost
        if lost > self._structures_lost_seen:
            n = lost - self._structures_lost_seen
            self._structures_lost_seen = lost
            self.messages.append(
                Message(
                    self.grid.time,
                    f"{n} building{'s' if n > 1 else ''} burning ({lost} lost total)",
                    "objective",
                )
            )

    def _check_outcome(self) -> None:
        obj = self.scenario.objectives
        s = self.grid.stats()
        civ = self.civilian_counts()
        if obj.get("max_structures_lost") is not None and s.structures_lost > int(obj["max_structures_lost"]):
            self._end(
                FAILED, f"Too many structures lost ({s.structures_lost} > {obj['max_structures_lost']})"
            )
            return
        if obj.get("max_civilians_lost") is not None and civ["lost"] > int(obj["max_civilians_lost"]):
            self._end(FAILED, f"Too many civilians lost ({civ['lost']})")
            return
        if obj.get("max_area_burned_pct") is not None and 100 * s.area_burned_frac > float(
            obj["max_area_burned_pct"]
        ):
            self._end(FAILED, f"Burned area exceeded the limit ({100 * s.area_burned_frac:.0f}%)")
            return
        if (
            obj.get("rescue_all_civilians")
            and not obj.get("win_on_contained", True)
            and civ["total"] > 0
            and civ["rescued"] == civ["total"]
        ):
            self.contained_at = self.grid.time
            self._end(CONTAINED, "All hikers safely evacuated")
            return
        pending_fire = any(
            i not in self._events_done and "ignite" in ev for i, ev in enumerate(self.scenario.events)
        )
        if (
            obj.get("win_on_contained", True)
            and not pending_fire
            and self._fire_started
            and self.tick > 1
            and self.grid.is_out()
        ):
            need_rescue = obj.get("rescue_all_civilians") and civ["total"] > 0
            if not need_rescue or civ["rescued"] + civ["lost"] == civ["total"]:
                self.contained_at = self.grid.time
                self._end(CONTAINED, f"Fire contained at {self.grid.time:.0f}s")
                return
        if self.grid.time >= self.scenario.duration:
            if obj.get("rescue_all_civilians") and civ["total"] and civ["rescued"] < civ["total"]:
                self._end(FAILED, "Time expired with civilians still unrescued")
            else:
                self._end(TIMEOUT, "Time expired")

    def _end(self, outcome: str, reason: str) -> None:
        self.outcome = outcome
        self.outcome_reason = reason
        self._msg(f"{outcome.upper()}: {reason}", "objective")

    # ---- scoring / stats --------------------------------------------------------

    def civilian_counts(self) -> dict[str, int]:
        cs = self.world.civilians()
        return {
            "total": len(cs),
            "rescued": sum(c.rescued for c in cs),
            "lost": sum((not c.alive) for c in cs),
            "aboard": sum((c.in_vehicle is not None) for c in cs),
        }

    def score(self) -> dict[str, float]:
        """Score breakdown using the scenario's weights (docs/SCORING.md)."""
        w = self.scenario.scoring
        s = self.grid.stats()
        civ = self.civilian_counts()
        fuel_cells = int((self.grid.fuel > 0.02).sum()) if self.grid.fuel.size else 0
        saved_cells = max(0, fuel_cells - s.burned_cells)
        remaining = 0.0 if self.budget is None else max(0.0, self.budget - self.spent)
        parts = {
            "structures_saved": (s.structures_total - s.structures_lost) * float(w["structure_saved"]),
            "civilians_rescued": civ["rescued"] * float(w["civilian_rescued"]),
            "civilians_lost": civ["lost"] * float(w["civilian_lost"]),
            "area_saved": saved_cells * float(w["acre_saved"]),
            "budget_remaining": remaining * float(w["budget_remaining"]),
            "contain_bonus": float(w["contain_bonus"]) if self.outcome == CONTAINED else 0.0,
            "time_bonus": (
                max(0.0, self.scenario.duration - self.contained_at) * float(w["time_bonus_per_second"])
                if self.contained_at is not None
                else 0.0
            ),
        }
        parts = {k: round(v, 1) for k, v in parts.items()}
        parts["total"] = round(sum(parts.values()), 1)
        return parts

    def stats(self) -> dict:
        s = self.grid.stats()
        civ = self.civilian_counts()
        return {
            "time": self.grid.time,
            "tick": self.tick,
            "outcome": self.outcome,
            "burning": s.burning,
            "smoldering": s.smoldering,
            "burned_cells": s.burned_cells,
            "area_burned_pct": round(100 * s.area_burned_frac, 2),
            "structures_total": s.structures_total,
            "structures_lost": s.structures_lost,
            "civilians": civ,
            "spot_fires": s.spot_fires,
            "embers_in_air": s.embers_in_air,
            "wind": (self.grid.wind_speed, self.grid.wind_bearing),
            "units": len([u for u in self.world.units if not u.is_civilian]),
            "pending": len(self.world.pending),
            "budget": self.budget,
            "spent": self.spent,
        }

    def state_hash(self) -> str:
        return self.grid.state_hash()

    # ---- replay -------------------------------------------------------------------

    def save_replay(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "format": REPLAY_FORMAT,
                    "scenario": self.scenario.to_dict(),
                    "commands": [c.__dict__ for c in self.log],
                    "final_tick": self.tick,
                    "final_hash": self.state_hash(),
                },
                f,
                indent=1,
            )

    @classmethod
    def replay(cls, path: str | Path, until_tick: int | None = None) -> "Simulation":
        """Rebuild a run from its replay file; returns the simulation at the final (or given) tick."""
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("format") != REPLAY_FORMAT:
            raise ValueError(f"not a Backburn replay (format={d.get('format')!r})")
        sim = cls(Scenario.from_dict(d["scenario"]))
        cmds = [Command(**c) for c in d["commands"]]
        target = d["final_tick"] if until_tick is None else min(until_tick, d["final_tick"])
        i = 0
        while sim.tick < target:
            while i < len(cmds) and cmds[i].tick == sim.tick:
                sim._apply(cmds[i])
                i += 1
            if sim.step() == 0:  # outcome reached early
                break
        # Commands can be issued while paused at the final saved tick.
        while i < len(cmds) and cmds[i].tick == sim.tick and sim.tick <= target:
            sim._apply(cmds[i])
            i += 1
        return sim

    def _apply(self, c: Command) -> None:
        a = c.args
        if c.kind == "ignite":
            if self.grid.ignite(a["x"], a["y"], a.get("radius", 0)):
                self._fire_started = True
        elif c.kind == "wind":
            self.grid.set_wind(a["speed"], a["bearing"])
        elif c.kind == "spawn":
            self._spawn(a["utype"], a.get("x"), a.get("y"))
        elif c.kind == "place":
            self.world.add(a["utype"], a["x"], a["y"], self.grid)
        elif c.kind == "order":
            u = self.world.by_id(a["uid"])
            if u is not None:
                u.give(
                    Order(
                        a["kind"],
                        tuple(a["target"]) if a.get("target") else None,
                        [tuple(p) for p in (a.get("points") or [])],
                        a.get("unit_id"),
                    ),
                    queue=a.get("queue", False),
                )
        elif c.kind == "paint":
            self._paint(a["x"], a["y"], a["ttype"], a.get("radius", 0))
        elif c.kind == "extinguish":
            self._extinguish(a["x"], a["y"], a.get("radius", 1))
        else:
            raise ValueError(f"unknown command kind in replay: {c.kind!r}")
        self.log.append(c)

    # ---- full-state savegame ---------------------------------------------------------

    def save_state(self, path: str | Path) -> None:
        """Write a resumable savegame: a zip holding meta.json and grid.npz (no pickling)."""
        meta = {
            "format": SAVE_FORMAT,
            "scenario": self.scenario.to_dict(),
            "grid": self.grid.meta(),
            "tick": self.tick,
            "spent": self.spent,
            "budget": self.budget,
            "outcome": self.outcome,
            "outcome_reason": self.outcome_reason,
            "contained_at": self.contained_at,
            "fire_started": self._fire_started,
            "events_done": sorted(self._events_done),
            "structures_lost_seen": self._structures_lost_seen,
            "log": [c.__dict__ for c in self.log],
            "messages": [m.__dict__ for m in self.messages[-200:]],
            "world": self.world.to_dict(),
        }
        buf = io.BytesIO()
        np.savez_compressed(buf, **self.grid.to_arrays())
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("meta.json", json.dumps(meta))
            z.writestr("grid.npz", buf.getvalue())

    @classmethod
    def load_state(cls, path: str | Path) -> "Simulation":
        with zipfile.ZipFile(path, "r") as z:
            meta = json.loads(z.read("meta.json"))
            arrays = dict(np.load(io.BytesIO(z.read("grid.npz")), allow_pickle=False))
        if meta.get("format") != SAVE_FORMAT:
            raise ValueError(f"not a Backburn savegame (format={meta.get('format')!r})")
        sim = cls.__new__(cls)
        sim.scenario = Scenario.from_dict(meta["scenario"])
        sim.grid = FireGrid.from_arrays(arrays, meta["grid"])
        sim.world = World.from_dict(meta["world"])
        sim.tick = int(meta["tick"])
        sim.spent = float(meta["spent"])
        sim.budget = meta["budget"]
        sim.outcome = meta["outcome"]
        sim.outcome_reason = meta["outcome_reason"]
        sim.contained_at = meta["contained_at"]
        sim._fire_started = bool(meta["fire_started"])
        sim._events_done = set(meta["events_done"])
        sim._structures_lost_seen = int(meta.get("structures_lost_seen", 0))
        sim.log = [Command(**c) for c in meta["log"]]
        sim.messages = [Message(**m) for m in meta["messages"]]
        return sim
