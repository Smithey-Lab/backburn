"""Original procedural pixel art. Visual randomness never touches the simulation."""

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


def terrain_surface(sim, overlay=None):
    g = sim.grid
    if overlay:
        rgb = base_rgb(sim, overlay)
    else:
        rgb = PALETTE[g.terrain].astype(float)
        ys, xs = np.indices(g.terrain.shape)
        grain = ((xs * 37 + ys * 19 + xs * ys * 7) % 13 - 6)[..., None]
        rgb += grain
        rgb[g.state == COLD] = (42, 43, 39)
        rgb[g.state == SMOLDER] = (85, 54, 38)
        fire = g.state == BURNING
        rgb[fire] = (248, 116, 42)
        flicker = fire & ((xs * 7 + ys * 11 + int(g.time)) % 3 == 0)
        rgb[flicker] = (255, 219, 113)
        water = np.clip(g.water, 0, 1)[..., None] * 0.45
        retardant = np.clip(g.retardant, 0, 1)[..., None] * 0.65
        rgb = rgb * (1 - water) + np.array([96, 190, 207]) * water
        rgb = rgb * (1 - retardant) + np.array([185, 67, 74]) * retardant
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))


def details(screen, sim, to_screen, zoom, viewport, clock):
    g = sim.grid
    if zoom >= 5:
        ys, xs = np.nonzero(((g.terrain == T.FOREST) | (g.terrain == T.DENSE_FOREST)) & (g.state == 0))
        for x, y in zip(xs[::3], ys[::3]):
            sx, sy = to_screen(x, y)
            if not viewport.collidepoint(sx, sy):
                continue
            r = max(2, int(zoom * 0.65))
            pygame.draw.line(screen, (47, 56, 39), (sx, sy), (sx, sy + r), 2)
            pygame.draw.polygon(
                screen, (39, 72, 49), [(sx, sy - r), (sx - r, sy + r // 2), (sx + r, sy + r // 2)]
            )
            pygame.draw.line(screen, (69, 107, 64), (sx, sy - r), (sx - r, sy + r // 2))
        ys, xs = np.nonzero((g.terrain == T.STRUCTURE) & (g.state == 0))
        for x, y in zip(xs, ys):
            sx, sy = to_screen(x, y)
            r = max(2, int(zoom * 0.45))
            pygame.draw.rect(screen, (43, 49, 40), (sx - r + 2, sy - r + 2, r * 2, r * 2))
            pygame.draw.rect(screen, (209, 190, 151), (sx - r, sy - r, r * 2, r * 2))
            pygame.draw.polygon(screen, (132, 76, 52), [(sx - r - 1, sy), (sx, sy - r - 2), (sx + r + 1, sy)])
    ys, xs = np.nonzero(g.state == BURNING)
    for x, y in zip(xs[::2], ys[::2]):
        sx, sy = to_screen(x, y)
        if not viewport.collidepoint(sx, sy):
            continue
        r = max(2, int(zoom * 0.65))
        rise = int((clock * 12 + x * 3 + y) % 10)
        pygame.draw.polygon(
            screen,
            (255, 181, 66),
            [(sx - r, sy + r // 2), (sx + 1, sy - r - rise // 3), (sx + r, sy + r // 2)],
        )
        if (x + y) % 5 == 0:
            smoke = pygame.Surface((r * 5 + 10, r * 5 + 20), pygame.SRCALPHA)
            pygame.draw.circle(smoke, (45, 47, 44, 90), (r * 2 + 5, r * 2 + 5), r + rise // 3)
            screen.blit(smoke, (sx - r * 2 + rise, sy - r * 4 - rise))


def unit_icon(screen, kind, pos, color, selected=False, clock=0, size=12):
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
    else:
        pygame.draw.rect(screen, (29, 35, 32), (x - 8, y - 10, 16, 21), border_radius=3)
        pygame.draw.rect(screen, color, (x - 6, y - 10, 12, 21), border_radius=2)
        pygame.draw.rect(screen, (51, 83, 89), (x - 5, y - 6, 10, 5))
        pygame.draw.rect(screen, (223, 222, 183), (x - 4, y + 3, 8, 5))
        if kind == "BULLDOZER":
            pygame.draw.rect(screen, (214, 177, 87), (x - 11, y - 13, 22, 4))
