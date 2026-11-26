"""Tests for the donor-pool validation (small synthetic panels; no network)."""

from __future__ import annotations

import argparse
import copy
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline.rules import Rule
from visionhousing.baseline.settings import BaselineSettings
from visionhousing.validation import donor_pool as dp

RULE = Rule("last_3_mean", "mean", 1, window=3)
BSET = BaselineSettings(
    first_year=2027,
    horizon=5,
    recent_window=5,
    min_history=10,
    n_classes=3,
    tolerance=0.05,
    cycle_quantiles={"weak": 0.2, "typical": 0.5, "strong": 0.8},
)


def _settings(**over):
    s = copy.deepcopy(dp.load_settings())
    s.update(held_out=["VT", "PA"], prior_state="PA", full_weight_cases=1000)
    s["donor"] = dict(s["donor"], k=30, max_cases_per_county=3)
    s.update(over)
    return s


def synthetic_panel(seed: int = 0, sizes: dict[str, tuple[int, float]] | None = None):
    """States with counties: flat series with lognormal noise, 1990-2025."""
    rng = np.random.default_rng(seed)
    sizes = sizes or {"VT": (3, 60.0), "PA": (12, 400.0), "NH": (4, 80.0), "OH": (10, 300.0)}
    rows = []
    for st, (n, level) in sizes.items():
        for c in range(n):
            base = level * rng.uniform(0.5, 1.5)
            for y in range(1990, 2026):
                u = base * math.exp(rng.normal(0, 0.3))
                rep = u * rng.uniform(0.6, 1.0)
                rows.append(
                    {
                        "county": f"{st}{c:03d}",
                        "state": st,
                        "year": y,
                        "units_1": u * 0.7,
                        "units_2": u * 0.1,
                        "units_3_4": u * 0.05,
                        "units_5plus": u * 0.15,
                        "units_reported": rep,
                        "imputed": int(rep < u),
                    }
                )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- features


def test_area_features_by_hand():
    years = np.arange(2000, 2012)
    totals = np.array([[float(y - 1999) for y in years]])  # 1..12
    reported = totals / 2
    donor = {"size_years": 5, "growth_recent_years": 3, "growth_base_years": 7,
             "completeness_years": 10}  # fmt: skip
    f = dp.area_features(totals, reported, years, [2009], donor)
    assert f["size"][0, 0] == pytest.approx(np.mean([6, 7, 8, 9, 10]))
    recent, base = np.mean([8, 9, 10]), np.mean([1, 2, 3, 4, 5, 6, 7])
    assert f["growth"][0, 0] == pytest.approx(math.log((recent + 1) / (base + 1)))
    assert f["completeness"][0, 0] == pytest.approx(0.5)


def test_area_features_ignore_unreported_years():
    years = np.arange(2000, 2006)
    totals = np.array([[10.0, np.nan, 10.0, 10.0, np.nan, 20.0]])
    reported = np.array([[10.0, 99.0, 5.0, 10.0, 99.0, 20.0]])
    donor = {"size_years": 3, "growth_recent_years": 2, "growth_base_years": 2,
             "completeness_years": 6}  # fmt: skip
    f = dp.area_features(totals, reported, years, [2005], donor)
    assert f["size"][0, 0] == pytest.approx(15.0)  # 10 and 20; the missing year is not zero
    assert f["completeness"][0, 0] == pytest.approx(45 / 50)


# --------------------------------------------------------------------------- donors


def _cands():
    return pd.DataFrame(
        {
            "area": ["a", "a", "a", "a", "b", "c", "d"],
            "state": ["X"] * 7,
            "origin": [2000, 2001, 2002, 2003, 2000, 2000, 2000],
            "ratio": [1.0, 1.1, 1.2, 1.3, 0.9, np.nan, 1.0],
            "size": [100.0, 100.0, 100.0, 100.0, 120.0, 100.0, 0.0],
            "growth": [0.0] * 7,
            "completeness": [1.0] * 7,
        }
    )


