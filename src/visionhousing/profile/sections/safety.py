"""Profile section ``safety``: offenses reported to police, per 1,000 residents, by county.

Plain-language summary
----------------------
From the FBI Crime Data Explorer (see :mod:`visionhousing.features.safety`):

- violent offenses (murder, rape, robbery, aggravated assault) per 1,000
  residents of the areas served by agencies that reported all year;
- property offenses (burglary, larceny-theft, motor-vehicle theft, arson) per
  1,000 residents, the same way;
- how many of the county's agencies reported all year, and the share of the
  county's residents their areas cover.

Rates = offenses / population served by the full-year agencies x 1,000. The
state rate adds up the counties the same way. Numbers are by county only, so a
tract shows its county and says so (``measured_at`` is ``county``).
"""

from __future__ import annotations

import dataclasses

import pandas as pd

from visionhousing.profile.context import Area, Measure, ProfileContext
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence

KEY = "safety"
TITLE = "Safety"
SOURCE = "fbi_cde_agency"
RATE_UNIT = "per 1,000 residents"

LIMITS = (
    "These are offenses known to police, not all crime: many offenses are never reported. "
    "Rates cover only the areas of agencies that reported all twelve months, so a county "
    "with low coverage may be misread. Pennsylvania moved to incident-based reporting (NIBRS) "
    "around 2021, which changed how offenses are counted; do not compare with older years. "
    "Agencies are placed in the first county the FBI lists for them. Numbers are by county "
    "only; a tract shows its county. Rates describe where offenses were reported, not the "
    "risk to any one person."
)


def _rate_text(value: float | None) -> str:
    return "not available" if value is None else f"{value:.1f} {RATE_UNIT}"


def _with_rate_text(fact: Fact) -> Fact:
    """Show rates with one decimal (the shared formatter rounds this unit to whole numbers)."""
    comparison = fact.comparison
    if comparison is not None:
        comparison = dataclasses.replace(comparison, text=_rate_text(comparison.value))
    value = None if fact.value is None else float(fact.value)  # type: ignore[arg-type]
    return dataclasses.replace(fact, text=_rate_text(value), comparison=comparison)


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``safety`` section; a tract gets its county's numbers."""
    place, _ = ctx.place(geoid)
    county = place if place.level == "county" else ctx.county_area(place.counties[0])
    state = ctx.state_area()
    table = ctx.need("safety_county").copy()
    table["county"] = table["county"].astype(str)
    year = str(int(pd.to_numeric(table["year"], errors="coerce").max()))

    def rows(area: Area) -> pd.DataFrame:
        return table[table["county"].isin(area.counties)]

    def total(column: str):
        def measure(area: Area) -> Measure:
            r = rows(area)
            return Measure(
                float(pd.to_numeric(r[column], errors="coerce").sum()) if len(r) else None
            )

        return measure

    def rate(column: str):
        def measure(area: Area) -> Measure:
            offenses = total(column)(area).value
            people = total("population_covered")(area).value
            if offenses is None or not people:
                return Measure(None)
            return Measure(offenses / people * 1000)

        return measure

    def coverage(area: Area) -> Measure:
        covered = total("population_covered")(area).value
        residents = area.count("population").value
        if covered is None or not residents:
            return Measure(None)
        return Measure(min(covered / residents, 1.0))

    fm = FactMaker(ctx, KEY, county, state)
    note = "offenses / residents served by agencies that reported all 12 months x 1,000"
    fm.add("violent_per_1000", "Violent offenses per 1,000 residents", RATE_UNIT, SOURCE, year,
           rate("violent"), note=note, measured_at="county")  # fmt: skip
    fm.add("property_per_1000", "Property offenses per 1,000 residents", RATE_UNIT, SOURCE, year,
           rate("property"), note=note, measured_at="county")  # fmt: skip
    fm.add("agencies", "Police agencies listed (city, county, State Police)", "agencies", SOURCE,
           year, total("agencies"), compare=False, measured_at="county")  # fmt: skip
    fm.add("agencies_full_year", "Of which reported all 12 months", "agencies", SOURCE, year,
           total("agencies_full_year"), compare=False, measured_at="county")  # fmt: skip
    fm.add("coverage_share", "Share of residents in full-year agencies' areas", "share", SOURCE,
           year, coverage, measured_at="county",
           note=f"population served / ACS {ctx.acs_years()} residents")  # fmt: skip
    facts = [_with_rate_text(f) if f.unit == RATE_UNIT else f for f in fm.facts]

    by_id = {f.id: f for f in facts}
    violent, prop = by_id.get(f"{KEY}.violent_per_1000"), by_id.get(f"{KEY}.property_per_1000")
    where = county.name if place.level == "county" else f"{county.name} (this tract's county)"
    parts = []
    if violent and violent.value is not None:
        words = f", {violent.comparison.words}" if violent.comparison else ""
        parts.append(f"In {year}, police in {where} reported {violent.text.replace(RATE_UNIT, '')}"
                     f"violent offenses per 1,000 residents{words}")  # fmt: skip
    if prop and prop.value is not None:
        words = f" ({prop.comparison.words})" if prop.comparison else ""
        parts.append(f"{prop.text.split(' ')[0]} property offenses{words}")
    return finish(KEY, TITLE, sentence(parts), facts, LIMITS)
