"""Profile section ``infrastructure``: water and sewer readiness, from official records only.

Plain-language summary
----------------------
Reads the pack's ``infra_tract``, ``infra_county`` and ``infra_contacts`` tables
(built by :mod:`visionhousing.features.infrastructure`). Every share is of 2020
Census homes (block counts):

- homes inside a mapped public sewer service area (an official county or state
  map, or a utility's whole-jurisdiction statement) - "verified available";
- homes outside the service areas of a complete county map (on-lot systems
  expected);
- homes under a documented sewer moratorium or connection ban (official records
  only; none were found, so this is shown as zero found, not as none existing);
- homes no official record speaks to - "unknown";
- homes inside a mapped public water supplier service area (Pennsylvania DEP);
- homes inside an EPA sewershed - context only, mostly modelled;
- Pennsylvania only: homes by the age of their municipality's Act 537 sewage
  facilities plan, and EPA's Clean Watersheds Needs Survey 2022 documented
  needs for the county;
- the largest sewer and water providers by homes, with an official listing
  page where one exists.

A county or the state adds up homes (exact counts from ``infra_county``); a
tract reads its own row and is compared with its county. Nothing here says how
much treatment capacity is left, and distance to a pipe is never used.

Charts: homes by sewer evidence (stack), homes by public water service (stack),
homes by Act 537 plan age (bar, counties and the state) and CWNS needs by
category (rank, by county).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from visionhousing.features.infrastructure import ACT537_ORDER
from visionhousing.profile.charts import items_from_counts
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.format import fmt
from visionhousing.profile.model import Chart, ChartCompare, Fact, Section
from visionhousing.profile.sections._common import FactMaker, finish, point, sentence

__all__ = ["build"]

KEY = "infrastructure"
TITLE = "Water and sewer readiness"
BLOCKS = "tiger_2020_tabblock"
WATER = "pa_dep_pws_service_areas"
EPA = "epa_sewersheds"
ACT537 = "pa_dep_act537_plan_status"
CWNS = "epa_cwns_2022"
STATUS = ("verified", "outside", "constraint", "unknown")
STATUS_LABELS = {
    "verified": "Inside a mapped public sewer service area",
    "outside": "Outside the mapped service areas",
    "constraint": "Documented moratorium or connection ban",
    "unknown": "No official record found",
}
ACT537_LABELS = {
    "lt10": "Plan under 10 years old",
    "10to20": "Plan 10 to 20 years old",
    "20plus": "Plan 20 or more years old",
    "prereg": "Pre-1971 county plan only",
}
CWNS_ITEMS = (
    ("cwns_treatment", "Treatment plants (I, II)"),
    ("cwns_sewer_repair", "Sewer repair, leaks (III)"),
    ("cwns_new_sewers", "New sewers (IV)"),
    ("cwns_cso", "Combined sewer overflows (V)"),
    ("cwns_stormwater", "Stormwater (VI)"),
    ("cwns_decentralized", "Septic and small systems (XII)"),
    ("cwns_nonpoint", "Nonpoint source (VII)"),
)

LIMITS = (
    "Only official records count: a published service-area map, a utility's own statement or a "
    "regulator's order. Distance to a sewer line or a treatment plant is never used, and nothing "
    "here measures spare treatment or pipe capacity: a home inside a service area may still need "
    "an extension, a connection fee or a capacity allocation. Most counties have no public map "
    "whose terms allow sharing results, so most homes are 'unknown'; that means no record was "
    "found, not that sewer is absent. No official moratorium or connection-ban list was found, so "
    "zero homes under a constraint means none found. EPA sewersheds are mostly modelled and are "
    "context only. Homes are 2020 Census block counts of all building types, placed by each "
    "block's centre point. Act 537 plan age is the date DEP last approved a municipality's plan; "
    "an old plan is not a ban. CWNS needs are capital needs states documented in 2022, in "
    "January 2022 dollars."
)


def _num(x: Any) -> float:
    v = pd.to_numeric(pd.Series([x]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(v) else float(v)


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``infrastructure`` section for a county or tract."""
    place, other = ctx.place(geoid)
    tracts = ctx.need("infra_tract").copy()
    tracts["geoid"] = tracts["geoid"].astype(str)
    counties = ctx.need("infra_county").copy()
    counties["county"] = counties["county"].astype(str)
    contacts = ctx.table("infra_contacts")
    homes_of = _tract_homes(ctx)

    def county_rows(area: Area) -> pd.DataFrame:
        return counties[counties["county"].isin(area.counties)]

    def tract_row(area: Area) -> pd.Series:
        row = tracts[tracts["geoid"] == area.geoid]
        if row.empty:
            raise MissingDataError(f"no infra_tract row for {area.geoid}")
        return row.iloc[0]

    def status_counts(area: Area) -> dict[str, float]:
        """Homes by sewer status (a tract: its homes split by its shares)."""
        if area.level != "tract":
            r = county_rows(area)
            return {k: float(pd.to_numeric(r[f"homes_{k}"], errors="coerce").sum()) for k in STATUS}
        row = tract_row(area)
        homes = float(homes_of.get(area.geoid, 0))
        known = _num(row["sewer_known"])
        verified = _num(row["sewer_share"])
        constraint = _num(row["constraint_share"])
        return {
            "verified": homes * max(verified - constraint, 0.0),
            "outside": homes * max(known - verified, 0.0),
            "constraint": homes * constraint,
            "unknown": homes * max(1.0 - known, 0.0),
        }

    def status_share(key: str):
        def measure(area: Area) -> Measure:
            counts = status_counts(area)
            total = sum(counts.values())
            return Measure(counts[key] / total if total > 0 else None)

        return measure

    def weighted(column: str, county_column: str | None = None):
        def measure(area: Area) -> Measure:
            if area.level == "tract":
                value = pd.to_numeric(pd.Series([tract_row(area)[column]]), errors="coerce")[0]
                return Measure(None if pd.isna(value) else float(value))
            if county_column is not None:
                r = county_rows(area)
                vals = pd.to_numeric(r[county_column], errors="coerce")
                w = pd.to_numeric(r["homes"], errors="coerce").fillna(0)
                ok = vals.notna() & (w > 0)
                if not ok.any():
                    return Measure(None)
                return Measure(float((vals[ok] * w[ok]).sum() / w[ok].sum()))
            return Measure(None)

        return measure

    sources = _sources(ctx, place, tracts, counties)
    sewer_src = sources[0] if sources else BLOCKS
    fm = FactMaker(ctx, KEY, place, other)
    mine = status_counts(place)
    # With no official record for the place, only the "unknown" share is shown: a 0% verified
    # share would read as "no sewer", which no record says.
    spoken = mine["verified"] + mine["outside"] + mine["constraint"] > 0
    fm.add(
        "sewer_verified",
        "Homes inside a mapped public sewer service area",
        "share",
        sewer_src,
        "2020 homes",
        status_share("verified"),
        note="official maps or a utility's statement only; never inferred from distance",
    )
    fm.add(
        "sewer_outside",
        "Homes outside the mapped public sewer service areas",
        "share",
        sewer_src,
        "2020 homes",
        status_share("outside"),
        note="county maps read as complete inventories; on-lot systems expected",
    )
    fm.add(
        "sewer_constraint",
        "Homes under a documented sewer moratorium or connection ban",
        "share",
        sewer_src,
        "2020 homes",
        status_share("constraint"),
        note="none found in official records read; zero means none found",
    )
    fm.add(
        "sewer_unknown",
        "Homes no official sewer record speaks to",
        "share",
        BLOCKS,
        "2020 homes",
        status_share("unknown"),
        note="no published service-area map with shareable terms, and no utility statement",
    )
    if not spoken:
        fm.facts = [f for f in fm.facts if f.id == f"{KEY}.sewer_unknown"]
    if place.level == "tract":
        row = tract_row(place)
        fm.facts.append(
            _word_fact(
                ctx,
                "sewer_status",
                "Sewer readiness status (verified needs over half of homes)",
                str(row["sewer_status"] or "no homes"),
                sewer_src,
                "tract",
            )
        )
    water = fm.add(
        "water_inside",
        "Homes inside a mapped public water service area",
        "share",
        WATER,
        "2020 homes",
        weighted("water_share", "water_share"),
        note="Pennsylvania DEP public water supplier service areas",
    )
    if water is not None and water.value is None:
        fm.facts.remove(water)
    fm.add(
        "epa_sewershed",
        "Homes inside an EPA sewershed (modelled; context only)",
        "share",
        EPA,
        "2020 homes",
        weighted("epa_share", "epa_share"),
        note="EPA version 1.3; mostly machine-learning boundaries; not a service record",
    )
    _act537_facts(ctx, fm, place, tracts, counties)
    facts: list[Fact] = list(fm.facts)
    county = place if place.level == "county" else ctx.county_area(place.counties[0])
    fc = FactMaker(ctx, KEY, county, ctx.state_area())

    def cwns(column: str):
        def measure(area: Area) -> Measure:
            vals = pd.to_numeric(county_rows(area)[column], errors="coerce")
            return Measure(float(vals.sum()) if vals.notna().any() else None)

        return measure

    if "cwns_total" in counties.columns and counties["cwns_total"].notna().any():
        fc.add(
            "cwns_total",
            "Documented clean-water capital needs (CWNS 2022)",
            "dollars",
            CWNS,
            "2022",
            cwns("cwns_total"),
            compare=False,
            measured_at="county",
            note="all categories, January 2022 dollars",
        )
        fc.add(
            "cwns_new_sewers",
            "Documented needs for new sewers (CWNS 2022)",
            "dollars",
            CWNS,
            "2022",
            cwns("cwns_new_sewers"),
            compare=False,
            measured_at="county",
            note="new collector and interceptor sewers (categories IV-A and IV-B)",
        )
        facts += fc.facts
    facts += _contact_facts(ctx, contacts, county.geoid)

    charts = [
        _status_chart(ctx, place, other, status_counts, sewer_src),
        _water_chart(ctx, place, other, tracts, counties, homes_of),
        _act537_chart(ctx, place, other, counties),
        _cwns_chart(ctx, county, counties),
    ]
    verified, unknown = fm.get("sewer_verified"), fm.get("sewer_unknown")
    lead = verified if verified and verified.value else unknown
    points = [
        point(verified, "{text} of homes inside a mapped public sewer service area{words}"),
        point(unknown, "{text} of homes have no official sewer record"),
        point(fm.get("water_inside"), "{text} of homes inside a public water service area{words}"),
    ]
    parts = []
    if verified and verified.value is not None:
        parts.append(
            f"official records place {verified.text} of {place.name}'s homes inside a "
            "mapped public sewer service area"
        )
    if unknown and unknown.value:
        whose = "" if parts else f" of {place.name}'s homes"
        parts.append(f"{unknown.text}{whose} have no official sewer record")
    if water and water.value is not None:
        parts.append(f"{water.text} are inside a mapped public water service area")
    return finish(
        KEY, TITLE, sentence(parts), facts, LIMITS, charts=charts, lead=lead, points=points
    )


