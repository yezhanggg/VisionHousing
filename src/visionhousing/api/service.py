"""What each endpoint returns, built from the engine, the ledger and the registry.

Plain-language summary
----------------------
The functions here shape the engine's output for the browser. They never do
arithmetic on a result: every number is copied from a ``Result`` record, the
ledger or the source registry exactly as it is. What they add is lookup and
selection (which state, which county, which ledger entries match a search) and
labels the engine does not carry (state and county names, file lists).

The routes in ``api.app`` call these functions; the tests call both.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.engine import REGISTRY
from visionhousing.engine.context import TESTLAND, StateContext
from visionhousing.engine.core import Result
from visionhousing.ledger import Ledger, load_ledger, load_scenarios

__all__ = [
    "STATE_NAMES",
    "available_states",
    "compare_rows",
    "county_detail",
    "dumps",
    "explanation",
    "export_csv",
    "export_html",
    "ledger_entries",
    "provenance",
    "reform_detail",
    "reform_list",
    "state_summary",
    "validation_table",
]

STATE_NAMES = {
    "TL": "Testland (synthetic test state)",
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip
"""Postal code to name (geography labels, not model inputs)."""

PACK_MARKERS = ("tracts.csv", "tracts.csv.gz")
LOG = logging.getLogger("visionhousing.api")


def dumps(data: Any) -> bytes:
    """Return compact JSON bytes. Refuses NaN and infinity, which JSON cannot hold."""
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def _clean(value: Any) -> Any:
    """Replace NaN and infinity (from pandas tables) with ``None``."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


# ------------------------------------------------------------------ states


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def available_states(packs_dir: Path) -> list[dict[str, Any]]:
    """Return Testland plus every state with a built pack, in code order."""
    out = [{"code": TESTLAND, "name": STATE_NAMES[TESTLAND], "synthetic": True, "pack_hash": None}]
    if packs_dir.is_dir():
        for folder in sorted(p for p in packs_dir.iterdir() if p.is_dir()):
            code = folder.name.upper()
            if code == TESTLAND or code not in STATE_NAMES:
                continue
            if not any((folder / m).is_file() for m in PACK_MARKERS):
                continue
            manifest = _read_json(folder / "pack_manifest.json") or {}
            out.append(
                {
                    "code": code,
                    "name": STATE_NAMES[code],
                    "synthetic": False,
                    "pack_hash": manifest.get("pack_hash"),
                }
            )
    return out


def _counties_table(folder: Path) -> pd.DataFrame | None:
    for name in ("counties.csv", "counties.csv.gz"):
        path = folder / name
        if path.is_file():
            return pd.read_csv(path, dtype={"geoid": str})
    return None


def _county_facts(folder: Path) -> dict[str, dict[str, Any]]:
    table = _counties_table(folder)
    if table is None:
        return {}
    keep = [c for c in ("name", "population", "housing_units") if c in table.columns]
    return {
        str(row["geoid"]): _clean(
            {k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in keep}
        )
        for _, row in table.iterrows()
    }


def _r(record: Any) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _hashes(result: Result) -> dict[str, str]:
    return {
        "code": result.hashes.code,
        "data": result.hashes.data,
        "assumptions": result.hashes.assumptions,
    }


def state_summary(ctx: StateContext, folder: Path, result: Result) -> dict[str, Any]:
    """Return a state's facts and its baseline (from a baseline-only run)."""
    facts = _county_facts(folder)
    counties = [
        {"fips": c, **facts.get(c, {"name": c}), "baseline": _r(result.baseline_counties[c])}
        if c in result.baseline_counties
        else {"fips": c, **facts.get(c, {"name": c}), "baseline": None}
        for c in result.county_ids
    ]
    return {
        "state": ctx.state,
        "name": STATE_NAMES.get(ctx.state, ctx.state),
        "synthetic": ctx.synthetic,
        "source": ctx.source,
        "tracts": len(ctx.tracts),
        "county_count": len(result.county_ids),
        "baseline": _r(result.baseline),
        "counties": counties,
        "reforms": reform_list(),
        "seed": result.seed,
        "draws": result.draws,
        "cycle": result.cycle,
        "hashes": _hashes(result),
        "notes": list(result.notes),
    }


