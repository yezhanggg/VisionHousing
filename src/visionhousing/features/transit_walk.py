"""Transit walksheds per tract: frequent-bus rule, network walksheds and pack tables.

Plain-language summary
----------------------
This module makes the pack tables ``transit_walk_tract`` and
``transit_walk_county`` for the transit-oriented density benchmark.

1. **Which stops are major.** Every rail (fixed-guideway) stop is major, as in the
   major-stop rule. A bus stop is major when buses leave **the same platform
   side** on average at least every 15 minutes in **both** weekday peak windows
   (7-9 am and 4-6 pm): at least 8 departures in each two-hour window, all
   routes added (rule ``tod.walk.direction_rule``). A stop with 8 departures one
   way in the morning and 8 the other way in the afternoon (a commuter-only
   pattern) does not qualify: the benchmark means all-day frequent service. A
   station qualifies when any one of its platform sides does. The departures
   are counted per hour from the agencies' schedule feeds
   (:mod:`visionhousing.data.sources.transit_peaks`) on a documented weekday and
   matched to the pack's stops the same way the daily counts are (same stop id
   within 200 m, else the nearest stop of the feed within 50 m). A bus stop
   with no current schedule is not major and is counted as "not covered".
2. **Walksheds.** County by county, the walking network near the major stops is
   read from OpenStreetMap (:mod:`visionhousing.data.sources.osm`) and the
   dissolved network and straight-line walksheds are drawn at 1/4, 1/2 and
   3/4 mile (:mod:`visionhousing.features.walkshed`). Stops in the neighbouring
   counties of the same state are included, so walksheds cross county lines.
3. **Tract areas.** Each walkshed is cut by every tract outline (2020
   cartographic boundary file): on the walkshed grid, a cell belongs to the
   tract holding its centre, and a tract's acres inside a walkshed are its
   cells there times the cell area. The tract's own area comes from its
   outline exactly (shoelace formula). The table keeps, for each tract touched by the
   3/4-mile straight-line circle: the tract's area, the major stops inside it,
   and per distance the straight-line acres, the network acres and the part of
   the network acres that is a straight-line fallback.

Shares and ratios are left to the reader of the table, so it stays small:
share = ``network_acres_* / tract_acres``; network-to-straight ratio =
``network_acres_* / straight_acres_*``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.features import planar
from visionhousing.features import transit as tf
from visionhousing.features import walkshed as ws

__all__ = [
    "ASSUMPTIONS",
    "COUNTY_COLUMNS",
    "TABLES",
    "TRACT_COLUMNS",
    "PeakRule",
    "build_transit_walk",
    "county_coverage",
    "describe",
    "frequent_bus_stops",
    "load_rules",
    "major_stops",
    "peak_counts",
    "tract_areas",
    "tract_labels",
]

ROOT = Path(__file__).resolve().parents[3]
ASSUMPTIONS = ROOT / "assumptions" / "transit_walk.yaml"
SQ_M_PER_ACRE = 4046.8564224
MATCH_ID_MILES = 200 / 1609.344
MATCH_NEAR_MILES = 50 / 1609.344
_SOURCES = ["ntm_stops", "gtfs_agency_feeds", "osm_overpass_walk", "cb_2020_tract"]


def _band(miles: float) -> str:
    return tf.radius_label(miles)


BANDS = (0.25, 0.5, 0.75)
TRACT_COLUMNS: tuple[str, ...] = (
    "geoid",
    "tract_acres",
    "major_rail",
    "major_bus",
    *(f"{w}_acres_{_band(m)}" for m in BANDS for w in ("straight", "network", "fallback")),
)
"""Columns of ``transit_walk_tract``, in order."""

COUNTY_COLUMNS: tuple[str, ...] = (
    "county",
    "bus_stops",
    "bus_stops_counted",
    "frequent_bus_stops",
    "d07_bus_stops",
    "rail_stops",
    "fallback_stops",
    "osm_tiles",
    "osm_tiles_failed",
    "schedule_dates",
)
"""Columns of ``transit_walk_county``, in order."""

TABLES: dict[str, dict[str, object]] = {
    "transit_walk_tract": {
        "key": ["geoid"],
        "sources": _SOURCES,
        "tier": "1",
        "description": (
            "Land near major transit stops per tract, by walking distance (1/4, 1/2, 3/4 "
            "mile): straight-line acres, network-walkshed acres (OpenStreetMap streets and "
            "paths, overlaps dissolved) and the part of the network acres that is a "
            "straight-line fallback. Major stops: rail, plus bus stops averaging a bus every "
            "15 minutes or better in both weekday peaks. Only tracts within 3/4 mile of a "
            "major stop are listed."
        ),
    },
    "transit_walk_county": {
        "key": ["county"],
        "sources": _SOURCES,
        "tier": "1",
        "description": (
            "Coverage of the walkshed table per county: bus stops with a current schedule, "
            "frequent bus stops (peak rule) next to the daily major-stop rule, rail stops, stops "
            "that fell back to straight lines, map tiles used and failed, and the schedule "
            "dates the peaks were counted on."
        ),
    },
}
"""The two pack tables: key, sources, tier and description."""

_COLUMNS: dict[str, tuple[str, str]] = {
    "geoid": ("code", "Tract code: 5-digit county code + 6-digit tract code"),
    "tract_acres": (
        "acres",
        "Area of the tract outline (2020 cartographic boundary, land and inland water)",
    ),
    "major_rail": ("stops", "Rail (fixed-guideway) stops inside the tract"),
    "major_bus": ("stops", "Bus stops inside the tract meeting the peak frequent-bus rule"),
    "county": ("code", "County code (2-digit state + 3-digit county)"),
    "bus_stops": ("stops", "Bus stops in the county (National Transit Map)"),
    "bus_stops_counted": (
        "stops",
        "Bus stops whose peak departures were counted from a current schedule",
    ),
    "frequent_bus_stops": (
        "stops",
        "Bus stops meeting the peak rule (headway at or under the ledger threshold in both peaks)",
    ),
    "d07_bus_stops": (
        "stops",
        "Bus stops meeting the daily major-stop rule (64 weekday departures), for comparison",
    ),
    "rail_stops": ("stops", "Rail (fixed-guideway) stops in the county"),
    "fallback_stops": (
        "stops",
        "Major stops in the county that used a straight-line circle (network incomplete)",
    ),
    "osm_tiles": ("tiles", "OpenStreetMap tiles read for the county's walksheds"),
    "osm_tiles_failed": ("tiles", "Tiles that could not be fetched (their stops fell back)"),
    "schedule_dates": (
        "text",
        "Schedule feed id and the weekday its peaks were counted on, with the day rule",
    ),
}
for _m in BANDS:
    _d = f"{_m:g} mile"
    _COLUMNS[f"straight_acres_{_band(_m)}"] = (
        "acres",
        f"Tract area within {_d} of a major stop in a straight line (circles joined)",
    )
    _COLUMNS[f"network_acres_{_band(_m)}"] = (
        "acres",
        f"Tract area within {_d} walk of a major stop (network walkshed, includes fallback)",
    )
    _COLUMNS[f"fallback_acres_{_band(_m)}"] = (
        "acres",
        f"Part of network_acres_{_band(_m)} from straight-line circles of stops with an "
        "incomplete network",
    )


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and description of a column (for the data dictionary)."""
    if name in _COLUMNS:
        return _COLUMNS[name]
    raise ValueError(f"no data-dictionary entry for {table}.{name}")


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/transit_walk.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- frequent buses


