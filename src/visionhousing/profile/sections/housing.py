"""Profile section ``housing``: what the homes are like (ACS) and how prices have moved (FHFA).

Plain-language summary
----------------------
From the American Community Survey 2020-2024 5-year estimates (ACS):

- **Homes**: all housing units, occupied and vacant (B25001).
- **Building types** (B25024): the share of homes that are single-family
  detached, single-family attached (row houses, townhouses), in 2-4 unit
  buildings, in buildings of 5 or more units, and mobile homes.
- **Median home value** (B25077) and **median gross rent** (B25064): a tract's
  published medians; for a county or the state, the median of the summed
  bracket counts (B25075 values, B25063 cash rents), found by straight-line
  interpolation inside the bracket that holds it.
- **Vacancy rate**: vacant homes / all homes (B25002).
- **Age of homes** (B25034): the share built before 1940 and the share built
  in 2010 or later (only where the pack has year-built columns).

From the Federal Housing Finance Agency's annual house price index (FHFA HPI):
the change in house prices over the latest year, five years and ten years. The
county index is used for counties; the tract index for tracts, where FHFA
publishes one (many tracts have too few sales). The pack holds no state index,
so county price changes are not compared with the state.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from visionhousing.features import brackets as br
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.model import Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence, with_words

KEY = "housing"
TITLE = "Homes"
SOURCE = "acs5_summary_file"

TYPES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("share_single_detached", "Single-family detached homes", ("units_1_detached",)),
    ("share_single_attached", "Single-family attached homes (row houses)", ("units_1_attached",)),
    ("share_2_4", "Homes in 2-4 unit buildings", ("units_2", "units_3_4")),
    ("share_5plus", "Homes in buildings of 5 or more units",
     ("units_5_9", "units_10_19", "units_20_plus")),
    ("share_mobile", "Mobile homes", ("units_mobile",)),
)  # fmt: skip
"""Building-type shares: fact name, label, B25024 columns added up."""


def _hpi(ctx: ProfileContext, column: str) -> Callable[[Area], Measure]:
    """A price-change measure: the tract index for a tract, else the county index."""

    def measure(area: Area) -> Measure:
        if area.level == "state":
            raise MissingDataError("no state price index in the pack")
        if area.single:
            table, key = ctx.table("prices_tract"), "tract"
        else:
            table, key = ctx.table("prices_county"), "county"
        if table is None or column not in table.columns:
            raise MissingDataError(f"no {column}")
        row = table[table[key] == area.geoid]
        if row.empty:
            return Measure(None)
        return Measure(pd.to_numeric(row[column], errors="coerce").iloc[0])

    return measure


def _hpi_year(ctx: ProfileContext, area: Area) -> str:
    table = ctx.table("prices_tract" if area.single else "prices_county")
    key = "tract" if area.single else "county"
    if table is None or "hpi_year" not in table.columns:
        return ""
    row = table[table[key] == area.geoid]
    year = pd.to_numeric(row["hpi_year"], errors="coerce").dropna()
    return str(int(year.iloc[0])) if len(year) else ""


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``housing`` section for a county or tract."""
    place, other = ctx.place(geoid)
    fm = FactMaker(ctx, KEY, place, other)
    years = ctx.acs_years()
    fm.add("housing_units", "Homes (housing units)", "units", SOURCE, years,
           lambda a: a.count("housing_units"), compare=False)  # fmt: skip
    for name, label, cols in TYPES:
        fm.add(name, label, "share", SOURCE, years,
               lambda a, cols=cols: a.ratio(list(cols), ["housing_units"]))  # fmt: skip
    fm.add("median_home_value", "Median home value (owner homes)", "dollars", SOURCE, years,
           lambda a: a.median("median_home_value", br.VALUE))  # fmt: skip
    fm.add("median_gross_rent", "Median gross rent", "dollars per month", SOURCE, years,
           lambda a: a.median("median_gross_rent", br.RENT))  # fmt: skip
    fm.add("vacancy_rate", "Vacant homes", "share", SOURCE, years,
           lambda a: a.ratio(["vacant_units"], ["housing_units"]))  # fmt: skip
    fm.add("share_built_before_1940", "Homes built before 1940", "share", SOURCE, years,
           lambda a: a.ratio(["built_1939_or_earlier"], ["housing_units"]))  # fmt: skip
    recent = ["built_2010_2019", "built_2020_plus"]
    fm.add("share_built_2010_plus", "Homes built in 2010 or later", "share", SOURCE, years,
           lambda a: a.ratio(recent, ["housing_units"]))  # fmt: skip

    hpi_source = "fhfa_hpi_tract" if place.single else "fhfa_hpi_county"
    hpi_year = _hpi_year(ctx, place)
    for name, label, column in (
        ("hpi_change_1yr", "House prices, change over the latest year", "hpi_annual_change_pct"),
        ("hpi_change_5yr", "House prices, change over five years", "hpi_change_5yr_pct"),
        ("hpi_change_10yr", "House prices, change over ten years", "hpi_change_10yr_pct"),
    ):
        fm.add(name, label, "percent change", hpi_source, hpi_year or "latest", _hpi(ctx, column),
               note="FHFA annual house price index; repeat sales of the same homes")  # fmt: skip

    detached, value = fm.get("share_single_detached"), fm.get("median_home_value")
    rent, hpi5 = fm.get("median_gross_rent"), fm.get("hpi_change_5yr")
    parts = []
    if detached and detached.value is not None:
        parts.append(f"{detached.text} of homes in {place.name} are single-family detached")
    if value and value.value is not None:
        parts.append(f"the median home value is {with_words(value)}")
    if rent and rent.value is not None:
        parts.append(f"the median rent is {rent.text}")
    if hpi5 and hpi5.value is not None:
        parts.append(f"prices changed {hpi5.text} in five years")
    limits = (
        "Survey estimates averaged over five years, with margins of error. County and state "
        "medians are estimated from bracket counts and can differ a little from the published "
        "medians. The price index tracks repeat sales; FHFA does not publish an index for tracts "
        "with too few sales, and the pack holds no state index to compare with."
    )
    return finish(KEY, TITLE, sentence(parts), fm.facts, limits)