def county_detail(ctx: StateContext, folder: Path, result: Result, fips: str) -> dict[str, Any]:
    """Return one county: its facts, baseline, each reform's and the package's records.

    Raises:
        KeyError: The county is not in this state.
    """
    if fips not in result.county_ids:
        raise KeyError(fips)
    facts = _county_facts(folder).get(fips, {"name": fips})
    features = []
    if ctx.boundaries is not None:
        for f in ctx.boundaries.get("features", []):
            geoid = str(f.get("properties", {}).get("geoid", ""))
            if geoid == fips or (len(geoid) > len(fips) and geoid.startswith(fips)):
                features.append(f)
    tracts = sorted(ctx.tracts.loc[ctx.tracts["county"].astype(str) == fips, "geoid"].astype(str))
    package = None
    if result.package is not None and fips in result.package.counties:
        package = _r(result.package.counties[fips])
    return {
        "state": result.state,
        "fips": fips,
        **facts,
        "tracts": tracts,
        "baseline": _r(result.baseline_counties[fips])
        if fips in result.baseline_counties
        else None,
        "reforms": {
            k: {"title": o.title, "added": _r(o.counties[fips]) if fips in o.counties else None}
            for k, o in result.reforms.items()
        },
        "package": package,
        "boundaries": {"type": "FeatureCollection", "features": features},
        "seed": result.seed,
        "draws": result.draws,
        "cycle": result.cycle,
        "hashes": _hashes(result),
        "synthetic": result.synthetic,
    }


# ------------------------------------------------------------------ reforms and ledger


def reform_list() -> list[dict[str, str]]:
    """Return every runnable reform: key, title and family."""
    return [{"key": s.key, "title": s.title, "family": s.family} for s in REGISTRY.values()]


def _param(pid: str, ledger: Ledger) -> dict[str, Any]:
    return {"id": pid, **ledger[pid].model_dump(mode="json")}


def reform_detail(key: str, ledger: Ledger | None = None) -> dict[str, Any]:
    """Return a reform's definition (scenario text, sources) and its ledger parameters.

    A scenario in ``assumptions/scenarios.yaml`` that has no engine module yet is
    returned with ``runnable: false``.

    Raises:
        KeyError: Neither a registered reform nor a scenario.
    """
    ledger = load_ledger() if ledger is None else ledger
    scenarios = load_scenarios(ledger)
    spec = REGISTRY.get(key)
    scenario = scenarios.get(key)
    if spec is None and scenario is None:
        raise KeyError(key)
    uses = spec.uses if spec is not None else key
    ids = ledger.used_by(uses)
    return {
        "key": key,
        "title": spec.title if spec is not None else scenario.title,  # type: ignore[union-attr]
        "family": spec.family if spec is not None else key,
        "runnable": spec is not None,
        "definition": None if scenario is None else scenario.model_dump(mode="json"),
        "parameters": [_param(pid, ledger) for pid in ids],
        "ledger_hash": ledger.hash(),
    }


def _matches(pid: str, entry: Mapping[str, Any], q: str) -> bool:
    hay = [pid, entry["label"], entry["unit"], entry["rationale"], entry.get("revisit", "")]
    for s in entry.get("source", []):
        hay += [s.get("id", ""), s.get("citation", "")]
    return any(q in str(h).lower() for h in hay)


