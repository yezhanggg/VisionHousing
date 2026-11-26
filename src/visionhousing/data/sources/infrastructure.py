"""Fetch the water and sewer readiness inputs: official service areas, plan status, EPA context.

Plain-language summary
----------------------
Whether a home can connect to a public sewer is decided by local utilities and
the municipality's sewage facilities plan, not by how close a pipe is. This
module fetches only **official records**, and the EPA national files that give
context, from their public services (no key):

Official, used for a tract's status (``assumptions/infrastructure.yaml`` lists them):

- **Mapped public sewer service areas** published by a county or state body,
  where one exists and its terms allow sharing results (Pennsylvania:
  Montgomery and Adams counties; Vermont: the state's known wastewater service
  areas). Read from each
  publisher's map service, page by page (:func:`fetch_layer`).
- **Public water supplier service areas** (Pennsylvania DEP, statewide).
- **Act 537 official plan status** (Pennsylvania DEP): the date DEP last
  approved each municipality's sewage facilities plan, from DEP's six regional
  tables (PDF), read with ``pypdf`` (:func:`fetch_act537`).

Context only (never a status, never a capacity):

- **EPA sewersheds** (version 1.3): the area draining to each wastewater
  treatment plant, mostly modelled by machine learning (EPA says they are not
  for property-level decisions).
- **EPA sewershed endpoints**: the treatment plants, with their authority and
  NPDES permit number (which gives the plant's EPA ECHO page, an official listing).
- **EPA Clean Watersheds Needs Survey 2022**: documented capital needs by county
  and category (January 2022 dollars).

Polygons are kept in the download cache (``data/raw/infrastructure/``) in
longitude/latitude, thinned by the server to about 5 m; only per-tract shares
reach the pack. Every file is recorded in the download manifest.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient

__all__ = [
    "ACT537_CLASSES",
    "CWNS_COUNTY_URL",
    "EPA_ENDPOINTS_URL",
    "EPA_SEWERSHEDS_URL",
    "Layer",
    "fetch_act537",
    "fetch_cwns_county",
    "fetch_endpoints",
    "fetch_layer",
    "parse_act537_text",
    "read_layer",
]

FOLDER = "infrastructure"
EPA_SERVICES = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services"
"""EPA's ArcGIS Online services folder."""
SEWERSHED_SERVICE = "_".join(("National", "Sewershed", "Web", "Map", "Internal", "WFL1"))
"""EPA's sewershed feature service (layer 0 sewersheds, layer 1 endpoint plants)."""
EPA_SEWERSHEDS_URL = f"{EPA_SERVICES}/{SEWERSHED_SERVICE}/FeatureServer/0"
EPA_ENDPOINTS_URL = f"{EPA_SERVICES}/{SEWERSHED_SERVICE}/FeatureServer/1"
CWNS_COUNTY_URL = f"{EPA_SERVICES}/ARTN_COUNTY/FeatureServer/0"
ECHO_URL = "https://echo.epa.gov/detailed-facility-report?fid={permit}&sys=NPDES"
ID_CHUNK = 100
"""Features asked for per request (by object id)."""

ACT537_CLASSES: dict[str, str] = {
    "Plan less than 10 years": "lt10",
    "Plan between 10 and 20 years": "10to20",
    "Plan between 20 and 53 years": "20plus",
    "Pre-Regulatory County Plan": "prereg",
}
"""DEP's plan-age words and the short codes used in the pack."""


@dataclass(frozen=True)
class Layer:
    """One map-service layer to read (an entry of ``assumptions/infrastructure.yaml``).

    Attributes:
        id: Source id in ``sources.yaml``.
        url: The layer's REST address (``.../MapServer/18`` or ``.../FeatureServer/0``).
        where: Filter on the layer's attributes.
        fields: Attribute names kept.
        counties: County codes it covers (empty: the whole state).
        extent: ``county``: a complete inventory, so homes in its counties outside
            every polygon are mapped as outside; ``inside_only``: only homes inside
            a polygon are known.
        as_of: Edition or date of the layer, as its publisher gives it.
        kind: ``sewer``, ``water`` or ``context``.
    """

    id: str
    url: str
    where: str = "1=1"
    fields: tuple[str, ...] = ()
    counties: tuple[str, ...] = ()
    extent: str = "inside_only"
    as_of: str = ""
    kind: str = "sewer"
    name_field: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_rules(cls, entry: dict[str, Any], kind: str) -> Layer:
        """Build from one YAML entry."""
        fields = tuple(entry.get("fields") or ())
        name_field = str(entry.get("name_field") or "")
        if name_field and name_field not in fields:
            fields = (name_field, *fields)
        return cls(
            id=str(entry["id"]),
            url=str(entry["url"]),
            where=str(entry.get("where") or "1=1"),
            fields=fields,
            counties=tuple(str(c) for c in entry.get("counties") or ()),
            extent=str(entry.get("extent") or "inside_only"),
            as_of=str(entry.get("as_of") or ""),
            kind=kind,
            name_field=name_field,
        )


