"""Baseline settings read from the Assumption Ledger, in one small object.

The baseline functions take a ``BaselineSettings`` instead of reading the ledger
themselves, so they stay pure and a test can pass its own settings. Every field
names the ledger entry it comes from; nothing here is a number written in code.
"""

from __future__ import annotations

from dataclasses import dataclass

from visionhousing.ledger import Ledger

CYCLES: tuple[str, ...] = ("weak", "typical", "strong")
"""The three housing-cycle choices a user can make."""

INTERVAL: tuple[float, float] = (0.1, 0.9)
"""P10 and P90: every likely range in the tool is an 80% interval. This is the
tool's definition of a range, not a modelling assumption."""


@dataclass(frozen=True)
class BaselineSettings:
    """What the baseline needs from the ledger.

    Attributes:
        first_year: First projected year (``baseline.first_year``).
        horizon: Years projected (``baseline.horizon_years``).
        recent_window: Years of recent permits used to sort counties into
            size classes (``baseline.recent_window_years``).
        min_history: Years of history every backtest origin needs
            (``baseline.long_window_years``).
        n_classes: Number of county size classes (``baseline.size_classes``).
        tolerance: How close to the best a simpler rule must be
            (``baseline.selection_tolerance``).
        cycle_quantiles: Error quantile for each cycle choice
            (``baseline.cycle_quantile_weak`` / ``_typical`` / ``_strong``).
    """

    first_year: int
    horizon: int
    recent_window: int
    min_history: int
    n_classes: int
    tolerance: float
    cycle_quantiles: dict[str, float]

    def __post_init__(self) -> None:
        if self.horizon < 1 or self.recent_window < 1 or self.min_history < 1:
            raise ValueError("horizon, recent window and minimum history must be at least 1")
        if self.n_classes < 1:
            raise ValueError("need at least one size class")
        for cycle, q in self.cycle_quantiles.items():
            if not INTERVAL[0] <= q <= INTERVAL[1]:
                raise ValueError(
                    f"cycle '{cycle}' quantile {q} must lie between the interval's P10 and P90"
                )

    def assumption_ids(self, cycle: str) -> tuple[str, ...]:
        """Return the ledger ids behind a baseline run with this cycle choice."""
        return (
            "baseline.first_year",
            "baseline.horizon_years",
            "baseline.recent_window_years",
            "baseline.long_window_years",
            "baseline.size_classes",
            f"baseline.cycle_quantile_{cycle}",
        )


def settings_from_ledger(ledger: Ledger) -> BaselineSettings:
    """Read the baseline settings from the ledger's central values.

    Args:
        ledger: The Assumption Ledger.

    Returns:
        The settings.
    """
    return BaselineSettings(
        first_year=round(ledger["baseline.first_year"].central),
        horizon=round(ledger["baseline.horizon_years"].central),
        recent_window=round(ledger["baseline.recent_window_years"].central),
        min_history=round(ledger["baseline.long_window_years"].central),
        n_classes=round(ledger["baseline.size_classes"].central),
        tolerance=ledger["baseline.selection_tolerance"].central,
        cycle_quantiles={c: ledger[f"baseline.cycle_quantile_{c}"].central for c in CYCLES},
    )
