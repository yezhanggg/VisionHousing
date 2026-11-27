"""Massachusetts ADU rights (c. 40A §§ 1A, 3) and the ADU survey as observed context.

No network and no built pack: the statute's terms come from the shipped
``assumptions/law/statutory_rights.yaml``, the dates from
``assumptions/law/states/ma.yaml``; tract inputs are small made-up tables.
One test at the end runs the real Massachusetts pack when it is present.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.sources import ma_intake as ma_src
from visionhousing.features import ma_intake as fx
from visionhousing.law import ma_adu
from visionhousing.law.ma_adu import MaLot, benchmark_gain, ma_adu_right, protected_size_sqft
from visionhousing.law.rights import load_rights
from visionhousing.ledger import apply_overrides, load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios.adu import (
    adu_funnel,
    load_fit_table,
    load_legal_share,
    scope_from_scenario,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "ma"
ON = date(2027, 7, 1)
SIZE = 600.0
TERMS = load_rights()["provisions"]["ma_40a_3_adu"]["versions"]["ma_adu_by_right_effective"]["adu"]


def lot(**kw) -> MaLot:
    base = dict(
        primary_sqft=1_600.0,
        single_family_district=True,
        station_miles=0.3,
        in_boston=False,
        town_requires_space=True,
    )
    return MaLot(**{**base, **kw})


# --------------------------------------------------------------------------- size floor


@pytest.mark.parametrize(
    ("primary", "cap"), [(1_000, 500), (1_199, 599.5), (1_200, 600), (1_600, 800), (2_400, 900)]
)
def test_size_cap_is_the_smaller_of_half_the_house_and_900(primary, cap):
    assert protected_size_sqft(primary, TERMS) == pytest.approx(cap)


def test_a_1000_sqft_house_gives_500_so_the_benchmark_adds_the_size_right():
    gain = benchmark_gain(lot(primary_sqft=1_000.0), ON, min_size_sqft=SIZE)
    assert gain.kind == "full" and gain.gaps == ("size",)
    assert gain.right.size_cap_sqft == 500


def test_a_1600_sqft_house_near_a_station_already_has_the_benchmark():
    gain = benchmark_gain(lot(primary_sqft=1_600.0, station_miles=0.2), ON, min_size_sqft=SIZE)
    assert gain.kind == "none" and gain.right.size_cap_sqft == 800


def test_a_town_that_already_allows_the_size_closes_the_size_gap():
    small = lot(primary_sqft=1_000.0, town_allows_size_sqft=900.0)
    assert benchmark_gain(small, ON, min_size_sqft=SIZE).kind == "none"


def test_unknown_house_size_stays_unknown():
    assert benchmark_gain(lot(primary_sqft=None), ON, min_size_sqft=SIZE).kind is None


# --------------------------------------------------------------------------- parking


def test_no_added_space_within_half_a_mile_of_a_listed_station():
    near = ma_adu_right(lot(station_miles=0.5), ON)
    far = ma_adu_right(lot(station_miles=0.51), ON)
    assert near.max_added_spaces == 0 and far.max_added_spaces == 1
    assert near.owner_occupancy_required is False


def test_beyond_the_station_distance_the_one_space_is_the_only_gap():
    gain = benchmark_gain(lot(station_miles=2.0), ON, min_size_sqft=SIZE)
    assert gain.kind == "conditions" and gain.gaps == ("parking",)
    free = benchmark_gain(lot(station_miles=2.0, town_requires_space=False), ON, min_size_sqft=SIZE)
    assert free.kind == "none"
    unknown = benchmark_gain(
        lot(station_miles=2.0, town_requires_space=None), ON, min_size_sqft=SIZE
    )
    assert unknown.kind is None


def test_only_rail_subway_and_ferry_stops_count_as_listed_stations():
    tracts = pd.DataFrame({"geoid": ["T1", "T2"]})
    blocks = pd.DataFrame(
        {
            "tract": ["T1", "T1", "T2"],
            "housing_units": [30, 10, 50],
            "lat": [42.0, 42.05, 42.3],
            "lon": [-71.0, -71.0, -71.3],
        }
    )
    stops = pd.DataFrame(
        {"lat": [42.001, 42.3], "lon": [-71.0, -71.3], "route_types": ["2;3", "3"]}
    )
    share = ma_adu.station_shares(tracts, blocks, stops, route_types=[0, 1, 2, 4], miles=0.5)
    # T1: the 30 homes by the commuter rail stop count, the 10 three miles away do not;
    # T2's only stop is a bus stop.
    assert share == pytest.approx([0.75, 0.0])


# --------------------------------------------------------------------------- scope and dates


def test_boston_is_outside_chapter_40a():
    gain = benchmark_gain(lot(in_boston=True), ON, min_size_sqft=SIZE)
    assert gain.kind == "full" and gain.gaps == ("boston",)
    assert ma_adu_right(lot(in_boston=True), ON).protected is False
    assert "boston" in TERMS["excluded_municipalities"]


def test_outside_a_single_family_district_the_state_right_does_not_reach():
    gain = benchmark_gain(lot(single_family_district=False), ON, min_size_sqft=SIZE)
    assert gain.kind == "full" and gain.gaps == ("district",)


def test_before_february_2_2025_no_state_right_is_in_force():
    before = ma_adu_right(lot(), date(2025, 2, 1))
    after = ma_adu_right(lot(), date(2025, 2, 2))
    assert before.version == "" and before.protected is False
    assert after.version == "ma_adu_by_right_effective" and after.protected is True


def test_boston_share_comes_from_the_tract_to_town_table():
    tracts = pd.DataFrame({"geoid": ["25025000100", "25025160100", "25017000100"]})
    munis = pd.DataFrame(
        {
            "tract": ["25025000100", "25025160100", "25025160100", "25017000100"],
            "county": ["25025", "25025", "25025", "25017"],
            "cousub": ["07000", "13205", "07000", "00380"],
            "housing_units_2020": ["900", "300", "100", "500"],
        }
    )
    got = ma_adu.boston_shares(tracts, munis, county="25025", cousub="07000")
    assert got == pytest.approx([1.0, 0.25, 0.0])
    assert ma_adu.boston_shares(tracts, None, county="25025", cousub="07000") is None


# --------------------------------------------------------------------------- the run rule


def _state(tracts, blocks, stops):
    return SimpleNamespace(state="MA", tracts=tracts, blocks=blocks, stops=stops, derived={},
                           source="")  # fmt: skip


def test_tract_rule_is_the_lot_test_averaged_over_the_lot_mix(monkeypatch):
    monkeypatch.setattr(
        "visionhousing.engine.place_scope.read_tract_municipalities", lambda state: None
    )
    tracts = pd.DataFrame({"geoid": ["25017000100", "25025000100"], "county": ["25017", "25025"]})
    blocks = pd.DataFrame(
        {"tract": ["25017000100", "25025000100"], "housing_units": [10, 10],
         "lat": [42.0, 42.3], "lon": [-71.0, -71.3]}
    )  # fmt: skip
    stops = pd.DataFrame({"lat": [42.0], "lon": [-71.0], "route_types": ["1"]})
    ledger = load_ledger()
    rule = ma_adu.state_floor_rule(_state(tracts, blocks, stops), ledger, year=2027)
    assert rule.near == pytest.approx([1.0, 0.0]) and rule.boston is None
    d, u, w, q, c, b = 0.8, 0.2, 0.25, 0.5, 0.7, 0.6
    led = rule.ledger
    col = {
        led["single_family_district_share"]: np.array([[d]]),
        led["primary_below_floor_share"]: np.array([[u]]),
        led["local_size_allows_benchmark_share"]: np.array([[w]]),
        led["town_requires_space_share"]: np.array([[q]]),
        led["parking_only_exposure"]: np.array([[c]]),
        led["boston_share_without_table"]: np.array([[b]]),
    }
    got = rule.share(col)[0]
    size_ok = (1 - u) + u * w
    # Tract 1: near a subway stop, no Boston: legal = d x size_ok.
    # Tract 2 (Suffolk): far from stations, Boston share b from the fallback entry.
    assert got[0] == pytest.approx(d * size_ok)
    assert got[1] == pytest.approx((1 - b) * d * size_ok * (1 - q * c))
    # The same answer lot by lot: near a station with the size covered, nothing new.
    near = MaLot(1_600.0, True, 0.1, False, True)
    assert benchmark_gain(near, ON, min_size_sqft=SIZE).kind == "none"


def test_size_parameter_must_match_the_benchmark_size():
    tracts = pd.DataFrame({"geoid": ["25017000100"], "county": ["25017"]})
    ledger = apply_overrides(load_ledger(), {"adu.min_size_sqft": {"central": 700, "low": 700,
                                                                   "high": 700}})  # fmt: skip
    with pytest.raises(ValueError, match="1200"):
        ma_adu.state_floor_rule(_state(tracts, None, None), ledger, year=2027)


def test_the_rule_is_massachusetts_only():
    tracts = pd.DataFrame({"geoid": ["42001030101"], "county": ["42001"]})
    state = SimpleNamespace(state="PA", tracts=tracts, blocks=None, stops=None, derived={})
    with pytest.raises(ValueError, match="does not apply to PA"):
        ma_adu.state_floor_rule(state, load_ledger(), year=2027)


def test_funnel_legal_rule_replaces_the_statewide_range():
    ledger = load_ledger()
    scenario = load_scenarios(ledger)["adu"]
    tracts = pd.read_csv(ROOT / "tests/fixtures/testland/tracts.csv",
                         dtype={"geoid": str, "county": str})  # fmt: skip
    scope = scope_from_scenario("adu", scenario, load_legal_share())
    fixed = apply_overrides(
        scenario_ledger(ledger, scenario),
        {"adu.already_legal_share": {"central": 0.3, "low": 0.3, "high": 0.3, "shape": "fixed"}},
    )

    class Flat:
        ids = ("adu.conditions_exposure_share",)

        def share(self, col):
            return np.full((300, len(tracts)), 0.3)

    a = adu_funnel(tracts, fixed, load_fit_table(), scope, draws=300, seed=3)
    b = adu_funnel(tracts, scenario_ledger(ledger, scenario), load_fit_table(), scope,
                   draws=300, seed=3, legal_rule=Flat())  # fmt: skip
    assert np.allclose(a.stages["eligible"], b.stages["eligible"])
    assert "already_legal_share_state_floor" in b.records
    assert "already_legal_share_state_floor" not in a.records
    assert "adu.already_legal_share" not in b.records["net"].assumption_ids


# --------------------------------------------------------------------------- the survey


def _survey_table() -> pd.DataFrame:
    parsed = ma_src.parse_adu_survey((FIX / "adu_survey.csv").read_bytes())
    subs = pd.DataFrame(
        {
            "county": ["25023", "25017", "25013", "25003", "25023"],
            "cousub": ["00170", "00380", "00840", "00555", "11525"],
            "name": ["Abington town", "Acton town", "Agawam Town city", "Adams town",
                     "Carver town"],
            "population": [1, 1, 1, 1, 1],
        }
    )  # fmt: skip
    table, _ = fx.adu_survey_table(parsed, fx.muni_lookup(subs), as_of="2026-08-12",
                                   route="hand_saved")  # fmt: skip
    return table


def test_survey_summary_keeps_both_vintages_and_counts_answering_towns():
    table = _survey_table()
    periods = fx.survey_summary(table, municipalities=351)
    cohorts = [p["cohort"] for p in periods]
    assert {"2025 first half", "2025 full year", "2026 first half"} <= set(cohorts)
    assert cohorts == sorted(cohorts, key=lambda c: (c[:4], "full" in c))
    assert sum(p["approvals"] for p in periods) == int(table["approvals"].sum())
    for p in periods:
        part = table[table["cohort"] == p["cohort"]]
        assert p["towns"] == part["muni_id"].nunique() and p["towns_of"] == 351


def test_survey_context_is_observed_text_never_a_rate():
    got = fx.survey_context(_survey_table())
    assert got["state"] == "observed"
    assert "approved" in got["text"] and "of 351 towns" in got["text"]
    assert "per" not in got["text"] and "rate" in got["note"]
    county = fx.survey_context(_survey_table(), county="25013")
    assert county["periods"] and all("towns_of" not in p for p in county["periods"])


def test_survey_absent_says_not_supplied():
    got = fx.survey_context(None)
    assert got["state"] == "not_supplied" and got["text"].startswith("not supplied")
    assert fx.read_survey(Path("/nonexistent")) is None


def test_plausibility_context_is_massachusetts_only():
    from visionhousing.validation.plausibility import _observed_context

    assert _observed_context("PA", None) is None


# --------------------------------------------------------------------------- real pack


@pytest.mark.realdata
def test_massachusetts_variant_counts_more_than_the_benchmark_and_keeps_it():
    pack = ROOT / "data" / "packs" / "MA" / "tracts.csv.gz"
    if not pack.is_file():
        pytest.skip("no Massachusetts pack")
    from visionhousing.engine.core import run

    bench = run("MA", {"adu": {}}, seed=0, draws=400, use_cache=False)
    var = run("MA", {"adu": {"variant": "ma_adu_state_floor"}}, seed=0, draws=400,
              use_cache=False)  # fmt: skip
    b, v = bench.reforms["adu"], var.reforms["adu"]
    assert "already_legal_share_state_floor" not in b.result.records
    share = v.result.records["already_legal_share_state_floor"]
    assert 0.0 < share.p10 <= share.p50 <= share.p90 < 1.0
    assert v.added.p50 != b.added.p50
