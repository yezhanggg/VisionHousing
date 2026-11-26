"""The ADU two-lever case (timing and financing access) on Testland.

Timing only moves permits in time; financing access never lowers homes; the
variants differ and pair with the benchmark draw by draw.
"""

import numpy as np
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.engine.variants import load_variants
from visionhousing.ledger import load_ledger
from visionhousing.scenarios import adu_levers as lv
from visionhousing.scenarios.adu import ramp_sum

DRAWS = 300
LEVERS = ("adu_timing_reliable", "adu_financing_access", "adu_both")


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def runs(ctx):
    out = {"bench": run("TL", ["adu"], draws=DRAWS, context=ctx, use_cache=False)}
    for name in LEVERS:
        out[name] = run("TL", {"adu": {"variant": name}}, draws=DRAWS, context=ctx, use_cache=False)
    return {k: r.reforms["adu"] for k, r in out.items()}


# ---------------------------------------------------------------- lag model


def test_lag_cdf_pull_only_shortens_the_slow_tail():
    x = np.linspace(1, 3000, 400)
    f0 = lv.lag_cdf(x, 250.0, 0.95, 0.0)
    f1 = lv.lag_cdf(x, 250.0, 0.95, 0.5)
    assert np.all(np.diff(f0) >= 0) and np.all(f1 >= f0 - 1e-12)
    below = x < 250
    assert np.allclose(f0[below], f1[below])
    assert lv.lag_cdf(np.array(250.0), 250.0, 0.95) == pytest.approx(0.5, abs=1e-6)
    assert lv.lag_cdf(np.array(0.0), 250.0, 0.95) == 0.0
    # Pulled P90 = m + (1 - pi)(P90 - m).
    p90 = 250.0 * np.exp(lv.Z90 * 0.95)
    assert lv.lag_cdf(np.array(250 + 0.5 * (p90 - 250)), 250.0, 0.95, 0.5) == pytest.approx(
        0.9, abs=1e-5
    )


def test_issuance_conserves_applications():
    filings = ramp_sum(np.array([0.5, 0.3]), np.array([3.0, 4.0]), 15)
    filings[:, 5:] = 0.0
    for pull in (0.0, 0.5, 1.0):
        issued = lv.issuance_by_year(filings, np.array([250.0, 400.0]), np.array([0.9, 1.4]), pull)
        assert np.all(issued >= -1e-12)
        pending = filings.sum(axis=1) - issued.sum(axis=1)
        assert np.all(pending >= -1e-9) and np.all(pending < 0.1 * filings.sum(axis=1))


def test_lag_ranges_bracket_the_calibration_files():
    a = lv.lag_anchors()
    led = load_ledger()
    med, sd = led[lv.MEDIAN], led[lv.LOG_SD]
    assert med.low <= a["phl_new1_p50"] + 1 and med.high >= a["ca_median_high"] - 1
    assert med.low <= a["ca_median_low"] <= med.high
    assert sd.low <= a["ca_log_sd_low"] + 0.01 and sd.high >= a["ca_log_sd_high"] - 0.01
    assert sd.low <= a["phl_new1_log_sd"] <= sd.high


def test_cash_access_is_one_at_zero_and_falls():
    c = np.array([0.0, 10_000, 50_000, 200_000])
    a = lv.cash_access(c, 50_000, 1.5)
    assert a[0] == 1.0 and a[2] == pytest.approx(0.5) and np.all(np.diff(a) < 0)


# ---------------------------------------------------------------- variants


def test_lever_variants_are_listed_and_distinct(runs):
    known = load_variants()
    assert set(LEVERS) <= set(known)
    p50 = {k: r.added.p50 for k, r in runs.items()}
    assert len({round(v, 6) for v in p50.values()}) == 4
    assert p50["bench"] < p50["adu_timing_reliable"] < p50["adu_both"]
    assert p50["bench"] < p50["adu_financing_access"] < p50["adu_both"]


def test_benchmark_has_no_lever(runs):
    b = runs["bench"]
    assert lv.lever_of(b) is None
    assert not any(k.startswith("lever_") for k in b.records)
    assert b.annual.timing["mechanism"] is False


