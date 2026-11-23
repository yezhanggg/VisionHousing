"""The candidate baseline rules: simple ways to carry recent permits forward.

Each rule looks only at permits up to an *origin* year and gives the units it
expects in each later target year. Four rules are compared:

``last_5_mean``
    The average of the last five years. Every target year gets that average.
``last_10_mean``
    The same with ten years, which spans most of a housing cycle.
``ewm``
    An exponentially weighted mean of all years up to the origin. The latest
    year has weight ``alpha``, the year before ``alpha x (1 - alpha)``, and so
    on; the weights are rescaled to add to one over the years with data.
``damped_trend``
    A straight line fitted to the last ten years. The projection is the
    window's average plus a share (the damping) of the fitted slope times the
    distance in years from the window's middle year. Damping 0 is a flat
    average; damping 1 carries the full line forward. A projection never goes
    below zero.

Rules work on many series at once: an array of shape (series, years) with
``NaN`` for years not reported. Missing years are skipped, not treated as zero.
A series with no data in the window gets zero and is flagged so the caller can
say why.

The window lengths, ``alpha`` and the damping come from the Assumption Ledger
(``rules_from_ledger``); none is written in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from visionhousing.ledger import Ledger

Method = Literal["mean", "ewm", "damped_trend"]


@dataclass(frozen=True)
class Rule:
    """One candidate projection rule.

    Attributes:
        name: Short id, for example ``last_5_mean``.
        method: ``mean``, ``ewm`` or ``damped_trend``.
        complexity: Rank for "simplest": 1 for plain means, 2 for the weighted
            mean, 3 for the trend. Lower is simpler.
        window: Years looked back (means and trend); ``None`` for ``ewm``.
        alpha: Weight on the latest year (``ewm`` only).
        damping: Share of the trend carried forward (``damped_trend`` only).
        assumption_ids: Ledger ids the rule's settings come from.
    """

    name: str
    method: Method
    complexity: int
    window: int | None = None
    alpha: float | None = None
    damping: float | None = None
    assumption_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.method in ("mean", "damped_trend") and (self.window is None or self.window < 1):
            raise ValueError(f"rule '{self.name}' needs a window of at least one year")
        if self.method == "ewm" and (self.alpha is None or not 0 < self.alpha <= 1):
            raise ValueError(f"rule '{self.name}' needs 0 < alpha <= 1")
        if self.method == "damped_trend" and (self.damping is None or not 0 <= self.damping <= 1):
            raise ValueError(f"rule '{self.name}' needs 0 <= damping <= 1")

    def describe(self, origin: int) -> str:
        """Say in words what the rule does at this origin year.

        Args:
            origin: Last year of history the rule may use.

        Returns:
            A short phrase, for example "mean of 2021-2025 permits".
        """
        if self.method == "mean":
            return f"mean of {origin - self.window + 1}-{origin} permits"
        if self.method == "ewm":
            return (
                f"exponentially weighted mean of permits up to {origin} "
                f"(weight {self.alpha:g} on {origin}, each earlier year x {1 - self.alpha:g})"
            )
        return (
            f"mean of {origin - self.window + 1}-{origin} permits plus {self.damping:g} x the "
            "fitted yearly slope x years from the window's middle year, floored at zero"
        )


def rules_from_ledger(ledger: Ledger) -> tuple[Rule, ...]:
    """Build the four candidate rules from their ledger settings.

    Args:
        ledger: The Assumption Ledger.

    Returns:
        ``last_5_mean`` (``baseline.recent_window_years``), ``last_10_mean``
        (``baseline.long_window_years``), ``ewm`` (``baseline.ewm_alpha``) and
        ``damped_trend`` (long window, ``baseline.trend_damping``).
    """
    short = round(ledger["baseline.recent_window_years"].central)
    long = round(ledger["baseline.long_window_years"].central)
    alpha = ledger["baseline.ewm_alpha"].central
    damping = ledger["baseline.trend_damping"].central
    return (
        Rule(
            f"last_{short}_mean",
            "mean",
            1,
            window=short,
            assumption_ids=("baseline.recent_window_years",),
        ),
        Rule(
            f"last_{long}_mean",
            "mean",
            1,
            window=long,
            assumption_ids=("baseline.long_window_years",),
        ),
        Rule("ewm", "ewm", 2, alpha=alpha, assumption_ids=("baseline.ewm_alpha",)),
        Rule(
            "damped_trend",
            "damped_trend",
            3,
            window=long,
            damping=damping,
            assumption_ids=("baseline.long_window_years", "baseline.trend_damping"),
        ),
    )


def project(
    rule: Rule, years: np.ndarray, values: np.ndarray, origin: int, targets: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Project each series to the target years from history up to the origin.

    Args:
        rule: The rule to apply.
        years: Calendar years of ``values``' columns, shape (Y,).
        values: Units by series and year, shape (S, Y); ``NaN`` = not reported.
        origin: Last year the rule may use. Later columns are ignored.
        targets: Target years, shape (H,), all after the origin.

    Returns:
        ``(projection, has_history)``: projection of shape (S, H), never
        negative; and a boolean (S,) that is false where the series had no data
        in the rule's window (its projection is then zero).
    """
    years = np.asarray(years)
    values = np.asarray(values, dtype=float)
    targets = np.asarray(targets)
    if values.ndim != 2 or values.shape[1] != years.size:
        raise ValueError("values must have shape (series, years)")
    if targets.size and targets.min() <= origin:
        raise ValueError("target years must come after the origin")
    if rule.method == "ewm":
        in_window = years <= origin
    else:
        in_window = (years <= origin) & (years > origin - rule.window)
    window_years = years[in_window].astype(float)
    window_values = values[:, in_window]
    observed = ~np.isnan(window_values)
    count = observed.sum(axis=1)
    has_history = count > 0
    filled = np.where(observed, window_values, 0.0)

    if rule.method == "ewm":
        weights = rule.alpha * (1 - rule.alpha) ** (origin - window_years)
        w = np.where(observed, weights[np.newaxis, :], 0.0)
        level = _safe_divide((w * filled).sum(axis=1), w.sum(axis=1))
        projection = np.repeat(level[:, np.newaxis], targets.size, axis=1)
    else:
        level = _safe_divide(filled.sum(axis=1), count)
        projection = np.repeat(level[:, np.newaxis], targets.size, axis=1)
        if rule.method == "damped_trend":
            t = np.where(observed, window_years[np.newaxis, :], 0.0)
            t_mean = _safe_divide(t.sum(axis=1), count)
            dt = np.where(observed, window_years[np.newaxis, :] - t_mean[:, np.newaxis], 0.0)
            dy = np.where(observed, filled - level[:, np.newaxis], 0.0)
            slope = _safe_divide((dt * dy).sum(axis=1), (dt * dt).sum(axis=1))
            distance = targets[np.newaxis, :] - t_mean[:, np.newaxis]
            projection = projection + rule.damping * slope[:, np.newaxis] * distance
    projection = np.where(has_history[:, np.newaxis], np.maximum(projection, 0.0), 0.0)
    return projection, has_history


def _safe_divide(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """Divide, giving zero where the denominator is zero."""
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    out = np.zeros_like(num)
    np.divide(num, den, out=out, where=den != 0)
    return out
