"""Tax-credit and incentive areas: where federal and state programs give developers an edge.

Plain-language summary
----------------------
Several public programs make building or rehabilitating homes cheaper in some
places. This module fetches the official lists, keeps only the state's rows,
and returns tidy tables. None of the sources needs a key.

========================  =================================================  ===========
source id                 what it is                                         geography
========================  =================================================  ===========
``hud_qct``               HUD Qualified Census Tracts: low-income tracts      2020 tract
                          where a Low-Income Housing Tax Credit (LIHTC)
                          project gets a 30% larger credit basis
``hud_dda``               HUD Difficult Development Areas: high-cost areas    ZCTA or
                          with the same 30% boost                             county
``census_zcta_tract_rel`` Census land shared by each ZIP Code Tabulation      ZCTA x tract
                          Area (ZCTA) and tract, to place ZCTA-based DDAs
``treasury_oz2027``       Treasury's Opportunity Zones for 2027 onward        2020 tract
                          (capital-gains tax relief for investments)
``cdfi_nmtc_lic``         CDFI Fund New Markets Tax Credit low-income         2020 tract
                          community test results (2016-2020 ACS)
``hud_lihtc``             HUD LIHTC database: projects placed in service      point
``dced_koz_sites``        Pennsylvania Keystone Opportunity Zone sites        point
                          (state and local tax abatements; PA only)
``nps_nrhp``              National Register of Historic Places listings       point and
                          (historic rehabilitation tax credit eligibility)    polygon
========================  =================================================  ===========

Map services ("feature services") are read page by page with
:func:`fetch_features`; the number of rows received is checked against the
service's own count. Spreadsheets are read with the project's own
standard-library readers (:func:`visionhousing.data.sources.prices.xlsx_rows`
and :mod:`visionhousing.data.sources.xlsb`).

Everything is cached under ``data/raw/incentives/`` as small state extracts.
Places are matched to tracts later, in :mod:`visionhousing.features.incentives`.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import STATE_FIPS
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.data.sources.acs import stream_filter
from visionhousing.data.sources.prices import xlsx_rows
from visionhousing.data.sources.xlsb import xlsb_rows

__all__ = [
    "STATE_NAMES",
    "IncentiveInputs",
    "fetch_features",
    "fetch_incentives",
    "read_feature_page",
    "read_nmtc",
    "read_oz2027",
    "read_zcta_tract",
]

PAGE = 1000
"""Rows asked for per map-service page (every service used allows at least 1,000)."""
NRHP_SIMPLIFY_DEGREES = 0.0001
"""District polygons are simplified by the server to about 10 metres."""

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
"""Two-digit state code to the state's name, as the Treasury, CDFI and NPS files write it."""

_POSTAL = {v: k for k, v in STATE_FIPS.items()}


