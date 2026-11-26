"""What this law changes (api/law_changes.py): four answers, denominators, read next (Testland).

Hand-made municipal rules in two Testland counties:

- Harbor County (99001): Harbor city (by right, detached, no owner rule: already allowed),
  Cove township (special exception: condition changed), Gull borough (prohibited by a
  verified exclusivity clause: newly allowed), Reef township (draft reading: unresolved),
  Dune township (verified, but its rule takes effect after the as-of date: unresolved),
  Sand township (not addressed, no clause on unlisted uses read: unresolved), and Inlet city,
  outside the planning code (outside a planning-code bill).
- Maple County (99003): Elm borough (no zoning at all: outside a bill on zoned places), and
  Oak township, not read.

Checks: per county and statewide, the four shares add up to 100% of the denominator; unknown
stays unknown (draft, not yet in force, not addressed without a clause, unread); a bill's
outside places leave the denominator; missing middle's legal increment; the capacity range;
every shown number carries its formula and ids; the read-next bounds find a reading that
switches the preferred option; the interface answers on Testland.
"""

from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api import law_changes as lc
from visionhousing.engine.context import load_context
from visionhousing.law import hooks as law_hooks

AS_OF = "2026-11-24"
HOOKS = {**law_hooks.load_hooks(), "engine_wired": True}

MUNIS = pd.DataFrame(
    [
        {"county": "99001", "cousub": c, "name": n, "zoning_regime": z, "population": p}
        for c, n, z, p in [
            ("00100", "Harbor city", "own", "4000"),
            ("00200", "Cove township", "own", "1000"),
            ("00300", "Gull borough", "own", "1000"),
            ("00400", "Reef township", "own", "1000"),
            ("00500", "Dune township", "own", "1000"),
            ("00600", "Sand township", "own", "1000"),
            ("00700", "Inlet city", "own", "1000"),
        ]
    ]
    + [
        {
            "county": "99003",
            "cousub": "00800",
            "name": "Elm borough",
            "zoning_regime": "none",
            "population": "2000",
        },
        {
            "county": "99003",
            "cousub": "00900",
            "name": "Oak township",
            "zoning_regime": "own",
            "population": "8000",
        },
    ]
).astype(str)


def _rule(geoid, adu, *, status="verified", method="hand", detached="", owner="", unlisted="",
          max_units="", park="", effective="", reviewed="2026-11-23", lots="100"):  # fmt: skip
    return {
        "geoid": geoid,
        "name": f"Place {geoid[-5:]}",
        "adu_status": adu,
        "adu_detached": detached,
        "adu_owner_occupancy": owner,
        "occupancy_restriction": "",
        "unlisted_uses": unlisted,
        "unlisted_uses_cite": "§ 9" if unlisted else "",
        "max_units_by_right_sf_district": max_units,
        "parking_min_per_unit_sf": park,
        "parking_min_per_unit_mf": park,
        "transport_impact_fee_ordinance": "",
        "section_cite": "§ 1",
        "doc_id": "doc",
        "evidence_quote": "quote",
        "method": method,
        "status": status,
        "reviewed_on": reviewed,
        "adopted_on": "",
        "effective_date": effective,
        "in_force_by": "",
        "sf_lots": lots,
    }


RULES = pd.DataFrame(
    [
        _rule(
            "9900100100", "by_right", detached="1", owner="0", max_units="1", park="0", lots="400"
        ),
        _rule("9900100200", "special_exception", max_units="3", park="2"),
        _rule("9900100300", "not_addressed", unlisted="prohibited", max_units="6"),
        _rule("9900100400", "by_right", status="draft", detached="1", owner="0"),
        _rule("9900100500", "by_right", detached="1", owner="0", effective="2027-06-01"),
        _rule("9900100600", "not_addressed"),
        _rule("9900100700", "by_right", detached="1", owner="0"),
        _rule("9900300800", "unknown", status="draft", method="unread", lots="200"),
        _rule("9900300900", "unknown", status="draft", method="unread", lots="800"),
    ]
).astype(str)

PROVISIONS = pd.DataFrame(
    [
        {
            "provision_id": "pa_sb1346",
            "citation": "SB 1346, new MPC § 603.2",
            "status": "pending",
            "scope": "mpc_zoned",
            "effective_date": "",
            "last_action": "laid on the table",
            "last_action_date": "2026-11-17",
            "url": "https://example.org/sb",
            "source_id": "sb",
        },
        {
            "provision_id": "mpc_adu_silent",
            "citation": "MPC Article VI",
            "status": "enacted",
            "scope": "mpc",
            "effective_date": "1969-01-01",
            "summary_own_words": "No ADU section.",
        },
    ]
).astype(str)

