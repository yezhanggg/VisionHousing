"""The decision view: the question, the answer by year, the binding constraint, evidence coverage.

Plain-language summary
----------------------
The screens' decision view reads a reform the way a policy analyst would brief it:
the question asked, the answer (with its years), where and why it happens, what to
change next, and how to use it. Most of that is already in the run reply. This module
adds the parts that need arithmetic or a lookup the browser must not do:

- :func:`binding_constraint` finds the funnel stage that loses the largest share of
  the step before it (the share lost is worked out in each Monte Carlo draw from
  the funnel's per-draw step totals; the middle of those draws ranks the stages
  and the P10-P90 is the likely range), names who acts there
  (state, municipality, utility, builder or household) and the next question to ask.
  The stage words, the actors and the questions live in ``assumptions/decision.yaml``
  with ids; reforms without a site funnel (fees, parking) name their one lever.
- :func:`county_coverage` gives each county's evidence coverage: three checks (local
  data behind its number, verified local rules read, a baseline back-test that was not
  far off) and the class they add up to (*Good* / *Partial* / *Thin*).
- :func:`measure_classes` puts each county in a class of the map's other measures
  (homes added per 1,000 existing homes, homes added) by fixed breaks from the same
  file, so the map can switch what it shades without the browser comparing numbers.
- :func:`decision_view` puts these together for a run of one reform: the question's
  definition and dates, the yearly answer (when the engine split it into years), the
  county that gains most, the binding constraint and the coverage counts.

Nothing here invents a number. Shares lost are divisions done draw by draw on the
engine's own step draws, then summarised (never a ratio of two percentiles), with the
division written out; every text form comes from the explanation layer's ``show`` /
``num``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from visionhousing.engine import records as rec
from visionhousing.engine.context import StateContext
from visionhousing.engine.core import Result
from visionhousing.engine.registry import get_reform
from visionhousing.explain import num, show
from visionhousing.explain.numbers import TABLE_SIG
from visionhousing.grades.trace import ratio_draws, ratio_summary, step_draws
from visionhousing.ledger.loader import ROOT

__all__ = [
    "DECISION_PATH",
    "binding_constraint",
    "county_coverage",
    "decision_view",
    "load_decision",
    "measure_classes",
]

DECISION_PATH = ROOT / "assumptions" / "decision.yaml"
MEASURE_CLASSES = ("q1", "q2", "q3", "q4")
"""Class ids of the per-1,000 and homes-added map measures, lowest first."""


@lru_cache(maxsize=4)
def _read(path: str, mtime: int) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_decision(path: Path = DECISION_PATH) -> dict[str, Any]:
    """Read ``assumptions/decision.yaml`` (cached until the file changes)."""
    p = Path(path)
    return _read(str(p), p.stat().st_mtime_ns)


def _mid(r: rec.Record) -> float | None:
    """A record's middle estimate: P50 for a range, the value for a single number."""
    v = r.p50 if r.p50 is not None else r.value
    return None if v is None or not math.isfinite(float(v)) else float(v)


def _shown(r: rec.Record) -> dict[str, str]:
    if r.p50 is None:
        return {"value": show(r.value, r.unit, TABLE_SIG)} if r.value is not None else {}
    return {k: show(getattr(r, k), r.unit, TABLE_SIG) for k in ("p10", "p50", "p90")}


def _rec(r: rec.Record) -> dict[str, Any]:
    """A record as the screens read it (values, text forms, formula, ids)."""
    return {
        "name": r.name,
        "unit": r.unit,
        "value": r.value,
        "p10": r.p10,
        "p50": r.p50,
        "p90": r.p90,
        "shown": _shown(r),
        "formula": r.formula,
        "tier": r.tier,
        "assumption_ids": list(r.assumption_ids),
        "source_ids": list(r.source_ids),
    }


def _find(names: Sequence[str], words: str) -> int | None:
    words = words.lower()
    return next((i for i, n in enumerate(names) if words in n), None)


def _label(step: rec.Record) -> str:
    """A step name without its scope in brackets: "lots with room for an ADU"."""
    return step.name.split(" (")[0]


