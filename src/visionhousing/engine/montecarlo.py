"""Monte Carlo helpers: baseline draws and summaries of draws.

Plain-language summary
----------------------
The engine reports every number as a likely range. It gets there by running the
model many times (the *draws*, 2,000 by default), each time with a different
plausible value of every uncertain input, and reading the 10th, 50th and 90th
percentiles of the results.

**Baseline uncertainty is carried through.** The baseline projection gives a
likely range for the state's permits (P10 / P50 / P90), from the projection
rule's own past errors on state totals. In each draw the engine picks one
position in that range, the same for every county (a statewide housing cycle:
in a weak year most counties are weak together), and scales each county's P50
by it. The reforms that act on baseline production (fees, parking) use that
same scale in the same draw, so "baseline" and "added by the reform" move
together, as they would in reality.

The position is a seeded number ``u`` between 0 and 1, from its own random
stream (``BASELINE_STREAM``), so it never changes any ledger parameter's draws.
The positions are *stratified*: with ``n`` draws, exactly one falls in each
slice ``[i/n, (i+1)/n)``, in a seeded random order. So the baseline's draws
reproduce its P10 / P50 / P90 almost exactly, and a reform that adds units
can never show a total below the baseline's P50 by sampling noise.

``u`` is turned into a scale by straight lines through the points
``(0.1, P10/P50)``, ``(0.5, 1)`` and ``(0.9, P90/P50)``, extended with the same
slopes to ``u = 0`` and ``u = 1`` and never below 0. So about 10% of draws
fall below the baseline's P10 and about 10% above its P90.
"""

from __future__ import annotations

import numpy as np

from visionhousing.engine.records import Record
from visionhousing.ledger import stream

__all__ = [
    "BASELINE_LABEL",
    "BASELINE_STREAM",
    "baseline_positions",
    "baseline_scale",
    "scale_at",
    "spread_ratios",
]

BASELINE_STREAM = "engine.baseline_cycle_position"
"""Name of the random stream behind the baseline's position in its range."""

BASELINE_LABEL = "Baseline housing cycle (permits at the low or high end of their likely range)"
"""Plain label of the baseline's own uncertainty, used in sensitivity tables."""


def spread_ratios(state: Record) -> tuple[float, float]:
    """Return the baseline's ``P10 / P50`` and ``P90 / P50``.

    Args:
        state: The baseline's state record (a range).

    Returns:
        ``(low ratio, high ratio)``; ``(1, 1)`` when the P50 is zero.
    """
    if not state.is_range:
        raise ValueError("the baseline record must be a range")
    p10, p50, p90 = float(state.p10), float(state.p50), float(state.p90)  # type: ignore[arg-type]
    if p50 <= 0:
        return 1.0, 1.0
    return p10 / p50, p90 / p50


def scale_at(u: np.ndarray | float, low_ratio: float, high_ratio: float) -> np.ndarray:
    """Turn positions ``u`` (0 to 1) into baseline multipliers.

    Args:
        u: Position(s) in the range.
        low_ratio: ``P10 / P50``.
        high_ratio: ``P90 / P50``.

    Returns:
        Multipliers, same shape as ``u``, never below 0. ``u = 0.5`` gives 1,
        ``u = 0.1`` gives ``low_ratio``, ``u = 0.9`` gives ``high_ratio``.
    """
    pos = np.asarray(u, dtype=float)
    below = 1.0 + (pos - 0.5) * (1.0 - low_ratio) / 0.4
    above = 1.0 + (pos - 0.5) * (high_ratio - 1.0) / 0.4
    return np.clip(np.where(pos < 0.5, below, above), 0.0, None)


def baseline_positions(n: int, seed: int) -> np.ndarray:
    """Return ``n`` seeded, stratified positions in the baseline range (0 to 1).

    Position ``i`` of the shuffled order is ``(slot + jitter) / n``: one per
    slice of width ``1 / n``.

    Args:
        n: Number of draws.
        seed: The run's seed.

    Returns:
        Array of shape ``(n,)``.
    """
    rng = stream(seed, BASELINE_STREAM)
    slots = rng.permutation(n)
    return (slots + rng.random(n)) / n


def baseline_scale(state: Record, n_counties: int, n: int, seed: int) -> np.ndarray:
    """Return the baseline multiplier per county and draw.

    Args:
        state: The baseline's state record.
        n_counties: Number of counties.
        n: Number of draws.
        seed: The run's seed.

    Returns:
        Array of shape ``(n_counties, n)``; every county shares the draw's value.
    """
    low, high = spread_ratios(state)
    scale = scale_at(baseline_positions(n, seed), low, high)
    return np.broadcast_to(scale, (n_counties, n)).copy()
