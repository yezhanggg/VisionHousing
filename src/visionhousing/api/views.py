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
- :func:`county_outlines` gives the county outlines only (no tracts), for the map.
- :func:`evidence_cards` gives the evidence library entries for a reform.
- :func:`pack_report_card` gives the state's data report card.

As in ``api.service``, nothing here invents a number. Percent changes come from
the engine's own ``_pct`` rule applied draw by draw and summarised by the
engine's ``from_draws``; text forms come from the explanation layer's ``show``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.context import StateContext
from visionhousing.engine.core import PCT, SPAN, Result, _pct
from visionhousing.explain import num, show
from visionhousing.explain.numbers import TABLE_SIG

__all__ = [
    "MAP_CLASSES",
    "county_map",
    "county_outlines",
    "county_tracts",
    "evidence_cards",
    "pack_report_card",
]

A_LOT_ID = "explain.verdict_a_lot_pct"
SOME_ID = "explain.verdict_some_pct"
MAP_CLASSES = ("little", "some", "a_lot")
"""Map classes from least to most change (the verdict words, without "Overlaps")."""
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


def county_map(ctx: StateContext, facts: Mapping[str, Mapping[str, Any]], result: Result):
    """Return every county's baseline, added homes and percent change, with its map class.

    Args:
        ctx: The state context the run used (for the ledger thresholds).
        facts: County id to its facts (name, population, housing units).
        result: The run.

    Returns:
        ``{"state", "reforms", "classes", "thresholds", "counties", "hashes", ...}``.
        ``counties`` is a list in the run's county order; each has ``fips``,
        ``name``, ``class`` (``none`` with no reform, ``nodata`` with no
        baseline), ``baseline``, ``added`` and ``change`` (``None`` when absent).
    """
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
            row["change"] = _record(change, ids=False)
            kept["change"].append(change)
            row["class"] = _class(change.p50, a_lot, some)
        counties.append(row)
    some_t, lot_t = num(some), num(a_lot)
    classes = [
        {"id": "little", "word": "Little effect", "label": f"under {some_t}%"},
        {"id": "some", "word": "Adds some", "label": f"{some_t}% to under {lot_t}%"},
        {"id": "a_lot", "word": "Adds a lot", "label": f"{lot_t}% or more"},
    ]
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
        "seed": result.seed,
        "draws": result.draws,
        "cycle": result.cycle,
        "hashes": {
            "code": result.hashes.code,
            "data": result.hashes.data,
            "assumptions": result.hashes.assumptions,
        },
    }


def county_tracts(result: Result, fips: str) -> dict[str, Any]:
    """Return the homes each tract-level reform adds in every tract of one county.

    Each tract value is P10 / P50 / P90 of the reform's own tract draws (run
    alone). Class breaks split zero to the largest tract P50 in this county,
    over every reform shown, into equal steps, so switching the shaded reform in
    the panel does not move them.

    Returns:
        ``{"reforms": {key: {"title", "unit", "formula", "tracts": [...]}},
        "breaks": [...], "labels": [...]}``; reforms that work at county level
        are left out (they have no tract values).
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
                expression="the tract's net added units",
            )
            top = max(top, r.p50)
            tracts.append(
                {"geoid": str(res.geoids[i]), "p10": r.p10, "p50": r.p50, "p90": r.p90,
                 "shown": _shown(r)}
            )  # fmt: skip
        out[key] = {
            "title": o.title,
            "unit": "units",
            "formula": (
                f"P10, P50, P90 of {result.draws:,} seeded draws of the tract's net added units "
                f"under {o.title}, run alone"
            ),
            "assumption_ids": list(o.added.assumption_ids),
            "source_ids": list(o.added.source_ids),
            "tracts": tracts,
        }
    breaks = [top * k / TRACT_CLASSES for k in range(1, TRACT_CLASSES)] if top > 0 else []
    edges = [0.0, *breaks, top]
    labels = (
        [f"{num(edges[k])} to {num(edges[k + 1])} homes" for k in range(TRACT_CLASSES)]
        if top > 0
        else []
    )
    return {"reforms": out, "breaks": breaks, "labels": labels}


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
