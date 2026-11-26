"""National state-total calibration: a state's range from every state's past state-total errors.

Plain-language summary
----------------------
The baseline's state range comes from the state's own past errors: at each
past start year, how far the rule's five-year state total was from the real
one. One state's record holds about one housing cycle, and those errors ran to
one side, so out of time the state range caught the real total too rarely
(Pennsylvania: at none of its scored start years).

This module tests, as fixed in advance (block ``national_state_calibration``
in ``assumptions/backtest.yaml``, fingerprint in
``assumptions/preregistration.yaml``), a range built from a **national pool**:

1. For a state ``s`` and a start year ``o``, take the state-total cases of the
   same rule for **every** state whose five target years had all been
   observed by ``o`` (``o' + lead + horizon - 1 <= o``).
2. **pooled**: the range's ends are rank-based percentiles (the ``k``-th
   smallest ratio, ``k = max(1, floor(0.1 (n + 1)))`` and
   ``k = min(n, ceil(0.9 (n + 1)))``) of those ratios of real to projected.
3. **shock**: errors in the same start year move together across states (a
   statewide, or national, shock). Each past start year's shock is the median
   log error over states; the rest is each state's own part. The ends are the
   rank ends of the shocks (one per start year) plus the rank ends of the
   own parts, so a run of one-sided years counts as few draws, not many.
4. P50 = the rule's own state projection; P10 = P50 x low end, P90 = P50 x
   high end, stretched just enough to hold the P50.

Scored on every state of the national county panel; judged on the pooled
coverage and on Pennsylvania, Vermont, Massachusetts and Wyoming. The decision
rule (:func:`decide`) is the block's. The command
``visionhousing validate-national-state-calibration`` writes
``assumptions/calibration/national_state_calibration.yaml``. Its decision is
``keep_current``, so the engine does not read it and the state range is unchanged;
:func:`national_ends_for` returns the adopted ends for a rule and is what an
adopted decision would be wired through (no engine code calls it today).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.baseline.backtest import backtest_cases, backtest_origins
from visionhousing.baseline.calibrate import conformal_ends
from visionhousing.baseline.panel import to_panel
from visionhousing.baseline.rules import Rule

__all__ = [
    "BLOCK",
    "OUT_PATH",
    "decide",
    "national_ends_for",
    "past_mask",
    "pool_ends",
    "run_study",
    "score_state",
    "state_cases",
    "write_document",
]

BLOCK = "national_state_calibration"
_ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = _ROOT / "assumptions" / "calibration" / "national_state_calibration.yaml"


# --------------------------------------------------------------------------- cases


def state_cases(
    panel: pd.DataFrame, rule: Rule, origins: Sequence[int], lead: int, horizon: int
) -> pd.DataFrame:
    """Back-test ``rule`` on each state's yearly total as a one-area series.

    Returns:
        Columns ``state``, ``origin``, ``projected``, ``actual``, ``ratio``.
    """
    p = to_panel(panel.drop(columns=[c for c in ("state", "units_reported") if c in panel]))
    state_of = panel.drop_duplicates("county").set_index("county")["state"].astype(str)
    names = np.asarray([str(state_of[c]) for c in p.counties], dtype=object)
    out = []
    for st in sorted(set(names.tolist())):
        block = p.units[names == st]
        present = ~np.isnan(block).all(axis=0)
        units = np.where(present, np.nansum(block, axis=0), np.nan)[None, :, :]
        cases = backtest_cases(rule, p.years, units, origins, lead, horizon)
        out.append(cases.drop(columns="place").assign(state=st))
    cols = ["state", "origin", "projected", "actual", "ratio"]
    return pd.concat(out, ignore_index=True)[cols] if out else pd.DataFrame(columns=cols)


def past_mask(origins: np.ndarray, o: int, lead: int, horizon: int) -> np.ndarray:
    """True for cases whose five target years had all been observed by ``o``."""
    return np.asarray(origins, dtype=int) + lead + horizon - 1 <= int(o)


def pool_ends(
    past: pd.DataFrame, variant: str, levels: Sequence[float] = (0.1, 0.9)
) -> tuple[float, float] | None:
    """The low and high ratio ends of one variant from a pool of past cases.

    Args:
        past: Past state cases (``origin``, ``ratio``; finite, above zero).
        variant: ``"pooled"`` or ``"shock"``.
        levels: The two levels.

    Returns:
        ``(low, high)`` ratios, or ``None`` with no usable case.
    """
    ok = past[np.isfinite(past["ratio"]) & (past["ratio"] > 0)]
    if ok.empty:
        return None
    if variant == "pooled":
        return conformal_ends(ok["ratio"].to_numpy(dtype=float), levels)
    if variant != "shock":
        raise ValueError(f"unknown variant {variant!r}")
    e = np.log(ok["ratio"].to_numpy(dtype=float))
    shock = ok.assign(e=e).groupby("origin")["e"].median()
    resid = e - shock.reindex(ok["origin"]).to_numpy(dtype=float)
    s = conformal_ends(np.exp(shock.to_numpy(dtype=float)), levels)
    r = conformal_ends(np.exp(resid), levels)
    if s is None or r is None:
        return None
    return s[0] * r[0], s[1] * r[1]


def score_state(
    cases: pd.DataFrame,
    state: str,
    lead: int,
    horizon: int,
    block: Mapping[str, Any],
    variant: str,
) -> pd.DataFrame:
    """Score one state's cases out of time with one variant (the pool = all states' cases)."""
    levels = tuple(float(x) for x in block.get("levels", (0.1, 0.9)))
    need = int(block["min_past_origins"])
    org = cases["origin"].to_numpy(dtype=int)
    ok = np.isfinite(cases["ratio"].to_numpy(dtype=float)) & (cases["ratio"].to_numpy() > 0)
    mine = cases[(cases["state"] == state) & (cases["actual"] > 0) & (cases["projected"] > 0)]
    rows = []
    for _, case in mine.iterrows():
        o = int(case["origin"])
        past = cases[ok & past_mask(org, o, lead, horizon)]
        if past["origin"].nunique() < need:
            continue
        ends = pool_ends(past, variant, levels)
        if ends is None:
            continue
        p50 = float(case["projected"])
        low, high = p50 * ends[0], p50 * ends[1]
        if block.get("contain_p50", True):
            low, high = min(low, p50), max(high, p50)
        rows.append({"state": state, "origin": o, "actual": float(case["actual"]), "p50": p50,
                     "low": low, "high": high, "pool_cases": len(past),
                     "pool_origins": int(past["origin"].nunique())})  # fmt: skip
    return pd.DataFrame(
        rows, columns=["state", "origin", "actual", "p50", "low", "high", "pool_cases",
                       "pool_origins"],
    )  # fmt: skip


def _stats(scored: pd.DataFrame) -> dict[str, Any]:
    if scored is None or scored.empty:
        return {"cases": 0, "covered": 0, "coverage": math.nan, "width": math.nan,
                "above": 0, "below": 0}  # fmt: skip
    a = scored["actual"].to_numpy(dtype=float)
    lo, hi = scored["low"].to_numpy(dtype=float), scored["high"].to_numpy(dtype=float)
    inside = (a >= lo) & (a <= hi)
    return {
        "cases": len(scored),
        "covered": int(inside.sum()),
        "coverage": float(inside.mean()),
        "width": float(np.mean((hi - lo) / scored["p50"].to_numpy(dtype=float))),
        "above": int((a > hi).sum()),
        "below": int((a < lo).sum()),
    }


def judge(per_state: Mapping[str, Mapping[str, Any]], pooled: Mapping[str, Any],
          block: Mapping[str, Any]) -> dict[str, Any]:  # fmt: skip
    """One variant against the pass rule: ``{passes, reason, pooled_coverage, short_states}``."""
    low, high = (float(x) for x in block["coverage_band"])
    floor = float(block["min_state_coverage"])
    c = pooled.get("coverage", math.nan)
    c = float(c) if c is not None else math.nan
    shorts = []
    for s in block["states_named"]:
        st = per_state.get(str(s)) or {}
        v = st.get("coverage")
        if not st.get("cases") or v is None or not math.isfinite(float(v)) or float(v) < floor:
            shorts.append(str(s))
    base = {"pooled_coverage": c, "pooled_cases": int(pooled.get("cases", 0)),
            "short_states": shorts}  # fmt: skip
    if not math.isfinite(c) or c < low:
        return {**base, "passes": False, "reason": "under_band"}
    if c > high:
        return {**base, "passes": False, "reason": "over_band"}
    if shorts:
        return {**base, "passes": False, "reason": "state_short"}
    return {**base, "passes": True, "reason": "in_band"}


def decide(judged: Mapping[str, Mapping[str, Any]], block: Mapping[str, Any]) -> dict[str, Any]:
    """The block's choice between variants: one passes -> it; both -> closer to 0.80; else keep."""
    passing = [v for v in block["variants"] if (judged.get(v) or {}).get("passes")]
    if not passing:
        return {"outcome": "keep_current", "variant": None,
                "reason": "no variant passes"}  # fmt: skip
    if len(passing) == 1:
        return {"outcome": "adopt", "variant": passing[0], "reason": "one variant passes"}
    target = 0.5 * sum(float(x) for x in block["coverage_band"])
    best = min(passing, key=lambda v: (abs(float(judged[v]["pooled_coverage"]) - target),
                                       list(block["variants"]).index(v)))  # fmt: skip
    return {"outcome": "adopt", "variant": best, "reason": "both pass; closer to the target"}