def binding_constraint(
    outcome: Any, family: str, cfg: Mapping[str, Any] | None = None
) -> dict | None:
    """The funnel stage that loses the most, grouped by who acts there.

    Args:
        outcome: A :class:`~visionhousing.engine.core.ReformOutcome` (its ``steps``).
        family: The reform's family (``adu`` for the ADU bills too).
        cfg: The decision file (default: ``assumptions/decision.yaml``).

    Returns:
        ``None`` when no stage of the file matches the reform's steps. Otherwise
        ``{"rule", "rule_id", "kind", "binding", "stages", "by_actor"}``: every
        matched stage with its ``lost`` share (``1 - after / before`` computed
        in each draw from the funnel's per-draw step totals, then the P50 over
        the draws, with ``lost_p10`` / ``lost_p90``, ``lost_text``,
        ``lost_range_text`` and the ``formula``; never a ratio of two
        percentiles) and its actor, the binding one, and the stages grouped by
        actor (largest loss first).
        ``kind`` is ``"loss"`` for a site funnel and ``"lever"`` for a reform that
        works through one lever (fees, parking), whose stage carries the lever's
        value instead of a loss.
    """
    cfg = cfg or load_decision()
    actors = cfg["actors"]
    steps = list(getattr(outcome, "steps", []) or [])
    names = [s.name.lower() for s in steps]
    draws = step_draws(outcome)
    rows: list[dict[str, Any]] = []
    for st in cfg["stages"].get(family, []):
        actor = actors[st["actor"]]
        base = {
            "id": st["id"],
            "name": st["name"],
            "actor": st["actor"],
            "actor_word": actor["word"],
            "who": actor["who"],
            "why": st["why"],
            "question": st["question"],
        }
        if "lever" in st:
            i = _find(names, st["lever"])
            if i is None:
                continue
            step = steps[i]
            rows.append({**base, "kind": "lever", "step": _label(step), "value": _rec(step)})
            continue
        a, b = _find(names, st["from"]), _find(names, st["match"])
        if a is None or b is None or draws is None:
            continue
        before, after = _mid(steps[a]), _mid(steps[b])
        if before is None or after is None:
            continue
        kept_draws = np.clip(ratio_draws(draws[b], draws[a]), 0.0, 1.0)
        lost_range = ratio_summary(1.0 - kept_draws)
        if lost_range["p50"] is None:
            continue  # the step before is zero in every draw: nothing to lose
        lost = lost_range["p50"]
        kept = 1.0 - lost
        rows.append(
            {
                **base,
                "kind": "loss",
                "from_label": _label(steps[a]),
                "to_label": _label(steps[b]),
                "before_text": show(before, steps[a].unit, TABLE_SIG),
                "after_text": show(after, steps[b].unit, TABLE_SIG),
                "lost": lost,
                "lost_p10": lost_range["p10"],
                "lost_p90": lost_range["p90"],
                "lost_text": show(lost, "share"),
                "lost_range_text": (
                    f"{show(lost_range['p10'], 'share')} to {show(lost_range['p90'], 'share')}"
                ),
                "draws_defined": lost_range["draws_defined"],
                "kept": kept,
                "kept_text": show(kept, "share"),
                "formula": (
                    f"1 - {_label(steps[b])} / {_label(steps[a])}, in each of "
                    f"{lost_range['draws_defined']:,} draws (capped to 0-1), then the middle "
                    f"(P50) = {num(lost, 3)}; likely range P10 {num(lost_range['p10'], 3)} to "
                    f"P90 {num(lost_range['p90'], 3)}. The steps' own middles: "
                    f"{show(before, steps[a].unit, TABLE_SIG)} and "
                    f"{show(after, steps[b].unit, TABLE_SIG)}"
                ),
            }
        )
    if not rows:
        return None
    losses = [r for r in rows if r["kind"] == "loss"]
    if losses:
        binding = max(losses, key=lambda r: r["lost"])
        sentence = (
            f"The largest loss is at “{binding['name']}”: in the middle draw "
            f"{binding['lost_text']} of {binding['from_label']} do not get there (likely range "
            f"{binding['lost_range_text']}). Who acts: {binding['actor_word'].lower()} "
            f"({binding['who']})."
        )
    else:
        binding = rows[0]
        shown = binding["value"]["shown"].get("value") or binding["value"]["shown"].get("p50", "")
        sentence = (
            f"This reform works through one lever, “{binding['name']}” "
            f"({binding['step'].lower()}: {shown}). Who acts: {binding['actor_word'].lower()} "
            f"({binding['who']})."
        )
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(
            r["actor"],
            {
                "actor": r["actor"],
                "word": r["actor_word"],
                "who": r["who"],
                "stages": [],
                "lost": None,
            },
        )
        g["stages"].append(r["name"])
        if r["kind"] == "loss" and (g["lost"] is None or r["lost"] > g["lost"]):
            g["lost"], g["lost_text"] = r["lost"], r["lost_text"]
    by_actor = sorted(groups.values(), key=lambda g: -(g["lost"] if g["lost"] is not None else -1))
    return {
        "rule": cfg["binding"]["rule"],
        "rule_id": cfg["binding"]["id"],
        "kind": "loss" if losses else "lever",
        "binding": {**binding, "sentence": sentence},
        "stages": rows,
        "by_actor": by_actor,
    }


