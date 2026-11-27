"""TOD on land already zoned by right (MA Section 3A); estimated sewer evidence.

Covers the TOD comparison with Section 3A capacity and the estimated sewer
evidence class of the practical capacity screen.

Acceptance cases from the plan: an identical pre/post envelope adds nothing; a
district straddling two tracts conserves its capacity; a district allowing 30
homes that the reform raises to 50 adds at most 20. All on Testland, with small
synthetic tables injected where ``scenarios.extra_common.pack_table`` looks first.
"""

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.capacity import (
    ESTIMATED_IN,
    ESTIMATED_OUT,
    PARAMS,
    SEPTIC_MULTI,
    UNKNOWN_SERVED,
    multipliers,
    tract_inputs,
)
from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute, prepare
from visionhousing.ledger import apply_overrides, load_ledger, sample, scenario_ledger
from visionhousing.proforma import MIDRISE
from visionhousing.scenarios.tod import (
    M2_PER_ACRE,
    SQ_M_PER_SQ_FT,
    ExistingCapacity,
    additional_legal_capacity,
    existing_capacity,
    scope_from_scenario,
    tod_funnel,
)

DRAWS = 300
SEED = 3
NFS, SIZE = 0.82, 850.0
RICH = {
    "multifamily.hard_cost_per_sqft": 120,
    "tod.new_unit_rent_premium": 2.5,
    "tod.acquisition_premium": 0.05,
    # Fixed intensity, so the prototype's homes per m2 is one number.
    "tod.net_floor_share": NFS,
    MIDRISE.unit_size.key: SIZE,
}
PROPOSED_PER_M2 = 2.5 * NFS / (SIZE * SQ_M_PER_SQ_FT)
"""Homes per m2 of the FAR 2.5 prototype at the fixed intensity (about 105 per acre)."""


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def tl_tracts(ctx):
    from visionhousing.engine.adapters import _tod_tracts

    run_context, draws = prepare(ctx, None, seed=0, draws=10)
    return _tod_tracts(run_context, draws.ledger)


@pytest.fixture(scope="module")
def setup(ctx):
    scenario = ctx.scenarios["tod"]
    led = apply_overrides(scenario_ledger(ctx.ledger, scenario), RICH)
    return led, scope_from_scenario("tod", scenario, "d07")


def whole_tract_3a(tracts, scope, per_m2):
    """Every tract's eligible land is 3A land allowing ``per_m2`` homes per m2."""
    land = tracts[f"transit_land_m2_{scope.suffix}"].to_numpy(dtype=float)
    n = len(land)
    return ExistingCapacity(
        land_m2=land,
        units=land * per_m2,
        measured=np.ones(n, dtype=bool),
        table_units=land * per_m2,
        table_station_units=land * per_m2,
    )


# ---------------------------------------------------------------- the formula


def test_additional_legal_capacity_is_the_increment_cell_by_cell():
    assert additional_legal_capacity(50, 30) == 20  # the plan's fixture
    assert additional_legal_capacity(30, 50) == 0
    assert additional_legal_capacity(42.0, 42.0) == 0
    # A cell already allowing more never offsets another cell.
    got = additional_legal_capacity([50, 30], [30, 50])
    assert got.tolist() == [20, 0] and got.sum() == 20


def test_prototype_is_about_105_homes_per_acre_against_3a_floor_of_15():
    """Different measures: a building on one lot against a district-wide minimum."""
    per_acre = PROPOSED_PER_M2 * M2_PER_ACRE
    assert 104 < per_acre < 106


# ---------------------------------------------------------------- acceptance in the funnel


def test_identical_envelope_adds_nothing(tl_tracts, setup):
    led, scope = setup
    same = whole_tract_3a(tl_tracts, scope, PROPOSED_PER_M2)
    got = tod_funnel(tl_tracts, led, scope, draws=DRAWS, seed=SEED, existing=same)
    for stage in ("capacity", "built", "absorbed", "authorized", "net"):
        assert np.abs(got.stages[stage]).max() == pytest.approx(0.0, abs=1e-9)
    assert got.records["additional_3a_capacity"].p90 == pytest.approx(0.0, abs=1e-9)
    assert np.abs(got.removed).max() == pytest.approx(0.0, abs=1e-9)  # no site is redeveloped
    plain = tod_funnel(tl_tracts, led, scope, draws=DRAWS, seed=SEED)
    assert plain.records["authorized"].p50 > 0  # the fixture does add homes without 3A
    # Land stages are untouched: only the capacity comparison changes.
    for stage in ("eligible", "developable", "feasible"):
        np.testing.assert_allclose(got.stages[stage], plain.stages[stage])


