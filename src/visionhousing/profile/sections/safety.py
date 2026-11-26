"""Profile section ``safety``: reported crime and signs of neighbourhood distress.

Plain-language summary
----------------------
Two sources, each used when the pack has it:

**City records, no key needed** (``safety_local``; Philadelphia, see
:mod:`visionhousing.features.safety_local`). For a Philadelphia county or tract:

- shooting victims, violent incidents (homicide, rape, robbery, aggravated
  assault), property incidents (burglary, theft, vehicle theft, arson), L&I
  code-violation cases and neglect-related 311 requests;
- for each: the yearly average per 1,000 residents over the last
  ``window_years`` full years (``assumptions/safety_local.yaml``), the count
  in the latest year, and the change from the first to the last year of the
  window (the trend);
- a tract is compared with its county (Philadelphia as a whole). The county
  itself has no statewide comparison: the city's records cover only the city.

Rate = records in the window / (residents x years) x 1,000, residents from the
ACS 2020-2024. Trend = latest year's count / first year's count - 1.

**FBI Crime Data Explorer** (``safety_county``; needs an api.data.gov key when
the pack is built, see :mod:`visionhousing.features.safety`): violent and
property offenses per 1,000 residents of the areas of agencies that reported
all year, by county, compared with the state. A tract shows its county.
"""

from __future__ import annotations

import dataclasses

import pandas as pd

from visionhousing.features import safety_local as local_fx
from visionhousing.profile.context import Area, Measure, ProfileContext
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence

KEY = "safety"
TITLE = "Safety"
SOURCE = "fbi_cde_agency"
RATE_UNIT = "per 1,000 residents"

FBI_LIMITS = (
    "FBI figures are offenses known to police, not all crime: many offenses are never "
    "reported. Rates cover only the areas of agencies that reported all twelve months, so a "
    "county with low coverage may be misread. Pennsylvania moved to incident-based reporting "
    "(NIBRS) around 2021, which changed how offenses are counted; do not compare with older "
    "years. Agencies are placed in the first county the FBI lists for them. FBI numbers are "
    "by county only; a tract shows its county."
)
LOCAL_LIMITS = (
    "City records are incidents known to police, victims of shootings, code-violation cases "
    "and 311 requests: they show where things were reported, not the risk to any one person. "
    "Many offenses are never reported, and 311 requests and code cases also reflect how often "
    "residents call and where inspectors go. Records are placed by their published location "
    "(mostly the hundred-block), so a few near a tract edge may fall in the next tract. Rates "
    "divide by residents, so tracts with many visitors and few residents (downtown, stadiums) "
    "look high; tracts with too few residents get no rate. The City revises records after "
    "they are first published."
)
NO_DATA = (
    "No safety numbers for this place yet. Keyless city records are read for Philadelphia "
    "only (Allegheny County's police files are out of date or have no stated licence). FBI "
    "county figures appear when the pack is built with an api.data.gov key."
)
LOCAL_LABELS = {
    "shootings": "Shooting victims",
    "violent": "Violent incidents",
    "property": "Property incidents",
    "li_cases": "L&I code-violation cases",
    "requests_311": "Neglect-related 311 requests",
}
"""Measures shown with a rate and a trend (``shootings_fatal`` is shown as a count only)."""


def _rate_text(value: float | None) -> str:
    if value is None:
        return "not available"
    digits = 2 if abs(value) < 1 else 1
    return f"{value:.{digits}f} {RATE_UNIT}"


def _with_rate_text(fact: Fact) -> Fact:
    """Show rates with decimals (the shared formatter rounds this unit to whole numbers)."""
    comparison = fact.comparison
    if comparison is not None:
        comparison = dataclasses.replace(comparison, text=_rate_text(comparison.value))
    value = None if fact.value is None else float(fact.value)  # type: ignore[arg-type]
    return dataclasses.replace(fact, text=_rate_text(value), comparison=comparison)


def _trend_words(fact: Fact | None) -> str:
    if fact is None or fact.value is None:
        return ""
    v = float(fact.value)  # type: ignore[arg-type]
    if abs(v) < 2:
        return "stayed about flat"
    return f"{'rose' if v > 0 else 'fell'} {abs(v):.0f}%"


# --------------------------------------------------------------------------- FBI


def _fbi(ctx: ProfileContext, place: Area) -> tuple[list[Fact], list[str]]:
    table = ctx.table("safety_county")
    if table is None or table.empty:
        return [], []
    county = place if place.level == "county" else ctx.county_area(place.counties[0])
    state = ctx.state_area()
    table = table.copy()
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
    return facts, parts


# --------------------------------------------------------------------------- city records


