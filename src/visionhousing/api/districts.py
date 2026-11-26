"""``GET /api/v1/states/{st}/districts``: a run's results by state legislative district.

Plain-language summary
----------------------
The endpoint answers "what does this scenario mean in each House (or Senate)
district?". It takes the same scenario fields as the other state endpoints
(``r`` reforms, ``a`` overrides, ``seed``, ``draws``, ``preset``) plus:

- ``chamber``: ``lower`` (state house, the default) or ``upper`` (state senate);
- ``district``: one district code, to get only that district's row.

It runs (or reuses) the engine run for the scenario, then calls
:func:`visionhousing.districts.results.district_results`, which shares the
run's stored tract and county draws out to districts draw by draw and only then
takes P10 / P50 / P90. The reply holds every district's baseline, each reform's
added homes, the package (two or more reforms), the split shares, a caveat
flag (split share above the ledger's ``districts.split_caveat_share``), the
plans' and outlines' vintage, a ``state_check`` (the districts summed per draw,
equal to the state ranges) and the district brief pointers.

A real state answers only the default seed and draws (another seed or draw
count is a 400 ``default_sampling_only``; ``POST /run`` takes those). The
rendered reply is cached under the run's key plus the district table's stamp
(its change time), so a rebuilt table is never served from an old reply.

A state whose pack has no district tables answers 404 ``districts_not_built``.
Testland answers with made-up districts (``synthetic: true``).

The reply is dressed for the screens (:func:`dress`; no new estimate, only
text forms and lookups): every range gets ``shown`` (the engine's own number
writer, ``explain.numbers.show``), each district gets a ``headline`` (the
package with two or more reforms, else the one reform, else nothing), its
class for the map fill (homes added per 1,000 of its 2020 homes, by the fixed
breaks of ``assumptions/decision.yaml``; ``none`` with no reform on), the
split share as text, a ``caveat_text`` when flagged, and the counties it
touches (name and the share of the county's 2020 homes inside the district,
from the county weights). The top of the reply adds the map classes, the plain
limits (``assumptions/limits.yaml``: districts, general, baseline, each reform)
and the caveat rule as text.

``GET /api/v1/states/{st}/district-outlines?chamber=lower`` returns the chamber's
simplified district outlines (GeoJSON, ``level, geoid, name`` properties) from
the pack's ``boundaries/districts_{chamber}.geojson``. Testland answers with
the outlines of its made-up districts (rectangles around their bands of block
points, :func:`visionhousing.districts.weights.synthetic_outlines`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import ValidationError

from visionhousing.api import service
from visionhousing.api.models import LIMITS, ScenarioError, scenario_from_query
from visionhousing.api.runner import Runner, require_default_sampling
from visionhousing.districts.results import (
    BOUNDARY_FILES,
    CHAMBERS,
    SPLIT_PARAMETER,
    DistrictTables,
    district_results,
    load_tables,
)
from visionhousing.districts.weights import synthetic_outlines
from visionhousing.engine.context import TESTLAND
from visionhousing.explain.limits import limits_for
from visionhousing.explain.numbers import TABLE_SIG, num, show

__all__ = ["dress", "register"]

_CODE = re.compile(r"[A-Za-z0-9-]{1,3}")
"""A district code: 1 to 3 letters, digits or dashes (Census SLDUST / SLDLST)."""


def _testland_outlines(path: Path) -> dict[str, dict[str, Any]]:
    """The made-up districts' outlines, inside the box of Testland's county outlines."""
    import pandas as pd

    blocks = pd.read_csv(path / "blocks.csv", dtype={"geoid": str, "tract": str})
    shapes = json.loads((path / "boundaries.geojson").read_text(encoding="utf-8"))
    xs: list[float] = []
    ys: list[float] = []

    def walk(c: Any) -> None:
        if c and isinstance(c[0], (int, float)):
            xs.append(float(c[0]))
            ys.append(float(c[1]))
        else:
            for part in c:
                walk(part)

    for f in shapes["features"]:
        walk(f["geometry"]["coordinates"])
    return synthetic_outlines(blocks, (min(xs), min(ys), max(xs), max(ys)))


def _shown(r: dict[str, float] | None, unit: str) -> dict[str, Any] | None:
    if r is None:
        return None
    return {**r, "shown": {q: show(float(r[q]), unit, TABLE_SIG) for q in ("p10", "p50", "p90")}}


def _rate(
    r: dict[str, float] | None, units: float, what: str, ids: dict[str, Any]
) -> dict[str, Any] | None:
    """A per-1,000 range with its shown forms, a complete ``likely`` text, formula and ids."""
    out = _shown(r, "per 1,000 homes")
    if out is None:
        return None
    lo, mid, hi = (num(float(r[q]), TABLE_SIG) for q in ("p10", "p50", "p90"))
    out["likely"] = f"{lo} to {hi} {what} per 1,000 of its 2020 homes"
    out["formula"] = (
        f"P10/P50/P90 of {what} \u00d7 1000 \u00f7 2020 housing units "
        f"({show(units, 'homes', 9)}) = {lo}, {mid}, {hi} per 1,000 homes"
    )
    out.update(ids)
    return out


def _limits(key: str) -> dict[str, Any]:
    lim = limits_for(key)
    return {"key": key, "title": lim.title, "items": list(lim.items)}


def dress(
    body: dict[str, Any],
    tables: DistrictTables,
    county_names: dict[str, str],
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add the screens' text forms and lookups to a district reply (changes no number).

    Args:
        body: The reply of :func:`district_results` (changed in place and returned).
        tables: The state's district tables (for the counties each district touches).
        county_names: County id to name.
        decision: The decision file (default ``assumptions/decision.yaml``), for the
            fixed per-1,000 class breaks.
    """
    from visionhousing.api import decision as dv

    cfg = decision or dv.load_decision()
    per = cfg["measures"]["per_1000"]
    breaks = [float(b) for b in per["breaks"]]
    edges = [None, *breaks, None]
    classes = [
        {"id": dv.MEASURE_CLASSES[i], "label": dv._break_label(edges[i], edges[i + 1])}
        for i in range(len(breaks) + 1)
    ]
    reforms = [r["key"] for r in body["reforms"]]
    titles = {r["key"]: r["title"] for r in body["reforms"]}
    head = "package" if len(reforms) > 1 else (reforms[0] if reforms else None)
    threshold = float(body["threshold"])
    chamber = body["chamber"]
    vintage = body.get("vintage") or {}
    rate_ids = {
        "rule_id": per["id"],
        "source_ids": [
            str(vintage[k]) for k in ("housing_units_source", "plans_source") if vintage.get(k)
        ],
    }
    cw = tables.county_weights
    cw = cw[cw["chamber"] == chamber]
    touched: dict[str, list[dict[str, Any]]] = {}
    for row in cw.itertuples(index=False):
        touched.setdefault(str(row.district), []).append(
            {
                "fips": str(row.county),
                "name": county_names.get(str(row.county), str(row.county)),
                "weight": float(row.weight),
                "shown": f"{show(float(row.weight), 'share', TABLE_SIG)} of the county's "
                "2020 homes",
            }
        )
    for d in body["districts"]:
        d["baseline"] = _shown(d["baseline"], "units")
        d["reforms"] = {k: _shown(v, "units") for k, v in d["reforms"].items()}
        d["package"] = _shown(d.get("package"), "units")
        rates = d.get("per_1000") or {}
        hu = float(d["units_2020"])
        added = "homes added"
        d["per_1000"] = {
            "baseline": _rate(rates.get("baseline"), hu, "homes permitted", rate_ids),
            "reforms": {
                k: _rate(v, hu, added, rate_ids) for k, v in (rates.get("reforms") or {}).items()
            },
            "package": _rate(rates.get("package"), hu, added, rate_ids),
        }
        if head is None:
            d["headline"] = None
            d["class"] = "none"
        else:
            homes = d["package"] if head == "package" else d["reforms"][head]
            rate = d["per_1000"]["package"] if head == "package" else d["per_1000"]["reforms"][head]
            d["headline"] = {
                "key": head,
                "title": "All reforms together" if head == "package" else titles[head],
                "homes": homes,
                "per_1000": rate,
            }
            d["class"] = (
                "nodata" if rate is None
                else dv.MEASURE_CLASSES[sum(1 for b in breaks if float(rate["p50"]) >= b)]
            )  # fmt: skip
        d["units_2020_shown"] = show(float(d["units_2020"]), "homes", 9)
        d["split_share_shown"] = show(float(d["split_share"]), "share", TABLE_SIG)
        d["caveat_text"] = (
            f"{d['split_share_shown']} of this district's 2020 homes are in census tracts it "
            f"shares with another district (the caveat starts above "
            f"{show(threshold, 'share', TABLE_SIG)}). Inside those tracts, added homes are "
            "shared out by where homes stood in 2020."
            if d["caveat"]
            else ""
        )
        d["counties_touched"] = sorted(
            touched.get(d["id"], []), key=lambda c: (-c["weight"], c["name"])
        )
    body["classes"] = {
        "measure": "per_1000",
        "title": "Homes added per 1,000 of the district's 2020 homes (middle estimate)",
        "rule_id": per["id"],
        "classes": classes,
    }
    body["threshold_shown"] = show(threshold, "share", TABLE_SIG)
    body["limits"] = {
        "districts": _limits("districts"),
        "general": _limits("general"),
        "baseline": _limits("baseline"),
        "reforms": {k: _limits(k) for k in reforms},
    }
    return body


