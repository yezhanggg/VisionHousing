"""The outcome contract: years add up, gross versus net, as-of date, bias-correction rule."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline.contest import (
    bias_correction_decision,
    origin_overlap,
    place_info_for_context,
    run_contest,
)
from visionhousing.baseline.panel import to_panel
from visionhousing.baseline.places import load_place_rules
from visionhousing.baseline.settings import settings_from_ledger
from visionhousing.engine import run
from visionhousing.engine.context import load_context
from visionhousing.engine.outcome import load_rules, reform_year_shares
from visionhousing.scenarios.adu import ramp_sum, year_shares

ALL = ["adu", "missing_middle", "tod", "fees", "parking"]
DRAWS = 300


@pytest.fixture(scope="module")
def result():
    return run("TL", ALL, seed=3, draws=DRAWS)


# ---------------------------------------------------------------- years add up


def test_every_reform_s_years_sum_to_its_five_year_total_in_every_draw(result):
    for key, o in result.reforms.items():
        a = o.annual
        assert a is not None, key
        assert a.years == (2027, 2028, 2029, 2030, 2031)
        assert a.state_draws.shape == (DRAWS, 5)
        assert np.allclose(a.state_draws.sum(axis=1), o.state_draws), key
        assert np.allclose(a.county_draws.sum(axis=2), o.county_draws), key
        assert np.allclose(a.shares.sum(axis=1), 1.0), key


def test_baseline_package_and_total_years_sum_in_every_draw(result):
    b, p, t = result.baseline_annual, result.package_annual, result.total_annual
    assert np.allclose(b.state_draws.sum(axis=1), result.baseline_draws)
    assert np.allclose(b.county_draws.sum(axis=2), result.baseline_county_draws)
    assert np.allclose(p.state_draws.sum(axis=1), result.package.state_draws)
    assert np.allclose(p.county_draws.sum(axis=2), result.package.county_draws)
    assert np.allclose(t.state_draws, b.state_draws + p.state_draws)


def test_tract_years_sum_to_the_tract_draws(result):
    o = result.reforms["adu"]
    table = o.tract_annual()
    assert table.p50.shape == (len(o.result.geoids), 5)
    by_year = o.result.added[:, :, None] * o.annual.shares[None, :, :]
    assert np.allclose(by_year.sum(axis=2), o.result.added)
    county = o.result.county_of[0]
    one = o.tract_annual(county=county)
    assert set(one.geoids) <= set(table.geoids)
    assert o.result is not None and result.reforms["fees"].tract_annual() is None


def test_state_year_records_carry_formula_and_ids(result):
    o = result.reforms["fees"]
    for r in o.annual.state:
        assert "ramp(t) / sum of ramp" in r.formula
        assert "fees.ramp_first_year_share" in r.assumption_ids
        assert "fees.start_delay_years" in r.assumption_ids
    assert o.annual.timing["mechanism"] is False
    assert o.annual.timing["shifted_into_window"].p90 == 0
    assert o.annual.timing["newly_feasible"].p50 == pytest.approx(o.added.p50)


def test_ramp_with_a_start_delay():
    plain = ramp_sum(0.5, 3, 5)
    assert plain == pytest.approx([0.5, 0.75, 1, 1, 1])
    late = ramp_sum(0.5, 3, 5, delay=1)
    assert late == pytest.approx([0, 0.5, 0.75, 1, 1])
    half = ramp_sum(0.5, 3, 5, delay=0.5)
    assert half == pytest.approx([0.25, 0.75, 1, 1, 1])
    assert year_shares(ramp_sum(0.5, 3, 5, delay=9)) == pytest.approx([0.2] * 5)


def test_a_start_delay_moves_years_not_the_fee_total():
    ctx = load_context("TL")
    fixed = {"central": 1, "low": 1, "high": 1, "shape": "fixed"}
    a = run("TL", ["fees"], seed=1, draws=200, context=ctx)
    b = run("TL", ["fees"], seed=1, draws=200, context=ctx,
            overrides={"fees.start_delay_years": fixed})  # fmt: skip
    assert np.allclose(a.reforms["fees"].state_draws, b.reforms["fees"].state_draws)
    assert np.all(b.reforms["fees"].annual.state_draws[:, 0] == 0)
    assert np.allclose(
        b.reforms["fees"].annual.state_draws.sum(axis=1), a.reforms["fees"].state_draws
    )


def test_year_shares_are_the_seeded_ramp():
    ctx = load_context("TL")
    rules = load_rules()
    s1, ids = reform_year_shares("parking", ctx.ledger, 50, 7, rules=rules)
    s2, _ = reform_year_shares("parking", ctx.ledger, 50, 7, rules=rules)
    assert np.array_equal(s1, s2)
    assert set(ids) == {
        "parking.start_delay_years",
        "parking.ramp_first_year_share",
        "parking.ramp_years_to_full",
    }


# ---------------------------------------------------------------- gross and net


def test_gross_is_never_below_net_where_homes_are_demolished(result):
    for key in ("missing_middle", "tod"):
        res = result.reforms[key].result
        assert res.net_stock is not None and res.removed is not None
        assert np.all(res.added >= res.net_stock - 1e-9), key
        g = result.reforms[key].gross_net
        assert set(g) == {"authorized", "net_stock", "removed", "baseline_on_sites"}
        assert g["authorized"] is result.reforms[key].added
        assert g["net_stock"].p50 <= g["authorized"].p50 + 1e-9
    adu = result.reforms["adu"].gross_net
    assert adu["net_stock"].p50 == adu["authorized"].p50  # an ADU removes no home


def test_gross_and_net_differ_by_the_reform_s_share_of_removals():
    ctx = load_context("TL")
    cheap = {  # make missing middle pencil in Testland so the arrays are not all zero
        "missing_middle.hard_cost_per_sqft": {
            "central": 60, "low": 60, "high": 60, "shape": "fixed"
        },
        "missing_middle.redevelopment_hazard_annual": {
            "central": 0.05, "low": 0.05, "high": 0.05, "shape": "fixed"
        },
        "missing_middle.baseline_replacement_probability": {
            "central": 0.2, "low": 0.2, "high": 0.2, "shape": "fixed"
        },
    }  # fmt: skip
    r = run("TL", ["missing_middle"], seed=0, draws=100, context=ctx, overrides=cheap)
    res = r.reforms["missing_middle"].result
    assert res.removed.sum() > 0
    assert np.allclose(res.added - res.net_stock, 0.8 * res.removed)


# ---------------------------------------------------------------- as of and vintages


def test_every_result_carries_the_contract(result):
    d = json.loads(json.dumps(result.to_dict()))
    assert d["as_of"] == load_rules()["as_of"] == result.as_of
    c = d["contract"]
    for k in ("measure", "window", "vintages", "effective_dates", "hold_constant", "audit"):
        assert c[k], k
    assert c["measure"]["name"] == "additional units authorized by building permits"
    assert set(c["audit"]) == {"baseline", *ALL}
    assert all(row["measure"] == "authorizations" for row in c["audit"].values())
    assert "keep" in c["baseline_correction"]
    assert d["annual"]["baseline"]["years"] == [2027, 2028, 2029, 2030, 2031]
    assert d["reform_results"]["adu"]["annual"]["counties"]["p50"]


def test_baseline_only_run_has_the_contract_too():
    r = run("TL", [], seed=0, draws=50)
    assert r.as_of and r.baseline_annual is not None and r.package_annual is None


# ---------------------------------------------------------------- bias-correction rule


def _tested(actual, proj, ratio_q):
    q10, q50, q90 = ratio_q
    return pd.DataFrame(
        {
            "actual": actual,
            "p50_none": proj,
            "low_size_class": np.asarray(proj) * q10,
            "p50_size_class": np.asarray(proj) * q50,
            "high_size_class": np.asarray(proj) * q90,
        }
    )


def test_decision_keeps_a_correction_that_helps_and_covers():
    proj = np.full(10, 100.0)
    actual = np.array([110, 115, 120, 118, 125, 121, 119, 60, 200, 117], dtype=float)
    d = bias_correction_decision(_tested(actual, proj, (0.9, 1.2, 1.3)), "size_class", (0.75, 0.85))
    assert d["corrected"]["median_abs"] < d["uncorrected"]["median_abs"]
    assert d["corrected"]["coverage"] == pytest.approx(0.8)
    assert d["keep"] is True


def test_decision_drops_a_correction_that_misses_coverage_or_error():
    proj = np.full(10, 100.0)
    actual = np.array([110, 115, 120, 118, 125, 121, 119, 60, 200, 117], dtype=float)
    narrow = bias_correction_decision(
        _tested(actual, proj, (1.19, 1.2, 1.21)), "size_class", (0.75, 0.85)
    )
    assert narrow["keep"] is False and "coverage" in narrow["reason"]
    wrong = bias_correction_decision(
        _tested(actual, proj, (0.4, 0.6, 2.0)), "size_class", (0.75, 0.85)
    )
    assert wrong["keep"] is False and "did not lower" in wrong["reason"]
    for d in (narrow, wrong):
        assert math.isfinite(d["uncorrected"]["coverage"])  # coverage computed for both


def test_decision_is_deterministic_and_applied():
    ctx = load_context("TL")
    rules = load_place_rules()
    assert rules["bias_correction"]["coverage_band"] == [0.75, 0.85]
    a = run_contest(ctx.permits, ctx.ledger, rules, place_info_for_context(ctx)).correction
    b = bias_correction_decision(
        run_contest(ctx.permits, ctx.ledger, rules, place_info_for_context(ctx)).tested,
        a["pools"],
        tuple(a["band"]),
    )
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    r = run("TL", [], seed=0, draws=20)
    assert r.contract["baseline_correction"]["keep"] == a["keep"]
    if not a["keep"]:
        assert "bias correction dropped" in next(iter(r.baseline_counties.values())).formula


def test_overlapping_origins_are_counted_as_independent_periods():
    ctx = load_context("TL")
    s = settings_from_ledger(ctx.ledger)
    o = origin_overlap(to_panel(ctx.permits), s)
    assert o["independent_periods"] == (o["last_origin"] - o["first_origin"]) // s.horizon + 1
    assert o["independent_periods"] < o["origins"]
