"""Profile section ``parcels``: parcels and property, from county assessment records.

Plain-language summary
----------------------
Only Philadelphia (Office of Property Assessment) and Allegheny County (Office
of Property Assessments, through WPRDC) publish parcel attributes free and
openly. For them this section shows, per county or tract (``parcels_area``
table):

- the number of parcels and the mix of land uses (shares of parcels);
- single-family homes: how many, their median assessed value, median lot
  size, and when they were built;
- single-family sales in the last 12 and 36 months (windows set in
  ``assumptions/parcels.yaml``) and their median price.

A tract is compared with its county. Every other county gets "no free parcel
attributes for this county".

The two counties' values are not comparable with each other: Philadelphia's
are current market values, Allegheny's are 2012 base-year values.
"""

from __future__ import annotations

from visionhousing.features.parcels import load_rules
from visionhousing.profile.context import Area, Measure, ProfileContext
from visionhousing.profile.model import Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence
from visionhousing.profile.sections._local import area_row, count_of, share_of

KEY = "parcels"
TITLE = "Parcels and property"
SOURCES = {"42101": "phl_opa_properties", "42003": "alle_assessments"}
SALES_SOURCES = {"42101": "phl_opa_properties", "42003": "alle_property_sales"}
NO_PARCELS = "No free parcel attributes for this county"


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``parcels`` section (Philadelphia and Allegheny only)."""
    place, other = ctx.place(geoid)
    county = place.geoid[:5]
    table = ctx.table("parcels_area")
    if county not in SOURCES or table is None or table.empty:
        return Section.no_data(
            KEY, TITLE, f"{NO_PARCELS}: only Philadelphia and Allegheny County publish them openly."
        )
    table = table.copy()
    table["geoid"] = table["geoid"].astype(str)
    if area_row(table, place) is None:
        return Section.no_data(KEY, TITLE, f"No parcels were placed in this {place.level}.")
    rules = load_rules()
    source, sales_source = SOURCES[county], SALES_SOURCES[county]
    compare_to = other if place.level == "tract" else None
    fm = FactMaker(ctx, KEY, place, compare_to)
    row0 = area_row(table, place)
    as_of = str(row0["as_of"])
    basis = str(row0["value_basis"])
    year = f"records read {as_of}"

    def col(name: str):
        return lambda a: Measure(count_of(area_row(table, a), name))

    def share(num: str, den: str, note: str = ""):
        return lambda a: share_of(
            count_of(area_row(table, a), num), count_of(area_row(table, a), den), note
        )

    def built_share(group: str):
        def measure(a: Area) -> Measure:
            row = area_row(table, a)
            known = sum(count_of(row, f"sf_built_{g}") or 0 for g in rules["year_built_groups"])
            if known < int(rules["min_parcels_for_median"]):
                return Measure(None)
            return share_of(count_of(row, f"sf_built_{group}"), known)

        return measure

    fm.add("parcels", "Parcels", "parcels", source, year, col("parcels"), compare=False)
    for cls, label in rules["land_use_classes"].items():
        if (count_of(row0, f"lu_{cls}") or 0) > 0:
            fm.add(
                f"share_{cls}",
                f"Share of parcels: {label.lower()}",
                "share",
                source,
                year,
                share(f"lu_{cls}", "parcels"),
            )
    fm.add(
        "sf_parcels",
        "Single-family parcels",
        "parcels",
        source,
        year,
        col("sf_parcels"),
        compare=False,
    )
    fm.add(
        "sf_median_value",
        "Median assessed value, single-family",
        "dollars",
        source,
        year,
        col("sf_median_value"),
        note=basis,
    )
    fm.add(
        "sf_median_lot",
        "Median lot size, single-family (square feet)",
        "square feet",
        source,
        year,
        col("sf_median_lot_sqft"),
    )
    for g, (lo, hi) in rules["year_built_groups"].items():
        span = f"before {hi + 1}" if lo == 0 else (f"{lo} or later" if hi >= 9999 else f"{lo}-{hi}")
        fm.add(
            f"sf_built_{g}",
            f"Share of single-family homes built {span}",
            "share",
            source,
            year,
            built_share(g),
            note="of homes with a year built on file",
        )
    short, long_ = sorted(rules["sales_windows_months"])
    sale_note = (
        f"latest recorded sale at or above ${rules['min_sale_price']:,}; "
        "a home sold twice counts once"
        if county == "42101"
        else "sales the assessment office marks valid"
    )
    for w, months in (("short", short), ("long", long_)):
        fm.add(
            f"sales_{months}m",
            f"Single-family sales, last {months} months",
            "sales",
            sales_source,
            year,
            col(f"sales_{w}"),
            compare=False,
            note=sale_note,
        )
        fm.add(
            f"median_sale_{months}m",
            f"Median single-family sale price, last {months} months",
            "dollars",
            sales_source,
            year,
            col(f"median_sale_{w}"),
            note=sale_note,
        )

    n, sf, v = (
        fm.get("parcels"),
        fm.get("share_single_family"),
        fm.get("median_sale_" + f"{long_}m"),
    )
    parts = []
    if n and n.value is not None:
        parts.append(f"{place.name} has {n.text} parcels")
    if sf and sf.value is not None:
        parts.append(f"{sf.text} are single-family homes")
    if v and v.value is not None:
        parts.append(f"single-family homes sold for a median {v.text} over the last {long_} months")
    limits = (
        "Assessment records describe parcels, not households. "
        + (
            "Philadelphia values are the City's current market values; condominium units are "
            "filed as single family and are counted separately here. Only each parcel's latest "
            "sale is on file, so homes sold twice in a window count once. The City's data "
            "licence is to be confirmed; only totals are shown. "
            if county == "42101"
            else "Allegheny values are 2012 base-year assessments, far below today's prices, so "
            "they rank places but do not price them. Sales are those the assessment office marks "
            "valid. "
        )
        + f"Medians resting on fewer than {rules['min_parcels_for_median']} homes or sales are "
        "not shown. Parcels are placed in 2020 tracts by their centre point; the county row "
        "also counts parcels that could not be placed."
    )
    return finish(KEY, TITLE, sentence(parts), fm.facts, limits)
