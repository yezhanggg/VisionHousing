"""Transit stops and service frequency: the National Transit Map plus agency schedule feeds.

Plain-language summary
----------------------
The reforms that depend on transit need to know where the **major transit
stops** are: every fixed-guideway station (subway, light rail or
trolley, commuter or regional rail, funicular), and every bus stop with at least
64 scheduled departures on a representative weekday.

Where the data come from
~~~~~~~~~~~~~~~~~~~~~~~~
1. **Stop locations and modes: the National Transit Map (NTM)** of the U.S.
   Bureau of Transportation Statistics. It is a map service; it is asked for
   every stop inside the state's bounding box, 2,000 at a time ("paged"), and
   the stops are then kept only if they fall inside one of the state's census
   tracts. For each stop the NTM gives the kinds of route that serve it
   (``stop_type``, as GTFS route type numbers), the agency's National Transit
   Database id and its schedule-feed id.
2. **Departures: the agencies' own schedule feeds.** A schedule feed (GTFS,
   the General Transit Feed Specification) is a zip of text tables: stops,
   routes, trips, the times each trip calls at each stop (``stop_times.txt``),
   and the days each service runs (``calendar.txt`` and ``calendar_dates.txt``).
   The list of feeds comes from the Mobility Database catalog (MobilityData),
   which lists each feed's direct download address and keeps a mirror copy.
   Agencies that catalog does not list may be named in
   ``assumptions/transit_feeds.yaml``: their operator's static schedule address
   is read from the Transitland Atlas catalogue (CC-BY 4.0) at a pinned commit,
   and each carries its own legal record; one whose terms forbid reuse is not
   read. Only feeds that download without a key or log-in are used. Each feed's
   agency address is tried first (over https), then the catalog's mirror. Only
   the needed tables are read out of the zip, by HTTP range requests where the
   server allows them; the large ``shapes.txt`` is never downloaded.

Counting departures
~~~~~~~~~~~~~~~~~~~
- **Representative weekday.** Among the Tuesdays, Wednesdays and Thursdays the
  feed covers, the day with the most scheduled trips once the calendar's
  exceptions (holidays, added service) are applied. Ties go to the earliest.
- A **departure** is one trip calling at the stop on that day and letting riders
  board: calls marked "no pickup" and each trip's last stop are not counted.
  Trips given as a frequency ("every 10 minutes from 7 to 9") count once per run.
- A station's departures are the sum over its platforms.

Matching the two sources
~~~~~~~~~~~~~~~~~~~~~~~~
An NTM feed is matched to the schedule feed that has the most of its stop ids at
the same place (within 200 m); at least half its stops must match (``paired_by``
= ``id`` in ``transit_feeds``). Where an agency renumbered its stops, the feed
may be paired **by place** instead (``paired_by`` = ``place``): the agency names
must share a distinctive word (not a state name or a word like "transit",
"valley" or "port"), half the NTM feed's stops must have a schedule stop within
50 m, and the reverse must hold too: half the schedule feed's in-state stops lie
within 50 m of the NTM feed's stops, or the stop names agree. A stop then
takes the departures of the stop with the same id (within 200 m), or of the
nearest stop of the same feed within 50 m. A ``frequencies.txt`` row that cannot
be read is skipped and counted in the feed's note (``frequencies_skipped``).

Honest gaps
~~~~~~~~~~~
- Rail and other fixed-guideway stops are classed by **mode only**: every one is
  major, whatever its frequency.
- A bus stop with no matched feed is marked **frequency unknown** and is not a
  major stop. The ``transit_feeds`` table lists, agency by agency, how many bus
  stops were counted and why the others were not.
- Bus rapid transit on its own lanes is not told apart from other buses; its
  stations count when their departures reach the minimum, which busway
  stations usually do.
- Feeds and the NTM are snapshots; the schedule date of each feed is recorded.

Cache
-----
Only small extracts are kept in ``data/raw/transit/``: the stops in the state's
bounding box, the agency names, the state's rows of the feed catalog, and for
each feed one table of stops with their counted departures plus a short JSON
note (dates, address used, or why it failed). No feed zip is stored. A feed
that could not be fetched is recorded as such, so a rebuild from the same cache
is identical; delete its note to try again.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import re
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.features import transit as tf

__all__ = [
    "FEED_COLUMNS",
    "GTFS_MEMBERS",
    "NTM_COLUMNS",
    "STATE_NAMES",
    "STOP_COLUMNS",
    "TransitInputs",
    "build_stop_table",
    "fetch_atlas_catalog",
    "fetch_feed_catalog",
    "fetch_gtfs_feed",
    "fetch_ntm_agencies",
    "fetch_ntm_stops",
    "fetch_transit",
    "is_stale",
    "load_feed_register",
    "query_ntm",
    "read_gtfs",
    "read_ntm_page",
    "read_ntm_stops",
    "representative_weekday",
    "route_group",
    "select_feeds",
    "state_bbox",
]

SOURCE_STOPS = "ntm_stops"
SOURCE_AGENCIES = "ntm_agencies"
SOURCE_CATALOG = "mdb_catalog"
SOURCE_GTFS = "gtfs_agency_feeds"
SOURCE_ATLAS = "transitland_atlas"
REGISTER_PATH = Path(__file__).resolve().parents[4] / "assumptions" / "transit_feeds.yaml"
"""Feeds found outside the Mobility Database catalog, with their legal records."""
CATALOG_COLUMNS = (
    "mdb_source_id",
    "provider",
    "status",
    "urls.direct_download",
    "urls.authentication_type",
    "urls.latest",
    "urls.license",
)
"""The catalog columns kept (the Transitland Atlas rows use the same ones)."""
NTM_PAGE = 2000
"""Rows per map-service page (the service's own maximum)."""
NTM_TRIES = 4
"""Tries of one map-service query that answers with an error object. Under load the service
sometimes refuses a valid query (seen for Massachusetts, 18 pages); the answer then still has
HTTP status 200, so the HTTP client does not retry it by itself."""
MATCH_ID_MILES = 200 / 1609.344
"""A stop with the same id must be this close (200 m) to count as the same stop."""
MATCH_NEAR_MILES = 50 / 1609.344
"""Without an id match, the nearest stop of the same feed within 50 m is used."""
FEED_MATCH_MIN_SHARE = 0.5
"""Share of an NTM feed's stops that must match a schedule feed for the two to be paired."""
_COMMON_NAME_WORDS = frozenset(
    {
        "agency", "airport", "area", "authority", "city", "coast", "commission",
        "community", "company", "council", "county", "department", "district", "express",
        "greater", "lines", "metro", "metropolitan", "municipal", "port", "public",
        "rapid", "region", "regional", "river", "rural", "service", "services", "shuttle",
        "state", "system", "town", "transit", "transport", "transportation", "valley",
        "bay", "cape", "central", "east", "north", "south", "west",
    }
)  # fmt: skip
_PLACE_STATE_WORDS = frozenset({"washington", "delaware", "indiana", "york", "columbia"})
"""State-name words kept as distinctive: they are also county or city names that local
agencies carry (Washington County, Delaware County, Indiana County, York, Columbia)."""
# GENERIC_NAME_WORDS is completed after STATE_NAMES below (every word of every state name).
STATE_EDGE_MILES = 0.1
"""A stop just outside every tract outline (a shoreline) joins the nearest block's tract within
this distance, if that block is in the state."""

NTM_COLUMNS = (
    "ntd_id",
    "feed_id",
    "stop_id",
    "stop_name",
    "lat",
    "lon",
    "location_type",
    "parent_station",
    "stop_type",
)
"""Columns kept from the National Transit Map stops service."""

STOP_COLUMNS = (
    "stop_id",
    "lat",
    "lon",
    "mode",
    "weekday_departures",
    "major",
    "frequency",
    "route_types",
    "county",
    "tract",
    "ntd_id",
    "feed_id",
    "stop_name",
)
"""Columns of the pack's ``transit_stops`` table; the first five are Testland's."""

FEED_COLUMNS = (
    "feed_id",
    "ntd_id",
    "agency",
    "stops",
    "rail_stops",
    "bus_stops",
    "bus_stops_counted",
    "major_bus_stops",
    "frequency",
    "schedule_feed",
    "paired_by",
    "schedule_provider",
    "schedule_date",
    "schedule_valid_from",
    "schedule_valid_to",
)
"""Columns of the pack's ``transit_feeds`` coverage table."""

GTFS_MEMBERS = (
    "agency.txt",
    "stops.txt",
    "routes.txt",
    "trips.txt",
    "stop_times.txt",
    "calendar.txt",
    "calendar_dates.txt",
    "frequencies.txt",
    "feed_info.txt",
)
"""The feed tables read; the others (shapes, fares, pathways) are never downloaded."""

STATE_NAMES: dict[str, str] = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas", "06": "California",
    "08": "Colorado", "09": "Connecticut", "10": "Delaware", "11": "District of Columbia",
    "12": "Florida", "13": "Georgia", "15": "Hawaii", "16": "Idaho", "17": "Illinois",
    "18": "Indiana", "19": "Iowa", "20": "Kansas", "21": "Kentucky", "22": "Louisiana",
    "23": "Maine", "24": "Maryland", "25": "Massachusetts", "26": "Michigan", "27": "Minnesota",
    "28": "Mississippi", "29": "Missouri", "30": "Montana", "31": "Nebraska", "32": "Nevada",
    "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico", "36": "New York",
    "37": "North Carolina", "38": "North Dakota", "39": "Ohio", "40": "Oklahoma", "41": "Oregon",
    "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina", "46": "South Dakota",
    "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont", "51": "Virginia",
    "53": "Washington", "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming",
    "72": "Puerto Rico",
}  # fmt: skip
"""State code to the state name the feed catalog uses."""

