"""Real-data check: Pennsylvania county permits add up to the published state totals.

Runs against the live Census files (cached in ``data/raw/bps``). Not part of
``make test``; run it with ``make test-realdata``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from visionhousing.data.sources import bps

pytestmark = pytest.mark.realdata

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
SPOT_YEARS = (2005, 2015, 2024)


@pytest.fixture(scope="module")
def pa_permits():
    return bps.fetch_bps("PA", range(bps.FIRST_YEAR, bps.LATEST_YEAR + 1), RAW)


def test_every_year_parses_with_67_counties(pa_permits):
    counties = pa_permits[pa_permits.level == "county"]
    per_year = counties.groupby("year")["geoid"].nunique()
    assert list(per_year.index) == list(range(1990, 2026))
    assert set(per_year) == {67}


@pytest.mark.parametrize("year", SPOT_YEARS)
def test_county_sums_match_published_state_totals(pa_permits, year):
    check = bps.county_state_check(pa_permits)
    rows = check[check.year == year]
    assert len(rows) == 4
    assert (rows["difference"] == 0).all(), rows.to_string()


def test_spot_check_totals_are_the_published_numbers(pa_permits):
    state = pa_permits[pa_permits.level == "state"].groupby("year")["units"].sum()
    # Published PA totals (all structure types) in the state annual files.
    assert state.loc[2005] == 44525
    assert state.loc[2015] == 22854
    assert state.loc[2024] == 24810
