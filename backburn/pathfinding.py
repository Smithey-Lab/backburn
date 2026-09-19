"""Grid pathfinding for ground and water units.

Two levels, both over an 8-connected grid with per-cell movement cost taken from the
terrain tables (inf = impassable). Diagonal moves cost sqrt(2)× and are refused when
they would cut a corner between two impassable cells. Burning and smoldering cells are
treated as impassable for non-air units.

* ``astar`` — A* with an octile heuristic on the fine cell grid. The hot loop runs on flat
  indices over a padded Python list, so it never touches NumPy scalars, and it only ever
  looks at a search *window* so the cost is proportional to the area explored rather than
  the size of the map.
* ``Navigator`` — a coarse block graph (``BLOCK``×``BLOCK`` cells) built with NumPy from
  the same cost grid. Long orders are planned on the block graph first and refined onto
  the fine grid only a few blocks ahead of the unit ("lazy corridor refinement"). A
  cross-map order on a 1.4-million-cell map therefore costs tens of milliseconds instead
  of seconds, and no detail is planned that the fire will change before the unit arrives.

Per-tick cost fields are cached on the navigator so several units planning in the same
tick share one NumPy pass. Nothing here is random; the same grid and request always give
the same path, which keeps replays exact.
"""

from __future__ import annotations

import hashlib
import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from .fire import BURNING, SMOLDER

BLOCK = 8  # coarse block size in cells
SHORT_RANGE = 48  # orders shorter than this (octile cells) skip the block graph
WINDOW_PAD = 24  # cells of slack around a short search's bounding box
CHUNK_BLOCKS = 6  # blocks refined ahead of the unit per fine search
SQRT2 = math.sqrt(2.0)
INF = float("inf")


def build_cost(
    terrain: np.ndarray, state: np.ndarray, cost_row: np.ndarray, avoid_fire: bool = True
) -> np.ndarray:
    cost = cost_row[terrain].astype(np.float32)
    if avoid_fire:
        cost[(state == BURNING) | (state == SMOLDER)] = np.inf
    return cost


def octile(ax: int, ay: int, bx: int, by: int) -> float:
    dx, dy = abs(ax - bx), abs(ay - by)
    return max(dx, dy) + (SQRT2 - 1.0) * min(dx, dy)


# ---- fine A* --------------------------------------------------------------------------


