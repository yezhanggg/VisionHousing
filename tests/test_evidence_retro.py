"""Tests for the retrospective reform panel (src/visionhousing/evidence/retro.py).

Synthetic panels where the true effect is known: the estimators must recover it,
cover zero when there is none, and flag a panel whose treated units already
drifted before the reform. Plus the file parser, the timing rule, the pool rule,
the synthetic control and the identification label. All offline.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from visionhousing.evidence import retro
from visionhousing.evidence.schema import ComparisonRecord

YEARS = np.arange(2012, 2026)


def make_panel(
    *,
    n_pool: int = 120,
    cohorts: dict[int, int] | None = None,
    effect: float = 0.0,
    pre_slope: float = 0.0,
    noise: float = 0.3,
    seed: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Unit and year effects plus noise; treated units get ``effect`` from g on.

    ``pre_slope`` adds a treated-only trend in years before g (a pre-trend violation).
    """
    cohorts = cohorts if cohorts is not None else {2019: 10, 2021: 10, 2022: 10}
    rng = np.random.default_rng(seed)
    cohort = np.concatenate([np.zeros(n_pool, int), *[np.full(k, g) for g, k in cohorts.items()]])
    n = cohort.size
    unit = rng.normal(2.0, 0.5, n)[:, None]
    year = rng.normal(0, 0.5, YEARS.size)[None, :]
    y = unit + year + rng.normal(0, noise, (n, YEARS.size))
    for i in np.flatnonzero(cohort > 0):
        g = cohort[i]
        y[i, g <= YEARS] += effect
        if pre_slope:
            y[i, g > YEARS] += pre_slope * (YEARS[g > YEARS] - g)
    return y, cohort


PRE, POST = [-6, -5, -4], [0, 1, 2]


def test_pooled_att_recovered_within_tolerance():
    y, cohort = make_panel(effect=2.0)
    res = retro.pooled_estimate(y, YEARS, cohort, PRE + POST, POST, PRE, draws=99, seed=3)
    assert res["overall"] == pytest.approx(2.0, abs=0.2)
    assert res["ci"][0] < 2.0 < res["ci"][1]
    # No pre-trend was built in; the test rejects about 10% of such panels by design
    # (checked over 40 seeds), so only a very small p would be a fault here.
    assert res["pretrend_p"] >= 0.01


def test_zero_effect_interval_covers_zero():
    y, cohort = make_panel(effect=0.0, seed=5)
    res = retro.pooled_estimate(y, YEARS, cohort, PRE + POST, POST, PRE, draws=99, seed=3)
    assert res["ci"][0] < 0.0 < res["ci"][1]
    one = np.flatnonzero(cohort == 2019)[0]
    case = retro.case_estimates(y, YEARS, cohort, one, draws=19)
    assert case["ci"][0] < 0.0 < case["ci"][1]
    assert case["p_value"] > 0.10


def test_pretrend_detection_fires_on_violating_panel():
    y, cohort = make_panel(effect=0.0, pre_slope=0.4, seed=7)
    res = retro.pooled_estimate(y, YEARS, cohort, PRE + POST, POST, PRE, draws=99, seed=3)
    assert res["pretrend_p"] < 0.10
    one = np.flatnonzero(cohort == 2021)[0]
    case = retro.case_estimates(y, YEARS, cohort, one, draws=19)
    assert case["pretrend_p"] < 0.10
    label, reasons = retro.identification_label(
        case,
        {
            "pretrend_p_min": 0.1,
            "placebo_date_p_min": 0.1,
            "min_comparisons": 30,
            "donor_fit_max": 0.25,
        },
        min_post_years=2,
    )
    assert label == "matched" and any("pre-trend" in r for r in reasons)


def test_single_case_effect_and_placebo_interval():
    y, cohort = make_panel(cohorts={2020: 1}, effect=3.0, seed=11)
    unit = int(np.flatnonzero(cohort > 0)[0])
    case = retro.case_estimates(y, YEARS, cohort, unit, draws=19)
    assert case["att"] == pytest.approx(3.0, abs=0.6)
    assert case["ci"][0] > 0
    assert case["post_years"] == 5 and case["comparisons"] == 120


def test_not_yet_treated_units_are_controls_only_before_their_year():
    cohort = np.array([0, 0, 2020, 2023])
    assert list(retro._controls(cohort, 2020, 2021)) == [True, True, False, True]
    assert list(retro._controls(cohort, 2020, 2023)) == [True, True, False, False]


def test_placebo_in_space_matches_unit_effects():
    y, cohort = make_panel(cohorts={2020: 1}, seed=2, n_pool=15)
    plac = retro.placebo_in_space(y, YEARS, cohort, 2020, [0, 1], exclude=[15])
    fake = retro.unit_effects(y, YEARS, cohort, [3], [0, 1], fake_g={3: 2020})
    # Unit 3's placebo equals unit_effects with the same controls (pool minus itself).
    assert plac[3] == pytest.approx(fake[0])


