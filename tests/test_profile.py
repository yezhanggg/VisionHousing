"""Place profiles on Testland: the model, each section, the registry, the renderers and the CLI.

Numbers are checked against the Testland tables directly, so a profile can never show a
number its sources do not hold.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import sys
import types

import numpy as np
import pandas as pd
import pytest

from visionhousing import cli
from visionhousing.data.registry import load_registry
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.features import brackets as br
from visionhousing.profile import (
    BUILT,
    RESERVED,
    SECTIONS,
    PlaceNotFoundError,
    Section,
    build_profile,
    list_places,
    load_profile_context,
    render,
)
from visionhousing.profile.format import compare_words, fmt
from visionhousing.profile.registry import build_section
from visionhousing.profile.render import CSV_COLUMNS

COUNTY = "99001"
TRACT = "99001000100"


@pytest.fixture(scope="module")
def ctx():
    return load_profile_context("TL")


@pytest.fixture(scope="module")
def county(ctx):
    return build_profile("TL", COUNTY, ctx=ctx)


@pytest.fixture(scope="module")
def tract(ctx):
    return build_profile("TL", TRACT, ctx=ctx)


@pytest.fixture(scope="module")
def tracts():
    return pd.read_csv(TESTLAND_DIR / "tracts.csv", dtype={"geoid": str, "county": str})


# --------------------------------------------------------------------------- model


def test_profile_has_every_section_in_order(county):
    assert [s.key for s in county.sections] == [k for k, _ in SECTIONS]
    assert county.level == "county" and county.name == "Harbor County"
    assert county.synthetic is True
    for key in BUILT:
        assert county.section(key).status == "ok", key
    for key in RESERVED:
        sec = county.section(key)
        assert sec.status == "coming_soon" and not sec.facts
        assert "not available" in sec.summary.lower()


def test_every_fact_has_a_registered_source_and_a_vintage(county, tract):
    registry = load_registry()
    for profile in (county, tract):
        for f in profile.facts():
            assert f.source in registry, f.id
            assert f.vintage == "synthetic test data (Testland)", f.id
            assert f.year and f.measured_at in ("state", "county", "tract"), f.id
            assert f.id.split(".")[0] in dict(SECTIONS)
            assert f.text
        for s in profile.sections:
            assert set(s.sources) <= set(registry), s.key
            assert set(s.sources) <= set(profile.sources)


def test_fact_ids_are_unique(county):
    ids = [f.id for f in county.facts()]
    assert len(ids) == len(set(ids))


def test_summaries_are_one_sentence(county, tract):
    for profile in (county, tract):
        for s in profile.sections:
            assert s.summary.endswith("."), s.key
            assert s.summary.count(". ") == 0, s.summary


def test_to_dict_is_json_ready(county):
    data = json.loads(json.dumps(county.to_dict(), allow_nan=False))
    assert data["geoid"] == COUNTY
    assert data["sections"][0]["facts"][0]["source"] == "acs5_summary_file"


def test_unknown_place_and_section(ctx):
    with pytest.raises(PlaceNotFoundError):
        build_profile("TL", "99999", ctx=ctx)
    with pytest.raises(PlaceNotFoundError):
        build_profile("TL", "abc", ctx=ctx)
    with pytest.raises(KeyError):
        build_profile("TL", COUNTY, ctx=ctx, sections=["nope"])


def test_section_subset(ctx):
    p = build_profile("TL", COUNTY, ctx=ctx, sections=["permits", "people"])
    assert [s.key for s in p.sections] == ["people", "permits"]


# --------------------------------------------------------------------------- formatting


def test_format_and_words():
    assert fmt(1234.4, "persons") == "1,234"
    assert fmt(65000, "dollars") == "$65,000"
    assert fmt(1050, "dollars per month") == "$1,050 a month"
    assert fmt(0.123, "share") == "12%"
    assert fmt(0.081, "share") == "8.1%"
    assert fmt(3.44, "percent change") == "+3.4%"
    assert fmt(None, "units") == "not available"
    assert fmt(float("nan"), "units") == "not available"
    assert compare_words(0.30, 0.20, "share", "state") == "higher than the state"
    assert compare_words(0.20, 0.21, "share", "county") == "about the same as the county"
    assert compare_words(100, 120, "dollars", "state") == "lower than the state"
    assert compare_words(100, 103, "dollars", "state") == "about the same as the state"
    assert compare_words(None, 1, "dollars", "state") == ""


# --------------------------------------------------------------------------- sections


def test_people_numbers_match_testland(county, tract, tracts):
    rows = tracts[tracts["county"] == COUNTY]
    people = county.section("people")
    assert people.fact("people.population").value == rows["population"].sum()
    hh = rows["households_owner"].sum() + rows["households_renter"].sum()
    assert people.fact("people.households").value == hh
    renter = people.fact("people.renter_share")
    assert renter.value == pytest.approx(rows["households_renter"].sum() / hh)
    assert renter.comparison.against == "state"
    all_hh = tracts["households_owner"].sum() + tracts["households_renter"].sum()
    assert renter.comparison.value == pytest.approx(tracts["households_renter"].sum() / all_hh)
    weights = rows["households_owner"] + rows["households_renter"]
    income = np.average(rows["median_household_income"], weights=weights)
    assert people.fact("people.median_household_income").value == pytest.approx(income)
    assert "approximate" in people.fact("people.median_household_income").note
    one = tracts[tracts["geoid"] == TRACT].iloc[0]
    t_income = tract.section("people").fact("people.median_household_income")
    assert t_income.value == one["median_household_income"]
    assert t_income.comparison.against == "county"
    assert t_income.comparison.value == pytest.approx(income)
    assert t_income.measured_at == "tract"


def test_housing_numbers_match_testland(county, tract, tracts):
    rows = tracts[tracts["county"] == COUNTY]
    housing = county.section("housing")
    units = rows["housing_units"].sum()
    assert housing.fact("housing.housing_units").value == units
    five = rows[["units_5_9", "units_10_19", "units_20_plus"]].sum().sum()
    assert housing.fact("housing.share_5plus").value == pytest.approx(five / units)
    counts = rows[list(br.VALUE.columns)].sum().to_numpy(dtype=float)
    expected = br.median_from_brackets(counts, br.VALUE).value
    value = housing.fact("housing.median_home_value")
    assert value.value == pytest.approx(expected)
    assert "bracket" in value.note
    one = tracts[tracts["geoid"] == TRACT].iloc[0]
    assert (
        tract.section("housing").fact("housing.median_gross_rent").value == one["median_gross_rent"]
    )
    # Testland has no year-built columns and no price index: those facts are left out.
    ids = {f.id for f in housing.facts}
    assert "housing.share_built_before_1940" not in ids
    assert "housing.hpi_change_5yr" not in ids


def test_permits_numbers_match_testland(county, tract):
    permits = pd.read_csv(TESTLAND_DIR / "permits_county_year.csv", dtype={"county": str})
    permits["total"] = permits[["units_1", "units_2", "units_3_4", "units_5plus"]].sum(axis=1)
    latest = int(permits["year"].max())
    mine = permits[permits["county"] == COUNTY]
    recent = mine[mine["year"] > latest - 5]
    before = mine[(mine["year"] > latest - 10) & (mine["year"] <= latest - 5)]
    sec = county.section("permits")
    assert sec.fact("permits.total_5yr").value == recent["total"].sum()
    assert sec.fact("permits.total_prior_5yr").value == before["total"].sum()
    change = (recent["total"].sum() / before["total"].sum() - 1) * 100
    assert sec.fact("permits.change_5yr").value == pytest.approx(change)
    share = recent["units_1"].sum() / recent["total"].sum()
    assert sec.fact("permits.single_family_share").value == pytest.approx(share)
    assert (
        sec.fact(f"permits.year_{latest}").value == mine.loc[mine["year"] == latest, "total"].sum()
    )
    state_recent = permits[permits["year"] > latest - 5]
    state_share = state_recent["units_1"].sum() / state_recent["total"].sum()
    assert sec.fact("permits.single_family_share").comparison.value == pytest.approx(state_share)
    # A tract shows its county's permits, measured by county.
    tsec = tract.section("permits")
    assert tsec.fact("permits.total_5yr").value == recent["total"].sum()
    assert {f.measured_at for f in tsec.facts} == {"county"}
    assert "which holds this tract" in tsec.summary


def test_transit_section(ctx, county, tract):
    sec = county.section("transit")
    stops = ctx.stops()
    expected = int(((stops["major"] == 1) & (stops["county"] == COUNTY)).sum())
    assert sec.fact("transit.major_stops").value == expected
    near = sec.fact("transit.homes_near_transit")
    assert 0 <= near.value <= 1
    assert near.source == "tiger_2020_tabblock"
    assert tract.section("transit").fact("transit.homes_near_transit").comparison.against == (
        "county"
    )


def test_data_quality_section(ctx, county):
    sec = county.section("data_quality")
    card = ctx.report_card()
    assert sec.facts
    assert {f.measured_at for f in sec.facts} == {"state"}
    assert str(len(card["inputs"])) in sec.summary
    assert "synthetic" in sec.limits


# --------------------------------------------------------------------------- degrading


@pytest.fixture()
def thin_pack(tmp_path):
    """Testland without permits, prices or transit stops."""
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    (folder / "permits_county_year.csv").unlink()
    (folder / "transit_stops.csv").unlink()
    return folder


def test_missing_tables_give_no_data_sections(thin_pack):
    ctx = load_profile_context("TL", folder=thin_pack)
    p = build_profile("TL", COUNTY, ctx=ctx)
    assert p.section("permits").status == "no_data"
    assert p.section("transit").status == "no_data"
    assert "no data" in p.section("permits").summary.lower()
    assert p.section("people").status == "ok"
    for fmt_name in ("md", "html", "csv", "json"):
        assert render(p, fmt_name)


def test_registry_degrades_and_picks_up_new_modules(ctx, monkeypatch):
    assert build_section("jobs", ctx, COUNTY).status == "coming_soon"
    fake = types.ModuleType("visionhousing.profile.sections.jobs")
    fake.build = lambda c, g: None
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    assert build_section("jobs", ctx, COUNTY).status == "no_data"
    fake.build = lambda c, g: Section("jobs", "Jobs and employers", "Jobs are here.")
    assert build_section("jobs", ctx, COUNTY).summary == "Jobs are here."


# --------------------------------------------------------------------------- places


def test_list_places(ctx):
    counties = list_places(ctx)
    names = [p["name"] for p in counties]
    assert names == sorted(names) and len(names) == 4
    assert any(p["name"] == "Harbor County" for p in counties)
    assert [p["geoid"] for p in list_places(ctx, "harb")] == [COUNTY]
    tr = list_places(ctx, COUNTY, level="tract", limit=500)
    assert tr and all(p["geoid"].startswith(COUNTY) for p in tr)
    assert tr[0]["name"].endswith("Harbor County")
    with pytest.raises(ValueError):
        list_places(ctx, level="block")


# --------------------------------------------------------------------------- renderers


def test_markdown(county):
    md = render(county, "md")
    assert md.startswith("# Harbor County")
    assert "Synthetic test state" in md
    assert "## People" in md and "## Sources" in md
    assert "Coming soon" in md
    assert "`acs5_summary_file`" in md


def test_html_is_self_contained_and_printable(county):
    page = render(county, "html")
    assert page.startswith("<!doctype html>")
    assert "@page" in page and "size: letter" in page
    assert "<script" not in page and "http://" not in page
    assert "Harbor County" in page and "<sup" in page


def test_csv_one_row_per_fact(county):
    rows = list(csv.DictReader(io.StringIO(render(county, "csv"))))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == len(county.facts())
    assert {r["fact_id"] for r in rows} == {f.id for f in county.facts()}
    assert all(r["source"] and r["vintage"] for r in rows)


def test_json_round_trips(county):
    assert json.loads(render(county, "json")) == json.loads(json.dumps(county.to_dict()))


def test_renderers_are_deterministic(ctx):
    a = build_profile("TL", COUNTY, ctx=ctx)
    b = build_profile("TL", COUNTY, ctx=load_profile_context("TL"))
    for fmt_name in ("md", "html", "csv", "json"):
        assert render(a, fmt_name) == render(b, fmt_name)


def test_unknown_format(county):
    with pytest.raises(ValueError):
        render(county, "pdf")


# --------------------------------------------------------------------------- CLI


def test_cli_profile_prints_markdown(capsys):
    assert cli.main(["profile", "--state", "TL", "--county", COUNTY]) == 0
    assert capsys.readouterr().out.startswith("# Harbor County")


@pytest.mark.parametrize("fmt_name", ["md", "html", "csv", "json"])
def test_cli_profile_writes_each_format(tmp_path, fmt_name, ctx):
    out = tmp_path / f"p.{fmt_name}"
    args = ["profile", "--state", "TL", "--tract", TRACT, "--format", fmt_name, "--out", str(out)]
    assert cli.main(args) == 0
    assert out.read_text(encoding="utf-8") == render(build_profile("TL", TRACT, ctx=ctx), fmt_name)


def test_cli_profile_errors(capsys):
    assert cli.main(["profile", "--state", "TL"]) == 2
    assert cli.main(["profile", "--state", "TL", "--county", "99003", "--tract", TRACT]) == 2
    assert cli.main(["profile", "--state", "TL", "--county", "99999"]) == 1
    assert "no county or tract" in capsys.readouterr().err
