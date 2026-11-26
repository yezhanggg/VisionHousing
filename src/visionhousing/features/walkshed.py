"""Walksheds: the land within a walking distance of transit stops, along streets and paths.

Plain-language summary
----------------------
A **straight-line buffer** (a circle of 1/2 mile around a stop) counts land that
is close as the crow flies but may be a long walk away: across a river with no
bridge, behind a highway, inside a rail yard. A **network walkshed** counts only
land a person can reach by walking on streets, sidewalks and paths no farther
than the distance.

This module turns a walking network (OpenStreetMap ways, see
:mod:`visionhousing.data.sources.osm`) into walkshed areas, in four steps:

1. **Flatten the map.** Latitude and longitude become metres on a local
   sinusoidal projection centred on the area: ``x = R cos(lat) (lon - lon0)``,
   ``y = R (lat - lat0)``, with ``R`` the Earth's radius of curvature at
   ``lat0``. It keeps areas exact and, within 40 km of the centre, distances
   within about half a percent. Each walkable way becomes straight segments
   between its OSM nodes (the way's own vertices), so no detail is lost.
2. **Walk out from every stop at once.** Every network node within the snap
   distance of a stop starts with its straight-line distance to that stop. A
   shortest-path search (Dijkstra's algorithm, with a heap) then gives every node
   its walking distance to the nearest stop, stopping at the largest distance
   needed. Using all stops together dissolves overlaps: a place near two stops
   is counted once.
3. **Draw the walkshed.** For a distance ``D`` every segment, or the part of it,
   within ``D`` of a stop is kept. Each kept piece is widened by the edge buffer
   (the lots that face the street), and the pieces are joined. The result is
   then cut to the straight-line circles, so the network walkshed is never
   larger than the straight-line buffer. The shapes are drawn on a fine grid of
   square cells in the same projection (``cell_m``, 10 m by default, from
   ``assumptions/transit_walk.yaml``): a cell belongs to a shape when its centre
   does (:mod:`visionhousing.features.planar`). Joining shapes is a logical OR of
   grids, cutting is an AND, and an area is ``cells x cell_m^2``. At 10 m the
   area of a 1/4-mile circle is within 0.2% of ``pi r^2`` and a single 50 m
   street buffer within 1% of its exact area (tests check both); on the
   Pennsylvania and Vermont builds every county's total agreed with an exact
   polygon method within 0.5%.
4. **Fallback.** A stop with no network node within the snap distance, or whose
   nearest nodes belong to a small disconnected piece of network (total length
   below the minimum), is marked **incomplete network**. Its straight-line circle
   is used instead and is reported separately (``fallback``).

The rules' numbers (snap distance, edge buffer, minimum piece length) come from
the ledger; see :class:`WalkRule`. Only NumPy is needed.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from visionhousing.features import planar

__all__ = [
    "CELL_M",
    "EARTH_A",
    "EARTH_E2",
    "METRES_PER_MILE",
    "Network",
    "WalkRule",
    "Walksheds",
    "build_network",
    "component_lengths",
    "multi_source_distances",
    "project",
    "reached_segments",
    "seed_stops",
    "stop_walksheds",
    "walkshed_grid",
    "walkshed_shapes",
]

METRES_PER_MILE = 1609.344
EARTH_A = 6_378_137.0
"""Equatorial radius of the GRS 80 / WGS 84 ellipsoid, metres."""
EARTH_E2 = 0.00669438002290
"""First eccentricity squared of the GRS 80 ellipsoid."""


CELL_M = 10.0
"""Default grid cell (metres) the walksheds are drawn on; the build reads
``network.cell_m`` from ``assumptions/transit_walk.yaml``."""


# --------------------------------------------------------------------------- projection


def project(
    lat: np.ndarray, lon: np.ndarray, lat0: float, lon0: float
) -> tuple[np.ndarray, np.ndarray]:
    """Local sinusoidal projection in metres around ``(lat0, lon0)`` (equal-area).

    ``R`` is the Gaussian mean radius of curvature of the GRS 80 ellipsoid at
    ``lat0``: ``sqrt(M N)``, where ``M`` and ``N`` are the meridian and prime
    vertical radii.
    """
    phi0 = math.radians(lat0)
    s2 = math.sin(phi0) ** 2
    n = EARTH_A / math.sqrt(1 - EARTH_E2 * s2)
    m = EARTH_A * (1 - EARTH_E2) / (1 - EARTH_E2 * s2) ** 1.5
    r = math.sqrt(m * n)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    x = r * np.cos(np.radians(lat)) * np.radians(lon - lon0)
    y = r * np.radians(lat - lat0)
    return x, y


# --------------------------------------------------------------------------- network


@dataclass(frozen=True)
class WalkRule:
    """The walkshed rule's numbers (from the ledger).

    Args:
        snap_m: A stop joins the network at every node within this distance.
        edge_buffer_m: Width added on each side of a reached street or path.
        min_component_m: A network piece shorter than this (total length) is
            treated as incomplete, and its stops fall back to straight lines.
    """

    snap_m: float
    edge_buffer_m: float
    min_component_m: float

    LEDGER_IDS = (
        "tod.walk_snap_m",
        "tod.walk_edge_buffer_m",
        "tod.walk_min_component_m",
    )
    """Ledger ids of the three numbers, in argument order."""

    @classmethod
    def from_ledger(cls, ledger: Any) -> WalkRule:
        """Build the rule from the ledger's central values."""
        return cls(*(float(ledger[i].central) for i in cls.LEDGER_IDS))


