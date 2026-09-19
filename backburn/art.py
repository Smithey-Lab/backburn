"""Original procedural pixel art. Visual randomness never touches the simulation.

The map is drawn once into a full-size surface (one pixel per cell) and then patched
in place: each tick only the regions the fire model touched, plus any cells crews
changed, are recomputed. Trees, buildings, flames and smoke are drawn per frame from
pre-rendered sprites for the current zoom, restricted to the visible window, and
batched with ``Surface.blits`` so a screen full of forest costs a few milliseconds.
"""

import math

import numpy as np
import pygame

from .config import TerrainType as T
from .fire import BURNING, COLD, SMOLDER
from .render import base_rgb

PALETTE = np.array(
    [
        (41, 88, 110),
        (125, 147, 78),
        (101, 126, 65),
        (54, 93, 61),
        (34, 66, 48),
        (94, 97, 84),
        (151, 141, 111),
        (186, 149, 108),
        (96, 78, 55),
        (175, 163, 114),
    ],
    dtype=np.uint8,
)
FIRE_C = np.array((248, 116, 42), np.float32)
FLICKER_C = np.array((255, 219, 113), np.float32)
COLD_C = np.array((42, 43, 39), np.float32)
SMOLDER_C = np.array((85, 54, 38), np.float32)
WATER_C = np.array([96, 190, 207], np.float32)
RETARDANT_C = np.array([185, 67, 74], np.float32)


def map_rgb(sim, overlay=None, box=None) -> np.ndarray:
    """RGB for the map (or the ``box`` = (y0, y1, x0, x1) part of it) in the game's palette."""
    g = sim.grid
    y0, y1, x0, x1 = box if box is not None else (0, g.h, 0, g.w)
    if overlay:
        return base_rgb(sim, overlay, box)
    terrain = g.terrain[y0:y1, x0:x1]
    state = g.state[y0:y1, x0:x1]
    rgb = PALETTE[terrain].astype(np.float32)
    ys, xs = np.indices(terrain.shape)
    ys += y0
    xs += x0
    grain = ((xs * 37 + ys * 19 + xs * ys * 7) % 13 - 6)[..., None]
    rgb += grain
    rgb[state == COLD] = COLD_C
    rgb[state == SMOLDER] = SMOLDER_C
    fire = state == BURNING
    if fire.any():
        rgb[fire] = FIRE_C
        flicker = fire & ((xs * 7 + ys * 11 + int(g.time)) % 3 == 0)
        rgb[flicker] = FLICKER_C
    water = np.clip(g.water[y0:y1, x0:x1], 0, 1)[..., None] * 0.45
    retardant = np.clip(g.retardant[y0:y1, x0:x1], 0, 1)[..., None] * 0.65
    rgb = rgb * (1 - water) + WATER_C * water
    rgb = rgb * (1 - retardant) + RETARDANT_C * retardant
    return np.clip(rgb, 0, 255).astype(np.uint8)


def terrain_surface(sim, overlay=None):
    """Whole-map surface; used for menu previews and by the map layer's first draw."""
    return pygame.surfarray.make_surface(np.transpose(map_rgb(sim, overlay), (1, 0, 2)))


