"""Massachusetts intake: MBTA Communities (Section 3A) zoning and the state ADU survey.

Plain-language summary
----------------------
Massachusetts publishes three kinds of data no other state in the pack has.
This module downloads them and reads them into plain tables; the pack tables
are made from them in :mod:`visionhousing.features.ma_intake`.

1. **The MBTA Communities (3A) District Atlas** (MassGIS, for the Executive
   Office of Housing and Livable Communities, HLC): one outline per zoning
   district that a town adopted to meet Section 3A of the Zoning Act
   (multi-family housing allowed as of right near transit), with the
   district's unit capacity, density, parking, height and the date the town
   submitted it. Only districts HLC has found compliant are in the atlas. Read
   from the MassGIS map service with the outlines simplified on the server
   (:func:`fetch_3a_districts`, :func:`parse_3a_districts`).
2. **The 3A lists** HLC posts as CSV files: each MBTA community's compliance
   status and deadline (:func:`parse_compliance_status`), its category and
   required capacity (:func:`parse_categories`), and HLC's tracker of
   housing developments it links to 3A zoning
   (:func:`parse_development_tracker`).
3. **The ADU survey** of municipal building officials: ADU applications,
   approvals (building permits issued) and certificates of occupancy per
   town, split detached / attached, for 2025 and the first half of 2026
   (:func:`parse_adu_survey`). Towns answered different waves, so the 2025
   period differs by town; each row carries its cohort.

Where the files come from
-------------------------
The atlas is read by script from the MassGIS map service. The HLC lists and
the ADU survey are CSV files on www.mass.gov, which refuses scripted requests
(HTTP 403). We respect that: :func:`fetch_copy` asks the publisher's address
once and, when refused, reads a copy a person downloaded in a browser and
saved into ``data/raw/local/ma/`` under the name in
``assumptions/ma_intake.yaml``. Each table records the edition it holds
(``as_of``) and the route (``publisher`` or ``hand_saved``). A hand-saved copy
records its own edition, never the one the settings list for the publisher's
address (the person may have saved a newer one): from a small file the person
fills next to it (``<file>.edition.yaml`` with ``as_of: YYYY-MM-DD``), else
from an "as of" date written in the file itself, else it is left unknown
(empty) and the build notes say so. Without a copy,
the 3A status table is built from the atlas alone and the ADU survey table is
left out. Every downloaded file is recorded in ``data/manifest.json`` with its
checksum.

Terms: the atlas says "Data are public" and is meant for policy analysis and
research; it is not a legal record of permitted uses (the compliance letter
is). The HLC lists and survey are public records posted for download.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.registry import get_source

__all__ = [
    "ATLAS_FIELDS",
    "EDITION_SUFFIX",
    "SETTINGS_PATH",
    "Copy",
    "edition_in_text",
    "fetch_3a_districts",
    "fetch_copy",
    "load_settings",
    "muni_key",
    "parse_3a_districts",
    "parse_adu_survey",
    "parse_categories",
    "parse_compliance_status",
    "parse_development_tracker",
    "read_edition",
    "survey_totals_check",
]

ROOT = Path(__file__).resolve().parents[4]
SETTINGS_PATH = ROOT / "assumptions" / "ma_intake.yaml"
FOLDER = ("local", "ma")
"""Cache folder (under ``data/raw``) for every Massachusetts intake file."""

ATLAS_FIELDS: dict[str, str] = {
    "ZONECOD": "zone_code",
    "MUNI_ID": "muni_id",
    "MUNI": "municipality",
    "ZONENAM": "zone_name",
    "ZONABBR": "zone_abbr",
    "DATE_": "submitted_on",
    "ISOVRLY": "overlay",
    "STATUS": "status",
    "MFUC_DIST": "capacity_district",
    "MFUC_CLN": "capacity_cleaned",
    "MFUC_PARC": "capacity_parcels",
    "ACRS_CM": "acres",
    "DNDN_CM": "density_denominator_acres",
    "DUAC_CM": "units_per_acre",
    "DUC_HMB": "capacity_station_area",
    "PRKNPDU": "parking_per_unit",
    "STORIES": "max_stories",
    "LOTCOV": "max_lot_coverage_pct",
    "FAR": "max_far",
    "AREAPDU": "lot_area_per_unit_sqft",
    "MXDUPAC": "max_units_per_acre",
    "DU_CAP": "district_unit_cap",
    "OSRQ_PC": "open_space_share",
    "NOTES": "notes",
}
"""Atlas attribute -> our column name (atlas field aliases in docs/modules/ma_intake.md)."""


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Read ``assumptions/ma_intake.yaml``."""
    return yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8"))


