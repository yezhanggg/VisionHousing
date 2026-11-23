"""Profile section ``pipeline_local``: city new-home permits and tax-credit awards.

Plain-language summary
----------------------
The ``permits`` section counts permitted homes by county only. This section
adds what local records show:

- **Philadelphia** (L&I building permits): new-home permits and the homes on
  them, per tract and county, for each recent year and the last five years;
- **Pittsburgh** (PLI permits): new residential building permits per tract and
  for the city part of Allegheny County. The file has no home counts, so only
  permits are shown;
- **everywhere in Pennsylvania**: developments awarded Low-Income Housing Tax
  Credits by PHFA, with their homes, in the county (and in the tract where the
  address could be placed).

A tract is compared with its county (the average tract), where both have data.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from visionhousing.profile.context import Area, Measure, ProfileContext
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence

KEY = "pipeline_local"
TITLE = "Local permit pipeline"
PERMIT_SOURCES = {"42101": "phl_li_permits", "42003": "pgh_pli_permits"}
LIHTC_SOURCE = "phfa_lihtc_awards"
WINDOW = 5


def _permits(ctx: ProfileContext) -> pd.DataFrame | None:
    t = ctx.table("pipeline_tract_year")
    if t is None or t.empty:
        return None
    t = t.copy()
    for c in ("year", "permits", "units"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t["tract"] = t["tract"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    t["county"] = t["county"].astype(str)
    return t


def _awards(ctx: ProfileContext) -> pd.DataFrame | None:
    t = ctx.table("lihtc_awards")
    if t is None or t.empty:
        return None
    t = t.copy()
    for c in ("year", "units", "lihtc_award"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t["tract"] = t["tract"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    t["county"] = t["county"].astype(str)
    return t


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``pipeline_local`` section."""
    place, other = ctx.place(geoid)
    county = place.geoid[:5]
    permits, awards = _permits(ctx), _awards(ctx)
    facts: list[Fact] = []
    parts: list[str] = []
    sources: list[str] = []
    source = PERMIT_SOURCES.get(county)
    if permits is not None and source:
        p = permits[permits["source"] == source]
        latest = max(int(p["year"].max()), date.today().year - 1) if len(p) else None
        if latest is not None:
            # The window ends with the last full calendar year before today.
            full = min(latest, date.today().year - 1)
            recent = range(full - WINDOW + 1, full + 1)

            def rows(area: Area) -> pd.DataFrame:
                if area.level == "tract":
                    return p[p["tract"] == area.geoid]
                return p[p["county"].isin(area.counties)]

            def total(col: str, years: range):
                def measure(area: Area) -> Measure:
                    r = rows(area)
                    r = r[r["year"].isin(list(years))]
                    if area.level == "county" and place.level == "tract":
                        # Compare a tract with the county's average tract.
                        n = p.loc[
                            p["county"].isin(area.counties) & (p["tract"] != ""), "tract"
                        ].nunique()
                        return Measure(
                            float(r[col].sum() / n) if n else None,
                            note="county figure is the average per tract with permits",
                        )
                    return Measure(float(r[col].sum()))

                return measure

            fm = FactMaker(ctx, KEY, place, other if place.level == "tract" else None)
            span = f"{recent.start}-{recent.stop - 1}"
            has_units = source == "phl_li_permits"
            where = "Philadelphia" if county == "42101" else "the City of Pittsburgh"
            fm.add(
                "permits_5yr",
                f"New-home building permits, {span}",
                "permits",
                source,
                span,
                total("permits", recent),
                note=f"{where} only",
            )
            if has_units:
                fm.add(
                    "units_5yr",
                    f"Homes on new-home permits, {span}",
                    "units",
                    source,
                    span,
                    total("units", recent),
                )
            for y in range(latest, latest - WINDOW - 1, -1):
                col = "units" if has_units else "permits"
                label = "Homes on new-home permits" if has_units else "New-home permits"
                part = " (part year)" if y >= date.today().year else ""
                fm.add(
                    f"{col}_{y}",
                    f"{label}, {y}{part}",
                    "units" if has_units else "permits",
                    source,
                    str(y),
                    total(col, range(y, y + 1)),
                    compare=False,
                )
            facts += fm.facts
            sources.append(source)
            lead = fm.get("units_5yr" if has_units else "permits_5yr")
            if lead and lead.value is not None:
                what = "homes on new-home permits" if has_units else "new-home permits"
                parts.append(f"{where} records {lead.text} {what} here in {span}")
    if awards is not None:
        a = awards[awards["county"] == county]
        if place.level == "tract":
            a = a[a["tract"] == place.geoid]
        first, last = int(awards["year"].min()), int(awards["year"].max())
        span = f"{first}-{last}"
        n_dev = len(a)
        homes = float(a["units"].sum()) if n_dev else 0.0
        vintage = ctx.vintage(LIHTC_SOURCE)
        years_read = ", ".join(str(int(y)) for y in sorted(awards["year"].unique()))
        facts.append(
            Fact(
                f"{KEY}.lihtc_developments",
                f"Developments awarded LIHTC, {span}",
                n_dev,
                "developments",
                f"{n_dev:,}",
                LIHTC_SOURCE,
                vintage,
                span,
                place.level,
                note=f"award lists read: {years_read}",
            )
        )
        facts.append(
            Fact(
                f"{KEY}.lihtc_homes",
                f"Homes in LIHTC awards, {span}",
                int(homes),
                "units",
                f"{int(homes):,}",
                LIHTC_SOURCE,
                vintage,
                span,
                place.level,
                note="homes as listed by PHFA; awards, not homes built",
            )
        )
        for r in (
            a.sort_values(["year", "development"], ascending=[False, True]).head(10).itertuples()
        ):
            units = "" if pd.isna(r.units) else f", {int(r.units)} homes"
            facts.append(
                Fact(
                    f"{KEY}.lihtc_{int(r.year)}_{r.Index}",
                    f"LIHTC award {int(r.year)}",
                    str(r.development),
                    "text",
                    f"{r.development} ({r.city}{units})",
                    LIHTC_SOURCE,
                    vintage,
                    str(int(r.year)),
                    place.level,
                )
            )
        sources.append(LIHTC_SOURCE)
        if n_dev:
            what = "development" if n_dev == 1 else "developments"
            parts.append(
                f"PHFA awarded tax credits to {n_dev} {what} with {int(homes):,} homes "
                f"here in the lists read ({years_read})"
            )
        else:
            parts.append(f"PHFA's award lists read ({years_read}) show no tax-credit awards here")
    if not facts:
        return Section.no_data(
            KEY, TITLE, "No local permit records or tax-credit awards for this place."
        )
    limits = (
        "Permits issued are not homes started or finished. City permit files cover "
        "Philadelphia and the City of Pittsburgh only; Pittsburgh's has no home counts. "
        "Philadelphia's 2021 count is swollen by a rush ahead of a tax-abatement change. "
        "Tax-credit awards are read from PHFA's yearly PDF lists, whose layout changes; a "
        "scanned list cannot be read and is skipped, and awards at intersections or scattered "
        "sites have no tract. An award is not a finished building."
    )
    section = finish(KEY, TITLE, sentence(parts), facts, limits)
    return section
