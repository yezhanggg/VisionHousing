"""Small helpers shared by the comparisons: records, county tables, plain numbers.

Every number a comparison shows is a record (``engine.records``): its P10 /
P50 / P90 over the run's draws, the formula in words, and the ledger and
comparison ids behind it. County results are compact tables (one formula and
one id list for the table, then P10 / P50 / P90 per county), which keeps the
payload small enough for the static site.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record

__all__ = ["SPAN", "county_table", "homes", "record_dict", "share", "state_record"]

SPAN = "2027-2031"
"""The five years every headline counts."""


def state_record(
    name: str,
    draws: np.ndarray,
    expression: str,
    ids: Iterable[str],
    *,
    sources: Iterable[str] = (),
    unit: str = "units",
    tier: int | None = None,
) -> Record:
    """A state range record from per-draw values."""
    return rec.from_draws(
        name,
        np.asarray(draws, dtype=float),
        unit,
        expression=expression,
        assumption_ids=sorted(set(ids)),
        source_ids=sorted(set(sources)),
        tier=tier,
    )


def county_table(
    name: str,
    draws: np.ndarray,
    county_ids: Sequence[str],
    expression: str,
    ids: Iterable[str],
    *,
    unit: str = "units",
) -> dict[str, Any]:
    """P10 / P50 / P90 per county of a ``(counties, draws)`` array, as one compact table."""
    arr = np.asarray(draws, dtype=float)
    p10, p50, p90 = np.percentile(arr, [10, 50, 90], axis=1)
    return {
        "name": name,
        "unit": unit,
        "formula": f"P10, P50, P90 of {arr.shape[1]:,} seeded draws, per county, of {expression}",
        "assumption_ids": sorted(set(ids)),
        "columns": ["county", "p10", "p50", "p90"],
        "rows": [
            [str(c), float(a), float(b), float(d)]
            for c, a, b, d in zip(county_ids, p10, p50, p90, strict=True)
        ],
    }


def record_dict(record: Record) -> dict[str, Any]:
    """A record as plain JSON-ready data."""
    return record.model_dump()


def homes(value: float) -> str:
    """A count of homes in words: rounded to a whole home, with commas (``1,234``)."""
    v = float(value)
    return f"{v:,.0f}" if abs(v) >= 0.5 else "0"


def share(value: float) -> str:
    """A share as a percent: ``0.031%``, ``4.2%``, ``35%``."""
    v = 100.0 * float(value)
    if v == 0:
        return "0%"
    if abs(v) < 1:
        return f"{v:.2g}%"  # two significant digits, so a small rate never reads as 0.0%
    return f"{v:.1f}%" if abs(v) < 10 else f"{v:.0f}%"
