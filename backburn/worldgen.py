"""World generator for desktop-sized maps (terrain mode ``"world"``).

The original ``generate_terrain`` stretches one noise lattice across the whole map, so a
bigger map just means bigger blobs. This generator keeps *feature size* constant
(``feature_cells`` per lattice interval) and instead fills a bigger map with more of
everything: several lakes, an optional meandering river with bridges, highways that
cross the map, gravel spurs, towns of separate buildings and outlying ranches.

Everything is seeded and deterministic (one ``numpy.random.Generator``, consumed in a
fixed order), so a saved seed reproduces the same map for replays and random incidents.
Scale: one cell is roughly 3 m, so a 3-cell road is a two-lane road and a 4×4 building
is a house.

Placement helpers at the bottom pick staging areas, ignition points and civilians for
authored missions and for the Random Incident mode. They are also deterministic.
"""

from __future__ import annotations

import json
import math

import numpy as np

from .config import TERRAIN, MoveClass
from .config import TerrainType as T

MAX_BRIDGE = 16  # a road may cross this many water cells (a river); more is a lake


# ---- noise ------------------------------------------------------------------------------


def _noise(rng: np.random.Generator, h: int, w: int, base_y: int, base_x: int, octaves: int) -> np.ndarray:
    """Value noise with a per-axis lattice so features keep the same size on any map."""
    out = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        gh, gw = base_y * (2**o) + 1, base_x * (2**o) + 1
        g = rng.random((gh, gw), dtype=np.float32)
        ys = np.linspace(0, gh - 1, h, dtype=np.float32)
        xs = np.linspace(0, gw - 1, w, dtype=np.float32)
        y0 = np.minimum(np.floor(ys).astype(int), gh - 2)
        x0 = np.minimum(np.floor(xs).astype(int), gw - 2)
        fy = (ys - y0)[:, None]
        fx = (xs - x0)[None, :]
        fy = fy * fy * (3 - 2 * fy)
        fx = fx * fx * (3 - 2 * fx)
        a = g[y0][:, x0]
        b = g[y0][:, x0 + 1]
        c = g[y0 + 1][:, x0]
        d = g[y0 + 1][:, x0 + 1]
        out += ((a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy) * amp
        total += amp
        amp *= 0.5
    return out / total


def _lattice(size: int, feature_cells: float) -> int:
    return max(2, int(round(size / max(8.0, feature_cells))))


def _dilate8(m: np.ndarray, times: int = 1) -> np.ndarray:
    out = m.copy()
    for _ in range(times):
        n = out.copy()
        n[1:, :] |= out[:-1, :]
        n[:-1, :] |= out[1:, :]
        n[:, 1:] |= out[:, :-1]
        n[:, :-1] |= out[:, 1:]
        n[1:, 1:] |= out[:-1, :-1]
        n[1:, :-1] |= out[:-1, 1:]
        n[:-1, 1:] |= out[1:, :-1]
        n[:-1, :-1] |= out[1:, 1:]
        out = n
    return out


def _dilate4(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


# ---- line rasterisation ---------------------------------------------------------------------


def _polyline_cells(pts: list[tuple[float, float]]) -> list[tuple[int, int]]:
    """Cells under a polyline, in order, without repeats (8-connected)."""
    cells: list[tuple[int, int]] = []
    last = None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(max(abs(x1 - x0), abs(y1 - y0)))))
        for i in range(n + 1):
            t = i / n
            c = (int(round(x0 + (x1 - x0) * t)), int(round(y0 + (y1 - y0) * t)))
            if c != last:
                cells.append(c)
                last = c
    return cells