def test_simplex_projection_and_synth_weights():
    w = retro.project_simplex(np.array([0.5, 2.0, -1.0]))
    assert w.sum() == pytest.approx(1.0) and (w >= 0).all()
    rng = np.random.default_rng(0)
    y0 = rng.normal(size=(10, 6))
    true = np.array([0.6, 0.4, 0, 0, 0, 0])
    got = retro.synth_weights(y0 @ true, y0, iters=20000)
    assert got == pytest.approx(true, abs=0.02)


def test_synth_control_reports_fit_and_gap():
    y, _ = make_panel(cohorts={2020: 1}, effect=2.0, seed=4, n_pool=40, noise=0.05)
    unit = 40
    sc = retro.synth_control(y, YEARS, unit, np.arange(40), 2020, placebos=True)
    assert sc["fit_ratio"] < 0.25
    assert sc["post_gap_mean"] == pytest.approx(2.0, abs=0.5)
    assert sc["placebo_rank_p"] <= 0.05


def test_treatment_year_rule():
    assert retro.treatment_year(date(2020, 1, 1)) == 2020
    assert retro.treatment_year(date(2021, 6, 30)) == 2021
    assert retro.treatment_year(date(2021, 8, 1)) == 2022


def test_identification_descriptive_when_too_few_post_years():
    est = {"post_years": 1, "comparisons": 80, "pretrend_p": 0.9, "placebo_date": {"p": 0.9}}
    th = {
        "pretrend_p_min": 0.1,
        "placebo_date_p_min": 0.1,
        "min_comparisons": 30,
        "donor_fit_max": 0.25,
    }
    assert retro.identification_label(est, th, min_post_years=2)[0] == "descriptive"
    est["post_years"] = 3
    assert retro.identification_label(est, th, min_post_years=2)[0] == "causal"
    assert retro.identification_label(est, th, min_post_years=2, donor_fit=0.5)[0] == "matched"


_IDS1 = (
    "Survey,State,6-Digit,County,Census Place,FIPS Place,FIPS MCD,Pop,CSA,CBSA,Footnote,"
    "Central,Zip,Region,Division,Number of,Place"
)
_IDS2 = "Date,Code,ID,Code,Code,Code,Code,  ,Code,Code,Code,City,Code,Code,Code,Months Rep,Name"
_GROUPS = ["1-unit", "2-units", "3-4 units", "5+ units"]
_HEAD1 = _IDS1 + "," + ",".join(f"{g},," for g in _GROUPS + [f"{g} rep" for g in _GROUPS])
_HEAD2 = _IDS2 + ",Bldgs,Units,Value" * 8
_MPLS = "79,79,1,15,30,1,7,24,1,34,3207,1"
_ARL = "205,205,1,0,0,0,0,0,0,4,1672,1"
PLACE_FILE = "\n".join(
    [
        _HEAD1.rstrip(","),
        _HEAD2,
        " ",
        "2020,27,497800,053,2585,43000 ,43000 ,382578 ,378,33460, ,1,55415      ,2,4,12,"
        f"Minneapolis,{_MPLS},{_MPLS}",
        "2020,51,109000,013,    ,00000 ,00000 ,240119 ,548,47900, ,1,22201      ,3,5,12,"
        f"Arlington County,{_ARL},{_ARL}",
    ]
)


def test_parse_place_annual():
    df = retro.parse_place_annual(PLACE_FILE, 2020)
    mpls = df[df["name"] == "Minneapolis"].iloc[0]
    assert mpls["place_fips"] == "43000" and mpls["state_fips"] == "27"
    assert (mpls["units_1"], mpls["units_2"], mpls["units_3_4"], mpls["units_5plus"]) == (
        79,
        30,
        24,
        3207,
    )
    assert mpls["months_rep"] == 12
    arl = df[df["name"] == "Arlington County"].iloc[0]
    assert retro._office_key(arl["state_fips"], arl["place_fips"], arl["county_fips"]) == "51c013"
    with pytest.raises(ValueError):
        retro.parse_place_annual(PLACE_FILE, 2021)


def _record(state: str, types: list[str], eff: str, juris: str = "statewide") -> ComparisonRecord:
    return ComparisonRecord.model_validate(
        {
            "id": f"{state.lower()}.x_{types[0]}",
            "kind": "policy",
            "state": state,
            "jurisdiction": juris,
            "policy_types": types,
            "title": "A law",
            "citation": "Act 1",
            "url": "https://example.gov/a",
            "effective_dates": [{"date": eff, "applies_to": "all"}],
            "geographic_scope": "statewide",
            "implementation_status": "legally_effective",
            "identification": "not_applicable",
            "retrieved": "2026-11-24",
            "verified": True,
            "sources": [{"label": "law", "url": "https://example.gov/a"}],
        }
    )


