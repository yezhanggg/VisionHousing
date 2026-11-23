"""Tornado: swing one input at a time from its low to its high value.

Plain-language summary
----------------------
A tornado chart answers "which single assumption moves this answer most?".

1. Pin **every** uncertain ledger parameter at its central value and work out
   the answer once (the *central total*).
2. For one parameter at a time, set it to its low value, then its high value,
   keeping all the others at central, and work out the answer each time.
3. The *swing* is the distance between the two answers. Sort the parameters by
   swing, largest first. Drawn as horizontal bars, the longest bar is at the
   top, so the chart looks like a tornado.

Because every input is pinned, no random numbers are involved: the tornado is
the same for every seed. Parameters with a fixed value (no range, for example
the Challenge's $50,000 benchmark fee) cannot swing and are left out.

The functions are pure. The answer is any function from a ledger to a number,
so the same code serves every reform.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import pandas as pd

from visionhousing.ledger import Ledger, apply_overrides

__all__ = ["COLUMNS", "Swing", "pin_central", "swing_frame", "tornado"]

COLUMNS = [
    "parameter",
    "label",
    "unit",
    "low",
    "central",
    "high",
    "total_low",
    "total_central",
    "total_high",
    "swing",
]
"""Columns of a tornado table."""


@dataclass(frozen=True)
class Swing:
    """One row of a tornado table.

    Attributes:
        parameter: Ledger id (or an engine input such as the baseline cycle).
        label: Plain label.
        unit: Unit of the input.
        low: Low value of the input.
        central: Central value.
        high: High value.
        total_low: The answer with the input at its low value.
        total_central: The answer with every input at central.
        total_high: The answer with the input at its high value.
    """

    parameter: str
    label: str
    unit: str
    low: float
    central: float
    high: float
    total_low: float
    total_central: float
    total_high: float

    @property
    def swing(self) -> float:
        """Distance between the answers at the low and the high value."""
        return abs(self.total_high - self.total_low)


def pin_central(ledger: Ledger) -> Ledger:
    """Return the ledger with every parameter fixed at its central value.

    Args:
        ledger: The ledger.

    Returns:
        A new ledger in which every entry has shape ``fixed``.
    """
    return apply_overrides(
        ledger, {pid: "central" for pid, p in ledger.items() if p.shape != "fixed"}
    )


def swing_frame(rows: Iterable[Swing]) -> pd.DataFrame:
    """Turn swings into a table sorted by swing, largest first (ties by id).

    Args:
        rows: The swings.

    Returns:
        A frame with :data:`COLUMNS` and a 1-based ``rank``.
    """
    data = [{**{c: getattr(r, c) for c in COLUMNS if c != "swing"}, "swing": r.swing} for r in rows]
    frame = pd.DataFrame(data, columns=COLUMNS)
    frame = frame.sort_values(["swing", "parameter"], ascending=[False, True], kind="mergesort")
    frame = frame.reset_index(drop=True)
    frame["rank"] = range(1, len(frame) + 1)
    return frame


def tornado(
    evaluate: Callable[[Ledger], float],
    ledger: Ledger,
    ids: Sequence[str],
    *,
    extra: Iterable[Swing] = (),
) -> pd.DataFrame:
    """Swing each parameter low -> high with the others at central, and rank them.

    Args:
        evaluate: The answer (for example a reform's state total) for a ledger
            whose entries are all fixed.
        ledger: The ledger the run used (its low, central and high values).
        ids: Parameters to swing; fixed ones and unknown ids are skipped.
        extra: Rows worked out elsewhere (for example the baseline's own range),
            added to the table before ranking.

    Returns:
        The tornado table (see :func:`swing_frame`).
    """
    central = pin_central(ledger)
    total_central = float(evaluate(central))
    rows = list(extra)
    for pid in sorted(set(ids)):
        if pid not in ledger or ledger[pid].shape == "fixed":
            continue
        p = ledger[pid]
        low = float(evaluate(apply_overrides(central, {pid: p.low})))
        high = float(evaluate(apply_overrides(central, {pid: p.high})))
        rows.append(Swing(pid, p.label, p.unit, p.low, p.central, p.high, low, total_central, high))
    return swing_frame(rows)
