"""The audited retro panel on today's comparison pool (evidence.retro_current_pool)."""

from __future__ import annotations

import copy

import pytest
import yaml

from visionhousing.evidence import retro, retro_audit
from visionhousing.evidence import retro_current_pool as rcp
from visionhousing.evidence.records import load_panel
from visionhousing.validation.preregistration import PreregistrationError, verify


def _block():
    return rcp.load_block_file()[rcp.BLOCK]


def test_block_matches_its_fingerprint_and_names_both_parents():
    verify(rcp.BLOCK, rcp.load_block_file())
    block = _block()
    assert block["inherits"] == [retro.PREREG_BLOCK, retro_audit.BLOCK]
    assert set(retro_audit.load_audit()[retro_audit.BLOCK]["variants"]) < set(block["variants"])
    assert block["variants"]["original_design"]["g"] == "design"


def test_changed_block_is_refused():
    doc = copy.deepcopy(rcp.load_block_file())
    doc[rcp.BLOCK]["pool"]["excluded_states"].remove("NV")
    with pytest.raises(PreregistrationError):
        verify(rcp.BLOCK, doc)


def test_frozen_pool_is_the_design_rule_on_todays_records():
    pool = _block()["pool"]
    today = retro.excluded_states(retro.load_design(), load_panel().records)
    assert sorted(pool["excluded_states"]) == sorted(today)
    original = yaml.safe_load(retro_audit.OUT_PATH.read_text(encoding="utf-8"))
    before = set(original["provenance"]["panel"]["excluded_states"])
    assert sorted(set(pool["excluded_states"]) - before) == sorted(pool["added_since_original_run"])
    left = sum(len(v) for v in pool["offices_leaving_the_pool"].values())
    assert original["provenance"]["panel"]["pool"] - left == pool["expected_pool"] == 68


def test_labels():
    assert rcp.pool_label("original", 81) == "original pool (81, as pre-registered)"
    assert rcp.pool_label("current", 68) == "current pool (68, update)"


def test_conclusions_rule():
    def s(lo, hi, ident="causal", pre=None):
        return {"ci": [lo, hi], "identification": ident, "pretrend_p": pre}

    before = {"cases": {"a": s(-1, 1), "b": s(-1, 1)}, "pooled": s(-0.2, 0.3, pre=0.25)}
    same = {"cases": {"a": s(-0.5, 2), "b": s(-2, 0.1)}, "pooled": s(-0.3, 0.2, pre=0.2)}
    assert rcp.conclusions(before, same, 0.10) == {"change": False, "changes": []}
    moved = copy.deepcopy(same)
    moved["cases"]["a"] = s(0.1, 2)
    moved["cases"]["b"]["identification"] = "matched"
    moved["pooled"]["pretrend_p"] = 0.05
    out = rcp.conclusions(before, moved, 0.10)
    assert out["change"] and len(out["changes"]) == 3
    assert rcp.side_of_zero([0.1, 2]) == "above" and rcp.side_of_zero([-2, -0.1]) == "below"


def test_both_results_files_load_with_their_pools():
    original = yaml.safe_load(retro_audit.OUT_PATH.read_text(encoding="utf-8"))
    current = yaml.safe_load(rcp.OUT_PATH.read_text(encoding="utf-8"))
    assert retro_audit.check_audit() == []
    assert rcp.check_current_pool() == []
    assert original["provenance"]["panel"]["pool"] == 81
    assert current["provenance"]["panel"]["pool"] == 68
    pools = current["pools"]
    assert pools["original"]["label"] == "original pool (81, as pre-registered)"
    assert pools["current"]["label"] == "current pool (68, update)"
    assert pools["original"]["file"] == "assumptions/evidence/retro_audit_results.yaml"
    assert current["provenance"]["frozen_list_matches_today"] is True
    # "before" is the published record: the audited variant on the original pool.
    assert current["before"] == original["variants"]["audited"]
    rows = {r["record"]: r for r in current["before_after"]}
    assert set(rows) == {c["record"] for c in retro.load_design()["cases"]}
    for r in rows.values():
        assert r["before"]["comparisons"] == 81 and r["after"]["comparisons"] == 68
        lo, hi = r["after"]["ci"]
        assert r["after"]["includes_zero"] == (lo <= 0 <= hi)
        assert set(r["variants"]) == set(_block()["variants"])
    assert current["pooled"]["before"]["comparisons"] == 81
    assert current["pooled"]["audited"]["comparisons"] == 68
    assert set(current["conclusions"]) == {"change", "changes"}
