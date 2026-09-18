"""Interactive pygame viewer / tuning harness for the Python prototype.

This is not the shipping UI (that is Godot). It implements the control scheme from
the brief §9 so fire and unit behaviour can be judged by playing rather than by
reading numbers. Full reference: docs/CONTROLS.md.

Quick reference
  Left click         select unit · click a civilian with an aircraft selected = PICKUP
  Right click        order selected unit (MOVE, or SUPPRESS / HOSE for sprayers; DROPOFF if carrying)
  Right drag         line order: CUT for crews/dozers, DROP for aircraft
  Shift + order      queue instead of replace
  Wheel / middle-drag / WASD   zoom / pan
  Space  1 2 3       pause · speed 1× 3× 8×
  [ ]  - =           wind bearing −/+15° · wind speed −/+1
  B                  buy menu (then a letter to purchase; budget + dispatch delay apply)
  Tab / Esc          cycle selection / clear selection (Esc twice quits)
  O                  cycle overlay: none → heat → moisture → elevation
  E  I  X            terrain editor · ignite mode · extinguish mode (hold, then click)
  0-9 in editor      terrain brush (order of terrain.json)
  F5 / F6 / F7       save replay · save game · load game   (files in the working directory)
  H                  toggle help panel
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
from .render import OVERLAYS, base_rgb
from .scenario import load_scenario
from .sim import RUNNING, TICKS_PER_SECOND, Simulation
from .units import ABOARD, CUT, DROP, DROPOFF, HOSE, LOST, MOVE, PICKUP, SAFE, SUPPRESS

SPEEDS = {pygame.K_1: 1.0, pygame.K_2: 3.0, pygame.K_3: 8.0}
TOP = 44  # HUD height
BUY_KEYS = "abcdefghijklmnopqrstuvwxyz"


class Viewer:
    def __init__(self, sim: Simulation, win=(1180, 820)):
        pygame.init()
        self.sim = sim
        self.screen = pygame.display.set_mode(win, pygame.RESIZABLE)
        pygame.display.set_caption(f"Backburn — {sim.scenario.name}")
        self.font = pygame.font.SysFont("consolas,dejavusansmono,monospace", 14)
        self.big = pygame.font.SysFont("consolas,dejavusansmono,monospace", 28, bold=True)
        self.zoom = max(2.0, min(win[0] / sim.grid.w, (win[1] - TOP - 40) / sim.grid.h))
        self.cam = [0.0, 0.0]
        self.selected: int | None = None
        self.paused = False
        self.speed = 1.0
        self.acc = 0.0
        self.editor = False
        self.brush = 1
        self.mode: str | None = None  # None | ignite | extinguish
        self.drag_start: tuple[float, float] | None = None
        self.mid_drag: tuple[int, int] | None = None
        self.buy_menu = False
        self.show_help = False
        self.overlay_i = 0
        self.clock = pygame.time.Clock()
        self._esc_armed = False
        self.flash: tuple[str, float] = ("", 0.0)

    # ---- coords -------------------------------------------------------------

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return int((x + 0.5 - self.cam[0]) * self.zoom), int((y + 0.5 - self.cam[1]) * self.zoom) + TOP

    def to_world(self, sx: int, sy: int) -> tuple[float, float]:
        return sx / self.zoom + self.cam[0] - 0.5, (sy - TOP) / self.zoom + self.cam[1] - 0.5

    def say(self, text: str) -> None:
        self.flash = (text, pygame.time.get_ticks() / 1000 + 2.5)

    # ---- loop ---------------------------------------------------------------

    def run(self, max_frames: int | None = None) -> None:
        frames = 0
        while True:
            dt = self.clock.tick(60) / 1000.0
            for ev in pygame.event.get():
                if self.handle(ev) is False:
                    pygame.quit()
                    return
            if not self.paused and self.sim.outcome == RUNNING:
                self.acc += dt * TICKS_PER_SECOND * self.speed
                n = int(self.acc)
                if n:
                    self.sim.step(n)
                    self.acc -= n
            self.draw()
            pygame.display.flip()
            frames += 1
            if max_frames is not None and frames >= max_frames:
                pygame.quit()
                return

    def handle(self, ev) -> bool | None:
        if ev.type == pygame.QUIT:
            return False
        if ev.type == pygame.KEYDOWN:
            r = self._key(ev)
            if r is False:
                return False
        if ev.type == pygame.KEYUP and ev.key in (pygame.K_i, pygame.K_x):
            self.mode = None
        if ev.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            wx, wy = self.to_world(mx, my)
            self.zoom = max(1.5, min(24.0, self.zoom * (1.15 if ev.y > 0 else 1 / 1.15)))
            nx, ny = self.to_world(mx, my)
            self.cam[0] += wx - nx
            self.cam[1] += wy - ny
        if ev.type == pygame.MOUSEBUTTONDOWN:
            self._mouse_down(ev)
        if ev.type == pygame.MOUSEMOTION:
            if self.mid_drag is not None:
                dx, dy = ev.pos[0] - self.mid_drag[0], ev.pos[1] - self.mid_drag[1]
                self.cam[0] -= dx / self.zoom
                self.cam[1] -= dy / self.zoom
                self.mid_drag = ev.pos
            elif self.editor and ev.buttons[0] and self.mode is None:
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
        if keys[pygame.K_w]:
            self.cam[1] -= pan
        if keys[pygame.K_s]:
            self.cam[1] += pan
        if keys[pygame.K_a]:
            self.cam[0] -= pan
        if keys[pygame.K_d]:
            self.cam[0] += pan
        return None

    def _key(self, ev) -> bool | None:
        k = ev.key
        sim = self.sim
        if self.buy_menu:
            if k == pygame.K_ESCAPE or k == pygame.K_b:
                self.buy_menu = False
                return None
            name = pygame.key.name(k)
            if len(name) == 1 and name in BUY_KEYS:
                i = BUY_KEYS.index(name)
                avail = sim.available_units()
                if i < len(avail):
                    ok = sim.cmd_spawn(avail[i])
                    self.say(sim.messages[-1].text if sim.messages else ("ordered" if ok else "refused"))
                    self.buy_menu = False
            return None
        if k == pygame.K_ESCAPE:
            if self.selected is not None or self.mode or self.editor or self.show_help:
                self.selected = None
                self.mode = None
                self.editor = False
                self.show_help = False
                self._esc_armed = False
                return None
            if self._esc_armed:
                return False
            self._esc_armed = True
            self.say("press Esc again to quit")
            return None
        self._esc_armed = False
        if k == pygame.K_SPACE:
            self.paused = not self.paused
        elif k in SPEEDS:
            self.speed = SPEEDS[k]
        elif k == pygame.K_LEFTBRACKET:
            sim.cmd_wind(sim.grid.wind_speed, sim.grid.wind_bearing - 15)
        elif k == pygame.K_RIGHTBRACKET:
            sim.cmd_wind(sim.grid.wind_speed, sim.grid.wind_bearing + 15)
        elif k == pygame.K_MINUS:
            sim.cmd_wind(max(0, sim.grid.wind_speed - 1), sim.grid.wind_bearing)
        elif k == pygame.K_EQUALS:
            sim.cmd_wind(sim.grid.wind_speed + 1, sim.grid.wind_bearing)
        elif k == pygame.K_e:
            self.editor = not self.editor
        elif k == pygame.K_i:
            self.mode = "ignite"
        elif k == pygame.K_x:
            self.mode = "extinguish"
        elif k == pygame.K_o:
            self.overlay_i = (self.overlay_i + 1) % len(OVERLAYS)
        elif k == pygame.K_b:
            self.buy_menu = True
        elif k == pygame.K_h:
            self.show_help = not self.show_help
        elif k == pygame.K_TAB:
            ids = [u.uid for u in sim.world.units if not u.is_civilian and u.alive]
            if ids:
                i = ids.index(self.selected) if self.selected in ids else -1
                self.selected = ids[(i + 1) % len(ids)]
        elif k == pygame.K_F5:
            sim.save_replay("replay.json")
            self.say("replay.json written")
        elif k == pygame.K_F6:
            sim.save_state("quicksave.bbsave")
            self.say("quicksave.bbsave written")
        elif k == pygame.K_F7:
            if Path("quicksave.bbsave").exists():
                self.sim = Simulation.load_state("quicksave.bbsave")
                self.selected = None
                self.say("quicksave loaded")
            else:
                self.say("no quicksave.bbsave in this directory")
        elif self.editor and pygame.K_0 <= k <= pygame.K_9:
            self.brush = min(k - pygame.K_0, len(TERRAIN.names) - 1)
        return None

    def _mouse_down(self, ev) -> None:
        sim = self.sim
        if ev.pos[1] < TOP:
            return
        wx, wy = self.to_world(*ev.pos)
        if ev.button == 2:
            self.mid_drag = ev.pos
        elif ev.button == 1:
            if self.mode == "ignite":
                sim.cmd_ignite(int(round(wx)), int(round(wy)), 0)
            elif self.mode == "extinguish":
                sim.cmd_extinguish(int(round(wx)), int(round(wy)), 2)
            elif self.editor:
                sim.cmd_paint(int(round(wx)), int(round(wy)), self.brush, 1)
            else:
                picked = self.pick(wx, wy)
                sel = sim.world.by_id(self.selected)
                target = sim.world.by_id(picked)
                # Aircraft selected + click on a foot unit/civilian = pick up.
                if (
                    sel is not None
                    and target is not None
                    and target.uid != sel.uid
                    and sel.passenger_slots > 0
                    and target.is_foot
                    and target.alive
                    and target.in_vehicle is None
                ):
                    sim.cmd_order(
                        sel.uid,
                        PICKUP,
                        unit_id=target.uid,
                        queue=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT),
                    )
                else:
                    self.selected = picked
        elif ev.button == 3 and self.selected is not None:
            self.drag_start = (wx, wy)

    def pick(self, wx: float, wy: float) -> int | None:
        best, bd = None, 1.6
        for u in self.sim.world.units:
            if not u.alive or u.state == ABOARD:
                continue
            d = math.hypot(u.x - wx, u.y - wy)
            if d < bd:
                best, bd = u.uid, d
        return best

    def issue(self, p0, p1, queue: bool) -> None:
        u = self.sim.world.by_id(self.selected)
        if u is None or u.is_civilian:
            return
        action = UNITS[u.utype]["action"]
        dragged = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 1.5
        if u.passengers and not dragged:
            self.sim.cmd_order(u.uid, DROPOFF, target=p1, queue=queue)
        elif action in ("CUT", "DROP") and dragged:
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
        rgb = base_rgb(sim, OVERLAYS[self.overlay_i])
        surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
        w, h = int(sim.grid.w * self.zoom), int(sim.grid.h * self.zoom)
        surf = pygame.transform.scale(surf, (w, h))
        scr.blit(surf, (int(-self.cam[0] * self.zoom), int(-self.cam[1] * self.zoom) + TOP))
        z = self.zoom

        # Zones.
        if sim.world.safe_zone:
            zx, zy, zr = sim.world.safe_zone
            pygame.draw.circle(scr, (120, 255, 120), self.to_screen(zx, zy), int(zr * z), 2)
        for (px, py), col in ((sim.world.staging, (255, 220, 120)), (sim.world.airbase, (255, 255, 255))):
            sx, sy = self.to_screen(px - 0.5, py - 0.5)
            pygame.draw.rect(scr, col, (sx, sy, int(2 * z), int(2 * z)), 1)
        for e in sim.grid.embers:
            scr.set_at(self.to_screen(e.x1, e.y1), (255, 220, 120))

        for u in sim.world.units:
            if u.state == ABOARD or (not u.alive and not u.is_civilian):
                continue
            col = tuple(UNITS[u.utype]["color"])
            sx, sy = self.to_screen(u.x, u.y)
            if u.hose and len(u.hose) > 1:
                pygame.draw.lines(
                    scr, (90, 190, 255), False, [self.to_screen(x, y) for x, y in u.hose], max(1, int(z / 3))
                )
            if u.path:
                pts = [(sx, sy)] + [self.to_screen(x, y) for x, y in u.path]
                for a, b in zip(pts, pts[1:]):
                    self._dotted(a, b)
            if u.current is not None and u.current.kind == CUT and u.current.points:
                pts = [(sx, sy)] + [self.to_screen(*p) for p in u.current.points[u.cut_index :]]
                if len(pts) > 1:
                    pygame.draw.lines(scr, (255, 230, 120), False, pts, 1)
            if u.current is not None and u.current.kind == DROP and len(u.current.points) >= 2:
                a, b = (self.to_screen(*p) for p in u.current.points[:2])
                pygame.draw.line(scr, (150, 220, 255), a, b, max(1, int(z / 2)))
            spec = UNITS[u.utype]
            if u.state == "WORKING" and spec.get("spray_radius", 0) > 0:
                pygame.draw.circle(scr, (120, 200, 255), (sx, sy), int(spec["spray_radius"] * z), 1)
            half = max(3, int(z * 0.8))
            if u.is_civilian:
                half = max(3, int(z * 0.5))
                fill = (90, 90, 90) if u.state == LOST else ((120, 255, 120) if u.state == SAFE else col)
                pygame.draw.circle(scr, fill, (sx, sy), half)
                pygame.draw.circle(scr, (0, 0, 0), (sx, sy), half, 1)
            elif u.is_air:
                pygame.draw.polygon(
                    scr, col, [(sx, sy - half), (sx + half, sy + half), (sx - half, sy + half)]
                )
            else:
                pygame.draw.rect(scr, col, (sx - half, sy - half, 2 * half, 2 * half))
                pygame.draw.rect(scr, (0, 0, 0), (sx - half, sy - half, 2 * half, 2 * half), 1)
            if u.uid == self.selected:
                pygame.draw.circle(scr, (255, 255, 255), (sx, sy), half + 4, 2)
            if u.state == "HOSE_BURNED":
                scr.blit(self.font.render("!", True, (255, 60, 60)), (sx + half + 2, sy - half - 4))
            if u.passengers:
                scr.blit(
                    self.font.render(str(len(u.passengers)), True, (255, 255, 255)),
                    (sx + half + 2, sy - half - 4),
                )

        if self.drag_start is not None:
            pygame.draw.line(
                scr, (255, 255, 255), self.to_screen(*self.drag_start), pygame.mouse.get_pos(), 1
            )

        self._hud()
        if self.buy_menu:
            self._buy_panel()
        if self.show_help:
            self._help_panel()
        if sim.outcome != RUNNING:
            self._banner(f"{sim.outcome.upper()} — {sim.outcome_reason}   score {sim.score()['total']:.0f}")

    def _hud(self) -> None:
        sim, scr = self.sim, self.screen
        st = sim.stats()
        civ = st["civilians"]
        sel = sim.world.by_id(self.selected) if self.selected else None
        ov = OVERLAYS[self.overlay_i]
        budget = "" if sim.budget is None else f"  budget {sim.budget - sim.spent:.0f}/{sim.budget:.0f}"
        line1 = (
            f"t={st['time']:6.0f}s {'PAUSED' if self.paused else f'{self.speed:.0f}x':>6}  "
            f"burning {st['burning']:4d}  burned {st['area_burned_pct']:5.1f}%  "
            f"bldg lost {st['structures_lost']}/{st['structures_total']}"
            + (f"  civ {civ['rescued']}/{civ['total']} safe {civ['lost']} lost" if civ["total"] else "")
            + f"  wind {sim.grid.wind_speed:.0f} m/s → {sim.grid.wind_bearing:.0f}°{budget}"
            + (f"  incoming {st['pending']}" if st["pending"] else "")
            + (f"  [overlay: {ov}]" if ov else "")
            + (f"  [EDITOR: {TERRAIN.names[self.brush]}]" if self.editor else "")
            + (f"  [{self.mode.upper()}]" if self.mode else "")
        )
        if sel:
            line2 = (
                f"{sel.label}  {sel.state}  {sel.current}  "
                + (f"tank {sel.tank:.0f}/{sel.capacity:.0f}  " if sel.capacity else "")
                + (f"aboard {len(sel.passengers)}/{sel.passenger_slots}  " if sel.passenger_slots else "")
                + (f"queued {len(sel.orders)}" if sel.orders else "")
            )
        else:
            line2 = "L-click select · R-click order · R-drag line · B buy · H help · Space pause"
        pygame.draw.rect(scr, (0, 0, 0), (0, 0, scr.get_width(), TOP))
        scr.blit(self.font.render(line1, True, (255, 255, 255)), (6, 4))
        scr.blit(self.font.render(line2, True, (200, 200, 200)), (6, 24))
        y = scr.get_height() - 18
        for m in sim.messages[-7:][::-1]:
            colr = {"objective": (255, 200, 80), "event": (140, 220, 255), "system": (200, 200, 200)}.get(
                m.kind, (230, 230, 230)
            )
            scr.blit(self.font.render(f"{m.time:5.0f}s  {m.text}", True, colr), (6, y))
            y -= 16
        text, until = self.flash
        if text and pygame.time.get_ticks() / 1000 < until:
            scr.blit(self.font.render(text, True, (255, 255, 160)), (scr.get_width() // 2 - 200, TOP + 6))
        r = math.radians(sim.grid.wind_bearing)
        cx, cy = scr.get_width() - 40, 22
        pygame.draw.line(
            scr,
            (255, 255, 255),
            (cx - math.sin(r) * 14, cy + math.cos(r) * 14),
            (cx + math.sin(r) * 14, cy - math.cos(r) * 14),
            3,
        )

    def _buy_panel(self) -> None:
        sim = self.sim
        avail = sim.available_units()
        lines = ["BUY  (letter to order, B/Esc to close)"]
        for i, t in enumerate(avail):
            spec = UNITS[t]
            ok = sim.can_afford(t)
            lines.append(
                f"{BUY_KEYS[i]}  {spec.get('label', t):<18} {spec.get('cost', 0):>6.0f}  "
                f"eta {spec.get('arrival_seconds', 0):>4.0f}s  {'' if ok else '(no budget)'}"
            )
        self._panel(lines, 60, TOP + 20)

    def _help_panel(self) -> None:
        lines = [line.strip() for line in __doc__.split("Quick reference")[1].strip().splitlines()]
        self._panel(["HELP  (H to close)"] + lines, self.screen.get_width() - 560, TOP + 20)

    def _panel(self, lines: list[str], x: int, y: int) -> None:
        w = max(self.font.size(line)[0] for line in lines) + 20
        h = 18 * len(lines) + 12
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        s.fill((0, 0, 0, 200))
        self.screen.blit(s, (x, y))
        for i, line in enumerate(lines):
            self.screen.blit(
                self.font.render(line, True, (255, 255, 255) if i else (255, 220, 120)),
                (x + 10, y + 6 + 18 * i),
            )

    def _banner(self, text: str) -> None:
        scr = self.screen
        surf = self.big.render(text, True, (255, 255, 255))
        bg = pygame.Surface((surf.get_width() + 40, surf.get_height() + 24), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 210))
        x = scr.get_width() // 2 - bg.get_width() // 2
        y = scr.get_height() // 2 - bg.get_height() // 2
        scr.blit(bg, (x, y))
        scr.blit(surf, (x + 20, y + 12))

    def _dotted(self, a, b) -> None:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(L / 6))
        for i in range(0, n + 1, 2):
            t = i / n
            self.screen.set_at(
                (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t)), (255, 255, 255)
            )


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else str(Path(__file__).resolve().parents[1] / "scenarios" / "prairie_fire.json")
    Viewer(Simulation(load_scenario(path))).run()


if __name__ == "__main__":
    main()
