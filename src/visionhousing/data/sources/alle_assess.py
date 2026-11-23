"""Allegheny County property assessments and sales (WPRDC), added up on the publisher's server.

Plain-language summary
----------------------
Allegheny County's Office of Property Assessments publishes every parcel's
assessment record (about 585,000 parcels) and every recorded sale, through the
Western Pennsylvania Regional Data Center (WPRDC), under a public-domain
dedication (CC0). WPRDC also publishes each parcel's centre point with its
2020 census tract (CC0). Their datastore answers SQL, so this module asks it
for **totals by tract**, joining the three tables on the parcel id on WPRDC's
own server. No owner name and no address is downloaded.

- :func:`fetch_use_counts`: parcels per tract and land use (``CLASSDESC``,
  ``USEDESC``), plus county totals that include parcels with no centre point.
- :func:`fetch_single_family`: per tract and for the county, the
  single-family parcels, their median fair market value and lot area, counts
  by year built, and three owner counts (owner type "CORPORATION", no
  homestead exemption, tax-bill mailing address outside Pennsylvania).
- :func:`fetch_sales`: valid single-family sales in the last 12 and 36 months
  and their median price, per tract and for the county.

The land-use list, year groups, sale codes and owner types are read from
``assumptions/parcels.yaml`` and ``assumptions/owners.yaml``.
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
    CKAN_SQL_URL,
    ckan_sql,
    read_extract,
    save_extract,
    sql_list,
    sql_text,
)

__all__ = [
    "ASSESS",
    "CENTROIDS",
    "SALES",
    "fetch_sales",
    "fetch_single_family",
    "fetch_use_counts",
    "months_back",
    "sales_query",
    "single_family_query",
    "use_counts_query",
]

ASSESS = "65855e14-549e-4992-b5be-d629afc676fa"
"""WPRDC resource id: Allegheny County property assessments."""
CENTROIDS = "3fab7152-3f11-4788-8372-4c33f86ea813"
"""WPRDC resource id: parcel centroids with geographic identifiers (March 2025)."""
SALES = "5bbe6c55-bce6-4edb-9d04-68edeb6bf7b1"
"""WPRDC resource id: Allegheny County real estate sales."""

ASSESS_SOURCE = "alle_assessments"
CENTROID_SOURCE = "alle_parcel_centroids"
SALES_SOURCE = "alle_property_sales"


def months_back(day: date, months: int) -> date:
    """The same day ``months`` calendar months earlier (clipped to the month's end)."""
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = m + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(y, month, d)
        except ValueError:
            continue
    raise ValueError(day)  # pragma: no cover


def _tract_join(alias: str = "a") -> str:
    return f'JOIN "{CENTROIDS}" c ON {alias}."PARID" = c."PIN"'


def use_counts_query(by_tract: bool) -> str:
    """Parcels by land use, per 2020 tract (``by_tract``) or for the whole county."""
    if by_tract:
        return (
            'SELECT lpad(c."GEOID"::text, 11, \'0\') AS tract, a."CLASSDESC" AS class, '
            'a."USEDESC" AS use, count(*) AS parcels '
            f'FROM "{ASSESS}" a {_tract_join()} GROUP BY 1, 2, 3'
        )
    return (
        'SELECT a."CLASSDESC" AS class, a."USEDESC" AS use, count(*) AS parcels '
        f'FROM "{ASSESS}" a GROUP BY 1, 2'
    )


def _year_case(groups: dict[str, list[int]]) -> str:
    parts = []
    for name, (lo, hi) in groups.items():
        cond = f'a."YEARBLT" BETWEEN {int(lo)} AND {int(hi)}'
        if int(lo) == 0:
            cond = f'a."YEARBLT" > 0 AND a."YEARBLT" <= {int(hi)}'
        parts.append(f"sum(CASE WHEN {cond} THEN 1 ELSE 0 END) AS sf_built_{name}")
    parts.append(
        'sum(CASE WHEN a."YEARBLT" IS NULL OR a."YEARBLT" <= 0 THEN 1 ELSE 0 END) '
        "AS sf_built_unknown"
    )
    return ", ".join(parts)


def single_family_query(
    single_family_uses: list[str],
    year_groups: dict[str, list[int]],
    entity_owner_types: list[str],
    home_state: str,
    by_tract: bool,
) -> str:
    """Single-family parcel totals: count, medians, year built and owner counts."""
    owner = " OR ".join(f'a."OWNERDESC" LIKE {sql_text(t + "%")}' for t in entity_owner_types)
    state = sql_text(rf"\m{home_state.upper()} *$")
    select = (
        "count(*) AS sf_parcels, "
        'percentile_cont(0.5) WITHIN GROUP (ORDER BY a."FAIRMARKETTOTAL") AS sf_median_value, '
        'percentile_cont(0.5) WITHIN GROUP (ORDER BY a."LOTAREA") AS sf_median_lot_sqft, '
        f"{_year_case(year_groups)}, "
        f"sum(CASE WHEN {owner} THEN 1 ELSE 0 END) AS sf_entity, "
        'sum(CASE WHEN a."HOMESTEADFLAG" IS NULL OR a."HOMESTEADFLAG" <> \'HOM\' '
        "THEN 1 ELSE 0 END) AS sf_non_homestead, "
        "sum(CASE WHEN a.\"CHANGENOTICEADDRESS3\" ~* '[A-Z]{2} *$' "
        f'AND a."CHANGENOTICEADDRESS3" !~* {state} THEN 1 ELSE 0 END) AS sf_out_of_state'
    )
    where = f'WHERE a."USEDESC" IN {sql_list(single_family_uses)}'
    if by_tract:
        return (
            f"SELECT lpad(c.\"GEOID\"::text, 11, '0') AS tract, {select} "
            f'FROM "{ASSESS}" a {_tract_join()} {where} GROUP BY 1'
        )
    return f'SELECT {select} FROM "{ASSESS}" a {where}'


def sales_query(
    single_family_uses: list[str],
    valid_codes: list[str],
    since_long: date,
    since_short: date,
    by_tract: bool,
) -> str:
    """Valid single-family sales since each cut-off date and their median prices."""
    short = sql_text(since_short.isoformat())
    select = (
        "count(*) AS sales_long, "
        f'sum(CASE WHEN s."SALEDATE" >= {short} THEN 1 ELSE 0 END) AS sales_short, '
        'percentile_cont(0.5) WITHIN GROUP (ORDER BY s."PRICE") AS median_sale_long, '
        'percentile_cont(0.5) WITHIN GROUP (ORDER BY s."PRICE") '
        f'FILTER (WHERE s."SALEDATE" >= {short}) '
        "AS median_sale_short"
    )
    body = (
        f'FROM "{SALES}" s JOIN "{ASSESS}" a ON a."PARID" = s."PARID" '
        + (_tract_join() + " " if by_tract else "")
        + f'WHERE s."SALECODE" IN {sql_list(valid_codes)} '
        f'AND s."SALEDATE" >= {sql_text(since_long.isoformat())} '
        f'AND a."USEDESC" IN {sql_list(single_family_uses)}'
    )
    if by_tract:
        return f"SELECT lpad(c.\"GEOID\"::text, 11, '0') AS tract, {select} {body} GROUP BY 1"
    return f"SELECT {select} {body}"


def _cached(dest: Path, source_id: str, make, manifest: Path | None, refresh: bool) -> pd.DataFrame:
    if refresh or not dest.exists():
        frame = make()
        frame["read_on"] = date.today().isoformat()
        save_extract(
            frame,
            dest,
            source_id=source_id,
            url=CKAN_SQL_URL,
            vintage=date.today().isoformat(),
            manifest=manifest,
        )
    return read_extract(dest)


def fetch_use_counts(
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> pd.DataFrame:
    """Parcels by land use: one row per tract and land use, county rows with ``tract`` empty."""
    client = client or HttpClient(timeout=300)
    folder = Path(cache) / "local" / "allegheny"

    def make() -> pd.DataFrame:
        tracts = ckan_sql(use_counts_query(True), client, order_by="1, 2, 3")
        county = ckan_sql(use_counts_query(False), client).assign(tract="")
        return pd.concat([tracts, county], ignore_index=True)

    return _cached(folder / "use_counts.csv", ASSESS_SOURCE, make, manifest, refresh)


def fetch_single_family(
    cache: Path,
    *,
    single_family_uses: list[str],
    year_groups: dict[str, list[int]],
    entity_owner_types: list[str],
    home_state: str,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> pd.DataFrame:
    """Single-family totals per tract, plus one county row (``tract`` empty)."""
    client = client or HttpClient(timeout=300)
    folder = Path(cache) / "local" / "allegheny"

    def make() -> pd.DataFrame:
        args = (single_family_uses, year_groups, entity_owner_types, home_state)
        tracts = ckan_sql(single_family_query(*args, by_tract=True), client)
        county = ckan_sql(single_family_query(*args, by_tract=False), client).assign(tract="")
        return pd.concat([tracts, county], ignore_index=True)

    return _cached(folder / "single_family.csv", ASSESS_SOURCE, make, manifest, refresh)


def fetch_sales(
    cache: Path,
    *,
    single_family_uses: list[str],
    valid_codes: list[str],
    windows: tuple[int, int],
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
    today: date | None = None,
) -> pd.DataFrame:
    """Valid single-family sales in the two windows (months), per tract and for the county."""
    client = client or HttpClient(timeout=300)
    folder = Path(cache) / "local" / "allegheny"
    day = today or date.today()
    short, long_ = sorted(windows)

    def make() -> pd.DataFrame:
        args = (single_family_uses, valid_codes, months_back(day, long_), months_back(day, short))
        tracts = ckan_sql(sales_query(*args, by_tract=True), client)
        county = ckan_sql(sales_query(*args, by_tract=False), client).assign(tract="")
        return pd.concat([tracts, county], ignore_index=True)

    return _cached(folder / "sales.csv", SALES_SOURCE, make, manifest, refresh)
