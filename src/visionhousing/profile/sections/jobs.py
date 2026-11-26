"""Profile section ``jobs``: the jobs located in a place, who holds them, and its businesses.

Plain-language summary
----------------------
Facts come from two Census Bureau products (see
:mod:`visionhousing.data.sources.jobs`):

- **LODES** workplace counts (all jobs, by the block where people work), summed
  over the place's tracts:

  - jobs located in the place;
  - jobs per resident = jobs / ACS residents (above 1 means the place draws
    workers from elsewhere; below 1 means many residents work elsewhere);
  - share of jobs paying more than $3,333 a month;
  - the largest industry sectors and their shares of jobs.

- **LODES** home-to-work flows, by county (a tract shows its county):

  - share of the county's jobs held by people who live outside the county;
  - share of employed residents who work in their home county (residents who
    work in another state are not counted, so this share is a ceiling).

- **County Business Patterns** (CBP), by county:

  - establishments with paid employees;
  - average yearly pay per employee = annual payroll / mid-March employment.

A county is compared with the state, a tract with its county. County-level
facts are compared with the state; for the state the counties are added up.

Charts (see :mod:`visionhousing.profile.charts`):

- **Jobs by sector** (a ranked bar list): the largest NAICS sectors by jobs
  located in the place (LODES), with the rest added up as "All other sectors",
  and the bigger area's shares of the same sectors.
- **How workers get to work** (a pie): ACS table B08301 for the workers who
  live in the place (drove alone, carpooled, transit, walked or cycled, other,
  worked from home).

Headline: jobs located in the place. Summary points: jobs per resident, the
largest sector and the share of workers who drive alone.

Named largest employers are not shown: Pennsylvania's Center for Workforce
Information publishes them only as PDF files.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.features.jobs import SECTOR_NAMES
from visionhousing.profile.charts import band_chart, items_from_counts
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.format import fmt
from visionhousing.profile.model import Chart, ChartCompare, Fact, Section
from visionhousing.profile.sections._common import FactMaker, finish, point, sentence

KEY = "jobs"
TITLE = "Jobs and employers"
WAC = "lodes_wac"
OD = "lodes_od"
CBP = "cbp_county"
TOP_SECTORS = 3
CHART_SECTORS = 6
"""Sectors shown one by one in the sector chart; the rest are added up."""

LIMITS = (
    "Jobs are counted where people work, from unemployment-insurance records and federal "
    "civilian jobs; self-employed people, the military and informal work are left out, and "
    "a person with two jobs counts twice. The Census Bureau adds noise to small counts, so a "
    "tract's numbers are rough. Commuting shares are by county and leave out residents who "
    "work in another state. Business counts are private establishments with paid employees "
    "(County Business Patterns, mid-March); government jobs are not in them. Named largest "
    "employers are not shown: the state publishes them only as PDF files."
)


def _sector_cols(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c.startswith("jobs_naics_")]


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``jobs`` section for a county or tract."""
    place, other = ctx.place(geoid)
    county = place if place.level == "county" else ctx.county_area(place.counties[0])
    state = ctx.state_area()
    per_tract = ctx.need("jobs_tract").copy()
    per_tract["tract"] = per_tract["tract"].astype(str)
    per_county = ctx.table("jobs_county")
    sectors = _sector_cols(per_tract)

    def rows(area: Area) -> pd.DataFrame:
        return per_tract[per_tract["tract"].isin(set(area.tracts["geoid"].astype(str)))]

    def jobs_sum(column: str):
        def measure(area: Area) -> Measure:
            r = rows(area)
            if r.empty:
                return Measure(None)
            return Measure(float(pd.to_numeric(r[column], errors="coerce").sum()))

        return measure

    def share_of_jobs(column: str):
        def measure(area: Area) -> Measure:
            total, part = jobs_sum("jobs")(area).value, jobs_sum(column)(area).value
            if not total or part is None:
                return Measure(None)
            return Measure(part / total)

        return measure

    def per_resident(area: Area) -> Measure:
        jobs = jobs_sum("jobs")(area).value
        people = area.count("population").value
        if jobs is None or not people:
            return Measure(None)
        return Measure(jobs / people)

    fm = FactMaker(ctx, KEY, place, other)
    fm.add("jobs_located", "Jobs located in the place", "jobs", WAC, ctx.vintage(WAC),
           jobs_sum("jobs"), compare=False)  # fmt: skip
    fm.add("jobs_per_resident", "Jobs per resident", "ratio", WAC, ctx.vintage(WAC),
           per_resident, note=f"LODES jobs / ACS {ctx.acs_years()} residents")  # fmt: skip
    fm.add("high_earning_share", "Share of jobs paying more than $3,333 a month", "share", WAC,
           ctx.vintage(WAC), share_of_jobs("jobs_earn_high"))  # fmt: skip

    top = rows(place)[sectors].apply(pd.to_numeric, errors="coerce").sum()
    top = top[top > 0].sort_values(ascending=False, kind="stable").head(TOP_SECTORS)
    for rank, column in enumerate(top.index, start=1):
        code = column.rsplit("_", 1)[1]
        name = SECTOR_NAMES.get(code, code)
        fm.add(f"sector_{rank}", f"Sector no. {rank} by jobs: {name}", "share", WAC,
               ctx.vintage(WAC), share_of_jobs(column),
               note=f"share of the place's jobs in NAICS {code}")  # fmt: skip

    facts: list[Fact] = list(fm.facts)
    fc = FactMaker(ctx, KEY, county, state)
    if per_county is not None and not per_county.empty:
        pc = per_county.copy()
        pc["county"] = pc["county"].astype(str)

        def county_ratio(num: str, den: str):
            def measure(area: Area) -> Measure:
                r = pc[pc["county"].isin(area.counties)]
                if r.empty or num not in r.columns or den not in r.columns:
                    raise MissingDataError(f"jobs_county lacks {num} or {den}")
                n = pd.to_numeric(r[num], errors="coerce").sum()
                d = pd.to_numeric(r[den], errors="coerce").sum()
                return Measure(float(n / d) if d > 0 else None)

            return measure

        def county_total(column: str):
            def measure(area: Area) -> Measure:
                r = pc[pc["county"].isin(area.counties)]
                if column not in r.columns:
                    raise MissingDataError(f"jobs_county lacks {column}")
                values = pd.to_numeric(r[column], errors="coerce")
                return Measure(float(values.sum()) if values.notna().any() else None)

            return measure

        def avg_pay(area: Area) -> Measure:
            ratio = county_ratio("cbp_annual_payroll_k", "cbp_employment")(area).value
            return Measure(None if ratio is None else ratio * 1000)

        fc.add("inflow_share", "Share of the county's jobs held by people living outside it",
               "share", OD, ctx.vintage(OD), county_ratio("inflow", "jobs"),
               measured_at="county")  # fmt: skip
        fc.add("work_in_home_county", "Share of employed residents working in their home county",
               "share", OD, ctx.vintage(OD),
               county_ratio("live_and_work", "employed_residents_in_state"),
               note="residents working in another state are not counted",
               measured_at="county")  # fmt: skip
        fc.add("establishments", "Business establishments with paid employees", "establishments",
               CBP, ctx.vintage(CBP), county_total("cbp_establishments"), compare=False,
               measured_at="county")  # fmt: skip
        fc.add("avg_pay", "Average yearly pay per employee (private establishments)",
               "dollars per year", CBP, ctx.vintage(CBP), avg_pay,
               note="annual payroll / mid-March employment; the state is the sum of its counties",
               measured_at="county")  # fmt: skip
        facts += fc.facts

    charts = [
        _sector_chart(ctx, place, other, rows, sectors),
        band_chart(
            ctx,
            KEY,
            "commute_mode",
            "commute",
            "pie",
            place,
            other,
            note="workers who live in the place, wherever they work",
        ),
    ]
    jobs, ratio = fm.get("jobs_located"), fm.get("jobs_per_resident")
    points = [point(ratio, "{text} jobs per resident{words}")]
    first_sector = fm.get("sector_1")
    if first_sector and first_sector.value is not None:
        name = first_sector.label.split(": ", 1)[1]
        points.append(f"Largest sector: {name.lower()} ({first_sector.text} of jobs)")
    commute = charts[1]
    if commute is not None:
        alone = next((i for i in commute.items if i.key == "drove_alone"), None)
        if alone is not None and alone.share is not None:
            points.append(f"{fmt(alone.share, 'share')} of working residents drive to work alone")
    parts = []
    if jobs and jobs.value is not None:
        lead = f"{place.name} has {jobs.text} jobs located in it"
        if ratio and ratio.value is not None:
            words = f" ({ratio.comparison.words})" if ratio.comparison else ""
            lead += f", {ratio.text} per resident{words}"
        parts.append(lead)
    first = fm.get("sector_1")
    if first and first.value is not None:
        name = first.label.split(": ", 1)[1]
        parts.append(f"the largest sector is {name.lower()} ({first.text} of jobs)")
    inflow = fc.get("inflow_share")
    if inflow and inflow.value is not None:
        where = "its" if place.level == "county" else "its county's"
        parts.append(f"{inflow.text} of {where} jobs are held by people who live elsewhere")
    return finish(KEY, TITLE, sentence(parts), facts, LIMITS, charts=charts, lead=jobs,
                  points=points)  # fmt: skip


