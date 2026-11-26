"""Fee cut: the fiscal identity per paired draw, zero-gain draws, named fiscal cases."""

import numpy as np
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.engine.readouts import (
    draw_share_words,
    fiscal_split,
    mc_standard_error,
    share_percent,
)
from visionhousing.ledger import load_ledger

DRAWS = 300


def test_fiscal_identity_with_the_plan_numbers():
    led = load_ledger()
    cut = np.array([25_000.0])
    fee = np.array([50_000.0])
    out = fiscal_split(cut, fee, np.array([100.0]), np.array([5.0]), led, ids=[], sources=[])
    assert out["fiscal_revenue_before"].p50 == pytest.approx(5_000_000)
    assert out["fiscal_revenue_after"].p50 == pytest.approx(2_625_000)
    assert out["fiscal_revenue_change"].p50 == pytest.approx(-2_375_000)
    assert out["fiscal_net_loss_per_added_home"].p50 == pytest.approx(475_000)
    # same identity as the split lines: - waived on baseline + paid by added
    assert out["fiscal_net_change"].p50 == pytest.approx(
        -out["fiscal_waived_on_baseline"].p50 + out["fiscal_paid_by_added"].p50
    )
    assert out["fiscal_zero_gain_share"].value == 0


def test_zero_gain_draws_are_counted_and_still_lose_revenue():
    led = load_ledger()
    cut = np.full(4, 25_000.0)
    out = fiscal_split(
        cut, cut * 2, np.full(4, 100.0), np.array([0.0, 0.0, 5.0, 10.0]), led, ids=[], sources=[]
    )
    assert out["fiscal_zero_gain_share"].value == 0.5
    assert out["fiscal_revenue_change"].p10 < 0
    assert "2 of" not in out["fiscal_net_loss_per_added_home"].name  # named by share, not count
    assert "50%" in out["fiscal_net_loss_per_added_home"].name


def test_monte_carlo_standard_error_and_precision():
    assert mc_standard_error(0.6, 2000) == pytest.approx(0.010954, abs=1e-6)
    assert share_percent(0.6, 2000) == "60%"
    assert share_percent(0.995, 2000) == "99.5%"
    assert share_percent(1.0, 2000) == "100%"
    words = draw_share_words(0.6, 2000)
    assert words.startswith("in 60% of the modeled draws") and "1.1 percentage points" in words
    with pytest.raises(ValueError):
        mc_standard_error(0.5, 0)


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


def _fees(ctx, options, overrides=None):
    return run(
        "TL",
        {"fees": options},
        overrides=overrides,
        draws=DRAWS,
        context=ctx,
        use_cache=False,
    ).reforms["fees"]


def test_zero_incidence_adds_nothing_but_still_shows_the_revenue_lost(ctx):
    o = _fees(ctx, {}, {"fees.passthrough_k": 0.0})
    assert np.all(np.asarray(o.state_draws) == 0)
    r = o.readouts
    assert r["revenue_given_up"].p10 > 0
    assert r["fiscal_waived_on_baseline"].p10 > 0
    assert r["fiscal_revenue_change"].p90 < 0
    assert r["fiscal_zero_gain_share"].value == 1.0
    assert "cost_per_added_home" not in r and "fiscal_net_loss_per_added_home" not in r
    assert "not defined" in o.readout_texts["cost_per_added_home"]


def test_named_fiscal_cases(ctx):
    base = _fees(ctx, {})
    back = _fees(ctx, {"variant": "fees_funded_backfill"})
    late = _fees(ctx, {"variant": "fees_delayed_capacity"})
    np.testing.assert_allclose(back.state_draws, base.state_draws)
    # funded backfill: the state pays the revenue given up; places keep F x A
    np.testing.assert_allclose(
        back.readouts["fiscal_replacement"].p50, back.readouts["revenue_given_up"].p50
    )
    assert back.readouts["fiscal_net_change"].p10 > 0
    # the identity before replacement does not change with who pays
    assert back.readouts["fiscal_revenue_change"].p50 == pytest.approx(
        base.readouts["fiscal_revenue_change"].p50
    )
    # delayed capacity: fewer added homes, draw by draw, never more
    assert np.all(np.asarray(late.state_draws) <= np.asarray(base.state_draws) + 1e-9)
    assert late.added.p50 < base.added.p50
    assert late.result.records["delayed_by_capacity"].p50 > 0
    for k in ("fiscal_by_recipient", "fiscal_by_year"):
        assert base.readout_texts[k].startswith("Not split")


def test_the_assumed_fee_id_is_renamed():
    led = load_ledger()
    assert "fees.pa_assumed_fee_per_unit_judgement" in led
    assert "fees.pa_documented_fee_per_unit" not in led
    assert led["fees.pa_assumed_fee_per_unit_judgement"].kind == "judgement"
