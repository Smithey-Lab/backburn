"""Flight lifecycle and desktop motion regressions."""

import math

import pytest

from backburn.game import Game, scenario_dir
from backburn.scenario import load_scenario
from backburn.sim import Simulation
from backburn.units import DROP, HOLD, Order, Unit


def flight():
    sim = Simulation(load_scenario(scenario_dir() / "prairie_fire.json"))
    plane = sim.world.add("WATER_BOMBER", 10, 40, sim.grid)
    plane.give(Order(DROP, points=[(30, 40), (65, 40)]))
    return sim, plane


def test_plane_flies_without_stopping_and_reloads_only_off_map():
    sim, plane = flight()
    assert plane.x < 0 or plane.x >= sim.grid.w
    entered = False
    states = set()
    for _ in range(3000):
        before = (plane.x, plane.y)
        plane.update(sim.grid, 0.1, sim.world)
        states.add(plane.state)
        if 0 <= plane.x < sim.grid.w:
            entered = True
            assert math.dist(before, (plane.x, plane.y)) > 0
        if plane.state == "RELOADING":
            assert plane.x < 0 or plane.x >= sim.grid.w
        if entered and plane.state == "READY":
            break
    assert {"INBOUND", "DROPPING", "EXITING", "RELOADING", "READY"} <= states
    assert plane.tank == plane.capacity
    assert Game.resource_status(plane) == "READY 100%"
    assert sim.grid.water.max() > 0


def test_orders_during_reload_wait_and_saved_flight_resumes():
    sim, plane = flight()
    for _ in range(1000):
        plane.update(sim.grid, 0.1, sim.world)
        if plane.state == "RELOADING":
            break
    plane.give(Order(DROP, points=[(70, 50), (20, 50)]))
    assert plane.current.kind == "REFILL"
    restored = Unit.from_dict(plane.to_dict())
    for _ in range(700):
        plane.update(sim.grid, 0.1, sim.world)
        restored.update(sim.grid, 0.1, sim.world)
        assert restored.to_dict() == plane.to_dict()
    assert plane.current is not None


def test_hold_does_not_leave_plane_hovering():
    sim, plane = flight()
    plane.x, plane.y = 50, 40
    plane.give(Order(HOLD))
    plane.update(sim.grid, 1, sim.world)
    assert plane.state == "EXITING"
    assert plane.x != 50


def test_render_interpolation_tracks_fraction_of_tick():
    game = object.__new__(Game)
    unit = Unit("ENGINE", 12, 20, uid=1)
    game.previous_positions = {1: (10, 20)}
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        game.acc = fraction
        assert game.display_position(unit) == pytest.approx((10 + 2 * fraction, 20))
