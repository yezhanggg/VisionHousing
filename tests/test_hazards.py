"""Hazards and land screen: projection, GeoTIFF reading, burning shapes, counts, tables, fetchers.

Everything runs on tiny synthetic grids and a fake HTTP client; no network.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.http import FetchError
from visionhousing.data.sources import hazards as src
from visionhousing.data.sources.geotiff import (
    GeoTiffError,
    Grid,
    read_geotiff,
    write_geotiff,
)
from visionhousing.features import hazards as hz
from visionhousing.features.hazards_build import county_counts, rules_hash

RULES = {
    "grid": {"cell_m": 30, "origin": [0, 0], "pad_m": 0},
    "landcover": {
        "year": 2025,
        "developed_classes": [21, 22, 23, 24],
        "water_classes": [11],
        "empty_classes": [0, 250],
    },
    "slope": {"steep_percent": 15},
    "flood": {"where": "SFHA_TF = 'T'", "max_offset_m": 5, "min_nfhl_coverage": 0.5},
}


# --------------------------------------------------------------------------- projection


def test_albers_matches_femas_own_projection():
    # Two vertices of an NFHL polygon in Philadelphia, read from FEMA's server in
    # NAD83 (EPSG:4269) and in Conus Albers (EPSG:5070) on 2026-11-24.
    x, y = hz.to_albers(
        [-75.19368340012278, -75.19368284875043], [39.8751640114815, 39.87518137119713]
    )
    assert x[0] == pytest.approx(1749690.9982058234, abs=1e-3)
    assert y[0] == pytest.approx(2065294.8338859035, abs=1e-3)
    assert x[1] == pytest.approx(1749690.621345195, abs=1e-3)
    assert y[1] == pytest.approx(2065296.742811473, abs=1e-3)


def test_albers_origin_is_zero():
    x, y = hz.to_albers(-96.0, 23.0)
    assert abs(float(x)) < 1e-6 and abs(float(y)) < 1e-6


def test_grid_for_bounds_snaps_to_the_origin():
    g = hz.grid_for_bounds(100, 110, 250, 230, 30, (15, 15), pad=0)
    assert (g.x0 - 15) % 30 == 0 and (g.y0 - 15) % 30 == 0
    xmin, ymin, xmax, ymax = g.bbox
    assert xmin <= 100 and ymin <= 110 and xmax >= 250 and ymax >= 230
    assert g.cols == round((xmax - xmin) / 30) and g.rows == round((ymax - ymin) / 30)


# --------------------------------------------------------------------------- GeoTIFF


@pytest.mark.parametrize("tile, deflate", [(None, False), (4, False), (4, True), (None, True)])
def test_geotiff_round_trip(tile, deflate):
    values = np.arange(35, dtype="f4").reshape(5, 7)
    data = write_geotiff(Grid(values, 10.0, 20.0, 30.0, 30.0, -9999.0), tile=tile, deflate=deflate)
    got = read_geotiff(data)
    assert (got.values == values).all()
    assert (got.x0, got.y0, got.dx, got.dy, got.nodata) == (10.0, 20.0, 30.0, 30.0, -9999.0)


def _drop_tile(data: bytes, k: int) -> bytes:
    """Make tile ``k`` sparse (offset and byte count 0), as the elevation service does."""
    import struct

    out = bytearray(data)
    ifd = struct.unpack("<I", out[4:8])[0]
    for i in range(struct.unpack("<H", out[ifd : ifd + 2])[0]):
        at = ifd + 2 + 12 * i
        tag, _typ, num, val = struct.unpack("<HHII", out[at : at + 12])
        if tag in (324, 325):
            pos = val + 4 * k if num > 1 else at + 8
            out[pos : pos + 4] = (0).to_bytes(4, "little")
    return bytes(out)


@pytest.mark.parametrize("nodata", [None, -9999.0])
def test_geotiff_sparse_tiles_read_as_no_data(nodata):
    values = np.arange(64, dtype="f4").reshape(8, 8)
    data = _drop_tile(write_geotiff(Grid(values, 0.0, 0.0, 30.0, 30.0, nodata), tile=4), 1)
    got = read_geotiff(data).values
    block = got[0:4, 4:8]
    assert np.isnan(block).all() if nodata is None else (block == nodata).all()
    assert (got[4:, :] == values[4:, :]).all() and (got[0:4, 0:4] == values[0:4, 0:4]).all()


def test_geotiff_reads_uint8():
    values = np.array([[11, 21], [250, 82]], dtype="u1")
    got = read_geotiff(write_geotiff(Grid(values, 0.0, 60.0, 30.0, 30.0, 250.0)))
    assert got.values.dtype == np.uint8 and (got.values == values).all()


def test_geotiff_refuses_what_it_cannot_read():
    with pytest.raises(GeoTiffError, match="not a TIFF"):
        read_geotiff(b"<?xml version='1.0'?><ows:ExceptionReport/>")
    data = bytearray(write_geotiff(Grid(np.zeros((2, 2), "u1"), 0, 0, 1, 1)))
    # Patch the compression tag (259) value to LZW (5).
    pos = data.find((259).to_bytes(2, "little") + (3).to_bytes(2, "little"))
    data[pos + 8 : pos + 10] = (5).to_bytes(2, "little")
    with pytest.raises(GeoTiffError, match="compression 5"):
        read_geotiff(bytes(data))


# --------------------------------------------------------------------------- burning


def _grid(rows=10, cols=10):
    return hz.GridSpec(0.0, rows * 30.0, 30.0, rows, cols)


def test_burn_rings_uses_even_odd_for_holes():
    g = _grid()
    outer = [[0, 0], [300, 0], [300, 300], [0, 300], [0, 0]]
    hole = [[90, 90], [210, 90], [210, 210], [90, 210], [90, 90]]
    mask = hz.burn_rings(g, [[outer, hole]])
    assert mask.sum() == 100 - 16
    assert mask[4, 4] == 0 and mask[0, 0] == 1


def _box(x0, y0, x1, y1):
    return [np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]], dtype=float)]


def test_label_tracts_by_cell_centre():
    g = _grid()
    labels = hz.label_tracts(g, [_box(0, 0, 150, 300), _box(150, 0, 300, 300)])
    assert (labels[:, :5] == 1).all() and (labels[:, 5:] == 2).all()


def test_project_geometry_keeps_every_ring():
    ring = [[-75.0, 40.0], [-74.99, 40.0], [-74.99, 40.01], [-75.0, 40.01], [-75.0, 40.0]]
    hole = [[-74.996, 40.004], [-74.994, 40.004], [-74.994, 40.006], [-74.996, 40.004]]
    rings = hz.project_geometry({"type": "MultiPolygon", "coordinates": [[ring, hole], [ring]]})
    assert len(rings) == 3 and rings[0].shape == (5, 2)
    x, y = hz.to_albers(-75.0, 40.0)
    assert rings[0][0, 0] == pytest.approx(float(x)) and rings[0][0, 1] == pytest.approx(float(y))


def test_slope_of_a_plane():
    xs = np.arange(10) * 30.0
    dem = np.tile(0.2 * xs, (10, 1)) + np.arange(10)[:, None] * 30.0 * 0.0
    slope = hz.slope_percent(dem, 30.0)
    assert np.allclose(slope, 20.0)
    dem[0, 0] = -3.4e38
    slope = hz.slope_percent(dem, 30.0)
    assert np.isnan(slope[0, 0]) and np.isnan(slope[0, 1])


# --------------------------------------------------------------------------- counts and tables


def _scene():
    """A 10 x 10 grid split into two tracts (west, east)."""
    g = _grid()
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[:, :5], labels[:, 5:] = 1, 2
    lc = np.full((10, 10), 41, dtype=np.uint8)  # forest
    lc[:, 3:5] = 22  # 20 developed cells in the west
    lc[0, 0] = 11  # one water cell
    imp = np.zeros((10, 10), dtype=np.uint8)
    imp[:, 3:5] = 50
    slope = np.zeros((10, 10))
    slope[:, 5:] = 25.0  # the east is steep
    sfha = np.zeros((10, 10), dtype=np.uint8)
    sfha[:, 4] = 1  # one developed column in the floodplain
    nfhl = np.zeros((10, 10), dtype=np.uint8)
    nfhl[:, :5] = 1  # only the west has a digital flood map
    blocks = pd.DataFrame(
        {
            "tract": ["A", "A", "B"],
            "housing_units": [10, 30, 5],
            "x": [4 * 30 + 15, 3 * 30 + 15, 8 * 30 + 15],
            "y": [285, 285, 285],
        }
    )
    return g, labels, lc, imp, slope, sfha, nfhl, blocks


def test_tract_counts_and_shares():
    g, labels, lc, imp, slope, sfha, nfhl, blocks = _scene()
    counts = hz.tract_counts(
        labels, ["A", "B"], lc, imp, slope, sfha, nfhl, RULES, blocks=blocks, grid=g
    )
    a, b = counts.iloc[0], counts.iloc[1]
    assert a["land_cells"] == 49 and b["land_cells"] == 50
    assert a["developed_cells"] == 20 and a["sfha_developed_cells"] == 10
    assert a["impervious_sum"] == 20 * 50 and a["impervious_cells"] == 49
    assert a["nfhl_developed_cells"] == 20 and b["nfhl_developed_cells"] == 0
    assert b["steep_cells"] == 50 and a["steep_cells"] == 0
    assert a["homes"] == 40 and a["sfha_homes"] == 10 and b["homes"] == 5
    table = hz.tract_table(counts, RULES)
    ta, tb = table.iloc[0], table.iloc[1]
    assert ta["sfha_share"] == 0.25 and ta["sfha_dev_share"] == 0.5
    assert ta["developed_share"] == round(20 / 49, 3)
    assert ta["impervious_share"] == round(10 / 49, 3)
    assert tb["steep_share"] == 1.0
    # The east has no digital flood map: flood shares are blank, not zero.
    assert pd.isna(tb["sfha_share"]) and pd.isna(tb["sfha_dev_share"])
    assert list(table.columns) == list(hz.TRACT_COLUMNS)


def test_county_table_adds_counts():
    g, labels, lc, imp, slope, sfha, nfhl, blocks = _scene()
    counts = hz.tract_counts(
        labels,
        ["42001000100", "42001000200"],
        lc,
        imp,
        slope,
        sfha,
        nfhl,
        RULES,
        blocks=blocks.assign(tract=["42001000100", "42001000100", "42001000200"]),
        grid=g,
    )
    county = hz.county_table(
        counts, {"42001": {"sfha_polygons": 3, "read_on": "2026-11-24"}}, RULES
    )
    row = county.iloc[0]
    assert row["tracts"] == 2 and row["tracts_flood_unmapped"] == 1
    assert row["homes"] == 45 and row["sfha_homes"] == 10 and row["nfhl_homes"] == 40
    assert row["nfhl_developed_acres"] == round(20 * 900 / 4046.8564224)
    assert row["sfha_share"] == 0.25  # mapped tracts only
    assert row["steep_share"] == round(50 / 99, 3)
    assert row["nfhl_coverage"] == round(49 / 99, 3)
    assert row["sfha_polygons"] == 3 and row["nlcd_year"] == 2025
    assert list(county.columns) == list(hz.COUNTY_COLUMNS)


def test_flood_shares_count_only_mapped_homes_and_land():
    """Half the west tract's homes are outside FEMA's map: they are not counted as dry."""
    g, labels, lc, imp, slope, sfha, nfhl, blocks = _scene()
    nfhl = nfhl.copy()
    nfhl[:, 3] = 0  # the map stops at column 4: the block at column 3 (30 homes) is unmapped
    counts = hz.tract_counts(
        labels, ["A", "B"], lc, imp, slope, sfha, nfhl, RULES, blocks=blocks, grid=g
    )
    a = counts.iloc[0]
    assert a["nfhl_homes"] == 10 and a["nfhl_developed_cells"] == 10
    # only 10 of 40 homes are mapped (25% < 50%): the flood shares are blank
    table = hz.tract_table(counts, RULES)
    assert pd.isna(table.iloc[0]["sfha_share"])
    lenient = {**RULES, "flood": {**RULES["flood"], "min_nfhl_coverage": 0.2}}
    ta = hz.tract_table(counts, lenient).iloc[0]
    assert ta["sfha_share"] == 1.0  # 10 SFHA homes / 10 mapped homes, not / 40
    assert ta["sfha_dev_share"] == 1.0  # 10 developed SFHA cells / 10 mapped developed cells


def test_mapped_is_judged_by_homes_then_land():
    rules = RULES
    counts = pd.DataFrame(
        {
            "homes": [100, 100, 0, 0],
            "nfhl_homes": [60, 40, 0, 0],
            "land_cells": [10, 10, 10, 10],
            "nfhl_cells": [0, 10, 6, 4],
        }
    )
    assert hz.flood_mapped(counts, rules).tolist() == [True, False, True, False]


def test_impervious_mean_uses_valid_cells_only():
    g, labels, lc, imp, slope, sfha, nfhl, blocks = _scene()
    imp = imp.copy()
    imp[5:, :3] = 255  # no value (outside the 0-100 range) in 15 west land cells
    counts = hz.tract_counts(
        labels, ["A", "B"], lc, imp, slope, sfha, nfhl, RULES, blocks=blocks, grid=g
    )
    a = counts.iloc[0]
    assert a["impervious_cells"] == 49 - 15 and a["impervious_sum"] == 1000
    assert hz.tract_table(counts, RULES).iloc[0]["impervious_share"] == round(10 / 34, 3)


def test_every_column_is_described():
    for table, cols in (("hazard_tract", hz.TRACT_COLUMNS), ("hazard_county", hz.COUNTY_COLUMNS)):
        for name in cols:
            unit, text = hz.describe(table, name)
            assert unit and text
    with pytest.raises(ValueError):
        hz.describe("hazard_tract", "nope")


def test_shipped_rules_load():
    rules = hz.load_rules()
    assert rules["slope"]["steep_percent"] == 15
    assert rules["landcover"]["developed_classes"] == [21, 22, 23, 24]
    assert rules_hash(rules) == rules_hash(dict(rules))


# --------------------------------------------------------------------------- fetchers (fake server)


class FakeClient:
    """Answers by URL: GeoTIFF bytes for grids, JSON pages for the flood layers."""

    def __init__(
        self, grid: hz.GridSpec, *, misplace: bool = False, drop: bool = False, extra: int = 0
    ):
        self.grid, self.misplace, self.drop, self.extra = grid, misplace, drop, extra
        self.calls: list[tuple[str, dict]] = []

    def get_bytes(self, url, params=None):
        params = dict(params or {})
        self.calls.append((url, params))
        g = self.grid
        if url == src.WCS_URL:
            lc = np.full((g.rows, g.cols), 22 if "Land-Cover" in params["coverage"] else 40, "u1")
            x0 = g.x0 + (30 if self.misplace else 0)
            return write_geotiff(Grid(lc, x0, g.y0, g.cell, g.cell, 250.0))
        if url.endswith("/exportImage"):
            dem = np.tile(np.arange(g.cols, dtype="f4") * 30 * 0.1, (g.rows, 1))
            return write_geotiff(Grid(dem, g.x0, g.y0, g.cell, g.cell))
        if url.endswith("/query"):
            ring = [
                [g.x0, g.y0 - g.rows * g.cell],
                [g.x0 + 60, g.y0 - g.rows * g.cell],
                [g.x0 + 60, g.y0],
                [g.x0, g.y0],
                [g.x0, g.y0 - g.rows * g.cell],
            ]
            if params.get("returnCountOnly"):
                return json.dumps({"count": 2}).encode()
            # Two polygons by count; the page holds what is left from the offset, one fewer
            # when dropping and some more when the server's count was short.
            offset = int(params["resultOffset"])
            n = max(2 + self.extra - offset - (1 if self.drop else 0), 0)
            feats = [
                {"attributes": {"OBJECTID": offset + k + 1}, "geometry": {"rings": [ring]}}
                for k in range(n)
            ]
            return json.dumps({"features": feats}).encode()
        raise AssertionError(url)


def test_nlcd_and_dem_grids_are_checked_for_placement():
    g = hz.GridSpec(1000.0, 2000.0, 30.0, 4, 6)
    reads = src.Reads()
    got = src.nlcd_grid(FakeClient(g), "landcover", 2025, g, reads)
    assert got.shape == (4, 6) and reads.rows[0]["source"] == "usgs_annual_nlcd"
    assert src.dem_grid(FakeClient(g), g).shape == (4, 6)
    with pytest.raises(FetchError, match="placed"):
        src.nlcd_grid(FakeClient(g, misplace=True), "landcover", 2025, g)


def test_flood_pages_must_add_up_to_the_count():
    g = hz.GridSpec(0.0, 300.0, 30.0, 10, 10)
    feats = src.flood_zones(FakeClient(g), g, RULES)
    assert len(feats) == 2
    with pytest.raises(FetchError, match="1 of 2"):
        src.flood_zones(FakeClient(g, drop=True), g, RULES)
    # A count one short of what the pages hold is accepted (FEMA's count can be).
    assert len(src.flood_zones(FakeClient(g, extra=1), g, RULES)) == 3


def test_county_counts_with_a_fake_server():
    lon, lat = -77.0, 40.0
    d = 0.01
    shapes = [
        (
            "42001000100",
            {
                "type": "Polygon",
                "coordinates": [
                    [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
                ],
            },
        )
    ]
    x0, y0 = hz.to_albers(lon, lat)
    grid = hz.grid_for_bounds(
        float(x0) - 100, float(y0) - 100, float(x0) + 1000, float(y0) + 1200, 30, (0, 0), pad=90
    )
    blocks = pd.DataFrame(
        {"tract": ["42001000100"], "housing_units": [7], "lon": [lon + d / 2], "lat": [lat + d / 2]}
    )
    rules = {**RULES, "grid": {"cell_m": 30, "origin": [0, 0], "pad_m": 90}}

    class Client(FakeClient):
        def get_bytes(self, url, params=None):
            # The grid is the county's own: rebuild it from the request.
            if "bbox" in (params or {}):
                b = [float(v) for v in params["bbox"].split(",")]
                self.grid = hz.GridSpec(
                    b[0], b[3], 30.0, round((b[3] - b[1]) / 30), round((b[2] - b[0]) / 30)
                )
            return super().get_bytes(url, params)

    counts, note = county_counts("42001", shapes, blocks, Client(grid), rules)
    row = counts.iloc[0]
    assert row["land_cells"] > 0 and row["developed_cells"] == row["land_cells"]
    assert row["homes"] == 7
    assert note["sfha_polygons"] == 2 and note["nlcd_year"] == 2025
    assert {r["source"] for r in note["reads"]} == {
        "usgs_annual_nlcd",
        "usgs_3dep_dem",
        "fema_nfhl",
    }


class DemClient:
    """Answers exportImage for any bbox: elevation = x / 10; the first reply is cut short."""

    def __init__(self, short_first: int = 1):
        self.short = short_first
        self.calls = 0

    def get_bytes(self, url, params=None):
        assert url.endswith("/exportImage")
        self.calls += 1
        xmin, _, xmax, ymax = (float(v) for v in params["bbox"].split(","))
        cols, rows = (int(v) for v in params["size"].split(","))
        cell = (xmax - xmin) / cols
        xs = xmin + (np.arange(cols) + 0.5) * cell
        data = write_geotiff(Grid(np.tile(xs / 10, (rows, 1)).astype("f4"), xmin, ymax, cell, cell))
        if self.short:
            self.short -= 1
            return data[: len(data) // 3]
        return data


def test_dem_is_read_in_tiles_and_short_replies_are_retried(monkeypatch):
    monkeypatch.setattr(src, "DEM_TILE", 3)
    g = hz.GridSpec(1000.0, 2000.0, 30.0, 5, 7)
    client = DemClient(short_first=1)
    reads = src.Reads()
    got = src.dem_grid(client, g, reads)
    assert got.shape == (5, 7) and (got.x0, got.y0) == (1000.0, 2000.0)
    xs = 1000.0 + (np.arange(7) + 0.5) * 30.0
    assert np.allclose(got.values, np.tile(xs / 10, (5, 1)))
    assert len(reads.rows) == 6 and client.calls == 7  # 2 x 3 tiles, one asked twice
    with pytest.raises(FetchError, match="3 tries"):
        src.dem_grid(DemClient(short_first=99), g)
