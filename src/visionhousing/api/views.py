"""Screen-ready views of one run: the county map, tract shading, outlines, evidence, report card.

Plain-language summary
----------------------
The screens need a few things the run reply does not hold in one place:

- :func:`county_map` gives each county's baseline, the homes the switched-on
  reforms add there, and that change as a percent of the county's own baseline
  (P10 / P50 / P90 from the same seeded draws the engine used). Each county is
  put in a map class by the verdict rule the explanation layer already uses
  (*Little effect* / *Adds some* / *Adds a lot*), with the thresholds read from
  the run's Assumption Ledger, so the class breaks are fixed and never move with
  the scenario. A county with no baseline is "no data".
- :func:`county_tracts` gives, for one county, the homes each tract-level reform
  adds in every tract (P10 / P50 / P90 of the engine's own draws), for the
  county panel's tract shading, with fixed class breaks for that county.
- :func:`state_tracts` gives every tract of the state in one compact,
  column-by-column reply, for the statewide 3D columns and the "funnel on the
  map": each tract's id, county and an interior point (worked out from the
  pack's tract outlines), the homes the scenario's tract-level reforms add
  there (P10 / P50 / P90), each reform's own P50, the reform funnel steps the
  reform modules compute per tract, and fixed class breaks from the Assumption
  Ledger. Reforms that work by county (fees, parking) are listed in
  ``county_only`` with their county values; they are never spread to tracts.
- :func:`county_outlines` gives the county outlines only (no tracts), for the map.
- :func:`county_permit_years` gives each county's homes authorized by building
  permits in every past year (from the state pack's permit table, first year in
  ``assumptions/decision.yaml``), the same per 1,000 existing homes and a fixed
  class, the state totals, and the baseline's projected years 2027-2031 on the
  same scale, for the map key's year slider. The division happens here, never
  in the browser.
- :func:`evidence_cards` gives the evidence library entries for a reform.
- :func:`pack_report_card` gives the state's data report card.
- :func:`contest_view` gives the Method screen one plain list of the baseline
  rules, scored on the rule contest's own measure (the typical miss, either
  way), with each rule's plain name and its text, which rule the baseline uses,
  and one sentence when that rule is not the lowest on the measure shown.

As in ``api.service``, nothing here invents a number. Percent changes come from
the engine's own ``_pct`` rule applied draw by draw and summarised by the
engine's ``from_draws``; text forms come from the explanation layer's ``show``.
Tract ranges are the engine's own draws summarised with the same percentile
rule as ``from_draws`` (:meth:`ReformOutcome.tract_steps`); they are only
rounded for size (see :func:`compact`).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.context import StateContext
from visionhousing.engine.core import PCT, SPAN, ReformOutcome, Result, _pct, _tract_expression
from visionhousing.engine.registry import get_reform
from visionhousing.explain import num, show
from visionhousing.explain.numbers import TABLE_SIG

__all__ = [
    "FUNNEL_STEPS",
    "MAP_CLASSES",
    "TRACT_BREAK_IDS",
    "compact",
    "contest_view",
    "county_map",
    "county_outlines",
    "county_permit_years",
    "county_rankings",
    "county_tracts",
    "evidence_cards",
    "interior_point",
    "pack_report_card",
    "state_tract_funnel",
    "state_tract_points",
    "state_tracts",
]

A_LOT_ID = "explain.verdict_a_lot_pct"
SOME_ID = "explain.verdict_some_pct"
MAP_CLASSES = ("little", "some", "a_lot")
"""Map classes from least to most change (the verdict words, without "Overlaps")."""
NO_BASE = "no_base"
"""Map class of a county with no baseline to compare against (baseline P50 of 0, or 0 in at
least half the draws) where the reforms still add homes: a percent change would read 0%."""
TRACT_CLASSES = 4
"""Shaded classes in the county panel's tract map (plus "none" for zero)."""


def _shown(r: rec.Record) -> dict[str, str]:
    return {k: show(getattr(r, k), r.unit, TABLE_SIG) for k in ("p10", "p50", "p90")}


def _record(r: rec.Record, ids: bool = True) -> dict[str, Any]:
    """A record as the screens read it: values, text forms, formula and (``ids``) its ids."""
    out = {
        "name": r.name,
        "unit": r.unit,
        "p10": r.p10,
        "p50": r.p50,
        "p90": r.p90,
        "shown": _shown(r),
        "formula": r.formula,
        "tier": r.tier,
    }
    if ids:
        out["assumption_ids"] = list(r.assumption_ids)
        out["source_ids"] = list(r.source_ids)
    return out


def _ids(records: list[rec.Record]) -> dict[str, list[str]]:
    """The assumption and source ids shared by a column of county records."""
    return {
        "assumption_ids": sorted({a for r in records for a in r.assumption_ids}),
        "source_ids": sorted({s for r in records for s in r.source_ids}),
    }


def _thresholds(result: Result, ctx: StateContext) -> tuple[float, float]:
    draws = getattr(result, "draws_obj", None)
    ledger = draws.ledger if draws is not None and draws.ledger is not None else ctx.ledger
    return float(ledger[A_LOT_ID].central), float(ledger[SOME_ID].central)


def _class(pct_p50: float, a_lot: float, some: float) -> str:
    if pct_p50 >= a_lot:
        return "a_lot"
    if pct_p50 >= some:
        return "some"
    return "little"