def test_thirty_existing_fifty_proposed_adds_up_to_twenty(tl_tracts, setup):
    led, scope = setup
    existing = whole_tract_3a(tl_tracts, scope, PROPOSED_PER_M2 * 30 / 50)
    got = tod_funnel(tl_tracts, led, scope, draws=DRAWS, seed=SEED, existing=existing)
    feasible_m2 = got.stages["feasible"] * M2_PER_ACRE
    proposed = feasible_m2 * PROPOSED_PER_M2
    np.testing.assert_allclose(got.stages["capacity"], proposed * 20 / 50, rtol=1e-9, atol=1e-9)
    assert (got.stages["built"] <= got.stages["capacity"] + 1e-9).all()
    rec = got.records
    assert rec["additional_3a_capacity"].p50 == pytest.approx(
        rec["proposed_3a_capacity"].p50 - rec["existing_3a_on_developable"].p50, rel=1e-6
    )
    assert "massgis_mbta3a_parcels" in rec["capacity"].source_ids
    assert "Section 3A" in rec["capacity"].formula


def test_no_3a_land_is_the_benchmark_exactly(tl_tracts, setup):
    led, scope = setup
    none = whole_tract_3a(tl_tracts, scope, 0.0)
    none = ExistingCapacity(
        land_m2=none.land_m2 * 0,
        units=none.units * 0,
        measured=none.measured,
        table_units=none.table_units,
        table_station_units=none.table_station_units,
    )
    got = tod_funnel(tl_tracts, led, scope, draws=DRAWS, seed=SEED, existing=none)
    plain = tod_funnel(tl_tracts, led, scope, draws=DRAWS, seed=SEED)
    for stage in plain.stages:
        np.testing.assert_allclose(got.stages[stage], plain.stages[stage])
    np.testing.assert_allclose(got.removed, plain.removed)
    np.testing.assert_allclose(got.baseline_on_sites, plain.baseline_on_sites)


def test_existing_and_acre_share_cannot_be_combined(tl_tracts, setup):
    led, scope = setup
    ex = whole_tract_3a(tl_tracts, scope, 0.0)
    with pytest.raises(ValueError, match="not both"):
        tod_funnel(tl_tracts, led, scope, draws=10, seed=0, existing=ex, zoned_m2=ex.land_m2)


# ---------------------------------------------------------------- reading the table


def two_tracts(land_s, land_h, area):
    return pd.DataFrame(
        {
            "geoid": ["A", "B", "C"],
            "transit_land_m2_d07": land_s,
            "transit_land_m2_0_50mi": land_h,
            "land_area_m2": area,
        }
    )


def test_district_straddling_two_tracts_conserves_capacity():
    """A 100-home, 10-acre district split 40/60 by area: same total, same increment."""
    acres = 10.0
    big = acres * M2_PER_ACRE * 10  # both tracts' land near stops is ample
    tracts = two_tracts([big, big, big], [big, big, big], [big * 2] * 3)
    table = pd.DataFrame(
        {
            "geoid": ["A", "B"],
            "capacity_units": [40.0, 60.0],
            "capacity_parcel_units": [40.0, 60.0],
            "station_area_units": [40.0, 60.0],
            "parcel_acres": [4.0, 6.0],
        }
    )
    ex = existing_capacity(tracts, table, "d07")
    assert ex.units.sum() == pytest.approx(100.0)
    assert ex.land_m2.sum() == pytest.approx(acres * M2_PER_ACRE)
    assert ex.measured.tolist() == [True, True, False]
    one = additional_legal_capacity(acres * M2_PER_ACRE * PROPOSED_PER_M2, 100.0)
    split = additional_legal_capacity(ex.land_m2 * PROPOSED_PER_M2, ex.units).sum()
    assert split == pytest.approx(one)


