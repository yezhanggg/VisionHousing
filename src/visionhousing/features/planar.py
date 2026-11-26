"""Flat-map geometry in plain NumPy: points in polygons, polygon areas, shapes burned onto grids.

Plain-language summary
----------------------
The spatial steps (walksheds, flood zones, tract outlines, service areas) only
ever need four questions answered on a flat map in metres:

1. **Is a point inside a polygon?** A point is inside when a ray from it to
   the right crosses the polygon's edges an odd number of times (the
   *even-odd* or *ray casting* rule). All rings of a shape are taken together,
   so holes and islands come out right without reading ring direction.
2. **How large is a polygon?** The *shoelace formula*: half the absolute sum
   of ``x_i * y_(i+1) - x_(i+1) * y_i`` over a ring's corners; a polygon's area
   is its outer ring minus its holes.
3. **Which grid cells does a shape cover?** A cell counts when its centre is
   inside the shape (rule 1, applied to every cell centre at once, row by row).
4. **Which cells lie within a distance of a point or a line?** Discs (a circle
   around a stop) and *capsules* (a street segment widened by a buffer on
   every side) are drawn by their cell centres too. Joining shapes (a union) is
   then a logical OR of the grids, cutting one by another an AND, and an area
   is ``cells x cell size squared``.

Using cell centres makes the area of a shape on a grid an unbiased estimate of
its true area: cells cut by the outline are counted when more than about half
of them is inside, on average. The error shrinks with the cell size; the
walkshed module documents the size it uses and the error it gives.

Everything here is vectorised: the work is done for many edges and many points
(or grid rows) together, in chunks so memory stays bounded. No compiled
geometry library is needed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "GridSpec",
    "contains",
    "fill_capsules",
    "fill_discs",
    "fill_rings",
    "geojson_polygons",
    "grid_for_bounds",
    "polygon_area",
    "ring_area",
    "rings_bounds",
]

_CHUNK = 4_000_000
"""Largest number of (edge, point) or (shape, cell) pairs worked on at once."""

Ring = np.ndarray
"""A ring: an ``(n, 2)`` array of corners (closed or not; the closing edge is implied)."""


# --------------------------------------------------------------------------- grid


@dataclass(frozen=True)
class GridSpec:
    """A north-up grid: top-left corner, cell size and shape.

    Cell ``(row, col)`` covers x from ``x0 + col*cell`` and y down from ``y0 - row*cell``.
    """

    x0: float
    y0: float
    cell: float
    rows: int
    cols: int

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """``(xmin, ymin, xmax, ymax)``."""
        return (self.x0, self.y0 - self.rows * self.cell, self.x0 + self.cols * self.cell, self.y0)

    @property
    def cell_area(self) -> float:
        """Area of one cell (square units of the coordinates)."""
        return self.cell * self.cell

    def window(self, xmin: float, ymin: float, xmax: float, ymax: float) -> tuple[slice, slice]:
        """Rows and columns whose cell centres can fall inside a box (clipped to the grid)."""
        c0 = max(math.floor((xmin - self.x0) / self.cell - 0.5), 0)
        c1 = min(math.ceil((xmax - self.x0) / self.cell + 0.5), self.cols)
        r0 = max(math.floor((self.y0 - ymax) / self.cell - 0.5), 0)
        r1 = min(math.ceil((self.y0 - ymin) / self.cell + 0.5), self.rows)
        return slice(r0, max(r0, r1)), slice(c0, max(c0, c1))

    def sub(self, rows: slice, cols: slice) -> GridSpec:
        """The grid of a window (same cells, smaller extent)."""
        return GridSpec(
            self.x0 + cols.start * self.cell,
            self.y0 - rows.start * self.cell,
            self.cell,
            rows.stop - rows.start,
            cols.stop - cols.start,
        )

    def centres(self, rows: slice, cols: slice) -> tuple[np.ndarray, np.ndarray]:
        """Cell-centre coordinates of a window, as two 2-D arrays."""
        xs = self.x0 + (np.arange(cols.start, cols.stop) + 0.5) * self.cell
        ys = self.y0 - (np.arange(rows.start, rows.stop) + 0.5) * self.cell
        return np.meshgrid(xs, ys)

    def cell_of(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Row and column of the cells holding points, and whether each is on the grid."""
        col = np.floor((np.asarray(x) - self.x0) / self.cell).astype(np.int64)
        row = np.floor((self.y0 - np.asarray(y)) / self.cell).astype(np.int64)
        ok = (row >= 0) & (row < self.rows) & (col >= 0) & (col < self.cols)
        return row, col, ok