@dataclass
class Network:
    """A walking network in projected metres.

    Attributes:
        x: Node x coordinates (metres).
        y: Node y coordinates (metres).
        u: First node of each edge (index into ``x``).
        v: Second node of each edge.
        length: Straight length of each edge (metres).
    """

    x: np.ndarray
    y: np.ndarray
    u: np.ndarray
    v: np.ndarray
    length: np.ndarray

    @property
    def n_nodes(self) -> int:
        """Number of nodes."""
        return len(self.x)

    def adjacency(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Compressed adjacency lists: ``(start, neighbour, weight)``.

        Node ``i``'s neighbours are ``neighbour[start[i]:start[i + 1]]``.
        """
        a = np.concatenate([self.u, self.v])
        b = np.concatenate([self.v, self.u])
        w = np.concatenate([self.length, self.length])
        order = np.argsort(a, kind="stable")
        a, b, w = a[order], b[order], w[order]
        start = np.searchsorted(a, np.arange(self.n_nodes + 1))
        return start, b, w


def build_network(node_x: np.ndarray, node_y: np.ndarray, ways: Sequence[np.ndarray]) -> Network:
    """Build a network from node coordinates and ways (each a sequence of node indices).

    Consecutive nodes of a way form an edge; repeated edges (shared by two ways,
    or both directions) are kept once, and zero-length edges are dropped. Nodes
    that no edge uses stay in the arrays but are never reached.
    """
    x = np.asarray(node_x, dtype=float)
    y = np.asarray(node_y, dtype=float)
    pairs = [np.column_stack([w[:-1], w[1:]]) for w in ways if len(w) >= 2]
    e = np.vstack(pairs).astype(np.int64) if pairs else np.zeros((0, 2), dtype=np.int64)
    e = np.sort(e, axis=1)
    e = e[e[:, 0] != e[:, 1]]
    e = np.unique(e, axis=0) if len(e) else e
    u, v = e[:, 0], e[:, 1]
    length = np.hypot(x[u] - x[v], y[u] - y[v])
    keep = length > 0
    return Network(x=x, y=y, u=u[keep], v=v[keep], length=length[keep])


def component_lengths(net: Network) -> np.ndarray:
    """Total edge length of the connected piece each node belongs to (0 for a lone node)."""
    parent = np.arange(net.n_nodes)

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in zip(net.u.tolist(), net.v.tolist(), strict=True):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([root(i) for i in range(net.n_nodes)], dtype=np.int64)
    total = np.zeros(net.n_nodes)
    np.add.at(total, roots[net.u], net.length)
    return total[roots]


def _grid_index(x: np.ndarray, y: np.ndarray, cell: float) -> dict[tuple[int, int], np.ndarray]:
    gx = np.floor(x / cell).astype(np.int64)
    gy = np.floor(y / cell).astype(np.int64)
    order = np.lexsort((gy, gx))
    keys = np.column_stack([gx[order], gy[order]])
    out: dict[tuple[int, int], np.ndarray] = {}
    if not len(order):
        return out
    cut = np.flatnonzero(np.any(np.diff(keys, axis=0) != 0, axis=1)) + 1
    for part in np.split(np.arange(len(order)), cut):
        out[(int(keys[part[0], 0]), int(keys[part[0], 1]))] = order[part]
    return out


def seed_stops(
    net: Network,
    stop_x: np.ndarray,
    stop_y: np.ndarray,
    rule: WalkRule,
    *,
    comp_length: np.ndarray | None = None,
) -> tuple[dict[int, float], np.ndarray, list[list[tuple[int, float]]]]:
    """Join each stop to the network.

    A stop is joined to every node of the network within ``rule.snap_m`` that
    lies on a network piece at least ``rule.min_component_m`` long; each such
    node starts at its straight-line distance from the stop. A stop with no such
    node is not joined (``ok`` is False): it falls back to a straight line.

    Returns:
        ``(seeds, ok, per_stop)``: the starting distance of every seeded node
        (the smallest over stops), whether each stop was joined, and each stop's
        own ``(node, distance)`` list.
    """
    sx = np.asarray(stop_x, dtype=float)
    sy = np.asarray(stop_y, dtype=float)
    used = np.zeros(net.n_nodes, dtype=bool)
    used[net.u] = True
    used[net.v] = True
    if comp_length is None:
        comp_length = component_lengths(net)
    good = used & (comp_length >= rule.min_component_m)
    cand = np.flatnonzero(good)
    grid = _grid_index(net.x[cand], net.y[cand], rule.snap_m)
    seeds: dict[int, float] = {}
    ok = np.zeros(len(sx), dtype=bool)
    per_stop: list[list[tuple[int, float]]] = []
    for k in range(len(sx)):
        gx, gy = math.floor(sx[k] / rule.snap_m), math.floor(sy[k] / rule.snap_m)
        near = [
            grid[c] for c in ((gx + i, gy + j) for i in (-1, 0, 1) for j in (-1, 0, 1)) if c in grid
        ]
        mine: list[tuple[int, float]] = []
        if near:
            idx = cand[np.concatenate(near)]
            d = np.hypot(net.x[idx] - sx[k], net.y[idx] - sy[k])
            for node, dist in zip(
                idx[d <= rule.snap_m].tolist(), d[d <= rule.snap_m].tolist(), strict=True
            ):
                mine.append((node, dist))
                if dist < seeds.get(node, math.inf):
                    seeds[node] = dist
        ok[k] = bool(mine)
        per_stop.append(mine)
    return seeds, ok, per_stop


def multi_source_distances(
    net: Network,
    seeds: dict[int, float],
    cutoff: float,
    adjacency: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
) -> np.ndarray:
    """Walking distance from the nearest seed to every node (``inf`` beyond ``cutoff``)."""
    start, nbr, wt = adjacency if adjacency is not None else net.adjacency()
    dist = np.full(net.n_nodes, np.inf)
    heap = [(d, n) for n, d in seeds.items() if d <= cutoff]
    heapq.heapify(heap)
    for d, n in heap:
        dist[n] = min(dist[n], d)
    start_l, nbr_l, wt_l = (
        a.tolist() if isinstance(a, np.ndarray) else a for a in (start, nbr, wt)
    )
    done = np.zeros(net.n_nodes, dtype=bool)
    while heap:
        d, n = heapq.heappop(heap)
        if done[n]:
            continue
        done[n] = True
        for k in range(start_l[n], start_l[n + 1]):
            m = nbr_l[k]
            nd = d + wt_l[k]
            if nd <= cutoff and nd < dist[m]:
                dist[m] = nd
                heapq.heappush(heap, (nd, m))
    return dist


def reached_segments(net: Network, dist: np.ndarray, reach: float) -> np.ndarray:
    """The segments (or parts) within ``reach`` metres of walking: an ``(n, 2, 2)`` array.

    For an edge ``u``-``v`` of length ``L``, ``a = reach - dist[u]`` metres can be
    walked in from ``u`` and ``b = reach - dist[v]`` from ``v``. If ``a + b >= L``
    the whole edge is reached; otherwise the first ``a`` metres from ``u`` and the
    last ``b`` metres before ``v`` are.
    """
    du, dv, length = dist[net.u], dist[net.v], net.length
    a = np.clip(reach - du, 0.0, None)
    b = np.clip(reach - dv, 0.0, None)
    a = np.where(np.isfinite(a), a, 0.0)
    b = np.where(np.isfinite(b), b, 0.0)
    p = np.column_stack([net.x[net.u], net.y[net.u]])
    q = np.column_stack([net.x[net.v], net.y[net.v]])
    full = (a + b >= length) & ((a > 0) | (b > 0))
    from_u = ~full & (a > 0)
    from_v = ~full & (b > 0)
    parts = [np.stack([p[full], q[full]], axis=1)]
    if from_u.any():
        t = (a[from_u] / length[from_u])[:, None]
        parts.append(np.stack([p[from_u], p[from_u] + (q[from_u] - p[from_u]) * t], axis=1))
    if from_v.any():
        t = (b[from_v] / length[from_v])[:, None]
        parts.append(np.stack([q[from_v] + (p[from_v] - q[from_v]) * t, q[from_v]], axis=1))
    return np.concatenate(parts, axis=0) if parts else np.zeros((0, 2, 2))


@dataclass
class Walksheds:
    """Walkshed shapes for one area, by distance in metres, as grids of cells.

    Attributes:
        grid: The grid (projected metres) every mask is drawn on.
        network: Network walkshed (reached streets widened, cut to the circles of
            the joined stops), by distance: a bool array, True inside.
        fallback: Straight-line circles of stops not joined to the network, minus
            the network walkshed, by distance.
        straight: Straight-line circles of every stop, joined, by distance.
        joined: Whether each stop was joined to the network.
    """

    grid: planar.GridSpec
    network: dict[float, np.ndarray]
    fallback: dict[float, np.ndarray]
    straight: dict[float, np.ndarray]
    joined: np.ndarray

    def area(self, mask: np.ndarray, box: tuple[float, float, float, float] | None = None) -> float:
        """Area (m2) of a mask, optionally only the cells whose centre is in a box."""
        if box is None:
            return float(mask.sum()) * self.grid.cell_area
        rows, cols = self.grid.window(*box)
        xs, ys = self.grid.centres(rows, cols)
        part = mask[rows, cols]
        inside = (xs >= box[0]) & (xs <= box[2]) & (ys >= box[1]) & (ys <= box[3])
        return float((part & inside).sum()) * self.grid.cell_area


def walkshed_grid(
    stop_x: np.ndarray,
    stop_y: np.ndarray,
    reach: float,
    cell_m: float = CELL_M,
    bounds: tuple[float, float, float, float] | None = None,
) -> planar.GridSpec:
    """The grid covering every stop's circle of ``reach`` metres, cut to ``bounds`` if given.

    Cells are aligned to multiples of ``cell_m`` from the projection's centre.
    """
    sx = np.asarray(stop_x, dtype=float)
    sy = np.asarray(stop_y, dtype=float)
    if not len(sx):
        return planar.GridSpec(0.0, 0.0, cell_m, 0, 0)
    xmin, ymin = float(sx.min()) - reach, float(sy.min()) - reach
    xmax, ymax = float(sx.max()) + reach, float(sy.max()) + reach
    if bounds is not None:
        xmin, ymin = max(xmin, bounds[0]), max(ymin, bounds[1])
        xmax, ymax = min(xmax, bounds[2]), min(ymax, bounds[3])
        if xmax <= xmin or ymax <= ymin:
            return planar.GridSpec(xmin, ymax, cell_m, 0, 0)
    return planar.grid_for_bounds(xmin, ymin, xmax, ymax, cell_m, pad=cell_m)


def walkshed_shapes(
    net: Network,
    stop_x: np.ndarray,
    stop_y: np.ndarray,
    distances: Sequence[float],
    rule: WalkRule,
    *,
    force_fallback: np.ndarray | None = None,
    cell_m: float = CELL_M,
    bounds: tuple[float, float, float, float] | None = None,
) -> Walksheds:
    """Dissolved network and straight-line walksheds of all stops, for each distance.

    Args:
        net: The walking network (projected metres). May be empty: every stop
            then falls back.
        stop_x: Stop x coordinates (metres, same projection).
        stop_y: Stop y coordinates.
        distances: Walking distances in metres (for example 402, 805, 1207).
        rule: Snap distance, edge buffer and minimum network piece.
        force_fallback: Stops that must use straight lines whatever the network
            (their map tile could not be fetched).
        cell_m: Grid cell size in metres.
        bounds: Only draw inside this box ``(xmin, ymin, xmax, ymax)`` (the area
            whose land is counted), to keep the grids small.
    """
    sx = np.asarray(stop_x, dtype=float)
    sy = np.asarray(stop_y, dtype=float)
    keep = np.ones(len(sx), dtype=bool) if force_fallback is None else ~np.asarray(force_fallback)
    _, ok, per_stop = seed_stops(net, sx, sy, rule)
    ok &= keep
    seeds: dict[int, float] = {}
    for k in np.flatnonzero(ok).tolist():
        for node, d in per_stop[k]:
            if d < seeds.get(node, math.inf):
                seeds[node] = d
    dist = (
        multi_source_distances(net, seeds, max(distances))
        if seeds
        else np.full(net.n_nodes, np.inf)
    )
    grid = walkshed_grid(sx, sy, max(distances), cell_m, bounds)
    out = Walksheds(grid=grid, network={}, fallback={}, straight={}, joined=ok)
    for d in distances:
        joined_circles = planar.fill_discs(grid, sx[ok], sy[ok], d)
        fb = planar.fill_discs(grid, sx[~ok], sy[~ok], d)
        ribbons = planar.fill_capsules(grid, reached_segments(net, dist, d), rule.edge_buffer_m)
        network = ribbons & joined_circles
        out.network[d] = network
        out.fallback[d] = fb & ~network
        out.straight[d] = joined_circles | fb
    return out


def stop_walksheds(
    net: Network,
    stop_x: np.ndarray,
    stop_y: np.ndarray,
    distance: float,
    rule: WalkRule,
    *,
    cell_m: float = CELL_M,
) -> list[tuple[bool, float, float]]:
    """Each stop's own walkshed on its own (no dissolving): ``(joined, network_m2, circle_m2)``.

    Both areas are counted on the same grid, so their ratio is not biased by
    the cell size. Used for checks by hand (stations next to rivers or
    highways), not for the pack.
    """
    sx = np.asarray(stop_x, dtype=float)
    sy = np.asarray(stop_y, dtype=float)
    comp = component_lengths(net)
    adj = tuple(a.tolist() for a in net.adjacency())
    _, ok, per_stop = seed_stops(net, sx, sy, rule, comp_length=comp)
    out = []
    for k in range(len(sx)):
        grid = walkshed_grid(sx[k : k + 1], sy[k : k + 1], distance, cell_m)
        circle = planar.fill_discs(grid, sx[k : k + 1], sy[k : k + 1], distance)
        c_m2 = float(circle.sum()) * grid.cell_area
        if not ok[k]:
            out.append((False, c_m2, c_m2))
            continue
        dist = multi_source_distances(net, dict(per_stop[k]), distance, adj)
        ribbons = planar.fill_capsules(
            grid, reached_segments(net, dist, distance), rule.edge_buffer_m
        )
        out.append((True, float((ribbons & circle).sum()) * grid.cell_area, c_m2))
    return out