def test_distance_scales_the_station_part_and_caps_at_the_eligible_land():
    m2 = M2_PER_ACRE
    table = pd.DataFrame(
        {
            "geoid": ["A", "B"],
            "capacity_units": [100.0, 100.0],
            "capacity_parcel_units": [100.0, 100.0],
            "station_area_units": [100.0, 50.0],
            "parcel_acres": [10.0, 10.0],
        }
    )
    # Tract A: the run's distance reaches half of the half-mile land.
    # Tract B: the run reaches all of the half-mile land plus half of the rest of the tract.
    tracts = two_tracts([50 * m2, 300 * m2, 0.0], [100 * m2, 100 * m2, 0.0], [1e9, 500 * m2, 1.0])
    ex = existing_capacity(tracts, table, "d07")
    assert ex.units[0] == pytest.approx(50.0) and ex.land_m2[0] == pytest.approx(5 * m2)
    assert ex.units[1] == pytest.approx(50.0 + 50.0 * 0.5)
    assert ex.land_m2[1] == pytest.approx((5.0 + 5.0 * 0.5) * m2)
    # Capped at the eligible land, density kept.
    small = two_tracts([2 * m2, 0, 0], [2 * m2, 1, 1], [1e9, 1, 1])
    capped = existing_capacity(small, table.iloc[:1], "d07")
    assert capped.land_m2[0] == pytest.approx(2 * m2)
    assert capped.units[0] / capped.land_m2[0] == pytest.approx(100.0 / (10 * m2))
    assert existing_capacity(small, None, "d07") is None


# ---------------------------------------------------------------- the engine


def three_a_table(tracts, near=True):
    """3A rows for the tracts with land near a major stop (``near``) or for the others."""
    pick = tracts[(tracts["transit_land_m2_d07"] > 0) == near]
    return pd.DataFrame(
        {
            "geoid": pick["geoid"].astype(str).to_numpy(),
            "county": pick["county"].astype(str).to_numpy(),
            "capacity_units": 400.0,
            "capacity_parcel_units": 400.0,
            "capacity_district_units": 0.0,
            "station_area_units": 300.0,
            "parcel_acres": 20.0,
        }
    )


def test_engine_uses_the_3a_table_and_the_variant_keeps_the_old_rule(tl_tracts):
    c = load_context("TL")
    c.derived[("pack_table", "mbta_3a_capacity_tract")] = three_a_table(tl_tracts)
    res = compute(c, ["tod"], RICH, seed=SEED, draws=DRAWS)
    r = res.reforms["tod"].result
    assert "existing_3a_capacity" in r.records and "additional_3a_capacity" in r.records
    assert any("additional legal capacity" in n for n in r.notes)
    assert r.records["existing_3a_resolved_share"].value == pytest.approx(1.0)
    assert r.records["existing_3a_capacity"].p50 > 0
    far = load_context("TL")
    far.derived[("pack_table", "mbta_3a_capacity_tract")] = three_a_table(tl_tracts, near=False)
    unresolved = compute(far, ["tod"], RICH, seed=SEED, draws=DRAWS).reforms["tod"].result
    assert unresolved.records["existing_3a_resolved_share"].value == 0.0
    old = compute(c, {"tod": {"variant": "tod_ma_acre_share"}}, RICH, seed=SEED, draws=DRAWS)
    assert "existing_3a_capacity" not in old.reforms["tod"].result.records
    bare = compute(load_context("TL"), ["tod"], RICH, seed=SEED, draws=DRAWS)
    np.testing.assert_allclose(old.reforms["tod"].state_draws, bare.reforms["tod"].state_draws)
    # The 3A land adds at most what the benchmark rule would have added there.
    assert res.reforms["tod"].added.p50 <= bare.reforms["tod"].added.p50 + 1e-9


# ---------------------------------------------------------------- estimated sewer evidence


def estimated_ctx():
    c = load_context("TL")
    geo = c.tracts["geoid"].astype(str).to_numpy()
    n = len(geo)
    i = np.arange(n)
    ver = np.where(i % 4 == 0, 1.0, 0.0)  # every fourth tract has an official answer
    c.derived[("pack_table", "infra_tract")] = pd.DataFrame(
        {
            "geoid": geo,
            "sewer_status": np.where(ver == 1, "verified", "unknown"),
            "sewer_share": ver,
            "sewer_known": ver,
            "constraint_share": 0.0,
        }
    )
    c.derived[("pack_table", "ma_sewer_estimated_tract")] = pd.DataFrame(
        {
            "geoid": geo[:-1],  # the last tract is not listed: it stays unknown
            "est_share": 0.6,
            "circle_only_share": 0.1,
            "geometry_quality": "polygon",
            "evidence_class": "estimated service",
        }
    )
    return c


