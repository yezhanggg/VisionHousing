"""Tract features from American Community Survey tables, with the state-pack column names.

Plain-language summary
----------------------
:func:`visionhousing.data.sources.acs.fetch_acs` returns raw ACS cells such as
``B25024_002E``. This module turns them into the tract table the model reads,
using the column names of the synthetic state "Testland"
(``tests/fixtures/testland/data_dictionary.json``), which are the schema every
real state pack follows.

What each feature is
--------------------
- ``population`` (B01003), ``housing_units`` (B25001).
- Units in structure (B25024): ``units_1_detached``, ``units_1_attached``,
  ``units_2``, ``units_3_4``, ``units_5_9``, ``units_10_19``,
  ``units_20_plus`` (20-49 plus 50 or more), ``units_mobile``; and
  ``units_boat_rv`` (boats, RVs, vans), which Testland does not have.
- Tenure (B25003): ``households_owner``, ``households_renter``.
- Vacancy (B25002): ``vacant_units`` and ``vacancy_rate`` = vacant / all units.
- Medians: ``median_household_income`` (B19013), ``median_home_value``
  (B25077), ``median_gross_rent`` (B25064).
- ``share_age_65plus`` (B01001): men and women aged 65 or older / everyone.
- Value brackets (B25075) ``value_*`` and gross-rent brackets (B25063)
  ``rent_*`` and ``rent_no_cash``, named by :mod:`visionhousing.features.brackets`.
- Year built (B25034): ``built_2020_plus`` ... ``built_1939_or_earlier``.
- Margins of error: every count and median above has a ``<name>_moe`` column.
  A sum of cells (``units_20_plus``) uses the Bureau's approximation, the square
  root of the sum of squared margins. ``share_age_65plus_moe`` uses the Bureau's
  formula for a proportion.

Rent by bedrooms (``median_rent_b0`` ... ``median_rent_b3``)
------------------------------------------------------------
B25031 gives median gross rent for units with 0, 1, 2 and 3 bedrooms, but in
many tracts the sample is too small and the cell is blank. The rule, per value:

1. **Tract median** where the tract reports one (method ``"tract"``).
2. Otherwise the **county ratio**: tract median gross rent (B25064) times the
   county's bedroom median divided by the county's median gross rent
   (B25031 / B25064, both at county level). Method ``"county_ratio"``.
3. Otherwise missing (method ``"missing"``).

The method is kept in ``median_rent_b{k}_method``. ``median_rent_b3`` uses the
3-bedroom cell (B25031_005); the 4- and 5-or-more-bedroom cells are not merged,
because medians cannot be added up.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from visionhousing.features import brackets as br

__all__ = [
    "BEDROOM_CELLS",
    "COLUMNS",
    "SIMPLE",
    "county_rent_ratios",
    "tract_features",
]

SIMPLE: dict[str, str] = {
    "population": "B01003_001",
    "housing_units": "B25001_001",
    "units_1_detached": "B25024_002",
    "units_1_attached": "B25024_003",
    "units_2": "B25024_004",
    "units_3_4": "B25024_005",
    "units_5_9": "B25024_006",
    "units_10_19": "B25024_007",
    "units_mobile": "B25024_010",
    "units_boat_rv": "B25024_011",
    "households_owner": "B25003_002",
    "households_renter": "B25003_003",
    "vacant_units": "B25002_003",
    "median_household_income": "B19013_001",
    "median_home_value": "B25077_001",
    "median_gross_rent": "B25064_001",
    "built_2020_plus": "B25034_002",
    "built_2010_2019": "B25034_003",
    "built_2000_2009": "B25034_004",
    "built_1990_1999": "B25034_005",
    "built_1980_1989": "B25034_006",
    "built_1970_1979": "B25034_007",
    "built_1960_1969": "B25034_008",
    "built_1950_1959": "B25034_009",
    "built_1940_1949": "B25034_010",
    "built_1939_or_earlier": "B25034_011",
}
"""Feature name to the one ACS cell it is copied from (without the E/M ending)."""

SUMS: dict[str, tuple[str, ...]] = {
    "units_20_plus": ("B25024_008", "B25024_009"),
}
"""Features that add several cells."""

BEDROOM_CELLS = {0: "B25031_002", 1: "B25031_003", 2: "B25031_004", 3: "B25031_005"}
"""B25031 cell used for each ``median_rent_b{k}``."""

_AGE_65 = tuple(f"B01001_{i:03d}" for i in (*range(20, 26), *range(44, 50)))

COLUMNS = (
    "geoid",
    "county",
    "housing_units",
    "population",
    "units_1_detached",
    "units_1_attached",
    "units_2",
    "units_3_4",
    "units_5_9",
    "units_10_19",
    "units_20_plus",
    "units_mobile",
    "households_owner",
    "households_renter",
    "vacant_units",
    "vacancy_rate",
    "median_household_income",
    "median_home_value",
    "median_gross_rent",
    "median_rent_b0",
    "median_rent_b1",
    "median_rent_b2",
    "median_rent_b3",
    "share_age_65plus",
    *br.VALUE.columns,
    *br.RENT.columns,
    "rent_no_cash",
)
"""The Testland tract columns this module fills (Testland's ``area_type`` and
``land_area_m2`` come from other sources), in Testland order."""


def _rss(frame: pd.DataFrame, cells: tuple[str, ...]) -> pd.Series:
    """Square root of the sum of squared margins (the Bureau's approximation for a sum)."""
    squares = sum(frame[f"{c}M"].pow(2) for c in cells)
    return np.sqrt(squares)


def county_rent_ratios(raw: pd.DataFrame) -> pd.DataFrame:
    """County bedroom-to-all rent ratios: county B25031 bedroom median / county B25064.

    Returns:
        Index ``county`` (5 digits); columns ``ratio_b0`` ... ``ratio_b3``. A
        ratio is NaN when either county median is missing.
    """
    counties = raw[raw["level"] == "county"].set_index("geoid")
    out = pd.DataFrame(index=counties.index)
    out.index.name = "county"
    for k, cell in BEDROOM_CELLS.items():
        out[f"ratio_b{k}"] = counties[f"{cell}E"] / counties["B25064_001E"]
    return out


def tract_features(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the tract feature table from the raw table of ``fetch_acs``.

    Args:
        raw: Rows for the state, its counties and its tracts (column ``level``),
            with the API-style ``..._E`` and ``..._M`` columns.

    Returns:
        One row per tract: :data:`COLUMNS` first, then the extra columns
        (``units_boat_rv``, year built, ``*_moe``, ``median_rent_b*_method``),
        sorted by ``geoid``.
    """
    tracts = raw[raw["level"] == "tract"].sort_values("geoid").reset_index(drop=True)
    out = pd.DataFrame({"geoid": tracts["geoid"], "county": tracts["geoid"].str[:5]})
    moe: dict[str, pd.Series] = {}
    for name, cell in SIMPLE.items():
        out[name] = tracts[f"{cell}E"]
        moe[f"{name}_moe"] = tracts[f"{cell}M"]
    for name, cells in SUMS.items():
        out[name] = sum(tracts[f"{c}E"] for c in cells)
        moe[f"{name}_moe"] = _rss(tracts, cells)

    total = tracts["B25002_001E"]
    out["vacancy_rate"] = (out["vacant_units"] / total).where(total > 0)

    people = tracts["B01001_001E"]
    older = sum(tracts[f"{c}E"] for c in _AGE_65)
    share = (older / people).where(people > 0)
    out["share_age_65plus"] = share
    older_moe = _rss(tracts, _AGE_65)
    under = older_moe**2 - share**2 * tracts["B01001_001M"].fillna(0) ** 2
    # The Bureau's rule: if the term under the root is negative, use the ratio formula (+).
    under = under.where(under >= 0, older_moe**2 + share**2 * tracts["B01001_001M"].fillna(0) ** 2)
    moe["share_age_65plus_moe"] = np.sqrt(under) / people.where(people > 0)

    ratios = county_rent_ratios(raw).reindex(out["county"]).reset_index(drop=True)
    methods: dict[str, pd.Series] = {}
    for k, cell in BEDROOM_CELLS.items():
        own = tracts[f"{cell}E"]
        scaled = tracts["B25064_001E"] * ratios[f"ratio_b{k}"]
        out[f"median_rent_b{k}"] = own.where(own.notna(), scaled.round())
        moe[f"median_rent_b{k}_moe"] = tracts[f"{cell}M"].where(own.notna())
        methods[f"median_rent_b{k}_method"] = pd.Series(
            np.select([own.notna(), scaled.notna()], ["tract", "county_ratio"], "missing"),
            index=out.index,
        )

    for table in (br.VALUE, br.RENT):
        for var, name in table.variables().items():
            out[name] = tracts[var]
            moe[f"{name}_moe"] = tracts[var[:-1] + "M"]

    extra = [c for c in out.columns if c not in COLUMNS]
    out = out[[*COLUMNS, *extra]]
    return pd.concat([out, pd.DataFrame(methods), pd.DataFrame(moe)], axis=1).reset_index(drop=True)
