"""Philadelphia safety and distress records, counted on the City's server (no key needed).

Plain-language summary
----------------------
The FBI's crime API needs a key. The City of Philadelphia publishes its own
records through its Carto SQL service with no key, under the City of
Philadelphia open-data licence (OpenDataPhilly terms of use). This module
reads four of them:

=================== ============================ ======================================
measure             Carto table                  what is counted
=================== ============================ ======================================
shootings           ``shootings``                shooting victims (and how many died)
violent, property   ``incidents_part1_part2``    police incidents by UCR code
li_cases            ``violations``               L&I code-violation cases (one per case)
requests_311        ``public_cases_fc``          311 requests about neglect of a block
=================== ============================ ======================================

Which codes, case types and request types count is set in
``assumptions/safety_local.yaml``.

**Nothing personal leaves the City's server.** Each query groups the records
by calendar year and by a small location cell (the point rounded to 4
decimals of a degree, about 10 metres) and returns only the count per cell.
No record, name, address or case number is downloaded or kept. The cells are
placed in 2020 tracts later (:mod:`visionhousing.features.safety_local`);
records with no location come back as one cell with an empty location and
are counted in the city total only.

One query is sent per table and year, so no answer is large and a busy
server can be retried year by year. Answers are cached under
``data/raw/local/phl/``; a later build reads the cache.
"""

# Queries are built from fixed text and values quoted by opendata.sql_text/sql_list.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.sources.opendata import (
    CARTO_URL,
    carto_sql,
    read_extract,
    save_extract,
    sql_list,
)

__all__ = [
    "DATASETS",
    "FILES",
    "SOURCES",
    "fetch_phl_safety",
    "queries",
    "years_to_read",
]

SOURCES = {
    "shootings": "phl_shootings",
    "incidents": "phl_crime_incidents",
    "violations": "phl_li_violations",
    "requests_311": "phl_311_requests",
}
"""Dataset -> source id in ``sources.yaml``."""

DATASETS = tuple(SOURCES)
FILES = {name: f"safety_{name}.csv" for name in DATASETS}
"""Dataset -> cache file name under ``data/raw/local/phl/``."""

COUNT_COLUMNS = {
    "shootings": ("shootings", "shootings_fatal"),
    "incidents": ("violent", "property"),
    "violations": ("li_cases",),
    "requests_311": ("requests_311",),
}
"""Dataset -> the count columns its extract carries."""


def _cell(geom: str, decimals: int) -> str:
    d = int(decimals)
    return f"round(ST_Y({geom})::numeric, {d}) AS lat, round(ST_X({geom})::numeric, {d}) AS lon"


def queries(year: int, rules: Mapping[str, Any], decimals: int) -> dict[str, str]:
    """The four Carto queries for one calendar year (counts per location cell).

    Args:
        year: Calendar year.
        rules: The ``philadelphia`` part of ``assumptions/safety_local.yaml``.
        decimals: Decimals the location is rounded to.
    """
    y, nxt = int(year), int(year) + 1
    cell = _cell("the_geom", decimals)
    violent, prop = list(rules["violent_ucr"]), list(rules["property_ucr"])
    return {
        "shootings": (
            f"SELECT {y} AS year, {cell}, count(*) AS shootings, "
            "sum(CASE WHEN coalesce(fatal::numeric, 0) > 0 THEN 1 ELSE 0 END) AS shootings_fatal "
            f"FROM shootings WHERE year::int = {y} GROUP BY 2, 3"
        ),
        "incidents": (
            f"SELECT {y} AS year, {cell}, "
            f"sum(CASE WHEN ucr_general IN {sql_list(violent)} THEN 1 ELSE 0 END) AS violent, "
            f"sum(CASE WHEN ucr_general IN {sql_list(prop)} THEN 1 ELSE 0 END) AS property "
            "FROM incidents_part1_part2 "
            f"WHERE dispatch_date >= '{y}-01-01' AND dispatch_date < '{nxt}-01-01' "
            f"AND ucr_general IN {sql_list(violent + prop)} GROUP BY 2, 3"
        ),
        "violations": (
            f"SELECT {y} AS year, {cell}, count(DISTINCT casenumber) AS li_cases "
            "FROM violations "
            f"WHERE casecreateddate >= '{y}-01-01' AND casecreateddate < '{nxt}-01-01' "
            f"AND casetype IN {sql_list(rules['li_case_types'])} GROUP BY 2, 3"
        ),
        "requests_311": (
            f"SELECT {y} AS year, {cell}, count(*) AS requests_311 FROM public_cases_fc "
            f"WHERE requested_datetime >= '{y}-01-01' AND requested_datetime < '{nxt}-01-01' "
            f"AND service_name IN {sql_list(rules['requests_311'])} GROUP BY 2, 3"
        ),
    }


def years_to_read(first_year: int, today: date | None = None) -> list[int]:
    """Full calendar years from ``first_year`` to last year."""
    last = (today or date.today()).year - 1
    return list(range(int(first_year), last + 1))


def _numbers(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    out = frame.copy()
    for c in ("year", "lat", "lon", *COUNT_COLUMNS[dataset]):
        if c not in out.columns:
            out[c] = pd.NA
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def fetch_phl_safety(
    cache: Path,
    *,
    rules: Mapping[str, Any],
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
    today: date | None = None,
) -> dict[str, pd.DataFrame]:
    """Download (or reuse) the four count extracts.

    Args:
        cache: The raw-data cache (``data/raw``).
        rules: The whole ``assumptions/safety_local.yaml``.
        client: HTTP client.
        manifest: Download manifest, or ``None``.
        refresh: Download again even when cached.
        today: The build date (full years end the year before).

    Returns:
        Dataset name -> table with ``year``, ``lat``, ``lon``, the count
        columns and ``read_on``. A dataset that could not be read is missing
        from the result only when it raised; the caller records why.
    """
    folder = Path(cache) / "local" / "phl"
    years = years_to_read(int(rules["first_year"]), today)
    decimals = int(rules["grid_decimals"])
    out: dict[str, pd.DataFrame] = {}
    for dataset in DATASETS:
        dest = folder / FILES[dataset]
        if refresh or not dest.exists():
            client = client or HttpClient(timeout=300)
            parts = [
                carto_sql(queries(y, rules["philadelphia"], decimals)[dataset], client)
                for y in years
            ]
            frame = pd.concat([p for p in parts if len(p)], ignore_index=True)
            read_on = (today or date.today()).isoformat()
            frame["read_on"] = read_on
            save_extract(
                frame,
                dest,
                source_id=SOURCES[dataset],
                url=CARTO_URL,
                vintage=read_on,
                manifest=manifest,
            )
        out[dataset] = _numbers(read_extract(dest), dataset)
    return out
