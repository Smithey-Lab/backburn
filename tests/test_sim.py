"""Behavioural tests for SimulationCore. Run: pytest -q"""

from pathlib import Path

import numpy as np

from backburn import Scenario, Simulation, load_scenario
from backburn.config import FIRE, TERRAIN, MoveClass
from backburn.config import TerrainType as T
from backburn.fire import BURNING, COLD, SMOLDER, UNBURNED, FireGrid
from backburn.pathfinding import build_cost, find_path
from backburn.scenario import generate_terrain

FIXTURES = Path(__file__).resolve().parent / "fixtures"  # v0.6 prototype-scale scenarios

ROOT = Path(__file__).resolve().parents[1]


def flat(tt, h=96, w=96):
    return np.full((h, w), int(tt), np.uint8)


def burn_extent(grid: FireGrid, ox: int):
    ys, xs = np.nonzero(grid.state > 0)
    return int(xs.max() - ox), int(ox - xs.min())


# ---- determinism --------------------------------------------------------------


def test_same_seed_same_hash():
    a = FireGrid(flat(T.GRASS), seed=42)
    b = FireGrid(flat(T.GRASS), seed=42)
    for g in (a, b):
        g.set_wind(5, 90)
        g.ignite(20, 48, 1)
        for _ in range(150):
            g.step()
    assert a.state_hash() == b.state_hash()


def test_different_seed_different_hash():
    a = FireGrid(flat(T.GRASS), seed=1)
    b = FireGrid(flat(T.GRASS), seed=2)
    for g in (a, b):
        g.ignite(20, 48, 1)
        for _ in range(100):
            g.step()
    assert a.state_hash() != b.state_hash()


def test_replay_reproduces_final_hash(tmp_path):
    sim = Simulation(load_scenario(FIXTURES / "prairie_fire.json"))
    uids = {u.utype: u.uid for u in sim.world.units}
    sim.step(5)
    sim.cmd_order(uids["CUT_TEAM"], "CUT", points=[(34, 44), (34, 62)])
    sim.cmd_order(uids["HELICOPTER"], "DROP", points=[(20, 36), (20, 48)])
    sim.step(100)
    sim.cmd_wind(9, 60)
    sim.step(150)
    p = tmp_path / "r.json"
    sim.save_replay(p)
    again = Simulation.replay(p)
    assert again.tick == sim.tick
    assert again.state_hash() == sim.state_hash()


# ---- fire behaviour ----------------------------------------------------------


def test_wind_biases_spread_downwind():
    g = FireGrid(flat(T.GRASS), seed=3)
    g.set_wind(6, 90)
    g.ignite(30, 48, 1)
    for _ in range(120):
        g.step()
    down, up = burn_extent(g, 30)
    assert down > 2 * up, (down, up)


def test_no_wind_is_roughly_isotropic():
    g = FireGrid(flat(T.GRASS), seed=3)
    g.set_wind(0, 0)
    g.ignite(48, 48, 1)
    for _ in range(100):
        g.step()
    ys, xs = np.nonzero(g.state > 0)
    ex = (xs.max() - 48, 48 - xs.min())
    ey = (ys.max() - 48, 48 - ys.min())
    assert max(ex + ey) < 1.6 * min(ex + ey)


def test_fuel_ordering_grass_shrub_forest_dense():
    """Area burned after a fixed time must strictly decrease through the fuel ladder."""
    res = {}
    for tt in (T.GRASS, T.SHRUB, T.FOREST, T.DENSE_FOREST):
        g = FireGrid(flat(tt, 128, 128), seed=3)
        g.set_wind(4, 90)
        g.ignite(20, 64, 1)
        for _ in range(120):
            g.step()
        res[tt] = int((g.state > 0).sum())
    assert res[T.GRASS] > res[T.SHRUB] > res[T.FOREST] > res[T.DENSE_FOREST], res
    assert res[T.GRASS] > 4 * res[T.DENSE_FOREST], res


