"""ADU scenario: method invariants that must hold for any data and any seed."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.features import brackets as br
from visionhousing.ledger import apply_overrides, load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios.adu import (
    STAGES,
    adu_funnel,
    load_fit_table,
    load_legal_share,
    scope_from_scenario,
)

TRACTS = Path(__file__).resolve().parent / "fixtures" / "testland" / "tracts.csv"
DRAWS = 300
UNIT_COLUMNS = ["units_1_detached", "units_1_attached", "units_2", "units_3_4"]


@pytest.fixture(scope="module")
def env():
    ledger = load_ledger()
    return {
        "ledger": ledger,
        "scenarios": load_scenarios(ledger),
        "fit": load_fit_table(),
        "legal": load_legal_share(),
        "tracts": pd.read_csv(TRACTS, dtype={"geoid": str, "county": str}),
    }


def net(env, tracts=None, sid="adu", seed=11, overrides=None, stage="net"):
    scenario = env["scenarios"][sid]
    led = apply_overrides(scenario_ledger(env["ledger"], scenario), overrides)
    result = adu_funnel(
        env["tracts"] if tracts is None else tracts,
        led,
        env["fit"],
        scope_from_scenario(sid, scenario, env["legal"]),
        draws=DRAWS,
        seed=seed,
    )
    return result.stages[stage] if stage else result


@pytest.mark.parametrize("sid", ["adu", "pa_hb2186", "pa_sb1346"])
def test_more_eligible_lots_never_reduce_units(env, sid):
    base = net(env, sid=sid)
    more = env["tracts"].copy()
    more[UNIT_COLUMNS] = more[UNIT_COLUMNS] * 1.5 + 10
    assert np.all(net(env, more, sid=sid) >= base - 1e-9)


@pytest.mark.parametrize(
    "pid",
    [
        "adu.hard_cost_per_sqft",
        "adu.site_utility_cost",
        "adu.permit_fee",
        "adu.utility_connection_fee",
        "adu.soft_cost_share",
        "econ.mortgage_rate_30y",
    ],
)
def test_a_higher_cost_never_raises_units(env, pid):
    low = net(env, overrides={pid: "low"})
    high = net(env, overrides={pid: "high"})
    assert np.all(high <= low + 1e-9)
    assert high.sum() < low.sum()


def test_a_higher_rent_never_lowers_units(env):
    base = net(env)
    richer = env["tracts"].copy()
    cols = list(br.RENT.columns)
    counts = richer[cols].to_numpy()
    shifted = np.zeros_like(counts)
    shifted[:, 1:] = counts[:, :-1]
    shifted[:, -1] += counts[:, -1]  # every renter moves up one bracket; the top stays on top
    richer[cols] = shifted
    for col in ("median_gross_rent", "median_rent_b1"):
        richer[col] = richer[col] * 1.25
    up = net(env, richer)
    assert np.all(up >= base - 1e-9)
    assert up.sum() > base.sum()


def test_zero_eligible_gives_zero(env):
    empty = env["tracts"].copy()
    empty[UNIT_COLUMNS] = 0
    result = net(env, empty, sid="pa_hb2186", stage=None)
    for stage in STAGES:
        assert np.all(result.stages[stage] == 0), stage
    assert result.records["net"].p90 == 0


def test_zero_take_up_gives_zero(env):
    assert np.all(net(env, overrides={"adu.takeup_feasible_annual": 0.0}) == 0)


def test_same_seed_same_result_and_a_new_seed_differs(env):
    a = net(env, seed=5, stage=None)
    b = net(env, seed=5, stage=None)
    for stage in STAGES:
        assert np.array_equal(a.stages[stage], b.stages[stage])
    assert a.records["net"] == b.records["net"]
    c = net(env, seed=6)
    assert not np.array_equal(a.stages["net"], c)


@pytest.mark.parametrize("sid", ["adu", "pa_hb2186", "pa_sb1346"])
def test_p10_p50_p90_are_ordered(env, sid):
    result = net(env, sid=sid, stage=None)
    for record in result.records.values():
        assert record.p10 <= record.p50 <= record.p90
    for record in result.county_records("feasible").values():
        assert record.p10 <= record.p50 <= record.p90


def test_sb1346_never_exceeds_hb2186(env):
    """SB 1346 reaches a subset of HB 2186's lots, so it never adds more units.

    SB 1346 also waives the utility connection fee, which HB 2186 does not. Draw
    by draw that waiver can lift SB 1346 above HB 2186 where few attached or
    2-4 unit lots fit, so the draw-by-draw check holds the fee treatment equal;
    the likely range (P10, P50, P90) is checked with each bill's own fees.
    """
    waived = {"adu.utility_connection_fee": 0.0}
    t = env["tracts"].copy()
    first, second = sorted(t["county"].unique())[:2]
    pa_like = t.assign(county=t["county"].replace({first: "42101", second: "42003"}))
    for tracts in (env["tracts"], pa_like):
        hb = net(env, tracts, sid="pa_hb2186", overrides=waived).sum(axis=1)
        sb = net(env, tracts, sid="pa_sb1346").sum(axis=1)
        assert np.all(sb <= hb + 1e-9)
        hb_own = net(env, tracts, sid="pa_hb2186", stage=None).records["net"]
        sb_own = net(env, tracts, sid="pa_sb1346", stage=None).records["net"]
        assert sb_own.p10 <= hb_own.p10
        assert sb_own.p50 <= hb_own.p50
        assert sb_own.p90 <= hb_own.p90


def test_benchmark_never_exceeds_hb2186(env):
    assert np.all(net(env, sid="adu") <= net(env, sid="pa_hb2186") + 1e-9)


def test_a_wider_already_legal_share_lowers_units(env):
    low = net(env, overrides={"adu.already_legal_share": "low"})
    high = net(env, overrides={"adu.already_legal_share": "high"})
    assert np.all(high <= low + 1e-9)
