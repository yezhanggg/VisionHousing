"""Practical capacity screen (``engine.capacity``) on Testland.

Most tests inject small synthetic ``infra_tract`` and ``hazard_tract`` tables
through ``StateContext.derived`` (in place of Testland's own fixture tables),
exactly where ``scenarios.extra_common.pack_table`` looks first.
"""

import numpy as np
import pandas as pd
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine.capacity import (
    FLOOD_BUILD,
    PARAMS,
    SEPTIC_SINGLE,
    UNKNOWN_SERVED,
    load_settings,
    multipliers,
    parse_settings,
    tract_inputs,
)
from visionhousing.engine.context import load_context
from visionhousing.engine.core import compute
from visionhousing.ledger import LedgerError, load_ledger, sample
from visionhousing.ledger.loader import read_yaml

DRAWS = 300
SEED = 5
KEYS = ["adu", "missing_middle", "tod", "parking", "fees"]
CHEAP = {
    "missing_middle.hard_cost_per_sqft": 120.0,
    "missing_middle.cap_rate": 0.045,
    "multifamily.hard_cost_per_sqft": 120,
    "tod.new_unit_rent_premium": 2.5,
    "tod.acquisition_premium": 0.05,
}
"""Overrides that make 2-6 unit buildings and TOD pencil on some Testland tracts."""


def fixed(value):
    return {"central": value, "low": value, "high": value, "shape": "fixed"}


def tables(ctx, *, flood=None):
    """Even tracts verified sewer, every third outside, the rest unknown; some flood, some steep."""
    geo = ctx.tracts["geoid"].astype(str).to_numpy()
    n = len(geo)
    i = np.arange(n)
    ver = np.where(i % 2 == 0, 1.0, 0.0)
    out = np.where((i % 2 == 1) & (i % 3 == 0), 1.0, 0.0)
    status = np.where(ver == 1, "verified", np.where(out == 1, "outside", "unknown"))
    infra = pd.DataFrame(
        {
            "geoid": geo,
            "sewer_status": status,
            "sewer_share": ver,
            "sewer_known": ver + out,
            "constraint_share": 0.0,
        }
    )
    sfha = np.where(i % 5 == 0, np.nan, 0.1) if flood is None else np.full(n, flood)
    hazard = pd.DataFrame({"geoid": geo, "sfha_share": sfha, "steep_share": 0.2})
    return infra, hazard


def with_tables(**kw):
    c = load_context("TL")
    infra, hazard = tables(c, **kw)
    c.derived[("pack_table", "infra_tract")] = infra
    c.derived[("pack_table", "hazard_tract")] = hazard
    return c


@pytest.fixture(scope="module")
def ctx():
    return with_tables()


@pytest.fixture(scope="module")
def today(ctx):
    return compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS)


@pytest.fixture(scope="module")
def screened(ctx):
    return compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS, capacity_screen=True)


# ---------------------------------------------------------------- not screened = today


def test_not_screened_is_today_exactly(today, screened):
    assert today.capacity is None and "capacity" not in today.to_dict()
    assert today.package.combined == screened.package.combined
    for k in KEYS:
        np.testing.assert_array_equal(today.reforms[k].state_draws, screened.reforms[k].state_draws)
        assert today.reforms[k].added == screened.reforms[k].added
        assert screened.capacity.reforms[k]["not_screened"] == today.reforms[k].added
    assert screened.capacity.package["not_screened"] == today.package.combined
    body = screened.to_dict()["capacity"]
    assert set(body["reforms"]) == set(KEYS) and body["coverage"]["wildfire"]["measured"] is False


def test_the_reforms_add_homes_to_screen(screened):
    for k in ("adu", "missing_middle", "tod", "parking", "fees"):
        assert screened.reforms[k].added.p90 > 0, k


# ---------------------------------------------------------------- monotone


def test_screening_never_adds_a_home(today, screened):
    cap = screened.capacity
    for k in KEYS:
        before = np.asarray(today.reforms[k].county_draws)
        after = cap.county_draws[k]
        assert np.all(after <= before + 1e-9), k
        assert np.all(after >= -1e-9), k
        assert cap.reforms[k]["change"].p90 <= 1e-9
    assert np.all(cap.state_draws["package"] <= np.asarray(today.package.state_draws) + 1e-9)
    assert cap.reforms["adu"]["change"].p50 < 0


