"""Tests for the baseline rule contest, the out-of-time correction test and the stress settings."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import project_baseline, settings_from_ledger
from visionhousing.baseline.contest import (
    choose_pools,
    choose_winner,
    contest_report,
    contest_rules,
    origin_kinds,
    out_of_time_test,
    own_widening,
    place_info_for_context,
    pool_quantiles,
    run_contest,
    surge_years,
)
from visionhousing.baseline.panel import STRUCTURE_TYPES, to_panel
from visionhousing.baseline.places import load_place_rules
from visionhousing.baseline.rules import PlaceInfo, Rule, project_places
from visionhousing.engine.context import load_context
from visionhousing.ledger import load_ledger

YEARS = np.arange(2010, 2020)
TARGETS = np.array([2021, 2022])


def units(rows: list[list[float]]) -> np.ndarray:
    """(places, years, 1) array from one row of yearly units per place."""
    return np.array(rows, dtype=float)[:, :, np.newaxis]


# --------------------------------------------------------------------------- rule arithmetic


def test_three_year_mean_and_median_of_five():
    u = units([[0, 0, 0, 0, 0, 10, 20, 30, 40, 500]])
    last3 = Rule("last_3_mean", "mean", 1, window=3)
    med5 = Rule("median_5", "median", 1, window=5)
    a, _ = project_places(last3, YEARS, u, 2019, TARGETS, None)
    b, _ = project_places(med5, YEARS, u, 2019, TARGETS, None)
    assert a[0, 0].tolist() == pytest.approx([190.0, 190.0])  # (30 + 40 + 500) / 3
    assert b[0, 0].tolist() == pytest.approx([30.0, 30.0])  # median of 10, 20, 30, 40, 500


def test_median_skips_missing_years():
    u = units([[1, 1, 1, 1, 1, 5, np.nan, 7, np.nan, 9]])
    b, hist = project_places(Rule("m", "median", 1, window=5), YEARS, u, 2019, TARGETS, None)
    assert b[0, 0, 0] == pytest.approx(7.0)
    assert hist[0, 0]


def test_shrink_pulls_small_counties_more():
    # Two counties in one group: A builds 10 a year on 1,000 homes, B 300 on 2,000.
    u = units([[10] * 10, [300] * 10])
    info = PlaceInfo(groups=("g", "g"), source="test", homes=np.array([1000.0, 2000.0]))
    rule = Rule("shrink", "shrink", 3, window=5, half_weight=100.0)
    got, _ = project_places(rule, YEARS, u, 2019, TARGETS, info)
    group_rate = (10 + 300) / 3000
    w_a, w_b = 10 / 110, 300 / 400
    want_a = 1000 * (w_a * 10 / 1000 + (1 - w_a) * group_rate)
    want_b = 2000 * (w_b * 300 / 2000 + (1 - w_b) * group_rate)
    assert got[0, 0, 0] == pytest.approx(want_a)
    assert got[1, 0, 0] == pytest.approx(want_b)
    # A is moved further, in proportion, than B.
    assert abs(got[0, 0, 0] / 10 - 1) > abs(got[1, 0, 0] / 300 - 1)


def test_shrink_without_homes_is_the_plain_mean():
    u = units([[10] * 10, [300] * 10])
    rule = Rule("shrink", "shrink", 3, window=5, half_weight=100.0)
    got, _ = project_places(rule, YEARS, u, 2019, TARGETS, None)
    assert got[:, 0, 0].tolist() == pytest.approx([10.0, 300.0])


def test_top_down_share_is_state_mean_times_long_share():
    # A built 10 a year for five years then 30; B 90 throughout.
    u = units([[10] * 5 + [30] * 5, [90] * 10])
    rule = Rule("td", "top_down", 2, window=5, share_window=10)
    got, _ = project_places(rule, YEARS, u, 2019, TARGETS, None)
    state_mean = 30 + 90
    share_a = (10 * 5 + 30 * 5) / (10 * 5 + 30 * 5 + 900)
    assert got[0, 0, 0] == pytest.approx(state_mean * share_a)
    assert got[:, 0, 0].sum() == pytest.approx(state_mean)  # shares add to one


def test_average_is_the_mean_of_its_components():
    u = units([[1, 2, 3, 4, 5, 6, 7, 8, 9, 100]])
    parts = (Rule("a", "mean", 1, window=3), Rule("b", "median", 1, window=5))
    avg = Rule("avg", "average", 4, components=parts)
    got, _ = project_places(avg, YEARS, u, 2019, TARGETS, None)
    a = project_places(parts[0], YEARS, u, 2019, TARGETS, None)[0]
    b = project_places(parts[1], YEARS, u, 2019, TARGETS, None)[0]
    assert np.allclose(got, (a + b) / 2)


def test_contest_rules_follow_the_ledger_file():
    rules = load_place_rules()
    made = contest_rules(load_ledger(), rules)
    assert [r.name for r in made] == list(rules["contest"]["rules"])
    avg = made[-1]
    assert avg.method == "average" and len(avg.components) == len(made) - 1
    ledger = load_ledger()
    assert all(a in ledger for r in made for a in r.assumption_ids)


# --------------------------------------------------------------------------- choosing


def skill(rows):
    return pd.DataFrame(rows, columns=["rule", "complexity", "median_abs_log_ratio", "coverage_80"])


def test_choosing_rule_filters_by_band_then_error_then_simplicity():
    table = skill(
        [
            ("a", 1, 0.30, 0.79),
            ("b", 1, 0.29, 0.70),  # lowest error, coverage outside the band
            ("c", 3, 0.25, 0.80),  # best eligible error
            ("d", 1, 0.26, 0.84),  # simpler, within 5% of c
        ]
    )
    assert choose_winner(table, (0.75, 0.85), 0.05) == ("d", "in band")
    assert choose_winner(table, (0.75, 0.85), 0.0) == ("c", "in band")


def test_choosing_rule_is_deterministic_and_falls_back_to_closest_coverage():
    table = skill([("a", 1, 0.30, 0.60), ("b", 2, 0.20, 0.95), ("c", 1, 0.25, 0.70)])
    first = choose_winner(table, (0.75, 0.85), 0.05)
    assert first == ("c", "closest coverage")
    assert all(choose_winner(table, (0.75, 0.85), 0.05) == first for _ in range(5))
    tie = skill([("a", 1, 0.30, 0.80), ("b", 1, 0.30, 0.80)])
    assert choose_winner(tie, (0.75, 0.85), 0.05)[0] == "a"  # ties go to the earlier row
    with pytest.raises(ValueError, match="no rule"):
        choose_winner(table.assign(median_abs_log_ratio=np.nan), (0.75, 0.85), 0.05)


def test_pool_adoption_rule():
    def st(err, cov):
        return {"median_abs": err, "coverage": cov}

    band = (0.75, 0.85)
    assert choose_pools({"size_class": st(0.3, 0.8), "place_type": st(0.2, 0.8)}, band)[0] == (
        "place_type"
    )
    assert choose_pools({"size_class": st(0.3, 0.8), "place_type": st(0.4, 0.8)}, band)[0] == (
        "size_class"
    )
    assert choose_pools({"size_class": st(0.3, 0.8), "place_type": st(0.2, 0.6)}, band)[0] == (
        "size_class"
    )
    # Neither in the band: closer to 80% counts.
    assert choose_pools({"size_class": st(0.3, 0.5), "place_type": st(0.2, 0.6)}, band)[0] == (
        "place_type"
    )


# --------------------------------------------------------------------------- pools


def test_place_type_pool_falls_back_to_all_counties_below_the_minimum():
    ratios = pd.DataFrame({"place": [0, 0, 1, 1, 1, 2], "ratio": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]})
    keys = np.array(["x", "x", "y"], dtype=object)
    q, note = pool_quantiles(ratios, 0, keys, (0.5,), 6)
    assert q[0] == pytest.approx(3.5)  # x (places 0 and 1) has five cases, fewer than 6
    assert "pooled" in note
    q, note = pool_quantiles(ratios, 0, keys, (0.5,), 5)
    assert q[0] == pytest.approx(3.0)  # x's own five cases: 1, 2, 3, 4, 5
    assert "pooled" not in note
    q, _ = pool_quantiles(ratios, 2, keys, (0.5,), None)  # size-class method: any case will do
    assert q[0] == pytest.approx(6.0)


def test_own_widening_only_for_far_off_counties_with_enough_cases():
    far = pd.DataFrame({"place": [0] * 10, "ratio": np.linspace(2.0, 3.0, 10)})
    q = np.array([0.8, 1.0, 1.2])
    out, note = own_widening(far, 0, q, (0.1, 0.5, 0.9), 10, 0.33)
    assert out[0] == q[0] and out[2] > q[2] and out[1] == q[1] and note
    assert own_widening(far, 0, q, (0.1, 0.5, 0.9), 11, 0.33)[1] == ""  # too few cases
    near = far.assign(ratio=np.linspace(0.9, 1.1, 10))
    assert own_widening(near, 0, q, (0.1, 0.5, 0.9), 10, 0.33)[1] == ""


def test_out_of_time_test_uses_only_earlier_origins():
    rng = np.random.default_rng(1)
    rows = []
    for origin in range(2000, 2016):
        for place in range(10):
            p = 100.0
            a = p * rng.uniform(0.5, 1.5)
            rows.append((place, origin, p, a, a / p))
    cases = pd.DataFrame(rows, columns=["place", "origin", "projected", "actual", "ratio"])
    keys = {"size_class": np.zeros(10, dtype=object), "place_type": np.zeros(10, dtype=object)}
    lead, horizon = 2, 5
    base = out_of_time_test(cases, keys, lead, horizon, 10, 10, 0.33)
    target = 2012
    # Change every case that a 2012 origin may not see (origins after 2012 - 6).
    hidden = cases["origin"] > target - lead - horizon + 1
    changed = cases.copy()
    changed.loc[hidden & (changed["origin"] != target), "ratio"] *= 50.0
    after = out_of_time_test(changed, keys, lead, horizon, 10, 10, 0.33)
    cols = ["p50_size_class", "low_place_type", "high_place_type_own"]
    a = base[base["origin"] == target][cols].to_numpy()
    b = after[after["origin"] == target][cols].to_numpy()
    assert a.size and np.allclose(a, b)
    # And an earlier case does move it.
    early = changed.copy()
    early.loc[early["origin"] == 2000, "ratio"] *= 50.0
    c = out_of_time_test(early, keys, lead, horizon, 10, 10, 0.33)
    assert not np.allclose(a, c[c["origin"] == target][cols].to_numpy())
    # The first origins have no earlier cases and are not scored.
    assert base["origin"].min() == 2000 + lead + horizon - 1


# --------------------------------------------------------------------------- surges, stress


def test_surge_years():
    got = surge_years(np.array([100.0, 800.0, 90.0, 110.0, 100.0]), np.arange(2021, 2026), 3.0)
    assert got == [(2022, 800.0, 100.0)]
    assert surge_years(np.array([0.0, 5.0, 0.0]), np.arange(3), 3.0) == []  # median 0: no surge


def test_origin_kinds_compare_target_years_with_the_years_before():
    years = list(range(2000, 2016))
    level = [10] * 8 + [2] * 4 + [20] * 4  # a slump from 2008, a boom from 2012
    table = pd.DataFrame({"county": "1", "year": years, **{t: level for t in STRUCTURE_TYPES}})
    kinds = origin_kinds(to_panel(table), [2004, 2005, 2009], 2, 5)
    assert kinds == {2004: "slump", 2005: "slump", 2009: "boom"}


# --------------------------------------------------------------------------- Testland


@pytest.fixture(scope="module")
def tl():
    return load_context("TL")


def test_contest_on_testland_is_deterministic_and_marks_one_winner(tl):
    rules = load_place_rules()
    info = place_info_for_context(tl)
    a = run_contest(tl.permits, tl.ledger, rules, info)
    b = run_contest(tl.permits.sample(frac=1, random_state=2), tl.ledger, rules, info)
    assert a.winner == b.winner
    assert int(a.skill["chosen"].sum()) == 1
    assert a.skill.loc[a.skill["chosen"], "rule"].item() == a.winner
    assert set(a.skill["rule"]) == set(rules["contest"]["rules"])
    assert a.pools in ("size_class", "place_type")


def test_place_type_pools_fall_back_and_say_so(tl):
    settings = settings_from_ledger(tl.ledger)
    rule = Rule(
        "last_5_mean", "mean", 1, window=5, assumption_ids=("baseline.recent_window_years",)
    )
    groups = ("a", "a", "b", "b")[: len(to_panel(tl.permits).counties)]
    info = PlaceInfo(groups=groups, source="test")
    calib = {"min_group_cases": 10_000, "min_own_cases": 10_000}
    res = project_baseline(
        tl.permits, rule, settings, info=info, pools="place_type", calibration=calib
    )
    assert "place_type" in res.multipliers.columns
    assert all("so all counties' ratios" in r.formula for r in res.counties.values() if r.p50)
    for r in res.counties.values():
        assert r.p10 <= r.p50 <= r.p90
    with pytest.raises(ValueError, match="pools"):
        project_baseline(tl.permits, rule, settings, pools="by_magic")


def test_contest_report_is_json_and_holds_the_parts(tl):
    rules = load_place_rules()
    info = place_info_for_context(tl)
    c = run_contest(tl.permits, tl.ledger, rules, info)
    rep = contest_report(c, tl.permits, tl.ledger, rules, state="TL", tracts=tl.tracts)
    json.dumps(rep, allow_nan=False)
    assert rep["winner"] == c.winner
    assert "before any score was computed" in rep["choosing_rule"]
    assert len(rep["rules"]) == len(rules["contest"]["rules"])
    assert rep["by_group"][0]["dimension"] == "all"
    assert {"none", "size_class", "place_type", "place_type_own"} <= set(rep["by_group"][0])
    assert [s["kind"] for s in rep["stress"]] == list(rules["stress"])
    for s in rep["stress"]:
        if s["state"]:
            assert s["state"]["p10"] <= s["state"]["p50"] <= s["state"]["p90"]
    assert rep["findings"] and all(isinstance(x, str) for x in rep["findings"])


def test_validation_payload_carries_the_contest(tl):
    from visionhousing.api.service import validation_table

    v = validation_table(tl)
    json.dumps(v, allow_nan=False)
    assert v["contest"]["winner"] == v["chosen_rule"]
    assert sum(bool(r["chosen"]) for r in v["rows"]) == 1
