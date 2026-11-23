"""Real-data checks for the place-profile sources (Pennsylvania).

Runs against the live published files, cached in ``data/raw``. Not part of
``make test``; run it with ``make test-realdata``. The safety check runs only when an
api.data.gov key is set in ``API_DATA_GOV_KEY`` or ``FBI_API_KEY``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from visionhousing.data.sources import incentives, jobs, safety

pytestmark = pytest.mark.realdata

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
PA_TRACTS_2020 = 3446


@pytest.fixture(scope="module")
def wac():
    return jobs.fetch_wac_tracts("PA", RAW)


@pytest.fixture(scope="module")
def inc():
    return incentives.fetch_incentives("PA", RAW)


def test_lodes_workplace_and_flow_files_count_the_same_jobs(wac):
    flows = jobs.fetch_od_county("PA", RAW)
    assert wac["tract"].str.startswith("42").all()
    assert 3300 < len(wac) <= PA_TRACTS_2020
    assert 5_000_000 < wac["jobs"].sum() < 7_000_000
    assert flows["jobs"].sum() == wac["jobs"].sum()
    assert set(flows["work_county"]) == {f"42{n:03d}" for n in range(1, 134, 2)}


def test_cbp_has_every_county_and_sector_totals():
    cbp = jobs.fetch_cbp("PA", RAW)
    totals = cbp[cbp["naics"] == "00"]
    assert len(totals) == 67
    allegheny = totals.set_index("county").loc["42003"]
    assert 500_000 < allegheny["employment"] < 900_000
    sectors = cbp[(cbp["county"] == "42003") & (cbp["naics"] != "00")]
    assert sectors["employment"].sum() <= allegheny["employment"] * 1.02


def test_incentive_lists_cover_the_state(inc):
    assert inc.notes == []
    assert 400 < len(inc.qct) < 900 and inc.qct["tract"].str.startswith("42").all()
    assert len(inc.nmtc) == PA_TRACTS_2020
    assert 0.2 < inc.nmtc["nmtc_lic"].mean() < 0.6
    assert 150 < len(inc.oz2027) < 400
    assert (inc.dda["dda_type"].isin(["SA", "NM"])).all()
    assert len(inc.lihtc) > 1500
    assert len(inc.koz) > 100 and len(inc.nrhp_points) > 2000
    assert len(inc.nrhp_districts) > 500


@pytest.mark.skipif(safety.api_key() is None, reason="no api.data.gov key in the environment")
def test_fbi_agency_counts_for_pennsylvania():
    table = safety.fetch_safety("PA", RAW)
    assert table is not None and len(table) > 900
    full = table[(table["violent_months"] >= 12) & (table["property_months"] >= 12)]
    assert len(full) > 0.7 * len(table)
    assert full["population"].sum() > 10_000_000
