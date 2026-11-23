"""Transit-oriented density: distance choice, zero upzoning, ranges, seeds, package rules."""

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute, prepare
from visionhousing.engine.packages import TodPackage, tod_middle_overlap, tod_package
from visionhousing.engine.registry import get_reform
from visionhousing.ledger import apply_overrides, scenario_ledger
from visionhousing.scenarios.tod import (
    DISTANCES,
    M2_PER_ACRE,
    STAGES,
    TodScope,
    distance_suffix,
    scope_from_scenario,
    tod_funnel,
)

DRAWS = 400

# Testland's rents are too low for mid-rise to pencil at central costs. Rich rents
# and cheap construction make every later stage visible.
RICH = {
    "multifamily.hard_cost_per_sqft": 120,
    "tod.new_unit_rent_premium": 2.5,
    "tod.acquisition_premium": 0.05,
}


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def tl_tracts(ctx):
    """Testland tracts with the transit columns (worked out by the adapter)."""
    from visionhousing.engine.adapters import _tod_tracts

    run_context, draws = prepare(ctx, None, seed=0, draws=10)
    return _tod_tracts(run_context, draws.ledger)


def run(ctx, tracts, *, distance="d07", overrides=None, seed=3, draws=DRAWS, cap=None):
    scenario = ctx.scenarios["tod"]
    led = apply_overrides(scenario_ledger(ctx.ledger, scenario), {**RICH, **(overrides or {})})
    scope = scope_from_scenario("tod", scenario, distance)
    return tod_funnel(tracts, led, scope, mf_baseline=cap, draws=draws, seed=seed)


def test_distance_choices_and_suffixes():
    assert distance_suffix(0.25) == "0_25mi"
    assert distance_suffix("0.5") == "0_50mi"
    assert distance_suffix("D07") == "d07"
    assert len(DISTANCES) == 4
    with pytest.raises(ValueError, match="distance"):
        TodScope("tod", 1.0)


def test_wider_distance_gives_more_eligible_land(ctx, tl_tracts):
    land = [run(ctx, tl_tracts, distance=d).records["eligible"].p50 for d in (0.25, 0.5, 0.75)]
    homes = [
        run(ctx, tl_tracts, distance=d).records["eligible_homes"].p50 for d in (0.25, 0.5, 0.75)
    ]
    assert 0 < land[0] < land[1] < land[2]
    assert homes[0] <= homes[1] <= homes[2]
    assert homes[2] > homes[0]


def test_wider_distance_gives_more_eligible_land_in_pennsylvania_columns():
    """The same order holds for the state-pack columns (nested rings, block by block)."""
    pa = pd.DataFrame(
        {
            "transit_land_m2_0_25mi": [10.0, 0.0],
            "transit_land_m2_0_50mi": [30.0, 5.0],
            "transit_land_m2_0_75mi": [55.0, 9.0],
        }
    )
    sums = [pa[f"transit_land_m2_{distance_suffix(d)}"].sum() for d in (0.25, 0.5, 0.75)]
    assert sums == sorted(sums)


def test_the_funnel_narrows_and_units_follow(ctx, tl_tracts):
    res = run(ctx, tl_tracts, distance=0.75)
    s = res.stages
    assert np.all(s["developable"] <= s["eligible"] + 1e-9)
    assert np.all(s["feasible"] <= s["developable"] + 1e-9)
    assert np.all(s["absorbed"] <= s["built"] + 1e-9)
    assert np.all(s["built"] <= s["capacity"] + 1e-9)
    assert np.all(s["net"] <= s["absorbed"] + 1e-9)
    assert res.records["net"].p90 > 0
    assert [r.name for r in res.funnel()] == [res.records[k].name for k in STAGES]
    assert res.records["eligible"].unit == "acres"
    acres = res.stages["eligible"][0].sum()
    assert acres == pytest.approx(tl_tracts["transit_land_m2_0_75mi"].sum() / M2_PER_ACRE)


@pytest.mark.parametrize(
    "zero",
    [{"tod.upzoned_far": 0.0}, {"tod.upzoned_far": 0.8, "tod.current_far_allowed": 0.8}],
    ids=["far_zero", "far_equal_to_today"],
)
def test_zero_upzoning_gives_zero(ctx, tl_tracts, zero):
    fixed = {k: {"central": v, "low": v, "high": v, "shape": "fixed"} for k, v in zero.items()}
    res = run(ctx, tl_tracts, distance=0.75, overrides=fixed)
    assert res.records["eligible"].p50 > 0
    for stage in ("capacity", "built", "absorbed", "net"):
        assert np.all(res.stages[stage] == 0)


def test_ranges_are_ordered(ctx, tl_tracts):
    res = run(ctx, tl_tracts, distance=0.5)
    for r in res.records.values():
        assert r.p10 <= r.p50 <= r.p90


def test_same_seed_same_result_and_other_seed_differs(ctx, tl_tracts):
    a = run(ctx, tl_tracts, seed=5)
    b = run(ctx, tl_tracts, seed=5)
    c = run(ctx, tl_tracts, seed=6)
    for stage in STAGES:
        assert np.array_equal(a.stages[stage], b.stages[stage])
    assert not np.array_equal(a.stages["net"], c.stages["net"])


