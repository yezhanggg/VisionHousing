"""Build the zoning-district tables of the Pennsylvania pilot and add them to a pack.

Plain-language summary
----------------------
Puts :mod:`visionhousing.law.zone_rules` and :mod:`visionhousing.law.rule_weights`
together for the sample in ``assumptions/law/zone_weights.yaml``:

1. reads the cached district and overlay maps and block outlines
   (:mod:`visionhousing.data.sources.zoning_layers`; fetched by script when
   missing), the pack's ``blocks`` and ``tracts`` tables and the 2020 block
   assignment file;
2. splits every block of each sample jurisdiction among its districts and
   overlays by area, for the current map and for each dated map;
3. resolves each zone to its reviewed rule on each interval of the window and
   sorts it into an opportunity class per benchmark;
4. writes two compact pack tables: ``zone_rule`` (the reviewed rows, without
   quotes) and ``rule_area_weights`` (zone-version, tract and jurisdiction
   rows of lots, homes and land; no outlines).

:func:`report` turns the tables into the numbers the pilot is judged by: the
per-benchmark shares with denominators, the coverage gained, a worked trace
from one citation to the state output, and how the law-changes view's shares
would move.
"""

from __future__ import annotations

import contextlib
import gzip
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.law import rule_weights as rw
from visionhousing.law import zone_rules as zr

__all__ = [
    "TABLES",
    "add_zone_tables",
    "build_tables",
    "describe",
    "report",
]