def grid_for_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    cell: float,
    origin: tuple[float, float] = (0.0, 0.0),
    pad: float = 0.0,
) -> GridSpec:
    """The smallest grid aligned with ``origin`` (a cell corner) that covers a padded box."""
    ox, oy = origin
    x0 = ox + math.floor((xmin - pad - ox) / cell) * cell
    x1 = ox + math.ceil((xmax + pad - ox) / cell) * cell
    y0 = oy - math.floor((oy - (ymax + pad)) / cell) * cell
    y1 = oy - math.ceil((oy - (ymin - pad)) / cell) * cell
    return GridSpec(x0, y0, cell, max(0, round((y0 - y1) / cell)), max(0, round((x1 - x0) / cell)))


# --------------------------------------------------------------------------- polygons


def geojson_polygons(geometry: Mapping[str, Any], move: Any = None) -> list[list[Ring]]:
    """A GeoJSON Polygon or MultiPolygon as polygons, each a list of rings (outer ring first).

    Args:
        geometry: The GeoJSON geometry (``type`` and ``coordinates``).
        move: Optional function ``(x, y) -> (x, y)`` applied to every ring (a projection).
    """
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    polys = coords if kind == "MultiPolygon" else [coords] if kind == "Polygon" else []
    out = []
    for poly in polys:
        rings = []
        for ring in poly:
            r = np.asarray(ring, dtype=float)[:, :2]
            if len(r) < 3:
                continue
            if move is not None:
                x, y = move(r[:, 0], r[:, 1])
                r = np.column_stack([x, y])
            rings.append(r)
        if rings:
            out.append(rings)
    return out


def ring_area(ring: Ring) -> float:
    """Area enclosed by one ring (shoelace formula, always positive)."""
    r = np.asarray(ring, dtype=float)
    x, y = r[:, 0] - r[0, 0], r[:, 1] - r[0, 1]  # shift for precision
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def polygon_area(polygons: Iterable[Sequence[Ring]]) -> float:
    """Area of polygons given as ``[outer, hole, hole, ...]`` ring lists (outer minus holes)."""
    total = 0.0
    for rings in polygons:
        if len(rings):
            total += ring_area(rings[0]) - sum(ring_area(h) for h in rings[1:])
    return total


def rings_bounds(rings: Iterable[Ring]) -> tuple[float, float, float, float]:
    """``(xmin, ymin, xmax, ymax)`` of some rings."""
    pts = np.concatenate([np.asarray(r, dtype=float)[:, :2] for r in rings])
    return (
        float(pts[:, 0].min()),
        float(pts[:, 1].min()),
        float(pts[:, 0].max()),
        float(pts[:, 1].max()),
    )


