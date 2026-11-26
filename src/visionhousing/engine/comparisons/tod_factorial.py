"""TOD factorial comparison: which definition of "near good transit" moves the estimate.

Plain-language summary
----------------------
The transit-oriented density (TOD) benchmark defines a major transit stop and a
distance in one way: a rail station, or a bus stop with at least 64 scheduled
weekday departures; 1/2 mile from rail and 1/4 mile from a frequent bus stop; in
a straight line. Three choices sit inside that definition. This part changes them
one at a time, and all together, on the **same stop table** and the **same seeded
draws**, so every difference from the benchmark is caused by the definition alone:

- **service rule**: ``daily`` (the benchmark), ``peak_average`` (a bus on average
  every 15 minutes or better in both weekday peaks, on one platform side) or
  ``peak_max_gap`` (the longest scheduled gap in each peak is at most 15 minutes,
  window edges included);
- **access**: ``straight`` line (the benchmark) or a ``network`` walk on mapped
  streets and paths;
- **distance**: ``d07`` (the benchmark's rail 1/2 mile, bus 1/4 mile), or 1/4, 1/2
  or 3/4 mile from any major stop.

Each cell is a full TOD funnel run (``scenarios.tod.tod_funnel``) with the
benchmark's ledger, seed and absorption cap. For each cell the part reports the
state's added units authorized 2027-2031 (P10 / P50 / P90) and the **paired
difference from the benchmark**, worked out draw by draw before any percentile is
taken, with the share of draws in which the cell is below or above the benchmark
and its Monte Carlo standard error.

Not every cell can be measured, and the part says which and why, as distinct outcomes:

- ``not_defined``: the walkshed table holds single distances, so the benchmark's
  mixed distance has no network version;
- ``not_measured``: the data for that cell are not in the pack (for example no
  walkshed drawn around the daily-rule stops);
- ``not_measurable``: the rule needs data the cache cannot give (an exact longest gap
  needs departure times; the cached schedules hold departures per hour);
- ``schedules_missing``: no bus stop has the schedule counts the rule needs;
- ``no_qualifying_service``: stops were measured but no land is near a qualifying one;
- ``infeasible``: land is near qualifying stops but the prototype pencils nowhere (P50);
- ``small_estimate``: above zero but below ``small_estimate_homes`` (P50), shown rounded;
- ``estimate``: an ordinary likely range.

**The longest-gap rule from hourly counts.** The cached schedule tables hold
departures per platform side and hour (``data/raw/transit/gtfs_<id>_peaks.csv.gz``).
From them the longest gap can be bounded from below
(``scenarios.tod.hourly_gap_lower_bound``) but never confirmed, because departures
may be bunched inside an hour. A stop side whose bound is above the headway fails
for sure; any other is "not ruled out". The ``peak_max_gap`` cells therefore keep
the stops not ruled out and are labelled an **upper reading** (``reading`` =
``upper``): eligible land can only shrink once departure times are read.

Every cell carries the footprint label from the transit status ladder
(``features.transit_status``): where some agencies' service is unmeasured, the
number is the measured contribution, labelled partial, and when the measured
share of bus stops is below ``tod.status_min_measured_bus_share`` the statewide
range is marked not shown, exactly as for the benchmark.

Settings: ``assumptions/comparisons.yaml`` (``tod_factorial``). Method:
``docs/methods/tod.md`` ("Factorial comparison of the transit definition").
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine.comparisons.bench import Bench
from visionhousing.engine.comparisons.common import SPAN, record_dict, share, state_record
from visionhousing.ledger import LedgerError

__all__ = [
    "PART",
    "FactorialSettings",
    "load_factorial_settings",
    "load_peak_tables",
    "tod_factorial",
]

PART = "tod_factorial"
"""Name of the comparisons part (opt-in: not in ``PARTS``)."""

ROOT = Path(__file__).resolve().parents[4]
RAW_TRANSIT = ROOT / "data" / "raw" / "transit"
"""Where the schedule caches live (read only; this part never fetches)."""

OUTCOMES = (
    "estimate",
    "small_estimate",
    "infeasible",
    "no_qualifying_service",
    "schedules_missing",
    "not_measurable",
    "not_measured",
    "not_defined",
)
"""Cell outcomes, from an ordinary range to a cell that has no meaning."""

FACTORS = ("service", "access", "distance")
PEAK_SOURCES = ("gtfs_agency_feeds", "ntm_stops")
WALK_SOURCES = ("osm_overpass_walk", "gtfs_agency_feeds", "cb_2020_tract")
BLOCK_SOURCES = ("census_blocks_2020", "ntm_stops", "gtfs_agency_feeds")


@dataclass(frozen=True)
class FactorialSettings:
    """``assumptions/comparisons.yaml`` -> ``tod_factorial``."""

    id: str
    benchmark: tuple[str, str, str]
    services: tuple[str, ...]
    accesses: Mapping[str, str]
    distances: tuple[str, ...]
    anchor: tuple[str, str]
    edge_rule: str
    dedupe_minutes: float
    small_homes: float


def _distance_key(value: Any) -> str:
    """``0.5`` -> ``"0.5"``; ``"d07"`` stays."""
    if isinstance(value, str) and value.lower() == "d07":
        return "d07"
    return f"{float(value):g}"


def load_factorial_settings(path: Path | str | None = None) -> FactorialSettings:
    """Read and check the ``tod_factorial`` block.

    Raises:
        LedgerError: The block is missing or a field does not make sense.
    """
    from visionhousing.scenarios.tod import ACCESSES, DISTANCES, SERVICES

    p = Path(path) if path else ROOT / "assumptions" / "comparisons.yaml"
    import yaml

    data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get(PART)
    if not isinstance(data, dict):
        raise LedgerError(f"{p.name}: no '{PART}' block")
    problems: list[str] = []
    services = tuple(str(s) for s in data.get("services") or ())
    if not services or any(s not in SERVICES for s in services):
        problems.append(f"services must be among {SERVICES}")
    accesses = {str(k): " ".join(str(v).split()) for k, v in (data.get("accesses") or {}).items()}
    if not accesses or any(a not in ACCESSES for a in accesses):
        problems.append(f"accesses must be among {ACCESSES}")
    known = {_distance_key(d) for d in DISTANCES}
    distances = tuple(_distance_key(d) for d in data.get("distances") or ())
    if not distances or any(d not in known for d in distances):
        problems.append(f"distances must be among {sorted(known)}")
    b = data.get("benchmark") or {}
    bench = (str(b.get("service")), str(b.get("access")), _distance_key(b.get("distance", "d07")))
    if bench[0] not in services or bench[1] not in accesses or bench[2] not in distances:
        problems.append("benchmark must be one cell of the grid")
    a = data.get("access_anchor") or {}
    anchor = (str(a.get("service")), _distance_key(a.get("distance", 0.5)))
    if anchor[0] != "peak_average" or anchor[1] not in distances or anchor[1] == "d07":
        problems.append(
            "access_anchor needs service peak_average (the walkshed table's stops) and a "
            "single distance"
        )
    gap = data.get("max_gap") or {}
    if str(gap.get("edge_rule")) != "window_edges":
        problems.append("max_gap.edge_rule must be 'window_edges'")
    small = float(data.get("small_estimate_homes", -1))
    if small < 0:
        problems.append("small_estimate_homes must be >= 0")
    if problems:
        raise LedgerError(f"{p.name} {PART}: " + "; ".join(problems))
    return FactorialSettings(
        id=str(data.get("id") or f"comparisons.{PART}"),
        benchmark=bench,
        services=services,
        accesses=accesses,
        distances=distances,
        anchor=anchor,
        edge_rule="window_edges",
        dedupe_minutes=float(gap.get("dedupe_minutes", 0)),
        small_homes=small,
    )


# ------------------------------------------------------------------ inputs


def _transit_feeds(state: Any) -> pd.DataFrame | None:
    """The pack's ``transit_feeds`` table (tests may place one in ``state.derived``)."""
    held = getattr(state, "derived", {}).get(("pack_table", "transit_feeds"))
    if held is not None:
        return held
    from visionhousing.scenarios.extra_common import _folder

    folder = _folder(state)
    if folder is None:
        return None
    for cand in (folder / "transit_feeds.csv.gz", folder / "transit_feeds.csv"):
        if cand.is_file():
            return pd.read_csv(cand, dtype={"feed_id": str, "schedule_feed": str, "ntd_id": str})
    return None


