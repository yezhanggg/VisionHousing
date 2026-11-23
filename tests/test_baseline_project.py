"""Baseline projection on Testland: ranges ordered, counties add up, zero is explained."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import (
    CYCLES,
    Rule,
    baseline_from_ledger,
    project_baseline,
    rules_from_ledger,
    settings_from_ledger,
)
from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.engine.records import Record
from visionhousing.ledger import load_ledger

PERMITS = Path(__file__).parent / "fixtures" / "testland" / "permits_county_year.csv"


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def settings(ledger):
    return settings_from_ledger(ledger)


@pytest.fixture(scope="module")
def table():
    return pd.read_csv(PERMITS)


def with_empty_county(table: pd.DataFrame) -> pd.DataFrame:
    empty = table[table["county"] == 99007].assign(
        county=99009, **dict.fromkeys(STRUCTURE_TYPES, 0)
    )
    return pd.concat([table, empty], ignore_index=True)


@pytest.mark.parametrize("cycle", CYCLES)
def test_every_rule_gives_ordered_ranges_that_add_up(table, ledger, settings, cycle):
    for rule in rules_from_ledger(ledger):
        result = project_baseline(table, rule, settings, cycle)
        records = [*result.counties.values(), result.state]
        for r in records:
            assert r.p10 <= r.p50 <= r.p90, (rule.name, r.name)
            assert r.p10 >= 0
        assert sum(r.p50 for r in result.counties.values()) == pytest.approx(
            result.state.p50, rel=1e-12
        )


def test_records_carry_formula_ids_sources_and_tier_0(table, ledger, settings):
    result = project_baseline(table, rules_from_ledger(ledger)[0], settings)
    assert set(result.counties) == {"99001", "99003", "99005", "99007"}
    for r in [*result.counties.values(), result.state]:
        assert r.is_range and r.tier == 0 and r.unit == "units"
        assert r.source_ids == ("bps_county_annual",)
        assert "baseline.cycle_quantile_typical" in r.assumption_ids
        assert "baseline.recent_window_years" in r.assumption_ids
        assert all(a in ledger for a in r.assumption_ids)
        assert "2027-2031" in r.name and r.formula
        assert Record.from_json(r.to_json()) == r
    assert "state totals" in result.state.formula
    assert "sum of county P50s" in result.state.formula


def test_point_projection_matches_the_annual_table(table, ledger, settings):
    rule = Rule(
        "last_5_mean", "mean", 1, window=5, assumption_ids=("baseline.recent_window_years",)
    )
    result = project_baseline(table, rule, settings)
    recent = table[(table["county"] == 99001) & table["year"].between(2021, 2025)]
    by_hand = 5 * recent[list(STRUCTURE_TYPES)].mean().sum()
    annual = result.annual[result.annual["county"] == "99001"]
    assert annual["units"].sum() == pytest.approx(by_hand)
    assert sorted(annual["year"].unique()) == [2027, 2028, 2029, 2030, 2031]
    inputs_total = sum(i.value for i in result.counties["99001"].inputs)
    assert inputs_total == pytest.approx(by_hand)


def test_county_range_is_point_times_class_multipliers(table, ledger, settings):
    rule = rules_from_ledger(ledger)[0]
    result = project_baseline(table, rule, settings)
    point = result.annual.groupby("county")["units"].sum()
    m = result.multipliers.set_index("size_class")
    # Prairie (99007) has the fewest recent permits: small class.
    r = result.counties["99007"]
    assert r.p10 == pytest.approx(point["99007"] * m.loc["small", "q_low"])
    assert r.p50 == pytest.approx(point["99007"] * m.loc["small", "q_cycle"])
    assert r.p90 == pytest.approx(point["99007"] * m.loc["small", "q_high"])
    assert m["counties"].sum() == 4


def test_cycle_moves_the_center_up_from_weak_to_strong(table, ledger, settings):
    rule = rules_from_ledger(ledger)[0]
    p50 = [project_baseline(table, rule, settings, c).state.p50 for c in CYCLES]
    assert p50[0] <= p50[1] <= p50[2]
    with pytest.raises(ValueError, match="cycle"):
        project_baseline(table, rule, settings, "booming")


def test_same_input_gives_same_output(table, ledger):
    a, skill_a = baseline_from_ledger(table, ledger)
    b, skill_b = baseline_from_ledger(table.sample(frac=1, random_state=3), ledger)
    assert a.state.to_json() == b.state.to_json()
    assert {k: v.to_json() for k, v in a.counties.items()} == {
        k: v.to_json() for k, v in b.counties.items()
    }
    pd.testing.assert_frame_equal(skill_a, skill_b)


def test_zero_history_gives_zero_with_a_reason(table, ledger, settings):
    result = project_baseline(with_empty_county(table), rules_from_ledger(ledger)[0], settings)
    r = result.counties["99009"]
    assert (r.p10, r.p50, r.p90) == (0.0, 0.0, 0.0)
    assert "zero" in r.formula and "so the projection is zero" in r.formula
    assert sum(x.p50 for x in result.counties.values()) == pytest.approx(result.state.p50)


def test_no_records_in_the_window_gives_zero_with_a_reason(table, ledger, settings):
    old = table[table["county"] == 99007].assign(county=99011)
    old = old[old["year"] < 2005]
    result = project_baseline(pd.concat([table, old]), rules_from_ledger(ledger)[0], settings)
    r = result.counties["99011"]
    assert r.p50 == 0.0 and "no permit records" in r.formula


def test_a_state_with_only_zeros_is_zero(table, ledger, settings):
    zeros = table.assign(**dict.fromkeys(STRUCTURE_TYPES, 0))
    result = project_baseline(zeros, rules_from_ledger(ledger)[0], settings)
    assert (result.state.p10, result.state.p50, result.state.p90) == (0.0, 0.0, 0.0)
    assert "every county projects zero" in result.state.formula
    assert np.isnan(result.multipliers["q_cycle"]).all()


def test_baseline_from_ledger_picks_a_rule_and_marks_it(table, ledger):
    result, skill = baseline_from_ledger(table, ledger)
    assert skill["chosen"].sum() == 1
    assert skill.loc[skill["chosen"], "rule"].item() == result.rule
    assert "baseline.selection_tolerance" in result.state.assumption_ids
