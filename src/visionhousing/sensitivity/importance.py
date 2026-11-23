"""Rank-correlation importance: which inputs move the answer when all vary at once.

Plain-language summary
----------------------
The tornado moves one input at a time. In the Monte Carlo run every input moves
together, so this check asks the same question under joint variation: across
the 2,000 draws, how closely does the answer follow each input?

The measure is **Spearman's rank correlation** (rho). Sort the draws by the
input and give each its rank (1, 2, 3, ...); do the same for the answer; rho is
the ordinary correlation of the two lists of ranks. It runs from -1 to +1:

- +1: the answer always goes up when the input goes up (in any shape of curve),
- 0: no steady link,
- -1: the answer always goes down when the input goes up.

Inputs are ranked by the size of rho (ignoring the sign). Ties share the mean
of their ranks. An input that does not vary (a fixed value) has no rho and is
left out. Written in numpy only.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

__all__ = ["importance", "rankdata", "spearman"]


def rankdata(values: np.ndarray) -> np.ndarray:
    """Ranks 1..n of ``values``; tied values share the mean of their ranks.

    Args:
        values: One-dimensional array.

    Returns:
        Float ranks, same length.
    """
    x = np.asarray(values, dtype=float)
    if x.ndim != 1:
        raise ValueError("values must be one-dimensional")
    _, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    mean_rank = ends - (counts - 1) / 2.0
    return mean_rank[inverse]


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman's rank correlation of two equal-length samples.

    Args:
        x: First sample.
        y: Second sample.

    Returns:
        rho between -1 and 1; ``nan`` if either sample does not vary.
    """
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("x and y must be one-dimensional and the same length")
    ra, rb = rankdata(a), rankdata(b)
    ra, rb = ra - ra.mean(), rb - rb.mean()
    denom = float(np.sqrt((ra * ra).sum() * (rb * rb).sum()))
    if denom == 0.0:
        return float("nan")
    return float(np.clip((ra * rb).sum() / denom, -1.0, 1.0))


def importance(
    inputs: Mapping[str, np.ndarray],
    output: np.ndarray,
    labels: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Rank inputs by the size of their rank correlation with the output.

    Args:
        inputs: Input id to its draws (same length as ``output``).
        output: The answer in each draw.
        labels: Input id to a plain label (defaults to the id).

    Returns:
        Columns ``parameter``, ``label``, ``rho``, ``abs_rho``, ``rank``
        (1-based), largest ``abs_rho`` first. Inputs that do not vary are left out.
    """
    labels = labels or {}
    y = np.asarray(output, dtype=float)
    rows = []
    for pid in sorted(inputs):
        rho = spearman(np.asarray(inputs[pid], dtype=float), y)
        if np.isnan(rho):
            continue
        rows.append(
            {"parameter": pid, "label": labels.get(pid, pid), "rho": rho, "abs_rho": abs(rho)}
        )
    frame = pd.DataFrame(rows, columns=["parameter", "label", "rho", "abs_rho"])
    frame = frame.sort_values(["abs_rho", "parameter"], ascending=[False, True], kind="mergesort")
    frame = frame.reset_index(drop=True)
    frame["rank"] = range(1, len(frame) + 1)
    return frame
