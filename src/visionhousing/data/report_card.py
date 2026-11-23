"""State data report card: for each model input, is it available, used or assumed, and how good?

Plain-language summary
----------------------
A state's results are only as good as its data. The report card lists every
input the model needs and says, for the state's pack:

- **status**: ``used`` (the pack has it and the engine reads it), ``available``
  (the pack has it as context or evidence, but the engine takes the number
  from the Assumption Ledger), ``assumed`` (no state data; a ledger parameter
  stands in), or ``missing`` (no data and no stand-in).
- **tier**: 0 = national files that exist for every state, 1 = published for
  many states (here, transit schedules), 2 = local refinement only.
- **vintage**: the edition of the source (survey years, release date, schedule
  date).
- **limits**: what the source cannot tell us, plus how complete it is in this
  state (for example, the share of tracts with a published median rent, or the
  share of bus stops whose departures could be counted).

It is built **from the pack alone**: the tables, ``data_dictionary.json`` and
(for real states) ``pack_manifest.json``. Nothing is fetched and the ledger is
not read; ledger ids are only named. It works for Testland (the synthetic test
state), whose files are plain CSV and which has no manifest.

Output: a JSON-ready dictionary (:func:`report_card`) and Markdown
(:func:`to_markdown`). Command line::

    uv run python -m visionhousing.data.report_card PA --out data/packs
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = ["INPUTS", "Input", "load_pack_files", "main", "report_card", "to_markdown"]

_TEXT = ("geoid", "county", "tract", "stop_id", "feed_id", "ntd_id", "schedule_feed")


@dataclass(frozen=True)
class Input:
    """One model input as the report card sees it.

    Attributes:
        id: Short name.
        label: Plain description.
        reforms: Which parts of the model use it.
        table: Pack table holding it ("" when the model always assumes it).
        columns: Columns that must be filled (prefixes ending in ``_`` match
            every column that starts with them).
        used: True when the engine reads it; False when it is context only.
        assumed_by: Ledger ids that stand in when there is no state data, or
            that the engine uses instead of the table.
        limits: Plain words on what it cannot tell us.
        sources: ``("tier2",)`` marks an input only local data could supply.
        derived_from: Tables the engine can compute the input from when the
            pack lacks its columns (for example Testland's transit access).
    """

    id: str
    label: str
    reforms: tuple[str, ...]
    table: str = ""
    columns: tuple[str, ...] = ()
    used: bool = True
    assumed_by: tuple[str, ...] = ()
    limits: str = ""
    sources: tuple[str, ...] = field(default=())
    derived_from: tuple[str, ...] = ()


INPUTS: tuple[Input, ...] = (
    Input(
        "permits",
        "Housing units authorized by building permits, by county, year and type",
        ("baseline", "all reforms"),
        "permits_county_year",
        ("units_1", "units_2", "units_3_4", "units_5plus"),
    ),
    Input(
        "housing_stock",
        "Existing homes by structure type, per tract",
        ("adu", "missing_middle", "parking", "fees"),
        "tracts",
        ("units_1_detached", "units_1_attached", "units_2", "units_3_4", "units_5_9"),
    ),
    Input(
        "rents",
        "Gross rents: brackets and medians by bedrooms, per tract",
        ("adu", "missing_middle", "fees", "parking"),
        "tracts",
        ("median_rent_b1", "median_rent_b2", "rent_"),
    ),
    Input(
        "home_values",
        "Owner home values: brackets and median, per tract",
        ("missing_middle", "fees", "parking"),
        "tracts",
        ("median_home_value", "value_"),
    ),
    Input(
        "lot_sizes",
        "Lot-size proxy and area type (from census blocks), per tract",
        ("adu", "missing_middle"),
        "tracts",
        ("lot_m2_per_unit_p25", "lot_m2_per_unit_p50", "lot_m2_per_unit_p75", "area_type"),
    ),
    Input(
        "blocks",
        "Census blocks: homes, land and an internal point",
        ("parking", "tod"),
        "blocks",
        ("housing_units", "land_area_m2", "lat", "lon"),
    ),
    Input(
        "transit_stops",
        "Transit stops and stations with their mode",
        ("parking", "tod"),
        "transit_stops",
        ("lat", "lon", "mode"),
    ),
    Input(
        "transit_frequency",
        "Weekday departures at bus stops (who counts as a frequent bus stop)",
        ("parking", "tod"),
        "transit_stops",
        ("weekday_departures",),
        assumed_by=("parking.transit_bus_min_departures",),
    ),
    Input(
        "transit_access",
        "Homes and land within 1/4, 1/2 and 3/4 mile of a major stop, per tract",
        ("parking", "tod"),
        "tracts",
        ("transit_units_d07", "transit_rich"),
        assumed_by=("parking.transit_rail_radius_miles", "parking.transit_bus_radius_miles"),
        derived_from=("blocks", "transit_stops"),
    ),
    Input(
        "house_price_trend",
        "House price index changes, per county and tract",
        ("context",),
        "prices_county",
        ("hpi_change_5yr_pct",),
        used=False,
    ),
    Input(
        "mortgage_rates",
        "Average offered mortgage rates by year",
        ("context",),
        "mortgage_rates_year",
        ("rate_30yr_pct",),
        used=False,
        assumed_by=("econ.mortgage_rate_30y",),
        limits="National only; the model's rate is the ledger entry, this table is its evidence.",
    ),
    Input(
        "construction_costs",
        "Construction wage index per county (local building-cost proxy)",
        ("context",),
        "costs_county",
        ("wage_index_county_vs_state",),
        used=False,
        assumed_by=(
            "adu.hard_cost_per_sqft",
            "missing_middle.hard_cost_per_sqft",
            "multifamily.hard_cost_per_sqft",
        ),
        limits="Not yet applied to the ledger's hard costs, which are statewide.",
    ),
    Input(
        "zoning_single_family",
        "Share of residential land zoned for single-family homes only",
        ("missing_middle",),
        assumed_by=("missing_middle.sf_only_zoned_share",),
        limits="No statewide zoning map exists; a local zoning atlas would be a Tier 2 refinement.",
        sources=("tier2",),
    ),
    Input(
        "adu_already_legal",
        "Share of lots where an ADU is already allowed",
        ("adu",),
        assumed_by=("adu.already_legal_share",),
        limits="Judgement from local ordinances; no complete municipal inventory.",
        sources=("tier2",),
    ),
    Input(
        "adu_physical_fit",
        "Share of lots with room for a detached ADU (needs parcels)",
        ("adu",),
        assumed_by=(
            "adu.fit_share_sf_detached",
            "adu.fit_share_sf_attached",
            "adu.fit_share_two_to_four",
        ),
        limits="Parcel data are local (Tier 2); the share is a calibrated range.",
        sources=("tier2",),
    ),
    Input(
        "development_fees",
        "Development and impact fees per unit",
        ("fees",),
        assumed_by=("fees.fee_total_per_unit", "fees.pa_documented_fee_per_unit"),
        limits="The benchmark $50,000 per unit; no statewide fee survey.",
        sources=("tier2",),
    ),
    Input(
        "parking_minimums",
        "Parking spaces required per unit",
        ("parking", "tod"),
        assumed_by=(
            "parking.required_spaces_per_unit_multifamily",
            "parking.required_spaces_per_unit_attached",
        ),
        limits="Set by each municipality; one typical value with a range.",
        sources=("tier2",),
    ),
)
"""Every input the report card checks, in display order."""


# --------------------------------------------------------------------------- reading


def _read_table(folder: Path, name: str) -> pd.DataFrame | None:
    for path in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
        if path.is_file():
            return pd.read_csv(path, dtype={c: str for c in _TEXT}, keep_default_na=True)
    return None


def load_pack_files(folder: Path) -> dict[str, Any]:
    """Read what the report card needs from a pack folder (tables, dictionary, manifest)."""
    folder = Path(folder)
    tables = {}
    for name in sorted({i.table for i in INPUTS if i.table} | {"transit_feeds"}):
        frame = _read_table(folder, name)
        if frame is not None:
            tables[name] = frame
    dictionary = json.loads((folder / "data_dictionary.json").read_text(encoding="utf-8"))
    manifest_path = folder / "pack_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    )
    return {"tables": tables, "dictionary": dictionary, "manifest": manifest}


# --------------------------------------------------------------------------- the card


def _columns(frame: pd.DataFrame, wanted: Sequence[str]) -> list[str]:
    out: list[str] = []
    for w in wanted:
        if w.endswith("_"):
            out += [
                c
                for c in frame.columns
                if c.startswith(w) and not c.endswith(("_moe", "_method")) and c != "rent_no_cash"
            ]
        elif w in frame.columns:
            out.append(w)
    return out


def _coverage(item: Input, frame: pd.DataFrame) -> tuple[float, str]:
    """Share of rows where the input is filled, and a sentence saying so."""
    if item.id == "transit_frequency":
        bus = frame[frame["mode"].astype(str) == "bus"]
        if "frequency" in frame.columns:
            counted = int((bus["frequency"].astype(str) == "counted").sum())
        else:
            counted = int(bus["weekday_departures"].notna().sum())
        share = counted / len(bus) if len(bus) else 0.0
        return share, f"departures counted for {counted:,} of {len(bus):,} bus stops ({share:.0%})"
    cols = _columns(frame, item.columns)
    if not cols or frame.empty:
        return 0.0, "no rows"
    filled = frame[cols].notna().all(axis=1)
    share = float(filled.mean())
    return share, f"{share:.0%} of {len(frame):,} {item.table.replace('_', ' ')} rows filled"


def _sources_for(item: Input, dictionary: Mapping[str, Any]) -> tuple[list[str], str]:
    srcs: list[str] = []
    tiers: list[str] = []
    for c in dictionary.get("columns", []):
        if c.get("table") == item.table and (
            c.get("name") in item.columns
            or any(w.endswith("_") and str(c.get("name")).startswith(w) for w in item.columns)
        ):
            srcs += [s.strip() for s in str(c.get("source", "")).split(",") if s.strip()]
            tiers.append(str(c.get("tier", "0")))
    return sorted(set(srcs)), (max(tiers) if tiers else "0")


def _vintage(srcs: Sequence[str], manifest: Mapping[str, Any] | None, synthetic: bool) -> str:
    if synthetic:
        return "synthetic"
    rows = {r["id"]: r for r in (manifest or {}).get("provenance", [])}
    parts = [f"{s}: {rows[s]['version']}" for s in srcs if s in rows]
    return "; ".join(parts) or "unknown"


def _limits(srcs: Sequence[str], manifest: Mapping[str, Any] | None) -> list[str]:
    rows = {r["id"]: r for r in (manifest or {}).get("provenance", [])}
    return [str(rows[s]["limits"]).strip() for s in srcs if s in rows and rows[s].get("limits")]


def _schedule_dates(tables: Mapping[str, pd.DataFrame]) -> str:
    feeds = tables.get("transit_feeds")
    if feeds is None or feeds.empty or "schedule_date" not in feeds.columns:
        return ""
    used = feeds[feeds["bus_stops_counted"].fillna(0) > 0]["schedule_date"].dropna()
    return f"schedules of {used.min()} to {used.max()}" if len(used) else ""


def report_card(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Build the report card from :func:`load_pack_files` output.

    Returns:
        ``{"state", "synthetic", "pack_hash", "counts", "inputs": [...]}``; each
        input has ``id``, ``label``, ``reforms``, ``status``, ``tier``,
        ``vintage``, ``coverage`` (0-1 or ``None``), ``limits`` (list of text),
        ``sources`` and ``assumed_by`` (ledger ids).
    """
    tables, dictionary, manifest = pack["tables"], pack["dictionary"], pack["manifest"]
    synthetic = bool(dictionary.get("synthetic"))
    rows = []
    for item in INPUTS:
        frame = tables.get(item.table) if item.table else None
        srcs, tier = _sources_for(item, dictionary) if item.table else ([], "2")
        coverage: float | None = None
        limits: list[str] = []
        if frame is not None and _columns(frame, item.columns) and len(frame):
            coverage, sentence = _coverage(item, frame)
            limits.append(sentence)
        derivable = (
            coverage is None
            and bool(item.derived_from)
            and all(len(tables.get(t, ())) for t in item.derived_from)
        )
        if derivable:
            coverage = 1.0
            limits.append(
                "not stored in this pack; computed at run time from "
                + " and ".join(t.replace("_", " ") for t in item.derived_from)
            )
            srcs = sorted(
                {
                    s
                    for t in item.derived_from
                    for s in _sources_for(Input(t, "", (), t, ("lat",)), dictionary)[0]
                }
            )
        has_data = coverage is not None and coverage > 0
        if has_data:
            status = "used" if item.used else "available"
        else:
            status = "assumed" if item.assumed_by else "missing"
        if not has_data and item.table:
            limits.append(f"no {item.table.replace('_', ' ')} data in this pack")
        if item.limits:
            limits.append(item.limits)
        limits += _limits(srcs, manifest)
        vintage = _vintage(srcs, manifest, synthetic) if item.table else "ledger"
        if item.id == "transit_frequency" and has_data and not synthetic:
            vintage = "; ".join(v for v in (vintage, _schedule_dates(tables)) if v)
        rows.append(
            {
                "id": item.id,
                "label": item.label,
                "reforms": list(item.reforms),
                "status": status,
                "tier": 2 if item.sources == ("tier2",) else int(tier),
                "vintage": vintage if has_data else "ledger",
                "coverage": None if coverage is None else round(coverage, 4),
                "limits": limits,
                "sources": srcs,
                "assumed_by": list(item.assumed_by),
            }
        )
    counts = {s: sum(r["status"] == s for r in rows) for s in ("used", "available", "assumed")}
    counts["missing"] = sum(r["status"] == "missing" for r in rows)
    return {
        "state": dictionary.get("state", ""),
        "synthetic": synthetic,
        "pack_hash": (manifest or {}).get("pack_hash"),
        "counts": counts,
        "inputs": rows,
    }


def to_markdown(card: Mapping[str, Any]) -> str:
    """Render the report card as a Markdown table plus a limits list."""
    title = f"# State data report card: {card['state']}"
    lines = [title, ""]
    if card["synthetic"]:
        lines += ["Synthetic test state (Testland). Not real data.", ""]
    if card.get("pack_hash"):
        lines += [f"Pack hash `{card['pack_hash']}`.", ""]
    c = card["counts"]
    lines += [
        f"{c['used']} inputs used, {c['available']} available as context, "
        f"{c['assumed']} assumed from the ledger, {c['missing']} missing.",
        "",
        "| Input | Status | Tier | Vintage | Coverage | Used by |",
        "|---|---|---|---|---|---|",
    ]
    for r in card["inputs"]:
        cov = "-" if r["coverage"] is None else f"{r['coverage']:.0%}"
        vintage = str(r["vintage"]).replace("|", "/")
        lines.append(
            f"| {r['label']} | {r['status']} | {r['tier']} | {vintage} | {cov} | "
            f"{', '.join(r['reforms'])} |"
        )
    lines += ["", "## Limits", ""]
    for r in card["inputs"]:
        lines.append(f"- **{r['label']}** ({r['status']}).")
        lines += [f"  - {text}" for text in r["limits"]]
        if r["assumed_by"]:
            lines.append(f"  - Ledger: {', '.join(f'`{i}`' for i in r['assumed_by'])}.")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Print or write the report card for a state code (``TL`` for Testland)."""
    from visionhousing.engine.context import pack_dir

    parser = argparse.ArgumentParser(description="State data report card")
    parser.add_argument("state", help="state code, for example PA, or TL for Testland")
    parser.add_argument("--out", type=Path, help="folder to write <STATE>_report_card.json/.md")
    args = parser.parse_args(argv)
    card = report_card(load_pack_files(pack_dir(args.state)))
    md = to_markdown(card)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stem = args.out / f"{args.state.upper()}_report_card"
        stem.with_suffix(".json").write_text(json.dumps(card, indent=1) + "\n", encoding="utf-8")
        stem.with_suffix(".md").write_text(md, encoding="utf-8")
    else:
        print(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
