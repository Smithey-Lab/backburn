"""Grid pathfinding for ground and water units.

A* (octile heuristic) over an 8-connected grid with per-cell movement cost taken from the
terrain tables (inf = impassable). Diagonal moves cost sqrt(2)× and are refused
when they would cut a corner between two impassable cells. Burning and smoldering
cells are treated as impassable for non-air units.

Deliberately simple. Flow fields come later if unit counts justify them (DECISIONS.md).
"""

from __future__ import annotations

import heapq
import math

import numpy as np

from .fire import BURNING, SMOLDER

_NB = [
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, math.sqrt(2)),
    (1, -1, math.sqrt(2)),
    (-1, 1, math.sqrt(2)),
    (-1, -1, math.sqrt(2)),
]


def build_cost(
    terrain: np.ndarray, state: np.ndarray, cost_row: np.ndarray, avoid_fire: bool = True
) -> np.ndarray:
    cost = cost_row[terrain].astype(np.float32)
    if avoid_fire:
        cost[(state == BURNING) | (state == SMOLDER)] = np.inf
    return cost


def find_path(
    cost: np.ndarray, start: tuple[int, int], goal: tuple[int, int], max_expand: int = 200_000
) -> list[tuple[int, int]] | None:
    """Return list of (x, y) from start (exclusive) to goal (inclusive), or None.

    If the goal itself is impassable, the nearest reachable cell to it is used.
    """
    h, w = cost.shape
    sx, sy = start
    gx, gy = goal
    if not (0 <= sx < w and 0 <= sy < h):
        return None
    # A unit may be standing on an impassable cell (spawned badly, terrain changed under it);
    # it is always allowed to leave.
    gx = min(max(gx, 0), w - 1)
    gy = min(max(gy, 0), h - 1)
    if (sx, sy) == (gx, gy):
        return []

    dist = np.full((h, w), np.inf, np.float32)
    came = np.full((h, w, 2), -1, np.int32)
    dist[sy, sx] = 0.0
    finite = cost[np.isfinite(cost)]
    min_c = float(finite.min()) if finite.size else 1.0

    def heur(x: int, y: int) -> float:  # octile distance × cheapest cost = admissible
        ddx, ddy = abs(gx - x), abs(gy - y)
        return (max(ddx, ddy) + (math.sqrt(2) - 1) * min(ddx, ddy)) * min_c

    pq: list[tuple[float, float, int, int]] = [(heur(sx, sy), 0.0, sx, sy)]
    best_cell, best_d = (sx, sy), math.hypot(gx - sx, gy - sy)
    expanded = 0
    while pq:
        _, d, x, y = heapq.heappop(pq)
        if d > dist[y, x]:
            continue
        if (x, y) == (gx, gy):
            best_cell = (x, y)
            break
        hd = math.hypot(gx - x, gy - y)
        if hd < best_d:
            best_d, best_cell = hd, (x, y)
        expanded += 1
        if expanded > max_expand:
            break
        for dx, dy, step in _NB:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            c = cost[ny, nx]
            if not np.isfinite(c):
                continue
            if dx and dy:  # no corner cutting
                if not (np.isfinite(cost[y, nx]) and np.isfinite(cost[ny, x])):
                    continue
            nd = d + step * float(c)
            if nd < dist[ny, nx]:
                dist[ny, nx] = nd
                came[ny, nx] = (x, y)
                heapq.heappush(pq, (nd + heur(nx, ny), nd, nx, ny))

    # Reconstruct to best_cell.
    path: list[tuple[int, int]] = []
    cx, cy = best_cell
    while (cx, cy) != (sx, sy):
        path.append((cx, cy))
        px, py = came[cy, cx]
        if px < 0:
            return None
        cx, cy = int(px), int(py)
    path.reverse()
    return path


def straight_line(start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[int, int]]:
    """Air units ignore terrain: a single waypoint."""
    return [(int(round(goal[0])), int(round(goal[1])))]