def test_absorption_cap_binds_and_zero_cap_gives_zero(ctx, tl_tracts):
    free = run(ctx, tl_tracts, distance=0.75)
    counties = sorted(set(tl_tracts["county"].astype(str)))
    tight = run(ctx, tl_tracts, distance=0.75, cap=pd.Series(1.0, index=counties))
    none = run(ctx, tl_tracts, distance=0.75, cap=pd.Series(0.0, index=counties))
    assert np.all(tight.stages["absorbed"] <= free.stages["absorbed"] + 1e-9)
    assert tight.records["absorbed"].p90 <= len(counties) * 1.0 + 1e-9
    assert np.all(none.stages["net"] == 0)
    assert max(tight.capped_share.values()) > 0


def test_net_new_share_scales_the_headline(ctx, tl_tracts):
    full = {"central": 1.0, "low": 1.0, "high": 1.0, "shape": "fixed"}
    half = {"central": 0.5, "low": 0.5, "high": 0.5, "shape": "fixed"}
    a = run(ctx, tl_tracts, overrides={"tod.net_new_share": full})
    b = run(ctx, tl_tracts, overrides={"tod.net_new_share": half})
    assert np.allclose(b.stages["net"], 0.5 * a.stages["net"])
    assert "tod.net_new_share" in a.records["net"].assumption_ids


def test_reduced_parking_raises_feasibility(ctx, tl_tracts):
    none_kept = {"central": 0.0, "low": 0.0, "high": 0.0, "shape": "fixed"}
    all_kept = {"central": 1.0, "low": 1.0, "high": 1.0, "shape": "fixed"}
    lean = run(ctx, tl_tracts, overrides={"parking.share_still_built_transit_rich": none_kept})
    full = run(ctx, tl_tracts, overrides={"parking.share_still_built_transit_rich": all_kept})
    assert np.all(lean.feasible_share >= full.feasible_share - 1e-12)
    assert np.all(lean.residual_per_unit > full.residual_per_unit)


def test_scope_needs_the_tod_reform(ctx):
    with pytest.raises(ValueError, match="does not turn on"):
        scope_from_scenario("adu", ctx.scenarios["adu"])


# ---------------------------------------------------------------- engine and package


def test_tod_is_registered_and_runs_in_the_engine(ctx):
    assert get_reform("tod").family == "tod"
    res = compute(ctx, {"tod": {"distance": 0.5}}, RICH, seed=2, draws=200)
    out = res.reforms["tod"]
    assert out.result.level == "tract"
    assert len(out.steps) == len(STAGES)
    assert np.all(out.state_draws >= 0)
    assert out.added.p90 > 0
    again = compute(ctx, {"tod": {"distance": 0.5}}, RICH, seed=2, draws=200)
    assert np.array_equal(out.state_draws, again.reforms["tod"].state_draws)


def test_engine_distance_option_orders_eligible_land(ctx):
    land = []
    for d in (0.25, 0.5, 0.75):
        res = compute(ctx, {"tod": {"distance": d}}, RICH, seed=2, draws=50)
        land.append(res.reforms["tod"].steps[0].p50)
    assert land[0] < land[1] < land[2]


CHEAP_ALL = {**RICH, "missing_middle.hard_cost_per_sqft": 100}


@pytest.fixture(scope="module")
def all_five(ctx):
    reforms = {
        "adu": {},
        "missing_middle": {},
        "tod": {"distance": 0.75},
        "parking": {},
        "fees": {},
    }
    return compute(ctx, reforms, CHEAP_ALL, seed=11, draws=DRAWS)


def test_package_invariant_holds_with_tod_on(all_five):
    pkg = all_five.package
    parts = sum(o.state_draws for o in all_five.reforms.values())
    assert np.all(pkg.state_draws <= parts + pkg.interaction_draws + 1e-9)
    assert np.allclose(pkg.state_draws, parts - pkg.overlap_draws + pkg.interaction_draws)
    assert not any("tod" in name for name in pkg.interactions)  # parking is already inside TOD
    adjusted = tod_package(all_five)
    assert isinstance(adjusted, TodPackage) and adjusted.applied
    assert np.all(adjusted.overlap_draws >= 0)
    assert adjusted.overlap.p90 > 0
    assert np.all(adjusted.state_draws <= pkg.state_draws + 1e-9)
    assert np.all(adjusted.state_draws <= parts + pkg.interaction_draws + 1e-9)
    middle = all_five.reforms["missing_middle"].state_draws
    assert np.all(adjusted.overlap_draws + pkg.overlap_draws <= middle + 1e-9)
    assert adjusted.combined.p10 <= adjusted.combined.p50 <= adjusted.combined.p90


def test_tod_overlap_flag_off_changes_nothing(all_five):
    off = tod_package(all_five, tod_wins=False)
    assert not off.applied
    assert np.all(off.overlap_draws == 0)
    assert np.array_equal(off.state_draws, all_five.package.state_draws)


def test_tod_overlap_is_zero_without_missing_middle(ctx):
    res = compute(ctx, ["tod", "parking"], RICH, seed=1, draws=100)
    out = tod_package(res)
    assert not out.applied and np.all(out.overlap_draws == 0)


def test_tod_middle_overlap_is_bounded_by_middle(all_five):
    middle = all_five.reforms["missing_middle"]
    inside = tod_middle_overlap(middle.result, all_five.reforms["tod"].result)
    assert np.all(inside >= 0)
    assert np.all(inside <= middle.state_draws + 1e-9)
