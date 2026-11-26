"""Number trace: one headline number taken apart, step by step, from the run's own records.

Plain-language summary
----------------------
:func:`trace` takes a run and a record id (``reform_results.adu.added`` for a
reform's state headline, ``reform_results.adu.counties.42003`` for one county;
ids as in ``explain.ids``) and returns a waterfall: the funnel steps in order,
each with its value (P50 and the P10-P90 range over the run's draws), what was
applied to the step before it, the ledger ids and the sources.

Nothing is re-estimated. The steps are read from the result:

- **Tract-level reforms** (ADU, missing middle, TOD, the ADU bills): the
  reform module's own funnel stages (eligible -> fit -> feasible -> take-up ->
  authorized), summed over the state's or the county's tracts in each draw
  (``ReformOutcome.county_steps`` for a county). The step "applied" is the step
  divided by the one before **in each draw**, reported as P10 / P50 / P90 of those
  per-draw ratios (:func:`step_draws`, :func:`ratio_draws`); it is never a ratio
  of two percentiles. The chain ends at the
  step named "added units authorized ..." (the headline, ``assumptions/grades.yaml``
  ``trace.headline_step_starts``); a later "net added homes" step is shown as a note.
- **County-level reforms** (fee cut, parking), state number: the module's
  explanation steps at central values (each a product, quotient or sum of the
  steps before it, written in its formula), then the likely range over the draws.
- **County-level reforms**, county number: from the module's per-draw arrays:
  the baseline units the saving reaches, the saving per unit, and the added
  units (for parking, the cost channel plus the site-yield gain).

``check`` compares the last step with the number the run reports: for the
funnels they are the same draws, so they match to rounding. ``chain_p50`` is
the first step times every applied ratio, multiplied draw by draw, then the
P50: it rebuilds the reported middle value exactly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from visionhousing.engine.records import DIVIDE, MINUS, TIMES, Record
from visionhousing.grades.rule import load_rule

__all__ = ["TraceError", "parse_id", "ratio_draws", "ratio_summary", "step_draws", "trace"]


class TraceError(ValueError):
    """The record id cannot be traced (unknown reform, county or kind of record)."""


def parse_id(record_id: str, result: Any) -> tuple[str, str | None]:
    """``(reform key, county id or None)`` from a record id.

    Accepts ``reform_results.<key>.added`` (state) and
    ``reform_results.<key>.counties.<fips>`` (county).
    """
    parts = str(record_id).split(".")
    if len(parts) < 3 or parts[0] != "reform_results":
        raise TraceError(
            "trace a reform's headline: reform_results.<key>.added or .counties.<fips>"
        )
    key = parts[1]
    if key not in result.reforms:
        raise TraceError(f"reform '{key}' is not in this run; it has {list(result.reforms)}")
    if parts[2:] == ["added"]:
        return key, None
    if len(parts) == 4 and parts[2] == "counties":
        if parts[3] not in result.reforms[key].counties:
            raise TraceError(f"no county {parts[3]} in this run")
        return key, parts[3]
    raise TraceError("only .added and .counties.<fips> records have a trace")


def _num(x: Any) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else float(x)


def _range(rec: Record) -> dict[str, Any]:
    if rec.is_range:
        return {"p10": rec.p10, "p50": rec.p50, "p90": rec.p90, "value": None}
    return {"p10": None, "p50": None, "p90": None, "value": rec.value}


def _mid(step: Mapping[str, Any]) -> float | None:
    return _num(step["p50"] if step.get("p50") is not None else step.get("value"))


def _new_ids(ids: Sequence[str], seen: set[str]) -> list[str]:
    fresh = [i for i in ids if i not in seen]
    seen.update(ids)
    return fresh


def _pct(arr: np.ndarray) -> tuple[float, float, float]:
    p10, p50, p90 = np.percentile(np.asarray(arr, dtype=float), [10, 50, 90])
    return float(p10), float(p50), float(p90)


def step_draws(outcome: Any, county: str | None = None) -> list[np.ndarray] | None:
    """Per-draw totals of each funnel step, in the order of ``outcome.steps``.

    Read-only from the reform module's own stage arrays (draws x tracts),
    summed over the state's tracts (or one county's) in each draw: the same
    draws the step records summarise. ``None`` for a county-level reform or a
    run that kept no stage arrays.
    """
    res = getattr(outcome, "result", None)
    detail = getattr(res, "detail", None)
    stages = getattr(detail, "stages", None)
    if getattr(outcome, "level", "county") != "tract" or not isinstance(stages, Mapping):
        return None
    if len(stages) != len(outcome.steps):
        return None
    if county is None:
        cols: Any = slice(None)
    else:
        cols = np.flatnonzero(np.asarray([str(c) for c in detail.counties]) == str(county))
    return [np.asarray(a, dtype=float)[:, cols].sum(axis=1) for a in stages.values()]


def ratio_draws(after: np.ndarray, before: np.ndarray) -> np.ndarray:
    """``after / before`` in each draw; NaN where the step before is zero (no ratio)."""
    a, b = np.asarray(after, dtype=float), np.asarray(before, dtype=float)
    out = np.full(a.shape, np.nan)
    ok = b > 0
    out[ok] = a[ok] / b[ok]
    return out


def ratio_summary(ratios: np.ndarray) -> dict[str, Any]:
    """P10 / P50 / P90 of a per-draw ratio over the draws where it is defined.

    ``draws_defined`` says how many draws had a step before above zero; with
    none, every percentile is ``None``.
    """
    r = np.asarray(ratios, dtype=float)
    ok = r[np.isfinite(r)]
    if ok.size == 0:
        return {"p10": None, "p50": None, "p90": None, "draws_defined": 0, "draws": int(r.size)}
    p10, p50, p90 = _pct(ok)
    return {"p10": p10, "p50": p50, "p90": p90, "draws_defined": int(ok.size),
            "draws": int(r.size)}  # fmt: skip


def _chain_factor(ratios: np.ndarray, after: np.ndarray) -> np.ndarray:
    """The ratio used to rebuild the chain draw by draw: 0 / 0 counts as 0."""
    r = np.asarray(ratios, dtype=float).copy()
    r[~np.isfinite(r) & (np.asarray(after, dtype=float) == 0)] = 0.0
    return r


def _applied_text(ratio: Mapping[str, Any], what: str) -> str:
    if ratio["p50"] is None:
        return "the step before is zero in every draw, so no ratio"
    return (
        f"{TIMES} {ratio['p50']:.4g} (middle over {ratio['draws_defined']:,} draws of {what}, "
        f"computed draw by draw; P10 {ratio['p10']:.4g}, P90 {ratio['p90']:.4g})"
    )


# ---------------------------------------------------------------- tract funnels


def _funnel(outcome: Any, county: str | None, cfg: Mapping[str, Any]) -> dict[str, Any]:
    start = str(cfg["headline_step_starts"]).lower()
    base = f"reform_results.{outcome.key}"
    rows = []
    if county is None:
        for i, rec in enumerate(outcome.steps):
            rows.append((f"{base}.steps.{i}", rec, rec.p10, rec.p50, rec.p90, rec.formula))
    else:
        for s in outcome.county_steps(county=county):
            if not len(s.geoids):
                continue
            rows.append(
                (None, s.record, float(s.p10[0]), float(s.p50[0]), float(s.p90[0]), s.formula)
            )
    if not rows:
        raise TraceError(f"no funnel steps for {outcome.key} in county {county}")
    draws = step_draws(outcome, county)
    if draws is None or len(draws) != len(rows):
        raise TraceError(f"the {outcome.key} run kept no per-draw funnel arrays to trace")
    end = next(
        (i for i, r in enumerate(rows) if r[1].name.lower().startswith(start)), len(rows) - 1
    )
    steps, seen = [], set()
    chain = draws[0].copy()
    for n, (rid, rec, p10, p50, p90, formula) in enumerate(rows[: end + 1]):
        ratio: dict[str, Any] | None = None
        if n > 0:
            per_draw = ratio_draws(draws[n], draws[n - 1])
            ratio = ratio_summary(per_draw)
            chain = chain * _chain_factor(per_draw, draws[n])
        steps.append(
            {
                "n": n + 1,
                "name": rec.name,
                "unit": rec.unit,
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "value": None,
                "applied": None
                if ratio is None
                else {
                    "kind": "share" if rec.unit == rows[n - 1][1].unit else "ratio",
                    "ratio": ratio,
                    "text": _applied_text(ratio, f"this step {DIVIDE} the step before"),
                },
                "formula": formula,
                "assumption_ids": list(rec.assumption_ids),
                "new_assumption_ids": _new_ids(rec.assumption_ids, seen),
                "source_ids": list(rec.source_ids),
                "record_id": rid,
            }
        )
    after = [
        {"name": r[1].name, "unit": r[1].unit, "p10": r[2], "p50": r[3], "p90": r[4],
         "record_id": r[0]}
        for r in rows[end + 1:]
    ]  # fmt: skip
    return {"kind": "funnel", "steps": steps, "after": after, "chain_draws": chain}


# ---------------------------------------------------------------- county-level reforms


def _op(rec: Record) -> str | None:
    """The arithmetic a central-value step did, read from its formula."""
    if not rec.inputs:
        return None
    head = rec.formula.split("=")[0]
    for sym, name in ((TIMES, "product"), (DIVIDE, "quotient"), (MINUS, "difference")):
        if f" {sym} " in head:
            return name
    if " + " in head:
        return "sum"
    return None


def _apply(op: str, values: Sequence[float]) -> float | None:
    if not values:
        return None
    if op == "product":
        return float(np.prod(values))
    if op == "sum":
        return float(sum(values))
    if op == "quotient" and len(values) == 2 and values[1] != 0:
        return values[0] / values[1]
    if op == "difference" and len(values) == 2:
        return values[0] - values[1]
    return None


def _central_chain(outcome: Any) -> dict[str, Any]:
    steps, seen = [], set()
    for i, rec in enumerate(outcome.steps):
        op = _op(rec)
        recomputed = _apply(op, [x.value for x in rec.inputs]) if op else None
        steps.append(
            {
                "n": i + 1,
                "name": rec.name,
                "unit": rec.unit,
                **_range(rec),
                "applied": None
                if op is None
                else {
                    "kind": op,
                    "inputs": [
                        {"name": x.name, "value": x.value, "unit": x.unit} for x in rec.inputs
                    ],
                    "recomputed": recomputed,
                    "text": f"the {op} of the {len(rec.inputs)} inputs named in the formula",
                },
                "formula": rec.formula,
                "assumption_ids": list(rec.assumption_ids),
                "new_assumption_ids": _new_ids(rec.assumption_ids, seen),
                "source_ids": list(rec.source_ids),
                "record_id": f"reform_results.{outcome.key}.steps.{i}",
            }
        )
    rec = outcome.added
    steps.append(
        {
            "n": len(steps) + 1,
            "name": f"{rec.name} (likely range over the run's draws)",
            "unit": rec.unit,
            **_range(rec),
            "applied": {
                "kind": "draws",
                "text": (
                    "every input drawn from its range in each draw; the middle of the draws "
                    "need not equal the central-value chain above"
                ),
            },
            "formula": rec.formula,
            "assumption_ids": list(rec.assumption_ids),
            "new_assumption_ids": _new_ids(rec.assumption_ids, seen),
            "source_ids": list(rec.source_ids),
            "record_id": f"reform_results.{outcome.key}.added",
        }
    )
    return {"kind": "central_chain", "steps": steps, "after": []}


def _county_arrays(outcome: Any, county: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    detail = getattr(outcome.result, "detail", None)
    per = getattr(detail, "per_unit", None) or {}
    if detail is None or "base" not in per or "saving" not in per:
        raise TraceError(f"the {outcome.key} run kept no per-draw arrays to trace a county")
    i = list(map(str, detail.county_ids)).index(str(county))
    base = np.asarray(per["base"], dtype=float)[i]  # types x draws
    saving = np.asarray(per["saving"], dtype=float)[i]
    added = np.asarray(detail.added, dtype=float)[i]
    base_t = base.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        save_avg = np.where(base_t > 0, (base * saving).sum(axis=0) / base_t, 0.0)
        share = np.where(base_t > 0, added.sum(axis=0) / base_t, 0.0)
    state_steps = list(outcome.steps)
    b_rec = next(
        (r for r in state_steps if r.name.lower().startswith(cfg["base_step_starts"])), None
    )
    s_rec = next((r for r in state_steps if cfg["saving_step_contains"] in r.name.lower()), None)
    reported = outcome.counties[county]
    seen: set[str] = set()

    def ids(rec: Record | None) -> list[str]:
        return [] if rec is None else list(rec.assumption_ids)

    rows = []
    for name, unit, arr, rec, applied in (
        ("baseline units 2027-2031 the saving reaches", "units", base_t, b_rec, None),
        ("saving per unit (baseline-weighted)", "$/unit", save_avg, s_rec, None),
        ("added units per baseline unit reached", "share", share, reported, None),
    ):
        p10, p50, p90 = _pct(arr)
        rows.append(
            {
                "name": name, "unit": unit, "p10": p10, "p50": p50, "p90": p90, "value": None,
                "applied": applied, "formula": "", "assumption_ids": ids(rec),
                "new_assumption_ids": _new_ids(ids(rec), seen),
                "source_ids": [] if rec is None else list(rec.source_ids), "record_id": None,
            }
        )  # fmt: skip
    rows[0]["formula"] = "sum over building types of the county's baseline units the saving reaches"
    rows[1]["formula"] = (
        "sum of baseline units x saving per unit, divided by baseline units, per draw"
    )
    rows[2]["formula"] = "added units / baseline units reached, per draw"
    added_t = added.sum(axis=0)
    p10, p50, p90 = _pct(added_t)
    per_draw = ratio_draws(added_t, base_t)
    ratio = ratio_summary(per_draw)
    parts = {
        name: dict(
            zip(
                ("p10", "p50", "p90"),
                _pct(np.asarray(arr, dtype=float)[i].sum(axis=0)),
                strict=True,
            )
        )
        for name, arr in (getattr(detail, "parts", None) or {}).items()
    }
    rows.append(
        {
            "name": reported.name, "unit": reported.unit, "p10": p10, "p50": p50, "p90": p90,
            "value": None,
            "applied": {
                "kind": "ratio", "ratio": ratio,
                "text": (
                    _applied_text(ratio, f"added units {DIVIDE} baseline units reached")
                    if ratio["p50"] is not None else "no baseline units reached"
                ),
                "parts": parts,
            },
            "formula": reported.formula,
            "assumption_ids": list(reported.assumption_ids),
            "new_assumption_ids": _new_ids(reported.assumption_ids, seen),
            "source_ids": list(reported.source_ids),
            "record_id": f"reform_results.{outcome.key}.counties.{county}",
        }
    )  # fmt: skip
    for n, r in enumerate(rows):
        r["n"] = n + 1
    chain = base_t * _chain_factor(per_draw, added_t)
    return {"kind": "county_arrays", "steps": rows, "after": [], "chain_draws": chain}


# ---------------------------------------------------------------- the trace


def trace(result: Any, record_id: str, rule: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The step-by-step waterfall behind one reform headline (state or county).

    Args:
        result: The engine ``Result``.
        record_id: ``reform_results.<key>.added`` or ``reform_results.<key>.counties.<fips>``.
        rule: The rule file (for the trace settings; default ``assumptions/grades.yaml``).

    Returns:
        ``{"record_id", "reform", "title", "level", "county", "reported", "kind",
        "steps", "after", "check", "method"}``.

    Raises:
        TraceError: The id cannot be traced.
    """
    cfg = (rule or load_rule())["trace"]
    key, county = parse_id(record_id, result)
    outcome = result.reforms[key]
    reported = outcome.added if county is None else outcome.counties[county]
    if outcome.level == "tract":
        body = _funnel(outcome, county, cfg)
    elif county is None:
        body = _central_chain(outcome)
    else:
        body = _county_arrays(outcome, county, cfg)
    steps = body["steps"]
    last = _mid(steps[-1])
    chain_draws = body.pop("chain_draws", None)
    chain = None
    if chain_draws is not None and np.all(np.isfinite(chain_draws)):
        chain = float(np.percentile(chain_draws, 50))
    recomputed = [
        abs(s["applied"]["recomputed"] - s["value"]) <= 1e-6 * max(1.0, abs(s["value"]))
        for s in steps
        if s.get("value") is not None and (s.get("applied") or {}).get("recomputed") is not None
    ]
    rep = _num(reported.p50 if reported.is_range else reported.value)
    tol = float(cfg["rounding_tolerance"])
    out = {
        "record_id": record_id,
        "reform": key,
        "title": outcome.title,
        "level": "state" if county is None else "county",
        "county": county,
        "draws": result.draws,
        "seed": result.seed,
        "reported": {"name": reported.name, "unit": reported.unit, **_range(reported)},
        **body,
        "check": {
            "last_step_p50": last,
            "reported_p50": rep,
            # P50 of (first step x every applied ratio), rebuilt draw by draw
            "chain_p50": chain,
            "matches": last is not None and rep is not None and abs(last - rep) <= tol,
            "central_steps_recomputed": len(recomputed),
            "central_steps_agree": all(recomputed),
            "tolerance_units": tol,
        },
    }
    out["method"] = {
        "funnel": (
            "Each step is the reform module's own funnel stage, summed over the tracts in each "
            "draw, then P10 / P50 / P90 over the draws. 'Applied' is the step divided by the "
            "one before in each draw, then P10 / P50 / P90 of those ratios (never a ratio of "
            "two percentiles)."
        ),
        "central_chain": (
            "Each step is the module's explanation step at central values; its formula names "
            "the inputs it multiplied, divided or added. The last step is the likely range over "
            "the draws, where every input varies."
        ),
        "county_arrays": (
            "Each step is read from the module's per-draw arrays for this county, then P10 / P50 "
            "/ P90 over the draws; the ratio is added units / baseline units in each draw."
        ),
    }[body["kind"]]
    return out