GENERIC_NAME_WORDS = _COMMON_NAME_WORDS | (
    {w for name in STATE_NAMES.values() for w in re.findall(r"[a-z]+", name.lower())}
    - _PLACE_STATE_WORDS
)
"""Words too common in agency names to show that two names mean the same agency: generic
transit words, compass and landform words, and the words of every state name (a state's
port authority and its transit authority share the state's name, not the agency)."""
PAIR_REVERSE_ID = "parking.transit_feed_pair_reverse_min_share"
PAIR_NAME_ID = "parking.transit_feed_pair_name_similarity"
"""Ledger ids of the two numbers of the reverse check on place pairing."""


# --------------------------------------------------------------------------- modes


def route_group(route_type: int | str) -> str:
    """Group a GTFS route type into ``rail`` (fixed guideway), ``bus`` or ``other``.

    Basic types: 0 tram / light rail / trolley, 1 subway, 2 rail, 5 cable tram,
    7 funicular and 12 monorail are fixed guideway; 3 bus and 11 trolleybus are
    bus; 4 ferry, 6 aerial lift and anything unknown are other. The extended
    types (100-1700) are grouped the same way by their hundreds.
    """
    try:
        t = int(str(route_type).strip().strip('"'))
    except ValueError:
        return "other"
    if t in (0, 1, 2, 5, 7, 12):
        return "rail"
    if t in (3, 11):
        return "bus"
    if 100 <= t < 200 or 400 <= t < 500 or 900 <= t < 1000 or t == 1400:
        return "rail"
    if 200 <= t < 300 or 700 <= t < 900:
        return "bus"
    return "other"


def _groups(types: str) -> set[str]:
    return {route_group(t) for t in str(types).split(";") if t.strip()}


def _mode(groups: set[str]) -> str:
    for mode in ("rail", "bus", "other"):
        if mode in groups:
            return mode
    return "unknown"


# --------------------------------------------------------------------------- NTM stops


def state_bbox(shapes: Iterable[Any], pad: float = 0.01) -> tuple[float, float, float, float]:
    """Bounding box ``(west, south, east, north)`` of shapes' GeoJSON geometries, padded."""
    xs: list[float] = []
    ys: list[float] = []
    for shape in shapes:
        geometry = shape.geometry if hasattr(shape, "geometry") else shape
        for ring in tf._rings(geometry):
            xs += [ring[:, 0].min(), ring[:, 0].max()]
            ys += [ring[:, 1].min(), ring[:, 1].max()]
    if not xs:
        raise ValueError("no shapes to take a bounding box from")
    return (
        round(min(xs) - pad, 4),
        round(min(ys) - pad, 4),
        round(max(xs) + pad, 4),
        round(max(ys) + pad, 4),
    )


