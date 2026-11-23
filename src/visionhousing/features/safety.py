"""Safety table for the state pack: reported offenses per 1,000 residents, by county.

Plain-language summary
----------------------
The FBI agency counts (:mod:`visionhousing.data.sources.safety`) are added up by
county:

- an agency is placed in the first county its FBI record lists (the county
  name is matched to the pack's county names);
- only agencies that reported all 12 months of both violent and property
  offenses are added ("full-year agencies"); the others are counted in
  ``agencies`` but not in the offense totals;
- ``population_covered`` is the sum of the populations the full-year agencies
  serve, as the FBI records them;
- the rates are offenses / ``population_covered`` x 1,000.

So a county's rate describes the area its reporting agencies police. When few
agencies reported, ``coverage_share`` (population covered / the county's ACS
population) is low and the rate says little about the county as a whole. In
Pennsylvania, State Police troops cover places without a local department, so
most residents are inside some agency's area.

No tract figures are made: agency areas are towns and townships, which do not
line up with tracts.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["COLUMNS", "PER", "TABLES", "safety_table"]

PER = 1000
"""Rates are per 1,000 residents of the agencies' areas."""
FULL_YEAR = 12

TABLES: dict[str, dict[str, object]] = {
    "safety_county": {
        "key": ["county"],
        "sources": ["fbi_cde_agency"],
        "description": (
            "Violent and property offenses reported by police agencies that reported all year, "
            "per 1,000 residents of their areas, by county (FBI Crime Data Explorer)."
        ),
    },
}
"""The safety pack table: key, sources and a one-line description."""

_S = "safety_county"
COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    (_S, "county"): ("code", "County code", "fbi_cde_agency"),
    (_S, "year"): ("year", "Calendar year of the offenses", "fbi_cde_agency"),
    (_S, "agencies"): ("agencies", "City, county and State Police agencies listed in the county",
                       "fbi_cde_agency"),
    (_S, "agencies_full_year"): ("agencies", "Of which reported all 12 months", "fbi_cde_agency"),
    (_S, "population_covered"): ("persons", "Residents served by the full-year agencies",
                                 "fbi_cde_agency"),
    (_S, "violent"): ("offenses", "Violent offenses reported by the full-year agencies",
                      "fbi_cde_agency"),
    (_S, "property"): ("offenses", "Property offenses reported by the full-year agencies",
                       "fbi_cde_agency"),
    (_S, "violent_per_1000"): ("per 1,000 residents", "violent / population_covered x 1,000",
                               "fbi_cde_agency"),
    (_S, "property_per_1000"): ("per 1,000 residents", "property / population_covered x 1,000",
                                "fbi_cde_agency"),
    (_S, "coverage_share"): ("share", "population_covered / the county's ACS population",
                             "fbi_cde_agency"),
}  # fmt: skip
"""``(table, column) -> (unit, description, source id)`` for the data dictionary."""


def _norm(name: object) -> str:
    return str(name or "").strip().upper().removesuffix(" COUNTY").strip()


def first_county(names: object, by_name: dict[str, str]) -> str:
    """The code of the first county an FBI agency record lists (``""`` when none matches)."""
    for part in str(names or "").split(","):
        code = by_name.get(_norm(part))
        if code:
            return code
    return ""


def safety_table(agencies: pd.DataFrame, counties: pd.DataFrame) -> pd.DataFrame:
    """Build ``safety_county`` from the agency table and the pack's ``counties`` table.

    Args:
        agencies: Columns of :data:`visionhousing.data.sources.safety.AGENCY_COLUMNS`.
        counties: The pack's counties (``geoid``, ``name``, ``population``).
    """
    by_name = {_norm(n): str(g) for g, n in zip(counties["geoid"], counties["name"], strict=True)}
    a = agencies.copy()
    a["county"] = a["county_names"].map(lambda n: first_county(n, by_name))
    a = a[a["county"] != ""]
    full = (pd.to_numeric(a["violent_months"], errors="coerce") >= FULL_YEAR) & (
        pd.to_numeric(a["property_months"], errors="coerce") >= FULL_YEAR
    )
    a["full"] = full.astype("int64")
    kept = a[full]
    out = pd.DataFrame({"county": sorted(counties["geoid"].astype(str))}).set_index("county")
    year = int(pd.to_numeric(agencies["year"], errors="coerce").max()) if len(agencies) else None
    out["year"] = year
    out["agencies"] = a.groupby("county").size().reindex(out.index).fillna(0).astype("int64")
    out["agencies_full_year"] = a.groupby("county")["full"].sum().reindex(out.index).fillna(0)
    out["agencies_full_year"] = out["agencies_full_year"].astype("int64")
    for col, name in (("population", "population_covered"), ("violent", "violent"),
                      ("property", "property")):  # fmt: skip
        values = pd.to_numeric(kept[col], errors="coerce").groupby(kept["county"]).sum()
        out[name] = values.reindex(out.index).fillna(0).round().astype("int64")
    covered = out["population_covered"].where(out["population_covered"] > 0)
    out["violent_per_1000"] = (out["violent"] / covered * PER).round(2)
    out["property_per_1000"] = (out["property"] / covered * PER).round(2)
    pop = pd.to_numeric(counties.set_index("geoid")["population"], errors="coerce")
    out["coverage_share"] = (out["population_covered"] / pop.reindex(out.index)).round(4)
    return out.reset_index()