def test_excluded_states_rule():
    design = {
        "comparison_pool": {"excluded_states_extra": {"CA": "why"}, "law_in_force_by": "2025-12-31"}
    }
    recs = [
        _record("WA", ["missing_middle"], "2023-07-23"),
        _record("NC", ["adu"], "2027-01-15"),  # after 2025: stays in the pool
        _record("TX", ["parking"], "2024-01-01"),  # wrong type
        _record("MN", ["missing_middle"], "2020-01-01", juris="Minneapolis"),  # local
    ]
    assert retro.excluded_states(design, recs) == {"CA", "WA"}
    # the cut-off is the design's, not the code's
    later = {"comparison_pool": {**design["comparison_pool"], "law_in_force_by": date(2027, 6, 1)}}
    assert retro.excluded_states(later, recs) == {"CA", "WA", "NC"}
    assert retro.load_design()["comparison_pool"]["law_in_force_by"] == date(2025, 12, 31)


def test_check_results_verifies_the_design_fingerprints(tmp_path):
    import shutil

    from visionhousing.validation.preregistration import PreregistrationError, verify

    design = tmp_path / "design.yaml"
    shutil.copy(retro.DESIGN_PATH, design)
    assert retro.check_results(retro.RESULTS_PATH, design) == []
    # a comment changes the file's bytes (results are stale) but not the registered values
    design.write_text(design.read_text(encoding="utf-8") + "# note\n", encoding="utf-8")
    problems = retro.check_results(retro.RESULTS_PATH, design)
    assert len(problems) == 1 and "design_sha256" in problems[0]
    # a changed rule fails both checks, and a new run refuses to score
    text = retro.DESIGN_PATH.read_text(encoding="utf-8").replace(
        "min_comparisons: 30", "min_comparisons: 20"
    )
    design.write_text(text, encoding="utf-8")
    problems = retro.check_results(retro.RESULTS_PATH, design)
    assert len(problems) == 2 and "Refusing to score" in problems[1]
    with pytest.raises(PreregistrationError):
        verify(retro.PREREG_BLOCK, retro.load_design(design))


def test_run_refuses_a_changed_design(monkeypatch, tmp_path):
    import argparse

    from visionhousing.validation.preregistration import PreregistrationError

    changed = retro.load_design()
    changed["thresholds"]["pretrend_p_min"] = 0.05
    monkeypatch.setattr(retro, "load_design", lambda *a, **k: changed)
    monkeypatch.setattr(retro, "load_panel", lambda: pytest.fail("scored before the check"))
    args = argparse.Namespace(action="run", raw=tmp_path, out=tmp_path / "r.yaml", draws=None)
    with pytest.raises(PreregistrationError):
        retro.run(args)


def test_build_panel_pool_rule_and_rates():
    design = {
        "data": {"years": [2018, 2021]},
        "comparison_pool": {
            "min_population": 100000,
            "population_year": 2019,
            "min_full_report_years": 3,
            "excluded_local_reformers": [{"state_fips": "48", "place_fips": "35000"}],
        },
        "cases": [
            {
                "record": "mn.case",
                "name": "Case",
                "g": 2020,
                "effective_date": date(2020, 1, 1),
                "office": {"state_fips": "27", "place_fips": "43000"},
            }
        ],
    }
    rows = []
    offices = [
        ("27", "43000", "Case", 400000),
        ("17", "14000", "Big", 2_000_000),
        ("48", "35000", "Reformer", 2_000_000),
        ("17", "00100", "Small", 5000),
        ("06", "44000", "Excluded", 3_000_000),
    ]
    for y in range(2018, 2022):
        for s, p, name, pop in offices:
            rows.append(
                {
                    "year": y,
                    "state_fips": s,
                    "office_id": "000001",
                    "county_fips": "001",
                    "place_fips": p,
                    "name": name,
                    "pop": pop,
                    "months_rep": 12,
                    "units_1": 10,
                    "units_2": 2,
                    "units_3_4": 4,
                    "units_5plus": 0,
                    "reported_units": 16,
                }
            )
    housing = pd.DataFrame(
        {
            "level": "place",
            "state_fips": [o[0] for o in offices],
            "code": [o[1] for o in offices],
            "housing_units": [2000.0] * len(offices),
        }
    )
    panel, summary = retro.build_panel(pd.DataFrame(rows), housing, design, {"CA"})
    assert panel.names == ["Big", "Case"] or set(panel.names) == {"Big", "Case"}
    assert summary["removed_by_rule"] == {"local_reformer": 1, "population": 1, "excluded_state": 1}
    case = panel.names.index("Case")
    assert panel.cohort[case] == 2020
    assert panel.rates["units_2_4"][case, 0] == pytest.approx(3.0)  # 6 units / 2,000 homes x 1,000


def test_repository_results_validate():
    if not retro.RESULTS_PATH.exists():
        pytest.skip("results not generated")
    assert retro.check_results() == []
    import yaml

    data = yaml.safe_load(retro.RESULTS_PATH.read_text(encoding="utf-8"))
    design = retro.load_design()
    assert data["provenance"]["design_file"].endswith("retro_design.yaml")
    assert {r["identification"] for r in data["records"]} <= {"descriptive", "matched", "causal"}
    assert len(data["records"]) == len(design["cases"])
    assert data["how_to_use"] and any("multiplier" in s for s in data["how_to_use"])