def county_map(
    ctx: StateContext,
    facts: Mapping[str, Mapping[str, Any]],
    result: Result,
    folder: Path | None = None,
):
    """Return every county's baseline, added homes and percent change, with its map class.

    Args:
        ctx: The state context the run used (for the ledger thresholds).
        facts: County id to its facts (name, population, housing units).
        result: The run.
        folder: The state's pack folder, for the back-test by county (default: the
            engine's pack folder for the state).

    Returns:
        ``{"state", "reforms", "classes", "thresholds", "counties", "hashes", ...}``.
        ``counties`` is a list in the run's county order; each has ``fips``,
        ``name``, ``class`` (``none`` with no reform, ``nodata`` with no
        baseline, ``no_base`` when the baseline is 0 but homes are added: then
        ``change`` is ``None`` and ``no_base.text`` says "No baseline to compare;
        adds N homes"), ``baseline``, ``added`` and ``change`` (``None`` when absent).
        Each county also carries ``coverage`` (its evidence coverage, see
        :func:`visionhousing.api.decision.county_coverage`) and ``measure_class``
        (its class of the map's other measures). The reply adds ``measures``
        (those measures and the coverage classes) and, for a run of exactly one
        reform, ``decision`` (:func:`visionhousing.api.decision.decision_view`).
    """
    from visionhousing.api import decision as dv

    a_lot, some = _thresholds(result, ctx)
    pkg = result.package
    counties = []
    kept: dict[str, list[rec.Record]] = {"baseline": [], "added": [], "change": []}
    for i, c in enumerate(result.county_ids):
        info = facts.get(c, {})
        base = result.baseline_counties.get(c)
        row: dict[str, Any] = {
            "fips": c,
            "name": info.get("name", c),
            "housing_units": info.get("housing_units"),
            "baseline": None,
            "added": None,
            "change": None,
            "class": "nodata" if base is None else "none",
        }
        if base is not None:
            row["baseline"] = _record(base, ids=False)
            kept["baseline"].append(base)
        if pkg is not None and result.reform_ids and base is not None and c in pkg.counties:
            added = pkg.counties[c]
            row["added"] = _record(added, ids=False)
            kept["added"].append(added)
            change = rec.from_draws(
                f"added units {SPAN}, % change vs baseline, county {c}",
                _pct(np.asarray(pkg.county_draws[i]), np.asarray(result.baseline_county_draws[i])),
                PCT,
                expression=(
                    "100 x the county's added units / the county's baseline units in the same "
                    "draw (0 where the baseline is 0)"
                ),
                assumption_ids=added.assumption_ids,
                source_ids=sorted({*added.source_ids, "bps_county_annual"}),
                tier=added.tier,
            )
            base_draws = np.asarray(result.baseline_county_draws[i], dtype=float)
            no_base = base.p50 <= 0 or float(np.mean(base_draws <= 0)) >= 0.5
            if no_base and added.p50 > 0:
                # Nothing to divide by: say so, with the homes added, instead of "0%".
                row["class"] = NO_BASE
                row["no_base"] = {
                    "text": f"No baseline to compare; adds {num(added.p50)} homes (middle)",
                    "rule": (
                        "the county's baseline middle estimate is 0, or 0 in at least half the "
                        "draws, so a share of it is not defined"
                    ),
                }
            else:
                row["change"] = _record(change, ids=False)
                kept["change"].append(change)
                row["class"] = _class(change.p50, a_lot, some)
            rate = _per_1000(c, "added units", pkg.county_draws[i], info, added)
            if rate is not None:
                row["per_1000"] = _record(rate, ids=False)
        if base is not None:
            rate = _per_1000(c, "baseline units", result.baseline_county_draws[i], info, base)
            if rate is not None:
                row["baseline_per_1000"] = _record(rate, ids=False)
        counties.append(row)
    coverage = dv.county_coverage(ctx, result, list(result.county_ids), folder)
    measures, by_measure = dv.measure_classes(counties)
    cov_cfg = dv.load_decision()["coverage"]
    for row in counties:
        row["coverage"] = coverage.get(row["fips"])
        row["measure_class"] = by_measure.get(row["fips"], {})
    measures.append(
        {
            "id": "coverage",
            "title": "Evidence coverage: local data, verified rules read, back-test",
            "rule_id": cov_cfg["id"],
            "rule": cov_cfg["rule"],
            "classes": [
                {"id": k["id"], "word": k["word"], "label": k["label"]} for k in cov_cfg["classes"]
            ],
        }
    )
    some_t, lot_t = num(some), num(a_lot)
    classes = [
        {"id": "little", "word": "Little effect", "label": f"under {some_t}%"},
        {"id": "some", "word": "Adds some", "label": f"{some_t}% to under {lot_t}%"},
        {"id": "a_lot", "word": "Adds a lot", "label": f"{lot_t}% or more"},
    ]
    if any(r["class"] == NO_BASE for r in counties):
        classes.append(
            {"id": NO_BASE, "word": "No baseline", "label": "adds homes; no baseline to compare"}
        )
    return {
        "state": result.state,
        "reforms": list(result.reform_ids),
        "measure": "Added homes as a share of the county's own baseline, middle estimate (P50)",
        "classes": classes,
        "thresholds": {
            SOME_ID: some,
            A_LOT_ID: a_lot,
            "rule": "the verdict thresholds of the Assumption Ledger",
        },
        "counties": counties,
        "ids": {k: _ids(v) for k, v in kept.items()},
        "rankings": county_rankings(counties, bool(result.reform_ids)),
        "measures": measures,
        "decision": dv.decision_view(ctx, result, counties, coverage),
        "seed": result.seed,
        "draws": result.draws,
        "cycle": result.cycle,
        "hashes": {
            "code": result.hashes.code,
            "data": result.hashes.data,
            "assumptions": result.hashes.assumptions,
        },
    }


PER_1000 = "per 1,000 homes"
"""Unit of a rate per 1,000 existing homes (the county's housing units in the pack)."""


def _per_1000(
    fips: str, what: str, draws: Any, info: Mapping[str, Any], like: rec.Record
) -> rec.Record | None:
    """Homes per 1,000 existing homes in one county, draw by draw (None without a stock).

    The county's housing units come from the state pack's county table; the rate is
    worked out in each draw and summarised with the engine's ``from_draws``, so its
    likely range is the engine's own, not a ratio of two middles.
    """
    stock = info.get("housing_units")
    try:
        stock = float(stock)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(stock) or stock <= 0:
        return None
    return rec.from_draws(
        f"{what} {SPAN} per 1,000 existing homes, county {fips}",
        1000.0 * np.asarray(draws, dtype=float) / stock,
        PER_1000,
        expression=f"1,000 x the county's {what} in each draw / the county's housing units",
        assumption_ids=like.assumption_ids,
        source_ids=like.source_ids,
        tier=like.tier,
    )


