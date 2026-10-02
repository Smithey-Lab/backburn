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
    game.act("buy:CUT_TEAM")
    game.issue(game.clamp_point((-100, -30)), game.clamp_point((999, 999)))
    order = game.sim.world.by_id(game.selected).orders[0]
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
    game.sim.step(1)
    game.act("dispatch")
    before = {u.uid for u in game.sim.world.units}
    game.act("buy:BULLDOZER")
    assert game.modal is None
    assert len(game.sim.world.pending) == 1
    assert game.paused
    assert game.sim.spent == UNITS["BULLDOZER"]["cost"]
    for _ in range(10):
        game.update(0.1)
    assert game.sim.tick == 1
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


def test_start_empty_paid_planning_purchase_and_offmap_aircraft_selection(game):
    for index in range(4):
        game.mission = index
        game.start()
        assert not [u for u in game.sim.world.units if not u.is_civilian]
    game.mission = 0
    game.start()
    game.act("begin")
    game.act("buy:WATER_BOMBER")
    plane = game.sim.world.by_id(game.selected)
    assert plane.is_plane and (plane.x < 0 or plane.x >= game.sim.grid.w)
    assert game.sim.spent == plane.spec["cost"]
    assert not game.sim.world.pending
    game.selected = None
    game.act("roster:air")
    game.draw()
    card = next(rect for rect, action in game.buttons if action == f"unit:{plane.uid}")
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=card.center))
    assert game.selected == plane.uid
    game.act("locate")
    position = game.to_screen(plane.x, plane.y)
    assert game.viewport.collidepoint(position)
    game.selected = None
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=position))
    assert game.selected == plane.uid


def test_mouse_draws_payload_limited_curve_and_escape_cancels(game):
    from backburn.drops import capacity_length, length

    game.start()
    game.act("begin")
    game.act("buy:WATER_BOMBER")
    plane = game.sim.world.by_id(game.selected)
    game.cam = [0, 0]
    points = [(20, 20), (30, 20), (35, 25), (35, 40), (45, 45), (80, 45)]
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=game.to_screen(*points[0])))
    for point in points[1:]:
        game.handle(pygame.event.Event(pygame.MOUSEMOTION, pos=game.to_screen(*point), buttons=(0, 0, 1)))
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONUP, button=3, pos=game.to_screen(*points[-1])))
    order = plane.orders[0]
    assert order.kind == "DROP" and len(order.points) > 2
    assert length(order.points) <= capacity_length(plane.spec, plane.capacity) + 1e-6
    assert order.points[-1][0] < points[-1][0]
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=game.to_screen(10, 10)))
    game.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
    game.handle(pygame.event.Event(pygame.MOUSEBUTTONUP, button=3, pos=game.to_screen(20, 20)))
    assert len(plane.orders) == 1 and game.modal is None


def test_camera_moves_beyond_map_at_overview_and_stays_bounded(game, monkeypatch):
    from collections import defaultdict

    game.start()
    game.act("begin")
    game.fit()
    before = list(game.cam)
    keys = defaultdict(int, {pygame.K_a: 1, pygame.K_w: 1})
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: keys)
    for _ in range(400):
        game.update(0.1)
    assert game.cam[0] < before[0] and game.cam[1] < before[1]
    assert game.cam[0] < 0 and game.cam[1] < 0
    stopped = list(game.cam)
    game.update(5)
    assert game.cam == stopped
    assert game.sim.tick == 0
