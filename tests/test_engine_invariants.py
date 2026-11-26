"""Method invariants of the engine (plan 6.5), on Testland."""

import numpy as np
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute
from visionhousing.engine.packages import lot_overlap

DRAWS = 800
ALL = ["adu", "fees", "parking"]


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def package(ctx):
    return compute(ctx, ALL, seed=11, draws=DRAWS)


def test_no_reform_gives_the_baseline_exactly(ctx):
    for reforms in (None, []):
        res = compute(ctx, reforms, seed=5, draws=DRAWS)
        assert res.reforms == {}
        assert res.total == res.baseline
        assert np.all(res.package.state_draws == 0)
        assert res.package.combined.p10 == res.package.combined.p90 == 0


def test_a_reform_that_adds_nothing_leaves_the_baseline_exactly(ctx):
    nothing = {
        "parking.share_still_built_transit_rich": 1.0,
        "parking.share_still_built_elsewhere": 1.0,
    }
    res = compute(ctx, "parking", nothing, seed=5, draws=DRAWS)
    assert np.all(res.reforms["parking"].state_draws == 0)
    assert res.total == res.baseline


def test_every_range_is_ordered(package):
    ranges = [r for r in package.records() if r.is_range]
    assert len(ranges) > 30
    for r in ranges:
        assert r.p10 <= r.p50 <= r.p90, r.name


def test_counties_sum_to_the_state_in_every_draw(package):
    assert np.allclose(package.baseline_county_draws.sum(axis=0), package.baseline_draws)
    county_p50 = sum(r.p50 for r in package.baseline_counties.values())
    assert county_p50 == pytest.approx(package.baseline.p50)
    for out in package.reforms.values():
        assert np.allclose(out.county_draws.sum(axis=0), out.state_draws)
        assert np.allclose(out.result.added.sum(axis=0), out.state_draws)
    pkg = package.package
    assert np.allclose(pkg.county_draws.sum(axis=0), pkg.state_draws)


def test_tracts_roll_up_to_their_counties(package):
    adu = package.reforms["adu"]
    detail = adu.result.detail
    for i, county in enumerate(package.county_ids):
        mask = detail.counties == county
        assert np.allclose(adu.county_draws[i], detail.stages["net"][:, mask].sum(axis=1))


def test_package_is_at_most_the_sum_of_parts_plus_stated_interactions(package):
    pkg = package.package
    parts = sum(o.state_draws for o in package.reforms.values())
    assert np.allclose(pkg.parts_draws, parts)
    stated = pkg.interaction_draws
    assert np.all(pkg.state_draws <= parts + stated + 1e-9)
    assert np.allclose(pkg.state_draws, parts - pkg.overlap_draws + pkg.interaction_draws)
    assert np.all(pkg.overlap_draws >= 0)


def test_fee_cut_feeds_the_adu_pro_forma(package):
    inter = package.package.interactions
    assert set(inter) == {"fees -> adu", "parking -> adu"}
    fee = inter["fees -> adu"]
    assert fee.p10 >= 0 and fee.p90 > 0
    assert package.package.interaction.p50 > 0
    # The benchmark ADU needs no added parking, so parking reform adds nothing to it.
    park = inter["parking -> adu"]
    assert park.p10 == park.p50 == park.p90 == 0


def test_interactions_are_never_negative_draw_by_draw(ctx):
    res = compute(ctx, ["adu", "fees"], seed=2, draws=DRAWS)
    assert np.all(res.package.interaction_draws >= -1e-9)


def test_parking_interaction_appears_when_adus_need_parking(ctx):
    needs = {"adu.added_parking_spaces": 1.0}
    res = compute(ctx, ["adu", "parking"], needs, seed=2, draws=DRAWS)
    assert res.package.interactions["parking -> adu"].p50 > 0


def test_no_overlap_without_missing_middle(package):
    assert np.all(package.package.overlap_draws == 0)
    assert package.package.overlap.p90 == 0


