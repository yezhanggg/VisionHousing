"""Shares above or below a dollar threshold, read from published ACS bracket counts.

Plain-language summary
----------------------
The American Community Survey (ACS) does not publish each home's value or each
unit's rent. It publishes *bracket counts*: how many owner homes are worth
"$100,000 to $124,999", how many renters pay "$800 to $899", and so on. The
feasibility step of the model needs a different number: the *share* of a tract's
homes (or rents) above a break-even level from the pro forma. This module turns
the bracket counts into that share.

How it works
------------
- **Edges.** Every bracket is a half-open interval ``[low, high)``. "Less than
  $10,000" is ``[0, 10000)``; "$10,000 to $14,999" is ``[10000, 15000)``. The top
  bracket ("$2,000,000 or more") is open: ``[2000000, inf)``.
- **Inside a bracket the units are spread evenly** (a uniform distribution). So
  a threshold 40% of the way through a bracket puts 60% of that bracket above it.
  This is straight-line (linear) interpolation of the cumulative count.
- **The open top bracket has no upper edge**, so it cannot be split by a
  straight line. Two stated rules, chosen by the ``top_rule`` argument:

  * ``"lower_bound"`` (default): every unit in the top bracket is treated as
    sitting exactly at the bracket's lower edge. A threshold inside the top
    bracket then counts none of the top bracket as above it. The share above is
    a **lower bound** and the share below is an **upper bound**, and the result
    says so. This needs no extra parameter.
  * ``"pareto"``: the top bracket follows a Pareto tail with shape ``alpha``.
    The share of the top bracket above a threshold ``t`` is ``(low / t) ** alpha``.
    ``alpha`` is an assumption and must be passed in; it is never set here.

- **"No cash rent" is left out.** Renters who pay no cash rent (table B25063,
  "No cash rent") have no rent to compare with a break-even. They are excluded
  from the denominator, so a rent share is a share of *cash renters*.
- **Zero totals** (a tract with no owner homes, say) give ``NaN`` and a reason,
  never an error.

Inputs and outputs
------------------
Counts are numbers aligned with an edge table. The scalar functions return a
small :class:`ShareResult`; the vectorised ones take a 2-D array (one row per
tract) and return :class:`ShareArrays`. :func:`median_from_brackets` estimates a
median the same way, to cross-check against published medians.
:func:`check_labels` checks that the ACS variable numbering for a vintage still
means what this module assumes, using label strings supplied by the caller (so it
runs offline).

All functions are pure: no files, no network, no global state.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

__all__ = [
    "RENT",
    "TOP_RULES",
    "VALUE",
    "BracketTable",
    "MedianResult",
    "ShareArrays",
    "ShareResult",
    "check_labels",
    "counts_from_row",
    "edges_from_columns",
    "median_from_brackets",
    "parse_bracket",
    "share_above",
    "share_above_many",
    "share_below",
    "share_below_many",
]

TOP_RULES = ("lower_bound", "pareto")
"""The two stated rules for the open top bracket."""

FIRST_SUPPORTED_VINTAGE = 2015
"""First ACS 5-year vintage with today's bracket edges (B25075 top "$2,000,000 or more")."""