def read_ntm_page(data: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Read one page of the map service's JSON answer.

    Returns:
        The rows' attributes and whether the service has more rows to give.

    Raises:
        FetchError: If the service answered with an error object.
    """
    doc = json.loads(data.decode("utf-8"))
    if "error" in doc:
        raise FetchError(f"map service error: {doc['error']}")
    rows = [f.get("attributes", {}) for f in doc.get("features", [])]
    return rows, bool(doc.get("exceededTransferLimit"))


def query_ntm(
    client: HttpClient, url: str, params: Mapping[str, Any], *, tries: int = NTM_TRIES
) -> bytes:
    """Send one map-service query; send it again when the answer is an error object.

    Waits between tries double from the client's ``backoff`` (capped at its
    ``max_wait``), like the client's own retries.

    Raises:
        FetchError: If every try answered with an error object.
    """
    last: object = None
    for attempt in range(max(1, tries)):
        data = client.get_bytes(url, params)
        try:
            doc = json.loads(data.decode("utf-8"))
        except ValueError:
            return data
        if not isinstance(doc, dict) or "error" not in doc:
            return data
        last = doc["error"]
        if attempt + 1 < tries:
            wait = min(client.backoff * (2**attempt), client.max_wait)
            client.waits.append(wait)
            client.sleep(wait)
    raise FetchError(f"map service error after {max(1, tries)} tries: {last}")


def _ntm_frame(rows: list[Mapping[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame = frame.rename(columns={"stop_lat": "lat", "stop_lon": "lon"})
    for col in NTM_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    out = frame[list(NTM_COLUMNS)].copy()
    for col in NTM_COLUMNS:
        if col not in ("lat", "lon"):
            out[col] = out[col].fillna("").astype(str).str.strip()
    out["location_type"] = out["location_type"].replace({"Null": "", "null": ""})
    out["lat"] = pd.to_numeric(out["lat"], errors="coerce")
    out["lon"] = pd.to_numeric(out["lon"], errors="coerce")
    out = out.dropna(subset=["lat", "lon"])
    return out.sort_values(["feed_id", "stop_id"], kind="stable").reset_index(drop=True)


def read_ntm_stops(path: Path) -> pd.DataFrame:
    """Read the cached stop extract (gzipped CSV of :data:`NTM_COLUMNS`)."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, compression="gzip")
    frame["lat"] = frame["lat"].astype(float)
    frame["lon"] = frame["lon"].astype(float)
    return frame[list(NTM_COLUMNS)]


