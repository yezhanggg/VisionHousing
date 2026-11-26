"""California APR tables and Census lot counts: download and stream into small frames.

Plain-language summary
----------------------
California cities and counties file a Housing Element Annual Progress Report
(APR) with the state's Department of Housing and Community Development (HCD)
every year. HCD publishes the reports on the California Open Data Portal as one
CSV per table, for every jurisdiction and year from 2018:

- **Table A**: housing development applications received in the year, with the
  date each was submitted, its unit category, its approval path (ministerial or
  discretionary, and any streamlining law used) and its status.
- **Table A2**: projects that reached a stage in the year: entitlement approved,
  building permit issued, certificate of occupancy (completion), with the date
  and the number of homes at each stage.

Both files are large (about 90 MB and 300 MB). They are downloaded once into the
local cache (``data/raw/hcd_apr/``, never into the repository) and then read in
chunks of rows, keeping only the dozen columns the study needs. No address,
project name or note text is kept after reading.

The denominator for ADU rates is the American Community Survey count of
one-unit detached homes (table B25024, cell 002), read from the Census Bureau's
keyless summary file by streaming it and keeping only California rows.

Settings: ``assumptions/validation_california.yaml``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources.acs import parse_summary_table, stream_filter

__all__ = [
    "SETTINGS_PATH",
    "TABLE_A2_COLUMNS",
    "TABLE_A_COLUMNS",
    "AprSettings",
    "fetch_apr",
    "fetch_lots",
    "load_settings",
    "parse_place_lots",
    "read_table_a",
    "read_table_a2",
]

SETTINGS_PATH = mf.ROOT / "assumptions" / "validation_california.yaml"

TABLE_A_COLUMNS = [
    "JURIS_NAME",
    "YEAR",
    "APN",
    "UNIT_CAT",
    "APP_SUBMIT_DT",
    "TOT_PROPOSED_UNITS",
    "APP_SUBMITTED_SB35",
    "APPLICATION_STATUS",
    "PROJECT_TYPE",
]
TABLE_A2_COLUMNS = [
    "JURIS_NAME",
    "YEAR",
    "APN",
    "UNIT_CAT",
    "ENT_APPROVE_DT1",
    "NO_ENTITLEMENTS",
    "BP_ISSUE_DT1",
    "NO_BUILDING_PERMITS",
    "CO_ISSUE_DT1",
    "NO_OTHER_FORMS_OF_READINESS",
    "APPROVE_SB35",
]


@dataclass(frozen=True)
class AprSettings:
    """Settings read from ``assumptions/validation_california.yaml`` (kept as a mapping)."""

    raw: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    @property
    def first_year(self) -> int:
        """First reporting year used."""
        return int(self.raw["first_year"])

    @property
    def last_year(self) -> int:
        """Last complete reporting year used."""
        return int(self.raw["last_year"])

    @property
    def years(self) -> list[int]:
        """Every reporting year used, in order."""
        return list(range(self.first_year, self.last_year + 1))

    @property
    def censor_date(self) -> date:
        """End of the follow-up: stages after this cannot be in the download yet."""
        return _as_date(self.raw["censor_date"])

    @property
    def earliest_valid_date(self) -> date:
        """Dates before this are treated as typing errors."""
        return _as_date(self.raw["earliest_valid_date"])


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def load_settings(path: Path = SETTINGS_PATH) -> AprSettings:
    """Read the study settings."""
    return AprSettings(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- fetch


def _apr_path(cache: Path, source_id: str) -> Path:
    return Path(cache) / "hcd_apr" / f"{source_id}.csv"


def fetch_apr(
    cache: Path,
    settings: AprSettings,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> dict[str, Path]:
    """Download Table A and Table A2 into the cache (once); returns their paths."""
    client = client or HttpClient(timeout=600)
    out: dict[str, Path] = {}
    for key in ("table_a", "table_a2"):
        source = get_source(settings["sources"][key])
        dest = _apr_path(cache, source.id)
        if refresh or not dest.exists():
            client.download(source.url(), dest)
            if manifest is not None:
                mf.record(
                    mf.entry_for_file(source.id, source.url(), dest, source.vintage, root=mf.ROOT),
                    manifest,
                )
        out[key] = dest
    return out


def _prefix_keep(prefixes: tuple[str, ...]):
    wanted = tuple(p.encode("ascii") for p in prefixes)

    def make(header: bytes):
        names = header.rstrip(b"\r\n").split(b"|")
        idx = names.index(b"GEO_ID")

        def keep(line: bytes) -> bool:
            parts = line.split(b"|", idx + 1)
            return len(parts) > idx and parts[idx].startswith(wanted)

        return keep

    return make


def fetch_lots(
    cache: Path,
    settings: AprSettings,
    *,
    client: HttpClient | None = None,
    refresh: bool = False,
) -> dict[str, Path]:
    """Stream the ACS B25024 table and the geography list, keeping California rows only."""
    client = client or HttpClient(timeout=600)
    fips = str(settings["state_fips"])
    prefixes = (f"0400000US{fips}", f"1600000US{fips}")
    folder = Path(cache) / "hcd_apr"
    jobs = {
        "table": (
            get_source(settings["sources"]["acs_table"]).url(table=settings["acs_table_name"]),
            folder / f"acs_{settings['acs_table_name']}_{fips}.dat",
        ),
        "geos": (
            get_source(settings["sources"]["acs_geos"]).url(),
            folder / f"acs_geos_{fips}.txt",
        ),
    }
    out: dict[str, Path] = {}
    for key, (url, dest) in jobs.items():
        if refresh or not dest.exists():
            stream_filter(client, url, dest, _prefix_keep(prefixes))
        out[key] = dest
    return out


def parse_place_lots(table: str | Path, geos: str | Path, cell: str) -> pd.DataFrame:
    """One row per California place and the state: ``geo_id, level, name, juris, lots``.

    ``juris`` is the place name in APR spelling: upper case, without the
    trailing " city" / " town" and the state ("Los Angeles city, California"
    becomes "LOS ANGELES"). The state row has ``juris`` = ``"CALIFORNIA"``.
    """
    values = parse_summary_table(Path(table) if isinstance(table, Path) else table)
    gtext = Path(geos).read_text(encoding="latin-1") if isinstance(geos, Path) else geos
    names = pd.read_csv(StringIO(gtext), sep="|", dtype=str, keep_default_na=False)
    names = names[names.get("COMPONENT", "00") == "00"][["GEO_ID", "NAME"]]
    frame = values[["GEO_ID", cell]].merge(names, on="GEO_ID", how="left")
    frame["level"] = frame["GEO_ID"].str[:3].map({"040": "state", "160": "place"})
    base = frame["NAME"].fillna("").str.split(",").str[0].str.strip()
    is_city = base.str.contains(r" (?:city|town)$", regex=True)
    juris = base.str.replace(r" (city|town)$", "", regex=True).str.upper()
    juris[frame["level"] == "state"] = "CALIFORNIA"
    frame["juris"] = juris
    frame["incorporated"] = is_city | (frame["level"] == "state")
    frame = frame.rename(columns={"GEO_ID": "geo_id", "NAME": "name", cell: "lots"})
    return frame[["geo_id", "level", "name", "juris", "incorporated", "lots"]].reset_index(
        drop=True
    )


# --------------------------------------------------------------------------- streaming reads


def _chunks(path: Path | str, columns: list[str], rows: int) -> Iterator[pd.DataFrame]:
    """Read only ``columns`` of a CSV, ``rows`` at a time, every value as text."""
    yield from pd.read_csv(
        path,
        usecols=lambda c: c in columns,
        dtype=str,
        chunksize=rows,
        keep_default_na=False,
        na_values=[""],
    )


def _dates(series: pd.Series, settings: AprSettings) -> pd.Series:
    d = pd.to_datetime(series, errors="coerce", format="mixed").dt.normalize()
    lo = pd.Timestamp(settings.earliest_valid_date)
    hi = pd.Timestamp(settings.censor_date)
    return d.where((d >= lo) & (d <= hi))


def _ints(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).clip(lower=0).astype("int32")


def apn_key(series: pd.Series, min_digits: int) -> pd.Series:
    """Parcel number with every non-digit removed; blank when too short to join on."""
    digits = series.fillna("").astype(str).str.replace(r"\D", "", regex=True)
    return digits.where(digits.str.len() >= min_digits, "")


def _text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def read_table_a(path: Path | str, settings: AprSettings) -> pd.DataFrame:
    """Stream Table A into one compact row per application.

    Columns: ``juris, year, apn, unit_cat, app_date, units, streamlining, status,
    project_type``. Rows outside the study years or without a valid date are dropped.
    """
    min_digits = int(settings["link"]["min_apn_digits"])
    parts = []
    for chunk in _chunks(path, TABLE_A_COLUMNS, int(settings["chunk_rows"])):
        part = pd.DataFrame(
            {
                "juris": _text(chunk["JURIS_NAME"]).str.upper(),
                "year": pd.to_numeric(chunk["YEAR"], errors="coerce"),
                "apn": apn_key(chunk["APN"], min_digits),
                "unit_cat": _text(chunk["UNIT_CAT"]),
                "app_date": _dates(chunk["APP_SUBMIT_DT"], settings),
                "units": _ints(chunk["TOT_PROPOSED_UNITS"]),
                "streamlining": _text(chunk["APP_SUBMITTED_SB35"]),
                "status": _text(chunk["APPLICATION_STATUS"]),
                "project_type": _text(chunk.get("PROJECT_TYPE", pd.Series(dtype=str))),
            }
        )
        keep = part["year"].between(settings.first_year, settings.last_year)
        parts.append(part[keep & part["app_date"].notna()])
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not out.empty:
        out["year"] = out["year"].astype("int16")
        for col in ("juris", "unit_cat", "streamlining", "status", "project_type"):
            out[col] = out[col].astype("category")
    return out


def read_table_a2(path: Path | str, settings: AprSettings) -> pd.DataFrame:
    """Stream Table A2 into one compact row per project-year.

    Columns: ``juris, year, apn, unit_cat, ent_date, ent_units, bp_date, bp_units,
    co_date, co_units, streamlining``.
    """
    min_digits = int(settings["link"]["min_apn_digits"])
    parts = []
    for chunk in _chunks(path, TABLE_A2_COLUMNS, int(settings["chunk_rows"])):
        part = pd.DataFrame(
            {
                "juris": _text(chunk["JURIS_NAME"]).str.upper(),
                "year": pd.to_numeric(chunk["YEAR"], errors="coerce"),
                "apn": apn_key(chunk["APN"], min_digits),
                "unit_cat": _text(chunk["UNIT_CAT"]),
                "ent_date": _dates(chunk["ENT_APPROVE_DT1"], settings),
                "ent_units": _ints(chunk["NO_ENTITLEMENTS"]),
                "bp_date": _dates(chunk["BP_ISSUE_DT1"], settings),
                "bp_units": _ints(chunk["NO_BUILDING_PERMITS"]),
                "co_date": _dates(chunk["CO_ISSUE_DT1"], settings),
                "co_units": _ints(chunk["NO_OTHER_FORMS_OF_READINESS"]),
                "streamlining": _text(chunk["APPROVE_SB35"]),
            }
        )
        parts.append(part[part["year"].between(settings.first_year, settings.last_year)])
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not out.empty:
        out["year"] = out["year"].astype("int16")
        for col in ("juris", "unit_cat", "streamlining"):
            out[col] = out[col].astype("category")
    return out