def thumbnail(terrain: np.ndarray, size=(320, 240)) -> pygame.Surface:
    """A small preview of a terrain array (no simulation needed)."""
    step = max(1, min(terrain.shape[1] // size[0], terrain.shape[0] // size[1]))
    small = terrain[::step, ::step]
    surf = pygame.surfarray.make_surface(np.transpose(PALETTE[small], (1, 0, 2)))
    return pygame.transform.smoothscale(surf, size)


class MapLayer:
    """Full-map surface kept current by patching only what changed."""

    def __init__(self, sim, overlay=None):
        self.sim = sim
        self.overlay = overlay
        self.version = None
        self.surface = terrain_surface(sim, overlay)
        self.version = sim.grid.version
        self.stamp = 0  # bumps whenever the surface changes (cache key for scaled copies)
        sim.grid.changed_cells.clear()
        sim.grid.changed_all = False

    def update(self, sim, overlay=None):
        """Bring the surface up to date with the simulation. Cheap when nothing changed."""
        g = sim.grid
        if sim is not self.sim or overlay != self.overlay or g.changed_all or g.w != self.surface.get_width():
            self.__init__(sim, overlay)
            return self.surface
        if g.version == self.version:
            return self.surface
        boxes = list(g._last_boxes) + list(g._pending)
        if g.changed_cells:
            xs = [c[0] for c in g.changed_cells]
            ys = [c[1] for c in g.changed_cells]
            boxes.append((min(ys), max(ys) + 1, min(xs), max(xs) + 1))
            g.changed_cells.clear()
        if overlay == "elevation":
            boxes = []  # static shading; state changes are not shown in this overlay
        for y0, y1, x0, x1 in boxes:
            y0, x0 = max(0, y0), max(0, x0)
            y1, x1 = min(g.h, y1), min(g.w, x1)
            if y0 >= y1 or x0 >= x1:
                continue
            rgb = map_rgb(sim, overlay, (y0, y1, x0, x1))
            sub = self.surface.subsurface(pygame.Rect(x0, y0, x1 - x0, y1 - y0))
            pygame.surfarray.blit_array(sub, np.transpose(rgb, (1, 0, 2)))
        self.version = g.version
        self.stamp += 1
        return self.surface


def tree_cells(terrain, state):
    """A tree's location depends only on its cell, never the count of surviving trees."""
    ys, xs = np.indices(terrain.shape)
    placement = ((xs * 73) ^ (ys * 151)) % 5 < 2
    return np.nonzero(placement & ((terrain == T.FOREST) | (terrain == T.DENSE_FOREST)) & (state == 0))


class Sprites:
    """Pre-rendered map decorations for a given zoom."""

    def __init__(self):
        self.cache = {}

    def key(self, zoom):
        return int(round(zoom * 4))

    def tree(self, zoom):
        k = ("tree", self.key(zoom))
        if k not in self.cache:
            r = max(2, int(zoom * 0.65))
            surf = pygame.Surface((2 * r + 4, 2 * r + 4), pygame.SRCALPHA)
            cx, cy = r + 2, r + 2
            pygame.draw.line(surf, (47, 56, 39), (cx, cy), (cx, cy + r), 2)
            pygame.draw.polygon(
                surf, (39, 72, 49), [(cx, cy - r), (cx - r, cy + r // 2), (cx + r, cy + r // 2)]
            )
            pygame.draw.line(surf, (69, 107, 64), (cx, cy - r), (cx - r, cy + r // 2))
            self.cache[k] = (surf, cx, cy)
        return self.cache[k]

    def building(self, zoom):
        k = ("bldg", self.key(zoom))
        if k not in self.cache:
            r = max(2, int(zoom * 0.45))
            surf = pygame.Surface((2 * r + 6, 2 * r + 6), pygame.SRCALPHA)
            cx, cy = r + 3, r + 3
            pygame.draw.rect(surf, (43, 49, 40), (cx - r + 2, cy - r + 2, r * 2, r * 2))
            pygame.draw.rect(surf, (209, 190, 151), (cx - r, cy - r, r * 2, r * 2))
            pygame.draw.polygon(surf, (132, 76, 52), [(cx - r - 1, cy), (cx, cy - r - 2), (cx + r + 1, cy)])
            self.cache[k] = (surf, cx, cy)
        return self.cache[k]

    def flame(self, zoom, frame):
        k = ("flame", self.key(zoom), frame)
        if k not in self.cache:
            r = max(2, int(zoom * 0.65))
            rise = frame
            surf = pygame.Surface((2 * r + 4, 2 * r + 8), pygame.SRCALPHA)
            cx, cy = r + 2, r + 5
            pygame.draw.polygon(
                surf,
                (255, 181, 66),
                [(cx - r, cy + r // 2), (cx + 1, cy - r - rise // 3), (cx + r, cy + r // 2)],
            )
            self.cache[k] = (surf, cx, cy)
        return self.cache[k]

    def smoke(self, zoom, frame):
        k = ("smoke", self.key(zoom), frame)
        if k not in self.cache:
            r = max(2, int(zoom * 0.65))
            rad = r + frame // 3
            surf = pygame.Surface((r * 5 + 10, r * 5 + 20), pygame.SRCALPHA)
            pygame.draw.circle(surf, (45, 47, 44, 90), (r * 2 + 5, r * 2 + 5), rad)
            self.cache[k] = (surf, r * 2 - frame, r * 4 + frame)
        return self.cache[k]


SPRITES = Sprites()
MAX_DECOR = 9000  # per frame: trees are thinned beyond this rather than dropping frames


def details(screen, sim, to_screen, zoom, viewport, clock, cam=None):
    """Draw trees, buildings, flames and smoke inside the viewport."""
    g = sim.grid
    ox, oy = to_screen(0, 0)
    # Window of cells that can touch the viewport.
    x0 = max(0, int(math.floor((viewport.left - ox) / zoom)) - 2)
    y0 = max(0, int(math.floor((viewport.top - oy) / zoom)) - 2)
    x1 = min(g.w, int(math.ceil((viewport.right - ox) / zoom)) + 3)
    y1 = min(g.h, int(math.ceil((viewport.bottom - oy) / zoom)) + 3)
    if x0 >= x1 or y0 >= y1:
        return
    terrain = g.terrain[y0:y1, x0:x1]
    state = g.state[y0:y1, x0:x1]
    ys, xs = np.indices(terrain.shape)
    ys += y0
    xs += x0

    def screen_xy(mask):
        sy, sx = np.nonzero(mask)
        gx, gy = xs[sy, sx], ys[sy, sx]
        return gx, gy, np.rint(ox + gx * zoom).astype(int), np.rint(oy + gy * zoom).astype(int)

    clip = screen.get_clip()
    if zoom >= 5:
        placement = ((xs * 73) ^ (ys * 151)) % 5 < 2
        trees = placement & ((terrain == T.FOREST) | (terrain == T.DENSE_FOREST)) & (state == 0)
        _, _, sx, sy = screen_xy(trees)
        if len(sx) > MAX_DECOR:
            keep = np.linspace(0, len(sx) - 1, MAX_DECOR).astype(int)
            sx, sy = sx[keep], sy[keep]
        sprite, cx, cy = SPRITES.tree(zoom)
        screen.blits(
            [(sprite, (int(x) - cx, int(y) - cy)) for x, y in zip(sx.tolist(), sy.tolist())], doreturn=False
        )
        _, _, sx, sy = screen_xy((terrain == T.STRUCTURE) & (state == 0))
        sprite, cx, cy = SPRITES.building(zoom)
        screen.blits(
            [(sprite, (int(x) - cx, int(y) - cy)) for x, y in zip(sx.tolist(), sy.tolist())], doreturn=False
        )
    gx, gy, sx, sy = screen_xy(state == BURNING)
    if len(sx):
        # Every other burning cell gets a flame; every fifth of those a smoke puff.
        gx, gy, sx, sy = gx[::2], gy[::2], sx[::2], sy[::2]
        if len(sx) > MAX_DECOR // 2:
            keep = np.linspace(0, len(sx) - 1, MAX_DECOR // 2).astype(int)
            gx, gy, sx, sy = gx[keep], gy[keep], sx[keep], sy[keep]
        rise = ((clock * 12 + gx * 3 + gy) % 10).astype(int)
        flames = []
        smokes = []
        for x, y, r, fx, fy in zip(sx.tolist(), sy.tolist(), rise.tolist(), gx.tolist(), gy.tolist()):
            sprite, cx, cy = SPRITES.flame(zoom, r)
            flames.append((sprite, (x - cx, y - cy)))
            if (fx + fy) % 5 == 0:
                sprite, dx, dy = SPRITES.smoke(zoom, r)
                smokes.append((sprite, (x - dx, y - dy)))
        screen.blits(flames, doreturn=False)
        screen.blits(smokes, doreturn=False)
    screen.set_clip(clip)


def unit_icon(screen, kind, pos, color, selected=False, clock=0, size=12, heading=None):
    if heading is not None:
        tile = pygame.Surface((72, 72), pygame.SRCALPHA)
        unit_icon(tile, kind, (36, 36), color, selected, clock, size)
        rotated = pygame.transform.rotozoom(tile, -90 - math.degrees(heading), 1)
        screen.blit(rotated, rotated.get_rect(center=pos))
        return
    x, y = map(int, pos)
    r = size
    pygame.draw.ellipse(screen, (27, 34, 30), (x - r + 3, y - r // 2 + 4, r * 2, r))
    if selected:
        pygame.draw.circle(screen, (249, 223, 159), (x, y), r + 7, 2)
        pygame.draw.circle(screen, (48, 60, 48), (x, y), r + 9, 1)
    if kind == "CIVILIAN" or kind in ("CUT_TEAM", "HOSE_TEAM", "HOTSHOTS", "SMOKEJUMPERS"):
        offsets = [0] if kind == "CIVILIAN" else [-5, 5]
        for offset in offsets:
            pygame.draw.line(screen, (33, 44, 44), (x + offset - 2, y + 3), (x + offset - 3, y + 8), 3)
            pygame.draw.line(screen, (33, 44, 44), (x + offset + 2, y + 3), (x + offset + 3, y + 8), 3)
            pygame.draw.rect(screen, color, (x + offset - 3, y - 3, 7, 8), border_radius=2)
            pygame.draw.circle(screen, (243, 206, 141), (x + offset, y - 5), 3)
    elif kind == "HELICOPTER":
        pygame.draw.line(screen, color, (x, y), (x, y + r + 4), 4)
        pygame.draw.line(screen, (211, 219, 199), (x - 5, y + r + 2), (x + 5, y + r + 2), 2)
        pygame.draw.ellipse(screen, color, (x - 5, y - 9, 11, 18))
        pygame.draw.ellipse(screen, (43, 76, 89), (x - 4, y - 8, 9, 6))
        a = clock * 22
        dx, dy = int(math.cos(a) * (r + 5)), int(math.sin(a) * (r + 5))
        pygame.draw.line(screen, (223, 231, 213), (x - dx, y - dy), (x + dx, y + dy), 2)
    elif "BOMBER" in kind or kind == "P3":
        pygame.draw.polygon(
            screen,
            color,
            [
                (x, y - r - 3),
                (x + 3, y - 2),
                (x + r + 5, y + 3),
                (x + r + 5, y + 6),
                (x + 3, y + 3),
                (x + 3, y + r),
                (x + 7, y + r + 3),
                (x - 7, y + r + 3),
                (x - 3, y + r),
                (x - 3, y + 3),
                (x - r - 5, y + 6),
                (x - r - 5, y + 3),
                (x - 3, y - 2),
            ],
        )
        pygame.draw.line(screen, (240, 229, 195), (x, y - r), (x, y + r), 2)
    elif kind == "FIRE_BOAT":
        pygame.draw.polygon(
            screen, (29, 35, 32), [(x - 9, y - 4), (x + 9, y - 4), (x + 6, y + 8), (x - 6, y + 8)]
        )
        pygame.draw.polygon(screen, color, [(x - 7, y - 3), (x + 7, y - 3), (x + 5, y + 6), (x - 5, y + 6)])
        pygame.draw.rect(screen, (223, 222, 183), (x - 3, y - 8, 6, 6))
    else:
        pygame.draw.rect(screen, (29, 35, 32), (x - 8, y - 10, 16, 21), border_radius=3)
        pygame.draw.rect(screen, color, (x - 6, y - 10, 12, 21), border_radius=2)
        pygame.draw.rect(screen, (51, 83, 89), (x - 5, y - 6, 10, 5))
        pygame.draw.rect(screen, (223, 222, 183), (x - 4, y + 3, 8, 5))
        if kind == "BULLDOZER":
            pygame.draw.rect(screen, (214, 177, 87), (x - 11, y - 13, 22, 4))
