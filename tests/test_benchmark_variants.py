"""Benchmark refinements and named variants (ADU, missing middle, parking, fees, TOD) on Testland.

The benchmark presets must keep their numbers where nothing new applies;
every named variant must run, differ from the preset where it should, and
keep its accounting identities (parking no-response = 0, fee split = revenue).
"""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.adapters import adu_lot_categories
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.engine.readouts import fiscal_split
from visionhousing.engine.variants import (
    expand_options,
    load_variants,
    parse_variants,
    variant_overrides,
)
from visionhousing.ledger import Ledger, LedgerError, apply_overrides, load_ledger, scenario_ledger
from visionhousing.scenarios.adu import adu_funnel
from visionhousing.scenarios.adu import scope_from_scenario as adu_scope
from visionhousing.scenarios.fees import fee_saving
from visionhousing.scenarios.missing_middle import missing_middle_funnel
from visionhousing.scenarios.missing_middle import scope_from_scenario as mm_scope
from visionhousing.scenarios.prototypes import PROTOTYPE_IDS, PROTOTYPES
from visionhousing.scenarios.tod import (
    TodScope,
    decomposition_verdict,
    network_columns,
)

DRAWS = 300


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


def _run(ctx, reforms, **kw):
    return run("TL", reforms, draws=DRAWS, context=ctx, use_cache=False, **kw)


# ---------------------------------------------------------------- variants file


def test_every_variant_is_valid_against_the_ledger():
    variants = load_variants()
    assert len(variants) >= 10
    again = parse_variants(
        {"variants": {k: {**v.__dict__} for k, v in variants.items()}}, load_ledger()
    )
    assert set(again) == set(variants)
    families = {v.reform for v in variants.values()}
    assert families == {
        "adu",
        "missing_middle",
        "parking",
        "fees",
        "tod",
        "transit_zoning_funded_infrastructure",
        "starter_home_lots",
        "fee_deferral_with_affordability",
    }


def test_variant_checks_refuse_bad_entries():
    led = load_ledger()
    with pytest.raises(LedgerError):
        parse_variants({"variants": {"x": {"reform": "nope", "title": "t", "question": "q"}}})
    with pytest.raises(LedgerError):
        parse_variants(
            {
                "variants": {
                    "x": {
                        "reform": "adu",
                        "title": "t",
                        "question": "q",
                        "overrides": {"adu.loan_share": "no.such_id"},
                    }
                }
            },
            led,
        )
    with pytest.raises(KeyError):
        expand_options("adu", {"variant": "no_such_variant"})
    with pytest.raises(ValueError):
        expand_options("fees", {"variant": "adu_financing_tight"})


def test_variant_override_copies_the_source_range():
    led = load_ledger()
    change = variant_overrides("adu", {"variant": "adu_financing_tight"}, led)
    out = apply_overrides(led, change)
    src = led["adu.financing_tight_loan_share"]
    got = out["adu.loan_share"]
    assert (got.low, got.central, got.high) == (src.low, src.central, src.high)
    assert variant_overrides("adu", {}, led) == {}


# ---------------------------------------------------------------- ADU


@pytest.fixture(scope="module")
def adu_inputs(ctx):
    scen = ctx.scenarios["adu"]
    return ctx.tracts, scenario_ledger(ctx.ledger, scen), ctx.fit, adu_scope("adu", scen)


