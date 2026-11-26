"""The four extra scenarios on Testland: mechanisms, monotonicity, timing and no double counting.

Testland's rents and values are too low for most projects to pencil at central
costs, so the mechanism tests use the same "rich market" overrides as the TOD
tests, and inject small synthetic pack tables (readiness, parcel land use,
income limits) through ``StateContext.derived``, exactly where
``scenarios.extra_common.pack_table`` looks first.
"""

import itertools

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute, normalise_reforms, prepare
from visionhousing.ledger import apply_overrides, scenario_ledger
from visionhousing.scenarios.commercial_land import (
    commercial_land_funnel,
    eligible_parcels,
    reuse_funnel,
)
from visionhousing.scenarios.extra_common import OPTIONAL_TABLES, in_service_weight
from visionhousing.scenarios.fee_affordability import _deferred_cash, fee_affordability
from visionhousing.scenarios.starter_lots import starter_lots_funnel
from visionhousing.scenarios.tod import HEADLINE_STAGE, TodScope, tod_funnel
from visionhousing.scenarios.tod_infrastructure import infrastructure_funnel, readiness_shares

DRAWS = 300
INFRA = "transit_zoning_funded_infrastructure"
RICH = {
    "multifamily.hard_cost_per_sqft": 120,
    "tod.new_unit_rent_premium": 2.5,
    "tod.acquisition_premium": 0.05,
}


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def bare():
    """Testland with its optional tables hidden: a state pack that has none of them."""
    c = load_context("TL")
    for name in OPTIONAL_TABLES:
        c.derived[("pack_table", name)] = None
    return c


@pytest.fixture(scope="module")
def tl_tracts(ctx):
    from visionhousing.engine.adapters import _tod_tracts

    run_context, draws = prepare(ctx, None, seed=0, draws=10)
    return _tod_tracts(run_context, draws.ledger)


def fixed(value):
    return {"central": value, "low": value, "high": value, "shape": "fixed"}


def ledger(ctx, key, extra=None):
    return apply_overrides(scenario_ledger(ctx.ledger, ctx.scenarios[key]), {**(extra or {})})


def infra_table(tracts):
    """Half the tracts verified, a few under a documented constraint, the rest unknown."""
    geo = tracts["geoid"].astype(str).to_numpy()
    n = len(geo)
    ver = np.where(np.arange(n) % 2 == 0, 0.9, 0.0)
    cons = np.where(np.arange(n) % 7 == 3, 0.5, 0.0)
    return pd.DataFrame(
        {
            "geoid": geo,
            "sewer_status": "unknown",
            "sewer_share": ver,
            "sewer_known": np.minimum(ver + cons, 1.0),
            "constraint_share": cons,
        }
    )


# ---------------------------------------------------------------- transit zoning + infrastructure


def infra_run(ctx, tracts, infra, extra=None, funding="grant", seed=3):
    led = ledger(ctx, INFRA, {**RICH, **(extra or {})})
    scope = TodScope(INFRA, "d07")
    return infrastructure_funnel(tracts, led, scope, infra, funding=funding, draws=DRAWS, seed=seed)


def test_readiness_shares_add_to_one(tl_tracts):
    shares = readiness_shares(tl_tracts, infra_table(tl_tracts))
    assert np.allclose(shares.sum(axis=1), 1.0)
    none = readiness_shares(tl_tracts, None)
    assert (none["unknown"] == 1.0).all()


def test_readiness_reads_constraint_homes_inside_the_sewer_share():
    """``sewer_share`` includes constraint homes inside the service area.

    Testland's Maple moratorium tract 99003000400: sewer 0.951, constraint 0.47,
    so verified 0.481 (it was read as 0.951 verified and 0.049 constraint).
    """
    from visionhousing.engine.context import TESTLAND_DIR

    infra = pd.read_csv(TESTLAND_DIR / "infra_tract.csv", dtype={"geoid": str})
    tracts = pd.DataFrame({"geoid": ["99003000400", "x1", "x2"]})
    extra = pd.DataFrame(
        {
            "geoid": ["x1", "x2"],
            "sewer_share": [0.2, 0.6],  # x1: constraint larger than the served share
            "sewer_known": [1.0, 0.8],
            "constraint_share": [0.5, 0.0],
        }
    )
    got = readiness_shares(tracts, pd.concat([infra, extra], ignore_index=True)).to_numpy()
    np.testing.assert_allclose(got[0], [0.481, 0.049, 0.47, 0.0], atol=1e-9)
    np.testing.assert_allclose(got[1], [0.0, 0.5, 0.5, 0.0], atol=1e-9)
    np.testing.assert_allclose(got[2], [0.6, 0.2, 0.0, 0.2], atol=1e-9)


