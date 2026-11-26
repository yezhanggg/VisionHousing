"""Massachusetts 3A modeled capacity by tract and MassDEP estimated sewer (no network)."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from visionhousing.data import local
from visionhousing.data.http import FetchError
from visionhousing.data.sources import infrastructure as infra_src
from visionhousing.data.sources import ma_capacity as src
from visionhousing.features import ma_capacity as fx

# Two tracts side by side, each 0.01 x 0.01 degrees: west [0, 0.01], east [0.01, 0.02].
# Coordinates are lon/lat near 42 N, as in Massachusetts.
LAT = 42.0


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def gj(ring):
    return {"type": "Polygon", "coordinates": [ring]}


TRACTS = [
    ("25001000100", gj(box(-71.02, LAT, -71.01, LAT + 0.01))),
    ("25001000200", gj(box(-71.01, LAT, -71.00, LAT + 0.01))),
]
RULE = fx.SplitRule(cells_per_side=200, min_cell_m=0.1, min_piece_share=0.05)


def parcel(oid, ring, cap, *, zone="Z1", muni=1, loc=None, station="Y", existing=2.0):
    return {
        "attributes": {
            "OBJECTID": oid, "LOC_ID": loc or f"L{oid}", "ZONECOD": zone, "MUNIID": muni,
            "MUNI": "Town", "STATUS": "Compliant", "TRNSTST": station, "PRCLACR": 1.0,
            "RB_RSDN": existing, "MDL_U_C": cap * 2, "F_L_M__": cap,
        },
        "rings": [ring],
    }  # fmt: skip


def districts_frame(rows):
    return pd.DataFrame(
        rows,
        columns=["district_id", "zone_code", "muni_id", "municipality", "capacity_district",
                 "geometry"],
    )  # fmt: skip


# --------------------------------------------------------------------------- splitting


def test_split_inside_one_tract_goes_wholly_to_it():
    index = fx.TractIndex.build(TRACTS)
    s = fx.split_shape([np.asarray(box(-71.018, 42.002, -71.016, 42.004))], index, RULE)
    assert s.shares == {"25001000100": 1.0}
    assert s.method == "corners" and s.unmatched == 0


def test_split_across_tracts_by_area_not_centre():
    # 1/4 west, 3/4 east: a centre rule would put it all in the east tract
    index = fx.TractIndex.build(TRACTS)
    s = fx.split_shape([np.asarray(box(-71.011, 42.002, -71.007, 42.004))], index, RULE)
    assert s.method == "grid"
    assert s.shares["25001000100"] == pytest.approx(0.25, abs=0.02)
    assert s.shares["25001000200"] == pytest.approx(0.75, abs=0.02)
    assert sum(s.shares.values()) + s.unmatched == pytest.approx(1.0)


def test_split_reports_unmatched_and_folds_slivers():
    index = fx.TractIndex.build(TRACTS)
    # half of it is south of both tracts (water): unmatched, not dropped
    s = fx.split_shape([np.asarray(box(-71.018, 41.998, -71.016, 42.002))], index, RULE)
    assert s.unmatched == pytest.approx(0.5, abs=0.02)
    assert sum(s.shares.values()) + s.unmatched == pytest.approx(1.0)
    # 2% across the tract line: a sliver folded into the larger piece
    t = fx.split_shape([np.asarray(box(-71.0102, 42.002, -71.0002, 42.004))], index, RULE)
    assert t.shares == {"25001000200": pytest.approx(1.0)}
    assert t.sliver == pytest.approx(0.02, abs=0.01)


# --------------------------------------------------------------------------- capacity


def test_split_across_tracts_conserves_capacity():
    feats = [
        parcel(1, box(-71.018, 42.002, -71.016, 42.004), 100),
        parcel(2, box(-71.011, 42.002, -71.007, 42.004), 40, station="N"),
        parcel(3, box(-71.018, 41.998, -71.016, 42.002), 10),  # half outside every tract
    ]
    parcels, _ = fx.dedupe_parcels(fx.parcel_frame(feats))
    d = districts_frame([[1, "Z1", 1, "Town", 150, json.dumps(gj(box(-71.02, 42, -71, 42.01)))]])
    tract, district, summary = fx.capacity_tables(parcels, d, TRACTS, RULE, read_on="2026-11-25")
    counted = float(district["capacity_counted"].sum())
    assert counted == 150
    assert tract["capacity_units"].sum() + district["unmatched_units"].sum() == pytest.approx(150)
    assert summary["unmatched_units"] == pytest.approx(5, abs=0.3)
    west = tract.set_index("geoid").loc["25001000100"]
    assert west["capacity_units"] == pytest.approx(100 + 0.5 * 10 + 0.25 * 40, abs=1.0)
    # station area: parcels 1 and 3 only
    assert tract["station_area_units"].sum() == pytest.approx(105, abs=0.3)


def test_district_total_never_added_to_its_parcels():
    geom = json.dumps(gj(box(-71.019, 42.001, -71.011, 42.009)))
    feats = [parcel(1, box(-71.018, 42.002, -71.016, 42.004), 60),
             parcel(2, box(-71.015, 42.002, -71.013, 42.004), 40)]  # fmt: skip
    parcels, _ = fx.dedupe_parcels(fx.parcel_frame(feats))
    d = districts_frame([
        [1, "Z1", 1, "Town", 100, geom],      # has parcels: counts them, not its 100
        [2, "Z2", 1, "Town", 70, geom],       # no parcels: counts its own 70, by outline
        [3, "Z1", 1, "Town", 100, geom],      # same code and town as 1: counts nothing
    ])  # fmt: skip
    tract, district, _ = fx.capacity_tables(parcels, d, TRACTS, RULE, read_on="x")
    by = district.set_index("district_id")
    assert list(by["basis"]) == ["parcels", "district_outline", "duplicate_code"]
    assert list(by["capacity_counted"]) == [100, 70, 0]
    assert by.loc[1, "difference"] == 0
    assert tract["capacity_units"].sum() == pytest.approx(170)
    assert tract["capacity_parcel_units"].sum() == pytest.approx(100)
    assert tract["capacity_district_units"].sum() == pytest.approx(70)


def test_parcels_without_a_district_are_counted_once():
    feats = [parcel(1, box(-71.018, 42.002, -71.016, 42.004), 25, zone="ORPHAN")]
    parcels, _ = fx.dedupe_parcels(fx.parcel_frame(feats))
    d = districts_frame([[1, "Z1", 1, "Town", 0, json.dumps(gj(box(-71.02, 42, -71, 42.01)))]])
    tract, district, _ = fx.capacity_tables(parcels, d, TRACTS, RULE, read_on="x")
    row = district[district["basis"] == "parcels_no_district"].iloc[0]
    assert row["capacity_counted"] == 25 and pd.isna(row["district_id"])
    assert tract["capacity_units"].sum() == pytest.approx(25)


def test_duplicate_parcel_keeps_larger_capacity():
    ring = box(-71.018, 42.002, -71.016, 42.004)
    frame = fx.parcel_frame([parcel(1, ring, 10, loc="A"), parcel(2, ring, 30, loc="A"),
                             parcel(3, ring, 5, loc="")])  # fmt: skip
    kept, dropped = fx.dedupe_parcels(frame)
    assert list(kept["parcel_id"]) == [2, 3]
    assert list(dropped["capacity_units"]) == [10]


def test_missing_capacity_stays_missing():
    frame = fx.parcel_frame([{"attributes": {"OBJECTID": 1, "TRNSTST": " "}, "rings": []}])
    assert pd.isna(frame.loc[0, "capacity_units"]) and pd.isna(frame.loc[0, "station_area"])


# --------------------------------------------------------------------------- sewer


def ngon(cx, cy, r_deg, n):
    k = math.cos(math.radians(cy))
    pts = [[cx + r_deg * math.cos(2 * math.pi * i / n) / k,
            cy + r_deg * math.sin(2 * math.pi * i / n)] for i in range(n)]  # fmt: skip
    return [*pts, pts[0]]


def test_circle_ratio_separates_placeholder_circles():
    circle, _ = fx.circle_ratio([np.asarray(ngon(-71.0, 42.0, 0.001, 32))])
    square, _ = fx.circle_ratio([np.asarray(box(-71.0, 42.0, -70.99, 42.0074))])
    assert circle > 0.99
    assert square == pytest.approx(math.pi / 4, abs=0.02)


def test_sewer_table_keeps_its_own_evidence_class_and_flags_circles():
    blocks = pd.DataFrame(
        {
            "tract": ["25001000100"] * 3 + ["25001000200"] * 2,
            "housing_units": [10, 30, 60, 50, 50],
            "lon": [-71.019, -71.015, -71.011, -71.005, -71.0021],
            "lat": [42.005] * 4 + [42.009],
        }
    )
    poly = {"attributes": {"SERVCECONF": "Confirmed", "SECTOR": "Municipal/Regional/District"},
            "rings": [box(-71.02, 42.0, -71.014, 42.01)]}  # fmt: skip
    unconf = {"attributes": {"SERVCECONF": "Unconfirmed", "SECTOR": "School"},
              "rings": [box(-71.012, 42.0, -71.010, 42.01)]}  # fmt: skip
    circle = {"attributes": {"SERVCECONF": "Unconfirmed", "SECTOR": "School"},
              "rings": [ngon(-71.0021, 42.009, 0.0005, 33)]}  # fmt: skip
    table, summary = fx.sewer_tract_table(
        blocks, {"potw": {"features": [poly]}, "non_potw": {"features": [unconf, circle]}},
        circle_min_ratio=0.95, circle_max_acres=200, edition="E", read_on="2026-11-25",
    )  # fmt: skip
    t = table.set_index("geoid")
    assert t.loc["25001000100", "est_share"] == 1.0
    assert t.loc["25001000100", "est_confirmed_share"] == 0.4
    assert t.loc["25001000100", "est_potw_share"] == 0.4
    assert t.loc["25001000100", "geometry_quality"] == "polygon"
    assert t.loc["25001000200", "est_share"] == 0.0
    assert t.loc["25001000200", "circle_only_share"] == 0.5
    assert t.loc["25001000200", "geometry_quality"] == "approximate_circle"
    assert set(table["evidence_class"]) == {"estimated service"}
    assert summary["placeholder_circles"] == 1


# --------------------------------------------------------------------------- fetch


class FakeService:
    """A map service: layer info, a count, ids and features by id chunk."""

    def __init__(self, features, *, count=None, drop=False):
        self.features, self.count, self.drop = features, count, drop

    def get_bytes(self, url, params=None):
        p = dict(params or {})
        if p.get("returnCountOnly"):
            n = len(self.features) if self.count is None else self.count
            return json.dumps({"count": n}).encode()
        if not url.endswith("/query"):
            fields = [{"name": n} for n in fx.CAPACITY_FIELDS]
            info = {"name": "Parcels", "fields": fields,
                    "editingInfo": {"lastEditDate": 1_793_000_000_000}}  # fmt: skip
            return json.dumps(info).encode()
        if p.get("returnIdsOnly"):
            return json.dumps({"objectIds": list(range(1, len(self.features) + 1))}).encode()
        ids = [int(i) for i in p["objectIds"].split(",")]
        if self.drop:
            ids = ids[:-1]
        feats = [{"attributes": self.features[i - 1]["attributes"],
                  "geometry": {"rings": self.features[i - 1]["rings"]}} for i in ids]  # fmt: skip
        return json.dumps({"features": feats}).encode()


def _settings():
    fields = [f for f in fx.CAPACITY_FIELDS if f != "OBJECTID"]
    return {"capacity": {"parcels": {"source": "massgis_mbta3a_parcels", "layer": 1,
                                     "file": "p.json.gz", "fields": fields,
                                     "simplify_degrees": 0.00001}}}  # fmt: skip


def test_fetch_paginates_by_id_records_stamp_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(infra_src, "ID_CHUNK", 2)
    feats = [parcel(i, box(-71.018, 42.002, -71.016, 42.004), i) for i in range(1, 6)]
    doc, path = src.fetch_capacity_layer(
        "parcels", tmp_path, _settings(), client=FakeService(feats), manifest=None
    )
    assert doc["received"] == doc["service_count"] == 5
    assert doc["last_edit"] and doc["read_on"]
    assert [f["attributes"]["OBJECTID"] for f in doc["features"]] == [1, 2, 3, 4, 5]
    again, _ = src.fetch_capacity_layer("parcels", tmp_path, _settings(), client=None,
                                        manifest=None)  # fmt: skip
    assert again == doc and path.is_file()


def test_fetch_fails_when_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(infra_src, "ID_CHUNK", 2)
    feats = [parcel(i, box(-71.018, 42.002, -71.016, 42.004), i) for i in range(1, 6)]
    with pytest.raises(FetchError):
        src.fetch_capacity_layer("parcels", tmp_path, _settings(),
                                 client=FakeService(feats, drop=True), manifest=None)  # fmt: skip
    with pytest.raises(FetchError, match="counts 6"):
        src.fetch_capacity_layer("parcels", tmp_path, _settings(),
                                 client=FakeService(feats, count=6), manifest=None)  # fmt: skip
    assert not (tmp_path / "local" / "ma" / "p.json.gz").exists()


# --------------------------------------------------------------------------- dictionary


def test_every_column_is_described_and_registered():
    for table, spec in fx.TABLES.items():
        assert table in local.TABLES and spec["tier"] == "2"
        for col in fx._COLUMNS[table]:
            unit, text = local.describe(table, col)
            assert unit and text


def test_read_on_of_a_cached_json_layer(tmp_path):
    import gzip

    from visionhousing.data.pack import _read_on

    path = tmp_path / "layer.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({"read_on": "2026-11-25", "features": [{"a": ",".join("x" * 50)}]}, fh)
    assert _read_on(path) == "2026-11-25"
