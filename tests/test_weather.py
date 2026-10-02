"""Wind-driven firebreak behavior, weather persistence and aircraft loading."""

import numpy as np
import pytest

from backburn.config import TerrainType as T
from backburn.fire import Ember, FireGrid
from backburn.scenario import Scenario
from backburn.sim import Simulation
from backburn.units import Order


@pytest.mark.parametrize("seed", [1, 3, 9])
def test_diagonal_closed_fireline_holds_in_low_wind(seed):
    ys, xs = np.indices((48, 48))
    distance = abs(xs - 24) + abs(ys - 24)
    terrain = np.full((48, 48), int(T.FOREST), np.uint8)
    terrain[distance == 11] = int(T.FIREBREAK)
    grid = FireGrid(terrain, seed=seed)
    grid.set_wind(4, 90)
    grid.ignite(24, 24, 2)
    for _ in range(240):
        grid.step()
    assert not (grid.state[distance > 11] > 0).any()
    assert not grid.embers and grid.spot_fires == 0


def test_strong_wind_embers_cross_grass_firebreak_and_spread_faster():
    results = {}
    for wind in (2, 16):
        terrain = np.full((64, 96), int(T.GRASS), np.uint8)
        terrain[:, 45:48] = int(T.FIREBREAK)
        grid = FireGrid(terrain, seed=3)
        grid.set_wind(wind, 90)
        grid.ignite(22, 32, 2)
        for _ in range(240):
            grid.step()
        results[wind] = grid
    assert not (results[2].state[:, 48:] > 0).any()
    assert (results[16].state[:, 48:] > 0).any()
    assert results[16].spot_fires > 0
    assert np.count_nonzero(results[16].ignited_at >= 0) > np.count_nonzero(results[2].ignited_at >= 0)


def test_variable_wind_is_bounded_smooth_and_save_resume_exact(tmp_path):
    scenario = Scenario(
        width=32,
        height=32,
        wind_speed=4,
        wind_bearing=350,
        variable_wind=True,
        objectives={"win_on_contained": False},
    )
    sim = Simulation(scenario)
    values = []
    for _ in range(80):
        sim.step()
        values.append((sim.grid.wind_speed, sim.grid.wind_bearing))
    assert max(x[0] for x in values) - min(x[0] for x in values) > 0.1
    assert all(3 <= speed <= 5.4 for speed, _ in values)
    assert all(abs(((bearing - 350 + 180) % 360) - 180) <= 20 for _, bearing in values)
    assert max(abs(a[0] - b[0]) for a, b in zip(values, values[1:])) < 0.2
    path = tmp_path / "weather.bbsave"
    sim.save_state(path)
    restored = Simulation.load_state(path)
    for _ in range(90):
        sim.step()
        restored.step()
        assert restored.grid.meta() == sim.grid.meta()
    assert restored.state_hash() == sim.state_hash()


def test_embers_leaving_map_do_not_pile_up_at_boundary():
    grid = FireGrid(np.full((16, 16), int(T.GRASS), np.uint8))
    grid.embers = [Ember(5, 5, 500, 5, 1)]
    grid.step()
    assert grid.spot_fires == 0 and not grid.state.any()


def test_new_plane_loads_empty_offmap_before_queued_drop():
    sim = Simulation(Scenario(width=48, height=48, objectives={"win_on_contained": False}))
    assert sim.cmd_spawn("WATER_BOMBER", immediate=True)
    plane = sim.world.units[-1]
    origin = (plane.x, plane.y)
    assert plane.tank == 0 and plane.state == "RELOADING"
    plane.give(Order("DROP", points=[(20, 20), (30, 20)]))
    sim.step(20)
    assert (plane.x, plane.y) == origin
    assert 0 < plane.tank < plane.capacity
    sim.step(30)
    assert (plane.x, plane.y) != origin
    assert plane.state in ("INBOUND", "DROPPING", "EXITING")


def test_cut_clears_endpoint_and_curved_route():
    terrain = np.full((40, 40), int(T.GRASS), np.uint8)
    sim = Simulation(
        Scenario(
            width=40,
            height=40,
            terrain_mode="grid",
            terrain_grid=terrain,
            objectives={"win_on_contained": False},
        )
    )
    crew = sim.world.add("CUT_TEAM", 10, 10, sim.grid)
    crew.give(Order("CUT", points=[(10, 10), (20, 10), (20, 20)]))
    sim.step(100)
    assert np.all(sim.grid.terrain[10, 10:21] == T.FIREBREAK)
    assert np.all(sim.grid.terrain[10:21, 20] == T.FIREBREAK)


def test_old_save_firebreak_fuel_is_removed_on_load():
    grid = FireGrid(np.full((16, 16), int(T.GRASS), np.uint8))
    grid.terrain[:, 8] = int(T.FIREBREAK)
    grid.fuel[:, 8] = 0.02
    grid.state[:, 8] = 1
    restored = FireGrid.from_arrays(grid.to_arrays(), grid.meta())
    assert not restored.fuel[:, 8].any()
    assert not restored.state[:, 8].any()
