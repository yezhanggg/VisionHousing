"""Local permit pipeline: city new-home permits by tract and year, and LIHTC awards.

Plain-language summary
----------------------
Builds two pack tables for the ``pipeline_local`` profile section:

- ``pipeline_tract_year``: one row per 2020 tract, year and source, with the
  new-home building permits issued (``permits``) and, for Philadelphia, the
  homes on them (``units``; empty for Pittsburgh, whose file has no unit
  counts). Each permit is put in the tract whose outline holds its point.
  Permits with no location are counted on a row with an empty ``tract``, so
  city totals stay whole.
- ``lihtc_awards``: one row per development awarded Low-Income Housing Tax
  Credits by PHFA: year, name, city, county, tract (when the address could be
  geocoded), homes and credit amount.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.features.parcels import place_in_tracts

__all__ = ["AWARD_COLUMNS", "PIPELINE_COLUMNS", "lihtc_table", "permits_by_tract_year"]

PIPELINE_COLUMNS = ("tract", "county", "year", "source", "permits", "units")
AWARD_COLUMNS = ("year", "development", "city", "county", "tract", "units", "lihtc_award")


def permits_by_tract_year(
    permits: pd.DataFrame,
    shapes: Iterable[tuple[str, Mapping[str, Any]]],
    county: str,
    source: str,
    *,
    has_units: bool,
) -> pd.DataFrame:
    """Count permits (and homes) per tract and year for one city.

    Args:
        permits: ``lat``, ``lon``, ``year`` and, when ``has_units``, ``units``.
        shapes: ``(tract, geometry)`` of the county's 2020 tracts.
        county: The county code.
        source: Source id written on every row.
        has_units: Whether the permits carry home counts.
    """
    p = permits.copy()
    p["year"] = pd.to_numeric(p["year"], errors="coerce")
    p = p[p["year"].notna()]
    lat = pd.to_numeric(p["lat"], errors="coerce")
    lon = pd.to_numeric(p["lon"], errors="coerce")
    ok = lat.notna() & lon.notna()
    tract = np.full(len(p), "", dtype=object)
    if ok.any():
        tract[ok.to_numpy()] = place_in_tracts(lat[ok], lon[ok], shapes)
    p["tract"] = tract
    p["units"] = pd.to_numeric(p.get("units", 0), errors="coerce").fillna(0) if has_units else 0
    out = (
        p.groupby(["tract", "year"], as_index=False)
        .agg(permits=("year", "size"), units=("units", "sum"))
        .astype({"year": int})
    )
    out["county"] = county
    out["source"] = source
    if not has_units:
        out["units"] = pd.NA
    return out[list(PIPELINE_COLUMNS)].sort_values(["tract", "year"]).reset_index(drop=True)


def lihtc_table(awards: pd.DataFrame) -> pd.DataFrame:
    """The ``lihtc_awards`` pack table (no street address, no sponsor)."""
    a = awards.copy()
    for c in AWARD_COLUMNS:
        if c not in a.columns:
            a[c] = None
    a["tract"] = a["tract"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    out = a[list(AWARD_COLUMNS)]
    return out.sort_values(["year", "county", "development"]).reset_index(drop=True)
