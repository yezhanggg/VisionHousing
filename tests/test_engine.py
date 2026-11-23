"""Engine contract: registry, adapters, records, hashes, cache, context, speed."""

import json
import time
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import baseline_from_ledger
from visionhousing.engine import core as engine
from visionhousing.engine.adapters import ADU_FEE_IDS, scaled_entry
from visionhousing.engine.context import TESTLAND_DIR, load_context
from visionhousing.engine.core import cache_info, clear_cache, compute, normalise_reforms, run
from visionhousing.engine.hashes import code_hash
from visionhousing.engine.montecarlo import baseline_positions, scale_at
from visionhousing.engine.registry import REGISTRY, ReformResult
from visionhousing.ledger import apply_overrides, sample, scenario_ledger
from visionhousing.scenarios.adu import adu_funnel, scope_from_scenario
from visionhousing.scenarios.fees import baseline_by_type, fee_reform

DRAWS = 500


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def package(ctx):
    return compute(ctx, ["adu", "fees", "parking"], seed=3, draws=DRAWS)


# ---------------------------------------------------------------- registry and inputs


def test_registry_holds_the_built_reforms():
    assert {"adu", "fees", "parking", "pa_hb2186", "pa_sb1346"} <= set(REGISTRY)
    assert REGISTRY["pa_sb1346"].family == "adu"
    assert REGISTRY["fees"].family == "fees"


def test_reform_arguments_are_normalised_and_checked():
    assert normalise_reforms(None) == {}
    assert normalise_reforms("adu") == {"adu": {}}
    assert normalise_reforms(["fees", "adu", "fees"]) == {"fees": {}, "adu": {}}
    assert normalise_reforms({"fees": {"view": "state"}}) == {"fees": {"view": "state"}}
    with pytest.raises(KeyError, match="unknown reform"):
        normalise_reforms(["granny_flats"])
    with pytest.raises(ValueError, match="same lots"):
        normalise_reforms(["adu", "pa_hb2186"])


def test_reform_result_checks_its_shapes():
    head = run("TL", "adu", draws=50).reforms["adu"].added
    with pytest.raises(ValueError, match="shape"):
        ReformResult("x", "x", "county", ("a",), ("a",), np.zeros((2, 3)), head)
    with pytest.raises(ValueError, match="non-finite"):
        ReformResult("x", "x", "county", ("a",), ("a",), np.array([[np.nan]]), head)
    r = ReformResult("x", "x", "tract", ("t1", "t2"), ("c1", "c1"), np.ones((2, 3)), head)
    assert r.county_draws(("c0", "c1")).tolist() == [[0, 0, 0], [2, 2, 2]]
    with pytest.raises(ValueError, match="unknown counties"):
        r.county_draws(("c0",))


def test_scaled_entry_scales_every_draw_exactly(ctx):
    param = ctx.ledger["adu.permit_fee"]
    led = apply_overrides(ctx.ledger, {"adu.permit_fee": scaled_entry(param, 0.5)})
    a = sample(ctx.ledger, n=200, seed=4, ids=["adu.permit_fee"])["adu.permit_fee"]
    b = sample(led, n=200, seed=4, ids=["adu.permit_fee"])["adu.permit_fee"]
    assert np.allclose(b, 0.5 * a)
    zero = scaled_entry(param, 0.0)
    assert zero["shape"] == "fixed" and zero["central"] == 0


# ---------------------------------------------------------------- the result


def test_baseline_is_the_baseline_module_unchanged(ctx, package):
    expected, _ = baseline_from_ledger(ctx.permits, ctx.ledger)
    assert package.baseline == expected.state
    assert package.baseline_counties == {str(c): r for c, r in expected.counties.items()}


def test_baseline_draws_follow_the_baseline_range(ctx):
    res = compute(ctx, None, seed=0, draws=2000)
    p10, p50, p90 = np.percentile(res.baseline_draws, [10, 50, 90])
    b = res.baseline
    assert p50 == pytest.approx(b.p50, rel=1e-3)
    assert p10 == pytest.approx(b.p10, rel=1e-2)
    assert p90 == pytest.approx(b.p90, rel=1e-2)
    u = baseline_positions(1000, 5)
    assert np.all((u >= 0) & (u < 1))
    assert np.histogram(u, bins=10, range=(0, 1))[0].tolist() == [100] * 10
    assert scale_at(0.5, 0.4, 1.2) == pytest.approx(1.0)
    assert scale_at(0.1, 0.4, 1.2) == pytest.approx(0.4)
    assert scale_at(0.9, 0.4, 1.2) == pytest.approx(1.2)
    assert scale_at(0.0, 0.1, 1.2) >= 0


