"""Tests for the out-of-time calibrated baseline ranges (visionhousing.baseline.calibrate)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
import yaml

from visionhousing.baseline import calibrate as cal
from visionhousing.baseline.places import load_place_rules
from visionhousing.validation.preregistration import (
    PreregistrationError,
    fingerprint,
    load_registry,
    verify,
)

BLOCK = {
    "levels": [0.1, 0.9],
    "min_pool_cases": 3,
    "min_past_cases": 4,
    "own_widening": False,
    "contain_p50": True,
    "coverage_band": [0.75, 0.85],
    "min_state_coverage": 0.70,
    "states": ["AA", "BB"],
    "state_check_min_past_cases": 3,
}


def test_fingerprint_is_pinned_and_recorded_before_scoring():
    rules = load_place_rules()
    reg = load_registry()
    entry = reg["blocks"]["oot_calibration"]
    assert entry["sha256"] == "db14162cf48cb3d740da26617da54e72147aa5dec122c5cec3f7f8b8fd0cba34"
    assert entry["logged_before_first_score"] is True
    assert verify("oot_calibration", rules)["verified"]
    changed = {**rules, "oot_calibration": {**rules["oot_calibration"], "min_past_cases": 31}}
    assert fingerprint(changed["oot_calibration"]) != entry["sha256"]
    with pytest.raises(PreregistrationError):
        verify("oot_calibration", changed)


def test_study_refuses_a_changed_block():
    rules = load_place_rules()
    bad = {**rules, "oot_calibration": {**rules["oot_calibration"], "min_pool_cases": 5}}
    with pytest.raises(PreregistrationError):
        cal.run_study(["PA"], rules=bad, inputs={})


def test_conformal_ends_use_finite_sample_ranks():
    r = np.arange(1, 10, dtype=float)  # n = 9: k_lo = floor(1.0) = 1, k_hi = ceil(9.0) = 9
    assert cal.conformal_ends(r) == (1.0, 9.0)
    r = np.arange(1, 20, dtype=float)  # n = 19: k_lo = 2, k_hi = 18
    assert cal.conformal_ends(r) == (2.0, 18.0)
    r = np.array([5.0, 1.0, 3.0])  # n = 3: k_lo = 1, k_hi = min(3, 4) = 3
    assert cal.conformal_ends(r) == (1.0, 5.0)
    assert cal.conformal_ends(np.array([np.nan])) is None
    # unordered input and the log scale give the same ranks
    r = np.random.default_rng(0).lognormal(size=99)
    lo, hi = cal.conformal_ends(r)
    llo, lhi = cal.conformal_ends(np.log(r))
    assert math.isclose(math.log(lo), llo) and math.isclose(math.log(hi), lhi)


def test_is_past_needs_all_target_years_observed():
    o = np.array([2000, 2006, 2007])
    assert cal.is_past(o, 2012, lead=2, horizon=5).tolist() == [True, True, False]


def _cases(rows):
    return pd.DataFrame(rows, columns=["place", "origin", "projected", "actual", "ratio"])


def test_calibration_uses_only_past_cases():
    """Leakage: changing a case that ends after the scored origin changes nothing."""
    rng = np.random.default_rng(1)
    rows = []
    for o in range(2000, 2016):
        for p in range(4):
            r = float(rng.lognormal(0, 0.3))
            rows.append((p, o, 100.0, 100.0 * r, r))
    cases = _cases(rows)
    classes = {o: np.zeros(4, dtype=int) for o in range(2000, 2016)}
    base = cal.calibrated_cases(cases, classes, 2, 5, BLOCK)
    # origin 2012 may use origins <= 2006 only; spoil every later case
    spoiled = cases.copy()
    late = spoiled["origin"] > 2006
    spoiled.loc[late & (spoiled["origin"] != 2012), "ratio"] = 50.0
    spoiled.loc[late & (spoiled["origin"] != 2012), "actual"] = 5000.0
    again = cal.calibrated_cases(spoiled, classes, 2, 5, BLOCK)
    a = base[base["origin"] == 2012].reset_index(drop=True)
    b = again[again["origin"] == 2012].reset_index(drop=True)
    assert len(a) == 4
    pd.testing.assert_frame_equal(a[["low", "high"]], b[["low", "high"]])
    # the earliest scored origin is the first with min_past_cases past cases
    assert int(base["origin"].min()) == 2006  # past: origin 2000 (4 cases)


def test_pool_by_class_at_case_origin_with_fallback():
    past = pd.DataFrame(
        {
            "place": [0, 1, 2, 3, 4],
            "ratio": [1.0, 2.0, 3.0, 10.0, 20.0],
            "size_class": [0, 0, 0, 1, 1],
        }
    )
    ends, pool, n = cal.pool_ends(past, 0, 0, BLOCK)
    assert pool == "size class" and n == 3 and ends == (1.0, 3.0)
    ends, pool, n = cal.pool_ends(past, 3, 1, BLOCK)  # 2 < 3 cases: all pooled
    assert pool == "all" and n == 5 and ends == (1.0, 20.0)


def test_range_always_holds_the_projection():
    rows = [(p, o, 100.0, 300.0, 3.0) for o in range(2000, 2010) for p in range(3)]
    classes = {o: np.zeros(3, dtype=int) for o in range(2000, 2010)}
    out = cal.calibrated_cases(_cases(rows), classes, 2, 5, BLOCK)
    assert (out["low"] <= out["p50"]).all() and (out["high"] >= out["p50"]).all()
    assert (out["low"] == out["p50"]).all()  # every past case ran 3x high: P10 clamped


def test_coverage_helper():
    scored = pd.DataFrame(
        {
            "origin": [2010, 2010, 2011, 2011],
            "actual": [100.0, 50.0, 200.0, 100.0],
            "p50": [100.0, 100.0, 100.0, 100.0],
            "low": [80.0, 80.0, 80.0, 100.0],
            "high": [120.0, 120.0, 120.0, 140.0],
        }
    )
    st = cal.coverage_stats(scored)
    assert st["cases"] == 4 and st["covered"] == 2 and st["coverage"] == 0.5
    assert math.isclose(st["width"], 0.4)
    assert math.isclose(st["median_abs_error"], np.median([0, math.log(2), math.log(2), 0]))
    assert st["origins"] == [2010, 2011]
    assert cal.coverage_stats(scored.iloc[:0])["cases"] == 0


@pytest.mark.parametrize(
    ("a", "b", "outcome", "reason"),
    [
        ((80, 100), (16, 20), "adopt", "in_band"),
        ((70, 100), (14, 20), "keep_current", "under_band"),
        ((90, 100), (18, 20), "keep_current", "over_band"),
        ((85, 100), (13, 20), "keep_current", "state_short"),  # 98/120 = 81.7%; BB 65%
        ((75, 100), (15, 20), "adopt", "in_band"),  # 90/120 = 75% exactly: edge counts
    ],
)
def test_adoption_branches(a, b, outcome, reason):
    per = {
        "AA": {"cases": a[1], "covered": a[0], "coverage": a[0] / a[1]},
        "BB": {"cases": b[1], "covered": b[0], "coverage": b[0] / b[1]},
    }
    pooled = (a[0] + b[0]) / (a[1] + b[1])
    d = cal.adoption_decision(per, BLOCK)
    assert (d["outcome"], d["reason"]) == (outcome, reason)
    assert math.isclose(d["pooled_coverage"], pooled)


def test_adoption_edges_and_missing_state():
    per = {
        "AA": {"cases": 100, "covered": 85, "coverage": 0.85},
        "BB": {"cases": 20, "covered": 17, "coverage": 0.85},
    }
    assert cal.adoption_decision(per, BLOCK)["outcome"] == "adopt"  # 85% edge counts
    per["BB"] = {"cases": 0, "covered": 0, "coverage": float("nan")}
    d = cal.adoption_decision(per, BLOCK)
    assert (d["outcome"], d["reason"], d["short_states"]) == ("keep_current", "state_short",
                                                              ["BB"])  # fmt: skip


def test_engine_reads_only_an_adopted_decision(tmp_path):
    rules = {"oot_calibration": {"results": "x.yaml", "levels": [0.1, 0.9]}}
    path = tmp_path / "r.yaml"
    assert cal.calibration_for(rules, path) is None  # no file
    path.write_text(yaml.safe_dump({"decision": {"outcome": "keep_current"}}))
    assert cal.calibration_for(rules, path) is None
    path.write_text(yaml.safe_dump({"decision": {"outcome": "adopt"}}))
    got = cal.calibration_for(rules, path)
    assert got is not None and got["levels"] == [0.1, 0.9]
    assert cal.calibration_for({}, path) is None