def test_multipliers_are_shares_and_fall_with_more_hazard(ctx):
    led = load_ledger()
    v = sample(led, n=DRAWS, seed=SEED, ids=PARAMS)
    inp = tract_inputs(ctx)
    rows = np.arange(len(inp.geoids))
    m = multipliers(inp, rows, v, "adu")
    for arr in m.values():
        assert arr.min() >= 0.0 and arr.max() <= 1.0
    worse = with_tables(flood=0.9)
    mw = multipliers(tract_inputs(worse), rows, v, "adu")
    assert np.all(mw["flood"] <= m["flood"] + 1e-12)
    assert np.all(mw["flood"][:-1] < 1.0)


def test_mitigation_everywhere_removes_nothing(ctx):
    every = {pid: fixed(1.0) for pid in PARAMS if "buildable" in pid or pid == UNKNOWN_SERVED}
    every["capacity.steep_lot_exposure"] = fixed(0.0)
    over = {**CHEAP, **every}
    base = compute(ctx, ["adu", "fees"], over, seed=SEED, draws=DRAWS)
    res = compute(ctx, ["adu", "fees"], over, seed=SEED, draws=DRAWS, capacity_screen=True)
    for k in ("adu", "fees"):
        np.testing.assert_allclose(res.capacity.state_draws[k], base.reforms[k].state_draws)


# ---------------------------------------------------------------- unknown is a range


def test_unknown_sewer_is_drawn_not_assumed_served(ctx):
    led = load_ledger()
    v = sample(led, n=DRAWS, seed=SEED, ids=PARAMS)
    inp = tract_inputs(ctx)
    status = ctx.derived[("pack_table", "infra_tract")]["sewer_status"].to_numpy()
    rows = np.arange(len(inp.geoids))  # every tract plus the "not listed" row
    sewer = multipliers(inp, rows, v, "adu")["sewer"]
    verified = np.append(status == "verified", False)
    unknown = np.append(status == "unknown", False)
    np.testing.assert_allclose(sewer[verified], 1.0)
    p, septic = v[UNKNOWN_SERVED], v[SEPTIC_SINGLE]
    np.testing.assert_allclose(
        sewer[unknown], np.broadcast_to(p + (1 - p) * septic, sewer[unknown].shape)
    )
    assert sewer[unknown].max() < 1.0 and np.ptp(sewer[unknown][0]) > 0.05
    # A tract no table lists (the last row) is wholly unknown.
    np.testing.assert_allclose(sewer[-1], p + (1 - p) * septic)


def test_unmapped_flood_takes_the_county_share_times_a_drawn_factor(ctx):
    led = load_ledger()
    v = sample(led, n=DRAWS, seed=SEED, ids=PARAMS)
    inp = tract_inputs(ctx)
    blank = ~np.isfinite(inp.flood[:-1])
    assert blank.any()
    flood = multipliers(inp, np.flatnonzero(blank), v, "adu")["flood"]
    expected = 1 - np.clip(0.1 * v["capacity.flood_unmapped_factor"], 0, 1) * (1 - v[FLOOD_BUILD])
    np.testing.assert_allclose(flood, np.broadcast_to(expected, flood.shape))


def test_a_state_without_tables_is_screened_from_ranges_and_says_so():
    bare = load_context("TL")
    for name in ("infra_tract", "hazard_tract"):  # Testland has both; hide them
        bare.derived[("pack_table", name)] = None
    res = compute(bare, ["adu"], seed=SEED, draws=DRAWS, capacity_screen=True)
    cov = res.capacity.coverage
    for name in ("sewer", "flood", "steep", "wildfire"):
        assert cov[name]["measured"] is False and cov[name]["homes_share"].value == 0.0
    assert "drawn from a ledger range" in cov["flood"]["text"]
    assert "removes nothing" in cov["wildfire"]["text"]
    assert res.capacity.reforms["adu"]["change"].p50 < 0


