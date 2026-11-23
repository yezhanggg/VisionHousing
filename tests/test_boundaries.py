"""Boundaries: shapefile reading, Douglas-Peucker simplification, GeoJSON output (no network)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from tests.pack_fakes import FileServer, client, sample_features, shape_zip
from visionhousing.data.registry import get_source
from visionhousing.data.sources import boundaries as bd


def test_douglas_peucker_by_hand():
    line = np.array([[0, 0], [1, 0.05], [2, -0.05], [3, 1.0], [4, 0.0]])
    assert list(bd.douglas_peucker(line, 0.1)) == [0, 2, 3, 4]  # (3, 1.0) is far; (1, .05) is not
    assert list(bd.douglas_peucker(line, 2.0)) == [0, 4]
    assert list(bd.douglas_peucker(line, 0.0)) == [0, 1, 2, 3, 4]
    assert list(bd.douglas_peucker(line[:2], 1.0)) == [0, 1]


def test_ring_area_sign():
    ccw = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    assert bd.ring_area(ccw) == 1.0 and bd.ring_area(ccw[::-1]) == -1.0


def circle(n=400, r=1.0, cw=False):
    angles = np.linspace(0, 2 * math.pi, n, endpoint=False)
    pts = [[r * math.cos(a), r * math.sin(a)] for a in (angles[::-1] if cw else angles)]
    return [*pts, pts[0]]


def test_simplify_keeps_shape_rounds_and_orients():
    holey = {"type": "Polygon", "coordinates": [circle(cw=True), circle(r=0.3)]}
    out = bd.simplify_geometry(holey, 0.01, digits=5)
    assert out["type"] == "Polygon"
    outer, hole = out["coordinates"]
    assert bd.ring_area(outer) > 0 and bd.ring_area(hole) < 0  # RFC 7946 order
    assert 8 < len(outer) < 400 and outer[0] == outer[-1]
    assert abs(bd.ring_area(outer) - math.pi) < 0.05
    for x, y in outer:
        assert round(x, 5) == x and round(y, 5) == y


def test_tiny_islands_drop_but_a_shape_never_vanishes():
    tiny = [[0, 0], [1e-6, 0], [1e-6, 1e-6], [0, 0]]
    multi = {"type": "MultiPolygon", "coordinates": [[circle()], [tiny]]}
    out = bd.simplify_geometry(multi, 0.01)
    assert out["type"] == "Polygon"  # the island collapsed and was dropped
    alone = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [0.002, 0], [0.002, 0.002], [0, 0.002], [0, 0]]],
    }
    kept = bd.simplify_geometry(alone, 0.01)  # coarser than the shape itself
    assert len(kept["coordinates"][0]) >= 4


def test_read_counties_filters_to_the_state():
    counties = sample_features("county")
    other = json.loads(json.dumps(counties[0]))
    other["properties"]["geoid"] = "98001"
    blob = shape_zip([*counties, other])
    shapes = bd.read_counties(blob, "99")
    assert [s.geoid for s in shapes] == sorted(f["properties"]["geoid"] for f in counties)
    assert shapes[0].name == counties[0]["properties"]["name"]
    assert bd.read_counties(shape_zip([other]), "99") == []


def test_write_boundaries_one_county_file_and_one_tract_file_per_county(tmp_path):
    counties = bd.read_counties(shape_zip(sample_features("county")), "99")
    tracts = bd.read_tracts(shape_zip(sample_features("tract")))
    files = bd.write_boundaries(counties, tracts, tmp_path)
    names = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    assert names[0] == "counties.geojson"
    assert names[1:] == [f"tracts/{c.geoid}.geojson" for c in counties]
    data = json.loads((tmp_path / "counties.geojson").read_text(encoding="utf-8"))
    assert {f["properties"]["level"] for f in data["features"]} == {"county"}
    ids = set()
    for p in (tmp_path / "tracts").iterdir():
        feats = json.loads(p.read_text(encoding="utf-8"))["features"]
        assert all(f["properties"]["geoid"].startswith(p.stem) for f in feats)
        ids |= {f["properties"]["geoid"] for f in feats}
    assert ids == {t.geoid for t in tracts}
    again = bd.write_boundaries(counties, tracts, tmp_path / "again")
    assert [p.read_bytes() for p in files] == [p.read_bytes() for p in again]


def test_county_budget_is_enforced(tmp_path, monkeypatch):
    counties = bd.read_counties(shape_zip(sample_features("county")), "99")
    monkeypatch.setattr(bd, "COUNTY_BUDGET_BYTES", 100)
    with pytest.raises(ValueError, match="budget"):
        bd.write_boundaries(counties, [], tmp_path)


def test_fetch_keeps_only_state_extracts_and_reuses_them(tmp_path, monkeypatch):
    county_url = get_source("cb_2020_county").url()
    tract_url = get_source("cb_2020_tract").url(state_fips="99")
    server = FileServer(
        {
            county_url: shape_zip(sample_features("county")),
            tract_url: shape_zip(sample_features("tract")),
        }
    )
    monkeypatch.setattr(bd, "to_state_fips", lambda s: "99")
    counties, tracts = bd.fetch_boundaries("TL", tmp_path, client=client(server), manifest=None)
    assert len(counties) == len(sample_features("county")) and len(tracts) == len(
        sample_features("tract")
    )
    kept = sorted(p.name for p in (tmp_path / "boundaries").iterdir())
    assert kept == ["cb_2020_99_county_500k.geojson.gz", "cb_2020_99_tract_500k.geojson.gz"]
    server.urls.clear()
    again = bd.fetch_boundaries("TL", tmp_path, client=client(server), manifest=None)
    assert server.urls == [] and again == (counties, tracts)