TABLES: dict[str, dict[str, object]] = {
    "zone_rule": {
        "key": ["rule_id"],
        "sources": ["phl_li_adu_checklist", "pgh_legistar", "pa_municipal_ordinances"],
        "description": (
            "Reviewed zoning rules of the pilot sample by jurisdiction, district, overlay and "
            "validity interval (assumptions/law/zone_rules.yaml): use permission, review path, "
            "homes by right, height, lot minimum, ADU forms and size, owner occupancy, base and "
            "overlay parking, utility conditions and the clause on unlisted uses, each stated "
            "or 'unknown'."
        ),
        "tier": "2",
    },
    "rule_area_weights": {
        "key": [
            "level",
            "jurisdiction",
            "tract",
            "map_version",
            "period",
            "district",
            "overlays",
            "valid_from",
            "benchmark",
            "opportunity_class",
        ],
        "sources": [
            "phl_zoning_base",
            "phl_zoning_base_dated",
            "phl_zoning_overlays",
            "pgh_zoning",
            "pgh_parking_reduction_overlay",
            "tiger_2020_tabblock_shapes",
            "tiger_2020_tabblock",
            "census_baf_2020_mcd",
            "acs5_summary_file",
        ],
        "description": (
            "Single-family lots, 2020 homes and land of the pilot sample by jurisdiction, zone "
            "version (district, rule-bearing overlays, validity interval) and opportunity class "
            "per benchmark; blocks split among districts by area, with unresolved, unmatched "
            "and sliver totals. Derived counts only; no outlines."
        ),
        "tier": "2",
    },
}
"""The two pack tables this module adds (tier 2: local refinement)."""


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and plain description of a column of :data:`TABLES`."""
    cols = zr.ZONE_RULE_COLUMNS if table == "zone_rule" else rw.WEIGHT_COLUMNS
    if name not in cols:
        raise ValueError(f"no data-dictionary entry for {table}.{name}")
    return cols[name]


def _shapes(
    doc: Mapping[str, Any],
    code_field: str,
    *,
    fixed: str | None = None,
    keep_types: list[str] | None = None,
) -> list[tuple[str, list]]:
    out = []
    for f in doc["features"]:
        a = f.get("attributes") or {}
        if keep_types and str(a.get("type")) not in keep_types:
            continue
        code = fixed or str(a.get(code_field) or "").strip()
        if code and f.get("rings"):
            out.append((code, f["rings"]))
    return out


def _block_cousub(cache: Path, fips: str) -> pd.DataFrame:
    path = Path(cache) / "local" / "census" / f"block_cousub_2020_{fips}.csv.gz"
    with gzip.open(path, "rt") as fh:
        return pd.read_csv(fh, dtype=str)


def build_tables(
    tables: Mapping[str, pd.DataFrame],
    cache: Path,
    *,
    settings: dict[str, Any] | None = None,
    rules: list[zr.ZoneRule] | None = None,
    layers: Mapping[str, Mapping[str, Any]] | None = None,
    block_shapes: Mapping[str, Any] | None = None,
    block_cousub: pd.DataFrame | None = None,
    fips: str = "42",
) -> dict[str, pd.DataFrame]:
    """The ``zone_rule`` and ``rule_area_weights`` tables, plus the block pieces (not packed).

    ``tables`` needs the pack's ``blocks`` and ``tracts``. Layers, block shapes
    and the block assignment are read from the cache unless given (tests pass
    them). Returns ``{"zone_rule", "rule_area_weights", "pieces", "blocks"}``.
    """
    from visionhousing.data.sources import zoning_layers as zl

    settings = settings or zr.load_settings()
    rules = rules if rules is not None else zr.load_rows()
    layer_docs: dict[str, Mapping[str, Any]] = dict(layers or {})
    if layers is None:
        for name, (doc, _p) in zl.fetch_sample_layers(cache, settings).items():
            layer_docs[name] = doc
    if block_shapes is None:
        block_shapes = zl.fetch_block_shapes(cache, fips, settings)[0]
    if block_cousub is None:
        block_cousub = _block_cousub(cache, fips)
    window = (
        date.fromisoformat(settings["window"]["start"]),
        date.fromisoformat(settings["window"]["end"]),
    )
    benches = settings["benchmarks"]
    rule = rw.SplitRule.from_settings(settings["split"])
    juris = [str(j["geoid"]) for j in settings["sample"]]
    weights = rw.block_weights(tables["blocks"], tables["tracts"], block_cousub, juris)
    shapes = block_shapes["blocks"]
    rows: list[pd.DataFrame] = []
    totals: list[dict[str, Any]] = []
    all_pieces: list[pd.DataFrame] = []
    all_blocks: list[pd.DataFrame] = []
    for j in settings["sample"]:
        jid = str(j["geoid"])
        w = weights[weights["jurisdiction"] == jid]
        w = w[(w["sf_lots"] > 0) | (w["housing_units"] > 0)]
        maps = [str(m) for m in j.get("maps") or []]
        ov_name = j.get("overlays")
        ov_shapes = None
        if ov_name:
            spec = settings["layers"][ov_name]
            ov_shapes = _shapes(
                layer_docs[ov_name],
                str(spec.get("overlay_field")),
                fixed=spec.get("overlay_code"),
                keep_types=spec.get("keep_types"),
            )
            keep = zr.relevant_overlays(rules, jid)
            ov_shapes = [s for s in ov_shapes if s[0] in keep]
        if not maps:
            pieces = pd.DataFrame(
                {"block": w["block"], "district": rw.NO_MAP, "overlays": "", "share": 1.0}
            )
            info = pd.DataFrame(
                {
                    "block": w["block"],
                    "unmatched": 0.0,
                    "sliver": 0.0,
                    "overlap": 0.0,
                    "method": "whole_jurisdiction",
                }
            )
            version = f"no map (read {date.today().isoformat()})"
            rows.append(
                rw.zone_weights(
                    pieces,
                    weights,
                    rules,
                    jurisdiction=jid,
                    map_name=None,
                    map_version=version,
                    period="window",
                    window=window,
                    benches=benches,
                )
            )
            rows.append(rw.tract_rows(pieces, weights, rows[-1]))
            totals.append(rw.jurisdiction_row(jid, version, "window", weights, info, pieces))
            all_pieces.append(pieces.assign(jurisdiction=jid, map_version=version))
            all_blocks.append(info.assign(jurisdiction=jid, map_version=version))
            continue
        blocks = {b: shapes[b] for b in w["block"] if b in shapes}
        missing = [b for b in w["block"] if b not in shapes]
        current_pieces = None
        for k, name in enumerate(maps):
            spec = settings["layers"][name]
            doc = layer_docs[name]
            dshapes = _shapes(doc, str(spec["district_field"]))
            skip = spec.get("not_a_district") or ()
            pieces, info = rw.split_blocks(blocks, dshapes, ov_shapes, rule, not_a_district=skip)
            if missing:
                lost = pd.DataFrame(
                    {"block": missing, "district": rw.UNMATCHED, "overlays": "", "share": 1.0}
                )
                pieces = pd.concat([pieces, lost], ignore_index=True)
                info = pd.concat(
                    [
                        info,
                        pd.DataFrame(
                            {
                                "block": missing,
                                "unmatched": 1.0,
                                "sliver": 0.0,
                                "overlap": 0.0,
                                "method": "no_outline",
                            }
                        ),
                    ],
                    ignore_index=True,
                )
            version = str(doc.get("map_version") or name)
            if k == 0:
                period, on, changed = "window", None, None
                current_pieces = pieces
            else:
                period, on = "map_history", window[0]
                changed = rw.district_change(pieces, current_pieces, w)
            z = rw.zone_weights(
                pieces,
                weights,
                rules,
                jurisdiction=jid,
                map_name=name,
                map_version=version,
                period=period,
                window=window,
                benches=benches,
                on=on,
            )
            rows.append(z)
            if k == 0:
                rows.append(rw.tract_rows(pieces, weights, z))
            totals.append(
                rw.jurisdiction_row(
                    jid, version, period, weights, info, pieces, district_changed_lots=changed
                )
            )
            all_pieces.append(pieces.assign(jurisdiction=jid, map_version=version))
            all_blocks.append(info.assign(jurisdiction=jid, map_version=version))
    table = pd.concat([*rows, pd.DataFrame(totals)], ignore_index=True)
    table = table.reindex(columns=list(rw.WEIGHT_COLUMNS))
    table = table[~((table["level"] == "tract") & (table["sf_lots"].astype(float) <= 0))]
    for c in (
        "sf_lots",
        "housing_units",
        "land_m2",
        "unmatched_area_share",
        "sliver_share",
        "overlap_area_share",
        "unresolved_geometry_lots",
        "district_changed_lots",
    ):
        table[c] = pd.to_numeric(table[c], errors="coerce").round(4)
    for c in ("weight", "window_share"):
        table[c] = pd.to_numeric(table[c], errors="coerce").round(10)
    table["blocks"] = pd.to_numeric(table["blocks"], errors="coerce").astype("Int64")
    table = table.fillna({c: "" for c in table.columns if table[c].dtype == object})
    table = table.reset_index(drop=True)
    return {
        "zone_rule": zr.zone_rule_table(rules, fips),
        "rule_area_weights": table,
        "pieces": pd.concat(all_pieces, ignore_index=True),
        "blocks": pd.concat(all_blocks, ignore_index=True),
    }


def add_zone_tables(
    folder: Path,
    cache: Path,
    *,
    lock: Callable[[], Any] | None = None,
    **kw: Any,
) -> dict[str, pd.DataFrame]:
    """Add (or refresh) ``zone_rule`` and ``rule_area_weights`` in a built pack.

    Only for a state with a sample (Pennsylvania); other packs are left as they
    are. ``lock`` is an optional context-manager factory held while files are
    written. Rewrites the data dictionary and manifest like the other add-on steps.
    """
    from visionhousing.data import pack

    folder = Path(folder)
    settings = kw.pop("settings", None) or zr.load_settings()
    fips = "42"
    if folder.name.upper() != "PA":
        return {}
    tables = {n: pack.load_pack(folder)[n] for n in ("blocks", "tracts")}
    built = build_tables(tables, Path(cache), settings=settings, fips=fips, **kw)
    out = {k: built[k] for k in TABLES}
    with lock() if lock is not None else contextlib.nullcontext():
        pack._rewrite(folder, Path(cache), out, [])
    return built


# --------------------------------------------------------------------------- report


def report(
    weights: pd.DataFrame,
    *,
    state_sf_lots: float,
    rules: list[zr.ZoneRule] | None = None,
) -> dict[str, Any]:
    """The pilot's numbers from its tables (every number worked out here, none typed).

    Returns ``{"shares": {benchmark: rows}, "state": {benchmark: shares of the
    state's lots}, "coverage": {...}, "checks": [...]}``. Coverage counts lots
    whose ADU class is not unresolved, by the review state it rests on.
    """
    out: dict[str, Any] = {"shares": {}, "state": {}, "u13": {}}
    for b in zr.BENCHMARKS:
        out["shares"][b] = rw.exposure_shares(weights, b, by=["jurisdiction"])
        out["state"][b] = rw.state_exposure(weights, b, state_sf_lots)
        out["u13"][b] = {
            "verified_only": rw.u13_answers(weights, b),
            "with_guidance": rw.u13_answers(weights, b, include_guidance=True),
        }
    z = weights[
        (weights["level"] == "zone")
        & (weights["period"] == "window")
        & (weights["benchmark"] == "adu")
    ]
    answered = z[z["opportunity_class"] != "unresolved"]
    cov = answered.groupby("review_state")["sf_lots"].sum().to_dict()
    out["coverage"] = {
        "sample_lots": float(z["sf_lots"].sum()),
        "answered_lots_by_review_state": {k: float(v) for k, v in cov.items()},
        "state_sf_lots": float(state_sf_lots),
    }
    out["checks"] = rw.check_weights(weights)
    return out