def test_firebreak_holds_without_spotting(monkeypatch):
    monkeypatch.setitem(FIRE, "spot_rate", 0)
    t = flat(T.GRASS)
    t[:, 50] = int(T.FIREBREAK)
    g = FireGrid(t, seed=3)
    g.set_wind(12, 90)
    g.ignite(20, 48, 1)
    for _ in range(400):
        g.step()
    assert not (g.state[:, 51:] > 0).any()


def test_road_and_water_do_not_burn():
    t = flat(T.GRASS)
    t[:, 40:43] = int(T.ROAD)
    t[:, 60:70] = int(T.WATER)
    g = FireGrid(t, seed=3)
    g.ignite(20, 48, 2)
    for _ in range(300):
        g.step()
    assert (g.state[:, 40:43] == UNBURNED).all()
    assert (g.state[:, 60:70] == UNBURNED).all()


def test_water_extinguishes_and_leaves_fuel():
    g = FireGrid(flat(T.GRASS, 64, 64), seed=3)
    g.ignite(32, 32, 2)
    for _ in range(15):
        g.step()
    before = g.stats().burning
    g.apply_water(32, 32, 6, 1.0)
    g.step()
    after = g.stats().burning
    assert after < before * 0.4
    # Extinguished cells are UNBURNED again with fuel left → can rekindle later.
    ys, xs = g._disc(32, 32, 6)
    assert (g.fuel[ys, xs] > 0).any()


def test_retardant_blocks_ignition():
    t = flat(T.GRASS, 64, 128)
    g = FireGrid(t, seed=3)
    g.set_wind(6, 90)
    g.ignite(20, 32, 1)
    g.apply_line(50, 0, 50, 63, 3, "retardant", 30.0)
    for _ in range(400):
        g.step()
    assert not (g.state[:, 54:] > 0).any()


def test_burn_lifecycle():
    g = FireGrid(flat(T.GRASS, 16, 16), seed=1)
    g.ignite(8, 8)
    seen = set()
    for _ in range(80):
        g.step()
        seen.add(int(g.state[8, 8]))
    assert {BURNING, SMOLDER, COLD} <= seen
    assert g.state[8, 8] == COLD


def test_structures_counted_as_buildings_not_cells():
    t = flat(T.GRASS, 32, 32)
    t[10:12, 10:12] = int(T.STRUCTURE)  # one 2×2 house
    t[20:23, 20:21] = int(T.STRUCTURE)  # one 3×1 house
    t[:, 16] = int(T.WATER)  # canal keeps the fire on the left half
    g = FireGrid(t, seed=1)
    assert g.stats().structures_total == 2
    g.ignite(5, 10, 1)
    for _ in range(250):
        g.step()
    s = g.stats()
    assert s.structures_lost == 1, s


# ---- pathfinding -------------------------------------------------------------


def test_engine_cannot_enter_forest_but_brush_truck_can():
    t = flat(T.GRASS, 32, 32)
    t[:, 15:18] = int(T.FOREST)
    st = np.zeros_like(t)
    road = build_cost(t, st, TERRAIN.cost_for(MoveClass.ROAD))
    off = build_cost(t, st, TERRAIN.cost_for(MoveClass.OFFROAD))
    p_road = find_path(road, (5, 16), (28, 16))
    p_off = find_path(off, (5, 16), (28, 16))
    assert p_road is not None and p_road[-1][0] < 15  # stops at forest edge
    assert p_off is not None and p_off[-1] == (28, 16)


def test_path_avoids_fire():
    t = flat(T.GRASS, 32, 32)
    g = FireGrid(t, seed=1)
    g.state[:, 15] = BURNING
    g.state[0:10, 15] = UNBURNED  # gap at top
    cost = build_cost(t, g.state, TERRAIN.cost_for(MoveClass.FOOT))
    p = find_path(cost, (5, 25), (28, 25))
    assert p is not None and p[-1] == (28, 25)
    assert all(g.state[y, x] != BURNING for x, y in p)


# ---- units ------------------------------------------------------------------