def test_select_donors_nearest_with_cap_and_eligibility():
    donor = {"weights": {"log_size": 1, "growth": 1, "completeness": 1}, "k": 10,
             "max_cases_per_county": 2, "min_completeness": 0.0}  # fmt: skip
    got = dp.select_donors(_cands(), {"size": 100.0, "growth": 0.0, "completeness": 1.0}, donor)
    # county a capped at two (its nearest, ties by origin); c has no ratio; d has no permits
    assert list(zip(got["area"], got["origin"], strict=True)) == [
        ("a", 2000),
        ("a", 2001),
        ("b", 2000),
    ]
    assert got["distance"].iloc[-1] == pytest.approx(math.log(1.2))


def test_select_donors_k_and_completeness_floor():
    donor = {"weights": {"log_size": 1, "growth": 1, "completeness": 1}, "k": 1,
             "max_cases_per_county": 5, "min_completeness": 0.0}  # fmt: skip
    got = dp.select_donors(_cands(), {"size": 120.0, "growth": 0.0, "completeness": 1.0}, donor)
    assert list(got["area"]) == ["b"]
    cands = _cands().assign(completeness=0.5)
    floor = dict(donor, min_completeness=0.6)
    assert dp.select_donors(cands, {"size": 100.0, "growth": 0.0, "completeness": 1.0}, floor).empty


def test_blend_only_widens():
    own, prior = (-0.1, 0.1, 1.0), (-0.5, 0.5, 1.2)
    low, high, mid = dp._blend(own, prior, 0.25)
    assert low == pytest.approx(0.25 * -0.1 + 0.75 * -0.5) and high == pytest.approx(0.4)
    assert mid == 1.0
    wide = (-1.0, 1.0, 1.0)
    assert dp._blend(wide, prior, 0.25)[:2] == (-1.0, 1.0)
    assert dp._blend(own, prior, 1.0)[:2] == (-0.1, 0.1)


# --------------------------------------------------------------------------- back-test


@pytest.fixture(scope="module")
def cases():
    return dp.area_cases(synthetic_panel(), RULE, BSET, _settings())


def test_area_cases_cover_counties_and_states(cases):
    assert cases.lead == 2 and cases.origins[0] == 1999 and cases.origins[-1] == 2019
    assert set(cases.states["area"]) == {"VT", "PA", "NH", "OH"}
    assert len(cases.counties) == (3 + 12 + 4 + 10) * 21
    vt = cases.states[(cases.states["area"] == "VT") & (cases.states["origin"] == 2005)].iloc[0]
    mine = cases.counties[(cases.counties["state"] == "VT") & (cases.counties["origin"] == 2005)]
    assert vt["actual"] == pytest.approx(mine["actual"].sum())
    assert vt["projected"] == pytest.approx(mine["projected"].sum())
    assert 0 < vt["completeness"] <= 1


def test_out_of_time_uses_only_completed_cases(cases):
    s = _settings()
    before = dp.method_ends(cases, "VT", 2012, "out_of_time", s)
    assert before is not None
    later = cases.states["origin"] > cases.completed_by(2012)
    changed = cases.states.copy()
    changed.loc[later, "ratio"] = 50.0
    changed_c = cases.counties.copy()
    changed_c.loc[changed_c["origin"] > cases.completed_by(2012), "ratio"] = 50.0
    other = dp.AreaCases(changed_c, changed, cases.origins, cases.lead, cases.horizon)
    after = dp.method_ends(other, "VT", 2012, "out_of_time", s)
    assert after["ends"] == before["ends"]
    # too early: own record has fewer than min_cases completed origins
    assert dp.method_ends(cases, "VT", 2005, "out_of_time", s) is None


def test_method_ends_exclude_target_state(cases):
    s = _settings()
    got = dp.method_ends(cases, "VT", 2015, "other_origins", s)
    # other origins at least a horizon away: no case shares a target year with the scored one
    far = (cases.counties["origin"] - 2015).abs() >= cases.horizon
    others = cases.counties[(cases.counties["state"] != "VT") & far]
    assert got["pool"]["national_counties"] == int(np.isfinite(others["ratio"]).sum())
    assert got["pool"]["donor"] == 30
    # origins 1999-2010 only (2011-2019 share target years with 2015)
    assert got["pool"]["own"] == 12 and got["pool"]["pa"] == 12
    assert 0 < got["w"] < 1


