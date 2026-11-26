"""Parking: spaces avoided = max(v, m_before) - max(v, m_after), and the binding share."""

import itertools

import numpy as np
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.ledger import load_ledger, sample
from visionhousing.scenarios.fees import window_factor
from visionhousing.scenarios.parking import (
    REQ_ATT,
    REQ_MF,
    SAVING_IDS,
    SPREAD,
    STILL_EL,
    STILL_TR,
    VOLUNTARY_IDS,
    binding_share,
    expected_provided,
    parking_saving,
    share_below,
)

N = 64
DRAWS = 300


def params(**fixed):
    led = load_ledger()
    p = sample(led, n=N, seed=3, ids=(*SAVING_IDS, *VOLUNTARY_IDS, SPREAD))
    for k, v in fixed.items():
        p[k.replace("__", ".")] = np.full(N, float(v))
    return p


WEIGHTS = np.array([[0.0, 0.3, 0.5, 0.7], [0.0, 0.0, 0.1, 0.2]])
BASE = np.array([[100.0, 10.0, 20.0, 50.0], [80.0, 5.0, 5.0, 10.0]])


def test_closed_forms_match_a_brute_force_average():
    rng = np.random.default_rng(0)
    for lo, hi, m in [(0.5, 1.5, 1.0), (0.2, 0.8, 1.5), (1.0, 2.0, 0.5), (0.9, 0.9, 1.0)]:
        v = rng.uniform(lo, hi, 400_000) if hi > lo else np.full(10, lo)
        assert expected_provided(lo, hi, m) == pytest.approx(np.maximum(v, m).mean(), abs=2e-3)
        assert share_below(lo, hi, m) == pytest.approx(np.mean(v < m), abs=2e-3)


@pytest.mark.parametrize("response", ["share", "provided", "voluntary"])
def test_no_old_minimum_means_nothing_avoided(response):
    p = params(**{REQ_MF.replace(".", "__"): 0.0, REQ_ATT.replace(".", "__"): 0.0})
    out = parking_saving(p, WEIGHTS, response)
    assert np.all(out["avoided"] == 0) and np.all(out["saving"] == 0)
    for kept in (1.0, 0.5, 0.0):
        assert np.all(binding_share(p, WEIGHTS, BASE, response, kept) == 0)


@pytest.mark.parametrize("response", ["provided", "voluntary"])
def test_builders_already_above_the_minimum_avoid_nothing(response):
    high = {pid.replace(".", "__"): 5.0 for pid in VOLUNTARY_IDS}
    p = params(**high, **{SPREAD.replace(".", "__"): 0.3})  # v at least 3.5 > 2.0
    out = parking_saving(p, WEIGHTS, response)
    assert np.all(out["avoided"] == 0)
    assert np.all(binding_share(p, WEIGHTS, BASE, response, 1.0) == 0)


def test_benchmark_share_one_means_no_binding_and_no_saving():
    p = params(**{STILL_TR.replace(".", "__"): 1.0, STILL_EL.replace(".", "__"): 1.0})
    assert np.all(parking_saving(p, WEIGHTS, "share")["avoided"] == 0)
    assert np.all(binding_share(p, WEIGHTS, BASE, "share", 1.0) == 0)


def test_benchmark_is_the_rule_with_v_a_share_of_the_minimum():
    p = params()
    req_mf = p[REQ_MF]
    for floor in (0.0, 0.3, 0.5, 0.8):
        got = parking_saving(p, WEIGHTS, "share", floor)["avoided"]
        # apartments elsewhere in county 2: weight 0.2 transit-rich
        v_tr, v_el = req_mf * p[STILL_TR], req_mf * p[STILL_EL]
        want_tr = np.maximum(v_tr, req_mf) - np.maximum(v_tr, floor * req_mf)
        want_el = np.maximum(v_el, req_mf) - np.maximum(v_el, floor * req_mf)
        np.testing.assert_allclose(got[1, 3], 0.2 * want_tr + 0.8 * want_el)


def test_half_cut_equals_full_removal_where_the_half_minimum_never_binds():
    p = params(**{STILL_TR.replace(".", "__"): 0.6, STILL_EL.replace(".", "__"): 0.9})
    full = parking_saving(p, WEIGHTS, "share", 0.0)["avoided"]
    half = parking_saving(p, WEIGHTS, "share", 0.5)["avoided"]
    np.testing.assert_array_equal(full, half)
    assert np.all(binding_share(p, WEIGHTS, BASE, "share", 0.5) == 0)
    assert np.all(binding_share(p, WEIGHTS, BASE, "share", 1.0) == 1)


def test_binding_share_rises_with_the_minimum_left():
    p = params()
    shares = [binding_share(p, WEIGHTS, BASE, "voluntary", k) for k in (0, 0.25, 0.5, 0.75, 1)]
    for a, b in itertools.pairwise(shares):
        assert np.all(a <= b + 1e-12)
    assert np.all(shares[0] == 0) and np.all((shares[-1] >= 0) & (shares[-1] <= 1))


def test_voluntary_some_projects_above_the_minimum_save_less_than_provided():
    p = params()
    vol = parking_saving(p, WEIGHTS, "voluntary")["avoided"]
    assert np.all(vol >= 0)
    # spread 0 gives the provided response back exactly
    p0 = params(**{SPREAD.replace(".", "__"): 0.0})
    np.testing.assert_allclose(
        parking_saving(p0, WEIGHTS, "voluntary")["avoided"],
        parking_saving(p0, WEIGHTS, "provided")["avoided"],
    )


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


def test_run_carries_the_curve_and_its_ends(ctx):
    res = run("TL", {"parking": {}}, draws=DRAWS, context=ctx, use_cache=False)
    o = res.reforms["parking"]
    recs = o.result.records
    np.testing.assert_allclose(recs["added_kept_000"].p50, o.added.p50)
    assert recs["added_kept_100"].p90 == 0
    assert recs["binding_share_before"].p50 == recs["binding_share_kept_100"].p50
    assert recs["binding_share_after"].p90 == 0
    for k in ("binding_share_kept_050", "added_kept_050"):
        assert recs[k].assumption_ids and "x old minimum" in recs[k].formula


def test_voluntary_variant_runs_and_differs(ctx):
    a = run("TL", {"parking": {}}, draws=DRAWS, context=ctx, use_cache=False)
    b = run(
        "TL",
        {"parking": {"variant": "parking_voluntary_floor"}},
        draws=DRAWS,
        context=ctx,
        use_cache=False,
    )
    assert SPREAD in b.reforms["parking"].added.assumption_ids
    assert a.reforms["parking"].added.p50 != b.reforms["parking"].added.p50


def test_window_sensitivity_is_pennsylvania_only_and_reads_the_diagnostic(ctx):
    f, entry = window_factor("baseline_pa_window_2022_2025", "PA")
    assert f == pytest.approx(entry["five_year"] / entry["shipped_state_component"])
    assert 0.5 < f < 1.0
    with pytest.raises(ValueError):
        window_factor("baseline_pa_window_2022_2025", "TL")
    with pytest.raises(ValueError):
        window_factor("no_such_window", "PA")
    with pytest.raises(ValueError):
        run(
            "TL",
            {"parking": {"variant": "parking_baseline_window_pa_2022_2025"}},
            draws=DRAWS,
            context=ctx,
            use_cache=False,
        )
