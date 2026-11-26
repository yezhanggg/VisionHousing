"""Testland fixtures: regenerate identically, stay small, and hang together."""

import csv
import json
from pathlib import Path

import make_testland

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "testland"
FILES = [
    "counties.csv",
    "tracts.csv",
    "blocks.csv",
    "permits_county_year.csv",
    "transit_stops.csv",
    "boundaries.geojson",
    "data_dictionary.json",
    "acs_bands.csv",
    "infra_tract.csv",
    "hazard_tract.csv",
    "parcels_area.csv",
    "afford_income_limits.csv",
]
OPTIONAL = ["infra_tract", "hazard_tract", "parcels_area", "afford_income_limits"]


def read(name: str) -> list[dict]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_regenerates_byte_identical(tmp_path):
    make_testland.main(["--out", str(tmp_path)])
    for name in FILES:
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes(), name
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(FILES)


def test_size_budget():
    total = sum((FIXTURES / name).stat().st_size for name in FILES)
    assert total < 300 * 1024


def test_counts_match_the_spec():
    assert len(read("counties.csv")) == 4
    assert len(read("tracts.csv")) == 40
    assert len(read("blocks.csv")) == 400
    assert len(read("transit_stops.csv")) == 30


def test_referential_integrity():
    counties = {r["geoid"] for r in read("counties.csv")}
    tracts = read("tracts.csv")
    tract_ids = {r["geoid"] for r in tracts}
    assert all(r["county"] in counties for r in tracts)
    assert all(r["tract"] in tract_ids for r in read("blocks.csv"))
    blocks = {r["geoid"] for r in read("blocks.csv")}
    assert len(blocks) == 400 and len(tract_ids) == 40


def test_blocks_sum_to_tracts_and_tracts_sum_to_counties():
    by_tract: dict[str, int] = {}
    for b in read("blocks.csv"):
        by_tract[b["tract"]] = by_tract.get(b["tract"], 0) + int(b["housing_units"])
    tracts = read("tracts.csv")
    for t in tracts:
        assert by_tract[t["geoid"]] == int(t["housing_units"]), t["geoid"]
    for c in read("counties.csv"):
        mine = [t for t in tracts if t["county"] == c["geoid"]]
        assert sum(int(t["housing_units"]) for t in mine) == int(c["housing_units"])
        assert sum(int(t["population"]) for t in mine) == int(c["population"])


def test_tract_parts_add_up():
    for t in read("tracts.csv"):
        hu = int(t["housing_units"])
        assert sum(int(t[c]) for c in make_testland.STRUCTURE_COLUMNS) == hu
        owners, renters = int(t["households_owner"]), int(t["households_renter"])
        assert owners + renters + int(t["vacant_units"]) == hu
        assert sum(int(t[c]) for c in make_testland.value_columns()) == owners
        assert sum(int(t[c]) for c in make_testland.rent_columns()) == renters


def test_permits_have_every_year_and_county():
    counties = {r["geoid"] for r in read("counties.csv")}
    rows = read("permits_county_year.csv")
    pairs = {(r["county"], int(r["year"])) for r in rows}
    assert pairs == {(c, y) for c in counties for y in range(1996, 2026)}
    assert len(rows) == len(pairs)
    assert {r["imputed"] for r in rows} == {"0", "1"}


def test_permits_dip_in_the_recession_and_rise_recently():
    totals: dict[int, int] = {}
    for r in read("permits_county_year.csv"):
        n = sum(int(r[c]) for c in ("units_1", "units_2", "units_3_4", "units_5plus"))
        totals[int(r["year"])] = totals.get(int(r["year"]), 0) + n
    peak = sum(totals[y] for y in range(2003, 2007)) / 4
    dip = sum(totals[y] for y in range(2008, 2012)) / 4
    recent = sum(totals[y] for y in range(2022, 2026)) / 4
    assert dip < 0.6 * peak
    assert recent > 1.5 * dip


def test_urban_is_attached_and_rental_others_detached_and_owned():
    for t in read("tracts.csv"):
        detached, attached = int(t["units_1_detached"]), int(t["units_1_attached"])
        owners, renters = int(t["households_owner"]), int(t["households_renter"])
        if t["area_type"] == "urban":
            assert attached > detached and renters > 0.3 * (owners + renters)
        else:
            assert detached > attached and owners > renters


def test_bracket_columns_follow_acs_edges():
    values = make_testland.value_columns()
    rents = make_testland.rent_columns()
    assert len(values) == 26 and values[0] == "value_lt_10000"
    assert values[-1] == "value_2000000_plus"
    assert len(rents) == 25 and rents[-2] == "rent_3500_plus" and rents[-1] == "rent_no_cash"