def ordinal(n: int) -> str:
    """``1`` -> ``1st``, ``12`` -> ``12th``, ``23`` -> ``23rd``."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


RANKINGS = {
    True: (
        ("added", "added", "Homes added", "Most homes added over five years (middle estimate)"),
        ("per_1000", "per_1000", "Per 1,000 homes", "Homes added per 1,000 existing homes"),
        ("change", "change", "% of baseline", "Homes added as a share of the county's baseline"),
    ),
    False: (
        ("baseline", "baseline", "Baseline homes", "Homes permitted under today's rules"),
        (
            "baseline_per_1000",
            "baseline_per_1000",
            "Per 1,000 homes",
            "Baseline homes per 1,000 existing homes",
        ),
    ),
}
"""Ranking lists of the county map: ``(id, county field, short label, plain title)``."""


def county_rankings(counties: Sequence[Mapping[str, Any]], with_reforms: bool) -> list[dict]:
    """Rank the counties of a map reply by each measure, highest middle estimate first.

    A presentation view: the order and the rank text ("3rd of 67") are worked out
    here, so the screens only show them. Counties without the measure are left out.

    Returns:
        One entry per measure: ``id``, ``label``, ``title``, ``field``, ``unit``,
        ``count`` and ``rows`` (``fips``, ``name``, ``rank``, ``rank_text`` and the
        county's record for that measure, highest first).
    """
    out = []
    for mid, field, label, title in RANKINGS[with_reforms]:
        have = [c for c in counties if c.get(field)]
        have.sort(key=lambda c: (-c[field]["p50"], c["name"]))
        total = len(have)
        out.append(
            {
                "id": mid,
                "field": field,
                "label": label,
                "title": title,
                "unit": have[0][field]["unit"] if have else "",
                "count": total,
                "rows": [
                    {
                        "fips": c["fips"],
                        "name": c["name"],
                        "rank": i + 1,
                        "rank_text": f"{ordinal(i + 1)} of {total}",
                        "value": c[field],
                    }
                    for i, c in enumerate(have)
                ],
            }
        )
    return out


def county_tracts(result: Result, fips: str) -> dict[str, Any]:
    """Return the homes each tract-level reform adds in every tract of one county.

    Each tract value is P10 / P50 / P90 of the reform's own tract draws (run
    alone). Class breaks split zero to the largest tract P50 in this county,
    over every reform shown, into equal steps, so switching the shaded reform in
    the panel does not move them.

    ``steps`` adds the reform's map-funnel steps for the county's tracts (as in
    :func:`state_tract_funnel`, columns in ``tracts`` order) with the county's
    own range of each step (its tracts summed in each draw).

    Returns:
        ``{"reforms": {key: {"title", "unit", "formula", "tracts": [...],
        "steps": [...]}}, "breaks": [...], "labels": [...]}``; reforms that work
        at county level are left out (they have no tract values).
    """
    out: dict[str, Any] = {}
    top = 0.0
    for key, o in result.reforms.items():
        res = o.result
        if res is None or res.level != "tract":
            continue
        rows = [i for i, c in enumerate(res.county_of) if str(c) == fips]
        tracts = []
        for i in rows:
            r = rec.from_draws(
                f"added units, {o.title} {SPAN}, tract {res.geoids[i]}",
                res.added[i],
                "units",
                expression="the tract's added units authorized",
            )
            top = max(top, r.p50)
            tracts.append(
                {"geoid": str(res.geoids[i]), "p10": r.p10, "p50": r.p50, "p90": r.p90,
                 "shown": _shown(r)}
            )  # fmt: skip
        steps = _funnel(o, None, [fips]) or []
        out[key] = {
            "title": o.title,
            "unit": "units",
            "formula": (
                f"P10, P50, P90 of {result.draws:,} seeded draws of the tract's added units "
                f"authorized under {o.title}, run alone"
            ),
            "assumption_ids": list(o.added.assumption_ids),
            "source_ids": list(o.added.source_ids),
            "tracts": tracts,
            "steps": steps,
        }
    breaks = [top * k / TRACT_CLASSES for k in range(1, TRACT_CLASSES)] if top > 0 else []
    edges = [0.0, *breaks, top]
    labels = (
        [f"{num(edges[k])} to {num(edges[k + 1])} homes" for k in range(TRACT_CLASSES)]
        if top > 0
        else []
    )
    return {"reforms": out, "breaks": breaks, "labels": labels}


# ------------------------------------------------------------------ every tract of the state

TRACT_BREAK_IDS = (
    "explain.tract_class_break_1_units",
    "explain.tract_class_break_2_units",
    "explain.tract_class_break_3_units",
)
"""Ledger ids of the fixed class breaks of the statewide tract columns (homes added, P50)."""

TRACT_COLUMN_CLASSES = ("none", "few", "some", "many", "most")
"""Classes of the statewide tract columns: no added homes, then one class per break."""

FUNNEL_STEPS = (
    ("scope", "lots in scope"),
    ("fit", "physically fit"),
    ("feasible", "financially feasible"),
    ("permits", "expected permits"),
)
"""The map funnel's four steps, in order: ``(id, plain words)``."""

STAGE_OF_STEP: dict[str, dict[str, str]] = {
    "adu": {"scope": "eligible", "fit": "fit", "feasible": "feasible", "permits": "built"},
    "missing_middle": {
        "scope": "eligible",
        "fit": "fit",
        "feasible": "feasible",
        "permits": "units",
    },
    "tod": {
        "scope": "eligible",
        "fit": "developable",
        "feasible": "feasible",
        "permits": "absorbed",
    },
}
"""Reform family to the module stage behind each map-funnel step. Stage keys are the
modules' own (``scenarios.adu.STAGES``, ``scenarios.missing_middle.STAGES``,
``scenarios.tod.STAGES``); TOD's first three steps are land (acres), not lots."""

BOUNDARY_SOURCE = "cb_2020_tract"
"""Registry id of the tract outlines the interior points are worked out from."""

ROUNDING = "whole numbers from 100 up, one decimal below 100"
"""How :func:`compact` rounds tract and county values (said in every reply)."""

POINT_RULE = (
    "an interior point of the tract's outline in the state pack: on the largest part, a "
    "horizontal line halfway between the two vertex heights nearest the middle of its "
    "bounding box; the middle of the widest stretch of that line inside the outline (holes "
    "left out). Always inside the tract, unlike a centroid. Degrees (WGS 84), 5 decimals"
)
"""The interior-point rule, as said in every reply."""

