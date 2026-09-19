"""UnitSystem + OrderSystem.

Units are objects with float positions living on top of the fire grid. Behaviour
is data-driven from data/units.json: every unit has a movement class, a speed and
one primary `action` (HOSE, CUT, SPRAY, DROP, NONE). Everything else is a small
state machine:

    IDLE → MOVING → WORKING → (REFILLING | RELOADING | HOSE_BURNED) → ...
    civilians: IDLE → FLEEING → (SAFE | LOST | ABOARD)

Orders are queued per unit. The sim pops the next order when the current one
completes. Aircraft ignore terrain and fly straight lines; ground and water units
path with A* over the move-cost grid and refuse to enter burning cells.

Transport: any unit with `passenger_slots` can PICKUP a foot unit (crew or
civilian) and DROPOFF it elsewhere. A civilian dropped inside the scenario's safe
zone counts as rescued.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .config import TERRAIN, UNITS, MoveClass, TerrainType
from .drops import capacity_length, clip_path, payload_per_cell
from .fire import BURNING, SMOLDER
from .pathfinding import build_cost, find_path

if TYPE_CHECKING:
    from .fire import FireGrid

# Order kinds
MOVE, SUPPRESS, HOSE, CUT, DROP, REFILL, PICKUP, DROPOFF, HOLD = (
    "MOVE",
    "SUPPRESS",
    "HOSE",
    "CUT",
    "DROP",
    "REFILL",
    "PICKUP",
    "DROPOFF",
    "HOLD",
)
ORDER_KINDS = {MOVE, SUPPRESS, HOSE, CUT, DROP, REFILL, PICKUP, DROPOFF, HOLD}

# Civilian / crew states beyond the movement ones
FLEEING, SAFE, LOST, ABOARD = "FLEEING", "SAFE", "LOST", "ABOARD"

_NO_CUT = {
    int(TerrainType.WATER),
    int(TerrainType.ROAD),
    int(TerrainType.STRUCTURE),
    int(TerrainType.GRAVEL),
    int(TerrainType.SAND),
    int(TerrainType.FIREBREAK),
}


@dataclass
class Order:
    kind: str
    target: tuple[float, float] | None = None
    points: list[tuple[float, float]] = field(default_factory=list)  # CUT polyline / DROP segment
    unit_id: int | None = None  # PICKUP target
    auto: bool = False  # created by the unit itself (auto-refill), not the player

    def __repr__(self) -> str:
        return f"Order({self.kind}, {self.unit_id if self.kind == PICKUP else (self.target or self.points)})"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "target": list(self.target) if self.target else None,
            "points": [list(p) for p in self.points],
            "unit_id": self.unit_id,
            "auto": self.auto,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(
            d["kind"],
            tuple(d["target"]) if d.get("target") else None,
            [tuple(p) for p in d.get("points", [])],
            d.get("unit_id"),
            bool(d.get("auto", False)),
        )


@dataclass
class Unit:
    utype: str
    x: float
    y: float
    uid: int = 0
    state: str = "IDLE"
    orders: deque = field(default_factory=deque)
    current: Order | None = None
    path: list = field(default_factory=list)
    tank: float = 0.0
    hose: list = field(default_factory=list)  # cells from unit to water source
    hose_source: tuple[int, int] | None = None
    reconnect_timer: float = 0.0
    reload_timer: float = 0.0
    replan_cooldown: float = 0.0
    arrived: bool = False
    cut_progress: float = 0.0
    cut_index: int = 0
    drop_phase: int = 0
    passengers: list = field(default_factory=list)  # uids aboard
    in_vehicle: int | None = None  # uid of carrier
    rescued: bool = False
    alive: bool = True
    flee_timer: float = 0.0
    log: list = field(default_factory=list)
    _t: float = 0.0

    # --- convenience -------------------------------------------------------

    @property
    def spec(self) -> dict:
        return UNITS[self.utype]

    @property
    def label(self) -> str:
        return f"{self.spec.get('label', self.utype)} #{self.uid}"

    @property
    def move_class(self) -> MoveClass:
        return self.spec["movement_class"]

    @property
    def is_air(self) -> bool:
        return self.move_class == MoveClass.AIR

    @property
    def is_plane(self) -> bool:
        return self.is_air and bool(self.spec.get("reload_at_base"))

    @property
    def is_civilian(self) -> bool:
        return bool(self.spec.get("is_civilian", False))

    @property
    def is_foot(self) -> bool:
        return self.move_class == MoveClass.FOOT

    @property
    def cell(self) -> tuple[int, int]:
        return int(round(self.x)), int(round(self.y))

    @property
    def capacity(self) -> float:
        return float(self.spec.get("capacity", 0))

    @property
    def passenger_slots(self) -> int:
        return int(self.spec.get("passenger_slots", 0))

    @property
    def busy(self) -> bool:
        return self.current is not None or bool(self.orders)

    def __post_init__(self) -> None:
        if self.tank == 0.0:
            self.tank = self.capacity

    def say(self, msg: str, t: float | None = None) -> None:
        self.log.append((self._t if t is None else t, f"{self.label}: {msg}"))

    # --- orders --------------------------------------------------------------

    def give(self, order: Order, queue: bool = False) -> None:
        if order.kind not in ORDER_KINDS:
            raise ValueError(f"unknown order kind {order.kind!r}")
        if self.is_plane and self.current and self.current.kind == REFILL:
            if not queue:
                self.orders.clear()
            if order.kind != HOLD:
                self.orders.append(order)
            return
        if not queue:
            self.orders.clear()
            self.current = None
            self._reset_order_state()
            if self.state not in (LOST, ABOARD):
                self.state = "IDLE"
        self.orders.append(order)

    def _reset_order_state(self) -> None:
        self.path = []
        self.cut_index = 0
        self.cut_progress = 0.0
        self.drop_phase = 0
        self.arrived = False

    def _next_order(self) -> None:
        self.current = self.orders.popleft() if self.orders else None
        self._reset_order_state()
        self.state = "IDLE" if self.current is None else "MOVING"

    def _finish(self) -> None:
        self.current = None
        self._reset_order_state()
        self.state = "IDLE"

    # --- movement ------------------------------------------------------------

    def _plan_to(self, grid: FireGrid, target: tuple[float, float]) -> bool:
        tx, ty = int(round(target[0])), int(round(target[1]))
        tx = min(max(tx, 0), grid.w - 1)
        ty = min(max(ty, 0), grid.h - 1)
        if self.is_air:
            self.path = [(tx, ty)]
            self.arrived = False
            return True
        if self.replan_cooldown > 0:
            return False
        self.replan_cooldown = 2.0
        cost = build_cost(grid.terrain, grid.state, TERRAIN.cost_for(self.move_class))
        p = find_path(cost, self.cell, (tx, ty))
        if p is None:
            return False
        self.path = p
        self.arrived = len(p) == 0
        return True

    def _advance(self, grid: FireGrid, dt: float) -> bool:
        """Move along self.path. Returns True when the path is exhausted."""
        speed = float(self.spec["speed"])
        budget = speed * dt
        while self.path and budget > 1e-6:
            nx, ny = self.path[0]
            if not self.is_air:
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
                self.say(f"can't get closer than {d:.0f} cells")
            return True
        if not self.path:
            if not self._plan_to(grid, target):
                if self.replan_cooldown > 0 and not self.arrived:
                    self.state = "MOVING"
                    return False
                return None
        self._advance(grid, dt)
        self.state = "MOVING"
        return False

    # --- per tick ------------------------------------------------------------

    def update(self, grid: FireGrid, dt: float, world: "World") -> None:
        if not self.alive or self.in_vehicle is not None:
            return
        self._t = grid.time
        self.replan_cooldown = max(0.0, self.replan_cooldown - dt)

        if self.is_plane:
            for _ in range(20):
                self._update_plane(grid, dt / 20)
            return

        if self.is_civilian:
            self._civilian(grid, dt, world)
            return

        # Ground units standing in a burning cell are overrun.
        cx, cy = self.cell
        if not self.is_air and grid.state[cy, cx] == BURNING:
            self.orders.clear()
            self.current = None
            self._reset_order_state()
            self.hose = []
            self.say("overrun by fire, retreating")
            self._flee_to_safety(grid)
            self.state = "IDLE"
            return

        if self.current is None:
            if self.orders:
                self._next_order()
            else:
                self.state = "IDLE"
                return
        o = self.current
        handler = {
            MOVE: self._do_move,
            SUPPRESS: self._do_suppress,
            HOSE: self._do_hose,
            CUT: self._do_cut,
            DROP: self._do_drop,
            REFILL: self._do_refill,
            PICKUP: self._do_pickup,
            DROPOFF: self._do_dropoff,
        }.get(o.kind)
        if handler is None:
            self.state = "WORKING"  # HOLD
            return
        handler(grid, o, dt, world)
        # Passengers ride along.
        for pid in self.passengers:
            p = world.by_id(pid)
            if p is not None:
                p.x, p.y = self.x, self.y

    # --- behaviours ----------------------------------------------------------

    def _do_move(self, grid, o, dt, world) -> None:
        r = self._approach(grid, o.target, 0.6, dt)
        if r is None:
            self.say("no path to target")
        if r is not False:
            self._finish()

    def _do_suppress(self, grid, o, dt, world) -> None:
        spec = self.spec
        if self.capacity > 0 and self.tank <= 0.0:
            self._auto_refill(grid, o, world)
            return
        r = float(spec["spray_radius"])
        a = self._approach(grid, o.target, max(0.9, r * 0.75), dt)
        if a is None:
            self.say("can't reach suppress target")
            self._finish()
            return
        if a is False:
            return
        self.state = "WORKING"
        rate = float(spec["spray_water"])
        ys, xs = grid._disc(self.x, self.y, r)
        if len(ys) == 0:
            return
        hot = (grid.state[ys, xs] == BURNING) | (grid.state[ys, xs] == SMOLDER) | (grid.heat[ys, xs] > 0)
        grid.water[ys, xs] = np.minimum(grid.water[ys, xs] + rate * dt, 1.5)
        if self.capacity > 0 and self.capacity < 1e6:
            self.tank -= rate * dt * max(1, int(hot.sum())) * 2.0
        if spec["action"] == "SPRAY" and not hot.any():
            ys2, xs2 = grid._disc(self.x, self.y, r + 3)
            if not ((grid.state[ys2, xs2] == BURNING) | (grid.state[ys2, xs2] == SMOLDER)).any():
                self.say("area cold, holding")
                self.current = Order(HOLD, o.target)
                self.state = "WORKING"

    def _auto_refill(self, grid, resume: Order, world) -> None:
        src = world.nearest_water(
            grid, self.cell, self.move_class, reach=float(self.spec.get("refill_radius", 2.5))
        )
        if src is None:
            self.say("tank empty, no water source reachable")
            self._finish()
            return
        self.say("tank empty, going to refill")
        self.current = Order(REFILL, target=src, auto=True)
        self.orders.appendleft(Order(resume.kind, resume.target, list(resume.points)))
        self._reset_order_state()

    def _do_refill(self, grid, o, dt, world) -> None:
        reach = float(self.spec.get("refill_radius", 2.5))
        at_base = bool(self.spec.get("reload_at_base"))
        if at_base:
            reach = 1.5
        a = self._approach(grid, o.target, reach, dt)
        if a is None:
            self.say("can't reach water")
            self._finish()
            return
        if a is False:
            return
        if at_base:
            self.state = "RELOADING"
            self.reload_timer -= dt
            if self.reload_timer <= 0:
                self.tank = self.capacity
                self.say("reloaded at base")
                self._finish()
            return
        self.state = "REFILLING"
        self.tank = min(self.capacity, self.tank + float(self.spec["refill_rate"]) * dt)
        if self.tank >= self.capacity - 1e-6:
            self.say("refilled")
            self._finish()

    def _do_hose(self, grid, o, dt, world) -> None:
        spec = self.spec
        a = self._approach(grid, o.target, 0.9, dt)
        if a is None:
            self.say("can't reach hose position")
            self._finish()
            return
        if a is False:
            self.hose = []
            return
        if self.state == "HOSE_BURNED":
            self.reconnect_timer -= dt
            if self.reconnect_timer > 0:
                return
        if not self.hose:
            if not self._lay_hose(grid, world):
                if self.state != "HOSE_BURNED":
                    self.say("no water source within hose reach")
                    self.state = "HOSE_BURNED"
                    self.reconnect_timer = 10.0
                return
            self.say(f"hose connected ({len(self.hose)} cells)")
        hy = np.fromiter((c[1] for c in self.hose), int)
        hx = np.fromiter((c[0] for c in self.hose), int)
        if (grid.state[hy, hx] == BURNING).any():
            self.hose = []
            self.state = "HOSE_BURNED"
            self.reconnect_timer = 8.0
            self.say("HOSE BURNED — reconnecting")
            return
        self.state = "WORKING"
        r = float(spec["spray_radius"])
        rate = float(spec["spray_water"])
        ys, xs = grid._disc(self.x, self.y, r)
        grid.water[ys, xs] = np.minimum(grid.water[ys, xs] + rate * dt, 1.5)

    def _lay_hose(self, grid, world) -> bool:
        """Hose runs from the unit to the nearest water source cell (lake edge or a parked
        engine's tank) within hose_max_length path cells."""
        maxlen = int(self.spec["hose_max_length"])
        cost = build_cost(grid.terrain, grid.state, TERRAIN.cost_for(MoveClass.FOOT))
        cx, cy = self.cell
        candidates: list[tuple[float, int, int]] = []
        ws = np.argwhere(TERRAIN.is_water_source[grid.terrain])
        if len(ws):
            d2 = (ws[:, 1] - cx) ** 2 + (ws[:, 0] - cy) ** 2
            for idx in np.argsort(d2)[:12]:
                candidates.append((float(d2[idx]) ** 0.5, int(ws[idx][1]), int(ws[idx][0])))
        # Engines with water act as a hydrant.
        for e in world.units:
            if e.alive and e.utype == "ENGINE" and e.tank > 10 and e.uid != self.uid:
                candidates.append((math.hypot(e.x - cx, e.y - cy), *e.cell))
        candidates.sort()
        for d, wx, wy in candidates:
            if d > maxlen:
                break
            p = find_path(cost, (cx, cy), (wx, wy))
            if p is None or len(p) > maxlen:
                continue
            self.hose = [(cx, cy)] + p
            self.hose_source = (wx, wy)
            return True
        return False

    def _do_cut(self, grid, o, dt, world) -> None:
        pts = o.points
        if not pts:
            self._finish()
            return
        if self.cut_index >= len(pts):
            self.say("line complete")
            self._finish()
            return
        tx, ty = pts[self.cut_index]
        d = math.hypot(tx - self.x, ty - self.y)
        if d < 0.5:
            self.cut_index += 1
            self.path = []
            self.arrived = False
            return
        if self.cut_index == 0:
            a = self._approach(grid, (tx, ty), 0.5, dt)
            if a is None:
                self.say("can't reach line start")
                self._finish()
                return
            if a is True:
                self.cut_index = 1
                self.path = []
                self.arrived = False
            return
        self.state = "WORKING"
        # Hotshots also knock down fire beside the line while they cut.
        sr = float(self.spec.get("spray_radius", 0))
        if sr > 0:
            ys, xs = grid._disc(self.x, self.y, sr)
            grid.water[ys, xs] = np.minimum(grid.water[ys, xs] + float(self.spec["spray_water"]) * dt, 1.5)
        self.cut_progress += float(self.spec["cut_rate"]) * dt
        if self.cut_progress >= 1.0:
            self.cut_progress = 0.0
            cx, cy = self.cell
            self._cut_cell(grid, cx, cy)
            step = min(1.0, d)
            nx, ny = self.x + (tx - self.x) / d * step, self.y + (ty - self.y) / d * step
            ncx, ncy = int(round(nx)), int(round(ny))
            if grid.state[ncy, ncx] == BURNING:
                self.say("line blocked by fire")
                self._finish()
                return
            self.x, self.y = nx, ny

    def _cut_cell(self, grid, cx: int, cy: int) -> None:
        width = int(self.spec["cut_width"])
        dense_ok = bool(self.spec.get("can_cut_dense", False))
        r = 0 if width <= 1 else width // 2
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if not (0 <= xx < grid.w and 0 <= yy < grid.h):
                    continue
                t = int(grid.terrain[yy, xx])
                if t in _NO_CUT or (t == int(TerrainType.DENSE_FOREST) and not dense_ok):
                    continue
                grid.set_terrain(xx, yy, int(TerrainType.FIREBREAK))

    def _advance_drop(self, grid, dt):
        """Fly and apply payload along each actual curved segment, never a chord."""
        rate = payload_per_cell(self.spec)
        budget = min(float(self.spec["speed"]) * dt, self.tank / rate)
        full_length = capacity_length(self.spec, self.capacity)
        while self.path and budget > 1e-8:
            target = self.path[0]
            distance = math.dist((self.x, self.y), target)
            if distance < 1e-8:
                self.path.pop(0)
                continue
            step = min(distance, budget)
            ox, oy = self.x, self.y
            self.x += (target[0] - self.x) * step / distance
            self.y += (target[1] - self.y) * step / distance
            grid.apply_line(
                ox,
                oy,
                self.x,
                self.y,
                float(self.spec["drop_width"]),
                self.spec.get("drop_agent", "water"),
                float(self.spec.get("drop_strength", 0.7)) * step / full_length * 6,
            )
            self.tank = max(0, self.tank - step * rate)
            budget -= step
            if step >= distance - 1e-8:
                self.path.pop(0)
        return not self.path or self.tank <= 1e-8

    def _do_drop(self, grid, o, dt, world) -> None:
        if len(o.points) < 2:
            self._finish()
            return
        if self.tank <= 1e-8:
            self._auto_reload(grid, o, world)
            return
        if self.drop_phase == 0:
            if not self.path:
                o.points = clip_path(o.points, capacity_length(self.spec, self.tank))
                self.path = [tuple(o.points[0])]
            if self._advance(grid, dt):
                self.drop_phase = 1
                self.path = [tuple(p) for p in o.points[1:]]
                self.say("beginning drop")
            self.state = "MOVING"
            return
        self.state = "WORKING"
        if self._advance_drop(grid, dt):
            self.say("drop complete")
            self._finish()
            if self.tank <= 1e-8:
                self._auto_reload(grid, None, world)

    def _auto_reload(self, grid, resume: Order | None, world) -> None:
        if self.spec.get("reload_at_base"):
            self.say("returning to base to reload")
            self.current = Order(REFILL, target=world.airbase, auto=True)
            self.reload_timer = float(self.spec["reload_seconds"])
        else:
            src = world.nearest_water(grid, self.cell, MoveClass.AIR, reach=2.5)
            if src is None:
                self.say("no water to refill from")
                self._finish()
                return
            self.say("going to refill")
            self.current = Order(REFILL, target=src, auto=True)
        if resume is not None:
            self.orders.appendleft(Order(resume.kind, resume.target, list(resume.points)))
        self._reset_order_state()

    def _plane_exit(self, grid):
        position = (self.x, self.y)
        exits = [(-12, self.y), (grid.w + 12, self.y), (self.x, -12), (self.x, grid.h + 12)]
        exit_point = min(exits, key=lambda p: math.dist(position, p))
        self.current = Order(REFILL, target=exit_point, auto=True)
        self.path = [exit_point]
        self.reload_timer = float(self.spec["reload_seconds"]) * max(0, 1 - self.tank / self.capacity)
        self.state = "EXITING"

    def _update_plane(self, grid, dt):
        """Fixed-wing sorties keep flying until they reach staging beyond the nearest map edge."""
        if self.current is None:
            if self.orders:
                self._next_order()
            elif 0 <= self.x < grid.w and 0 <= self.y < grid.h:
                self._plane_exit(grid)
            else:
                self.state = "READY"
                return
        order = self.current
        if order.kind == REFILL:
            if self.path:
                self.state = "EXITING"
                self._advance(grid, dt)
                return
            self.state = "RELOADING"
            self.reload_timer = max(0, self.reload_timer - dt)
            duration = float(self.spec["reload_seconds"])
            self.tank = self.capacity * (1 - self.reload_timer / duration)
            if self.reload_timer <= 0:
                self.tank = self.capacity
                self._finish()
                self.state = "READY"
            return
        if order.kind == DROP and len(order.points) >= 2:
            if self.drop_phase == 0:
                if not self.path:
                    order.points = clip_path(order.points, capacity_length(self.spec, self.tank))
                    if len(order.points) < 2:
                        self._plane_exit(grid)
                        return
                    self.path = [tuple(order.points[0])]
                self.state = "INBOUND"
                if self._advance(grid, dt):
                    self.drop_phase = 1
                    self.path = [tuple(p) for p in order.points[1:]]
                return
            self.state = "DROPPING"
            if self._advance_drop(grid, dt):
                self._plane_exit(grid)
            return
        if order.kind == MOVE and order.target:
            if not self.path:
                self.path = [tuple(order.target)]
            self.state = "INBOUND"
            if self._advance(grid, dt):
                self._plane_exit(grid)
            return
        self._plane_exit(grid)

    def _do_pickup(self, grid, o, dt, world) -> None:
        target = world.by_id(o.unit_id)
        if target is None or not target.alive or target.in_vehicle is not None or not target.is_foot:
            self.say("nothing to pick up")
            self._finish()
            return
        if len(self.passengers) >= self.passenger_slots:
            self.say("no room aboard")
            self._finish()
            return
        a = self._approach(grid, (target.x, target.y), 1.2, dt)
        if a is None:
            self.say("can't reach pickup")
            self._finish()
            return
        if a is False:
            return
        target.in_vehicle = self.uid
        target.orders.clear()
        target.current = None
        target._reset_order_state()
        target.hose = []
        target.state = ABOARD
        self.passengers.append(target.uid)
        self.say(f"picked up {target.label}")
        self._finish()

    def _do_dropoff(self, grid, o, dt, world) -> None:
        if not self.passengers:
            self._finish()
            return
        a = self._approach(grid, o.target, 1.2, dt)
        if a is None:
            self.say("can't reach drop-off")
            self._finish()
            return
        if a is False:
            return
        for pid in list(self.passengers):
            p = world.by_id(pid)
            if p is None:
                continue
            px, py = World.snap_passable(grid, p.move_class, self.x, self.y)
            p.x, p.y = px, py
            p.in_vehicle = None
            p.state = "IDLE"
            if p.is_civilian and world.in_safe_zone((px, py)):
                p.rescued = True
                p.state = SAFE
                self.say(f"{p.label} delivered to safety")
            else:
                self.say(f"dropped off {p.label}")
        self.passengers.clear()
        self._finish()

    # --- survival ------------------------------------------------------------

    def _fire_near(self, grid, r: float) -> tuple[float, float] | None:
        """Centroid of burning cells within r, or None."""
        ys, xs = grid._disc(self.x, self.y, r)
        if len(ys) == 0:
            return None
        b = grid.state[ys, xs] == BURNING
        if not b.any():
            return None
        return float(xs[b].mean()), float(ys[b].mean())

    def _safe_cell(
        self, grid, away_from: tuple[float, float] | None, clear: int = 4, max_r: int = 16
    ) -> tuple[int, int] | None:
        """Nearest passable cell with no fire within `clear`, preferring away from `away_from`."""
        cost = TERRAIN.cost_for(self.move_class)
        cx, cy = self.cell
        best, bs = None, -1e9
        for r in range(2, max_r + 1, 2):
            for dy in range(-r, r + 1, 2):
                for dx in range(-r, r + 1, 2):
                    x, y = cx + dx, cy + dy
                    if not (0 <= x < grid.w and 0 <= y < grid.h) or not math.isfinite(
                        cost[grid.terrain[y, x]]
                    ):
                        continue
                    ys, xs = grid._disc(x, y, clear)
                    if (grid.state[ys, xs] == BURNING).any():
                        continue
                    score = -math.hypot(dx, dy)
                    if away_from is not None:
                        score += 0.5 * math.hypot(x - away_from[0], y - away_from[1])
                    if score > bs:
                        bs, best = score, (x, y)
            if best is not None:
                return best
        return None

    def _flee_to_safety(self, grid) -> None:
        c = self._safe_cell(grid, self._fire_near(grid, 6))
        if c is not None:
            self.x, self.y = float(c[0]), float(c[1])

    def _civilian(self, grid, dt: float, world) -> None:
        if self.state in (SAFE, LOST):
            return
        cx, cy = self.cell
        if grid.state[cy, cx] == BURNING:
            self.alive = False
            self.state = LOST
            self.say("lost to the fire")
            return
        if world.in_safe_zone((self.x, self.y)):
            self.rescued = True
            self.state = SAFE
            self.say("reached safety")
            return
        near = self._fire_near(grid, float(self.spec.get("flee_radius", 9)))
        self.flee_timer -= dt
        if near is not None and (self.flee_timer <= 0 or not self.path):
            self.flee_timer = 6.0
            c = self._safe_cell(grid, near, clear=6, max_r=20)
            if c is not None:
                cost = build_cost(grid.terrain, grid.state, TERRAIN.cost_for(self.move_class))
                p = find_path(cost, self.cell, c)
                self.path = p or []
                if self.state != FLEEING:
                    self.say("fleeing the fire")
                self.state = FLEEING
        if self.path:
            self._advance(grid, dt)
        elif near is None:
            self.state = "IDLE"

    # --- (de)serialisation -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "utype": self.utype,
            "x": self.x,
            "y": self.y,
            "uid": self.uid,
            "state": self.state,
            "orders": [o.to_dict() for o in self.orders],
            "current": self.current.to_dict() if self.current else None,
            "path": [list(p) for p in self.path],
            "tank": self.tank,
            "hose": [list(c) for c in self.hose],
            "hose_source": list(self.hose_source) if self.hose_source else None,
            "reconnect_timer": self.reconnect_timer,
            "reload_timer": self.reload_timer,
            "replan_cooldown": self.replan_cooldown,
            "arrived": self.arrived,
            "cut_progress": self.cut_progress,
            "cut_index": self.cut_index,
            "drop_phase": self.drop_phase,
            "passengers": list(self.passengers),
            "in_vehicle": self.in_vehicle,
            "rescued": self.rescued,
            "alive": self.alive,
            "flee_timer": self.flee_timer,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Unit":
        u = cls(d["utype"], float(d["x"]), float(d["y"]), uid=int(d["uid"]))
        u.state = d["state"]
        u.orders = deque(Order.from_dict(o) for o in d["orders"])
        u.current = Order.from_dict(d["current"]) if d["current"] else None
        u.path = [tuple(p) for p in d["path"]]
        u.tank = float(d["tank"])
        u.hose = [tuple(c) for c in d["hose"]]
        u.hose_source = tuple(d["hose_source"]) if d["hose_source"] else None
        for k in (
            "reconnect_timer",
            "reload_timer",
            "replan_cooldown",
            "arrived",
            "cut_progress",
            "cut_index",
            "drop_phase",
            "passengers",
            "in_vehicle",
            "rescued",
            "alive",
            "flee_timer",
        ):
            setattr(u, k, d[k])
        return u


@dataclass
class Arrival:
    utype: str
    at: float
    x: float
    y: float


class World:
    """Container for units plus shared lookups (water sources, airbase, staging, safe zone)."""

    def __init__(
        self,
        airbase: tuple[float, float] = (0.0, 0.0),
        staging: tuple[float, float] | None = None,
        safe_zone: tuple[float, float, float] | None = None,
    ):
        self.units: list[Unit] = []
        self.airbase = tuple(airbase)
        self.staging = tuple(staging) if staging else tuple(airbase)
        self.safe_zone = tuple(safe_zone) if safe_zone else None  # (x, y, radius)
        self.pending: list[Arrival] = []
        self._next_uid = 1  # per-world counter so replays assign identical ids

    # ---- spawning -------------------------------------------------------------------

    def add(self, utype: str, x: float, y: float, grid: "FireGrid | None" = None) -> Unit:
        if utype not in UNITS:
            raise KeyError(f"unknown unit type {utype}")
        if grid is not None:
            x, y = self.snap_passable(grid, UNITS[utype]["movement_class"], x, y)
        u = Unit(utype, float(x), float(y), uid=self._next_uid)
        if grid is not None and u.is_plane:
            u.x = -12.0 if u.uid % 2 else grid.w + 12.0
            u.y = min(max(y, 1), grid.h - 2)
            u.state = "READY"
        self._next_uid += 1
        self.units.append(u)
        return u

    def spawn_point(self, utype: str, x: float | None, y: float | None) -> tuple[float, float]:
        """Where a purchased unit appears: aircraft at the airbase, everything else at staging,
        unless the caller gave explicit coordinates."""
        if x is not None and y is not None:
            return float(x), float(y)
        mc = UNITS[utype]["movement_class"]
        return self.airbase if mc == MoveClass.AIR else self.staging

    def request(self, utype: str, now: float, x: float | None = None, y: float | None = None) -> Arrival:
        """Queue a unit to arrive after its dispatch delay."""
        px, py = self.spawn_point(utype, x, y)
        a = Arrival(utype, now + float(UNITS[utype].get("arrival_seconds", 0)), px, py)
        self.pending.append(a)
        return a

    def _deliver(self, grid: "FireGrid", now: float) -> list[Unit]:
        arrived = []
        for a in list(self.pending):
            if a.at <= now:
                u = self.add(a.utype, a.x, a.y, grid)
                u._t = now
                u.say("arrived on scene", now)
                arrived.append(u)
                self.pending.remove(a)
        return arrived

    @staticmethod
    def snap_passable(
        grid: "FireGrid", mc: MoveClass, x: float, y: float, max_r: int = 12
    ) -> tuple[float, float]:
        """Nearest cell this movement class can stand on (scenario placement is forgiving)."""
        cost = TERRAIN.cost_for(mc)
        cx, cy = int(round(x)), int(round(y))
        cx = min(max(cx, 0), grid.w - 1)
        cy = min(max(cy, 0), grid.h - 1)
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

    # ---- queries ----------------------------------------------------------------------

    def by_id(self, uid: int | None) -> Unit | None:
        if uid is None:
            return None
        for u in self.units:
            if u.uid == uid:
                return u
        return None

    def in_safe_zone(self, p: tuple[float, float]) -> bool:
        if self.safe_zone is None:
            return False
        x, y, r = self.safe_zone
        return math.hypot(p[0] - x, p[1] - y) <= r

    def civilians(self) -> list[Unit]:
        return [u for u in self.units if u.is_civilian]

    def nearest_water(
        self, grid: "FireGrid", frm: tuple[int, int], mc: MoveClass, reach: float = 2.5
    ) -> tuple[int, int] | None:
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
            r = int(math.ceil(reach))
            best = None
            bd = 1e9
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

    # ---- tick -------------------------------------------------------------------------

    def update(self, grid: "FireGrid", dt: float) -> list[Unit]:
        arrived = self._deliver(grid, grid.time)
        for u in self.units:
            u.update(grid, dt, self)
        return arrived

    # ---- (de)serialisation -------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "airbase": list(self.airbase),
            "staging": list(self.staging),
            "safe_zone": list(self.safe_zone) if self.safe_zone else None,
            "next_uid": self._next_uid,
            "units": [u.to_dict() for u in self.units],
            "pending": [a.__dict__ for a in self.pending],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "World":
        w = cls(
            tuple(d["airbase"]), tuple(d["staging"]), tuple(d["safe_zone"]) if d.get("safe_zone") else None
        )
        w._next_uid = int(d["next_uid"])
        w.units = [Unit.from_dict(u) for u in d["units"]]
        w.pending = [Arrival(**a) for a in d["pending"]]
        return w
