"""Out-of-time calibrated baseline ranges: past errors set the range, tested on later years.

Plain-language summary
----------------------
The baseline's likely range (P10 to P90) is meant to catch the real five-year
total in about 8 of 10 cases. Once the back-test stopped borrowing errors from
later years, every rule's range caught it only about 4 to 6 times in 10: the
ranges were too narrow.

This module tests a simple fix that was written down before it was scored
(block ``oot_calibration`` in ``assumptions/backtest.yaml``, fingerprint in
``assumptions/preregistration.yaml``):

1. **Past cases only.** For a start year (origin) ``o``, use the rule's county
   cases whose five target years had all been observed by ``o``
   (``o' + lead + horizon - 1 <= o``). The county's own earlier cases count:
   they were known by then.
2. **Pool by size class.** Keep the past cases whose county was, at that case's
   own origin, in the size class the county is in now; with fewer than
   ``min_pool_cases`` of them, use all past cases of the state.
3. **Ends from ranks.** Sort the ``n`` pooled ratios (actual / projected). The
   low end is the ``k``-th smallest with ``k = max(1, floor(0.1 x (n + 1)))``;
   the high end the ``k``-th smallest with ``k = min(n, ceil(0.9 x (n + 1)))``.
   This is a finite-sample ("conformal-style") percentile: with ``n`` earlier
   cases and one new case drawn like them, the new case falls between the two
   ranks at least about 80% of the time. It is the same as reading percentiles
   of the log errors, with no interpolation.
4. **Own-error widening** as in the rest of the baseline (a county whose own
   past is far off gets the wider of its pool's and its own ends).
5. **P50 = the rule's own projection.** P10 = projection x low end, P90 =
   projection x high end, then the range is stretched just enough to hold the
   P50 (a pool can have run low in more than 90% of cases).

The test scores every origin with at least ``min_past_cases`` past cases, for
every contest rule, in each state named in the block. The contest's
pre-stated choosing rule is re-run with these coverages, and the adoption rule
(:func:`adoption_decision`) is judged on each state's winner.

:func:`run_study` reads the state packs (permits, tracts and county types only;
light) and :func:`write_results` writes ``assumptions/calibration/
baseline_calibration.yaml``. The engine reads that file's decision
(:func:`calibration_for`): with ``adopt`` the shipped ranges use step 1-5
multipliers; with ``keep_current`` nothing changes.
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
    origin_size_classes,
    validate_baseline,
)
from visionhousing.baseline.contest import choose_winner, contest_rules, own_widening
from visionhousing.baseline.panel import to_panel
from visionhousing.baseline.rules import PlaceInfo, Rule
from visionhousing.baseline.settings import BaselineSettings, settings_from_ledger
from visionhousing.ledger import Ledger

__all__ = [
    "BLOCK",
    "RESULTS_PATH",
    "adoption_decision",
    "calibrated_cases",
    "calibration_for",
    "conformal_ends",
    "coverage_stats",
    "is_past",
    "pool_ends",
    "run_study",
    "state_total_check",
    "write_results",
]

BLOCK = "oot_calibration"
"""Name of the pre-registered block in ``assumptions/backtest.yaml``."""

_ROOT = Path(__file__).resolve().parents[3]
RESULTS_PATH = _ROOT / "assumptions" / "calibration" / "baseline_calibration.yaml"
"""Default results file (the block's ``results`` names it too)."""


# --------------------------------------------------------------------------- pieces


def is_past(origins: np.ndarray, o: int, lead: int, horizon: int) -> np.ndarray:
    """True for cases whose five target years had all been observed by origin ``o``."""
    return np.asarray(origins, dtype=int) + lead + horizon - 1 <= int(o)


def conformal_ends(
    ratios: np.ndarray, levels: Sequence[float] = (0.1, 0.9)
) -> tuple[float, float] | None:
    """Finite-sample low and high ends of a set of ratios (``None`` if there are none).

    With ``n`` finite ratios sorted from smallest, the low end is the ``k``-th
    smallest, ``k = max(1, floor(levels[0] x (n + 1)))``, and the high end the
    ``k``-th smallest, ``k = min(n, ceil(levels[1] x (n + 1)))``.
    """
    r = np.sort(np.asarray(ratios, dtype=float)[np.isfinite(ratios)])
    n = r.size
    if n == 0:
        return None
    k_lo = max(1, math.floor(float(levels[0]) * (n + 1) + 1e-9))
    k_hi = min(n, math.ceil(float(levels[1]) * (n + 1) - 1e-9))
    return float(r[min(k_lo, n) - 1]), float(r[max(k_hi, 1) - 1])


def pool_ends(
    past: pd.DataFrame,
    county: int,
    county_class: Any,
    block: Mapping[str, Any],
    *,
    min_own: int | None = None,
    beyond: float = math.inf,
) -> tuple[tuple[float, float] | None, str, int]:
    """Calibrated ends for one county from past cases (steps 2-4 of the module docstring).

    Args:
        past: Past cases only (``place``, ``ratio`` finite, ``size_class`` = the
            county's class at the case's own origin).
        county: The county's row index.
        county_class: The county's size class now (at the scored origin).
        block: The ``oot_calibration`` block.
        min_own: ``calibration.min_own_cases`` (``None``: no own-error widening).
        beyond: ``name_few_cases_beyond``.

    Returns:
        ``(ends or None, pool, n)``: ``pool`` is ``"size class"`` or ``"all"``.
    """
    levels = tuple(float(x) for x in block.get("levels", (0.1, 0.9)))
    mine = past[past["size_class"].to_numpy() == county_class]
    pool, use = "size class", mine
    if len(mine) < int(block["min_pool_cases"]):
        pool, use = "all", past
    ends = conformal_ends(use["ratio"].to_numpy(dtype=float), levels)
    if ends is None:
        return None, pool, 0
    if block.get("own_widening", True) and min_own is not None:
        q, _ = own_widening(past, county, np.array(ends), levels, int(min_own), float(beyond))
        ends = (float(q[0]), float(q[-1]))
    return ends, pool, len(use)


def calibrated_cases(
    cases: pd.DataFrame,
    classes: Mapping[int, np.ndarray],
    lead: int,
    horizon: int,
    block: Mapping[str, Any],
    *,
    min_own: int | None = None,
    beyond: float = math.inf,
) -> pd.DataFrame:
    """Score every origin with enough past cases, using past cases only.

    Args:
        cases: ``backtest_cases`` output for the state's counties (one rule).
        classes: Origin -> size class of each county at that origin.
        lead: Years from origin to first target year.
        horizon: Target years.
        block: The ``oot_calibration`` block.
        min_own: ``calibration.min_own_cases`` (own-error widening).
        beyond: ``name_few_cases_beyond``.

    Returns:
        Scored cases (actual and projection above zero) with ``p50`` (the
        projection), ``low``, ``high``, ``pool`` and ``pool_cases``.
    """
    finite = cases[np.isfinite(cases["ratio"])].copy()
    cols = ["place", "origin", "projected", "actual", "ratio", "p50", "low", "high", "pool",
            "pool_cases"]  # fmt: skip
    if finite.empty:
        return pd.DataFrame(columns=cols)
    finite["size_class"] = [
        int(classes[int(o)][int(p)]) for p, o in zip(finite["place"], finite["origin"], strict=True)
    ]
    origins = finite["origin"].to_numpy(dtype=int)
    out = []
    for o in sorted(set(origins.tolist())):
        past = finite[is_past(origins, o, lead, horizon)]
        if len(past) < int(block["min_past_cases"]):
            continue
        now = finite[(origins == o) & (finite["actual"].to_numpy() > 0)
                     & (finite["projected"].to_numpy() > 0)]  # fmt: skip
        for _, case in now.iterrows():
            c = int(case["place"])
            ends, pool, n = pool_ends(
                past, c, int(classes[int(o)][c]), block, min_own=min_own, beyond=beyond
            )
            if ends is None:
                continue
            p50 = float(case["projected"])
            low, high = p50 * ends[0], p50 * ends[1]
            if block.get("contain_p50", True):
                low, high = min(low, p50), max(high, p50)
            out.append({"place": c, "origin": int(o), "projected": p50,
                        "actual": float(case["actual"]), "ratio": float(case["ratio"]),
                        "p50": p50, "low": low, "high": high, "pool": pool,
                        "pool_cases": n})  # fmt: skip
    return pd.DataFrame(out, columns=cols)


def coverage_stats(scored: pd.DataFrame) -> dict[str, Any]:
    """Coverage, width and error of calibrated cases (the block's definitions).

    Coverage = share with ``low <= actual <= high``; width = mean of
    ``(high - low) / p50``; error = median of ``|log(p50 / actual)|``.
    """
    if scored is None or len(scored) == 0:
        return {"cases": 0, "covered": 0, "coverage": math.nan, "width": math.nan,
                "median_abs_error": math.nan, "origins": []}  # fmt: skip
    a = scored["actual"].to_numpy(dtype=float)
    lo, hi = scored["low"].to_numpy(dtype=float), scored["high"].to_numpy(dtype=float)
    p50 = scored["p50"].to_numpy(dtype=float)
    inside = (a >= lo) & (a <= hi)
    return {
        "cases": len(scored),
        "covered": int(inside.sum()),
        "coverage": float(inside.mean()),
        "width": float(np.mean((hi - lo) / p50)),
        "median_abs_error": float(np.median(np.abs(np.log(p50 / a)))),
        "origins": sorted({int(o) for o in scored["origin"]}),
    }


def adoption_decision(
    per_state: Mapping[str, Mapping[str, Any]], block: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply the pre-registered adoption rule (see the block's ``outcomes``).

    Args:
        per_state: State -> ``{"cases", "covered", "coverage"}`` of its winner.
        block: The ``oot_calibration`` block.

    Returns:
        ``{outcome, reason, pooled_coverage, pooled_cases, short_states}``;
        ``outcome`` is ``adopt`` or ``keep_current``; ``reason`` is ``in_band``,
        ``under_band``, ``over_band`` or ``state_short``.
    """
    low, high = (float(x) for x in block["coverage_band"])
    floor = float(block["min_state_coverage"])
    states = [str(s) for s in block["states"]]
    cases = sum(int((per_state.get(s) or {}).get("cases", 0)) for s in states)
    covered = sum(int((per_state.get(s) or {}).get("covered", 0)) for s in states)
    pooled = covered / cases if cases else math.nan

    def short(s: str) -> bool:
        st = per_state.get(s) or {}
        c = st.get("coverage", math.nan)
        return not st.get("cases") or not math.isfinite(float(c)) or float(c) < floor

    shorts = [s for s in states if short(s)]
    base = {"pooled_coverage": pooled, "pooled_cases": cases, "short_states": shorts}
    if not math.isfinite(pooled) or pooled < low:
        return {**base, "outcome": "keep_current", "reason": "under_band"}
    if pooled > high:
        return {**base, "outcome": "keep_current", "reason": "over_band"}
    if shorts:
        return {**base, "outcome": "keep_current", "reason": "state_short"}
    return {**base, "outcome": "adopt", "reason": "in_band"}


def state_total_check(
    rule: Rule,
    years: np.ndarray,
    state_units: np.ndarray,
    origins: Sequence[int],
    lead: int,
    horizon: int,
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """Descriptive out-of-time check of the same ends on the state's own yearly totals."""
    cases = backtest_cases(rule, years, state_units, origins, lead, horizon)
    cases = cases[np.isfinite(cases["ratio"])]
    levels = tuple(float(x) for x in block.get("levels", (0.1, 0.9)))
    need = int(block["state_check_min_past_cases"])
    rows = []
    org = cases["origin"].to_numpy(dtype=int)
    for _, case in cases.iterrows():
        o = int(case["origin"])
        past = cases[is_past(org, o, lead, horizon)]
        if len(past) < need or case["actual"] <= 0 or case["projected"] <= 0:
            continue
        ends = conformal_ends(past["ratio"].to_numpy(dtype=float), levels)
        if ends is None:
            continue
        p50 = float(case["projected"])
        rows.append({"origin": o, "actual": float(case["actual"]), "p50": p50,
                     "low": min(p50 * ends[0], p50), "high": max(p50 * ends[1], p50)})  # fmt: skip
    stats = coverage_stats(pd.DataFrame(rows))
    stats["min_past_cases"] = need
    return stats


# --------------------------------------------------------------------------- one state


def state_study(
    table: pd.DataFrame,
    ledger: Ledger,
    rules: Mapping[str, Any],
    info: PlaceInfo,
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """Every contest rule, calibrated out of time, and the re-run contest, for one state."""
    settings = settings_from_ledger(ledger)
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    candidates = contest_rules(ledger, rules)
    skill = validate_baseline(table, candidates, origins, settings, info)
    band = tuple(float(x) for x in rules["contest"]["coverage_band"])
    before, before_reason = choose_winner(skill, band, settings.tolerance)
    classes = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    calib = rules.get("calibration") or {}
    beyond = float(rules.get("name_few_cases_beyond", math.inf))
    per_rule: dict[str, dict[str, Any]] = {}
    scored_by_rule: dict[str, pd.DataFrame] = {}
    for rule in candidates:
        cases = backtest_cases(
            rule, panel.years, panel.units, origins, lead, settings.horizon, info
        )
        scored = calibrated_cases(
            cases, classes, lead, settings.horizon, block,
            min_own=int(calib["min_own_cases"]) if "min_own_cases" in calib else None,
            beyond=beyond,
        )  # fmt: skip
        scored_by_rule[rule.name] = scored
        st = coverage_stats(scored)
        row = skill.set_index("rule").loc[rule.name]
        per_rule[rule.name] = {
            "complexity": int(rule.complexity),
            "contest_error": float(row["median_abs_log_ratio"]),
            "before_coverage": float(row["coverage_80"]),
            "before_width": float(row["range_width"]),
            **{k: v for k, v in st.items() if k != "origins"},
            "pooled_all_share": float((scored["pool"] == "all").mean()) if len(scored) else None,
        }
    rerun = skill.copy()
    rerun["coverage_80"] = [per_rule[r]["coverage"] for r in rerun["rule"]]
    rerun["range_width"] = [per_rule[r]["width"] for r in rerun["rule"]]
    after, after_reason = choose_winner(rerun, band, settings.tolerance)
    w = per_rule[after]
    b = per_rule[before]
    rule_after = next(r for r in candidates if r.name == after)
    state_check = state_total_check(
        rule_after, panel.years, panel.state_series(), origins, lead, settings.horizon, block
    )
    return {
        "counties": len(panel.counties),
        "origins": [int(origins[0]), int(origins[-1])] if origins else [],
        "scored_origins": coverage_stats(scored_by_rule[after])["origins"],
        "winner_before": before,
        "winner_before_reason": before_reason,
        "winner_after": after,
        "winner_after_reason": after_reason,
        "before": {
            "rule": before,
            "coverage": b["before_coverage"],
            "width": b["before_width"],
            "median_abs_error": b["median_abs_error"],
            "note": "today's contest coverage (other counties' past errors, raw ratios) and "
            "the projection's error on the calibrated scored cases",
        },
        "after": {
            "rule": after,
            "cases": w["cases"],
            "covered": w["covered"],
            "coverage": w["coverage"],
            "width": w["width"],
            "median_abs_error": w["median_abs_error"],
            "pooled_all_share": w["pooled_all_share"],
        },
        "rules": per_rule,
        "state_total_check": state_check,
    }


# --------------------------------------------------------------------------- study


def _state_inputs(
    state: str, ledger: Ledger, settings: BaselineSettings
) -> tuple[pd.DataFrame, PlaceInfo]:
    """Read one state's permits, tracts and county types (light; no blocks or stops)."""
    from visionhousing.baseline.backtest import size_classes
    from visionhousing.baseline.contest import place_info
    from visionhousing.baseline.places import load_place_rules, read_pack_table
    from visionhousing.engine.context import pack_dir

    folder = pack_dir(state)
    permits = read_pack_table(folder, "permits_county_year")
    if permits is None:
        raise FileNotFoundError(f"no permit table in the {state} pack; build it first")
    permits = permits.assign(county=permits["county"].astype(str))
    tracts = read_pack_table(folder, "tracts")
    typology = read_pack_table(folder, "typology_county")
    panel = to_panel(permits)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    order = load_place_rules().get("place_type", ["size_class"])
    info = place_info(
        panel.counties, classes, settings.n_classes, order, typology=typology, tracts=tracts
    )
    return permits, info


def run_study(
    states: Sequence[str] | None = None,
    *,
    rules: Mapping[str, Any] | None = None,
    ledger: Ledger | None = None,
    inputs: Mapping[str, tuple[pd.DataFrame, PlaceInfo]] | None = None,
) -> dict[str, Any]:
    """Run the pre-registered study; refuses to score if the block has changed.

    Args:
        states: States to score (default: the block's ``states``).
        rules: ``assumptions/backtest.yaml`` (default: read it).
        ledger: The ledger (default: read it).
        inputs: State -> ``(permit table, place info)`` (tests); default reads
            the state packs.

    Returns:
        The results document (see :func:`write_results`).
    """
    from visionhousing.baseline.places import load_place_rules
    from visionhousing.ledger import load_ledger
    from visionhousing.validation.preregistration import verify

    rules = rules if rules is not None else load_place_rules()
    checked = verify(BLOCK, rules)  # raises before anything is scored
    block = rules[BLOCK]
    ledger = ledger if ledger is not None else load_ledger()
    settings = settings_from_ledger(ledger)
    states = [str(s).upper() for s in (states or block["states"])]
    per_state: dict[str, Any] = {}
    for s in states:
        table, info = (inputs or {}).get(s) or _state_inputs(s, ledger, settings)
        per_state[s] = state_study(table, ledger, rules, info, block)
    decision = adoption_decision({s: per_state[s]["after"] for s in states}, block)
    return _clean(
        {
            "version": 1,
            "id": "baseline_calibration_2026_11",
            "label": "Out-of-time calibrated baseline ranges (pre-registered test)",
            "status": "validation",
            "use": (
                "If decision.outcome is 'adopt', the baseline's P10 and P90 use the calibrated "
                "ends (oot_calibration in assumptions/backtest.yaml); with 'keep_current' "
                "nothing in the engine changes. Method: docs/modules/baseline.md."
            ),
            "provenance": {
                "summary": (
                    "How often each baseline rule's P10-P90 range caught the real five-year "
                    "county total when the range is built only from errors known at the time."
                ),
                "method": (
                    "Census Building Permits Survey county files in each state pack; "
                    "visionhousing.baseline.calibrate; settings in assumptions/backtest.yaml "
                    "(oot_calibration)."
                ),
                "generated_by": "visionhousing validate-baseline-calibration",
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
                    "The first scored origins can draw only on errors made in the boom before "
                    "the national bust, and are asked to cover the bust and the recovery; "
                    "no ranking of past errors can see a new housing cycle coming.",
                    "Rolling origins overlap (each shares four of its five target years with "
                    "the next), so the scored cases hold far fewer independent periods.",
                    "The state-total check scores few origins per state and is descriptive.",
                ],
            },
            "preregistration": checked,
            "states": per_state,
            "decision": decision,
        }
    )


HEADER = """\
# Calibration (validation): out-of-time calibrated baseline ranges (generated; do not edit).
# Written by `visionhousing validate-baseline-calibration` from the Census Building
# Permits Survey county files in the state packs. A pre-registered test (block
# oot_calibration in assumptions/backtest.yaml) of ranges built only from errors
# known at each past start year. The baseline reads decision.outcome.
# Method: docs/modules/baseline.md ("Out-of-time calibrated ranges").
"""


def write_results(doc: Mapping[str, Any], path: Path = RESULTS_PATH) -> Path:
    """Write the results document as YAML with the generated-file header."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


def calibration_for(rules: Mapping[str, Any], path: Path | None = None) -> dict[str, Any] | None:
    """The calibration the engine applies, or ``None`` (block absent, no results, not adopted)."""
    block = rules.get(BLOCK)
    if not block:
        return None
    where = Path(path) if path is not None else _ROOT / str(block.get("results", RESULTS_PATH))
    if not where.is_file():
        return None
    doc = yaml.safe_load(where.read_text(encoding="utf-8")) or {}
    if (doc.get("decision") or {}).get("outcome") != "adopt":
        return None
    return {**block, "source": str(block.get("results", "")), "decision": doc["decision"]}


def _clean(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        return None if not math.isfinite(v) else round(v, 4)
    return v


# --------------------------------------------------------------------------- command


def add_parser(sub: Any) -> None:
    """Add ``validate-baseline-calibration`` to the command line."""
    p = sub.add_parser(
        "validate-baseline-calibration",
        help="Test out-of-time calibrated baseline ranges (pre-registered; writes the results)",
    )
    p.add_argument("--states", nargs="*", default=None, help="states (default: the block's)")
    p.add_argument("--out", type=Path, default=RESULTS_PATH, help="results file")


def command(args: Any) -> int:
    """Run the study, print the table and the decision, and write the results file."""
    doc = run_study(args.states)
    for s, st in doc["states"].items():
        b, a = st["before"], st["after"]
        print(
            f"{s}: before {b['rule']} coverage {b['coverage']:.1%} width {b['width']:.2f} "
            f"error {b['median_abs_error']:.3f} | after {a['rule']} coverage "
            f"{a['coverage']:.1%} width {a['width']:.2f} error {a['median_abs_error']:.3f} "
            f"({a['cases']} cases)"
        )
    d = doc["decision"]
    print(f"pooled coverage {d['pooled_coverage']:.1%}: {d['outcome']} ({d['reason']})")
    print(f"wrote {write_results(doc, args.out)}")
    return 0