def test_dictionary_covers_every_column():
    doc = json.loads((FIXTURES / "data_dictionary.json").read_text(encoding="utf-8"))
    described = {(e["table"], e["name"]) for e in doc["columns"]}
    for e in doc["columns"]:
        assert e["source"] == "synthetic" and e["unit"] and e["description"] and e["tier"]
    for name in FILES:
        if name.endswith(".csv"):
            table = name.removesuffix(".csv")
            with (FIXTURES / name).open(encoding="utf-8") as f:
                header = f.readline().strip().split(",")
            assert all((table, col) in described for col in header), table


def test_boundaries_cover_every_county_and_tract():
    doc = json.loads((FIXTURES / "boundaries.geojson").read_text(encoding="utf-8"))
    ids = {(f["properties"]["level"], f["properties"]["geoid"]) for f in doc["features"]}
    assert {("county", r["geoid"]) for r in read("counties.csv")} <= ids
    assert {("tract", r["geoid"]) for r in read("tracts.csv")} <= ids
    assert len(ids) == 44


# ------------------------------------------------------------------ the optional extra tables


def header(name: str) -> list[str]:
    with (FIXTURES / f"{name}.csv").open(encoding="utf-8") as f:
        return f.readline().strip().split(",")


def test_optional_tables_use_the_real_pack_columns():
    """Same columns, same order, as the builders of a real state pack write."""
    from visionhousing.features import affordability, hazards, infrastructure, parcels
    from visionhousing.scenarios.extra_common import OPTIONAL_TABLES

    assert set(OPTIONAL) <= set(OPTIONAL_TABLES)
    assert header("infra_tract") == list(infrastructure.TRACT_COLUMNS)
    assert header("hazard_tract") == list(hazards.TRACT_COLUMNS)
    assert header("parcels_area") == parcels.parcel_columns(parcels.load_rules())
    limits = header("afford_income_limits")
    assert limits[:5] == ["county", "hud_area_code", "hud_area_name", "areas_in_county", "median"]
    assert limits[5:] == list(affordability.LIMIT_COLUMNS)


def test_optional_tables_cover_every_tract_and_county():
    tracts = {r["geoid"] for r in read("tracts.csv")}
    counties = {r["geoid"] for r in read("counties.csv")}
    assert [r["geoid"] for r in read("infra_tract.csv")] == sorted(tracts)
    assert [r["geoid"] for r in read("hazard_tract.csv")] == sorted(tracts)
    assert {r["county"] for r in read("afford_income_limits.csv")} == counties
    parcels = read("parcels_area.csv")
    assert {r["geoid"] for r in parcels if r["level"] == "tract"} <= tracts
    measured = {make_testland.STATE_FIPS + c for c in make_testland.PARCEL_COUNTIES}
    assert {r["county"] for r in parcels} == measured < counties  # the rest: not measured


def share(v: str) -> float | None:
    return None if v == "" else float(v)


def test_readiness_has_every_answer_and_shares_stay_in_range():
    rows = read("infra_tract.csv")
    assert {r["sewer_status"] for r in rows} == {"verified", "outside", "constraint", "unknown"}
    for r in rows:
        for c in ("sewer_share", "sewer_known", "constraint_share", "water_share", "epa_share"):
            v = share(r[c])
            assert v is None or 0.0 <= v <= 1.0, (r["geoid"], c)
        if r["sewer_status"] == "unknown":
            assert r["sewer_share"] == "" and float(r["sewer_known"]) == 0.0


def test_hazards_leave_unmapped_flood_blank_not_zero():
    rows = read("hazard_tract.csv")
    blank = [r for r in rows if r["sfha_share"] == ""]
    assert blank and all(r["geoid"].startswith("99007") for r in blank)
    for r in rows:
        for c in ("sfha_share", "sfha_dev_share", "developed_share", "impervious_share",
                  "steep_share"):  # fmt: skip
            v = share(r[c])
            assert v is None or 0.0 <= v <= 1.0


def test_parcel_counts_add_up():
    from visionhousing.features import parcels as pc

    classes = [f"lu_{c}" for c in pc.load_rules()["land_use_classes"]]
    rows = read("parcels_area.csv")
    for r in rows:
        assert sum(int(r[c]) for c in classes) == int(r["parcels"])
        assert int(r["sf_parcels"]) == int(r["lu_single_family"])
        built = [c for c in r if c.startswith("sf_built_")]
        assert sum(int(r[c]) for c in built) == int(r["sf_parcels"])
    for county in {r["county"] for r in rows}:
        top = next(r for r in rows if r["level"] == "county" and r["geoid"] == county)
        mine = [r for r in rows if r["level"] == "tract" and r["county"] == county]
        for c in ("parcels", *classes):
            assert sum(int(r[c]) for r in mine) == int(top[c])


def test_income_limits_rise_with_household_size_and_income_level():
    for r in read("afford_income_limits.csv"):
        for pct in (30, 50, 80):
            row = [int(r[f"l{pct}_{s}"]) for s in range(1, 9)]
            assert row == sorted(row) and all(v % 50 == 0 for v in row)
        assert int(r["l30_4"]) < int(r["l50_4"]) < int(r["l80_4"]) < int(r["median"])