# Testland's rents are low, so 2-6 unit rebuilds rarely pencil at the central cost.
# A low build cost makes the overlap and the interactions visible.
CHEAP_MIDDLE = {"missing_middle.hard_cost_per_sqft": 100}


@pytest.fixture(scope="module")
def with_middle(ctx):
    reforms = ["adu", "missing_middle", "fees", "parking"]
    return compute(ctx, reforms, CHEAP_MIDDLE, seed=11, draws=DRAWS)


def test_missing_middle_is_registered_and_runs(ctx):
    res = compute(ctx, "missing_middle", seed=1, draws=200)
    out = res.reforms["missing_middle"]
    assert out.result.level == "tract"
    assert len(out.steps) == 7
    assert np.all(out.state_draws >= 0)
    assert res.package.overlap.p90 == 0


def test_adu_and_missing_middle_share_lots_once(with_middle):
    pkg = with_middle.package
    assert np.all(pkg.overlap_draws >= 0)
    assert pkg.overlap.p90 > 0
    middle = with_middle.reforms["missing_middle"].state_draws
    assert np.all(pkg.overlap_draws <= middle + 1e-9)
    assert "missing_middle.adu_lot_overlap_share" in pkg.overlap.assumption_ids


def test_overlap_is_what_missing_middle_loses_to_adu_lots(ctx, with_middle):
    alone = with_middle.reforms["missing_middle"].county_draws
    with pytest.raises(ValueError, match="cannot add"):
        lot_overlap(alone, alone + 1.0)
    assert np.allclose(lot_overlap(alone, alone), 0)


def test_package_with_missing_middle_obeys_the_bound(with_middle):
    pkg = with_middle.package
    parts = sum(o.state_draws for o in with_middle.reforms.values())
    assert np.all(pkg.state_draws <= parts + pkg.interaction_draws + 1e-9)
    assert np.allclose(pkg.state_draws, parts - pkg.overlap_draws + pkg.interaction_draws)
    fee = pkg.interactions["fees -> missing_middle"]
    assert fee.p10 >= 0 and fee.p90 > 0
    assert {"fees -> adu", "parking -> missing_middle"} <= set(pkg.interactions)


def test_reforms_never_remove_units(package):
    for out in package.reforms.values():
        assert np.all(out.state_draws >= 0)
        assert np.all(out.county_draws >= 0)
    assert np.all(package.total.p10 >= package.baseline.p10)


def test_same_seed_same_result_and_a_new_seed_differs(ctx):
    a = compute(ctx, ALL, seed=21, draws=300)
    b = compute(ctx, ALL, seed=21, draws=300)
    c = compute(ctx, ALL, seed=22, draws=300)
    assert a.to_dict() == b.to_dict()
    for key in ALL:
        assert np.array_equal(a.reforms[key].county_draws, b.reforms[key].county_draws)
        assert not np.array_equal(a.reforms[key].state_draws, c.reforms[key].state_draws)
    assert np.array_equal(a.baseline_draws, b.baseline_draws)
    assert np.array_equal(a.package.state_draws, b.package.state_draws)


def test_widening_an_input_range_never_narrows_the_output_range(ctx):
    pid = "supply.elasticity_medium_run"
    p = ctx.ledger[pid]
    widths = []
    for s in (1.0, 1.1, 1.2):
        wider = {
            pid: {
                "low": p.central - s * (p.central - p.low),
                "high": p.central + s * (p.high - p.central),
            }
        }
        res = compute(ctx, "fees", wider, seed=4, draws=DRAWS)
        added = res.reforms["fees"].added
        widths.append(added.p90 - added.p10)
    assert widths[0] <= widths[1] <= widths[2]
    assert widths[2] > widths[0]