def test_score_and_summarise_identical_cases(cases):
    s = _settings()
    scored = dp.score_holdouts(cases, s)
    assert set(scored["method"]) == set(s["methods"])
    per = scored.groupby(["variant", "state", "method"]).size().unstack("method")
    assert (per.nunique(axis=1) == 1).all()  # every method on the same cases
    one = scored[(scored["variant"] == "out_of_time") & (scored["state"] == "VT")]
    assert one["abs_log_error"].nunique() == one["origin"].nunique()  # same P50 for all methods
    summ = dp.summarise(scored)
    assert ((summ["coverage"] >= 0) & (summ["coverage"] <= 1)).all()


# --------------------------------------------------------------------------- decision


def _summary(cov_c, width_c, cov_k, width_k, states=("VT", "NH")):
    rows = []
    for st in states:
        rows.append({"variant": "out_of_time", "state": st, "method": "donor_blend",
                     "cases": 10, "coverage": cov_c, "width": width_c})  # fmt: skip
        rows.append({"variant": "out_of_time", "state": st, "method": "pa_blend",
                     "cases": 10, "coverage": cov_k, "width": width_k})  # fmt: skip
    return pd.DataFrame(rows)


def test_decide_adopts_when_closer_and_not_too_wide():
    s = _settings(held_out=["VT", "NH", "PA"])
    d = dp.decide(_summary(0.8, 1.2, 0.6, 0.8), ["VT", "NH"], s)
    assert d["outcome"] == "adopt" and d["wider_than_current"] and not d["wider_than_needed"]
    assert d["decision_states"] == ["VT", "NH"]


def test_decide_keeps_current_when_not_closer_or_tie():
    s = _settings(held_out=["VT", "NH"])
    assert dp.decide(_summary(0.6, 0.8, 0.8, 1.0), ["VT", "NH"], s)["outcome"] == "keep_current"
    assert dp.decide(_summary(0.7, 0.8, 0.9, 1.0), ["VT", "NH"], s)["outcome"] == "keep_current"


def test_decide_keeps_current_when_wider_than_needed():
    s = _settings(held_out=["VT", "NH"])
    d = dp.decide(_summary(0.9, 1.5, 0.6, 0.8), ["VT", "NH"], s)
    assert d["closer_to_target"] and d["wider_than_needed"] and d["outcome"] == "keep_current"


def test_decide_ignores_non_sparse_states():
    s = _settings(held_out=["VT", "PA"])
    summ = pd.concat([_summary(0.6, 1, 0.8, 1, ("VT",)), _summary(0.8, 1, 0.2, 1, ("PA",))])
    assert dp.decide(summ, ["VT"], s)["outcome"] == "keep_current"


# --------------------------------------------------------------------------- whole study


def test_run_study_document():
    s = _settings(held_out=["VT", "NH", "PA"])
    doc = dp.run_study(
        synthetic_panel(1), s, BSET, RULE, as_of="2026-11-24", check_preregistration=False
    )
    assert doc["decision"]["outcome"] in ("adopt", "keep_current")
    assert doc["decision"]["decision_states"] == ["VT", "NH", "PA"]  # all sparse here
    assert set(doc["holdout"]["out_of_time"]) == {"VT", "NH", "PA"}
    vt = doc["sparse_states"]["VT"]
    q = vt["ratio_quantiles"]
    assert q[0.1] <= q[0.5] <= q[0.9]
    assert vt["donor_cases"] == 30 and vt["features_at"] == 2025
    assert doc["provenance"]["sources"][0]["id"] == "bps_county_annual"
    again = dp.run_study(
        synthetic_panel(1), s, BSET, RULE, as_of="2026-11-24", check_preregistration=False
    )
    assert again == doc


def test_write_document_round_trip(tmp_path):
    import yaml

    doc = {"version": 1, "id": "x", "sparse_states": {"VT": {"ratio_quantiles": {0.1: 0.5}}}}
    path = dp.write_document(doc, tmp_path / "d.yaml")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Calibration (validation)")
    assert yaml.safe_load(text)["sparse_states"]["VT"]["ratio_quantiles"][0.1] == 0.5


# --------------------------------------------------------------------------- national panel

HEAD = (
    "Survey,FIPS,FIPS,Region,Division,County,,1-unit,,,2-units,,,3-4 units,,,5+ units,,,"
    "1-unit rep,,,2-units rep,,,3-4 units rep,,, 5+units rep\n"
    "Date,State,County,Code,Code,Name," + ",".join(["Bldgs,Units,Value"] * 8) + "\n\n"
)


