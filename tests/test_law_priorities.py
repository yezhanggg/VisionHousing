"""Review-priority scoring: the formula, its parts, and that it is deterministic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from visionhousing.law import priorities

PARAMS = {
    "target_lot_coverage": 0.5,
    "switch_floor": 0.1,
    "effort": {
        "own_pdf_text": 1.0,
        "own_html": 1.0,
        "own_pdf_scan": 3.0,
        "not_searched": 2.0,
        "commercial_only": None,
        "not_general_zoning": None,
    },
}
HOOKS = {"min_coverage": 0.5}

# County 99001: a read city (5,000 residents), two unread towns and two municipalities
# zoned by the county. County 99003: one unread municipality and one with no zoning.
MUNIS = pd.DataFrame(
    [
        {"county": "99001", "cousub": "00100", "name": "City", "zoning_regime": "own",
         "population": 5000},
        {"county": "99001", "cousub": "00200", "name": "Town A", "zoning_regime": "own",
         "population": 3000},
        {"county": "99001", "cousub": "00300", "name": "Town B", "zoning_regime": "own",
         "population": 1000},
        {"county": "99001", "cousub": "00400", "name": "Rural C", "zoning_regime": "county",
         "population": 600},
        {"county": "99001", "cousub": "00500", "name": "Rural D", "zoning_regime": "county",
         "population": 400},
        {"county": "99003", "cousub": "00600", "name": "Lake E", "zoning_regime": "own",
         "population": 2000},
        {"county": "99003", "cousub": "00700", "name": "Hill F", "zoning_regime": "none",
         "population": 500},
    ]
)  # fmt: skip
LOTS = {"00100": 1000, "00200": 800, "00300": 300, "00400": 200, "00500": 100, "00600": 600,
        "00700": 150}  # fmt: skip
RULES = pd.DataFrame(
    [
        {
            "geoid": ("99003" if c in ("00600", "00700") else "99001") + c,
            "status": "verified" if c == "00100" else "draft",
            "adopted_on": "2019",
            "reviewed_on": "2026-11-01",  # seen in force before the run's as-of date
            "sf_lots": n,
        }
        for c, n in LOTS.items()
    ]
)


def _rank(rules=RULES, **kw):
    args = {"params": PARAMS, "hooks": HOOKS, "sources": {}, **kw}
    return priorities.review_priorities(rules, MUNIS, **args).set_index("unit_id")


def test_units_group_county_zoning_and_skip_read_and_unzoned():
    r = _rank()
    assert set(r.index) == {"9900100200", "9900100300", "county:99001", "9900300600"}
    c = r.loc["county:99001"]
    assert c["municipalities"] == 2 and c["unknown_lots"] == pytest.approx(300)
    assert c["residents"] == 1000


def test_switch_factor_counts_the_share_of_missing_residents():
    r = _rank()
    # county 99001: 10,000 residents, 5,000 read = exactly the bar, so every unit counts fully
    assert r.loc["9900100200", "switch_factor"] == pytest.approx(1.0)
    # county 99003: 2,500 residents, none read; the bar needs 1,250; Lake E has 2,000
    assert r.loc["9900300600", "switch_factor"] == pytest.approx(1.0)
    rules = RULES.assign(status="draft")
    r = _rank(rules=rules)
    # now 99001 needs 5,000: Town A (3,000) closes 60% of the gap; Town B 20%
    assert r.loc["9900100200", "switch_factor"] == pytest.approx(0.6)
    assert r.loc["9900100300", "switch_factor"] == pytest.approx(0.2)
    assert r.loc["county:99001", "switch_factor"] == pytest.approx(0.2)


def test_score_is_lots_times_sensitivity_times_representativeness_over_effort():
    sources = {"9900100200": {"effort": "own_pdf_scan", "url": "https://a.example/z.pdf"}}
    r = _rank(sources=sources)
    a = r.loc["9900100200"]
    assert a["effort"] == 3.0 and a["effort_basis"] == "own_pdf_scan"
    assert a["source_url"] == "https://a.example/z.pdf"
    expected = a["unknown_lots"] * a["sensitivity"] * a["representativeness"] / a["effort"]
    assert a["score"] == pytest.approx(expected, rel=1e-3)
    assert r.loc["9900100300", "effort"] == 2.0  # not searched yet


def test_commercial_only_units_score_zero():
    r = _rank(sources={"9900100200": {"effort": "commercial_only"}})
    assert r.loc["9900100200", "score"] == 0
    assert r["rank"].max() == len(r)
    assert r.sort_values("rank").index[-1] == "9900100200"


def test_sensitivity_uses_the_adu_run_yield_ratio():
    # The state's own P50 (280) is not the sum of the county P50s (300): it is used.
    run = {"reform_results": {"adu": {"added": {"p50": 280.0},
                                      "counties": {"99001": {"p50": 240.0},
                                                   "99003": {"p50": 60.0}}}}}  # fmt: skip
    r = _rank(adu_run=run)
    # 99001: 240 / 2,400 lots = 0.1; 99003: 60 / 750 = 0.08; state 280 / 3,150
    state = 280 / 3150
    assert r.loc["9900100200", "yield_ratio"] == pytest.approx(0.1 / state, rel=1e-3)
    assert r.loc["9900300600", "yield_ratio"] == pytest.approx(0.08 / state, rel=1e-3)
    assert r.loc["9900100200", "sensitivity_source"].startswith("ADU run")
    assert _rank().loc["9900100200", "yield_ratio"] == 1.0


def test_representativeness_rises_where_the_place_type_is_thinly_read():
    typ = pd.DataFrame(
        [{"geoid": "99001000100", "type_name": "Urban"},
         {"geoid": "99001000200", "type_name": "Rural"},
         {"geoid": "99003000300", "type_name": "Rural"}]
    )  # fmt: skip
    tm = pd.DataFrame(
        [{"tract": "99001000100", "county": "99001", "cousub": "00100", "housing_units_2020": 900},
         {"tract": "99001000100", "county": "99001", "cousub": "00200", "housing_units_2020": 700},
         {"tract": "99001000200", "county": "99001", "cousub": "00300", "housing_units_2020": 200},
         {"tract": "99001000200", "county": "99001", "cousub": "00400", "housing_units_2020": 100},
         {"tract": "99001000200", "county": "99001", "cousub": "00500", "housing_units_2020": 50},
         {"tract": "99003000300", "county": "99003", "cousub": "00600", "housing_units_2020": 500},
         {"tract": "99003000300", "county": "99003", "cousub": "00700", "housing_units_2020": 100}]
    )  # fmt: skip
    r = _rank(typology_tract=typ, tract_munis=tm)
    # Urban: 1,000 of 1,800 lots read (56%) -> above the 50% target -> 1.0
    assert r.loc["9900100200", "place_type"] == "Urban"
    assert r.loc["9900100200", "representativeness"] == pytest.approx(1.0)
    # Rural: nothing read -> 2.0
    assert r.loc["9900100300", "representativeness"] == pytest.approx(2.0)


def test_ranking_is_deterministic_and_ties_break_by_unit_id():
    a = priorities.review_priorities(RULES, MUNIS, params=PARAMS, hooks=HOOKS, sources={})
    b = priorities.review_priorities(
        RULES.sample(frac=1, random_state=3), MUNIS.sample(frac=1, random_state=5),
        params=PARAMS, hooks=HOOKS, sources={},
    )  # fmt: skip
    pd.testing.assert_frame_equal(a, b)
    tie = RULES.assign(sf_lots=100)
    r = priorities.review_priorities(tie, MUNIS.assign(population=1000), params=PARAMS,
                                     hooks=HOOKS, sources={})  # fmt: skip
    same = r[r["score"] == r["score"].iloc[0]]
    assert list(same["unit_id"]) == sorted(same["unit_id"])


def test_shipped_parameters_and_sources_load():
    p = priorities.load_priorities()
    assert p["effort"]["not_searched"] > 0 and p["effort"]["commercial_only"] is None
    for unit, s in priorities.load_review_sources().items():
        assert s["effort"] in p["effort"], unit


def test_verified_rows_not_in_force_stay_on_the_list():
    # The city is verified but has no date and no reading date: unread by the in-force
    # rule, so it is ranked.
    undated = RULES.assign(adopted_on="", reviewed_on="")
    r = _rank(rules=undated, as_of="2026-11-24")
    assert "9900100100" in r.index
    assert r.loc["9900100100", "note"].startswith("verified but not in force")
    # A verified rule taking effect after the as-of date is not read yet either.
    later = RULES.assign(adopted_on="2019", effective_date="2027-01-01")
    assert "9900100100" in _rank(rules=later, as_of="2026-11-24").index
    # Dated and in force: done, not ranked.
    assert "9900100100" not in _rank(as_of="2026-11-24").index


def test_adu_yield_ratio_is_one_where_lots_or_the_state_are_zero():
    lots = pd.Series({"99001": 2400.0, "99003": 0.0})
    run = {"reform_results": {"adu": {"added": {"p50": 280.0},
                                      "counties": {"99001": {"p50": 240.0},
                                                   "99003": {"p50": 60.0}}}}}  # fmt: skip
    ratio, label = priorities.adu_yield_ratio(run, lots)
    assert ratio["99003"] == 1.0 and np.isfinite(ratio).all()
    assert ratio["99001"] == pytest.approx((240 / 2400) / (280 / 2400))
    assert label.startswith("ADU run")
    for bad in (
        {**run["reform_results"]["adu"], "added": {"p50": 0.0}},
        {k: v for k, v in run["reform_results"]["adu"].items() if k != "added"},
    ):
        ratio, label = priorities.adu_yield_ratio({"reform_results": {"adu": bad}}, lots)
        assert (ratio == 1.0).all() and label.startswith("none")
    ratio, _ = priorities.adu_yield_ratio(run, pd.Series({"99001": 0.0, "99003": 0.0}))
    assert (ratio == 1.0).all()


def test_min_coverage_comes_from_the_assumptions(monkeypatch, tmp_path):
    from visionhousing.law import hooks, inforce

    assert inforce.hook_value(None, "min_coverage") == hooks.load_hooks()["min_coverage"]
    assert inforce.hook_value({"min_coverage": 0.7}, "min_coverage") == 0.7
    empty = tmp_path / "hooks.yaml"
    empty.write_text("other: 1\n", encoding="utf-8")
    monkeypatch.setattr(hooks, "HOOKS_PATH", empty)
    with pytest.raises(KeyError, match="min_coverage"):
        inforce.hook_value({}, "min_coverage")
