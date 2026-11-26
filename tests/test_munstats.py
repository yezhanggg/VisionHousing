"""Munstats reader, DCED-to-Census crosswalk and zoning-status tables (fixtures, no network)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from visionhousing.data.sources import munstats as m
from visionhousing.features import zoning_status as zs

FIX = Path(__file__).parent / "fixtures" / "local"


@pytest.fixture
def dced() -> pd.DataFrame:
    return m.parse_munstats((FIX / "munstats_sample.csv").read_bytes())


@pytest.fixture
def census() -> pd.DataFrame:
    pop = m.parse_subdivision_population((FIX / "subest_sample.csv").read_bytes())
    return m.census_subdivisions(pop)


def test_form_fields_and_export_url():
    page = (FIX / "munstats_page.html").read_text()
    fields = m.hidden_fields(page)
    assert fields == {"__VIEWSTATE": "abc&def", "__VIEWSTATEGENERATOR": "754BCA1B"}
    url = m.export_url(page)
    assert url.startswith("https://apps.dced.pa.gov/Munstats-public/")
    assert "&OpType=Export" in url and url.endswith("Format=CSV")
    with pytest.raises(ValueError, match="ExportUrlBase"):
        m.export_url("<html></html>")


def test_parse_drops_county_rows_and_counts_members(dced):
    assert "ADAMS COUNTY" not in set(dced["name"])
    row = dced.set_index("name").loc["ARENDTSVILLE BORO"]
    assert row["comprehensive_plan"] == 1 and row["plan_members"] == 2
    assert row["zoning_members"] == 2
    with pytest.raises(ValueError, match="lacks columns"):
        m.parse_munstats("A,B\n1,2\n")


@pytest.mark.parametrize(
    ("name", "regime"),
    [
        ("ABBOTTSTOWN BORO", "own"),
        ("ARENDTSVILLE BORO", "joint"),
        ("BERWICK TWP", "county"),
        ("MC SHERRYSTOWN BORO", "none"),
    ],
)
def test_zoning_regime(dced, name, regime):
    assert m.zoning_regime(dced.set_index("name").loc[name]) == regime


def test_zoning_regime_unmatched_row_is_blank():
    assert m.zoning_regime({"zoning_ordinance": pd.NA}) == ""


def test_county_codes_and_names():
    assert m.dced_county_fips("010033") == "42001"
    assert m.dced_county_fips("020303") == "42003"
    assert m.dced_county_fips("510012") == "42101"
    assert m.normalise_name("Mc Sherrystown Boro") == m.normalise_name("McSherrystown borough")
    assert m.normalise_name("Mount Joy Twp") == m.normalise_name("Mt. Joy township")


def test_population_includes_consolidated_city(census):
    phl = census[census["county"] == "42101"]
    assert list(phl["cousub"]) == ["60000"] and int(phl["population"].iloc[0]) == 1573916
    assert not census["name"].str.contains("not defined").any()


def test_crosswalk_matches_and_lists_the_rest(dced, census):
    cw = m.crosswalk(dced, census, {"541865": "79120"})
    by_name = cw.table.set_index("name")
    assert by_name.loc["Bethel Park municipality", "dced_id"] == "020303"  # type word differs
    assert by_name.loc["Oil City city", "dced_id"] == "610512"  # DCED has no type word
    assert by_name.loc["Upper Mahantongo township", "dced_id"] == "541865"  # hand-checked pair
    assert by_name.loc["McSherrystown borough", "dced_id"] == "010153"
    assert list(cw.unmatched_dced["name"]) == ["NOWHERE TWP"]
    assert "Wheatland borough" in set(cw.unmatched_census["name"])
    assert cw.match_rate == pytest.approx(7 / 8)


def test_crosswalk_without_override_leaves_spelling_difference(dced, census):
    cw = m.crosswalk(dced, census)
    assert "UPPER MAHANTANGO TWP" in set(cw.unmatched_dced["name"])


def test_block_assignment_and_tables(dced, census):
    baf = m.parse_block_assignment((FIX / "baf_mcd_sample.txt").read_bytes())
    blocks = pd.DataFrame(
        {
            "geoid": ["420010301011000", "420010301011001", "421010001001000"],
            "tract": ["42001030101", "42001030101", "42101000100"],
            "housing_units": [4, 6, 10],
        }
    )
    pieces = zs.tract_municipalities(blocks, baf)
    split = pieces[pieces["tract"] == "42001030101"].set_index("cousub")["housing_units_2020"]
    assert split.to_dict() == {"00116": 4, "05000": 6}
    cw = m.crosswalk(dced, census, {"541865": "79120"})
    table = zs.municipal_planning(cw, pieces, multi_min=2)
    assert list(table.columns) == list(zs.MUNICIPAL_COLUMNS)
    row = table.set_index("name").loc["Arendtsville borough"]
    assert row["multi_municipal_plan"] == 1 and row["zoning_regime"] == "joint"
    assert table.set_index("name").loc["Abbottstown borough", "housing_units_2020"] == 4
    assert table.set_index("name").loc["Wheatland borough", "dced_id"] == ""


def test_rules_and_plan_citations():
    rules = zs.load_rules()
    assert list(rules["regimes"]) == list(m.REGIMES)
    plans = zs.read_plan_citations()
    assert {"42101", "42003"} <= set(plans)
    for code, plan in plans.items():
        assert len(code) == 5 and isinstance(plan["verified"], bool)
        # A link is given only when it was opened; a confirmed plan always has one.
        for link in (plan["url"], plan.get("commission_url")):
            assert link is None or link.startswith("https://")
        assert not plan["verified"] or plan["url"]
