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
from fastapi import Request
from fastapi.responses import Response

from visionhousing.engine import REGISTRY
from visionhousing.engine.context import TESTLAND, StateContext
from visionhousing.engine.core import Result
from visionhousing.ledger import Ledger, load_ledger, load_scenarios

__all__ = [
    "STATE_NAMES",
    "available_states",
    "compare_rows",
    "comparisons_payload",
    "county_detail",
    "dumps",
    "explanation",
    "export_csv",
    "export_html",
    "ledger_entries",
    "provenance",
    "reform_detail",
    "reform_list",
    "register_comparisons",
    "scenario_list",
    "state_summary",
    "validation_table",
]

STATE_NAMES = {
    "TL": "Testland (synthetic test state)",
    "DM": "Demo state (synthetic)",  # tests/fixtures/demo_state; web/js/content/states.js
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
            # A built pack has a manifest; a synthetic pack (the demo state) says so in its
            # data dictionary (read only then: a real pack's dictionary is large).
            dictionary = {} if manifest else (_read_json(folder / "data_dictionary.json") or {})
            out.append(
                {
                    "code": code,
                    "name": STATE_NAMES[code],
                    "synthetic": dictionary.get("synthetic") is True,
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
        "baseline_card": _baseline_card(ctx, _r(result.baseline)),
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
        **_county_notes(ctx, folder, result, fips),
        "state": result.state,
        "fips": fips,
        **facts,
        "tracts": tracts,
        "baseline": _r(result.baseline_counties[fips])
        if fips in result.baseline_counties
        else None,
        "reforms": {
            k: {
                "title": o.title,
                "added": _r(o.counties[fips]) if fips in o.counties else None,
                "readouts": {
                    name: _r(by[fips]) for name, by in o.readout_counties.items() if fips in by
                },
                "net_stock": _r(o.net_stock_counties[fips])
                if fips in o.net_stock_counties
                else None,
                "annual": _annual_row(o.annual, fips),
                "tracts_annual": _tracts_annual(o, fips),
            }
            for k, o in result.reforms.items()
        },
        "package": package,
        "annual": {
            "baseline": _annual_row(result.baseline_annual, fips),
            "package": _annual_row(result.package_annual, fips),
        },
        "as_of": result.as_of,
        "boundaries": {"type": "FeatureCollection", "features": features},
        "seed": result.seed,
        "draws": result.draws,
        "cycle": result.cycle,
        "hashes": _hashes(result),
        "synthetic": result.synthetic,
    }


def _annual_row(series: Any, fips: str) -> dict[str, Any] | None:
    """One county's years from an annual series: ``{years, p10, p50, p90, formula, ids}``."""
    if series is None or fips not in series.counties.geoids:
        return None
    t = series.counties
    i = t.geoids.index(fips)
    return {
        "label": series.label,
        "years": list(t.years),
        "p10": [float(x) for x in t.p10[i]],
        "p50": [float(x) for x in t.p50[i]],
        "p90": [float(x) for x in t.p90[i]],
        "formula": t.formula,
        "assumption_ids": list(t.assumption_ids),
        "source_ids": list(t.source_ids),
    }


def _tracts_annual(outcome: Any, fips: str) -> dict[str, Any] | None:
    """A tract-level reform's years for every tract of one county (``None``: county-level)."""
    table = outcome.tract_annual(county=fips)
    return None if table is None else table.to_dict()


def _county_notes(ctx: StateContext, folder: Path, result: Result, fips: str) -> dict[str, Any]:
    """Plain notes for the county panel: local law used, and the baseline's back-test record.

    ``local_law``: the sentences of :meth:`visionhousing.law.hooks.LocalLaw.plain_words`
    with the readings cited, when this county's ADU share comes from read local
    law. ``backtest``: :func:`visionhousing.baseline.places.county_note`. Either
    is ``None`` when it does not apply.
    """
    law = ctx.local_law
    words = law.plain_words(fips) if law is not None else None
    local = (
        {"text": words, "citations": list(law.citations.get(fips, [])), "source_id": law.source_id}
        if words and law is not None
        else None
    )
    note = None
    rc = result.run_context
    if rc is not None:
        from visionhousing.baseline.places import (
            county_note,
            load_place_rules,
            place_backtest_for_context,
        )

        pb = place_backtest_for_context(ctx, rc.baseline.rule, folder if folder.is_dir() else None)
        note = county_note(pb.counties, fips, load_place_rules())
    return {"notes": {"local_law": local, "backtest": note}}


# ------------------------------------------------------------------ reforms and ledger


def _scenario_kinds() -> dict[str, str]:
    """Scenario id to its kind (``benchmark``, ``state``, ``extra``) from ``scenarios.yaml``."""
    return {k: v.kind for k, v in load_scenarios(load_ledger()).items()}


def reform_list() -> list[dict[str, str]]:
    """Return every runnable reform: key, title, family and kind.

    ``kind`` is the scenario kind in ``assumptions/scenarios.yaml``: ``benchmark``
    (the five Handbook reforms), ``state`` (bills) or ``extra`` (named scenarios
    beyond the benchmarks). Screens that show the benchmarks by default keep to
    ``kind == "benchmark"``.
    """
    kinds = _scenario_kinds()
    return [
        {"key": s.key, "title": s.title, "family": s.family, "kind": kinds.get(s.key, "state")}
        for s in REGISTRY.values()
    ]


def scenario_list() -> list[dict[str, Any]]:
    """Every scenario in ``assumptions/scenarios.yaml``, with its description and how to run it.

    Each entry: ``id``, ``title``, ``kind`` (``baseline``, ``benchmark``, ``state``,
    ``extra``), ``description``, ``reforms``, ``runnable``, ``excludes`` (reform
    families it may not share a package with), ``variants`` (named variants, each
    with ``run_with``), ``notes`` and ``sources`` (id, citation, url).
    """
    ledger = load_ledger()
    out = []
    for sid, sc in load_scenarios(ledger).items():
        spec = REGISTRY.get(sid)
        out.append(
            {
                "id": sid,
                "title": sc.title,
                "kind": sc.kind,
                "description": " ".join(sc.description.split()),
                "reforms": list(sc.reforms),
                "runnable": sc.kind == "baseline" or all(r in REGISTRY for r in sc.reforms),
                "run_with": {"reforms": list(sc.reforms)},
                "excludes": list(spec.excludes) if spec is not None else [],
                "variants": _variants(spec.family) if spec is not None else [],
                "notes": [" ".join(n.split()) for n in sc.notes],
                "sources": [
                    {"id": s.id, "citation": " ".join(s.citation.split()), "url": s.url}
                    for s in sc.source
                ],
            }
        )
    return out


def _param(pid: str, ledger: Ledger) -> dict[str, Any]:
    return {"id": pid, **ledger[pid].model_dump(mode="json")}


def reform_detail(key: str, ledger: Ledger | None = None) -> dict[str, Any]:
    """Return a reform's definition (scenario text, sources), its ledger parameters and variants.

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
        "variants": _variants(spec.family if spec is not None else key),
        "ledger_hash": ledger.hash(),
    }


def _variants(family: str) -> list[dict[str, Any]]:
    """The named variants of a reform family (``assumptions/variants.yaml``), for display."""
    from visionhousing.engine.variants import variants_for

    return [
        {
            "name": v.name,
            "title": v.title,
            "question": v.question,
            "overrides": dict(v.overrides),
            "options": dict(v.options),
            "run_with": {"variant": v.name},
        }
        for v in variants_for(family).values()
    ]


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


def validation_table(ctx: StateContext, folder: Path | None = None) -> dict[str, Any]:
    """Return the baseline backtest skill table (one row per candidate rule).

    ``contest`` is the baseline rule contest (every rule's scores, the choosing
    rule, the winner), the out-of-time test of the bias correction and of the
    per-place-type ranges (before and after, by group), the surge-year check,
    the stress-setting alternative ranges and the sentences written from them
    (``baseline.contest``).

    ``by_place`` adds the chosen rule's back-test broken down by kind of place
    (county size, county type, urban or rural, Qualified Census Tract share,
    named counties), each county's own median signed error for the error map,
    and the sentences written from them (``baseline.places``). ``reliability``
    says how often ranges like the baseline's caught the real total in past
    years, out of time, for the state range and each county group
    (``baseline.reliability``; ``None`` for the synthetic state). ``cards`` names
    the study, origins, target, geography and release behind each of the state's
    coverage counts (``validation.cards``). ``folder`` is the state's pack folder,
    for the optional grouping tables.
    """
    from visionhousing.baseline import baseline_from_ledger
    from visionhousing.baseline.contest import contest_report, place_info_for_context, run_contest
    from visionhousing.baseline.places import (
        load_place_rules,
        place_backtest_for_context,
        read_pack_table,
    )

    places = place_info_for_context(ctx)
    baseline, skill = baseline_from_ledger(
        ctx.permits, ctx.ledger, widen=not ctx.synthetic, places=places, state=ctx.state
    )
    by_place = place_backtest_for_context(ctx, baseline.rule, folder).to_dict()
    place_rules = load_place_rules()
    counties = read_pack_table(folder, "counties")
    contest = contest_report(
        run_contest(ctx.permits, ctx.ledger, place_rules, places),
        ctx.permits,
        ctx.ledger,
        place_rules,
        state=ctx.state,
        tracts=ctx.tracts,
        typology=read_pack_table(folder, "typology_county"),
        incentives=read_pack_table(folder, "incentives_county"),
        names=None
        if counties is None or "name" not in counties.columns
        else dict(zip(counties["geoid"].astype(str), counties["name"].astype(str), strict=False)),
        widen=not ctx.synthetic,
    )
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
            "median_signed_log_error": "median of log(projected / actual); above zero ran high",
            "range_width": "mean of (P90 - P10) / projection",
            "eligible": "coverage inside the contest's band",
            "coverage_80": "share of actuals inside the 80% interval (leave one county out)",
            "coverage_cases": "cases with an interval",
            "chosen": "the rule the baseline uses",
        },
        "rows": rows,
        "baseline": _r(baseline.state),
        "by_place": _clean(by_place),
        "contest": _clean(contest),
        "reliability": _reliability(ctx, baseline.rule),
        "baseline_card": _baseline_card(ctx, _r(baseline.state)),
        "cards": _validation_cards(ctx),
        "data_hash": ctx.data_hash,
    }


def _validation_cards(ctx: StateContext) -> list[dict[str, Any]]:
    """The state's validation cards, with their fuller scores (empty for the synthetic state).

    Each card names the study, origins, target, geography and release behind one
    coverage count (``validation.cards``).
    """
    from visionhousing.validation.cards import cards_for

    return [] if ctx.synthetic else _clean(cards_for(ctx.state))


def _baseline_card(
    ctx: StateContext, baseline: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """The state's baseline card: years, permit columns, data release, cutoff, gap, checks.

    ``None`` for the synthetic state or a state without a card (``baseline.card``).
    ``view`` holds the card as display rows (``api.baseline_view``), with the
    reconciliation compared against ``baseline`` (the run's state record).
    """
    from visionhousing.api.baseline_view import view_for

    return None if ctx.synthetic else _clean(view_for(ctx.state, baseline))


def _reliability(ctx: StateContext, rule: str) -> dict[str, Any] | None:
    """How often ranges like the baseline's caught past totals (out of time), with sentences.

    ``None`` for the synthetic state, a state not counted, or a count made on another rule.
    """
    from visionhousing.baseline.reliability import label, reliability_for, sentence

    entry = None if ctx.synthetic else reliability_for(ctx.state)
    if not entry or entry.get("rule") != rule:
        return None
    s, lab = str(entry["sentence"]), str(entry["label"])
    county = entry.get("county_ranges") or {}
    groups = {
        g: {**c, "sentence": sentence(c, s), "label": label(c, lab)}
        for g, c in (county.get("groups") or {}).items()
    }
    return _clean(
        {
            "rule": rule,
            "source": entry.get("source"),
            "state_range": {
                **entry["state_range"],
                "sentence": sentence(entry["state_range"], s),
                "label": label(entry["state_range"], lab),
            },
            "county_ranges": {
                **(county.get("all") or {}),
                "sentence": sentence(county.get("all"), s),
                "label": label(county.get("all"), lab),
                "groups": groups,
            },
            "note": (
                "Out-of-time back-test of ranges built the way the baseline builds them. "
                "Reform ranges are simulation ranges given the baseline and are not counted."
            ),
        }
    )


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
    out = {
        "label": label,
        "reforms": list(result.reform_ids),
        "overrides": json.loads(json.dumps(dict(result.overrides), sort_keys=True, default=str)),
        "cycle": result.cycle,
        "baseline": _r(result.baseline),
        "reform_results": {
            k: {
                "title": o.title,
                "added": _r(o.added),
                "pct_change": _r(o.pct_change),
                "readouts": {name: _r(x) for name, x in o.readouts.items()},
                "readout_texts": dict(o.readout_texts),
            }
            for k, o in result.reforms.items()
        },
        "package": package,
        "total": None if result.total is None else _r(result.total),
        "hashes": _hashes(result),
    }
    market = getattr(result, "market", None)
    if market is not None:  # package option "market": today / choice (/ capacity) side by side
        out["market"] = market.to_dict()
    capacity = getattr(result, "capacity", None)
    if capacity is not None:  # run option capacity_screen: screened beside today's numbers
        out["capacity"] = capacity.to_dict()
    return out


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
        for rec in o.readouts.values():
            yield row("state readout", result.state, k, rec)
        for c, rec in o.counties.items():
            yield row("county", c, k, rec)
        for by in o.readout_counties.values():
            for c, rec in by.items():
                yield row("county readout", c, k, rec)
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


# ------------------------------------------------------------------ comparisons

COMPARISON_DEFAULT_DRAWS = 2000
"""Draws for a comparisons request when none is given (the engine's default)."""


def comparisons_payload(
    ctx: StateContext, parts: list[str], *, seed: int = 0, draws: int = COMPARISON_DEFAULT_DRAWS
) -> dict[str, Any]:
    """The comparisons (``engine.comparisons``) for one state, exactly as the engine gives them.

    State and county results only (no tract arrays). Parts: ``interactions``,
    ``dials``, ``matrix``, ``ablation``, ``ranking``. The text form of every
    number is added beside it (``api.comparison_text``), so the screens write none.
    Each part is kept on disk and read back while its inputs are unchanged
    (``api.comparison_cache``), so a restart does not lose it.
    """
    from visionhousing.api.comparison_cache import cached_comparisons, default_cache
    from visionhousing.api.comparison_text import comparison_texts

    data = cached_comparisons(ctx, parts, seed=seed, draws=draws, cache=default_cache())
    return comparison_texts(data)


def register_comparisons(app: Any, *, api: str, error: Any, context: Any, timed: Any) -> None:
    """Add ``GET {api}/states/{st}/comparisons?part=...&seed=&draws=`` to ``app``.

    ``part`` is one part, several comma-separated, or ``all`` (default
    ``interactions``). ``market`` (the package under project choice and the
    shared capacity limit) and ``tod_factorial`` (the TOD transit-definition
    factorial) are asked for by name only; ``all`` leaves them out.
    The reply is cached like a run; a state with many tracts takes a while the
    first time (the request may answer 504 and finish in the background, as
    runs do).
    """
    from visionhousing.api.models import LIMITS
    from visionhousing.api.runner import Runner
    from visionhousing.engine.comparisons import OPT_IN_PARTS, PARTS

    @app.get(f"{api}/states/{{st}}/comparisons")
    def comparisons_route(st: str, request: Request) -> Response:
        """Interactions, policy dials, the take-up x cost matrix, data ablation, ranking."""
        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        params = dict(request.query_params)
        raw = params.get("part", "interactions")
        parts = list(PARTS) if raw == "all" else [p.strip() for p in raw.split(",") if p.strip()]
        bad = [p for p in parts if p not in (*PARTS, *OPT_IN_PARTS)]
        if bad or not parts:
            known = [*PARTS, *OPT_IN_PARTS]
            raise error(400, "invalid_part", f"part must be 'all' or among {known}")
        try:
            seed = int(params.get("seed", 0))
            draws = int(params.get("draws", COMPARISON_DEFAULT_DRAWS))
        except ValueError:
            raise error(400, "invalid_query", "seed and draws must be whole numbers") from None
        if seed < 0 or not 1 <= draws <= LIMITS["draws"]:
            raise error(400, "invalid_query", f"seed >= 0 and 1 <= draws <= {LIMITS['draws']}")
        ctx = context(st)
        key = Runner.key(
            "comparisons", ctx.state, ctx.data_hash, ctx.calibration_hash, ctx.ledger.hash(),
            sorted(parts), seed, draws,
        )  # fmt: skip

        def job() -> bytes:
            return dumps(comparisons_payload(ctx, parts, seed=seed, draws=draws))

        return Response(timed(key, job), media_type="application/json")