def _backtest_table(ctx: StateContext, result: Result, folder: Path | None):
    """The baseline's back-test by county (None when the run has no context)."""
    rc = getattr(result, "run_context", None)
    if rc is None:
        return None, {}
    from visionhousing.baseline.places import load_place_rules, place_backtest_for_context

    try:
        pb = place_backtest_for_context(ctx, rc.baseline.rule, folder)
    except Exception:  # a state without the tables the breakdown needs
        return None, {}
    return pb.counties, load_place_rules()


def county_coverage(
    ctx: StateContext,
    result: Result,
    county_ids: Sequence[str],
    folder: Path | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Each county's evidence coverage: the three checks and the class they add up to.

    Returns:
        County id to ``{"class", "word", "met", "checks": [{id, label, met, text}]}``.
        ``met`` is how many checks the county meets.
    """
    cfg = cfg or load_decision()
    cov = cfg["coverage"]
    classes = sorted(cov["classes"], key=lambda c: -int(c["min_checks"]))
    law = getattr(ctx, "local_law", None)
    table, rules = _backtest_table(ctx, result, folder)
    far = float(rules.get("name_few_cases_beyond", 0.33)) if rules else 0.33
    rows = {}
    if table is not None and len(table):
        rows = {str(r["county"]): r for _, r in table.iterrows()}
    pkg = result.package if result.reform_ids else None
    out: dict[str, dict[str, Any]] = {}
    for c in county_ids:
        r = (pkg.counties.get(c) if pkg is not None else None) or result.baseline_counties.get(c)
        tier = None if r is None else r.tier
        local = tier is not None and tier >= int(cov["local_tier"])
        checks = [
            {
                "id": "local_data",
                "label": "Local data",
                "met": local,
                "text": f"Data tier {tier}" if tier is not None else "No data tier",
            }
        ]
        share = None
        if law is not None and hasattr(law, "lot_coverage"):
            try:
                share = law.lot_coverage(c)
            except Exception:
                share = None
        bar = float(getattr(law, "min_coverage", 0.5)) if law is not None else 0.5
        checks.append(
            {
                "id": "rules_read",
                "label": "Rules read",
                "met": share is not None and share >= bar,
                "text": (
                    f"Verified rules cover {show(share, 'share')} of single-family lots"
                    if share is not None
                    else "No verified local rules read"
                ),
            }
        )
        bt = rows.get(str(c))
        pct = None if bt is None else float(bt.get("typical_pct", math.nan))
        scored = (
            bt is not None
            and int(bt.get("cases", 0) or 0) > 0
            and pct is not None
            and math.isfinite(pct)
        )
        checks.append(
            {
                "id": "backtest",
                "label": "Back-test",
                "met": bool(scored and abs(pct) < far),
                "text": (
                    f"Back-test: typically {show(abs(pct), 'share')} "
                    f"{'high' if pct > 0 else 'low'} over {int(bt['cases'])} past start years"
                    if scored
                    else "No back-test case"
                ),
            }
        )
        met = sum(1 for k in checks if k["met"])
        cls = next(k for k in classes if met >= int(k["min_checks"]))
        out[c] = {"class": cls["id"], "word": cls["word"], "met": met, "checks": checks}
    return out


def _break_label(lo: float | None, hi: float | None) -> str:
    if lo is None:
        return f"under {num(hi)}"
    if hi is None:
        return f"{num(lo)} or more"
    return f"{num(lo)} to under {num(hi)}"


def measure_classes(
    counties: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any] | None = None
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Classes of the map's other measures by fixed breaks.

    Args:
        counties: The map reply's county rows (``per_1000`` and ``added`` records as dicts).
        cfg: The decision file (default: ``assumptions/decision.yaml``).

    Returns:
        ``(measures, by_county)``: each measure's id, title, rule id and classes
        (``id``, ``word``, ``label``), and county id to ``{measure id: class id}``
        (``nodata`` where the county has no such number).
    """
    cfg = cfg or load_decision()
    measures = []
    by_county: dict[str, dict[str, str]] = {str(c["fips"]): {} for c in counties}
    for mid, m in cfg["measures"].items():
        breaks = [float(b) for b in m["breaks"]]
        edges = [None, *breaks, None]
        classes = [
            {"id": MEASURE_CLASSES[i], "word": "", "label": _break_label(edges[i], edges[i + 1])}
            for i in range(len(breaks) + 1)
        ]
        measures.append({"id": mid, "title": m["title"], "rule_id": m["id"], "classes": classes})
        for c in counties:
            r = c.get(mid)
            v = None if not r else r.get("p50")
            if v is None or not math.isfinite(float(v)):
                by_county[str(c["fips"])][mid] = "nodata"
                continue
            k = sum(1 for b in breaks if float(v) >= b)
            by_county[str(c["fips"])][mid] = MEASURE_CLASSES[k]
    return measures, by_county


def coverage_summary(
    coverage: Mapping[str, Mapping[str, Any]], cfg: Mapping[str, Any] | None = None
) -> dict:
    """How many counties are in each coverage class, with one sentence."""
    cfg = cfg or load_decision()
    classes = cfg["coverage"]["classes"]
    total = len(coverage)
    counts = []
    for k in classes:
        n = sum(1 for v in coverage.values() if v["class"] == k["id"])
        counts.append(
            {
                "id": k["id"],
                "word": k["word"],
                "label": k["label"],
                "count": n,
                "text": f"{n:,} of {total:,}",
            }
        )
    parts = [f"{c['count']:,} {c['word'].lower()}" for c in counts]
    return {
        "rule": cfg["coverage"]["rule"],
        "rule_id": cfg["coverage"]["id"],
        "counts": counts,
        "total": total,
        "sentence": f"Evidence coverage across {total:,} counties: {', '.join(parts)}.",
    }


def _definition(key: str, ctx: StateContext) -> dict[str, str] | None:
    from visionhousing.ledger import load_scenarios

    try:
        sc = load_scenarios(ctx.ledger).get(key)
    except Exception:
        return None
    if sc is None:
        return None
    d = sc.model_dump(mode="json")
    return {"text": d.get("description") or "", "handbook_text": d.get("handbook_text") or ""}


def decision_view(
    ctx: StateContext,
    result: Result,
    counties: Sequence[Mapping[str, Any]],
    coverage: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """The decision view of a run of exactly one reform (``None`` otherwise).

    Args:
        ctx: The state context the run used.
        result: The run.
        counties: The map reply's county rows (for the county that gains most).
        coverage: :func:`county_coverage` of the same run.

    Returns:
        ``{"reform", "title", "question", "annual", "net", "top_county",
        "binding", "coverage"}``. ``annual`` is ``None`` when the engine did not
        split the result into years; ``net`` is ``None`` when no home is removed.
    """
    if len(result.reform_ids) != 1:
        return None
    key = result.reform_ids[0]
    o = result.reforms.get(key)
    if o is None:
        return None
    spec = get_reform(key)
    contract = getattr(result, "contract", None)
    as_of = None
    if isinstance(contract, Mapping):
        as_of = contract.get("as_of")
    elif contract is not None:
        as_of = getattr(contract, "as_of", None)
    custom = len(result.overrides or {})
    question = {
        "window": "2027\u20132031",
        "measure": "additional homes authorized by building permits",
        "as_of": as_of,
        "settings": (
            "Benchmark settings (every assumption across its sourced range)"
            if not custom
            else f"Your settings: {custom} assumption{'s' if custom != 1 else ''} changed"
        ),
        "definition": _definition(key, ctx),
    }
    annual = None
    series = getattr(o, "annual", None)
    if series is not None and getattr(series, "state", None):
        timing = getattr(series, "timing", None) or {}
        annual = {
            "label": series.label,
            "years": [
                {"year": int(y), **_rec(r)}
                for y, r in zip(series.years, series.state, strict=False)
            ],
            "note": timing.get("note") if isinstance(timing, Mapping) else None,
        }
    net = None
    gn = getattr(o, "gross_net", None) or {}
    if "removed" in gn and "net_stock" in gn:
        net = {"net_stock": _rec(gn["net_stock"]), "removed": _rec(gn["removed"])}
    have = [c for c in counties if c.get("added")]
    top = max(have, key=lambda c: (c["added"]["p50"], c["name"])) if have else None
    return {
        "reform": key,
        "title": spec.title,
        "family": spec.family,
        "question": question,
        "annual": annual,
        "net": net,
        "top_county": (
            {
                "fips": top["fips"],
                "name": top["name"],
                "added": top["added"],
                "per_1000": top.get("per_1000"),
            }
            if top
            else None
        ),
        "binding": binding_constraint(o, spec.family),
        "coverage": coverage_summary(coverage),
    }
