"""Scenarios: benchmark wording, the two Pennsylvania bills, overrides checked by the ledger."""

import pytest

from visionhousing.ledger import LedgerError, load_ledger, load_scenarios, scenario_ledger
from visionhousing.ledger.scenarios import parse_scenarios


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def scenarios(ledger):
    return load_scenarios(ledger)


def test_all_scenarios_present(scenarios):
    assert set(scenarios) == {
        "baseline",
        "adu",
        "missing_middle",
        "tod",
        "parking",
        "fees",
        "pa_hb2186",
        "pa_sb1346",
        "transit_zoning_funded_infrastructure",
        "commercial_land_housing",
        "adaptive_reuse",
        "starter_home_lots",
        "fee_deferral_with_affordability",
    }
    must = {sid for sid, s in scenarios.items() if s.priority == "must"}
    assert must == {"baseline", "adu", "parking", "fees"}


def test_benchmark_wording_carries_key_phrases(scenarios):
    assert "at least 600 square feet" in scenarios["adu"].handbook_text
    assert "no additional parking is required" in scenarios["adu"].handbook_text
    assert "$50,000 per unit" in scenarios["fees"].handbook_text
    assert "by 50%" in scenarios["fees"].handbook_text
    assert f"2{chr(0x2013)}6 dwelling units" in scenarios["missing_middle"].handbook_text
    assert "three stories" in scenarios["missing_middle"].handbook_text
    assert "developer-provided parking" in scenarios["parking"].handbook_text
    assert "major transit stops" in scenarios["tod"].handbook_text
    assert scenarios["baseline"].reforms == ()


def test_pennsylvania_bills(scenarios):
    hb, sb = scenarios["pa_hb2186"], scenarios["pa_sb1346"]
    assert hb.kind == sb.kind == "state"
    assert hb.excluded_places == ()
    assert set(hb.building_types) >= {"sf_detached", "sf_attached", "two_to_four"}
    assert sb.building_types == ("sf_detached",)
    assert {p.name for p in sb.excluded_places} == {"Philadelphia", "Pittsburgh"}
    assert sb.overrides == {"adu.utility_connection_fee": 0}
    for s in (hb, sb):
        bills = [src for src in s.source if src.id.startswith(("pa_hb", "pa_sb"))]
        assert bills and all(src.url for src in bills)


def test_sb1346_ledger_has_no_connection_fee(ledger, scenarios):
    new = scenario_ledger(ledger, scenarios["pa_sb1346"])
    assert new["adu.utility_connection_fee"].central == 0
    assert ledger["adu.utility_connection_fee"].central > 0


def test_every_override_names_a_ledger_parameter(ledger, scenarios):
    for scenario in scenarios.values():
        for pid in scenario.overrides:
            assert pid in ledger


def test_bad_override_in_scenario_is_named(ledger):
    data = {
        "scenarios": {
            "odd": {
                "title": "Odd",
                "kind": "state",
                "priority": "could",
                "description": "Test.",
                "reforms": ["adu"],
                "overrides": {"adu.no_such_thing": 1},
                "source": [{"id": "x", "citation": "y"}],
            }
        }
    }
    with pytest.raises(LedgerError, match=r"scenario 'odd'.*adu\.no_such_thing"):
        parse_scenarios(data, ledger)


def test_benchmark_needs_handbook_text_and_baseline_needs_no_reforms(ledger):
    entry = {
        "title": "T",
        "kind": "benchmark",
        "priority": "must",
        "description": "Test.",
        "source": [{"id": "x", "citation": "y"}],
    }
    with pytest.raises(LedgerError, match=r"scenario 'b'.*Handbook"):
        parse_scenarios({"scenarios": {"b": entry}}, ledger)
    base = {**entry, "kind": "baseline", "handbook_text": "x", "reforms": ["fees"]}
    with pytest.raises(LedgerError, match=r"scenario 'base'.*no reforms"):
        parse_scenarios({"scenarios": {"base": base}}, ledger)
    with pytest.raises(LedgerError, match="scenario 'r'"):
        parse_scenarios({"scenarios": {"r": {**entry, "reforms": ["rent_control"]}}}, ledger)
