"""Transit features: distance from every census block to the nearest major stop, and tract totals.

Plain-language summary
----------------------
Decision D-07 defines a **major transit stop** as a fixed-guideway station
(subway, light rail or trolley, commuter or regional rail, and similar), or a
bus stop with at least 64 scheduled departures on a representative weekday
(about one every 15 minutes for 16 hours). A home is "near transit" when its
census block's internal point is within 1/2 mile of a rail station or 1/4 mile
of a frequent bus stop, in a straight line. The interface also offers 1/4, 1/2
and 3/4 mile from any major stop.

This module does three things, with numpy only:

1. **Nearest stop.** For every block it finds the nearest major stop of each
   type (rail, frequent bus) and its great-circle distance in miles. Each point
   becomes a unit vector on a sphere; the nearest stop on a sphere is the one
   with the largest dot product, so one matrix product per chunk of blocks
   finds it. The distance to that stop is then computed with the haversine
   formula, which is accurate at short range. This is exact (no grid or
   approximation) and takes a few seconds for Pennsylvania's 337,000 blocks.
2. **Tract totals.** For each tract: housing units and land within 1/4, 1/2 and
   3/4 mile of any major stop, and within the D-07 distance, as counts and as
   shares of the tract's block totals (2020 Census units and land).
3. **Transit-rich flag.** The parking reform's rule: at least half of the
   tract's homes within the D-07 distance (``parking.transit_rich_min_share``),
   or a major stop inside the tract.

Units in these shares are 2020 Census block counts, not the ACS estimates in
the tract table; the two differ a little.

Also here: :func:`points_in_polygons`, an even-odd (ray casting) test that says
which county or tract polygon holds each stop.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "DISTANCE_CHOICES_MILES",
    "EARTH_RADIUS_MILES",
    "FEATURE_COLUMNS",
    "TransitRule",
    "block_distances",
    "great_circle_miles",
    "major_stop_table",
    "nearest_stop",
    "points_in_polygons",
    "radius_label",
    "station_count",
    "tract_transit_features",
]

EARTH_RADIUS_MILES = 3958.7613
"""Mean Earth radius (IUGG), in miles."""

DISTANCE_CHOICES_MILES = (0.25, 0.5, 0.75)
"""The straight-line distances the interface offers (decision D-07)."""

FIXED_GUIDEWAY = "rail"
FREQUENT_BUS = "bus"


def radius_label(miles: float) -> str:
    """Column suffix for a distance: ``0.25`` -> ``"0_25mi"``."""
    return f"{miles:.2f}".replace(".", "_") + "mi"


FEATURE_COLUMNS: tuple[str, ...] = (
    *(
        f"transit_{what}_{radius_label(r)}"
        for r in DISTANCE_CHOICES_MILES
        for what in ("units", "units_share", "land_m2", "land_share")
    ),
    "transit_units_d07",
    "transit_units_share_d07",
    "transit_land_m2_d07",
    "transit_land_share_d07",
    "transit_major_stops",
    "transit_rich",
)
"""Tract columns made by :func:`tract_transit_features`, in order (after ``tract``)."""


class TransitRule:
    """The D-07 rule's numbers, read from the ledger by the caller.

    Args:
        min_bus_departures: Weekday departures a bus stop needs to be major.
        rail_radius: Miles around a fixed-guideway station that count as near.
        bus_radius: Miles around a frequent bus stop that count as near.
        rich_min_share: Share of a tract's homes near a stop that makes it
            transit-rich.
    """

    def __init__(
        self,
        min_bus_departures: float,
        rail_radius: float,
        bus_radius: float,
        rich_min_share: float,
    ):
        self.min_bus_departures = float(min_bus_departures)
        self.rail_radius = float(rail_radius)
        self.bus_radius = float(bus_radius)
        self.rich_min_share = float(rich_min_share)

    LEDGER_IDS = (
        "parking.transit_bus_min_departures",
        "parking.transit_rail_radius_miles",
        "parking.transit_bus_radius_miles",
        "parking.transit_rich_min_share",
    )
    """Ledger ids of the four numbers, in argument order."""

    @classmethod
    def from_ledger(cls, ledger: Any) -> TransitRule:
        """Build the rule from the ledger's central values."""
        return cls(*(ledger[i].central for i in cls.LEDGER_IDS))


# --------------------------------------------------------------------------- geometry


