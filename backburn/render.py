"""Headless renderer: FireGrid + World → RGB frame → PNG / GIF.

Used for automated verification (an agent can look at its own output) and for
documentation. The interactive viewer (viewer.py) reuses `frame()`.

Colour language follows the brief §7: flat blocky terrain with hard separation,
orange/yellow fire over red-hot cells, dark burn scar, small unit squares with
selection rings, white dotted order lines, visible drop/spray circles.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .config import TERRAIN, UNITS
from .fire import BURNING, COLD, SMOLDER
from .sim import Simulation

FIRE_HOT = np.array([255, 190, 40], np.uint8)
FIRE_MID = np.array([255, 110, 20], np.uint8)
SMOLDER_C = np.array([120, 40, 20], np.uint8)
COLD_C = np.array([48, 44, 44], np.uint8)
WATER_TINT = np.array([80, 160, 255], np.uint8)
RETARD_TINT = np.array([230, 60, 120], np.uint8)


def base_rgb(sim: Simulation) -> np.ndarray:
    g = sim.grid
    rgb = TERRAIN.color[g.terrain].copy()
    st = g.state
    # Burn scar.
    rgb[st == COLD] = COLD_C
    rgb[st == SMOLDER] = SMOLDER_C
    # Fire: flicker by heat and a hash of position so it looks alive frame to frame.
    b = st == BURNING
    if b.any():
        ys, xs = np.nonzero(b)
        flick = ((xs * 7 + ys * 13 + int(g.time * 3)) % 3) == 0
        rgb[ys[flick], xs[flick]] = FIRE_HOT
        rgb[ys[~flick], xs[~flick]] = FIRE_MID
    # Suppression overlays.
    w = np.clip(g.water, 0, 1)[..., None]
    rgb = (rgb * (1 - 0.55 * w) + WATER_TINT * 0.55 * w).astype(np.uint8)
    r = np.clip(g.retardant, 0, 1)[..., None]
    rgb = (rgb * (1 - 0.6 * r) + RETARD_TINT * 0.6 * r).astype(np.uint8)
    return rgb


def frame(sim: Simulation, scale: int = 5, hud: bool = True, selected: int | None = None) -> Image.Image:
    rgb = base_rgb(sim)
    img = Image.fromarray(rgb, "RGB").resize((sim.grid.w * scale, sim.grid.h * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    s = scale

    for u in sim.world.units:
        col = tuple(UNITS[u.utype]["color"])
        cx, cy = u.x * s + s / 2, u.y * s + s / 2
        # Hose line.
        if u.hose:
            pts = [(hx * s + s / 2, hy * s + s / 2) for hx, hy in u.hose]
            d.line(pts, fill=(90, 190, 255), width=max(1, s // 3))
        # Order path (white dotted).
        if u.path:
            pts = [(cx, cy)] + [(px * s + s / 2, py * s + s / 2) for px, py in u.path]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                _dotted(d, x0, y0, x1, y1, s)
        if u.current is not None and u.current.kind == "CUT" and u.current.points:
            pts = [(cx, cy)] + [(px * s + s / 2, py * s + s / 2) for px, py in u.current.points[u.cut_index:]]
            d.line(pts, fill=(255, 230, 120), width=1)
        if u.current is not None and u.current.kind == "DROP" and len(u.current.points) >= 2:
            (x0, y0), (x1, y1) = u.current.points[:2]
            d.line([(x0 * s + s / 2, y0 * s + s / 2), (x1 * s + s / 2, y1 * s + s / 2)],
                   fill=(150, 220, 255), width=max(1, s // 2))
        # Spray circle while working.
        spec = UNITS[u.utype]
        if u.state == "WORKING" and spec.get("spray_radius", 0) > 0:
            r = spec["spray_radius"] * s
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(120, 200, 255))
        # Body.
        half = max(2, s * 0.8)
        if u.is_air:
            d.polygon([(cx, cy - half), (cx + half, cy + half), (cx - half, cy + half)], fill=col, outline=(0, 0, 0))
        else:
            d.rectangle([cx - half, cy - half, cx + half, cy + half], fill=col, outline=(0, 0, 0))
        if selected == u.uid:
            r = half + 3
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=2)
        if u.state == "HOSE_BURNED":
            d.text((cx + half + 2, cy - half), "!", fill=(255, 60, 60))

    # Airbase marker.
    ax, ay = sim.world.airbase
    d.rectangle([ax * s, ay * s, ax * s + 2 * s, ay * s + 2 * s], outline=(255, 255, 255))

    if hud:
        st = sim.stats()
        wx, wy = _wind_arrow(sim.grid.wind_bearing)
        txt = (f"t={st['time']:.0f}s  burning={st['burning']}  burned={st['area_burned_pct']:.1f}%  "
               f"structures lost {st['structures_lost']}/{st['structures_total']}  "
               f"wind {sim.grid.wind_speed:.0f} m/s → {sim.grid.wind_bearing:.0f}°")
        d.rectangle([0, 0, img.width, 14], fill=(0, 0, 0))
        d.text((4, 2), txt, fill=(255, 255, 255))
        # Wind arrow top-right.
        ox, oy = img.width - 18, 8
        d.line([(ox - wx * 6, oy - wy * 6), (ox + wx * 6, oy + wy * 6)], fill=(255, 255, 255), width=2)
    return img


def _dotted(d: ImageDraw.ImageDraw, x0, y0, x1, y1, s: int) -> None:
    L = math.hypot(x1 - x0, y1 - y0)
    n = max(1, int(L / max(2, s)))
    for i in range(n + 1):
        if i % 2:
            continue
        t = i / max(1, n)
        px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        d.point((px, py), fill=(255, 255, 255))


def _wind_arrow(bearing: float) -> tuple[float, float]:
    r = math.radians(bearing)
    return math.sin(r), -math.cos(r)


def record_gif(sim: Simulation, path: str | Path, ticks: int, every: int = 10, scale: int = 4,
               callback=None) -> list[Image.Image]:
    """Run the sim for `ticks`, grabbing a frame every `every` ticks. `callback(sim, tick)`
    can inject commands (used for scripted demos and tests)."""
    frames = [frame(sim, scale)]
    for t in range(ticks):
        if callback is not None:
            callback(sim, sim.tick)
        sim.step()
        if (t + 1) % every == 0:
            frames.append(frame(sim, scale))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=120, loop=0, optimize=False)
    return frames
