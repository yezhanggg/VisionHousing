"""Peak-hour bus departures per stop, from the agencies' schedule feeds (GTFS).

Plain-language summary
----------------------
The major-stop rule calls a bus stop "major" by its departures over a whole weekday.
The transit-oriented density benchmark also tests a stricter, commonly used
rule: **a bus stop is frequent when buses leave the same platform at least
every 15 minutes in both weekday peaks** (:mod:`visionhousing.features.transit_walk`
applies it). This module counts what that rule needs: for each platform, each
side of the street it serves and each hour of the day, the scheduled
departures on one weekday.

Which weekday
~~~~~~~~~~~~~
The **pinned date** is the representative weekday already recorded for the feed
when its daily counts were made (``data/raw/transit/gtfs_<id>_note.json``). If
the feed fetched now still runs trips that day, and the day is a **regular**
weekday, that day is used, so the two counts describe the same day. Otherwise
the busiest regular Tuesday, Wednesday or Thursday of the feed is used (the
same rule as the daily counts,
:func:`visionhousing.data.sources.transit.representative_weekday`, with
irregular days left out). A day is not regular when it is a US federal holiday
(or the weekday it is observed on) or falls between December 24 and January 1
(:func:`is_irregular_day`): schedules then often run a holiday or reduced
service. The note says which (``day_rule``: ``pinned`` or ``busiest_midweek``).

Counting (rule ``tod.walk.direction_rule``, ``assumptions/transit_walk.yaml``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- A departure is a trip calling at the stop where riders may board: no-pickup
  calls and each trip's last stop are left out (as for the daily counts).
- Its hour is the departure time's hour (arrival time when the departure is
  blank); times after midnight of the service day count as hours 24 to 29.
- Trips given as a frequency ("every 10 minutes from 7 to 9") are expanded into
  one run per headway, each calling at the stops at the trip's own offsets
  from its first stop (the first stop counts for the offsets even when riders
  may not board there). A frequency row with a blank or unreadable time or
  headway is skipped and counted in the note (``frequencies_skipped``).
- **The counting unit is the platform-level stop** (the ``stop_id`` that trips
  call at: one pole or platform). All routes and both GTFS ``direction_id``
  values are added together there, because ``direction_id`` is defined route by
  route: route A's direction 0 and route B's direction 0 need not point the
  same way, so adding "direction 0" across routes would undercount.
- **A pole is split in two only when it serves both directions of travel.**
  Each trip's heading at the pole is the compass bearing from the trip's
  previous stop to its next stop (from the pole itself at a trip's first stop),
  so two routes that turn opposite ways at the corner stay one side. The pole's
  main heading is the 30-degree sector holding the most trips;
  a trip whose heading is more than ``tod.frequent_bus_opposite_heading_deg``
  (120 degrees) from that sector's centre runs the other way. Where some trips
  run each way, the pole has two rows (``main`` and ``opposite``); otherwise one
  (``all``). A trip with no heading (its stops have no position or are at the
  same place) takes the side of the other trips of the same route and
  direction at the pole (``direction_id``, else the headsign with any
  "via ..." part cut, so "Downtown" and "Downtown via X" are one direction),
  else the main side.
- **Stations** (``parent_station``): each platform is counted on its own, and the
  station also gets one row per platform side (``direction`` =
  ``<platform>/<side>``). The station is frequent when any one platform side
  meets the rule; platforms are never added together.

Cache
-----
``data/raw/transit/gtfs_<id>_peaks.csv.gz`` (one row per stop and side:
``stop_id``, ``lat``, ``lon``, ``direction``, ``h00`` to ``h29``) and
``gtfs_<id>_peaks_note.json`` (the date, the rule that chose it, the counting
rule and its version, the feed's dates and when it was fetched). No feed zip is
kept. A feed that cannot be read gets a note and no table; delete the note to
try again. A note written under another counting rule (``rule``) is recounted.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.sources import transit as transit_src

__all__ = [
    "HOURS",
    "OPPOSITE_ID",
    "RULE_VERSION",
    "fetch_peaks",
    "headsign_key",
    "hour_columns",
    "is_irregular_day",
    "read_gtfs_peaks",
    "read_peaks",
]

LOG = logging.getLogger(__name__)

HOURS = 30
"""Hour columns kept: 0 to 29 (service days may run past midnight)."""
RULE_VERSION = 2
"""Version of the counting rule; a cached note with another version is recounted.
1: per ``direction_id`` summed across routes, platforms added to the station.
2: per platform-level stop, split only where one pole serves both directions."""
OPPOSITE_ID = "tod.frequent_bus_opposite_heading_deg"
"""Ledger id of the angle beyond which a trip runs the other way at a pole."""
SECTOR_DEG = 30.0
"""Width of the compass sectors used to find a pole's main heading."""
SAME_PLACE_M = 10.0
"""A next stop closer than this gives no heading."""


