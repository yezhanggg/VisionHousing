"""Growth-aware coverage study: do fast-growing states miss on one side?

Plain-language summary
----------------------
The donor-pool tests (:mod:`visionhousing.validation.donor_pool`) found that the
baseline's likely ranges for small, few-county states caught the real five-year
total much less often than the 80% they aim for, and the misses were worst in
fast-growing states. A possible reason: the scored rule projects the mean of the
last three years, so a state that has been growing for years keeps building
above that mean, and its real totals land above the top of the range again and
again (a one-sided miss).

This module checks that in two steps.

1. **Diagnosis** (describes, decides nothing). Every state total in the national
   county panel gets a *growth class* at each past start year (*origin*): how
   its permits over the five years up to the origin compare with the five years
   before, ranked against every other state at the same origin and cut into
   three equal groups (slow, middle, fast). For each class the study reports
   how often the current range caught the real total, and whether the misses
   fell above the range or below it.
2. **A pre-registered test** of a growth-aware widening. The candidate keeps
   the current range and only ever widens it toward the spread of past errors
   of states in the same growth class (pooled nationally, the held-out state
   left out). The candidate, the held-out evaluation and the adoption rule are
   fixed in the block ``growth_coverage_test`` of
   ``assumptions/validation_donor_pool.yaml``; its fingerprint (SHA-256) was
   logged before the candidate was scored and is repeated in the results.

The command ``visionhousing validate-growth-coverage`` writes
``assumptions/calibration/growth_coverage.yaml``. Nothing here changes a
baseline run; the baseline functions are used read-only.

Method in full: ``docs/modules/validation_donor_pool.md`` (section "A third
test: growth-aware coverage").
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

from visionhousing.baseline.backtest import ratio_quantiles
from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.baseline.rules import Rule
from visionhousing.baseline.settings import BaselineSettings
from visionhousing.validation import donor_pool as dp

__all__ = [
    "GROWTH_KEY",
    "OUT_PATH",
    "class_ends",
    "class_pools",
    "decide_growth",
    "diagnose",
    "growth_classes",
    "implied_ranges",
    "run_growth_study",
    "score_cases",
    "state_growth",
    "write_document",
]

GROWTH_KEY = "growth_coverage_test"
OUT_PATH = dp.ROOT / "assumptions" / "calibration" / "growth_coverage.yaml"

HEADER = """\
# Calibration (validation): growth-aware coverage study (generated; do not edit).
# Written by `visionhousing validate-growth-coverage` from the Census Building
# Permits Survey county files of every state. How often the baseline's state
# range caught the real five-year total, by growth class; a pre-registered test
# of a growth-aware widening for sparse states. If the decision is 'adopt', the
# baseline reads decision.outcome and sparse_states (growth_widen in backtest.yaml).
# Method: docs/modules/validation_donor_pool.md.
"""


# --------------------------------------------------------------------------- growth classes


def state_growth(
    panel: pd.DataFrame, origins: Sequence[int], block: Mapping[str, Any]
) -> pd.DataFrame:
    """Each state total's growth at each origin, from years up to the origin only.

    growth = log((mean yearly units, last ``recent_years`` years to the origin + 1)
    / (mean yearly units, the ``base_years`` before them + 1)); a year counts when
    at least one county of the state reported a figure.

    Returns:
        Columns ``state``, ``origin``, ``growth`` (``NaN`` when a window is empty).
    """
    c = block["classes"]
    years = np.arange(int(panel["year"].min()), int(panel["year"].max()) + 1)
    tot = panel.assign(total=panel[list(STRUCTURE_TYPES)].sum(axis=1, min_count=1))
    by = tot.groupby(["state", "year"])["total"].sum(min_count=1)
    states = sorted(tot["state"].unique())
    t = by.unstack("year").reindex(index=states, columns=years).to_numpy(dtype=float)
    feats = dp.area_features(
        t,
        np.zeros_like(t),
        years,
        [int(o) for o in origins],
        {
            "size_years": int(c["recent_years"]),
            "growth_recent_years": int(c["recent_years"]),
            "growth_base_years": int(c["base_years"]),
            "completeness_years": 1,
        },
    )
    g = feats["growth"]
    rows = [
        {"state": s, "origin": int(o), "growth": float(g[i, j])}
        for i, s in enumerate(states)
        for j, o in enumerate(origins)
    ]
    return pd.DataFrame(rows)


def growth_classes(growth: pd.DataFrame, block: Mapping[str, Any]) -> pd.DataFrame:
    """Cut each origin's states into ``n_classes`` equal groups by growth.

    States are ranked at each origin by growth (smallest first, ties by postal
    code); a state of rank r (0-based) among n gets class floor(n_classes x r / n):
    0 = slowest. States with no growth value get no class. Only states in the
    block's ``classes.states`` list are ranked. The result depends only on the
    values, never on row order.

    Returns:
        ``growth`` plus an integer ``growth_class`` column (-1 = no class).
    """
    c = block["classes"]
    k = int(c["n_classes"])
    keep = set(c["states"])
    g = growth[growth["state"].isin(keep)].copy()
    g["growth_class"] = -1
    out = []
    for _, part in g.groupby("origin", sort=True):
        ok = part[np.isfinite(part["growth"])].sort_values(["growth", "state"], kind="mergesort")
        n = len(ok)
        cls = pd.Series(-1, index=part.index)
        if n:
            cls.loc[ok.index] = [int(k * r // n) for r in range(n)]
        out.append(part.assign(growth_class=cls.astype(int)))
    if not out:
        return g
    return pd.concat(out).sort_values(["state", "origin"]).reset_index(drop=True)


# --------------------------------------------------------------------------- ranges


def class_ends(ratios: np.ndarray, min_cases: int) -> tuple[float, float, int] | None:
    """(log P10, log P90, n) of the class pool's raw ratios, or ``None`` if too few.

    Raw, not divided by the pool's median: a class whose real totals run above
    the projection keeps that shift, so the widening can be one-sided.
    """
    r = np.asarray(ratios, dtype=float)
    r = r[np.isfinite(r) & (r > 0)]
    if r.size < int(min_cases):
        return None
    q = ratio_quantiles(r, (0.1, 0.9))
    if q is None or not q[0] > 0:
        return None
    return math.log(q[0]), math.log(q[1]), int(r.size)


def _widen(cur: tuple[float, float], pool: tuple[float, float], w: float) -> tuple[float, float]:
    """Blend each current end toward the pool's (weight w on the current); only ever widen."""
    low = min(cur[0], w * cur[0] + (1 - w) * pool[0])
    high = max(cur[1], w * cur[1] + (1 - w) * pool[1])
    return low, high