@dataclass(frozen=True)
class PeakRule:
    """The frequent-bus rule's numbers (from the ledger).

    Args:
        max_headway_min: Longest average headway allowed in each window.
        am: Morning window ``(start hour, end hour)``, end exclusive.
        pm: Afternoon window.
    """

    max_headway_min: float
    am: tuple[int, int]
    pm: tuple[int, int]

    LEDGER_IDS = (
        "tod.frequent_bus_max_headway_min",
        "tod.frequent_bus_am_start_hour",
        "tod.frequent_bus_am_end_hour",
        "tod.frequent_bus_pm_start_hour",
        "tod.frequent_bus_pm_end_hour",
    )
    """Ledger ids, in order."""

    @classmethod
    def from_ledger(cls, ledger: Any) -> PeakRule:
        """Build the rule from the ledger's central values."""
        h, a0, a1, p0, p1 = (ledger[i].central for i in cls.LEDGER_IDS)
        return cls(float(h), (int(a0), int(a1)), (int(p0), int(p1)))

    def min_departures(self, window: tuple[int, int]) -> float:
        """Departures a window needs: its minutes / the longest headway."""
        return (window[1] - window[0]) * 60 / self.max_headway_min


def peak_counts(peaks: pd.DataFrame, rule: PeakRule) -> pd.DataFrame:
    """Per stop: departures in each peak window on its best platform side.

    Each row of ``peaks`` is one platform side (``direction``: ``all``, ``main``,
    ``opposite``, or ``<platform>/<side>`` for a station). Both windows are read
    from the **same** row: the row kept is the one nearest to passing in its
    weaker window (largest ``min(am / need_am, pm / need_pm)``; ties to the most
    departures), so a stop passes exactly when one platform side passes both.

    Returns a frame indexed by ``stop_id`` with ``lat``, ``lon``, ``am`` and ``pm``.
    """
    out = peaks[["stop_id", "direction", "lat", "lon"]].copy()
    for name, (h0, h1) in (("am", rule.am), ("pm", rule.pm)):
        cols = [f"h{h:02d}" for h in range(h0, h1)]
        out[name] = peaks[cols].sum(axis=1).to_numpy()
    out["_score"] = np.minimum(
        out["am"] / rule.min_departures(rule.am), out["pm"] / rule.min_departures(rule.pm)
    )
    out["_total"] = out["am"] + out["pm"]
    best = out.sort_values(["stop_id", "_score", "_total"], ascending=[True, False, False])
    best = best.drop_duplicates("stop_id").set_index("stop_id")
    return best[["lat", "lon", "am", "pm"]]