TABLES = {
    "muni_zoning_rules": RULES,
    "municipal_planning": MUNIS,
    "state_law_provisions": PROVISIONS,
}
SETTINGS = lc.load_settings()


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(autouse=True)
def _outside(monkeypatch):
    """Inlet city stands in for a city outside the planning code (Testland's own list)."""
    from visionhousing.law import state as law_state

    monkeypatch.setattr(
        law_state, "load_provisions", lambda: {"state": "TL", "outside_mpc": ["9900100700"]}
    )


def payload(ctx, key, settings=None):
    return lc.build_payload(
        ctx, key, TABLES, None, settings=settings or SETTINGS, hooks=HOOKS, as_of=AS_OF
    )


def county(p, fips):
    return next(c for c in p["counties"] if c["fips"] == fips)


def muni(c, name):
    return next(m for m in c["municipalities"] if m["name"] == name)


# ------------------------------------------------------------------ four answers


@pytest.mark.parametrize("key", ["adu", "pa_sb1346", "missing_middle", "parking", "fees", "tod"])
def test_classes_sum_to_one_per_denominator(ctx, key):
    p = payload(ctx, key)
    for c in [
        *p["counties"],
        {"classes": p["statewide"]["classes"], "denominator": p["statewide"]["denominator"]},
    ]:
        den = c["denominator"]["value"]
        counts = sum(c["classes"][k]["count"]["value"] for k in lc.CLASSES)
        assert counts == pytest.approx(den)
        if den > 0:
            assert sum(c["classes"][k]["share"]["value"] for k in lc.CLASSES) == pytest.approx(1.0)
        else:
            assert all(c["classes"][k]["share"] is None for k in lc.CLASSES)


def test_adu_answers_follow_the_law_layer(ctx):
    h = county(payload(ctx, "adu"), "99001")
    assert muni(h, "Harbor city")["answer"] == "already_allowed"
    assert muni(h, "Cove township")["answer"] == "changed_condition"
    assert muni(h, "Gull borough")["answer"] == "newly_allowed"
    # Lots: 400 already, 100 changed, 100 new, 300 unresolved (Reef, Dune, Sand), 100 Inlet
    # (a benchmark reaches it: by right, so already allowed).
    shares = {k: h["classes"][k]["count"]["value"] for k in lc.CLASSES}
    assert shares == {
        "already_allowed": 500,
        "changed_condition": 100,
        "newly_allowed": 100,
        "unresolved": 300,
    }
    assert h["answer"] == "already_allowed"


def test_unknown_stays_unknown(ctx):
    h = county(payload(ctx, "adu"), "99001")
    for name, why in [
        ("Reef township", "draft"),
        ("Dune township", "not in force"),
        ("Sand township", "does not answer"),
    ]:
        m = muni(h, name)
        assert m["answer"] == "unresolved", name
        assert why in m["why"], m["why"]
    # A county nobody has read is unresolved, never "no" (newly allowed).
    maple = county(payload(ctx, "adu"), "99003")
    assert maple["answer"] == "unresolved"
    assert maple["classes"]["unresolved"]["share"]["value"] == 1.0
    assert maple["observed"]["kind"] == "fallback"
    assert "adu.already_legal_share" in maple["observed"]["text"]
    for key in ("parking", "missing_middle", "fees", "tod"):
        assert county(payload(ctx, key), "99003")["classes"]["newly_allowed"]["count"]["value"] == 0


def test_bill_outside_places_leave_the_denominator(ctx):
    p = payload(ctx, "pa_sb1346")
    h, m = county(p, "99001"), county(p, "99003")
    assert muni(h, "Inlet city")["answer"] == "outside"
    assert h["outside"]["value"] == 100 and h["denominator"]["value"] == 900
    # Elm borough has no zoning: outside a bill on zoned places; unread Oak stays inside.
    assert m["outside"]["value"] == 200 and m["denominator"]["value"] == 800
    assert p["provision"]["kind"] == "bill"
    assert "No effective date" in p["provision"]["effective_text"]
    assert p["statewide"]["outside"]["value"] == 300


def test_benchmark_has_no_section_or_date(ctx):
    prov = payload(ctx, "adu")["provision"]
    assert prov["kind"] == "benchmark" and prov["effective_date"] is None
    assert prov["current_law"][0]["citation"] == "MPC Article VI"