def test_coverage_counts_measured_tracts_and_homes(ctx, screened):
    cov = screened.capacity.coverage
    n = len(ctx.tracts)
    i = np.arange(n)
    known = (i % 2 == 0) | (i % 3 == 0)
    assert cov["sewer"]["tracts_measured"] == int(known.sum())
    assert cov["sewer"]["tracts_share"].value == pytest.approx(known.mean())
    assert cov["flood"]["tracts_measured"] == int((i % 5 != 0).sum())
    assert cov["steep"]["tracts_share"].value == 1.0
    assert cov["wildfire"]["tracts_share"].value == 0.0
    assert "of homes" in cov["sewer"]["text"]


# ---------------------------------------------------------------- paired draws and binding


def test_variant_pairs_with_the_run_option(ctx, screened):
    for k in KEYS:
        v = compute(ctx, {k: {"variant": f"{k}_capacity_screen"}}, CHEAP, seed=SEED, draws=DRAWS)
        np.testing.assert_allclose(v.reforms[k].state_draws, screened.capacity.state_draws[k])
        assert any("Practical capacity screen on" in t for t in v.reforms[k].notes)
        assert "capacity_not_screened" in v.reforms[k].records
    adu = compute(ctx, {"adu": {"variant": "adu_capacity_screen"}}, CHEAP, seed=SEED, draws=DRAWS)
    steps = adu.reforms["adu"].tract_steps()
    assert steps[-1].stage == "capacity_screened"


def test_same_seed_same_screen_and_new_seed_new_draws(ctx, screened):
    again = compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS, capacity_screen=True)
    np.testing.assert_array_equal(
        again.capacity.state_draws["adu"], screened.capacity.state_draws["adu"]
    )
    other = compute(ctx, ["adu"], CHEAP, seed=SEED + 1, draws=DRAWS, capacity_screen=True)
    assert not np.allclose(other.capacity.state_draws["adu"], screened.capacity.state_draws["adu"])


def test_screen_and_variant_together_are_refused(ctx):
    with pytest.raises(ValueError, match="already screened"):
        compute(
            ctx,
            {"adu": {"variant": "adu_capacity_screen"}},
            seed=SEED,
            draws=50,
            capacity_screen=True,
        )


def test_binding_screen_names_the_largest_loss():
    flooded = with_tables(flood=1.0)
    over = {**CHEAP, FLOOD_BUILD: fixed(0.0)}
    res = compute(flooded, ["adu", "fees"], over, seed=SEED, draws=DRAWS, capacity_screen=True)
    b = res.capacity.binding["adu"]
    assert b["screen"] == "flood" and b["share_of_draws"]["flood"] == 1.0
    assert b["text"].startswith("The FEMA flood zone screen binds most")
    np.testing.assert_allclose(res.capacity.state_draws["adu"], 0.0, atol=1e-9)
    assert res.capacity.texts["adu"].startswith("Screened for practical capacity")
    counties = res.capacity.counties["adu"]
    total = sum(np.asarray(res.capacity.county_draws["adu"]).sum(axis=1))
    assert total == pytest.approx(0.0, abs=1e-6) and set(counties) == set(res.county_ids)


# ---------------------------------------------------------------- settings and interface


def test_settings_file_is_valid_and_checked():
    s = load_settings()
    assert set(s.screens) == {"sewer", "flood", "steep", "wildfire"}
    assert "adu" in s.single_families
    raw = read_yaml(__import__("visionhousing.engine.capacity", fromlist=["x"]).SETTINGS_PATH)
    with pytest.raises(LedgerError):
        parse_settings({**raw, "screens": {}})
    led = load_ledger()
    for pid in PARAMS:
        p = led[pid]
        assert p.kind == "judgement" and p.low <= p.central <= p.high


def test_api_run_option():
    client = AsgiClient(create_app())
    body = {"state": "TL", "reforms": ["adu", "fees"], "draws": 200, "seed": 3}
    plain = client.post("/api/v1/run", {**body, "explain": False})
    assert plain.status == 200 and "capacity" not in plain.json()
    on = client.post("/api/v1/run", {**body, "explain": False, "capacity_screen": True})
    assert on.status == 200
    cap = on.json()["capacity"]
    assert set(cap["reforms"]) == {"adu", "fees"}
    assert cap["reforms"]["adu"]["not_screened"] == plain.json()["reform_results"]["adu"]["added"]
    cmp = client.post("/api/v1/compare", {**body, "capacity_screen": True})
    assert cmp.status == 200 and "capacity" in cmp.json()["columns"][0]