def _json(client: HttpClient, url: str, params: dict[str, str]) -> dict[str, Any]:
    doc = json.loads(client.get_bytes(url, params).decode("utf-8"))
    if "error" in doc:
        raise FetchError(f"{url}: {doc['error']}")
    return doc


def query_features(
    client: HttpClient,
    url: str,
    where: str,
    fields: tuple[str, ...],
    *,
    geometry: bool = True,
    offset_deg: float = 0.00005,
) -> list[dict[str, Any]]:
    """Every feature matching ``where``: attributes, and rings in lon/lat (NAD83) when asked.

    Asks for the object ids first, then for the features in chunks of
    :data:`ID_CHUNK` ids, so it works on services with or without paging.

    Raises:
        FetchError: The service answered with an error or left features out.
    """
    ids = _json(client, f"{url}/query", {"where": where, "returnIdsOnly": "true", "f": "json"})
    if not geometry and len(ids.get("objectIds") or []) <= ID_CHUNK * 20:
        # Attribute-only tables are small: one query by the filter, checked against the ids.
        # (EPA's ArcGIS host answered some id-list queries with "304 Not Modified".)
        params = {
            "where": where,
            "outFields": ",".join(fields) or "OBJECTID",
            "returnGeometry": "false",
            "resultRecordCount": str(ID_CHUNK * 20),
            "f": "json",
        }
        doc = _json(client, f"{url}/query", params)
        rows = [{"attributes": f.get("attributes") or {}} for f in doc.get("features", [])]
        if len(rows) != len(ids.get("objectIds") or []):
            raise FetchError(f"{url}: got {len(rows)} of {len(ids.get('objectIds') or [])} rows")
        return rows
    object_ids = sorted(int(i) for i in ids.get("objectIds") or [])
    out: list[dict[str, Any]] = []
    for k in range(0, len(object_ids), ID_CHUNK):
        chunk = object_ids[k : k + ID_CHUNK]
        params = {
            "objectIds": ",".join(str(i) for i in chunk),
            "outFields": ",".join(fields) if fields else "OBJECTID",
            "returnGeometry": "true" if geometry else "false",
            "f": "json",
        }
        if geometry:
            params.update(
                {"outSR": "4269", "maxAllowableOffset": f"{offset_deg:g}", "geometryPrecision": "6"}
            )
        doc = _json(client, f"{url}/query", params)
        for f in doc.get("features", []):
            row: dict[str, Any] = {"attributes": f.get("attributes") or {}}
            geom = f.get("geometry") or {}
            if geometry and "x" in geom:
                row["point"] = [geom.get("x"), geom.get("y")]
            elif geometry:
                row["rings"] = geom.get("rings") or []
            out.append(row)
    if len(out) != len(object_ids):
        raise FetchError(f"{url}: got {len(out)} of {len(object_ids)} features")
    return out


def _cache_path(cache: Path, name: str) -> Path:
    return Path(cache) / FOLDER / name


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    with part.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    part.replace(path)


