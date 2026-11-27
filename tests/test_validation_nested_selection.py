"""Tests for the nested rolling-origin selection study (validation.nested_selection)."""

from __future__ import annotations

import copy

import pytest
import yaml

from visionhousing.baseline.contest import place_info_for_context
from visionhousing.engine.context import load_context
from visionhousing.validation import nested_selection as ns
from visionhousing.validation.preregistration import PreregistrationError, load_registry, verify


@pytest.fixture(scope="module")
def tl_study():
    ctx = load_context("TL")
    return ns.run_study(["TL"], inputs={"TL": (ctx.permits, place_info_for_context(ctx))})


def test_block_is_preregistered_and_pinned():
    doc = ns.load_settings()
    entry = load_registry()["blocks"][ns.BLOCK]
    assert entry["logged_before_first_score"] is True
    assert verify(ns.BLOCK, doc)["verified"]
    changed = copy.deepcopy(doc)
    changed[ns.BLOCK]["min_past_origins"] = 7
    with pytest.raises(PreregistrationError):
        ns.run_study(["TL"], settings_doc=changed)


def test_matured_keeps_only_cases_observed_by_the_origin():
    assert ns.matured([2000, 2005, 2006, 2007], 2012, lead=2, horizon=5) == [2000, 2005, 2006]


def test_testland_parity_audit_and_arms(tl_study):
    st = tl_study["states"]["TL"]
    assert st["parity"]["match"] is True
    assert st["fold_audit_ok"] is True
    assert set(st["arms"]) == {"full_record", "full_record_permit_only", "nested"}
    for row in st["fold_audit"]:
        assert row["selection_last_target_year"] <= row["origin"]
        assert row["evaluation_target_years"][0] > row["origin"]
    for arm in st["arms"].values():
        sr = arm["state_range"]
        assert sr["covered"] <= sr["cases"] <= len(st["evaluation_origins"])
        for r in sr["by_origin"].values():
            assert r["p10"] <= r["p50"] <= r["p90"]
            assert r["caught"] == (r["p10"] <= r["actual"] <= r["p90"])


def test_permit_only_info_uses_no_typology_or_homes():
    from visionhousing.baseline.panel import to_panel

    ctx = load_context("TL")
    info = ns.permit_only_info(to_panel(ctx.permits), 2015, 5, 3)
    assert info.source == "size_class" and info.homes is None


def test_saved_results_cover_the_four_states():
    doc = yaml.safe_load(ns.RESULTS_PATH.read_text(encoding="utf-8"))
    assert doc["preregistration"]["sha256"] == load_registry()["blocks"][ns.BLOCK]["sha256"]
    assert set(doc["states"]) == {"PA", "VT", "MA", "WY"}
    for st in doc["states"].values():
        assert st["parity"]["match"] is True
        assert st["fold_audit_ok"] is True
        assert st["arms"]["full_record"]["rule_by_origin"]
        assert all(
            v == st["shipped_rule"] for v in st["arms"]["full_record"]["rule_by_origin"].values()
        )