def test_baseline_cycle_moves_the_baseline_only(ctx):
    weak = compute(ctx, "adu", seed=1, draws=200, cycle="weak")
    strong = compute(ctx, "adu", seed=1, draws=200, cycle="strong")
    assert weak.baseline.p50 < strong.baseline.p50
    assert np.array_equal(weak.reforms["adu"].state_draws, strong.reforms["adu"].state_draws)


# The tract-level part of a package (the statewide tract columns read it).


def _to_counties(result, rows, row_counties):
    at = {c: i for i, c in enumerate(result.county_ids)}
    out = np.zeros((len(result.county_ids), rows.shape[1]))
    for county, row in zip(row_counties, rows, strict=True):
        out[at[county]] += row
    return out


def test_tract_part_plus_county_reforms_is_the_package_in_every_draw(with_middle):
    pkg = with_middle.package
    assert pkg.tract_reforms == ("adu", "missing_middle")
    assert pkg.county_reforms == ("fees", "parking")
    assert pkg.tract_draws.shape == (len(pkg.tract_geoids), DRAWS)
    summed = _to_counties(with_middle, pkg.tract_draws, pkg.tract_counties)
    assert np.allclose(summed, pkg.tract_county_draws, rtol=0, atol=1e-9)
    rest = sum(with_middle.reforms[k].county_draws for k in pkg.county_reforms)
    assert np.allclose(pkg.tract_county_draws + rest, pkg.county_draws, rtol=0, atol=1e-9)
    # Tract by tract: each reform alone, minus the ADU-lot overlap, plus the interactions.
    alone = sum(with_middle.reforms[k].state_draws for k in pkg.tract_reforms)
    expected = alone - pkg.overlap_draws + pkg.interaction_draws
    assert np.allclose(pkg.tract_draws.sum(axis=0), expected, rtol=0, atol=1e-9)
    assert np.all(pkg.tract_draws >= -1e-9)
    assert pkg.tract_combined.p50 == pytest.approx(np.percentile(expected, 50), abs=1e-9)
    assert "missing_middle.adu_lot_overlap_share" in pkg.tract_combined.assumption_ids


def test_one_tract_reform_alone_is_its_own_tract_part(ctx):
    res = compute(ctx, "adu", seed=3, draws=300)
    pkg, adu = res.package, res.reforms["adu"]
    assert pkg.county_reforms == () and pkg.tract_reforms == ("adu",)
    assert np.array_equal(pkg.tract_draws, adu.result.added)
    assert pkg.tract_combined.p50 == pytest.approx(adu.added.p50, rel=1e-12)
    county_only = compute(ctx, "fees", seed=3, draws=300).package
    assert county_only.tract_draws is None and county_only.county_reforms == ("fees",)


def test_step_summaries_come_from_the_module_stages(ctx):
    res = compute(ctx, ["adu", "fees"], seed=3, draws=300)
    adu = res.reforms["adu"]
    detail = adu.result.detail
    steps = adu.tract_steps()
    assert [s.stage for s in steps] == list(detail.stages)
    for s, record in zip(steps, adu.steps, strict=True):
        assert s.record == record and s.level == "tract"
        assert np.all(s.p10 <= s.p50) and np.all(s.p50 <= s.p90)
        assert np.allclose(s.p50, np.median(detail.stages[s.stage], axis=0))
    by_county = {s.stage: s for s in adu.county_steps()}
    for stage in ("eligible", "built"):
        own = detail.county_records(stage)  # the module's own county records
        step = by_county[stage]
        for i, county in enumerate(step.geoids):
            assert step.p50[i] == pytest.approx(own[county].p50, rel=1e-9)
    one = adu.tract_steps(["fit"], county="99001")
    assert [s.stage for s in one] == ["fit"]
    mine = {g for g, c in zip(detail.geoids, detail.counties, strict=True) if c == "99001"}
    assert set(one[0].geoids) == mine
    assert res.reforms["fees"].tract_steps() == () and res.reforms["fees"].county_steps() == ()
