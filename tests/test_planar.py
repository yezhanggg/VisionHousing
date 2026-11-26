"""Flat-map geometry in plain NumPy: point in polygon, shoelace area, shapes on grids."""

from __future__ import annotations

import math

import numpy as np
import pytest

from visionhousing.features import planar

SQUARE = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], dtype=float)
HOLE = np.array([[3, 3], [7, 3], [7, 7], [3, 7]], dtype=float)  # not closed: closing implied


def test_contains_uses_even_odd_for_holes():
    x = np.array([1.0, 5.0, 9.5, 11.0, -0.5, 5.0])
    y = np.array([1.0, 5.0, 9.5, 5.0, 5.0, 2.0])
    got = planar.contains(x, y, [SQUARE, HOLE])
    assert got.tolist() == [True, False, True, False, False, True]
    assert not planar.contains(x, y, []).any()


def test_contains_on_a_concave_ring():
    # a U shape: the notch (x 4-6, y 4-10) is outside
    u = np.array([[0, 0], [10, 0], [10, 10], [6, 10], [6, 4], [4, 4], [4, 10], [0, 10]], float)
    got = planar.contains(np.array([5.0, 5.0, 2.0, 8.0]), np.array([8.0, 2.0, 8.0, 8.0]), [u])
    assert got.tolist() == [False, True, True, True]


def test_shoelace_area_outer_minus_holes():
    assert planar.ring_area(SQUARE) == 100.0
    assert planar.ring_area(SQUARE[::-1]) == 100.0  # direction does not matter
    assert planar.polygon_area([[SQUARE, HOLE], [SQUARE + 20]]) == 100 - 16 + 100


def test_fill_rings_matches_contains_on_cell_centres():
    g = planar.GridSpec(-2.0, 12.0, 0.5, 28, 28)
    mask = planar.fill_rings(g, [SQUARE, HOLE])
    xs, ys = g.centres(slice(0, g.rows), slice(0, g.cols))
    want = planar.contains(xs.ravel(), ys.ravel(), [SQUARE, HOLE]).reshape(xs.shape)
    assert (mask == want).all()
    assert mask.sum() * g.cell_area == pytest.approx(84.0)


def test_fill_rings_clips_shapes_larger_than_the_grid():
    g = planar.GridSpec(2.0, 8.0, 1.0, 4, 4)  # inside the square, around the hole
    mask = planar.fill_rings(g, [SQUARE, HOLE])
    assert mask.sum() == 16 - 9  # cells with centres 3.5..5.5 are in the hole


def test_discs_and_capsules_are_unions():
    g = planar.grid_for_bounds(-50, -50, 150, 50, 1.0)
    one = planar.fill_discs(g, np.array([0.0]), np.array([0.0]), 20.0)
    both = planar.fill_discs(g, np.array([0.0, 10.0]), np.array([0.0, 0.0]), 20.0)
    assert one.sum() == pytest.approx(math.pi * 400, rel=0.01)
    assert (both | one).sum() == both.sum() and both.sum() > one.sum()
    seg = np.array([[[0.0, 0.0], [100.0, 0.0]], [[50.0, 0.0], [100.0, 0.0]]])
    cap = planar.fill_capsules(g, seg, 5.0)
    assert cap.sum() == pytest.approx(2 * 5 * 100 + math.pi * 25, rel=0.02)
    dot = planar.fill_capsules(g, np.array([[[0.0, 0.0], [0.0, 0.0]]]), 5.0)  # zero length
    assert dot.sum() == pytest.approx(math.pi * 25, rel=0.1)


def test_grid_for_bounds_and_window():
    g = planar.grid_for_bounds(1.0, 1.0, 19.0, 9.0, 10.0)
    assert (g.x0, g.y0, g.rows, g.cols) == (0.0, 10.0, 1, 2)
    sub = g.sub(slice(0, 1), slice(1, 2))
    assert (sub.x0, sub.y0, sub.rows, sub.cols) == (10.0, 10.0, 1, 1)


def test_geojson_polygons_projects_every_ring():
    geom = {"type": "Polygon", "coordinates": [SQUARE.tolist(), HOLE.tolist()]}
    polys = planar.geojson_polygons(geom, lambda x, y: (x * 2, y))
    assert len(polys) == 1 and len(polys[0]) == 2
    assert planar.polygon_area(polys) == 2 * 84.0
    assert planar.geojson_polygons({"type": "Point", "coordinates": [0, 0]}) == []
