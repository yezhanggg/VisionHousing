"""Paired readouts, cost per added home, variance shares and the frequency wording (Testland)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from visionhousing.engine import run
from visionhousing.engine.readouts import (
    NOT_DEFINED,
    THRESHOLD_IDS,
    chance_at_least,
    cost_per_added,
    cost_readouts,
    group_sums,
    width,
)
from visionhousing.explain import cited_texts, explain, numbers_in
from visionhousing.explain import wording as w
from visionhousing.ledger import load_ledger
from visionhousing.sensitivity import (
    first_order_share,
    first_order_shares,
    range_shrink,
    uncertainty,
)

REFORMS = ["adu", "missing_middle", "tod", "parking", "fees"]


@pytest.fixture(scope="module")
def result():
    return run("TL", REFORMS, seed=0)


@pytest.fixture(scope="module")
def explained(result):
    return explain(result, with_drivers=False)


# ---------------------------------------------------------------- pure helpers


def test_cost_per_added_home_is_undefined_at_zero():
    cost = cost_per_added(np.array([100.0, 50.0, 0.0]), np.array([2.0, 0.0, 0.0]))
    assert cost[0] == 50.0
    assert np.isnan(cost[1]) and np.isnan(cost[2])
    assert not np.isinf(cost).any()


def test_cost_record_says_not_defined_when_nothing_is_added():
    ledger = load_ledger()
    base = np.full((2, 50), 100.0)
    added = np.zeros((2, 50))
    from visionhousing.engine.records import quantity

    head = quantity("added units", 0.0, "units")
    recs, counties, texts = cost_readouts(
        "fee cut", head, added, base, ("1", "2"), np.full(50, 25000.0),
        fee_id="fees.fee_total_per_unit", ledger=ledger,
    )  # fmt: skip
    assert "cost_per_added_home" not in recs
    assert texts["cost_per_added_home"] == NOT_DEFINED
    assert not any(ch.isdigit() for ch in NOT_DEFINED)
    assert counties["cost_per_added_home"] == {}
    assert recs["revenue_given_up"].p50 == pytest.approx(2 * 100 * 25000)


def test_chance_and_width():
    draws = np.arange(1, 101, dtype=float)
    assert chance_at_least(draws, 51) == 0.5
    assert chance_at_least(draws, 0) == 1.0
    assert width(draws) == pytest.approx(np.percentile(draws, 90) - np.percentile(draws, 10))


def test_group_sums_add_up():
    arr = np.arange(12, dtype=float).reshape(3, 4)
    got = group_sums(arr, ("a", "b", "c"), {"a": "x", "b": "y", "c": "x"})
    assert np.array_equal(got["x"], arr[0] + arr[2])
    assert np.array_equal(got["y"], arr[1])


# ---------------------------------------------------------------- engine readouts


def test_baseline_and_reforms_share_their_draws(result):
    """The total is the baseline plus the package in the same draw (paired, not resampled)."""
    total = result.baseline_draws + result.package.state_draws
    assert result.total.p50 == pytest.approx(float(np.percentile(total, 50)))
    fees = result.reforms["fees"]
    pct = 100 * fees.state_draws / result.baseline_draws
    assert fees.pct_change.p50 == pytest.approx(float(np.percentile(pct, 50)))


def test_chance_records_follow_the_draws_and_the_ledger(result):
    ledger = result.draws_obj.ledger
    for key, o in result.reforms.items():
        for i, pid in enumerate(THRESHOLD_IDS, start=1):
            r = o.readouts[f"chance_at_least_{i}"]
            want = float(np.mean(o.state_draws >= ledger[pid].central))
            assert r.value == want, key
            assert pid in r.assumption_ids and r.formula


def test_paired_effect_is_tighter_than_either_level(result):
    """The paired difference is narrower than the baseline, the total and unpaired draws.

    Added homes are the total minus the baseline in the same draw.
    """
    rng = np.random.default_rng(0)
    base = np.asarray(result.baseline_draws)
    for key, o in result.reforms.items():
        added = np.asarray(o.state_draws)
        total = base + added
        assert width(added) < width(base), key
        assert width(added) < width(total), key
        unpaired = rng.permutation(total) - base
        assert width(added) < width(unpaired), key
        assert o.readouts["added_width"].value == pytest.approx(width(added))
        assert o.readouts["baseline_width"].value == pytest.approx(width(base))


def test_share_of_baseline_by_county(result):
    o = result.reforms["fees"]
    by = o.readout_counties["share_of_baseline"]
    assert set(by) == set(result.county_ids)
    for i, c in enumerate(result.county_ids):
        base = result.baseline_county_draws[i]
        share = np.where(base > 0, 100 * o.county_draws[i] / np.where(base > 0, base, 1), 0)
        assert by[c].p50 == pytest.approx(float(np.percentile(share, 50)))


def test_fee_revenue_counties_sum_to_state_in_every_draw(result):
    o = result.reforms["fees"]
    params = result.draws_obj.ledger
    cut = params["fees.fee_total_per_unit"].central * params["fees.fee_cut_share"].central
    rev_c = cut * (result.baseline_county_draws + o.county_draws)
    state = rev_c.sum(axis=0)
    assert np.allclose(state, cut * (result.baseline_draws + o.state_draws))
    r = o.readouts["revenue_given_up"]
    assert r.p50 == pytest.approx(float(np.percentile(state, 50)))
    counties = o.readout_counties["revenue_given_up"]
    for i, c in enumerate(result.county_ids):
        assert counties[c].p50 == pytest.approx(float(np.percentile(rev_c[i], 50)))
    cost = cost_per_added(state, o.state_draws)
    ok = np.isfinite(cost)
    assert o.readouts["cost_per_added_home"].p50 == pytest.approx(
        float(np.percentile(cost[ok], 50))
    )


def test_targeted_fee_cut_uses_the_county_draws(result):
    o = result.reforms["fees"]
    types = result.run_context.state.county_types
    assert types, "Testland's county groups are worked out from its counties table"
    targeted = [k for k in o.readouts if k.startswith("cost_per_added_home_targeted_")]
    assert len(targeted) == len(set(types.values()))
    for k in targeted:
        assert o.readouts[k].p50 > 0 and math.isfinite(o.readouts[k].p90)


def test_state_fee_variant_is_labelled(result):
    o = result.reforms["fees"]
    r = o.readouts["cost_per_added_home_state_fees"]
    assert "fees.pa_assumed_fee_per_unit_judgement" in r.assumption_ids
    assert "state fee level (draft judgement)" in r.name


def test_parking_saving_is_spaces_times_cost(result):
    r = result.reforms["parking"].readouts
    assert r["private_saving_per_home"].p50 > 0
    assert "parking.cost_per_space_surface" in r["cost_per_space_avoided"].assumption_ids
    by = result.reforms["parking"].readout_counties["private_saving_total"]
    assert set(by) == set(result.county_ids)


def test_other_reforms_say_no_direct_public_cost(result):
    for key in ("adu", "missing_middle", "tod"):
        text = result.reforms[key].readout_texts["cost"]
        assert text.startswith("No direct public cost") and "not modelled" in text
        assert not any(ch.isdigit() for ch in text)
    assert "infrastructure" in result.reforms["tod"].readout_texts["cost"]


def test_readouts_are_in_the_json_and_the_record_list(result):
    data = result.to_dict()
    fees = data["reform_results"]["fees"]
    assert fees["readouts"]["cost_per_added_home"]["formula"]
    assert fees["readout_counties"]["revenue_given_up"]
    names = {r.name for r in result.records()}
    assert result.reforms["fees"].readouts["cost_per_added_home"].name in names


# ---------------------------------------------------------------- variance shares


def test_known_linear_model_variance_shares():
    rng = np.random.default_rng(1)
    n = 20_000
    x1, x2, x3 = rng.uniform(0, 1, n), rng.uniform(0, 1, n), rng.uniform(0, 1, n)
    y = 3 * x1 + 1 * x2 + rng.normal(0, 0.3, n)
    var1, var2 = 9 / 12, 1 / 12
    total = var1 + var2 + 0.09
    shares = first_order_shares({"x1": x1, "x2": x2, "x3": x3}, y, bins=10)
    assert shares["x1"] == pytest.approx(var1 / total, abs=0.03)
    assert shares["x2"] == pytest.approx(var2 / total, abs=0.03)
    assert shares["x3"] < 0.01  # irrelevant input scores near zero
    assert next(iter(shares)) == "x1"


def test_variance_share_edge_cases():
    assert math.isnan(first_order_share(np.ones(100), np.arange(100.0)))
    assert math.isnan(first_order_share(np.arange(100.0), np.ones(100)))
    assert range_shrink(0.0) == 0.0
    assert range_shrink(1.0) == 1.0
    assert range_shrink(0.36) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        first_order_share(np.arange(5.0), np.arange(5.0), bins=10)


def test_uncertainty_of_engine_reforms(result):
    for key in ("adu", "fees", "parking"):
        n = uncertainty(result, key)
        shares = [s.share.value for s in n.items]
        assert shares == sorted(shares, reverse=True)
        assert all(0 <= s <= 1 for s in shares)
        assert n.leftover.value == pytest.approx(max(0.0, 1 - sum(shares)))
        assert n.bins == 10 and n.draws == result.draws
    # ADUs do not depend on the baseline cycle: its share is near zero.
    adu = {s.parameter: s.share.value for s in uncertainty(result, "adu").items}
    assert adu.get("engine.baseline_cycle_position", 0.0) < 0.02


# ---------------------------------------------------------------- sentences


def test_frequency_wording_uses_the_engine_percentiles(result, explained):
    for key, block in explained["readouts"].items():
        o = result.reforms[key]
        text = block["frequency"]["text"]
        assert text.startswith(w.FREQUENCY_LEAD)
        fields = {c["field"]: c["value"] for c in block["frequency"]["cites"]}
        if fields:
            assert fields["p10"] == o.added.p10 and fields["p90"] == o.added.p90
    assert explained["headline"]["frequency"]["text"].startswith(w.FREQUENCY_LEAD)


def test_adu_wording_carries_the_permit_caveat(explained):
    assert w.ADU_MEASUREMENT in explained["readouts"]["adu"]["frequency"]["text"]
    assert w.ADU_MEASUREMENT in explained["readouts"]["adu"]["added_label"]["text"]
    assert w.ADU_MEASUREMENT not in explained["readouts"]["fees"]["frequency"]["text"]
    assert "permit statistics" in explained["headline"]["numbers"][1]["label"]["text"]


def test_paired_sentence_and_agenda(explained):
    paired = explained["readouts"]["fees"]["paired"]["text"]
    assert paired.startswith("The future level of building is uncertain")
    agenda = explained["narrowing"]["fees"]["agenda"]
    assert agenda and all(a["text"].startswith("Knowing ") for a in agenda)
    assert len(agenda) <= 3


def test_every_new_sentence_is_cited(explained):
    """No typed number: every digit in the new sentences is covered by a cite."""
    from visionhousing.explain import uncited

    blocks = [explained["readouts"], explained["narrowing"], explained["headline"]["frequency"]]
    texts = list(cited_texts(blocks))
    assert texts
    for t in texts:
        assert uncited(t["text"], t["cites"]) == [], t["text"]
    assert any(numbers_in(t["text"]) for t in texts)


def test_cost_per_added_home_needs_the_ledger_minimum_share_of_draws():
    from visionhousing.engine.readouts import MIN_SHARE, not_defined_text
    from visionhousing.engine.records import quantity

    ledger = load_ledger()
    floor = float(ledger[MIN_SHARE].central)
    base = np.full((1, 100), 100.0)
    head = quantity("added units", 0.0, "units")
    few = np.zeros((1, 100))
    few[0, : int(floor * 100) - 1] = 5.0  # just under the minimum share add homes
    recs, _, texts = cost_readouts(
        "fee cut", head, few, base, ("1",), np.full(100, 25000.0),
        fee_id="fees.fee_total_per_unit", ledger=ledger,
    )  # fmt: skip
    assert "cost_per_added_home" not in recs
    assert texts["cost_per_added_home"] == not_defined_text((floor * 100 - 1) / 100, floor)
    assert MIN_SHARE in texts["cost_per_added_home"]
    enough = np.zeros((1, 100))
    enough[0, : int(floor * 100) + 10] = 5.0
    recs, _, texts = cost_readouts(
        "fee cut", head, enough, base, ("1",), np.full(100, 25000.0),
        fee_id="fees.fee_total_per_unit", ledger=ledger,
    )  # fmt: skip
    r = recs["cost_per_added_home"]
    assert f"homes added in {(floor * 100 + 10) / 100:.0%} of the modeled draws" in r.name
    assert MIN_SHARE in r.assumption_ids
    assert "cost_per_added_home" not in texts
    assert not_defined_text(0.0, floor) == NOT_DEFINED