def hour_columns() -> list[str]:
    """``["h00", ..., "h29"]``."""
    return [f"h{h:02d}" for h in range(HOURS)]


def _seconds(text: Any) -> float:
    return transit_src.clock_seconds(text)


def headsign_key(text: Any) -> str:
    """A headsign reduced to its destination: lower case, any "via ..." or bracketed part cut.

    "Downtown", "DOWNTOWN via Main St" and "Downtown (Express)" all give "downtown".
    """
    t = re.sub(r"\(.*?\)", " ", str(text or "").lower())
    t = re.split(r"\bvia\b", t, maxsplit=1)[0]
    return " ".join(re.findall(r"[a-z0-9]+", t))


def _opposite_deg() -> float:
    from visionhousing.ledger import load_ledger

    return float(load_ledger()[OPPOSITE_ID].central)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th given weekday of a month (``n = -1``: the last)."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """A fixed-date holiday's observed day: Friday before a Saturday, Monday after a Sunday."""
    return (
        day - timedelta(days=1)
        if day.weekday() == 5
        else (day + timedelta(days=1) if day.weekday() == 6 else day)
    )


def federal_holidays(year: int) -> set[date]:
    """US federal holidays of a year (5 U.S.C. 6103), with their observed weekdays."""
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11),
             date(year, 12, 25)]  # fmt: skip
    out = set(fixed) | {_observed(d) for d in fixed}
    out |= {
        _nth_weekday(year, 1, 0, 3),  # Birthday of Martin Luther King, Jr.
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
    }
    out.add(_observed(date(year + 1, 1, 1)))  # may fall on December 31
    return out


def is_irregular_day(day: date) -> bool:
    """True for a US federal holiday (or its observed day) and for December 24 to January 1."""
    if (day.month == 12 and day.day >= 24) or (day.month == 1 and day.day == 1):
        return True
    return day in federal_holidays(day.year)


def _busiest_regular_midweek(
    calendar: pd.DataFrame, calendar_dates: pd.DataFrame, trips: pd.DataFrame
) -> tuple[date | None, set[str]]:
    """The regular Tuesday to Thursday with the most trips (ties: the earliest)."""
    day, services, _ = transit_src.representative_weekday(calendar, calendar_dates, trips)
    if day is None or not is_irregular_day(day):
        return day, services
    per_service = trips.groupby("service_id").size() if len(trips) else pd.Series(dtype=int)
    bounds = [
        transit_src._ymd(v)
        for v in (
            list(calendar["start_date"]) + list(calendar["end_date"]) if len(calendar) else []
        )
    ] + [transit_src._ymd(v) for v in (calendar_dates["date"] if len(calendar_dates) else [])]
    best: tuple[date | None, set[str], int] = (None, set(), -1)
    d = min(bounds)
    while d <= max(bounds):
        if d.weekday() in (1, 2, 3) and not is_irregular_day(d):
            running = _day_services(calendar, calendar_dates, d)
            n = int(per_service.reindex(sorted(running)).fillna(0).sum())
            if n > best[2]:
                best = (d, running, n)
        d += timedelta(days=1)
    return best[0], best[1]


def _day_services(calendar: pd.DataFrame, calendar_dates: pd.DataFrame, day: date) -> set[str]:
    """Services running on ``day``: weekly pattern within dates, plus added, minus removed."""
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    running: set[str] = set()
    for row in calendar.to_dict("records") if len(calendar) else []:
        start = transit_src._ymd(row["start_date"])
        end = transit_src._ymd(row["end_date"])
        if start <= day <= end and str(row.get(names[day.weekday()], "0")) == "1":
            running.add(row["service_id"])
    key = day.strftime("%Y%m%d")
    for row in calendar_dates.to_dict("records") if len(calendar_dates) else []:
        if str(row["date"]).strip() == key:
            if str(row["exception_type"]).strip() == "1":
                running.add(row["service_id"])
            else:
                running.discard(row["service_id"])
    return running