def _draw_road(t: np.ndarray, pts, width: int, kind: int, bridges: bool = True) -> None:
    """Rasterise a road of ``width`` cells; short water crossings become bridges."""
    h, w = t.shape
    cells = [(x, y) for x, y in _polyline_cells(pts) if 0 <= x < w and 0 <= y < h]
    if not cells:
        return
    water = t == int(T.WATER)
    keep = np.zeros(len(cells), bool)
    bridge_ok = np.zeros((h, w), bool)
    i = 0
    while i < len(cells):
        x, y = cells[i]
        if not water[y, x]:
            keep[i] = True
            i += 1
            continue
        j = i
        while j < len(cells) and water[cells[j][1], cells[j][0]]:
            j += 1
        run = j - i
        if bridges and run <= MAX_BRIDGE and i > 0 and j < len(cells):
            for k in range(i, j):
                keep[k] = True
                bridge_ok[cells[k][1], cells[k][0]] = True
        i = j
    line = np.zeros((h, w), bool)
    for k, (x, y) in enumerate(cells):
        if keep[k]:
            line[y, x] = True
    if width >= 3:
        mask = _dilate8(line, (width - 1) // 2)
        bridge_ok = _dilate8(bridge_ok, (width - 1) // 2)
    elif width == 2:
        mask = line.copy()
        mask[1:, :] |= line[:-1, :]
        mask[:, 1:] |= line[:, :-1]
        mask[1:, 1:] |= line[:-1, :-1]
        b = bridge_ok.copy()
        b[1:, :] |= bridge_ok[:-1, :]
        b[:, 1:] |= bridge_ok[:, :-1]
        b[1:, 1:] |= bridge_ok[:-1, :-1]
        bridge_ok = b
    else:
        mask = line
    paint = mask & (~water | bridge_ok) & (t != int(T.STRUCTURE))
    t[paint] = kind


# ---- the generator -------------------------------------------------------------------------


_WORLD_CACHE: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}


def generate_world(w: int, h: int, seed: int, **params) -> np.ndarray:
    """Seeded terrain for a large map. Every parameter is a design dial (docs/SCENARIO_FORMAT.md)."""
    return generate_world_pair(w, h, seed, **params)[0].copy()


def generate_world_pair(w: int, h: int, seed: int, **params) -> tuple[np.ndarray, np.ndarray]:
    """Terrain and matching elevation (metres). Cached so a scenario builds both in one pass."""
    key = (w, h, seed, json.dumps(params, sort_keys=True))
    hit = _WORLD_CACHE.get(key)
    if hit is None:
        if len(_WORLD_CACHE) >= 16:
            _WORLD_CACHE.pop(next(iter(_WORLD_CACHE)))
        hit = _generate(w, h, seed, **params)
        _WORLD_CACHE[key] = hit
    return hit


def _generate(
    w: int,
    h: int,
    seed: int,
    water_level: float = 0.28,
    shrub_level: float = 0.45,
    forest_level: float = 0.52,
    dense_level: float = 0.68,
    feature_cells: float = 96.0,
    lake: bool = True,
    river: bool = False,
    river_width: int = 4,
    highways: int = 2,
    spurs: int = 4,
    towns: int = 1,
    town_size: int = 12,
    town_radius: int = 34,
    ranches: int = 8,
    road_width: int = 3,
    relief: float = 0.0,
    canyon: bool = False,
    canyon_grade: float = 0.3,
    town_centers: list | None = None,
    shore_cabins: int = 0,
    roads: list | None = None,
    **_: object,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    by, bx = _lattice(h, feature_cells), _lattice(w, feature_cells)
    n = _noise(rng, h, w, by, bx, 4)  # low spots hold lakes and, with relief, valleys
    veg = _noise(rng, h, w, max(2, by * 2 // 3), max(2, bx * 2 // 3), 3)
    t = np.full((h, w), int(T.GRASS), np.uint8)
    t[veg > shrub_level] = int(T.SHRUB)
    t[veg > forest_level] = int(T.FOREST)
    t[veg > dense_level] = int(T.DENSE_FOREST)

    hills = (n - n.min()) / max(1e-6, float(n.max() - n.min()))
    elevation = (hills * relief).astype(np.float32)
    if lake:
        water = n < water_level
        t[water] = int(T.WATER)
    if river or canyon:
        line_dist = _draw_river(t, rng, river_width if river else 0)
        if canyon:
            # V-shaped valley: the river runs along the floor, walls climb at canyon_grade.
            cell_m = 10.0
            walls = np.minimum(line_dist, 0.22 * max(w, h))  # the rim levels off into a plateau
            elevation = (walls * cell_m * canyon_grade + hills * relief * 0.35).astype(np.float32)
    water = t == int(T.WATER)
    if water.any():
        fringe = _dilate4(water) & ~water
        t[fringe] = int(T.SAND)
    near_water = _dilate8(water, 6)

    def land_point(x: float, y: float) -> tuple[float, float]:
        """Nudge a waypoint off water so highways only cross rivers, not lakes."""
        xi, yi = int(min(max(x, 0), w - 1)), int(min(max(y, 0), h - 1))
        if not near_water[yi, xi]:
            return float(xi), float(yi)
        for r in range(4, 80, 4):
            for _ in range(6):
                cx = int(min(max(xi + rng.integers(-r, r + 1), 0), w - 1))
                cy = int(min(max(yi + rng.integers(-r, r + 1), 0), h - 1))
                if not near_water[cy, cx]:
                    return float(cx), float(cy)
        return float(xi), float(yi)

    for polyline in roads or []:  # authored highways, drawn first so towns can sit on them
        _draw_road(t, [(float(x), float(y)) for x, y in polyline], road_width, int(T.ROAD))
    for k in range(highways):
        horizontal = (k + int(rng.random() < 0.5)) % 2 == 0
        if horizontal:
            y = float(rng.integers(h // 6, 5 * h // 6))
            pts = [(-2.0, y)]
            for frac in np.linspace(0.2, 0.8, 3):
                pts.append(
                    land_point(frac * w + rng.integers(-w // 12, w // 12), y + rng.integers(-h // 5, h // 5))
                )
            pts.append((w + 2.0, pts[-1][1]))
        else:
            x = float(rng.integers(w // 6, 5 * w // 6))
            pts = [(x, -2.0)]
            for frac in np.linspace(0.2, 0.8, 3):
                pts.append(
                    land_point(x + rng.integers(-w // 5, w // 5), frac * h + rng.integers(-h // 12, h // 12))
                )
            pts.append((pts[-1][0], h + 2.0))
        _draw_road(t, pts, road_width, int(T.ROAD))

    road_cells = np.argwhere(t == int(T.ROAD))
    centers: list[tuple[int, int]] = []
    for cx, cy in town_centers or []:
        # Authored town: snap to the nearest road, or lay a gravel spur to it from the road.
        near = road_cell_near(t, cx, cy, (T.ROAD,))
        if near is not None and math.hypot(near[0] - cx, near[1] - cy) <= 40:
            centers.append(near)
        else:
            if near is not None:
                _draw_road(t, [(float(near[0]), float(near[1])), (float(cx), float(cy))], 2, int(T.GRAVEL))
            centers.append((int(cx), int(cy)))
    if len(road_cells):
        for _ in range(max(0, towns - len(centers))):
            for _try in range(60):
                ry, rx = road_cells[rng.integers(len(road_cells))]
                if near_water[ry, rx] or not (
                    town_radius < rx < w - town_radius and town_radius < ry < h - town_radius
                ):
                    continue
                if all(math.hypot(rx - cx, ry - cy) > 3 * town_radius for cx, cy in centers):
                    centers.append((int(rx), int(ry)))
                    break
        for _ in range(spurs):
            ry, rx = road_cells[rng.integers(len(road_cells))]
            angle = rng.random() * 2 * math.pi
            length = float(rng.integers(w // 10, w // 4))
            end = land_point(rx + math.cos(angle) * length, ry + math.sin(angle) * length)
            mid = land_point(
                (rx + end[0]) / 2 + rng.integers(-20, 21), (ry + end[1]) / 2 + rng.integers(-20, 21)
            )
            _draw_road(t, [(float(rx), float(ry)), mid, end], 2, int(T.GRAVEL), bridges=False)

    for cx, cy in centers:
        for _ in range(int(rng.integers(2, 4))):
            angle = rng.random() * 2 * math.pi
            length = float(rng.integers(town_radius // 2, town_radius))
            end = (cx + math.cos(angle) * length, cy + math.sin(angle) * length)
            _draw_road(t, [(float(cx), float(cy)), end], 2, int(T.GRAVEL), bridges=False)
        _place_buildings(t, rng, cx, cy, town_radius, town_size, near_water)

    road_like = np.argwhere((t == int(T.ROAD)) | (t == int(T.GRAVEL)))
    placed = 0
    tries = 0
    while placed < ranches and tries < ranches * 40 and len(road_like):
        tries += 1
        ry, rx = road_like[rng.integers(len(road_like))]
        if any(math.hypot(rx - cx, ry - cy) < town_radius * 1.5 for cx, cy in centers):
            continue
        if _place_building(t, rng, int(rx), int(ry), 5, 10, near_water):
            placed += 1
    shore = np.argwhere(t == int(T.SAND))
    placed, tries = 0, 0
    while placed < shore_cabins and tries < shore_cabins * 60 and len(shore):
        tries += 1
        sy, sx = shore[rng.integers(len(shore))]
        if _place_building(t, rng, int(sx), int(sy), 2, 7, near_water):
            placed += 1
    return t, elevation


def _draw_river(t: np.ndarray, rng: np.random.Generator, width: int) -> np.ndarray:
    """Meandering river ``width`` cells wide (0 = only compute the line). Returns the
    across-map distance of every cell from the river line, used for canyon walls."""
    h, w = t.shape
    vertical = rng.random() < 0.5
    length = h if vertical else w
    span = w if vertical else h
    phase = rng.random(3) * 2 * math.pi
    amp = span * np.array([0.18, 0.07, 0.03])
    lam = length * np.array([0.9, 0.35, 0.12])
    base = span * (0.3 + 0.4 * rng.random())
    s = np.arange(length, dtype=np.float32)
    off = base + sum(a * np.sin(2 * math.pi * s / L + p) for a, L, p in zip(amp, lam, phase))
    off = np.clip(off, 4, span - 5)
    if vertical:
        dist = np.abs(np.arange(w, dtype=np.float32)[None, :] - off[:, None])
    else:
        dist = np.abs(np.arange(h, dtype=np.float32)[:, None] - off[None, :])
    if width <= 0:
        return dist
    line = np.zeros((h, w), bool)
    if vertical:
        line[s.astype(int), off.astype(int)] = True
    else:
        line[off.astype(int), s.astype(int)] = True
    mask = _dilate8(line, max(0, (width - 1) // 2))
    if width % 2 == 0:
        m2 = mask.copy()
        m2[:, 1:] |= mask[:, :-1]
        m2[1:, :] |= mask[:-1, :]
        mask = m2
    t[mask] = int(T.WATER)
    return dist


def _place_building(
    t: np.ndarray, rng: np.random.Generator, rx: int, ry: int, min_off: int, max_off: int, near_water
) -> bool:
    h, w = t.shape
    sw, sh = int(rng.integers(3, 7)), int(rng.integers(3, 6))
    ox, oy = int(rng.integers(-max_off, max_off + 1)), int(rng.integers(-max_off, max_off + 1))
    if abs(ox) < min_off and abs(oy) < min_off:
        return False
    x, y = rx + ox, ry + oy
    if not (2 <= x < w - sw - 2 and 2 <= y < h - sh - 2):
        return False
    blk = t[y - 1 : y + sh + 1, x - 1 : x + sw + 1]
    if np.isin(blk, [int(T.WATER), int(T.ROAD), int(T.GRAVEL), int(T.STRUCTURE), int(T.SAND)]).any():
        return False
    t[y : y + sh, x : x + sw] = int(T.STRUCTURE)
    return True


def _place_buildings(t, rng, cx, cy, radius, count, near_water) -> int:
    anchors = np.argwhere((t == int(T.ROAD)) | (t == int(T.GRAVEL)))
    if not len(anchors):
        return 0
    d = np.hypot(anchors[:, 1] - cx, anchors[:, 0] - cy)
    anchors = anchors[d <= radius]
    placed, tries = 0, 0
    while placed < count and tries < count * 40 and len(anchors):
        tries += 1
        ay, ax = anchors[rng.integers(len(anchors))]
        if _place_building(t, rng, int(ax), int(ay), 3, 8, near_water):
            placed += 1
    return placed


def generate_world_elevation(
    w: int, h: int, seed: int, relief: float = 60.0, feature_cells: float = 160.0, octaves: int = 3
) -> np.ndarray:
    """Rolling terrain in metres with constant hill size; pairs with ``generate_world``."""
    rng = np.random.default_rng(seed)
    n = _noise(rng, h, w, _lattice(h, feature_cells), _lattice(w, feature_cells), octaves)
    n = (n - n.min()) / max(1e-6, float(n.max() - n.min()))
    return (n * relief).astype(np.float32)


# ---- placement helpers ---------------------------------------------------------------------


def cells_of(t: np.ndarray, kinds) -> np.ndarray:
    """(y, x) rows of every cell whose terrain is in ``kinds``."""
    return np.argwhere(np.isin(t, [int(k) for k in kinds]))


def passable_near(t: np.ndarray, x: float, y: float, mc: MoveClass = MoveClass.OFFROAD, max_r: int = 40):
    """Nearest cell the movement class can stand on."""
    cost = TERRAIN.cost_for(mc)
    h, w = t.shape
    cx, cy = int(min(max(round(x), 0), w - 1)), int(min(max(round(y), 0), h - 1))
    if math.isfinite(cost[t[cy, cx]]):
        return cx, cy
    for r in range(1, max_r + 1):
        y0, y1 = max(0, cy - r), min(h, cy + r + 1)
        x0, x1 = max(0, cx - r), min(w, cx + r + 1)
        sub = np.isfinite(cost[t[y0:y1, x0:x1]])
        if sub.any():
            ys, xs = np.nonzero(sub)
            k = int(np.argmin((ys + y0 - cy) ** 2 + (xs + x0 - cx) ** 2))
            return int(xs[k] + x0), int(ys[k] + y0)
    return cx, cy


def road_cell_near(t: np.ndarray, x: float, y: float, kinds=(T.ROAD, T.GRAVEL)) -> tuple[int, int] | None:
    rc = cells_of(t, kinds)
    if not len(rc):
        return None
    k = int(np.argmin((rc[:, 1] - x) ** 2 + (rc[:, 0] - y) ** 2))
    return int(rc[k][1]), int(rc[k][0])


def fuel_cell_near(t: np.ndarray, rng: np.random.Generator, x: float, y: float, radius: float, kinds=None):
    """Random burnable cell within ``radius`` of (x, y), or the nearest one if none."""
    kinds = kinds or (T.GRASS, T.SHRUB, T.FOREST)
    fc = cells_of(t, kinds)
    if not len(fc):
        return None
    d = np.hypot(fc[:, 1] - x, fc[:, 0] - y)
    inside = np.flatnonzero(d <= radius)
    if len(inside):
        k = int(inside[rng.integers(len(inside))])
    else:
        k = int(np.argmin(d))
    return int(fc[k][1]), int(fc[k][0])


def structure_centroid(t: np.ndarray) -> tuple[float, float] | None:
    sc = cells_of(t, (T.STRUCTURE,))
    if not len(sc):
        return None
    return float(sc[:, 1].mean()), float(sc[:, 0].mean())


def upwind_point(x: float, y: float, bearing: float, distance: float) -> tuple[float, float]:
    """A point ``distance`` cells upwind of (x, y) for a wind blowing toward ``bearing``."""
    r = math.radians(bearing)
    return x - math.sin(r) * distance, y + math.cos(r) * distance


def town_center(t: np.ndarray, radius: int = 40) -> tuple[int, int] | None:
    """Centre of the densest cluster of buildings (the 'town'), or None without structures."""
    sc = cells_of(t, (T.STRUCTURE,))
    if not len(sc):
        return None
    sample = sc[:: max(1, len(sc) // 400)]
    ys, xs = sc[:, 0], sc[:, 1]
    best, best_n = None, -1
    for y, x in sample:
        n = int(((xs - x) ** 2 + (ys - y) ** 2 <= radius * radius).sum())
        if n > best_n:
            best_n, best = n, (int(x), int(y))
    return best


def edge_road_cell(t: np.ndarray, side: str, kinds=(T.ROAD,)) -> tuple[int, int] | None:
    """Road cell closest to one map edge ('W', 'E', 'N' or 'S'), a natural staging area."""
    rc = cells_of(t, kinds)
    if not len(rc):
        return None
    h, w = t.shape
    score = {"W": rc[:, 1], "E": w - 1 - rc[:, 1], "N": rc[:, 0], "S": h - 1 - rc[:, 0]}[side]
    # Prefer cells near the edge but away from the corners.
    mid = {"W": rc[:, 0] - h / 2, "E": rc[:, 0] - h / 2, "N": rc[:, 1] - w / 2, "S": rc[:, 1] - w / 2}[side]
    k = int(np.argmin(score * 4 + np.abs(mid)))
    return int(rc[k][1]), int(rc[k][0])


def spots_near(
    t: np.ndarray,
    rng: np.random.Generator,
    x: float,
    y: float,
    radius: float,
    n: int,
    kinds=None,
    spread: float = 6.0,
) -> list[tuple[int, int]]:
    """``n`` foot-passable fuel cells near (x, y), at least ``spread`` cells apart."""
    kinds = kinds or (T.GRASS, T.SHRUB, T.FOREST, T.DENSE_FOREST)
    fc = cells_of(t, kinds)
    if not len(fc):
        return []
    d = np.hypot(fc[:, 1] - x, fc[:, 0] - y)
    inside = fc[d <= radius]
    if not len(inside):
        inside = fc[np.argsort(d)[: max(n * 20, 50)]]
    out: list[tuple[int, int]] = []
    tries = 0
    while len(out) < n and tries < n * 60:
        tries += 1
        cy, cx = inside[rng.integers(len(inside))]
        if all(math.hypot(cx - ox, cy - oy) >= spread for ox, oy in out):
            out.append((int(cx), int(cy)))
    return out