def cache_dir(cache: Path) -> Path:
    """The folder that holds the Massachusetts intake files in the raw-data cache."""
    return Path(cache).joinpath(*FOLDER)


# --------------------------------------------------------------------------- names


_TYPE_WORDS = re.compile(r"\s+(town city|city|town)$", flags=re.IGNORECASE)


def muni_key(name: object, overrides: dict[str, str] | None = None) -> str:
    """One spelling of a Massachusetts municipality name, for joining tables.

    Lower case, without the Census type word ("town", "city", "Town city"),
    without spaces and punctuation. ``overrides`` maps a name as the intake
    files write it to the Census name without its type word.
    """
    text = str(name or "").strip()
    if overrides and text in overrides:
        text = overrides[text]
    text = _TYPE_WORDS.sub("", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


# --------------------------------------------------------------------------- copies


@dataclass(frozen=True)
class Copy:
    """The copy of an intake file a build read: where it is and where it came from.

    ``edition_from`` says how ``as_of`` is known: ``publisher`` (the address the
    settings list for that edition), ``sidecar`` (the date a person wrote in
    ``<file>.edition.yaml``), ``file`` (an "as of" date in the file itself) or
    ``unknown`` (``as_of`` is empty).
    """

    path: Path
    route: str
    url: str
    as_of: str
    fetched_on: str
    edition_from: str = "publisher"


EDITION_SUFFIX = ".edition.yaml"
"""Name ending of the small file a person fills next to a hand-saved copy."""

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
_AS_OF = re.compile(
    r"as\s+of\s+(?:"
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<m>\d{1,2})[-/.](?P<d>\d{1,2})[-/.](?P<y>\d{4}|\d{2})"
    r"|(?P<month>[a-z]+)\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})"
    r")",
    flags=re.IGNORECASE,
)


def edition_in_text(raw: bytes | str, lines: int = 5) -> str:
    """The "as of" date written at the top of a published file, as ISO text, or "".

    HLC writes the edition into titles such as "Compliance Status Sheet as of
    11-24-26". Only the title rows and the header row are searched (lines up
    to and including the first with two or more filled cells, at most
    ``lines``), so a date inside the data is never taken for the edition.
    """
    import csv

    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    top: list[str] = []
    for line in text.splitlines()[:lines]:
        top.append(line)
        cells = next(csv.reader([line]), [])
        if sum(1 for c in cells if c.strip()) >= 2:
            break  # the header row: data follow
    head = "\n".join(top)
    for m in _AS_OF.finditer(head):
        try:
            if m["iso"]:
                return date.fromisoformat(m["iso"]).isoformat()
            if m["m"]:
                year = int(m["y"]) + (2000 if len(m["y"]) == 2 else 0)
                return date(year, int(m["m"]), int(m["d"])).isoformat()
            month = _MONTHS.get(m["month"].lower())
            if month:
                return date(int(m["year"]), month, int(m["day"])).isoformat()
        except ValueError:
            continue
    return ""


def read_edition(path: Path) -> dict[str, str] | None:
    """What a person wrote in ``<file>.edition.yaml`` next to a hand-saved copy, or None.

    The file holds ``as_of: YYYY-MM-DD`` (the date the publisher gives the
    edition) and, optionally, ``url:`` (the address it was saved from).

    Raises:
        ValueError: The file exists but ``as_of`` is not a date.
    """
    side = path.with_name(path.name + EDITION_SUFFIX)
    if not side.is_file():
        return None
    data = yaml.safe_load(side.read_text(encoding="utf-8")) or {}
    value = data.get("as_of")
    try:
        as_of = (value if isinstance(value, date) else date.fromisoformat(str(value))).isoformat()
    except ValueError as err:
        raise ValueError(f"{side.name}: as_of must be a date (YYYY-MM-DD), got {value!r}") from err
    return {"as_of": as_of, "url": str(data.get("url") or "")}


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".source.json")


