"""Treatment-date audit of the retrospective panel (visionhousing.evidence.retro_audit)."""

from __future__ import annotations

import copy

import numpy as np
import pytest
import yaml

from visionhousing.evidence import retro, retro_audit
from visionhousing.validation.preregistration import PreregistrationError, verify

YEARS = np.arange(2012, 2026)


def test_never_treated_only_drops_not_yet_treated_controls():
    cohort = np.array([0, 0, 2020, 2023])
    assert list(retro._controls(cohort, 2020, 2021)) == [True, True, False, True]
    assert list(retro._controls(cohort, 2020, 2021, False)) == [True, True, False, False]


def test_never_treated_only_equals_dropping_the_later_case():
    rng = np.random.default_rng(4)
    cohort = np.array([0] * 30 + [2020, 2023])
    y = rng.normal(1.0, 0.3, (cohort.size, YEARS.size))
    y[31, YEARS >= 2021] += 3.0  # the later case moves before its own g
    ev = [0, 1]
    with_later = retro.unit_effects(y, YEARS, cohort, [30], ev)
    without = retro.unit_effects(y, YEARS, cohort, [30], ev, not_yet_treated=False)
    dropped = retro.unit_effects(y[:31], YEARS, cohort[:31], [30], ev)
    assert np.allclose(without, dropped)
    assert not np.allclose(with_later, without)


def _audit_block():
    return retro_audit.load_audit()[retro_audit.BLOCK]


def test_audit_block_matches_its_fingerprint_and_the_design():
    verify(retro_audit.BLOCK, retro_audit.load_audit())
    assert retro_audit.check_definitions(_audit_block(), retro.load_design()) == []


def test_audit_names_the_corrected_phases_and_convention():
    cases = {c["record"]: c for c in _audit_block()["cases"]}
    stp = cases["mn.saint_paul_1to4_units"]
    assert str(stp["effective_date"]) == "2023-11-26" and stp["phase"] == "Phase 2"
    assert stp["g"]["audited"] == 2024 and stp["g_before"] == 2022
    pdx = cases["or.portland_rip_2021"]
    assert pdx["g"] == {"audited": 2022, "any_exposure": 2021, "first_full_year_all_phases": 2023}
    assert "June 30" in _audit_block()["year_convention"]["rule"]


def test_check_definitions_catches_a_broken_convention():
    block = copy.deepcopy(_audit_block())
    block["cases"][1]["g"]["audited"] = 2023  # Saint Paul: Nov 26 must give the next year
    problems = retro_audit.check_definitions(block, retro.load_design())
    assert any("June-30 rule" in p for p in problems)


def test_changed_audit_block_is_refused():
    audit = copy.deepcopy(retro_audit.load_audit())
    audit[retro_audit.BLOCK]["variants"]["audited"]["comparisons"] = "anything"
    with pytest.raises(PreregistrationError):
        verify(retro_audit.BLOCK, audit)


def test_robust_to_convention():
    rows = {"a": {"ci": [-1, 1]}, "b": {"ci": [-0.2, 0.3]}, "c": {"ci": [0.1, 0.5]}}
    assert retro_audit.robust_to_convention(rows, ["a", "b"])
    assert not retro_audit.robust_to_convention(rows, ["a", "c"])


def test_repository_audit_results():
    path = retro_audit.OUT_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert retro_audit.check_audit(path) == []
    prov = data["provenance"]
    # The original design re-run on the same pool and files reproduces "before".
    assert prov["reproduction"]["matches"] is True
    assert prov["panel"]["pool"] == 81
    rows = {r["record"]: r for r in data["before_after"]}
    assert rows["mn.saint_paul_1to4_units"]["g_changed"] is True
    assert rows["mn.saint_paul_1to4_units"]["after"]["g"] == 2024
    assert rows["or.portland_rip_2021"]["after"]["g"] == 2022
    for r in data["before_after"]:
        lo, hi = r["after"]["ci"]
        assert r["after"]["includes_zero"] == (lo <= 0 <= hi)  # null results stay visible
        assert set(r["variants"]) == set(_audit_block()["variants"])