def _write_gz(frame: pd.DataFrame, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    text = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    with part.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(text)
    part.replace(dest)


def fetch_ntm_stops(
    state: str,
    bbox: tuple[float, float, float, float],
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Every NTM stop in the bounding box, paged; cached as a small gzipped CSV.

    Raises:
        FetchError: If the pages do not add up to the service's own count.
    """
    fips = to_state_fips(state)
    dest = Path(cache) / "transit" / f"ntm_stops_{fips}.csv.gz"
    if dest.exists():
        return read_ntm_stops(dest)
    client = client or HttpClient()
    url = get_source(SOURCE_STOPS).url()
    base = {
        "where": "1=1",
        "geometry": ",".join(str(v) for v in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
    }
    count = json.loads(query_ntm(client, url, {**base, "returnCountOnly": "true"}))["count"]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page, more = read_ntm_page(
            query_ntm(
                client,
                url,
                {
                    **base,
                    "outFields": ",".join(
                        c if c not in ("lat", "lon") else f"stop_{c}" for c in NTM_COLUMNS
                    ),
                    "returnGeometry": "false",
                    "orderByFields": "OBJECTID",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(NTM_PAGE),
                },
            )
        )
        rows += page
        offset += len(page)
        if not page or (not more and len(page) < NTM_PAGE):
            break
    if len(rows) != count:
        raise FetchError(f"National Transit Map: got {len(rows)} stops, the service counts {count}")
    frame = _ntm_frame(rows)
    _write_gz(frame, dest)
    if manifest is not None:
        mf.record(
            mf.entry_for_file(
                SOURCE_STOPS, url, dest, get_source(SOURCE_STOPS).vintage, root=mf.ROOT
            ),
            manifest,
        )
    return read_ntm_stops(dest)


def fetch_ntm_agencies(
    state: str,
    ntd_ids: Sequence[str],
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Agency names for the given National Transit Database ids; cached as a CSV."""
    fips = to_state_fips(state)
    dest = Path(cache) / "transit" / f"ntm_agencies_{fips}.csv"
    if not dest.exists():
        client = client or HttpClient()
        url = get_source(SOURCE_AGENCIES).url()
        ids = sorted({i for i in ntd_ids if i and i.isalnum()})
        rows: list[dict[str, Any]] = []
        for start in range(0, len(ids), 100):
            where = "NTD_ID IN (" + ",".join(f"'{i}'" for i in ids[start : start + 100]) + ")"
            page, _ = read_ntm_page(
                query_ntm(
                    client,
                    url,
                    {
                        "where": where,
                        "outFields": "NTD_ID,Agency_Name,City,State",
                        "returnGeometry": "false",
                        "f": "json",
                    },
                )
            )
            rows += page
        frame = pd.DataFrame(rows, columns=["NTD_ID", "Agency_Name", "City", "State"])
        frame.columns = ["ntd_id", "agency", "city", "state"]
        frame = frame.drop_duplicates("ntd_id").sort_values("ntd_id")
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(dest, index=False, lineterminator="\n")
        if manifest is not None:
            source = get_source(SOURCE_AGENCIES)
            mf.record(
                mf.entry_for_file(source.id, url, dest, source.vintage, root=mf.ROOT), manifest
            )
    return pd.read_csv(dest, dtype=str, keep_default_na=False)


# --------------------------------------------------------------------------- feed catalog


def fetch_feed_catalog(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """The catalog's schedule feeds (GTFS) for the state; only those rows are cached."""
    fips = to_state_fips(state)
    dest = Path(cache) / "transit" / f"gtfs_catalog_{fips}.csv"
    if not dest.exists():
        client = client or HttpClient()
        source = get_source(SOURCE_CATALOG)
        url = source.url()
        data = client.get_bytes(url)
        frame = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
        keep = (frame["data_type"] == "gtfs") & (
            frame["location.subdivision_name"] == STATE_NAMES[fips]
        )
        frame = frame.loc[keep, list(CATALOG_COLUMNS)].sort_values(
            "mdb_source_id", key=lambda s: s.astype(int)
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(dest, index=False, lineterminator="\n")
        if manifest is not None:
            mf.record(
                mf.entry_for_file(source.id, url, dest, source.vintage, root=mf.ROOT), manifest
            )
    return pd.read_csv(dest, dtype=str, keep_default_na=False)


def load_feed_register(path: Path = REGISTER_PATH) -> dict[str, Any]:
    """Read ``assumptions/transit_feeds.yaml`` (feeds found outside the main catalog)."""
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def register_feeds(state: str, register: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The register's feeds for one state (two-letter code), read or not."""
    register = load_feed_register() if register is None else register
    st = state.upper()
    return [dict(f) for f in register.get("feeds", []) if str(f.get("state", "")).upper() == st]


def fetch_atlas_catalog(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    register: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Catalog rows for the register's feeds to read in ``state``, from the Transitland Atlas.

    Each feed marked ``read: true`` whose legal status is ``usable`` is looked up
    in its catalogue file at the pinned commit; its current static schedule
    address becomes ``urls.direct_download``. A feed whose terms forbid reuse
    (any other legal status) is not fetched. The rows are cached in
    ``data/raw/transit/gtfs_atlas_<fips>.csv`` (no file when the state has none).

    Returns:
        Rows in the catalog's columns (:data:`CATALOG_COLUMNS`); the id is the
        register's id (``tl-...``), never a Mobility Database number.
    """
    fips = to_state_fips(state)
    register = load_feed_register() if register is None else register
    wanted = [
        f for f in register_feeds(state, register)
        if f.get("read") and (f.get("legal") or {}).get("status") == "usable"
    ]  # fmt: skip
    if not wanted:
        return pd.DataFrame(columns=list(CATALOG_COLUMNS))
    dest = Path(cache) / "transit" / f"gtfs_atlas_{fips}.csv"
    if not dest.exists():
        client = client or HttpClient()
        source = get_source(SOURCE_ATLAS)
        commit = str(register["atlas"]["commit"])
        rows, files = [], {}
        for feed in wanted:
            url = source.url(commit=commit, atlas_file=feed["atlas_file"])
            if url not in files:
                files[url] = json.loads(client.get_bytes(url).decode("utf-8"))
            entry = next(
                (e for e in files[url].get("feeds", []) if e.get("id") == feed["onestop_id"]), None
            )
            if entry is None:
                raise ValueError(f"{feed['onestop_id']} is not in {feed['atlas_file']} at {commit}")
            licence = entry.get("license") or {}
            rows.append(
                {
                    "mdb_source_id": feed["id"],
                    "provider": feed["agency"],
                    "status": "active",
                    "urls.direct_download": (entry.get("urls") or {}).get("static_current", ""),
                    "urls.authentication_type": "0",
                    "urls.latest": "",
                    "urls.license": licence.get("url", "") or feed["legal"].get("terms_url", ""),
                }
            )
        frame = pd.DataFrame(rows, columns=list(CATALOG_COLUMNS))
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(dest, index=False, lineterminator="\n")
        if manifest is not None:
            for url in files:
                mf.record(mf.entry_for_file(source.id, url, dest, commit, root=mf.ROOT), manifest)
    return pd.read_csv(dest, dtype=str, keep_default_na=False)


def select_feeds(catalog: pd.DataFrame) -> pd.DataFrame:
    """Keep the feeds to read: keyless, and not superseded.

    A feed that needs a key or log-in (``urls.authentication_type`` not 0 or
    blank) is left out. A feed the catalog marks deprecated is kept only when
    the same provider has no active feed (it may still describe the service).
    """
    keyless = catalog["urls.authentication_type"].isin(["", "0"])
    cat = catalog[keyless].copy()
    active = set(cat.loc[cat["status"] != "deprecated", "provider"])
    keep = (cat["status"] != "deprecated") | ~cat["provider"].isin(active)
    return cat[keep].reset_index(drop=True)


# --------------------------------------------------------------------------- GTFS


def _table(members: Mapping[str, bytes], name: str) -> pd.DataFrame:
    data = members.get(name)
    if not data:
        return pd.DataFrame()
    frame = pd.read_csv(
        io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig", low_memory=False
    )
    frame.columns = [c.strip() for c in frame.columns]
    return frame.apply(lambda s: s.str.strip())


def _ymd(text: str) -> date:
    t = str(text).strip()
    return date(int(t[:4]), int(t[4:6]), int(t[6:8]))


def representative_weekday(
    calendar: pd.DataFrame, calendar_dates: pd.DataFrame, trips: pd.DataFrame
) -> tuple[date | None, set[str], int]:
    """Pick the midweek day with the most trips, after the calendar's exceptions.

    Looks at every Tuesday, Wednesday and Thursday from the first to the last
    date the feed covers. On each, the services running are those whose weekly
    pattern includes that day within their date range, plus the services added
    for that date (exception type 1), minus those removed (type 2). The day's
    trips are the trips of those services. Ties go to the earliest day.

    Returns:
        The day (``None`` if the feed covers no midweek day), its service ids
        and its number of trips.
    """
    trips_per_service = trips.groupby("service_id").size() if len(trips) else pd.Series(dtype=int)
    weekly: list[tuple[str, date, date, set[int]]] = []
    bounds: list[date] = []
    for row in calendar.to_dict("records") if len(calendar) else []:
        days = {
            i
            for i, d in enumerate(
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
            )
            if str(row.get(d, "0")) == "1"
        }
        start, end = _ymd(row["start_date"]), _ymd(row["end_date"])
        weekly.append((row["service_id"], start, end, days))
        bounds += [start, end]
    added: dict[date, set[str]] = {}
    removed: dict[date, set[str]] = {}
    for row in calendar_dates.to_dict("records") if len(calendar_dates) else []:
        day = _ymd(row["date"])
        bounds.append(day)
        target = added if str(row["exception_type"]) == "1" else removed
        target.setdefault(day, set()).add(row["service_id"])
    if not bounds:
        return None, set(), 0
    best: tuple[date | None, set[str], int] = (None, set(), -1)
    day = min(bounds)
    last = max(bounds)
    while day <= last:
        if day.weekday() in (1, 2, 3):
            running = {sid for sid, s, e, days in weekly if s <= day <= e and day.weekday() in days}
            running = (running | added.get(day, set())) - removed.get(day, set())
            n = int(trips_per_service.reindex(sorted(running)).fillna(0).sum())
            if n > best[2]:
                best = (day, running, n)
        day += timedelta(days=1)
    if best[0] is None:
        return None, set(), 0
    return best


def clock_seconds(text: Any) -> float:
    """``"HH:MM:SS"`` (hours may pass 24) as seconds; NaN when blank or unreadable."""
    parts = str(text if text is not None else "").strip().split(":")
    if len(parts) != 3:
        return math.nan
    try:
        h, m, s = (float(x) for x in parts)
    except ValueError:
        return math.nan
    out = h * 3600 + m * 60 + s
    return out if math.isfinite(out) else math.nan


def frequency_rows(
    freq: pd.DataFrame, trip_ids: set[str] | None = None
) -> tuple[list[tuple[str, float, float, float]], int]:
    """Readable ``frequencies.txt`` rows as ``(trip_id, start s, end s, headway s)``.

    ``headway_secs`` is read as a number (``"600"`` and ``"600.0"`` both work). A row
    with a blank or unreadable time or headway, a headway of zero or less, or an end
    not after its start is skipped, not fatal: the second value is how many were
    skipped. Rows for trips outside ``trip_ids`` (when given) are ignored, not counted.
    """
    rows: list[tuple[str, float, float, float]] = []
    skipped = 0
    if freq is None or not len(freq):
        return rows, 0
    for r in freq.to_dict("records"):
        trip = str(r.get("trip_id", "")).strip()
        if trip_ids is not None and trip not in trip_ids:
            continue
        s0, s1 = clock_seconds(r.get("start_time")), clock_seconds(r.get("end_time"))
        try:
            hw = float(str(r.get("headway_secs", "")).strip())
        except ValueError:
            hw = math.nan
        readable = math.isfinite(s0) and math.isfinite(s1) and math.isfinite(hw)
        if readable and hw > 0 and s1 > s0:
            rows.append((trip, s0, s1, hw))
        else:
            skipped += 1
    return rows, skipped


def read_gtfs(members: Mapping[str, bytes]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Count each stop's departures on the feed's representative weekday.

    Args:
        members: Feed table name (for example ``"stop_times.txt"``) to its bytes.

    Returns:
        A stop table (``stop_id``, ``lat``, ``lon``, ``location_type``,
        ``parent_station``, ``stop_name``, ``route_groups``, ``weekday_departures``) and a note
        with the day chosen, its trips and the dates the feed covers.
    """
    stops = _table(members, "stops.txt")
    routes = _table(members, "routes.txt")
    trips = _table(members, "trips.txt")
    calendar = _table(members, "calendar.txt")
    calendar_dates = _table(members, "calendar_dates.txt")
    day, services, n_trips = representative_weekday(calendar, calendar_dates, trips)
    active = trips[trips["service_id"].isin(services)] if len(trips) else trips
    usecols = ["trip_id", "stop_id", "stop_sequence", "pickup_type"]
    st = pd.read_csv(
        io.BytesIO(members.get("stop_times.txt", b"")),
        dtype={c: str for c in ("trip_id", "stop_id", "pickup_type")},
        keep_default_na=False,
        encoding="utf-8-sig",
        usecols=lambda c: c.strip() in usecols,
    )
    st.columns = [c.strip() for c in st.columns]
    if "pickup_type" not in st.columns:
        st["pickup_type"] = ""
    st = st[st["trip_id"].isin(set(active["trip_id"]))].copy()
    for col in ("trip_id", "stop_id"):
        st[col] = st[col].astype(str).str.strip()
    st["seq"] = pd.to_numeric(st["stop_sequence"], errors="coerce")
    last = st.groupby("trip_id")["seq"].transform("max")
    st = st[(st["seq"] < last) & (st["pickup_type"].astype(str).str.strip() != "1")]
    runs = pd.Series(1.0, index=pd.Index(sorted(set(st["trip_id"]))))
    freq_rows, freq_skipped = frequency_rows(_table(members, "frequencies.txt"), set(runs.index))
    if freq_rows:
        n = [math.ceil((s1 - s0) / hw) for _, s0, s1, hw in freq_rows]
        per_trip = pd.Series(n, index=[r[0] for r in freq_rows], dtype=float).groupby(level=0).sum()
        runs.loc[per_trip.index] = per_trip
    st["w"] = st["trip_id"].map(runs).fillna(1.0)
    deps = st.groupby("stop_id")["w"].sum()
    route_type = dict(zip(routes.get("route_id", []), routes.get("route_type", []), strict=False))
    trip_group = active.set_index("trip_id")["route_id"].map(
        lambda r: route_group(route_type.get(r, ""))
    )
    st["group"] = st["trip_id"].map(trip_group)
    groups = st.groupby("stop_id")["group"].agg(lambda g: ";".join(sorted(set(g.dropna()))))
    out = pd.DataFrame(
        {
            "stop_id": stops["stop_id"],
            "lat": pd.to_numeric(stops["stop_lat"], errors="coerce"),
            "lon": pd.to_numeric(stops["stop_lon"], errors="coerce"),
            "location_type": stops.get("location_type", pd.Series("", index=stops.index)),
            "parent_station": stops.get("parent_station", pd.Series("", index=stops.index)),
            "stop_name": stops.get("stop_name", pd.Series("", index=stops.index)),
        }
    )
    out["weekday_departures"] = out["stop_id"].map(deps).fillna(0.0)
    out["route_groups"] = out["stop_id"].map(groups).fillna("")
    child = out[out["parent_station"] != ""]
    if len(child):
        add = child.groupby("parent_station")["weekday_departures"].sum()
        grp = child.groupby("parent_station")["route_groups"].agg(
            lambda g: ";".join(sorted({x for v in g for x in v.split(";") if x}))
        )
        is_parent = out["stop_id"].isin(add.index)
        out.loc[is_parent, "weekday_departures"] += out.loc[is_parent, "stop_id"].map(add)
        out.loc[is_parent, "route_groups"] = out.loc[is_parent, "stop_id"].map(grp)
    out["weekday_departures"] = out["weekday_departures"].round().astype("int64")
    out = out.dropna(subset=["lat", "lon"]).sort_values("stop_id").reset_index(drop=True)
    agency = _table(members, "agency.txt")
    names = sorted(set(agency.get("agency_name", [])))
    bounds = [
        *(calendar.get("start_date", [])),
        *(calendar.get("end_date", [])),
        *(calendar_dates.get("date", [])),
    ]
    note = {
        "agency_names": names,
        "representative_date": day.isoformat() if day else None,
        "trips_on_date": n_trips,
        "valid_from": _ymd(min(bounds)).isoformat() if bounds else None,
        "valid_to": _ymd(max(bounds)).isoformat() if bounds else None,
        "stops": len(out),
    }
    if freq_skipped:
        note["frequencies_skipped"] = freq_skipped
    info = _table(members, "feed_info.txt")
    if len(info):
        first = info.iloc[0]
        for col in ("feed_publisher_name", "feed_publisher_url", "feed_version"):
            if str(first.get(col, "")):
                note[col] = str(first[col])
        for col in ("feed_start_date", "feed_end_date"):
            if re.fullmatch(r"\d{8}", str(first.get(col, ""))):
                note[col] = _ymd(str(first[col])).isoformat()
    return out, note


def _https(url: str) -> str:
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


def _read_members(client: HttpClient, url: str) -> tuple[dict[str, bytes], str]:
    """Read the needed feed tables: by range requests when possible, else the whole zip."""
    try:
        listing = client.remote_zip_members(url)
        names = {n.rsplit("/", 1)[-1]: n for n in listing}
        out = {m: client.read_zip_member(url, names[m]) for m in GTFS_MEMBERS if m in names}
        return out, "range"
    except (FetchError, OSError, ValueError, KeyError):
        pass
    data = client.get_bytes(url)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = {n.rsplit("/", 1)[-1]: n for n in zf.namelist()}
        return {m: zf.read(names[m]) for m in GTFS_MEMBERS if m in names}, "whole"


def fetch_gtfs_feed(
    feed: Mapping[str, str],
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Count one feed's departures per stop; cache the stop table and a note.

    Tries the agency's address (over https), then the catalog's mirror. If both
    fail, the note records the error and no stop table is written.

    Returns:
        The stop table (or ``None`` when the feed could not be read) and the note.
    """
    mdb = str(feed["mdb_source_id"])
    folder = Path(cache) / "transit"
    stops_path = folder / f"gtfs_{mdb}_stops.csv.gz"
    note_path = folder / f"gtfs_{mdb}_note.json"
    if note_path.exists():
        note = json.loads(note_path.read_text(encoding="utf-8"))
        table = _read_feed_stops(stops_path) if stops_path.exists() else None
        return table, note
    client = client or HttpClient()
    note: dict[str, Any] = {
        "schedule_feed": mdb,
        "provider": feed.get("provider", ""),
        "catalog_status": feed.get("status", "") or "active",
        "licence_url": feed.get("urls.license", ""),
        "fetched_on": date.today().isoformat(),
    }
    errors = []
    table = None
    for label, url in (
        ("agency", feed.get("urls.direct_download", "")),
        ("mirror", feed.get("urls.latest", "")),
    ):
        if not url:
            continue
        try:
            members, how = _read_members(client, _https(url))
            if "stop_times.txt" not in members or "stops.txt" not in members:
                raise ValueError("the zip lacks stops.txt or stop_times.txt")
            table, info = read_gtfs(members)
            note.update(info, status="ok", address=label, url=_https(url), read=how)
            break
        except (FetchError, OSError, ValueError, KeyError, zipfile.BadZipFile) as err:
            errors.append(f"{label}: {type(err).__name__}: {str(err)[:200]}")
    if table is None:
        note.update(status="unreachable", errors=errors)
    else:
        if errors:
            note["errors"] = errors
        _write_gz(table, stops_path)
    folder.mkdir(parents=True, exist_ok=True)
    note_path.write_text(json.dumps(note, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if manifest is not None and table is not None:
        source = get_source(SOURCE_GTFS)
        mf.record(
            mf.entry_for_file(
                source.id,
                note["url"],
                stops_path,
                note.get("representative_date") or "",
                root=mf.ROOT,
            ),
            manifest,
        )
    return table, note


def _read_feed_stops(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, compression="gzip")
    frame["lat"] = frame["lat"].astype(float)
    frame["lon"] = frame["lon"].astype(float)
    frame["weekday_departures"] = frame["weekday_departures"].astype("int64")
    return frame


# --------------------------------------------------------------------------- assembly


@dataclass
class TransitInputs:
    """Everything the transit tables are built from, already read."""

    ntm: pd.DataFrame
    agencies: pd.DataFrame
    feeds: list[tuple[dict[str, Any], pd.DataFrame | None]] = field(default_factory=list)
    files: list[tuple[str, Path]] = field(default_factory=list)


def fetch_transit(
    state: str,
    county_shapes: Sequence[Any],
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> TransitInputs:
    """Fetch (or reuse) the stops, agency names, feed catalog and every keyless feed."""
    fips = to_state_fips(state)
    client = client or HttpClient()
    ntm = fetch_ntm_stops(state, state_bbox(county_shapes), cache, client=client, manifest=manifest)
    agencies = fetch_ntm_agencies(
        state, sorted(set(ntm["ntd_id"])), cache, client=client, manifest=manifest
    )
    catalog = select_feeds(fetch_feed_catalog(state, cache, client=client, manifest=manifest))
    atlas = fetch_atlas_catalog(state, cache, client=client, manifest=manifest)
    if len(atlas):
        catalog = pd.concat([catalog, atlas], ignore_index=True)
    feeds = [
        fetch_gtfs_feed(row, cache, client=client, manifest=manifest)
        for row in catalog.to_dict("records")
    ]
    folder = Path(cache) / "transit"
    files = [
        (SOURCE_STOPS, folder / f"ntm_stops_{fips}.csv.gz"),
        (SOURCE_AGENCIES, folder / f"ntm_agencies_{fips}.csv"),
        (SOURCE_CATALOG, folder / f"gtfs_catalog_{fips}.csv"),
        (SOURCE_ATLAS, folder / f"gtfs_atlas_{fips}.csv"),
    ]
    for table, note in feeds:
        mdb = note["schedule_feed"]
        files.append((SOURCE_GTFS, folder / f"gtfs_{mdb}_note.json"))
        if table is not None:
            files.append((SOURCE_GTFS, folder / f"gtfs_{mdb}_stops.csv.gz"))
    return TransitInputs(
        ntm=ntm,
        agencies=agencies,
        feeds=[(note, table) for table, note in feeds],
        files=[(s, p) for s, p in files if p.exists()],
    )


def name_words(text: object) -> set[str]:
    """The distinctive words of an agency name (lower case, 4+ letters, not generic)."""
    words = re.findall(r"[a-z]+", str(text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in GENERIC_NAME_WORDS}


def _feed_words(note: Mapping[str, Any]) -> set[str]:
    return name_words(note.get("provider", "")) | name_words(note.get("agency_names", ""))


def _stop_name_key(text: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _reverse_ok(
    group: pd.DataFrame,
    table: pd.DataFrame,
    bbox: tuple[float, float, float, float],
    min_share: float,
    min_similarity: float,
) -> bool:
    """The check from the schedule feed's side, for a pairing by place.

    True when at least ``min_share`` of the schedule feed's stops inside the
    state's box lie within 50 m of the map feed's stops (the two feeds cover the
    same network), or, where both tables carry stop names, when at least half of
    the map feed's stops have a schedule stop within 50 m whose name is at least
    ``min_similarity`` alike (difflib ratio). A big agency's feed fails it against
    a small operator's few stops that happen to sit at its stops.
    """
    from difflib import SequenceMatcher

    s, w, n, e = bbox
    inside = table[
        (table["lat"] >= s) & (table["lat"] <= n) & (table["lon"] >= w) & (table["lon"] <= e)
    ]
    if len(inside):
        _, miles = tf.nearest_stop(
            inside["lat"].to_numpy(),
            inside["lon"].to_numpy(),
            group["lat"].to_numpy(),
            group["lon"].to_numpy(),
        )
        if (miles <= MATCH_NEAR_MILES).mean() >= min_share:
            return True
    if "stop_name" not in table.columns or "stop_name" not in group.columns:
        return False
    idx, miles = tf.nearest_stop(
        group["lat"].to_numpy(),
        group["lon"].to_numpy(),
        table["lat"].to_numpy(),
        table["lon"].to_numpy(),
    )
    names = table["stop_name"].to_numpy()
    alike = [
        near
        and SequenceMatcher(None, _stop_name_key(a), _stop_name_key(names[k])).ratio()
        >= min_similarity
        for a, k, near in zip(group["stop_name"], idx, miles <= MATCH_NEAR_MILES, strict=True)
    ]
    return float(np.mean(alike)) >= FEED_MATCH_MIN_SHARE if alike else False


def _pair_numbers() -> tuple[float, float]:
    from visionhousing.ledger import load_ledger

    ledger = load_ledger()
    return float(ledger[PAIR_REVERSE_ID].central), float(ledger[PAIR_NAME_ID].central)


def _pair_feeds(
    ntm: pd.DataFrame,
    feeds: Sequence[tuple[dict[str, Any], pd.DataFrame | None]],
    agencies: pd.DataFrame | None = None,
    *,
    reverse_min_share: float | None = None,
    name_similarity: float | None = None,
) -> dict[str, tuple[int, str]]:
    """For each NTM feed, the position of the schedule feed it matches and how (``id``|``place``).

    First by stop id: the schedule feed with the most same-id stops within
    200 m, if they are at least half the NTM feed's stops (``id``). Agencies
    sometimes renumber their stops, so a feed with no such match is then paired
    by place (``place``), only when all three hold:

    1. the schedule feed's agency name shares a distinctive word with the NTM
       agency's name (:data:`GENERIC_NAME_WORDS` are not distinctive: state
       names, "transit", "authority", "valley", "port" and the like);
    2. at least half the NTM feed's stops have a schedule stop within 50 m;
    3. the reverse check (:func:`_reverse_ok`): at least
       ``parking.transit_feed_pair_reverse_min_share`` of the schedule feed's
       in-state stops lie within 50 m of the NTM feed's stops, or the stop names
       agree (``parking.transit_feed_pair_name_similarity``).

    So a town shuttle or an airport operator is never read from a big agency's
    timetable at shared stops. Among schedule feeds passing all three, the one
    with the most stops within 50 m wins.
    """
    if reverse_min_share is None or name_similarity is None:
        rev, sim = _pair_numbers()
        reverse_min_share = rev if reverse_min_share is None else reverse_min_share
        name_similarity = sim if name_similarity is None else name_similarity
    names = (
        {}
        if agencies is None
        else dict(zip(agencies["ntd_id"].astype(str), agencies["agency"], strict=False))
    )
    pad = 0.01
    bbox = (
        (float(ntm["lat"].min()) - pad, float(ntm["lon"].min()) - pad,
         float(ntm["lat"].max()) + pad, float(ntm["lon"].max()) + pad)
        if len(ntm) else (0.0, 0.0, 0.0, 0.0)
    )  # fmt: skip
    pairs: dict[str, tuple[int, str]] = {}
    for feed_id, group in ntm.groupby("feed_id", sort=True):
        best, best_n = -1, 0
        for k, (_, table) in enumerate(feeds):
            if table is None:
                continue
            m = group.merge(table[["stop_id", "lat", "lon"]], on="stop_id", suffixes=("", "_g"))
            if m.empty:
                continue
            close = (
                tf.great_circle_miles(m["lat"], m["lon"], m["lat_g"], m["lon_g"]) <= MATCH_ID_MILES
            )
            n = int(close.sum())
            if n > best_n:
                best, best_n = k, n
        if best >= 0 and best_n >= FEED_MATCH_MIN_SHARE * len(group):
            pairs[str(feed_id)] = (best, "id")
            continue
        words = name_words(names.get(str(group["ntd_id"].iloc[0]), ""))
        if not words:
            continue
        best, best_n = -1, 0
        for k, (note, table) in enumerate(feeds):
            if table is None or table.empty or not (words & _feed_words(note)):
                continue
            _, miles = tf.nearest_stop(
                group["lat"].to_numpy(),
                group["lon"].to_numpy(),
                table["lat"].to_numpy(),
                table["lon"].to_numpy(),
            )
            n = int((miles <= MATCH_NEAR_MILES).sum())
            if (
                n > best_n
                and n >= FEED_MATCH_MIN_SHARE * len(group)
                and _reverse_ok(group, table, bbox, reverse_min_share, name_similarity)
            ):
                best, best_n = k, n
        if best >= 0:
            pairs[str(feed_id)] = (best, "place")
    return pairs


def _match_stops(group: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """Departures and route groups for one NTM feed's stops from its schedule feed."""
    m = group[["stop_id", "lat", "lon"]].merge(
        table[["stop_id", "lat", "lon", "weekday_departures", "route_groups"]],
        on="stop_id",
        how="left",
        suffixes=("", "_g"),
    )
    ok = (
        tf.great_circle_miles(m["lat"], m["lon"], m["lat_g"].fillna(0), m["lon_g"].fillna(0))
        <= MATCH_ID_MILES
    ) & m["lat_g"].notna()
    deps = m["weekday_departures"].where(ok)
    grp = m["route_groups"].where(ok)
    todo = np.flatnonzero(~ok.to_numpy())
    if len(todo):
        idx, miles = tf.nearest_stop(
            m["lat"].to_numpy()[todo],
            m["lon"].to_numpy()[todo],
            table["lat"].to_numpy(),
            table["lon"].to_numpy(),
        )
        near = miles <= MATCH_NEAR_MILES
        deps.iloc[todo[near]] = table["weekday_departures"].to_numpy()[idx[near]]
        grp.iloc[todo[near]] = table["route_groups"].to_numpy()[idx[near]]
    return pd.DataFrame(
        {"weekday_departures": deps.to_numpy(), "route_groups": grp.fillna("").to_numpy()},
        index=group.index,
    )


def _state_tracts(
    ntm: pd.DataFrame, tract_shapes: Sequence[Any], blocks: pd.DataFrame | None
) -> np.ndarray:
    lat, lon = ntm["lat"].to_numpy(), ntm["lon"].to_numpy()
    tract = tf.points_in_polygons(lat, lon, ((s.geoid, s.geometry) for s in tract_shapes))
    if blocks is not None and len(blocks):
        miss = np.flatnonzero(tract == "")
        if len(miss):
            idx, miles = tf.nearest_stop(
                lat[miss], lon[miss], blocks["lat"].to_numpy(), blocks["lon"].to_numpy()
            )
            near = miles <= STATE_EDGE_MILES
            tract[miss[near]] = blocks["tract"].astype(str).to_numpy()[idx[near]]
    return tract


def build_stop_table(
    transit: TransitInputs,
    tract_shapes: Sequence[Any],
    rule: tf.TransitRule,
    *,
    blocks: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the pack's ``transit_stops`` and ``transit_feeds`` tables (pure: no file access).

    Steps: drop entrances and other non-boarding points; drop platforms whose
    station is listed (the station stands for them); keep stops inside the
    state's tracts; class each stop's mode from its route types; take bus
    departures from the matched schedule feed; flag the major stops.

    Args:
        transit: The fetched inputs.
        tract_shapes: The state's tracts (``geoid`` and full-detail ``geometry``).
        rule: The major-stop rule's numbers.
        blocks: Optional blocks (``tract``, ``lat``, ``lon``) to place stops on a
            shoreline just outside every tract outline.
    """
    ntm = transit.ntm.copy()
    ntm = ntm[~ntm["location_type"].isin(["2", "3", "4"])]
    listed = set(zip(ntm["feed_id"], ntm["stop_id"], strict=True))
    has_station = [
        (f, p) in listed for f, p in zip(ntm["feed_id"], ntm["parent_station"], strict=True)
    ]
    ntm = ntm[~np.asarray(has_station, dtype=bool)].reset_index(drop=True)
    feeds = transit.feeds
    pairs = _pair_feeds(ntm, feeds, transit.agencies)
    ntm["tract"] = _state_tracts(ntm, tract_shapes, blocks)
    ntm = ntm[ntm["tract"] != ""].reset_index(drop=True)
    ntm["county"] = ntm["tract"].str[:5]
    ntm["weekday_departures"] = np.nan
    ntm["gtfs_groups"] = ""
    ntm["stale"] = False
    for feed_id, group in ntm.groupby("feed_id", sort=True):
        if feed_id in pairs:
            note, table = feeds[pairs[feed_id][0]]
            matched = _match_stops(group, table)
            ntm.loc[group.index, "weekday_departures"] = matched["weekday_departures"]
            ntm.loc[group.index, "gtfs_groups"] = matched["route_groups"]
            ntm.loc[group.index, "stale"] = is_stale(note)
    groups = [
        _groups(t) or {g for g in str(f).split(";") if g}
        for t, f in zip(ntm["stop_type"], ntm["gtfs_groups"], strict=True)
    ]
    ntm["mode"] = [_mode(g) for g in groups]
    bus = ntm["mode"] == "bus"
    stale = bus & ntm["stale"].astype(bool) & ntm["weekday_departures"].notna()
    counted = bus & ntm["weekday_departures"].notna() & ~stale
    ntm["frequency"] = np.select(
        [ntm["mode"] == "rail", counted, stale, bus],
        ["mode_only", "counted", "stale", "unknown"],
        "not_major",
    )
    ntm["major"] = (
        (ntm["mode"] == "rail") | (counted & (ntm["weekday_departures"] >= rule.min_bus_departures))
    ).astype("int64")
    ntm["route_types"] = ntm["stop_type"].str.replace('"', "", regex=False)
    ntm["weekday_departures"] = ntm["weekday_departures"].round().astype("Int64")
    ntm["stop_name"] = ntm["stop_name"].where(ntm["major"] == 1, "")
    ntm["stop_id"] = ntm["feed_id"] + ":" + ntm["stop_id"]
    stops = ntm[list(STOP_COLUMNS)].sort_values("stop_id").reset_index(drop=True)
    return stops, _feed_table(stops, transit, pairs)


def is_stale(note: Mapping[str, Any]) -> bool:
    """True when a feed's schedule had already ended on the day it was fetched.

    Its counts are kept for reference but do not make a stop major: an expired
    schedule says little about today's service.
    """
    end, fetched = note.get("valid_to"), note.get("fetched_on")
    return bool(end and fetched and str(end) < str(fetched))


def _feed_table(
    stops: pd.DataFrame, transit: TransitInputs, pairs: Mapping[str, int]
) -> pd.DataFrame:
    names = dict(zip(transit.agencies["ntd_id"], transit.agencies["agency"], strict=False))
    rows = []
    used = {k for k, _ in pairs.values()}
    for feed_id, g in stops.groupby("feed_id", sort=True):
        k, how = pairs.get(feed_id, (None, ""))
        note = transit.feeds[k][0] if k is not None else {}
        bus = g["mode"] == "bus"
        n_bus, n_counted = int(bus.sum()), int((g["frequency"] == "counted").sum())
        if n_bus == 0:
            status = "not needed (no bus stops)"
        elif k is None:
            status = "unknown: no keyless schedule feed matched"
        elif is_stale(note):
            status = f"unknown: the only keyless schedule ended {note.get('valid_to')}"
        elif n_counted < n_bus:
            status = f"counted for {n_counted / n_bus:.0%} of bus stops"
        else:
            status = "counted"
        rows.append(
            {
                "feed_id": feed_id,
                "ntd_id": g["ntd_id"].iloc[0],
                "agency": names.get(g["ntd_id"].iloc[0], ""),
                "stops": len(g),
                "rail_stops": int((g["mode"] == "rail").sum()),
                "bus_stops": n_bus,
                "bus_stops_counted": n_counted,
                "major_bus_stops": int((bus & (g["major"] == 1)).sum()),
                "frequency": status,
                "schedule_feed": note.get("schedule_feed", ""),
                "paired_by": how,
                "schedule_provider": note.get("provider", ""),
                "schedule_date": note.get("representative_date") or "",
                "schedule_valid_from": note.get("valid_from") or "",
                "schedule_valid_to": note.get("valid_to") or "",
            }
        )
    for k, (note, _) in enumerate(transit.feeds):
        if k in used:
            continue
        rows.append(
            {
                "feed_id": "",
                "ntd_id": "",
                "agency": "",
                "stops": 0,
                "rail_stops": 0,
                "bus_stops": 0,
                "bus_stops_counted": 0,
                "major_bus_stops": 0,
                "frequency": (
                    "schedule feed unreachable"
                    if note.get("status") != "ok"
                    else "schedule feed matched no National Transit Map stops in the state"
                    + (f" (schedule ended {note.get('valid_to')})" if is_stale(note) else "")
                ),
                "schedule_feed": note.get("schedule_feed", ""),
                "paired_by": "",
                "schedule_provider": note.get("provider", ""),
                "schedule_date": note.get("representative_date") or "",
                "schedule_valid_from": note.get("valid_from") or "",
                "schedule_valid_to": note.get("valid_to") or "",
            }
        )
    return pd.DataFrame(rows, columns=list(FEED_COLUMNS))
