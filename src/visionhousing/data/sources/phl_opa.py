"""Philadelphia property records (Office of Property Assessment) and deed transfers.

Plain-language summary
----------------------
The City of Philadelphia publishes every property's assessment record
(``opa_properties_public``, about 584,000 parcels) and every recorded deed's
transfer-tax record (``rtt_summary``) through its Carto SQL service. This
module reads them **without ever downloading an owner's name**:

- :func:`fetch_opa_parcels` asks for one slim row per parcel: its location
  (rounded to 5 decimals, about one metre), its OPA category, the market
  value, year built, lot area, last sale date and price, and three yes/no
  flags worked out on the City's server: homestead exemption, owner name
  looks like an entity (company, trust ...), tax-bill mailing address outside
  Pennsylvania. Owner names are tested against the word list in
  ``assumptions/owners.yaml`` inside the query; only the 0/1 answer comes back.
- :func:`fetch_deed_flow` counts, per year, arm's-length deeds of
  single-family homes and how many went to an entity grantee. The counting is
  done on the City's server; only yearly totals come back.

The data licence of the City's open data is not confirmed. Only derived
totals are kept and shown, the City is credited, and DATA_PROVENANCE.md says
the licence must be confirmed before release.
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
    carto_sql,
    read_extract,
    save_extract,
    sql_list,
    sql_text,
    word_regex,
)

__all__ = [
    "DEEDS_SOURCE",
    "OPA_SOURCE",
    "PARCEL_COLUMNS",
    "deed_flow_query",
    "fetch_deed_flow",
    "fetch_opa_parcels",
    "opa_page_query",
]

OPA_SOURCE = "phl_opa_properties"
DEEDS_SOURCE = "phl_rtt_summary"
PAGE = 100_000
"""Parcels asked for per request (by ``cartodb_id`` range)."""

PARCEL_COLUMNS = (
    "lat",
    "lon",
    "category",
    "condo",
    "market_value",
    "year_built",
    "lot_sqft",
    "sale_date",
    "sale_price",
    "homestead",
    "entity",
    "out_of_state",
)
"""Columns of the slim parcel extract (no names, no addresses)."""


def opa_page_query(
    start: int, stop: int, entity_words: list[str], home_state: str, condo_word: str
) -> str:
    """The query for parcels with ``start < cartodb_id <= stop``.

    The owner-name test (``entity``) and the mailing-state test
    (``out_of_state``) run on the City's server; names never leave it.
    """
    pattern = sql_text(word_regex(entity_words))
    state = sql_text(home_state.upper())
    condo = sql_text(f"%{condo_word}%")
    return (
        "SELECT round(ST_Y(the_geom)::numeric, 5) AS lat, "
        "round(ST_X(the_geom)::numeric, 5) AS lon, "
        "category_code AS category, "
        f"(coalesce(building_code_description, '') ILIKE {condo})::int AS condo, "
        "market_value, "
        "CASE WHEN year_built ~ '^[0-9]{4}$' THEN year_built::int END AS year_built, "
        "total_area AS lot_sqft, "
        "to_char(sale_date, 'YYYY-MM-DD') AS sale_date, sale_price, "
        "(coalesce(homestead_exemption, 0) > 0)::int AS homestead, "
        f"(concat_ws(' ', owner_1, owner_2) ~* {pattern})::int AS entity, "
        "(mailing_city_state IS NOT NULL AND trim(mailing_city_state) <> '' "
        f"AND upper(right(trim(mailing_city_state), 2)) <> {state})::int AS out_of_state "
        f"FROM opa_properties_public WHERE cartodb_id > {int(start)} AND cartodb_id <= {int(stop)}"
    )


def fetch_opa_parcels(
    cache: Path,
    *,
    entity_words: list[str],
    home_state: str,
    condo_word: str,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> tuple[pd.DataFrame, str]:
    """Download (or reuse) the slim parcel extract; return it and the date it was read.

    Returns:
        The extract (columns :data:`PARCEL_COLUMNS`, numbers parsed) and the
        read date as ``YYYY-MM-DD`` (sales windows count back from it).
    """
    folder = Path(cache) / "local" / "phl"
    dest = folder / "opa_parcels.csv.gz"
    stamp = folder / "opa_parcels.read_on"
    if refresh or not dest.exists() or not stamp.exists():
        client = client or HttpClient(timeout=300)
        top = carto_sql("SELECT max(cartodb_id) AS m FROM opa_properties_public", client)
        last = int(float(top["m"].iloc[0]))
        pages = []
        for start in range(0, last, PAGE):
            q = opa_page_query(start, start + PAGE, entity_words, home_state, condo_word)
            pages.append(carto_sql(q, client))
        frame = pd.concat(pages, ignore_index=True)
        read_on = date.today().isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        frame.to_csv(dest, index=False, compression={"method": "gzip", "mtime": 0})
        stamp.write_text(read_on + "\n", encoding="utf-8")
        if manifest is not None:
            mf.record(
                mf.entry_for_file(OPA_SOURCE, CARTO_URL, dest, read_on, root=mf.ROOT), manifest
            )
    frame = pd.read_csv(dest, dtype={"category": str, "sale_date": str})
    return frame, stamp.read_text(encoding="utf-8").strip()


def deed_flow_query(
    entity_words: list[str],
    document_types: list[str],
    min_consideration: float,
    max_property_count: int,
    first_year: int,
    condo_word: str,
) -> str:
    """Yearly arm's-length deeds of single-family homes and how many went to an entity.

    A deed counts when its type is in ``document_types``, its total
    consideration is at least ``min_consideration``, it conveys at most
    ``max_property_count`` properties, and the property is single family
    (OPA category 1, not a condominium) in today's assessment file.
    """
    pattern = sql_text(word_regex(entity_words))
    condo = sql_text(f"%{condo_word}%")
    return (
        "SELECT extract(year FROM r.display_date)::int AS year, count(*) AS deeds, "
        f"sum(CASE WHEN r.grantees ~* {pattern} THEN 1 ELSE 0 END) AS entity_deeds "
        "FROM rtt_summary r JOIN opa_properties_public o ON o.parcel_number = r.opa_account_num "
        f"WHERE r.document_type IN {sql_list(document_types)} "
        f"AND r.total_consideration >= {float(min_consideration)} "
        f"AND coalesce(r.property_count, 1) <= {int(max_property_count)} "
        "AND o.category_code = '1' "
        f"AND coalesce(o.building_code_description, '') NOT ILIKE {condo} "
        f"AND r.display_date >= '{int(first_year)}-01-01' "
        "GROUP BY 1 ORDER BY 1"
    )


def fetch_deed_flow(
    cache: Path,
    *,
    entity_words: list[str],
    document_types: list[str],
    min_consideration: float,
    max_property_count: int,
    first_year: int,
    condo_word: str,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> pd.DataFrame:
    """Download (or reuse) the yearly deed counts.

    Columns ``year``, ``deeds``, ``entity_deeds`` and ``read_on``.
    """
    dest = Path(cache) / "local" / "phl" / "deed_flow.csv"
    if refresh or not dest.exists():
        q = deed_flow_query(
            entity_words,
            document_types,
            min_consideration,
            max_property_count,
            first_year,
            condo_word,
        )
        frame = carto_sql(q, client or HttpClient(timeout=300))
        frame["read_on"] = date.today().isoformat()
        save_extract(
            frame,
            dest,
            source_id=DEEDS_SOURCE,
            url=CARTO_URL,
            vintage=date.today().isoformat(),
            manifest=manifest,
        )
    out = read_extract(dest)
    for c in ("year", "deeds", "entity_deeds"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    return out
