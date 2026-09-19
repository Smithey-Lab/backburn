"""Cell-based wildfire model (SimulationCore).

A probabilistic cellular automaton on an 8-neighbour grid, fully vectorised with
NumPy. Per tick, every burning/smoldering cell radiates heat to its neighbours,
biased by wind; each unburned neighbour rolls for ignition against a hazard rate
built from the brief's formula:

    hazard = base fuel rate × wind factor × slope factor × dryness
             × neighbour heat × (1 − suppression)

    P(ignite in dt) = 1 − exp(−hazard · dt)

Cell life cycle:  UNBURNED → BURNING → SMOLDER → COLD
Extinguishing a BURNING cell sends it back to UNBURNED with less fuel and high
moisture, which is what produces apparent rekindling once that moisture dries.

Coordinates: arrays are indexed [y, x] with y growing downward (row 0 is the top
of the map). Wind direction is a compass bearing in degrees the wind is blowing
TOWARD: 0 = north (up, −y), 90 = east (+x), 180 = south (+y), 270 = west (−x).

Active regions
--------------
Every array covers the whole map, but a tick only computes inside a small set of
bounding boxes ("regions") that together contain every burning and smoldering
cell (plus one cell of margin, the furthest heat travels per tick) and every wet
cell (water, retardant or raised moisture, which all decay). Outside them nothing
can change, so the maths is identical to a full-grid pass while the cost follows
the size of the fires, not the size of the map. Regions are re-derived from the
arrays every tick by grouping active 32×32 tiles, so separate fires stay separate
and merge only when they approach each other. Embers and scripted ignitions that
land outside every region open a new one. Maps up to 256×256 skip all of this and
process the whole grid.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

import numpy as np

from .config import FIRE, TERRAIN, TerrainType

UNBURNED, BURNING, SMOLDER, COLD = 0, 1, 2, 3
FULL_GRID_CELLS = 256 * 256  # at or below this size, every tick processes the whole map
RESCAN_EVERY = 64  # ticks between exact full-map re-derivations of the regions
TILE = 32  # activity is grouped per tile of this size when regions are derived
MAX_REGIONS = 12

# 8-neighbour offsets as (dx, dy) and their distance weight.
_OFFSETS: list[tuple[int, int, float]] = [
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, 0.7071),
    (1, -1, 0.7071),
    (-1, 1, 0.7071),
    (-1, -1, 0.7071),
]


def bearing_to_vector(bearing_deg: float) -> tuple[float, float]:
    """Compass bearing (blowing toward) → unit vector (dx, dy) in grid coords."""
    r = math.radians(bearing_deg)
    return math.sin(r), -math.cos(r)


@dataclass
class FireStats:
    burning: int
    smoldering: int
    burned_cells: int
    structures_total: int
    structures_lost: int
    area_burned_frac: float
    spot_fires: int = 0
    embers_in_air: int = 0


@dataclass
class Ember:
    """A lofted ember: launched this tick, lands after `ttl` ticks."""

    x0: float
    y0: float
    x1: float
    y1: float
    ttl: int
    duration: int = 0

    def __post_init__(self):
        if self.duration <= 0:
            self.duration = self.ttl


Box = tuple[int, int, int, int]  # y0, y1, x0, x1 (half-open)


def _union(a: Box | None, b: Box | None) -> Box | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3])


def _mask_box(mask: np.ndarray, oy: int = 0, ox: int = 0) -> Box | None:
    """Tight bounding box of the True cells of ``mask``, offset into map coordinates."""
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    return int(rows[0]) + oy, int(rows[-1]) + 1 + oy, int(cols[0]) + ox, int(cols[-1]) + 1 + ox


def _mask_regions(mask: np.ndarray, oy: int = 0, ox: int = 0) -> list[Box]:
    """Bounding boxes of the connected groups of active tiles in ``mask`` (map coordinates)."""
    h, w = mask.shape
    th, tw = -(-h // TILE), -(-w // TILE)
    padded = np.zeros((th * TILE, tw * TILE), bool)
    padded[:h, :w] = mask
    tiles = padded.reshape(th, TILE, tw, TILE).any(axis=(1, 3))
    active = {(int(y), int(x)) for y, x in np.argwhere(tiles)}
    out: list[Box] = []
    while active:
        seed = next(iter(active))
        stack = [seed]
        active.discard(seed)
        ty0 = ty1 = seed[0]
        tx0 = tx1 = seed[1]
        while stack:
            y, x = stack.pop()
            ty0, ty1, tx0, tx1 = min(ty0, y), max(ty1, y), min(tx0, x), max(tx1, x)
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if (ny, nx) in active:
                        active.discard((ny, nx))
                        stack.append((ny, nx))
        # Tighten from tiles to cells.
        sub = mask[ty0 * TILE : min(h, (ty1 + 1) * TILE), tx0 * TILE : min(w, (tx1 + 1) * TILE)]
        box = _mask_box(sub, ty0 * TILE + oy, tx0 * TILE + ox)
        if box is not None:
            out.append(box)
    return out


def _overlaps(a: Box, b: Box, gap: int) -> bool:
    return a[0] < b[1] + gap and b[0] < a[1] + gap and a[2] < b[3] + gap and b[2] < a[3] + gap


def _merge_regions(boxes: list[Box], gap: int = 2, limit: int = MAX_REGIONS) -> list[Box]:
    """Union boxes that come within ``gap`` cells of each other; cap the count by merging
    the closest pairs. Sorted so processing order is deterministic."""
    boxes = [b for b in boxes if b[0] < b[1] and b[2] < b[3]]
    merged = True
    while merged:
        merged = False
        out: list[Box] = []
        for b in boxes:
            for i, o in enumerate(out):
                if _overlaps(b, o, gap):
                    out[i] = _union(o, b)
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    while len(boxes) > limit:
        best, pair = None, None
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                u = _union(boxes[i], boxes[j])
                grow = (u[1] - u[0]) * (u[3] - u[2])
                if best is None or grow < best:
                    best, pair = grow, (i, j)
        i, j = pair
        boxes = [b for k, b in enumerate(boxes) if k not in pair] + [_union(boxes[i], boxes[j])]
        boxes = _merge_regions(boxes, gap, limit + 1) if len(boxes) > limit else boxes
    return sorted(boxes)


class FireGrid:
    """All per-cell state plus the fire-spread step."""

    def __init__(
        self,
        terrain: np.ndarray,
        seed: int = 0,
        base_moisture: float = 0.15,
        elevation: np.ndarray | None = None,
    ):
        assert terrain.ndim == 2
        self.h, self.w = terrain.shape
        self.terrain = terrain.astype(np.uint8)
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.time = 0.0
        self.version = 0  # bumps whenever fire state or terrain changes (caches key on it)

        self.fuel = TERRAIN.fuel[self.terrain].copy()
        self.base_moisture = np.full((self.h, self.w), base_moisture, np.float32)
        self.moisture = self.base_moisture.copy()
        self.state = np.zeros((self.h, self.w), np.uint8)
        self.heat = np.zeros((self.h, self.w), np.float32)
        self.exposure = np.zeros((self.h, self.w), np.float32)  # last tick's incoming heat (overlay)
        self.water = np.zeros((self.h, self.w), np.float32)
        self.retardant = np.zeros((self.h, self.w), np.float32)
        self.smolder_timer = np.zeros((self.h, self.w), np.float32)
        self.ignited_at = np.full((self.h, self.w), -1.0, np.float32)
        self.elevation = (
            elevation.astype(np.float32) if elevation is not None else np.zeros((self.h, self.w), np.float32)
        )

        self.weather_enabled = False
        self.weather_base_speed = 0.0
        self.weather_base_bearing = 0.0
        self.weather_epoch = 0.0
        self.wind_speed = 0.0  # m/s
        self.wind_bearing = 0.0  # degrees, blowing toward
        self._labels: np.ndarray | None = None
        self._structures_total = 0
        self._relabel_structures()
        self._wind_cache: tuple[float, float] | None = None
        self._wind_factors: np.ndarray | None = None
        self._slope_tabs: list[np.ndarray] | None = None
        self.embers: list[Ember] = []
        self.spot_fires = 0
        self._tables()
        # Active-region bookkeeping (see module docstring).
        self._use_box = self.w * self.h > FULL_GRID_CELLS
        self._regions: list[Box] = []  # tight boxes around burning/smoldering/wet cells
        self._pending: list[Box] = []  # boxes written outside the step (ignitions, drops, embers)
        self._scar: Box | None = None  # any cell that ever burned
        self._last_boxes: list[Box] = []  # where heat/exposure were written last tick
        self._ticks = 0
        self._stats_cache: tuple[int, FireStats] | None = None
        self.navigator = None  # pathfinding.Navigator, created lazily (avoids an import cycle)
        self.changed_cells: list[tuple[int, int]] = []  # terrain edits since a renderer last drained them
        self.changed_all = False
        if elevation is not None:
            self.set_elevation(elevation)

    # ---- cached per-cell terrain tables -------------------------------------------------

    def _tables(self) -> None:
        T = self.terrain
        self._fuel_ok = TERRAIN.fuel[T] > 0.01
        self._ign_rate = TERRAIN.ignition_rate[T]
        self._burn_rate = TERRAIN.burn_rate[T]
        self._heat_out = TERRAIN.heat_output[T]
        self._smolder_time = TERRAIN.smolder_time[T]
        self._fuel_cells = int((TERRAIN.fuel[T] > 0.02).sum())

    def _table_cell(self, x: int, y: int, ttype: int) -> None:
        was_fuel = TERRAIN.fuel[self.terrain[y, x]] > 0.02
        self._fuel_ok[y, x] = TERRAIN.fuel[ttype] > 0.01
        self._ign_rate[y, x] = TERRAIN.ignition_rate[ttype]
        self._burn_rate[y, x] = TERRAIN.burn_rate[ttype]
        self._heat_out[y, x] = TERRAIN.heat_output[ttype]
        self._smolder_time[y, x] = TERRAIN.smolder_time[ttype]
        now_fuel = TERRAIN.fuel[ttype] > 0.02
        self._fuel_cells += int(now_fuel) - int(was_fuel)

    def nav(self):
        """Per-tick pathfinding cache shared by every unit on this grid."""
        if self.navigator is None:
            from .pathfinding import Navigator  # local import: pathfinding imports this module

            self.navigator = Navigator()
        return self.navigator

    @property
    def fuel_cells(self) -> int:
        """Number of cells that hold enough fuel to burn (scored as 'area')."""
        return self._fuel_cells

    # ---- wind ---------------------------------------------------------------

    def set_wind(self, speed: float, bearing_deg: float) -> None:
        self.wind_speed = float(max(0.0, speed))
        self.wind_bearing = float(bearing_deg % 360.0)
        self.weather_base_speed = self.wind_speed
        self.weather_base_bearing = self.wind_bearing
        self.weather_epoch = self.time
        self._wind_cache = None

    def _update_weather(self):
        if not self.weather_enabled:
            return
        phase = max(0, self.time - self.weather_epoch) / 35.0
        index = int(phase)
        fraction = phase - index
        blend = fraction * fraction * (3 - 2 * fraction)

        def knot(number):
            if number == 0:
                return 0.0, 0.0
            rng = random.Random(self.seed * 104729 + number * 8191)
            return rng.uniform(-0.25, 0.35), rng.uniform(-20, 20)

        a, b = knot(index), knot(index + 1)
        speed_offset = a[0] + (b[0] - a[0]) * blend
        bearing_offset = a[1] + (b[1] - a[1]) * blend
        self.wind_speed = self.weather_base_speed * (1 + speed_offset)
        self.wind_bearing = (self.weather_base_bearing + bearing_offset) % 360
        self._wind_cache = None

    def _wind_factor_table(self) -> np.ndarray:
        key = (self.wind_speed, self.wind_bearing)
        if self._wind_cache == key and self._wind_factors is not None:
            return self._wind_factors
        wx, wy = bearing_to_vector(self.wind_bearing)
        gain = float(FIRE["wind_gain"])
        upmin = float(FIRE["wind_upwind_min"])
        out = np.zeros(len(_OFFSETS), np.float32)
        for i, (dx, dy, dist_w) in enumerate(_OFFSETS):
            n = math.hypot(dx, dy)
            cos_t = (dx * wx + dy * wy) / n
            f = 1.0 + gain * self.wind_speed * cos_t
            out[i] = max(upmin, f) * dist_w
        self._wind_cache = key
        self._wind_factors = out
        return out

    # ---- slope ------------------------------------------------------------------

    def set_elevation(self, elevation: np.ndarray) -> None:
        """Elevation in metres per cell. Uphill spread is faster (Rothermel-style slope factor)."""
        self.elevation = elevation.astype(np.float32)
        self._slope_tabs = None

    def _slope_factor_tables(self) -> list[np.ndarray] | None:
        """Per-direction multiplier on heat arriving at a target cell, cached.
        factor = exp(slope_gain · rise/run) clamped, rise/run measured from source to target."""
        gain = float(FIRE.get("slope_gain", 0.0))
        if gain <= 0.0 or self.elevation is None or not np.any(self.elevation):
            return None
        if self._slope_tabs is not None:
            return self._slope_tabs
        cell_m = float(FIRE.get("cell_size_m", 10.0))
        h, w = self.h, self.w
        tabs = []
        for dx, dy, _ in _OFFSETS:
            tab = np.ones((h, w), np.float32)
            run = math.hypot(dx, dy) * cell_m
            tgt = self.elevation[max(0, dy) : h + min(0, dy), max(0, dx) : w + min(0, dx)]
            src = self.elevation[max(0, -dy) : h + min(0, -dy), max(0, -dx) : w + min(0, -dx)]
            grade = np.clip((tgt - src) / run, -1.0, 1.0)
            tab[max(0, dy) : h + min(0, dy), max(0, dx) : w + min(0, dx)] = np.exp(gain * grade)
            tabs.append(tab)
        self._slope_tabs = tabs
        return tabs

    # ---- active region ----------------------------------------------------------------

    def _touch(self, y0: int, y1: int, x0: int, x1: int) -> None:
        """Record that cells in this box were written outside the step (ignitions, drops)."""
        box = (max(0, y0), min(self.h, y1), max(0, x0), min(self.w, x1))
        if box[0] < box[1] and box[2] < box[3]:
            self._pending.append(box)

    def _clamp(self, box: Box | None, margin: int = 0) -> Box | None:
        if box is None:
            return None
        return (
            max(0, box[0] - margin),
            min(self.h, box[1] + margin),
            max(0, box[2] - margin),
            min(self.w, box[3] + margin),
        )

    def _activity(self, y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
        st = self.state[y0:y1, x0:x1]
        return (
            (st == BURNING)
            | (st == SMOLDER)
            | (self.water[y0:y1, x0:x1] > 0)
            | (self.retardant[y0:y1, x0:x1] > 0)
            | (self.moisture[y0:y1, x0:x1] != self.base_moisture[y0:y1, x0:x1])
        )

    def rescan(self) -> None:
        """Re-derive every region from the arrays (after direct edits or a load)."""
        st = self.state
        self._scar = _mask_box(st != UNBURNED)
        self._regions = _merge_regions(_mask_regions(self._activity(0, self.h, 0, self.w)))
        self._pending = []
        self._stats_cache = None

    def regions(self) -> list[Box]:
        """Boxes the next tick will process, each with its one-cell margin (whole map on small grids)."""
        if not self._use_box:
            return [(0, self.h, 0, self.w)]
        return [self._clamp(b, 1) for b in _merge_regions(self._regions + self._pending)]

    def active_box(self) -> Box:
        """Union of the regions, for display and benchmarks."""
        box = None
        for b in self.regions():
            box = _union(box, b)
        return box or (0, 0, 0, 0)

    # ---- external actions ---------------------------------------------------

    def ignite(self, x: int, y: int, radius: int = 0) -> int:
        """Set cells burning. Returns number of cells actually ignited."""
        ys, xs = self._disc(x, y, radius)
        m = (self.state[ys, xs] == UNBURNED) & (self.fuel[ys, xs] > 0.01)
        ys, xs = ys[m], xs[m]
        if len(ys):
            self.state[ys, xs] = BURNING
            self.ignited_at[ys, xs] = self.time
            box = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
            self._scar = _union(self._scar, box)
            self._touch(*box)
            self.version += 1
            self._stats_cache = None
        return int(len(ys))

    def extinguish(self, x: int, y: int, radius: int = 1) -> None:
        """Editor eraser: remove fire without any suppression side effects."""
        ys, xs = self._disc(x, y, radius)
        if len(ys):
            self.state[ys, xs] = UNBURNED
            self.smolder_timer[ys, xs] = 0.0
            self._touch(int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
            self.version += 1
            self._stats_cache = None

    def apply_water(self, x: float, y: float, radius: float, amount: float) -> None:
        ys, xs = self._disc(x, y, radius)
        if len(ys):
            self.water[ys, xs] = np.minimum(self.water[ys, xs] + amount, 1.5)
            self._touch(int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)

    def apply_retardant(self, x: float, y: float, radius: float, amount: float) -> None:
        ys, xs = self._disc(x, y, radius)
        if len(ys):
            self.retardant[ys, xs] = np.minimum(self.retardant[ys, xs] + amount, 1.0)
            self._touch(int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)

    def apply_line(
        self, x0: float, y0: float, x1: float, y1: float, width: float, agent: str, amount: float
    ) -> None:
        """Lay water or retardant along a segment (aircraft drop)."""
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) * 2) + 1)
        for t in np.linspace(0.0, 1.0, n):
            px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            if agent == "retardant":
                self.apply_retardant(px, py, width / 2, amount / n * 3)
            else:
                self.apply_water(px, py, width / 2, amount / n * 3)

    def _relabel_structures(self) -> None:
        """Group STRUCTURE cells into buildings (4-connected components). A building is
        lost when any of its cells burns, so 'structures' counts buildings, not cells."""
        struct = self.terrain == TerrainType.STRUCTURE
        labels = np.zeros((self.h, self.w), np.int32)
        n = 0
        ys, xs = np.nonzero(struct)
        for y0, x0 in zip(ys, xs):
            if labels[y0, x0]:
                continue
            n += 1
            stack = [(int(y0), int(x0))]
            labels[y0, x0] = n
            while stack:
                y, x = stack.pop()
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < self.h and 0 <= nx < self.w and struct[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = n
                        stack.append((ny, nx))
        self._labels = labels
        self._structures_total = n

    def structures_lost(self) -> int:
        if self._labels is None:
            self._relabel_structures()
        if self._structures_total == 0:
            return 0
        box = (0, self.h, 0, self.w) if not self._use_box else self._scar
        if box is None:
            return 0
        y0, y1, x0, x1 = box
        st = self.state[y0:y1, x0:x1]
        lab = self._labels[y0:y1, x0:x1]
        hot = (st != UNBURNED) & (lab > 0)
        return int(len(np.unique(lab[hot])))

    def set_terrain(self, x: int, y: int, ttype: int, reset_fuel: bool = True) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        was = int(self.terrain[y, x])
        if was == ttype and (not reset_fuel or self.fuel[y, x] == TERRAIN.fuel[ttype]):
            return
        if was == int(TerrainType.STRUCTURE) or ttype == int(TerrainType.STRUCTURE):
            self._labels = None  # relabel lazily
        self._table_cell(x, y, ttype)
        self.terrain[y, x] = ttype
        if reset_fuel:
            self.fuel[y, x] = TERRAIN.fuel[ttype]
        if TERRAIN.fuel[ttype] <= 0.02 and self.state[y, x] == BURNING:
            self.state[y, x] = COLD
        self.version += 1
        self._stats_cache = None
        if len(self.changed_cells) < 4096:
            self.changed_cells.append((x, y))
        else:
            self.changed_all = True

    def _disc(self, x: float, y: float, radius: float) -> tuple[np.ndarray, np.ndarray]:
        r = int(math.ceil(radius))
        cx, cy = int(round(x)), int(round(y))
        y0, y1 = max(0, cy - r), min(self.h, cy + r + 1)
        x0, x1 = max(0, cx - r), min(self.w, cx + r + 1)
        if y0 >= y1 or x0 >= x1:
            return np.zeros(0, int), np.zeros(0, int)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        m = (xx - x) ** 2 + (yy - y) ** 2 <= (radius + 0.5) ** 2
        return yy[m], xx[m]

    # ---- the step -------------------------------------------------------------

    def step(self, dt: float = 1.0) -> None:
        self._update_weather()
        self.version += 1
        self._stats_cache = None
        self._ticks += 1
        if self._use_box and self._ticks % RESCAN_EVERY == 0:
            self.rescan()
        boxes = self.regions()
        # Clear the display fields where they were written last tick.
        for ly0, ly1, lx0, lx1 in self._last_boxes:
            self.heat[ly0:ly1, lx0:lx1] = 0.0
            self.exposure[ly0:ly1, lx0:lx1] = 0.0
        self._last_boxes = boxes
        self._pending = []
        found: list[Box] = []
        for box in boxes:
            found.extend(self._step_box(box, dt))
        self._spot_land(dt)
        if self._use_box:
            self._regions = _merge_regions(found + self._pending)
            self._pending = []
        self.time += dt

    def _step_box(self, box: Box, dt: float) -> list[Box]:
        """One tick of the fire model inside ``box``; returns the tight regions left active."""
        y0, y1, x0, x1 = box
        h, w = y1 - y0, x1 - x0
        st = self.state[y0:y1, x0:x1]
        fuel = self.fuel[y0:y1, x0:x1]
        water = self.water[y0:y1, x0:x1]
        retardant = self.retardant[y0:y1, x0:x1]
        moisture = self.moisture[y0:y1, x0:x1]
        smolder_timer = self.smolder_timer[y0:y1, x0:x1]
        fuel_ok = self._fuel_ok[y0:y1, x0:x1]
        heat_out = self._heat_out[y0:y1, x0:x1]
        burning = st == BURNING
        smold = st == SMOLDER

        # 1. Heat each cell emits this tick.
        emitted = np.zeros((h, w), np.float32)
        emitted[burning] = heat_out[burning]
        if FIRE["smolder_ignites_neighbours"] and smold.any():
            frac = float(FIRE["smolder_heat_fraction"])
            st_time = self._smolder_time[y0:y1, x0:x1][smold]
            ratio = np.where(st_time > 0, smolder_timer[smold] / np.maximum(st_time, 1e-6), 0.0)
            emitted[smold] = heat_out[smold] * frac * ratio
        self.heat[y0:y1, x0:x1] = emitted  # display/debug field

        # 2. Exposure: shifted sums of emitted heat, wind-weighted per direction.
        wf = self._wind_factor_table()
        slope = self._slope_factor_tables()
        exposure = np.zeros((h, w), np.float32)
        for i, (dx, dy, _) in enumerate(_OFFSETS):
            # Heat travelling in direction (dx, dy): target[y, x] gets source[y-dy, x-dx].
            ys, ye, xs, xe = max(0, dy), h + min(0, dy), max(0, dx), w + min(0, dx)
            tgt_view = exposure[ys:ye, xs:xe]
            src_view = emitted[max(0, -dy) : h + min(0, -dy), max(0, -dx) : w + min(0, -dx)]
            if dx and dy:
                # Diagonal neighbour heat cannot squeeze between touching
                # non-fuel cells in a completed road, cut line or water barrier.
                side_a = fuel_ok[ys:ye, xs - dx : xe - dx]
                side_b = fuel_ok[ys - dy : ye - dy, xs:xe]
                src_view = src_view * (side_a & side_b)
            if slope is not None:
                tgt_view += src_view * wf[i] * slope[i][y0 + ys : y0 + ye, x0 + xs : x0 + xe]
            else:
                tgt_view += src_view * wf[i]
        self.exposure[y0:y1, x0:x1] = exposure

        # 3. Hazard → probability.
        dryness = np.clip(1.0 - moisture, float(FIRE["dryness_floor"]), 1.0)
        suppression = np.clip(water, 0.0, 1.0)
        hazard = (
            exposure
            * float(FIRE["exposure_scale"])
            * self._ign_rate[y0:y1, x0:x1]
            * dryness
            * np.clip(1.0 - float(FIRE["retardant_strength"]) * retardant, 0.0, 1.0)
            * (1.0 - suppression)
        )
        p = 1.0 - np.exp(-hazard * dt)
        roll = self.rng.random((h, w), dtype=np.float32)
        can_ignite = (st == UNBURNED) & (fuel > 0.01)
        new_fire = can_ignite & (roll < p)

        # 4. Burning cells consume fuel; run out → smolder.
        if burning.any():
            fuel[burning] -= self._burn_rate[y0:y1, x0:x1][burning] * dt
            out = burning & (fuel <= 0.0)
            fuel[out] = 0.0
            st[out] = SMOLDER
            smolder_timer[out] = self._smolder_time[y0:y1, x0:x1][out]

        # 5. Water extinguishes burning cells (back to UNBURNED, wet, less fuel).
        thr = float(FIRE["water_extinguish_threshold"])
        put_out = (st == BURNING) & (water >= thr)
        if put_out.any():
            st[put_out] = UNBURNED
            moisture[put_out] = np.minimum(1.0, moisture[put_out] + float(FIRE["water_moisture_gain"]))
            water[put_out] *= 0.5
            fuel[put_out] *= 0.85

        # 6. Smolder cools; water kills smolder quickly.
        smold = st == SMOLDER
        if smold.any():
            smolder_timer[smold] -= dt * (1.0 + 6.0 * np.clip(water[smold], 0, 1))
            cold = smold & (smolder_timer <= 0)
            st[cold] = COLD
            smolder_timer[cold] = 0.0

        # 7. Apply ignitions last so they don't burn on the tick they start.
        st[new_fire] = BURNING
        self.ignited_at[y0:y1, x0:x1][new_fire] = self.time

        # 7b. Ember spotting: strong wind lofts embers from hot fuels to land downwind.
        self._spot_launch(burning, dt, y0, x0)

        # 8. Field decay: water evaporates, moisture returns toward base, retardant fades.
        base = self.base_moisture[y0:y1, x0:x1]
        np.maximum(moisture, np.minimum(1.0, water * 0.4 + moisture), out=moisture)
        water -= water * float(FIRE["water_decay"]) * dt
        water[water < 1e-3] = 0.0
        moisture -= (moisture - base) * float(FIRE["moisture_dry_rate"]) * dt
        settled = np.abs(moisture - base) < 1e-4
        moisture[settled] = base[settled]
        retardant -= retardant * float(FIRE["retardant_decay"]) * dt
        retardant[retardant < 1e-3] = 0.0

        if not self._use_box:
            return []
        # 9. Tighten: what is still active here, grouped so separate fires stay separate.
        scar = _mask_box(st != UNBURNED, y0, x0)
        self._scar = _union(self._scar, scar)
        return _mask_regions(self._activity(y0, y1, x0, x1), y0, x0)

    # ---- spotting ---------------------------------------------------------------

    def _spot_land(self, dt: float) -> None:
        """Land embers whose flight time is up; landing on unburned fuel may ignite it."""
        if not self.embers:
            return
        keep = []
        lx, ly = [], []
        for e in self.embers:
            e.ttl -= 1
            if e.ttl <= 0:
                lx.append(e.x1)
                ly.append(e.y1)
            else:
                keep.append(e)
        self.embers = keep
        if not lx:
            return
        xs = np.rint(lx).astype(int)
        ys = np.rint(ly).astype(int)
        inside = (xs >= 0) & (xs < self.w) & (ys >= 0) & (ys < self.h)
        xs, ys = xs[inside], ys[inside]
        if len(xs) == 0:
            return
        dryness = np.clip(1.0 - self.moisture[ys, xs], float(FIRE["dryness_floor"]), 1.0)
        p_ign = (
            float(FIRE.get("spot_ignite", 0.5))
            * dryness
            * np.clip(1.0 - float(FIRE["retardant_strength"]) * self.retardant[ys, xs], 0, 1)
            * (1.0 - np.clip(self.water[ys, xs], 0, 1))
        )
        roll = self.rng.random(len(xs))
        ok = (self.state[ys, xs] == UNBURNED) & (self.fuel[ys, xs] > 0.01) & (roll < p_ign)
        if ok.any():
            ys, xs = ys[ok], xs[ok]
            self.state[ys, xs] = BURNING
            self.ignited_at[ys, xs] = self.time
            self.spot_fires += int(len(ys))
            box = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
            self._scar = _union(self._scar, box)
            self._touch(*box)

    def _spot_launch(self, burning: np.ndarray, dt: float, oy: int, ox: int) -> None:
        """Launch new embers from burning cells in the active box.

        Each burning cell with heat_output ≥ spot_min_heat launches with probability
        spot_rate·(wind − spot_min_wind)·heat·dt. Landing point is downwind at
        spot_dist_base + wind·spot_dist_gain cells, with jitter.
        """
        min_wind = float(FIRE.get("spot_min_wind", 8.0))
        if self.wind_speed < min_wind or not burning.any():
            return
        rate = float(FIRE.get("spot_rate", 0.002))
        min_heat = float(FIRE.get("spot_min_heat", 0.9))
        ys, xs = np.nonzero(burning)
        heat = TERRAIN.heat_output[self.terrain[ys + oy, xs + ox]]
        hot = heat >= min_heat
        if not hot.any():
            return
        ys, xs, heat = ys[hot] + oy, xs[hot] + ox, heat[hot]
        p = rate * (self.wind_speed - min_wind) * heat * dt
        launch = self.rng.random(len(ys)) < p
        if not launch.any():
            return
        ys, xs = ys[launch], xs[launch]
        n = len(ys)
        wx, wy = bearing_to_vector(self.wind_bearing)
        dist = float(FIRE.get("spot_dist_base", 4.0)) + (self.wind_speed - min_wind) * float(
            FIRE.get("spot_dist_gain", 1.2)
        ) * self.rng.random(n, dtype=np.float32)
        jitter = self.rng.normal(0.0, float(FIRE.get("spot_jitter", 1.5)), size=(n, 2))
        x1 = xs + wx * dist + jitter[:, 0]
        y1 = ys + wy * dist + jitter[:, 1]
        ttl = np.maximum(1, np.rint(dist / max(1.0, self.wind_speed * 0.5)).astype(int))
        for i in range(n):
            self.embers.append(Ember(float(xs[i]), float(ys[i]), float(x1[i]), float(y1[i]), int(ttl[i])))

    def is_out(self) -> bool:
        """True when nothing is burning, nothing is smoldering hot enough to re-ignite, and no embers fly."""
        if self.embers:
            return False
        s = self.stats()
        if s.burning:
            return False
        if s.smoldering == 0:
            return True
        return all(bool(self.smolder_timer[y0:y1, x0:x1].max() <= 0.0) for y0, y1, x0, x1 in self.regions())

    # ---- (de)serialisation for savegames ------------------------------------------

    def to_arrays(self) -> dict:
        return {
            "terrain": self.terrain,
            "fuel": self.fuel,
            "moisture": self.moisture,
            "base_moisture": self.base_moisture,
            "state": self.state,
            "heat": self.heat,
            "water": self.water,
            "retardant": self.retardant,
            "smolder_timer": self.smolder_timer,
            "ignited_at": self.ignited_at,
            "elevation": self.elevation,
        }

    def meta(self) -> dict:
        return {
            "seed": self.seed,
            "time": self.time,
            "wind_speed": self.wind_speed,
            "wind_bearing": self.wind_bearing,
            "spot_fires": self.spot_fires,
            "rng_state": self.rng.bit_generator.state,
            "weather_enabled": self.weather_enabled,
            "weather_base_speed": self.weather_base_speed,
            "weather_base_bearing": self.weather_base_bearing,
            "weather_epoch": self.weather_epoch,
            "embers": [e.__dict__ for e in self.embers],
            "ticks": self._ticks,
        }

    @classmethod
    def from_arrays(cls, arrays: dict, meta: dict) -> "FireGrid":
        g = cls(arrays["terrain"], seed=int(meta["seed"]))
        for k in (
            "fuel",
            "moisture",
            "base_moisture",
            "state",
            "heat",
            "water",
            "retardant",
            "smolder_timer",
            "ignited_at",
        ):
            setattr(g, k, np.array(arrays[k]))
        # Older saves gave cleared lines residual fuel; keep them nonflammable
        # under the corrected terrain rules, including any already-burning line.
        cleared = g.terrain == TerrainType.FIREBREAK
        g.fuel[cleared] = 0
        g.state[cleared] = UNBURNED
        g.smolder_timer[cleared] = 0
        g.heat[cleared] = 0
        g.set_elevation(np.array(arrays["elevation"]))
        g.time = float(meta["time"])
        g.set_wind(meta["wind_speed"], meta["wind_bearing"])
        g.weather_enabled = bool(meta.get("weather_enabled", False))
        g.weather_base_speed = float(meta.get("weather_base_speed", g.wind_speed))
        g.weather_base_bearing = float(meta.get("weather_base_bearing", g.wind_bearing))
        g.weather_epoch = float(meta.get("weather_epoch", g.time))
        g.spot_fires = int(meta.get("spot_fires", 0))
        g.rng.bit_generator.state = meta["rng_state"]
        g.embers = [Ember(**e) for e in meta.get("embers", [])]
        g._ticks = int(meta.get("ticks", round(g.time)))
        g._relabel_structures()
        g._tables()
        g.rescan()
        g._last_boxes = _mask_regions(g.heat != 0)
        return g

    # ---- introspection ------------------------------------------------------

    def stats(self) -> FireStats:
        if self._stats_cache is not None and self._stats_cache[0] == self.version:
            return self._stats_cache[1]
        if self._labels is None:
            self._relabel_structures()
        if self._use_box:
            burning = smoldering = 0
            for hy0, hy1, hx0, hx1 in self.regions():
                hot = self.state[hy0:hy1, hx0:hx1]
                burning += int((hot == BURNING).sum())
                smoldering += int((hot == SMOLDER).sum())
            if self._scar is None:
                burned = 0
            else:
                sy0, sy1, sx0, sx1 = self._scar
                scar = self.state[sy0:sy1, sx0:sx1]
                burned = int(((scar == SMOLDER) | (scar == COLD)).sum())
        else:
            st = self.state
            burning = int((st == BURNING).sum())
            smoldering = int((st == SMOLDER).sum())
            burned = int(((st == SMOLDER) | (st == COLD)).sum())
        lost = self.structures_lost()
        frac = float(burned / max(1, self._fuel_cells))
        s = FireStats(
            burning,
            smoldering,
            burned,
            self._structures_total,
            lost,
            frac,
            self.spot_fires,
            len(self.embers),
        )
        self._stats_cache = (self.version, s)
        return s

    def state_hash(self) -> str:
        m = hashlib.sha256()
        for arr in (self.terrain, self.state, self.fuel, self.moisture, self.water, self.retardant):
            m.update(np.ascontiguousarray(arr).tobytes())
        return m.hexdigest()[:16]

    def passable_for(self, cost_row: np.ndarray) -> np.ndarray:
        return np.isfinite(cost_row[self.terrain])