def _case_ranges(
    cases: dp.AreaCases,
    classes: pd.DataFrame,
    state: str,
    origin: int,
    settings: Mapping[str, Any],
    block: Mapping[str, Any],
    *,
    with_candidate: bool,
) -> dict[str, Any] | None:
    """Current (and, if asked, candidate) range ends for one state and origin, out of time."""
    done = cases.completed_by(origin)
    st = cases.states[(cases.states["origin"] <= done) & np.isfinite(cases.states["ratio"])]
    co = cases.counties[(cases.counties["origin"] <= done) & np.isfinite(cases.counties["ratio"])]
    min_cases = int(settings["min_cases"])
    own_r = st[st["area"] == state]["ratio"].to_numpy()
    pa_r = st[st["area"] == settings["prior_state"]]["ratio"].to_numpy()
    own_r, pa_r = own_r[own_r > 0], pa_r[pa_r > 0]
    if own_r.size < min_cases or pa_r.size < min_cases:
        return None
    own, pa = dp._ends(own_r), dp._ends(pa_r)
    if own is None or pa is None:
        return None
    w = min(1.0, int((co["state"] == state).sum()) / float(settings["full_weight_cases"]))
    cur = dp._blend(own, pa, w)
    out: dict[str, Any] = {"w": w, "current": (cur[0], cur[1]), "own": (own[0], own[1])}
    if with_candidate:
        cls = classes[(classes["state"] == state) & (classes["origin"] == origin)]
        my_class = int(cls["growth_class"].iloc[0]) if len(cls) else -1
        pool_n, cand = 0, (cur[0], cur[1])
        if my_class >= 0:
            lab = classes[["state", "origin", "growth_class"]].rename(columns={"state": "area"})
            pool = st[st["area"] != state].merge(lab, on=["area", "origin"], how="inner")
            pool = pool[pool["growth_class"] == my_class]
            ce = class_ends(pool["ratio"].to_numpy(), int(block["candidate"]["min_pool_cases"]))
            if ce is not None:
                pool_n = ce[2]
                cand = _widen((cur[0], cur[1]), (ce[0], ce[1]), w)
        out["candidate"], out["pool"] = cand, pool_n
    return out


