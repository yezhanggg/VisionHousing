"""How often the shipped baseline range caught the real total in past years (honest labels).

Plain-language summary
----------------------
The baseline's range (P10 to P90) is meant to catch the real five-year total in
about 8 of 10 cases. Tested on past years with only the errors known at the
time ("out of time"), ranges built the way the tool builds them caught it less
often. So every baseline range now says how often ranges like it caught the
real total in the past, in words a reader can check:

    In past years, ranges like this caught the real five-year total in N of M
    cases (out of time).

and its short label is "range (caught N% of past totals)" rather than "likely
range". The reform ranges keep the words "likely range": they are simulation
ranges, conditional on the baseline and the assumptions, and were never
checked against past totals (there are no past totals of a reform not yet
passed).

What is counted (block ``baseline_reliability`` in ``assumptions/backtest.yaml``,
fingerprint in ``assumptions/preregistration.yaml``, written before any count):

* **County ranges.** The contest's own out-of-time test
  (:func:`visionhousing.baseline.backtest.leave_one_county_out` with the time
  cut): each past county case gets a range from other counties' errors in its
  size class whose five target years were all observed by the case's start
  year. Counted for the state and for each county group (size class at the
  case's own start year: small, medium, large).
* **State range.** The state range as shipped, rebuilt at each past start year
  from the state's earlier totals only: ratio quantiles q10, q50, q90 of those
  past cases; P50 = state projection x q50 when the bias correction is kept
  (else the projection); P10 = P50 x q10 / q50, P90 = P50 x q90 / q50; then the
  same small-sample and growth-aware widening the engine applies. Start years
  with at least ``min_past_origins`` earlier state cases are counted.

The command ``visionhousing validate-baseline-reliability`` (permits only; light)
writes ``assumptions/calibration/baseline_reliability.yaml``. The engine reads
it (:func:`reliability_for`) and adds the sentence to the formula of the
state's and each county's baseline record (:func:`attach`), so every screen and
document that shows the record shows the measured reliability with it. Nothing
here changes an estimate.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    class_label,
    lead_years,
    leave_one_county_out,
    origin_size_classes,
    ratio_quantiles,
    size_classes,
)
from visionhousing.baseline.panel import to_panel
from visionhousing.baseline.rules import PlaceInfo, Rule

__all__ = [
    "BLOCK",
    "RESULTS_PATH",
    "attach",
    "county_reliability",
    "label",
    "range_tag",
    "reliability_for",
    "run_study",
    "sentence",
    "state_reliability",
    "write_results",
]

BLOCK = "baseline_reliability"
"""Name of the pre-registered block in ``assumptions/backtest.yaml``."""

_ROOT = Path(__file__).resolve().parents[3]
RESULTS_PATH = _ROOT / "assumptions" / "calibration" / "baseline_reliability.yaml"
"""Default results file (the block's ``results`` names it too)."""


# --------------------------------------------------------------------------- counts


def _count(inside: np.ndarray) -> dict[str, Any]:
    n = int(inside.size)
    k = int(inside.sum())
    return {"cases": n, "covered": k, "coverage": (k / n) if n else math.nan}


def county_reliability(
    cases: pd.DataFrame,
    classes: Mapping[int, np.ndarray],
    lead: int,
    horizon: int,
    n_classes: int,
) -> dict[str, Any]:
    """Out-of-time coverage of the county ranges, for the state and by size class.

    Args:
        cases: ``backtest_cases`` of the shipped rule (counties).
        classes: Origin -> size class of each county at that origin.
        lead: Years from origin to first target year.
        horizon: Target years.
        n_classes: Number of size classes.

    Returns:
        ``{"all": {cases, covered, coverage}, "groups": {label: {...}}}``.
    """
    scored = leave_one_county_out(cases, classes, lead, horizon)
    a = scored["actual"].to_numpy(dtype=float)
    inside = (a >= scored["low"].to_numpy(dtype=float)) & (
        a <= scored["high"].to_numpy(dtype=float)
    )
    groups = {}
    cls = scored["size_class"].to_numpy(dtype=int) if len(scored) else np.array([], dtype=int)
    for k in range(n_classes):
        groups[class_label(k, n_classes)] = _count(inside[cls == k])
    return {"all": _count(inside), "groups": groups}


def state_reliability(
    rule: Rule,
    years: np.ndarray,
    state_units: np.ndarray,
    county_cases: pd.DataFrame,
    origins: Sequence[int],
    lead: int,
    horizon: int,
    block: Mapping[str, Any],
    *,
    keep_correction: bool = True,
    small_sample: Mapping[str, Any] | None = None,
    growth: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Out-of-time coverage of the state range as shipped (see the module docstring).

    Args:
        rule: The shipped rule.
        years: Calendar years of the panel.
        state_units: State totals, shape (1, years, types).
        county_cases: ``backtest_cases`` of the rule for the counties (for the
            small-sample weight: past finite county cases at each origin).
        origins: Back-test origins.
        lead: Years from origin to first target year.
        horizon: Target years.
        block: The ``baseline_reliability`` block.
        keep_correction: The contest's bias-correction choice.
        small_sample: ``small_sample`` of ``assumptions/backtest.yaml`` (or ``None``).
        growth: The state's growth-aware widening (``growth_widen_for``) or ``None``.

    Returns:
        ``{cases, covered, coverage, width, origins, min_past_origins}``.
    """
    from visionhousing.baseline.project import widen_growth_class, widen_small_sample

    levels = tuple(float(x) for x in block.get("state_quantiles", (0.1, 0.5, 0.9)))
    need = int(block["min_past_origins"])
    cases = backtest_cases(rule, years, state_units, origins, lead, horizon)
    cases = cases[np.isfinite(cases["ratio"])]
    org = cases["origin"].to_numpy(dtype=int)
    c_org = county_cases["origin"].to_numpy(dtype=int)
    c_ok = np.isfinite(county_cases["ratio"].to_numpy(dtype=float))
    rows = []
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
        rows.append((o, float(case["actual"]), p50, p50 * math.exp(low), p50 * math.exp(high)))
    if not rows:
        return {"cases": 0, "covered": 0, "coverage": math.nan, "width": math.nan,
                "origins": [], "min_past_origins": need}  # fmt: skip
    arr = np.array([r[1:] for r in rows], dtype=float)
    inside = (arr[:, 0] >= arr[:, 2]) & (arr[:, 0] <= arr[:, 3])
    return {
        **_count(inside),
        "width": float(np.mean((arr[:, 3] - arr[:, 2]) / arr[:, 1])),
        "origins": [r[0] for r in rows],
        "min_past_origins": need,
    }


# --------------------------------------------------------------------------- study


def state_study(
    state: str,
    table: pd.DataFrame,
    ledger: Any,
    rules: Mapping[str, Any],
    info: PlaceInfo | None,
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """The shipped rule's county and state reliability for one state."""
    from visionhousing.baseline.contest import run_contest
    from visionhousing.baseline.project import growth_widen_for
    from visionhousing.baseline.settings import settings_from_ledger

    settings = settings_from_ledger(ledger)
    contest = run_contest(table, ledger, rules, info)
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    classes = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    rule = contest.rule
    cases = backtest_cases(
        rule, panel.years, panel.units, origins, lead, settings.horizon, contest.info
    )
    county = county_reliability(cases, classes, lead, settings.horizon, settings.n_classes)
    growth = growth_widen_for(state, ledger, rules, int(panel.years[-1]))
    st = state_reliability(
        rule, panel.years, panel.state_series(), cases, origins, lead, settings.horizon, block,
        keep_correction=bool(contest.correction.get("keep", True)),
        small_sample=rules.get("small_sample"), growth=growth,
    )  # fmt: skip
    return {
        "rule": rule.name,
        "bias_correction_kept": bool(contest.correction.get("keep", True)),
        "growth_widening": bool(growth),
        "state_range": st,
        "county_ranges": county,
    }


def run_study(
    states: Sequence[str] | None = None,
    *,
    rules: Mapping[str, Any] | None = None,
    ledger: Any = None,
    inputs: Mapping[str, tuple[pd.DataFrame, PlaceInfo]] | None = None,
) -> dict[str, Any]:
    """Count the shipped ranges' past coverage; refuses to run if the block has changed."""
    from visionhousing.baseline.calibrate import _clean, _state_inputs
    from visionhousing.baseline.places import load_place_rules
    from visionhousing.baseline.settings import settings_from_ledger
    from visionhousing.ledger import load_ledger
    from visionhousing.validation.preregistration import verify

    rules = rules if rules is not None else load_place_rules()
    checked = verify(BLOCK, rules)
    block = rules[BLOCK]
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
            "id": "baseline_reliability_2026_11",
            "label": "Measured reliability of the baseline range (out of time)",
            "status": "validation",
            "use": (
                "The engine adds each state's and county group's sentence to the baseline "
                "records' formulas and labels the baseline range with its measured share. "
                "Changes no estimate. Method: docs/modules/baseline.md."
            ),
            "provenance": {
                "summary": (
                    "How often ranges built the way the baseline builds them caught the real "
                    "five-year total in past years, using only errors known at each start year."
                ),
                "method": (
                    "Census Building Permits Survey county files in each state pack; "
                    "visionhousing.baseline.reliability; settings in assumptions/backtest.yaml "
                    "(baseline_reliability)."
                ),
                "generated_by": "visionhousing validate-baseline-reliability",
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
                    "The past start years hold about one housing cycle, so these shares rest "
                    "on few independent periods.",
                    "The state range's widening inputs (the small-sample prior and the "
                    "growth-class pool) were set from the full record, so the state share is "
                    "slightly favourable to the widened states.",
                    "The rule scored here (and its bias-correction choice) was chosen by the "
                    "contest on the full record, which saw the same target years, so the "
                    "shares are slightly favourable.",
                ],
            },
            "preregistration": checked,
            "sentence": block["sentence"],
            "range_label": block["label"],
            "states": per_state,
        }
    )


