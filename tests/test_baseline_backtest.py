"""Backtest harness: origins, coverage on a series with known noise, rule choice."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import (
    Rule,
    backtest_cases,
    backtest_origins,
    choose_rule,
    interval_coverage,
    rules_from_ledger,
    settings_from_ledger,
    to_panel,
    validate_baseline,
)
from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.ledger import load_ledger

PERMITS = Path(__file__).parent / "fixtures" / "testland" / "permits_county_year.csv"
LAST_5 = Rule("last_5_mean", "mean", 1, window=5)


@pytest.fixture(scope="module")
def settings():
    return settings_from_ledger(load_ledger())


def synthetic(n_counties: int, sigma: float, seed: int) -> pd.DataFrame:
    """Flat county series (100 single-unit permits a year) with lognormal yearly noise."""
    rng = np.random.default_rng(seed)
    years = np.arange(1976, 2026)
    rows = []
    for c in range(n_counties):
        noise = np.exp(rng.normal(0.0, sigma, years.size)) if sigma else np.ones(years.size)
        for y, e in zip(years, noise, strict=True):
            rows.append(
                {
                    "county": 90000 + c,
                    "year": int(y),
                    "units_1": 100 * e,
                    "units_2": 0,
                    "units_3_4": 0,
                    "units_5plus": 0,
                }
            )
    return pd.DataFrame(rows)


def test_origins_need_history_before_and_five_years_after():
    years = np.arange(1996, 2026)
    # History 10 years -> first origin 2005; lead 2, horizon 5 -> last origin 2019.
    assert backtest_origins(years, 2, 5, 10) == list(range(2005, 2020))
    assert backtest_origins(np.arange(2020, 2026), 2, 5, 10) == []


def test_backtest_case_by_hand():
    # One county, 1..12 single-unit permits in 2010-2021. Origin 2014, lead 2:
    # projected mean(2010-2014) = 3 per year -> 15; actual 2016-2020 = 7+8+9+10+11 = 45.
    years = np.arange(2010, 2022)
    units = np.zeros((1, years.size, 4))
    units[0, :, 0] = np.arange(1, 13)
    cases = backtest_cases(LAST_5, years, units, [2014], lead=2, horizon=5)
    assert cases[["projected", "actual", "ratio"]].iloc[0].tolist() == [15.0, 45.0, 3.0]


def test_interval_coverage_by_hand():
    assert interval_coverage([1, 5, 10, 3], [0, 6, 10, 1], [2, 8, 12, 2]) == 0.5
    assert np.isnan(interval_coverage([], [], []))


def test_coverage_is_one_without_noise(settings):
    skill = validate_baseline(synthetic(9, 0.0, 0), [LAST_5], None, settings)
    row = skill.iloc[0]
    assert row["coverage_80"] == 1.0 and row["mape"] == pytest.approx(0.0)
    assert row["median_abs_log_ratio"] == pytest.approx(0.0)


def test_coverage_is_near_80_percent_with_known_noise(settings):
    # Independent lognormal noise, the same for every county: an honest empirical
    # interval built from other counties' errors should hold about 80% of cases.
    skill = validate_baseline(synthetic(60, 0.3, 7), [LAST_5], None, settings)
    row = skill.iloc[0]
    assert row["cases"] == 60 * len(range(1985, 2020))
    # Ranges use earlier origins only (o' <= o - lead - horizon + 1 = o - 6), so the
    # first six origins have no range.
    assert row["coverage_cases"] == 60 * len(range(1991, 2020))
    assert 0.75 <= row["coverage_80"] <= 0.85
    # Five-year totals of a flat series with sigma 0.3 a year are typically off by
    # well under 30%; the error measures must reflect noise, not bias.
    assert 0.05 < row["median_abs_log_ratio"] < 0.3


def test_skill_table_has_every_rule_and_metric(settings):
    table = pd.read_csv(PERMITS)
    rules = rules_from_ledger(load_ledger())
    skill = validate_baseline(table, rules, None, settings)
    assert skill["rule"].tolist() == [r.name for r in rules]
    assert set(skill.columns) >= {"mape", "median_abs_log_ratio", "coverage_80", "cases"}
    assert (skill["cases"] == 4 * 15).all()
    assert skill["coverage_80"].between(0, 1).all()
    again = validate_baseline(table, rules, None, settings)
    pd.testing.assert_frame_equal(skill, again)


def test_explicit_origins_are_used(settings):
    table = pd.read_csv(PERMITS)
    skill = validate_baseline(table, [LAST_5], [2010, 2011], settings)
    assert skill.iloc[0]["cases"] == 8


def test_choose_rule_prefers_the_simplest_within_tolerance():
    skill = pd.DataFrame(
        {
            "rule": ["last_5_mean", "last_10_mean", "ewm", "damped_trend"],
            "complexity": [1, 1, 2, 3],
            "median_abs_log_ratio": [0.104, 0.120, 0.101, 0.100],
        }
    )
    assert choose_rule(skill, 0.05) == "last_5_mean"  # 0.104 <= 0.100 x 1.05
    assert choose_rule(skill, 0.02) == "ewm"  # 0.101 <= 0.102, last_5 is not
    assert choose_rule(skill, 0.0) == "damped_trend"
    with pytest.raises(ValueError, match="no rule"):
        choose_rule(skill.assign(median_abs_log_ratio=np.nan), 0.05)


def test_lead_comes_from_the_data_and_the_first_projected_year(settings):
    table = pd.read_csv(PERMITS)
    late = table.assign(year=table["year"] + 2)  # data now reach 2027
    with pytest.raises(ValueError, match="not before the first projected year"):
        validate_baseline(late, [LAST_5], None, settings)
    assert to_panel(table).years[-1] == 2025
    assert set(STRUCTURE_TYPES) <= set(table.columns)


def test_ranges_use_other_counties_at_earlier_origins_only():
    from visionhousing.baseline.backtest import leave_one_county_out

    # Three counties, origins 2000-2010. County 0's 2010 case may pool only the
    # other counties' cases with origin <= 2010 - 2 - 5 + 1 = 2004.
    rows = []
    for o in range(2000, 2011):
        for c in range(3):
            ratio = 10.0 if o > 2004 else 1.0  # later cases would widen the range a lot
            rows.append((c, o, 100.0, 100.0 * ratio, ratio))
    cases = pd.DataFrame(rows, columns=["place", "origin", "projected", "actual", "ratio"])
    classes = np.zeros(3, dtype=int)
    timed = leave_one_county_out(cases, classes, lead=2, horizon=5)
    assert timed["origin"].min() == 2006  # 2000-2005 have no earlier case
    last = timed[(timed["place"] == 0) & (timed["origin"] == 2010)].iloc[0]
    assert last["high"] == pytest.approx(100.0)  # only the ratio-1 cases of 2000-2004
    plain = leave_one_county_out(cases, classes)  # geographic split only
    assert len(plain) == len(cases)
    assert plain[(plain["place"] == 0) & (plain["origin"] == 2010)].iloc[0]["high"] > 100.0


def test_size_classes_at_an_origin_use_years_up_to_it_only():
    from visionhousing.baseline.backtest import origin_size_classes, size_classes

    # County 0 is small until 2015, then the largest.
    rows = []
    for y in range(2000, 2026):
        for c, units in enumerate([5 if y <= 2015 else 500, 50, 100]):
            rows.append(
                {"county": 90000 + c, "year": y, "units_1": units, "units_2": 0,
                 "units_3_4": 0, "units_5plus": 0}
            )  # fmt: skip
    panel = to_panel(pd.DataFrame(rows))
    assert size_classes(panel, 5, 3).tolist() == [2, 0, 1]
    at = origin_size_classes(panel, [2010, 2025], 5, 3)
    assert at[2010].tolist() == [0, 1, 2]
    assert at[2025].tolist() == [2, 0, 1]
