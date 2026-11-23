"""The zoning, plans, parcels, owners and pipeline_local sections on a tiny hand-made pack."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from visionhousing.profile.context import load_profile_context
from visionhousing.profile.registry import build_section

PHL, PHL_T = "42101", "42101000100"
ALLE, ALLE_T = "42003", "42003010100"
RURAL, RURAL_T = "42027", "42027010100"


def _write(folder: Path, name: str, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(folder / f"{name}.csv", index=False)


@pytest.fixture
def ctx(tmp_path):
    f = tmp_path / "PA"
    f.mkdir()
    _write(
        f,
        "counties",
        [
            {"geoid": PHL, "name": "Philadelphia County"},
            {"geoid": ALLE, "name": "Allegheny County"},
            {"geoid": RURAL, "name": "Centre County"},
        ],
    )
    _write(
        f,
        "tracts",
        [
            {"geoid": PHL_T, "county": PHL},
            {"geoid": ALLE_T, "county": ALLE},
            {"geoid": RURAL_T, "county": RURAL},
        ],
    )
    base = {
        "dced_id": "x",
        "subdivision_ordinance": 1,
        "planning_commission": 1,
        "zoning_hearing_board": 1,
        "ucc_code": 1,
    }
    _write(
        f,
        "municipal_planning",
        [
            {
                **base,
                "county": PHL,
                "cousub": "60000",
                "name": "Philadelphia city",
                "zoning_regime": "own",
                "comprehensive_plan": 1,
                "multi_municipal_plan": 0,
                "population": 1500000,
                "housing_units_2020": 700000,
            },
            {
                **base,
                "county": ALLE,
                "cousub": "61000",
                "name": "Pittsburgh city",
                "zoning_regime": "own",
                "comprehensive_plan": 1,
                "multi_municipal_plan": 0,
                "population": 300000,
                "housing_units_2020": 160000,
            },
            {
                **base,
                "county": RURAL,
                "cousub": "00100",
                "name": "A township",
                "zoning_regime": "none",
                "comprehensive_plan": 0,
                "multi_municipal_plan": 0,
                "population": 3000,
                "housing_units_2020": 1200,
            },
            {
                **base,
                "county": RURAL,
                "cousub": "00200",
                "name": "B borough",
                "zoning_regime": "own",
                "comprehensive_plan": 1,
                "multi_municipal_plan": 1,
                "population": 1000,
                "housing_units_2020": 500,
            },
        ],
    )
    _write(
        f,
        "tract_municipalities",
        [
            {"tract": PHL_T, "county": PHL, "cousub": "60000", "housing_units_2020": 900},
            {"tract": ALLE_T, "county": ALLE, "cousub": "61000", "housing_units_2020": 800},
            {"tract": RURAL_T, "county": RURAL, "cousub": "00100", "housing_units_2020": 300},
            {"tract": RURAL_T, "county": RURAL, "cousub": "00200", "housing_units_2020": 100},
        ],
    )
    groups = {
        "sf_built_pre_1940": 30,
        "sf_built_y1940_1979": 40,
        "sf_built_y1980_1999": 20,
        "sf_built_y2000_plus": 10,
        "sf_built_unknown": 0,
    }
    parcel = {
        "parcels": 200,
        "lu_single_family": 100,
        "lu_commercial": 100,
        "sf_parcels": 100,
        "sf_median_value": 200000,
        "sf_median_lot_sqft": 1500,
        **groups,
        "sales_short": 20,
        "sales_long": 50,
        "median_sale_short": 250000,
        "median_sale_long": 240000,
        "as_of": "2026-11-22",
        "value_basis": "market value",
    }
    _write(
        f,
        "parcels_area",
        [
            {**parcel, "level": "tract", "geoid": PHL_T, "county": PHL},
            {
                **parcel,
                "level": "county",
                "geoid": PHL,
                "county": PHL,
                "parcels": 400,
                "lu_single_family": 300,
                "lu_commercial": 100,
                "sf_parcels": 300,
                "unplaced": 0,
            },
            {**parcel, "level": "county", "geoid": ALLE, "county": ALLE, "unplaced": 3},
        ],
    )
    owner = {
        "as_of": "2026-11-22",
        "entity_basis": "name test",
        "sf_parcels": 100,
        "sf_entity": 10,
        "sf_non_homestead": 40,
        "sf_out_of_state": 5,
    }
    _write(
        f,
        "owners_area",
        [
            {**owner, "level": "tract", "geoid": PHL_T, "county": PHL},
            {
                **owner,
                "level": "county",
                "geoid": PHL,
                "county": PHL,
                "sf_parcels": 300,
                "sf_entity": 15,
            },
        ],
    )
    _write(
        f,
        "owners_deeds_year",
        [
            {"county": PHL, "year": 2025, "deeds": 100, "entity_deeds": 25, "partial_year": 0},
            {"county": PHL, "year": 2026, "deeds": 50, "entity_deeds": 10, "partial_year": 1},
        ],
    )
    _write(
        f,
        "pipeline_tract_year",
        [
            {
                "tract": PHL_T,
                "county": PHL,
                "year": y,
                "source": "phl_li_permits",
                "permits": 2,
                "units": 6,
            }
            for y in range(2020, 2027)
        ]
        + [
            {
                "tract": ALLE_T,
                "county": ALLE,
                "year": 2024,
                "source": "pgh_pli_permits",
                "permits": 3,
                "units": None,
            }
        ],
    )
    _write(
        f,
        "lihtc_awards",
        [
            {
                "year": 2024,
                "development": "Dev A",
                "city": "Philadelphia",
                "county": PHL,
                "tract": PHL_T,
                "units": 40,
                "lihtc_award": 1000000,
            },
            {
                "year": 2022,
                "development": "Dev B",
                "city": "State College",
                "county": RURAL,
                "tract": "",
                "units": 30,
                "lihtc_award": 900000,
            },
        ],
    )
    return load_profile_context("PA", folder=f)


def test_zoning_county_and_tract(ctx):
    s = build_section("zoning", ctx, RURAL)
    assert s.status == "ok"
    assert s.fact("zoning.municipalities").value == 2
    assert s.fact("zoning.people_none").value == pytest.approx(0.75)
    assert s.fact("zoning.people_own").comparison.against == "state"
    t = build_section("zoning", ctx, RURAL_T)
    assert t.fact("zoning.homes_none").value == pytest.approx(0.75)
    assert t.fact("zoning.municipalities").value == "A township; B borough"
    assert "single municipality" in build_section("zoning", ctx, PHL).summary


def test_plans(ctx):
    s = build_section("plans", ctx, RURAL)
    assert s.fact("plans.municipalities_with_plan").value == pytest.approx(0.5)
    assert s.fact("plans.people_multi_municipal").value == pytest.approx(0.25)
    plan = build_section("plans", ctx, PHL).fact("plans.county_plan")
    assert "Philadelphia2035" in plan.text and plan.source == "county_comprehensive_plans"
    t = build_section("plans", ctx, RURAL_T)
    assert t.fact("plans.municipality_plan").value == "no"


def test_parcels(ctx):
    s = build_section("parcels", ctx, PHL_T)
    assert s.status == "ok"
    assert s.fact("parcels.share_single_family").value == pytest.approx(0.5)
    assert s.fact("parcels.share_single_family").comparison.value == pytest.approx(0.75)
    assert s.fact("parcels.sf_built_pre_1940").value == pytest.approx(0.3)
    assert s.fact("parcels.median_sale_36m").value == 240000
    assert "licence is to be confirmed" in s.limits
    a = build_section("parcels", ctx, ALLE)
    assert (
        "2012 base-year" in a.limits and a.fact("parcels.sales_12m").source == "alle_property_sales"
    )
    r = build_section("parcels", ctx, RURAL)
    assert r.status == "no_data" and "No free parcel attributes for this county" in r.limits


def test_owners(ctx):
    s = build_section("owners", ctx, PHL)
    assert s.fact("owners.entity_share").value == pytest.approx(0.05)
    assert s.fact("owners.entity_deeds_2025").value == pytest.approx(0.25)
    assert "part year" in s.fact("owners.entity_deeds_2026").label
    bench = [f for f in s.facts if f.id.startswith("owners.benchmark_")]
    assert {f.source for f in bench} == {"philly_fed_owners_2025", "pew_phl_home_sales_2024"}
    assert all("Cited, not computed here" in f.note for f in bench)
    assert "not the same as an institutional investor" in s.limits and "estate planning" in s.limits
    t = build_section("owners", ctx, PHL_T)
    assert t.fact("owners.entity_share").comparison.value == pytest.approx(0.05)
    assert build_section("owners", ctx, RURAL).status == "no_data"


def test_pipeline_local(ctx):
    s = build_section("pipeline_local", ctx, PHL)
    assert s.fact("pipeline_local.units_5yr").value == 30  # 2021-2025: 2026 is the year read
    assert s.fact("pipeline_local.lihtc_homes").value == 40
    p = build_section("pipeline_local", ctx, ALLE_T)
    assert p.fact("pipeline_local.permits_5yr").value == 3
    assert "pipeline_local.units_5yr" not in [f.id for f in p.facts]
    r = build_section("pipeline_local", ctx, RURAL)
    assert r.fact("pipeline_local.lihtc_developments").value == 1


def test_sections_without_tables_say_no_data(tmp_path):
    f = tmp_path / "PA"
    f.mkdir()
    _write(f, "counties", [{"geoid": RURAL, "name": "Centre County"}])
    _write(f, "tracts", [{"geoid": RURAL_T, "county": RURAL}])
    ctx = load_profile_context("PA", folder=f)
    for key in ("zoning", "plans", "parcels", "owners", "pipeline_local"):
        assert build_section(key, ctx, RURAL).status == "no_data"