def _row(year, st, co, u1, r1):
    est = [u1, u1, 100, 0, 0, 0, 0, 0, 0, 1, 10, 500]
    rep = [r1, r1, 90, 0, 0, 0, 0, 0, 0, 1, 10, 500]
    return f"{year},{st},{co},1,1,Some County," + ",".join(map(str, est + rep)) + "\n"


def test_build_national_panel_from_files(tmp_path):
    for year in (2024, 2025):
        text = HEAD + _row(year, "50", "001", 20, 15) + _row(year, "72", "001", 5, 5)
        (tmp_path / f"co{year}a.txt").write_text(text, encoding="latin-1")
    paths = [tmp_path / "co2024a.txt", tmp_path / "co2025a.txt"]
    cache = tmp_path / "cache" / dp.PANEL_FILE
    panel = dp.build_national_panel(paths, {"states": ["VT"]}, cache)
    assert list(panel["county"]) == ["50001", "50001"]  # Puerto Rico left out
    assert list(panel["state"]) == ["VT", "VT"]
    assert panel["units_1"].tolist() == [20, 20] and panel["units_5plus"].tolist() == [10, 10]
    assert panel["units_reported"].tolist() == [25, 25] and panel["imputed"].tolist() == [1, 1]
    again = dp.build_national_panel([], {"states": ["VT"]}, cache)
    assert again["county"].tolist() == ["50001", "50001"]
    table = dp.state_table(panel, "VT")
    assert "units_reported" not in table and "state" not in table


def test_fetch_national_reuses_pack_cache(tmp_path):
    (tmp_path / "bps").mkdir()
    (tmp_path / "bps" / "co2025a.txt").write_text("x", encoding="utf-8")

    class NoNet:
        def download(self, *a, **k):  # pragma: no cover - must not be called
            raise AssertionError("network used")

    out = dp.fetch_national(tmp_path, [2025], client=NoNet(), manifest=tmp_path / "m.json")
    assert out == [tmp_path / "bps_national" / "co2025a.txt"] and out[0].read_text() == "x"


# --------------------------------------------------------------------------- confidence


def _rec(rid, state, types, ident, verified=True, observed=True):
    return SimpleNamespace(
        id=rid,
        state=state,
        policy_types=tuple(types),
        identification=ident,
        verified=verified,
        observed_result=object() if observed else None,
    )


def test_confidence_signals_are_separate():
    s = _settings(held_out=["VT"])
    doc = {
        "decision": {"outcome": "keep_current"},
        "county_cases": {"VT": 294, "PA": 1400},
        "holdout": {"out_of_time": {"VT": [{"method": "pa_blend", "coverage": 0.8, "cases": 10}]}},
    }
    ev = SimpleNamespace(
        records=[
            _rec("ca.x", "CA", ["adu"], "matched"),
            _rec("vt.y", "VT", ["adu", "missing_middle"], "not_applicable", observed=False),
            _rec("or.z", "OR", ["parking"], "descriptive"),
            _rec("tx.w", "TX", ["fees"], "descriptive", verified=False),
        ]
    )
    out = dp.confidence_signals("VT", donor_doc=doc, evidence=ev, settings=s)
    b = out["baseline_performance"]
    assert b["error_source"] == "pennsylvania_prior" and b["grade"] == "borrowed"
    assert b["held_out_coverage"] == 0.8 and "8 times" in b["sentence"]
    r = out["reform_evidence"]["by_reform_type"]
    assert r["adu"]["grade"] == "tested elsewhere" and r["adu"]["in_state"] == 1
    assert r["missing_middle"]["grade"] == "law on record"
    assert r["parking"]["grade"] == "observed elsewhere"
    assert r["fees"]["grade"] == "no records"
    pa = dp.confidence_signals("PA", donor_doc=doc, evidence=ev, settings=s)
    assert pa["baseline_performance"]["error_source"] == "own"
    adopted = dict(doc, decision={"outcome": "adopt"})
    vt = dp.confidence_signals("VT", donor_doc=adopted, evidence=ev, settings=s)
    assert vt["baseline_performance"]["error_source"] == "donor_pool"