def _match(group: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """``am`` and ``pm`` for one map feed's bus stops.

    Same stop id within 200 m, else the nearest schedule stop within 50 m.
    """
    local = group["stop_id"].str.split(":", n=1).str[1]
    m = pd.DataFrame(
        {"sid": local.to_numpy(), "lat": group["lat"].to_numpy(), "lon": group["lon"].to_numpy()}
    )
    m = m.merge(table, left_on="sid", right_index=True, how="left", suffixes=("", "_g"))
    ok = m["lat_g"].notna() & (
        tf.great_circle_miles(m["lat"], m["lon"], m["lat_g"].fillna(0), m["lon_g"].fillna(0))
        <= MATCH_ID_MILES
    )
    am = m["am"].where(ok).to_numpy(dtype=float, copy=True)
    pm = m["pm"].where(ok).to_numpy(dtype=float, copy=True)
    todo = np.flatnonzero(~ok.to_numpy())
    if len(todo) and len(table):
        idx, miles = tf.nearest_stop(
            m["lat"].to_numpy()[todo],
            m["lon"].to_numpy()[todo],
            table["lat"].to_numpy(),
            table["lon"].to_numpy(),
        )
        near = miles <= MATCH_NEAR_MILES
        am[todo[near]] = table["am"].to_numpy()[idx[near]]
        pm[todo[near]] = table["pm"].to_numpy()[idx[near]]
    return pd.DataFrame({"am": am, "pm": pm}, index=group.index)


def frequent_bus_stops(
    stops: pd.DataFrame,
    feeds: pd.DataFrame,
    peaks: Mapping[str, pd.DataFrame],
    rule: PeakRule,
) -> pd.DataFrame:
    """Add ``am``, ``pm`` (peak departures, best platform side) and ``frequent`` to the stops.

    Args:
        stops: The pack's ``transit_stops``.
        feeds: The pack's ``transit_feeds`` (which schedule feed each map feed was
            paired with, and whether that schedule had expired).
        peaks: Schedule feed id to its peaks table (``read_gtfs_peaks``).
        rule: The frequent-bus rule.

    Returns:
        ``stops`` with ``am`` and ``pm`` (blank where not counted) and
        ``frequent`` (1 for a bus stop meeting the rule in both windows, on one
        platform side).
    """
    out = stops.copy()
    out["am"] = np.nan
    out["pm"] = np.nan
    paired = feeds[
        (feeds["feed_id"].astype(str) != "")
        & feeds["feed_id"].notna()
        & feeds["schedule_feed"].notna()
        & ~feeds["frequency"].astype(str).str.startswith("unknown")
        & (pd.to_numeric(feeds["bus_stops"], errors="coerce") > 0)
    ]
    pair = {
        str(f): str(s).split(".")[0]
        for f, s in zip(paired["feed_id"], paired["schedule_feed"], strict=True)
    }
    bus = out["mode"].astype(str) == "bus"
    for feed_id, group in out[bus].groupby("feed_id", sort=True):
        mdb = pair.get(str(feed_id))
        if mdb is None or peaks.get(mdb) is None:
            continue
        matched = _match(group, peak_counts(peaks[mdb], rule))
        out.loc[group.index, "am"] = matched["am"]
        out.loc[group.index, "pm"] = matched["pm"]
    need_am, need_pm = rule.min_departures(rule.am), rule.min_departures(rule.pm)
    out["frequent"] = (bus & (out["am"] >= need_am) & (out["pm"] >= need_pm)).astype("int64")
    return out


def major_stops(stops: pd.DataFrame) -> pd.DataFrame:
    """Rail stops and frequent bus stops, with ``kind`` (``rail`` or ``bus``)."""
    rail = stops["mode"].astype(str) == "rail"
    bus = stops["frequent"] == 1
    out = stops[rail | bus].copy()
    out["kind"] = np.where(rail[rail | bus], "rail", "bus")
    return out.reset_index(drop=True)


def county_coverage(
    stops: pd.DataFrame, notes: Mapping[str, Mapping[str, Any]], feeds: pd.DataFrame
) -> pd.DataFrame:
    """Per county: bus stops, those counted, frequent (peak rule) and daily-rule bus, rail stops."""
    bus = stops["mode"].astype(str) == "bus"
    g = stops.assign(
        _bus=bus,
        _counted=bus & stops["am"].notna(),
        _freq=stops["frequent"] == 1,
        _d07=bus & (pd.to_numeric(stops["major"], errors="coerce") == 1),
        _rail=stops["mode"].astype(str) == "rail",
    ).groupby("county")
    out = pd.DataFrame(
        {
            "bus_stops": g["_bus"].sum(),
            "bus_stops_counted": g["_counted"].sum(),
            "frequent_bus_stops": g["_freq"].sum(),
            "d07_bus_stops": g["_d07"].sum(),
            "rail_stops": g["_rail"].sum(),
        }
    ).astype("int64")
    pair = dict(
        zip(
            feeds["feed_id"].astype(str),
            feeds["schedule_feed"].astype(str).str.split(".").str[0],
            strict=False,
        )
    )
    dates = {}
    for county, grp in stops[bus & stops["am"].notna()].groupby("county"):
        ids = sorted({pair.get(str(f), "") for f in grp["feed_id"]} - {""})
        dates[county] = "; ".join(
            f"{i}: {notes.get(i, {}).get('date')} ({notes.get(i, {}).get('day_rule')})" for i in ids
        )
    out["schedule_dates"] = pd.Series(dates).reindex(out.index).fillna("")
    return out.reset_index(names="county")


# --------------------------------------------------------------------------- geometry per county


def _to_polygons(geometry: Mapping[str, Any], lat0: float, lon0: float) -> list[Any]:
    """A tract's GeoJSON outline as polygons (ring lists) in the local projection."""
    return planar.geojson_polygons(
        geometry, lambda lon, lat: ws.project(np.asarray(lat), np.asarray(lon), lat0, lon0)
    )


def tract_labels(grid: planar.GridSpec, tracts: Sequence[Sequence[Any]]) -> np.ndarray:
    """Label each cell with ``1 + index`` of the tract holding its centre (0 = none)."""
    labels = np.zeros((grid.rows, grid.cols), dtype=np.int32)
    for k, polys in enumerate(tracts):
        rings = [r for p in polys for r in p]
        if not rings:
            continue
        rows, cols = grid.window(*planar.rings_bounds(rings))
        if rows.stop <= rows.start or cols.stop <= cols.start:
            continue
        inside = planar.fill_rings(grid.sub(rows, cols), rings)
        labels[rows, cols][inside] = k + 1
    return labels


def tract_areas(
    grid: planar.GridSpec, tracts: Sequence[Sequence[Any]], masks: Mapping[Any, np.ndarray]
) -> np.ndarray:
    """Area (m2) of each tract inside each mask: an array of shape ``(tracts, masks)``.

    Args:
        grid: The grid the masks are drawn on.
        tracts: Each tract's polygons (ring lists) in the grid's projection.
        masks: Bool arrays on ``grid`` (True inside a walkshed).
    """
    labels = tract_labels(grid, tracts)
    out = np.zeros((len(tracts), len(masks)))
    for j, mask in enumerate(masks.values()):
        if mask is None or not mask.any():
            continue
        counts = np.bincount(labels[mask], minlength=len(tracts) + 1)
        out[:, j] = counts[1:] * grid.cell_area
    return out


def build_transit_walk(
    stops: pd.DataFrame,
    tract_shapes: Sequence[tuple[str, Mapping[str, Any]]],
    peak_rule: PeakRule,
    walk_rule: ws.WalkRule,
    rules: Mapping[str, Any],
    fetch_tiles: Callable[[list[tuple[int, int]]], tuple[list[Path], list[tuple[int, int]]]],
    read_tiles: Callable[[list[Path]], Any],
    *,
    counties: Sequence[str] | None = None,
    stop_report: bool = False,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Make ``transit_walk_tract`` rows and the per-county network counts, county by county.

    Args:
        stops: The pack's stops after :func:`frequent_bus_stops` (``frequent``, ``mode``,
            ``lat``, ``lon``, ``tract``, ``county``).
        tract_shapes: ``(geoid, GeoJSON geometry)`` of the state's tracts.
        peak_rule: The frequent-bus rule (only for the log).
        walk_rule: Snap distance, edge buffer, smallest network piece.
        rules: ``assumptions/transit_walk.yaml``.
        fetch_tiles: Fetches (or reuses) map tiles; returns files and failed tiles.
        read_tiles: Reads tile files into :class:`visionhousing.data.sources.osm.OsmWays`.
        counties: Only these counties (tests, partial runs); default all with tracts.
        stop_report: Also measure each rail stop's own 1/2-mile walkshed (checks by hand).
        log: Progress messages.

    Returns:
        ``(tract rows, county network counts, stop report or None)``.
    """
    from visionhousing.data.sources import osm

    miles = [float(m) for m in rules["network"]["distances_miles"]]
    dists = [m * ws.METRES_PER_MILE for m in miles]
    dmax = max(dists)
    deg = float(rules["osm"]["tile_deg"])
    cell_m = float(rules["network"].get("cell_m", ws.CELL_M))
    major = major_stops(stops)
    by_county: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for geoid, geom in tract_shapes:
        by_county.setdefault(geoid[:5], []).append((geoid, geom))
    rows, crow, report = [], [], []
    lat_all, lon_all = major["lat"].to_numpy(float), major["lon"].to_numpy(float)
    for county in sorted(by_county):
        if counties is not None and county not in counties:
            continue
        t0 = time.time()
        items = by_county[county]
        coords = np.vstack([np.asarray(r, dtype=float) for _, g in items for r in tf._rings(g)])
        w, s_, e, n = coords[:, 0].min(), coords[:, 1].min(), coords[:, 0].max(), coords[:, 1].max()
        lat0, lon0 = (s_ + n) / 2, (w + e) / 2
        pad_lat = (dmax + 200) / 111_000
        pad_lon = (dmax + 200) / (111_000 * math.cos(math.radians(lat0)))
        near = (
            (lat_all >= s_ - pad_lat)
            & (lat_all <= n + pad_lat)
            & (lon_all >= w - pad_lon)
            & (lon_all <= e + pad_lon)
        )
        cand = major[near].reset_index(drop=True)
        own = int((major["county"].astype(str) == county).sum())
        if cand.empty:
            crow.append(
                {"county": county, "fallback_stops": 0, "osm_tiles": 0, "osm_tiles_failed": 0}
            )
            continue
        tiles = osm.tiles_for_points(cand["lat"], cand["lon"], dmax + 100, deg)
        files, failed = fetch_tiles(tiles)
        bad = set(failed)
        stop_tiles = [
            osm.tiles_for_points([la], [lo], dmax + 100, deg)
            for la, lo in zip(cand["lat"], cand["lon"], strict=True)
        ]
        forced = np.array([any(t in bad for t in ts) for ts in stop_tiles], dtype=bool)
        ways = read_tiles(files)
        nx, ny = ws.project(ways.lat, ways.lon, lat0, lon0)
        net = ws.build_network(nx, ny, ways.ways)
        del ways
        sx, sy = ws.project(cand["lat"].to_numpy(float), cand["lon"].to_numpy(float), lat0, lon0)
        geoms = [_to_polygons(g, lat0, lon0) for _, g in items]
        box = planar.rings_bounds([r for polys in geoms for p in polys for r in p])
        sheds = ws.walkshed_shapes(
            net, sx, sy, dists, walk_rule, force_fallback=forced, cell_m=cell_m, bounds=box
        )
        shapes: dict[Any, Any] = {}
        for d in dists:
            shapes[("s", d)] = sheds.straight[d]
            shapes[("n", d)] = sheds.network[d]
            shapes[("f", d)] = sheds.fallback[d]
        areas = tract_areas(sheds.grid, geoms, shapes)
        keys = list(shapes)
        tract_ids = [g for g, _ in items]
        in_tract = cand[cand["county"].astype(str) == county]
        rail_n = in_tract[in_tract["kind"] == "rail"].groupby("tract").size()
        bus_n = in_tract[in_tract["kind"] == "bus"].groupby("tract").size()
        for i, geoid in enumerate(tract_ids):
            if areas[i, keys.index(("s", dmax))] <= 0:
                continue
            row: dict[str, Any] = {
                "geoid": geoid,
                "tract_acres": planar.polygon_area(geoms[i]) / SQ_M_PER_ACRE,
                "major_rail": int(rail_n.get(geoid, 0)),
                "major_bus": int(bus_n.get(geoid, 0)),
            }
            for m, d in zip(miles, dists, strict=True):
                s_a = areas[i, keys.index(("s", d))]
                f_a = areas[i, keys.index(("f", d))]
                n_a = min(areas[i, keys.index(("n", d))] + f_a, s_a)
                row[f"straight_acres_{_band(m)}"] = s_a / SQ_M_PER_ACRE
                row[f"network_acres_{_band(m)}"] = n_a / SQ_M_PER_ACRE
                row[f"fallback_acres_{_band(m)}"] = f_a / SQ_M_PER_ACRE
            rows.append(row)
        is_own = (cand["county"].astype(str) == county).to_numpy()
        crow.append(
            {
                "county": county,
                "fallback_stops": int((~sheds.joined & is_own).sum()),
                "osm_tiles": len(tiles),
                "osm_tiles_failed": len(failed),
            }
        )
        if stop_report:
            sel = np.flatnonzero(is_own & (cand["kind"] == "rail").to_numpy())
            if len(sel):
                half = dists[miles.index(float(rules["network"]["headline_miles"]))]
                res = ws.stop_walksheds(net, sx[sel], sy[sel], half, walk_rule, cell_m=cell_m)
                for k, (joined, n_m2, c_m2) in zip(sel.tolist(), res, strict=True):
                    report.append(
                        {
                            "stop_id": cand.at[k, "stop_id"],
                            "stop_name": cand.at[k, "stop_name"],
                            "county": county,
                            "lat": cand.at[k, "lat"],
                            "lon": cand.at[k, "lon"],
                            "joined": bool(joined),
                            "network_acres": n_m2 / SQ_M_PER_ACRE,
                            "circle_acres": c_m2 / SQ_M_PER_ACRE,
                            "ratio": n_m2 / c_m2,
                        }
                    )
        log(
            f"{county}: {own} major stops ({len(cand)} with neighbours), {len(tiles)} tiles "
            f"({len(failed)} failed), {net.n_nodes:,} nodes, {len(net.u):,} edges, "
            f"{int((~sheds.joined).sum())} fallback, {time.time() - t0:.0f} s"
        )
        del net, sheds, shapes
    tract = pd.DataFrame(rows, columns=list(TRACT_COLUMNS))
    for col in (*TRACT_COLUMNS[4:], "tract_acres"):
        tract[col] = tract[col].astype(float).round(1)
    tract = tract.sort_values("geoid").reset_index(drop=True)
    county_net = pd.DataFrame(
        crow, columns=["county", "fallback_stops", "osm_tiles", "osm_tiles_failed"]
    )
    rep = pd.DataFrame(report) if stop_report else None
    return tract, county_net, rep