def register(
    app: FastAPI,
    *,
    api: str,
    error: Callable[[int, str, str], Exception],
    context: Callable[[str], Any],
    execute: Callable[[Any], tuple[str, Any]],
    timed: Callable[[str, Any], Any],
    folder: Callable[[str], Path],
) -> None:
    """Add the districts endpoint to ``app``.

    Args:
        app: The FastAPI app (before its catch-all ``/api/v1/...`` route).
        api: The address prefix (``/api/v1``).
        error: Makes the app's JSON error: ``error(status, code, message)``.
        context: State code to its loaded context (raises the app's errors).
        execute: Scenario request to ``(cache key, Result)``.
        timed: Runs a job on the pool within the time limit.
        folder: State code to its data folder.
    """
    held: dict[str, tuple[tuple, DistrictTables]] = {}

    def tables(state: str) -> tuple[tuple, DistrictTables]:
        path = folder(state)
        synthetic = state == TESTLAND
        probe = path / ("blocks.csv" if synthetic else "districts.csv.gz")
        stamp = (str(path), probe.stat().st_mtime_ns if probe.exists() else 0)
        if state in held and held[state][0] == stamp:
            return held[state]
        try:
            loaded = load_tables(path, synthetic=synthetic)
        except FileNotFoundError as exc:
            raise error(404, "districts_not_built", str(exc)) from None
        held[state] = (stamp, loaded)
        return held[state]

    @app.get(f"{api}/states/{{st}}/districts")
    def districts(st: str, request: Request) -> Response:
        """Every district of one chamber under the scenario in the query."""
        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        params = dict(request.query_params)
        chamber = params.pop("chamber", "lower")
        district = params.pop("district", None)
        if "reforms" in params:
            if "r" in params:
                raise error(400, "invalid_query", "give r or reforms, not both")
            params["r"] = params.pop("reforms")
        if chamber not in CHAMBERS:
            raise error(400, "invalid_chamber", "chamber must be 'upper' or 'lower'")
        if district is not None and not _CODE.fullmatch(district):
            raise error(400, "invalid_district", "district must be a code of 1 to 3 characters")
        try:
            req = scenario_from_query(params, st)
        except ScenarioError as exc:
            raise error(400, "invalid_query", str(exc)) from None
        except ValidationError as exc:
            message = "; ".join(str(e["msg"]) for e in exc.errors())
            raise error(400, "invalid_query", message) from None
        refused = require_default_sampling(req)
        if refused:
            raise error(400, "default_sampling_only", refused)
        ctx = context(req.state)
        table_stamp, found = tables(ctx.state)
        key, result = execute(req)
        threshold = float(ctx.ledger[SPLIT_PARAMETER].central)

        def render() -> bytes:
            body = district_results(result, found, chamber, threshold)
            facts = service._county_facts(folder(ctx.state))
            names = {k: str(v.get("name", k)) for k, v in facts.items()}
            dress(body, found, names)
            body["state"] = ctx.state
            body["scenario"] = {"reforms": list(result.reform_ids), "seed": result.seed,
                                "draws": result.draws}  # fmt: skip
            return service.dumps(body)

        body = timed(Runner.key("districts", key, chamber, table_stamp), render)
        if district is None:
            return Response(body, media_type="application/json")
        data = json.loads(body)
        rows = [d for d in data["districts"] if d["id"] == district]
        if not rows:
            raise error(
                404, "district_not_found", f"no {chamber} district {district} in {ctx.state}"
            )
        data["districts"] = rows
        return Response(service.dumps(data), media_type="application/json")

    @app.get(f"{api}/states/{{st}}/district-outlines")
    def district_outlines(st: str, chamber: str = "lower") -> Response:
        """One chamber's district outlines as GeoJSON (empty for Testland)."""
        if chamber not in CHAMBERS:
            raise error(400, "invalid_chamber", "chamber must be 'upper' or 'lower'")
        ctx = context(st)
        path = folder(ctx.state) / BOUNDARY_FILES[chamber]
        if ctx.state == TESTLAND:
            return Response(service.dumps(_testland_outlines(folder(ctx.state))[chamber]),
                            media_type="application/geo+json")  # fmt: skip
        if not path.is_file():
            raise error(404, "districts_not_built", f"no district outlines for {ctx.state}")
        return Response(path.read_bytes(), media_type="application/geo+json")
