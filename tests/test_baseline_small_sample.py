"""Small-sample widening of the state baseline range (assumptions/backtest.yaml, small_sample)."""

from __future__ import annotations

import importlib
import math

import numpy as np
import pytest

from visionhousing.baseline import baseline_from_ledger
from visionhousing.baseline.project import load_small_sample_rule, widen_small_sample
from visionhousing.engine.context import PACKS_DIR, load_context

Q = (0.1, 0.5, 0.9)
RULE = {
    "full_weight_cases": 1000,
    "prior": {"state": "XX", "ratio_quantiles": {0.1: 0.5, 0.5: 1.0, 0.8: 1.2, 0.9: 1.5}},
}


def test_enough_cases_keep_their_own_range_exactly():
    assert widen_small_sample(-0.7, 0.2, 1000, Q, RULE) == (-0.7, 0.2, "")
    assert widen_small_sample(-0.7, 0.2, 5000, Q, RULE) == (-0.7, 0.2, "")
    assert widen_small_sample(-0.7, 0.2, 10, Q, None) == (-0.7, 0.2, "")


def test_few_cases_blend_toward_the_prior_and_only_widen():
    low, high, note = widen_small_sample(-0.9, 0.1, 250, Q, RULE)
    w = 0.25
    assert low == -0.9  # own end is already wider than the blend: kept
    assert high == pytest.approx(w * 0.1 + (1 - w) * math.log(1.5))
    assert "250 county-origins" in note and "XX prior" in note


def test_fewer_cases_widen_more_and_never_narrow():
    widths = []
    for n in (50, 200, 500, 900, 1000):
        lo, hi, _ = widen_small_sample(-0.3, 0.1, n, Q, RULE)
        assert lo <= -0.3 and hi >= 0.1
        widths.append(hi - lo)
    assert widths == sorted(widths, reverse=True)


def test_prior_is_interpolated_for_any_cycle_quantile():
    lo, hi, _ = widen_small_sample(0.0, 0.0, 0, (0.1, 0.65, 0.9), RULE)
    centre = float(np.interp(0.65, [0.1, 0.5, 0.8, 0.9], np.log([0.5, 1.0, 1.2, 1.5])))
    assert lo == pytest.approx(math.log(0.5) - centre)
    assert hi == pytest.approx(math.log(1.5) - centre)


def test_widening_on_testland_data_says_why_and_only_widens():
    ctx = load_context("TL")
    plain, _ = baseline_from_ledger(ctx.permits, ctx.ledger)
    wide, _ = baseline_from_ledger(ctx.permits, ctx.ledger, widen=True)
    assert "small_sample" not in plain.state.formula  # Testland runs are never widened
    assert "small_sample" in wide.state.formula
    assert wide.state.p50 == plain.state.p50
    assert wide.state.p10 <= plain.state.p10 and wide.state.p90 >= plain.state.p90
    assert wide.counties == plain.counties  # county ranges are not widened


def test_engine_widens_real_states_only():
    from visionhousing.engine.core import prepare

    rc, _ = prepare(load_context("TL"), None, seed=0, draws=10)
    assert "small_sample" not in rc.baseline.state.formula


def test_shipped_rule_is_well_formed():
    rule = load_small_sample_rule()
    assert rule["full_weight_cases"] > 0
    qs = rule["prior"]["ratio_quantiles"]
    assert {0.1, 0.5, 0.9} <= {float(k) for k in qs}
    vals = [qs[k] for k in sorted(qs)]
    assert vals == sorted(vals)


@pytest.mark.skipif(not (PACKS_DIR / "PA").is_dir(), reason="needs the PA pack (make data)")
def test_prior_matches_the_pennsylvania_back_test_and_pa_is_unchanged():
    from visionhousing.baseline.backtest import backtest_cases, backtest_origins, lead_years
    from visionhousing.baseline.contest import contest_rules, place_info_for_context
    from visionhousing.baseline.panel import to_panel
    from visionhousing.baseline.places import load_place_rules
    from visionhousing.baseline.settings import settings_from_ledger

    ctx = load_context("PA")
    settings = settings_from_ledger(ctx.ledger)
    name = load_small_sample_rule()["prior"]["rule"]
    rule = next(r for r in contest_rules(ctx.ledger, load_place_rules()) if r.name == name)
    panel = to_panel(ctx.permits)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    cases = backtest_cases(rule, panel.years, panel.state_series(), origins, lead, settings.horizon)
    prior = load_small_sample_rule()["prior"]["ratio_quantiles"]
    levels = sorted(float(k) for k in prior)
    got = np.quantile(cases["ratio"], levels)
    assert got == pytest.approx([prior[k] for k in sorted(prior)], abs=5e-4)
    result, _ = baseline_from_ledger(
        ctx.permits, ctx.ledger, widen=True, places=place_info_for_context(ctx)
    )
    assert result.rule == name  # the prior is Pennsylvania's own winner
    assert "small_sample" not in result.state.formula
    assert "growth-aware" not in result.state.formula  # PA has a full record: unchanged