# --------------------------------------------------------------------------- study


def run_study(
    panel: pd.DataFrame,
    rules_by_state: Mapping[str, Rule],
    other_rule: Rule,
    bsettings: Any,
    *,
    block: Mapping[str, Any],
    checked: Mapping[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Score both variants on every state of the panel; decide; write production ends.

    Args:
        panel: The national county panel (``county``, ``state``, ``year``, units).
        rules_by_state: Rule for each named state (the engine's contest winners).
        other_rule: Rule for every other state.
        bsettings: Baseline settings (lead, horizon, origins).
        block: The ``national_state_calibration`` block.
        checked: The pre-registration check (provenance).
        as_of: Date measured.
    """
    years = np.arange(int(panel["year"].min()), int(panel["year"].max()) + 1)
    lead = bsettings.first_year - int(years[-1])
    horizon = bsettings.horizon
    origins = backtest_origins(years, lead, horizon, bsettings.min_history)
    by_rule: dict[str, pd.DataFrame] = {}
    rule_objs = {r.name: r for r in [*rules_by_state.values(), other_rule]}
    for name, rule in rule_objs.items():
        by_rule[name] = state_cases(panel, rule, origins, lead, horizon)
    states = sorted(set(panel["state"].astype(str)))
    rule_of = {s: (rules_by_state.get(s) or other_rule).name for s in states}
    variants: dict[str, Any] = {}
    judged: dict[str, Any] = {}
    for v in block["variants"]:
        parts = [score_state(by_rule[rule_of[s]], s, lead, horizon, block, v) for s in states]
        scored = pd.concat([p for p in parts if not p.empty], ignore_index=True)
        per = {s: _stats(g) for s, g in scored.groupby("state")}
        pooled = _stats(scored)
        named = {s: per.get(s, _stats(None)) for s in block["states_named"]}
        judged[v] = judge(per, pooled, block)
        variants[v] = {
            "pooled": pooled,
            "named_states": named,
            "named_detail": {
                s: [
                    {
                        "origin": int(r.origin),
                        "actual": r.actual,
                        "low": r.low,
                        "p50": r.p50,
                        "high": r.high,
                        "pool_cases": int(r.pool_cases),
                    }
                    for r in scored[scored["state"] == s].itertuples()
                ]
                for s in block["states_named"]
            },
            "origins": sorted({int(o) for o in scored["origin"]}),
            "judged": judged[v],
        }
    decision = decide(judged, block)
    production: dict[str, Any] = {}
    last = int(years[-1])
    levels = tuple(float(x) for x in block.get("levels", (0.1, 0.9)))
    for name, cases in by_rule.items():
        org = cases["origin"].to_numpy(dtype=int)
        past = cases[past_mask(org, last, lead, horizon) & np.isfinite(cases["ratio"])]
        production[name] = {
            v: (lambda e: None if e is None else {"low": e[0], "high": e[1]})(
                pool_ends(past, v, levels)
            )
            for v in block["variants"]
        }
        production[name]["pool_cases"] = len(past)
        production[name]["pool_origins"] = int(past["origin"].nunique())
    from visionhousing.validation import donor_pool as dp

    return dp._clean(
        {
            "version": 1,
            "id": "national_state_calibration_2026_11",
            "label": "National state-total calibration of the baseline state range "
            "(pre-registered test)",
            "status": "validation",
            "use": (
                "If decision.outcome is 'adopt', the baseline's STATE range is the state's "
                "point projection times production_ends[rule][variant], holding the P50; "
                "county ranges are unchanged. With 'keep_current' nothing changes. "
                "Method: docs/modules/baseline.md."
            ),
            "provenance": {
                "summary": (
                    "How often a state range built from every state's earlier state-total "
                    "errors caught the real five-year total, out of time."
                ),
                "measured_on": as_of or date.today().isoformat(),
                "method": (
                    "National county panel from the BPS annual county files (the donor-pool "
                    "study's cache); visionhousing.validation.national_state; settings in "
                    "assumptions/backtest.yaml (national_state_calibration)."
                ),
                "generated_by": "visionhousing validate-national-state-calibration",
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
                    "Every state's errors at one start year share the national housing "
                    "cycle, so the pool holds far fewer independent periods than cases.",
                    "Each named state is scored at a handful of overlapping five-year "
                    "windows; its own coverage moves in steps of one case.",
                    "Each named state's rule is the one its contest picks today on the full "
                    "record, which saw the same target years, so its coverage is slightly "
                    "favourable.",
                ],
            },
            "preregistration": dict(checked or {}),
            "settings": {
                "lead_years": lead,
                "horizon_years": horizon,
                "origins": [int(origins[0]), int(origins[-1])] if origins else [],
                "rules": {s: rule_of[s] for s in block["states_named"]},
                "other_rule": other_rule.name,
                "states_scored": len(states),
            },
            "variants": variants,
            "decision": decision,
            "production_ends": production,
        }
    )


HEADER = """\
# Calibration (validation): national state-total calibration (generated; do not edit).
# Written by `visionhousing validate-national-state-calibration` from the Census
# Building Permits Survey county files of every state. A pre-registered test (block
# national_state_calibration in assumptions/backtest.yaml) of a state range built
# from every state's earlier state-total errors. The baseline reads decision.outcome
# and production_ends. Method: docs/modules/baseline.md.
"""


def write_document(doc: Mapping[str, Any], path: Path = OUT_PATH) -> Path:
    """Write the results with the generated-file header."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


def national_ends_for(
    rule: str, rules: Mapping[str, Any], path: Path | None = None
) -> dict[str, Any] | None:
    """The adopted national ends for ``rule``, or ``None`` (absent, not adopted, no ends)."""
    block = rules.get(BLOCK)
    if not block:
        return None
    where = Path(path) if path is not None else _ROOT / str(block.get("results", OUT_PATH))
    if not where.is_file():
        return None
    doc = yaml.safe_load(where.read_text(encoding="utf-8")) or {}
    d = doc.get("decision") or {}
    if d.get("outcome") != "adopt" or not d.get("variant"):
        return None
    ends = ((doc.get("production_ends") or {}).get(rule) or {}).get(d["variant"])
    if not ends:
        return None
    return {
        "low": float(ends["low"]),
        "high": float(ends["high"]),
        "variant": d["variant"],
        "pool_cases": int(
            ((doc.get("production_ends") or {}).get(rule) or {}).get("pool_cases", 0)
        ),
        "source": str(block.get("results", "")),
    }


# --------------------------------------------------------------------------- command


def add_parser(sub: Any) -> None:
    """Add ``validate-national-state-calibration``."""
    p = sub.add_parser(
        "validate-national-state-calibration",
        help="Test a state range from every state's past state-total errors (pre-registered)",
    )
    p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    p.add_argument("--out", type=Path, default=OUT_PATH, help="results file")


def command(args: Any) -> int:
    """Build (or reuse) the national panel, resolve the named states' rules, score, write."""
    from visionhousing.baseline.calibrate import _state_inputs
    from visionhousing.baseline.contest import contest_rules, run_contest
    from visionhousing.baseline.places import load_place_rules
    from visionhousing.baseline.settings import settings_from_ledger
    from visionhousing.ledger import load_ledger
    from visionhousing.validation import donor_pool as dp
    from visionhousing.validation.preregistration import verify

    rules = load_place_rules()
    checked = verify(BLOCK, rules)  # before anything is scored
    block = rules[BLOCK]
    ledger = load_ledger()
    bsettings = settings_from_ledger(ledger)
    s = dp.load_settings()
    paths = dp.fetch_national(Path(args.raw), range(int(s["first_year"]), int(s["last_year"]) + 1))
    panel = dp.build_national_panel(paths, s, Path(args.raw) / "bps_national" / dp.PANEL_FILE)
    by_name = {r.name: r for r in contest_rules(ledger, rules)}
    named = {}
    for st in block["states_named"]:
        table, info = _state_inputs(str(st), ledger, bsettings)
        named[str(st)] = run_contest(table, ledger, rules, info).rule
    doc = run_study(panel, named, by_name[str(block["other_rule"])], bsettings, block=block,
                    checked=checked)  # fmt: skip
    for v, res in doc["variants"].items():
        p = res["pooled"]
        named_txt = ", ".join(
            f"{k} {x['covered']}/{x['cases']}" for k, x in res["named_states"].items()
        )
        print(f"{v}: pooled {p['covered']}/{p['cases']} = {p['coverage']:.1%} "
              f"width {p['width']:.2f}; {named_txt}; {res['judged']['reason']}")  # fmt: skip
    print(f"decision: {doc['decision']}")
    print(f"wrote {write_document(doc, args.out)}")
    return 0