def _local(ctx: ProfileContext, place: Area, other: Area) -> tuple[list[Fact], list[str]]:
    """Facts from ``safety_local`` and the summary sentences (finished, with full stops)."""
    table = ctx.table("safety_local")
    if table is None or table.empty:
        return [], []
    t = table.copy()
    t["geoid"] = t["geoid"].astype(str)
    t["county"] = t["county"].astype(str)
    t["year"] = pd.to_numeric(t["year"], errors="coerce")
    county = place.geoid[:5]
    if county not in set(t["county"]):
        return [], []
    rules = local_fx.load_rules()
    years = sorted(int(y) for y in t.loc[t["county"] == county, "year"].dropna().unique())
    window = years[-int(rules["window_years"]) :]
    first, last = window[0], window[-1]
    span = f"{first}-{last}" if first != last else str(last)
    min_pop = float(rules["min_tract_population"])
    min_trend = max(float(rules["min_trend_count"]), 1.0)

    def rows(area: Area) -> pd.DataFrame:
        if area.level == "tract":
            return t[(t["level"] == "tract") & (t["geoid"] == area.geoid)]
        if area.level == "county":
            return t[(t["level"] == "county") & t["geoid"].isin(area.counties)]
        return t.iloc[0:0]

    def count(m: str, yrs: list[int]):
        def measure(area: Area) -> Measure:
            r = rows(area)
            r = r[r["year"].isin(yrs)]
            if r.empty or m not in r.columns:
                return Measure(None)
            return Measure(float(pd.to_numeric(r[m], errors="coerce").sum()))

        return measure

    def rate(m: str):
        def measure(area: Area) -> Measure:
            r = rows(area)
            n = count(m, window)(area).value
            pop = pd.to_numeric(r["population"], errors="coerce").max() if len(r) else None
            if n is None or pop is None or pd.isna(pop) or pop <= 0:
                return Measure(None)
            if area.level == "tract" and pop < min_pop:
                return Measure(None, note=f"fewer than {min_pop:,.0f} residents: no rate")
            return Measure(n / (float(pop) * len(window)) * 1000)

        return measure

    def trend(m: str):
        def measure(area: Area) -> Measure:
            a, b = count(m, [first])(area).value, count(m, [last])(area).value
            if a is None or b is None or a < min_trend:
                return Measure(None, note=f"fewer than {min_trend:,.0f} records in {first}: "
                                          "no trend")  # fmt: skip
            return Measure((b / a - 1) * 100)

        return measure

    compare_to = other if place.level == "tract" else None
    fm = FactMaker(ctx, KEY, place, compare_to)
    scope = "" if place.level == "tract" else "; city records, so no statewide figure"
    for m, label in LOCAL_LABELS.items():
        if m not in t.columns:
            continue
        sid = local_fx.MEASURES[m][2]
        fm.add(f"{m}_per_1000", f"{label} per 1,000 residents, yearly average {span}",
               RATE_UNIT, sid, span, rate(m),
               note=f"records {span} / (ACS {ctx.acs_years()} residents x {len(window)} years) "
                    f"x 1,000{scope}")  # fmt: skip
        fm.add(f"{m}_{last}", f"{label}, {last}", local_fx.MEASURES[m][0], sid, str(last),
               count(m, [last]), compare=False)  # fmt: skip
        if first != last:
            fm.add(f"{m}_trend", f"{label}: change {first} to {last}", "percent change", sid,
                   span, trend(m), note=f"{last} count / {first} count - 1")  # fmt: skip
    if "shootings_fatal" in t.columns:
        fm.add(f"shootings_fatal_{last}", f"Shooting victims who died, {last}", "victims",
               local_fx.MEASURES["shootings_fatal"][2], str(last), count("shootings_fatal", [last]),
               compare=False)  # fmt: skip
    facts = [_with_rate_text(f) if f.unit == RATE_UNIT else f for f in fm.facts]
    by_id = {f.id: f for f in facts}

    def said(m: str, what: str) -> str:
        f = by_id.get(f"{KEY}.{m}_per_1000")
        if f is None or f.value is None:
            return ""
        words = f" ({f.comparison.words})" if f.comparison and f.comparison.words else ""
        return f"{f.text.replace(' ' + RATE_UNIT, '')} {what}{words}"

    def listed(items: list[str]) -> str:
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]

    parts = []
    lead = [s for s in (said("shootings", "shooting victims"), said("violent", "violent incidents"),
                        said("property", "property incidents")) if s]  # fmt: skip
    if lead:
        parts.append(f"City records for {place.name}, yearly average per 1,000 residents in "
                     f"{span}: {listed(lead)}.")  # fmt: skip
    distress = [s for s in (said("li_cases", "code-violation cases"),
                            said("requests_311", "neglect-related 311 requests")) if s]  # fmt: skip
    if distress:
        parts.append(f"Signs of neglect: {listed(distress)} per 1,000 residents a year.")
    moves = []
    for m, what in (("violent", "violent incidents"), ("shootings", "shooting victims")):
        w = _trend_words(by_id.get(f"{KEY}.{m}_trend"))
        if w:
            moves.append(f"{what} {w}")
    if moves:
        parts.append(f"From {first} to {last}, {listed(moves)}.")
    if parts and place.level != "tract":
        parts.append("These are city records, so there is no statewide figure to compare with.")
    return facts, parts


# --------------------------------------------------------------------------- section


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``safety`` section from city records and/or the FBI table."""
    place, other = ctx.place(geoid)
    local_facts, local_parts = _local(ctx, place, other)
    fbi_facts, fbi_parts = _fbi(ctx, place)
    facts = local_facts + fbi_facts
    if not facts:
        return Section.no_data(KEY, TITLE, NO_DATA)
    limits = " ".join(x for x in (LOCAL_LIMITS if local_facts else "",
                                  FBI_LIMITS if fbi_facts else "") if x)  # fmt: skip
    summary = " ".join(s for s in (*local_parts, sentence(fbi_parts)) if s)
    return finish(KEY, TITLE, summary, facts, limits)
