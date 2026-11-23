"""Profile section ``people``: who lives in the place (American Community Survey).

Plain-language summary
----------------------
Population, households, the share of residents aged 65 or older, median
household income, the share of households that rent, and a rough rent-to-income
gauge, all from the American Community Survey 2020-2024 5-year estimates (ACS).

How each number is worked out:

- **Population, households**: sums of the tracts. Households = owner-occupied
  plus renter-occupied homes (B25003). The margin of error is the Census
  Bureau's approximation for a sum.
- **Share aged 65 or older** (B01001): a tract's published share; for a county
  or the state, the tracts' shares weighted by population (which equals the
  share of the summed counts).
- **Median household income** (B19013): a tract's published median. The pack
  has no county or state income brackets, so a county's or state's figure is
  the average of its tracts' medians weighted by households, and is marked
  approximate.
- **Share of households renting**: renter households / all households (B25003).
- **Rent-to-income gauge**: median gross rent x 12 / median household income.
  It is a rough gauge only. It is not the share of renters who pay more than
  30% of income (table B25070 is not in the pack yet), and renters usually earn
  less than the median household.

A county is compared with the state, a tract with its county.
"""

from __future__ import annotations

from visionhousing.features import brackets as br
from visionhousing.profile.context import Area, Measure, ProfileContext
from visionhousing.profile.model import Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence, with_words

KEY = "people"
TITLE = "People"
SOURCE = "acs5_summary_file"
HOUSEHOLDS = ("households_owner", "households_renter")
INCOME_NOTE = (
    "approximate for a county or state: the average of its tracts' medians, weighted by households"
)


def _income(area: Area) -> Measure:
    m = area.weighted_mean("median_household_income", list(HOUSEHOLDS))
    return m if area.single else Measure(m.value, None, INCOME_NOTE)


def _rent_gauge(area: Area) -> Measure:
    rent = area.median("median_gross_rent", br.RENT).value
    income = _income(area).value
    if not rent or not income:
        return Measure(None)
    return Measure(rent * 12 / income)


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``people`` section for a county or tract."""
    place, other = ctx.place(geoid)
    fm = FactMaker(ctx, KEY, place, other)
    years = ctx.acs_years()
    fm.add("population", "Residents", "persons", SOURCE, years, lambda a: a.count("population"),
           compare=False)  # fmt: skip
    fm.add("households", "Households (occupied homes)", "households", SOURCE, years,
           lambda a: a.count(*HOUSEHOLDS), compare=False)  # fmt: skip
    fm.add("share_age_65plus", "Residents aged 65 or older", "share", SOURCE, years,
           lambda a: a.weighted_mean("share_age_65plus", "population"))  # fmt: skip
    fm.add("median_household_income", "Median household income", "dollars", SOURCE, years, _income)
    fm.add("renter_share", "Households that rent", "share", SOURCE, years,
           lambda a: a.ratio(["households_renter"], list(HOUSEHOLDS)))  # fmt: skip
    fm.add(
        "rent_to_income",
        "Median rent as a share of median income (rough gauge)",
        "share",
        SOURCE,
        years,
        _rent_gauge,
        note="median gross rent x 12 / median household income; not the share of rent-burdened "
        "renters",
    )
    pop, hh = fm.get("population"), fm.get("households")
    income, renters = fm.get("median_household_income"), fm.get("renter_share")
    parts = []
    if pop and pop.value is not None:
        lead = f"About {pop.text} people live in {place.name}"
        if hh and hh.value is not None:
            lead += f" in {hh.text} households"
        parts.append(lead)
    if income and income.value is not None:
        parts.append(f"the median household income is {with_words(income)}")
    if renters and renters.value is not None:
        parts.append(f"{renters.text} of households rent")
    summary = sentence(parts)
    limits = (
        "Survey estimates averaged over five years, with margins of error; small tracts often have "
        "wide margins. County and state income are approximate (tract medians weighted by "
        "households). Rent burden (the share of renters paying over 30% of income) is not in the "
        "data pack yet; the rent-to-income gauge is only a rough stand-in."
    )
    return finish(KEY, TITLE, summary, fm.facts, limits)