def read_layer(path: Path) -> dict[str, Any]:
    """Read a cached layer file written by :func:`fetch_layer`."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def fetch_layer(
    layer: Layer,
    cache: Path,
    *,
    client: HttpClient,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
    suffix: str = "",
) -> tuple[dict[str, Any], Path]:
    """Read one layer (or reuse the cached copy) and return it with its cache path.

    The cached file holds ``{"id", "url", "where", "as_of", "read_on", "features"}``,
    each feature ``{"attributes": {...}, "rings": [[[lon, lat], ...], ...]}``.
    """
    path = _cache_path(cache, f"{layer.id}{suffix}.json.gz")
    if path.exists() and not refresh:
        return read_layer(path), path
    features = query_features(client, layer.url, layer.where, layer.fields)
    data = {
        "id": layer.id,
        "url": layer.url,
        "where": layer.where,
        "as_of": layer.as_of,
        "read_on": date.today().isoformat(),
        "features": features,
    }
    _save_json(path, data)
    if manifest is not None:
        mf.record(
            mf.entry_for_file(layer.id, layer.url, path, layer.as_of or "current", root=mf.ROOT),
            manifest,
        )
    return data, path


# --------------------------------------------------------------------------- EPA tables


def fetch_endpoints(
    state: str, cache: Path, *, client: HttpClient, manifest: Path | None = mf.MANIFEST_PATH
) -> tuple[pd.DataFrame, Path]:
    """EPA sewershed endpoints (treatment plants) in a state: id, names, permit, link, location."""
    path = _cache_path(cache, f"epa_sewershed_endpoints_{state}.csv")
    if not path.exists():
        fields = (
            "CWNS_ID",
            "FACILITY_NAME",
            "Authority_Name",
            "COUNTY_FIPS",
            "Permit_Number",
            "TOTAL_RES_POPULATION_2022",
            "END_FACILITY",
        )
        rows = query_features(client, EPA_ENDPOINTS_URL, f"STATE_CODE = '{state}'", fields)
        frame = pd.DataFrame([r["attributes"] for r in rows], columns=list(fields))
        frame["lon"] = [(r.get("point") or [None, None])[0] for r in rows]
        frame["lat"] = [(r.get("point") or [None, None])[1] for r in rows]
        frame["echo_url"] = [
            ECHO_URL.format(permit=p)
            if isinstance(p, str) and re.fullmatch(r"[A-Z]{2}\d{7}", p)
            else ""
            for p in frame["Permit_Number"]
        ]
        frame = frame.sort_values("CWNS_ID").reset_index(drop=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, lineterminator="\n")
        if manifest is not None:
            mf.record(
                mf.entry_for_file(
                    "epa_sewersheds", EPA_ENDPOINTS_URL, path, "version 1.3", root=mf.ROOT
                ),
                manifest,
            )
    return pd.read_csv(path, dtype=str, keep_default_na=False), path


CWNS_FIELDS = (
    "COUNTY_FIPS",
    "I_NEED",
    "II_NEED",
    "III_A_NEED",
    "III_B_NEED",
    "IV_A_NEED",
    "IV_B_NEED",
    "V_NEED",
    "WWTP_TOTAL",
    "DECENTRALIZED_TOTAL",
    "STORMWATER_TOTAL",
    "NPS_TOTAL",
    "ALL_TOTAL",
)


def fetch_cwns_county(
    state_fips: str,
    cache: Path,
    *,
    client: HttpClient,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> tuple[pd.DataFrame, Path]:
    """EPA CWNS 2022 documented needs per county (January 2022 dollars), one row per county."""
    path = _cache_path(cache, f"cwns_2022_county_{state_fips}.csv")
    if not path.exists():
        rows = query_features(
            client,
            CWNS_COUNTY_URL,
            f"COUNTY_FIPS LIKE '{state_fips}%'",
            CWNS_FIELDS,
            geometry=False,
        )
        frame = pd.DataFrame([r["attributes"] for r in rows], columns=list(CWNS_FIELDS))
        frame = frame.rename(columns={"COUNTY_FIPS": "county"}).sort_values("county")
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, lineterminator="\n")
        if manifest is not None:
            mf.record(
                mf.entry_for_file(
                    "epa_cwns_2022", CWNS_COUNTY_URL, path, "2022 survey", root=mf.ROOT
                ),
                manifest,
            )
    frame = pd.read_csv(path, dtype={"county": str})
    return frame, path


# --------------------------------------------------------------------------- Act 537

ACT537_LINE = re.compile(
    r"^(?P<name>.+) (?P<county>\S+) (?P<date>\d{1,2}/\d{1,2}/\d{4}) (?P<status>.+)$"
)


def parse_act537_text(text: str) -> pd.DataFrame:
    """Rows of one DEP plan-age table: municipality, county, approval date, plan-age words.

    Lines that do not end in a date and a known plan-age phrase are skipped
    (headers, page numbers).
    """
    rows = []
    for line in text.splitlines():
        m = ACT537_LINE.match(line.strip())
        if not m or m.group("status").strip() not in ACT537_CLASSES:
            continue
        day = datetime.strptime(m.group("date"), "%m/%d/%Y").date()
        rows.append(
            {
                "name": m.group("name").strip(),
                "county_name": m.group("county").strip(),
                "approved": day.isoformat(),
                "status": m.group("status").strip(),
                "plan_class": ACT537_CLASSES[m.group("status").strip()],
            }
        )
    return pd.DataFrame(rows, columns=["name", "county_name", "approved", "status", "plan_class"])


def fetch_act537(
    rules: dict[str, Any],
    cache: Path,
    *,
    client: HttpClient,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> tuple[pd.DataFrame, list[Path]]:
    """DEP's Act 537 plan-age tables for every region, read from the cached or fetched PDFs.

    Returns:
        One row per municipality (columns of :func:`parse_act537_text` plus
        ``region``), and the PDF files read.
    """
    from pypdf import PdfReader

    frames, files = [], []
    for region in rules["regions"]:
        url = rules["url_pattern"].format(region=region)
        path = _cache_path(cache, f"act537/{region}_plan_ages.pdf")
        if not path.exists():
            result = client.download(url, path)
            if manifest is not None:
                mf.record(
                    mf.entry_for_file(
                        "pa_dep_act537_plan_status",
                        result.url,
                        path,
                        str(rules["as_of"]),
                        root=mf.ROOT,
                    ),
                    manifest,
                )
        reader = PdfReader(io.BytesIO(path.read_bytes()))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        frame = parse_act537_text(text)
        frame["region"] = region
        frames.append(frame)
        files.append(path)
    return pd.concat(frames, ignore_index=True), files
