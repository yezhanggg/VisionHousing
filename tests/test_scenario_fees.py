"""Fee reform (benchmark 5): golden case, method invariants, cross-check, records, tornado."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import baseline_from_ledger
from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.features.brackets import RENT, VALUE
from visionhousing.ledger import load_ledger
from visionhousing.scenarios.fees import (
    FEE_IDS,
    baseline_by_type,
    channel_b,
    fee_reform,
    market_from_tracts,
)

TESTLAND = Path(__file__).parent / "fixtures" / "testland"
DRAWS = 400


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def baseline_result(ledger):
    result, _ = baseline_from_ledger(pd.read_csv(TESTLAND / "permits_county_year.csv"), ledger)
    return result


@pytest.fixture(scope="module")
def baseline(baseline_result):
    return baseline_by_type(baseline_result)


@pytest.fixture(scope="module")
def tracts():
    return pd.read_csv(TESTLAND / "tracts.csv")


@pytest.fixture(scope="module")
def central(baseline, tracts, ledger):
    return fee_reform(baseline, tracts, ledger, n_draws=DRAWS, seed=1)


def run(baseline, tracts, ledger, **kw):
    kw.setdefault("n_draws", DRAWS)
    kw.setdefault("seed", 1)
    return fee_reform(baseline, tracts, ledger, **kw)


# ---------------------------------------------------------------- tiny hand-checkable state


def tiny_tracts() -> pd.DataFrame:
    """One county, one tract: every owner home at $250,000-299,999, every rent $1,000-1,249."""
    row = dict.fromkeys([*VALUE.columns, *RENT.columns, "rent_no_cash"], 0)
    row.update(geoid="01001000100", county="01001", housing_units=200)
    row["value_250000_299999"] = 100  # median = 250,000 + 0.5 x 50,000 = 275,000
    row["rent_1000_1249"] = 100  # median = 1,000 + 0.5 x 250 = 1,125
    return pd.DataFrame([row])


def tiny_baseline(units_1=1000.0, units_5plus=600.0) -> pd.DataFrame:
    return pd.DataFrame(
        [[units_1, 0.0, 0.0, units_5plus]], index=["01001"], columns=list(STRUCTURE_TYPES)
    )


PINS = {
    "supply.elasticity_medium_run": 1.0,
    "fees.passthrough_k": 0.5,
    "supply.new_construction_value_premium": 0.2,
    "multifamily.cap_rate": 0.055,
    "multifamily.operating_cost_share": 0.38,
    "missing_middle.cap_rate": 0.06,
    "missing_middle.operating_cost_share": 0.35,
}


def test_tiny_market_medians_are_hand_checkable():
    m = market_from_tracts(tiny_tracts(), ["01001"])
    assert m.median_value[0] == pytest.approx(275_000)
    assert m.median_rent[0] == pytest.approx(1_125)


def test_golden_fee_case(ledger):
    r = fee_reform(tiny_baseline(), tiny_tracts(), ledger, n_draws=50, overrides=PINS)
    # 1-unit: 1,000 x 1 x ($50,000 x 0.5 / ($275,000 x 1.2)) x 0.5 = 37.8788
    one = 1000 * 1.0 * (25_000 / 330_000) * 0.5
    # 5+: value = $1,125 x 1.2 x 12 x (1 - 0.38) / 0.055 = $182,618.18
    v5 = 1125 * 1.2 * 12 * (1 - 0.38) / 0.055
    five = 600 * 1.0 * (25_000 / v5) * 0.5
    assert one == pytest.approx(37.878787878, rel=1e-9)
    assert v5 == pytest.approx(182_618.1818, rel=1e-9)
    assert r.state.p10 == r.state.p50 == r.state.p90
    assert r.state.p50 == pytest.approx(one + five, rel=1e-12)
    assert r.by_type["units_1"].p50 == pytest.approx(one, rel=1e-12)
    assert r.by_type["units_5plus"].p50 == pytest.approx(five, rel=1e-12)
    assert r.by_type["units_2"].p50 == 0.0
    # The funnel at central values reproduces the same total exactly.
    steps = {s.id: s.record for s in r.steps}
    assert steps["saving"].point() == 25_000
    assert steps["added"].point() == pytest.approx(one + five, rel=1e-12)


def test_channel_b_formula_by_hand():
    out = channel_b(np.array([200.0]), 1.5, 10_000.0, 250_000.0, 0.4)
    assert out[0] == pytest.approx(200 * 1.5 * 0.04 * 0.4)


def test_channel_b_rejects_bad_inputs():
    with pytest.raises(ValueError, match="cannot be negative"):
        channel_b(1.0, 1.0, -1.0, 1.0, 0.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        channel_b(1.0, 1.0, 1.0, 1.0, 1.5)
    with pytest.raises(ValueError, match="must be positive"):
        channel_b(np.array([1.0]), 1.0, 1.0, np.array([0.0]), 0.5)
    # No baseline: no value needed, no effect.
    assert channel_b(np.array([0.0]), 1.0, 1.0, np.array([np.nan]), 0.5)[0] == 0.0


# ---------------------------------------------------------------- method invariants


def test_no_reform_gives_baseline_exactly(baseline, tracts, ledger):
    r = run(baseline, tracts, ledger, overrides={"fees.fee_cut_share": 0.0})
    assert np.all(r.added == 0.0)
    base = baseline.to_numpy()[:, :, None]
    assert np.array_equal(base + r.added, np.broadcast_to(base, r.added.shape))
    assert r.state.p90 == 0.0
    assert r.crosscheck["state"].p90 == 0.0
    assert r.crosscheck_share.p90 == 0.0


@pytest.mark.parametrize("pid", ["fees.passthrough_k", "supply.elasticity_medium_run"])
def test_zero_k_or_zero_elasticity_gives_zero(baseline, tracts, ledger, pid):
    r = run(baseline, tracts, ledger, overrides={pid: 0.0})
    assert np.all(r.added == 0.0)
    if pid == "fees.passthrough_k":
        assert r.crosscheck["state"].p90 == 0.0


def test_lower_cost_never_reduces_units(baseline, tracts, ledger):
    previous = None
    for cut in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
        r = run(baseline, tracts, ledger, overrides={"fees.fee_cut_share": cut})
        assert np.all(r.added >= 0)
        if previous is not None:
            assert np.all(r.added >= previous.added)
            assert np.all(r.crosscheck["state"].p50 >= previous.crosscheck["state"].p50)
        previous = r


def test_ranges_are_ordered(central):
    records = [
        *central.counties.values(),
        central.state,
        *central.by_type.values(),
        *central.crosscheck.values(),
        central.crosscheck_share,
    ]
    for rec in records:
        assert rec.p10 <= rec.p50 <= rec.p90, rec.name


def test_same_seed_same_result(baseline, tracts, ledger, central):
    again = run(baseline, tracts, ledger)
    assert np.array_equal(again.added, central.added)
    assert again.state == central.state
    other = run(baseline, tracts, ledger, seed=2)
    assert not np.array_equal(other.added, central.added)


def test_counties_add_up_to_the_state_in_every_draw(central):
    per_county = central.added.sum(axis=1).sum(axis=0)
    assert np.allclose(per_county, central.state_draws())
    assert central.added.shape == (4, 4, DRAWS)


def test_one_county_alone_gives_the_same_draws(baseline, tracts, ledger, central):
    alone = run(baseline.iloc[[1]], tracts, ledger)
    assert np.allclose(alone.added[0], central.added[1])


def test_baseline_scale_is_carried_through(baseline, tracts, ledger, central):
    scale = np.full((4, DRAWS), 2.0)
    doubled = run(baseline, tracts, ledger, baseline_scale=scale)
    assert np.allclose(doubled.added, 2 * central.added)
    with pytest.raises(ValueError, match="baseline_scale"):
        run(baseline, tracts, ledger, baseline_scale=np.ones((3, DRAWS)))


def test_effect_is_a_plausible_small_share_of_baseline(baseline, central):
    share = central.state.p50 / baseline.to_numpy().sum()
    assert 0.01 < share < 0.15


# ---------------------------------------------------------------- views, records, tornado


def test_state_view_is_labelled_draft_and_smaller(baseline, tracts, ledger, central):
    state = run(baseline, tracts, ledger, view="state")
    assert state.view == "state"
    assert "fees.pa_assumed_fee_per_unit_judgement" in state.state.assumption_ids
    assert any("draft" in n for n in state.notes)
    assert state.state.p50 < central.state.p50
    with pytest.raises(ValueError, match="view"):
        run(baseline, tracts, ledger, view="other")


def test_records_carry_formula_ids_sources_and_tier(central):
    rec = central.state
    assert "e x (fees per unit x cut share / value per new unit) x k" in rec.formula
    assert set(FEE_IDS["benchmark"]) <= set(rec.assumption_ids)
    assert {"bps_county_annual", "acs5_summary_file", "handbook_benchmarks"} <= set(rec.source_ids)
    assert rec.tier == 0
    assert set(central.counties) == {"99001", "99003", "99005", "99007"}


def test_funnel_steps_are_in_order_with_formulas(central):
    ids = [s.id for s in central.steps]
    assert ids == [
        "saving",
        "baseline",
        "value",
        "ratio",
        "elasticity",
        "passthrough",
        "added",
    ]
    assert all(s.record.formula for s in central.steps)
    assert "fees.passthrough_k" in central.steps[-1].record.assumption_ids


def test_passthrough_tops_the_fee_tornado(central):
    assert central.swing.iloc[0]["parameter"] == "fees.passthrough_k"
    assert list(central.swing["swing"]) == sorted(central.swing["swing"], reverse=True)


def test_crosscheck_is_reported_side_by_side(central):
    cross = central.crosscheck["state"]
    assert cross.p50 > 0
    assert "pro-forma cross-check" in cross.name
    assert central.crosscheck_share.unit == "percentage points"
    assert 0 < central.crosscheck_share.p50 < 100


def test_baseline_by_type_adds_to_county_p50(baseline_result, baseline):
    for county, record in baseline_result.counties.items():
        assert baseline.loc[str(county)].sum() == pytest.approx(record.p50, rel=1e-12)
