"""Rolling-origin backtest: how well each rule would have done in past years.

The idea in one line: pretend it is an earlier year, apply the rule using only
the permits known then, and compare its five-year projection with what was
actually permitted.

* An **origin** is the last year of history the rule may use. The gap from the
  origin to the first projected year (the *lead*) is the same as in the real
  run: data end in 2025 and the projection starts in 2027, so the lead is two
  years and every backtest projects years ``origin + 2`` to ``origin + 6``.
* An origin is used only if every one of those target years has data, and if
  at least ``min_history`` years of history come before it, so every rule is
  scored on the same cases.
* Each case gives a **ratio**: actual five-year units / projected five-year
  units, for one county (all structure types added). A ratio of 1.2 means the
  rule was 20% too low.
* Counties are sorted into **size classes** (terciles by their average yearly
  permits over the recent window). Small counties have larger proportional
  errors, so each class has its own ratio distribution.

``validate_baseline`` returns the **skill table**: for each rule, the mean
absolute percentage error (MAPE), the median absolute log ratio (a symmetric
error: 0.10 means "typically about 10% off", whether high or low), and the
**coverage** of the 80% interval. Coverage is measured out of sample by
geography: each county's interval is built from the other counties' ratios in
its size class (all other counties if it is alone in its class). A well-judged
interval contains the actual total in about 80% of cases.

``choose_rule`` picks the simplest rule whose median absolute log ratio is
within the ledger tolerance of the best.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from visionhousing.baseline.panel import Panel, to_panel
from visionhousing.baseline.rules import Rule, project
from visionhousing.baseline.settings import INTERVAL, BaselineSettings


def lead_years(panel: Panel, settings: BaselineSettings) -> int:
    """Return the years from the last data year to the first projected year.

    Raises:
        ValueError: The data already reach the first projected year.
    """
    lead = settings.first_year - int(panel.years[-1])
    if lead < 1:
        raise ValueError(
            f"data run to {int(panel.years[-1])}, which is not before the first projected "
            f"year {settings.first_year}"
        )
    return lead


def backtest_origins(years: np.ndarray, lead: int, horizon: int, min_history: int) -> list[int]:
    """List the origins that have enough history before and five target years after.

    Args:
        years: Calendar years with data (ascending, consecutive).
        lead: Years from the origin to the first target year.
        horizon: Number of target years.
        min_history: Years of history needed up to and including the origin.

    Returns:
        The usable origin years, ascending (possibly empty).
    """
    first, last = int(years[0]), int(years[-1])
    start = first + min_history - 1
    end = last - lead - horizon + 1
    return list(range(start, end + 1))


def _as_series(units: np.ndarray) -> np.ndarray:
    """Reshape (places, years, types) to (places x types, years)."""
    places, n_years, n_types = units.shape
    return units.transpose(0, 2, 1).reshape(places * n_types, n_years)


def project_totals(
    rule: Rule, years: np.ndarray, units: np.ndarray, origin: int, targets: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project each place by structure type and add the target years up.

    Args:
        rule: The rule.
        years: Calendar years, shape (Y,).
        units: Units, shape (places, Y, types).
        origin: Last year of history used.
        targets: Target years, shape (H,).

    Returns:
        ``(by_type, annual, has_history)``: five-year totals by place and type
        (places, types); annual projections (places, types, H); and whether each
        place had any data in the rule's window (places,).
    """
    places, _, n_types = units.shape
    proj, hist = project(rule, years, _as_series(units), origin, targets)
    annual = proj.reshape(places, n_types, targets.size)
    has_history = hist.reshape(places, n_types).any(axis=1)
    return annual.sum(axis=2), annual, has_history


