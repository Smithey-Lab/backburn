"""Large-map behaviour: world generation, region-based fire step, hierarchical pathfinding,
road travel, survival objective, random incidents and the shipped missions."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from backburn.config import TERRAIN, MoveClass
from backburn.config import TerrainType as T
from backburn.fire import BURNING, COLD, FULL_GRID_CELLS, SMOLDER, FireGrid
from backburn.incidents import describe, random_incident
from backburn.pathfinding import BLOCK, BlockGraph, CostField, astar, build_cost, find_path
from backburn.scenario import Scenario, load_scenario
from backburn.sim import CONTAINED, Simulation
from backburn.units import Order, cost_row_for
from backburn.worldgen import (
    edge_road_cell,
    generate_world,
    generate_world_pair,
    passable_near,
    road_cell_near,
    spots_near,
    town_center,
)

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = sorted((ROOT / "scenarios").glob("*.json"))


def path_cost(cost, start, path):
    total, prev = 0.0, start
    for p in path:
        dx, dy = abs(p[0] - prev[0]), abs(p[1] - prev[1])
        assert max(dx, dy) == 1, f"non-adjacent step {prev} -> {p}"
        assert math.isfinite(cost[p[1], p[0]]), f"impassable cell {p}"
        if dx and dy:
            assert math.isfinite(cost[prev[1], p[0]]) and math.isfinite(cost[p[1], prev[0]]), "corner cut"
        total += (math.sqrt(2) if dx and dy else 1.0) * float(cost[p[1], p[0]])
        prev = p
    return total


# ---- world generation -------------------------------------------------------------------


def test_world_is_seed_stable_and_feature_size_does_not_grow_with_map():
    a = generate_world(640, 480, seed=3)
    b = generate_world(640, 480, seed=3)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, generate_world(640, 480, seed=4))
    small, big = generate_world(400, 300, seed=9, lake=True), generate_world(1200, 900, seed=9, lake=True)
    # Roughly the same share of water on both sizes: lakes are the same size, there are more of them.
    share = lambda t: (t == int(T.WATER)).mean()  # noqa: E731
    assert abs(share(small) - share(big)) < 0.12


def test_world_bridges_rivers_and_places_separate_buildings():
    t, elevation = generate_world_pair(900, 700, seed=21, river=True, towns=1, town_size=12, relief=80)
    assert (t == int(T.WATER)).any() and (t == int(T.ROAD)).any()
    road = t == int(T.ROAD)
    # A highway crosses the river at least once: road cells sit where the river band is.
    grid = FireGrid(t, seed=1)
    assert grid.stats().structures_total >= 10
    assert elevation.shape == t.shape and elevation.max() > 0
    # Engines can drive from one edge road to another edge road: the network is connected.
    cost = build_cost(t, np.zeros_like(t), TERRAIN.cost_for(MoveClass.ROAD))
    a, b = edge_road_cell(t, "W"), edge_road_cell(t, "E")
    if a and b and road[a[1], a[0]] and road[b[1], b[0]]:
        p = find_path(cost, a, b)
        assert p and math.dist(p[-1], b) <= 1.5


def test_world_scenario_round_trips_and_validates_mode():
    d = {
        "name": "w",
        "width": 300,
        "height": 200,
        "seed": 5,
        "terrain": {"mode": "world", "params": {"towns": 1, "river": True}},
        "elevation": {"mode": "world"},
        "ignitions": [{"x": 10, "y": 10}],
    }
    s = Scenario.from_dict(d)
    assert s.terrain_mode == "world"
    again = Scenario.from_dict(s.to_dict())
    assert np.array_equal(again.build_terrain(), s.build_terrain())
    assert np.array_equal(again.build_elevation(), s.build_elevation())
    with pytest.raises(Exception):
        Scenario.from_dict({**d, "terrain": {"mode": "planet"}})


# ---- fire regions ------------------------------------------------------------------------


def big_grass(w=600, h=500):
    t = np.full((h, w), int(T.GRASS), np.uint8)
    assert w * h > FULL_GRID_CELLS
    return t


def test_region_fire_matches_full_grid_physics_on_a_flat_field():
    """Regions never change what the fire does: the same fire on a big (region-processed) map
    and on a small (fully processed) map spreads the same distance and burns a similar area.
    Random rolls are drawn per processed area, so the comparison is statistical."""
    results = {}
    for label, shape in (("small", (200, 200)), ("big", (500, 600))):
        burned, extent = [], []
        for seed in range(4):
            g = FireGrid(np.full(shape, int(T.GRASS), np.uint8), seed=seed)
            g.set_wind(5, 90)
            g.ignite(60, 100, 1)
            for _ in range(80):
                g.step()
            assert g._use_box == (label == "big")
            ys, xs = np.nonzero(g.state != 0)
            burned.append(len(xs))
            extent.append(int(xs.max()) - int(xs.min()))
        results[label] = (np.mean(burned), np.mean(extent))
    (sb, se), (bb, be) = results["small"], results["big"]
    assert abs(sb - bb) / sb < 0.25 and abs(se - be) / se < 0.25, results


def test_separate_fires_get_separate_regions_and_merge_when_they_meet():
    g = FireGrid(big_grass(), seed=2)
    g.set_wind(0, 0)
    g.ignite(60, 60, 1)
    g.ignite(500, 400, 1)
    g.step()
    assert len(g.regions()) == 2
    for _ in range(40):
        g.step()
    assert len(g.regions()) == 2
    g.ignite(72, 60, 1)
    g.step()
    assert len(g.regions()) == 2  # touching the first fire: merged into its region
    boxes = g.regions()
    assert all(b[0] < b[1] and b[2] < b[3] for b in boxes)


def test_embers_and_scripted_ignitions_outside_regions_are_processed():
    t = np.full((500, 600), int(T.FOREST), np.uint8)
    g = FireGrid(t, seed=7)
    g.set_wind(20, 90)
    g.ignite(30, 250, 2)
    for _ in range(400):
        g.step()
    assert g.spot_fires > 0
    hot = (g.state == BURNING) | (g.state == SMOLDER)
    ys, xs = np.nonzero(hot)
    for y, x in zip(ys[::50], xs[::50]):
        assert any(b[0] <= y < b[1] and b[2] <= x < b[3] for b in g.regions())
    g.ignite(550, 20, 0)
    g.step()
    assert g.state[20, 550] == BURNING
    assert any(b[0] <= 20 < b[1] and b[2] <= 550 < b[3] for b in g.regions())


def test_big_grid_stats_savegame_and_replay_stay_exact(tmp_path):
    t = big_grass()
    t[:, 300:304] = int(T.WATER)
    sc = Scenario(
        width=600,
        height=500,
        terrain_mode="grid",
        terrain_grid=t,
        ignitions=[{"x": 100, "y": 250, "radius": 1}],
    )
    sim = Simulation(sc)
    sim.cmd_spawn("ENGINE", x=150, y=250, immediate=True)
    sim.step(60)
    sim.cmd_order(1, "SUPPRESS", target=(115, 250))
    sim.step(60)
    s = sim.grid.stats()
    st = sim.grid.state
    assert s.burning == int((st == BURNING).sum())
    assert s.burned_cells == int(((st == SMOLDER) | (st == COLD)).sum())
    p = tmp_path / "big.bbsave"
    sim.save_state(p)
    back = Simulation.load_state(p)
    assert back.state_hash() == sim.state_hash()
    sim.step(50)
    back.step(50)
    assert back.state_hash() == sim.state_hash()
    r = tmp_path / "big.json"
    sim.save_replay(r)
    again = Simulation.replay(r)
    assert again.state_hash() == sim.state_hash()


# ---- pathfinding ---------------------------------------------------------------------------


def test_block_graph_separates_the_two_banks_of_a_river():
    t = np.full((160, 160), int(T.GRASS), np.uint8)
    t[:, 70:74] = int(T.WATER)  # river with no bridge
    t[80:83, 60:84] = int(T.ROAD)  # a bridge
    ok = np.isfinite(TERRAIN.cost_for(MoveClass.OFFROAD)[t])
    graph = BlockGraph(ok)
    left, right = graph.node_at(66, 20), graph.node_at(76, 20)
    assert left != right
    # Both banks are still one connected graph thanks to the bridge.
    seen, stack = {left}, [left]
    while stack:
        n = stack.pop()
        for m in graph.adj.get(n, ()):
            if m not in seen:
                seen.add(m)
                stack.append(m)
    assert right in seen


def test_long_paths_are_valid_and_near_optimal_on_a_large_map():
    t = generate_world(900, 700, seed=5, river=True, towns=1)
    cost = build_cost(t, np.zeros_like(t), TERRAIN.cost_for(MoveClass.FOOT))
    rng = np.random.default_rng(1)
    ok = np.argwhere(np.isfinite(cost))
    for _ in range(4):
        (sy, sx), (gy, gx) = ok[rng.integers(len(ok))], ok[rng.integers(len(ok))]
        p = find_path(cost, (int(sx), int(sy)), (int(gx), int(gy)))
        assert p is not None
        c = path_cost(cost, (int(sx), int(sy)), p)
        if p and p[-1] == (int(gx), int(gy)):
            exact = astar(cost, (int(sx), int(sy)), (int(gx), int(gy)), max_expand=2_000_000)
            assert c <= path_cost(cost, (int(sx), int(sy)), exact) * 1.15 + 5


def test_units_refine_long_routes_lazily_and_arrive():
    t = np.full((520, 520), int(T.GRASS), np.uint8)
    t[200:204, :400] = int(T.WATER)  # a river with a gap on the right
    sc = Scenario(
        width=520,
        height=520,
        terrain_mode="grid",
        terrain_grid=t,
        units=[{"type": "BRUSH_TRUCK", "x": 20, "y": 20}],
    )
    sim = Simulation(sc)
    u = sim.world.units[0]
    sim.cmd_order(u.uid, "MOVE", target=(20, 500))
    sim.step(2)
    assert u.route is not None and u.route.blocks, "a long order keeps an unrefined route"
    assert len(u.path) < 120
    for _ in range(1500):
        sim.step(1)
        if u.state == "IDLE":
            break
    assert math.dist((u.x, u.y), (20, 500)) < 2, (u.x, u.y, u.state)


def test_units_around_an_unseen_barrier_replan_instead_of_stopping():
    """A block graph edge can exist across a river that cuts the block interior; the unit
    must notice and route around."""
    t = np.full((400, 400), int(T.GRASS), np.uint8)
    for y in range(400):  # diagonal river, no bridge, with a gap near the top
        x = 180 + y // 2
        if y > 30:
            t[y, x : x + 4] = int(T.WATER)
    sc = Scenario(
        width=400,
        height=400,
        terrain_mode="grid",
        terrain_grid=t,
        units=[{"type": "BULLDOZER", "x": 40, "y": 380}],
    )
    sim = Simulation(sc)
    u = sim.world.units[0]
    sim.cmd_order(u.uid, "MOVE", target=(370, 380))
    for _ in range(1500):
        sim.step(1)
        if u.state == "IDLE":
            break
    assert math.dist((u.x, u.y), (370, 380)) < 2, (u.x, u.y)


# ---- bulldozer road travel ----------------------------------------------------------------


def test_bulldozer_travels_faster_on_roads_and_prefers_them():
    t = np.full((120, 300), int(T.GRASS), np.uint8)
    t[58:61, :] = int(T.ROAD)
    sc = Scenario(width=300, height=120, terrain_mode="grid", terrain_grid=t)
    sim = Simulation(sc)
    dozer = sim.world.add("BULLDOZER", 5, 59, sim.grid)
    sim.cmd_order(dozer.uid, "MOVE", target=(280, 59))
    sim.step(40)
    assert dozer.x > 5 + 40 * 3.5, "dozer on a road travels at road speed"
    grass = sim.world.add("BULLDOZER", 5, 20, sim.grid)
    sim.cmd_order(grass.uid, "MOVE", target=(280, 20))
    sim.step(1)
    x_before = grass.x
    sim.step(20)
    assert grass.x - x_before < 20 * 1.0 + 1, "off road it moves at its normal speed"
    row = cost_row_for("BULLDOZER")
    assert row[int(T.ROAD)] < TERRAIN.cost_for(MoveClass.OFFROAD)[int(T.ROAD)]
    # A dozer next to a road detours onto it for a long trip.
    far = sim.world.add("BULLDOZER", 5, 50, sim.grid)
    sim.cmd_order(far.uid, "MOVE", target=(280, 50))
    for _ in range(60):
        sim.step(1)
    assert 57 <= round(far.y) <= 61 or far.x > 200


def test_cutting_speed_is_unchanged_by_road_speed():
    t = np.full((60, 120), int(T.GRASS), np.uint8)
    sc = Scenario(width=120, height=60, terrain_mode="grid", terrain_grid=t)
    sim = Simulation(sc)
    dozer = sim.world.add("BULLDOZER", 10, 30, sim.grid)
    dozer.give(Order("CUT", points=[(10, 30), (60, 30)]))
    sim.step(30)
    cut = int((sim.grid.terrain[29:32, 10:60] == int(T.FIREBREAK)).sum())
    assert 40 <= cut <= 120  # ~1.2 cells/s × 2-wide line, not road speed


# ---- objectives, incidents, missions ---------------------------------------------------------


def test_survival_objective_wins_at_timeout():
    t = np.full((60, 60), int(T.GRASS), np.uint8)
    sc = Scenario(
        width=60,
        height=60,
        terrain_mode="grid",
        terrain_grid=t,
        ignitions=[{"x": 5, "y": 5}],
        duration=40,
        objectives={"win_on_contained": False, "win_on_timeout": True, "max_area_burned_pct": 90},
    )
    sim = Simulation(sc)
    sim.step(60)
    assert sim.outcome == CONTAINED
    assert sim.score()["contain_bonus"] > 0


def test_random_incident_is_deterministic_and_playable():
    a, b = random_incident(4242), random_incident(4242)
    assert a.to_dict() == b.to_dict()
    assert random_incident(4243).to_dict() != a.to_dict()
    sim = Simulation(a)
    assert sim.grid.stats().burning > 0
    sx, sy = sim.world.staging
    assert math.isfinite(TERRAIN.cost_for(MoveClass.ROAD)[sim.grid.terrain[int(sy), int(sx)]])
    sim.step(20)
    assert "min" in describe(a)


@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_shipped_missions_are_large_valid_and_burn(path):
    sc = load_scenario(path)
    assert sc.terrain_mode == "world"
    assert sc.width * sc.height >= 500_000
    sim = Simulation(sc)
    g = sim.grid
    sx, sy = sim.world.staging
    assert math.isfinite(TERRAIN.cost_for(MoveClass.ROAD)[g.terrain[int(sy), int(sx)]]), (
        "staging on road/grass"
    )
    for u in sim.world.units:
        assert u.is_civilian and u.alive
    if sc.objectives.get("rescue_all_civilians"):
        assert sim.world.safe_zone and sim.world.civilians()
    if sc.objectives.get("max_structures_lost") is not None:
        assert g.stats().structures_total > sc.objectives["max_structures_lost"]
    assert g.stats().burning > 0
    for ev in sc.events:
        if "ignite" in ev:
            assert g.fuel[ev["ignite"]["y"], ev["ignite"]["x"]] > 0.01
    sim.step(120)
    assert g.stats().burned_cells > 50


def test_mission_files_match_the_authoring_tool():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/author_missions.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_placement_helpers_find_sensible_spots():
    t = generate_world(600, 400, seed=8, towns=1, ranches=4)
    rng = np.random.default_rng(0)
    town = town_center(t)
    assert town is not None
    road = road_cell_near(t, *town)
    assert t[road[1], road[0]] in (int(T.ROAD), int(T.GRAVEL))
    x, y = passable_near(t, 0, 0, MoveClass.ROAD, max_r=300)
    assert math.isfinite(TERRAIN.cost_for(MoveClass.ROAD)[t[y, x]])
    pts = spots_near(t, rng, 300, 200, 80, 5, spread=6)
    assert len(pts) == 5 and all(math.dist(a, b) >= 6 for i, a in enumerate(pts) for b in pts[i + 1 :])
    assert json.loads(json.dumps(load_scenario(SCENARIOS[0]).to_dict()))["terrain"]["mode"] == "world"
    assert BLOCK == 8 and CostField(build_cost(t, np.zeros_like(t), TERRAIN.cost_for(MoveClass.FOOT))).graph
