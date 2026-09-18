"""Backburn desktop front end: mission room, tactical map and persistent progress."""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pygame

from . import __version__
from .art import details, terrain_surface, unit_icon
from .config import TERRAIN, UNITS
from .render import OVERLAYS
from .scenario import load_scenario
from .sim import RUNNING, Simulation
from .storage import data_dir, read_json, save_game, write_json
from .units import ABOARD, CUT, DROP, DROPOFF, HOLD, MOVE, PICKUP

BG = (16, 25, 29)
PANEL = (23, 35, 39)
LINE = (49, 66, 67)
INK = (234, 232, 216)
MUTED = (153, 173, 168)
ORANGE = (238, 157, 82)
TEAL = (124, 201, 179)
MISSIONS = ["prairie_fire", "stranded_hikers", "refinery_row", "wall_of_fire"]
DESCRIPTIONS = [
    "Hold the road. Protect the settlement.",
    "Four hikers. One helicopter. Bring them home.",
    "Protect an industrial corridor on a budget.",
    "Heavy timber, airborne embers, shifting winds.",
]
DIFFICULTIES = [
    "01 / FIRST RESPONSE",
    "02 / SEARCH & RESCUE",
    "03 / RESOURCE MANAGEMENT",
    "04 / EXTREME CONDITIONS",
]


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
        self.scenarios = [load_scenario(scenario_dir() / f"{key}.json") for key in MISSIONS]
        self.previews = [terrain_surface(Simulation(s)) for s in self.scenarios]
        self.settings = read_json(data_dir() / "settings.json", {"sound": True})
        self.progress = read_json(data_dir() / "progress.json", {})
        self.sim = Simulation(self.scenarios[0])
        self.buttons = []
        self.toast = ("", 0)
        self.zoom = 7
        self.cam = [0.0, 0.0]
        self.selected = None
        self.paused = True
        self.speed = 1
        self.acc = 0.0
        self.overlay = 0
        self.drag = None
        self.pan = None
        self.order_mode = "AUTO"
        self.roster_scroll = 0
        self.sandbox = False
        self.editor_tool = None
        self.brush = 1
        self.result_saved = False
        self.auto_tick = 0
        self.audio = None
        if pygame.mixer.get_init():
            frequency, _, channels = pygame.mixer.get_init()
            samples = np.arange(1800) / frequency
            wave = (np.sin(samples * 2 * math.pi * 620) * np.exp(-samples * 45) * 3000).astype(np.int16)
            if channels > 1:
                wave = np.repeat(wave[:, None], channels, axis=1)
            self.audio = pygame.sndarray.make_sound(wave)
        self.layout()

    def layout(self):
        w, h = self.screen.get_size()
        self.viewport = pygame.Rect(18, 92, max(200, w - 350), max(200, h - 212))
        self.sidebar = pygame.Rect(w - 316, 92, 298, h - 110)
        self.minimap = pygame.Rect(w - 298, h - 176, 262, 142)

    def text(self, text, x, y, size=17, color=INK, font=None):
        surf = (font or self.fonts[size]).render(str(text), True, color)
        self.screen.blit(surf, (x, y))
        return surf.get_width()

    def wrap(self, text, x, y, width, size=17, color=MUTED):
        words = str(text).split()
        line = ""
        for word in words:
            test = (line + " " + word).strip()
            if self.fonts[size].size(test)[0] > width and line:
                self.text(line, x, y, size, color)
                y += size + 8
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

    def fit(self):
        g = self.sim.grid
        self.zoom = min(self.viewport.w / g.w, self.viewport.h / g.h)
        self.cam = [(g.w - self.viewport.w / self.zoom) / 2, (g.h - self.viewport.h / self.zoom) / 2]

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

    def start(self, sandbox=False):
        self.sim = Simulation(load_scenario(scenario_dir() / f"{MISSIONS[self.mission]}.json"))
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
        self.selected = next((u.uid for u in self.sim.world.units if not u.is_civilian), None)
        self.result_saved = False
        self.auto_tick = 0
        self.roster_scroll = 0
        self.editor_tool = None
        self.order_mode = "AUTO"
        self.overlay = 0
        self.fit()

    def save(self, name="quicksave.bbsave"):
        try:
            save_game(self.sim, name)
            self.say("Operation saved. Your saves are kept between updates.")
        except OSError as exc:
            self.say(f"Could not save: {exc}")

    def load(self, name="quicksave.bbsave"):
        try:
            restored = Simulation.load_state(data_dir() / name)
        except Exception as exc:
            self.say(f"Could not load save: {exc}")
            return
        self.sim = restored
        self.mission = next((i for i, s in enumerate(self.scenarios) if s.name == restored.scenario.name), 0)
        self.page = "game"
        self.modal = None
        self.paused = True
        self.sandbox = restored.scenario.duration == 86400
        self.selected = None
        self.drag = None
        self.result_saved = False
        self.acc = 0
        self.auto_tick = restored.tick
        self.fit()
        self.say("Save loaded and paused. Press Space when ready.")

    def act(self, action):
        self.beep()
        if action.startswith("mission:"):
            self.mission = int(action.split(":")[1])
        elif action == "start":
            self.start()
        elif action == "sandbox":
            self.start(True)
        elif action == "begin":
            self.modal = None
            self.paused = False
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
        elif action.startswith("unit:"):
            self.selected = int(action.split(":")[1])
            self.order_mode = "AUTO"
            u = self.sim.world.by_id(self.selected)
            if not self.viewport.collidepoint(self.to_screen(u.x, u.y)):
                self.cam = [u.x - self.viewport.w / self.zoom / 2, u.y - self.viewport.h / self.zoom / 2]
        elif action.startswith("order:"):
            self.order_mode = action.split(":")[1]
            if self.order_mode == HOLD and self.selected:
                self.sim.cmd_order(self.selected, HOLD)
                self.order_mode = "AUTO"
        elif action.startswith("buy:"):
            kind = action.split(":")[1]
            self.sim.cmd_spawn(kind)
            self.say(self.sim.messages[-1].text)
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
                elif ev.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    self.speed = {pygame.K_1: 1, pygame.K_2: 3, pygame.K_3: 8}[ev.key]
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
            p = self.clamp_point(self.to_world(ev.pos))
            if ev.button == 2:
                self.pan = ev.pos
            elif ev.button == 3:
                self.drag = p
            elif ev.button == 1:
                if self.sandbox and self.editor_tool:
                    self.paint(p)
                    return
                candidates = [
                    u for u in self.sim.world.units if u.alive and u.state != ABOARD and not u.rescued
                ]
                nearest = min(candidates, key=lambda u: math.hypot(u.x - p[0], u.y - p[1]), default=None)
                picked = (
                    nearest
                    if nearest and math.hypot(nearest.x - p[0], nearest.y - p[1]) <= max(1.8, 17 / self.zoom)
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
                    self.issue(self.drag, self.clamp_point(self.to_world(ev.pos)))
                self.drag = None
        elif ev.type == pygame.MOUSEMOTION and not self.modal:
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
                self.zoom = max(2, min(25, self.zoom * 1.15**ev.y))
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

    def issue(self, p0, p1):
        u = self.sim.world.by_id(self.selected)
        if not u or u.is_civilian or self.sim.outcome != RUNNING:
            return
        dragged = math.dist(p0, p1) > 1.5
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
        self.sim.cmd_order(
            u.uid,
            kind,
            target=p1 if kind not in (CUT, DROP) else None,
            points=[p0, p1] if kind in (CUT, DROP) else None,
            queue=queue,
        )
        self.beep()
        self.say(f"{u.label}: {kind.lower()} {'queued' if queue else 'ordered'}")

    def objectives(self):
        obj = self.sim.scenario.objectives
        st = self.sim.stats()
        lines = []
        if self.sandbox:
            return ["Sandbox / experiment freely", "No score limits. Change wind with [ ] - +"]
        if obj.get("rescue_all_civilians"):
            lines.append(f"Rescue hikers: {st['civilians']['rescued']} / {st['civilians']['total']} safe")
        if obj.get("max_structures_lost") is not None:
            lines.append(f"Buildings lost: {st['structures_lost']} / {obj['max_structures_lost']} allowed")
        if obj.get("max_area_burned_pct") is not None:
            lines.append(f"Keep burned area below {obj['max_area_burned_pct']}%")
        if obj.get("win_on_contained", True):
            lines.append("Contain flames and cool remaining hot spots")
        return lines

    def draw_menu(self):
        w, h = self.screen.get_size()
        preview = pygame.transform.scale(self.previews[self.mission], (w, h))
        self.screen.blit(preview, (0, 0))
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((9, 18, 21, 222))
        self.screen.blit(shade, (0, 0))
        self.text("B A C K B U R N", 48, 36, 46)
        self.text("W I L D F I R E   C O M M A N D", 51, 98, 15, ORANGE)
        self.text("The wildfire is the opponent.", 48, 155, 32)
        self.text("Read the land. Build your lines. Bring everyone home.", 50, 204, 20, MUTED)
        card_w = (w - 120) // 4
        for i, s in enumerate(self.scenarios):
            x, y = 48 + i * (card_w + 8), 280
            rect = pygame.Rect(x, y, card_w, 260)
            pygame.draw.rect(self.screen, PANEL, rect, border_radius=8)
            self.screen.blit(pygame.transform.scale(self.previews[i], (card_w - 16, 126)), (x + 8, y + 8))
            pygame.draw.rect(self.screen, ORANGE if i == self.mission else LINE, rect, 2, border_radius=8)
            self.text(DIFFICULTIES[i], x + 16, y + 151, 13, ORANGE)
            self.text(s.name, x + 16, y + 177, 24)
            self.wrap(DESCRIPTIONS[i], x + 16, y + 212, card_w - 30, 15)
            self.buttons.append((rect, f"mission:{i}"))
        y = 565
        self.wrap(self.scenarios[self.mission].briefing, 50, y, w - 460, 17)
        self.button("DEPLOY TO INCIDENT", (w - 366, y, 316, 48), "start", True)
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
        best = self.progress.get(MISSIONS[self.mission], {})
        if best:
            self.text(f"Personal best  {best.get('score', 0):,.0f}", w - 360, h - 40, 15, TEAL)

    def draw_map(self):
        scr = self.screen
        scr.set_clip(self.viewport)
        pygame.draw.rect(scr, (28, 43, 40), self.viewport)
        surf = terrain_surface(self.sim, OVERLAYS[self.overlay])
        scaled = pygame.transform.scale(
            surf, (max(1, round(self.sim.grid.w * self.zoom)), max(1, round(self.sim.grid.h * self.zoom)))
        )
        scr.blit(
            scaled,
            (
                self.viewport.x - round(self.cam[0] * self.zoom),
                self.viewport.y - round(self.cam[1] * self.zoom),
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
        for pos, label in [(world.airbase, "AIR BASE"), (world.staging, "STAGING")]:
            x, y = self.to_screen(*pos)
            pygame.draw.rect(scr, (217, 206, 157), (x - 10, y - 10, 20, 20), 2)
            self.text(label, x + 14, y - 8, 13)
        for u in world.units:
            if u.state == ABOARD or not u.alive or u.rescued:
                continue
            pos = self.to_screen(u.x, u.y)
            if u.hose and len(u.hose) > 1:
                pygame.draw.lines(scr, (123, 199, 220), False, [self.to_screen(*p) for p in u.hose], 2)
            if u.uid == self.selected:
                if u.path:
                    pts = [pos] + [self.to_screen(*p) for p in u.path]
                    pygame.draw.lines(scr, (230, 235, 205), False, pts, 1)
                    for p in pts[::3]:
                        pygame.draw.circle(scr, INK, p, 2)
                if u.current and len(u.current.points) >= 2:
                    pygame.draw.lines(scr, ORANGE, False, [self.to_screen(*p) for p in u.current.points], 2)
                radius = u.spec.get("spray_radius", 0)
                if radius:
                    pygame.draw.circle(scr, (147, 210, 219), pos, max(1, int(radius * self.zoom)), 1)
            if not self.viewport.inflate(30, 30).collidepoint(pos):
                continue
            unit_icon(scr, u.utype, pos, tuple(u.spec["color"]), u.uid == self.selected, now, 10)
            if u.uid == self.selected or u.is_civilian:
                self.text("HIKER" if u.is_civilian else f"{u.uid:02d}", pos[0] + 15, pos[1] - 15, 13)
        if self.drag:
            pygame.draw.line(scr, INK, self.to_screen(*self.drag), pygame.mouse.get_pos(), 2)
            self.text(
                "Release to order / Shift to queue", self.viewport.x + 16, self.viewport.bottom - 32, 15
            )
        scr.set_clip(None)
        pygame.draw.rect(scr, LINE, self.viewport, 1)
        return surf

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
        self.text(f"Resources  {money}", x + 18, 225, 17, TEAL)
        self.button(f"Dispatch units  /  {st['pending']} inbound", (x + 18, 255, 262, 34), "dispatch")
        self.text("ON SCENE / SELECT TO COMMAND", x + 18, 309, 13, MUTED)
        roster = [u for u in self.sim.world.units if u.alive and not u.is_civilian and u.state != ABOARD]
        visible = max(2, (h - 650) // 43)
        self.roster_scroll = min(self.roster_scroll, max(0, len(roster) - visible))
        y = 335
        for u in roster[self.roster_scroll : self.roster_scroll + visible]:
            self.button(
                f"{u.uid:02d}  {u.label}",
                (x + 16, y, 266, 38),
                f"unit:{u.uid}",
                active=u.uid == self.selected,
            )
            pygame.draw.circle(self.screen, TEAL if u.state == "WORKING" else ORANGE, (x + 269, y + 19), 3)
            y += 43
        self.text(f"{len(roster)} units / scroll for more", x + 18, y + 2, 13, MUTED)
        y = max(y + 30, h - 337)
        sel = self.sim.world.by_id(self.selected)
        if sel and not sel.is_civilian:
            self.text(sel.label.upper(), x + 18, y, 17, ORANGE)
            self.text(sel.state.replace("_", " ") + f" / {len(sel.orders)} queued", x + 18, y + 26, 13)
            if sel.capacity:
                pygame.draw.rect(self.screen, LINE, (x + 18, y + 52, 180, 5))
                pygame.draw.rect(
                    self.screen, TEAL, (x + 18, y + 52, int(180 * max(0, min(1, sel.tank / sel.capacity))), 5)
                )
                self.text(f"{max(0, sel.tank) / sel.capacity:.0%}", x + 216, y + 43, 13, TEAL)
            if sel.passenger_slots:
                self.text(
                    f"Passengers  {len(sel.passengers)} / {sel.passenger_slots}", x + 18, y + 64, 13, TEAL
                )
            self.button("Auto / Q", (x + 18, y + 89, 84, 30), "order:AUTO", active=self.order_mode == "AUTO")
            self.button("Move / M", (x + 108, y + 89, 84, 30), "order:MOVE", active=self.order_mode == MOVE)
            self.button("Hold", (x + 198, y + 89, 82, 30), "order:HOLD")
        else:
            self.wrap("Select a unit on the map or in the roster to issue orders.", x + 18, y, 260, 17)
        self.screen.blit(pygame.transform.scale(mini, self.minimap.size), self.minimap)
        self.screen.set_clip(self.minimap)
        vr = pygame.Rect(
            self.minimap.x + self.cam[0] / self.sim.grid.w * self.minimap.w,
            self.minimap.y + self.cam[1] / self.sim.grid.h * self.minimap.h,
            self.viewport.w / self.zoom / self.sim.grid.w * self.minimap.w,
            self.viewport.h / self.zoom / self.sim.grid.h * self.minimap.h,
        )
        pygame.draw.rect(self.screen, INK, vr, 1)
        for u in roster:
            pygame.draw.circle(
                self.screen,
                INK,
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
        for i, speed in enumerate((1, 3, 8)):
            self.button(f"{speed}x", (280 + i * 50, y, 44, 32), f"speed:{speed}", active=self.speed == speed)
        if self.sandbox:
            for i, tool in enumerate(("ignite", "water", "terrain")):
                self.button(
                    tool.title(), (448 + i * 85, y, 80, 32), f"tool:{tool}", active=self.editor_tool == tool
                )
            self.button(TERRAIN.names[self.brush], (705, y, 130, 32), "brush")
        else:
            self.text("RIGHT-DRAG  line / drop    SHIFT  queue    WASD  pan", 448, y + 7, 13, MUTED)
        if self.sim.messages:
            msg = self.sim.messages[-1]
            self.wrap(
                f"RADIO  {int(msg.time) // 60:02d}:{int(msg.time) % 60:02d}  {msg.text}",
                20,
                y + 44,
                self.viewport.w - 20,
                15,
                MUTED,
            )
        if self.paused and not self.modal:
            pygame.draw.rect(self.screen, BG, (self.viewport.centerx - 94, 106, 188, 33), border_radius=4)
            self.text("PAUSED / SPACE", self.viewport.centerx - 69, 112, 15, ORANGE)

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
            bottom = self.wrap(self.sim.scenario.briefing, x, y + 94, 644, 20, INK)
            for line in self.objectives():
                bottom = self.wrap("• " + line, x, bottom + 10, 644, 17, TEAL)
            tip = (
                "Select the helicopter, then click a hiker. Hold Shift to queue more pickups. Right-click inside the green rescue zone to unload."
                if self.mission == 1
                else "Select a unit in the roster. Right-click to move or attack. Right-drag a line with crews to cut a firebreak, or with aircraft to drop water. Pause any time to plan."
            )
            self.wrap(tip, x, max(bottom + 25, y + 275), 644, 17)
            self.button("BEGIN OPERATION / ENTER", (x, rect.bottom - 78, 652, 46), "begin", True)
        elif self.modal == "help":
            self.text("FIELD GUIDE", x, y, 32)
            lines = [
                (
                    "SELECT & ORDER",
                    "Left-click a unit. Right-click to act. M forces a move order; Q restores automatic actions.",
                ),
                (
                    "CUT LINES & AIR DROPS",
                    "Right-drag from start to end. Cut teams remove fuel; aircraft lay a strip of water or retardant.",
                ),
                (
                    "RESCUE",
                    "Select a helicopter, click a hiker. Shift-click queues pickups. Right-click the green safe zone to unload.",
                ),
                (
                    "WATER & RESOURCES",
                    "Hose teams need a nearby lake or engine. Vehicles refill automatically. B opens dispatch.",
                ),
                (
                    "CAMERA & SAVES",
                    "Wheel zooms, WASD or middle-drag pans, Home fits map. Space pauses. F6 saves; F7 loads. F5 exports replay.",
                ),
            ]
            yy = y + 65
            for title, body in lines:
                self.text(title, x, yy, 13, ORANGE)
                yy = self.wrap(body, x, yy + 22, 644, 17) + 16
            self.button("Back", (x, rect.bottom - 64, 652, 36), "close")
        elif self.modal == "dispatch":
            self.text("RESOURCE DISPATCH", x, y, 32)
            self.text(
                "Units arrive at staging or the air base after their dispatch delay.", x, y + 48, 15, MUTED
            )
            avail = self.sim.available_units()
            for i, kind in enumerate(avail):
                spec = UNITS[kind]
                col = i % 2
                row = i // 2
                label = f"{spec.get('label', kind)} / ${spec.get('cost', 0):,.0f}"
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
        if self.page == "menu":
            self.draw_menu()
        else:
            self.draw_game()
        self.draw_modal()
        if time.monotonic() < self.toast[1]:
            width = min(self.screen.get_width() - 40, self.fonts[15].size(self.toast[0])[0] + 32)
            rect = pygame.Rect(20, self.screen.get_height() - 48, width, 34)
            pygame.draw.rect(self.screen, (42, 67, 63), rect, border_radius=5)
            self.text(self.toast[0], rect.x + 12, rect.y + 7, 15, INK)

    def update(self, dt):
        if self.page != "game" or self.modal:
            return
        keys = pygame.key.get_pressed()
        self.cam[0] += (keys[pygame.K_d] - keys[pygame.K_a]) * dt * 350 / self.zoom
        self.cam[1] += (keys[pygame.K_s] - keys[pygame.K_w]) * dt * 350 / self.zoom
        if not self.paused and self.sim.outcome == RUNNING:
            self.acc += min(dt, 0.1) * 6 * self.speed
            n = int(self.acc)
            if n:
                self.sim.step(n)
                self.acc -= n
        if self.sim.tick - self.auto_tick >= 180:
            try:
                save_game(self.sim, "autosave.bbsave")
            except OSError:
                pass
            self.auto_tick = self.sim.tick
        if self.sim.outcome != RUNNING and not self.result_saved:
            self.result_saved = True
            self.modal = "result"
            if not self.sandbox:
                key = MISSIONS[self.mission]
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
        if self.page == "game":
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