def test_cli_registers_command():
    from visionhousing.cli import build_parser

    args = build_parser().parse_args(["validate-donor-pool", "--raw", "x"])
    assert args.command == "validate-donor-pool" and str(args.raw) == "x"
    assert isinstance(args, argparse.Namespace)


# --------------------------------------------------------------------------- second test

PREREG_SHA256 = "c657753f5bbd3b6580258e97a564f6664fc2cb583f04b9f300ab13c7c810f3f7"


def test_preregistered_block_is_unchanged():
    """The block's fingerprint is the one logged before any score was computed."""
    block = dp.load_settings()[dp.PREREG_KEY]
    assert dp.preregistration_sha256(block) == PREREG_SHA256
    changed = dict(block, width_margin=0.2)
    assert dp.preregistration_sha256(changed) != PREREG_SHA256


def test_preregistration_hash_in_results():
    import yaml

    doc = yaml.safe_load(dp.OUT_PATH.read_text(encoding="utf-8"))
    pre = doc[dp.PREREG_KEY]["preregistration"]
    assert pre["block"] == dp.PREREG_KEY and pre["sha256"] == PREREG_SHA256


def _fresh_inputs():
    counts = {"AA": 100, "BB": 200, "CC": 300, "DD": 2000, "EE": 50, "VT": 10, "DC": 5, "FF": 70}
    sizes = {"AA": 30.0, "BB": 10.0, "CC": 20.0, "DD": 1.0, "EE": 20.0, "VT": 1.0, "DC": 1.0,
             "FF": 99.0}  # fmt: skip
    s = {
        "states": ["FF", "EE", "DD", "CC", "BB", "AA", "VT", "DC"],
        "held_out": ["VT"],
        "full_weight_cases": 1000,
        dp.PREREG_KEY: {"exclude": ["DC"], "n_states": 4},
    }
    return counts, sizes, s


def test_select_fresh_states_rule():
    counts, sizes, s = _fresh_inputs()
    got = dp.select_fresh_states(counts, sizes, s)
    # VT used before, DC excluded, DD not sparse; smallest first, CC/EE tie by postal code
    assert got["states"] == ["BB", "CC", "EE", "AA"]
    assert got["dropped"] == {"first_test": ["VT"], "excluded": ["DC"], "not_sparse": ["DD"]}
    assert [r["state"] for r in got["eligible"]] == ["BB", "CC", "EE", "AA", "FF"]


def test_select_fresh_states_deterministic():
    counts, sizes, s = _fresh_inputs()
    first = dp.select_fresh_states(counts, sizes, s)
    rev = {k: counts[k] for k in reversed(list(counts))}
    rsz = {k: sizes[k] for k in reversed(list(sizes))}
    s2 = dict(s, states=list(reversed(s["states"])))
    assert dp.select_fresh_states(rev, rsz, s2) == first
    assert dp.select_fresh_states(counts, sizes, s) == first


def test_select_fresh_states_real_settings_avoid_first_test():
    s = dp.load_settings()
    counts = {st: 100 for st in s["states"]}
    sizes = {st: float(i) for i, st in enumerate(sorted(s["states"]))}
    got = dp.select_fresh_states(counts, sizes, s)
    test = s[dp.PREREG_KEY]
    assert len(got["states"]) == test["n_states"]
    assert not set(got["states"]) & (set(s["held_out"]) | set(test["exclude"]))


def _pooled_summary(cov_c, width_c, cov_k, width_k, n_states=6):
    rows = []
    for i in range(n_states):
        for m, cov, w in (("states_blend", cov_c, width_c), ("pa_blend", cov_k, width_k),
                          ("own", 0.5, 0.7), ("national_states", 0.7, 1.1)):  # fmt: skip
            rows.append({"variant": "out_of_time", "state": f"S{i}", "method": m,
                         "cases": 10, "coverage": cov, "width": w})  # fmt: skip
    return pd.DataFrame(rows)


def _decide2(*args, n_states=6):
    s = dp.load_settings()
    states = [f"S{i}" for i in range(n_states)]
    return dp.decide_pooled_states(_pooled_summary(*args, n_states=n_states), states, s)


