"""Baseline rules: each candidate rule reproduces a hand calculation."""

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import Rule, project, rules_from_ledger, to_panel
from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.ledger import load_ledger

YEARS = np.arange(2016, 2026)
TARGETS = np.arange(2027, 2032)


def run(rule, values, years=YEARS, origin=2025, targets=TARGETS):
    proj, hist = project(rule, years, np.asarray([values], dtype=float), origin, targets)
    return proj[0], bool(hist[0])


def test_last_5_mean_by_hand():
    # 2021-2025 hold 6, 7, 8, 9, 10: mean 8 in every target year, 40 over five years.
    proj, hist = run(Rule("last_5_mean", "mean", 1, window=5), list(range(1, 11)))
    assert hist and proj.tolist() == [8.0] * 5 and proj.sum() == 40.0


def test_last_10_mean_by_hand():
    proj, _ = run(Rule("last_10_mean", "mean", 1, window=10), list(range(1, 11)))
    assert proj.tolist() == [5.5] * 5


def test_ewm_by_hand():
    # alpha 0.5 on 1, 2, 4 (latest last): (0.125x1 + 0.25x2 + 0.5x4) / 0.875 = 3.
    rule = Rule("ewm", "ewm", 2, alpha=0.5)
    proj, _ = run(rule, [1, 2, 4], years=np.arange(2023, 2026))
    assert proj == pytest.approx([3.0] * 5)


def test_damped_trend_by_hand():
    # 10..50 over 2021-2025: mean 30, middle year 2023, slope 10 a year.
    # Damping 0.5: 2027 -> 30 + 0.5 x 10 x 4 = 50; each later year adds 5.
    values = [10, 20, 30, 40, 50]
    years = np.arange(2021, 2026)
    proj, _ = run(Rule("dt", "damped_trend", 3, window=5, damping=0.5), values, years)
    assert proj == pytest.approx([50, 55, 60, 65, 70])
    flat, _ = run(Rule("dt0", "damped_trend", 3, window=5, damping=0.0), values, years)
    assert flat == pytest.approx([30] * 5)


def test_damped_trend_never_goes_below_zero():
    # Falling 50..10, full trend: 2031 would be 30 - 10 x 8 = -50, floored at 0.
    rule = Rule("dt", "damped_trend", 3, window=5, damping=1.0)
    proj, _ = run(rule, [50, 40, 30, 20, 10], years=np.arange(2021, 2026))
    assert proj[-1] == 0.0 and (proj >= 0).all()


def test_missing_years_are_skipped_not_zero():
    values = [np.nan] * 5 + [6, 7, np.nan, 9, 10]
    proj, _ = run(Rule("m", "mean", 1, window=5), values)
    assert proj[0] == pytest.approx(8.0)


def test_rule_ignores_years_after_the_origin():
    values = list(range(1, 11))
    proj, _ = run(
        Rule("m", "mean", 1, window=5), values, origin=2020, targets=np.arange(2022, 2027)
    )
    assert proj[0] == pytest.approx(3.0)  # 2016-2020 hold 1..5


def test_no_history_gives_zero_and_a_flag():
    proj, hist = run(Rule("m", "mean", 1, window=5), [np.nan] * 10)
    assert not hist and proj.tolist() == [0.0] * 5


def test_bad_rules_are_refused():
    with pytest.raises(ValueError, match="window"):
        Rule("m", "mean", 1)
    with pytest.raises(ValueError, match="alpha"):
        Rule("e", "ewm", 2, alpha=0.0)
    with pytest.raises(ValueError, match="damping"):
        Rule("d", "damped_trend", 3, window=5, damping=1.5)
    with pytest.raises(ValueError, match="after the origin"):
        run(Rule("m", "mean", 1, window=5), list(range(10)), targets=np.array([2025]))


def test_rules_from_ledger_are_the_four_candidates():
    rules = rules_from_ledger(load_ledger())
    assert [r.name for r in rules] == ["last_5_mean", "last_10_mean", "ewm", "damped_trend"]
    assert [r.complexity for r in rules] == [1, 1, 2, 3]
    ledger = load_ledger()
    for r in rules:
        assert r.assumption_ids and all(a in ledger for a in r.assumption_ids)


def test_panel_marks_missing_years_and_checks_the_table():
    table = pd.DataFrame(
        {"county": [1, 1], "year": [2020, 2022], **{t: [1, 2] for t in STRUCTURE_TYPES}}
    )
    panel = to_panel(table)
    assert panel.years.tolist() == [2020, 2021, 2022]
    assert np.isnan(panel.units[0, 1]).all() and np.isnan(panel.totals()[0, 1])
    with pytest.raises(ValueError, match="missing columns"):
        to_panel(table.drop(columns="units_2"))
    with pytest.raises(ValueError, match="more than once"):
        to_panel(pd.concat([table, table]))
    with pytest.raises(ValueError, match="negative"):
        to_panel(table.assign(units_1=[-1, 2]))