def test_paired_on_common_draws(runs):
    b = runs["bench"].state_draws
    for name in LEVERS:
        r = runs[name]
        lev = lv.lever_of(r)
        assert np.allclose(lev.bench_net, b)
        diff = r.state_draws - b
        assert np.allclose(diff, lev.moved + lev.newly)
        assert np.all(diff >= -1e-9)
        years = sorted(k for k in r.records if k.startswith("lever_change_"))
        assert len(years) == 5
        by_year = (lev.moved_by_year + lev.newly_by_year).sum(axis=1)
        assert np.allclose(by_year, diff, rtol=1e-6, atol=1e-6)
        # County rows add up to the state.
        assert np.allclose(lev.moved_county.sum(axis=0), lev.moved)
        assert np.allclose(lev.newly_county.sum(axis=0), lev.newly)


def test_timing_only_keeps_the_total_and_moves_the_share(runs):
    r = runs["adu_timing_reliable"]
    lev = lv.lever_of(r)
    rc = r.records
    assert np.allclose(lev.newly, 0.0)
    assert rc["lever_moved_into_window"].p50 > 0
    # Five-year + beyond total = applications filed in the window, same with and without.
    total_bench = rc["lever_benchmark_net"].p50 + rc["lever_after_window_benchmark"].p50
    total_lever = rc["lever_net"].p50 + rc["lever_after_window"].p50
    assert total_bench == pytest.approx(rc["lever_window_applications"].p50, rel=0.05)
    bench_draws = lev.bench_net
    apps = r.records["lever_window_applications"]
    assert apps.p50 >= rc["lever_net"].p50 >= rc["lever_benchmark_net"].p50
    assert total_lever == pytest.approx(total_bench, rel=0.05)
    assert np.all(r.state_draws >= bench_draws - 1e-9)
    # Outcome contract reports the shift apart from newly feasible.
    t = r.annual.timing
    assert t["mechanism"] is True
    assert t["shifted_into_window"].p50 == pytest.approx(rc["lever_moved_into_window"].p50)
    assert r.annual.state_draws.sum(axis=1) == pytest.approx(r.state_draws)


def test_timing_total_identity_per_draw(ctx):
    led = load_ledger()
    rng = np.random.default_rng(1)
    n = 50
    v = {
        lv.MEDIAN: rng.uniform(141, 670, n),
        lv.LOG_SD: rng.uniform(0.55, 1.63, n),
        lv.PULL: rng.uniform(0.25, 0.75, n),
        lv.BEYOND: np.full(n, led[lv.BEYOND].central),
        "adu.ramp_first_year_share": rng.uniform(0.3, 0.7, n),
        "adu.ramp_years_to_full": rng.uniform(2, 4, n),
    }
    t = lv._timing_factor(v, 5, np.zeros(n), ramp_sum)
    # In window + after window = applications, with and without the lever.
    assert np.allclose(1.0 + t["after0"], t["apps"])
    assert np.allclose(t["tau"] + t["after1"], t["apps"])
    assert np.all(t["tau"] >= 1.0 - 1e-12) and np.all(t["tau"] <= t["apps"] + 1e-12)


def test_financing_access_is_monotone(ctx):
    base = run("TL", ["adu"], draws=DRAWS, context=ctx, use_cache=False).reforms["adu"]
    last = base.state_draws
    for amount in (5_000, 15_000, 30_000, 60_000):
        over = {
            "adu.financing_program_amount": {
                "central": amount, "low": amount, "high": amount, "shape": "fixed"
            }
        }  # fmt: skip
        r = run("TL", ["adu"], overrides=over, draws=DRAWS, context=ctx, use_cache=False)
        now = r.reforms["adu"].state_draws
        assert np.all(now >= last - 1e-9)
        last = now
    assert last.mean() > base.state_draws.mean()


def test_financing_only_moves_nothing_in_time(runs):
    r = runs["adu_financing_access"]
    lev = lv.lever_of(r)
    assert np.allclose(lev.moved, 0.0)
    assert lev.newly.mean() > 0
    assert r.records["lever_owner_cash_after"].p50 < r.records["lever_owner_cash_before"].p50
    assert r.annual.timing["mechanism"] is False


