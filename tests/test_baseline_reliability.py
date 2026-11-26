"""Tests for the measured reliability of the baseline range (visionhousing.baseline.reliability)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from visionhousing.baseline import reliability as rel
from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    lead_years,
    origin_size_classes,
    validate_baseline,
)
from visionhousing.baseline.contest import contest_rules
from visionhousing.baseline.panel import STRUCTURE_TYPES, to_panel
from visionhousing.baseline.places import load_place_rules
from visionhousing.baseline.project import project_baseline
from visionhousing.baseline.settings import settings_from_ledger
from visionhousing.ledger import load_ledger
from visionhousing.validation.preregistration import PreregistrationError, load_registry, verify

PERMITS = Path(__file__).parent / "fixtures" / "testland" / "permits_county_year.csv"
TEMPLATE = (
    "In past years, ranges like this caught the real five-year total in {covered} of "
    "{cases} cases (out of time)."
)
LABEL = "range (caught {pct}% of past totals)"


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def settings(ledger):
    return settings_from_ledger(ledger)


@pytest.fixture(scope="module")
def table():
    t = pd.read_csv(PERMITS)
    return t.assign(county=t["county"].astype(str))


@pytest.fixture(scope="module")
def rules():
    return load_place_rules()


def _rule(ledger, rules, name="last_3_mean"):
    return next(r for r in contest_rules(ledger, rules) if r.name == name)


def test_fingerprint_is_pinned_and_recorded_before_scoring(rules):
    entry = load_registry()["blocks"]["baseline_reliability"]
    assert entry["sha256"] == "3207b76a91e6ca9502791bc8ad9b01aab4d1d68f35f055538820edfaedc704a5"
    assert entry["logged_before_first_score"] is True
    assert verify("baseline_reliability", rules)["verified"]
    bad = {
        **rules,
        "baseline_reliability": {**rules["baseline_reliability"], "min_past_origins": 9},
    }
    with pytest.raises(PreregistrationError):
        verify("baseline_reliability", bad)
    with pytest.raises(PreregistrationError):
        rel.run_study(["PA"], rules=bad, inputs={})


def test_block_holds_the_shown_words(rules):
    block = rules["baseline_reliability"]
    assert block["sentence"] == TEMPLATE
    assert block["label"] == LABEL


def test_sentence_and_label_by_hand():
    c = {"cases": 8, "covered": 6, "coverage": 0.75}
    assert rel.sentence(c, TEMPLATE) == (
        "In past years, ranges like this caught the real five-year total in 6 of 8 cases "
        "(out of time)."
    )
    assert rel.label(c, LABEL) == "range (caught 75% of past totals)"
    assert rel.label({"cases": 3, "covered": 2}, LABEL) == "range (caught 67% of past totals)"
    assert rel.sentence({"cases": 0, "covered": 0}, TEMPLATE) is None
    assert rel.label(None, LABEL) is None


def test_county_counts_match_the_contest_and_add_up(table, ledger, rules, settings):
    rule = _rule(ledger, rules)
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    classes = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    cases = backtest_cases(rule, panel.years, panel.units, origins, lead, settings.horizon)
    got = rel.county_reliability(cases, classes, lead, settings.horizon, settings.n_classes)
    skill = validate_baseline(table, [rule], origins, settings).iloc[0]
    assert got["all"]["cases"] == skill["coverage_cases"]
    assert math.isclose(got["all"]["coverage"], skill["coverage_80"])
    groups = got["groups"]
    assert set(groups) == {"small", "medium", "large"}
    assert sum(g["cases"] for g in groups.values()) == got["all"]["cases"]
    assert sum(g["covered"] for g in groups.values()) == got["all"]["covered"]


def _state(totals: np.ndarray) -> np.ndarray:
    u = np.zeros((1, totals.size, len(STRUCTURE_TYPES)))
    u[0, :, 0] = totals
    return u


def _cases_frame(origins):
    return pd.DataFrame({"place": 0, "origin": list(origins), "projected": 1.0, "actual": 1.0,
                         "ratio": 1.0})  # fmt: skip


def test_state_count_flat_series_is_all_caught(ledger, rules, settings):
    years = np.arange(1990, 2026)
    units = _state(np.full(years.size, 100.0))
    lead = settings.first_year - 2025
    origins = backtest_origins(years, lead, settings.horizon, settings.min_history)
    block = {"state_quantiles": [0.1, 0.5, 0.9], "min_past_origins": 8}
    got = rel.state_reliability(
        _rule(ledger, rules), years, units, _cases_frame(origins), origins, lead,
        settings.horizon, block,
    )  # fmt: skip
    assert got["cases"] == got["covered"] > 0
    # the first scored origin has exactly eight earlier cases
    assert got["origins"][0] == origins[0] + lead + settings.horizon - 1 + 7


def test_state_count_uses_only_earlier_totals(ledger, rules, settings):
    """Spoiling the years after a scored case's targets changes no earlier verdict."""
    years = np.arange(1990, 2026)
    rng = np.random.default_rng(3)
    base = 100.0 * np.exp(rng.normal(0, 0.3, years.size))
    lead = settings.first_year - 2025
    origins = backtest_origins(years, lead, settings.horizon, settings.min_history)
    block = {"state_quantiles": [0.1, 0.5, 0.9], "min_past_origins": 8}
    rule = _rule(ledger, rules)

    def verdicts(series):
        out = {}
        units = _state(series)
        cases = backtest_cases(rule, years, units, origins, lead, settings.horizon)
        for o in origins:
            sub = [x for x in origins if x <= o]
            st = rel.state_reliability(rule, years, units, _cases_frame(sub), sub, lead,
                                       settings.horizon, block)  # fmt: skip
            out[o] = (st["covered"], st["cases"])
        return out, cases

    first, _ = verdicts(base)
    spoiled = base.copy()
    spoiled[-3:] *= 50.0  # only the last cases' target years change
    second, _ = verdicts(spoiled)
    last_clean = 2025 - 3 - lead - settings.horizon + 1
    for o in origins:
        if o <= last_clean:
            assert first[o] == second[o], o