def _sector_chart(ctx, place, other, rows, sectors: list[str]) -> Chart | None:
    """Jobs by NAICS sector: the largest :data:`CHART_SECTORS`, then "All other sectors"."""

    def counts(area) -> pd.Series:
        return rows(area)[sectors].apply(pd.to_numeric, errors="coerce").sum()

    mine = counts(place)
    if not len(mine) or mine.sum() <= 0:
        return None
    order = mine[mine > 0].sort_values(ascending=False, kind="stable")
    top = list(order.index[:CHART_SECTORS])
    rest = [c for c in sectors if c not in top]

    def items(series: pd.Series):
        out = []
        for column in top:
            code = column.rsplit("_", 1)[1]
            out.append((code, SECTOR_NAMES.get(code, code), float(series[column]), None))
        if rest:
            out.append(("other", "All other sectors", float(series[rest].sum()), None))
        return items_from_counts(out)

    made, total = items(mine)
    compare = None
    if other is not None:
        theirs = counts(other)
        if theirs.sum() > 0:
            compare = ChartCompare(other.name, items(theirs)[0])
    return Chart(
        id=f"{KEY}.sectors",
        kind="rank",
        title="Jobs by industry sector",
        unit="jobs",
        items=made,
        total=total,
        compare=compare,
        source_ids=(WAC,),
        vintage=ctx.vintage(WAC),
        note=f"LODES jobs by NAICS sector; largest {CHART_SECTORS} shown, the rest added up; "
        "share = sector jobs / all jobs.",
    )