def _edges(rings: Iterable[Ring]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Every non-horizontal edge of the rings (each ring closed): ``x1, y1, x2, y2``."""
    a, b = [], []
    for ring in rings:
        r = np.asarray(ring, dtype=float)[:, :2]
        if len(r) < 2:
            continue
        a.append(r)
        b.append(np.roll(r, -1, axis=0))
    if not a:
        empty = np.zeros(0)
        return empty, empty, empty, empty
    p, q = np.concatenate(a), np.concatenate(b)
    keep = p[:, 1] != q[:, 1]
    return p[keep, 0], p[keep, 1], q[keep, 0], q[keep, 1]


def _pairs(lo: np.ndarray, hi: np.ndarray) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    """For ranges ``[lo_i, hi_i)``, yield ``(i, j)`` pairs (every j in range i), in chunks."""
    count = np.maximum(hi - lo, 0)
    ends = np.cumsum(count)
    start = 0
    while start < len(count):
        base = ends[start - 1] if start else 0
        stop = int(np.searchsorted(ends, base + _CHUNK, side="right"))
        stop = max(stop, start + 1)
        idx = np.arange(start, stop)
        c = count[start:stop]
        total = int(c.sum())
        if total:
            i = np.repeat(idx, c)
            first = np.cumsum(c) - c
            j = lo[i] + (np.arange(total) - np.repeat(first, c))
            yield i, j
        start = stop


def contains(x: np.ndarray, y: np.ndarray, rings: Iterable[Ring]) -> np.ndarray:
    """Whether each point is inside the rings, by the even-odd rule (ray casting).

    A point on the boundary may fall either way, as in any ray-casting test.
    """
    px = np.asarray(x, dtype=float)
    py = np.asarray(y, dtype=float)
    inside = np.zeros(len(px), dtype=bool)
    x1, y1, x2, y2 = _edges(rings)
    if not len(px) or not len(x1):
        return inside
    order = np.argsort(py, kind="stable")
    ys = py[order]
    ylo, yhi = np.minimum(y1, y2), np.maximum(y1, y2)
    # A ray at height py crosses an edge when ylo <= py < yhi.
    lo = np.searchsorted(ys, ylo, side="left")
    hi = np.searchsorted(ys, yhi, side="left")
    odd = np.zeros(len(px), dtype=np.int64)
    for e, k in _pairs(lo, hi):
        p = order[k]
        xc = x1[e] + (py[p] - y1[e]) * (x2[e] - x1[e]) / (y2[e] - y1[e])
        hit = px[p] < xc
        odd += np.bincount(p[hit], minlength=len(px))
    inside[:] = (odd % 2) == 1
    return inside


def fill_rings(grid: GridSpec, rings: Iterable[Ring]) -> np.ndarray:
    """Cells of a grid whose centre is inside the rings (even-odd rule): a bool array.

    Each edge is crossed with the rows of cell centres it spans; a crossing at
    ``x`` flips every cell of that row whose centre lies left of ``x``, so a
    cell ends inside when it was flipped an odd number of times (the same
    answer as :func:`contains` for its centre).
    """
    out = np.zeros((grid.rows, grid.cols), dtype=bool)
    x1, y1, x2, y2 = _edges(rings)
    if not len(x1) or not grid.rows or not grid.cols:
        return out
    c = grid.cell
    ylo, yhi = np.minimum(y1, y2), np.maximum(y1, y2)
    # Row r has its centre at y0 - (r + 0.5) c; it crosses the edge when ylo <= y < yhi.
    r_lo = np.floor((grid.y0 - yhi) / c - 0.5).astype(np.int64) + 1
    r_hi = np.floor((grid.y0 - ylo) / c - 0.5).astype(np.int64) + 1
    r_lo = np.clip(r_lo, 0, grid.rows)
    r_hi = np.clip(r_hi, 0, grid.rows)
    width = grid.cols + 1
    flips = np.zeros(grid.rows * width, dtype=np.uint8)
    for e, r in _pairs(r_lo, r_hi):
        yc = grid.y0 - (r + 0.5) * c
        xc = x1[e] + (yc - y1[e]) * (x2[e] - x1[e]) / (y2[e] - y1[e])
        # number of cell centres strictly left of xc
        k = np.clip(np.ceil((xc - grid.x0) / c - 0.5), 0, grid.cols).astype(np.int64)
        np.bitwise_xor.at(flips, r * width + k, 1)
    f = flips.reshape(grid.rows, width)
    # cell j is inside when the crossings with k > j are odd: a running XOR from the right
    suffix = np.bitwise_xor.accumulate(f[:, ::-1], axis=1)[:, ::-1]
    out[:] = suffix[:, 1:].astype(bool)
    return out


def fill_discs(grid: GridSpec, cx: np.ndarray, cy: np.ndarray, radius: float) -> np.ndarray:
    """Cells whose centre is within ``radius`` of any of the points: a bool array (the union)."""
    out = np.zeros((grid.rows, grid.cols), dtype=bool)
    cx = np.asarray(cx, dtype=float)
    cy = np.asarray(cy, dtype=float)
    if not len(cx) or not grid.rows or not grid.cols:
        return out
    c = grid.cell
    # rows whose centre y is within radius of cy
    r_lo = np.clip(np.ceil((grid.y0 - (cy + radius)) / c - 0.5), 0, grid.rows).astype(np.int64)
    r_hi = np.clip(np.floor((grid.y0 - (cy - radius)) / c - 0.5) + 1, 0, grid.rows).astype(np.int64)
    width = grid.cols + 1
    marks = np.zeros(grid.rows * width, dtype=np.int32)
    for d, r in _pairs(r_lo, r_hi):
        dy = grid.y0 - (r + 0.5) * c - cy[d]
        h = np.sqrt(np.maximum(radius * radius - dy * dy, 0.0))
        c0 = np.clip(np.ceil((cx[d] - h - grid.x0) / c - 0.5), 0, grid.cols).astype(np.int64)
        c1 = np.clip(np.floor((cx[d] + h - grid.x0) / c - 0.5) + 1, 0, grid.cols).astype(np.int64)
        ok = c1 > c0
        np.add.at(marks, r[ok] * width + c0[ok], 1)
        np.add.at(marks, r[ok] * width + c1[ok], -1)
    cover = np.cumsum(marks.reshape(grid.rows, width), axis=1, dtype=np.int32)
    out[:] = cover[:, :-1] > 0
    return out


def fill_capsules(grid: GridSpec, segments: np.ndarray, width: float) -> np.ndarray:
    """Cells whose centre is within ``width`` of any segment: a bool array (the union).

    Args:
        grid: The grid.
        segments: An ``(n, 2, 2)`` array of segment end points ``[[x1, y1], [x2, y2]]``.
        width: The distance added on every side (the buffer).
    """
    out = np.zeros(grid.rows * grid.cols, dtype=bool)
    seg = np.asarray(segments, dtype=float).reshape(-1, 2, 2)
    if not len(seg) or not grid.rows or not grid.cols:
        return out.reshape(grid.rows, grid.cols)
    ax, ay, bx, by = seg[:, 0, 0], seg[:, 0, 1], seg[:, 1, 0], seg[:, 1, 1]
    c = grid.cell
    c0 = np.clip(np.ceil((np.minimum(ax, bx) - width - grid.x0) / c - 0.5), 0, grid.cols)
    c1 = np.clip(np.floor((np.maximum(ax, bx) + width - grid.x0) / c - 0.5) + 1, 0, grid.cols)
    r0 = np.clip(np.ceil((grid.y0 - (np.maximum(ay, by) + width)) / c - 0.5), 0, grid.rows)
    r1 = np.clip(np.floor((grid.y0 - (np.minimum(ay, by) - width)) / c - 0.5) + 1, 0, grid.rows)
    c0, c1, r0, r1 = (v.astype(np.int64) for v in (c0, c1, r0, r1))
    ncol = np.maximum(c1 - c0, 0)
    nrow = np.maximum(r1 - r0, 0)
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    w2 = width * width
    for s, t in _pairs(np.zeros(len(seg), dtype=np.int64), ncol * nrow):
        row = r0[s] + t // ncol[s]
        col = c0[s] + t % ncol[s]
        px = grid.x0 + (col + 0.5) * c - ax[s]
        py = grid.y0 - (row + 0.5) * c - ay[s]
        u = np.where(l2[s] > 0, (px * dx[s] + py * dy[s]) / np.where(l2[s] > 0, l2[s], 1.0), 0.0)
        u = np.clip(u, 0.0, 1.0)
        ex, ey = px - u * dx[s], py - u * dy[s]
        hit = ex * ex + ey * ey <= w2
        out[row[hit] * grid.cols + col[hit]] = True
    return out.reshape(grid.rows, grid.cols)