def backtest_cases(
    rule: Rule,
    years: np.ndarray,
    units: np.ndarray,
    origins: Sequence[int],
    lead: int,
    horizon: int,
) -> pd.DataFrame:
    """Apply a rule at each origin and compare with what was permitted.

    Args:
        rule: The rule.
        years: Calendar years, shape (Y,).
        units: Units, shape (places, Y, types).
        origins: Origin years to test.
        lead: Years from each origin to its first target year.
        horizon: Number of target years.

    Returns:
        One row per place and origin whose target years all have data:
        ``place`` (row index into ``units``), ``origin``, ``projected``,
        ``actual`` and ``ratio`` (actual / projected; ``NaN`` when the
        projection is zero, since no ratio can be formed).
    """
    present = ~np.isnan(units).all(axis=2)
    totals = np.where(present, np.nansum(units, axis=2), np.nan)
    rows = []
    for origin in origins:
        targets = np.arange(origin + lead, origin + lead + horizon)
        cols = targets - int(years[0])
        if cols.min() < 0 or cols.max() >= years.size:
            continue
        by_type, _, _ = project_totals(rule, years, units, origin, targets)
        projected = by_type.sum(axis=1)
        actual_years = totals[:, cols]
        complete = ~np.isnan(actual_years).any(axis=1)
        actual = np.where(complete, np.nansum(actual_years, axis=1), np.nan)
        for place in np.flatnonzero(complete):
            p, a = float(projected[place]), float(actual[place])
            rows.append((int(place), int(origin), p, a, a / p if p > 0 else np.nan))
    return pd.DataFrame(rows, columns=["place", "origin", "projected", "actual", "ratio"])


