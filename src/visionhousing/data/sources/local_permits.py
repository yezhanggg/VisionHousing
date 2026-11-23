"""City building permits for new homes: Philadelphia L&I and Pittsburgh PLI.

Plain-language summary
----------------------
The Census Bureau's permit survey counts homes by county only. Two cities
publish their own permit records with locations, which lets the profile show
where inside the city new homes are being permitted:

- **Philadelphia** Department of Licenses and Inspections (L&I) building
  permits, through the City's Carto SQL service (table ``permits``). New
  construction building permits carry the number of homes
  (``numberofunits``).
- **Pittsburgh** Department of Permits, Licenses and Inspections (PLI),
  through WPRDC (CC-BY 4.0). The file has no unit counts, so only permits are
  counted.

Which permit types count as new homes is set in
``assumptions/pipeline_local.yaml``. Only the location (rounded to 5
decimals), year and units of each permit are kept; no owner, contractor or
address.
"""

# Queries are built from fixed text and values quoted by opendata.sql_text/sql_list.
# ruff: noqa: S608

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.sources.opendata import (
    CARTO_URL,
    CKAN_SQL_URL,
    carto_sql,
    ckan_sql,
    read_extract,
    save_extract,
    sql_list,
    sql_text,
)

__all__ = [
    "PGH_PERMITS",
    "fetch_pgh_permits",
    "fetch_phl_permits",
    "pgh_query",
    "phl_query",
]

PHL_SOURCE = "phl_li_permits"
PGH_SOURCE = "pgh_pli_permits"
PGH_PERMITS = "f4d1177a-f597-4c32-8cbf-7885f56253f6"
"""WPRDC resource id: City of Pittsburgh PLI permits."""


def phl_query(permit_types: list[str], work_types: list[str], first_year: int) -> str:
    """Philadelphia new-construction building permits: location, year and homes."""
    return (
        "SELECT round(ST_Y(the_geom)::numeric, 5) AS lat, "
        "round(ST_X(the_geom)::numeric, 5) AS lon, "
        "extract(year FROM permitissuedate)::int AS year, coalesce(numberofunits, 0) AS units "
        f"FROM permits WHERE permittype IN {sql_list(permit_types)} "
        f"AND typeofwork IN {sql_list(work_types)} "
        f"AND permitissuedate >= '{int(first_year)}-01-01'"
    )


def pgh_query(permit_types: list[str], work_type_like: str, use: str, first_year: int) -> str:
    """Pittsburgh residential new-construction permits: location and year."""
    return (
        'SELECT round("latitude"::numeric, 5) AS lat, round("longitude"::numeric, 5) AS lon, '
        'substring("issue_date"::text, 1, 4) AS year '
        f'FROM "{PGH_PERMITS}" WHERE "permit_type" IN {sql_list(permit_types)} '
        f'AND "work_type" ILIKE {sql_text("%" + work_type_like + "%")} '
        f'AND "commercial_or_residential" = {sql_text(use)} '
        f'AND "issue_date" >= {sql_text(f"{int(first_year)}-01-01")}'
    )


def _numbers(frame: pd.DataFrame) -> pd.DataFrame:
    for c in ("lat", "lon", "year", "units"):
        if c in frame.columns:
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
    return frame


def fetch_phl_permits(
    cache: Path,
    *,
    permit_types: list[str],
    work_types: list[str],
    first_year: int,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> pd.DataFrame:
    """Download (or reuse) Philadelphia new-home permits: ``lat``, ``lon``, ``year``, ``units``."""
    dest = Path(cache) / "local" / "phl" / "new_home_permits.csv"
    if refresh or not dest.exists():
        frame = carto_sql(
            phl_query(permit_types, work_types, first_year), client or HttpClient(timeout=300)
        )
        frame["read_on"] = date.today().isoformat()
        save_extract(
            frame,
            dest,
            source_id=PHL_SOURCE,
            url=CARTO_URL,
            vintage=date.today().isoformat(),
            manifest=manifest,
        )
    return _numbers(read_extract(dest))


def fetch_pgh_permits(
    cache: Path,
    *,
    permit_types: list[str],
    work_type_like: str,
    use: str,
    first_year: int,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> pd.DataFrame:
    """Download (or reuse) Pittsburgh new-home permits: ``lat``, ``lon``, ``year``."""
    dest = Path(cache) / "local" / "pittsburgh" / "new_home_permits.csv"
    if refresh or not dest.exists():
        q = pgh_query(permit_types, work_type_like, use, first_year)
        frame = ckan_sql(q, client or HttpClient(timeout=300))
        frame["read_on"] = date.today().isoformat()
        save_extract(
            frame,
            dest,
            source_id=PGH_SOURCE,
            url=CKAN_SQL_URL,
            vintage=date.today().isoformat(),
            manifest=manifest,
        )
    return _numbers(read_extract(dest))