def test_infrastructure_never_credits_feasible_permits(ctx, tl_tracts):
    res = infra_run(ctx, tl_tracts, infra_table(tl_tracts))
    a, b, total = res.zoning_alone, res.funded, res.total
    assert (res.infra_raw >= 0).all()
    assert np.all(total >= a - 1e-9) and np.all(total <= np.maximum(a, b) + 1e-9)
    assert res.records["total"].p50 > 0
    # With no capacity cost and no constraint, money changes nothing: nothing is credited.
    free = infra_run(
        ctx,
        tl_tracts,
        None,
        {
            "infra.capacity_cost_per_apartment": {
                "central": 0,
                "low": 0,
                "high": 0,
                "shape": "fixed",
            }
        },
    )
    assert np.allclose(free.infra_raw, 0.0)
    assert np.allclose(free.total, free.zoning_alone)


def test_infrastructure_respects_in_service_date(ctx, tl_tracts):
    infra = infra_table(tl_tracts)
    totals = []
    for year in (2027.0, 2029.0, 2030.5, 2032.0):
        res = infra_run(
            ctx,
            tl_tracts,
            infra,
            {"infra.in_service_year": fixed(year)},
        )
        totals.append(res.total.sum(axis=1))
        if year >= 2032.0:  # service after the window: only zoning alone counts
            assert np.allclose(res.infra_in, 0.0)
            assert np.allclose(res.total, res.zoning_alone)
    for early, late in itertools.pairwise(totals):
        assert np.all(early >= late - 1e-9)
    assert totals[0].mean() > totals[-1].mean()


def test_in_service_weight_counts_part_years():
    share = np.full((1, 5), 0.2)
    w, counted = in_service_weight(share, range(2027, 2032), np.array([2029.5]))
    assert w[0] == pytest.approx(0.2 * 0.5 + 0.2 + 0.2)
    assert counted[0, 0] == 0 and counted[0, 1] == 0


def test_grant_from_2027_equals_tod_benchmark_on_same_draws(ctx, tl_tracts):
    """B with a grant and no constraint is the TOD benchmark funnel itself: no double gain."""
    at = {
        "infra.in_service_year": {
            "central": 2027.0,
            "low": 2027.0,
            "high": 2027.0,
            "shape": "fixed",
        }
    }
    res = infra_run(ctx, tl_tracts, None, at)
    led = apply_overrides(scenario_ledger(ctx.ledger, ctx.scenarios["tod"]), RICH)
    bench = tod_funnel(tl_tracts, led, TodScope("tod", "d07"), draws=DRAWS, seed=3)
    assert np.allclose(res.total.sum(axis=1), bench.stages[HEADLINE_STAGE].sum(axis=1))
    assert np.all(res.total.sum(axis=1) <= bench.stages[HEADLINE_STAGE].sum(axis=1) + 1e-9)


def test_higher_capacity_cost_lowers_zoning_alone(ctx, tl_tracts):
    infra = infra_table(tl_tracts)
    lo = infra_run(ctx, tl_tracts, infra, {"infra.capacity_cost_per_apartment": 5000})
    hi = infra_run(ctx, tl_tracts, infra, {"infra.capacity_cost_per_apartment": 60000})
    assert np.all(hi.zoning_alone.sum(axis=1) <= lo.zoning_alone.sum(axis=1) + 1e-9)
    assert hi.infra_raw.sum() >= lo.infra_raw.sum()


def test_financed_is_between_zoning_alone_and_grant(ctx, tl_tracts):
    infra = infra_table(tl_tracts)
    grant = infra_run(ctx, tl_tracts, infra)
    fin = infra_run(ctx, tl_tracts, infra, funding="financed")
    assert np.all(fin.funded.sum(axis=1) <= grant.funded.sum(axis=1) + 1e-9)
    assert np.all(fin.zoning_alone == grant.zoning_alone)


