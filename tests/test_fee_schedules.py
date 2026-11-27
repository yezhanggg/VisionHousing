"""Documented local fee schedules: prototype prices, counted-once rules, the local variant."""

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from visionhousing.engine.adapters import ADU_FEE_IDS
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.engine.readouts import fiscal_split
from visionhousing.features import fee_schedules as fs
from visionhousing.ledger import load_ledger

DRAWS = 200


def _params(cost=150.0, share=0.07, position=0.0, n=1):
    sched = fs.load_schedule()
    p = {i: np.full(n, cost) for i in sched.cost_ids}
    p["fees.local_electrical_cost_share"] = np.full(n, share)
    p[fs.POSITION_ID] = np.full(n, position)
    return p


def _write(tmp_path: Path, edit) -> Path:
    data = yaml.safe_load(fs.SCHEDULE_PATH.read_text(encoding="utf-8"))
    edit(data)
    out = tmp_path / "fee_schedules.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out


def test_schedule_loads_and_every_documented_item_is_complete():
    sched = fs.load_schedule()
    needed = ("category", "exemptions", "payment_stage", "dollars_year", "payee", "service")
    for iid, it in sched.items.items():
        assert it["status"] in fs.STATUSES, iid
        for key in needed:
            assert key in it, (iid, key)
        if it["status"] == "documented":
            assert it["basis"] in fs.BASES and it["quote"] and it["doc"] in sched.documents
            assert sched.documents[it["doc"]]["url"].startswith("https://")
    assert set(sched.type_prototypes) == {"units_1", "units_2", "units_3_4", "units_5plus"}


def test_philadelphia_single_family_by_hand():
    sched = fs.load_schedule()
    price = fs.price_prototype(sched, "philadelphia", "single_family", _params())
    electrical = 25 * math.ceil(round(0.07 * 150 * 1600 / 1000, 9))
    expected = (
        1328  # building permit, new 1-2 family (the $25 filing fee is credited to it)
        + 189  # zoning, new 1-2 family; no use fee for single-family
        + 284 + 25 * (12 - 7)  # plumbing, 12 fixtures
        + electrical
        + 189  # mechanical
        + 3 + 4.5  # surcharges, one permit at the low reading
        + 0.01 * 1600 * 89.15  # Development Impact Tax, type VB table value
        + 365 + 245 + 360 + 375  # utility plan review, 3/4" ferrule, 5/8" meter, sewer
    )  # fmt: skip
    assert price.low[0] == pytest.approx(expected)
    assert price.per_unit(0.0)[0] == pytest.approx(expected)
    assert price.complete


def test_application_fee_is_credited_and_counted_once(tmp_path):
    sched = fs.load_schedule()
    price = fs.price_prototype(sched, "philadelphia", "duplex", _params())
    filing = next(ln for ln in price.lines if ln.item == "phl_li_filing_one_two")
    shown = next(ln for ln in price.shown if ln.item == "phl_li_filing_one_two")
    assert shown.low[0] == 25 and filing.credited and filing.low[0] == 0

    def cheap_permit(d):
        d["items"]["phl_li_building_new_one_two"]["amount"] = 10

    cheap = fs.load_schedule(_write(tmp_path, cheap_permit))
    p2 = fs.price_prototype(cheap, "philadelphia", "duplex", _params())
    line = next(ln for ln in p2.lines if ln.item == "phl_li_filing_one_two")
    assert line.low[0] == 15  # only the excess over the permit it is credited to
    assert p2.low[0] - price.low[0] == pytest.approx(10 - 1328 + 15)


def test_two_readings_give_a_range_and_position_interpolates():
    sched = fs.load_schedule()
    adu = fs.price_prototype(sched, "philadelphia", "adu", _params())
    assert adu.low[0] < adu.high[0]
    mid = adu.per_unit(0.5)[0]
    assert mid == pytest.approx((adu.low[0] + adu.high[0]) / 2)
    # the 20-unit building's separate fire service is a high reading only
    apt = fs.price_prototype(sched, "philadelphia", "apartment_20", _params())
    hi = {ln.item: ln.high[0] for ln in apt.lines}
    lo = {ln.item: ln.low[0] for ln in apt.lines}
    assert hi["phl_pwd_valve_4"] == 14950 and lo["phl_pwd_valve_4"] == 0


