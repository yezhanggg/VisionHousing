"""Missing-middle scenario: method invariants that must hold for any data and any seed."""

import itertools
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.features import brackets as br
from visionhousing.ledger import apply_overrides, load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios.missing_middle import (
    STAGES,
    missing_middle_funnel,
    scope_from_scenario,
)

TRACTS = Path(__file__).resolve().parent / "fixtures" / "testland" / "tracts.csv"
DRAWS = 300
RENTS = ["median_rent_b2", "median_gross_rent"]


@pytest.fixture(scope="module")
def env():
    ledger = load_ledger()
    tracts = pd.read_csv(TRACTS, dtype={"geoid": str, "county": str})
    # Testland rents rarely pencil; doubled rents give every invariant something to bite on.
    rich = tracts.copy()
    rich[RENTS] = rich[RENTS] * 2.0
    return {"ledger": ledger, "scenarios": load_scenarios(ledger), "tracts": rich}


def result(env, tracts=None, seed=11, overrides=None, draws=DRAWS):
    scenario = env["scenarios"]["missing_middle"]
    led = apply_overrides(scenario_ledger(env["ledger"], scenario), overrides)
    return missing_middle_funnel(
        env["tracts"] if tracts is None else tracts,
        led,
        None,
        scope_from_scenario("missing_middle", scenario),
        draws=draws,
        seed=seed,
    )


def shift_values_up(tracts):
    """Move every owner home one value bracket up (the top bracket keeps its own)."""
    out = tracts.copy()
    counts = out[list(br.VALUE.columns)].to_numpy(dtype=float)
    moved = np.zeros_like(counts)
    moved[:, 1:] = counts[:, :-1]
    moved[:, -1] += counts[:, -1]
    out[list(br.VALUE.columns)] = moved
    out["median_home_value"] = out["median_home_value"] * 1.25
    return out


def test_baseline_has_something_to_test(env):
    assert result(env).records["net"].p50 > 0


def test_higher_land_value_never_raises_units(env):
    base = result(env)
    dearer = result(env, tracts=shift_values_up(env["tracts"]))
    for stage in ("feasible", "units", "net"):
        assert (dearer.stages[stage] <= base.stages[stage] + 1e-9).all()
    assert dearer.records["net"].p50 < base.records["net"].p50


def test_higher_rent_never_lowers_units(env):
    base = result(env)
    t = env["tracts"].copy()
    t[RENTS] = t[RENTS] * 1.15
    richer = result(env, tracts=t)
    for stage in ("feasible", "units", "net"):
        assert (richer.stages[stage] >= base.stages[stage] - 1e-9).all()
    assert richer.records["net"].p50 > base.records["net"].p50


def test_higher_cost_and_fees_never_raise_units(env):
    base = result(env)
    t = env["tracts"].assign(cost_factor=1.2)
    assert (result(env, tracts=t).stages["net"] <= base.stages["net"] + 1e-9).all()
    cut = result(env, overrides={"fees.fee_total_per_unit": 25_000})
    assert (cut.stages["net"] >= base.stages["net"] - 1e-9).all()


def test_zero_zoning_share_gives_zero(env):
    res = result(env, overrides={"missing_middle.sf_only_zoned_share": 0})
    for stage in STAGES:
        assert (res.stages[stage] == 0).all(), stage
    assert res.records["net"].p90 == 0


def test_zero_lots_or_zero_hazard_give_zero(env):
    t = env["tracts"].assign(units_1_detached=0)
    assert (result(env, tracts=t).stages["net"] == 0).all()
    res = result(env, overrides={"missing_middle.redevelopment_hazard_annual": 0})
    assert (res.stages["net"] == 0).all()


def test_gross_and_net_follow_demolition_and_replacement(env):
    res = result(env)
    s = res.stages
    removed, on_sites = res.removed, res.baseline_on_sites
    assert removed == pytest.approx(s["redeveloped"])  # one home demolished per lot
    assert (on_sites <= s["redeveloped"] + 1e-9).all()  # q <= 1, one home per teardown
    assert (s["authorized"] >= s["net"] - 1e-9).all()  # gross >= net where homes are removed
    assert (s["authorized"] <= s["units"] + 1e-9).all()
    assert (s["units"] >= 2 * s["redeveloped"] - 1e-9).all()
    assert (s["units"] <= 6 * s["redeveloped"] + 1e-9).all()


def test_funnel_narrows_at_every_step(env):
    s = result(env).stages
    order = ["eligible", "fit", "feasible", "redeveloped"]
    for a, b in itertools.pairwise(order):
        assert (s[b] <= s[a] + 1e-9).all(), (a, b)
    assert (s["authorized"] <= s["units"] + 1e-9).all()
    assert (s["net"] <= s["authorized"] + 1e-9).all()
    assert (s["net"] >= 0).all()


def test_shares_stay_between_zero_and_one(env):
    res = result(env)
    assert ((res.feasible_share >= 0) & (res.feasible_share <= 1)).all()
    for p in res.feasible_share_by_type.values():
        assert ((p >= 0) & (p <= 1)).all()
    total = sum(res.mix_weights.values())
    assert total == pytest.approx(np.ones(DRAWS))


def test_bigger_buildings_have_higher_land_value(env):
    rlv = result(env).residual_land_value
    for k in (2, 3, 4, 5):
        assert (np.sign(rlv[k + 1]) == np.sign(rlv[k])).all()
        assert (np.abs(rlv[k + 1]) >= np.abs(rlv[k])).all()  # every line is linear in units


def test_percentiles_are_ordered(env):
    res = result(env)
    for record in res.records.values():
        assert record.p10 <= record.p50 <= record.p90, record.name


def test_same_seed_same_result(env):
    a, b = result(env, seed=5), result(env, seed=5)
    for stage in STAGES:
        np.testing.assert_array_equal(a.stages[stage], b.stages[stage])
    assert a.records["net"] == b.records["net"]
    c = result(env, seed=6)
    assert not np.array_equal(a.stages["net"], c.stages["net"])
