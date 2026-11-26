"""Profile section ``zoning``: who regulates zoning here (PA DCED Municipal Statistics).

Plain-language summary
----------------------
In Pennsylvania each municipality decides whether to zone. Where it does not,
the county's zoning ordinance may apply, or none at all. This section says,
from DCED's record (``municipal_planning`` table):

- for a **county**: how many municipalities it has, and what share of its
  people (2024 estimates) live where zoning is set by the municipality's own
  ordinance, a joint (multi-municipal) ordinance, the county's ordinance, or
  no ordinance. Compared with the state.
- for a **tract**: the municipalities it lies in, and the share of its 2020
  homes under each kind of zoning (a tract crossing a municipal line is split
  by where its homes are). Compared with its county, worked out the same way.

Where the law tables are in the pack (:mod:`visionhousing.law`), it also says
what the rules allow, municipality by municipality:

- for a **county**: the share of its residents whose municipality's ordinance
  has been read and verified (compared with the state), how many
  municipalities fall under the county's own zoning ordinance, and the ADU
  rule of each verified municipality (all rules when the county is one
  municipality);
- for a **tract**: the rules of the municipality holding most of its homes:
  ADU rule with its citation, reading date and method, smallest residential
  minimum lot, parking minimums and housing types permitted.

A municipality whose ordinance nobody has read shows "not read yet
(unknown)", never "no". Statewide zoning maps are not openly available.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from visionhousing.features.zoning_status import load_rules
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections import _law
from visionhousing.profile.sections._common import FactMaker, finish, sentence

KEY = "zoning"
TITLE = "Zoning and overlays"
SOURCE = "pa_dced_munstats"
YEAR = "current DCED record"


def _munis(ctx: ProfileContext) -> pd.DataFrame:
    m = ctx.need("municipal_planning").copy()
    m["population"] = pd.to_numeric(m["population"], errors="coerce").fillna(0)
    m["housing_units_2020"] = pd.to_numeric(m["housing_units_2020"], errors="coerce").fillna(0)
    m["zoning_regime"] = m["zoning_regime"].fillna("")
    return m


def _tract_pieces(ctx: ProfileContext, munis: pd.DataFrame) -> pd.DataFrame:
    pieces = ctx.need("tract_municipalities").copy()
    pieces["housing_units_2020"] = pd.to_numeric(
        pieces["housing_units_2020"], errors="coerce"
    ).fillna(0)
    for c in ("county", "cousub"):
        pieces[c] = pieces[c].astype(str).str.zfill(5)
    keep = munis[["county", "cousub", "name", "zoning_regime"]].copy()
    keep["cousub"] = keep["cousub"].astype(str).str.zfill(5)
    return pieces.merge(keep, on=["county", "cousub"], how="left")


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``zoning`` section for a county or tract."""
    place, other = ctx.place(geoid)
    munis = _munis(ctx)
    munis["county"] = munis["county"].astype(str)
    regimes: dict[str, str] = load_rules()["regimes"]

    def in_area(area: Area) -> pd.DataFrame:
        return munis if area.level == "state" else munis[munis["county"].isin(area.counties)]

    if place.level == "county":
        if in_area(place).empty:
            raise MissingDataError(f"no DCED municipalities for county {place.geoid}")

        def pop_share(regime: str):
            def measure(area: Area) -> Measure:
                rows = in_area(area)
                total = rows["population"].sum()
                if total <= 0:
                    return Measure(None)
                return Measure(
                    float(rows.loc[rows["zoning_regime"] == regime, "population"].sum() / total)
                )

            return measure

        fm = FactMaker(ctx, KEY, place, other)
        fm.add(
            "municipalities",
            "Municipalities",
            "municipalities",
            SOURCE,
            YEAR,
            lambda a: Measure(float(len(in_area(a)))),
            compare=False,
        )
        fm.add(
            "no_zoning_municipalities",
            "Municipalities with no zoning ordinance",
            "municipalities",
            SOURCE,
            YEAR,
            lambda a: Measure(float((in_area(a)["zoning_regime"] == "none").sum())),
            compare=False,
        )
        for regime, label in regimes.items():
            fm.add(
                f"people_{regime}",
                f"Share of people under: {label.lower()}",
                "share",
                SOURCE,
                "2024 population estimates",
                pop_share(regime),
                note="population of municipalities in each group / county population "
                "(Census 2024 estimates)",
            )
        facts = fm.facts
        own, none_ = fm.get("people_own"), fm.get("people_none")
        n = fm.get("municipalities")
        parts = []
        if n and n.value == 1:
            parts.append(f"{place.name} is a single municipality")
        elif n and n.value is not None:
            parts.append(f"{place.name} has {n.text} municipalities")
        if own and own.value is not None:
            parts.append(
                f"{own.text} of its people live where the municipality sets its own zoning"
            )
        if none_ and none_.value:
            parts.append(f"{none_.text} live where no zoning ordinance applies")
        summary = sentence(parts)
    else:
        pieces = _tract_pieces(ctx, munis)

        def homes_share(regime: str):
            def measure(area: Area) -> Measure:
                if area.level == "tract":
                    rows = pieces[pieces["tract"].astype(str) == area.geoid]
                else:
                    rows = pieces[pieces["county"].isin(area.counties)]
                total = rows["housing_units_2020"].sum()
                if total <= 0:
                    return Measure(None)
                hit = rows.loc[rows["zoning_regime"] == regime, "housing_units_2020"].sum()
                return Measure(float(hit / total))

            return measure

        mine = pieces[pieces["tract"].astype(str) == place.geoid]
        if mine.empty:
            raise MissingDataError(f"tract {place.geoid} is not in the municipality table")
        mine = mine.sort_values("housing_units_2020", ascending=False)
        names = [str(n) for n in mine["name"].dropna().unique()]
        fm = FactMaker(ctx, KEY, place, other)
        facts = [
            Fact(
                id=f"{KEY}.municipalities",
                label="Municipality (largest share of homes first)",
                value="; ".join(names) or None,
                unit="text",
                text="; ".join(names) or "not available",
                source=SOURCE,
                vintage=ctx.vintage(SOURCE),
                year="2020 municipal boundaries",
                measured_at="tract",
                note="from the tract's 2020 census blocks and the Census block assignment file",
            )
        ]
        for regime, label in regimes.items():
            fm.add(
                f"homes_{regime}",
                f"Share of 2020 homes under: {label.lower()}",
                "share",
                SOURCE,
                "2020 housing units",
                homes_share(regime),
                note="2020 housing units of the tract's blocks in each group / all its 2020 "
                "housing units",
            )
        facts += fm.facts
        regime = mine["zoning_regime"].iloc[0] if len(mine) else ""
        summary = (
            sentence(
                [
                    f"This tract lies in {names[0]}"
                    + (f", where zoning is: {regimes[regime].lower()}" if regime in regimes else "")
                ]
            )
            if names
            else ""
        )
    law_facts, law_summary = _law_facts(
        ctx, place, other, munis, mine if place.level != "county" else None
    )
    facts = list(facts) + law_facts
    if law_summary:
        summary = (summary[:-1] + "; " + law_summary + ".") if summary else law_summary + "."
    engine_words = _engine_words(ctx, munis, place)
    if engine_words:
        where = "here" if place.level == "county" else "in its county"
        tail = f"this tool's ADU estimate {where} uses these read rules, not the statewide guess"
        summary = (
            (summary[:-1] + "; " + tail + ".") if summary else tail[0].upper() + tail[1:] + "."
        )
        facts.append(
            Fact(
                id=f"{KEY}.adu_estimate_uses_local_law",
                label="Used in this tool's estimates",
                value="read local law",
                unit="category",
                text=engine_words,
                source=LAW_SOURCE,
                vintage=ctx.vintage(LAW_SOURCE),
                year="as read (see each rule's date)",
                measured_at="county",
                note="assumptions/law/hooks.yaml: a county uses its own already-legal ADU share "
                "and parking reach when verified rules cover enough of its residents",
            )
        )
    limits = (
        "From DCED's Municipal Statistics, which municipalities report themselves; an entry may "
        "be out of date. It says who sets zoning, not what the zoning allows (densities, lot "
        "sizes, uses), and not when the ordinance was last changed. Joint zoning means a "
        "municipality's ordinance is shared with neighbours. DCED names were matched to Census "
        "municipalities by name. ADU, lot and parking rules come only from ordinances a person "
        "read on the municipality's own website; the rest of the state is unknown, not 'no'. "
        "Posted copies can lag amendments."
    )
    return finish(KEY, TITLE, summary, facts, limits)