def _bearing(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Compass bearing (degrees) from point 1 to point 2; NaN closer than ``SAME_PLACE_M``."""
    dx = (lon2 - lon1) * np.cos(np.radians(lat1))
    dy = lat2 - lat1
    far = np.hypot(dx, dy) * 111_195.0 >= SAME_PLACE_M
    return np.where(far, np.degrees(np.arctan2(dx, dy)) % 360.0, np.nan)


def _headings(st: pd.DataFrame, stops: pd.DataFrame) -> np.ndarray:
    """Each call's heading: bearing from the trip's previous stop to its next stop.

    Measured across the stop (previous to next), two routes that come down the same
    street and turn opposite ways at the corner still read as one side; only
    service running the other way along the street reads as opposite. A trip's
    first stop uses the bearing from the stop itself to the next one. NaN where
    there is no next stop or the points have no position or coincide.
    """
    lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
    lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
    la = dict(zip(stops["stop_id"], lat, strict=True))
    lo = dict(zip(stops["stop_id"], lon, strict=True))
    by_trip = st.groupby("trip_id")["stop_id"]
    here_la = st["stop_id"].map(la).to_numpy(float)
    here_lo = st["stop_id"].map(lo).to_numpy(float)
    nxt, prv = by_trip.shift(-1), by_trip.shift(1)
    n_la, n_lo = nxt.map(la).to_numpy(float), nxt.map(lo).to_numpy(float)
    p_la, p_lo = prv.map(la).to_numpy(float), prv.map(lo).to_numpy(float)
    across = _bearing(p_la, p_lo, n_la, n_lo)
    ahead = _bearing(here_la, here_lo, n_la, n_lo)
    return np.where(np.isnan(across), ahead, across)


def _sides(calls: pd.DataFrame, opposite_deg: float) -> pd.Series:
    """``all``, ``main`` or ``opposite`` for each call (see the module notes)."""
    known = calls[calls["heading"].notna()]
    sector = (known["heading"] // SECTOR_DEG).astype(int) % int(360 / SECTOR_DEG)
    tally = (
        pd.DataFrame({"stop_id": known["stop_id"], "sector": sector})
        .value_counts()
        .reset_index(name="n")
        .sort_values(["stop_id", "n", "sector"], ascending=[True, False, True])
        .drop_duplicates("stop_id")
        .set_index("stop_id")["sector"]
    )
    centre = known["stop_id"].map(tally).to_numpy(float) * SECTOR_DEG + SECTOR_DEG / 2
    diff = np.abs((known["heading"].to_numpy(float) - centre + 180.0) % 360.0 - 180.0)
    side = pd.Series("main", index=calls.index, dtype=object)
    side.loc[known.index] = np.where(diff > opposite_deg, "opposite", "main")
    unknown = calls.index[calls["heading"].isna()]
    if len(unknown):
        keys = ["stop_id", "route_id", "dkey"]
        share = (
            (side.loc[known.index] == "opposite")
            .groupby([known[k] for k in keys])
            .mean()
            .rename("share")
            .reset_index()
        )
        filled = calls.loc[unknown, keys].merge(share, on=keys, how="left")["share"].to_numpy()
        side.loc[unknown] = np.where(filled > 0.5, "opposite", "main")
    split = set(calls.loc[side == "opposite", "stop_id"])
    return side.where(calls["stop_id"].isin(split), "all")


def read_gtfs_peaks(
    members: Mapping[str, bytes],
    pinned: date | None = None,
    *,
    opposite_heading_deg: float | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Count departures per platform-level stop, side and hour on one weekday.

    Args:
        members: Feed table name to its bytes (``stops.txt``, ``trips.txt``,
            ``stop_times.txt``, ``calendar.txt``, ``calendar_dates.txt``,
            ``frequencies.txt``).
        pinned: The date to use if the feed runs trips that day.
        opposite_heading_deg: Angle beyond which a trip runs the other way at a
            pole; default the ledger's ``tod.frequent_bus_opposite_heading_deg``.

    Returns:
        The table (``stop_id``, ``lat``, ``lon``, ``direction`` and the hour
        columns; ``direction`` is ``all``, ``main`` or ``opposite`` for a pole and
        ``<platform>/<side>`` for a station) and a note (``date``, ``day_rule``,
        ``trips_on_date``, ``stops``, ``split_poles``, ``frequencies_skipped``,
        ``rule``).
    """
    opposite = _opposite_deg() if opposite_heading_deg is None else float(opposite_heading_deg)
    tab = transit_src._table
    stops = tab(members, "stops.txt")
    trips = tab(members, "trips.txt")
    calendar = tab(members, "calendar.txt")
    calendar_dates = tab(members, "calendar_dates.txt")
    day, services, rule = None, set(), ""
    if pinned is not None and not is_irregular_day(pinned):
        services = _day_services(calendar, calendar_dates, pinned)
        if len(trips) and trips["service_id"].isin(services).any():
            day, rule = pinned, "pinned"
    if day is None:
        day, services = _busiest_regular_midweek(calendar, calendar_dates, trips)
        rule = "busiest_midweek"
    active = trips[trips["service_id"].isin(services)].copy() if len(trips) else trips
    for col in ("route_id", "direction_id", "trip_headsign"):
        if col not in active.columns:
            active[col] = ""
    active["dkey"] = np.where(
        active["direction_id"] != "", "d" + active["direction_id"],
        "h" + active["trip_headsign"].map(headsign_key),
    )  # fmt: skip
    wanted = [
        "trip_id",
        "stop_id",
        "stop_sequence",
        "pickup_type",
        "departure_time",
        "arrival_time",
    ]
    st = pd.read_csv(
        io.BytesIO(members.get("stop_times.txt", b"")),
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        usecols=lambda c: c.strip() in wanted,
    )
    st.columns = [c.strip() for c in st.columns]
    for col in ("pickup_type", "departure_time", "arrival_time"):
        if col not in st.columns:
            st[col] = ""
    st = st[st["trip_id"].str.strip().isin(set(active["trip_id"]))].copy()
    for col in st.columns:
        st[col] = st[col].str.strip()
    st["seq"] = pd.to_numeric(st["stop_sequence"], errors="coerce")
    st["t"] = [
        _seconds(d) if d else _seconds(a)
        for d, a in zip(st["departure_time"], st["arrival_time"], strict=True)
    ]
    # Blank times between timed stops are filled by straight-line interpolation along the
    # trip (a trip's first and last stops always carry times, so trips never mix).
    st = st.sort_values(["trip_id", "seq"], kind="stable").reset_index(drop=True)
    st["t"] = st["t"].interpolate(limit_area="inside")
    st["heading"] = _headings(st, stops)
    trip_start = st.groupby("trip_id")["t"].min()  # all calls, boarding or not
    last = st.groupby("trip_id")["seq"].transform("max")
    st = st[(st["seq"] < last) & (st["pickup_type"] != "1") & st["t"].notna()].copy()
    info = active.set_index("trip_id")
    st["route_id"] = st["trip_id"].map(info["route_id"])
    st["dkey"] = st["trip_id"].map(info["dkey"])
    st["direction"] = _sides(st, opposite) if len(st) else pd.Series(dtype=object)
    calls = st[["trip_id", "stop_id", "direction", "t"]]
    freq = tab(members, "frequencies.txt")
    rows, skipped = transit_src.frequency_rows(freq, set(calls["trip_id"]))
    if skipped:
        LOG.warning("frequencies.txt: %d unreadable rows skipped", skipped)
    if rows:
        runs = []
        for trip_id, s0, s1, hw in rows:
            starts = s0 + hw * np.arange(math.ceil((s1 - s0) / hw))
            c = calls[calls["trip_id"] == trip_id]
            off = (c["t"] - trip_start[trip_id]).to_numpy()
            runs.append(
                pd.DataFrame(
                    {
                        "stop_id": np.repeat(c["stop_id"].to_numpy(), len(starts)),
                        "direction": np.repeat(c["direction"].to_numpy(), len(starts)),
                        "t": (off[:, None] + starts[None, :]).ravel(),
                    }
                )
            )
        expanded = {r[0] for r in rows}
        calls = pd.concat(
            [calls[~calls["trip_id"].isin(expanded)].drop(columns="trip_id"), *runs],
            ignore_index=True,
        )
    else:
        calls = calls.drop(columns="trip_id")
    calls = calls.assign(hour=np.clip((calls["t"] // 3600).astype(int), 0, HOURS - 1))
    counts = (
        calls.groupby(["stop_id", "direction", "hour"]).size().unstack("hour", fill_value=0)
        if len(calls)
        else pd.DataFrame()
    )
    counts = counts.reindex(columns=range(HOURS), fill_value=0)
    counts.columns = hour_columns()
    counts = counts.reset_index()
    if len(counts) and "parent_station" in stops.columns:
        parent = dict(zip(stops["stop_id"], stops["parent_station"], strict=True))
        child = counts[counts["stop_id"].map(parent).fillna("") != ""]
        if len(child):
            station = child.assign(
                direction=child["stop_id"] + "/" + child["direction"],
                stop_id=child["stop_id"].map(parent),
            )
            counts = pd.concat([counts, station], ignore_index=True)
    where = pd.DataFrame(
        {
            "stop_id": stops["stop_id"],
            "lat": pd.to_numeric(stops["stop_lat"], errors="coerce"),
            "lon": pd.to_numeric(stops["stop_lon"], errors="coerce"),
        }
    )
    out = where.merge(counts, on="stop_id", how="inner") if len(counts) else where.iloc[:0]
    out = out.dropna(subset=["lat", "lon"]).sort_values(["stop_id", "direction"])
    note = {
        "date": day.isoformat() if day else None,
        "day_rule": rule,
        "trips_on_date": len(active),
        "stops": int(out["stop_id"].nunique()) if len(out) else 0,
        "split_poles": int(out.loc[out["direction"] == "opposite", "stop_id"].nunique())
        if len(out)
        else 0,
        "frequencies_skipped": skipped,
        "rule": _rule(opposite),
    }
    return out.reset_index(drop=True), note


def _rule(opposite: float) -> dict[str, Any]:
    return {
        "version": RULE_VERSION,
        "heading": "previous_to_next",
        "opposite_heading_deg": opposite,
    }


def read_peaks(path: Path) -> pd.DataFrame:
    """Read a cached peaks table."""
    frame = pd.read_csv(path, dtype={"stop_id": str, "direction": str}, keep_default_na=False)
    return frame


def fetch_peaks(
    mdb_id: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    opposite_heading_deg: float | None = None,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Count one schedule feed's peak departures (or reuse the cache).

    The address is the one the daily counts used (from the feed's note), then
    the catalog's mirror. The pinned date is the note's representative date.
    A cached count made under another counting rule (version or angle), or
    on an irregular day (a holiday, see :func:`is_irregular_day`), is redone.
    """
    opposite = _opposite_deg() if opposite_heading_deg is None else float(opposite_heading_deg)
    folder = Path(cache) / "transit"
    table_path = folder / f"gtfs_{mdb_id}_peaks.csv.gz"
    note_path = folder / f"gtfs_{mdb_id}_peaks_note.json"
    if note_path.exists():
        note = json.loads(note_path.read_text(encoding="utf-8"))
        counted = note.get("date")
        holiday = bool(counted) and is_irregular_day(date.fromisoformat(str(counted)))
        if (note.get("rule") == _rule(opposite) and not holiday) or note.get("status") != "ok":
            return (read_peaks(table_path) if table_path.exists() else None), note
    daily = json.loads((folder / f"gtfs_{mdb_id}_note.json").read_text(encoding="utf-8"))
    pinned_text = daily.get("representative_date")
    pinned = date.fromisoformat(pinned_text) if pinned_text else None
    urls = [daily.get("url", "")]
    catalog = sorted(folder.glob("gtfs_catalog_*.csv"))
    for path in catalog:
        cat = pd.read_csv(path, dtype=str, keep_default_na=False)
        row = cat[cat["mdb_source_id"] == str(mdb_id)]
        if len(row) and "urls.latest" in row.columns:
            urls.append(row["urls.latest"].iloc[0])
    client = client or HttpClient()
    note: dict[str, Any] = {
        "schedule_feed": str(mdb_id),
        "pinned_date": pinned_text,
        "fetched_on": date.today().isoformat(),
    }
    table, errors = None, []
    for url in [u for u in urls if u]:
        try:
            members, how = transit_src._read_members(client, transit_src._https(url))
            if "stop_times.txt" not in members or "stops.txt" not in members:
                raise ValueError("the zip lacks stops.txt or stop_times.txt")
            table, info = read_gtfs_peaks(members, pinned, opposite_heading_deg=opposite)
            note.update(info, status="ok", url=transit_src._https(url), read=how)
            break
        except (FetchError, OSError, ValueError, KeyError) as err:
            errors.append(f"{type(err).__name__}: {str(err)[:200]}")
    if errors:
        note["errors"] = errors
    if table is None:
        note["status"] = "unreachable"
    else:
        transit_src._write_gz(table, table_path)
        if manifest is not None:
            mf.record(
                mf.entry_for_file(
                    transit_src.SOURCE_GTFS, note["url"], table_path, note.get("date") or "",
                    root=mf.ROOT,
                ),
                manifest,
            )  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    note_path.write_text(json.dumps(note, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return table, note