def load_peak_tables(
    feeds: pd.DataFrame | None, raw_dir: Path | None = None
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, Any]], list[str]]:
    """The cached hourly peak tables of the schedule feeds the pack paired (never fetched).

    Returns:
        ``(tables, notes, missing)``: schedule feed id to its table and to its note
        (date, day rule, fetch date), and the paired feeds with no cached table.
    """
    from visionhousing.data.sources.transit_peaks import read_peaks
    from visionhousing.scenarios.tod import _paired_schedules

    if feeds is None or feeds.empty:
        return {}, {}, []
    where = Path(raw_dir) if raw_dir is not None else RAW_TRANSIT
    tables: dict[str, pd.DataFrame] = {}
    notes: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for mdb in sorted(set(_paired_schedules(feeds).values())):
        path = where / f"gtfs_{mdb}_peaks.csv.gz"
        note = where / f"gtfs_{mdb}_peaks_note.json"
        if not path.is_file():
            missing.append(mdb)
            continue
        tables[mdb] = read_peaks(path)
        if note.is_file():
            raw = json.loads(note.read_text(encoding="utf-8"))
            notes[mdb] = {k: raw.get(k) for k in ("date", "day_rule", "fetched_on", "status")}
    return tables, notes, missing


# ------------------------------------------------------------------ cells


