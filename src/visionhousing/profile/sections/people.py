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
- **Median household income** (B19013): the Bureau's published median for a
  tract, a county or the state (county and state medians are in the pack's
  ``acs_bands`` table). A pack without that table falls back, for a county or
  the state, to the average of its tracts' medians weighted by households,
  marked approximate.
- **Share of households renting**: renter households / all households (B25003).
- **Rent-to-income gauge**: median gross rent x 12 / median household income.
  It is a rough gauge only; the real share of renters paying 30% or more of
  income (B25070) is in the ``housing`` section.

A county is compared with the state, a tract with its county.

Charts (see :mod:`visionhousing.profile.charts`): age (6 bands, B01001), race
and ethnicity (B03002), household income (6 bands, B19001), and, for counties
and the state, household size (B11016) and education of adults aged 25 or older
(B15003). Each shows the bigger area's shares next to the place's.

Headline: residents. Summary points: residents and households, median
household income, and the largest age group.
"""

from __future__ import annotations

from visionhousing.features import brackets as br
from visionhousing.profile.charts import band_chart, largest, published_median
from visionhousing.profile.context import Area, Measure, ProfileContext
from visionhousing.profile.format import fmt
from visionhousing.profile.model import Section
from visionhousing.profile.sections._common import FactMaker, finish, point, sentence, with_words

KEY = "people"
TITLE = "People"
SOURCE = "acs5_summary_file"
HOUSEHOLDS = ("households_owner", "households_renter")
INCOME_NOTE = (
    "approximate for a county or state: the average of its tracts' medians, weighted by households"
)


def _income_of(ctx: ProfileContext):
    def measure(area: Area) -> Measure:
        published = published_median(ctx, area, "median_household_income")
        if published is not None:
            return published
        m = area.weighted_mean("median_household_income", list(HOUSEHOLDS))
        return m if area.single else Measure(m.value, None, INCOME_NOTE)

    return measure


def _rent_gauge_of(ctx: ProfileContext):
    def measure(area: Area) -> Measure:
        rent = published_median(ctx, area, "median_gross_rent") or area.median(
            "median_gross_rent", br.RENT
        )
        income = _income_of(ctx)(area).value
        if not rent.value or not income:
            return Measure(None)
        return Measure(rent.value * 12 / income)

    return measure


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
    fm.add("median_household_income", "Median household income", "dollars", SOURCE, years,
           _income_of(ctx))  # fmt: skip
    fm.add("renter_share", "Households that rent", "share", SOURCE, years,
           lambda a: a.ratio(["households_renter"], list(HOUSEHOLDS)))  # fmt: skip
    fm.add(
        "rent_to_income",
        "Median rent as a share of median income (rough gauge)",
        "share",
        SOURCE,
        years,
        _rent_gauge_of(ctx),
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
    charts = [
        band_chart(ctx, KEY, "age", "age", "bar", place, other, title="Residents by age"),
        band_chart(ctx, KEY, "race", "race", "pie", place, other),
        band_chart(ctx, KEY, "income", "income", "bar", place, other, title="Households by income"),
        band_chart(
            ctx, KEY, "household_size", "hhsize", "bar", place, other, title="Households by size"
        ),
        band_chart(ctx, KEY, "education", "edu", "bar", place, other),
    ]
    points = [
        point(pop, "{text} residents" + (f" in {hh.text} households" if hh and hh.value else "")),
        point(income, "Median household income {text}{words}"),
    ]
    top_age = largest(charts[0])
    if top_age is not None:
        points.append(f"Largest age group: {top_age.label} ({fmt(top_age.share, 'share')})")
    limits = (
        "Survey estimates averaged over five years, with margins of error; small tracts often have "
        "wide margins. County and state medians are the Bureau's published medians where the "
        "pack has them, else approximate (tract medians weighted by households). The "
        "rent-to-income gauge is only a rough stand-in for rent burden, which the Homes section "
        "shows. Household size and education are shown for counties and the "
        "state only, to keep the data pack small."
    )
    return finish(KEY, TITLE, summary, fm.facts, limits, charts=charts, lead=pop, points=points)
