"""Profile section ``hazards``: flood zones, developed land, impervious cover and slope.

Plain-language summary
----------------------
Reads the pack's ``hazard_tract`` and ``hazard_county`` tables (built by
:mod:`visionhousing.features.hazards`). Facts:

- homes in FEMA's Special Flood Hazard Area (the 1% annual chance floodplain),
  as a share of 2020 Census homes; blank where FEMA has no digital flood map;
- developed land in that floodplain, as a share of developed land;
- land that is developed (Annual NLCD classes 21-24), as a share of land;
- mean impervious cover of land (Annual NLCD fractional impervious surface);
- land steeper than the slope threshold (15%; USGS 3DEP at 30 m);
- for a county or the state, the share of land FEMA has mapped digitally.

A tract reads its own row and is compared with its county; a county reads its
exact row (sums of cell counts) and is compared with the state, which adds up
the counties weighted by homes or land. These are screening measures for
"practical versus nominal capacity": they say where building may be harder,
not what any law allows.

Charts (stacks): homes in and outside the floodplain, land developed and not,
land steep and not.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.profile.charts import items_from_counts
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.model import Chart, ChartCompare, Section
from visionhousing.profile.sections._common import FactMaker, finish, point, sentence

__all__ = ["build"]

KEY = "hazards"
TITLE = "Hazards and land"
FLOOD = "fema_nfhl"
NLCD = "usgs_annual_nlcd"
DEM = "usgs_3dep_dem"
ACRES_PER_M2 = 1 / 4046.8564224

LIMITS = (
    "Screening measures only: they show where building may be harder, not what the rules "
    "allow, and they do not change the model's estimates. The flood share uses FEMA's regulatory "
    "1% annual chance floodplain; it leaves out heavy-rain flooding away from mapped streams, "
    "and some flood maps are decades old. Where FEMA has no digital map the flood shares are "
    "blank, not zero. A home counts as in the floodplain when its 2020 Census block's centre "
    "point falls on a 30 m grid cell inside the floodplain, so large rural blocks are coarse. "
    "Land cover is a 30 m satellite classification; developed land includes parks and lawns. "
    "Slope comes from a 30 m elevation grid, which smooths short steep banks, so the steep share "
    "is a lower bound for small sites."
)


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``hazards`` section for a county or tract."""
    place, other = ctx.place(geoid)
    tracts = ctx.need("hazard_tract").copy()
    tracts["geoid"] = tracts["geoid"].astype(str)
    counties = ctx.need("hazard_county").copy()
    counties["county"] = counties["county"].astype(str)
    base = ctx.need("tracts").copy()
    base["geoid"] = base["geoid"].astype(str)
    base = base.set_index("geoid")
    homes_column = "housing_units_2020" if "housing_units_2020" in base.columns else "housing_units"
    if homes_column not in base.columns or "land_area_m2" not in base.columns:
        raise MissingDataError("the tract table has no housing units or land area")
    year = str(int(pd.to_numeric(counties["nlcd_year"], errors="coerce").max()))

    def row(area: Area) -> pd.Series:
        r = tracts[tracts["geoid"] == area.geoid]
        if r.empty:
            raise MissingDataError(f"no hazard_tract row for {area.geoid}")
        return r.iloc[0]

    def rows(area: Area) -> pd.DataFrame:
        return counties[counties["county"].isin(area.counties)]

    # Flood shares are shares of what FEMA has mapped; packs built before the
    # mapped-home columns existed weight by all homes and all developed land.
    fallback = {"nfhl_homes": "homes", "nfhl_developed_acres": "developed_acres"}

    def share(column: str, weight: str):
        """A tract's own share; for a county or the state, counties weighted by ``weight``."""

        def measure(area: Area) -> Measure:
            if area.level == "tract":
                v = pd.to_numeric(pd.Series([row(area)[column]]), errors="coerce").iloc[0]
                return Measure(None if pd.isna(v) else float(v))
            r = rows(area)
            v = pd.to_numeric(r[column], errors="coerce")
            w_col = weight if weight in r.columns else fallback.get(weight, weight)
            w = pd.to_numeric(r[w_col], errors="coerce").fillna(0)
            ok = v.notna() & (w > 0)
            if not ok.any():
                return Measure(None)
            return Measure(float((v[ok] * w[ok]).sum() / w[ok].sum()))

        return measure

    flood_homes = share("sfha_share", "nfhl_homes")

    fm = FactMaker(ctx, KEY, place, other)
    fm.add(
        "homes_in_floodplain",
        "Homes in FEMA's 1% annual chance floodplain (SFHA)",
        "share",
        FLOOD,
        "2020 homes",
        flood_homes,
        note="2020 Census block homes by centre point on a 30 m grid; blank where FEMA has "
        "no digital flood map",
    )
    fm.add(
        "developed_in_floodplain",
        "Developed land in the floodplain",
        "share",
        FLOOD,
        year,
        share("sfha_dev_share", "nfhl_developed_acres"),
        note=f"share of developed land (NLCD {year} classes 21-24) inside the SFHA",
    )
    fm.add(
        "developed_land",
        "Land that is developed",
        "share",
        NLCD,
        year,
        share("developed_share", "land_acres"),
        note="NLCD classes 21-24 (open space, low, medium, high intensity) / land",
    )
    fm.add(
        "impervious_cover",
        "Mean impervious cover of land",
        "share",
        NLCD,
        year,
        share("impervious_share", "land_acres"),
        note="Annual NLCD fractional impervious surface, averaged over land cells",
    )
    fm.add(
        "steep_land",
        "Land steeper than 15% slope",
        "share",
        DEM,
        "as read",
        share("steep_share", "land_acres"),
        note="slope from the 3DEP elevation service on a 30 m grid",
    )
    if place.level != "tract":
        fm.add(
            "flood_map_coverage",
            "Land covered by FEMA's digital flood map",
            "share",
            FLOOD,
            "as read",
            share("nfhl_coverage", "land_acres"),
            compare=False,
            note="NFHL availability polygons; elsewhere flood shares are blank",
        )

    def homes(area: Area) -> float:
        if area.level == "tract":
            return float(pd.to_numeric(base.loc[area.geoid, homes_column], errors="coerce"))
        return float(pd.to_numeric(rows(area)["homes"], errors="coerce").sum())

    def land_acres(area: Area) -> float:
        if area.level == "tract":
            return float(base.loc[area.geoid, "land_area_m2"]) * ACRES_PER_M2
        return float(pd.to_numeric(rows(area)["land_acres"], errors="coerce").sum())

    charts = [
        _split_chart(
            ctx,
            place,
            other,
            "floodplain_homes",
            "Homes in the floodplain",
            "units",
            ("in", "In the SFHA"),
            ("out", "Outside the SFHA"),
            homes,
            flood_homes,
            (FLOOD,),
            "2020 homes by block; blank where FEMA has no digital map.",
        ),
        _split_chart(
            ctx,
            place,
            other,
            "developed_land",
            "Land by development",
            "acres",
            ("developed", "Developed"),
            ("other", "Not developed"),
            land_acres,
            share("developed_share", "land_acres"),
            (NLCD,),
            f"Annual NLCD {year}; a tract's acres are its Census land area x the share.",
        ),
        _split_chart(
            ctx,
            place,
            other,
            "steep_land",
            "Land by slope",
            "acres",
            ("steep", "Steeper than 15%"),
            ("gentle", "15% or flatter"),
            land_acres,
            share("steep_share", "land_acres"),
            (DEM,),
            "3DEP elevation on a 30 m grid; acres as for land cover.",
        ),
    ]
    flood = fm.get("homes_in_floodplain")
    points = [
        point(flood, "{text} of homes in the 1% annual chance floodplain{words}"),
        point(fm.get("developed_land"), "{text} of land is developed{words}"),
        point(fm.get("steep_land"), "{text} of land is steeper than 15%{words}"),
    ]
    parts = []
    if flood and flood.value is not None:
        parts.append(
            f"{flood.text} of {place.name}'s homes are in FEMA's 1% annual chance floodplain"
        )
    dev = fm.get("developed_land")
    if dev and dev.value is not None:
        parts.append(f"{dev.text} of its land is developed")
    steep = fm.get("steep_land")
    if steep and steep.value is not None:
        parts.append(f"{steep.text} is steeper than 15%")
    return finish(
        KEY,
        TITLE,
        sentence(parts),
        list(fm.facts),
        LIMITS,
        charts=charts,
        lead=flood,
        points=points,
    )


def _split_chart(
    ctx: ProfileContext,
    place: Area,
    other: Area | None,
    name: str,
    title: str,
    unit: str,
    first: tuple[str, str],
    second: tuple[str, str],
    total_of,
    share_of,
    sources: tuple[str, ...],
    note: str,
) -> Chart | None:
    """A two-part stack: total x share and total x (1 - share)."""

    def items(area: Area):
        total = total_of(area)
        s = share_of(area).value
        if s is None or not total or total <= 0:
            return None
        a = round(total * s)
        return items_from_counts(
            [(first[0], first[1], a, None), (second[0], second[1], round(total) - a, None)]
        )

    try:
        mine = items(place)
    except (MissingDataError, KeyError):
        return None
    if mine is None or not mine[1]:
        return None
    theirs = None
    if other is not None:
        try:
            theirs = items(other)
        except (MissingDataError, KeyError):
            theirs = None
    return Chart(
        id=f"{KEY}.{name}",
        kind="stack",
        title=title,
        unit=unit,
        items=mine[0],
        total=mine[1],
        compare=None if theirs is None else ChartCompare(other.name, theirs[0]),
        source_ids=sources,
        vintage=ctx.vintage(sources[0]),
        note=note,
    )
