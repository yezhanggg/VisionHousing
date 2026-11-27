"""Tests for the uncertainty structure checks and sensitivities (engine.correlated)."""

from __future__ import annotations

import numpy as np
import pytest
import yaml

from visionhousing.engine import correlated as cr
from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute
from visionhousing.engine.montecarlo import baseline_scale, baseline_scale_hook
from visionhousing.ledger import load_ledger, sample, stream
from visionhousing.ledger.sampling import draw, joint_ranks
from visionhousing.sensitivity.importance import spearman

N = 300
REFORMS = ["adu", "missing_middle", "tod", "parking", "fees"]


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def settings():
    return cr.load_settings()


@pytest.fixture(scope="module")
def runs(ctx, settings):
    reg = cr.regional_sigmas(ctx, settings)
    out = {}
    for name in cr.SCENARIOS:
        out[name] = cr.run_scenario(
            ctx, name, REFORMS, seed=0, draws=N, settings=settings, regional=reg
        )
    return out


# ------------------------------------------------------------------ hooks


def test_sample_unchanged_outside_the_hook():
    led = load_ledger()
    pid = "adu.hard_cost_per_sqft"
    want = draw(led[pid], N, stream(3, pid))
    np.testing.assert_array_equal(sample(led, n=N, seed=3, ids=[pid])[pid], want)


def test_joint_ranks_reorder_only():
    led = load_ledger()
    pid, other = "adu.hard_cost_per_sqft", "adu.soft_cost_share"
    base = sample(led, n=N, seed=1, ids=[pid, other])
    ranks = np.arange(N)  # sorted order
    with joint_ranks(1, N, {pid: ranks}):
        got = sample(led, n=N, seed=1, ids=[pid, other])
        other_seed = sample(led, n=N, seed=2, ids=[pid])
        other_n = sample(led, n=N + 1, seed=1, ids=[pid])
    np.testing.assert_array_equal(np.sort(got[pid]), np.sort(base[pid]))  # same values
    assert np.all(np.diff(got[pid]) >= 0)  # in the given order
    np.testing.assert_array_equal(got[other], base[other])
    np.testing.assert_array_equal(other_seed[pid], sample(led, n=N, seed=2, ids=[pid])[pid])
    np.testing.assert_array_equal(other_n[pid], sample(led, n=N + 1, seed=1, ids=[pid])[pid])
    with pytest.raises(ValueError), joint_ranks(1, N, {pid: np.arange(N - 1)}):
        pass


def test_scale_hook_shape_checked_and_scoped(ctx):
    from visionhousing.engine.core import prepare

    rc, _ = prepare(ctx, draws=N)
    state = rc.baseline.state
    plain = baseline_scale(state, 3, N, 0)
    with baseline_scale_hook(lambda u, lo, hi, nc: np.ones((nc, u.size))):
        assert np.all(baseline_scale(state, 3, N, 0) == 1)
    with baseline_scale_hook(lambda u, lo, hi, nc: np.ones((1, 1))), pytest.raises(ValueError):
        baseline_scale(state, 3, N, 0)
    np.testing.assert_array_equal(baseline_scale(state, 3, N, 0), plain)


# ------------------------------------------------------------------ conservation


@pytest.mark.parametrize("name", cr.SCENARIOS)
def test_state_equals_counties_and_years_in_every_draw(runs, name):
    result, _ = runs[name]
    cons = cr.conservation(result)
    assert {"baseline", "package", "total", *REFORMS} <= set(cons)
    for row in cons.values():
        assert set(row) >= {"state_vs_counties", "years_vs_five_year_state"}
        assert max(row.values()) < 1e-6


def test_county_draws_follow_the_state_multiplier(runs):
    result, _ = runs["benchmark"]
    p50 = np.array([float(result.baseline_counties[c].p50) for c in result.county_ids])
    c = np.asarray(result.baseline_county_draws)
    share = result.baseline_draws / p50.sum()
    np.testing.assert_allclose(c, p50[:, None] * share[None, :], rtol=1e-12)
    rep = cr.county_reconciliation(result)
    assert rep["counties"] == int((p50 > 0).sum())


def test_benchmark_scenario_equals_a_plain_run(ctx, runs):
    plain = compute(ctx, REFORMS, seed=0, draws=N)
    bench, _ = runs["benchmark"]
    np.testing.assert_array_equal(plain.baseline_draws, bench.baseline_draws)
    for k in REFORMS:
        np.testing.assert_array_equal(plain.reforms[k].state_draws, bench.reforms[k].state_draws)