def size_classes(panel: Panel, recent_window: int, n_classes: int) -> np.ndarray:
    """Sort counties into size classes by their recent average yearly permits.

    Counties are ranked by average yearly units (all structure types) over the
    last ``recent_window`` years of data, smallest first, ties broken by county
    id. With three classes the bottom third is class 0 (small), the middle third
    class 1, the top third class 2.

    Args:
        panel: The permit panel.
        recent_window: Years at the end of the data to average.
        n_classes: Number of classes.

    Returns:
        Class number for each county, shape (counties,).
    """
    totals = panel.totals()[:, -recent_window:]
    observed = ~np.isnan(totals)
    count = observed.sum(axis=1)
    mean = np.zeros(len(panel.counties))
    np.divide(np.where(observed, totals, 0).sum(axis=1), count, out=mean, where=count > 0)
    order = sorted(range(len(panel.counties)), key=lambda i: (mean[i], panel.counties[i]))
    classes = np.zeros(len(panel.counties), dtype=int)
    n = len(order)
    for rank, i in enumerate(order):
        classes[i] = min(n_classes - 1, rank * n_classes // n)
    return classes


def class_label(k: int, n_classes: int) -> str:
    """Name a size class: small / medium / large for three classes."""
    if n_classes == 3:
        return ("small", "medium", "large")[k]
    return f"size class {k + 1} of {n_classes}"


def ratio_quantiles(ratios: np.ndarray, quantiles: Sequence[float]) -> np.ndarray | None:
    """Return quantiles of the finite ratios, or ``None`` if there are none."""
    r = np.asarray(ratios, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return None
    return np.quantile(r, list(quantiles))


def interval_coverage(actual: np.ndarray, low: np.ndarray, high: np.ndarray) -> float:
    """Share of cases whose actual value lies inside [low, high] (edges count as inside).

    Args:
        actual: Actual values.
        low: Interval low ends.
        high: Interval high ends.

    Returns:
        The share, or ``NaN`` when there are no cases.
    """
    actual, low, high = (np.asarray(x, dtype=float) for x in (actual, low, high))
    if actual.size == 0:
        return float("nan")
    return float(np.mean((actual >= low) & (actual <= high)))


def leave_one_county_out(cases: pd.DataFrame, classes: np.ndarray) -> pd.DataFrame:
    """Give each case an 80% interval built only from other counties' ratios.

    The pool for county *c* is the other counties in *c*'s size class; if there
    are none with a usable ratio, it is all other counties. Cases with no pool,
    or whose projection is zero, are dropped.

    Args:
        cases: Output of ``backtest_cases`` for counties.
        classes: Size class of each county.

    Returns:
        The usable cases with added ``low`` and ``high`` columns.
    """
    usable = cases[np.isfinite(cases["ratio"])].copy()
    usable["size_class"] = classes[usable["place"].to_numpy()]
    lows, highs = np.full(len(usable), np.nan), np.full(len(usable), np.nan)
    places = usable["place"].to_numpy()
    for place in np.unique(places):
        mine = places == place
        others = usable[~mine]
        same = others[others["size_class"] == classes[place]]
        q = ratio_quantiles(same["ratio"].to_numpy(), INTERVAL)
        if q is None:
            q = ratio_quantiles(others["ratio"].to_numpy(), INTERVAL)
        if q is None:
            continue
        projected = usable.loc[mine, "projected"].to_numpy()
        lows[mine], highs[mine] = projected * q[0], projected * q[1]
    usable["low"], usable["high"] = lows, highs
    return usable[np.isfinite(usable["low"])]


def validate_baseline(
    table: pd.DataFrame,
    rules: Sequence[Rule],
    origins: Sequence[int] | None,
    settings: BaselineSettings,
) -> pd.DataFrame:
    """Backtest each rule on every county and return the skill table.

    Args:
        table: Tidy permit table (county x year x structure type).
        rules: Candidate rules.
        origins: Origin years to test; ``None`` uses every usable origin
            (``backtest_origins``).
        settings: Baseline settings from the ledger.

    Returns:
        One row per rule, in the order given: ``rule``, ``complexity``,
        ``cases`` (county-origins with actual and projection both above zero),
        ``mape``, ``median_abs_log_ratio``, ``coverage_80`` and
        ``coverage_cases`` (cases with a leave-one-county-out interval).
    """
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    if origins is None:
        origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    rows = []
    for rule in rules:
        cases = backtest_cases(rule, panel.years, panel.units, origins, lead, settings.horizon)
        scored = cases[(cases["actual"] > 0) & (cases["projected"] > 0)]
        ape = (scored["actual"] - scored["projected"]).abs() / scored["actual"]
        alr = np.abs(np.log(scored["ratio"].to_numpy()))
        covered = leave_one_county_out(cases, classes)
        rows.append(
            {
                "rule": rule.name,
                "complexity": rule.complexity,
                "cases": len(scored),
                "mape": float(ape.mean()) if len(scored) else float("nan"),
                "median_abs_log_ratio": float(np.median(alr)) if alr.size else float("nan"),
                "coverage_80": interval_coverage(
                    covered["actual"], covered["low"], covered["high"]
                ),
                "coverage_cases": len(covered),
            }
        )
    return pd.DataFrame(rows)


def choose_rule(skill: pd.DataFrame, tolerance: float, metric: str = "median_abs_log_ratio") -> str:
    """Pick the simplest rule whose error is within the tolerance of the best.

    A rule qualifies if its ``metric`` is at most ``best x (1 + tolerance)``.
    Among qualifying rules the lowest ``complexity`` wins; ties go to the lower
    error, then to the earlier row.

    Args:
        skill: Output of ``validate_baseline``.
        tolerance: Allowed relative gap to the best (``baseline.selection_tolerance``).
        metric: Column to compare.

    Returns:
        The chosen rule's name.

    Raises:
        ValueError: No rule has a finite score.
    """
    scored = skill[np.isfinite(skill[metric])]
    if scored.empty:
        raise ValueError("no rule has a finite score; the backtest had no usable cases")
    best = scored[metric].min()
    ok = scored[scored[metric] <= best * (1 + tolerance)]
    ok = ok.assign(_row=range(len(ok))).sort_values(["complexity", metric, "_row"])
    return str(ok.iloc[0]["rule"])
