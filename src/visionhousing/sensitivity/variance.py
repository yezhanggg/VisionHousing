"""What would narrow the range: each input's share of the answer's variance.

Plain-language summary
----------------------
The tornado asks "how far does the answer move if this input goes low or
high?". This module asks a different question: "how much of the uncertainty in
the answer comes from this input?". If an input were known exactly, the part of
the spread it causes would disappear; that part is the input's **first-order
variance share** (also called the first-order sensitivity index).

It is estimated from the run's own stored draws, with no new run and no model
fitting:

1. Sort the draws by the input X and cut them into equal-count bins
   (``sensitivity.variance_bins``, ten bins of 200 draws for 2,000 draws).
2. In each bin, compute the variance of the answer Y; average over the bins.
   Inside one bin X hardly moves, so what is left is the spread from
   everything else.
3. Share explained = 1 - (average within-bin variance / total variance of Y).

A share of 0.4 means knowing X would remove about 40% of the variance. The
likely range (P90 - P10) scales with the standard deviation, the square root
of the variance, so it would shrink by about 1 - sqrt(1 - 0.4) = 23%. That
shrink is what the research-agenda sentence reports.

The shares of all inputs do not add up to one: the **leftover** (one minus
their sum, never below zero) is reported as "interactions and noise". It holds
the parts of the spread that come from inputs acting together, and the small
bias of the bin estimate. With few draws per bin an input that does not matter
scores a little above zero (about bins / draws), never exactly zero.

A permutation check (shuffle one input, rerun) would need one rerun of the
reform per input, so it is not done here; the rank-correlation table of
``sensitivity.importance`` is the cross-check on the same draws.

Written in numpy only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record

__all__ = [
    "AGENDA_ID",
    "BINS_ID",
    "Narrowing",
    "Share",
    "first_order_share",
    "first_order_shares",
    "narrowing",
    "range_shrink",
]

BINS_ID = "sensitivity.variance_bins"
"""Ledger id of the number of equal-count bins."""
AGENDA_ID = "explain.agenda_inputs"
"""Ledger id of how many inputs the research agenda names."""


def first_order_share(x: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """First-order variance share of ``y`` explained by ``x`` (equal-count bins).

    Args:
        x: The input's draws.
        y: The answer in the same draws.
        bins: Number of equal-count bins (at least 2, at most the number of draws).

    Returns:
        ``1 - mean within-bin variance / total variance``, between 0 and 1;
        ``nan`` when ``y`` or ``x`` does not vary.
    """
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if xa.shape != ya.shape or xa.ndim != 1:
        raise ValueError("x and y must be one-dimensional and the same length")
    if not 2 <= bins <= xa.size:
        raise ValueError("bins must be between 2 and the number of draws")
    total = float(np.var(ya))
    if total <= 0 or float(np.ptp(xa)) == 0:
        return float("nan")
    order = np.argsort(xa, kind="stable")
    within = [float(np.var(ya[part])) for part in np.array_split(order, bins)]
    return float(np.clip(1.0 - np.mean(within) / total, 0.0, 1.0))


def first_order_shares(
    inputs: Mapping[str, np.ndarray], output: np.ndarray, bins: int = 10
) -> dict[str, float]:
    """First-order share of every input that varies (largest first, ties by id)."""
    out = {}
    for pid in sorted(inputs):
        s = first_order_share(inputs[pid], output, bins)
        if not np.isnan(s):
            out[pid] = s
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def range_shrink(share: float) -> float:
    """How much the likely range shrinks if an input explaining ``share`` were known.

    The range scales with the standard deviation, so it falls by
    ``1 - sqrt(1 - share)``.
    """
    return float(1.0 - np.sqrt(max(0.0, 1.0 - float(share))))


@dataclass(frozen=True)
class Share:
    """One input's part of the answer's uncertainty.

    Attributes:
        rank: 1 for the largest share.
        parameter: Ledger id (or the baseline-cycle stream).
        label: Plain label.
        share: First-order variance share (record, unit ``share``).
        shrink: Likely-range shrink if the input were known (record, unit ``share``).
    """

    rank: int
    parameter: str
    label: str
    share: Record
    shrink: Record


@dataclass(frozen=True)
class Narrowing:
    """Every input's variance share for one reform's state total.

    Attributes:
        reform: Reform key.
        bins: Bins used.
        draws: Draws used.
        items: The inputs, largest share first.
        leftover: One minus the sum of the shares, at least 0 ("interactions and noise").
        agenda: How many inputs the research agenda names.
    """

    reform: str
    bins: int
    draws: int
    items: tuple[Share, ...]
    leftover: Record
    agenda: int


def narrowing(
    inputs: Mapping[str, np.ndarray],
    output: np.ndarray,
    labels: Mapping[str, str],
    *,
    reform: str,
    what: str,
    bins: int,
    agenda: int,
    ids: Mapping[str, tuple[str, ...]] | None = None,
) -> Narrowing:
    """Variance shares as records, with the leftover.

    Args:
        inputs: Input id to its draws.
        output: The answer in the same draws.
        labels: Input id to a plain label.
        reform: Reform key.
        what: The answer, in words (for the formulas).
        bins: Number of equal-count bins.
        agenda: How many inputs the research agenda names.
        ids: Input id to the ledger ids behind it (defaults to the id itself).

    Returns:
        The shares, largest first.
    """
    y = np.asarray(output, dtype=float)
    n = y.size
    shares = first_order_shares(inputs, y, bins)
    ids = ids or {}
    items = []
    for rank, (pid, s) in enumerate(shares.items(), start=1):
        behind = (*ids.get(pid, (pid,)), BINS_ID)
        share = Record(
            name=f"share of the variance of {what} explained by {labels.get(pid, pid)}",
            value=s,
            unit="share",
            formula=(
                f"1 {rec.MINUS} mean variance of {what} within {bins} equal-count bins of the "
                f"{n:,} draws sorted by {labels.get(pid, pid)} {rec.DIVIDE} total variance = "
                f"{rec.format_number(s)}"
            ),
            assumption_ids=tuple(sorted(set(behind))),
        )
        shrink_v = range_shrink(s)
        shrink = Record(
            name=f"likely-range shrink if {labels.get(pid, pid)} were known",
            value=shrink_v,
            unit="share",
            formula=(
                f"1 {rec.MINUS} square root of (1 {rec.MINUS} variance share "
                f"{rec.format_number(s)}) = {rec.format_number(shrink_v)}; the likely range "
                "scales with the standard deviation"
            ),
            assumption_ids=tuple(sorted(set(behind))),
        )
        items.append(Share(rank, pid, labels.get(pid, pid), share, shrink))
    total = float(sum(shares.values()))
    rest = max(0.0, 1.0 - total)
    leftover = Record(
        name=f"share of the variance of {what} from interactions and noise",
        value=rest,
        unit="share",
        formula=(
            f"1 {rec.MINUS} sum of the {len(shares)} first-order shares "
            f"({rec.format_number(total)}), at least 0 = {rec.format_number(rest)}"
        ),
        assumption_ids=(BINS_ID,),
    )
    return Narrowing(reform, bins, n, tuple(items), leftover, agenda)
