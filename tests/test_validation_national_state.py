"""Tests for the national state-total calibration (visionhousing.validation.national_state)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
import yaml

from visionhousing.baseline.places import load_place_rules
from visionhousing.validation import national_state as ns
from visionhousing.validation.preregistration import PreregistrationError, load_registry, verify

BLOCK = {
    "levels": [0.1, 0.9],
    "min_past_origins": 2,
    "contain_p50": True,
    "coverage_band": [0.75, 0.85],
    "min_state_coverage": 0.70,
    "states_named": ["AA", "BB"],
    "variants": ["pooled", "shock"],
}


def test_fingerprint_is_pinned_and_recorded_before_scoring():
    rules = load_place_rules()
    entry = load_registry()["blocks"]["national_state_calibration"]
    assert entry["sha256"] == "9c74cf2d20535af5f95f9bb0c3897bcd16dc0509d72ad0671b5459fb0ca4819e"
    assert entry["logged_before_first_score"] is True
    assert verify("national_state_calibration", rules)["verified"]
    bad = {**rules, "national_state_calibration": {**rules["national_state_calibration"],
                                                   "min_past_origins": 7}}  # fmt: skip
    with pytest.raises(PreregistrationError):
        verify("national_state_calibration", bad)


def test_earlier_block_is_unchanged():
    assert verify("oot_calibration", load_place_rules())["verified"]


def _past(rows):
    return pd.DataFrame(rows, columns=["origin", "ratio"])


def test_pooled_ends_are_ranks():
    past = _past([(2000, r) for r in np.arange(1, 20, dtype=float)])  # n = 19: ranks 2 and 18
    assert ns.pool_ends(past, "pooled") == (2.0, 18.0)
    assert ns.pool_ends(_past([(2000, np.nan)]), "pooled") is None


def test_shock_ends_by_hand():
    # two origins, three states each; shocks are the medians, residuals the rest
    e = {2000: [-0.2, 0.0, 0.2], 2001: [0.3, 0.5, 0.7]}
    past = _past([(o, math.exp(x)) for o, xs in e.items() for x in xs])
    lo, hi = ns.pool_ends(past, "shock")
    # shocks 0.0 and 0.5 (n = 2: ranks 1 and 2); residuals -0.2 .. 0.2 (n = 6: ranks 1 and 6)
    assert math.isclose(math.log(lo), 0.0 - 0.2)
    assert math.isclose(math.log(hi), 0.5 + 0.2)
    with pytest.raises(ValueError):
        ns.pool_ends(past, "other")


def test_past_needs_all_target_years():
    assert ns.past_mask(np.array([2000, 2006, 2007]), 2012, 2, 5).tolist() == [True, True, False]


def _cases(rows):
    return pd.DataFrame(rows, columns=["state", "origin", "projected", "actual", "ratio"])


def _grid(seed=0, spoil_after=None):
    rng = np.random.default_rng(seed)
    rows = []
    for s in ("AA", "BB", "CC"):
        for o in range(2000, 2016):
            r = float(np.exp(rng.normal(0, 0.2)))
            if spoil_after is not None and o > spoil_after:
                r *= 40.0
            rows.append((s, o, 100.0, 100.0 * r, r))
    return _cases(rows)


def test_scoring_uses_only_earlier_cases():
    a = ns.score_state(_grid(), "AA", 2, 5, BLOCK, "pooled")
    b = ns.score_state(_grid(spoil_after=2010), "AA", 2, 5, BLOCK, "pooled")
    # a case at origin o uses pool origins <= o - 6; the range of o <= 2016 is unchanged
    ka = a.set_index("origin")[["low", "high"]]
    kb = b.set_index("origin")[["low", "high"]]
    assert not ka.empty
    pd.testing.assert_frame_equal(ka, kb)
    # first scored origin has two distinct past origins
    assert int(a["origin"].min()) == 2000 + 6 + 1


def test_range_holds_the_p50():
    rows = [(s, o, 100.0, 300.0, 3.0) for s in ("AA", "BB") for o in range(2000, 2012)]
    scored = ns.score_state(_cases(rows), "AA", 2, 5, BLOCK, "pooled")
    assert (scored["low"] <= scored["p50"]).all() and (scored["high"] >= scored["p50"]).all()


@pytest.mark.parametrize(
    ("c", "a", "b", "passes", "reason"),
    [
        (0.80, 0.8, 0.8, True, "in_band"),
        (0.70, 0.8, 0.8, False, "under_band"),
        (0.90, 0.8, 0.8, False, "over_band"),
        (0.80, 0.6, 0.8, False, "state_short"),
        (0.75, 0.70, 0.70, True, "in_band"),
        (0.85, 0.70, 0.70, True, "in_band"),
    ],
)
def test_judge_branches(c, a, b, passes, reason):
    per = {"AA": {"cases": 8, "coverage": a}, "BB": {"cases": 8, "coverage": b}}
    got = ns.judge(per, {"coverage": c, "cases": 100}, BLOCK)
    assert got["passes"] is passes and got["reason"] == reason


def test_judge_missing_named_state_fails():
    got = ns.judge({"AA": {"cases": 8, "coverage": 0.8}}, {"coverage": 0.8, "cases": 9}, BLOCK)
    assert got["reason"] == "state_short" and got["short_states"] == ["BB"]


def test_decide_branches():
    p = {"passes": True, "pooled_coverage": 0.84}
    s = {"passes": True, "pooled_coverage": 0.78}
    f = {"passes": False, "pooled_coverage": 0.6}
    assert ns.decide({"pooled": f, "shock": f}, BLOCK)["outcome"] == "keep_current"
    assert ns.decide({"pooled": f, "shock": s}, BLOCK)["variant"] == "shock"
    assert ns.decide({"pooled": p, "shock": s}, BLOCK)["variant"] == "shock"
    tie = {"passes": True, "pooled_coverage": 0.82}
    assert ns.decide({"pooled": tie, "shock": tie}, BLOCK)["variant"] == "pooled"


def test_engine_reads_only_an_adopted_decision(tmp_path):
    rules = {"national_state_calibration": {"results": "x"}}
    path = tmp_path / "n.yaml"
    doc = {"decision": {"outcome": "keep_current", "variant": None},
           "production_ends": {"last_3_mean": {"pooled": {"low": 0.5, "high": 1.5},
                                               "pool_cases": 9}}}  # fmt: skip
    path.write_text(yaml.safe_dump(doc))
    assert ns.national_ends_for("last_3_mean", rules, path) is None
    doc["decision"] = {"outcome": "adopt", "variant": "pooled"}
    path.write_text(yaml.safe_dump(doc))
    got = ns.national_ends_for("last_3_mean", rules, path)
    assert (got["low"], got["high"], got["pool_cases"]) == (0.5, 1.5, 9)
    assert ns.national_ends_for("median_5", rules, path) is None
    assert ns.national_ends_for("last_3_mean", {}, path) is None


def test_shipped_results_record_the_block():
    doc = yaml.safe_load(ns.OUT_PATH.read_text(encoding="utf-8"))
    entry = load_registry()["blocks"]["national_state_calibration"]
    assert doc["preregistration"]["sha256"] == entry["sha256"]
    for res in doc["variants"].values():
        p = res["pooled"]
        assert 0 <= p["covered"] <= p["cases"]
    passing = [v for v, r in doc["variants"].items() if r["judged"]["passes"]]
    if doc["decision"]["outcome"] == "keep_current":
        assert passing == []
    else:
        assert doc["decision"]["variant"] in passing
