"""Philadelphia permit-stage dates: zoning permit issued, building permit issued.

Plain-language summary
----------------------
The City of Philadelphia's open permit table (Carto table ``permits``, run by
the Department of Licenses and Inspections) gives each permit an **issue
date** but no **application date**. So the time from applying to being
approved cannot be measured from it. Each new-construction building permit
does name the zoning permit it was built on (``zoningpermitjobid``), and that
zoning permit has its own issue date. The gap between the two issue dates is a
real, observed stage of the approval path: building plans prepared, submitted
and reviewed after zoning was settled.

This module sends two queries and keeps only dates, unit counts and yes/no
flags (no address, owner, contractor or scope text):

- :func:`building_query`: new-construction building permits issued from the
  first year, their homes, an ADU-like flag and the issue date of the zoning
  permit they name.
- :func:`zoning_query`: zoning permits for new construction issued from the
  first year, a "new structure, not an addition" flag worked out on the City's
  server, and the first new-construction building permit issued on each (blank
  when none yet). Blank rows are the still-open cases used for right-censoring.

The settings (permit types, text patterns, years) live in
``assumptions/permit_timing.yaml`` and ``assumptions/pipeline_local.yaml``.
"""

# Queries are built from fixed text and values quoted by opendata.sql_text/sql_list.
# ruff: noqa: S608

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.sources.opendata import (
    CARTO_URL,
    carto_sql,
    read_extract,
    save_extract,
    sql_list,
    sql_text,
)

__all__ = [
    "SETTINGS_PATH",
    "SOURCE_ID",
    "TERMS_URL",
    "TimingSettings",
    "building_query",
    "fetch_timing_extracts",
    "load_settings",
    "query_hash",
    "zoning_query",
]

SOURCE_ID = "phl_li_permits"
TERMS_URL = (
    "https://opendataphilly.org/datasets/licenses-and-inspections-building-and-zoning-permits/"
)
SETTINGS_PATH = mf.ROOT / "assumptions" / "permit_timing.yaml"
PIPELINE_PATH = mf.ROOT / "assumptions" / "pipeline_local.yaml"


@dataclass(frozen=True)
class TimingSettings:
    """Settings read from ``assumptions/permit_timing.yaml`` (plus building types)."""

    first_year: int
    last_year: int
    building_types: list[str]
    building_work_types: list[str]
    adu_pattern: str
    adu_permit_types: list[str]
    zoning_permit_types: list[str]
    zoning_work_types: list[str]
    new_structure_pattern: str
    addition_pattern: str
    unit_groups: dict[str, tuple[int, int | None]]
    quantiles: list[float]
    horizons_days: list[int]
    closed_statuses: list[str]


def load_settings(
    path: Path = SETTINGS_PATH, pipeline_path: Path = PIPELINE_PATH
) -> TimingSettings:
    """Read the timing settings and the new-home building permit types."""
    data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    pipe = yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8"))["philadelphia"]
    groups = {
        str(k): (int(v[0]), None if v[1] is None else int(v[1]))
        for k, v in data["unit_groups"].items()
    }
    return TimingSettings(
        first_year=int(data["first_year"]),
        last_year=int(data["last_year"]),
        building_types=list(pipe["permit_types"]),
        building_work_types=list(pipe["work_types"]),
        adu_pattern=str(data["adu_pattern"]),
        adu_permit_types=list(data["adu_permit_types"]),
        zoning_permit_types=list(data["zoning_permit_types"]),
        zoning_work_types=list(data["zoning_work_types"]),
        new_structure_pattern=str(data["new_structure_pattern"]),
        addition_pattern=str(data["addition_pattern"]),
        unit_groups=groups,
        quantiles=[float(q) for q in data["quantiles"]],
        horizons_days=[int(h) for h in data["horizons_days"]],
        closed_statuses=[str(x).lower() for x in data["closed_statuses"]],
    )


def _first_zoning(s: TimingSettings) -> str:
    """Sub-query: the first issue date of each zoning job."""
    return (
        "SELECT posse_jobid, min(permitissuedate)::date AS zoning_issued FROM permits "
        f"WHERE permittype IN {sql_list(s.zoning_permit_types)} GROUP BY posse_jobid"
    )


def building_query(s: TimingSettings) -> str:
    """New-home (and ADU-like) building permits with the issue date of their zoning permit."""
    adu = f"(b.approvedscopeofwork ~* {sql_text(s.adu_pattern)})"
    return (
        "SELECT b.permitissuedate::date AS issued, b.permittype, b.typeofwork, "
        f"coalesce(b.numberofunits, 0) AS units, coalesce({adu}, false) AS adu_like, "
        "z.zoning_issued "
        f"FROM permits b LEFT JOIN ({_first_zoning(s)}) z "
        "ON z.posse_jobid = b.zoningpermitjobid "
        f"WHERE b.permitissuedate >= '{int(s.first_year)}-01-01' AND ("
        f"(b.permittype IN {sql_list(s.building_types)} "
        f"AND b.typeofwork IN {sql_list(s.building_work_types)}) "
        f"OR (b.permittype IN {sql_list(s.adu_permit_types)} AND {adu}))"
    )


def zoning_query(s: TimingSettings) -> str:
    """New-construction zoning permits and the first building permit issued on each."""
    scope = "coalesce(z.approvedscopeofwork, '')"
    return (
        "SELECT z.permitissuedate::date AS zoning_issued, z.status, "
        f"({scope} ~* {sql_text(s.new_structure_pattern)} "
        f"AND NOT {scope} ~* {sql_text(s.addition_pattern)}) AS new_structure, "
        "b.building_issued "
        "FROM permits z LEFT JOIN ("
        "SELECT zoningpermitjobid, min(permitissuedate)::date AS building_issued FROM permits "
        f"WHERE permittype IN {sql_list(s.building_types)} "
        f"AND typeofwork IN {sql_list(s.building_work_types)} GROUP BY zoningpermitjobid"
        ") b ON b.zoningpermitjobid = z.posse_jobid "
        f"WHERE z.permittype IN {sql_list(s.zoning_permit_types)} "
        f"AND z.typeofwork IN {sql_list(s.zoning_work_types)} "
        f"AND z.permitissuedate >= '{int(s.first_year)}-01-01'"
    )


def query_hash(text: str) -> str:
    """SHA-256 of a query's text, so a stored table names exactly what was asked."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_timing_extracts(
    cache: Path,
    settings: TimingSettings,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Download (or reuse) the two extracts; returns ``{"building": ..., "zoning": ...}``.

    Each extract carries a ``read_on`` column: the date the City's table was read.
    """
    client = client or HttpClient(timeout=300)
    out: dict[str, pd.DataFrame] = {}
    for name, query in (
        ("building", building_query(settings)),
        ("zoning", zoning_query(settings)),
    ):
        dest = Path(cache) / "local" / "phl" / f"permit_timing_{name}.csv"
        if refresh or not dest.exists():
            frame = carto_sql(query, client)
            frame["read_on"] = date.today().isoformat()
            save_extract(
                frame,
                dest,
                source_id=SOURCE_ID,
                url=CARTO_URL,
                vintage=date.today().isoformat(),
                manifest=manifest,
            )
        out[name] = read_extract(dest)
    return out