def score_cases(
    cases: dp.AreaCases,
    classes: pd.DataFrame,
    states: Sequence[str],
    settings: Mapping[str, Any],
    block: Mapping[str, Any],
    *,
    with_candidate: bool,
) -> pd.DataFrame:
    """Score the current range (and the candidate) on every out-of-time case of ``states``.

    Returns:
        One row per state and origin: ``growth_class``, ``projected``, ``actual``,
        ``signed_log_error`` (log(actual / projected); above 0 = built more than
        projected), ``w``, and per method (``current``, ``candidate``) the range
        ``*_low``/``*_high``, ``*_inside``, ``*_above`` (real total above the
        top), ``*_below`` and ``*_width`` ((P90 - P10) / P50).
    """
    lab = classes.set_index(["state", "origin"])["growth_class"]
    methods = ["current", "candidate"] if with_candidate else ["current"]
    rows = []
    for state in states:
        mine = cases.states[cases.states["area"] == state].sort_values("origin")
        for _, case in mine.iterrows():
            proj, act = float(case["projected"]), float(case["actual"])
            if not (proj > 0 and act > 0):
                continue
            o = int(case["origin"])
            got = _case_ranges(
                cases, classes, state, o, settings, block, with_candidate=with_candidate
            )
            if got is None:
                continue
            row: dict[str, Any] = {
                "state": state,
                "origin": o,
                "growth_class": int(lab.get((state, o), -1)),
                "projected": proj,
                "actual": act,
                "signed_log_error": math.log(act / proj),
                "w": got["w"],
            }
            if with_candidate:
                row["pool"] = got["pool"]
            for m in methods:
                lo, hi = proj * math.exp(got[m][0]), proj * math.exp(got[m][1])
                row.update(
                    {
                        f"{m}_low": lo,
                        f"{m}_high": hi,
                        f"{m}_inside": bool(lo <= act <= hi),
                        f"{m}_above": bool(act > hi),
                        f"{m}_below": bool(act < lo),
                        f"{m}_width": math.exp(got[m][1]) - math.exp(got[m][0]),
                    }
                )
            rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- diagnosis


