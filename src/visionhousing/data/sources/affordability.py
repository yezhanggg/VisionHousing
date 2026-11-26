"""Affordability and assisted-housing sources: cost burden, income limits, subsidised homes.

Plain-language summary
----------------------
This module fetches the federal files behind the place profile's
"Cost and affordability" section and the county stock ledger, keeps only one
state's rows, and saves them as small extracts under ``data/raw/afford/``.

=======================  =================================================  ================
source id                what it is                                         geography
=======================  =================================================  ================
``hud_chas``             HUD Comprehensive Housing Affordability Strategy   state, county,
                         (CHAS) tables: households by tenure, income        tract
                         (share of HUD area median family income) and
                         housing cost burden; renter homes by the income
                         their rent is affordable to; renters by the age
                         of their building
``hud_income_limits``    HUD income limits: the area median family income   county (towns in
                         and the 30%, 50% and 80% limits for households     New England)
                         of 1 to 8 people
``hud_mf_assisted``      HUD multifamily properties with project-based      point
                         rental assistance (Section 8 and others), with
                         their contracts' units and expiration dates
``hud_public_housing``   HUD public housing developments and their units    point
``hud_hcv_tract``        Households using Housing Choice Vouchers, by       2020 tract
                         tract
=======================  =================================================  ================

The HUD tax-credit property list (``hud_lihtc``) is already fetched by
:mod:`visionhousing.data.sources.incentives`; its cached extract is reused.

Two practical points:

- **HUD's file server** (``www.huduser.gov``) answers requests that do not
  name a browser-style client with an empty "202" page (a bot filter). The
  files are public; this module asks for them with the user-agent
  ``Mozilla/5.0 (compatible; visionhousing/1.0; open-source research tool)``,
  the common format crawlers use to name themselves honestly. It does not
  pretend to be a particular browser.
- **CHAS** is published as one national zip per geographic level, each holding
  25 tables. Only three tables are needed (8, 15C and 12), so each is read out
  of the remote zip with HTTP range requests
  (:meth:`visionhousing.data.http.HttpClient.read_zip_member`); the 226 MB
  tract zip is never downloaded whole.

Nothing here works anything out; :mod:`visionhousing.features.affordability`
turns the extracts into pack tables.
"""

from __future__ import annotations

import dataclasses
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import STATE_FIPS
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.data.sources.incentives import fetch_features
from visionhousing.data.sources.prices import xlsx_rows

__all__ = [
    "CHAS_COLUMNS",
    "CHAS_LEVELS",
    "CHAS_VINTAGE",
    "FOLDER",
    "HUD_USER_AGENT",
    "IL_FISCAL_YEAR",
    "AffordInputs",
    "chas_columns",
    "fetch_affordability",
    "hud_client",
    "parse_expiration",
    "read_chas_table",
    "read_income_limits",
    "read_layer",
]

FOLDER = "afford"
"""Cache sub-folder under ``data/raw``."""

HUD_USER_AGENT = "Mozilla/5.0 (compatible; visionhousing/1.0; open-source research tool)"
"""Names this tool to HUD's file server in the format its bot filter accepts."""

CHAS_VINTAGE = "2018thru2022"
"""The CHAS edition: ACS 2018-2022 5-year estimates (HUD release of 2025-12-23)."""

CHAS_LEVELS: dict[str, str] = {"040": "state", "050": "county", "140": "tract"}
"""CHAS summary level code to the level name used in the pack."""

IL_FISCAL_YEAR = 2026
"""HUD income limits fiscal year (effective spring 2026)."""


def _table8() -> list[int]:
    """Table 8 subtotal cells: tenure totals, income-band totals and their cost-burden cells.

    The table runs owner (cell 2) then renter (cell 68). Each tenure holds five
    income bands of 13 cells; a band's total is its first cell and its four
    cost-burden subtotals are 1, 4, 7 and 10 cells after it (the cells in
    between split each burden by kitchen and plumbing, which is not used).
    """
    cells = [1]
    for base in (2, 68):
        cells.append(base)
        for band in range(5):
            start = base + 1 + 13 * band
            cells += [start, start + 1, start + 4, start + 7, start + 10]
    return cells