def great_circle_miles(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Great-circle (haversine) distance in miles between paired points, element by element."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _unit_vectors(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    phi, lmb = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(phi) * np.cos(lmb), np.cos(phi) * np.sin(lmb), np.sin(phi)])


def nearest_stop(
    lat: np.ndarray,
    lon: np.ndarray,
    stop_lat: np.ndarray,
    stop_lon: np.ndarray,
    *,
    cells: int = 8_000_000,
) -> tuple[np.ndarray, np.ndarray]:
    """Find the nearest stop to each point and its great-circle distance.

    Args:
        lat: Latitudes of the points, in degrees.
        lon: Longitudes of the points, in degrees.
        stop_lat: Latitudes of the stops, in degrees.
        stop_lon: Longitudes of the stops, in degrees.
        cells: Largest point-by-stop matrix built at once (8 million cells,
            about 64 MB), to bound memory.

    Returns:
        ``(index, miles)``: the position of the nearest stop and the distance to
        it. With no stops, the index is -1 and the distance infinite.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    n = len(lat)
    index = np.full(n, -1, dtype=np.int64)
    miles = np.full(n, np.inf)
    if n == 0 or len(stop_lat) == 0:
        return index, miles
    s_lat = np.asarray(stop_lat, dtype=float)
    s_lon = np.asarray(stop_lon, dtype=float)
    stops = _unit_vectors(s_lat, s_lon).T
    chunk = max(1, cells // len(s_lat))
    for start in range(0, n, chunk):
        sl = slice(start, start + chunk)
        dots = _unit_vectors(lat[sl], lon[sl]) @ stops
        index[sl] = np.argmax(dots, axis=1)
    miles = great_circle_miles(lat, lon, s_lat[index], s_lon[index])
    return index, miles


def _rings(geometry: Mapping[str, Any]) -> list[np.ndarray]:
    """Every ring (outer rings and holes) of a GeoJSON Polygon or MultiPolygon."""
    kind, coords = geometry.get("type"), geometry.get("coordinates", [])
    polygons = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    return [np.asarray(ring, dtype=float) for poly in polygons for ring in poly if len(ring) >= 4]


def _inside_ring(ring: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    x1, y1 = ring[:-1, 0][None, :], ring[:-1, 1][None, :]
    x2, y2 = ring[1:, 0][None, :], ring[1:, 1][None, :]
    px, py = lon[:, None], lat[:, None]
    crosses = (y1 > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
    return (crosses & (px < x_at)).sum(axis=1) % 2 == 1


def points_in_polygons(
    lat: np.ndarray,
    lon: np.ndarray,
    shapes: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    chunk: int = 5_000,
) -> np.ndarray:
    """Return the id of the polygon that holds each point, or ``""`` when none does.

    Uses the even-odd rule over all of a shape's rings, so holes are handled. A
    bounding-box test skips shapes far from a point. Where shapes overlap (they
    should not), the first in order wins.

    Args:
        lat: Latitudes of the points, in degrees.
        lon: Longitudes of the points, in degrees.
        shapes: ``(id, GeoJSON geometry)`` pairs.
        chunk: Points tested against one ring at a time, to bound memory.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    out = np.full(len(lat), "", dtype=object)
    for shape_id, geometry in shapes:
        rings = _rings(geometry)
        if not rings:
            continue
        allpts = np.vstack(rings)
        box = (
            (out == "")
            & (lon >= allpts[:, 0].min())
            & (lon <= allpts[:, 0].max())
            & (lat >= allpts[:, 1].min())
            & (lat <= allpts[:, 1].max())
        )
        idx = np.flatnonzero(box)
        for start in range(0, len(idx), chunk):
            part = idx[start : start + chunk]
            odd = np.zeros(len(part), dtype=bool)
            for ring in rings:
                odd ^= _inside_ring(ring, lon[part], lat[part])
            out[part[odd]] = shape_id
    return out


# --------------------------------------------------------------------------- features


def major_stop_table(stops: pd.DataFrame, rule: TransitRule) -> pd.DataFrame:
    """Keep the major stops of decision D-07, with a ``kind`` column (``rail`` or ``bus``).

    A stop is major when its mode is ``rail`` (any fixed guideway), or its mode
    is ``bus`` and its counted weekday departures reach the rule's minimum. Bus
    stops whose frequency is unknown (no schedule feed) are not major.
    """
    if stops.empty:
        return stops.assign(kind=pd.Series(dtype=str)).reset_index(drop=True)
    mode = stops["mode"].astype(str).str.lower()
    deps = pd.to_numeric(stops["weekday_departures"], errors="coerce")
    rail = mode == FIXED_GUIDEWAY
    bus = (mode == FREQUENT_BUS) & (deps >= rule.min_bus_departures)
    out = stops.loc[rail | bus].copy()
    out["kind"] = np.where(rail[rail | bus], FIXED_GUIDEWAY, FREQUENT_BUS)
    return out.reset_index(drop=True)


def block_distances(blocks: pd.DataFrame, major: pd.DataFrame) -> pd.DataFrame:
    """Distance in miles from each block's internal point to the nearest major stop, by type.

    Args:
        blocks: Blocks with ``lat`` and ``lon``.
        major: Output of :func:`major_stop_table`.

    Returns:
        A frame aligned with ``blocks`` with ``dist_rail_mi``, ``dist_bus_mi``
        and ``dist_any_mi`` (infinite where there is no stop of that type).
    """
    lat = blocks["lat"].to_numpy(dtype=float)
    lon = blocks["lon"].to_numpy(dtype=float)
    out = {}
    for kind in (FIXED_GUIDEWAY, FREQUENT_BUS):
        part = major[major["kind"] == kind]
        _, miles = nearest_stop(
            lat, lon, part["lat"].to_numpy(dtype=float), part["lon"].to_numpy(dtype=float)
        )
        out[f"dist_{kind}_mi"] = miles
    frame = pd.DataFrame(out, index=blocks.index)
    frame["dist_any_mi"] = np.minimum(frame["dist_rail_mi"], frame["dist_bus_mi"])
    return frame


def tract_transit_features(
    blocks: pd.DataFrame,
    stops: pd.DataFrame,
    rule: TransitRule,
    *,
    stop_tracts: Sequence[str] | None = None,
    tracts: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Per-tract housing units and land near major transit, and the transit-rich flag.

    Args:
        blocks: Blocks with ``tract``, ``housing_units``, ``land_area_m2``,
            ``lat`` and ``lon``.
        stops: Stop table with ``lat``, ``lon``, ``mode``, ``weekday_departures``.
        rule: The D-07 numbers (see :class:`TransitRule`).
        stop_tracts: The tract holding each stop, aligned with ``stops`` (from
            :func:`points_in_polygons` or a ``tract`` column). Without it the
            "major stop inside the tract" test is skipped.
        tracts: Every tract id to report, so tracts with no blocks get zeros.

    Returns:
        One row per tract: ``tract`` then :data:`FEATURE_COLUMNS`. Shares are
        of the tract's 2020 block units and block land; a tract with no units
        has a units share of 0.
    """
    major_mask = major_stop_table(stops.assign(_row=np.arange(len(stops))), rule)
    dist = block_distances(blocks, major_mask)
    units = blocks["housing_units"].to_numpy(dtype=float)
    land = blocks["land_area_m2"].to_numpy(dtype=float)
    near_d07 = (dist["dist_rail_mi"] <= rule.rail_radius) | (dist["dist_bus_mi"] <= rule.bus_radius)
    cols: dict[str, np.ndarray] = {
        "tract": blocks["tract"].astype(str).to_numpy(),
        "_units": units,
        "_land": land,
    }
    for r in DISTANCE_CHOICES_MILES:
        within = (dist["dist_any_mi"] <= r).to_numpy()
        cols[f"transit_units_{radius_label(r)}"] = units * within
        cols[f"transit_land_m2_{radius_label(r)}"] = land * within
    cols["transit_units_d07"] = units * near_d07.to_numpy()
    cols["transit_land_m2_d07"] = land * near_d07.to_numpy()
    sums = pd.DataFrame(cols).groupby("tract", sort=True).sum()
    ids = sorted(set(sums.index) | set(map(str, () if tracts is None else tracts)))
    sums = sums.reindex(ids, fill_value=0.0)

    def share(num: pd.Series, den: pd.Series) -> pd.Series:
        return (num / den.where(den > 0)).fillna(0.0).clip(0.0, 1.0)

    out = pd.DataFrame(index=sums.index)
    for suffix in [radius_label(r) for r in DISTANCE_CHOICES_MILES] + ["d07"]:
        u, a = f"transit_units_{suffix}", f"transit_land_m2_{suffix}"
        out[u] = sums[u].round().astype("int64")
        out[f"transit_units_share_{suffix}"] = share(sums[u], sums["_units"]).round(4)
        out[a] = sums[a].round().astype("int64")
        out[f"transit_land_share_{suffix}"] = share(sums[a], sums["_land"]).round(4)
    if stop_tracts is not None and len(major_mask):
        holder = np.asarray(stop_tracts, dtype=object)[major_mask["_row"].to_numpy()]
        counts = pd.Series(holder[holder != ""]).value_counts()
        out["transit_major_stops"] = counts.reindex(out.index, fill_value=0).astype("int64")
    else:
        out["transit_major_stops"] = np.zeros(len(out), dtype="int64")
    rich = (out["transit_units_share_d07"] >= rule.rich_min_share) & (
        sums["_units"].reindex(out.index) > 0
    )
    if stop_tracts is not None:
        rich |= out["transit_major_stops"] > 0
    out["transit_rich"] = rich.astype("int64")
    out = out.reset_index(names="tract")
    return out[["tract", *FEATURE_COLUMNS]]


def station_count(stops: pd.DataFrame, merge_miles: float = 0.1) -> int:
    """Count rail stations: rail stops within ``merge_miles`` of each other count as one.

    Agencies list each platform or direction of a station as its own stop, and a
    station served by two agencies appears twice. Joining rail stops that lie
    within 0.1 mile (160 m) of another, in chains, gives a count close to what a
    rider would call stations. Used as a plausibility check only.
    """
    rail = stops[stops["mode"].astype(str) == FIXED_GUIDEWAY]
    n = len(rail)
    if n == 0:
        return 0
    lat, lon = rail["lat"].to_numpy(dtype=float), rail["lon"].to_numpy(dtype=float)
    parent = np.arange(n)

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        close = np.flatnonzero(great_circle_miles(lat[i], lon[i], lat[i:], lon[i:]) <= merge_miles)
        for j in close + i:
            a, b = root(i), root(int(j))
            if a != b:
                parent[a] = b
    return len({root(i) for i in range(n)})
