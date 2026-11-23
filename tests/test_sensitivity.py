"""Sensitivity: rank correlation, tornado, and the top drivers on Testland."""

import itertools

import numpy as np
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute
from visionhousing.engine.montecarlo import BASELINE_STREAM
from visionhousing.ledger import parse_ledger
from visionhousing.sensitivity import (
    TOP,
    drivers,
    importance,
    pin_central,
    rankdata,
    spearman,
    tornado,
)

REFORMS = ["adu", "fees", "parking"]
DRAWS = 2000


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def by_seed(ctx):
    out = {}
    for seed in (0, 1, 2):
        res = compute(ctx, REFORMS, seed=seed, draws=DRAWS)
        out[seed] = {k: drivers(res, k) for k in REFORMS}
    return out


# ---------------------------------------------------------------- rank correlation


def test_rankdata_averages_ties():
    assert rankdata(np.array([10.0, 30.0, 20.0])).tolist() == [1, 3, 2]
    assert rankdata(np.array([5.0, 1.0, 5.0, 2.0])).tolist() == [3.5, 1, 3.5, 2]


def test_spearman_by_hand():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert spearman(x, x**3) == pytest.approx(1.0)
    assert spearman(x, -np.exp(x)) == pytest.approx(-1.0)
    assert np.isnan(spearman(x, np.ones(5)))
    # ranks (1,2,3,4,5) and (2,1,4,3,5): d^2 = 1+1+1+1+0 = 4; rho = 1 - 6*4/(5*24) = 0.8
    assert spearman(x, np.array([2.0, 1.0, 4.0, 3.0, 5.0])) == pytest.approx(0.8)
    with pytest.raises(ValueError):
        spearman(x, x[:3])


def test_importance_orders_by_size_and_drops_constants():
    rng = np.random.default_rng(0)
    a, b = rng.random(500), rng.random(500)
    y = 5 * a - b
    frame = importance({"a": a, "b": b, "c": np.ones(500)}, y, {"a": "Input A"})
    assert frame["parameter"].tolist() == ["a", "b"]
    assert frame.loc[0, "label"] == "Input A"
    assert frame.loc[1, "rho"] < 0
    assert frame["rank"].tolist() == [1, 2]


# ---------------------------------------------------------------- tornado


def toy_ledger():
    def entry(central, low, high, shape="pert"):
        return {
            "label": "toy",
            "unit": "ratio",
            "central": central,
            "low": low,
            "high": high,
            "shape": shape,
            "kind": "judgement",
            "rationale": "test",
            "used_by": ["fees"],
            "revisit": "test",
            "status": "draft",
        }

    return parse_ledger(
        {
            "parameters": {
                "toy.a": entry(1.0, 0.0, 4.0),
                "toy.b": entry(2.0, 1.0, 3.0, "triangular"),
                "toy.c": entry(5.0, 5.0, 5.0, "fixed"),
            }
        }
    )


def test_tornado_swings_one_input_with_the_others_central():
    ledger = toy_ledger()
    seen = []

    def evaluate(led):
        assert all(led[p].shape == "fixed" for p in led)
        seen.append((led["toy.a"].central, led["toy.b"].central))
        return 10 * led["toy.a"].central + led["toy.b"].central + led["toy.c"].central

    frame = tornado(evaluate, ledger, ["toy.a", "toy.b", "toy.c", "toy.zzz"])
    assert frame["parameter"].tolist() == ["toy.a", "toy.b"]
    assert frame["swing"].tolist() == pytest.approx([40.0, 2.0])
    row = frame.iloc[0]
    assert (row.total_low, row.total_central, row.total_high) == pytest.approx((7, 17, 47))
    assert (0.0, 2.0) in seen and (4.0, 2.0) in seen and (1.0, 3.0) in seen
    assert all(p.shape == "fixed" for _, p in pin_central(ledger).items())


def test_engine_tornado_matches_the_fee_modules_own_swing_table(ctx, by_seed):
    """The generic tornado reproduces the fee module's own one-at-a-time table."""
    mine = by_seed[0]["fees"].tornado.set_index("parameter")
    res = compute(ctx, "fees", seed=0, draws=50)
    theirs = res.reforms["fees"].result.detail.swing.set_index("parameter")
    for pid in theirs.index:
        assert mine.loc[pid, "swing"] == pytest.approx(theirs.loc[pid, "swing"], rel=1e-9), pid
    assert len(mine) == len(theirs) + 1  # plus the baseline-cycle row


# ---------------------------------------------------------------- drivers


def test_top_five_have_plain_labels_and_are_ranked(ctx, by_seed):
    ledger = ctx.ledger
    for key in REFORMS:
        d = by_seed[0][key]
        assert len(d.top) == TOP
        assert [x.rank for x in d.top] == [1, 2, 3, 4, 5]
        swings = [x.swing for x in d.top]
        assert swings == sorted(swings, reverse=True)
        for x in d.top:
            assert x.label and "." not in x.label.split()[0]
            if x.parameter != BASELINE_STREAM:
                assert x.label == ledger[x.parameter].label
            assert np.isfinite(x.rho)
            assert x.sentence().startswith(x.label)
        assert d.labels() == [x.label for x in d.top]


def test_tornado_top_five_is_stable_across_seeds(by_seed):
    for key in REFORMS:
        tops = [[x.parameter for x in by_seed[s][key].top] for s in by_seed]
        assert tops[0] == tops[1] == tops[2], key


def test_importance_agrees_with_itself_across_seeds(by_seed):
    for key in REFORMS:
        sets = [set(by_seed[s][key].importance.head(TOP)["parameter"]) for s in by_seed]
        for a, b in itertools.combinations(sets, 2):
            assert len(a & b) >= TOP - 1, key


def test_importance_confirms_the_tornado_leader(by_seed):
    for key in REFORMS:
        d = by_seed[0][key]
        leader = d.top[0].parameter
        top_imp = list(d.importance.head(3)["parameter"])
        assert leader in top_imp, key


def test_adu_does_not_depend_on_the_baseline_cycle(by_seed):
    rows = by_seed[0]["adu"].tornado.set_index("parameter")
    assert rows.loc[BASELINE_STREAM, "swing"] == 0
    fees = by_seed[0]["fees"].tornado.set_index("parameter")
    assert fees.loc[BASELINE_STREAM, "swing"] > 0


def test_drivers_needs_a_reform_in_the_result(ctx):
    res = compute(ctx, "adu", draws=20)
    with pytest.raises(KeyError, match="not in this result"):
        drivers(res, "fees")
