"""Load balance data (terrain.json, units.json) into fast lookup tables.

Everything numeric that a designer might tune lives in the JSON files, not here.
This module only turns those files into NumPy arrays indexed by terrain id so the
simulation can stay fully vectorised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import numpy as np

_DATA_DIR = Path(__file__).parent / "data"


class MoveClass(IntEnum):
    FOOT = 0
    ROAD = 1
    OFFROAD = 2
    AIR = 3
    WATERCRAFT = 4


def _load(name: str) -> dict:
    with open(_DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


_terrain_json = _load("terrain.json")
_units_json = _load("units.json")

# ---- Terrain -----------------------------------------------------------------

_ttypes = _terrain_json["types"]
_by_id = sorted(_ttypes.items(), key=lambda kv: kv[1]["id"])
_ids = [v["id"] for _, v in _by_id]
assert _ids == list(range(len(_ids))), "terrain ids must be contiguous from 0"

TerrainType = IntEnum("TerrainType", {name: v["id"] for name, v in _by_id})  # type: ignore[misc]

N_TERRAIN = len(_by_id)


@dataclass(frozen=True)
class TerrainTables:
    names: list[str]
    color: np.ndarray  # (N,3) uint8
    fuel: np.ndarray  # (N,) float32 starting fuel
    ignition_rate: np.ndarray  # (N,) float32
    burn_rate: np.ndarray  # (N,) float32 fuel consumed per second while burning
    heat_output: np.ndarray  # (N,) float32 heat emitted per second while burning
    smolder_time: np.ndarray  # (N,) float32 seconds of residual heat after fuel is gone
    move_cost: np.ndarray  # (n_moveclass, N) float32, inf = impassable
    is_water_source: np.ndarray  # (N,) bool
    is_objective: np.ndarray  # (N,) bool

    def cost_for(self, mc: MoveClass) -> np.ndarray:
        return self.move_cost[int(mc)]


def _build_terrain() -> TerrainTables:
    n = N_TERRAIN
    names = [k for k, _ in _by_id]
    color = np.zeros((n, 3), np.uint8)
    fuel = np.zeros(n, np.float32)
    ign = np.zeros(n, np.float32)
    burn = np.zeros(n, np.float32)
    heat = np.zeros(n, np.float32)
    smol = np.zeros(n, np.float32)
    move = np.full((len(MoveClass), n), np.inf, np.float32)
    water = np.zeros(n, bool)
    obj = np.zeros(n, bool)
    for name, v in _by_id:
        i = v["id"]
        color[i] = v["color"]
        fuel[i] = v["fuel"]
        ign[i] = v["ignition_rate"]
        burn[i] = v["burn_rate"]
        heat[i] = v["heat_output"]
        smol[i] = v["smolder_time"]
        water[i] = bool(v.get("is_water_source", False))
        obj[i] = bool(v.get("is_objective", False))
        for mc in MoveClass:
            c = v["move"].get(mc.name)
            move[int(mc), i] = np.inf if c is None else float(c)
    return TerrainTables(names, color, fuel, ign, burn, heat, smol, move, water, obj)


TERRAIN = _build_terrain()
FIRE: dict[str, float | bool] = dict(_terrain_json["fire"])

# ---- Units -------------------------------------------------------------------

UNITS: dict[str, dict] = {k: v for k, v in _units_json["types"].items()}
for _name, _u in UNITS.items():
    _u["movement_class"] = MoveClass[_u["movement"]]
