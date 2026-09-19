"""Backburn desktop front end: mission room, tactical map and persistent progress."""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pygame

from . import __version__
from .art import MapLayer, details, thumbnail, unit_icon
from .campaign import desktop_scenario
from .config import FIRE, TERRAIN, UNITS
from .drops import capacity_length, clip_path, coverage_centers, length, payload_per_cell, smooth_path
from .fire import bearing_to_vector
from .incidents import RANDOM_NAME, describe, incident_seed, random_incident
from .render import OVERLAYS
from .scenario import load_scenario
from .sim import RUNNING, Simulation
from .storage import data_dir, read_json, save_game, save_snapshot, write_json
from .units import ABOARD, CUT, DROP, DROPOFF, HOLD, MOVE, PICKUP

BG = (16, 25, 29)
PANEL = (23, 35, 39)
LINE = (49, 66, 67)
INK = (234, 232, 216)
MUTED = (153, 173, 168)
ORANGE = (238, 157, 82)
TEAL = (124, 201, 179)
NORMAL_TICKS_PER_SECOND = 1
SPEEDS = (1, 3, 8, 16)
RANDOM = "random"
MISSIONS = [
    "prairie_fire",
    "stranded_hikers",
    "refinery_row",
    "wall_of_fire",
    "canyon_run",
    "lakeshore_cabins",
    "highway_9",
    "timber_ridge",
    "ember_storm",
    "fire_complex",
    "long_watch",
    RANDOM,
]
DESCRIPTIONS = [
    "Hold the highways. Protect the settlement.",
    "Six hikers on a burning ridge. One helicopter.",
    "Protect two industrial strips on a budget.",
    "Heavy timber, airborne embers, ninety minutes.",
    "Fire runs uphill toward a town on the rim.",
    "Cabins ring a lake. Boats, dips and engines.",
    "A long corridor, two towns, new roadside starts.",
    "No roads. Lightning. Crews walk or fly in.",
    "Evacuate a town ahead of a wind-driven fire.",
    "Three fires, one budget. Choose what to hold.",
    "Survival: starts keep coming for two hours.",
    "A new map and incident every time. Reroll it.",
]
DIFFICULTIES = [
    "01 / FIRST RESPONSE",
    "02 / SEARCH & RESCUE",
    "03 / RESOURCES",
    "04 / EXTREME WEATHER",
    "05 / TERRAIN",
    "06 / WATER",
    "07 / CORRIDOR",
    "08 / BACKCOUNTRY",
    "09 / EVACUATION",
    "10 / COMPLEX",
    "11 / SURVIVAL",
    "RANDOM INCIDENT",
]
THUMB = (320, 240)


def scenario_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "scenarios"


