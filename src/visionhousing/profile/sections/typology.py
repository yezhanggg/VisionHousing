"""Profile section ``typology``: places like this one.

Plain-language summary
----------------------
Reads the pack tables ``typology_county`` and ``typology_tract`` (built by
:mod:`visionhousing.features.typology`) and says:

- which group of similar places this county or tract is in, by the group's
  rule-made name ("Suburban, growing, higher-cost"), and how many places
  share it;
- what sets the group apart from the state's average place (its most unusual
  features, in words);
- the few places most like this one ("Similar places: X, Y and Z"): the
  nearest in the same z-score space, whatever their group.

Every number and name comes from the pack; the method, the features, k and
the seed are in ``assumptions/typology.yaml``.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.features import typology as ty
from visionhousing.profile.context import MissingDataError, ProfileContext, tract_name
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections._common import finish, sentence

KEY = "typology"
TITLE = "Places like this one"
SOURCE = "acs5_summary_file"
LIMITS = (
    "Groups and neighbours describe how alike places are on a few published numbers "
    "(population, growth, density, rents, home values, building, transit, renters, empty "
    "homes, age); they say nothing about why. Survey numbers carry margins of error that are "
    "not carried into the grouping. Missing numbers are set to the state median. The number "
    "of groups comes from a rule (the elbow rule), not from a test that the groups are real; "
    "another rule could give more or fewer. Places near the edge of a group may be as close "
    "to the next group."
)


def _names(ctx: ProfileContext, ids: list[str], level: str) -> list[str]:
    if level == "county":
        return [ctx.county_name(g) for g in ids]
    return [f"{tract_name(g)} ({ctx.county_name(g[:5])})" for g in ids]


def typology_table(ctx: ProfileContext, level: str) -> pd.DataFrame:
    """The pack's ``typology_<level>`` table.

    A pack built before the typology existed (or Testland) has no such table;
    it is then worked out from the pack's own tables with the same function
    the pack build uses, so the section always follows one method.
    """
    name = f"typology_{level}"
    table = ctx.table(name)
    if table is not None and not table.empty:
        return table
    key = "typology_tables"
    if key not in ctx._cache:
        tables = {n: ctx.table(n) for n in ("counties", "tracts", "permits_county_year")}
        ctx._cache[key] = ty.typology_tables({n: t for n, t in tables.items() if t is not None})
    if name not in ctx._cache[key]:
        raise MissingDataError(f"the {ctx.state} pack has too few places for {name}")
    return ctx._cache[key][name]


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``typology`` section (``None`` when the place has no group)."""
    place, _ = ctx.place(geoid)
    table = typology_table(ctx, place.level).copy()
    table["geoid"] = table["geoid"].astype(str)
    hit = table[table["geoid"] == place.geoid]
    if hit.empty:
        return Section.no_data(
            KEY,
            TITLE,
            "This place is not grouped (too few residents for its numbers to mean much).",
        )
    row = hit.iloc[0]
    rules = ty.load_rules()
    features = str(row["features"]).split(";")
    z, _ = ty.zscores(
        table.set_index("geoid")[features],
        {s["name"]: s.get("transform", "none") for s in rules[f"{place.level}_features"]},
    )
    centre = z[table.set_index("geoid")["type_id"] == row["type_id"]].mean()
    apart = ty.traits(centre, rules)
    similar_ids = [s for s in str(row["similar"]).split(";") if s]
    similar = _names(ctx, similar_ids, place.level)
    vintage = ctx.vintage(SOURCE)
    years = ctx.acs_years()
    plural = "counties" if place.level == "county" else "tracts"
    k, members, total = int(row["k"]), int(row["places_in_type"]), len(table)
    method = (
        f"k-means on {len(features)} z-scored features ({', '.join(features)}), k = {k} by the "
        f"elbow rule, seed {rules['seed']} (assumptions/typology.yaml)"
    )

    def fact(name: str, label: str, value, unit: str, text: str, note: str = "") -> Fact:
        return Fact(f"{KEY}.{name}", label, value, unit, text, SOURCE, vintage, years,
                    place.level, note=note)  # fmt: skip

    facts = [
        fact("type_name", "Group of similar places", str(row["type_name"]), "text",
             str(row["type_name"]), note=f"group {int(row['type_id'])} of {k}; {method}"),
        fact("places_in_type", f"{plural.capitalize()} in this group", members, plural,
             f"{members:,} of {total:,}"),
        fact("similar", "Similar places", "; ".join(similar_ids), "text", ", ".join(similar),
             note="nearest in the same z-score space, nearest first"),
    ]  # fmt: skip
    if apart:
        why = (
            "the group's most unusual features against the state's average place "
            "(at least half a standard deviation)"
        )
        facts.append(
            fact("traits", "What sets the group apart", "; ".join(apart), "text",
                 ", ".join(apart), note=why)
        )  # fmt: skip
    distance = pd.to_numeric(row.get("distance"), errors="coerce")
    if pd.notna(distance):
        facts.append(fact("distance", "Distance from the group's centre", float(distance),
                          "z-score units", f"{float(distance):.2f}",
                          note="0 = the group's typical place; larger = less typical"))  # fmt: skip
    parts = [f"{place.name} is in the group “{row['type_name']}” "
             f"({members:,} of the state's {total:,} {plural})"]  # fmt: skip
    if similar:
        joined = (
            similar[0] if len(similar) == 1 else ", ".join(similar[:-1]) + " and " + similar[-1]
        )
        parts.append(f"the places most like it are {joined}")
    return finish(KEY, TITLE, sentence(parts), facts, LIMITS)
