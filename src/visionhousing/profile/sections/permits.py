"""Profile section ``permits``: homes authorized by building permits (Census Bureau survey).

Plain-language summary
----------------------
The Census Bureau's Building Permits Survey (BPS) counts housing units
authorized by permits each year, by county and by building type. Permits are
only counted by county, so a tract's profile shows its county's numbers and
says so (``measured_at`` is ``county``).

Facts:

- units permitted in the last five years and in the five years before, and the
  change between the two (the recent trend);
- the yearly pace per 1,000 existing homes (five-year total / 5 / the ACS
  housing units x 1,000), so places of different size can be compared;
- the share of the last five years' units in single-family (1-unit)
  buildings, and the units in buildings of 5 or more;
- units permitted in each of the last ten years, latest first.

The pace and the single-family share are compared with the state (the sum of
its counties). Years the Census Bureau partly estimated ("imputed") are named
in the limits.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.model import Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence

KEY = "permits"
TITLE = "Permits"
SOURCE = "bps_county_annual"
TYPES = ("units_1", "units_2", "units_3_4", "units_5plus")
WINDOW = 5
SERIES_YEARS = 10


def _frame(ctx: ProfileContext) -> pd.DataFrame:
    permits = ctx.need("permits_county_year").copy()
    for c in (*TYPES, "year"):
        permits[c] = pd.to_numeric(permits[c], errors="coerce")
    permits["total"] = permits[list(TYPES)].sum(axis=1, min_count=1)
    return permits


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``permits`` section; a tract gets its county's numbers."""
    place, _ = ctx.place(geoid)
    county = place if place.level == "county" else ctx.county_area(place.counties[0])
    state = ctx.state_area()
    permits = _frame(ctx)
    latest = int(permits["year"].max())
    recent = range(latest - WINDOW + 1, latest + 1)
    before = range(latest - 2 * WINDOW + 1, latest - WINDOW + 1)

    def rows(area: Area) -> pd.DataFrame:
        return permits[permits["county"].isin(area.counties)]

    def total(years: range, col: str = "total"):
        def measure(area: Area) -> Measure:
            r = rows(area)
            r = r[r["year"].isin(list(years))]
            if r.empty:
                return Measure(None)
            return Measure(float(r[col].sum()))

        return measure

    def change(area: Area) -> Measure:
        now, then = total(recent)(area).value, total(before)(area).value
        if now is None or not then:
            return Measure(None)
        return Measure((now / then - 1) * 100)

    def pace(area: Area) -> Measure:
        units = total(recent)(area).value
        homes = area.count("housing_units").value
        if units is None or not homes:
            return Measure(None)
        return Measure(units / WINDOW / homes * 1000)

    def single_share(area: Area) -> Measure:
        one, all_ = total(recent, "units_1")(area).value, total(recent)(area).value
        if one is None or not all_:
            return Measure(None)
        return Measure(one / all_)

    fm = FactMaker(ctx, KEY, county, state)
    span = f"{recent.start}-{recent.stop - 1}"
    span_before = f"{before.start}-{before.stop - 1}"
    fm.add("total_5yr", f"Units permitted, {span}", "units", SOURCE, span, total(recent),
           compare=False)  # fmt: skip
    fm.add("total_prior_5yr", f"Units permitted, {span_before}", "units", SOURCE, span_before,
           total(before), compare=False)  # fmt: skip
    fm.add("change_5yr", f"Change, {span} against {span_before}", "percent change", SOURCE,
           f"{span_before} to {span}", change)  # fmt: skip
    fm.add("pace_per_1000", "Units permitted a year per 1,000 existing homes", "per 1,000 homes",
           SOURCE, span, pace,
           note=f"{span} yearly average / ACS {ctx.acs_years()} housing units x 1,000")  # fmt: skip
    fm.add("single_family_share", f"Share in single-family buildings, {span}", "share", SOURCE,
           span, single_share)  # fmt: skip
    fm.add("units_5plus_5yr", f"Units in buildings of 5 or more, {span}", "units", SOURCE, span,
           total(recent, "units_5plus"), compare=False)  # fmt: skip
    for year in range(latest, latest - SERIES_YEARS, -1):
        fm.add(f"year_{year}", f"Units permitted in {year}", "units", SOURCE, str(year),
               total(range(year, year + 1)), compare=False)  # fmt: skip

    if rows(county).empty:
        raise MissingDataError(f"no permit rows for county {county.geoid}")
    total5, chg, single = fm.get("total_5yr"), fm.get("change_5yr"), fm.get("single_family_share")
    where = county.name if place.level == "county" else f"{county.name}, which holds this tract,"
    parts = []
    if total5 and total5.value is not None:
        lead = f"{where} permitted {total5.text} homes in {span}"
        if chg and chg.value is not None:
            lead += f" ({chg.text} against {span_before})"
        parts.append(lead)
    if single and single.value is not None:
        parts.append(f"{single.text} were in single-family buildings")

    window = permits[permits["county"].isin(county.counties) & (permits["year"] >= before.start)]
    flagged: list[int] = []
    if "imputed" in window.columns:
        marked = pd.to_numeric(window["imputed"], errors="coerce") == 1
        flagged = sorted(int(y) for y in window.loc[marked, "year"])
    limits = (
        "Counts homes authorized by permit, not homes started or finished; some permitted homes "
        "are never built. Permits are counted by county only, so a tract shows its county. "
        "The state figure is the sum of its counties."
    )
    if flagged:
        years = ", ".join(map(str, flagged))
        limits += f" The Census Bureau partly estimated these years for this county: {years}."
    return finish(KEY, TITLE, sentence(parts), fm.facts, limits)