@dataclass(frozen=True)
class BracketTable:
    """One ACS bracket table: its edges, column names and variable numbers.

    Attributes:
        table: ACS table id, for example ``"B25075"``.
        prefix: Column-name prefix used in state packs, ``"value"`` or ``"rent"``.
        lows: Lower edge of each bracket, in dollars, ascending. The first is 0.
        first_var: Variable number of the first bracket (``2`` means ``_002``).
        excluded: Columns that belong to the table but are not brackets and are
            left out of every share (for rent: ``rent_no_cash``).
        excluded_var: Variable number of the excluded column, or ``None``.
    """

    table: str
    prefix: str
    lows: tuple[int, ...]
    first_var: int
    excluded: tuple[str, ...] = ()
    excluded_var: int | None = None

    @property
    def highs(self) -> tuple[float, ...]:
        """Upper edge of each bracket (exclusive); the last is ``inf``."""
        return (*(float(x) for x in self.lows[1:]), math.inf)

    @property
    def edges(self) -> np.ndarray:
        """Edges as a ``(k, 2)`` float array of ``[low, high)`` pairs."""
        return np.column_stack([np.asarray(self.lows, dtype=float), np.asarray(self.highs)])

    @property
    def columns(self) -> tuple[str, ...]:
        """State-pack column names, in bracket order (excluded columns not listed)."""
        out = []
        for i, low in enumerate(self.lows):
            if i == 0:
                out.append(f"{self.prefix}_lt_{self.lows[1]}")
            elif i == len(self.lows) - 1:
                out.append(f"{self.prefix}_{low}_plus")
            else:
                out.append(f"{self.prefix}_{low}_{self.lows[i + 1] - 1}")
        return tuple(out)

    @property
    def labels(self) -> tuple[str, ...]:
        """The last part of each ACS label, for example ``"$10,000 to $14,999"``."""
        out = []
        for i, low in enumerate(self.lows):
            if i == 0:
                out.append(f"Less than ${self.lows[1]:,}")
            elif i == len(self.lows) - 1:
                out.append(f"${low:,} or more")
            else:
                out.append(f"${low:,} to ${self.lows[i + 1] - 1:,}")
        return tuple(out)

    def variables(self) -> dict[str, str]:
        """Map ACS estimate variable ids to state-pack column names.

        Returns:
            For example ``{"B25075_002E": "value_lt_10000", ...}``. The excluded
            column (no cash rent) is included so a fetcher can keep it.
        """
        out = {f"{self.table}_{self.first_var + i:03d}E": col for i, col in enumerate(self.columns)}
        if self.excluded_var is not None:
            out[f"{self.table}_{self.excluded_var:03d}E"] = self.excluded[0]
        return out


_VALUE_LOWS = (
    0, 10_000, 15_000, 20_000, 25_000, 30_000, 35_000, 40_000, 50_000, 60_000,
    70_000, 80_000, 90_000, 100_000, 125_000, 150_000, 175_000, 200_000, 250_000,
    300_000, 400_000, 500_000, 750_000, 1_000_000, 1_500_000, 2_000_000,
)  # fmt: skip
_RENT_LOWS = (
    0, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750,
    800, 900, 1_000, 1_250, 1_500, 2_000, 2_500, 3_000, 3_500,
)  # fmt: skip

VALUE = BracketTable(
    table="B25075",
    prefix="value",
    lows=_VALUE_LOWS,
    first_var=2,
)
"""B25075, value of owner-occupied housing units: 26 brackets."""

RENT = BracketTable(
    table="B25063",
    prefix="rent",
    lows=_RENT_LOWS,
    first_var=3,
    excluded=("rent_no_cash",),
    excluded_var=27,
)
"""B25063, gross rent of renter-occupied units: 24 cash brackets plus "No cash rent"."""

_SPECIAL_LABELS = {
    # Variables that are not brackets but whose meaning must still hold.
    "B25075": {1: "Total"},
    "B25063": {1: "Total", 2: "With cash rent", 27: "No cash rent"},
}

_NAME = re.compile(r"^(?P<prefix>[a-z]+)_(?:lt_(?P<lt>\d+)|(?P<a>\d+)_(?:(?P<b>\d+)|plus))$")