_POINTS: dict[tuple[str, str], dict[str, tuple[float, float]]] = {}
"""Interior points by (state, data hash), worked out once per pack version."""


def compact(values: Iterable[float] | np.ndarray) -> list[float | int | None]:
    """Round values for a small reply: whole numbers from 100 up, one decimal below.

    Rounding never reorders values, so P10 <= P50 <= P90 still holds after it.
    Whole results are written without a decimal point; a value that is not a
    finite number becomes ``None``.

    Args:
        values: The numbers.

    Returns:
        The rounded numbers, as a plain list.
    """
    arr = np.asarray(values if isinstance(values, np.ndarray) else list(values), dtype=float)
    with np.errstate(invalid="ignore"):
        out = np.where(np.abs(arr) >= 100, np.round(arr), np.round(arr, 1))
    return [None if not math.isfinite(v) else int(v) if v.is_integer() else v for v in out.tolist()]


def _polygons(geometry: Mapping[str, Any] | None) -> list[Any]:
    """The polygons (each a list of rings) of a GeoJSON Polygon or MultiPolygon."""
    if not geometry:
        return []
    kind, coords = geometry.get("type"), geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


def _closed(ring: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _area(ring: Sequence[tuple[float, float]]) -> float:
    """Unsigned area of a closed ring (shoelace formula), in square degrees."""
    return 0.5 * abs(
        math.fsum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in itertools.pairwise(ring))
    )


def interior_point(geometry: Mapping[str, Any] | None) -> tuple[float, float] | None:
    """Return a point inside a polygon outline (see :data:`POINT_RULE`).

    A centroid can fall outside a curved or hollow tract; this point never does.
    The scan line runs halfway between two vertex heights, so it touches no
    vertex and every crossing is clean; crossings with the outer ring and the
    holes, sorted, pair up into the stretches that are inside.

    Args:
        geometry: A GeoJSON ``Polygon`` or ``MultiPolygon``.

    Returns:
        ``(lon, lat)``, or ``None`` for an empty or degenerate outline.
    """
    best: list[list[tuple[float, float]]] | None = None
    best_area = -1.0
    for polygon in _polygons(geometry):
        rings = [_closed(r) for r in polygon if len(r) >= 3]
        if not rings:
            continue
        area = _area(rings[0]) - sum(_area(r) for r in rings[1:])
        if area > best_area:
            best, best_area = rings, area
    if best is None:
        return None
    ys = sorted({y for ring in best for _x, y in ring})
    if len(ys) < 2:
        return None
    middle = (ys[0] + ys[-1]) / 2
    below = max(y for y in ys if y <= middle)
    above = min(y for y in ys if y > middle)
    scan = (below + above) / 2
    xs = sorted(
        x0 + (scan - y0) * (x1 - x0) / (y1 - y0)
        for ring in best
        for (x0, y0), (x1, y1) in itertools.pairwise(ring)
        if (y0 > scan) != (y1 > scan)
    )
    stretches = [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]
    if not stretches:
        return None
    a, b = max(stretches, key=lambda s: s[1] - s[0])
    return (a + b) / 2, scan


def tract_points(ctx: StateContext) -> dict[str, tuple[float, float]]:
    """Return tract id to its interior point ``(lon, lat)``, rounded to 5 decimals.

    Worked out once per pack version (state and data hash) and kept in memory.
    Tracts with no outline in the pack are absent.
    """
    key = (ctx.state, ctx.data_hash)
    held = _POINTS.get(key)
    if held is not None:
        return held
    out: dict[str, tuple[float, float]] = {}
    for f in (ctx.boundaries or {}).get("features", []):
        props = f.get("properties") or {}
        geoid = str(props.get("geoid", ""))
        if props.get("level", "tract" if len(geoid) == 11 else "") != "tract":
            continue
        point = interior_point(f.get("geometry"))
        if point is not None:
            out[geoid] = (round(point[0], 5), round(point[1], 5))
    while len(_POINTS) >= 4:
        _POINTS.pop(next(iter(_POINTS)))
    _POINTS[key] = out
    return out


def _tract_breaks(result: Result, ctx: StateContext) -> tuple[list[float], dict[str, Any]]:
    draws = getattr(result, "draws_obj", None)
    ledger = draws.ledger if draws is not None and draws.ledger is not None else ctx.ledger
    breaks = [float(ledger[i].central) for i in TRACT_BREAK_IDS]
    if breaks != sorted(breaks) or breaks[0] <= 0:
        raise ValueError(f"the tract class breaks {TRACT_BREAK_IDS} must be positive and rising")
    return breaks, {
        **dict(zip(TRACT_BREAK_IDS, breaks, strict=True)),
        "rule": "fixed homes-added breaks of the Assumption Ledger (the same for every scenario)",
    }


def _column_classes(breaks: Sequence[float]) -> list[dict[str, str]]:
    edges = [num(b, TABLE_SIG) for b in breaks]
    words = [f"under {edges[0]} {'home' if edges[0] == '1' else 'homes'}"]
    words += [f"{edges[k]} to under {edges[k + 1]} homes" for k in range(len(edges) - 1)]
    words.append(f"{edges[-1]} homes or more")
    return [
        {"id": TRACT_COLUMN_CLASSES[0], "label": "no added homes"},
        *({"id": c, "label": w} for c, w in zip(TRACT_COLUMN_CLASSES[1:], words, strict=True)),
    ]


def _align(values: np.ndarray, geoids: Sequence[str], index: Mapping[str, int]) -> np.ndarray:
    """Put per-tract values in the reply's tract order (0 for a tract with no row)."""
    out = np.zeros(len(index))
    unknown = [g for g in geoids if g not in index]
    if unknown:
        raise ValueError(f"tracts {unknown[:3]} are not in the state's tract table")
    out[[index[g] for g in geoids]] = np.asarray(values, dtype=float)
    return out


def _ids_of(r: rec.Record) -> dict[str, list[str]]:
    return {"assumption_ids": list(r.assumption_ids), "source_ids": list(r.source_ids)}


