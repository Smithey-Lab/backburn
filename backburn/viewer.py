"""Interactive pygame viewer / scratch UI for the Python prototype.

This is a tuning harness, not the shipping UI (that is Godot). It implements the
control scheme from the brief §9 so fire and unit behaviour can be judged by
playing rather than by reading numbers.

Controls
  Left click        select unit (click empty map to deselect)
  Right click       order selected unit: MOVE, or SUPPRESS / HOSE for sprayers
  Right drag        line order for cut crews / dozers (CUT) and aircraft (DROP)
  Shift + order     queue instead of replace
  Mouse wheel       zoom          Middle drag / WASD   pan
  Space             pause         1 / 2 / 3           speed 1× 3× 8×
  [ ]               rotate wind   - =                 wind speed
  I + click         ignite (editor)      E            toggle terrain editor
  0-9 in editor     pick brush (order of terrain.json), left drag paints
  Tab               cycle units   F5                  save replay to replay.json
  Esc               quit
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

try:
    import pygame
except ImportError as e:  # pragma: no cover
    raise SystemExit("pygame is required for the viewer: pip install pygame") from e

from .config import TERRAIN, UNITS
from .render import base_rgb
from .scenario import load_scenario
from .sim import TICKS_PER_SECOND, Simulation
from .units import CUT, DROP, HOSE, MOVE, SUPPRESS

SPEEDS = {pygame.K_1: 1.0, pygame.K_2: 3.0, pygame.K_3: 8.0}


class Viewer:
    def __init__(self, sim: Simulation, win=(1100, 780)):
        pygame.init()
        self.sim = sim
        self.screen = pygame.display.set_mode(win, pygame.RESIZABLE)
        pygame.display.set_caption(f"Backburn — {sim.scenario.name}")
        self.font = pygame.font.SysFont("consolas,dejavusansmono,monospace", 14)
        self.zoom = max(2.0, min(win[0] / sim.grid.w, (win[1] - 60) / sim.grid.h))
        self.cam = [0.0, 0.0]
        self.selected: int | None = None
        self.paused = False
        self.speed = 1.0
        self.acc = 0.0
        self.editor = False
        self.brush = 1
        self.drag_start: tuple[float, float] | None = None
        self.mid_drag: tuple[int, int] | None = None
        self.ignite_mode = False
        self.clock = pygame.time.Clock()
        self.base_surface = None

    # ---- coords -------------------------------------------------------------

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return int((x + 0.5 - self.cam[0]) * self.zoom), int((y + 0.5 - self.cam[1]) * self.zoom) + 40

    def to_world(self, sx: int, sy: int) -> tuple[float, float]:
        return sx / self.zoom + self.cam[0] - 0.5, (sy - 40) / self.zoom + self.cam[1] - 0.5

    # ---- loop ---------------------------------------------------------------

    def run(self, max_frames: int | None = None) -> None:
        frames = 0
        while True:
            dt = self.clock.tick(60) / 1000.0
            for ev in pygame.event.get():
                if self.handle(ev) is False:
                    pygame.quit(); return
            if not self.paused:
                self.acc += dt * TICKS_PER_SECOND * self.speed
                n = int(self.acc)
                if n:
                    self.sim.step(n); self.acc -= n
            self.draw()
            pygame.display.flip()
            frames += 1
            if max_frames is not None and frames >= max_frames:
                pygame.quit(); return

    def handle(self, ev) -> bool | None:
        if ev.type == pygame.QUIT:
            return False
        if ev.type == pygame.KEYDOWN:
            k = ev.key
            if k == pygame.K_ESCAPE:
                return False
            if k == pygame.K_SPACE:
                self.paused = not self.paused
            elif k in SPEEDS:
                self.speed = SPEEDS[k]
            elif k == pygame.K_LEFTBRACKET:
                self.sim.cmd_wind(self.sim.grid.wind_speed, self.sim.grid.wind_bearing - 15)
            elif k == pygame.K_RIGHTBRACKET:
                self.sim.cmd_wind(self.sim.grid.wind_speed, self.sim.grid.wind_bearing + 15)
            elif k == pygame.K_MINUS:
                self.sim.cmd_wind(max(0, self.sim.grid.wind_speed - 1), self.sim.grid.wind_bearing)
            elif k == pygame.K_EQUALS:
                self.sim.cmd_wind(self.sim.grid.wind_speed + 1, self.sim.grid.wind_bearing)
            elif k == pygame.K_e:
                self.editor = not self.editor
            elif k == pygame.K_i:
                self.ignite_mode = True
            elif k == pygame.K_TAB and self.sim.world.units:
                ids = [u.uid for u in self.sim.world.units]
                i = ids.index(self.selected) if self.selected in ids else -1
                self.selected = ids[(i + 1) % len(ids)]
            elif k == pygame.K_F5:
                self.sim.save_replay("replay.json")
            elif self.editor and pygame.K_0 <= k <= pygame.K_9:
                self.brush = min(k - pygame.K_0, len(TERRAIN.names) - 1)
        if ev.type == pygame.KEYUP and ev.key == pygame.K_i:
            self.ignite_mode = False
        if ev.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            wx, wy = self.to_world(mx, my)
            self.zoom = max(1.5, min(24.0, self.zoom * (1.15 if ev.y > 0 else 1 / 1.15)))
            nx, ny = self.to_world(mx, my)
            self.cam[0] += wx - nx; self.cam[1] += wy - ny
        if ev.type == pygame.MOUSEBUTTONDOWN:
            wx, wy = self.to_world(*ev.pos)
            if ev.button == 2:
                self.mid_drag = ev.pos
            elif ev.button == 1:
                if self.ignite_mode:
                    self.sim.cmd_ignite(int(round(wx)), int(round(wy)), 0)
                elif self.editor:
                    self.sim.cmd_paint(int(round(wx)), int(round(wy)), self.brush, 1)
                else:
                    self.selected = self.pick(wx, wy)
            elif ev.button == 3 and self.selected is not None:
                self.drag_start = (wx, wy)
        if ev.type == pygame.MOUSEMOTION:
            if self.mid_drag is not None:
                dx, dy = ev.pos[0] - self.mid_drag[0], ev.pos[1] - self.mid_drag[1]
                self.cam[0] -= dx / self.zoom; self.cam[1] -= dy / self.zoom
                self.mid_drag = ev.pos
            elif self.editor and ev.buttons[0]:
                wx, wy = self.to_world(*ev.pos)
                self.sim.cmd_paint(int(round(wx)), int(round(wy)), self.brush, 1)
        if ev.type == pygame.MOUSEBUTTONUP:
            if ev.button == 2:
                self.mid_drag = None
            elif ev.button == 3 and self.drag_start is not None and self.selected is not None:
                wx, wy = self.to_world(*ev.pos)
                self.issue(self.drag_start, (wx, wy), queue=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
                self.drag_start = None
        keys = pygame.key.get_pressed()
        pan = 12 / self.zoom
        if keys[pygame.K_w]: self.cam[1] -= pan
        if keys[pygame.K_s]: self.cam[1] += pan
        if keys[pygame.K_a]: self.cam[0] -= pan
        if keys[pygame.K_d]: self.cam[0] += pan
        return None

    def pick(self, wx: float, wy: float) -> int | None:
        best, bd = None, 1.6
        for u in self.sim.world.units:
            d = math.hypot(u.x - wx, u.y - wy)
            if d < bd:
                best, bd = u.uid, d
        return best

    def issue(self, p0, p1, queue: bool) -> None:
        u = self.sim.world.by_id(self.selected)
        if u is None:
            return
        action = UNITS[u.utype]["action"]
        dragged = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 1.5
        if action in ("CUT", "DROP") and dragged:
            self.sim.cmd_order(u.uid, CUT if action == "CUT" else DROP, points=[p0, p1], queue=queue)
        elif action == "SPRAY":
            self.sim.cmd_order(u.uid, SUPPRESS, target=p1, queue=queue)
        elif action == "HOSE":
            self.sim.cmd_order(u.uid, HOSE, target=p1, queue=queue)
        else:
            self.sim.cmd_order(u.uid, MOVE, target=p1, queue=queue)

    # ---- draw -----------------------------------------------------------------

    def draw(self) -> None:
        sim = self.sim
        scr = self.screen
        scr.fill((12, 12, 14))
        rgb = base_rgb(sim)
        surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
        w, h = int(sim.grid.w * self.zoom), int(sim.grid.h * self.zoom)
        surf = pygame.transform.scale(surf, (w, h))
        scr.blit(surf, (int(-self.cam[0] * self.zoom), int(-self.cam[1] * self.zoom) + 40))

        z = self.zoom
        for u in sim.world.units:
            col = tuple(UNITS[u.utype]["color"])
            sx, sy = self.to_screen(u.x, u.y)
            if u.hose:
                pts = [self.to_screen(x, y) for x, y in u.hose]
                if len(pts) > 1:
                    pygame.draw.lines(scr, (90, 190, 255), False, pts, max(1, int(z / 3)))
            if u.path:
                pts = [(sx, sy)] + [self.to_screen(x, y) for x, y in u.path]
                for a, b in zip(pts, pts[1:]):
                    self._dotted(a, b)
            if u.current is not None and u.current.kind == CUT and u.current.points:
                pts = [(sx, sy)] + [self.to_screen(*p) for p in u.current.points[u.cut_index:]]
                if len(pts) > 1:
                    pygame.draw.lines(scr, (255, 230, 120), False, pts, 1)
            if u.current is not None and u.current.kind == DROP and len(u.current.points) >= 2:
                a, b = (self.to_screen(*p) for p in u.current.points[:2])
                pygame.draw.line(scr, (150, 220, 255), a, b, max(1, int(z / 2)))
            spec = UNITS[u.utype]
            if u.state == "WORKING" and spec.get("spray_radius", 0) > 0:
                pygame.draw.circle(scr, (120, 200, 255), (sx, sy), int(spec["spray_radius"] * z), 1)
            half = max(3, int(z * 0.8))
            if u.is_air:
                pygame.draw.polygon(scr, col, [(sx, sy - half), (sx + half, sy + half), (sx - half, sy + half)])
            else:
                pygame.draw.rect(scr, col, (sx - half, sy - half, 2 * half, 2 * half))
                pygame.draw.rect(scr, (0, 0, 0), (sx - half, sy - half, 2 * half, 2 * half), 1)
            if u.uid == self.selected:
                pygame.draw.circle(scr, (255, 255, 255), (sx, sy), half + 4, 2)
            if u.state == "HOSE_BURNED":
                scr.blit(self.font.render("!", True, (255, 60, 60)), (sx + half + 2, sy - half - 4))

        # Drag preview.
        if self.drag_start is not None:
            a = self.to_screen(*self.drag_start); b = pygame.mouse.get_pos()
            pygame.draw.line(scr, (255, 255, 255), a, b, 1)

        # HUD.
        st = sim.stats()
        sel = sim.world.by_id(self.selected) if self.selected else None
        line1 = (f"t={st['time']:6.0f}s  {'PAUSED' if self.paused else f'{self.speed:.0f}x'}  "
                 f"burning {st['burning']:4d}  burned {st['area_burned_pct']:5.1f}%  "
                 f"structures lost {st['structures_lost']}/{st['structures_total']}  "
                 f"wind {sim.grid.wind_speed:.0f} m/s → {sim.grid.wind_bearing:.0f}°"
                 f"{'   EDITOR brush=' + TERRAIN.names[self.brush] if self.editor else ''}"
                 f"{'   IGNITE' if self.ignite_mode else ''}")
        line2 = (f"{sel.utype}#{sel.uid}  {sel.state}  {sel.current}  tank {sel.tank:.0f}/{sel.capacity:.0f}"
                 if sel else "click a unit · right-click to order · drag for lines · Space pause · E editor · I ignite")
        pygame.draw.rect(scr, (0, 0, 0), (0, 0, scr.get_width(), 40))
        scr.blit(self.font.render(line1, True, (255, 255, 255)), (6, 4))
        scr.blit(self.font.render(line2, True, (200, 200, 200)), (6, 22))
        # Latest messages, bottom-left.
        y = scr.get_height() - 18
        for _, m in sim.messages[-6:][::-1]:
            scr.blit(self.font.render(m, True, (230, 230, 230)), (6, y)); y -= 16
        # Wind arrow.
        r = math.radians(sim.grid.wind_bearing)
        cx, cy = scr.get_width() - 40, 20
        pygame.draw.line(scr, (255, 255, 255), (cx - math.sin(r) * 14, cy + math.cos(r) * 14),
                         (cx + math.sin(r) * 14, cy - math.cos(r) * 14), 3)

    def _dotted(self, a, b) -> None:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(L / 6))
        for i in range(0, n + 1, 2):
            t = i / n
            self.screen.set_at((int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t)), (255, 255, 255))


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else str(Path(__file__).resolve().parents[1] / "scenarios" / "prairie_fire.json")
    Viewer(Simulation(load_scenario(path))).run()


if __name__ == "__main__":
    main()