def test_estimated_service_is_its_own_class_and_circles_stay_unknown():
    c = estimated_ctx()
    inp = tract_inputs(c)
    n = len(c.tracts)
    i = np.arange(n)
    off = (i % 4 != 0) & (i < n - 1)
    np.testing.assert_allclose(inp.estimated_in[:-1][off], 0.6)
    np.testing.assert_allclose(inp.estimated_out[:-1][off], 0.3)
    np.testing.assert_allclose(inp.unknown[:-1][off], 0.1)  # placeholder circles
    assert inp.estimated_in[0] == 0.0 and inp.unknown[0] == 0.0  # verified wins
    assert inp.unknown[n - 1] == 1.0  # not listed
    v = sample(load_ledger(), n=DRAWS, seed=SEED, ids=PARAMS)
    sewer = multipliers(inp, np.flatnonzero(off), v, "tod")["sewer"]
    s, p = v[SEPTIC_MULTI], v[UNKNOWN_SERVED]
    e_in, e_out = v[ESTIMATED_IN], v[ESTIMATED_OUT]
    want = 0.6 * (e_in + (1 - e_in) * s) + 0.3 * (e_out + (1 - e_out) * s) + 0.1 * (p + (1 - p) * s)
    np.testing.assert_allclose(sewer, np.broadcast_to(want, sewer.shape))
    assert np.ptp(sewer[0]) > 0.01 and sewer.max() < 1.0  # a range, never verified


def test_coverage_reports_verified_estimated_unknown():
    c = estimated_ctx()
    res = compute(c, ["tod"], RICH, seed=SEED, draws=DRAWS, capacity_screen=True)
    cov = res.capacity.coverage["sewer"]
    parts = [cov[f"homes_{k}_share"].value for k in ("verified", "estimated", "unknown")]
    assert sum(parts) == pytest.approx(1.0)
    assert all(x > 0 for x in parts)
    assert "published estimate" in cov["split_text"] and cov["split_text"] in cov["text"]
    # "Measured" stays official answers only; the estimate is never counted as verified.
    assert cov["homes_share"].value == pytest.approx(cov["homes_verified_share"].value)
    assert "massdep_sewer_service_areas" in cov["homes_estimated_share"].source_ids
    ids = res.capacity.reforms["tod"]["screened"].assumption_ids
    assert ESTIMATED_IN in ids and ESTIMATED_OUT in ids


def test_without_an_estimated_table_nothing_changes():
    c = load_context("TL")
    inp = tract_inputs(c)
    assert not inp.estimated()
    assert not inp.estimated_in.any() and not inp.estimated_out.any()
    res = compute(c, ["adu"], seed=SEED, draws=DRAWS, capacity_screen=True)
    ids = res.capacity.reforms["adu"]["screened"].assumption_ids
    assert ESTIMATED_IN not in ids
    assert res.capacity.coverage["sewer"]["split_text"] == ""


# ---------------------------------------------------------------- screened work types


@pytest.mark.parametrize("key", ["adu", "missing_middle"])
def test_screened_work_types_add_up_to_the_screened_headline(key):
    """The capacity-screened variant keeps the module's work-type split (review F1)."""
    c = load_context("TL")
    cheap = {
        "missing_middle.hard_cost_per_sqft": 120.0,
        "missing_middle.cap_rate": 0.045,
    }
    plain = compute(c, [key], cheap, seed=5, draws=DRAWS).reforms[key]
    scr = compute(
        c, {key: {"variant": f"{key}_capacity_screen"}}, cheap, seed=5, draws=DRAWS
    ).reforms[key]
    work = scr.result.detail.work_types
    np.testing.assert_allclose(sum(work.values()), scr.state_draws, rtol=1e-9, atol=1e-9)
    assert scr.state_draws.sum() < plain.state_draws.sum()  # the screen did remove homes
    books = scr.accounting
    assert "work_types" not in books.status  # no rescue needed: they already add up
    assert books.records["comparable"].p50 > 0
    if key == "adu":
        assert plain.accounting.records["comparable"].p50 > books.records["comparable"].p50


def test_accounting_never_reclassifies_silently():
    """Work types that do not add up are scaled to the headline, with a status note."""
    from types import SimpleNamespace

    from visionhousing.engine import outcome as oc
    from visionhousing.engine import records as rec

    head = np.array([10.0, 20.0, 30.0])
    headline = rec.from_draws("added", head, "units", expression="test")
    detail = SimpleNamespace(
        work_types={"new_standalone": np.array([4.0, 4.0, 4.0]), "conversion": np.ones(3)}
    )
    res = SimpleNamespace(records={}, detail=detail)
    books = oc.accounting("adu", "ADUs", headline, head, res, None, {}, None)
    assert "scaled per draw" in books.status["work_types"]
    np.testing.assert_allclose(
        books.records["work_new_standalone"].p50, np.median(head * 0.8), rtol=1e-6
    )
    assert books.records["comparable"].p50 > 0


