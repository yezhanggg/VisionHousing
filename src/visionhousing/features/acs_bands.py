"""Collapse American Community Survey tables into a few plain bands each, for the profile charts.

Plain-language summary
----------------------
The Census Bureau publishes most survey tables in fine detail: 23 age groups
per sex, 16 income brackets, 24 school grades. A chart is easier to read with
five or six groups, and the state pack must stay small, so only the
**collapsed bands** are kept. A band is the sum of the published cells it
covers; nothing is estimated or modelled.

Each group below is one chart. Its bands do not overlap and together cover the
group's whole universe (the people, households or homes the table counts), so
the bands always add up to the group's total. The total is the published
total cell (``{group}_total``).

Margins of error
----------------
Every band keeps a 90% margin of error (``{band}_moe``). A band of one cell
keeps the published margin. A band of several cells uses the Bureau's
approximation for a sum: the square root of the sum of the squared margins,
rounded to a whole number. Margins the Bureau marks as "controlled" (the
estimate is fixed by the population controls, so it has no sampling error)
are blank.

The groups
----------
=========== ========= ============================================ ===================
group       table     universe                                      bands
=========== ========= ============================================ ===================
age         B01001    all residents                                 6 age bands
race        B03002    all residents                                 6 groups
hhsize      B11016    households                                    1, 2, 3, 4, 5+ people
income      B19001    households                                    6 income bands
edu         B15003    residents aged 25 or older                    5 levels
commute     B08301    workers aged 16 or older                      6 ways to work
tenure      B25003    occupied homes                                owner, renter
units       B25024    all homes                                     6 building types
built       B25034    all homes                                     6 periods built
burden      B25070    renter homes paying cash rent                 4 bands of rent / income
vacancy     B25004    vacant homes                                  5 reasons vacant
=========== ========= ============================================ ===================

Two published medians are kept as well, for the state and each county:
``median_household_income`` (B19013) and ``median_gross_rent`` (B25064). These
are the Bureau's own medians, which the tract table cannot give.

Tracts: fewer groups, to keep the pack small
--------------------------------------------
The state pack must stay under its 12 MB budget, and Pennsylvania has about 3,400 tracts.
Tract rows therefore keep only the groups in :data:`TRACT_GROUPS` (age, race and
ethnicity, household income, commute mode and rent burden). For a tract:

- owners and renters, building type and year built are read from the tract
  table, which already holds those cells with their margins
  (:data:`FROM_TRACT_TABLE` says which tract columns make each band);
- household size, education and vacancy status are left out (their tract
  counts are small and their margins wide); the county and state rows have them.
- the medians are in the tract table already.

Race and ethnicity follow the Bureau's table B03002: "Hispanic or Latino" is
one group of any race; the other groups count people who are not Hispanic or
Latino.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "FROM_TRACT_TABLE",
    "GROUPS",
    "MEDIANS",
    "TRACT_GROUPS",
    "Band",
    "Group",
    "band_columns",
    "band_table",
    "group",
    "needed_tables",
]


@dataclass(frozen=True)
class Band:
    """One band of a group: its short key, plain label and the cell numbers it adds up."""

    key: str
    label: str
    cells: tuple[int, ...]


@dataclass(frozen=True)
class Group:
    """One collapsed table (one chart).

    Attributes:
        key: Short name, used as the column prefix (``age``).
        title: Plain title.
        table: The ACS table id.
        universe: What the table counts, in plain words.
        unit: The unit of the counts (``persons``, ``households``, ``units``).
        bands: The bands, in display order.
        total: The cell that holds the table's total (usually cell 1).
    """

    key: str
    title: str
    table: str
    universe: str
    unit: str
    bands: tuple[Band, ...]
    total: int = 1

    def column(self, band: str) -> str:
        """Column name of a band (or of ``"total"``)."""
        return f"{self.key}_{band}"

    def columns(self) -> list[str]:
        """The total column, then every band column."""
        return [self.column("total"), *(self.column(b.key) for b in self.bands)]


def _r(a: int, b: int) -> tuple[int, ...]:
    return tuple(range(a, b + 1))


def _both_sexes(*cells: int) -> tuple[int, ...]:
    """B01001 cells for men (as given) and the matching cells for women (+24)."""
    return (*cells, *(c + 24 for c in cells))


GROUPS: tuple[Group, ...] = (
    Group("age", "Age", "B01001", "all residents", "persons", (
        Band("under_18", "Under 18", _both_sexes(*_r(3, 6))),
        Band("18_24", "18 to 24", _both_sexes(*_r(7, 10))),
        Band("25_34", "25 to 34", _both_sexes(11, 12)),
        Band("35_54", "35 to 54", _both_sexes(*_r(13, 16))),
        Band("55_64", "55 to 64", _both_sexes(*_r(17, 19))),
        Band("65_plus", "65 or older", _both_sexes(*_r(20, 25))),
    )),
    Group("race", "Race and ethnicity", "B03002", "all residents", "persons", (
        Band("white", "White (not Hispanic)", (3,)),
        Band("black", "Black (not Hispanic)", (4,)),
        Band("asian", "Asian (not Hispanic)", (6,)),
        Band("hispanic", "Hispanic or Latino (any race)", (12,)),
        Band("multi", "Two or more races (not Hispanic)", (9,)),
        Band("other", "Other race (not Hispanic)", (5, 7, 8)),
    )),
    Group("hhsize", "Household size", "B11016", "households", "households", (
        Band("1", "1 person", (10,)),
        Band("2", "2 people", (3, 11)),
        Band("3", "3 people", (4, 12)),
        Band("4", "4 people", (5, 13)),
        Band("5_plus", "5 or more people", (6, 7, 8, 14, 15, 16)),
    )),
    Group("income", "Household income", "B19001", "households", "households", (
        Band("lt_25k", "Under $25,000", _r(2, 5)),
        Band("25k_50k", "$25,000 to $49,999", _r(6, 10)),
        Band("50k_75k", "$50,000 to $74,999", (11, 12)),
        Band("75k_100k", "$75,000 to $99,999", (13,)),
        Band("100k_150k", "$100,000 to $149,999", (14, 15)),
        Band("150k_plus", "$150,000 or more", (16, 17)),
    )),
    Group("edu", "Education (aged 25 or older)", "B15003", "residents aged 25 or older",
          "persons", (
        Band("lt_hs", "No high school diploma", _r(2, 16)),
        Band("hs", "High school diploma or GED", (17, 18)),
        Band("some_college", "Some college or associate degree", (19, 20, 21)),
        Band("bachelors", "Bachelor's degree", (22,)),
        Band("graduate", "Graduate or professional degree", (23, 24, 25)),
    )),
    Group("commute", "How workers get to work", "B08301", "workers aged 16 or older",
          "persons", (
        Band("drove_alone", "Drove alone", (3,)),
        Band("carpool", "Carpooled", (4,)),
        Band("transit", "Public transit", (10,)),
        Band("walk_bike", "Walked or cycled", (18, 19)),
        Band("other", "Taxi, motorcycle or other", (16, 17, 20)),
        Band("home", "Worked from home", (21,)),
    )),
    Group("tenure", "Owners and renters", "B25003", "occupied homes", "households", (
        Band("owner", "Owner households", (2,)),
        Band("renter", "Renter households", (3,)),
    )),
    Group("units", "Homes by building type", "B25024", "all homes", "units", (
        Band("1_detached", "Single-family detached", (2,)),
        Band("1_attached", "Single-family attached (row houses)", (3,)),
        Band("2_4", "2 to 4 units", (4, 5)),
        Band("5_19", "5 to 19 units", (6, 7)),
        Band("20_plus", "20 or more units", (8, 9)),
        Band("mobile_other", "Mobile home, boat, RV or van", (10, 11)),
    )),
    Group("built", "Homes by year built", "B25034", "all homes", "units", (
        Band("2010_plus", "2010 or later", (2, 3)),
        Band("2000_2009", "2000 to 2009", (4,)),
        Band("1980_1999", "1980 to 1999", (5, 6)),
        Band("1960_1979", "1960 to 1979", (7, 8)),
        Band("1940_1959", "1940 to 1959", (9, 10)),
        Band("pre_1940", "Before 1940", (11,)),
    )),
    Group("burden", "Rent as a share of household income", "B25070",
          "renter homes paying cash rent", "households", (
        Band("lt_30", "Under 30% of income", _r(2, 6)),
        Band("30_49", "30% to 49% (cost-burdened)", (7, 8, 9)),
        Band("50_plus", "50% or more (severely cost-burdened)", (10,)),
        Band("not_computed", "Not computed (no income or loss)", (11,)),
    )),
    Group("vacancy", "Why vacant homes are empty", "B25004", "vacant homes", "units", (
        Band("for_rent", "For rent", (2,)),
        Band("for_sale", "For sale", (4,)),
        Band("rented_sold", "Rented or sold, not yet moved in", (3, 5)),
        Band("seasonal", "Seasonal or migrant-worker use", (6, 7)),
        Band("other", "Other vacant", (8,)),
    )),
)  # fmt: skip
"""Every collapsed group, in display order."""

MEDIANS: dict[str, str] = {
    "median_household_income": "B19013_001",
    "median_gross_rent": "B25064_001",
}
"""Published medians kept next to the bands: column name to ACS cell."""

TRACT_GROUPS: tuple[str, ...] = ("age", "race", "income", "commute", "burden")
"""Groups kept on tract rows (see "Tracts" above)."""

FROM_TRACT_TABLE: dict[str, dict[str, tuple[str, ...]]] = {
    "tenure": {
        "total": ("households_owner", "households_renter"),
        "owner": ("households_owner",),
        "renter": ("households_renter",),
    },
    "units": {
        "total": ("housing_units",),
        "1_detached": ("units_1_detached",),
        "1_attached": ("units_1_attached",),
        "2_4": ("units_2", "units_3_4"),
        "5_19": ("units_5_9", "units_10_19"),
        "20_plus": ("units_20_plus",),
        "mobile_other": ("units_mobile", "units_boat_rv"),
    },
    "built": {
        "total": ("housing_units",),
        "2010_plus": ("built_2010_2019", "built_2020_plus"),
        "2000_2009": ("built_2000_2009",),
        "1980_1999": ("built_1980_1989", "built_1990_1999"),
        "1960_1979": ("built_1960_1969", "built_1970_1979"),
        "1940_1959": ("built_1940_1949", "built_1950_1959"),
        "pre_1940": ("built_1939_or_earlier",),
    },
}
"""For a tract, the tract-table columns that make each band of these groups (the same
ACS cells; B25024 and B25034 totals equal all housing units, B25001). A missing
column (``units_boat_rv`` in Testland) counts as zero only when the other columns
of the band are present."""

_BY_KEY = {g.key: g for g in GROUPS}


def group(key: str) -> Group:
    """The group with this key (``KeyError`` when unknown)."""
    return _BY_KEY[key]


def needed_tables() -> tuple[str, ...]:
    """Every ACS table the bands and medians read, sorted."""
    return tuple(sorted({g.table for g in GROUPS} | {c[:6] for c in MEDIANS.values()}))


def band_columns() -> list[str]:
    """Every estimate column of :func:`band_table`, in order (margins follow as ``*_moe``)."""
    return [c for g in GROUPS for c in g.columns()] + list(MEDIANS)


def _cells(table: str, cells: tuple[int, ...], suffix: str) -> list[str]:
    return [f"{table}_{c:03d}{suffix}" for c in cells]


def band_table(
    raw: pd.DataFrame,
    tracts: set[str] | None = None,
    tract_groups: tuple[str, ...] = TRACT_GROUPS,
) -> pd.DataFrame:
    """Collapse the raw ACS cells into the bands of :data:`GROUPS`.

    Args:
        raw: One row per geography with ``level`` (``state``, ``county`` or
            ``tract``), ``geoid`` and the API-style cells (``B19001_002E``,
            ``B19001_002M`` ...), as :func:`visionhousing.data.sources.acs.fetch_acs`
            and :func:`~visionhousing.data.sources.acs.fetch_acs_profile` return them.
        tracts: Tract codes to keep (``None`` keeps every tract).
        tract_groups: Groups filled on tract rows; the other groups and the
            medians are blank there.

    Returns:
        ``geoid, level``, every column of :func:`band_columns`, then each one's
        ``*_moe``; sorted by ``geoid`` (so the state first, then its counties,
        then its tracts). Counts are whole numbers; a band is missing when any
        of its cells is.
    """
    rows = raw[raw["level"].isin(["state", "county", "tract"])].copy()
    if tracts is not None:
        keep = (rows["level"] != "tract") | rows["geoid"].astype(str).isin(tracts)
        rows = rows[keep]
    rows = rows.sort_values("geoid", kind="stable").reset_index(drop=True)
    est: dict[str, pd.Series] = {}
    moe: dict[str, pd.Series] = {}

    def num(col: str) -> pd.Series:
        return pd.to_numeric(rows[col], errors="coerce").astype("float64")

    for g in GROUPS:
        est[g.column("total")] = num(f"{g.table}_{g.total:03d}E")
        moe[f"{g.column('total')}_moe"] = num(f"{g.table}_{g.total:03d}M")
        for b in g.bands:
            values = [num(c) for c in _cells(g.table, b.cells, "E")]
            margins = [num(c) for c in _cells(g.table, b.cells, "M")]
            total = values[0]
            for v in values[1:]:
                total = total + v  # NaN when any cell is missing
            est[g.column(b.key)] = total
            if len(margins) == 1:
                moe[f"{g.column(b.key)}_moe"] = margins[0]
            else:
                squares = sum(m.fillna(0) ** 2 for m in margins)
                any_m = pd.concat(margins, axis=1).notna().any(axis=1)
                moe[f"{g.column(b.key)}_moe"] = np.sqrt(squares).round().where(any_m)
    for name, cell in MEDIANS.items():
        est[name] = num(f"{cell}E")
        moe[f"{name}_moe"] = num(f"{cell}M")
    head = pd.DataFrame({"geoid": rows["geoid"].astype(str), "level": rows["level"]})
    out = pd.concat([head, pd.DataFrame(est), pd.DataFrame(moe)], axis=1)
    kept = {c for g in GROUPS if g.key in tract_groups for c in g.columns()}
    blank = [c for c in band_columns() if c not in kept]
    is_tract = out["level"] == "tract"
    out.loc[is_tract, [*blank, *(f"{c}_moe" for c in blank)]] = np.nan
    return out


PACK_TABLE: dict[str, dict[str, object]] = {
    "acs_bands": {
        "key": ["geoid"],
        "sources": ["acs5_profile_tables"],
        "description": (
            "One row for the state, each county and each tract: ACS 2020-2024 tables collapsed "
            "into a few bands each (age, race and ethnicity, household size, income, education, "
            "commute mode, owners and renters, building type, year built, rent burden, vacancy "
            "status), with margins of error, for the place-profile charts. Tract rows keep only "
            "age, race and ethnicity, income, commute mode and rent burden."
        ),
    }
}
"""The pack table this module makes (merged into :data:`visionhousing.data.pack.TABLES`)."""


def describe(name: str) -> tuple[str, str]:
    """``(unit, description)`` of an ``acs_bands`` column, for the data dictionary."""
    if name == "geoid":
        return "code", "State (2 digits), county (5 digits) or tract (11 digits) code"
    if name == "level":
        return "category", "state, county or tract"
    if name.endswith("_moe"):
        unit, _ = describe(name[: -len("_moe")])
        return unit, f"90% margin of error of {name[: -len('_moe')]}"
    if name == "median_household_income":
        return "dollars per year", "Published median household income (B19013); state and counties"
    if name == "median_gross_rent":
        return "dollars per month", "Published median gross rent (B25064); state and counties"
    for g in GROUPS:
        if name == g.column("total"):
            return g.unit, f"{g.title}: all {g.universe} ({g.table} total)"
        for b in g.bands:
            if name == g.column(b.key):
                cells = ", ".join(f"{c:03d}" for c in b.cells)
                return g.unit, f"{g.title}: {b.label} ({g.table} cells {cells})"
    raise ValueError(f"no data-dictionary entry for acs_bands.{name}")