def _tract_homes(ctx: ProfileContext) -> pd.Series:
    """2020 Census homes per tract (block sums), else the survey's housing units."""
    base = ctx.need("tracts")
    column = "housing_units_2020" if "housing_units_2020" in base.columns else "housing_units"
    if column not in base.columns:
        raise MissingDataError("the tract table has no housing units")
    values = pd.to_numeric(base[column], errors="coerce").fillna(0)
    return pd.Series(values.to_numpy(), index=base["geoid"].astype(str))


def _sources(
    ctx: ProfileContext, place: Area, tracts: pd.DataFrame, counties: pd.DataFrame
) -> list[str]:
    """The official sewer records used for the place, in the registry."""
    if place.level == "tract":
        row = tracts[tracts["geoid"] == place.geoid]
        text = "" if row.empty else str(row["sewer_source"].iloc[0] or "")
    else:
        rows = counties[counties["county"].isin(place.counties)]
        text = "|".join(str(s) for s in rows["sewer_sources"].fillna("") if s)
    seen: list[str] = []
    for s in text.split("|"):
        if s and s in ctx.registry and s not in seen:
            seen.append(s)
    return seen


def _word_fact(
    ctx: ProfileContext, name: str, label: str, value: str, source: str, level: str, note: str = ""
) -> Fact:
    return Fact(
        id=f"{KEY}.{name}",
        label=label,
        value=value,
        unit="category",
        text=value,
        source=source,
        vintage=ctx.vintage(source),
        year="2020 homes",
        measured_at=level,
        comparison=None,
        moe=None,
        low=None,
        high=None,
        range_text="",
        note=note,
    )