def test_units_snap_to_passable_and_move():
    t = flat(T.GRASS, 48, 48)
    t[20, :] = int(T.ROAD)
    t[:15, :] = int(T.DENSE_FOREST)
    sc = Scenario(
        width=48, height=48, terrain_mode="grid", terrain_grid=t, units=[{"type": "ENGINE", "x": 5, "y": 5}]
    )
    sim = Simulation(sc)
    e = sim.world.units[0]
    assert TERRAIN.cost_for(MoveClass.ROAD)[t[int(e.y), int(e.x)]] < np.inf
    sim.cmd_order(e.uid, "MOVE", target=(40, 20))
    sim.step(60)
    assert abs(e.x - 40) < 1.5 and abs(e.y - 20) < 1.5


def test_cut_team_makes_firebreak():
    t = flat(T.GRASS, 48, 48)
    sc = Scenario(
        width=48,
        height=48,
        terrain_mode="grid",
        terrain_grid=t,
        units=[{"type": "CUT_TEAM", "x": 10, "y": 10}],
    )
    sim = Simulation(sc)
    u = sim.world.units[0]
    sim.cmd_order(u.uid, "CUT", points=[(10, 12), (10, 30)])
    sim.step(120)
    col = sim.grid.terrain[12:30, 10]
    assert (col == int(T.FIREBREAK)).mean() > 0.8


def test_hose_team_connects_and_burns():
    t = flat(T.GRASS, 48, 48)
    t[:, 0:3] = int(T.WATER)
    sc = Scenario(
        width=48,
        height=48,
        terrain_mode="grid",
        terrain_grid=t,
        units=[{"type": "HOSE_TEAM", "x": 6, "y": 24}],
    )
    sim = Simulation(sc)
    u = sim.world.units[0]
    sim.cmd_order(u.uid, "HOSE", target=(12, 24))
    sim.step(40)
    assert u.hose and u.state == "WORKING"
    # Burn a cell under the hose.
    hx, hy = u.hose[len(u.hose) // 2]
    sim.grid.state[hy, hx] = BURNING
    sim.step(1)
    assert u.state == "HOSE_BURNED"


def test_helicopter_drops_and_refills():
    t = flat(T.GRASS, 48, 48)
    t[0:6, 0:6] = int(T.WATER)
    sc = Scenario(
        width=48,
        height=48,
        terrain_mode="grid",
        terrain_grid=t,
        units=[{"type": "HELICOPTER", "x": 3, "y": 3}],
    )
    sim = Simulation(sc)
    u = sim.world.units[0]
    sim.cmd_order(u.uid, "DROP", points=[(20, 20), (40, 20)])
    sim.step(30)
    assert sim.grid.water[20, 20:31].max() > 0 or sim.grid.moisture[20, 20:31].max() > 0.2
    sim.step(60)
    assert u.tank == u.capacity  # went back to the lake


# ---- scenario I/O -------------------------------------------------------------


def test_scenario_roundtrip_grid_mode(tmp_path):
    t = generate_terrain(40, 30, seed=9)
    sc = Scenario(
        name="rt",
        width=40,
        height=30,
        terrain_mode="grid",
        terrain_grid=t,
        ignitions=[{"x": 3, "y": 3}],
        units=[{"type": "ENGINE", "x": 1, "y": 1}],
    )
    from backburn import save_scenario

    p = tmp_path / "s.json"
    save_scenario(sc, p)
    back = load_scenario(p)
    assert back.terrain_mode == "grid"
    assert np.array_equal(back.build_terrain(), t)
    assert back.units == sc.units


def test_generated_terrain_is_seed_stable():
    assert np.array_equal(generate_terrain(64, 64, seed=5), generate_terrain(64, 64, seed=5))
    assert not np.array_equal(generate_terrain(64, 64, seed=5), generate_terrain(64, 64, seed=6))


def test_perf_budget():
    """The Python prototype must sustain well above 10 ticks/s on a 128×96 map with 8 units."""
    import time

    sim = Simulation(load_scenario(FIXTURES / "prairie_fire.json"))
    t0 = time.time()
    sim.step(200)
    dt = time.time() - t0
    assert 200 / dt > 40, f"{200 / dt:.0f} ticks/s"