def astar(
    cost: np.ndarray,
    start: tuple[int, int],
    goal: tuple[int, int],
    max_expand: int = 200_000,
    window: tuple[int, int, int, int] | None = None,
) -> list[tuple[int, int]] | None:
    """A* from start (exclusive) to goal (inclusive) over ``cost``; None if nothing reachable.

    ``window`` = (x0, y0, x1, y1) limits the search to that half-open rectangle. If the goal
    is unreachable (impassable, fenced off, outside the window or beyond ``max_expand``) the
    path to the explored cell nearest to it is returned instead, so a unit always makes
    progress toward what the player pointed at.
    """
    h, w = cost.shape
    sx, sy = start
    gx, gy = goal
    if not (0 <= sx < w and 0 <= sy < h):
        return None
    gx = min(max(gx, 0), w - 1)
    gy = min(max(gy, 0), h - 1)
    if (sx, sy) == (gx, gy):
        return []
    if window is None:
        x0, y0, x1, y1 = 0, 0, w, h
    else:
        x0, y0, x1, y1 = window
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if not (x0 <= sx < x1 and y0 <= sy < y1):
            return None
    ww, wh = x1 - x0, y1 - y0
    W = ww + 2  # padded row stride
    padded = np.full((wh + 2, ww + 2), np.inf, np.float32)
    padded[1:-1, 1:-1] = cost[y0:y1, x0:x1]
    c = padded.ravel().tolist()
    finite = padded[np.isfinite(padded)]
    min_c = float(finite.min()) if finite.size else 1.0
    if min_c <= 0.0:
        min_c = 1e-3

    # Local padded coordinates.
    lsx, lsy = sx - x0 + 1, sy - y0 + 1
    lgx, lgy = gx - x0 + 1, gy - y0 + 1
    s_idx = lsy * W + lsx
    g_idx = lgy * W + lgx
    h_scale = min_c
    d1 = SQRT2 - 1.0

    def heur(idx: int) -> float:
        y, x = divmod(idx, W)
        dx, dy = abs(lgx - x), abs(lgy - y)
        return (max(dx, dy) + d1 * min(dx, dy)) * h_scale

    dist = {s_idx: 0.0}
    came: dict[int, int] = {}
    pq = [(heur(s_idx), 0.0, s_idx)]
    best_idx, best_d = s_idx, math.hypot(lgx - lsx, lgy - lsy)
    expanded = 0
    push, pop = heapq.heappush, heapq.heappop
    straight = ((1, 1.0), (-1, 1.0), (W, 1.0), (-W, 1.0))
    while pq:
        _, d, i = pop(pq)
        if d > dist.get(i, INF):
            continue
        if i == g_idx:
            best_idx = i
            break
        iy, ix = divmod(i, W)
        hd = math.hypot(lgx - ix, lgy - iy)
        if hd < best_d:
            best_d, best_idx = hd, i
        expanded += 1
        if expanded > max_expand:
            break
        for off, step in straight:
            n = i + off
            cn = c[n]
            if cn == INF:
                continue
            nd = d + step * cn
            if nd < dist.get(n, INF):
                dist[n] = nd
                came[n] = i
                push(pq, (nd + heur(n), nd, n))
        # Diagonals: both orthogonal neighbours must be passable (no corner cutting).
        cr, cl, cd, cu = c[i + 1], c[i - 1], c[i + W], c[i - W]
        if cr != INF and cd != INF:
            n = i + W + 1
            cn = c[n]
            if cn != INF:
                nd = d + SQRT2 * cn
                if nd < dist.get(n, INF):
                    dist[n] = nd
                    came[n] = i
                    push(pq, (nd + heur(n), nd, n))
        if cl != INF and cd != INF:
            n = i + W - 1
            cn = c[n]
            if cn != INF:
                nd = d + SQRT2 * cn
                if nd < dist.get(n, INF):
                    dist[n] = nd
                    came[n] = i
                    push(pq, (nd + heur(n), nd, n))
        if cr != INF and cu != INF:
            n = i - W + 1
            cn = c[n]
            if cn != INF:
                nd = d + SQRT2 * cn
                if nd < dist.get(n, INF):
                    dist[n] = nd
                    came[n] = i
                    push(pq, (nd + heur(n), nd, n))
        if cl != INF and cu != INF:
            n = i - W - 1
            cn = c[n]
            if cn != INF:
                nd = d + SQRT2 * cn
                if nd < dist.get(n, INF):
                    dist[n] = nd
                    came[n] = i
                    push(pq, (nd + heur(n), nd, n))

    path: list[tuple[int, int]] = []
    i = best_idx
    while i != s_idx:
        y, x = divmod(i, W)
        path.append((x - 1 + x0, y - 1 + y0))
        i = came.get(i, -1)
        if i < 0:
            return None
    path.reverse()
    return path


def find_path(
    cost: np.ndarray, start: tuple[int, int], goal: tuple[int, int], max_expand: int = 200_000
) -> list[tuple[int, int]] | None:
    """Complete path from start (exclusive) to goal (inclusive), or None.

    Short requests search a padded window around the two points; long ones plan on the
    block graph and refine every chunk immediately. Use ``Navigator`` when the caller can
    refine lazily while the unit moves.
    """
    h, w = cost.shape
    gx = min(max(int(goal[0]), 0), w - 1)
    gy = min(max(int(goal[1]), 0), h - 1)
    goal = (gx, gy)
    if w * h <= 40_000:
        return astar(cost, start, goal, max_expand)
    if octile(*start, *goal) <= SHORT_RANGE:
        p = astar(cost, start, goal, max_expand, _window_around(start, goal, w, h))
        if p is not None and (p and p[-1] == goal or not np.isfinite(cost[gy, gx])):
            return p
    field_ = CostField(cost)
    route = field_.plan(start, goal)
    if route is None:
        return None
    path, rest = route
    guard = 0
    while rest is not None and guard < 10_000:
        guard += 1
        end = path[-1] if path else start
        more = field_.refine(end, rest)
        if more is None:
            break
        chunk, rest = more
        path.extend(chunk)
    return path


def straight_line(start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[int, int]]:
    """Air units ignore terrain: a single waypoint."""
    return [(int(round(goal[0])), int(round(goal[1])))]