def _act537_facts(
    ctx: ProfileContext, fm: FactMaker, place: Area, tracts: pd.DataFrame, counties: pd.DataFrame
) -> None:
    if ACT537 not in ctx.registry or "act537_20plus" not in counties.columns:
        return
    if counties["act537_20plus"].isna().all():
        return
    if place.level == "tract":
        row = tracts[tracts["geoid"] == place.geoid]
        code = "" if row.empty else str(row["act537_class"].iloc[0] or "")
        if code and code != "nan":
            fm.facts.append(
                _word_fact(
                    ctx,
                    "act537_class",
                    "Act 537 sewage plan age (municipality with most homes)",
                    ACT537_LABELS.get(code, code),
                    ACT537,
                    "tract",
                    note="DEP plan-age table",
                )
            )
        return

    def old_share(area: Area) -> Measure:
        r = counties[counties["county"].isin(area.counties)]
        w = pd.to_numeric(r["homes"], errors="coerce").fillna(0)
        v = pd.to_numeric(r["act537_20plus"], errors="coerce").fillna(0) + pd.to_numeric(
            r["act537_prereg"], errors="coerce"
        ).fillna(0)
        return Measure(float((v * w).sum() / w.sum()) if w.sum() > 0 else None)

    fm.add(
        "act537_old",
        "Homes where the Act 537 sewage plan is 20+ years old or pre-1971",
        "share",
        ACT537,
        "DEP table 2025-05-29",
        old_share,
        note="date DEP last approved each municipality's official plan; an old plan is not a ban",
    )


