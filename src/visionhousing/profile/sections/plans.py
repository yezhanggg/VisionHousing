"""Profile section ``plans``: comprehensive plans (PA DCED Munstats, county plan citations).

Plain-language summary
----------------------
A comprehensive plan sets out where a community wants homes, jobs and roads
to go. Pennsylvania's Municipalities Planning Code requires each county to
have one; municipalities may adopt their own or join a multi-municipal plan.
This section shows:

- for a **county**: the share of its municipalities with a comprehensive plan
  (DCED record), the share of its people living in one, the share in a
  multi-municipal plan (compared with the state), and the county's own plan
  with its adoption year and link where it is in ``assumptions/plans.yaml``;
- for a **tract**: whether the municipality holding most of its homes has a
  plan and whether it is multi-municipal, and its county's plan.

The county plan list is a citation list checked by hand; a plan marked
"not confirmed" could not be checked on the county's own page.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.features.zoning_status import read_plan_citations
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence
from visionhousing.profile.sections.zoning import _munis, _tract_pieces

KEY = "plans"
TITLE = "Comprehensive plans"
SOURCE = "pa_dced_munstats"
PLAN_SOURCE = "county_comprehensive_plans"
YEAR = "current DCED record"


def _plan_fact(ctx: ProfileContext, county: str, measured_at: str) -> Fact | None:
    plan = read_plan_citations().get(county)
    if not plan:
        return None
    year = plan.get("adopted")
    text = plan["name"] + (f" (adopted {year})" if year else " (adoption year not found)")
    if not plan.get("verified"):
        text += "; not confirmed on the county's own page"
    note = f"{plan['url']} (read {plan['accessed']})"
    if plan.get("note"):
        note += f". {plan['note']}"
    return Fact(
        id=f"{KEY}.county_plan",
        label="County comprehensive plan",
        value=plan["name"],
        unit="text",
        text=text,
        source=PLAN_SOURCE,
        vintage=ctx.vintage(PLAN_SOURCE),
        year=str(year) if year else "not stated",
        measured_at=measured_at,
        note=note,
    )


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``plans`` section for a county or tract."""
    place, other = ctx.place(geoid)
    munis = _munis(ctx)
    munis["county"] = munis["county"].astype(str)
    for c in ("comprehensive_plan", "multi_municipal_plan"):
        munis[c] = pd.to_numeric(munis[c], errors="coerce")

    def in_area(area: Area) -> pd.DataFrame:
        rows = munis if area.level == "state" else munis[munis["county"].isin(area.counties)]
        return rows[rows["comprehensive_plan"].notna()]

    county = place.geoid if place.level == "county" else place.geoid[:5]
    if place.level == "county":
        if in_area(place).empty:
            raise MissingDataError(f"no DCED municipalities for county {county}")

        def muni_share(area: Area) -> Measure:
            rows = in_area(area)
            return Measure(float(rows["comprehensive_plan"].mean())) if len(rows) else Measure(None)

        def pop_share(col: str):
            def measure(area: Area) -> Measure:
                rows = in_area(area)
                total = rows["population"].sum()
                if total <= 0:
                    return Measure(None)
                return Measure(float(rows.loc[rows[col] == 1, "population"].sum() / total))

            return measure

        fm = FactMaker(ctx, KEY, place, other)
        fm.add(
            "municipalities_with_plan",
            "Share of municipalities with a comprehensive plan",
            "share",
            SOURCE,
            YEAR,
            muni_share,
        )
        fm.add(
            "people_with_plan",
            "Share of people in a municipality with a comprehensive plan",
            "share",
            SOURCE,
            "2024 population estimates",
            pop_share("comprehensive_plan"),
        )
        fm.add(
            "people_multi_municipal",
            "Share of people covered by a multi-municipal plan",
            "share",
            SOURCE,
            "2024 population estimates",
            pop_share("multi_municipal_plan"),
            note="a plan DCED lists with two or more member municipalities",
        )
        facts = list(fm.facts)
        shared = fm.get("municipalities_with_plan")
        lead = (
            f"{shared.text} of {place.name}'s municipalities have a comprehensive plan"
            if shared and shared.value is not None
            else ""
        )
    else:
        pieces = _tract_pieces(ctx, munis)
        mine = pieces[pieces["tract"].astype(str) == place.geoid].sort_values(
            "housing_units_2020", ascending=False
        )
        if mine.empty:
            raise MissingDataError(f"tract {place.geoid} is not in the municipality table")
        top = mine.iloc[0]
        row = munis[
            (munis["county"] == str(top["county"]))
            & (munis["cousub"].astype(str).str.zfill(5) == str(top["cousub"]))
        ]
        if row.empty or pd.isna(row["comprehensive_plan"].iloc[0]):
            raise MissingDataError(f"no DCED record for the municipality of tract {place.geoid}")
        r = row.iloc[0]

        def yes_no(v: object) -> str:
            return "yes" if v == 1 else "no"

        facts = [
            Fact(
                f"{KEY}.municipality_plan",
                f"{r['name']} has a comprehensive plan",
                yes_no(r["comprehensive_plan"]),
                "text",
                yes_no(r["comprehensive_plan"]),
                SOURCE,
                ctx.vintage(SOURCE),
                YEAR,
                "municipality",
                note="the municipality holding most of the tract's 2020 homes",
            ),
            Fact(
                f"{KEY}.municipality_multi",
                "Its plan is multi-municipal",
                yes_no(r["multi_municipal_plan"]),
                "text",
                yes_no(r["multi_municipal_plan"]),
                SOURCE,
                ctx.vintage(SOURCE),
                YEAR,
                "municipality",
            ),
        ]
        lead = (
            f"{r['name']}, which holds most of this tract's homes, "
            f"{'has a' if r['comprehensive_plan'] == 1 else 'has no'} comprehensive plan"
        )
    plan = _plan_fact(ctx, county, "county")
    parts = [lead]
    if plan is not None:
        facts.append(plan)
        parts.append(f"the county plan is {plan.text}")
    limits = (
        "DCED's record says whether a plan exists, not how old it is or what it says. "
        "Municipalities report it themselves. County plans are listed only for large counties, "
        "from each county's own page; a plan marked not confirmed could not be checked there."
    )
    return finish(KEY, TITLE, sentence(parts), facts, limits)
