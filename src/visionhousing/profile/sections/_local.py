"""Shared helpers for the local sections (zoning, plans, parcels, owners, pipeline_local).

Plain-language summary
----------------------
These sections read tier-2 tables that exist only for some places (see
:mod:`visionhousing.data.local`). The helpers here find a place's row in a
table that has one row per tract and one per county (``level``, ``geoid``),
and divide two counts safely, so every section words "no data" the same way.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.profile.context import Area, Measure

__all__ = ["area_row", "count_of", "share_of"]


def area_row(frame: pd.DataFrame, area: Area) -> pd.Series | None:
    """A county or tract row of a ``level``/``geoid`` table (``None`` for the state)."""
    if area.level not in ("county", "tract"):
        return None
    hit = frame[(frame["level"] == area.level) & (frame["geoid"].astype(str) == area.geoid)]
    return None if hit.empty else hit.iloc[0]


def count_of(row: pd.Series | None, col: str) -> float | None:
    """A number from a row, or ``None`` when the row or value is missing."""
    if row is None or col not in row.index:
        return None
    v = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
    return None if pd.isna(v) else float(v)


def share_of(num: float | None, den: float | None, note: str = "") -> Measure:
    """``num / den`` as a :class:`Measure`, or no value when either is missing or ``den`` is 0."""
    if num is None or den is None or den <= 0:
        return Measure(None)
    return Measure(num / den, None, note)
