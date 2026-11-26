"""Tests for the growth-aware coverage study (small synthetic data; no network)."""

from __future__ import annotations

import argparse
import copy
import math

import numpy as np
import pandas as pd
import pytest

from tests.test_validation_donor_pool import BSET, RULE, synthetic_panel
from visionhousing.validation import donor_pool as dp
from visionhousing.validation import growth_coverage as gc

# Logged in the decision log before the candidate was scored. Never update this
# to match an edited block: an edit makes it a new study.
GROWTH_SHA256 = "52500257051aec27b8bf40b303c0db488c1f85cac7106b1224c2c5a3999ce080"
POOLED_SHA256 = "c657753f5bbd3b6580258e97a564f6664fc2cb583f04b9f300ab13c7c810f3f7"


def _settings():
    return copy.deepcopy(dp.load_settings())


def _block():
    return _settings()[gc.GROWTH_KEY]


# --------------------------------------------------------------------------- fingerprint


def test_growth_block_fingerprint_pinned():
    s = _settings()
    assert dp.preregistration_sha256(s[gc.GROWTH_KEY]) == GROWTH_SHA256
    # The earlier pre-registered block is untouched by the new one.
    assert dp.preregistration_sha256(s[dp.PREREG_KEY]) == POOLED_SHA256
    changed = copy.deepcopy(s[gc.GROWTH_KEY])
    changed["decision"]["max_pooled_coverage"] = 0.9
    assert dp.preregistration_sha256(changed) != GROWTH_SHA256


# --------------------------------------------------------------------------- growth classes


def test_state_growth_by_hand():
    years = list(range(1990, 2001))
    rows = [
        {"county": "A1", "state": "AA", "year": y, "units_1": float(y - 1989),
         "units_2": 0.0, "units_3_4": 0.0, "units_5plus": 0.0}
        for y in years
    ]  # fmt: skip
    block = {"classes": {"recent_years": 5, "base_years": 5}}
    g = gc.state_growth(pd.DataFrame(rows), [1999], block)
    recent, base = np.mean([6, 7, 8, 9, 10]), np.mean([1, 2, 3, 4, 5])
    assert g["growth"].iloc[0] == pytest.approx(math.log((recent + 1) / (base + 1)))


def _growth_frame():
    rows = []
    vals = {"AA": 0.1, "BB": 0.3, "CC": 0.3, "DD": -0.2, "EE": 0.5, "FF": float("nan")}
    for o in (2000, 2001):
        for s, v in vals.items():
            rows.append({"state": s, "origin": o, "growth": v + (0.01 if o == 2001 else 0)})
    return pd.DataFrame(rows)


BLOCK3 = {"classes": {"n_classes": 3, "states": ["AA", "BB", "CC", "DD", "EE", "FF"]}}


def test_growth_classes_rule_and_ties():
    got = gc.growth_classes(_growth_frame(), BLOCK3)
    at = got[got["origin"] == 2000].set_index("state")["growth_class"].to_dict()
    # ranks: DD 0, AA 1, BB 2, CC 3 (tie with BB, broken by postal code), EE 4 of n = 5
    assert at == {"DD": 0, "AA": 0, "BB": 1, "CC": 1, "EE": 2, "FF": -1}


def test_growth_classes_deterministic_under_row_order():
    frame = _growth_frame()
    a = gc.growth_classes(frame, BLOCK3)
    b = gc.growth_classes(frame.sample(frac=1.0, random_state=3), BLOCK3)
    pd.testing.assert_frame_equal(a, b)


def test_growth_classes_only_listed_states():
    got = gc.growth_classes(_growth_frame(), {"classes": {"n_classes": 3, "states": ["AA", "EE"]}})
    assert set(got["state"]) == {"AA", "EE"}


# --------------------------------------------------------------------------- widening


def test_class_ends_raw_and_minimum():
    r = np.array([1.2, 1.3, 1.4, 1.5, 1.6, 1.7])
    lo, _hi, n = gc.class_ends(r, 5)
    assert n == 6 and lo > 0  # raw ratios above 1: both ends above the projection
    assert gc.class_ends(r[:4], 5) is None


def test_widen_only_widens_and_full_weight_keeps_current():
    cur = (-0.5, 0.2)
    assert gc._widen(cur, (-0.1, 0.6), 1.0) == cur
    low, high = gc._widen(cur, (-0.1, 0.6), 0.25)
    assert low == cur[0] and high == pytest.approx(0.25 * 0.2 + 0.75 * 0.6)
    assert gc._widen(cur, (-0.2, 0.1), 0.0) == cur


# --------------------------------------------------------------------------- decision


def _scored(cov_cur, cov_cand, width_cur=1.0, width_cand=1.1, state_prefix="S", cases=10):
    rows = []
    for i, (kc, cc) in enumerate(zip(cov_cur, cov_cand, strict=True)):
        for j in range(cases):
            rows.append({
                "state": f"{state_prefix}{i}", "origin": 2000 + j,
                "current_inside": j < round(kc * cases), "candidate_inside": j < round(cc * cases),
                "current_width": width_cur, "candidate_width": width_cand,
            })  # fmt: skip
    return pd.DataFrame(rows)