def parse_bracket(name: str) -> tuple[float, float]:
    """Map a state-pack column name to its ``[low, high)`` dollar edges.

    Args:
        name: For example ``"value_lt_10000"``, ``"rent_800_899"`` or
            ``"value_2000000_plus"``.

    Returns:
        ``(low, high)``. ``high`` is exclusive: ``"rent_800_899"`` gives
        ``(800.0, 900.0)``. The open top bracket gives ``high = inf``.

    Raises:
        ValueError: If the name is not a bracket column (``rent_no_cash`` is not).
    """
    m = _NAME.match(name)
    if not m:
        raise ValueError(f"not a bracket column: {name!r}")
    if m["lt"] is not None:
        return 0.0, float(m["lt"])
    low = float(m["a"])
    if m["b"] is None:
        return low, math.inf
    high = float(m["b"]) + 1.0
    if high <= low:
        raise ValueError(f"bracket {name!r} has high edge below its low edge")
    return low, high


def edges_from_columns(columns: Sequence[str]) -> np.ndarray:
    """Edges for a list of bracket columns, as a ``(k, 2)`` array.

    Args:
        columns: Bracket column names in ascending order.

    Returns:
        ``[low, high)`` rows.

    Raises:
        ValueError: If the brackets are not contiguous and ascending, or only the
            last one is allowed to be open.
    """
    edges = np.array([parse_bracket(c) for c in columns], dtype=float).reshape(-1, 2)
    _validate_edges(edges)
    return edges


def counts_from_row(row: Mapping[str, object], table: BracketTable) -> np.ndarray:
    """Pull one tract's bracket counts out of a row, in edge order.

    The excluded column (``rent_no_cash``) is not read: it never enters a share.

    Args:
        row: A mapping with the table's column names, for example a CSV row.
        table: :data:`VALUE` or :data:`RENT`.

    Returns:
        A float array of length ``len(table.lows)``.
    """
    return np.array([float(row[c]) for c in table.columns], dtype=float)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ShareResult:
    """A share read from bracket counts.

    Attributes:
        value: The share, between 0 and 1, or ``NaN`` when it cannot be computed.
        in_top_bracket: True when the threshold lies strictly inside the open top
            bracket, so the answer depends on the top-bracket rule.
        bound: ``"lower"`` or ``"upper"`` when the answer is only a bound (the
            ``"lower_bound"`` rule with the threshold in the top bracket), else
            ``None``.
        rule: The top-bracket rule used.
        total: The denominator: units in the brackets (no-cash renters excluded).
        reason: Why ``value`` is ``NaN``, else ``None``.
    """

    value: float
    in_top_bracket: bool
    bound: str | None
    rule: str
    total: float
    reason: str | None = None


@dataclass(frozen=True)
class ShareArrays:
    """Vectorised shares, one entry per tract.

    Attributes:
        value: Shares (``NaN`` where a reason is given).
        in_top_bracket: Threshold strictly inside the open top bracket.
        bound: Per tract ``"lower"``, ``"upper"`` or ``""`` (not a bound).
        total: Denominators.
        reason: Per tract reason for ``NaN``, or ``""``.
        rule: The top-bracket rule used.
    """

    value: np.ndarray
    in_top_bracket: np.ndarray
    bound: np.ndarray
    total: np.ndarray
    reason: np.ndarray
    rule: str


def _validate_edges(edges: np.ndarray) -> None:
    if edges.ndim != 2 or edges.shape[1] != 2 or edges.shape[0] == 0:
        raise ValueError("edges must be a non-empty (k, 2) array of [low, high) pairs")
    lows, highs = edges[:, 0], edges[:, 1]
    if np.any(~np.isfinite(lows)) or np.any(highs <= lows):
        raise ValueError("every bracket needs a finite low edge below its high edge")
    if np.any(~np.isfinite(highs[:-1])):
        raise ValueError("only the last bracket may be open-ended")
    if np.any(highs[:-1] != lows[1:]):
        raise ValueError("brackets must be contiguous: each high edge is the next low edge")