def _ranges(p10: Any, p50: Any, p90: Any, formula: str) -> dict[str, Any]:
    """Columns of P10 / P50 / P90 (rounded by :func:`compact`) with how they were made."""
    return {"p10": compact(p10), "p50": compact(p50), "p90": compact(p90), "formula": formula}


def _by_county(ids: Sequence[str], values: np.ndarray, counties: Sequence[str]) -> np.ndarray:
    """Values given for ``ids`` put in ``counties`` order (NaN, written ``None``, where absent)."""
    at = {c: i for i, c in enumerate(ids)}
    return np.array([values[at[c]] if c in at else np.nan for c in counties], dtype=float)


def state_tract_points(ctx: StateContext) -> dict[str, Any]:
    """Return every tract's id, county and interior point: the geometry the columns stand on.

    It does not depend on the scenario, so the map loads it once and keys its
    tract source by ``geoids`` (the same ids, in the same order, as every
    :func:`state_tracts` reply).

    Returns:
        ``{"geoids", "counties", "county_index", "lon", "lat", "rule", ...}``;
        ``lon`` / ``lat`` are ``None`` for a tract with no outline in the pack.
    """
    tracts = ctx.tracts.assign(
        geoid=ctx.tracts["geoid"].astype(str), county=ctx.tracts["county"].astype(str)
    )
    county_of = dict(zip(tracts["geoid"], tracts["county"], strict=True))
    geoids = sorted(county_of)
    counties = sorted(set(county_of.values()))
    at = {c: i for i, c in enumerate(counties)}
    points = tract_points(ctx)
    return {
        "state": ctx.state,
        "count": len(geoids),
        "geoids": geoids,
        "counties": counties,
        "county_index": [at[county_of[g]] for g in geoids],
        "lon": [points[g][0] if g in points else None for g in geoids],
        "lat": [points[g][1] if g in points else None for g in geoids],
        "rule": POINT_RULE,
        "source_ids": [] if ctx.synthetic else [BOUNDARY_SOURCE],
        "missing": sum(g not in points for g in geoids),
        "synthetic": ctx.synthetic,
        "data_hash": ctx.data_hash,
    }


def _reform_columns(
    o: ReformOutcome, draws: int, index: Mapping[str, int], counties: Sequence[str]
) -> dict[str, Any]:
    res = o.result
    if res is None:
        raise ValueError(f"reform '{o.key}' has no tract result")
    p50 = _align(np.percentile(res.added, 50, axis=1), res.geoids, index)
    recs = [o.counties.get(c) for c in counties]
    county = [[np.nan if r is None else getattr(r, f) for r in recs] for f in ("p10", "p50", "p90")]
    return {
        "title": o.title,
        "unit": "units",
        "p50": compact(p50),
        "formula": (
            f"P50 of {draws:,} seeded draws, per tract, of the tract's added units authorized "
            f"under {o.title}, run alone"
        ),
        **_ids_of(o.added),
        "counties": _ranges(
            *county,
            f"P10, P50, P90 of {draws:,} seeded draws, per county, of the county's tracts "
            f"summed in each draw, under {o.title} run alone",
        ),
        "total": _record(o.added, ids=False),
    }


def _funnel(
    o: ReformOutcome, index: Mapping[str, int] | None, counties: Sequence[str] | None
) -> list[dict[str, Any]] | None:
    """The reform's four map-funnel steps: per tract, per county and for the state.

    With ``index`` (tract id to position) the tract columns cover the whole state
    and the county columns follow ``counties``; with ``index=None`` and one
    county in ``counties``, both cover that county only (the county panel).
    """
    mapping = STAGE_OF_STEP.get(get_reform(o.key).family)
    if mapping is None:
        return None
    stages = list(mapping.values())
    one = counties[0] if index is None and counties else None
    by_tract = {s.stage: s for s in o.tract_steps(stages, county=one)}
    by_county = {s.stage: s for s in o.county_steps(stages, county=one)}
    out = []
    for step, words in FUNNEL_STEPS:
        t, c = by_tract.get(mapping[step]), by_county.get(mapping[step])
        if t is None or c is None:
            continue
        tract_cols = (t.p10, t.p50, t.p90)
        if index is not None:
            tract_cols = tuple(_align(q, t.geoids, index) for q in tract_cols)
        county_cols = tuple(
            _by_county(c.geoids, q, counties or c.geoids) for q in (c.p10, c.p50, c.p90)
        )
        out.append(
            {
                "step": step,
                "words": words,
                "stage": t.stage,
                "label": t.record.name,
                "unit": t.record.unit,
                "tracts": _ranges(*tract_cols, t.formula),
                "counties": _ranges(*county_cols, c.formula),
                "state": _record(t.record),
            }
        )
    return out


def _county_only(o: ReformOutcome, counties: Sequence[str], draws: int) -> dict[str, Any]:
    recs = [o.counties.get(c) for c in counties]
    notes = dict((o.result.county_notes if o.result is not None else {}) or {})
    county = [[np.nan if r is None else getattr(r, f) for r in recs] for f in ("p10", "p50", "p90")]
    return {
        "title": o.title,
        "unit": "units",
        "level": "county",
        **_ranges(
            *county,
            f"P10, P50, P90 of {draws:,} seeded draws, per county, of the county's added units "
            f"under {o.title}, run alone",
        ),
        "assumption_ids": list(o.added.assumption_ids),
        "source_ids": sorted({*o.added.source_ids, *(s for _t, ss in notes.values() for s in ss)}),
        "county_notes": {c: text for c, (text, _s) in sorted(notes.items())},
        "total": _record(o.added, ids=False),
        "funnel": None,
        "funnel_note": (
            "This reform works by county: its funnel steps exist only for the state (the "
            "run's reform steps), so none is shown per county or per tract."
        ),
    }