def test_adu_categories_change_only_the_named_county(adu_inputs):
    tracts, led, fit, scope = adu_inputs
    county = sorted(set(tracts["county"]))[0]
    base = adu_funnel(tracts, led, fit, scope, draws=DRAWS, seed=3)
    unknown = adu_funnel(
        tracts, led, fit, scope, draws=DRAWS, seed=3,
        local_categories={county: {"unknown": 1.0}},
    )  # fmt: skip
    # All lots unknown: the statewide range applies, so nothing changes.
    np.testing.assert_allclose(unknown.stages["net"], base.stages["net"])
    banned = adu_funnel(
        tracts, led, fit, scope, draws=DRAWS, seed=3,
        local_categories={county: {"prohibited": 1.0}},
    )  # fmt: skip
    legal = adu_funnel(
        tracts, led, fit, scope, draws=DRAWS, seed=3,
        local_categories={county: {"already_permitted": 1.0}},
    )  # fmt: skip
    inside = (tracts["county"] == county).to_numpy()
    assert np.all(legal.stages["eligible"][:, inside] == 0)
    assert np.all(banned.stages["eligible"][:, inside] >= base.stages["eligible"][:, inside])
    np.testing.assert_allclose(
        banned.stages["eligible"][:, ~inside], base.stages["eligible"][:, ~inside]
    )
    assert "exposure_read_counties" in banned.records
    assert "adu.conditions_exposure_share" in banned.records["net"].assumption_ids
    assert "adu.conditions_exposure_share" not in base.records["net"].assumption_ids


def test_adu_conditions_share_sets_the_exposed_part(adu_inputs):
    tracts, led, fit, scope = adu_inputs
    county = sorted(set(tracts["county"]))[0]
    inside = (tracts["county"] == county).to_numpy()
    cats = {county: {"by_right_conditions": 1.0}}
    full = adu_funnel(
        tracts, apply_overrides(led, {"adu.conditions_exposure_share": 1.0}), fit, scope,
        draws=DRAWS, seed=3, local_categories=cats,
    )  # fmt: skip
    none = adu_funnel(
        tracts, apply_overrides(led, {"adu.conditions_exposure_share": 0.0}), fit, scope,
        draws=DRAWS, seed=3, local_categories=cats,
    )  # fmt: skip
    assert np.all(none.stages["eligible"][:, inside] == 0)
    assert np.all(full.stages["eligible"][:, inside] > 0)


def test_adu_forms_add_up_to_adus_authorized(ctx):
    o = _run(ctx, ["adu"]).reforms["adu"]
    res = o.result.detail
    assert set(res.records) >= {"form_detached", "form_semi_detached", "installed_cost"}
    built = res.stages["built"].sum(axis=1)
    det = res.records["form_detached"]
    semi = res.records["form_semi_detached"]
    assert det.p50 > 0 and semi.p50 > 0
    assert det.p50 + semi.p50 == pytest.approx(np.median(built), rel=0.35)
    cost = res.records["installed_cost"]
    util = res.records["installed_utility_cost"]
    assert cost.p50 > util.p50 > 0
    assert res.records["owner_cash"].p50 < cost.p50


def test_adu_lot_categories_feature_detect():
    assert adu_lot_categories(None, {"1": (0, 1)}) == {}

    class Law:
        categories = pd.DataFrame({"county": ["1"], "weight": ["residents"]})

        def adu_categories(self, county):
            return {"already_permitted": 0.5, "unknown": 0.5}

    assert adu_lot_categories(Law(), {"1": (0, 1)}) == {}
    Law.categories = pd.DataFrame({"county": ["1"], "weight": ["sf_lots"]})
    law = Law()
    law.adu_categories = lambda c: {
        "already_permitted": 0.2,
        "by_right_conditions": 0.1,
        "discretionary": 0.1,
        "prohibited": 0.1,
        "unknown": 0.5,
    }
    assert adu_lot_categories(law, {"1": (0, 1)})["1"]["unknown"] == 0.5
    assert adu_lot_categories(law, {"2": (0, 1)}) == {}


def test_adu_financing_variants_are_ordered(ctx):
    base = _run(ctx, ["adu"]).reforms["adu"]
    tight = _run(ctx, {"adu": {"variant": "adu_financing_tight"}}).reforms["adu"]
    easy = _run(ctx, {"adu": {"variant": "adu_financing_easy"}}).reforms["adu"]
    full = _run(ctx, {"adu": {"variant": "adu_full_installed_cost"}}).reforms["adu"]
    assert tight.added.p50 < base.added.p50 < easy.added.p50
    assert full.added.p50 < base.added.p50
    assert tight.records["owner_cash"].p50 > base.records["owner_cash"].p50
    assert any("Variant 'adu_financing_tight'" in n for n in tight.notes)
    assert not any("Variant" in n for n in base.notes)


# ---------------------------------------------------------------- missing middle


