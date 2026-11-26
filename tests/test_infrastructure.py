"""Water and sewer readiness: points in service areas, statuses, Act 537 matching, tables, fetchers.

Tiny synthetic blocks and polygons, and a fake HTTP client; no network.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.http import FetchError
from visionhousing.data.sources import infrastructure as src
from visionhousing.features import infrastructure as fx


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def feature(rings, **attrs):
    return {"attributes": attrs, "rings": rings}


# --------------------------------------------------------------------------- points in polygons


def test_feature_index_even_odd_and_first_match():
    feats = [
        feature([square(0, 0, 10, 10), square(4, 4, 6, 6)]),  # a square with a hole
        feature([square(3, 3, 7, 7)]),  # covers the hole
    ]
    lon = np.array([1.0, 5.0, 20.0, 3.5])
    lat = np.array([1.0, 5.0, 20.0, 3.5])
    assert list(fx.feature_index(lon, lat, feats)) == [0, 1, -1, 0]


# --------------------------------------------------------------------------- block answers

BLOCKS = pd.DataFrame(
    {
        "geoid": [f"b{i}" for i in range(7)],
        "tract": ["42001000100"] * 3 + ["42003000100"] * 2 + ["42101000100", "42005000100"],
        "housing_units": [10, 20, 30, 5, 5, 100, 0],
        "lon": [1.0, 2.0, 50.0, 1.0, 50.0, 70.0, 1.0],
        "lat": [1.0, 2.0, 50.0, 1.0, 50.0, 70.0, 1.0],
    }
)
SEWER_MAP = {"features": [feature([square(0, 0, 10, 10)], Name="Borough Authority")]}
WATER_MAP = {"features": [feature([square(0, 0, 60, 60)], NAME="Water Co", PWS_ID="7010019")]}
EPA = {
    "features": [feature([square(0, 0, 5, 5)], CWNS_ID="42000001001", FACILITY_NAME="Borough STP")]
}


def answers(constraints=()):
    sewer = [
        (
            {
                "id": "pa_adams_sewer_service_areas",
                "name_field": "Name",
                "counties": ["42001"],
                "extent": "county",
            },
            SEWER_MAP,
        ),
        (
            {
                "id": "vt_accd_wastewater_service_areas",
                "name_field": "Name",
                "counties": ["42003"],
                "extent": "inside_only",
            },
            SEWER_MAP,
        ),
    ]
    records = [{"id": "phl_pwd_wastewater", "counties": ["42101"], "name": "City Water"}]
    water = [
        (
            {
                "id": "pa_dep_pws_service_areas",
                "name_field": "NAME",
                "fields": ["PWS_ID"],
                "extent": "state",
            },
            WATER_MAP,
        )
    ]
    return fx.block_answers(BLOCKS, sewer, records, list(constraints), water, EPA)


def test_block_answers():
    a = answers().set_index("geoid")
    assert "b6" not in a.index  # no homes
    # County inventory: inside is verified, outside is known-outside.
    assert a.loc["b0", "sewer_in"] and a.loc["b0", "sewer_known"]
    assert not a.loc["b2", "sewer_in"] and a.loc["b2", "sewer_known"]
    assert a.loc["b2", "sewer_source"] == "pa_adams_sewer_service_areas"
    # Inside-only layer: outside stays unknown.
    assert a.loc["b3", "sewer_in"] and not a.loc["b4", "sewer_known"]
    # Whole-jurisdiction statement.
    assert a.loc["b5", "sewer_in"] and a.loc["b5", "sewer_name"] == "City Water"
    # Water: statewide map, so outside counts as known.
    assert a.loc["b2", "water_in"] and not a.loc["b5", "water_in"] and a.loc["b5", "water_known"]
    assert a.loc["b0", "water_id"] == "7010019"
    # EPA context.
    assert a.loc["b0", "epa_in"] and not a.loc["b2", "epa_in"]


def test_status_and_ties():
    assert fx.status_of(6, 5, 0, 0) == "verified"  # 6 of 11 homes: a majority
    # a plurality is not enough: 4 of 10 homes verified, the rest split
    assert fx.status_of(4, 3, 0, 3) == "unknown"
    assert fx.status_of(4, 3, 0, 2) == "outside"
    assert fx.status_of(5, 0, 0, 5) == "unknown"  # exactly half is not more than half
    assert fx.status_of(6, 4, 0, 0, min_verified_share=0.7) == "outside"
    assert fx.load_rules()["status"]["verified_min_share"] == fx.VERIFIED_MIN_SHARE
    assert fx.status_of(5, 5, 0, 0) == "outside"  # ties go to the more cautious answer
    assert fx.status_of(5, 0, 0, 5) == "unknown"
    assert fx.status_of(1, 5, 5, 0) == "constraint"
    assert fx.status_of(0, 0, 0, 0) == "unknown"
    assert list(fx.STATUS_ORDER) == fx.load_rules()["status"]["order"]


def test_tract_table():
    tracts = ["42001000100", "42003000100", "42005000100", "42101000100"]
    t = fx.tract_table(answers(), tracts).set_index("geoid")
    assert t.loc["42001000100", "sewer_status"] == "outside"  # 30 v 30: the cautious answer
    assert t.loc["42001000100", "sewer_share"] == 0.5
    assert t.loc["42001000100", "sewer_known"] == 1.0
    assert t.loc["42003000100", "sewer_status"] == "unknown"  # 5 v 5
    assert t.loc["42003000100", "sewer_share"] == 0.5
    assert t.loc["42003000100", "sewer_known"] == 0.5
    assert t.loc["42005000100", "sewer_status"] == ""  # no homes
    assert pd.isna(t.loc["42005000100", "sewer_share"])
    assert t.loc["42101000100", "sewer_status"] == "verified"
    assert t.loc["42101000100", "sewer_source"] == "phl_pwd_wastewater"
    assert t.loc["42001000100", "water_share"] == 1.0
    assert t.loc["42001000100", "epa_share"] == 0.5
    assert list(t.reset_index().columns) == list(fx.TRACT_COLUMNS)


def test_documented_constraint_wins_its_homes():
    rec = {"id": "x", "tracts": ["42001000100"]}
    t = fx.tract_table(answers([rec]), ["42001000100"]).set_index("geoid")
    assert t.loc["42001000100", "sewer_status"] == "constraint"
    assert t.loc["42001000100", "constraint_share"] == 1.0


def test_county_table_and_contacts():
    a = answers()
    tracts = fx.tract_table(a, ["42001000100", "42003000100", "42101000100"])
    cwns = pd.DataFrame(
        {
            "I_NEED": [1.0],
            "II_NEED": [2.0],
            "IV_A_NEED": [3.0],
            "IV_B_NEED": [4.0],
            "ALL_TOTAL": [10.0],
        },
        index=["42001"],
    )
    shares = pd.DataFrame(
        {"lt10": [0.5], "10to20": [0.5], "20plus": [0.0], "prereg": [0.0], "unmatched": [0.0]},
        index=["42001"],
    )
    endpoints = pd.DataFrame(
        {
            "county": ["42001", "42001"],
            "CWNS_ID": ["42000001001", "x"],
            "echo_url": ["https://echo.epa.gov/x", ""],
        }
    )
    county = fx.county_table(
        a,
        tracts,
        ["42001", "42101"],
        {"pa_adams_sewer_service_areas": "2024-09"},
        shares,
        cwns,
        endpoints,
    ).set_index("county")
    row = county.loc["42001"]
    assert row["homes"] == 60 and row["homes_verified"] == 30 and row["homes_outside"] == 30
    assert row["tracts_outside"] == 1 and row["sewer_as_of"] == "2024-09"
    assert row["cwns_treatment"] == 3 and row["cwns_new_sewers"] == 7 and row["cwns_total"] == 10
    assert row["act537_lt10"] == 0.5 and row["epa_plants"] == 2
    assert county.loc["42101", "homes_verified"] == 100
    contacts = fx.contacts_table(a, endpoints, per_county=3)
    water = contacts[(contacts["kind"] == "water") & (contacts["county"] == "42001")].iloc[0]
    assert water["contact_url"].endswith("P200_PWSID=PA7010019")
    epa = contacts[contacts["kind"] == "epa_sewershed"].iloc[0]
    assert epa["contact_url"] == "https://echo.epa.gov/x" and epa["name"] == "Borough STP"
    sewer = contacts[(contacts["kind"] == "sewer") & (contacts["county"] == "42001")].iloc[0]
    assert sewer["name"] == "Borough Authority" and sewer["homes"] == 30


def test_every_column_is_described():
    for table, cols in (
        ("infra_tract", fx.TRACT_COLUMNS),
        ("infra_county", fx.COUNTY_COLUMNS),
        ("infra_contacts", fx.CONTACT_COLUMNS),
    ):
        for name in cols:
            unit, text = fx.describe(table, name)
            assert unit and text


# --------------------------------------------------------------------------- Act 537

TABLE_TEXT = """NAME COUNTY DATE STATUS
Bedminster Township Bucks 11/20/2003 Plan between 20 and 53 years
Bristol Borough Bucks 1/1/1971 Pre-Regulatory County Plan
Bristol Township Bucks 10/8/1999 Plan between 20 and 53 years
Falls Township Bucks 10/18/2018 Plan less than 10 years
Page 1 of 5
Philadelphia City Philadelphia 2/27/2009 Plan between 10 and 20 years"""


def test_parse_act537_text():
    rows = src.parse_act537_text(TABLE_TEXT)
    assert len(rows) == 5
    first = rows.iloc[0]
    assert (first["name"], first["county_name"], first["approved"], first["plan_class"]) == (
        "Bedminster Township",
        "Bucks",
        "2003-11-20",
        "20plus",
    )
    assert rows.iloc[1]["plan_class"] == "prereg"


def test_act537_matching_and_tables():
    plans = src.parse_act537_text(TABLE_TEXT)
    munis = pd.DataFrame(
        {
            "county": ["42017", "42017", "42017", "42017", "42101"],
            "cousub": ["05000", "08752", "08760", "25112", "60000"],
            "name": [
                "Bedminster township",
                "Bristol borough",
                "Bristol township",
                "Falls township",
                "Philadelphia city",
            ],
        }
    )
    counties = pd.DataFrame(
        {"geoid": ["42017", "42101"], "name": ["Bucks County", "Philadelphia County"]}
    )
    by = fx.act537_by_cousub(plans, munis, counties).set_index("cousub")
    assert by.loc["08752", "plan_class"] == "prereg"  # borough, not the township
    assert by.loc["08760", "plan_class"] == "20plus"
    assert by.loc["60000", "plan_class"] == "10to20"
    pieces = pd.DataFrame(
        {
            "tract": ["42017100100", "42017100100", "42101000100"],
            "county": ["42017", "42017", "42101"],
            "cousub": ["25112", "08752", "60000"],
            "housing_units_2020": [70, 30, 50],
        }
    )
    main, shares = fx.act537_tables(by.reset_index(), pieces)
    assert main["42017100100"] == "lt10"
    assert shares.loc["42017", "lt10"] == 0.7 and shares.loc["42017", "prereg"] == 0.3


# --------------------------------------------------------------------------- fetchers (fake server)


class FakeClient:
    def __init__(self, features, *, drop=False):
        self.features, self.drop = features, drop

    def get_bytes(self, url, params=None):
        p = dict(params or {})
        if p.get("returnIdsOnly"):
            return json.dumps({"objectIds": list(range(1, len(self.features) + 1))}).encode()
        if "objectIds" not in p:  # attribute-only query by the filter
            rows = [{"attributes": f["attributes"]} for f in self.features]
            return json.dumps({"features": rows[: len(rows) - int(self.drop)]}).encode()
        ids = [int(i) for i in p["objectIds"].split(",")]
        if self.drop:
            ids = ids[:-1]
        feats = [
            {
                "attributes": self.features[i - 1]["attributes"],
                "geometry": {"rings": self.features[i - 1]["rings"]},
            }
            for i in ids
        ]
        return json.dumps({"features": feats}).encode()


def test_fetch_layer_reads_by_id_chunks_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(src, "ID_CHUNK", 2)
    feats = [feature([square(0, 0, 1, 1)], Name=f"n{i}") for i in range(5)]
    layer = src.Layer.from_rules(
        {
            "id": "pa_adams_sewer_service_areas",
            "url": "https://x.test/0",
            "name_field": "Name",
            "as_of": "2024-09",
        },
        "sewer",
    )
    assert layer.fields == ("Name",)
    data, path = src.fetch_layer(layer, tmp_path, client=FakeClient(feats), manifest=None)
    assert len(data["features"]) == 5 and path.exists()
    again, _ = src.fetch_layer(layer, tmp_path, client=None, manifest=None)  # cached
    assert again["features"] == data["features"]
    with pytest.raises(FetchError, match="got"):
        src.query_features(FakeClient(feats, drop=True), "https://x.test/0", "1=1", ("Name",))


def test_shipped_rules_name_registered_sources():
    from visionhousing.data.registry import load_registry

    rules = fx.load_rules()
    registry = load_registry()
    ids = [
        e["id"]
        for group in ("sewer_layers", "water_layers", "service_records")
        for entries in rules[group].values()
        for e in entries or []
    ]
    ids += [e["id"] for e in rules["context_layers"]]
    assert ids and all(i in registry for i in ids)
    for table in fx.TABLES.values():
        assert all(s in registry for s in table["sources"])  # type: ignore[union-attr]


def test_attribute_tables_use_one_filtered_query():
    feats = [feature([], COUNTY_FIPS=f"4200{i}") for i in range(3)]
    rows = src.query_features(
        FakeClient(feats), "https://x.test/0", "1=1", ("COUNTY_FIPS",), geometry=False
    )
    assert [r["attributes"]["COUNTY_FIPS"] for r in rows] == ["42000", "42001", "42002"]
    with pytest.raises(FetchError, match="2 of 3"):
        src.query_features(
            FakeClient(feats, drop=True), "https://x.test/0", "1=1", ("C",), geometry=False
        )
