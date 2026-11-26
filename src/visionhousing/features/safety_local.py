"""Keyless safety and distress table: city records counted per 2020 tract and county, per year.

Plain-language summary
----------------------
The City count extracts (:mod:`visionhousing.data.sources.phl_safety`) hold
one count per calendar year and small location cell. This module turns them
into the pack table ``safety_local``:

- each cell is placed in the 2020 tract whose outline holds its point;
- counts are added up per tract and year, and per county and year (the county
  row also counts records with no location or outside every tract, so city
  totals stay whole);
- each count is divided by the area's residents (ACS 2020-2024 population) and
  multiplied by 1,000 to give a rate per 1,000 residents. The same resident
  count is used for every year, so a change in a rate is a change in the
  count. A tract with fewer residents than ``min_tract_population``
  (``assumptions/safety_local.yaml``) gets counts but no rate.

Measures (one column each, plus ``<measure>_per_1000``):

================= ==========================================================
shootings         shooting victims
shootings_fatal   of which died
violent           violent incidents: homicide, rape, robbery, aggravated assault
property          property incidents: burglary, theft, motor-vehicle theft, arson
li_cases          L&I code-violation cases opened
requests_311      311 requests about neglect of a block (dumping, abandoned cars ...)
================= ==========================================================

No point, record or address is in the table: only totals per tract or county.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.features.parcels import PHILADELPHIA, place_in_tracts

__all__ = [
    "ASSUMPTIONS",
    "COLUMNS",
    "MEASURES",
    "PER",
    "PHILADELPHIA",
    "TABLES",
    "load_rules",
    "safety_local_table",
]

ROOT = Path(__file__).resolve().parents[3]
ASSUMPTIONS = ROOT / "assumptions" / "safety_local.yaml"
PER = 1000

MEASURES: dict[str, tuple[str, str, str]] = {
    "shootings": ("victims", "Shooting victims", "phl_shootings"),
    "shootings_fatal": ("victims", "Shooting victims who died", "phl_shootings"),
    "violent": ("incidents", "Violent incidents (homicide, rape, robbery, aggravated assault)",
                "phl_crime_incidents"),
    "property": ("incidents", "Property incidents (burglary, theft, vehicle theft, arson)",
                 "phl_crime_incidents"),
    "li_cases": ("cases", "L&I code-violation cases opened", "phl_li_violations"),
    "requests_311": ("requests", "311 requests about neglect of a block", "phl_311_requests"),
}  # fmt: skip
"""Measure -> (unit, plain label, source id)."""

_T = "safety_local"
TABLES: dict[str, dict[str, object]] = {
    _T: {
        "key": ["level", "geoid", "year"],
        "sources": sorted({m[2] for m in MEASURES.values()} | {"cb_2020_tract",
                                                                 "acs5_summary_file"}),
        "description": (
            "Shootings, violent and property incidents, L&I violation cases and neglect-related "
            "311 requests per year, as counts and per 1,000 residents, per 2020 tract and county "
            "(Philadelphia city records; no record or address kept)."
        ),
        "tier": "2",
    },
}  # fmt: skip
"""The pack table: key, sources, description and tier."""

COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    (_T, "level"): ("category", "county or tract", "cb_2020_tract"),
    (_T, "geoid"): ("code", "County (5 digits) or 2020 tract (11 digits) code", "cb_2020_tract"),
    (_T, "county"): ("code", "County code", "cb_2020_tract"),
    (_T, "year"): ("year", "Calendar year of the records", "phl_crime_incidents"),
    (_T, "population"): ("persons", "Residents (ACS 2020-2024), used for every year",
                         "acs5_summary_file"),
    (_T, "unplaced"): ("records", "County rows: records with no location or outside every tract "
                       "(all measures together)", "cb_2020_tract"),
}  # fmt: skip
for _name, (_unit, _label, _sid) in MEASURES.items():
    COLUMNS[(_T, _name)] = (_unit, _label, _sid)
    COLUMNS[(_T, f"{_name}_per_1000")] = (
        "per 1,000 residents",
        f"{_label} / population x 1,000 (empty for tracts under the minimum population)",
        _sid,
    )
"""``(table, column) -> (unit, description, source id)`` for the data dictionary."""


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/safety_local.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _cells(extracts: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """All extracts in one long table: ``year``, ``lat``, ``lon``, ``measure``, ``n``."""
    parts = []
    for frame in extracts.values():
        for measure in MEASURES:
            if measure in frame.columns:
                part = frame[["year", "lat", "lon"]].copy()
                part["measure"] = measure
                part["n"] = pd.to_numeric(frame[measure], errors="coerce").fillna(0)
                parts.append(part)
    if not parts:
        return pd.DataFrame(columns=["year", "lat", "lon", "measure", "n"])
    out = pd.concat(parts, ignore_index=True)
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    return out[out["year"].notna() & (out["n"] > 0)]


def safety_local_table(
    extracts: Mapping[str, pd.DataFrame],
    shapes: Iterable[tuple[str, Mapping[str, Any]]],
    tracts: pd.DataFrame,
    county: str,
    min_tract_population: float,
) -> pd.DataFrame:
    """Build the ``safety_local`` rows of one county.

    Args:
        extracts: Dataset name -> count extract (``year``, ``lat``, ``lon`` and
            measure columns), as from
            :func:`visionhousing.data.sources.phl_safety.fetch_phl_safety`.
        shapes: ``(tract, geometry)`` of the county's 2020 tracts.
        tracts: The pack's tracts (``geoid``, ``county``, ``population``).
        county: The county code.
        min_tract_population: Tracts with fewer residents get no rate.

    Returns:
        One row per tract and year and per county and year; tracts with no
        record in a year get zeros, so every tract has every year.
    """
    cells = _cells(extracts)
    measures = [m for m in MEASURES if m in set(cells["measure"])]
    lat = pd.to_numeric(cells["lat"], errors="coerce")
    lon = pd.to_numeric(cells["lon"], errors="coerce")
    ok = (lat.notna() & lon.notna()).to_numpy()
    where = np.full(len(cells), "", dtype=object)
    if ok.any():
        where[ok] = place_in_tracts(lat[ok], lon[ok], list(shapes))
    cells = cells.assign(tract=where)

    own = tracts[tracts["county"].astype(str) == county]
    tract_ids = sorted(own["geoid"].astype(str))
    pop = pd.to_numeric(own.set_index("geoid")["population"], errors="coerce")
    years = sorted(int(y) for y in cells["year"].unique())

    wide = cells.pivot_table(
        index=["tract", "year"], columns="measure", values="n", aggfunc="sum", fill_value=0
    )
    full = pd.MultiIndex.from_product([tract_ids, years], names=["tract", "year"])
    tract_rows = wide.reindex(full, fill_value=0).reset_index()
    tract_rows.insert(0, "level", "tract")
    tract_rows = tract_rows.rename(columns={"tract": "geoid"})
    tract_rows["population"] = tract_rows["geoid"].map(pop)

    by_year = cells.groupby(["year", "measure"])["n"].sum().unstack(fill_value=0)
    county_rows = by_year.reindex(years, fill_value=0).reset_index()
    county_rows.insert(0, "level", "county")
    county_rows.insert(1, "geoid", county)
    county_rows["population"] = float(pop.sum())
    unplaced = cells[cells["tract"] == ""].groupby("year")["n"].sum()
    county_rows["unplaced"] = county_rows["year"].map(unplaced).fillna(0)

    out = pd.concat([county_rows, tract_rows], ignore_index=True)
    out["county"] = county
    out["year"] = out["year"].astype(int)
    small = (out["level"] == "tract") & ~(out["population"] >= float(min_tract_population))
    for m in measures:
        out[m] = out[m].fillna(0).round().astype("int64")
        rate = out[m] / out["population"].where(out["population"] > 0) * PER
        out[f"{m}_per_1000"] = rate.mask(small).round(2)
    columns = ["level", "geoid", "county", "year", "population", "unplaced"]
    columns += measures + [f"{m}_per_1000" for m in measures]
    out = out[columns].sort_values(["level", "geoid", "year"]).reset_index(drop=True)
    return out
