"""Tests for the state confidence check (small synthetic panels; no network)."""

from __future__ import annotations

import argparse
import copy

import pytest
import yaml

from tests.test_validation_donor_pool import BSET, RULE, synthetic_panel
from visionhousing.intake.readiness import _holdout
from visionhousing.validation import donor_pool as dp
from visionhousing.validation import growth_coverage as gc
from visionhousing.validation import state_holdouts as sh

# Recorded in assumptions/preregistration.yaml and the decision log before the first
# score. Never update this to match an edited block: an edit makes it a new check.
CHECK_SHA256 = "bb09229bc08c972a1ca937746341013c5e256e1dd82d430fa38fcdc6b7ef9127"


def _settings():
    s = copy.deepcopy(dp.load_settings())
    s.update(prior_state="PA", full_weight_cases=1000)
    return s


def _check(states=("VT", "NH")):
    c = copy.deepcopy(sh.load_check())
    c[sh.BLOCK_KEY]["states"] = list(states)
    return c


def test_block_fingerprint_pinned_and_registered():
    from visionhousing.validation import preregistration as pr

    c = sh.load_check()
    assert pr.fingerprint(c[sh.BLOCK_KEY]) == CHECK_SHA256
    reg = pr.load_registry()
    assert reg["blocks"][sh.BLOCK_KEY]["sha256"] == CHECK_SHA256
    assert pr.verify(sh.BLOCK_KEY, c, reg)["verified"] is True
    assert c[sh.BLOCK_KEY]["states"] == ["MA", "WY", "VT"]


@pytest.fixture(scope="module")
def doc():
    return sh.run_check(
        synthetic_panel(seed=4), _settings(), _check(), BSET, RULE,
        as_of="2026-01-01", check_preregistration=False,
    )  # fmt: skip


def test_check_document(doc):
    assert set(doc["states"]) == {"VT", "NH"}
    for row in doc["states"].values():
        assert row["cases"] > 0
        assert row["covered"] + row["above"] + row["below"] == row["cases"]
        assert row["coverage"] == pytest.approx(row["covered"] / row["cases"], abs=1e-4)
        assert row["held_out"] is (row["cases"] >= 5)
        assert row["weight_own"] < 1
    assert doc["settings"]["method"] == "pa_blend"


def test_matches_current_rule_of_growth_study():
    """Same method and cases as the third test's current rule, so the same coverage."""
    panel, s = synthetic_panel(seed=4), _settings()
    cases = dp.area_cases(panel, RULE, BSET, s)
    block = s[gc.GROWTH_KEY]
    classes = gc.growth_classes(gc.state_growth(panel, list(cases.origins), block), block)
    scored = gc.score_cases(cases, classes, ["VT"], s, block, with_candidate=False)
    got = sh.run_check(panel, s, _check(["VT"]), BSET, RULE, check_preregistration=False)
    assert got["states"]["VT"]["coverage"] == pytest.approx(
        float(scored["current_inside"].mean()), abs=1e-4
    )


def test_state_with_no_cases_is_not_held_out():
    got = sh.run_check(
        synthetic_panel(seed=4), _settings(), _check(["VT", "ZZ"]), BSET, RULE,
        check_preregistration=False,
    )  # fmt: skip
    assert got["states"]["ZZ"] == {"cases": 0, "held_out": False, "coverage": None}


def test_refuses_a_changed_block(monkeypatch):
    from visionhousing.validation.preregistration import PreregistrationError

    def boom(*a, **k):
        raise AssertionError("scored before the check")

    monkeypatch.setattr(dp, "area_cases", boom)
    c = copy.deepcopy(sh.load_check())
    c[sh.BLOCK_KEY]["min_cases_scored"] = 3
    with pytest.raises(PreregistrationError, match=sh.BLOCK_KEY):
        sh.run_check(synthetic_panel(seed=4), dp.load_settings(), c, BSET, RULE)
    with pytest.raises(AssertionError, match="scored before the check"):
        sh.run_check(synthetic_panel(seed=4), dp.load_settings(), sh.load_check(), BSET, RULE)


def test_write_and_held_out_states(doc, tmp_path):
    path = sh.write_document(doc, tmp_path / "state_holdouts.yaml")
    assert path.read_text(encoding="utf-8").startswith("# Calibration (validation)")
    held = sh.held_out_states(path)
    assert set(held) == {s for s, r in doc["states"].items() if r["held_out"]}
    assert sh.held_out_states(tmp_path / "absent.yaml") == {}


def _write(root, pool_states=None, rows=None):
    calib = root / "assumptions" / "calibration"
    calib.mkdir(parents=True, exist_ok=True)
    if pool_states is not None:
        (calib / "donor_pool_errors.yaml").write_text(
            yaml.safe_dump({"settings": {"held_out": pool_states}}), encoding="utf-8"
        )
    if rows is not None:
        (calib / "state_holdouts.yaml").write_text(
            yaml.safe_dump({"states": rows}), encoding="utf-8"
        )


def test_readiness_reads_the_state_check(tmp_path):
    row = {"cases": 11, "held_out": True, "coverage": 0.5455}
    _write(tmp_path, pool_states=["VT"], rows={"MA": row})
    got = _holdout("MA", tmp_path, synthetic=False)
    assert got["status"] == "pass"
    assert "55%" in got["detail"] and "state_holdouts.yaml" in got["detail"]
    assert _holdout("VT", tmp_path, synthetic=False)["status"] == "pass"
    assert _holdout("OH", tmp_path, synthetic=False)["status"] == "fail"
    assert _holdout("MA", tmp_path, synthetic=True)["status"] == "unknown"


def test_readiness_needs_held_out_flag(tmp_path):
    _write(tmp_path, rows={"MA": {"cases": 2, "held_out": False, "coverage": 0.5}})
    assert _holdout("MA", tmp_path, synthetic=False)["status"] == "fail"
    assert _holdout("MA", tmp_path / "empty", synthetic=False)["status"] == "unknown"


def test_cli_registers_command():
    from visionhousing.validation.commands import add_state_holdouts_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_state_holdouts_parser(sub)
    assert parser.parse_args(["validate-state-holdouts"]).command == "validate-state-holdouts"