def _contact_facts(ctx: ProfileContext, contacts: pd.DataFrame | None, county: str) -> list[Fact]:
    if contacts is None or contacts.empty:
        return []
    rows = contacts[(contacts["county"].astype(str) == county) & (contacts["rank"] == 1)]
    labels = {
        "sewer": "Largest mapped sewer provider",
        "water": "Largest public water system",
        "epa_sewershed": "Largest EPA sewershed treatment plant",
    }
    out = []
    for _, r in rows.iterrows():
        kind = str(r["kind"])
        source = str(r["source"])
        if kind not in labels or source not in ctx.registry:
            continue
        url = str(r.get("contact_url") or "")
        note = f"official listing: {url}" if url and url != "nan" else "no official contact listing"
        out.append(
            Fact(
                id=f"{KEY}.contact_{kind}",
                label=labels[kind],
                value=str(r["name"]),
                unit="name",
                text=str(r["name"]),
                source=source,
                vintage=ctx.vintage(source),
                year="as listed",
                measured_at="county",
                comparison=None,
                moe=None,
                low=None,
                high=None,
                range_text="",
                note=f"{note}; serves {fmt(r['homes'], 'units')} of the county's 2020 homes",
            )
        )
    return out


def _status_chart(
    ctx: ProfileContext, place: Area, other: Area | None, counts_of, source: str
) -> Chart | None:
    def items(area: Area):
        counts = counts_of(area)
        return items_from_counts([(k, STATUS_LABELS[k], round(counts[k]), None) for k in STATUS])

    try:
        made, total = items(place)
    except MissingDataError:
        return None
    if not total:
        return None
    compare = None
    if other is not None:
        try:
            compare = ChartCompare(other.name, items(other)[0])
        except MissingDataError:
            compare = None
    return Chart(
        id=f"{KEY}.sewer_evidence",
        kind="stack",
        title="Homes by sewer evidence",
        unit="units",
        items=made,
        total=total,
        compare=compare,
        source_ids=(source, BLOCKS),
        vintage=ctx.vintage(source),
        note="2020 Census homes by block; official records only; share = homes / all homes. "
        "A tract's split applies its shares to its 2020 homes.",
    )


