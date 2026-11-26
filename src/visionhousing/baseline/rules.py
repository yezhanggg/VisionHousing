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

The baseline rule contest (:mod:`visionhousing.baseline.contest`) adds rules of
four more kinds:

``median``
    The median of the last ``window`` years (``median_5``): one freak year in
    the window moves it much less than it moves a mean.
``shrink``
    Each county's mean of the last ``window`` years, as permits per home, is
    pulled toward its place-type group's rate:
    ``rate = w x own rate + (1 - w) x group rate`` with
    ``w = s / (s + half_weight)``, ``s`` the county's mean yearly permits in the
    window. Small counties, whose own mean is noisier, are pulled more.
``top_down``
    The state's mean of the last ``window`` years times the county's share of
    state permits over the last ``share_window`` years.
``average``
    The simple average of the ``components`` rules' projections.

``shrink`` and ``top_down`` need all counties at once (and the place types and
homes in :class:`PlaceInfo`), so they run through :func:`project_places`, which
handles every kind. On a single series (the state total) both are the plain
mean of the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from visionhousing.ledger import Ledger

Method = Literal["mean", "ewm", "damped_trend", "median", "shrink", "top_down", "average"]


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
        share_window: Years over which a county's share of the state is
            measured (``top_down`` only).
        half_weight: Mean yearly permits at which a county's own rate and its
            group's get equal weight (``shrink`` only).
        components: The rules averaged (``average`` only).
    """

    name: str
    method: Method
    complexity: int
    window: int | None = None
    alpha: float | None = None
    damping: float | None = None
    assumption_ids: tuple[str, ...] = ()
    share_window: int | None = None
    half_weight: float | None = None
    components: tuple[Rule, ...] = ()

    def __post_init__(self) -> None:
        windowed = ("mean", "damped_trend", "median", "shrink", "top_down")
        if self.method in windowed and (self.window is None or self.window < 1):
            raise ValueError(f"rule '{self.name}' needs a window of at least one year")
        if self.method == "ewm" and (self.alpha is None or not 0 < self.alpha <= 1):
            raise ValueError(f"rule '{self.name}' needs 0 < alpha <= 1")
        if self.method == "damped_trend" and (self.damping is None or not 0 <= self.damping <= 1):
            raise ValueError(f"rule '{self.name}' needs 0 <= damping <= 1")
        if self.method == "top_down" and (self.share_window is None or self.share_window < 1):
            raise ValueError(f"rule '{self.name}' needs a share window of at least one year")
        if self.method == "shrink" and (self.half_weight is None or self.half_weight < 0):
            raise ValueError(f"rule '{self.name}' needs a half weight of zero or more")
        if self.method == "average" and not self.components:
            raise ValueError(f"rule '{self.name}' needs at least one component rule")

    def describe(self, origin: int) -> str:
        """Say in words what the rule does at this origin year.

        Args:
            origin: Last year of history the rule may use.

        Returns:
            A short phrase, for example "mean of 2021-2025 permits".
        """
        if self.method == "mean":
            return f"mean of {origin - self.window + 1}-{origin} permits"
        if self.method == "median":
            return f"median of {origin - self.window + 1}-{origin} permits"
        if self.method == "shrink":
            return (
                f"mean of {origin - self.window + 1}-{origin} permits per home, pulled toward "
                f"the county's place-type rate with weight s / (s + {self.half_weight:g}) on its "
                "own rate (s = its mean yearly permits)"
            )
        if self.method == "top_down":
            return (
                f"state mean of {origin - self.window + 1}-{origin} permits x the county's "
                f"share of state permits in {origin - self.share_window + 1}-{origin}"
            )
        if self.method == "average":
            return "simple average of " + ", ".join(c.name for c in self.components)
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
    if rule.method in ("shrink", "top_down", "average"):
        annual, hist = project_places(rule, years, values[:, :, np.newaxis], origin, targets, None)
        return annual[:, 0, :], hist[:, 0]
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
    elif rule.method == "median":
        with np.errstate(all="ignore"):
            level = np.zeros(values.shape[0])
            if window_values.shape[1]:
                med = np.nanmedian(np.where(observed, window_values, np.nan), axis=1)
                level = np.where(has_history, med, 0.0)
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


@dataclass(frozen=True)
class PlaceInfo:
    """What the cross-sectional rules and the place-type ranges know about counties.

    Attributes:
        groups: Place-type label of each county, in panel order.
        source: Which grouping the labels come from (``typology`` or ``size_class``).
        homes: Homes in each county (panel order), ``NaN`` where unknown; or
            ``None`` when the state has no home counts.
    """

    groups: tuple[str, ...]
    source: str
    homes: np.ndarray | None = None


def _window_history(rule: Rule, years: np.ndarray, units: np.ndarray, origin: int) -> np.ndarray:
    """Whether each (place, type) series has data in the rule's own window."""
    if rule.method == "average":
        out = np.zeros(units.shape[0::2], dtype=bool)
        for c in rule.components:
            out |= _window_history(c, years, units, origin)
        return out
    if rule.method == "ewm":
        cols = years <= origin
    else:
        cols = (years <= origin) & (years > origin - rule.window)
    return (~np.isnan(units[:, cols, :])).any(axis=1)