def ledger_entries(
    ledger: Ledger,
    q: str = "",
    used_by: str | None = None,
    kind: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Return the Assumption Ledger, optionally filtered by a search and fields."""
    q = q.strip().lower()
    rows = []
    for pid, param in ledger.items():
        entry = param.model_dump(mode="json")
        if used_by and used_by not in entry["used_by"]:
            continue
        if kind and entry["kind"] != kind:
            continue
        if status and entry["status"] != status:
            continue
        if q and not _matches(pid, entry, q):
            continue
        rows.append({"id": pid, **entry})
    return {
        "total": len(ledger),
        "count": len(rows),
        "hash": ledger.hash(),
        "version": ledger.version,
        "parameters": rows,
    }


# ------------------------------------------------------------------ provenance, validation


def provenance(folder: Path | None = None) -> dict[str, Any]:
    """Return one provenance row per registered source, plus a pack's file list."""
    from visionhousing.data.manifest import provenance_rows

    out: dict[str, Any] = {"sources": [asdict(row) for row in provenance_rows()]}
    if folder is not None:
        manifest = _read_json(folder / "pack_manifest.json")
        if manifest:
            out["pack"] = {
                k: manifest.get(k) for k in ("state", "pack_hash", "code_version", "files")
            }
    return out


def validation_table(ctx: StateContext) -> dict[str, Any]:
    """Return the baseline backtest skill table (one row per candidate rule)."""
    from visionhousing.baseline import baseline_from_ledger

    baseline, skill = baseline_from_ledger(ctx.permits, ctx.ledger)
    rows = _clean(json.loads(skill.to_json(orient="records")))
    return {
        "state": ctx.state,
        "synthetic": ctx.synthetic,
        "chosen_rule": baseline.rule,
        "metric": "median_abs_log_ratio",
        "columns": {
            "rule": "projection rule",
            "complexity": "rule complexity (lower is simpler)",
            "cases": "county-origin cases scored",
            "mape": "mean absolute percentage error",
            "median_abs_log_ratio": "median of |log(actual / projected)|",
            "coverage_80": "share of actuals inside the 80% interval (leave one county out)",
            "coverage_cases": "cases with an interval",
            "chosen": "the rule the baseline uses",
        },
        "rows": rows,
        "baseline": _r(baseline.state),
        "data_hash": ctx.data_hash,
    }


# ------------------------------------------------------------------ compare and export


def compare_rows(label: str, result: Result) -> dict[str, Any]:
    """Return one comparison column: the state-level records of one run."""
    package = None
    if result.package is not None:
        p = result.package
        package = {
            "reforms": list(p.reforms),
            "combined": _r(p.combined),
            "pct_change": _r(p.pct_change),
            "waterfall": [_r(x) for x in p.waterfall],
        }
    return {
        "label": label,
        "reforms": list(result.reform_ids),
        "overrides": json.loads(json.dumps(dict(result.overrides), sort_keys=True, default=str)),
        "cycle": result.cycle,
        "baseline": _r(result.baseline),
        "reform_results": {
            k: {"title": o.title, "added": _r(o.added), "pct_change": _r(o.pct_change)}
            for k, o in result.reforms.items()
        },
        "package": package,
        "total": None if result.total is None else _r(result.total),
        "hashes": _hashes(result),
    }


CSV_COLUMNS = [
    "scope",
    "geography",
    "reform",
    "measure",
    "p10",
    "p50",
    "p90",
    "value",
    "unit",
    "tier",
    "formula",
    "assumption_ids",
    "source_ids",
    "state",
    "seed",
    "draws",
    "cycle",
    "code_hash",
    "data_hash",
    "assumption_hash",
]


def _csv_cell(value: Any) -> Any:
    """Stop spreadsheet programs from reading a text cell as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _csv_rows(result: Result) -> Iterable[list[Any]]:
    stamp = [
        result.state,
        result.seed,
        result.draws,
        result.cycle,
        result.hashes.code,
        result.hashes.data,
        result.hashes.assumptions,
    ]

    def row(scope: str, geo: str, reform: str, rec: Any) -> list[Any]:
        d = rec.model_dump(mode="json")
        return [
            scope,
            geo,
            reform,
            d["name"],
            d["p10"],
            d["p50"],
            d["p90"],
            d["value"],
            d["unit"],
            d["tier"],
            d["formula"],
            " ".join(d["assumption_ids"]),
            " ".join(d["source_ids"]),
            *stamp,
        ]

    yield row("state", result.state, "baseline", result.baseline)
    for c, rec in result.baseline_counties.items():
        yield row("county", c, "baseline", rec)
    for k, o in result.reforms.items():
        yield row("state", result.state, k, o.added)
        yield row("state", result.state, k, o.pct_change)
        for c, rec in o.counties.items():
            yield row("county", c, k, rec)
    if result.package is not None:
        p = result.package
        for rec in p.waterfall:
            yield row("state", result.state, "package", rec)
        yield row("state", result.state, "package", p.pct_change)
        for c, rec in p.counties.items():
            yield row("county", c, "package", rec)
    if result.total is not None:
        yield row("state", result.state, "total", result.total)


def export_csv(result: Result) -> bytes:
    """Return the run's state and county records as CSV, each row stamped with the hashes."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in _csv_rows(result):
        writer.writerow([_csv_cell(v) for v in r])
    return buf.getvalue().encode("utf-8")


# ------------------------------------------------------------------ explanation and brief


def explanation(result: Result, drivers: bool = False) -> tuple[Any, str]:
    """Return ``(payload, status)`` from the explanation layer for a run.

    The payload is ``visionhousing.explain.explain(result)``: sentences, funnels,
    drivers, the waterfall, limits and "explain this number" for every record,
    each number cited to its record. The top drivers need the sensitivity
    tornado (many reruns; minutes on a large state), so they are included only
    when ``drivers`` is true. If the layer is missing the payload is
    ``None`` and the status ``"not built yet"``; if it fails, the numbers are
    still returned, with status ``"failed"`` (and the error is logged).
    """
    try:
        from visionhousing.explain import explain
    except ImportError:
        return None, "not built yet"
    try:
        return explain(result, with_drivers=drivers), "ok"
    except Exception as exc:
        LOG.exception("explanation failed", exc_info=exc)
        return None, "failed"


def export_html(result: Result) -> bytes:
    """Return the run's print-ready brief (the report writer's HTML)."""
    from visionhousing.report import render

    return render(result, "html")["brief"].encode("utf-8")
