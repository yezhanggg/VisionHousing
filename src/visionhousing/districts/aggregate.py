"""Add up engine draws into legislative districts, draw by draw, then take percentiles.

Plain-language summary
----------------------
The engine gives every result as many **draws** (one possible future each).
A district's result is made the same way as a county's:

1. In every draw, each tract's (or county's) added homes are shared out to
   districts by the weights of :mod:`visionhousing.districts.weights`:
   ``district[d, k] = sum over tracts t of weight(t, d) x tract[t, k]``.
2. Only then are the district's draws summarised as P10 / P50 / P90 (the 10th,
   50th and 90th percentiles over draws).

Percentiles are never weighted or added: the P50 of a sum is not the sum of
P50s. Because every tract's weights add up to 1 (each tract's stored weights
are divided by their sum here, so four-decimal rounding cannot leak homes),
the districts add up to the state total in every draw.

:func:`district_draws` is pure: arrays and a weight table in, arrays out. The
engine and the interface call it with the engine's stored draws
(``ReformResult.added`` for a tract-level reform, ``ReformOutcome.county_draws``
for a county-level one, ``Result.baseline_county_draws`` for the baseline);
:func:`visionhousing.districts.results.district_results` does that for a run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["DistrictWeights", "district_draws", "percentiles"]


@dataclass(frozen=True)
class DistrictWeights:
    """One chamber's weights at one level (tract or county), ready for arithmetic.

    Attributes:
        chamber: ``"upper"`` or ``"lower"``.
        level: ``"tract"`` or ``"county"``.
        districts: District codes, in output row order.
        geo: Tract or county id of each weight row.
        row: Index into ``districts`` of each weight row.
        weight: Each row's weight, divided by its geography's total so every
            geography's weights add up to 1.
    """

    chamber: str
    level: str
    districts: tuple[str, ...]
    geo: tuple[str, ...]
    row: np.ndarray
    weight: np.ndarray

    @classmethod
    def from_table(
        cls,
        table: pd.DataFrame,
        chamber: str,
        level: str,
        districts: Sequence[str] | None = None,
    ) -> DistrictWeights:
        """Read one chamber from a weight table (``chamber, district, {level}, weight``).

        Args:
            table: A ``district_tract_weights`` or ``district_county_weights`` table.
            chamber: The chamber to keep.
            level: ``"tract"`` or ``"county"`` (the id column).
            districts: Output order; default the sorted district codes in the table.

        Raises:
            ValueError: A negative weight, or a geography whose weights add up to zero.
        """
        t = table[table["chamber"] == chamber]
        ids = tuple(districts) if districts is not None else tuple(sorted(set(t["district"])))
        index = {d: i for i, d in enumerate(ids)}
        unknown = sorted(set(t["district"]) - set(index))
        if unknown:
            raise ValueError(f"weights name districts not in the list: {unknown[:3]}")
        w = t["weight"].to_numpy(dtype=float)
        if (w < 0).any():
            raise ValueError("weights must not be negative")
        geo = t[level].astype(str).to_numpy()
        totals = pd.Series(w).groupby(geo).transform("sum").to_numpy()
        if (totals <= 0).any():
            raise ValueError(f"a {level}'s weights add up to zero")
        return cls(
            chamber=chamber,
            level=level,
            districts=ids,
            geo=tuple(geo),
            row=np.array([index[d] for d in t["district"]], dtype=np.int64),
            weight=w / totals,
        )


def district_draws(
    values: np.ndarray, geoids: Sequence[str], weights: DistrictWeights
) -> np.ndarray:
    """Share per-geography draws out to districts, draw by draw.

    Args:
        values: Draws by geography, shape ``(len(geoids), draws)``.
        geoids: Tract or county id of each row of ``values``.
        weights: The chamber's weights at the same level.

    Returns:
        District draws, shape ``(len(weights.districts), draws)``. Summed over
        districts, each draw equals ``values`` summed over geographies.

    Raises:
        ValueError: A geography with non-zero values has no weights (its homes
            would be lost), or the shapes disagree.
    """
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 2 or arr.shape[0] != len(geoids):
        raise ValueError("values must have shape (len(geoids), draws)")
    position = {str(g): i for i, g in enumerate(geoids)}
    covered = set(weights.geo)
    lost = [g for g, i in position.items() if g not in covered and np.any(arr[i] != 0)]
    if lost:
        raise ValueError(
            f"{len(lost)} {weights.level}s have results but no weights (first {lost[0]})"
        )
    keep = np.array([g in position for g in weights.geo], dtype=bool)
    src = np.array(
        [position[g] for g, k in zip(weights.geo, keep, strict=True) if k], dtype=np.int64
    )
    out = np.zeros((len(weights.districts), arr.shape[1]))
    if src.size:
        np.add.at(out, weights.row[keep], weights.weight[keep][:, None] * arr[src])
    return out


def percentiles(draws: np.ndarray) -> np.ndarray:
    """P10, P50 and P90 of each row over draws, shape ``(rows, 3)``."""
    arr = np.asarray(draws, dtype=float)
    if arr.shape[1] == 0:
        return np.zeros((arr.shape[0], 3))
    return np.percentile(arr, [10, 50, 90], axis=1).T