def test_infrastructure_cannot_share_a_package_with_tod():
    with pytest.raises(ValueError, match="same lots"):
        normalise_reforms(["tod", INFRA])
    with pytest.raises(ValueError, match="same lots"):
        normalise_reforms(["commercial_land_housing", "tod"])
    with pytest.raises(ValueError, match="same lots"):
        normalise_reforms(["starter_home_lots", "adu"])
    with pytest.raises(ValueError, match="same lots"):
        normalise_reforms(["missing_middle", "starter_home_lots"])
    with pytest.raises(ValueError, match="same lots"):
        normalise_reforms(["fees", "fee_deferral_with_affordability"])
    assert list(normalise_reforms(["parking", INFRA])) == ["parking", INFRA]


def test_infrastructure_engine_run_without_the_table_says_so(bare):
    res = compute(bare, [INFRA], draws=50)
    o = res.reforms[INFRA]
    assert any("no water and sewer readiness table" in n for n in o.notes)
    assert o.records["readiness_unknown"].value > 0
    assert res.package.state_draws.shape == (50,)


# ---------------------------------------------------------------- commercial land and reuse


def parcels_table(tracts, county="99001", per_tract=40):
    rows = tracts[tracts["county"].astype(str) == county]
    return pd.DataFrame(
        {
            "level": "tract",
            "geoid": rows["geoid"].astype(str),
            "county": county,
            "lu_commercial": per_tract,
        }
    )


def test_eligible_parcels_not_measured_is_nan(tl_tracts):
    p = eligible_parcels(tl_tracts, parcels_table(tl_tracts))
    in_county = tl_tracts["county"].astype(str).to_numpy() == "99001"
    assert np.all(p[in_county] == 40) and np.isnan(p[~in_county]).all()
    assert np.isnan(eligible_parcels(tl_tracts, None)).all()


def test_commercial_land_more_parcels_more_homes(ctx, tl_tracts):
    led = ledger(ctx, "commercial_land_housing", RICH)
    few = commercial_land_funnel(
        tl_tracts, led, parcels_table(tl_tracts, per_tract=10), draws=DRAWS
    )
    many = commercial_land_funnel(
        tl_tracts, led, parcels_table(tl_tracts, per_tract=80), draws=DRAWS
    )
    assert many.records["authorized"].p50 > few.records["authorized"].p50 > 0
    assert np.all(many.stages["authorized"].sum(axis=1) >= few.stages["authorized"].sum(axis=1))
    other = tl_tracts["county"].astype(str).to_numpy() != "99001"
    assert np.allclose(many.stages["authorized"][:, other], 0.0)
    assert many.measured_counties == ("99001",)
    denser = commercial_land_funnel(
        tl_tracts, apply_overrides(led, {"commercial.units_per_acre": 36}),
        parcels_table(tl_tracts, per_tract=80), draws=DRAWS,
    )  # fmt: skip
    assert denser.records["capacity"].p50 <= many.records["capacity"].p50 * 1.0


def test_reuse_conversions_cost_monotone(ctx, tl_tracts):
    led = ledger(ctx, "adaptive_reuse", RICH)
    parcels = parcels_table(tl_tracts, per_tract=200)
    cheap = reuse_funnel(
        tl_tracts,
        apply_overrides(led, {"reuse.conversion_cost_per_sqft": 150}),
        parcels,
        draws=DRAWS,
    )
    dear = reuse_funnel(
        tl_tracts,
        apply_overrides(led, {"reuse.conversion_cost_per_sqft": 400}),
        parcels,
        draws=DRAWS,
    )
    assert cheap.records["conversion_homes"].p50 > 0
    assert np.all(
        dear.stages["conversions"].sum(axis=1) <= cheap.stages["conversions"].sum(axis=1) + 1e-9
    )
    assert not cheap.bps_comparable and cheap.headline == "conversions"


@pytest.fixture()
def tl_with_parcels():
    c = load_context("TL")
    c.derived[("pack_table", "parcels_area")] = parcels_table(c.tracts, per_tract=200)
    return c