def state_tracts(result: Result, ctx: StateContext | None = None) -> dict[str, Any]:
    """Return the homes every tract gains under one scenario, column by column.

    Geometry-free: the tract points come from :func:`state_tract_points` (same
    ``geoids``, same order), so the map keeps one tract source keyed by geoid and
    only swaps these values. Every per-tract column is in ``geoids`` order and
    every per-county column in ``counties`` order:

    - ``added``: P10 / P50 / P90 of the homes the scenario's tract-level reforms
      add (the package's tract-level part, :attr:`PackageOutcome.tract_draws`),
      with ``counties`` (the same draws summed by county) and ``total`` (summed
      over the state) from the engine; ``class`` puts each tract's P50 against
      the ledger's fixed breaks.
    - ``by_reform``: each tract-level reform's own P50 per tract (run alone) and
      its county ranges.
    - ``funnels``: the tract-level reforms that have a map funnel, each with the
      one-reform scenario to ask :func:`state_tract_funnel` for (each reform's
      funnel is the reform run alone, so the same seed, draws and assumptions
      give the same numbers in any package). Kept out of this reply so every
      reply stays small.
    - ``county_only``: reforms that work by county, with their county values;
      never spread to tracts.

    Values are rounded by :func:`compact`. Tract ranges do not add up: a sum of
    tract medians is not the median of the sum, so county and state values are
    the engine's own sums of draws, given here so nothing has to be added up.

    Args:
        result: The run.
        ctx: The state context the run used (default: the run's own).

    Returns:
        The reply (see ``docs/modules/api.md``).
    """
    if ctx is None:
        if result.run_context is None:
            raise ValueError("the run has no state context")
        ctx = result.run_context.state
    geoids = sorted(ctx.tracts["geoid"].astype(str))
    index = {g: i for i, g in enumerate(geoids)}
    counties = list(result.county_ids)
    breaks, thresholds = _tract_breaks(result, ctx)
    draws = result.draws
    pkg = result.package

    added = None
    p50 = np.zeros(len(geoids))
    notes: list[str] = []
    if pkg is not None and pkg.tract_draws is not None and pkg.tract_combined is not None:
        p10, p50, p90 = (
            _align(q, pkg.tract_geoids, index)
            for q in np.percentile(pkg.tract_draws, [10, 50, 90], axis=1)
        )
        titles = " + ".join(result.reforms[k].title for k in pkg.tract_reforms)
        expression = _tract_expression(pkg.tract_combined)
        added = {
            "name": f"added units, {titles} {SPAN}, per tract",
            "unit": "units",
            "reforms": list(pkg.tract_reforms),
            **_ranges(
                p10,
                p50,
                p90,
                f"P10, P50, P90 of {draws:,} seeded draws, per tract, of {expression}",
            ),
            **_ids_of(pkg.tract_combined),
            "counties": _ranges(
                *np.percentile(pkg.tract_county_draws, [10, 50, 90], axis=1),
                f"P10, P50, P90 of {draws:,} seeded draws, per county, of the sum over the "
                f"county's tracts of {expression}",
            ),
            "total": _record(pkg.tract_combined, ids=False),
        }
        if len(pkg.reforms) > 1:
            notes.append(
                "The columns are the package's tract-level part, worked out tract by tract in "
                "every draw: overlap and interactions are inside them."
            )
    cls = np.searchsorted(np.asarray(breaks), p50, side="right") + 1
    cls[p50 <= 0] = 0

    by_reform: dict[str, Any] = {}
    funnels: dict[str, Any] = {}
    county_only: dict[str, Any] = {}
    for key, o in result.reforms.items():
        if o.level == "tract":
            by_reform[key] = _reform_columns(o, draws, index, counties)
            if get_reform(key).family in STAGE_OF_STEP:
                funnels[key] = {"title": o.title, "ask": {"reforms": [key]}}
        else:
            county_only[key] = _county_only(o, counties, draws)
    if county_only:
        names = " and ".join(o["title"] for o in county_only.values())
        verb, their = ("work", "their") if len(county_only) > 1 else ("works", "its")
        notes.append(
            f"{names} {verb} by county: {their} county values are in county_only and are not "
            "spread to tracts."
        )
    total = None
    if pkg is not None and result.reform_ids:
        total = _record(pkg.combined)
    return {
        "state": result.state,
        "reforms": list(result.reform_ids),
        "span": SPAN,
        "unit": "units",
        "measure": "Homes added by the scenario's tract-level reforms, per tract, 2027-2031",
        "count": len(geoids),
        "geoids": geoids,
        "counties": counties,
        "points": {
            "address": f"/states/{result.state}/tract-points",
            "note": "Tract points, counties and ids: the same geoids in the same order.",
        },
        "added": added,
        "class": cls.tolist(),
        "classes": _column_classes(breaks),
        "breaks": breaks,
        "thresholds": thresholds,
        "class_rule": "the tract's added P50 against the breaks; none when the P50 is 0",
        "by_reform": by_reform,
        "funnels": funnels,
        "county_only": county_only,
        "baseline": {
            "tract_split": False,
            "note": "The baseline is projected by county only; it has no tract values.",
        },
        "scenario_total": total,
        "rounding": ROUNDING,
        "sum_rule": (
            "Add draws, not percentiles: the sum of the tract P50s is not the county or state "
            "P50. County and state values here are the same draws summed first."
        ),
        "notes": notes,
        "seed": result.seed,
        "draws": draws,
        "cycle": result.cycle,
        "synthetic": result.synthetic,
        "hashes": {
            "code": result.hashes.code,
            "data": result.hashes.data,
            "assumptions": result.hashes.assumptions,
        },
    }


