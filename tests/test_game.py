"""Exercise actual desktop inputs without requiring a display in CI."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from backburn.game import Game


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKBURN_DATA_DIR", str(tmp_path))
    g = Game()
    yield g
    pygame.quit()


def test_mission_selection_and_deployment_by_mouse(game):
    game.draw()
    card = next(rect for rect, action in game.buttons if action == "mission:1")
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=card.center))
    game.draw()
    deploy = next(rect for rect, action in game.buttons if action == "start")
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=deploy.center))
    assert game.sim.scenario.name == "Stranded Hikers"
    game.update(1)
    assert game.sim.tick == 0
    game.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
    assert game.paused
    for _ in range(20):
        game.update(0.1)
    assert game.sim.tick == 0
    game.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    for _ in range(12):
        game.update(0.1)
    assert game.sim.tick > 0


def test_ui_order_clamps_map_edges_and_save_load_restores(game):
    game.start()
    game.modal = None
    game.selected = 4
    game.issue(game.clamp_point((-100, -30)), game.clamp_point((999, 999)))
    order = game.sim.world.by_id(4).orders[0]
    assert order.kind == "CUT"
    assert order.points == [(0, 0), (game.sim.grid.w - 1, game.sim.grid.h - 1)]
    game.sim.step(3)
    game.save()
    before = game.sim.state_hash()
    game.sim.step(6)
    game.load()
    assert game.sim.state_hash() == before
    assert game.paused


def test_all_views_draw_at_minimum_window(game):
    game.screen = pygame.display.set_mode((1100, 760))
    game.layout()
    game.draw()
    for index in range(4):
        game.mission = index
        game.start()
        game.draw()
        for modal in (None, "help", "dispatch", "pause_menu", "result"):
            game.modal = modal
            game.draw()
            assert all(game.screen.get_rect().contains(rect) for rect, _ in game.buttons)


def test_game_starts_with_stereo_audio_device(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKBURN_DATA_DIR", str(tmp_path))
    pygame.mixer.init(frequency=44100, size=-16, channels=2)
    try:
        g = Game()
        assert pygame.mixer.get_init()[2] == 2
        assert g.audio is not None
        g.draw()
    finally:
        pygame.quit()


def test_purchased_dozer_arrives_and_has_locate_button(game):
    from backburn.config import UNITS

    game.start(True)
    game.act("begin")
    game.act("dispatch")
    before = {u.uid for u in game.sim.world.units}
    game.act("buy:BULLDOZER")
    assert game.modal is None
    assert len(game.sim.world.pending) == 1
    assert game.paused
    assert game.sim.spent == UNITS["BULLDOZER"]["cost"]
    for _ in range(10):
        game.update(0.1)
    assert game.sim.tick == 0
    game.act("pause")
    game.speed = 8
    for _ in range(70):
        game.update(0.1)
    arrived = [u for u in game.sim.world.units if u.uid not in before]
    assert len(arrived) == 1 and arrived[0].utype == "BULLDOZER"
    assert not game.sim.world.pending
    assert game.latest_arrival == arrived[0].uid
    game.draw()
    game.act(f"unit:{game.latest_arrival}")
    assert game.viewport.collidepoint(game.to_screen(arrived[0].x, arrived[0].y))
