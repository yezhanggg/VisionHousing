"""County ranks: where a county stands among its state's counties on a few market measures.

Plain-language summary
----------------------
A county profile lists the county's rank among all counties of the state,
for example "12th highest of 67 counties in Pennsylvania", for six measures:

====================== ============================================================
measure                how it is worked out
====================== ============================================================
median household income the Bureau's published county median (B19013) where the
                       pack has it, else the tract medians weighted by households
median gross rent      the published county median (B25064), else the tract
                       medians weighted by renter households
rent-burden share      renter households paying 30% or more of income in rent /
                       renter households whose share could be worked out (B25070)
jobs per resident      jobs located in the county (LODES) / residents (ACS)
permits per 1,000 homes average yearly homes permitted over the last
                       ``permit_years`` years (``assumptions/typology.yaml``) /
                       homes x 1,000
population growth      ACS 2020-2024 residents / 2020 Census residents - 1
====================== ============================================================

The last three use the same formulas as the "places like this one" grouping
(:func:`visionhousing.features.typology.county_features`).

Ranking rules
-------------
- Rank 1 is the **highest** value ("12th of 67" counts from the top).
- The words count from the nearer end: "4th highest of 67" in the top half,
  "9th lowest of 67" in the bottom half (``direction`` is ``highest`` or
  ``lowest``).
- Only counties with a value are ranked; "of N" counts them.
- Values are compared after rounding to 6 significant digits, so tiny
  floating-point differences never split a tie.
- **Ties** share the best rank among them ("competition" ranking: 1, 2, 2, 4),
  and the rank says "tied".
- The result never depends on the order of the rows: counties are sorted by
  value, then by code.

The ranks are worked out once per state and kept with the pack's context.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping

import numpy as np
import pandas as pd

from visionhousing.profile.context import ProfileContext
from visionhousing.profile.format import fmt
from visionhousing.profile.model import Rank

__all__ = ["MEASURES", "county_ranks", "ordinal", "rank_values"]

MEASURES: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    ("median_household_income", "Median household income", "dollars",
     ("acs5_profile_tables",), "published county median (B19013)"),
    ("median_gross_rent", "Median gross rent", "dollars per month",
     ("acs5_profile_tables",), "published county median (B25064)"),
    ("rent_burden_share", "Renters paying 30% or more of income in rent", "share",
     ("acs5_profile_tables",), "renter households paying 30%+ / those with a computed share"),
    ("jobs_per_resident", "Jobs per resident", "ratio",
     ("lodes_wac", "acs5_summary_file"), "LODES jobs located in the county / ACS residents"),
    ("permits_per_1000", "Homes permitted a year per 1,000 homes", "per 1,000 homes",
     ("bps_county_annual", "acs5_summary_file"),
     "average yearly permitted units over the last permit_years years / homes x 1,000"),
    ("population_change", "Population growth since 2020", "share",
     ("acs5_summary_file", "tiger_2020_tabblock"),
     "ACS 2020-2024 residents / 2020 Census residents - 1"),
)  # fmt: skip
"""Measure id, label, unit, source ids and formula in words, in display order."""

SIGNIFICANT = 6


def ordinal(n: int) -> str:
    """``1`` -> ``"1st"``, ``12`` -> ``"12th"``, ``22`` -> ``"22nd"``."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _round(x: float) -> float:
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, SIGNIFICANT - 1 - math.floor(math.log10(abs(x))))


def rank_values(values: Mapping[str, float | None]) -> dict[str, tuple[int, int, bool]]:
    """Rank places by value, highest first, with ties sharing the best rank.

    Args:
        values: Place code to value; ``None`` or NaN values are not ranked.

    Returns:
        Place code to ``(rank, how many ranked, tied)``.
    """
    known = {
        k: _round(float(v))
        for k, v in values.items()
        if v is not None and not (isinstance(v, float) and math.isnan(v))
    }
    order = sorted(known, key=lambda k: (-known[k], k))
    first: dict[float, int] = {}
    counts: dict[float, int] = {}
    for position, code in enumerate(order, start=1):
        first.setdefault(known[code], position)
        counts[known[code]] = counts.get(known[code], 0) + 1
    out: dict[str, tuple[int, int, bool]] = {}
    for code in order:
        v = known[code]
        out[code] = (first[v], len(order), counts[v] > 1)
    return out