def test_valuation_based_fee_follows_the_ledger_cost():
    sched = fs.load_schedule()
    cheap = fs.price_prototype(sched, "philadelphia", "fourplex", _params(cost=150))
    dear = fs.price_prototype(sched, "philadelphia", "fourplex", _params(cost=275))
    e = {ln.item: ln.low[0] for ln in cheap.lines}["phl_li_electrical"]
    f = {ln.item: ln.low[0] for ln in dear.lines}["phl_li_electrical"]
    assert e == 25 * math.ceil(round(0.07 * 150 * 4000 / 1000, 9)) and f > e


def test_pittsburgh_is_not_applied_and_unknowns_stay_unknown():
    sched = fs.load_schedule()
    st = fs.city_status(sched, "pittsburgh")
    assert not st.applied and "building" in st.gaps
    price = fs.price_prototype(sched, "pittsburgh", "single_family", _params())
    assert "pgh_pli_building" in price.unknown and not price.complete
    assert price.low[0] == 610  # only the PWSA permit and tap/meter are documented
    fallback = np.array([15_000.0])
    m, status = fs.county_fee_matrix(
        sched, ["42003", "42101", "42001"], ["units_1", "units_5plus"], _params(), fallback
    )
    assert m[0, 0, 0] == 15_000 and m[2, 1, 0] == 15_000  # Allegheny, Adams: judgement
    assert m[1, 0, 0] == pytest.approx(
        fs.price_prototype(sched, "philadelphia", "single_family", _params()).low[0]
    )
    assert status["philadelphia"].applied


def test_a_not_reviewed_core_category_blocks_the_city(tmp_path):
    def unreviewed(d):
        d["cities"]["philadelphia"]["review"]["sewer_connection"] = "unknown_not_reviewed"

    sched = fs.load_schedule(_write(tmp_path, unreviewed))
    st = fs.city_status(sched, "philadelphia")
    assert not st.applied and st.gaps == ("sewer_connection",)
    m, _ = fs.county_fee_matrix(sched, ["42101"], ["units_1"], _params(), np.array([9.0]))
    assert m[0, 0, 0] == 9.0


def test_quote_checks_and_document_fetch(tmp_path):
    sched = fs.load_schedule()
    item = sched.items["phl_pwd_sewer_6"]
    assert fs.verify_text(sched, "phl_pwd_regulations_ch5", "xx 5” or 6”  connection\n$375.00 ")
    text = " ".join(
        str(it[k])
        for it in sched.items.values()
        if it.get("doc") == "phl_pwd_regulations_ch5"
        for k in ("quote", "table_quote")
        if it.get(k)
    )
    assert fs.verify_text(sched, "phl_pwd_regulations_ch5", text.upper()) == []
    assert item["quote"] in text
    assert fs.normalise("1”  Size") == fs.normalise('1" size')

    def fake_fetch(url, dest):
        dest.write_text("<html><body>nothing here</body></html>", encoding="utf-8")

    page_docs = {d for d, doc in sched.documents.items() if doc["kind"] == "html"}
    sub = fs.Schedule(
        sched.id, sched.read_on, sched.prototypes,
        {d: sched.documents[d] for d in page_docs}, sched.cities,
        {i: it for i, it in sched.items.items() if it["doc"] in page_docs},
        sched.type_prototypes, sched.core, sched.secondary, sched.cost_ids,
    )  # fmt: skip
    report = fs.check_documents(sub, tmp_path, fetch=fake_fetch)
    assert report and all(problems for problems in report.values())


