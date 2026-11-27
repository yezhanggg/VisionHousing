"""Nested rolling-origin selection: choose the baseline rule with only what was known then.

Plain-language summary
----------------------
The baseline contest picks each state's projection rule using every past year,
including the years later used to score it, and it describes counties with
today's place types. That makes the past "share caught" somewhat favourable.
This study asks how much: at each past start year (origin) ``o`` it

1. keeps only the past cases whose five target years had all been observed by
   ``o`` (*matured*: ``o' + lead + horizon - 1 <= o``, with the forecast's own
   lead and horizon, so the information gap is the forecast's);
2. reruns the contest on those cases alone, describing counties by permit-only
   size classes as they stood at ``o`` (no typology, no tract homes), and lets
   the contest's own choosing rule pick a rule;
3. scores that rule's state and county ranges at ``o`` exactly as the measured
   reliability study does (:mod:`visionhousing.baseline.reliability`).

Three arms are scored at the same start years (block ``nested_selection`` in
``assumptions/validation_nested_selection.yaml``, fingerprint in
``assumptions/preregistration.yaml``, written before any score):

* ``full_record``: the shipped rule with today's place info (its state counts
  must equal the reliability study's: a parity check);
* ``full_record_permit_only``: the shipped rule with permit-only place info at
  ``o`` (the effect of present-day place types alone);
* ``nested``: the rule chosen at ``o`` from matured cases only.

The bias-correction choice, the small-sample prior and the growth-aware
widening stay as chosen on the full record in every arm; the results say so.
Nothing here changes an estimate: it is descriptive.

``visionhousing validate-nested-selection`` (permits only; light) writes
``assumptions/calibration/nested_selection.yaml``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    lead_years,
    leave_one_county_out,
    origin_size_classes,
    ratio_quantiles,
    size_classes,
    validate_baseline,
)
from visionhousing.baseline.panel import Panel, to_panel
from visionhousing.baseline.rules import PlaceInfo, Rule
from visionhousing.ledger.loader import ROOT

__all__ = [
    "BLOCK",
    "RESULTS_PATH",
    "SETTINGS_PATH",
    "add_parser",
    "command",
    "nested_pick",
    "permit_only_info",
    "run_study",
    "state_rows",
    "state_study",
    "write_results",
]

BLOCK = "nested_selection"
SETTINGS_PATH = ROOT / "assumptions" / "validation_nested_selection.yaml"
RESULTS_PATH = ROOT / "assumptions" / "calibration" / "nested_selection.yaml"


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Read the study's settings file (the pre-registered block lives in it)."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def permit_only_info(panel: Panel, origin: int, recent_window: int, n_classes: int) -> PlaceInfo:
    """Place info known at ``origin`` from permits alone: size classes through it, no homes."""
    from visionhousing.baseline.contest import place_info

    classes = size_classes(panel, recent_window, n_classes, through=int(origin))
    return place_info(panel.counties, classes, n_classes, ["size_class"])


def matured(origins: Sequence[int], origin: int, lead: int, horizon: int) -> list[int]:
    """Origins whose five target years had all been observed by ``origin``."""
    return [int(o) for o in origins if int(o) + lead + horizon - 1 <= int(origin)]


def state_rows(
    rule: Rule,
    years: np.ndarray,
    state_units: np.ndarray,
    county_cases: pd.DataFrame,
    origins: Sequence[int],
    lead: int,
    horizon: int,
    need: int,
    *,
    levels: Sequence[float] = (0.1, 0.5, 0.9),
    keep_correction: bool = True,
    small_sample: Mapping[str, Any] | None = None,
    growth: Mapping[str, Any] | None = None,
) -> dict[int, tuple[float, float, float, float]]:
    """The state range rebuilt at each origin from matured state cases (reliability's method).

    Returns:
        Origin -> ``(actual, P50, P10, P90)`` for each scored origin.
    """
    from visionhousing.baseline.project import widen_growth_class, widen_small_sample

    cases = backtest_cases(rule, years, state_units, origins, lead, horizon)
    cases = cases[np.isfinite(cases["ratio"])]
    org = cases["origin"].to_numpy(dtype=int)
    c_org = county_cases["origin"].to_numpy(dtype=int)
    c_ok = np.isfinite(county_cases["ratio"].to_numpy(dtype=float))
    out: dict[int, tuple[float, float, float, float]] = {}
    for _, case in cases.iterrows():
        o = int(case["origin"])
        past = cases[org + lead + horizon - 1 <= o]
        if len(past) < need or case["actual"] <= 0 or case["projected"] <= 0:
            continue
        q = ratio_quantiles(past["ratio"].to_numpy(dtype=float), levels)
        if q is None or not q[1] > 0 or not q[0] > 0:
            continue
        low, high = math.log(q[0] / q[1]), math.log(q[2] / q[1])
        n = int((c_ok & (c_org + lead + horizon - 1 <= o)).sum())
        low, high, _ = widen_small_sample(low, high, n, levels, small_sample)
        if growth:
            low, high, _ = widen_growth_class(low, high, n, growth.get("entry"), growth)
        proj = float(case["projected"])
        p50 = proj * float(q[1]) if keep_correction else proj
        out[o] = (float(case["actual"]), p50, p50 * math.exp(low), p50 * math.exp(high))
    return out


def nested_pick(
    table: pd.DataFrame,
    candidates: Sequence[Rule],
    origins: Sequence[int],
    origin: int,
    settings: Any,
    info: PlaceInfo,
    band: Sequence[float],
) -> tuple[str | None, str, list[int]]:
    """Rerun the contest on cases matured by ``origin`` only and apply its choosing rule.

    Returns:
        ``(rule name or None, reason, selection origins)``.
    """
    from visionhousing.baseline.contest import choose_winner

    panel = to_panel(table)
    lead = lead_years(panel, settings)
    sel = matured(origins, origin, lead, settings.horizon)
    if not sel:
        return None, "no matured cases", sel
    skill = validate_baseline(table, candidates, sel, settings, info)
    try:
        name, reason = choose_winner(skill, band, settings.tolerance)
    except ValueError as exc:
        return None, str(exc), sel
    return name, reason, sel


def _summary(rows: Mapping[int, tuple[float, float, float, float]]) -> dict[str, Any]:
    if not rows:
        return {"cases": 0, "covered": 0, "coverage": math.nan, "width": math.nan,
                "median_abs_log_error": math.nan}  # fmt: skip
    arr = np.array(list(rows.values()), dtype=float)
    inside = (arr[:, 0] >= arr[:, 2]) & (arr[:, 0] <= arr[:, 3])
    return {
        "cases": int(inside.size),
        "covered": int(inside.sum()),
        "coverage": float(inside.mean()),
        "width": float(np.mean((arr[:, 3] - arr[:, 2]) / arr[:, 0])),
        "median_abs_log_error": float(np.median(np.abs(np.log(arr[:, 0] / arr[:, 1])))),
    }


def _county_inside(
    cases: pd.DataFrame, classes: Mapping[int, np.ndarray], origin: int, lead: int, horizon: int
) -> np.ndarray:
    scored = leave_one_county_out(cases, classes, lead, horizon)
    now = scored[scored["origin"].to_numpy(dtype=int) == int(origin)]
    a = now["actual"].to_numpy(dtype=float)
    return (a >= now["low"].to_numpy(dtype=float)) & (a <= now["high"].to_numpy(dtype=float))


def state_study(
    state: str,
    table: pd.DataFrame,
    ledger: Any,
    rules: Mapping[str, Any],
    info: PlaceInfo | None,
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """Score the three arms for one state (see the module docstring)."""
    from visionhousing.baseline.contest import run_contest
    from visionhousing.baseline.project import growth_widen_for
    from visionhousing.baseline.reliability import state_reliability
    from visionhousing.baseline.settings import settings_from_ledger

    settings = settings_from_ledger(ledger)
    contest = run_contest(table, ledger, rules, info)
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    horizon = settings.horizon
    origins = backtest_origins(panel.years, lead, horizon, settings.min_history)
    classes = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    need = int(block["min_past_origins"])
    keep = bool(contest.correction.get("keep", True))
    small = rules.get("small_sample")
    growth = growth_widen_for(state, ledger, rules, int(panel.years[-1]))
    state_units = panel.state_series()
    band = tuple(float(x) for x in rules["contest"]["coverage_band"])
    by_name = {r.name: r for r in contest.rules}
    shipped = contest.rule

    case_cache: dict[tuple[str, Any], pd.DataFrame] = {}

    def cases_for(rule: Rule, key: Any, pinfo: PlaceInfo) -> pd.DataFrame:
        k = (rule.name, key)
        if k not in case_cache:
            case_cache[k] = backtest_cases(
                rule, panel.years, panel.units, origins, lead, horizon, pinfo
            )
        return case_cache[k]

    def rows_for(rule: Rule, cases: pd.DataFrame) -> dict[int, tuple[float, float, float, float]]:
        return state_rows(
            rule, panel.years, state_units, cases, origins, lead, horizon, need,
            keep_correction=keep, small_sample=small, growth=growth,
        )  # fmt: skip

    # Arm full_record: the shipped rule, today's place info.
    shipped_cases = cases_for(shipped, "shipped", contest.info)
    shipped_rows = rows_for(shipped, shipped_cases)
    eval_origins = sorted(shipped_rows)
    parity_ref = state_reliability(
        shipped, panel.years, state_units, shipped_cases, origins, lead, horizon,
        {"min_past_origins": need}, keep_correction=keep, small_sample=small, growth=growth,
    )  # fmt: skip

    arms: dict[str, dict[str, Any]] = {
        a: {"state_rows": {}, "county": [], "rules": {}, "unscored": []} for a in block["arms"]
    }
    audit = []
    for o in eval_origins:
        pinfo = permit_only_info(panel, o, settings.recent_window, settings.n_classes)
        picked, reason, sel = nested_pick(table, contest.rules, origins, o, settings, pinfo, band)
        last_sel_target = (max(sel) + lead + horizon - 1) if sel else None
        audit.append(
            {
                "origin": o,
                "selection_origins": [min(sel), max(sel)] if sel else [],
                "selection_last_target_year": last_sel_target,
                "selection_matured": last_sel_target is None or last_sel_target <= o,
                "evaluation_target_years": [o + lead, o + lead + horizon - 1],
                "nested_rule": picked,
                "nested_reason": reason,
            }
        )
        plan = {
            "full_record": (shipped, "shipped", contest.info),
            "full_record_permit_only": (shipped, ("permit", o), pinfo),
            "nested": (by_name.get(picked) if picked else None, ("permit", o), pinfo),
        }
        for arm, (rule, key, pi) in plan.items():
            if arm not in arms:
                continue
            if rule is None:
                arms[arm]["unscored"].append(o)
                continue
            cases = cases_for(rule, key, pi)
            row = shipped_rows.get(o) if arm == "full_record" else rows_for(rule, cases).get(o)
            arms[arm]["rules"][o] = rule.name
            if row is None:
                arms[arm]["unscored"].append(o)
            else:
                arms[arm]["state_rows"][o] = row
            arms[arm]["county"].append(_county_inside(cases, classes, o, lead, horizon))

    out_arms = {}
    for arm, a in arms.items():
        inside = np.concatenate(a["county"]) if a["county"] else np.array([], dtype=bool)
        out_arms[arm] = {
            "state_range": {
                **_summary(a["state_rows"]),
                "by_origin": {
                    int(o): {
                        "actual": r[0],
                        "p10": r[2],
                        "p50": r[1],
                        "p90": r[3],
                        "caught": bool(r[2] <= r[0] <= r[3]),
                    }
                    for o, r in a["state_rows"].items()
                },
            },
            "county_ranges": {
                "cases": int(inside.size),
                "covered": int(inside.sum()),
                "coverage": float(inside.mean()) if inside.size else math.nan,
            },
            "rule_by_origin": {int(o): n for o, n in a["rules"].items()},
            "unscored_origins": [int(o) for o in a["unscored"]],
        }
    nested_rules = out_arms.get("nested", {}).get("rule_by_origin", {})
    agree = sum(1 for n in nested_rules.values() if n == shipped.name)
    full = out_arms["full_record"]["state_range"]
    return {
        "shipped_rule": shipped.name,
        "bias_correction_kept": keep,
        "growth_widening": bool(growth),
        "evaluation_origins": [int(o) for o in eval_origins],
        "lead": int(lead),
        "horizon": int(horizon),
        "arms": out_arms,
        "agreement_with_shipped": {"same_rule": agree, "origins": len(nested_rules)},
        "parity": {
            "reliability_covered": int(parity_ref["covered"]),
            "reliability_cases": int(parity_ref["cases"]),
            "full_record_covered": int(full["covered"]),
            "full_record_cases": int(full["cases"]),
            "match": int(parity_ref["covered"]) == int(full["covered"])
            and int(parity_ref["cases"]) == int(full["cases"]),
        },
        "fold_audit": audit,
        "fold_audit_ok": all(r["selection_matured"] for r in audit),
    }


def run_study(
    states: Sequence[str] | None = None,
    *,
    settings_doc: Mapping[str, Any] | None = None,
    rules: Mapping[str, Any] | None = None,
    ledger: Any = None,
    inputs: Mapping[str, tuple[pd.DataFrame, PlaceInfo]] | None = None,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score the nested selection test; refuses to run if the block has changed."""
    from visionhousing.baseline.calibrate import _clean, _state_inputs
    from visionhousing.baseline.places import load_place_rules
    from visionhousing.baseline.settings import settings_from_ledger
    from visionhousing.ledger import load_ledger
    from visionhousing.validation.preregistration import verify

    doc = settings_doc if settings_doc is not None else load_settings()
    checked = verify(BLOCK, doc, registry)
    block = doc[BLOCK]
    rules = rules if rules is not None else load_place_rules()
    ledger = ledger if ledger is not None else load_ledger()
    settings = settings_from_ledger(ledger)
    states = [str(s).upper() for s in (states or block["states"])]
    per_state = {}
    for s in states:
        table, info = (inputs or {}).get(s) or _state_inputs(s, ledger, settings)
        per_state[s] = state_study(s, table, ledger, rules, info, block)
    return _clean(
        {
            "version": 1,
            "id": "nested_selection_2026_11",
            "label": "Nested rolling-origin selection of the baseline rule (out of time)",
            "status": "validation",
            "use": (
                "Descriptive. Shows how the baseline's past share caught changes when the "
                "rule is chosen at each start year from matured cases only. Changes no "
                "estimate. Method: docs/modules/validation_nested_selection.md."
            ),
            "provenance": {
                "method": (
                    "Census Building Permits Survey county files in each state pack; "
                    "visionhousing.validation.nested_selection; block nested_selection in "
                    "assumptions/validation_nested_selection.yaml."
                ),
                "generated_by": "visionhousing validate-nested-selection",
                "sources": [
                    {
                        "id": "bps_county_annual",
                        "citation": "U.S. Census Bureau, Building Permits Survey, county "
                        "annual files",
                        "url": "https://www2.census.gov/econ/bps/County/",
                        "licence": "Public domain (U.S. government work)",
                    }
                ],
                "limits": [
                    "The bias-correction choice, the small-sample prior and the growth-aware "
                    "widening were set on the full record and are held in every arm.",
                    "Start years one year apart share most of their target years, so the "
                    "counts rest on few independent periods.",
                    "Permit-only place info drops the typology and tract homes; it is not a "
                    "rebuilt historical typology.",
                ],
            },
            "preregistration": checked,
            "states": per_state,
        }
    )


HEADER = """\
# Calibration (validation): nested rolling-origin selection of the baseline rule
# (generated; do not edit). Written by `visionhousing validate-nested-selection`
# from the Census Building Permits Survey county files in the state packs.
# Descriptive (block nested_selection in assumptions/validation_nested_selection.yaml);
# changes no estimate. Method: docs/modules/validation_nested_selection.md.
"""


def write_results(doc: Mapping[str, Any], path: Path = RESULTS_PATH) -> Path:
    """Write the results file (header, then YAML)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + text, encoding="utf-8")
    return path


def add_parser(sub: Any) -> None:
    """Add ``validate-nested-selection`` to the command line."""
    p = sub.add_parser(
        "validate-nested-selection",
        help="Choose the baseline rule at each past start year from matured cases only; score it",
    )
    p.add_argument("--states", nargs="*", default=None, help="states (default: the block's)")
    p.add_argument("--out", type=Path, default=RESULTS_PATH, help="results file")


def command(args: Any) -> int:
    """Run the study, print a line per state and arm, and write the results file."""
    doc = run_study(args.states)
    for s, st in doc["states"].items():
        for arm, a in st["arms"].items():
            sr, cr = a["state_range"], a["county_ranges"]
            print(
                f"{s} {arm}: state caught {sr['covered']} of {sr['cases']}; "
                f"counties {cr['covered']} of {cr['cases']}"
            )
        ag = st["agreement_with_shipped"]
        print(f"{s}: nested pick = shipped rule at {ag['same_rule']} of {ag['origins']} origins")
    print(f"wrote {write_results(doc, args.out)}")
    return 0