def _bands_column(ctx: ProfileContext, compute: Callable[[pd.DataFrame], pd.Series]) -> pd.Series:
    table = ctx.table("acs_bands")
    if table is None or table.empty:
        return pd.Series(dtype=float)
    rows = table[table["level"] == "county"]
    return compute(rows.set_index(rows["geoid"].astype(str)))


def _num(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


def _table(ctx: ProfileContext) -> pd.DataFrame:
    """One row per county, one column per measure (NaN where the pack lacks the data)."""
    if "ranks" in ctx._cache:
        return ctx._cache["ranks"]
    from visionhousing.features import typology as ty

    counties = ctx.counties()
    tracts = ctx.need("tracts")
    rules = ty.load_rules()
    feats = ty.county_features(
        counties, tracts, ctx.table("permits_county_year"), int(rules["permit_years"]),
        jobs=ctx.table("jobs_county"),
    )  # fmt: skip
    out = pd.DataFrame(index=feats.index)
    weighted_income = ty._weighted(tracts.assign(county=tracts["county"].astype(str)),
                                   "median_household_income",
                                   ["households_owner", "households_renter"])  # fmt: skip
    published = _bands_column(ctx, lambda r: _num(r, "median_household_income"))
    published = published.reindex(out.index)
    out["median_household_income"] = published.fillna(weighted_income.reindex(out.index))
    out["_estimated_median_household_income"] = published.isna()
    rent_published = _bands_column(ctx, lambda r: _num(r, "median_gross_rent"))
    rent_published = rent_published.reindex(out.index)
    out["median_gross_rent"] = rent_published.fillna(feats["median_rent"])
    out["_estimated_median_gross_rent"] = rent_published.isna()

    def burden(rows: pd.DataFrame) -> pd.Series:
        hit = _num(rows, "burden_30_49") + _num(rows, "burden_50_plus")
        return hit / (hit + _num(rows, "burden_lt_30")).where(lambda d: d > 0)

    out["rent_burden_share"] = _bands_column(ctx, burden).reindex(out.index)
    for name in ("jobs_per_resident", "permits_per_1000", "population_change"):
        out[name] = feats[name] if name in feats.columns else np.nan
    ctx._cache["ranks"] = out
    return out


def county_ranks(ctx: ProfileContext, geoid: str) -> tuple[Rank, ...]:
    """The county's rank among the state's counties on each measure it has a value for."""
    table = _table(ctx)
    if geoid not in table.index:
        return ()
    n_counties = len(table)
    out = []
    for name, label, unit, sources, formula in MEASURES:
        column = table[name]
        value = column.get(geoid)
        if value is None or pd.isna(value):
            continue
        ranked = rank_values(column.to_dict())
        rank, of, tied = ranked[geoid]
        low, _, _ = rank_values((-column).to_dict())[geoid]
        tie = "tied " if tied else ""
        place_words = f"counties in {ctx.state_name}"
        if rank <= low:
            direction, n = "highest", rank
        else:
            direction, n = "lowest", low
        if n == 1 and not tied:
            words = f"the {direction} of {of} {place_words}"
        else:
            words = f"{tie}{ordinal(n)} {direction} of {of} {place_words}"
        note = formula
        if bool(table.get(f"_estimated_{name}", pd.Series(False, index=table.index))[geoid]):
            note = "estimated: the average of the county's tract medians, weighted by households"
        if of < n_counties:
            note += f"; {n_counties - of} counties have no value and are not ranked"
        out.append(
            Rank(
                id=f"rank.{name}",
                label=label,
                value=round(float(value), 4),
                text=fmt(float(value), unit),
                unit=unit,
                rank=rank,
                of=of,
                tied=tied,
                text_rank=f"{ordinal(rank)} of {of}",
                words=words,
                direction=direction,
                source_ids=sources,
                note=note,
            )
        )
    return tuple(out)