def state_tract_funnel(result: Result, ctx: StateContext | None = None) -> dict[str, Any]:
    """Return the map funnel of each tract-level reform of a run, tract by tract.

    For each tract-level reform (run alone) with a funnel, the four steps of
    :data:`FUNNEL_STEPS` (lots in scope, physically fit, financially feasible,
    expected permits), each the reform module's own stage (:data:`STAGE_OF_STEP`):
    ``tracts`` (P10 / P50 / P90 per tract, in ``geoids`` order), ``counties``
    (the county's tracts summed in each draw, in ``counties`` order) and
    ``state`` (the module's state record). Reforms that work by county have no
    funnel by tract or county; they are named in ``county_only``. The baseline
    has no tract split. A one-reform scenario keeps the reply small (about
    240 KB for a Pennsylvania ADU reform).

    Args:
        result: The run.
        ctx: The state context the run used (default: the run's own).

    Returns:
        ``{"geoids", "counties", "funnel_steps", "funnel": {key: {"steps": [...]}}, ...}``.
    """
    if ctx is None:
        if result.run_context is None:
            raise ValueError("the run has no state context")
        ctx = result.run_context.state
    geoids = sorted(ctx.tracts["geoid"].astype(str))
    index = {g: i for i, g in enumerate(geoids)}
    counties = list(result.county_ids)
    funnel: dict[str, Any] = {}
    county_only: dict[str, str] = {}
    for key, o in result.reforms.items():
        steps = _funnel(o, index, counties) if o.level == "tract" else None
        if steps is not None:
            funnel[key] = {"title": o.title, "run": "alone", "steps": steps}
        elif o.level != "tract":
            county_only[key] = o.title
    return {
        "state": result.state,
        "reforms": list(result.reform_ids),
        "span": SPAN,
        "count": len(geoids),
        "geoids": geoids,
        "counties": counties,
        "funnel_steps": [{"id": i, "words": w} for i, w in FUNNEL_STEPS],
        "stage_of_step": {k: dict(v) for k, v in STAGE_OF_STEP.items()},
        "funnel": funnel,
        "county_only": county_only,
        "county_only_note": (
            "These reforms work by county: their funnel steps exist only for the state (the "
            "run's reform steps), so none is shown per county or per tract."
        ),
        "baseline": {
            "tract_split": False,
            "note": "The baseline is projected by county only; it has no tract values.",
        },
        "rounding": ROUNDING,
        "sum_rule": (
            "Add draws, not percentiles: the sum of the tract P50s is not the county or state "
            "P50. County and state values here are the same draws summed first."
        ),
        "notes": ["Funnel steps are each reform run alone, before any overlap or interaction."],
        "seed": result.seed,
        "draws": result.draws,
        "cycle": result.cycle,
        "synthetic": result.synthetic,
        "hashes": {
            "code": result.hashes.code,
            "data": result.hashes.data,
            "assumptions": result.hashes.assumptions,
        },
    }


def county_outlines(ctx: StateContext) -> dict[str, Any]:
    """Return the county outlines only (the tract outlines come with each county)."""
    features = []
    if ctx.boundaries is not None:
        for f in ctx.boundaries.get("features", []):
            props = f.get("properties", {})
            if props.get("level") == "county" or len(str(props.get("geoid", ""))) == 5:
                features.append(f)
    return {"type": "FeatureCollection", "state": ctx.state, "features": features}


def evidence_cards(reform: str | None = None) -> dict[str, Any]:
    """Return the evidence library entries (all, or one reform's), as JSON-ready data."""
    from visionhousing.ledger.evidence import load_evidence

    lib = load_evidence()
    ids = lib.by_reform(reform) if reform else list(lib)
    return {
        "count": len(ids),
        "entries": [{"id": eid, **lib[eid].model_dump(mode="json")} for eid in ids],
    }


def pack_report_card(folder: Path) -> dict[str, Any]:
    """Return the state's data report card, built from its pack folder."""
    from visionhousing.data.report_card import load_pack_files, report_card

    return report_card(load_pack_files(folder))


UNITS_COLUMNS = ("units_1", "units_2", "units_3_4", "units_5plus")
"""The permit table's unit columns (one per building size); a year's homes are their sum."""


def _per_1000_value(units: float, stock: float | None) -> float | None:
    if stock is None or not math.isfinite(stock) or stock <= 0 or not math.isfinite(units):
        return None
    return 1000.0 * units / stock


