"""Parcel and owner aggregates for Philadelphia and Allegheny (small synthetic rows, no network)."""

from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.sources import alle_assess, opendata, phl_opa
from visionhousing.features import owners as ow
from visionhousing.features import parcels as pc

RULES = pc.load_rules()
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
SQUARE2 = {"type": "Polygon", "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]}
SHAPES = [("42101000100", SQUARE), ("42101000200", SQUARE2)]


def _phl(n: int = 12) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "lat": 0.5,
                "lon": 0.5,
                "category": "1",
                "condo": 0,
                "market_value": 100000 + i * 1000,
                "year_built": 1925 if i < 6 else 2005,
                "lot_sqft": 1000 + i,
                "sale_date": "2026-06-01" if i < 3 else "2024-06-01",
                "sale_price": 200000 if i < 11 else 1,
                "homestead": 1 if i < 8 else 0,
                "entity": 1 if i < 2 else 0,
                "out_of_state": 1 if i == 0 else 0,
            }
        )
    rows.append({**rows[0], "category": "1", "condo": 1, "lon": 1.5})
    rows.append({**rows[0], "category": "6", "lon": 1.5})
    rows.append({**rows[0], "category": "14", "lat": 5.0, "lon": 5.0})
    return pd.DataFrame(rows)


def test_word_regex_and_quoting():
    assert opendata.word_regex(["llc", "L L C"]) == r"\m(LLC|L L C)\M"
    with pytest.raises(ValueError):
        opendata.word_regex(["LLC)|(.*"])
    assert opendata.sql_text("O'Brien") == "'O''Brien'"
    assert opendata.sql_list(["a", "b'c"]) == "('a', 'b''c')"


def test_queries_never_select_names():
    q = phl_opa.opa_page_query(0, 10, ["LLC", "TRUST"], "PA", "CONDO")
    select = q.split(" FROM ")[0]
    assert "owner_1" in q and " AS entity" in select
    # Every selected column is named, and the names are exactly the slim extract's columns:
    # owner and mailing fields are only tested inside an expression, never returned.
    aliases = re.findall(r"\bAS (\w+)", select)
    unnamed = {"market_value", "sale_price"}
    assert set(aliases) | unnamed == set(phl_opa.PARCEL_COLUMNS)
    d = phl_opa.deed_flow_query(["LLC"], ["DEED"], 10000, 1, 2015, "CONDO")
    assert "GROUP BY 1" in d and "grantees ~*" in d
    sf = alle_assess.single_family_query(
        ["SINGLE FAMILY"], RULES["year_built_groups"], ["CORPORATION"], "PA", True
    )
    assert "GROUP BY 1" in sf and "OWNERDESC" in sf and "sf_built_pre_1940" in sf