def test_missing_middle_counts_only_the_increment(ctx):
    h = county(payload(ctx, "missing_middle"), "99001")
    assert muni(h, "Harbor city")["answer"] == "newly_allowed"  # 1 home today
    assert muni(h, "Cove township")["answer"] == "changed_condition"  # 3 today: 4-6 new
    assert muni(h, "Gull borough")["answer"] == "already_allowed"  # 6 today
    top = float(load_context("TL").ledger["missing_middle.max_units"].central)
    # Low: Harbor 400 lots x (6 - 1) + Cove 100 x (6 - 3); high adds every unresolved lot at
    # the full gain from one home (6 - 1).
    low = 400 * (top - 1) + 100 * (top - 3)
    unresolved = h["classes"]["unresolved"]["count"]["value"]
    assert unresolved == 400  # Reef, Dune, Sand, and Inlet (its unit count not read)
    assert h["capacity"]["low"] == pytest.approx(low)
    assert h["capacity"]["high"] == pytest.approx(low + unresolved * (top - 1))


def test_capacity_and_units_are_server_values(ctx):
    from visionhousing.engine.core import run

    res = run("TL", ["adu"], context=ctx, draws=60)
    p = lc.build_payload(ctx, "adu", TABLES, res, settings=SETTINGS, hooks=HOOKS, as_of=AS_OF)
    for c in p["counties"]:
        assert c["capacity"]["low"] <= c["capacity"]["high"]
        assert c["units"]["shown"]["p50"] == lc.show(
            res.reforms["adu"].counties[c["fips"]].p50, "units", lc.TABLE_SIG
        )
    assert p["statewide"]["units"]["p50"] == pytest.approx(res.reforms["adu"].added.p50)
    unres = p["statewide"]["unresolved_result_share"]["value"]
    assert 0.0 <= unres <= 1.0
    assert payload(ctx, "parking")["statewide"]["capacity"] is None


def _numbers(node):
    if isinstance(node, dict):
        if "shown" in node and ("value" in node or "low" in node or "p50" in node):
            yield node
        for v in node.values():
            yield from _numbers(v)
    elif isinstance(node, list):
        for v in node:
            yield from _numbers(v)


@pytest.mark.parametrize("key", ["adu", "pa_sb1346", "missing_middle", "parking"])
def test_every_shown_number_carries_formula_and_ids(ctx, key):
    nums = list(_numbers(payload(ctx, key)))
    assert nums
    for n in nums:
        assert n["formula"], n
        assert n["source_ids"], n


def test_tie_goes_to_unresolved():
    assert (
        lc._answer({"already_allowed": 0.5, "unresolved": 0.5}, SETTINGS["answer_tie_order"])
        == "unresolved"
    )


# ------------------------------------------------------------------ read next


def _fake_run(ctx, key):
    """ADU adds 100 homes, plus 60 for each county using read local law; the fee cut 190."""
    law = ctx.local_law
    used = len(law.adu_coefficients()) if law is not None else 0
    value = 100.0 + 60.0 * used if key == "adu" else 190.0
    rec = SimpleNamespace(name=f"{key} added", unit="units", p10=value, p50=value, p90=value,
                          formula="fake", source_ids=("s",), assumption_ids=("a",))  # fmt: skip
    counties = {"99001": rec, "99003": rec}
    state = np.full(5, value)
    return SimpleNamespace(
        reforms={key: SimpleNamespace(added=rec, counties=counties, state_draws=state)}
    )


def test_read_next_change_is_the_percentiles_of_the_per_draw_change(ctx):
    """The change is P50 of (reading - today) in each draw, not P50(reading) - P50(today)."""
    today = np.array([0.0, 10.0, 20.0, 30.0, 100.0])
    read = np.array([50.0, 10.0, 20.0, 30.0, 0.0])  # same P50 (20); the change differs by draw

    def run_fn(c, key):
        law = c.local_law
        changed = law is not None and len(law.adu_coefficients()) > 1
        draws = read if (changed and key == "adu") else today
        p10, p50, p90 = np.percentile(draws, [10, 50, 90])
        rec = SimpleNamespace(name="x", unit="units", p10=p10, p50=p50, p90=p90,
                              formula="fake", source_ids=("s",), assumption_ids=("a",))  # fmt: skip
        return SimpleNamespace(
            reforms={key: SimpleNamespace(added=rec, counties={}, state_draws=draws)}
        )

    out = lc.read_next(
        replace_law(ctx, RULES), "adu", TABLES, run_fn, settings=SETTINGS, hooks=HOOKS,
        as_of=AS_OF, top=5,
    )  # fmt: skip
    oak = {u["unit_id"]: u for u in out["units"]}["9900300900"]
    change = oak["bounds"]["permissive"]["change"]
    diff = read - today  # 50, 0, 0, 0, -100
    assert change["p50"] == pytest.approx(np.percentile(diff, 50)) == pytest.approx(0.0)
    assert change["p10"] == pytest.approx(np.percentile(diff, 10))
    assert change["p90"] == pytest.approx(np.percentile(diff, 90))
    assert change["value"] == change["p50"] and "in the same draw" in change["formula"]
    assert oak["impact"]["low"] <= oak["impact"]["high"]


