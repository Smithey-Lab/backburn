"""Headless renderer: FireGrid + World → RGB frame → PNG / GIF.

Used for automated verification (an agent can look at its own output), for the
docs, and by the pygame viewer, which reuses `base_rgb()` for the map layer.

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
from .sim import RUNNING, Simulation
from .units import ABOARD, CUT, DROP, LOST, SAFE

FIRE_HOT = np.array([255, 190, 40], np.uint8)
FIRE_MID = np.array([255, 110, 20], np.uint8)
SMOLDER_C = np.array([120, 40, 20], np.uint8)
COLD_C = np.array([48, 44, 44], np.uint8)
WATER_TINT = np.array([80, 160, 255], np.uint8)
RETARD_TINT = np.array([230, 60, 120], np.uint8)
HEAT_TINT = np.array([255, 80, 0], np.uint8)

OVERLAYS = (None, "heat", "moisture", "elevation")


def base_rgb(sim: Simulation, overlay: str | None = None) -> np.ndarray:
    """Map layer as an (h, w, 3) uint8 array. overlay: None | 'heat' | 'moisture' | 'elevation'."""
    g = sim.grid
    rgb = TERRAIN.color[g.terrain].copy()
    if overlay == "elevation" and np.ptp(g.elevation) > 0:
        e = (g.elevation - g.elevation.min()) / max(1e-6, float(np.ptp(g.elevation)))
        rgb = (rgb * (0.55 + 0.45 * e[..., None])).astype(np.uint8)
    st = g.state
    rgb[st == COLD] = COLD_C
    rgb[st == SMOLDER] = SMOLDER_C
    b = st == BURNING
    if b.any():
        ys, xs = np.nonzero(b)
        flick = ((xs * 7 + ys * 13 + int(g.time * 3)) % 3) == 0
        rgb[ys[flick], xs[flick]] = FIRE_HOT
        rgb[ys[~flick], xs[~flick]] = FIRE_MID
    w = np.clip(g.water, 0, 1)[..., None]
    rgb = (rgb * (1 - 0.55 * w) + WATER_TINT * 0.55 * w).astype(np.uint8)
    r = np.clip(g.retardant, 0, 1)[..., None]
    rgb = (rgb * (1 - 0.6 * r) + RETARD_TINT * 0.6 * r).astype(np.uint8)
    if overlay == "heat":
        e = np.clip(g.exposure / 2.0, 0, 1)[..., None]
        rgb = (rgb * (1 - 0.7 * e) + HEAT_TINT * 0.7 * e).astype(np.uint8)
    elif overlay == "moisture":
        m = np.clip(g.moisture, 0, 1)[..., None]
        rgb = (rgb * (1 - 0.5 * m) + WATER_TINT * 0.5 * m).astype(np.uint8)
    return rgb


def frame(
    sim: Simulation, scale: int = 5, hud: bool = True, selected: int | None = None, overlay: str | None = None
) -> Image.Image:
    rgb = base_rgb(sim, overlay)
    img = Image.fromarray(rgb, "RGB").resize((sim.grid.w * scale, sim.grid.h * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    s = scale

    # Safe zone, staging, airbase.
    if sim.world.safe_zone:
        zx, zy, zr = sim.world.safe_zone
        d.ellipse(
            [(zx - zr) * s, (zy - zr) * s, (zx + zr + 1) * s, (zy + zr + 1) * s],
            outline=(120, 255, 120),
            width=2,
        )
    sx_, sy_ = sim.world.staging
    d.rectangle([sx_ * s, sy_ * s, sx_ * s + 2 * s, sy_ * s + 2 * s], outline=(255, 220, 120))
    ax, ay = sim.world.airbase
    d.rectangle([ax * s, ay * s, ax * s + 2 * s, ay * s + 2 * s], outline=(255, 255, 255))

    for e in sim.grid.embers:
        d.point((e.x1 * s + s / 2, e.y1 * s + s / 2), fill=(255, 220, 120))

    for u in sim.world.units:
        if u.state == ABOARD or (not u.alive and not u.is_civilian):
            continue
        col = tuple(UNITS[u.utype]["color"])
        cx, cy = u.x * s + s / 2, u.y * s + s / 2
        if u.hose:
            pts = [(hx * s + s / 2, hy * s + s / 2) for hx, hy in u.hose]
            if len(pts) > 1:
                d.line(pts, fill=(90, 190, 255), width=max(1, s // 3))
        if u.path:
            pts = [(cx, cy)] + [(px * s + s / 2, py * s + s / 2) for px, py in u.path]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                _dotted(d, x0, y0, x1, y1, s)
        if u.current is not None and u.current.kind == CUT and u.current.points:
            pts = [(cx, cy)] + [
                (px * s + s / 2, py * s + s / 2) for px, py in u.current.points[u.cut_index :]
            ]
            if len(pts) > 1:
                d.line(pts, fill=(255, 230, 120), width=1)
        if u.current is not None and u.current.kind == DROP and len(u.current.points) >= 2:
            (x0, y0), (x1, y1) = u.current.points[:2]
            d.line(
                [(x0 * s + s / 2, y0 * s + s / 2), (x1 * s + s / 2, y1 * s + s / 2)],
                fill=(150, 220, 255),
                width=max(1, s // 2),
            )
        spec = UNITS[u.utype]
        if u.state == "WORKING" and spec.get("spray_radius", 0) > 0:
            r = spec["spray_radius"] * s
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(120, 200, 255))
        half = max(2, s * 0.8)
        if u.is_civilian:
            half = max(2, s * 0.5)
            fill = (90, 90, 90) if u.state == LOST else ((120, 255, 120) if u.state == SAFE else col)
            d.ellipse([cx - half, cy - half, cx + half, cy + half], fill=fill, outline=(0, 0, 0))
        elif u.is_air:
            d.polygon(
                [(cx, cy - half), (cx + half, cy + half), (cx - half, cy + half)], fill=col, outline=(0, 0, 0)
            )
        else:
            d.rectangle([cx - half, cy - half, cx + half, cy + half], fill=col, outline=(0, 0, 0))
        if selected == u.uid:
            r = half + 3
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=2)
        if u.state == "HOSE_BURNED":
            d.text((cx + half + 2, cy - half), "!", fill=(255, 60, 60))
        if u.passengers:
            d.text((cx + half + 2, cy - half), str(len(u.passengers)), fill=(255, 255, 255))

    if hud:
        st = sim.stats()
        civ = st["civilians"]
        txt = (
            f"t={st['time']:.0f}s  burning={st['burning']}  burned={st['area_burned_pct']:.1f}%  "
            f"bldg lost {st['structures_lost']}/{st['structures_total']}  "
        )
        if civ["total"]:
            txt += f"civ {civ['rescued']}/{civ['total']} safe, {civ['lost']} lost  "
        txt += f"wind {sim.grid.wind_speed:.0f} m/s → {sim.grid.wind_bearing:.0f}°"
        if st["outcome"] != RUNNING:
            txt += f"   [{st['outcome'].upper()}]"
        d.rectangle([0, 0, img.width, 14], fill=(0, 0, 0))
        d.text((4, 2), txt, fill=(255, 255, 255))
        wx, wy = _wind_arrow(sim.grid.wind_bearing)
        ox, oy = img.width - 18, 8
        d.line([(ox - wx * 6, oy - wy * 6), (ox + wx * 6, oy + wy * 6)], fill=(255, 255, 255), width=2)
    return img


def _dotted(d: ImageDraw.ImageDraw, x0, y0, x1, y1, s: int) -> None:
    L = math.hypot(x1 - x0, y1 - y0)
    n = max(1, int(L / max(2, s)))
    for i in range(0, n + 1, 2):
        t = i / max(1, n)
        d.point((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t), fill=(255, 255, 255))


def _wind_arrow(bearing: float) -> tuple[float, float]:
    r = math.radians(bearing)
    return math.sin(r), -math.cos(r)


def record_gif(
    sim: Simulation,
    path: str | Path,
    ticks: int,
    every: int = 10,
    scale: int = 4,
    callback=None,
    overlay: str | None = None,
) -> list[Image.Image]:
    """Run the sim for `ticks`, grabbing a frame every `every` ticks. `callback(sim, tick)`
    can inject commands (used for scripted demos and tests). Stops early on an outcome."""
    frames = [frame(sim, scale, overlay=overlay)]
    for t in range(ticks):
        if callback is not None:
            callback(sim, sim.tick)
        if sim.step() == 0:
            break
        if (t + 1) % every == 0:
            frames.append(frame(sim, scale, overlay=overlay))
    frames.append(frame(sim, scale, overlay=overlay))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=120, loop=0, optimize=False)
    return frames