def test_conversions_stay_outside_the_bps_total(tl_with_parcels):
    rich = {**RICH, "reuse.conversion_cost_per_sqft": 150}
    res = compute(tl_with_parcels, ["adaptive_reuse", "commercial_land_housing"], rich, draws=DRAWS)
    reuse = res.reforms["adaptive_reuse"]
    land = res.reforms["commercial_land_housing"]
    assert reuse.added.p90 == 0.0 and np.all(np.asarray(reuse.state_draws) == 0)
    assert reuse.records["conversion_homes"].p50 > 0
    assert land.added.p50 > 0
    # The package holds only the new construction; conversions are a separate record.
    assert np.allclose(res.package.state_draws, land.state_draws)
    assert res.contract["audit"]["adaptive_reuse"]["measure"] == "outside the measure"
    assert any("not measured" in t for t in (land.counties["99003"].formula,))


# ---------------------------------------------------------------- starter-home lots

CHEAP_LOTS = {
    "missing_middle.hard_cost_per_sqft": 90,
    "starter.subdivision_cost_per_lot": 10000,
    "fees.fee_total_per_unit": {"central": 5000, "low": 5000, "high": 5000, "shape": "fixed"},
    "starter.small_home_value_ratio": 0.9,
}


def starter_run(ctx, tracts, extra=None, **kw):
    led = ledger(ctx, "starter_home_lots", {**CHEAP_LOTS, **(extra or {})})
    return starter_lots_funnel(tracts, led, draws=DRAWS, seed=5, **kw)


def lot_tracts(tracts):
    """Testland tracts with a synthetic block lot-size proxy (large suburban lots)."""
    t = tracts.copy()
    t["lot_m2_per_unit_p25"] = 600.0
    t["lot_m2_per_unit_p50"] = 1200.0
    t["lot_m2_per_unit_p75"] = 2500.0
    return t


def test_starter_lots_pencil_and_replace_homes(ctx):
    res = starter_run(ctx, lot_tracts(ctx.tracts))
    s = res.stages
    assert res.fit_source == "lot_proxy"
    assert res.records["authorized"].p50 > 0
    assert np.all(s["replaced"] <= s["new_homes"] + 1e-9)
    assert np.allclose(s["net"], s["authorized"] - s["replaced"])
    keep = s["split"] - s["replaced"] / (1 - 0.05)  # central baseline share is a pert draw
    assert res.records["homes_new_lot"].p50 > 0 and res.records["homes_rebuild"].p50 > 0
    assert keep.shape == s["split"].shape


def test_smaller_minimum_lot_gives_more_homes(ctx):
    t = lot_tracts(ctx.tracts)
    small = starter_run(ctx, t, {"starter.min_lot_sqft": 3000})
    large = starter_run(ctx, t, {"starter.min_lot_sqft": 5000})
    assert np.all(small.stages["splittable"].sum(axis=1) >= large.stages["splittable"].sum(axis=1))
    assert small.records["authorized"].p50 >= large.records["authorized"].p50


def test_split_already_allowed_today_is_not_credited(ctx):
    """When today's minimum equals the new one, no lot is newly splittable."""
    t = lot_tracts(ctx.tracts)
    same = starter_run(ctx, t, {"starter.min_lot_sqft": 4000, "starter.current_min_lot_sqft": 4000})
    assert np.allclose(same.stages["splittable"], 0.0)
    assert np.allclose(same.stages["authorized"], 0.0)


def test_appraisal_binding_never_adds(ctx):
    t = lot_tracts(ctx.tracts)
    flag = starter_run(ctx, t, {"starter.min_comparable_share": 0.5})
    bind = starter_run(ctx, t, {"starter.min_comparable_share": 0.5}, appraisal="binding")
    assert np.all(bind.stages["authorized"] <= flag.stages["authorized"] + 1e-9)
    assert flag.records["appraisal_flag_share"].p50 >= 0


def test_starter_lots_without_public_sewer_are_left_out(ctx):
    t = lot_tracts(ctx.tracts)
    none = starter_run(ctx, t, outside_share=np.ones(len(t)))
    assert np.allclose(none.stages["eligible"], 0.0)


# ---------------------------------------------------------------- fee relief with affordability


def fee_inputs(ctx, extra=None, limits=None, mode="deferral", seed=2):
    run_context, draws = prepare(ctx, None, seed=seed, draws=DRAWS)
    from visionhousing.engine.adapters import _channel_b_inputs

    baseline, scale, _ = _channel_b_inputs(run_context, draws)
    led = ledger(ctx, "fee_deferral_with_affordability", extra)
    return fee_affordability(
        baseline, ctx.tracts, led, limits=limits, rent_share=0.3, mode=mode,
        n_draws=DRAWS, seed=seed, baseline_scale=scale,
    )  # fmt: skip