LAW_SOURCE = "pa_municipal_ordinances"


def _engine_words(ctx: ProfileContext, munis: pd.DataFrame, place: Area) -> str:
    """Plain words when the engine uses this county's read rules (``law.hooks``), else ``""``."""
    from visionhousing.law.hooks import local_law_for_engine

    try:
        rules = _law.rules_table(ctx)
    except MissingDataError:
        return ""
    if place.level == "state" or not place.counties:
        return ""
    if "_local_law" not in ctx._cache:
        ctx._cache["_local_law"] = local_law_for_engine(rules, munis)
    law = ctx._cache["_local_law"]
    if law is None:
        return ""
    words = law.plain_words(place.counties[0])
    if not words:
        return ""
    return words


VERIFIED_SHOWN = 6
"""At most this many verified municipalities are listed for a county (display only)."""


def _law_facts(
    ctx: ProfileContext,
    place: Area,
    other: Area | None,
    munis: pd.DataFrame,
    mine: pd.DataFrame | None,
) -> tuple[list[Fact], str]:
    """Facts from the law tables; empty when the pack has none."""
    try:
        rules = _law.rules_table(ctx)
    except MissingDataError:
        return [], ""
    m = munis.copy()
    m["geoid"] = _law.muni_geoids(m)
    joined = m.merge(
        rules.drop(columns=["name", "dced_id", "zoning_regime"], errors="ignore"),
        on="geoid",
        how="left",
    )
    if mine is not None:
        top = mine.iloc[0]
        geoid = str(top["county"]).zfill(5) + str(top["cousub"]).zfill(5)
        hit = joined[joined["geoid"] == geoid]
        if hit.empty:
            return [], ""
        row = hit.iloc[0]
        facts = _law.rule_facts(ctx, KEY, row, str(row["name"]))
        return facts, f"in {row['name']} ADUs are {_law.ADU_WORDS[str(row['adu_status'])]}"

    def in_area(area: Area) -> pd.DataFrame:
        return joined if area.level == "state" else joined[joined["county"].isin(area.counties)]

    def verified_share(area: Area) -> Measure:
        rows = in_area(area)
        total = rows["population"].sum()
        if total <= 0:
            return Measure(None)
        return Measure(float(rows.loc[rows["status"] == "verified", "population"].sum() / total))

    fm = FactMaker(ctx, KEY, place, other)
    fm.add(
        "share_read",
        "Share of residents whose municipality's zoning rules have been read and verified",
        "share",
        LAW_SOURCE,
        "2024 population estimates",
        verified_share,
        note="residents of municipalities with a verified row in muni_zoning_rules / all residents",
    )
    facts = list(fm.facts)
    land = ctx.table("county_land_use")
    if land is not None and place.level == "county":
        row = land[land["county"].astype(str).str.zfill(5) == place.geoid]
        if len(row):
            r = row.iloc[0]
            n, people = int(r["munis_under_county_zoning"]), int(r["residents_under_county_zoning"])
            facts.append(
                Fact(
                    id=f"{KEY}.under_county_zoning",
                    label="Municipalities under the county's zoning ordinance",
                    value=n,
                    unit="municipalities",
                    text=f"{n:,} ({people:,} residents)",
                    source=SOURCE,
                    vintage=ctx.vintage(SOURCE),
                    year=YEAR,
                    measured_at="county",
                    note="county zoning applies only where a municipality has none of its own "
                    "(Municipalities Planning Code section 602)",
                )
            )
    here = in_area(place)
    if len(here) == 1:
        row = here.iloc[0]
        facts += _law.rule_facts(ctx, KEY, row, str(row["name"]))
        return facts, f"in {row['name']} ADUs are {_law.ADU_WORDS[str(row['adu_status'])]}"
    read = here[here["status"] == "verified"].sort_values("population", ascending=False)
    for _, row in read.head(VERIFIED_SHOWN).iterrows():
        f = _law.rule_facts(ctx, KEY, row, str(row["name"]))[0]
        facts.append(replace(f, id=f"{KEY}.adu_rule_{row['geoid']}"))
    share = fm.get("share_read")
    words = f"{share.text} of residents live where the zoning rules have been read" if share else ""
    return facts, words
