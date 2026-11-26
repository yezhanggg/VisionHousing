"""Keyless city safety records: Carto queries, the cached fetch, the tract table, the section.

No test uses the network. A fake Carto server answers with the small hand-made CSV
answers in ``tests/fixtures/place/carto_safety_*.csv`` (the shape the City's service
returns: one count per year and location cell). Their points sit in synthetic
Testland, so no real record is in the repository.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.parse
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tests.pack_fakes import Response, client
from visionhousing.data.sources import phl_safety as src
from visionhousing.data.sources import place as place_src
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.features import safety_local as fx
from visionhousing.profile import load_profile_context
from visionhousing.profile.registry import build_section

FIX = Path(__file__).resolve().parent / "fixtures" / "place"
TODAY = date(2025, 3, 1)
COUNTY = "99001"
RULES = {**fx.load_rules(), "first_year": 2023}
TABLE_OF = {
    "shootings": "shootings",
    "incidents_part1_part2": "incidents",
    "violations": "violations",
    "public_cases_fc": "requests_311",
}


class FakeCarto:
    """Answers each query with the fixture rows of its table and year."""

    def __init__(self):
        self.queries: list[str] = []

    def open(self, req, data=None, timeout=None):
        params = dict(urllib.parse.parse_qsl(req.full_url.partition("?")[2]))
        q = params["q"]
        assert params["format"] == "csv"
        self.queries.append(q)
        table = re.search(r"FROM (\w+)", q).group(1)
        year = re.match(r"SELECT (\d{4}) AS year", q).group(1)
        rows = pd.read_csv(FIX / f"carto_safety_{TABLE_OF[table]}.csv", dtype=str)
        rows = rows[rows["year"] == year]
        return Response(200, rows.to_csv(index=False).encode())


def shapes() -> list[tuple[str, dict]]:
    geo = json.loads((TESTLAND_DIR / "boundaries.geojson").read_text())
    return [(f["properties"]["geoid"], f["geometry"]) for f in geo["features"]
            if f["properties"]["level"] == "tract"]  # fmt: skip


def tracts() -> pd.DataFrame:
    return pd.read_csv(TESTLAND_DIR / "tracts.csv", dtype={"geoid": str, "county": str})


@pytest.fixture
def extracts(tmp_path):
    return src.fetch_phl_safety(tmp_path, rules=RULES, client=client(FakeCarto()),
                                manifest=None, today=TODAY)  # fmt: skip


# --------------------------------------------------------------------------- queries


def test_queries_count_on_the_server_and_keep_no_record():
    qs = src.queries(2024, RULES["philadelphia"], 4)
    assert set(qs) == set(src.DATASETS)
    for q in qs.values():
        assert q.startswith("SELECT 2024 AS year, round(ST_Y(the_geom)::numeric, 4) AS lat")
        assert "GROUP BY 2, 3" in q and "SELECT *" not in q and ".*" not in q
        for private in ("location", "address", "casenumber AS", "owner", "dc_key"):
            assert private not in q
    assert "ucr_general IN ('100', '200', '300', '400')" in qs["incidents"]
    assert "dispatch_date >= '2024-01-01' AND dispatch_date < '2025-01-01'" in qs["incidents"]
    assert "count(DISTINCT casenumber)" in qs["violations"]
    assert "'Illegal Dumping'" in qs["requests_311"]


def test_query_values_are_quoted():
    rules = {**RULES["philadelphia"], "requests_311": ["O'Hara's request"]}
    assert "'O''Hara''s request'" in src.queries(2024, rules, 4)["requests_311"]


def test_only_full_years_are_read():
    assert src.years_to_read(2021, date(2026, 11, 23)) == [2021, 2022, 2023, 2024, 2025]
    assert src.years_to_read(2025, date(2025, 6, 1)) == []


# --------------------------------------------------------------------------- fetch


def test_fetch_reads_each_year_once_then_uses_the_cache(tmp_path):
    fake = FakeCarto()
    c = client(fake)
    got = src.fetch_phl_safety(tmp_path, rules=RULES, client=c, manifest=None, today=TODAY)
    assert len(fake.queries) == 4 * 2  # four tables, years 2023 and 2024
    assert set(got) == set(src.DATASETS)
    s = got["shootings"]
    assert sorted(s["year"].unique()) == [2023, 2024] and s["shootings"].sum() == 70
    assert (tmp_path / "local" / "phl" / "safety_incidents.csv").exists()
    src.fetch_phl_safety(tmp_path, rules=RULES, client=c, manifest=None, today=TODAY)
    assert len(fake.queries) == 8


# --------------------------------------------------------------------------- table


def test_table_places_cells_in_tracts_and_keeps_city_totals(extracts):
    t = fx.safety_local_table(extracts, shapes(), tracts(), COUNTY, 500)
    county = t[t["level"] == "county"].set_index("year")
    assert county.loc[2024, "shootings"] == 30  # 15 + 12 + 3 with no location
    assert county.loc[2024, "unplaced"] == 3 + 4  # one shooting cell, one violation cell
    assert county.loc[2023, "violent"] == 110
    one = t[(t["geoid"] == "99001000100")].set_index("year")
    assert one.loc[2023, "shootings"] == 30 and one.loc[2024, "property"] == 500
    pop = one.loc[2023, "population"]
    assert one.loc[2023, "shootings_per_1000"] == pytest.approx(30 / pop * 1000, abs=0.01)
    # every tract of the county has every year, with zeros where nothing was recorded
    assert len(t[t["level"] == "tract"]) == 14 * 2
    quiet = t[(t["geoid"] == "99001001400") & (t["year"] == 2024)].iloc[0]
    assert quiet["violent"] == 0 and quiet["violent_per_1000"] == 0
    assert set(t["county"]) == {COUNTY}
    for col in t.columns:
        assert ("safety_local", col) in fx.COLUMNS


def test_small_tracts_get_counts_but_no_rate(extracts):
    t = fx.safety_local_table(extracts, shapes(), tracts(), COUNTY, 5000)
    small = t[(t["geoid"] == "99001000200") & (t["year"] == 2023)].iloc[0]
    assert small["population"] < 5000 and small["shootings"] == 10
    assert pd.isna(small["shootings_per_1000"])
    assert t[t["level"] == "county"]["shootings_per_1000"].notna().all()


def test_place_tables_builds_the_philadelphia_table(extracts, monkeypatch):
    monkeypatch.setattr(fx, "PHILADELPHIA", COUNTY)
    inputs = place_src.PlaceInputs(safety_local=extracts)
    counties = pd.read_csv(TESTLAND_DIR / "counties.csv", dtype={"geoid": str})
    out = place_src.place_tables(inputs, tracts(), counties, shapes())
    assert "safety_local" in out and "safety_county" not in out
    assert ("safety_local", "violent_per_1000") in place_src.TIER_TWO


# --------------------------------------------------------------------------- section


@pytest.fixture
def pack(tmp_path, extracts):
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    t = fx.safety_local_table(extracts, shapes(), tracts(), COUNTY, 500)
    t.to_csv(folder / "safety_local.csv", index=False)
    return load_profile_context("TL", folder=folder)


def test_section_county_has_rates_counts_and_trend_without_state(pack):
    sec = build_section("safety", pack, COUNTY)
    assert sec.status == "ok"
    rate = sec.fact("safety.shootings_per_1000")
    pop = float(tracts().query("county == @COUNTY")["population"].sum())
    assert rate.value == pytest.approx(70 / (pop * 2) * 1000)
    assert rate.comparison is None and "no statewide figure" in rate.note
    assert rate.source == "phl_shootings" and rate.year == "2023-2024"
    assert sec.fact("safety.shootings_2024").value == 30
    assert sec.fact("safety.shootings_trend").value == pytest.approx((30 / 40 - 1) * 100)
    assert sec.fact("safety.shootings_fatal_2024").value == 2
    assert "no statewide figure" in sec.summary and "fell 25%" in sec.summary
    assert "not the risk to any one person" in sec.limits
    assert "FBI" not in sec.limits  # no FBI table in this pack


def test_section_tract_is_compared_with_its_county(pack):
    sec = build_section("safety", pack, "99001000100")
    rate = sec.fact("safety.violent_per_1000")
    pop = float(tracts().set_index("geoid").loc["99001000100", "population"])
    assert rate.value == pytest.approx(180 / (pop * 2) * 1000)
    assert rate.comparison.against == "county" and rate.comparison.words == "higher than the county"
    assert "(higher than the county)" in sec.summary
    # a first-year count under min_trend_count gives no trend
    assert sec.fact("safety.li_cases_trend").value == pytest.approx(20.0)
    two = build_section("safety", pack, "99001000200")
    assert two.fact("safety.shootings_trend").value is None
    assert "no trend" in two.fact("safety.shootings_trend").note


def test_section_outside_the_city_has_no_city_records(pack):
    sec = build_section("safety", pack, "99003")
    assert sec.status == "no_data" and "api.data.gov key" in sec.limits
