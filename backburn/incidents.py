"""Random Incident mode: a complete mission rolled from one seed.

The same seed always produces the same world, fires, staging, town and objectives, so a
random incident can be saved, replayed and retried like an authored one. Difficulty
comes from the roll itself: wind, moisture, how many fires and how much money.
"""

from __future__ import annotations

import math

import numpy as np

from .config import TerrainType as T
from .scenario import Scenario
from .worldgen import (
    edge_road_cell,
    fuel_cell_near,
    generate_world_pair,
    passable_near,
    road_cell_near,
    spots_near,
    town_center,
    upwind_point,
)

SIZES = {"medium": (864, 648), "large": (1152, 864), "huge": (1440, 990)}
RANDOM_NAME = "Random Incident"


def random_incident(seed: int, size: str = "large") -> Scenario:
    rng = np.random.default_rng(seed)
    w, h = SIZES.get(size, SIZES["large"])
    wind_speed = float(rng.integers(3, 14))
    bearing = float(rng.integers(0, 360))
    moisture = float(rng.uniform(0.06, 0.2))
    river = bool(rng.random() < 0.4)
    relief = float(rng.choice([0, 0, 60, 120, 220]))
    params = dict(
        water_level=float(rng.uniform(0.16, 0.3)),
        shrub_level=float(rng.uniform(0.38, 0.5)),
        forest_level=float(rng.uniform(0.48, 0.66)),
        dense_level=float(rng.uniform(0.62, 0.86)),
        feature_cells=float(rng.integers(90, 150)),
        river=river,
        river_width=int(rng.integers(3, 6)),
        highways=int(rng.integers(1, 4)),
        spurs=int(rng.integers(2, 7)),
        towns=int(rng.integers(1, 3)),
        town_size=int(rng.integers(10, 26)),
        town_radius=int(rng.integers(30, 48)),
        ranches=int(rng.integers(4, 14)),
        relief=relief,
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    town = town_center(t) or (w // 2, h // 2)
    # Staging: a road cell on the downwind side of town, so crews arrive between town and fire.
    dx, dy = upwind_point(0, 0, bearing, -1)
    staging_hint = (town[0] + dx * 90, town[1] + dy * 90)
    road = road_cell_near(t, *staging_hint) or edge_road_cell(t, "W") or town
    staging = list(passable_near(t, road[0], road[1]))
    n_fires = int(rng.integers(1, 4))
    distance = float(rng.integers(260, 460))
    ux, uy = upwind_point(town[0], town[1], bearing, distance)
    ux, uy = min(max(ux, 70), w - 70), min(max(uy, 70), h - 70)
    fires = spots_near(t, rng, ux, uy, 90, n_fires, (T.GRASS, T.SHRUB, T.FOREST), 60)
    ignitions = [{"x": x, "y": y, "radius": int(rng.integers(0, 3))} for x, y in fires]
    civilians = []
    safe_zone = None
    objectives = {"win_on_contained": True, "win_on_timeout": True}
    structures = int((t == int(T.STRUCTURE)).sum() > 0)
    if structures:
        objectives["max_structures_lost"] = int(rng.integers(3, 8))
    if rng.random() < 0.35:
        n = int(rng.integers(3, 7))
        civilians = spots_near(t, rng, town[0], town[1], 60, n, (T.GRASS, T.SHRUB, T.FOREST), 8)
        shelter = passable_near(t, staging[0], staging[1])
        safe_zone = [shelter[0], shelter[1], 12]
        objectives.update({"rescue_all_civilians": True, "max_civilians_lost": 0})
    if rng.random() < 0.5:
        objectives["max_area_burned_pct"] = int(rng.integers(25, 45))
    events = []
    at = int(rng.integers(500, 900))
    if rng.random() < 0.6:
        events.append(
            {
                "at": at,
                "wind": {
                    "speed": float(min(16, wind_speed + rng.integers(2, 6))),
                    "bearing": (bearing + rng.integers(-50, 51)) % 360,
                },
                "message": "Wind shift forecast has arrived",
            }
        )
    if rng.random() < 0.5:
        spot = fuel_cell_near(
            t, rng, town[0] + rng.integers(-200, 201), town[1] + rng.integers(-200, 201), 80
        )
        if spot:
            events.append(
                {
                    "at": at + 400,
                    "ignite": {"x": spot[0], "y": spot[1], "radius": 1},
                    "message": "New start reported",
                }
            )
    events.append(
        {"at": at + 700, "budget": int(rng.integers(3, 8)) * 1000, "message": "Additional funding released"}
    )
    if rng.random() < 0.4:
        kind = str(rng.choice(["HELICOPTER", "HOTSHOTS", "WATER_BOMBER", "RETARDANT_BOMBER"]))
        events.append(
            {"at": at + 900, "spawn": {"type": kind}, "message": "Mutual aid dispatched to your incident"}
        )
    events.sort(key=lambda e: e["at"])
    compass = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")[
        int((bearing + 22.5) // 45) % 8
    ]
    fire_word = {1: "One fire", 2: "Two fires", 3: "Three fires"}[n_fires]
    briefing = (
        f"{fire_word} reported {distance / 300:.1f} km upwind of the settlement, wind {wind_speed:.0f} m/s "
        f"toward the {compass}. Fuels are {'dry' if moisture < 0.11 else 'moderate' if moisture < 0.16 else 'damp'}."
        + (" Residents are still in town; get them to the shelter." if civilians else "")
        + " Assemble a fleet within the budget and hold what matters."
    )
    d = {
        "name": f"{RANDOM_NAME} #{seed}",
        "briefing": briefing,
        "seed": int(seed),
        "width": w,
        "height": h,
        "moisture": round(moisture, 3),
        "wind": {"speed": wind_speed, "bearing": bearing, "variable": True},
        "terrain": {"mode": "world", "params": params},
        "airbase": [6, 6],
        "staging": staging,
        "safe_zone": safe_zone,
        "budget": int(rng.integers(14, 30)) * 1000,
        "available_units": None,
        "duration": float(int(rng.integers(50, 91)) * 60),
        "objectives": objectives,
        "events": events,
        "ignitions": ignitions,
        "units": [{"type": "CIVILIAN", "x": x, "y": y} for x, y in civilians],
    }
    if relief:
        d["elevation"] = {"mode": "world"}
    return Scenario.from_dict(d)


def incident_seed(rng: np.random.Generator | None = None) -> int:
    rng = rng or np.random.default_rng()
    return int(rng.integers(1, 100_000))


def describe(scenario: Scenario) -> str:
    """One line for the mission card."""
    o = scenario.objectives
    parts = [f"{scenario.width}×{scenario.height}", f"wind {scenario.wind_speed:.0f} m/s"]
    if o.get("rescue_all_civilians"):
        parts.append(f"{len(scenario.units)} residents")
    if o.get("max_structures_lost") is not None:
        parts.append(f"≤{o['max_structures_lost']} buildings lost")
    if o.get("max_area_burned_pct") is not None:
        parts.append(f"≤{o['max_area_burned_pct']}% burned")
    parts.append(f"{int(math.ceil(scenario.duration / 60))} min")
    return ", ".join(parts)
