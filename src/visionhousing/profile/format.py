"""Formatting for place profiles: numbers as plain text, and comparisons as plain words.

Plain-language summary
----------------------
Every fact keeps its exact value; this module only decides how it is shown.

- :func:`fmt` turns a value and its unit into text: ``1,234`` people,
  ``$65,000``, ``$1,050 a month``, ``12%``, ``+3.4%``.
- :func:`compare_words` says how a place compares with a bigger area:
  "higher than the state", "lower than the county" or "about the same as the
  state". Two numbers are called "about the same" when they are close:

  * for shares (a number between 0 and 1), within :data:`SAME_SHARE_POINTS`
    percentage points (2 points);
  * for percent changes, within :data:`SAME_PERCENT_POINTS` points (2 points);
  * for everything else, within :data:`SAME_RELATIVE` of each other (5%).

  These bands are display wording only, not model assumptions: they never
  change a number, only whether the words say "higher" or "about the same".
  The exact values are always shown next to the words.

- :func:`tidy` shortens a raw number for the JSON reply: a float is cut to
  :data:`RAW_DIGITS` significant digits (``105.62196741208714`` becomes
  ``105.6``), but only when the shorter number still formats to exactly the same
  text by :func:`fmt`; otherwise it keeps more digits, up to the exact value.
  Every shown text is made from the exact value before this step, so no text
  changes; the raw number only loses digits no one sees. Whole numbers are kept.
"""

from __future__ import annotations

import math

__all__ = [
    "SAME_PERCENT_POINTS",
    "SAME_RELATIVE",
    "SAME_SHARE_POINTS",
    "compare_words",
    "fmt",
    "is_missing",
    "tidy",
]

SAME_SHARE_POINTS = 0.02
"""Shares within 2 percentage points read "about the same"."""

SAME_PERCENT_POINTS = 2.0
"""Percent changes within 2 points read "about the same"."""

SAME_RELATIVE = 0.05
"""Other values within 5% of each other read "about the same"."""

MISSING = "not available"

RAW_DIGITS = 4
"""Significant digits kept in a profile's raw numbers (JSON only; see :func:`tidy`)."""


def is_missing(value: object) -> bool:
    """True for ``None`` and NaN."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _num(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


def fmt(value: object, unit: str) -> str:
    """Format a value for people, by its unit.

    Args:
        value: The number (or a word for categories).
        unit: The fact's unit.

    Returns:
        The text, or ``"not available"`` for a missing value.
    """
    if is_missing(value):
        return MISSING
    if isinstance(value, str):
        return value
    x = float(value)  # type: ignore[arg-type]
    if unit == "dollars":
        return f"${_num(x)}"
    if unit == "dollars per month":
        return f"${_num(x)} a month"
    if unit == "dollars per year":
        return f"${_num(x)} a year"
    if unit == "share":
        return f"{x * 100:.0f}%" if abs(x) >= 0.1 or x == 0 else f"{x * 100:.1f}%"
    if unit == "percent":
        return f"{x:.1f}%"
    if unit == "percent change":
        return f"{x:+.1f}%"
    if unit in ("ratio",):
        return f"{x:.2f}"
    if unit == "per 1,000 homes":
        return f"{x:.1f} per 1,000 homes"
    if unit == "year":
        return f"{x:.0f}"
    return _num(x)


def compare_words(value: object, other: object, unit: str, against: str) -> str:
    """Plain words for how ``value`` compares with ``other`` (the bigger area's value).

    Args:
        value: The place's value.
        other: The bigger area's value.
        unit: The shared unit (decides which "about the same" band applies).
        against: The bigger area's level, used in the words ("state", "county").

    Returns:
        "higher than the state", "lower than the county", "about the same as
        the state", or "" when either value is missing or not a number.
    """
    if is_missing(value) or is_missing(other):
        return ""
    if isinstance(value, str) or isinstance(other, str):
        return ""
    a, b = float(value), float(other)  # type: ignore[arg-type]
    diff = a - b
    if unit == "share":
        same = abs(diff) < SAME_SHARE_POINTS
    elif unit in ("percent", "percent change"):
        same = abs(diff) < SAME_PERCENT_POINTS
    else:
        same = abs(diff) <= SAME_RELATIVE * max(abs(b), 1e-12)
    if same:
        return f"about the same as the {against}"
    return f"{'higher' if diff > 0 else 'lower'} than the {against}"


def tidy(value: object, unit: str) -> object:
    """A raw float cut to :data:`RAW_DIGITS` significant digits where that shows the same.

    Args:
        value: The raw value (anything that is not a finite, non-whole float comes back
            unchanged).
        unit: The value's unit, as :func:`fmt` uses it.

    Returns:
        The shortest float with at least :data:`RAW_DIGITS` significant digits whose
        :func:`fmt` text equals the exact value's, or ``value`` itself.
    """
    if not isinstance(value, float) or not math.isfinite(value) or value.is_integer():
        return value
    shown = fmt(value, unit)
    for digits in range(RAW_DIGITS, 17):
        short = float(f"{value:.{digits - 1}e}")
        if short == value:
            return value
        if fmt(short, unit) == shown:
            return short
    return value