def _table(scored: pd.DataFrame, method: str, names: Mapping[int, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    groups = [(names.get(int(c), str(c)), g) for c, g in scored.groupby("growth_class", sort=True)]
    groups.append(("all", scored))
    for name, g in groups:
        if g.empty:
            continue
        misses = int((~g[f"{method}_inside"]).sum())
        out[name] = {
            "cases": len(g),
            "states": int(g["state"].nunique()),
            "coverage": float(g[f"{method}_inside"].mean()),
            "above_share": float(g[f"{method}_above"].mean()),
            "below_share": float(g[f"{method}_below"].mean()),
            "misses_above_share_of_misses": float(g[f"{method}_above"].sum() / misses)
            if misses
            else None,
            "mean_signed_log_error": float(g["signed_log_error"].mean()),
            "median_signed_log_error": float(g["signed_log_error"].median()),
            "width": float(g[f"{method}_width"].mean()),
        }
    return out


def _class_names(block: Mapping[str, Any]) -> dict[int, str]:
    return {i: str(n) for i, n in enumerate(block["classes"]["names"])}


def diagnose(
    scored: pd.DataFrame, sparse: Sequence[str], block: Mapping[str, Any]
) -> dict[str, Any]:
    """Coverage and signed error of the current range by growth class (descriptive only).

    Returns ``{"all_states": table, "sparse_states": table}``; each table gives,
    per class and overall: cases, states, coverage, the share of cases above and
    below the range, the share of misses that fell above, the mean and median of
    log(actual / projected), and the mean width.
    """
    names = _class_names(block)
    classed = scored[scored["growth_class"] >= 0]
    return {
        "all_states": _table(classed, "current", names),
        "sparse_states": _table(classed[classed["state"].isin(set(sparse))], "current", names),
    }


# --------------------------------------------------------------------------- decision


def _stats(scored: pd.DataFrame, method: str, target: float) -> dict[str, Any]:
    if scored.empty:
        return {"states": 0, "cases": 0, "mean_gap": None, "coverage": None, "width": None}
    by = scored.groupby("state")[f"{method}_inside"].mean()
    return {
        "states": len(by),
        "cases": len(scored),
        "mean_gap": float(np.mean(np.abs(by.to_numpy() - target))),
        "coverage": float(scored[f"{method}_inside"].mean()),
        "width": float(scored[f"{method}_width"].mean()),
    }


def decide_growth(
    scored: pd.DataFrame,
    decision_states: Sequence[str],
    pa_scored: pd.DataFrame,
    settings: Mapping[str, Any],
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the pre-registered adoption rule (``growth_coverage_test.decision``).

    Adopt only if all hold, out of time:
      1. gap(candidate) < gap(current), both rounded to ``gap_decimals`` (a tie keeps current);
      2. the candidate's pooled coverage over the decision states is at most
         ``max_pooled_coverage`` (coverage below the band does not reject);
      3. the prior state's coverage: if the current rule's is inside ``pa_band``,
         the candidate's must be inside too; if it is already outside, the
         candidate's must not be further from the target.
    At least ``min_states_scored`` decision states must be scored, or the test is
    inconclusive and the current rule stays.
    """
    d = block["decision"]
    target = float(settings["target_coverage"])
    nd = int(d["gap_decimals"])
    cap = float(d["max_pooled_coverage"])
    band = [float(x) for x in d["pa_band"]]
    rows = scored[scored["state"].isin(set(decision_states))]
    c, k = _stats(rows, "candidate", target), _stats(rows, "current", target)
    pc, pk = _stats(pa_scored, "candidate", target), _stats(pa_scored, "current", target)
    table = {"current": k, "candidate": c, "prior_state_current": pk, "prior_state_candidate": pc}
    if c["states"] < int(d["min_states_scored"]):
        return {
            "outcome": "inconclusive",
            "states_scored": c["states"],
            "methods": table,
            "reason": f"only {c['states']} sparse states could be scored; the current rule stays",
        }
    gc, gk = round(c["mean_gap"], nd), round(k["mean_gap"], nd)
    beats = gc < gk
    under_cap = c["coverage"] <= cap
    if pk["coverage"] is None or pc["coverage"] is None:
        pa_ok = False
    elif band[0] <= pk["coverage"] <= band[1]:
        pa_ok = band[0] <= pc["coverage"] <= band[1]
    else:
        pa_ok = abs(pc["coverage"] - target) <= abs(pk["coverage"] - target)
    adopt = beats and under_cap and pa_ok
    fails = []
    if not beats:
        fails.append(f"mean gap not lower ({gc:.3f} vs {gk:.3f})")
    if not under_cap:
        fails.append(f"pooled coverage {c['coverage']:.1%} above {cap:.0%}")
    if not pa_ok:
        fails.append("the prior state's coverage moved outside its allowed range")
    reason = (
        f"candidate beats the current rule (mean gap {gc:.3f} vs {gk:.3f}), pooled coverage "
        f"{c['coverage']:.1%} is not above {cap:.0%}, and the prior state's coverage stays allowed"
        if adopt
        else "; ".join(fails) + "; the current widening stays"
    )
    return {
        "outcome": "adopt" if adopt else "keep_current",
        "states_scored": c["states"],
        "beats_current": bool(beats),
        "pooled_coverage_not_above_cap": bool(under_cap),
        "prior_state_ok": bool(pa_ok),
        "methods": table,
        "reason": reason,
    }


# --------------------------------------------------------------------------- real-run ranges


def implied_ranges(
    cases: dp.AreaCases,
    panel: pd.DataFrame,
    classes_last: pd.DataFrame,
    states: Sequence[str],
    rule: Rule,
    counts: Mapping[str, int],
    settings: Mapping[str, Any],
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """Each state's likely range for a real run, under the current rule and the candidate.

    The origin is the last data year; every case completed by then is used.
    ``classes_last`` holds every state's growth class at the last year, and the
    class pool takes other states' cases in the same class at their own origin.
    """
    last = int(panel["year"].max())
    done = cases.completed_by(last)
    st = cases.states[(cases.states["origin"] <= done) & np.isfinite(cases.states["ratio"])]
    growth = state_growth(panel, list(cases.origins), block)
    lab = growth_classes(growth, block)[["state", "origin", "growth_class"]]
    names = _class_names(block)
    full = float(settings["full_weight_cases"])
    out: dict[str, Any] = {}
    for state in states:
        own = dp._ends(st[st["area"] == state]["ratio"].to_numpy())
        pa = dp._ends(st[st["area"] == settings["prior_state"]]["ratio"].to_numpy())
        if own is None or pa is None or state not in counts:
            continue
        w = min(1.0, counts[state] / full)
        cur = dp._blend(own, pa, w)
        mine = classes_last[classes_last["state"] == state]
        cls = int(mine["growth_class"].iloc[0]) if len(mine) else -1
        cand, pool_n = (cur[0], cur[1]), 0
        if cls >= 0:
            pool = st[st["area"] != state].merge(
                lab.rename(columns={"state": "area"}), on=["area", "origin"], how="inner"
            )
            ce = class_ends(
                pool[pool["growth_class"] == cls]["ratio"].to_numpy(),
                int(block["candidate"]["min_pool_cases"]),
            )
            if ce is not None:
                pool_n, cand = ce[2], _widen((cur[0], cur[1]), (ce[0], ce[1]), w)
        p50 = dp._state_projection(panel, state, rule, last, cases.lead, cases.horizon)
        first = last + cases.lead
        entry: dict[str, Any] = {
            "years": [first, first + cases.horizon - 1],
            "origin": last,
            "growth_class": names.get(cls, "none"),
            "growth_log": float(mine["growth"].iloc[0]) if len(mine) else None,
            "weight_own": round(w, 4),
            "class_pool_cases": pool_n,
            "p50_units": round(p50),
        }
        for name, (lo_l, hi_l) in (("current", (cur[0], cur[1])), ("candidate", cand)):
            lo, hi = math.exp(lo_l), math.exp(hi_l)
            entry[name] = {
                "low_units": round(p50 * lo),
                "high_units": round(p50 * hi),
                "low_ratio": round(lo, 4),
                "high_ratio": round(hi, 4),
                "width": round(hi - lo, 4),
            }
        out[state] = entry
    return out


def class_pools(
    cases: dp.AreaCases,
    panel: pd.DataFrame,
    classes_last: pd.DataFrame,
    counts: Mapping[str, int],
    settings: Mapping[str, Any],
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """What a real run would borrow: each sparse ranked state's class pool at the last year.

    For every ranked state with fewer county-origin cases than full_weight_cases:
    its growth class at the last data year, and the raw ratio P10 and P90 of the
    pool (other ranked states' completed cases in the same class at their own
    origin), with the pool size and the weight on the state's own range.
    """
    last = int(panel["year"].max())
    done = cases.completed_by(last)
    st = cases.states[(cases.states["origin"] <= done) & np.isfinite(cases.states["ratio"])]
    lab = growth_classes(state_growth(panel, list(cases.origins), block), block)
    labelled = st.merge(
        lab[["state", "origin", "growth_class"]].rename(columns={"state": "area"}),
        on=["area", "origin"],
        how="inner",
    )
    names = _class_names(block)
    full = float(settings["full_weight_cases"])
    out: dict[str, Any] = {}
    for _, row in classes_last.sort_values("state").iterrows():
        s, cls = str(row["state"]), int(row["growth_class"])
        if cls < 0 or s not in counts or counts[s] >= full:
            continue
        pool = labelled[(labelled["area"] != s) & (labelled["growth_class"] == cls)]
        ce = class_ends(pool["ratio"].to_numpy(), int(block["candidate"]["min_pool_cases"]))
        out[s] = {
            "growth_class": names.get(cls, str(cls)),
            "growth_log": float(row["growth"]),
            "weight_own": min(1.0, counts[s] / full),
            "pool_cases": ce[2] if ce else len(pool),
            "pool_ratio_p10": math.exp(ce[0]) if ce else None,
            "pool_ratio_p90": math.exp(ce[1]) if ce else None,
        }
    return out


# --------------------------------------------------------------------------- study


def decision_states(counts: Mapping[str, int], settings: Mapping[str, Any]) -> list[str]:
    """Every sparse state (fewer county-origin cases than full_weight_cases), minus ``exclude``."""
    block = settings[GROWTH_KEY]
    full = float(settings["full_weight_cases"])
    skip = set(block["evaluation"]["exclude"])
    return sorted(
        s for s in settings["states"] if s in counts and counts[s] < full and s not in skip
    )


def run_growth_study(
    panel: pd.DataFrame,
    settings: Mapping[str, Any],
    bsettings: BaselineSettings,
    rule: Rule,
    *,
    as_of: str | None = None,
    with_candidate: bool = True,
    check_preregistration: bool = True,
) -> dict[str, Any]:
    """Run the diagnosis and (if ``with_candidate``) the pre-registered test.

    Before anything is scored, the shared rules (``donor_pool_rules``) and this
    test's block are checked against ``assumptions/preregistration.yaml``; a
    changed rule stops the run. ``check_preregistration=False`` is for
    synthetic test settings only.
    """
    checked = []
    if check_preregistration:
        from visionhousing.validation.preregistration import verify

        checked = [verify(dp.RULES_BLOCK, settings), verify(GROWTH_KEY, settings)]
    block = settings[GROWTH_KEY]
    cases = dp.area_cases(panel, rule, bsettings, settings)
    counts = dp._state_case_counts(cases)
    growth = state_growth(panel, list(cases.origins), block)
    classes = growth_classes(growth, block)
    every = sorted(set(block["classes"]["states"]) & set(cases.states["area"]))
    scored = score_cases(cases, classes, every, settings, block, with_candidate=with_candidate)
    sparse = decision_states(counts, settings)
    doc: dict[str, Any] = {
        "version": 1,
        "id": "growth_coverage_2026_11",
        "label": "Growth-aware coverage study (hold-out-state back-test)",
        "status": "validation",
        "use": (
            "If decision.outcome is 'adopt', the baseline widens each listed sparse state's "
            "range toward its growth class's pooled error record (sparse_states; growth_widen "
            "in assumptions/backtest.yaml; docs/modules/baseline.md)."
        ),
        "provenance": {
            "summary": (
                "Coverage and signed error of the baseline's state range by pre-origin growth "
                "class, all states, out of time; and a pre-registered test of a growth-aware "
                "widening for sparse states."
            ),
            "measured_on": as_of or date.today().isoformat(),
            "method": (
                "National county panel from the BPS annual county files (settings in "
                "assumptions/validation_donor_pool.yaml, block growth_coverage_test); back-test "
                "cases from visionhousing.baseline (read-only); see "
                "docs/modules/validation_donor_pool.md."
            ),
            "generated_by": "visionhousing validate-growth-coverage",
            "limits": [
                "The sparse states were already seen by the two earlier tests, so this test is "
                "not on fresh states; only the candidate and its rule are new.",
                "Origins share the national housing cycle (one boom and bust), so growth "
                "classes and errors move together across states.",
                "Each state is scored on about a dozen overlapping five-year windows.",
            ],
        },
        "preregistration": {
            "block": GROWTH_KEY,
            "sha256": dp.preregistration_sha256(block),
            "fingerprint": "SHA-256 of the block as JSON, keys sorted, separators (',', ':')",
            "checks": checked,
        },
        "settings": {
            "rule": settings["rule"],
            "lead_years": cases.lead,
            "horizon_years": cases.horizon,
            "origins": [int(min(cases.origins)), int(max(cases.origins))],
            "full_weight_cases": settings["full_weight_cases"],
        },
        "decision_states": sparse,
        "diagnosis": diagnose(scored, sparse, block),
    }
    if with_candidate:
        prior = str(settings["prior_state"])
        pa_scored = scored[scored["state"] == prior]
        doc["decision"] = decide_growth(scored, sparse, pa_scored, settings, block)
        names = _class_names(block)
        rows = scored[scored["state"].isin(set(sparse)) & (scored["growth_class"] >= 0)]
        doc["by_class"] = {
            "current": _table(rows, "current", names),
            "candidate": _table(rows, "candidate", names),
        }
        target = float(settings["target_coverage"])
        doc["by_state"] = {
            s: {
                "cases": len(g),
                "current_coverage": float(g["current_inside"].mean()),
                "candidate_coverage": float(g["candidate_inside"].mean()),
                "current_width": float(g["current_width"].mean()),
                "candidate_width": float(g["candidate_width"].mean()),
                "gap_current": abs(float(g["current_inside"].mean()) - target),
                "gap_candidate": abs(float(g["candidate_inside"].mean()) - target),
            }
            for s, g in scored[scored["state"].isin(set(sparse) | {prior})].groupby("state")
        }
        last = int(panel["year"].max())
        last_classes = growth_classes(state_growth(panel, [last], block), block)
        doc["implied_range"] = implied_ranges(
            cases, panel, last_classes, list(block["range_states"]), rule, counts, settings, block
        )
        doc["sparse_states"] = class_pools(cases, panel, last_classes, counts, settings, block)
    return dp._clean(doc)


def write_document(doc: Mapping[str, Any], path: Path = OUT_PATH) -> Path:
    """Write the document as YAML with the generated-file header; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(dict(doc), Dumper=dp._Dumper, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path
