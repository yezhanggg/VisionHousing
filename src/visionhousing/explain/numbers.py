"""How numbers are written in text, and the "every number is cited" check.

Plain-language summary
----------------------
The engine keeps full precision. People read rounded numbers. This module is
the one place that decides how a number from the engine is written:

- :func:`num` rounds to a fixed count of significant figures (two in
  sentences and slides, three in tables) and adds thousands commas, so
  ``8,010.5`` becomes ``8,000`` and ``6.203`` becomes ``6.2``;
- :func:`show` adds the unit: money as ``$25,000/unit``, shares as a percent
  (``0.0987`` becomes ``9.9%``), counts with their unit word.

Every sentence the explanation layer writes is a :class:`Text`: the words plus
a list of :class:`Cite` entries. A cite names where a number came from (a path
into the engine result, such as ``reform_results.adu.added``, and a field such
as ``p10``), the exact value there, and how it was written. Two checks follow:

1. :func:`uncited` lists any number in the words that no cite accounts for (a
   hand-typed number would show up here);
2. the tests resolve every cite against the engine result itself and confirm
   the value is there and is written the way :func:`num` writes it.

Only digits count as numbers. Words such as "five-year" are not checked.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SENTENCE_SIG",
    "TABLE_SIG",
    "Cite",
    "Text",
    "count",
    "normalise",
    "num",
    "numbers_in",
    "round_sig",
    "show",
    "uncited",
]

SENTENCE_SIG = 2
"""Significant figures in sentences, big numbers and slides."""

TABLE_SIG = 3
"""Significant figures in tables."""

PERCENT_UNITS = frozenset({"% of baseline", "%"})
COUNT_UNITS = frozenset({"units", "lots", "tracts"})
SHARE_UNITS = frozenset({"share", "share per year"})

_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")


def round_sig(value: float, sig: int = SENTENCE_SIG) -> float:
    """Round to ``sig`` significant figures (``8010.5`` -> ``8000.0`` at 2).

    Args:
        value: The number.
        sig: Significant figures (at least 1).

    Returns:
        The rounded number (0 stays 0; non-finite values are returned as is).
    """
    if sig < 1:
        raise ValueError("sig must be at least 1")
    if value == 0 or not math.isfinite(value):
        return float(value)
    return float(f"{value:.{sig}g}")


def num(value: float, sig: int = SENTENCE_SIG) -> str:
    """Write a number rounded to ``sig`` significant figures, with commas.

    Values whose rounded form is a whole number are written without decimals
    (``8,000``, ``22``); smaller values keep only the decimals the significant
    figures need (``6.2``, ``0.33``). A negative number starts with a minus
    sign (U+2212) so it is not read as a dash.

    Args:
        value: The number.
        sig: Significant figures.

    Returns:
        The number as text.
    """
    r = round_sig(float(value), sig)
    if not math.isfinite(r):
        return str(r)
    sign = "\u2212" if r < 0 else ""
    r = abs(r)
    if r == 0:
        return "0"
    exponent = math.floor(math.log10(r))
    decimals = max(0, sig - 1 - exponent)
    text = f"{r:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return sign + text


def show(value: float, unit: str, sig: int = SENTENCE_SIG) -> str:
    """Write a number with its unit, for tables and big numbers.

    Args:
        value: The number, in the engine's unit.
        unit: The engine's unit (``units``, ``$/unit``, ``share``, ...).
        sig: Significant figures.

    Returns:
        For example ``8,000 units``, ``$25,000/unit``, ``9.9%`` (a share),
        ``6.2%`` (a percent of baseline), ``1.6 spaces/unit``.
    """
    if unit in SHARE_UNITS:
        text = f"{num(100.0 * value, sig)}%"
        return text if unit == "share" else f"{text} a year"
    if unit in PERCENT_UNITS:
        return f"{num(value, sig)}%"
    if unit.startswith("$"):
        body = num(value, sig)
        sign = "\u2212" if body.startswith("\u2212") else ""
        return f"{sign}${body.lstrip(chr(0x2212))}{unit[1:]}"
    if unit in ("ratio", "factor", "multiplier"):
        return f"\u00d7{num(value, sig)}"
    if unit in ("", "1"):
        return num(value, sig)
    if unit == "year":
        return str(round(value))
    if unit in COUNT_UNITS:
        return f"{count(value, sig)} {unit}"
    return f"{num(value, sig)} {unit}"


def count(value: float, sig: int = TABLE_SIG) -> str:
    """Write a count of homes or lots: whole numbers from 1 up, else two significant figures.

    ``34.22`` -> ``34``; ``41,903.9`` -> ``41,900`` (at three figures);
    ``0.587`` -> ``0.59``. Homes come in whole numbers, so a table never shows
    "34.2 homes"; values under one keep two figures so they do not read as zero.

    Args:
        value: The count.
        sig: Significant figures for large values.

    Returns:
        The count as text.
    """
    if abs(value) < 1:
        return num(value, SENTENCE_SIG)
    r = round_sig(float(value), sig)
    return num(float(round(r)), 12)


def normalise(token: str) -> str:
    """Canonical form of a written number: no commas, no trailing zeros.

    Args:
        token: A number as written (``"8,000"``, ``"6.20"``).

    Returns:
        ``"8000"``, ``"6.2"``.
    """
    text = token.replace(",", "")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def numbers_in(text: str) -> list[str]:
    """Every number written in ``text``, normalised, in order.

    A number is a run of digits (with commas or one decimal point) that is not
    part of a word, so ``P10``, ``B25031`` and hexadecimal hashes are skipped,
    while ``2027-2031`` gives ``2027`` and ``2031``.

    Args:
        text: Any text.

    Returns:
        The numbers, normalised with :func:`normalise`.
    """
    return [normalise(m.group(0)) for m in _NUMBER.finditer(text)]


@dataclass(frozen=True)
class Cite:
    """Where one number (or one quoted piece of engine text) in a sentence came from.

    Attributes:
        ref: Path into the engine result's ``to_dict()`` output, dot-separated
            (``reform_results.adu.added``), or into the explanation's own
            sensitivity block (``drivers.adu.0``).
        field: Field at that path (``p10``, ``title``, ``total_low``...).
        value: The exact value found there.
        shown: How it is written in the sentence.
    """

    ref: str
    field: str
    value: Any
    shown: str

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-ready form."""
        return {"ref": self.ref, "field": self.field, "value": self.value, "shown": self.shown}


@dataclass(frozen=True)
class Text:
    """A sentence (or short paragraph) and the cites behind every number in it.

    Attributes:
        text: The words.
        cites: One entry per number or quoted engine text.
    """

    text: str
    cites: tuple[Cite, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-ready form."""
        return {"text": self.text, "cites": [c.to_dict() for c in self.cites]}


def uncited(text: str, cites: Iterable[Cite | Mapping[str, Any]]) -> list[str]:
    """Numbers in ``text`` that none of ``cites`` accounts for.

    A cite accounts for every number in its ``shown`` text (so citing a reform
    title such as "Pennsylvania HB 2186" covers the 2186).

    Args:
        text: The words.
        cites: The cites (objects or their ``to_dict()`` form).

    Returns:
        The uncited numbers, normalised, in order; empty when all are cited.
    """
    allowed: set[str] = set()
    for c in cites:
        shown = c["shown"] if isinstance(c, Mapping) else c.shown
        allowed.update(numbers_in(str(shown)))
    return [n for n in numbers_in(text) if n not in allowed]
