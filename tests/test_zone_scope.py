"""Zoning-district ADU variants and the law-changes district view (engine/zone_scope.py)."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from visionhousing.api import law_changes as lc
from visionhousing.engine import zone_scope as zs
from visionhousing.engine.core import run
from visionhousing.ledger import apply_overrides, load_ledger
from visionhousing.ledger.scenarios import load_scenarios, scenario_ledger
from visionhousing.scenarios.adu import (
    adu_funnel,
    load_fit_table,
    load_legal_share,
    scope_from_scenario,
)

ROOT = Path(__file__).resolve().parents[1]
S = "adu.already_legal_share"
C = "adu.conditions_exposure_share"


def _tracts(detached=(100.0, 100.0, 100.0, 100.0), attached=(0.0, 0.0, 0.0, 0.0)):
    return pd.DataFrame(
        {
            "geoid": ["42001000100", "42001000200", "42001000300", "42001000400"],
            "county": ["42001"] * 4,
            "units_1_detached": list(detached),
            "units_1_attached": list(attached),
        }
    )


def _zone(j, cls, state, lots, why="w"):
    return {"level": "zone", "jurisdiction": j, "county": j[:5], "tract": "", "period": "window",
            "benchmark": "adu", "opportunity_class": cls, "review_state": state, "sf_lots": lots,
            "housing_units": lots * 2, "rule_ids": f"r_{cls}", "why": why}  # fmt: skip


def _tract(j, tract, cls, lots):
    return {"level": "tract", "jurisdiction": j, "county": j[:5], "tract": tract,
            "period": "window", "benchmark": "adu", "opportunity_class": cls,
            "review_state": "", "sf_lots": lots, "housing_units": 0.0, "rule_ids": "",
            "why": ""}  # fmt: skip


def _table():
    """A two-jurisdiction table over four tracts.

    Tract 1: known by right (verified). Tract 2: guidance conditions relaxed.
    Tract 3: 40 of its 100 lots in the sample, unresolved. Tract 4: outside.
    """
    a, b = "4200100001", "4200100002"
    rows = [
        _zone(a, "known_by_right", "verified_code", 100.0),
        _zone(b, "conditions_relaxed", "guidance", 100.0),
        _zone(b, "unresolved", "unread", 40.0),
        _tract(a, "42001000100", "known_by_right", 100.0),
        _tract(b, "42001000200", "conditions_relaxed", 100.0),
        _tract(b, "42001000300", "unresolved", 40.0),
    ]
    return zs.typed_table(pd.DataFrame(rows))


def _col(s=0.3, c=0.7, n=5):
    return {S: np.full((n, 1), s), C: np.full((n, 1), c)}


def _rule(option, table=None, tracts=None, **kw):
    state = SimpleNamespace(tracts=_tracts() if tracts is None else tracts, derived={})
    return zs.district_rule(state, option=option, table=_table() if table is None else table, **kw)


# ---------------------------------------------------------------- classes and the blend


def test_classes_read_as_the_settings_say():
    r = _rule("zoning_districts_guidance")
    base = np.full((5, 4), 0.9)
    got = r.share(_col(), base=base)
    assert got[0, 0] == pytest.approx(1.0)  # known by right: legal
    assert got[0, 1] == pytest.approx(1 - 0.7)  # conditions relaxed: 1 - c
    assert got[0, 2] == pytest.approx(0.6 * 0.9 + 0.4 * 0.3)  # 40% unresolved at s, rest base
    assert got[0, 3] == pytest.approx(0.9)  # outside the sample: the benchmark's share


def test_unresolved_uses_the_statewide_range_not_the_base():
    r = _rule("zoning_districts")
    s = np.linspace(0.1, 0.5, 5)[:, None]
    col = {S: s, C: np.full((5, 1), 0.7)}
    base = np.full((5, 4), 0.95)  # e.g. a county hook: never used for unresolved lots
    got = r.share(col, base=base)
    # tract 2 is all unresolved in the verified view (its guidance row does not count)
    assert np.allclose(got[:, 1], s[:, 0])
    assert np.allclose(got[:, 2], 0.6 * 0.95 + 0.4 * s[:, 0])


def test_guidance_is_excluded_from_the_verified_variant():
    v = _rule("zoning_districts")
    g = _rule("zoning_districts_guidance")
    assert v.classes.lots_by_class["conditions_relaxed"] == 0.0
    assert v.classes.moved_lots == pytest.approx(100.0)
    assert g.classes.lots_by_class["conditions_relaxed"] == pytest.approx(100.0)
    assert g.classes.moved_lots == 0.0
    col = _col(s=0.3, c=0.7)
    assert v.share(col)[0, 1] == pytest.approx(0.3)
    col = _col(s=0.3, c=0.2)  # c chosen so 1 - c differs from s
    assert v.share(col)[0, 1] == pytest.approx(0.3)
    assert g.share(col)[0, 1] == pytest.approx(0.8)
    assert any("does not count" in n for n in v.notes)


def test_newly_eligible_and_discretionary_are_fully_exposed():
    j = "4200100001"
    rows = [
        _zone(j, "newly_eligible", "verified_code", 50.0),
        _zone(j, "discretionary", "verified_code", 50.0),
        _tract(j, "42001000100", "newly_eligible", 100.0),
        _tract(j, "42001000200", "discretionary", 100.0),
    ]
    r = _rule("zoning_districts", table=zs.typed_table(pd.DataFrame(rows)))
    got = r.share(_col(), base=np.full((5, 4), 0.5))
    assert np.allclose(got[:, :2], 0.0)
    assert np.allclose(got[:, 2:], 0.5)


def test_attached_lots_keep_the_benchmark_share():
    tracts = _tracts(detached=(100, 100, 100, 100), attached=(300, 0, 0, 0))
    r = _rule("zoning_districts", tracts=tracts, building_types=("sf_detached", "sf_attached"))
    got = r.share(_col(), base=np.full((5, 4), 0.2))
    # tract 1: a quarter of its reached lots are detached and known by right
    assert got[0, 0] == pytest.approx(0.25 * 1.0 + 0.75 * 0.2)
    assert zs.detached_share(tracts, ("sf_detached",))[0] == 1.0


def test_no_table_gives_the_base_exactly():
    state = SimpleNamespace(tracts=_tracts(), derived={})
    r = zs.district_rule(state, option="zoning_districts")
    base = np.random.default_rng(1).uniform(size=(5, 4))
    assert np.array_equal(r.share(_col(), base=base), base)
    assert "no rows" in r.notes[0]


def test_unknown_option_is_refused():
    with pytest.raises(ValueError, match="legal must be one of"):
        _rule("zoning_everything")


def test_maps_agree_with_the_law_layer():
    from visionhousing.law import rule_weights, zone_rules

    assert zs.U13_ANSWER == rule_weights.U13_ANSWER
    assert tuple(zs.CLASSES) == tuple(zone_rules.CLASSES)


def test_scenario_module_does_not_import_the_law_layer():
    tree = ast.parse((ROOT / "src/visionhousing/scenarios/adu.py").read_text(encoding="utf-8"))
    names = [
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in (n.names if isinstance(n, ast.Import) else [n])
    ]
    assert not [m for m in names if m and m.startswith("visionhousing.law")]


def test_source_ids_are_in_the_registry():
    text = (ROOT / "src/visionhousing/data/sources.yaml").read_text(encoding="utf-8")
    for sid in zs.load_settings()["source_ids"]:
        assert f"- id: {sid}\n" in text, sid


# ---------------------------------------------------------------- the funnel and the engine


def test_funnel_blends_and_adds_sample_records():
    ledger = load_ledger()
    scenario = load_scenarios(ledger)["adu"]
    tracts = pd.read_csv(ROOT / "tests/fixtures/testland/tracts.csv",
                         dtype={"geoid": str, "county": str})  # fmt: skip
    scope = scope_from_scenario("adu", scenario, load_legal_share())
    led = scenario_ledger(ledger, scenario)
    g = tracts["geoid"].iloc[0]
    j = "9900100001"
    lots = float(tracts["units_1_detached"].iloc[0])
    table = zs.typed_table(pd.DataFrame([
        _zone(j, "known_by_right", "verified_code", lots),
        _tract(j, g, "known_by_right", lots),
    ]))  # fmt: skip
    state = SimpleNamespace(tracts=tracts, derived={})
    rule = zs.district_rule(state, option="zoning_districts", table=table,
                            building_types=scope.building_types, ledger=led)  # fmt: skip
    a = adu_funnel(tracts, led, load_fit_table(), scope, draws=200, seed=4)
    b = adu_funnel(tracts, led, load_fit_table(), scope, draws=200, seed=4, legal_rule=rule)
    # only the first tract changes, and it loses its detached lots' exposure
    assert np.allclose(a.stages["eligible"][:, 1:], b.stages["eligible"][:, 1:])
    assert (b.stages["eligible"][:, 0] < a.stages["eligible"][:, 0]).all()
    assert {"already_legal_share_zoning_districts", "zoning_districts_sample_lots",
            "zoning_districts_sample_legal_share"} <= set(b.records)  # fmt: skip
    assert b.records["zoning_districts_sample_legal_share"].p50 == pytest.approx(1.0)
    assert S in b.records["net"].assumption_ids


def test_variant_equals_the_benchmark_without_the_tables():
    bench = run("TL", {"adu": {}}, seed=2, draws=200, use_cache=False)
    for name in ("adu_zoning_districts", "adu_zoning_districts_guidance"):
        var = run("TL", {"adu": {"variant": name}}, seed=2, draws=200, use_cache=False)
        assert np.array_equal(bench.reforms["adu"].state_draws, var.reforms["adu"].state_draws)
        assert any("no rows" in n for n in var.reforms["adu"].result.notes)


def test_both_variants_are_alternatives_rows_for_pennsylvania():
    import yaml

    from visionhousing.engine.variants import load_variants

    v = load_variants()
    alt = yaml.safe_load((ROOT / "assumptions/alternatives.yaml").read_text(encoding="utf-8"))
    rows = {r.get("variant"): r for r in alt["reforms"]["adu"]["rows"] if r.get("variant")}
    for name, opt in (("adu_zoning_districts", "zoning_districts"),
                      ("adu_zoning_districts_guidance", "zoning_districts_guidance")):  # fmt: skip
        assert v[name].reform == "adu" and v[name].options == {"legal": opt}
        assert rows[name]["states"] == ["PA"]


# ---------------------------------------------------------------- law-changes district view


def _classified():
    return pd.DataFrame({
        "geoid": ["4200100001", "4200100002", "4200100003"],
        "county": ["42001"] * 3, "name": ["A", "B", "C"],
        "answer": ["already_allowed", "unresolved", "newly_allowed"],
        "weight": [100.0, 140.0, 60.0], "cap_low": [0.0, 0.0, 1.0], "cap_high": [0.0, 1.0, 1.0],
        "why": ["", "", ""],
    })  # fmt: skip


def test_district_view_replaces_only_sample_answers():
    den = {"unit": "lots", "label": "single-family lots", "source_ids": ["acs5_summary_file"]}
    tables = {lc.DISTRICT_TABLE: _table().astype(str)}
    kw = {"rule": "adu_category", "proposed": 1.0, "weight_col": "sf_lots", "den": den}
    out, block = lc.district_view(_classified(), tables, view="districts_guidance", **kw)
    assert out["weight"].sum() == pytest.approx(300.0)  # denominator unchanged
    b = out[out["geoid"] == "4200100002"].set_index("answer")["weight"]
    assert b["changed_condition"] == pytest.approx(140.0 * 100 / 140)
    assert b["unresolved"] == pytest.approx(140.0 * 40 / 140)
    assert out[out["geoid"] == "4200100003"]["answer"].tolist() == ["newly_allowed"]
    assert [m["geoid"] for m in block["municipalities"]] == ["4200100001", "4200100002"]
    out_v, _ = lc.district_view(_classified(), tables, view="districts", **kw)
    bv = out_v[out_v["geoid"] == "4200100002"]
    assert bv["answer"].tolist() == ["unresolved"]  # guidance does not count
    assert bv["cap_high"].tolist() == [1.0]


def test_district_view_without_the_table_keeps_every_answer():
    den = {"unit": "lots", "label": "lots", "source_ids": []}
    out, block = lc.district_view(_classified(), {}, rule="adu_category", proposed=1.0,
                                  view="districts", weight_col="sf_lots", den=den)  # fmt: skip
    assert out.equals(_classified())
    assert block["note"]


@pytest.mark.realdata
def test_pa_district_view_and_default_view():
    """On the built Pennsylvania pack (skipped without the zoning-district table)."""
    from visionhousing.engine.context import load_context, pack_dir

    folder = pack_dir("PA")
    if not (folder / "rule_area_weights.csv.gz").is_file():
        pytest.skip("no built PA pack with the zoning-district tables")
    ctx = load_context("PA")
    tables = lc.read_tables(folder)
    base = lc.build_payload(ctx, "adu", tables, None)
    assert "view" not in base
    dist = lc.build_payload(ctx, "adu", tables, None, view="districts")
    guid = lc.build_payload(ctx, "adu", tables, None, view="districts_guidance")

    def sh(p, k):
        return p["statewide"]["classes"][k]["share"]["value"]

    for k in lc.CLASSES:  # verified district answers agree with the verified municipal rows
        assert sh(dist, k) == pytest.approx(sh(base, k), abs=1e-6)
    assert sh(guid, "changed_condition") > sh(base, "changed_condition")
    assert sh(guid, "unresolved") < sh(base, "unresolved")
    with pytest.raises(ValueError):
        lc.build_payload(ctx, "adu", tables, None, view="everything")


def test_ledger_override_still_reaches_the_rule():
    # a user override of the statewide range moves the unresolved lots too
    led = apply_overrides(load_ledger(), {S: {"central": 0.5, "low": 0.5, "high": 0.5,
                                              "shape": "fixed"}})  # fmt: skip
    assert led[S].central == 0.5
    r = _rule("zoning_districts")
    assert r.share({S: np.full((2, 1), 0.5), C: np.full((2, 1), 0.7)})[0, 1] == 0.5
