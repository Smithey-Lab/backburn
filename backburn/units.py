"""UnitSystem + OrderSystem.

Units are objects with float positions living on top of the fire grid. Behaviour
is data-driven from data/units.json: every unit has a movement class, a speed and
one primary `action` (HOSE, CUT, SPRAY, DROP, NONE). Everything else is a small
state machine:

    IDLE → MOVING → WORKING → (REFILLING | RELOADING | HOSE_BURNED) → ...

Orders are queued. The sim pops the next order when the current one completes.
Aircraft ignore terrain and fly straight lines; ground units path with Dijkstra
and refuse to enter burning cells.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .config import TERRAIN, UNITS, MoveClass, TerrainType
from .fire import BURNING, SMOLDER
from .pathfinding import build_cost, find_path

if TYPE_CHECKING:
    from .fire import FireGrid


# Order kinds
MOVE, SUPPRESS, HOSE, CUT, DROP, REFILL, PICKUP, DROPOFF, HOLD = (
    "MOVE", "SUPPRESS", "HOSE", "CUT", "DROP", "REFILL", "PICKUP", "DROPOFF", "HOLD")


@dataclass
class Order:
    kind: str
    target: tuple[float, float] | None = None
    points: list[tuple[float, float]] = field(default_factory=list)  # CUT polyline / DROP segment
    auto: bool = False  # created by the unit itself (auto-refill), not the player

    def __repr__(self) -> str:
        return f"Order({self.kind}, {self.target or self.points})"


@dataclass
class Unit:
    utype: str
    x: float
    y: float
    uid: int = 0
    state: str = "IDLE"
    orders: deque[Order] = field(default_factory=deque)
    current: Order | None = None
    path: list[tuple[int, int]] = field(default_factory=list)
    tank: float = 0.0
    hose: list[tuple[int, int]] = field(default_factory=list)   # cells from water source to unit
    hose_source: tuple[int, int] | None = None
    reconnect_timer: float = 0.0
    reload_timer: float = 0.0
    replan_cooldown: float = 0.0
    arrived: bool = False       # last path finished but target may be unreachable exactly
    cut_progress: float = 0.0
    cut_index: int = 0           # which segment endpoint of the CUT polyline we're heading to
    drop_phase: int = 0          # 0 → fly to p0, 1 → dropping p0→p1, 2 → done
    passengers: list[int] = field(default_factory=list)
    alive: bool = True
    log: list[str] = field(default_factory=list)
    _t: float = 0.0

    # --- convenience -------------------------------------------------------

    @property
    def spec(self) -> dict:
        return UNITS[self.utype]

    @property
    def move_class(self) -> MoveClass:
        return self.spec["movement_class"]

    @property
    def is_air(self) -> bool:
        return self.move_class == MoveClass.AIR

    @property
    def cell(self) -> tuple[int, int]:
        return int(round(self.x)), int(round(self.y))

    @property
    def capacity(self) -> float:
        return float(self.spec.get("capacity", 0))

    def __post_init__(self) -> None:
        self.tank = self.capacity

    def say(self, msg: str, t: float) -> None:
        self.log.append(f"[{t:7.1f}] {self.utype}#{self.uid}: {msg}")

    # --- orders --------------------------------------------------------------

    def give(self, order: Order, queue: bool = False) -> None:
        if not queue:
            self.orders.clear()
            self.current = None
            self.path = []
            self.cut_index = 0
            self.cut_progress = 0.0
            self.drop_phase = 0
            self.arrived = False
            self.state = "IDLE"
        self.orders.append(order)

    def _next_order(self, grid: FireGrid) -> None:
        self.current = self.orders.popleft() if self.orders else None
        self.path = []
        self.cut_index = 0
        self.cut_progress = 0.0
        self.drop_phase = 0
        self.arrived = False
        if self.current is None:
            self.state = "IDLE"
            return
        self.state = "MOVING"

    # --- movement ------------------------------------------------------------

    def _plan_to(self, grid: FireGrid, target: tuple[float, float]) -> bool:
        tx, ty = int(round(target[0])), int(round(target[1]))
        if self.is_air:
            self.path = [(tx, ty)]
            return True
        if self.replan_cooldown > 0:
            return False
        self.replan_cooldown = 2.0
        cost = build_cost(grid.terrain, grid.state, TERRAIN.cost_for(self.move_class))
        p = find_path(cost, self.cell, (tx, ty))
        if p is None:
            return False
        self.path = p
        self.arrived = (len(p) == 0)
        return True

    def _advance(self, grid: FireGrid, dt: float) -> bool:
        """Move along self.path. Returns True when the path is exhausted."""
        speed = float(self.spec["speed"])
        budget = speed * dt
        while self.path and budget > 1e-6:
            nx, ny = self.path[0]
            if not self.is_air:
                # Refuse to step into fire; force a replan.
                if grid.state[ny, nx] in (BURNING, SMOLDER):
                    self.path = []
                    return False
                c = float(TERRAIN.cost_for(self.move_class)[grid.terrain[ny, nx]])
                if not math.isfinite(c):
                    self.path = []
                    return False
            else:
                c = 1.0
            dx, dy = nx - self.x, ny - self.y
            dist = math.hypot(dx, dy)
            step = budget / c
            if step >= dist:
                self.x, self.y = float(nx), float(ny)
                budget -= dist * c
                self.path.pop(0)
            else:
                self.x += dx / dist * step
                self.y += dy / dist * step
                budget = 0.0
        if not self.path:
            self.arrived = True
        return not self.path

    def _approach(self, grid: FireGrid, target: tuple[float, float], tol: float, dt: float) -> bool | None:
        """Walk toward target. True = in position, False = still moving, None = unreachable."""
        d = math.hypot(target[0] - self.x, target[1] - self.y)
        if d <= tol:
            return True
        if self.arrived and not self.path:
            if d > tol + 3.0:
                self.say(f"can't get closer than {d:.0f} cells", self._t)
            return True  # closest reachable cell; treat as in position
        if not self.path:
            if not self._plan_to(grid, target):
                if self.replan_cooldown > 0 and not self.arrived:
                    self.state = "MOVING"
                    return False  # waiting on cooldown after a blocked step
                return None
        self._advance(grid, dt)
        self.state = "MOVING"
        return False

    # --- per tick ------------------------------------------------------------

    def update(self, grid: FireGrid, dt: float, world: "World") -> None:
        if not self.alive:
            return
        self._t = grid.time
        self.replan_cooldown = max(0.0, self.replan_cooldown - dt)
        # Ground units caught in a burning cell are lost.
        cx, cy = self.cell
        if not self.is_air and 0 <= cx < grid.w and 0 <= cy < grid.h:
            if grid.state[cy, cx] == BURNING and not self.spec.get("is_civilian", False):
                # Crews on foot flee: they survive but drop everything.
                self.orders.clear(); self.current = None; self.path = []
                self.state = "IDLE"
                self.hose = []
                self.say("overrun by fire, retreating", grid.time)
                self._flee(grid)
                return

        if self.current is None:
            if self.orders:
                self._next_order(grid)
            else:
                self.state = "IDLE"
                return
        o = self.current
        kind = o.kind

        if kind == MOVE:
            self._do_move(grid, o, dt)
        elif kind == SUPPRESS:
            self._do_suppress(grid, o, dt, world)
        elif kind == HOSE:
            self._do_hose(grid, o, dt)
        elif kind == CUT:
            self._do_cut(grid, o, dt)
        elif kind == DROP:
            self._do_drop(grid, o, dt, world)
        elif kind == REFILL:
            self._do_refill(grid, o, dt, world)
        elif kind == HOLD:
            self.state = "WORKING"
        else:
            self._finish(grid)

    def _finish(self, grid: FireGrid) -> None:
        self.current = None
        self.state = "IDLE"

    # --- behaviours ----------------------------------------------------------

    def _do_move(self, grid: FireGrid, o: Order, dt: float) -> None:
        r = self._approach(grid, o.target, 0.6, dt)
        if r is None:
            self.say("no path to target", grid.time)
        if r is not False:
            self._finish(grid)

    def _do_suppress(self, grid: FireGrid, o: Order, dt: float, world: "World") -> None:
        spec = self.spec
        if self.capacity > 0 and self.tank <= 0.0:
            self._auto_refill(grid, o, world); return
        r = float(spec["spray_radius"])
        # Move within range of the target first.
        a = self._approach(grid, o.target, max(0.9, r * 0.75), dt)
        if a is None:
            self.say("can't reach suppress target", grid.time)
            self._finish(grid); return
        if a is False:
            return
        self.state = "WORKING"
        rate = float(spec["spray_water"])
        ys, xs = grid._disc(self.x, self.y, r)
        if len(ys) == 0:
            return
        hot = (grid.state[ys, xs] == BURNING) | (grid.state[ys, xs] == SMOLDER) | (grid.heat[ys, xs] > 0)
        grid.water[ys, xs] = np.minimum(grid.water[ys, xs] + rate * dt, 1.5)
        if self.capacity > 0:
            self.tank -= rate * dt * max(1, int(hot.sum())) * 2.0
        # Fire boats and hose teams never finish on their own; sprayers stop once nothing nearby is hot.
        if spec["action"] == "SPRAY" and not self.spec.get("is_civilian") and not hot.any():
            # Look a little wider before giving up.
            ys2, xs2 = grid._disc(self.x, self.y, r + 3)
            if not ((grid.state[ys2, xs2] == BURNING) | (grid.state[ys2, xs2] == SMOLDER)).any():
                self.say("area cold, holding", grid.time)
                self.current = Order(HOLD, o.target)
                self.state = "WORKING"

    def _auto_refill(self, grid: FireGrid, resume: Order, world: "World") -> None:
        src = world.nearest_water(grid, self.cell, self.move_class, reach=float(self.spec.get("refill_radius", 2.5)))
        if src is None:
            self.say("tank empty, no water source reachable", grid.time)
            self._finish(grid); return
        self.say("tank empty, going to refill", grid.time)
        self.current = Order(REFILL, target=src, auto=True)
        self.orders.appendleft(Order(resume.kind, resume.target, resume.points))
        self.path = []

    def _do_refill(self, grid: FireGrid, o: Order, dt: float, world: "World") -> None:
        reach = float(self.spec.get("refill_radius", 2.5))
        if self.spec.get("reload_at_base"):
            reach = 1.5
        a = self._approach(grid, o.target, reach, dt)
        if a is None:
            self.say("can't reach water", grid.time)
            self._finish(grid); return
        if a is False:
            return
        if self.spec.get("reload_at_base"):
            self.state = "RELOADING"
            self.reload_timer -= dt
            if self.reload_timer <= 0:
                self.tank = self.capacity
                self.say("reloaded at base", grid.time)
                self._finish(grid)
            return
        self.state = "REFILLING"
        self.tank = min(self.capacity, self.tank + float(self.spec["refill_rate"]) * dt)
        if self.tank >= self.capacity - 1e-6:
            self.say("refilled", grid.time)
            self._finish(grid)

    def _do_hose(self, grid: FireGrid, o: Order, dt: float) -> None:
        spec = self.spec
        a = self._approach(grid, o.target, 0.9, dt)
        if a is None:
            self.say("can't reach hose position", grid.time)
            self._finish(grid); return
        if a is False:
            self.hose = []
            return
        # In position. Need a hose line to water.
        if self.state == "HOSE_BURNED":
            self.reconnect_timer -= dt
            if self.reconnect_timer > 0:
                return
        if not self.hose:
            if not self._lay_hose(grid):
                if self.state != "HOSE_BURNED":
                    self.say("no water source within hose reach", grid.time)
                    self.state = "HOSE_BURNED"
                    self.reconnect_timer = 10.0
                return
            self.say(f"hose connected ({len(self.hose)} cells)", grid.time)
        # Burn check.
        hy = np.array([c[1] for c in self.hose]); hx = np.array([c[0] for c in self.hose])
        if (grid.state[hy, hx] == BURNING).any():
            self.hose = []
            self.state = "HOSE_BURNED"
            self.reconnect_timer = 8.0
            self.say("HOSE BURNED — reconnecting", grid.time)
            return
        self.state = "WORKING"
        r = float(spec["spray_radius"]); rate = float(spec["spray_water"])
        ys, xs = grid._disc(self.x, self.y, r)
        grid.water[ys, xs] = np.minimum(grid.water[ys, xs] + rate * dt, 1.5)

    def _lay_hose(self, grid: FireGrid) -> bool:
        maxlen = int(self.spec["hose_max_length"])
        cost = build_cost(grid.terrain, grid.state, TERRAIN.cost_for(MoveClass.FOOT))
        # Hose can cross water edges; treat water as passable for the hose path only at the source.
        ws = np.argwhere(TERRAIN.is_water_source[grid.terrain])
        if len(ws) == 0:
            return False
        cx, cy = self.cell
        d2 = (ws[:, 1] - cx) ** 2 + (ws[:, 0] - cy) ** 2
        for idx in np.argsort(d2)[:12]:
            wy, wx = int(ws[idx][0]), int(ws[idx][1])
            if math.hypot(wx - cx, wy - cy) > maxlen:
                break
            # Path from unit to a land cell adjacent to the water cell.
            p = find_path(cost, (cx, cy), (wx, wy))
            if p is None or len(p) > maxlen:
                continue
            self.hose = [(cx, cy)] + p
            self.hose_source = (wx, wy)
            return True
        return False

    def _do_cut(self, grid: FireGrid, o: Order, dt: float) -> None:
        pts = o.points
        if not pts:
            self._finish(grid); return
        if self.cut_index >= len(pts):
            self.say("line complete", grid.time)
            self._finish(grid); return
        tx, ty = pts[self.cut_index]
        d = math.hypot(tx - self.x, ty - self.y)
        if d < 0.5:
            self.cut_index += 1
            self.path = []
            self.arrived = False
            return
        if self.cut_index == 0:
            # Travel to the start of the line using normal pathing.
            a = self._approach(grid, (tx, ty), 0.5, dt)
            if a is None:
                self.say("can't reach line start", grid.time)
                self._finish(grid); return
            if a is True:
                self.cut_index = 1
                self.path = []
                self.arrived = False
            return
        # Cutting: convert the cell we stand on, then step one cell along the segment.
        self.state = "WORKING"
        self.cut_progress += float(self.spec["cut_rate"]) * dt
        if self.cut_progress >= 1.0:
            self.cut_progress = 0.0
            cx, cy = self.cell
            self._cut_cell(grid, cx, cy)
            step = min(1.0, d)
            nx, ny = self.x + (tx - self.x) / d * step, self.y + (ty - self.y) / d * step
            ncx, ncy = int(round(nx)), int(round(ny))
            if grid.state[ncy, ncx] == BURNING:
                self.say("line blocked by fire", grid.time)
                self._finish(grid); return
            self.x, self.y = nx, ny

    def _cut_cell(self, grid: FireGrid, cx: int, cy: int) -> None:
        width = int(self.spec["cut_width"])
        dense_ok = bool(self.spec.get("can_cut_dense", False))
        r = 0 if width <= 1 else width // 2
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if not (0 <= xx < grid.w and 0 <= yy < grid.h):
                    continue
                t = int(grid.terrain[yy, xx])
                if t in (int(TerrainType.WATER), int(TerrainType.ROAD), int(TerrainType.STRUCTURE),
                         int(TerrainType.GRAVEL), int(TerrainType.SAND), int(TerrainType.FIREBREAK)):
                    continue
                if t == int(TerrainType.DENSE_FOREST) and not dense_ok:
                    continue
                grid.set_terrain(xx, yy, int(TerrainType.FIREBREAK))

    def _do_drop(self, grid: FireGrid, o: Order, dt: float, world: "World") -> None:
        spec = self.spec
        if len(o.points) < 2:
            self._finish(grid); return
        p0, p1 = o.points[0], o.points[1]
        if self.tank <= 0.0:
            self._auto_reload(grid, o, world); return
        if self.drop_phase == 0:
            if not self.path:
                self.path = [(int(round(p0[0])), int(round(p0[1])))]
            if self._advance(grid, dt):
                self.drop_phase = 1
                self.path = [(int(round(p1[0])), int(round(p1[1])))]
                self.say("beginning drop", grid.time)
            self.state = "MOVING"
            return
        if self.drop_phase == 1:
            self.state = "WORKING"
            ox, oy = self.x, self.y
            done = self._advance(grid, dt)
            width = float(spec["drop_width"])
            agent = spec.get("drop_agent", "water")
            strength = float(spec.get("drop_strength", 0.7))
            seg = math.hypot(self.x - ox, self.y - oy)
            total = max(1.0, math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
            amount = strength * (seg / total) * 6.0
            grid.apply_line(ox, oy, self.x, self.y, width, agent, amount)
            self.tank -= self.capacity * (seg / total)
            if done or self.tank <= 0:
                self.drop_phase = 2
                self.say("drop complete", grid.time)
                self.tank = max(0.0, self.tank)
                self._finish(grid)
                if self.tank <= 0.0 or self.spec.get("reload_at_base"):
                    self._auto_reload(grid, None, world)

    def _auto_reload(self, grid: FireGrid, resume: Order | None, world: "World") -> None:
        if self.spec.get("reload_at_base"):
            base = world.airbase
            self.say("returning to base to reload", grid.time)
            self.current = Order(REFILL, target=base, auto=True)
            self.reload_timer = float(self.spec["reload_seconds"])
        else:
            src = world.nearest_water(grid, self.cell, MoveClass.AIR, reach=2.5)
            if src is None:
                self.say("no water to refill from", grid.time)
                self._finish(grid); return
            self.say("going to refill", grid.time)
            self.current = Order(REFILL, target=src, auto=True)
        if resume is not None:
            self.orders.appendleft(Order(resume.kind, resume.target, list(resume.points)))
        self.path = []

    def _flee(self, grid: FireGrid) -> None:
        # Walk to nearest non-burning passable cell.
        cost = build_cost(grid.terrain, grid.state, TERRAIN.cost_for(self.move_class))
        cx, cy = self.cell
        best = None
        for r in range(1, 6):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < grid.w and 0 <= y < grid.h and math.isfinite(cost[y, x]):
                        best = (x, y); break
                if best: break
            if best: break
        if best:
            self.x, self.y = float(best[0]), float(best[1])


class World:
    """Container for units plus shared lookups (water sources, airbase)."""

    def __init__(self, airbase: tuple[float, float] = (0.0, 0.0)):
        self.units: list[Unit] = []
        self.airbase = airbase
        self._next_uid = 1  # per-world counter so replays assign identical ids

    def add(self, utype: str, x: float, y: float, grid: "FireGrid | None" = None) -> Unit:
        if utype not in UNITS:
            raise KeyError(f"unknown unit type {utype}")
        if grid is not None:
            x, y = self.snap_passable(grid, UNITS[utype]["movement_class"], x, y)
        u = Unit(utype, float(x), float(y), uid=self._next_uid)
        self._next_uid += 1
        self.units.append(u)
        return u

    @staticmethod
    def snap_passable(grid: FireGrid, mc: MoveClass, x: float, y: float, max_r: int = 12) -> tuple[float, float]:
        """Nearest cell this movement class can stand on (scenario placement is forgiving)."""
        cost = TERRAIN.cost_for(mc)
        cx, cy = int(round(x)), int(round(y))
        cx = min(max(cx, 0), grid.w - 1); cy = min(max(cy, 0), grid.h - 1)
        if math.isfinite(cost[grid.terrain[cy, cx]]):
            return float(cx), float(cy)
        best, bd = None, 1e9
        for r in range(1, max_r + 1):
            for yy in range(max(0, cy - r), min(grid.h, cy + r + 1)):
                for xx in range(max(0, cx - r), min(grid.w, cx + r + 1)):
                    if math.isfinite(cost[grid.terrain[yy, xx]]):
                        d = math.hypot(xx - cx, yy - cy)
                        if d < bd:
                            bd, best = d, (xx, yy)
            if best is not None:
                return float(best[0]), float(best[1])
        return float(cx), float(cy)

    def by_id(self, uid: int) -> Unit | None:
        for u in self.units:
            if u.uid == uid:
                return u
        return None

    def nearest_water(self, grid: FireGrid, frm: tuple[int, int], mc: MoveClass,
                      reach: float = 2.5) -> tuple[int, int] | None:
        """Nearest cell from which this unit can touch a water source."""
        ws = np.argwhere(TERRAIN.is_water_source[grid.terrain])
        if len(ws) == 0:
            return None
        fx, fy = frm
        d2 = (ws[:, 1] - fx) ** 2 + (ws[:, 0] - fy) ** 2
        order = np.argsort(d2)
        if mc == MoveClass.AIR:
            wy, wx = ws[order[0]]
            return int(wx), int(wy)
        cost = build_cost(grid.terrain, grid.state, TERRAIN.cost_for(mc))
        for idx in order[:40]:
            wy, wx = int(ws[idx][0]), int(ws[idx][1])
            # Find a passable cell within reach of this water cell.
            r = int(math.ceil(reach))
            best = None; bd = 1e9
            for yy in range(max(0, wy - r), min(grid.h, wy + r + 1)):
                for xx in range(max(0, wx - r), min(grid.w, wx + r + 1)):
                    if math.isfinite(cost[yy, xx]) and math.hypot(xx - wx, yy - wy) <= reach:
                        dd = math.hypot(xx - fx, yy - fy)
                        if dd < bd:
                            bd, best = dd, (xx, yy)
            if best is None:
                continue
            if find_path(cost, (fx, fy), best) is not None:
                return best
        return None

    def update(self, grid: FireGrid, dt: float) -> None:
        for u in self.units:
            u.update(grid, dt, self)
