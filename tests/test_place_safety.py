"""Safety data for place profiles: FBI API readers, key handling, the county table, the section.

No test needs an api.data.gov key or the network: a fake server answers with the small
files in ``tests/fixtures/place``.
"""

from __future__ import annotations

import json
import shutil
import urllib.parse
from pathlib import Path

import pandas as pd
import pytest

from tests.pack_fakes import Response, client
from visionhousing.data.sources import safety as src
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.features import safety as fx
from visionhousing.profile import load_profile_context
from visionhousing.profile.registry import build_section

FIX = Path(__file__).resolve().parent / "fixtures" / "place"
KEY = "test-key-0123456789"


def test_key_comes_only_from_the_environment():
    assert src.api_key({}) is None
    assert src.api_key({"FBI_API_KEY": " abc "}) == "abc"
    assert src.api_key({"API_DATA_GOV_KEY": "first", "FBI_API_KEY": "second"}) == "first"


def test_agency_list_reader_dedupes_and_keeps_counties():
    t = src.read_agencies((FIX / "cde_agencies_sample.json").read_bytes())
    assert list(t["ori"]) == ["TL0010100", "TL0010900", "TL001SP00", "TL0030100"]
    row = t.set_index("ori").loc["TL0030100"]
    assert row["county_names"] == "MAPLE, HARBOR" and row["agency_type"] == "City"


def test_offense_reader_picks_the_agency_series():
    total, months, pop = src.read_agency_offenses(
        (FIX / "cde_offenses_sample.json").read_bytes(), "Pennsylvania"
    )
    assert total == 30 and months == 12 and pop == 25000
    assert src.read_agency_offenses("{}") == (None, 0, None)


class FakeCde:
    """Serves the agency list and the same offense answer for every agency and offense."""

    def __init__(self):
        self.urls: list[str] = []

    def open(self, req, data=None, timeout=None):
        url = req.full_url
        self.urls.append(url)
        if "/agency/byStateAbbr/" in url:
            return Response(200, (FIX / "cde_agencies_sample.json").read_bytes())
        query = dict(urllib.parse.parse_qsl(url.partition("?")[2]))
        assert query["from"] == "01-2024" and query["to"] == "12-2024"
        return Response(200, (FIX / "cde_offenses_sample.json").read_bytes())


def test_fetch_without_a_key_does_nothing(tmp_path, monkeypatch):
    for name in src.KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    fake = FakeCde()
    assert src.fetch_safety("PA", tmp_path, client=client(fake), manifest=None) is None
    assert fake.urls == []


def test_fetch_with_a_key_caches_without_the_key(tmp_path):
    fake = FakeCde()
    c = client(fake)
    table = src.fetch_safety("PA", tmp_path, key=KEY, client=c, manifest=None)
    # the university police serve no residents and are left out
    assert list(table["ori"]) == ["TL0010100", "TL001SP00", "TL0030100"]
    assert (table["violent"] == 30).all() and (table["violent_months"] == 12).all()
    assert all(f"API_KEY={KEY}" in u for u in fake.urls)
    for path in tmp_path.rglob("*"):
        assert KEY not in path.name
        if path.is_file():
            assert KEY not in path.read_text()
    calls = len(fake.urls)
    src.fetch_safety("PA", tmp_path, key=KEY, client=c, manifest=None)
    assert len(fake.urls) == calls  # read from the cache


def test_errors_never_show_the_key(tmp_path):
    class Refuses:
        def open(self, req, data=None, timeout=None):
            return Response(403, f"bad key {KEY}".encode())

    with pytest.raises(Exception) as err:
        src.fetch_safety("PA", tmp_path, key=KEY, client=client(Refuses()), manifest=None)
    assert KEY not in str(err.value)


# --------------------------------------------------------------------------- table


def counties() -> pd.DataFrame:
    return pd.read_csv(TESTLAND_DIR / "counties.csv", dtype={"geoid": str})


def agencies() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ori": ["A", "B", "C", "D", "E"],
            "agency_name": ["a", "b", "c", "d", "e"],
            "agency_type": ["City", "State Police", "City", "City", "City"],
            "county_names": ["HARBOR", "Harbor County", "MAPLE, HARBOR", "HARBOR", "NOWHERE"],
            "year": [2024] * 5,
            "violent": [30, 10, 5, 100, 1],
            "violent_months": [12, 12, 12, 6, 12],
            "property": [300, 100, 50, 900, 1],
            "property_months": [12, 12, 12, 12, 12],
            "population": [20000, 10000, 5000, 9000, 100],
        }
    )


def test_safety_table_adds_full_year_agencies_by_first_county():
    t = fx.safety_table(agencies(), counties()).set_index("county")
    harbor = t.loc["99001"]
    assert harbor["agencies"] == 3 and harbor["agencies_full_year"] == 2  # D reported 6 months
    assert harbor["population_covered"] == 30000
    assert harbor["violent_per_1000"] == pytest.approx(40 / 30000 * 1000, abs=0.01)
    assert harbor["property_per_1000"] == pytest.approx(400 / 30000 * 1000, abs=0.01)
    maple = t.loc["99003"]  # C lists Maple first
    assert maple["violent"] == 5 and maple["population_covered"] == 5000
    assert t.loc["99007", "agencies"] == 0 and pd.isna(t.loc["99007", "violent_per_1000"])
    assert harbor["coverage_share"] == pytest.approx(30000 / 62970, abs=1e-4)
    for col in t.reset_index().columns:
        assert ("safety_county", col) in fx.COLUMNS


# --------------------------------------------------------------------------- section


@pytest.fixture
def pack(tmp_path):
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    fx.safety_table(agencies(), counties()).to_csv(folder / "safety_county.csv", index=False)
    return load_profile_context("TL", folder=folder)


def test_safety_section_county_and_state(pack):
    sec = build_section("safety", pack, "99001")
    assert sec.status == "ok"
    v = sec.fact("safety.violent_per_1000")
    assert v.value == pytest.approx(40 / 30000 * 1000)
    assert v.text == "1.3 per 1,000 residents"
    assert v.comparison.against == "state"
    assert v.comparison.value == pytest.approx(45 / 35000 * 1000)
    assert v.comparison.text == "1.3 per 1,000 residents"
    assert sec.fact("safety.agencies_full_year").value == 2
    assert "Harbor County" in sec.summary and "2024" in sec.summary
    assert "not all crime" in sec.limits


def test_safety_section_tract_shows_its_county(pack):
    sec = build_section("safety", pack, "99001000100")
    v = sec.fact("safety.violent_per_1000")
    assert v.measured_at == "county" and v.value == pytest.approx(40 / 30000 * 1000)
    assert "this tract's county" in sec.summary


def test_safety_section_without_table_is_no_data():
    assert build_section("safety", load_profile_context("TL"), "99001").status == "no_data"


def test_sample_offense_file_is_valid_json():
    assert json.loads((FIX / "cde_offenses_sample.json").read_text())["offenses"]
