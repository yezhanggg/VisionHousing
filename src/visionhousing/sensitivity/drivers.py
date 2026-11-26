"""What drives each reform's answer: the tornado and the importance ranking for one run.

Plain-language summary
----------------------
:func:`drivers` takes a finished engine result and one reform in it, and
returns:

- the **tornado** table: every uncertain input the reform's headline number
  depends on, swung from low to high with all others at central, plus one row
  for the baseline's own range (fees and parking scale with baseline
  production; ADUs do not), ranked by the change in the state total;
- the **importance** table: Spearman rank correlation between each input's
  2,000 draws and the reform's state total in the same draws;
- the **top five** drivers by the tornado, each with its plain label, its swing
  and its rank correlation, ready for the "what drives it" panel.

The input draws used for importance are exactly the ones the reform used: the
reform's ledger sampled with the run's seed (every parameter has its own
seeded stream), and the baseline's position in its range from the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from visionhousing.engine.core import Result, reform_total
from visionhousing.engine.montecarlo import (
    BASELINE_LABEL,
    BASELINE_STREAM,
    baseline_positions,
    spread_ratios,
)
from visionhousing.ledger import sample
from visionhousing.sensitivity.importance import importance
from visionhousing.sensitivity.tornado import Swing, pin_central, tornado
from visionhousing.sensitivity.variance import AGENDA_ID, BINS_ID, Narrowing, narrowing

__all__ = ["TOP", "Driver", "Drivers", "drivers", "input_draws", "uncertain_ids", "uncertainty"]

TOP = 5
"""How many drivers the summary lists."""


@dataclass(frozen=True)
class Driver:
    """One of a reform's top drivers.

    Attributes:
        rank: 1 for the largest swing.
        parameter: Ledger id, or ``engine.baseline_cycle_position``.
        label: Plain label.
        swing: Change in the state total from the input's low to its high value
            (units, 2027-2031), others at central.
        total_low: State total with the input at its low value.
        total_high: State total with the input at its high value.
        rho: Spearman rank correlation with the state total across the draws
            (``nan`` if not computed).
    """

    rank: int
    parameter: str
    label: str
    swing: float
    total_low: float
    total_high: float
    rho: float

    def sentence(self) -> str:
        """One plain sentence, for example for the reform page."""
        lo, hi = round(self.total_low), round(self.total_high)
        return (
            f"{self.label}: from its low to its high value, the five-year total moves "
            f"from about {lo:,} to {hi:,} units."
        )


@dataclass(frozen=True)
class Drivers:
    """Sensitivity of one reform in one run.

    Attributes:
        reform: Reform key.
        tornado: The tornado table (see ``sensitivity.tornado``).
        importance: The importance table (see ``sensitivity.importance``).
        top: The top drivers by swing, with their rank correlation.
    """

    reform: str
    tornado: pd.DataFrame
    importance: pd.DataFrame
    top: tuple[Driver, ...]

    def labels(self) -> list[str]:
        """Plain labels of the top drivers, in order."""
        return [d.label for d in self.top]


def uncertain_ids(result: Result, key: str) -> list[str]:
    """Ledger ids behind a reform's headline that have a range (not fixed).

    Args:
        result: An engine result.
        key: Reform key in the result.

    Returns:
        Sorted ids.
    """
    res = result.reforms[key].result
    if res is None or res.ledger is None:
        raise ValueError(f"reform '{key}' carries no ledger; rerun it with engine.run")
    ledger = res.ledger
    return sorted(i for i in res.assumption_ids if i in ledger and ledger[i].shape != "fixed")


def drivers(result: Result, key: str, top: int = TOP) -> Drivers:
    """Tornado, importance and the top drivers for one reform of a result.

    Args:
        result: An engine result (from ``engine.run``).
        key: A reform key in ``result.reforms``.
        top: How many drivers to list.

    Returns:
        The sensitivity tables and the top drivers.

    Raises:
        KeyError: The reform is not in the result.
    """
    if key not in result.reforms:
        raise KeyError(f"reform '{key}' is not in this result; it has {list(result.reforms)}")
    outcome = result.reforms[key]
    res = outcome.result
    ctx = result.run_context
    if res is None or res.ledger is None or ctx is None:
        raise ValueError("this result has no run context; rerun it with engine.run")
    ledger = res.ledger
    ids = uncertain_ids(result, key)
    options = dict(outcome.options)

    def evaluate(led, multiplier: float = 1.0) -> float:
        return reform_total(ctx, key, ledger=led, options=options, baseline_multiplier=multiplier)

    low_ratio, high_ratio = spread_ratios(result.baseline)
    central = pin_central(ledger)
    at_central = evaluate(central)
    base_row = Swing(
        parameter=BASELINE_STREAM,
        label=BASELINE_LABEL,
        unit="multiplier on baseline P50",
        low=low_ratio,
        central=1.0,
        high=high_ratio,
        total_low=evaluate(central, low_ratio),
        total_central=at_central,
        total_high=evaluate(central, high_ratio),
    )
    swings = tornado(evaluate, ledger, ids, extra=[base_row])

    draws = sample(ledger, n=result.draws, seed=result.seed, ids=ids)
    draws[BASELINE_STREAM] = baseline_positions(result.draws, result.seed)
    labels = {i: ledger[i].label for i in ids} | {BASELINE_STREAM: BASELINE_LABEL}
    imp = importance(draws, np.asarray(outcome.state_draws), labels)
    rho = dict(zip(imp["parameter"], imp["rho"], strict=True))

    rows = swings.head(top).itertuples(index=False)
    best = tuple(
        Driver(
            rank=int(r.rank),
            parameter=str(r.parameter),
            label=str(r.label),
            swing=float(r.swing),
            total_low=float(r.total_low),
            total_high=float(r.total_high),
            rho=float(rho.get(str(r.parameter), float("nan"))),
        )
        for r in rows
    )
    return Drivers(reform=key, tornado=swings, importance=imp, top=best)


def input_draws(result: Result, key: str) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    """The draws of every uncertain input behind a reform, exactly as the run used them.

    The reform's ledger sampled with the run's seed (each parameter has its own
    stream), plus the baseline's position in its range.

    Args:
        result: An engine result.
        key: Reform key in the result.

    Returns:
        ``(input id -> draws, input id -> plain label)``.
    """
    res = result.reforms[key].result
    if res is None or res.ledger is None:
        raise ValueError(f"reform '{key}' carries no ledger; rerun it with engine.run")
    ledger = res.ledger
    ids = uncertain_ids(result, key)
    draws = sample(ledger, n=result.draws, seed=result.seed, ids=ids)
    draws[BASELINE_STREAM] = baseline_positions(result.draws, result.seed)
    labels = {i: ledger[i].label for i in ids} | {BASELINE_STREAM: BASELINE_LABEL}
    return draws, labels


def uncertainty(result: Result, key: str) -> Narrowing:
    """What would narrow the range of one reform's state total (no new run).

    Each input's first-order variance share of the reform's state added units,
    from the run's own draws (see ``sensitivity.variance``). The number of bins
    and the length of the research agenda come from the run's ledger.

    Args:
        result: An engine result (from ``engine.run``).
        key: A reform key in ``result.reforms``.

    Returns:
        The shares, largest first, and the leftover.
    """
    if key not in result.reforms:
        raise KeyError(f"reform '{key}' is not in this result; it has {list(result.reforms)}")
    outcome = result.reforms[key]
    ledger = outcome.result.ledger if outcome.result is not None else None
    if ledger is None or result.draws_obj is None:
        raise ValueError("this result has no run context; rerun it with engine.run")
    run_ledger = result.draws_obj.ledger
    bins = int(run_ledger[BINS_ID].central) if BINS_ID in run_ledger else 10
    agenda = int(run_ledger[AGENDA_ID].central) if AGENDA_ID in run_ledger else 3
    bins = max(2, min(bins, result.draws))
    draws, labels = input_draws(result, key)
    return narrowing(
        draws,
        np.asarray(outcome.state_draws),
        labels,
        reform=key,
        what=f"added units, {outcome.title}, state",
        bins=bins,
        agenda=agenda,
        ids={BASELINE_STREAM: tuple(result.baseline.assumption_ids)},
    )
