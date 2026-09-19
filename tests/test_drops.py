import pytest

from backburn.drops import capacity_length, clip_path, length, payload_per_cell
from backburn.game import Game, scenario_dir
from backburn.scenario import load_scenario
from backburn.sim import Simulation
from backburn.units import Order


def test_clipping_follows_arc_length_not_distance_between_ends():
    curve = [(10, 10), (20, 10), (20, 20), (10, 20)]
    clipped = clip_path(curve, 25)
    assert clipped == [(10, 10), (20, 10), (20, 20), (15, 20)]
    assert length(clipped) == 25


def test_aircraft_applies_curve_and_spends_only_used_payload():
    sim = Simulation(load_scenario(scenario_dir() / "prairie_fire.json"))
    plane = sim.world.add("WATER_BOMBER", 10, 10, sim.grid)
    curve = [(20, 20), (30, 20), (30, 30)]
    plane.x, plane.y = curve[0]
    plane.give(Order("DROP", points=curve))
    for _ in range(1000):
        plane.update(sim.grid, 0.05, sim.world)
        if plane.state == "EXITING":
            break
    assert plane.state == "EXITING"
    assert plane.tank == pytest.approx(plane.capacity - length(curve) * payload_per_cell(plane.spec))
    assert sim.grid.water[20, 25] > 0
    assert sim.grid.water[25, 30] > 0
    assert sim.grid.water[25, 25] == 0  # No shortcut through the inside of the curve.
    assert plane.reload_timer == pytest.approx(
        plane.spec["reload_seconds"] * (1 - plane.tank / plane.capacity)
    )


def test_short_helicopter_drop_preserves_unused_water_and_preview_uses_remainder():
    sim = Simulation(load_scenario(scenario_dir() / "prairie_fire.json"))
    heli = sim.world.add("HELICOPTER", 20, 20, sim.grid)
    heli.give(Order("DROP", points=[(20, 20), (25, 20)]))
    for _ in range(5):
        heli.update(sim.grid, 1, sim.world)
    assert heli.tank == pytest.approx(60)
    game = object.__new__(Game)
    points, limited, payload = game.plan_drop(heli, [(25, 20), (60, 20)])
    assert limited and payload == 60
    assert length(points) == pytest.approx(capacity_length(heli.spec, 60))


def test_planning_purchases_replay_with_budget_and_no_free_units(tmp_path):
    from backburn.campaign import expanded_scenario

    sim = Simulation(expanded_scenario(load_scenario(scenario_dir() / "prairie_fire.json")))
    assert not sim.world.units
    assert sim.cmd_spawn("WATER_BOMBER", immediate=True)
    plane = sim.world.units[-1]
    sim.cmd_order(plane.uid, "DROP", points=[(20, 20), (30, 20), (30, 30)])
    sim.step(15)
    file = tmp_path / "replay.json"
    sim.save_replay(file)
    replay = Simulation.replay(file)
    assert replay.state_hash() == sim.state_hash()
    assert replay.spent == sim.spent


def test_oversized_drop_stops_at_real_payload_limit():
    sim = Simulation(load_scenario(scenario_dir() / "prairie_fire.json"))
    plane = sim.world.add("WATER_BOMBER", 20, 20, sim.grid)
    plane.x, plane.y = 20, 20
    plane.give(Order("DROP", points=[(20, 20), (120, 20)]))
    for _ in range(1000):
        plane.update(sim.grid, 0.05, sim.world)
        if plane.state == "EXITING":
            break
    assert plane.tank == pytest.approx(0, abs=1e-6)
    assert length(plane.current.points) == 0  # Now an exit/refill order.
    assert sim.grid.water[20, 40] > 0
    assert sim.grid.water[:, 70:].max() == 0