def _cell_key(service: str, access: str, distance: str) -> str:
    return f"{service}|{access}|{distance}"


def _distance_value(d: str) -> Any:
    return "d07" if d == "d07" else float(d)


def _service_words(service: str, ledger: Any) -> str:
    """Plain words for a service rule, from the ledger (no number typed in code)."""
    deps = ledger["parking.transit_bus_min_departures"].central
    hw = ledger["tod.frequent_bus_max_headway_min"].central
    am = (
        ledger["tod.frequent_bus_am_start_hour"].central,
        ledger["tod.frequent_bus_am_end_hour"].central,
    )
    pm = (
        ledger["tod.frequent_bus_pm_start_hour"].central,
        ledger["tod.frequent_bus_pm_end_hour"].central,
    )
    windows = f"{am[0]:g}:00-{am[1]:g}:00 and {pm[0]:g}:00-{pm[1]:g}:00"
    if service == "daily":
        return (
            f"rail, or a bus stop with at least {deps:g} scheduled departures on the "
            "representative weekday"
        )
    if service == "peak_average":
        return (
            f"rail, or a bus stop whose busiest platform side averages a bus every {hw:g} minutes "
            f"or better in both weekday peaks ({windows})"
        )
    return (
        f"rail, or a bus stop whose longest scheduled gap is {hw:g} minutes or less in both "
        f"weekday peaks ({windows}) on one platform side, window edges included"
    )


SERVICE_IDS = {
    "daily": ("parking.transit_bus_min_departures",),
    "peak_average": (
        "tod.frequent_bus_max_headway_min",
        "tod.frequent_bus_am_start_hour",
        "tod.frequent_bus_am_end_hour",
        "tod.frequent_bus_pm_start_hour",
        "tod.frequent_bus_pm_end_hour",
        "tod.frequent_bus_opposite_heading_deg",
    ),
}
SERVICE_IDS["peak_max_gap"] = SERVICE_IDS["peak_average"]


def _draw_share(mask: np.ndarray) -> dict[str, float]:
    """Share of draws where ``mask`` holds, and its Monte Carlo standard error."""
    n = mask.size
    p = float(mask.mean()) if n else 0.0
    return {"share": p, "se": float(np.sqrt(p * (1.0 - p) / n)) if n else 0.0}