# ---------------------------------------------------------------- q x existing (variant)

Q_ID = "tod.baseline_replacement_probability"


def test_q_scaled_with_q_one_equals_full(tl_tracts, setup):
    led, scope = setup
    led1 = apply_overrides(led, {Q_ID: 1.0})
    existing = whole_tract_3a(tl_tracts, scope, PROPOSED_PER_M2 * 30 / 50)
    full = tod_funnel(tl_tracts, led1, scope, draws=DRAWS, seed=SEED, existing=existing)
    qs = tod_funnel(
        tl_tracts,
        led1,
        scope,
        draws=DRAWS,
        seed=SEED,
        existing=existing,
        existing_subtract="q_scaled",
    )
    for stage in ("capacity", "built", "absorbed", "authorized", "net"):
        np.testing.assert_array_equal(qs.stages[stage], full.stages[stage])


def test_q_scaled_subtracts_q_times_existing(tl_tracts, setup):
    led, scope = setup
    half = apply_overrides(led, {Q_ID: 0.5})
    existing = whole_tract_3a(tl_tracts, scope, PROPOSED_PER_M2 * 30 / 50)
    kw = {"draws": DRAWS, "seed": SEED, "existing": existing}
    full = tod_funnel(tl_tracts, half, scope, **kw)
    qs = tod_funnel(tl_tracts, half, scope, existing_subtract="q_scaled", **kw)
    proposed = qs.stages["feasible"] * M2_PER_ACRE * PROPOSED_PER_M2
    # max(0, 50 - 0.5 x 30) / 50 = 0.7 of the proposed homes, against 20 / 50 under full.
    np.testing.assert_allclose(qs.stages["capacity"], proposed * 0.7, rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(full.stages["capacity"], proposed * 0.4, rtol=1e-9, atol=1e-9)
    assert (qs.stages["authorized"] >= full.stages["authorized"] - 1e-9).all()
    assert "q x existing" in qs.records["capacity"].formula
    assert "q x existing" in qs.records["additional_3a_capacity"].formula
    assert "q x existing" not in full.records["capacity"].formula
    with pytest.raises(ValueError, match="existing_subtract"):
        tod_funnel(tl_tracts, half, scope, existing_subtract="half", **kw)


def test_engine_variant_q_scaled_is_named_and_at_least_the_headline(tl_tracts):
    c = load_context("TL")
    c.derived[("pack_table", "mbta_3a_capacity_tract")] = three_a_table(tl_tracts)
    head = compute(c, ["tod"], RICH, seed=SEED, draws=DRAWS).reforms["tod"]
    alt = compute(c, {"tod": {"variant": "tod_ma_3a_q_scaled"}}, RICH, seed=SEED, draws=DRAWS)
    a = alt.reforms["tod"]
    assert (a.state_draws >= head.state_draws - 1e-9).all()
    assert any("q_scaled" in n for n in a.result.notes)
    assert not any("q_scaled" in n for n in head.result.notes)
    with pytest.raises(ValueError, match="existing_3a_subtract"):
        compute(c, {"tod": {"existing_3a_subtract": "x"}}, RICH, seed=SEED, draws=DRAWS)


# ---------------------------------------------------------------- note wording by state


def test_by_right_note_names_massachusetts_only_for_ma(tmp_path):
    """The synthetic demo state has the by-right capacity table but is not Massachusetts."""
    import shutil
    from pathlib import Path

    from visionhousing.engine.core import run
    from visionhousing.scenarios.tod import by_right_words

    fix = Path(__file__).resolve().parent / "fixtures" / "demo_state"
    folder = tmp_path / "DM"
    shutil.copytree(fix, folder)
    c = load_context("DM", folder=folder)
    out = run("DM", ["tod"], seed=0, draws=200, context=c, use_cache=False).reforms["tod"]
    r = out.result
    assert "existing_3a_capacity" in r.records  # the capacity rule is used
    words = [*r.notes, *(t for t, _ in r.county_notes.values())]
    assert any("already zones for multi-family homes by right" in n for n in words)
    for banned in ("Massachusetts", "Section 3A", "F_L_M__", "3A district"):
        assert not any(banned in n for n in words), banned
    ma = by_right_words("MA")
    assert ma["who"] == "Massachusetts" and "F_L_M__" in ma["land"]
    assert by_right_words("dm") == by_right_words("")