# ---------------------------------------------------------------- growth-aware widening

GROWTH = {"enabled": True, "min_pool_cases": 5, "full_weight_cases": 1000, "origin": 2025}
VT_LIKE = {"growth_class": "fast", "pool_cases": 330, "pool_ratio_p10": 0.4475,
           "pool_ratio_p90": 1.5414}  # fmt: skip


def test_growth_widening_raises_the_high_end_and_keeps_a_wider_low_end():
    from visionhousing.baseline.project import widen_growth_class

    low, high = math.log(0.444), math.log(1.197)  # VT-like ends after small-sample widening
    new_low, new_high, note = widen_growth_class(low, high, 294, VT_LIKE, GROWTH)
    w = 0.294
    assert new_low == low  # log(0.444) is already below the blend: unchanged
    assert new_high == pytest.approx(w * high + (1 - w) * math.log(1.5414))
    assert new_high > high
    assert "growth class fast at 2025" in note and "330 other-state cases" in note


def test_growth_widening_never_narrows_and_skips_full_records_and_small_pools():
    from visionhousing.baseline.project import widen_growth_class

    for lo, hi in ((-0.2, 0.1), (-1.5, 0.9), (-0.8, 0.43)):
        nl, nh, _ = widen_growth_class(lo, hi, 300, VT_LIKE, GROWTH)
        assert nl <= lo and nh >= hi
    assert widen_growth_class(-0.2, 0.1, 1000, VT_LIKE, GROWTH) == (-0.2, 0.1, "")
    small = {**VT_LIKE, "pool_cases": 4}
    assert widen_growth_class(-0.2, 0.1, 300, small, GROWTH) == (-0.2, 0.1, "")
    assert widen_growth_class(-0.2, 0.1, 300, None, GROWTH) == (-0.2, 0.1, "")


def test_growth_widening_applies_only_when_adopted_and_listed(tmp_path):
    import yaml

    from visionhousing.baseline.places import load_place_rules
    from visionhousing.ledger import load_ledger
    from visionhousing.ledger.loader import ROOT

    project = importlib.import_module("visionhousing.baseline.project")

    rules, ledger = load_place_rules(), load_ledger()
    vt = project.growth_widen_for("VT", ledger, rules, 2025)
    assert vt is not None and vt["entry"]["growth_class"] and vt["full_weight_cases"] > 0
    assert project.growth_widen_for("PA", ledger, rules, 2025) is None  # not a sparse state
    assert project.growth_widen_for("TL", ledger, rules, 2025) is None
    assert project.growth_widen_for(None, ledger, rules, 2025) is None
    study = yaml.safe_load((ROOT / project.GROWTH_SOURCE).read_text(encoding="utf-8"))
    study["decision"]["outcome"] = "keep_current"
    path = tmp_path / "growth.yaml"
    path.write_text(yaml.safe_dump(study))
    kept = {**rules, "growth_widen": {**rules["growth_widen"], "source": str(path)}}
    assert project.growth_widen_for("VT", ledger, kept, 2025) is None
    off = {**rules, "growth_widen": {**rules["growth_widen"], "enabled": False}}
    assert project.growth_widen_for("VT", ledger, off, 2025) is None


def test_growth_widening_on_testland_data_only_widens_the_state_range(monkeypatch):
    ctx = load_context("TL")
    plain, _ = baseline_from_ledger(ctx.permits, ctx.ledger, widen=True)
    project = importlib.import_module("visionhousing.baseline.project")
    growth = {**GROWTH, "entry": {**VT_LIKE, "pool_ratio_p90": 3.0, "pool_ratio_p10": 0.2}}
    monkeypatch.setattr(project, "growth_widen_for", lambda *a, **k: growth)
    wide, _ = baseline_from_ledger(ctx.permits, ctx.ledger, widen=True, state="VT")
    assert wide.state.p50 == plain.state.p50
    assert wide.state.p10 <= plain.state.p10 and wide.state.p90 >= plain.state.p90
    assert "growth-aware widening" in wide.state.formula
    assert "baseline.growth_widen" in wide.state.assumption_ids
    assert any("growth class" in i.name for i in wide.state.inputs)
    assert wide.counties == plain.counties