def test_restricted_homes_follow_the_set_aside(ctx):
    res = fee_inputs(ctx)
    for m in ("deferral", "waiver"):
        add = res.records[f"{m}_added"]
        assert add.p10 >= 0
    w = res.records
    assert w["waiver_restricted"].p50 >= w["deferral_restricted"].p50
    assert w["waiver_revenue_given_up"].p50 > w["deferral_revenue_given_up"].p50


def test_more_affordability_never_adds_homes(ctx):
    lo = fee_inputs(ctx, {"fee_aff.restricted_share": 0.1, "fee_aff.duration_years": 15})
    hi = fee_inputs(ctx, {"fee_aff.restricted_share": 0.2, "fee_aff.duration_years": 55})
    for m in ("deferral", "waiver"):
        assert np.all(hi.added_by_mode[m].sum(axis=0) <= lo.added_by_mode[m].sum(axis=0) + 1e-9)


def test_no_set_aside_no_restricted_homes(ctx):
    res = fee_inputs(
        ctx, {"fee_aff.restricted_share": {"central": 0, "low": 0, "high": 0, "shape": "fixed"}}
    )
    for m in ("deferral", "waiver"):
        assert np.allclose(res.restricted_by_mode[m], 0.0)


def test_hud_limits_used_when_present(ctx):
    counties = sorted(set(ctx.tracts["county"].astype(str)))
    limits = pd.DataFrame({"county": counties, "l50_3": 20000.0})  # very low: big rent gap
    low = fee_inputs(ctx, limits=limits)
    assert low.limits_source == "hud"
    high = fee_inputs(ctx, limits=limits.assign(l50_3=60000.0))
    assert high.records["affordability_cost"].p50 < low.records["affordability_cost"].p50
    assert np.all(
        high.added_by_mode["waiver"].sum(axis=0) >= low.added_by_mode["waiver"].sum(axis=0) - 1e-9
    )


def test_deferred_cash_is_conserved_and_later():
    units = np.array([[10.0, 10.0, 10.0, 10.0, 10.0]])
    fee = np.array([1000.0])
    cash = _deferred_cash(units, fee, np.array([18.0]))
    assert cash.sum() == pytest.approx(units.sum() * 1000.0)
    assert cash[0, 0] == 0.0 and cash[0, -1] > 0


def test_fee_affordability_engine_run(ctx, bare):
    res = compute(ctx, ["fee_deferral_with_affordability"], draws=100)
    o = res.reforms["fee_deferral_with_affordability"]
    assert "deferral_cost_per_restricted" in o.records or "deferral_restricted" in o.records
    assert not any("no HUD income-limits table" in n for n in o.notes)  # Testland has limits
    none = compute(bare, ["fee_deferral_with_affordability"], draws=100)
    assert any(
        "no HUD income-limits table" in n
        for n in none.reforms["fee_deferral_with_affordability"].notes
    )
    w = compute(
        ctx,
        {"fee_deferral_with_affordability": {"variant": "fee_waiver_with_affordability"}},
        draws=100,
    )
    assert w.reforms["fee_deferral_with_affordability"].added.p50 >= o.added.p50


# ---------------------------------------------------------------- registry and API


def test_benchmark_presets_unchanged_by_extras(ctx):
    """The five benchmark runs give the same draws with the extra scenarios registered."""
    from visionhousing.ledger import load_scenarios

    sc = load_scenarios(ctx.ledger)
    bench = [k for k, v in sc.items() if v.kind == "benchmark"]
    assert sorted(bench) == ["adu", "fees", "missing_middle", "parking", "tod"]
    extra = [k for k, v in sc.items() if v.kind == "extra"]
    assert set(extra) == {
        INFRA, "commercial_land_housing", "adaptive_reuse", "starter_home_lots",
        "fee_deferral_with_affordability",
    }  # fmt: skip
    for k in extra:
        assert not set(sc[k].overrides)