HEADER = """\
# Calibration (validation): measured reliability of the baseline range (generated; do not edit).
# Written by `visionhousing validate-baseline-reliability` from the Census Building
# Permits Survey county files in the state packs. How often ranges built like the
# shipped baseline range caught the real five-year total in past years (out of
# time). Descriptive (block baseline_reliability in assumptions/backtest.yaml);
# the engine shows it beside the range and changes no estimate.
# Method: docs/modules/baseline.md ("Measured reliability").
"""


def write_results(doc: Mapping[str, Any], path: Path = RESULTS_PATH) -> Path:
    """Write the results document as YAML with the generated-file header."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- engine side


def reliability_for(state: str | None, path: Path | None = None) -> dict[str, Any] | None:
    """The state's entry in the results file (with the sentence and label), or ``None``."""
    if not state:
        return None
    where = Path(path) if path is not None else RESULTS_PATH
    if not where.is_file():
        return None
    doc = yaml.safe_load(where.read_text(encoding="utf-8")) or {}
    entry = (doc.get("states") or {}).get(str(state).upper())
    if not entry:
        return None
    return {
        **entry,
        "sentence": doc.get("sentence"),
        "label": doc.get("range_label"),
        "source": "assumptions/calibration/baseline_reliability.yaml",
    }


def sentence(counts: Mapping[str, Any] | None, template: str) -> str | None:
    """The reliability sentence for one count, or ``None`` when nothing was scored."""
    if not counts or not counts.get("cases"):
        return None
    return template.format(covered=int(counts["covered"]), cases=int(counts["cases"]))