@pytest.fixture(scope="module")
def rich_tracts(ctx):
    t = ctx.tracts.copy()
    for c in ("median_gross_rent", "median_rent_b1", "median_rent_b2", "median_rent_b3"):
        t[c] = t[c] * 3.0
    t["lot_m2_per_unit_p25"] = 250.0
    t["lot_m2_per_unit_p50"] = 600.0
    t["lot_m2_per_unit_p75"] = 1400.0
    return t


@pytest.fixture(scope="module")
def mm_ledger(ctx):
    scen = ctx.scenarios["missing_middle"]
    return scenario_ledger(ctx.ledger, scen), mm_scope("missing_middle", scen)


def test_prototype_records_leave_the_benchmark_unchanged(rich_tracts, mm_ledger):
    led, scope = mm_ledger
    with_protos = missing_middle_funnel(rich_tracts, led, None, scope, draws=DRAWS, seed=1)
    bare = Ledger({k: v for k, v in led.items() if k not in PROTOTYPE_IDS}, version=led.version)
    without = missing_middle_funnel(rich_tracts, bare, None, scope, draws=DRAWS, seed=1)
    for stage in without.stages:
        np.testing.assert_array_equal(with_protos.stages[stage], without.stages[stage])
    assert "proto_duplex_alone" in with_protos.records
    assert "proto_duplex_alone" not in without.records


def test_prototype_choice_accounting(rich_tracts, mm_ledger):
    led, scope = mm_ledger
    res = missing_middle_funnel(
        rich_tracts, led, None, scope, draws=DRAWS, seed=1, method="prototypes"
    )
    mix = missing_middle_funnel(rich_tracts, led, None, scope, draws=DRAWS, seed=1)
    rec = res.records
    fits = [rec[f"proto_{p.key}_fit"].p50 for p in PROTOTYPES]
    assert fits == sorted(fits, reverse=True)  # bigger buildings fit on fewer lots
    for p in PROTOTYPES:
        assert rec[f"proto_{p.key}_alone"].p50 <= rec[f"proto_{p.key}_fit"].p50 + 1e-9
    assert res.records["authorized"].p50 != pytest.approx(mix.records["authorized"].p50)
    # Units per redeveloped lot lie between 2 and 6.
    lots = res.stages["redeveloped"].sum()
    assert 2.0 <= res.stages["units"].sum() / lots <= 6.0
    with pytest.raises(ValueError):
        missing_middle_funnel(rich_tracts, led, None, scope, draws=10, method="nope")


def test_duplex_pencils_where_six_does_not_on_small_lots(ctx, mm_ledger):
    led, scope = mm_ledger
    t = ctx.tracts.copy()
    for c in ("median_gross_rent", "median_rent_b1", "median_rent_b2", "median_rent_b3"):
        t[c] = t[c] * 3.0
    # Small lots: few fit a six-unit building.
    t["lot_m2_per_unit_p25"], t["lot_m2_per_unit_p50"], t["lot_m2_per_unit_p75"] = 300, 450, 650
    res = missing_middle_funnel(t, led, None, scope, draws=DRAWS, seed=2)
    assert res.records["proto_duplex_alone"].p50 > res.records["proto_sixplex_alone"].p50


# ---------------------------------------------------------------- parking


def test_parking_no_response_gives_zero(ctx):
    o = _run(ctx, {"parking": {"variant": "parking_no_response"}}).reforms["parking"]
    assert np.all(o.state_draws == 0)


def test_parking_variants_bracket_the_benchmark(ctx):
    base = _run(ctx, ["parking"]).reforms["parking"]
    surf = _run(ctx, {"parking": {"variant": "parking_all_surface"}}).reforms["parking"]
    struct = _run(ctx, {"parking": {"variant": "parking_all_structured"}}).reforms["parking"]
    provided = _run(ctx, {"parking": {"variant": "parking_provided_spaces"}}).reforms["parking"]
    assert surf.added.p50 < base.added.p50 < struct.added.p50
    assert provided.added.p50 != pytest.approx(base.added.p50)
    r = base.records
    assert r["provided_apartment_transit_rich_before"].value == pytest.approx(
        load_ledger()["parking.required_spaces_per_unit_multifamily"].central
    )
    assert r["saving_all_surface"].value < r["saving_all_structured"].value