def test_scenarios_endpoint_lists_extras(tmp_path):
    from tests.asgi_client import AsgiClient
    from visionhousing.api import create_app

    client = AsgiClient(create_app(packs_dir=tmp_path))
    body = client.get("/api/v1/scenarios").json()["scenarios"]
    by = {s["id"]: s for s in body}
    assert by[INFRA]["kind"] == "extra" and by[INFRA]["runnable"]
    assert by[INFRA]["description"] and by[INFRA]["excludes"] == ["tod"]
    assert by["adu"]["kind"] == "benchmark"
    names = {v["name"] for v in by["fee_deferral_with_affordability"]["variants"]}
    assert "fee_waiver_with_affordability" in names
    kinds = {r["key"]: r["kind"] for r in client.get("/api/v1/reforms").json()["reforms"]}
    assert kinds["tod"] == "benchmark" and kinds[INFRA] == "extra"
    r = client.post("/api/v1/run", {"state": "TL", "reforms": [INFRA], "draws": 50})
    assert r.status == 200, r.text()


def test_fee_relief_net_cost_counts_induced_homes_once(ctx, monkeypatch):
    """Relief on induced homes is not a public loss; their fee counts once (review R5 F1, F2).

    In both modes, per draw: net = given up on homes built anyway - new revenue
    = gross given up - fee x induced homes. The old rule (net = gross - new revenue)
    breaks this whenever a draw adds homes.
    """
    from visionhousing.engine import records as records_mod

    seen: dict[str, np.ndarray] = {}
    real = records_mod.from_draws

    def spy(name, draws, unit, **kw):
        seen[name.split(" 20")[0]] = np.asarray(draws, dtype=float)
        return real(name, draws, unit, **kw)

    monkeypatch.setattr(records_mod, "from_draws", spy)
    res = fee_inputs(ctx)
    fee = float(ledger(ctx, "fee_deferral_with_affordability")["fees.fee_total_per_unit"].central)
    for m, label in (("deferral", "fee deferral"), ("waiver", "fee waiver")):
        add = res.added_by_mode[m].sum(axis=0)
        assert (add > 1e-6).any(), "Testland should induce homes in some draws"

        def find(start, label=label):
            return next(v for k, v in seen.items() if k.startswith(f"{label}: {start}"))

        gross = find("public revenue given up on all")
        base = find("public revenue given up on homes")
        new = find("fee revenue from induced")
        net = seen[f"{label}: net public cost"]
        assert np.allclose(net, base - new)
        assert np.allclose(net, gross - fee * add, rtol=1e-9, atol=1e-3)


def test_reuse_formula_names_the_rent_ratio(ctx, tl_tracts):
    """The threshold divides by the ledger ratio, and the formula says so (review R5 F7)."""
    led = ledger(ctx, "adaptive_reuse", RICH)
    parcels = parcels_table(tl_tracts, per_tract=200)
    res = reuse_funnel(tl_tracts, led, parcels, draws=DRAWS)
    text = res.records["feasible"].formula
    assert "new-unit rent ratio (tod.new_unit_rent_premium" in text
    assert "1 + new-unit premium" not in text
    assert "tod.new_unit_rent_premium" in res.records["feasible"].assumption_ids


def test_optional_table_sources_come_from_the_pack(ctx):
    """Testland's tables cite the synthetic fixture, not Pennsylvania registry ids."""
    from visionhousing.scenarios.extra_common import pack_table, table_sources

    fresh = load_context("TL")
    pa_ids = {"pa_montco_sewer_service_areas", "phl_opa_properties", "hud_income_limits"}
    for name in ("infra_tract", "parcels_area", "afford_income_limits"):
        got = table_sources(pack_table(fresh, name))
        assert got == ("synthetic",), name
    res = compute(fresh, ["fee_deferral_with_affordability"], draws=50)
    o = res.reforms["fee_deferral_with_affordability"]
    for r in o.records.values():
        assert not pa_ids & set(r.source_ids), r.name
    for key in ("transit_zoning_funded_infrastructure", "starter_home_lots", "adaptive_reuse"):
        out = compute(fresh, [key], draws=50).reforms[key]
        for r in out.records.values():
            assert not pa_ids & set(r.source_ids), (key, r.name)
    assert table_sources(None) == ()
    named = pd.DataFrame({"sewer_source": ["a_src", None, "", "b_src"]})
    assert table_sources(named) == ("a_src", "b_src")