def _window_around(a, b, w, h, pad: int = WINDOW_PAD) -> tuple[int, int, int, int]:
    x0 = max(0, min(a[0], b[0]) - pad)
    y0 = max(0, min(a[1], b[1]) - pad)
    x1 = min(w, max(a[0], b[0]) + pad + 1)
    y1 = min(h, max(a[1], b[1]) + pad + 1)
    return x0, y0, x1, y1


# ---- coarse block graph -------------------------------------------------------------------


@dataclass
class Route:
    """The part of a long order not yet refined: the final goal and the blocks still ahead.

    ``blocked`` remembers blocks the block graph thought were connected but a fine search
    could not cross (a river or lake inside the block); replans route around them."""

    goal: tuple[int, int]
    blocks: list[int] = field(default_factory=list)  # graph node ids from the unit onward
    blocked: set = field(default_factory=set)  # (bx, by) blocks to route around

    def to_dict(self) -> dict:
        return {
            "goal": list(self.goal),
            "blocks": [int(b) for b in self.blocks],
            "blocked": sorted(list(b) for b in self.blocked),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "Route | None":
        if not d:
            return None
        blocks = d.get("blocks", [])
        if blocks and not isinstance(blocks[0], int):
            return None  # an older save's route; the unit simply replans
        return cls(
            (int(d["goal"][0]), int(d["goal"][1])),
            [int(b) for b in blocks],
            {(int(b[0]), int(b[1])) for b in d.get("blocked", [])},
        )


class BlockGraph:
    """Static connectivity of one movement class at block resolution.

    Nodes are (block, component) pairs: a block cut by a river or lake holds one node per
    side, so a coarse route never assumes a crossing that is not there. Components are
    4-connected sets of passable cells inside the block, found with a vectorised label
    propagation over only the mixed blocks. Edges join components whose cells touch across
    a block border. Built once per passability pattern; fire is layered on at plan time.
    """

    MAX_COMP = 8  # components per block that get their own node

    def __init__(self, passable: np.ndarray):
        B = BLOCK
        self.h, self.w = passable.shape
        self.bw = -(-self.w // B)
        self.bh = -(-self.h // B)
        H, W = self.bh * B, self.bw * B
        ok = np.zeros((H, W), bool)
        ok[: self.h, : self.w] = passable
        self.ok = ok
        blocks4 = ok.reshape(self.bh, B, self.bw, B)
        n_pass = blocks4.sum(axis=(1, 3))
        comp = np.full((H, W), -1, np.int16)
        comp4 = comp.reshape(self.bh, B, self.bw, B)
        full = np.argwhere(n_pass == B * B)
        if len(full):
            comp4[full[:, 0], :, full[:, 1], :] = 0
        mixed = np.argwhere((n_pass > 0) & (n_pass < B * B))
        if len(mixed):
            sub = blocks4[mixed[:, 0], :, mixed[:, 1], :]  # (n, B, B)
            big = B * B + 1
            labels = np.where(sub, np.arange(B * B, dtype=np.int32).reshape(1, B, B), big)
            for _ in range(B * B):
                before = labels
                labels = labels.copy()
                np.minimum(labels[:, 1:, :], before[:, :-1, :], out=labels[:, 1:, :])
                np.minimum(labels[:, :-1, :], before[:, 1:, :], out=labels[:, :-1, :])
                np.minimum(labels[:, :, 1:], before[:, :, :-1], out=labels[:, :, 1:])
                np.minimum(labels[:, :, :-1], before[:, :, 1:], out=labels[:, :, :-1])
                labels[~sub] = big
                if np.array_equal(labels, before):
                    break
            # Compact labels to 0..k-1 per block, by order of first appearance.
            flat = labels.reshape(len(mixed), B * B)
            order = np.argsort(flat, axis=1, kind="stable")
            sorted_ = np.take_along_axis(flat, order, axis=1)
            new_group = np.concatenate(
                [np.ones((len(mixed), 1), bool), sorted_[:, 1:] != sorted_[:, :-1]], axis=1
            )
            ranks = np.cumsum(new_group, axis=1) - 1
            ids = np.empty_like(flat)
            np.put_along_axis(ids, order, ranks, axis=1)
            ids = np.minimum(ids, self.MAX_COMP - 1).astype(np.int16)
            ids[~sub.reshape(len(mixed), B * B)] = -1
            comp4[mixed[:, 0], :, mixed[:, 1], :] = ids.reshape(len(mixed), B, B)
        self.comp = comp
        self.n_pass = n_pass
        # Nodes: block * MAX_COMP + component. Edges across block borders.
        block_id = (np.arange(self.bh)[:, None] * self.bw + np.arange(self.bw)[None, :]).astype(np.int64)
        node = np.repeat(np.repeat(block_id, B, axis=0), B, axis=1) * self.MAX_COMP + np.maximum(comp, 0)
        node[comp < 0] = -1
        self.node = node
        pairs = []
        if self.bw > 1:
            a = node[:, B - 1 : W - B : B].ravel()
            b = node[:, B::B].ravel()
            m = (a >= 0) & (b >= 0)
            pairs.append(np.stack([a[m], b[m]], axis=1))
        if self.bh > 1:
            a = node[B - 1 : H - B : B, :].ravel()
            b = node[B::B, :].ravel()
            m = (a >= 0) & (b >= 0)
            pairs.append(np.stack([a[m], b[m]], axis=1))
        self.adj: dict[int, list[int]] = {}
        if pairs:
            pr = np.concatenate(pairs)
            n_nodes = self.bh * self.bw * self.MAX_COMP
            keys = np.unique(pr[:, 0] * n_nodes + pr[:, 1])  # 1-D unique is a plain sort
            for a, b in zip((keys // n_nodes).tolist(), (keys % n_nodes).tolist()):
                self.adj.setdefault(a, []).append(b)
                self.adj.setdefault(b, []).append(a)

    def node_at(self, x: int, y: int) -> int:
        return int(self.node[y, x])

    @staticmethod
    def block_of(node: int) -> tuple[int, int]:
        return node // BlockGraph.MAX_COMP, node % BlockGraph.MAX_COMP

    def representative(self, node: int) -> tuple[int, int] | None:
        """Cell of this node nearest the block centre."""
        b, _ = self.block_of(node)
        by, bx = divmod(b, self.bw)
        B = BLOCK
        sub = self.node[by * B : (by + 1) * B, bx * B : (bx + 1) * B] == node
        ys, xs = np.nonzero(sub)
        if len(ys) == 0:
            return None
        cy, cx = (sub.shape[0] - 1) / 2, (sub.shape[1] - 1) / 2
        k = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
        x, y = bx * B + int(xs[k]), by * B + int(ys[k])
        return (min(x, self.w - 1), min(y, self.h - 1))


class CostField:
    """One movement profile's cost grid (fire included) plus its static block graph."""

    def __init__(self, cost: np.ndarray, graph: BlockGraph | None = None):
        self.cost = cost
        self.h, self.w = cost.shape
        self.bw = -(-self.w // BLOCK)
        self.bh = -(-self.h // BLOCK)
        self.graph = graph if graph is not None else BlockGraph(np.isfinite(cost))
        self._block_cost: np.ndarray | None = None

    def _block_costs(self) -> np.ndarray:
        """Optimistic per-block cost (inf where fire makes the block unsafe to route through)."""
        if self._block_cost is not None:
            return self._block_cost
        B = BLOCK
        H, W = self.bh * B, self.bw * B
        cost = np.full((H, W), np.inf, np.float32)
        cost[: self.h, : self.w] = self.cost
        ok = np.isfinite(cost)
        blocks = cost.reshape(self.bh, B, self.bw, B)
        n = ok.reshape(self.bh, B, self.bw, B).sum(axis=(1, 3))
        total = np.where(ok, cost, 0.0).reshape(self.bh, B, self.bw, B).sum(axis=(1, 3))
        cheapest = blocks.min(axis=(1, 3))
        with np.errstate(invalid="ignore", divide="ignore"):
            average = np.where(n > 0, total / np.maximum(n, 1), np.inf)
        mean = np.where(n > 0, 0.6 * cheapest + 0.4 * average, np.inf).astype(np.float32)
        # Fire inside a block (impassable cells that are passable terrain) makes it unsafe.
        static = self.graph.n_pass
        mean[(static - n) > 0] = np.inf
        self._block_cost = mean
        return mean

    def _coarse_astar(
        self, start: tuple[int, int], goal: tuple[int, int], blocked: set | None = None
    ) -> list[int] | None:
        """A* over graph nodes. Returns the node list from the start node (exclusive) onward."""
        g = self.graph
        bc = self._block_costs()
        bw = self.bw
        s_node = g.node_at(*start)
        if s_node < 0:  # standing on an impassable cell: use the block's first component
            s_node = (start[1] // BLOCK * bw + start[0] // BLOCK) * g.MAX_COMP
        g_node = g.node_at(*goal)
        gbx, gby = goal[0] // BLOCK, goal[1] // BLOCK
        sbx, sby = start[0] // BLOCK, start[1] // BLOCK
        if (sbx, sby) == (gbx, gby) and (g_node < 0 or g_node == s_node):
            return []
        m = bc.ravel().tolist()
        for bx, by in blocked or ():
            if 0 <= bx < bw and 0 <= by < self.bh and (bx, by) != (sbx, sby):
                m[by * bw + bx] = INF
        finite = bc[np.isfinite(bc)]
        min_c = float(finite.min()) if finite.size else 1.0
        if min_c <= 0:
            min_c = 1e-3
        scale = BLOCK * min_c
        d1 = SQRT2 - 1.0
        MC = g.MAX_COMP

        def heur(node: int) -> float:
            y, x = divmod(node // MC, bw)
            dx, dy = abs(gbx - x), abs(gby - y)
            return (max(dx, dy) + d1 * min(dx, dy)) * scale

        dist = {s_node: 0.0}
        came: dict[int, int] = {}
        pq = [(heur(s_node), 0.0, s_node)]
        best, best_d = s_node, math.hypot(gbx - sbx, gby - sby)
        push, pop = heapq.heappush, heapq.heappop
        expanded = 0
        adj = g.adj
        while pq:
            _, d, i = pop(pq)
            if d > dist.get(i, INF):
                continue
            ib = i // MC
            if ib == gby * bw + gbx and (g_node < 0 or i == g_node):
                best = i
                break
            iy, ix = divmod(ib, bw)
            hd = math.hypot(gbx - ix, gby - iy)
            if hd < best_d:
                best_d, best = hd, i
            expanded += 1
            if expanded > 80_000:
                break
            for n in adj.get(i, ()):
                nb = n // MC
                cn = m[nb]
                if cn == INF:
                    continue
                nd = d + BLOCK * cn
                if nd < dist.get(n, INF):
                    dist[n] = nd
                    came[n] = i
                    push(pq, (nd + heur(n), nd, n))
        nodes: list[int] = []
        i = best
        while i != s_node:
            nodes.append(i)
            i = came.get(i, -1)
            if i < 0:
                return None
        nodes.reverse()
        return nodes

    def _block_xy(self, node: int) -> tuple[int, int]:
        b = node // self.graph.MAX_COMP
        by, bx = divmod(b, self.bw)
        return bx, by

    # -- planning API -----------------------------------------------------------------------

    def plan(
        self, start: tuple[int, int], goal: tuple[int, int], blocked: set | None = None
    ) -> tuple[list[tuple[int, int]], Route | None] | None:
        """First leg of a path plus the remaining route (None once the leg reaches the goal)."""
        gx = min(max(int(goal[0]), 0), self.w - 1)
        gy = min(max(int(goal[1]), 0), self.h - 1)
        goal = (gx, gy)
        if octile(*start, *goal) <= SHORT_RANGE:
            p = astar(self.cost, start, goal, 40_000, _window_around(start, goal, self.w, self.h))
            # A short window can hide the detour a nearby goal needs; only trust it when it
            # reached the goal (or the goal itself cannot be stood on).
            if p is not None and (p and p[-1] == goal or not np.isfinite(self.cost[gy, gx])):
                return p, None
        blocked = set(blocked or ())
        nodes = self._coarse_astar(start, goal, blocked)
        if nodes is None:
            p = astar(self.cost, start, goal, 60_000, _window_around(start, goal, self.w, self.h, 48))
            return (p, None) if p is not None else None
        if not nodes:
            # Same block, but the short search above did not reach the goal: widen once.
            p = astar(self.cost, start, goal, 60_000, _window_around(start, goal, self.w, self.h, 64))
            return (p, None) if p is not None else None
        return self.refine(start, Route(goal, nodes, blocked))

    def refine(
        self, start: tuple[int, int], route: Route, attempts: int = 4
    ) -> tuple[list[tuple[int, int]], Route | None] | None:
        """Fine path from ``start`` through the next few nodes of ``route``.

        If the corridor still turns out to be cut, the offending block is added to
        ``route.blocked`` and the route is replanned around it a few times before falling
        back to a wide fine search."""
        g = self.graph
        goal = route.goal
        for _ in range(attempts):
            nodes = route.blocks
            sb = (start[0] // BLOCK, start[1] // BLOCK)
            while nodes and self._block_xy(nodes[0]) == sb:
                nodes = nodes[1:]
            if not nodes:
                p = astar(self.cost, start, goal, 40_000, _window_around(start, goal, self.w, self.h))
                return (p, None) if p is not None else None
            nb = self._block_xy(nodes[0])
            if max(abs(nb[0] - sb[0]), abs(nb[1] - sb[1])) > 1:
                # The previous leg ended somewhere else (it was cut short): replan from here.
                nodes = self._coarse_astar(start, goal, route.blocked)
                if not nodes:
                    break
                route = Route(goal, nodes, route.blocked)
                continue
            k = min(CHUNK_BLOCKS, len(nodes))
            corridor = [sb] + [self._block_xy(n) for n in nodes[:k]]
            last = k == len(nodes)
            if last:
                target = goal
            else:
                target = g.representative(nodes[k - 1])
                if target is None:
                    bx, by = corridor[-1]
                    target = (
                        min(bx * BLOCK + BLOCK // 2, self.w - 1),
                        min(by * BLOCK + BLOCK // 2, self.h - 1),
                    )
            bxs = [b[0] for b in corridor]
            bys = [b[1] for b in corridor]
            win = (
                max(0, (min(bxs) - 1) * BLOCK),
                max(0, (min(bys) - 1) * BLOCK),
                min(self.w, (max(bxs) + 2) * BLOCK),
                min(self.h, (max(bys) + 2) * BLOCK),
            )
            p = astar(self.cost, start, target, 20_000, win)
            end = p[-1] if p else start
            reached = p is not None and (
                (last and (end == goal or not np.isfinite(self.cost[goal[1], goal[0]])))
                or (not last and (end[0] // BLOCK, end[1] // BLOCK) == corridor[-1])
            )
            if reached:
                rest = None if last else Route(goal, nodes[k - 1 :], route.blocked)
                return p, rest
            # Cut corridor: blame the first block past where the search got to and re-route.
            eb = (end[0] // BLOCK, end[1] // BLOCK)
            j = next((i for i, b in enumerate(corridor) if b == eb), 0)
            culprit = corridor[min(j + 1, len(corridor) - 1)]
            if culprit == sb:
                culprit = corridor[1]
            route.blocked.add(culprit)
            nodes = self._coarse_astar(start, goal, route.blocked)
            if not nodes:
                break
            route = Route(goal, nodes, route.blocked)
        # Give up on the block graph: one wide best-effort search toward the goal.
        p = astar(self.cost, start, goal, 120_000, _window_around(start, goal, self.w, self.h, 96))
        return (p, None) if p is not None else None


class Navigator:
    """Per-tick cache of cost fields keyed by movement profile, plus the static block
    graphs, which survive across ticks until a profile's passability pattern changes."""

    def __init__(self):
        self._fields: dict[bytes, CostField] = {}
        self._version: int | None = None
        self._graphs: dict[bytes, tuple[bytes, BlockGraph]] = {}

    def graph(self, passable: np.ndarray, key: bytes) -> BlockGraph:
        digest = hashlib.blake2b(np.packbits(passable).tobytes(), digest_size=16).digest()
        hit = self._graphs.get(key)
        if hit is None or hit[0] != digest:
            hit = (digest, BlockGraph(passable))
            self._graphs[key] = hit
        return hit[1]

    def field(self, grid, cost_row: np.ndarray, version: int) -> CostField:
        if version != self._version:
            self._fields.clear()
            self._version = version
        key = cost_row.tobytes()
        f = self._fields.get(key)
        if f is None:
            passable = np.isfinite(cost_row[grid.terrain])
            f = CostField(build_cost(grid.terrain, grid.state, cost_row), self.graph(passable, key))
            self._fields[key] = f
        return f