class Game:
    def __init__(self, size=(1440, 900)):
        pygame.mixer.pre_init(22050, -16, 1, 512)
        pygame.init()
        self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.display.set_caption(f"Backburn | Wildfire command | {__version__}")
        regular = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
        self.fonts = {
            s: pygame.font.Font(str(regular), s) if regular.exists() else pygame.font.SysFont("dejavusans", s)
            for s in (13, 15, 17, 20, 24, 32, 46, 72)
        }
        self.bold = pygame.font.SysFont("segoeui,dejavusans", 20, bold=True)
        self.clock = pygame.time.Clock()
        self.running = True
        self.page = "menu"
        self.modal = None
        self.mission = 0
        self.settings = read_json(data_dir() / "settings.json", {"sound": True})
        self.progress = read_json(data_dir() / "progress.json", {})
        self.random_seed = int(self.settings.get("random_seed") or incident_seed())
        if self.settings.get("random_seed") != self.random_seed:
            self.settings["random_seed"] = self.random_seed
            try:
                write_json(data_dir() / "settings.json", self.settings)
            except OSError:
                pass
        self.scenarios = [self.load_mission(key) for key in MISSIONS]
        self.previews = [None] * len(MISSIONS)
        self.sim = None
        self.map_layer = None
        self.buttons = []
        self.toast = ("", 0)
        self.zoom = 7
        self.cam = [0.0, 0.0]
        self.selected = None
        self.paused = True
        self.speed = 1
        self.acc = 0.0
        self.previous_positions = {}
        self.latest_arrival = None
        self.overlay = 0
        self.drag = None
        self.drag_points = []
        self.pan = None
        self.order_mode = "AUTO"
        self.roster_scroll = 0
        self.roster_filter = "all"
        self.sandbox = False
        self.editor_tool = None
        self.brush = 1
        self.result_saved = False
        self.auto_tick = 0
        self.autosave_thread = None
        self._view_cache = None
        self._minimap_cache = None
        self.audio = None
        if pygame.mixer.get_init():
            frequency, _, channels = pygame.mixer.get_init()
            samples = np.arange(1800) / frequency
            wave = (np.sin(samples * 2 * math.pi * 620) * np.exp(-samples * 45) * 3000).astype(np.int16)
            if channels > 1:
                wave = np.repeat(wave[:, None], channels, axis=1)
            self.audio = pygame.sndarray.make_sound(wave)
        self.layout()

    # ---- missions -----------------------------------------------------------------------

    def load_mission(self, key):
        if key == RANDOM:
            return random_incident(self.random_seed)
        return desktop_scenario(load_scenario(scenario_dir() / f"{key}.json"))

    def preview(self, index):
        """Thumbnail for a mission card; generated once and cached on disk."""
        if self.previews[index] is not None:
            return self.previews[index]
        s = self.scenarios[index]
        folder = data_dir() / "thumbs"
        path = folder / f"{MISSIONS[index]}_{s.seed}_{s.width}x{s.height}.png"
        surf = None
        if path.exists():
            try:
                surf = pygame.image.load(str(path)).convert()
            except pygame.error:
                surf = None
        if surf is None:
            surf = thumbnail(s.build_terrain(), THUMB)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                pygame.image.save(surf, str(path))
            except (OSError, pygame.error):
                pass
        self.previews[index] = surf
        return surf

    def reroll(self):
        self.random_seed = incident_seed()
        self.settings["random_seed"] = self.random_seed
        write_json(data_dir() / "settings.json", self.settings)
        index = MISSIONS.index(RANDOM)
        self.scenarios[index] = random_incident(self.random_seed)
        self.previews[index] = None
        self.mission = index

    def layout(self):
        w, h = self.screen.get_size()
        self.viewport = pygame.Rect(18, 92, max(200, w - 350), max(200, h - 212))
        self.sidebar = pygame.Rect(w - 316, 92, 298, h - 110)
        self.minimap = pygame.Rect(w - 298, h - 176, 262, 142)
        self._view_cache = None

    def text(self, text, x, y, size=17, color=INK, font=None):
        surf = (font or self.fonts[size]).render(str(text), True, color)
        self.screen.blit(surf, (x, y))
        return surf.get_width()

    def wrap(self, text, x, y, width, size=17, color=MUTED, max_lines=None):
        words = str(text).split()
        line = ""
        lines = 0
        for word in words:
            test = (line + " " + word).strip()
            if self.fonts[size].size(test)[0] > width and line:
                self.text(line, x, y, size, color)
                y += size + 8
                lines += 1
                if max_lines and lines >= max_lines:
                    return y
                line = word
            else:
                line = test
        self.text(line, x, y, size, color)
        return y + size + 8

    def button(self, label, rect, action, accent=False, active=False, enabled=True):
        rect = pygame.Rect(rect)
        hover = rect.collidepoint(pygame.mouse.get_pos())
        color = ORANGE if accent else ((48, 72, 71) if hover or active else PANEL)
        if not enabled:
            color = (31, 40, 42)
        pygame.draw.rect(self.screen, color, rect, border_radius=5)
        pygame.draw.rect(self.screen, TEAL if active else LINE, rect, 1, border_radius=5)
        surf = self.fonts[15].render(label, True, BG if accent else (INK if enabled else MUTED))
        self.screen.blit(surf, surf.get_rect(center=rect.center))
        if enabled:
            self.buttons.append((rect, action))

    def say(self, message):
        self.toast = (message, time.monotonic() + 5)

    def beep(self):
        if self.audio and self.settings.get("sound", True):
            self.audio.play()

    # ---- camera -------------------------------------------------------------------------

    def fit(self):
        g = self.sim.grid
        self.zoom = min(self.viewport.w / g.w, self.viewport.h / g.h)
        self.cam = [(g.w - self.viewport.w / self.zoom) / 2, (g.h - self.viewport.h / self.zoom) / 2]

    def tactical_camera(self):
        self.zoom = 8.0
        x, y = self.sim.world.staging
        if self.sim.scenario.ignitions:
            fire = min(self.sim.scenario.ignitions, key=lambda p: math.hypot(p["x"] - x, p["y"] - y))
            x, y = (x + fire["x"]) / 2, (y + fire["y"]) / 2
        self.cam = [x - self.viewport.w / self.zoom / 2, y - self.viewport.h / self.zoom / 2]
        self.clamp_camera()

    def clamp_camera(self):
        for axis, (size, visible) in enumerate(
            ((self.sim.grid.w, self.viewport.w / self.zoom), (self.sim.grid.h, self.viewport.h / self.zoom))
        ):
            margin = max(24, min(80, visible * 0.3))
            center = (size - visible) / 2
            lower = min(-margin, center - margin)
            upper = max(size - visible + margin, center + margin)
            self.cam[axis] = min(upper, max(lower, self.cam[axis]))

    def to_screen(self, x, y):
        return (
            round(self.viewport.x + (x + 0.5 - self.cam[0]) * self.zoom),
            round(self.viewport.y + (y + 0.5 - self.cam[1]) * self.zoom),
        )

    def to_world(self, pos):
        return (
            (pos[0] - self.viewport.x) / self.zoom + self.cam[0] - 0.5,
            (pos[1] - self.viewport.y) / self.zoom + self.cam[1] - 0.5,
        )

    def clamp_point(self, p):
        return (min(self.sim.grid.w - 1, max(0, p[0])), min(self.sim.grid.h - 1, max(0, p[1])))

    # ---- lifecycle ------------------------------------------------------------------------

    def start(self, sandbox=False):
        key = MISSIONS[self.mission]
        self.sim = Simulation(self.load_mission(key) if key != RANDOM else self.scenarios[self.mission])
        self.map_layer = None
        self.sandbox = sandbox
        if sandbox:
            self.sim.scenario.objectives = {"win_on_contained": False}
            self.sim.scenario.duration = 86400
            self.sim.scenario.available_units = None
            self.sim.budget = None
        self.page = "game"
        self.modal = "briefing"
        self.paused = True
        self.speed = 1
        self.acc = 0
        self.drag = None
        self.drag_points = []
        self.previous_positions = {}
        self.latest_arrival = None
        self.selected = next((u.uid for u in self.sim.world.units if not u.is_civilian), None)
        self.result_saved = False
        self.auto_tick = 0
        self.roster_scroll = 0
        self.roster_filter = "all"
        self.editor_tool = None
        self.order_mode = "AUTO"
        self.overlay = 0
        self._view_cache = None
        self._minimap_cache = None
        self.tactical_camera()

    def save(self, name="quicksave.bbsave"):
        try:
            save_game(self.sim, name)
            self.say("Operation saved. Your saves are kept between updates.")
        except OSError as exc:
            self.say(f"Could not save: {exc}")

    def autosave(self):
        """Snapshot now (milliseconds), compress and write on a worker thread."""
        if self.autosave_thread is not None and self.autosave_thread.is_alive():
            return
        snapshot = self.sim.snapshot()

        def work():
            try:
                save_snapshot(snapshot, "autosave.bbsave")
            except OSError:
                pass

        self.autosave_thread = threading.Thread(target=work, daemon=True)
        self.autosave_thread.start()

    def load(self, name="quicksave.bbsave"):
        try:
            restored = Simulation.load_state(data_dir() / name)
        except Exception as exc:
            self.say(f"Could not load save: {exc}")
            return
        self.sim = restored
        self.map_layer = None
        names = [s.name for s in self.scenarios]
        self.mission = (
            names.index(restored.scenario.name)
            if restored.scenario.name in names
            else (MISSIONS.index(RANDOM) if restored.scenario.name.startswith(RANDOM_NAME) else 0)
        )
        self.page = "game"
        self.modal = None
        self.paused = True
        self.sandbox = restored.scenario.duration == 86400
        self.selected = None
        self.drag = None
        self.drag_points = []
        self.result_saved = False
        self.acc = 0
        self.previous_positions = {}
        self.latest_arrival = None
        self.auto_tick = restored.tick
        self._view_cache = None
        self._minimap_cache = None
        self.fit()
        self.say("Save loaded and paused. Press Space when ready.")

    def progress_key(self):
        return MISSIONS[self.mission]

    # ---- actions ----------------------------------------------------------------------------

    def act(self, action):
        self.beep()
        if action.startswith("mission:"):
            self.mission = int(action.split(":")[1])
        elif action == "reroll":
            self.reroll()
        elif action == "start":
            self.start()
        elif action == "sandbox":
            self.start(True)
        elif action == "begin":
            self.modal = None
            self.paused = True
            self.say("Planning phase: position your camera and queue orders. Press Resume when ready.")
        elif action == "close":
            self.modal = None
        elif action == "pause":
            self.paused = not self.paused
        elif action == "menu":
            self.save("autosave.bbsave")
            self.page = "menu"
            self.modal = None
        elif action == "quit":
            self.running = False
        elif action == "retry":
            self.start(self.sandbox)
        elif action == "save":
            self.save()
        elif action == "load":
            self.load()
        elif action == "continue":
            self.load("autosave.bbsave")
        elif action == "fit":
            self.fit()
        elif action == "overlay":
            self.overlay = (self.overlay + 1) % len(OVERLAYS)
        elif action == "sound":
            self.settings["sound"] = not self.settings.get("sound", True)
            write_json(data_dir() / "settings.json", self.settings)
        elif action in ("help", "dispatch", "pause_menu"):
            self.modal = action
        elif action.startswith("speed:"):
            self.speed = int(action.split(":")[1])
        elif action == "scroll:up":
            self.roster_scroll = max(0, self.roster_scroll - 1)
        elif action == "scroll:down":
            self.roster_scroll += 1
        elif action.startswith("roster:"):
            self.roster_filter = action.split(":")[1]
            self.roster_scroll = 0
        elif action == "locate":
            unit = self.sim.world.by_id(self.selected)
            if unit:
                self.cam = [
                    unit.x - self.viewport.w / self.zoom / 2,
                    unit.y - self.viewport.h / self.zoom / 2,
                ]
                self.clamp_camera()
        elif action.startswith("unit:"):
            self.selected = int(action.split(":")[1])
            self.order_mode = "AUTO"
            u = self.sim.world.by_id(self.selected)
            if not u.is_plane and not self.viewport.collidepoint(self.to_screen(u.x, u.y)):
                self.cam = [u.x - self.viewport.w / self.zoom / 2, u.y - self.viewport.h / self.zoom / 2]
        elif action.startswith("order:"):
            self.order_mode = action.split(":")[1]
            if self.order_mode == HOLD and self.selected:
                self.sim.cmd_order(self.selected, HOLD)
                self.order_mode = "AUTO"
        elif action.startswith("buy:"):
            kind = action.split(":")[1]
            accepted = self.sim.cmd_spawn(kind, immediate=self.sim.tick == 0)
            self.say(self.sim.messages[-1].text + (" / Space to resume arrivals" if self.paused else ""))
            if accepted:
                self.modal = None
                if self.sim.tick == 0:
                    unit = self.sim.world.units[-1]
                    self.act(f"unit:{unit.uid}")
                    self.roster_filter = "air" if unit.is_air else "ground"
                    self.roster_scroll = 0
                    self.latest_arrival = unit.uid
                    self.say(
                        f"{unit.label}: "
                        + (
                            "loading off-map. Draw a drop to queue it."
                            if unit.is_plane
                            else "purchased and selected. Ready for orders."
                        )
                    )
        elif action.startswith("tool:"):
            tool = action.split(":")[1]
            self.editor_tool = None if self.editor_tool == tool else tool
        elif action == "brush":
            self.brush = (self.brush + 1) % len(TERRAIN.names)

    def handle(self, ev):
        if ev.type == pygame.QUIT:
            self.running = False
        elif ev.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.set_mode((max(1100, ev.w), max(760, ev.h)), pygame.RESIZABLE)
            self.layout()
        elif ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE and self.drag is not None:
                self.drag = None
                self.drag_points = []
                self.say("Drop plan cancelled.")
                return
            if ev.key == pygame.K_ESCAPE:
                self.modal = None if self.modal else ("pause_menu" if self.page == "game" else None)
            elif self.modal:
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE) and self.modal == "briefing":
                    self.act("begin")
            elif self.page == "game":
                keys = {
                    pygame.K_SPACE: "pause",
                    pygame.K_h: "help",
                    pygame.K_b: "dispatch",
                    pygame.K_F6: "save",
                    pygame.K_F7: "load",
                    pygame.K_HOME: "fit",
                    pygame.K_o: "overlay",
                    pygame.K_m: "order:MOVE",
                    pygame.K_q: "order:AUTO",
                }
                if ev.key in keys:
                    self.act(keys[ev.key])
                elif ev.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
                    self.speed = SPEEDS[(pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4).index(ev.key)]
                elif ev.key == pygame.K_TAB:
                    ids = [
                        u.uid
                        for u in self.sim.world.units
                        if u.alive and not u.is_civilian and u.state != ABOARD
                    ]
                    if ids:
                        self.act(
                            f"unit:{ids[(ids.index(self.selected) + 1) % len(ids)] if self.selected in ids else ids[0]}"
                        )
                elif ev.key == pygame.K_F5:
                    try:
                        self.sim.save_replay(data_dir() / "replay.json")
                        self.say("Replay exported to your saves folder.")
                    except OSError as exc:
                        self.say(str(exc))
                elif self.sandbox and ev.key in (
                    pygame.K_LEFTBRACKET,
                    pygame.K_RIGHTBRACKET,
                    pygame.K_MINUS,
                    pygame.K_EQUALS,
                ):
                    g = self.sim.grid
                    self.sim.cmd_wind(
                        max(0, g.wind_speed + ({pygame.K_MINUS: -1, pygame.K_EQUALS: 1}.get(ev.key, 0))),
                        g.wind_bearing
                        + ({pygame.K_LEFTBRACKET: -15, pygame.K_RIGHTBRACKET: 15}.get(ev.key, 0)),
                    )
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            if ev.button == 1:
                for rect, action in reversed(self.buttons):
                    if rect.collidepoint(ev.pos):
                        self.act(action)
                        return
            if self.modal or self.page != "game":
                return
            if ev.button == 1 and self.minimap.collidepoint(ev.pos):
                self.cam = [
                    (ev.pos[0] - self.minimap.x) / self.minimap.w * self.sim.grid.w
                    - self.viewport.w / self.zoom / 2,
                    (ev.pos[1] - self.minimap.y) / self.minimap.h * self.sim.grid.h
                    - self.viewport.h / self.zoom / 2,
                ]
                return
            if not self.viewport.collidepoint(ev.pos):
                return
            p = self.to_world(ev.pos) if ev.button == 1 else self.clamp_point(self.to_world(ev.pos))
            if ev.button == 2:
                self.pan = ev.pos
            elif ev.button == 3:
                self.drag = p
                self.drag_points = [p]
            elif ev.button == 1:
                if self.sandbox and self.editor_tool:
                    self.paint(self.clamp_point(p))
                    return
                candidates = [
                    u for u in self.sim.world.units if u.alive and u.state != ABOARD and not u.rescued
                ]
                nearest = min(candidates, key=lambda u: math.dist(self.display_position(u), p), default=None)
                picked = (
                    nearest
                    if nearest and math.dist(self.display_position(nearest), p) <= max(1.8, 17 / self.zoom)
                    else None
                )
                sel = self.sim.world.by_id(self.selected)
                if picked and picked.is_civilian and sel and sel.passenger_slots:
                    self.sim.cmd_order(
                        sel.uid,
                        PICKUP,
                        unit_id=picked.uid,
                        queue=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT),
                    )
                    self.say(f"Rescue ordered: {picked.label}")
                    self.beep()
                else:
                    self.selected = picked.uid if picked else None
                    self.order_mode = "AUTO"
        elif ev.type == pygame.MOUSEBUTTONUP:
            if ev.button == 2:
                self.pan = None
            elif ev.button == 3:
                if self.drag and not self.modal and self.viewport.collidepoint(ev.pos):
                    self.issue(self.drag, self.clamp_point(self.to_world(ev.pos)), self.drag_points)
                self.drag = None
                self.drag_points = []
        elif ev.type == pygame.MOUSEMOTION and not self.modal:
            if self.drag is not None and self.viewport.collidepoint(ev.pos):
                point = self.clamp_point(self.to_world(ev.pos))
                if math.dist(self.drag_points[-1], point) >= 0.5 and len(self.drag_points) < 1500:
                    self.drag_points.append(point)
            if self.pan:
                self.cam[0] -= (ev.pos[0] - self.pan[0]) / self.zoom
                self.cam[1] -= (ev.pos[1] - self.pan[1]) / self.zoom
                self.pan = ev.pos
            elif self.sandbox and self.editor_tool and ev.buttons[0] and self.viewport.collidepoint(ev.pos):
                self.paint(self.clamp_point(self.to_world(ev.pos)))
        elif ev.type == pygame.MOUSEWHEEL and self.page == "game" and not self.modal:
            pos = pygame.mouse.get_pos()
            if self.viewport.collidepoint(pos):
                before = self.to_world(pos)
                minimum = min(self.viewport.w / self.sim.grid.w, self.viewport.h / self.sim.grid.h) * 0.6
                self.zoom = max(minimum, min(25, self.zoom * 1.15**ev.y))
                after = self.to_world(pos)
                self.cam[0] += before[0] - after[0]
                self.cam[1] += before[1] - after[1]
            elif self.sidebar.collidepoint(pos):
                self.roster_scroll = max(0, self.roster_scroll - ev.y)

    def paint(self, p):
        x, y = map(round, p)
        if self.editor_tool == "ignite":
            self.sim.cmd_ignite(x, y, 1)
        elif self.editor_tool == "water":
            self.sim.cmd_extinguish(x, y, 2)
        elif self.editor_tool == "terrain":
            self.sim.cmd_paint(x, y, self.brush, 1)

    # ---- orders -----------------------------------------------------------------------------

    def plan_drop(self, unit, raw, queue=False):
        payload = unit.tank
        if unit.is_plane and (queue or unit.state in ("EXITING", "RELOADING")):
            payload = unit.capacity
        elif queue and not unit.is_plane:
            reserved = list(unit.orders)
            if unit.current and unit.current.kind == DROP:
                remaining = (
                    [self.display_position(unit)] + unit.path if unit.drop_phase else unit.current.points
                )
                payload -= length(remaining) * payload_per_cell(unit.spec)
            for order in reserved:
                if order.kind == DROP:
                    if payload <= 1e-6:
                        payload = unit.capacity
                    payload -= length(order.points) * payload_per_cell(unit.spec)
            if payload <= 1e-6:
                payload = unit.capacity
        curve = smooth_path(raw)
        maximum = capacity_length(unit.spec, payload)
        return clip_path(curve, maximum), length(curve) > maximum + 0.01, payload

    def draw_coverage(self, points, unit, color, fill=False):
        if len(points) < 2:
            return
        width = (
            2 * (int(unit.spec["cut_width"]) // 2) + 1
            if unit.spec["action"] == CUT
            else float(unit.spec["drop_width"])
        )
        radius = max(2, round(width * self.zoom / 2))
        centers = coverage_centers(points, max(1, width * 1.1))
        if fill:
            shade = pygame.Surface(self.viewport.size, pygame.SRCALPHA)
            for point in centers:
                x, y = self.to_screen(*point)
                pygame.draw.circle(shade, (*color, 30), (x - self.viewport.x, y - self.viewport.y), radius)
            self.screen.blit(shade, self.viewport.topleft)
        pygame.draw.lines(self.screen, color, False, [self.to_screen(*p) for p in points], 2)
        for point in centers:
            pygame.draw.circle(self.screen, color, self.to_screen(*point), radius, 1)
        self.text("START", *self.to_screen(*points[0]), 13, INK)
        self.text("END", *self.to_screen(*points[-1]), 13, INK)

    def issue(self, p0, p1, drawn=None):
        u = self.sim.world.by_id(self.selected)
        if not u or u.is_civilian or self.sim.outcome != RUNNING:
            return
        dragged = length(list(drawn or [p0]) + [p1]) > 1.5
        queue = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
        action = u.spec["action"]
        if self.order_mode == MOVE:
            kind = MOVE
        elif u.passengers and not dragged:
            kind = DROPOFF
        elif action in (CUT, DROP) and dragged:
            kind = action
        elif action == "SPRAY":
            kind = "SUPPRESS"
        elif action == "HOSE":
            kind = "HOSE"
        else:
            kind = MOVE
        points = [p0, p1]
        if kind == DROP:
            points, limited, payload = self.plan_drop(u, list(drawn or [p0]) + [p1], queue)
            if length(points) < 0.5:
                self.say("No payload available. Wait for a refill before drawing a drop.")
                return
        elif kind == CUT:
            points = smooth_path(list(drawn or [p0]) + [p1])
        elif u.is_plane and self.order_mode != MOVE:
            self.say("Right-drag a curved drop zone. Escape cancels the plan.")
            return
        self.sim.cmd_order(
            u.uid,
            kind,
            target=p1 if kind not in (CUT, DROP) else None,
            points=points if kind in (CUT, DROP) else None,
            queue=queue,
        )
        self.beep()
        self.say(f"{u.label}: {kind.lower()} {'queued' if queue else 'ordered'}")
        if kind == DROP:
            self.say(
                f"{u.label}: curved drop {'queued' if queue else 'ordered'} / {length(points) * payload_per_cell(u.spec) / u.capacity:.0%} load"
                + (" / shortened to payload limit" if limited else "")
            )

    def objectives(self):
        obj = self.sim.scenario.objectives
        st = self.sim.stats()
        lines = []
        if self.sandbox:
            return ["Sandbox / experiment freely", "No score limits. Change wind with [ ] - +"]
        if obj.get("rescue_all_civilians"):
            lines.append(f"Rescue civilians: {st['civilians']['rescued']} / {st['civilians']['total']} safe")
        if obj.get("max_structures_lost") is not None:
            lines.append(f"Buildings lost: {st['structures_lost']} / {obj['max_structures_lost']} allowed")
        if obj.get("max_area_burned_pct") is not None:
            lines.append(f"Keep burned area below {obj['max_area_burned_pct']}%")
        if obj.get("win_on_contained", True):
            lines.append("Contain flames and cool remaining hot spots")
        if obj.get("win_on_timeout"):
            d = int(self.sim.scenario.duration)
            lines.append(f"Or hold the line until {d // 60:02d}:{d % 60:02d}")
        return lines

    def briefing_tip(self):
        obj = self.sim.scenario.objectives
        if obj.get("rescue_all_civilians"):
            return (
                "Buy a helicopter, select it, then click a civilian. Hold Shift to queue more pickups. "
                "Right-click inside the green safe zone to unload. Civilians also walk to the zone on their own."
            )
        tip = (
            "Buy your fleet, then select a card in the roster. Right-click to move or attack. Right-drag a line "
            "with crews to cut a firebreak, or with aircraft to drop water. Dozers travel fast on roads. "
            "Pause any time to plan; 1-4 set the speed."
        )
        if obj.get("win_on_timeout"):
            tip += " This incident is also won by holding the limits until the clock runs out."
        return tip

    # ---- drawing ------------------------------------------------------------------------------

    def draw_menu(self):
        w, h = self.screen.get_size()
        preview = pygame.transform.scale(self.preview(self.mission), (w, h))
        self.screen.blit(preview, (0, 0))
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((9, 18, 21, 222))
        self.screen.blit(shade, (0, 0))
        compact = h < 840
        self.text("B A C K B U R N", 48, 22 if compact else 30, 46)
        self.text("W I L D F I R E   C O M M A N D", 51, 82 if compact else 92, 15, ORANGE)
        self.text("The wildfire is the opponent.", 48, 112 if compact else 132, 32)
        if not compact:
            self.text("Read the land. Build your lines. Bring everyone home.", 50, 178, 20, MUTED)
        columns = 6
        gap = 8
        card_w = (w - 96 - gap * (columns - 1)) // columns
        card_h = 152 if compact else 166
        top = 162 if compact else 218
        for i, s in enumerate(self.scenarios):
            col, row = i % columns, i // columns
            x, y = 48 + col * (card_w + gap), top + row * (card_h + gap)
            rect = pygame.Rect(x, y, card_w, card_h)
            pygame.draw.rect(self.screen, PANEL, rect, border_radius=8)
            self.screen.blit(pygame.transform.smoothscale(self.preview(i), (card_w - 16, 72)), (x + 8, y + 8))
            pygame.draw.rect(self.screen, ORANGE if i == self.mission else LINE, rect, 2, border_radius=8)
            self.text(DIFFICULTIES[i], x + 12, y + 86, 13, ORANGE)
            name = s.name if MISSIONS[i] != RANDOM else RANDOM_NAME
            self.text(name, x + 12, y + 103, 17)
            self.wrap(DESCRIPTIONS[i], x + 12, y + 126, card_w - 22, 13, max_lines=1 if compact else 2)
            self.buttons.append((rect, f"mission:{i}"))
        y = top + 2 * (card_h + gap) + 6
        s = self.scenarios[self.mission]
        self.text(
            f"{s.name.upper()}  /  {describe(s)}",
            50,
            y,
            13,
            TEAL,
        )
        self.wrap(s.briefing, 50, y + 22, w - 470, 17, max_lines=2 if compact else 3)
        self.button("DEPLOY TO INCIDENT", (w - 366, y, 316, 48), "start", True)
        if MISSIONS[self.mission] == RANDOM:
            self.button(f"Reroll incident (seed {self.random_seed})", (w - 366, y + 60, 316, 40), "reroll")
        else:
            self.button("Open as sandbox", (w - 366, y + 60, 316, 40), "sandbox")
        self.button(
            "Continue autosave",
            (50, h - 102, 190, 40),
            "continue",
            enabled=(data_dir() / "autosave.bbsave").exists(),
        )
        self.button(
            "Load quicksave",
            (250, h - 102, 170, 40),
            "load",
            enabled=(data_dir() / "quicksave.bbsave").exists(),
        )
        self.button("Field guide", (430, h - 102, 130, 40), "help")
        self.button(
            "Sound: " + ("on" if self.settings.get("sound") else "off"), (570, h - 102, 130, 40), "sound"
        )
        self.button("Exit", (w - 150, h - 102, 100, 40), "quit")
        self.text(f"ORIGINAL GAME / v{__version__} / WINDOWS DEMO", 50, h - 40, 13, MUTED)
        best = self.progress.get(self.progress_key(), {})
        if best:
            self.text(f"Personal best  {best.get('score', 0):,.0f}", w - 360, h - 40, 15, TEAL)

    def map_surface(self):
        if self.map_layer is None:
            self.map_layer = MapLayer(self.sim, OVERLAYS[self.overlay])
            self._view_cache = None
            self._minimap_cache = None
        return self.map_layer.update(self.sim, OVERLAYS[self.overlay])

    def draw_map(self):
        scr = self.screen
        scr.set_clip(self.viewport)
        pygame.draw.rect(scr, (28, 43, 40), self.viewport)
        surf = self.map_surface()
        visible = pygame.Rect(
            math.floor(self.cam[0]),
            math.floor(self.cam[1]),
            math.ceil(self.viewport.w / self.zoom) + 2,
            math.ceil(self.viewport.h / self.zoom) + 2,
        ).clip(surf.get_rect())
        if visible.w and visible.h:
            key = (self.map_layer.stamp, tuple(visible), round(self.zoom, 4))
            if self._view_cache is None or self._view_cache[0] != key:
                scaled = pygame.transform.scale(
                    surf.subsurface(visible),
                    (max(1, round(visible.w * self.zoom)), max(1, round(visible.h * self.zoom))),
                )
                self._view_cache = (key, scaled)
            scr.blit(
                self._view_cache[1],
                (
                    self.viewport.x + round((visible.x - self.cam[0]) * self.zoom),
                    self.viewport.y + round((visible.y - self.cam[1]) * self.zoom),
                ),
            )
        now = time.monotonic()
        if not self.overlay:
            details(scr, self.sim, self.to_screen, self.zoom, self.viewport, now)
        world = self.sim.world
        if world.safe_zone:
            x, y, r = world.safe_zone
            pygame.draw.circle(scr, TEAL, self.to_screen(x, y), int(r * self.zoom), 2)
            self.text("RESCUE ZONE", *self.to_screen(x - r, y + r + 1), 13, INK)
        for pos, label in [(world.staging, "STAGING")]:
            x, y = self.to_screen(*pos)
            pygame.draw.rect(scr, (217, 206, 157), (x - 10, y - 10, 20, 20), 2)
            self.text(label, x + 14, y - 8, 13)
        for stage_x, label in ((-12, "WEST AIR STAGING"), (self.sim.grid.w + 12, "EAST AIR STAGING")):
            point = self.to_screen(stage_x, world.airbase[1])
            pygame.draw.circle(scr, (76, 106, 113), point, 20, 1)
            self.text(label, point[0] - 60, point[1] + 24, 13, MUTED)
        for u in world.units:
            if u.state == ABOARD or not u.alive or u.rescued:
                continue
            pos = self.to_screen(*self.display_position(u))
            if u.hose and len(u.hose) > 1:
                pygame.draw.lines(scr, (123, 199, 220), False, [self.to_screen(*p) for p in u.hose], 2)
            if u.uid == self.selected:
                if u.path:
                    pts = [pos] + [self.to_screen(*p) for p in u.path]
                    pygame.draw.lines(scr, (230, 235, 205), False, pts, 1)
                    for p in pts[::3]:
                        pygame.draw.circle(scr, INK, p, 2)
                if u.route is not None and u.route.blocks:
                    # The unrefined remainder of a long move: block centres to the goal.
                    from .pathfinding import BLOCK, BlockGraph

                    bw = -(-self.sim.grid.w // BLOCK)
                    pts = [pos]
                    for node in u.route.blocks[::2]:
                        by, bx = divmod(node // BlockGraph.MAX_COMP, bw)
                        pts.append(self.to_screen(bx * BLOCK + BLOCK // 2, by * BLOCK + BLOCK // 2))
                    pts.append(self.to_screen(*u.route.goal))
                    pygame.draw.lines(scr, (150, 160, 140), False, pts, 1)
                if u.current and len(u.current.points) >= 2:
                    pygame.draw.lines(scr, ORANGE, False, [self.to_screen(*p) for p in u.current.points], 2)
                radius = u.spec.get("spray_radius", 0)
                if radius:
                    pygame.draw.circle(scr, (147, 210, 219), pos, max(1, int(radius * self.zoom)), 1)
            if not self.viewport.inflate(30, 30).collidepoint(pos):
                continue
            heading = None
            if u.is_plane:
                previous = self.previous_positions.get(u.uid, (u.x - 1, u.y))
                heading = math.atan2(u.y - previous[1], u.x - previous[0])
            unit_icon(
                scr, u.utype, pos, tuple(u.spec["color"]), u.uid == self.selected, now, 10, heading=heading
            )
            if u.uid == self.selected or u.is_civilian:
                self.text("CIVILIAN" if u.is_civilian else f"{u.uid:02d}", pos[0] + 15, pos[1] - 15, 13)
        selected = world.by_id(self.selected)
        if selected and selected.spec["action"] in (CUT, DROP):
            for order in ([selected.current] if selected.current else []) + list(selected.orders)[:3]:
                if order.kind in (CUT, DROP):
                    self.draw_coverage(order.points, selected, (93, 133, 137))
        if self.drag is not None:
            end = self.clamp_point(self.to_world(pygame.mouse.get_pos()))
            if selected and selected.spec["action"] == DROP and self.order_mode != MOVE:
                queue = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
                points, limited, payload = self.plan_drop(selected, self.drag_points + [end], queue)
                color = (237, 137, 167) if selected.spec.get("drop_agent") == "retardant" else TEAL
                self.draw_coverage(points, selected, color, fill=True)
                used = length(points) * payload_per_cell(selected.spec) / selected.capacity
                label = f"DROP LOAD {used:.0%} / " + ("LIMIT REACHED" if limited else "draw to curve")
                self.text(
                    label, self.viewport.x + 16, self.viewport.bottom - 56, 17, ORANGE if limited else INK
                )
            elif selected and selected.spec["action"] == CUT and self.order_mode != MOVE:
                points = smooth_path(self.drag_points + [end])
                self.draw_coverage(points, selected, ORANGE, fill=True)
                self.text(
                    "FIREBREAK / continuous cleared line", self.viewport.x + 16, self.viewport.bottom - 56, 17
                )
            else:
                pygame.draw.line(scr, INK, self.to_screen(*self.drag), self.to_screen(*end), 2)
                if selected and self.order_mode != MOVE and selected.spec.get("spray_radius", 0):
                    pygame.draw.circle(
                        scr, TEAL, self.to_screen(*end), round(selected.spec["spray_radius"] * self.zoom), 2
                    )
            self.text(
                "Release to confirm / Shift to queue / Esc to cancel",
                self.viewport.x + 16,
                self.viewport.bottom - 30,
                15,
            )
        for ember in self.sim.grid.embers[:200]:
            progress = max(0, min(1, (ember.duration - ember.ttl + self.acc) / max(1, ember.duration)))
            x = ember.x0 + (ember.x1 - ember.x0) * progress
            y = ember.y0 + (ember.y1 - ember.y0) * progress
            tip = self.to_screen(x, y)
            tail = self.to_screen(x - (ember.x1 - ember.x0) * 0.12, y - (ember.y1 - ember.y0) * 0.12)
            pygame.draw.line(scr, (230, 137, 57), tail, tip, 1)
            pygame.draw.circle(scr, (255, 225, 136), tip, 2)
        self.draw_wind()
        scr.set_clip(None)
        pygame.draw.rect(scr, LINE, self.viewport, 1)
        return surf

    def draw_wind(self):
        grid = self.sim.grid
        rect = pygame.Rect(self.viewport.right - 226, self.viewport.top + 12, 214, 94)
        pygame.draw.rect(self.screen, BG, rect, border_radius=6)
        center = (rect.x + 37, rect.y + 43)
        pygame.draw.circle(self.screen, LINE, center, 24, 1)
        self.text("N", center[0] - 4, rect.y + 1, 13, MUTED)
        dx, dy = bearing_to_vector(grid.wind_bearing)
        start = (center[0] - dx * 15, center[1] - dy * 15)
        tip = (center[0] + dx * 21, center[1] + dy * 21)
        tint = ORANGE if grid.wind_speed >= FIRE["spot_min_wind"] else TEAL
        pygame.draw.line(self.screen, tint, start, tip, 3)
        pygame.draw.polygon(
            self.screen,
            tint,
            [
                tip,
                (tip[0] - dx * 10 - dy * 6, tip[1] - dy * 10 + dx * 6),
                (tip[0] - dx * 10 + dy * 6, tip[1] - dy * 10 - dx * 6),
            ],
        )
        direction = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((grid.wind_bearing + 22.5) // 45) % 8]
        self.text(f"TOWARD {direction}", rect.x + 75, rect.y + 11, 13, MUTED)
        self.text(f"{grid.wind_speed:.1f} m/s", rect.x + 75, rect.y + 31, 20, INK)
        risk = "Embers can cross lines" if grid.wind_speed >= FIRE["spot_min_wind"] else "Low ember risk"
        self.text(risk, rect.x + 12, rect.y + 70, 13, tint)

    def draw_game(self):
        w, h = self.screen.get_size()
        st = self.sim.stats()
        self.text("BACKBURN", 20, 13, 24)
        self.text(self.sim.scenario.name.upper(), 20, 48, 15, ORANGE)
        for i, (label, value) in enumerate(
            [
                ("INCIDENT TIME", f"{int(st['time']) // 60:02d}:{int(st['time']) % 60:02d}"),
                ("ACTIVE FIRE", f"{st['burning']} cells"),
                ("LAND BURNED", f"{st['area_burned_pct']:.1f}%"),
                ("WIND TOWARD", f"{self.sim.grid.wind_bearing:.0f}° / {self.sim.grid.wind_speed:.0f} m/s"),
            ]
        ):
            x = 230 + i * ((w - 580) // 4)
            self.text(label, x, 16, 13, MUTED)
            self.text(value, x, 39, 20, INK)
        self.button(
            "II Pause" if not self.paused else "> Resume", (w - 316, 16, 128, 48), "pause", active=self.paused
        )
        self.button("Menu", (w - 178, 16, 74, 48), "pause_menu")
        self.button("Help", (w - 94, 16, 76, 48), "help")
        mini = self.draw_map()
        x = self.sidebar.x
        pygame.draw.rect(self.screen, PANEL, self.sidebar, border_radius=6)
        self.text("INCIDENT OBJECTIVES", x + 18, 108, 13, ORANGE)
        y = 137
        for line in self.objectives():
            y = self.wrap(line, x + 18, y, 264, 15, INK)
        money = "Unlimited" if self.sim.budget is None else f"${self.sim.budget - self.sim.spent:,.0f}"
        self.text(f"Resources  {money}", x + 18, 245, 17, TEAL)
        self.button(f"Buy units  /  {st['pending']} inbound", (x + 18, 275, 262, 34), "dispatch")
        for index, (key, title) in enumerate((("all", "All"), ("ground", "Ground"), ("air", "Aircraft"))):
            self.button(
                title, (x + 16 + index * 90, 317, 86, 28), f"roster:{key}", active=self.roster_filter == key
            )
        if self.sim.world.pending:
            arrival = min(self.sim.world.pending, key=lambda a: a.at)
            eta = max(0, math.ceil(arrival.at - self.sim.grid.time))
            self.text(
                f"Inbound: {UNITS[arrival.utype]['label']} {eta}s" + (" / paused" if self.paused else ""),
                x + 18,
                349,
                13,
                ORANGE,
            )
        else:
            self.text("Select a card to command", x + 18, 349, 13, MUTED)
        roster = [
            u
            for u in self.sim.world.units
            if u.alive
            and not u.is_civilian
            and u.state != ABOARD
            and (self.roster_filter == "all" or u.is_air == (self.roster_filter == "air"))
        ]
        panel_y = h - 337
        visible = max(1, (panel_y - 368 - 30) // 50)
        self.roster_scroll = min(self.roster_scroll, max(0, len(roster) - visible))
        y = 368
        for u in roster[self.roster_scroll : self.roster_scroll + visible]:
            self.button("", (x + 16, y, 266, 44), f"unit:{u.uid}", active=u.uid == self.selected)
            self.text(u.label, x + 25, y + 5, 15)
            ready = u.capacity and u.tank >= u.capacity and u.state not in ("RELOADING", "EXITING")
            tint = TEAL if ready else MUTED
            status = self.resource_status(u)
            if u.is_plane and not (0 <= u.x < self.sim.grid.w and 0 <= u.y < self.sim.grid.h):
                status = "STAGED / " + status
            self.text(status, x + 25, y + 27, 13, tint)
            if u.uid == self.selected:
                pygame.draw.rect(self.screen, TEAL, (x + 17, y + 5, 3, 38), border_radius=1)
            y += 50
        if not roster:
            self.text(
                "No aircraft purchased" if self.roster_filter == "air" else "No units in this group",
                x + 24,
                y + 8,
                15,
                MUTED,
            )
        self.text(f"{len(roster)} unit" + ("s" if len(roster) != 1 else ""), x + 18, panel_y - 23, 13, MUTED)
        self.button("Prev", (x + 146, panel_y - 29, 62, 24), "scroll:up", enabled=self.roster_scroll > 0)
        self.button(
            "Next",
            (x + 216, panel_y - 29, 62, 24),
            "scroll:down",
            enabled=self.roster_scroll + visible < len(roster),
        )
        pygame.draw.line(self.screen, LINE, (x + 16, panel_y - 1), (x + 282, panel_y - 1))
        y = panel_y + 8
        sel = self.sim.world.by_id(self.selected)
        if sel and not sel.is_civilian:
            self.text(sel.label.upper(), x + 18, y, 17, ORANGE)
            self.text(self.resource_status(sel) + f" / {len(sel.orders)} queued", x + 18, y + 26, 13)
            if sel.capacity:
                pygame.draw.rect(self.screen, LINE, (x + 18, y + 50, 180, 10))
                pygame.draw.rect(
                    self.screen,
                    TEAL,
                    (x + 18, y + 50, int(180 * max(0, min(1, sel.tank / sel.capacity))), 10),
                )
                self.text(f"{max(0, sel.tank) / sel.capacity:.0%}", x + 216, y + 43, 13, TEAL)
            if sel.passenger_slots:
                self.text(
                    f"Passengers  {len(sel.passengers)} / {sel.passenger_slots}", x + 18, y + 64, 13, TEAL
                )
            self.button("Auto / Q", (x + 18, y + 89, 84, 30), "order:AUTO", active=self.order_mode == "AUTO")
            self.button("Locate", (x + 108, y + 89, 84, 30), "locate")
            self.button("Hold", (x + 198, y + 89, 82, 30), "order:HOLD")
        else:
            self.wrap(
                "Buy your fleet to begin. Aircraft can be selected here even when staged off-map.",
                x + 18,
                y,
                260,
                17,
            )
        if self._minimap_cache is None or self._minimap_cache[0] != (self.map_layer.stamp, self.minimap.size):
            self._minimap_cache = (
                (self.map_layer.stamp, self.minimap.size),
                pygame.transform.scale(mini, self.minimap.size),
            )
        self.screen.blit(self._minimap_cache[1], self.minimap)
        self.screen.set_clip(self.minimap)
        vr = pygame.Rect(
            self.minimap.x + self.cam[0] / self.sim.grid.w * self.minimap.w,
            self.minimap.y + self.cam[1] / self.sim.grid.h * self.minimap.h,
            self.viewport.w / self.zoom / self.sim.grid.w * self.minimap.w,
            self.viewport.h / self.zoom / self.sim.grid.h * self.minimap.h,
        )
        pygame.draw.rect(self.screen, INK, vr, 1)
        for u in self.sim.world.units:
            if not u.alive or u.rescued or u.state == ABOARD:
                continue
            pygame.draw.circle(
                self.screen,
                ORANGE if u.is_civilian else INK,
                (
                    round(self.minimap.x + u.x / self.sim.grid.w * self.minimap.w),
                    round(self.minimap.y + u.y / self.sim.grid.h * self.minimap.h),
                ),
                2,
            )
        self.screen.set_clip(None)
        y = self.viewport.bottom + 12
        self.button("Fit / Home", (18, y, 100, 32), "fit")
        self.button("Map: " + str(OVERLAYS[self.overlay] or "terrain"), (126, y, 140, 32), "overlay")
        for i, speed in enumerate(SPEEDS):
            self.button(f"{speed}x", (280 + i * 50, y, 44, 32), f"speed:{speed}", active=self.speed == speed)
        if self.sandbox:
            for i, tool in enumerate(("ignite", "water", "terrain")):
                self.button(
                    tool.title(), (498 + i * 85, y, 80, 32), f"tool:{tool}", active=self.editor_tool == tool
                )
            self.button(TERRAIN.names[self.brush], (755, y, 130, 32), "brush")
        else:
            self.text(
                "RIGHT-DRAG  line / drop    SHIFT  queue / fast pan    WASD  pan", 498, y + 7, 13, MUTED
            )
        if self.sim.messages:
            msg = self.sim.messages[-1]
            self.wrap(
                f"RADIO  {int(msg.time) // 60:02d}:{int(msg.time) % 60:02d}  {msg.text}",
                20,
                y + 44,
                self.viewport.w - 20,
                15,
                MUTED,
                max_lines=2,
            )
        if self.paused and not self.modal:
            pygame.draw.rect(self.screen, BG, (self.viewport.centerx - 94, 106, 188, 33), border_radius=4)
            self.text("PLAN / SPACE", self.viewport.centerx - 60, 112, 15, ORANGE)

    def draw_modal(self):
        if not self.modal:
            return
        self.buttons = []
        w, h = self.screen.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((3, 10, 13, 190))
        self.screen.blit(shade, (0, 0))
        rect = pygame.Rect(w // 2 - 360, h // 2 - 285, 720, 570)
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=12)
        pygame.draw.rect(self.screen, LINE, rect, 1, border_radius=12)
        x, y = rect.x + 34, rect.y + 28
        if self.modal == "briefing":
            self.text("INCIDENT BRIEFING", x, y, 15, ORANGE)
            self.text(self.sim.scenario.name, x, y + 35, 32)
            bottom = self.wrap(self.sim.scenario.briefing, x, y + 94, 644, 20, INK, max_lines=5)
            for line in self.objectives():
                bottom = self.wrap("• " + line, x, bottom + 6, 644, 17, TEAL, max_lines=1)
            self.wrap(
                self.briefing_tip(),
                x,
                min(max(bottom + 18, y + 275), rect.bottom - 170),
                644,
                15,
                max_lines=4,
            )
            self.button("OPEN MAP TO PLAN / ENTER", (x, rect.bottom - 78, 652, 46), "begin", True)
        elif self.modal == "help":
            self.text("FIELD GUIDE", x, y, 32)
            lines = [
                (
                    "SELECT & ORDER",
                    "Left-click a unit. Right-click to act. M forces a move order; Q restores automatic actions.",
                ),
                (
                    "CUT LINES & AIR DROPS",
                    "Right-drag a curve: circles show the payload-limited swath. Esc cancels. Planes exit to reload. "
                    "Dozers and engines travel at road speed on highways and gravel; the planner routes them along roads.",
                ),
                (
                    "RESCUE",
                    "Select a helicopter, click a civilian. Shift-click queues pickups. Right-click the green safe zone to unload.",
                ),
                (
                    "WATER & RESOURCES",
                    "B buys units. Planning purchases are ready immediately; later purchases show a countdown. Hose teams need a lake or engine.",
                ),
                (
                    "CAMERA, SPEED & SAVES",
                    "Wheel zooms, WASD pans (Shift for fast), middle-drag pans, Home fits map, minimap click jumps. "
                    "1-4 set 1x/3x/8x/16x. Space pauses. F6 saves; F7 loads. F5 exports replay.",
                ),
            ]
            yy = y + 60
            for title, body in lines:
                self.text(title, x, yy, 13, ORANGE)
                yy = self.wrap(body, x, yy + 20, 644, 15, max_lines=3) + 8
            self.button("Back", (x, rect.bottom - 64, 652, 36), "close")
        elif self.modal == "dispatch":
            self.text("RESOURCE DISPATCH", x, y, 32)
            self.text(
                "Crews are ready during planning. Planes start empty and load off-map after Resume.",
                x,
                y + 48,
                15,
                MUTED,
            )
            avail = self.sim.available_units()
            for i, kind in enumerate(avail):
                spec = UNITS[kind]
                col = i % 2
                row = i // 2
                delay = (
                    (f"load {spec['reload_seconds']:.0f}s" if spec.get("reload_at_base") else "ready now")
                    if self.sim.tick == 0
                    else f"{spec.get('arrival_seconds', 0):.0f}s"
                )
                label = f"{spec.get('label', kind)} ${spec.get('cost', 0):,.0f} / {delay}"
                self.button(
                    label,
                    (x + col * 332, y + 90 + row * 48, 318, 40),
                    f"buy:{kind}",
                    enabled=self.sim.can_afford(kind),
                )
            if not avail:
                self.wrap("All available units are already on scene for this operation.", x, y + 105, 644, 20)
            self.button("Return to operation", (x, rect.bottom - 64, 652, 36), "close")
        elif self.modal == "result":
            success = self.sim.outcome == "contained"
            self.text(
                "INCIDENT CONTAINED" if success else "OPERATION ENDED", x, y, 32, TEAL if success else ORANGE
            )
            yy = self.wrap(self.sim.outcome_reason, x, y + 60, 644, 20, INK)
            self.text(f"{self.sim.score()['total']:,.0f}", x, yy + 20, 72)
            self.text("OPERATION SCORE", x, yy + 110, 13, MUTED)
            self.wrap(
                "The final map stays available for review. Retry to improve your plan, or choose another incident from the mission room.",
                x,
                yy + 160,
                640,
                20,
            )
            self.button("Review map", (x, rect.bottom - 126, 652, 40), "close")
            self.button("Retry", (x, rect.bottom - 72, 316, 42), "retry", True)
            self.button("Mission room", (x + 332, rect.bottom - 72, 320, 42), "menu")
        else:
            self.text("OPERATION PAUSED", x, y, 32)
            for i, (label, action) in enumerate(
                [
                    ("Return to operation", "close"),
                    ("Save operation / F6", "save"),
                    ("Load quicksave / F7", "load"),
                    ("Restart incident", "retry"),
                    ("Mission room (autosaves)", "menu"),
                    ("Exit game (autosaves)", "quit"),
                ]
            ):
                self.button(label, (x, y + 80 + i * 60, 652, 46), action, accent=i == 0)

    def draw(self):
        self.buttons = []
        self.screen.fill(BG)
        if self.page == "menu" or self.sim is None:
            self.draw_menu()
        else:
            self.draw_game()
        self.draw_modal()
        if time.monotonic() < self.toast[1]:
            width = min(self.screen.get_width() - 40, self.fonts[15].size(self.toast[0])[0] + 32)
            rect = pygame.Rect(20, self.screen.get_height() - 48, width, 34)
            pygame.draw.rect(self.screen, (42, 67, 63), rect, border_radius=5)
            self.text(self.toast[0], rect.x + 12, rect.y + 7, 15, INK)

    def display_position(self, unit):
        previous = self.previous_positions.get(unit.uid, (unit.x, unit.y))
        alpha = max(0, min(1, self.acc))
        return (previous[0] + (unit.x - previous[0]) * alpha, previous[1] + (unit.y - previous[1]) * alpha)

    @staticmethod
    def resource_status(unit):
        if unit.state == "RELOADING":
            return f"LOAD {math.ceil(unit.reload_timer)}s / {unit.tank / unit.capacity:.0%}"
        if unit.is_plane and unit.state == "READY":
            return "READY 100%"
        if unit.capacity:
            amount = max(0, min(100, round(unit.tank / unit.capacity * 100)))
            return f"{unit.state} {amount}%"
        return unit.state.replace("_", " ")

    # ---- per frame ----------------------------------------------------------------------------

    def update(self, dt):
        if self.page != "game" or self.modal or self.sim is None:
            return
        keys = pygame.key.get_pressed()
        pan = 600 * (3 if keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT] else 1)
        self.cam[0] += (keys[pygame.K_d] - keys[pygame.K_a]) * dt * pan / self.zoom
        self.cam[1] += (keys[pygame.K_s] - keys[pygame.K_w]) * dt * pan / self.zoom
        if not self.paused and self.sim.outcome == RUNNING:
            self.acc += min(dt, 0.1) * NORMAL_TICKS_PER_SECOND * self.speed
            n = int(self.acc)
            budget = time.monotonic() + 0.05  # never spend more than this per frame stepping
            for _ in range(n):
                self.previous_positions = {u.uid: (u.x, u.y) for u in self.sim.world.units}
                reloading = {u.uid for u in self.sim.world.units if u.is_plane and u.state == "RELOADING"}
                known = {u.uid for u in self.sim.world.units}
                self.sim.step(1)
                for u in self.sim.world.units:
                    if u.uid not in known and not u.is_civilian:
                        self.latest_arrival = u.uid
                        self.say(f"{u.label} has arrived. Select its roster card, then Locate to find it.")
                for u in self.sim.world.units:
                    if u.uid in reloading and u.tank >= u.capacity:
                        self.say(f"{u.label}: fully loaded and ready for another run.")
                self.acc -= 1
                if time.monotonic() > budget:
                    self.acc = min(self.acc, 1.0)  # fall behind gracefully instead of freezing the frame
                    break
        self.clamp_camera()
        if self.sim.tick - self.auto_tick >= 180:
            self.autosave()
            self.auto_tick = self.sim.tick
        if self.sim.outcome != RUNNING and not self.result_saved:
            self.result_saved = True
            self.modal = "result"
            if not self.sandbox:
                key = self.progress_key()
                score = self.sim.score()["total"]
                if score > self.progress.get(key, {}).get("score", -1):
                    self.progress[key] = {"score": score, "outcome": self.sim.outcome}
                    try:
                        write_json(data_dir() / "progress.json", self.progress)
                    except OSError:
                        pass

    def run(self, max_frames=None):
        frames = 0
        while self.running:
            dt = self.clock.tick(60) / 1000
            for ev in pygame.event.get():
                self.handle(ev)
            self.update(dt)
            self.draw()
            pygame.display.flip()
            frames += 1
            if max_frames and frames >= max_frames:
                break
        if self.page == "game" and self.sim is not None:
            try:
                save_game(self.sim, "autosave.bbsave")
            except OSError:
                pass
        pygame.quit()


def main():
    if "--smoke-test" in sys.argv:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        game = Game()
        game.start()
        game.modal = None
        game.run(5)
    else:
        Game().run()


if __name__ == "__main__":
    main()