def test_months_back():
    assert alle_assess.months_back(date(2026, 11, 22), 12) == date(2025, 11, 22)
    assert alle_assess.months_back(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert alle_assess.months_back(date(2026, 1, 15), 36) == date(2023, 1, 15)


def test_land_use_classes():
    cls = pc.philadelphia_class(
        pd.Series(["1", "1", "2", "14", "99"]), pd.Series([0, 1, 0, 0, 0]), RULES
    )
    assert list(cls) == ["single_family", "condo", "small_multi", "apartments", "other"]
    a = pc.allegheny_class(
        pd.Series(["RESIDENTIAL", "COMMERCIAL", "GOVERNMENT", "RESIDENTIAL"]),
        pd.Series(["TOWNHOUSE", "APART: 5-19 UNITS", "PUBLIC PARK", "VACANT LAND"]),
        RULES,
    )
    assert list(a) == ["single_family", "apartments", "government", "vacant"]


def test_philadelphia_parcels():
    p = _phl()
    tracts = pc.place_in_tracts(p["lat"], p["lon"], SHAPES)
    assert list(tracts[-3:]) == ["42101000200", "42101000200", ""]
    out = pc.philadelphia_parcels(p, tracts, "2026-11-22", RULES)
    assert list(out.columns) == pc.parcel_columns(RULES)
    t1 = out.set_index("geoid").loc["42101000100"]
    assert t1["parcels"] == 12 and t1["sf_parcels"] == 12
    assert t1["sf_median_value"] == pytest.approx(105500)
    assert t1["sf_built_pre_1940"] == 6 and t1["sf_built_y2000_plus"] == 6
    assert t1["sales_short"] == 3 and t1["sales_long"] == 11  # one sale below the price floor
    assert pd.isna(t1["median_sale_short"])  # 3 sales: under the median minimum
    t2 = out.set_index("geoid").loc["42101000200"]
    assert t2["lu_condo"] == 1 and t2["lu_vacant"] == 1 and pd.isna(t2["sf_median_value"])
    county = out.set_index("geoid").loc["42101"]
    assert county["parcels"] == 15 and county["unplaced"] == 1 and county["lu_apartments"] == 1


def test_philadelphia_owners():
    p = _phl()
    tracts = pc.place_in_tracts(p["lat"], p["lon"], SHAPES)
    out = ow.philadelphia_owners(p, tracts, "2026-11-22", RULES).set_index("geoid")
    assert sorted(out.reset_index().columns) == sorted(ow.AREA_COLUMNS)
    t1 = out.loc["42101000100"]
    assert (t1["sf_parcels"], t1["sf_entity"], t1["sf_non_homestead"], t1["sf_out_of_state"]) == (
        12,
        2,
        4,
        1,
    )
    assert (
        out.loc["42101", "sf_parcels"] == 12
    )  # condo, vacant and apartment rows are not single-family


def _alle_extracts():
    uses = pd.DataFrame(
        {
            "tract": ["42003010100", "42003010100", "42003010200", None, None, None],
            "class": [
                "RESIDENTIAL",
                "COMMERCIAL",
                "RESIDENTIAL",
                "RESIDENTIAL",
                "COMMERCIAL",
                "OTHER",
            ],
            "use": ["SINGLE FAMILY", "OFFICE", "TWO FAMILY", "SINGLE FAMILY", "OFFICE", "X"],
            "parcels": ["20", "5", "3", "21", "5", "4"],
        }
    )
    sf = pd.DataFrame(
        {
            "tract": ["42003010100", None],
            "sf_parcels": ["20", "21"],
            "sf_median_value": ["90000", "91000"],
            "sf_median_lot_sqft": ["5000", "5100"],
            **{f"sf_built_{g}": ["5", "5"] for g in RULES["year_built_groups"]},
            "sf_built_unknown": ["0", "1"],
            "sf_entity": ["2", "2"],
            "sf_non_homestead": ["6", "7"],
            "sf_out_of_state": ["1", "1"],
            "read_on": ["2026-11-22", "2026-11-22"],
        }
    )
    sales = pd.DataFrame(
        {
            "tract": ["42003010100", None],
            "sales_short": ["2", "2"],
            "sales_long": ["12", "13"],
            "median_sale_short": ["150000", "150000"],
            "median_sale_long": ["140000", "141000"],
        }
    )
    return uses, sf, sales


def test_allegheny_parcels_and_owners():
    uses, sf, sales = _alle_extracts()
    out = pc.allegheny_parcels(uses, sf, sales, RULES).set_index("geoid")
    t = out.loc["42003010100"]
    assert t["parcels"] == 25 and t["lu_single_family"] == 20 and t["lu_commercial"] == 5
    assert (
        t["sf_median_value"] == 90000 and t["sales_long"] == 12 and t["median_sale_long"] == 140000
    )
    assert pd.isna(t["median_sale_short"])  # two sales: blanked
    assert out.loc["42003010200", "sf_parcels"] == 0
    county = out.loc["42003"]
    assert county["parcels"] == 30 and county["unplaced"] == 2 and county["lu_other"] == 4
    owners = ow.allegheny_owners(sf).set_index("geoid")
    assert owners.loc["42003", "sf_entity"] == 2 and owners.loc["42003010100", "level"] == "tract"


def test_deeds_year_marks_partial_year():
    flow = pd.DataFrame(
        {
            "year": ["2025", "2026"],
            "deeds": ["100", "40"],
            "entity_deeds": ["30", "10"],
            "read_on": ["2026-11-22"] * 2,
        }
    )
    out = ow.deeds_year(flow)
    assert list(out["partial_year"]) == [0, 1] and list(out["entity_deeds"]) == [30, 10]


def test_benchmarks_are_cited_and_registered():
    from visionhousing.data.registry import load_registry

    items = ow.benchmarks()
    assert {b["id"] for b in items} == {
        "philly_fed_2025_large_corporate",
        "pew_2024_investor_purchases",
    }
    registry = load_registry()
    for b in items:
        assert b["registry_id"] in registry
        assert 0 < b["value"] < 1 and b["source"]["url"].startswith("https://")
    assert np.isclose(next(b["value"] for b in items if "philly" in b["id"]), 0.088)


def test_owner_rules():
    rules = ow.load_rules()
    assert "LLC" in rules["entity_words"] and "TRUST" in rules["entity_words"]
    opendata.word_regex(rules["entity_words"])  # every word is safe in a pattern


def test_every_local_column_is_described():
    from visionhousing.data import local
    from visionhousing.features import pipeline_local, zoning_status

    columns = {
        "parcels_area": pc.parcel_columns(RULES),
        "owners_area": ow.AREA_COLUMNS,
        "owners_deeds_year": ow.DEED_COLUMNS,
        "pipeline_tract_year": pipeline_local.PIPELINE_COLUMNS,
        "lihtc_awards": pipeline_local.AWARD_COLUMNS,
        "municipal_planning": zoning_status.MUNICIPAL_COLUMNS,
        "tract_municipalities": zoning_status.TRACT_COLUMNS,
    }
    assert set(columns) == set(local.TABLES)
    for table, names in columns.items():
        for name in names:
            unit, text = local.describe(table, name)
            assert unit and text, (table, name)