def test_parking_provided_spaces_bounds(ctx):
    high = {pid: 5.0 for pid in (
        "parking.voluntary_spaces_apartment_transit_rich",
        "parking.voluntary_spaces_apartment_elsewhere",
        "parking.voluntary_spaces_small_transit_rich",
        "parking.voluntary_spaces_small_elsewhere",
    )}  # fmt: skip
    o = _run(ctx, {"parking": {"variant": "parking_provided_spaces"}}, overrides=high)
    assert np.all(o.reforms["parking"].state_draws == 0)  # builders give more than the minimum


# ---------------------------------------------------------------- fees


def test_fee_split_sums_to_revenue(ctx):
    o = _run(ctx, ["fees"]).reforms["fees"]
    rd = o.readouts
    for key in ("fiscal_waived_on_baseline", "fiscal_waived_on_added", "fiscal_net_change"):
        assert key in rd
    assert rd["fiscal_replacement"].p50 == 0  # none by default
    cut = np.full(4, 25_000.0)
    fee = np.full(4, 50_000.0)
    base = np.array([100.0, 200.0, 300.0, 0.0])
    added = np.array([5.0, 0.0, 7.0, 1.0])
    led = apply_overrides(load_ledger(), {"fees.replacement_funding_share": 0.5})
    out = fiscal_split(cut, fee, base, added, led, ids=["fees.fee_total_per_unit"], sources=[])
    revenue = cut * (base + added)
    waived = (out["fiscal_waived_on_baseline"], out["fiscal_waived_on_added"])
    assert sum(r.p50 for r in waived) == pytest.approx(
        np.median(cut * base) + np.median(cut * added)
    )
    total = cut * base + cut * added
    np.testing.assert_allclose(total, revenue)
    assert out["fiscal_replacement"].p50 == pytest.approx(np.median(0.5 * revenue))
    net = -cut * base + (fee - cut) * added + 0.5 * revenue
    assert out["fiscal_net_change"].p50 == pytest.approx(np.median(net))


def test_fee_deferral_variant(ctx):
    base = _run(ctx, ["fees"]).reforms["fees"]
    defer = _run(ctx, {"fees": {"variant": "fees_deferral"}}).reforms["fees"]
    assert 0 < defer.added.p50 < base.added.p50
    rd = defer.readouts
    assert rd["deferral_public_cost"].p50 < base.readouts["revenue_given_up"].p50
    assert "revenue_given_up" not in rd
    led = load_ledger()
    saving = fee_saving(
        {
            "fees.fee_total_per_unit": np.array([50_000.0]),
            "fees.construction_loan_rate": np.array([0.08]),
            "fees.deferral_months": np.array([12.0]),
        },
        "deferral",
    )
    assert saving[0] == pytest.approx(4_000.0)
    assert led["fees.fee_total_per_unit"].central == 50_000


# ---------------------------------------------------------------- TOD


def _walk_table(tracts: pd.DataFrame) -> pd.DataFrame:
    acres = tracts["land_area_m2"].to_numpy(dtype=float) / 4046.8564224
    out = pd.DataFrame({"geoid": tracts["geoid"].astype(str), "tract_acres": acres})
    for lab, share in (("0_25mi", 0.02), ("0_50mi", 0.06), ("0_75mi", 0.12)):
        out[f"straight_acres_{lab}"] = acres * share * 1.3
        out[f"network_acres_{lab}"] = acres * share
        out[f"fallback_acres_{lab}"] = acres * share * 0.25
    return out


def test_network_columns_convert_acres(ctx):
    t = ctx.tracts.head(5)
    walk = _walk_table(t)
    out = network_columns(t, walk)
    land = t["land_area_m2"].to_numpy(dtype=float)
    np.testing.assert_allclose(out["transit_land_m2_net_0_50mi"], land * 0.06, rtol=1e-9)
    np.testing.assert_allclose(out["transit_fallback_m2_net_0_50mi"], land * 0.06 * 0.25, rtol=1e-9)
    assert (out["transit_units_share_net_0_50mi"].between(0, 1)).all()
    assert network_columns(t, None) is t
    with pytest.raises(ValueError):
        TodScope("tod", "d07", "network")