def test_attach_keeps_values_and_adds_sentences(table, ledger, rules, settings):
    rule = _rule(ledger, rules)
    result = project_baseline(table, rule, settings)
    entry = {
        "rule": rule.name,
        "sentence": TEMPLATE,
        "label": LABEL,
        "source": "assumptions/calibration/baseline_reliability.yaml",
        "state_range": {"cases": 8, "covered": 7},
        "county_ranges": {"all": {"cases": 30, "covered": 15},
                          "groups": {"small": {"cases": 10, "covered": 4},
                                     "medium": {"cases": 10, "covered": 5},
                                     "large": {"cases": 10, "covered": 6}}},
    }  # fmt: skip
    out = rel.attach(result, table, settings, entry)
    assert (out.state.p10, out.state.p50, out.state.p90) == (
        result.state.p10, result.state.p50, result.state.p90)  # fmt: skip
    assert "7 of 8 cases (out of time)" in out.state.formula
    assert rel.range_tag(out.state.formula) == "range (caught 88% of past totals)"
    assert out.state.formula.startswith(result.state.formula)
    for c, rec in out.counties.items():
        assert rec.p50 == result.counties[c].p50
        assert "-county ranges" in rec.formula and "of 10 cases" in rec.formula
    assert rel.range_tag(result.state.formula) is None


def test_reliability_for_reads_the_state(tmp_path):
    path = tmp_path / "r.yaml"
    states = {"PA": {"rule": "x", "state_range": {}}}
    path.write_text(yaml.safe_dump({"sentence": TEMPLATE, "range_label": LABEL, "states": states}))
    got = rel.reliability_for("pa", path)
    assert got["rule"] == "x" and got["sentence"] == TEMPLATE
    assert rel.reliability_for("VT", path) is None
    assert rel.reliability_for(None, path) is None
    assert rel.reliability_for("PA", tmp_path / "missing.yaml") is None


def test_shipped_results_match_the_block(rules):
    doc = yaml.safe_load(rel.RESULTS_PATH.read_text(encoding="utf-8"))
    assert doc["preregistration"]["sha256"] == load_registry()["blocks"][
        "baseline_reliability"]["sha256"]  # fmt: skip
    assert doc["sentence"] == rules["baseline_reliability"]["sentence"]
    for st in doc["states"].values():
        c = st["county_ranges"]
        assert sum(g["cases"] for g in c["groups"].values()) == c["all"]["cases"]
        assert 0 <= st["state_range"]["covered"] <= st["state_range"]["cases"]


def test_validation_payload_carries_the_counts():
    from types import SimpleNamespace

    from visionhousing.api.service import _reliability

    doc = yaml.safe_load(rel.RESULTS_PATH.read_text(encoding="utf-8"))
    state, entry = next(iter(doc["states"].items()))
    got = _reliability(SimpleNamespace(state=state, synthetic=False), entry["rule"])
    s = got["state_range"]
    assert s["sentence"] == rel.sentence(entry["state_range"], doc["sentence"])
    assert s["label"].startswith("range (caught ")
    assert set(got["county_ranges"]["groups"]) == {"small", "medium", "large"}
    assert _reliability(SimpleNamespace(state=state, synthetic=False), "no_such_rule") is None
    assert _reliability(SimpleNamespace(state="TL", synthetic=True), entry["rule"]) is None


def test_reliability_label_needs_the_same_range():
    """The count is shown only for the range it was measured on (review R5 F4)."""
    from visionhousing.baseline.project import reliability_matches

    entry = {"rule": "top_down_share", "bias_correction_kept": False, "growth_widening": False}
    same = {"rule": "top_down_share", "bias_correction_kept": False, "growth_widening": False}
    assert reliability_matches(entry, **same, cycle="typical")
    assert not reliability_matches(None, **same, cycle="typical")
    assert not reliability_matches(entry, **{**same, "rule": "other"}, cycle="typical")
    assert not reliability_matches(entry, **{**same, "bias_correction_kept": True}, cycle="typical")
    assert not reliability_matches(entry, **{**same, "growth_widening": True}, cycle="typical")
    for cycle in ("weak", "strong"):
        assert not reliability_matches(entry, **same, cycle=cycle)
