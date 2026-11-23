"""Profile section ``owners``: who owns and who buys single-family homes (counts only).

Plain-language summary
----------------------
For the two counties with open parcel records (``owners_area`` table):

- **who owns today (stock)**: the share of single-family parcels owned by an
  entity (Philadelphia: the owner name contains a word such as LLC, INC or
  TRUST, listed in ``assumptions/owners.yaml``; Allegheny: the assessment
  office's owner type is "CORPORATION"), the share with no homestead
  exemption, and the share whose tax bill goes outside Pennsylvania;
- **who is buying (flow, Philadelphia only)**: the share of arm's-length
  single-family deeds that went to an entity grantee, each year
  (``owners_deeds_year``).

Next to these, published figures (Philadelphia Fed 2025, Pew 2024; both for
Philadelphia only) are shown as cited benchmarks from
``assumptions/evidence.yaml``. They use other definitions and are not
computed here.

No owner name is stored or shown anywhere.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.features.owners import benchmarks
from visionhousing.profile.context import Measure, ProfileContext
from visionhousing.profile.format import fmt
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence
from visionhousing.profile.sections._local import area_row, count_of, share_of

KEY = "owners"
TITLE = "Institutional buyers"
SOURCES = {"42101": "phl_opa_properties", "42003": "alle_assessments"}
DEEDS_SOURCE = "phl_rtt_summary"


def _deed_facts(ctx: ProfileContext, county: str, measured_at: str) -> list[Fact]:
    table = ctx.table("owners_deeds_year")
    if table is None or table.empty:
        return []
    t = table[table["county"].astype(str) == county].copy()
    for c in ("year", "deeds", "entity_deeds", "partial_year"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    facts = []
    for r in t.sort_values("year", ascending=False).itertuples():
        if not r.deeds:
            continue
        share = r.entity_deeds / r.deeds
        partial = " (part year)" if r.partial_year == 1 else ""
        facts.append(
            Fact(
                id=f"{KEY}.entity_deeds_{int(r.year)}",
                label=f"Share of single-family purchases by entities, {int(r.year)}{partial}",
                value=share,
                unit="share",
                text=fmt(share, "share"),
                source=DEEDS_SOURCE,
                vintage=ctx.vintage(DEEDS_SOURCE),
                year=str(int(r.year)),
                measured_at=measured_at,
                note=f"{int(r.entity_deeds):,} of {int(r.deeds):,} arm's-length deeds went to "
                "a grantee "
                "whose name contains an entity word",
            )
        )
    return facts


def _benchmark_facts(ctx: ProfileContext) -> list[Fact]:
    out = []
    for b in benchmarks():
        s = b["source"]
        source_id = b["registry_id"]
        out.append(
            Fact(
                id=f"{KEY}.benchmark_{b['id']}",
                label=f"Published benchmark: {b['measure']} ({b['place']})",
                value=float(b["value"]),
                unit=b["unit"],
                text=fmt(float(b["value"]), b["unit"]),
                source=source_id,
                vintage=f"published, read {s['accessed']}",
                year=str(b["period"]),
                measured_at="county",
                note=f"Cited, not computed here. {b['definition'].strip()} Source: "
                f"{' '.join(s['citation'].split())}, {s['locator']}. {s['url']}",
            )
        )
    return out


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``owners`` section (Philadelphia and Allegheny only)."""
    place, other = ctx.place(geoid)
    county = place.geoid[:5]
    table = ctx.table("owners_area")
    if county not in SOURCES or table is None or table.empty:
        return Section.no_data(
            KEY,
            TITLE,
            "No free parcel owner records for this county: only Philadelphia and Allegheny "
            "County publish them openly.",
        )
    table = table.copy()
    table["geoid"] = table["geoid"].astype(str)
    row0 = area_row(table, place)
    if row0 is None:
        return Section.no_data(
            KEY, TITLE, f"No single-family parcels were placed in this {place.level}."
        )
    source = SOURCES[county]
    year = f"records read {row0['as_of']}"
    fm = FactMaker(ctx, KEY, place, other if place.level == "tract" else None)

    def share(col: str):
        return lambda a: share_of(
            count_of(area_row(table, a), col), count_of(area_row(table, a), "sf_parcels")
        )

    fm.add(
        "sf_parcels",
        "Single-family parcels",
        "parcels",
        source,
        year,
        lambda a: Measure(count_of(area_row(table, a), "sf_parcels")),
        compare=False,
    )
    entity_label = (
        "Share of single-family homes owned by an entity (name test)"
        if county == "42101"
        else "Share of single-family homes with owner type corporation"
    )
    fm.add(
        "entity_share",
        entity_label,
        "share",
        source,
        year,
        share("sf_entity"),
        note=str(row0["entity_basis"]),
    )
    fm.add(
        "non_homestead_share",
        "Share of single-family homes with no homestead exemption",
        "share",
        source,
        year,
        share("sf_non_homestead"),
        note="the owner has not claimed the home as their main residence; many owner-occupiers "
        "never apply",
    )
    fm.add(
        "out_of_state_share",
        "Share of single-family homes billed to an out-of-state address",
        "share",
        source,
        year,
        share("sf_out_of_state"),
    )
    facts = list(fm.facts)
    if county == "42101":
        facts += _deed_facts(ctx, county, "county")
        facts += _benchmark_facts(ctx)
    e = fm.get("entity_share")
    parts = []
    if e and e.value is not None:
        parts.append(f"{e.text} of single-family homes here are owned by an entity")
    latest = next(
        (f for f in facts if f.id.startswith(f"{KEY}.entity_deeds_") and "part" not in f.label),
        None,
    )
    if latest is not None:
        parts.append(
            f"in {latest.year}, {latest.text} of the county's single-family purchases "
            "went to entities"
        )
    limits = (
        "An entity is not the same as an institutional investor: it includes one-home "
        "landlord companies, nonprofits, churches, banks after foreclosure and trusts, and "
        "many trusts are family estate planning. A missing homestead exemption does not prove "
        "a home is rented. Out-of-state means the tax bill goes outside Pennsylvania. "
        + (
            "Philadelphia's entity test reads owner and grantee names for words such as LLC "
            "and TRUST on the City's server; names are never kept. Deeds count arm's-length "
            "sales of homes that are single-family today. The published benchmarks use other "
            "definitions (for example owners of 26 or more homes) and are shown only for "
            "context. The City's data licence is to be confirmed; only totals are shown."
            if county == "42101"
            else "Allegheny's owner type is the assessment office's code, not a name test, so "
            "trusts and many small landlords are not counted as corporations."
        )
    )
    return finish(KEY, TITLE, sentence(parts), facts, limits)