def test_decide_pooled_states_rule():
    assert _decide2(0.78, 1.05, 0.6, 1.0)["outcome"] == "adopt"  # closer, within margin
    assert _decide2(0.80, 1.5, 0.6, 1.0)["outcome"] == "adopt"  # much wider but in band
    d = _decide2(0.95, 1.5, 0.6, 1.0)  # closer, much wider, above band
    assert d["outcome"] == "keep_current" and d["wider_than_margin"]
    d = _decide2(0.65, 1.5, 0.6, 1.0)  # closer, much wider, below band: clause as stated
    assert d["outcome"] == "keep_current" and d["coverage_below_band"]
    assert _decide2(0.6, 1.0, 0.8, 1.0)["outcome"] == "keep_current"  # not closer
    # tie on the rounded gap: only a strictly narrower candidate wins
    assert _decide2(0.7, 0.9, 0.9, 1.0)["outcome"] == "adopt"
    assert _decide2(0.7, 1.0, 0.9, 1.0)["outcome"] == "keep_current"
    assert _decide2(0.8, 1.0, 0.6, 1.0, n_states=5)["outcome"] == "inconclusive"


def test_pooled_states_study_on_synthetic_panel(cases):
    panel = synthetic_panel()
    s = _settings(held_out=["VT"], range_example_state="VT")
    s[dp.PREREG_KEY] = dict(s[dp.PREREG_KEY], exclude=[], n_states=3, min_states_scored=1)
    counts = {st: int(n) for st, n in cases.counties.groupby("state").size().items()}
    got = dp.pooled_states_study(cases, panel, counts, s, RULE)
    assert got["preregistration"]["sha256"] == dp.preregistration_sha256(s[dp.PREREG_KEY])
    assert got["fresh_states"] == ["NH", "OH", "PA"]  # by mean yearly units, smallest first
    assert got["decision"]["outcome"] in ("adopt", "keep_current")
    r = got["implied_range"]
    assert r["state"] == "VT" and r["years"] == [2027, 2031]
    for m in ("own", "pa_blend", "states_blend"):
        assert r[m]["low_units"] <= r["p50_units"] <= r[m]["high_units"]
    # blends only widen the state's own range
    assert r["states_blend"]["width"] >= r["own"]["width"] - 1e-9


# --------------------------------------------------------------------------- pre-registration check


def test_registry_matches_the_logged_fingerprints_and_todays_blocks():
    from visionhousing.validation import preregistration as pr

    reg = pr.load_registry()
    blocks = reg["blocks"]
    assert blocks[dp.PREREG_KEY]["sha256"] == PREREG_SHA256  # logged before its first score
    s = dp.load_settings()
    for name in (dp.RULES_BLOCK, dp.PREREG_KEY, "growth_coverage_test"):
        assert pr.verify(name, s, reg)["verified"] is True
    # the fingerprint is the one the results carry
    assert dp.preregistration_sha256(s[dp.PREREG_KEY]) == pr.fingerprint(s[dp.PREREG_KEY])
    # recorded honestly: after the first run
    assert reg["recorded_after_first_run"] is True


def test_run_study_refuses_to_score_a_changed_rule(monkeypatch):
    import copy

    from visionhousing.validation.preregistration import PreregistrationError

    def boom(*a, **k):
        raise AssertionError("scored before the check")

    monkeypatch.setattr(dp, "area_cases", boom)
    s = copy.deepcopy(dp.load_settings())
    s["decision"] = {**s["decision"], "note": "changed after the scores"}
    with pytest.raises(PreregistrationError, match="donor_pool_rules"):
        dp.run_study(synthetic_panel(1), s, BSET, RULE)
    s = copy.deepcopy(dp.load_settings())
    s[dp.PREREG_KEY]["width_margin"] = 0.2
    with pytest.raises(PreregistrationError, match=dp.PREREG_KEY):
        dp.run_study(synthetic_panel(1), s, BSET, RULE)


def test_results_carry_the_verified_fingerprints():
    import yaml

    doc = yaml.safe_load(dp.OUT_PATH.read_text(encoding="utf-8"))
    checks = {c["block"]: c for c in doc["preregistration_checks"]}
    assert set(checks) == {dp.RULES_BLOCK, dp.PREREG_KEY}
    assert all(c["verified"] for c in checks.values())
    assert checks[dp.PREREG_KEY]["sha256"] == PREREG_SHA256