def _as_edges(edges: BracketTable | np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    arr = edges.edges if isinstance(edges, BracketTable) else np.asarray(edges, dtype=float)
    _validate_edges(arr)
    return arr


def _check_rule(top_rule: str, alpha: float | None) -> None:
    if top_rule not in TOP_RULES:
        raise ValueError(f"top_rule must be one of {TOP_RULES}, got {top_rule!r}")
    if top_rule == "pareto" and (alpha is None or not alpha > 0):
        raise ValueError("the pareto rule needs a positive alpha (an assumption, passed in)")


def _bracket_share_above(low: float, high: float, t: float, top_rule: str, alpha: float | None):
    """Fraction of one bracket's units above ``t`` (uniform inside, rule for the top)."""
    if t <= low:
        return 1.0
    if math.isinf(high):
        if top_rule == "pareto":
            return (low / t) ** alpha
        return 0.0  # lower_bound: all top units sit at the low edge
    if t >= high:
        return 0.0
    return (high - t) / (high - low)


def share_above(
    counts: Sequence[float] | np.ndarray,
    edges: BracketTable | np.ndarray | Sequence[Sequence[float]],
    threshold: float,
    *,
    top_rule: str = "lower_bound",
    alpha: float | None = None,
) -> ShareResult:
    """Share of units with a value (or rent) at or above ``threshold``.

    Formula: ``sum_j count_j * frac_j(threshold) / sum_j count_j``, where
    ``frac_j`` is 1 for brackets wholly above the threshold, 0 for brackets wholly
    below, ``(high - t) / (high - low)`` for the bracket that holds it, and the
    top-bracket rule for the open top bracket.

    Args:
        counts: Units per bracket, aligned with ``edges``. Leave out no-cash rent.
        edges: A :class:`BracketTable` or a ``(k, 2)`` array of ``[low, high)``.
        threshold: Dollar break-even.
        top_rule: ``"lower_bound"`` (default) or ``"pareto"``.
        alpha: Pareto shape; required with ``top_rule="pareto"``.

    Returns:
        A :class:`ShareResult`. ``NaN`` with a reason for zero totals, missing
        counts or a missing threshold.

    Raises:
        ValueError: On bad edges, misaligned counts, negative counts or a bad rule.
    """
    _check_rule(top_rule, alpha)
    e = _as_edges(edges)
    c = np.asarray(counts, dtype=float)
    if c.shape != (e.shape[0],):
        raise ValueError(f"expected {e.shape[0]} counts, got shape {c.shape}")
    if np.any(c < 0):
        raise ValueError("bracket counts cannot be negative")
    total = float(c.sum())
    nan = math.nan
    if np.any(np.isnan(c)):
        return ShareResult(nan, False, None, top_rule, nan, "missing bracket count")
    if total <= 0:
        return ShareResult(nan, False, None, top_rule, total, "zero total: no units in brackets")
    t = float(threshold)
    if math.isnan(t):
        return ShareResult(nan, False, None, top_rule, total, "missing threshold")
    above = 0.0
    for (low, high), count in zip(e, c, strict=True):
        above += count * _bracket_share_above(low, high, t, top_rule, alpha)
    in_top = t > e[-1, 0] and math.isinf(e[-1, 1])
    bound = "lower" if in_top and top_rule == "lower_bound" else None
    return ShareResult(above / total, bool(in_top), bound, top_rule, total)


def share_below(
    counts: Sequence[float] | np.ndarray,
    edges: BracketTable | np.ndarray | Sequence[Sequence[float]],
    threshold: float,
    *,
    top_rule: str = "lower_bound",
    alpha: float | None = None,
) -> ShareResult:
    """Share of units with a value (or rent) below ``threshold``: ``1 - share_above``.

    Under the ``"lower_bound"`` rule a threshold inside the top bracket makes this
    an **upper** bound. Arguments and errors are as for :func:`share_above`.

    Returns:
        A :class:`ShareResult`.
    """
    r = share_above(counts, edges, threshold, top_rule=top_rule, alpha=alpha)
    bound = {"lower": "upper", "upper": "lower"}.get(r.bound) if r.bound else None
    return ShareResult(1.0 - r.value, r.in_top_bracket, bound, r.rule, r.total, r.reason)


def share_above_many(
    counts: np.ndarray,
    edges: BracketTable | np.ndarray | Sequence[Sequence[float]],
    threshold: float | np.ndarray,
    *,
    top_rule: str = "lower_bound",
    alpha: float | None = None,
) -> ShareArrays:
    """Vectorised :func:`share_above` over many tracts.

    Args:
        counts: ``(n, k)`` array, one row per tract.
        edges: As for :func:`share_above`.
        threshold: A scalar, or one threshold per tract (shape ``(n,)``).
        top_rule: ``"lower_bound"`` or ``"pareto"``.
        alpha: Pareto shape; required with ``top_rule="pareto"``.

    Returns:
        A :class:`ShareArrays`, equal element by element to a loop of
        :func:`share_above`.

    Raises:
        ValueError: On bad edges, misaligned shapes, negative counts or a bad rule.
    """
    _check_rule(top_rule, alpha)
    e = _as_edges(edges)
    c = np.asarray(counts, dtype=float)
    if c.ndim != 2 or c.shape[1] != e.shape[0]:
        raise ValueError(f"counts must have shape (n, {e.shape[0]}), got {c.shape}")
    n = c.shape[0]
    t = np.broadcast_to(np.asarray(threshold, dtype=float), (n,)).astype(float)
    if np.any(c < 0):
        raise ValueError("bracket counts cannot be negative")
    lows, highs = e[:, 0], e[:, 1]
    finite_high = np.where(np.isfinite(highs), highs, 0.0)
    width = np.where(np.isfinite(highs), highs - lows, 1.0)
    tt = t[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = np.clip((finite_high - tt) / width, 0.0, 1.0)
        top = np.isinf(highs)
        if top_rule == "pareto":
            top_frac = np.where(tt <= lows, 1.0, (lows / tt) ** alpha)
        else:
            top_frac = np.where(tt <= lows, 1.0, 0.0)
        frac = np.where(top, top_frac, frac)
        total = c.sum(axis=1)
        value = (c * frac).sum(axis=1) / total
    reason = np.full(n, "", dtype=object)
    missing_count = np.isnan(c).any(axis=1)
    zero = ~missing_count & (total <= 0)
    missing_t = ~missing_count & ~zero & np.isnan(t)
    reason[missing_count] = "missing bracket count"
    reason[zero] = "zero total: no units in brackets"
    reason[missing_t] = "missing threshold"
    bad = reason != ""
    value = np.where(bad, np.nan, value)
    total = np.where(missing_count, np.nan, total)
    in_top = ~bad & bool(np.isinf(highs[-1])) & (t > lows[-1])
    bound = np.full(n, "", dtype=object)
    if top_rule == "lower_bound":
        bound[in_top] = "lower"
    return ShareArrays(value, in_top, bound, total, reason, top_rule)


def share_below_many(
    counts: np.ndarray,
    edges: BracketTable | np.ndarray | Sequence[Sequence[float]],
    threshold: float | np.ndarray,
    *,
    top_rule: str = "lower_bound",
    alpha: float | None = None,
) -> ShareArrays:
    """Vectorised :func:`share_below`: ``1 - share_above_many``, bounds flipped.

    Returns:
        A :class:`ShareArrays`.
    """
    r = share_above_many(counts, edges, threshold, top_rule=top_rule, alpha=alpha)
    bound = np.where(r.bound == "lower", "upper", r.bound).astype(object)
    return ShareArrays(1.0 - r.value, r.in_top_bracket, bound, r.total, r.reason, r.rule)


@dataclass(frozen=True)
class MedianResult:
    """A median estimated from bracket counts.

    Attributes:
        value: Dollars, or ``NaN`` with a reason.
        bracket: Index of the bracket that holds the median, or ``-1``.
        bound: ``"lower"`` when the median falls in the open top bracket (the
            value is then that bracket's lower edge, as the ACS reports
            "$2,000,000+"), else ``None``.
        reason: Why ``value`` is ``NaN``, else ``None``.
    """

    value: float
    bracket: int
    bound: str | None
    reason: str | None = None


def median_from_brackets(
    counts: Sequence[float] | np.ndarray,
    edges: BracketTable | np.ndarray | Sequence[Sequence[float]],
) -> MedianResult:
    """Median by straight-line interpolation inside the bracket that holds it.

    Formula: find the first bracket ``j`` whose cumulative count reaches half the
    total, then ``low_j + (total / 2 - count below j) / count_j * width_j``. This
    is the standard grouped-data median; it will not equal the published median
    exactly, but it must land in the same bracket.

    Args:
        counts: Units per bracket.
        edges: A :class:`BracketTable` or a ``(k, 2)`` edge array.

    Returns:
        A :class:`MedianResult`.

    Raises:
        ValueError: On bad edges, misaligned counts or negative counts.
    """
    e = _as_edges(edges)
    c = np.asarray(counts, dtype=float)
    if c.shape != (e.shape[0],):
        raise ValueError(f"expected {e.shape[0]} counts, got shape {c.shape}")
    if np.any(c < 0):
        raise ValueError("bracket counts cannot be negative")
    if np.any(np.isnan(c)):
        return MedianResult(math.nan, -1, None, "missing bracket count")
    total = float(c.sum())
    if total <= 0:
        return MedianResult(math.nan, -1, None, "zero total: no units in brackets")
    half = total / 2.0
    below = 0.0
    for j, ((low, high), count) in enumerate(zip(e, c, strict=True)):
        if count > 0 and below + count >= half:
            if math.isinf(high):
                return MedianResult(float(low), j, "lower")
            return MedianResult(float(low + (half - below) / count * (high - low)), j, None)
        below += count
    raise AssertionError("unreachable: a positive total always reaches half")  # pragma: no cover


def check_labels(
    labels: Mapping[str, str],
    table: BracketTable,
    vintage: int,
) -> list[str]:
    """Check that ACS variable labels mean what this module's edges assume.

    The Census Bureau has renumbered brackets between vintages (for example the
    top value bracket moved from "$1,000,000 or more" to "$2,000,000 or more").
    A fetcher downloads the variable labels and passes them here before trusting
    the numbering.

    Args:
        labels: Variable id to label string, for example
            ``{"B25075_002E": "Estimate!!Total:!!Less than $10,000", ...}``.
            Only the last ``!!`` part is compared, with a trailing colon dropped.
        table: :data:`VALUE` or :data:`RENT`.
        vintage: ACS 5-year end year, for example ``2023``.

    Returns:
        A list of problems, one line each. An empty list means the labels match.

    Raises:
        ValueError: For a vintage before 2015, whose edges differ from these.
    """
    if vintage < FIRST_SUPPORTED_VINTAGE:
        raise ValueError(
            f"vintage {vintage} uses different {table.table} brackets; "
            f"supported from {FIRST_SUPPORTED_VINTAGE}"
        )
    expected = {
        f"{table.table}_{table.first_var + i:03d}E": want for i, want in enumerate(table.labels)
    }
    for num, want in _SPECIAL_LABELS.get(table.table, {}).items():
        expected[f"{table.table}_{num:03d}E"] = want
    problems = []
    for var, want in sorted(expected.items()):
        got = labels.get(var)
        if got is None:
            problems.append(f"{var}: missing, expected {want!r}")
            continue
        last = got.split("!!")[-1].strip().rstrip(":").strip()
        if last != want:
            problems.append(f"{var}: label {got!r}, expected it to end with {want!r}")
    extra = sorted(
        v
        for v in labels
        if v.startswith(f"{table.table}_") and v.endswith("E") and v not in expected
    )
    problems.extend(f"{v}: unexpected variable, the table has more brackets" for v in extra)
    return problems