# ------------------------------------------------------------------ regional residuals


def test_reconcile_keeps_the_weighted_total():
    rng = np.random.default_rng(0)
    m = np.exp(rng.normal(0, 0.3, size=(5, 40)))
    p50 = np.array([10.0, 0.0, 5.0, 100.0, 1.0])
    r = cr.reconcile(m, p50)
    np.testing.assert_allclose((p50[:, None] * r).sum(axis=0), p50.sum())


def test_regional_keeps_state_draws_and_moves_counties(runs):
    bench, _ = runs["benchmark"]
    reg, _ = runs["regional"]
    unrec, _ = runs["regional_unreconciled"]
    np.testing.assert_allclose(reg.baseline_draws, bench.baseline_draws, rtol=1e-10)
    assert not np.allclose(reg.baseline_county_draws, bench.baseline_county_draws)
    assert not np.allclose(unrec.baseline_draws, bench.baseline_draws)


def test_regional_sigmas_are_measured(ctx, settings):
    reg = cr.regional_sigmas(ctx, settings)
    assert reg["groups"] and reg["pooled_sigma"] >= 0
    for g in reg["groups"].values():
        assert g["sigma"] >= 0 and g["counties"] >= 1
    m = cr.regional_multipliers(
        ["a", "b", "zz"], {"a": "x", "b": "y"}, {"x": 0.2, "y": 0.0}, 4000, 0
    )
    assert abs(m[0].mean() - 1) < 0.02 and np.all(m[1] == 1) and np.all(m[2] == 1)


# ------------------------------------------------------------------ common factor


def test_factor_ranks_follow_signs(settings):
    f, ranks, base, _ = cr.factor_ranks(settings, 2000, 0)
    assert spearman(f, ranks["adu.hard_cost_per_sqft"]) > 0.3
    assert spearman(f, ranks["adu.loan_share"]) < -0.3
    assert spearman(f, base) < -0.3
    f0, r0, _, _ = cr.factor_ranks(settings, 2000, 0, loading=0.0)
    assert abs(spearman(f0, r0["adu.hard_cost_per_sqft"])) < 0.1
    with pytest.raises(ValueError):
        cr.factor_ranks(settings, 10, 0, loading=1.5)


def test_correlated_keeps_marginals_and_links_both_arms(runs, settings):
    bench, _ = runs["benchmark"]
    cor, factor = runs["correlated"]
    np.testing.assert_allclose(np.sort(cor.baseline_draws), np.sort(bench.baseline_draws))
    pid = "adu.hard_cost_per_sqft"
    np.testing.assert_array_equal(
        np.sort(cor.draws_obj.values[pid]), np.sort(bench.draws_obj.values[pid])
    )
    assert spearman(factor, cor.draws_obj.values[pid]) > 0.3
    assert spearman(factor, cor.baseline_draws) < -0.3
    assert spearman(factor, cor.reforms["fees"].state_draws) < 0


def test_state_report_testland(ctx, settings):
    rep = cr.state_report(ctx, settings=settings, draws=200)
    sc = rep["scenarios"]
    assert set(sc) == set(cr.SCENARIOS)
    for block in sc.values():
        assert block["conservation"]["holds"]
        for d in block["diagnostic_draws"]:
            assert abs(d["state_minus_counties"]) < 1e-6
            assert abs(d["five_year_minus_sum_of_years"]) < 1e-6
            assert abs(sum(d["baseline_by_group"].values()) - d["state_baseline"]) < 1e-6
        for r in block["ranges"].values():
            assert r["p10"] <= r["p50"] <= r["p90"] and r["formula"]
    adv = sc["correlated"]["adverse_corner"]
    assert adv["draws"] == pytest.approx(200 * 0.2, abs=1)
    assert adv["ranges"]["baseline"]["p50"] < sc["benchmark"]["ranges"]["baseline"]["p50"]
    assert sc["correlated"]["moved_inputs"]["adu.start_delay_years"].startswith("fixed")
    assert set(sc["benchmark"]["ranking"]["first_share"]) == set(REFORMS)


def test_saved_results_hold_for_the_four_states():
    doc = yaml.safe_load(cr.RESULTS_PATH.read_text(encoding="utf-8"))
    assert {"PA", "VT", "MA", "WY"} <= set(doc["states"])
    for st in doc["states"].values():
        for block in st["scenarios"].values():
            assert block["conservation"]["holds"]
            for d in block["diagnostic_draws"]:
                assert abs(d["state_minus_counties"]) < 1e-3