def test_tod_network_variants_read_the_walkshed_table(ctx):
    straight = _run(ctx, ["tod"]).reforms["tod"]
    walk_ctx = replace(ctx, transit_walk=_walk_table(ctx.tracts))
    bench = _run(walk_ctx, ["tod"]).reforms["tod"]
    # The benchmark preset stays straight-line (D-07) even when the pack has walksheds.
    np.testing.assert_array_equal(bench.state_draws, straight.state_draws)
    net = _run(walk_ctx, {"tod": {"variant": "tod_network_half_mile"}}).reforms["tod"]
    assert net.records["eligible"].p50 != pytest.approx(straight.records["eligible"].p50)
    assert net.records["walk_fallback_share"].value == pytest.approx(0.25)
    notes = net.result.county_notes
    assert all("straight-line fallback" in text for text, _ in notes.values())
    quarter = _run(walk_ctx, {"tod": {"variant": "tod_network_quarter_mile"}}).reforms["tod"]
    three = _run(walk_ctx, {"tod": {"variant": "tod_network_three_quarter_mile"}}).reforms["tod"]
    q, h, t = (r.records["eligible"].p50 for r in (quarter, net, three))
    assert q < h < t
    # Asked for on a pack without walksheds: straight-line, and said so.
    asked = _run(ctx, {"tod": {"variant": "tod_network_half_mile"}}).reforms["tod"]
    half = _run(ctx, {"tod": {"walk": "straight", "distance": 0.5}}).reforms["tod"]
    np.testing.assert_array_equal(asked.state_draws, half.state_draws)
    assert asked.records["eligible"].p50 == pytest.approx(half.records["eligible"].p50)
    assert any("no walkshed table" in n for n in asked.notes)
    assert all("no network walkshed table" in t for t, _ in asked.result.county_notes.values())


def test_tod_zero_decomposition(ctx):
    o = _run(ctx, {"tod": {"decompose": True}}).reforms["tod"]
    keys = {k for k in o.records if k.startswith("decomp_")}
    assert len(keys) == 12
    assert o.records["decomp_wider_eligible"].p50 >= o.records["decomp_benchmark_eligible"].p50
    text = decomposition_verdict(o.records)
    assert "Eligible land" in text
    assert any("Why this TOD estimate" in n for n in o.notes)
    low = _run(ctx, {"tod": {"variant": "tod_lowrise"}}).reforms["tod"]
    assert low.result.ledger["tod.upzoned_storeys"].central == 4


def test_tod_decomposition_keeps_user_overrides_and_reads_its_settings(ctx, monkeypatch):
    """Order base, scenario, variant, user: a user's pin on a low-rise target still wins."""
    from visionhousing.engine import adapters
    from visionhousing.engine.variants import tod_decomposition

    settings = tod_decomposition()
    assert settings["lowrise_variant"] == "tod_lowrise" and settings["wider_distance"] == 0.75
    seen = []
    real = adapters.tod_funnel

    def spy(tracts, led, sc, **kw):
        seen.append((led["tod.upzoned_storeys"].central, sc.distance))
        return real(tracts, led, sc, **kw)

    monkeypatch.setattr(adapters, "tod_funnel", spy)
    _run(ctx, {"tod": {"decompose": True}}, overrides={"tod.upzoned_storeys": 7})
    assert len(seen) == 4  # the run itself (reused as the benchmark case), low-rise, wider, both
    assert all(storeys == 7 for storeys, _ in seen)
    assert {d for _, d in seen} >= {settings["wider_distance"]}
    # Without the user's pin the low-rise prototype's own storeys are used.
    seen.clear()
    _run(ctx, {"tod": {"decompose": True}})
    low = ctx.ledger["tod.lowrise_storeys"].central
    assert sorted({s for s, _ in seen}) == sorted({ctx.ledger["tod.upzoned_storeys"].central, low})
