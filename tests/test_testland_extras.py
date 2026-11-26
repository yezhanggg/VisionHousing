"""The extra scenarios on Testland's own optional tables, at central costs.

Testland carries small synthetic versions of the four optional pack tables
(readiness, hazards, parcel land use, income limits; made by
``scripts/make_testland.py``). These tests check that each extra scenario reads
them, and that each one either shows a range above zero or is zero for a
stated economic reason that a market-sensitivity run confirms. They also check
that the five benchmark reforms read none of the new tables.
"""

import numpy as np
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute
from visionhousing.scenarios.extra_common import OPTIONAL_TABLES

DRAWS = 200
INFRA = "transit_zoning_funded_infrastructure"
BENCHMARKS = ["adu", "missing_middle", "tod", "parking", "fees"]
# The market-sensitivity overrides the TOD and extra-scenario tests already use.
RICH = {
    "multifamily.hard_cost_per_sqft": 120,
    "tod.new_unit_rent_premium": 2.5,
    "tod.acquisition_premium": 0.05,
}
CHEAP_LOTS = {
    "missing_middle.hard_cost_per_sqft": 90,
    "starter.subdivision_cost_per_lot": 10000,
    "fees.fee_total_per_unit": {"central": 5000, "low": 5000, "high": 5000, "shape": "fixed"},
    "starter.small_home_value_ratio": 0.9,
}


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def bare():
    c = load_context("TL")
    for name in OPTIONAL_TABLES:
        c.derived[("pack_table", name)] = None
    return c


def one(ctx, key, overrides=None):
    return compute(ctx, [key], overrides, draws=DRAWS, seed=0).reforms[key]


def test_testland_has_every_optional_table(ctx):
    from visionhousing.scenarios.extra_common import pack_table

    for name in OPTIONAL_TABLES:
        table = pack_table(ctx, name)
        assert table is not None and not table.empty, name


def test_adaptive_reuse_counts_conversions_outside_the_permit_total(ctx):
    o = one(ctx, "adaptive_reuse")
    assert o.added.p90 == 0.0  # alterations are not in the permit survey, by design
    assert o.records["conversion_homes"].p90 > 0
    assert o.records["measured_tracts"].value > 0


def test_fee_deferral_uses_the_income_limits(ctx):
    o = one(ctx, "fee_deferral_with_affordability")
    assert o.added.p90 > 0
    assert not any("no HUD income-limits table" in n for n in o.notes)


def test_funded_transit_zoning_reads_readiness_and_is_capped_by_the_tod_benchmark(ctx):
    o = one(ctx, INFRA)
    assert o.records["readiness_verified"].value > 0
    assert o.records["readiness_outside"].value > 0
    assert not any("no water and sewer readiness table" in n for n in o.notes)
    if o.added.p90 == 0:
        # Reason: the TOD benchmark itself adds nothing on Testland at central costs
        # (rents too low for mid-rise apartments), and the grant total never exceeds it.
        assert one(ctx, "tod").added.p90 == 0
        assert one(ctx, INFRA, RICH).added.p90 > 0


def test_commercial_land_reads_parcels_and_zero_means_it_does_not_pencil(ctx):
    o = one(ctx, "commercial_land_housing")
    assert o.records["measured_tracts"].value > 0
    assert o.records["eligible"].p50 > 0
    if o.added.p90 == 0:
        # Reason: a walk-up's residual land value does not cover the site anywhere.
        assert o.records["feasible"].p90 == 0
        assert one(ctx, "commercial_land_housing", RICH).added.p90 > 0


def test_starter_lots_read_sewer_and_zero_means_homes_cost_more_than_they_sell_for(ctx, bare):
    o = one(ctx, "starter_home_lots")
    assert not any("No water and sewer readiness table" in n for n in o.notes)
    # Lots outside public sewer are left out, so fewer lots are eligible than without the table.
    assert o.records["eligible"].p50 < one(bare, "starter_home_lots").records["eligible"].p50
    if o.added.p90 == 0:
        # Reason: Testland's home values sit below a starter home's cost at central inputs.
        assert o.records["feasible"].p90 == 0
        assert one(ctx, "starter_home_lots", CHEAP_LOTS).added.p90 > 0


def test_benchmarks_read_none_of_the_optional_tables(ctx, bare):
    with_tables = compute(ctx, BENCHMARKS, draws=DRAWS, seed=0)
    without = compute(bare, BENCHMARKS, draws=DRAWS, seed=0)
    np.testing.assert_array_equal(with_tables.package.state_draws, without.package.state_draws)
    for key in BENCHMARKS:
        a, b = with_tables.reforms[key], without.reforms[key]
        np.testing.assert_array_equal(a.state_draws, b.state_draws)
        assert a.notes == b.notes
        assert set(a.records) == set(b.records)
        for name in a.records:
            assert a.records[name].p50 == b.records[name].p50, (key, name)
