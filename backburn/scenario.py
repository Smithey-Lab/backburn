"""ScenarioSystem: human-readable JSON scenarios (brief §10, §11 steps 6/9/11).

See docs/SCENARIO_FORMAT.md for the full field reference and schema/scenario.schema.json
for the machine-readable schema. Two terrain modes round-trip through the same file:

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

from .config import UNITS
from .config import TerrainType as T

LETTER_TO_T = {
    "W": T.WATER,
    "G": T.GRASS,
    "S": T.SHRUB,
    "F": T.FOREST,
    "D": T.DENSE_FOREST,
    "R": T.ROAD,
    "V": T.GRAVEL,
    "B": T.STRUCTURE,
    "X": T.FIREBREAK,
    "A": T.SAND,
}
T_TO_LETTER = {int(v): k for k, v in LETTER_TO_T.items()}

DEFAULT_OBJECTIVES = {
    "max_structures_lost": None,  # int → lose when exceeded
    "max_civilians_lost": None,  # int → lose when exceeded
    "max_area_burned_pct": None,  # float → lose when exceeded
    "rescue_all_civilians": False,  # bool → required to win on timeout
    "win_on_contained": True,  # bool → win the moment the fire is out
}
DEFAULT_SCORING = {
    "structure_saved": 500,
    "civilian_rescued": 1000,
    "civilian_lost": -1500,
    "acre_saved": 1.0,  # per unburned fuel cell
    "budget_remaining": 0.1,  # per currency unit left
    "contain_bonus": 2000,
    "time_bonus_per_second": 1.0,  # × seconds remaining when contained
}
EVENT_KINDS = {"wind", "ignite", "message", "spawn", "budget"}


class ScenarioError(ValueError):
    """Raised for a malformed scenario file, with a message naming the field."""


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
    elevation: dict | None = (
        None  # None | {"mode": "generate", "relief": m} | {"mode": "grid", "rows": [[...]]}
    )
    ignitions: list = field(default_factory=list)
    units: list = field(default_factory=list)
    airbase: tuple = (2.0, 2.0)
    staging: tuple | None = None
    safe_zone: tuple | None = None  # (x, y, radius)
    budget: float | None = None  # None = unlimited (Sandbox mode)
    available_units: list | None = None  # None = every non-civilian type can be purchased
    duration: float = 1800.0
    objectives: dict = field(default_factory=lambda: dict(DEFAULT_OBJECTIVES))
    scoring: dict = field(default_factory=lambda: dict(DEFAULT_SCORING))
    events: list = field(default_factory=list)  # [{"at": seconds, "<kind>": ...}]
    briefing: str = ""
    notes: str = ""

    # ---- terrain ----------------------------------------------------------

    def build_terrain(self) -> np.ndarray:
        if self.terrain_mode == "grid":
            assert self.terrain_grid is not None, "grid mode needs terrain_grid"
            return self.terrain_grid.astype(np.uint8)
        return generate_terrain(self.width, self.height, self.seed, **self.terrain_params)

    def build_elevation(self) -> np.ndarray | None:
        e = self.elevation
        if not e:
            return None
        if e.get("mode") == "grid":
            return np.asarray(e["rows"], dtype=np.float32)
        return generate_elevation(
            self.width,
            self.height,
            int(e.get("seed", self.seed + 1000)),
            relief=float(e.get("relief", 60.0)),
            octaves=int(e.get("octaves", 3)),
        )

    # ---- (de)serialisation ------------------------------------------------

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "briefing": self.briefing,
            "notes": self.notes,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "moisture": self.moisture,
            "wind": {"speed": self.wind_speed, "bearing": self.wind_bearing},
            "airbase": list(self.airbase),
            "staging": list(self.staging) if self.staging else None,
            "safe_zone": list(self.safe_zone) if self.safe_zone else None,
            "budget": self.budget,
            "available_units": self.available_units,
            "duration": self.duration,
            "objectives": self.objectives,
            "scoring": self.scoring,
            "events": self.events,
            "ignitions": self.ignitions,
            "units": self.units,
        }
        if self.terrain_mode == "grid" and self.terrain_grid is not None:
            rows = ["".join(T_TO_LETTER[int(v)] for v in row) for row in self.terrain_grid]
            d["terrain"] = {"mode": "grid", "rows": rows}
        else:
            d["terrain"] = {"mode": "generate", "params": self.terrain_params}
        if self.elevation:
            d["elevation"] = self.elevation
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        validate(d)
        wind = d.get("wind", {})
        s = cls(
            name=d.get("name", "Untitled"),
            briefing=d.get("briefing", ""),
            notes=d.get("notes", ""),
            seed=int(d.get("seed", 1)),
            width=int(d.get("width", 128)),
            height=int(d.get("height", 128)),
            moisture=float(d.get("moisture", 0.15)),
            wind_speed=float(wind.get("speed", 4.0)),
            wind_bearing=float(wind.get("bearing", 90.0)),
            ignitions=list(d.get("ignitions", [])),
            units=list(d.get("units", [])),
            airbase=tuple(d.get("airbase", [2.0, 2.0])),
            staging=tuple(d["staging"]) if d.get("staging") else None,
            safe_zone=tuple(d["safe_zone"]) if d.get("safe_zone") else None,
            budget=d.get("budget"),
            available_units=d.get("available_units"),
            duration=float(d.get("duration", 1800.0)),
            objectives={**DEFAULT_OBJECTIVES, **d.get("objectives", {})},
            scoring={**DEFAULT_SCORING, **d.get("scoring", {})},
            events=sorted(list(d.get("events", [])), key=lambda e: e["at"]),
            elevation=d.get("elevation"),
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


def validate(d: dict) -> None:
    """Raise ScenarioError with a precise message for anything that would break the sim."""

    def num(key, lo=None, hi=None, parent=d, label=None):
        label = label or key
        if key not in parent or parent[key] is None:
            return
        v = parent[key]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ScenarioError(f"{label} must be a number, got {v!r}")
        if lo is not None and v < lo:
            raise ScenarioError(f"{label} must be ≥ {lo}, got {v}")
        if hi is not None and v > hi:
            raise ScenarioError(f"{label} must be ≤ {hi}, got {v}")

    if not isinstance(d, dict):
        raise ScenarioError("scenario must be a JSON object")
    num("width", 8, 512)
    num("height", 8, 512)
    num("moisture", 0, 1)
    num("duration", 1)
    num("seed")
    num("budget", 0)
    wind = d.get("wind", {})
    if not isinstance(wind, dict):
        raise ScenarioError("wind must be an object {speed, bearing}")
    num("speed", 0, 40, wind, "wind.speed")
    num("bearing", parent=wind, label="wind.bearing")

    t = d.get("terrain", {})
    mode = t.get("mode", "generate")
    if mode not in ("generate", "grid"):
        raise ScenarioError(f"terrain.mode must be 'generate' or 'grid', got {mode!r}")
    if mode == "grid":
        rows = t.get("rows")
        if not rows or not all(isinstance(r, str) for r in rows):
            raise ScenarioError("terrain.rows must be a non-empty list of strings")
        w = len(rows[0])
        for y, r in enumerate(rows):
            if len(r) != w:
                raise ScenarioError(f"terrain.rows[{y}] has length {len(r)}, expected {w}")
            bad = set(r) - set(LETTER_TO_T)
            if bad:
                raise ScenarioError(
                    f"terrain.rows[{y}] has unknown letters {sorted(bad)}; valid: {''.join(LETTER_TO_T)}"
                )
        width, height = w, len(rows)
    else:
        width, height = int(d.get("width", 128)), int(d.get("height", 128))

    def point(v, label, n=2):
        if not (isinstance(v, (list, tuple)) and len(v) == n and all(isinstance(c, (int, float)) for c in v)):
            raise ScenarioError(f"{label} must be a list of {n} numbers, got {v!r}")
        if not (0 <= v[0] < width and 0 <= v[1] < height):
            raise ScenarioError(f"{label} {v[:2]} is outside the {width}×{height} map")

    if "airbase" in d:
        point(d["airbase"], "airbase")
    if d.get("staging"):
        point(d["staging"], "staging")
    if d.get("safe_zone"):
        point(d["safe_zone"], "safe_zone", 3)
        if d["safe_zone"][2] <= 0:
            raise ScenarioError("safe_zone radius must be > 0")

    for i, ig in enumerate(d.get("ignitions", [])):
        if not isinstance(ig, dict) or "x" not in ig or "y" not in ig:
            raise ScenarioError(f"ignitions[{i}] needs x and y")
        point([ig["x"], ig["y"]], f"ignitions[{i}]")
        num("radius", 0, 32, ig, f"ignitions[{i}].radius")

    for i, u in enumerate(d.get("units", [])):
        if not isinstance(u, dict) or u.get("type") not in UNITS:
            raise ScenarioError(
                f"units[{i}].type must be one of {sorted(UNITS)}, got {u.get('type') if isinstance(u, dict) else u!r}"
            )
        if "x" not in u or "y" not in u:
            raise ScenarioError(f"units[{i}] needs x and y")
        point([u["x"], u["y"]], f"units[{i}]")

    au = d.get("available_units")
    if au is not None:
        if not isinstance(au, list) or any(u not in UNITS for u in au):
            raise ScenarioError(f"available_units must be a list of unit types from {sorted(UNITS)}")

    for i, ev in enumerate(d.get("events", [])):
        if not isinstance(ev, dict) or "at" not in ev:
            raise ScenarioError(f"events[{i}] needs an 'at' time in seconds")
        num("at", 0, parent=ev, label=f"events[{i}].at")
        kinds = EVENT_KINDS & set(ev)
        if not kinds:
            raise ScenarioError(f"events[{i}] must contain one of {sorted(EVENT_KINDS)}")
        if "spawn" in ev and (not isinstance(ev["spawn"], dict) or ev["spawn"].get("type") not in UNITS):
            raise ScenarioError(f"events[{i}].spawn.type must be a known unit type")

    obj = d.get("objectives", {})
    for k in obj:
        if k not in DEFAULT_OBJECTIVES:
            raise ScenarioError(
                f"objectives.{k} is not a known objective; valid: {sorted(DEFAULT_OBJECTIVES)}"
            )
    for k in d.get("scoring", {}):
        if k not in DEFAULT_SCORING:
            raise ScenarioError(f"scoring.{k} is not a known weight; valid: {sorted(DEFAULT_SCORING)}")

    e = d.get("elevation")
    if e:
        if e.get("mode", "generate") == "grid":
            rows = e.get("rows")
            if not rows or len(rows) != height or any(len(r) != width for r in rows):
                raise ScenarioError("elevation.rows must be a height×width array of numbers")


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    if not path.exists():
        raise ScenarioError(f"scenario file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as ex:
            raise ScenarioError(f"{path.name}: invalid JSON at line {ex.lineno}: {ex.msg}") from ex
    return Scenario.from_dict(d)


def save_scenario(s: Scenario, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, indent=2)


# ---- procedural map ---------------------------------------------------------


def _value_noise(rng: np.random.Generator, h: int, w: int, octaves: int = 4, base: int = 8) -> np.ndarray:
    out = np.zeros((h, w), np.float32)
    amp = 1.0
    total = 0.0
    for o in range(octaves):
        gh, gw = base * (2**o) + 1, base * (2**o) + 1
        g = rng.random((gh, gw), dtype=np.float32)
        ys = np.linspace(0, gh - 1, h)
        xs = np.linspace(0, gw - 1, w)
        y0 = np.floor(ys).astype(int)
        x0 = np.floor(xs).astype(int)
        y1 = np.minimum(y0 + 1, gh - 1)
        x1 = np.minimum(x0 + 1, gw - 1)
        fy = (ys - y0)[:, None]
        fx = (xs - x0)[None, :]
        fy = fy * fy * (3 - 2 * fy)
        fx = fx * fx * (3 - 2 * fx)
        a = g[y0][:, x0]
        b = g[y0][:, x1]
        c = g[y1][:, x0]
        d = g[y1][:, x1]
        layer = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
        out += layer * amp
        total += amp
        amp *= 0.5
    return out / total


def generate_elevation(w: int, h: int, seed: int, relief: float = 60.0, octaves: int = 3) -> np.ndarray:
    """Rolling hills: value noise scaled to `relief` metres of total rise."""
    rng = np.random.default_rng(seed)
    n = _value_noise(rng, h, w, octaves=octaves, base=4)
    n = (n - n.min()) / max(1e-6, float(n.max() - n.min()))
    return (n * relief).astype(np.float32)


def generate_terrain(
    w: int,
    h: int,
    seed: int,
    water_level: float = 0.28,
    forest_level: float = 0.52,
    dense_level: float = 0.68,
    shrub_level: float = 0.45,
    roads: int = 2,
    structures: int = 14,
    lake: bool = True,
    **_: object,
) -> np.ndarray:
    """Seeded procedural terrain. Every parameter is a design dial."""
    rng = np.random.default_rng(seed)
    n = _value_noise(rng, h, w, octaves=4, base=6)
    veg = _value_noise(rng, h, w, octaves=3, base=5)
    t = np.full((h, w), int(T.GRASS), np.uint8)

    t[veg > shrub_level] = int(T.SHRUB)
    t[veg > forest_level] = int(T.FOREST)
    t[veg > dense_level] = int(T.DENSE_FOREST)

    if lake:
        water = n < water_level
        t[water] = int(T.WATER)
        fringe = _dilate(water) & ~water
        t[fringe] = int(T.SAND)

    for _ in range(roads):
        if rng.random() < 0.5:
            y = int(rng.integers(h // 5, 4 * h // 5))
            x_bend = int(rng.integers(w // 4, 3 * w // 4))
            y2 = int(np.clip(y + rng.integers(-h // 4, h // 4), 1, h - 2))
            _draw_road(t, [(0, y), (x_bend, y), (x_bend, y2), (w - 1, y2)])
        else:
            x = int(rng.integers(w // 5, 4 * w // 5))
            y_bend = int(rng.integers(h // 4, 3 * h // 4))
            x2 = int(np.clip(x + rng.integers(-w // 4, w // 4), 1, w - 2))
            _draw_road(t, [(x, 0), (x, y_bend), (x2, y_bend), (x2, h - 1)])

    road_cells = np.argwhere(t == int(T.ROAD))
    placed = 0
    tries = 0
    while placed < structures and tries < structures * 40 and len(road_cells):
        tries += 1
        ry, rx = road_cells[rng.integers(len(road_cells))]
        ox, oy = int(rng.integers(-4, 5)), int(rng.integers(-4, 5))
        x, y = int(rx + ox), int(ry + oy)
        if (
            1 <= x < w - 2
            and 1 <= y < h - 2
            and t[y, x] not in (int(T.WATER), int(T.ROAD), int(T.STRUCTURE), int(T.SAND))
        ):
            sw, sh = int(rng.integers(1, 3)), int(rng.integers(1, 3))
            blk = t[y : y + sh, x : x + sw]
            if not np.isin(blk, [int(T.WATER), int(T.ROAD)]).any():
                blk[:] = int(T.STRUCTURE)
                placed += 1
    return t


def _dilate(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def _draw_road(t: np.ndarray, pts: list) -> None:
    h, w = t.shape
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(abs(x1 - x0), abs(y1 - y0)) + 1
        for i in range(n):
            x = int(round(x0 + (x1 - x0) * i / max(1, n - 1)))
            y = int(round(y0 + (y1 - y0) * i / max(1, n - 1)))
            if 0 <= x < w and 0 <= y < h and t[y, x] != int(T.WATER):
                t[y, x] = int(T.ROAD)