def _read_sidecar(path: Path) -> Copy | None:
    """The copy recorded for ``path``, unless the file or a person's edition note changed."""
    side = _sidecar(path)
    if not side.is_file():
        return None
    d = json.loads(side.read_text(encoding="utf-8"))
    if d["route"] == "hand_saved":
        edition = path.with_name(path.name + EDITION_SUFFIX)
        note = edition.stat().st_mtime if edition.is_file() else None
        if d.get("sha256") != _sha256(path) or d.get("edition_mtime") != note:
            return None  # replaced by hand or edition note added: read it afresh
    return Copy(
        path,
        d["route"],
        d["url"],
        d["as_of"],
        d["fetched_on"],
        d.get("edition_from", "publisher"),
    )


def _write_sidecar(copy: Copy) -> None:
    data: dict[str, Any] = {
        "route": copy.route,
        "url": copy.url,
        "as_of": copy.as_of,
        "fetched_on": copy.fetched_on,
        "edition_from": copy.edition_from,
        "sha256": _sha256(copy.path),
    }
    if copy.route == "hand_saved":
        edition = copy.path.with_name(copy.path.name + EDITION_SUFFIX)
        data["edition_mtime"] = edition.stat().st_mtime if edition.is_file() else None
    _sidecar(copy.path).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", "utf-8")


def _hand_saved(dest: Path, spec: dict[str, Any]) -> Copy:
    """A hand-saved copy with its own edition: the person's note, else the file, else unknown."""
    saved = date.fromtimestamp(dest.stat().st_mtime).isoformat()
    first = spec["copies"][0] if spec.get("copies") else {"url": ""}
    note = read_edition(dest)
    if note is not None:
        return Copy(
            dest, "hand_saved", note["url"] or str(first["url"]), note["as_of"], saved, "sidecar"
        )
    found = edition_in_text(dest.read_bytes())
    if found:
        return Copy(dest, "hand_saved", str(first["url"]), found, saved, "file")
    return Copy(dest, "hand_saved", str(first["url"]), "", saved, "unknown")


