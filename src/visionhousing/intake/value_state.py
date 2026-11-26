"""Value states: one shared word for why a number is, or is not, there.

Plain-language summary
----------------------
A blank cell can mean very different things. A county that issued no permits
is a real zero. A county the publisher did not report is missing. A figure the
publisher withheld for privacy is suppressed. A figure filled in by an estimate
is imputed. A question that does not arise (a permit stage for a census table)
is not applicable. A download that failed is a feed failure, and it must never
be shown as zero.

This module names those six states once, so the command line, the interface
and the report card all use the same words:

==================  ===========================================================
state               meaning
==================  ===========================================================
``true_zero``       measured, and the count really is zero
``missing``         not measured or not reported; no value stands in
``suppressed``      the publisher withheld the value (privacy, small counts)
``imputed``         a value is shown, but it was estimated, not measured
``not_applicable``  the question does not arise for this input or place
``feed_failure``    the source was expected but did not arrive (or came empty)
==================  ===========================================================

A value that is present, measured and not zero has the state ``observed``.

:func:`classify` gives the state of one value; :func:`count_states` counts the
states over table cells; :func:`display` turns a value and its state into text
that can never show a failure or a gap as "0".
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

__all__ = [
    "LABELS",
    "OBSERVED",
    "VALUE_STATES",
    "classify",
    "count_states",
    "describe",
    "display",
    "is_value_state",
    "worst",
]

VALUE_STATES = (
    "true_zero",
    "missing",
    "suppressed",
    "imputed",
    "not_applicable",
    "feed_failure",
)
"""The six value states, in the order they are listed everywhere."""

OBSERVED = "observed"
"""The state of a measured, non-zero value."""

LABELS = {
    OBSERVED: "observed",
    "true_zero": "zero (measured)",
    "missing": "no data",
    "suppressed": "withheld by the publisher",
    "imputed": "estimated (imputed)",
    "not_applicable": "does not apply",
    "feed_failure": "source failed to load (not zero)",
}
"""Short plain words for each state, used in tables and on screens."""

_DESCRIPTIONS = {
    OBSERVED: "Measured and reported by the source.",
    "true_zero": "Measured, and the count really is zero.",
    "missing": "Not measured or not reported; no value stands in for it.",
    "suppressed": "The publisher withheld the value (for privacy or small counts).",
    "imputed": "A value is shown, but it was estimated rather than measured.",
    "not_applicable": "The question does not arise for this input or place.",
    "feed_failure": (
        "The source was expected but did not arrive, or arrived empty. "
        "This is a failure, not a zero."
    ),
}

_SEVERITY = ("feed_failure", "missing", "suppressed", "imputed", "not_applicable", "true_zero")
"""From most to least serious, for :func:`worst`."""


def is_value_state(word: str) -> bool:
    """True when ``word`` is one of the six states or ``observed``."""
    return word in VALUE_STATES or word == OBSERVED


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def classify(
    value: Any,
    *,
    applicable: bool = True,
    feed_ok: bool = True,
    suppressed: bool = False,
    imputed: bool = False,
) -> str:
    """The state of one value.

    The checks run in a fixed order: a value that does not apply is
    ``not_applicable``; a failed feed is ``feed_failure`` (whatever the value
    says, because a failed feed's zeros are not real); a withheld value is
    ``suppressed``; a blank is ``missing``; an estimated value is ``imputed``;
    a zero is ``true_zero``; anything else is ``observed``.

    Args:
        value: The value (a number, text, ``None`` or NaN).
        applicable: False when the question does not arise.
        feed_ok: False when the source that should hold it failed.
        suppressed: True when the publisher flags it as withheld.
        imputed: True when the value was estimated.

    Returns:
        One of :data:`VALUE_STATES` or :data:`OBSERVED`.
    """
    if not applicable:
        return "not_applicable"
    if not feed_ok:
        return "feed_failure"
    if suppressed:
        return "suppressed"
    if _is_blank(value):
        return "missing"
    if imputed:
        return "imputed"
    try:
        if float(value) == 0.0:
            return "true_zero"
    except (TypeError, ValueError):
        pass
    return OBSERVED


def count_states(
    values: Iterable[Any],
    *,
    imputed: Sequence[bool] | None = None,
    suppressed: Sequence[bool] | None = None,
    feed_ok: bool = True,
) -> dict[str, int]:
    """Count the states over many values (for example one table column).

    Args:
        values: The values.
        imputed: One flag per value, True where the value was estimated.
        suppressed: One flag per value, True where it was withheld.
        feed_ok: False when the whole source failed (every value is then a
            feed failure).

    Returns:
        ``{state: count}`` for every state (zeros kept, so the keys are always
        the same), ``observed`` first.
    """
    out = {OBSERVED: 0, **{s: 0 for s in VALUE_STATES}}
    for i, v in enumerate(values):
        state = classify(
            v,
            feed_ok=feed_ok,
            imputed=bool(imputed[i]) if imputed is not None else False,
            suppressed=bool(suppressed[i]) if suppressed is not None else False,
        )
        out[state] += 1
    return out


def worst(states: Iterable[str]) -> str:
    """The most serious state in a list (``observed`` when none is listed)."""
    seen = set(states)
    for s in _SEVERITY:
        if s in seen:
            return s
    return OBSERVED


def describe(state: str) -> str:
    """One plain sentence on what a state means."""
    if not is_value_state(state):
        raise ValueError(f"unknown value state {state!r}")
    return _DESCRIPTIONS[state]


def display(value: Any, state: str | None = None, fmt: str = "{:,.0f}") -> str:
    """Text for a value that never shows a gap or a failure as "0".

    Args:
        value: The value.
        state: Its state; worked out with :func:`classify` when not given.
        fmt: Format for a number.

    Returns:
        The formatted number for ``observed`` and ``true_zero`` (a true zero
        shows as "0"), the number plus "(imputed)" for ``imputed``, and the
        state's label for every other state.
    """
    state = state or classify(value)
    if not is_value_state(state):
        raise ValueError(f"unknown value state {state!r}")
    if state in (OBSERVED, "true_zero", "imputed") and not _is_blank(value):
        try:
            text = fmt.format(float(value))
        except (TypeError, ValueError):
            text = str(value)
        return f"{text} (imputed)" if state == "imputed" else text
    return LABELS[state]