CHAS_COLUMNS: dict[str, list[int]] = {
    "8": _table8(),
    "15C": [1, 2, 4, 25, 46, 67],
    "12": [88, 123, 124, 140, 141, 157, 158],
}
"""CHAS cells kept, by table: the estimate and its margin of error for each.

- Table 8: tenure x household income (5 bands of HUD area median family
  income) x housing cost burden (4 bands).
- Table 15C: renter-occupied homes by the income their rent is affordable to
  (cells 4, 25, 46, 67: rent at most RHUD30, RHUD30-50, RHUD50-80, over RHUD80;
  cell 2: homes lacking a complete kitchen or plumbing, any rent).
- Table 12: renter households (cell 88) and those in buildings built 1960-1979,
  1940-1959 and in 1939 or earlier (cells 123, 140, 157), each followed by
  those households at or below 50% of area median family income.

The cell meanings are checked against HUD's data dictionary in the tests
(``tests/fixtures/affordability/chas_dictionary_rows.csv``).
"""


def chas_columns(table: str) -> list[str]:
    """The published column names kept from one CHAS table (estimates, then margins)."""
    cells = CHAS_COLUMNS[table]
    return [f"T{table}_est{c}" for c in cells] + [f"T{table}_moe{c}" for c in cells]


@dataclass
class AffordInputs:
    """One state's affordability extracts; each ``None`` when it could not be fetched."""

    chas: pd.DataFrame | None = None
    income_limits: pd.DataFrame | None = None
    mf_assisted: pd.DataFrame | None = None
    public_housing: pd.DataFrame | None = None
    hcv: pd.DataFrame | None = None
    lihtc: pd.DataFrame | None = None
    read_on: str | None = None
    """The date the assisted-housing lists were read (expirations are counted from it)."""
    files: list[tuple[str, Path]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def hud_client(client: HttpClient | None = None) -> HttpClient:
    """A copy of ``client`` (or a new one) that names itself with :data:`HUD_USER_AGENT`."""
    return dataclasses.replace(client or HttpClient(), user_agent=HUD_USER_AGENT)


# --------------------------------------------------------------------------- readers


def _geoid(raw: str, level: str) -> str:
    """CHAS ``geoid`` (``0500000US42101``, ``14000US42101000100``) to a plain code."""
    code = str(raw).split("US", 1)[-1]
    return {"state": code[:2], "county": code[:5], "tract": code[:11]}[level]


def read_chas_table(
    source: bytes | str | Path, table: str, state_fips: str, level: str
) -> pd.DataFrame:
    """Read one CHAS table file, keep one state's rows and the cells in :data:`CHAS_COLUMNS`.

    Args:
        source: The CSV bytes (as read from the zip) or a path.
        table: ``"8"``, ``"15C"`` or ``"12"``.
        state_fips: Two-digit state code.
        level: ``"state"``, ``"county"`` or ``"tract"``.

    Returns:
        Columns ``level``, ``geoid`` and the kept cells as numbers, sorted by ``geoid``.
    """
    wanted = chas_columns(table)
    data = io.BytesIO(source) if isinstance(source, bytes) else source
    frame = pd.read_csv(
        data,
        dtype={"geoid": str, "st": str},
        usecols=["geoid", "st", *wanted],
        encoding="latin-1",
    )
    frame = frame[frame["st"].str.zfill(2) == state_fips]
    cols = {"level": level, "geoid": [_geoid(g, level) for g in frame["geoid"]]}
    cols.update({c: pd.to_numeric(frame[c], errors="coerce").to_numpy() for c in wanted})
    out = pd.DataFrame(cols)
    return out.sort_values("geoid").reset_index(drop=True)


def read_income_limits(source: bytes | str | Path, state: str) -> pd.DataFrame:
    """Read HUD's Section 8 income limits workbook; keep one state's rows.

    Args:
        source: The ``.xlsx`` bytes or path (first sheet, one row per county, or
            per town in New England).
        state: Two-letter postal code.

    Returns:
        Columns ``county`` (5 digits), ``hud_area_code``, ``hud_area_name``,
        ``town``, ``median`` (area median family income), ``l30_1``..``l30_8``
        (extremely low income), ``l50_1``..``l50_8`` (very low income) and
        ``l80_1``..``l80_8`` (low income), in dollars a year.
    """
    rows = iter(xlsx_rows(source))
    header = [str(h).strip() for h in next(rows)]
    pos = {name.lower(): i for i, name in enumerate(header)}
    median_col = next(i for name, i in pos.items() if name.startswith("median"))
    out = []
    for row in rows:
        cell = {name: (row[i] if i < len(row) else None) for name, i in pos.items()}
        if str(cell.get("stusps") or "").upper() != state.upper():
            continue
        rec = {
            "county": str(cell["fips"])[:5],
            "hud_area_code": cell["hud_area_code"],
            "hud_area_name": cell["hud_area_name"],
            "town": cell.get("county_town_name"),
            "median": row[median_col],
        }
        for size in range(1, 9):
            rec[f"l30_{size}"] = cell[f"eli_{size}"]
            rec[f"l50_{size}"] = cell[f"l50_{size}"]
            rec[f"l80_{size}"] = cell[f"l80_{size}"]
        out.append(rec)
    frame = pd.DataFrame(out)
    return frame.sort_values(["county", "town"], na_position="first").reset_index(drop=True)


_MONTHS = {
    m: i
    for i, m in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}


def parse_expiration(text: object) -> date | None:
    """HUD contract dates ``"31-MAR-30"`` to a date (two-digit years are 2000-2099)."""
    if not isinstance(text, str) or text.count("-") != 2:
        return None
    day, mon, year = text.strip().split("-")
    if mon.upper() not in _MONTHS or not (day.isdigit() and year.isdigit()):
        return None
    yy = int(year)
    try:
        return date(2000 + yy if yy < 100 else yy, _MONTHS[mon.upper()], int(day))
    except ValueError:
        return None


def read_layer(path: str | Path) -> pd.DataFrame:
    """Read a cached map-service extract (``hud_mf_assisted_42.csv`` and the like).

    Codes stay text (leading zeros kept); counts become numbers; HUD's negative
    "suppressed" codes become blanks.
    """
    text = (
        "property_id",
        "development_code",
        "county_level",
        "geoid",
        "program_type1",
        "expiration_date1",
        "expiration_date2",
        "read_on",
    )
    frame = pd.read_csv(path, dtype={c: str for c in text})
    for col in frame.columns:
        if col not in text:
            values = pd.to_numeric(frame[col], errors="coerce")
            frame[col] = values.where(values >= 0) if col not in ("lat", "lon") else values
    return frame


# --------------------------------------------------------------------------- fetch

MF_FIELDS = (
    "PROPERTY_ID",
    "TOTAL_UNIT_COUNT",
    "TOTAL_ASSISTED_UNIT_COUNT",
    "CONTRACT_COUNT",
    "UNITS1",
    "UNITS2",
    "EXPIRATION_DATE1",
    "EXPIRATION_DATE2",
    "PROGRAM_TYPE1",
    "COUNTY_LEVEL",
    "LAT",
    "LON",
)
PH_FIELDS = (
    "DEVELOPMENT_CODE",
    "TOTAL_DWELLING_UNITS",
    "ACC_UNITS",
    "TOTAL_OCCUPIED",
    "COUNTY_LEVEL",
    "LAT",
    "LON",
)
HCV_FIELDS = ("GEOID", "HCV_PUBLIC")


def _write(frame: pd.DataFrame, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    frame.to_csv(part, index=False, lineterminator="\n")
    part.replace(dest)


def _record(source_id: str, url: str, dest: Path, manifest: Path | None) -> None:
    if manifest is not None:
        vintage = get_source(source_id).vintage
        mf.record(mf.entry_for_file(source_id, url, dest, vintage, root=mf.ROOT), manifest)


def _fetch_chas(fips: str, folder: Path, client: HttpClient, manifest: Path | None) -> Path:
    dest = folder / f"chas_{CHAS_VINTAGE}_{fips}.csv.gz"
    if dest.exists():
        return dest
    source = get_source("hud_chas")
    parts = []
    for code, level in CHAS_LEVELS.items():
        url = source.url(vintage=CHAS_VINTAGE, sumlevel=code, table="8")
        merged = None
        for table in CHAS_COLUMNS:
            raw = client.read_zip_member(url, f"{code}/Table{table}.csv")
            frame = read_chas_table(raw, table, fips, level)
            merged = frame if merged is None else merged.merge(frame, on=["level", "geoid"])
        parts.append(merged)
    out = pd.concat(parts, ignore_index=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    out.to_csv(part, index=False, lineterminator="\n", compression={"method": "gzip", "mtime": 0})
    part.replace(dest)
    _record("hud_chas", source.url(vintage=CHAS_VINTAGE, sumlevel="050", table="8"), dest, manifest)
    return dest


def _fetch_il(
    state: str, fips: str, folder: Path, client: HttpClient, manifest: Path | None
) -> Path:
    dest = folder / f"hud_il_fy{IL_FISCAL_YEAR}_{fips}.csv"
    if not dest.exists():
        yy = f"{IL_FISCAL_YEAR % 100:02d}"
        url = get_source("hud_income_limits").url(yy=yy)
        _write(read_income_limits(client.get_bytes(url), state), dest)
        _record("hud_income_limits", url, dest, manifest)
    return dest


def _fetch_layer(
    source_id: str,
    where: str,
    fields: tuple[str, ...],
    dest: Path,
    client: HttpClient,
    manifest: Path | None,
) -> Path:
    if not dest.exists():
        url = get_source(source_id).url()
        feats = fetch_features(client, url, where, ",".join(fields))
        frame = pd.DataFrame([f.get("attributes", {}) for f in feats]).reindex(columns=list(fields))
        frame.columns = [c.lower() for c in fields]
        frame["read_on"] = date.today().isoformat()
        _write(frame.sort_values(frame.columns[0]).reset_index(drop=True), dest)
        _record(source_id, url, dest, manifest)
    return dest


def fetch_affordability(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> AffordInputs:
    """Fetch (or reuse) every affordability extract for one state.

    A source that cannot be fetched is left ``None`` and named in ``notes``; the
    others still load. Cached extracts are reused as they are.

    Args:
        state: Two-letter postal code.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client; a copy naming itself with :data:`HUD_USER_AGENT` is used.
        manifest: The download manifest to record fetches in.
    """
    from visionhousing.data.sources.incentives import _fetch_lihtc

    fips = to_state_fips(state)
    postal = next(k for k, v in STATE_FIPS.items() if v == fips)
    folder = Path(cache) / FOLDER
    http = hud_client(client)
    out = AffordInputs()

    def attempt(name: str, func):
        try:
            return func()
        except Exception as err:  # one source failing must not stop the rest
            out.notes.append(f"{name}: {type(err).__name__}: {err}")
            return None

    path = attempt("hud_chas", lambda: _fetch_chas(fips, folder, http, manifest))
    if path is not None:
        out.chas = pd.read_csv(path, dtype={"geoid": str})
        out.files.append(("hud_chas", path))
    path = attempt("hud_income_limits", lambda: _fetch_il(postal, fips, folder, http, manifest))
    if path is not None:
        out.income_limits = pd.read_csv(path, dtype={"county": str})
        out.files.append(("hud_income_limits", path))
    layers = (
        ("mf_assisted", "hud_mf_assisted", f"STD_ST='{postal}'", MF_FIELDS),
        ("public_housing", "hud_public_housing", f"STD_ST='{postal}'", PH_FIELDS),
        ("hcv", "hud_hcv_tract", f"STATE='{fips}'", HCV_FIELDS),
    )
    for attr, sid, where, fields in layers:
        dest = folder / f"{sid}_{fips}.csv"
        path = attempt(
            sid, lambda s=sid, w=where, f=fields, d=dest: _fetch_layer(s, w, f, d, http, manifest)
        )
        if path is not None:
            frame = read_layer(path)
            setattr(out, attr, frame)
            out.files.append((sid, path))
            if attr == "mf_assisted" and "read_on" in frame.columns and len(frame):
                out.read_on = str(frame["read_on"].iloc[0])
    lihtc_folder = Path(cache) / "incentives"
    path = attempt("hud_lihtc", lambda: _fetch_lihtc(fips, lihtc_folder, http, manifest))
    if path is not None:
        out.lihtc = pd.read_csv(path, dtype={"hud_id": str, "county": str, "year_pis": str})
        out.files.append(("hud_lihtc", path))
    return out
