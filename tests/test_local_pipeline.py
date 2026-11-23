"""PHFA award-list reader and city permit aggregation (small fixtures, no network)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from visionhousing.data.sources import local_permits, phfa
from visionhousing.features import pipeline_local as pl

FIX = Path(__file__).parent / "fixtures" / "local"
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}


def test_parse_awards_older_layout():
    out = phfa.parse_awards((FIX / "phfa_awards_2021.txt").read_text(), 2021)
    assert list(out["development"]) == ["Cedar Homes", "Willow Court"]
    assert list(out["county"]) == ["42003", "42029"]
    assert list(out["units"]) == [46, 52]
    assert list(out["lihtc_award"]) == [1247044, 1244573]
    assert out.loc[0, "zip"] == "15205" and out.loc[0, "city"] == "Pittsburgh"
    text = " ".join(out.astype(str).to_numpy().ravel())
    assert "@" not in text and "Jane" not in text  # contacts are never kept


def test_parse_awards_newer_layout_and_page_titles():
    out = phfa.parse_awards((FIX / "phfa_awards_2024.txt").read_text(), 2024)
    assert list(out["development"]) == ["North Ridge", "Hays Manor"]
    assert list(out["units"]) == [44, 50]  # total homes, not the "62+" age limit
    assert out.loc[0, "address"].startswith("Lot 4, Intersection")
    assert out.loc[1, "county"] == "42003" and out.loc[1, "lihtc_award"] == 1429655


def test_parse_awards_empty_text():
    assert phfa.parse_awards("", 2025).empty


def test_award_links():
    page = (
        '<a href="https://www.phfa.org/forms/multifamily_news/news/2024/2024-lihtc-awards.pdf">'
        '<a href="/forms/multifamily_news/news/2020/2020-lihtc-awrds.pdf">'
        '<a href="https://www.phfa.org/forms/x/2024-lihtc-qap.pdf">'
    )
    links = phfa.award_links(page, "lihtc-aw")
    assert sorted(links) == [2020, 2024]
    assert links[2020].startswith("https://www.phfa.org/forms/")


def test_county_codes():
    assert phfa.county_fips("Adams") == "42001"
    assert phfa.county_fips("PHILADELPHIA") == "42101"
    assert phfa.county_fips("York") == "42133"


def test_permit_queries_quote_values():
    q = local_permits.phl_query(["Building"], ["New Construction"], 2016)
    assert "permittype IN ('Building')" in q and "numberofunits" in q
    g = local_permits.pgh_query(["BUILDING"], "new construction", "Residential", 2016)
    assert "ILIKE '%new construction%'" in g and "owner_name" not in g


def test_permits_by_tract_year():
    permits = pd.DataFrame(
        {
            "lat": [0.5, 0.5, 0.2, 5.0, None],
            "lon": [0.5, 0.5, 0.2, 5.0, None],
            "year": [2024, 2024, 2025, 2024, 2025],
            "units": [2, 10, 1, 3, 4],
        }
    )
    out = pl.permits_by_tract_year(
        permits, [("42101000100", SQUARE)], "42101", "phl_li_permits", has_units=True
    )
    got = {(r.tract, r.year): (r.permits, r.units) for r in out.itertuples()}
    assert got[("42101000100", 2024)] == (2, 12)
    assert got[("", 2024)] == (1, 3) and got[("", 2025)] == (1, 4)
    pgh = pl.permits_by_tract_year(
        permits.drop(columns="units"),
        [("42003000100", SQUARE)],
        "42003",
        "pgh_pli_permits",
        has_units=False,
    )
    assert pgh["units"].isna().all() and pgh["permits"].sum() == 5


def test_lihtc_table_keeps_no_address():
    awards = pd.DataFrame(
        {
            "year": [2024],
            "development": ["A"],
            "address": ["1 Main St"],
            "city": ["X"],
            "zip": ["19000"],
            "county": ["42101"],
            "units": [10],
            "lihtc_award": [5],
            "tract": [42101000100.0],
        }
    )
    out = pl.lihtc_table(awards)
    assert list(out.columns) == list(pl.AWARD_COLUMNS)
    assert out.loc[0, "tract"] == "42101000100"