def _gunzip(data: bytes) -> bytes:
    """The bytes, unpacked when the server sent them gzip-compressed."""
    import gzip

    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def _looks_like_csv(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return bool(head) and not head.startswith((b"<!doctype", b"<html", b"{"))


def fetch_copy(
    name: str,
    cache: Path,
    settings: dict[str, Any] | None = None,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> Copy:
    """Fetch (or reuse) one intake file: the publisher's copy, else a hand-saved one.

    Args:
        name: Key under ``files`` in the settings (for example ``"adu_survey"``).
        cache: The raw-data cache (``data/raw``).
        settings: The parsed settings; default ``assumptions/ma_intake.yaml``.
        client: HTTP client.
        manifest: Download manifest, or ``None``.
        refresh: Ask the publisher again even when the cache has the file.

    Returns:
        The copy read, with its route (``publisher`` or ``hand_saved``) and
        edition. A hand-saved copy's edition comes from ``<file>.edition.yaml``,
        else from the file, else is unknown (empty); never from the
        publisher's listed edition.

    Raises:
        FetchError: The publisher refused or failed and no hand-saved copy is in
            the cache; the message says why and where to save one.
    """
    settings = settings or load_settings()
    spec = settings["files"][name]
    dest = cache_dir(cache) / spec["file"]
    if dest.is_file() and not refresh:
        known = _read_sidecar(dest)
        if known is not None:
            return known
    client = client or HttpClient(timeout=120)
    source = get_source(spec["source"])
    problems: list[str] = []
    for copy in spec.get("copies", []):
        url = str(copy["url"])
        try:
            data = _gunzip(client.get_bytes(url))
        except FetchError as err:
            status = f"HTTP {err.status}" if getattr(err, "status", None) else str(err)[:120]
            problems.append(f"{copy['route']}: {status}")
            continue
        if not _looks_like_csv(data):
            problems.append(f"{copy['route']}: the answer is not a CSV file")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(data)
        part.replace(dest)
        got = Copy(dest, str(copy["route"]), url, str(copy["as_of"]), date.today().isoformat())
        _write_sidecar(got)
        if manifest is not None:
            vintage = f"as of {copy['as_of']}"
            mf.record(mf.entry_for_file(source.id, url, dest, vintage, root=mf.ROOT), manifest)
        return got
    if dest.is_file() and spec.get("hand_saved"):
        got = _hand_saved(dest, spec)
        _write_sidecar(got)
        return got
    where = f"data/raw/local/ma/{spec['file']}"
    raise FetchError(
        f"{name}: {'; '.join(problems) or 'no address listed'}; save the file by hand from "
        f"{spec.get('page', 'the publisher page')} as {where}"
    )


def _read_csv(raw: bytes | str) -> pd.DataFrame:
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("﻿")
    frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def _number(series: pd.Series) -> pd.Series:
    """Numbers written with thousands commas, percent signs or a dash for none."""
    text = series.astype(str).str.strip().str.replace(",", "", regex=False)
    pct = text.str.endswith("%")
    text = text.str.rstrip("%").replace({"-": "", "\u2013": ""})
    out = pd.to_numeric(text, errors="coerce")
    return out.where(~pct, out / 100.0)


def _sum_known(series: pd.Series) -> float:
    """Sum of the filled values; missing (not 0) when every value is blank."""
    return series.sum(min_count=1)


def _yes(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip().str.lower()
    return text.map({"yes": 1, "y": 1, "no": 0, "n": 0}).astype("Int64")


# --------------------------------------------------------------------------- 3A lists


def parse_compliance_status(raw: bytes | str) -> pd.DataFrame:
    """HLC's compliance status sheet: one row per MBTA community.

    Returns:
        ``municipality``, ``compliance_status`` (as written, trimmed),
        ``compliance_details`` (empty for "-"), ``deadline`` (ISO date),
        ``pre_adoption_review`` and ``adopted_zoning`` (1 yes, 0 no).

    Raises:
        ValueError: A column the sheet always has is missing.
    """
    frame = _read_csv(raw)
    want = {
        "Municipality": "municipality",
        "Compliance Status": "compliance_status",
        "Compliance Details": "compliance_details",
        "Compliance Deadlines": "deadline",
        "Submitted for Pre-adoption review (Y/N)": "pre_adoption_review",
        "Adopted zoning intended for 3A compliance": "adopted_zoning",
    }
    missing = [c for c in want if c not in frame.columns]
    if missing:
        raise ValueError(f"compliance status sheet lacks columns: {missing}")
    out = frame[list(want)].rename(columns=want)
    out = out[out["municipality"].str.strip() != ""].copy()
    for col in ("municipality", "compliance_status", "compliance_details"):
        out[col] = out[col].astype(str).str.strip()
    out["compliance_details"] = out["compliance_details"].replace({"-": ""})
    out["deadline"] = pd.to_datetime(out["deadline"].str.strip(), format="%m/%d/%Y").dt.strftime(
        "%Y-%m-%d"
    )
    for col in ("pre_adoption_review", "adopted_zoning"):
        out[col] = _yes(out[col])
    return out.sort_values("municipality").reset_index(drop=True)


def parse_categories(raw: bytes | str) -> pd.DataFrame:
    """HLC's community categories and capacity requirements: one row per MBTA community.

    Returns:
        ``municipality``, ``category``, ``housing_units_2020``,
        ``min_unit_capacity``, ``capacity_share`` (of 2020 homes),
        ``min_land_acres``, ``station_area_acres`` (developable land within
        half a mile of a station; blank where there is none) and
        ``station_area_share`` (share of the district that must lie there).
    """
    frame = _read_csv(raw)
    cols = list(frame.columns)
    if len(cols) < 8 or not cols[0].lower().startswith("community"):
        raise ValueError(f"categories file has unexpected columns: {cols}")
    names = [
        "municipality",
        "category",
        "housing_units_2020",
        "min_unit_capacity",
        "capacity_share",
        "min_land_acres",
        "station_area_acres",
        "station_area_share",
    ]
    out = frame[cols[:8]].copy()
    out.columns = names
    out = out[out["municipality"].str.strip() != ""].copy()
    out["municipality"] = out["municipality"].str.strip()
    out["category"] = out["category"].str.strip()
    for col in names[2:]:
        out[col] = _number(out[col])
    return out.sort_values("municipality").reset_index(drop=True)


def parse_development_tracker(raw: bytes | str) -> pd.DataFrame:
    """HLC's 3A development tracker, summed per municipality.

    Only counts are kept (no developer names or addresses).

    Returns:
        ``municipality``, ``tracker_projects``, ``tracker_units``,
        ``tracker_net_units`` and ``tracker_deed_restricted``.
    """
    frame = _read_csv(raw)
    lower = {c.lower(): c for c in frame.columns}

    def col(*words: str) -> str:
        for key, name in lower.items():
            if all(w in key for w in words):
                return name
        raise ValueError(f"development tracker lacks a column with {words}")

    rows = pd.DataFrame(
        {
            "municipality": frame[col("municipality")].astype(str).str.strip(),
            "tracker_units": _number(frame[col("total units")]),
            "tracker_net_units": _number(frame[col("net units")]),
            "tracker_deed_restricted": _number(frame[col("deed")]),
        }
    )
    rows = rows[rows["municipality"] != ""]
    out = rows.groupby("municipality", as_index=False).agg(
        tracker_projects=("tracker_units", "size"),
        tracker_units=("tracker_units", _sum_known),
        tracker_net_units=("tracker_net_units", _sum_known),
        tracker_deed_restricted=("tracker_deed_restricted", _sum_known),
    )
    return out.sort_values("municipality").reset_index(drop=True)


# --------------------------------------------------------------------------- ADU survey


_ADU_MEASURES = (
    ("Submitted", "applications"),
    ("Approved", "approvals"),
    ("CoO", "certificates_of_occupancy"),
)


def parse_adu_survey(raw: bytes | str, settings: dict[str, Any] | None = None) -> pd.DataFrame:
    """HLC's ADU survey composite: one row per municipality and survey period.

    A town that gave no answer for a period has no row for it (unknown is not
    zero). A town that answered "0" has a row of zeros.

    Returns:
        ``muni_id``, ``municipality``, ``cohort``, ``period_start``,
        ``period_end``, ``answer`` (the survey wave the numbers come from),
        ``applications``, ``approvals``, ``certificates_of_occupancy`` and the
        same three split ``_detached`` and ``_attached``.

    Raises:
        ValueError: A period's column is missing, or a town's answer is not one
            the settings know.
    """
    spec = (settings or load_settings())["adu_survey"]
    frame = _read_csv(raw)
    rows: list[pd.DataFrame] = []
    for period in spec["periods"]:
        suffix, source_col = str(period["suffix"]), str(period["source_column"])
        if source_col not in frame.columns:
            raise ValueError(f"ADU survey lacks column {source_col}")
        answer = frame[source_col].astype(str).str.strip()
        known = set(period["answers"]) | {str(spec["no_answer"]), ""}
        odd = sorted(set(answer) - known)
        if odd:
            raise ValueError(f"ADU survey {source_col}: unknown answers {odd}")
        keep = answer.isin(period["answers"])
        part = pd.DataFrame(
            {
                "muni_id": pd.to_numeric(frame.loc[keep, "muni_id"]).astype(int),
                "municipality": frame.loc[keep, "muni_name"].astype(str).str.strip(),
                "answer": answer[keep],
            }
        )
        meta = part["answer"].map(period["answers"])
        part["cohort"] = meta.map(lambda m: m["cohort"])
        part["period_start"] = meta.map(lambda m: str(m["start"]))
        part["period_end"] = meta.map(lambda m: str(m["end"]))
        for raw_name, name in _ADU_MEASURES:
            for split, label in (("", ""), ("_Detached", "_detached"), ("_Attached", "_attached")):
                column = f"{raw_name}{split}_{suffix}"
                if column not in frame.columns:
                    raise ValueError(f"ADU survey lacks column {column}")
                part[f"{name}{label}"] = _number(frame.loc[keep, column]).astype("Int64")
        rows.append(part)
    order = [
        "muni_id",
        "municipality",
        "cohort",
        "period_start",
        "period_end",
        "answer",
        *[f"{n}{s}" for _, n in _ADU_MEASURES for s in ("", "_detached", "_attached")],
    ]
    out = pd.concat(rows, ignore_index=True)[order]
    return out.sort_values(["muni_id", "period_start", "period_end"]).reset_index(drop=True)


def survey_totals_check(raw: bytes | str, settings: dict[str, Any] | None = None) -> list[str]:
    """Towns whose ``*_Total`` columns differ from the sum of their period columns.

    A plain check on the published file (the two should agree); it returns the
    town names, which the build writes into its notes. The period columns are
    the suffixes listed under ``adu_survey.periods`` in
    ``assumptions/ma_intake.yaml``, so a new survey wave needs only a settings
    line. A town whose period cells are all blank is skipped when its total is
    blank too (unknown, not a disagreement).
    """
    spec = (settings or load_settings())["adu_survey"]
    suffixes = [str(p["suffix"]) for p in spec["periods"]]
    frame = _read_csv(raw)
    bad: list[str] = []
    for raw_name, _ in _ADU_MEASURES:
        columns = [f"{raw_name}_{s}" for s in suffixes]
        absent = [c for c in [f"{raw_name}_Total", *columns] if c not in frame.columns]
        if absent:
            raise ValueError(f"ADU survey lacks column(s) {absent}")
        total = _number(frame[f"{raw_name}_Total"])
        parts = pd.concat([_number(frame[c]) for c in columns], axis=1).sum(axis=1, min_count=1)
        differ = (total.fillna(0) != parts.fillna(0)) & ~(total.isna() & parts.isna())
        bad += list(frame.loc[differ, "muni_name"].astype(str))
    return sorted(set(bad))


# --------------------------------------------------------------------------- atlas


def fetch_3a_districts(
    cache: Path,
    settings: dict[str, Any] | None = None,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> dict[str, Any]:
    """Download (or reuse) the 3A district outlines as GeoJSON, simplified on the server.

    Raises:
        FetchError: The service answered with an error, or the pages do not add
            up to its own count.
    """
    import gzip

    spec = (settings or load_settings())["atlas"]
    dest = cache_dir(cache) / "mbta3a_districts.geojson.gz"
    if dest.is_file() and not refresh:
        return json.loads(gzip.decompress(dest.read_bytes()).decode("utf-8"))
    from visionhousing.data.sources.transit import query_ntm

    client = client or HttpClient(timeout=120)
    source = get_source(str(spec["source"]))
    url = source.url(layer=int(spec["layer"]))
    base = {"where": "1=1", "f": "json"}
    count = json.loads(query_ntm(client, url, {**base, "returnCountOnly": "true"}))["count"]
    features: list[dict[str, Any]] = []
    offset, page = 0, int(spec["page"])
    while offset < count:
        doc = json.loads(
            query_ntm(
                client,
                url,
                {
                    "where": "1=1",
                    "outFields": ",".join(["OBJECTID", *ATLAS_FIELDS]),
                    "outSR": "4326",
                    "maxAllowableOffset": str(spec["simplify_degrees"]),
                    "geometryPrecision": str(spec["precision"]),
                    "orderByFields": "OBJECTID",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(page),
                    "f": "geojson",
                },
            )
        )
        got = doc.get("features", [])
        if not got:
            break
        features += got
        offset += len(got)
    if len(features) != count:
        raise FetchError(f"3A atlas: got {len(features)} districts, the service counts {count}")
    collection = {"type": "FeatureCollection", "features": features}
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(collection, sort_keys=True, separators=(",", ":")).encode("utf-8")
    part = dest.with_name(dest.name + ".part")
    with part.open("wb") as fh, gzip.GzipFile(filename="", fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(text)
    part.replace(dest)
    if manifest is not None:
        vintage = f"read {date.today().isoformat()}"
        mf.record(mf.entry_for_file(source.id, url, dest, vintage, root=mf.ROOT), manifest)
    return collection


def _epoch_date(value: object) -> str:
    if value in (None, "", 0):
        return ""
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def parse_3a_districts(collection: dict[str, Any]) -> pd.DataFrame:
    """One row per 3A district: atlas attributes under our names, plus the outline.

    ``geometry`` is the simplified outline as compact GeoJSON text (longitude,
    latitude). ``submitted_on`` is the date in the town's submission (empty
    when the atlas has none). Blank text is trimmed to empty.
    """
    rows: list[dict[str, Any]] = []
    for feature in collection.get("features", []):
        props = feature.get("properties") or {}
        row: dict[str, Any] = {"objectid": props.get("OBJECTID")}
        for field, name in ATLAS_FIELDS.items():
            value = props.get(field)
            if isinstance(value, str):
                value = value.strip()
            row[name] = value
        row["submitted_on"] = _epoch_date(props.get("DATE_"))
        geometry = feature.get("geometry")
        row["geometry"] = json.dumps(geometry, separators=(",", ":")) if geometry else ""
        rows.append(row)
    out = pd.DataFrame(rows, columns=["objectid", *ATLAS_FIELDS.values(), "geometry"])
    out["muni_id"] = pd.to_numeric(out["muni_id"], errors="coerce").astype("Int64")
    return out.sort_values(["municipality", "zone_code", "objectid"]).reset_index(drop=True)