def county_permit_years(
    ctx: StateContext,
    facts: Mapping[str, Mapping[str, Any]],
    result: Result | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Homes permitted by county and year, past and projected, for the year slider.

    Past years come straight from the pack's ``permits_county_year`` table (the
    Census Building Permits Survey): the homes are the sum of its unit columns,
    and the rate is 1,000 x homes / the county's housing units (the pack's county
    table). Each county-year is put in a fixed class by the breaks of
    ``permit_years`` in ``assumptions/decision.yaml``; rows the pack marks as
    imputed (the survey's estimate for places that did not report) say so.

    The projected years are the baseline's own yearly county table (``result``,
    a baseline run): P10 / P50 / P90 per county and year, its rate on the same
    stock (1,000 x the P50 / stock is exactly the P50 of the rate, the stock being
    fixed) and its class on the same breaks. The state rows are the pack's
    county sums (past) and the engine's yearly state records (projected).

    Args:
        ctx: The state's context (its permit table).
        facts: County id to ``{name, housing_units}`` (the pack's county table).
        result: A baseline run with yearly results, or ``None`` (no projected years).
        cfg: The decision file (default: ``assumptions/decision.yaml``).

    Returns:
        A JSON-ready dict, county rows in ``counties`` order and years in ``years`` order.
    """
    from visionhousing.api.decision import MEASURE_CLASSES, _break_label, load_decision

    cfg = cfg or load_decision()
    spec = cfg["permit_years"]
    breaks = [float(b) for b in spec["breaks"]]
    edges = [None, *breaks, None]
    classes = [
        {"id": MEASURE_CLASSES[i], "label": _break_label(edges[i], edges[i + 1])}
        for i in range(len(breaks) + 1)
    ]

    def cls(v: float | None) -> str:
        if v is None or not math.isfinite(v):
            return "nodata"
        return MEASURE_CLASSES[sum(1 for b in breaks if v >= b)]

    permits = ctx.permits.copy()
    permits["county"] = permits["county"].astype(str)
    first = int(spec["first_year"])
    permits = permits[permits["year"] >= first]
    cols = [c for c in UNITS_COLUMNS if c in permits.columns]
    permits["homes"] = permits[cols].sum(axis=1).astype(float)
    years = sorted(int(y) for y in permits["year"].unique())
    counties = sorted(set(permits["county"]) | set(facts))
    stock: dict[str, float | None] = {}
    for c in counties:
        try:
            v = float(facts.get(c, {}).get("housing_units"))
        except (TypeError, ValueError):
            v = None
        stock[c] = v if v is not None and math.isfinite(v) and v > 0 else None

    cell = {(r.county, int(r.year)): r for r in permits.itertuples(index=False)}
    rows = []
    for c in counties:
        units, rate, klass, imputed, units_shown, rate_shown = [], [], [], [], [], []
        for y in years:
            r = cell.get((c, y))
            if r is None:
                units.append(None)
                rate.append(None)
                klass.append("nodata")
                imputed.append(False)
                units_shown.append("—")
                rate_shown.append("—")
                continue
            h = float(r.homes)
            p = _per_1000_value(h, stock[c])
            units.append(round(h))
            rate.append(None if p is None else round(p, 3))
            klass.append(cls(p))
            imputed.append(bool(getattr(r, "imputed", 0)))
            units_shown.append(show(h, "units", TABLE_SIG))
            rate_shown.append("—" if p is None else show(p, PER_1000, TABLE_SIG))
        rows.append(
            {
                "fips": c,
                "name": facts.get(c, {}).get("name", c),
                "housing_units": stock[c],
                "units": units,
                "per_1000": rate,
                "class": klass,
                "imputed": imputed,
                "shown": {"units": units_shown, "per_1000": rate_shown},
            }
        )

    totals = permits.groupby("year")["homes"].sum()
    state_past = [
        {
            "year": y,
            "units": float(totals.get(y, 0.0)),
            "shown": show(float(totals.get(y, 0.0)), "units", TABLE_SIG),
        }
        for y in years
    ]

    projected: dict[str, Any] | None = None
    series = getattr(result, "baseline_annual", None) if result is not None else None
    if series is not None:
        table = series.counties
        index = {g: i for i, g in enumerate(table.geoids)}
        prow = []
        for c in counties:
            i = index.get(c)
            if i is None:
                prow.append(None)
                continue
            p50 = [float(v) for v in table.p50[i]]
            rate = [_per_1000_value(v, stock[c]) for v in p50]
            prow.append(
                {
                    "p10": [round(float(v), 3) for v in table.p10[i]],
                    "p50": [round(v, 3) for v in p50],
                    "p90": [round(float(v), 3) for v in table.p90[i]],
                    "per_1000": [None if v is None else round(v, 3) for v in rate],
                    "class": [cls(v) for v in rate],
                    "shown": {
                        "range": [
                            f"{show(float(a), 'units', TABLE_SIG)} to "
                            f"{show(float(b), 'units', TABLE_SIG)}"
                            for a, b in zip(table.p10[i], table.p90[i], strict=True)
                        ],
                        "p50": [show(v, "units", TABLE_SIG) for v in p50],
                        "per_1000": [
                            "—" if v is None else show(v, PER_1000, TABLE_SIG) for v in rate
                        ],
                    },
                }
            )
        projected = {
            "label": series.label,
            "years": list(series.years),
            "counties": prow,
            "state": [
                {**_record(r, ids=False), "year": y}
                for y, r in zip(series.years, series.state, strict=True)
            ],
            "formula": table.formula,
            "rate_formula": (
                "1,000 x the county's baseline P50 in the year / the county's housing units "
                "(the stock is fixed, so this is the P50 of the rate)"
            ),
            "assumption_ids": list(series.assumption_ids),
            "source_ids": list(table.source_ids),
        }

    return {
        "state": ctx.state,
        "title": spec["title"],
        "rule_id": spec["id"],
        "unit": PER_1000,
        "years": years,
        "first_year": first,
        "latest": years[-1] if years else None,
        "classes": classes,
        "counties": rows,
        "state_past": state_past,
        "projected": projected,
        "formula": (
            "homes = units authorized in 1, 2, 3-4 and 5+ unit buildings in the year; "
            "per 1,000 = 1,000 x homes / the county's housing units"
        ),
        "imputed_note": (
            "Marked rows are the Census Bureau's estimates for places that did not report "
            "every month (imputed)."
        ),
        "source_ids": ["bps_county_annual", "acs5_summary_file"],
        "synthetic": ctx.synthetic,
    }


def contest_view(validation: Mapping[str, Any]) -> dict[str, Any] | None:
    """The baseline rules on one measure, ready for the Method screen's left panel.

    The left panel and the rule contest table must speak the same language: the
    plain rule names (``three-year mean``, not ``last_3_mean``) and the same
    measure, the contest's *typical miss* (``abs_pct``: the median of
    ``|log(projected / actual)|`` written as a percent miss). Each rule's text
    is written here with the explanation layer's ``show`` (three significant
    figures), so the browser only places it.

    The baseline uses ``validation["chosen_rule"]``. When that rule is not the
    lowest on the typical miss, ``note`` says why in one line: the rule fixed
    before scoring also weighs range coverage and simplicity.

    Args:
        validation: The ``GET /validation`` reply (``service.validation_table``).

    Returns:
        ``{measure, measure_help, used, used_name, used_text, rules: [{rule,
        name, value, text, used}], note}``, or None without a contest.
    """
    contest = validation.get("contest") or {}
    rules = [r for r in contest.get("rules") or [] if r.get("abs_pct") is not None]
    if not rules:
        return None
    used = str(validation.get("chosen_rule") or contest.get("winner") or "")
    rows = [
        {
            "rule": r["rule"],
            "name": r.get("name") or r["rule"],
            "value": float(r["abs_pct"]),
            "text": show(float(r["abs_pct"]), "share", TABLE_SIG),
            "used": r["rule"] == used,
        }
        for r in rules
    ]
    mine = next((r for r in rows if r["used"]), None)
    best = min(rows, key=lambda r: r["value"])
    note = ""
    if mine is not None and best["value"] < mine["value"]:
        note = (
            f"The {mine['name']} is used although the {best['name']} misses less "
            f"({best['text']} against {mine['text']}): the rule fixed before scoring "
            "keeps only rules whose likely ranges caught the real total often enough, "
            "then takes the simplest one close to the lowest miss."
        )
    elif mine is not None and any(r["text"] == mine["text"] and not r["used"] for r in rows):
        tied = next(r for r in rows if r["text"] == mine["text"] and not r["used"])
        note = (
            f"The {mine['name']} and the {tied['name']} show the same typical miss at this "
            f"rounding; the {mine['name']} is lower before rounding."
        )
    return {
        "measure": "Typical miss (either way)",
        "measure_help": "median of |log(projected / actual)|, written as a percent miss",
        "used": used,
        "used_name": mine["name"] if mine else used,
        "used_text": mine["text"] if mine else "",
        "rules": rows,
        "note": note,
    }