def project_places(
    rule: Rule,
    years: np.ndarray,
    units: np.ndarray,
    origin: int,
    targets: np.ndarray,
    info: PlaceInfo | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Project every place and structure type at once (all rule kinds).

    Args:
        rule: The rule.
        years: Calendar years, shape (Y,).
        units: Units, shape (places, Y, types); ``NaN`` = not reported.
        origin: Last year of history used.
        targets: Target years, shape (H,).
        info: Place types and homes, for ``shrink``; ``None`` treats every
            place as its own group (so ``shrink`` is the plain mean).

    Returns:
        ``(annual, has_history)``: projections of shape (places, types, H),
        never negative; and whether each (place, type) had data in the rule's
        window, shape (places, types).
    """
    years = np.asarray(years)
    targets = np.asarray(targets)
    places, n_years, n_types = units.shape
    if rule.method == "average":
        parts = [project_places(c, years, units, origin, targets, info)[0] for c in rule.components]
        return np.mean(parts, axis=0), _window_history(rule, years, units, origin)
    if rule.method not in ("shrink", "top_down"):
        series = units.transpose(0, 2, 1).reshape(places * n_types, n_years)
        proj, hist = project(rule, years, series, origin, targets)
        return proj.reshape(places, n_types, targets.size), hist.reshape(places, n_types)

    mean_rule = Rule("window_mean", "mean", 1, window=rule.window)
    base, hist = project_places(mean_rule, years, units, origin, targets, None)
    level = base[:, :, 0]  # (places, types): mean yearly units in the window
    if rule.method == "top_down":
        state = np.where((~np.isnan(units)).any(axis=0), np.nansum(units, axis=0), np.nan)[
            np.newaxis
        ]
        state_level = project_places(mean_rule, years, state, origin, targets, None)[0][0, :, 0]
        share_cols = (years <= origin) & (years > origin - rule.share_window)
        own = np.nansum(units[:, share_cols, :], axis=1)
        total = own.sum(axis=0)
        share = _safe_divide(own, np.broadcast_to(total, own.shape))
        new_level = share * state_level[np.newaxis, :]
        hist = hist | (~np.isnan(units[:, share_cols, :])).any(axis=1)
    else:
        new_level = level.copy()
        if info is not None and info.homes is not None and places > 1:
            homes = np.asarray(info.homes, dtype=float)
            known = np.isfinite(homes) & (homes > 0)
            s = level.sum(axis=1)
            w = _safe_divide(s, s + float(rule.half_weight))
            labels = np.array(info.groups, dtype=object)
            for g in dict.fromkeys(info.groups):
                members = (labels == g) & known & hist.any(axis=1)
                if not members.any():
                    continue
                group_rate = level[members].sum(axis=0) / homes[members].sum()
                own_rate = level[members] / homes[members, np.newaxis]
                wm = w[members, np.newaxis]
                new_level[members] = homes[members, np.newaxis] * (
                    wm * own_rate + (1 - wm) * group_rate[np.newaxis, :]
                )
    new_level = np.where(hist, np.maximum(new_level, 0.0), 0.0)
    return np.repeat(new_level[:, :, np.newaxis], targets.size, axis=2), hist
