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
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from .config import FIRE, TERRAIN, TerrainType

UNBURNED, BURNING, SMOLDER, COLD = 0, 1, 2, 3

# 8-neighbour offsets as (dx, dy) and their distance weight.
_OFFSETS: list[tuple[int, int, float]] = [
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, 0.7071), (1, -1, 0.7071), (-1, 1, 0.7071), (-1, -1, 0.7071),
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


class FireGrid:
    """All per-cell state plus the fire-spread step."""

    def __init__(self, terrain: np.ndarray, seed: int = 0, base_moisture: float = 0.15,
                 elevation: np.ndarray | None = None):
        assert terrain.ndim == 2
        self.h, self.w = terrain.shape
        self.terrain = terrain.astype(np.uint8)
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.time = 0.0

        self.fuel = TERRAIN.fuel[self.terrain].copy()
        self.base_moisture = np.full((self.h, self.w), base_moisture, np.float32)
        self.moisture = self.base_moisture.copy()
        self.state = np.zeros((self.h, self.w), np.uint8)
        self.heat = np.zeros((self.h, self.w), np.float32)
        self.water = np.zeros((self.h, self.w), np.float32)
        self.retardant = np.zeros((self.h, self.w), np.float32)
        self.smolder_timer = np.zeros((self.h, self.w), np.float32)
        self.ignited_at = np.full((self.h, self.w), -1.0, np.float32)
        self.elevation = (elevation.astype(np.float32) if elevation is not None
                          else np.zeros((self.h, self.w), np.float32))

        self.wind_speed = 0.0     # m/s
        self.wind_bearing = 0.0   # degrees, blowing toward
        self._structures_total = int((self.terrain == TerrainType.STRUCTURE).sum())
        self._wind_cache: tuple[float, float] | None = None
        self._wind_factors: np.ndarray | None = None

    # ---- wind ---------------------------------------------------------------

    def set_wind(self, speed: float, bearing_deg: float) -> None:
        self.wind_speed = float(max(0.0, speed))
        self.wind_bearing = float(bearing_deg % 360.0)
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

    # ---- external actions ---------------------------------------------------

    def ignite(self, x: int, y: int, radius: int = 0) -> int:
        """Set cells burning. Returns number of cells actually ignited."""
        ys, xs = self._disc(x, y, radius)
        m = (self.state[ys, xs] == UNBURNED) & (self.fuel[ys, xs] > 0.01)
        ys, xs = ys[m], xs[m]
        self.state[ys, xs] = BURNING
        self.ignited_at[ys, xs] = self.time
        return int(len(ys))

    def apply_water(self, x: float, y: float, radius: float, amount: float) -> None:
        ys, xs = self._disc(x, y, radius)
        self.water[ys, xs] = np.minimum(self.water[ys, xs] + amount, 1.5)

    def apply_retardant(self, x: float, y: float, radius: float, amount: float) -> None:
        ys, xs = self._disc(x, y, radius)
        self.retardant[ys, xs] = np.minimum(self.retardant[ys, xs] + amount, 1.0)

    def apply_line(self, x0: float, y0: float, x1: float, y1: float, width: float,
                   agent: str, amount: float) -> None:
        """Lay water or retardant along a segment (aircraft drop)."""
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) * 2) + 1)
        for t in np.linspace(0.0, 1.0, n):
            px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            if agent == "retardant":
                self.apply_retardant(px, py, width / 2, amount / n * 3)
            else:
                self.apply_water(px, py, width / 2, amount / n * 3)

    def set_terrain(self, x: int, y: int, ttype: int, reset_fuel: bool = True) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        self.terrain[y, x] = ttype
        if reset_fuel:
            self.fuel[y, x] = TERRAIN.fuel[ttype]
        if TERRAIN.fuel[ttype] <= 0.02 and self.state[y, x] == BURNING:
            self.state[y, x] = COLD

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
        h, w = self.h, self.w
        T = self.terrain
        st = self.state
        burning = st == BURNING
        smold = st == SMOLDER

        # 1. Heat each cell emits this tick.
        emitted = np.zeros((h, w), np.float32)
        emitted[burning] = TERRAIN.heat_output[T[burning]]
        if FIRE["smolder_ignites_neighbours"] and smold.any():
            frac = float(FIRE["smolder_heat_fraction"])
            st_time = TERRAIN.smolder_time[T[smold]]
            ratio = np.where(st_time > 0, self.smolder_timer[smold] / np.maximum(st_time, 1e-6), 0.0)
            emitted[smold] = TERRAIN.heat_output[T[smold]] * frac * ratio
        self.heat = emitted  # display/debug field

        # 2. Exposure: shifted sums of emitted heat, wind-weighted per direction.
        wf = self._wind_factor_table()
        exposure = np.zeros((h, w), np.float32)
        for i, (dx, dy, _) in enumerate(_OFFSETS):
            # Heat travelling in direction (dx, dy): target[y, x] gets source[y-dy, x-dx].
            src = emitted
            tgt_view = exposure[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)]
            src_view = src[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
            tgt_view += src_view * wf[i]

        # 3. Hazard → probability.
        dryness = np.clip(1.0 - self.moisture, float(FIRE["dryness_floor"]), 1.0)
        suppression = np.clip(self.water, 0.0, 1.0)
        hazard = (exposure * float(FIRE["exposure_scale"])
                  * TERRAIN.ignition_rate[T] * dryness
                  * np.clip(1.0 - float(FIRE["retardant_strength"]) * self.retardant, 0.0, 1.0)
                  * (1.0 - suppression))
        p = 1.0 - np.exp(-hazard * dt)
        roll = self.rng.random((h, w), dtype=np.float32)
        can_ignite = (st == UNBURNED) & (self.fuel > 0.01)
        new_fire = can_ignite & (roll < p)

        # 4. Burning cells consume fuel; run out → smolder.
        if burning.any():
            self.fuel[burning] -= TERRAIN.burn_rate[T[burning]] * dt
            out = burning & (self.fuel <= 0.0)
            self.fuel[out] = 0.0
            st[out] = SMOLDER
            self.smolder_timer[out] = TERRAIN.smolder_time[T[out]]

        # 5. Water extinguishes burning cells (back to UNBURNED, wet, less fuel).
        thr = float(FIRE["water_extinguish_threshold"])
        put_out = (st == BURNING) & (self.water >= thr)
        if put_out.any():
            st[put_out] = UNBURNED
            self.moisture[put_out] = np.minimum(1.0, self.moisture[put_out] + float(FIRE["water_moisture_gain"]))
            self.water[put_out] *= 0.5
            self.fuel[put_out] *= 0.85

        # 6. Smolder cools; water kills smolder quickly.
        smold = st == SMOLDER
        if smold.any():
            self.smolder_timer[smold] -= dt * (1.0 + 6.0 * np.clip(self.water[smold], 0, 1))
            cold = smold & (self.smolder_timer <= 0)
            st[cold] = COLD
            self.smolder_timer[cold] = 0.0

        # 7. Apply ignitions last so they don't burn on the tick they start.
        st[new_fire] = BURNING
        self.ignited_at[new_fire] = self.time

        # 8. Field decay: water evaporates, moisture returns toward base, retardant fades.
        self.moisture = np.maximum(self.moisture, np.minimum(1.0, self.water * 0.4 + self.moisture))
        self.water -= self.water * float(FIRE["water_decay"]) * dt
        self.water[self.water < 1e-3] = 0.0
        self.moisture -= (self.moisture - self.base_moisture) * float(FIRE["moisture_dry_rate"]) * dt
        self.retardant -= self.retardant * float(FIRE["retardant_decay"]) * dt
        self.retardant[self.retardant < 1e-3] = 0.0

        self.time += dt

    # ---- introspection ------------------------------------------------------

    def stats(self) -> FireStats:
        st = self.state
        burned = (st == SMOLDER) | (st == COLD)
        struct = self.terrain == TerrainType.STRUCTURE
        lost = int((struct & (burned | (st == BURNING))).sum())
        fuel_cells = TERRAIN.fuel[self.terrain] > 0.02
        frac = float(burned.sum() / max(1, fuel_cells.sum()))
        return FireStats(int((st == BURNING).sum()), int((st == SMOLDER).sum()),
                         int(burned.sum()), self._structures_total, lost, frac)

    def state_hash(self) -> str:
        m = hashlib.sha256()
        for arr in (self.terrain, self.state, self.fuel, self.moisture, self.water, self.retardant):
            m.update(np.ascontiguousarray(arr).tobytes())
        return m.hexdigest()[:16]

    def passable_for(self, cost_row: np.ndarray) -> np.ndarray:
        return np.isfinite(cost_row[self.terrain])