def _decide(cur, cand, pa_cur=0.8, pa_cand=0.8):
    s = _settings()
    block = s[gc.GROWTH_KEY]
    scored = _scored(cur, cand)
    pa = _scored([pa_cur], [pa_cand], state_prefix="PA")
    states = sorted(scored["state"].unique())
    return gc.decide_growth(scored, states, pa, s, block)


N = 10


def test_decide_adopts_when_closer():
    d = _decide([0.6] * N, [0.7] * N)
    assert d["outcome"] == "adopt" and d["beats_current"]


def test_decide_under_coverage_does_not_reject():
    # Clause 2 only caps coverage from above: a candidate still below 75% is not rejected.
    d = _decide([0.5] * N, [0.6] * N)
    assert d["outcome"] == "adopt" and d["pooled_coverage_not_above_cap"]


def test_decide_keeps_on_tie_or_worse():
    assert _decide([0.7] * N, [0.7] * N)["outcome"] == "keep_current"
    assert _decide([0.8] * N, [0.9] * N)["outcome"] == "keep_current"


def test_decide_rejects_above_cap():
    # gap falls (0.3 -> 0.1) but pooled coverage 90% > 85%
    d = _decide([0.5] * N, [0.9] * N)
    assert not d["pooled_coverage_not_above_cap"] and d["outcome"] == "keep_current"


def test_decide_prior_state_clause():
    # inside the band -> must stay inside
    assert _decide([0.6] * N, [0.7] * N, pa_cur=0.8, pa_cand=0.9)["outcome"] == "keep_current"
    # already outside -> may not move further from target, may stay
    assert _decide([0.6] * N, [0.7] * N, pa_cur=0.9, pa_cand=0.9)["outcome"] == "adopt"
    assert _decide([0.6] * N, [0.7] * N, pa_cur=0.9, pa_cand=1.0)["outcome"] == "keep_current"
    assert _decide([0.6] * N, [0.7] * N, pa_cur=0.9, pa_cand=0.8)["outcome"] == "adopt"


def test_decide_inconclusive_with_few_states():
    d = _decide([0.6] * 3, [0.7] * 3)
    assert d["outcome"] == "inconclusive"


# --------------------------------------------------------------------------- whole study


@pytest.fixture(scope="module")
def doc():
    panel = synthetic_panel(seed=4)
    s = _settings()
    s.update(held_out=["VT", "PA"], prior_state="PA", full_weight_cases=1000)
    return gc.run_growth_study(
        panel, s, BSET, RULE, as_of="2026-01-01", check_preregistration=False
    )


def test_study_document(doc):
    assert doc["preregistration"]["sha256"] == GROWTH_SHA256
    assert doc["decision"]["outcome"] in {"adopt", "keep_current", "inconclusive"}
    assert set(doc["diagnosis"]) == {"all_states", "sparse_states"}
    vt = doc["implied_range"]["VT"]
    assert vt["candidate"]["low_units"] <= vt["current"]["low_units"]
    assert vt["candidate"]["high_units"] >= vt["current"]["high_units"]
    for row in doc["sparse_states"].values():
        assert row["growth_class"] in {"slow", "middle", "fast"}
        assert row["weight_own"] < 1


def test_candidate_never_narrower_and_target_left_out():
    panel = synthetic_panel(seed=4)
    s = _settings()
    s.update(prior_state="PA", full_weight_cases=1000)
    block = s[gc.GROWTH_KEY]
    cases = dp.area_cases(panel, RULE, BSET, s)
    classes = gc.growth_classes(gc.state_growth(panel, list(cases.origins), block), block)
    scored = gc.score_cases(cases, classes, ["VT", "NH"], s, block, with_candidate=True)
    assert not scored.empty
    assert (scored["candidate_low"] <= scored["current_low"] + 1e-9).all()
    assert (scored["candidate_high"] >= scored["current_high"] - 1e-9).all()
    # Leave-one-state-out: changing VT's own ratios does not move VT's class pool.
    o = int(scored["origin"].max())
    base = gc._case_ranges(cases, classes, "VT", o, s, block, with_candidate=True)
    st = cases.states.copy()
    st.loc[st["area"] == "VT", "ratio"] = 50.0
    other = dp.AreaCases(cases.counties, st, cases.origins, cases.lead, cases.horizon)
    moved = gc._case_ranges(other, classes, "VT", o, s, block, with_candidate=True)
    assert moved["pool"] == base["pool"]


def test_cli_registers_command():
    from visionhousing.validation.commands import add_growth_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_growth_parser(sub)
    args = parser.parse_args(["validate-growth-coverage"])
    assert args.command == "validate-growth-coverage"


def test_growth_study_refuses_a_changed_block(monkeypatch):
    from visionhousing.validation.preregistration import PreregistrationError

    def boom(*a, **k):
        raise AssertionError("scored before the check")

    monkeypatch.setattr(dp, "area_cases", boom)
    s = _settings()
    s[gc.GROWTH_KEY]["decision"]["max_pooled_coverage"] = 0.9
    with pytest.raises(PreregistrationError, match=gc.GROWTH_KEY):
        gc.run_growth_study(synthetic_panel(seed=4), s, BSET, RULE)
    s = _settings()  # unchanged settings pass the check and reach the scoring
    with pytest.raises(AssertionError, match="scored before the check"):
        gc.run_growth_study(synthetic_panel(seed=4), s, BSET, RULE)