def _water_chart(
    ctx: ProfileContext,
    place: Area,
    other: Area | None,
    tracts: pd.DataFrame,
    counties: pd.DataFrame,
    homes_of: pd.Series,
) -> Chart | None:
    if WATER not in ctx.registry:
        return None

    def items(area: Area):
        if area.level == "tract":
            row = tracts[tracts["geoid"] == area.geoid]
            share = pd.to_numeric(row["water_share"], errors="coerce")
            if row.empty or share.isna().all():
                return None
            homes = float(homes_of.get(area.geoid, 0))
            inside = homes * float(share.iloc[0])
        else:
            r = counties[counties["county"].isin(area.counties)]
            share = pd.to_numeric(r["water_share"], errors="coerce")
            if share.isna().all():
                return None
            homes = float(pd.to_numeric(r["homes"], errors="coerce").sum())
            inside = float((share.fillna(0) * pd.to_numeric(r["homes"], errors="coerce")).sum())
        return items_from_counts(
            [
                ("inside", "Inside a public water service area", round(inside), None),
                ("outside", "Outside (self-supplied, often wells)", round(homes - inside), None),
            ]
        )

    mine = items(place)
    if mine is None or not mine[1]:
        return None
    theirs = items(other) if other is not None else None
    return Chart(
        id=f"{KEY}.water",
        kind="stack",
        title="Homes by public water service",
        unit="units",
        items=mine[0],
        total=mine[1],
        compare=None if theirs is None else ChartCompare(other.name, theirs[0]),
        source_ids=(WATER, BLOCKS),
        vintage=ctx.vintage(WATER),
        note="Pennsylvania DEP public water supplier service areas; 2020 homes by block.",
    )


def _act537_chart(
    ctx: ProfileContext, place: Area, other: Area | None, counties: pd.DataFrame
) -> Chart | None:
    if place.level == "tract" or "act537_20plus" not in counties.columns:
        return None
    if ACT537 not in ctx.registry or counties["act537_20plus"].isna().all():
        return None

    def items(area: Area):
        r = counties[counties["county"].isin(area.counties)]
        w = pd.to_numeric(r["homes"], errors="coerce").fillna(0)
        rows = []
        for k in ACT537_ORDER:
            v = (pd.to_numeric(r[f"act537_{k}"], errors="coerce").fillna(0) * w).sum()
            rows.append((k, ACT537_LABELS[k], round(float(v)), None))
        return items_from_counts(rows)

    made, total = items(place)
    if not total:
        return None
    compare = ChartCompare(other.name, items(other)[0]) if other is not None else None
    return Chart(
        id=f"{KEY}.act537",
        kind="bar",
        title="Homes by Act 537 sewage plan age",
        unit="units",
        items=made,
        total=total,
        compare=compare,
        source_ids=(ACT537, BLOCKS),
        vintage=ctx.vintage(ACT537),
        note="Each municipality's plan age from DEP's table (2025-05-29), weighted by 2020 homes; "
        "municipalities not matched to the table are left out of the total.",
    )


def _cwns_chart(ctx: ProfileContext, county: Area, counties: pd.DataFrame) -> Chart | None:
    if "cwns_total" not in counties.columns:
        return None
    r = counties[counties["county"] == county.geoid]
    if r.empty or pd.isna(r["cwns_total"].iloc[0]):
        return None
    row = r.iloc[0]
    values = [(k, label, round(_num(row[k])), None) for k, label in CWNS_ITEMS]
    values.sort(key=lambda v: -v[2])
    made, total = items_from_counts(values)
    if not total:
        return None
    return Chart(
        id=f"{KEY}.cwns_needs",
        kind="rank",
        title=f"Documented clean-water needs, {county.name}",
        unit="dollars",
        items=made,
        total=total,
        compare=None,
        source_ids=(CWNS,),
        vintage=ctx.vintage(CWNS),
        note="EPA Clean Watersheds Needs Survey 2022, January 2022 dollars, by category; "
        "share = category / sum of the categories shown.",
    )
