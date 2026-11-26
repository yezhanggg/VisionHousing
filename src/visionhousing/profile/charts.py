"""Chart-ready breakdowns for profile sections: counts, shares and margins, all worked out here.

Plain-language summary
----------------------
A chart in a place profile shows how a whole splits into parts: residents by
age, homes by building type, jobs by industry. This module makes the
:class:`~visionhousing.profile.model.Chart` records the sections attach.

- **Bands** come from the pack's ``acs_bands`` table (American Community Survey
  2020-2024 tables collapsed into a few bands each; see
  :mod:`visionhousing.features.acs_bands`). For a tract, owners and renters,
  building type and year built come from the tract table instead.
- **Shares** are worked out here, never in the browser:
  ``share = band count / total``, rounded to 4 decimals, where the total is the
  sum of the bands shown. The bands of a group cover its whole universe, so
  this equals the published total; when a band is left out (renters whose
  rent-to-income share "could not be computed") the total is the sum of the
  bands that remain, and the chart's note says so. Shares therefore add up to
  1, give or take rounding.
- **Comparison**: the same shares for the bigger area (the state for a county,
  the county for a tract), so a chart can show "here" next to "there".
- **Margins of error** of the counts are passed through as published (or the
  Bureau's approximation for a sum of cells).

A chart is left out (``None``) when the pack has no data for it; a section
then simply shows fewer charts.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from visionhousing.features import acs_bands as ab
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.model import Chart, ChartCompare, ChartItem, ChartKind

__all__ = ["SOURCE", "band_chart", "band_share", "items_from_counts", "largest",
           "published_median", "share_of"]  # fmt: skip

SOURCE = "acs5_profile_tables"
TRACT_TABLE_SOURCE = "acs5_summary_file"
SHARE_DIGITS = 4


def share_of(value: float | None, total: float | None) -> float | None:
    """``value / total`` rounded to 4 decimals; ``None`` when either is missing or total is 0."""
    if value is None or total is None or total <= 0:
        return None
    return round(value / total, SHARE_DIGITS)


def items_from_counts(
    rows: Sequence[tuple[str, str, float | None, float | None]],
) -> tuple[tuple[ChartItem, ...], float | None]:
    """Turn ``(key, label, count, moe)`` rows into chart items with shares of their sum.

    Returns:
        The items and their total (``None`` when no count is known).
    """
    known = [r[2] for r in rows if r[2] is not None]
    total = float(sum(known)) if known else None
    items = tuple(
        ChartItem(
            label=label,
            value=None if value is None else float(value),
            share=share_of(value, total),
            moe=None if moe is None else float(moe),
            key=key,
        )
        for key, label, value, moe in rows
    )
    return items, total


def _group_items(
    values: dict[str, float | None], group: ab.Group, exclude: Sequence[str]
) -> tuple[tuple[ChartItem, ...], float | None] | None:
    rows = []
    for band in group.bands:
        if band.key in exclude:
            continue
        col = group.column(band.key)
        if col not in values:
            return None
        rows.append((band.key, band.label, values[col], values.get(f"{col}_moe")))
    if all(r[2] is None for r in rows):
        return None
    return items_from_counts(rows)


def band_chart(
    ctx: ProfileContext,
    section: str,
    name: str,
    group_key: str,
    kind: ChartKind,
    place: Area,
    other: Area | None,
    *,
    title: str | None = None,
    exclude: Sequence[str] = (),
    note: str = "",
) -> Chart | None:
    """One chart of a collapsed ACS group for a place, with the bigger area's shares.

    Args:
        ctx: The profile context.
        section: The section key (the chart id starts with it).
        name: Short chart name; the id is ``<section>.<name>``.
        group_key: The group in :data:`visionhousing.features.acs_bands.GROUPS`.
        kind: ``pie``, ``bar``, ``stack`` or ``rank``.
        place: The place.
        other: The bigger area to compare with, or ``None``.
        title: Plain title (default: the group's title).
        exclude: Band keys left out of the chart and of its total.
        note: Extra words for the chart's note.

    Returns:
        The chart, or ``None`` when the pack has no bands for the place.
    """
    group = ab.group(group_key)
    mine = ctx.bands(place)
    if mine is None:
        return None
    values, from_tracts = mine
    made = _group_items(values, group, exclude)
    if made is None or made[1] is None or made[1] <= 0:
        return None
    items, total = made
    sources = [TRACT_TABLE_SOURCE if group_key in from_tracts else SOURCE]
    compare = None
    if other is not None:
        theirs = ctx.bands(other)
        if theirs is not None:
            made_other = _group_items(theirs[0], group, exclude)
            if made_other is not None and made_other[1]:
                compare = ChartCompare(other.name, made_other[0])
                src = TRACT_TABLE_SOURCE if group_key in theirs[1] else SOURCE
                if src not in sources:
                    sources.append(src)
    words = [f"ACS {group.table}, {group.universe}; share = count / total shown"]
    if exclude:
        left = [b.label.lower() for b in group.bands if b.key in exclude]
        words.append(f"total leaves out: {', '.join(left)}")
    if group_key in from_tracts:
        words.append("tract counts from the tract table")
    if note:
        words.append(note)
    return Chart(
        id=f"{section}.{name}",
        kind=kind,
        title=title or group.title,
        unit=group.unit,
        items=items,
        total=total,
        compare=compare,
        source_ids=tuple(sources),
        vintage=ctx.vintage(sources[0]),
        note="; ".join(words) + ".",
    )


def largest(chart: Chart | None) -> ChartItem | None:
    """The item with the biggest share (the first one on a tie), or ``None``."""
    if chart is None:
        return None
    best = None
    for item in chart.items:
        if item.share is not None and (best is None or item.share > best.share):
            best = item
    return best


def band_share(
    ctx: ProfileContext, group_key: str, num: Sequence[str], den: Sequence[str]
) -> Callable[[Area], Measure]:
    """A measure: the share of ``den`` bands that fall in ``num`` bands, with its margin.

    The margin uses the Bureau's formula for a proportion,
    ``sqrt(moe_num^2 - p^2 * moe_den^2) / den`` (``+`` when the term under the
    root is negative), with the margins of the summed bands from the
    approximation for a sum.

    Raises:
        MissingDataError: The pack has no bands for the area.
    """
    group = ab.group(group_key)

    def total(values: dict[str, float | None], keys: Sequence[str]) -> tuple[float | None, float]:
        value = 0.0
        squares = 0.0
        for k in keys:
            v = values.get(group.column(k))
            if v is None:
                return None, 0.0
            value += v
            m = values.get(f"{group.column(k)}_moe")
            squares += (m or 0.0) ** 2
        return value, math.sqrt(squares)

    def measure(area: Area) -> Measure:
        found = ctx.bands(area)
        if found is None:
            raise MissingDataError(f"no acs_bands row for {area.geoid}")
        n, n_moe = total(found[0], num)
        d, d_moe = total(found[0], den)
        if n is None or d is None or d <= 0:
            return Measure(None)
        p = n / d
        under = n_moe**2 - p**2 * d_moe**2
        if under < 0:
            under = n_moe**2 + p**2 * d_moe**2
        return Measure(p, math.sqrt(under) / d)

    return measure


PUBLISHED_NOTE = "the Census Bureau's published median for the {level}"


def published_median(ctx: ProfileContext, area: Area, name: str) -> Measure | None:
    """A county's or the state's published ACS median from ``acs_bands`` (``None`` if absent).

    ``name`` is ``median_household_income`` or ``median_gross_rent``. Tracts read
    their published median from the tract table instead, so this returns
    ``None`` for a tract.
    """
    if area.single:
        return None
    found = ctx.bands(area)
    if found is None or found[0].get(name) is None:
        return None
    values = found[0]
    return Measure(values[name], values.get(f"{name}_moe"), PUBLISHED_NOTE.format(level=area.level))