def test_adu_outcome_equals_a_direct_call(ctx, package):
    scenario = ctx.scenarios["adu"]
    direct = adu_funnel(
        ctx.tracts,
        scenario_ledger(ctx.ledger, scenario),
        ctx.fit,
        scope_from_scenario("adu", scenario, ctx.legal),
        draws=DRAWS,
        seed=3,
    )
    out = package.reforms["adu"]
    assert np.allclose(out.state_draws, direct.stages["net"].sum(axis=1))
    assert out.added.p50 == pytest.approx(direct.records["net"].p50)


def test_fee_outcome_equals_a_direct_call_with_the_baseline_scale(ctx, package):
    base, _ = baseline_from_ledger(ctx.permits, ctx.ledger)
    types = baseline_by_type(base)
    scale = package.draws_obj.baseline_scale
    direct = fee_reform(types, ctx.tracts, ctx.ledger, n_draws=DRAWS, seed=3, baseline_scale=scale)
    assert np.allclose(package.reforms["fees"].state_draws, direct.state_draws())


def test_percent_change_is_added_over_baseline_in_each_draw(package):
    out = package.reforms["fees"]
    pct = 100 * out.state_draws / package.baseline_draws
    assert out.pct_change.p50 == pytest.approx(np.percentile(pct, 50))
    assert out.pct_change.unit == "% of baseline"


def test_every_record_has_a_formula_and_reform_records_have_ids(package):
    for record in package.records():
        assert record.formula
    for out in package.reforms.values():
        for record in (out.added, out.pct_change, *out.counties.values()):
            assert record.assumption_ids, record.name
            assert record.source_ids, record.name
        assert out.steps
    assert "fees.fee_cut_share" in package.package.interaction.assumption_ids
    assert len(package.package.waterfall) == 4


def test_to_dict_is_plain_json(package):
    data = package.to_dict()
    text = json.dumps(data)
    assert json.loads(text)["hashes"] == data["hashes"]
    assert set(data["reform_results"]) == {"adu", "fees", "parking"}
    assert data["package"]["waterfall"][-1]["name"].startswith("combined added units")


def test_user_override_wins_over_the_scenario(ctx):
    res = compute(ctx, "pa_sb1346", {"adu.utility_connection_fee": 5000}, draws=50)
    used = res.reforms["pa_sb1346"].result.ledger
    assert used["adu.utility_connection_fee"].central == 5000
    plain = compute(ctx, "pa_sb1346", draws=50).reforms["pa_sb1346"].result.ledger
    assert plain["adu.utility_connection_fee"].central == 0


def test_fee_state_view_is_an_option(ctx):
    res = compute(ctx, {"fees": {"view": "state"}}, draws=100)
    assert res.reforms["fees"].result.detail.view == "state"
    with pytest.raises(ValueError, match="view"):
        compute(ctx, {"fees": {"view": "local"}}, draws=10)


def test_shared_draws_are_the_reform_draws(ctx, package):
    """The engine's shared samples equal the draws each reform takes from its ledger."""
    used = package.reforms["adu"].result.ledger
    ids = ["adu.takeup_feasible_annual", "econ.mortgage_rate_30y"]
    mine = sample(used, n=DRAWS, seed=3, ids=ids)
    for pid in ids:
        assert np.array_equal(mine[pid], package.draws_obj.values[pid])


def test_adu_fee_lines_are_cut_in_the_package(ctx, package):
    """The interaction run sees the ADU fee lines cut by the fee reform's share."""
    cut = ctx.ledger["fees.fee_cut_share"].central
    opts = {"package_fee_cut_share": cut}
    res = REGISTRY["adu"].adapter(package.run_context, package.draws_obj, opts)
    for pid in ADU_FEE_IDS:
        assert res.ledger[pid].central == pytest.approx(ctx.ledger[pid].central * (1 - cut))


# ---------------------------------------------------------------- hashes, cache, context