def test_unresolved_result_share_is_taken_per_draw(ctx):
    """Unresolved share of the added homes: sum_c(draws_c x u_c) / sum_c(draws_c) per draw."""
    from visionhousing.engine.core import run

    res = run("TL", ["adu"], context=ctx, draws=60)
    out = res.reforms["adu"]
    p = lc.build_payload(ctx, "adu", TABLES, res, settings=SETTINGS, hooks=HOOKS, as_of=AS_OF)
    u = {
        c["fips"]: (c["classes"]["unresolved"]["share"] or {}).get("value", 0.0)
        for c in p["counties"]
    }
    cd = np.asarray(out.county_draws, dtype=float)
    num = sum(cd[i] * u.get(str(c), 0.0) for i, c in enumerate(out.counties))
    den = cd.sum(axis=0)
    ok = den > 0
    ratio = num[ok] / den[ok]
    got = p["statewide"]["unresolved_result_share"]
    assert got["p50"] == pytest.approx(np.percentile(ratio, 50))
    assert got["p10"] == pytest.approx(np.percentile(ratio, 10))
    assert got["p90"] == pytest.approx(np.percentile(ratio, 90))
    assert got["p10"] <= got["value"] <= got["p90"]


def test_read_next_bounds_and_switch(ctx):
    base = replace_law(ctx, RULES)
    out = lc.read_next(
        base, "adu", TABLES, _fake_run, settings=SETTINGS, hooks=HOOKS, as_of=AS_OF, top=5
    )
    assert out["options"] == ["adu", "fees"]
    # Today Harbor County uses its readings (70% of residents): 100 + 60 = 160 < 190.
    assert out["preferred_now"] == "fees"
    units = {u["unit_id"]: u for u in out["units"]}
    # Oak township (8,000 of Maple's 10,000 residents) puts Maple over the 50% bar when read.
    oak = units["9900300900"]
    assert oak["bounds"]["permissive"]["engine_uses_county"] is True
    assert oak["bounds"]["permissive"]["county_coverage"]["value"] == pytest.approx(0.8)
    assert oak["switch"] is True and "switch" in oak["switch_text"]
    assert oak["impact"]["low"] <= oak["impact"]["high"]
    assert oak["effort"]["text"].startswith("Effort")


def replace_law(ctx, rules):
    from dataclasses import replace

    return replace(ctx, local_law=law_hooks.local_law_for_engine(rules, MUNIS, HOOKS, as_of=AS_OF))


def test_read_next_without_law_tables(ctx):
    out = lc.read_next(ctx, "adu", {}, _fake_run, settings=SETTINGS, hooks=HOOKS, as_of=AS_OF)
    assert out["units"] == [] and "no municipal law table" in out["note"]


# ------------------------------------------------------------------ interface and static


def test_interface_on_testland(tmp_path, monkeypatch):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
    client = AsgiClient(create_app(packs_dir=tmp_path))
    r = client.get("/api/v1/states/TL/law-changes?r=adu")
    assert r.status == 200
    body = r.json()["changes"]
    assert body["reform"] == "adu" and len(body["counties"]) == 4
    assert {c["answer"] for c in body["counties"]} == {"unresolved"}
    assert body["readings"]["note"]
    assert body["statewide"]["units"]["shown"]["p50"]
    assert client.get("/api/v1/states/TL/law-changes?r=pa_hb2186").status == 400
    assert client.get("/api/v1/states/TL/law-changes?r=nope").status == 400
    rn = client.get("/api/v1/states/TL/law-changes/read-next?r=adu")
    assert rn.status == 200 and rn.json()["read_next"]["units"] == []


def test_static_files_and_size(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
    from visionhousing.engine.core import run

    tables = lc.read_tables(tmp_path)  # Testland: no law tables
    res = {"adu": run("TL", ["adu"], context=ctx, draws=60)}
    sizes = lc.write_static(ctx, tmp_path / "data", res, tables)
    path = tmp_path / "data" / "TL" / "law_changes" / "adu.json"
    assert sizes == {"adu": path.stat().st_size}
    body = json.loads(path.read_text())
    assert body["changes"]["reform"] == "adu" and body["read_next"] is None
    est = lc.size_estimate(ctx, tables, res)
    assert est["adu"]["raw"] == path.stat().st_size
    assert est["adu"]["gzip"] == len(gzip.compress(path.read_bytes(), 9))
    assert np.isfinite(est["total_gzip"])