def test_connection_fees_are_not_double_counted_with_adu_site_work():
    sched = fs.load_schedule()
    assert "adu.site_utility_cost" not in ADU_FEE_IDS
    assert "adu.utility_connection_fee" in ADU_FEE_IDS
    taps = [it for it in sched.items.values() if it["category"] == "water_connection"]
    assert taps and all("site" not in it["category"] for it in taps)
    assert "adu.site_utility_cost" in fs.ADU_LEDGER_RULE
    for iid in ("phl_pwd_ferrule_3_4", "phl_pwd_sewer_6"):
        assert "site cost, not a fee" in sched.items[iid]["exemptions"]


def test_benchmark_is_never_overwritten():
    led = load_ledger()
    p = led["fees.fee_total_per_unit"]
    assert (p.central, p.low, p.high, p.shape) == (50000, 50000, 50000, "fixed")
    variants = yaml.safe_load((fs.ROOT / "assumptions" / "variants.yaml").read_text())
    v = variants["variants"]["fees_documented_local"]
    assert v["options"] == {"view": "local"} and not v["overrides"]


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


def _fees(ctx, options):
    return run("TL", {"fees": options}, draws=DRAWS, context=ctx, use_cache=False).reforms["fees"]


def test_local_view_equals_state_view_where_nothing_is_documented(ctx):
    state = _fees(ctx, {"view": "state"})
    local = _fees(ctx, {"variant": "fees_documented_local"})
    np.testing.assert_allclose(local.state_draws, state.state_draws)
    assert local.readouts["fiscal_revenue_change"].p50 == pytest.approx(
        state.readouts["fiscal_revenue_change"].p50
    )
    assert any("not in this state's counties" in n for n in local.result.notes)


def test_documented_county_uses_documented_fees_and_the_identity_holds(ctx, tmp_path, monkeypatch):
    detail = _fees(ctx, {"view": "state"}).result.detail
    county = detail.county_ids[0]

    def move(d):
        d["cities"]["philadelphia"]["county"] = county

    monkeypatch.setattr(fs, "SCHEDULE_PATH", _write(tmp_path, move))
    state = _fees(ctx, {"view": "state"})
    local = _fees(ctx, {"variant": "fees_documented_local"})
    det = local.result.detail
    fee = det.per_unit["fee"]
    assert np.all(fee[0] < 10_000)  # documented Philadelphia fees, far below the judgement
    np.testing.assert_allclose(fee[1:], np.broadcast_to(fee[1:, :1, :], fee[1:].shape))
    assert local.added.p50 < state.added.p50
    rec = local.result.records
    assert "local_fee_philadelphia_units_1" in rec and "local_fee_state_average" in rec
    r = local.readouts
    # fiscal identity: change = after - before (same draws), and net = -waived + paid
    assert r["fiscal_revenue_change"].p50 == pytest.approx(
        r["fiscal_revenue_after"].p50 - r["fiscal_revenue_before"].p50, rel=0.05
    )
    assert r["fiscal_revenue_change"].p50 > state.readouts["fiscal_revenue_change"].p50
    assert r["fiscal_net_change"].p50 == pytest.approx(
        -r["fiscal_waived_on_baseline"].p50 + r["fiscal_paid_by_added"].p50, rel=0.05
    )


def test_fiscal_split_by_county_matches_the_scalar_identity():
    led = load_ledger()
    cut = np.array([[25_000.0], [2_000.0]])
    fee = cut * 2
    base = np.array([[100.0], [50.0]])
    added = np.array([[5.0], [1.0]])
    out = fiscal_split(cut, fee, base, added, led, ids=[], sources=[])
    before = 50_000 * 100 + 4_000 * 50
    after = 25_000 * 105 + 2_000 * 51
    assert out["fiscal_revenue_before"].p50 == pytest.approx(before)
    assert out["fiscal_revenue_after"].p50 == pytest.approx(after)
    assert out["fiscal_revenue_change"].p50 == pytest.approx(after - before)
    assert out["fiscal_net_loss_per_added_home"].p50 == pytest.approx((before - after) / 6)