def test_three_hashes(ctx):
    a = compute(ctx, "adu", draws=20)
    b = compute(ctx, "adu", {"adu.takeup_feasible_annual": 0.01}, draws=20)
    for h in a.hashes.as_tuple():
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert a.hashes.code == b.hashes.code == code_hash()
    assert a.hashes.data == b.hashes.data
    assert a.hashes.assumptions != b.hashes.assumptions
    other = replace(ctx, data_hash="0" * 64)
    assert compute(other, "adu", draws=20).hashes.data == "0" * 64
    assert set(a.hashes.short()) == {"code", "data", "assumptions"}


def test_result_cache():
    clear_cache()
    first = run("TL", "adu", draws=100, seed=1)
    again = run("TL", ["adu"], draws=100, seed=1)
    assert again is first
    assert cache_info()["hits"] == 1
    other = run("TL", "adu", draws=100, seed=2)
    assert other is not first
    fresh = run("TL", "adu", draws=100, seed=1, use_cache=False)
    assert fresh is not first
    assert np.array_equal(fresh.reforms["adu"].state_draws, first.reforms["adu"].state_draws)
    for _ in range(engine.CACHE_SIZE + 2):
        run("TL", None, draws=10, seed=_)
    assert cache_info()["size"] <= engine.CACHE_SIZE


def test_missing_state_pack_says_how_to_build_it():
    with pytest.raises(FileNotFoundError, match="make data STATE=ZZ"):
        run("ZZ", "adu")


def test_context_reads_a_pack_folder(tmp_path):
    for name in ("tracts.csv", "permits_county_year.csv", "blocks.csv", "transit_stops.csv"):
        (tmp_path / name).write_bytes((TESTLAND_DIR / name).read_bytes())
    ctx = load_context("QQ", folder=tmp_path)
    assert ctx.boundaries is None and not ctx.synthetic
    assert ctx.data_hash != load_context("TL").data_hash
    assert all(isinstance(g, str) for g in ctx.tracts["geoid"])
    (tmp_path / "blocks.csv").unlink()
    with pytest.raises(FileNotFoundError, match=r"blocks\.csv"):
        load_context("QQ", folder=tmp_path)


def test_testland_notes_say_synthetic(package):
    assert package.synthetic
    assert any("synthetic" in n for n in package.notes)


def test_single_reform_speed(ctx):
    """A Testland single-reform run with 2,000 draws takes about a second or less."""
    compute(ctx, "fees", draws=50)  # warm up
    for key in ("adu", "fees", "parking"):
        start = time.perf_counter()
        compute(ctx, key, draws=2000)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"{key}: {elapsed:.2f}s"


def test_county_order_is_sorted_and_complete(ctx, package):
    counties = sorted(set(ctx.tracts["county"]) | set(ctx.permits["county"]))
    assert list(package.county_ids) == counties
    assert isinstance(package.baseline_county_draws, np.ndarray)
    assert package.baseline_county_draws.shape == (len(counties), DRAWS)
    assert not package.reforms["adu"].state_draws.flags.writeable
    assert isinstance(ctx.permits, pd.DataFrame)


def test_context_reads_a_boundaries_folder(tmp_path):
    """A pack may hold its outlines as boundaries/counties.geojson + boundaries/tracts/*."""
    for name in ("tracts.csv", "permits_county_year.csv", "blocks.csv", "transit_stops.csv"):
        (tmp_path / name).write_bytes((TESTLAND_DIR / name).read_bytes())
    whole = json.loads((TESTLAND_DIR / "boundaries.geojson").read_text())
    feats = whole["features"]
    counties = [f for f in feats if f["properties"]["level"] == "county"]
    tracts = [f for f in feats if f["properties"]["level"] == "tract"]
    (tmp_path / "boundaries" / "tracts").mkdir(parents=True)
    (tmp_path / "boundaries" / "counties.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": counties})
    )
    for c in sorted({f["properties"]["geoid"][:5] for f in tracts}):
        part = [f for f in tracts if f["properties"]["geoid"].startswith(c)]
        (tmp_path / "boundaries" / "tracts" / f"{c}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": part})
        )
    ctx = load_context("QQ", folder=tmp_path)
    got = ctx.boundaries["features"]
    assert len(got) == len(feats)
    assert sorted(f["properties"]["geoid"] for f in got) == sorted(
        f["properties"]["geoid"] for f in feats
    )
    flat = load_context("TL")
    a = compute(ctx, "parking", draws=100, seed=1).reforms["parking"].state_draws
    b = compute(flat, "parking", draws=100, seed=1).reforms["parking"].state_draws
    assert np.allclose(a, b)
