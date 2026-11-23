"""Profile section ``incentives``: tax-credit and incentive areas in a place.

Plain-language summary
----------------------
Which public programs make building or rehabilitating homes cheaper here (see
:mod:`visionhousing.features.incentives` for how each is placed on tracts):

- **Qualified Census Tracts** (HUD) and **Difficult Development Areas** (HUD):
  a Low-Income Housing Tax Credit (LIHTC) project in either can claim a 30%
  larger credit basis.
- **Opportunity Zones from 2027** (Treasury): capital-gains tax relief for
  investments held in the zone.
- **New Markets Tax Credit** low-income community test (CDFI Fund): the tract
  meets the poverty (20% or more) or income (80% of the benchmark or less) test.
- **LIHTC projects** already placed in service (HUD), and their units per
  1,000 homes.
- **National Register of Historic Places** (NPS): listings, and the share of
  homes inside listed historic districts (where rehabilitating an
  income-producing building can earn the federal historic tax credit).
- **Keystone Opportunity Zone** sites (Pennsylvania DCED): state and local tax
  abatements on listed sites.

For a tract the facts are yes/no answers and counts. For a county they are
counts of tracts, the share of its tracts, and program totals. A county is
compared with the state, a tract with its county.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.model import Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence

KEY = "incentives"
TITLE = "Tax credits and incentive areas"

LIMITS = (
    "Being in a designated area makes a project eligible; it does not give it credits or "
    "decide whether it pays. QCT and DDA lists change every year and Difficult Development "
    "Areas are ZIP-code areas placed on tracts by shared land. Opportunity Zones shown are "
    "the round that starts in 2027; the first round ends on 2026-12-31. The New Markets test "
    "uses 2016-2020 survey data and leaves out other routes to eligibility. Tax-credit "
    "projects are those HUD's map lists (Pennsylvania records stop at 2020). Historic "
    "listings are those the National Park Service has mapped, so counts are a floor. "
    "Keystone Opportunity Zone sites are those on the state's public map."
)


def _yes_no(value: object) -> str | None:
    v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(v):
        return None
    return "yes" if v >= 1 else "no"


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``incentives`` section for a county or tract."""
    place, other = ctx.place(geoid)
    table = ctx.need("incentives_tract").copy()
    table["tract"] = table["tract"].astype(str)

    def rows(area: Area) -> pd.DataFrame:
        return table[table["tract"].isin(set(area.tracts["geoid"].astype(str)))]

    def need(column: str) -> None:
        if column not in table.columns or table[column].isna().all():
            raise MissingDataError(f"incentives_tract has no {column}")

    def total(column: str):
        def measure(area: Area) -> Measure:
            need(column)
            r = rows(area)
            return Measure(
                float(pd.to_numeric(r[column], errors="coerce").sum()) if len(r) else None
            )

        return measure

    def tract_share(column: str):
        def measure(area: Area) -> Measure:
            need(column)
            values = pd.to_numeric(rows(area)[column], errors="coerce").dropna()
            return Measure(float((values >= 1).mean()) if len(values) else None)

        return measure

    def flag(column: str):
        def measure(area: Area) -> Measure:
            need(column)
            r = rows(area)
            return Measure(_yes_no(r[column].iloc[0]) if len(r) else None)  # type: ignore[arg-type]

        return measure

    def value(column: str):
        def measure(area: Area) -> Measure:
            need(column)
            r = rows(area)
            v = pd.to_numeric(r[column], errors="coerce").iloc[0] if len(r) else None
            return Measure(None if v is None or pd.isna(v) else float(v))

        return measure

    def per_thousand_homes(column: str):
        def measure(area: Area) -> Measure:
            units = total(column)(area).value
            homes = area.count("housing_units").value
            if units is None or not homes:
                return Measure(None)
            return Measure(units / homes * 1000)

        return measure

    def district_home_share(area: Area) -> Measure:
        inside = total("nrhp_district_units")(area).value
        homes = area.count("housing_units_2020").value
        if inside is None or not homes:
            return Measure(None)
        return Measure(inside / homes)

    v_qct = ctx.vintage("hud_qct")
    v_dda = ctx.vintage("hud_dda")
    v_oz = ctx.vintage("treasury_oz2027")
    v_nr = ctx.vintage("nps_nrhp")
    v_koz = ctx.vintage("dced_koz_sites")
    fm = FactMaker(ctx, KEY, place, other)
    if place.level == "tract":
        fm.add("qct", "Qualified Census Tract (HUD)", "flag", "hud_qct", v_qct, flag("qct"),
               compare=False)  # fmt: skip
        fm.add("dda_land_share", "Share of land in a Difficult Development Area (HUD)", "share",
               "hud_dda", v_dda, value("dda_land_share"), compare=False)  # fmt: skip
        fm.add("oz2027", "Opportunity Zone from 2027 (Treasury)", "flag", "treasury_oz2027", v_oz,
               flag("oz2027"), compare=False)  # fmt: skip
        fm.add("nmtc_lic", "Meets the New Markets Tax Credit low-income test (CDFI Fund)", "flag",
               "cdfi_nmtc_lic", "2016-2020", flag("nmtc_lic"), compare=False)  # fmt: skip
        fm.add("nmtc_poverty_rate", "Poverty rate used for the New Markets test", "percent",
               "cdfi_nmtc_lic", "2016-2020", value("nmtc_poverty_rate"), compare=False)  # fmt: skip
    else:
        fm.add("qct_tracts", "Qualified Census Tracts (HUD)", "tracts", "hud_qct", v_qct,
               total("qct"), compare=False)  # fmt: skip
        fm.add("qct_share", "Share of tracts that are Qualified Census Tracts", "share",
               "hud_qct", v_qct, tract_share("qct"))  # fmt: skip
        fm.add("dda_tracts", "Tracts at least half in a Difficult Development Area (HUD)",
               "tracts", "hud_dda", v_dda, total("dda"), compare=False)  # fmt: skip
        fm.add("oz2027_tracts", "Opportunity Zone tracts from 2027 (Treasury)", "tracts",
               "treasury_oz2027", v_oz, total("oz2027"), compare=False)  # fmt: skip
        fm.add("nmtc_share", "Share of tracts meeting the New Markets Tax Credit low-income test",
               "share", "cdfi_nmtc_lic", "2016-2020", tract_share("nmtc_lic"))  # fmt: skip
    fm.add("lihtc_projects", "Tax-credit (LIHTC) projects placed in service", "projects",
           "hud_lihtc", "1987-2020", total("lihtc_projects"), compare=False)  # fmt: skip
    fm.add("lihtc_units", "Units in tax-credit projects", "units", "hud_lihtc", "1987-2020",
           total("lihtc_units"), compare=False)  # fmt: skip
    fm.add("lihtc_per_1000", "Tax-credit units per 1,000 homes", "per 1,000 homes", "hud_lihtc",
           "1987-2020", per_thousand_homes("lihtc_units"),
           note=f"LIHTC units / ACS {ctx.acs_years()} housing units x 1,000")  # fmt: skip
    fm.add("nrhp_listings", "National Register of Historic Places listings", "listings",
           "nps_nrhp", v_nr, total("nrhp_listings"), compare=False)  # fmt: skip
    fm.add("historic_district_homes", "Share of homes inside listed historic districts", "share",
           "nps_nrhp", "2020", district_home_share,
           note="2020 Census block homes whose block centre is inside a district")  # fmt: skip
    if ctx.state == "PA":
        fm.add("koz_sites", "Keystone Opportunity Zone sites (state map)", "sites",
               "dced_koz_sites", v_koz, total("koz_sites"), compare=False)  # fmt: skip

    parts = []
    if place.level == "tract":
        names = {"qct": "a Qualified Census Tract", "oz2027": "a 2027 Opportunity Zone",
                 "nmtc_lic": "eligible for New Markets Tax Credits"}  # fmt: skip
        yes = [text for key, text in names.items() if (f := fm.get(key)) and f.value == "yes"]
        if yes:
            parts.append(f"{place.name} is " + ", ".join(yes))
        else:
            parts.append(f"{place.name} is not in a Qualified Census Tract, 2027 Opportunity Zone "
                         "or New Markets low-income tract")  # fmt: skip
    else:
        q, oz = fm.get("qct_tracts"), fm.get("oz2027_tracts")
        if q and q.value is not None:
            share = fm.get("qct_share")
            lead = f"{place.name} has {q.text} Qualified Census Tracts"
            if share and share.value is not None and share.comparison:
                lead += f" ({share.text} of its tracts, {share.comparison.words})"
            parts.append(lead)
        if oz and oz.value is not None:
            parts.append(f"{oz.text} Opportunity Zone tracts from 2027")
    units = fm.get("lihtc_units")
    if units and units.value is not None:
        parts.append(f"its tax-credit (LIHTC) projects hold {units.text} homes")
    return finish(KEY, TITLE, sentence(parts), fm.facts, LIMITS)
