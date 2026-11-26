"""Project choice and a shared market (``engine.market``) on Testland.

Checks: each shared lot is counted once; the capacity limit is never exceeded;
the no-constraint case equals project choice, and without the option the
package is today's package bit for bit; every case is paired draw by draw.
Testland's 2-6 unit buildings do not pencil at the ledger's values, so the
choice tests use cheaper construction (ledger overrides) to make them compete.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine import compute
from visionhousing.engine.context import load_context
from visionhousing.engine.market import (
    MARKET_MODES,
    capacity_allocate,
    load_settings,
    parse_settings,
)
from visionhousing.ledger import LedgerError, sample
from visionhousing.scenarios import missing_middle as mm
from visionhousing.scenarios import prototypes as proto

DRAWS = 400
SEED = 5
KEYS = ["adu", "missing_middle", "tod", "parking", "fees"]
CHEAP = {"missing_middle.hard_cost_per_sqft": 120.0, "missing_middle.cap_rate": 0.045}
"""Ledger overrides that make 2-6 unit buildings pencil on some Testland lots."""


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def today(ctx):
    return compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS)


@pytest.fixture(scope="module")
def capacity(ctx):
    return compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS, market="choice+capacity")


@pytest.fixture(scope="module")
def choice(ctx):
    return compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS, market="choice")


# ---------------------------------------------------------------- today's package is unchanged


def test_without_the_option_the_package_is_unchanged(ctx, today, choice):
    assert today.market is None and "market" not in today.to_dict()
    np.testing.assert_array_equal(choice.package.state_draws, today.package.state_draws)
    np.testing.assert_array_equal(choice.package.county_draws, today.package.county_draws)
    assert choice.package.combined == today.package.combined


def test_today_case_is_the_package_draw_by_draw(today, capacity):
    m = capacity.market
    np.testing.assert_allclose(m.state_draws["today"], today.package.state_draws, atol=1e-6)
    np.testing.assert_allclose(m.county_draws["today"], today.package.county_draws, atol=1e-6)


def test_choice_changes_nothing_without_competing_reforms(ctx):
    r = compute(ctx, ["parking", "fees"], seed=SEED, draws=DRAWS, market="choice")
    np.testing.assert_allclose(r.market.state_draws["choice"], r.package.state_draws, atol=1e-9)
    assert not r.market.substitution and not r.market.lots


def test_unknown_market_is_refused(ctx):
    with pytest.raises(ValueError, match="market must be one of"):
        compute(ctx, ["adu"], draws=10, market="capacity")
    assert MARKET_MODES == ("choice", "choice+capacity")


# ---------------------------------------------------------------- each lot once


def test_each_shared_lot_is_counted_once(capacity):
    d = capacity.market.draws
    uses = ("adu", "middle", "conventional", "retain")
    for k in uses:
        assert np.all(d[f"lots.{k}"] >= -1e-9), k
    total = sum(d[f"lots.{k}"] for k in uses)
    np.testing.assert_allclose(total, d["lots.shared"], rtol=1e-9)
    assert np.all(d["lots.deferred"] <= d["lots.adu"] + d["lots.middle"] + 1e-9)


def test_substitution_moves_lots_between_reforms(capacity):
    d = capacity.market.draws
    assert np.median(d["substitution.adu_to_middle_lots"]) > 0
    # ADUs given up are exactly the change in the ADU part, draw by draw.
    np.testing.assert_allclose(
        d["parts.today.adu"] - d["parts.choice.adu"],
        d["substitution.adu_to_middle_units"],
        atol=1e-6,
    )
    assert np.all(d["parts.choice.adu"] <= d["parts.today.adu"] + 1e-9)


def test_bridge_adds_up_today_to_choice(capacity):
    m = capacity.market
    steps = [m.draws[f"bridge.{i}"] for i, r in enumerate(m.bridge) if "capacity" not in r.name]
    np.testing.assert_allclose(
        m.state_draws["today"] + sum(steps), m.state_draws["choice"], rtol=1e-9, atol=1e-6
    )


def test_reforms_outside_the_choice_keep_their_draws(capacity):
    d = capacity.market.draws
    for k in ("tod", "parking", "fees"):
        np.testing.assert_array_equal(d[f"parts.choice.{k}"], d[f"parts.today.{k}"])


def test_paired_draws_repeat_exactly(ctx, capacity):
    again = compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS, market="choice+capacity")
    for case in capacity.market.cases:
        np.testing.assert_array_equal(
            again.market.state_draws[case], capacity.market.state_draws[case]
        )


# ---------------------------------------------------------------- capacity


def test_capacity_never_exceeds_the_room():
    rng = np.random.default_rng(0)
    wanted = rng.gamma(2.0, 50.0, size=(3, 6, 40, 5))
    group_of = np.array([0, 0, 1, 1, 2, 2])
    room = rng.uniform(0, 300, size=(3, 40, 5))
    res = capacity_allocate(wanted, group_of, room)
    assert np.all(res.used <= room + 1e-9)
    total = wanted.sum(axis=-1)
    np.testing.assert_allclose(res.on_time + res.shifted + res.pushed, total, rtol=1e-9)
    np.testing.assert_allclose(res.kept.sum(axis=-1) + res.pushed, total, rtol=1e-9)
    dropped = capacity_allocate(wanted, group_of, room, carry_over=False)
    assert np.all(dropped.shifted == 0) and np.all(dropped.used <= room + 1e-9)


def test_capacity_shares_room_proportionally():
    wanted = np.zeros((2, 1, 1, 1))
    wanted[0, 0, 0, 0], wanted[1, 0, 0, 0] = 30.0, 10.0
    res = capacity_allocate(wanted, np.array([0]), np.full((1, 1, 1), 20.0))
    np.testing.assert_allclose(res.kept[:, 0, 0, 0], [15.0, 5.0])
    np.testing.assert_allclose(res.pushed[:, 0, 0], [15.0, 5.0])


def test_run_never_uses_more_than_the_room(capacity):
    m = capacity.market
    assert m.groups and all(g["room_used"].p90 <= 1 + 1e-9 for g in m.groups)
    assert np.all(m.state_draws["capacity"] <= m.state_draws["choice"] + 1e-6)
    d = m.draws
    np.testing.assert_allclose(
        d["timing.on_time"] + d["timing.shifted_later"] + d["timing.pushed_past"],
        m.state_draws["choice"],
        rtol=1e-9,
        atol=1e-6,
    )


def test_no_constraint_equals_project_choice(ctx, capacity):
    huge = {
        **CHEAP,
        "market.capacity_peak_multiplier": {
            "central": 1e9,
            "low": 1e9,
            "high": 1e9,
            "shape": "fixed",
        },
    }
    r = compute(ctx, KEYS, huge, seed=SEED, draws=DRAWS, market="choice+capacity")
    np.testing.assert_allclose(
        r.market.state_draws["capacity"], r.market.state_draws["choice"], rtol=1e-12
    )
    np.testing.assert_allclose(
        r.market.state_draws["choice"], capacity.market.state_draws["choice"]
    )
    assert "no_constraint" in capacity.market.texts


# ---------------------------------------------------------------- outputs and text


def test_outputs_are_kept_apart(capacity):
    m = capacity.market
    assert m.cases == ("today", "choice", "capacity")
    for case in m.cases:
        got = m.measures[case]
        assert {"local_gain", "statewide_net", "net_stock", "stock_losses", "fee_effects"} <= set(
            got
        )
        assert got["local_gain"].p50 >= got["statewide_net"].p50
        assert got["net_stock"].p50 <= got["statewide_net"].p50
        assert got["statewide_net"].assumption_ids and got["statewide_net"].formula
    assert set(m.timing) == {"on_time", "shifted_later", "pushed_past"}
    assert (
        "market.capacity_peak_multiplier" in m.measures["capacity"]["statewide_net"].assumption_ids
    )


def test_texts_are_written_from_the_records(capacity):
    m = capacity.market
    net = m.measures["capacity"]["statewide_net"]
    assert f"{round(net.p50):,}" in m.texts["combined"]
    lots = m.substitution["adu_to_middle_lots"]
    assert f"{round(lots.p50):,}" in m.texts["adu_to_middle"]
    d = m.to_dict()
    assert d["mode"] == "choice+capacity" and d["texts"] == dict(m.texts)
    assert capacity.to_dict()["market"]["cases"] == ["today", "choice", "capacity"]


# ---------------------------------------------------------------- the funnel hooks


def test_rivals_only_lower_the_building_share(ctx):
    led = ctx.ledger
    ids = [*mm._needed_ids(led), *proto.PROTOTYPE_IDS, proto.CONVENTIONAL_SIZE]
    v = sample(led, n=50, seed=1, ids=ids)
    col = {k: a[:, None] for k, a in v.items()}
    col["missing_middle.hard_cost_per_sqft"] = np.full((50, 1), 120.0)
    t = ctx.tracts
    alone = proto.prototype_choice(t, col, mm.PROFORMA_IDS, 50)
    again = proto.prototype_choice(t, col, mm.PROFORMA_IDS, 50, rival=None, floor=None)
    np.testing.assert_array_equal(alone.feasible, again.feasible)
    floor = proto.conventional_bid(t, col, mm.PROFORMA_IDS, 50)
    conv = proto.prototype_choice(t, col, mm.PROFORMA_IDS, 50, floor=floor)
    rival = (np.full((50, 1), 0.4), np.full((50, len(t)), 50_000.0))
    win = proto.prototype_choice(t, col, mm.PROFORMA_IDS, 50, rival=rival, floor=floor)
    assert np.all(conv.feasible <= alone.feasible + 1e-12)
    assert np.all(win.feasible <= conv.feasible + 1e-12)


def test_rivals_need_the_prototype_method_and_replace_the_overlap(ctx):
    scope = mm.scope_from_scenario("missing_middle", ctx.scenarios["missing_middle"])
    n = len(ctx.tracts)
    rivals = mm.MiddleRivals(np.zeros((5, n)), np.zeros((5, 1)), np.zeros((5, n)))
    with pytest.raises(ValueError, match="prototype method"):
        mm.missing_middle_funnel(ctx.tracts, ctx.ledger, None, scope, draws=5, rivals=rivals)
    with pytest.raises(ValueError, match="overlap"):
        mm.missing_middle_funnel(
            ctx.tracts,
            ctx.ledger,
            None,
            scope,
            draws=5,
            method="prototypes",
            rivals=rivals,
            adu_lots=np.zeros(n),
        )


def test_settings_are_checked():
    s = load_settings()
    assert s.middle_method == "prototypes" and s.allocation == "proportional"
    with pytest.raises(LedgerError, match="group_by"):
        parse_settings(
            {
                "modes": {m: {"id": m, "title": m, "question": m} for m in MARKET_MODES},
                "choice": {"id": "c", "middle_method": "prototypes", "conventional_rebuild": True},
                "capacity": {
                    "id": "k",
                    "group_by": "zip",
                    "ungrouped": "x",
                    "peak_window_years": 15,
                    "allocation": "proportional",
                    "carry_over": True,
                },
            }
        )


# ---------------------------------------------------------------- API


def test_api_run_and_compare_carry_the_market(tmp_path):
    client = AsgiClient(create_app(packs_dir=tmp_path))
    body = {
        "state": "TL",
        "reforms": ["adu", "fees"],
        "draws": 200,
        "seed": 2,
        "market": "choice+capacity",
        "explain": False,
    }
    r = client.post("/api/v1/run", body)
    assert r.status == 200
    assert r.json()["market"]["mode"] == "choice+capacity"
    plain = client.post("/api/v1/run", {**body, "market": None}).json()
    assert "market" not in plain
    cmp = client.post(
        "/api/v1/compare",
        {
            "state": "TL",
            "draws": 200,
            "scenarios": [
                {"label": "today", "reforms": ["adu", "fees"]},
                {"label": "choice", "reforms": ["adu", "fees"], "market": "choice"},
            ],
        },
    ).json()["columns"]
    assert "market" not in cmp[0] and cmp[1]["market"]["cases"] == ["today", "choice"]
    bad = client.post("/api/v1/run", {**body, "market": "capacity"})
    assert bad.status in (400, 422)


def test_full_market_share_is_worked_out(capacity):
    m = capacity.market
    text = m.texts["full_market"]
    share = int(text.split("In ", 1)[1].split("%", 1)[0])
    assert 0 <= share <= 100
    assert all(0 <= g["binding_share"] <= 1 for g in m.groups)
    assert all(g["kept_share"].p90 <= 1 + 1e-9 for g in m.groups)


def test_comparisons_market_part_is_opt_in(tmp_path):
    from visionhousing.engine.comparisons import MARKET_PART, PARTS, comparisons

    assert MARKET_PART not in PARTS
    out = comparisons("TL", MARKET_PART, seed=1, draws=100)
    assert out["market"]["cases"] == ["today", "choice", "capacity"]
    client = AsgiClient(create_app(packs_dir=tmp_path))
    r = client.get("/api/v1/states/TL/comparisons?part=market&draws=100&seed=1")
    assert r.status == 200
    body = r.json()["market"]
    assert body["measures"]["capacity"]["statewide_net"]["likely"]
    assert body["texts"]["no_constraint"]


def test_interaction_lift_on_overlap_lots_is_its_own_step(capacity):
    """The fee and parking lift on the ADU lots is not folded into the prototype step (R5 F6)."""
    m = capacity.market

    def at(words):
        return next(i for i, r in enumerate(m.bridge) if words in r.name)

    lift = at("fee and parking lift on the lots ADUs use")
    proto_step = at("2-6 unit building priced as the best prototype per lot")
    assert at("today's overlap rule undone") < lift < proto_step
    assert np.median(m.draws[f"bridge.{lift}"]) >= 0  # cheaper fees and parking add homes
    assert "same fee and parking changes" in m.bridge[proto_step].formula


def test_no_lift_step_without_fee_or_parking(ctx):
    r = compute(ctx, ["adu", "missing_middle"], CHEAP, seed=SEED, draws=50, market="choice")
    names = [b.name for b in r.market.bridge]
    assert not any("fee and parking lift" in n for n in names)
    assert any("overlap rule undone" in n for n in names)
    steps = [r.market.draws[f"bridge.{i}"] for i in range(len(names))]
    np.testing.assert_allclose(
        r.market.state_draws["today"] + sum(steps), r.market.state_draws["choice"], atol=1e-6
    )


def test_capacity_room_is_taken_by_local_permits(ctx, monkeypatch):
    """The package takes room with its local permits, not its net homes (review R5 F8).

    The cap counts permits in the area, so each reform's homes / kept share use it.
    """
    from visionhousing.engine import market as mk

    seen = {}
    real = mk.capacity_allocate

    def spy(wanted, *a, **kw):
        seen["wanted"] = np.asarray(wanted)
        return real(wanted, *a, **kw)

    monkeypatch.setattr(mk, "capacity_allocate", spy)
    r = compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS, market="choice+capacity")
    m = r.market
    local = m.measures["choice"]["local_gain"]
    assert local.p50 > m.measures["choice"]["statewide_net"].p50  # some displacement
    wanted = seen["wanted"].sum(axis=(0, 1, 3))
    np.testing.assert_allclose(np.percentile(wanted, 50), local.p50, rtol=1e-9)
    # Back in net homes: kept + pushed past = choice, in every draw.
    np.testing.assert_allclose(
        m.state_draws["capacity"] + m.draws["timing.pushed_past"],
        m.state_draws["choice"],
        rtol=1e-9,
        atol=1e-6,
    )