@dataclass
class IncentiveInputs:
    """The state's incentive extracts, each ``None`` when its source could not be fetched."""

    qct: pd.DataFrame | None = None
    dda: pd.DataFrame | None = None
    zcta_tract: pd.DataFrame | None = None
    oz2027: pd.DataFrame | None = None
    nmtc: pd.DataFrame | None = None
    lihtc: pd.DataFrame | None = None
    koz: pd.DataFrame | None = None
    nrhp_points: pd.DataFrame | None = None
    nrhp_districts: list[tuple[str, dict[str, Any]]] | None = None
    files: list[tuple[str, Path]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- map services


def read_feature_page(data: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Read one page of an ArcGIS query answer (``f=json``).

    Returns:
        The features (each ``{"attributes": ..., "geometry": ...}``) and whether
        the service says more rows exist.

    Raises:
        FetchError: If the service answered with an error object.
    """
    doc = json.loads(data.decode("utf-8"))
    if "error" in doc:
        raise FetchError(f"map service error: {doc['error']}")
    return list(doc.get("features", [])), bool(doc.get("exceededTransferLimit"))


def fetch_features(
    client: HttpClient,
    url: str,
    where: str,
    out_fields: str,
    *,
    geometry: bool = False,
    extra: Mapping[str, str] | None = None,
    page: int = PAGE,
) -> list[dict[str, Any]]:
    """Every feature a query matches, page by page, checked against the service's count.

    Raises:
        FetchError: If the pages do not add up to the service's count.
    """
    base = {"where": where, "f": "json", **(extra or {})}
    count = json.loads(client.get_bytes(url, {**base, "returnCountOnly": "true"}))
    if "error" in count:
        raise FetchError(f"map service error: {count['error']}")
    expected = int(count["count"])
    out: list[dict[str, Any]] = []
    offset = 0
    while offset < expected:
        params = {
            **base,
            "outFields": out_fields,
            "returnGeometry": "true" if geometry else "false",
            "orderByFields": "OBJECTID",
            "resultOffset": str(offset),
            "resultRecordCount": str(page),
        }
        if geometry:
            params["outSR"] = "4326"
        features, _ = read_feature_page(client.get_bytes(url, params))
        if not features:
            break
        out += features
        offset += len(features)
    if len(out) != expected:
        raise FetchError(f"{url}: got {len(out)} features, the service counts {expected}")
    return out


def _attrs(features: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([f.get("attributes", {}) for f in features])


def _points(features: list[dict[str, Any]]) -> pd.DataFrame:
    frame = _attrs(features)
    frame["lat"] = [(f.get("geometry") or {}).get("y") for f in features]
    frame["lon"] = [(f.get("geometry") or {}).get("x") for f in features]
    return frame


# --------------------------------------------------------------------------- spreadsheets


def read_oz2027(source: bytes | str | Path, state_fips: str | None = None) -> pd.DataFrame:
    """Read Treasury's 2027 Opportunity Zone designations (``.xlsx``) or the cached CSV.

    Returns:
        Columns ``tract`` and ``rural`` (1 for a rural zone, else 0).
    """
    if isinstance(source, str | Path) and str(source).endswith(".csv"):
        frame = pd.read_csv(source, dtype={"tract": str})
    else:
        rows = []
        header: list[str] | None = None
        for cells in xlsx_rows(source):
            names = [str(c).strip().lower() if c is not None else "" for c in cells]
            if header is None:
                if "census tract number" in names:
                    header = names
                continue
            rec = dict(zip(header, cells, strict=False))
            tract = str(rec.get("census tract number") or "").strip()
            if len(tract) == 10:
                tract = "0" + tract
            if len(tract) != 11 or not tract.isdigit():
                continue
            rural = str(rec.get("rural qoz") or "").strip().lower() == "rural"
            rows.append({"tract": tract, "rural": int(rural)})
        if header is None:
            raise ValueError("Opportunity Zone file has no 'Census Tract Number' header")
        frame = pd.DataFrame(rows, columns=["tract", "rural"])
    if state_fips is not None:
        frame = frame[frame["tract"].str.startswith(state_fips)]
    return frame.drop_duplicates("tract").sort_values("tract").reset_index(drop=True)


NMTC_COLUMNS = ("tract", "nmtc_lic", "nmtc_poverty_rate", "nmtc_mfi_pct", "nmtc_metro")
_NMTC_HEADERS = {
    "tract": "2020 census tract number",
    "nmtc_lic": "does census tract qualify for nmtc",
    "nmtc_poverty_rate": "census tract poverty rate",
    "nmtc_mfi_pct": "census tract percent of benchmarked median family income",
    "nmtc_metro": "omb metro/non-metro designation",
}


def read_nmtc(source: bytes | str | Path, state_fips: str | None = None) -> pd.DataFrame:
    """Read the CDFI Fund NMTC low-income community file (``.xlsb``) or the cached CSV.

    Columns are found by the start of their header text. ``nmtc_lic`` is 1 when
    the tract meets the poverty or the income test, else 0. ``nmtc_mfi_pct`` is
    the tract's median family income as a percent of its benchmark (the file
    stores a fraction; it is multiplied by 100).
    """
    if isinstance(source, str | Path) and str(source).endswith(".csv"):
        frame = pd.read_csv(source, dtype={"tract": str})
    else:
        rows = []
        index: dict[str, int] | None = None
        for cells in xlsb_rows(source):
            if index is None:
                names = [str(c).strip().lower() if c is not None else "" for c in cells]
                found = {}
                for key, start in _NMTC_HEADERS.items():
                    hits = [i for i, n in enumerate(names) if n.startswith(start)]
                    if hits:
                        found[key] = hits[0]
                if len(found) == len(_NMTC_HEADERS):
                    index = found
                continue
            tract = str(cells[index["tract"]] or "").strip() if index["tract"] < len(cells) else ""
            if len(tract) != 11 or not tract.isdigit():
                continue

            def cell(key: str) -> Any:
                i = index[key]  # type: ignore[index]
                return cells[i] if i < len(cells) else None

            mfi = cell("nmtc_mfi_pct")
            rows.append(
                {
                    "tract": tract,
                    "nmtc_lic": int(str(cell("nmtc_lic") or "").strip().upper() == "YES"),
                    "nmtc_poverty_rate": _num(cell("nmtc_poverty_rate")),
                    "nmtc_mfi_pct": None if _num(mfi) is None else round(_num(mfi) * 100, 2),
                    "nmtc_metro": str(cell("nmtc_metro") or "").strip(),
                }
            )
        if index is None:
            raise ValueError("NMTC file: header row with the expected columns not found")
        frame = pd.DataFrame(rows, columns=list(NMTC_COLUMNS))
    if state_fips is not None:
        frame = frame[frame["tract"].str.startswith(state_fips)]
    return frame[list(NMTC_COLUMNS)].sort_values("tract").reset_index(drop=True)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def read_zcta_tract(source: str | Path) -> pd.DataFrame:
    """Read the ZCTA-to-tract relationship rows: ``zcta``, ``tract``, ``land_m2`` (shared land)."""
    frame = pd.read_csv(source, sep="|", dtype=str, encoding="utf-8-sig", keep_default_na=False)
    out = pd.DataFrame(
        {
            "zcta": frame["GEOID_ZCTA5_20"],
            "tract": frame["GEOID_TRACT_20"],
            "land_m2": pd.to_numeric(frame["AREALAND_PART"], errors="coerce").fillna(0),
        }
    )
    return out[out["zcta"] != ""].reset_index(drop=True)


# --------------------------------------------------------------------------- fetch


def _write_csv(frame: pd.DataFrame, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    frame.to_csv(part, index=False, lineterminator="\n")
    part.replace(dest)


def _record(source_id: str, url: str, dest: Path, manifest: Path | None) -> None:
    if manifest is not None:
        vintage = get_source(source_id).vintage
        mf.record(mf.entry_for_file(source_id, url, dest, vintage, root=mf.ROOT), manifest)


def _fetch_qct(fips: str, folder: Path, client: HttpClient, manifest: Path | None) -> Path:
    dest = folder / f"hud_qct_{fips}.csv"
    if not dest.exists():
        url = get_source("hud_qct").url()
        feats = fetch_features(client, url, f"STATE='{fips}'", "GEOID")
        frame = _attrs(feats).rename(columns={"GEOID": "tract"})
        frame = frame[["tract"]].astype(str).drop_duplicates().sort_values("tract")
        _write_csv(frame, dest)
        _record("hud_qct", url, dest, manifest)
    return dest


def _fetch_zcta_tract(fips: str, folder: Path, client: HttpClient, manifest: Path | None) -> Path:
    dest = folder / f"zcta_tract_{fips}.txt"
    if not dest.exists():
        url = get_source("census_zcta_tract_rel").url()
        prefix = fips.encode("ascii")

        def make_keep(header: bytes):
            names = header.decode("utf-8-sig").split("|")
            if "GEOID_TRACT_20" not in names:
                raise ValueError("relationship file lacks GEOID_TRACT_20")
            at = names.index("GEOID_TRACT_20")
            return lambda line: line.split(b"|")[at].startswith(prefix)

        stream_filter(client, url, dest, make_keep)
        _record("census_zcta_tract_rel", url, dest, manifest)
    return dest


def _fetch_dda(
    fips: str, folder: Path, client: HttpClient, manifest: Path | None, zctas: set[str]
) -> Path:
    dest = folder / f"hud_dda_{fips}.csv"
    if not dest.exists():
        url = get_source("hud_dda").url()
        feats = fetch_features(client, url, "1=1", "ZCTA5,DDA_CODE,DDA_TYPE,DDA_NAME")
        frame = _attrs(feats).fillna("")
        frame = frame.rename(
            columns={"ZCTA5": "code", "DDA_CODE": "dda_code", "DDA_TYPE": "dda_type"}
        )
        frame = frame.rename(columns={"DDA_NAME": "dda_name"})
        county = (frame["dda_type"] == "NM") & frame["code"].astype(str).str.startswith(fips)
        metro = (frame["dda_type"] == "SA") & frame["code"].astype(str).isin(zctas)
        frame = frame[county | metro][["code", "dda_type", "dda_code", "dda_name"]]
        _write_csv(frame.sort_values(["dda_type", "code"]), dest)
        _record("hud_dda", url, dest, manifest)
    return dest


def _fetch_oz(fips: str, folder: Path, client: HttpClient, manifest: Path | None) -> Path:
    dest = folder / f"oz2027_{fips}.csv"
    if not dest.exists():
        url = get_source("treasury_oz2027").url()
        tmp = folder / "oz2027.xlsx"
        try:
            client.download(url, tmp)
            frame = read_oz2027(tmp, fips)
        finally:
            tmp.unlink(missing_ok=True)
        _write_csv(frame, dest)
        _record("treasury_oz2027", url, dest, manifest)
    return dest


def _fetch_nmtc(fips: str, folder: Path, client: HttpClient, manifest: Path | None) -> Path:
    dest = folder / f"nmtc_lic_{fips}.csv"
    if not dest.exists():
        url = get_source("cdfi_nmtc_lic").url()
        tmp = folder / "nmtc_lic.xlsb"
        try:
            client.download(url, tmp)
            frame = read_nmtc(tmp, fips)
        finally:
            tmp.unlink(missing_ok=True)
        if frame.empty:
            raise ValueError(f"NMTC file has no tracts for state {fips}")
        _write_csv(frame, dest)
        _record("cdfi_nmtc_lic", url, dest, manifest)
    return dest


LIHTC_FIELDS = ("HUD_ID", "N_UNITS", "LI_UNITS", "YR_PIS", "COUNTY_LEVEL", "LAT", "LON")


def _fetch_lihtc(fips: str, folder: Path, client: HttpClient, manifest: Path | None) -> Path:
    dest = folder / f"hud_lihtc_{fips}.csv"
    if not dest.exists():
        url = get_source("hud_lihtc").url()
        feats = fetch_features(client, url, f"PROJ_ST='{_POSTAL[fips]}'", ",".join(LIHTC_FIELDS))
        frame = _attrs(feats).reindex(columns=list(LIHTC_FIELDS))
        frame.columns = ["hud_id", "units", "li_units", "year_pis", "county", "lat", "lon"]
        _write_csv(frame.sort_values("hud_id"), dest)
        _record("hud_lihtc", url, dest, manifest)
    return dest


KOZ_FIELDS = ("county", "acreage", "developed_y_n", "occupied_y_n", "expiration_date")


def _fetch_koz(fips: str, folder: Path, client: HttpClient, manifest: Path | None) -> Path | None:
    if fips != "42":
        return None
    dest = folder / f"dced_koz_sites_{fips}.csv"
    if not dest.exists():
        url = get_source("dced_koz_sites").url()
        feats = fetch_features(client, url, "1=1", ",".join(KOZ_FIELDS), geometry=True)
        frame = _points(feats).reindex(columns=[*KOZ_FIELDS, "lat", "lon"])
        frame.columns = ["county_name", "acres", "developed", "occupied", "expires", "lat", "lon"]
        _write_csv(frame, dest)
        _record("dced_koz_sites", url, dest, manifest)
    return dest


def _fetch_nrhp(
    fips: str, folder: Path, client: HttpClient, manifest: Path | None
) -> tuple[Path, Path]:
    points = folder / f"nrhp_points_{fips}.csv"
    districts = folder / f"nrhp_districts_{fips}.geojson.gz"
    source = get_source("nps_nrhp")
    where = f"STATE='{STATE_NAMES[fips].upper()}'"
    if not points.exists():
        url = source.url(layer=0)
        feats = fetch_features(client, url, where, "NRIS_Refnum,ResType,STATUS", geometry=True)
        frame = _points(feats).reindex(columns=["NRIS_Refnum", "ResType", "STATUS", "lat", "lon"])
        frame.columns = ["refnum", "res_type", "status", "lat", "lon"]
        _write_csv(frame.sort_values("refnum"), points)
        _record("nps_nrhp", url, points, manifest)
    if not districts.exists():
        url = source.url(layer=1)
        feats = fetch_features(
            client,
            url,
            f"{where} AND ResType='district'",
            "NRIS_Refnum,STATUS",
            geometry=True,
            extra={
                "maxAllowableOffset": str(NRHP_SIMPLIFY_DEGREES),
                "geometryPrecision": "5",
            },
        )
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "refnum": f["attributes"].get("NRIS_Refnum"),
                        "status": f["attributes"].get("STATUS"),
                    },
                    "geometry": {"type": "Polygon", "coordinates": f["geometry"]["rings"]},
                }
                for f in feats
                if (f.get("geometry") or {}).get("rings")
            ],
        }
        part = districts.with_name(districts.name + ".part")
        with part.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(collection, separators=(",", ":")).encode("utf-8"))
        part.replace(districts)
        _record("nps_nrhp", url, districts, manifest)
    return points, districts


def read_districts(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Read the cached district polygons as ``(reference number, GeoJSON geometry)`` pairs."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    return [
        (str(f["properties"]["refnum"]), f["geometry"])
        for f in doc["features"]
        if str(f["properties"].get("status") or "Listed") != "Removed"
    ]


def fetch_incentives(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> IncentiveInputs:
    """Fetch (or reuse) every incentive extract for one state.

    Each source is fetched on its own: one that fails is left ``None`` and named
    in ``notes``, so a busy server does not stop the whole build.
    """
    fips = to_state_fips(state)
    folder = Path(cache) / "incentives"
    folder.mkdir(parents=True, exist_ok=True)
    client = client or HttpClient()
    out = IncentiveInputs()

    def attempt(name: str, func):
        try:
            return func()
        except (FetchError, ValueError, KeyError, OSError) as err:
            out.notes.append(f"incentives: {name} not fetched ({type(err).__name__}: {err})")
            return None

    path = attempt("hud_qct", lambda: _fetch_qct(fips, folder, client, manifest))
    if path:
        out.qct = pd.read_csv(path, dtype={"tract": str})
        out.files.append(("hud_qct", path))
    path = attempt("zcta_tract", lambda: _fetch_zcta_tract(fips, folder, client, manifest))
    if path:
        out.zcta_tract = read_zcta_tract(path)
        out.files.append(("census_zcta_tract_rel", path))
        zctas = set(out.zcta_tract["zcta"])
        path = attempt("hud_dda", lambda: _fetch_dda(fips, folder, client, manifest, zctas))
        if path:
            out.dda = pd.read_csv(path, dtype=str, keep_default_na=False)
            out.files.append(("hud_dda", path))
    path = attempt("treasury_oz2027", lambda: _fetch_oz(fips, folder, client, manifest))
    if path:
        out.oz2027 = read_oz2027(path)
        out.files.append(("treasury_oz2027", path))
    path = attempt("cdfi_nmtc_lic", lambda: _fetch_nmtc(fips, folder, client, manifest))
    if path:
        out.nmtc = read_nmtc(path)
        out.files.append(("cdfi_nmtc_lic", path))
    path = attempt("hud_lihtc", lambda: _fetch_lihtc(fips, folder, client, manifest))
    if path:
        out.lihtc = pd.read_csv(path, dtype={"hud_id": str, "county": str, "year_pis": str})
        out.files.append(("hud_lihtc", path))
    path = attempt("dced_koz_sites", lambda: _fetch_koz(fips, folder, client, manifest))
    if path:
        out.koz = pd.read_csv(path, dtype={"county_name": str, "expires": str})
        out.files.append(("dced_koz_sites", path))
    pair = attempt("nps_nrhp", lambda: _fetch_nrhp(fips, folder, client, manifest))
    if pair:
        out.nrhp_points = pd.read_csv(pair[0], dtype={"refnum": str})
        out.nrhp_districts = read_districts(pair[1])
        out.files += [("nps_nrhp", pair[0]), ("nps_nrhp", pair[1])]
    return out
