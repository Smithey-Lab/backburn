"""Author the shipped missions: generate each world, place staging, fires, hikers and
objectives with the worldgen helpers, and write ``scenarios/<key>.json``.

    python tools/author_missions.py                 # rewrite every mission file
    python tools/author_missions.py --preview DIR   # also render annotated PNG previews
    python tools/author_missions.py --check         # exit 1 if the files on disk differ

The placement rules live here rather than in the JSON so a map can be re-rolled with a
new seed and every marker follows it. Coordinates are baked into the files, which stay
the single source of truth the game loads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backburn.config import MoveClass  # noqa: E402
from backburn.config import TerrainType as T  # noqa: E402
from backburn.scenario import Scenario  # noqa: E402
from backburn.worldgen import (  # noqa: E402
    edge_road_cell,
    fuel_cell_near,
    generate_world_pair,
    passable_near,
    road_cell_near,
    spots_near,
    town_center,
    upwind_point,
)

OUT = ROOT / "scenarios"


def base(name, key, w, h, seed, params, **fields):
    d = {
        "name": name,
        "briefing": "",
        "notes": f"Authored by tools/author_missions.py ({key}). Re-run it after changing the rules.",
        "seed": seed,
        "width": w,
        "height": h,
        "moisture": 0.12,
        "wind": {"speed": 5, "bearing": 90, "variable": True},
        "terrain": {"mode": "world", "params": params},
        "elevation": {"mode": "world"} if params.get("relief") or params.get("canyon") else None,
        "airbase": [3, 3],
        "staging": None,
        "safe_zone": None,
        "budget": 18000,
        "available_units": None,
        "duration": 3600,
        "objectives": {"max_structures_lost": 6, "win_on_contained": True},
        "scoring": {},
        "events": [],
        "ignitions": [],
        "units": [],
    }
    d.update(fields)
    if d["elevation"] is None:
        del d["elevation"]
    if not d["scoring"]:
        del d["scoring"]
    return d


def ground(t, x, y):
    """Nearest cell every ground class can stand on (road, gravel or grass)."""
    return list(passable_near(t, x, y, MoveClass.ROAD, max_r=120))


def fire_points(t, rng, from_xy, bearing, distance, n, radius=1, spread=40, margin=70):
    """Ignitions ``distance`` cells upwind of ``from_xy``, kept inside the map by ``margin``."""
    h, w = t.shape
    ux, uy = upwind_point(from_xy[0], from_xy[1], bearing, distance)
    ux, uy = min(max(ux, margin), w - margin), min(max(uy, margin), h - margin)
    pts = spots_near(t, rng, ux, uy, 70, n, (T.GRASS, T.SHRUB, T.FOREST), spread)
    return [{"x": x, "y": y, "radius": radius} for x, y in pts]


# ---- missions ---------------------------------------------------------------------------------


def prairie_fire():
    w, h, seed = 1152, 864, 7
    params = dict(
        water_level=0.2,
        shrub_level=0.55,
        forest_level=0.68,
        dense_level=0.86,
        feature_cells=110,
        highways=3,
        spurs=4,
        towns=1,
        town_size=16,
        town_radius=36,
        ranches=10,
        town_centers=[[700, 430]],
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    town = town_center(t)
    staging = ground(t, *road_cell_near(t, town[0] - 120, town[1] + 40))
    fires = fire_points(t, rng, town, 90, 420, 2, 1, 60)
    d = base(
        "Prairie Fire",
        "prairie_fire",
        w,
        h,
        seed,
        params,
        moisture=0.12,
        wind={"speed": 6, "bearing": 90, "variable": True},
        staging=staging,
        airbase=[6, 6],
        budget=26000,
        duration=3600,
        objectives={"max_structures_lost": 6, "win_on_contained": True, "win_on_timeout": True},
        ignitions=fires,
        events=[
            {
                "at": 600,
                "wind": {"speed": 9, "bearing": 60},
                "message": "Wind veering north-east and strengthening",
            },
            {"at": 1500, "budget": 4000, "message": "County released additional funds"},
        ],
        briefing=(
            "Grass fire west of the settlement, westerly wind. Hold the highways, protect the houses "
            "and the outlying ranches. Choose your response fleet within the budget."
        ),
    )
    return d


def stranded_hikers():
    w, h, seed = 864, 648, 21
    params = dict(
        water_level=0.22,
        shrub_level=0.4,
        forest_level=0.48,
        dense_level=0.64,
        feature_cells=100,
        highways=1,
        spurs=3,
        towns=0,
        ranches=3,
        relief=260,
    )
    t, e = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    trailhead = edge_road_cell(t, "S")
    trailhead = ground(t, trailhead[0], trailhead[1] - 12)
    staging = ground(t, trailhead[0] + 10, trailhead[1])
    # Hikers sit on the highest forested ground away from the trailhead.
    forest = np.argwhere((t == int(T.FOREST)) | (t == int(T.DENSE_FOREST)))
    far = np.hypot(forest[:, 1] - trailhead[0], forest[:, 0] - trailhead[1]) > 300
    forest = forest[far]
    top = forest[np.argsort(e[forest[:, 0], forest[:, 1]])[-1500:]]
    ridge = (int(np.median(top[:, 1])), int(np.median(top[:, 0])))
    hikers = spots_near(t, rng, ridge[0], ridge[1], 60, 6, (T.FOREST, T.DENSE_FOREST, T.SHRUB), 12)
    fires = fire_points(t, rng, ridge, 20, 150, 1, 1)
    d = base(
        "Stranded Hikers",
        "stranded_hikers",
        w,
        h,
        seed,
        params,
        moisture=0.14,
        wind={"speed": 4, "bearing": 20, "variable": True},
        staging=staging,
        airbase=[6, h - 8],
        safe_zone=[trailhead[0], trailhead[1], 12],
        budget=9000,
        duration=2700,
        available_units=["HELICOPTER", "HOSE_TEAM", "CUT_TEAM", "HOTSHOTS", "SMOKEJUMPERS"],
        objectives={"rescue_all_civilians": True, "max_civilians_lost": 0, "win_on_contained": False},
        ignitions=fires,
        units=[{"type": "CIVILIAN", "x": x, "y": y} for x, y in hikers],
        events=[{"at": 400, "wind": {"speed": 8}, "message": "Afternoon winds picking up"}],
        briefing=(
            "Six hikers are cut off on a forested ridge with a fire climbing toward them. Fly them to the "
            "trailhead (green circle). Ground crews are limited; the helicopter is your rescue asset first "
            "and a water drop second."
        ),
    )
    return d


def refinery_row():
    w, h, seed = 1152, 720, 33
    params = dict(
        water_level=0.18,
        shrub_level=0.55,
        forest_level=0.72,
        dense_level=0.9,
        feature_cells=120,
        highways=2,
        spurs=5,
        towns=2,
        town_size=22,
        town_radius=40,
        ranches=6,
        town_centers=[[560, 360], [820, 300]],
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    town = town_center(t)
    staging = ground(t, *road_cell_near(t, town[0] - 140, town[1] + 80))
    fires = fire_points(t, rng, town, 100, 380, 2, 1, 80)
    spot = fuel_cell_near(t, rng, town[0] + 220, town[1] - 160, 60, (T.GRASS, T.SHRUB))
    d = base(
        "Refinery Row",
        "refinery_row",
        w,
        h,
        seed,
        params,
        moisture=0.1,
        wind={"speed": 7, "bearing": 100, "variable": True},
        staging=staging,
        airbase=[6, 6],
        budget=18000,
        duration=3600,
        available_units=[
            "ENGINE",
            "BRUSH_TRUCK",
            "BULLDOZER",
            "HOSE_TEAM",
            "CUT_TEAM",
            "RETARDANT_BOMBER",
            "WATER_BOMBER",
        ],
        objectives={"max_structures_lost": 5, "win_on_contained": True, "win_on_timeout": True},
        ignitions=fires,
        events=[
            {
                "at": 700,
                "ignite": {"x": spot[0], "y": spot[1], "radius": 1},
                "message": "Spot fire reported north of the highway",
            },
            {"at": 1000, "budget": 5000, "message": "County released additional funds"},
        ],
        briefing=(
            "Two industrial strips along the highway with a grass fire upwind. Structures are the objective and "
            "you lose if more than five burn. Retardant ahead of the fire and engines on the road are the tools "
            "that work here."
        ),
    )
    return d


def wall_of_fire():
    w, h, seed = 1440, 990, 44
    params = dict(
        water_level=0.24,
        shrub_level=0.4,
        forest_level=0.47,
        dense_level=0.6,
        feature_cells=120,
        river=True,
        river_width=5,
        highways=2,
        spurs=4,
        towns=1,
        town_size=12,
        ranches=8,
        relief=90,
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    west = edge_road_cell(t, "W")
    staging = ground(t, *road_cell_near(t, west[0] + 90, west[1]))
    fires = [
        {"x": x, "y": y, "radius": r}
        for (x, y), r in zip(
            spots_near(t, rng, 70, h // 2, 120, 3, (T.GRASS, T.SHRUB, T.FOREST), 90), (2, 1, 1)
        )
    ]
    d = base(
        "Wall of Fire",
        "wall_of_fire",
        w,
        h,
        seed,
        params,
        moisture=0.08,
        wind={"speed": 12, "bearing": 80, "variable": True},
        staging=staging,
        airbase=[6, 6],
        budget=30000,
        duration=5400,
        objectives={"max_area_burned_pct": 30, "win_on_contained": True, "win_on_timeout": True},
        ignitions=fires,
        events=[
            {
                "at": 900,
                "wind": {"bearing": 130},
                "message": "Frontal passage: wind backing to the south-east",
            },
            {
                "at": 1400,
                "spawn": {"type": "P3_BOMBER"},
                "message": "State has assigned a P-3 to your incident",
            },
            {"at": 2400, "budget": 8000, "message": "Federal cost share approved"},
        ],
        briefing=(
            "Extreme wind, heavy timber, embers flying. Direct attack will fail; anchor on the river and the "
            "highway, cut wide, and use aircraft to hold the flanks. Lose if more than 30% of the fuel burns "
            "before the weather breaks in ninety minutes."
        ),
    )
    return d


def canyon_run():
    w, h, seed = 1152, 864, 61
    params = dict(
        water_level=0.14,
        shrub_level=0.42,
        forest_level=0.52,
        dense_level=0.7,
        feature_cells=110,
        river=True,
        river_width=4,
        canyon=True,
        canyon_grade=0.38,
        relief=140,
        highways=1,
        spurs=3,
        towns=1,
        town_size=14,
        ranches=6,
        town_centers=[[860, 180]],
    )
    t, e = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    town = town_center(t)
    # Staging on the valley floor road; the fire starts low in the canyon upwind of the rim town.
    floor = np.argwhere(t == int(T.ROAD))
    low = floor[np.argsort(e[floor[:, 0], floor[:, 1]])[:400]]
    k = int(np.argmin(np.hypot(low[:, 1] - town[0] + 200, low[:, 0] - town[1] - 250)))
    staging = ground(t, int(low[k][1]), int(low[k][0]))
    # The fire starts on the canyon floor south-west of the town and climbs the east wall.
    river = np.argwhere(t == int(T.WATER))
    j = int(np.argmin(np.hypot(river[:, 1] - (town[0] - 260), river[:, 0] - (town[1] + 330))))
    bank = (int(river[j][1]) + 26, int(river[j][0]))
    pts = spots_near(t, rng, bank[0], bank[1], 40, 2, (T.GRASS, T.SHRUB, T.FOREST), 30)
    fires = [{"x": x, "y": y, "radius": r} for (x, y), r in zip(pts, (2, 1))]
    d = base(
        "Canyon Run",
        "canyon_run",
        w,
        h,
        seed,
        params,
        moisture=0.1,
        wind={"speed": 7, "bearing": 35, "variable": True},
        staging=staging,
        airbase=[6, h - 8],
        budget=24000,
        duration=4200,
        objectives={"max_structures_lost": 4, "win_on_contained": True, "win_on_timeout": True},
        ignitions=fires,
        events=[
            {"at": 800, "wind": {"speed": 10}, "message": "Up-canyon winds building through the afternoon"},
            {"at": 1600, "spawn": {"type": "HELICOPTER"}, "message": "Mutual aid: a helicopter is inbound"},
        ],
        briefing=(
            "A river canyon with a settlement on the rim. Fire runs uphill fast, and the valley road is the "
            "only fast way in. Get crews above the fire before it does, and use aircraft on the slopes."
        ),
    )
    return d


def lakeshore_cabins():
    w, h, seed = 1056, 792, 85
    params = dict(
        water_level=0.4,
        shrub_level=0.46,
        forest_level=0.54,
        dense_level=0.7,
        feature_cells=300,
        highways=2,
        spurs=6,
        towns=1,
        town_size=8,
        ranches=4,
        shore_cabins=30,
        relief=50,
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    cabins = town_center(t, radius=140)
    road = road_cell_near(t, cabins[0], cabins[1])
    staging = ground(t, *road)
    fires = fire_points(t, rng, cabins, 120, 360, 2, 1, 70)
    d = base(
        "Lakeshore Cabins",
        "lakeshore_cabins",
        w,
        h,
        seed,
        params,
        moisture=0.11,
        wind={"speed": 6, "bearing": 120, "variable": True},
        staging=staging,
        airbase=[6, 6],
        budget=22000,
        duration=3600,
        available_units=[
            "ENGINE",
            "BRUSH_TRUCK",
            "FIRE_BOAT",
            "HOSE_TEAM",
            "CUT_TEAM",
            "HELICOPTER",
            "WATER_BOMBER",
            "BULLDOZER",
        ],
        objectives={"max_structures_lost": 5, "win_on_contained": True, "win_on_timeout": True},
        ignitions=fires,
        events=[
            {"at": 900, "wind": {"bearing": 160}, "message": "Lake breeze swinging the fire along the shore"},
            {"at": 1500, "budget": 4000, "message": "Homeowners' association funds released"},
        ],
        briefing=(
            "Cabins ring a large lake with a fire moving in from the north-west shore. Water is everywhere: "
            "the fire boat covers the shoreline, helicopters dip from the lake, and engines hold the cabins."
        ),
    )
    return d


def highway_9():
    w, h, seed = 1440, 720, 93
    params = dict(
        water_level=0.16,
        shrub_level=0.5,
        forest_level=0.66,
        dense_level=0.85,
        feature_cells=120,
        highways=1,
        spurs=6,
        towns=2,
        town_size=18,
        town_radius=40,
        ranches=10,
        roads=[[[-2, 400], [260, 380], [520, 360], [790, 330], [1060, 360], [1300, 300], [1442, 290]]],
        town_centers=[[520, 360], [1060, 360]],
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    towns = [(520, 360), (1060, 360)]
    staging = ground(t, *road_cell_near(t, 790, 330))
    fires = fire_points(t, rng, towns[0], 95, 280, 2, 1, 70)
    spots = [fuel_cell_near(t, rng, x, 360 + dy, 50, (T.GRASS, T.SHRUB)) for x, dy in ((760, -40), (900, 50))]
    d = base(
        "Highway 9",
        "highway_9",
        w,
        h,
        seed,
        params,
        moisture=0.1,
        wind={"speed": 8, "bearing": 95, "variable": True},
        staging=staging,
        airbase=[6, 6],
        budget=20000,
        duration=4200,
        available_units=[
            "ENGINE",
            "BRUSH_TRUCK",
            "BULLDOZER",
            "HOSE_TEAM",
            "CUT_TEAM",
            "HOTSHOTS",
            "WATER_BOMBER",
            "RETARDANT_BOMBER",
        ],
        objectives={"max_structures_lost": 6, "win_on_contained": True, "win_on_timeout": True},
        ignitions=fires,
        events=[
            {
                "at": 500,
                "ignite": {"x": spots[0][0], "y": spots[0][1], "radius": 1},
                "message": "Vehicle fire on the shoulder has spread into the grass",
            },
            {
                "at": 1100,
                "ignite": {"x": spots[1][0], "y": spots[1][1], "radius": 1},
                "message": "Second roadside start reported east of town",
            },
            {"at": 1300, "budget": 6000, "message": "State highway funds released"},
        ],
        briefing=(
            "A long highway corridor with two towns. The road is your anchor and your fastest route: engines "
            "and dozers travel it at speed. Expect new roadside starts as traffic keeps moving."
        ),
    )
    return d


def timber_ridge():
    w, h, seed = 1152, 864, 108
    params = dict(
        water_level=0.2,
        shrub_level=0.34,
        forest_level=0.42,
        dense_level=0.58,
        feature_cells=120,
        highways=0,
        spurs=0,
        towns=0,
        ranches=0,
        relief=180,
    )
    t, e = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    meadow = fuel_cell_near(t, rng, 120, h - 120, 90, (T.GRASS,))
    staging = ground(t, *meadow)
    strikes = spots_near(t, rng, w * 0.55, h * 0.45, 260, 3, (T.FOREST, T.DENSE_FOREST), 200)
    d = base(
        "Timber Ridge",
        "timber_ridge",
        w,
        h,
        seed,
        params,
        moisture=0.13,
        wind={"speed": 5, "bearing": 70, "variable": True},
        staging=staging,
        airbase=[6, h - 8],
        budget=26000,
        duration=4800,
        available_units=[
            "HOTSHOTS",
            "SMOKEJUMPERS",
            "CUT_TEAM",
            "HELICOPTER",
            "WATER_BOMBER",
            "RETARDANT_BOMBER",
            "P3_BOMBER",
        ],
        objectives={"max_area_burned_pct": 20, "win_on_contained": True, "win_on_timeout": True},
        ignitions=[{"x": strikes[0][0], "y": strikes[0][1], "radius": 1}],
        events=[
            {
                "at": 500,
                "ignite": {"x": strikes[1][0], "y": strikes[1][1], "radius": 1},
                "message": "Lightning: second strike confirmed burning",
            },
            {
                "at": 1200,
                "ignite": {"x": strikes[2][0], "y": strikes[2][1], "radius": 1},
                "message": "Lightning: third start on the ridge",
            },
            {"at": 1500, "wind": {"speed": 9, "bearing": 50}, "message": "Dry cold front: winds increasing"},
        ],
        briefing=(
            "Backcountry timber with no roads. Lightning has started one fire and more strikes are expected. "
            "Hotshots and smokejumpers walk or fly in; keep the burned area under 20% until the front passes."
        ),
    )
    return d


def ember_storm():
    w, h, seed = 1152, 864, 131
    params = dict(
        water_level=0.17,
        shrub_level=0.44,
        forest_level=0.6,
        dense_level=0.8,
        feature_cells=110,
        highways=2,
        spurs=4,
        towns=1,
        town_size=26,
        town_radius=46,
        ranches=8,
        town_centers=[[760, 470]],
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    town = town_center(t)
    residents = spots_near(t, rng, town[0], town[1], 44, 8, (T.GRASS, T.SHRUB), 9)
    shelter = ground(t, *road_cell_near(t, town[0] + 130, town[1] + 60))
    staging = ground(t, *road_cell_near(t, town[0] - 60, town[1] + 150))
    fires = fire_points(t, rng, town, 80, 420, 2, 2, 90)
    d = base(
        "Ember Storm",
        "ember_storm",
        w,
        h,
        seed,
        params,
        moisture=0.07,
        wind={"speed": 14, "bearing": 80, "variable": True},
        staging=staging,
        airbase=[6, 6],
        safe_zone=[shelter[0], shelter[1], 14],
        budget=30000,
        duration=3000,
        objectives={
            "rescue_all_civilians": True,
            "max_civilians_lost": 0,
            "max_structures_lost": 8,
            "win_on_contained": True,
            "win_on_timeout": True,
        },
        ignitions=fires,
        units=[{"type": "CIVILIAN", "x": x, "y": y} for x, y in residents],
        events=[
            {"at": 700, "wind": {"speed": 17}, "message": "Gusts to 17 m/s: embers crossing every line"},
            {
                "at": 1200,
                "spawn": {"type": "RETARDANT_BOMBER"},
                "message": "Mutual aid: a retardant bomber is inbound",
            },
            {"at": 1900, "wind": {"speed": 9}, "message": "Winds easing"},
        ],
        briefing=(
            "A wind-driven fire is bearing down on a town of residents who cannot outrun it. Evacuate them to "
            "the shelter (green circle) by helicopter or on foot, hold as many homes as you can, and survive "
            "fifty minutes of ember storm."
        ),
    )
    return d


def fire_complex():
    w, h, seed = 1440, 990, 152
    params = dict(
        water_level=0.24,
        shrub_level=0.45,
        forest_level=0.55,
        dense_level=0.72,
        feature_cells=120,
        river=True,
        highways=2,
        spurs=6,
        towns=2,
        town_size=14,
        ranches=10,
        relief=70,
        town_centers=[[430, 300], [1010, 640]],
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    staging = ground(t, *road_cell_near(t, 720, 500))
    f1 = fire_points(t, rng, (430, 300), 60, 320, 1, 1)
    f2 = fire_points(t, rng, (1010, 640), 60, 320, 1, 1)
    f3 = fire_points(t, rng, (720, 120), 60, 80, 1, 2)
    d = base(
        "Fire Complex",
        "fire_complex",
        w,
        h,
        seed,
        params,
        moisture=0.1,
        wind={"speed": 6, "bearing": 60, "variable": True},
        staging=staging,
        airbase=[6, 6],
        budget=22000,
        duration=5400,
        objectives={
            "max_structures_lost": 6,
            "max_area_burned_pct": 30,
            "win_on_contained": True,
            "win_on_timeout": True,
        },
        ignitions=f1 + f2 + f3,
        events=[
            {"at": 600, "budget": 6000, "message": "Complex declared: additional funding released"},
            {"at": 1200, "spawn": {"type": "P3_BOMBER"}, "message": "A P-3 has been assigned to the complex"},
            {"at": 1800, "spawn": {"type": "HOTSHOTS"}, "message": "Interagency hotshot crew arriving"},
            {"at": 2600, "budget": 6000, "message": "Federal cost share approved"},
            {"at": 3000, "wind": {"speed": 10, "bearing": 30}, "message": "Wind shift: north-east push"},
        ],
        briefing=(
            "Three separate fires on one huge map and one budget. Two threaten towns, one is deep in the "
            "timber. Decide what you can hold, what you can catch small, and what you have to let go."
        ),
    )
    return d


def long_watch():
    w, h, seed = 1152, 864, 177
    params = dict(
        water_level=0.22,
        shrub_level=0.47,
        forest_level=0.58,
        dense_level=0.76,
        feature_cells=110,
        highways=2,
        spurs=5,
        towns=1,
        town_size=16,
        ranches=12,
        relief=60,
        town_centers=[[600, 440]],
    )
    t, _ = generate_world_pair(w, h, seed, **params)
    rng = np.random.default_rng(seed)
    town = town_center(t)
    staging = ground(t, *road_cell_near(t, town[0], town[1] + 90))
    first = fire_points(t, rng, town, 90, 360, 1, 1)
    events = []
    # New starts every five to eight minutes, from all around the map, plus wind swings.
    starts = spots_near(t, rng, w / 2, h / 2, 520, 14, (T.GRASS, T.SHRUB, T.FOREST), 150)
    at = 300
    for i, (x, y) in enumerate(starts):
        events.append(
            {
                "at": at,
                "ignite": {"x": x, "y": y, "radius": 1},
                "message": f"New start reported ({i + 2} of the night)",
            }
        )
        at += int(rng.integers(300, 480))
    for k, (time_, speed, bearing) in enumerate(
        ((1200, 8, 140), (2400, 5, 220), (3600, 9, 300), (4800, 6, 40), (6000, 10, 100))
    ):
        events.append({"at": time_, "wind": {"speed": speed, "bearing": bearing}, "message": "Wind shift"})
    for time_ in (900, 1800, 2700, 3600, 4500, 5400, 6300):
        events.append({"at": time_, "budget": 2500, "message": "Overnight funding tranche"})
    events.sort(key=lambda e_: e_["at"])
    d = base(
        "The Long Watch",
        "long_watch",
        w,
        h,
        seed,
        params,
        moisture=0.11,
        wind={"speed": 6, "bearing": 100, "variable": True},
        staging=staging,
        airbase=[6, 6],
        budget=16000,
        duration=7200,
        objectives={
            "max_structures_lost": 8,
            "max_area_burned_pct": 35,
            "win_on_contained": True,
            "win_on_timeout": True,
        },
        ignitions=first,
        events=events,
        briefing=(
            "Survival mode. Starts keep coming all night, the wind keeps turning and the money arrives in "
            "small tranches. Keep the burned area under 35% and the town standing for two hours."
        ),
    )
    return d


MISSIONS = {
    "prairie_fire": prairie_fire,
    "stranded_hikers": stranded_hikers,
    "refinery_row": refinery_row,
    "wall_of_fire": wall_of_fire,
    "canyon_run": canyon_run,
    "lakeshore_cabins": lakeshore_cabins,
    "highway_9": highway_9,
    "timber_ridge": timber_ridge,
    "ember_storm": ember_storm,
    "fire_complex": fire_complex,
    "long_watch": long_watch,
}


def preview(d: dict, path: Path) -> None:
    from PIL import Image, ImageDraw

    from backburn.art import PALETTE

    sc = Scenario.from_dict(d)
    t = sc.build_terrain()
    e = sc.build_elevation()
    rgb = PALETTE[t].astype(np.float32)
    if e is not None and np.ptp(e) > 0:
        en = (e - e.min()) / np.ptp(e)
        rgb = rgb * (0.6 + 0.5 * en[..., None])
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    dr = ImageDraw.Draw(img)

    def mark(x, y, r, color, label=None):
        dr.ellipse([x - r, y - r, x + r, y + r], outline=color, width=3)
        if label:
            dr.text((x + r + 2, y - 6), label, fill=color)

    for ig in d["ignitions"]:
        mark(ig["x"], ig["y"], 10 + 3 * ig.get("radius", 0), (255, 60, 30), "FIRE")
    for ev in d["events"]:
        if "ignite" in ev:
            mark(ev["ignite"]["x"], ev["ignite"]["y"], 8, (255, 160, 40), f"t={ev['at']}")
    if d.get("staging"):
        mark(d["staging"][0], d["staging"][1], 12, (255, 240, 120), "STAGING")
    mark(d["airbase"][0], d["airbase"][1], 8, (255, 255, 255), "AIR")
    if d.get("safe_zone"):
        x, y, r = d["safe_zone"]
        mark(x, y, r, (120, 255, 140), "SAFE")
    for u in d["units"]:
        mark(u["x"], u["y"], 5, (255, 255, 0))
    img = img.resize((img.width // 2, img.height // 2), Image.BILINEAR)
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=Path)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    stale = []
    for key, build in MISSIONS.items():
        if args.only and key not in args.only:
            continue
        d = build()
        Scenario.from_dict(d)  # strict validation
        text = json.dumps(d, indent=2) + "\n"
        path = OUT / f"{key}.json"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(key)
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)} ({d['width']}x{d['height']})")
        if args.preview:
            args.preview.mkdir(parents=True, exist_ok=True)
            preview(d, args.preview / f"{key}.png")
    if stale:
        print("stale mission files:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