def label(counts: Mapping[str, Any] | None, template: str) -> str | None:
    """The short range label ("range (caught N% of past totals)"), or ``None``."""
    if not counts or not counts.get("cases"):
        return None
    pct = round(100.0 * int(counts["covered"]) / int(counts["cases"]))
    return template.format(pct=pct)


_TAG = re.compile(r'shown as "([^"]+)"')


def range_tag(formula: str) -> str | None:
    """The range's label ("range (caught N% of past totals)") from a formula, or ``None``."""
    m = _TAG.search(str(formula or ""))
    return m.group(1) if m else None


def attach(result: Any, table: pd.DataFrame, settings: Any, entry: Mapping[str, Any]) -> Any:
    """Add the reliability sentence to the state's and each county's baseline record formula.

    The state record gets the state-range count; each county record gets its
    current size class's county-range count. Values are unchanged.
    """
    from dataclasses import replace

    template = str(entry.get("sentence") or "")
    lab = str(entry.get("label") or "")
    src = entry.get("source", "")

    def add(rec: Any, counts: Mapping[str, Any] | None, scope: str) -> Any:
        text = sentence(counts, template)
        if not text:
            return rec
        tag = label(counts, lab)
        shown = f', shown as "{tag}"' if tag else ""
        words = f"; {scope}{shown}: {text[0].lower()}{text[1:].rstrip('.')} ({src})"
        return rec.model_copy(update={"formula": rec.formula + words})

    state = add(result.state, entry.get("state_range"), "measured reliability of the state range")
    panel = to_panel(table)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    groups = (entry.get("county_ranges") or {}).get("groups") or {}
    counties = {}
    for i, c in enumerate(panel.counties):
        rec = result.counties.get(c)
        if rec is None:
            continue
        name = class_label(int(classes[i]), settings.n_classes)
        counties[c] = add(rec, groups.get(name), f"measured reliability of {name}-county ranges")
    for c, rec in result.counties.items():
        counties.setdefault(c, rec)
    return replace(result, state=state, counties=counties)


# --------------------------------------------------------------------------- command


def add_parser(sub: Any) -> None:
    """Add ``validate-baseline-reliability`` to the command line."""
    p = sub.add_parser(
        "validate-baseline-reliability",
        help="Count how often the shipped baseline range caught past totals (writes the results)",
    )
    p.add_argument("--states", nargs="*", default=None, help="states (default: the block's)")
    p.add_argument("--out", type=Path, default=RESULTS_PATH, help="results file")


def command(args: Any) -> int:
    """Run the count, print it and write the results file."""
    doc = run_study(args.states)
    for s, st in doc["states"].items():
        a, c = st["state_range"], st["county_ranges"]["all"]
        print(
            f"{s} ({st['rule']}): state range caught {a['covered']} of {a['cases']}; "
            f"county ranges caught {c['covered']} of {c['cases']}"
        )
    print(f"wrote {write_results(doc, args.out)}")
    return 0
