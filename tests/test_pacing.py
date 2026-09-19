from pathlib import Path

import numpy as np

from backburn.art import tree_cells
from backburn.campaign import expanded_scenario
from backburn.config import TerrainType
from backburn.game import Game
from backburn.scenario import load_scenario
from backburn.sim import Simulation

FIXTURES = Path(__file__).resolve().parent / "fixtures"  # v0.6 prototype-scale scenarios


def test_trees_keep_their_positions_when_other_trees_burn():
    terrain = np.full((32, 32), int(TerrainType.FOREST), dtype=np.uint8)
    state = np.zeros_like(terrain)
    before = set(zip(*tree_cells(terrain, state)))
    removed = sorted(before)[9]
    state[removed] = 1
    after = set(zip(*tree_cells(terrain, state)))
    assert after == before - {removed}


def test_expanded_mission_retains_geography_and_rescue_is_winnable():
    source = load_scenario(FIXTURES / "stranded_hikers.json")
    expanded = expanded_scenario(source)
    assert (expanded.width, expanded.height) == (288, 216)
    assert np.array_equal(expanded.build_terrain()[::3, ::3], source.build_terrain())
    sim = Simulation(expanded)
    assert all(u.is_civilian for u in sim.world.units)
    assert sim.cmd_spawn("HELICOPTER", immediate=True)
    heli = sim.world.units[-1]
    for civilian in sim.world.civilians():
        sim.cmd_order(heli.uid, "PICKUP", unit_id=civilian.uid, queue=True)
    sim.cmd_order(heli.uid, "DROPOFF", target=sim.world.safe_zone[:2], queue=True)
    sim.step(600)
    assert sim.civilian_counts()["rescued"] == 4
    assert sim.outcome == "contained"


def test_normal_speed_and_camera_start_allow_preparation(tmp_path, monkeypatch):
    import pygame

    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.setenv("BACKBURN_DATA_DIR", str(tmp_path))
    game = Game()
    try:
        game.start()
        game.act("begin")
        for _ in range(600):
            game.update(1 / 60)
        assert game.sim.tick == 0
        assert game.viewport.w / game.zoom < game.sim.grid.w
        assert game.viewport.h / game.zoom < game.sim.grid.h
        game.act("pause")
        for _ in range(600):
            game.update(1 / 60)
        assert 9 <= game.sim.tick <= 10
        game.fit()
        assert game.sim.grid.w * game.zoom <= game.viewport.w + 1
    finally:
        pygame.quit()