def tod_factorial(
    bench: Bench,
    settings: FactorialSettings | None = None,
    *,
    peaks: Mapping[str, pd.DataFrame] | None = None,
    peak_notes: Mapping[str, Mapping[str, Any]] | None = None,
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    """The factorial comparison of the TOD transit definition (see the module summary).

    Args:
        bench: The prepared run (its seed, draws and ledger are the benchmark's).
        settings: The ``tod_factorial`` settings (default: read the file).
        peaks: Schedule feed id to its hourly peaks table (default: the cache).
        peak_notes: Their notes (dates); default from the cache.
        raw_dir: Schedule cache folder (default ``data/raw/transit``).

    Returns:
        JSON-ready: ``benchmark``, ``cells`` (the full grid), ``one_at_a_time``,
        ``access_anchor``, ``moves_most``, ``stops`` (major stops per rule),
        ``footprint``, ``schedules`` and ``checks``.
    """
    from visionhousing.engine.adapters import MF_COLUMN, _tod_tracts, tod_by_right
    from visionhousing.features.transit import TransitRule
    from visionhousing.features.transit_walk import PeakRule
    from visionhousing.ledger.loader import Ledger
    from visionhousing.scenarios import tod as T  # noqa: N812

    cfg = settings or load_factorial_settings()
    ctx = bench.run_context
    state = ctx.state
    draws = bench.draws
    base = bench.alone("tod")
    ledger: Ledger = base.ledger if base.ledger is not None else draws.ledger
    bench_draws = np.asarray(base.detail.stages[T.HEADLINE_STAGE], dtype=float).sum(axis=1)
    trule = TransitRule.from_ledger(ledger)
    prule = PeakRule.from_ledger(ledger)
    stops = state.stops
    feeds = _transit_feeds(state)
    if peaks is None:
        peaks, loaded_notes, missing_feeds = load_peak_tables(feeds, raw_dir)
        peak_notes = peak_notes or loaded_notes
    else:
        missing_feeds = []
    peak_notes = dict(peak_notes or {})
    has_cols = stops is not None and {"mode", "lat", "lon"} <= set(stops.columns)
    flags = T.service_flags(stops, feeds, peaks, prule) if has_cols else None
    walk = getattr(state, "transit_walk", None)
    mf = ctx.baseline_types.get(MF_COLUMN)
    min_share_id = "tod.status_min_measured_bus_share"
    min_share = float(ledger[min_share_id].central) if min_share_id in ledger else 0.0

    # ---- stops under each rule (same stop table)
    mode = stops["mode"].astype(str).str.lower() if has_cols else pd.Series(dtype=str)
    bus = (mode == "bus").to_numpy()
    rail_n = int((mode == "rail").sum())
    deps = pd.to_numeric(stops.get("weekday_departures"), errors="coerce") if has_cols else None
    if has_cols and "major" in stops.columns:  # the pack's own flag (stale schedules are not major)
        daily_major = (pd.to_numeric(stops["major"], errors="coerce") == 1).to_numpy()
    else:
        daily_major = (deps >= trule.min_bus_departures).to_numpy() if has_cols else bus
    daily_bus = int((bus & daily_major).sum()) if has_cols else 0
    daily_counted = int((bus & deps.notna().to_numpy()).sum()) if has_cols else 0
    stop_rows: dict[str, Any] = {
        "rail": rail_n,
        "bus": int(bus.sum()),
        "daily": {"major_bus": daily_bus, "bus_counted": daily_counted},
    }
    peak_counted = 0
    if flags is not None:
        peak_counted = int(flags["peak_counted"].sum())
        gap = flags["gap"].to_numpy()
        stop_rows["peak_average"] = {
            "major_bus": int(flags["peak_average"].sum()),
            "bus_counted": peak_counted,
        }
        stop_rows["peak_max_gap"] = {
            "not_ruled_out_bus": int((bus & (gap == T.GAP_NOT_RULED_OUT)).sum()),
            "ruled_out_bus": int((bus & (gap == T.GAP_RULED_OUT)).sum()),
            "no_schedule_bus": int((bus & (gap == T.GAP_NO_SCHEDULE)).sum()),
            "bus_counted": peak_counted,
        }
    n_bus = int(bus.sum())

    # ---- footprint (W1 status ladder for the benchmark; peak coverage for the peak rules)
    status = base.status or {}
    st = status.get("state") or {}
    shown = bool((status.get("statewide") or {}).get("shown", True))

    def coverage(counted: int, label_rule: str) -> dict[str, Any]:
        part = counted / n_bus if n_bus else 1.0
        complete = n_bus == 0 or counted >= n_bus
        return {
            "measured_bus_share": part,
            "complete": complete,
            "label": (
                "complete"
                if complete
                else f"partial footprint: {counted:,} of {n_bus:,} bus stops ({share(part)}) "
                f"have the schedule counts the {label_rule} rule needs"
            ),
            "statewide_shown": part >= min_share or n_bus == 0,
        }

    footprint = {
        "daily": {
            "label": str(st.get("completeness") or coverage(daily_counted, "daily")["label"]),
            "statewide_shown": shown,
            "complete": str(st.get("completeness", "complete")) == "complete",
            "measured_bus_share": daily_counted / n_bus if n_bus else 1.0,
        },
        "peak_average": coverage(peak_counted, "peak"),
        "peak_max_gap": coverage(peak_counted, "peak"),
        "ledger_ids": [min_share_id],
    }

    # ---- tract tables per (service, access); built only when used
    tables: dict[tuple[str, str], pd.DataFrame] = {}

    def tracts_for(service: str, access: str) -> pd.DataFrame:
        key = (service, access)
        if key not in tables:
            if access == "walk_straight":
                tables[key] = T.walk_straight_columns(_tod_tracts(ctx, ledger), walk)
            elif service == "daily_blocks":
                tables[key] = T.recoded_transit_columns(state.tracts, state.blocks, stops, trule)
            elif access == "network" or service == "daily":
                tables[key] = _tod_tracts(ctx, ledger)
            else:
                recoded = T.stops_for_service(stops, flags, service, trule.min_bus_departures)
                tables[key] = T.recoded_transit_columns(state.tracts, state.blocks, recoded, trule)
        return tables[key]

    def run(service: str, access: str, distance: str) -> dict[str, np.ndarray]:
        tracts = tracts_for(service, access)
        walk_kind = "network" if access in ("network", "walk_straight") else "straight"
        scope = T.TodScope(name="tod", distance=_distance_value(distance), walk=walk_kind)
        order = sorted(set(tracts["county"].astype(str)))
        rows = [ctx.county_ids.index(c) for c in order]
        _, existing_for, zoned = tod_by_right(state, tracts, {})
        res = T.tod_funnel(
            tracts,
            ledger,
            scope,
            mf_baseline=mf,
            mf_scale=draws.baseline_scale[rows],
            draws=draws.n,
            seed=draws.seed,
            zoned_m2=zoned,
            existing=existing_for(scope.suffix),
        )
        out = {
            s: np.asarray(res.stages[s], dtype=float).sum(axis=1)
            for s in ("eligible", "feasible", T.HEADLINE_STAGE)
        }
        out["_ids"] = np.array(res.records[T.HEADLINE_STAGE].assumption_ids, dtype=object)
        out["_sources"] = np.array(res.records[T.HEADLINE_STAGE].source_ids, dtype=object)
        del res
        return out

    bs, ba, bd = cfg.benchmark
    head_ids = list(base.headline.assumption_ids)

    def unavailable(service: str, access: str, distance: str) -> tuple[str, str] | None:
        if access == "network" and distance == "d07":
            return (
                "not_defined",
                "The walkshed table holds single distances (1/4, 1/2, 3/4 mile) around all "
                "major stops; the benchmark's mixed distance (rail 1/2, bus 1/4 mile) has no "
                "network version.",
            )
        if access == "network" and walk is None:
            return ("not_measured", "This state's pack has no walkshed table (transit_walk_tract).")
        if access == "network" and service == "daily":
            return (
                "not_measured",
                "The pack's walksheds are drawn around peak-rule stops only; a network walk "
                "around the daily-rule stops needs the walkshed build run for that stop set.",
            )
        if access == "network" and service == "peak_max_gap":
            return (
                "not_measurable",
                "The longest-gap rule cannot be confirmed from hourly counts, and the "
                "walkshed table cannot be split by stop.",
            )
        if service != "daily" and n_bus and peak_counted == 0:
            return (
                "schedules_missing",
                "No bus stop in this state has peak-hour schedule counts (no current, "
                "readable schedule feed was paired); the rule cannot be applied.",
            )
        return None

    def classify(out: Mapping[str, np.ndarray]) -> str:
        elig = float(np.median(out["eligible"]))
        feas = float(np.median(out["feasible"]))
        p50 = float(np.median(out[T.HEADLINE_STAGE]))
        if elig <= 0:
            return "no_qualifying_service"
        if feas <= 0 or p50 <= 0:
            return "infeasible"
        if p50 < cfg.small_homes:
            return "small_estimate"
        return "estimate"

    def describe(service: str, access: str, distance: str) -> str:
        from visionhousing.scenarios.tod import distance_words

        a = "straight line" if access == "straight" else "network walk"
        if access == "walk_straight":
            a = "straight line on the walkshed grid"
        return (
            f"{service.replace('_', ' ')} service, {a}, {distance_words(_distance_value(distance))}"
        )

    cells: list[dict[str, Any]] = []
    draws_of: dict[str, np.ndarray] = {}

    def cell(service: str, access: str, distance: str) -> dict[str, Any]:
        key = _cell_key(service, access, distance)
        changed = [
            f
            for f, v, b in zip(FACTORS, (service, access, distance), cfg.benchmark, strict=True)
            if v != b
        ]
        row: dict[str, Any] = {
            "key": key,
            "service": service,
            "access": access,
            "distance": distance,
            "changed": changed,
            "benchmark": not changed,
            "words": describe(service, access, distance),
        }
        gone = unavailable(service, access, distance)
        if gone is not None:
            row.update(outcome=gone[0], why=gone[1], p10=None, p50=None, p90=None)
            return row
        out = run(service, access, distance)
        draws_of[key] = out[T.HEADLINE_STAGE]
        ids = sorted({*map(str, out["_ids"]), *SERVICE_IDS.get(service, ()), cfg.id})
        sources = sorted(
            {*map(str, out["_sources"]), *(WALK_SOURCES if access != "straight" else ())}
        )
        if service != "daily":
            sources = sorted({*sources, *PEAK_SOURCES})
        r = state_record(
            f"TOD added units authorized {SPAN}, {row['words']}, state",
            out[T.HEADLINE_STAGE],
            "the TOD funnel (scenarios.tod.tod_funnel) with the benchmark's ledger, seed, "
            f"draws and absorption cap, the major stops chosen by the {service} rule from the "
            f"pack's stop table, land measured by {access.replace('_', ' ')}, distance {distance}",
            ids,
            sources=sources,
        )
        e = state_record(
            f"TOD eligible land, {row['words']}, state",
            out["eligible"],
            "block or walkshed land within the distance of a major stop under this cell's rule",
            ids,
            sources=sources,
            unit="acres",
        )
        diff = out[T.HEADLINE_STAGE] - bench_draws
        d = state_record(
            f"TOD paired difference from the benchmark, {row['words']}, state",
            diff,
            "per draw: this cell's added units authorized - the benchmark's, same seed and "
            "draws; percentiles of the differences (never a difference of percentiles)",
            sorted({*ids, *head_ids}),
            sources=sources,
        )
        outcome = classify(out)
        fp = footprint.get(service, footprint["daily"])
        row.update(
            outcome=outcome,
            reading="upper" if service == "peak_max_gap" else "measured",
            p10=r.p10,
            p50=r.p50,
            p90=r.p90,
            eligible_acres_p50=e.p50,
            feasible_acres_p50=float(np.median(out["feasible"])),
            footprint=fp["label"],
            statewide_shown=bool(fp["statewide_shown"]),
            difference={
                "p10": d.p10,
                "p50": d.p50,
                "p90": d.p90,
                "below_benchmark": _draw_share(diff < -1e-9),
                "above_benchmark": _draw_share(diff > 1e-9),
                "record": record_dict(d),
            },
            record=record_dict(r),
            eligible_record=record_dict(e),
        )
        if service == "peak_max_gap":
            row["why"] = (
                "Upper reading: stops kept are those the hourly counts do not rule out; exact "
                "longest gaps need departure times, which the cached schedules do not hold."
            )
        if outcome == "small_estimate":
            row["why"] = f"P50 above 0 but below {cfg.small_homes:g} homes: shown rounded."
        return row

    for s in cfg.services:
        for a in cfg.accesses:
            for dist in cfg.distances:
                cells.append(cell(s, a, dist))

    by_key = {c["key"]: c for c in cells}
    bkey = _cell_key(bs, ba, bd)
    reproduced = bkey in draws_of and bool(np.array_equal(draws_of[bkey], bench_draws))

    # ---- access contrast at the anchor: same stops, same walkshed grid
    anchor_s, anchor_d = cfg.anchor
    anchor: dict[str, Any] = {"service": anchor_s, "distance": anchor_d}
    net_key = _cell_key(anchor_s, "network", anchor_d)
    if net_key in draws_of and walk is not None:
        grid = run(anchor_s, "walk_straight", anchor_d)[T.HEADLINE_STAGE]
        blocks_key = _cell_key(anchor_s, "straight", anchor_d)
        diff = draws_of[net_key] - grid
        ids = sorted({*head_ids, *SERVICE_IDS.get(anchor_s, ()), cfg.id})
        rec_d = state_record(
            f"TOD paired difference, network walk - straight line, {anchor_s.replace('_', ' ')} "
            f"service, {anchor_d} mile, state",
            diff,
            "per draw: network-walk cell - straight-line cell on the same walkshed grid "
            "(transit_walk_tract straight_acres vs network_acres), same stops, seed and draws",
            ids,
            sources=[*WALK_SOURCES],
        )
        rec_g = state_record(
            f"TOD added units authorized {SPAN}, {anchor_s.replace('_', ' ')} service, straight "
            f"line on the walkshed grid, {anchor_d} mile, state",
            grid,
            "the TOD funnel on the walkshed table's straight-line acres (same conversion as "
            "the network acres)",
            ids,
            sources=[*WALK_SOURCES],
        )
        anchor.update(
            measured=True,
            straight_grid={
                "p10": rec_g.p10,
                "p50": rec_g.p50,
                "p90": rec_g.p90,
                "record": record_dict(rec_g),
            },
            difference={
                "p10": rec_d.p10,
                "p50": rec_d.p50,
                "p90": rec_d.p90,
                "below": _draw_share(diff < -1e-9),
                "record": record_dict(rec_d),
            },
        )
        if blocks_key in draws_of:
            bridge = grid - draws_of[blocks_key]
            rb = state_record(
                "TOD bridge: straight line on the walkshed grid - straight line from blocks, "
                f"{anchor_s.replace('_', ' ')} service, {anchor_d} mile, state",
                bridge,
                "per draw: the two straight-line land measures around the same stops (outline "
                "acres x land share vs 2020 block land); method difference, not a policy effect",
                ids,
                sources=[*WALK_SOURCES, *BLOCK_SOURCES],
            )
            anchor["bridge"] = {
                "p10": rb.p10,
                "p50": rb.p50,
                "p90": rb.p90,
                "record": record_dict(rb),
            }
    else:
        anchor.update(
            measured=False,
            why="No network cell at the anchor (no walkshed table or no peak counts).",
        )

    # ---- one factor at a time from the benchmark, and which factor moves TOD most
    oat: list[dict[str, Any]] = []
    for c in cells:
        if len(c["changed"]) != 1:
            continue
        oat.append(
            {
                "factor": c["changed"][0],
                "key": c["key"],
                "level": c[c["changed"][0]],
                "outcome": c["outcome"],
                "reading": c.get("reading"),
                "difference_p50": (c.get("difference") or {}).get("p50"),
            }
        )
    effects: dict[str, dict[str, Any]] = {}
    for f in FACTORS:
        best = None
        for o in oat:
            if o["factor"] != f or o["difference_p50"] is None or o["reading"] == "upper":
                continue
            if best is None or abs(o["difference_p50"]) > abs(best["difference_p50"]):
                best = o
        if best is not None:
            effects[f] = {
                "level": best["level"],
                "difference_p50": best["difference_p50"],
                "where": "from the benchmark",
            }
    if "access" not in effects and anchor.get("measured"):
        effects["access"] = {
            "level": "network",
            "difference_p50": anchor["difference"]["p50"],
            "where": f"at the anchor ({anchor_s.replace('_', ' ')} service, {anchor_d} mile), "
            "not defined at the benchmark",
        }
    moves = None
    if effects:
        top = max(effects, key=lambda f: abs(float(effects[f]["difference_p50"] or 0.0)))
        e = effects[top]
        moves = {
            "factor": top,
            "level": e["level"],
            "difference_p50": e["difference_p50"],
            "effects": effects,
            "sentence": (
                f"The {top} choice moves the TOD estimate most: changing it to {e['level']} "
                f"({e['where']}) changes added units authorized by a median of "
                f"{e['difference_p50']:+,.0f} homes per draw (P50 of the paired differences)."
                if abs(float(e["difference_p50"] or 0.0)) >= 0.5
                else "No measured one-factor change moves the TOD estimate: every P50 of the "
                "paired differences rounds to 0 homes."
            ),
            "rule": "largest |P50 of the paired difference| among measured one-factor changes; "
            "upper readings (peak_max_gap) are not ranked",
        }

    # ---- checks
    checks: dict[str, Any] = {"benchmark_reproduced": reproduced}
    if (
        has_cols
        and "transit_land_m2_d07" in state.tracts
        and any(
            c["service"] != "daily" and c["access"] == "straight" and c["key"] in draws_of
            for c in cells
        )
    ):
        # Peak-rule straight-line cells redraw the tract columns from the pack's blocks;
        # the benchmark reads the pack's columns. The same redraw for the daily rule gives
        # the method difference between the two (rounded block coordinates), per draw.
        redrawn = run("daily_blocks", "straight", bd)[T.HEADLINE_STAGE] - bench_draws
        rb = state_record(
            "TOD bridge: daily rule redrawn from the pack's blocks - the benchmark, state",
            redrawn,
            "per draw: the benchmark funnel on tract columns redrawn from the pack's 2020 "
            "blocks and stops (the method of the peak-rule cells) - the benchmark on the "
            "pack's own columns; a method difference, not a policy effect",
            sorted({*head_ids, cfg.id}),
            sources=[*BLOCK_SOURCES],
        )
        checks["block_redraw_bridge"] = {
            "p10": rb.p10,
            "p50": rb.p50,
            "p90": rb.p90,
            "record": record_dict(rb),
        }
    wc = getattr(state, "transit_walk_county", None)
    if flags is not None and wc is not None and "frequent_bus_stops" in wc:
        checks["peak_average_bus_vs_walk_table"] = {
            "here": int(flags["peak_average"].sum()),
            "walk_table": int(
                pd.to_numeric(wc["frequent_bus_stops"], errors="coerce").fillna(0).sum()
            ),
        }
    hw = float(ledger["tod.frequent_bus_max_headway_min"].central)
    return {
        "id": cfg.id,
        "benchmark": {
            "key": bkey,
            "service": bs,
            "access": ba,
            "distance": bd,
            "p10": by_key[bkey].get("p10"),
            "p50": by_key[bkey].get("p50"),
            "p90": by_key[bkey].get("p90"),
            "record": record_dict(base.headline),
        },
        "services": {s: _service_words(s, ledger) for s in cfg.services},
        "accesses": dict(cfg.accesses),
        "distances": list(cfg.distances),
        "cells": cells,
        "one_at_a_time": oat,
        "access_anchor": anchor,
        "moves_most": moves,
        "stops": stop_rows,
        "footprint": footprint,
        "schedules": {
            "feeds": peak_notes,
            "missing_peak_tables": list(missing_feeds),
            "max_gap": {
                "headway_min": hw,
                "edge_rule": cfg.edge_rule,
                "dedupe_minutes": cfg.dedupe_minutes,
                "measure": "lower bound from hourly counts (scenarios.tod.hourly_gap_lower_bound)",
            },
        },
        "checks": checks,
        "outcomes": list(OUTCOMES),
        "seed": draws.seed,
        "draws": bench.n,
    }
