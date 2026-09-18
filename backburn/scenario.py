"""ScenarioSystem: human-readable JSON scenarios (brief §10, §11 step 9/11).

Two terrain modes round-trip through the same file:
  "generate" — seeded procedural map from a handful of parameters
  "grid"     — explicit rows of single-letter terrain codes (editor output)

Letter codes: W water  G grass  S shrub  F forest  D dense forest
              R road   V gravel B building/structure  X firebreak  A sand
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import TerrainType as T

LETTER_TO_T = {"W": T.WATER, "G": T.GRASS, "S": T.SHRUB, "F": T.FOREST, "D": T.DENSE_FOREST,
               "R": T.ROAD, "V": T.GRAVEL, "B": T.STRUCTURE, "X": T.FIREBREAK, "A": T.SAND}
T_TO_LETTER = {int(v): k for k, v in LETTER_TO_T.items()}


@dataclass
class Scenario:
    name: str = "Untitled"
    seed: int = 1
    width: int = 128
    height: int = 128
    moisture: float = 0.15
    wind_speed: float = 4.0
    wind_bearing: float = 90.0
    terrain_mode: str = "generate"
    terrain_params: dict = field(default_factory=dict)
    terrain_grid: np.ndarray | None = None
    ignitions: list[dict] = field(default_factory=list)
    units: list[dict] = field(default_factory=list)
    airbase: tuple[float, float] = (2.0, 2.0)
    duration: float = 1800.0
    notes: str = ""

    # ---- terrain ----------------------------------------------------------

    def build_terrain(self) -> np.ndarray:
        if self.terrain_mode == "grid":
            assert self.terrain_grid is not None, "grid mode needs terrain_grid"
            return self.terrain_grid.astype(np.uint8)
        return generate_terrain(self.width, self.height, self.seed, **self.terrain_params)

    # ---- (de)serialisation ------------------------------------------------

    def to_dict(self) -> dict:
        d = {
            "name": self.name, "seed": self.seed, "width": self.width, "height": self.height,
            "moisture": self.moisture,
            "wind": {"speed": self.wind_speed, "bearing": self.wind_bearing},
            "airbase": list(self.airbase), "duration": self.duration,
            "ignitions": self.ignitions, "units": self.units, "notes": self.notes,
        }
        if self.terrain_mode == "grid" and self.terrain_grid is not None:
            rows = ["".join(T_TO_LETTER[int(v)] for v in row) for row in self.terrain_grid]
            d["terrain"] = {"mode": "grid", "rows": rows}
        else:
            d["terrain"] = {"mode": "generate", "params": self.terrain_params}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        s = cls(
            name=d.get("name", "Untitled"), seed=int(d.get("seed", 1)),
            width=int(d.get("width", 128)), height=int(d.get("height", 128)),
            moisture=float(d.get("moisture", 0.15)),
            wind_speed=float(d.get("wind", {}).get("speed", 4.0)),
            wind_bearing=float(d.get("wind", {}).get("bearing", 90.0)),
            ignitions=list(d.get("ignitions", [])), units=list(d.get("units", [])),
            airbase=tuple(d.get("airbase", [2.0, 2.0])), duration=float(d.get("duration", 1800.0)),
            notes=d.get("notes", ""),
        )
        t = d.get("terrain", {"mode": "generate", "params": {}})
        s.terrain_mode = t.get("mode", "generate")
        if s.terrain_mode == "grid":
            rows = t["rows"]
            s.height, s.width = len(rows), len(rows[0])
            g = np.zeros((s.height, s.width), np.uint8)
            for y, row in enumerate(rows):
                for x, ch in enumerate(row):
                    g[y, x] = int(LETTER_TO_T[ch])
            s.terrain_grid = g
        else:
            s.terrain_params = dict(t.get("params", {}))
        return s


def load_scenario(path: str | Path) -> Scenario:
    with open(path, "r", encoding="utf-8") as f:
        return Scenario.from_dict(json.load(f))


def save_scenario(s: Scenario, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, indent=2)


# ---- procedural map ---------------------------------------------------------

def _value_noise(rng: np.random.Generator, h: int, w: int, octaves: int = 4, base: int = 8) -> np.ndarray:
    out = np.zeros((h, w), np.float32)
    amp = 1.0
    total = 0.0
    for o in range(octaves):
        gh, gw = base * (2 ** o) + 1, base * (2 ** o) + 1
        g = rng.random((gh, gw), dtype=np.float32)
        ys = np.linspace(0, gh - 1, h)
        xs = np.linspace(0, gw - 1, w)
        y0 = np.floor(ys).astype(int); x0 = np.floor(xs).astype(int)
        y1 = np.minimum(y0 + 1, gh - 1); x1 = np.minimum(x0 + 1, gw - 1)
        fy = (ys - y0)[:, None]; fx = (xs - x0)[None, :]
        fy = fy * fy * (3 - 2 * fy); fx = fx * fx * (3 - 2 * fx)
        a = g[y0][:, x0]; b = g[y0][:, x1]; c = g[y1][:, x0]; d = g[y1][:, x1]
        layer = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
        out += layer * amp
        total += amp
        amp *= 0.5
    return out / total


def generate_terrain(w: int, h: int, seed: int, water_level: float = 0.28, forest_level: float = 0.52,
                     dense_level: float = 0.68, shrub_level: float = 0.45, roads: int = 2,
                     structures: int = 14, lake: bool = True, **_: object) -> np.ndarray:
    """Seeded procedural terrain. Every parameter is a design dial."""
    rng = np.random.default_rng(seed)
    n = _value_noise(rng, h, w, octaves=4, base=6)
    veg = _value_noise(rng, h, w, octaves=3, base=5)
    t = np.full((h, w), int(T.GRASS), np.uint8)

    # Vegetation bands from a second noise field.
    t[veg > shrub_level] = int(T.SHRUB)
    t[veg > forest_level] = int(T.FOREST)
    t[veg > dense_level] = int(T.DENSE_FOREST)

    # Water from the elevation-ish field.
    if lake:
        water = n < water_level
        t[water] = int(T.WATER)
        # Sand fringe.
        fringe = _dilate(water) & ~water
        t[fringe] = int(T.SAND)

    # Roads: straight lines with a bend, drawn over everything but water.
    for _ in range(roads):
        if rng.random() < 0.5:
            y = int(rng.integers(h // 5, 4 * h // 5)); x_bend = int(rng.integers(w // 4, 3 * w // 4))
            y2 = int(np.clip(y + rng.integers(-h // 4, h // 4), 1, h - 2))
            _draw_road(t, [(0, y), (x_bend, y), (x_bend, y2), (w - 1, y2)])
        else:
            x = int(rng.integers(w // 5, 4 * w // 5)); y_bend = int(rng.integers(h // 4, 3 * h // 4))
            x2 = int(np.clip(x + rng.integers(-w // 4, w // 4), 1, w - 2))
            _draw_road(t, [(x, 0), (x, y_bend), (x2, y_bend), (x2, h - 1)])

    # Structures cluster near roads.
    road_cells = np.argwhere(t == int(T.ROAD))
    placed = 0
    tries = 0
    while placed < structures and tries < structures * 40 and len(road_cells):
        tries += 1
        ry, rx = road_cells[rng.integers(len(road_cells))]
        ox, oy = int(rng.integers(-4, 5)), int(rng.integers(-4, 5))
        x, y = int(rx + ox), int(ry + oy)
        if 1 <= x < w - 2 and 1 <= y < h - 2 and t[y, x] not in (int(T.WATER), int(T.ROAD), int(T.STRUCTURE), int(T.SAND)):
            sw, sh = int(rng.integers(1, 3)), int(rng.integers(1, 3))
            blk = t[y:y + sh, x:x + sw]
            if not np.isin(blk, [int(T.WATER), int(T.ROAD)]).any():
                blk[:] = int(T.STRUCTURE)
                placed += 1
    return t


def _dilate(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:, :] |= m[:-1, :]; out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]; out[:, :-1] |= m[:, 1:]
    return out


def _draw_road(t: np.ndarray, pts: list[tuple[int, int]]) -> None:
    h, w = t.shape
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(abs(x1 - x0), abs(y1 - y0)) + 1
        for i in range(n):
            x = int(round(x0 + (x1 - x0) * i / max(1, n - 1)))
            y = int(round(y0 + (y1 - y0) * i / max(1, n - 1)))
            if 0 <= x < w and 0 <= y < h and t[y, x] != int(T.WATER):
                t[y, x] = int(T.ROAD)