def test_binding_text_and_card_fields(runs):
    for name in LEVERS:
        lev = lv.lever_of(runs[name])
        assert lev.binding["factor"] in ("timing", "financing", "eligibility")
        assert "P50" in lev.binding["text"]
    fields = lv.card_fields(runs["adu_both"], county=lv.lever_of(runs["adu_both"]).county_ids[0])
    assert {"moved", "newly", "binding", "years", "county_moved", "county_newly"} <= set(
        fields["lever"]
    )
    assert lv.card_fields(runs["bench"]) == {}
    assert lv._binding(0.0, 0.0, 100.0, 0.02)["factor"] == "eligibility"
    assert lv._binding(10.0, 1.0, 100.0, 0.02)["factor"] == "timing"
    assert lv._binding(1.0, 10.0, 100.0, 0.02)["factor"] == "financing"


def test_lever_card_lists_the_variants():
    from visionhousing.api.cards import load_cards

    names = load_cards()["lever"]["reforms"]["adu"]["variants"]
    assert set(LEVERS) <= set(names)


# ---------------------------------------------------------------- review fixes


def _fixed(value):
    return {"central": value, "low": value, "high": value, "shape": "fixed"}


def test_program_only_run_uses_no_counted_rent(ctx, monkeypatch):
    """Only the program is on: the counted rent stays at the run's 0 (no leak).

    The variant ranges feed the binding text alone, so every applied number is
    the same when that second ledger is switched off.
    """
    over = {lv.PROGRAM: _fixed(15_000)}
    a = run("TL", ["adu"], overrides=over, draws=DRAWS, context=ctx, use_cache=False)
    monkeypatch.setattr(lv, "_with_sources", lambda ledger: ledger)
    b = run("TL", ["adu"], overrides=over, draws=DRAWS, context=ctx, use_cache=False)
    assert np.array_equal(a.reforms["adu"].state_draws, b.reforms["adu"].state_draws)
    la, lb = lv.lever_of(a.reforms["adu"]), lv.lever_of(b.reforms["adu"])
    assert np.array_equal(la.newly, lb.newly)
    # And the counted rent, when it is on, does add homes.
    both = {**over, lv.RENT_SHARE: _fixed(0.2)}
    c = run("TL", ["adu"], overrides=both, draws=DRAWS, context=ctx, use_cache=False)
    assert c.reforms["adu"].state_draws.mean() > a.reforms["adu"].state_draws.mean()


def test_yearly_changes_add_up_to_the_paired_change_exactly(runs):
    for name in LEVERS:
        lev = lv.lever_of(runs[name])
        by_year = (lev.moved_by_year + lev.newly_by_year).sum(axis=1)
        assert np.allclose(by_year, lev.moved + lev.newly, rtol=1e-10, atol=1e-9)
        assert np.allclose(lev.moved_by_year.sum(axis=1), lev.moved, rtol=1e-10, atol=1e-9)


def test_financing_takes_the_cheapest_dollar_first():
    """A cheaper counted-rent loan fills the cash need before dearer program money."""
    n = 1
    cash0 = 10_000.0
    lines = {
        "owner_cash": np.array([[cash0]]),
        "cash_cost_factor": np.array([[0.010]]),  # own cash: $10 a month per $1,000
        "loan_payment_factor": np.array([[0.006]]),  # lender's loan
        "share_kept": np.array([[0.8]]),
    }
    col = {"adu.loan_term_years": np.full((n, 1), 30.0), "adu.required_coverage": 1.0}
    v = {
        lv.PROGRAM: np.array([10_000.0]),
        lv.PROGRAM_RATE: np.array([0.09]),  # dearer than the loan
        lv.RENT_SHARE: np.array([0.5]),
        lv.CASH_MEDIAN: np.array([20_000.0]),
        lv.CASH_SHAPE: np.array([1.5]),
    }
    rent = np.array([[30.0]])  # counted 15 / 0.006 = $2,500 of loan
    out = lv._financing(v, col, lines, np.array([[1_000.0]]), rent)
    loan = 0.5 * 30.0 / 0.006
    program = cash0 - loan
    prog_f = float(lv.payment_factor(0.09, 30.0))
    saving = loan * (0.010 - 0.006) + program * (0.010 - min(prog_f, 0.010))
    assert out.cash_after[0, 0] == pytest.approx(0.0)
    assert out.breakeven[0, 0] == pytest.approx(1_000.0 - saving / 0.8)
